import argparse
import csv
import json
from pathlib import Path

import duckdb
import joblib

from scripts.model_learning import (
    aggregate_diagnostics, fit_candidate, grouped_bootstrap, metrics,
    probabilities, reliability, select_candidate,
)
from scripts.model_protocol import TARGET_PATH, load_development_labels, sha256_file, support_gate, write_json
from scripts.model_training_io import (
    EXPERIMENT_DIR, FEATURE_NAMES, MODEL_SOURCES, PROTOCOL_DIR, ROOT,
    artifact_hashes, assert_pair, freeze_experiment, load_features, matrix,
    observed_sample, read_json, reserve_evaluation, seal_json,
    verify_experiment, verify_local_model, verify_sealed,
)


def develop():
    config, _ = verify_experiment()
    write_json(EXPERIMENT_DIR / 'development_receipt.json', {
        'status': 'started_before_development_labels',
        'artifacts_sha256': artifact_hashes(EXPERIMENT_DIR, ['config.json', 'freeze.json'])})
    samples, supports = {}, {}
    with duckdb.connect(config={'threads': 2}) as con:
        for fold in ('fold1', 'fold2'):
            train_name, eval_name = fold + '_train', fold + '_validation'
            train_features, eval_features = load_features(con, train_name), load_features(con, eval_name)
            assert_pair(train_features, eval_features)
            for name, features in ((train_name, train_features), (eval_name, eval_features)):
                labels = load_development_labels(con, PROTOCOL_DIR, name)
                supports[name] = support_gate(labels) | {'prospective_rows': len(features)}
                samples[name] = observed_sample(labels, features)
    seal_json('development_support.json', supports)
    results = []
    all_supported = all(item['passed'] for item in supports.values())
    if all_supported:
        for candidate in config['candidates']:
            result = {**candidate, 'folds': []}
            for fold in ('fold1', 'fold2'):
                _, x_train, y_train = samples[fold + '_train']
                _, x_eval, y_eval = samples[fold + '_validation']
                fitted = fit_candidate(candidate, x_train, y_train, config['seed'])
                p = probabilities(fitted, x_eval)
                score = metrics(y_eval, p, float(y_train.mean()))
                score['split'] = fold + '_validation'
                result['folds'].append(score)
                seal_json(candidate['id'] + '.' + fold + '.json', score)
            results.append(result)
    selected = select_candidate(results)
    status = 'selected' if selected else 'blocked_no_candidate' if all_supported else 'blocked_support'
    development = {'status': status, 'support': supports, 'candidates': results,
                   'selected_candidate': selected, 'config_sha256': sha256_file(EXPERIMENT_DIR / 'config.json'),
                   'scope': config['scope'], 'automatic_correction': False}
    development_sha = seal_json('development.json', development)
    selection = {'status': status, 'selected_candidate': selected, 'development_sha256': development_sha,
                 'artifacts_sha256': artifact_hashes(EXPERIMENT_DIR, ['config.json', 'freeze.json', 'development.json',
                                                                    'development_support.json']),
                 'next_action': 'Fit selected candidate on frozen final training split' if selected else
                     'Holdout stays sealed. Ask parent before one focused correction; never relax frozen features, cuts or support.'}
    seal_json('selection.json', selection)
    return development


def fit_final():
    config, _ = verify_experiment()
    selection = verify_sealed('selection.json')
    development = verify_sealed('development.json')
    if (selection['status'] != 'selected' or not selection['selected_candidate']
            or selection['selected_candidate'] != select_candidate(development['candidates'])
            or selection['development_sha256'] != sha256_file(EXPERIMENT_DIR / 'development.json')):
        raise ValueError('No eligible frozen development selection')
    candidate = next(c for c in config['candidates'] if c['id'] == selection['selected_candidate'])
    write_json(EXPERIMENT_DIR / 'final_training_receipt.json', {
        'status': 'started_before_final_fit', 'candidate': candidate,
        'artifacts_sha256': artifact_hashes(EXPERIMENT_DIR, ['selection.json', 'config.json', 'freeze.json'])})
    with duckdb.connect(config={'threads': 2}) as con:
        features = load_features(con, 'final_train')
        labels = load_development_labels(con, PROTOCOL_DIR, 'final_train')
    support = support_gate(labels) | {'prospective_rows': len(features)}
    seal_json('final_training_support.json', support)
    if not support['passed']:
        report = {'status': 'blocked_final_training_support', 'support': support, 'holdout_opened': False}
        seal_json('final_training_blocked.json', report)
        return report
    _, x, y = observed_sample(labels, features)
    model = fit_candidate(candidate, x, y, config['seed'])
    with (EXPERIMENT_DIR / 'model.joblib').open('xb') as stream:
        joblib.dump(model, stream, compress=3)
    files = sorted(path.name for path in EXPERIMENT_DIR.iterdir() if path.is_file())
    manifest = {
        'status': 'fitted_before_holdout', 'model_version': 'experiment-v1:' + candidate['id'],
        'candidate': candidate, 'parameters': model.get_params(deep=True),
        'feature_order': list(FEATURE_NAMES), 'runtime': config['runtime'],
        'train_prevalence': float(y.mean()), 'train_rows': len(y), 'support': support,
        'artifacts_sha256': artifact_hashes(EXPERIMENT_DIR, files),
        'source_sha256': artifact_hashes(ROOT, MODEL_SOURCES),
        'prepared_sha256': sha256_file(PROTOCOL_DIR / 'prepared.json'),
        'input_binding': read_json(EXPERIMENT_DIR / 'freeze.json')['gate']['binding'],
        'scope': config['scope'],
        'loading_policy': 'Trusted fixed local artifact only; SHA verifies integrity, not safety of external pickles',
    }
    seal_json('model_manifest.json', manifest)
    return {'status': manifest['status'], 'model_sha256': sha256_file(EXPERIMENT_DIR / 'model.joblib'),
            'manifest_sha256': sha256_file(EXPERIMENT_DIR / 'model_manifest.json'), 'support': support}


