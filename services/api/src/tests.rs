use super::*;
use axum::{body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::Value;
use tower::ServiceExt;

#[tokio::test]
async fn parquet_snapshot_preserves_model_data_and_requires_membership() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let source = tempfile::tempdir().unwrap();
    let output = tempfile::tempdir().unwrap();
    for path in [
        "reports/score_v3/assessments.parquet",
        "reports/score_v3/summary.json",
        "reports/cash_position/cash_position_monthly.parquet",
        "reports/payment_delay/payment_delay_monthly.parquet",
        "reports/transfer_resolution_v2/resolution.parquet",
    ] {
        let destination = source.path().join(path);
        std::fs::create_dir_all(destination.parent().unwrap()).unwrap();
        std::fs::copy(root.join(path), destination).unwrap();
    }
    let file = dataset_import::build(source.path(), output.path(), "test-revision")
        .await
        .unwrap();
    assert_eq!(
        file,
        dataset_import::build(source.path(), output.path(), "later-revision")
            .await
            .unwrap()
    );
    let mut state = state().await;
    state.config.demo_login = true;
    state.dataset = Some(
        dataset::connect(&format!("sqlite://{}", file.display()))
            .await
            .unwrap(),
    );
    let pool = state.dataset.as_ref().unwrap();
    assert!(
        sqlx::query("DELETE FROM parquet_records")
            .execute(pool)
            .await
            .is_err(),
        "Runtime snapshot must be read-only"
    );
    let missing: String = sqlx::query_scalar("SELECT payload FROM parquet_records WHERE source='scores' AND json_extract(payload,'$.health_score') IS NULL LIMIT 1").fetch_one(pool).await.unwrap();
    let missing: Value = serde_json::from_str(&missing).unwrap();
    let id = missing["company_id"].as_str().unwrap();
    auth::provision(
        &state,
        "team@example.com",
        "long-test-password",
        &["DEMO_001"],
    )
    .await
    .unwrap();
    let app = router(state.clone());
    let path = format!("/api/companies/{id}/assessment");
    assert_eq!(
        request(app.clone(), "GET", &path, json!(null), None, None)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let login = request(
        app.clone(),
        "POST",
        "/api/auth/login",
        json!({"email":"team@example.com","password":"long-test-password"}),
        None,
        Some(&state.config.app_origin),
    )
    .await;
    let cookie = login.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned();
    assert_eq!(
        request(app.clone(), "GET", &path, json!(null), Some(&cookie), None)
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
    dataset::grant_team_access(&state, "team@example.com")
        .await
        .unwrap();
    let response = request(app.clone(), "GET", &path, json!(null), Some(&cookie), None).await;
    assert_eq!(response.status(), StatusCode::OK);
    let result: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(result["kind"], "model");
    assert!(result.get("forecast").is_none());
    let records = result["records"].as_array().unwrap();
    let record = records
        .iter()
        .find(|row| row["as_of"] == missing["as_of"])
        .unwrap();
    assert!(record["health_score"].is_null());
    assert_eq!(record["reasons"], missing["reasons"]);
    assert_eq!(result["provenance"]["source_revision"], "test-revision");
    for row in records {
        for field in ["cash", "payment"] {
            if !row[field].is_null() {
                assert_eq!(
                    row[field]["month"].as_str().unwrap()[..10],
                    row["month"].as_str().unwrap()[..10]
                );
            }
        }
    }
    let total: i64 = sqlx::query_scalar("SELECT count(*) FROM parquet_records")
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(
        total,
        result["provenance"]["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|file| file["rows"].as_i64().unwrap())
            .sum::<i64>()
    );
    let demo = request(
        app.clone(),
        "POST",
        "/api/auth/demo",
        json!({}),
        None,
        Some(&state.config.app_origin),
    )
    .await;
    assert_eq!(demo.status(), StatusCode::OK);
    let demo_cookie = demo.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap();
    assert_eq!(
        request(app, "GET", &path, json!(null), Some(demo_cookie), None)
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
    assert!(
        dataset::grant_team_access(&state, "visitor@demo.blaubeere.local")
            .await
            .is_err()
    );
    std::fs::write(
        source.path().join("reports/score_v3/assessments.parquet"),
        b"invalid parquet",
    )
    .unwrap();
    assert!(
        dataset_import::build(source.path(), output.path(), "broken")
            .await
            .is_err()
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM parquet_records")
            .fetch_one(pool)
            .await
            .unwrap(),
        total,
        "Failed import cannot damage the active snapshot"
    );
    pool.close().await;
}

#[test]
fn origins_match_the_served_oauth_endpoints() {
    let valid = Config {
        demo_login: false,
        app_origin: "http://localhost:3100".into(),
        api_origin: "https://api.example.com".into(),
        mcp_resource: "https://mcp.example.com/mcp".into(),
    };
    assert!(valid.validate().is_ok());
    for origin in [
        "https://app.example.com/",
        "https://app.example.com/app",
        "http://app.example.com",
        "https://user:pass@app.example.com",
    ] {
        assert!(
            Config {
                app_origin: origin.into(),
                ..valid.clone()
            }
            .validate()
            .is_err()
        );
    }
    assert!(
        Config {
            api_origin: "https://api.example.com/oauth".into(),
            ..valid.clone()
        }
        .validate()
        .is_err()
    );
    assert!(
        Config {
            mcp_resource: "https://mcp.example.com/other".into(),
            ..valid
        }
        .validate()
        .is_err()
    );
}

pub(crate) async fn state() -> AppState {
    AppState::new(
        "sqlite::memory:",
        Config {
            demo_login: false,
            app_origin: "http://localhost:3100".into(),
            api_origin: "http://localhost:8080".into(),
            mcp_resource: "http://localhost:8081/mcp".into(),
        },
    )
    .await
    .unwrap()
}
async fn request(
    app: Router,
    method: &str,
    path: &str,
    body: serde_json::Value,
    cookie: Option<&str>,
    origin: Option<&str>,
) -> Response {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json");
    if let Some(cookie) = cookie {
        request = request.header("cookie", cookie);
    }
    if let Some(origin) = origin {
        request = request.header("origin", origin);
    }
    app.oneshot(request.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap()
}

#[tokio::test]
async fn registration_validates_identity_and_keeps_company_access_private() {
    let mut state = state().await;
    state.config.app_origin = "https://app.example.com".into();
    let origin = state.config.app_origin.clone();
    let app = router(state.clone());
    let password = format!("{}-cash-passphrase", "é".repeat(60));
    let credentials = json!({"email":"  New.User@Example.com  ","password":password});
    for origin in [None, Some("https://evil.example")] {
        assert_eq!(
            request(
                app.clone(),
                "POST",
                "/api/auth/register",
                credentials.clone(),
                None,
                origin
            )
            .await
            .status(),
            StatusCode::FORBIDDEN
        );
    }
    let bearer = Request::builder()
        .method("POST")
        .uri("/api/auth/register")
        .header("content-type", "application/json")
        .header("authorization", "Bearer ignored")
        .body(Body::from(credentials.to_string()))
        .unwrap();
    assert_eq!(
        app.clone().oneshot(bearer).await.unwrap().status(),
        StatusCode::BAD_REQUEST
    );
    for email in [
        "invalid",
        "a@@example.com",
        ".a@example.com",
        "a..b@example.com",
        "a b@example.com",
        "a@example",
        "a@-example.com",
        "a@example..com",
    ] {
        assert_eq!(
            request(
                app.clone(),
                "POST",
                "/api/auth/register",
                json!({"email":email,"password":password}),
                None,
                Some(&origin)
            )
            .await
            .status(),
            StatusCode::BAD_REQUEST
        );
    }
    for invalid in ["short".to_owned(), "🪴".repeat(6), "a".repeat(129)] {
        assert_eq!(
            request(
                app.clone(),
                "POST",
                "/api/auth/register",
                json!({"email":"new@example.com","password":invalid}),
                None,
                Some(&origin)
            )
            .await
            .status(),
            StatusCode::BAD_REQUEST
        );
    }
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/register",
            json!({"email":"new@example.com","password":password,"company_ids":["DEMO_001"]}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users")
            .fetch_one(&state.db)
            .await
            .unwrap(),
        0
    );

    let response = request(
        app.clone(),
        "POST",
        "/api/auth/register",
        credentials,
        None,
        Some(&origin),
    )
    .await;
    assert_eq!(response.status(), StatusCode::CREATED);
    let session = response.headers()[header::SET_COOKIE].to_str().unwrap();
    assert!(
        session.contains("HttpOnly")
            && session.contains("Secure")
            && session.contains("SameSite=Lax")
    );
    let cookie = session.split(';').next().unwrap().to_owned();
    let response = request(
        app.clone(),
        "GET",
        "/api/me",
        json!(null),
        Some(&cookie),
        None,
    )
    .await;
    let identity: serde_json::Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(identity["email"], "new.user@example.com");
    assert_eq!(identity["company_ids"], json!([]));
    assert_eq!(
        request(
            app.clone(),
            "GET",
            "/api/companies/DEMO_001/assessment",
            json!(null),
            Some(&cookie),
            None
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );

    for expected in [
        StatusCode::CONFLICT,
        StatusCode::CONFLICT,
        StatusCode::TOO_MANY_REQUESTS,
    ] {
        let response = request(
            app.clone(),
            "POST",
            "/api/auth/register",
            json!({"email":"NEW.USER@example.com","password":"different-password"}),
            None,
            Some(&origin),
        )
        .await;
        assert_eq!(response.status(), expected);
        assert!(!response.headers().contains_key(header::SET_COOKIE));
    }
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/logout",
            json!({}),
            Some(&cookie),
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert_eq!(
        request(
            app.clone(),
            "GET",
            "/api/me",
            json!(null),
            Some(&cookie),
            None
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/login",
            json!({"email":"new.user@example.com","password":password}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::OK,
        "Duplicate registration must not replace the password; Unicode passphrases must still sign in"
    );
    sqlx::query("UPDATE rate_limits SET hits = 30 WHERE key = 'register:global'")
        .execute(&state.db)
        .await
        .unwrap();
    assert_eq!(
        request(
            app,
            "POST",
            "/api/auth/register",
            json!({"email":"another@example.com","password":password}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::TOO_MANY_REQUESTS
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users")
            .fetch_one(&state.db)
            .await
            .unwrap(),
        1
    );
}

#[tokio::test]
async fn sessions_require_sign_in_origin_and_company_membership() {
    let state = state().await;
    auth::provision(
        &state,
        "finance@example.com",
        "long-test-password",
        &["DEMO_001"],
    )
    .await
    .unwrap();
    let app = router(state.clone());
    let credentials = json!({"email":"finance@example.com","password":"long-test-password"});
    assert_eq!(
        request(app.clone(), "GET", "/api/me", json!(null), None, None)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/login",
            credentials.clone(),
            None,
            Some("https://evil.example")
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    let response = request(
        app.clone(),
        "POST",
        "/api/auth/login",
        credentials.clone(),
        None,
        Some(&state.config.app_origin),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = response.headers()[header::SET_COOKIE]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned();
    let response = request(
        app.clone(),
        "GET",
        "/api/me",
        json!(null),
        Some(&cookie),
        None,
    )
    .await;
    let identity: serde_json::Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(identity["company_ids"], json!(["DEMO_001"]));
    let user: String = sqlx::query_scalar("SELECT id FROM users")
        .fetch_one(&state.db)
        .await
        .unwrap();
    assert!(
        auth::company_access(&state, &user, "DEMO_001")
            .await
            .is_ok()
    );
    assert_eq!(
        auth::company_access(&state, &user, "DEMO_002")
            .await
            .unwrap_err()
            .0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/logout",
            json!({}),
            Some(&cookie),
            Some(&state.config.app_origin)
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert_eq!(
        request(
            app.clone(),
            "GET",
            "/api/me",
            json!(null),
            Some(&cookie),
            None
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    for _ in 0..7 {
        request(
            app.clone(),
            "POST",
            "/api/auth/login",
            json!({"email":"finance@example.com","password":"wrong"}),
            None,
            Some(&state.config.app_origin),
        )
        .await;
    }
    assert_eq!(
        request(
            app,
            "POST",
            "/api/auth/login",
            credentials,
            None,
            Some(&state.config.app_origin)
        )
        .await
        .status(),
        StatusCode::TOO_MANY_REQUESTS
    );
}

#[tokio::test]
async fn demo_entry_keeps_sessions_and_company_isolation() {
    let mut state = state().await;
    let origin = state.config.app_origin.clone();
    assert_eq!(
        request(
            router(state.clone()),
            "POST",
            "/api/auth/demo",
            json!({}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    state.config.demo_login = true;
    auth::provision(
        &state,
        "team@example.com",
        "long-test-password",
        &["PRIVATE_001"],
    )
    .await
    .unwrap();
    let app = router(state.clone());
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/demo",
            json!({}),
            None,
            Some("https://evil.example")
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    let bearer = Request::builder()
        .method("POST")
        .uri("/api/auth/demo")
        .header("authorization", "Bearer ignored")
        .body(Body::empty())
        .unwrap();
    assert_eq!(
        app.clone().oneshot(bearer).await.unwrap().status(),
        StatusCode::BAD_REQUEST
    );
    let mut cookies = Vec::new();
    for _ in 0..2 {
        let response = request(
            app.clone(),
            "POST",
            "/api/auth/demo",
            json!({"email":"team@example.com"}),
            None,
            Some(&origin),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let header = response.headers()[header::SET_COOKIE].to_str().unwrap();
        assert!(
            header.contains("HttpOnly")
                && header.contains("SameSite=Lax")
                && header.contains("Max-Age=43200")
        );
        let cookie = header.split(';').next().unwrap().to_owned();
        let response = request(
            app.clone(),
            "GET",
            "/api/me",
            json!(null),
            Some(&cookie),
            None,
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let identity: serde_json::Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(identity["company_ids"], json!(["DEMO_001"]));
        assert_ne!(identity["email"], "team@example.com");
        assert_eq!(
            request(
                app.clone(),
                "GET",
                "/api/companies/DEMO_001/assessment",
                json!(null),
                Some(&cookie),
                None
            )
            .await
            .status(),
            StatusCode::OK
        );
        assert_eq!(
            request(
                app.clone(),
                "GET",
                "/api/companies/PRIVATE_001/assessment",
                json!(null),
                Some(&cookie),
                None
            )
            .await
            .status(),
            StatusCode::FORBIDDEN
        );
        cookies.push(cookie);
    }
    let visitors: i64 = sqlx::query_scalar("SELECT COUNT(DISTINCT user_id) FROM sessions")
        .fetch_one(&state.db)
        .await
        .unwrap();
    assert_eq!(
        visitors, 2,
        "Demo visitors must not share identities or assistant grants"
    );
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/login",
            json!({"email":"team@example.com","password":"wrong"}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app.clone(),
            "POST",
            "/api/auth/logout",
            json!({}),
            Some(&cookies[0]),
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert_eq!(
        request(
            app.clone(),
            "GET",
            "/api/me",
            json!(null),
            Some(&cookies[0]),
            None
        )
        .await
        .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        request(
            app.clone(),
            "GET",
            "/api/me",
            json!(null),
            Some(&cookies[1]),
            None
        )
        .await
        .status(),
        StatusCode::OK
    );
    let mut companies = (*state.companies).clone();
    for company in &mut companies {
        company.data_mode = "real".into();
    }
    state.companies = Arc::new(companies);
    assert_eq!(
        request(
            router(state),
            "POST",
            "/api/auth/demo",
            json!({}),
            None,
            Some(&origin)
        )
        .await
        .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
}
