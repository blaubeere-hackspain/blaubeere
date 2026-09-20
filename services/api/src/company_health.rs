//! One dated, evidence-based summary for the dashboard and MCP.
use crate::{ApiError, ApiResult, AppState, auth, dataset};
use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
};
use chrono::{NaiveDate, Utc};
use serde::Deserialize;
use serde_json::{Value, json};

// Product attention threshold, not a calibrated credit-risk boundary.
const LOW_SCORE: f64 = 40.0;

fn number(row: &Value, path: &str) -> Option<f64> {
    row.pointer(path)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
}
fn score(row: &Value) -> Option<f64> {
    if row["excluida"] == true
        || !matches!(row["confidence"].as_str(), Some("baja" | "media" | "alta"))
    {
        return None;
    }
    number(row, "/health_score").filter(|v| (0.0..99.99).contains(v))
}
pub fn has_activity(row: &Value) -> bool {
    number(row, "/health_score").is_some()
        || [
            "/n_meses_con_actividad",
            "/cash/volumen_conocido",
            "/payment/pago_n_facturas",
            "/payment/cobro_n_facturas",
            "/debt/servicio_observado_eur",
            "/debt/servicio_esperado_eur",
        ]
        .iter()
        .any(|path| number(row, path).is_some_and(|v| v > 0.0))
}

pub fn status(row: &Value, previous: Option<&Value>) -> Value {
    let current = score(row);
    let previous_score = previous.and_then(score);
    let delta = current.zip(previous_score).map(|(a, b)| a - b);
    let mut issues = Vec::new();
    if let Some(value) = current.filter(|v| *v < LOW_SCORE) {
        issues.push(json!({"code":"low_health_score","severity":"warning","title":"Cash-flow health needs attention","detail":format!("The saved score is {value:.1}/100, below the {LOW_SCORE:.0}-point attention threshold. Review cash and overdue payments."),"value":value,"unit":"points","source":"health_score"}));
    }
    if let Some(value) = delta.filter(|v| *v <= -5.0) {
        issues.push(json!({"code":"score_decline","severity":"warning","title":"Health score has fallen","detail":format!("The score fell {:.1} points since the previous month. Review the changes in cash, payments and collections.", -value),"value":value,"unit":"points","source":"health_score","previous_as_of":previous.map(|p| &p["as_of"])}));
    }
    for (path, code, title, detail, negative) in [
        (
            "/cash/saldo_reversa_eur",
            "negative_cash",
            "Reconstructed cash is negative",
            "Confirm available bank balances and upcoming obligations before making commitments.",
            true,
        ),
        (
            "/payment/pago_vencido_eur",
            "overdue_payments",
            "Supplier payments are overdue",
            "Review unpaid invoices and agree payment dates with suppliers.",
            false,
        ),
        (
            "/payment/cobro_vencido_eur",
            "overdue_collections",
            "Customer collections are overdue",
            "Review aging and confirm expected receipt dates with customers.",
            false,
        ),
        (
            "/cash/flujo_neto",
            "net_cash_outflow",
            "Cash outflows exceed inflows this month",
            "Review the largest movements; a net outflow alone does not establish financial distress.",
            true,
        ),
        (
            "/debt/deficit_servicio_eur",
            "debt_service_shortfall",
            "Observed debt service is below the expected amount",
            "Reconcile the shortfall with the lender schedule and source coverage; missing records can contribute.",
            false,
        ),
    ] {
        if let Some(value) =
            number(row, path).filter(|v| if negative { *v < 0.0 } else { *v > 0.0 })
        {
            issues.push(json!({"code":code,"severity":"warning","title":title,"detail":detail,"value":value,"unit":"EUR","source":path}));
        }
    }
    let state = if current.is_none() {
        "insufficient_evidence"
    } else if issues.is_empty() {
        "no_flags"
    } else {
        "attention_needed"
    };
    json!({"state":state,"score":current,"reported_score":row["health_score"],"confidence":row["confidence"],"previous_score":previous_score,"change_points":delta,"previous_as_of":previous.map(|p| &p["as_of"]),"issues":issues,"low_score_threshold":LOW_SCORE,"policy_note":"Attention rules are provisional product checks, not calibrated default probabilities. No flags does not establish financial health."})
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HealthQuery {
    pub month: Option<String>,
}

pub fn summarize(assessment: &Value, month: Option<&str>, today: NaiveDate) -> ApiResult<Value> {
    if let Some(month) = month
        && (month.len() != 7
            || NaiveDate::parse_from_str(&format!("{month}-01"), "%Y-%m-%d").is_err())
    {
        return Err(ApiError::bad("Use a calendar month in YYYY-MM format."));
    }
    let rows = assessment["records"].as_array().ok_or_else(|| {
        ApiError::bad("No published monthly assessments are available for this company.")
    })?;
    let available: Vec<&Value> = rows.iter().filter(|r| has_activity(r)).collect();
    let selected = available
        .iter()
        .rev()
        .find(|r| month.is_none_or(|m| r["month"].as_str().is_some_and(|s| s.starts_with(m))));
    let Some(row) = selected else {
        return Err(ApiError(StatusCode::NOT_FOUND, "No company data for this month. Choose an available month from the company assessment.".into()));
    };
    let cutoff = row["as_of"]
        .as_str()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok());
    let previous_month = row["month"]
        .as_str()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .and_then(|d| d.pred_opt())
        .map(|d| d.format("%Y-%m").to_string());
    let previous = rows.iter().find(|r| {
        previous_month
            .as_ref()
            .is_some_and(|m| r["month"].as_str().is_some_and(|s| s.starts_with(m)))
    });
    let cash_by_currency: Vec<Value> = row["daily_cash"].as_array().into_iter().flatten().map(|c| json!({"currency":c["currency"],"income":c["income"],"expenses":c["expense"],"month_end_cash":c["closing_balance"],"anchor_date":c["anchor_date"]})).collect();
    Ok(json!({
        "company":assessment["company"],"as_of":row["as_of"],"requested_month":month,
        "latest_available_as_of":available.last().map(|r| &r["as_of"]),
        "available_months":available.iter().filter_map(|r| r["month"].as_str().map(|s| s.chars().take(7).collect::<String>())).collect::<Vec<_>>(),
        "retrieved_on":today.to_string(),"data_age_days":cutoff.map(|d| (today-d).num_days()),
        "health":status(row, previous),
        "metrics":{"currency":"EUR","amount_unit":"major units, not cents","monthly_net_movement":row["cash"]["flujo_neto"],"reconstructed_cash":row["cash"]["saldo_reversa_eur"],"overdue_supplier_payments":row["payment"]["pago_vencido_eur"],"overdue_customer_collections":row["payment"]["cobro_vencido_eur"],"payment_arrears_index":row["mora_indice"],"cash_by_original_currency":cash_by_currency},
        "payment_aging":row["payment"],
        "model_inputs":{"observed_months":row["n_meses_con_actividad"],"window_months":row["n_meses_ventana"],"collections_eur":row["c6"],"effective_obligations_eur":row["t6_efectivo"],"applicable_cash_cushion_eur":row["colchon_aplicable"],"arrears_penalty_points":row["penalizacion_mora_puntos"],"debt_multiplier_penalty_points":row["penalizacion_multiplicador_puntos"]},
        "coverage":{"reasons":row["reasons"],"cash_confidence":row["cash"]["confidence"],"cash_flags":row["cash"]["flags"],"payment_confidence":row["payment"]["confidence"]},
        "provenance":{"source_revision":assessment["provenance"]["source_revision"],"imported_at":assessment["provenance"]["imported_at"],"files":assessment["provenance"]["files"],"model_version":row["version"],"model_limitations":assessment["provenance"]["model_summary"]["limitaciones"]},
        "interpretation":["This is the selected historical month-end snapshot, not live cash or a prediction of default.","The challenge data is synthetic. Scores and attention thresholds are provisional, not credit ratings.","Missing values are unknown, never zero. A missing or excluded score is insufficient evidence, not poor health.","Reconstructed cash uses a later anchor; it is not a bank balance observed on the cutoff date.","Currency subtotals remain separate. Do not add amounts in different currencies or infer revenue or profit from cash flows."]
    }))
}

