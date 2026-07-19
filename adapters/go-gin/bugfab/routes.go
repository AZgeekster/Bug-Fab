package bugfab

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
)

// pngSignature is the magic-byte prefix every valid PNG file starts
// with. We sniff the first 8 bytes of the uploaded screenshot to
// reject mis-typed or hand-crafted payloads — Content-Type alone is
// client-trusted and easy to spoof.
var pngSignature = []byte{0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A}

// multipartOverheadBytes is the fixed allowance added to the sum of the
// field caps when computing the pre-parse Content-Length bound. Covers
// the multipart boundary lines and per-part headers so a request sized
// at exactly the field caps is not rejected by the coarse guard before
// the precise per-field checks can run. Mirrors the Python reference's
// intake.MULTIPART_OVERHEAD_BYTES.
const multipartOverheadBytes = 16 * 1024

// Adapter glues the eight Bug-Fab endpoints together. Build one with
// New(); mount its handlers on any Gin engine with Register().
type Adapter struct {
	Config  Config
	Storage Storage
	Limiter *RateLimiter
}

// New builds an Adapter from cfg, defaulting Storage to FileStorage
// rooted at cfg.StorageDir. Returns an error only if the storage
// directory cannot be created — the rate limiter and defaults are
// non-fallible.
func New(cfg Config) (*Adapter, error) {
	storage, err := NewFileStorage(cfg.StorageDir, cfg.IDPrefix)
	if err != nil {
		return nil, err
	}
	var limiter *RateLimiter
	if cfg.RateLimitEnabled {
		limiter = NewRateLimiter(cfg.RateLimitMax, cfg.RateLimitWindow)
	}
	return &Adapter{Config: cfg, Storage: storage, Limiter: limiter}, nil
}

// Register wires the eight protocol endpoints onto group. Pass
// engine.Group("/api/bug-fab") (or whatever prefix you want); the
// adapter never assumes a specific mount point so consumers can put
// intake and viewer behind different auth.
func (a *Adapter) Register(group *gin.RouterGroup) {
	group.POST("/bug-reports", a.handleSubmit)
	group.GET("/reports", a.handleListReports)
	group.GET("/reports/:id", a.handleGetReport)
	group.GET("/reports/:id/screenshot", a.handleGetScreenshot)
	group.PUT("/reports/:id/status", a.handleUpdateStatus)
	group.DELETE("/reports/:id", a.handleDeleteReport)
	group.POST("/bulk-close-fixed", a.handleBulkCloseFixed)
	group.POST("/bulk-archive-closed", a.handleBulkArchiveClosed)
}

