using BugFab.AspNetCore.Data.Entities;
using Microsoft.EntityFrameworkCore;

namespace BugFab.AspNetCore.Data;

/// <summary>
/// EF Core <see cref="DbContext"/> for the Bug-Fab tables.
/// </summary>
/// <remarks>
/// <para>
/// Tables are namespaced with the <c>bug_fab_</c> prefix to avoid colliding
/// with consumer schemas. Indexes match the upstream Python reference's
/// indexed columns exactly so query plans look comparable across stacks.
/// </para>
/// <para>
/// The <see cref="BugReport.Severity"/> CHECK constraint is intentionally
/// added at the application layer (in the storage write path), NOT as a SQL
/// CHECK — EF Core's cross-provider CHECK syntax is awkward for SQL Server vs
/// PostgreSQL, and the protocol's deprecated-values rule means existing rows
/// may carry deprecated severities the constraint would reject on migration.
/// </para>
/// </remarks>
public class BugFabDbContext : DbContext
{
    public BugFabDbContext(DbContextOptions<BugFabDbContext> options) : base(options) { }

    public DbSet<BugReport> BugReports => Set<BugReport>();
    public DbSet<BugReportLifecycle> Lifecycle => Set<BugReportLifecycle>();
    public DbSet<BugFabIdCounter> IdCounters => Set<BugFabIdCounter>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        modelBuilder.Entity<BugReport>(b =>
        {
            b.HasKey(x => x.Id);
            b.HasIndex(x => x.ReceivedAt).HasDatabaseName("idx_bug_fab_received_at");
            b.HasIndex(x => x.Status).HasDatabaseName("idx_bug_fab_status");
            b.HasIndex(x => x.Severity).HasDatabaseName("idx_bug_fab_severity");
            b.HasIndex(x => x.Environment).HasDatabaseName("idx_bug_fab_environment");
            b.HasIndex(x => x.ArchivedAt).HasDatabaseName("idx_bug_fab_archived_at");

            // ID generation: provider-managed identity column. SQL Server's
            // IDENTITY, PostgreSQL's IDENTITY/SERIAL, and SQLite's ROWID-backed
            // INTEGER PRIMARY KEY all give us race-free monotonic IDs without
            // a HiLo sequence dance. Consumers running provider-specific
            // tweaks (e.g., HiLo on SQL Server for high-throughput batch
            // inserts) can override this in their own DbContext.
            b.Property(x => x.IdSequence).ValueGeneratedOnAdd();

            b.HasMany(x => x.Lifecycle)
                .WithOne(x => x.BugReport)
                .HasForeignKey(x => x.BugReportId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        modelBuilder.Entity<BugReportLifecycle>(b =>
        {
            b.HasKey(x => x.Id);
            b.HasIndex(x => x.BugReportId).HasDatabaseName("idx_bug_fab_lifecycle_report_id");
        });
    }
}
