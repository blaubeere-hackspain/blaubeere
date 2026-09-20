import argparse
from collections import Counter, defaultdict
import copy
from datetime import date, datetime
import json
import math
from pathlib import Path
import statistics

import duckdb

from scripts.financial_v3_cash import month_shift
from scripts.financial_v3_contract import canonical_bytes, month_end, require
from scripts.financial_v3_score import dimension_points, clip
from scripts.financial_v3_dataset_contract import (
    ROOT, DIRECTORY, VERSION, SOURCES, ASSIGNMENT, policy, check as contract_check,
    development_window, validate_company, write_new, sha256,
)


MONTHS = [month_shift('2024-09-01', i) for i in range(24)]
RETRIEVED = policy()['availability']['retrieved_at']
BUCKETS = {'operating': ('cobros_operativos', 'pagos_operativos'),
           'financing': ('financiacion_entradas', 'financiacion_salidas'),
           'investment': ('inversion_entradas', 'inversion_salidas'),
           'transfer': ('transferencias_entradas', 'transferencias_salidas'),
           'adjustment': ('ajustes_entradas', 'ajustes_salidas'),
           'unknown': ('sin_clasificar_entradas', 'sin_clasificar_salidas')}
AGES = ['0_30', '31_60', '61_90', '91_180', '181_360', '360_mas']
INVOICE_FIELDS = ['tiene_erp'] + [f'{side}_{field}' for side in ('ap', 'ar') for field in (
    'abierto_eur', 'abierto_conocido_eur', 'vencido_eur', 'n_abiertas', 'abierto_cobertura_eur_pct',
    'n_estado_incierto', 'n_vencimiento_desconocido', *[f'aging_{age}_eur' for age in AGES])]
SERVICE_FIELDS = ['tiene_actividad_caja', 'principal_conocido_eur', 'intereses_conocido_eur',
    'servicio_deuda_conocido_eur', 'servicio_deuda_eur', 'n_pagos_servicio', 'n_servicio_sin_eur',
    'n_posibles_pagos_no_identificados', 'financiacion_no_desglosada_conocido_eur']
POINT_FIELDS = {'month', 'as_of', 'known_on', 'flow', 'service', 'invoices', 'documents', 'growth', 'cash', 'score', 'change'}


