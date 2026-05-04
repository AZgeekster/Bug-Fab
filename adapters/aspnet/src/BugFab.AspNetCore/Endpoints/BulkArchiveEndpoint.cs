using System.Text.Json.Nodes;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>POST /bulk-archive-closed</c> — move every <c>closed</c> report into
/// the storage backend's archive area.
/// </summary>
public static class BulkArchiveEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapPost("/bulk-archive-closed", async (
            IStorage storage,
            CancellationToken ct) =>
        {
            if (!options.ViewerPermissions.CanBulk)
            {
                return ErrorResults.Forbidden("viewer action 'can_bulk' is disabled by configuration");
            }

            var archived = await storage.BulkArchiveClosedAsync(ct).ConfigureAwait(false);

            return Results.Json(new JsonObject { ["archived"] = archived },
                options: options.JsonOptions,
                statusCode: StatusCodes.Status200OK);
        }).WithName("BugFab_BulkArchiveClosed");
    }
}
