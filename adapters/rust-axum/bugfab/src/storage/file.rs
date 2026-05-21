//! On-disk JSON storage backend.
//!
//! Mirrors `bug_fab/storage/files.py`. The on-disk layout is identical so
//! a deployment can swap between Python and Rust adapters without a data
//! migration:
//!
//! ```text
//! <storage_dir>/
//!   index.json
//!   bug-001.json
//!   bug-001.png
//!   archive/
//!     bug-002.json
//!     bug-002.png
//! ```
//!
//! Concurrency is coordinated by a single `tokio::sync::Mutex` — same
//! correctness story as the Python adapter (process-local; multi-worker
//! setups should use `SqlxStorage`).
//!
//! All writes are tmp+rename for crash safety.

use std::path::{Path, PathBuf};

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use tokio::fs;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;

use super::{ListFilters, Storage, StorageError};
use crate::schemas::{BugReportDetail, BugReportSummary};

const INDEX_FILENAME: &str = "index.json";
const ARCHIVE_SUBDIR: &str = "archive";

/// JSON-on-disk implementation of the `Storage` trait.
pub struct FileStorage {
    storage_dir: PathBuf,
    archive_dir: PathBuf,
    index_path: PathBuf,
    id_prefix: String,
    lock: Mutex<()>,
}

impl FileStorage {
    /// Construct a backend rooted at `storage_dir`. Creates the directory
    /// (and `archive/` subdir) if they do not exist yet.
    pub fn new(storage_dir: impl Into<PathBuf>, id_prefix: impl Into<String>) -> std::io::Result<Self> {
        let storage_dir = storage_dir.into();
        let archive_dir = storage_dir.join(ARCHIVE_SUBDIR);
        let index_path = storage_dir.join(INDEX_FILENAME);
        std::fs::create_dir_all(&storage_dir)?;
        std::fs::create_dir_all(&archive_dir)?;
        Ok(Self {
            storage_dir,
            archive_dir,
            index_path,
            id_prefix: id_prefix.into(),
            lock: Mutex::new(()),
        })
    }

    fn now_iso() -> String {
        let now: DateTime<Utc> = Utc::now();
        // Match the Python adapter's `datetime.now(timezone.utc).isoformat()`
        // shape (e.g., `2026-04-27T15:30:00.123456+00:00`).
        now.to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
    }