def known(values):
    return all(v is not None and isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def aging_exposure(buckets):
    require(len(buckets) == 6, 'Six source aging bins required')
    if not known(buckets):
        return None
    require(min(buckets) >= 0, 'Negative overdue exposure')
    rest = math.fsum(v * w for v, w in zip(buckets[1:], [1.5, 2, 3, 4, 4]))
    return [rest + buckets[0], rest + 1.25 * buckets[0]]


def flow_point(row):
    volume, net = row.get('volumen_caja_conocido_eur'), row.get('neto_caja_conocido_eur')
    total = [(volume + net) / 2, (volume - net) / 2] if known([volume, net]) else [None, None]
    return {'observed': bool(row.get('tiene_actividad_caja')), 'rows': row.get('n_tx_caja', 0),
            'known_in_out': total, 'net_known': net, 'net_observed': row.get('neto_caja_eur'),
            'composition': {k: [row.get(a + '_conocido_eur'), row.get(b + '_conocido_eur')] for k, (a, b) in BUCKETS.items()},
            'operating_receipts': row.get('cobros_operativos_eur'), 'operating_payments': row.get('pagos_operativos_eur'),
            'operating_bounds': [row.get('operativo_min_eur'), row.get('operativo_max_eur')],
            'bounds_without_outliers': [row.get('operativo_min_sin_atipicos_eur'), row.get('operativo_max_sin_atipicos_eur')],
            'coverage': {'eur': row.get('cobertura_eur_pct'), 'classification': row.get('cobertura_clasificacion_pct'),
                         'missing_eur_rows': row.get('n_sin_eur', 0), 'ambiguous_rows': row.get('n_ambiguos', 0),
                         'active_accounts': row.get('n_cuentas_activas', 0)},
            'reason': None if row.get('tiene_actividad_caja') else 'no_observed_cash_activity_not_zero'}


def service_point(row):
    return {'principal_paid_known': row.get('principal_conocido_eur'),
            'interest_paid_known': row.get('intereses_conocido_eur'),
            'service_paid_known': row.get('servicio_deuda_conocido_eur'),
            'service_paid_observed': row.get('servicio_deuda_eur'),
            'unseparated_financing': row.get('financiacion_no_desglosada_conocido_eur'),
            'rows': row.get('n_pagos_servicio', 0), 'missing_eur_rows': row.get('n_servicio_sin_eur', 0),
            'possible_unidentified_rows': row.get('n_posibles_pagos_no_identificados', 0), 'outstanding': None}


def invoice_point(row):
    result = {}
    for side in ('ap', 'ar'):
        active = row.get('tiene_erp', False)
        def get(field):
            return row.get(f'{side}_{field}') if active else None
        bins = [get(f'aging_{age}_eur') for age in AGES]
        result[side] = {'pending': get('abierto_eur'), 'pending_known': get('abierto_conocido_eur'),
                        'overdue': get('vencido_eur'), 'aging': bins, 'eur_coverage': get('abierto_cobertura_eur_pct'),
                        'uncertain_state_rows': get('n_estado_incierto'), 'unknown_due_rows': get('n_vencimiento_desconocido')}
    ap = result['ap']
    result['mora_exposure_range'] = aging_exposure(ap['aging']) if ap['overdue'] is not None else None
    result['reason'] = 'retrospective_sign_role_and_final_state_proxy' if row.get('tiene_erp') else 'no_invoice_observation_yet'
    return result


def growth(rows, month, mode, field):
    offsets = {'mom': ([0], [-1]), 'three_vs_three': ([-2, -1, 0], [-5, -4, -3]), 'yoy': ([0], [-12])}
    current, prior = [[month_shift(month, offset) for offset in window] for window in offsets[mode]]
    values = [[rows.get(m, {}).get(field) for m in window] for window in (current, prior)]
    history = [rows.get(m, {}) for m in prior + current]
    result = {'value': None, 'current': current, 'prior': prior, 'numerator': None, 'denominator': None,
              'reason': 'insufficient_observed_history', 'min_eur_coverage': None, 'min_classification_coverage': None,
              'active_perimeter_changed': (len({tuple(r['_perimeter']) for r in history}) > 1
                  if all('_perimeter' in r for r in history) else None)}
    for key, source in [('min_eur_coverage', 'cobertura_eur_pct'), ('min_classification_coverage', 'cobertura_clasificacion_pct')]:
        coverage = [r.get(source) for r in history]
        result[key] = min(coverage) if known(coverage) else None
    if all(known(window) for window in values):
        now, before = [math.fsum(window) for window in values]
        result.update(numerator=now-before, denominator=before, reason='zero_reference' if before <= 0 else None)
        if before > 0:
            result['value'] = (now-before) / before
    return result


def invoice_documents(rows, month):
    ids = [r.get('operation_id') for r in rows]
    require(None not in ids and len(ids) == len(set(ids)), 'Duplicate/null invoice identity')
    end = month_end(month).isoformat()
    selected = [r for r in rows if r.get('document_type_norm') == 'invoice' and r.get('issuance_date_ok')
                and str(r['issuance_date_ok'])[:10] <= end and r.get('amount')]
    if not selected:
        return {'ap_due_face': None, 'ap_due_known': None, 'ap_issued_known': None, 'ar_issued_known': None,
                'unknown_due_rows': None, 'due_missing_eur_rows': None, 'issued_rows': 0}
    due = [r for r in selected if r['amount'] < 0 and r.get('maturity_date_ok') and month <= str(r['maturity_date_ok'])[:10] <= end]
    unknown = sum(r['amount'] < 0 and (not r.get('maturity_date_ok') or str(r['maturity_date_ok'])[:10] < str(r['issuance_date_ok'])[:10]) for r in selected)
    missing = sum(r.get('amount_eur') is None for r in due)
    subtotal = math.fsum(abs(r['amount_eur']) for r in due if r.get('amount_eur') is not None)
    issued = [r for r in selected if month <= str(r['issuance_date_ok'])[:10] <= end]
    return {'ap_due_face': subtotal if not unknown and not missing else None, 'ap_due_known': subtotal,
            'ap_issued_known': math.fsum(abs(r['amount_eur']) for r in issued if r['amount'] < 0 and r.get('amount_eur') is not None),
            'ar_issued_known': math.fsum(abs(r['amount_eur']) for r in issued if r['amount'] > 0 and r.get('amount_eur') is not None),
            'unknown_due_rows': unknown, 'due_missing_eur_rows': missing, 'issued_rows': len(issued)}


def reverse_account(anchor, rows, month):
    result = {'product_id': anchor['product_id'], 'currency': anchor['currency'], 'opening_scenarios': None,
              'closing_scenarios': None, 'closing_range': None, 'gap_months': None, 'available_balance': None,
              'verified': False, 'reason': 'no_anchor_or_pre_activity'}
    if anchor.get('currency') != anchor.get('anchor_currency') or anchor.get('company_id') != anchor.get('anchor_company'):
        result['reason'] = 'anchor_perimeter_mismatch'
        return result
    if not known([anchor.get('balance'), anchor.get('before_anchor'), anchor.get('through_anchor')]) or not rows or month < min(rows):
        return result
    if anchor.get('bad_rows'):
        result['reason'] = 'unusable_native_movements'
        return result
    cumul = math.fsum(r['net'] for m, r in rows.items() if m <= month)
    prior = math.fsum(r['net'] for m, r in rows.items() if m < month)
    cuts = [anchor['before_anchor'], anchor['through_anchor']]
    result['closing_scenarios'] = [anchor['balance'] + cumul - c for c in cuts]
    result['opening_scenarios'] = [anchor['balance'] + prior - c for c in cuts]
    result['closing_range'] = sorted(result['closing_scenarios'])
    bridge_end = min(anchor['anchor_date'][:7] + '-01', MONTHS[-1])
    result['gap_months'] = sum(m not in rows for m in MONTHS if month <= m <= bridge_end)
    result['reason'] = 'single_anchor_unknown_cut_observed_ledger_only'
    return result


def scoped_score(flows, documents, month, exposure):
    window = [month_shift(month, i) for i in range(-5, 1)]
    receipts = [flows.get(m, {}).get('cobros_operativos_eur') for m in window]
    due = [documents.get(m, {}).get('ap_due_face') for m in window]
    S = statistics.mean(receipts) if known(receipts) else None
    points = dict.fromkeys(('generation', 'growth', 'stability'))
    if S is not None and S > 0:
        recent, prior = statistics.mean(receipts[-3:]), statistics.mean(receipts[:3])
        if prior > 0:
            points['growth'] = dimension_points('growth', {'recent_receipts': recent, 'prior_receipts': prior})
        if known(due[-3:]):
            F = statistics.mean(r-o for r, o in zip(receipts[-3:], due[-3:]))
            points['generation'] = dimension_points('generation', {'F': F, 'S': S})
        if known(due):
            sigma = statistics.pstdev(r-o for r, o in zip(receipts, due))
            points['stability'] = dimension_points('stability', {'F_std': sigma, 'S': S})
    penalty = [0.0, 40.0]
    if S is not None and S > 0 and exposure is not None:
        penalty = [40 * E / (S + E) for E in exposure]
    weights = {'generation': 0.4, 'growth': 0.3, 'stability': 0.3}
    low = math.fsum(weights[k] * (v if v is not None else 0) for k, v in points.items())
    high = math.fsum(weights[k] * (v if v is not None else 100) for k, v in points.items())
    return {'headline': None, 'full_company_interval': [0, 100], 'components': points,
            'receipt_reference': S, 'mora_penalty_range': penalty, 'operating_scope_score': None,
            'operating_scope_interval': [clip(low - penalty[1]), clip(high - penalty[0])],
            'missing_components': [k for k, v in points.items() if v is None]}


def feature_row(company, group, split, month, flows, documents, service):
    window = development_window(split, month)
    if window is None:
        return None
    months = [month_shift(month, i) for i in (-2, -1, 0)]
    history = [flows.get(m, {}) for m in months]
    reasons = []
    if not all(r.get('tiene_actividad_caja') for r in history):
        reasons.append('three_contiguous_cash_months_missing')
    if any(r.get('n_sin_eur') != 0 for r in history):
        reasons.append('eur_coverage_incomplete')
    receipts = [r.get('cobros_operativos_conocido_eur') for r in history]
    if not known(receipts) or math.fsum(v or 0 for v in receipts) <= 0:
        reasons.append('positive_observed_receipt_reference_missing')
    fields = {'receipts_known': 'cobros_operativos_conocido_eur', 'payments_known': 'pagos_operativos_conocido_eur',
              'operating_low': 'operativo_min_eur', 'operating_high': 'operativo_max_eur',
              'classification_coverage': 'cobertura_clasificacion_pct', 'eur_coverage': 'cobertura_eur_pct'}
    values = {}
    for name, field in fields.items():
        nums = [r.get(field) for r in history]
        values[name] = nums[-1]
        values[name + '_mean3'] = statistics.mean(nums) if known(nums) else None
    for key in ('ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows'):
        values[key] = documents.get(month, {}).get(key)
    values['service_paid_known'] = service.get(month, {}).get('servicio_deuda_conocido_eur')
    return {'company_id': company, 'group_id': group, 'month': month, 'split': split, 'window': window,
            'eligible': not reasons, 'reasons': reasons, 'strict_fullscore_eligible': False,
            'availability_assumption': policy()['availability']['assumption_id'], 'values': values}


def historical_view(profile, cutoff, strict=False):
    require(not strict, 'No verified historical known_on: dataset-v1 is retrospective only')
    result = copy.deepcopy(profile)
    result['financial_v3']['points'] = [p for p in result['financial_v3']['points'] if p['as_of'] <= cutoff]
    if cutoff < '2026-08-31':
        result['financial_v3']['meta'].pop('snapshot_context', None)
    result['assessment_date'] = cutoff
    return result


def invoice_sql(source):
    return f"""WITH months AS (SELECT CAST(m AS DATE) AS month, last_day(m) AS cutoff
        FROM generate_series(DATE '2024-09-01', DATE '2026-08-01', INTERVAL 1 MONTH) g(m)),
        inv AS (SELECT operation_id, company_id, amount, amount_eur,
            CAST(issuance_date_ok AS DATE) issued, CAST(maturity_date_ok AS DATE) due
            FROM {source} WHERE document_type_norm='invoice' AND issuance_date_ok IS NOT NULL AND amount<>0),
        agg AS (SELECT company_id, month,
            coalesce(sum(abs(amount_eur)) FILTER(WHERE amount<0 AND due BETWEEN month AND cutoff),0) ap_due_known,
            coalesce(sum(abs(amount_eur)) FILTER(WHERE amount<0 AND issued BETWEEN month AND cutoff),0) ap_issued_known,
            coalesce(sum(abs(amount_eur)) FILTER(WHERE amount>0 AND issued BETWEEN month AND cutoff),0) ar_issued_known,
            count(*) FILTER(WHERE amount<0 AND (due IS NULL OR due<issued)) unknown_due_rows,
            count(*) FILTER(WHERE amount<0 AND due BETWEEN month AND cutoff AND amount_eur IS NULL) due_missing_eur_rows,
            count(*) FILTER(WHERE issued BETWEEN month AND cutoff) issued_rows
            FROM inv JOIN months ON issued<=cutoff GROUP BY company_id,month)
        SELECT *, CASE WHEN unknown_due_rows=0 AND due_missing_eur_rows=0 THEN ap_due_known END ap_due_face
        FROM agg ORDER BY company_id, month"""


class Inputs:
    def __init__(self):
        self.con = duckdb.connect(':memory:', config={'threads': 2, 'memory_limit': '768MB', 'temp_directory': ''})
        self.queries = []
        self.paths = {Path(p).stem: ROOT / p for p in SOURCES}

    def source(self, name):
        require(name in self.paths, 'Not an allowed input')
        return "read_parquet('" + self.paths[name].as_posix().replace("'", "''") + "')"

    def select(self, sql):
        statements = self.con.extract_statements(sql)
        require(len(statements) == 1 and statements[0].type == duckdb.StatementType.SELECT, 'Read-only SELECT required')
        self.queries.append(sql)
        cursor = self.con.execute(sql)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, [v.isoformat() if isinstance(v, (date, datetime)) else v for v in row])) for row in cursor.fetchall()]

    def table(self, name, fields):
        return self.select(f"SELECT {', '.join(fields)} FROM {self.source(name)} ORDER BY company_id,month")


