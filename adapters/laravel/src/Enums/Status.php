<?php

declare(strict_types=1);

namespace BugFab\Laravel\Enums;

/**
 * Locked status vocabulary for the lifecycle workflow.
 *
 * Per the deprecated-values rule in PROTOCOL.md § Versioning, adapters MUST
 * accept deprecated values on READ but MAY reject them on WRITE. This enum
 * encodes write-side strictness; read paths use raw string comparisons.
 */
enum Status: string
{
    case Open = 'open';
    case Investigating = 'investigating';
    case Fixed = 'fixed';
    case Closed = 'closed';
}
