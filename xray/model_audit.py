import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import duckdb

from xray.marts.common import CASH_PRODUCTS, parquet
from xray.period import DATASET_END, FIRST_MONTH, LAST_MONTH
from xray.pipeline import digest
from xray.schema import TABLES


CHECK_COMMANDS = (
    ('run_checks', [sys.executable, '-B', '-m', 'scripts.verify_data', 'checks']),
    ('verify_ingest', [sys.executable, '-B', '-m', 'scripts.verify_data', 'ingest']),
)
LEGACY_BUILD_COMMAND = ['.venv/bin/python', '-B', '-m', 'xray.cli', 'build']
TARGET_METADATA = ('company_id', 'group_id', 'month', 'origin_as_of',
                   'available_at_deficit', 'motivo_deficit_no_observable', 'target_version')
DEFICIT_BOUNDS = ('operativo_min_eur', 'operativo_max_eur',
                  'operativo_min_sin_atipicos_eur', 'operativo_max_sin_atipicos_eur')


def write_json(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.exists():
        previous = file.parent / 'history' / f'{file.stem}.{digest(file)}.json'
        previous.parent.mkdir(parents=True, exist_ok=True)
        if not previous.exists():
            previous.write_bytes(file.read_bytes())
    file.write_text(json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False) + '\n')


def current_source_hashes(root):
    files = set()
    for folder, suffixes in {'xray': {'.py'}, 'tests': {'.py'}, 'scripts': {'.py', '.sh'},
                             'tests/fixtures': {'.csv'}}.items():
        for file in (root / folder).rglob('*'):
            if file.is_file() and not file.is_symlink() and file.suffix in suffixes:
                if not any(part.startswith('.') or part == '__pycache__' for part in file.relative_to(root).parts):
                    files.add(file)
    if (root / 'requirements.txt').is_file():
        files.add(root / 'requirements.txt')
    return {file.relative_to(root).as_posix(): digest(file) for file in sorted(files)}


def workspace_fingerprint(root):
    pointer = root / 'reports/current.json'
    return digest(pointer) if pointer.is_file() else None


def record_check(root, name):
    if name not in dict(CHECK_COMMANDS):
        raise ValueError('Only portable checks and ingest verification may execute')
    command = dict(CHECK_COMMANDS)[name]
    folder = root / 'reports/modeling'
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / 'recheck.json'
    results = json.loads(record.read_text()) if record.exists() else []
    prior = [item for item in results if item['command'] == command]
    log = folder / f'{name}.{uuid4().hex}.log'
    sources_before, workspace_before = current_source_hashes(root), workspace_fingerprint(root)
    with log.open('xb') as stream:
        try:
            code = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT).returncode
        except OSError as error:
            stream.write(f'Command unavailable: {type(error).__name__}\n'.encode())
            code = 127
    suites = re.findall(r'^Ran ([0-9]+) tests? in ([0-9.]+)s$', log.read_text(), re.MULTILINE)
    sources_after, workspace_after = current_source_hashes(root), workspace_fingerprint(root)
    results.append({'command': command, 'exit_code': code, 'attempt': len(prior)+1,
                    'source_hashes': sources_before, 'source_hashes_after': sources_after,
                    'sources_unchanged': sources_before == sources_after,
                    'workspace_fingerprint_before': workspace_before, 'workspace_fingerprint': workspace_after,
                    'workspace_unchanged': workspace_before == workspace_after,
                    'log': log.relative_to(root).as_posix(), 'log_sha256': digest(log),
                    'unittest_summaries': [{'tests': int(count), 'seconds': float(seconds)} for count,seconds in suites]})
    write_json(record, results)
    print(f'{name}: exit {code}; log {log.relative_to(root)}', flush=True)
    return code


def current_check(root, results, command, sources):
    workspace = workspace_fingerprint(root)
    candidates = [(index, item) for index, item in enumerate(results)
                  if item['command'] == command
                  and (item.get('source_hashes') == sources or item.get('source_hashes_after') == sources)
                  and (item.get('workspace_fingerprint') == workspace
                       or item.get('workspace_fingerprint_before') == workspace)]
    if not candidates:
        return {'passed': False, 'reason': 'No attempt bound to current sources and workspace'}
    index, latest = candidates[-1]
    log = root / latest['log']
    passed = (latest['exit_code'] == 0 and latest.get('sources_unchanged') is True
              and latest.get('source_hashes') == latest.get('source_hashes_after') == sources
              and latest.get('workspace_unchanged') is True
              and latest.get('workspace_fingerprint_before') == latest.get('workspace_fingerprint') == workspace
              and log.is_file() and digest(log) == latest['log_sha256'])
    return {'passed': passed, 'attempt_index': index, 'exit_code': latest['exit_code'],
            'log': latest['log'], 'reason': 'Latest current-source attempt; earlier failed attempts are preserved, not gate failures'}


