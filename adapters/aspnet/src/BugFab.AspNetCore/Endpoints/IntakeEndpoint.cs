using System.Buffers;
using System.Text.Json.Nodes;
using BugFab.AspNetCore.GitHub;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// <c>POST /bug-reports</c> — the intake endpoint.
/// </summary>
/// <remarks>
/// <para>
/// Accepts a multipart body with two parts: <c>metadata</c> (JSON string) and
/// <c>screenshot</c> (PNG file). Validates protocol version, severity, status,
/// report_type strictly. Verifies the screenshot's PNG magic bytes — wrong
/// type rejects with <c>415</c>.
/// </para>
/// <para>
/// Captures the request-header <c>User-Agent</c> as the source-of-truth
/// <c>server_user_agent</c> field, preserving any client-supplied value as
/// <c>client_reported_user_agent</c> for diagnostics.
/// </para>
/// </remarks>
public static class IntakeEndpoint
{
    public static RouteHandlerBuilder Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        // Accepts metadata is ENFORCED for minimal APIs: a request whose
        // Content-Type matches neither entry is rejected with an empty-body
        // 415 before the handler runs. Both form envelopes are listed so a
        // urlencoded body (which can never carry a file) reaches the handler
        // and gets the protocol-mandated 400 "screenshot part is required"
        // instead; anything else (e.g. application/json) still 415s in the
        // handler's HasFormContentType check with the protocol envelope.
        return app.MapPost("/bug-reports", HandleAsync)
            .WithName("BugFab_Intake")
            .WithDisplayName("Bug-Fab Intake")
            .DisableAntiforgery()
            .Accepts<IFormFile>("multipart/form-data", "application/x-www-form-urlencoded");
    }

    public static async Task<IResult> HandleAsync(
        HttpRequest request,
        IStorage storage,
        GitHubIssueSync githubSync,
        ILoggerFactory loggerFactory,
        Microsoft.Extensions.Options.IOptions<BugFabOptions> opts,
        CancellationToken ct)
    {
        var logger = loggerFactory.CreateLogger("BugFab.Intake");
        var options = opts.Value;

        if (!request.HasFormContentType)
        {
            return ErrorResults.UnsupportedMediaType(
                "Content-Type must be multipart/form-data");
        }

        IFormCollection form;
        try
        {
            form = await request.ReadFormAsync(ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            return ErrorResults.ValidationError($"Failed to read multipart body: {ex.Message}");
        }

        if (!form.TryGetValue("metadata", out var metadataValues) ||
            string.IsNullOrEmpty(metadataValues.ToString()))
        {
            return ErrorResults.ValidationError("metadata part is required");
        }

        var metadataJson = metadataValues.ToString();
        if (System.Text.Encoding.UTF8.GetByteCount(metadataJson) > options.MaxMetadataBytes)
        {
            return ErrorResults.PayloadTooLarge(
                "metadata exceeds size limit",
                options.MaxMetadataBytes);
        }

        if (form.Files.Count == 0 || form.Files["screenshot"] is not { } screenshotFile)
        {
            return ErrorResults.ValidationError("screenshot part is required");
        }

        if (screenshotFile.Length <= 0)
        {
            return ErrorResults.ValidationError("screenshot file is empty");
        }
        if (screenshotFile.Length > options.MaxScreenshotBytes)
        {
            return ErrorResults.PayloadTooLarge(
                "screenshot exceeds size limit",
                options.MaxScreenshotBytes);
        }

        // Buffer the screenshot fully so we can magic-byte validate before
        // handing bytes to storage. The cap above limits worst-case memory.
        byte[] screenshotBytes;
        await using (var stream = screenshotFile.OpenReadStream())
        {
            using var ms = new MemoryStream(capacity: (int)screenshotFile.Length);
            await stream.CopyToAsync(ms, ct).ConfigureAwait(false);
            screenshotBytes = ms.ToArray();
        }

        if (!PayloadValidator.IsValidPng(screenshotBytes))
        {
            return ErrorResults.UnsupportedMediaType(
                "screenshot must be a PNG image (image/png)");
        }

        var validation = PayloadValidator.ValidateMetadata(metadataJson);
        if (validation.IsUnsupportedProtocolVersion)
        {
            return ErrorResults.UnsupportedProtocolVersion(
                $"protocol_version '{validation.RejectedProtocolVersion}' is not supported by this adapter");
        }
        if (!validation.IsValid)
        {
            return ErrorResults.SchemaError(validation.Failures);
        }

        var metadata = validation.Metadata!;

        // Stamp the server-derived fields BEFORE persistence. The wire
        // protocol's User-Agent trust boundary requires us to capture this
        // independently of the client-supplied value.
        var rawObj = metadata.RawObject;
        var serverUserAgent = request.Headers.UserAgent.ToString();
        var clientUserAgent = (rawObj["context"] as JsonObject)?["user_agent"]?.GetValue<string>() ?? string.Empty;
        var environment = (rawObj["context"] as JsonObject)?["environment"]?.GetValue<string>()
            ?? rawObj["environment"]?.GetValue<string>()
            ?? string.Empty;

        rawObj["server_user_agent"] = serverUserAgent;
        rawObj["client_reported_user_agent"] = clientUserAgent;
        rawObj["environment"] = environment;

        string reportId;
        try
        {
            reportId = await storage.SaveReportAsync(rawObj, screenshotBytes, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Bug-Fab storage save failed");
            return ErrorResults.InternalError("Failed to persist bug report");
        }

        var detail = await storage.GetReportAsync(reportId, ct).ConfigureAwait(false);
        if (detail is null)
        {
            // Storage contract violation — saved an id we can't read back.
            return ErrorResults.InternalError("Stored report could not be read back");
        }

        // GitHub sync is best-effort. A failed POST does not roll back the
        // local save; the report simply lacks a github_issue_url until a
        // later replay or manual cross-link.
        string? githubIssueUrl = null;
        if (githubSync.Enabled)
        {
            var (issueNumber, issueUrl) = await githubSync.CreateIssueAsync(detail, ct)
                .ConfigureAwait(false);
            if (issueNumber is not null && issueUrl is not null)
            {
                githubIssueUrl = issueUrl;
                await storage.SetGitHubLinkAsync(reportId, issueNumber.Value, issueUrl, ct)
                    .ConfigureAwait(false);
            }
        }

        // Per PROTOCOL.md § Response — minimal envelope, NOT the full
        // BugReportDetail. Privacy: response bodies may be logged by reverse
        // proxies; user-submitted free text shouldn't ride along.
        var responseBody = new JsonObject
        {
            ["id"] = reportId,
            ["received_at"] = detail.CreatedAt,
            ["stored_at"] = $"bug-fab://reports/{reportId}",
            ["github_issue_url"] = githubIssueUrl,
        };

        return Results.Json(responseBody,
            options: options.JsonOptions,
            statusCode: StatusCodes.Status201Created);
    }
}
