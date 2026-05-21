package bugfab

import (
	"sync"
	"time"
)

// RateLimiter is a per-IP fixed-window limiter. Matches the Python
// reference's _rate_limit.RateLimiter contract: max-events per window-
// seconds, with the window resetting independently for each key.
//
// Fixed-window is the right choice for v0.1 over token-bucket because
// the limiter is meant to deflect submission-spam from one misbehaving
// browser, not to enforce a strict per-second cap. Fixed-window keeps
// the code one map per process and the eviction policy obvious.
type RateLimiter struct {
	maxPerWindow  int
	windowSeconds int
	mu            sync.Mutex
	hits          map[string]*window
}

type window struct {
	start time.Time
	count int
}

// NewRateLimiter returns a limiter that allows maxPerWindow events
// per windowSeconds per key. Pass 0 for maxPerWindow to effectively
// disable (every Check returns true).
func NewRateLimiter(maxPerWindow, windowSeconds int) *RateLimiter {
	return &RateLimiter{
		maxPerWindow:  maxPerWindow,
		windowSeconds: windowSeconds,
		hits:          map[string]*window{},
	}
}

// Check records a hit for key and returns true if the request should
// proceed, false if the limit was exceeded. The window resets the
// next time any request lands more than windowSeconds after the
// window's start.
func (r *RateLimiter) Check(key string) bool {
	if r.maxPerWindow <= 0 {
		return true
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	w, ok := r.hits[key]
	if !ok || now.Sub(w.start) > time.Duration(r.windowSeconds)*time.Second {
		r.hits[key] = &window{start: now, count: 1}
		return true
	}
	w.count++
	return w.count <= r.maxPerWindow
}

// WindowSeconds exposes the configured window for the Retry-After
// hint in the 429 response body.
func (r *RateLimiter) WindowSeconds() int { return r.windowSeconds }
