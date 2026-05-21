<?php

declare(strict_types=1);

namespace BugFab\Laravel\Schemas;

use BugFab\Laravel\Enums\Severity;
use BugFab\Laravel\Enums\Status;

/**
 * Compact representation used by list views. Mirrors the Python
 * BugReportSummary in bug_fab/schemas.py field-for-field.
 *
 * Adapter code constructs these from storage rows; the controllers
 * serialize to JSON via toArray().
 */
final class BugReportSummary
{
    public function __construct(
        public readonly string $id,
        public readonly string $title,
        public readonly string $report_type = 'bug',
        public readonly string $severity = 'medium',
        public readonly string $status = 'open',
        public readonly string $module = '',
        public readonly string $created_at = '',
        public readonly bool $has_screenshot = true,
        public readonly ?string $github_issue_url = null,
    ) {
    }

    public function toArray(): array
    {
        return [
            'id'                => $this->id,
            'title'             => $this->title,
            'report_type'       => $this->report_type,
            'severity'          => $this->severity,
            'status'            => $this->status,
            'module'            => $this->module,
            'created_at'        => $this->created_at,
            'has_screenshot'    => $this->has_screenshot,
            'github_issue_url'  => $this->github_issue_url,
        ];
    }

    public static function fromArray(array $data): self
    {
        return new self(
            id:                (string) ($data['id'] ?? ''),
            title:             (string) ($data['title'] ?? ''),
            report_type:       (string) ($data['report_type'] ?? 'bug'),
            severity:          (string) ($data['severity'] ?? Severity::Medium->value),
            status:            (string) ($data['status'] ?? Status::Open->value),
            module:            (string) ($data['module'] ?? ''),
            created_at:        (string) ($data['created_at'] ?? ''),
            has_screenshot:    (bool) ($data['has_screenshot'] ?? true),
            github_issue_url:  $data['github_issue_url'] ?? null,
        );
    }
}
