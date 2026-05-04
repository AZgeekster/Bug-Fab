using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace BugFab.AspNetCore.Storage;

/// <summary>
/// Disk-only <see cref="IStorage"/>. One folder per report, with
/// <c>metadata.json</c>, <c>lifecycle.json</c>, and <c>screenshot.png</c>
/// inside.
/// </summary>
/// <remarks>
/// Suitable for tests, demos, and single-process deployments. NOT
/// suitable for multi-replica production — there's no transactional locking.
/// Switch to <see cref="EfCoreStorage"/> for production.
/// </remarks>
public sealed class FileStorage : IStorage
{
    private readonly BugFabOptions _options;
    private readonly object _idLock = new();
    private readonly JsonSerializerOptions _jsonOptions;

    public FileStorage(BugFabOptions options)
    {
        _options = options;
        _jsonOptions = options.JsonOptions;
        Directory.CreateDirectory(options.StorageDirectory);
    }

    public Task<string> SaveReportAsync(
        JsonObject metadata,
        ReadOnlyMemory<byte> screenshot,
        CancellationToken ct = default)
    {
        var id = AllocateId();
        var dir = Path.Combine(_options.StorageDirectory, id);
        Directory.CreateDirectory(dir);

        var receivedAt = DateTimeOffset.UtcNow;
        metadata["id"] = id;
        metadata["created_at"] = receivedAt.ToString("O");
        metadata["status"] = "open";
        metadata["has_screenshot"] = true;

        File.WriteAllText(
            Path.Combine(dir, "metadata.json"),
            metadata.ToJsonString(_jsonOptions));

        var lifecycle = new JsonArray
        {
            new JsonObject
            {
                ["action"] = "created",
                ["by"] = (metadata["reporter"] as JsonObject)?["email"]?.GetValue<string>() ?? "anonymous",
                ["at"] = receivedAt.ToString("O"),
                ["status"] = "open",
            },
        };
        File.WriteAllText(
            Path.Combine(dir, "lifecycle.json"),
            lifecycle.ToJsonString(_jsonOptions));

        File.WriteAllBytes(Path.Combine(dir, "screenshot.png"), screenshot.ToArray());

        return Task.FromResult(id);
    }

    public Task<StoredReport?> GetReportAsync(string reportId, CancellationToken ct = default)
    {
        var dir = Path.Combine(_options.StorageDirectory, reportId);
        var metaPath = Path.Combine(dir, "metadata.json");
        if (!File.Exists(metaPath)) return Task.FromResult<StoredReport?>(null);

        var raw = JsonNode.Parse(File.ReadAllText(metaPath)) as JsonObject ?? new JsonObject();
        var lifecyclePath = Path.Combine(dir, "lifecycle.json");
        var lifecycle = File.Exists(lifecyclePath)
            ? (JsonNode.Parse(File.ReadAllText(lifecyclePath)) as JsonArray)?
                .OfType<JsonObject>().ToList() ?? new List<JsonObject>()
            : new List<JsonObject>();

        var ctxNode = raw["context"] as JsonObject ?? new JsonObject();
        var reporterNode = raw["reporter"] as JsonObject ?? new JsonObject();
        var tags = (raw["tags"] as JsonArray)?
            .Select(t => t?.GetValue<string>() ?? string.Empty)
            .Where(s => !string.IsNullOrEmpty(s))
            .ToList() ?? new List<string>();

        return Task.FromResult<StoredReport?>(new StoredReport(
            Id: reportId,
            Title: raw["title"]?.GetValue<string>() ?? string.Empty,
            ReportType: raw["report_type"]?.GetValue<string>() ?? "bug",
            Severity: raw["severity"]?.GetValue<string>() ?? "medium",
            Status: raw["status"]?.GetValue<string>() ?? "open",
            Module: ctxNode["module"]?.GetValue<string>() ?? string.Empty,
            CreatedAt: raw["created_at"]?.GetValue<string>() ?? string.Empty,
            UpdatedAt: raw["updated_at"]?.GetValue<string>() ?? string.Empty,
            HasScreenshot: File.Exists(Path.Combine(dir, "screenshot.png")),
            GitHubIssueUrl: raw["github_issue_url"]?.GetValue<string>(),
            GitHubIssueNumber: raw["github_issue_number"] is JsonValue n && n.TryGetValue<int>(out var num)
                ? num
                : null,
            Description: raw["description"]?.GetValue<string>() ?? string.Empty,
            ExpectedBehavior: raw["expected_behavior"]?.GetValue<string>() ?? string.Empty,
            Tags: tags,
            Reporter: reporterNode,
            Context: ctxNode,
            Lifecycle: lifecycle,
            ServerUserAgent: raw["server_user_agent"]?.GetValue<string>() ?? string.Empty,
            ClientReportedUserAgent: raw["client_reported_user_agent"]?.GetValue<string>() ?? string.Empty,
            Environment: raw["environment"]?.GetValue<string>() ?? string.Empty,
            ClientTs: raw["client_ts"]?.GetValue<string>() ?? string.Empty,
            ProtocolVersion: raw["protocol_version"]?.GetValue<string>() ?? "0.1",
            MetadataJson: raw.ToJsonString()));
    }

