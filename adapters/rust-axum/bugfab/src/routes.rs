//! Axum handlers for the eight Bug-Fab v0.1 endpoints.
//!
//! All handlers share the `AppState` from `lib.rs` which carries the
//! storage trait object plus rate-limiter and settings. Errors are
//! converted to the protocol's documented JSON envelope via
//! [`ApiError`].

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{ConnectInfo, Multipart, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::middleware::is_png;
use crate::schemas::{
    BugReportCreate, BugReportDetail, BugReportIntakeResponse, BugReportListResponse,
    BugReportStatusUpdate,
};
use crate::storage::ListFilters;
use crate::AppState;

/// Centralized API error type — every handler funnels through this so the
/// `{error, detail}` envelope is uniform across the protocol surface.
#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub code: &'static str,
    pub detail: Value,
}

impl ApiError {
    pub fn new(status: StatusCode, code: &'static str, detail: impl Into<Value>) -> Self {
        Self {
            status,
            code,
            detail: detail.into(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = json!({"error": self.code, "detail": self.detail});
        (self.status, Json(body)).into_response()
    }
}

#[derive(Debug, Deserialize)]
pub struct ListQuery {
    pub status: Option<String>,
    pub severity: Option<String>,
    pub module: Option<String>,
    pub environment: Option<String>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
}

fn validate_report_id(id: &str) -> Result<(), ApiError> {
    // Shape: `bug-` then an optional single ASCII letter then 1..=12 digits.
    // Mirrors the Python adapter's `^bug-[A-Za-z]?\d{1,12}$` regex without
    // a regex crate dep.
    let is_valid = id.strip_prefix("bug-").map(check_tail).unwrap_or(false);
    if !is_valid {
        return Err(not_found());
    }
    Ok(())
}

fn check_tail(tail: &str) -> bool {
    let bytes = tail.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    let digits = if bytes[0].is_ascii_alphabetic() {
        &bytes[1..]
    } else {
        bytes
    };
    !digits.is_empty() && digits.len() <= 12 && digits.iter().all(u8::is_ascii_digit)
}

fn forwarded_ip(headers: &HeaderMap) -> Option<String> {
    let xff = headers.get("x-forwarded-for")?.to_str().ok()?;
    Some(xff.split(',').next()?.trim().to_string())
}

/// Resolve the rate-limit key. `X-Forwarded-For` is client-controlled and
/// spoofable — rotating it per request would mint a fresh bucket each time
/// and defeat the limiter — so it is honored only when the direct peer is
/// in `trusted_proxies` (or the set contains `"*"`). Otherwise the direct
/// peer address is used.
fn ip_for(
    headers: &HeaderMap,
    peer: Option<&ConnectInfo<std::net::SocketAddr>>,
    trusted_proxies: &std::collections::HashSet<String>,
) -> String {
    let peer_ip = peer.map(|ConnectInfo(addr)| addr.ip().to_string());
    let peer_trusted = trusted_proxies.contains("*")
        || peer_ip
            .as_deref()
            .is_some_and(|p| trusted_proxies.contains(p));
    if peer_trusted {
        if let Some(s) = forwarded_ip(headers) {
            return s;
        }
    }
    peer_ip.unwrap_or_else(|| "unknown".to_string())
}

/// POST /bug-reports
pub async fn submit(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    peer: Option<ConnectInfo<std::net::SocketAddr>>,
    mut multipart: Multipart,
) -> Result<Response, ApiError> {
    // Rate-limit per IP — only if enabled. (The middleware variant is
    // optional; the handler-level check is the always-on default.)
    if let Some(limiter) = &state.rate_limiter {
        let ip_str = ip_for(
            &headers,
            peer.as_ref(),
            &state.settings.rate_limit_trusted_proxies,
        );
        let ip: std::net::IpAddr = ip_str
            .parse()
            .unwrap_or_else(|_| std::net::IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED));
        if let Err(retry) = limiter.check(ip).await {
            return Err(ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "rate_limited",
                json!({
                    "message": format!("rate limit exceeded; retry after {retry}s"),
                    "retry_after_seconds": retry,
                }),
            ));
        }
    }

