using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using BugFab.AspNetCore.Storage;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace BugFab.AspNetCore.GitHub;

/// <summary>
/// Best-effort sync of new bug reports to GitHub Issues.
/// </summary>
/// <remarks>
/// <para>
/// Per <c>docs/PROTOCOL.md</c> § "Failure modes that MUST NOT yield non-2xx",
/// every method here swallows exceptions and returns a "no link" result. A
/// GitHub outage MUST NOT fail an otherwise-valid bug submission.
/// </para>
/// <para>
/// Uses <see cref="IHttpClientFactory"/> via the <see cref="HttpClient"/> the
/// DI container injects so consumers can layer Polly retry policies, custom
/// handlers, or test doubles via the standard ASP.NET Core HTTP client
/// extensibility points.
/// </para>
/// </remarks>
public sealed class GitHubIssueSync
{
    private readonly HttpClient _http;
    private readonly BugFabOptions _options;
    private readonly ILogger<GitHubIssueSync> _logger;

    public GitHubIssueSync(
        HttpClient http,
        IOptions<BugFabOptions> options,
        ILogger<GitHubIssueSync> logger)
    {
        _http = http;
        _options = options.Value;
        _logger = logger;
    }

    public bool Enabled =>
        _options.GitHub.Enabled
        && !string.IsNullOrEmpty(_options.GitHub.PersonalAccessToken)
        && !string.IsNullOrEmpty(_options.GitHub.Repository);

    /// <summary>
    /// Create a GitHub issue mirroring the given report. Returns a tuple of
    /// (issueNumber, issueUrl) on success, or (null, null) on any failure
    /// (including disabled sync, network errors, or 4xx/5xx from GitHub).
    /// </summary>
    public async Task<(int? Number, string? Url)> CreateIssueAsync(
        StoredReport report,
        CancellationToken ct = default)
    {
        if (!Enabled) return (null, null);

        try
        {
            var body = new
            {
                title = $"[Bug] {report.Title}",
                body = BuildIssueBody(report),
                labels = new[] { "bug", $"severity:{report.Severity}" },
            };

            using var request = new HttpRequestMessage(
                HttpMethod.Post,
                $"{_options.GitHub.ApiBase}/repos/{_options.GitHub.Repository}/issues")
            {
                Content = JsonContent.Create(body),
            };
            ApplyAuthHeaders(request);

            using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning(
                    "GitHub issue creation failed for report {Id}: HTTP {Status}",
                    report.Id, (int)response.StatusCode);
                return (null, null);
            }

            var payload = await response.Content
                .ReadFromJsonAsync<JsonNode>(cancellationToken: ct)
                .ConfigureAwait(false);
            var number = payload?["number"]?.GetValue<int>();
            var url = payload?["html_url"]?.GetValue<string>();
            return (number, url);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex,
                "GitHub issue creation failed for report {Id}", report.Id);
            return (null, null);
        }
    }

    /// <summary>
    /// Sync a status change to the linked GitHub issue. <c>fixed</c> /
    /// <c>closed</c> closes the issue; <c>open</c> / <c>investigating</c>
    /// reopens it.
    /// </summary>
    public async Task SyncIssueStateAsync(int issueNumber, string status, CancellationToken ct = default)
    {
        if (!Enabled) return;

        var newState = status is "fixed" or "closed" ? "closed" : "open";
        try
        {
            var body = new { state = newState };
            using var request = new HttpRequestMessage(
                HttpMethod.Patch,
                $"{_options.GitHub.ApiBase}/repos/{_options.GitHub.Repository}/issues/{issueNumber}")
            {
                Content = JsonContent.Create(body),
            };
            ApplyAuthHeaders(request);

            using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning(
                    "GitHub issue state sync failed for issue #{Issue}: HTTP {Status}",
                    issueNumber, (int)response.StatusCode);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex,
                "GitHub issue state sync failed for issue #{Issue}", issueNumber);
        }
    }

    private void ApplyAuthHeaders(HttpRequestMessage request)
    {
        request.Headers.Authorization = new AuthenticationHeaderValue(
            "token", _options.GitHub.PersonalAccessToken);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.UserAgent.ParseAdd("bug-fab-aspnetcore/0.1");
    }

    private static string BuildIssueBody(StoredReport report)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"**Bug-Fab report:** `{report.Id}`");
        sb.AppendLine($"**Severity:** {report.Severity}");
        sb.AppendLine($"**Reported at:** {report.CreatedAt}");
        sb.AppendLine($"**Module:** {report.Module}");
        sb.AppendLine();
        if (!string.IsNullOrEmpty(report.Description))
        {
            sb.AppendLine("### Description");
            sb.AppendLine(report.Description);
            sb.AppendLine();
        }
        if (!string.IsNullOrEmpty(report.ExpectedBehavior))
        {
            sb.AppendLine("### Expected behavior");
            sb.AppendLine(report.ExpectedBehavior);
        }
        return sb.ToString();
    }
}
