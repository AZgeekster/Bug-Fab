// Re-export of the IStorage contract for convenient import from
// `bug-fab-express/storage`. The canonical interface lives in `../types.ts`.
//
// Reference: https://github.com/AZgeekster/Bug-Fab/blob/main/docs/ADAPTERS_REGISTRY.md
//   § "Adapter authorship checklist", item 3.

export type {
  IStorage,
  SaveReportInput,
  BugReportSummary,
  BugReportDetail,
  BugReportListStats,
  ListFilters,
  Status,
  Severity,
  ReportType,
  Reporter,
  BugReportContext,
  LifecycleEvent,
  LifecycleAction,
} from '../types.js'
