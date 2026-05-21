<?php

declare(strict_types=1);

namespace BugFab\Laravel\Storage;

use BugFab\Laravel\Enums\Status;
use BugFab\Laravel\Schemas\BugReportDetail;
use BugFab\Laravel\Schemas\BugReportSummary;
use BugFab\Laravel\Support\IdGenerator;
use Illuminate\Contracts\Filesystem\Filesystem;
use Illuminate\Support\Facades\Storage as StorageFacade;

/**
 * On-disk JSON+PNG storage matching the Python FileStorage layout.
 *
 *     <root>/
 *     ├── index.json
 *     ├── bug-001.json
 *     ├── bug-001.png
 *     └── archive/
 *         ├── bug-002.json
 *         └── bug-002.png
 *
 * Concurrency caveat — atomic-ish via PHP's tmp+rename, but lacking a
 * cross-process lock; do NOT use FileStorage with multi-worker FPM /
 * horizontal scaling. Use EloquentStorage for those deployments.
 *
 * The "disk" is a Laravel filesystem disk — local by default — so consumers
 * can point Bug-Fab at S3, etc. by reconfiguring the disk in
 * config/filesystems.php and changing config('bugfab.storages.file.disk').
 */
final class FileStorage implements StorageContract
{
    private Filesystem $disk;
    private string $root;
    private string $idPrefix;

    public function __construct(string $disk, string $root, string $idPrefix = '')
    {
        $this->disk = StorageFacade::disk($disk);
        $this->root = trim($root, '/');
        $this->idPrefix = $idPrefix;
        $this->ensureDirs();
    }

    private function ensureDirs(): void
    {
        if (! $this->disk->exists($this->root)) {
            $this->disk->makeDirectory($this->root);
        }
        $archive = $this->root . '/archive';
        if (! $this->disk->exists($archive)) {
            $this->disk->makeDirectory($archive);
        }
    }

    public function saveReport(array $metadata, string $screenshotBytes): string
    {
        $index = $this->readIndex();
        $reportId = IdGenerator::format($index['next_number'] ?? 1, $this->idPrefix);
        $now = $this->isoNow();
        $report = $this->buildReport($reportId, $metadata, $now);

        $this->disk->put($this->root . "/{$reportId}.png", $screenshotBytes);
        $this->writeJson($this->root . "/{$reportId}.json", $report);

        $index['reports'][] = $this->buildIndexEntry($report);
        $index['next_number'] = ($index['next_number'] ?? 1) + 1;
        $this->writeJson($this->root . '/index.json', $index);

        return $reportId;
    }

    public function getReport(string $reportId): ?BugReportDetail
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $data = $this->readReport($reportId);
        if ($data === null) {
            return null;
        }

