using BugFab.AspNetCore;
using BugFab.AspNetCore.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace BugFab.AspNetCore.Tests;

/// <summary>
/// Test host helpers for the Bug-Fab adapter.
/// </summary>
/// <remarks>
/// <para>
/// We deliberately do <strong>not</strong> use
/// <c>Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactory&lt;T&gt;</c> here.
/// The factory's bootstrapping is geared toward an existing
/// <c>Program.cs</c> entry point (<c>TEntryPoint</c>) and an MVC host; this
/// adapter is mounted as Minimal API endpoints on top of a freshly built
/// <see cref="WebApplication"/>, so the factory adds plumbing that doesn't
/// pay rent.
/// </para>
/// <para>
/// All tests in this project call <see cref="BuildApp"/> directly. It returns
/// a started-on-demand <see cref="WebApplication"/> with Bug-Fab wired,
/// EF Core's in-memory provider configured, and a unique storage directory
/// per test for filesystem isolation.
/// </para>
/// </remarks>
public static class TestApp
{
    /// <summary>
    /// Build a small <see cref="WebApplication"/> wired with Bug-Fab. The
    /// caller is responsible for <c>StartAsync</c> / <c>DisposeAsync</c>.
    /// </summary>
    public static WebApplication BuildApp(string storageDirectory)
        => BuildApp(storageDirectory, configure: null);

    /// <summary>
    /// Build a Bug-Fab test host with optional <see cref="BugFabOptions"/>
    /// overrides. Used by rate-limit tests to flip <c>RateLimit.Enabled</c>
    /// and tighten <c>MaxPerWindow</c> to a value the test can drive.
    /// </summary>
    public static WebApplication BuildApp(
        string storageDirectory,
        Action<BugFabOptions>? configure)
    {
        var builder = WebApplication.CreateBuilder();
        builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["BugFab:RoutePrefix"] = "/bug-fab",
            ["BugFab:StorageDirectory"] = storageDirectory,
            ["BugFab:UseEfCoreStorage"] = "true",
        });

        builder.Services.AddBugFab(builder.Configuration, configure);
        // The InMemory database name MUST be captured once per host so every
        // scope shares the same backing store. Inlining `Guid.NewGuid()` inside
        // the options callback would re-evaluate per resolution and silently
        // hand each scope a fresh, empty database — saves succeed, reads
        // return null.
        var databaseName = $"bug-fab-tests-{Guid.NewGuid():N}";
        builder.Services.AddDbContext<BugFabDbContext>(opts =>
            opts.UseInMemoryDatabase(databaseName));

        // Bind to an ephemeral loopback port so parallel xUnit collections
        // don't fight over Kestrel's default 5000. Each test gets its own
        // WebApplication, its own port, its own InMemory database.
        var app = builder.Build();
        app.Urls.Clear();
        app.Urls.Add("http://127.0.0.1:0");
        app.UseBugFab();
        return app;
    }
}

/// <summary>Tiny multipart/form-data builder for the intake tests.</summary>
public static class MultipartHelper
{
    public static MultipartFormDataContent BuildIntake(string metadataJson, byte[] screenshotBytes)
    {
        var form = new MultipartFormDataContent();
        form.Add(new StringContent(metadataJson), "metadata");

        var fileContent = new ByteArrayContent(screenshotBytes);
        fileContent.Headers.ContentType =
            new System.Net.Http.Headers.MediaTypeHeaderValue("image/png");
        form.Add(fileContent, "screenshot", "screenshot.png");
        return form;
    }

    /// <summary>
    /// Minimum-viable PNG: just the magic-byte signature. Enough to pass the
    /// magic-byte check; not a renderable image, but the adapter never decodes
    /// the bytes.
    /// </summary>
    public static readonly byte[] MinimalPng = new byte[]
    {
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        // empty IHDR-ish bytes (not a real image, just enough length)
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    };
}
