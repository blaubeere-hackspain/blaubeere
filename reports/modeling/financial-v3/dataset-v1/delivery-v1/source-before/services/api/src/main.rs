use blaubeere_api::{AppState, Config, auth, router};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    std::fs::create_dir_all(".local")?;
    let database =
        std::env::var("DATABASE_URL").unwrap_or_else(|_| "sqlite://.local/blaubeere.db".into());
    let mut state = AppState::new(&database, Config::from_env()?).await?;
    if let Ok(path) = std::env::var("ASSESSMENT_FILE") {
        state.companies = std::sync::Arc::new(blaubeere_api::finance::load_file(
            std::path::Path::new(&path),
        )?);
    }
    if let (Ok(email), Ok(password)) = (
        std::env::var("BOOTSTRAP_EMAIL"),
        std::env::var("BOOTSTRAP_PASSWORD"),
    ) {
        let companies = std::env::var("BOOTSTRAP_COMPANIES").unwrap_or_else(|_| "DEMO_001".into());
        auth::provision(
            &state,
            &email,
            &password,
            &companies.split(',').collect::<Vec<_>>(),
        )
        .await?;
    }
    let address = std::env::var("API_BIND").unwrap_or_else(|_| "127.0.0.1:8080".into());
    let listener = tokio::net::TcpListener::bind(address).await?;
    println!("Blaubeere API listening on {}", listener.local_addr()?);
    axum::serve(listener, router(state))
        .with_graceful_shutdown(async {
            tokio::signal::ctrl_c().await.ok();
        })
        .await?;
    Ok(())
}
