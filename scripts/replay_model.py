import argparse
from datetime import date
import json
import math
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.export_assessments import (
    ROOT, TRUSTED_PREDICTIONS_SHA256, digest, encoded, require, verified_sources,
)
from scripts.model_features import FEATURE_NAMES, build_features, feature_contract, load_panel

AS_OF = '2026-08-31'
ORIGIN = date(2026, 8, 1)
PANEL_PATH = 'data/marts/panel_flujos.parquet'
PROBABILITY_TOLERANCE = 1e-12


def bound_panel(manifest, root=ROOT):
    binding = manifest['input_binding']
    runs = (root / 'data/runs').resolve()
    run = Path(binding['run_path']).resolve()
    require(run.parent == runs and run.name == binding['run_id'], 'Run outside bound data/runs')
    paths = {'manifest.json': binding['run_manifest_sha256'],
             PANEL_PATH: binding['input_sha256'][PANEL_PATH]}
    for name, expected in paths.items():
        path = (run / name).resolve()
        require(path.is_relative_to(run) and path.is_file(), 'Bound path escaped or missing')
        require(digest(path.read_bytes()) == expected, f'Bound hash mismatch: {name}')
    run_manifest = json.loads((run / 'manifest.json').read_bytes())
    require(run_manifest['run_id'] == binding['run_id'], 'Run ID mismatch')
    require(run_manifest['outputs'][PANEL_PATH] == paths[PANEL_PATH], 'Panel manifest link mismatch')
    return run / PANEL_PATH


def latest_features(panel):
    groups = {row['company_id']: row['group_id'] for row in panel}
    features = build_features(panel, [(company, ORIGIN) for company in sorted(groups)], cutoff=AS_OF)
    covered = {row['company_id'] for row in features}
    for company in sorted(groups.keys() - covered):
        features.append({'company_id': company, 'group_id': groups[company], 'month': ORIGIN,
                         'known_as_of': date.fromisoformat(AS_OF), 'eligible': False,
                         'coverage': {'eligibility_reasons': ['missing_current_month'],
                                      'valid_months_3m': 0, 'valid_months_6m': 0},
                         'features': dict.fromkeys(FEATURE_NAMES),
                         'missing_reason': dict.fromkeys(FEATURE_NAMES, 'missing_current_month')})
    return sorted(features, key=lambda row: row['company_id'])


def compare_records(stored, replayed, feature_order):
    require(feature_order == list(FEATURE_NAMES), 'Feature order mismatch')

    def keyed(rows):
        result = {}
        for row in rows:
            company = row['company_id']
            require(type(company) is str and company and company not in result, 'Duplicate or invalid company ID')
            require(set(row['features']) == set(FEATURE_NAMES), 'Feature keys mismatch')
            require(set(row['missing_reason']) == {key for key, value in row['features'].items() if value is None},
                    'Missing reason keys mismatch')
            require(all(value is None or (type(value) in (int, float) and math.isfinite(value))
                        for value in row['features'].values()), 'Invalid feature value')
            require(type(row['eligible']) is bool and type(row['accepted']) is bool, 'Invalid eligibility/acceptance')
            p = row['p_proxy']
            available = row['eligible'] and row['accepted']
            require((type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1) if available else p is None,
                    'Invalid probability or abstention')
            require(bool(row['unavailable_reasons']) is (not available), 'Contradictory abstention reasons')
            result[company] = row
        return result

    before, after = keyed(stored), keyed(replayed)
    require(before.keys() == after.keys(), 'Company set mismatch')
    errors = []
    for company, expected in before.items():
        actual = after[company]
        require(encoded({k: v for k, v in expected.items() if k != 'p_proxy'}) ==
                encoded({k: v for k, v in actual.items() if k != 'p_proxy'}),
                f'Metadata/features mismatch: {company}')
        p, q = expected['p_proxy'], actual['p_proxy']
        require((p is None) == (q is None), f'Unknown probability mismatch: {company}')
        if p is not None:
            errors.append(abs(p - q))
            require(errors[-1] <= PROBABILITY_TOLERANCE, f'Probability mismatch: {company}')
    return {'companies': len(before), 'estimates': len(errors), 'abstentions': len(before) - len(errors),
            'metadata_features_exact': True, 'probabilities_exact': all(error == 0 for error in errors),
            'max_probability_abs_error': max(errors, default=0.),
            'probability_absolute_tolerance': PROBABILITY_TOLERANCE}


def replay():
    latest, sealed_manifest, _ = verified_sources()
    require(latest['as_of'] == AS_OF and latest['horizon_months'] == 3, 'Unexpected inference date/horizon')
    require(latest['feature_order'] == list(FEATURE_NAMES), 'Feature order mismatch')
    require(latest['feature_descriptions'] == feature_contract()['definitions'], 'Feature descriptions mismatch')
    require(latest['scope'] == sealed_manifest['scope'], 'Scope mismatch')
    panel_path = bound_panel(sealed_manifest)

    import duckdb
    from scripts.model_inference import prediction_records
    from scripts.model_training_io import EXPERIMENT_DIR, verify_local_model

    model, manifest = verify_local_model(EXPERIMENT_DIR, latest['model_manifest_sha256'])
    require(manifest == sealed_manifest, 'Model manifest changed during verification')
    with duckdb.connect(config={'threads': 2}) as con:
        panel = load_panel(con, panel_path)
    records = prediction_records(latest_features(panel), model, manifest['model_version'], latest['accepted'])
    result = compare_records(latest['predictions'], records, latest['feature_order'])
    require(result['companies'] == latest['companies'] == 1286, 'Company count mismatch')
    require(result['estimates'] == latest['companies_with_estimate'], 'Estimate count mismatch')
    require(sum(row['eligible'] for row in records) == latest['eligible_companies'], 'Eligibility count mismatch')
    bound_panel(manifest)
    verified_sources()
    return {'status': 'passed', 'operation': 'read_only_inference_replay', 'as_of': AS_OF,
            'training_performed': False, 'evaluation_performed': False, 'target_values_queried': 0,
            'accepted': latest['accepted'], 'model_version': manifest['model_version'], **result,
            'latest_predictions_sha256': TRUSTED_PREDICTIONS_SHA256,
            'model_manifest_sha256': latest['model_manifest_sha256'],
            'model_sha256': manifest['artifacts_sha256']['model.joblib'],
            'final_report_sha256': latest['final_report_sha256'],
            'run_manifest_sha256': manifest['input_binding']['run_manifest_sha256'],
            'panel_sha256': latest['input_sha256']}


def main():
    parser = argparse.ArgumentParser(description='Read-only replay of the sealed August 2026 local model; no fitting or evaluation.')
    parser.add_argument('--check', required=True, action='store_true')
    parser.parse_args()
    print(json.dumps(replay(), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
