// Public API surface for `bug-fab-hono`.
//
// Primary entry: `createBugFabApp(opts)` returns a Hono instance with
// all 8 protocol endpoints. `mountBugFab(parent, opts)` is the same
// thing wrapped for parent-app composition. Storage backends are
// importable individually for tree-shaking on edge deploys.

export { createBugFabApp, mountBugFab } from './app.js'
export type { CreateBugFabAppExtraOptions } from './app.js'

export { MemoryStorage } from './storage/MemoryStorage.js'
export type { MemoryStorageOptions } from './storage/MemoryStorage.js'

export { R2Storage } from './storage/R2Storage.js'
export type { R2Storage as R2StorageType, R2StorageOptions, R2Bucket } from './storage/R2Storage.js'

export { KVStorage } from './storage/KVStorage.js'
export type { KVStorageOptions, KVNamespace } from './storage/KVStorage.js'

export { Errors } from './errors.js'
export type { BugFabErrorBody } from './errors.js'

export {
  validateSubmission,
  validateStatusUpdate,
  isValidPng,
  isValidSeverity,
  isValidStatus,
  isValidReportType,
  isSupportedProtocolVersion,
  isValidReportId,
  VALID_SEVERITIES,
  VALID_STATUSES,
  VALID_REPORT_TYPES,
  SUPPORTED_PROTOCOL_VERSIONS,
  MAX_SCREENSHOT_BYTES,
  MAX_TOTAL_REQUEST_BYTES,
  MAX_TITLE_LENGTH,
  MAX_REPORTER_FIELD_LENGTH,
} from './validation.js'

export type {
  // Wire types
  Severity,
  Status,
  ReportType,
  ProtocolVersion,
  Reporter,
  BugReportContext,
  BugReportSubmission,
  StoredMetadata,
  LifecycleEvent,
  BugReportSummary,
  BugReportDetail,
  BugReportListStats,
  BugReportListResponse,
  BugReportIntakeResponse,
  StatusUpdateRequest,
  ListFilters,
  // Storage interface
  IStorage,
  // App options
  BugFabAppOptions,
  BugFabGitHubOptions,
  BugFabRateLimitOptions,
  BugFabViewerPermissions,
} from './types.js'
