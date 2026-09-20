import json
import os
import platform
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np

from scripts.model_features import FEATURE_NAMES, as_date, feature_contract
from scripts.model_protocol import (
    ROOT, bind_inputs, matching_splits, protocol_contract,
    read_prepared, sha256_file, write_json,
)
from xray.model_audit import CHECK_COMMANDS, current_check, current_source_hashes


PROTOCOL_DIR = ROOT / 'reports/modeling/protocol-v1'
EXPERIMENT_DIR = ROOT / 'reports/modeling/experiment-v1'
PREPARED_SHA256 = 'ddb51011076204f1f1856d09f2e346765ccb098df68efbce941e8ee68f9c0178'
MODEL_SOURCES = (
    'scripts/model_features.py', 'scripts/model_protocol.py', 'scripts/model_learning.py',
    'scripts/model_training_io.py', 'scripts/model_experiment.py', 'scripts/model_inference.py',
    'tests/test_model_training.py', 'requirements-model.in', 'requirements-model.txt',
)


def read_json(path):
    return json.loads(Path(path).read_text())


def check_hashes(base, hashes):
    base = Path(base).resolve()
    for relative, expected in hashes.items():
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f'Bound artifact changed or missing: {relative}')


def artifact_hashes(base, names):
    return {name: sha256_file(base / name) for name in names}


def verify_contracts(frozen):
    for key, expected in (('feature_contract', feature_contract()), ('protocol', protocol_contract())):
        actual = {name: value for name, value in frozen[key].items() if name != 'binding'}
        if actual != expected:
            raise ValueError(f'Frozen {key} differs from approved contract')


def verify_gate():
    path = ROOT / 'reports/modeling/data_gate.json'
    gate = read_json(path)
    if (gate.get('audit_exit_code') != 0 or gate.get('blockers')
            or gate.get('gate_status') != 'pass_for_approved_retrospective_scope'):
        raise ValueError('G-DATA blocked')
    sources = current_source_hashes(ROOT)
    if gate['current_source_hashes'] != sources:
        raise ValueError('G-DATA source evidence is stale; refresh --verify before fitting')
    for _, command in CHECK_COMMANDS:
        if not current_check(ROOT, gate['commands'], command, sources)['passed']:
            raise ValueError('G-DATA portable check evidence is stale')
    run, binding = bind_inputs(ROOT)
    if ROOT / gate['run'] != run or not all(gate['manifest']['checks'].values()):
        raise ValueError('G-DATA run binding differs')
    manifest = read_json(run / 'manifest.json')
    check_hashes(run, manifest['outputs'])
    check_hashes(run / 'source', manifest['source'])
    check_hashes(ROOT / 'data', manifest['inputs'])
    check_hashes(ROOT, {entry['log']: entry['log_sha256'] for entry in gate['commands']})
    return {'data_gate_sha256': sha256_file(path), 'binding': binding}


def configuration():
    return {
        'experiment_version': 'experiment-v1', 'seed': 1729, 'threads': 2,
        'feature_order': list(FEATURE_NAMES),
        'candidates': [
            {'id': 'logistic_c0.1', 'family': 'logistic', 'C': 0.1},
            {'id': 'logistic_c1', 'family': 'logistic', 'C': 1.0},
            {'id': 'hgb_leaf7', 'family': 'hgb', 'max_leaf_nodes': 7},
            {'id': 'hgb_leaf15', 'family': 'hgb', 'max_leaf_nodes': 15},
        ],
        'logistic': {'imputer': 'median', 'keep_empty_features': True, 'add_indicator': True,
                     'scaler': 'StandardScaler', 'max_iter': 2000, 'solver': 'lbfgs'},
        'hgb': {'max_depth': 3, 'learning_rate': 0.05, 'max_iter': 150,
                'l2_regularization': 5., 'early_stopping': False, 'missing': 'native'},
        'selection': 'Both folds AP > prevalence and Brier <= 0.95 * train-prevalence baseline; minimum mean Brier; exact ties logistic then candidate ID',
        'baseline': 'mean(observed training labels), no evaluation fit',
        'threshold': 0.5, 'bootstrap': {'unit': 'group_id', 'resamples': 300, 'seed': 1729},
        'latest_origin': '2026-08-01', 'horizon_months': 3,
        'scope': 'Synthetic retrospective probability estimate of target_deficit_3m proxy only; not calibrated, default, official health score, cash balance or causal diagnosis',
        'runtime': {'python': platform.python_version(), **{name: version(name) for name in (
            'duckdb', 'scikit-learn', 'numpy', 'scipy', 'joblib', 'cloudpickle', 'threadpoolctl')}},
    }


