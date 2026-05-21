package bugfab

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"sync"
	"time"
)

// Storage is the abstraction the routes use. The default
// implementation, FileStorage, mirrors the Python reference's on-disk
// layout exactly so a Go-stored report is readable by the Python
// adapter and vice versa.
type Storage interface {
	SaveReport(metadata map[string]interface{}, screenshot []byte) (string, error)
	GetReport(id string) (*BugReportDetail, error)
	ListReports(filters map[string]string, page, pageSize int) ([]BugReportSummary, int, error)
	GetScreenshotPath(id string) (string, error)
	UpdateStatus(id, status, fixCommit, fixDescription, by string) (*BugReportDetail, error)
	DeleteReport(id string) (bool, error)
	BulkCloseFixed(by string) (int, error)
	BulkArchiveClosed() (int, error)
}

// reportIDRe is the path-traversal guard. Matches bug-NNN with an
// optional one-letter environment prefix (e.g., bug-P038 / bug-D012)
// per the protocol id regex documented in PROTOCOL.md.
var reportIDRe = regexp.MustCompile(`^bug-[A-Za-z]?\d{1,12}$`)

// ErrReportNotFound is returned by FileStorage when an id-shape-valid
// id has no on-disk file.
var ErrReportNotFound = errors.New("bug-fab: report not found")

const (
	indexFilename = "index.json"
	archiveSubdir = "archive"
)

// FileStorage is the zero-dependency on-disk backend. Layout matches
// bug_fab/storage/files.py exactly:
//
//	<storage_dir>/
//	├── index.json
//	├── bug-001.json
//	├── bug-001.png
//	└── archive/
//	    ├── bug-002.json
//	    └── bug-002.png
//
// Atomicity uses tmp+rename for both the index and per-report JSON
// (audit finding B3 in the reference — the prior implementation wrote
// in place and could corrupt the index on crash).
//
// Concurrency is process-local: a sync.Mutex serializes index reads
// and per-report writes. Multi-process deployments must either run a
// single worker or layer an external lock — same caveat as the Python
// reference.
type FileStorage struct {
	storageDir string
	idPrefix   string
	mu         sync.Mutex
}

// NewFileStorage returns a FileStorage rooted at storageDir. The
// directory and its archive/ subdir are created if missing.
// idPrefix is the optional environment letter (e.g., "P" for prod,
// "D" for dev) — assigned ids become bug-P001 / bug-D001.
func NewFileStorage(storageDir, idPrefix string) (*FileStorage, error) {
	if err := os.MkdirAll(storageDir, 0o755); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(storageDir, archiveSubdir), 0o755); err != nil {
		return nil, err
	}
	return &FileStorage{storageDir: storageDir, idPrefix: idPrefix}, nil
}

func nowISO() string {
	return time.Now().UTC().Format(time.RFC3339Nano)
}

// atomicWrite stages the payload at <path>.tmp then renames over the
// target. os.Rename is atomic on POSIX and Windows for in-volume
// renames; partial-write windows are eliminated.
func atomicWrite(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// indexFile is the denormalized listing used to answer GET /reports
// without re-reading every report JSON.
type indexFile struct {
	NextNumber int                      `json:"next_number"`
	Reports    []map[string]interface{} `json:"reports"`
}

func (f *FileStorage) readIndex() (*indexFile, error) {
	path := filepath.Join(f.storageDir, indexFilename)
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return &indexFile{NextNumber: 1, Reports: []map[string]interface{}{}}, nil
	}
	if err != nil {
		return nil, err
	}
	var idx indexFile
	if err := json.Unmarshal(data, &idx); err != nil {
		// Corrupt index — start fresh so a single bad write doesn't
		// brick the whole listing. Same behavior as the Python reference.
		return &indexFile{NextNumber: 1, Reports: []map[string]interface{}{}}, nil
	}
	if idx.Reports == nil {
		idx.Reports = []map[string]interface{}{}
	}
	if idx.NextNumber < 1 {
		idx.NextNumber = len(idx.Reports) + 1
	}
	return &idx, nil
}

