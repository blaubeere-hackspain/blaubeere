import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import duckdb


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = '20260919T124814Z-e5302b58'
RUN_MANIFEST_SHA256 = '88059a142248927f2c6ae0816993eae6e1177abf3138f089df09d93bc642ce6b'
OUTPUT = Path('reports/modeling/financial-v3')
DOCS = ('docs/PRODUCT.md', 'docs/REQUIREMENTS.md', 'docs/DASHBOARD.md',
        'readme.md', 'reports/planning/financial-scoring-plan.md')
TABLES = ('groups', 'companies', 'banking_products', 'debt_products',
          'debt_schedule_config', 'balances', 'transactions', 'invoices')
MARTS = ('fx_rates', 'observabilidad', 'panel_flujos', 'panel_deuda',
         'panel_cobro', 'panel_evidencia')
ALLOWED = {f'data/clean/{name}.parquet' for name in TABLES}
ALLOWED.update(f'data/marts/{name}.parquet' for name in MARTS)
ALLOWED.add('data/interim/tx_flow_class.parquet')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pinned_input(root, relative):
    if relative not in ALLOWED:
        raise ValueError(f'Not an allowed pinned input: {relative}')
    return root / 'data/runs' / RUN_ID / relative


def assignment_summary(assignment):
    counts = Counter(assignment.values())
    if len(assignment) != 250 or counts['final_test'] != 50 or set(counts) - {'train', 'validation', 'final_test'}:
        raise ValueError('Expected 250 assigned groups including all 50 excluded final_test groups')
    return {'assignment_counts': dict(sorted(counts.items())), 'excluded_groups': 50,
            'development_groups': 200, 'new_confirmatory_groups_identified': 0}


def select_rows(con, sql, max_rows=512):
    statements = con.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError('Exactly one read-only SELECT statement required')
    cursor = con.execute(sql)
    columns = [entry[0] for entry in cursor.description]
    rows = cursor.fetchmany(max_rows + 1)
    if len(rows) > max_rows:
        raise ValueError('Aggregate result exceeded row bound')
    return [dict(zip(columns, row)) for row in rows]


def ledger_coverage_sql(source):
    return f"""SELECT active_months, span_months-active_months AS internal_gap_months,
        count(*) AS products FROM (
        SELECT product_id, count(DISTINCT month) AS active_months,
        date_diff('month', min(month), max(month))+1 AS span_months
        FROM {source} GROUP BY product_id) x GROUP BY 1,2 ORDER BY 1,2"""


def compare_hashes(expected, actual):
    return [{'path': key, 'reason': 'added' if key not in expected else
             'missing' if key not in actual else 'changed'}
            for key in sorted(expected.keys() | actual.keys())
            if expected.get(key) != actual.get(key)]


def protected_paths(root):
    result = set()
    for directory in ('data', 'xray', 'scripts', 'tests', 'reports/modeling',
                      'reports/readiness', 'reports/quality', 'apps/app', 'services'):
        for path in (root / directory).rglob('*'):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in {'__pycache__', 'node_modules', '.next', 'target', '.git'} for part in relative.parts):
                continue
            if relative.is_relative_to(OUTPUT) or 'financial_v3' in path.name:
                continue
            if path.is_symlink():
                raise ValueError(f'Unexpected protected symlink: {relative}')
            result.add(relative.as_posix())
    for relative in ('data_dictionary.md', 'reports/build_manifest.json', 'requirements.txt',
                     'requirements-model.txt', 'requirements-model.in', 'Cargo.toml',
                     'Cargo.lock', 'package.json', 'bun.lock', '.gitattributes', '.gitignore'):
        if (root / relative).is_file():
            result.add(relative)
    return sorted(result)


def current_hashes(root):
    return {relative: sha256(root / relative) for relative in protected_paths(root)}


def git(root, *arguments):
    return subprocess.run(['git', *arguments], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def write_exclusive(path, value):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False) + '\n')


