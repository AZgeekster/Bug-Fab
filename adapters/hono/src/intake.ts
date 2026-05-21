// POST /bug-reports — intake handler.
//
// Hono multipart parsing: `c.req.parseBody()` returns a
// `Record<string, string | File | (string | File)[]>`. The screenshot
// part comes back as a Web `File`; convert with `await file.arrayBuffer()`
// then wrap in `Uint8Array`. Do NOT use `Buffer` — edge runtimes don't
// expose it.
//
// Per docs/PROTOCOL.md:
//   - reject missing parts → 400 validation_error
//   - reject non-PNG → 415 unsupported_media_type
//   - reject screenshot > 10 MiB → 413 payload_too_large (with limit_bytes)
//   - reject unknown protocol_version → 400 unsupported_protocol_version
//   - reject schema failures → 422 schema_error
//   - 201 envelope is `{ id, received_at, stored_at, github_issue_url }`
//     ONLY — never echo user-submitted free text (privacy).

import { Hono } from 'hono'
import type { BugFabAppOptions, BugReportIntakeResponse } from './types.js'
import { Errors } from './errors.js'
import {
  validateSubmission,
  isValidPng,
  MAX_SCREENSHOT_BYTES,
} from './validation.js'
import { createGitHubIssue } from './github.js'

// Sliding-window per-IP rate limiter.
// In edge runtimes the worker instance may be ephemeral; this is a
// best-effort signal, not a security boundary. For real abuse defence,
// front Bug-Fab with a runtime / CDN rate limiter (Cloudflare WAF,
// Vercel Edge Config, etc.).
const rateLimitMap = new Map<string, number[]>()

function checkRateLimit(ip: string, max: number, windowMs: number): boolean {
  const now = Date.now()
  const start = now - windowMs
  const history = (rateLimitMap.get(ip) ?? []).filter((t) => t > start)
  if (history.length >= max) {
    rateLimitMap.set(ip, history)
    return false
  }
  history.push(now)
  rateLimitMap.set(ip, history)
  return true
}

function clientIp(req: Request): string {
  // Best-effort across runtimes. Cloudflare sets `cf-connecting-ip`;
  // most CDNs set `x-forwarded-for`. Fall back to a sentinel.
  const cf = req.headers.get('cf-connecting-ip')
  if (cf) return cf
  const xff = req.headers.get('x-forwarded-for')
  if (xff) return xff.split(',')[0]?.trim() ?? 'unknown'
  return req.headers.get('x-real-ip') ?? 'unknown'
}

