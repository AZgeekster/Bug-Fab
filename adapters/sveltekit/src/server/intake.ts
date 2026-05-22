// Intake handler factory — POST /bug-reports.
//
// Returns a SvelteKit RequestHandler. Consumers wire it into a `+server.ts`:
//
//   // src/routes/api/bug-reports/+server.ts
//   import { createIntakeHandler } from 'bug-fab-sveltekit/server';
//   import { storage } from '$lib/server/bug-fab';
//   export const POST = createIntakeHandler({ storage });
//
// The factory pattern keeps consumers in control of the URL and any
// per-route hooks (CSRF exemption, custom middleware in `hooks.server.ts`).

import { json } from '@sveltejs/kit';
import type { RequestEvent, RequestHandler } from '@sveltejs/kit';
import { Errors, jsonError } from './errors.js';
import {
  validateSubmission,
  isValidPngMagic,
  asSubmission,
  MAX_SCREENSHOT_BYTES
} from './validation.js';
import { syncToGitHubIssues } from './github.js';
import type { BugFabAdapterOptions, BugReportIntakeResponse } from './types.js';

export function createIntakeHandler(opts: BugFabAdapterOptions): RequestHandler {
  const maxBytes = opts.maxScreenshotBytes ?? MAX_SCREENSHOT_BYTES;

  return async (event: RequestEvent): Promise<Response> => {
    const { request } = event;

    // Content-Type check.
    //
    // Per docs/PROTOCOL.md §Error mapping:
    //   - "multipart Content-Type wrong" (e.g., application/json) → 415
    //   - "multipart missing required parts" → 400 validation_error
    //
    // We accept any form-style envelope here (multipart OR urlencoded);
    // the missing-parts check below distinguishes the two error modes.
    // An urlencoded body can never carry a file, so it falls through to
    // the missing-screenshot 400 path — which is what the protocol
    // mandates for that case. httpx's `client.post(..., files=None)`
    // sends `application/x-www-form-urlencoded`, so without this branch
    // the conformance `test_missing_screenshot_is_rejected` would see a
    // wrong-shape 415 instead of the required 400.
    const contentType = (request.headers.get('content-type') ?? '').toLowerCase();
    const isFormEnvelope =
      contentType.startsWith('multipart/form-data') ||
      contentType.startsWith('application/x-www-form-urlencoded');
    if (!isFormEnvelope) {
      return jsonError(
        Errors.unsupportedMediaType('Content-Type must be multipart/form-data with metadata + screenshot parts.'),
        415
      );
    }

    // Parse the multipart body. `formData()` is single-shot — we only call it once.
    let form: FormData;
    try {
      form = await request.formData();
    } catch (err) {
      return jsonError(
        Errors.validationError(`Failed to parse multipart body: ${err instanceof Error ? err.message : String(err)}`),
        400
      );
    }

    const metadataRaw = form.get('metadata');
    const screenshot = form.get('screenshot');

    if (typeof metadataRaw !== 'string') {
      return jsonError(
        Errors.validationError('metadata part is required and must be a JSON string'),
        400
      );
    }
    if (!(screenshot instanceof File) && !(screenshot instanceof Blob)) {
      return jsonError(Errors.validationError('screenshot part is required'), 400);
    }

    // Screenshot size + content-type checks. We check the declared MIME first
    // for a quick reject; magic bytes verify after we read.
    const declaredType = (screenshot as File).type ?? '';
    if (declaredType && declaredType !== 'image/png') {
      return jsonError(Errors.unsupportedMediaType(), 415);
    }
    if (screenshot.size > maxBytes) {
      return jsonError(Errors.payloadTooLarge(maxBytes), 413);
    }

    let screenshotBytes: Uint8Array;
    try {
      screenshotBytes = new Uint8Array(await screenshot.arrayBuffer());
    } catch (err) {
      return jsonError(
        Errors.validationError(`Failed to read screenshot: ${err instanceof Error ? err.message : String(err)}`),
        400
      );
    }

    if (!isValidPngMagic(screenshotBytes)) {
      return jsonError(
        Errors.unsupportedMediaType('Screenshot is not a valid PNG (magic bytes mismatch).'),
        415
      );
    }

    // Parse metadata JSON.
    let parsed: unknown;
    try {
      parsed = JSON.parse(metadataRaw);
    } catch (err) {
      return jsonError(
        Errors.validationError(`metadata is not valid JSON: ${err instanceof Error ? err.message : String(err)}`),
        400
      );
    }

    const result = validateSubmission(parsed);
    if (!result.ok) {
      if (result.unsupportedProtocolVersion !== undefined) {
        return jsonError(
          Errors.unsupportedProtocolVersion(result.unsupportedProtocolVersion),
          400
        );
      }
      return jsonError(Errors.schemaError(result.errors), 422);
    }

    const submission = asSubmission(parsed);

    // Server-captured User-Agent — source of truth.
    const serverUserAgent = request.headers.get('user-agent') ?? '';
    const clientReportedUserAgent =
      typeof submission.context?.user_agent === 'string' ? submission.context.user_agent : undefined;

    let saved: { id: string; storedAt: string; receivedAt: string };
    try {
      saved = await opts.storage.saveReport({
        submission,
        serverUserAgent,
        clientReportedUserAgent,
        screenshotBytes
      });
    } catch (err) {
      console.error(`[bug-fab] storage.saveReport failed: ${err instanceof Error ? err.message : String(err)}`);
      return jsonError(Errors.storageUnavailable(), 503);
    }

    // Best-effort GitHub Issues sync. Failures MUST NOT cause 5xx.
    let githubIssueUrl: string | null = null;
    if (opts.github?.enabled) {
      try {
        const detail = await opts.storage.getReport(saved.id);
        if (detail) {
          const issue = await syncToGitHubIssues(detail, opts.github);
          if (issue && opts.storage.setGitHubIssue) {
            await opts.storage.setGitHubIssue(saved.id, issue.url, issue.number);
            githubIssueUrl = issue.url;
          }
        }
      } catch (err) {
        console.warn(
          `[bug-fab] GitHub sync threw during intake: ${err instanceof Error ? err.message : String(err)}`
        );
      }
    }

    const body: BugReportIntakeResponse = {
      id: saved.id,
      received_at: saved.receivedAt,
      stored_at: saved.storedAt,
      github_issue_url: githubIssueUrl
    };

    return json(body, { status: 201 });
  };
}
