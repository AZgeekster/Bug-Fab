using System.Threading.RateLimiting;
using BugFab.AspNetCore.Endpoints;
using BugFab.AspNetCore.GitHub;
using BugFab.AspNetCore.Storage;
using BugFab.AspNetCore.Validation;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace BugFab.AspNetCore;

/// <summary>
/// DI registration and route mounting for the Bug-Fab adapter.
/// </summary>
/// <remarks>
/// Two extension methods are public:
/// <list type="bullet">
///   <item><see cref="AddBugFab"/> — registers <see cref="BugFabOptions"/>,
///   the configured <see cref="IStorage"/>, and the GitHub sync client.</item>
///   <item><see cref="UseBugFab(WebApplication)"/> — mounts the eight HTTP
///   endpoints plus the two HTML viewer pages under
///   <see cref="BugFabOptions.RoutePrefix"/>.</item>
/// </list>
/// </remarks>
public static class BugFabExtensions
{
    /// <summary>
    /// Name of the rate-limiting policy applied to the intake endpoint when
    /// <see cref="RateLimitOptions.Enabled"/> is true. Exposed publicly so
    /// consumers can re-use or replace it.
    /// </summary>
    public const string IntakeRateLimitPolicy = "bug-fab-intake";

    /// <summary>
    /// Registers Bug-Fab services in the DI container.
    /// </summary>
    /// <param name="services">The host's service collection.</param>
    /// <param name="configuration">
    /// Configuration root. The <c>"BugFab"</c> section is bound to
    /// <see cref="BugFabOptions"/>; missing is fine — defaults apply.
    /// </param>
    /// <param name="configure">
    /// Optional callback to override values after configuration binding.
    /// Run last, so it wins on conflict.
    /// </param>
    public static IServiceCollection AddBugFab(
        this IServiceCollection services,
        IConfiguration configuration,
        Action<BugFabOptions>? configure = null)
    {
        ArgumentNullException.ThrowIfNull(services);
        ArgumentNullException.ThrowIfNull(configuration);

        var section = configuration.GetSection("BugFab");
        services.Configure<BugFabOptions>(section);
        if (configure is not null)
        {
            services.PostConfigure(configure);
        }

        // Storage selection happens at runtime via factory because the choice
        // depends on a value bound from configuration.
        services.TryAddSingleton<IStorage>(sp =>
        {
            var options = sp.GetRequiredService<
                Microsoft.Extensions.Options.IOptions<BugFabOptions>>().Value;

            ValidateOptions(options);

            if (options.UseEfCoreStorage)
            {
                return new EfCoreStorage(sp, options);
            }
            return new FileStorage(options);
        });

        services.AddHttpClient<GitHubIssueSync>();
        services.TryAddSingleton<GitHubIssueSync>();

        // Bind a snapshot of BugFabOptions so we can decide whether to wire the
        // rate-limiter policy. The post-configure hook above has not run yet,
        // but the configuration-bound values are already in the section. The
        // rate-limit policy callback below also reads IOptions<BugFabOptions>
        // at request-time so any post-configure overrides are honored when
        // the partition key / limits are computed.
        var optionsSnapshot = new BugFabOptions();
        section.Bind(optionsSnapshot);
        configure?.Invoke(optionsSnapshot);

        if (optionsSnapshot.RateLimit.Enabled)
        {
            AddIntakeRateLimiter(services);
        }

        return services;
    }

