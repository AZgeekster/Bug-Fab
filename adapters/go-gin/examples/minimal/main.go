// Minimal Bug-Fab consumer using Gin.
//
// Run: go run ./examples/minimal
// Submit: curl -F 'metadata={...};type=application/json' \
//              -F 'screenshot=@./shot.png;type=image/png' \
//              http://localhost:8080/api/bug-fab/bug-reports
//
// Lift the AddBugFab block into any existing Gin app to drop in the
// eight protocol endpoints under a chosen prefix.
package main

import (
	"log"

	"github.com/AZgeekster/Bug-Fab/adapters/go-gin/bugfab"

	"github.com/gin-gonic/gin"
)

func main() {
	cfg := bugfab.NewConfigFromEnv()
	if cfg.StorageDir == "" {
		cfg.StorageDir = "./var/bug-fab"
	}
	adapter, err := bugfab.New(cfg)
	if err != nil {
		log.Fatalf("bug-fab: configure failed: %v", err)
	}

	r := gin.Default()
	// In a real consumer you'd attach auth middleware to this group
	// — Bug-Fab v0.1 delegates all auth to the mount point.
	adapter.Register(r.Group("/api/bug-fab"))

	log.Println("bug-fab listening on :8080 — POST /api/bug-fab/bug-reports")
	if err := r.Run(":8080"); err != nil {
		log.Fatal(err)
	}
}
