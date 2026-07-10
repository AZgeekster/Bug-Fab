import Foundation
import NIOConcurrencyHelpers
import Vapor

// On-disk file backend. Mirrors the Python reference (`bug_fab/storage/files.py`):
//   storage_dir/
//     index.json            — denormalized listing, append-only growth
//     bug-NNN.json          — full payload
//     bug-NNN.png           — screenshot bytes
//     archive/              — same layout for archived rows
//
// Concurrency uses NIOLock; multi-process safety needs an external lock
// (or use BugFabFluentStorage). Writes are atomic via tmp+replace.

public final class BugFabFileStorage: BugFabStorage, @unchecked Sendable {
    public let storageDirectory: URL
    public let archiveDirectory: URL
    public let idPrefix: String
    private let indexURL: URL
    private let lock = NIOLock()

    public init(storageDirectory: URL, idPrefix: String = "") throws {
        self.storageDirectory = storageDirectory
        self.archiveDirectory = storageDirectory.appendingPathComponent("archive", isDirectory: true)
        self.idPrefix = idPrefix
        self.indexURL = storageDirectory.appendingPathComponent("index.json", isDirectory: false)
        let fm = FileManager.default
        try fm.createDirectory(at: storageDirectory, withIntermediateDirectories: true)
        try fm.createDirectory(at: archiveDirectory, withIntermediateDirectories: true)
    }

    // MARK: BugFabStorage

