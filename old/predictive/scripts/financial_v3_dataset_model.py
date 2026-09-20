import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys

import duckdb

from scripts.financial_v3_audit import check_baseline
from scripts.financial_v3_cash import month_shift
from scripts.financial_v3_contract import canonical_bytes, month_end, require
from scripts.financial_v3_dataset import MONTHS, grouped, invoice_sql, check_export
from scripts.financial_v3_dataset_contract import (
    ROOT, DIRECTORY as DATASET, SOURCES, ASSIGNMENT, development_window, policy as dataset_policy,
    protected_hashes, sha256, write_new,
)
from scripts import financial_v3_dataset_model_metrics as metrics


DIRECTORY = DATASET / 'model-v1'
VERSION = 'financial-v3-dataset-v1-model-v1-receipt-extension-1'
FEATURE_FIELDS = {'receipts_known': 'cobros_operativos_conocido_eur',
    'payments_known': 'pagos_operativos_conocido_eur', 'operating_low': 'operativo_min_eur',
    'operating_high': 'operativo_max_eur', 'classification_coverage': 'cobertura_clasificacion_pct',
    'eur_coverage': 'cobertura_eur_pct'}
OUTCOME_FIELDS = ('tiene_actividad_caja', 'n_sin_eur', 'n_ambiguos_entrada', 'n_cuentas_activas',
    'cobros_operativos_eur', 'cobertura_eur_pct', 'cobertura_clasificacion_pct')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def protocol():
    return {'version': VERSION, 'scope': 'internal_retrospective_existing_synthetic_24_months',
        'extends_recommended_targets_before_outcomes': True, 'strict_headline': None, 'automatic_alerts': False,
        'targets': {
            metrics.TARGETS[0]: 'Complete observed classified operating receipts <=0.8 of mean m-2..m in >=2 of m+1..m+3; all six months observable and identical observed account IDs; otherwise unknown.',
            metrics.TARGETS[1]: 'Complete observed classified operating receipts >=1.2 of mean m-2..m in >=2 of m+1..m+3; same completeness and account identity rules.',
            metrics.TARGETS[2]: 'CURRENT receipt dip cohort: complete m <=0.8 of mean prior m-3..m-1 >0 with identical observed accounts. Next three all complete and comparable. Recovered: >=0.9 prior baseline in two CONSECUTIVE future months. Persistent: <=0.8 prior baseline in >=2 of 3. Otherwise mixed. Classes disjoint; missing warmup/followup unknown.'},
        'complete_month': 'tiene_actividad_caja true; all cash EUR n_sin_eur=0; n_ambiguos_entrada=0; finite nonnegative cobros_operativos_eur; nonempty observed booked account IDs matching panel count. Outgoing classification ambiguity alone is not receipt incompleteness.',
        'input_target_distinction': 'X uses known receipt SUBTOTAL, including incomplete inbound classification; Y requires complete classified receipts. Neither is sales, solvency, default, or generic financial improvement.',
        'feature_order': list(metrics.FEATURES), 'fit': dataset_policy()['modeling']['fit'],
        'validation': dataset_policy()['modeling']['validation'], 'purge': dataset_policy()['modeling']['purge'],
        'excluded': 'All final_test groups excluded from every feature, label, fit, inference and case selection; 208 product-only abstentions. No 2026 outcomes, v2 reserve outcomes, targets_proxy, v1 fit or v2 evaluate.',
        'outcome_queries': 'Only explicitly sealed eligible company/group/month keys for Sep 2024-Dec 2025, including current/baseline/warmup and required followup; SELECT projections only.',
        'candidates': {t: metrics.candidates(t) for t in metrics.TARGETS},
        'logistic': {'C': [.1, 1.0], 'seed': 1729, 'max_iter': 2000, 'solver': 'lbfgs', 'threads': 2},
        'preprocessing': 'Fit-only column medians, zero placeholder for entirely missing train column, missing indicator for each of 18 features, then train-only StandardScaler. No IDs, metadata, anchors, debt stocks, final invoice state, scores or outcome predictors.',
        'persistence': 'Train class frequencies conditioned on current known receipt / trailing mean3 <=.8, >=1.2 or middle; two prevalence pseudo-observations, unseen condition uses train prevalence.',
        'trend': 'Projected current +2*(current-prior2_mean), floored at zero; probability .8 if directional 20% threshold relative mean3 hit, else .2. No tuned coefficients.',
        'minimum_support': {'rows_per_class': 20, 'groups_per_class': 5, 'groups_total': 10, 'insufficient': 'All candidate models and probabilities null, reasons explicit'},
        'selection': 'Smallest mean-group Brier on same frozen internal validation; tie <=1e-12 chooses candidate list order (simpler first). No retuning. Store all predictions before selecting. Selection-biased descriptive validation, never independent test.',
        'utility': {'minimum_labeled_fraction': .2, 'precision': .6, 'recall': .4, 'ap_gain': .05,
            'brier_gain_constant': .01, 'brier_gain_simple': .005, 'maximum_ece5': .15, 'maximum_alert_rate': .5},
        'utility_scope': 'Frozen development gates, not a six-question financial acceptance rule. Binary positive metrics or Q4 one-vs-rest macro at threshold .6; min validation support same as training. Every criterion must pass.',
        'fit_budget': {'candidate_estimators_max': 12, 'refit_estimators_max': 3, 'sklearn_fits_max': 8, 'real_executions_max': 1},
        'refit': 'Only selected supported candidate per target, on union of already allowed train and validation labeled origins, maturity <=2025-12-31. Never add other origins.',
        'overlay': {'months': ['2026-01-01', '2026-08-01'], 'selection_cutoff': '2025-12-31',
            'earlier_probability': None, 'issued_at': 'actual execution timestamp, never historical issuance',
            'availability': dataset_policy()['availability'], 'input_only': 'Read allowed nonholdout pinned monthly inputs within 24 months; each monthly feature helper reads only m-2..m. Q4 condition only m-3..m. Never query their future outcomes.'},
        'uncertainty': 'No bootstrap or individual uncertainty claims; point estimates only, correlated overlapping windows, groups equally weighted. All probabilities uncalibrated.',
        'lead': 'Not reported. Any later lead-to-first-future-receipt-hit would be a descriptive window proxy, not independent economic episode anticipation.',
        'limitations': ['Synthetic measured-input experiment, internal retrospective availability assumptions',
            'No independent test; v1/v2 prior exposure remains disclosed', 'Unknown company perimeter outside observed accounts',
            'No unrestricted reconciled cash or total financial debt history', 'Receipt-only labels cannot validate all six goal questions',
            'No automatic alerts or production acceptance; no app integration in this scope']}