// handleSubmit is POST /bug-reports — the intake endpoint. Validation
// order mirrors bug_fab.routers.submit.submit_bug_report exactly:
//
//  1. Rate-limit gate (if enabled).
//  2. Parse the metadata JSON string.
//  3. Validate the parsed metadata against schema rules.
//  4. Read the screenshot file, enforce the size cap.
//  5. Verify PNG magic bytes.
//  6. Build the persistence payload, save through the Storage.
//
// Each step has a documented status code; reordering shifts the
// observed error code and breaks conformance tests.
func (a *Adapter) handleSubmit(c *gin.Context) {
	if a.Limiter != nil {
		ip := clientIP(c, a.Config.RateLimitTrustedProxies)
		if !a.Limiter.Check(ip) {
			retry := a.Limiter.WindowSeconds()
			c.JSON(http.StatusTooManyRequests, ErrorEnvelope{
				Error:             "rate_limited",
				Detail:            "rate limit exceeded — try again later",
				RetryAfterSeconds: &retry,
			})
			return
		}
	}

	// Pre-parse size guard: reject by declared Content-Length before
	// c.PostForm/c.FormFile trigger ParseMultipartForm, which buffers the
	// whole body (32 MiB in memory, remainder spooled to temp files) —
	// without this the caps ran too late to protect the resource they
	// bound. The MaxBytesReader backstop bounds bodies sent without a
	// Content-Length (chunked) by aborting the read at the cap.
	maxRequest := a.Config.MaxScreenshotBytes + a.Config.MaxMetadataBytes + multipartOverheadBytes
	if c.Request.ContentLength > maxRequest {
		c.JSON(http.StatusRequestEntityTooLarge, ErrorEnvelope{
			Error:      "payload_too_large",
			Detail:     "request body exceeds configured maximum size",
			LimitBytes: &maxRequest,
		})
		return
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxRequest)

	metadataStr := c.PostForm("metadata")
	if metadataStr == "" {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"missing required multipart field: metadata")
		return
	}
	if int64(len(metadataStr)) > a.Config.MaxMetadataBytes {
		limit := a.Config.MaxMetadataBytes
		c.JSON(http.StatusRequestEntityTooLarge, ErrorEnvelope{
			Error:      "payload_too_large",
			Detail:     "metadata exceeds configured maximum size",
			LimitBytes: &limit,
		})
		return
	}

	var raw map[string]interface{}
	if err := json.Unmarshal([]byte(metadataStr), &raw); err != nil {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"metadata is not valid JSON: "+err.Error())
		return
	}
	var payload BugReportCreate
	if err := json.Unmarshal([]byte(metadataStr), &payload); err != nil {
		writeValidationError(c, http.StatusUnprocessableEntity, "schema_error",
			"metadata schema invalid: "+err.Error())
		return
	}
	payload.applyDefaults()
	if versionErr, fieldErrs := payload.Validate(); versionErr != nil {
		c.JSON(http.StatusBadRequest, ErrorEnvelope{
			Error:  "unsupported_protocol_version",
			Detail: versionErr.Error(),
		})
		return
	} else if len(fieldErrs) > 0 {
		c.JSON(http.StatusUnprocessableEntity, ErrorEnvelope{
			Error:  "schema_error",
			Detail: fieldErrs,
		})
		return
	}

	fileHeader, err := c.FormFile("screenshot")
	if err != nil {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"missing required multipart field: screenshot")
		return
	}
	if fileHeader.Size > a.Config.MaxScreenshotBytes {
		limit := a.Config.MaxScreenshotBytes
		c.JSON(http.StatusRequestEntityTooLarge, ErrorEnvelope{
			Error:      "payload_too_large",
			Detail:     "screenshot exceeds configured maximum size",
			LimitBytes: &limit,
		})
		return
	}
	file, err := fileHeader.Open()
	if err != nil {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"could not read screenshot upload")
		return
	}
	defer file.Close()
	screenshotBytes, err := io.ReadAll(io.LimitReader(file, a.Config.MaxScreenshotBytes+1))
	if err != nil {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"could not read screenshot upload: "+err.Error())
		return
	}
	if int64(len(screenshotBytes)) > a.Config.MaxScreenshotBytes {
		// Defense in depth: Content-Length can lie, so re-check
		// against the byte count we actually read.
		limit := a.Config.MaxScreenshotBytes
		c.JSON(http.StatusRequestEntityTooLarge, ErrorEnvelope{
			Error:      "payload_too_large",
			Detail:     "screenshot exceeds configured maximum size",
			LimitBytes: &limit,
		})
		return
	}
	if len(screenshotBytes) == 0 {
		writeValidationError(c, http.StatusBadRequest, "validation_error",
			"screenshot file is empty")
		return
	}
	if !hasPNGSignature(screenshotBytes) {
		c.JSON(http.StatusUnsupportedMediaType, ErrorEnvelope{
			Error:  "unsupported_media_type",
			Detail: "screenshot must be a PNG image (image/png)",
		})
		return
	}

	// The server is authoritative for User-Agent — the client value (if
	// any) was already preserved on payload.Context.UserAgent during
	// unmarshalling. We attach the request-header value separately so
	// the trust boundary stays explicit.
	serverUA := c.GetHeader("User-Agent")
	metadataDict := raw
	metadataDict["server_user_agent"] = serverUA
	if metadataDict["context"] == nil {
		metadataDict["context"] = map[string]interface{}{}
	}

	id, err := a.Storage.SaveReport(metadataDict, screenshotBytes)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{
			Error:  "internal_error",
			Detail: "could not persist bug report: " + err.Error(),
		})
		return
	}
	detail, err := a.Storage.GetReport(id)
	if err != nil || detail == nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{
			Error:  "internal_error",
			Detail: "stored report could not be read back",
		})
		return
	}
	c.JSON(http.StatusCreated, BugReportIntakeResponse{
		ID:         id,
		ReceivedAt: detail.CreatedAt,
		StoredAt:   "bug-fab://reports/" + id,
	})
}

