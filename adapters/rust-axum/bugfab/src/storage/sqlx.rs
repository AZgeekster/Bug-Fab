//! sqlx-backed SQLite storage (feature `sqlx`).
//!
//! Schema is created on first connect via `init()`. Reports are stored as a
//! single row with a `report` JSON column plus denormalized columns
//! (`status`, `severity`, `module`, `created_at`, `environment`) for fast
//! filter/sort. Screenshots live in a sibling table `screenshots(id BLOB)`
//! to keep the SELECT-without-blob path cheap for listing.
//!
//! The choice to keep payload as JSON mirrors the file-storage layout:
//! consumer-specific extra context keys are preserved verbatim and there
//! is no schema-migration footgun when the protocol grows.

use std::path::PathBuf;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::SqlitePool;

use super::{ListFilters, Storage, StorageError};
use crate::schemas::{BugReportDetail, BugReportSummary};

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    module TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    archived_at TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_severity ON reports(severity);
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at DESC);
CREATE TABLE IF NOT EXISTS screenshots (
    id TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
    bytes BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"#;

pub struct SqlxStorage {
    pool: SqlitePool,
    id_prefix: String,
}

impl SqlxStorage {
    /// Open (and bootstrap) a SQLite database. The DSN format is whatever
    /// `sqlx::sqlite` accepts — `sqlite::memory:` is the test-friendly
    /// shorthand; `sqlite:bugfab.sqlite?mode=rwc` is the production form.
    pub async fn connect(dsn: &str, id_prefix: impl Into<String>) -> Result<Self, StorageError> {
        let opts: SqliteConnectOptions = dsn.parse()?;
        let pool = SqlitePoolOptions::new()
            .max_connections(5)
            .connect_with(opts.create_if_missing(true))
            .await?;
        // Auto-init the schema. See [[auto_init_storage_schema]] — calling
        // create_all from __init__ avoids the "viewer 500s on first GET"
        // footgun where a consumer forgets the bootstrap call.
        sqlx::query(SCHEMA).execute(&pool).await?;
        Ok(Self {
            pool,
            id_prefix: id_prefix.into(),
        })
    }

    fn now_iso() -> String {
        let now: DateTime<Utc> = Utc::now();
        now.to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
    }

    async fn next_id(&self) -> Result<String, StorageError> {
        let mut tx = self.pool.begin().await?;
        // Init the counter row on first call.
        sqlx::query("INSERT OR IGNORE INTO counters(name, value) VALUES('next_report', 1)")
            .execute(&mut *tx)
            .await?;
        let row: (i64,) =
            sqlx::query_as("SELECT value FROM counters WHERE name = 'next_report'")
                .fetch_one(&mut *tx)
                .await?;
        sqlx::query("UPDATE counters SET value = value + 1 WHERE name = 'next_report'")
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok(format!("bug-{}{:03}", self.id_prefix, row.0))
    }
}

// Reimplement payload assembly inline rather than reach across to
// `file::FileStorage` private associated functions — keeps the two
// backends independently testable. See MIGRATION_NOTES.md for the
// rationale.
mod payload {
    use super::*;

    pub(super) fn build_report(report_id: &str, metadata: &Value, now: &str) -> Value {
        let context = metadata
            .get("context")
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
        let reporter_in = metadata
            .get("reporter")
            .cloned()
            .unwrap_or_else(|| Value::Object(Default::default()));
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
            "client_reported_user_agent": metadata.get("client_reported_user_agent").cloned().unwrap_or_else(|| Value::String(String::new())),
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
}

#[async_trait]
impl Storage for SqlxStorage {
    async fn save_report(
        &self,
        metadata: Value,
        screenshot_bytes: Vec<u8>,
    ) -> Result<String, StorageError> {
        let report_id = self.next_id().await?;
        let now = Self::now_iso();
        let report = payload::build_report(&report_id, &metadata, &now);
        let payload_text = serde_json::to_string(&report)?;
        let severity = report
            .get("severity")
            .and_then(|v| v.as_str())
            .unwrap_or("medium")
            .to_string();
        let module = report
            .get("module")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let environment = report
            .get("environment")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let mut tx = self.pool.begin().await?;
        sqlx::query(
            r#"INSERT INTO reports (id, status, severity, module, environment, created_at, payload)
               VALUES (?, 'open', ?, ?, ?, ?, ?)"#,
        )
        .bind(&report_id)
        .bind(severity)
        .bind(module)
        .bind(environment)
        .bind(&now)
        .bind(&payload_text)
        .execute(&mut *tx)
        .await?;
        sqlx::query("INSERT INTO screenshots (id, bytes) VALUES (?, ?)")
            .bind(&report_id)
            .bind(&screenshot_bytes)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok(report_id)
    }

    async fn get_report(&self, report_id: &str) -> Result<Option<BugReportDetail>, StorageError> {
        let row: Option<(String,)> = sqlx::query_as("SELECT payload FROM reports WHERE id = ?")
            .bind(report_id)
            .fetch_optional(&self.pool)
            .await?;
        match row {
            Some((text,)) => {
                let value: Value = serde_json::from_str(&text)?;
                Ok(Some(serde_json::from_value(value)?))
            }
            None => Ok(None),
        }
    }

    async fn list_reports(
        &self,
        filters: &ListFilters,
        page: u32,
        page_size: u32,
    ) -> Result<(Vec<BugReportSummary>, u64), StorageError> {
        let mut sql = String::from("SELECT payload FROM reports WHERE archived_at IS NULL");
        let mut params: Vec<String> = Vec::new();
        if let Some(s) = &filters.status {
            sql.push_str(" AND status = ?");
            params.push(s.clone());
        }
        if let Some(s) = &filters.severity {
            sql.push_str(" AND severity = ?");
            params.push(s.clone());
        }
        if let Some(m) = &filters.module {
            sql.push_str(" AND module = ?");
            params.push(m.clone());
        }
        if let Some(e) = &filters.environment {
            sql.push_str(" AND environment = ?");
            params.push(e.clone());
        }
        let count_sql = format!("SELECT COUNT(*) FROM ({sql}) AS t");
        let mut count_q = sqlx::query_scalar::<_, i64>(&count_sql);
        for p in &params {
            count_q = count_q.bind(p);
        }
        let total: i64 = count_q.fetch_one(&self.pool).await?;

        sql.push_str(" ORDER BY created_at DESC LIMIT ? OFFSET ?");
        let mut q = sqlx::query_as::<_, (String,)>(&sql);
        for p in &params {
            q = q.bind(p);
        }
        q = q
            .bind(page_size as i64)
            .bind((page.saturating_sub(1) as i64) * page_size as i64);
        let rows: Vec<(String,)> = q.fetch_all(&self.pool).await?;
        let mut items = Vec::with_capacity(rows.len());
        for (text,) in rows {
            let value: Value = serde_json::from_str(&text)?;
            items.push(serde_json::from_value(value)?);
        }
        Ok((items, total as u64))
    }

    async fn get_screenshot_path(
        &self,
        _report_id: &str,
    ) -> Result<Option<PathBuf>, StorageError> {
        // SQL backend keeps blobs in-row — there's no on-disk path.
        Ok(None)
    }

    async fn get_screenshot_bytes(
        &self,
        report_id: &str,
    ) -> Result<Option<Vec<u8>>, StorageError> {
        let row: Option<(Vec<u8>,)> =
            sqlx::query_as("SELECT bytes FROM screenshots WHERE id = ?")
                .bind(report_id)
                .fetch_optional(&self.pool)
                .await?;
        Ok(row.map(|(b,)| b))
    }

    async fn update_status(
        &self,
        report_id: &str,
        status: &str,
        fix_commit: &str,
        fix_description: &str,
        by: &str,
    ) -> Result<Option<BugReportDetail>, StorageError> {
        let row: Option<(String,)> = sqlx::query_as("SELECT payload FROM reports WHERE id = ?")
            .bind(report_id)
            .fetch_optional(&self.pool)
            .await?;
        let Some((text,)) = row else {
            return Ok(None);
        };
        let mut data: Value = serde_json::from_str(&text)?;
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
        let new_text = serde_json::to_string(&data)?;
        sqlx::query("UPDATE reports SET status = ?, payload = ? WHERE id = ?")
            .bind(status)
            .bind(&new_text)
            .bind(report_id)
            .execute(&self.pool)
            .await?;
        Ok(Some(serde_json::from_value(data)?))
    }

    async fn delete_report(&self, report_id: &str) -> Result<bool, StorageError> {
        let res = sqlx::query("DELETE FROM reports WHERE id = ?")
            .bind(report_id)
            .execute(&self.pool)
            .await?;
        sqlx::query("DELETE FROM screenshots WHERE id = ?")
            .bind(report_id)
            .execute(&self.pool)
            .await
            .ok();
        Ok(res.rows_affected() > 0)
    }

    async fn archive_report(&self, report_id: &str) -> Result<bool, StorageError> {
        let now = Self::now_iso();
        let res = sqlx::query("UPDATE reports SET archived_at = ? WHERE id = ? AND archived_at IS NULL")
            .bind(now)
            .bind(report_id)
            .execute(&self.pool)
            .await?;
        Ok(res.rows_affected() > 0)
    }

    async fn bulk_close_fixed(&self, by: &str) -> Result<u64, StorageError> {
        let ids: Vec<(String,)> =
            sqlx::query_as("SELECT id FROM reports WHERE status = 'fixed'")
                .fetch_all(&self.pool)
                .await?;
        let mut count = 0u64;
        for (id,) in ids {
            if self
                .update_status(&id, "closed", "", "", by)
                .await?
                .is_some()
            {
                count += 1;
            }
        }
        Ok(count)
    }

    async fn bulk_archive_closed(&self) -> Result<u64, StorageError> {
        let now = Self::now_iso();
        let res = sqlx::query(
            "UPDATE reports SET archived_at = ? WHERE status = 'closed' AND archived_at IS NULL",
        )
        .bind(now)
        .execute(&self.pool)
        .await?;
        Ok(res.rows_affected())
    }
}
