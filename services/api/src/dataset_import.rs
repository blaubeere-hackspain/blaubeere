//! Deployment command in the API binary; no separate importer service or runtime.
use anyhow::{Context, ensure};
use chrono::{Datelike, NaiveDate, Utc};
use parquet::file::reader::{FileReader, SerializedFileReader};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{Connection, SqliteConnection, sqlite::SqliteConnectOptions};
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};

const SOURCES: [(&str, &str); 5] = [
    ("scores", "reports/score_v4/assessments.parquet"),
    (
        "cash",
        "reports/cash_backfill/cash_backfill_monthly.parquet",
    ),
    (
        "payments",
        "reports/payment_delay_v2/payment_delay_v2_monthly.parquet",
    ),
    (
        "debt",
        "reports/debt_obligation/debt_obligation_monthly.parquet",
    ),
    (
        "transfers",
        "reports/transfer_resolution_v2/resolution.parquet",
    ),
];
const SUMMARY: &str = "reports/score_v4/summary.json";

fn hash(path: &Path) -> anyhow::Result<String> {
    let mut file = File::open(path).with_context(|| format!("Read {}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}
fn text<'a>(row: &'a Value, key: &str) -> anyhow::Result<&'a str> {
    row[key]
        .as_str()
        .filter(|s| !s.is_empty())
        .with_context(|| format!("Missing {key}"))
}

pub async fn build(root: &Path, directory: &Path, revision: &str) -> anyhow::Result<PathBuf> {
    let mut files = Vec::new();
    for (table, path) in SOURCES {
        files.push(json!({"table":table,"path":path,"sha256":hash(&root.join(path))?}));
    }
    let daily_sources = crate::daily_cash::SOURCES;
    let has_daily = daily_sources
        .iter()
        .any(|(_, path)| root.join(path).exists());
    if has_daily {
        for (table, path) in daily_sources {
            files.push(json!({"table":table,"path":path,"sha256":hash(&root.join(path))?}));
        }
    }
    let projection_offset = files.len();
    for (table, path) in crate::cash_projection::SOURCES {
        files.push(json!({"table":table,"path":path,"sha256":hash(&root.join(path))?}));
    }
    let summary_hash = hash(&root.join(SUMMARY))?;
    let batch = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&json!([8, files, summary_hash]))?)
    );
    std::fs::create_dir_all(directory)?;
    let target = directory
        .canonicalize()?
        .join(format!("finance-{batch}.sqlite"));
    if target.exists() {
        let pool = crate::dataset::connect(&format!("sqlite://{}", target.display())).await?;
        let check: String = sqlx::query_scalar("PRAGMA quick_check")
            .fetch_one(&pool)
            .await?;
        let metadata: String = sqlx::query_scalar("SELECT payload FROM dataset_metadata")
            .fetch_one(&pool)
            .await?;
        ensure!(
            check == "ok" && serde_json::from_str::<Value>(&metadata)?["batch_id"] == batch,
            "Existing snapshot is inconsistent"
        );
        pool.close().await;
        return Ok(target);
    }
    let staging = tempfile::Builder::new()
        .prefix(".finance-")
        .suffix(".sqlite")
        .tempfile_in(directory)?;
    let mut db =
        SqliteConnection::connect_with(&SqliteConnectOptions::new().filename(staging.path()))
            .await?;
    let mut tx = db.begin().await?;
    sqlx::raw_sql("CREATE TABLE dataset_metadata(payload TEXT NOT NULL CHECK(json_valid(payload)));
        CREATE TABLE dataset_companies(id TEXT PRIMARY KEY,group_id TEXT NOT NULL,name TEXT NOT NULL);
        CREATE TABLE parquet_records(source TEXT NOT NULL,record_key TEXT NOT NULL,company_id TEXT,period TEXT,payload TEXT NOT NULL CHECK(json_valid(payload)),PRIMARY KEY(source,record_key)) WITHOUT ROWID;
        CREATE INDEX dataset_company_period ON parquet_records(source,company_id,period);")
        .execute(&mut *tx).await?;
    let mut companies = BTreeMap::new();
    for info in &mut files[..SOURCES.len()] {
        let table = text(info, "table")?.to_owned();
        let reader = SerializedFileReader::new(File::open(root.join(text(info, "path")?))?)?;
        let mut count = 0;
        let mut rows = reader.get_row_iter(None)?;
        loop {
            let batch = rows.by_ref().take(1000).collect::<Result<Vec<_>, _>>()?;
            if batch.is_empty() {
                break;
            }
            let mut insert = sqlx::QueryBuilder::new("INSERT INTO parquet_records VALUES ");
            let mut values = insert.separated(",");
            for record in batch {
                let row = record.to_json_value();
                let (key, company, period) = if table == "transfers" {
                    (text(&row, "transaction_id")?.to_owned(), None, None)
                } else {
                    let company = text(&row, "company_id")?;
                    let period = text(&row, "month")?.get(..10).context("Invalid month")?;
                    let month = NaiveDate::parse_from_str(period, "%Y-%m-%d")?;
                    ensure!(month.day() == 1, "Invalid company-month key");
                    if table == "scores" {
                        let as_of = NaiveDate::parse_from_str(text(&row, "as_of")?, "%Y-%m-%d")?;
                        ensure!(
                            as_of.year() == month.year()
                                && as_of.month() == month.month()
                                && as_of.succ_opt().is_some_and(|day| day.day() == 1),
                            "Score must be dated at month end"
                        );
                        ensure!(
                            row["version"] == "healthscore_v4",
                            "Unexpected score version"
                        );
                        ensure!(
                            row.get("health_score").is_some(),
                            "Missing health_score field"
                        );
                        if !row["health_score"].is_null() {
                            let score = row["health_score"]
                                .as_f64()
                                .context("Invalid health score")?;
                            ensure!(
                                (-1e-9..=100.0 + 1e-9).contains(&score),
                                "Score outside 0–100"
                            );
                        }
                        ensure!(
                            row["reasons"]
                                .as_array()
                                .is_some_and(|reasons| reasons.iter().all(Value::is_string)),
                            "Invalid model reason codes"
                        );
                        ensure!(
                            row["excluida"].is_boolean()
                                && (row["excluida"] != true || row["health_score"].is_null()),
                            "Excluded companies must not have a score"
                        );
                        for field in [
                            "t6_efectivo",
                            "deficit_servicio_6",
                            "obligacion_vencida_m",
                            "colchon_v4",
                            "mora_indice",
                            "multiplicador_deuda",
                            "h_antes_de_ajustes",
                            "penalizacion_multiplicador_puntos",
                        ] {
                            ensure!(
                                row.get(field).is_some_and(|value| value.is_null()
                                    || value.as_f64().is_some_and(f64::is_finite)),
                                "Missing or invalid v4 input: {field}"
                            );
                        }
                        let group = text(&row, "group_id")?;
                        let previous = companies.insert(company.to_owned(), group.to_owned());
                        ensure!(
                            previous.is_none_or(|previous| previous == group),
                            "Inconsistent company group"
                        );
                    }
                    (
                        format!("{company}:{period}"),
                        Some(company.to_owned()),
                        Some(period.to_owned()),
                    )
                };
                values
                    .push("(")
                    .push_bind_unseparated(table.clone())
                    .push_unseparated(",")
                    .push_bind_unseparated(key)
                    .push_unseparated(",")
                    .push_bind_unseparated(company)
                    .push_unseparated(",")
                    .push_bind_unseparated(period)
                    .push_unseparated(",")
                    .push_bind_unseparated(serde_json::to_string(&row)?)
                    .push_unseparated(")");
                count += 1;
            }
            insert.build().execute(&mut *tx).await?;
        }
        ensure!(count > 0, "Empty {table} source");
        info["rows"] = json!(count);
        eprintln!("Imported {table}: {count} rows");
    }
    if has_daily {
        let counts = crate::daily_cash::import(root, &mut tx).await?;
        for (info, count) in files[SOURCES.len()..projection_offset]
            .iter_mut()
            .zip(counts)
        {
            info["rows"] = json!(count);
        }
        eprintln!("Imported daily cash in original currencies");
    }
    let projection_counts = crate::cash_projection::import(root, &mut tx).await?;
    for (info, count) in files[projection_offset..].iter_mut().zip(projection_counts) {
        info["rows"] = json!(count);
    }
    eprintln!(
        "Imported invoice cash projections: {} horizons",
        projection_counts[0]
    );
    for (company, group) in companies {
        let name = if company == "COMP_0318" {
            "Blau, Corp."
        } else {
            &company
        };
        sqlx::query("INSERT INTO dataset_companies (id,group_id,name) VALUES (?,?,?)")
            .bind(&company)
            .bind(group)
            .bind(name)
            .execute(&mut *tx)
            .await?;
    }
    let unknown: i64 = sqlx::query_scalar("SELECT count(*) FROM parquet_records WHERE company_id IS NOT NULL AND company_id NOT IN (SELECT id FROM dataset_companies)").fetch_one(&mut *tx).await?;
    ensure!(unknown == 0, "Supporting data contains unknown companies");
    let summary: Value = serde_json::from_slice(&std::fs::read(root.join(SUMMARY))?)?;
    ensure!(
        summary["model_version"] == "healthscore_v4"
            && summary["limitaciones"].is_array()
            && summary["advertencia"].is_string(),
        "Missing v4 model summary or limitations"
    );
    let metadata = json!({"schema_version":4,"batch_id":batch,"source_revision":revision,"imported_at":Utc::now().to_rfc3339(),"files":files,"summary_sha256":summary_hash,"model_summary":summary});
    sqlx::query("INSERT INTO dataset_metadata VALUES (?)")
        .bind(metadata.to_string())
        .execute(&mut *tx)
        .await?;
    for info in &files {
        ensure!(
            hash(&root.join(text(info, "path")?))? == text(info, "sha256")?,
            "Source changed during import"
        );
    }
    ensure!(
        hash(&root.join(SUMMARY))? == summary_hash,
        "Summary changed during import"
    );
    tx.commit().await?;
    let check: String = sqlx::query_scalar("PRAGMA integrity_check")
        .fetch_one(&mut db)
        .await?;
    ensure!(check == "ok", "Dataset integrity check failed");
    db.close().await?;
    staging.persist_noclobber(&target)?;
    Ok(target)
}