def freeze_experiment():
    if EXPERIMENT_DIR.exists():
        raise FileExistsError('Experiment exists; inspect partial history, never auto-overwrite')
    frozen = read_prepared(PROTOCOL_DIR)
    if sha256_file(PROTOCOL_DIR / 'prepared.json') != PREPARED_SHA256:
        raise ValueError('Approved T2 receipt changed')
    verify_contracts(frozen)
    gate = verify_gate()
    old = frozen['protocol']['binding']
    for name in ('run_manifest_sha256', 'current_pointer_sha256', 'input_sha256', 'source_sha256'):
        if gate['binding'][name] != old[name]:
            raise ValueError('T2 input binding changed')
    EXPERIMENT_DIR.mkdir(exist_ok=False)
    write_json(EXPERIMENT_DIR / 'config.json', configuration())
    write_json(EXPERIMENT_DIR / 'freeze.json', {
        'status': 'frozen_before_development_labels', 'development_label_values_fetched': 0,
        'config_sha256': sha256_file(EXPERIMENT_DIR / 'config.json'),
        'prepared_sha256': PREPARED_SHA256, 'gate': gate,
        'source_sha256': artifact_hashes(ROOT, MODEL_SOURCES),
    })
    return {'status': 'frozen', 'config_sha256': sha256_file(EXPERIMENT_DIR / 'config.json'),
            'freeze_sha256': sha256_file(EXPERIMENT_DIR / 'freeze.json')}


def verify_experiment():
    frozen = read_prepared(PROTOCOL_DIR)
    verify_contracts(frozen)
    freeze = read_json(EXPERIMENT_DIR / 'freeze.json')
    check_hashes(PROTOCOL_DIR, {'prepared.json': PREPARED_SHA256})
    check_hashes(EXPERIMENT_DIR, {'config.json': freeze['config_sha256']})
    check_hashes(ROOT, freeze['source_sha256'])
    if read_json(EXPERIMENT_DIR / 'config.json') != configuration():
        raise ValueError('Experiment configuration changed')
    if freeze['gate'] != verify_gate():
        raise ValueError('Experiment input/gate binding changed')
    return read_json(EXPERIMENT_DIR / 'config.json'), frozen


def matrix(rows, feature_order):
    if list(feature_order) != list(FEATURE_NAMES):
        raise ValueError('Feature order changed')
    x = np.array([[row['features'][name] if row['features'][name] is not None else np.nan
                   for name in feature_order] for row in rows], dtype=float).reshape(-1, len(FEATURE_NAMES))
    if np.isinf(x).any():
        raise ValueError('Infinite predictor')
    return x


