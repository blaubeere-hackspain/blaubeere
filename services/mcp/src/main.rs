use axum::{
    Json, Router,
    extract::{Request, State},
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use blaubeere_api::{AppState, Config, auth, finance, oauth};
use rmcp::{
    ErrorData, RoleServer, ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, ContentBlock, ServerCapabilities, ServerInfo},
    service::RequestContext,
    tool, tool_handler, tool_router,
    transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    },
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

#[derive(Clone)]
struct Principal(String);
#[derive(Clone)]
struct FinanceTools {
    state: AppState,
    tool_router: ToolRouter<Self>,
}
#[derive(Deserialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
struct AssessmentInput {
    company_id: String,
    /// Forecast horizon between 7 and 180 days; defaults to 90.
    days: Option<i64>,
    /// Cash floor in integer minor units (cents for EUR).
    buffer_cents: Option<i64>,
}
#[derive(Deserialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
struct PlanInput {
    company_id: String,
    goal: finance::Goal,
}

fn principal(ctx: &RequestContext<RoleServer>) -> Result<&str, ErrorData> {
    ctx.extensions
        .get::<axum::http::request::Parts>()
        .and_then(|p| p.extensions.get::<Principal>())
        .map(|p| p.0.as_str())
        .ok_or_else(|| ErrorData::invalid_request("Authentication context is missing.", None))
}
fn result(value: Value) -> CallToolResult {
    CallToolResult::success(vec![ContentBlock::text(value.to_string())])
}
fn failure(error: blaubeere_api::ApiError) -> ErrorData {
    ErrorData::invalid_request(error.1, None)
}

#[tool_router]
impl FinanceTools {
    #[tool(
        description = "List the companies this signed-in finance user may access. Read-only.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn list_companies(
        &self,
        ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let identity = auth::identity(&self.state, principal(&ctx)?)
            .await
            .map_err(failure)?;
        Ok(result(
            finance::company_summaries(&self.state, &identity.company_ids)
                .await
                .map_err(failure)?,
        ))
    }
    #[tool(
        description = "Read a dated cash outlook or imported monthly model assessments, evidence and missing inputs for an authorised company. Imported model inputs are EUR amounts; forecasts use integer cents. No forecast is inferred from relative cash movements. Demo fixtures and reconstructed history are labelled.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn get_cash_outlook(
        &self,
        Parameters(input): Parameters<AssessmentInput>,
        ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        auth::company_access(&self.state, principal(&ctx)?, &input.company_id)
            .await
            .map_err(failure)?;
        Ok(result(
            finance::assessment_for(
                &self.state,
                &input.company_id,
                input.days,
                input.buffer_cents,
            )
            .await
            .map_err(failure)?,
        ))
    }
    #[tool(
        description = "Compare a baseline with two bounded, conditional financial plans. Does not save changes or modify source records. Supported metrics: min_cash, ending_cash, monthly_revenue (requires explicit business inputs). Targets and amounts are integer cents; deadlines are ISO dates 7–180 days after assessment.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn compare_plans(
        &self,
        Parameters(input): Parameters<PlanInput>,
        ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        auth::company_access(&self.state, principal(&ctx)?, &input.company_id)
            .await
            .map_err(failure)?;
        Ok(result(
            finance::compare_for(&self.state, &input.company_id, &input.goal)
                .await
                .map_err(failure)?,
        ))
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for FinanceTools {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build()).with_instructions("Blaubeere supports internal finance planning. Preserve source labels, dates, missing coverage and explicit assumptions in every answer. Scenarios are conditional, not guarantees. Never infer retention or profit from bank data. Each company is authorised independently.")
    }
}

