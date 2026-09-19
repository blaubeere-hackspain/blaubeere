use super::*;
use axum::{body::Body, http::Request};
use http_body_util::BodyExt;
use tower::ServiceExt;

#[test]
fn origins_match_the_served_oauth_endpoints() {
    let valid = Config {
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
