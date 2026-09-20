import argparse
import calendar
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/modeling/experiment-v1"
OUTPUT = ROOT / "reports/modeling/assessment-v1"
TRUSTED_PREDICTIONS_SHA256 = "ec6b3d3f813238412420a0627dabf56e1ee7ef38111f92aadb2415d98a770cec"
TARGET = "target_deficit_3m"
DEFINITION = "Operating deficit in at least two of the next three calendar months, within the observed cash-account perimeter; robust to ambiguous operating classifications."
LIMITS = [
    "Synthetic, retrospectively reconstructed data; not a certified point-in-time record.",
    "Probability estimate is not calibrated and is not a probability of default or a health score.",
    "Validation is conditional on observed labels; censored outcomes are not negative labels.",
    "Grouped bootstrap intervals describe aggregate fixed-fit validation, not individual probability uncertainty.",
    "Observed input features are evidence, not model contributions or causal explanations.",
    "Cash balances, dated cash flows and cash history are unknown; empty arrays do not mean no activity.",
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sealed_json(folder, name, expected):
    data = (folder / name).read_bytes()
    actual = digest(data)
    require(actual == expected, f"Sealed hash mismatch: {name}")
    integrity = json.loads((folder / f"{name}.integrity.json").read_bytes())
    require(integrity == {"sha256": actual}, f"Integrity mismatch: {name}")
    return json.loads(data)


def verified_sources(folder=SOURCE):
    latest = sealed_json(folder, "latest_predictions.json", TRUSTED_PREDICTIONS_SHA256)
    model = sealed_json(folder, "model_manifest.json", latest["model_manifest_sha256"])
    report = sealed_json(folder, "final_report.json", latest["final_report_sha256"])
    require(report["model_manifest_sha256"] == latest["model_manifest_sha256"], "Broken report/model link")
    require(latest["selection_sha256"] == model["artifacts_sha256"]["selection.json"], "Broken selection link")
    for name, expected in model["artifacts_sha256"].items():
        require(Path(name).name == name, "Invalid artifact path")
        require(digest((folder / name).read_bytes()) == expected, f"Model artifact mismatch: {name}")
    require(latest["accepted"] is report["accepted"] is True, "Model was not accepted")
    require(latest["calibrated"] is report["calibrated"] is False, "Unexpected calibration claim")
    require(latest["future_labels_consulted"] is False, "Unexpected inference label access")
    require(latest["status"] == report["status"] == "accepted_proxy_only", "Unexpected model scope")
    require(latest["feature_order"] == model["feature_order"], "Feature order mismatch")
    require(latest["input_sha256"] == model["input_binding"]["input_sha256"]["data/marts/panel_flujos.parquet"], "Broken analytical input link")
    require(latest["feature_descriptions"]["log_gross"] == "log1p(volumen_caja_conocido_eur)", "Analytical EUR basis not verified")
    return latest, model, report


def calendar_horizon(as_of):
    from datetime import date, timedelta
    cutoff = date.fromisoformat(as_of)
    require(cutoff.day == calendar.monthrange(cutoff.year, cutoff.month)[1], "Cutoff must close a calendar month")
    start = cutoff + timedelta(days=1)
    month_index = cutoff.year * 12 + cutoff.month - 1 + 3
    year, month = divmod(month_index, 12)
    end = date(year, month + 1, calendar.monthrange(year, month + 1)[1])
    return start.isoformat(), end.isoformat()


def assessments(latest, model, report):
    start, end = calendar_horizon(latest["as_of"])
    require(latest["horizon_months"] == 3, "Unexpected horizon")
    metrics = report["metrics"]
    validation = {
        "labeled_rows": report["coverage"]["labeled_rows"],
        "prospective_rows": report["coverage"]["prospective_rows"],
        "groups": report["support"]["groups"],
        "average_precision": metrics["average_precision"],
        "prevalence": metrics["prevalence"],
        "brier": metrics["brier"],
        "baseline_brier": metrics["baseline"]["brier"],
        "relative_brier_skill": metrics["relative_brier_skill"],
        "relative_brier_skill_ci95": report["grouped_bootstrap"]["intervals_95"]["relative_brier_skill"],
        "interval_method": report["grouped_bootstrap"]["method"],
        "interval_sampling_unit": report["grouped_bootstrap"]["sampling_unit"],
        "limits": LIMITS,
    }
    result, ids = [], set()
    for row in latest["predictions"]:
        require(row["company_id"] and row["company_id"] not in ids, "Duplicate or empty company ID")
        ids.add(row["company_id"])
        require(row["as_of"] == latest["as_of"] and row["horizon_months"] == 3 and row["target"] == TARGET, "Inconsistent row scope")
        require(row["model_version"] == model["model_version"] and row["accepted"] is latest["accepted"], "Inconsistent model acceptance")
        require(type(row["eligible"]) is bool, "Invalid eligibility")
        p = row["p_proxy"]
        available = row["accepted"] and row["eligible"]
        require((type(p) in (float, int) and math.isfinite(p) and 0 <= p <= 1) if available else p is None, "Invalid or disallowed probability")
        require(bool(row["unavailable_reasons"]) is (not available), "Missing or contradictory abstention reason")
        require(set(row["features"]) == set(latest["feature_order"]), "Missing feature keys")
        require(all(value is None or (type(value) in (float, int) and math.isfinite(value)) for value in row["features"].values()), "Invalid feature")
        require(set(row["missing_reason"]) == {key for key, value in row["features"].items() if value is None}, "Missing feature reasons")
        result.append({
            "kind": "proxy_only", "id": row["company_id"],
            "name": f"Synthetic company {row['company_id']}", "group": row["group_id"],
            "currency": "EUR", "assessment_date": row["as_of"], "model_version": row["model_version"],
            "history_mode": "reconstructed", "data_mode": "synthetic",
            "opening_cash_cents": None, "buffer_cents": None, "health": None,
            "history": [], "drivers": [], "coverage": [], "flows": [],
            "predictive": {
                "target": row["target"], "definition": DEFINITION,
                "as_of": row["as_of"], "horizon_months": 3, "horizon_start": start, "horizon_end": end,
                "probability_estimate": p, "eligible": row["eligible"], "accepted": row["accepted"],
                "unavailable_reasons": row["unavailable_reasons"], "coverage": row["coverage"],
                "observed_features": row["features"], "missing_reason": row["missing_reason"],
                "feature_descriptions": latest["feature_descriptions"], "model_version": row["model_version"],
                "calibrated": False, "scope": latest["scope"],
                "currency_basis": "Analytical EUR from verified known-EUR source features; not the company's native reporting currency.",
                "validation": validation,
                "provenance": {"latest_predictions_sha256": TRUSTED_PREDICTIONS_SHA256, "model_manifest_sha256": latest["model_manifest_sha256"], "final_report_sha256": latest["final_report_sha256"]},
            },
        })
    require(len(result) == latest["companies"] == 1286, "Company count mismatch")
    require(sum(c["predictive"]["probability_estimate"] is not None for c in result) == latest["companies_with_estimate"] == latest["eligible_companies"] == 1010, "Estimate count mismatch")
    return result


def export(output=OUTPUT, check=False, source=SOURCE):
    latest, model, report = verified_sources(source)
    companies = assessments(latest, model, report)
    payload = encoded(companies)
    manifest = {
        "schema_version": 1, "kind": "proxy_only", "as_of": latest["as_of"],
        "companies": len(companies), "estimates": 1010, "abstentions": 276,
        "companies_sha256": digest(payload), "exporter_sha256": digest(Path(__file__).read_bytes()),
        "source": "reports/modeling/experiment-v1/latest_predictions.json",
        "source_sha256": TRUSTED_PREDICTIONS_SHA256,
        "model_manifest_sha256": latest["model_manifest_sha256"], "final_report_sha256": latest["final_report_sha256"],
        "model_version": model["model_version"], "scope": latest["scope"],
        "cash_history_semantics": "Unknown, not zero and not no activity",
        "reproduction": "python3 scripts/export_assessments.py --check",
    }
    output = Path(output).resolve()
    require(not any(output.is_relative_to(ROOT / "reports/modeling" / name) for name in ("experiment-v1", "protocol-v1")), "Cannot write sealed source directories")
    files = {"companies.json": payload, "manifest.json": encoded(manifest)}
    for name, content in files.items():
        path = output / name
        if path.exists():
            require(path.read_bytes() == content, f"Immutable output differs: {name}")
        else:
            require(not check, f"Missing output: {name}")
    if not check:
        output.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            path = output / name
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(content)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export sealed proxy-only assessments without training, evaluation, or inference.")
    parser.add_argument("--check", action="store_true", help="Verify identical existing output without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(export(args.output, args.check), sort_keys=True))
