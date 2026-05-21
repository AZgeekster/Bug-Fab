import Foundation
import NIOFoundationCompat
import Vapor

// All eight endpoints. Mounted by `Routes.swift`; relies on the
// `BugFabContextHolder` stashed on the Application.
//
// Wire-protocol contract — anything routable here MUST round-trip the
// shapes defined in `repo/docs/PROTOCOL.md`. Validation is strict: enums
// rejected → 422; payload too big → 413 with `limit_bytes`; non-PNG → 415;
// rate-limited → 429 with `retry_after_seconds`.

public struct BugReportsController: RouteCollection, Sendable {
    public init() {}

    public func boot(routes: RoutesBuilder) throws {
        routes.on(.POST, "bug-reports", body: .collect(maxSize: "12mb"), use: self.submit)
        routes.get("reports", use: self.list)
        routes.get("reports", ":id", use: self.detail)
        routes.get("reports", ":id", "screenshot", use: self.screenshot)
        routes.put("reports", ":id", "status", use: self.updateStatus)
        routes.delete("reports", ":id", use: self.delete)
        routes.post("bulk-close-fixed", use: self.bulkCloseFixed)
        routes.post("bulk-archive-closed", use: self.bulkArchiveClosed)
    }

    // MARK: POST /bug-reports

    @Sendable
    func submit(req: Request) async throws -> Response {
        let ctx = req.application.bugFab

        // Rate limit first — cheaper than parsing.
        let ip = Self.clientIP(req)
        if let limiter = ctx.rateLimiter, ctx.settings.rateLimitEnabled {
            if !limiter.check(ip) {
                throw BugFabAbort(
                    status: .tooManyRequests,
                    errorCode: "rate_limited",
                    detail:
                        "Rate limit exceeded: max \(ctx.settings.rateLimitMax) reports per \(Int(ctx.settings.rateLimitWindowSeconds)) seconds",
                    retryAfterSeconds: limiter.retryAfter(ip)
                )
            }
        }

        // Decode multipart with the configured 4-MB cap. We also check
        // Content-Length up front as a cheap fail-fast — without this,
        // the multipart body decoder buffers the whole upload before
        // we get a chance to reject.
        if let cl = req.headers.first(name: .contentLength), let n = Int(cl),
            n > ctx.settings.maxUploadBytes + (256 * 1024)
        {
            throw BugFabAbort(
                status: .payloadTooLarge,
                errorCode: "payload_too_large",
                detail: "Request body exceeds limit",
                limitBytes: ctx.settings.maxUploadBytes
            )
        }

        struct Form: Content {
            var metadata: String
            var screenshot: File
        }
        let form: Form
        do {
            form = try req.content.decode(Form.self)
        } catch {
            throw BugFabAbort(
                status: .badRequest,
                errorCode: "validation_error",
                detail: "multipart body must include 'metadata' and 'screenshot' parts"
            )
        }

        // Protocol-version gate is a 400, not a 422 — distinct error code.
        if let pre = Self.extractProtocolVersion(form.metadata) {
            if pre != ctx.settings.protocolVersion {
                throw BugFabAbort(
                    status: .badRequest,
                    errorCode: "unsupported_protocol_version",
                    detail: "Submitted protocol_version '\(pre)' is not supported by this adapter"
                )
            }
        } else {
            throw BugFabAbort(
                status: .badRequest,
                errorCode: "validation_error",
                detail: "metadata.protocol_version is required"
            )
        }

        // Now validate the rest with our strict decoder.
        let payloadData = Data(form.metadata.utf8)
        let payload: BugFabBugReportCreate
        do {
            payload = try JSONDecoder().decode(BugFabBugReportCreate.self, from: payloadData)
        } catch let err as BugFabValidationError {
            throw BugFabAbort(
                status: .unprocessableEntity, errorCode: "schema_error",
                detail: err.description
            )
        } catch let err as DecodingError {
            throw BugFabAbort(
                status: .unprocessableEntity, errorCode: "schema_error",
                detail: Self.humanReadable(err)
            )
        } catch {
            throw BugFabAbort(
                status: .badRequest, errorCode: "validation_error",
                detail: "metadata is not valid JSON"
            )
        }

        // Drain screenshot bytes (already capped by collect(maxSize:)).
        var buf = form.screenshot.data
        let screenshotBytes = buf.readData(length: buf.readableBytes) ?? Data()
        if screenshotBytes.isEmpty {
            throw BugFabAbort(
                status: .badRequest, errorCode: "validation_error",
                detail: "Screenshot file is empty"
            )
        }
        if screenshotBytes.count > ctx.settings.maxUploadBytes {
            throw BugFabAbort(
                status: .payloadTooLarge,
                errorCode: "payload_too_large",
                detail: "Screenshot exceeds maximum size",
                limitBytes: ctx.settings.maxUploadBytes
            )
        }
        if !Self.isPNG(screenshotBytes) {
            throw BugFabAbort(
                status: .unsupportedMediaType,
                errorCode: "unsupported_media_type",
                detail: "Screenshot must be a PNG image"
            )
        }

        // Build the metadata bag the storage layer expects (server-side
        // overrides for UA / environment / etc.).
        let serverUA = req.headers.first(name: .userAgent) ?? ""
        var metadataDict: [String: BugFabJSONValue] =
            (try? JSONDecoder().decode([String: BugFabJSONValue].self, from: payloadData)) ?? [:]
        metadataDict["server_user_agent"] = .string(serverUA)
        metadataDict["client_reported_user_agent"] = .string(payload.context.userAgent)
        metadataDict["environment"] = .string(payload.context.environment)

        let id: String
        do {
            id = try await ctx.storage.saveReport(
                metadata: metadataDict, screenshotBytes: screenshotBytes
            )
        } catch {
            req.logger.report(error: error)
            throw BugFabAbort(
                status: .internalServerError, errorCode: "internal_error",
                detail: "Failed to persist bug report"
            )
        }

        guard let detail = try await ctx.storage.getReport(id: id) else {
            throw BugFabAbort(
                status: .internalServerError, errorCode: "internal_error",
                detail: "Stored report could not be read back"
            )
        }

        let body = BugFabIntakeResponse(
            id: id,
            receivedAt: detail.createdAt,
            storedAt: "bug-fab://reports/\(id)",
            githubIssueUrl: nil
        )
        let response = Response(status: .created)
        try response.content.encode(body)
        return response
    }

