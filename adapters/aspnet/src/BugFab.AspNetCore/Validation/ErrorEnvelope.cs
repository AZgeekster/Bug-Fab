using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Http;

namespace BugFab.AspNetCore.Validation;

/// <summary>
/// The Bug-Fab error envelope. Every non-2xx response (except <c>204</c>) uses
/// this exact shape per <c>docs/PROTOCOL.md</c> § "Error response shape".
/// </summary>
/// <remarks>
/// Do NOT substitute ASP.NET Core's <c>ProblemDetails</c> /
/// <c>ValidationProblemDetails</c>. The wire-protocol contract is fixed at
/// <c>{ error, detail }</c>; conformance tests verify the exact key set.
/// </remarks>
public sealed class ErrorEnvelope
{
    [JsonPropertyName("error")]
    public string Error { get; init; } = string.Empty;

    [JsonPropertyName("detail")]
    public object Detail { get; init; } = string.Empty;

    /// <summary>Optional limit field for <c>413 payload_too_large</c>.</summary>
    [JsonPropertyName("limit_bytes")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? LimitBytes { get; init; }

    /// <summary>Optional retry hint for <c>429 rate_limited</c>.</summary>
    [JsonPropertyName("retry_after_seconds")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? RetryAfterSeconds { get; init; }
}

/// <summary>Helpers for building <see cref="IResult"/> instances that emit
/// the Bug-Fab error envelope.</summary>
public static class ErrorResults
{
    public static IResult ValidationError(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "validation_error", Detail = detail },
            statusCode: StatusCodes.Status400BadRequest);

    public static IResult UnsupportedProtocolVersion(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "unsupported_protocol_version", Detail = detail },
            statusCode: StatusCodes.Status400BadRequest);

    public static IResult SchemaError(object detail) =>
        Results.Json(new ErrorEnvelope { Error = "schema_error", Detail = detail },
            statusCode: StatusCodes.Status422UnprocessableEntity);

    public static IResult UnsupportedMediaType(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "unsupported_media_type", Detail = detail },
            statusCode: StatusCodes.Status415UnsupportedMediaType);

    public static IResult PayloadTooLarge(string detail, long limitBytes) =>
        Results.Json(new ErrorEnvelope
            {
                Error = "payload_too_large",
                Detail = detail,
                LimitBytes = limitBytes,
            },
            statusCode: StatusCodes.Status413PayloadTooLarge);

    public static IResult NotFound(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "not_found", Detail = detail },
            statusCode: StatusCodes.Status404NotFound);

    public static IResult Forbidden(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "forbidden", Detail = detail },
            statusCode: StatusCodes.Status403Forbidden);

    public static IResult RateLimited(string detail, int retryAfterSeconds) =>
        Results.Json(new ErrorEnvelope
            {
                Error = "rate_limited",
                Detail = detail,
                RetryAfterSeconds = retryAfterSeconds,
            },
            statusCode: StatusCodes.Status429TooManyRequests);

    public static IResult InternalError(string detail) =>
        Results.Json(new ErrorEnvelope { Error = "internal_error", Detail = detail },
            statusCode: StatusCodes.Status500InternalServerError);
}
