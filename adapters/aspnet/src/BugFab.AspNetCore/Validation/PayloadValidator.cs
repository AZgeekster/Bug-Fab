using System.Text.Json;
using System.Text.Json.Nodes;

namespace BugFab.AspNetCore.Validation;

/// <summary>
/// Strict validators for the Bug-Fab v0.1 wire protocol.
/// </summary>
/// <remarks>
/// <para>
/// Every helper here is pure and side-effect-free; results are reported via
/// <see cref="ValidationFailure"/>. The intake endpoint composes these into
/// a single pass-or-fail decision.
/// </para>
/// <para>
/// Severity / status / report_type checks are STRICT: invalid values produce a
/// <c>422 schema_error</c>, never a silent fallback. Hand-rolled C# adapters
/// frequently default to <c>"medium"</c>; this is forbidden by the protocol.
/// </para>
/// </remarks>
public static class PayloadValidator
{
    /// <summary>The wire-protocol version this adapter speaks.</summary>
    public const string ProtocolVersion = "0.1";

    /// <summary>Locked severity vocabulary. Adapters MUST reject other values.</summary>
    public static readonly IReadOnlySet<string> AllowedSeverities =
        new HashSet<string>(StringComparer.Ordinal) { "low", "medium", "high", "critical" };

    /// <summary>
    /// Locked status vocabulary for write paths. Read paths accept any string
    /// (deprecated-values rule).
    /// </summary>
    public static readonly IReadOnlySet<string> AllowedStatusesForWrite =
        new HashSet<string>(StringComparer.Ordinal) { "open", "investigating", "fixed", "closed" };

    /// <summary>Locked report-type vocabulary.</summary>
    public static readonly IReadOnlySet<string> AllowedReportTypes =
        new HashSet<string>(StringComparer.Ordinal) { "bug", "feature_request" };

