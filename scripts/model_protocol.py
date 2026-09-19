import argparse
import hashlib
import json
import platform
from collections import Counter
from pathlib import Path

import duckdb

from scripts.model_features import (
    FEATURE_NAMES, SOURCE_COLUMNS, as_date, build_features, feature_contract,
    last_day, load_panel, shift_month,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ('scripts/model_features.py', 'scripts/model_protocol.py',
                'tests/test_model_features.py', 'tests/test_model_protocol.py')
PANEL_PATH = 'data/marts/panel_flujos.parquet'
TARGET_PATH = 'data/marts/targets_proxy.parquet'
MIN_SUPPORT = {'rows_per_class': 20, 'groups_per_class': 5, 'groups_total': 10}
SPLIT_SPECS = {
    'fold1_train': {'groups': ['train'], 'labels_as_of': '2025-06-30', 'before': '2025-07-01'},
    'fold1_validation': {'groups': ['validation'], 'start': '2025-07-01', 'end': '2025-09-01', 'labels_as_of': '2025-12-31'},
    'fold2_train': {'groups': ['train'], 'labels_as_of': '2025-09-30', 'before': '2025-10-01'},
    'fold2_validation': {'groups': ['validation'], 'start': '2025-10-01', 'end': '2025-12-01', 'labels_as_of': '2026-03-31'},
    'final_train': {'groups': ['train', 'validation'], 'labels_as_of': '2026-03-31', 'before': '2026-04-01'},
    'final_test': {'groups': ['final_test'], 'start': '2026-04-01', 'end': '2026-05-01', 'labels_as_of': '2026-08-31'},
}


def sha256_file(file):
    digest = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(file, value):
    with Path(file).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str, allow_nan=False)
        stream.write('\n')


def assign_groups(groups):
    universe = set(groups)
    if any(not isinstance(group, str) or not group for group in universe):
        raise ValueError('Nonempty string group IDs required')
    ordered = sorted(universe, key=lambda group: (
        hashlib.sha256(('deficit-v1:' + group).encode('utf-8')).hexdigest(), group))
    n = len(ordered)
    if n < 5:
        raise ValueError('At least five groups required for nonempty partitions')
    return {group: 'train' if i < 3 * n // 5 else 'validation' if i < 4 * n // 5 else 'final_test'
            for i, group in enumerate(ordered)}


def protocol_contract():
    return {
        'protocol_version': 'deficit-v1', 'status': 'frozen_before_label_inspection',
        'scope': 'Approved synthetic retrospective proxy only; not official score, default or causal health diagnosis',
        'target': {'name': 'target_deficit_3m', 'version': 1, 'positive': 'Robust operating deficit in at least 2 of the next 3 calendar months',
                   'availability': 'last_day(origin + 3 months)',
                   'censoring': 'Unknown/immature/unobservable/sensitive labels remain NULL, never negative; inference eligibility is independent'},
        'group_assignment': {'hash': "sha256(UTF-8('deficit-v1:' + group_id))", 'order': 'ascending digest, group_id tie-break',
                             'universe': 'All group IDs in bound panel, before eligibility filtering',
                             'boundaries': 'train ranks [0,floor(0.6*N)); validation [floor(0.6*N),floor(0.8*N)); remainder final_test',
                             'ids_are_predictors': False},
        'splits': SPLIT_SPECS,
        'selection': {'required_folds': ['fold1', 'fold2'],
                      'reason_two_not_five': 'Only 24 closed months; three-month label maturity purge plus group holdout leaves two defensible chronological validation windows',
                      'rule': 'Both metrics must pass on BOTH folds, not a pooled average; support must pass in each training and evaluation sample',
                      'last_validation_label_matures': '2026-03-31',
                      'forbidden': ['random_cross_validation', 'random_early_stopping', 'holdout_guided_features_or_cutoffs'],
                      'preprocessing': 'Fit transforms, missing-value imputation, model and constant prevalence baseline on training split only'},
        'final_evaluation': {'rule': 'Select and freeze model using development folds first; train on final_train; evaluate final_test exactly once afterwards',
                             'outcomes_sealed_in_T2': True, 'final_loader': 'Not exposed by T2; T3 must require a persisted selected-model hash and one-shot evaluation record',
                             'known_as_of': 'last_day(each test origin), not final training date'},
        'acceptance': {'pr_auc': 'average_precision > evaluation_prevalence',
                       'brier_max_baseline_ratio': 0.95,
                       'baseline': 'Constant p=mean(y_train); Brier baseline=mean((y_eval-p)^2), never fitted on evaluation',
                       'required_on': ['fold1_validation', 'fold2_validation', 'final_test'],
                       'insufficient_support': 'blocked; no metrics or success claim; no relaxing cuts after outcomes'},
        'minimum_support': MIN_SUPPORT,
        'support_rationale': 'Predeclared conservative feasibility floor, not a power claim: >=20 labeled rows and >=5 distinct groups in each class, >=10 total groups in both train and evaluation',
        'preparation': 'Keys and contracts frozen without reading any target column; feature values and structural missingness only; dates of label availability calculated from calendar',
    }


