import Foundation
import Vapor

// Wire-protocol schema mirror, hand-translated from
// `bug_fab/schemas.py` (Pydantic v2). Names are `snake_case` on the wire,
// `camelCase` in Swift; CodingKeys do the translation.
//
// IMPORTANT: keep these aligned with `repo/docs/protocol-schema.json`.
// `Severity` and `Status` use strict Codable with manual validation
// (see decode init) so unknown values produce a 422 — silent coercion
// fails conformance.

public enum BugFabSeverity: String, Codable, CaseIterable, Sendable {
    case low, medium, high, critical

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        guard let value = BugFabSeverity(rawValue: raw) else {
            throw BugFabValidationError.invalidEnum(
                field: "severity",
                value: raw,
                allowed: BugFabSeverity.allCases.map { $0.rawValue }
            )
        }
        self = value
    }
}

public enum BugFabStatus: String, Codable, CaseIterable, Sendable {
    case open, investigating, fixed, closed

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        guard let value = BugFabStatus(rawValue: raw) else {
            throw BugFabValidationError.invalidEnum(
                field: "status",
                value: raw,
                allowed: BugFabStatus.allCases.map { $0.rawValue }
            )
        }
        self = value
    }
}

public enum BugFabReportType: String, Codable, CaseIterable, Sendable {
    case bug
    case feature_request

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        guard let value = BugFabReportType(rawValue: raw) else {
            throw BugFabValidationError.invalidEnum(
                field: "report_type",
                value: raw,
                allowed: BugFabReportType.allCases.map { $0.rawValue }
            )
        }
        self = value
    }
}

// MARK: - Reporter

public struct BugFabReporter: Codable, Sendable {
    public var name: String
    public var email: String
    public var userId: String

    public init(name: String = "", email: String = "", userId: String = "") {
        self.name = name
        self.email = email
        self.userId = userId
    }

    enum CodingKeys: String, CodingKey {
        case name
        case email
        case userId = "user_id"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        let email = try c.decodeIfPresent(String.self, forKey: .email) ?? ""
        let userId = try c.decodeIfPresent(String.self, forKey: .userId) ?? ""
        // 256-char cap per protocol § Reporter.
        for (field, value) in [("name", name), ("email", email), ("user_id", userId)] {
            if value.count > 256 {
                throw BugFabValidationError.fieldTooLong(field: "reporter.\(field)", limit: 256)
            }
        }
        self.name = name
        self.email = email
        self.userId = userId
    }
}

// MARK: - Context

// Context is an extra-allowed object (Pydantic `extra="allow"`). We model
// the known fields and stash the rest in `extras` so round-trip is lossless.
public struct BugFabContext: Codable, Sendable {
    public var url: String
    public var module: String
    public var userAgent: String
    public var viewportWidth: Int
    public var viewportHeight: Int
    public var consoleErrors: [BugFabJSONValue]
    public var networkLog: [BugFabJSONValue]
    public var sourceMapping: [String: BugFabJSONValue]
    public var appVersion: String
    public var environment: String
    public var extras: [String: BugFabJSONValue]

    public init(
        url: String = "",
        module: String = "",
        userAgent: String = "",
        viewportWidth: Int = 0,
        viewportHeight: Int = 0,
        consoleErrors: [BugFabJSONValue] = [],
        networkLog: [BugFabJSONValue] = [],
        sourceMapping: [String: BugFabJSONValue] = [:],
        appVersion: String = "",
        environment: String = "",
        extras: [String: BugFabJSONValue] = [:]
    ) {
        self.url = url
        self.module = module
        self.userAgent = userAgent
        self.viewportWidth = viewportWidth
        self.viewportHeight = viewportHeight
        self.consoleErrors = consoleErrors
        self.networkLog = networkLog
        self.sourceMapping = sourceMapping
        self.appVersion = appVersion
        self.environment = environment
        self.extras = extras
    }

    private static let knownKeys: Set<String> = [
        "url", "module", "user_agent",
        "viewport_width", "viewport_height",
        "console_errors", "network_log",
        "source_mapping", "app_version", "environment",
    ]

