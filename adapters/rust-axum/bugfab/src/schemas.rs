//! Wire-protocol schemas for Bug-Fab v0.1.
//!
//! Mirrors `bug_fab/schemas.py` from the Python reference. Enums use
//! `#[serde(rename_all = "lowercase")]` so the on-wire vocabulary matches
//! the protocol exactly. Deserialization is strict — unknown enum values
//! produce a serde error which the HTTP layer converts into 422.
//!
//! The Reporter / Context / Detail structs are kept extra-tolerant
//! (preserving unknown keys via `serde_json::Map<String, Value>` on
//! `BugReportContext.extra`) so consumer-specific diagnostic fields
//! survive round-trip per PROTOCOL.md.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// Locked severity vocabulary. Adapters MUST reject other values with 422.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Low,
    Medium,
    High,
    Critical,
}

impl Default for Severity {
    fn default() -> Self {
        Severity::Medium
    }
}

impl Severity {
    pub fn as_wire(&self) -> &'static str {
        match self {
            Severity::Low => "low",
            Severity::Medium => "medium",
            Severity::High => "high",
            Severity::Critical => "critical",
        }
    }
}

/// Locked status vocabulary for the lifecycle workflow.
///
/// Note: per PROTOCOL.md §"Deprecated-values rule", adapters MUST accept
/// deprecated values on read paths. The viewer's storage layer therefore
/// reads `status` as a raw `String` (the schemas surface it as such on
/// `BugReportDetail`/`BugReportSummary`) and only this `Status` enum is
/// used for write-side validation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Open,
    Investigating,
    Fixed,
    Closed,
}

impl Status {
    pub fn as_wire(&self) -> &'static str {
        match self {
            Status::Open => "open",
            Status::Investigating => "investigating",
            Status::Fixed => "fixed",
            Status::Closed => "closed",
        }
    }
}

/// Report type — frozen for v0.1.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReportType {
    Bug,
    FeatureRequest,
}

impl Default for ReportType {
    fn default() -> Self {
        ReportType::Bug
    }
}

impl ReportType {
    pub fn as_wire(&self) -> &'static str {
        match self {
            ReportType::Bug => "bug",
            ReportType::FeatureRequest => "feature_request",
        }
    }
}

/// Protocol version literal — only `"0.1"` is accepted on write.
pub const PROTOCOL_VERSION: &str = "0.1";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Reporter {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub email: String,
    #[serde(default)]
    pub user_id: String,
}

impl Reporter {
    /// Per PROTOCOL.md — sub-fields are opaque strings, capped at 256 chars.
    pub fn validate(&self) -> Result<(), String> {
        for (field, value) in [
            ("reporter.name", &self.name),
            ("reporter.email", &self.email),
            ("reporter.user_id", &self.user_id),
        ] {
            if value.len() > 256 {
                return Err(format!("{} exceeds 256 character cap", field));
            }
        }
        Ok(())
    }
}

/// Auto-captured browser context. Extra keys are preserved via `extra`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BugReportContext {
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub module: String,
    #[serde(default)]
    pub user_agent: String,
    #[serde(default)]
    pub viewport_width: u32,
    #[serde(default)]
    pub viewport_height: u32,
    #[serde(default)]
    pub console_errors: Vec<Value>,
    #[serde(default)]
    pub network_log: Vec<Value>,
    #[serde(default)]
    pub source_mapping: Map<String, Value>,
    #[serde(default)]
    pub app_version: String,
    #[serde(default)]
    pub environment: String,

    /// Forward-additive bag — preserves consumer-specific diagnostic keys.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

/// Submission payload (the JSON string in the multipart `metadata` field).
#[derive(Debug, Clone, Deserialize)]
pub struct BugReportCreate {
    pub protocol_version: String,
    pub title: String,
    pub client_ts: String,
    #[serde(default)]
    pub report_type: ReportType,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub expected_behavior: String,
    #[serde(default)]
    pub severity: Severity,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub reporter: Reporter,
    #[serde(default)]
    pub context: BugReportContext,
}

