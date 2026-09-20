import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = next(p for p in Path(__file__).resolve().parents if (p / '.git').exists())
sys.path.insert(0, str(ROOT))
from scripts import financial_v3_dataset_delivery_amendment as parent

HERE = Path(__file__).resolve().parent
DOCS = ('docs/DATA-MODEL-CONTEXT.md', 'reports/modeling/training-report.md',
        'docs/PRODUCT.md', 'docs/REQUIREMENTS.md', 'docs/DASHBOARD.md',
        'readme.md', 'reports/planning/financial-scoring-plan.md')


def digest(path):
    return parent.sha_file(path)


def load(path):
    return parent.parse(path.read_bytes())


def fresh(path, value, raw=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(value if raw else parent.canonical(value))


def normalized(path):
    return path.read_text().replace('\r\n', '\n')


def capture():
    parent.require(not (HERE / 'before.json').exists(), 'Documentation revision already captured')
    checked = parent.verify(ROOT)
    before = {}
    for relative in DOCS:
        source = parent.safe_file(ROOT, relative)
        content = source.read_bytes()
        fresh(HERE / 'before' / relative, content, raw=True)
        before[relative] = hashlib.sha256(content).hexdigest()
    receipt = {
        'version': 'documentation-refresh-v1', 'authorization_date': '2026-09-20',
        'request': 'actualizar data-model-context.md, training-report.md y documentos relacionados pendientes',
        'root': str(ROOT), 'documents': before, 'verifier_sha256': digest(Path(__file__)),
        'parent_source_receipt_sha256': checked['current_source_receipt_sha256'],
        'parent_decision_sha256': checked['decision_sha256'], 'parent_check': checked,
        'scope': 'Documentation only; captured before bytes resolve explicitly authorized doc changes. No scientific code, data, model, metrics, immutable receipt or old checker edits.'}
    fresh(HERE / 'before.json', receipt)
    return {'captured_documents': len(before), 'scientific_check_before': checked['ok']}


def validate():
    receipt = load(HERE / 'before.json')
    parent.require(set(receipt['documents']) == set(DOCS), 'Documentation scope changed')
    parent.require(digest(Path(__file__)) == receipt['verifier_sha256'], 'Documentation verifier changed')
    parent.validate_decision((ROOT / parent.OUT_REL / 'amendment.json').read_bytes())
    parent.require(parent.DECISION_PIN == receipt['parent_decision_sha256'], 'Parent decision changed')
    source_path = ROOT / parent.OUT_REL / 'source-after.json'
    parent.require(digest(source_path) == receipt['parent_source_receipt_sha256'], 'Parent source receipt changed')
    source = load(source_path)
    parent.validate_source_receipt(source)
    parent.check_map(ROOT / parent.OUT_REL, source['support_sha256'])
    parent.preservation_check(ROOT, source)
    for relative, expected in receipt['documents'].items():
        parent.require(digest(HERE / 'before' / relative) == expected, 'Document before copy changed: ' + relative)
    for relative, expected in source['source_sha256'].items():
        path = HERE / 'before' / relative if relative in DOCS else parent.safe_file(ROOT, relative)
        parent.require(digest(path) == expected, 'Application/non-document source changed: ' + relative)
    parent.check_map(ROOT, {parent.DELIVERY_REL + '/transition.json': parent.TRANSITION_PIN,
        parent.DATASET_REL + '/manifest.json': parent.DATASET_PIN,
        parent.DATASET_REL + '/model-v1/manifest.json': parent.MODEL_PIN})
    transition = load(ROOT / parent.DELIVERY_REL / 'transition.json')
    exact = 0
    for relative, expected in transition['historical_bindings'].items():
        if relative == parent.CACHE_PATH:
            parent.require(expected == parent.CACHE_EXPECTED, 'Historical cache identity changed')
            continue
        if relative in transition['before']:
            path = ROOT / parent.DELIVERY_REL / 'source-before' / relative
        elif relative in DOCS:
            path = HERE / 'before' / relative
        else:
            path = parent.safe_file(ROOT, relative)
        parent.require(digest(path) == expected, 'Historical non-document asset changed: ' + relative)
        exact += 1
    for directory, excluded in ((parent.DATASET_REL, ('delivery-v1', 'model-v1')), (parent.DATASET_REL + '/model-v1', ())):
        manifest = load(ROOT / directory / 'manifest.json')
        parent.check_inventory(ROOT, directory, set(manifest['artifacts_sha256']) | {'manifest.json', 'verification.json'}, excluded)
        parent.check_map(ROOT, {directory + '/' + p: h for p, h in manifest['artifacts_sha256'].items()})
    report = 'reports/modeling/training-report.md'
    old = normalized(HERE / 'before' / report)
    historical = old[old.index('## 1. Resumen ejecutivo de v1'):]
    parent.require(historical in normalized(ROOT / report), 'Historical training sections 1–20 changed')
    plan = 'reports/planning/financial-scoring-plan.md'
    old_plan = normalized(HERE / 'before' / plan)
    old_json = old_plan.split('```json\n', 1)[1].split('\n```', 1)[0]
    parent.require(old_json in normalized(ROOT / plan), 'Preregistered v2 JSON changed')
    links = 0
    for relative in DOCS:
        path = ROOT / relative
        text = normalized(path)
        parent.require(text.endswith('\n'), 'Missing final newline: ' + relative)
        parent.require(not any(line.rstrip() != line for line in text.splitlines()), 'Trailing whitespace: ' + relative)
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('#'):
                continue
            target = unquote(target.split('#', 1)[0])
            if target:
                parent.require((path.parent / target).exists(), 'Broken local link: ' + relative + ' -> ' + target)
                links += 1
    return {'ok': True, 'documents': {p: digest(ROOT / p) for p in DOCS},
        'documentation_before_preserved': True, 'application_and_scientific_code_changed': False,
        'historical_training_sections_preserved': True, 'v2_preregistration_preserved': True,
        'historical_exact_bindings_via_authorized_snapshots': exact, 'cache_exception_count': 1,
        'cache': parent.cache_record(ROOT), 'local_links_checked': links,
        'source_queries': 0, 'outcome_queries': 0, 'fits': 0,
        'historical_whole_worktree_integrity': False, 'scientific_utility_pass': False,
        'old_receipts_edited': False, 'old_current_document_checks_superseded_only_by_this_documentation_revision': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('capture', 'seal', 'check'))
    args = parser.parse_args()
    if args.action == 'capture':
        result = capture()
    else:
        result = validate()
        if args.action == 'seal':
            fresh(HERE / 'after.json', {'before_sha256': digest(HERE / 'before.json'), 'verification': result})
        else:
            after = load(HERE / 'after.json')
            parent.require(after['before_sha256'] == digest(HERE / 'before.json'), 'Before receipt changed')
            parent.require(after['verification']['documents'] == result['documents'], 'Documentation drift')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
