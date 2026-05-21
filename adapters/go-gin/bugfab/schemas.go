// Package bugfab implements the Bug-Fab v0.1 wire protocol as a Gin
// adapter. See docs/PROTOCOL.md in the upstream repo for the
// authoritative wire spec.
//
// This file defines the in-memory schemas that mirror the Python
// reference Pydantic models in bug_fab/schemas.py. Field naming uses
// snake_case JSON tags everywhere — the wire protocol is snake_case
// in both directions and clients convert to local conventions on
// receipt.
package bugfab

import (
	"encoding/json"
	"fmt"
)

// ProtocolVersion is the only protocol version v0.1 adapters accept on
// write. Future revisions bump this and ship a deprecation window.
const ProtocolVersion = "0.1"

// Severity values are locked in v0.1. Adapters MUST reject other values
// with 422; silent coercion to "medium" fails conformance.
var ValidSeverities = map[string]struct{}{
	"low": {}, "medium": {}, "high": {}, "critical": {},
}

// Status values for the lifecycle workflow. The deprecated-values rule
// (PROTOCOL.md) means writes are strict, but reads MUST accept any
// historical value indefinitely — file-stored reports outlive enum
// revisions.
var ValidStatuses = map[string]struct{}{
	"open": {}, "investigating": {}, "fixed": {}, "closed": {},
}

// ValidReportTypes lists the two literal values the protocol freezes
// in v0.1. Unlike severity/status there is no read-side deprecation
// concern, so this is also enforced strictly.
var ValidReportTypes = map[string]struct{}{
	"bug": {}, "feature_request": {},
}

// Reporter is the optional submitter-identity sub-object.
//
// All three fields are opaque strings capped at 256 characters per the
// 2026-04-28 spec-gap decisions. The protocol intentionally does NOT
// validate format because consumer user IDs vary (UUIDs, emails,
// integers-as-strings, SSO subjects).
type Reporter struct {
	Name   string `json:"name,omitempty"`
	Email  string `json:"email,omitempty"`
	UserID string `json:"user_id,omitempty"`
}

// Validate enforces the 256-char cap. Returns a per-field error list
// shaped like the FastAPI/Pydantic envelope so consumers see a
// consistent 422 body across adapters.
func (r *Reporter) Validate() []FieldError {
	var errs []FieldError
	if len(r.Name) > 256 {
		errs = append(errs, FieldError{Loc: []string{"reporter", "name"}, Msg: "name exceeds 256 characters", Type: "value_error.too_long"})
	}
	if len(r.Email) > 256 {
		errs = append(errs, FieldError{Loc: []string{"reporter", "email"}, Msg: "email exceeds 256 characters", Type: "value_error.too_long"})
	}
	if len(r.UserID) > 256 {
		errs = append(errs, FieldError{Loc: []string{"reporter", "user_id"}, Msg: "user_id exceeds 256 characters", Type: "value_error.too_long"})
	}
	return errs
}

// BugReportContext is the auto-captured browser blob. It is
// intentionally extensible — consumers may attach extra diagnostic
// fields and the protocol preserves them verbatim through round-trip.
// The Extras map stores anything that isn't one of the named fields.
type BugReportContext struct {
	URL            string                   `json:"url,omitempty"`
	Module         string                   `json:"module,omitempty"`
	UserAgent      string                   `json:"user_agent,omitempty"`
	ViewportWidth  int                      `json:"viewport_width,omitempty"`
	ViewportHeight int                      `json:"viewport_height,omitempty"`
	ConsoleErrors  []map[string]interface{} `json:"console_errors,omitempty"`
	NetworkLog     []map[string]interface{} `json:"network_log,omitempty"`
	SourceMapping  map[string]interface{}   `json:"source_mapping,omitempty"`
	AppVersion     string                   `json:"app_version,omitempty"`
	Environment    string                   `json:"environment,omitempty"`

	// Extras carries any keys the client sent that aren't in the named
	// set above. The protocol explicitly allows extras and adapters
	// MUST preserve them so the protocol stays forward-additive.
	Extras map[string]interface{} `json:"-"`
}

// known context fields — kept in sync with BugReportContext JSON tags.
var contextKnownFields = map[string]struct{}{
	"url": {}, "module": {}, "user_agent": {}, "viewport_width": {},
	"viewport_height": {}, "console_errors": {}, "network_log": {},
	"source_mapping": {}, "app_version": {}, "environment": {},
}