def parse_lfs_pointer(content):
    text = content.decode('ascii')
    match = re.fullmatch(r'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?', text)
    if not match:
        raise ValueError('HEAD raw entry is not a canonical LFS pointer')
    return match[1], int(match[2])


def raw_identity(root):
    entries = {}
    for spec in TABLES.values():
        command = ['git', 'show', f'HEAD:data/{spec.filename}']
        result = subprocess.run(command, cwd=root, capture_output=True, check=True)
        oid, size = parse_lfs_pointer(result.stdout)
        file = root / 'data' / spec.filename
        actual = digest(file)
        entries[spec.filename] = {
            'command': command, 'exit_code': result.returncode, 'head_lfs_oid_sha256': oid,
            'head_lfs_size': size, 'sha256': actual, 'size': file.stat().st_size,
            'matches_head': actual == oid and file.stat().st_size == size,
        }
    return entries


def verify_files(base, expected):
    result = {}
    for relative, expected_hash in sorted(expected.items()):
        file = base / relative
        if not file.resolve().is_relative_to(base.resolve()):
            raise ValueError('Manifest path escapes its workspace')
        actual = digest(file) if file.is_file() else None
        result[relative] = {'expected_sha256': expected_hash, 'sha256': actual,
                            'matches': actual == expected_hash}
    return result


