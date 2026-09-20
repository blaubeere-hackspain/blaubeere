//! Daily native-currency cash, built by the existing Parquet importer.
use anyhow::{Context, ensure};
use chrono::{Datelike, NaiveDate};
use parquet::file::reader::{FileReader, SerializedFileReader};
use serde::Serialize;
use serde_json::Value;
use sqlx::SqliteConnection;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    path::Path,
};

pub const SOURCES: [(&str, &str); 2] = [
    ("cash_transactions", "data/clean/transactions.parquet"),
    ("cash_balances", "data/clean/balances.parquet"),
];

#[derive(Default)]
struct Movement {
    income: f64,
    expense: f64,
    unknown: u32,
}
#[derive(Default)]
struct AccountCash {
    anchor: f64,
    anchor_unknown: bool,
    anchored_products: BTreeSet<String>,
    products: BTreeSet<String>,
    movements: BTreeMap<NaiveDate, Movement>,
}
#[derive(Serialize)]
struct Day {
    date: NaiveDate,
    income: Option<f64>,
    expense: Option<f64>,
    balance: Option<f64>,
    unknown_movements: u32,
}
#[derive(Serialize)]
struct Month {
    currency: String,
    anchor_date: NaiveDate,
    income: Option<f64>,
    expense: Option<f64>,
    closing_balance: Option<f64>,
    days: Vec<Day>,
}
fn text<'a>(row: &'a Value, key: &str) -> anyhow::Result<&'a str> {
    row[key]
        .as_str()
        .filter(|s| !s.is_empty())
        .with_context(|| format!("Missing daily-cash {key}"))
}
fn day(row: &Value, key: &str) -> anyhow::Result<NaiveDate> {
    Ok(NaiveDate::parse_from_str(
        text(row, key)?.get(..10).context("Invalid cash date")?,
        "%Y-%m-%d",
    )?)
}
fn amount(row: &Value, key: &str) -> Option<f64> {
    row[key].as_f64().filter(|v| v.is_finite())
}
fn is_cash(row: &Value) -> bool {
    row["product_source"] == "banking"
        && matches!(
            row["product_type"].as_str(),
            Some("checking" | "wallet" | "saving" | "tpv")
        )
}
fn key(row: &Value) -> anyhow::Result<(String, String)> {
    let currency = text(row, "product_currency")?;
    ensure!(
        currency.len() == 3 && currency.bytes().all(|c| c.is_ascii_uppercase()),
        "Invalid original currency"
    );
    Ok((text(row, "company_id")?.into(), currency.into()))
}
fn rounded(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

impl AccountCash {
    fn months(&self, currency: &str, first: NaiveDate, anchor_date: NaiveDate) -> Vec<Month> {
        // The 1 September snapshot is the closing balance of 31 August, before
        // September's transactions. Never sum balances in different currencies.
        let mut balance = (!self.anchor_unknown
            && !self.anchored_products.is_empty()
            && self.products.is_subset(&self.anchored_products))
        .then_some(self.anchor);
        let mut days = Vec::new();
        let mut date = anchor_date.pred_opt().unwrap();
        loop {
            let movement = self.movements.get(&date);
            let income = movement.map_or(0.0, |m| m.income);
            let expense = movement.map_or(0.0, |m| m.expense);
            let unknown = movement.map_or(0, |m| m.unknown);
            days.push(Day {
                date,
                income: (unknown == 0).then(|| rounded(income)),
                expense: (unknown == 0).then(|| rounded(expense)),
                balance: balance.map(rounded),
                unknown_movements: unknown,
            });
            balance = if unknown > 0 {
                None
            } else {
                balance.map(|value| value - income + expense)
            };
            if date == first {
                break;
            }
            date = date.pred_opt().unwrap();
        }
        days.reverse();
        let mut months: Vec<Month> = Vec::new();
        for day in days {
            if day.date.day() == 1 {
                months.push(Month {
                    currency: currency.into(),
                    anchor_date,
                    income: Some(0.0),
                    expense: Some(0.0),
                    closing_balance: None,
                    days: Vec::new(),
                });
            }
            let month = months.last_mut().unwrap();
            month.income = month.income.zip(day.income).map(|(a, b)| rounded(a + b));
            month.expense = month.expense.zip(day.expense).map(|(a, b)| rounded(a + b));
            month.closing_balance = day.balance;
            month.days.push(day);
        }
        months
    }
}

pub async fn import(root: &Path, db: &mut SqliteConnection) -> anyhow::Result<[i64; 2]> {
    let first = NaiveDate::from_ymd_opt(2025, 1, 1).unwrap();
    let mut accounts: BTreeMap<(String, String), AccountCash> = BTreeMap::new();
    let balances = SerializedFileReader::new(File::open(root.join(SOURCES[1].1))?)?;
    let mut anchor_date = None;
    for record in balances.get_row_iter(None)? {
        let row = record?.to_json_value();
        if row["is_snapshot_date"] != true || !is_cash(&row) {
            continue;
        }
        let date = day(&row, "date_ok")?;
        ensure!(
            date.day() == 1 && date > first && anchor_date.is_none_or(|previous| previous == date),
            "Cash anchors must share a month boundary"
        );
        anchor_date = Some(date);
        let account = accounts.entry(key(&row)?).or_default();
        ensure!(
            account
                .anchored_products
                .insert(text(&row, "product_id")?.into()),
            "Duplicate cash anchor"
        );
        match amount(&row, "balance_ok") {
            Some(value) => account.anchor += value,
            None => account.anchor_unknown = true,
        }
    }
    let anchor_date = anchor_date.context("No dated cash anchor")?;
    let transactions = SerializedFileReader::new(File::open(root.join(SOURCES[0].1))?)?;
    for record in transactions.get_row_iter(None)? {
        let row = record?.to_json_value();
        if row["is_booked"] != true || !is_cash(&row) {
            continue;
        }
        let date = day(&row, "date_ok")?;
        if date < first || date >= anchor_date {
            continue;
        }
        let account = accounts.entry(key(&row)?).or_default();
        account.products.insert(text(&row, "product_id")?.into());
        let movement = account.movements.entry(date).or_default();
        match amount(&row, "amount") {
            Some(value) if value >= 0.0 => movement.income += value,
            Some(value) => movement.expense -= value,
            None => movement.unknown += 1,
        }
    }
    let mut rows: BTreeMap<(String, NaiveDate), Vec<Month>> = BTreeMap::new();
    for ((company, currency), account) in accounts {
        for month in account.months(&currency, first, anchor_date) {
            rows.entry((company.clone(), month.days[0].date))
                .or_default()
                .push(month);
        }
    }
    for ((company, month), currencies) in rows {
        sqlx::query("INSERT INTO parquet_records VALUES ('daily_cash',?,?,?,?)")
            .bind(format!("{company}:{month}"))
            .bind(&company)
            .bind(month.to_string())
            .bind(serde_json::to_string(&currencies)?)
            .execute(&mut *db)
            .await?;
    }
    Ok([
        transactions.metadata().file_metadata().num_rows(),
        balances.metadata().file_metadata().num_rows(),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_cash_keeps_daily_signs_gaps_and_month_boundaries() {
        let first = NaiveDate::from_ymd_opt(2026, 8, 1).unwrap();
        let anchor = NaiveDate::from_ymd_opt(2026, 9, 1).unwrap();
        let mut cash = AccountCash {
            anchor: 100.0,
            anchored_products: BTreeSet::from(["a".into()]),
            products: BTreeSet::from(["a".into()]),
            ..Default::default()
        };
        cash.movements.insert(
            first,
            Movement {
                income: 200.0,
                expense: 150.0,
                unknown: 0,
            },
        );
        cash.movements.insert(
            anchor.pred_opt().unwrap(),
            Movement {
                income: 10.0,
                expense: 0.0,
                unknown: 0,
            },
        );
        let months = cash.months("GBP", first, anchor);
        let month = &months[0];
        assert_eq!(month.currency, "GBP");
        assert_eq!(month.days.len(), 31);
        assert_eq!(
            (month.income, month.expense, month.closing_balance),
            (Some(210.0), Some(150.0), Some(100.0))
        );
        assert_eq!(month.days[0].balance, Some(90.0));
        assert_eq!(month.days[1].income, Some(0.0));
        cash.movements
            .get_mut(&anchor.pred_opt().unwrap())
            .unwrap()
            .unknown = 1;
        let missing = cash.months("GBP", first, anchor);
        assert_eq!(missing[0].days[30].balance, Some(100.0));
        assert_eq!(missing[0].days[29].balance, None);
        assert_eq!(missing[0].income, None);
        cash.products.insert("missing-anchor".into());
        assert!(
            cash.months("GBP", first, anchor)[0]
                .days
                .iter()
                .all(|day| day.balance.is_none())
        );
    }
}