    let mut metadata_raw: Option<String> = None;
    let mut screenshot_bytes: Option<Bytes> = None;

    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|e| ApiError::new(StatusCode::BAD_REQUEST, "validation_error", e.to_string()))?
    {
        let name = field.name().unwrap_or("").to_string();
        match name.as_str() {
            "metadata" => {
                metadata_raw = Some(field.text().await.map_err(|e| {
                    ApiError::new(StatusCode::BAD_REQUEST, "validation_error", e.to_string())
                })?);
            }
            "screenshot" => {
                screenshot_bytes = Some(field.bytes().await.map_err(|e| {
                    ApiError::new(
                        StatusCode::PAYLOAD_TOO_LARGE,
                        "payload_too_large",
                        json!({
                            "message": e.to_string(),
                            "limit_bytes": state.settings.max_screenshot_bytes,
                        }),
                    )
                })?);
            }
            _ => {
                // Ignore unknown parts — forward-compatible.
                let _ = field.bytes().await;
            }
        }
    }

    let metadata_text = metadata_raw.ok_or_else(|| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "validation_error",
            "missing 'metadata' multipart part",
        )
    })?;
    let screenshot = screenshot_bytes.ok_or_else(|| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "validation_error",
            "missing 'screenshot' multipart part",
        )
    })?;

    if screenshot.is_empty() {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "validation_error",
            "screenshot file is empty",
        ));
    }
    if screenshot.len() > state.settings.max_screenshot_bytes {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "payload_too_large",
            json!({
                "message": "screenshot exceeds configured limit",
                "limit_bytes": state.settings.max_screenshot_bytes,
            }),
        ));
    }
    if !is_png(&screenshot) {
        return Err(ApiError::new(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            "screenshot must be PNG (image/png)",
        ));
    }

    // Parse JSON → typed struct.
    let metadata_json: Value = serde_json::from_str(&metadata_text).map_err(|e| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "validation_error",
            format!("metadata is not valid JSON: {e}"),
        )
    })?;
    let payload: BugReportCreate = match serde_json::from_value(metadata_json.clone()) {
        Ok(p) => p,
        Err(e) => {
            // Distinguish unsupported-protocol-version from generic schema errors.
            let msg = e.to_string();
            let code = if msg.contains("protocol_version") {
                "unsupported_protocol_version"
            } else {
                "schema_error"
            };
            let status = if code == "unsupported_protocol_version" {
                StatusCode::BAD_REQUEST
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            };
            return Err(ApiError::new(status, code, msg));
        }
    };
    if let Err(detail) = payload.validate() {
        let code = if detail.contains("protocol_version") {
            "unsupported_protocol_version"
        } else {
            "schema_error"
        };
        let status = if code == "unsupported_protocol_version" {
            StatusCode::BAD_REQUEST
        } else {
            StatusCode::UNPROCESSABLE_ENTITY
        };
        return Err(ApiError::new(status, code, detail));
    }

    // Server-captured User-Agent is authoritative; client value is mirrored.
    let server_user_agent = headers
        .get("user-agent")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let client_user_agent = payload.context.user_agent.clone();
    let environment = if !payload.context.environment.is_empty() {
        payload.context.environment.clone()
    } else {
        metadata_json
            .get("environment")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };

    // Convert validated payload back to JSON for the storage layer (which
    // accepts a generic `Value` so it can preserve extra keys). Layer the
    // server-owned fields on top.
    let mut metadata_for_store = metadata_json;
    if let Some(obj) = metadata_for_store.as_object_mut() {
        obj.insert(
            "server_user_agent".to_string(),
            Value::String(server_user_agent),
        );
        obj.insert(
            "client_reported_user_agent".to_string(),
            Value::String(client_user_agent),
        );
        obj.insert("environment".to_string(), Value::String(environment));
    }

    let report_id = state
        .storage
        .save_report(metadata_for_store, screenshot.to_vec())
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "save_report failed");
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
                "failed to persist bug report",
            )
        })?;

    let detail = state
        .storage
        .get_report(&report_id)
        .await
        .map_err(|e| {
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
                e.to_string(),
            )
        })?
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
                "stored report could not be read back",
            )
        })?;

    let resp = BugReportIntakeResponse {
        id: report_id.clone(),
        received_at: detail.created_at,
        stored_at: format!("bug-fab://reports/{report_id}"),
        github_issue_url: None,
    };
    Ok((StatusCode::CREATED, Json(resp)).into_response())
}

/// GET /reports
pub async fn list_reports(
    State(state): State<Arc<AppState>>,
    Query(q): Query<ListQuery>,
) -> Result<Json<BugReportListResponse>, ApiError> {
    let page = q.page.unwrap_or(1).max(1);
    let page_size = q.page_size.unwrap_or(state.settings.viewer_page_size).clamp(1, 200);
    let filters = ListFilters {
        status: q.status.filter(|s| !s.is_empty()),
        severity: q.severity.filter(|s| !s.is_empty()),
        module: q.module.filter(|s| !s.is_empty()),
        environment: q.environment.filter(|s| !s.is_empty()),
    };

    let (items, total) = state
        .storage
        .list_reports(&filters, page, page_size)
        .await
        .map_err(internal)?;

    let mut stats: std::collections::BTreeMap<String, u64> =
        std::collections::BTreeMap::from_iter(
            ["open", "investigating", "fixed", "closed"]
                .iter()
                .map(|k| (k.to_string(), 0u64)),
        );
    for state_name in ["open", "investigating", "fixed", "closed"] {
        let f = ListFilters {
            status: Some(state_name.to_string()),
            ..filters.clone()
        };
        let (_items, total) = state
            .storage
            .list_reports(&f, 1, 1)
            .await
            .map_err(internal)?;
        stats.insert(state_name.to_string(), total);
    }

    Ok(Json(BugReportListResponse {
        items,
        total,
        page,
        page_size,
        stats,
    }))
}