        return $this->coerceDetail($data);
    }

    public function listReports(array $filters, int $page, int $pageSize): array
    {
        $index = $this->readIndex();
        $entries = $index['reports'] ?? [];

        $matched = array_values(array_filter($entries, fn ($e) => $this->matches($e, $filters)));
        usort($matched, fn ($a, $b) => strcmp($b['created_at'] ?? '', $a['created_at'] ?? ''));
        $total = count($matched);
        $start = max(0, ($page - 1) * $pageSize);
        $slice = array_slice($matched, $start, $pageSize);

        $items = array_map(fn ($e) => BugReportSummary::fromArray($e), $slice);

        return [$items, $total];
    }

    public function getScreenshot(string $reportId): ?array
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $primary = $this->root . "/{$reportId}.png";
        $archived = $this->root . "/archive/{$reportId}.png";
        foreach ([$primary, $archived] as $path) {
            if ($this->disk->exists($path)) {
                return [
                    'bytes' => (string) $this->disk->get($path),
                    'path'  => method_exists($this->disk, 'path') ? $this->disk->path($path) : null,
                ];
            }
        }

        return null;
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
        $data = $this->readReport($reportId);
        if ($data === null) {
            return null;
        }
        $data['status'] = $status;
        $data['updated_at'] = $this->isoNow();
        $data['lifecycle'][] = [
            'action'          => 'status_changed',
            'by'              => $by,
            'at'              => $data['updated_at'],
            'fix_commit'      => $fixCommit,
            'fix_description' => $fixDescription,
        ];
        $this->writeReport($reportId, $data);
        $this->updateIndexEntry($reportId, ['status' => $status]);

        return $this->coerceDetail($data);
    }

    public function setGitHubLink(string $reportId, int $issueNumber, string $issueUrl): ?BugReportDetail
    {
        if (! IdGenerator::isValid($reportId)) {
            return null;
        }
        $data = $this->readReport($reportId);
        if ($data === null) {
            return null;
        }
        $data['github_issue_number'] = $issueNumber;
        $data['github_issue_url'] = $issueUrl;
        $this->writeReport($reportId, $data);
        $this->updateIndexEntry($reportId, ['github_issue_url' => $issueUrl]);

        return $this->coerceDetail($data);
    }

    public function deleteReport(string $reportId): bool
    {
        if (! IdGenerator::isValid($reportId)) {
            return false;
        }
        $removed = false;
        foreach ($this->candidatePaths($reportId) as $path) {
            if ($this->disk->exists($path)) {
                $this->disk->delete($path);
                $removed = true;
            }
        }
        if ($removed) {
            $index = $this->readIndex();
            $index['reports'] = array_values(array_filter(
                $index['reports'] ?? [],
                fn ($e) => ($e['id'] ?? '') !== $reportId
            ));
            $this->writeJson($this->root . '/index.json', $index);
        }

        return $removed;
    }

    public function archiveReport(string $reportId): bool
    {
        if (! IdGenerator::isValid($reportId)) {
            return false;
        }

        return $this->archiveOne($reportId);
    }

    public function bulkCloseFixed(string $by = ''): int
    {
        $index = $this->readIndex();
        $ids = array_map(
            fn ($e) => $e['id'],
            array_filter($index['reports'] ?? [], fn ($e) => ($e['status'] ?? '') === 'fixed')
        );
        $closed = 0;
        foreach ($ids as $id) {
            if ($this->updateStatus($id, Status::Closed->value, '', '', $by) !== null) {
                $closed++;
            }
        }

        return $closed;
    }

    public function bulkArchiveClosed(): int
    {
        $index = $this->readIndex();
        $ids = array_map(
            fn ($e) => $e['id'],
            array_filter($index['reports'] ?? [], fn ($e) => ($e['status'] ?? '') === 'closed')
        );
        $archived = 0;
        foreach ($ids as $id) {
            if ($this->archiveOne($id)) {
                $archived++;
            }
        }

        return $archived;
    }

    // --- internals ----------------------------------------------------------

    private function readIndex(): array
    {
        $path = $this->root . '/index.json';
        if (! $this->disk->exists($path)) {
            return ['reports' => [], 'next_number' => 1];
        }
        $raw = (string) $this->disk->get($path);
        try {
            $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return ['reports' => [], 'next_number' => 1];
        }
        if (! is_array($decoded)) {
            return ['reports' => [], 'next_number' => 1];
        }
        $decoded['reports'] = $decoded['reports'] ?? [];
        $decoded['next_number'] = $decoded['next_number'] ?? (count($decoded['reports']) + 1);

        return $decoded;
    }

    private function readReport(string $reportId): ?array
    {
        $primary = $this->root . "/{$reportId}.json";
        $archived = $this->root . "/archive/{$reportId}.json";
        foreach ([$primary, $archived] as $path) {
            if ($this->disk->exists($path)) {
                try {
                    return json_decode((string) $this->disk->get($path), true, 512, JSON_THROW_ON_ERROR);
                } catch (\JsonException) {
                    return null;
                }
            }
        }

        return null;
    }

    private function writeReport(string $reportId, array $data): void
    {
        $primary = $this->root . "/{$reportId}.json";
        $archived = $this->root . "/archive/{$reportId}.json";
        $target = $this->disk->exists($archived) && ! $this->disk->exists($primary)
            ? $archived
            : $primary;
        $this->writeJson($target, $data);
    }

    private function writeJson(string $path, array $payload): void
    {
        $this->disk->put($path, json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
    }

    private function updateIndexEntry(string $reportId, array $fields): void
    {
        $index = $this->readIndex();
        foreach ($index['reports'] as &$entry) {
            if (($entry['id'] ?? '') === $reportId) {
                foreach ($fields as $k => $v) {
                    $entry[$k] = $v;
                }
                break;
            }
        }
        unset($entry);
        $this->writeJson($this->root . '/index.json', $index);
    }

    private function archiveOne(string $reportId): bool
    {
        $jsonSrc = $this->root . "/{$reportId}.json";
        $pngSrc  = $this->root . "/{$reportId}.png";
        $jsonDst = $this->root . "/archive/{$reportId}.json";
        $pngDst  = $this->root . "/archive/{$reportId}.png";
        $moved = false;
        if ($this->disk->exists($jsonSrc)) {
            $this->disk->move($jsonSrc, $jsonDst);
            $moved = true;
        }
        if ($this->disk->exists($pngSrc)) {
            $this->disk->move($pngSrc, $pngDst);
            $moved = true;
        }
        if ($moved) {
            $index = $this->readIndex();
            $index['reports'] = array_values(array_filter(
                $index['reports'] ?? [],
                fn ($e) => ($e['id'] ?? '') !== $reportId
            ));
            $this->writeJson($this->root . '/index.json', $index);
        }

        return $moved;
    }

    private function candidatePaths(string $reportId): array
    {
        return [
            $this->root . "/{$reportId}.json",
            $this->root . "/{$reportId}.png",
            $this->root . "/archive/{$reportId}.json",
            $this->root . "/archive/{$reportId}.png",
        ];
    }

    private function buildReport(string $reportId, array $metadata, string $now): array
    {
        $context = $metadata['context'] ?? [];
        $reporter = $metadata['reporter'] ?? [];

        return [
            'id'                          => $reportId,
            'protocol_version'            => $metadata['protocol_version'] ?? '0.1',
            'title'                       => $metadata['title'] ?? '',
            'client_ts'                   => $metadata['client_ts'] ?? '',
            'report_type'                 => $metadata['report_type'] ?? 'bug',
            'description'                 => $metadata['description'] ?? '',
            'expected_behavior'           => $metadata['expected_behavior'] ?? '',
            'severity'                    => $metadata['severity'] ?? 'medium',
            'status'                      => Status::Open->value,
            'tags'                        => array_values((array) ($metadata['tags'] ?? [])),
            'reporter'                    => [
                'name'    => (string) ($reporter['name'] ?? ''),
                'email'   => (string) ($reporter['email'] ?? ''),
                'user_id' => (string) ($reporter['user_id'] ?? ''),
            ],
            'context'                     => is_array($context) ? $context : [],
            'module'                      => $metadata['module']
                                              ?? ($context['module'] ?? ''),
            'created_at'                  => $now,
            'updated_at'                  => $now,
            'has_screenshot'              => true,
            'server_user_agent'           => $metadata['server_user_agent'] ?? '',
            'client_reported_user_agent'  => $context['user_agent'] ?? '',
            'environment'                 => $metadata['environment']
                                              ?? ($context['environment'] ?? ''),
            'github_issue_url'            => null,
            'github_issue_number'         => null,
            'lifecycle'                   => [
                [
                    'action'          => 'created',
                    'by'              => $metadata['submitted_by'] ?? 'anonymous',
                    'at'              => $now,
                    'fix_commit'      => '',
                    'fix_description' => '',
                ],
            ],
        ];
    }

    private function buildIndexEntry(array $report): array
    {
        return [
            'id'               => $report['id'],
            'title'            => $report['title'] ?? '',
            'report_type'      => $report['report_type'] ?? 'bug',
            'severity'         => $report['severity'] ?? 'medium',
            'status'           => $report['status'] ?? 'open',
            'module'           => $report['module'] ?? '',
            'created_at'       => $report['created_at'] ?? '',
            'has_screenshot'   => $report['has_screenshot'] ?? true,
            'github_issue_url' => $report['github_issue_url'] ?? null,
        ];
    }

    private function matches(array $entry, array $filters): bool
    {
        foreach (['status', 'severity', 'module', 'report_type'] as $key) {
            $wanted = $filters[$key] ?? null;
            if ($wanted !== null && $wanted !== '' && ($entry[$key] ?? null) !== $wanted) {
                return false;
            }
        }
        $search = $filters['search'] ?? null;
        if ($search) {
            $needle = mb_strtolower((string) $search);
            $hay = mb_strtolower(
                implode(' ', [
                    (string) ($entry['title'] ?? ''),
                    (string) ($entry['module'] ?? ''),
                    (string) ($entry['id'] ?? ''),
                ])
            );
            if (mb_strpos($hay, $needle) === false) {
                return false;
            }
        }

        return true;
    }

    private function coerceDetail(array $data): BugReportDetail
    {
        return new BugReportDetail(
            id:                          (string) ($data['id'] ?? ''),
            title:                       (string) ($data['title'] ?? ''),
            report_type:                 (string) ($data['report_type'] ?? 'bug'),
            severity:                    (string) ($data['severity'] ?? 'medium'),
            status:                      (string) ($data['status'] ?? 'open'),
            module:                      (string) ($data['module'] ?? ''),
            created_at:                  (string) ($data['created_at'] ?? ''),
            has_screenshot:              (bool) ($data['has_screenshot'] ?? true),
            github_issue_url:            $data['github_issue_url'] ?? null,
            description:                 (string) ($data['description'] ?? ''),
            expected_behavior:           (string) ($data['expected_behavior'] ?? ''),
            tags:                        array_values((array) ($data['tags'] ?? [])),
            reporter:                    (array) ($data['reporter'] ?? []),
            context:                     (array) ($data['context'] ?? []),
            lifecycle:                   array_values((array) ($data['lifecycle'] ?? [])),
            server_user_agent:           (string) ($data['server_user_agent'] ?? ''),
            client_reported_user_agent:  (string) ($data['client_reported_user_agent'] ?? ''),
            environment:                 (string) ($data['environment'] ?? ''),
            client_ts:                   (string) ($data['client_ts'] ?? ''),
            protocol_version:            (string) ($data['protocol_version'] ?? '0.1'),
            updated_at:                  (string) ($data['updated_at'] ?? ''),
            github_issue_number:         isset($data['github_issue_number']) ? (int) $data['github_issue_number'] : null,
        );
    }

    private function isoNow(): string
    {
        return (new \DateTimeImmutable('now', new \DateTimeZone('UTC')))
            ->format('Y-m-d\TH:i:s.uP');
    }
}
