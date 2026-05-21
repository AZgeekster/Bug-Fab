<?php

declare(strict_types=1);

namespace BugFab\Laravel\Schemas;

use BugFab\Laravel\Enums\Severity;
use BugFab\Laravel\Enums\Status;

/**
 * Full report detail. Extends summary with description, expected_behavior,
 * tags, reporter, context, lifecycle, user-agent dual fields, and the
 * version/timestamp tracking.
 *
 * Mirrors BugReportDetail in bug_fab/schemas.py one-for-one to keep
 * conformance suite expectations met.
 */
final class BugReportDetail
{
    /**
     * @param array<string,mixed> $reporter
     * @param array<string,mixed> $context
     * @param array<int,array<string,mixed>> $lifecycle
     * @param array<int,string> $tags
     */
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
        public readonly string $description = '',
        public readonly string $expected_behavior = '',
        public readonly array $tags = [],
        public readonly array $reporter = [],
        public readonly array $context = [],
        public readonly array $lifecycle = [],
        public readonly string $server_user_agent = '',
        public readonly string $client_reported_user_agent = '',
        public readonly string $environment = '',
        public readonly string $client_ts = '',
        public readonly string $protocol_version = '0.1',
        public readonly string $updated_at = '',
        public readonly ?int $github_issue_number = null,
    ) {
    }

    public function toArray(): array
    {
        return [
            'id'                         => $this->id,
            'title'                      => $this->title,
            'report_type'                => $this->report_type,
            'severity'                   => $this->severity,
            'status'                     => $this->status,
            'module'                     => $this->module,
            'created_at'                 => $this->created_at,
            'has_screenshot'             => $this->has_screenshot,
            'github_issue_url'           => $this->github_issue_url,
            'description'                => $this->description,
            'expected_behavior'          => $this->expected_behavior,
            'tags'                       => $this->tags,
            // Always emit the full reporter shape — sub-fields default to ""
            // so consumers can rely on keys existing.
            'reporter'                   => [
                'name'    => (string) ($this->reporter['name'] ?? ''),
                'email'   => (string) ($this->reporter['email'] ?? ''),
                'user_id' => (string) ($this->reporter['user_id'] ?? ''),
            ],
            'context'                    => $this->context,
            'lifecycle'                  => $this->lifecycle,
            'server_user_agent'          => $this->server_user_agent,
            'client_reported_user_agent' => $this->client_reported_user_agent,
            'environment'                => $this->environment,
            'client_ts'                  => $this->client_ts,
            'protocol_version'           => $this->protocol_version,
            'updated_at'                 => $this->updated_at,
            'github_issue_number'        => $this->github_issue_number,
        ];
    }
}
