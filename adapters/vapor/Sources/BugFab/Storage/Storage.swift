import Foundation
import Vapor

// Storage protocol — both `BugFabFileStorage` and `BugFabFluentStorage`
// implement this. async throws everywhere because Fluent + filesystem I/O
// both want the EventLoop. The Python reference uses an ABC; Swift's
// equivalent is a protocol with an associated `Sendable` conformance.

public struct BugFabSavedReport: Sendable {
    public let id: String
    public let detail: BugFabBugReportDetail
}

public protocol BugFabStorage: Sendable {
    /// Persist a new report. `metadata` is the on-the-wire JSON (post-
    /// validation); `screenshotBytes` is verified PNG.
    func saveReport(metadata: [String: BugFabJSONValue], screenshotBytes: Data) async throws
        -> String

    /// Fetch by id, or nil when not found.
    func getReport(id: String) async throws -> BugFabBugReportDetail?

    /// Filterable listing — keys: status, severity, module, environment.
    func listReports(filters: [String: String], page: Int, pageSize: Int) async throws -> (
        items: [BugFabBugReportSummary], total: Int
    )

    /// Returns the screenshot bytes (and the discovered mime, always
    /// `image/png` in v0.1) or nil when the file is missing.
    func getScreenshot(id: String) async throws -> Data?

    /// Append a `status_changed` lifecycle event and update the status.
    func updateStatus(
        id: String, status: String, fixCommit: String, fixDescription: String, by: String
    ) async throws -> BugFabBugReportDetail?

    /// Hard delete.
    func deleteReport(id: String) async throws -> Bool

    /// Move from live → archive subdir / set archived_at.
    func archiveReport(id: String) async throws -> Bool

    /// Transition every `fixed` report to `closed`.
    func bulkCloseFixed(by: String) async throws -> Int

    /// Archive every `closed` report. Returns the count moved.
    func bulkArchiveClosed() async throws -> Int

    /// Stamp a github issue link onto an existing report.
    func setGithubLink(id: String, issueNumber: Int, issueUrl: String) async throws
        -> BugFabBugReportDetail?
}