def runtime():
    return {'python': platform.python_version(), 'executable': str(Path(sys.executable).resolve()),
        'executable_sha256': sha256(Path(sys.executable).resolve()), 'platform': platform.platform(),
        'packages': {p: importlib.metadata.version(p) for p in ('numpy', 'scipy', 'scikit-learn', 'duckdb', 'threadpoolctl')},
        'threads': 2}


def model_code():
    return {p.relative_to(ROOT).as_posix(): sha256(p) for pattern in (
        'scripts/financial_v3_dataset_model*.py', 'tests/test_financial_v3_dataset_model*.py') for p in sorted(ROOT.glob(pattern))}


def check_hashes(bindings, root=ROOT):
    for path, expected in bindings.items():
        require(sha256(root / path) == expected, 'Changed frozen bytes: ' + path)


def read_json(path):
    return json.loads(path.read_text())


def features_and_scope():
    assignment = read_json(ROOT / ASSIGNMENT)
    index = read_json(DATASET / 'index.json')['companies']
    companies = {c['id']: c['group'] for c in index}
    with (DATASET / 'features.jsonl').open() as handle:
        rows = [json.loads(line) for line in handle]
    validate_features(rows, assignment, companies)
    require(len(rows) == 4930 and sum(r['eligible'] for r in rows) == 1881, 'Frozen dataset feature coverage mismatch')
    require(Counter(assignment.values()) == {'train': 150, 'validation': 50, 'final_test': 50}, 'Frozen group assignment mismatch')
    return rows, assignment, companies


def validate_features(rows, assignment, companies):
    seen = set()
    for row in rows:
        split, group, company, month = (row[k] for k in ('split', 'group_id', 'company_id', 'month'))
        require(split in ('train', 'validation') and assignment.get(group) == split, 'Excluded or mismatched group')
        require(companies.get(company) == group, 'Company/group mismatch')
        require(development_window(split, month) == row['window'] and row['window'] is not None, 'Origin outside purged windows')
        require(set(row['values']) == set(metrics.FEATURES), 'Exact fixed predictor allowlist required')
        require(all(v is None or type(v) in (int, float) and math.isfinite(v) for v in row['values'].values()), 'Nonfinite feature')
        key = (company, month)
        require(key not in seen, 'Duplicate feature origin')
        seen.add(key)
        maturity = str(month_end(month_shift(month, 3)))
        require(maturity <= ('2025-06-30' if split == 'train' else '2025-12-31'), 'Immature or overlapping label window')


def approved_keys(rows):
    keys = set()
    for row in rows:
        if row['eligible']:
            for offset in range(-3, 4):
                month = month_shift(row['month'], offset)
                if '2024-09-01' <= month <= '2025-12-01':
                    keys.add((row['company_id'], row['group_id'], month))
    return [dict(zip(('company_id', 'group_id', 'month'), values)) for values in sorted(keys)]


