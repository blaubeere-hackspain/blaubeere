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

// Same v4 formula as xray/scoring_v4.py, changing only the cash cushion.
fn score_for_cash(row: &Value, cash: f64) -> Option<f64> {
    let c6 = number(row, "/c6")?;
    let t6 = number(row, "/t6_efectivo")?;
    let alpha = number(row, "/alpha")?;
    let k = number(row, "/k")?;
    let beta = number(row, "/beta")?;
    let beta_fx = number(row, "/beta_fx")?;
    if !cash.is_finite()
        || [c6, t6, alpha, k].iter().any(|v| *v < 0.0)
        || !(0.0..=1.0).contains(&beta)
        || !(0.0..=1.0).contains(&beta_fx)
    {
        return None;
    }
    for field in [
        "r_hist",
        "mora_indice",
        "multiplicador_deuda",
        "indice_fx_aplicado",
    ] {
        if !row.get(field)?.is_null()
            && !number(row, &format!("/{field}")).is_some_and(|v| (0.0..=1.0).contains(&v))
        {
            return None;
        }
    }
    let history = number(row, "/r_hist");
    let cushion = cash.max(0.0).min(alpha * t6);
    let denominator = c6 + cushion + t6 + if history.is_some() { k } else { 0.0 };
    if denominator <= 0.0 || !denominator.is_finite() {
        return None;
    }
    let base = 100.0 * (c6 + cushion + history.map_or(0.0, |ratio| k * ratio)) / denominator;
    let adjusted = base
        * number(row, "/mora_indice").map_or(1.0, |mora| 1.0 - beta * mora)
        * number(row, "/multiplicador_deuda").unwrap_or(1.0)
        * number(row, "/indice_fx_aplicado").map_or(1.0, |fx| 1.0 - beta_fx * fx);
    adjusted.is_finite().then(|| adjusted.clamp(0.0, 100.0))
}

