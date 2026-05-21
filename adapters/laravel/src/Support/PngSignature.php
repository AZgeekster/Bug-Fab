<?php

declare(strict_types=1);

namespace BugFab\Laravel\Support;

/**
 * PNG magic-byte sniffer. The wire protocol locks intake to image/png; the
 * Content-Type header alone is not trusted because clients lie. We verify
 * the first 8 bytes match the PNG signature before persisting.
 */
final class PngSignature
{
    /** \x89PNG\r\n\x1a\n — the only image format accepted on intake. */
    public const SIGNATURE = "\x89PNG\r\n\x1a\n";

    public static function verify(string $bytes): bool
    {
        return strlen($bytes) >= 8 && substr($bytes, 0, 8) === self::SIGNATURE;
    }
}
