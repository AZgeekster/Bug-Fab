import Foundation
import Vapor

// Adapter-level configuration. Mirrors `bug_fab/config.py` in the Python
// reference. Defaults are safe-on by design — rate limiting off, viewer
// permissions on, 4 MiB cap (stricter than the protocol's 10 MiB ceiling
// per the per-adapter MAY clause).
public struct BugFabSettings: Sendable {
    /// Maximum allowed screenshot size in bytes. Anything larger is rejected
    /// with `413 payload_too_large` and the `limit_bytes` field populated.
    public var maxUploadBytes: Int

    /// Required JSON wire-protocol version.
    public var protocolVersion: String

    /// When true, requests exceeding `rateLimitMax` per
    /// `rateLimitWindowSeconds` for a given client IP receive 429.
    public var rateLimitEnabled: Bool
    public var rateLimitMax: Int
    public var rateLimitWindowSeconds: TimeInterval

    /// Direct-peer addresses allowed to supply `X-Forwarded-For` as the
    /// rate-limit key. Empty (the secure default) ignores the header and
    /// meters by the direct peer; `"*"` trusts every peer. Mirrors the
    /// Python reference's `rate_limit_trusted_proxies`.
    public var rateLimitTrustedProxies: Set<String>

    /// Optional id prefix (e.g., "P" or "D") for multi-env shared collectors.
    public var idPrefix: String

    /// Viewer permission flags — gate destructive routes.
    public var canEditStatus: Bool
    public var canDelete: Bool
    public var canBulk: Bool

    public init(
        maxUploadBytes: Int = 4 * 1024 * 1024,
        protocolVersion: String = "0.1",
        rateLimitEnabled: Bool = false,
        rateLimitMax: Int = 30,
        rateLimitWindowSeconds: TimeInterval = 60,
        rateLimitTrustedProxies: Set<String> = [],
        idPrefix: String = "",
        canEditStatus: Bool = true,
        canDelete: Bool = true,
        canBulk: Bool = true
    ) {
        self.maxUploadBytes = maxUploadBytes
        self.protocolVersion = protocolVersion
        self.rateLimitEnabled = rateLimitEnabled
        self.rateLimitMax = rateLimitMax
        self.rateLimitWindowSeconds = rateLimitWindowSeconds
        self.rateLimitTrustedProxies = rateLimitTrustedProxies
        self.idPrefix = idPrefix
        self.canEditStatus = canEditStatus
        self.canDelete = canDelete
        self.canBulk = canBulk
    }

    /// Build a settings instance from environment variables. Documented
    /// in the README — every key prefix `BUG_FAB_`.
    public static func fromEnvironment(_ env: Environment) -> BugFabSettings {
        var s = BugFabSettings()
        if let v = Environment.get("BUG_FAB_MAX_UPLOAD_MB"), let mb = Int(v) {
            s.maxUploadBytes = mb * 1024 * 1024
        }
        if let v = Environment.get("BUG_FAB_PROTOCOL_VERSION") {
            s.protocolVersion = v
        }
        if let v = Environment.get("BUG_FAB_RATE_LIMIT_ENABLED") {
            s.rateLimitEnabled = (v == "1" || v.lowercased() == "true")
        }
        if let v = Environment.get("BUG_FAB_RATE_LIMIT_MAX"), let n = Int(v) {
            s.rateLimitMax = n
        }
        if let v = Environment.get("BUG_FAB_RATE_LIMIT_WINDOW_SECONDS"),
            let n = TimeInterval(v)
        {
            s.rateLimitWindowSeconds = n
        }
        if let v = Environment.get("BUG_FAB_RATE_LIMIT_TRUSTED_PROXIES") {
            s.rateLimitTrustedProxies = Set(
                v.split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
            )
        }
        if let v = Environment.get("BUG_FAB_ID_PREFIX") {
            s.idPrefix = v
        }
        if let v = Environment.get("BUG_FAB_CAN_EDIT_STATUS") {
            s.canEditStatus = (v == "1" || v.lowercased() == "true")
        }
        if let v = Environment.get("BUG_FAB_CAN_DELETE") {
            s.canDelete = (v == "1" || v.lowercased() == "true")
        }
        if let v = Environment.get("BUG_FAB_CAN_BULK") {
            s.canBulk = (v == "1" || v.lowercased() == "true")
        }
        _ = env  // unused but kept in the signature for future env-mode branching
        return s
    }
}

// Storage attached to the Application — accessible from Request via
// `req.application.bugFab`. This is the Vapor idiomatic place for
// long-lived adapter state.
public struct BugFabContextHolder: Sendable {
    public let storage: any BugFabStorage
    public let settings: BugFabSettings
    public let rateLimiter: BugFabRateLimiter?

    public init(
        storage: any BugFabStorage,
        settings: BugFabSettings,
        rateLimiter: BugFabRateLimiter? = nil
    ) {
        self.storage = storage
        self.settings = settings
        self.rateLimiter = rateLimiter
    }
}

extension Application {
    private struct BugFabKey: StorageKey {
        typealias Value = BugFabContextHolder
    }

    public var bugFab: BugFabContextHolder {
        get {
            guard let value = self.storage[BugFabKey.self] else {
                fatalError(
                    "BugFab is not configured. Call try app.bugFab(...) before using routes."
                )
            }
            return value
        }
        set {
            self.storage[BugFabKey.self] = newValue
        }
    }
}
