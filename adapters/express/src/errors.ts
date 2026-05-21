// Bug-Fab error envelope factories.
//
// All non-2xx responses (except 204 and the binary screenshot 404) use the
// envelope `{ error: <code>, detail: <string> }` per
// https://github.com/AZgeekster/Bug-Fab/blob/main/docs/PROTOCOL.md § "Error response shape".

export interface BugFabErrorBody {
  error:  string
  detail: string
  // Some codes carry extra hints (e.g. payload_too_large carries limit_bytes).
  [extra: string]: unknown
}

function envelope(code: string, detail: string, extra?: Record<string, unknown>): BugFabErrorBody {
  return { error: code, detail, ...(extra ?? {}) }
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

  payloadTooLarge: (limitBytes: number): BugFabErrorBody =>
    envelope(
      'payload_too_large',
      `Payload exceeds the maximum size of ${limitBytes} bytes.`,
      { limit_bytes: limitBytes },
    ),

  unsupportedMediaType: (detail = 'Screenshot must be PNG (image/png).'): BugFabErrorBody =>
    envelope('unsupported_media_type', detail),

  notFound: (what: string): BugFabErrorBody =>
    envelope('not_found', `${what} not found.`),

  rateLimited: (retryAfterSeconds?: number): BugFabErrorBody =>
    envelope(
      'rate_limited',
      'Too many submissions from this IP. Please wait before trying again.',
      retryAfterSeconds !== undefined ? { retry_after_seconds: retryAfterSeconds } : undefined,
    ),

  internalError: (detail = 'An unexpected error occurred.'): BugFabErrorBody =>
    envelope('internal_error', detail),

  storageUnavailable: (detail = 'Storage backend is unavailable.'): BugFabErrorBody =>
    envelope('storage_unavailable', detail),
}
