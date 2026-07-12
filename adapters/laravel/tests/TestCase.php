<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests;

use BugFab\Laravel\BugFabServiceProvider;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Storage;
use Orchestra\Testbench\TestCase as Orchestra;

/**
 * Base test case wires Testbench into a fresh SQLite-in-memory DB and a
 * fake "local" disk per test. No external services touched.
 */
abstract class TestCase extends Orchestra
{
    use RefreshDatabase;

    protected function getPackageProviders($app): array
    {
        return [BugFabServiceProvider::class];
    }

    protected function defineEnvironment($app): void
    {
        // In-memory SQLite captures every persistence path the Eloquent
        // backend uses, without any DB infrastructure.
        $app['config']->set('database.default', 'testing');
        $app['config']->set('database.connections.testing', [
            'driver'   => 'sqlite',
            'database' => ':memory:',
            'prefix'   => '',
        ]);
        $app['config']->set('bugfab.storage', 'eloquent');
        $app['config']->set('bugfab.storages.eloquent.connection', null);
        $app['config']->set('bugfab.storages.eloquent.screenshot_disk', 'local');
        $app['config']->set('bugfab.storages.eloquent.screenshot_path', 'bug-fab/screenshots');
        $app['config']->set('bugfab.storages.file.disk', 'local');
        $app['config']->set('bugfab.storages.file.path', 'bug-fab-file');
        $app['config']->set('bugfab.max_screenshot_mb', 4);
        $app['config']->set('bugfab.rate_limit.enabled', false);
        $app['config']->set('bugfab.viewer_permissions', [
            'can_edit_status' => true,
            'can_delete'      => true,
            'can_bulk'        => true,
        ]);
        // No middleware groups in tests — Testbench doesn't ship 'api'/'web'.
        $app['config']->set('bugfab.routes.intake.middleware', []);
        $app['config']->set('bugfab.routes.viewer.middleware', []);
        // CSRF middleware would 419 the POST without a token; disable it
        // by clearing the global middleware in test environment.
        $app['config']->set('bugfab.csp_nonce_header', null);
        $app['config']->set('bugfab.routes.intake.prefix', 'api');
        $app['config']->set('bugfab.routes.viewer.prefix', 'admin/bug-reports');
    }

    protected function setUp(): void
    {
        parent::setUp();
        Storage::fake('local');
    }

    /** Construct a valid PNG byte string (8-byte signature + IHDR + IEND). */
    protected function makePng(int $extraBytes = 100): string
    {
        $sig = "\x89PNG\r\n\x1a\n";
        return $sig . str_repeat("\x00", $extraBytes);
    }

    /** Build a default valid metadata array, with overrides merged in. */
    protected function makeMetadata(array $overrides = []): array
    {
        return array_replace_recursive([
            'protocol_version' => '0.1',
            'title'            => 'Save button is unresponsive',
            'client_ts'        => '2026-04-27T15:29:58-07:00',
            'report_type'      => 'bug',
            'description'      => 'Click does nothing on the cart page.',
            'severity'         => 'high',
            'tags'             => ['regression'],
            'reporter'         => ['email' => 'alice@example.com'],
            'context'          => [
                'url'         => 'https://example.com/cart',
                'module'      => 'checkout',
                'user_agent'  => 'Mozilla/5.0 fake',
                'environment' => 'prod',
            ],
        ], $overrides);
    }

    /** Build an UploadedFile fixture + payload, return Response. */
    protected function doIntake(array $metadata, ?string $screenshotBytes = null, array $server = [])
    {
        $screenshotBytes ??= $this->makePng();
        $tmp = tempnam(sys_get_temp_dir(), 'bugfab') . '.png';
        file_put_contents($tmp, $screenshotBytes);
        $file = new \Illuminate\Http\UploadedFile(
            $tmp,
            'screenshot.png',
            'image/png',
            null,
            true
        );

        return $this->call(
            'POST',
            '/api/bug-reports',
            ['metadata' => json_encode($metadata)],
            [],
            ['screenshot' => $file],
            $server,
        );
    }
}