/// GET /reports/{id}
pub async fn get_report(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<BugReportDetail>, ApiError> {
    validate_report_id(&id)?;
    let detail = state
        .storage
        .get_report(&id)
        .await
        .map_err(internal)?
        .ok_or_else(not_found)?;
    Ok(Json(detail))
}

/// GET /reports/{id}/screenshot
pub async fn get_screenshot(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    validate_report_id(&id)?;
    let bytes = state
        .storage
        .get_screenshot_bytes(&id)
        .await
        .map_err(internal)?
        .ok_or_else(not_found)?;
    Ok((
        StatusCode::OK,
        [(axum::http::header::CONTENT_TYPE, "image/png")],
        bytes,
    )
        .into_response())
}

/// PUT /reports/{id}/status
pub async fn update_status(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
    body: Result<Json<Value>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BugReportDetail>, ApiError> {
    if !state.settings.can_edit_status {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "forbidden",
            "viewer action 'can_edit_status' is disabled by configuration",
        ));
    }
    validate_report_id(&id)?;
    let Json(raw) = body.map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "schema_error",
            e.to_string(),
        )
    })?;
    let parsed: BugReportStatusUpdate = serde_json::from_value(raw).map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "schema_error",
            e.to_string(),
        )
    })?;
    let detail = state
        .storage
        .update_status(
            &id,
            parsed.status.as_wire(),
            &parsed.fix_commit,
            &parsed.fix_description,
            "viewer",
        )
        .await
        .map_err(internal)?
        .ok_or_else(not_found)?;
    Ok(Json(detail))
}

/// DELETE /reports/{id}
pub async fn delete_report(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !state.settings.can_delete {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "forbidden",
            "viewer action 'can_delete' is disabled by configuration",
        ));
    }
    validate_report_id(&id)?;
    let removed = state
        .storage
        .delete_report(&id)
        .await
        .map_err(internal)?;
    if removed {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(not_found())
    }
}

/// POST /bulk-close-fixed
pub async fn bulk_close_fixed(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Value>, ApiError> {
    if !state.settings.can_bulk {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "forbidden",
            "viewer action 'can_bulk' is disabled by configuration",
        ));
    }
    let closed = state
        .storage
        .bulk_close_fixed("viewer")
        .await
        .map_err(internal)?;
    Ok(Json(json!({"closed": closed})))
}

/// POST /bulk-archive-closed
pub async fn bulk_archive_closed(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Value>, ApiError> {
    if !state.settings.can_bulk {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "forbidden",
            "viewer action 'can_bulk' is disabled by configuration",
        ));
    }
    let archived = state
        .storage
        .bulk_archive_closed()
        .await
        .map_err(internal)?;
    Ok(Json(json!({"archived": archived})))
}

fn internal<E: std::fmt::Display>(e: E) -> ApiError {
    ApiError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal_error",
        e.to_string(),
    )
}

fn not_found() -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, "not_found", "Bug report not found")
}

#[cfg(test)]
mod ip_for_tests {
    use super::*;
    use std::collections::HashSet;
    use std::net::SocketAddr;

    fn peer(addr: &str) -> ConnectInfo<SocketAddr> {
        ConnectInfo(addr.parse().unwrap())
    }

    fn xff(value: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert("x-forwarded-for", value.parse().unwrap());
        h
    }

    #[test]
    fn untrusted_peer_forwarded_header_is_ignored() {
        // Secure default: empty trust set keys on the direct peer, so a
        // rotating spoofed header cannot mint a fresh bucket per request.
        let trusted = HashSet::new();
        let p = peer("203.0.113.5:44321");
        assert_eq!(ip_for(&xff("9.9.9.9"), Some(&p), &trusted), "203.0.113.5");
    }

    #[test]
    fn trusted_peer_forwarded_header_is_honored() {
        let trusted: HashSet<String> = ["10.0.0.1".to_string()].into();
        let p = peer("10.0.0.1:44321");
        assert_eq!(
            ip_for(&xff("9.9.9.9, 7.7.7.7"), Some(&p), &trusted),
            "9.9.9.9"
        );
    }

    #[test]
    fn wildcard_trusts_every_peer() {
        let trusted: HashSet<String> = ["*".to_string()].into();
        let p = peer("203.0.113.5:44321");
        assert_eq!(ip_for(&xff("9.9.9.9"), Some(&p), &trusted), "9.9.9.9");
    }

    #[test]
    fn no_peer_and_untrusted_yields_unknown() {
        let trusted = HashSet::new();
        assert_eq!(ip_for(&xff("9.9.9.9"), None, &trusted), "unknown");
    }
}