    /// <summary>
    /// Register the per-IP fixed-window rate limiter that gates the intake
    /// endpoint. Idempotent on the policy name — calling AddBugFab twice (or
    /// alongside a consumer's own AddRateLimiter call) merges configuration
    /// rather than throwing.
    /// </summary>
    private static void AddIntakeRateLimiter(IServiceCollection services)
    {
        services.AddRateLimiter(limiterOptions =>
        {
            // The 429 envelope MUST match the wire protocol's
            // {error, detail, retry_after_seconds} shape — NOT ASP.NET Core's
            // default text/plain "Too Many Requests" response. Conformance
            // tests verify the exact key set.
            limiterOptions.OnRejected = async (context, ct) =>
            {
                var bugFab = context.HttpContext.RequestServices
                    .GetRequiredService<IOptions<BugFabOptions>>().Value;

                var retryAfter = bugFab.RateLimit.WindowSeconds;
                if (context.Lease.TryGetMetadata(MetadataName.RetryAfter, out var retry))
                {
                    retryAfter = (int)Math.Ceiling(retry.TotalSeconds);
                }

                context.HttpContext.Response.StatusCode =
                    StatusCodes.Status429TooManyRequests;
                context.HttpContext.Response.Headers["Retry-After"] =
                    retryAfter.ToString(System.Globalization.CultureInfo.InvariantCulture);

                var envelope = new ErrorEnvelope
                {
                    Error = "rate_limited",
                    Detail =
                        $"Rate limit exceeded: max {bugFab.RateLimit.MaxPerWindow} reports " +
                        $"per {bugFab.RateLimit.WindowSeconds} seconds",
                    RetryAfterSeconds = retryAfter,
                };

                await context.HttpContext.Response
                    .WriteAsJsonAsync(envelope, bugFab.JsonOptions, cancellationToken: ct)
                    .ConfigureAwait(false);
            };

            limiterOptions.AddPolicy(IntakeRateLimitPolicy, httpContext =>
            {
                var bugFab = httpContext.RequestServices
                    .GetRequiredService<IOptions<BugFabOptions>>().Value;

                // Honor X-Forwarded-For first hop so deployments behind a
                // reverse proxy meter per-end-user. Falls back to the direct
                // peer address; "unknown" is a stable last resort so the
                // limiter still has a partition key.
                var partitionKey = ResolveClientIp(httpContext);

                return RateLimitPartition.GetFixedWindowLimiter(
                    partitionKey: partitionKey,
                    factory: _ => new FixedWindowRateLimiterOptions
                    {
                        PermitLimit = Math.Max(bugFab.RateLimit.MaxPerWindow, 1),
                        Window = TimeSpan.FromSeconds(
                            Math.Max(bugFab.RateLimit.WindowSeconds, 1)),
                        QueueLimit = 0,
                        QueueProcessingOrder = QueueProcessingOrder.OldestFirst,
                        AutoReplenishment = true,
                    });
            });
        });
    }

    private static string ResolveClientIp(HttpContext httpContext)
    {
        var forwarded = httpContext.Request.Headers["X-Forwarded-For"].ToString();
        if (!string.IsNullOrEmpty(forwarded))
        {
            var firstHop = forwarded.Split(',', 2)[0].Trim();
            if (!string.IsNullOrEmpty(firstHop))
            {
                return firstHop;
            }
        }
        return httpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown";
    }

    /// <summary>
    /// Mount all Bug-Fab endpoints under <see cref="BugFabOptions.RoutePrefix"/>.
    /// </summary>
    /// <returns>
    /// A <see cref="BugFabRouteGroup"/> exposing the intake and viewer route
    /// builders so consumers can apply per-group authorization policies.
    /// </returns>
    public static BugFabRouteGroup UseBugFab(this WebApplication app)
        => UseBugFab(app, configureGroups: null);

