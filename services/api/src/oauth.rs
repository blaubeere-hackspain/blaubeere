use crate::{ApiError, ApiResult, AppState, auth};
use axum::{
    Form, Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::{Sqlite, Transaction};

pub const SCOPE: &str = "finance:read";

pub async fn metadata(State(state): State<AppState>) -> Json<Value> {
    let issuer = &state.config.api_origin;
    Json(
        json!({"issuer":issuer,"authorization_endpoint":format!("{}/connect",state.config.app_origin),"token_endpoint":format!("{issuer}/oauth/token"),"registration_endpoint":format!("{issuer}/oauth/register"),"revocation_endpoint":format!("{issuer}/oauth/revoke"),"response_types_supported":["code"],"grant_types_supported":["authorization_code","refresh_token"],"token_endpoint_auth_methods_supported":["none"],"code_challenge_methods_supported":["S256"],"scopes_supported":[SCOPE],"client_id_metadata_document_supported":false}),
    )
}

#[derive(Deserialize)]
pub struct Registration {
    client_name: Option<String>,
    redirect_uris: Vec<String>,
    token_endpoint_auth_method: Option<String>,
    grant_types: Option<Vec<String>>,
    response_types: Option<Vec<String>>,
}

pub async fn register(
    State(state): State<AppState>,
    Json(input): Json<Registration>,
) -> ApiResult<Response> {
    auth::limit(&state, "oauth:registration", 30, 60).await?;
    let name = input.client_name.unwrap_or_else(|| "MCP client".into());
    if name.trim().is_empty()
        || name.len() > 128
        || input.redirect_uris.is_empty()
        || input.redirect_uris.len() > 5
        || input
            .token_endpoint_auth_method
            .as_deref()
            .is_some_and(|m| m != "none")
        || input.grant_types.as_ref().is_some_and(|types| {
            types
                .iter()
                .any(|t| !matches!(t.as_str(), "authorization_code" | "refresh_token"))
        })
        || input
            .response_types
            .as_ref()
            .is_some_and(|types| types != &["code"])
    {
        return Err(ApiError::bad("invalid_client_metadata"));
    }
    for redirect in &input.redirect_uris {
        let url = url::Url::parse(redirect).map_err(|_| ApiError::bad("invalid_redirect_uri"))?;
        if redirect.len() > 2048
            || url.fragment().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
            || !(url.scheme() == "https"
                || (url.scheme() == "http"
                    && matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))))
        {
            return Err(ApiError::bad("invalid_redirect_uri"));
        }
    }
    let id = auth::secret();
    sqlx::query("INSERT INTO oauth_clients VALUES (?, ?, ?)")
        .bind(&id)
        .bind(&name)
        .bind(json!(input.redirect_uris).to_string())
        .execute(&state.db)
        .await?;
    Ok((StatusCode::CREATED,Json(json!({"client_id":id,"client_name":name,"redirect_uris":input.redirect_uris,"token_endpoint_auth_method":"none","grant_types":["authorization_code","refresh_token"],"response_types":["code"]}))).into_response())
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Authorization {
    pub client_id: String,
    pub redirect_uri: String,
    pub response_type: String,
    pub code_challenge: String,
    pub code_challenge_method: String,
    pub resource: String,
    pub scope: Option<String>,
    pub state: Option<String>,
}

async fn validate(state: &AppState, input: &Authorization) -> ApiResult<String> {
    if input.response_type != "code"
        || input.code_challenge_method != "S256"
        || input.code_challenge.len() != 43
        || URL_SAFE_NO_PAD.decode(&input.code_challenge).is_err()
        || input.resource != state.config.mcp_resource
        || input.scope.as_deref().is_some_and(|s| s != SCOPE)
        || input.state.as_ref().is_some_and(|s| s.len() > 1024)
    {
        return Err(ApiError::bad(
            "Invalid authorization request. Use S256 PKCE and the advertised MCP resource and scope.",
        ));
    }
    let client: Option<(String, String)> =
        sqlx::query_as("SELECT name, redirect_uris FROM oauth_clients WHERE id = ?")
            .bind(&input.client_id)
            .fetch_optional(&state.db)
            .await?;
    let (name, redirects) = client.ok_or_else(|| ApiError::bad("Unknown OAuth client."))?;
    let redirects: Vec<String> = serde_json::from_str(&redirects)
        .map_err(|_| ApiError::bad("Invalid client registration."))?;
    if !redirects.contains(&input.redirect_uri) {
        return Err(ApiError::bad(
            "The redirect URL is not registered for this client.",
        ));
    }
    Ok(name)
}

