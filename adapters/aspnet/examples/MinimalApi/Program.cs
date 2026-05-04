// Minimal Bug-Fab consumer — ~15 lines of glue, plus the SQLite wiring.
//
// Run:   dotnet run --project examples/MinimalApi
// Open:  http://localhost:5000/bug-fab/
// Submit a report by POST'ing multipart/form-data to /bug-fab/bug-reports.

using BugFab.AspNetCore;
using BugFab.AspNetCore.Data;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddBugFab(builder.Configuration);

builder.Services.AddDbContext<BugFabDbContext>(opts =>
    opts.UseSqlite("Data Source=bug-fab.db"));

var app = builder.Build();

// Apply migrations on startup so the example "just runs".
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<BugFabDbContext>();
    db.Database.EnsureCreated();
}

app.UseBugFab();

app.MapGet("/", () => Results.Content(
    "<html><body><h1>Bug-Fab MinimalApi example</h1>" +
    "<p>Open <a href=\"/bug-fab/\">/bug-fab/</a> for the viewer.</p>" +
    "</body></html>", "text/html"));

app.Run();
