<?php

declare(strict_types=1);

namespace BugFab\Laravel\Support;

use Illuminate\Http\JsonResponse;

/**
 * Standard error-envelope builder. Every non-2xx response (except 204 and
 * the binary 404 from /screenshot) MUST shape as { error, detail }.
 *
 * See PROTOCOL.md § Error response shape.
 */
final class Errors
{
    public const VALIDATION_ERROR             = 'validation_error';
    public const UNSUPPORTED_PROTOCOL_VERSION = 'unsupported_protocol_version';
    public const PAYLOAD_TOO_LARGE            = 'payload_too_large';
    public const UNSUPPORTED_MEDIA_TYPE       = 'unsupported_media_type';
    public const SCHEMA_ERROR                 = 'schema_error';
    public const RATE_LIMITED                 = 'rate_limited';
    public const NOT_FOUND                    = 'not_found';
    public const INTERNAL_ERROR               = 'internal_error';
    public const STORAGE_UNAVAILABLE          = 'storage_unavailable';

    public static function json(string $code, mixed $detail, int $status, array $extra = []): JsonResponse
    {
        $body = ['error' => $code, 'detail' => $detail];
        foreach ($extra as $key => $value) {
            $body[$key] = $value;
        }

        return new JsonResponse($body, $status);
    }
}