// handleListReports is GET /reports — filterable JSON list. Query
// param names match PROTOCOL.md exactly (status, severity,
// environment, page, page_size).
func (a *Adapter) handleListReports(c *gin.Context) {
	page := parseIntDefault(c.Query("page"), 1)
	if page < 1 {
		page = 1
	}
	pageSize := parseIntDefault(c.Query("page_size"), 20)
	if pageSize < 1 {
		pageSize = 1
	}
	if pageSize > 200 {
		pageSize = 200
	}
	filters := stripEmpty(map[string]string{
		"status":      c.Query("status"),
		"severity":    c.Query("severity"),
		"environment": c.Query("environment"),
		"module":      c.Query("module"),
		"report_type": c.Query("report_type"),
	})
	items, total, err := a.Storage.ListReports(filters, page, pageSize)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	stats := a.computeStats()
	c.JSON(http.StatusOK, BugReportListResponse{
		Items:    items,
		Total:    total,
		Page:     page,
		PageSize: pageSize,
		Stats:    stats,
	})
}

// computeStats walks the four locked lifecycle states with one
// list-call each. Always emits the full four-key map even when zero
// so consumers can rely on a stable stat-card shape — matches the
// Python reference's behavior.
func (a *Adapter) computeStats() map[string]int {
	stats := map[string]int{}
	for _, state := range []string{"open", "investigating", "fixed", "closed"} {
		_, total, err := a.Storage.ListReports(map[string]string{"status": state}, 1, 1)
		if err == nil {
			stats[state] = total
		} else {
			stats[state] = 0
		}
	}
	return stats
}

// handleGetReport is GET /reports/{id}. The id-shape guard lives in
// FileStorage.GetReport (returns nil/nil for bad shapes) so the route
// can map any non-hit to a uniform 404 envelope.
func (a *Adapter) handleGetReport(c *gin.Context) {
	id := c.Param("id")
	detail, err := a.Storage.GetReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	if detail == nil {
		c.JSON(http.StatusNotFound, ErrorEnvelope{Error: "not_found", Detail: "bug report not found"})
		return
	}
	c.JSON(http.StatusOK, detail)
}

// handleGetScreenshot is GET /reports/{id}/screenshot. The response
// is raw image/png bytes — no JSON envelope — so consumers can drop
// the URL straight into an <img src>.
func (a *Adapter) handleGetScreenshot(c *gin.Context) {
	id := c.Param("id")
	path, err := a.Storage.GetScreenshotPath(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	if path == "" {
		c.JSON(http.StatusNotFound, ErrorEnvelope{Error: "not_found", Detail: "screenshot not found"})
		return
	}
	c.File(path)
}

// handleUpdateStatus is PUT /reports/{id}/status. Validation order:
// schema (422 on bad enum), then storage (404 on unknown id, or 422
// on a storage-layer rejection of an otherwise-legal value).
func (a *Adapter) handleUpdateStatus(c *gin.Context) {
	if !a.Config.CanEditStatus {
		writeForbidden(c, "can_edit_status")
		return
	}
	id := c.Param("id")
	var update BugReportStatusUpdate
	if err := c.ShouldBindJSON(&update); err != nil {
		writeValidationError(c, http.StatusUnprocessableEntity, "schema_error",
			"invalid status update body: "+err.Error())
		return
	}
	if errs := update.Validate(); len(errs) > 0 {
		c.JSON(http.StatusUnprocessableEntity, ErrorEnvelope{Error: "schema_error", Detail: errs})
		return
	}
	by := actorFromContext(c)
	detail, err := a.Storage.UpdateStatus(id, update.Status, update.FixCommit, update.FixDescription, by)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	if detail == nil {
		c.JSON(http.StatusNotFound, ErrorEnvelope{Error: "not_found", Detail: "bug report not found"})
		return
	}
	c.JSON(http.StatusOK, detail)
}

// handleDeleteReport is DELETE /reports/{id}. Returns 204 with no
// body on success, 404 otherwise. No soft-delete here — that lives
// on POST /bulk-archive-closed.
func (a *Adapter) handleDeleteReport(c *gin.Context) {
	if !a.Config.CanDelete {
		writeForbidden(c, "can_delete")
		return
	}
	id := c.Param("id")
	deleted, err := a.Storage.DeleteReport(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	if !deleted {
		c.JSON(http.StatusNotFound, ErrorEnvelope{Error: "not_found", Detail: "bug report not found"})
		return
	}
	c.Status(http.StatusNoContent)
}

// handleBulkCloseFixed is POST /bulk-close-fixed. Idempotent at the
// per-report level — reports already in `closed` aren't transitioned
// and don't count against the response total.
func (a *Adapter) handleBulkCloseFixed(c *gin.Context) {
	if !a.Config.CanBulk {
		writeForbidden(c, "can_bulk")
		return
	}
	by := actorFromContext(c)
	count, err := a.Storage.BulkCloseFixed(by)
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"closed": count})
}

