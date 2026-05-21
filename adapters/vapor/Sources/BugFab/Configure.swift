import Foundation
import Vapor

// One-shot configuration helper. Consumers call `try app.bugFab(...)`
// during their app's configure phase to wire up storage, settings, error
// middleware, and an optional rate limiter.
//
// The exposed entrypoint is an Application extension so consumers can
// keep their existing `configure(_:)` flat. Once registered, every
// `Request` can reach `req.application.bugFab` for the typed holder.

extension Application {
    /// Configure the Bug-Fab adapter for this application.
    ///
    /// - Parameters:
    ///   - storage: A `BugFabStorage` implementation (file or Fluent).
    ///   - settings: Override defaults; if nil, `.fromEnvironment(...)` is used.
    public func bugFab(
        storage: any BugFabStorage,
        settings: BugFabSettings? = nil
    ) throws {
        let effectiveSettings = settings ?? BugFabSettings.fromEnvironment(self.environment)
        // Enforce the multipart body cap globally too, defending the
        // EventLoop from unbounded `req.body.collect()` calls outside
        // the route's `body: .collect(maxSize:)` parameter.
        // ByteCount is ExpressibleByIntegerLiteral; build it from raw bytes.
        self.routes.defaultMaxBodySize =
            ByteCount(integerLiteral: effectiveSettings.maxUploadBytes + (1024 * 1024))

        let limiter: BugFabRateLimiter? =
            effectiveSettings.rateLimitEnabled
            ? BugFabRateLimiter(
                maxPerWindow: effectiveSettings.rateLimitMax,
                windowSeconds: effectiveSettings.rateLimitWindowSeconds
            ) : nil
        self.bugFab = BugFabContextHolder(
            storage: storage, settings: effectiveSettings, rateLimiter: limiter
        )
        // Replace Vapor's default error middleware with our envelope-shaped
        // version. Vapor pre-registers `ErrorMiddleware.default(...)` on
        // every Application, which emits `{"error": true, "reason": "..."}`
        // — not the spec envelope. We rebuild `app.middleware` from scratch
        // so our middleware is the only error handler in the chain.
        // Without this reset, the default ErrorMiddleware sits inside ours
        // and converts thrown AbortErrors into Responses before our handler
        // ever sees them, leaking the `error: true` boolean shape.
        self.middleware = .init()
        self.middleware.use(BugFabErrorMiddleware())
    }
}
