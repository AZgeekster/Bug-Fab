import Vapor

// `routes(_:)` wires every Bug-Fab endpoint onto whatever
// `RoutesBuilder` the consumer hands in. This is the function consumers
// call from their own `routes.swift` after `app.bugFab(...)`:
//
//     try app.bugFab(storage: storage, settings: settings)
//     try BugFab.routes(app.grouped("api"))
//
// Splitting intake vs viewer into two groups is encouraged so consumers
// can apply different auth middleware to each (see PROTOCOL.md §
// "Auth — mount-point delegation").

public enum BugFab {
    public static func routes(_ routes: RoutesBuilder) throws {
        try routes.register(collection: BugReportsController())
    }

    public static func intakeRoutes(_ routes: RoutesBuilder) throws {
        let collection = BugReportsController()
        routes.on(.POST, "bug-reports", body: .collect(maxSize: "12mb"), use: collection.submit)
    }

    public static func viewerRoutes(_ routes: RoutesBuilder) throws {
        let c = BugReportsController()
        routes.get("reports", use: c.list)
        routes.get("reports", ":id", use: c.detail)
        routes.get("reports", ":id", "screenshot", use: c.screenshot)
        routes.put("reports", ":id", "status", use: c.updateStatus)
        routes.delete("reports", ":id", use: c.delete)
        routes.post("bulk-close-fixed", use: c.bulkCloseFixed)
        routes.post("bulk-archive-closed", use: c.bulkArchiveClosed)
    }
}
