//! Import the published 30/60/90-day invoice forecast in the existing API snapshot.
use anyhow::{Context, ensure};
use chrono::{Datelike, Duration, NaiveDate};
use parquet::file::reader::{FileReader, SerializedFileReader};
use serde_json::{Value, json};
use sqlx::{Sqlite, Transaction};
use std::{collections::BTreeMap, fs::File, path::Path};

pub const VERSION: &str = "cashflow_projection_v1";
pub const SOURCES: [(&str, &str); 2] = [
    (
        "cash_projection",
        "reports/cashflow_projection/horizontes.parquet",
    ),
    (
        "projection_metadata",
        "reports/cashflow_projection/metricas.json",
    ),
];

fn amount(row: &Value, key: &str) -> anyhow::Result<Option<f64>> {
    let value = row
        .get(key)
        .with_context(|| format!("Missing projection amount: {key}"))?;
    if value.is_null() {
        return Ok(None);
    }
    Ok(Some(
        value
            .as_f64()
            .filter(|v| v.is_finite())
            .context("Invalid projection amount")?,
    ))
}
fn reconciles(actual: Option<f64>, left: Option<f64>, right: Option<f64>) -> bool {
    match (actual, left.zip(right)) {
        (None, None) => true,
        (Some(actual), Some((left, right))) => {
            (actual - left - right).abs() <= 0.01 + actual.abs() * 1e-10
        }
        _ => false,
    }
}

pub async fn import(root: &Path, tx: &mut Transaction<'_, Sqlite>) -> anyhow::Result<[usize; 2]> {
    let metadata: Value = serde_json::from_slice(&std::fs::read(root.join(SOURCES[1].1))?)?;
    ensure!(
        metadata["version"] == VERSION,
        "Unexpected cash projection version"
    );
    let mut projections: BTreeMap<(String, NaiveDate), Value> = BTreeMap::new();
    let reader = SerializedFileReader::new(File::open(root.join(SOURCES[0].1))?)?;
    let mut count = 0;
    for record in reader.get_row_iter(None)? {
        let mut row = record?.to_json_value();
        let id = row["company_id"]
            .as_str()
            .filter(|s| !s.is_empty())
            .context("Missing projection company")?
            .to_owned();
        let cutoff = NaiveDate::parse_from_str(
            row["corte"]
                .as_str()
                .and_then(|s| s.get(..10))
                .context("Missing projection cutoff")?,
            "%Y-%m-%d",
        )?;
        ensure!(
            cutoff.succ_opt().is_some_and(|d| d.day() == 1),
            "Projection cutoff must be month end"
        );
        let horizon = row["h"].as_i64().context("Invalid projection horizon")?;
        ensure!(
            [30, 60, 90].contains(&horizon),
            "Unsupported projection horizon"
        );
        let incoming = amount(&row, "entrada_esperada_eur")?;
        let outgoing = amount(&row, "salida_esperada_eur")?;
        ensure!(
            incoming.is_none_or(|v| v >= 0.0) && outgoing.is_none_or(|v| v <= 0.0),
            "Projection cash signs changed"
        );
        let net = amount(&row, "flujo_neto_esperado_eur")?;
        let opening = amount(&row, "saldo_corte_eur")?;
        ensure!(
            reconciles(net, incoming, outgoing),
            "Projection net must preserve signed amounts and unknowns"
        );
        ensure!(
            reconciles(amount(&row, "saldo_proyectado_eur")?, opening, net),
            "Projection balance does not reconcile"
        );
        row["corte"] = json!(cutoff.to_string());
        row["date"] = json!((cutoff + Duration::days(horizon)).to_string());
        let projection = projections.entry((id, cutoff)).or_insert_with(
            || json!({"version":VERSION,"as_of":cutoff.to_string(),"currency":"EUR","horizons":[]}),
        );
        let horizons = projection["horizons"].as_array_mut().unwrap();
        ensure!(
            !horizons.iter().any(|h| h["h"] == row["h"]),
            "Duplicate projection horizon"
        );
        ensure!(
            horizons
                .first()
                .is_none_or(|h| h["saldo_corte_eur"] == row["saldo_corte_eur"]),
            "Inconsistent opening cash across horizons"
        );
        horizons.push(row);
        count += 1;
    }
    ensure!(count > 0, "Empty cash projection source");
    for ((company, cutoff), mut projection) in projections {
        let horizons = projection["horizons"].as_array_mut().unwrap();
        horizons.sort_by_key(|h| h["h"].as_i64().unwrap());
        ensure!(horizons.len() == 3, "Missing 30/60/90-day projection");
        let month = cutoff.format("%Y-%m-01").to_string();
        sqlx::query("INSERT INTO parquet_records VALUES ('cash_projection',?,?,?,?)")
            .bind(format!("{company}:{month}"))
            .bind(company)
            .bind(month)
            .bind(projection.to_string())
            .execute(&mut **tx)
            .await?;
    }
    Ok([count, 1])
}

#[test]
fn unknown_cash_is_not_zero_and_outflows_stay_signed() {
    assert!(reconciles(Some(70.0), Some(100.0), Some(-30.0)));
    assert!(!reconciles(Some(130.0), Some(100.0), Some(-30.0)));
    assert!(reconciles(None, None, Some(0.0)));
    assert!(!reconciles(Some(0.0), None, Some(0.0)));
    assert_eq!(amount(&json!({"cash":null}), "cash").unwrap(), None);
    assert!(amount(&json!({}), "cash").is_err());
}
