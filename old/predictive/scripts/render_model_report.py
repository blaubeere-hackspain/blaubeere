import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'reports/modeling/financial-v3/dataset-v1'
DATASET_PIN = 'e360ef33ba7cae39aa9c94d0f8e778f49c38a3985f77053a2f53bd4ae5ef8a44'
MODEL_PIN = '71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428'
TEMPLATE = Path(__file__).with_name('model_report.html')
DEFAULT_OUTPUT = ROOT / '.local/model-report.html'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    json.dumps(value, allow_nan=False)
    return value


def read_verified(root, relative, expected, size=None):
    path = PurePosixPath(relative)
    require(relative and not path.is_absolute() and not any(c in relative for c in ('\\', ':'))
            and all(p not in ('.', '..', '') for p in relative.split('/')), 'Unsafe artifact path')
    target = Path(root)
    require(not target.is_symlink(), 'Symlink artifact root')
    for part in path.parts:
        target = target / part
        require(not target.is_symlink(), 'Symlink artifact path')
    require(target.is_file() and target.stat().st_size <= 4 * 1024 * 1024, 'Artifact missing or too large')
    data = target.read_bytes()
    require(size is None or len(data) == size, 'Artifact byte count mismatch: ' + relative)
    require(hashlib.sha256(data).hexdigest() == expected, 'Artifact SHA256 mismatch: ' + relative)
    return parse_json(data)


def load_report(company_ids=None, all_companies=False):
    sources = {}

    def pinned(root, name, digest, size=None):
        result = read_verified(root, name, digest, size)
        sources[(root / name).relative_to(ROOT).as_posix()] = digest
        return result

    def artifact(root, manifest, name, size=None):
        return pinned(root, name, manifest['artifacts_sha256'][name], size)

    manifest = pinned(DATASET, 'manifest.json', DATASET_PIN)
    model_root = DATASET / 'model-v1'
    model_manifest = pinned(model_root, 'manifest.json', MODEL_PIN)
    index = artifact(DATASET, manifest, 'index.json')['companies']
    model_index = artifact(model_root, model_manifest, 'index.json')
    metrics = artifact(model_root, model_manifest, 'metrics-summary.json')
    coverage = artifact(DATASET, manifest, 'coverage.json')
    entries = {c['id']: c for c in index}
    overlays = {c['company_id']: c for c in model_index['companies']}
    excluded = {c['company_id']: c for c in model_index['excluded_product_only']}
    require(not (all_companies and company_ids), 'Choose company IDs or all companies, not both')
    selected = sorted(set(company_ids)) if company_ids is not None else sorted(entries) if all_companies else sorted(overlays)[:3]
    require(selected and all(cid in entries for cid in selected), 'Unknown or empty company selection')
    companies = []
    for cid in selected:
        require(re.fullmatch(r'COMP_\d{4}', cid), 'Invalid company ID')
        entry = entries[cid]
        require(entry['path'] == f'profiles/{cid}.json', 'Company profile path mismatch')
        require(entry['sha256'] == manifest['artifacts_sha256'][entry['path']], 'Index hash mismatch')
        profile = artifact(DATASET, manifest, entry['path'], entry['bytes'])
        require(profile['id'] == cid and profile['group'] == entry['group'], 'Company identity mismatch')
        meta = profile['financial_v3']['meta']
        model_points, reason = [], None
        if cid in overlays:
            model_entry = overlays[cid]
            require(model_entry['path'] == f'profiles/{cid}.json'
                    and model_entry['group_id'] == entry['group']
                    and model_entry['sha256'] == model_manifest['artifacts_sha256'][model_entry['path']],
                    'Model index mismatch')
            model = artifact(model_root, model_manifest, model_entry['path'], model_entry['bytes'])
            require(model['company_id'] == cid and model['group_id'] == entry['group'], 'Model identity mismatch')
            for point in model['points']:
                model_points.append({key: point[key] for key in ('month', 'as_of', 'issued_at', 'forecast')})
        else:
            require(cid in excluded and excluded[cid]['group_id'] == entry['group'], 'Missing model exclusion')
            reason = excluded[cid]['reason']
        companies.append({
            **{key: profile[key] for key in ('id', 'name', 'group', 'currency')},
            'points': profile['financial_v3']['points'], 'model_points': model_points, 'model_reason': reason,
            'retrieved_at': meta['retrieved_at'], 'source_keys': meta['source_keys'],
            'score_policy': meta['score_policy'],
            'anchors': [{key: a.get(key) for key in ('product_id', 'currency', 'anchor_date')} for a in meta['anchors']],
        })
    return {'version': 'local-model-report-v1', 'scope': 'synthetic_internal_retrospective',
            'dataset_version': manifest['version'], 'model_version': model_manifest['version'],
            'automatic_alerts': False, 'total_companies': len(index), 'companies': companies,
            'selection': 'explicit' if company_ids else 'all' if all_companies else 'first_three_non_holdout_ids',
            'metrics': metrics, 'coverage': coverage, 'sources_sha256': sources}


def render_report(data):
    template = TEMPLATE.read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for char, escaped in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        payload = payload.replace(char, escaped)
    require(template.count('__REPORT_DATA__') == 1, 'Report data placeholder mismatch')
    html = template.replace('__REPORT_DATA__', payload)
    script = re.search(r'<script id="report-code">(.*?)</script>', html, re.S).group(1)
    style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
    for token, content in (('__SCRIPT_HASH__', script), ('__STYLE_HASH__', style)):
        digest = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
        html = html.replace(token, digest)
    return html


def save_report(output, html, check=False):
    output = Path(output).absolute()
    require(output.suffix == '.html', 'Output must be an HTML file')
    for directory in ('data', 'reports', 'apps', 'services', 'scripts', 'tests', '.devin'):
        require(not output.resolve().is_relative_to(ROOT / directory), 'Output must be outside source and sealed artifact directories')
    require(not output.is_symlink(), 'Output cannot be a symlink')
    content = html.encode('utf-8')
    if output.exists() or check:
        require(output.read_bytes() == content, 'Existing report differs; choose a new --output path')
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as handle:
        handle.write(content)


def main():
    parser = argparse.ArgumentParser(description='Render sealed model results as a standalone local HTML file. No training, inference or evaluation.')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--company', action='append', dest='companies', help='Company ID; repeat to include several')
    selection.add_argument('--all-companies', action='store_true', help='Embed all exported companies (larger local file)')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check', action='store_true', help='Verify an identical existing report without writing')
    args = parser.parse_args()
    try:
        data = load_report(args.companies, args.all_companies)
        html = render_report(data)
        save_report(args.output, html, args.check)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'Report not generated: {error}\n')
    print(json.dumps({'output': str(args.output.absolute()), 'companies': len(data['companies']),
                      'bytes': len(html.encode('utf-8')), 'check': args.check, 'training': False, 'inference': False,
                      'evaluation': False}, sort_keys=True))


if __name__ == '__main__':
    main()
