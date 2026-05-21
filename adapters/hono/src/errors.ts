// Bug-Fab error envelope factories.
//
// Every non-2xx response (except 204 and the binary 404 on
// GET /reports/:id/screenshot) MUST carry the `{ error, detail }`
// envelope per docs/PROTOCOL.md § Error response shape. Hono's default
// 500 handler emits plain text — `app.onError(...)` in app.ts re-routes
// to `internalError()` to preserve the envelope.

export interface BugFabErrorBody {
  error: string
  detail: string | Array<Record<string, unknown>>
  /** Present on 413 only. Names the actual cap (e.g., 10 MiB). */
  limit_bytes?: number
  /** Present on 429 only when the adapter knows how long to wait. */
  retry_after_seconds?: number
}

function envelope(code: string, detail: string): BugFabErrorBody {
  return { error: code, detail }
}

export const Errors = {
  validationError: (detail: string): BugFabErrorBody =>
    envelope('validation_error', detail),

  schemaError: (detail: string): BugFabErrorBody =>
    envelope('schema_error', detail),

  unsupportedProtocolVersion: (version: string): BugFabErrorBody =>
    envelope(
      'unsupported_protocol_version',
      `Unknown protocol_version: "${version}". Only "0.1" is supported.`,
    ),

  payloadTooLarge: (limitBytes: number): BugFabErrorBody => ({
    error: 'payload_too_large',
    detail: `Payload exceeds the configured cap of ${limitBytes} bytes.`,
    limit_bytes: limitBytes,
  }),

  unsupportedMediaType: (
    detail = 'Screenshot must be PNG (multipart/form-data).',
  ): BugFabErrorBody => envelope('unsupported_media_type', detail),

  notFound: (subject: string): BugFabErrorBody =>
    envelope('not_found', `${subject} not found.`),

  rateLimited: (retryAfterSeconds?: number): BugFabErrorBody => {
    const body: BugFabErrorBody = {
      error: 'rate_limited',
      detail: 'Too many submissions from this IP. Please wait before trying again.',
    }
    if (retryAfterSeconds !== undefined) body.retry_after_seconds = retryAfterSeconds
    return body
  },

  internalError: (detail = 'An unexpected error occurred.'): BugFabErrorBody =>
    envelope('internal_error', detail),

  storageUnavailable: (detail = 'Storage backend is unavailable.'): BugFabErrorBody =>
    envelope('storage_unavailable', detail),

  forbidden: (action: string): BugFabErrorBody =>
    envelope(
      'forbidden',
      `viewer action '${action}' is disabled by configuration`,
    ),
} as const
