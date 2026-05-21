// Public entry point for `bug-fab-express`.
//
// Quickstart:
//
//   import express from 'express'
//   import { createBugFabRouter, FileStorage } from 'bug-fab-express'
//
//   const app = express()
//   const storage = new FileStorage({ storageDir: './var/bug_fab' })
//   app.use('/admin/bug-reports', createBugFabRouter({ storage }))
//   app.listen(3000)
//
// See README.md for the full quickstart, CSP guidance, and deployment
// patterns. Reference: https://github.com/AZgeekster/Bug-Fab

export { createBugFabRouter } from './router.js'
export { FileStorage } from './storage/FileStorage.js'
export type { FileStorageOptions } from './storage/FileStorage.js'

export type {
  // Wire-protocol shapes
  Severity, Status, ReportType, ProtocolVersion,
  Reporter, BugReportContext, BugReportSubmission, LifecycleEvent, LifecycleAction,
  BugReportSummary, BugReportDetail,
  BugReportListStats, BugReportListResponse, BugReportIntakeResponse,
  StatusUpdateRequest, ListFilters,
  // Storage contract
  IStorage, SaveReportInput,
  // Router options
  BugFabRouterOptions, BugFabGitHubOptions, BugFabRateLimitOptions,
  BugFabViewerPermissions, Logger,
} from './types.js'

export { Errors } from './errors.js'
export type { BugFabErrorBody } from './errors.js'

export {
  validateSubmission, validateStatusUpdate, isValidPngBuffer,
  isValidSeverity, isValidStatus, isValidReportType, isValidProtocolVersion,
  VALID_SEVERITIES, VALID_STATUSES, VALID_REPORT_TYPES,
  SUPPORTED_PROTOCOL_VERSION, DEFAULT_MAX_SCREENSHOT_BYTES,
  MAX_TITLE_LENGTH, MAX_REPORTER_FIELD_LENGTH,
} from './validation.js'
