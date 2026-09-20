import argparse
import contextlib
import importlib.metadata
import json
import os
import platform
import shutil
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts import trajectory_contract as tc
from scripts.trajectory_events import evaluate_period, partition_groups
from scripts.trajectory_metrics import bootstrap
from scripts.trajectory_signals import INPUT_COLUMNS, build_trajectory


PROTOCOL_SHA256 = 'd37ee0cad9101b4e6ae1dbc39f69d471ff7457b2cf3c5dac8ac5bd786c70159d'
IMPLEMENTATION_FILES = tuple(f'scripts/trajectory_{name}.py' for name in ('contract', 'signals', 'events', 'metrics', 'backtest')) + tuple(
    f'tests/test_trajectory_{name}.py' for name in ('contract', 'signals', 'events', 'metrics', 'backtest')) + (
    'scripts/model_features.py', 'requirements.txt', 'requirements-model.txt')
PROTECTED_FILES = tuple(f'{tc.MODEL}/{name}' for name in (
    'model.joblib', 'model_manifest.json', 'freeze.json', 'development_receipt.json',
    'final_training_receipt.json', 'evaluation_receipt.json', 'final_test_predictions.csv',
    'latest_predictions.json', 'latest_predictions.json.integrity.json'))
MANIFEST = 'execution-manifest.json'
PHASES = ('development', 'reserved')
PHASE_ARTIFACTS = {
    phase: [f'{phase}-{name}' for name in ('events.json', 'alerts.json', 'event-windows.parquet', 'group-diagnostics.json', 'report.json')]
    + (['development-cases.json', 'development-case-history.parquet'] if phase == 'development' else [])
    for phase in PHASES}


