// Re-export the storage interface from src/types.ts so consumers can
// `import type { IStorage } from 'bug-fab-hono/storage'` without
// pulling in the rest of the public type surface.
//
// Edge-runtime constraint: screenshot bytes use `Uint8Array`, NOT
// Node's `Buffer`. `Buffer` is unavailable on Cloudflare Workers,
// Deno Deploy, and Vercel Edge.

export type {
  IStorage,
  StoredMetadata,
  BugReportDetail,
  BugReportSummary,
  BugReportListStats,
  ListFilters,
  Status,
  Severity,
  ReportType,
  LifecycleEvent,
  Reporter,
  BugReportContext,
} from '../types.js'
