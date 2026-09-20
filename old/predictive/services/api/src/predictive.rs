//! The selected JSON persistence models, evaluated against verified published inputs.
//! Raw-snapshot assessment remains in Python; this path serves the imported dataset.
use anyhow::{Context, ensure};
use chrono::{Datelike, Months, NaiveDate, Utc};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::SqliteConnection;
use std::{collections::BTreeMap, path::Path, sync::LazyLock};

pub const DIRECTORY: &str = "reports/modeling/financial-v3/dataset-v1/model-v1";
pub const MANIFEST_SHA256: &str =
    "71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428";
const MODEL_BYTES: &[u8] =
    include_bytes!("../../../reports/modeling/financial-v3/dataset-v1/model-v1/models.json");
const INDEX_BYTES: &[u8] =
    include_bytes!("../../../reports/modeling/financial-v3/dataset-v1/model-v1/index.json");
const MODEL_SHA256: &str = "4e73d095bcb6929afbaf8458e0b0707953d8e3d0fbf0d3ffc95399b8a461b550";
const INDEX_SHA256: &str = "0cb8b2129e90fcdc61ef4799bbf879da400ff859a9f5afd5fc6f7527f0ba0016";
const TARGETS: [&str; 3] = [
    "receipt_contraction_3m",
    "receipt_expansion_3m",
    "current_receipt_dip_3m",
];
static MODELS: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_slice(MODEL_BYTES).expect("compiled model JSON"));
static INDEX: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_slice(INDEX_BYTES).expect("compiled model index"));

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub fn verify_embedded() -> anyhow::Result<()> {
    ensure!(
        hash(MODEL_BYTES) == MODEL_SHA256 && hash(INDEX_BYTES) == INDEX_SHA256,
        "Predictive artifact mismatch"
    );
    ensure!(MODELS[TARGETS[2]].is_null(), "Unsupported dip estimator");
    for target in &TARGETS[..2] {
        ensure!(
            MODELS[target]["candidate"] == "persistence" && MODELS[target]["target"] == *target,
            "Unsupported predictive estimator"
        );
    }
    Ok(())
}

pub fn source_identity(root: &Path) -> anyhow::Result<Option<&'static str>> {
    let directory = root.join(DIRECTORY);
    if !directory.exists() {
        return Ok(None);
    }
    verify_embedded()?;
    ensure!(
        hash(&std::fs::read(directory.join("manifest.json"))?) == MANIFEST_SHA256,
        "Predictive source manifest mismatch"
    );
    ensure!(
        std::fs::read(directory.join("models.json"))? == MODEL_BYTES
            && std::fs::read(directory.join("index.json"))? == INDEX_BYTES,
        "Predictive source mismatch"
    );
    Ok(Some(MANIFEST_SHA256))
}

fn input(profile: &Value, point: &Value, profile_hash: &str) -> Value {
    json!({"company_id":profile["company_id"], "group_id":profile["group_id"],
        "as_of":point["as_of"], "features":point["features"], "input_reasons":point["input_reasons"],
        "source_profile_sha256":profile_hash})
}

