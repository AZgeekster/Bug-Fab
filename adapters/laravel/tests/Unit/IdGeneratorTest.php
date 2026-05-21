<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests\Unit;

use BugFab\Laravel\Support\IdGenerator;
use PHPUnit\Framework\TestCase;

class IdGeneratorTest extends TestCase
{
    public function test_format_with_no_prefix(): void
    {
        $this->assertEquals('bug-001', IdGenerator::format(1));
        $this->assertEquals('bug-042', IdGenerator::format(42));
        $this->assertEquals('bug-100', IdGenerator::format(100));
    }

    public function test_format_with_alpha_prefix(): void
    {
        $this->assertEquals('bug-P001', IdGenerator::format(1, 'P'));
        $this->assertEquals('bug-D012', IdGenerator::format(12, 'D'));
    }

    public function test_valid_ids(): void
    {
        $this->assertTrue(IdGenerator::isValid('bug-001'));
        $this->assertTrue(IdGenerator::isValid('bug-P001'));
        $this->assertTrue(IdGenerator::isValid('bug-1'));
        $this->assertTrue(IdGenerator::isValid('bug-999999'));
    }

    public function test_invalid_ids(): void
    {
        $this->assertFalse(IdGenerator::isValid('001'));
        $this->assertFalse(IdGenerator::isValid('bug-'));
        $this->assertFalse(IdGenerator::isValid('bug-abc'));
        $this->assertFalse(IdGenerator::isValid('bug-../etc/passwd'));
        $this->assertFalse(IdGenerator::isValid('BUG-001'));
        $this->assertFalse(IdGenerator::isValid(''));
    }

    public function test_parse_number_strips_alpha_prefix(): void
    {
        $this->assertEquals(1, IdGenerator::parseNumber('bug-001'));
        $this->assertEquals(38, IdGenerator::parseNumber('bug-P038'));
        $this->assertEquals(0, IdGenerator::parseNumber('bug-abc'));
    }
}
