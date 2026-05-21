<?php

declare(strict_types=1);

/*
|--------------------------------------------------------------------------
| Bug-Fab Routes
|--------------------------------------------------------------------------
|
| Loaded by BugFabServiceProvider with two mount points: intake (open) and
| viewer (admin). The wire protocol defines 8 endpoints — see PROTOCOL.md.
|
| Intake routes live at the "intake" prefix; viewer routes at the "viewer"
| prefix. Both prefixes are configurable in config/bugfab.php.
|
*/

use BugFab\Laravel\Http\Controllers\BugReportsController;
use Illuminate\Support\Facades\Route;

// --- Intake (POST /bug-reports) ---------------------------------------------
Route::middleware(config('bugfab.routes.intake.middleware', ['api']))
    ->prefix(config('bugfab.routes.intake.prefix', 'api'))
    ->group(function (): void {
        Route::post('/bug-reports', [BugReportsController::class, 'submit'])
            ->name('bugfab.submit');
    });

// --- Viewer (list / detail / status / delete / bulk) -------------------------
Route::middleware(config('bugfab.routes.viewer.middleware', ['web']))
    ->prefix(config('bugfab.routes.viewer.prefix', 'admin/bug-reports'))
    ->group(function (): void {
        // HTML list — the viewer "root".
        Route::get('/', [BugReportsController::class, 'listHtml'])
            ->name('bugfab.list.html');

        // JSON management endpoints.
        Route::get('/reports', [BugReportsController::class, 'listJson'])
            ->name('bugfab.list.json');

        Route::get('/reports/{report_id}/screenshot', [BugReportsController::class, 'screenshot'])
            ->where('report_id', 'bug-[A-Za-z]?\d{1,12}')
            ->name('bugfab.screenshot');

        Route::put('/reports/{report_id}/status', [BugReportsController::class, 'updateStatus'])
            ->where('report_id', 'bug-[A-Za-z]?\d{1,12}')
            ->name('bugfab.status');

        Route::get('/reports/{report_id}', [BugReportsController::class, 'detailJson'])
            ->where('report_id', 'bug-[A-Za-z]?\d{1,12}')
            ->name('bugfab.detail.json');

        Route::delete('/reports/{report_id}', [BugReportsController::class, 'delete'])
            ->where('report_id', 'bug-[A-Za-z]?\d{1,12}')
            ->name('bugfab.delete');

        Route::post('/bulk-close-fixed', [BugReportsController::class, 'bulkCloseFixed'])
            ->name('bugfab.bulk.close');

        Route::post('/bulk-archive-closed', [BugReportsController::class, 'bulkArchiveClosed'])
            ->name('bugfab.bulk.archive');

        // HTML detail page lives under the prefix root — keep it last so the
        // static-segment routes above (reports, bulk-...) match first.
        Route::get('/{report_id}', [BugReportsController::class, 'detailHtml'])
            ->where('report_id', 'bug-[A-Za-z]?\d{1,12}')
            ->name('bugfab.detail.html');
    });