export function buildIntakeApp(opts: BugFabAppOptions): Hono {
  const intake = new Hono()

  intake.post('/bug-reports', async (c) => {
    // Per-IP rate limiting (best-effort).
    if (opts.rateLimit?.enabled) {
      const ip = clientIp(c.req.raw)
      if (!checkRateLimit(ip, opts.rateLimit.maxRequests, opts.rateLimit.windowMs)) {
        return c.json(
          Errors.rateLimited(Math.ceil(opts.rateLimit.windowMs / 1000)),
          429,
        )
      }
    }

    // Content-Type sanity check. Hono's parseBody handles the heavy
    // lifting but a non-form body would throw a less helpful error.
    //
    // Per docs/PROTOCOL.md §Error mapping:
    //   - "multipart Content-Type wrong" (e.g., application/json) → 415
    //   - "multipart missing required parts" → 400 validation_error
    // We accept any form-style envelope here (multipart OR urlencoded);
    // the missing-parts check below distinguishes the two error modes.
    // An urlencoded body can never carry a file, so it will always fall
    // through to the missing-screenshot 400 path — which is what the
    // protocol mandates for that case.
    const ct = (c.req.header('content-type') ?? '').toLowerCase()
    const isFormEnvelope =
      ct.includes('multipart/form-data') ||
      ct.includes('application/x-www-form-urlencoded')
    if (!isFormEnvelope) {
      return c.json(
        Errors.unsupportedMediaType(
          'Content-Type must be multipart/form-data with metadata + screenshot parts.',
        ),
        415,
      )
    }

    let body: Record<string, string | File | (string | File)[]>
    try {
      body = (await c.req.parseBody()) as Record<string, string | File | (string | File)[]>
    } catch (err) {
      return c.json(
        Errors.validationError(
          `Failed to parse multipart body: ${(err as Error).message}`,
        ),
        400,
      )
    }

    const metadataRaw = body['metadata']
    const screenshotEntry = body['screenshot']

    if (typeof metadataRaw !== 'string') {
      return c.json(
        Errors.validationError('metadata field is required (JSON string).'),
        400,
      )
    }
    if (!(screenshotEntry instanceof File)) {
      return c.json(Errors.validationError('screenshot file is required.'), 400)
    }

    let screenshotBuf: Uint8Array
    try {
      screenshotBuf = new Uint8Array(await screenshotEntry.arrayBuffer())
    } catch (err) {
      return c.json(
        Errors.validationError(
          `Failed to read screenshot bytes: ${(err as Error).message}`,
        ),
        400,
      )
    }

    if (screenshotBuf.byteLength > MAX_SCREENSHOT_BYTES) {
      return c.json(Errors.payloadTooLarge(MAX_SCREENSHOT_BYTES), 413)
    }

    // Magic-byte PNG check. The conformance suite has an explicit
    // JPEG-rejection test — Content-Type alone is untrusted.
    if (!isValidPng(screenshotBuf)) {
      return c.json(Errors.unsupportedMediaType('Screenshot must be a PNG.'), 415)
    }

    let parsedMetadata: unknown
    try {
      parsedMetadata = JSON.parse(metadataRaw)
    } catch (err) {
      return c.json(
        Errors.validationError(
          `metadata is not valid JSON: ${(err as Error).message}`,
        ),
        400,
      )
    }

    const result = validateSubmission(parsedMetadata)
    if (!result.ok) {
      if (result.kind === 'unsupported_protocol_version') {
        const offending = result.errors[0] ?? 'missing'
        return c.json(Errors.unsupportedProtocolVersion(offending), 400)
      }
      return c.json(Errors.schemaError(result.errors.join('; ')), 422)
    }

    // Validation passed — apply defaults BEFORE handing to storage.
    // `as Record<string, unknown>` is safe here because the validator
    // accepted the shape.
    const sub = parsedMetadata as Record<string, unknown>
    const ctx = (sub['context'] as Record<string, unknown> | undefined) ?? {}
    const clientReportedUA =
      typeof ctx['user_agent'] === 'string' ? (ctx['user_agent'] as string) : ''

    const serverUserAgent = c.req.header('user-agent') ?? ''

    try {
      const id = await opts.storage.saveReport(
        {
          protocol_version: '0.1',
          title: sub['title'] as string,
          client_ts: sub['client_ts'] as string,
          report_type: ((sub['report_type'] as 'bug' | 'feature_request' | undefined) ?? 'bug'),
          description: (sub['description'] as string | undefined) ?? '',
          expected_behavior: (sub['expected_behavior'] as string | undefined) ?? '',
          severity: (sub['severity'] as 'low' | 'medium' | 'high' | 'critical' | undefined) ?? 'medium',
          tags: (sub['tags'] as string[] | undefined) ?? [],
          reporter:
            (sub['reporter'] as { name?: string; email?: string; user_id?: string } | undefined) ?? {},
          context: ctx,
          server_user_agent: serverUserAgent,
          client_reported_user_agent: clientReportedUA,
        },
        screenshotBuf,
      )

      const receivedAt = new Date().toISOString()
      let githubIssueUrl: string | null = null

      // Best-effort GitHub sync. Never blocks or fails the response.
      if (opts.github?.enabled && opts.github.pat && opts.github.repo) {
        try {
          const issue = await createGitHubIssue(
            { pat: opts.github.pat, repo: opts.github.repo, apiBase: opts.github.apiBase },
            {
              id,
              title: sub['title'] as string,
              description: (sub['description'] as string | undefined) ?? '',
              severity:
                (sub['severity'] as 'low' | 'medium' | 'high' | 'critical' | undefined) ?? 'medium',
              tags: sub['tags'] as string[] | undefined,
              context: ctx as { url?: string; environment?: string; app_version?: string },
            },
            (msg) => console.warn(msg),
          )
          if (issue) {
            githubIssueUrl = issue.issueUrl
            if (typeof opts.storage.setGitHubIssue === 'function') {
              await opts.storage
                .setGitHubIssue(id, issue.issueUrl, issue.issueNumber)
                .catch((err) =>
                  console.warn(`[bug-fab] setGitHubIssue failed: ${String(err)}`),
                )
            }
          }
        } catch (err) {
          console.warn(`[bug-fab] github sync failed: ${String(err)}`)
        }
      }

      const response: BugReportIntakeResponse = {
        id,
        received_at: receivedAt,
        // Opaque diagnostic string. Edge runtimes don't have a
        // filesystem URI, so use a generic scheme.
        stored_at: `bug-fab:${id}`,
        github_issue_url: githubIssueUrl,
      }
      return c.json(response, 201)
    } catch (err) {
      console.error('[bug-fab] saveReport failed:', err)
      return c.json(Errors.storageUnavailable(), 503)
    }
  })

  return intake
}
