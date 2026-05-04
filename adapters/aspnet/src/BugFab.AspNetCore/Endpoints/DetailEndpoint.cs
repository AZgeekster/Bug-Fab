using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>GET /reports/{id}</c> — full detail for one report.
/// </summary>
public static class DetailEndpoint
{
    /// <summary>
    /// Path-traversal guard. The file backend uses <c>bug-NNN</c> ids and the
    /// SQL backend uses the same shape (with optional <c>P</c> / <c>D</c> env
    /// prefix). Anything outside this character class is rejected with a 404
    /// before it reaches the storage layer.
    /// </summary>
    public static readonly Regex ReportIdPattern = new(
        @"^bug-[A-Za-z]?\d{1,12}$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapGet("/reports/{id}", async (
            string id,
            IStorage storage,
            CancellationToken ct) =>
        {
            if (!ReportIdPattern.IsMatch(id))
            {
                return ErrorResults.NotFound("Bug report not found");
            }

            var report = await storage.GetReportAsync(id, ct).ConfigureAwait(false);
            if (report is null)
            {
                return ErrorResults.NotFound("Bug report not found");
            }

            return Results.Json(ToWireDetail(report),
                options: options.JsonOptions,
                statusCode: StatusCodes.Status200OK);
        }).WithName("BugFab_Detail");
    }

    /// <summary>
    /// Project a <see cref="StoredReport"/> to the wire-protocol JSON shape
    /// (<c>BugReportDetail</c> in the schema).
    /// </summary>
    public static JsonObject ToWireDetail(StoredReport r)
    {
        var lifecycle = new JsonArray();
        foreach (var entry in r.Lifecycle)
        {
            lifecycle.Add(JsonNode.Parse(entry.ToJsonString()));
        }

        return new JsonObject
        {
            ["id"] = r.Id,
            ["title"] = r.Title,
            ["report_type"] = r.ReportType,
            ["severity"] = r.Severity,
            ["status"] = r.Status,
            ["module"] = r.Module,
            ["created_at"] = r.CreatedAt,
            ["has_screenshot"] = r.HasScreenshot,
            ["github_issue_url"] = r.GitHubIssueUrl,
            ["description"] = r.Description,
            ["expected_behavior"] = r.ExpectedBehavior,
            ["tags"] = new JsonArray(r.Tags.Select(t => (JsonNode)t!).ToArray()),
            ["reporter"] = JsonNode.Parse(r.Reporter.ToJsonString()),
            ["context"] = JsonNode.Parse(r.Context.ToJsonString()),
            ["lifecycle"] = lifecycle,
            ["server_user_agent"] = r.ServerUserAgent,
            ["client_reported_user_agent"] = r.ClientReportedUserAgent,
            ["environment"] = r.Environment,
            ["client_ts"] = r.ClientTs,
            ["protocol_version"] = r.ProtocolVersion,
            ["updated_at"] = r.UpdatedAt,
            ["github_issue_number"] = r.GitHubIssueNumber,
        };
    }
}
