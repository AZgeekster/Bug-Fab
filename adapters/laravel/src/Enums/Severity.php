<?php

declare(strict_types=1);

namespace BugFab\Laravel\Enums;

/**
 * Locked severity vocabulary — adapters MUST reject other values with 422.
 *
 * Silent coercion (e.g., rewriting "urgent" to "medium") fails conformance.
 * See PROTOCOL.md § Severity enum.
 */
enum Severity: string
{
    case Low = 'low';
    case Medium = 'medium';
    case High = 'high';
    case Critical = 'critical';
}