// handleBulkArchiveClosed is POST /bulk-archive-closed. Soft-archive
// — moves files into archive/, drops from the index, but doesn't
// delete anything. Reversible with manual file moves.
func (a *Adapter) handleBulkArchiveClosed(c *gin.Context) {
	if !a.Config.CanBulk {
		writeForbidden(c, "can_bulk")
		return
	}
	count, err := a.Storage.BulkArchiveClosed()
	if err != nil {
		c.JSON(http.StatusInternalServerError, ErrorEnvelope{Error: "internal_error", Detail: err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"archived": count})
}

// clientIP resolves the rate-limit key. X-Forwarded-For is
// client-controlled and spoofable — rotating it per request would mint a
// fresh bucket each time and defeat the limiter — so the header is
// honored only when the direct peer is in trustedProxies (or the list
// contains "*"). The fallback is c.RemoteIP(), the network peer, NOT
// c.ClientIP(): Gin's ClientIP is itself forwarded-header-aware and
// trusts all proxies unless the engine is configured otherwise, which
// would reopen the hole through the back door.
func clientIP(c *gin.Context, trustedProxies []string) string {
	peer := c.RemoteIP()
	trusted := false
	for _, p := range trustedProxies {
		if p == "*" || (peer != "" && p == peer) {
			trusted = true
			break
		}
	}
	if trusted {
		if fwd := c.GetHeader("X-Forwarded-For"); fwd != "" {
			first := fwd
			if i := strings.Index(fwd, ","); i > 0 {
				first = fwd[:i]
			}
			if s := strings.TrimSpace(first); s != "" {
				return s
			}
		}
	}
	if peer != "" {
		return peer
	}
	return "unknown"
}

// actorFromContext lets consumer middleware stash an authenticated
// user identifier on the gin.Context for lifecycle audit attribution.
// Mirrors the Python reference's request.state.bug_fab_actor pattern.
// Consumers without auth get the literal "viewer" string — matches
// the Python adapter's sentinel.
func actorFromContext(c *gin.Context) string {
	if v, ok := c.Get("bug_fab_actor"); ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return "viewer"
}

// hasPNGSignature peeks at the first 8 bytes. Returns false for any
// buffer shorter than that.
func hasPNGSignature(data []byte) bool {
	if len(data) < len(pngSignature) {
		return false
	}
	for i, b := range pngSignature {
		if data[i] != b {
			return false
		}
	}
	return true
}

// parseIntDefault tolerates malformed query params — bad input gives
// the default rather than 400. Pagination params shouldn't take a
// whole request down.
func parseIntDefault(s string, fallback int) int {
	if s == "" {
		return fallback
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return fallback
	}
	return n
}

// stripEmpty drops keys whose value is empty/whitespace-only so the
// storage layer sees only meaningful filter keys.
func stripEmpty(filters map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range filters {
		if strings.TrimSpace(v) != "" {
			out[k] = strings.TrimSpace(v)
		}
	}
	return out
}

// writeValidationError emits the canonical {error, detail} envelope
// at the requested status. Kept central so the wire shape stays
// identical across all six rejection sites.
func writeValidationError(c *gin.Context, status int, code, msg string) {
	c.JSON(status, ErrorEnvelope{Error: code, Detail: msg})
}

// writeForbidden rejects a destructive viewer action disabled by
// configuration with 403 and the standard envelope, mirroring the Rust
// and Vapor adapters. action is the permission key (e.g. "can_delete").
func writeForbidden(c *gin.Context, action string) {
	c.JSON(http.StatusForbidden, ErrorEnvelope{
		Error:  "forbidden",
		Detail: "viewer action '" + action + "' is disabled by configuration",
	})
}

// ensure standard library imports are used so go vet doesn't trip
// on unused imports during partial-build experimentation.
var _ = errors.New
