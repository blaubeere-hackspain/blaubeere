use axum::{
    Json, Router,
    body::{Body, to_bytes},
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

const COMPANY_PICKER: &str = "ui://blau/company-picker-v4.html";
const PICKER_VERSIONS: &[&str] = &[
    COMPANY_PICKER,
    "ui://blau/company-picker-v1.html",
    "ui://blau/company-picker-chatgpt-v2.html",
    "ui://blau/company-picker-v3.html",
];

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
#[derive(Deserialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
struct CompanyPickerInput {
    /// Exact IDs returned by list_companies. Only these companies appear in the picker.
    company_ids: Vec<String>,
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
fn company_result(companies: Value) -> CallToolResult {
    let mut response = result(companies.clone());
    response.structured_content = Some(json!({"companies": companies}));
    response
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
        description = "List only companies this signed-in finance user may access, with exact IDs and metadata. Returns data without rendering UI. To let the user choose interactively, pass the returned IDs to render_company_picker. Read-only.",
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
        Ok(company_result(companies))
    }
    #[tool(
        description = "Render the interactive company picker in ChatGPT. First call list_companies, then pass the returned company IDs (or the subset requested by the user). Each choice loads financial health, issues and metrics inside the widget. Read-only; every ID must belong to the signed-in account.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn render_company_picker(
        &self,
        Parameters(input): Parameters<CompanyPickerInput>,
        ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let identity = auth::identity(&self.state, principal(&ctx)?)
            .await
            .map_err(failure)?;
        if input
            .company_ids
            .iter()
            .any(|id| !identity.company_ids.contains(id))
        {
            return Err(ErrorData::invalid_params(
                "Company access is not granted.",
                None,
            ));
        }
        let companies = finance::company_summaries(&self.state, &input.company_ids)
            .await
            .map_err(failure)?;
        Ok(company_result(companies))
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
        _ctx: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        Ok(serde_json::from_value(json!({"resources":[{"uri":COMPANY_PICKER,"name":"Blau company picker","mimeType":"text/html;profile=mcp-app"}]})).expect("valid resource list"))
    }
    async fn read_resource(
        &self,
        input: ReadResourceRequestParams,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        if !PICKER_VERSIONS.contains(&input.uri.as_str()) {
            return Err(ErrorData::invalid_params("Unknown resource", None));
        }
        println!("Serving MCP UI template {}", input.uri);
        let mime = if input.uri.ends_with("v3.html") || input.uri.ends_with("chatgpt-v2.html") {
            "text/html+skybridge"
        } else {
            "text/html;profile=mcp-app"
        };
        Ok(serde_json::from_value::<ReadResourceResult>(json!({"contents":[{"uri":input.uri,"mimeType":mime,"text":include_str!("company-picker.html"),"_meta":{"ui":{"prefersBorder":true,"csp":{"connectDomains":[],"resourceDomains":[]}},"openai/widgetPrefersBorder":true,"openai/widgetCSP":{"connect_domains":[],"resource_domains":[]},"openai/widgetDescription":"Pick an authorised company and inspect its dated financial health, alerts and metrics."}}]})).expect("valid UI resource").into())
    }
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().enable_resources().build()).with_instructions("Blaubeere supports internal finance planning. First use list_companies to retrieve authorised company IDs, then render_company_picker with those IDs when the user wants an interactive selector. Use get_company_health for a company's health, issues and metrics. Surface returned risk alerts and the assessment date; historical snapshots are not live financial status. Insufficient evidence is not poor health. Attention thresholds are provisional, not default probabilities. Preserve source labels, dates, missing coverage and explicit assumptions in every answer. Scenarios are conditional, not guarantees. Never infer retention or profit from bank data. Each company is authorised independently.")
    }
}