    async fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), StorageError> {
        let tmp = path.with_extension(
            format!(
                "{}.tmp",
                path.extension().and_then(|e| e.to_str()).unwrap_or("")
            ),
        );
        {
            let mut f = fs::File::create(&tmp).await?;
            f.write_all(bytes).await?;
            f.flush().await?;
        }
        fs::rename(&tmp, &path).await?;
        Ok(())
    }

    async fn read_index(&self) -> Result<Value, StorageError> {
        if !self.index_path.exists() {
            return Ok(json!({"reports": [], "next_number": 1}));
        }
        let text = fs::read_to_string(&self.index_path).await?;
        match serde_json::from_str::<Value>(&text) {
            Ok(mut v) => {
                if !v.is_object() {
                    return Ok(json!({"reports": [], "next_number": 1}));
                }
                let obj = v.as_object_mut().unwrap();
                obj.entry("reports".to_string()).or_insert(Value::Array(vec![]));
                obj.entry("next_number".to_string()).or_insert(Value::from(1));
                Ok(v)
            }
            Err(_) => Ok(json!({"reports": [], "next_number": 1})),
        }
    }

    async fn write_index(&self, index: &Value) -> Result<(), StorageError> {
        let pretty = serde_json::to_vec_pretty(index)?;
        Self::atomic_write(&self.index_path, &pretty).await
    }

    async fn read_report(&self, report_id: &str) -> Result<Option<Value>, StorageError> {
        let primary = self.storage_dir.join(format!("{report_id}.json"));
        if primary.exists() {
            let text = fs::read_to_string(&primary).await?;
            return Ok(Some(serde_json::from_str(&text)?));
        }
        let archived = self.archive_dir.join(format!("{report_id}.json"));
        if archived.exists() {
            let text = fs::read_to_string(&archived).await?;
            return Ok(Some(serde_json::from_str(&text)?));
        }
        Ok(None)
    }

    async fn write_report(&self, report_id: &str, data: &Value) -> Result<(), StorageError> {
        let mut path = self.storage_dir.join(format!("{report_id}.json"));
        if !path.exists() {
            let archived = self.archive_dir.join(format!("{report_id}.json"));
            if archived.exists() {
                path = archived;
            }
        }
        let pretty = serde_json::to_vec_pretty(data)?;
        Self::atomic_write(&path, &pretty).await
    }

    async fn write_screenshot(
        &self,
        report_id: &str,
        bytes: &[u8],
    ) -> Result<(), StorageError> {
        let path = self.storage_dir.join(format!("{report_id}.png"));
        Self::atomic_write(&path, bytes).await
    }

    fn next_id(&self, index: &Value) -> String {
        let n = index
            .get("next_number")
            .and_then(|v| v.as_u64())
            .unwrap_or(1);
        format!("bug-{}{:03}", self.id_prefix, n)
    }

    fn build_report(report_id: &str, metadata: &Value, now: &str) -> Value {
        let context = metadata
            .get("context")
            .cloned()
            .unwrap_or_else(|| Value::Object(Map::new()));
        let reporter_in = metadata
            .get("reporter")
            .cloned()
            .unwrap_or_else(|| Value::Object(Map::new()));
        let module = metadata
            .get("module")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .or_else(|| {
                context
                    .get("module")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            })
            .unwrap_or_default();
        let environment = metadata
            .get("environment")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .or_else(|| {
                context
                    .get("environment")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            })
            .unwrap_or_default();

        json!({
            "id": report_id,
            "protocol_version": metadata.get("protocol_version").cloned().unwrap_or_else(|| Value::String("0.1".into())),
            "title": metadata.get("title").cloned().unwrap_or_else(|| Value::String(String::new())),
            "client_ts": metadata.get("client_ts").cloned().unwrap_or_else(|| Value::String(String::new())),
            "report_type": metadata.get("report_type").cloned().unwrap_or_else(|| Value::String("bug".into())),
            "description": metadata.get("description").cloned().unwrap_or_else(|| Value::String(String::new())),
            "expected_behavior": metadata.get("expected_behavior").cloned().unwrap_or_else(|| Value::String(String::new())),
            "severity": metadata.get("severity").cloned().unwrap_or_else(|| Value::String("medium".into())),
            "status": "open",
            "tags": metadata.get("tags").cloned().unwrap_or_else(|| Value::Array(vec![])),
            "reporter": json!({
                "name": reporter_in.get("name").cloned().unwrap_or_else(|| Value::String(String::new())),
                "email": reporter_in.get("email").cloned().unwrap_or_else(|| Value::String(String::new())),
                "user_id": reporter_in.get("user_id").cloned().unwrap_or_else(|| Value::String(String::new())),
            }),
            "context": context,
            "module": module,
            "created_at": now,
            "updated_at": now,
            "has_screenshot": true,
            "server_user_agent": metadata.get("server_user_agent").cloned().unwrap_or_else(|| Value::String(String::new())),
            "client_reported_user_agent": metadata
                .get("client_reported_user_agent")
                .cloned()
                .unwrap_or_else(|| Value::String(String::new())),
            "environment": environment,
            "github_issue_url": Value::Null,
            "github_issue_number": Value::Null,
            "lifecycle": [json!({
                "action": "created",
                "by": metadata.get("submitted_by").cloned().unwrap_or_else(|| Value::String(String::new())),
                "at": now,
                "fix_commit": "",
                "fix_description": "",
            })],
        })
    }

    fn build_index_entry(report: &Value) -> Value {
        json!({
            "id": report.get("id").cloned().unwrap_or(Value::Null),
            "title": report.get("title").cloned().unwrap_or_else(|| Value::String(String::new())),
            "report_type": report.get("report_type").cloned().unwrap_or_else(|| Value::String("bug".into())),
            "severity": report.get("severity").cloned().unwrap_or_else(|| Value::String("medium".into())),
            "status": report.get("status").cloned().unwrap_or_else(|| Value::String("open".into())),
            "module": report.get("module").cloned().unwrap_or_else(|| Value::String(String::new())),
            "created_at": report.get("created_at").cloned().unwrap_or_else(|| Value::String(String::new())),
            "has_screenshot": report.get("has_screenshot").cloned().unwrap_or(Value::Bool(true)),
            "github_issue_url": report.get("github_issue_url").cloned().unwrap_or(Value::Null),
            "environment": report.get("environment").cloned().unwrap_or_else(|| Value::String(String::new())),
        })
    }

    async fn archive_one(&self, report_id: &str) -> Result<bool, StorageError> {
        let json_src = self.storage_dir.join(format!("{report_id}.json"));
        let png_src = self.storage_dir.join(format!("{report_id}.png"));
        if !json_src.exists() && !png_src.exists() {
            return Ok(false);
        }
        if json_src.exists() {
            let dest = self.archive_dir.join(format!("{report_id}.json"));
            fs::rename(&json_src, &dest).await?;
        }
        if png_src.exists() {
            let dest = self.archive_dir.join(format!("{report_id}.png"));
            fs::rename(&png_src, &dest).await?;
        }
        let mut index = self.read_index().await?;
        if let Some(arr) = index.get_mut("reports").and_then(|v| v.as_array_mut()) {
            arr.retain(|e| e.get("id").and_then(|v| v.as_str()) != Some(report_id));
        }
        self.write_index(&index).await?;
        Ok(true)
    }
}

