<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests\Feature;

use BugFab\Laravel\Tests\TestCase;

/**
 * Verifies the FileStorage backend by switching the config and re-exercising
 * the wire endpoints. Same conformance contract, different persistence.
 */
class FileStorageRoundTripTest extends TestCase
{
    protected function defineEnvironment($app): void
    {
        parent::defineEnvironment($app);
        $app['config']->set('bugfab.storage', 'file');
    }

    public function test_file_backend_round_trips_intake_to_detail(): void
    {
        $res = $this->doIntake($this->makeMetadata());
        $res->assertStatus(201);
        $id = $res->json('id');

        $detail = $this->getJson("/admin/bug-reports/reports/{$id}");
        $detail->assertStatus(200);
        $this->assertEquals('high', $detail->json('severity'));
        $this->assertEquals('checkout', $detail->json('context.module'));
    }

    public function test_file_backend_status_update_and_bulk(): void
    {
        $id = $this->doIntake($this->makeMetadata())->json('id');
        $this->putJson("/admin/bug-reports/reports/{$id}/status", ['status' => 'fixed'])
             ->assertStatus(200);

        $closed = $this->postJson('/admin/bug-reports/bulk-close-fixed');
        $this->assertEquals(1, $closed->json('closed'));
    }
}