async fn authorize(State(state): State<AppState>, mut request: Request, next: Next) -> Response {
    // Discovery and this static HTML contain no account data. ChatGPT's app
    // refresh/template loader can request them without the user's OAuth token.
    if request.method() == axum::http::Method::POST {
        let (parts, body) = request.into_parts();
        let Ok(bytes) = to_bytes(body, 32 * 1024).await else {
            return StatusCode::PAYLOAD_TOO_LARGE.into_response();
        };
        let public = serde_json::from_slice::<Value>(&bytes).is_ok_and(|rpc| {
            rpc["jsonrpc"] == "2.0"
                && (matches!(
                    rpc["method"].as_str(),
                    Some(
                        "initialize"
                            | "notifications/initialized"
                            | "ping"
                            | "tools/list"
                            | "resources/list"
                            | "resources/templates/list"
                    )
                ) || (rpc["method"] == "resources/read"
                    && rpc["params"]["uri"]
                        .as_str()
                        .is_some_and(|uri| PICKER_VERSIONS.contains(&uri))))
        });
        request = Request::from_parts(parts, Body::from(bytes));
        if public {
            return next.run(request).await;
        }
    }
    match oauth::access_user(&state, request.headers()).await {
        Ok(user) => {
            request.extensions_mut().insert(Principal(user));
            next.run(request).await
        }
        Err(_) => {
            println!(
                "MCP authorization rejected for {} request",
                request.method()
            );
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
                let mut meta = json!({"securitySchemes":[{"type":"oauth2","scopes":[oauth::SCOPE]}],"ui":{"visibility":["model","app"]},"openai/widgetAccessible":true});
                if route.attr.name == "render_company_picker" {
                    meta["ui"]["resourceUri"] = json!(COMPANY_PICKER);
                    meta["openai/outputTemplate"] = json!("ui://blau/company-picker-v3.html");
                    meta["openai/toolInvocation/invoking"] = json!("Opening your companies…");
                    meta["openai/toolInvocation/invoked"] = json!("Choose a company");
                }
                if matches!(
                    route.attr.name.as_ref(),
                    "list_companies" | "render_company_picker"
                ) {
                    route.attr.output_schema = Some(Arc::new(json!({
                        "type":"object", "required":["companies"], "properties": {
                            "companies": {"type":"array", "items": {
                                "type":"object", "required":["id","name","currency"], "properties": {
                                    "id":{"type":"string"}, "name":{"type":"string"}, "currency":{"type":"string"}
                                }
                            }}
                        }
                    }).as_object().unwrap().clone()));
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
        for body in [
            json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_companies","arguments":{}}}),
            json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"render_company_picker","arguments":{"company_ids":["COMP_0006"]}}}),
            json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_company_health","arguments":{"company_id":"COMP_0006"}}}),
            json!({"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"file:///private"}}),
            json!([{"jsonrpc":"2.0","id":1,"method":"tools/list"},{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_companies"}}]),
        ] {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri("/mcp")
                        .header("content-type", "application/json")
                        .body(Body::from(body.to_string()))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }
        for (name, args, allowed) in [
            ("list_companies", json!({}), true),
            (
                "render_company_picker",
                json!({"company_ids":["COMP_0006"]}),
                true,
            ),
            ("render_company_picker", json!({"company_ids":[]}), true),
            (
                "render_company_picker",
                json!({"company_ids":["COMP_0006","COMP_PRIVATE"]}),
                false,
            ),
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
            if allowed && name == "render_company_picker" {
                let companies = value["result"]["structuredContent"]["companies"]
                    .as_array()
                    .unwrap();
                let ids = args["company_ids"].as_array().unwrap();
                assert_eq!(companies.len(), ids.len());
                assert!(companies.iter().all(|company| ids.contains(&company["id"])));
            }
        }
        for (method, params) in [
            (
                "initialize",
                json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"unauthenticated-widget-loader","version":"1"}}),
            ),
            ("tools/list", json!({})),
            ("resources/list", json!({})),
            ("resources/read", json!({"uri":COMPANY_PICKER})),
            ("resources/read", json!({"uri":PICKER_VERSIONS[1]})),
            ("resources/read", json!({"uri":PICKER_VERSIONS[2]})),
            ("resources/read", json!({"uri":PICKER_VERSIONS[3]})),
        ] {
            let request = Request::builder()
                .method("POST")
                .uri("/mcp")
                .header("host", "localhost:8081")
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
                    .find(|t| t["name"] == "render_company_picker")
                    .unwrap();
                assert_eq!(
                    picker["_meta"]["openai/outputTemplate"],
                    "ui://blau/company-picker-v3.html"
                );
                assert_eq!(picker["_meta"]["ui"]["resourceUri"], COMPANY_PICKER);
                assert_eq!(picker["_meta"]["openai/widgetAccessible"], true);
                assert_eq!(picker["outputSchema"]["required"], json!(["companies"]));
                for tool in value["result"]["tools"].as_array().unwrap() {
                    if tool["name"] != "render_company_picker" {
                        assert!(tool["_meta"]["ui"]["resourceUri"].is_null());
                        assert!(tool["_meta"]["openai/outputTemplate"].is_null());
                    }
                }
            } else if method == "resources/read" {
                let content = &value["result"]["contents"][0];
                assert_eq!(content["uri"], params["uri"]);
                assert_eq!(content["_meta"]["ui"]["csp"]["connectDomains"], json!([]));
                if params["uri"] == COMPANY_PICKER {
                    assert_eq!(content["mimeType"], "text/html;profile=mcp-app");
                }
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
