using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace BugFab.AspNetCore.Data.Entities;

/// <summary>
/// One entry in a bug report's append-only lifecycle audit log.
/// </summary>
/// <remarks>
/// Field names lock to <c>action / by / at</c> per audit IF16 — the prior-art
/// template/service drift (<c>status / changed_by / timestamp</c>) is the
/// cautionary tale that motivated the lock.
/// </remarks>
[Table("bug_fab_bug_report_lifecycle")]
public sealed class BugReportLifecycle
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("bug_report_id")]
    [MaxLength(64)]
    public string BugReportId { get; set; } = default!;

    /// <summary>One of <c>created / status_changed / deleted / archived</c>.</summary>
    [Column("action")]
    [MaxLength(32)]
    public string Action { get; set; } = default!;

    /// <summary>
    /// Consumer-supplied user identifier (opaque, capped at 256 chars in v0.2
    /// when the AuthAdapter ABC arrives). MAY be null when the adapter has no
    /// auth context.
    /// </summary>
    [Column("by")]
    [MaxLength(256)]
    public string? By { get; set; }

    [Column("at")]
    public DateTimeOffset At { get; set; }

    /// <summary>
    /// New status value when <see cref="Action"/> is <c>status_changed</c>.
    /// Stored as a typed column so the wire-shape projection can emit it
    /// directly without round-tripping through <see cref="MetadataJson"/> —
    /// see PROTOCOL.md § Lifecycle audit log.
    /// </summary>
    [Column("status")]
    [MaxLength(32)]
    public string? Status { get; set; }

    [Column("fix_commit")]
    [MaxLength(256)]
    public string? FixCommit { get; set; }

    [Column("fix_description")]
    public string? FixDescription { get; set; }

    /// <summary>Free-form per-action metadata (e.g. the new status value).</summary>
    [Column("metadata_json")]
    public string? MetadataJson { get; set; }

    public BugReport BugReport { get; set; } = default!;
}