def grouped(rows):
    result = defaultdict(dict)
    for row in rows:
        key, month = row['company_id'], row['month']
        require(month not in result[key], 'Duplicate company month')
        result[key][month] = row
    return result


def load(inputs):
    s, q = inputs.source, inputs.select
    companies = q(f"SELECT company_id,group_id,currency_norm FROM {s('companies')} ORDER BY company_id")
    flows = grouped(inputs.table('panel_flujos', ['*']))
    service = grouped(inputs.table('panel_deuda', ['company_id', 'month'] + SERVICE_FIELDS))
    invoices = grouped(inputs.table('panel_cobro', ['company_id', 'month'] + INVOICE_FIELDS))
    for name, identity in [('invoices', 'operation_id'), ('transactions', 'transaction_id'), ('balances', 'product_id')]:
        counts = q(f'SELECT count(*) n,count(DISTINCT {identity}) unique_n FROM {s(name)}')[0]
        require(counts['n'] == counts['unique_n'], f'Duplicate or null {identity}')
    documents = grouped(q(invoice_sql(s('invoices'))))
    cash_filter = "is_booked AND product_source='banking' AND product_type IN ('checking','saving','wallet')"
    movement_rows = q(f"""SELECT company_id,product_id,CAST(date_trunc('month',date_ok) AS DATE) AS month,
        sum(amount) net,count(*) n FROM {s('transactions')} WHERE {cash_filter}
        GROUP BY 1,2,3 ORDER BY 1,2,3""")
    movements = defaultdict(dict)
    for row in movement_rows:
        movements[row['product_id']][row['month']] = row
        flow = flows.get(row['company_id'], {}).get(row['month'])
        if flow is not None:
            flow.setdefault('_perimeter', []).append(row['product_id'])
    for history in flows.values():
        for row in history.values():
            row['_perimeter'] = sorted(row.get('_perimeter', []))
    anchors = q(f"""WITH cash AS (SELECT * FROM {s('transactions')} WHERE {cash_filter}),
        bridge AS (SELECT b.product_id,
            coalesce(sum(t.amount) FILTER(WHERE CAST(t.date_ok AS DATE)<CAST(b.date_ok AS DATE)),0) before_anchor,
            coalesce(sum(t.amount) FILTER(WHERE CAST(t.date_ok AS DATE)<=CAST(b.date_ok AS DATE)),0) through_anchor,
            count(*) FILTER(WHERE t.transaction_id IS NOT NULL AND (t.date_ok IS NULL OR t.amount IS NULL
                OR NOT isfinite(t.amount) OR t.product_currency<>b.product_currency OR t.company_id<>b.company_id)) bad_rows
            FROM {s('balances')} b LEFT JOIN cash t USING(product_id) GROUP BY b.product_id)
        SELECT p.product_id,p.company_id,p.currency_norm currency,b.product_currency anchor_currency,
            b.company_id anchor_company,CAST(b.date_ok AS DATE) anchor_date,b.balance_ok balance,
            bridge.before_anchor,bridge.through_anchor,bridge.bad_rows,b._src_row anchor_source_row
        FROM {s('banking_products')} p LEFT JOIN {s('balances')} b USING(product_id)
        LEFT JOIN bridge USING(product_id) WHERE p.type_norm IN ('checking','saving','wallet') ORDER BY p.company_id,p.product_id""")
    debt = q(f"SELECT company_id,product_id,currency_norm currency,outstanding FROM {s('debt_products')} ORDER BY company_id,product_id")
    return companies, flows, service, invoices, documents, movements, anchors, debt


