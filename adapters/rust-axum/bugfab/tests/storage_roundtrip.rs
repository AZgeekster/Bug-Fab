//! Storage-level round-trip tests for the `FileStorage` backend.
//!
//! These tests bypass the HTTP surface and exercise the trait methods
//! directly so failures localize to the storage layer.

use bugfab::schemas::BugReportDetail;
use bugfab::storage::file::FileStorage;
use bugfab::storage::{ListFilters, Storage};
use serde_json::json;
use tempfile::TempDir;

fn fake_metadata() -> serde_json::Value {
    json!({
        "protocol_version": "0.1",
        "title": "round trip",
        "client_ts": "t",
        "severity": "medium",
        "tags": ["regression"],
        "context": {"module": "checkout", "environment": "dev", "extra_key": "preserve"},
        "server_user_agent": "test-ua",
    })
}

#[tokio::test]
async fn save_and_get_preserves_extra_context_keys() {
    let tmp = TempDir::new().unwrap();
    let storage = FileStorage::new(tmp.path(), "").unwrap();
    let id = storage
        .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n\x00".to_vec())
        .await
        .unwrap();
    let detail: BugReportDetail = storage.get_report(&id).await.unwrap().unwrap();
    assert_eq!(detail.title, "round trip");
    assert_eq!(detail.module, "checkout");
    assert_eq!(detail.environment, "dev");
    assert_eq!(detail.context.extra.get("extra_key").unwrap(), "preserve");
}

#[tokio::test]
async fn list_paginates_and_filters_by_status() {
    let tmp = TempDir::new().unwrap();
    let storage = FileStorage::new(tmp.path(), "").unwrap();
    let mut ids = vec![];
    for _ in 0..5 {
        let id = storage
            .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n".to_vec())
            .await
            .unwrap();
        ids.push(id);
    }
    // Move two to "fixed".
    storage
        .update_status(&ids[0], "fixed", "", "", "test")
        .await
        .unwrap();
    storage
        .update_status(&ids[1], "fixed", "", "", "test")
        .await
        .unwrap();

    let (page, total) = storage
        .list_reports(&ListFilters::default(), 1, 3)
        .await
        .unwrap();
    assert_eq!(page.len(), 3);
    assert_eq!(total, 5);

    let f = ListFilters {
        status: Some("fixed".to_string()),
        ..Default::default()
    };
    let (page, total) = storage.list_reports(&f, 1, 20).await.unwrap();
    assert_eq!(page.len(), 2);
    assert_eq!(total, 2);
}

#[tokio::test]
async fn bulk_close_fixed_transitions_only_fixed() {
    let tmp = TempDir::new().unwrap();
    let storage = FileStorage::new(tmp.path(), "").unwrap();
    let id1 = storage
        .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n".to_vec())
        .await
        .unwrap();
    let id2 = storage
        .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n".to_vec())
        .await
        .unwrap();
    storage
        .update_status(&id1, "fixed", "", "", "test")
        .await
        .unwrap();
    // id2 stays open.

    let closed = storage.bulk_close_fixed("ops").await.unwrap();
    assert_eq!(closed, 1);
    let d = storage.get_report(&id1).await.unwrap().unwrap();
    assert_eq!(d.status, "closed");
    let d2 = storage.get_report(&id2).await.unwrap().unwrap();
    assert_eq!(d2.status, "open");
}

#[tokio::test]
async fn delete_removes_files_and_index_entry() {
    let tmp = TempDir::new().unwrap();
    let storage = FileStorage::new(tmp.path(), "").unwrap();
    let id = storage
        .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n".to_vec())
        .await
        .unwrap();
    assert!(storage.delete_report(&id).await.unwrap());
    assert!(storage.get_report(&id).await.unwrap().is_none());
    let (items, total) = storage
        .list_reports(&ListFilters::default(), 1, 10)
        .await
        .unwrap();
    assert!(items.is_empty());
    assert_eq!(total, 0);
}

#[tokio::test]
async fn id_prefix_applies() {
    let tmp = TempDir::new().unwrap();
    let storage = FileStorage::new(tmp.path(), "P").unwrap();
    let id = storage
        .save_report(fake_metadata(), b"\x89PNG\r\n\x1a\n".to_vec())
        .await
        .unwrap();
    assert!(id.starts_with("bug-P"), "{id}");
}
