"""Adaptador IO de healthscore_v3: une el motor puro (xray/scoring_v3.py) con
las capas derivadas de datos. Solo lectura de fuentes; escribe solo en la
ruta de salida del CLI.

Entradas consumidas (todas point-in-time, no se modifican):
  - data/clean/companies.parquet: universo de empresas (perimetro y grupos,
    identicos a xray/scoring_io.py).
  - data/marts/panel_flujos.parquet y panel_deuda.parquet: tiene_actividad_caja
    y servicio_deuda_eur por empresa-mes.
  - data/clean/transactions.parquet + data/interim/tx_flow_class.parquet:
    base de movimientos para recomputar C y P (el servicio de deuda D viene
    del martillo; las salidas ambiguas anulan el mes, y por eso D entra en la
    deduccion de confianza 'entradas_desconocidas').
  - reports/transfer_resolution_v2/resolution.parquet: capa de resolucion de
    transferencias (transaction_id, resolved_class, ...).
  - reports/cash_position/cash_position_monthly.parquet: colchon y
    salida_media_mensual (NUNCA posicion_acumulada como colchon).
  - reports/payment_delay/payment_delay_monthly.parquet: mora_ratio del lado
    PAGO (no el de cobro).

Recomputo de C y P aplicando la resolucion sobre los movimientos:
  - internal_transfer -> EXCLUIDO: no es C, ni P, ni D.
  - operating_in      -> suma a C (importe absoluto conocido).
  - operating_out     -> suma a P.
  - ambiguo           -> no suma a ningun total; su importe se acumula aparte
    como volumen_ambiguo_eur de la empresa-mes.
  - Movimientos ausentes de resolution.parquet conservan su clase original de
    tx_flow_class, exactamente como hoy: operating_in suma a C, operating_out
    a P, y las clases ambiguas originales (transfer/unknown/non_economic) no
    suman y dejan C o P desconocidos por direccion, como hace hoy el panel.
  Un mes con C o P desconocido se pasa al motor como None: nulo nunca es cero,
  no se imputa.

D (servicio de deuda) proviene de panel_deuda; la resolucion no lo altera.

Motivos de no-nota (resumen del corte): denominador_nulo (c6 = 0 y t6 = 0 en
la ventana, incluye las empresas sin actividad; el denominador de H seria 0).

Confianza de entradas por empresa-mes: la PEOR entre la capa C/P (baja si C o
P quedo desconocido), la de la capa de colchon y la de la capa de mora (esta
solo cuando hay mora observable; el motor ya deduce sin_mora_observable). La
confianza por transaccion de la resolucion NO se agrega al minimo: en un mes
docenas de movimientos, el minimo seria siempre 'baja' y destruiria la senal.
D (servicio de deuda) desconocido SI baja la confianza declarada aqui y en
el motor: entra en la deduccion 'entradas_desconocidas' igual que C y P.

Disciplina point-in-time: para el corte as_of solo influyen meses <= as_of.
Todas las capas se leen filtradas por month <= as_of y su available_at es el
fin del propio mes.
"""

import argparse
import hashlib
import json
import sys
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import duckdb

from xray import paths
from xray.marts.common import AMBIGUOUS_CLASSES, CASH_PRODUCTS, parquet
from xray.period import DATASET_END, FIRST_MONTH, LAST_MONTH
from xray.scoring_v3 import LIMITACIONES as MOTOR_LIMITACIONES
from xray.scoring_v3 import VERSION, score_company

MODEL_VERSION = VERSION
DEFAULT_K = 4178.45
DEFAULT_ALPHA = 3.0
DEFAULT_BETA = 0.25
CONFIDENCE_INPUTS = ('alta', 'media', 'baja', 'ninguna')
RESOLVED_CLASSES = ('internal_transfer', 'operating_in', 'operating_out', 'ambiguo')
ADVERTENCIA_CALIBRACION = (
    'alpha=3.0 y beta=0.25 son PROVISIONALES, sin calibrar; solo k esta medido '
    '(reports/calibration_k/report.md). La escala no es comparable hasta calibrar.'
)

