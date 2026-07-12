using System.Text.Json.Nodes;
using BugFab.AspNetCore;
using BugFab.AspNetCore.Data;
using BugFab.AspNetCore.Storage;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace BugFab.AspNetCore.Tests;

/// <summary>
/// Regression tests that run <see cref="EfCoreStorage"/> against the REAL
/// SQLite provider instead of InMemory. The two providers accept different
/// LINQ: InMemory happily orders by <c>DateTimeOffset</c>, while SQLite
/// throws <c>NotSupportedException</c> at query translation — a class of bug
/// the cross-stack conformance harness caught (GET /reports 500'd on the
/// SQLite-backed example while every InMemory test stayed green).
/// </summary>
public class EfCoreSqliteTests : IDisposable
{
    private readonly SqliteConnection _connection;
    private readonly ServiceProvider _services;
    private readonly string _storageDir;

    public EfCoreSqliteTests()
    {
        // A shared in-memory SQLite DB lives exactly as long as this open
        // connection; every DbContext scope reuses it.
        _connection = new SqliteConnection("DataSource=:memory:");
        _connection.Open();

        _storageDir = Path.Combine(
            Path.GetTempPath(), "bug-fab-sqlite-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_storageDir);

        var servicesBuilder = new ServiceCollection();
        servicesBuilder.AddDbContext<BugFabDbContext>(opts => opts.UseSqlite(_connection));
        _services = servicesBuilder.BuildServiceProvider();

        using var scope = _services.CreateScope();
        scope.ServiceProvider.GetRequiredService<BugFabDbContext>().Database.EnsureCreated();
    }

    public void Dispose()
    {
        _services.Dispose();
        _connection.Dispose();
        try
        {
            Directory.Delete(_storageDir, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    private EfCoreStorage NewStorage() =>
        new(_services, new BugFabOptions
        {
            UseEfCoreStorage = true,
            StorageDirectory = _storageDir,
        });

    private static JsonObject Metadata(string title) => new()
    {
        ["protocol_version"] = "0.1",
        ["title"] = title,
        ["client_ts"] = "2026-07-12T00:00:00Z",
        ["severity"] = "high",
    };

    private static readonly byte[] MinimalPng =
    {
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00,
    };

    [Fact]
    public async Task ListReports_works_on_the_sqlite_provider_newest_first()
    {
        var storage = NewStorage();
        var first = await storage.SaveReportAsync(Metadata("first"), MinimalPng);
        var second = await storage.SaveReportAsync(Metadata("second"), MinimalPng);

        // Throws NotSupportedException at query translation when the ORDER BY
        // uses an expression SQLite cannot represent (e.g. DateTimeOffset).
        var (items, total) = await storage.ListReportsAsync(new ReportFilters(), page: 1, pageSize: 20);

        total.Should().Be(2);
        items.Select(i => i.Id).Should().ContainInOrder(second, first);
    }
}