def binding_checks(root):
    run = root / 'data/runs' / RUN_ID
    if sha256(run / 'manifest.json') != RUN_MANIFEST_SHA256:
        raise ValueError('Pinned run manifest mismatch')
    manifest = json.loads((run / 'manifest.json').read_text())
    checks = {}
    for relative, expected in manifest['outputs'].items():
        checks[f'run_output:{relative}'] = sha256(run / relative) == expected
    for relative, expected in manifest['source'].items():
        checks[f'frozen_source:{relative}'] = sha256(run / 'source' / relative) == expected
    for relative, expected in manifest['inputs'].items():
        checks[f'raw_byte_hash_only:{relative}'] = sha256(root / 'data' / relative) == expected
    v1 = json.loads((root / 'reports/modeling/experiment-v1/model_manifest.json').read_text())
    for relative, expected in v1['source_sha256'].items():
        checks[f'v1_source:{relative}'] = sha256(root / relative) == expected
    checks['v1_run_binding'] = v1['input_binding']['run_id'] == RUN_ID
    for relative, expected in v1['artifacts_sha256'].items():
        checks[f'v1_artifact:{relative}'] = sha256(root / 'reports/modeling/experiment-v1' / relative) == expected
    v2 = json.loads((root / 'reports/modeling/trajectory-v2/execution-manifest.json').read_text())
    for relative, expected in v2['implementation_sha256'].items():
        checks[f'v2_source:{relative}'] = sha256(root / relative) == expected
    if not all(checks.values()):
        raise ValueError(f'Binding mismatches: {[key for key, ok in checks.items() if not ok]}')
    return checks


