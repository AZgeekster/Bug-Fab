import Fluent
import FluentSQL
import Foundation
import Vapor

// Fluent-backed storage. Postgres for production
// (`fluent-postgres-driver`); SQLite for tests (`fluent-sqlite-driver`).
// Schema columns match the Python SQLAlchemy / SQLModel reference so a
// shared collector can use either adapter against the same database
// (subject to driver-specific migration sequencing — see
// `MIGRATION_NOTES.md`).

public final class BugFabReport: Model, @unchecked Sendable {
    public static let schema = "bug_fab_reports"

    @ID(custom: "id", generatedBy: .user)
    public var id: String?

    @Field(key: "metadata_json")
    public var metadataJson: String

    @Field(key: "screenshot")
    public var screenshot: Data?

    @Field(key: "status")
    public var status: String

    @Field(key: "severity")
    public var severity: String

    @Field(key: "module")
    public var module: String

    @Field(key: "report_type")
    public var reportType: String

    @Field(key: "environment")
    public var environment: String

    @Field(key: "title")
    public var title: String

    @Field(key: "github_issue_url")
    public var githubIssueUrl: String?

    @Field(key: "github_issue_number")
    public var githubIssueNumber: Int?

    @Field(key: "created_at")
    public var createdAt: String

    @Field(key: "updated_at")
    public var updatedAt: String

    @Field(key: "archived_at")
    public var archivedAt: String?

    public init() {}
}

public struct CreateBugFabReport: AsyncMigration {
    public init() {}
    public func prepare(on database: Database) async throws {
        try await database.schema(BugFabReport.schema)
            .field("id", .string, .identifier(auto: false))
            .field("metadata_json", .string, .required)
            .field("screenshot", .data)
            .field("status", .string, .required)
            .field("severity", .string, .required)
            .field("module", .string, .required)
            .field("report_type", .string, .required)
            .field("environment", .string, .required)
            .field("title", .string, .required)
            .field("github_issue_url", .string)
            .field("github_issue_number", .int)
            .field("created_at", .string, .required)
            .field("updated_at", .string, .required)
            .field("archived_at", .string)
            .create()
    }
    public func revert(on database: Database) async throws {
        try await database.schema(BugFabReport.schema).delete()
    }
}

/// Single-row allocator for sequential `bug-NNN` ids. Incremented by an
/// atomic `UPDATE ... SET last_value = last_value + 1` so a delete cannot
/// rewind it (see `CreateBugFabIdCounter` for the rationale).
public final class BugFabIdCounter: Model, @unchecked Sendable {
    public static let schema = "bug_fab_id_counter"

    @ID(custom: "id", generatedBy: .user)
    public var id: Int?

    @Field(key: "last_value")
    public var lastValue: Int

    public init() {}
}

public struct CreateBugFabIdCounter: AsyncMigration {
    public init() {}
    public func prepare(on database: Database) async throws {
        try await database.schema(BugFabIdCounter.schema)
            .field("id", .int, .identifier(auto: false))
            .field("last_value", .int, .required)
            .create()
        // Seed the single row the allocator increments. `saveReport` assumes
        // it exists; without it the first UPDATE would touch zero rows and the
        // read-back would be empty.
        let counter = BugFabIdCounter()
        counter.id = 1
        counter.lastValue = 0
        try await counter.create(on: database)
    }
    public func revert(on database: Database) async throws {
        try await database.schema(BugFabIdCounter.schema).delete()
    }
}

public final class BugFabFluentStorage: BugFabStorage, @unchecked Sendable {
    let app: Application
    let idPrefix: String

    public init(app: Application, idPrefix: String = "") {
        self.app = app
        self.idPrefix = idPrefix
    }

    var db: Database { app.db }

