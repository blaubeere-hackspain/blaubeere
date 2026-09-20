use axum::{
    Json, Router,
    extract::{Request, State},
    http::{HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use blaubeere_api::{AppState, Config, auth, company_health, finance, oauth};
use rmcp::{
    ErrorData, RoleServer, ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{
        CallToolResult, ContentBlock, ListResourcesResult, PaginatedRequestParams,
        ReadResourceRequestParams, ReadResourceResponse, ReadResourceResult, ServerCapabilities,
        ServerInfo,
    },
    service::RequestContext,
    tool, tool_handler, tool_router,
    transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    },
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;

const COMPANY_PICKER: &str = "ui://blau/company-picker-v1.html";

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
#[derive(Deserialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
struct CompanyHealthInput {
    /// Exact company ID from list_companies, for example COMP_0006.
    company_id: String,
    /// Optional YYYY-MM cutoff. Omit to use the latest month with company data.
    month: Option<String>,
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
        description = "Get a company's financial health state, saved monthly score, risk alerts, current issues at that cutoff and valuable metrics: cash movements, reconstructed cash, overdue collections/payments, aging, arrears and original-currency totals. Use for 'How is COMP_0006 doing?' or 'What needs attention?'. Returns dated evidence and missing coverage, not a prediction or credit rating. Use list_companies for exact IDs. Read-only.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn get_company_health(
        &self,
        Parameters(input): Parameters<CompanyHealthInput>,
        ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        auth::company_access(&self.state, principal(&ctx)?, &input.company_id)
            .await
            .map_err(failure)?;
        let summary =
            company_health::for_company(&self.state, &input.company_id, input.month.as_deref())
                .await
                .map_err(failure)?;
        Ok(CallToolResult::structured(summary))
    }
    #[tool(
        description = "Show the interactive company picker in ChatGPT and list only companies this signed-in finance user may access. Use when the user wants to choose a company or see their companies. Read-only.",
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
        let companies = finance::company_summaries(&self.state, &identity.company_ids)
            .await
            .map_err(failure)?;
        let mut response = result(companies.clone());
        response.structured_content = Some(json!({"companies":companies}));
        Ok(response)
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
    async fn list_resources(
        &self,
        _: Option<PaginatedRequestParams>,
        ctx: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        principal(&ctx)?;
        Ok(serde_json::from_value(json!({"resources":[{"uri":COMPANY_PICKER,"name":"Blau company picker","mimeType":"text/html;profile=mcp-app"}]})).expect("valid resource list"))
    }
    async fn read_resource(
        &self,
        input: ReadResourceRequestParams,
        ctx: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        principal(&ctx)?;
        if input.uri != COMPANY_PICKER {
            return Err(ErrorData::invalid_params("Unknown resource", None));
        }
        Ok(serde_json::from_value::<ReadResourceResult>(json!({"contents":[{"uri":COMPANY_PICKER,"mimeType":"text/html;profile=mcp-app","text":include_str!("company-picker.html"),"_meta":{"ui":{"prefersBorder":true,"csp":{"connectDomains":[],"resourceDomains":[]}},"openai/widgetDescription":"Pick an authorised company and inspect its dated financial health, alerts and metrics."}}]})).expect("valid UI resource").into())
    }
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().enable_resources().build()).with_instructions("Blaubeere supports internal finance planning. Use list_companies to show the company picker; use get_company_health for a company's health, issues and metrics. Surface returned risk alerts and the assessment date; historical snapshots are not live financial status. Insufficient evidence is not poor health. Attention thresholds are provisional, not default probabilities. Preserve source labels, dates, missing coverage and explicit assumptions in every answer. Scenarios are conditional, not guarantees. Never infer retention or profit from bank data. Each company is authorised independently.")
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
            let mut tool_router = FinanceTools::tool_router();
            for route in tool_router.map.values_mut() {
                let mut meta = json!({"securitySchemes":[{"type":"oauth2","scopes":[oauth::SCOPE]}],"ui":{"visibility":["model","app"]}});
                if route.attr.name == "list_companies" {
                    meta["ui"]["resourceUri"] = json!(COMPANY_PICKER);
                    meta["openai/outputTemplate"] = json!(COMPANY_PICKER);
                }
                route.attr.meta = Some(rmcp::model::MetaObject(meta.as_object().unwrap().clone()));
            }
            Ok(FinanceTools {
                state: tools_state.clone(),
                tool_router,
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
        let mut state = AppState::new(
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
            &["DEMO_001", "COMP_0006"],
        )
        .await
        .unwrap();
        let pool = sqlx::sqlite::SqlitePoolOptions::new()
            .max_connections(1)
            .connect("sqlite::memory:")
            .await
            .unwrap();
        sqlx::raw_sql("CREATE TABLE dataset_companies(id TEXT PRIMARY KEY, group_id TEXT); CREATE TABLE parquet_records(source TEXT, record_key TEXT, company_id TEXT, period TEXT, payload TEXT); CREATE TABLE dataset_metadata(payload TEXT); INSERT INTO dataset_metadata VALUES ('{}'); INSERT INTO dataset_companies VALUES ('COMP_0006','GROUP_TEST'),('COMP_PRIVATE','GROUP_PRIVATE');").execute(&pool).await.unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../apps/landing/data/product-preview.json"
        ))
        .unwrap();
        for (source, payload) in [
            ("scores", &fixture["record"]),
            ("cash", &fixture["record"]["cash"]),
            ("payments", &fixture["record"]["payment"]),
            ("debt", &fixture["record"]["debt"]),
            ("daily_cash", &fixture["record"]["daily_cash"]),
        ] {
            sqlx::query("INSERT INTO parquet_records VALUES (?,'COMP_0006:2026-08','COMP_0006','2026-08-01',?)").bind(source).bind(payload.to_string()).execute(&pool).await.unwrap();
        }
        state.dataset = Some(pool);
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
            (
                "get_company_health",
                json!({"company_id":"COMP_0006"}),
                true,
            ),
            (
                "get_company_health",
                json!({"company_id":"COMP_0006","month":"2026-08"}),
                true,
            ),
            (
                "get_company_health",
                json!({"company_id":"COMP_PRIVATE"}),
                false,
            ),
            (
                "get_company_health",
                json!({"company_id":"COMP_0006","month":"2026-09"}),
                false,
            ),
            (
                "get_company_health",
                json!({"company_id":"COMP_0006","month":"2026-13"}),
                false,
            ),
        ] {
            let request=Request::builder().method("POST").uri("/mcp").header("host","localhost:8081").header("authorization","Bearer test-access").header("content-type","application/json").header("accept","application/json, text/event-stream").header("mcp-protocol-version","2025-11-25").body(Body::from(json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}}).to_string())).unwrap();
            let response = app.clone().oneshot(request).await.unwrap();
            let bytes = response.into_body().collect().await.unwrap().to_bytes();
            let value: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(value.get("error").is_none(), allowed, "{value}");
            if allowed && name == "get_company_health" {
                let summary = &value["result"]["structuredContent"];
                assert_eq!(summary["company"]["id"], "COMP_0006");
                assert_eq!(summary["as_of"], "2026-08-31");
                assert_eq!(summary["metrics"]["overdue_supplier_payments"], 5870.98);
                assert_eq!(summary["health"]["state"], "insufficient_evidence");
                assert_eq!(summary["health"]["issues"][0]["code"], "overdue_payments");
            }
            if allowed && name == "list_companies" {
                assert_eq!(
                    value["result"]["structuredContent"]["companies"]
                        .as_array()
                        .unwrap()
                        .len(),
                    2
                );
                assert!(!value.to_string().contains("COMP_PRIVATE"));
            }
        }
        for (method, params) in [
            ("tools/list", json!({})),
            ("resources/read", json!({"uri":COMPANY_PICKER})),
        ] {
            let request = Request::builder()
                .method("POST")
                .uri("/mcp")
                .header("host", "localhost:8081")
                .header("authorization", "Bearer test-access")
                .header("content-type", "application/json")
                .header("accept", "application/json, text/event-stream")
                .body(Body::from(
                    json!({"jsonrpc":"2.0","id":2,"method":method,"params":params}).to_string(),
                ))
                .unwrap();
            let bytes = app
                .clone()
                .oneshot(request)
                .await
                .unwrap()
                .into_body()
                .collect()
                .await
                .unwrap()
                .to_bytes();
            let value: Value = serde_json::from_slice(&bytes).unwrap();
            assert!(value.get("error").is_none(), "{value}");
            if method == "tools/list" {
                let picker = value["result"]["tools"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|t| t["name"] == "list_companies")
                    .unwrap();
                assert_eq!(picker["_meta"]["ui"]["resourceUri"], COMPANY_PICKER);
            } else {
                assert_eq!(
                    value["result"]["contents"][0]["mimeType"],
                    "text/html;profile=mcp-app"
                );
                assert!(
                    value["result"]["contents"][0]["text"]
                        .as_str()
                        .unwrap()
                        .contains("get_company_health")
                );
            }
        }
    }
}
