//! Tower middleware used by the Bug-Fab router.
//!
//! * `body_limit_layer` — `tower-http::limit::RequestBodyLimitLayer`.
//!   Caps the multipart payload at the configured byte budget; over-cap
//!   requests get a 413 with the protocol's documented error envelope.
//! * `rate_limit_layer` — small custom token-bucket layer keyed by client
//!   IP. We avoid `tower::limit::RateLimitLayer` because it gates *all*
//!   requests on a single bucket rather than per-IP, which would let a
//!   single misbehaving client starve everyone else.
//! * `RateLimiterState` is the per-IP token-bucket store, shared in
//!   `Arc` across handlers.
//!
//! Rate limiting is best-effort and in-process. Multi-process deployments
//! should front the adapter with a real edge limiter (Cloudflare, nginx
//! `limit_req`, etc.).

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::Mutex;
use tower_http::limit::RequestBodyLimitLayer;

/// Per-IP token bucket store.
#[derive(Clone)]
pub struct RateLimiterState {
    inner: Arc<Mutex<RateLimiterInner>>,
}

struct RateLimiterInner {
    buckets: HashMap<IpAddr, Bucket>,
    max_per_window: u32,
    window: Duration,
    last_sweep: Instant,
}

struct Bucket {
    count: u32,
    window_start: Instant,
}

impl RateLimiterState {
    pub fn new(max_per_window: u32, window_seconds: u64) -> Self {
        Self {
            inner: Arc::new(Mutex::new(RateLimiterInner {
                buckets: HashMap::new(),
                max_per_window,
                window: Duration::from_secs(window_seconds),
                last_sweep: Instant::now(),
            })),
        }
    }

    /// Atomically check + tick the bucket for `ip`. Returns `Ok(())` when
    /// the request is allowed, `Err(retry_after_seconds)` otherwise.
    pub async fn check(&self, ip: IpAddr) -> Result<(), u64> {
        let mut inner = self.inner.lock().await;
        let now = Instant::now();
        let window = inner.window;
        let max = inner.max_per_window;
        // Evict fully-expired buckets, throttled to once per window so the
        // scan amortizes to O(1) per check. Without eviction the map grows
        // by one entry per distinct source key forever — enabling the
        // limiter would itself be a memory-exhaustion sink. An expired
        // bucket would reset to count=0 on its next hit anyway, so
        // dropping it is behavior-neutral.
        if now.duration_since(inner.last_sweep) >= window {
            inner.last_sweep = now;
            inner
                .buckets
                .retain(|_, b| now.duration_since(b.window_start) < window);
        }
        let bucket = inner.buckets.entry(ip).or_insert(Bucket {
            count: 0,
            window_start: now,
        });
        if now.duration_since(bucket.window_start) >= window {
            bucket.window_start = now;
            bucket.count = 0;
        }
        if bucket.count >= max {
            let elapsed = now.duration_since(bucket.window_start);
            let retry = window.saturating_sub(elapsed).as_secs().max(1);
            return Err(retry);
        }
        bucket.count += 1;
        Ok(())
    }

    /// Number of tracked source keys. Exposed for tests.
    #[cfg(test)]
    async fn tracked_keys(&self) -> usize {
        self.inner.lock().await.buckets.len()
    }
}

/// Construct the body-size limit layer.
pub fn body_limit_layer(max_bytes: usize) -> RequestBodyLimitLayer {
    RequestBodyLimitLayer::new(max_bytes)
}

// NB: a dedicated `from_fn_with_state` middleware function for per-IP
// rate limiting was considered but the intake handler invokes the
// limiter directly to keep the request-flow obvious. If a future
// revision needs a true tower::Layer (e.g., to rate-limit viewer reads),
// promote `RateLimiterState::check` into a layer at that point.

/// Magic-byte check shared by the intake handler. The middleware layer
/// proper can't easily inspect multipart parts, so the intake handler
/// invokes this on the screenshot bytes after the multipart split.
#[inline]
pub fn is_png(bytes: &[u8]) -> bool {
    const SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";
    bytes.starts_with(SIGNATURE)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn rate_limiter_admits_within_window() {
        let lim = RateLimiterState::new(3, 60);
        let ip: IpAddr = "10.0.0.1".parse().unwrap();
        assert!(lim.check(ip).await.is_ok());
        assert!(lim.check(ip).await.is_ok());
        assert!(lim.check(ip).await.is_ok());
        assert!(lim.check(ip).await.is_err());
    }

    #[tokio::test]
    async fn rate_limiter_independent_per_ip() {
        let lim = RateLimiterState::new(1, 60);
        let ip1: IpAddr = "10.0.0.1".parse().unwrap();
        let ip2: IpAddr = "10.0.0.2".parse().unwrap();
        assert!(lim.check(ip1).await.is_ok());
        assert!(lim.check(ip2).await.is_ok());
        assert!(lim.check(ip1).await.is_err());
    }

    #[tokio::test]
    async fn rate_limiter_evicts_idle_buckets() {
        // 1-second window, real sleep — mirrors the Python suite's
        // real-clock eviction test. Idle buckets must be swept, or a
        // client cycling source keys grows the map without bound.
        let lim = RateLimiterState::new(5, 1);
        let ip1: IpAddr = "10.0.0.1".parse().unwrap();
        let ip2: IpAddr = "10.0.0.2".parse().unwrap();
        assert!(lim.check(ip1).await.is_ok());
        assert!(lim.check(ip2).await.is_ok());
        assert_eq!(lim.tracked_keys().await, 2);
        tokio::time::sleep(Duration::from_millis(1100)).await;
        // The next check triggers the once-per-window sweep; the two idle
        // buckets go, leaving only the fresh key.
        let ip3: IpAddr = "10.0.0.3".parse().unwrap();
        assert!(lim.check(ip3).await.is_ok());
        assert_eq!(lim.tracked_keys().await, 1);
    }

    #[test]
    fn png_magic() {
        let png = b"\x89PNG\r\n\x1a\nrest";
        let jpg = b"\xFF\xD8\xFF\xE0rest";
        assert!(is_png(png));
        assert!(!is_png(jpg));
        assert!(!is_png(&[]));
    }
}
