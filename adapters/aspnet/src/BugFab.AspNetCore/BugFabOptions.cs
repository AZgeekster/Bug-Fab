using System.Text.Json;

namespace BugFab.AspNetCore;

/// <summary>
/// Configuration for the Bug-Fab adapter. Bind from <c>appsettings.json</c> §
/// <c>"BugFab"</c> via <see cref="BugFabExtensions.AddBugFab"/>.
/// </summary>
/// <remarks>
/// All options have sensible defaults. The two values consumers typically
/// override are <see cref="RoutePrefix"/> (where to mount the routes) and
/// <see cref="StorageDirectory"/> (where to write screenshot blobs even when
/// EF Core handles metadata).
/// </remarks>
public sealed class BugFabOptions
{
    /// <summary>
    /// Mount-point prefix for every Bug-Fab route. MUST be non-empty per the
    /// adapter authorship checklist — the viewer mounts an HTML list at this
    /// prefix's root, which would collide with the host app at <c>/</c>.
    /// </summary>
    public string RoutePrefix { get; set; } = "/bug-fab";

    /// <summary>
    /// Filesystem directory where screenshot PNG blobs land. Used by both
    /// <see cref="Storage.FileStorage"/> (full report tree) and
    /// <see cref="Storage.EfCoreStorage"/> (just the screenshot files; metadata
    /// in the database).
    /// </summary>
    public string StorageDirectory { get; set; } = "./var/bug-fab";

    /// <summary>Maximum screenshot size in bytes. Default 10 MiB.</summary>
    public long MaxScreenshotBytes { get; set; } = 10 * 1024 * 1024;

    /// <summary>Maximum metadata JSON size in bytes. Default 256 KiB.</summary>
    public long MaxMetadataBytes { get; set; } = 256 * 1024;

    /// <summary>
    /// When true (default), use <see cref="Storage.EfCoreStorage"/>. The
    /// consumer MUST register a <see cref="Data.BugFabDbContext"/> in DI when
    /// this flag is true.
    /// </summary>
    public bool UseEfCoreStorage { get; set; } = true;

    /// <summary>Per-route viewer permission flags.</summary>
    public ViewerPermissions ViewerPermissions { get; set; } = new();

    /// <summary>Optional GitHub Issues sync configuration.</summary>
    public GitHubOptions GitHub { get; set; } = new();

    /// <summary>Optional per-IP rate limit on intake submissions.</summary>
    public RateLimitOptions RateLimit { get; set; } = new();

    /// <summary>
    /// Opt-in flag for wiring ASP.NET Core's <c>IAntiforgery</c> middleware on
    /// the viewer's mutating endpoints (<c>PUT /reports/{id}/status</c>,
    /// <c>DELETE /reports/{id}</c>, <c>POST /bulk-close-fixed</c>,
    /// <c>POST /bulk-archive-closed</c>).
    /// </summary>
    /// <remarks>
    /// <para>
    /// <b>Default: false.</b> The intake endpoint always disables antiforgery
    /// because the JS bundle posts cross-origin from the host page. The viewer
    /// endpoints are NOT antiforgery-protected by default — the recommended
    /// pattern is to require host-app authentication on the viewer mount
    /// prefix as the line of defense (see README § "CSRF / Antiforgery").
    /// </para>
    /// <para>
    /// <b>v0.1 status: declared, NOT wired.</b> Setting this to <c>true</c>
    /// today has no effect — Bug-Fab v0.1 ships without an
    /// <see cref="Microsoft.AspNetCore.Antiforgery.IAntiforgery"/>
    /// integration. The flag is reserved so consumer config files written
    /// today don't need to change shape when v0.2 lands. v0.2's
    /// <c>AuthAdapter</c> work will wire <c>RequireAntiforgery()</c> on the
    /// viewer group when this flag is true.
    /// </para>
    /// <para>
    /// TODO(v0.2): wire <c>IAntiforgery.ValidateRequestAsync</c> on the viewer
    /// group when <see cref="EnableAntiforgeryOnViewer"/> is <c>true</c>. See
    /// <c>docs/ROADMAP.md</c> § "v0.2 Auth + CSRF".
    /// </para>
    /// </remarks>
    public bool EnableAntiforgeryOnViewer { get; set; } = false;

    /// <summary>
    /// Optional ID prefix written into <c>bug-{prefix}NNN</c>. Useful for
    /// multi-environment shared collectors (<c>bug-P038</c>, <c>bug-D012</c>).
    /// </summary>
    public string IdPrefix { get; set; } = string.Empty;

    /// <summary>
    /// JSON options used by every Bug-Fab endpoint. Defaults to snake_case
    /// per the wire protocol contract. Endpoint-level — does NOT modify the
    /// host application's global JSON options.
    /// </summary>
    public JsonSerializerOptions JsonOptions { get; set; } = CreateDefaultJsonOptions();

    internal static JsonSerializerOptions CreateDefaultJsonOptions()
        => new(JsonSerializerDefaults.Web)
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            DictionaryKeyPolicy = JsonNamingPolicy.SnakeCaseLower,
            PropertyNameCaseInsensitive = false,
        };
}

/// <summary>
/// Per-route viewer permissions. Each flag gates one endpoint group; routes
/// for disabled groups respond <c>403 Forbidden</c> without ever reaching the
/// storage layer.
/// </summary>
/// <remarks>
/// These flags are NOT a per-user check. Bug-Fab v0.1 has no auth abstraction;
/// per-user gating arrives in v0.2 with the <c>AuthAdapter</c> ABC.
/// </remarks>
public sealed class ViewerPermissions
{
    public bool CanEditStatus { get; set; } = true;
    public bool CanDelete { get; set; } = true;
    public bool CanBulk { get; set; } = true;
}

/// <summary>GitHub Issues sync configuration.</summary>
public sealed class GitHubOptions
{
    /// <summary>Enable best-effort sync of new reports to GitHub Issues.</summary>
    public bool Enabled { get; set; } = false;

    /// <summary>Repository in <c>"owner/name"</c> form.</summary>
    public string Repository { get; set; } = string.Empty;

    /// <summary>
    /// Personal access token. Read from configuration; secret managers (Azure
    /// Key Vault, AWS Secrets Manager, etc.) layer on top via the standard
    /// <c>IConfiguration</c> pipeline.
    /// </summary>
    public string PersonalAccessToken { get; set; } = string.Empty;

    /// <summary>Override the GitHub API base URL (mostly used by tests).</summary>
    public string ApiBase { get; set; } = "https://api.github.com";
}

/// <summary>Per-IP rate limit configuration. Disabled by default.</summary>
public sealed class RateLimitOptions
{
    public bool Enabled { get; set; } = false;
    public int MaxPerWindow { get; set; } = 30;
    public int WindowSeconds { get; set; } = 60;
}