def query_rows(con, sql):
    result = con.execute(sql)
    names = [column[0] for column in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def gross_exposure(source):
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    cash = f"coalesce(product_source='banking' AND product_type IN ({products}),false)"
    return f'''SELECT company_id,month,
        coalesce(sum(abs(amount_eur)) FILTER (WHERE {cash}),0) AS _cash_gross_eur,
        coalesce(sum(abs(amount_eur)) FILTER (WHERE NOT ({cash})),0) AS _outside_gross_eur
        FROM {parquet(source)} WHERE is_booked AND NOT is_open_month
        AND month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}' GROUP BY 1,2'''


def compare_panel(con, old, new, columns=None, gross_sources=None):
    before, after = parquet(old), parquet(new)
    schemas = [{row[0]: row[1] for row in con.execute(f'DESCRIBE SELECT * FROM {source}').fetchall()}
               for source in (before, after)]
    columns = list(columns) if columns else list(schemas[1])
    if any(schemas[0].get(column) != schemas[1].get(column) for column in columns):
        return {'schema_matches': False, 'logical_matches': False, 'economic_matches': False}
    joined = f'{before} a FULL OUTER JOIN {after} b USING (company_id,month)'
    if gross_sources:
        joined += (f' LEFT JOIN ({gross_sources[0]}) ga ON ga.company_id=a.company_id AND ga.month=a.month'
                   f' LEFT JOIN ({gross_sources[1]}) gb ON gb.company_id=b.company_id AND gb.month=b.month')
    expressions, errors = [], []
    monetary = {}
    for column in columns:
        a, b = f'a."{column}"', f'b."{column}"'
        condition = f'{a} IS DISTINCT FROM {b}'
        if schemas[1][column] in ('DOUBLE', 'FLOAT'):
            finite = f'(isfinite({a}) AND isfinite({b}))'
            error = f'abs({a}-{b})'
            condition = (f'(({a} IS NULL)<>({b} IS NULL)) OR '
                         f'({a} IS NOT NULL AND {b} IS NOT NULL AND ('
                         f'NOT isfinite({a}) OR NOT isfinite({b}) OR '
                         f'{error}>greatest(1e-8,1e-12*greatest(abs({a}),abs({b})))))')
            errors.append(f'coalesce(max({error}) FILTER (WHERE {finite}),0) AS "{column}"')
            if gross_sources and column.endswith('_eur'):
                gross_column = '_outside_gross_eur' if column == 'fuera_perimetro_conocido_eur' else '_cash_gross_eur'
                gross = f'greatest(ga.{gross_column},gb.{gross_column})'
                metrics = {
                    'max_abs_error': f'coalesce(max({error}) FILTER (WHERE {finite}),0)',
                    'max_error_over_gross': f'coalesce(max(CASE WHEN {finite} AND {gross}>0 THEN {error}/{gross} END),0)',
                    'max_gross_eur': f'max({gross})',
                    'null_mismatches': f'count(*) FILTER (WHERE ({a} IS NULL)<>({b} IS NULL))',
                    'nonfinite_rows': f'count(*) FILTER (WHERE NOT isfinite({a}) OR NOT isfinite({b}))',
                    'errors_at_least_one_cent': f'count(*) FILTER (WHERE {finite} AND {error}>=0.01)',
                    'different_cents': f'count(*) FILTER (WHERE round({a},2) IS DISTINCT FROM round({b},2))',
                    'cent_sign_changes': f'count(*) FILTER (WHERE {finite} AND sign(round({a},2))<>sign(round({b},2)))',
                    'changed_without_positive_gross': f'count(*) FILTER (WHERE {finite} AND {error}>0 AND NOT coalesce({gross}>0,false))',
                }
                stats = query_rows(con, 'SELECT ' + ','.join(f'{sql} AS "{name}"' for name,sql in metrics.items()) + f' FROM {joined}')[0]
                stats['gross_basis'] = gross_column
                stats['cent_identity_required'] = column in DEFICIT_BOUNDS
                stats['economic_matches'] = (stats['max_abs_error'] < 0.01 and stats['max_error_over_gross'] <= 1e-12
                    and not any(stats[name] for name in ('null_mismatches', 'nonfinite_rows', 'errors_at_least_one_cent',
                                                       'changed_without_positive_gross'))
                    and (not stats['cent_identity_required'] or not (stats['different_cents'] or stats['cent_sign_changes'])))
                monetary[column] = stats
        expressions.append(f'count(*) FILTER (WHERE {condition}) AS "{column}"')
    differences = query_rows(con, f'SELECT {",".join(expressions)} FROM {joined}')[0]
    max_errors = query_rows(con, f'SELECT {",".join(errors)} FROM {joined}')[0] if errors else {}
    grains = [con.execute(f'''SELECT count(*),count(DISTINCT(company_id,month)),
        count(*) FILTER (WHERE company_id IS NULL OR month IS NULL) FROM {source}''').fetchone()
        for source in (before, after)]
    same_grain = grains[0][0] == grains[1][0] and all(n == unique and nulls == 0 for n,unique,nulls in grains)
    economic = same_grain and schemas[0] == schemas[1] and all(
        monetary[column]['economic_matches'] if column in monetary else count == 0 for column,count in differences.items())
    return {'schema_matches': schemas[0] == schemas[1], 'columns_compared': columns,
            'rows_before': grains[0][0], 'rows_after': grains[1][0], 'valid_grain': same_grain,
            'differences_by_column': differences, 'max_abs_error_by_column': max_errors,
            'float_tolerance': {'absolute': 1e-8, 'relative': 1e-12},
            'logical_matches': grains[0][0] == grains[1][0] and not any(differences.values()),
            'economic_matches': economic, 'monetary_diagnostics': monetary,
            'economic_policy': {
                'scope': 'Only monetary *_eur FLOAT/DOUBLE columns with independently aggregated known EUR gross; no target values.',
                'absolute_error_must_be_below_eur': 0.01, 'relative_error_to_gross_max': 1e-12,
                'cent_identity_columns': list(DEFICIT_BOUNDS),
                'required_different_cents_for_bounds': 0, 'required_cent_sign_changes_for_bounds': 0,
                'required_null_or_nonfinite_mismatches': 0,
                'gross_definition': 'Maximum historical/current sum(abs(amount_eur)) by company-month, separate cash/noncash booked closed perimeter; unknown FX excluded, never imputed.',
                'justification': 'Explicitly approved F3 requirement change: only the four bounds feeding deficit_state require unchanged cents/signs. Other monetary aggregates allow absolute error below 0.01 EUR; all retain gross-relative, null and nonfinite guards and original strict diagnostics. Prior blocked attempts remain historical; no retry reset.',
                'nonmonetary_policy': 'Original strict comparator unchanged: exact discrete values; original 1e-8 absolute / 1e-12 relative tolerance for nonmonetary floating values.',
            }}


def compare_economic_sources(run, historical, manifest):
    economic = lambda sources: {name: value for name,value in sources.items()
        if name.startswith('xray/') and name not in ('xray/pipeline.py', 'xray/model_audit.py')}
    before, after = economic(historical['source']), economic(manifest['source'])
    base, result = run / 'source', {}
    for relative in sorted(before.keys() | after.keys()):
        file = base / relative
        if not file.resolve().is_relative_to(base.resolve()):
            raise ValueError('Manifest path escapes its workspace')
        content = file.read_bytes() if file.is_file() else None
        actual = hashlib.sha256(content).hexdigest() if content is not None else None
        item = {'historical_sha256': before.get(relative), 'current_manifest_sha256': after.get(relative),
                'current_sha256': actual, 'same_manifest_bytes': before.get(relative) == after.get(relative),
                'current_bytes_verified': actual is not None and actual == after.get(relative),
                'historical_hash_verified': False, 'normalized_bytes_match': False, 'matches': False}
        result[relative] = item
        if relative not in before or relative not in after:
            item['reason'] = 'Source missing from historical or current manifest'
        elif not item['current_bytes_verified']:
            item['reason'] = 'Current source bytes do not match current manifest'
        elif item['same_manifest_bytes']:
            item.update(historical_hash_verified=True, normalized_bytes_match=True, matches=True,
                        reason='Identical verified manifest hashes')
        else:
            command = ['git', 'show', f'{historical["git_revision"]}:{relative}']
            item['historical_blob_command'] = command
            try:
                blob = subprocess.run(command, cwd=run, capture_output=True)
            except OSError as error:
                item['reason'] = f'Historical source blob unavailable: {type(error).__name__}'
                continue
            item['historical_blob_exit_code'] = blob.returncode
            if blob.returncode:
                item['reason'] = 'Historical source blob unavailable'
                continue
            lf = blob.stdout.replace(b'\r\n', b'\n')
            variants = {'blob': blob.stdout, 'lf': lf, 'crlf': lf.replace(b'\n', b'\r\n')}
            item['historical_variant_sha256'] = {name: hashlib.sha256(value).hexdigest() for name,value in variants.items()}
            item['git_blob_sha256'] = item['historical_variant_sha256']['blob']
            item['historical_hash_verified'] = before[relative] in item['historical_variant_sha256'].values()
            item['normalized_bytes_match'] = content.replace(b'\r\n', b'\n') == lf
            item['matches'] = item['historical_hash_verified'] and item['normalized_bytes_match']
            item['reason'] = ('Historical hash not traceable to blob or LF/CRLF variants' if not item['historical_hash_verified']
                              else 'Verified LF/CRLF equivalence' if item['matches'] else 'Source content differs beyond LF/CRLF')
    return result


def historical_comparison(con, run, manifest):
    before = run / 'before'
    historical = json.loads((before / 'reports/build_manifest.json').read_text())
    outputs = {}
    for relative, new_hash in manifest['outputs'].items():
        old = before / relative
        outputs[relative] = {
            'historical_manifest_sha256': historical['outputs'].get(relative),
            'preserved_before_sha256': digest(old) if old.is_file() else None,
            'new_sha256': new_hash, 'same_manifest_bytes': historical['outputs'].get(relative) == new_hash,
        }
    gross_sources = []
    for name, workspace in (('before', before), ('after', run)):
        table = f'audit_gross_{name}'
        con.execute(f'CREATE OR REPLACE TEMP TABLE {table} AS {gross_exposure(workspace / "data/clean/transactions.parquet")}')
        gross_sources.append(f'SELECT * FROM {table}')
    logical = {}
    for relative, columns in (
        ('data/marts/panel_flujos.parquet', None),
        ('data/marts/targets_proxy.parquet', TARGET_METADATA),
    ):
        if (before / relative).exists():
            logical[relative] = compare_panel(con, before / relative, run / relative, columns,
                                              gross_sources if relative.endswith('panel_flujos.parquet') else None)
        else:
            logical[relative] = {'logical_matches': False, 'economic_matches': False, 'reason': 'historical artifact unavailable'}
    sources = compare_economic_sources(run, historical, manifest)
    return {'historical_run': historical['run_id'], 'historical_git_revision': historical['git_revision'],
            'inputs_match': historical['inputs'] == manifest['inputs'],
            'economic_sources_match': all(item['matches'] for item in sources.values()),
            'economic_sources': sources,
            'economic_sources_differing_bytes': [name for name,item in sources.items() if not item['same_manifest_bytes']],
            'economic_sources_mismatches': [name for name,item in sources.items() if not item['matches']],
            'source_comparison_policy': 'Exact current run/source hash verification; differing historical hashes must match git blob or LF/CRLF variant hashes before normalized byte equality can establish identity. Current manifest integrity remains byte-exact.',
            'parameters_match': historical['parameters'] == manifest['parameters'],
            'outputs': outputs, 'logical': logical,
            'target_comparison_policy': 'Only keys, dates, version and exclusion reasons; no target values, deficit counts or prevalences.'}


def structural_audit(con, run):
    sources = {'tx': 'data/clean/transactions.parquet', 'companies': 'data/clean/companies.parquet',
               'groups': 'data/clean/groups.parquet', 'banking': 'data/clean/banking_products.parquet',
               'debt': 'data/clean/debt_products.parquet', 'flows': 'data/interim/tx_flow_class.parquet',
               'panel': 'data/marts/panel_flujos.parquet'}
    for name, relative in sources.items():
        con.execute(f'CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM {parquet(run / relative)}')
    con.execute(f'CREATE OR REPLACE TEMP VIEW target_metadata AS SELECT {",".join(TARGET_METADATA)} '
                f'FROM {parquet(run / "data/marts/targets_proxy.parquet")}')
    cash_types = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW disposition AS
        SELECT t.*, CASE
            WHEN NOT is_booked THEN CASE WHEN status_norm='unknown' THEN 'status_missing' ELSE 'status_not_booked' END
            WHEN month IS NULL THEN 'invalid_date'
            WHEN is_open_month THEN 'open_month'
            WHEN month NOT BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}' THEN 'outside_window'
            WHEN product_source='orphan' THEN 'orphan_product'
            WHEN product_source='debt' THEN 'debt_ledger'
            WHEN product_type IS NULL OR product_type NOT IN ({cash_types}) THEN 'non_cash_product'
            ELSE 'cash_included' END AS reason
        FROM tx t
    ''')
    sql_checks = {
        'company_group_orphans': 'SELECT count(*) FROM companies c LEFT JOIN groups g USING(group_id) WHERE g.group_id IS NULL',
        'product_id_collision': 'SELECT count(*) FROM banking JOIN debt USING(product_id)',
        'product_owner_orphans': '''SELECT count(*) FROM (SELECT company_id FROM banking UNION ALL SELECT company_id FROM debt) p
            LEFT JOIN companies c USING(company_id) WHERE c.company_id IS NULL''',
        'transaction_company_orphans': 'SELECT count(*) FROM tx t LEFT JOIN companies c USING(company_id) WHERE c.company_id IS NULL',
        'transaction_owner_mismatches': '''SELECT count(*) FROM tx t
            JOIN (SELECT product_id,company_id FROM banking UNION ALL SELECT product_id,company_id FROM debt) p USING(product_id)
            WHERE t.company_id IS DISTINCT FROM p.company_id''',
        'panel_duplicate_keys': 'SELECT count(*)-count(DISTINCT(company_id,month)) FROM panel',
        'panel_group_mismatches': 'SELECT count(*) FROM panel p LEFT JOIN companies c USING(company_id) WHERE c.company_id IS NULL OR p.group_id IS DISTINCT FROM c.group_id',
        'available_at_not_month_end': 'SELECT count(*) FROM panel WHERE available_at IS DISTINCT FROM last_day(month)',
        'target_metadata_maturity': f'''SELECT count(*) FROM target_metadata WHERE
            origin_as_of IS DISTINCT FROM last_day(month) OR
            available_at_deficit IS DISTINCT FROM last_day(month+INTERVAL 3 MONTH) OR
            (motivo_deficit_no_observable IS NULL AND available_at_deficit>DATE '{DATASET_END}')''',
        'classification_future_evidence': '''SELECT count(*) FROM flows f JOIN tx t USING(_src_row,transaction_id)
            WHERE flow_source='transferida' AND (evidence_month IS NULL OR evidence_month>=t.month)''',
        'fx_identity': "SELECT count(*) FROM tx WHERE product_currency='EUR' AND amount_eur IS DISTINCT FROM amount",
        'fx_reported': '''SELECT count(*) FROM tx WHERE product_currency<>'EUR' AND amount_eur IS NOT NULL
            AND (exchange_rate IS NULL OR NOT isfinite(exchange_rate) OR exchange_rate<=0 OR exchange_rate=1
                 OR abs(amount_eur-amount/exchange_rate)>1e-8)''',
        'fx_unknown_flag': "SELECT count(*) FROM tx WHERE (amount_eur IS NULL) <> ('fx_not_convertible'=any(quality_flags))",
        'panel_missing_fx_not_zero': 'SELECT count(*) FROM panel WHERE n_sin_eur>0 AND neto_caja_eur IS NOT NULL',
        'panel_missing_activity_not_zero': 'SELECT count(*) FROM panel WHERE NOT tiene_actividad_caja AND neto_caja_conocido_eur IS NOT NULL',
        'panel_reconciliation': '''WITH direct AS (
            SELECT company_id,month,count(*) AS n,coalesce(sum(amount_eur),0) AS net
            FROM disposition WHERE reason='cash_included' GROUP BY 1,2)
            SELECT count(*) FROM panel p FULL OUTER JOIN direct d USING(company_id,month)
            WHERE p.company_id IS NULL OR p.n_tx_caja IS DISTINCT FROM coalesce(d.n,0)
                OR abs(p.neto_caja_conocido_eur-d.net)>0.01
                OR (d.n IS NOT NULL AND p.neto_caja_conocido_eur IS NULL)
                OR p.n_tx_total<>p.n_tx_caja+p.n_tx_fuera_perimetro''',
    }
    violations = {name: con.execute(sql).fetchone()[0] for name, sql in sql_checks.items()}
    panel = query_rows(con, '''SELECT count(*) AS rows,count(DISTINCT company_id) AS companies,
        count(DISTINCT group_id) AS groups,count(DISTINCT month) AS months,min(month) AS first_month,max(month) AS last_month,
        count(*) FILTER (WHERE tiene_actividad_caja) AS active_rows,
        count(*) FILTER (WHERE flujo_operativo_eur IS NOT NULL) AS complete_operating_rows,
        sum(n_tx_caja) AS cash_transactions,sum(n_tx_fuera_perimetro) AS outside_perimeter,
        sum(n_sin_eur) AS cash_transactions_without_eur,sum(n_ambiguos) AS ambiguous_cash_transactions
        FROM panel''')[0]
    violations['panel_grid_size'] = abs(panel['rows'] - con.execute('SELECT count(*) FROM companies').fetchone()[0] * 24)
    exclusions = query_rows(con, '''SELECT reason,coalesce(product_currency,'unknown') AS currency,count(*) AS rows,
        count(*) FILTER (WHERE amount_eur IS NULL) AS rows_without_eur,
        sum(CASE WHEN product_currency IS NOT NULL THEN abs(amount) END) AS absolute_native_amount,
        sum(abs(amount_eur)) AS known_absolute_eur FROM disposition GROUP BY 1,2 ORDER BY 1,2''')
    violations['disposition_row_loss'] = abs(sum(row['rows'] for row in exclusions) - con.execute('SELECT count(*) FROM tx').fetchone()[0])
    violations['disposition_cash_count'] = abs(sum(row['rows'] for row in exclusions if row['reason']=='cash_included') - panel['cash_transactions'])
    coverage = {}
    for dimension in ('month', 'group_id'):
        coverage[dimension] = query_rows(con, f'''SELECT {dimension},count(*) AS rows,
            count(*) FILTER (WHERE tiene_actividad_caja) AS active_rows,
            count(*) FILTER (WHERE NOT coalesce(es_calentamiento,true) AND tiene_actividad_caja) AS post_warmup_rows,
            sum(n_tx_caja) AS cash_transactions,sum(n_sin_eur) AS rows_without_eur FROM panel GROUP BY 1 ORDER BY 1''')
    coverage['company_active_months'] = query_rows(con, '''SELECT active_months,count(*) AS companies FROM (
        SELECT company_id,count(*) FILTER (WHERE tiene_actividad_caja) AS active_months FROM panel GROUP BY 1)
        GROUP BY 1 ORDER BY 1''')
    return {'violations': violations, 'queries': sql_checks, 'panel_flujos': panel, 'coverage': coverage,
            'transaction_dispositions_by_currency': exclusions,
            'disposition_policy': 'Disjoint first-match reasons in status/date/window/product order; unknown currency amounts are not summed.',
            'origin_exclusions': query_rows(con, '''SELECT CASE WHEN NOT tiene_actividad_caja THEN 'sin_caja_origen'
                WHEN coalesce(es_calentamiento,true) THEN 'calentamiento'
                WHEN operativo_min_eur IS NULL OR operativo_max_eur IS NULL THEN 'sin_cotas_operativas_fx'
                ELSE 'candidate_origin' END AS reason,count(*) AS rows FROM panel GROUP BY 1 ORDER BY 1'''),
            'target_exclusion_reasons_only': query_rows(con, '''SELECT coalesce(motivo_deficit_no_observable,'eligible_metadata_only') AS reason,
                count(*) AS rows FROM target_metadata GROUP BY 1 ORDER BY 1'''),
            'quality_flags': query_rows(con, 'SELECT flag,count(*) AS rows FROM (SELECT unnest(quality_flags) AS flag FROM tx) GROUP BY 1 ORDER BY 1')}


def audit_controls(root, report):
    folder = root / 'reports/modeling'
    report['raw'] = raw_identity(root)
    report['current_source_hashes'] = current_source_hashes(root)
    report['commands'] = json.loads((folder / 'recheck.json').read_text()) if (folder / 'recheck.json').exists() else []
    if len(report['raw']) != 8 or not all(item['matches_head'] for item in report['raw'].values()):
        report['blockers'].append('Raw identity does not match eight HEAD LFS OIDs and sizes.')
    report['current_checks'] = {name: current_check(root, report['commands'], command, report['current_source_hashes'])
                                for name,command in CHECK_COMMANDS}
    if not all(item['passed'] for item in report['current_checks'].values()):
        report['blockers'].append('Required current-source portable checks are missing, failed or stale.')
    builds = [item for item in report['commands'] if item['command'] == LEGACY_BUILD_COMMAND]
    build = builds[-1] if builds else None
    valid_build = (build is not None and build['exit_code'] == 0 and (root / build['log']).is_file()
                   and digest(root / build['log']) == build['log_sha256'])
    if not valid_build:
        report['blockers'].append('Successful build evidence is missing or invalid.')
    pointer_path = root / 'reports/current.json'
    if not pointer_path.exists():
        report['blockers'].append('No published run pointer.')
    elif valid_build:
        pointer = json.loads(pointer_path.read_text())
        run = (root / pointer['workspace']).resolve()
        if not run.is_relative_to((root / 'data/runs').resolve()):
            raise ValueError('Published workspace outside data/runs')
        manifest = json.loads((run / 'manifest.json').read_text())
        production = lambda sources: {name: value for name,value in sources.items()
            if name.startswith('xray/') and name != 'xray/model_audit.py'}
        manifest_checks = {
            'current_production_source_matches': production(report['current_source_hashes']) == production(manifest['source']),
            'recorded_build_matches_run': f'Publicada {run.name};' in (root / build['log']).read_text(),
            'runtime_matches': manifest['python'] == platform.python_version() and manifest['dependencies']['duckdb'] == duckdb.__version__,
            'pointer_matches': pointer['manifest_sha256'] == digest(run / 'manifest.json'),
            'portable_manifest_matches': digest(root / 'reports/build_manifest.json') == digest(run / 'manifest.json'),
            'inputs_match_raw': manifest['inputs'] == {name: item['sha256'] for name,item in report['raw'].items()},
            'verification_log_matches': manifest['verification']['log_sha256'] == digest(run / 'verification.log'),
            'verification_passed': manifest['verification']['exit_code'] == 0,
        }
        report['run'] = run.relative_to(root).as_posix()
        report['current_checks']['build'] = {'passed': all(manifest_checks.values()),
            'log': build['log'], 'source_identity': 'Immutable build manifest source; current production modules match; no new build attempted'}
        report['current_source_changes_since_build'] = {name: {'build_sha256': manifest['source'].get(name), 'current_sha256': value}
            for name,value in report['current_source_hashes'].items() if manifest['source'].get(name) != value}
        report['manifest'] = {'checks': manifest_checks, 'source': verify_files(run / 'source', manifest['source']),
                              'outputs': verify_files(run, manifest['outputs']), 'parameters': manifest['parameters'],
                              'git_revision': manifest['git_revision'], 'python': manifest['python'], 'dependencies': manifest['dependencies']}
        if not all(manifest_checks.values()) or any(not item['matches'] for category in ('source','outputs') for item in report['manifest'][category].values()):
            report['blockers'].append('Manifest integrity check failed.')
        report['auditor'] = {'path': 'xray/model_audit.py', 'sha256': digest(Path(__file__)),
                             'build_source_sha256': manifest['source'].get('xray/model_audit.py')}
        try:
            with duckdb.connect() as con:
                con.execute('SET threads=4')
                report['structural'] = structural_audit(con, run)
                report['historical_comparison'] = historical_comparison(con, run, manifest)
        except (duckdb.Error, OSError, ValueError) as error:
            report['blockers'].append(f'Audit control could not complete: {type(error).__name__}: {error}')
        if 'structural' in report and any(report['structural']['violations'].values()):
            report['blockers'].append('One or more structural checks have violations; inspect named counts before modeling.')
        if 'historical_comparison' in report:
            comparison = report['historical_comparison']
            if (not comparison['inputs_match'] or not comparison['parameters_match'] or not comparison['economic_sources_match']
                    or any(not item['economic_matches'] for item in comparison['logical'].values())):
                report['blockers'].append('Historical input/parameter/source/economic comparison differs; inspect strict and monetary diagnostics before modeling.')


def audit(root, verify=False):
    folder = root / 'reports/modeling'
    report = {
        'audit_command': [sys.executable, '-B', '-m', 'xray.model_audit'] + (['--verify'] if verify else []),
        'scope': 'G-DATA for synthetic retrospective cash-flow route only; not model acceptance or certified historical availability.',
        'target': 'target_deficit_3m',
        'excluded_features': ['invoices', 'debt snapshots', 'balance snapshots', 'targets_proxy'],
        'availability': {'source': 'xray/marts/common.py:company_months; xray/marts/flujos.py:build_panel_flujos',
                         'rule': 'panel.available_at = last_day(month); transactions.date_ok sets month; booked final snapshot',
                         'limitation': 'No observation/import/category revision logs. Month-end is an approved retrospective assumption, not verified known_on.'},
        'fx_policy': 'Approved reported division into EUR, native EUR identity; missing/ambiguous FX remains unknown. EUR is analytical reporting currency, not company base currency.',
        'audit_policy': 'Only structural counts, coverage, exclusions and invariant checks. No holdout outcomes or global prevalences queried. Existing build evidence and all check logs retained without exposing target statistics.',
        'check_selection_policy': 'Preserve every attempt; use latest attempt bound to current sources/workspace per portable check, require success, unchanged sources/workspace and intact log. Build remains bound to immutable published source, with production-code identity checked against current code.',
        'evidence_policy': 'Reports and logs are evidence only; verification executes fixed portable commands, never commands read from reports.',
        'blockers': [],
    }
    try:
        audit_controls(root, report)
    except (OSError, ValueError, KeyError, TypeError, duckdb.Error, subprocess.SubprocessError) as error:
        report['blockers'].append(f'Audit inputs or manifest could not be verified: {type(error).__name__}: {error}')
    report['gate_status'] = 'blocked' if report['blockers'] else 'pass_for_approved_retrospective_scope'
    report['audit_exit_code'] = 1 if report['blockers'] else 0
    target = folder / 'data_gate.json'
    if target.exists():
        report['prior_data_gate'] = {'sha256': digest(target),
            'preserved_path': (folder / 'history' / f'data_gate.{digest(target)}.json').relative_to(root).as_posix()}
    write_json(target, report)
    print(json.dumps({'gate_status': report['gate_status'], 'blockers': report['blockers'],
                      'report': 'reports/modeling/data_gate.json'}))
    return 1 if report['blockers'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    exit_code = 0
    if args.verify:
        for name, _ in CHECK_COMMANDS:
            code = record_check(root, name)
            exit_code = code or exit_code
    audit_code = audit(root, verify=args.verify)
    return audit_code or exit_code


if __name__ == '__main__':
    raise SystemExit(main())
