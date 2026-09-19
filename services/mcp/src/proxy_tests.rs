use super::*;
use axum::body::Body;
use http_body_util::BodyExt;
use tower::ServiceExt;

async fn call(app: &Router, name: &str, arguments: Value) -> Value {
    let request = Request::builder()
        .method("POST")
        .uri("/mcp")
        .header("host", "localhost:8081")
        .header("authorization", "Bearer proxy-test-access")
        .header("content-type", "application/json")
        .header("accept", "application/json, text/event-stream")
        .header("mcp-protocol-version", "2025-11-25")
        .body(Body::from(json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":arguments}}).to_string()))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
#[ignore = "requires explicit EXPORTED_ASSESSMENT_FILE generated from sealed predictions"]
async fn actual_export_mcp_rejects_proxy_plans() {
    let path = std::env::var("EXPORTED_ASSESSMENT_FILE").expect("Pass exported companies.json");
    let mut state = AppState::new(
        "sqlite::memory:",
        Config {
            app_origin: "http://localhost:3100".into(),
            api_origin: "http://localhost:8080".into(),
            mcp_resource: "http://localhost:8081/mcp".into(),
        },
    )
    .await
    .unwrap();
    state.companies = Arc::new(finance::load(&std::fs::read_to_string(path).unwrap()).unwrap());
    assert_eq!(state.companies.len(), 1286);
    auth::provision(
        &state,
        "proxy-mcp@example.com",
        "test-password-long",
        &["COMP_0001", "COMP_0002"],
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
        .bind(auth::digest("proxy-test-access"))
        .bind(auth::now() + 3600)
        .execute(&state.db)
        .await
        .unwrap();
    let app = router(state);
    for id in ["COMP_0001", "COMP_0002"] {
        let value = call(
            &app,
            "get_cash_outlook",
            json!({"company_id":id,"days":180,"buffer_cents":0}),
        )
        .await;
        assert!(value.get("error").is_none(), "{value}");
        let body: Value =
            serde_json::from_str(value["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
        assert!(body["forecast"].is_null());
        assert_eq!(body["cash_planning_available"], false);
        assert_eq!(
            body["company"]["predictive"]["probability_estimate"].is_null(),
            id == "COMP_0002"
        );
        let value = call(&app, "compare_plans", json!({"company_id":id,"goal":{
            "metric":"ending_cash","target_cents":0,"deadline":"2026-11-30","cash_floor_cents":0,
            "max_collection_days":0,"max_spend_reduction_pct":0,"max_funding_cents":0,"max_growth_pct":0,"business":null
        }})).await;
        assert!(
            value["error"]["message"]
                .as_str()
                .unwrap()
                .contains("Cash planning is unavailable")
        );
    }
    let denied = call(&app, "get_cash_outlook", json!({"company_id":"COMP_0003"})).await;
    assert!(
        denied["error"]["message"]
            .as_str()
            .unwrap()
            .contains("access")
    );
}
