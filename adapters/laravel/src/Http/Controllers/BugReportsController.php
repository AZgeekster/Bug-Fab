<?php

declare(strict_types=1);

namespace BugFab\Laravel\Http\Controllers;

use BugFab\Laravel\Enums\Severity;
use BugFab\Laravel\Enums\Status;
use BugFab\Laravel\Http\Requests\StoreBugReportRequest;
use BugFab\Laravel\Storage\StorageContract;
use BugFab\Laravel\Support\Errors;
use BugFab\Laravel\Support\IdGenerator;
use BugFab\Laravel\Support\PngSignature;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Routing\Controller;
use Illuminate\Support\Facades\RateLimiter;
use Symfony\Component\HttpFoundation\Response as SymfonyResponse;
use Symfony\Component\HttpFoundation\StreamedResponse;

/**
 * Bug-Fab v0.1 controller. Handles all 8 protocol endpoints.
 *
 * Validation is delegated to StoreBugReportRequest for intake; the remaining
 * routes do per-endpoint lightweight validation inline (so we can return
 * the exact error envelopes PROTOCOL.md mandates rather than Laravel's
 * default validation JSON shape).
 */
class BugReportsController extends Controller
{
    public function __construct(private readonly StorageContract $storage)
    {
    }

    // ------------------------------------------------------------------
    // POST /bug-reports
    // ------------------------------------------------------------------

    public function submit(StoreBugReportRequest $request): JsonResponse
    {
        // Rate limit per-IP. Disabled by default; enabled via env.
        $rateConf = config('bugfab.rate_limit', []);
        if (! empty($rateConf['enabled'])) {
            $key = 'bugfab:intake:' . $this->clientIp($request);
            $max = (int) ($rateConf['max'] ?? 10);
            $window = (int) ($rateConf['window'] ?? 60);
            if (RateLimiter::tooManyAttempts($key, $max)) {
                return Errors::json(
                    Errors::RATE_LIMITED,
                    "Rate limit exceeded: max {$max} per {$window}s",
                    429,
                    ['retry_after_seconds' => RateLimiter::availableIn($key)]
                );
            }
            RateLimiter::hit($key, $window);
        }

        // Strict protocol_version gate (400, not 422, per PROTOCOL.md).
        $request->checkProtocolVersion();

        $metadata = $request->metadata();

        $uploaded = $request->file('screenshot');
        if ($uploaded === null) {
            return Errors::json(Errors::VALIDATION_ERROR, 'screenshot is required', 400);
        }

        // Size cap — return 413 with limit_bytes per the standard error code table.
        $maxBytes = (int) config('bugfab.max_screenshot_mb', 4) * 1024 * 1024;
        $fileSize = (int) $uploaded->getSize();
        if ($fileSize > $maxBytes) {
            return Errors::json(
                Errors::PAYLOAD_TOO_LARGE,
                "Screenshot exceeds maximum size of " . ($maxBytes / 1024 / 1024) . " MiB",
                413,
                ['limit_bytes' => $maxBytes]
            );
        }

        $bytes = (string) file_get_contents($uploaded->getRealPath());
        if ($bytes === '') {
            return Errors::json(Errors::VALIDATION_ERROR, 'screenshot file is empty', 400);
        }

        // Magic-byte verification — Content-Type alone cannot be trusted.
        if (! PngSignature::verify($bytes)) {
            return Errors::json(
                Errors::UNSUPPORTED_MEDIA_TYPE,
                'Screenshot must be a PNG image (image/png)',
                415
            );
        }

        // Build the persistence payload. Server captures User-Agent
        // independently from any client-supplied value.
        $serverUserAgent = (string) $request->header('User-Agent', '');
        $context = (array) ($metadata['context'] ?? []);
        $environment = (string) (
            ($metadata['environment'] ?? null)
            ?? ($context['environment'] ?? '')
        );
        $metadata['server_user_agent'] = $serverUserAgent;
        $metadata['client_reported_user_agent'] = (string) ($context['user_agent'] ?? '');
        $metadata['environment'] = $environment;

        try {
            $reportId = $this->storage->saveReport($metadata, $bytes);
        } catch (\InvalidArgumentException $e) {
            return Errors::json(Errors::SCHEMA_ERROR, $e->getMessage(), 422);
        } catch (\Throwable $e) {
            report($e);

            return Errors::json(Errors::INTERNAL_ERROR, 'Failed to persist bug report', 500);
        }

        $detail = $this->storage->getReport($reportId);
        $receivedAt = $detail?->created_at ?? '';

        return new JsonResponse(
            [
                'id'               => $reportId,
                'received_at'      => $receivedAt,
                'stored_at'        => "bug-fab-laravel://reports/{$reportId}",
                'github_issue_url' => null,
            ],
            201
        );
    }