func (f *FileStorage) writeIndex(idx *indexFile) error {
	data, err := json.MarshalIndent(idx, "", "  ")
	if err != nil {
		return err
	}
	return atomicWrite(filepath.Join(f.storageDir, indexFilename), data)
}

func (f *FileStorage) nextID(idx *indexFile) string {
	return fmt.Sprintf("bug-%s%03d", f.idPrefix, idx.NextNumber)
}

// SaveReport assigns an id, writes screenshot + JSON atomically, and
// appends an entry to the index. Returns the assigned id.
func (f *FileStorage) SaveReport(metadata map[string]interface{}, screenshot []byte) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	idx, err := f.readIndex()
	if err != nil {
		return "", err
	}
	id := f.nextID(idx)
	now := nowISO()
	report := f.buildReport(id, metadata, now)
	if err := atomicWrite(filepath.Join(f.storageDir, id+".png"), screenshot); err != nil {
		return "", err
	}
	if err := f.writeReportFile(id, report); err != nil {
		return "", err
	}
	idx.Reports = append(idx.Reports, f.buildIndexEntry(report))
	idx.NextNumber++
	if err := f.writeIndex(idx); err != nil {
		return "", err
	}
	return id, nil
}

// buildReport assembles the on-disk shape from the validated wire
// payload — keep in sync with files.py:_build_report so reports are
// readable by either adapter.
func (f *FileStorage) buildReport(id string, metadata map[string]interface{}, now string) map[string]interface{} {
	contextRaw, _ := metadata["context"].(map[string]interface{})
	if contextRaw == nil {
		contextRaw = map[string]interface{}{}
	}
	reporterRaw, _ := metadata["reporter"].(map[string]interface{})
	if reporterRaw == nil {
		reporterRaw = map[string]interface{}{}
	}
	module := stringOr(metadata["module"], stringOr(contextRaw["module"], ""))
	environment := stringOr(metadata["environment"], stringOr(contextRaw["environment"], ""))
	clientUA := stringOr(contextRaw["user_agent"], "")

	report := map[string]interface{}{
		"id":                         id,
		"protocol_version":           stringOr(metadata["protocol_version"], ProtocolVersion),
		"title":                      stringOr(metadata["title"], ""),
		"client_ts":                  stringOr(metadata["client_ts"], ""),
		"report_type":                stringOr(metadata["report_type"], "bug"),
		"description":                stringOr(metadata["description"], ""),
		"expected_behavior":          stringOr(metadata["expected_behavior"], ""),
		"severity":                   stringOr(metadata["severity"], "medium"),
		"status":                     "open",
		"tags":                       metadata["tags"],
		"reporter":                   reporterRaw,
		"context":                    contextRaw,
		"module":                     module,
		"created_at":                 now,
		"updated_at":                 now,
		"has_screenshot":             true,
		"server_user_agent":          stringOr(metadata["server_user_agent"], ""),
		"client_reported_user_agent": clientUA,
		"environment":                environment,
		"github_issue_url":           nil,
		"github_issue_number":        nil,
		"lifecycle": []map[string]interface{}{
			{
				"action":          "created",
				"by":              stringOr(metadata["submitted_by"], "anonymous"),
				"at":              now,
				"fix_commit":      "",
				"fix_description": "",
			},
		},
	}
	if report["tags"] == nil {
		report["tags"] = []string{}
	}
	return report
}

func (f *FileStorage) buildIndexEntry(report map[string]interface{}) map[string]interface{} {
	return map[string]interface{}{
		"id":               report["id"],
		"title":            report["title"],
		"report_type":      report["report_type"],
		"severity":         report["severity"],
		"status":           report["status"],
		"module":           report["module"],
		"created_at":       report["created_at"],
		"has_screenshot":   report["has_screenshot"],
		"github_issue_url": report["github_issue_url"],
	}
}