fn detail_from_value(data: Value) -> Result<BugReportDetail, StorageError> {
    serde_json::from_value::<BugReportDetail>(data).map_err(StorageError::from)
}

fn summary_from_entry(entry: &Value) -> Result<BugReportSummary, StorageError> {
    serde_json::from_value::<BugReportSummary>(entry.clone()).map_err(StorageError::from)
}

#[async_trait]
impl Storage for FileStorage {
    async fn save_report(
        &self,
        metadata: Value,
        screenshot_bytes: Vec<u8>,
    ) -> Result<String, StorageError> {
        let _guard = self.lock.lock().await;
        let mut index = self.read_index().await?;
        let report_id = self.next_id(&index);
        let now = Self::now_iso();
        let report = Self::build_report(&report_id, &metadata, &now);

        self.write_screenshot(&report_id, &screenshot_bytes).await?;
        self.write_report(&report_id, &report).await?;

        let entry = Self::build_index_entry(&report);
        if let Some(arr) = index.get_mut("reports").and_then(|v| v.as_array_mut()) {
            arr.push(entry);
        }
        let next = index
            .get("next_number")
            .and_then(|v| v.as_u64())
            .unwrap_or(1)
            + 1;
        if let Some(obj) = index.as_object_mut() {
            obj.insert("next_number".to_string(), Value::from(next));
        }
        self.write_index(&index).await?;

        Ok(report_id)
    }

    async fn get_report(&self, report_id: &str) -> Result<Option<BugReportDetail>, StorageError> {
        let _guard = self.lock.lock().await;
        let Some(data) = self.read_report(report_id).await? else {
            return Ok(None);
        };
        Ok(Some(detail_from_value(data)?))
    }

    async fn list_reports(
        &self,
        filters: &ListFilters,
        page: u32,
        page_size: u32,
    ) -> Result<(Vec<BugReportSummary>, u64), StorageError> {
        let _guard = self.lock.lock().await;
        let index = self.read_index().await?;
        let empty: Vec<Value> = vec![];
        let entries = index
            .get("reports")
            .and_then(|v| v.as_array())
            .unwrap_or(&empty);

        let mut matched: Vec<(BugReportSummary, String)> = Vec::with_capacity(entries.len());
        for entry in entries {
            let summary = summary_from_entry(entry)?;
            let env = entry
                .get("environment")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if filters.matches(&summary, &env) {
                matched.push((summary, env));
            }
        }
        matched.sort_by(|a, b| b.0.created_at.cmp(&a.0.created_at));
        let total = matched.len() as u64;
        let start = page.saturating_sub(1) as usize * page_size as usize;
        let end = std::cmp::min(start + page_size as usize, matched.len());
        let page_items = if start >= matched.len() {
            vec![]
        } else {
            matched[start..end].iter().map(|(s, _)| s.clone()).collect()
        };
        Ok((page_items, total))
    }

    async fn get_screenshot_path(
        &self,
        report_id: &str,
    ) -> Result<Option<PathBuf>, StorageError> {
        let primary = self.storage_dir.join(format!("{report_id}.png"));
        if primary.exists() {
            return Ok(Some(primary));
        }
        let archived = self.archive_dir.join(format!("{report_id}.png"));
        if archived.exists() {
            return Ok(Some(archived));
        }
        Ok(None)
    }