def evaluate_final():
    config, frozen = verify_experiment()
    manifest = verify_sealed('model_manifest.json')
    if manifest['status'] != 'fitted_before_holdout':
        raise ValueError('Final model must be fitted and persisted before holdout access')
    manifest_sha = sha256_file(EXPERIMENT_DIR / 'model_manifest.json')
    model, manifest = verify_local_model(EXPERIMENT_DIR, manifest_sha)
    required = manifest['artifacts_sha256'] | artifact_hashes(EXPERIMENT_DIR, [
        'model_manifest.json', 'model_manifest.json.integrity.json'])
    receipt_sha = reserve_evaluation(EXPERIMENT_DIR, required)
    with duckdb.connect(config={'threads': 2}) as con:
        features = load_features(con, 'final_test')
        assert_pair(load_features(con, 'final_train'), features)
        spec = frozen['protocol']['splits']['final_test']
        assignment = read_json(PROTOCOL_DIR / 'group_assignment.json')
        groups = sorted(group for group, partition in assignment.items() if partition == 'final_test')
        result = con.execute('''WITH selected_keys AS MATERIALIZED (
                SELECT company_id,group_id,month,label_available_at FROM read_parquet(?)
                WHERE month BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND label_available_at<=CAST(? AS DATE)
                  AND group_id IN (SELECT unnest(?))
            ), scoped_targets AS MATERIALIZED (
                SELECT t.company_id,t.group_id,t.month,t.target_deficit_3m,t.target_version
                FROM read_parquet(?) t JOIN selected_keys k USING(company_id,group_id,month)
                WHERE t.available_at_deficit=k.label_available_at
                  AND t.available_at_deficit<=CAST(? AS DATE)
            )
            SELECT k.company_id,k.group_id,k.month,t.target_deficit_3m AS value,t.target_version
            FROM selected_keys k LEFT JOIN scoped_targets t USING(company_id,group_id,month)
            ORDER BY k.company_id,k.month''',
            [str(PROTOCOL_DIR / 'splits/final_test.parquet'), spec['start'], spec['end'], spec['labels_as_of'],
             groups, str(Path(frozen['protocol']['binding']['run_path']) / TARGET_PATH), spec['labels_as_of']])
        names = [column[0] for column in result.description]
        labels = [dict(zip(names, row)) for row in result.fetchall()]
    if any(row['target_version'] != 1 for row in labels):
        raise ValueError('Missing/immature/wrong-version holdout labels; receipt remains consumed')
    support = support_gate(labels) | {'prospective_rows': len(features)}
    rows, x, y = observed_sample(labels, features)
    baseline = manifest['train_prevalence']
    report = {'status': 'blocked_support', 'accepted': False, 'support': support,
              'model_manifest_sha256': manifest_sha, 'evaluation_receipt_sha256': receipt_sha,
              'scope': config['scope'], 'calibrated': False,
              'coverage': {'prospective_rows': len(features), 'labeled_rows': len(rows),
                           'label_observation_fraction': len(rows) / len(features) if features else None}}
    p_all = probabilities(model, matrix(features, FEATURE_NAMES))
    if support['passed']:
        p = probabilities(model, x)
        score = metrics(y, p, baseline)
        report.update(status='accepted_proxy_only' if score['passed'] else 'blocked_final_metrics',
                      accepted=score['passed'], metrics=score, reliability_bins=reliability(y, p),
                      grouped_bootstrap=grouped_bootstrap(y, p, [r['group_id'] for r in rows], baseline,
                                                         config['seed'], config['bootstrap']['resamples']),
                      diagnostics=aggregate_diagnostics(rows, y, p, baseline, features))
    observed = {(r['company_id'], r['month']): r['value'] for r in labels}
    with (EXPERIMENT_DIR / 'final_test_predictions.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['company_id', 'group_id', 'as_of', 'horizon_months',
                                                   'label', 'p_proxy', 'baseline_probability', 'accepted',
                                                   'model_version', 'coverage_json'])
        writer.writeheader()
        for row, probability in zip(features, p_all):
            writer.writerow({'company_id': row['company_id'], 'group_id': row['group_id'],
                             'as_of': row['known_as_of'], 'horizon_months': 3,
                             'label': observed[(row['company_id'], row['month'])],
                             'p_proxy': float(probability), 'baseline_probability': baseline,
                             'accepted': report['accepted'], 'model_version': manifest['model_version'],
                             'coverage_json': json.dumps(row['coverage'], sort_keys=True)})
    report['predictions_sha256'] = sha256_file(EXPERIMENT_DIR / 'final_test_predictions.csv')
    seal_json('final_report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bounded frozen synthetic proxy experiment; exclusive artifacts and one-shot holdout')
    parser.add_argument('command', choices=['freeze', 'develop', 'fit-final', 'evaluate-final', 'infer-latest'])
    args = parser.parse_args(argv)
    if args.command == 'infer-latest':
        from scripts.model_inference import infer_latest
        result = infer_latest()
    else:
        result = {'freeze': freeze_experiment, 'develop': develop,
                  'fit-final': fit_final, 'evaluate-final': evaluate_final}[args.command]()
    print(json.dumps(result, sort_keys=True, default=str, allow_nan=False))
    return 0 if not result.get('status', '').startswith('blocked') else 2


if __name__ == '__main__':
    raise SystemExit(main())