def schema_notes():
    return {'version': VERSION, 'kind': 'financial_health_v3', 'company_base': {
        'id': 'string', 'name': 'string (source has no company name; id is displayed)', 'group': 'string', 'currency': 'string',
        'assessment_date': 'ISO date', 'model_version': VERSION, 'history_mode': 'retrospective', 'data_mode': 'synthetic',
        'opening_cash_cents': 'null', 'buffer_cents': 'null', 'health': 'null', 'predictive': 'null',
        'history': '[]', 'flows': '[]', 'coverage': '[]', 'drivers': '[]'},
        'financial_v3': {'points': 'Point[24]', 'meta': 'shared provenance, assumptions, anchors, optional extraction snapshot context'},
        'Point': {'month': 'YYYY-MM-01', 'as_of': 'calendar month-end', 'known_on': 'null',
            'flow': 'observed totals/subtotals/composition in EUR, bounds and coverage',
            'service': 'observed principal/interest service EUR; outstanding null',
            'invoices': 'AP/AR provisional pending/overdue/aging EUR; mora exposure interval',
            'documents': 'all-status issued and due-face proxy EUR|null',
            'growth': 'observed/subtotal -> mom/three_vs_three/yoy: value|null, exact windows, numerator, denominator and coverage',
            'cash': 'accounts -> native opening/closing cutoff scenarios, range, gap count; verified=false',
            'score': 'headline=null, full_company_interval=[0,100], observed components, operating scope interval; no midpoint'},
        'array_units': {'composition': '[known inflow, known outflow]', 'aging': ['1-30', '31-60', '61-90', '91-180', '181-360', '361+'],
                        'cash_scenarios': ['anchor opening day', 'anchor closing day'], 'intervals': '[lower,upper]'},
        'number': 'finite source binary64; null means unavailable, never NaN', 'source_keys': 'meta sources reference receipt input hashes',
        'historical_view': 'strict=True rejects; retrospective filters points and removes later snapshot context before August'}


