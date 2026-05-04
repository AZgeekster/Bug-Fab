using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>GET /reports/{id}/screenshot</c> — raw PNG screenshot bytes.
/// </summary>
/// <remarks>
/// The media type is always <c>image/png</c>. PROTOCOL.md v0.1 locks the
/// screenshot wire format to PNG; the intake endpoint rejects anything else
/// with 415, and the bundled JS frontend (html2canvas) only emits PNG.
/// </remarks>
public static class ScreenshotEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        return app.MapGet("/reports/{id}/screenshot", async (
            string id,
            IStorage storage,
            CancellationToken ct) =>
        {
            if (!DetailEndpoint.ReportIdPattern.IsMatch(id))
            {
                return ErrorResults.NotFound("Screenshot not found");
            }

            var path = await storage.GetScreenshotPathAsync(id, ct).ConfigureAwait(false);
            if (path is null)
            {
                return ErrorResults.NotFound("Screenshot not found");
            }

            return Results.File(path, contentType: "image/png");
        }).WithName("BugFab_Screenshot");
    }
}
