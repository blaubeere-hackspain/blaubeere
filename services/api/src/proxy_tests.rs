use crate::finance::{self, Company, Goal};
use serde_json::{Value, json};

fn fixture() -> Value {
    serde_json::from_str(r#"{
        "kind":"proxy_only", "id":"COMP_0001", "name":"Synthetic company COMP_0001",
        "group":"GROUP_0147", "currency":"EUR", "assessment_date":"2026-08-31",
        "model_version":"experiment-v1:hgb_leaf15", "history_mode":"reconstructed", "data_mode":"synthetic",
        "opening_cash_cents":null, "buffer_cents":null, "health":null, "history":[], "drivers":[], "coverage":[], "flows":[],
        "predictive": {
            "target":"target_deficit_3m", "definition":"Operating deficit in at least two of the next three calendar months.",
            "as_of":"2026-08-31", "horizon_months":3, "horizon_start":"2026-09-01", "horizon_end":"2026-11-30",
            "probability_estimate":0.5675128970680213, "accepted":true, "eligible":true, "unavailable_reasons":[],
            "coverage":{"eligibility_reasons":[], "valid_months_3m":3, "valid_months_6m":6, "n_sin_eur_current":0},
            "observed_features":{"net_ratio":0.15804897560333453}, "missing_reason":{},
            "feature_descriptions":{"net_ratio":"neto_caja_eur / gross"},
            "model_version":"experiment-v1:hgb_leaf15", "calibrated":false,
            "scope":"Synthetic retrospective proxy, not calibrated, default or health", "currency_basis":"Analytical EUR, not native reporting currency",
            "validation":{
                "labeled_rows":131,"prospective_rows":299,"groups":31,
                "average_precision":0.8156032515457942,"prevalence":0.5114503816793893,
                "brier":0.1884291793355566,"baseline_brier":0.25172915660467343,"relative_brier_skill":0.2514606497034664,
                "relative_brier_skill_ci95":[0.12025993468083153,0.3807192648045509],
                "interval_method":"percentile_2.5_97.5_conditional_on_fixed_fit","interval_sampling_unit":"group_id_with_replacement_all_rows",
                "limits":["Validation conditional on observed labels; not calibrated."]
            },
            "provenance":{
                "latest_predictions_sha256":"ec6b3d3f813238412420a0627dabf56e1ee7ef38111f92aadb2415d98a770cec",
                "model_manifest_sha256":"9b30c24feeb75fb0c2f17bca9a2eb178a508dd3e24759ac53c713424dd531dc8",
                "final_report_sha256":"2b248e2c798e869ca41d540a601662ee9dcbad83d3b0dd30d813e245d786c9a2"
            }
        }
    }"#).unwrap()
}

fn load(value: Value) -> anyhow::Result<Vec<Company>> {
    finance::load(&json!([value]).to_string())
}

fn goal(c: &Company) -> Goal {
    Goal {
        metric: "ending_cash".into(),
        target_cents: 0,
        deadline: c.assessment_date + chrono::Duration::days(90),
        cash_floor_cents: 0,
        max_collection_days: 0,
        max_spend_reduction_pct: 0.0,
        max_funding_cents: 0,
        max_growth_pct: 0.0,
        business: None,
    }
}

fn assert_no_cash(c: &Company) {
    for (days, buffer) in [(90, None), (180, Some(0)), (7, Some(1_000_000))] {
        let assessment = finance::assess(c, days, buffer).unwrap();
        assert!(assessment["forecast"].is_null());
        assert_eq!(assessment["cash_planning_available"], false);
        assert!(
            assessment["cash_planning_reason"]
                .as_str()
                .unwrap()
                .contains("unknown")
        );
        assert!(assessment["company"]["health"].is_null());
    }
    assert!(
        finance::compare(c, &goal(c))
            .unwrap_err()
            .1
            .contains("Cash planning is unavailable")
    );
}

#[test]
fn valid_proxy_and_abstention_never_produce_cash() {
    let c = load(fixture()).unwrap().remove(0);
    assert_no_cash(&c);
    let mut unavailable = fixture();
    unavailable["predictive"]["eligible"] = json!(false);
    unavailable["predictive"]["probability_estimate"] = Value::Null;
    unavailable["predictive"]["unavailable_reasons"] = json!(["no_cash_activity"]);
    unavailable["predictive"]["coverage"]["eligibility_reasons"] = json!(["no_cash_activity"]);
    assert_no_cash(&load(unavailable).unwrap().remove(0));
    let mut rejected = fixture();
    rejected["predictive"]["accepted"] = json!(false);
    rejected["predictive"]["probability_estimate"] = Value::Null;
    rejected["predictive"]["unavailable_reasons"] = json!(["model_not_accepted"]);
    assert_no_cash(&load(rejected).unwrap().remove(0));
}

