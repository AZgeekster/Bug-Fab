import Foundation
import NIOConcurrencyHelpers

/// Token-bucket-ish per-IP limiter using a sliding window. Off by default.
/// One process-local instance per application; multi-instance deployments
/// should pair this with an external rate-limit layer (Cloudflare, Caddy,
/// nginx limit_req_zone, etc.) — see README.
public final class BugFabRateLimiter: @unchecked Sendable {
    private let maxPerWindow: Int
    private let windowSeconds: TimeInterval
    private let lock = NIOLock()
    private var hits: [String: [Date]] = [:]

    public init(maxPerWindow: Int, windowSeconds: TimeInterval) {
        self.maxPerWindow = maxPerWindow
        self.windowSeconds = windowSeconds
    }

    /// Returns `true` when the request is permitted; `false` when over cap.
    public func check(_ ip: String, now: Date = Date()) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let cutoff = now.addingTimeInterval(-windowSeconds)
        var bucket = hits[ip, default: []].filter { $0 >= cutoff }
        if bucket.count >= maxPerWindow {
            hits[ip] = bucket
            return false
        }
        bucket.append(now)
        hits[ip] = bucket
        return true
    }

    /// Number of seconds the caller should wait before retrying.
    public func retryAfter(_ ip: String, now: Date = Date()) -> Int {
        lock.lock()
        defer { lock.unlock() }
        let cutoff = now.addingTimeInterval(-windowSeconds)
        let bucket = hits[ip, default: []].filter { $0 >= cutoff }
        guard let oldest = bucket.first else { return 0 }
        let remaining = windowSeconds - now.timeIntervalSince(oldest)
        return max(1, Int(remaining.rounded(.up)))
    }
}
