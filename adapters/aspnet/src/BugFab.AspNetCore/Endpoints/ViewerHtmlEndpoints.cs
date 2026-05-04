using System.Net;
using System.Text;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace BugFab.AspNetCore.Endpoints;

/// <summary>
/// HTML viewer pages (list + detail).
/// </summary>
/// <remarks>
/// <para>
/// The HTML pages use a self-contained inline template — they don't require
/// the host application to register Razor view-rendering services. This keeps
/// <c>BugFab.AspNetCore</c> drop-in for any Minimal API host (including those
/// without MVC).
/// </para>
/// <para>
/// Consumers wanting pixel-parity with the upstream Python reference's Jinja2
/// templates (or wanting to apply a corporate theme) can disable these pages
/// via <see cref="BugFabExtensions.MapBugFabApi"/> and serve their own.
/// </para>
/// </remarks>
public static class ViewerHtmlEndpoints
{
    public static void Map(IEndpointRouteBuilder app, BugFabOptions options)
    {
        app.MapGet("/", async (
            HttpContext http,
            IStorage storage,
            CancellationToken ct) =>
        {
            var (items, total) = await storage.ListReportsAsync(new ReportFilters(), 1, 100, ct)
                .ConfigureAwait(false);
            var stats = await storage.ComputeStatsAsync(ct).ConfigureAwait(false);
            var html = RenderListHtml(items, total, stats);
            return Results.Content(html, "text/html; charset=utf-8");
        }).WithName("BugFab_HtmlList");

        app.MapGet("/{id:regex(^bug-[A-Za-z]?\\d{{1,12}}$)}", async (
            string id,
            IStorage storage,
            CancellationToken ct) =>
        {
            var report = await storage.GetReportAsync(id, ct).ConfigureAwait(false);
            if (report is null)
            {
                return Results.Content(RenderNotFoundHtml(id), "text/html; charset=utf-8",
                    statusCode: StatusCodes.Status404NotFound);
            }
            return Results.Content(RenderDetailHtml(report), "text/html; charset=utf-8");
        }).WithName("BugFab_HtmlDetail");
    }

    private static string RenderListHtml(
        IReadOnlyList<StoredReportSummary> items,
        int total,
        IReadOnlyDictionary<string, int> stats)
    {
        var sb = new StringBuilder();
        AppendHead(sb, "Bug Reports — Bug-Fab");
        sb.AppendLine("<body><main class=\"bug-fab-viewer\">");
        sb.AppendLine("<h1>Bug Reports</h1>");
        sb.AppendLine("<section class=\"stats\">");
        foreach (var key in new[] { "open", "investigating", "fixed", "closed", "total" })
        {
            stats.TryGetValue(key, out var v);
            sb.AppendLine($"  <div class=\"stat\"><span class=\"label\">{H(key)}</span><span class=\"value\">{v}</span></div>");
        }
        sb.AppendLine("</section>");
        sb.AppendLine($"<p class=\"count\">{total} reports total</p>");
        sb.AppendLine("<table class=\"reports\">");
        sb.AppendLine("<thead><tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Module</th><th>Created</th></tr></thead>");
        sb.AppendLine("<tbody>");
        foreach (var item in items)
        {
            sb.AppendLine("<tr>");
            sb.AppendLine($"  <td><a href=\"./{H(item.Id)}\">{H(item.Id)}</a></td>");
            sb.AppendLine($"  <td>{H(item.Title)}</td>");
            sb.AppendLine($"  <td>{H(item.Severity)}</td>");
            sb.AppendLine($"  <td>{H(item.Status)}</td>");
            sb.AppendLine($"  <td>{H(item.Module)}</td>");
            sb.AppendLine($"  <td>{H(item.CreatedAt)}</td>");
            sb.AppendLine("</tr>");
        }
        sb.AppendLine("</tbody></table>");
        sb.AppendLine("</main></body></html>");
        return sb.ToString();
    }

