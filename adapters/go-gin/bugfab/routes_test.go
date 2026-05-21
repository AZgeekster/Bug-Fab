package bugfab

import (
	"bytes"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func init() {
	gin.SetMode(gin.TestMode)
}

// newTestAdapter spins up an Adapter rooted at a temp dir with the
// default config plus the screenshot cap reduced to 256 KiB so the
// 413 test can exercise it without a huge buffer.
func newTestAdapter(t *testing.T) (*Adapter, *gin.Engine) {
	t.Helper()
	cfg := DefaultConfig()
	cfg.StorageDir = t.TempDir()
	cfg.MaxScreenshotBytes = 256 * 1024
	adapter, err := New(cfg)
	if err != nil {
		t.Fatalf("New(): %v", err)
	}
	r := gin.New()
	adapter.Register(r.Group("/"))
	return adapter, r
}

// buildMultipart packages the metadata JSON string + screenshot bytes
// into a real multipart body so the route sees what a browser would
// send. Returns the body buffer and the Content-Type with boundary.
func buildMultipart(t *testing.T, metadata string, screenshot []byte) (*bytes.Buffer, string) {
	t.Helper()
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	if err := w.WriteField("metadata", metadata); err != nil {
		t.Fatalf("WriteField: %v", err)
	}
	fw, err := w.CreateFormFile("screenshot", "screenshot.png")
	if err != nil {
		t.Fatalf("CreateFormFile: %v", err)
	}
	fw.Write(screenshot)
	w.Close()
	return &buf, w.FormDataContentType()
}

func sampleMetadataJSON(severity string) string {
	m := map[string]interface{}{
		"protocol_version": "0.1",
		"title":            "Save fails",
		"client_ts":        "2026-04-27T15:00:00Z",
		"severity":         severity,
		"context": map[string]interface{}{
			"url":         "https://example.com/cart",
			"module":      "checkout",
			"environment": "prod",
			"user_agent":  "Mozilla/5.0",
		},
	}
	b, _ := json.Marshal(m)
	return string(b)
}

func TestSubmit_HappyPath(t *testing.T) {
	_, r := newTestAdapter(t)
	body, ct := buildMultipart(t, sampleMetadataJSON("high"), tinyPNG)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	req.Header.Set("User-Agent", "Mozilla/5.0 ServerSide")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("want 201, got %d body=%s", w.Code, w.Body.String())
	}
	var resp BugReportIntakeResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("response not JSON: %v", err)
	}
	if !strings.HasPrefix(resp.ID, "bug-") {
		t.Fatalf("id should start with bug-, got %q", resp.ID)
	}
	if !strings.HasPrefix(resp.StoredAt, "bug-fab://") {
		t.Fatalf("stored_at should be the opaque bug-fab:// URI, got %q", resp.StoredAt)
	}
}

func TestSubmit_RejectsUnknownSeverity(t *testing.T) {
	_, r := newTestAdapter(t)
	body, ct := buildMultipart(t, sampleMetadataJSON("urgent"), tinyPNG)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("want 422, got %d body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "urgent") {
		t.Fatalf("422 body should mention offending value, got %s", w.Body.String())
	}
}

