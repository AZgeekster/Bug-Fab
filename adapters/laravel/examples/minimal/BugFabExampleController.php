<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Routing\Controller;

/**
 * Example: showing how a consumer might surface a "report a bug" page that
 * embeds the Bug-Fab JS bundle. The bundle itself ships separately from
 * the package and submits to /api/bug-reports, which the service provider
 * already binds.
 *
 * The handler is plain PHP — nothing here imports Bug-Fab classes. The
 * package is a black box; the consumer only needs to embed the bundle.
 */
class BugFabExampleController extends Controller
{
    public function index(Request $request)
    {
        return response()->view('example.bugfab-host', [
            'app_version' => '1.4.2',
            'environment' => config('app.env'),
            // The bundle reads these data-* attributes on its <script> tag.
        ]);
    }
}
