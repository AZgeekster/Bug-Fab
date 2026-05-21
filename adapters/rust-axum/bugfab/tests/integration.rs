//! End-to-end tests exercising the protocol surface through the router.
//!
//! We use `tower::ServiceExt::oneshot` to drive the Axum router without
//! a live socket, which keeps the suite fast and deterministic.

use std::sync::Arc;

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use bugfab::storage::file::FileStorage;
use bugfab::{build_app, AppState, Settings};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use tempfile::TempDir;
use tower::util::ServiceExt;

fn png_bytes() -> Vec<u8> {
    // Minimal valid-looking PNG: signature + zero IHDR. The intake layer
    // only inspects the 8-byte signature; we don't need a parseable image.
    let sig = b"\x89PNG\r\n\x1a\n";
    let mut v = sig.to_vec();
    v.extend_from_slice(&[0u8; 64]);
    v
}

fn build_state(tmp: &TempDir) -> Arc<AppState> {
    let storage = Arc::new(FileStorage::new(tmp.path(), "").unwrap());
    let settings = Settings {
        rate_limit_max_per_window: None, // off by default for integration tests
        ..Settings::default()
    };
    Arc::new(AppState::new(storage, settings))
}

fn multipart_request(metadata: &str, screenshot: &[u8]) -> Request<Body> {
    let boundary = "----bugfab-test-boundary";
    let mut body: Vec<u8> = Vec::new();
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        b"Content-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n",
    );
    body.extend_from_slice(metadata.as_bytes());
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        b"Content-Disposition: form-data; name=\"screenshot\"; filename=\"x.png\"\r\nContent-Type: image/png\r\n\r\n",
    );
    body.extend_from_slice(screenshot);
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(format!("--{boundary}--\r\n").as_bytes());

    Request::builder()
        .method("POST")
        .uri("/bug-reports")
        .header(
            header::CONTENT_TYPE,
            format!("multipart/form-data; boundary={boundary}"),
        )
        .header(header::USER_AGENT, "test-client/1.0")
        .body(Body::from(body))
        .unwrap()
}

async fn body_to_json(resp: axum::response::Response) -> Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn submit_happy_path_returns_201_with_intake_envelope() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "Save button is unresponsive",
        "client_ts": "2026-04-27T15:29:58-07:00",
        "severity": "high",
    })
    .to_string();
    let resp = app
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CREATED);
    let body = body_to_json(resp).await;
    assert!(body.get("id").and_then(|v| v.as_str()).unwrap().starts_with("bug-"));
    assert!(body.get("stored_at").is_some());
    assert!(body.get("received_at").is_some());
}

#[tokio::test]
async fn submit_invalid_severity_rejected_with_422() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "x",
        "client_ts": "t",
        "severity": "urgent",
    })
    .to_string();
    let resp = app
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body = body_to_json(resp).await;
    assert_eq!(body["error"], "schema_error");
}

#[tokio::test]
async fn submit_unknown_protocol_version_returns_400() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));
    let metadata = json!({
        "protocol_version": "9.9",
        "title": "x",
        "client_ts": "t",
    })
    .to_string();
    let resp = app
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_to_json(resp).await;
    assert_eq!(body["error"], "unsupported_protocol_version");
}

#[tokio::test]
async fn submit_non_png_rejected_with_415() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "x",
        "client_ts": "t",
    })
    .to_string();
    let resp = app
        .oneshot(multipart_request(&metadata, b"\xFF\xD8\xFF\xE0not-a-png"))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNSUPPORTED_MEDIA_TYPE);
    let body = body_to_json(resp).await;
    assert_eq!(body["error"], "unsupported_media_type");
}