#[test]
fn inconsistent_proxy_payloads_fail_closed() {
    for (pointer, value) in [
        ("/opening_cash_cents", json!(0)),
        ("/buffer_cents", json!(0)),
        ("/health", json!({"score":43})),
        ("/history", json!([{"cash_cents":0}])),
        ("/drivers", json!([{"points":0}])),
        ("/flows", json!([{}])),
        ("/kind", json!("cash")),
        ("/predictive", Value::Null),
        ("/predictive/eligible", json!(false)),
        ("/predictive/accepted", json!(false)),
        ("/predictive/probability_estimate", json!(-0.01)),
        ("/predictive/probability_estimate", json!(1.01)),
        ("/predictive/probability_estimate", Value::Null),
        ("/predictive/probability_estimate", json!("NaN")),
        ("/predictive/target", json!("health")),
        ("/predictive/calibrated", json!(true)),
        ("/predictive/horizon_months", json!(90)),
        ("/predictive/horizon_end", json!("2026-11-29")),
        ("/predictive/as_of", json!("2026-08-30")),
        ("/predictive/model_version", json!("other")),
        ("/predictive/observed_features/net_ratio", Value::Null),
        ("/predictive/coverage/valid_months_3m", json!(2)),
    ] {
        let mut value_to_load = fixture();
        *value_to_load.pointer_mut(pointer).unwrap() = value;
        assert!(
            load(value_to_load).is_err(),
            "Accepted invalid field: {pointer}"
        );
    }
    let mut value = fixture();
    value["known_on"] = json!("2026-08-31");
    assert!(load(value).is_err());
    let mut value = fixture();
    value["predictive"]["known_on"] = json!("2026-08-31");
    assert!(load(value).is_err());
    let serialized = json!([fixture()]).to_string();
    assert!(finance::load(&serialized.replace("0.5675128970680213", "1e999")).is_err());
    for edge in [0.0, 1.0] {
        let mut value = fixture();
        value["predictive"]["probability_estimate"] = json!(edge);
        assert!(load(value).is_ok());
    }
}

#[test]
fn proxy_numbers_preserve_ieee754_roundtrip() {
    let mut value = fixture();
    value["predictive"]["probability_estimate"] = json!(0.43328044815789507_f64);
    value["predictive"]["observed_features"]["net_ratio"] = json!(12.088171884194395_f64);
    let c = load(value).unwrap().remove(0);
    let p = c.predictive.unwrap();
    assert_eq!(
        p.probability_estimate.unwrap().to_bits(),
        0.43328044815789507_f64.to_bits()
    );
    assert_eq!(
        p.observed_features["net_ratio"].unwrap().to_bits(),
        12.088171884194395_f64.to_bits()
    );
}

#[test]
fn old_demo_retains_cash_forecast_and_planning() {
    let c = finance::load(include_str!("../../../fixtures/companies.json"))
        .unwrap()
        .remove(0);
    let assessment = finance::assess(&c, 90, None).unwrap();
    assert_eq!(assessment["cash_planning_available"], true);
    assert_eq!(assessment["forecast"]["funding_needed_cents"], 210_000_000);
    assert!(finance::compare(&c, &goal(&c)).is_ok());
    let mut missing = serde_json::to_value(c).unwrap();
    missing["opening_cash_cents"] = Value::Null;
    assert!(load(missing).is_err());
}

#[test]
#[ignore = "requires explicit EXPORTED_ASSESSMENT_FILE generated from sealed predictions"]
fn actual_export_loads_all_1286_companies() {
    let path = std::env::var("EXPORTED_ASSESSMENT_FILE")
        .expect("Pass the actual exported companies.json path");
    let companies = finance::load(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(companies.len(), 1286);
    assert_eq!(
        companies
            .iter()
            .filter(|c| c
                .predictive
                .as_ref()
                .unwrap()
                .probability_estimate
                .is_some())
            .count(),
        1010
    );
    for c in companies {
        assert_no_cash(&c);
    }
}
