use crate::{ApiError, ApiResult, AppState, auth};
use axum::{
    Json,
    extract::{Path, Query, State},
    http::HeaderMap,
};
use chrono::{Duration, NaiveDate};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};

const MAX_MONEY: i64 = 1_000_000_000_000;

#[derive(Clone, Serialize, Deserialize)]
pub struct Company {
    pub id: String,
    pub name: String,
    pub group: String,
    pub currency: String,
    pub assessment_date: NaiveDate,
    pub model_version: String,
    pub history_mode: String,
    pub data_mode: String,
    pub opening_cash_cents: i64,
    pub buffer_cents: i64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub health_assessments: Vec<HealthAssessment>,
    pub health: Value,
    pub history: Vec<Value>,
    pub drivers: Vec<Value>,
    pub coverage: Vec<Value>,
    pub flows: Vec<Flow>,
}
// A model-returned rating and its evidence, frozen at that day's cutoff.
#[derive(Clone, Serialize, Deserialize)]
pub struct HealthAssessment {
    pub date: NaiveDate,
    pub model_version: String,
    pub history_mode: String,
    pub health: HealthRating,
    pub drivers: Vec<HealthDriver>,
    pub coverage: Vec<HealthCoverage>,
    pub flows: Vec<Flow>,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct HealthRating {
    pub score: f64,
    pub previous_score: Option<f64>,
    pub period: String,
    pub label: String,
    pub note: String,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct HealthDriver {
    pub label: String,
    pub detail: String,
    pub points: Option<f64>,
    pub source_ids: Vec<String>,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct HealthCoverage {
    pub label: String,
    pub status: String,
    pub as_of: Option<NaiveDate>,
    pub detail: String,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Flow {
    pub id: String,
    pub label: String,
    pub amount_cents: i64,
    pub settled_cents: i64,
    pub date: NaiveDate,
    pub known_on: NaiveDate,
    pub kind: String,
    pub timing: String,
    pub source: String,
}

pub fn load(input: &str) -> anyhow::Result<Vec<Company>> {
    let companies: Vec<Company> = serde_json::from_str(input)?;
    let mut ids = HashSet::new();
    for c in &companies {
        anyhow::ensure!(
            ids.insert(&c.id) && !c.id.is_empty(),
            "Duplicate or empty company ID"
        );
        anyhow::ensure!(
            c.currency.len() == 3 && c.currency.bytes().all(|b| b.is_ascii_uppercase()),
            "Use ISO currency codes"
        );
        anyhow::ensure!(
            (-MAX_MONEY..=MAX_MONEY).contains(&c.opening_cash_cents)
                && (0..=MAX_MONEY).contains(&c.buffer_cents),
            "Cash outside supported bounds"
        );
        anyhow::ensure!(
            matches!(c.history_mode.as_str(), "as_known" | "reconstructed"),
            "Unknown history mode"
        );
        validate_flows(&c.flows)?;
        let mut dates = HashSet::new();
        for assessment in &c.health_assessments {
            anyhow::ensure!(
                dates.insert(assessment.date) && assessment.date <= c.assessment_date,
                "Duplicate or future health assessment date"
            );
            anyhow::ensure!(
                !assessment.model_version.trim().is_empty()
                    && matches!(
                        assessment.history_mode.as_str(),
                        "as_known" | "reconstructed"
                    ),
                "Health assessment needs a model version and history mode"
            );
            anyhow::ensure!(
                (0.0..=100.0).contains(&assessment.health.score)
                    && assessment
                        .health
                        .previous_score
                        .is_none_or(|score| (0.0..=100.0).contains(&score)),
                "Health scores must be between 0 and 100"
            );
            anyhow::ensure!(
                assessment.drivers.iter().all(|driver| driver
                    .points
                    .is_none_or(|points| (-100.0..=100.0).contains(&points))),
                "Invalid score driver effect"
            );
            anyhow::ensure!(
                assessment.coverage.iter().all(|source| matches!(
                    source.status.as_str(),
                    "available" | "missing" | "stale"
                ) && source
                    .as_of
                    .is_none_or(|date| date <= assessment.date)),
                "Invalid health evidence coverage"
            );
            validate_flows(&assessment.flows)?;
            anyhow::ensure!(
                assessment
                    .flows
                    .iter()
                    .all(|flow| flow.known_on <= assessment.date),
                "Health evidence cannot include future knowledge"
            );
        }
    }
    Ok(companies)
}

fn validate_flows(flows: &[Flow]) -> anyhow::Result<()> {
    let mut ids = HashSet::new();
    anyhow::ensure!(flows.len() <= 10_000, "Too many flows in one assessment");
    for f in flows {
        anyhow::ensure!(ids.insert(&f.id), "Duplicate cash flow: {}", f.id);
        anyhow::ensure!(
            (-MAX_MONEY..=MAX_MONEY).contains(&f.amount_cents)
                && (0..=f.amount_cents.abs()).contains(&f.settled_cents),
            "Invalid flow amount or settlement"
        );
        anyhow::ensure!(
            matches!(
                f.kind.as_str(),
                "receivable"
                    | "payable"
                    | "payroll"
                    | "tax"
                    | "debt"
                    | "discretionary"
                    | "operating_receipt"
                    | "internal_transfer"
            ),
            "Unknown flow kind"
        );
        anyhow::ensure!(
            matches!(f.timing.as_str(), "contractual" | "estimated"),
            "Unknown timing basis"
        );
    }
    anyhow::ensure!(
        flows.iter().map(|f| f.amount_cents.abs()).sum::<i64>() <= 100 * MAX_MONEY,
        "Assessment amount exceeds supported bounds"
    );
    Ok(())
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Point {
    pub date: NaiveDate,
    pub cash_cents: i64,
}
#[derive(Serialize)]
pub struct Forecast {
    pub points: Vec<Point>,
    pub checkpoints: Vec<Value>,
    pub minimum: Point,
    pub first_shortfall: Option<Point>,
    pub funding_needed_cents: i64,
    pub closing_cash_cents: i64,
    pub buffer_cents: i64,
    pub horizon_days: i64,
}

fn forecast(
    company: &Company,
    days: i64,
    buffer: i64,
    changes: &Changes,
    business: Option<&Business>,
) -> Forecast {
    let cutoff = company.assessment_date;
    let mut daily = BTreeMap::<NaiveDate, i64>::new();
    for flow in company
        .flows
        .iter()
        .filter(|f| f.known_on <= cutoff && f.kind != "internal_transfer")
    {
        let mut amount =
            flow.amount_cents.signum() * (flow.amount_cents.abs() - flow.settled_cents);
        let mut date = flow.date.max(cutoff + Duration::days(1));
        if flow.kind == "receivable" && amount > 0 {
            date = (date - Duration::days(changes.collection_days)).max(cutoff + Duration::days(1));
        }
        if flow.kind == "discretionary" && amount < 0 {
            amount = ((amount as f64) * (1.0 - changes.spend_reduction_pct / 100.0)).round() as i64;
        }
        *daily.entry(date).or_default() += amount;
    }
    if changes.funding_cents > 0 {
        *daily.entry(cutoff + Duration::days(1)).or_default() += changes.funding_cents;
        // Interest is paid daily so cash constraints include the funding cost.
        for day in 1..=days {
            *daily.entry(cutoff + Duration::days(day)).or_default() -=
                (changes.funding_cents as f64 * 0.08 / 365.0).ceil() as i64;
        }
    }
    if let Some(business) = business {
        // Incremental growth only: the supplied starting revenue is already represented by baseline cash.
        for month in 1..=days / 30 {
            let extra = (business.monthly_revenue_cents as f64
                * ((1.0 + changes.growth_pct / 100.0).powi(month as i32) - 1.0))
                .round() as i64;
            *daily
                .entry(cutoff + Duration::days(month * 30))
                .or_default() -=
                (extra as f64 * (1.0 - business.gross_margin_pct / 100.0)).round() as i64;
            *daily
                .entry(cutoff + Duration::days(month * 30 + business.collection_days))
                .or_default() += extra;
        }
    }
    let mut cash = company.opening_cash_cents;
    let points: Vec<Point> = (0..=days)
        .map(|day| {
            let date = cutoff + Duration::days(day);
            cash += daily.get(&date).copied().unwrap_or_default();
            Point {
                date,
                cash_cents: cash,
            }
        })
        .collect();
    let minimum = points.iter().min_by_key(|p| p.cash_cents).unwrap().clone();
    let first_shortfall = points.iter().find(|p| p.cash_cents < buffer).cloned();
    let checkpoints = [30, 60, 90].into_iter().filter(|day| *day <= days).map(|day| json!({"day":day,"date":points[day as usize].date,"cash_cents":points[day as usize].cash_cents})).collect();
    Forecast {
        funding_needed_cents: (buffer - minimum.cash_cents).max(0),
        closing_cash_cents: cash,
        minimum,
        first_shortfall,
        points,
        checkpoints,
        buffer_cents: buffer,
        horizon_days: days,
    }
}

#[derive(Clone, Default, Serialize)]
struct Changes {
    collection_days: i64,
    spend_reduction_pct: f64,
    funding_cents: i64,
    growth_pct: f64,
}

#[derive(Clone, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Business {
    pub monthly_revenue_cents: i64,
    pub gross_margin_pct: f64,
    pub collection_days: i64,
}
#[derive(Clone, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Goal {
    pub metric: String,
    pub target_cents: i64,
    pub deadline: NaiveDate,
    pub cash_floor_cents: i64,
    pub max_collection_days: i64,
    pub max_spend_reduction_pct: f64,
    pub max_funding_cents: i64,
    pub max_growth_pct: f64,
    pub business: Option<Business>,
}

fn validate_goal(company: &Company, goal: &Goal) -> ApiResult<i64> {
    let days = (goal.deadline - company.assessment_date).num_days();
    if !(7..=180).contains(&days) {
        return Err(ApiError::bad(
            "Choose a deadline 7–180 days after the assessment.",
        ));
    }
    if !matches!(
        goal.metric.as_str(),
        "min_cash" | "ending_cash" | "monthly_revenue"
    ) {
        return Err(ApiError::bad(
            "This metric needs additional inputs and is not supported yet.",
        ));
    }
    if [
        goal.target_cents,
        goal.cash_floor_cents,
        goal.max_funding_cents,
    ]
    .iter()
    .any(|v| !(0..=MAX_MONEY).contains(v))
        || !(0..=45).contains(&goal.max_collection_days)
        || !goal.max_spend_reduction_pct.is_finite()
        || !(0.0..=30.0).contains(&goal.max_spend_reduction_pct)
        || !goal.max_growth_pct.is_finite()
        || !(0.0..=30.0).contains(&goal.max_growth_pct)
    {
        return Err(ApiError::bad(
            "Enter non-negative amounts, up to 45 collection days, and percentages from 0 to 30.",
        ));
    }
    if goal.metric == "monthly_revenue" {
        let business = goal.business.as_ref().ok_or_else(|| {
            ApiError::bad(
                "Revenue goals need starting monthly revenue, gross margin, and collection timing.",
            )
        })?;
        if days < 30
            || !(1..=MAX_MONEY).contains(&business.monthly_revenue_cents)
            || !business.gross_margin_pct.is_finite()
            || !(0.0..=100.0).contains(&business.gross_margin_pct)
            || !(0..=90).contains(&business.collection_days)
        {
            return Err(ApiError::bad(
                "Revenue goals need at least 30 days, positive revenue, margin 0–100%, and collection timing 0–90 days.",
            ));
        }
    } else if goal.business.is_some() {
        return Err(ApiError::bad(
            "Business assumptions apply to revenue goals only.",
        ));
    }
    Ok(days)
}

pub fn compare(company: &Company, goal: &Goal) -> ApiResult<Value> {
    let days = validate_goal(company, goal)?;
    let baseline = forecast(
        company,
        days,
        goal.cash_floor_cents,
        &Changes::default(),
        None,
    );
    let candidates = [
        (
            "collections",
            "Collect earlier",
            Changes {
                collection_days: goal.max_collection_days,
                growth_pct: goal.max_growth_pct / 2.0,
                ..Changes::default()
            },
            "Earlier collections depend on customer agreement. Growth requires the entered revenue assumptions.",
        ),
        (
            "balanced",
            "Combine cash levers",
            Changes {
                collection_days: goal.max_collection_days,
                spend_reduction_pct: goal.max_spend_reduction_pct,
                funding_cents: goal.max_funding_cents,
                growth_pct: goal.max_growth_pct,
            },
            "Spending cuts apply only to discretionary costs. Funding is uncommitted, assumed on day one at 8% annual interest, with principal due after this horizon. No retention benefit is assumed.",
        ),
    ];
    let outcome = |projection: &Forecast, growth: f64| -> i64 {
        match goal.metric.as_str() {
            "min_cash" => projection.minimum.cash_cents,
            "ending_cash" => projection.closing_cash_cents,
            _ => (goal.business.as_ref().unwrap().monthly_revenue_cents as f64
                * (1.0 + growth / 100.0).powi((days / 30) as i32))
            .round() as i64,
        }
    };
    let plans: Vec<Value> = candidates.into_iter().map(|(id, title, mut changes, trade_off)| {
        if goal.business.is_none() { changes.growth_pct = 0.0; }
        let projection = forecast(company, days, goal.cash_floor_cents, &changes, goal.business.as_ref());
        let value = outcome(&projection, changes.growth_pct);
        let target_met = value >= goal.target_cents;
        let floor_met = projection.minimum.cash_cents >= goal.cash_floor_cents;
        json!({"id":id,"title":title,"changes":changes,"value_cents":value,"target_met":target_met,"cash_floor_met":floor_met,"qualifies":target_met && floor_met,"remaining_target_cents":(goal.target_cents-value).max(0),"remaining_cash_cents":projection.funding_needed_cents,"cash_impact_cents":projection.minimum.cash_cents-baseline.minimum.cash_cents,"forecast":projection,"trade_off":trade_off})
    }).collect();
    let any = plans.iter().any(|p| p["qualifies"] == true);
    Ok(
        json!({"goal":goal,"baseline_value_cents":outcome(&baseline,0.0),"baseline":baseline,"plans":plans,"any_qualifies":any,"explanation":if any {"A tested plan meets the target and daily cash floor under these assumptions."} else {"Neither tested plan meets every condition. Review the target gap and the cash shortfall for each plan; other combinations may work."},"assumptions":["Daily closing cash; intraday ordering is not modelled.","Source records, observed score and the baseline are unchanged.","Overdue open items are assumed to settle tomorrow; confirm actual timing.","All amounts are in the company's reporting currency; no FX conversion is inferred.","Growth is incremental to baseline cash; costs are paid at each 30-day period end, receipts after the entered collection delay. No churn model is inferred."]}),
    )
}

#[derive(Deserialize)]
pub struct Horizon {
    pub days: Option<i64>,
    pub buffer_cents: Option<i64>,
}

pub async fn companies(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    let identity = auth::identity(&state, &user).await?;
    Ok(Json(json!(state.companies.iter().filter(|c| identity.company_ids.contains(&c.id)).map(|c| json!({"id":c.id,"name":c.name,"group":c.group,"currency":c.currency,"data_mode":c.data_mode})).collect::<Vec<_>>())))
}
pub async fn assessment(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(query): Query<Horizon>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    auth::company_access(&state, &user, &id).await?;
    let company = state
        .companies
        .iter()
        .find(|c| c.id == id)
        .ok_or_else(|| ApiError::bad("Assessment not available for this company."))?;
    Ok(Json(assess(
        company,
        query.days.unwrap_or(90),
        query.buffer_cents.unwrap_or(company.buffer_cents),
    )?))
}

pub fn assess(company: &Company, days: i64, buffer: i64) -> ApiResult<Value> {
    if !(7..=180).contains(&days) || !(0..=MAX_MONEY).contains(&buffer) {
        return Err(ApiError::bad(
            "Use a 7–180 day horizon and a non-negative buffer.",
        ));
    }
    let mut snapshot = company.clone();
    snapshot
        .flows
        .retain(|flow| flow.known_on <= company.assessment_date);
    Ok(
        json!({"company":snapshot,"forecast":forecast(company,days,buffer,&Changes::default(),None)}),
    )
}

pub async fn plans(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(goal): Json<Goal>,
) -> ApiResult<Json<Value>> {
    let user = auth::browser_user(&state, &headers).await?;
    auth::company_access(&state, &user, &id).await?;
    let company = state
        .companies
        .iter()
        .find(|c| c.id == id)
        .ok_or_else(|| ApiError::bad("Assessment not available for this company."))?;
    Ok(Json(compare(company, &goal)?))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn company() -> Company {
        load(include_str!("../../../fixtures/companies.json"))
            .unwrap()
            .remove(0)
    }
    #[test]
    fn dated_health_snapshots_are_preserved_and_validated() {
        let c = company();
        let payload = assess(&c, 90, c.buffer_cents).unwrap();
        assert_eq!(
            payload["company"]["health_assessments"][4]["health"]["score"],
            58.0
        );
        assert_eq!(
            payload["company"]["health_assessments"][4]["drivers"][0]["points"],
            -1.0
        );
        let input = serde_json::to_value(vec![c]).unwrap();
        for (path, value) in [
            ("/0/health_assessments/0/health/score", json!(101)),
            ("/0/health_assessments/0/date", json!("2026-09-01")),
            (
                "/0/health_assessments/0/flows/0/known_on",
                json!("2026-06-03"),
            ),
            ("/0/health_assessments/0/flows/0/settled_cents", json!(-1)),
            (
                "/0/health_assessments/0/coverage/0/as_of",
                json!("2026-06-03"),
            ),
            ("/0/health_assessments/0/drivers/0/points", json!(101)),
        ] {
            let mut invalid = input.clone();
            *invalid.pointer_mut(path).unwrap() = value;
            assert!(load(&invalid.to_string()).is_err(), "Accepted {path}");
        }
        let mut duplicate = input.clone();
        duplicate[0]["health_assessments"][1]["date"] =
            duplicate[0]["health_assessments"][0]["date"].clone();
        assert!(load(&duplicate.to_string()).is_err());
        let mut legacy = input;
        legacy[0]
            .as_object_mut()
            .unwrap()
            .remove("health_assessments");
        assert!(
            load(&legacy.to_string()).unwrap()[0]
                .health_assessments
                .is_empty()
        );
    }
    #[test]
    fn early_shortfalls_survive_later_receipts_and_future_knowledge_is_excluded() {
        let mut c = company();
        c.flows.truncate(1);
        let initial = forecast(&c, 90, 0, &Changes::default(), None);
        assert_eq!(initial.funding_needed_cents, 200_000_000);
        c.flows.push(Flow {
            id: "later".into(),
            amount_cents: 300_000_000,
            date: c.assessment_date + Duration::days(40),
            ..c.flows[0].clone()
        });
        let later = forecast(&c, 90, 0, &Changes::default(), None);
        assert!(later.closing_cash_cents > 0);
        assert_eq!(later.minimum.cash_cents, -200_000_000);
        c.flows[1].known_on = c.assessment_date + Duration::days(1);
        assert_eq!(
            forecast(&c, 90, 0, &Changes::default(), None).closing_cash_cents,
            initial.closing_cash_cents
        );
        c.flows[0].settled_cents = 100_000_000;
        assert_eq!(
            forecast(&c, 90, 0, &Changes::default(), None).funding_needed_cents,
            100_000_000
        );
    }
    #[test]
    fn plans_enforce_the_whole_path_and_preserve_baseline_and_health() {
        let c = company();
        let before = serde_json::to_value(&c).unwrap();
        let mut goal = Goal {
            metric: "ending_cash".into(),
            target_cents: 0,
            deadline: c.assessment_date + Duration::days(90),
            cash_floor_cents: 10_000_000,
            max_collection_days: 0,
            max_spend_reduction_pct: 0.0,
            max_funding_cents: 0,
            max_growth_pct: 0.0,
            business: None,
        };
        let result = compare(&c, &goal).unwrap();
        assert_eq!(result["plans"][0]["target_met"], true);
        assert_eq!(result["plans"][0]["cash_floor_met"], false);
        assert_eq!(result["any_qualifies"], false);
        goal.max_funding_cents = 250_000_000;
        assert_eq!(compare(&c, &goal).unwrap()["plans"][1]["qualifies"], true);
        assert_eq!(serde_json::to_value(&c).unwrap(), before);
        goal.metric = "monthly_revenue".into();
        assert!(compare(&c, &goal).is_err());
        goal.business = Some(Business {
            monthly_revenue_cents: 100_000_000,
            gross_margin_pct: 60.0,
            collection_days: 30,
        });
        goal.max_growth_pct = 10.0;
        assert_eq!(
            compare(&c, &goal).unwrap()["plans"][1]["value_cents"],
            133_100_000
        );
        goal.max_growth_pct = 100.0;
        assert!(compare(&c, &goal).is_err());
    }
}