def matching_splits(group_partition, month):
    month = as_date(month)
    maturity = last_day(shift_month(month, 3))
    return [name for name, spec in SPLIT_SPECS.items()
            if group_partition in spec['groups'] and maturity <= as_date(spec['labels_as_of'])
            and ('before' not in spec or month < as_date(spec['before']))
            and ('start' not in spec or as_date(spec['start']) <= month <= as_date(spec['end']))]


def build_split_keys(features, groups):
    splits = {name: [] for name in SPLIT_SPECS}
    seen = set()
    for record in sorted(features, key=lambda row: (row['company_id'], row['month'])):
        key = (record['company_id'], record['month'])
        if key in seen:
            raise ValueError('Duplicate feature origin')
        seen.add(key)
        if not record['eligible']:
            continue
        for name in matching_splits(groups[record['group_id']], record['month']):
            splits[name].append({key: record[key] for key in ('company_id', 'group_id', 'month', 'known_as_of')} |
                                {'label_available_at': last_day(shift_month(record['month'], 3))})
    return splits


def bind_inputs(root):
    root = Path(root).resolve()
    pointer_path = root / 'reports/current.json'
    pointer = json.loads(pointer_path.read_text())
    run = (root / pointer['workspace']).resolve()
    if not run.is_relative_to(root / 'data/runs'):
        raise ValueError('Published run escapes local immutable runs')
    manifest_path = run / 'manifest.json'
    manifest_hash = sha256_file(manifest_path)
    if manifest_hash != pointer['manifest_sha256']:
        raise ValueError('Published manifest hash mismatch')
    manifest = json.loads(manifest_path.read_text())
    for name, expected in {'target_version': 1, 'target_horizon_months': 3, 'min_deficit_months': 2,
                           'dataset_end': '2026-08-31'}.items():
        if manifest['parameters'].get(name) != expected:
            raise ValueError(f'Unexpected frozen target parameter: {name}')
    files = {}
    for relative in (PANEL_PATH, TARGET_PATH):
        file = run / relative
        actual = sha256_file(file)
        if manifest['outputs'][relative] != actual:
            raise ValueError(f'Input hash mismatch: {relative}')
        files[relative] = actual
    binding = {'run_id': manifest['run_id'], 'run_path': str(run),
               'run_manifest_sha256': manifest_hash, 'current_pointer_sha256': sha256_file(pointer_path),
               'input_sha256': files, 'raw_input_sha256': manifest['inputs'],
               'source_sha256': {name: sha256_file(ROOT / name) for name in SOURCE_FILES},
               'python': platform.python_version(), 'duckdb': duckdb.__version__,
               'target_access': 'Byte-level SHA only in prepare; target parquet is never queried or parsed'}
    return run, binding