pub fn cash_projection(row: &Value) -> Option<Value> {
    let current = score(row)?;
    if row["version"] != "healthscore_v4" {
        return None;
    }
    // Fail closed if a future model revision no longer reproduces the saved score.
    let baseline = score_for_cash(row, number(row, "/colchon_v4")?)?;
    if (baseline - current).abs() > 1e-6 {
        return None;
    }
    let forecast = &row["cash_projection"];
    if forecast["as_of"] != row["as_of"] || forecast["currency"] != "EUR" {
        return None;
    }
    let horizons = forecast["horizons"].as_array()?;
    if horizons.len() != 3 {
        return None;
    }
    let mut points = Vec::new();
    for (point, h) in horizons.iter().zip([30, 60, 90]) {
        if point["h"] != h {
            return None;
        }
        let opening = number(point, "/saldo_corte_eur");
        let comparable = opening
            .zip(number(row, "/cash/saldo_reversa_eur"))
            .is_some_and(|(a, b)| (a - b).abs() <= 0.01);
        let estimated = if comparable {
            number(point, "/saldo_proyectado_eur").and_then(|cash| score_for_cash(row, cash))
        } else {
            None
        };
        points.push(json!({"h":h,"date":point["date"],"health_score":estimated}));
    }
    Some(
        json!({"as_of":row["as_of"],"method":"cash_only_scenario_v1","points":points,
        "assumptions":"Only the cash cushion changes. Historical flows, obligations, arrears, debt and FX factors stay fixed at the selected month. Conditional estimates, not published future scores."}),
    )
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
    let forecast_available = row["cash_projection"]["horizons"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|point| {
            [
                "/entrada_esperada_eur",
                "/salida_esperada_eur",
                "/flujo_neto_esperado_eur",
                "/saldo_proyectado_eur",
            ]
            .iter()
            .any(|path| number(point, path).is_some())
        })
        || row["health_projection"]["points"]
            .as_array()
            .into_iter()
            .flatten()
            .any(|point| number(point, "/health_score").is_some());
    Ok(json!({
        "company":assessment["company"],"as_of":row["as_of"],"requested_month":month,
        "latest_available_as_of":available.last().map(|r| &r["as_of"]),
        "available_months":available.iter().filter_map(|r| r["month"].as_str().map(|s| s.chars().take(7).collect::<String>())).collect::<Vec<_>>(),
        "retrieved_on":today.to_string(),"data_age_days":cutoff.map(|d| (today-d).num_days()),
        "health":status(row, previous),
        "forecast_available":forecast_available,
        "cash_projection":row["cash_projection"],
        "health_projection":row["health_projection"],
        "metrics":{"currency":"EUR","amount_unit":"major units, not cents","monthly_net_movement":row["cash"]["flujo_neto"],"reconstructed_cash":row["cash"]["saldo_reversa_eur"],"overdue_supplier_payments":row["payment"]["pago_vencido_eur"],"overdue_customer_collections":row["payment"]["cobro_vencido_eur"],"payment_arrears_index":row["mora_indice"],"cash_by_original_currency":cash_by_currency},
        "payment_aging":row["payment"],
        "model_inputs":{"observed_months":row["n_meses_con_actividad"],"window_months":row["n_meses_ventana"],"collections_eur":row["c6"],"effective_obligations_eur":row["t6_efectivo"],"applicable_cash_cushion_eur":row["colchon_aplicable"],"arrears_penalty_points":row["penalizacion_mora_puntos"],"debt_multiplier_penalty_points":row["penalizacion_multiplicador_puntos"]},
        "coverage":{"reasons":row["reasons"],"cash_confidence":row["cash"]["confidence"],"cash_flags":row["cash"]["flags"],"payment_confidence":row["payment"]["confidence"]},
        "provenance":{"source_revision":assessment["provenance"]["source_revision"],"imported_at":assessment["provenance"]["imported_at"],"files":assessment["provenance"]["files"],"model_version":row["version"],"model_limitations":assessment["provenance"]["model_summary"]["limitaciones"]},
        "interpretation":["This is the selected historical month-end snapshot, not live cash or a prediction of default.","The challenge data is synthetic. Scores and attention thresholds are provisional, not credit ratings.","Missing values are unknown, never zero. A missing or excluded score is insufficient evidence, not poor health.","Reconstructed cash uses a later anchor; it is not a bank balance observed on the cutoff date.","Currency subtotals remain separate. Do not add amounts in different currencies or infer revenue or profit from cash flows.","Cash projections are cumulative 30/60/90-day estimates from the selected cutoff, in EUR major units, not cents. Outflows are negative. They cover open invoices only, excluding payroll, taxes and other cash movements; do not add the horizons together. Missing opening balances can leave projected balances unknown even when invoice flows are available.","Health projections are conditional 0–100 cash-only estimates, not published future scores. Only the cash cushion changes; all other model inputs remain fixed at the selected month. Forecasts are not guarantees."]
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
    fn cash_health_projection_keeps_adjustments_caps_and_missing_evidence() {
        let mut row = json!({"version":"healthscore_v4","as_of":"2026-08-31","health_score":35.64,
        "excluida":false,"confidence":"alta","c6":100.0,"t6_efectivo":100.0,"alpha":3.0,
        "k":4178.45,"r_hist":null,"beta":0.25,"mora_indice":0.4,"multiplicador_deuda":0.8,
        "beta_fx":0.05,"indice_fx_aplicado":0.2,"colchon_v4":0.0,"cash":{"saldo_reversa_eur":0.0},
        "cash_projection":{"as_of":"2026-08-31","currency":"EUR","horizons":[
            {"h":30,"date":"2026-09-30","saldo_corte_eur":0.0,"saldo_proyectado_eur":100.0},
            {"h":60,"date":"2026-10-30","saldo_corte_eur":0.0,"saldo_proyectado_eur":300.0},
            {"h":90,"date":"2026-11-29","saldo_corte_eur":0.0,"saldo_proyectado_eur":500.0}
        ]}});
        let original = row.clone();
        let projection = cash_projection(&row).unwrap();
        assert_eq!(row, original, "Estimates never overwrite observations");
        for (point, expected) in projection["points"]
            .as_array()
            .unwrap()
            .iter()
            .zip([47.52, 57.024, 57.024])
        {
            assert!((point["health_score"].as_f64().unwrap() - expected).abs() < 1e-9);
        }
        assert!((score_for_cash(&row, -100.0).unwrap() - 35.64).abs() < 1e-9);
        row["cash_projection"]["horizons"][1]["saldo_proyectado_eur"] = Value::Null;
        assert!(cash_projection(&row).unwrap()["points"][1]["health_score"].is_null());
        row["cash_projection"]["horizons"][0]["saldo_corte_eur"] = json!(100.0);
        assert!(cash_projection(&row).unwrap()["points"][0]["health_score"].is_null());
        for (field, value) in [
            ("excluida", json!(true)),
            ("confidence", json!("ninguna")),
            ("health_score", Value::Null),
            ("health_score", json!(90.0)),
            ("t6_efectivo", Value::Null),
        ] {
            let mut invalid = original.clone();
            invalid[field] = value;
            assert!(
                cash_projection(&invalid).is_none(),
                "Reject unsupported baseline: {field}"
            );
        }
        row["cash_projection"]["as_of"] = json!("2026-07-31");
        assert!(cash_projection(&row).is_none());
    }

    #[test]
    fn summaries_preserve_evidence_dates_currency_and_unknowns() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/company-health.json")).unwrap();
        let mut row = fixture["record"].clone();
        let mut previous = row.clone();
        previous["month"] = json!("2026-07-01");
        previous["as_of"] = json!("2026-07-31");
        previous["health_score"] = json!(45.1);
        row["cash_projection"] = json!({"as_of":"2026-08-31","currency":"EUR","horizons":[
            {"h":30,"date":"2026-09-30","saldo_proyectado_eur":17000.0},
            {"h":60,"date":"2026-10-30","saldo_proyectado_eur":null},
            {"h":90,"date":"2026-11-29","saldo_proyectado_eur":21000.0}
        ]});
        row["health_projection"] = json!({"as_of":"2026-08-31","method":"cash_only_scenario_v1","points":[
            {"h":30,"date":"2026-09-30","health_score":31.0},
            {"h":60,"date":"2026-10-30","health_score":null},
            {"h":90,"date":"2026-11-29","health_score":32.0}
        ]});
        let assessment =
            json!({"company":{"id":"COMP_0006"},"records":[previous,row],"provenance":{}});
        let today = NaiveDate::from_ymd_opt(2026, 9, 20).unwrap();
        let result = summarize(&assessment, None, today).unwrap();
        assert_eq!(result["as_of"], "2026-08-31");
        assert_eq!(result["data_age_days"], 20);
        assert_eq!(result["forecast_available"], true);
        assert_eq!(result["cash_projection"], row["cash_projection"]);
        assert_eq!(result["health_projection"], row["health_projection"]);
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
        let earlier = summarize(&assessment, Some("2026-07"), today).unwrap();
        assert_eq!(earlier["as_of"], "2026-07-31");
        assert_eq!(earlier["forecast_available"], false);
        assert!(earlier["cash_projection"].is_null());
        assert!(earlier["health_projection"].is_null());
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
        row["health_projection"] = Value::Null;
        row["cash_projection"]["horizons"] = json!([
            {"h":30,"saldo_proyectado_eur":null,"flujo_neto_esperado_eur":null}
        ]);
        let unavailable = summarize(&json!({"records":[row]}), None, today).unwrap();
        assert_eq!(unavailable["forecast_available"], false);
        row["cash_projection"]["horizons"][0]["flujo_neto_esperado_eur"] = json!(0.0);
        let flows_only = summarize(&json!({"records":[row]}), None, today).unwrap();
        assert_eq!(flows_only["forecast_available"], true, "Known zero is data");
        assert!(flows_only["cash_projection"]["horizons"][0]["saldo_proyectado_eur"].is_null());
        assert!(flows_only["health_projection"].is_null());
    }
}