impl BugReportCreate {
    /// Per-field validation that serde alone can't express.
    ///
    /// Returns the human-readable detail string for a 422 response.
    pub fn validate(&self) -> Result<(), String> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(format!(
                "unsupported protocol_version: {:?}",
                self.protocol_version
            ));
        }
        let title_len = self.title.chars().count();
        if title_len == 0 || title_len > 200 {
            return Err("title must be 1..=200 characters".to_string());
        }
        if self.client_ts.is_empty() {
            return Err("client_ts is required".to_string());
        }
        self.reporter.validate()?;
        Ok(())
    }
}

/// Body of `PUT /reports/{id}/status` — strict enum validation.
#[derive(Debug, Clone, Deserialize)]
pub struct BugReportStatusUpdate {
    pub status: Status,
    #[serde(default)]
    pub fix_commit: String,
    #[serde(default)]
    pub fix_description: String,
}

/// One entry in a report's lifecycle audit log.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub action: String,
    #[serde(default)]
    pub by: String,
    pub at: String,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub fix_commit: String,
    #[serde(default)]
    pub fix_description: String,
}

/// Summary representation used for the list endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BugReportSummary {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default = "default_report_type")]
    pub report_type: String,
    #[serde(default = "default_severity")]
    pub severity: String,
    #[serde(default = "default_status")]
    pub status: String,
    #[serde(default)]
    pub module: String,
    pub created_at: String,
    #[serde(default = "default_true")]
    pub has_screenshot: bool,
    #[serde(default)]
    pub github_issue_url: Option<String>,
}

fn default_report_type() -> String {
    "bug".to_string()
}
fn default_severity() -> String {
    "medium".to_string()
}
fn default_status() -> String {
    "open".to_string()
}
fn default_true() -> bool {
    true
}

/// Full detail payload returned by detail / status endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BugReportDetail {
    pub id: String,
    pub title: String,
    pub report_type: String,
    pub severity: String,
    pub status: String,
    #[serde(default)]
    pub module: String,
    pub created_at: String,
    pub has_screenshot: bool,
    #[serde(default)]
    pub github_issue_url: Option<String>,

    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub expected_behavior: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub reporter: Reporter,
    #[serde(default)]
    pub context: BugReportContext,
    #[serde(default)]
    pub lifecycle: Vec<LifecycleEvent>,
    #[serde(default)]
    pub server_user_agent: String,
    #[serde(default)]
    pub client_reported_user_agent: String,
    #[serde(default)]
    pub environment: String,
    #[serde(default)]
    pub client_ts: String,
    #[serde(default = "default_protocol_version")]
    pub protocol_version: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub github_issue_number: Option<i64>,
}

fn default_protocol_version() -> String {
    PROTOCOL_VERSION.to_string()
}

/// Pagination envelope for `GET /reports`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BugReportListResponse {
    pub items: Vec<BugReportSummary>,
    pub total: u64,
    pub page: u32,
    pub page_size: u32,
    pub stats: std::collections::BTreeMap<String, u64>,
}

/// Minimal `201 Created` body for `POST /bug-reports`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BugReportIntakeResponse {
    pub id: String,
    pub received_at: String,
    pub stored_at: String,
    pub github_issue_url: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn severity_invalid_value_rejected() {
        let payload = r#"{"protocol_version":"0.1","title":"x","client_ts":"now","severity":"urgent"}"#;
        let err = serde_json::from_str::<BugReportCreate>(payload).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("urgent") || msg.contains("variant"), "{msg}");
    }

    #[test]
    fn severity_default_is_medium() {
        let payload = r#"{"protocol_version":"0.1","title":"x","client_ts":"now"}"#;
        let parsed: BugReportCreate = serde_json::from_str(payload).unwrap();
        assert_eq!(parsed.severity.as_wire(), "medium");
    }

    #[test]
    fn protocol_version_validate_rejects_unknown() {
        let payload = r#"{"protocol_version":"0.2","title":"x","client_ts":"now"}"#;
        let parsed: BugReportCreate = serde_json::from_str(payload).unwrap();
        let err = parsed.validate().unwrap_err();
        assert!(err.contains("protocol_version"), "{err}");
    }

    #[test]
    fn context_preserves_extra_keys() {
        let payload = r#"{"url":"x","custom_field":"keep me"}"#;
        let parsed: BugReportContext = serde_json::from_str(payload).unwrap();
        assert_eq!(parsed.extra.get("custom_field").unwrap(), "keep me");
    }
}
