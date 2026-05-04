using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>DELETE /reports/{id}</c> — hard-delete a single report.
/// </summary>
public static class DeleteEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapDelete("/reports/{id}", async (
            string id,
            IStorage storage,
            CancellationToken ct) =>
        {
            if (!options.ViewerPermissions.CanDelete)
            {
                return ErrorResults.Forbidden("viewer action 'can_delete' is disabled by configuration");
            }
            if (!DetailEndpoint.ReportIdPattern.IsMatch(id))
            {
                return ErrorResults.NotFound("Bug report not found");
            }

            var deleted = await storage.DeleteReportAsync(id, ct).ConfigureAwait(false);
            return deleted
                ? Results.NoContent()
                : ErrorResults.NotFound("Bug report not found");
        }).WithName("BugFab_Delete");
    }
}
