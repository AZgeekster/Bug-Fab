using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace BugFab.AspNetCore.Data.Entities;

/// <summary>
/// One stored bug report. Mirrors the SQL schema used by the upstream Python
/// <c>bug_fab/storage/_models.py</c> reference exactly so a single conformance
/// suite can probe both implementations.
/// </summary>
/// <remarks>
/// <para>
/// Screenshots are NEVER stored in the database — the
/// <see cref="ScreenshotPath"/> column points to a file on disk written by
/// the storage backend.
/// </para>
/// <para>
/// The <see cref="MetadataJson"/> column holds the full original wire-protocol
/// payload for fidelity; the typed columns above it are denormalized for
/// efficient indexed queries (status, severity, environment, received_at,
/// archived_at).
/// </para>
/// </remarks>
[Table("bug_fab_bug_reports")]
public sealed class BugReport
{
    /// <summary>Server-assigned id, e.g. <c>bug-001</c>, <c>bug-P038</c>.</summary>
    [Key]
    [Column("id")]
    [MaxLength(64)]
    public string Id { get; set; } = default!;

    /// <summary>
    /// Underlying monotonic integer pulled from the <c>bug_report_id_seq</c>
    /// HiLo-managed sequence. The wire <see cref="Id"/> is derived as
    /// <c>bug-{prefix}{IdSequence:D3}</c>. Stored separately so concurrent
    /// intakes can't collide on a <c>COUNT(*) + 1</c> race; see
    /// <see cref="BugFabDbContext.OnModelCreating"/> for the
    /// <c>UseHiLo("bug_report_id_seq")</c> registration.
    /// </summary>
    [Column("id_sequence")]
    public long IdSequence { get; set; }

    [Column("received_at")]
    public DateTimeOffset ReceivedAt { get; set; }

    [Column("protocol_version")]
    [MaxLength(16)]
    public string ProtocolVersion { get; set; } = "0.1";

    [Column("title")]
    [MaxLength(200)]
    public string Title { get; set; } = default!;

    [Column("description")]
    public string Description { get; set; } = string.Empty;

    /// <summary>One of <c>low / medium / high / critical</c> on write paths.
    /// Read paths accept any string value (deprecated-values rule).</summary>
    [Column("severity")]
    [MaxLength(32)]
    public string? Severity { get; set; }

    /// <summary>One of <c>open / investigating / fixed / closed</c> on write
    /// paths.</summary>
    [Column("status")]
    [MaxLength(32)]
    public string Status { get; set; } = "open";

    [Column("environment")]
    [MaxLength(64)]
    public string? Environment { get; set; }

    [Column("app_name")]
    [MaxLength(128)]
    public string? AppName { get; set; }

    [Column("app_version")]
    [MaxLength(64)]
    public string? AppVersion { get; set; }

    [Column("reporter")]
    [MaxLength(512)]
    public string? Reporter { get; set; }

    [Column("page_url")]
    [MaxLength(2048)]
    public string? PageUrl { get; set; }

    [Column("module")]
    [MaxLength(128)]
    public string? Module { get; set; }

    /// <summary>Server-captured User-Agent. Source of truth.</summary>
    [Column("user_agent_server")]
    [MaxLength(512)]
    public string? UserAgentServer { get; set; }

    /// <summary>Client-reported User-Agent. Diagnostic only.</summary>
    [Column("user_agent_client")]
    [MaxLength(512)]
    public string? UserAgentClient { get; set; }

    /// <summary>Full wire-protocol payload preserved verbatim for round-trip fidelity.</summary>
    [Column("metadata_json")]
    public string MetadataJson { get; set; } = "{}";

    [Column("screenshot_path")]
    [MaxLength(1024)]
    public string ScreenshotPath { get; set; } = string.Empty;

    [Column("github_issue_url")]
    [MaxLength(512)]
    public string? GitHubIssueUrl { get; set; }

    [Column("github_issue_number")]
    public int? GitHubIssueNumber { get; set; }

    [Column("archived_at")]
    public DateTimeOffset? ArchivedAt { get; set; }

    public List<BugReportLifecycle> Lifecycle { get; set; } = new();
}
