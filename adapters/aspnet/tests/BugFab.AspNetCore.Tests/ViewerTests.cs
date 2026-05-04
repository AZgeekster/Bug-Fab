using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Xunit;

namespace BugFab.AspNetCore.Tests;

/// <summary>
/// Viewer endpoint tests — list shape, detail round-trip, screenshot retrieval,
/// status updates, deletes.
/// </summary>
public sealed class ViewerTests
{
    private const string ValidMetadata =
        """
        {
          "protocol_version": "0.1",
          "title": "List smoke test",
          "client_ts": "2026-05-01T12:00:00Z",
          "severity": "medium",
          "context": {"url": "https://example.com/page", "module": "checkout"}
        }
        """;

    [Fact]
    public async Task List_endpoint_returns_paginated_json_with_snake_case_keys()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // Submit one report so the list has content.
        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        await client.PostAsync("/bug-fab/bug-reports", form);

        var response = await client.GetAsync("/bug-fab/reports");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("items").GetArrayLength().Should().BeGreaterThan(0);
        body.GetProperty("total").GetInt32().Should().BeGreaterThan(0);
        body.GetProperty("page_size").GetInt32().Should().Be(20);
        body.GetProperty("stats").GetProperty("open").GetInt32().Should().BeGreaterThan(0);

        // snake_case round-trip — the keys should NOT be camelCase.
        var raw = await response.Content.ReadAsStringAsync();
        raw.Should().Contain("\"page_size\"");
        raw.Should().NotContain("\"pageSize\"");
        raw.Should().Contain("\"has_screenshot\"");
        raw.Should().NotContain("\"hasScreenshot\"");
    }

    [Fact]
    public async Task Detail_endpoint_round_trips_metadata()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var submit = await client.PostAsync("/bug-fab/bug-reports", form);
        var submitBody = await submit.Content.ReadFromJsonAsync<JsonElement>();
        var id = submitBody.GetProperty("id").GetString();

        var response = await client.GetAsync($"/bug-fab/reports/{id}");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("id").GetString().Should().Be(id);
        body.GetProperty("title").GetString().Should().Be("List smoke test");
        body.GetProperty("severity").GetString().Should().Be("medium");
        body.GetProperty("status").GetString().Should().Be("open");
        body.GetProperty("protocol_version").GetString().Should().Be("0.1");
        body.GetProperty("lifecycle").GetArrayLength().Should().Be(1);
        body.GetProperty("server_user_agent").GetString().Should().NotBeNull();
    }

    [Fact]
    public async Task Detail_endpoint_with_invalid_id_returns_404()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        var response = await client.GetAsync("/bug-fab/reports/../etc/passwd");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Status_update_with_invalid_value_returns_422()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var submit = await client.PostAsync("/bug-fab/bug-reports", form);
        var id = (await submit.Content.ReadFromJsonAsync<JsonElement>())
            .GetProperty("id").GetString();

        var bad = new StringContent(
            """{"status": "wontfix"}""",
            Encoding.UTF8, "application/json");
        var response = await client.PutAsync($"/bug-fab/reports/{id}/status", bad);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task Status_update_with_valid_value_appends_lifecycle_entry()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var submit = await client.PostAsync("/bug-fab/bug-reports", form);
        var id = (await submit.Content.ReadFromJsonAsync<JsonElement>())
            .GetProperty("id").GetString();

        var update = new StringContent(
            """{"status": "fixed", "fix_commit": "abc123"}""",
            Encoding.UTF8, "application/json");
        var response = await client.PutAsync($"/bug-fab/reports/{id}/status", update);
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("status").GetString().Should().Be("fixed");
        body.GetProperty("lifecycle").GetArrayLength().Should().Be(2);
    }

    [Fact]
    public async Task Delete_returns_204_then_404_on_repeat()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var submit = await client.PostAsync("/bug-fab/bug-reports", form);
        var id = (await submit.Content.ReadFromJsonAsync<JsonElement>())
            .GetProperty("id").GetString();

        var first = await client.DeleteAsync($"/bug-fab/reports/{id}");
        first.StatusCode.Should().Be(HttpStatusCode.NoContent);

        var second = await client.DeleteAsync($"/bug-fab/reports/{id}");
        second.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    private static string NewStorageDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "bug-fab-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }
}
