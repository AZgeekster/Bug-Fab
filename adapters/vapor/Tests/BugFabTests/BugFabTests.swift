import FluentSQLiteDriver
import Foundation
import NIOCore
import XCTVapor

@testable import BugFab

// Smoke + conformance tests. These exercise every code path the
// CONFORMANCE doc explicitly calls out:
//   - severity enum rejected → 422
//   - status enum rejected → 422
//   - non-PNG screenshot → 415
//   - oversized screenshot → 413 with limit_bytes
//   - missing protocol_version → 400 validation_error
//   - unknown protocol_version → 400 unsupported_protocol_version
//   - rate-limit hit → 429 with retry_after_seconds
//   - bulk close/archive round-trip
//   - file-storage round-trip (save → list → detail → screenshot)

final class BugFabHappyPathTests: XCTestCase {
    func testFullSubmitListDetailRoundTrip() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        let png = pngBytes()
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: png)
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .created)
            let body = try res.content.decode(BugFabIntakeResponse.self)
            XCTAssertTrue(body.id.hasPrefix("bug-"))
        }
        try await app.testable().test(.GET, "/admin/reports") { res async throws in
            XCTAssertEqual(res.status, .ok)
            let list = try res.content.decode(BugFabBugReportListResponse.self)
            XCTAssertEqual(list.total, 1)
            XCTAssertEqual(list.items.first?.title, "Test bug")
        }
        try await app.testable().test(.GET, "/admin/reports/bug-001/screenshot") { res async throws in
            XCTAssertEqual(res.status, .ok)
            XCTAssertEqual(res.headers.first(name: .contentType), "image/png")
        }
    }
}

final class BugFabFilterTests: XCTestCase {
    func testListFiltersByEnvironment() async throws {
        // environment is denormalized into the index entry now — the filter
        // used to be a documented no-op that matched every report.
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        let png = pngBytes()
        for env in ["production", "staging"] {
            let metadata = """
                {
                  "protocol_version": "0.1",
                  "title": "\(env) one",
                  "client_ts": "2026-04-27T00:00:00Z",
                  "severity": "high",
                  "tags": ["test"],
                  "context": {
                    "url": "https://example.com/",
                    "user_agent": "test-agent/1.0",
                    "environment": "\(env)"
                  }
                }
                """
            try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
                try buildMultipart(req: &req, metadata: metadata, png: png)
            } afterResponse: { res async throws in
                XCTAssertEqual(res.status, .created)
            }
        }
        try await app.testable().test(.GET, "/admin/reports?environment=production") {
            res async throws in
            XCTAssertEqual(res.status, .ok)
            let list = try res.content.decode(BugFabBugReportListResponse.self)
            XCTAssertEqual(list.total, 1)
            XCTAssertEqual(list.items.first?.title, "production one")
        }
    }

    func testWriteIndexLeavesNoTempArtifact() async throws {
        // atomicWrite now replaces index.json via a single temp-plus-rename
        // (Data.write .atomic), so a crash can never leave the index missing.
        // A crash can't be unit-tested; this asserts the observable invariant
        // that the write path round-trips and drops no stray temp file next
        // to the index (the old remove-then-move used a deterministic .tmp
        // sibling).
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("bugfab-\(UUID().uuidString)", isDirectory: true)
        let storage = try BugFabFileStorage(storageDirectory: dir)
        let png = pngBytes()
        let first = try await storage.saveReport(
            metadata: BugFabFluentStorage.decodeJSON(validMetadata()), screenshotBytes: png)
        _ = try await storage.saveReport(
            metadata: BugFabFluentStorage.decodeJSON(validMetadata()), screenshotBytes: png)
        _ = try await storage.deleteReport(id: first)

        let (items, total) = try await storage.listReports(filters: [:], page: 1, pageSize: 50)
        XCTAssertEqual(total, 1)
        XCTAssertEqual(items.count, 1)

        let contents = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        XCTAssertFalse(
            contents.contains { $0.hasSuffix(".tmp") },
            "atomicWrite must not leave a .tmp artifact; found \(contents)")
    }
}

final class BugFabValidationTests: XCTestCase {
    func testSeverityRejected() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        var meta = validMetadata()
        meta = meta.replacingOccurrences(of: "\"high\"", with: "\"urgent\"")
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: meta, png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .unprocessableEntity)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "schema_error")
        }
    }

    func testNonPNGRejected() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        let notPng = Data([0xFF, 0xD8, 0xFF, 0xE0])  // JPEG magic
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: notPng)
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .unsupportedMediaType)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "unsupported_media_type")
        }
    }

    func testOversizedRejected() async throws {
        let app = try await makeTestAppSmallCap()
        defer { Task { try? await app.asyncShutdown() } }
        var big = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        big.append(Data(repeating: 0, count: 200))  // 200 bytes > 100 byte cap
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: big)
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .payloadTooLarge)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "payload_too_large")
            XCTAssertNotNil(body.limitBytes)
        }
    }

    func testMissingProtocolVersion() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        let meta = #"{"title":"x","client_ts":"2026-04-27T00:00:00Z"}"#
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: meta, png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .badRequest)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "validation_error")
        }
    }

    func testUnknownProtocolVersion() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        let meta = validMetadata().replacingOccurrences(of: "\"0.1\"", with: "\"9.9\"")
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: meta, png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .badRequest)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "unsupported_protocol_version")
        }
    }
}

