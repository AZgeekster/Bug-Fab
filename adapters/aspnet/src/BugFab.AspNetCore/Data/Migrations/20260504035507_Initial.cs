using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace BugFab.AspNetCore.Data.Migrations
{
    /// <inheritdoc />
    public partial class Initial : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "bug_fab_bug_reports",
                columns: table => new
                {
                    id = table.Column<string>(type: "TEXT", maxLength: 64, nullable: false),
                    id_sequence = table.Column<long>(type: "INTEGER", nullable: false),
                    received_at = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
                    protocol_version = table.Column<string>(type: "TEXT", maxLength: 16, nullable: false),
                    title = table.Column<string>(type: "TEXT", maxLength: 200, nullable: false),
                    description = table.Column<string>(type: "TEXT", nullable: false),
                    severity = table.Column<string>(type: "TEXT", maxLength: 32, nullable: true),
                    status = table.Column<string>(type: "TEXT", maxLength: 32, nullable: false),
                    environment = table.Column<string>(type: "TEXT", maxLength: 64, nullable: true),
                    app_name = table.Column<string>(type: "TEXT", maxLength: 128, nullable: true),
                    app_version = table.Column<string>(type: "TEXT", maxLength: 64, nullable: true),
                    reporter = table.Column<string>(type: "TEXT", maxLength: 512, nullable: true),
                    page_url = table.Column<string>(type: "TEXT", maxLength: 2048, nullable: true),
                    module = table.Column<string>(type: "TEXT", maxLength: 128, nullable: true),
                    user_agent_server = table.Column<string>(type: "TEXT", maxLength: 512, nullable: true),
                    user_agent_client = table.Column<string>(type: "TEXT", maxLength: 512, nullable: true),
                    metadata_json = table.Column<string>(type: "TEXT", nullable: false),
                    screenshot_path = table.Column<string>(type: "TEXT", maxLength: 1024, nullable: false),
                    github_issue_url = table.Column<string>(type: "TEXT", maxLength: 512, nullable: true),
                    github_issue_number = table.Column<int>(type: "INTEGER", nullable: true),
                    archived_at = table.Column<DateTimeOffset>(type: "TEXT", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_bug_fab_bug_reports", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "bug_fab_bug_report_lifecycle",
                columns: table => new
                {
                    id = table.Column<int>(type: "INTEGER", nullable: false)
                        .Annotation("Sqlite:Autoincrement", true),
                    bug_report_id = table.Column<string>(type: "TEXT", maxLength: 64, nullable: false),
                    action = table.Column<string>(type: "TEXT", maxLength: 32, nullable: false),
                    by = table.Column<string>(type: "TEXT", maxLength: 256, nullable: true),
                    at = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
                    status = table.Column<string>(type: "TEXT", maxLength: 32, nullable: true),
                    fix_commit = table.Column<string>(type: "TEXT", maxLength: 256, nullable: true),
                    fix_description = table.Column<string>(type: "TEXT", nullable: true),
                    metadata_json = table.Column<string>(type: "TEXT", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_bug_fab_bug_report_lifecycle", x => x.id);
                    table.ForeignKey(
                        name: "FK_bug_fab_bug_report_lifecycle_bug_fab_bug_reports_bug_report_id",
                        column: x => x.bug_report_id,
                        principalTable: "bug_fab_bug_reports",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_lifecycle_report_id",
                table: "bug_fab_bug_report_lifecycle",
                column: "bug_report_id");

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_archived_at",
                table: "bug_fab_bug_reports",
                column: "archived_at");

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_environment",
                table: "bug_fab_bug_reports",
                column: "environment");

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_received_at",
                table: "bug_fab_bug_reports",
                column: "received_at");

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_severity",
                table: "bug_fab_bug_reports",
                column: "severity");

            migrationBuilder.CreateIndex(
                name: "idx_bug_fab_status",
                table: "bug_fab_bug_reports",
                column: "status");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "bug_fab_bug_report_lifecycle");

            migrationBuilder.DropTable(
                name: "bug_fab_bug_reports");
        }
    }
}