    async fn get_screenshot_bytes(
        &self,
        report_id: &str,
    ) -> Result<Option<Vec<u8>>, StorageError> {
        match self.get_screenshot_path(report_id).await? {
            Some(p) => Ok(Some(fs::read(p).await?)),
            None => Ok(None),
        }
    }

    async fn update_status(
        &self,
        report_id: &str,
        status: &str,
        fix_commit: &str,
        fix_description: &str,
        by: &str,
    ) -> Result<Option<BugReportDetail>, StorageError> {
        let _guard = self.lock.lock().await;
        let Some(mut data) = self.read_report(report_id).await? else {
            return Ok(None);
        };
        let now = Self::now_iso();
        if let Some(obj) = data.as_object_mut() {
            obj.insert("status".to_string(), Value::String(status.to_string()));
            obj.insert("updated_at".to_string(), Value::String(now.clone()));
            let event = json!({
                "action": "status_changed",
                "by": by,
                "at": now,
                "status": status,
                "fix_commit": fix_commit,
                "fix_description": fix_description,
            });
            let lifecycle = obj
                .entry("lifecycle".to_string())
                .or_insert_with(|| Value::Array(vec![]));
            if let Some(arr) = lifecycle.as_array_mut() {
                arr.push(event);
            }
        }
        self.write_report(report_id, &data).await?;

        let mut index = self.read_index().await?;
        if let Some(arr) = index.get_mut("reports").and_then(|v| v.as_array_mut()) {
            for entry in arr.iter_mut() {
                if entry.get("id").and_then(|v| v.as_str()) == Some(report_id) {
                    if let Some(obj) = entry.as_object_mut() {
                        obj.insert("status".to_string(), Value::String(status.to_string()));
                    }
                    break;
                }
            }
        }
        self.write_index(&index).await?;

        Ok(Some(detail_from_value(data)?))
    }

    async fn delete_report(&self, report_id: &str) -> Result<bool, StorageError> {
        let _guard = self.lock.lock().await;
        let candidates = [
            self.storage_dir.join(format!("{report_id}.json")),
            self.storage_dir.join(format!("{report_id}.png")),
            self.archive_dir.join(format!("{report_id}.json")),
            self.archive_dir.join(format!("{report_id}.png")),
        ];
        let mut removed = false;
        for path in candidates.iter() {
            if path.exists() {
                fs::remove_file(path).await?;
                removed = true;
            }
        }
        if removed {
            let mut index = self.read_index().await?;
            if let Some(arr) = index.get_mut("reports").and_then(|v| v.as_array_mut()) {
                arr.retain(|e| e.get("id").and_then(|v| v.as_str()) != Some(report_id));
            }
            self.write_index(&index).await?;
        }
        Ok(removed)
    }

    async fn archive_report(&self, report_id: &str) -> Result<bool, StorageError> {
        let _guard = self.lock.lock().await;
        self.archive_one(report_id).await
    }

    async fn bulk_close_fixed(&self, by: &str) -> Result<u64, StorageError> {
        let ids: Vec<String> = {
            let _guard = self.lock.lock().await;
            let index = self.read_index().await?;
            let empty: Vec<Value> = vec![];
            index
                .get("reports")
                .and_then(|v| v.as_array())
                .unwrap_or(&empty)
                .iter()
                .filter(|e| e.get("status").and_then(|v| v.as_str()) == Some("fixed"))
                .filter_map(|e| e.get("id").and_then(|v| v.as_str()).map(str::to_string))
                .collect()
        };
        let mut closed = 0u64;
        for id in ids {
            if self
                .update_status(&id, "closed", "", "", by)
                .await?
                .is_some()
            {
                closed += 1;
            }
        }
        Ok(closed)
    }

    async fn bulk_archive_closed(&self) -> Result<u64, StorageError> {
        let _guard = self.lock.lock().await;
        let ids: Vec<String> = {
            let index = self.read_index().await?;
            let empty: Vec<Value> = vec![];
            index
                .get("reports")
                .and_then(|v| v.as_array())
                .unwrap_or(&empty)
                .iter()
                .filter(|e| e.get("status").and_then(|v| v.as_str()) == Some("closed"))
                .filter_map(|e| e.get("id").and_then(|v| v.as_str()).map(str::to_string))
                .collect()
        };
        let mut archived = 0u64;
        for id in ids {
            if self.archive_one(&id).await? {
                archived += 1;
            }
        }
        Ok(archived)
    }
}