pub async fn import(
    root: &Path,
    db: &mut SqliteConnection,
    companies: &BTreeMap<String, String>,
) -> anyhow::Result<usize> {
    let mut count = 0;
    for entry in INDEX["companies"]
        .as_array()
        .context("Missing predictive company index")?
    {
        let company = entry["company_id"]
            .as_str()
            .context("Missing predictive company")?;
        let path = format!("profiles/{company}.json");
        ensure!(
            entry["path"] == path
                && company.starts_with("COMP_")
                && company[5..].bytes().all(|b| b.is_ascii_digit()),
            "Invalid predictive profile path"
        );
        let bytes = std::fs::read(root.join(DIRECTORY).join(path))?;
        let digest = hash(&bytes);
        ensure!(
            entry["sha256"] == digest,
            "Predictive profile hash mismatch: {company}"
        );
        let profile: Value = serde_json::from_slice(&bytes)?;
        ensure!(
            profile["company_id"] == company
                && profile["group_id"] == entry["group_id"]
                && companies
                    .get(company)
                    .is_some_and(|group| profile["group_id"] == *group),
            "Predictive company/group mismatch"
        );
        let mut insert = sqlx::QueryBuilder::new("INSERT INTO parquet_records VALUES ");
        let mut rows = insert.separated(",");
        for point in profile["points"]
            .as_array()
            .context("Missing predictive points")?
        {
            let date = NaiveDate::parse_from_str(
                point["as_of"].as_str().context("Missing prediction date")?,
                "%Y-%m-%d",
            )?;
            ensure!(
                date.succ_opt().is_some_and(|next| next.day() == 1),
                "Prediction requires month end"
            );
            let period = date.with_day(1).unwrap().to_string();
            let snapshot = input(&profile, point, &digest);
            forecast(
                company,
                entry["group_id"].as_str().unwrap(),
                &date.to_string(),
                Some(&snapshot),
            )?;
            rows.push("(")
                .push_bind_unseparated("predictive_inputs")
                .push_unseparated(",")
                .push_bind_unseparated(format!("{company}:{period}"))
                .push_unseparated(",")
                .push_bind_unseparated(company)
                .push_unseparated(",")
                .push_bind_unseparated(period)
                .push_unseparated(",")
                .push_bind_unseparated(snapshot.to_string())
                .push_unseparated(")");
            count += 1;
        }
        insert.build().execute(&mut *db).await?;
    }
    Ok(count)
}

fn predict(model: &Value, features: &Value) -> Value {
    let state = match (
        features["receipts_known"].as_f64(),
        features["receipts_known_mean3"].as_f64(),
    ) {
        (Some(current), Some(mean)) if mean > 0.0 => {
            if current <= 0.8 * mean {
                "low"
            } else if current >= 1.2 * mean {
                "high"
            } else {
                "middle"
            }
        }
        _ => "unknown",
    };
    model["states"]
        .get(state)
        .unwrap_or(&model["prevalence"])
        .clone()
}