ASSESSMENT_SCHEMA = (
    ('version', 'VARCHAR'), ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'),
    ('month', 'DATE'), ('as_of', 'DATE'), ('health_score', 'DOUBLE'),
    ('confidence', 'VARCHAR'), ('n_meses_ventana', 'INTEGER'),
    ('n_meses_con_actividad', 'INTEGER'), ('ventana_parcial', 'BOOLEAN'),
    ('c6', 'DOUBLE'), ('t6', 'DOUBLE'), ('p6', 'DOUBLE'), ('d6', 'DOUBLE'),
    ('r_hist', 'DOUBLE'), ('colchon_bruto', 'DOUBLE'),
    ('colchon_aplicable', 'DOUBLE'), ('mora_ratio', 'DOUBLE'),
    ('h_antes_de_mora', 'DOUBLE'), ('penalizacion_mora_puntos', 'DOUBLE'),
    ('volumen_ambiguo_eur', 'DOUBLE'), ('volumen_ambiguo_pct', 'DOUBLE'),
    ('k', 'DOUBLE'), ('alpha', 'DOUBLE'), ('beta', 'DOUBLE'),
    ('reasons', 'VARCHAR[]'),
)


def _cutoff(as_of):
    cutoff = date.fromisoformat(str(as_of))
    if not FIRST_MONTH <= cutoff <= DATASET_END:
        raise ValueError(f'as_of debe estar entre {FIRST_MONTH} y {DATASET_END}')
    if cutoff.day != monthrange(cutoff.year, cutoff.month)[1]:
        raise ValueError('as_of debe ser fin de mes')
    return cutoff


def _records(con, query, params=None):
    result = con.execute(query, params)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _indexed(rows, companies, source):
    indexed = {}
    for row in rows:
        company, month = row['company_id'], row['month']
        if company not in companies:
            raise ValueError(f'{source}: empresa ajena al universo: {company!r}')
        if type(month) is not date or month.day != 1:
            raise ValueError(f'{source}: month debe ser el primer dia del mes')
        if row['available_at'] != month.replace(day=monthrange(month.year, month.month)[1]):
            raise ValueError(f'{source}: available_at incoherente para {company}, {month}')
        if row['group_id'] != companies[company]:
            raise ValueError(f'{source}: grupo incoherente para {company}')
        key = (company, month)
        if key in indexed:
            raise ValueError(f'{source}: clave duplicada: {key}')
        indexed[key] = row
    return indexed


def _confianza_entradas(c_desconocido, p_desconocido, cash_row, delay_row):
    """Confianza declarada y determinista de las entradas de un empresa-mes.

    Es la PEOR de: la capa C/P (baja si C o P quedo desconocido tras la
    resolucion, alta si no), la confianza de la capa de colchon (si hay fila)
    y la de la capa de mora (solo si hay mora observable; sin cartera el
    motor ya deduce 'sin_mora_observable' y no se penaliza dos veces).
    'ninguna' solo entra por las propias capas derivadas.
    """
    levels = ['baja' if (c_desconocido or p_desconocido) else 'alta']
    if cash_row is not None:
        levels.append(cash_row['confidence'])
    if delay_row is not None and delay_row['mora_ratio'] is not None:
        levels.append(delay_row['confidence'])
    return max(levels, key=CONFIDENCE_INPUTS.index)


