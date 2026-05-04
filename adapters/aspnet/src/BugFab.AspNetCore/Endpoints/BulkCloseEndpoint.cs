using System.Text.Json.Nodes;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>POST /bulk-close-fixed</c> — transition every <c>fixed</c> report to
/// <c>closed</c>.
/// </summary>
public static class BulkCloseEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapPost("/bulk-close-fixed", async (
            HttpContext http,
            IStorage storage,
            CancellationToken ct) =>
        {
            if (!options.ViewerPermissions.CanBulk)
            {
                return ErrorResults.Forbidden("viewer action 'can_bulk' is disabled by configuration");
            }

            var actor = StatusEndpoint.ResolveActor(http);
            var closed = await storage.BulkCloseFixedAsync(actor, ct).ConfigureAwait(false);

            return Results.Json(new JsonObject { ["closed"] = closed },
                options: options.JsonOptions,
                statusCode: StatusCodes.Status200OK);
        }).WithName("BugFab_BulkCloseFixed");
    }
}