pub fn forecast(
    company: &str,
    group: &str,
    as_of: &str,
    snapshot: Option<&Value>,
) -> anyhow::Result<Value> {
    let date = NaiveDate::parse_from_str(as_of, "%Y-%m-%d")?;
    ensure!(
        date.succ_opt().is_some_and(|next| next.day() == 1),
        "Invalid predictive cutoff"
    );
    let month = date.with_day(1).unwrap();
    let horizon_start = month
        .checked_add_months(Months::new(1))
        .context("Invalid horizon")?;
    let horizon_end = month
        .checked_add_months(Months::new(4))
        .and_then(|day| day.pred_opt())
        .context("Invalid horizon")?;
    let mut reasons = Vec::<String>::new();
    let mut features = &Value::Null;
    if let Some(snapshot) = snapshot {
        ensure!(
            snapshot["company_id"] == company
                && snapshot["group_id"] == group
                && snapshot["as_of"] == as_of,
            "Predictive input identity mismatch"
        );
        for reason in snapshot["input_reasons"]
            .as_array()
            .context("Missing input reasons")?
        {
            reasons.push(reason.as_str().context("Invalid input reason")?.to_owned());
        }
        features = &snapshot["features"];
        if !features.is_null() {
            let names = MODELS[TARGETS[0]]["feature_order"].as_array().unwrap();
            let values = features
                .as_object()
                .context("Invalid predictive features")?;
            ensure!(
                values.len() == names.len()
                    && names
                        .iter()
                        .all(|name| values.contains_key(name.as_str().unwrap()))
                    && values
                        .values()
                        .all(|value| value.is_null() || value.as_f64().is_some_and(f64::is_finite)),
                "Invalid predictive feature contract"
            );
        }
    }
    if features.is_null() {
        reasons.push("predictive_inputs_unavailable".into());
    }
    if INDEX["excluded_product_only"]
        .as_array()
        .unwrap()
        .iter()
        .any(|row| row["company_id"] == company || row["group_id"] == group)
    {
        reasons.push("original_benchmark_excluded".into());
    }
    if date >= Utc::now().date_naive().with_day(1).unwrap() {
        reasons.push("month_not_closed".into());
    }
    let mut predictions = serde_json::Map::new();
    for target in TARGETS {
        let model = &MODELS[target];
        let mut reasons = reasons.clone();
        if model.is_null() {
            reasons.push("insufficient_training_support_or_no_selected_model".into());
        } else if as_of <= model["fit_label_cutoff"].as_str().unwrap() {
            reasons.push("before_selection_cutoff_no_backcast".into());
        }
        reasons.sort();
        reasons.dedup();
        let probabilities = if reasons.is_empty() {
            predict(model, features)
        } else {
            Value::Null
        };
        predictions.insert(
            target.into(),
            json!({"probabilities":probabilities,"reasons":reasons}),
        );
    }
    Ok(
        json!({"schema_version":"receipt-outlook-1", "model_version":"predictive-model-1",
        "implementation":"rust-persistence-1", "as_of":as_of,
        "horizon_start":horizon_start, "horizon_end":horizon_end,
        "view":"reconstructed_retrospective", "calibration":"uncalibrated_internal_retrospective",
        "scope":"classified_operating_receipts_not_solvency", "automatic_alerts":false,
        "source_manifest_sha256":MANIFEST_SHA256, "models_sha256":MODEL_SHA256,
        "input_sha256":snapshot.map(|value| hash(value.to_string().as_bytes())),
        "predictions":predictions}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn published_predictions_and_abstentions_match() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        assert_eq!(source_identity(&root).unwrap(), Some(MANIFEST_SHA256));
        let mut matched = 0;
        for entry in INDEX["companies"].as_array().unwrap() {
            let bytes =
                std::fs::read(root.join(DIRECTORY).join(entry["path"].as_str().unwrap())).unwrap();
            assert_eq!(hash(&bytes), entry["sha256"]);
            let profile: Value = serde_json::from_slice(&bytes).unwrap();
            for point in profile["points"].as_array().unwrap() {
                let actual = forecast(
                    profile["company_id"].as_str().unwrap(),
                    profile["group_id"].as_str().unwrap(),
                    point["as_of"].as_str().unwrap(),
                    Some(&input(&profile, point, entry["sha256"].as_str().unwrap())),
                )
                .unwrap();
                for target in TARGETS {
                    let expected = &point["forecast"][target]["probabilities"];
                    let result = &actual["predictions"][target]["probabilities"];
                    assert_eq!(
                        result.is_null(),
                        expected.is_null(),
                        "{} {} {target}",
                        profile["company_id"],
                        point["as_of"]
                    );
                    if !expected.is_null() {
                        for class in ["0", "1"] {
                            assert!(
                                (result[class].as_f64().unwrap()
                                    - expected[class].as_f64().unwrap())
                                .abs()
                                    <= 1e-12
                            );
                        }
                        matched += 1;
                    }
                }
            }
        }
        assert_eq!(matched, 12578);
    }

    #[test]
    fn boundaries_exclusions_and_identity_are_preserved() {
        verify_embedded().unwrap();
        let model = &MODELS[TARGETS[0]];
        for (value, state) in [
            (80.0, "low"),
            (80.001, "middle"),
            (119.999, "middle"),
            (120.0, "high"),
        ] {
            assert_eq!(
                predict(
                    model,
                    &json!({"receipts_known":value,"receipts_known_mean3":100})
                ),
                model["states"][state]
            );
        }
        assert_eq!(predict(model, &Value::Null), model["prevalence"]);
        let excluded = &INDEX["excluded_product_only"][0];
        let result = forecast(
            excluded["company_id"].as_str().unwrap(),
            excluded["group_id"].as_str().unwrap(),
            "2026-08-31",
            None,
        )
        .unwrap();
        assert_eq!(result["horizon_start"], "2026-09-01");
        assert_eq!(result["horizon_end"], "2026-11-30");
        assert!(result["predictions"][TARGETS[0]]["probabilities"].is_null());
        assert!(
            result["predictions"][TARGETS[0]]["reasons"]
                .as_array()
                .unwrap()
                .contains(&json!("original_benchmark_excluded"))
        );
        assert!(forecast("a", "b", "2026-08-31", Some(&json!({"company_id":"other"}))).is_err());
        assert!(forecast("a", "b", "2026-08-30", None).is_err());
    }
}
