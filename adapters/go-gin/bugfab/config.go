package bugfab

import (
	"os"
	"strconv"
)

// Config captures the runtime knobs Bug-Fab consumers may tune. Build
// one with NewConfigFromEnv() to honor the BUG_FAB_* environment
// variables documented in the README, or construct directly when
// wiring from your own config layer.
type Config struct {
	// StorageDir is where FileStorage writes the per-report files and
	// index.json. Required when using the default storage.
	StorageDir string

	// IDPrefix is the optional one-letter environment tag baked into
	// every assigned id (e.g., "P" → bug-P001). Useful for multi-
	// environment shared collectors so prod / dev ids don't collide.
	IDPrefix string

	// MaxScreenshotBytes caps the screenshot upload. Default 4 MiB —
	// PROTOCOL.md allows up to 10 MiB; we ship a tighter cap by
	// default and let consumers raise it explicitly if they handle
	// high-DPI captures.
	MaxScreenshotBytes int64

	// RateLimitEnabled toggles the per-IP limiter. Off by default
	// because most Bug-Fab consumers are behind auth and an internal
	// abuse vector is unlikely; the toggle exists so public POCs can
	// turn it on with one env var.
	RateLimitEnabled bool
	RateLimitMax     int
	RateLimitWindow  int // seconds

	// Viewer permissions gate the destructive viewer actions. Each
	// defaults to true (all actions allowed); set one to false to make
	// the matching route return 403 forbidden regardless of the caller.
	// Mirrors the Python reference's viewer_permissions and the Laravel
	// BUG_FAB_VIEWER_CAN_* env vars.
	CanEditStatus bool // PUT /reports/{id}/status
	CanDelete     bool // DELETE /reports/{id}
	CanBulk       bool // POST /bulk-close-fixed, /bulk-archive-closed
}

// DefaultConfig returns the documented v0.1 defaults — safe for
// development, conservative on caps.
func DefaultConfig() Config {
	return Config{
		StorageDir:         "./var/bug-fab",
		IDPrefix:           "",
		MaxScreenshotBytes: 4 * 1024 * 1024,
		RateLimitEnabled:   false,
		RateLimitMax:       30,
		RateLimitWindow:    60,
		CanEditStatus:      true,
		CanDelete:          true,
		CanBulk:            true,
	}
}

// NewConfigFromEnv reads the same BUG_FAB_* env vars the Python
// reference honors, falling back to DefaultConfig values for anything
// not set. Boolean vars accept "1", "true", "yes" (case-insensitive);
// anything else is false.
func NewConfigFromEnv() Config {
	c := DefaultConfig()
	if v := os.Getenv("BUG_FAB_STORAGE_DIR"); v != "" {
		c.StorageDir = v
	}
	if v := os.Getenv("BUG_FAB_ID_PREFIX"); v != "" {
		c.IDPrefix = v
	}
	if v := os.Getenv("BUG_FAB_MAX_UPLOAD_MB"); v != "" {
		if mb, err := strconv.Atoi(v); err == nil && mb > 0 {
			c.MaxScreenshotBytes = int64(mb) * 1024 * 1024
		}
	}
	c.RateLimitEnabled = envBool("BUG_FAB_RATE_LIMIT_ENABLED")
	if v := os.Getenv("BUG_FAB_RATE_LIMIT_MAX"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			c.RateLimitMax = n
		}
	}
	if v := os.Getenv("BUG_FAB_RATE_LIMIT_WINDOW_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			c.RateLimitWindow = n
		}
	}
	c.CanEditStatus = envBoolDefault("BUG_FAB_VIEWER_CAN_EDIT_STATUS", true)
	c.CanDelete = envBoolDefault("BUG_FAB_VIEWER_CAN_DELETE", true)
	c.CanBulk = envBoolDefault("BUG_FAB_VIEWER_CAN_BULK", true)
	return c
}

func envBool(key string) bool {
	v := os.Getenv(key)
	switch v {
	case "1", "true", "TRUE", "True", "yes", "YES":
		return true
	}
	return false
}

// envBoolDefault reads a boolean env var that defaults to def when unset
// or empty. Used for the viewer permissions, which are allowed unless a
// consumer explicitly turns one off — the opposite polarity from the
// opt-in flags envBool serves.
func envBoolDefault(key string, def bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	switch v {
	case "1", "true", "TRUE", "True", "yes", "YES":
		return true
	case "0", "false", "FALSE", "False", "no", "NO":
		return false
	}
	return def
}
