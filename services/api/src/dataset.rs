//! Read published model outputs from the immutable, indexed Parquet import.
use crate::{ApiError, ApiResult, AppState};
use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use serde_json::{Value, json};
use sqlx::{
    SqlitePool,
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
};
use std::str::FromStr;

pub async fn connect(url: &str) -> anyhow::Result<SqlitePool> {
    let pool = SqlitePoolOptions::new()
        .max_connections(4)
        .connect_with(SqliteConnectOptions::from_str(url)?.read_only(true))
        .await?;
    let metadata: String = sqlx::query_scalar("SELECT payload FROM dataset_metadata")
        .fetch_one(&pool)
        .await?;
    let metadata: Value = serde_json::from_str(&metadata)?;
    anyhow::ensure!(
        metadata["schema_version"] == 4
            && metadata["model_summary"]["model_version"] == "healthscore_v4",
        "Dataset requires a fresh v4 import; run the API import-parquet command"
    );
    Ok(pool)
}
fn decode(payload: &str) -> ApiResult<Value> {
    serde_json::from_str(payload).map_err(|error| {
        tracing::error!(%error, "invalid dataset record");
        ApiError(
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            "The imported assessment is unavailable.".into(),
        )
    })
}
fn company(id: &str, group: &str, name: &str) -> Value {
    json!({"id":id,"name":name,"group":group,"currency":"EUR","data_mode":"challenge"})
}

pub async fn companies(pool: &SqlitePool) -> ApiResult<Vec<Value>> {
    let rows: Vec<(String, String, String)> =
        sqlx::query_as("SELECT id, group_id, name FROM dataset_companies ORDER BY id")
            .fetch_all(pool)
            .await?;
    Ok(rows
        .into_iter()
        .map(|(id, group, name)| company(&id, &group, &name))
        .collect())
}

// Public demo reads only the explicitly published challenge snapshot. Private
// company fixtures, memberships and OAuth grants never participate in this path.
fn demo_pool(state: &AppState) -> ApiResult<&SqlitePool> {
    if !state.config.dataset_demo {
        return Err(ApiError(
            StatusCode::NOT_FOUND,
            "The company demo is not enabled on this server.".into(),
        ));
    }
    state.dataset.as_ref().ok_or_else(|| ApiError(StatusCode::SERVICE_UNAVAILABLE, "The company dataset has not been imported yet. Please retry after the import finishes.".into()))
}

pub async fn demo_companies(State(state): State<AppState>) -> ApiResult<Json<Vec<Value>>> {
    Ok(Json(companies(demo_pool(&state)?).await?))
}

