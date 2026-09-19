from datetime import date
from pathlib import Path

import duckdb

from scripts.model_features import FEATURE_NAMES, build_features, load_panel
from scripts.model_learning import probabilities
from scripts.model_protocol import PANEL_PATH, sha256_file
from scripts.model_training_io import (
    EXPERIMENT_DIR, matrix, seal_json, verify_experiment, verify_local_model, verify_sealed,
)


def prediction_records(features, model, model_version, accepted):
    eligible = [row for row in features if row['eligible']]
    estimates = {}
    if model is not None and accepted and eligible:
        values = probabilities(model, matrix(eligible, FEATURE_NAMES))
        estimates = {row['company_id']: float(value) for row, value in zip(eligible, values)}
    result = []
    for row in features:
        reasons = list(row['coverage']['eligibility_reasons'])
        if not accepted:
            reasons.append('no_accepted_model')
        result.append({
            'company_id': row['company_id'], 'group_id': row['group_id'],
            'as_of': str(row['known_as_of']), 'origin_month': str(row['month']), 'horizon_months': 3,
            'target': 'target_deficit_3m', 'p_proxy': estimates.get(row['company_id']),
            'eligible': row['eligible'], 'accepted': bool(accepted), 'model_version': model_version,
            'coverage': row['coverage'], 'unavailable_reasons': reasons,
            'features': row['features'], 'missing_reason': row['missing_reason'],
        })
    return result


def infer_latest():
    config, frozen = verify_experiment()
    selection = verify_sealed('selection.json')
    model, manifest, accepted = None, None, False
    final_report_path = EXPERIMENT_DIR / 'final_report.json'
    if final_report_path.exists():
        report = verify_sealed('final_report.json')
        model, manifest = verify_local_model(EXPERIMENT_DIR, report['model_manifest_sha256'])
        accepted = report['accepted']
    elif selection['status'] == 'selected' and not (EXPERIMENT_DIR / 'final_training_blocked.json').exists():
        raise ValueError('Selected experiment is incomplete; inspect evaluation receipt before inference')
    run = Path(frozen['protocol']['binding']['run_path'])
    with duckdb.connect(config={'threads': 2}) as con:
        panel = load_panel(con, run / PANEL_PATH)
    groups = {row['company_id']: row['group_id'] for row in panel}
    origin = date.fromisoformat(config['latest_origin'])
    features = build_features(panel, [(company, origin) for company in sorted(groups)], cutoff='2026-08-31')
    covered = {row['company_id'] for row in features}
    for company in sorted(groups.keys() - covered):
        features.append({'company_id': company, 'group_id': groups[company], 'month': origin,
                         'known_as_of': date(2026, 8, 31), 'eligible': False,
                         'coverage': {'eligibility_reasons': ['missing_current_month'],
                                      'valid_months_3m': 0, 'valid_months_6m': 0},
                         'features': dict.fromkeys(FEATURE_NAMES),
                         'missing_reason': dict.fromkeys(FEATURE_NAMES, 'missing_current_month')})
    features.sort(key=lambda row: row['company_id'])
    records = prediction_records(features, model, manifest['model_version'] if manifest else None, accepted)
    result = {
        'schema_version': 1, 'status': 'accepted_proxy_only' if accepted else 'blocked_no_accepted_model',
        'as_of': '2026-08-31', 'horizon_months': 3, 'accepted': accepted, 'scope': config['scope'],
        'calibrated': False, 'future_labels_consulted': False,
        'feature_order': list(FEATURE_NAMES), 'feature_descriptions': frozen['feature_contract']['definitions'],
        'input_sha256': frozen['protocol']['binding']['input_sha256'][PANEL_PATH],
        'model_manifest_sha256': sha256_file(EXPERIMENT_DIR / 'model_manifest.json') if manifest else None,
        'selection_sha256': sha256_file(EXPERIMENT_DIR / 'selection.json'),
        'final_report_sha256': sha256_file(final_report_path) if final_report_path.exists() else None,
        'companies': len(records), 'eligible_companies': sum(row['eligible'] for row in records),
        'companies_with_estimate': sum(row['p_proxy'] is not None for row in records), 'predictions': records,
    }
    digest = seal_json('latest_predictions.json', result)
    return {name: result[name] for name in ('status', 'companies', 'eligible_companies', 'companies_with_estimate')} | {
        'path': 'reports/modeling/experiment-v1/latest_predictions.json', 'sha256': digest}