def assess_at_v3(con, as_of, company_id=None, *, k=DEFAULT_K, alpha=DEFAULT_ALPHA,
                 beta=DEFAULT_BETA):
    """Evalua healthscore_v3 para el corte as_of sobre las capas derivadas.

    Devuelve dict serializable con as_of, model_version='healthscore_v3',
    params, data_workspace, summary y assessments (dicts por empresa con el
    esquema cerrado de reports/score_v3/assessments.parquet).
    """
    cutoff = _cutoff(as_of)
    as_of_month = cutoff.replace(day=1)
    companies = {}
    for row in _records(con, f'SELECT company_id, group_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")} ORDER BY company_id'):
        if row['company_id'] is None:
            raise ValueError('companies: company_id nulo')
        if row['company_id'] in companies:
            raise ValueError(f'companies: clave duplicada: {row["company_id"]}')
        companies[row['company_id']] = row['group_id']
    if company_id is not None and company_id not in companies:
        raise ValueError(f'Empresa desconocida: {company_id}')
    selected = companies if company_id is None else {company_id: companies[company_id]}
    filter_company = '' if company_id is None else ' AND company_id=?'
    params = [cutoff]
    if company_id is not None:
        params.append(company_id)

    # --- paneles de entrada (actividad y deuda) ----------------------------
    panels = {}
    for name, columns in (('panel_flujos', ('tiene_actividad_caja', 'primer_mes_caja',
                                              'volumen_caja_conocido_eur')),
                          ('panel_deuda', ('servicio_deuda_eur',))):
        fields = ', '.join(('company_id', 'group_id', 'month', 'available_at', *columns))
        rows = _records(con, f'''SELECT {fields} FROM {parquet(paths.MARTS_DIR / (name + '.parquet'))}
            WHERE month<=? AND (available_at<=? OR available_at IS NULL){filter_company}''',
                        [cutoff, cutoff] + ([company_id] if company_id is not None else []))
        panels[name] = _indexed(rows, selected, name)

    # --- recomputo de C y P con la capa de resolucion -----------------------
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    labels = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
    for source in (transactions, labels):
        n, row_ids, ids = con.execute(
            f'SELECT count(*), count(DISTINCT _src_row), count(DISTINCT transaction_id) FROM {source}'
        ).fetchone()
        if n != row_ids or n != ids:
            raise ValueError('transactions/clasificacion: claves nulas o duplicadas')
    missing = con.execute(f'''
        SELECT count(*) FROM {transactions} t FULL OUTER JOIN {labels} f
        USING (_src_row, transaction_id)
        WHERE t.transaction_id IS NULL OR f.transaction_id IS NULL
    ''').fetchone()[0]
    if missing:
        raise ValueError(f'Clasificacion no alineada con transactions: {missing} filas')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    ambiguous = ','.join(f"'{value}'" for value in AMBIGUOUS_CLASSES)
    resolution_path = paths.ROOT / 'reports/transfer_resolution_v2/resolution.parquet'
    company_params = [cutoff] + ([company_id] if company_id is not None else [])
    if resolution_path.exists():
        resolution = parquet(resolution_path)
        n_res, ids_res = con.execute(
            f'SELECT count(*), count(DISTINCT transaction_id) FROM {resolution}').fetchone()
        if n_res != ids_res:
            raise ValueError('resolution: transaction_id duplicado')
        bad = con.execute(f'''SELECT count(*) FROM {resolution}
            WHERE resolved_class IS NULL OR resolved_class NOT IN
            ('internal_transfer','operating_in','operating_out','ambiguo')''').fetchone()[0]
        if bad:
            raise ValueError(f'resolution: resolved_class fuera de contrato: {bad} filas')
        con.execute(f'''
            CREATE OR REPLACE TEMP TABLE v3_tx AS
            SELECT t.company_id, t.month, t.direction, t.amount_eur,
                   CASE WHEN r.resolved_class IS NOT NULL THEN r.resolved_class
                        ELSE f.flow_class END AS clase,
                   r.resolved_class,
                   (r.resolved_class='ambiguo' OR (r.resolved_class IS NULL
                      AND f.flow_class IN ({ambiguous}))) AS es_ambiguo
            FROM {transactions} t
            JOIN {labels} f USING (_src_row, transaction_id)
            LEFT JOIN {resolution} r USING (transaction_id)
            WHERE t.is_booked AND NOT t.is_open_month
              AND t.month BETWEEN DATE '{FIRST_MONTH.isoformat()}' AND ?
              AND t.product_source='banking' AND t.product_type IN ({products})
              {filter_company}
        ''', company_params)
    else:
        # Sin capa de resolucion: todos los movimientos conservan su clase
        # original, exactamente como hoy.
        con.execute(f'''
            CREATE OR REPLACE TEMP TABLE v3_tx AS
            SELECT t.company_id, t.month, t.direction, t.amount_eur,
                   f.flow_class AS clase,
                   CAST(NULL AS VARCHAR) AS resolved_class,
                   (f.flow_class IN ({ambiguous})) AS es_ambiguo
            FROM {transactions} t
            JOIN {labels} f USING (_src_row, transaction_id)
            WHERE t.is_booked AND NOT t.is_open_month
              AND t.month BETWEEN DATE '{FIRST_MONTH.isoformat()}' AND ?
              AND t.product_source='banking' AND t.product_type IN ({products})
              {filter_company}
        ''', company_params)
    con.execute('''
        CREATE OR REPLACE TEMP TABLE v3_tx_agg AS
        SELECT company_id, month,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE clase='operating_in'),0) AS c_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE clase='operating_out'),0) AS p_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE resolved_class='ambiguo'),0) AS volumen_ambiguo_eur,
               count(*) FILTER (WHERE clase='operating_in' AND amount_eur IS NULL) AS n_c_sin_eur,
               count(*) FILTER (WHERE clase='operating_out' AND amount_eur IS NULL) AS n_p_sin_eur,
               count(*) FILTER (WHERE es_ambiguo AND direction IS DISTINCT FROM 'zero') AS n_ambiguos,
               count(*) FILTER (WHERE es_ambiguo AND (direction='in' OR direction IS NULL)) AS n_ambiguos_entrada,
               count(*) FILTER (WHERE es_ambiguo AND (direction='out' OR direction IS NULL)) AS n_ambiguos_salida
        FROM v3_tx GROUP BY 1,2
    ''')
    tx_agg = {(row['company_id'], row['month']): row for row in _records(
        con, 'SELECT * FROM v3_tx_agg')}

    # --- capas de colchon y mora -------------------------------------------
    cash_source = parquet(paths.ROOT / 'reports/cash_position/cash_position_monthly.parquet')
    delay_source = parquet(paths.ROOT / 'reports/payment_delay/payment_delay_monthly.parquet')
    cash_rows = _records(con, f'''SELECT company_id, month, colchon, salida_media_mensual, confidence
        FROM {cash_source} WHERE month<=?{filter_company}''',
                         [cutoff] + ([company_id] if company_id is not None else []))
    cash = {}
    for row in cash_rows:
        month = row['month']
        if type(month) is not date or month.day != 1:
            raise ValueError(f'cash_position: month debe ser el primer dia del mes: {month}')
        if row['company_id'] not in selected:
            continue
        key = (row['company_id'], month)
        if key in cash:
            raise ValueError(f'cash_position: clave duplicada: {key}')
        cash[key] = row
    delay = {}
    for row in _records(con, f'''SELECT company_id, CAST(month AS DATE) AS month,
            mora_ratio, confidence FROM {delay_source} WHERE month<=?{filter_company}''',
                        [cutoff] + ([company_id] if company_id is not None else [])):
        month = row['month']
        if type(month) is not date or month.day != 1:
            raise ValueError(f'payment_delay: month debe ser el primer dia del mes: {month}')
        if row['company_id'] not in selected:
            continue
        key = (row['company_id'], month)
        if key in delay:
            raise ValueError(f'payment_delay: clave duplicada: {key}')
        delay[key] = row

    # --- filas mensuales y motor -------------------------------------------
    primer_mes = {}
    for (company, month), row in panels['panel_flujos'].items():
        if row['primer_mes_caja'] is not None:
            current = primer_mes.get(company)
            if current is None or row['primer_mes_caja'] < current:
                primer_mes[company] = row['primer_mes_caja']
    monthly_rows = defaultdict(list)
    volume = {}
    for (company, month), flow in panels['panel_flujos'].items():
        start = primer_mes.get(company)
        if start is None or month < start or month > cutoff:
            continue
        debt = panels['panel_deuda'].get((company, month))
        agg = tx_agg.get((company, month))
        cash_row = cash.get((company, month))
        delay_row = delay.get((company, month))
        c_desconocido = (flow['tiene_actividad_caja'] and agg is not None
                         and (agg['n_c_sin_eur'] > 0 or agg['n_ambiguos_entrada'] > 0))
        p_desconocido = (flow['tiene_actividad_caja'] and agg is not None
                         and (agg['n_p_sin_eur'] > 0 or agg['n_ambiguos_salida'] > 0))
        # Con actividad y sin movimientos agregados no puede pasar: el panel y
        # los movimientos salen de la misma fuente; si ocurre, es incoherencia.
        if flow['tiene_actividad_caja'] and agg is None:
            raise ValueError(f'panel_flujos sin agregado de transacciones: {company}, {month}')
        c_eur = None
        if flow['tiene_actividad_caja'] and not c_desconocido:
            c_eur = float(agg['c_conocido_eur'])
        p_eur = None
        if flow['tiene_actividad_caja'] and not p_desconocido:
            p_eur = float(agg['p_conocido_eur'])
        d_eur = debt['servicio_deuda_eur'] if debt is not None else None
        colchon = cash_row['colchon'] if cash_row is not None else None
        salida_media = cash_row['salida_media_mensual'] if cash_row is not None else None
        mora = delay_row['mora_ratio'] if delay_row is not None else None
        volumen_ambiguo = float(agg['volumen_ambiguo_eur']) if agg is not None else 0.0
        volumen_caja = flow['volumen_caja_conocido_eur']
        pct = (volumen_ambiguo / volumen_caja
               if volumen_caja is not None and volumen_caja > 0 else None)
        monthly_rows[company].append({
            'month': month,
            'c_eur': c_eur,
            'p_eur': p_eur,
            'd_eur': d_eur,
            'tiene_actividad': bool(flow['tiene_actividad_caja']),
            'colchon_eur': colchon,
            'salida_media_mensual_eur': salida_media,
            'mora_ratio': mora,
            'confianza_entradas': _confianza_entradas(c_desconocido, p_desconocido, cash_row, delay_row),
        })
        volume[(company, month)] = (volumen_ambiguo, pct)

    assessments = []
    for company, group in selected.items():
        engine = score_company(company, group, monthly_rows[company], cutoff,
                               k=k, alpha=alpha, beta=beta)
        volumen_ambiguo, volumen_ambiguo_pct = volume.get((company, as_of_month), (0.0, None))
        health = engine['health_score']
        if health is None:
            if 'denominador_nulo' in engine['reasons']:
                motivo = 'denominador_nulo'
            elif 'sin_actividad_de_caja_observada' in engine['reasons']:
                motivo = 'falta_de_actividad'
            else:
                raise ValueError(f'Nota nula sin motivo declarado: {company}')
        else:
            motivo = None
        assessments.append({
            'version': engine['version'],
            'company_id': company,
            'group_id': group,
            'month': as_of_month,
            'as_of': cutoff,
            'health_score': health,
            'confidence': engine['confidence'],
            'n_meses_ventana': engine['window']['n_meses_ventana'],
            'n_meses_con_actividad': engine['window']['n_meses_con_actividad'],
            'ventana_parcial': engine['window']['ventana_parcial'],
            'c6': engine['inputs']['c6'],
            't6': engine['inputs']['t6'],
            'p6': engine['inputs']['p6'],
            'd6': engine['inputs']['d6'],
            'r_hist': engine['inputs']['r_hist'],
            'colchon_bruto': engine['inputs']['colchon_bruto'],
            'colchon_aplicable': engine['inputs']['colchon_aplicable'],
            'mora_ratio': engine['inputs']['mora_ratio'],
            'h_antes_de_mora': engine['adjustments']['h_antes_de_mora'],
            'penalizacion_mora_puntos': engine['adjustments']['penalizacion_mora_puntos'],
            'volumen_ambiguo_eur': volumen_ambiguo,
            'volumen_ambiguo_pct': volumen_ambiguo_pct,
            'k': engine['params']['k'],
            'alpha': engine['params']['alpha'],
            'beta': engine['params']['beta'],
            'reasons': engine['reasons'],
            '_motivo_sin_nota': motivo,
        })
    summary = {
        'total_companies': len(assessments),
        'n_con_nota': sum(row['health_score'] is not None for row in assessments),
        'n_sin_nota_por_falta_de_actividad': sum(row['_motivo_sin_nota'] == 'falta_de_actividad'
                                                 for row in assessments),
        'n_sin_nota_por_denominador_nulo': sum(
            row['_motivo_sin_nota'] == 'denominador_nulo'
            for row in assessments),
        'reparto_confidence': dict(sorted(Counter(row['confidence'] for row in assessments).items())),
        'motivos': dict(sorted(Counter(reason for row in assessments for reason in row['reasons']).items())),
        'empresa_mes_con_volumen_ambiguo': sum(row['volumen_ambiguo_eur'] > 0 for row in assessments),
    }
    assert (summary['n_con_nota'] + summary['n_sin_nota_por_falta_de_actividad']
            + summary['n_sin_nota_por_denominador_nulo']) == summary['total_companies']
    workspace = paths.WORKSPACE
    if workspace.is_relative_to(paths.ROOT):
        workspace = workspace.relative_to(paths.ROOT)
    return {
        'as_of': cutoff.isoformat(),
        'model_version': MODEL_VERSION,
        'params': {'k': float(k), 'alpha': float(alpha), 'beta': float(beta)},
        'params_calibrados': {'k': True, 'alpha': False, 'beta': False},
        'advertencia': ADVERTENCIA_CALIBRACION,
        'data_workspace': str(workspace),
        'summary': summary,
        'assessments': assessments,
        'limitaciones': list(MOTOR_LIMITACIONES),
    }


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _fingerprint(directories):
    fingerprint = {}
    for directory in directories:
        for file in sorted(Path(directory).rglob('*')):
            if file.is_file():
                fingerprint[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    return fingerprint


def _public_summary(result):
    summary = result['summary']
    scores = sorted(row['health_score'] for row in result['assessments']
                    if row['health_score'] is not None)
    return {
        'as_of': result['as_of'],
        'total_companies': summary['total_companies'],
        'n_con_nota': summary['n_con_nota'],
        'n_sin_nota_por_falta_de_actividad': summary['n_sin_nota_por_falta_de_actividad'],
        'n_sin_nota_por_denominador_nulo':
            summary['n_sin_nota_por_denominador_nulo'],
        'reparto_confidence': summary['reparto_confidence'],
        'health_score_p10': _percentile(scores, 0.10) if scores else None,
        'health_score_p50': _percentile(scores, 0.50) if scores else None,
        'health_score_p90': _percentile(scores, 0.90) if scores else None,
        'empresa_mes_con_volumen_ambiguo': summary['empresa_mes_con_volumen_ambiguo'],
    }


def run_score_v3(output: Path, k=DEFAULT_K, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """Genera assessments.parquet y summary.json para todos los meses cerrados.

    Devuelve el informe publicado por stdout: resumen del ultimo corte y
    diagnostico |dH| mes a mes sobre el panel completo.
    """
    directories = [paths.CLEAN_DIR, paths.INTERIM_DIR, paths.MARTS_DIR]
    before = _fingerprint(directories)
    con = duckdb.connect(':memory:')
    try:
        con.execute('SET threads=4')
        con.execute("SET memory_limit='2GB'")
        months = []
        month = FIRST_MONTH
        while month <= LAST_MONTH:
            months.append(month)
            month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
        assessments = []
        scores = {}
        por_corte = {}
        ultimo_result = None
        for month in months:
            cutoff = month.replace(day=monthrange(month.year, month.month)[1])
            result = assess_at_v3(con, cutoff, None, k=k, alpha=alpha, beta=beta)
            assessments.extend(result['assessments'])
            summary = result['summary']
            por_corte[result['as_of']] = {
                'total_companies': summary['total_companies'],
                'n_con_nota': summary['n_con_nota'],
                'n_sin_nota_por_falta_de_actividad': summary['n_sin_nota_por_falta_de_actividad'],
                'n_sin_nota_por_denominador_nulo':
                    summary['n_sin_nota_por_denominador_nulo'],
                'reparto_confidence': summary['reparto_confidence'],
            }
            for row in result['assessments']:
                scores[row['company_id'], row['month']] = row['health_score']
            ultimo_result = result
        if _fingerprint(directories) != before:
            raise ValueError('Las fuentes cambiaron durante la evaluacion; no se publica nada')
        ultimo_resumen = _public_summary(ultimo_result)
        deltas = []
        for company in {row['company_id'] for row in assessments}:
            for month, next_month in zip(months, months[1:]):
                before_score = scores.get((company, month))
                after_score = scores.get((company, next_month))
                if before_score is not None and after_score is not None:
                    deltas.append(abs(after_score - before_score))
        con_ambiguo = sorted(row['volumen_ambiguo_pct'] for row in assessments
                             if row['volumen_ambiguo_pct'] is not None
                             and row['volumen_ambiguo_pct'] > 0)
        diagnostico = {
            'delta_h_mensual': {
                'n_pares': len(deltas),
                'p50': _percentile(deltas, 0.50),
                'p75': _percentile(deltas, 0.75),
                'p90': _percentile(deltas, 0.90),
            },
            'volumen_ambiguo_pct': {
                'empresa_mes_con_ambiguo': len(con_ambiguo),
                'p50': _percentile(con_ambiguo, 0.50),
                'p90': _percentile(con_ambiguo, 0.90),
                'max': _percentile(con_ambiguo, 1.0),
            },
        }

        output = Path(output)
        output.mkdir(parents=True)
        columns = ', '.join(f'{name} {dtype}' for name, dtype in ASSESSMENT_SCHEMA)
        con.execute(f'CREATE OR REPLACE TEMP TABLE assessments ({columns})')
        placeholders = ','.join('?' for _ in ASSESSMENT_SCHEMA)
        fields = [name for name, _ in ASSESSMENT_SCHEMA]
        con.executemany(
            f'INSERT INTO assessments VALUES ({placeholders})',
            [tuple(row[field] for field in fields) for row in assessments])
        target = str(output / 'assessments.parquet').replace("'", "''")
        con.execute(f"COPY assessments TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        assessments.clear()
        con.close()

        report = {
            'model_version': MODEL_VERSION,
            'params': {'k': float(k), 'alpha': float(alpha), 'beta': float(beta)},
            'advertencia': ADVERTENCIA_CALIBRACION,
            'limitaciones': list(MOTOR_LIMITACIONES),
            'por_corte': por_corte,
            'ultimo_corte': ultimo_resumen,
            'diagnostico': diagnostico,
        }
        (output / 'summary.json').write_text(
            json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
        return report
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Puntua healthscore_v3 para todos los meses cerrados y empresas')
    parser.add_argument('--output', type=Path, default=paths.ROOT / 'reports/score_v3')
    parser.add_argument('--k', type=float, default=DEFAULT_K)
    parser.add_argument('--alpha', type=float, default=DEFAULT_ALPHA)
    parser.add_argument('--beta', type=float, default=DEFAULT_BETA)
    args = parser.parse_args(argv)
    if args.output.exists():
        print('La salida ya existe; usa --output con una ruta nueva para conservar artefactos previos',
              file=sys.stderr)
        return 1
    for name, value in (('k', args.k), ('alpha', args.alpha), ('beta', args.beta)):
        if value != value or value < 0:
            print(f'--{name}: debe ser un numero finito no negativo', file=sys.stderr)
            return 1
    try:
        report = run_score_v3(args.output, args.k, args.alpha, args.beta)
    except (ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
