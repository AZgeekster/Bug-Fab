<?php

declare(strict_types=1);

namespace BugFab\Laravel;

use BugFab\Laravel\Storage\EloquentStorage;
use BugFab\Laravel\Storage\FileStorage;
use BugFab\Laravel\Storage\StorageContract;
use Illuminate\Support\ServiceProvider;

/**
 * Bug-Fab Laravel service provider — auto-registered via Laravel's package
 * discovery (see composer.json "extra.laravel.providers").
 *
 * Responsibilities:
 *  - Merge the default config so `config('bugfab.*')` always resolves.
 *  - Bind a singleton StorageContract → FileStorage or EloquentStorage
 *    based on config('bugfab.storage').
 *  - Load the route file under the configured mount prefixes.
 *  - Load + publish migrations and Blade views.
 *
 * DI rule (per the spec's anti-patterns): the storage binding is registered
 * inside register() but the StorageFacade-touching FileStorage/EloquentStorage
 * are instantiated LAZILY by the container when first resolved — register()
 * itself does not open DB connections, and the binding is a closure not a
 * concrete object.
 */
class BugFabServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->mergeConfigFrom(__DIR__ . '/../config/bugfab.php', 'bugfab');

        // Storage binding — closure-based, so no DB or disk handles open
        // here. Container resolves the concrete storage on first use,
        // which means MySQL connections happen in the request lifecycle
        // (boot/handle) not at provider register-time.
        $this->app->singleton(StorageContract::class, function ($app): StorageContract {
            $backend = (string) $app['config']->get('bugfab.storage', 'eloquent');
            $idPrefix = (string) $app['config']->get('bugfab.id_prefix', '');
            return match ($backend) {
                'file' => new FileStorage(
                    disk:     (string) $app['config']->get('bugfab.storages.file.disk', 'local'),
                    root:     (string) $app['config']->get('bugfab.storages.file.path', 'bug-fab'),
                    idPrefix: $idPrefix,
                ),
                'eloquent', 'db' => new EloquentStorage(
                    disk:           (string) $app['config']->get('bugfab.storages.eloquent.screenshot_disk', 'local'),
                    screenshotPath: (string) $app['config']->get('bugfab.storages.eloquent.screenshot_path', 'bug-fab/screenshots'),
                    idPrefix:       $idPrefix,
                ),
                default => throw new \InvalidArgumentException(
                    "Unknown bug-fab storage backend: {$backend}. Expected 'file' or 'eloquent'."
                ),
            };
        });
    }

    public function boot(): void
    {
        // Routes — loaded only if at least one prefix is configured.
        $this->loadRoutesFrom(__DIR__ . '/../routes/bugfab.php');

        // Views — registered under the "bug-fab" namespace so consumers can
        // override individual templates by publishing or by creating
        // resources/views/vendor/bug-fab/list.blade.php in their app.
        $this->loadViewsFrom(__DIR__ . '/../resources/views/bug-fab', 'bug-fab');

        // Migrations.
        $this->loadMigrationsFrom(__DIR__ . '/../database/migrations');

        // Publishable assets — config, migrations, views.
        if ($this->app->runningInConsole()) {
            $this->publishes([
                __DIR__ . '/../config/bugfab.php' => config_path('bugfab.php'),
            ], 'bugfab-config');

            $this->publishes([
                __DIR__ . '/../database/migrations' => database_path('migrations'),
            ], 'bugfab-migrations');

            $this->publishes([
                __DIR__ . '/../resources/views/bug-fab' => resource_path('views/vendor/bug-fab'),
            ], 'bugfab-views');
        }
    }
}