// reportPath returns the live path, falling back to the archive dir
// if the report was moved by bulk-archive.
func (f *FileStorage) reportPath(id, ext string) (string, bool) {
	live := filepath.Join(f.storageDir, id+ext)
	if _, err := os.Stat(live); err == nil {
		return live, false
	}
	archived := filepath.Join(f.storageDir, archiveSubdir, id+ext)
	if _, err := os.Stat(archived); err == nil {
		return archived, true
	}
	return "", false
}

func (f *FileStorage) readReportFile(id string) (map[string]interface{}, error) {
	path, _ := f.reportPath(id, ".json")
	if path == "" {
		return nil, ErrReportNotFound
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var out map[string]interface{}
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (f *FileStorage) writeReportFile(id string, report map[string]interface{}) error {
	path, _ := f.reportPath(id, ".json")
	if path == "" {
		path = filepath.Join(f.storageDir, id+".json")
	}
	data, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return err
	}
	return atomicWrite(path, data)
}

// GetReport reads one report's full payload. Returns
// (nil, ErrReportNotFound) on a known-shape id with no file. Returns
// (nil, nil) on an id-shape rejection so callers can map that to 404
// without leaking the regex.
func (f *FileStorage) GetReport(id string) (*BugReportDetail, error) {
	if !reportIDRe.MatchString(id) {
		return nil, nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	data, err := f.readReportFile(id)
	if errors.Is(err, ErrReportNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return coerceDetail(data)
}

// ListReports filters by the documented query params (status,
// severity, environment, module, report_type) and returns a page of
// summaries plus the unfiltered total — pagination math lives in the
// route, not the storage.
func (f *FileStorage) ListReports(filters map[string]string, page, pageSize int) ([]BugReportSummary, int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	idx, err := f.readIndex()
	if err != nil {
		return nil, 0, err
	}
	var matched []map[string]interface{}
	for _, e := range idx.Reports {
		if matchesFilters(e, filters) {
			matched = append(matched, e)
		}
	}
	sort.Slice(matched, func(i, j int) bool {
		a, _ := matched[i]["created_at"].(string)
		b, _ := matched[j]["created_at"].(string)
		return a > b
	})
	total := len(matched)
	start := (page - 1) * pageSize
	if start < 0 {
		start = 0
	}
	end := start + pageSize
	if start > total {
		start = total
	}
	if end > total {
		end = total
	}
	items := make([]BugReportSummary, 0, end-start)
	for _, e := range matched[start:end] {
		items = append(items, coerceSummary(e))
	}
	return items, total, nil
}

// GetScreenshotPath returns the on-disk path or "" if missing.
func (f *FileStorage) GetScreenshotPath(id string) (string, error) {
	if !reportIDRe.MatchString(id) {
		return "", nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	path, _ := f.reportPath(id, ".png")
	return path, nil
}

// UpdateStatus mutates the stored report's status, appends a
// lifecycle entry, and persists. Returns (nil, nil) for unknown ids
// so the route can map to 404. The lifecycle write is part of the
// same mutation — both succeed or both don't, eliminating the audit-
// miss footgun.
func (f *FileStorage) UpdateStatus(id, status, fixCommit, fixDescription, by string) (*BugReportDetail, error) {
	if !reportIDRe.MatchString(id) {
		return nil, nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	data, err := f.readReportFile(id)
	if errors.Is(err, ErrReportNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	now := nowISO()
	data["status"] = status
	data["updated_at"] = now
	event := map[string]interface{}{
		"action":          "status_changed",
		"by":              by,
		"at":              now,
		"status":          status,
		"fix_commit":      fixCommit,
		"fix_description": fixDescription,
	}
	lifecycle, _ := data["lifecycle"].([]interface{})
	lifecycle = append(lifecycle, event)
	data["lifecycle"] = lifecycle
	if err := f.writeReportFile(id, data); err != nil {
		return nil, err
	}
	if err := f.updateIndexEntry(id, map[string]interface{}{"status": status}); err != nil {
		return nil, err
	}
	return coerceDetail(data)
}

func (f *FileStorage) updateIndexEntry(id string, fields map[string]interface{}) error {
	idx, err := f.readIndex()
	if err != nil {
		return err
	}
	for _, e := range idx.Reports {
		if e["id"] == id {
			for k, v := range fields {
				e[k] = v
			}
			break
		}
	}
	return f.writeIndex(idx)
}

// DeleteReport is a hard-delete — JSON, PNG, and index entry all go.
// Bulk-archive uses a separate path for soft-delete semantics.
func (f *FileStorage) DeleteReport(id string) (bool, error) {
	if !reportIDRe.MatchString(id) {
		return false, nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	candidates := []string{
		filepath.Join(f.storageDir, id+".json"),
		filepath.Join(f.storageDir, id+".png"),
		filepath.Join(f.storageDir, archiveSubdir, id+".json"),
		filepath.Join(f.storageDir, archiveSubdir, id+".png"),
	}
	removed := false
	for _, p := range candidates {
		if err := os.Remove(p); err == nil {
			removed = true
		} else if !errors.Is(err, os.ErrNotExist) {
			return false, err
		}
	}
	if removed {
		idx, err := f.readIndex()
		if err != nil {
			return false, err
		}
		out := idx.Reports[:0]
		for _, e := range idx.Reports {
			if e["id"] != id {
				out = append(out, e)
			}
		}
		idx.Reports = out
		if err := f.writeIndex(idx); err != nil {
			return false, err
		}
	}
	return removed, nil
}

// BulkCloseFixed transitions every fixed report to closed. Returns
// the count of transitioned reports (no-ops aren't counted) so the
// caller can shape the JSON envelope cleanly.
func (f *FileStorage) BulkCloseFixed(by string) (int, error) {
	f.mu.Lock()
	idx, err := f.readIndex()
	if err != nil {
		f.mu.Unlock()
		return 0, err
	}
	var ids []string
	for _, e := range idx.Reports {
		if s, _ := e["status"].(string); s == "fixed" {
			if id, _ := e["id"].(string); id != "" {
				ids = append(ids, id)
			}
		}
	}
	f.mu.Unlock()
	count := 0
	for _, id := range ids {
		updated, err := f.UpdateStatus(id, "closed", "", "", by)
		if err != nil {
			return count, err
		}
		if updated != nil {
			count++
		}
	}
	return count, nil
}

// BulkArchiveClosed moves every closed report's files into the
// archive subdir and drops them from the index. Archived reports are
// excluded from GET /reports by default (filter with
// include_archived=true to retrieve them).
func (f *FileStorage) BulkArchiveClosed() (int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	idx, err := f.readIndex()
	if err != nil {
		return 0, err
	}
	var ids []string
	for _, e := range idx.Reports {
		if s, _ := e["status"].(string); s == "closed" {
			if id, _ := e["id"].(string); id != "" {
				ids = append(ids, id)
			}
		}
	}
	archived := 0
	for _, id := range ids {
		if f.archiveOne(id) {
			archived++
		}
	}
	out := idx.Reports[:0]
	dropped := map[string]struct{}{}
	for _, id := range ids {
		dropped[id] = struct{}{}
	}
	for _, e := range idx.Reports {
		if id, _ := e["id"].(string); id != "" {
			if _, drop := dropped[id]; !drop {
				out = append(out, e)
			}
		}
	}
	idx.Reports = out
	if err := f.writeIndex(idx); err != nil {
		return archived, err
	}
	return archived, nil
}

// archiveOne moves one report's files into the archive subdir.
// Errors are swallowed (and recorded as "didn't archive") because
// bulk-archive must keep going on individual failures.
func (f *FileStorage) archiveOne(id string) bool {
	moved := false
	for _, ext := range []string{".json", ".png"} {
		src := filepath.Join(f.storageDir, id+ext)
		dst := filepath.Join(f.storageDir, archiveSubdir, id+ext)
		if _, err := os.Stat(src); err == nil {
			if err := os.Rename(src, dst); err == nil {
				moved = true
			}
		}
	}
	return moved
}

// matchesFilters returns true if every non-empty filter value matches
// the corresponding index field.
func matchesFilters(entry map[string]interface{}, filters map[string]string) bool {
	for _, key := range []string{"status", "severity", "module", "report_type", "environment"} {
		want := filters[key]
		if want == "" {
			continue
		}
		got, _ := entry[key].(string)
		if got != want {
			return false
		}
	}
	return true
}

// stringOr unwraps an interface{} into a string, falling back to a
// caller-supplied default when the value is nil, missing, or the
// wrong type. Used heavily in buildReport since the wire payload is
// just a generic JSON map.
func stringOr(v interface{}, fallback string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return fallback
}

// coerceSummary maps a raw index entry into the BugReportSummary
// shape. Tolerates missing fields so a corrupted index entry doesn't
// crash the listing.
func coerceSummary(entry map[string]interface{}) BugReportSummary {
	s := BugReportSummary{
		ID:            stringOr(entry["id"], ""),
		Title:         stringOr(entry["title"], ""),
		ReportType:    stringOr(entry["report_type"], "bug"),
		Severity:      stringOr(entry["severity"], "medium"),
		Status:        stringOr(entry["status"], "open"),
		Module:        stringOr(entry["module"], ""),
		CreatedAt:     stringOr(entry["created_at"], ""),
		HasScreenshot: boolOr(entry["has_screenshot"], true),
	}
	if url, ok := entry["github_issue_url"].(string); ok && url != "" {
		s.GitHubIssueURL = &url
	}
	return s
}

// coerceDetail maps a raw report dict (read from disk) into the
// BugReportDetail shape. Re-uses the summary mapper for the embedded
// fields. Returns the canonical struct so the route layer can marshal
// straight to JSON without re-shaping.
func coerceDetail(data map[string]interface{}) (*BugReportDetail, error) {
	d := &BugReportDetail{
		BugReportSummary:        coerceSummary(data),
		Description:             stringOr(data["description"], ""),
		ExpectedBehavior:        stringOr(data["expected_behavior"], ""),
		Tags:                    sliceOfStrings(data["tags"]),
		ServerUserAgent:         stringOr(data["server_user_agent"], ""),
		ClientReportedUserAgent: stringOr(data["client_reported_user_agent"], ""),
		Environment:             stringOr(data["environment"], ""),
		ClientTS:                stringOr(data["client_ts"], ""),
		ProtocolVersion:         stringOr(data["protocol_version"], ProtocolVersion),
		UpdatedAt:               stringOr(data["updated_at"], ""),
	}
	if rep, ok := data["reporter"].(map[string]interface{}); ok {
		d.Reporter = Reporter{
			Name:   stringOr(rep["name"], ""),
			Email:  stringOr(rep["email"], ""),
			UserID: stringOr(rep["user_id"], ""),
		}
	}
	if ctx, ok := data["context"].(map[string]interface{}); ok {
		raw, _ := json.Marshal(ctx)
		_ = json.Unmarshal(raw, &d.Context)
	}
	if lc, ok := data["lifecycle"].([]interface{}); ok {
		for _, item := range lc {
			if m, ok := item.(map[string]interface{}); ok {
				d.Lifecycle = append(d.Lifecycle, LifecycleEvent{
					Action:         stringOr(m["action"], ""),
					By:             stringOr(m["by"], ""),
					At:             stringOr(m["at"], ""),
					Status:         stringOr(m["status"], ""),
					FixCommit:      stringOr(m["fix_commit"], ""),
					FixDescription: stringOr(m["fix_description"], ""),
				})
			}
		}
	}
	if n, ok := data["github_issue_number"].(float64); ok {
		v := int(n)
		d.GitHubIssueNumber = &v
	}
	return d, nil
}

func boolOr(v interface{}, fallback bool) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	return fallback
}

func sliceOfStrings(v interface{}) []string {
	if arr, ok := v.([]interface{}); ok {
		out := make([]string, 0, len(arr))
		for _, item := range arr {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return []string{}
}
