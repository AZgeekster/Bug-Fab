using System.Text.Json.Nodes;
using BugFab.AspNetCore.GitHub;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Logging;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>PUT /reports/{id}/status</c> — apply a status change.
/// </summary>
/// <remarks>
/// Body: <c>{ "status": "...", "fix_commit": "...", "fix_description": "..." }</c>.
/// Strict status validation — invalid values reject with <c>422</c>. Appends a
/// <c>status_changed</c> lifecycle entry. Fires GitHub state sync when a
/// linked issue exists; failures log only.
/// </remarks>
public static class StatusEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapPut("/reports/{id}/status", async (
            string id,
            HttpContext http,
            IStorage storage,
            GitHubIssueSync githubSync,
            ILoggerFactory loggerFactory,
            CancellationToken ct) =>
        {
            if (!options.ViewerPermissions.CanEditStatus)
            {
                return ErrorResults.Forbidden("viewer action 'can_edit_status' is disabled by configuration");
            }
            if (!DetailEndpoint.ReportIdPattern.IsMatch(id))
            {
                return ErrorResults.NotFound("Bug report not found");
            }

            JsonObject body;
            try
            {
                using var reader = new StreamReader(http.Request.Body);
                var raw = await reader.ReadToEndAsync(ct).ConfigureAwait(false);
                body = JsonNode.Parse(raw) as JsonObject ?? new JsonObject();
            }
            catch (Exception ex)
            {
                return ErrorResults.ValidationError($"request body is not valid JSON: {ex.Message}");
            }

            var validation = PayloadValidator.ValidateStatusUpdate(body);
            if (!validation.IsValid)
            {
                return ErrorResults.SchemaError(new[] { validation.Failure! });
            }

            var actor = ResolveActor(http);
            var updated = await storage.UpdateStatusAsync(
                    id,
                    validation.Status!,
                    validation.FixCommit,
                    validation.FixDescription,
                    actor,
                    ct)
                .ConfigureAwait(false);
            if (updated is null)
            {
                return ErrorResults.NotFound("Bug report not found");
            }

            if (githubSync.Enabled && updated.GitHubIssueNumber is int issueNumber)
            {
                await githubSync.SyncIssueStateAsync(issueNumber, validation.Status!, ct)
                    .ConfigureAwait(false);
            }

            return Results.Json(DetailEndpoint.ToWireDetail(updated),
                options: options.JsonOptions,
                statusCode: StatusCodes.Status200OK);
        }).WithName("BugFab_StatusUpdate");
    }

    /// <summary>
    /// Best-effort actor extraction. Bug-Fab v0.1 has no AuthAdapter, so this
    /// reads from <c>HttpContext.Items["bug_fab_actor"]</c> if a consumer's
    /// own middleware put a value there; otherwise falls back to "viewer".
    /// </summary>
    internal static string ResolveActor(HttpContext http)
    {
        if (http.Items.TryGetValue("bug_fab_actor", out var value) && value is string s && !string.IsNullOrEmpty(s))
        {
            return s;
        }
        return http.User.Identity?.Name ?? "viewer";
    }
}
