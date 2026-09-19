use super::Company;
use anyhow::ensure;
use chrono::{Datelike, Months, NaiveDate};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Predictive {
    pub target: String,
    pub definition: String,
    pub as_of: NaiveDate,
    pub horizon_months: u32,
    pub horizon_start: NaiveDate,
    pub horizon_end: NaiveDate,
    pub probability_estimate: Option<f64>,
    pub eligible: bool,
    pub accepted: bool,
    pub unavailable_reasons: Vec<String>,
    pub coverage: Coverage,
    pub observed_features: BTreeMap<String, Option<f64>>,
    pub missing_reason: BTreeMap<String, String>,
    pub feature_descriptions: BTreeMap<String, String>,
    pub model_version: String,
    pub calibrated: bool,
    pub scope: String,
    pub currency_basis: String,
    pub validation: Validation,
    pub provenance: Provenance,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Coverage {
    pub eligibility_reasons: Vec<String>,
    pub n_sin_eur_current: u32,
    pub valid_months_3m: u32,
    pub valid_months_6m: u32,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Validation {
    pub labeled_rows: u32,
    pub prospective_rows: u32,
    pub groups: u32,
    pub average_precision: f64,
    pub prevalence: f64,
    pub brier: f64,
    pub baseline_brier: f64,
    pub relative_brier_skill: f64,
    pub relative_brier_skill_ci95: [f64; 2],
    pub interval_method: String,
    pub interval_sampling_unit: String,
    pub limits: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    pub latest_predictions_sha256: String,
    pub model_manifest_sha256: String,
    pub final_report_sha256: String,
}

pub fn validate(c: &Company) -> anyhow::Result<()> {
    let p = c
        .predictive
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("Proxy evidence required"))?;
    ensure!(
        c.opening_cash_cents.is_none()
            && c.buffer_cents.is_none()
            && c.health.is_null()
            && c.flows.is_empty()
            && c.history.is_empty()
            && c.drivers.is_empty()
            && c.coverage.is_empty(),
        "Proxy-only assessment cannot claim cash, history, health or causal drivers"
    );
    ensure!(
        c.data_mode == "synthetic" && c.history_mode == "reconstructed" && c.currency == "EUR",
        "Proxy scope must be synthetic reconstructed analytical EUR"
    );
    ensure!(
        c.name == format!("Synthetic company {}", c.id),
        "Proxy company names must identify synthetic IDs"
    );
    ensure!(
        !p.calibrated && p.target == "target_deficit_3m" && p.horizon_months == 3,
        "Unsupported proxy target, calibration or horizon"
    );
    ensure!(
        p.as_of == c.assessment_date && p.model_version == c.model_version,
        "Inconsistent proxy date or model version"
    );
    let start = p
        .as_of
        .succ_opt()
        .ok_or_else(|| anyhow::anyhow!("Invalid cutoff"))?;
    let end = start
        .checked_add_months(Months::new(3))
        .and_then(|d| d.pred_opt());
    ensure!(
        start.day() == 1 && p.horizon_start == start && Some(p.horizon_end) == end,
        "Proxy horizon must cover the next three calendar months"
    );
    let available = p.accepted && p.eligible;
    ensure!(
        p.probability_estimate.is_some() == available,
        "Probability requires accepted and eligible evidence"
    );
    ensure!(
        p.probability_estimate
            .is_none_or(|v| v.is_finite() && (0.0..=1.0).contains(&v)),
        "Probability must be finite in [0,1]"
    );
    ensure!(
        p.unavailable_reasons.is_empty() == available
            && p.unavailable_reasons.iter().all(|s| !s.trim().is_empty()),
        "Abstention must have explicit reasons"
    );
    ensure!(
        p.coverage.valid_months_3m <= 3
            && p.coverage.valid_months_6m <= 6
            && p.coverage.valid_months_6m >= p.coverage.valid_months_3m,
        "Invalid coverage months"
    );
    ensure!(
        p.coverage.eligibility_reasons.is_empty() == p.eligible,
        "Inconsistent eligibility reasons"
    );
    ensure!(
        !p.eligible || (p.coverage.valid_months_3m == 3 && p.coverage.n_sin_eur_current == 0),
        "Eligible proxy lacks current coverage"
    );
    ensure!(
        !p.observed_features.is_empty()
            && p.observed_features.keys().eq(p.feature_descriptions.keys()),
        "Missing feature descriptions"
    );
    for (name, value) in &p.observed_features {
        ensure!(
            value.is_none_or(f64::is_finite),
            "Non-finite observed feature"
        );
        ensure!(
            value.is_none() == p.missing_reason.contains_key(name),
            "Missing or contradictory feature reason"
        );
    }
    ensure!(
        p.missing_reason
            .iter()
            .all(|(name, reason)| p.observed_features.contains_key(name) && !reason.is_empty()),
        "Invalid missing feature reason"
    );
    ensure!(
        !p.definition.is_empty()
            && !p.scope.is_empty()
            && !p.currency_basis.is_empty()
            && !p.model_version.is_empty(),
        "Missing proxy scope"
    );
    let v = &p.validation;
    ensure!(
        v.labeled_rows > 0
            && v.labeled_rows <= v.prospective_rows
            && v.groups > 0
            && v.groups <= v.labeled_rows
            && !v.limits.is_empty(),
        "Invalid validation coverage"
    );
    ensure!(
        [v.average_precision, v.prevalence, v.brier, v.baseline_brier]
            .iter()
            .all(|n| n.is_finite() && (0.0..=1.0).contains(n)),
        "Invalid validation metric"
    );
    ensure!(
        v.relative_brier_skill.is_finite()
            && v.relative_brier_skill_ci95.iter().all(|n| n.is_finite())
            && v.relative_brier_skill_ci95[0] <= v.relative_brier_skill_ci95[1],
        "Invalid validation interval"
    );
    for hash in [
        &p.provenance.latest_predictions_sha256,
        &p.provenance.model_manifest_sha256,
        &p.provenance.final_report_sha256,
    ] {
        ensure!(
            hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit()),
            "Invalid provenance digest"
        );
    }
    Ok(())
}
