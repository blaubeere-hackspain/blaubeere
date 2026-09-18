use axum::{Router, routing::get};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    let address = std::env::var("API_BIND").unwrap_or_else(|_| "127.0.0.1:8080".into());
    let listener = tokio::net::TcpListener::bind(address).await?;
    axum::serve(listener, Router::new().route("/health", get(|| async { "ok" }))).await?;
    Ok(())
}