def output_path(root, filename):
    tc.require('/' not in filename and filename not in ('.', '..'), 'Invalid output filename')
    return tc.inside(Path(root), f'{tc.DIRECTORY}/{filename}')


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sidecar(path):
    with Path(str(path) + '.sha256').open('x', encoding='utf-8') as stream:
        stream.write(tc.digest(path) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(path.parent)


def write_json(path, value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _sidecar(path)


def verified_hash(path):
    actual = tc.digest(path)
    tc.require(Path(str(path) + '.sha256').read_text(encoding='utf-8') == actual + '\n', f'Output hash mismatch: {path.name}')
    return actual


def environment():
    names = ('duckdb', 'numpy', 'scikit-learn', 'scipy', 'joblib', 'cloudpickle', 'threadpoolctl')
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    tc.require(versions['duckdb'] is not None, 'Existing DuckDB dependency unavailable')
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'system': platform.system(), 'machine': platform.machine(), 'dependencies': versions}


def output_names():
    return {phase: [f'{phase}-access.json', *PHASE_ARTIFACTS[phase], f'{phase}-completion.json'] for phase in PHASES}


def contract_proof(root):
    tc.require(tc.inside(root, tc.RECEIPT).is_file(), 'Existing protocol receipt required; runner never creates it')
    tc.require(tc.digest(tc.inside(root, tc.PROTOCOL)) == PROTOCOL_SHA256, 'Canonical protocol hash mismatch')
    return tc.check(root)


def freeze(root=tc.ROOT):
    root = Path(root).resolve()
    target = output_path(root, MANIFEST)
    if target.exists():
        raise FileExistsError('Execution manifest already exists; do not overwrite')
    tc.require(all(not output_path(root, name).exists() and not Path(str(output_path(root, name)) + '.sha256').exists()
                   for names in output_names().values() for name in names), 'Cannot freeze after prior phase records')
    proof = contract_proof(root)
    included, excluded = partition_groups(tc.read_json(tc.inside(root, tc.GROUPS)))
    manifest = {'protocol_version': 'trajectory-v2', 'protocol_sha256': proof['protocol_sha256'],
                'protocol_receipt_sha256': proof['receipt_sha256'], 'input_sha256': proof['input_sha256'],
                'implementation_sha256': {name: tc.digest(tc.inside(root, name)) for name in IMPLEMENTATION_FILES},
                'protected_sha256': {name: tc.digest(tc.inside(root, name)) for name in PROTECTED_FILES},
                'environment': environment(), 'included_groups': included, 'excluded_groups': excluded,
                'outputs': output_names(), 'sidecars': 'Every artifact has an exclusive SHA256 .sha256 sidecar',
                'created_at': datetime.now(timezone.utc).isoformat(), 'outcome_values_read': 0,
                'interruption_policy': 'Receipt is consumed before access; inspect outputs, never delete or replay',
                'bootstrap': {'replicates': 1000, 'seed': 1729, 'algorithm': 'stdlib Random MT19937 shared group multiplicities'},
                'scope': 'Metadata and byte hashes only; no database connection or outcome queries'}
    write_json(target, manifest)
    return {'status': 'frozen', 'manifest_sha256': verified_hash(target), 'outcome_values_read': 0,
            'included_groups': len(included), 'excluded_groups': len(excluded)}


def verify_execution(root):
    root = Path(root).resolve()
    manifest_sha = verified_hash(output_path(root, MANIFEST))
    manifest = tc.read_json(output_path(root, MANIFEST))
    proof = contract_proof(root)
    tc.require(manifest['protocol_sha256'] == proof['protocol_sha256'] and
               manifest['protocol_receipt_sha256'] == proof['receipt_sha256'] and
               manifest['input_sha256'] == proof['input_sha256'], 'Execution input binding mismatch')
    tc.require(set(manifest['implementation_sha256']) == set(IMPLEMENTATION_FILES) and
               set(manifest['protected_sha256']) == set(PROTECTED_FILES), 'Execution file set mismatch')
    for key in ('implementation_sha256', 'protected_sha256'):
        for name, expected in manifest[key].items():
            tc.require(tc.digest(tc.inside(root, name)) == expected, f'Frozen hash mismatch: {name}; existing evidence invalid, retain records')
    included, excluded = partition_groups(tc.read_json(tc.inside(root, tc.GROUPS)))
    tc.require(manifest['included_groups'] == included and manifest['excluded_groups'] == excluded, 'Frozen group mismatch')
    tc.require(manifest['outputs'] == output_names(), 'Frozen output names mismatch')
    tc.require(manifest['environment'] == environment(), 'Frozen runtime/dependency mismatch')
    return {**manifest, 'manifest_sha256': manifest_sha}


def access_record(manifest, protocol, phase):
    return {'phase': phase, 'manifest_sha256': manifest['manifest_sha256'],
            'protocol_sha256': manifest['protocol_sha256'], 'implementation_sha256': manifest['implementation_sha256'],
            'period': protocol['evaluation'][phase], 'consumed': True,
            'policy': 'Exclusive durable receipt before first DB connection. Interruption consumes access; never replay.'}


def verify_access(root, manifest, protocol, phase):
    path = output_path(root, f'{phase}-access.json')
    verified_hash(path)
    record = tc.read_json(path)
    tc.require({key: record[key] for key in access_record(manifest, protocol, phase)} == access_record(manifest, protocol, phase),
               'Access receipt binding mismatch')


@contextlib.contextmanager
def readonly_connection():
    import duckdb
    with tempfile.TemporaryDirectory(prefix='trajectory-readonly-') as directory:
        database = str(Path(directory) / 'empty.duckdb')
        duckdb.connect(database).close()
        with duckdb.connect(database, read_only=True) as connection:
            yield connection


def load_panel(root, protocol, phase):
    tc.require(phase in PHASES, 'Unknown loader phase')
    root = Path(root).resolve()
    manifest = verify_execution(root)
    tc.require(protocol == tc.read_json(tc.inside(root, tc.PROTOCOL)), 'Pinned protocol mismatch in loader')
    verify_access(root, manifest, protocol, phase)
    included, _ = partition_groups(tc.read_json(tc.inside(root, tc.GROUPS)))
    panel = tc.inside(root, f'data/runs/{protocol["source"]["run_id"]}/{tc.PANEL}')
    placeholders = ', '.join('?' for _ in included)
    source = ' FROM read_parquet(?) WHERE group_id IN (' + placeholders + ')'
    first = protocol['source']['calendar']['first_month']
    cutoff = protocol['evaluation'][phase]['outcomes_through']
    with readonly_connection() as connection:
        query = connection.execute('SELECT company_id, group_id' + source + ' AND month = ? ORDER BY company_id',
                                   [str(panel), *included, first])
        tc.require([item[0] for item in query.description] == ['company_id', 'group_id'], 'Unexpected identity projection')
        universe = {}
        for company, group in query.fetchall():
            tc.require(company not in universe and isinstance(company, str) and company and group in included, 'Invalid company universe')
            universe[company] = group
        phase_filter = ' AND month >= ? AND month <= ? ORDER BY company_id, month'
        parameters = [str(panel), *included, first, cutoff]
        query = connection.execute('SELECT company_id, group_id, month' + source + phase_filter, parameters)
        tc.require([item[0] for item in query.description] == ['company_id', 'group_id', 'month'], 'Unexpected phase identity projection')
        identities = set()
        for company, group, month in query.fetchall():
            key = company, str(month)
            tc.require(key not in identities and company in universe and group == universe[company]
                       and first <= str(month) <= cutoff and date.fromisoformat(str(month)).day == 1, 'Invalid phase identity')
            identities.add(key)
        columns = sorted(INPUT_COLUMNS)
        query = connection.execute('SELECT ' + ', '.join(f'"{column}"' for column in columns) + source + phase_filter, parameters)
        tc.require([item[0] for item in query.description] == columns, 'Unexpected financial projection')
        rows = [dict(zip(columns, (float(value) if isinstance(value, Decimal) else value for value in row))) for row in query.fetchall()]
    tc.require(len(rows) == len(identities) and {(row['company_id'], str(row['month'])) for row in rows} == identities and
               all(row['company_id'] in universe and row['group_id'] == universe[row['company_id']]
                   and first <= str(row['month']) <= cutoff for row in rows), 'Unexpected identity or phase cutoff in input')
    return rows, universe


def write_parquet(path, rows):
    import duckdb
    with tempfile.TemporaryDirectory(prefix='trajectory-output-') as directory:
        temporary = Path(directory) / 'rows.parquet'
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE evidence (company_id VARCHAR, group_id VARCHAR, month VARCHAR, row_json VARCHAR)')
            values = [(row['company_id'], row['group_id'], row.get('month', row.get('onset_month')),
                       json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)) for row in rows]
            if values:
                connection.executemany('INSERT INTO evidence VALUES (?, ?, ?, ?)', values)
            connection.execute('COPY evidence TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(temporary)])
        with path.open('xb') as target, temporary.open('rb') as source:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
    _sidecar(path)


def _phase_status(root, manifest, phase):
    names = manifest['outputs'][phase]
    present = [name for name in names if output_path(root, name).exists()]
    if not present:
        return 'not_started'
    for name in present:
        verified_hash(output_path(root, name))
    protocol = tc.read_json(tc.inside(root, tc.PROTOCOL))
    verify_access(root, manifest, protocol, phase)
    completion_path = output_path(root, f'{phase}-completion.json')
    if not completion_path.exists():
        return 'consumed_incomplete_inspect_do_not_replay'
    completion = tc.read_json(completion_path)
    expected = [f'{phase}-access.json', *PHASE_ARTIFACTS[phase]]
    tc.require(completion['manifest_sha256'] == manifest['manifest_sha256'] and
               set(completion['outputs_sha256']) == set(expected), 'Completion binding mismatch')
    for name, expected_hash in completion['outputs_sha256'].items():
        tc.require(verified_hash(output_path(root, name)) == expected_hash, 'Completion output hash mismatch')
    return 'complete'


def verify(root=tc.ROOT):
    root = Path(root).resolve()
    manifest = verify_execution(root)
    return {'status': 'verified', 'manifest_sha256': manifest['manifest_sha256'], 'outcome_values_read': 0,
            'phases': {phase: _phase_status(root, manifest, phase) for phase in PHASES}}


def run_phase(root, phase):
    tc.require(phase in PHASES, 'Unknown execution phase')
    root = Path(root).resolve()
    if output_path(root, f'{phase}-access.json').exists():
        raise FileExistsError('Phase receipt already consumed; inspect, never replay')
    manifest = verify_execution(root)
    if phase == 'reserved':
        tc.require(_phase_status(root, manifest, 'development') == 'complete', 'Completed development evidence required before reserve')
    for name in manifest['outputs'][phase]:
        tc.require(not output_path(root, name).exists() and not Path(str(output_path(root, name)) + '.sha256').exists(),
                   'Existing phase output blocks access; inspect, never overwrite')
    protocol = tc.read_json(tc.inside(root, tc.PROTOCOL))
    write_json(output_path(root, f'{phase}-access.json'),
               {**access_record(manifest, protocol, phase), 'created_at': datetime.now(timezone.utc).isoformat()})
    rows, universe = load_panel(root, protocol, phase)
    trajectory = build_trajectory(rows, protocol, protocol['evaluation'][phase]['outcomes_through'], company_groups=universe)
    result = evaluate_period(trajectory, protocol, tc.read_json(tc.inside(root, tc.GROUPS)), phase)
    result['bootstrap'] = bootstrap(result['group_counts'], manifest['included_groups'])
    write_json(output_path(root, f'{phase}-events.json'), result.pop('events'))
    write_json(output_path(root, f'{phase}-alerts.json'), result.pop('alerts'))
    write_parquet(output_path(root, f'{phase}-event-windows.parquet'), result.pop('event_windows'))
    write_json(output_path(root, f'{phase}-group-diagnostics.json'),
               {'scope': result['interpretation'], 'groups': result.pop('group_counts')})
    cases = result.pop('cases')
    if phase == 'development':
        write_json(output_path(root, 'development-cases.json'), cases)
        chosen = {case['company_id'] for case in cases.values() if case['status'] == 'available'}
        write_parquet(output_path(root, 'development-case-history.parquet'), [row for row in trajectory if row['company_id'] in chosen])
    result.update(manifest_sha256=manifest['manifest_sha256'], source_panel_sha256=protocol['source']['panel_sha256'],
                  exclusion_scope=protocol['evaluation']['exclusion_scope'],
                  input_projection=sorted(INPUT_COLUMNS), availability_assumption=protocol['source']['availability'])
    write_json(output_path(root, f'{phase}-report.json'), result)
    verify_execution(root)
    completion = {'phase': phase, 'manifest_sha256': manifest['manifest_sha256'],
                  'outputs_sha256': {name: verified_hash(output_path(root, name)) for name in [f'{phase}-access.json', *PHASE_ARTIFACTS[phase]]},
                  'protected_hashes_unchanged': True, 'completed_at': datetime.now(timezone.utc).isoformat()}
    write_json(output_path(root, f'{phase}-completion.json'), completion)
    return {'phase': phase, 'status': 'complete', 'coverage': result['coverage'], 'strata': result['strata'],
            'report': f'{phase}-report.json', 'protected_hashes_unchanged': True}


def argument_parser():
    parser = argparse.ArgumentParser(description='Sealed internal retrospective trajectory backtest; no v1 targets or fitting')
    parser.add_argument('command', choices=('freeze', 'develop', 'evaluate-reserved', 'verify'))
    parser.add_argument('--root', type=Path, default=tc.ROOT)
    return parser


def main():
    parser = argument_parser()
    args = parser.parse_args()
    try:
        if args.command == 'freeze':
            result = freeze(args.root)
        elif args.command == 'verify':
            result = verify(args.root)
        else:
            result = run_phase(args.root, {'develop': 'development', 'evaluate-reserved': 'reserved'}[args.command])
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except Exception as error:
        parser.exit(1, f'Trajectory operation blocked/interrupted ({type(error).__name__}); inspect retained receipts and outputs, never replay consumed access.\n')


if __name__ == '__main__':
    main()
