<?php

declare(strict_types=1);

namespace BugFab\Laravel\Storage;

use BugFab\Laravel\Enums\Severity;
use BugFab\Laravel\Enums\Status;
use BugFab\Laravel\Models\BugReport;
use BugFab\Laravel\Models\BugReportLifecycle;
use BugFab\Laravel\Schemas\BugReportDetail;
use BugFab\Laravel\Schemas\BugReportSummary;
use BugFab\Laravel\Support\IdGenerator;
use Illuminate\Contracts\Filesystem\Filesystem;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage as StorageFacade;

/**
 * Eloquent-backed storage. Use this for multi-worker FPM, Octane, or any
 * horizontally scaled deployment — file storage cannot coordinate across
 * processes without an external lock.
 *
 * Screenshots are written to a Laravel filesystem disk (default "local")
 * with the relative path persisted on the row.
 */
final class EloquentStorage implements StorageContract
{
    private Filesystem $disk;
    private string $screenshotPath;
    private string $idPrefix;

    public function __construct(string $disk, string $screenshotPath, string $idPrefix = '')
    {
        $this->disk = StorageFacade::disk($disk);
        $this->screenshotPath = trim($screenshotPath, '/');
        $this->idPrefix = $idPrefix;
    }

    public function saveReport(array $metadata, string $screenshotBytes): string
    {
        $title = (string) ($metadata['title'] ?? '');
        if ($title === '') {
            throw new \InvalidArgumentException('metadata.title is required');
        }
        $severity = (string) ($metadata['severity'] ?? Severity::Medium->value);

        $context = (array) ($metadata['context'] ?? []);
        $reporter = (array) ($metadata['reporter'] ?? []);

        $reportId = '';
        DB::connection(config('bugfab.storages.eloquent.connection'))
          ->transaction(function () use (&$reportId, $metadata, $context, $reporter, $severity, $title, $screenshotBytes) {
            // Allocate next ID under a lock so two parallel inserts don't
            // collide. select_for_update style.
            $last = BugReport::query()
                ->orderBy('received_at', 'desc')
                ->orderBy('id', 'desc')
                ->lockForUpdate()
                ->first();
            $nextInt = $last === null ? 1 : (IdGenerator::parseNumber($last->id) + 1);
            $reportId = IdGenerator::format($nextInt, $this->idPrefix);

            $screenshotRelPath = "{$this->screenshotPath}/{$reportId}.png";
            $this->disk->put($screenshotRelPath, $screenshotBytes);

            $now = now();
            $row = new BugReport();
            $row->id = $reportId;
            $row->received_at = $now;
            $row->protocol_version = (string) ($metadata['protocol_version'] ?? '0.1');
            $row->title = $title;
            $row->description = (string) ($metadata['description'] ?? '');
            $row->severity = $severity;
            $row->status = Status::Open->value;
            $row->report_type = (string) ($metadata['report_type'] ?? 'bug');
            $row->environment = (string) ($metadata['environment'] ?? ($context['environment'] ?? ''));
            $row->app_version = (string) ($context['app_version'] ?? '');
            $row->module = (string) ($metadata['module'] ?? ($context['module'] ?? ''));
            $row->reporter = $this->printableReporter($reporter);
            $row->page_url = (string) ($context['url'] ?? '');
            $row->user_agent_server = (string) ($metadata['server_user_agent'] ?? '');
            $row->user_agent_client = (string) ($context['user_agent'] ?? '');
            $row->metadata_json = json_encode($metadata, JSON_UNESCAPED_SLASHES);
            $row->screenshot_path = $screenshotRelPath;
            $row->github_issue_url = '';
            $row->save();

            BugReportLifecycle::create([
                'bug_report_id'   => $reportId,
                'action'          => 'created',
                'by'              => (string) ($metadata['submitted_by'] ?? 'anonymous'),
                'at'              => $now,
                'fix_commit'      => '',
                'fix_description' => '',
            ]);
        });

        return $reportId;
    }

    public function getReport(string $reportId): ?BugReportDetail
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $row = BugReport::with('lifecycle')->find($reportId);
        if ($row === null) {
            return null;
        }

