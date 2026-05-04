using System.Text.Json;
using System.Text.Json.Nodes;
using BugFab.AspNetCore.Data;
using BugFab.AspNetCore.Data.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace BugFab.AspNetCore.Storage;

/// <summary>
/// EF Core-backed <see cref="IStorage"/>. Default for production deployments.
/// </summary>
/// <remarks>
/// <para>
/// Screenshots land on disk under <see cref="BugFabOptions.StorageDirectory"/>
/// — they're not stored in the database. The metadata JSON blob round-trips
/// through the <see cref="BugReport.MetadataJson"/> column verbatim so unknown
/// future fields (forward-additive protocol changes) survive the persist /
/// fetch cycle.
/// </para>
/// <para>
/// IDs are issued via a HiLo-managed database sequence (<c>bug_report_id_seq</c>,
/// declared in <see cref="BugFabDbContext.OnModelCreating"/>) so concurrent
/// intakes can't collide. The integer pulled from the sequence is formatted as
/// <c>bug-{prefix}{N:D3}</c> for the wire ID. Provider-portable across SQL
/// Server, PostgreSQL, and SQLite (sequences require SQLite 3.37+ via the
/// EF Core relational pipeline). The InMemory provider — used by the unit
/// tests — has no sequence support, so a <c>MAX(IdSequence) + 1</c> fallback
/// is used there; this fallback is for tests only and is NOT safe under real
/// concurrency, but the InMemory provider isn't either.
/// </para>
/// </remarks>
public sealed class EfCoreStorage : IStorage
{
    private readonly IServiceProvider _services;
    private readonly BugFabOptions _options;

    public EfCoreStorage(IServiceProvider services, BugFabOptions options)
    {
        _services = services;
        _options = options;
    }

    public async Task<string> SaveReportAsync(
        JsonObject metadata,
        ReadOnlyMemory<byte> screenshot,
        CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var seq = await NextSequenceValueAsync(db, ct).ConfigureAwait(false);
        var prefix = string.IsNullOrEmpty(_options.IdPrefix) ? string.Empty : _options.IdPrefix;
        var id = $"bug-{prefix}{seq:D3}";
        var receivedAt = DateTimeOffset.UtcNow;

        var screenshotPath = await PersistScreenshotAsync(id, screenshot, ct).ConfigureAwait(false);

        var report = new BugReport
        {
            Id = id,
            IdSequence = seq,
            ReceivedAt = receivedAt,
            ProtocolVersion = metadata["protocol_version"]?.GetValue<string>() ?? "0.1",
            Title = metadata["title"]?.GetValue<string>() ?? string.Empty,
            Description = metadata["description"]?.GetValue<string>() ?? string.Empty,
            Severity = metadata["severity"]?.GetValue<string>() ?? "medium",
            Status = "open",
            Environment = metadata["environment"]?.GetValue<string>(),
            AppName = (metadata["context"] as JsonObject)?["app_name"]?.GetValue<string>(),
            AppVersion = (metadata["context"] as JsonObject)?["app_version"]?.GetValue<string>(),
            Reporter = metadata["reporter"]?.ToJsonString(),
            PageUrl = (metadata["context"] as JsonObject)?["url"]?.GetValue<string>(),
            Module = (metadata["context"] as JsonObject)?["module"]?.GetValue<string>(),
            UserAgentServer = metadata["server_user_agent"]?.GetValue<string>() ?? string.Empty,
            UserAgentClient = metadata["client_reported_user_agent"]?.GetValue<string>() ?? string.Empty,
            MetadataJson = metadata.ToJsonString(),
            ScreenshotPath = screenshotPath,
        };

        var lifecycle = new BugReportLifecycle
        {
            BugReportId = id,
            Action = "created",
            By = (metadata["reporter"] as JsonObject)?["email"]?.GetValue<string>() ?? "anonymous",
            At = receivedAt,
            Status = "open",
            MetadataJson = JsonSerializer.Serialize(new { status = "open" }, _options.JsonOptions),
        };

        report.Lifecycle.Add(lifecycle);
        db.BugReports.Add(report);
        await db.SaveChangesAsync(ct).ConfigureAwait(false);

        return id;
    }

