<?php

declare(strict_types=1);

/*
|--------------------------------------------------------------------------
| Bug-Fab Laravel Adapter Configuration
|--------------------------------------------------------------------------
|
| All values are env-driven with sensible defaults. Publish via:
|
|     php artisan vendor:publish --tag=bugfab-config
|
| The wire-protocol contract (HTTP shapes, enum values, ID regex) is fixed
| in the BugFab\Laravel\Schemas namespace — values here only control the
| operational envelope around the protocol, never the protocol itself.
|
*/

return [
    /*
     * Wire-protocol version this adapter implements.
     * Locked at "0.1" for v0.1. Adapters MUST reject unknown values with 400.
     */
    'protocol_version' => '0.1',

    /*
     * Storage backend — "file" or "eloquent".
     *
     * - "file":     On-disk JSON + PNG layout matching the Python reference
     *               adapter. Single-node only (no cross-process locking).
     * - "eloquent": MySQL-first (MariaDB / PostgreSQL also supported via
     *               Laravel's database drivers). Use this in multi-worker
     *               or Octane deployments. SQLite is used for tests.
     */
    'storage' => env('BUG_FAB_STORAGE', 'eloquent'),

    /*
     * Storage-backend-specific settings.
     */
    'storages' => [
        'file' => [
            // Filesystem disk to write to (must be configured in
            // config/filesystems.php). Default "local".
            'disk' => env('BUG_FAB_FILE_DISK', 'local'),
            // Path prefix under the disk root.
            'path' => env('BUG_FAB_FILE_PATH', 'bug-fab'),
        ],
        'eloquent' => [
            // Database connection (null = default). Useful when reports go to
            // a separate "audit" DB.
            'connection' => env('BUG_FAB_DB_CONNECTION'),
            // Disk for screenshot bytes (files live next to the DB rows).
            'screenshot_disk' => env('BUG_FAB_SCREENSHOT_DISK', 'local'),
            'screenshot_path' => env('BUG_FAB_SCREENSHOT_PATH', 'bug-fab/screenshots'),
        ],
    ],

    /*
     * Optional ID prefix (BUG_FAB_ID_PREFIX). When set to "P", IDs render
     * as "bug-P001". When unset, IDs render as "bug-001". Helpful for shared
     * multi-environment collectors.
     */
    'id_prefix' => env('BUG_FAB_ID_PREFIX', ''),

    /*
     * Screenshot upload cap. PROTOCOL.md sets the wire-protocol envelope at
     * 10 MiB; this adapter defaults to a tighter 4 MiB cap per the v0.1
     * Laravel-adapter spec. Adapters MAY enforce a stricter limit.
     *
     * Exceeding this returns 413 with body { error: "payload_too_large",
     *                                         limit_bytes: <N> }.
     */
    'max_screenshot_mb' => (int) env('BUG_FAB_MAX_SCREENSHOT_MB', 4),

    /*
     * Per-IP rate limiting (Laravel RateLimiter facade). Off by default —
     * mount-point auth is the primary line of defense.
     */
    'rate_limit' => [
        'enabled' => env('BUG_FAB_RATE_LIMIT_ENABLED', false),
        // Allowed submissions per window per IP.
        'max'     => (int) env('BUG_FAB_RATE_LIMIT_MAX', 10),
        // Window length in seconds.
        'window'  => (int) env('BUG_FAB_RATE_LIMIT_WINDOW', 60),
    ],

    /*
     * Mount points. Each is a URL prefix the service provider binds the
     * matching router under. Disable a router by setting the value to null.
     */
    'routes' => [
        // Intake — POST /bug-reports. Usually mounted under /api/ so the
        // bundled JS frontend can submit without an admin login.
        'intake' => [
            'prefix'     => env('BUG_FAB_INTAKE_PREFIX', 'api'),
            // Middleware groups applied to the intake route. Defaults to "api".
            'middleware' => ['api'],
        ],
        // Viewer — list / detail / status / delete / bulk endpoints.
        // Mount under whatever middleware your admin auth covers.
        'viewer' => [
            'prefix'     => env('BUG_FAB_VIEWER_PREFIX', 'admin/bug-reports'),
            'middleware' => ['web'],
        ],
    ],

    /*
     * Viewer permissions — in-band veto for destructive actions. These are
     * not per-user checks (the v0.1 protocol has no AuthAdapter); they
     * control whether the endpoints exist at all on this mount.
     */
    'viewer_permissions' => [
        'can_edit_status' => (bool) env('BUG_FAB_VIEWER_CAN_EDIT_STATUS', true),
        'can_delete'      => (bool) env('BUG_FAB_VIEWER_CAN_DELETE', true),
        'can_bulk'        => (bool) env('BUG_FAB_VIEWER_CAN_BULK', true),
    ],

    /*
     * Viewer pagination — default page size when the request doesn't
     * supply one. Hard-capped at 200 per the wire protocol.
     */
    'viewer_page_size' => (int) env('BUG_FAB_VIEWER_PAGE_SIZE', 20),

    /*
     * Optional CSP nonce header. When set, the viewer reads this header
     * off the incoming request and emits it on inline <script> tags in
     * Blade views. Use with your CSP middleware.
     */
    'csp_nonce_header' => env('BUG_FAB_CSP_NONCE_HEADER'),

    /*
     * GitHub Issues sync (best-effort). Disabled by default. Failures are
     * logged and NEVER cause the bug-report intake to fail.
     */
    'github' => [
        'enabled' => env('BUG_FAB_GITHUB_ENABLED', false),
        'pat'     => env('BUG_FAB_GITHUB_PAT'),
        'repo'    => env('BUG_FAB_GITHUB_REPO'),
        'api_base'=> env('BUG_FAB_GITHUB_API_BASE', 'https://api.github.com'),
    ],
];
