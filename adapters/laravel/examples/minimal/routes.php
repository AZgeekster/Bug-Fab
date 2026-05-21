<?php

declare(strict_types=1);

/*
|--------------------------------------------------------------------------
| Example: Bug-Fab in a Laravel 11 app
|--------------------------------------------------------------------------
|
| The Bug-Fab service provider auto-registers via composer's package
| discovery, so the only consumer-side step needed is:
|
|   1.  composer require bugfab/laravel-adapter
|   2.  php artisan vendor:publish --tag=bugfab-config   (optional)
|   3.  php artisan migrate                              (if using eloquent backend)
|
| That's it — POST /api/bug-reports and GET /admin/bug-reports/ are now
| live. The lines below are NOT required; they're included only as an
| illustration of how a consumer can customize the mount points or add an
| admin guard.
|
*/

use Illuminate\Support\Facades\Route;

// Move the viewer behind your existing admin middleware. The package
// already mounts the routes; this group simply wraps the prefix with
// extra middleware via Laravel's URL middleware groups in your app's
// app/Http/Kernel.php.
Route::middleware(['web', 'auth', 'can:viewBugReports'])
    ->prefix('admin/bug-reports')
    ->group(function () {
        // Bug-Fab's own routes are already bound here by the service
        // provider — declaring nothing else lets them inherit this
        // middleware stack automatically if the prefix matches.
    });