    // ------------------------------------------------------------------
    // GET / (HTML list)
    // ------------------------------------------------------------------

    public function listHtml(Request $request)
    {
        [$items, $total, $stats, $page, $pageSize] = $this->listCommon($request);

        return view('bug-fab::list', [
            'items'       => array_map(fn ($s) => $s->toArray(), $items),
            'total'       => $total,
            'stats'       => $stats,
            'page'        => $page,
            'page_size'   => $pageSize,
            'total_pages' => max((int) ceil($total / max($pageSize, 1)), 1),
            'filters'     => $this->buildFilters($request),
            'permissions' => config('bugfab.viewer_permissions', []),
            'csp_nonce'   => $this->cspNonce($request),
        ]);
    }

    // ------------------------------------------------------------------
    // GET /reports (JSON list)
    // ------------------------------------------------------------------

    public function listJson(Request $request): JsonResponse
    {
        [$items, $total, $stats, $page, $pageSize] = $this->listCommon($request);

        return new JsonResponse([
            'items'     => array_map(fn ($s) => $s->toArray(), $items),
            'total'     => $total,
            'page'      => $page,
            'page_size' => $pageSize,
            'stats'     => [
                'open'          => $stats['open'] ?? 0,
                'investigating' => $stats['investigating'] ?? 0,
                'fixed'         => $stats['fixed'] ?? 0,
                'closed'        => $stats['closed'] ?? 0,
            ],
        ]);
    }

    // ------------------------------------------------------------------
    // GET /reports/{id} (JSON detail)
    // ------------------------------------------------------------------