    /// <summary>
    /// Mount all Bug-Fab endpoints with a per-group configuration callback.
    /// </summary>
    public static BugFabRouteGroup UseBugFab(
        this WebApplication app,
        Action<BugFabRouteGroup>? configureGroups)
    {
        ArgumentNullException.ThrowIfNull(app);

        var options = app.Services.GetRequiredService<
            Microsoft.Extensions.Options.IOptions<BugFabOptions>>().Value;

        ValidateOptions(options);

        if (options.RateLimit.Enabled)
        {
            // The middleware must be in the pipeline before any endpoint with
            // RequireRateLimiting fires. Calling UseRateLimiter is idempotent
            // for our purposes: if a consumer already invoked it on the same
            // app, the second call adds a second middleware instance that's a
            // no-op for endpoints that don't opt in. Cheap, safe.
            app.UseRateLimiter();
        }

        var prefix = options.RoutePrefix.TrimEnd('/');
        var intake = app.MapGroup(prefix);
        var viewer = app.MapGroup(prefix);

        IntakeEndpoint.Map(intake, options);
        ListEndpoint.Map(viewer, options);
        DetailEndpoint.Map(viewer, options);
        ScreenshotEndpoint.Map(viewer, options);
        StatusEndpoint.Map(viewer, options);
        DeleteEndpoint.Map(viewer, options);
        BulkCloseEndpoint.Map(viewer, options);
        BulkArchiveEndpoint.Map(viewer, options);

        ViewerHtmlEndpoints.Map(viewer, options);

        if (options.RateLimit.Enabled)
        {
            intake.RequireRateLimiting(IntakeRateLimitPolicy);
        }

        var group = new BugFabRouteGroup(intake, viewer);
        configureGroups?.Invoke(group);
        return group;
    }

    /// <summary>
    /// Mount only the JSON API endpoints — skip the HTML viewer pages. Useful
    /// for headless deployments (mobile-only consumers, remote collectors).
    /// </summary>
    public static BugFabRouteGroup MapBugFabApi(this WebApplication app)
    {
        ArgumentNullException.ThrowIfNull(app);

        var options = app.Services.GetRequiredService<
            Microsoft.Extensions.Options.IOptions<BugFabOptions>>().Value;

        ValidateOptions(options);

        if (options.RateLimit.Enabled)
        {
            app.UseRateLimiter();
        }

        var prefix = options.RoutePrefix.TrimEnd('/');
        var intake = app.MapGroup(prefix);
        var viewer = app.MapGroup(prefix);

        IntakeEndpoint.Map(intake, options);
        ListEndpoint.Map(viewer, options);
        DetailEndpoint.Map(viewer, options);
        ScreenshotEndpoint.Map(viewer, options);
        StatusEndpoint.Map(viewer, options);
        DeleteEndpoint.Map(viewer, options);
        BulkCloseEndpoint.Map(viewer, options);
        BulkArchiveEndpoint.Map(viewer, options);

        if (options.RateLimit.Enabled)
        {
            intake.RequireRateLimiting(IntakeRateLimitPolicy);
        }

        return new BugFabRouteGroup(intake, viewer);
    }

    private static void ValidateOptions(BugFabOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.RoutePrefix) ||
            options.RoutePrefix == "/")
        {
            throw new ArgumentException(
                "BugFabOptions.RoutePrefix must be a non-empty, non-root path " +
                "(e.g. \"/bug-fab\"). The viewer's HTML list mounts at this " +
                "prefix and would collide with the host application at \"/\".",
                nameof(options));
        }
        if (!options.RoutePrefix.StartsWith('/'))
        {
            throw new ArgumentException(
                "BugFabOptions.RoutePrefix must start with '/'.",
                nameof(options));
        }
        if (options.MaxScreenshotBytes <= 0)
        {
            throw new ArgumentException(
                "BugFabOptions.MaxScreenshotBytes must be positive.",
                nameof(options));
        }
    }
}

/// <summary>
/// Returned by <see cref="BugFabExtensions.UseBugFab(WebApplication)"/> so
/// consumers can apply authorization policies separately to the intake and
/// viewer route groups.
/// </summary>
/// <param name="Intake">The <c>POST /bug-reports</c> route group.</param>
/// <param name="Viewer">
/// The viewer route group — list, detail, screenshot, status update, delete,
/// bulk operations.
/// </param>
public sealed record BugFabRouteGroup(
    RouteGroupBuilder Intake,
    RouteGroupBuilder Viewer)
{
    /// <summary>
    /// Apply <c>RequireAuthorization()</c> to both groups. Equivalent to the
    /// most common consumer pattern.
    /// </summary>
    public BugFabRouteGroup RequireAuthorization()
    {
        Intake.RequireAuthorization();
        Viewer.RequireAuthorization();
        return this;
    }
}