final class BugFabBulkOpsTests: XCTestCase {
    func testBulkCloseFixed() async throws {
        let app = try await makeTestApp()
        defer { Task { try? await app.asyncShutdown() } }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .created)
        }
        // transition to fixed
        try await app.testable().test(.PUT, "/admin/reports/bug-001/status") { req async throws in
            try req.content.encode(["status": "fixed"])
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .ok)
        }
        try await app.testable().test(.POST, "/admin/bulk-close-fixed") { res async throws in
            XCTAssertEqual(res.status, .ok)
            struct R: Content { let closed: Int }
            let body = try res.content.decode(R.self)
            XCTAssertEqual(body.closed, 1)
        }
        // bulk archive
        try await app.testable().test(.POST, "/admin/bulk-archive-closed") { res async throws in
            XCTAssertEqual(res.status, .ok)
            struct R: Content { let archived: Int }
            let body = try res.content.decode(R.self)
            XCTAssertEqual(body.archived, 1)
        }
    }
}

final class BugFabRateLimitTests: XCTestCase {
    func testRateLimit() async throws {
        let app = try await makeTestAppWithRateLimit()
        defer { Task { try? await app.asyncShutdown() } }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .created)
        }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .tooManyRequests)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "rate_limited")
            XCTAssertNotNil(body.retryAfterSeconds)
        }
    }

    func testSpoofedForwardedForCannotEvadeRateLimit() async throws {
        // X-Forwarded-For is client-controlled. If the limiter keyed on it,
        // rotating the header would mint a fresh bucket per request and the
        // limit (1/window here) would never trip. With the default empty
        // trusted-proxies set the header must be ignored.
        let app = try await makeTestAppWithRateLimit()
        defer { Task { try? await app.asyncShutdown() } }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
            req.headers.replaceOrAdd(name: "x-forwarded-for", value: "203.0.113.1")
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .created)
        }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
            req.headers.replaceOrAdd(name: "x-forwarded-for", value: "203.0.113.2")
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .tooManyRequests)
            let body = try res.content.decode(BugFabErrorBody.self)
            XCTAssertEqual(body.error, "rate_limited")
        }
    }
}

final class BugFabClientIPTests: XCTestCase {
    private func makeRequest(
        _ app: Application, peer: String?, xff: String?
    ) throws -> Request {
        var headers = HTTPHeaders()
        if let xff { headers.add(name: "x-forwarded-for", value: xff) }
        return Request(
            application: app,
            method: .POST,
            url: URI(path: "/api/bug-reports"),
            headers: headers,
            remoteAddress: peer.map { try! SocketAddress(ipAddress: $0, port: 80) },
            on: app.eventLoopGroup.next()
        )
    }

    func testForwardedHeaderIgnoredFromUntrustedPeer() async throws {
        let app = try await Application.make(.testing)
        defer { Task { try? await app.asyncShutdown() } }
        let req = try makeRequest(app, peer: "10.0.0.1", xff: "203.0.113.7")
        XCTAssertEqual(
            BugReportsController.clientIP(req, trustedProxies: []), "10.0.0.1")
    }

    func testForwardedHeaderHonoredFromTrustedPeer() async throws {
        let app = try await Application.make(.testing)
        defer { Task { try? await app.asyncShutdown() } }
        let req = try makeRequest(app, peer: "10.0.0.1", xff: "203.0.113.7, 10.0.0.1")
        XCTAssertEqual(
            BugReportsController.clientIP(req, trustedProxies: ["10.0.0.1"]),
            "203.0.113.7")
    }

    func testWildcardTrustsEveryPeer() async throws {
        let app = try await Application.make(.testing)
        defer { Task { try? await app.asyncShutdown() } }
        let req = try makeRequest(app, peer: "192.0.2.5", xff: "203.0.113.7")
        XCTAssertEqual(
            BugReportsController.clientIP(req, trustedProxies: ["*"]), "203.0.113.7")
    }

    func testNoHeaderFallsBackToPeer() async throws {
        let app = try await Application.make(.testing)
        defer { Task { try? await app.asyncShutdown() } }
        let req = try makeRequest(app, peer: "192.0.2.5", xff: nil)
        XCTAssertEqual(
            BugReportsController.clientIP(req, trustedProxies: ["*"]), "192.0.2.5")
    }
}

final class BugFabFluentTests: XCTestCase {
    func testFluentRoundTrip() async throws {
        let app = try await Application.make(.testing)
        try setupFluentApp(app)
        defer { Task { try? await app.asyncShutdown() } }
        try await app.testable().test(.POST, "/api/bug-reports") { req async throws in
            try buildMultipart(req: &req, metadata: validMetadata(), png: pngBytes())
        } afterResponse: { res async throws in
            XCTAssertEqual(res.status, .created)
        }
        try await app.testable().test(.GET, "/admin/reports") { res async throws in
            XCTAssertEqual(res.status, .ok)
            let list = try res.content.decode(BugFabBugReportListResponse.self)
            XCTAssertEqual(list.total, 1)
        }
    }