def schema():
    number, text, null = {'type': ['number', 'null']}, {'type': 'string'}, {'type': 'null'}
    def obj(fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}
    def array(item, size=None, nullable=False):
        result = {'type': ['array', 'null'] if nullable else 'array', 'items': item}
        if size is not None:
            result.update(minItems=size, maxItems=size)
        return result
    pair = array(number, 2)
    interval = array(number, 2, True)
    growth_type = obj({'value': number, 'current': array(text), 'prior': array(text), 'numerator': number,
        'denominator': number, 'reason': {'type': ['string', 'null']}, 'min_eur_coverage': number,
        'min_classification_coverage': number, 'active_perimeter_changed': {'type': ['boolean', 'null']}})
    growth_modes = obj({k: growth_type for k in ('mom', 'three_vs_three', 'yoy')})
    flow_type = obj({**{k: number for k in ('rows', 'net_known', 'net_observed', 'operating_receipts', 'operating_payments')},
        'observed': {'type': 'boolean'}, 'known_in_out': pair, 'composition': obj({k: pair for k in BUCKETS}),
        'operating_bounds': pair, 'bounds_without_outliers': pair,
        'coverage': obj({k: number for k in ('eur', 'classification', 'missing_eur_rows', 'ambiguous_rows', 'active_accounts')}),
        'reason': {'type': ['string', 'null']}})
    side = obj({**{k: number for k in ('pending', 'pending_known', 'overdue', 'eur_coverage', 'uncertain_state_rows', 'unknown_due_rows')},
                'aging': array(number, 6)})
    score = obj({'headline': null, 'full_company_interval': {'const': [0, 100]},
        'components': obj({k: number for k in ('generation', 'growth', 'stability')}), 'receipt_reference': number,
        'mora_penalty_range': pair, 'operating_scope_score': null, 'operating_scope_interval': pair,
        'missing_components': array(text)})
    account = obj({'product_id': text, 'currency': text, 'opening_scenarios': interval, 'closing_scenarios': interval,
        'closing_range': interval, 'gap_months': number, 'available_balance': null, 'verified': {'const': False}, 'reason': text})
    point = obj({'month': text, 'as_of': text, 'known_on': null, 'flow': flow_type,
        'service': obj({k: number for k in service_point({})}),
        'invoices': obj({'ap': side, 'ar': side, 'mora_exposure_range': interval, 'reason': text}),
        'documents': obj({k: number for k in invoice_documents([], MONTHS[0])}),
        'growth': obj({'observed': growth_modes, 'known_subtotal': growth_modes}), 'cash': obj({'accounts': array(account)}), 'score': score,
        'change': obj({'component_deltas': obj({k: number for k in ('generation', 'growth', 'stability')}),
                       'receipt_reference_changed': {'type': 'boolean'}, 'coverage_changed': {'type': 'boolean'}, 'reason': text})})
    result = obj({**{k: text for k in ('id', 'name', 'group', 'currency', 'assessment_date')},
        'kind': {'const': 'financial_health_v3'}, 'model_version': {'const': VERSION},
        'history_mode': {'const': 'retrospective'}, 'data_mode': {'const': 'synthetic'},
        **{k: null for k in ('opening_cash_cents', 'buffer_cents', 'health', 'predictive')},
        **{k: array({}, 0) for k in ('history', 'flows', 'coverage', 'drivers')},
        'financial_v3': obj({'points': array(point, 24), 'meta': {'type': 'object'}})})
    result.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': VERSION,
                   '$comment': json.dumps(schema_notes(), separators=(',', ':'))})
    return result


