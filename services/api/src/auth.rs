use crate::{ApiError, ApiResult, AppState};
use argon2::{
    Argon2, PasswordHash, PasswordHasher, PasswordVerifier,
    password_hash::{SaltString, rand_core::OsRng},
};
use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};

pub fn now() -> i64 {
    chrono::Utc::now().timestamp()
}
pub fn secret() -> String {
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}
pub fn digest(value: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(value.as_bytes()))
}

pub async fn hash_password(password: String) -> anyhow::Result<String> {
    tokio::task::spawn_blocking(move || {
        Argon2::default()
            .hash_password(password.as_bytes(), &SaltString::generate(&mut OsRng))
            .map(|hash| hash.to_string())
            .map_err(|e| anyhow::anyhow!(e.to_string()))
    })
    .await?
}

pub async fn provision(
    state: &AppState,
    email: &str,
    password: &str,
    companies: &[&str],
) -> anyhow::Result<()> {
    anyhow::ensure!(
        email.contains('@') && email.len() <= 254 && (12..=128).contains(&password.len()),
        "Use a valid email and a password of 12–128 characters"
    );
    let email = email.trim().to_lowercase();
    if sqlx::query_scalar::<_, String>("SELECT id FROM users WHERE email = ?")
        .bind(&email)
        .fetch_optional(&state.db)
        .await?
        .is_some()
    {
        return Ok(());
    }
    let hash = hash_password(password.to_owned()).await?;
    let id = secret();
    let mut tx = state.db.begin().await?;
    sqlx::query("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)")
        .bind(&id)
        .bind(email)
        .bind(hash)
        .execute(&mut *tx)
        .await?;
    for company in companies.iter().filter(|id| !id.trim().is_empty()) {
        sqlx::query("INSERT INTO memberships VALUES (?, ?)")
            .bind(&id)
            .bind(company.trim())
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}

pub async fn limit(state: &AppState, key: &str, max: i64, seconds: i64) -> ApiResult<()> {
    let hits: i64 = sqlx::query_scalar("INSERT INTO rate_limits (key, hits, resets_at) VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET hits = CASE WHEN resets_at <= ? THEN 1 ELSE hits + 1 END, resets_at = CASE WHEN resets_at <= ? THEN ? ELSE resets_at END RETURNING hits")
        .bind(key).bind(now() + seconds).bind(now()).bind(now()).bind(now() + seconds).fetch_one(&state.db).await?;
    if hits > max {
        return Err(ApiError(
            StatusCode::TOO_MANY_REQUESTS,
            "Too many attempts. Please try again later.".into(),
        ));
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Credentials {
    pub email: String,
    pub password: String,
}

pub async fn login(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<Credentials>,
) -> ApiResult<Response> {
    if headers.contains_key(header::AUTHORIZATION) {
        return Err(ApiError::bad("Use browser sign-in."));
    }
    if input.email.len() > 254 || input.password.len() > 128 {
        return Err(ApiError::bad("Check your email and password."));
    }
    let email = input.email.trim().to_lowercase();
    limit(&state, "login:global", 120, 60).await?;
    limit(&state, &format!("login:{}", digest(&email)), 8, 900).await?;
    let user: Option<(String, String)> =
        sqlx::query_as("SELECT id, password_hash FROM users WHERE email = ?")
            .bind(&email)
            .fetch_optional(&state.db)
            .await?;
    let hash = user
        .as_ref()
        .map(|u| u.1.clone())
        .unwrap_or_else(|| (*state.dummy_hash).clone());
    let valid = tokio::task::spawn_blocking(move || {
        PasswordHash::new(&hash).is_ok_and(|hash| {
            Argon2::default()
                .verify_password(input.password.as_bytes(), &hash)
                .is_ok()
        })
    })
    .await
    .map_err(|_| {
        ApiError(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Please retry signing in.".into(),
        )
    })?;
    let user_id = user.filter(|_| valid).map(|u| u.0).ok_or_else(|| {
        ApiError(
            StatusCode::UNAUTHORIZED,
            "Email or password is incorrect.".into(),
        )
    })?;
    start_session(&state, &user_id, &email).await
}

pub async fn demo_login(State(state): State<AppState>, headers: HeaderMap) -> ApiResult<Response> {
    if !state.config.demo_login {
        return Err(ApiError::forbidden());
    }
    if headers.contains_key(header::AUTHORIZATION) {
        return Err(ApiError::bad("Use browser sign-in."));
    }
    let company = state
        .companies
        .iter()
        .find(|company| company.id == "DEMO_001" && company.data_mode == "demo")
        .ok_or_else(|| {
            ApiError(
                StatusCode::SERVICE_UNAVAILABLE,
                "The sample workspace is temporarily unavailable. Please retry.".into(),
            )
        })?;
    limit(&state, "login:demo", 120, 60).await?;
    let user_id = secret();
    let email = format!("visitor-{user_id}@demo.blaubeere.local");
    // ponytail: demo identities persist with their OAuth grants; prune inactive demo visitors for long-running public demos.
    let mut tx = state.db.begin().await?;
    sqlx::query("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)")
        .bind(&user_id)
        .bind(&email)
        .bind(state.dummy_hash.as_str())
        .execute(&mut *tx)
        .await?;
    sqlx::query("INSERT INTO memberships (user_id, company_id) VALUES (?, ?)")
        .bind(&user_id)
        .bind(&company.id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    start_session(&state, &user_id, &email).await
}

async fn start_session(state: &AppState, user_id: &str, email: &str) -> ApiResult<Response> {
    let token = secret();
    sqlx::query("INSERT INTO sessions VALUES (?, ?, ?)")
        .bind(digest(&token))
        .bind(user_id)
        .bind(now() + 43_200)
        .execute(&state.db)
        .await?;
    let cookie = cookie(state, &token, 43_200);
    Ok(([(header::SET_COOKIE, cookie)], Json(json!({"email":email}))).into_response())
}

fn cookie(state: &AppState, token: &str, age: i64) -> String {
    format!(
        "blaubeere_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{}",
        if state.config.app_origin.starts_with("https://") {
            "; Secure"
        } else {
            ""
        }
    )
}
fn session_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::COOKIE)?
        .to_str()
        .ok()?
        .split(';')
        .find_map(|part| part.trim().strip_prefix("blaubeere_session="))
}

pub async fn browser_user(state: &AppState, headers: &HeaderMap) -> ApiResult<String> {
    if headers.contains_key(header::AUTHORIZATION) {
        return Err(ApiError::unauthorized());
    }
    let token = session_token(headers).ok_or_else(ApiError::unauthorized)?;
    sqlx::query_scalar("SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at > ?")
        .bind(digest(token))
        .bind(now())
        .fetch_optional(&state.db)
        .await?
        .ok_or_else(ApiError::unauthorized)
}

pub async fn company_access(state: &AppState, user: &str, company: &str) -> ApiResult<()> {
    let allowed: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM memberships WHERE user_id = ? AND company_id = ?)",
    )
    .bind(user)
    .bind(company)
    .fetch_one(&state.db)
    .await?;
    if !allowed {
        return Err(ApiError::forbidden());
    }
    Ok(())
}

