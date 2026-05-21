<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests\Feature;

use BugFab\Laravel\Tests\TestCase;

class ViewerTest extends TestCase
{
    private function submit(array $overrides = []): string
    {
        return $this->doIntake($this->makeMetadata($overrides))->json('id');
    }

    public function test_list_returns_stats_with_all_four_states(): void
    {
        $this->submit();
        $res = $this->getJson('/admin/bug-reports/reports');
        $res->assertStatus(200);
        $body = $res->json();
        $this->assertArrayHasKey('items', $body);
        $this->assertArrayHasKey('total', $body);
        $this->assertEquals(['open', 'investigating', 'fixed', 'closed'], array_keys($body['stats']));
    }

    public function test_detail_returns_full_round_trip(): void
    {
        $id = $this->submit();
        $res = $this->getJson("/admin/bug-reports/reports/{$id}");
        $res->assertStatus(200);
        $this->assertEquals('0.1', $res->json('protocol_version'));
        $this->assertEquals('high', $res->json('severity'));
        $this->assertEquals('checkout', $res->json('module'));
        $this->assertEquals('alice@example.com', $res->json('reporter.email'));
    }

    public function test_screenshot_returns_image_png_bytes(): void
    {
        $id = $this->submit();
        $res = $this->call('GET', "/admin/bug-reports/reports/{$id}/screenshot");
        $res->assertStatus(200);
        $res->assertHeader('Content-Type', 'image/png');
        $this->assertStringStartsWith("\x89PNG", $res->getContent());
    }

    public function test_status_update_appends_lifecycle_entry(): void
    {
        $id = $this->submit();
        $res = $this->putJson("/admin/bug-reports/reports/{$id}/status", [
            'status'          => 'fixed',
            'fix_commit'      => 'abc123',
            'fix_description' => 'Restored the event listener',
        ]);
        $res->assertStatus(200);
        $this->assertEquals('fixed', $res->json('status'));
        $lifecycle = $res->json('lifecycle');
        $this->assertCount(2, $lifecycle);
        $this->assertEquals('status_changed', end($lifecycle)['action']);
    }

    public function test_invalid_status_returns_422(): void
    {
        $id = $this->submit();
        $res = $this->putJson("/admin/bug-reports/reports/{$id}/status", ['status' => 'archived']);
        $res->assertStatus(422);
        $this->assertEquals('schema_error', $res->json('error'));
    }

    public function test_unknown_report_id_returns_404(): void
    {
        $res = $this->getJson('/admin/bug-reports/reports/bug-999');
        $res->assertStatus(404);
        $this->assertEquals('not_found', $res->json('error'));
    }

    public function test_path_traversal_id_returns_404(): void
    {
        $res = $this->getJson('/admin/bug-reports/reports/bug-..%2Fetc%2Fpasswd');
        $res->assertStatus(404);
    }

    public function test_delete_returns_204_then_404(): void
    {
        $id = $this->submit();
        $this->deleteJson("/admin/bug-reports/reports/{$id}")->assertStatus(204);
        $this->getJson("/admin/bug-reports/reports/{$id}")->assertStatus(404);
    }

    public function test_bulk_close_fixed_transitions_only_fixed(): void
    {
        $a = $this->submit(['title' => 'A']);
        $b = $this->submit(['title' => 'B']);
        $c = $this->submit(['title' => 'C']);
        $this->putJson("/admin/bug-reports/reports/{$a}/status", ['status' => 'fixed']);
        $this->putJson("/admin/bug-reports/reports/{$b}/status", ['status' => 'fixed']);
        // c stays open
        $res = $this->postJson('/admin/bug-reports/bulk-close-fixed');
        $res->assertStatus(200);
        $this->assertEquals(2, $res->json('closed'));

        $this->assertEquals('closed', $this->getJson("/admin/bug-reports/reports/{$a}")->json('status'));
        $this->assertEquals('open',   $this->getJson("/admin/bug-reports/reports/{$c}")->json('status'));
    }

    public function test_bulk_archive_closed_archives_only_closed(): void
    {
        $a = $this->submit(['title' => 'A']);
        $this->putJson("/admin/bug-reports/reports/{$a}/status", ['status' => 'closed']);
        $res = $this->postJson('/admin/bug-reports/bulk-archive-closed');
        $res->assertStatus(200);
        $this->assertEquals(1, $res->json('archived'));

        // Default list excludes archived.
        $list = $this->getJson('/admin/bug-reports/reports');
        $this->assertEquals(0, $list->json('total'));
    }

    public function test_disabled_viewer_permission_returns_403(): void
    {
        config(['bugfab.viewer_permissions.can_delete' => false]);
        $id = $this->submit();
        $res = $this->deleteJson("/admin/bug-reports/reports/{$id}");
        $res->assertStatus(403);
    }
}