pub async fn consent_request(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(input): Query<Authorization>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    let name = validate(&state, &input).await?;
    let identity = auth::identity(&state, &user).await?;
    Ok(Json(
        json!({"client_name":name,"redirect_uri":input.redirect_uri,"scope":SCOPE,"company_ids":identity.company_ids,"email":identity.email}),
    ))
}
#[derive(Deserialize)]
pub struct Decision {
    pub request: Authorization,
    pub approve: bool,
}

pub async fn consent(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<Decision>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    validate(&state, &input.request).await?;
    let mut redirect = url::Url::parse(&input.request.redirect_uri)
        .map_err(|_| ApiError::bad("Invalid redirect URL."))?;
    if let Some(value) = &input.request.state {
        redirect.query_pairs_mut().append_pair("state", value);
    }
    if !input.approve {
        redirect
            .query_pairs_mut()
            .append_pair("error", "access_denied");
        return Ok(Json(json!({"redirect":redirect.as_str()})));
    }
    let code = auth::secret();
    let grant = auth::secret();
    let mut tx = state.db.begin().await?;
    sqlx::query("INSERT INTO oauth_grants (id,user_id,client_id,resource,scope,expires_at) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(&grant).bind(&user).bind(&input.request.client_id).bind(&input.request.resource).bind(SCOPE).bind(auth::now()+30*86400).execute(&mut *tx).await?;
    sqlx::query("INSERT INTO oauth_codes VALUES (?, ?, ?, ?, ?)")
        .bind(auth::digest(&code))
        .bind(&grant)
        .bind(&input.request.redirect_uri)
        .bind(&input.request.code_challenge)
        .bind(auth::now() + 300)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    redirect.query_pairs_mut().append_pair("code", &code);
    Ok(Json(json!({"redirect":redirect.as_str()})))
}

#[derive(Deserialize)]
pub struct TokenRequest {
    pub grant_type: String,
    pub client_id: String,
    pub resource: String,
    pub code: Option<String>,
    pub redirect_uri: Option<String>,
    pub code_verifier: Option<String>,
    pub refresh_token: Option<String>,
}

async fn issue(tx: &mut Transaction<'_, Sqlite>, grant: &str) -> ApiResult<Value> {
    let access = auth::secret();
    let refresh = auth::secret();
    for (token, kind, ttl) in [(&access, "access", 3600), (&refresh, "refresh", 30 * 86400)] {
        sqlx::query("INSERT INTO oauth_tokens (hash,grant_id,kind,expires_at) VALUES (?, ?, ?, ?)")
            .bind(auth::digest(token))
            .bind(grant)
            .bind(kind)
            .bind(auth::now() + ttl)
            .execute(&mut **tx)
            .await?;
    }
    Ok(
        json!({"access_token":access,"refresh_token":refresh,"token_type":"Bearer","expires_in":3600,"scope":SCOPE}),
    )
}

async fn exchange(state: &AppState, input: TokenRequest) -> ApiResult<Value> {
    if input.resource != state.config.mcp_resource {
        return Err(ApiError::bad("invalid_target"));
    }
    auth::limit(state, "oauth:token", 120, 60).await?;
    let mut tx = state.db.begin().await?;
    let grant = match input.grant_type.as_str() {
        "authorization_code" => {
            let code = input.code.ok_or_else(|| ApiError::bad("invalid_grant"))?;
            let verifier = input
                .code_verifier
                .ok_or_else(|| ApiError::bad("invalid_grant"))?;
            if !(43..=128).contains(&verifier.len())
                || !verifier
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"-._~".contains(&b))
            {
                return Err(ApiError::bad("invalid_grant"));
            }
            let record: Option<(String,String,String)> = sqlx::query_as("SELECT c.grant_id, c.redirect_uri, c.challenge FROM oauth_codes c JOIN oauth_grants g ON g.id = c.grant_id WHERE c.hash = ? AND c.expires_at > ? AND g.client_id = ? AND g.resource = ? AND g.revoked = 0 AND g.expires_at > ?")
                .bind(auth::digest(&code)).bind(auth::now()).bind(&input.client_id).bind(&input.resource).bind(auth::now()).fetch_optional(&mut *tx).await?;
            let (grant, redirect, challenge) =
                record.ok_or_else(|| ApiError::bad("invalid_grant"))?;
            if Some(redirect) != input.redirect_uri || challenge != auth::digest(&verifier) {
                return Err(ApiError::bad("invalid_grant"));
            }
            sqlx::query("DELETE FROM oauth_codes WHERE hash = ?")
                .bind(auth::digest(&code))
                .execute(&mut *tx)
                .await?;
            grant
        }
        "refresh_token" => {
            let refresh = input
                .refresh_token
                .ok_or_else(|| ApiError::bad("invalid_grant"))?;
            let record: Option<(String,i64)> = sqlx::query_as("SELECT t.grant_id, t.used FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id WHERE t.hash = ? AND t.kind = 'refresh' AND t.expires_at > ? AND g.client_id = ? AND g.resource = ? AND g.revoked = 0 AND g.expires_at > ?")
                .bind(auth::digest(&refresh)).bind(auth::now()).bind(&input.client_id).bind(&input.resource).bind(auth::now()).fetch_optional(&mut *tx).await?;
            let (grant, used) = record.ok_or_else(|| ApiError::bad("invalid_grant"))?;
            if used != 0 {
                sqlx::query("UPDATE oauth_grants SET revoked = 1 WHERE id = ?")
                    .bind(&grant)
                    .execute(&mut *tx)
                    .await?;
                tx.commit().await?;
                return Err(ApiError::bad("invalid_grant"));
            }
            sqlx::query("UPDATE oauth_tokens SET used = 1 WHERE hash = ?")
                .bind(auth::digest(&refresh))
                .execute(&mut *tx)
                .await?;
            grant
        }
        _ => return Err(ApiError::bad("unsupported_grant_type")),
    };
    let result = issue(&mut tx, &grant).await?;
    tx.commit().await?;
    Ok(result)
}
pub async fn token(
    State(state): State<AppState>,
    Form(input): Form<TokenRequest>,
) -> ApiResult<Json<Value>> {
    Ok(Json(exchange(&state, input).await?))
}

