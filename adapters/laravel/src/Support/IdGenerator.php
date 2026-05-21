<?php

declare(strict_types=1);

namespace BugFab\Laravel\Support;

/**
 * Bug-Fab ID format helpers.
 *
 * IDs match the protocol regex ^bug-[A-Za-z]?\d{3,}$. An optional one-letter
 * prefix (configured via BUG_FAB_ID_PREFIX) lets multi-environment shared
 * collectors disambiguate (bug-P038 for prod, bug-D012 for dev).
 *
 * The counter is shared across prefixes — strip the optional alpha character
 * when parsing existing IDs.
 */
final class IdGenerator
{
    /** Pattern for ID validation — keep in sync with the route constraint and PROTOCOL.md. */
    public const PATTERN = '/^bug-[A-Za-z]?\d{1,12}$/';

    public static function isValid(string $reportId): bool
    {
        return (bool) preg_match(self::PATTERN, $reportId);
    }

    public static function format(int $n, string $prefix = ''): string
    {
        return sprintf('bug-%s%03d', $prefix, $n);
    }

    /** Extract the numeric portion from a bug-XYNNN ID. Returns 0 on parse failure. */
    public static function parseNumber(string $reportId): int
    {
        $raw = preg_replace('/^bug-/', '', $reportId);
        $digits = preg_replace('/\D/', '', (string) $raw);

        return $digits === '' ? 0 : (int) $digits;
    }
}
