using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using BugFab.AspNetCore;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace BugFab.AspNetCore.Tests;

/// <summary>
/// Bulk-action tests — close-fixed and archive-closed must be idempotent at
/// the per-row level (already-closed reports don't double-count).
/// </summary>
public sealed class BulkActionTests
{
    private const string ValidMetadata =
        """
        {
          "protocol_version": "0.1",
          "title": "Bulk smoke",
          "client_ts": "2026-05-01T12:00:00Z",
          "severity": "low"
        }
        """;

    [Fact]
    public async Task BulkCloseFixed_transitions_only_fixed_reports()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // Submit two reports.
        var idA = await SubmitOne(client);
        var idB = await SubmitOne(client);

        // Mark A as fixed.
        var update = new StringContent("""{"status": "fixed"}""", Encoding.UTF8, "application/json");
        await client.PutAsync($"/bug-fab/reports/{idA}/status", update);

        // First bulk-close: 1 row affected.
        var first = await client.PostAsync("/bug-fab/bulk-close-fixed", null);
        first.StatusCode.Should().Be(HttpStatusCode.OK);
        var firstBody = await first.Content.ReadFromJsonAsync<JsonElement>();
        firstBody.GetProperty("closed").GetInt32().Should().Be(1);

        // Second bulk-close: 0 rows affected (idempotent).
        var second = await client.PostAsync("/bug-fab/bulk-close-fixed", null);
        var secondBody = await second.Content.ReadFromJsonAsync<JsonElement>();
        secondBody.GetProperty("closed").GetInt32().Should().Be(0);
    }

    [Fact]
    public async Task BulkArchiveClosed_archives_only_closed_reports()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        var idA = await SubmitOne(client);
        await client.PutAsync($"/bug-fab/reports/{idA}/status",
            new StringContent("""{"status": "closed"}""", Encoding.UTF8, "application/json"));

        var response = await client.PostAsync("/bug-fab/bulk-archive-closed", null);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("archived").GetInt32().Should().BeGreaterThanOrEqualTo(1);
    }

    [Fact]
    public async Task BulkActions_respect_can_bulk_permission_flag()
    {
        var dir = NewStorageDir();
        var builder = Microsoft.AspNetCore.Builder.WebApplication.CreateBuilder();
        builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["BugFab:RoutePrefix"] = "/bug-fab",
            ["BugFab:StorageDirectory"] = dir,
            ["BugFab:UseEfCoreStorage"] = "true",
            ["BugFab:ViewerPermissions:CanBulk"] = "false",
        });
        builder.Services.AddBugFab(builder.Configuration);
        builder.Services.AddDbContext<BugFab.AspNetCore.Data.BugFabDbContext>(opts =>
            opts.UseInMemoryDatabase($"bug-fab-tests-{Guid.NewGuid()}"));

        await using var app = builder.Build();
        app.UseBugFab();
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        var response = await client.PostAsync("/bug-fab/bulk-close-fixed", null);
        response.StatusCode.Should().Be(HttpStatusCode.Forbidden);
    }

    private static async Task<string> SubmitOne(HttpClient client)
    {
        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        return body.GetProperty("id").GetString()!;
    }

    private static string NewStorageDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "bug-fab-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }
}
