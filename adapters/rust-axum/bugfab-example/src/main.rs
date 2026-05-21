//! Minimal example consumer for the Bug-Fab Axum adapter.
//!
//! Mounts the combined intake+viewer router on `127.0.0.1:8080` with a
//! `./bug-fab-data/` file backend. Production setups should mount intake
//! and viewer separately behind different auth middleware — see the
//! workspace README.

use std::net::SocketAddr;
use std::sync::Arc;

use bugfab::storage::file::FileStorage;
use bugfab::{build_app, AppState, Settings};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber_init();
    let storage = Arc::new(FileStorage::new("./bug-fab-data", "")?);
    let state = Arc::new(AppState::new(storage, Settings::default()));
    let app = build_app(state);

    let addr: SocketAddr = "127.0.0.1:8080".parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!(%addr, "bugfab-example listening");
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;
    Ok(())
}

fn tracing_subscriber_init() {
    // Single tracing::info!() in main is enough for the demo. Real
    // consumers wire tracing-subscriber with their own formatting +
    // filter config. Avoid pulling tracing-subscriber into this example
    // crate to keep the binary lean.
}