    public Task<(IReadOnlyList<StoredReportSummary> Items, int Total)> ListReportsAsync(
        ReportFilters filters,
        int page,
        int pageSize,
        CancellationToken ct = default)
    {
        var rows = EnumerateAllReports(filters)
            .OrderByDescending(s => s.CreatedAt, StringComparer.Ordinal)
            .ToList();
        var total = rows.Count;
        var paged = rows.Skip((page - 1) * pageSize).Take(pageSize).ToList();
        return Task.FromResult<(IReadOnlyList<StoredReportSummary>, int)>((paged, total));
    }

    public Task<string?> GetScreenshotPathAsync(string reportId, CancellationToken ct = default)
    {
        var path = Path.Combine(_options.StorageDirectory, reportId, "screenshot.png");
        return Task.FromResult(File.Exists(path) ? path : null);
    }

    public async Task<StoredReport?> UpdateStatusAsync(
        string reportId,
        string status,
        string fixCommit,
        string fixDescription,
        string by,
        CancellationToken ct = default)
    {
        var dir = Path.Combine(_options.StorageDirectory, reportId);
        var metaPath = Path.Combine(dir, "metadata.json");
        if (!File.Exists(metaPath)) return null;

        var raw = JsonNode.Parse(File.ReadAllText(metaPath)) as JsonObject ?? new JsonObject();
        raw["status"] = status;
        raw["updated_at"] = DateTimeOffset.UtcNow.ToString("O");
        File.WriteAllText(metaPath, raw.ToJsonString(_jsonOptions));

        var lifecyclePath = Path.Combine(dir, "lifecycle.json");
        var arr = File.Exists(lifecyclePath)
            ? JsonNode.Parse(File.ReadAllText(lifecyclePath)) as JsonArray ?? new JsonArray()
            : new JsonArray();

        arr.Add(new JsonObject
        {
            ["action"] = "status_changed",
            ["by"] = by,
            ["at"] = DateTimeOffset.UtcNow.ToString("O"),
            ["status"] = status,
            ["fix_commit"] = fixCommit,
            ["fix_description"] = fixDescription,
        });
        File.WriteAllText(lifecyclePath, arr.ToJsonString(_jsonOptions));

        return await GetReportAsync(reportId, ct).ConfigureAwait(false);
    }

    public Task<bool> DeleteReportAsync(string reportId, CancellationToken ct = default)
    {
        var dir = Path.Combine(_options.StorageDirectory, reportId);
        if (!Directory.Exists(dir)) return Task.FromResult(false);
        Directory.Delete(dir, recursive: true);
        return Task.FromResult(true);
    }

    public async Task<int> BulkCloseFixedAsync(string by, CancellationToken ct = default)
    {
        var fixedReports = EnumerateAllReports(new ReportFilters(Status: "fixed"))
            .Select(s => s.Id)
            .ToList();
        foreach (var id in fixedReports)
        {
            await UpdateStatusAsync(id, "closed", string.Empty, string.Empty, by, ct)
                .ConfigureAwait(false);
        }
        return fixedReports.Count;
    }

    public Task<int> BulkArchiveClosedAsync(CancellationToken ct = default)
    {
        var archiveDir = Path.Combine(_options.StorageDirectory, "archive");
        Directory.CreateDirectory(archiveDir);

        var closed = EnumerateAllReports(new ReportFilters(Status: "closed"))
            .Select(s => s.Id)
            .ToList();
        foreach (var id in closed)
        {
            var src = Path.Combine(_options.StorageDirectory, id);
            var dst = Path.Combine(archiveDir, id);
            if (Directory.Exists(dst)) Directory.Delete(dst, recursive: true);
            Directory.Move(src, dst);
        }
        return Task.FromResult(closed.Count);
    }

