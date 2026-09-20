import argparse
import hashlib
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = 'reports/modeling/trajectory-v2'
PROTOCOL = f'{DIRECTORY}/protocol.json'
RECEIPT = f'{DIRECTORY}/protocol-receipt.json'
V1 = 'reports/modeling/protocol-v1'
MODEL = 'reports/modeling/experiment-v1'
GROUPS = f'{V1}/group_assignment.json'
PANEL = 'data/marts/panel_flujos.parquet'
RUN_ID = '20260919T124814Z-e5302b58'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inside(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and '..' not in relative.parts, 'Path escape')
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), 'Path escape')
    return path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate_rules(contract):
    expected = {
        'protocol_version': 'trajectory-v2',
        'source.run_id': RUN_ID,
        'source.run_relative_panel': PANEL,
        'source.calendar': {'first_month': '2024-09-01', 'last_month': '2026-08-01', 'months_per_company': 24},
        'direction.threshold': 0.05,
        'direction.prior_month_offsets': [-5, -4, -3],
        'direction.recent_month_offsets': [-2, -1, 0],
        'direction.improving': 'delta_lower>=0.05 in base AND trim',
        'direction.deteriorating': 'delta_upper<=-0.05 in base AND trim',
        'direction.stable': 'Entire delta interval strictly inside (-0.05,0.05) in base AND trim',
        'events.deterioration': 'non_deficit at s-2,s-1 then deficit at s,s+1',
        'events.improvement': 'deficit at s-2,s-1 then non_deficit at s,s+1',
        'evaluation.group_assignment': GROUPS,
        'evaluation.included_assignments': ['train', 'validation'],
        'evaluation.excluded_assignment': 'final_test',
        'evaluation.included_groups_by_v1_design': 200,
        'evaluation.excluded_groups_by_v1_design': 50,
        'evaluation.lead_months': [1, 2],
        'evaluation.development': {'alert_origin_first': '2025-03-01', 'alert_origin_last': '2025-09-01', 'outcomes_through': '2025-12-31'},
        'evaluation.reserved': {'alert_origin_first': '2026-03-01', 'alert_origin_last': '2026-05-01', 'outcomes_through': '2026-08-31', 'possible_event_onsets': ['2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01']},
        'reporting.bootstrap.replicates': 1000,
        'reporting.bootstrap.seed': 1729,
        'v1_probability_overlay.model_version': 'experiment-v1:hgb_leaf15',
        'v1_probability_overlay.training_and_selection_labels_through': '2026-03-31',
        'v1_probability_overlay.first_permitted_as_of': '2026-04-30',
        'v1_probability_overlay.last_permitted_as_of': '2026-08-31',
    }
    for key, value in expected.items():
        actual = contract
        for part in key.split('.'):
            actual = actual[part]
        require(json.dumps(actual, sort_keys=True) == json.dumps(value, sort_keys=True), f'Invalid rule: {key}')
    overlay = contract['v1_probability_overlay']
    dates = [date.fromisoformat(overlay[key]) for key in ('training_and_selection_labels_through', 'first_permitted_as_of', 'last_permitted_as_of')]
    require(dates[0] < dates[1] <= dates[2], 'Invalid probability cutoff')


def check(root=ROOT):
    root = Path(root).resolve()
    protocol, receipt = inside(root, PROTOCOL), inside(root, RECEIPT)
    protocol_sha = digest(protocol)
    if receipt.exists():
        require(read_json(receipt)['protocol_sha256'] == protocol_sha, 'Frozen protocol hash mismatch')
    contract = read_json(protocol)
    validate_rules(contract)
    source, overlay = contract['source'], contract['v1_probability_overlay']
    hashes = dict(contract['metadata_inputs_sha256'])
    for name in hashes:
        inside(root, name)
    require(set(hashes) == {f'{V1}/protocol.json', f'{V1}/feature_contract.json', GROUPS}, 'Unexpected metadata inputs')
    run_name = f'data/runs/{source["run_id"]}'
    run = inside(root, run_name)
    hashes.update({f'{run_name}/manifest.json': source['run_manifest_sha256'], f'{run_name}/{PANEL}': source['panel_sha256'],
                   f'{MODEL}/model.joblib': overlay['model_sha256'], f'{MODEL}/model_manifest.json': overlay['model_manifest_sha256']})
    for name, expected in hashes.items():
        path = inside(root, name)
        if name.startswith(f'{run_name}/'):
            require(path.is_relative_to(run), 'Run path escape')
        require(digest(path) == expected, f'Input hash mismatch: {name}')
    assignment = read_json(inside(root, GROUPS))
    require(all(isinstance(key, str) and key and value in ('train', 'validation', 'final_test') for key, value in assignment.items()), 'Invalid group assignment')
    counts = {label: list(assignment.values()).count(label) for label in ('train', 'validation', 'final_test')}
    require(counts == {'train': 150, 'validation': 50, 'final_test': 50}, 'Invalid group exclusions/counts')
    v1 = read_json(inside(root, f'{V1}/protocol.json'))
    cutoff = overlay['training_and_selection_labels_through']
    require(v1['selection']['last_validation_label_matures'] == v1['splits']['final_train']['labels_as_of'] == cutoff, 'V1 cutoff mismatch')
    model = read_json(inside(root, f'{MODEL}/model_manifest.json'))
    require(model['model_version'] == overlay['model_version'] and model['artifacts_sha256']['model.joblib'] == overlay['model_sha256'], 'Model binding mismatch')
    for binding in (v1['binding'], read_json(inside(root, f'{V1}/feature_contract.json'))['binding'], model['input_binding']):
        require(binding['run_id'] == source['run_id'] and binding['run_manifest_sha256'] == source['run_manifest_sha256'] and binding['input_sha256'][PANEL] == source['panel_sha256'], 'Source binding mismatch')
        require(Path(binding['run_path']).resolve() == run, 'Bound run path mismatch')
    manifest = read_json(inside(root, f'{run_name}/manifest.json'))
    require(manifest['run_id'] == source['run_id'] and manifest['outputs'][PANEL] == source['panel_sha256'], 'Run manifest link mismatch')
    proof = {'protocol_version': contract['protocol_version'], 'protocol_sha256': protocol_sha, 'input_sha256': hashes,
             'group_counts': counts, 'included_groups': 200, 'excluded_groups': 50,
             'verification_scope': 'Metadata and byte hashes only; no Parquet queries, model loading or outcome values', 'outcome_values_read': 0}
    if receipt.exists():
        require(read_json(receipt) == proof, 'Protocol receipt mismatch')
    else:
        with receipt.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(proof, indent=2, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    return {**proof, 'receipt_sha256': digest(receipt)}


def main():
    parser = argparse.ArgumentParser(description='Verify v2 contract; exclusively create its proof receipt only if absent. Never inspect outcomes.')
    parser.add_argument('--check', action='store_true', required=True)
    parser.parse_args()
    try:
        print(json.dumps(check(), indent=2, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'Contract verification failed: {error}\n')


if __name__ == '__main__':
    main()