        return $this->toDetail($row);
    }

    public function listReports(array $filters, int $page, int $pageSize): array
    {
        $page = max(1, $page);
        $pageSize = max(1, min($pageSize, 200));
        $q = BugReport::query();

        if (empty($filters['include_archived'])) {
            $q->whereNull('archived_at');
        }
        foreach (['status', 'severity', 'environment', 'module', 'report_type'] as $key) {
            $val = $filters[$key] ?? null;
            if ($val !== null && $val !== '') {
                $q->where($key, $val);
            }
        }
        if (! empty($filters['search'])) {
            $needle = '%' . mb_strtolower((string) $filters['search']) . '%';
            $q->where(function ($w) use ($needle) {
                $w->whereRaw('LOWER(title) LIKE ?', [$needle])
                  ->orWhereRaw('LOWER(module) LIKE ?', [$needle])
                  ->orWhereRaw('LOWER(id) LIKE ?', [$needle]);
            });
        }

        $total = $q->count();
        $rows = $q->orderBy('received_at', 'desc')
                  ->orderBy('id', 'desc')
                  ->offset(($page - 1) * $pageSize)
                  ->limit($pageSize)
                  ->get();
        $items = $rows->map(fn (BugReport $r) => $this->toSummary($r))->all();

        return [$items, $total];
    }

    public function getScreenshot(string $reportId): ?array
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $row = BugReport::find($reportId);
        if ($row === null || ! $row->screenshot_path) {
            return null;
        }
        if (! $this->disk->exists($row->screenshot_path)) {
            return null;
        }

        return [
            'bytes' => (string) $this->disk->get($row->screenshot_path),
            'path'  => method_exists($this->disk, 'path')
                ? $this->disk->path($row->screenshot_path)
                : null,
        ];
    }

    public function updateStatus(
        string $reportId,
        string $status,
        string $fixCommit = '',
        string $fixDescription = '',
        string $by = ''
    ): ?BugReportDetail {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $result = null;
        DB::connection(config('bugfab.storages.eloquent.connection'))
          ->transaction(function () use ($reportId, $status, $fixCommit, $fixDescription, $by, &$result) {
            $row = BugReport::lockForUpdate()->find($reportId);
            if ($row === null) {
                return;
            }
            $row->status = $status;
            $row->save();
            BugReportLifecycle::create([
                'bug_report_id'   => $reportId,
                'action'          => 'status_changed',
                'by'              => $by,
                'at'              => now(),
                'fix_commit'      => $fixCommit,
                'fix_description' => $fixDescription,
                'metadata_json'   => json_encode(['status' => $status]),
            ]);
            $result = $row->fresh('lifecycle');
        });

        return $result === null ? null : $this->toDetail($result);
    }

    public function setGitHubLink(string $reportId, int $issueNumber, string $issueUrl): ?BugReportDetail
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $row = BugReport::find($reportId);
        if ($row === null) {
            return null;
        }
        $row->github_issue_number = $issueNumber;
        $row->github_issue_url = $issueUrl;
        $row->save();

        return $this->toDetail($row->fresh('lifecycle'));
    }

    public function deleteReport(string $reportId): bool
    {
        if (! IdGenerator::isValid($reportId)) {
            return false;
        }
        $row = BugReport::find($reportId);
        if ($row === null) {
            return false;
        }
        $screenshotPath = $row->screenshot_path;
        $row->delete();
        if ($screenshotPath && $this->disk->exists($screenshotPath)) {
            $this->disk->delete($screenshotPath);
        }

        return true;
    }

    public function archiveReport(string $reportId): bool
    {
        if (! IdGenerator::isValid($reportId)) {
            return false;
        }
        $row = BugReport::find($reportId);
        if ($row === null || $row->archived_at !== null) {
            return false;
        }
        $now = now();
        $row->archived_at = $now;
        $row->save();
        BugReportLifecycle::create([
            'bug_report_id' => $reportId,
            'action'        => 'archived',
            'by'            => '',
            'at'            => $now,
        ]);

        return true;
    }

    public function bulkCloseFixed(string $by = ''): int
    {
        $now = now();
        $count = 0;
        DB::connection(config('bugfab.storages.eloquent.connection'))
          ->transaction(function () use ($by, $now, &$count) {
            $ids = BugReport::query()
                ->where('status', Status::Fixed->value)
                ->lockForUpdate()
                ->pluck('id')
                ->all();
            if (empty($ids)) {
                return;
            }
            BugReport::query()->whereIn('id', $ids)->update(['status' => Status::Closed->value]);
            $rows = array_map(fn ($id) => [
                'bug_report_id'   => $id,
                'action'          => 'status_changed',
                'by'              => $by,
                'at'              => $now,
                'fix_commit'      => '',
                'fix_description' => null,
                'metadata_json'   => json_encode([
                    'status' => Status::Closed->value,
                    'via'    => 'bulk_close_fixed',
                ]),
            ], $ids);
            BugReportLifecycle::insert($rows);
            $count = count($ids);
        });

        return $count;
    }

    public function bulkArchiveClosed(): int
    {
        $now = now();
        $count = 0;
        DB::connection(config('bugfab.storages.eloquent.connection'))
          ->transaction(function () use ($now, &$count) {
            $ids = BugReport::query()
                ->where('status', Status::Closed->value)
                ->whereNull('archived_at')
                ->lockForUpdate()
                ->pluck('id')
                ->all();
            if (empty($ids)) {
                return;
            }
            BugReport::query()->whereIn('id', $ids)->update(['archived_at' => $now]);
            $rows = array_map(fn ($id) => [
                'bug_report_id'   => $id,
                'action'          => 'archived',
                'by'              => '',
                'at'              => $now,
                'fix_commit'      => '',
                'fix_description' => null,
            ], $ids);
            BugReportLifecycle::insert($rows);
            $count = count($ids);
        });

        return $count;
    }

    // --- projection helpers -------------------------------------------------

    private function printableReporter(array $reporter): string
    {
        foreach (['email', 'user_id', 'name'] as $k) {
            $v = $reporter[$k] ?? '';
            if ($v) {
                return (string) $v;
            }
        }

        return '';
    }

    private function toSummary(BugReport $row): BugReportSummary
    {
        return new BugReportSummary(
            id:               $row->id,
            title:            $row->title,
            report_type:      $row->report_type ?: 'bug',
            severity:         $row->severity ?: Severity::Medium->value,
            status:           $row->status ?: Status::Open->value,
            module:           $row->module ?: '',
            created_at:       $row->received_at?->toIso8601String() ?? '',
            has_screenshot:   (bool) $row->screenshot_path,
            github_issue_url: $row->github_issue_url ?: null,
        );
    }

    private function toDetail(BugReport $row): BugReportDetail
    {
        $metadata = $this->decodeMetadata($row->metadata_json);
        $context = (array) ($metadata['context'] ?? []);
        $reporter = (array) ($metadata['reporter'] ?? []);

        $lifecycle = $row->lifecycle->map(fn (BugReportLifecycle $e) => [
            'action'          => $e->action,
            'by'              => $e->by ?? '',
            'at'              => $e->at?->toIso8601String() ?? '',
            'fix_commit'      => $e->fix_commit ?? '',
            'fix_description' => $e->fix_description ?? '',
        ])->all();

        return new BugReportDetail(
            id:                          $row->id,
            title:                       $row->title,
            report_type:                 $row->report_type ?: 'bug',
            severity:                    $row->severity ?: Severity::Medium->value,
            status:                      $row->status ?: Status::Open->value,
            module:                      $row->module ?: '',
            created_at:                  $row->received_at?->toIso8601String() ?? '',
            has_screenshot:              (bool) $row->screenshot_path,
            github_issue_url:            $row->github_issue_url ?: null,
            description:                 $row->description ?: '',
            expected_behavior:           (string) ($metadata['expected_behavior'] ?? ''),
            tags:                        array_values((array) ($metadata['tags'] ?? [])),
            reporter:                    $reporter,
            context:                     $context,
            lifecycle:                   $lifecycle,
            server_user_agent:           $row->user_agent_server ?: '',
            client_reported_user_agent:  $row->user_agent_client ?: '',
            environment:                 $row->environment ?: '',
            client_ts:                   (string) ($metadata['client_ts'] ?? ''),
            protocol_version:            $row->protocol_version ?: '0.1',
            updated_at:                  $row->updated_at?->toIso8601String() ?? '',
            github_issue_number:         $row->github_issue_number,
        );
    }

    private function decodeMetadata(?string $raw): array
    {
        if (! $raw) {
            return [];
        }
        try {
            $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return [];
        }

        return is_array($decoded) ? $decoded : [];
    }
}
