<?php

declare(strict_types=1);

namespace BugFab\Laravel\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * Append-only lifecycle audit entry. Never updated — state transitions
 * always insert a new row.
 */
class BugReportLifecycle extends Model
{
    protected $table = 'bug_fab_lifecycle';

    public $timestamps = false;

    protected $guarded = [];

    protected $casts = [
        'at' => 'datetime',
    ];

    public function getConnectionName(): ?string
    {
        return config('bugfab.storages.eloquent.connection');
    }

    public function bugReport(): BelongsTo
    {
        return $this->belongsTo(BugReport::class, 'bug_report_id', 'id');
    }
}