    private static string RenderDetailHtml(StoredReport report)
    {
        var sb = new StringBuilder();
        AppendHead(sb, $"{report.Id} — {report.Title}");
        sb.AppendLine("<body><main class=\"bug-fab-viewer\">");
        sb.AppendLine($"<p><a href=\"./\">&larr; Back to list</a></p>");
        sb.AppendLine($"<h1>{H(report.Id)}: {H(report.Title)}</h1>");
        sb.AppendLine("<dl class=\"meta\">");
        AppendDt(sb, "Status", report.Status);
        AppendDt(sb, "Severity", report.Severity);
        AppendDt(sb, "Report type", report.ReportType);
        AppendDt(sb, "Module", report.Module);
        AppendDt(sb, "Environment", report.Environment);
        AppendDt(sb, "Created", report.CreatedAt);
        AppendDt(sb, "Updated", report.UpdatedAt);
        AppendDt(sb, "Protocol version", report.ProtocolVersion);
        if (!string.IsNullOrEmpty(report.GitHubIssueUrl))
        {
            sb.AppendLine($"<dt>GitHub issue</dt><dd><a href=\"{H(report.GitHubIssueUrl)}\">{H(report.GitHubIssueUrl)}</a></dd>");
        }
        sb.AppendLine("</dl>");

        if (!string.IsNullOrEmpty(report.Description))
        {
            sb.AppendLine("<h2>Description</h2>");
            sb.AppendLine($"<p>{H(report.Description)}</p>");
        }
        if (!string.IsNullOrEmpty(report.ExpectedBehavior))
        {
            sb.AppendLine("<h2>Expected behavior</h2>");
            sb.AppendLine($"<p>{H(report.ExpectedBehavior)}</p>");
        }

        if (report.HasScreenshot)
        {
            sb.AppendLine("<h2>Screenshot</h2>");
            sb.AppendLine($"<img src=\"./reports/{H(report.Id)}/screenshot\" alt=\"Screenshot\" style=\"max-width:100%\" />");
        }

        sb.AppendLine("<h2>Lifecycle</h2><ol class=\"lifecycle\">");
        foreach (var entry in report.Lifecycle)
        {
            var action = entry["action"]?.GetValue<string>() ?? string.Empty;
            var by = entry["by"]?.GetValue<string>() ?? string.Empty;
            var at = entry["at"]?.GetValue<string>() ?? string.Empty;
            sb.AppendLine($"<li><strong>{H(action)}</strong> by {H(by)} at {H(at)}</li>");
        }
        sb.AppendLine("</ol>");

        sb.AppendLine("</main></body></html>");
        return sb.ToString();
    }

    private static string RenderNotFoundHtml(string id)
    {
        var sb = new StringBuilder();
        AppendHead(sb, "Not found — Bug-Fab");
        sb.AppendLine($"<body><main class=\"bug-fab-viewer\"><h1>404 — Bug report not found</h1>");
        sb.AppendLine($"<p>No report with id <code>{H(id)}</code>.</p>");
        sb.AppendLine($"<p><a href=\"./\">Back to list</a></p>");
        sb.AppendLine("</main></body></html>");
        return sb.ToString();
    }

    private static void AppendDt(StringBuilder sb, string label, string value)
    {
        if (!string.IsNullOrEmpty(value))
        {
            sb.AppendLine($"<dt>{H(label)}</dt><dd>{H(value)}</dd>");
        }
    }

    private static void AppendHead(StringBuilder sb, string title)
    {
        sb.AppendLine("<!doctype html><html lang=\"en\"><head>");
        sb.AppendLine("<meta charset=\"utf-8\" />");
        sb.AppendLine("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />");
        sb.AppendLine($"<title>{H(title)}</title>");
        sb.AppendLine("<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.5}");
        sb.AppendLine(".stats{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}");
        sb.AppendLine(".stat{padding:.5rem 1rem;background:#f4f4f5;border-radius:.5rem}");
        sb.AppendLine(".stat .label{display:block;font-size:.75rem;text-transform:uppercase;color:#71717a}");
        sb.AppendLine(".stat .value{display:block;font-size:1.5rem;font-weight:600}");
        sb.AppendLine("table.reports{width:100%;border-collapse:collapse}");
        sb.AppendLine("table.reports th, table.reports td{padding:.5rem;border-bottom:1px solid #e4e4e7;text-align:left}");
        sb.AppendLine("dl.meta dt{font-weight:600;margin-top:.5rem}");
        sb.AppendLine("dl.meta dd{margin:0 0 .5rem 1rem}");
        sb.AppendLine("</style></head>");
    }

    /// <summary>HTML-escape a string for safe inclusion in attributes / text nodes.</summary>
    private static string H(string? raw) => WebUtility.HtmlEncode(raw ?? string.Empty);
}