pub async fn demo_assessment(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> ApiResult<Json<Value>> {
    demo_pool(&state)?;
    assessment(&state, &id).await?.map(Json).ok_or_else(|| {
        ApiError(
            StatusCode::NOT_FOUND,
            "This company is not in the published dataset.".into(),
        )
    })
}
pub async fn assessment(state: &AppState, id: &str) -> ApiResult<Option<Value>> {
    let Some(pool) = &state.dataset else {
        return Ok(None);
    };
    let company_row: Option<(String, String)> =
        sqlx::query_as("SELECT group_id, name FROM dataset_companies WHERE id=?")
            .bind(id)
            .fetch_optional(pool)
            .await?;
    let Some((group, name)) = company_row else {
        return Ok(None);
    };
    type JoinedRecord = (
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
    );
    let rows: Vec<JoinedRecord> = sqlx::query_as(
        "SELECT projection.payload,s.payload,c.payload,p.payload,d.payload,f.payload FROM parquet_records s \
         LEFT JOIN parquet_records c ON c.source='cash' AND c.record_key=s.record_key \
         LEFT JOIN parquet_records p ON p.source='payments' AND p.record_key=s.record_key \
         LEFT JOIN parquet_records d ON d.source='debt' AND d.record_key=s.record_key \
         LEFT JOIN parquet_records f ON f.source='daily_cash' AND f.record_key=s.record_key \
         LEFT JOIN parquet_records projection ON projection.source='cash_projection' AND projection.record_key=s.record_key \
         WHERE s.source='scores' AND s.company_id=? ORDER BY s.period",
    )
    .bind(id)
    .fetch_all(pool)
    .await?;
    let mut records = Vec::with_capacity(rows.len());
    for (projection, score, cash, payment, debt, daily_cash) in rows {
        let mut row = decode(&score)?;
        row["cash_projection"] = projection
            .as_deref()
            .map(decode)
            .transpose()?
            .unwrap_or(Value::Null);
        row["cash"] = cash
            .as_deref()
            .map(decode)
            .transpose()?
            .unwrap_or(Value::Null);
        row["payment"] = payment
            .as_deref()
            .map(decode)
            .transpose()?
            .unwrap_or(Value::Null);
        row["debt"] = debt
            .as_deref()
            .map(decode)
            .transpose()?
            .unwrap_or(Value::Null);
        row["daily_cash"] = daily_cash
            .as_deref()
            .map(decode)
            .transpose()?
            .unwrap_or(Value::Null);
        row["health_status"] = crate::company_health::status(&row, records.last());
        row["health_projection"] =
            crate::company_health::cash_projection(&row).unwrap_or(Value::Null);
        records.push(row);
    }
    let metadata: String = sqlx::query_scalar("SELECT payload FROM dataset_metadata")
        .fetch_one(pool)
        .await?;
    Ok(Some(
        json!({"kind":"model","company":company(id, &group, &name),"records":records,"provenance":decode(&metadata)?}),
    ))
}

// Only the explicitly configured team account receives the imported company memberships.
pub async fn grant_team_access(state: &AppState, email: &str) -> anyhow::Result<()> {
    let email = email.trim().to_lowercase();
    anyhow::ensure!(
        !email.ends_with("@demo.blaubeere.local"),
        "Cannot grant dataset access to a demo visitor"
    );
    let Some(pool) = &state.dataset else {
        return Ok(());
    };
    let user: String = sqlx::query_scalar("SELECT id FROM users WHERE email=?")
        .bind(email.trim().to_lowercase())
        .fetch_one(&state.db)
        .await?;
    let ids: Vec<String> = sqlx::query_scalar("SELECT id FROM dataset_companies")
        .fetch_all(pool)
        .await?;
    let mut tx = state.db.begin().await?;
    for id in ids {
        sqlx::query("INSERT OR IGNORE INTO memberships(user_id,company_id) VALUES (?,?)")
            .bind(&user)
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}

/// Administrative CLI only: add explicitly selected companies to an existing account.
pub async fn grant_company_access(
    state: &AppState,
    email: &str,
    ids: &[&str],
    replace: Option<&str>,
) -> anyhow::Result<()> {
    anyhow::ensure!(!ids.is_empty(), "Select at least one company");
    anyhow::ensure!(
        replace.is_none_or(|old| !ids.contains(&old)),
        "Replacement must be a different company"
    );
    let pool = state
        .dataset
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("Dataset is not configured"))?;
    let email = email.trim().to_lowercase();
    anyhow::ensure!(
        !email.ends_with("@demo.blaubeere.local"),
        "Cannot grant private access to a demo visitor"
    );
    let user: Option<String> = sqlx::query_scalar("SELECT id FROM users WHERE email=?")
        .bind(&email)
        .fetch_optional(&state.db)
        .await?;
    let user = user.ok_or_else(|| {
        anyhow::anyhow!("Account does not exist; create it through the sign-up page first")
    })?;
    for id in ids {
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM dataset_companies WHERE id=?)")
                .bind(id)
                .fetch_one(pool)
                .await?;
        anyhow::ensure!(exists, "Company {id} is not in the imported dataset");
    }
    let mut tx = state.db.begin().await?;
    for id in ids {
        sqlx::query("INSERT OR IGNORE INTO memberships(user_id,company_id) VALUES (?,?)")
            .bind(&user)
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    if let Some(old) = replace {
        sqlx::query("DELETE FROM memberships WHERE user_id=? AND company_id=?")
            .bind(&user)
            .bind(old)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}