    public Task SetGitHubLinkAsync(
        string reportId,
        int issueNumber,
        string issueUrl,
        CancellationToken ct = default)
    {
        var metaPath = Path.Combine(_options.StorageDirectory, reportId, "metadata.json");
        if (!File.Exists(metaPath)) return Task.CompletedTask;
        var raw = JsonNode.Parse(File.ReadAllText(metaPath)) as JsonObject ?? new JsonObject();
        raw["github_issue_number"] = issueNumber;
        raw["github_issue_url"] = issueUrl;
        File.WriteAllText(metaPath, raw.ToJsonString(_jsonOptions));
        return Task.CompletedTask;
    }

    public Task<IReadOnlyDictionary<string, int>> ComputeStatsAsync(CancellationToken ct = default)
    {
        var result = new Dictionary<string, int>(StringComparer.Ordinal)
        {
            ["open"] = 0,
            ["investigating"] = 0,
            ["fixed"] = 0,
            ["closed"] = 0,
            ["total"] = 0,
        };
        foreach (var summary in EnumerateAllReports(new ReportFilters()))
        {
            if (result.ContainsKey(summary.Status)) result[summary.Status]++;
            result["total"]++;
        }
        return Task.FromResult<IReadOnlyDictionary<string, int>>(result);
    }

    private IEnumerable<StoredReportSummary> EnumerateAllReports(ReportFilters filters)
    {
        if (!Directory.Exists(_options.StorageDirectory)) yield break;

        foreach (var dir in Directory.EnumerateDirectories(_options.StorageDirectory))
        {
            var id = Path.GetFileName(dir);
            if (id is null || id.Equals("archive", StringComparison.Ordinal)) continue;
            if (!id.StartsWith("bug-", StringComparison.Ordinal)) continue;

            var metaPath = Path.Combine(dir, "metadata.json");
            if (!File.Exists(metaPath)) continue;

            JsonObject raw;
            try
            {
                raw = JsonNode.Parse(File.ReadAllText(metaPath)) as JsonObject ?? new JsonObject();
            }
            catch (JsonException)
            {
                continue;
            }

            var status = raw["status"]?.GetValue<string>() ?? "open";
            var severity = raw["severity"]?.GetValue<string>() ?? "medium";
            var module = (raw["context"] as JsonObject)?["module"]?.GetValue<string>() ?? string.Empty;
            var environment = raw["environment"]?.GetValue<string>() ?? string.Empty;

            if (!string.IsNullOrEmpty(filters.Status) && status != filters.Status) continue;
            if (!string.IsNullOrEmpty(filters.Severity) && severity != filters.Severity) continue;
            if (!string.IsNullOrEmpty(filters.Module) && module != filters.Module) continue;
            if (!string.IsNullOrEmpty(filters.Environment) && environment != filters.Environment) continue;

            yield return new StoredReportSummary(
                Id: id,
                Title: raw["title"]?.GetValue<string>() ?? string.Empty,
                ReportType: raw["report_type"]?.GetValue<string>() ?? "bug",
                Severity: severity,
                Status: status,
                Module: module,
                CreatedAt: raw["created_at"]?.GetValue<string>() ?? string.Empty,
                HasScreenshot: File.Exists(Path.Combine(dir, "screenshot.png")),
                GitHubIssueUrl: raw["github_issue_url"]?.GetValue<string>());
        }
    }

    private string AllocateId()
    {
        lock (_idLock)
        {
            int max = 0;
            if (Directory.Exists(_options.StorageDirectory))
            {
                foreach (var dir in Directory.EnumerateDirectories(_options.StorageDirectory))
                {
                    var name = Path.GetFileName(dir);
                    if (name is null || !name.StartsWith("bug-", StringComparison.Ordinal)) continue;
                    var digits = new string(name.Skip(4).SkipWhile(char.IsLetter).ToArray());
                    if (int.TryParse(digits, out var n) && n > max) max = n;
                }
            }
            var prefix = string.IsNullOrEmpty(_options.IdPrefix) ? string.Empty : _options.IdPrefix;
            return $"bug-{prefix}{max + 1:D3}";
        }
    }
}
