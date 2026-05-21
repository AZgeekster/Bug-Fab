// Re-export of the IStorage interface. See ../types.ts for the canonical
// definition. Adapter authors implement this contract; the FileStorage and
// DrizzleStorage classes in this folder are reference implementations.
export type {
  IStorage,
  SaveReportInput,
  StoredReport,
  BugReportDetail,
  BugReportSummary,
  BugReportListStats,
  ListFilters,
  Status,
  Severity,
  ReportType,
  LifecycleEvent,
  Reporter,
  BugReportContext
} from '../types.js';
