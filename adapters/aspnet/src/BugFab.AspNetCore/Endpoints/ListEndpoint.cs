using System.Text.Json.Nodes;
using BugFab.AspNetCore.Storage;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>GET /reports</c> — paginated, filterable list of report summaries.
/// </summary>
public static class ListEndpoint
{
    private const int DefaultPageSize = 20;
    private const int MaxPageSize = 200;

    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapGet("/reports", async (
            HttpContext http,
            IStorage storage,
            CancellationToken ct) =>
        {
            var query = http.Request.Query;
            var page = ParseInt(query, "page", 1, min: 1);
            var pageSize = ParseInt(query, "page_size", DefaultPageSize, min: 1, max: MaxPageSize);
            var includeArchived = ParseBool(query, "include_archived", false);

            var filters = new ReportFilters(
                Status: GetString(query, "status"),
                Severity: GetString(query, "severity"),
                Module: GetString(query, "module"),
                Environment: GetString(query, "environment"),
                IncludeArchived: includeArchived);

            var (items, total) = await storage.ListReportsAsync(filters, page, pageSize, ct)
                .ConfigureAwait(false);
            var stats = await storage.ComputeStatsAsync(ct).ConfigureAwait(false);

            var jsonItems = new JsonArray();
            foreach (var item in items)
            {
                jsonItems.Add(SummaryToJson(item));
            }

            var response = new JsonObject
            {
                ["items"] = jsonItems,
                ["total"] = total,
                ["page"] = page,
                ["page_size"] = pageSize,
                ["stats"] = StatsToJson(stats),
            };

            return Results.Json(response,
                options: options.JsonOptions,
                statusCode: StatusCodes.Status200OK);
        }).WithName("BugFab_List");
    }

    private static JsonObject SummaryToJson(StoredReportSummary s) => new()
    {
        ["id"] = s.Id,
        ["title"] = s.Title,
        ["report_type"] = s.ReportType,
        ["severity"] = s.Severity,
        ["status"] = s.Status,
        ["module"] = s.Module,
        ["created_at"] = s.CreatedAt,
        ["has_screenshot"] = s.HasScreenshot,
        ["github_issue_url"] = s.GitHubIssueUrl,
    };

    private static JsonObject StatsToJson(IReadOnlyDictionary<string, int> stats)
    {
        var node = new JsonObject();
        foreach (var (k, v) in stats) node[k] = v;
        return node;
    }

    private static string? GetString(IQueryCollection q, string key)
    {
        var raw = q[key].ToString();
        return string.IsNullOrWhiteSpace(raw) ? null : raw.Trim();
    }

    private static int ParseInt(IQueryCollection q, string key, int @default, int min = int.MinValue, int max = int.MaxValue)
    {
        var raw = q[key].ToString();
        if (string.IsNullOrEmpty(raw) || !int.TryParse(raw, out var value)) return @default;
        if (value < min) value = min;
        if (value > max) value = max;
        return value;
    }

    private static bool ParseBool(IQueryCollection q, string key, bool @default)
    {
        var raw = q[key].ToString();
        if (string.IsNullOrEmpty(raw)) return @default;
        return bool.TryParse(raw, out var v) ? v : @default;
    }
}