    public function detailJson(string $reportId): JsonResponse
    {
        if (! IdGenerator::isValid($reportId)) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }
        $detail = $this->storage->getReport($reportId);
        if ($detail === null) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }

        return new JsonResponse($detail->toArray());
    }

    // ------------------------------------------------------------------
    // GET /{id} (HTML detail)
    // ------------------------------------------------------------------

    public function detailHtml(Request $request, string $reportId)
    {
        if (! IdGenerator::isValid($reportId)) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }
        $detail = $this->storage->getReport($reportId);
        if ($detail === null) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }

        $arr = $detail->toArray();

        return view('bug-fab::detail', [
            'report'           => $arr,
            'safe_context_url' => $this->safeContextUrl($arr),
            'permissions'      => config('bugfab.viewer_permissions', []),
            'csp_nonce'        => $this->cspNonce($request),
        ]);
    }

    // ------------------------------------------------------------------
    // GET /reports/{id}/screenshot
    // ------------------------------------------------------------------

    public function screenshot(string $reportId): SymfonyResponse
    {
        if (! IdGenerator::isValid($reportId)) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }
        $shot = $this->storage->getScreenshot($reportId);
        if ($shot === null) {
            return Errors::json(Errors::NOT_FOUND, 'Screenshot not found', 404);
        }
        $bytes = $shot['bytes'];

        return new Response(
            $bytes,
            200,
            [
                'Content-Type'   => 'image/png',
                'Content-Length' => (string) strlen($bytes),
            ]
        );
    }

    // ------------------------------------------------------------------
    // PUT /reports/{id}/status
    // ------------------------------------------------------------------

    public function updateStatus(Request $request, string $reportId): JsonResponse
    {
        if (! $this->viewerCan('can_edit_status')) {
            return Errors::json('forbidden', "viewer action 'can_edit_status' is disabled", 403);
        }
        if (! IdGenerator::isValid($reportId)) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }

        $body = $request->json()->all();
        if (! is_array($body)) {
            return Errors::json(Errors::VALIDATION_ERROR, 'request body must be JSON', 400);
        }
        $statusRaw = $body['status'] ?? null;
        $statusEnum = is_string($statusRaw) ? Status::tryFrom($statusRaw) : null;
        if ($statusEnum === null) {
            return Errors::json(
                Errors::SCHEMA_ERROR,
                "status must be one of: open, investigating, fixed, closed",
                422
            );
        }

        $actor = $this->viewerActor($request);
        $updated = $this->storage->updateStatus(
            $reportId,
            $statusEnum->value,
            (string) ($body['fix_commit'] ?? ''),
            (string) ($body['fix_description'] ?? ''),
            $actor,
        );
        if ($updated === null) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }

        return new JsonResponse($updated->toArray());
    }

    // ------------------------------------------------------------------
    // DELETE /reports/{id}
    // ------------------------------------------------------------------

    public function delete(string $reportId): SymfonyResponse
    {
        if (! $this->viewerCan('can_delete')) {
            return Errors::json('forbidden', "viewer action 'can_delete' is disabled", 403);
        }
        if (! IdGenerator::isValid($reportId)) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }
        $ok = $this->storage->deleteReport($reportId);
        if (! $ok) {
            return Errors::json(Errors::NOT_FOUND, 'Bug report not found', 404);
        }

        return new Response('', 204);
    }

    // ------------------------------------------------------------------
    // POST /bulk-close-fixed
    // ------------------------------------------------------------------

    public function bulkCloseFixed(Request $request): JsonResponse
    {
        if (! $this->viewerCan('can_bulk')) {
            return Errors::json('forbidden', "viewer action 'can_bulk' is disabled", 403);
        }
        $closed = $this->storage->bulkCloseFixed($this->viewerActor($request));

        return new JsonResponse(['closed' => $closed]);
    }

    // ------------------------------------------------------------------
    // POST /bulk-archive-closed
    // ------------------------------------------------------------------

    public function bulkArchiveClosed(): JsonResponse
    {
        if (! $this->viewerCan('can_bulk')) {
            return Errors::json('forbidden', "viewer action 'can_bulk' is disabled", 403);
        }
        $archived = $this->storage->bulkArchiveClosed();

        return new JsonResponse(['archived' => $archived]);
    }

    // ------------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------------

    private function listCommon(Request $request): array
    {
        $page = max(1, (int) $request->query('page', 1));
        $pageSize = (int) $request->query(
            'page_size',
            (string) config('bugfab.viewer_page_size', 20)
        );
        $pageSize = max(1, min($pageSize, 200));
        $filters = $this->buildFilters($request);
        [$items, $total] = $this->storage->listReports($filters, $page, $pageSize);
        $stats = $this->computeStats();

        return [$items, $total, $stats, $page, $pageSize];
    }

    private function buildFilters(Request $request): array
    {
        $raw = [
            'status'      => $request->query('status'),
            'severity'    => $request->query('severity'),
            'module'      => $request->query('module'),
            'environment' => $request->query('environment'),
        ];
        $out = [];
        foreach ($raw as $k => $v) {
            if (is_string($v) && trim($v) !== '') {
                $out[$k] = trim($v);
            }
        }
        if (filter_var($request->query('include_archived', false), FILTER_VALIDATE_BOOLEAN)) {
            $out['include_archived'] = true;
        }

        return $out;
    }

    /**
     * Compute stat counts per status — same shape as the FastAPI viewer.
     */
    private function computeStats(): array
    {
        $stats = [];
        foreach (['open', 'investigating', 'fixed', 'closed'] as $state) {
            [, $total] = $this->storage->listReports(['status' => $state], 1, 1);
            $stats[$state] = $total;
        }
        [, $total] = $this->storage->listReports([], 1, 1);
        $stats['total'] = $total;

        return $stats;
    }

    private function viewerCan(string $flag): bool
    {
        $perms = (array) config('bugfab.viewer_permissions', []);

        return (bool) ($perms[$flag] ?? false);
    }

    private function viewerActor(Request $request): string
    {
        $explicit = $request->attributes->get('bugfab_actor');
        if ($explicit) {
            return (string) $explicit;
        }
        $user = $request->user();
        if ($user !== null) {
            // Common Laravel idioms — name or email then id.
            foreach (['email', 'name'] as $attr) {
                $val = $user->{$attr} ?? null;
                if ($val) {
                    return (string) $val;
                }
            }
            $id = method_exists($user, 'getAuthIdentifier') ? $user->getAuthIdentifier() : null;
            if ($id !== null) {
                return (string) $id;
            }
        }

        return 'viewer';
    }

    private function clientIp(Request $request): string
    {
        // Deliberately NOT reading X-Forwarded-For here: the header is
        // client-controlled, and rotating it would mint a fresh rate-limit
        // bucket per request, defeating the limiter. Laravel's own
        // TrustProxies middleware is the trust gate — when the consumer
        // declares their proxies (framework-level `trustProxies`),
        // $request->ip() resolves the real client from the forwarding
        // chain; otherwise it is the direct peer.
        return $request->ip() ?: 'unknown';
    }

    private function cspNonce(Request $request): ?string
    {
        $header = (string) config('bugfab.csp_nonce_header', '');
        if ($header === '') {
            return null;
        }
        $val = $request->header($header);

        return is_string($val) && $val !== '' ? $val : null;
    }

    /**
     * Allow only http(s) and root-relative URLs as the rendered "open in app"
     * link. Refuses to render javascript:, data:, etc. as a clickable href.
     */
    private function safeContextUrl(array $report): string
    {
        $url = (string) ($report['context']['url'] ?? '');
        if (str_starts_with($url, 'http://') || str_starts_with($url, 'https://') || str_starts_with($url, '/')) {
            return $url;
        }

        return '';
    }
}
