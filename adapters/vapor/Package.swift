// swift-tools-version:6.0
//
// Bug-Fab Vapor adapter — DRAFT.
//
// Wire-protocol target: 0.1 (see ../../../repo/docs/PROTOCOL.md).
//
// This package is a single-product library (`BugFab`) plus an example
// executable (`BugFabExample`) demonstrating end-to-end wiring with
// `BugFabFileStorage`. The Fluent storage is built behind the same
// `BugFabStorage` protocol but is exercised via SQLite in the tests
// to keep CI dependency-free.
import PackageDescription

let package = Package(
    name: "bug-fab-vapor",
    platforms: [
        // Linux is the primary deployment target; macOS 13 is the minimum
        // for the swift-nio / async-http-client stack Vapor 4 ships with.
        .macOS(.v13)
    ],
    products: [
        .library(name: "BugFab", targets: ["BugFab"]),
        .executable(name: "BugFabExample", targets: ["BugFabExample"]),
    ],
    dependencies: [
        // Vapor 4.x — async/await ergonomics, NIO under the hood.
        .package(url: "https://github.com/vapor/vapor.git", from: "4.92.0"),
        // Fluent ORM for the SQL storage backend.
        .package(url: "https://github.com/vapor/fluent.git", from: "4.9.0"),
        // Postgres driver — preferred for production deployments.
        .package(url: "https://github.com/vapor/fluent-postgres-driver.git", from: "2.8.0"),
        // SQLite driver — primarily here so the test suite can exercise
        // BugFabFluentStorage without spinning a Postgres container.
        .package(url: "https://github.com/vapor/fluent-sqlite-driver.git", from: "4.6.0"),
    ],
    targets: [
        .target(
            name: "BugFab",
            dependencies: [
                .product(name: "Vapor", package: "vapor"),
                .product(name: "Fluent", package: "fluent"),
                .product(name: "FluentPostgresDriver", package: "fluent-postgres-driver"),
                .product(name: "FluentSQLiteDriver", package: "fluent-sqlite-driver"),
            ],
            path: "Sources/BugFab"
        ),
        .executableTarget(
            name: "BugFabExample",
            dependencies: [
                "BugFab",
                .product(name: "Vapor", package: "vapor"),
            ],
            path: "Sources/BugFabExample"
        ),
        .testTarget(
            name: "BugFabTests",
            dependencies: [
                "BugFab",
                .product(name: "XCTVapor", package: "vapor"),
                .product(name: "FluentSQLiteDriver", package: "fluent-sqlite-driver"),
            ],
            path: "Tests/BugFabTests"
        ),
    ]
)