def write_pretty_new(path, value):
    with path.open('x') as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def preflight():
    require(not (DIRECTORY / 'preflight.json').exists(), 'Preflight already recorded; preserve attempt, do not silently overwrite')
    DIRECTORY.mkdir(exist_ok=True)
    initial_code = model_code()
    commands = [
        {'command': 'pwd && ls', 'exit_code': 0, 'purpose': 'root verification'},
        {'command': 'ls -ld scripts tests reports/modeling/financial-v3/dataset-v1 .venv/bin/python', 'exit_code': 0},
        {'command': '.venv/bin/python -B -m unittest tests.test_financial_v3_dataset_model', 'exit_code': 1, 'tests': 1,
            'reason': 'Expected tests-first RED: new model module absent; no data queries or real fits'},
        {'command': '.venv/bin/python -B -m unittest tests.test_financial_v3_dataset_model', 'exit_code': 0, 'tests': 21,
            'reason': 'Initial fixture suite GREEN; further scope tests added before freeze'}]
    commands_to_run = [
        ['.venv/bin/python', '-B', '-m', 'unittest', 'tests.test_financial_v3_dataset_model'],
        ['.venv/bin/python', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_financial_v3*.py']]
    results = []
    for command in commands_to_run:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        record = {'command': ' '.join(command), 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        commands.append(record)
        results.append(result.returncode == 0)
        print(result.stdout + result.stderr, file=sys.stderr)
        if result.returncode:
            break
    require(initial_code == model_code(), 'Source changed during tests')
    output = {'version': VERSION, 'recorded_at': now(), 'code_sha256': initial_code,
        'new_tests_pass': results[0], 'all_v3_tests_pass': len(results) == 2 and results[1],
        'commands': commands, 'fixture_training_only': True, 'real_outcome_queries': 0,
        'real_model_fits': 0, 'review_scope': 'Dataset review 89ed47ac previously approved scoped development; no claim this new model has independent review.'}
    write_pretty_new(DIRECTORY / 'preflight.json', output)
    require(all(results) and len(results) == 2, 'Preflight failed; preserve result and stop this attempt')
    return {'ok': True, 'new_tests_pass': True, 'all_v3_tests_pass': True, 'code_sha256': initial_code}


def freeze():
    require(not DIRECTORY.exists() or not any(p.name not in ('preflight.json',) for p in DIRECTORY.iterdir()), 'Model freeze already started; no overwrite')
    old_checks = {'dataset': check_export(), 'audit': check_baseline(ROOT)}
    require(old_checks['audit']['ok'], 'Protected audit mismatch')
    rows, assignment, companies = features_and_scope()
    code = model_code()
    require(len(code) >= 3, 'Model source/tests missing')
    preflight = read_json(DIRECTORY / 'preflight.json')
    require(preflight['code_sha256'] == code and preflight['new_tests_pass'] and preflight['all_v3_tests_pass'], 'Tests must pass on exact source before freeze')
    bindings = protected_hashes()
    for p in sorted(DATASET.rglob('*')):
        if p.is_file() and not p.is_relative_to(DIRECTORY):
            bindings[p.relative_to(ROOT).as_posix()] = sha256(p)
    for pattern in ('scripts/financial_v3_dataset*.py', 'tests/test_financial_v3_dataset*.py'):
        for p in ROOT.glob(pattern):
            bindings[p.relative_to(ROOT).as_posix()] = sha256(p)
    for p, h in read_json(DATASET / 'receipt.json')['inputs_sha256'].items():
        bindings[p] = h
    bindings.update(code)
    keys = approved_keys(rows)
    write_new(DIRECTORY / 'protocol.json', protocol())
    write_new(DIRECTORY / 'approved-keys.json', keys)
    receipt = {'version': VERSION, 'frozen_at': now(), 'before_any_new_labels_or_outcomes': True,
        'runtime': runtime(), 'bindings_sha256': dict(sorted(bindings.items())), 'new_code_sha256': code,
        'protocol_sha256': sha256(DIRECTORY / 'protocol.json'), 'keys_sha256': sha256(DIRECTORY / 'approved-keys.json'),
        'preflight_sha256': sha256(DIRECTORY / 'preflight.json'), 'approved_keys': len(keys),
        'feature_rows': len(rows), 'eligible_rows': sum(r['eligible'] for r in rows), 'old_checks': old_checks,
        'old_models': 'All old audit-protected v1/v2 models, receipts, sources and results included in bindings_sha256, as are all dataset artifacts including its verification and every new model/test source.'}
    write_new(DIRECTORY / 'seal.json', receipt)
    return {'ok': True, 'bindings': len(bindings), 'new_code_files': len(code), 'approved_keys': len(keys),
        'label_queries': 0, 'fits': 0, 'seal_sha256': sha256(DIRECTORY / 'seal.json')}


def check_frozen():
    seal = read_json(DIRECTORY / 'seal.json')
    require(read_json(DIRECTORY / 'protocol.json') == protocol(), 'Protocol drift')
    require(seal['runtime'] == runtime(), 'Runtime drift')
    require(seal['new_code_sha256'] == model_code(), 'New code or tests unbound/changed')
    for filename, key in [('protocol.json', 'protocol_sha256'), ('approved-keys.json', 'keys_sha256'), ('preflight.json', 'preflight_sha256')]:
        require(sha256(DIRECTORY / filename) == seal[key], 'Frozen artifact changed: ' + filename)
    check_hashes(seal['bindings_sha256'])
    return seal


def claim_execution(directory, receipt):
    write_new(directory / 'execution-receipt.json', {**receipt, 'claimed_at': now(), 'exclusive_attempt': 1,
        'before_label_query': True, 'interruption_policy': 'No blind repeat; this receipt permanently consumes model-v1 execution even on failure.'})


def complete(row):
    amount = row.get('cobros_operativos_eur')
    return bool(row.get('tiene_actividad_caja') and row.get('n_sin_eur') == 0 and row.get('n_ambiguos_entrada', 0) == 0
        and type(amount) in (int, float) and math.isfinite(amount) and amount >= 0)


def comparable(history, months):
    selected = [history.get(m, {}) for m in months]
    reasons = []
    if not all(complete(r) for r in selected):
        reasons.append('incomplete_receipt_months')
    perimeters = [tuple(r.get('_perimeter', [])) for r in selected]
    if not all(perimeters) or len(set(perimeters)) != 1 or any(len(p) != r.get('n_cuentas_activas') for p, r in zip(perimeters, selected)):
        reasons.append('changed_or_missing_observed_accounts')
    return reasons


def current_dip(history, origin):
    months = [month_shift(origin, i) for i in range(-3, 1)]
    reasons = comparable(history, months)
    baseline = None
    if not reasons:
        baseline = statistics.mean(history[m]['cobros_operativos_eur'] for m in months[:3])
        if baseline <= 0:
            reasons.append('nonpositive_receipt_reference')
        elif history[origin]['cobros_operativos_eur'] > .8 * baseline:
            reasons.append('not_current_receipt_dip')
    return {'eligible': not reasons, 'baseline': baseline, 'reasons': reasons, 'uses_future': False}


def labels_for(feature, history):
    origin = feature['month']
    require(development_window(feature['split'], origin) is not None, 'Labels forbidden outside frozen development origins')
    future_months = [month_shift(origin, i) for i in (1, 2, 3)]
    base_months = [month_shift(origin, i) for i in (-2, -1, 0)]
    result = {}
    for target in metrics.TARGETS:
        reasons = [] if feature['eligible'] else ['input_ineligible']
        baseline = None
        value = None
        condition = current_dip(history, origin) if target == metrics.TARGETS[2] and not reasons else None
        if not reasons:
            if condition is not None:
                reasons.extend(condition['reasons'])
                baseline = condition['baseline']
                window = [month_shift(origin, -3)] + base_months + future_months
            else:
                window = base_months + future_months
            if not reasons:
                reasons.extend(comparable(history, window))
            if not reasons:
                if condition is None:
                    baseline = statistics.mean(history[m]['cobros_operativos_eur'] for m in base_months)
                if baseline <= 0:
                    reasons.append('nonpositive_receipt_reference')
            if not reasons:
                future = [history[m]['cobros_operativos_eur'] for m in future_months]
                if target == metrics.TARGETS[2]:
                    recovered = any(future[i] >= .9 * baseline and future[i+1] >= .9 * baseline for i in (0, 1))
                    persistent = sum(v <= .8 * baseline for v in future) >= 2
                    require(not (recovered and persistent), 'Conditional classes overlap')
                    value = 'recovered' if recovered else 'persistent' if persistent else 'mixed'
                else:
                    hits = [v <= .8 * baseline if target == metrics.TARGETS[0] else v >= 1.2 * baseline for v in future]
                    value = int(sum(hits) >= 2)
        result[target] = {'value': value, 'reasons': sorted(set(reasons)), 'baseline': baseline,
            'matured_at': str(month_end(future_months[-1])) if value is not None else None,
            'required_followup_end': str(month_end(future_months[-1])), 'condition': condition,
            'scope': 'classified_operating_receipt_proxy_not_solvency'}
    return result


def quote(text):
    return "'" + text.replace("'", "''") + "'"


def keys_sql(keys):
    require(bool(keys), 'No approved keys')
    values = ','.join('(' + ','.join(quote(k[n]) for n in ('company_id', 'group_id', 'month')) + ')' for k in keys)
    return '(SELECT company_id,group_id,CAST(month AS DATE) AS month FROM (VALUES ' + values + ') k(company_id,group_id,month))'


def perimeter_sql(source, keys):
    return f"""SELECT t.company_id,CAST(date_trunc('month',t.date_ok) AS DATE) AS month,
        list(DISTINCT t.product_id ORDER BY t.product_id) AS accounts
        FROM {source} t JOIN {keys} k ON t.company_id=k.company_id AND CAST(date_trunc('month',t.date_ok) AS DATE)=k.month
        WHERE t.is_booked AND t.product_source='banking' AND t.product_type IN ('checking','saving','wallet')
        AND CAST(t.date_ok AS DATE)>=k.month AND CAST(t.date_ok AS DATE)<=last_day(k.month)
        GROUP BY 1,2 ORDER BY 1,2"""


class ScopedInputs:
    def __init__(self, directory=DIRECTORY):
        require((directory / 'execution-receipt.json').exists(), 'Execution receipt required before any source query')
        self.directory = directory
        self.paths = {Path(p).stem: ROOT / p for p in SOURCES}
        self.con = duckdb.connect(':memory:', config={'threads': 2, 'memory_limit': '768MB', 'temp_directory': ''})
        self.ledger = []

    def source(self, name):
        require(name in ('companies', 'panel_flujos', 'panel_deuda', 'transactions', 'invoices'), 'Source outside modeling input allowlist')
        return 'read_parquet(' + quote(self.paths[name].as_posix()) + ')'

    def select(self, name, sql, purpose, keys):
        statements = self.con.extract_statements(sql)
        require(len(statements) == 1 and statements[0].type == duckdb.StatementType.SELECT, 'Read-only SELECT only')
        if purpose == 'outcome':
            require(all('2024-09-01' <= k['month'] <= '2025-12-01' for k in keys), 'Forbidden outcome dates')
        require(purpose in ('outcome', 'inference_input'), 'Undeclared query purpose')
        entry = {'sequence': len(self.ledger)+1, 'name': name, 'purpose': purpose, 'sql': sql,
            'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(), 'keys_sha256': digest(keys), 'key_count': len(keys),
            'months': [min(k['month'] for k in keys), max(k['month'] for k in keys)], 'started_at': now(),
            'source_hashes': {p.relative_to(ROOT).as_posix(): sha256(p) for p in self.paths.values() if p.stem in ('companies', 'panel_flujos', 'panel_deuda', 'transactions', 'invoices')}}
        write_new(self.directory / ('query-%02d-intent.json' % entry['sequence']), entry)
        self.ledger.append(entry)
        cur = self.con.execute(sql)
        names = [c[0] for c in cur.description]
        rows = [dict(zip(names, [v.isoformat() if isinstance(v, (date, datetime)) else v for v in row])) for row in cur.fetchall()]
        entry.update(rows=len(rows), result_sha256=digest(rows), completed_at=now())
        write_new(self.directory / ('query-%02d-result.json' % entry['sequence']), entry)
        return rows

    def flows(self, keys, purpose):
        k = keys_sql(keys)
        actual = self.select(purpose + '_identity', f'SELECT DISTINCT c.company_id,c.group_id FROM {self.source("companies")} c JOIN {k} k ON c.company_id=k.company_id ORDER BY 1,2', purpose, keys)
        require({(r['company_id'], r['group_id']) for r in actual} == {(r['company_id'], r['group_id']) for r in keys}, 'Pinned company/group mismatch')
        fields = list(OUTCOME_FIELDS)
        if purpose == 'inference_input':
            fields += [f for f in FEATURE_FIELDS.values() if f not in fields]
        select = ','.join('f.' + f for f in fields)
        rows = self.select(purpose + '_flows', f'SELECT f.company_id,f.group_id,CAST(f.month AS DATE) AS month,{select} FROM {self.source("panel_flujos")} f JOIN {k} k ON f.company_id=k.company_id AND f.group_id=k.group_id AND f.month=k.month ORDER BY 1,3', purpose, keys)
        allowed = {(v['company_id'], v['group_id'], v['month']) for v in keys}
        require(all((r['company_id'], r['group_id'], r['month']) in allowed for r in rows), 'Unapproved source key')
        result = grouped(rows)
        perimeters = self.select(purpose + '_perimeters', perimeter_sql(self.source('transactions'), k), purpose, keys)
        for r in perimeters:
            require(r['month'] in result.get(r['company_id'], {}), 'Perimeter outside flow keys')
            result[r['company_id']][r['month']]['_perimeter'] = r['accounts']
        return result

    def inference(self, companies, assignment):
        keys = [{'company_id': c, 'group_id': g, 'month': m} for c, g in sorted(companies.items()) if assignment[g] != 'final_test'
            for m in MONTHS if m >= '2025-10-01']
        flows = self.flows(keys, 'inference_input')
        k = keys_sql(keys)
        s = self.source
        service = grouped(self.select('inference_service', f'SELECT d.company_id,CAST(d.month AS DATE) AS month,d.servicio_deuda_conocido_eur FROM {s("panel_deuda")} d JOIN {k} k ON d.company_id=k.company_id AND d.month=k.month ORDER BY 1,2', 'inference_input', keys))
        ids = '(SELECT DISTINCT company_id FROM ' + k + ')'
        inv = f'(SELECT i.operation_id,i.company_id,i.amount,i.amount_eur,i.issuance_date_ok,i.maturity_date_ok,i.document_type_norm FROM {s("invoices")} i JOIN {ids} a ON i.company_id=a.company_id)'
        counts = self.select('inference_invoice_identity', f'SELECT count(*) AS n,count(DISTINCT operation_id) AS unique_n FROM {inv}', 'inference_input', keys)[0]
        require(counts['n'] == counts['unique_n'], 'Duplicate or null invoice identity')
        documents = grouped(self.select('inference_documents', 'SELECT d.* FROM (' + invoice_sql(inv) + f') d JOIN {k} k ON d.company_id=k.company_id AND d.month=k.month ORDER BY d.company_id,d.month', 'inference_input', keys))
        return flows, service, documents


def monthly_features(company, group, split, month, flows, documents, service):
    require(split in ('train', 'validation'), 'Final-test inference prohibited')
    history = [flows.get(month_shift(month, i), {}) for i in (-2, -1, 0)]
    reasons = []
    if not all(r.get('tiene_actividad_caja') for r in history):
        reasons.append('three_contiguous_cash_months_missing')
    if any(r.get('n_sin_eur') != 0 for r in history):
        reasons.append('eur_coverage_incomplete')
    receipts = [r.get('cobros_operativos_conocido_eur') for r in history]
    def known(values):
        return all(type(v) in (float, int) and math.isfinite(v) for v in values)
    if not known(receipts) or math.fsum(v or 0 for v in receipts) <= 0:
        reasons.append('positive_observed_receipt_reference_missing')
    values = {}
    for name, field in FEATURE_FIELDS.items():
        nums = [r.get(field) for r in history]
        values[name] = nums[-1]
        values[name + '_mean3'] = statistics.mean(nums) if known(nums) else None
    for key in ('ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows'):
        values[key] = documents.get(month, {}).get(key)
    values['service_paid_known'] = service.get(month, {}).get('servicio_deuda_conocido_eur')
    return {'company_id': company, 'group_id': group, 'month': month, 'split': split,
        'values': values, 'eligible': not reasons, 'reasons': reasons,
        'missing_features': [k for k in metrics.FEATURES if values[k] is None]}


def overlay_time_reason(month):
    if month <= '2025-12-01':
        return 'before_selection_cutoff_no_backcast'
    return None if '2026-01-01' <= month <= '2026-08-01' else 'outside_frozen_product_window'


def preselection_predictions(model, features, labels, target, flows):
    result = []
    for f in features:
        if f['split'] != 'validation':
            continue
        condition = current_dip(flows.get(f['company_id'], {}), f['month']) if target == metrics.TARGETS[2] else None
        reasons = list(f['reasons'])
        if condition and not condition['eligible']:
            reasons.extend(condition['reasons'])
        if model is None:
            reasons.append('insufficient_training_support')
        label = labels[(f['company_id'], f['month'])][target]
        result.append({'company_id': f['company_id'], 'group_id': f['group_id'], 'origin': f['month'],
            'target': target, 'label': label['value'], 'label_reasons': label['reasons'],
            'probabilities': metrics.predict(model, f) if f['eligible'] and not reasons else None,
            'prediction_reasons': reasons, 'current_dip': condition, 'train_label_cutoff': '2025-06-30',
            'preselection': True, 'historically_issued': False})
    return result


def support_report(features, labels, target, split):
    selected = [(f, labels[(f['company_id'], f['month'])][target]) for f in features if f['split'] == split]
    known_rows = [{'feature': f, 'value': label['value']} for f, label in selected if label['value'] is not None]
    reasons = Counter(reason for _, label in selected for reason in label['reasons'])
    eligible = sum(f['eligible'] for f, _ in selected)
    return known_rows, {'origins': len(selected), 'eligible_inputs': eligible, 'labeled': len(known_rows),
        'unknown_or_not_in_cohort': len(selected)-len(known_rows), 'censor_reasons_nonexclusive': dict(reasons),
        'labeled_fraction_of_eligible': len(known_rows) / eligible if eligible else 0,
        'support': metrics.support(known_rows, metrics.classes(target))}


def schema():
    return {'version': VERSION, 'kind': 'operating_receipt_probability_overlay', 'authorization': 'API must authorize company before lookup; index is not permission to access. No loader/app changed here.',
        'index': 'companies: [{company_id,group_id,path,sha256,bytes}]; excluded_product_only: [{company_id,group_id,reason}]',
        'profile': {'company_id': 'authorized nonholdout company', 'group_id': 'existing group', 'version': VERSION,
            'scope': 'receipt-only internal retrospective synthetic, not financial health', 'points': '24 origin points', 'model_refs': 'target -> models.json#/target'},
        'point': {'month': 'calendar YYYY-MM-01', 'as_of': 'calendar month-end', 'known_on': None, 'headline': None,
            'issued_at': 'actual export time; not historical issuance', 'selection_cutoff': '2025-12-31',
            'features': 'fixed raw input object or null before selection; known subtotal not complete target',
            'missing_features': 'names of null fields', 'input_reasons': 'eligibility reasons',
            'forecast': 'per target: probabilities {class:finite 0..1}|null, reasons[], horizon [m+1,m+3], current_dip condition (Q4 only)',
            'explanation': 'models.json standardized coefficients and exact transformation; no causal interpretation'},
        'displays': {metrics.TARGETS[0]: 'Observed operating-receipt contraction, next 3 months',
            metrics.TARGETS[1]: 'Observed operating-receipt expansion, next 3 months',
            metrics.TARGETS[2]: 'Current operating-receipt dip: recovery / persistence / mixed over next 3 months'},
        'required_disclaimers': protocol()['limitations'], 'probability_status': 'uncalibrated, not confidence or solvency',
        'no_financial_fabrication': 'No total debt, unrestricted cash, full-health score or generic improvement labels.'}


def export_overlay(models, inputs, companies, assignment, issued_at):
    flows, service, documents = inputs.inference(companies, assignment)
    profiles = DIRECTORY / 'profiles'
    profiles.mkdir()
    index, excluded, coverage = [], [], Counter()
    for company, group in sorted(companies.items()):
        split = assignment[group]
        if split == 'final_test':
            excluded.append({'company_id': company, 'group_id': group, 'reason': 'final_test_product_only_no_model_inference'})
            continue
        points = []
        for month in MONTHS:
            time_reason = overlay_time_reason(month)
            f = None if time_reason else monthly_features(company, group, split, month, flows.get(company, {}), documents.get(company, {}), service.get(company, {}))
            point = {'month': month, 'as_of': str(month_end(month)), 'known_on': None, 'headline': None,
                'issued_at': issued_at, 'actually_issued_historically': False, 'selection_cutoff': '2025-12-31',
                'features': f['values'] if f else None, 'missing_features': f['missing_features'] if f else [],
                'input_reasons': f['reasons'] if f else [time_reason], 'forecast': {}}
            coverage['points'] += 1
            if f:
                coverage['postselection_points'] += 1
                coverage['eligible_input_points'] += f['eligible']
            for target in metrics.TARGETS:
                reasons = [time_reason] if time_reason else list(f['reasons'])
                condition = current_dip(flows.get(company, {}), month) if target == metrics.TARGETS[2] and f else None
                if condition and not condition['eligible']:
                    reasons.extend(condition['reasons'])
                if models[target] is None:
                    reasons.append('insufficient_training_support_or_no_selected_model')
                p = metrics.predict(models[target], f) if not reasons else None
                point['forecast'][target] = {'probabilities': p, 'reasons': sorted(set(reasons)),
                    'model_fitted_at': models[target]['fitted_at'] if models[target] else None,
                    'model_selected_at': models[target]['selected_at'] if models[target] else None,
                    'fit_label_cutoff': '2025-12-31',
                    'horizon': [month_shift(month, 1), month_shift(month, 3)], 'current_dip': condition,
                    'calibration': 'uncalibrated_internal_retrospective', 'target_scope': 'classified_operating_receipts_not_solvency'}
                coverage[target + '_probability_points'] += p is not None
            points.append(point)
        profile = {'company_id': company, 'group_id': group, 'version': VERSION,
            'scope': 'receipt-only internal retrospective synthetic; not financial health', 'points': points,
            'model_refs': {t: 'models.json#/' + t for t in metrics.TARGETS},
            'availability_assumption': dataset_policy()['availability'], 'automatic_alerts': False}
        path = profiles / (company + '.json')
        write_new(path, profile)
        index.append({'company_id': company, 'group_id': group, 'path': path.relative_to(DIRECTORY).as_posix(),
            'sha256': sha256(path), 'bytes': path.stat().st_size})
    require(len(excluded) == 208 and len(index) == 1078, 'Unexpected authorized/excluded company scope')
    write_new(DIRECTORY / 'index.json', {'version': VERSION, 'companies': index, 'excluded_product_only': excluded})
    write_new(DIRECTORY / 'schema.json', schema())
    return {**coverage, 'companies': len(index), 'final_test_product_only_abstentions': len(excluded),
        'maximum_profile_bytes': max(r['bytes'] for r in index), 'index_bytes': (DIRECTORY / 'index.json').stat().st_size}


def execute():
    seal = check_frozen()
    claim_execution(DIRECTORY, {'version': VERSION, 'seal_sha256': sha256(DIRECTORY / 'seal.json'),
        'protocol_sha256': seal['protocol_sha256'], 'runtime': runtime()})
    inputs = None
    fit_log = []
    def fit(name, rows, target, stage):
        adequate = metrics.support(rows, metrics.classes(target))['adequate']
        record = {'stage': stage, 'target': target, 'candidate': name, 'fitted': False,
            'sklearn_fit': adequate and name.startswith('logistic'), 'train_rows': len(rows),
            'labels_mature_by': '2025-06-30' if stage == 'candidate' else '2025-12-31', 'status': 'started'}
        fit_log.append(record)
        require(len(fit_log) <= 15 and sum(r['sklearn_fit'] for r in fit_log) <= 8, 'Fit budget exhausted')
        write_new(DIRECTORY / ('fit-%02d-intent.json' % len(fit_log)), record)
        model = metrics.fit_candidate(name, rows, target)
        record.update(fitted=model is not None, status='completed')
        write_new(DIRECTORY / ('fit-%02d.json' % len(fit_log)), record)
        return model
    try:
        inputs = ScopedInputs()
        features, assignment, companies = features_and_scope()
        keys = approved_keys(features)
        require(keys == read_json(DIRECTORY / 'approved-keys.json'), 'Approved keys changed')
        flows = inputs.flows(keys, 'outcome')
        labels = {}
        label_rows = []
        for f in features:
            key = (f['company_id'], f['month'])
            labels[key] = labels_for(f, flows.get(f['company_id'], {}))
            label_rows.append({'company_id': key[0], 'group_id': f['group_id'], 'month': key[1], 'split': f['split'],
                'labels': labels[key], 'source_binding': 'seal.json#/bindings_sha256', 'query_binding': 'queries.json'})
        write_new(DIRECTORY / 'labels.json', label_rows)
        report, final_models = {}, {}
        for target in metrics.TARGETS:
            train, train_coverage = support_report(features, labels, target, 'train')
            valid, valid_coverage = support_report(features, labels, target, 'validation')
            fitted, scores, prediction_rows = {}, {}, []
            for name in metrics.candidates(target):
                fitted[name] = fit(name, train, target, 'candidate')
                model = fitted[name]
                all_predictions = preselection_predictions(model, features, labels, target, flows)
                by_key = {(r['company_id'], r['origin']): r for r in all_predictions}
                p = [by_key[(r['feature']['company_id'], r['feature']['month'])]['probabilities'] for r in valid]
                scores[name] = metrics.evaluate(valid, p, metrics.classes(target))
                scores[name]['all_validation_origins'] = len(all_predictions)
                scores[name]['all_validation_predictions'] = sum(r['probabilities'] is not None for r in all_predictions)
                scores[name]['unknown_label_predictions'] = sum(r['probabilities'] is not None and r['label'] is None for r in all_predictions)
                prediction_rows.extend({**r, 'candidate': name} for r in all_predictions)
            write_new(DIRECTORY / (target + '-validation-predictions.json'), prediction_rows)
            write_new(DIRECTORY / (target + '-candidate-models.json'), fitted)
            selected = metrics.choose(scores, target)
            selected_at = now()
            write_new(DIRECTORY / (target + '-selection.json'), {'selected': selected, 'selected_at': selected_at,
                'prediction_sha256_before_selection': sha256(DIRECTORY / (target + '-validation-predictions.json')),
                'selection_cutoff': '2025-12-31', 'validation_reused_for_selection': True})
            final = fit(selected, train + valid, target, 'refit') if selected else None
            if final:
                final.update(fitted_at=now(), selected_at=selected_at, selection_cutoff='2025-12-31', fit_origin_windows=[protocol()['fit']['origins'], protocol()['validation']['origins']],
                    fit_label_cutoff='2025-12-31', historical_issuance=False)
            final_models[target] = final
            report[target] = {'training': train_coverage, 'validation': valid_coverage, 'candidates': scores,
                'selected': selected, 'utility': metrics.utility(scores, selected, valid_coverage['labeled_fraction_of_eligible'], protocol()['utility']),
                'abstention_reasons': [] if final else ['insufficient_training_support_or_no_validation_predictions'],
                'selection_bias': 'Same internal validation used to choose model; not independent test.'}
        require(sum(r['sklearn_fit'] for r in fit_log) <= 8 and len(fit_log) <= 15, 'Frozen fit budget exceeded')
        write_new(DIRECTORY / 'models.json', final_models)
        write_new(DIRECTORY / 'metrics.json', {'version': VERSION, 'targets': report, 'fit_log': fit_log,
            'scope': protocol()['scope'], 'limitations': protocol()['limitations'], 'confidence_intervals': None})
        summary = {t: {'training': r['training'], 'validation': r['validation'], 'selected': r['selected'],
            'utility': r['utility'], 'candidates': {name: {k: v.get(k) for k in ('average_precision', 'brier',
                'mean_group_brier', 'mean_company_brier', 'precision', 'recall', 'alert_rate', 'ece5',
                'predicted_rows', 'missing_predictions', 'all_validation_predictions', 'unknown_label_predictions')}
                for name, v in r['candidates'].items()}} for t, r in report.items()}
        write_pretty_new(DIRECTORY / 'metrics-summary.json', summary)
        overlay = export_overlay(final_models, inputs, companies, assignment, now())
        write_new(DIRECTORY / 'queries.json', {'queries': inputs.ledger, 'outcome_query_count': sum(q['purpose'] == 'outcome' for q in inputs.ledger),
            'inference_input_query_count': sum(q['purpose'] == 'inference_input' for q in inputs.ledger), 'no_2026_outcome_queries': True})
        check_frozen()
        old = {'audit': check_baseline(ROOT), 'dataset': check_export()}
        require(old['audit']['ok'], 'Protected old files changed')
        write_new(DIRECTORY / 'execution-result.json', {'ok': True, 'finished_at': now(), 'real_executions': 1,
            'fit_log': fit_log, 'overlay': overlay, 'old_checks': old})
        artifacts = {p.relative_to(DIRECTORY).as_posix(): sha256(p) for p in sorted(DIRECTORY.rglob('*')) if p.is_file()}
        write_new(DIRECTORY / 'manifest.json', {'version': VERSION, 'artifacts_sha256': artifacts,
            'seal_sha256': sha256(DIRECTORY / 'seal.json'), 'new_code_sha256': model_code(), 'overlay': overlay,
            'fit_counts': {'candidate_attempts': sum(r['stage'] == 'candidate' for r in fit_log),
                'fitted_estimators': sum(r['fitted'] for r in fit_log), 'sklearn_fits': sum(r['sklearn_fit'] for r in fit_log)},
            'verification_excluded_to_avoid_self_reference': True})
        write_pretty_new(DIRECTORY / 'verification.json', {'version': VERSION, 'status': 'executed_internal_receipt_proxy_not_full_health_acceptance',
            'execution_attempts': 1, 'commands_before_freeze': read_json(DIRECTORY / 'preflight.json')['commands'],
            'model_commands': [
                {'command': '.venv/bin/python -B -m scripts.financial_v3_dataset_model --preflight', 'exit_code': 0, 'real_label_queries': 0, 'real_fits': 0},
                {'command': '.venv/bin/python -B -m scripts.financial_v3_dataset_model --freeze', 'exit_code': 0, 'label_queries': 0, 'fits': 0},
                {'command': '.venv/bin/python -B -m scripts.financial_v3_dataset_model --execute', 'result': 'completed', 'fits': fit_log}],
            'old_checks_after': old, 'old_bound_files_rehashed_equal': len(seal['bindings_sha256']),
            'manifest_sha256': sha256(DIRECTORY / 'manifest.json'), 'seal_sha256': sha256(DIRECTORY / 'seal.json'),
            'source_query_ledger': 'queries.json', 'metrics': 'metrics.json', 'artifact_hashes': 'manifest.json#/artifacts_sha256',
            'coverage': overlay, 'strict_headline': None, 'automatic_alerts': False, 'new_files_only': True,
            'no_network_install_config_commit_push_subagents': True, 'limitations': protocol()['limitations']})
        return {'ok': True, 'real_executions': 1, 'sklearn_fits': sum(r['sklearn_fit'] for r in fit_log),
            'selected': {t: r['selected'] for t, r in report.items()}, 'utility_pass': {t: r['utility']['pass'] for t, r in report.items()}, 'overlay': overlay}
    except Exception as exc:
        if not (DIRECTORY / 'execution-result.json').exists():
            write_new(DIRECTORY / 'execution-result.json', {'ok': False, 'failed_at': now(), 'error': str(exc),
                'type': type(exc).__name__, 'fit_log': fit_log, 'queries_attempted': inputs.ledger if inputs else [],
                'stop': 'Execution consumed. Preserve all partial artifacts; do not repeat.'})
        raise
    finally:
        if inputs:
            inputs.con.close()


def check():
    seal = check_frozen()
    manifest = read_json(DIRECTORY / 'manifest.json')
    actual = {p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob('*') if p.is_file()}
    require(actual == set(manifest['artifacts_sha256']) | {'manifest.json', 'verification.json'}, 'Unbound artifact addition/removal')
    check_hashes(manifest['artifacts_sha256'], DIRECTORY)
    require(manifest['new_code_sha256'] == model_code(), 'Unbound source')
    verification = read_json(DIRECTORY / 'verification.json')
    require(verification['manifest_sha256'] == sha256(DIRECTORY / 'manifest.json'), 'Verification manifest binding changed')
    require(manifest['seal_sha256'] == sha256(DIRECTORY / 'seal.json'), 'Seal binding changed')
    index = read_json(DIRECTORY / 'index.json')
    require(len(index['companies']) == 1078 and len(index['excluded_product_only']) == 208, 'Overlay scope count')
    assignment = read_json(ROOT / ASSIGNMENT)
    counts = Counter()
    for company in index['companies']:
        require(assignment[company['group_id']] != 'final_test', 'Final-test inference')
        require(sha256(DIRECTORY / company['path']) == company['sha256'], 'Profile index hash')
        profile = read_json(DIRECTORY / company['path'])
        require([p['month'] for p in profile['points']] == MONTHS, 'Overlay monthly grid')
        require(profile['company_id'] == company['company_id'] and profile['group_id'] == company['group_id'], 'Profile identity mismatch')
        for point in profile['points']:
            require(point['headline'] is None and point['known_on'] is None and not point['actually_issued_historically'], 'Fabricated historical/full-health claim')
            counts['points'] += 1
            for target, forecast in point['forecast'].items():
                p = forecast['probabilities']
                if p is not None:
                    require(not overlay_time_reason(point['month']) and not forecast['reasons'], 'Backcast or ineligible forecast')
                    require(set(p) == {str(c) for c in metrics.classes(target)}, 'Invalid probability classes')
                    require(all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in p.values()), 'Nonfinite probability')
                    require(abs(sum(p.values())-1) < 1e-12, 'Probability sum')
                    counts[target + '_probability_points'] += 1
    for c in index['excluded_product_only']:
        require(assignment[c['group_id']] == 'final_test', 'Excluded group mismatch')
    for k, n in counts.items():
        require(n == manifest['overlay'][k], 'Overlay count mismatch: ' + k)
    require(manifest['fit_counts']['sklearn_fits'] <= 8, 'Fit budget')
    old = {'audit': check_baseline(ROOT), 'dataset': check_export()}
    require(old['audit']['ok'], 'Old audit mismatch')
    return {'ok': True, 'bound_files': len(seal['bindings_sha256']), 'artifacts_checked': len(manifest['artifacts_sha256']),
        'profiles': len(index['companies']), 'final_test_abstentions': 208, 'counts': dict(counts),
        'label_queries': 0, 'source_queries': 0, 'fits': 0, 'writes': 0, 'old_checks': old}


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'freeze', 'execute', 'check'):
        choice.add_argument('--' + name, action='store_true')
    args = parser.parse_args()
    action = preflight if args.preflight else freeze if args.freeze else execute if args.execute else check
    print(json.dumps(action(), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
