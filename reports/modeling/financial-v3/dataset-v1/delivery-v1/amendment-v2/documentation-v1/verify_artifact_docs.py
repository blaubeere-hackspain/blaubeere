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


def text(path):
    return path.read_text().replace('\r\n', '\n')


def load(path):
    return parent.parse(path.read_bytes())


def analytical(path):
    return path.startswith(('data/', 'xray/', 'reports/')) or (
        path.startswith(('scripts/', 'tests/')) and path.endswith('.py')) or path in (
        'requirements.txt', 'requirements-model.txt', 'requirements-model.in',
        'data_dictionary.md', '.gitattributes', '.gitignore')


def validate():
    before = load(HERE / 'before.json')
    parent.require(set(before['documents']) == set(DOCS), 'Document scope changed')
    parent.require(digest(HERE / 'verify.py') == before['verifier_sha256'], 'Original verification evidence changed')
    for path, expected in before['documents'].items():
        parent.require(digest(HERE / 'before' / path) == expected, 'Original document changed: ' + path)
    parent.check_map(ROOT, {parent.DELIVERY_REL + '/transition.json': parent.TRANSITION_PIN,
        parent.DATASET_REL + '/manifest.json': parent.DATASET_PIN,
        parent.DATASET_REL + '/model-v1/manifest.json': parent.MODEL_PIN})
    transition = load(ROOT / parent.DELIVERY_REL / 'transition.json')
    checked = 0
    for path, expected in transition['historical_bindings'].items():
        if not analytical(path):
            continue
        source = HERE / 'before' / path if path in DOCS else parent.safe_file(ROOT, path)
        parent.require(digest(source) == expected, 'Analytical source/artifact changed: ' + path)
        checked += 1
    for directory, excluded in ((parent.DATASET_REL, ('delivery-v1', 'model-v1')), (parent.DATASET_REL + '/model-v1', ())):
        manifest = load(ROOT / directory / 'manifest.json')
        parent.check_inventory(ROOT, directory, set(manifest['artifacts_sha256']) | {'manifest.json', 'verification.json'}, excluded)
        parent.check_map(ROOT, {directory + '/' + path: expected for path, expected in manifest['artifacts_sha256'].items()})
    report_path = 'reports/modeling/training-report.md'
    old_report = text(HERE / 'before' / report_path)
    historical = old_report[old_report.index('## 1. Resumen ejecutivo de v1'):]
    report = text(ROOT / report_path)
    parent.require(historical in report, 'Historical v1/v2 report sections changed')
    plan_path = 'reports/planning/financial-scoring-plan.md'
    old_plan = text(HERE / 'before' / plan_path)
    sealed_json = old_plan.split('```json\n', 1)[1].split('\n```', 1)[0]
    parent.require(sealed_json in text(ROOT / plan_path), 'V2 preregistration changed')
    metrics = load(ROOT / parent.DATASET_REL / 'model-v1/metrics-summary.json')
    for target, title in (('receipt_contraction_3m', 'Contracción'), ('receipt_expansion_3m', 'Expansión')):
        for candidate, label in (('constant', 'Constante'), ('persistence', '**Persistencia, seleccionada**'),
                                 ('trend', 'Tendencia'), ('logistic_C0.1', 'Logística C=0,1'), ('logistic_C1', 'Logística C=1')):
            values = [format(metrics[target]['candidates'][candidate][key], '.6f').replace('.', ',')
                      for key in ('average_precision', 'brier', 'mean_group_brier')]
            if candidate == 'persistence':
                values = ['**' + value + '**' for value in values]
            row = '| ' + ' | '.join([title, label, *values]) + ' |'
            parent.require(row in report, 'Published model metric differs from sealed result: ' + target + '/' + candidate)
    links = 0
    for relative in DOCS:
        source = ROOT / relative
        content = text(source)
        parent.require(content.endswith('\n'), 'Missing final newline: ' + relative)
        parent.require(not any(line.rstrip() != line for line in content.splitlines()), 'Trailing whitespace: ' + relative)
        for destination in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', destination) or destination.startswith('#'):
                continue
            destination = unquote(destination.split('#', 1)[0])
            if destination:
                parent.require((source.parent / destination).exists(), 'Broken local link: ' + relative + ' -> ' + destination)
                links += 1
    return {'ok': True, 'scope': 'authorized_documentation_and_analytical_artifacts_only',
        'application_and_backend_validation_performed': False,
        'prior_whole_application_snapshot_check_superseded': False,
        'historical_whole_worktree_integrity': False, 'scientific_utility_pass': False,
        'documents': {path: digest(ROOT / path) for path in DOCS},
        'before_receipt_sha256': digest(HERE / 'before.json'), 'verifier_sha256': digest(Path(__file__)),
        'original_document_bytes_preserved': True, 'analytical_bindings_checked': checked,
        'scientific_artifact_inventory_preserved': True, 'historical_training_sections_preserved': True,
        'v2_preregistration_preserved': True, 'metric_rows_matched_to_sealed_output': 10,
        'local_links_checked': links, 'source_queries': 0, 'outcome_queries': 0, 'fits': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('seal', 'check'))
    args = parser.parse_args()
    result = validate()
    receipt = HERE / 'artifact-after.json'
    if args.action == 'seal':
        with receipt.open('xb') as stream:
            stream.write(parent.canonical(result))
    else:
        parent.require(load(receipt) == result, 'Documentation verification drift')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
