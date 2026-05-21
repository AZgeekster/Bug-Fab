package bugfab

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestBugReportCreate_DefaultsApplied(t *testing.T) {
	var b BugReportCreate
	b.ProtocolVersion = "0.1"
	b.Title = "Save fails"
	b.ClientTS = "2026-04-27T15:00:00Z"
	b.applyDefaults()
	if b.ReportType != "bug" {
		t.Fatalf("want default report_type=bug, got %q", b.ReportType)
	}
	if b.Severity != "medium" {
		t.Fatalf("want default severity=medium, got %q", b.Severity)
	}
	if b.Tags == nil {
		t.Fatalf("tags should default to non-nil empty slice")
	}
}

func TestBugReportCreate_Validate_MissingProtocolVersion(t *testing.T) {
	b := BugReportCreate{Title: "x", ClientTS: "now"}
	versionErr, fieldErrs := b.Validate()
	if versionErr != nil {
		t.Fatalf("missing version should be field error, not version error")
	}
	if len(fieldErrs) == 0 {
		t.Fatalf("expected field error for missing protocol_version")
	}
}

func TestBugReportCreate_Validate_UnsupportedProtocolVersion(t *testing.T) {
	b := BugReportCreate{ProtocolVersion: "9.9", Title: "x", ClientTS: "now"}
	versionErr, _ := b.Validate()
	if versionErr == nil {
		t.Fatalf("expected version error for 9.9")
	}
}

func TestBugReportCreate_Validate_RejectsUnknownSeverity(t *testing.T) {
	// The conformance suite has an explicit severity="urgent" rejection
	// test — silently coercing to "medium" would fail conformance.
	b := BugReportCreate{
		ProtocolVersion: "0.1",
		Title:           "x",
		ClientTS:        "now",
		Severity:        "urgent",
	}
	_, fieldErrs := b.Validate()
	if len(fieldErrs) == 0 {
		t.Fatalf("expected field error for severity=urgent")
	}
	if !strings.Contains(fieldErrs[0].Msg, "urgent") {
		t.Fatalf("severity error should mention offending value, got %q", fieldErrs[0].Msg)
	}
	if fieldErrs[0].Type != "value_error.enum" {
		t.Fatalf("want enum-error type, got %q", fieldErrs[0].Type)
	}
}

func TestBugReportCreate_Validate_AcceptsAllLockedSeverities(t *testing.T) {
	for _, s := range []string{"low", "medium", "high", "critical"} {
		b := BugReportCreate{
			ProtocolVersion: "0.1",
			Title:           "x",
			ClientTS:        "now",
			Severity:        s,
		}
		_, fieldErrs := b.Validate()
		if len(fieldErrs) != 0 {
			t.Fatalf("severity %q should validate, got errors %+v", s, fieldErrs)
		}
	}
}

func TestBugReportCreate_Validate_RejectsTitleOver200(t *testing.T) {
	b := BugReportCreate{
		ProtocolVersion: "0.1",
		Title:           strings.Repeat("x", 201),
		ClientTS:        "now",
	}
	_, fieldErrs := b.Validate()
	if len(fieldErrs) == 0 {
		t.Fatalf("expected field error for 201-char title")
	}
}

func TestBugReportCreate_Validate_RejectsReporterFieldOver256(t *testing.T) {
	b := BugReportCreate{
		ProtocolVersion: "0.1",
		Title:           "x",
		ClientTS:        "now",
		Reporter:        Reporter{Email: strings.Repeat("a", 257)},
	}
	_, fieldErrs := b.Validate()
	if len(fieldErrs) == 0 {
		t.Fatalf("expected field error for 257-char reporter.email")
	}
}

func TestStatusUpdate_Validate_RejectsUnknown(t *testing.T) {
	u := BugReportStatusUpdate{Status: "resolved"}
	errs := u.Validate()
	if len(errs) == 0 {
		t.Fatalf("status=resolved must be rejected on write")
	}
}

func TestStatusUpdate_Validate_AcceptsKnown(t *testing.T) {
	for _, s := range []string{"open", "investigating", "fixed", "closed"} {
		u := BugReportStatusUpdate{Status: s}
		if errs := u.Validate(); len(errs) != 0 {
			t.Fatalf("status %q should validate, got %+v", s, errs)
		}
	}
}

func TestBugReportContext_RoundtripPreservesExtras(t *testing.T) {
	// Extra keys MUST survive round-trip per the wire protocol's
	// "context allows extras" guarantee.
	in := `{"url":"https://x","custom_field":"keep-me","module":"checkout"}`
	var ctx BugReportContext
	if err := json.Unmarshal([]byte(in), &ctx); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}
	if ctx.URL != "https://x" {
		t.Fatalf("URL not parsed: %q", ctx.URL)
	}
	if ctx.Extras["custom_field"] != "keep-me" {
		t.Fatalf("extra field not captured: %+v", ctx.Extras)
	}
	out, err := json.Marshal(ctx)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}
	var roundtrip map[string]interface{}
	if err := json.Unmarshal(out, &roundtrip); err != nil {
		t.Fatalf("roundtrip unmarshal failed: %v", err)
	}
	if roundtrip["custom_field"] != "keep-me" {
		t.Fatalf("extra field lost on marshal: %s", string(out))
	}
	if roundtrip["url"] != "https://x" {
		t.Fatalf("named field lost on marshal: %s", string(out))
	}
}