def load_features(con, split):
    columns = ','.join('f.' + name for name in FEATURE_NAMES)
    result = con.execute('''SELECT f.company_id,f.group_id,f.month,f.known_as_of,
        k.label_available_at,f.coverage_json,f.missing_reason_json,''' + columns + '''
        FROM read_parquet(?) f JOIN read_parquet(?) k USING(company_id,group_id,month)
        WHERE f.eligible ORDER BY f.company_id,f.month''',
        [str(PROTOCOL_DIR / 'features.parquet'), str(PROTOCOL_DIR / f'splits/{split}.parquet')])
    names = [column[0] for column in result.description]
    rows = []
    assignment = read_json(PROTOCOL_DIR / 'group_assignment.json')
    for values in result.fetchall():
        row = dict(zip(names, values))
        row['features'] = {name: row.pop(name) for name in FEATURE_NAMES}
        row['coverage'] = json.loads(row.pop('coverage_json'))
        row['missing_reason'] = json.loads(row.pop('missing_reason_json'))
        if split not in matching_splits(assignment[row['group_id']], row['month']):
            raise ValueError('Key outside frozen group/date/maturity scope')
        rows.append(row)
    count = con.execute('SELECT count(*) FROM read_parquet(?)',
                        [str(PROTOCOL_DIR / f'splits/{split}.parquet')]).fetchone()[0]
    if len(rows) != count or len({(r['company_id'], r['month']) for r in rows}) != count:
        raise ValueError('Feature/key coverage differs')
    return rows


def assert_pair(train, evaluation):
    if not train or not evaluation:
        raise ValueError('Empty split')
    if ({(r['company_id'], r['month']) for r in train} &
            {(r['company_id'], r['month']) for r in evaluation}):
        raise ValueError('Overlapping keys')
    if {r['group_id'] for r in train} & {r['group_id'] for r in evaluation}:
        raise ValueError('Overlapping groups')
    if max(as_date(r['label_available_at']) for r in train) >= min(as_date(r['month']) for r in evaluation):
        raise ValueError('Training label maturity violates forward purge')


def observed_sample(labels, features):
    keyed = {(r['company_id'], r['group_id'], r['month']): r for r in features}
    if len(labels) != len(keyed) or len(labels) != len(features):
        raise ValueError('Label-feature key coverage differs')
    seen, rows, y = set(), [], []
    for label in labels:
        key = (label['company_id'], label['group_id'], label['month'])
        if key not in keyed or key in seen:
            raise ValueError('Label key differs or duplicated')
        seen.add(key)
        if label['value'] is not None:
            if label['value'] not in (True, False, 0, 1):
                raise ValueError('Nonbinary label')
            rows.append(keyed[key])
            y.append(int(label['value']))
    return rows, matrix(rows, FEATURE_NAMES), np.asarray(y, dtype=int)


def seal_json(name, value):
    path = EXPERIMENT_DIR / name
    write_json(path, value)
    digest = sha256_file(path)
    write_json(EXPERIMENT_DIR / (name + '.integrity.json'), {'sha256': digest})
    return digest


def verify_sealed(name):
    check_hashes(EXPERIMENT_DIR, {name: read_json(EXPERIMENT_DIR / (name + '.integrity.json'))['sha256']})
    return read_json(EXPERIMENT_DIR / name)


def reserve_evaluation(base, required_hashes):
    check_hashes(base, required_hashes)
    path = base / 'evaluation_receipt.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'consumed_before_holdout_read', 'artifacts_sha256': required_hashes,
                   'retry': 'Forbidden even after interruption; inspect receipt and partial artifacts'},
                  stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return sha256_file(path)


def verify_local_model(directory, expected_manifest_sha256):
    directory = Path(directory)
    if directory.resolve() != EXPERIMENT_DIR or directory.is_symlink():
        raise ValueError('Only the fixed trusted local experiment model can be loaded; no external pickle')
    check_hashes(directory, {'model_manifest.json': expected_manifest_sha256})
    manifest = read_json(directory / 'model_manifest.json')
    if manifest['feature_order'] != list(FEATURE_NAMES):
        raise ValueError('Model feature order changed')
    check_hashes(directory, manifest['artifacts_sha256'])
    check_hashes(ROOT, manifest['source_sha256'])
    check_hashes(PROTOCOL_DIR, {'prepared.json': PREPARED_SHA256})
    verify_contracts(read_prepared(PROTOCOL_DIR))
    if manifest['runtime'] != configuration()['runtime']:
        raise ValueError('Model dependency runtime differs')
    return joblib.load(directory / 'model.joblib'), manifest
