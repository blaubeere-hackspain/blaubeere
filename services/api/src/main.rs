use blaubeere_api::{AppState, Config, auth, router};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|arg| arg == "import-parquet") {
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
    if !args.is_empty() {
        let (ids, replace): (Vec<&str>, Option<&str>) = match args[0].as_str() {
            "grant-company-access" if args.len() >= 3 => {
                (args[2..].iter().map(String::as_str).collect(), None)
            }
            "replace-company-access" if args.len() == 4 => (vec![&args[3]], Some(&args[2])),
            _ => anyhow::bail!(
                "Usage: grant-company-access EMAIL COMPANY_ID [...] or replace-company-access EMAIL OLD_ID NEW_ID"
            ),
        };
        blaubeere_api::dataset::grant_company_access(&state, &args[1], &ids, replace).await?;
        let user: String = sqlx::query_scalar("SELECT id FROM users WHERE email=?")
            .bind(args[1].trim().to_lowercase())
            .fetch_one(&state.db)
            .await?;
        for id in &ids {
            auth::company_access(&state, &user, id)
                .await
                .map_err(|error| anyhow::anyhow!(error.1))?;
            let summary = blaubeere_api::company_health::for_company(&state, id, None)
                .await
                .map_err(|error| anyhow::anyhow!(error.1))?;
            println!(
                "{}: {} at {}",
                id, summary["health"]["state"], summary["as_of"]
            );
        }
        println!(
            "Assigned companies for {}: {}",
            args[1],
            auth::identity(&state, &user)
                .await
                .map_err(|error| anyhow::anyhow!(error.1))?
                .company_ids
                .join(", ")
        );
        return Ok(());
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