    public func saveReport(metadata: [String: BugFabJSONValue], screenshotBytes: Data)
        async throws -> String
    {
        try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            var index = readIndex()
            let n = index.nextNumber
            let id = String(format: "bug-\(idPrefix)%03d", n)
            let now = Self.nowIso()
            let report = Self.buildReport(id: id, metadata: metadata, now: now)
            try writeScreenshot(id: id, data: screenshotBytes)
            try writeReport(id: id, data: report)
            index.reports.append(Self.indexEntry(for: report))
            index.nextNumber += 1
            try writeIndex(index)
            return id
        }
    }

    public func getReport(id: String) async throws -> BugFabBugReportDetail? {
        guard Self.isValidId(id) else { return nil }
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            guard let raw = try readReport(id: id) else { return nil }
            return try Self.coerceDetail(raw)
        }
    }

    public func listReports(
        filters: [String: String],
        page: Int,
        pageSize: Int
    ) async throws -> (items: [BugFabBugReportSummary], total: Int) {
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            let index = readIndex()
            lock.unlock()

            var matched = index.reports.filter { entry in
                Self.matches(entry: entry, filters: filters)
            }
            matched.sort { a, b in (a.createdAt) > (b.createdAt) }
            let total = matched.count
            let start = max(0, (page - 1) * pageSize)
            let end = min(matched.count, start + pageSize)
            let page = (start < end) ? Array(matched[start..<end]) : []
            let items = try page.map { entry -> BugFabBugReportSummary in
                let data = try JSONEncoder().encode(entry)
                return try JSONDecoder().decode(BugFabBugReportSummary.self, from: data)
            }
            return (items, total)
        }
    }

    public func getScreenshot(id: String) async throws -> Data? {
        guard Self.isValidId(id) else { return nil }
        let fm = FileManager.default
        let live = storageDirectory.appendingPathComponent("\(id).png", isDirectory: false)
        if fm.fileExists(atPath: live.path) {
            return try Data(contentsOf: live)
        }
        let archived = archiveDirectory.appendingPathComponent("\(id).png", isDirectory: false)
        if fm.fileExists(atPath: archived.path) {
            return try Data(contentsOf: archived)
        }
        return nil
    }

    public func updateStatus(
        id: String, status: String, fixCommit: String, fixDescription: String, by: String
    ) async throws -> BugFabBugReportDetail? {
        guard Self.isValidId(id) else { return nil }
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            guard var raw = try readReport(id: id) else { return nil }
            let now = Self.nowIso()
            raw["status"] = .string(status)
            raw["updated_at"] = .string(now)
            var lifecycle: [BugFabJSONValue] = []
            if case .array(let existing) = raw["lifecycle"] ?? .null { lifecycle = existing }
            lifecycle.append(
                .object([
                    "action": .string("status_changed"),
                    "by": .string(by),
                    "at": .string(now),
                    "status": .string(status),
                    "fix_commit": .string(fixCommit),
                    "fix_description": .string(fixDescription),
                ]))
            raw["lifecycle"] = .array(lifecycle)
            try writeReport(id: id, data: raw)
            updateIndexEntry(id: id, status: status)
            return try Self.coerceDetail(raw)
        }
    }

    public func deleteReport(id: String) async throws -> Bool {
        guard Self.isValidId(id) else { return false }
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            var removed = false
            let fm = FileManager.default
            for path in candidatePaths(for: id) {
                if fm.fileExists(atPath: path.path) {
                    try fm.removeItem(at: path)
                    removed = true
                }
            }
            if removed {
                var index = readIndex()
                index.reports.removeAll { $0.id == id }
                try writeIndex(index)
            }
            return removed
        }
    }

    public func archiveReport(id: String) async throws -> Bool {
        guard Self.isValidId(id) else { return false }
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            return try archiveOne(id: id)
        }
    }

    public func bulkCloseFixed(by: String) async throws -> Int {
        lock.lock()
        let toClose = readIndex().reports.filter { $0.status == "fixed" }.map(\.id)
        lock.unlock()
        var closed = 0
        for id in toClose {
            if try await updateStatus(
                id: id, status: "closed", fixCommit: "", fixDescription: "", by: by
            ) != nil {
                closed += 1
            }
        }
        return closed
    }

    public func bulkArchiveClosed() async throws -> Int {
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            let ids = readIndex().reports.filter { $0.status == "closed" }.map(\.id)
            var archived = 0
            for id in ids {
                if try archiveOne(id: id) { archived += 1 }
            }
            return archived
        }
    }

    public func setGithubLink(id: String, issueNumber: Int, issueUrl: String) async throws
        -> BugFabBugReportDetail?
    {
        guard Self.isValidId(id) else { return nil }
        return try await Self.detachedThrowing { [self] in
            lock.lock()
            defer { lock.unlock() }
            guard var raw = try readReport(id: id) else { return nil }
            raw["github_issue_number"] = .int(issueNumber)
            raw["github_issue_url"] = .string(issueUrl)
            try writeReport(id: id, data: raw)
            updateIndexEntry(id: id, githubIssueUrl: issueUrl)
            return try Self.coerceDetail(raw)
        }
    }

    // MARK: - Private helpers

    struct Index: Codable {
        var reports: [IndexEntry]
        var nextNumber: Int

        enum CodingKeys: String, CodingKey {
            case reports
            case nextNumber = "next_number"
        }
    }

    struct IndexEntry: Codable {
        var id: String
        var title: String
        var reportType: String
        var severity: String
        var status: String
        var module: String
        var createdAt: String
        var hasScreenshot: Bool
        var githubIssueUrl: String?
        // Optional so an index.json written before this field existed still
        // decodes (a missing key would otherwise fail the whole index read
        // and lose every entry). Fresh writes always populate it.
        var environment: String?

        enum CodingKeys: String, CodingKey {
            case id, title
            case reportType = "report_type"
            case severity, status, module
            case createdAt = "created_at"
            case hasScreenshot = "has_screenshot"
            case githubIssueUrl = "github_issue_url"
            case environment
        }
    }

    private func readIndex() -> Index {
        let fm = FileManager.default
        guard fm.fileExists(atPath: indexURL.path),
            let data = try? Data(contentsOf: indexURL)
        else {
            return Index(reports: [], nextNumber: 1)
        }
        do {
            return try JSONDecoder().decode(Index.self, from: data)
        } catch {
            return Index(reports: [], nextNumber: 1)
        }
    }

    private func writeIndex(_ index: Index) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(index)
        try Self.atomicWrite(data: data, to: indexURL)
    }

    private func readReport(id: String) throws -> [String: BugFabJSONValue]? {
        let fm = FileManager.default
        let primary = storageDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let archived = archiveDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let url: URL
        if fm.fileExists(atPath: primary.path) { url = primary }
        else if fm.fileExists(atPath: archived.path) { url = archived }
        else { return nil }
        let bytes = try Data(contentsOf: url)
        let decoded = try JSONDecoder().decode([String: BugFabJSONValue].self, from: bytes)
        return decoded
    }

    private func writeReport(id: String, data: [String: BugFabJSONValue]) throws {
        let fm = FileManager.default
        let primary = storageDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let archived = archiveDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let url: URL
        if fm.fileExists(atPath: primary.path) {
            url = primary
        } else if fm.fileExists(atPath: archived.path) {
            url = archived
        } else {
            url = primary
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let payload = try encoder.encode(data)
        try Self.atomicWrite(data: payload, to: url)
    }

    private func writeScreenshot(id: String, data: Data) throws {
        let url = storageDirectory.appendingPathComponent("\(id).png", isDirectory: false)
        try Self.atomicWrite(data: data, to: url)
    }

    private func candidatePaths(for id: String) -> [URL] {
        [
            storageDirectory.appendingPathComponent("\(id).json", isDirectory: false),
            storageDirectory.appendingPathComponent("\(id).png", isDirectory: false),
            archiveDirectory.appendingPathComponent("\(id).json", isDirectory: false),
            archiveDirectory.appendingPathComponent("\(id).png", isDirectory: false),
        ]
    }

    private func updateIndexEntry(
        id: String, status: String? = nil, githubIssueUrl: String? = nil
    ) {
        var index = readIndex()
        for i in 0..<index.reports.count where index.reports[i].id == id {
            if let s = status { index.reports[i].status = s }
            if let u = githubIssueUrl { index.reports[i].githubIssueUrl = u }
        }
        try? writeIndex(index)
    }

    private func archiveOne(id: String) throws -> Bool {
        let fm = FileManager.default
        let jsonSrc = storageDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let pngSrc = storageDirectory.appendingPathComponent("\(id).png", isDirectory: false)
        let jsonDst = archiveDirectory.appendingPathComponent("\(id).json", isDirectory: false)
        let pngDst = archiveDirectory.appendingPathComponent("\(id).png", isDirectory: false)
        let hadJson = fm.fileExists(atPath: jsonSrc.path)
        let hadPng = fm.fileExists(atPath: pngSrc.path)
        if !hadJson && !hadPng { return false }
        if hadJson {
            if fm.fileExists(atPath: jsonDst.path) {
                try fm.removeItem(at: jsonDst)
            }
            try fm.moveItem(at: jsonSrc, to: jsonDst)
        }
        if hadPng {
            if fm.fileExists(atPath: pngDst.path) {
                try fm.removeItem(at: pngDst)
            }
            try fm.moveItem(at: pngSrc, to: pngDst)
        }
        var index = readIndex()
        index.reports.removeAll { $0.id == id }
        try writeIndex(index)
        return true
    }

    private static func atomicWrite(data: Data, to url: URL) throws {
        let tmp = url.appendingPathExtension("tmp")
        try data.write(to: tmp, options: [.atomic])
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
        try FileManager.default.moveItem(at: tmp, to: url)
    }

    private static func nowIso() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: Date())
    }

    static func isValidId(_ id: String) -> Bool {
        // Matches the protocol regex `^bug-[A-Za-z]?\d{1,12}$`.
        let pattern = "^bug-[A-Za-z]?\\d{1,12}$"
        return id.range(of: pattern, options: .regularExpression) != nil
    }

    static func matches(entry: IndexEntry, filters: [String: String]) -> Bool {
        for key in ["status", "severity", "module", "report_type", "environment"] {
            guard let wanted = filters[key], !wanted.isEmpty else { continue }
            let actual: String
            switch key {
            case "status": actual = entry.status
            case "severity": actual = entry.severity
            case "module": actual = entry.module
            case "report_type": actual = entry.reportType
            case "environment": actual = entry.environment ?? ""
            default: continue
            }
            if actual != wanted { return false }
        }
        return true
    }

    static func buildReport(
        id: String,
        metadata: [String: BugFabJSONValue],
        now: String
    ) -> [String: BugFabJSONValue] {
        var context: [String: BugFabJSONValue] = [:]
        if case .object(let c) = metadata["context"] ?? .null { context = c }
        var reporter: [String: BugFabJSONValue] = [:]
        if case .object(let r) = metadata["reporter"] ?? .null { reporter = r }

        func str(_ key: String, from src: [String: BugFabJSONValue]? = nil) -> String {
            let dict = src ?? metadata
            if case .string(let s) = dict[key] ?? .null { return s }
            return ""
        }

        let title = str("title")
        let clientTs = str("client_ts")
        let reportType = (metadata["report_type"].flatMap { v -> String? in
            if case .string(let s) = v { return s } else { return nil }
        }) ?? "bug"
        let description = str("description")
        let expectedBehavior = str("expected_behavior")
        let severity =
            (metadata["severity"].flatMap { v -> String? in
                if case .string(let s) = v { return s } else { return nil }
            }) ?? "medium"
        var tags: [BugFabJSONValue] = []
        if case .array(let t) = metadata["tags"] ?? .null { tags = t }

        let module: String = {
            if case .string(let m) = metadata["module"] ?? .null, !m.isEmpty { return m }
            return str("module", from: context)
        }()
        let environment: String = {
            if case .string(let e) = metadata["environment"] ?? .null, !e.isEmpty { return e }
            return str("environment", from: context)
        }()
        let serverUA = str("server_user_agent")
        let clientUA = str("user_agent", from: context)

        let lifecycle: [BugFabJSONValue] = [
            .object([
                "action": .string("created"),
                "by": .string(str("submitted_by")),
                "at": .string(now),
                "status": .string("open"),
                "fix_commit": .string(""),
                "fix_description": .string(""),
            ])
        ]

        let report: [String: BugFabJSONValue] = [
            "id": .string(id),
            "protocol_version": .string(str("protocol_version").isEmpty
                ? "0.1" : str("protocol_version")),
            "title": .string(title),
            "client_ts": .string(clientTs),
            "report_type": .string(reportType),
            "description": .string(description),
            "expected_behavior": .string(expectedBehavior),
            "severity": .string(severity),
            "status": .string("open"),
            "tags": .array(tags),
            "reporter": .object([
                "name": .string(str("name", from: reporter)),
                "email": .string(str("email", from: reporter)),
                "user_id": .string(str("user_id", from: reporter)),
            ]),
            "context": .object(context),
            "module": .string(module),
            "created_at": .string(now),
            "updated_at": .string(now),
            "has_screenshot": .bool(true),
            "server_user_agent": .string(serverUA),
            "client_reported_user_agent": .string(clientUA),
            "environment": .string(environment),
            "github_issue_url": .null,
            "github_issue_number": .null,
            "lifecycle": .array(lifecycle),
        ]
        return report
    }

    static func indexEntry(for report: [String: BugFabJSONValue]) -> IndexEntry {
        func s(_ k: String) -> String {
            if case .string(let v) = report[k] ?? .null { return v }
            return ""
        }
        return IndexEntry(
            id: s("id"), title: s("title"),
            reportType: s("report_type"), severity: s("severity"),
            status: s("status"), module: s("module"),
            createdAt: s("created_at"), hasScreenshot: true,
            githubIssueUrl: nil, environment: s("environment")
        )
    }

    static func coerceDetail(_ raw: [String: BugFabJSONValue]) throws -> BugFabBugReportDetail {
        let data = try JSONEncoder().encode(raw)
        return try JSONDecoder().decode(BugFabBugReportDetail.self, from: data)
    }

    // Vapor likes us to keep blocking work off the EventLoop. We bounce
    // through a detached Task so file I/O doesn't stall the request loop.
    static func detachedThrowing<T: Sendable>(_ work: @Sendable @escaping () throws -> T)
        async throws -> T
    {
        try await Task.detached(priority: .userInitiated, operation: work).value
    }
}
