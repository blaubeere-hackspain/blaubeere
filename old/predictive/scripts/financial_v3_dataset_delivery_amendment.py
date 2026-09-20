import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
DATASET_REL = 'reports/modeling/financial-v3/dataset-v1'
DELIVERY_REL = DATASET_REL + '/delivery-v1'
OUT_REL = DELIVERY_REL + '/amendment-v2'
CACHE_PATH = 'apps/app/tsconfig.tsbuildinfo'
CACHE_EXPECTED = 'a3891095f07a0fd0c435132d2bc560e8970c79f2e9bc8945fe02b0a36affacbf'
CACHE_OBSERVED = '8ecfc04867158213c1034082f48adf108d0a002a1aa92058617cb4334c4307fc'
RECOVERY_HASH = '761f40c0b1fe1e10f44394db96a255e1af6f46309ae6a84422f01c711cf4de7d'
TRANSITION_PIN = 'c878d0e2767bbf71e5ae2a0da5d27709081817d0d004b5c10984b76690f33f45'
DATASET_PIN = 'e360ef33ba7cae39aa9c94d0f8e778f49c38a3985f77053a2f53bd4ae5ef8a44'
MODEL_PIN = '71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428'
VERSION = 'financial-v3-delivery-amendment-v2'
DOCS = ('readme.md', 'docs/PRODUCT.md', 'docs/REQUIREMENTS.md', 'docs/DASHBOARD.md')
BEFORE_PATHS = ('services/api/src/finance.rs', 'services/api/src/lib.rs', 'services/api/src/main.rs',
    'services/mcp/src/main.rs', 'apps/app/app/dashboard/page.tsx', 'apps/app/app/globals.css',
    'apps/app/lib/types.ts', 'apps/app/components/trajectory-assessment.tsx', 'apps/app/lib/trajectory-types.ts',
    'apps/app/lib/trajectory.ts', 'apps/app/lib/trajectory.test.ts', 'docs/PRODUCT.md', 'docs/REQUIREMENTS.md',
    'docs/DASHBOARD.md', 'readme.md', 'package.json')
SOURCE_PATHS = BEFORE_PATHS + ('services/api/src/financial_v3.rs', 'services/api/src/financial_v3_tests.rs',
    'services/mcp/src/financial_v3_tests.rs', 'apps/app/components/financial-v3-assessment.tsx',
    'apps/app/lib/financial-v3.ts', 'apps/app/lib/financial-v3.test.ts', 'scripts/financial_v3_dataset_delivery.py',
    'scripts/check-financial-v3-e2e.ts', 'scripts/financial_v3_dataset_delivery_amendment.py',
    'tests/test_financial_v3_dataset_delivery_amendment.py')
SUPPORT_PATHS = ('amendment.json', 'runner.py', 'check-financial-v3-e2e.ts', 'adapter-patch.json',
    'authorization.json', 'generated-before/next-env.d.ts') + tuple('source-before-docs/' + p for p in DOCS)
DECISION = {
    'version': VERSION,
    'kind': 'delivery_integrity_amendment',
    'authorization': {'date': '2026-09-20', 'user_instruction': 'hay que desbloquear los bloqueo',
        'parent_record': '.devin/goal.md#enmienda-de-cache-autorizada--2026-09-20'},
    'original_transition_sha256': TRANSITION_PIN,
    'exceptions': [{'path': CACHE_PATH, 'kind': 'generated_regenerable',
        'historical_expected_sha256': CACHE_EXPECTED, 'historical_bytes_preserved': False,
        'initial_observed_sha256': CACHE_OBSERVED, 'failed_recovery_sha256': RECOVERY_HASH,
        'failed_recovery_bytes': 137560, 'recovery_was_exact': False}],
    'other_historical_bindings': 'All other 3025 bindings exact: immutable scientific/current bytes or previously captured authorized application-before bytes.',
    'current_sources': 'Separate fixed-list source-after receipt; no source hash exceptions.',
    'cache_rule': 'Only this exact regular nonsymlink cache may regenerate. Record actual SHA256, bytes and compiler version on every check; no stable current hash requirement.',
    'historical_whole_worktree_integrity': False,
    'old_seals_or_checkers_modified': False,
    'scientific_utility_pass': False,
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


DECISION_PIN = '3fd536ba3b5432adb71282ae7da6933a0f1297a02306c7c88dbf2804d0bb44b5'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def parse(content):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)


