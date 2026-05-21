// POST /bug-reports — Bug-Fab intake handler.
//
// Reference: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md § "POST /bug-reports"

import type { NextFunction, Request, Response, RequestHandler } from 'express'
import multer from 'multer'

import { Errors } from './errors.js'
import {
  validateSubmission, isValidPngBuffer, PROTOCOL_VERSION_SENTINEL,
  DEFAULT_MAX_SCREENSHOT_BYTES,
} from './validation.js'
import { createGitHubIssue } from './github.js'
import type {
  BugFabRouterOptions, IStorage, Logger, ReportType, Severity,
  BugReportContext, Reporter,
} from './types.js'

// ----- Rate limiter (in-memory, per-IP, sliding window) -----
//
// Multi-process caveat: this is local to a single Node process. PM2-clustered
// or load-balanced deployments should rely on a reverse-proxy rate limiter.

const rateLimitMap = new Map<string, number[]>()

function checkRateLimit(ip: string, maxRequests: number, windowMs: number): boolean {
  const now = Date.now()
  const start = now - windowMs
  const times = (rateLimitMap.get(ip) ?? []).filter((t) => t > start)
  if (times.length >= maxRequests) {
    rateLimitMap.set(ip, times)
    return false
  }
  times.push(now)
  rateLimitMap.set(ip, times)
  return true
}

// ----- Multer error translator -----
//
// `multer` rejects oversized files mid-stream with a `LIMIT_FILE_SIZE` error
// passed through Express's error pipeline. Map it to the protocol's 413.
// Also map our own fileFilter rejections to 415.

interface MulterErrorWithCode extends Error {
  code?: string
}

function isMulterError(err: unknown): err is MulterErrorWithCode {
  return err instanceof Error && (err as MulterErrorWithCode).code !== undefined
}

function translateMulterError(err: unknown, maxBytes: number): {
  status: number
  body:   ReturnType<typeof Errors.payloadTooLarge | typeof Errors.unsupportedMediaType | typeof Errors.validationError>
} {
  if (isMulterError(err)) {
    if (err.code === 'LIMIT_FILE_SIZE') {
      return { status: 413, body: Errors.payloadTooLarge(maxBytes) }
    }
    if (err.message === 'unsupported_media_type') {
      return { status: 415, body: Errors.unsupportedMediaType() }
    }
    return { status: 400, body: Errors.validationError(`multipart parse error: ${err.code ?? err.message}`) }
  }
  return { status: 400, body: Errors.validationError('multipart parse error') }
}

// ----- Multer factory -----

export function buildIntakeMulter(maxBytes: number): multer.Multer {
  return multer({
    storage: multer.memoryStorage(),
    limits:  { fileSize: maxBytes, fields: 10, files: 1 },
    fileFilter: (_req, file, cb) => {
      // We accept the upload here regardless of mimetype so we can return a
      // structured 415 from the handler with the protocol's error envelope.
      // (multer's fileFilter rejection routes through the error middleware
      // and is awkward to translate cleanly otherwise.)
      //
      // The Content-Type _hint_ is checked too — but the magic-byte check
      // below is the source of truth. Browsers sometimes mis-label PNGs
      // and html2canvas always emits image/png, so this is the right order.
      if (file.fieldname !== 'screenshot') {
        return cb(new Error(`unexpected file field: ${file.fieldname}`))
      }
      cb(null, true)
    },
  })
}

// ----- Intake handler factory -----

