package bugfab

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// minimal valid PNG: signature + IHDR + IEND. Enough to satisfy the
// adapter's magic-byte sniff in tests without dragging in image/png.
var tinyPNG = []byte{
	0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
	0x00, 0x00, 0x00, 0x0D, 'I', 'H', 'D', 'R',
	0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
	0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
	0x89, 0x00, 0x00, 0x00, 0x0D, 'I', 'D', 'A', 'T',
	0x78, 0x9C, 0x62, 0x00, 0x00, 0x00, 0x00, 0x05,
	0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00,
	0x00, 0x00, 'I', 'E', 'N', 'D', 0xAE, 0x42, 0x60, 0x82,
}

// newTestStorage spins up a FileStorage rooted at t.TempDir() — every
// test gets its own filesystem scope.
func newTestStorage(t *testing.T) *FileStorage {
	t.Helper()
	s, err := NewFileStorage(t.TempDir(), "")
	if err != nil {
		t.Fatalf("NewFileStorage: %v", err)
	}
	return s
}

func sampleMetadata() map[string]interface{} {
	return map[string]interface{}{
		"protocol_version": "0.1",
		"title":            "Save button does nothing",
		"client_ts":        "2026-04-27T15:00:00Z",
		"severity":         "high",
		"description":      "Click fails",
		"reporter":         map[string]interface{}{"email": "alice@example.com"},
		"context": map[string]interface{}{
			"url":         "https://example.com/cart",
			"module":      "checkout",
			"environment": "prod",
			"user_agent":  "Mozilla/5.0",
		},
	}
}

func TestFileStorage_SaveAndGetRoundtrip(t *testing.T) {
	s := newTestStorage(t)
	id, err := s.SaveReport(sampleMetadata(), tinyPNG)
	if err != nil {
		t.Fatalf("SaveReport: %v", err)
	}
	if id != "bug-001" {
		t.Fatalf("first id should be bug-001, got %q", id)
	}
	detail, err := s.GetReport(id)
	if err != nil || detail == nil {
		t.Fatalf("GetReport: %v / %v", detail, err)
	}
	if detail.Title != "Save button does nothing" {
		t.Fatalf("title roundtrip failed: %q", detail.Title)
	}
	if detail.Severity != "high" {
		t.Fatalf("severity roundtrip failed: %q", detail.Severity)
	}
	if detail.Module != "checkout" {
		t.Fatalf("module should derive from context.module: %q", detail.Module)
	}
	if detail.Environment != "prod" {
		t.Fatalf("environment should derive from context.environment: %q", detail.Environment)
	}
	if len(detail.Lifecycle) != 1 || detail.Lifecycle[0].Action != "created" {
		t.Fatalf("expected single 'created' lifecycle entry, got %+v", detail.Lifecycle)
	}
}

func TestFileStorage_IDsAreSequential(t *testing.T) {
	s := newTestStorage(t)
	for i := 1; i <= 3; i++ {
		id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
		want := []string{"bug-001", "bug-002", "bug-003"}[i-1]
		if id != want {
			t.Fatalf("save #%d: want %q, got %q", i, want, id)
		}
	}
}

func TestFileStorage_IDPrefix(t *testing.T) {
	dir := t.TempDir()
	s, _ := NewFileStorage(dir, "P")
	id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
	if id != "bug-P001" {
		t.Fatalf("prefix P should yield bug-P001, got %q", id)
	}
}

func TestFileStorage_OnDiskLayoutMatchesPythonReference(t *testing.T) {
	// Files cross-readable by the Python reference's FileStorage —
	// the test pins the layout so a future refactor can't drift.
	dir := t.TempDir()
	s, _ := NewFileStorage(dir, "")
	id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
	jsonPath := filepath.Join(dir, id+".json")
	pngPath := filepath.Join(dir, id+".png")
	idxPath := filepath.Join(dir, indexFilename)
	for _, p := range []string{jsonPath, pngPath, idxPath} {
		if _, err := os.Stat(p); err != nil {
			t.Fatalf("expected %s to exist: %v", p, err)
		}
	}
	// Index is JSON with {next_number, reports[]} — same shape as the
	// Python reference so either can read either's storage.
	raw, _ := os.ReadFile(idxPath)
	var idx map[string]interface{}
	if err := json.Unmarshal(raw, &idx); err != nil {
		t.Fatalf("index.json not valid JSON: %v", err)
	}
	if _, ok := idx["reports"]; !ok {
		t.Fatalf("index missing 'reports' key: %s", string(raw))
	}
	if _, ok := idx["next_number"]; !ok {
		t.Fatalf("index missing 'next_number' key: %s", string(raw))
	}
}

func TestFileStorage_ListReportsFiltersAndPaginates(t *testing.T) {
	s := newTestStorage(t)
	high := sampleMetadata()
	high["severity"] = "high"
	low := sampleMetadata()
	low["severity"] = "low"
	for i := 0; i < 5; i++ {
		s.SaveReport(high, tinyPNG)
	}
	for i := 0; i < 3; i++ {
		s.SaveReport(low, tinyPNG)
	}
	items, total, err := s.ListReports(map[string]string{"severity": "high"}, 1, 100)
	if err != nil {
		t.Fatalf("ListReports: %v", err)
	}
	if total != 5 {
		t.Fatalf("want 5 high reports, got %d", total)
	}
	if len(items) != 5 {
		t.Fatalf("want 5 items, got %d", len(items))
	}
	page2, _, _ := s.ListReports(map[string]string{"severity": "high"}, 2, 3)
	if len(page2) != 2 {
		t.Fatalf("page 2 of size 3 should have 2 items, got %d", len(page2))
	}
}