def validate_decision(content):
    require(sha_bytes(content) == DECISION_PIN and content == canonical(DECISION), 'Pinned amendment decision mismatch')
    return parse(content)


def relative(path):
    require(type(path) is str and path and ':' not in path and '\\' not in path, 'Invalid relative path')
    parts = path.split('/')
    require(all(p not in ('', '.', '..') for p in parts) and not PurePosixPath(path).is_absolute(), 'Unsafe path')
    return path


def safe_file(root, path):
    path = root / relative(path)
    try:
        for ancestor in (path, *path.parents):
            require(not stat.S_ISLNK(ancestor.lstat().st_mode), 'Symlink forbidden: ' + str(ancestor))
        require(stat.S_ISREG(path.stat().st_mode), 'Regular file required: ' + str(path))
    except OSError as exc:
        raise ValueError('Missing/unreadable file: ' + str(path)) from exc
    return path


def read(root, path):
    return parse(safe_file(root, path).read_bytes())


def hashes(root, paths):
    return {p: sha_file(safe_file(root, p)) for p in paths}


def valid_hash(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def check_map(root, binding):
    require(type(binding) is dict, 'Binding map required')
    for path, expected in binding.items():
        require(valid_hash(expected), 'Invalid hash binding')
        require(sha_file(safe_file(root, path)) == expected, 'Bound bytes changed: ' + path)


def cache_record(root):
    path = safe_file(root, CACHE_PATH)
    require(path.stat().st_size <= 10_000_000, 'Compiler cache exceeds bound')
    data = path.read_bytes()
    value = parse(data)
    require(type(value) is dict and type(value.get('version')) is str and bool(value['version'])
        and type(value.get('fileNames')) is list and type(value.get('fileInfos')) is list, 'Invalid compiler cache schema')
    return {'path': CACHE_PATH, 'kind': 'generated_regenerable', 'sha256': sha_bytes(data), 'bytes': len(data),
        'version': value['version'], 'observed_at': now(), 'historical_expected_sha256': CACHE_EXPECTED,
        'historical_bytes_preserved': False, 'historical_hash_matches_current': sha_bytes(data) == CACHE_EXPECTED}


def check_historical(root, binding, before):
    require(binding.get(CACHE_PATH) == CACHE_EXPECTED, 'Original cache binding changed')
    require(type(before) is dict and set(before) <= set(BEFORE_PATHS), 'Unapproved before-snapshot redirection')
    for path, info in before.items():
        require(type(info) is dict and set(info) == {'sha256', 'historic_seal_bound'} and valid_hash(info['sha256']), 'Invalid before binding')
        require(type(info['historic_seal_bound']) is bool, 'Before binding flag')
        require(info['historic_seal_bound'] == (path in binding), 'Before bound scope changed')
        if path in binding:
            require(info['sha256'] == binding[path], 'Before bytes differ from historical seal')
        check_map(root, {DELIVERY_REL + '/source-before/' + path: info['sha256']})
    checked = 0
    for path, expected in binding.items():
        relative(path)
        if path == CACHE_PATH:
            continue
        target = DELIVERY_REL + '/source-before/' + path if path in before else path
        check_map(root, {target: expected})
        checked += 1
    return {'historical_exact_bindings': checked, 'exception_count': 1, 'derived_cache': cache_record(root),
        'historical_whole_worktree_integrity': False, 'lost_historical_cache_bytes_recovered': False}


def inventory(root, directory, excluded=()):
    relative(directory)
    allowed = {DATASET_REL: {'delivery-v1', 'model-v1'}, DELIVERY_REL: {'amendment-v2'}}
    require(set(excluded) <= allowed.get(directory, set()), 'Unapproved inventory exclusion')
    base = root / directory
    require(base.is_dir() and not base.is_symlink(), 'Invalid inventory root')
    found = set()
    for current, directories, files in os.walk(base, followlinks=False):
        for name in list(directories):
            child = Path(current) / name
            require(not child.is_symlink(), 'Inventory directory symlink')
            if child.relative_to(base).as_posix() in excluded:
                directories.remove(name)
        for name in files:
            child = Path(current) / name
            safe_file(root, child.relative_to(root).as_posix())
            found.add(child.relative_to(base).as_posix())
    return found


def check_inventory(root, directory, expected, excluded=()):
    require(inventory(root, directory, excluded) == set(expected), 'Immutable artifact inventory changed: ' + directory)


def scientific_check(root):
    check_map(root, {DELIVERY_REL + '/transition.json': TRANSITION_PIN,
        DATASET_REL + '/manifest.json': DATASET_PIN, DATASET_REL + '/model-v1/manifest.json': MODEL_PIN})
    transition = read(root, DELIVERY_REL + '/transition.json')
    require(len(transition['historical_bindings']) == 3026 and set(transition['before']) == set(BEFORE_PATHS), 'Historical inventory scope changed')
    result = check_historical(root, transition['historical_bindings'], transition['before'])
    for folder, excluded in ((DATASET_REL, ('delivery-v1', 'model-v1')), (DATASET_REL + '/model-v1', ())):
        manifest = read(root, folder + '/manifest.json')
        expected = set(manifest['artifacts_sha256']) | {'manifest.json', 'verification.json'}
        check_inventory(root, folder, expected, excluded)
        check_map(root, {folder + '/' + p: h for p, h in manifest['artifacts_sha256'].items()})
    result.update(dataset_manifest_sha256=DATASET_PIN, model_manifest_sha256=MODEL_PIN,
        immutable_scientific_artifact_inventory=True, source_queries=0, outcome_queries=0, fits=0)
    return result


def validate_source_receipt(value):
    require(type(value) is dict and set(value) == {'version', 'decision_sha256', 'source_sha256', 'preservation_sha256', 'support_sha256', 'historical_whole_worktree_integrity'}, 'Source receipt fields')
    require(value['version'] == VERSION and value['decision_sha256'] == DECISION_PIN
        and value['historical_whole_worktree_integrity'] is False and valid_hash(value['preservation_sha256']), 'Source receipt policy')
    for key, paths in (('source_sha256', SOURCE_PATHS), ('support_sha256', SUPPORT_PATHS)):
        require(type(value[key]) is dict and set(value[key]) == set(paths)
            and all(valid_hash(v) for v in value[key].values()), 'Source receipt fixed-list mismatch')


def fresh(root, name, value, raw=False):
    relative(name)
    path = root / OUT_REL / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(value if raw else canonical(value))


def prepare(root):
    out = root / OUT_REL
    require(not (out / 'amendment.json').exists(), 'Amendment already prepared')
    check_map(root, {DELIVERY_REL + '/source-after-blocked.json': '8e1866678150ce276ccd3e987c4789800fcebf35cff967e1d66f9cd5e7089b6c',
        DELIVERY_REL + '/verification.json': '030ee1352baa4c88d670bcd61092ffb1439b13a521312a0e1b2a0e5a32697566',
        DELIVERY_REL + '/cache-recovery-1/candidate-1.tsbuildinfo': RECOVERY_HASH})
    previous = read(root, DELIVERY_REL + '/source-after-blocked.json')['sha256']
    require(set(previous) == set(SOURCE_PATHS[:-2]), 'Previous source receipt scope')
    check_map(root, previous)
    integrity = scientific_check(root)
    old_files = inventory(root, DELIVERY_REL, ('amendment-v2',))
    old_hashes = hashes(root, [DELIVERY_REL + '/' + p for p in sorted(old_files)])
    for path in DOCS:
        fresh(root, 'source-before-docs/' + path, safe_file(root, path).read_bytes(), raw=True)
    fresh(root, 'generated-before/next-env.d.ts', safe_file(root, 'apps/app/next-env.d.ts').read_bytes(), raw=True)
    excerpt = 'El usuario pide «hay que desbloquear los bloqueo» tras proponerse explícitamente registrar la caché TypeScript como regenerable.'
    goal = safe_file(root, '.devin/goal.md').read_text()
    require(excerpt in goal and '## Enmienda de caché autorizada — 2026-09-20' in goal, 'Parent authorization record absent')
    fresh(root, 'authorization.json', {'source': '.devin/goal.md', 'source_sha256_at_authorization': sha_file(root / '.devin/goal.md'),
        'excerpt': excerpt, 'user_authorized_exact_cache_exception': CACHE_PATH, 'recorded_at': now(), 'goal_is_parent_owned_not_a_fixed_runtime_source': True})
    fresh(root, 'amendment.json', DECISION)
    original = safe_file(root, 'scripts/check-financial-v3-e2e.ts').read_text()
    substitutions = [
        ('const root = resolve(import.meta.dir, "..");', 'const root = ' + json.dumps(str(root)) + ';'),
        ('const output = join(dataset, "delivery-v1");', 'const output = join(dataset, "delivery-v1", "amendment-v2");'),
        ('from "../apps/app/lib/financial-v3";', 'from ' + json.dumps(str(root / 'apps/app/lib/financial-v3')) + ';'),
        ('command: "bun run test:financial-v3:e2e"', 'command: "bun ' + OUT_REL + '/check-financial-v3-e2e.ts"'),
    ]
    adapted = original
    for before, after in substitutions:
        require(adapted.count(before) == 1, 'Unexpected E2E adapter source')
        adapted = adapted.replace(before, after)
    fresh(root, 'check-financial-v3-e2e.ts', adapted.encode(), raw=True)
    fresh(root, 'adapter-patch.json', {'source': 'scripts/check-financial-v3-e2e.ts', 'source_sha256': sha_file(root / 'scripts/check-financial-v3-e2e.ts'),
        'adapted_sha256': sha_bytes(adapted.encode()), 'substitutions': substitutions,
        'scope': 'Only root/import/output/report-command paths changed in isolated copy; all 15-step test assertions identical. Original helper unchanged.'})
    fresh(root, 'preservation-receipt.json', {'version': VERSION, 'decision_sha256': DECISION_PIN,
        'previous_source_sha256': previous, 'old_delivery_sha256': old_hashes,
        'documentation_before_sha256': hashes(root, DOCS), 'historical_before_check': integrity})
    return {'prepared': True, 'decision_sha256': DECISION_PIN, 'preserved_old_delivery_files': len(old_hashes), 'integrity': integrity}


def preservation_check(root, receipt):
    check_map(root, {OUT_REL + '/preservation-receipt.json': receipt['preservation_sha256']})
    preservation = read(root, OUT_REL + '/preservation-receipt.json')
    require(set(preservation) == {'version', 'decision_sha256', 'previous_source_sha256', 'old_delivery_sha256', 'documentation_before_sha256', 'historical_before_check'}
        and preservation['version'] == VERSION and preservation['decision_sha256'] == DECISION_PIN, 'Preservation receipt fields/policy')
    expected_old = {p.removeprefix(DELIVERY_REL + '/') for p in preservation['old_delivery_sha256']}
    require(all(p.startswith(DELIVERY_REL + '/') for p in preservation['old_delivery_sha256']), 'Old evidence path scope')
    check_inventory(root, DELIVERY_REL, expected_old, ('amendment-v2',))
    check_map(root, preservation['old_delivery_sha256'])
    for path, expected in preservation['documentation_before_sha256'].items():
        require(path in DOCS, 'Documentation capture scope')
        check_map(root, {OUT_REL + '/source-before-docs/' + path: expected})
    return preservation


def freeze(root):
    require(not (root / OUT_REL / 'source-after.json').exists(), 'Current source receipt already fixed')
    validate_decision(safe_file(root, OUT_REL + '/amendment.json').read_bytes())
    scientific_check(root)
    receipt = {'version': VERSION, 'decision_sha256': DECISION_PIN, 'source_sha256': hashes(root, SOURCE_PATHS),
        'preservation_sha256': sha_file(safe_file(root, OUT_REL + '/preservation-receipt.json')),
        'support_sha256': hashes(root / OUT_REL, SUPPORT_PATHS), 'historical_whole_worktree_integrity': False}
    validate_source_receipt(receipt)
    preservation_check(root, receipt)
    fresh(root, 'source-after.json', receipt)
    fresh(root, 'source-after-pin.json', {'sha256': sha_bytes(canonical(receipt))})
    return verify(root)


def verify(root):
    validate_decision(safe_file(root, OUT_REL + '/amendment.json').read_bytes())
    pin = read(root, OUT_REL + '/source-after-pin.json')
    require(type(pin) is dict and set(pin) == {'sha256'} and valid_hash(pin['sha256']), 'Source receipt pin shape')
    check_map(root, {OUT_REL + '/source-after.json': pin['sha256']})
    receipt = read(root, OUT_REL + '/source-after.json')
    validate_source_receipt(receipt)
    check_map(root, receipt['source_sha256'])
    check_map(root / OUT_REL, receipt['support_sha256'])
    preserved = preservation_check(root, receipt)
    result = scientific_check(root)
    result.update(ok=True, policy=VERSION, decision_sha256=DECISION_PIN, current_source_files=len(receipt['source_sha256']),
        current_source_receipt_sha256=pin['sha256'], old_delivery_evidence_files=len(preserved['old_delivery_sha256']),
        old_delivery_evidence_unchanged=True, source_before_preserved=True,
        technical_integrity_scope='3025 exact historical bindings plus one explicitly authorized recorded derived cache; current application separately bound',
        original_verifier_expected_to_fail=True, scientific_utility_pass=False)
    return result


def finalize(root):
    result = verify(root)
    receipt = read(root, OUT_REL + '/source-after.json')
    names = ('amendment-tdd-green', 'pure-python', 'app-final', 'rust-real-final', 'browser-final')
    commands = {}
    for name in names:
        value = read(root, OUT_REL + '/command-' + name + '.json')
        require(value['exit_code'] == 0 and value['source_unchanged'], 'Required command failed/drifted: ' + name)
        require(value['source_after_sha256'] == receipt['source_sha256'], 'Command not on final source: ' + name)
        commands[name] = {'path': 'command-' + name + '.json', 'sha256': sha_file(root / OUT_REL / ('command-' + name + '.json')),
            'command': value['command'], 'exit_code': value['exit_code'], 'cache_before': value['cache_before'], 'cache_after': value['cache_after'],
            'unittest_counts': [int(n) for n in re.findall(r'Ran (\d+) tests?', value['stderr'])],
            'rust_results': re.findall(r'test result: [^\n]+', value['stdout']),
            'source_sha256': value['source_after_sha256']}
    browsers = sorted((root / OUT_REL).glob('browser-*.json'))
    require(len(browsers) == 1, 'Expected single successful final browser attempt or explicit correction handling')
    browser = read(root, browsers[0].relative_to(root).as_posix())
    require(browser['status'] == 'passed' and len(browser['steps']) == 15 and not browser['page_errors'], 'Browser coverage failed')
    before = read(root, OUT_REL + '/preservation-receipt.json')
    documentation_changes = {p: {'before_sha256': before['documentation_before_sha256'][p], 'after_sha256': receipt['source_sha256'][p]} for p in DOCS}
    source_after = {'version': VERSION, 'source_sha256': receipt['source_sha256'], 'initial_receipt_sha256': result['current_source_receipt_sha256'],
        'decision_sha256': DECISION_PIN, 'verified_at': now(), 'cache_observation': result['derived_cache'], 'source_unchanged_across_required_commands': True,
        'documentation_before_after': documentation_changes, 'historical_whole_worktree_integrity': False}
    fresh(root, 'final-source-after.json', source_after)
    verification = {'version': VERSION, 'status': 'scoped_technical_checks_passed_independent_review_pending',
        'technical_acceptance': {'automated_delivery_checks_pass': True, 'independent_review': 'pending_parent_read_only_review', 'whole_goal_complete': False},
        'scientific_utility': {'pass': False, 'both_receipt_proxy_gates_failed': True, 'persistence_baseline_won': True,
            'validation_labeled': 28, 'eligible': 345, 'groups': 8, 'contraction_ap': 0.443, 'contraction_brier': 0.211,
            'expansion_ap': 0.410, 'expansion_brier': 0.225, 'recall_at_0_6': 0, 'Q4': 'insufficient_training_classes_no_forecast',
            'full_health_headline': None, 'automatic_alerts': False},
        'integrity': result, 'commands': commands, 'browser': browser,
        'source_after': {'path': 'final-source-after.json', 'sha256': sha_file(root / OUT_REL / 'final-source-after.json')},
        'documentation_before_after': documentation_changes,
        'legacy_scope': 'Original delivery verifier still rejects the lost historic compiler-cache bytes. Old scientific whole-worktree checkers and test_all_input_and_protected_hashes_after remain unchanged and are not claimed passing on current application. Original STOP/recovery reports are retained, not retroactively corrected.',
        'python_scope': 'Prior 189 pre-integration tests are historical evidence only. Current selected pure fixture suites and new amendment tests are recorded explicitly; complete financial discovery was not run. No real target queries or scientific fit/evaluation replay.',
        'failure_budget': {'tests_first_missing_module': {'corrections': 1, 'green_evidence': 'command-amendment-tdd-green.json'}, 'runtime_regressions': 'See all retained command logs; no assertions relaxed'},
        'old_scientific_data_source_model_writes': 0, 'outcome_queries': 0, 'new_real_fits_or_evaluations': 0,
        'remaining': ['Independent technical review by parent', 'Scientific utility and missing full-health/Q4/anticipation capabilities remain failed or unsupported']}
    fresh(root, 'verification.json', verification)
    artifacts = inventory(root, OUT_REL)
    fresh(root, 'evidence-manifest.json', {'version': VERSION, 'decision_sha256': DECISION_PIN,
        'artifacts_sha256': hashes(root / OUT_REL, sorted(artifacts)), 'excludes_self_and_future_append_only_check_observations': True})
    return {'technical_checks_pass': True, 'scientific_utility_pass': False, 'exception_count': 1,
        'historical_exact_bindings': result['historical_exact_bindings'], 'independent_review': 'pending',
        'report': OUT_REL + '/verification.json', 'current_source_receipt_sha256': result['current_source_receipt_sha256']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'freeze', 'verify', 'finalize', 'policy'))
    args = parser.parse_args()
    if args.action == 'policy':
        print(json.dumps({'decision': DECISION, 'sha256': DECISION_PIN}, indent=2))
        return
    action = {'prepare': prepare, 'freeze': freeze, 'verify': verify, 'finalize': finalize}[args.action]
    try:
        result = action(ROOT)
    except Exception as exc:
        if args.action == 'verify':
            try:
                cache = cache_record(ROOT)
            except Exception as cache_error:
                cache = {'unavailable_reason': str(cache_error)}
            fresh(ROOT, 'check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json', {'ok': False, 'error': str(exc), 'derived_cache': cache, 'historical_whole_worktree_integrity': False})
        raise
    if args.action == 'verify':
        fresh(ROOT, 'check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json', result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