// UnmarshalJSON splits the JSON into the named fields plus an Extras
// catch-all. We do this by hand rather than json.RawMessage because
// MarshalJSON needs to merge them back transparently downstream.
func (c *BugReportContext) UnmarshalJSON(data []byte) error {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	type alias BugReportContext
	var named alias
	if err := json.Unmarshal(data, &named); err != nil {
		return err
	}
	*c = BugReportContext(named)
	c.Extras = make(map[string]interface{})
	for k, v := range raw {
		if _, known := contextKnownFields[k]; known {
			continue
		}
		var val interface{}
		if err := json.Unmarshal(v, &val); err != nil {
			return fmt.Errorf("context extra %q: %w", k, err)
		}
		c.Extras[k] = val
	}
	return nil
}

// MarshalJSON re-merges the named fields and Extras back into a single
// JSON object so consumers can't tell the difference between an extra
// and a built-in.
func (c BugReportContext) MarshalJSON() ([]byte, error) {
	out := make(map[string]interface{}, len(c.Extras)+10)
	for k, v := range c.Extras {
		out[k] = v
	}
	if c.URL != "" {
		out["url"] = c.URL
	}
	if c.Module != "" {
		out["module"] = c.Module
	}
	if c.UserAgent != "" {
		out["user_agent"] = c.UserAgent
	}
	if c.ViewportWidth != 0 {
		out["viewport_width"] = c.ViewportWidth
	}
	if c.ViewportHeight != 0 {
		out["viewport_height"] = c.ViewportHeight
	}
	if c.ConsoleErrors != nil {
		out["console_errors"] = c.ConsoleErrors
	}
	if c.NetworkLog != nil {
		out["network_log"] = c.NetworkLog
	}
	if c.SourceMapping != nil {
		out["source_mapping"] = c.SourceMapping
	}
	if c.AppVersion != "" {
		out["app_version"] = c.AppVersion
	}
	if c.Environment != "" {
		out["environment"] = c.Environment
	}
	return json.Marshal(out)
}

// BugReportCreate is the payload submitted as the multipart "metadata"
// JSON string. It mirrors bug_fab.schemas.BugReportCreate.
type BugReportCreate struct {
	ProtocolVersion  string           `json:"protocol_version"`
	Title            string           `json:"title"`
	ClientTS         string           `json:"client_ts"`
	ReportType       string           `json:"report_type,omitempty"`
	Description      string           `json:"description,omitempty"`
	ExpectedBehavior string           `json:"expected_behavior,omitempty"`
	Severity         string           `json:"severity,omitempty"`
	Tags             []string         `json:"tags,omitempty"`
	Reporter         Reporter         `json:"reporter"`
	Context          BugReportContext `json:"context"`
}

// applyDefaults fills in the documented defaults so downstream code
// doesn't have to special-case empty values.
func (b *BugReportCreate) applyDefaults() {
	if b.ReportType == "" {
		b.ReportType = "bug"
	}
	if b.Severity == "" {
		b.Severity = "medium"
	}
	if b.Tags == nil {
		b.Tags = []string{}
	}
}

// Validate runs the strict checks defined in PROTOCOL.md. Returns
// (protocol-version-error, validation-errors) so the caller can map
// the first to 400 unsupported_protocol_version and the second to 422
// schema_error.
func (b *BugReportCreate) Validate() (versionErr error, fieldErrs []FieldError) {
	if b.ProtocolVersion == "" {
		fieldErrs = append(fieldErrs, FieldError{Loc: []string{"protocol_version"}, Msg: "field required", Type: "value_error.missing"})
	} else if b.ProtocolVersion != ProtocolVersion {
		versionErr = fmt.Errorf("unsupported protocol_version %q (want %q)", b.ProtocolVersion, ProtocolVersion)
		return
	}
	if b.Title == "" {
		fieldErrs = append(fieldErrs, FieldError{Loc: []string{"title"}, Msg: "title must be non-empty", Type: "value_error.too_short"})
	} else if len(b.Title) > 200 {
		fieldErrs = append(fieldErrs, FieldError{Loc: []string{"title"}, Msg: "title exceeds 200 characters", Type: "value_error.too_long"})
	}
	if b.ClientTS == "" {
		fieldErrs = append(fieldErrs, FieldError{Loc: []string{"client_ts"}, Msg: "client_ts must be non-empty", Type: "value_error.too_short"})
	}
	if b.ReportType != "" {
		if _, ok := ValidReportTypes[b.ReportType]; !ok {
			fieldErrs = append(fieldErrs, FieldError{
				Loc:  []string{"report_type"},
				Msg:  fmt.Sprintf("report_type must be one of: bug, feature_request (got %q)", b.ReportType),
				Type: "value_error.enum",
			})
		}
	}
	if b.Severity != "" {
		if _, ok := ValidSeverities[b.Severity]; !ok {
			// Silent coercion fails conformance — see PROTOCOL.md
			// § Severity enum. The conformance suite has an explicit
			// rejection test for severity="urgent".
			fieldErrs = append(fieldErrs, FieldError{
				Loc:  []string{"severity"},
				Msg:  fmt.Sprintf("severity must be one of: low, medium, high, critical (got %q)", b.Severity),
				Type: "value_error.enum",
			})
		}
	}
	fieldErrs = append(fieldErrs, b.Reporter.Validate()...)
	return
}

