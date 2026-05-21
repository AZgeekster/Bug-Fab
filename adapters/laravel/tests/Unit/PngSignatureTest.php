<?php

declare(strict_types=1);

namespace BugFab\Laravel\Tests\Unit;

use BugFab\Laravel\Support\PngSignature;
use PHPUnit\Framework\TestCase;

class PngSignatureTest extends TestCase
{
    public function test_accepts_valid_png_signature(): void
    {
        $this->assertTrue(PngSignature::verify("\x89PNG\r\n\x1a\n" . str_repeat("\x00", 10)));
    }

    public function test_rejects_jpeg(): void
    {
        $this->assertFalse(PngSignature::verify("\xFF\xD8\xFF\xE0JFIF"));
    }

    public function test_rejects_empty(): void
    {
        $this->assertFalse(PngSignature::verify(''));
    }

    public function test_rejects_too_short(): void
    {
        $this->assertFalse(PngSignature::verify("\x89PNG"));
    }

    public function test_rejects_text(): void
    {
        $this->assertFalse(PngSignature::verify('hello world'));
    }
}
