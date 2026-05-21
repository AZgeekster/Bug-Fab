<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Bug-Fab persistence schema.
 *
 * Two tables:
 *  - bug_fab_reports     — one row per report. Hot columns denormalized for
 *                          filtering; full submitted JSON kept in
 *                          metadata_json for round-trip fidelity per
 *                          PROTOCOL.md § Storage round-trip notes.
 *  - bug_fab_lifecycle   — append-only audit log of state changes.
 *
 * Screenshots are stored as files on the configured screenshot disk; the
 * column screenshot_path holds the relative path.
 */
return new class extends Migration
{
    public function up(): void
    {
        $connection = config('bugfab.storages.eloquent.connection');

        Schema::connection($connection)->create('bug_fab_reports', function (Blueprint $table) {
            // bug-NNN / bug-{prefix}NNN format. 64 chars covers every plausible prefix.
            $table->string('id', 64)->primary();

            $table->timestamp('received_at')->index();
            $table->string('protocol_version', 16)->default('0.1');
            $table->string('title', 200);
            $table->text('description')->nullable();

            $table->string('severity', 16)->default('medium')->index();
            $table->string('status', 16)->default('open')->index();
            $table->string('report_type', 32)->default('bug')->index();

            $table->string('environment', 64)->default('')->index();
            $table->string('app_version', 64)->default('');
            $table->string('module', 128)->default('')->index();

            // Denormalized printable reporter (priority email > user_id > name).
            $table->string('reporter', 256)->default('');

            $table->text('page_url')->nullable();

            // User-Agent trust boundary — server captures one, client supplies
            // another. Never overwrite the server value with the client value.
            $table->text('user_agent_server')->nullable();
            $table->text('user_agent_client')->nullable();

            $table->longText('metadata_json')->nullable();
            $table->string('screenshot_path', 512)->nullable();

            $table->string('github_issue_url', 512)->default('');
            $table->integer('github_issue_number')->nullable();

            $table->timestamp('archived_at')->nullable()->index();
            $table->timestamps();

            $table->index(['status', 'severity'], 'bugfab_status_severity_idx');
        });

        Schema::connection($connection)->create('bug_fab_lifecycle', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->string('bug_report_id', 64);
            $table->string('action', 32);
            $table->string('by', 256)->default('anonymous');
            $table->timestamp('at')->useCurrent();
            $table->string('fix_commit', 512)->default('');
            $table->text('fix_description')->nullable();
            $table->text('metadata_json')->nullable();

            $table->foreign('bug_report_id')
                  ->references('id')
                  ->on('bug_fab_reports')
                  ->cascadeOnDelete();

            $table->index(['bug_report_id', 'at'], 'bugfab_lifecycle_report_at_idx');
        });
    }

    public function down(): void
    {
        $connection = config('bugfab.storages.eloquent.connection');
        Schema::connection($connection)->dropIfExists('bug_fab_lifecycle');
        Schema::connection($connection)->dropIfExists('bug_fab_reports');
    }
};
