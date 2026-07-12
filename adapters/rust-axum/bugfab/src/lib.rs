//! Bug-Fab adapter — Axum (Rust) reference draft.
//!
//! This crate wires the Bug-Fab v0.1 wire protocol to Axum 0.7. Two
//! routers are exposed (`intake_router` and `viewer_router`) so consumers
//! can apply different auth middleware to submission vs. administration
//! (the protocol's "mount-point delegation" pattern — see
//! `docs/PROTOCOL.md`).
//!
//! Quick start:
//!
//! ```no_run
//! use std::sync::Arc;
//! use std::net::SocketAddr;
//! use bugfab::{build_app, AppState, Settings};
//! use bugfab::storage::file::FileStorage;
//!
//! # async fn run() -> Result<(), Box<dyn std::error::Error>> {
//! let storage = Arc::new(FileStorage::new("./bug-fab-data", "")?);
//! let state = Arc::new(AppState::new(storage, Settings::default()));
//! let app = build_app(state);
//! let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
//! let listener = tokio::net::TcpListener::bind(addr).await?;
//! axum::serve(listener, app.into_make_service_with_connect_info::<SocketAddr>()).await?;
//! # Ok(())
//! # }
//! ```
//!
//! Storage backends live under [`storage`]. The default backend is
//! [`storage::file::FileStorage`]; the SQLite-backed one
//! ([`storage::sqlx::SqlxStorage`]) is gated by the `sqlx` cargo feature.

pub mod middleware;
pub mod routes;
pub mod schemas;
pub mod storage;

use std::sync::Arc;

use axum::routing::{delete, get, post, put};
use axum::Router;

/// Runtime settings — kept tiny on purpose. Anything that isn't a
/// hot-path concern (e.g., GitHub sync) is wired by consumers as a
/// separate optional service rather than balooning this struct.
#[derive(Debug, Clone)]
pub struct Settings {
    /// Maximum screenshot size in bytes. Default 10 MiB per PROTOCOL.md.
    pub max_screenshot_bytes: usize,
    /// Total multipart body cap in bytes. Default 11 MiB.
    pub max_body_bytes: usize,
    /// Viewer page size default for `GET /reports` when caller omits it.
    pub viewer_page_size: u32,
    /// Per-IP rate-limit budget (requests per window). `None` disables.
    pub rate_limit_max_per_window: Option<u32>,
    /// Rate-limit window in seconds.
    pub rate_limit_window_seconds: u64,
    /// Direct-peer addresses allowed to supply `X-Forwarded-For` as the
    /// rate-limit key. The header is client-controlled and spoofable, so
    /// it is honored only when the connecting peer is in this set; empty
    /// (the secure default) meters by the direct peer address. `"*"`
    /// trusts every peer. Mirrors the Python reference's
    /// `rate_limit_trusted_proxies`.
    pub rate_limit_trusted_proxies: std::collections::HashSet<String>,
    /// Viewer permissions — mirror the Python adapter's flags.
    pub can_edit_status: bool,
    pub can_delete: bool,
    pub can_bulk: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            max_screenshot_bytes: 10 * 1024 * 1024,
            max_body_bytes: 11 * 1024 * 1024,
            viewer_page_size: 20,
            rate_limit_max_per_window: Some(60),
            rate_limit_window_seconds: 60,
            rate_limit_trusted_proxies: std::collections::HashSet::new(),
            can_edit_status: true,
            can_delete: true,
            can_bulk: true,
        }
    }
}

/// Shared state injected into every handler.
pub struct AppState {
    pub storage: Arc<dyn storage::Storage>,
    pub settings: Settings,
    pub rate_limiter: Option<middleware::RateLimiterState>,
}

impl AppState {
    pub fn new(storage: Arc<dyn storage::Storage>, settings: Settings) -> Self {
        let rate_limiter = settings.rate_limit_max_per_window.map(|max| {
            middleware::RateLimiterState::new(max, settings.rate_limit_window_seconds)
        });
        Self {
            storage,
            settings,
            rate_limiter,
        }
    }
}

/// The intake router. Mount this where unauthenticated users can POST.
pub fn intake_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/bug-reports", post(routes::submit))
        .layer(middleware::body_limit_layer(state.settings.max_body_bytes))
        .with_state(state)
}

/// The viewer router. Mount this behind your admin auth middleware.
pub fn viewer_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/reports", get(routes::list_reports))
        .route("/reports/:id", get(routes::get_report))
        .route("/reports/:id", delete(routes::delete_report))
        .route("/reports/:id/screenshot", get(routes::get_screenshot))
        .route("/reports/:id/status", put(routes::update_status))
        .route("/bulk-close-fixed", post(routes::bulk_close_fixed))
        .route("/bulk-archive-closed", post(routes::bulk_archive_closed))
        .with_state(state)
}

/// Convenience: build a single combined `Router` covering both intake and
/// viewer at the protocol's canonical paths. Production deployments
/// generally want to mount intake and viewer separately (different auth);
/// this helper is for examples, tests, and POCs.
pub fn build_app(state: Arc<AppState>) -> Router {
    Router::new()
        .merge(intake_router(state.clone()))
        .merge(viewer_router(state))
}