def validate_wire(value, spec=None):
    spec = schema() if spec is None else spec
    if 'const' in spec:
        require(value == spec['const'], 'Wire constant mismatch')
        return
    types = spec.get('type')
    if types is not None:
        types = types if isinstance(types, list) else [types]
        valid = {'null': value is None, 'number': type(value) in (int, float) and math.isfinite(value),
                 'string': type(value) is str, 'boolean': type(value) is bool,
                 'array': type(value) is list, 'object': type(value) is dict}
        require(any(valid[t] for t in types), f'Wire type mismatch: {types}')
    if type(value) is dict and 'properties' in spec:
        require(set(spec['required']) <= set(value), 'Missing required wire field')
        require(spec.get('additionalProperties', True) or set(value) <= set(spec['properties']), 'Unknown wire field')
        for key, val in value.items():
            validate_wire(val, spec['properties'][key])
    if type(value) is list and 'items' in spec:
        require(spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', len(value)), 'Wire array length')
        for val in value:
            validate_wire(val, spec['items'])


def build_company(company, flows, service, invoices, documents, movements, anchors, debt, split, receipt_hash):
    points = []
    for month in MONTHS:
        inv = invoice_point(invoices.get(month, {}))
        cash = [reverse_account(a, movements[a['product_id']], month) for a in anchors]
        point = {'month': month, 'as_of': month_end(month).isoformat(), 'known_on': None,
            'flow': flow_point(flows.get(month, {})), 'service': service_point(service.get(month, {})),
            'invoices': inv, 'documents': {k: v for k, v in documents.get(month, invoice_documents([], month)).items() if k not in ('company_id', 'month')},
            'growth': {scope: {mode: growth(flows, month, mode, field) for mode in ('mom', 'three_vs_three', 'yoy')}
                       for scope, field in [('observed', 'cobros_operativos_eur'), ('known_subtotal', 'cobros_operativos_conocido_eur')]},
            'cash': {'accounts': cash}, 'score': scoped_score(flows, documents, month, inv['mora_exposure_range'])}
        old = points[-1] if points else None
        deltas = {k: v-old['score']['components'][k] if old and known([v, old['score']['components'][k]]) else None
                  for k, v in point['score']['components'].items()}
        point['change'] = {'component_deltas': deltas,
            'receipt_reference_changed': bool(old and old['score']['receipt_reference'] != point['score']['receipt_reference']),
            'coverage_changed': bool(old and (old['flow']['coverage'] != point['flow']['coverage'] or
                flows.get(old['month'], {}).get('_perimeter') != flows.get(month, {}).get('_perimeter'))),
            'reason': 'first_month' if old is None else 'missing_components_or_changed_reference_not_causal'}
        points.append(point)
    meta = {'version': VERSION, 'policy_sha256': sha256(DIRECTORY / 'policy.json'), 'receipt_sha256': receipt_hash,
        'source_keys': ['panel_flujos', 'panel_deuda', 'panel_cobro', 'invoices', 'transactions', 'balances', 'banking_products', 'debt_products', 'companies'],
        'provenance_grain': 'company_id/month in marts; product_id/anchor_source_row for balances; issued/due invoice aggregate query in queries.json',
        'retrieved_at': RETRIEVED, 'reversal_available_at': RETRIEVED, 'known_on': None,
        'strict_historical_eligible': False, 'product_only_group': split == 'final_test',
        'score_policy': policy()['scoring'], 'availability_assumption': policy()['availability']['assumption_id'],
        'limits': ['Observed source totals are not whole-company totals.', 'AP/AR roles and final-state stocks are retrospective proxies; not verified allocations.',
                   'Due-face includes terminal cancellations: documented issuance, not enforceable complete costs.',
                   'Cash has two cutoff scenarios, unknown completeness/restrictions, no stock FX and no independent reconciliation.',
                   'Receipt growth is not sales; classification, currency and account perimeter may change.',
                   'No model, labels, predictive validation or six-question success claim.'],
        'anchors': anchors,
        'snapshot_context': {'available_at': RETRIEVED, 'extracted_at': RETRIEVED, 'measured_at': None,
            'retrospective': True, 'historical_feature_eligible': False, 'reason': 'debt_products lacks measured date; extraction context only, signed raw outstanding, not historical or verified debt',
            'debt_products': debt}}
    profile = {'id': company['company_id'], 'name': company['company_id'], 'group': company['group_id'],
        'currency': company['currency_norm'], 'kind': 'financial_health_v3', 'assessment_date': '2026-08-31',
        'model_version': VERSION, 'history_mode': 'retrospective', 'data_mode': 'synthetic',
        'opening_cash_cents': None, 'buffer_cents': None, 'health': None, 'predictive': None,
        'history': [], 'flows': [], 'coverage': [], 'drivers': [], 'financial_v3': {'points': points, 'meta': meta}}
    validate_wire(profile)
    return validate_company(profile)


def export():
    contract_check()
    require(not (DIRECTORY / 'manifest.json').exists(), 'Export already sealed')
    require(not (DIRECTORY / 'index.json').exists(), 'Partial export exists; stop, do not overwrite')
    inputs = Inputs()
    try:
        companies, flows, service, invoices, documents, movements, anchors, debt = load(inputs)
    finally:
        inputs.con.close()
    require(len(companies) == 1286 and len({c['company_id'] for c in companies}) == 1286, 'Company universe changed')
    assignment = json.loads((ROOT / ASSIGNMENT).read_text())
    by_anchor, by_debt = defaultdict(list), defaultdict(list)
    for row in anchors:
        by_anchor[row['company_id']].append(row)
    for row in debt:
        by_debt[row['company_id']].append(row)
    (DIRECTORY / 'profiles').mkdir(exist_ok=True)
    counts, missing, index, features = Counter(), Counter(), [], []
    receipt_hash = sha256(DIRECTORY / 'receipt.json')
    sample = None
    for company in companies:
        cid, gid = company['company_id'], company['group_id']
        require('/' not in cid and '\\' not in cid and cid not in ('.', '..'), 'Unsafe company id')
        profile = build_company(company, flows[cid], service[cid], invoices[cid], documents[cid], movements,
                                by_anchor[cid], by_debt[cid], assignment[gid], receipt_hash)
        path = f'profiles/{cid}.json'
        write_new(DIRECTORY / path, profile)
        index.append({**{k: profile[k] for k in ('id', 'name', 'group', 'currency', 'data_mode', 'kind')},
                      'path': path, 'bytes': (DIRECTORY / path).stat().st_size, 'sha256': sha256(DIRECTORY / path)})
        counts['companies'] += 1
        for point in profile['financial_v3']['points']:
            counts['company_months'] += 1
            counts['cash_active_months'] += point['flow']['observed']
            counts['cash_net_observed_months'] += point['flow']['net_observed'] is not None
            counts['operating_receipts_observed_months'] += point['flow']['operating_receipts'] is not None
            counts['invoice_ap_pending_months'] += point['invoices']['ap']['pending'] is not None
            counts['mora_exposure_months'] += point['invoices']['mora_exposure_range'] is not None
            counts['service_paid_observed_months'] += point['service']['service_paid_observed'] is not None
            counts['due_face_proxy_months'] += point['documents'].get('ap_due_face') is not None
            counts['cash_reversal_company_months'] += any(a['closing_range'] is not None for a in point['cash']['accounts'])
            for key, value in point['score']['components'].items():
                counts['component_' + key + '_months'] += value is not None
            for key, value in point['growth']['observed'].items():
                counts['growth_' + key + '_months'] += value['value'] is not None
            for key in point['score']['missing_components']:
                missing[key] += 1
            f = feature_row(cid, gid, assignment[gid], point['month'], flows[cid], documents[cid], service[cid])
            if f is not None:
                features.append(f)
                counts['feature_rows'] += 1
                counts['feature_eligible_rows'] += f['eligible']
                counts['feature_' + f['window'] + '_rows'] += 1
                counts['feature_' + f['window'] + '_eligible'] += f['eligible']
        if sample is None and assignment[gid] == 'train':
            sample = {'selection': 'First lexicographic train company, fixed August point; no labels or performance selection',
                      'company_id': cid, 'point': profile['financial_v3']['points'][-1]}
    with (DIRECTORY / 'features.jsonl').open('xb') as handle:
        for row in features:
            handle.write(canonical_bytes(row) + b'\n')
    coverage = {'counts': dict(counts), 'missing_components': dict(missing), 'strict_headline_eligible': 0,
                'strict_historical_feature_eligible': 0, 'no_data_vs_choice': {
                    'no_data': ['unknown unrestricted cash', 'unknown historical debt', 'no invoice observation or absent EUR/due fields'],
                    'policy_choice': ['fixed weights and mora multipliers unvalidated', 'all-status AP due-face proxy',
                        'retrospective availability assumption', '50 groups product-only; development dates purged']},
                'limits': policy()['modeling'], 'features_contain_targets': False}
    write_new(DIRECTORY / 'index.json', {'version': VERSION, 'companies': index})
    write_new(DIRECTORY / 'coverage.json', coverage)
    write_new(DIRECTORY / 'sample.json', sample)
    write_new(DIRECTORY / 'schema.json', schema())
    write_new(DIRECTORY / 'queries.json', {'purpose': 'product inputs only; no outcomes, cohort fields, labels, target queries or evaluation', 'queries': inputs.queries})
    contract_check()
    files = {p.relative_to(DIRECTORY).as_posix(): sha256(p) for p in sorted(DIRECTORY.rglob('*')) if p.is_file() and p.name != 'verification.json'}
    source_code = {p.relative_to(ROOT).as_posix(): sha256(p) for pattern in ('scripts/financial_v3_dataset*.py', 'tests/test_financial_v3_dataset*.py') for p in sorted(ROOT.glob(pattern))}
    write_new(DIRECTORY / 'manifest.json', {'version': VERSION, 'inputs_sha256': json.loads((DIRECTORY / 'receipt.json').read_text())['inputs_sha256'],
        'source_sha256': source_code, 'artifacts_sha256': files, 'counts': dict(counts), 'training': False, 'outcomes': False,
        'profile_bytes': sum(r['bytes'] for r in index), 'max_profile_bytes': max(r['bytes'] for r in index),
        'artifact_count_including_manifest': len(files) + 1})
    return {'ok': True, **counts, 'profile_bytes': sum(r['bytes'] for r in index)}


def check_export():
    contract_check()
    manifest = json.loads((DIRECTORY / 'manifest.json').read_text())
    for p, h in manifest['source_sha256'].items():
        require(sha256(ROOT / p) == h, f'Source changed: {p}')
    for p, h in manifest['artifacts_sha256'].items():
        require(sha256(DIRECTORY / p) == h, f'Artifact changed: {p}')
    index = json.loads((DIRECTORY / 'index.json').read_text())['companies']
    require(len(index) == 1286 and len({c['id'] for c in index}) == 1286, 'Bad company index')
    for item in index:
        profile = validate_company(json.loads((DIRECTORY / item['path']).read_text()))
        require([p['month'] for p in profile['financial_v3']['points']] == MONTHS, 'Month grid mismatch')
        require(all(set(p) == POINT_FIELDS for p in profile['financial_v3']['points']), 'Point schema mismatch')
        require(item['sha256'] == sha256(DIRECTORY / item['path']), 'Index hash mismatch')
    assignment = json.loads((ROOT / ASSIGNMENT).read_text())
    n = 0
    with (DIRECTORY / 'features.jsonl').open() as handle:
        for line in handle:
            row = json.loads(line)
            require(row['split'] == assignment[row['group_id']], 'Feature group mismatch')
            require(development_window(row['split'], row['month']) == row['window'], 'Inadmissible feature origin/group')
            require(row['split'] != 'final_test', 'Holdout feature leakage')
            require(not any(k in row['values'] for k in policy()['availability']['never_features']), 'Forbidden feature')
            n += 1
    require(n == manifest['counts']['feature_rows'], 'Feature count mismatch')
    return {'ok': True, 'companies': len(index), 'months': len(index)*24, 'feature_rows': n, 'input_queries': 0,
            'outcome_queries': 0, 'writes': 0, 'training': False, 'artifacts_checked': len(manifest['artifacts_sha256'])}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--export', action='store_true')
    group.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(export() if args.export else check_export(), sort_keys=True))


if __name__ == '__main__':
    main()
