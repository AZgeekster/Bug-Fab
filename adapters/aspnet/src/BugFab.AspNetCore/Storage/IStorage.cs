using System.Text.Json.Nodes;

namespace BugFab.AspNetCore.Storage;

/// <summary>
/// Storage abstraction for Bug-Fab reports. Async by design — both shipped
/// implementations may issue I/O (database round-trips for EF Core, disk
/// writes for the file backend).
/// </summary>
/// <remarks>
/// Implementations MUST preserve the full submitted metadata JSON verbatim
/// for round-trip fidelity. Future protocol-version fields the entity model
/// doesn't recognize must round-trip unmodified.
/// </remarks>
public interface IStorage
{
    /// <summary>
    /// Persist a new report. The <paramref name="metadata"/> object includes
    /// every wire-protocol field plus the server-derived
    /// <c>server_user_agent</c>, <c>client_reported_user_agent</c>, and
    /// <c>environment</c> fields.
    /// </summary>
    /// <returns>The server-assigned <c>bug-NNN</c> id.</returns>
    Task<string> SaveReportAsync(
        JsonObject metadata,
        ReadOnlyMemory<byte> screenshot,
        CancellationToken ct = default);

    /// <summary>Return one report's full detail, or null if missing.</summary>
    Task<StoredReport?> GetReportAsync(string reportId, CancellationToken ct = default);

    /// <summary>List reports matching <paramref name="filters"/>.</summary>
    Task<(IReadOnlyList<StoredReportSummary> Items, int Total)> ListReportsAsync(
        ReportFilters filters,
        int page,
        int pageSize,
        CancellationToken ct = default);

    /// <summary>Return the on-disk path of the screenshot file, or null.</summary>
    Task<string?> GetScreenshotPathAsync(string reportId, CancellationToken ct = default);

    /// <summary>
    /// Apply a status change and append a <c>status_changed</c> lifecycle
    /// entry. Returns the updated detail or null when the report is missing.
    /// </summary>
    Task<StoredReport?> UpdateStatusAsync(
        string reportId,
        string status,
        string fixCommit,
        string fixDescription,
        string by,
        CancellationToken ct = default);

    /// <summary>
    /// Hard-delete the report and its screenshot. Returns true if a report
    /// was deleted, false if it didn't exist.
    /// </summary>
    Task<bool> DeleteReportAsync(string reportId, CancellationToken ct = default);

    /// <summary>
    /// Transition every <c>fixed</c> report to <c>closed</c>. Returns the
    /// number of rows affected. Idempotent at the per-row level.
    /// </summary>
    Task<int> BulkCloseFixedAsync(string by, CancellationToken ct = default);

    /// <summary>
    /// Move every <c>closed</c> report into the archive area. Returns the
    /// number of rows affected.
    /// </summary>
    Task<int> BulkArchiveClosedAsync(CancellationToken ct = default);

    /// <summary>Persist a GitHub Issues link for a previously stored report.</summary>
    Task SetGitHubLinkAsync(
        string reportId,
        int issueNumber,
        string issueUrl,
        CancellationToken ct = default);

    /// <summary>Return aggregate status counts for stat-card rendering.</summary>
    Task<IReadOnlyDictionary<string, int>> ComputeStatsAsync(CancellationToken ct = default);
}

/// <summary>Filter set for <see cref="IStorage.ListReportsAsync"/>.</summary>
public sealed record ReportFilters(
    string? Status = null,
    string? Severity = null,
    string? Module = null,
    string? Environment = null,
    bool IncludeArchived = false);

/// <summary>Compact representation used by list views.</summary>
public sealed record StoredReportSummary(
    string Id,
    string Title,
    string ReportType,
    string Severity,
    string Status,
    string Module,
    string CreatedAt,
    bool HasScreenshot,
    string? GitHubIssueUrl);

/// <summary>
/// The full stored report. Mirrors <c>BugReportDetail</c> on the wire — the
/// raw <see cref="MetadataJson"/> blob is preserved verbatim so unknown
/// fields round-trip correctly.
/// </summary>
public sealed record StoredReport(
    string Id,
    string Title,
    string ReportType,
    string Severity,
    string Status,
    string Module,
    string CreatedAt,
    string UpdatedAt,
    bool HasScreenshot,
    string? GitHubIssueUrl,
    int? GitHubIssueNumber,
    string Description,
    string ExpectedBehavior,
    IReadOnlyList<string> Tags,
    JsonObject Reporter,
    JsonObject Context,
    IReadOnlyList<JsonObject> Lifecycle,
    string ServerUserAgent,
    string ClientReportedUserAgent,
    string Environment,
    string ClientTs,
    string ProtocolVersion,
    string MetadataJson);
