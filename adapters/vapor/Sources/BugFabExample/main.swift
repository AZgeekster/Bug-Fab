import BugFab
import Foundation
import Vapor

// Tiny example: file-backed storage, intake on /api/, viewer on /admin/.
// Run with `swift run BugFabExample` and POST to localhost:8080/api/bug-reports.

@main
struct BugFabExampleEntry {
    static func main() async throws {
        var env = try Environment.detect()
        try LoggingSystem.bootstrap(from: &env)
        let app = try await Application.make(env)
        defer {
            Task {
                try? await app.asyncShutdown()
            }
        }
        try configure(app)
        try await app.execute()
    }
}

func configure(_ app: Application) throws {
    let dir = URL(fileURLWithPath: "./bug-fab-data", isDirectory: true)
    let storage = try BugFabFileStorage(storageDirectory: dir)
    try app.bugFab(storage: storage)

    let intake = app.grouped("api")
    try BugFab.intakeRoutes(intake)

    let viewer = app.grouped("admin")
    try BugFab.viewerRoutes(viewer)

    app.get("healthz") { _ in "ok" }
}
