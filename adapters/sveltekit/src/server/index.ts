// Public server-side entry point for `bug-fab-sveltekit/server`.
//
// Consumers import factory functions from here and wire them into their
// `+server.ts` route files. The ONLY public API contract is what's re-exported
// below — internal modules (validation, errors, github) are intentionally not
// re-exported so we can refactor them freely.

export { createIntakeHandler } from './intake.js';
export { createListHandler } from './viewer/list.js';
export { createDetailHandler } from './viewer/detail.js';
export { createScreenshotHandler } from './viewer/screenshot.js';
export { createStatusHandler } from './viewer/status.js';
export { createDeleteHandler } from './viewer/delete.js';
export { createBulkCloseHandler } from './viewer/bulk-close.js';
export { createBulkArchiveHandler } from './viewer/bulk-archive.js';
// HTML index page for the viewer mount root (mount-prefix invariant —
// Adapter Authorship Checklist item 6).
export { createViewerIndexHandler } from './viewer/index.js';

// Storage backends.
export { FileStorage } from './storage/FileStorage.js';
export type { FileStorageOptions } from './storage/FileStorage.js';
export { DrizzleStorage } from './storage/DrizzleStorage.js';
export type { DrizzleStorageOptions } from './storage/DrizzleStorage.js';

// Public types — these match the wire protocol exactly.
export type {
  IStorage,
  SaveReportInput,
  BugFabAdapterOptions,
  GitHubSyncOptions,
  ViewerPermissions,
  BugReportSubmission,
  BugReportDetail,
  BugReportSummary,
  BugReportListResponse,
  BugReportListStats,
  BugReportIntakeResponse,
  StatusUpdateRequest,
  BulkCloseResponse,
  BulkArchiveResponse,
  ListFilters,
  Severity,
  Status,
  ReportType,
  Reporter,
  BugReportContext,
  LifecycleEvent,
  LifecycleAction,
  StoredReport
} from './types.js';

// Validation helpers (useful for consumers writing their own preflight checks).
export {
  isValidSeverity,
  isValidStatus,
  isValidReportType,
  isSupportedProtocolVersion,
  isValidPngMagic,
  validateSubmission,
  validateStatusUpdate,
  VALID_SEVERITIES,
  VALID_STATUSES,
  VALID_REPORT_TYPES,
  SUPPORTED_PROTOCOL_VERSIONS,
  MAX_SCREENSHOT_BYTES,
  MAX_TITLE_LENGTH,
  MAX_REPORTER_FIELD_LENGTH
} from './validation.js';