// BugReportStatusUpdate is the body of PUT /reports/{id}/status.
type BugReportStatusUpdate struct {
	Status         string `json:"status"`
	FixCommit      string `json:"fix_commit,omitempty"`
	FixDescription string `json:"fix_description,omitempty"`
}

// Validate enforces the locked status enum on the write path. Reads
// still tolerate any historical value per the deprecated-values rule.
func (s *BugReportStatusUpdate) Validate() []FieldError {
	if s.Status == "" {
		return []FieldError{{Loc: []string{"status"}, Msg: "field required", Type: "value_error.missing"}}
	}
	if _, ok := ValidStatuses[s.Status]; !ok {
		return []FieldError{{
			Loc:  []string{"status"},
			Msg:  fmt.Sprintf("status must be one of: open, investigating, fixed, closed (got %q)", s.Status),
			Type: "value_error.enum",
		}}
	}
	return nil
}

// LifecycleEvent records one state-changing action on a report. The
// audit log is append-only — adapters MUST NOT mutate or remove
// entries once written.
type LifecycleEvent struct {
	Action         string `json:"action"`
	By             string `json:"by"`
	At             string `json:"at"`
	Status         string `json:"status,omitempty"`
	FixCommit      string `json:"fix_commit,omitempty"`
	FixDescription string `json:"fix_description,omitempty"`
}

// BugReportSummary is the compact shape used by list responses. Fields
// here are denormalized onto the index so listing doesn't have to
// re-read every report JSON.
type BugReportSummary struct {
	ID             string  `json:"id"`
	Title          string  `json:"title"`
	ReportType     string  `json:"report_type"`
	Severity       string  `json:"severity"`
	Status         string  `json:"status"`
	Module         string  `json:"module"`
	CreatedAt      string  `json:"created_at"`
	HasScreenshot  bool    `json:"has_screenshot"`
	GitHubIssueURL *string `json:"github_issue_url"`
}

// BugReportDetail is the full payload returned by GET /reports/{id}
// and PUT /reports/{id}/status. It extends the summary with everything
// the viewer detail panel needs.
type BugReportDetail struct {
	BugReportSummary
	Description             string           `json:"description"`
	ExpectedBehavior        string           `json:"expected_behavior"`
	Tags                    []string         `json:"tags"`
	Reporter                Reporter         `json:"reporter"`
	Context                 BugReportContext `json:"context"`
	Lifecycle               []LifecycleEvent `json:"lifecycle"`
	ServerUserAgent         string           `json:"server_user_agent"`
	ClientReportedUserAgent string           `json:"client_reported_user_agent"`
	Environment             string           `json:"environment"`
	ClientTS                string           `json:"client_ts"`
	ProtocolVersion         string           `json:"protocol_version"`
	UpdatedAt               string           `json:"updated_at"`
	GitHubIssueNumber       *int             `json:"github_issue_number"`
}

// BugReportListResponse is the wire envelope for GET /reports.
//
// Stats always emits the four locked lifecycle states (even when zero)
// so consumers can rely on a stable shape for stat cards.
type BugReportListResponse struct {
	Items    []BugReportSummary `json:"items"`
	Total    int                `json:"total"`
	Page     int                `json:"page"`
	PageSize int                `json:"page_size"`
	Stats    map[string]int     `json:"stats"`
}

// BugReportIntakeResponse is the minimal 201 envelope. NOT the full
// detail — privacy: the response body may be logged by reverse
// proxies, so user-submitted free text shouldn't ride along.
type BugReportIntakeResponse struct {
	ID             string  `json:"id"`
	ReceivedAt     string  `json:"received_at"`
	StoredAt       string  `json:"stored_at"`
	GitHubIssueURL *string `json:"github_issue_url"`
}

// FieldError mirrors the FastAPI/Pydantic 422 entry shape: each entry
// has a loc path, a human message, and a machine-readable type.
type FieldError struct {
	Loc  []string `json:"loc"`
	Msg  string   `json:"msg"`
	Type string   `json:"type"`
}

// ErrorEnvelope is the JSON shape used by every non-2xx response
// except 204 and the binary 404 from /screenshot.
type ErrorEnvelope struct {
	Error             string      `json:"error"`
	Detail            interface{} `json:"detail"`
	LimitBytes        *int64      `json:"limit_bytes,omitempty"`
	RetryAfterSeconds *int        `json:"retry_after_seconds,omitempty"`
}
