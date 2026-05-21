// Bug-Fab error response factories.
//
// CRITICAL: We do NOT use SvelteKit's `error()` helper — it throws an
// `HttpError` whose default body shape is `{ message }`, which doesn't match
// the protocol's `{ error, detail }` envelope. Instead, every handler returns
// `json(errorBody, { status })` directly.
//
// Reference: repo/docs/PROTOCOL.md § Error response shape.

import { json } from '@sveltejs/kit';

export interface BugFabErrorBody {
  error: string;
  detail: string | unknown[];
  /** Used by 413 payload_too_large */
  limit_bytes?: number;
  /** Used by 429 rate_limited */
  retry_after_seconds?: number;
}

function body(code: string, detail: string | unknown[], extras: Record<string, unknown> = {}): BugFabErrorBody {
  return { error: code, detail, ...extras };
}

export const Errors = {
  validationError: (detail: string | unknown[]) => body('validation_error', detail),
  schemaError: (detail: string | unknown[]) => body('schema_error', detail),
  unsupportedProtocolVersion: (version: string) =>
    body(
      'unsupported_protocol_version',
      `Unknown protocol_version: "${version}". Only "0.1" is supported.`
    ),
  unsupportedMediaType: (detail = 'Screenshot must be image/png.') =>
    body('unsupported_media_type', detail),
  payloadTooLarge: (limitBytes: number) =>
    body('payload_too_large', `Request exceeds maximum size of ${limitBytes} bytes.`, { limit_bytes: limitBytes }),
  notFound: (id: string) => body('not_found', `Report "${id}" not found.`),
  rateLimited: (retryAfterSeconds?: number) =>
    body(
      'rate_limited',
      'Too many submissions from this IP. Please wait before trying again.',
      retryAfterSeconds !== undefined ? { retry_after_seconds: retryAfterSeconds } : {}
    ),
  internalError: (detail = 'An unexpected error occurred.') => body('internal_error', detail),
  storageUnavailable: (detail = 'Storage backend is unavailable.') =>
    body('storage_unavailable', detail)
};

/** Convenience: build a JSON error Response with the protocol envelope. */
export function jsonError(payload: BugFabErrorBody, status: number): Response {
  return json(payload, { status });
}