func TestSubmit_RejectsNonPNGAs415(t *testing.T) {
	_, r := newTestAdapter(t)
	body, ct := buildMultipart(t, sampleMetadataJSON("high"), []byte("GIF89a-not-a-png"))
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("want 415, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestSubmit_RejectsOversizedAs413(t *testing.T) {
	_, r := newTestAdapter(t)
	big := make([]byte, 300*1024) // > 256 KiB cap
	copy(big, tinyPNG)
	body, ct := buildMultipart(t, sampleMetadataJSON("high"), big)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("want 413, got %d body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "limit_bytes") {
		t.Fatalf("413 body must include limit_bytes per PROTOCOL.md, got %s", w.Body.String())
	}
}

func TestSubmit_RejectsBadProtocolVersion(t *testing.T) {
	_, r := newTestAdapter(t)
	md := map[string]interface{}{
		"protocol_version": "9.9",
		"title":            "x",
		"client_ts":        "now",
	}
	mdBytes, _ := json.Marshal(md)
	body, ct := buildMultipart(t, string(mdBytes), tinyPNG)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("want 400, got %d body=%s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "unsupported_protocol_version") {
		t.Fatalf("400 should use unsupported_protocol_version code, got %s", w.Body.String())
	}
}

func TestSubmit_RejectsMissingMetadata(t *testing.T) {
	_, r := newTestAdapter(t)
	body, ct := buildMultipart(t, "", tinyPNG)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("want 400 for missing metadata, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestSubmit_RejectsMalformedMetadataAs400(t *testing.T) {
	_, r := newTestAdapter(t)
	body, ct := buildMultipart(t, "{not-valid-json", tinyPNG)
	req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
	req.Header.Set("Content-Type", ct)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("malformed JSON should be 400 not 422, got %d", w.Code)
	}
}

func TestList_EmptyReturnsZeroAndStats(t *testing.T) {
	_, r := newTestAdapter(t)
	req := httptest.NewRequest(http.MethodGet, "/reports", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	var resp BugReportListResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp.Total != 0 {
		t.Fatalf("empty store should have total=0, got %d", resp.Total)
	}
	// All four lifecycle states must appear in stats even when zero.
	for _, s := range []string{"open", "investigating", "fixed", "closed"} {
		if _, ok := resp.Stats[s]; !ok {
			t.Fatalf("stats missing key %q", s)
		}
	}
}

func TestGet_UnknownReturns404(t *testing.T) {
	_, r := newTestAdapter(t)
	req := httptest.NewRequest(http.MethodGet, "/reports/bug-999", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", w.Code)
	}
}

func TestUpdateStatus_RejectsUnknownEnum(t *testing.T) {
	a, r := newTestAdapter(t)
	id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
	body := strings.NewReader(`{"status":"resolved"}`)
	req := httptest.NewRequest(http.MethodPut, "/reports/"+id+"/status", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("want 422, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestUpdateStatus_HappyPath(t *testing.T) {
	a, r := newTestAdapter(t)
	id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
	body := strings.NewReader(`{"status":"fixed","fix_commit":"abc"}`)
	req := httptest.NewRequest(http.MethodPut, "/reports/"+id+"/status", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestDelete_ReturnsNoContent(t *testing.T) {
	a, r := newTestAdapter(t)
	id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
	req := httptest.NewRequest(http.MethodDelete, "/reports/"+id, nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204, got %d", w.Code)
	}
	// Body MUST be empty per PROTOCOL.md.
	if len(w.Body.Bytes()) != 0 {
		t.Fatalf("204 body must be empty, got %q", w.Body.String())
	}
}

func TestBulkCloseFixed_CountsAccurately(t *testing.T) {
	a, r := newTestAdapter(t)
	for i := 0; i < 2; i++ {
		id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
		a.Storage.UpdateStatus(id, "fixed", "", "", "alice")
	}
	a.Storage.SaveReport(sampleMetadata(), tinyPNG) // stays open
	req := httptest.NewRequest(http.MethodPost, "/bulk-close-fixed", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	var body map[string]int
	json.Unmarshal(w.Body.Bytes(), &body)
	if body["closed"] != 2 {
		t.Fatalf("want closed=2, got %d", body["closed"])
	}
}

func TestBulkArchiveClosed_CountsAccurately(t *testing.T) {
	a, r := newTestAdapter(t)
	for i := 0; i < 2; i++ {
		id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
		a.Storage.UpdateStatus(id, "closed", "", "", "alice")
	}
	req := httptest.NewRequest(http.MethodPost, "/bulk-archive-closed", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	var body map[string]int
	json.Unmarshal(w.Body.Bytes(), &body)
	if body["archived"] != 2 {
		t.Fatalf("want archived=2, got %d", body["archived"])
	}
}

func TestScreenshot_ReturnsPNGBytes(t *testing.T) {
	a, r := newTestAdapter(t)
	id, _ := a.Storage.SaveReport(sampleMetadata(), tinyPNG)
	req := httptest.NewRequest(http.MethodGet, "/reports/"+id+"/screenshot", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	got, _ := io.ReadAll(w.Body)
	if !bytes.HasPrefix(got, pngSignature) {
		t.Fatalf("response body should start with PNG signature, got %x...", got[:8])
	}
}

func TestRateLimit_GatesIntake(t *testing.T) {
	cfg := DefaultConfig()
	cfg.StorageDir = t.TempDir()
	cfg.MaxScreenshotBytes = 256 * 1024
	cfg.RateLimitEnabled = true
	cfg.RateLimitMax = 2
	cfg.RateLimitWindow = 60
	a, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	r := gin.New()
	a.Register(r.Group("/"))

	post := func() int {
		body, ct := buildMultipart(t, sampleMetadataJSON("high"), tinyPNG)
		req := httptest.NewRequest(http.MethodPost, "/bug-reports", body)
		req.Header.Set("Content-Type", ct)
		w := httptest.NewRecorder()
		r.ServeHTTP(w, req)
		return w.Code
	}
	// First two requests should succeed; the third trips the limiter.
	if got := post(); got != http.StatusCreated {
		t.Fatalf("req 1 want 201, got %d", got)
	}
	if got := post(); got != http.StatusCreated {
		t.Fatalf("req 2 want 201, got %d", got)
	}
	if got := post(); got != http.StatusTooManyRequests {
		t.Fatalf("req 3 want 429, got %d", got)
	}
}