    public init(from decoder: Decoder) throws {
        let dyn = try decoder.container(keyedBy: BugFabJSONValue.DynamicKey.self)

        func str(_ key: String) throws -> String {
            guard let k = BugFabJSONValue.DynamicKey(stringValue: key) else { return "" }
            return (try? dyn.decodeIfPresent(String.self, forKey: k)) ?? ""
        }
        func int(_ key: String) throws -> Int {
            guard let k = BugFabJSONValue.DynamicKey(stringValue: key) else { return 0 }
            return (try? dyn.decodeIfPresent(Int.self, forKey: k)) ?? 0
        }
        func arr(_ key: String) throws -> [BugFabJSONValue] {
            guard let k = BugFabJSONValue.DynamicKey(stringValue: key) else { return [] }
            return (try? dyn.decodeIfPresent([BugFabJSONValue].self, forKey: k)) ?? []
        }
        func obj(_ key: String) throws -> [String: BugFabJSONValue] {
            guard let k = BugFabJSONValue.DynamicKey(stringValue: key) else { return [:] }
            return (try? dyn.decodeIfPresent([String: BugFabJSONValue].self, forKey: k)) ?? [:]
        }

        self.url = try str("url")
        self.module = try str("module")
        self.userAgent = try str("user_agent")
        self.viewportWidth = try int("viewport_width")
        self.viewportHeight = try int("viewport_height")
        self.consoleErrors = try arr("console_errors")
        self.networkLog = try arr("network_log")
        self.sourceMapping = try obj("source_mapping")
        self.appVersion = try str("app_version")
        self.environment = try str("environment")

        var extras: [String: BugFabJSONValue] = [:]
        for key in dyn.allKeys where !Self.knownKeys.contains(key.stringValue) {
            if let v = try? dyn.decode(BugFabJSONValue.self, forKey: key) {
                extras[key.stringValue] = v
            }
        }
        self.extras = extras
    }

    public func encode(to encoder: Encoder) throws {
        var dyn = encoder.container(keyedBy: BugFabJSONValue.DynamicKey.self)
        func key(_ s: String) -> BugFabJSONValue.DynamicKey { .init(stringValue: s)! }
        try dyn.encode(url, forKey: key("url"))
        try dyn.encode(module, forKey: key("module"))
        try dyn.encode(userAgent, forKey: key("user_agent"))
        try dyn.encode(viewportWidth, forKey: key("viewport_width"))
        try dyn.encode(viewportHeight, forKey: key("viewport_height"))
        try dyn.encode(consoleErrors, forKey: key("console_errors"))
        try dyn.encode(networkLog, forKey: key("network_log"))
        try dyn.encode(sourceMapping, forKey: key("source_mapping"))
        try dyn.encode(appVersion, forKey: key("app_version"))
        try dyn.encode(environment, forKey: key("environment"))
        for (k, v) in extras {
            if let dk = BugFabJSONValue.DynamicKey(stringValue: k) {
                try dyn.encode(v, forKey: dk)
            }
        }
    }
}

// MARK: - Submission payload

// Per § "POST /bug-reports" metadata schema. `protocol_version` MUST equal
// the literal "0.1"; mismatches are rejected with 400
// `unsupported_protocol_version` *outside* this decode (the controller
// checks it before delegating to JSONDecoder so the error code matches the
// protocol's distinction between 400 and 422).
public struct BugFabBugReportCreate: Codable, Sendable {
    public var protocolVersion: String
    public var title: String
    public var clientTs: String
    public var reportType: BugFabReportType
    public var description: String
    public var expectedBehavior: String
    public var severity: BugFabSeverity
    public var tags: [String]
    public var reporter: BugFabReporter
    public var context: BugFabContext

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case title
        case clientTs = "client_ts"
        case reportType = "report_type"
        case description
        case expectedBehavior = "expected_behavior"
        case severity, tags, reporter, context
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.protocolVersion = try c.decode(String.self, forKey: .protocolVersion)
        self.title = try c.decode(String.self, forKey: .title)
        if title.isEmpty || title.count > 200 {
            throw BugFabValidationError.invalidLength(
                field: "title", min: 1, max: 200, actual: title.count
            )
        }
        self.clientTs = try c.decode(String.self, forKey: .clientTs)
        if clientTs.isEmpty {
            throw BugFabValidationError.invalidLength(
                field: "client_ts", min: 1, max: nil, actual: 0
            )
        }
        self.reportType =
            try c.decodeIfPresent(BugFabReportType.self, forKey: .reportType) ?? .bug
        self.description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        self.expectedBehavior =
            try c.decodeIfPresent(String.self, forKey: .expectedBehavior) ?? ""
        self.severity = try c.decodeIfPresent(BugFabSeverity.self, forKey: .severity) ?? .medium
        self.tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []
        self.reporter = try c.decodeIfPresent(BugFabReporter.self, forKey: .reporter)
            ?? BugFabReporter()
        self.context = try c.decodeIfPresent(BugFabContext.self, forKey: .context)
            ?? BugFabContext()
    }
}

