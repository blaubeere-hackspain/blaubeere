use blaubeere_api::{AppState, Config, auth, router};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if !args.is_empty() {
        anyhow::ensure!(
            args.len() == 4 && args[0] == "import-parquet",
            "Usage: blaubeere-api import-parquet ROOT DATABASE_DIRECTORY REVISION"
        );
        let path = blaubeere_api::dataset_import::build(
            std::path::Path::new(&args[1]),
            std::path::Path::new(&args[2]),
            &args[3],
        )
        .await?;
        println!("{}", path.display());
        return Ok(());
    }
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    std::fs::create_dir_all(".local")?;
    let database =
        std::env::var("DATABASE_URL").unwrap_or_else(|_| "sqlite://.local/blaubeere.db".into());
    let mut state = AppState::new(&database, Config::from_env()?).await?;
    if let Ok(url) = std::env::var("DATASET_DATABASE_URL") {
        state.dataset = Some(blaubeere_api::dataset::connect(&url).await?);
    }
    if let Ok(path) = std::env::var("ASSESSMENT_FILE") {
        state.companies = std::sync::Arc::new(blaubeere_api::finance::load(
            &std::fs::read_to_string(path)?,
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
    if let Ok(email) = std::env::var("DATASET_TEAM_EMAIL") {
        blaubeere_api::dataset::grant_team_access(&state, &email).await?;
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
