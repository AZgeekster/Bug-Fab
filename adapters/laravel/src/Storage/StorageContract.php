<?php

declare(strict_types=1);

namespace BugFab\Laravel\Storage;

use BugFab\Laravel\Schemas\BugReportDetail;
use BugFab\Laravel\Schemas\BugReportSummary;

/**
 * Storage interface for the Bug-Fab Laravel adapter.
 *
 * Two implementations ship: FileStorage (on-disk JSON+PNG, single-node) and
 * EloquentStorage (DB-backed, multi-worker/Octane-safe). Custom storage
 * backends can implement this contract and be wired via the container.
 *
 * All methods are synchronous — Laravel is sync-by-default and the Python
 * async ABC would only add complexity here.
 */
interface StorageContract
{
    /**
     * Persist a new report. Returns the assigned ID (bug-NNN format).
     *
     * @param array<string,mixed> $metadata Validated wire-protocol payload
     *                                       plus server_user_agent + environment.
     * @param string $screenshotBytes Raw PNG bytes (already magic-byte verified).
     */
    public function saveReport(array $metadata, string $screenshotBytes): string;

    /**
     * Fetch a single report's full detail. Returns null when not found.
     */
    public function getReport(string $reportId): ?BugReportDetail;

    /**
     * Return paginated summaries plus the total count matching filters.
     *
     * @param array<string,mixed> $filters
     * @return array{0: array<int,BugReportSummary>, 1: int}
     */
    public function listReports(array $filters, int $page, int $pageSize): array;

    /**
     * Return raw screenshot bytes plus the on-disk path (when applicable).
     * Returns null when no screenshot exists.
     *
     * @return array{bytes: string, path: ?string}|null
     */
    public function getScreenshot(string $reportId): ?array;

    /**
     * Mutate status + append lifecycle entry. Returns the updated detail or
     * null when the report is missing.
     */
    public function updateStatus(
        string $reportId,
        string $status,
        string $fixCommit = '',
        string $fixDescription = '',
        string $by = ''
    ): ?BugReportDetail;

    /**
     * Stamp a GitHub issue link onto a report. Returns the updated detail.
     */
    public function setGitHubLink(string $reportId, int $issueNumber, string $issueUrl): ?BugReportDetail;

    /**
     * Hard-delete. Returns true if a row was removed.
     */
    public function deleteReport(string $reportId): bool;

    /**
     * Soft-archive (file storage moves to archive/, eloquent stamps archived_at).
     */
    public function archiveReport(string $reportId): bool;

    /**
     * Transition every "fixed" report to "closed". Returns count transitioned.
     */
    public function bulkCloseFixed(string $by = ''): int;

    /**
     * Archive every non-archived "closed" report. Returns count archived.
     */
    public function bulkArchiveClosed(): int;
}
