//! Storage abstraction.
//!
//! `Storage` is the trait every backend implements. The `async_trait` macro
//! gives us object-safe async methods so consumers inject backends as
//! `Arc<dyn Storage>` (see `lib.rs::AppState`).
//!
//! Two backends ship in v0.1:
//!
//! * `FileStorage` — JSON files plus an `index.json` for fast listing.
//!   Mirrors the on-disk layout of the Python reference adapter so a
//!   site running Bug-Fab can swap adapter languages without migrating
//!   data.
//! * `SqlxStorage` — sqlite-first, behind the `sqlx` cargo feature.
//!
//! Per `MIGRATION_NOTES.md`, all backends must be `Send + Sync` to flow
//! through Axum's state — boxing them in `Arc<dyn Storage>` keeps the
//! call sites monomorphism-free without forcing every consumer to pick
//! a concrete type.

pub mod file;
#[cfg(feature = "sqlx")]
pub mod sqlx;

use std::path::PathBuf;

use async_trait::async_trait;
use serde_json::Value;

use crate::schemas::{BugReportDetail, BugReportSummary};

/// Filter dictionary handed to `list_reports`. Empty values are dropped
/// at the router boundary so backends only see populated keys.
#[derive(Debug, Clone, Default)]
pub struct ListFilters {
    pub status: Option<String>,
    pub severity: Option<String>,
    pub module: Option<String>,
    pub environment: Option<String>,
}

impl ListFilters {
    pub fn matches(&self, summary: &BugReportSummary, env: &str) -> bool {
        if let Some(s) = &self.status {
            if summary.status != *s {
                return false;
            }
        }
        if let Some(s) = &self.severity {
            if summary.severity != *s {
                return false;
            }
        }
        if let Some(m) = &self.module {
            if summary.module != *m {
                return false;
            }
        }
        if let Some(e) = &self.environment {
            if env != e {
                return false;
            }
        }
        true
    }
}

/// Storage trait every backend implements.
#[async_trait]
pub trait Storage: Send + Sync {
    /// Persist a new report. `metadata` is the validated wire payload
    /// (already mutated with `server_user_agent`, `environment`, etc.).
    async fn save_report(
        &self,
        metadata: Value,
        screenshot_bytes: Vec<u8>,
    ) -> Result<String, StorageError>;

    async fn get_report(&self, report_id: &str) -> Result<Option<BugReportDetail>, StorageError>;

    async fn list_reports(
        &self,
        filters: &ListFilters,
        page: u32,
        page_size: u32,
    ) -> Result<(Vec<BugReportSummary>, u64), StorageError>;

    async fn get_screenshot_path(
        &self,
        report_id: &str,
    ) -> Result<Option<PathBuf>, StorageError>;

    async fn get_screenshot_bytes(
        &self,
        report_id: &str,
    ) -> Result<Option<Vec<u8>>, StorageError>;

    async fn update_status(
        &self,
        report_id: &str,
        status: &str,
        fix_commit: &str,
        fix_description: &str,
        by: &str,
    ) -> Result<Option<BugReportDetail>, StorageError>;

    async fn delete_report(&self, report_id: &str) -> Result<bool, StorageError>;

    async fn archive_report(&self, report_id: &str) -> Result<bool, StorageError>;

    async fn bulk_close_fixed(&self, by: &str) -> Result<u64, StorageError>;

    async fn bulk_archive_closed(&self) -> Result<u64, StorageError>;
}

#[derive(Debug, thiserror::Error)]
pub enum StorageError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("serde error: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("invalid storage state: {0}")]
    Invalid(String),

    #[cfg(feature = "sqlx")]
    #[error("sqlx error: {0}")]
    Sqlx(#[from] ::sqlx::Error),
}

/// Compile-time sanity check that the trait is object-safe.
#[allow(dead_code)]
fn _assert_object_safe(_: &dyn Storage) {}