def source_audit(root):
    assignment = json.loads((root / 'reports/modeling/protocol-v1/group_assignment.json').read_text())
    assignment_info = assignment_summary(assignment)
    excluded = ','.join("'" + group.replace("'", "''") + "'" for group, split in sorted(assignment.items()) if split == 'final_test')
    paths = {name: pinned_input(root, f'data/clean/{name}.parquet') for name in TABLES}
    paths.update({name: pinned_input(root, f'data/marts/{name}.parquet') for name in MARTS})
    paths['tx_flow_class'] = pinned_input(root, 'data/interim/tx_flow_class.parquet')
    def read(name):
        return "read_parquet('" + paths[name].as_posix().replace("'", "''") + "')"
    ctes = [f"companies AS (SELECT * FROM {read('companies')} WHERE group_id NOT IN ({excluded}))"]
    for name in TABLES:
        if name not in {'companies', 'groups'}:
            ctes.append(f'{name} AS (SELECT s.* FROM {read(name)} s JOIN companies c USING(company_id))')
    for name in ('panel_flujos', 'panel_deuda'):
        ctes.append(f'{name} AS (SELECT s.* FROM {read(name)} s JOIN companies c USING(company_id))')
    ctes.append("cash AS (SELECT * FROM transactions WHERE is_booked AND product_source='banking' AND product_type IN ('checking','saving','wallet') AND month >= DATE '2024-09-01' AND month < DATE '2026-09-01')")
    prefix = 'WITH ' + ',\n'.join(ctes) + '\n'
    queries = {}
    results = {}
    con = duckdb.connect(':memory:', config={'threads': 2, 'memory_limit': '512MB', 'temp_directory': ''})
    try:
        schemas = {}
        inventory = {}
        for name, path in paths.items():
            cursor = con.execute(f'SELECT * FROM {read(name)} LIMIT 0')
            schemas[name] = [{'name': field[0], 'type': str(field[1])} for field in cursor.description]
            inventory[name] = select_rows(con, f'SELECT count(*) AS rows FROM {read(name)}')[0]['rows']
        def query(name, sql, scoped=True):
            full = prefix + sql if scoped else sql
            queries[name] = full
            results[name] = select_rows(con, full)
        query('company_scope', 'SELECT count(*) AS companies, count(DISTINCT group_id) AS groups FROM companies')
        query('source_completeness', """SELECT 'banking_products' AS source, count(*) AS rows, count(DISTINCT company_id) AS companies,
            count(*) FILTER(WHERE created_at_ok IS NOT NULL) AS valid_created_at FROM banking_products UNION ALL
            SELECT 'debt_products', count(*), count(DISTINCT company_id), count(*) FILTER(WHERE created_at_ok IS NOT NULL) FROM debt_products UNION ALL
            SELECT 'invoices', count(*), count(DISTINCT company_id), count(*) FILTER(WHERE issuance_date_ok IS NOT NULL) FROM invoices UNION ALL
            SELECT 'transactions', count(*), count(DISTINCT company_id), count(*) FILTER(WHERE date_ok IS NOT NULL) FROM transactions""")
        query('transaction_dates', """SELECT min(date_ok) AS first_booking, max(date_ok) AS last_booking,
            count(*) FILTER(WHERE date_ok IS NULL) AS invalid_booking,
            count(*) FILTER(WHERE value_date_ok IS NULL) AS invalid_value_date,
            count(*) FILTER(WHERE date_ok <> value_date_ok) AS booking_value_different,
            count(*) FILTER(WHERE date_ok >= DATE '2026-09-01') AS open_month_rows,
            count(*) FILTER(WHERE amount IS NULL OR NOT isfinite(amount)) AS unusable_amount,
            count(*)-count(DISTINCT transaction_id) AS duplicate_or_null_ids FROM transactions""")
        query('transaction_status', 'SELECT status_norm, count(*) AS rows FROM transactions GROUP BY 1 ORDER BY 1')
        query('cash_monthly_coverage', """SELECT month, count(*) AS rows, count(DISTINCT company_id) AS companies,
            count(DISTINCT product_id) AS products, count(*) FILTER(WHERE amount_eur IS NULL) AS unknown_eur,
            count(*) FILTER(WHERE amount IS NULL OR NOT isfinite(amount)) AS unusable_native_amount
            FROM cash GROUP BY 1 ORDER BY 1""")
        query('account_month_coverage_distribution', ledger_coverage_sql('cash'))
        query('account_coverage_summary', """SELECT count(*) AS cash_products,
            count(*) FILTER(WHERE t.product_id IS NOT NULL) AS with_booked_closed_activity,
            count(*) FILTER(WHERE t.n=24) AS with_activity_all_24,
            count(*) FILTER(WHERE t.last_month=DATE '2026-08-01') AS with_august_activity,
            count(*) FILTER(WHERE t.first_month > DATE '2024-09-01') AS first_activity_after_start,
            count(*) FILTER(WHERE t.last_month < DATE '2026-08-01') AS last_activity_before_end
            FROM banking_products b LEFT JOIN (SELECT product_id, count(DISTINCT month) AS n,
            min(month) AS first_month, max(month) AS last_month FROM cash GROUP BY 1) t USING(product_id)
            WHERE b.type_norm IN ('checking','saving','wallet')""")
        query('anchors', """SELECT product_source, product_type, count(*) AS rows, count(DISTINCT company_id) AS companies,
            min(date_ok) AS first_date, max(date_ok) AS last_date,
            count(*) FILTER(WHERE balance_ok IS NULL OR NOT isfinite(balance_ok)) AS invalid_balance,
            count(*) FILTER(WHERE available IS NULL OR NOT isfinite(available)) AS missing_available,
            count(*) FILTER(WHERE product_currency <> 'EUR') AS non_eur,
            count(*) FILTER(WHERE CAST(date_ok AS TIME) <> TIME '00:00:00') AS intraday_timestamps
            FROM balances GROUP BY 1,2 ORDER BY 1,2""")
        query('cash_anchor_coverage', """SELECT count(*) AS cash_products,
            count(*) FILTER(WHERE a.product_id IS NOT NULL) AS with_anchor,
            count(*) FILTER(WHERE isfinite(a.balance_ok)) AS with_finite_clean_anchor,
            count(*) FILTER(WHERE isfinite(a.balance_ok) AND b.currency_norm='EUR') AS eur_finite_anchor,
            count(*) FILTER(WHERE isfinite(a.balance_ok) AND t.n=24) AS finite_anchor_and_24_active_months,
            count(*) FILTER(WHERE a.product_id IS NOT NULL AND a.company_id<>b.company_id) AS ownership_mismatch
            FROM banking_products b LEFT JOIN balances a USING(product_id)
            LEFT JOIN (SELECT product_id, count(DISTINCT month) AS n FROM cash GROUP BY 1) t USING(product_id)
            WHERE b.type_norm IN ('checking','saving','wallet')""")
        query('anchor_multiplicity', """SELECT snapshots, count(*) AS products FROM (
            SELECT product_id, count(*) AS snapshots FROM balances GROUP BY 1) x GROUP BY 1 ORDER BY 1""")
        query('anchor_day_movements', """SELECT count(*) AS transaction_rows_on_anchor_day,
            count(DISTINCT t.product_id) AS products_on_anchor_day FROM transactions t JOIN balances a USING(product_id)
            WHERE t.is_booked AND t.product_source='banking' AND t.product_type IN ('checking','saving','wallet')
            AND CAST(t.date_ok AS DATE)=CAST(a.date_ok AS DATE)""")
        query('debt_snapshot', """SELECT count(*) AS products, count(DISTINCT company_id) AS companies,
            count(*) FILTER(WHERE outstanding IS NULL OR NOT isfinite(outstanding)) AS unknown_outstanding,
            count(*) FILTER(WHERE outstanding<0) AS negative_outstanding,
            count(*) FILTER(WHERE outstanding=0) AS reported_zero,
            count(*) FILTER(WHERE currency_norm<>'EUR') AS non_eur,
            count(*) FILTER(WHERE product_id IN (SELECT product_id FROM transactions)) AS with_any_ledger_rows,
            count(*) FILTER(WHERE product_id IN (SELECT product_id FROM debt_schedule_config)) AS scheduled
            FROM debt_products""")
        query('debt_schedule_coverage', """SELECT count(*) AS schedules, count(DISTINCT s.company_id) AS companies,
            count(*) FILTER(WHERE s.settlement_product_id IS NULL) AS missing_settlement_account,
            count(*) FILTER(WHERE b.product_id IS NOT NULL) AS settlement_account_found,
            count(*) FILTER(WHERE b.product_id IS NOT NULL AND b.company_id=s.company_id) AS settlement_same_company,
            count(*) FILTER(WHERE b.product_id IS NOT NULL AND b.currency_norm=upper(trim(s.currency))) AS settlement_same_currency,
            count(*) FILTER(WHERE s.next_payment_date_ok IS NOT NULL) AS valid_next_payment,
            count(*) FILTER(WHERE s.last_payment_date_ok IS NOT NULL) AS valid_last_payment,
            count(*) FILTER(WHERE s.interest_rate_ok IS NOT NULL) AS valid_latest_rate
            FROM debt_schedule_config s LEFT JOIN banking_products b ON b.product_id=s.settlement_product_id""")
        query('schedule_terms', """SELECT amortising_frequency, amortization_type, interest_type,
            count(*) AS schedules FROM debt_schedule_config GROUP BY 1,2,3 ORDER BY 1,2,3""")
        query('settlement_account_activity_not_payment_match', """SELECT count(*) AS schedules,
            count(*) FILTER(WHERE settlement_product_id IN (SELECT product_id FROM cash)) AS with_cash_account_activity
            FROM debt_schedule_config""")
        query('invoice_source_completeness', """SELECT count(*) AS rows, count(DISTINCT company_id) AS companies,
            count(*) FILTER(WHERE issuance_date_ok IS NULL) AS invalid_issuance,
            count(*) FILTER(WHERE maturity_date_ok IS NULL) AS missing_valid_due,
            count(*) FILTER(WHERE settlement_date_ok IS NOT NULL) AS valid_terminal_settlement_date,
            count(*) FILTER(WHERE payment_date_ok IS NOT NULL AND status_norm<>'paid') AS nonpaid_payment_date_not_actual,
            count(*) FILTER(WHERE pending_amount IS NULL) AS missing_pending,
            count(*) FILTER(WHERE pending_amount<>0 AND abs(pending_amount)<abs(amount)) AS partial_snapshot_candidates,
            count(*) FILTER(WHERE status_norm='paid' AND pending_amount<>0) AS paid_with_pending,
            count(*) FILTER(WHERE amount_eur IS NULL) AS unknown_eur,
            count(*)-count(DISTINCT operation_id) AS duplicate_or_null_ids FROM invoices""")
        query('invoice_types_and_signs', """SELECT document_type_norm,
            CASE WHEN amount>0 THEN 'positive' WHEN amount<0 THEN 'negative' ELSE 'zero_or_null' END AS amount_sign,
            count(*) AS rows FROM invoices GROUP BY 1,2 ORDER BY 1,2""")
        query('invoice_issue_month_coverage', """SELECT month, count(*) AS rows,
            count(DISTINCT company_id) AS companies, count(*) FILTER(WHERE amount_eur IS NULL) AS unknown_eur
            FROM invoices WHERE month>=DATE '2024-09-01' AND month<DATE '2026-09-01' GROUP BY 1 ORDER BY 1""")
        query('panel_flow_input_coverage', """SELECT count(*) AS company_months,
            count(*) FILTER(WHERE tiene_actividad_caja) AS active_company_months,
            count(*) FILTER(WHERE neto_caja_eur IS NOT NULL) AS known_cash_net,
            count(*) FILTER(WHERE cobros_operativos_eur IS NOT NULL) AS known_operating_receipts,
            count(*) FILTER(WHERE pagos_operativos_eur IS NOT NULL) AS known_operating_payments,
            count(*) FILTER(WHERE flujo_operativo_eur IS NOT NULL) AS complete_operating_net,
            count(*) FILTER(WHERE n_ambiguos>0) AS ambiguous_classification,
            count(*) FILTER(WHERE n_sin_eur>0) AS missing_eur FROM panel_flujos""")
        query('growth_history_coverage', """SELECT count(*) AS company_months,
            count(*) FILTER(WHERE n6=6 AND contiguous6=5) AS six_contiguous_known_receipt_months,
            count(*) FILTER(WHERE n15=15 AND contiguous15=14) AS fifteen_contiguous_known_receipt_months
            FROM (SELECT company_id, month,
            count(cobros_operativos_eur) OVER w6 AS n6,
            date_diff('month', min(month) OVER w6, month) AS contiguous6,
            count(cobros_operativos_eur) OVER w15 AS n15,
            date_diff('month', min(month) OVER w15, month) AS contiguous15
            FROM panel_flujos WINDOW w6 AS (PARTITION BY company_id ORDER BY month ROWS BETWEEN 5 PRECEDING AND CURRENT ROW),
            w15 AS (PARTITION BY company_id ORDER BY month ROWS BETWEEN 14 PRECEDING AND CURRENT ROW)) x""")
        query('debt_mart_input_coverage', """SELECT count(*) AS company_months,
            count(*) FILTER(WHERE servicio_deuda_eur IS NOT NULL) AS identifiable_paid_service,
            count(*) FILTER(WHERE n_pagos_servicio>0) AS with_identified_service_payments
            FROM panel_deuda""")
        query('fx_evidence', f"""SELECT moneda='EUR' AS is_eur, count(*) AS currency_months,
            count(*) FILTER(WHERE rate_por_eur IS NOT NULL) AS applicable_rates FROM {read('fx_rates')} GROUP BY 1 ORDER BY 1""", scoped=False)
    finally:
        con.close()
    v2_receipt = json.loads((root / 'reports/modeling/trajectory-v2/reserved-access.json').read_text())
    return {'schemas': schemas, 'inventory_all_groups_counts_only': inventory,
            'aggregate_input_coverage_development_groups_only': results,
            'query_sql': queries, 'assignment': assignment_info,
            'evaluation': {'v1_evaluation_receipt_exists': (root / 'reports/modeling/experiment-v1/evaluation_receipt.json').is_file(),
                           'v2_reserve_consumed': v2_receipt['consumed'], 'new_test_identified': False,
                           'required': 'New external/future data with documented untouched access history; no relabelled old reserve.'},
            'scope': {'run_id': RUN_ID, 'financial_values_exclude_all_v1_final_test_groups': True,
                      'raw_csv_access': 'byte hashes only', 'target_proxy_access': 'byte hashes only; no schema or values',
                      'outcomes_events_predictions_access': 'byte hashes only; no evaluation or new labels',
                      'aggregate_inventory': 'all groups row counts and schemas only; financial completeness is 200 development groups',
                      'duckdb': 'in-memory connection, SELECT-only audit queries, 512 aggregate rows maximum per query, no spill directory',
                      'balance_reconstruction_executed': False, 'certified_as_of_evidence': False},
            'limitations': ['Activity in every month does not prove a complete bank statement or a genuine zero in other months.',
                            'Snapshot midnight dates do not identify opening versus closing balance; no historical ingestion timestamps.',
                            'One balance per account cannot independently reconcile a reversed ledger; no FX valuation adjustment table.',
                            'Debt outstanding and schedule rates are extraction snapshots, not historical vintages or installment/payment ledgers.',
                            'Invoice signs are historical AP/AR proxies, not explicit verified role fields; terminal payment dates omit partial-payment histories.',
                            'No obligation-to-transaction allocation keys, dated cancellation/renegotiation events or completeness attestations exist in the input schema.',
                            'No headline company financial-health score is warranted by source presence alone.']}


