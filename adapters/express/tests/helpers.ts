// Shared test helpers — boots an Express app with the Bug-Fab router
// mounted at /admin/bug-reports and a temp-dir FileStorage backend.

import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import express, { type Express } from 'express'
import { createBugFabRouter } from '../src/index.js'
import { FileStorage } from '../src/storage/FileStorage.js'
import type { BugFabRouterOptions } from '../src/types.js'

export interface TestHarness {
  app:      Express
  storage:  FileStorage
  cleanup:  () => void
  storageDir: string
}

export function buildHarness(opts: Partial<BugFabRouterOptions> = {}): TestHarness {
  const storageDir = mkdtempSync(join(tmpdir(), 'bug-fab-express-test-'))
  const storage = new FileStorage({ storageDir })

  const app = express()
  app.use('/admin/bug-reports', createBugFabRouter({ storage, ...opts }))

  return {
    app,
    storage,
    storageDir,
    cleanup: () => rmSync(storageDir, { recursive: true, force: true }),
  }
}

// Minimal valid PNG (8-byte header + IHDR + IEND). Produces a 1x1 transparent
// PNG that passes magic-byte validation. Generated once and embedded as bytes
// rather than read from disk so tests stay self-contained.
export const TINY_PNG: Buffer = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,  // PNG signature
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,  // IHDR length + 'IHDR'
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  // 1x1
  0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,  // 8-bit RGBA, CRC
  0x89, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x44, 0x41,  // IDAT
  0x54, 0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00,
  0x05, 0x00, 0x01, 0x0d, 0x0a, 0x2d, 0xb4, 0x00,
  0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae,  // IEND
  0x42, 0x60, 0x82,
])

export const TINY_JPEG: Buffer = Buffer.from([
  0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46,  // JFIF marker
  0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
  0x00, 0x01, 0x00, 0x00, 0xff, 0xd9,              // EOI
])

export function validMetadata(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol_version: '0.1',
    title:            'Save button broken',
    client_ts:        '2026-04-30T15:30:00-07:00',
    description:      'Click does nothing on the cart page.',
    severity:         'high',
    report_type:      'bug',
    tags:             ['regression', 'checkout'],
    reporter:         { email: 'tester@example.com' },
    context: {
      url:              'https://example.com/cart',
      module:           'checkout',
      user_agent:       'Mozilla/5.0 test',
      viewport_width:   1920,
      viewport_height:  1080,
      app_version:      '1.4.2',
      environment:      'prod',
      console_errors:   [],
      network_log:      [],
    },
    ...overrides,
  }
}
