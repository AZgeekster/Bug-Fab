//! Shared wire-report assembly for the storage backends.
//!
//! Both `FileStorage` and `SqlxStorage` persist the identical
//! `BugReportDetail` JSON shape. This module owns the single builder so
//! the two backends cannot drift — they previously carried byte-for-byte
//! copies of `build_report` (the `sqlx` copy even carried a note
//! conceding the duplication), which is exactly how the `status` and
//! lifecycle-`by` fields drift across adapters.

use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};

/// UTC now in the Python reference adapter's
/// `datetime.now(timezone.utc).isoformat()` shape
/// (e.g. `2026-04-27T15:30:00.123456+00:00`) so timestamps are identical
/// across language adapters sharing a data directory.
pub(crate) fn now_iso() -> String {
    let now: DateTime<Utc> = Utc::now();
    now.to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}

/// Assemble the persisted `BugReportDetail` JSON from validated intake
/// `metadata`. The single source of truth for the on-the-wire report
/// shape every backend writes; `now` is the shared created/updated
/// timestamp so both the top-level fields and the `created` lifecycle
/// event agree.
pub(crate) fn build_report(report_id: &str, metadata: &Value, now: &str) -> Value {
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

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: &str = "2026-01-01T00:00:00.000000+00:00";

    #[test]
    fn build_report_golden_full_payload() {
        // A fully-populated intake payload maps to a fixed wire shape.
        // This golden pins every key so the two backends — which both
        // call this one function — cannot silently drift.
        let metadata = json!({
            "protocol_version": "0.1",
            "title": "Checkout crashes",
            "client_ts": "2026-01-01T00:00:00Z",
            "report_type": "bug",
            "description": "It broke",
            "expected_behavior": "It works",
            "severity": "high",
            "tags": ["regression", "checkout"],
            "reporter": {"name": "Ada", "email": "ada@example.com", "user_id": "u-1"},
            "context": {"module": "ctx-mod", "environment": "ctx-env", "extra": "kept"},
            "module": "checkout",
            "environment": "production",
            "server_user_agent": "server-ua",
            "client_reported_user_agent": "client-ua",
            "submitted_by": "intake-user",
        });
        let expected = json!({
            "id": "bug-001",
            "protocol_version": "0.1",
            "title": "Checkout crashes",
            "client_ts": "2026-01-01T00:00:00Z",
            "report_type": "bug",
            "description": "It broke",
            "expected_behavior": "It works",
            "severity": "high",
            "status": "open",
            "tags": ["regression", "checkout"],
            "reporter": {"name": "Ada", "email": "ada@example.com", "user_id": "u-1"},
            "context": {"module": "ctx-mod", "environment": "ctx-env", "extra": "kept"},
            "module": "checkout",
            "created_at": NOW,
            "updated_at": NOW,
            "has_screenshot": true,
            "server_user_agent": "server-ua",
            "client_reported_user_agent": "client-ua",
            "environment": "production",
            "github_issue_url": Value::Null,
            "github_issue_number": Value::Null,
            "lifecycle": [{
                "action": "created",
                "by": "intake-user",
                "at": NOW,
                "fix_commit": "",
                "fix_description": "",
            }],
        });
        assert_eq!(build_report("bug-001", &metadata, NOW), expected);
    }

    #[test]
    fn build_report_defaults_and_context_fallback() {
        // A near-empty payload exercises every default: protocol_version
        // 0.1, report_type "bug", severity "medium", status "open", an
        // empty-string lifecycle `by`, and module/environment resolved
        // from `context` when the top-level keys are absent.
        let metadata = json!({
            "context": {"module": "from-ctx", "environment": "from-ctx-env"},
        });
        let report = build_report("bug-042", &metadata, NOW);
        assert_eq!(report["protocol_version"], "0.1");
        assert_eq!(report["report_type"], "bug");
        assert_eq!(report["severity"], "medium");
        assert_eq!(report["status"], "open");
        assert_eq!(report["module"], "from-ctx");
        assert_eq!(report["environment"], "from-ctx-env");
        assert_eq!(report["reporter"]["name"], "");
        assert_eq!(report["lifecycle"][0]["action"], "created");
        assert_eq!(report["lifecycle"][0]["by"], "");
        assert_eq!(report["lifecycle"][0]["at"], NOW);
    }
}