#[tokio::test]
async fn submit_oversized_screenshot_returns_413() {
    let tmp = TempDir::new().unwrap();
    let storage = Arc::new(FileStorage::new(tmp.path(), "").unwrap());
    let settings = Settings {
        max_screenshot_bytes: 1024,
        max_body_bytes: 8 * 1024,
        rate_limit_max_per_window: None,
        ..Settings::default()
    };
    let state = Arc::new(AppState::new(storage, settings));
    let app = build_app(state);
    let mut big = b"\x89PNG\r\n\x1a\n".to_vec();
    big.resize(4096, 0);
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "x",
        "client_ts": "t",
    })
    .to_string();
    let resp = app.oneshot(multipart_request(&metadata, &big)).await.unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
    let body = body_to_json(resp).await;
    assert_eq!(body["error"], "payload_too_large");
}

#[tokio::test]
async fn get_report_404_for_bad_id() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));
    let req = Request::builder()
        .uri("/reports/not-a-bug")
        .body(Body::empty())
        .unwrap();
    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn status_update_invalid_value_returns_422() {
    let tmp = TempDir::new().unwrap();
    let state = build_state(&tmp);
    let app = build_app(state.clone());
    // Create a report.
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "x",
        "client_ts": "t",
    })
    .to_string();
    let resp = app
        .clone()
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    let body = body_to_json(resp).await;
    let id = body["id"].as_str().unwrap().to_string();

    let req = Request::builder()
        .method("PUT")
        .uri(format!("/reports/{id}/status"))
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(r#"{"status":"on_fire"}"#))
        .unwrap();
    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn full_lifecycle_round_trip() {
    let tmp = TempDir::new().unwrap();
    let app = build_app(build_state(&tmp));

    // Create.
    let metadata = json!({
        "protocol_version": "0.1",
        "title": "round trip",
        "client_ts": "t",
        "severity": "low",
        "context": {"module": "checkout", "environment": "dev", "custom": 42},
    })
    .to_string();
    let resp = app
        .clone()
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CREATED);
    let id = body_to_json(resp).await["id"].as_str().unwrap().to_string();

    // Detail.
    let req = Request::builder()
        .uri(format!("/reports/{id}"))
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_to_json(resp).await;
    assert_eq!(body["status"], "open");
    assert_eq!(body["severity"], "low");
    assert_eq!(body["module"], "checkout");
    assert_eq!(body["context"]["custom"], 42);

    // List shows it.
    let req = Request::builder().uri("/reports").body(Body::empty()).unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_to_json(resp).await;
    assert_eq!(body["total"], 1);
    assert!(body["stats"].is_object());

    // Status update → fixed.
    let req = Request::builder()
        .method("PUT")
        .uri(format!("/reports/{id}/status"))
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(r#"{"status":"fixed","fix_commit":"abc123"}"#))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_to_json(resp).await;
    assert_eq!(body["status"], "fixed");
    let lifecycle = body["lifecycle"].as_array().unwrap();
    assert!(lifecycle.len() >= 2);

    // Screenshot.
    let req = Request::builder()
        .uri(format!("/reports/{id}/screenshot"))
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers().get(header::CONTENT_TYPE).unwrap(),
        "image/png"
    );

    // Bulk close (already fixed) → 1.
    let req = Request::builder()
        .method("POST")
        .uri("/bulk-close-fixed")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_to_json(resp).await;
    assert_eq!(body["closed"], 1);

    // Bulk archive closed → 1.
    let req = Request::builder()
        .method("POST")
        .uri("/bulk-archive-closed")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_to_json(resp).await;
    assert_eq!(body["archived"], 1);

    // Delete (the archived one is gone from primary; create a new and delete it).
    let resp = app
        .clone()
        .oneshot(multipart_request(&metadata, &png_bytes()))
        .await
        .unwrap();
    let id2 = body_to_json(resp).await["id"].as_str().unwrap().to_string();
    let req = Request::builder()
        .method("DELETE")
        .uri(format!("/reports/{id2}"))
        .body(Body::empty())
        .unwrap();
    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::NO_CONTENT);
}