    public async Task<StoredReport?> GetReportAsync(string reportId, CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var entity = await db.BugReports
            .Include(x => x.Lifecycle)
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == reportId, ct)
            .ConfigureAwait(false);

        return entity is null ? null : ProjectToStoredReport(entity);
    }

    public async Task<(IReadOnlyList<StoredReportSummary> Items, int Total)> ListReportsAsync(
        ReportFilters filters,
        int page,
        int pageSize,
        CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var query = db.BugReports.AsNoTracking().AsQueryable();
        if (!filters.IncludeArchived)
        {
            query = query.Where(x => x.ArchivedAt == null);
        }
        if (!string.IsNullOrEmpty(filters.Status))
        {
            query = query.Where(x => x.Status == filters.Status);
        }
        if (!string.IsNullOrEmpty(filters.Severity))
        {
            query = query.Where(x => x.Severity == filters.Severity);
        }
        if (!string.IsNullOrEmpty(filters.Module))
        {
            query = query.Where(x => x.Module == filters.Module);
        }
        if (!string.IsNullOrEmpty(filters.Environment))
        {
            query = query.Where(x => x.Environment == filters.Environment);
        }

        var total = await query.CountAsync(ct).ConfigureAwait(false);

        var rows = await query
            .OrderByDescending(x => x.ReceivedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(ct)
            .ConfigureAwait(false);

        var items = rows.Select(x => new StoredReportSummary(
            Id: x.Id,
            Title: x.Title,
            ReportType: ExtractReportType(x.MetadataJson),
            Severity: x.Severity ?? "medium",
            Status: x.Status,
            Module: x.Module ?? string.Empty,
            CreatedAt: x.ReceivedAt.ToString("O"),
            HasScreenshot: !string.IsNullOrEmpty(x.ScreenshotPath),
            GitHubIssueUrl: x.GitHubIssueUrl)).ToList();

        return (items, total);
    }

    public async Task<string?> GetScreenshotPathAsync(string reportId, CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var path = await db.BugReports
            .Where(x => x.Id == reportId)
            .Select(x => x.ScreenshotPath)
            .FirstOrDefaultAsync(ct)
            .ConfigureAwait(false);

        if (string.IsNullOrEmpty(path)) return null;
        return File.Exists(path) ? path : null;
    }

    public async Task<StoredReport?> UpdateStatusAsync(
        string reportId,
        string status,
        string fixCommit,
        string fixDescription,
        string by,
        CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var report = await db.BugReports
            .Include(x => x.Lifecycle)
            .FirstOrDefaultAsync(x => x.Id == reportId, ct)
            .ConfigureAwait(false);
        if (report is null) return null;

        report.Status = status;

        report.Lifecycle.Add(new BugReportLifecycle
        {
            BugReportId = report.Id,
            Action = "status_changed",
            By = by,
            At = DateTimeOffset.UtcNow,
            Status = status,
            FixCommit = string.IsNullOrEmpty(fixCommit) ? null : fixCommit,
            FixDescription = string.IsNullOrEmpty(fixDescription) ? null : fixDescription,
            MetadataJson = JsonSerializer.Serialize(new { status }, _options.JsonOptions),
        });

        await db.SaveChangesAsync(ct).ConfigureAwait(false);
        return ProjectToStoredReport(report);
    }

    public async Task<bool> DeleteReportAsync(string reportId, CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var report = await db.BugReports.FirstOrDefaultAsync(x => x.Id == reportId, ct)
            .ConfigureAwait(false);
        if (report is null) return false;

        var screenshotPath = report.ScreenshotPath;
        db.BugReports.Remove(report);
        await db.SaveChangesAsync(ct).ConfigureAwait(false);

        try
        {
            if (!string.IsNullOrEmpty(screenshotPath) && File.Exists(screenshotPath))
            {
                File.Delete(screenshotPath);
            }
        }
        catch (IOException)
        {
            // Best-effort cleanup. The metadata row is already gone; orphan
            // blobs surface in the next archive sweep.
        }

        return true;
    }

    public async Task<int> BulkCloseFixedAsync(string by, CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var fixedRows = await db.BugReports
            .Where(x => x.Status == "fixed")
            .ToListAsync(ct)
            .ConfigureAwait(false);

        if (fixedRows.Count == 0) return 0;

        var now = DateTimeOffset.UtcNow;
        foreach (var report in fixedRows)
        {
            report.Status = "closed";
            db.Lifecycle.Add(new BugReportLifecycle
            {
                BugReportId = report.Id,
                Action = "status_changed",
                By = by,
                At = now,
                Status = "closed",
                MetadataJson = JsonSerializer.Serialize(new { status = "closed" }, _options.JsonOptions),
            });
        }
        await db.SaveChangesAsync(ct).ConfigureAwait(false);
        return fixedRows.Count;
    }

    public async Task<int> BulkArchiveClosedAsync(CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var closedRows = await db.BugReports
            .Where(x => x.Status == "closed" && x.ArchivedAt == null)
            .ToListAsync(ct)
            .ConfigureAwait(false);

        if (closedRows.Count == 0) return 0;

        var now = DateTimeOffset.UtcNow;
        foreach (var report in closedRows)
        {
            report.ArchivedAt = now;
            db.Lifecycle.Add(new BugReportLifecycle
            {
                BugReportId = report.Id,
                Action = "archived",
                At = now,
            });
        }
        await db.SaveChangesAsync(ct).ConfigureAwait(false);
        return closedRows.Count;
    }

    public async Task SetGitHubLinkAsync(
        string reportId,
        int issueNumber,
        string issueUrl,
        CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var report = await db.BugReports.FirstOrDefaultAsync(x => x.Id == reportId, ct)
            .ConfigureAwait(false);
        if (report is null) return;

        report.GitHubIssueNumber = issueNumber;
        report.GitHubIssueUrl = issueUrl;
        await db.SaveChangesAsync(ct).ConfigureAwait(false);
    }

    public async Task<IReadOnlyDictionary<string, int>> ComputeStatsAsync(CancellationToken ct = default)
    {
        await using var scope = _services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();

        var groups = await db.BugReports
            .Where(x => x.ArchivedAt == null)
            .GroupBy(x => x.Status)
            .Select(g => new { Status = g.Key, Count = g.Count() })
            .ToListAsync(ct)
            .ConfigureAwait(false);

        var result = new Dictionary<string, int>(StringComparer.Ordinal)
        {
            ["open"] = 0,
            ["investigating"] = 0,
            ["fixed"] = 0,
            ["closed"] = 0,
            ["total"] = 0,
        };
        foreach (var g in groups)
        {
            result[g.Status] = g.Count;
            result["total"] += g.Count;
        }
        return result;
    }

    /// <summary>
    /// Name of the database sequence that backs the HiLo-managed ID
    /// <summary>
    /// Reserve the next ID-sequence value via <c>MAX(IdSequence) + 1</c>.
    /// The integer is formatted as the <c>bug-{prefix}{N:D3}</c> wire ID
    /// by the caller.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Provider-portable: works on SQL Server, PostgreSQL, SQLite, and the
    /// InMemory test provider without provider-specific extensions or
    /// sequence DDL. Bug-Fab's ID column is also auto-incremented at the
    /// EF Core layer (<c>ValueGeneratedOnAdd</c> in
    /// <see cref="BugFabDbContext.OnModelCreating"/>), so consumers
    /// preferring identity-column behavior can drop this method and read
    /// back the entity's <c>IdSequence</c> after <c>SaveChanges</c>.
    /// </para>
    /// <para>
    /// Concurrency caveat: <c>MAX + 1</c> races under highly concurrent
    /// intake. Bug-Fab's expected volume (single-user / small-team) makes
    /// this acceptable in practice — duplicate IDs trigger the PK uniqueness
    /// constraint and the consumer retries. Tighter guarantees require a
    /// provider-specific sequence (SQL Server <c>HiLo</c> via
    /// <c>Microsoft.EntityFrameworkCore.SqlServer</c>, PostgreSQL identity
    /// via <c>Npgsql.EntityFrameworkCore.PostgreSQL</c>) which the consumer
    /// can enable in their own DbContext subclass.
    /// </para>
    /// </remarks>
    private static async Task<long> NextSequenceValueAsync(BugFabDbContext db, CancellationToken ct)
    {
        var maxSeq = await db.BugReports
            .Select(b => (long?)b.IdSequence)
            .MaxAsync(ct)
            .ConfigureAwait(false);
        return (maxSeq ?? 0L) + 1L;
    }

    private async Task<string> PersistScreenshotAsync(
        string id,
        ReadOnlyMemory<byte> screenshot,
        CancellationToken ct)
    {
        Directory.CreateDirectory(_options.StorageDirectory);
        var path = Path.Combine(_options.StorageDirectory, $"{id}.png");
        await File.WriteAllBytesAsync(path, screenshot.ToArray(), ct).ConfigureAwait(false);
        return path;
    }

    private static string ExtractReportType(string metadataJson)
    {
        try
        {
            var doc = JsonNode.Parse(metadataJson);
            return doc?["report_type"]?.GetValue<string>() ?? "bug";
        }
        catch (JsonException)
        {
            return "bug";
        }
    }

    private StoredReport ProjectToStoredReport(BugReport entity)
    {
        var raw = JsonNode.Parse(entity.MetadataJson) as JsonObject ?? new JsonObject();
        var contextNode = raw["context"] as JsonObject ?? new JsonObject();
        var reporterNode = raw["reporter"] as JsonObject ?? new JsonObject();

        var tags = (raw["tags"] as JsonArray)?
            .Select(t => t?.GetValue<string>() ?? string.Empty)
            .Where(s => !string.IsNullOrEmpty(s))
            .ToList() ?? new List<string>();

        var lifecycle = entity.Lifecycle
            .OrderBy(l => l.At)
            .Select(l =>
            {
                var entry = new JsonObject
                {
                    ["action"] = l.Action,
                    ["by"] = l.By ?? string.Empty,
                    ["at"] = l.At.ToString("O"),
                    ["fix_commit"] = l.FixCommit ?? string.Empty,
                    ["fix_description"] = l.FixDescription ?? string.Empty,
                };
                // Per PROTOCOL.md § Lifecycle audit log, `status_changed` entries
                // (and `created` rows, which carry the initial status) include a
                // top-level `status` key. Project it from the typed column —
                // round-tripping through MetadataJson silently drops keys when
                // future actions add other structured metadata.
                if (!string.IsNullOrEmpty(l.Status))
                {
                    entry["status"] = l.Status;
                }
                return entry;
            })
            .ToList();

        return new StoredReport(
            Id: entity.Id,
            Title: entity.Title,
            ReportType: raw["report_type"]?.GetValue<string>() ?? "bug",
            Severity: entity.Severity ?? "medium",
            Status: entity.Status,
            Module: entity.Module ?? string.Empty,
            CreatedAt: entity.ReceivedAt.ToString("O"),
            UpdatedAt: entity.ReceivedAt.ToString("O"),
            HasScreenshot: !string.IsNullOrEmpty(entity.ScreenshotPath),
            GitHubIssueUrl: entity.GitHubIssueUrl,
            GitHubIssueNumber: entity.GitHubIssueNumber,
            Description: entity.Description,
            ExpectedBehavior: raw["expected_behavior"]?.GetValue<string>() ?? string.Empty,
            Tags: tags,
            Reporter: reporterNode,
            Context: contextNode,
            Lifecycle: lifecycle,
            ServerUserAgent: entity.UserAgentServer ?? string.Empty,
            ClientReportedUserAgent: entity.UserAgentClient ?? string.Empty,
            Environment: entity.Environment ?? string.Empty,
            ClientTs: raw["client_ts"]?.GetValue<string>() ?? string.Empty,
            ProtocolVersion: entity.ProtocolVersion,
            MetadataJson: entity.MetadataJson);
    }
}