    /// <summary>PNG file signature — the eight magic bytes every PNG starts with.</summary>
    public static readonly byte[] PngSignature =
        new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };

    /// <summary>Maximum length for free-text reporter sub-fields.</summary>
    public const int MaxReporterFieldLength = 256;

    /// <summary>Bug-report title length bounds.</summary>
    public const int MinTitleLength = 1;
    public const int MaxTitleLength = 200;

    /// <summary>
    /// Verify the first 8 bytes match the PNG signature. The screenshot is
    /// always served as <c>image/png</c> to match the upstream contract.
    /// </summary>
    public static bool IsValidPng(ReadOnlySpan<byte> payload)
    {
        if (payload.Length < PngSignature.Length) return false;
        for (var i = 0; i < PngSignature.Length; i++)
        {
            if (payload[i] != PngSignature[i]) return false;
        }
        return true;
    }

    /// <summary>
    /// Extract a string from a JSON node without throwing on wrong-typed
    /// scalars. <c>GetValue&lt;string&gt;()</c> throws on <c>{"title": 123}</c>,
    /// which surfaced as an unhandled 500 instead of a <c>422 schema_error</c>.
    /// Returns true when the node is absent (value null) or a string; false
    /// when the node holds any other JSON type.
    /// </summary>
    private static bool TryGetString(JsonNode? node, out string? value)
    {
        value = null;
        if (node is null) return true;
        if (node is JsonValue v && v.TryGetValue<string>(out var s))
        {
            value = s;
            return true;
        }
        return false;
    }

    /// <summary>
    /// Parse and validate the metadata JSON. Returns a populated
    /// <see cref="ValidatedMetadata"/> on success or a list of failures.
    /// </summary>
    public static ValidationResult ValidateMetadata(string rawJson)
    {
        if (string.IsNullOrWhiteSpace(rawJson))
        {
            return ValidationResult.Failed(
                new ValidationFailure("metadata", "metadata JSON is empty"));
        }

        JsonNode? root;
        try
        {
            root = JsonNode.Parse(rawJson);
        }
        catch (JsonException ex)
        {
            return ValidationResult.Failed(
                new ValidationFailure("metadata", $"metadata is not valid JSON: {ex.Message}"));
        }
        if (root is not JsonObject obj)
        {
            return ValidationResult.Failed(
                new ValidationFailure("metadata", "metadata JSON must be an object"));
        }

        var failures = new List<ValidationFailure>();

        // protocol_version — required, MUST equal "0.1"
        if (!TryGetString(obj["protocol_version"], out var protocolVersion))
        {
            failures.Add(new ValidationFailure("protocol_version",
                "protocol_version must be a string"));
        }
        else if (string.IsNullOrEmpty(protocolVersion))
        {
            failures.Add(new ValidationFailure("protocol_version",
                "protocol_version is required"));
        }
        else if (protocolVersion != ProtocolVersion)
        {
            return ValidationResult.UnsupportedProtocolVersion(protocolVersion);
        }

        // title — required, length 1-200
        if (!TryGetString(obj["title"], out var title))
        {
            failures.Add(new ValidationFailure("title", "title must be a string"));
        }
        else if (string.IsNullOrEmpty(title))
        {
            failures.Add(new ValidationFailure("title", "title is required"));
        }
        else if (title.Length < MinTitleLength || title.Length > MaxTitleLength)
        {
            failures.Add(new ValidationFailure("title",
                $"title length must be between {MinTitleLength} and {MaxTitleLength}"));
        }

        // client_ts — required, non-empty
        if (!TryGetString(obj["client_ts"], out var clientTs))
        {
            failures.Add(new ValidationFailure("client_ts", "client_ts must be a string"));
        }
        else if (string.IsNullOrEmpty(clientTs))
        {
            failures.Add(new ValidationFailure("client_ts", "client_ts is required"));
        }

        // report_type — optional, default "bug"
        var reportType = "bug";
        if (!TryGetString(obj["report_type"], out var reportTypeRaw))
        {
            failures.Add(new ValidationFailure("report_type",
                "report_type must be a string"));
        }
        else
        {
            reportType = reportTypeRaw ?? "bug";
            if (!AllowedReportTypes.Contains(reportType))
            {
                failures.Add(new ValidationFailure("report_type",
                    $"report_type must be one of: {string.Join(", ", AllowedReportTypes)}"));
            }
        }

        // severity — optional, default "medium"
        var severity = "medium";
        if (!TryGetString(obj["severity"], out var severityRaw))
        {
            failures.Add(new ValidationFailure("severity",
                "severity must be a string"));
        }
        else
        {
            severity = severityRaw ?? "medium";
            if (!AllowedSeverities.Contains(severity))
            {
                failures.Add(new ValidationFailure("severity",
                    $"severity must be one of: {string.Join(", ", AllowedSeverities)}"));
            }
        }

        // reporter sub-fields — capped at 256 chars
        if (obj["reporter"] is JsonObject reporter)
        {
            foreach (var field in new[] { "name", "email", "user_id" })
            {
                if (!TryGetString(reporter[field], out var v))
                {
                    failures.Add(new ValidationFailure($"reporter.{field}",
                        $"reporter.{field} must be a string"));
                }
                else if (v is not null && v.Length > MaxReporterFieldLength)
                {
                    failures.Add(new ValidationFailure($"reporter.{field}",
                        $"reporter.{field} length must be <= {MaxReporterFieldLength}"));
                }
            }
        }

        if (failures.Count > 0)
        {
            return ValidationResult.Failed(failures.ToArray());
        }

        return ValidationResult.Success(new ValidatedMetadata(
            ProtocolVersion: protocolVersion!,
            Title: title!,
            ClientTs: clientTs!,
            ReportType: reportType,
            Severity: severity,
            RawObject: obj));
    }

    /// <summary>
    /// Validate a status-update body. Returns the parsed status string or a
    /// failure. Strict — unknown enum values are rejected.
    /// </summary>
    public static StatusUpdateValidationResult ValidateStatusUpdate(JsonObject body)
    {
        if (!TryGetString(body["status"], out var statusValue))
        {
            return new StatusUpdateValidationResult(
                Failure: new ValidationFailure("status", "status must be a string"));
        }
        if (string.IsNullOrEmpty(statusValue))
        {
            return new StatusUpdateValidationResult(
                Failure: new ValidationFailure("status", "status is required"));
        }
        if (!AllowedStatusesForWrite.Contains(statusValue))
        {
            return new StatusUpdateValidationResult(
                Failure: new ValidationFailure("status",
                    $"status must be one of: {string.Join(", ", AllowedStatusesForWrite)}"));
        }

        if (!TryGetString(body["fix_commit"], out var fixCommit))
        {
            return new StatusUpdateValidationResult(
                Failure: new ValidationFailure("fix_commit", "fix_commit must be a string"));
        }
        if (!TryGetString(body["fix_description"], out var fixDescription))
        {
            return new StatusUpdateValidationResult(
                Failure: new ValidationFailure("fix_description",
                    "fix_description must be a string"));
        }

        return new StatusUpdateValidationResult(
            Status: statusValue,
            FixCommit: fixCommit ?? string.Empty,
            FixDescription: fixDescription ?? string.Empty);
    }
}

/// <summary>One validation failure on one field.</summary>
public sealed record ValidationFailure(string Field, string Message);

/// <summary>Parsed + validated metadata payload, ready for storage.</summary>
public sealed record ValidatedMetadata(
    string ProtocolVersion,
    string Title,
    string ClientTs,
    string ReportType,
    string Severity,
    JsonObject RawObject);

/// <summary>Result of metadata validation: either success + parsed payload,
/// or a list of field-level failures.</summary>
public sealed class ValidationResult
{
    public bool IsValid { get; init; }
    public bool IsUnsupportedProtocolVersion { get; init; }
    public string? RejectedProtocolVersion { get; init; }
    public ValidatedMetadata? Metadata { get; init; }
    public IReadOnlyList<ValidationFailure> Failures { get; init; } = Array.Empty<ValidationFailure>();

    public static ValidationResult Success(ValidatedMetadata metadata) =>
        new() { IsValid = true, Metadata = metadata };

    public static ValidationResult Failed(params ValidationFailure[] failures) =>
        new() { IsValid = false, Failures = failures };

    public static ValidationResult UnsupportedProtocolVersion(string rejected) =>
        new() { IsValid = false, IsUnsupportedProtocolVersion = true, RejectedProtocolVersion = rejected };
}

/// <summary>Result of a status-update body validation.</summary>
public sealed record StatusUpdateValidationResult(
    string? Status = null,
    string FixCommit = "",
    string FixDescription = "",
    ValidationFailure? Failure = null)
{
    public bool IsValid => Failure is null && !string.IsNullOrEmpty(Status);
}
