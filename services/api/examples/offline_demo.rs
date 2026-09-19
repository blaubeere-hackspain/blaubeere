use blaubeere_api::finance::{Goal, assess, compare, load};
use chrono::Duration;
use serde_json::{Value, json};

// Only bundled synthetic data can enter the public demo; never load ASSESSMENT_FILE.
fn snapshot() -> Value {
    let company = load(include_str!("../../../fixtures/companies.json"))
        .unwrap()
        .remove(0);
    assert_eq!(company.id, "DEMO_001");
    assert_eq!(company.data_mode, "demo");
    let goal = Goal {
        metric: "min_cash".into(),
        target_cents: company.buffer_cents,
        deadline: company.assessment_date + Duration::days(90),
        cash_floor_cents: company.buffer_cents,
        max_collection_days: 14,
        max_spend_reduction_pct: 10.0,
        max_funding_cents: 250_000_000,
        max_growth_pct: 0.0,
        business: None,
    };
    let forecasts: serde_json::Map<String, Value> = [30, 60, 90, 180]
        .into_iter()
        .map(|days| {
            (
                days.to_string(),
                assess(&company, days, company.buffer_cents).unwrap()["forecast"].take(),
            )
        })
        .collect();
    json!({
        "company": assess(&company, 90, company.buffer_cents).unwrap()["company"],
        "forecasts": forecasts,
        "comparison": compare(&company, &goal).unwrap(),
    })
}

fn main() {
    println!("{}", snapshot());
}

#[test]
fn public_demo_matches_the_finance_model() {
    let bundled: Value =
        serde_json::from_str(include_str!("../../../apps/app/lib/demo.json")).unwrap();
    assert_eq!(
        bundled,
        snapshot(),
        "Regenerate with cargo run -p blaubeere-api --example offline_demo > apps/app/lib/demo.json"
    );
}