func TestFileStorage_ListReportsFiltersByEnvironment(t *testing.T) {
	// environment is denormalized into the index entry now — the filter
	// used to match nothing because buildIndexEntry omitted it.
	s := newTestStorage(t)
	prod := sampleMetadata()
	prod["environment"] = "production"
	staging := sampleMetadata()
	staging["environment"] = "staging"
	s.SaveReport(prod, tinyPNG)
	s.SaveReport(staging, tinyPNG)
	_, total, err := s.ListReports(map[string]string{"environment": "production"}, 1, 100)
	if err != nil {
		t.Fatalf("ListReports: %v", err)
	}
	if total != 1 {
		t.Fatalf("want 1 production report, got %d", total)
	}
}

func TestFileStorage_UpdateStatusAppendsLifecycle(t *testing.T) {
	s := newTestStorage(t)
	id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
	d, err := s.UpdateStatus(id, "fixed", "abc123", "fixed the listener", "alice")
	if err != nil {
		t.Fatalf("UpdateStatus: %v", err)
	}
	if d == nil {
		t.Fatalf("UpdateStatus returned nil for known id")
	}
	if d.Status != "fixed" {
		t.Fatalf("status should be fixed, got %q", d.Status)
	}
	if len(d.Lifecycle) != 2 {
		t.Fatalf("expected 2 lifecycle entries (created + status_changed), got %d", len(d.Lifecycle))
	}
	if d.Lifecycle[1].Action != "status_changed" {
		t.Fatalf("second entry should be status_changed, got %q", d.Lifecycle[1].Action)
	}
	if d.Lifecycle[1].By != "alice" {
		t.Fatalf("'by' should be alice, got %q", d.Lifecycle[1].By)
	}
}

func TestFileStorage_UpdateStatus_UnknownIDReturnsNil(t *testing.T) {
	s := newTestStorage(t)
	d, err := s.UpdateStatus("bug-999", "fixed", "", "", "alice")
	if err != nil {
		t.Fatalf("err should be nil, got %v", err)
	}
	if d != nil {
		t.Fatalf("unknown id should yield nil")
	}
}

func TestFileStorage_DeleteRemovesAllFiles(t *testing.T) {
	s := newTestStorage(t)
	id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
	ok, err := s.DeleteReport(id)
	if err != nil || !ok {
		t.Fatalf("Delete failed: ok=%v err=%v", ok, err)
	}
	d, _ := s.GetReport(id)
	if d != nil {
		t.Fatalf("deleted report should not be readable")
	}
	path, _ := s.GetScreenshotPath(id)
	if path != "" {
		t.Fatalf("screenshot path should be empty after delete, got %q", path)
	}
}

func TestFileStorage_BulkCloseFixed(t *testing.T) {
	s := newTestStorage(t)
	for i := 0; i < 3; i++ {
		id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
		s.UpdateStatus(id, "fixed", "", "", "alice")
	}
	// One additional report left "open" — must not be touched.
	s.SaveReport(sampleMetadata(), tinyPNG)
	n, err := s.BulkCloseFixed("alice")
	if err != nil {
		t.Fatalf("BulkCloseFixed: %v", err)
	}
	if n != 3 {
		t.Fatalf("expected 3 transitions, got %d", n)
	}
	_, total, _ := s.ListReports(map[string]string{"status": "closed"}, 1, 100)
	if total != 3 {
		t.Fatalf("expected 3 closed reports after bulk, got %d", total)
	}
}

func TestFileStorage_BulkArchiveClosed(t *testing.T) {
	s := newTestStorage(t)
	for i := 0; i < 2; i++ {
		id, _ := s.SaveReport(sampleMetadata(), tinyPNG)
		s.UpdateStatus(id, "closed", "", "", "alice")
	}
	n, err := s.BulkArchiveClosed()
	if err != nil {
		t.Fatalf("BulkArchiveClosed: %v", err)
	}
	if n != 2 {
		t.Fatalf("expected 2 archived, got %d", n)
	}
	// Default listing excludes archived (they're gone from the index).
	_, total, _ := s.ListReports(map[string]string{}, 1, 100)
	if total != 0 {
		t.Fatalf("after archive, default list should be empty, got %d", total)
	}
}

func TestFileStorage_RejectsBogusIDShape(t *testing.T) {
	s := newTestStorage(t)
	for _, bad := range []string{"../../etc/passwd", "bug-../escape", "bug-", "report-001", ""} {
		d, err := s.GetReport(bad)
		if err != nil {
			t.Fatalf("bogus id %q yielded error: %v", bad, err)
		}
		if d != nil {
			t.Fatalf("bogus id %q yielded a result", bad)
		}
	}
}
