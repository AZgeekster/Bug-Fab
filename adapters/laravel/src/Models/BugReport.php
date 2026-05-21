<?php

declare(strict_types=1);

namespace BugFab\Laravel\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * One row per bug report.
 *
 * The string primary key (bug-NNN format) means we disable auto-incrementing
 * and the assignment happens inside EloquentStorage::saveReport().
 */
class BugReport extends Model
{
    use HasFactory;

    protected $table = 'bug_fab_reports';

    protected $primaryKey = 'id';
    public $incrementing = false;
    protected $keyType = 'string';

    protected $guarded = [];

    protected $casts = [
        'received_at'         => 'datetime',
        'archived_at'         => 'datetime',
        'github_issue_number' => 'integer',
    ];

    public function getConnectionName(): ?string
    {
        return config('bugfab.storages.eloquent.connection');
    }

    public function lifecycle(): HasMany
    {
        return $this->hasMany(BugReportLifecycle::class, 'bug_report_id', 'id')
                    ->orderBy('at')
                    ->orderBy('id');
    }
}