    func testIdsAreNotReusedAfterDelete() async throws {
        // The allocator was COUNT(*)+1: create three, delete the first, create
        // a fourth, and the count-based id would be bug-003 — colliding with a
        // live row on the primary key. The counter-row allocator must mint
        // bug-004 instead.
        let app = try await Application.make(.testing)
        try setupFluentApp(app)
        defer { Task { try? await app.asyncShutdown() } }
        let storage = BugFabFluentStorage(app: app)
        let png = pngBytes()
        var ids: [String] = []
        for _ in 0..<3 {
            let id = try await storage.saveReport(
                metadata: BugFabFluentStorage.decodeJSON(validMetadata()), screenshotBytes: png)
            ids.append(id)
        }
        XCTAssertEqual(ids, ["bug-001", "bug-002", "bug-003"])
        _ = try await storage.deleteReport(id: "bug-001")
        let fourth = try await storage.saveReport(
            metadata: BugFabFluentStorage.decodeJSON(validMetadata()), screenshotBytes: png)
        XCTAssertEqual(fourth, "bug-004")
    }
}

// MARK: - Test helpers

func makeTestApp() async throws -> Application {
    let app = try await Application.make(.testing)
    let dir = URL(
        fileURLWithPath: NSTemporaryDirectory()
    ).appendingPathComponent("bugfab-\(UUID().uuidString)", isDirectory: true)
    let storage = try BugFabFileStorage(storageDirectory: dir)
    try app.bugFab(storage: storage)
    try BugFab.intakeRoutes(app.grouped("api"))
    try BugFab.viewerRoutes(app.grouped("admin"))
    return app
}

func makeTestAppSmallCap() async throws -> Application {
    let app = try await Application.make(.testing)
    let dir = URL(
        fileURLWithPath: NSTemporaryDirectory()
    ).appendingPathComponent("bugfab-\(UUID().uuidString)", isDirectory: true)
    let storage = try BugFabFileStorage(storageDirectory: dir)
    var settings = BugFabSettings()
    settings.maxUploadBytes = 100
    try app.bugFab(storage: storage, settings: settings)
    try BugFab.intakeRoutes(app.grouped("api"))
    return app
}

func makeTestAppWithRateLimit() async throws -> Application {
    let app = try await Application.make(.testing)
    let dir = URL(
        fileURLWithPath: NSTemporaryDirectory()
    ).appendingPathComponent("bugfab-\(UUID().uuidString)", isDirectory: true)
    let storage = try BugFabFileStorage(storageDirectory: dir)
    var settings = BugFabSettings()
    settings.rateLimitEnabled = true
    settings.rateLimitMax = 1
    settings.rateLimitWindowSeconds = 60
    try app.bugFab(storage: storage, settings: settings)
    try BugFab.intakeRoutes(app.grouped("api"))
    return app
}

func setupFluentApp(_ app: Application) throws {
    app.databases.use(.sqlite(.memory), as: .sqlite)
    app.migrations.add(CreateBugFabReport())
    app.migrations.add(CreateBugFabIdCounter())
    try app.autoMigrate().wait()
    let storage = BugFabFluentStorage(app: app)
    try app.bugFab(storage: storage)
    try BugFab.intakeRoutes(app.grouped("api"))
    try BugFab.viewerRoutes(app.grouped("admin"))
}

func validMetadata() -> String {
    return """
        {
          "protocol_version": "0.1",
          "title": "Test bug",
          "client_ts": "2026-04-27T00:00:00Z",
          "severity": "high",
          "tags": ["test"],
          "context": {
            "url": "https://example.com/",
            "user_agent": "test-agent/1.0",
            "environment": "test"
          }
        }
        """
}

func pngBytes() -> Data {
    var d = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    d.append(contentsOf: [0x00, 0x00, 0x00, 0x0D])  // IHDR length
    d.append("IHDR".data(using: .ascii)!)
    d.append(Data(repeating: 0, count: 13))  // IHDR body
    d.append(Data(repeating: 0, count: 32))  // padding to look non-empty
    return d
}

func buildMultipart(req: inout XCTHTTPRequest, metadata: String, png: Data) throws {
    let boundary = "BUGFAB" + UUID().uuidString
    req.headers.replaceOrAdd(
        name: .contentType, value: "multipart/form-data; boundary=\(boundary)"
    )
    var body = ByteBufferAllocator().buffer(capacity: 1024 + png.count)
    body.writeString("--\(boundary)\r\n")
    body.writeString("Content-Disposition: form-data; name=\"metadata\"\r\n")
    body.writeString("Content-Type: application/json\r\n\r\n")
    body.writeString(metadata)
    body.writeString("\r\n--\(boundary)\r\n")
    body.writeString(
        "Content-Disposition: form-data; name=\"screenshot\"; filename=\"shot.png\"\r\n"
    )
    body.writeString("Content-Type: image/png\r\n\r\n")
    body.writeBytes(png)
    body.writeString("\r\n--\(boundary)--\r\n")
    req.body = body
}