def capture(root):
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'audit.json').exists():
        raise ValueError('Audit already captured; use --check, do not overwrite')
    baseline_path = output / 'protected-baseline.json'
    if not baseline_path.exists():
        baseline = {'captured_at': datetime.now(timezone.utc).isoformat(),
                    'head': git(root, 'rev-parse', 'HEAD').strip(),
                    'status_before': git(root, 'status', '--short'),
                    'index_diff_before': git(root, 'diff', '--cached', '--stat'),
                    'protected_sha256': current_hashes(root), 'run_id': RUN_ID,
                    'policy': 'Scientific/data/application baseline before any v3 source-data analysis; goal and documentation notices excluded.'}
        write_exclusive(baseline_path, baseline)
        write_exclusive(output / 'docs-before.json', {relative: (root / relative).read_text() for relative in DOCS})
        with (output / 'docs-worktree-before.patch').open('x') as handle:
            handle.write(git(root, 'diff', '--', *DOCS))
    checked = check_baseline(root)
    if not checked['ok']:
        raise ValueError(f'Protected baseline mismatch: {checked}')
    bindings = binding_checks(root)
    report = source_audit(root)
    report.update({'created_at': datetime.now(timezone.utc).isoformat(), 'duckdb_version': duckdb.__version__,
                   'bindings': bindings, 'audit_source_sha256': sha256(Path(__file__)),
                   'capture_command': '.venv/bin/python -B scripts/financial_v3_audit.py --capture',
                   'baseline_sha256': sha256(baseline_path)})
    checked_after = check_baseline(root)
    if not checked_after['ok']:
        raise ValueError('Protected assets changed during audit')
    report['protected_integrity_after_audit'] = checked_after
    write_exclusive(output / 'audit.json', report)
    return {'ok': True, 'artifact': str(OUTPUT / 'audit.json'), 'queries': len(report['query_sql']),
            'protected_assets': checked_after['protected_assets'], 'new_confirmatory_test': False}


def check_baseline(root):
    output = root / OUTPUT
    baseline = json.loads((output / 'protected-baseline.json').read_text())
    differences = compare_hashes(baseline['protected_sha256'], current_hashes(root))
    docs_before = json.loads((output / 'docs-before.json').read_text())
    docs_preserved = all((root / path).read_text().endswith(content) for path, content in docs_before.items())
    return {'ok': not differences and docs_preserved, 'protected_assets': len(baseline['protected_sha256']),
            'differences': differences, 'old_document_bytes_preserved_as_suffix': docs_preserved,
            'mode': 'hash-only read-only check; no DuckDB queries, pipeline, labels or evaluation'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--capture', action='store_true')
    args = parser.parse_args(argv)
    result = check_baseline(ROOT) if args.check else capture(ROOT)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