pub async fn for_company(state: &AppState, id: &str, month: Option<&str>) -> ApiResult<Value> {
    let assessment = dataset::assessment(state, id).await?.ok_or_else(|| ApiError(StatusCode::NOT_FOUND, "This company has no imported monthly health data. Use get_cash_outlook for forecast-only companies.".into()))?;
    summarize(&assessment, month, Utc::now().date_naive())
}

pub async fn endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(query): Query<HealthQuery>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    auth::company_access(&state, &user, &id).await?;
    Ok(Json(
        for_company(&state, &id, query.month.as_deref()).await?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn summaries_preserve_evidence_dates_currency_and_unknowns() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../apps/landing/data/product-preview.json"
        ))
        .unwrap();
        let mut row = fixture["record"].clone();
        let mut previous = row.clone();
        previous["month"] = json!("2026-07-01");
        previous["as_of"] = json!("2026-07-31");
        previous["health_score"] = json!(45.1);
        let assessment =
            json!({"company":{"id":"COMP_0006"},"records":[previous,row],"provenance":{}});
        let today = NaiveDate::from_ymd_opt(2026, 9, 20).unwrap();
        let result = summarize(&assessment, None, today).unwrap();
        assert_eq!(result["as_of"], "2026-08-31");
        assert_eq!(result["data_age_days"], 20);
        assert_eq!(result["health"]["state"], "insufficient_evidence");
        assert_eq!(result["metrics"]["monthly_net_movement"], 4419.34);
        assert_eq!(
            result["metrics"]["cash_by_original_currency"][0]["income"],
            35572.28
        );
        assert!(
            result["health"]["issues"]
                .as_array()
                .unwrap()
                .iter()
                .any(|i| i["code"] == "overdue_payments")
        );
        assert_eq!(
            summarize(&assessment, Some("2026-07"), today).unwrap()["as_of"],
            "2026-07-31"
        );
        assert!(summarize(&assessment, Some("2026-09"), today).is_err());
        for month in ["2026-13", "2026-8", "2026-08-31"] {
            assert!(summarize(&assessment, Some(month), today).is_err());
        }
        for score in [Value::Null, json!(100), json!(-1)] {
            row["health_score"] = score;
            assert_eq!(status(&row, None)["state"], "insufficient_evidence");
        }
        row["health_score"] = json!(30);
        row["confidence"] = json!("ninguna");
        assert!(status(&row, None)["score"].is_null());
        row["confidence"] = json!("alta");
        assert_eq!(status(&row, None)["issues"][0]["code"], "low_health_score");
        row["excluida"] = json!(true);
        assert!(status(&row, None)["score"].is_null());
        row["excluida"] = json!(false);
        row["health_score"] = json!(40);
        row["cash"] = Value::Null;
        row["payment"] = Value::Null;
        row["debt"] = Value::Null;
        assert!(status(&row, None)["issues"].as_array().unwrap().is_empty());
        let unknown = summarize(&json!({"records":[row]}), None, today).unwrap();
        assert!(unknown["metrics"]["monthly_net_movement"].is_null());
    }
}
