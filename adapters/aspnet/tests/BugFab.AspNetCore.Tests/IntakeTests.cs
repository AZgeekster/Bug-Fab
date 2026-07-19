using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using BugFab.AspNetCore.Validation;
using FluentAssertions;
using Xunit;

namespace BugFab.AspNetCore.Tests;

/// <summary>
/// Intake endpoint tests — covers severity rejection, PNG magic bytes, the
/// protocol-version contract, and the size-limit branches.
/// </summary>
public sealed class IntakeTests
{
    private const string ValidMetadata =
        """
        {
          "protocol_version": "0.1",
          "title": "Smoke test",
          "client_ts": "2026-05-01T12:00:00Z",
          "report_type": "bug",
          "severity": "high",
          "description": "Something broke.",
          "context": {"url": "https://example.com/page"}
        }
        """;

    [Fact]
    public async Task Submit_with_valid_payload_returns_201()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("id").GetString().Should().StartWith("bug-");
        body.GetProperty("received_at").GetString().Should().NotBeNullOrEmpty();
        body.GetProperty("stored_at").GetString().Should().StartWith("bug-fab://");
    }

    [Fact]
    public async Task Submit_with_invalid_severity_returns_422()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        var bad = ValidMetadata.Replace("\"high\"", "\"urgent\"");
        using var form = MultipartHelper.BuildIntake(bad, MultipartHelper.MinimalPng);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("schema_error");
    }

    [Fact]
    public async Task Submit_with_unknown_protocol_version_returns_400()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        var bad = ValidMetadata.Replace("\"0.1\"", "\"99.9\"");
        using var form = MultipartHelper.BuildIntake(bad, MultipartHelper.MinimalPng);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("unsupported_protocol_version");
    }

    [Fact]
    public async Task Submit_with_non_png_screenshot_returns_415()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // JPEG magic bytes — not PNG.
        var jpegBytes = new byte[] { 0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46 };
        using var form = MultipartHelper.BuildIntake(ValidMetadata, jpegBytes);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.UnsupportedMediaType);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("unsupported_media_type");
    }

    [Fact]
    public async Task Submit_without_screenshot_returns_400()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        using var form = new MultipartFormDataContent();
        form.Add(new StringContent(ValidMetadata), "metadata");
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Submit_with_oversize_screenshot_returns_413()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // Build a 12 MiB blob — exceeds the 10 MiB default cap.
        var huge = new byte[12 * 1024 * 1024];
        Buffer.BlockCopy(MultipartHelper.MinimalPng, 0, huge, 0, MultipartHelper.MinimalPng.Length);

        using var form = MultipartHelper.BuildIntake(ValidMetadata, huge);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.RequestEntityTooLarge);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("payload_too_large");
        body.GetProperty("limit_bytes").GetInt64().Should().Be(10 * 1024 * 1024);
    }

    [Fact]
    public async Task Submit_above_rate_limit_returns_429_with_envelope()
    {
        // Configure a tight per-IP window — 2 permits per 60 s — so the third
        // request inside the same window is guaranteed to trip the limiter.
        // Verifies that the documented BugFab:RateLimit config wires through
        // to Microsoft.AspNetCore.RateLimiting and the OnRejected callback
        // emits the protocol's {error, detail, retry_after_seconds} envelope
        // rather than ASP.NET Core's default text/plain "Too Many Requests".
        await using var app = TestApp.BuildApp(NewStorageDir(), opts =>
        {
            opts.RateLimit.Enabled = true;
            opts.RateLimit.MaxPerWindow = 2;
            opts.RateLimit.WindowSeconds = 60;
        });
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // Two permitted requests fill the window; the third must reject.
        for (var i = 0; i < 2; i++)
        {
            using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
            var ok = await client.PostAsync("/bug-fab/bug-reports", form);
            ok.StatusCode.Should().Be(HttpStatusCode.Created);
        }

        using var third = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        var rejected = await client.PostAsync("/bug-fab/bug-reports", third);

        rejected.StatusCode.Should().Be(HttpStatusCode.TooManyRequests);
        var body = await rejected.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("rate_limited");
        body.GetProperty("detail").GetString().Should().Contain("Rate limit exceeded");
        body.GetProperty("retry_after_seconds").GetInt32().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Submit_spoofed_forwarded_for_cannot_evade_rate_limit()
    {
        // X-Forwarded-For is client-controlled. If the limiter partitioned on
        // it, rotating the header would mint a fresh bucket per request and
        // the limit would never trip. The partition key must come from the
        // connection's resolved address (rewritten only by the consumer's
        // ForwardedHeadersMiddleware, never by the raw header).
        await using var app = TestApp.BuildApp(NewStorageDir(), opts =>
        {
            opts.RateLimit.Enabled = true;
            opts.RateLimit.MaxPerWindow = 2;
            opts.RateLimit.WindowSeconds = 60;
        });
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        for (var i = 0; i < 2; i++)
        {
            using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
            using var req = new HttpRequestMessage(HttpMethod.Post, "/bug-fab/bug-reports")
            {
                Content = form,
            };
            req.Headers.Add("X-Forwarded-For", $"203.0.113.{i}");
            var ok = await client.SendAsync(req);
            ok.StatusCode.Should().Be(HttpStatusCode.Created);
        }

        using var thirdForm = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
        using var thirdReq = new HttpRequestMessage(HttpMethod.Post, "/bug-fab/bug-reports")
        {
            Content = thirdForm,
        };
        thirdReq.Headers.Add("X-Forwarded-For", "203.0.113.99");
        var rejected = await client.SendAsync(thirdReq);

        rejected.StatusCode.Should().Be(HttpStatusCode.TooManyRequests);
        var body = await rejected.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("rate_limited");
    }

    [Fact]
    public void Magic_byte_check_rejects_short_payload()
    {
        PayloadValidator.IsValidPng(new byte[] { 0x89, 0x50 }).Should().BeFalse();
        PayloadValidator.IsValidPng(MultipartHelper.MinimalPng).Should().BeTrue();
    }

    [Fact]
    public void Reporter_field_length_cap_enforced()
    {
        var longString = new string('a', 257);
        var json =
            $$"""
            {
              "protocol_version": "0.1",
              "title": "x",
              "client_ts": "2026-05-01T12:00:00Z",
              "reporter": {"name": "{{longString}}"}
            }
            """;
        var result = PayloadValidator.ValidateMetadata(json);
        result.IsValid.Should().BeFalse();
        result.Failures.Should().Contain(f => f.Field == "reporter.name");
    }

    [Fact]
    public async Task Submit_with_wrong_typed_scalar_returns_422_not_500()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        // A number where a string belongs used to escape as an unhandled
        // InvalidOperationException from GetValue<string>() -> 500.
        var bad = ValidMetadata.Replace("\"Smoke test\"", "123");
        using var form = MultipartHelper.BuildIntake(bad, MultipartHelper.MinimalPng);
        var response = await client.PostAsync("/bug-fab/bug-reports", form);

        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        body.GetProperty("error").GetString().Should().Be("schema_error");
    }

    [Theory]
    [InlineData("protocol_version", "1")]
    [InlineData("title", "123")]
    [InlineData("client_ts", "false")]
    [InlineData("report_type", "[]")]
    [InlineData("severity", "{\"level\": 2}")]
    [InlineData("reporter", "{\"name\": 42}")]
    public void ValidateMetadata_rejects_wrong_typed_scalars_without_throwing(
        string field, string badValue)
    {
        var fields = new Dictionary<string, string>
        {
            ["protocol_version"] = "\"0.1\"",
            ["title"] = "\"x\"",
            ["client_ts"] = "\"2026-05-01T12:00:00Z\"",
        };
        fields[field] = badValue;
        var json = "{" + string.Join(",", fields.Select(kv => $"\"{kv.Key}\": {kv.Value}")) + "}";

        var result = PayloadValidator.ValidateMetadata(json);
        result.IsValid.Should().BeFalse();
        result.Failures.Should().Contain(f => f.Message.Contains("must be a string"));
    }

    [Fact]
    public void ValidateStatusUpdate_rejects_wrong_typed_status_without_throwing()
    {
        var body = System.Text.Json.Nodes.JsonNode.Parse(
            """{"status": 5}""")!.AsObject();
        var result = PayloadValidator.ValidateStatusUpdate(body);
        result.IsValid.Should().BeFalse();
        result.Failure!.Message.Should().Contain("must be a string");
    }

    [Fact]
    public async Task Ids_are_not_reused_after_delete()
    {
        await using var app = TestApp.BuildApp(NewStorageDir());
        await app.StartAsync();
        using var client = new HttpClient { BaseAddress = new Uri(app.Urls.First()) };

        async Task<string> Submit()
        {
            using var form = MultipartHelper.BuildIntake(ValidMetadata, MultipartHelper.MinimalPng);
            var res = await client.PostAsync("/bug-fab/bug-reports", form);
            res.StatusCode.Should().Be(HttpStatusCode.Created);
            return (await res.Content.ReadFromJsonAsync<JsonElement>())
                .GetProperty("id").GetString()!;
        }

        var ids = new[] { await Submit(), await Submit(), await Submit() };
        ids.Should().Equal("bug-001", "bug-002", "bug-003");

        // Delete the highest-numbered report. MAX(IdSequence)+1 would then
        // recompute 3 and reissue bug-003; the counter row must mint bug-004.
        var del = await client.DeleteAsync($"/bug-fab/reports/{ids[^1]}");
        del.StatusCode.Should().Be(HttpStatusCode.NoContent);

        (await Submit()).Should().Be("bug-004");
    }

    private static string NewStorageDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "bug-fab-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }
}
