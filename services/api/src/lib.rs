pub mod auth;
pub mod finance;
pub mod oauth;

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Request, State},
    http::{HeaderValue, Method, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::json;
use sqlx::{
    SqlitePool,
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
};
use std::{str::FromStr, sync::Arc, time::Duration};
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct Config {
    pub app_origin: String,
    pub api_origin: String,
    pub mcp_resource: String,
}

impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        let config = Self {
            app_origin: std::env::var("APP_ORIGIN")
                .unwrap_or_else(|_| "http://localhost:3100".into()),
            api_origin: std::env::var("API_ORIGIN")
                .unwrap_or_else(|_| "http://localhost:8080".into()),
            mcp_resource: std::env::var("MCP_RESOURCE")
                .unwrap_or_else(|_| "http://localhost:8081/mcp".into()),
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> anyhow::Result<()> {
        for value in [&self.app_origin, &self.api_origin, &self.mcp_resource] {
            let url = url::Url::parse(value)?;
            anyhow::ensure!(
                url.username().is_empty()
                    && url.password().is_none()
                    && url.query().is_none()
                    && url.fragment().is_none(),
                "Origins must not include credentials, queries, or fragments"
            );
            anyhow::ensure!(
                url.scheme() == "https"
                    || (url.scheme() == "http"
                        && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))),
                "Use HTTPS outside localhost"
            );
        }
        anyhow::ensure!(
            [&self.app_origin, &self.api_origin]
                .iter()
                .all(|value| url::Url::parse(value)
                    .is_ok_and(|url| url.origin().ascii_serialization() == **value)),
            "APP_ORIGIN and API_ORIGIN must be canonical origins without a path or trailing slash"
        );
        anyhow::ensure!(
            url::Url::parse(&self.mcp_resource)?.path() == "/mcp",
            "MCP_RESOURCE must end in /mcp"
        );
        Ok(())
    }
}

#[derive(Clone)]
pub struct AppState {
    pub db: SqlitePool,
    pub config: Config,
    pub dummy_hash: Arc<String>,
    pub companies: Arc<Vec<finance::Company>>,
}

impl AppState {
    pub async fn new(database_url: &str, config: Config) -> anyhow::Result<Self> {
        // ponytail: SQLite serves one deployment; move identity storage to Postgres for multiple replicas.
        let options = SqliteConnectOptions::from_str(database_url)?
            .create_if_missing(true)
            .foreign_keys(true)
            .busy_timeout(Duration::from_secs(5));
        let db = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        sqlx::migrate!().run(&db).await?;
        let dummy_hash = Arc::new(auth::hash_password(auth::secret()).await?);
        Ok(Self {
            db,
            config,
            dummy_hash,
            companies: Arc::new(finance::load(include_str!(
                "../../../fixtures/companies.json"
            ))?),
        })
    }
}

#[derive(Debug)]
pub struct ApiError(pub StatusCode, pub String);
pub type ApiResult<T> = Result<T, ApiError>;

impl ApiError {
    pub fn bad(message: impl Into<String>) -> Self {
        Self(StatusCode::BAD_REQUEST, message.into())
    }
    pub fn unauthorized() -> Self {
        Self(StatusCode::UNAUTHORIZED, "Please sign in again.".into())
    }
    pub fn forbidden() -> Self {
        Self(
            StatusCode::FORBIDDEN,
            "You do not have access to this company.".into(),
        )
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error": self.1}))).into_response()
    }
}
impl From<sqlx::Error> for ApiError {
    fn from(error: sqlx::Error) -> Self {
        tracing::error!(%error, "database operation failed");
        Self(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not complete the request. Please retry.".into(),
        )
    }
}

pub fn router(state: AppState) -> Router {
    let cors = CorsLayer::new()
        .allow_origin(
            state
                .config
                .app_origin
                .parse::<HeaderValue>()
                .expect("validated origin"),
        )
        .allow_credentials(true)
        .allow_methods([Method::GET, Method::POST])
        .allow_headers([header::CONTENT_TYPE, header::AUTHORIZATION]);
    Router::new()
        .route("/health", get(|| async { Json(json!({"status": "ok"})) }))
        .route("/api/auth/login", post(auth::login))
        .route("/api/auth/logout", post(auth::logout))
        .route("/api/me", get(auth::me))
        .route(
            "/.well-known/oauth-authorization-server",
            get(oauth::metadata),
        )
        .route("/oauth/register", post(oauth::register))
        .route("/oauth/token", post(oauth::token))
        .route("/oauth/revoke", post(oauth::revoke))
        .route("/api/oauth/request", get(oauth::consent_request))
        .route("/api/oauth/consent", post(oauth::consent))
        .route("/api/connections", get(oauth::connections))
        .route("/api/connections/{id}/revoke", post(oauth::disconnect))
        .route("/api/companies", get(finance::companies))
        .route("/api/companies/{id}/assessment", get(finance::assessment))
        .route("/api/companies/{id}/plans", post(finance::plans))
        .layer(DefaultBodyLimit::max(32 * 1024))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            protect_browser,
        ))
        .layer(cors)
        .with_state(state)
}

async fn protect_browser(State(state): State<AppState>, request: Request, next: Next) -> Response {
    if request.method() == Method::POST && request.uri().path().starts_with("/api/") {
        let bearer = request.headers().contains_key(header::AUTHORIZATION);
        let origin = request
            .headers()
            .get(header::ORIGIN)
            .and_then(|v| v.to_str().ok());
        // Browser mutations require an exact origin; OAuth bearer requests never use cookies.
        if !bearer && origin != Some(state.config.app_origin.as_str()) {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"error":"This request must come from the app."})),
            )
                .into_response();
        }
    }
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    response
}

#[cfg(test)]
mod tests;