async fn authorize(State(state): State<AppState>, mut request: Request, next: Next) -> Response {
    match oauth::access_user(&state, request.headers()).await {
        Ok(user) => {
            request.extensions_mut().insert(Principal(user));
            next.run(request).await
        }
        Err(_) => {
            let base = state.config.mcp_resource.trim_end_matches("/mcp");
            let mut response = (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error":"invalid_token"})),
            )
                .into_response();
            if let Ok(header) = HeaderValue::from_str(&format!(
                "Bearer resource_metadata=\"{base}/.well-known/oauth-protected-resource/mcp\", scope=\"{}\"",
                oauth::SCOPE
            )) {
                response
                    .headers_mut()
                    .insert(header::WWW_AUTHENTICATE, header);
            }
            response
        }
    }
}
async fn metadata(State(state): State<AppState>) -> Json<Value> {
    Json(
        json!({"resource":state.config.mcp_resource,"authorization_servers":[state.config.api_origin],"scopes_supported":[oauth::SCOPE],"bearer_methods_supported":["header"],"resource_name":"Blaubeere finance tools"}),
    )
}
fn router(state: AppState) -> Router {
    let tools_state = state.clone();
    let mut config = StreamableHttpServerConfig::default()
        .with_legacy_session_mode(false)
        .with_json_response(true)
        .with_allowed_origins([state.config.app_origin.clone()])
        .with_allowed_hosts([
            "localhost".to_owned(),
            "127.0.0.1".to_owned(),
            state
                .config
                .mcp_resource
                .split("//")
                .nth(1)
                .unwrap_or_default()
                .split('/')
                .next()
                .unwrap_or_default()
                .to_owned(),
        ]);
    config.max_request_body_bytes = 32 * 1024;
    let service = StreamableHttpService::new(
        move || {
            Ok(FinanceTools {
                state: tools_state.clone(),
                tool_router: FinanceTools::tool_router(),
            })
        },
        Arc::new(LocalSessionManager::default()),
        config,
    );
    let mcp = Router::new()
        .nest_service("/mcp", service)
        .layer(middleware::from_fn_with_state(state.clone(), authorize));
    Router::new()
        .merge(mcp)
        .route("/health", get(|| async { Json(json!({"status":"ok"})) }))
        .route("/.well-known/oauth-protected-resource", get(metadata))
        .route("/.well-known/oauth-protected-resource/mcp", get(metadata))
        .with_state(state)
}

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
    if let Ok(url) = std::env::var("DATASET_DATABASE_URL") {
        state.dataset = Some(blaubeere_api::dataset::connect(&url).await?);
    }
    if let Ok(path) = std::env::var("ASSESSMENT_FILE") {
        state.companies = Arc::new(finance::load(&std::fs::read_to_string(path)?)?);
    }
    let address = std::env::var("MCP_BIND").unwrap_or_else(|_| "127.0.0.1:8081".into());
    let listener = tokio::net::TcpListener::bind(address).await?;
    println!("Blaubeere MCP listening on {}", listener.local_addr()?);
    axum::serve(listener, router(state))
        .with_graceful_shutdown(async {
            tokio::signal::ctrl_c().await.ok();
        })
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use http_body_util::BodyExt;
    use tower::ServiceExt;
    #[tokio::test]
    async fn transport_challenges_and_enforces_company_access() {
        let state = AppState::new(
            "sqlite::memory:",
            Config {
                demo_login: false,
                dataset_demo: false,
                app_origin: "http://localhost:3100".into(),
                api_origin: "http://localhost:8080".into(),
                mcp_resource: "http://localhost:8081/mcp".into(),
            },
        )
        .await
        .unwrap();
        auth::provision(
            &state,
            "mcp@example.com",
            "test-password-long",
            &["DEMO_001"],
        )
        .await
        .unwrap();
        let user: String = sqlx::query_scalar("SELECT id FROM users")
            .fetch_one(&state.db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO oauth_clients VALUES ('test','test','[]')")
            .execute(&state.db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO oauth_grants VALUES ('grant',?,'test',?,'finance:read',?,0)")
            .bind(&user)
            .bind(&state.config.mcp_resource)
            .bind(auth::now() + 3600)
            .execute(&state.db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO oauth_tokens VALUES (?,'grant','access',?,0)")
            .bind(auth::digest("test-access"))
            .bind(auth::now() + 3600)
            .execute(&state.db)
            .await
            .unwrap();
        let app = router(state.clone());
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/mcp")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert!(
            response.headers()[header::WWW_AUTHENTICATE]
                .to_str()
                .unwrap()
                .contains("resource_metadata")
        );
        for (name, args, allowed) in [
            ("list_companies", json!({}), true),
            ("get_cash_outlook", json!({"company_id":"DEMO_001"}), true),
            ("get_cash_outlook", json!({"company_id":"DEMO_002"}), false),
        ] {
            let request=Request::builder().method("POST").uri("/mcp").header("host","localhost:8081").header("authorization","Bearer test-access").header("content-type","application/json").header("accept","application/json, text/event-stream").header("mcp-protocol-version","2025-11-25").body(Body::from(json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}}).to_string())).unwrap();
            let response = app.clone().oneshot(request).await.unwrap();
            let bytes = response.into_body().collect().await.unwrap().to_bytes();
            let value: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(value.get("error").is_none(), allowed, "{value}");
        }
    }
}
