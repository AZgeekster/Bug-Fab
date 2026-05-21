<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests\Feature;

use BugFab\Laravel\Tests\TestCase;

class IntakeTest extends TestCase
{
    public function test_valid_submission_returns_201_with_minimal_envelope(): void
    {
        $res = $this->doIntake($this->makeMetadata());
        $res->assertStatus(201);
        $body = $res->json();
        $this->assertMatchesRegularExpression('/^bug-\d{3,}$/', $body['id']);
        $this->assertNotEmpty($body['received_at']);
        $this->assertStringStartsWith('bug-fab-laravel://reports/', $body['stored_at']);
        $this->assertNull($body['github_issue_url']);
    }

    public function test_unknown_protocol_version_returns_400_unsupported(): void
    {
        $res = $this->doIntake($this->makeMetadata(['protocol_version' => '99.9']));
        $res->assertStatus(400);
        $this->assertEquals('unsupported_protocol_version', $res->json('error'));
    }

    public function test_invalid_severity_returns_422_schema_error(): void
    {
        // PROTOCOL.md § Severity enum — silent coercion fails conformance.
        $res = $this->doIntake($this->makeMetadata(['severity' => 'urgent']));
        $res->assertStatus(422);
        $this->assertEquals('schema_error', $res->json('error'));
    }

    public function test_missing_title_returns_422(): void
    {
        $res = $this->doIntake($this->makeMetadata(['title' => '']));
        $res->assertStatus(422);
        $this->assertEquals('schema_error', $res->json('error'));
    }

    public function test_oversize_reporter_field_returns_422(): void
    {
        $longEmail = str_repeat('a', 300);
        $res = $this->doIntake($this->makeMetadata(['reporter' => ['email' => $longEmail]]));
        $res->assertStatus(422);
        $this->assertEquals('schema_error', $res->json('error'));
    }

    public function test_non_png_screenshot_returns_415(): void
    {
        $res = $this->doIntake($this->makeMetadata(), 'not a png file');
        $res->assertStatus(415);
        $this->assertEquals('unsupported_media_type', $res->json('error'));
    }

    public function test_oversize_screenshot_returns_413_with_limit_bytes(): void
    {
        $big = $this->makePng(5 * 1024 * 1024); // exceeds 4 MiB cap
        $res = $this->doIntake($this->makeMetadata(), $big);
        $res->assertStatus(413);
        $this->assertEquals('payload_too_large', $res->json('error'));
        $this->assertIsInt($res->json('limit_bytes'));
    }

    public function test_malformed_metadata_json_returns_400(): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'bugfab') . '.png';
        file_put_contents($tmp, $this->makePng());
        $file = new \Illuminate\Http\UploadedFile($tmp, 'screenshot.png', 'image/png', null, true);

        $res = $this->call('POST', '/api/bug-reports', ['metadata' => '{ not valid json'], [], ['screenshot' => $file]);
        $res->assertStatus(400);
        $this->assertEquals('validation_error', $res->json('error'));
    }

    public function test_server_user_agent_is_captured_independently(): void
    {
        $res = $this->doIntake($this->makeMetadata());
        $res->assertStatus(201);
        $id = $res->json('id');

        $detail = $this->getJson("/admin/bug-reports/reports/{$id}");
        $detail->assertStatus(200);
        // Client-reported value preserved separately.
        $this->assertEquals('Mozilla/5.0 fake', $detail->json('client_reported_user_agent'));
        // Server value comes from the request header (Symfony test client may
        // set "Symfony" by default — assert it's NOT the client's spoofed value).
        $this->assertNotEquals('Mozilla/5.0 fake', $detail->json('server_user_agent'));
    }

    public function test_rate_limit_when_enabled_returns_429(): void
    {
        config(['bugfab.rate_limit.enabled' => true]);
        config(['bugfab.rate_limit.max' => 2]);
        config(['bugfab.rate_limit.window' => 60]);

        $this->doIntake($this->makeMetadata())->assertStatus(201);
        $this->doIntake($this->makeMetadata())->assertStatus(201);
        $res = $this->doIntake($this->makeMetadata());
        $res->assertStatus(429);
        $this->assertEquals('rate_limited', $res->json('error'));
    }
}