def export_parquet(con, file, schema, rows):
    if file.exists():
        raise FileExistsError(file)
    con.execute('DROP TABLE IF EXISTS model_export')
    con.execute('CREATE TEMP TABLE model_export (' + ','.join(f'{name} {dtype}' for name, dtype in schema) + ')')
    if rows:
        con.executemany('INSERT INTO model_export VALUES (' + ','.join('?' for _ in schema) + ')', rows)
    con.execute('COPY model_export TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(file)])


def prepare(root=ROOT, output=None):
    root = Path(root).resolve()
    output = Path(output or root / 'reports/modeling/protocol-v1').resolve()
    if output.exists():
        raise FileExistsError(f'Frozen directory already exists: {output}; use a new explicitly versioned directory')
    if not output.is_relative_to(root / 'reports/modeling'):
        raise ValueError('Preparation must use a new local reports/modeling directory, never an immutable run')
    run, binding = bind_inputs(root)
    with duckdb.connect() as con:
        con.execute('SET threads=4')
        panel = load_panel(con, run / PANEL_PATH)
        groups = assign_groups(row['group_id'] for row in panel)
        origins = [(row['company_id'], row['month']) for row in panel
                   if matching_splits(groups[row['group_id']], row['month'])]
        features = build_features(panel, origins)
        splits = build_split_keys(features, groups)
        schema = con.execute('DESCRIBE SELECT ' + ','.join(SOURCE_COLUMNS) +
                             ' FROM read_parquet(?)', [str(run / PANEL_PATH)]).fetchall()
        binding['panel_schema'] = {row[0]: row[1] for row in schema}
        output.mkdir(parents=True, exist_ok=False)
        (output / 'splits').mkdir()
        protocol = protocol_contract() | {'binding': binding}
        contract = feature_contract() | {'binding': binding}
        write_json(output / 'protocol.json', protocol)
        write_json(output / 'feature_contract.json', contract)
        write_json(output / 'group_assignment.json', groups)
        key_schema = [('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
                      ('known_as_of', 'DATE'), ('label_available_at', 'DATE')]
        for name, keys in splits.items():
            export_parquet(con, output / f'splits/{name}.parquet', key_schema,
                           [tuple(key[field] for field, _ in key_schema) for key in keys])
        feature_schema = key_schema[:4] + [('eligible', 'BOOLEAN'), ('coverage_json', 'VARCHAR'),
                                          ('missing_reason_json', 'VARCHAR')] + [(name, 'DOUBLE') for name in FEATURE_NAMES]
        export_parquet(con, output / 'features.parquet', feature_schema, [
            (row['company_id'], row['group_id'], row['month'], row['known_as_of'], row['eligible'],
             json.dumps(row['coverage'], sort_keys=True), json.dumps(row['missing_reason'], sort_keys=True)) +
            tuple(row['features'][name] for name in FEATURE_NAMES) for row in features])
    coverage = {
        'report_type': 'structural_only_no_outcomes', 'group_counts': dict(Counter(groups.values())),
        'feature_rows': len(features), 'eligible_feature_rows': sum(row['eligible'] for row in features),
        'ineligibility_reasons': dict(Counter(reason for row in features for reason in row['coverage']['eligibility_reasons'])),
        'missing_by_feature': {name: sum(row['features'][name] is None for row in features) for name in FEATURE_NAMES},
        'splits': {name: {'rows': len(keys), 'groups': len({key['group_id'] for key in keys}),
                          'companies': len({key['company_id'] for key in keys}),
                          'origin_min': min((key['month'] for key in keys), default=None),
                          'origin_max': max((key['month'] for key in keys), default=None),
                          'maturity_max': max((key['label_available_at'] for key in keys), default=None)}
                   for name, keys in splits.items()},
        'label_support': 'NOT_INSPECTED; minimum class support is a mandatory T3 gate before metrics',
        'structural_blockers': [name for name, keys in splits.items()
                                if len({key['group_id'] for key in keys}) < MIN_SUPPORT['groups_total']],
    }
    write_json(output / 'coverage.json', coverage)
    artifacts = {file.relative_to(output).as_posix(): sha256_file(file)
                 for file in sorted(output.rglob('*')) if file.is_file()}
    receipt = {'status': 'prepared_not_trained', 'label_values_fetched': 0,
               'binding': binding, 'artifacts_sha256': artifacts,
               'structural_blockers': coverage['structural_blockers'],
               'freeze_order': ['contracts', 'group_assignment', 'split_keys', 'features', 'coverage', 'receipt'],
               'g_data': 'Run xray.model_audit --verify after source changes and require pass before T3'}
    write_json(output / 'prepared.json', receipt)
    return receipt


def read_prepared(output):
    """Verify the preparation receipt and return contracts; never query outcomes."""
    output = Path(output).resolve()
    receipt = json.loads((output / 'prepared.json').read_text())
    for relative, expected in receipt['artifacts_sha256'].items():
        file = (output / relative).resolve()
        if not file.is_relative_to(output) or sha256_file(file) != expected:
            raise ValueError(f'Frozen artifact mismatch: {relative}')
    return {'receipt': receipt,
            'protocol': json.loads((output / 'protocol.json').read_text()),
            'feature_contract': json.loads((output / 'feature_contract.json').read_text())}


def load_development_labels(con, output, split_name):
    """T3 only: fetch keyed nullable labels after group/origin/maturity SQL filtering."""
    if split_name == 'final_test':
        raise PermissionError('Held-out outcomes sealed: a selected-model/one-shot final evaluation guard is required in T3')
    if split_name not in SPLIT_SPECS:
        raise ValueError('Unknown development split')
    output = Path(output)
    frozen = read_prepared(output)
    binding = frozen['protocol']['binding']
    run = Path(binding['run_path'])
    if sha256_file(run / 'manifest.json') != binding['run_manifest_sha256']:
        raise ValueError('Bound run manifest changed')
    for relative, expected in binding['input_sha256'].items():
        if sha256_file(run / relative) != expected:
            raise ValueError('Bound model input changed')
    for relative, expected in binding['source_sha256'].items():
        if sha256_file(ROOT / relative) != expected:
            raise ValueError('Frozen preparation code changed')
    cutoff = frozen['protocol']['splits'][split_name]['labels_as_of']
    result = con.execute('''WITH selected_keys AS MATERIALIZED (
            SELECT company_id,group_id,month,label_available_at FROM read_parquet(?)
        ), scoped_targets AS MATERIALIZED (
            SELECT t.company_id,t.group_id,t.month,t.target_deficit_3m,t.available_at_deficit,t.target_version
            FROM read_parquet(?) t INNER JOIN selected_keys k
              ON t.company_id=k.company_id AND t.group_id=k.group_id AND t.month=k.month
            WHERE t.available_at_deficit<=CAST(? AS DATE)
              AND t.available_at_deficit=k.label_available_at
        )
        SELECT k.company_id,k.group_id,k.month,t.target_deficit_3m AS value,
               t.available_at_deficit AS available_at,t.target_version
        FROM selected_keys k LEFT JOIN scoped_targets t USING(company_id,group_id,month)
        ORDER BY k.company_id,k.month''',
        [str(output / f'splits/{split_name}.parquet'), str(run / TARGET_PATH), cutoff])
    names = [column[0] for column in result.description]
    labels = [dict(zip(names, row)) for row in result.fetchall()]
    if any(row['target_version'] != 1 for row in labels):
        raise ValueError('Missing, immature or wrong-version label record; never impute an outcome')
    if len(labels) != len({(row['company_id'], row['month']) for row in labels}):
        raise ValueError('Duplicate target keys')
    return labels


def support_gate(labels):
    """Call separately on each train/evaluation sample BEFORE any metric computation."""
    observed = [row for row in labels if row['value'] is not None]
    if any(row['value'] not in (True, False, 0, 1) or not row['group_id'] for row in observed):
        raise ValueError('Binary nullable outcomes and nonempty group IDs required')
    classes = {str(value): {'rows': sum(row['value'] == value for row in observed),
                           'groups': len({row['group_id'] for row in observed if row['value'] == value})}
               for value in (0, 1)}
    groups = len({row['group_id'] for row in observed})
    return {'passed': groups >= MIN_SUPPORT['groups_total'] and all(
                stats['rows'] >= MIN_SUPPORT['rows_per_class'] and stats['groups'] >= MIN_SUPPORT['groups_per_class']
                for stats in classes.values()),
            'classes': classes, 'groups': groups, 'censored': len(labels) - len(observed),
            'minimum': MIN_SUPPORT.copy()}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Freeze retrospective deficit-v1 features/splits without outcomes or training')
    parser.add_argument('command', choices=['prepare'])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    receipt = prepare(args.root, args.output)
    output = (args.output or args.root / 'reports/modeling/protocol-v1').resolve()
    print(json.dumps({'status': receipt['status'], 'output': str(output), 'label_values_fetched': 0,
                      'prepared_sha256': sha256_file(output / 'prepared.json'),
                      'structural_blockers': receipt['structural_blockers']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