    // MARK: GET /reports

    @Sendable
    func list(req: Request) async throws -> BugFabBugReportListResponse {
        let ctx = req.application.bugFab
        let page = try req.query.get(Int?.self, at: "page") ?? 1
        let rawPageSize = try req.query.get(Int?.self, at: "page_size") ?? 20
        let pageSize = max(1, min(200, rawPageSize))

        var filters: [String: String] = [:]
        for key in ["status", "severity", "module", "environment"] {
            if let v: String = try? req.query.get(at: key), !v.isEmpty {
                filters[key] = v
            }
        }
        let (items, total) = try await ctx.storage.listReports(
            filters: filters, page: page, pageSize: pageSize
        )
        let stats = try await Self.computeStats(storage: ctx.storage)
        return BugFabBugReportListResponse(
            items: items, total: total, page: page, pageSize: pageSize, stats: stats
        )
    }

    // MARK: GET /reports/:id

    @Sendable
    func detail(req: Request) async throws -> BugFabBugReportDetail {
        let ctx = req.application.bugFab
        let id = try Self.requireId(req)
        guard let detail = try await ctx.storage.getReport(id: id) else {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Bug report not found"
            )
        }
        return detail
    }

    // MARK: GET /reports/:id/screenshot

    @Sendable
    func screenshot(req: Request) async throws -> Response {
        let ctx = req.application.bugFab
        let id = try Self.requireId(req)
        guard let data = try await ctx.storage.getScreenshot(id: id) else {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Screenshot not found"
            )
        }
        var headers = HTTPHeaders()
        headers.add(name: .contentType, value: "image/png")
        headers.add(name: .contentLength, value: "\(data.count)")
        return Response(
            status: .ok, headers: headers, body: .init(data: data)
        )
    }

    // MARK: PUT /reports/:id/status

    @Sendable
    func updateStatus(req: Request) async throws -> BugFabBugReportDetail {
        let ctx = req.application.bugFab
        guard ctx.settings.canEditStatus else {
            throw BugFabAbort(
                status: .forbidden, errorCode: "forbidden",
                detail: "viewer action 'can_edit_status' is disabled"
            )
        }
        let id = try Self.requireId(req)
        let body: BugFabStatusUpdate
        do {
            body = try req.content.decode(BugFabStatusUpdate.self)
        } catch let err as BugFabValidationError {
            throw BugFabAbort(
                status: .unprocessableEntity, errorCode: "schema_error",
                detail: err.description
            )
        } catch {
            throw BugFabAbort(
                status: .unprocessableEntity, errorCode: "schema_error",
                detail: "status update body is invalid"
            )
        }
        let actor = (req.headers.first(name: "x-bug-fab-actor") ?? "viewer")
        guard
            let updated = try await ctx.storage.updateStatus(
                id: id, status: body.status.rawValue,
                fixCommit: body.fixCommit, fixDescription: body.fixDescription,
                by: actor
            )
        else {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Bug report not found"
            )
        }
        return updated
    }

    // MARK: DELETE /reports/:id

    @Sendable
    func delete(req: Request) async throws -> Response {
        let ctx = req.application.bugFab
        guard ctx.settings.canDelete else {
            throw BugFabAbort(
                status: .forbidden, errorCode: "forbidden",
                detail: "viewer action 'can_delete' is disabled"
            )
        }
        let id = try Self.requireId(req)
        let removed = try await ctx.storage.deleteReport(id: id)
        if !removed {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Bug report not found"
            )
        }
        return Response(status: .noContent)
    }

    // MARK: POST /bulk-close-fixed

    @Sendable
    func bulkCloseFixed(req: Request) async throws -> Response {
        let ctx = req.application.bugFab
        guard ctx.settings.canBulk else {
            throw BugFabAbort(
                status: .forbidden, errorCode: "forbidden",
                detail: "viewer action 'can_bulk' is disabled"
            )
        }
        let actor = (req.headers.first(name: "x-bug-fab-actor") ?? "viewer")
        let n = try await ctx.storage.bulkCloseFixed(by: actor)
        let response = Response(status: .ok)
        try response.content.encode(["closed": n])
        return response
    }

    // MARK: POST /bulk-archive-closed

    @Sendable
    func bulkArchiveClosed(req: Request) async throws -> Response {
        let ctx = req.application.bugFab
        guard ctx.settings.canBulk else {
            throw BugFabAbort(
                status: .forbidden, errorCode: "forbidden",
                detail: "viewer action 'can_bulk' is disabled"
            )
        }
        let n = try await ctx.storage.bulkArchiveClosed()
        let response = Response(status: .ok)
        try response.content.encode(["archived": n])
        return response
    }

    // MARK: helpers

    static func requireId(_ req: Request) throws -> String {
        guard let raw = req.parameters.get("id") else {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Bug report not found"
            )
        }
        if !BugFabFileStorage.isValidId(raw) {
            throw BugFabAbort(
                status: .notFound, errorCode: "not_found", detail: "Bug report not found"
            )
        }
        return raw
    }

    static func clientIP(_ req: Request) -> String {
        if let xff = req.headers.first(name: "x-forwarded-for") {
            return xff.split(separator: ",").first.map { $0.trimmingCharacters(in: .whitespaces) }
                ?? "unknown"
        }
        return req.remoteAddress?.ipAddress ?? "unknown"
    }

    static func isPNG(_ bytes: Data) -> Bool {
        let signature: [UInt8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]
        guard bytes.count >= signature.count else { return false }
        for i in 0..<signature.count {
            if bytes[i] != signature[i] { return false }
        }
        return true
    }

    static func extractProtocolVersion(_ metadata: String) -> String? {
        // Quick parse — avoid running the strict decoder just to find the
        // protocol_version (we need a different HTTP status than 422).
        guard let data = metadata.data(using: .utf8),
            let any = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return any["protocol_version"] as? String
    }

    static func computeStats(storage: any BugFabStorage) async throws -> [String: Int] {
        var stats: [String: Int] = [:]
        for state in ["open", "investigating", "fixed", "closed"] {
            let (_, total) = try await storage.listReports(
                filters: ["status": state], page: 1, pageSize: 1
            )
            stats[state] = total
        }
        return stats
    }

    static func humanReadable(_ error: DecodingError) -> String {
        switch error {
        case .keyNotFound(let key, _):
            return "Required field '\(key.stringValue)' is missing"
        case .typeMismatch(_, let ctx):
            return "Type mismatch at \(ctx.codingPath.map(\.stringValue).joined(separator: "."))"
        case .valueNotFound(_, let ctx):
            return "Value missing at \(ctx.codingPath.map(\.stringValue).joined(separator: "."))"
        case .dataCorrupted(let ctx):
            return ctx.debugDescription
        @unknown default:
            return "Metadata payload is invalid"
        }
    }
}