#[derive(Serialize)]
pub struct Identity {
    pub email: String,
    pub company_ids: Vec<String>,
    pub mcp_resource: String,
}

pub async fn identity(state: &AppState, user: &str) -> ApiResult<Identity> {
    Ok(Identity {
        email: sqlx::query_scalar("SELECT email FROM users WHERE id = ?")
            .bind(user)
            .fetch_one(&state.db)
            .await?,
        company_ids: sqlx::query_scalar(
            "SELECT company_id FROM memberships WHERE user_id = ? ORDER BY company_id",
        )
        .bind(user)
        .fetch_all(&state.db)
        .await?,
        mcp_resource: state.config.mcp_resource.clone(),
    })
}
pub async fn me(State(state): State<AppState>, headers: HeaderMap) -> ApiResult<Json<Identity>> {
    let user = browser_user(&state, &headers).await?;
    Ok(Json(identity(&state, &user).await?))
}
pub async fn logout(State(state): State<AppState>, headers: HeaderMap) -> ApiResult<Response> {
    let user = browser_user(&state, &headers).await?;
    sqlx::query("DELETE FROM sessions WHERE user_id = ? AND token_hash = ?")
        .bind(user)
        .bind(digest(session_token(&headers).unwrap_or_default()))
        .execute(&state.db)
        .await?;
    Ok((
        [(header::SET_COOKIE, cookie(&state, "", 0))],
        Json(json!({"ok":true})),
    )
        .into_response())
}