    public func saveReport(metadata: [String: BugFabJSONValue], screenshotBytes: Data)
        async throws -> String
    {
        let now = Self.nowIso()
        // Allocate the id and insert the row in one transaction: the id comes
        // from an atomic counter, not COUNT(*)+1 (a delete would make the next
        // id collide with a live row), and doing both under one transaction
        // means a rolled-back insert cannot leave a live report holding a
        // skipped number.
        return try await db.transaction { tx in
            let n = try await self.nextNumber(on: tx)
            let id = String(format: "bug-\(self.idPrefix)%03d", n)

            let fullReport = BugFabFileStorage.buildReport(id: id, metadata: metadata, now: now)
            let json = try Self.encodeJSON(fullReport)
            let row = BugFabReport()
            row.id = id
            row.metadataJson = json
            row.screenshot = screenshotBytes
            row.status = "open"
            row.severity = self.stringFor(fullReport["severity"]) ?? "medium"
            row.module = self.stringFor(fullReport["module"]) ?? ""
            row.reportType = self.stringFor(fullReport["report_type"]) ?? "bug"
            row.environment = self.stringFor(fullReport["environment"]) ?? ""
            row.title = self.stringFor(fullReport["title"]) ?? ""
            row.githubIssueUrl = nil
            row.githubIssueNumber = nil
            row.createdAt = now
            row.updatedAt = now
            row.archivedAt = nil
            try await row.create(on: tx)
            return id
        }
    }

    private struct CounterValue: Decodable {
        let lastValue: Int
        enum CodingKeys: String, CodingKey { case lastValue = "last_value" }
    }

    /// Allocate the next report number by incrementing the single counter row.
    ///
    /// A single atomic `UPDATE ... SET last_value = last_value + 1` — never a
    /// `SELECT ... FOR UPDATE`, which is a syntax error on the SQLite driver
    /// this adapter ships for tests. SQLite serializes writers so the increment
    /// cannot be lost; Postgres holds a row lock for the statement's duration.
    /// The read-back is read-your-own-write within the enclosing transaction.
    private func nextNumber(on database: Database) async throws -> Int {
        guard let sql = database as? any SQLDatabase else {
            throw Abort(.internalServerError, reason: "Fluent database is not SQL-capable")
        }
        try await sql.raw("UPDATE bug_fab_id_counter SET last_value = last_value + 1 WHERE id = 1")
            .run()
        let rows = try await sql.raw("SELECT last_value FROM bug_fab_id_counter WHERE id = 1")
            .all(decoding: CounterValue.self)
        guard let value = rows.first?.lastValue else {
            throw Abort(.internalServerError, reason: "bug_fab_id_counter row missing")
        }
        return value
    }

    public func getReport(id: String) async throws -> BugFabBugReportDetail? {
        guard BugFabFileStorage.isValidId(id) else { return nil }
        guard let row = try await BugFabReport.find(id, on: db) else { return nil }
        return try Self.detailFromRow(row)
    }

    public func listReports(
        filters: [String: String], page: Int, pageSize: Int
    ) async throws -> (items: [BugFabBugReportSummary], total: Int) {
        var query = BugFabReport.query(on: db).filter(\.$archivedAt == nil)
        if let s = filters["status"] { query = query.filter(\.$status == s) }
        if let s = filters["severity"] { query = query.filter(\.$severity == s) }
        if let s = filters["module"] { query = query.filter(\.$module == s) }
        if let s = filters["environment"] { query = query.filter(\.$environment == s) }
        let total = try await query.count()
        let rows = try await query
            .sort(\.$createdAt, .descending)
            .range((max(0, (page - 1) * pageSize))..<((max(0, (page - 1) * pageSize)) + pageSize))
            .all()
        let items = try rows.map { row -> BugFabBugReportSummary in
            BugFabBugReportSummary(
                id: row.id ?? "",
                title: row.title,
                reportType: row.reportType,
                severity: row.severity,
                status: row.status,
                module: row.module,
                createdAt: row.createdAt,
                hasScreenshot: row.screenshot != nil,
                githubIssueUrl: row.githubIssueUrl
            )
        }
        return (items, total)
    }

    public func getScreenshot(id: String) async throws -> Data? {
        guard BugFabFileStorage.isValidId(id) else { return nil }
        guard let row = try await BugFabReport.find(id, on: db) else { return nil }
        return row.screenshot
    }