export function buildIntakeHandler(
  storage:   IStorage,
  options:   BugFabRouterOptions,
  log:       Logger,
  maxBytes:  number,
): RequestHandler {
  return async (req: Request, res: Response, _next: NextFunction): Promise<void> => {
    // Rate-limit gate
    if (options.rateLimit?.enabled) {
      const ip = req.ip ?? 'unknown'
      if (!checkRateLimit(ip, options.rateLimit.maxRequests, options.rateLimit.windowMs)) {
        res.status(429).json(Errors.rateLimited())
        return
      }
    }

    // multer should have populated req.body.metadata + req.file by now.
    const metadataRaw: unknown = (req.body as Record<string, unknown> | undefined)?.metadata
    const file = req.file

    if (typeof metadataRaw !== 'string') {
      res.status(400).json(Errors.validationError('metadata field is required (multipart text part).'))
      return
    }
    if (!file) {
      res.status(400).json(Errors.validationError('screenshot file is required (multipart file part).'))
      return
    }

    // Magic-byte PNG check — this rejects JPEGs, GIFs, and bare bytes
    // regardless of the Content-Type the client claimed.
    if (!isValidPngBuffer(file.buffer)) {
      res.status(415).json(Errors.unsupportedMediaType())
      return
    }
    // Defensive size check — multer should have already enforced this, but
    // we recheck in case a custom multer config raised the limit.
    if (file.buffer.length > maxBytes) {
      res.status(413).json(Errors.payloadTooLarge(maxBytes))
      return
    }

    let parsed: unknown
    try {
      parsed = JSON.parse(metadataRaw)
    } catch (err) {
      res.status(400).json(Errors.validationError(`metadata is not valid JSON: ${(err as Error).message}`))
      return
    }

    const result = validateSubmission(parsed)
    if (!result.ok) {
      const first = result.errors[0] ?? ''
      if (first.startsWith(`${PROTOCOL_VERSION_SENTINEL}:`)) {
        const v = first.slice(PROTOCOL_VERSION_SENTINEL.length + 1)
        res.status(400).json(Errors.unsupportedProtocolVersion(v))
        return
      }
      res.status(422).json(Errors.schemaError(result.errors.join('; ')))
      return
    }

    const submission = parsed as Record<string, unknown>

    const reporter: Reporter = {
      name:    asString((submission.reporter as Record<string, unknown> | undefined)?.name),
      email:   asString((submission.reporter as Record<string, unknown> | undefined)?.email),
      user_id: asString((submission.reporter as Record<string, unknown> | undefined)?.user_id),
    }
    const context: BugReportContext = (submission.context as BugReportContext) ?? {}

    const serverUserAgent  = req.header('user-agent') ?? ''
    const clientUserAgent  = typeof context.user_agent === 'string' ? context.user_agent : ''

    try {
      const id = await storage.saveReport(
        {
          protocol_version:           submission.protocol_version as string,
          title:                      submission.title as string,
          description:                asString(submission.description),
          expected_behavior:          asString(submission.expected_behavior),
          report_type:                ((submission.report_type as ReportType | undefined) ?? 'bug'),
          severity:                   ((submission.severity as Severity | undefined) ?? 'medium'),
          tags:                       Array.isArray(submission.tags) ? (submission.tags as string[]) : [],
          reporter,
          context,
          client_ts:                  submission.client_ts as string,
          server_user_agent:          serverUserAgent,
          client_reported_user_agent: clientUserAgent,
        },
        file.buffer,
      )

      const receivedAt = new Date().toISOString()
      let githubIssueUrl: string | null = null

      // Best-effort GitHub sync — failures logged, response always 201.
      if (options.github?.enabled && options.github.pat && options.github.repo) {
        const ghResult = await createGitHubIssue(
          {
            pat:     options.github.pat,
            repo:    options.github.repo,
            apiBase: options.github.apiBase,
          },
          {
            id,
            title:       submission.title as string,
            description: asString(submission.description),
            severity:    (submission.severity as Severity | undefined) ?? 'medium',
            tags:        Array.isArray(submission.tags) ? (submission.tags as string[]) : [],
            context: {
              url:         typeof context.url === 'string' ? context.url : undefined,
              environment: typeof context.environment === 'string' ? context.environment : undefined,
              app_version: typeof context.app_version === 'string' ? context.app_version : undefined,
            },
          },
          log,
        )

        if (ghResult) {
          githubIssueUrl = ghResult.issueUrl
          if (typeof storage.setGitHubIssue === 'function') {
            try {
              await storage.setGitHubIssue(id, ghResult.issueUrl, ghResult.issueNumber)
            } catch (err) {
              log.warn(`[bug-fab-express] setGitHubIssue failed for ${id}`, err)
            }
          }
        }
      }

      // The intake response is intentionally minimal per the protocol —
      // do NOT echo user-submitted free text in the body.
      const storedAt = computeStoredAt(storage, id)
      res.status(201).json({
        id,
        received_at:      receivedAt,
        stored_at:        storedAt,
        github_issue_url: githubIssueUrl,
      })
    } catch (err) {
      log.error('[bug-fab-express] saveReport failed', err)
      res.status(503).json(Errors.storageUnavailable())
    }
  }
}

// ----- Multer error middleware -----
//
// Mounted on the same router AFTER the intake route so multer-thrown errors
// (most commonly LIMIT_FILE_SIZE) get translated into the wire envelope.
// Express recognises 4-arg middleware as an error handler.
export function buildMulterErrorHandler(maxBytes: number): (
  err:  unknown,
  req:  Request,
  res:  Response,
  next: NextFunction,
) => void {
  return (err, _req, res, next): void => {
    // Pass through any non-multer error to the next handler — the consumer's
    // own error middleware should deal with it.
    if (!isMulterError(err) && !(err instanceof Error && err.message === 'unsupported_media_type')) {
      next(err)
      return
    }
    const { status, body } = translateMulterError(err, maxBytes)
    res.status(status).json(body)
  }
}

export const INTAKE_DEFAULTS = {
  maxBytes: DEFAULT_MAX_SCREENSHOT_BYTES,
}

// ----- helpers -----

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function computeStoredAt(storage: IStorage, id: string): string {
  // FileStorage exposes a helper; other backends fall back to the opaque
  // adapter-defined string. The protocol does not validate stored_at format.
  const maybe = storage as unknown as { storedAtFor?: (id: string) => string }
  if (typeof maybe.storedAtFor === 'function') return maybe.storedAtFor(id)
  return `bug-fab-express:${id}`
}