pub async fn access_user(state: &AppState, headers: &HeaderMap) -> ApiResult<String> {
    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer "))
        .filter(|s| s.len() <= 128)
        .ok_or_else(ApiError::unauthorized)?;
    sqlx::query_scalar("SELECT g.user_id FROM oauth_tokens t JOIN oauth_grants g ON g.id = t.grant_id WHERE t.hash = ? AND t.kind = 'access' AND t.expires_at > ? AND g.expires_at > ? AND g.revoked = 0 AND g.resource = ? AND g.scope = ?")
        .bind(auth::digest(token)).bind(auth::now()).bind(auth::now()).bind(&state.config.mcp_resource).bind(SCOPE).fetch_optional(&state.db).await?.ok_or_else(ApiError::unauthorized)
}

#[derive(Deserialize)]
pub struct Revocation {
    token: String,
    client_id: String,
}
pub async fn revoke(
    State(state): State<AppState>,
    Form(input): Form<Revocation>,
) -> ApiResult<Json<Value>> {
    auth::limit(&state, "oauth:revoke", 120, 60).await?;
    sqlx::query("UPDATE oauth_grants SET revoked = 1 WHERE client_id = ? AND id IN (SELECT grant_id FROM oauth_tokens WHERE hash = ?)").bind(input.client_id).bind(auth::digest(&input.token)).execute(&state.db).await?;
    Ok(Json(json!({})))
}
pub async fn connections(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    let rows: Vec<(String,String)> = sqlx::query_as("SELECT g.id, c.name FROM oauth_grants g JOIN oauth_clients c ON c.id=g.client_id WHERE g.user_id = ? AND g.revoked = 0 AND g.expires_at > ? ORDER BY c.name")
        .bind(user).bind(auth::now()).fetch_all(&state.db).await?;
    Ok(Json(json!(
        rows.into_iter()
            .map(|(id, name)| json!({"id":id,"name":name}))
            .collect::<Vec<_>>()
    )))
}
pub async fn disconnect(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    sqlx::query("UPDATE oauth_grants SET revoked = 1 WHERE id = ? AND user_id = ?")
        .bind(id)
        .bind(user)
        .execute(&state.db)
        .await?;
    Ok(Json(json!({"ok":true})))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn pkce_codes_rotate_refresh_tokens_and_revoke_replayed_grants() {
        let state = crate::tests::state().await;
        auth::provision(
            &state,
            "user@example.com",
            "test-password-long",
            &["DEMO_001"],
        )
        .await
        .unwrap();
        sqlx::query("INSERT INTO oauth_clients VALUES ('client','test','[\"https://client.example/callback\"]')").execute(&state.db).await.unwrap();
        let user: String = sqlx::query_scalar("SELECT id FROM users")
            .fetch_one(&state.db)
            .await
            .unwrap();
        sqlx::query("INSERT INTO sessions VALUES (?, ?, ?)")
            .bind(auth::digest("session"))
            .bind(user)
            .bind(auth::now() + 60)
            .execute(&state.db)
            .await
            .unwrap();
        let headers =
            HeaderMap::from_iter([(header::COOKIE, "blaubeere_session=session".parse().unwrap())]);
        let verifier = auth::secret();
        let input = Authorization {
            client_id: "client".into(),
            redirect_uri: "https://client.example/callback".into(),
            response_type: "code".into(),
            code_challenge: auth::digest(&verifier),
            code_challenge_method: "S256".into(),
            resource: state.config.mcp_resource.clone(),
            scope: Some(SCOPE.into()),
            state: Some("csrf".into()),
        };
        let mut bad = input.clone();
        bad.redirect_uri = "https://evil.example".into();
        assert!(validate(&state, &bad).await.is_err());
        bad = input.clone();
        bad.resource = "https://other.example/mcp".into();
        assert!(validate(&state, &bad).await.is_err());
        let Json(result) = consent(
            State(state.clone()),
            headers,
            Json(Decision {
                request: input.clone(),
                approve: true,
            }),
        )
        .await
        .unwrap();
        let uri = url::Url::parse(result["redirect"].as_str().unwrap()).unwrap();
        let code = uri
            .query_pairs()
            .find(|(k, _)| k == "code")
            .unwrap()
            .1
            .into_owned();
        let request = |verifier: String| TokenRequest {
            grant_type: "authorization_code".into(),
            client_id: "client".into(),
            resource: input.resource.clone(),
            code: Some(code.clone()),
            redirect_uri: Some(input.redirect_uri.clone()),
            code_verifier: Some(verifier),
            refresh_token: None,
        };
        assert!(exchange(&state, request(auth::secret())).await.is_err());
        let tokens = exchange(&state, request(verifier.clone())).await.unwrap();
        assert!(exchange(&state, request(verifier)).await.is_err());
        let access = HeaderMap::from_iter([(
            header::AUTHORIZATION,
            format!("Bearer {}", tokens["access_token"].as_str().unwrap())
                .parse()
                .unwrap(),
        )]);
        assert!(access_user(&state, &access).await.is_ok());
        assert!(auth::browser_user(&state, &access).await.is_err());
        let refresh = || TokenRequest {
            grant_type: "refresh_token".into(),
            client_id: "client".into(),
            resource: input.resource.clone(),
            code: None,
            redirect_uri: None,
            code_verifier: None,
            refresh_token: Some(tokens["refresh_token"].as_str().unwrap().into()),
        };
        assert!(exchange(&state, refresh()).await.is_ok());
        assert!(exchange(&state, refresh()).await.is_err());
        assert!(access_user(&state, &access).await.is_err());
    }
}