    public func updateStatus(
        id: String, status: String, fixCommit: String, fixDescription: String, by: String
    ) async throws -> BugFabBugReportDetail? {
        guard BugFabFileStorage.isValidId(id) else { return nil }
        guard let row = try await BugFabReport.find(id, on: db) else { return nil }
        let now = Self.nowIso()
        var current = try Self.decodeJSON(row.metadataJson)
        current["status"] = .string(status)
        current["updated_at"] = .string(now)
        var lifecycle: [BugFabJSONValue] = []
        if case .array(let arr) = current["lifecycle"] ?? .null { lifecycle = arr }
        lifecycle.append(
            .object([
                "action": .string("status_changed"),
                "by": .string(by),
                "at": .string(now),
                "status": .string(status),
                "fix_commit": .string(fixCommit),
                "fix_description": .string(fixDescription),
            ]))
        current["lifecycle"] = .array(lifecycle)
        row.status = status
        row.updatedAt = now
        row.metadataJson = try Self.encodeJSON(current)
        try await row.update(on: db)
        return try Self.detailFromRow(row)
    }

    public func deleteReport(id: String) async throws -> Bool {
        guard BugFabFileStorage.isValidId(id) else { return false }
        guard let row = try await BugFabReport.find(id, on: db) else { return false }
        try await row.delete(on: db)
        return true
    }

    public func archiveReport(id: String) async throws -> Bool {
        guard BugFabFileStorage.isValidId(id) else { return false }
        guard let row = try await BugFabReport.find(id, on: db) else { return false }
        row.archivedAt = Self.nowIso()
        try await row.update(on: db)
        return true
    }

    public func bulkCloseFixed(by: String) async throws -> Int {
        let rows = try await BugFabReport.query(on: db)
            .filter(\.$status == "fixed")
            .filter(\.$archivedAt == nil)
            .all()
        var n = 0
        for row in rows {
            guard let id = row.id else { continue }
            if try await updateStatus(
                id: id, status: "closed", fixCommit: "", fixDescription: "", by: by
            ) != nil { n += 1 }
        }
        return n
    }

    public func bulkArchiveClosed() async throws -> Int {
        let rows = try await BugFabReport.query(on: db)
            .filter(\.$status == "closed")
            .filter(\.$archivedAt == nil)
            .all()
        let now = Self.nowIso()
        for row in rows { row.archivedAt = now; try await row.update(on: db) }
        return rows.count
    }

    public func setGithubLink(id: String, issueNumber: Int, issueUrl: String) async throws
        -> BugFabBugReportDetail?
    {
        guard BugFabFileStorage.isValidId(id) else { return nil }
        guard let row = try await BugFabReport.find(id, on: db) else { return nil }
        row.githubIssueNumber = issueNumber
        row.githubIssueUrl = issueUrl
        var data = try Self.decodeJSON(row.metadataJson)
        data["github_issue_number"] = .int(issueNumber)
        data["github_issue_url"] = .string(issueUrl)
        row.metadataJson = try Self.encodeJSON(data)
        try await row.update(on: db)
        return try Self.detailFromRow(row)
    }

    // MARK: - Helpers

    static func detailFromRow(_ row: BugFabReport) throws -> BugFabBugReportDetail {
        let bytes = Data(row.metadataJson.utf8)
        var detail = try JSONDecoder().decode(BugFabBugReportDetail.self, from: bytes)
        // Trust the column values for the small set of fields the index
        // denormalizes — they may have been updated independently of the
        // JSON blob (status updates write both).
        detail.status = row.status
        detail.severity = row.severity
        detail.module = row.module
        detail.reportType = row.reportType
        detail.environment = row.environment
        detail.title = row.title
        detail.createdAt = row.createdAt
        detail.updatedAt = row.updatedAt
        detail.hasScreenshot = row.screenshot != nil
        detail.githubIssueUrl = row.githubIssueUrl
        detail.githubIssueNumber = row.githubIssueNumber
        return detail
    }

    static func encodeJSON(_ value: [String: BugFabJSONValue]) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(value)
        return String(decoding: data, as: UTF8.self)
    }

    static func decodeJSON(_ s: String) throws -> [String: BugFabJSONValue] {
        try JSONDecoder().decode([String: BugFabJSONValue].self, from: Data(s.utf8))
    }

    static func nowIso() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: Date())
    }

    private func stringFor(_ v: BugFabJSONValue?) -> String? {
        if case .string(let s) = v ?? .null { return s }
        return nil
    }
}
