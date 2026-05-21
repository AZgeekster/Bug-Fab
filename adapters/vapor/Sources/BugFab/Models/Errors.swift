import Foundation
import Vapor

// Wire-protocol error envelope.
//
// Per PROTOCOL.md § "Error response shape", every non-2xx (except 204 and
// the binary 404 from screenshot) returns:
//     { "error": "<code>", "detail": <string | array> }
//
// We surface this as a Vapor AbortError so middleware can translate it
// uniformly; the `errorMiddleware` we register in Configure.swift writes
// the JSON body in the right shape.
public struct BugFabErrorBody: Content, Sendable {
    public let error: String
    public let detail: BugFabJSONValue?
    public let limitBytes: Int?
    public let retryAfterSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case error, detail
        case limitBytes = "limit_bytes"
        case retryAfterSeconds = "retry_after_seconds"
    }

    public init(
        error: String,
        detail: BugFabJSONValue? = nil,
        limitBytes: Int? = nil,
        retryAfterSeconds: Int? = nil
    ) {
        self.error = error
        self.detail = detail
        self.limitBytes = limitBytes
        self.retryAfterSeconds = retryAfterSeconds
    }
}

public struct BugFabAbort: AbortError, Sendable {
    public let status: HTTPResponseStatus
    public let errorCode: String
    public let detailMessage: String
    public let limitBytes: Int?
    public let retryAfterSeconds: Int?

    public var reason: String { detailMessage }
    public var headers: HTTPHeaders { [:] }

    public init(
        status: HTTPResponseStatus,
        errorCode: String,
        detail: String,
        limitBytes: Int? = nil,
        retryAfterSeconds: Int? = nil
    ) {
        self.status = status
        self.errorCode = errorCode
        self.detailMessage = detail
        self.limitBytes = limitBytes
        self.retryAfterSeconds = retryAfterSeconds
    }

    public func body() -> BugFabErrorBody {
        BugFabErrorBody(
            error: errorCode,
            detail: .string(detailMessage),
            limitBytes: limitBytes,
            retryAfterSeconds: retryAfterSeconds
        )
    }
}

// Thrown by Codable initializers. The intake controller catches these
// and re-raises as the appropriate 422 BugFabAbort.
public enum BugFabValidationError: Error, CustomStringConvertible {
    case invalidEnum(field: String, value: String, allowed: [String])
    case invalidLength(field: String, min: Int?, max: Int?, actual: Int)
    case fieldTooLong(field: String, limit: Int)
    case missingField(name: String)

    public var description: String {
        switch self {
        case .invalidEnum(let field, let value, let allowed):
            return "\(field) must be one of: \(allowed.joined(separator: ", ")) (got: \(value))"
        case .invalidLength(let field, let lo, let hi, let actual):
            let lower = lo.map { "min \($0)" } ?? ""
            let upper = hi.map { "max \($0)" } ?? ""
            let parts = [lower, upper].filter { !$0.isEmpty }.joined(separator: ", ")
            return "\(field) length out of bounds (\(parts)); got \(actual)"
        case .fieldTooLong(let field, let limit):
            return "\(field) exceeds maximum length of \(limit) characters"
        case .missingField(let name):
            return "Required field '\(name)' is missing"
        }
    }
}

// Vapor's default ErrorMiddleware doesn't produce our envelope shape, so
// we ship a small replacement. Registered in `Configure.bugFab(_:)`.
public struct BugFabErrorMiddleware: AsyncMiddleware {
    public init() {}

    public func respond(to request: Request, chainingTo next: AsyncResponder) async throws
        -> Response
    {
        do {
            return try await next.respond(to: request)
        } catch let abort as BugFabAbort {
            return try makeResponse(abort, on: request)
        } catch let abort as AbortError {
            let body = BugFabErrorBody(
                error: defaultCode(for: abort.status),
                detail: .string(abort.reason)
            )
            let response = Response(status: abort.status)
            try response.content.encode(body)
            return response
        } catch let validation as BugFabValidationError {
            let abort = BugFabAbort(
                status: .unprocessableEntity,
                errorCode: "schema_error",
                detail: validation.description
            )
            return try makeResponse(abort, on: request)
        } catch {
            request.logger.report(error: error)
            let abort = BugFabAbort(
                status: .internalServerError,
                errorCode: "internal_error",
                detail: "Unhandled server error"
            )
            return try makeResponse(abort, on: request)
        }
    }

    private func makeResponse(_ abort: BugFabAbort, on request: Request) throws -> Response {
        let response = Response(status: abort.status)
        try response.content.encode(abort.body())
        return response
    }

    private func defaultCode(for status: HTTPResponseStatus) -> String {
        switch status {
        case .badRequest: return "validation_error"
        case .unprocessableEntity: return "schema_error"
        case .payloadTooLarge: return "payload_too_large"
        case .unsupportedMediaType: return "unsupported_media_type"
        case .notFound: return "not_found"
        case .forbidden: return "forbidden"
        case .tooManyRequests: return "rate_limited"
        case .serviceUnavailable: return "storage_unavailable"
        default: return "internal_error"
        }
    }
}
