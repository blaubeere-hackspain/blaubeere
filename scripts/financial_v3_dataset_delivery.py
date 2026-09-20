import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'reports/modeling/financial-v3/dataset-v1'
DELIVERY = DATASET / 'delivery-v1'
TRANSITION_PIN = 'c878d0e2767bbf71e5ae2a0da5d27709081817d0d004b5c10984b76690f33f45'
PINS = {'manifest.json': 'e360ef33ba7cae39aa9c94d0f8e778f49c38a3985f77053a2f53bd4ae5ef8a44',
        'model-v1/manifest.json': '71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428'}
EXISTING = ('services/api/src/finance.rs', 'services/api/src/lib.rs', 'services/api/src/main.rs',
    'services/mcp/src/main.rs', 'apps/app/app/dashboard/page.tsx', 'apps/app/app/globals.css',
    'apps/app/lib/types.ts', 'apps/app/components/trajectory-assessment.tsx',
    'apps/app/lib/trajectory-types.ts', 'apps/app/lib/trajectory.ts', 'apps/app/lib/trajectory.test.ts',
    'docs/PRODUCT.md', 'docs/REQUIREMENTS.md', 'docs/DASHBOARD.md', 'readme.md', 'package.json')
NEW = ('services/api/src/financial_v3.rs', 'services/api/src/financial_v3_tests.rs',
    'services/mcp/src/financial_v3_tests.rs', 'apps/app/components/financial-v3-assessment.tsx',
    'apps/app/lib/financial-v3.ts', 'apps/app/lib/financial-v3.test.ts',
    'scripts/financial_v3_dataset_delivery.py', 'scripts/check-financial-v3-e2e.ts')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def fresh(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def bindings():
    result = {}
    def add(values, base=ROOT):
        for p, h in values.items():
            key = (base / p).relative_to(ROOT).as_posix()
            require(key not in result or result[key] == h, 'Conflicting historical binding: ' + key)
            result[key] = h
    for p, h in PINS.items():
        require(sha(DATASET / p) == h, 'Manifest trust pin mismatch: ' + p)
        manifest = read(DATASET / p)
        add({p: h}, DATASET)
        add(manifest['artifacts_sha256'], (DATASET / p).parent)
        add(manifest.get('source_sha256', {}))
        add(manifest.get('new_code_sha256', {}))
        add(manifest.get('inputs_sha256', {}))
    receipt = read(DATASET / 'receipt.json')
    add(receipt['inputs_sha256'])
    add(receipt['protected_before_sha256'])
    add(read(DATASET / 'model-v1/seal.json')['bindings_sha256'])
    for p in ('verification.json', 'model-v1/verification.json'):
        add({p: sha(DATASET / p)}, DATASET)
    return dict(sorted(result.items()))


def capture():
    require(not (DELIVERY / 'transition.json').exists(), 'Transition already captured; never overwrite')
    bound = bindings()
    for p, h in bound.items():
        require(sha(ROOT / p) == h, 'Before bytes differ from seal: ' + p)
    before = {}
    for p in EXISTING:
        source = ROOT / p
        require(source.is_file() and not source.is_symlink(), 'Invalid before source: ' + p)
        content = source.read_bytes()
        h = hashlib.sha256(content).hexdigest()
        require(p not in bound or bound[p] == h, 'Application before differs from seal: ' + p)
        target = DELIVERY / 'source-before' / p
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(content)
        before[p] = {'sha256': h, 'historic_seal_bound': p in bound}
    record = {'version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(), 'pins': PINS,
        'before': before, 'historical_bindings': bound, 'authorized_existing': EXISTING, 'authorized_new': NEW,
        'legacy_scope': 'Old whole-worktree checkers and test_all_input_and_protected_hashes_after assert historical application bytes. They are unchanged, intentionally not claimed passing on delivered application. This verifier checks every old bound file against current scientific bytes or exact source-before application bytes, and binds delivered source separately.',
        'source_queries': 0, 'outcome_queries': 0, 'fits': 0}
    fresh(DELIVERY / 'transition.json', record)
    fresh(DELIVERY / 'transition-pin.json', {'sha256': sha(DELIVERY / 'transition.json')})
    return {'captured_files': len(before), 'historical_bindings': len(bound), 'transition_sha256': sha(DELIVERY / 'transition.json')}


def verify(after=False):
    require(sha(DELIVERY / 'transition.json') == read(DELIVERY / 'transition-pin.json')['sha256'] == TRANSITION_PIN, 'Fixed transition changed')
    transition = read(DELIVERY / 'transition.json')
    bound = bindings()
    require(bound == transition['historical_bindings'], 'Historical binding inventory changed')
    changed = []
    for p, h in bound.items():
        target = DELIVERY / 'source-before' / p if p in transition['before'] else ROOT / p
        require(sha(target) == h, 'Historical bytes changed: ' + p)
    for p, info in transition['before'].items():
        require(sha(DELIVERY / 'source-before' / p) == info['sha256'], 'Before snapshot changed: ' + p)
        if sha(ROOT / p) != info['sha256']:
            changed.append(p)
    for area in (DATASET, DATASET / 'model-v1'):
        manifest = read(area / 'manifest.json')
        actual = {p.relative_to(area).as_posix() for p in area.rglob('*') if p.is_file()
                  and not p.is_relative_to(DELIVERY) and (area != DATASET or not p.is_relative_to(DATASET / 'model-v1'))}
        require(actual == set(manifest['artifacts_sha256']) | {'manifest.json', 'verification.json'}, 'Scientific artifact inventory changed')
    if after:
        output = read(DELIVERY / 'source-after.json')
        for p, h in output['sha256'].items():
            require(sha(ROOT / p) == h, 'Delivered source drift: ' + p)
    return {'ok': True, 'historical_bindings_checked': len(bound), 'authorized_application_differences': changed,
        'scientific_differences': [], 'delivered_source_checked': after, 'source_queries': 0, 'outcome_queries': 0, 'fits': 0}


def seal_after():
    checked = verify()
    paths = [p for p in EXISTING + NEW if (ROOT / p).is_file()]
    fresh(DELIVERY / 'source-after.json', {'sha256': {p: sha(ROOT / p) for p in paths}, 'integrity': checked})
    return verify(True)


def inspect():
    for name in ('index.json', 'model-v1/index.json'):
        v = read(DATASET / name)
        print(name, json.dumps({k: [val[0], {'count': len(val)}] if isinstance(val, list) else val for k, val in v.items()}, indent=2))
    for name in ('profiles/COMP_0001.json', 'model-v1/profiles/COMP_0001.json'):
        v = read(DATASET / name)
        payload = v.get('financial_v3', v)
        payload['points'] = [payload['points'][0], payload['points'][-1]]
        print(name, json.dumps(v, indent=2))


def diagnose():
    transition = read(DELIVERY / 'transition.json')
    require(sha(DELIVERY / 'transition.json') == TRANSITION_PIN, 'Fixed transition changed')
    require(bindings() == transition['historical_bindings'], 'Historical inventory changed')
    differences = []
    for p, expected in transition['historical_bindings'].items():
        target = DELIVERY / 'source-before' / p if p in transition['before'] else ROOT / p
        actual = sha(target) if target.is_file() else None
        if actual != expected:
            differences.append({'path': p, 'expected_sha256': expected, 'actual_sha256': actual,
                'authorized_transition': False})
    paths = [p for p in EXISTING + NEW if (ROOT / p).is_file()]
    after = {'status': 'blocked_not_scientific_integrity_acceptance',
        'sha256': {p: sha(ROOT / p) for p in paths}, 'historical_differences': differences,
        'source_before_verified': all(sha(DELIVERY / 'source-before' / p) == info['sha256'] for p, info in transition['before'].items()),
        'authorized_existing_changes': [p for p, info in transition['before'].items() if sha(ROOT / p) != info['sha256']],
        'new_source_files': list(NEW), 'historical_bindings_checked': len(transition['historical_bindings']),
        'source_queries': 0, 'outcome_queries': 0, 'fits': 0,
        'stop': 'No safe historical-byte restoration is available for the untracked generated compiler cache. Do not widen the fixed transition, edit old seals, or silently omit this asset. Parent must handle integrity blocker.'}
    fresh(DELIVERY / 'source-after-blocked.json', after)
    return after


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['capture', 'verify', 'seal-after', 'inspect', 'run', 'diagnose'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'run':
        label, *command = args.arguments
        require(label.replace('-', '').isalnum(), 'Invalid evidence label')
        target = DELIVERY / ('command-' + label + '.json')
        require(not target.exists(), 'Command attempt already recorded')
        start = datetime.now(timezone.utc).isoformat()
        source = {p: sha(ROOT / p) for p in EXISTING + NEW if (ROOT / p).is_file()}
        env = {k: os.environ[k] for k in ('FINANCIAL_V3_ASSESSMENT_FILE', 'TRAJECTORY_ASSESSMENT_FILE', 'PROXY_ASSESSMENT_FILE', 'CARGO_NET_OFFLINE', 'NEXT_TELEMETRY_DISABLED') if k in os.environ}
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        fresh(target, {'command': command, 'started_at': start, 'exit_code': proc.returncode,
            'environment_allowlist': env, 'source_sha256_at_start': source,
            'source_unchanged_during_command': all(sha(ROOT / p) == h for p, h in source.items()),
            'stdout': proc.stdout, 'stderr': proc.stderr})
        print(proc.stdout + proc.stderr)
        raise SystemExit(proc.returncode)
    if args.action == 'diagnose':
        print(json.dumps(diagnose(), indent=2))
        return
    if args.action == 'inspect':
        inspect()
        return
    value = capture() if args.action == 'capture' else seal_after() if args.action == 'seal-after' else verify((DELIVERY / 'source-after.json').exists())
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