// MARK: - Status update body

public struct BugFabStatusUpdate: Codable, Sendable {
    public var status: BugFabStatus
    public var fixCommit: String
    public var fixDescription: String

    enum CodingKeys: String, CodingKey {
        case status
        case fixCommit = "fix_commit"
        case fixDescription = "fix_description"
    }

    public init(status: BugFabStatus, fixCommit: String = "", fixDescription: String = "") {
        self.status = status
        self.fixCommit = fixCommit
        self.fixDescription = fixDescription
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.status = try c.decode(BugFabStatus.self, forKey: .status)
        self.fixCommit = try c.decodeIfPresent(String.self, forKey: .fixCommit) ?? ""
        self.fixDescription = try c.decodeIfPresent(String.self, forKey: .fixDescription) ?? ""
    }
}

// MARK: - Lifecycle / read-side shapes

public struct BugFabLifecycleEvent: Codable, Sendable {
    public var action: String
    public var by: String
    public var at: String
    public var fixCommit: String
    public var fixDescription: String

    enum CodingKeys: String, CodingKey {
        case action, by, at
        case fixCommit = "fix_commit"
        case fixDescription = "fix_description"
    }

    public init(
        action: String, by: String = "", at: String,
        fixCommit: String = "", fixDescription: String = ""
    ) {
        self.action = action
        self.by = by
        self.at = at
        self.fixCommit = fixCommit
        self.fixDescription = fixDescription
    }
}

// Read-tolerant: severity / status are plain strings so deprecated values
// (per § deprecated-values rule) round-trip unchanged on the read path.
public struct BugFabBugReportSummary: Codable, Sendable, Content {
    public var id: String
    public var title: String
    public var reportType: String
    public var severity: String
    public var status: String
    public var module: String
    public var createdAt: String
    public var hasScreenshot: Bool
    public var githubIssueUrl: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case reportType = "report_type"
        case severity, status, module
        case createdAt = "created_at"
        case hasScreenshot = "has_screenshot"
        case githubIssueUrl = "github_issue_url"
    }
}

public struct BugFabBugReportDetail: Codable, Sendable, Content {
    public var id: String
    public var title: String
    public var reportType: String
    public var severity: String
    public var status: String
    public var module: String
    public var createdAt: String
    public var hasScreenshot: Bool
    public var githubIssueUrl: String?
    public var description: String
    public var expectedBehavior: String
    public var tags: [String]
    public var reporter: BugFabReporter
    public var context: BugFabContext
    public var lifecycle: [BugFabLifecycleEvent]
    public var serverUserAgent: String
    public var clientReportedUserAgent: String
    public var environment: String
    public var clientTs: String
    public var protocolVersion: String
    public var updatedAt: String
    public var githubIssueNumber: Int?

    enum CodingKeys: String, CodingKey {
        case id, title
        case reportType = "report_type"
        case severity, status, module
        case createdAt = "created_at"
        case hasScreenshot = "has_screenshot"
        case githubIssueUrl = "github_issue_url"
        case description
        case expectedBehavior = "expected_behavior"
        case tags, reporter, context, lifecycle
        case serverUserAgent = "server_user_agent"
        case clientReportedUserAgent = "client_reported_user_agent"
        case environment
        case clientTs = "client_ts"
        case protocolVersion = "protocol_version"
        case updatedAt = "updated_at"
        case githubIssueNumber = "github_issue_number"
    }
}

public struct BugFabBugReportListResponse: Content, Sendable {
    public var items: [BugFabBugReportSummary]
    public var total: Int
    public var page: Int
    public var pageSize: Int
    public var stats: [String: Int]

    enum CodingKeys: String, CodingKey {
        case items, total, page
        case pageSize = "page_size"
        case stats
    }
}

public struct BugFabIntakeResponse: Content, Sendable {
    public var id: String
    public var receivedAt: String
    public var storedAt: String
    public var githubIssueUrl: String?

    enum CodingKeys: String, CodingKey {
        case id
        case receivedAt = "received_at"
        case storedAt = "stored_at"
        case githubIssueUrl = "github_issue_url"
    }
}
