"""Adaptador IO de healthscore_v4: une las capas derivadas v4 con el motor
puro (xray/scoring_v4.py). Solo lectura de fuentes; escribe solo en la ruta
de salida del CLI. Referencia de estilo y contrato: xray/scoring_io_v3.py,
que CONVIVE intacto (v3 no se modifica ni se regenera).

Entradas consumidas (todas point-in-time, no se modifican):
  - data/clean/companies.parquet: universo de empresas (perimetro y grupos),
    identico a xray/scoring_io_v3.py.
  - data/marts/panel_flujos.parquet y panel_deuda.parquet:
    tiene_actividad_caja, primer_mes_caja y servicio_deuda_eur (D) por
    empresa-mes; mismos martillos que v3.
  - data/clean/transactions.parquet + data/interim/tx_flow_class.parquet:
    base de movimientos para recomputar C y P con la capa de resolucion
    (reports/transfer_resolution_v2/resolution.parquet), exactamente como v3.
  - reports/debt_obligation/debt_obligation_monthly.parquet (capa A v4):
    obligacion_vencida_eur (STOCK del corte), deficit_servicio_eur (FLUJO
    mensual) y multiplicador_deuda.
  - reports/cash_backfill/cash_backfill_monthly.parquet (capa B v4):
    saldo_reversa_eur (NIVEL de caja reconstruido en reversa; sustituye al
    `colchon` de cash_position, que era invariante al ancla).
  - reports/payment_delay_v2/payment_delay_v2_monthly.parquet (capa C v4):
    mora_indice (indice de morosidad robusto; sustituye al mora_ratio de v1).

AVISO DE DTYPE: cash_backfill guarda `month` con dtype distinto de las otras
dos capas (las capas A y C lo guardan como datetime64[ms]). El join
NORMALIZA siempre con CAST(month AS DATE). Tras el join se VERIFICA que los
tres conjuntos de (company_id, month) de meses cerrados son identicos
(30.864 pares en el workspace canonico); un join que devuelva menos es un
bug y aborta con el diagnostico de diferencias.

Motivos de no-nota (guardas del motor, en orden):
  falta_de_actividad              <- 'sin_actividad_de_caja_observada'
  pagos_operativos_no_demostrados <- guarda P6 <= 0 sin obligacion vencida
  sin_flujos_observados_en_la_ventana <- c6 = 0 y t6_efectivo = 0
La nueva guarda (P6 = 0 CON obligacion vencida > 0) NO retira la nota: da
una nota BAJA (evidencia de que la empresa debe y no paga).

Confianza de entradas por empresa-mes: la PEOR entre la capa C/P (baja si C
o P quedo desconocido), la de la capa de caja reconstruida (cash_backfill)
y la de la capa de mora robusta (solo cuando hay mora observable). D
desconocido NO degrada la confianza (fix P6: reason informativo sin restar
nivel). La confianza de la capa de obligacion (debt_obligation) tampoco
entra en el minimo: su cobertura de cartera declararia 'ninguna' para la
mitad del panel y destruiria la senal, el mismo patron del defecto-2 que el
fix P6 corrigio para D.

Disciplina point-in-time: para el corte as_of solo influyen meses <= as_of.
"""

import argparse
import hashlib
import json
import math
import sys
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import duckdb

from xray import paths
from xray.marts.common import AMBIGUOUS_CLASSES, CASH_PRODUCTS, parquet
from xray.period import DATASET_END, FIRST_MONTH, LAST_MONTH
from xray.score_exclusions_v4 import (DECLARACION_PLANTILLA as DECLARACION_EXCLUSION,
                                      MES_CORTE_REFERENCIA as MES_EXCLUSION,
                                      MOTIVO_EXCLUSION, UMBRAL_NOTA_BAJA,
                                      UMBRAL_PROPORCION, build_exclusion_report,
                                      excluded_ids, write_exclusions_json)
from xray.scoring_v4 import LIMITACIONES as MOTOR_LIMITACIONES
from xray.scoring_v4 import VERSION, score_company

MODEL_VERSION = VERSION
DEFAULT_K = 4178.45
DEFAULT_ALPHA = 3.0
DEFAULT_BETA = 0.25
CONFIDENCE_INPUTS = ('alta', 'media', 'baja', 'ninguna')
# NOTA (calibracion v4, 2026-09-19): k = 4178.45 es el valor ORIGINALMENTE
# MEDIDO sobre la rejilla v3 (reports/calibration_k/report.md) y fue
# REVALIDADO SIN CAMBIOS sobre la rejilla v4 por la calibracion de
# sensibilidad y estabilidad de xray/calibrate_params_v4.py
# (reports/calibration_v4/report.md): los tres parametros (k, alpha = 3.0,
# beta = 0.25) quedan revalidados en sus valores heredados, con estabilidad
# mes a mes |dH| p75 = 6.99 < 10 sobre 10.022 pares consecutivos. La escala
# sigue condicionada al sesgo de observabilidad de la poblacion con nota
# (ver advertencia de provisionalidad del informe de calibracion).
ADVERTENCIA_CALIBRACION = (
    'k=4178.45 es el valor originalmente medido sobre la rejilla v3 '
    '(reports/calibration_k/report.md) y fue REVALIDADO sin cambios sobre '
    'la rejilla v4 por calibracion de sensibilidad y estabilidad '
    '(reports/calibration_v4/report.md, 2026-09-19): k, alpha=3.0 y '
    'beta=0.25 quedan revalidados; estabilidad mes a mes |dH| p75=6.99 < 10 '
    'sobre 10.022 pares. Los valores siguen siendo PROVISIONALES en cuanto '
    'a la escala: la poblacion de calibracion esta sesgada por '
    'observabilidad; revalidar cuando se resuelva la clasificacion de '
    'cobros (informe de calibracion).'
)
REFERENCIA_V3_SIN_FIX = {
    'n_con_nota': 959,
    'n_con_nota_99_99': 112,
    'fuente': 'reports/score_v3/summary.json (version publicada, con defectos conocidos)',
}
REFERENCIA_V3_CON_FIX = {
    'n_con_nota': 516,
    'n_con_nota_99_99': 0,
    'fuente': 'reports/fix_p6_preservado/FIX_P6.md (estado con fix, revertido por decision de producto)',
}
UMBRAL_NOTA_ALTA = 99.99
N_PAIRS_ESPERADOS = 30864

ASSESSMENT_SCHEMA = (
    ('version', 'VARCHAR'), ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'),
    ('month', 'DATE'), ('as_of', 'DATE'), ('health_score', 'DOUBLE'),
    ('confidence', 'VARCHAR'), ('n_meses_ventana', 'INTEGER'),
    ('n_meses_con_actividad', 'INTEGER'), ('ventana_parcial', 'BOOLEAN'),
    ('c6', 'DOUBLE'), ('p6', 'DOUBLE'), ('d6', 'DOUBLE'),
    ('deficit_servicio_6', 'DOUBLE'), ('obligacion_vencida_m', 'DOUBLE'),
    ('t6_efectivo', 'DOUBLE'), ('r_hist', 'DOUBLE'),
    ('colchon_v4', 'DOUBLE'), ('colchon_aplicable', 'DOUBLE'),
    ('mora_indice', 'DOUBLE'), ('multiplicador_deuda', 'DOUBLE'),
    ('h_antes_de_ajustes', 'DOUBLE'), ('penalizacion_mora_puntos', 'DOUBLE'),
    ('penalizacion_multiplicador_puntos', 'DOUBLE'),
    ('volumen_ambiguo_eur', 'DOUBLE'), ('volumen_ambiguo_pct', 'DOUBLE'),
    ('k', 'DOUBLE'), ('alpha', 'DOUBLE'), ('beta', 'DOUBLE'),
    ('reasons', 'VARCHAR[]'), ('excluida', 'BOOLEAN'),
)

MOTIVOS_SIN_NOTA = {
    'sin_actividad_de_caja_observada': 'falta_de_actividad',
    'pagos_operativos_no_demostrados': 'pagos_operativos_no_demostrados',
    'sin_flujos_observados_en_la_ventana': 'sin_flujos_observados_en_la_ventana',
}


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
    resolucion, alta si no), la confianza de la capa de caja reconstruida
    (cash_backfill) y la de la capa de mora robusta (solo si hay mora
    observable; sin cartera el motor ya deduce 'sin_mora_observable' y no
    se penaliza dos veces). 'ninguna' solo entra por las propias capas
    derivadas. D desconocido NO entra: el fix P6 lo saca del cubo.
    """
    levels = ['baja' if (c_desconocido or p_desconocido) else 'alta']
    if cash_row is not None:
        levels.append(cash_row['confidence'])
    if delay_row is not None and delay_row['mora_indice'] is not None:
        levels.append(delay_row['confidence'])
    return max(levels, key=CONFIDENCE_INPUTS.index)


def _layer_path(name):
    return paths.ROOT / f'reports/{name}/{name}_monthly.parquet'


def _load_layer(con, source, columns, selected, cutoff, company_id=None):
    """Lee una capa v4 normalizando el dtype de `month` a DATE."""
    fields = ', '.join('company_id, CAST(month AS DATE) AS month, ' + column
                       for column in columns)
    rows = _records(con, f'''SELECT {fields} FROM {parquet(source)}
        WHERE CAST(month AS DATE)<=?{'' if company_id is None else ' AND company_id=?'}''',
                    [cutoff] + ([company_id] if company_id is not None else []))
    indexed = {}
    for row in rows:
        month = row['month']
        if type(month) is not date or month.day != 1:
            raise ValueError(f'{source}: month debe ser el primer dia del mes: {month}')
        if row['company_id'] not in selected:
            continue
        key = (row['company_id'], month)
        if key in indexed:
            raise ValueError(f'{source}: clave duplicada: {key}')
        indexed[key] = row
    return indexed


def _check_join_sets(layers):
    """Los tres conjuntos de (company_id, month) deben ser identicos.

    Un join que pierda filas es un bug: se aborta con el diagnostico de
    diferencias. Devuelve el numero de pares comunes.
    """
    (reference_name, reference), *rest = sorted(
        ((name, set(indexed)) for name, indexed in layers.items()))
    for name, keys in rest:
        if keys != reference:
            only_reference = sorted(reference - keys)[:5]
            only_other = sorted(keys - reference)[:5]
            raise ValueError(
                'join de capas v4 pierde filas: '
                f'{reference_name} tiene {len(reference)} pares, {name} '
                f'tiene {len(keys)}; solo en {reference_name}: '
                f'{only_reference}; solo en {name}: {only_other}')
    return len(reference)


def _tx_tables(con, cutoff, company_id, selected):
    """Recomputo de C y P con la capa de resolucion (idem scoring_io_v3)."""
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
            CREATE OR REPLACE TEMP TABLE v4_tx AS
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
              {'' if company_id is None else ' AND company_id=?'}
        ''', company_params)
    else:
        con.execute(f'''
            CREATE OR REPLACE TEMP TABLE v4_tx AS
            SELECT t.company_id, t.month, t.direction, t.amount_eur,
                   f.flow_class AS clase,
                   CAST(NULL AS VARCHAR) AS resolved_class,
                   (f.flow_class IN ({ambiguous})) AS es_ambiguo
            FROM {transactions} t
            JOIN {labels} f USING (_src_row, transaction_id)
            WHERE t.is_booked AND NOT t.is_open_month
              AND t.month BETWEEN DATE '{FIRST_MONTH.isoformat()}' AND ?
              AND t.product_source='banking' AND t.product_type IN ({products})
              {'' if company_id is None else ' AND company_id=?'}
        ''', company_params)
    con.execute('''
        CREATE OR REPLACE TEMP TABLE v4_tx_agg AS
        SELECT company_id, month,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE clase='operating_in'),0) AS c_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE clase='operating_out'),0) AS p_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE resolved_class='ambiguo'),0) AS volumen_ambiguo_eur,
               count(*) FILTER (WHERE clase='operating_in' AND amount_eur IS NULL) AS n_c_sin_eur,
               count(*) FILTER (WHERE clase='operating_out' AND amount_eur IS NULL) AS n_p_sin_eur,
               count(*) FILTER (WHERE es_ambiguo AND (direction='in' OR direction IS NULL)) AS n_ambiguos_entrada,
               count(*) FILTER (WHERE es_ambiguo AND (direction='out' OR direction IS NULL)) AS n_ambiguos_salida
        FROM v4_tx GROUP BY 1,2
    ''')
    return {(row['company_id'], row['month']): row for row in _records(
        con, 'SELECT * FROM v4_tx_agg')}


def assess_at_v4(con, as_of, company_id=None, *, k=DEFAULT_K, alpha=DEFAULT_ALPHA,
                 beta=DEFAULT_BETA):
    """Evalua healthscore_v4 para el corte as_of sobre las capas derivadas.

    Devuelve dict serializable con as_of, model_version='healthscore_v4',
    params, data_workspace, summary y assessments (dicts por empresa con el
    esquema cerrado de reports/score_v4/assessments.parquet). La clave
    interna '_monthly_rows' (filas construidas para el motor) permite
    reevaluar contrafactuales del analisis de triple conteo; no se
    serializa ni se publica.
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

    # --- paneles de entrada (actividad y deuda) ----------------------------
    panels = {}
    filter_company = '' if company_id is None else ' AND company_id=?'
    company_extra = [company_id] if company_id is not None else []
    for name, columns in (('panel_flujos', ('tiene_actividad_caja', 'primer_mes_caja',
                                              'volumen_caja_conocido_eur')),
                          ('panel_deuda', ('servicio_deuda_eur',))):
        fields = ', '.join(('company_id', 'group_id', 'month', 'available_at', *columns))
        rows = _records(con, f'''SELECT {fields} FROM {parquet(paths.MARTS_DIR / (name + '.parquet'))}
            WHERE month<=? AND (available_at<=? OR available_at IS NULL){filter_company}''',
                        [cutoff, cutoff] + company_extra)
        panels[name] = _indexed(rows, selected, name)

    tx_agg = _tx_tables(con, cutoff, company_id, selected)

    # --- capas v4: obligacion (A), caja reversa (B), mora robusta (C) -------
    layer_paths = {name: _layer_path(name)
                   for name in ('debt_obligation', 'cash_backfill', 'payment_delay_v2')}
    for name, path in layer_paths.items():
        if not path.exists():
            raise ValueError(f'Capa v4 ausente: {path}')
    layers = {
        'debt_obligation': _load_layer(
            con, layer_paths['debt_obligation'],
            ('obligacion_vencida_eur', 'deficit_servicio_eur',
             'multiplicador_deuda', 'confidence'),
            selected, cutoff, company_id),
        'cash_backfill': _load_layer(
            con, layer_paths['cash_backfill'],
            ('saldo_reversa_eur', 'salida_media_mensual_reversa', 'confidence'),
            selected, cutoff, company_id),
        'payment_delay_v2': _load_layer(
            con, layer_paths['payment_delay_v2'], ('mora_indice', 'confidence'),
            selected, cutoff, company_id),
    }
    n_pairs = _check_join_sets(layers)
    debt = layers['debt_obligation']
    cash = layers['cash_backfill']
    delay = layers['payment_delay_v2']

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
        debt_row = debt.get((company, month))
        cash_row = cash.get((company, month))
        delay_row = delay.get((company, month))
        panel_deuda = panels['panel_deuda'].get((company, month))
        agg = tx_agg.get((company, month))
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
        d_eur = panel_deuda['servicio_deuda_eur'] if panel_deuda is not None else None
        saldo_reversa = cash_row['saldo_reversa_eur'] if cash_row is not None else None
        salida_media = (cash_row['salida_media_mensual_reversa']
                        if cash_row is not None else None)
        mora = delay_row['mora_indice'] if delay_row is not None else None
        deficit = debt_row['deficit_servicio_eur'] if debt_row is not None else None
        obligacion = debt_row['obligacion_vencida_eur'] if debt_row is not None else None
        multiplicador = debt_row['multiplicador_deuda'] if debt_row is not None else None
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
            'saldo_reversa_eur': saldo_reversa,
            'deficit_servicio_eur': deficit,
            'obligacion_vencida_eur': obligacion,
            'multiplicador_deuda': multiplicador,
            'mora_indice': mora,
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
            motivo = None
            for reason, mapped in MOTIVOS_SIN_NOTA.items():
                if reason in engine['reasons']:
                    motivo = mapped
                    break
            if motivo is None:
                raise ValueError(f'Nota nula sin motivo declarado: {company}')
        else:
            motivo = None
        inputs = engine['inputs']
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
            'c6': inputs['c6'],
            'p6': inputs['p6'],
            'd6': inputs['d6'],
            'deficit_servicio_6': inputs['deficit_servicio_6'],
            'obligacion_vencida_m': inputs['obligacion_vencida_m'],
            't6_efectivo': inputs['t6_efectivo'],
            'r_hist': inputs['r_hist'],
            'colchon_v4': inputs['colchon_v4'],
            'colchon_aplicable': inputs['colchon_aplicable'],
            'mora_indice': inputs['mora_indice'],
            'multiplicador_deuda': inputs['multiplicador_deuda'],
            'h_antes_de_ajustes': engine['adjustments']['h_antes_de_ajustes'],
            'penalizacion_mora_puntos': engine['adjustments']['penalizacion_mora_puntos'],
            'penalizacion_multiplicador_puntos': engine['adjustments']['penalizacion_multiplicador_puntos'],
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
        'n_sin_nota_por_falta_de_actividad': sum(
            row['_motivo_sin_nota'] == 'falta_de_actividad' for row in assessments),
        'n_sin_nota_por_pagos_no_demostrados': sum(
            row['_motivo_sin_nota'] == 'pagos_operativos_no_demostrados'
            for row in assessments),
        'n_sin_nota_por_sin_flujos': sum(
            row['_motivo_sin_nota'] == 'sin_flujos_observados_en_la_ventana'
            for row in assessments),
        'reparto_confidence': dict(sorted(Counter(row['confidence'] for row in assessments).items())),
        'motivos': dict(sorted(Counter(reason for row in assessments for reason in row['reasons']).items())),
        'empresa_mes_con_volumen_ambiguo': sum(row['volumen_ambiguo_eur'] > 0 for row in assessments),
        'pares_capas_join': n_pairs,
    }
    assert (summary['n_con_nota'] + summary['n_sin_nota_por_falta_de_actividad']
            + summary['n_sin_nota_por_pagos_no_demostrados']
            + summary['n_sin_nota_por_sin_flujos']) == summary['total_companies']
    workspace = paths.WORKSPACE
    if workspace.is_relative_to(paths.ROOT):
        workspace = workspace.relative_to(paths.ROOT)
    return {
        'as_of': cutoff.isoformat(),
        'model_version': MODEL_VERSION,
        'params': {'k': float(k), 'alpha': float(alpha), 'beta': float(beta)},
        'params_calibrados': {'k': False, 'alpha': False, 'beta': False},
        'advertencia': ADVERTENCIA_CALIBRACION,
        'data_workspace': str(workspace),
        'summary': summary,
        'assessments': assessments,
        '_monthly_rows': monthly_rows,
        'limitaciones': list(MOTOR_LIMITACIONES),
    }


# --------------------------------------------------------------------- #
# Comparativa v3 y analisis de triple conteo
# --------------------------------------------------------------------- #

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


def _distribution(scores):
    return {
        'n': len(scores),
        'p10': _percentile(scores, 0.10),
        'p50': _percentile(scores, 0.50),
        'p90': _percentile(scores, 0.90),
        'min': scores[0] if scores else None,
        'max': scores[-1] if scores else None,
    }


def _aplicar_exclusion(assessments, excluidos):
    """Marca (NUNCA borra) las filas de las empresas excluidas.

    Exclusion REVERSIBLE y auditable: la fila se conserva con health_score
    = NULL, columna booleana `excluida` = true y el motivo
    'excluida_nota_cero_persistente' en `reasons`. Borrar filas estaria
    PROHIBIDO. Devuelve el numero de filas marcadas.
    """
    marcadas = 0
    for row in assessments:
        if row['company_id'] in excluidos:
            row['health_score'] = None
            row['excluida'] = True
            row['reasons'] = [MOTIVO_EXCLUSION, *row['reasons']]
            marcadas += 1
    return marcadas


def _summary_corte(rows, n_pairs):
    """Resumen de un corte sobre poblacion FINAL declarada.

    Las categorias son EXCLUYENTES: n_con_nota + motivos de sin-nota +
    n_excluidas = total_companies. Con la exclusion activada, TODAS las
    estadisticas de poblacion se calculan sin las empresas excluidas y la
    clave 'poblacion' lo declara; ninguna cifra mezcla poblaciones sin
    decirlo.
    """
    con_nota = [row for row in rows if row['health_score'] is not None]
    excluidas = [row for row in rows if row.get('excluida')]
    sin_nota = [row for row in rows
                if row['health_score'] is None and not row.get('excluida')]
    assert len(con_nota) + len(sin_nota) + len(excluidas) == len(rows)
    return {
        'total_companies': len(rows),
        'n_con_nota': len(con_nota),
        'n_excluidas': len(excluidas),
        'n_sin_nota_por_falta_de_actividad': sum(
            row['_motivo_sin_nota'] == 'falta_de_actividad' for row in sin_nota),
        'n_sin_nota_por_pagos_no_demostrados': sum(
            row['_motivo_sin_nota'] == 'pagos_operativos_no_demostrados'
            for row in sin_nota),
        'n_sin_nota_por_sin_flujos': sum(
            row['_motivo_sin_nota'] == 'sin_flujos_observados_en_la_ventana'
            for row in sin_nota),
        'reparto_confidence': dict(sorted(Counter(
            row['confidence'] for row in rows if not row.get('excluida')).items())),
        'poblacion_reparto_confidence': 'todas las empresas del corte salvo '
                                        'las excluidas (excluida = true)',
        'motivos': dict(sorted(Counter(
            reason for row in rows for reason in row['reasons']).items())),
        'empresa_mes_con_volumen_ambiguo': sum(
            row['volumen_ambiguo_eur'] > 0 for row in rows if not row.get('excluida')),
        'pares_capas_join': n_pairs,
        'poblacion': (
            f"todas las empresas del corte ({len(rows)}); las cifras de nota "
            'se calculan sobre las empresas NO excluidas: '
            f'{len(con_nota)} con nota + {len(sin_nota)} sin nota; las '
            f'{len(excluidas)} excluidas conservan su fila en el parquet con '
            'health_score = NULL (nulo nunca es cero) y el motivo '
            f'{MOTIVO_EXCLUSION} en reasons'),
    }


def _comparativa_v3(con, assessments, ultimo_result, excluidos=frozenset()):
    """Comparativa obligatoria v4 vs v3 (D5).

    |dH| p50/p75/p90 SOLO sobre la POBLACION COMUN de pares (company_id,
    month) con nota en ambas versiones; comparar |dH| sobre poblaciones
    distintas es invalido (reports/fix_p6_preservado/FIX_P6.md).
    """
    cutoff = date.fromisoformat(ultimo_result['as_of'])
    v4 = [row for row in ultimo_result['assessments'] if row['as_of'] == cutoff]
    con_nota = [row for row in v4 if row['health_score'] is not None]
    guardadas = [row for row in con_nota if row['p6'] <= 0.0
                 and row['obligacion_vencida_m'] is not None
                 and row['obligacion_vencida_m'] > 0.0]
    comparativa = {
        'poblacion': 'corte 2026-08-31 salvo indicacion; |dH| p50/p75/p90 '
                     'RESTRINGIDO a la poblacion comun de pares (company_id, '
                     'month) con nota en ambas versiones',
        'nota_exclusion': (
            f'{len(excluidos)} empresas excluidas por nota 0 persistente '
            '(excluida = true, health_score NULL): quedan FUERA de estas '
            'cifras v4 y de la poblacion comun; ninguna cifra mezcla '
            'poblaciones sin decirlo'
        ) if excluidos else 'sin exclusion aplicada en este run',
        'referencias': {'v3_sin_fix': REFERENCIA_V3_SIN_FIX,
                        'v3_con_fix_revertido': REFERENCIA_V3_CON_FIX},
        'v4': {
            'n_con_nota': len(con_nota),
            'n_con_nota_99_99_ultimo_corte': sum(
                1 for row in con_nota if row['health_score'] >= UMBRAL_NOTA_ALTA),
            'n_con_nota_99_99_panel': sum(
                1 for row in assessments
                if row['health_score'] is not None
                and row['health_score'] >= UMBRAL_NOTA_ALTA),
            'health_score_p10': _percentile([row['health_score'] for row in con_nota], 0.10),
            'health_score_p50': _percentile([row['health_score'] for row in con_nota], 0.50),
            'health_score_p90': _percentile([row['health_score'] for row in con_nota], 0.90),
            'reparto_confidence': dict(sorted(Counter(
                row['confidence'] for row in v4).items())),
            'empresas_con_nota_gracias_a_la_guarda': len(guardadas),
            'notas_de_esas_empresas': _distribution(
                sorted(row['health_score'] for row in guardadas)),
        },
    }
    v3_path = paths.ROOT / 'reports/score_v3/assessments.parquet'
    if not v3_path.exists():
        comparativa['delta_h_poblacion_comun'] = {
            'nota': 'reports/score_v3/assessments.parquet no disponible; '
                    'sin comparacion de |dH|'}
        return comparativa
    con.execute('''
        CREATE OR REPLACE TEMP TABLE v4_assessments_source (
            company_id VARCHAR, month DATE, health_score DOUBLE)
    ''')
    con.executemany('INSERT INTO v4_assessments_source VALUES (?, ?, ?)',
                    [(row['company_id'], row['month'], row['health_score'])
                     for row in assessments])
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE v3_scores AS
        SELECT company_id, CAST(month AS DATE) AS month, health_score
        FROM {parquet(v3_path)}
        WHERE health_score IS NOT NULL
    ''')
    deltas = [row[0] for row in con.execute('''
        SELECT abs(v4.health_score - v3.health_score)
        FROM v3_scores v3 JOIN v4_assessments_source v4
        ON v3.company_id = v4.company_id AND v3.month = v4.month
        WHERE v4.health_score IS NOT NULL
    ''').fetchall()]
    n_v3 = con.execute('SELECT count(*) FROM v3_scores').fetchone()[0]
    comparativa['v3_sin_fix_medido'] = {
        'n_pares_con_nota_en_el_panel': n_v3,
        'nota': 'pares con health_score no nulo en reports/score_v3/'
                'assessments.parquet (version SIN fix P6)',
    }
    comparativa['delta_h_poblacion_comun'] = {
        'definicion': 'pares (company_id, month) con health_score no nulo en '
                      'v3 y en v4; |dH| = |health_score_v4 - health_score_v3|',
        'n_pares_v3_con_nota': n_v3,
        'n_pares_comunes': len(deltas),
        'p50': _percentile(deltas, 0.50),
        'p75': _percentile(deltas, 0.75),
        'p90': _percentile(deltas, 0.90),
    }
    return comparativa


def _subsets(items):
    result = [frozenset()]
    for item in items:
        result += [subset | {item} for subset in result]
    return result


def _pearson_spearman(con, xs, ys):
    rows = con.execute('''
        WITH data(x, y) AS (SELECT unnest(?::DOUBLE[]), unnest(?::DOUBLE[])),
        ranked AS (SELECT x, y,
                   rank() OVER (ORDER BY x) AS rx, rank() OVER (ORDER BY y) AS ry
                   FROM data)
        SELECT (SELECT corr(x, y) FROM data),
               (SELECT corr(rx, ry) FROM ranked)
    ''', [xs, ys]).fetchone()
    return {'pearson': rows[0], 'spearman': rows[1]}


def _shapley_castigos(channels, scores):
    """Castigo Shapley por canal: castigo_i = -phi_i del valor nota.

    v(S) = nota con los canales de S activos; anadir canales solo baja la
    nota, asi que phi_i <= 0 y castigo_i = -phi_i >= 0. Suma de castigos =
    v(empty) - v(full).
    """
    n = len(channels)
    castigos = {canal: 0.0 for canal in channels}
    for canal in channels:
        for subset in _subsets(channels):
            if canal in subset:
                continue
            weight = (math.factorial(len(subset))
                      * math.factorial(n - len(subset) - 1) / math.factorial(n))
            castigos[canal] += weight * (scores[subset] - scores[subset | {canal}])
    return {canal: max(0.0, valor) for canal, valor in castigos.items()}


def _riesgo_triple_conteo(con, ultimo_result, excluidos=frozenset()):
    """D3: correlaciones y descomposicion del castigo por canal.

    NO cambia la formula: mide la triple exposicion a la senal de facturas
    vencidas impagadas (denominador, multiplicador, mora lado pago) y
    produce una recomendacion para la decision de calibracion del
    coordinador.
    """
    cutoff = date.fromisoformat(ultimo_result['as_of'])
    debt_path = _layer_path('debt_obligation')
    delay_path = _layer_path('payment_delay_v2')
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE tc_deuda AS
        SELECT company_id, CAST(month AS DATE) AS month,
               obligacion_vencida_eur, multiplicador_deuda
        FROM {parquet(debt_path)}
        WHERE CAST(month AS DATE) <= ?
    ''', [cutoff])
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE tc_mora AS
        SELECT company_id, CAST(month AS DATE) AS month, mora_pago_robusta
        FROM {parquet(delay_path)}
        WHERE CAST(month AS DATE) <= ?
    ''', [cutoff])
    pairs = con.execute('''
        SELECT d.obligacion_vencida_eur AS obligacion,
               d.multiplicador_deuda AS multiplicador,
               m.mora_pago_robusta AS mora_pago
        FROM tc_deuda d JOIN tc_mora m USING (company_id, month)
    ''').fetchall()
    correlaciones = {}
    for name, keep in (('obligacion_vs_multiplicador', (0, 1)),
                       ('obligacion_vs_mora_pago', (0, 2)),
                       ('multiplicador_vs_mora_pago', (1, 2))):
        sample = [(row[i], row[j]) for row in pairs
                  for i, j in [keep]
                  if row[i] is not None and row[j] is not None]
        metrica = _pearson_spearman(con, [a for a, _ in sample],
                                    [b for _, b in sample])
        metrica['n'] = len(sample)
        correlaciones[name] = metrica

    # Descomposicion del castigo (Shapley sobre 3 canales binarios) en el
    # ultimo corte: cuanto pierde la nota por cada canal por separado.
    params = ultimo_result['params']
    monthly_rows = ultimo_result['_monthly_rows']
    groups = {row['company_id']: row['group_id']
              for row in ultimo_result['assessments']}
    filas = []
    descartadas = 0
    omitidas = 0
    sin_nota_base = 0
    excluidas_omitidas = 0
    for company in sorted(monthly_rows):
        if company in excluidos:
            # Poblacion declarada: las excluidas quedan fuera del analisis de
            # castigos (estan fuera del algoritmo v4); se cuentan y no se
            # mezclan con el resto.
            excluidas_omitidas += 1
            continue
        rows = monthly_rows[company]
        as_of_row_index = next((i for i, r in enumerate(rows)
                                if r['month'] == cutoff.replace(day=1)), None)
        if as_of_row_index is None:
            omitidas += 1
            continue
        full = frozenset(_CHANNELS)

        def nota_con(active):
            filas = [dict(r) for r in rows]
            fila = filas[as_of_row_index]
            if 'obligacion_en_denominador' not in active:
                fila['obligacion_vencida_eur'] = None
            if 'mora_indice' not in active:
                fila['mora_indice'] = None
            if 'multiplicador_deuda' not in active:
                fila['multiplicador_deuda'] = None
            engine = score_company(company, groups[company], filas, cutoff, **params)
            return engine['health_score']

        scores = {full: nota_con(full)}
        if scores[full] is None:
            sin_nota_base += 1
            continue
        imposible = False
        for subset in _subsets(_CHANNELS):
            if subset == full:
                continue
            value = nota_con(subset)
            if value is None:
                # Desactivar el canal denominador puede retirar la nota
                # (nueva guarda): sin obligacion vencida y con p6 = 0 no hay
                # nota, y sin nota no hay castigo descomponible.
                imposible = True
                break
            scores[subset] = value
        if imposible:
            descartadas += 1
            continue
        castigos = _shapley_castigos(_CHANNELS, scores)
        sin_castigos = scores[frozenset()]
        filas.append({
            'company_id': company,
            'nota_final': scores[full],
            'nota_sin_castigos': sin_castigos,
            'castigo_total_puntos': sin_castigos - scores[full],
            **{f'castigo_{canal}_puntos': castigos[canal] for canal in _CHANNELS},
        })
    filas.sort(key=lambda row: -row['castigo_total_puntos'])
    con_penal = [row for row in filas if row['castigo_total_puntos'] > 1e-9]
    shares = {canal: [row[f'castigo_{canal}_puntos'] / row['castigo_total_puntos']
                      for row in con_penal] for canal in _CHANNELS}
    reparto = {
        canal: {
            'p50': _percentile(vals, 0.50),
            'p90': _percentile(vals, 0.90),
            'n_positivo': sum(1 for v in vals if v > 1e-9),
        } for canal, vals in shares.items()
    }
    return {
        'corte': cutoff.isoformat(),
        'poblacion': 'pares (company_id, month) de meses cerrados <= corte '
                     'con ambos valores no nulos (nulo nunca es cero)',
        'nota_exclusion': (
            f'{excluidas_omitidas} empresas excluidas por nota 0 persistente '
            'quedan fuera de este analisis (estan fuera del algoritmo v4); '
            'ninguna cifra mezcla poblaciones sin decirlo'
        ) if excluidas_omitidas else 'sin exclusion aplicada en este run',
        'correlaciones': correlaciones,
        'descomposicion_castigo': {
            'metodo': 'Shapley sobre los 3 canales binarios (8 combinaciones '
                      'por empresa; el motor se reevalua con cada canal '
                      'desactivado); castigos en puntos de nota',
            'canales': list(_CHANNELS),
            'n_empresas_evaluadas': len(filas),
            'n_empresas_omitidas_sin_fila_en_el_corte': omitidas,
            'n_empresas_sin_nota_en_el_corte': sin_nota_base,
            'n_empresas_descartadas_por_nota_none_en_variante': descartadas,
            'nota_sobre_descartadas': 'las descartadas son en su mayor parte '
                                      'empresas que reciben nota solo gracias '
                                      'a la nueva guarda (p6=0 con obligacion '
                                      'vencida): sin el canal denominador no '
                                      'habria nota y no hay castigo que '
                                      'descomponer',
            'top_penalizadas': filas[:15],
            'reparto_del_castigo_entre_penalizadas': reparto,
        },
    }


_CHANNELS = ('obligacion_en_denominador', 'mora_indice', 'multiplicador_deuda')


def _fingerprint(directories):
    fingerprint = {}
    for directory in directories:
        for file in sorted(Path(directory).rglob('*')):
            if file.is_file():
                fingerprint[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    return fingerprint


def _public_summary(result, declaracion_exclusion=None):
    """Resumen publico del ultimo corte, sobre poblacion DECLARADA.

    Con la exclusion activada, TODAS las cifras de poblacion (n_con_nota,
    motivos, confidence, percentiles) se recalculan SIN las empresas
    excluidas, que conservan su fila con health_score = NULL: ninguna cifra
    publica mezcla poblaciones sin decirlo.
    """
    rows = result['assessments']
    con_nota = [row['health_score'] for row in rows
                if row['health_score'] is not None]
    excluidas = [row for row in rows if row.get('excluida')]
    sin_nota = [row for row in rows
                if row['health_score'] is None and not row.get('excluida')]
    assert (len(con_nota) + len(sin_nota) + len(excluidas)) == len(rows)
    public = {
        'as_of': result['as_of'],
        'poblacion': (
            f"todas las empresas del corte ({len(rows)}); TODAS las cifras de "
            'este bloque se calculan sobre la poblacion NO excluida: '
            f'{len(con_nota)} con nota + {len(sin_nota)} sin nota; las '
            f'{len(excluidas)} excluidas conservan su fila con health_score = '
            'NULL (nulo nunca es cero)'),
        'total_companies': len(rows),
        'n_con_nota': len(con_nota),
        'n_excluidas': len(excluidas),
        'n_sin_nota_por_falta_de_actividad': sum(
            row['_motivo_sin_nota'] == 'falta_de_actividad' for row in sin_nota),
        'n_sin_nota_por_pagos_no_demostrados': sum(
            row['_motivo_sin_nota'] == 'pagos_operativos_no_demostrados'
            for row in sin_nota),
        'n_sin_nota_por_sin_flujos': sum(
            row['_motivo_sin_nota'] == 'sin_flujos_observados_en_la_ventana'
            for row in sin_nota),
        'reparto_confidence': dict(sorted(Counter(
            row['confidence'] for row in rows if not row.get('excluida')).items())),
        'poblacion_reparto_confidence': 'todas las empresas del corte salvo '
                                        'las excluidas (excluida = true)',
        'health_score_p10': _percentile(con_nota, 0.10) if con_nota else None,
        'health_score_p50': _percentile(con_nota, 0.50) if con_nota else None,
        'health_score_p90': _percentile(con_nota, 0.90) if con_nota else None,
        'empresa_mes_con_volumen_ambiguo': sum(
            row['volumen_ambiguo_eur'] > 0 for row in rows if not row.get('excluida')),
    }
    if declaracion_exclusion is not None:
        public['declaracion_exclusion'] = declaracion_exclusion
    return public


def run_score_v4(output: Path, k=DEFAULT_K, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA,
                 excluir_cero_persistente=True):
    """Genera assessments.parquet y summary.json para todos los meses cerrados.

    La exclusion de empresas con nota 0 persistente es un PARAMETRO de
    perimetro (nunca de formula): activada por defecto (decision de
    producto del usuario) y reversible con excluir_cero_persistente=False
    (flag CLI --no-excluir-cero-persistente). Con la exclusion activada:
      - calcula el criterio sobre las filas PRE-exclusion y escribe
        exclusiones.json en el directorio de salida;
      - marca (NUNCA borra) las filas de las excluidas con health_score =
        NULL, excluida = true y el motivo en reasons;
      - recalcula TODAS las estadisticas de poblacion sin ellas, declarando
        explicitamente sobre que poblacion se calcula cada cifra.

    Devuelve el informe publicado por stdout: resumen del ultimo corte,
    comparativa v3 vs v4 (poblacion declarada) y analisis de triple conteo.
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
            month = date(month.year + (month.month == 12),
                         1 if month.month == 12 else month.month + 1, 1)
        assessments = []
        pares_por_corte = {}
        ultimo_result = None
        for month in months:
            cutoff = month.replace(day=monthrange(month.year, month.month)[1])
            result = assess_at_v4(con, cutoff, None, k=k, alpha=alpha, beta=beta)
            assessments.extend(result['assessments'])
            pares_por_corte[result['as_of']] = result['summary']['pares_capas_join']
            ultimo_result = result
        if _fingerprint(directories) != before:
            raise ValueError('Las fuentes cambiaron durante la evaluacion; no se publica nada')

        # --- Exclusion de perimetro (reversible; el motor NO se toca) --------
        excluidos = set()
        exclusion_report = None
        if excluir_cero_persistente:
            # El criterio se mide sobre las filas PRE-exclusion: build ANTES
            # de marcar las filas.
            exclusion_report = build_exclusion_report(assessments, con)
            excluidos = excluded_ids(exclusion_report)
            filas_excluidas = _aplicar_exclusion(assessments, excluidos)
        for row in assessments:
            row.setdefault('excluida', False)

        # Resumen por corte RECALCULADO sobre la poblacion final declarada.
        rows_por_corte = defaultdict(list)
        for row in assessments:
            rows_por_corte[row['as_of'].isoformat()].append(row)
        por_corte = {as_of: _summary_corte(rows_por_corte[as_of], pares_por_corte[as_of])
                     for as_of in sorted(rows_por_corte)}
        ultimo_resumen = _public_summary(
            ultimo_result,
            declaracion_exclusion=(exclusion_report['declaracion']
                                   if exclusion_report else None))
        comparativa = _comparativa_v3(con, assessments, ultimo_result, excluidos)
        triple = _riesgo_triple_conteo(con, ultimo_result, excluidos)

        output = Path(output)
        output.mkdir(parents=True)
        if exclusion_report is not None:
            write_exclusions_json(output / 'exclusiones.json', exclusion_report)
        columns = ', '.join(f'{name} {dtype}' for name, dtype in ASSESSMENT_SCHEMA)
        con.execute(f'CREATE OR REPLACE TEMP TABLE assessments ({columns})')
        placeholders = ','.join('?' for _ in ASSESSMENT_SCHEMA)
        fields = [name for name, _ in ASSESSMENT_SCHEMA]
        con.executemany(
            f'INSERT INTO assessments VALUES ({placeholders})',
            [tuple(row[field] for field in fields) for row in assessments])
        target = str(output / 'assessments.parquet').replace("'", "''")
        con.execute(f"COPY assessments TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")

        exclusion_bloque = {
            'activada': bool(exclusion_report),
            'flag_activar': '--excluir-cero-persistente',
            'flag_desactivar': '--no-excluir-cero-persistente',
            'valor_por_defecto': 'activada (decision de producto del usuario; '
                                 'reversible con --no-excluir-cero-persistente)',
        }
        if exclusion_report is not None:
            exclusion_bloque.update({
                'motivo_reasons': MOTIVO_EXCLUSION,
                'criterio_literal': exclusion_report['criterio_literal'],
                'umbrales': exclusion_report['umbrales'],
                'n_empresas_excluidas': len(excluidos),
                'cifras_agregadas': exclusion_report['cifras_agregadas'],
                'efecto_corte_referencia': exclusion_report['efecto_corte_referencia'],
                'actividad_real_excluidas': exclusion_report['actividad_real_excluidas'],
                'declaracion': exclusion_report['declaracion'],
                'fichero': 'reports/score_v4/exclusiones.json',
            })
        else:
            exclusion_bloque['nota'] = (
                'run SIN exclusion: salida identica a la del perimetro '
                'completo (mismas filas y mismas notas; ninguna empresa '
                'marcada con excluida)')

        report = {
            'model_version': MODEL_VERSION,
            'params': {'k': float(k), 'alpha': float(alpha), 'beta': float(beta)},
            'params_calibrados': {'k': False, 'alpha': False, 'beta': False},
            'advertencia': ADVERTENCIA_CALIBRACION,
            'limitaciones': list(MOTOR_LIMITACIONES),
            'exclusion': exclusion_bloque,
            'por_corte': por_corte,
            'ultimo_corte': ultimo_resumen,
            'comparativa_v3': comparativa,
            'riesgo_triple_conteo': triple,
        }
        (output / 'summary.json').write_text(
            json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
        return report
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Puntua healthscore_v4 para todos los meses cerrados y empresas')
    parser.add_argument('--workspace', type=Path, default=None,
                        help='workspace de datos (data/runs/<id>); por defecto '
                             'el publicado en reports/current.json')
    parser.add_argument('--output', type=Path, default=paths.ROOT / 'reports/score_v4')
    parser.add_argument('--k', type=float, default=DEFAULT_K)
    parser.add_argument('--alpha', type=float, default=DEFAULT_ALPHA)
    parser.add_argument('--beta', type=float, default=DEFAULT_BETA)
    exclusions = parser.add_mutually_exclusive_group()
    exclusions.add_argument('--excluir-cero-persistente', dest='excluir',
                            action='store_true', default=True,
                            help='excluye las empresas con nota 0 persistente '
                                 '(CRITERIO en xray/score_exclusions_v4.py); es '
                                 'el VALOR POR DEFECTO, decision de producto '
                                 'del usuario')
    exclusions.add_argument('--no-excluir-cero-persistente', dest='excluir',
                            action='store_false',
                            help='revierte la exclusion: salida identica al '
                                 'perimetro completo (mismas notas, misma '
                                 'poblacion); la exclusion es un parametro, '
                                 'no una constante')
    args = parser.parse_args(argv)
    if args.output.exists():
        print('La salida ya existe; usa --output con una ruta nueva para '
              'conservar artefactos previos', file=sys.stderr)
        return 1
    for name, value in (('k', args.k), ('alpha', args.alpha), ('beta', args.beta)):
        if value != value or value < 0:
            print(f'--{name}: debe ser un numero finito no negativo', file=sys.stderr)
            return 1
    restore = None
    if args.workspace is not None:
        workspace = args.workspace.resolve()
        if not (workspace / 'data/clean').is_dir():
            print(f'Error: el workspace {workspace} no tiene data/clean',
                  file=sys.stderr)
            return 1
        mapping = {'WORKSPACE': workspace,
                   'CLEAN_DIR': workspace / 'data/clean',
                   'MARTS_DIR': workspace / 'data/marts',
                   'INTERIM_DIR': workspace / 'data/interim'}
        restore = vars(paths).copy()
        for name, value in mapping.items():
            setattr(paths, name, value)
    try:
        report = run_score_v4(args.output, args.k, args.alpha, args.beta,
                              excluir_cero_persistente=args.excluir)
    except (ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        if restore is not None:
            for name, value in restore.items():
                if name in vars(paths):
                    vars(paths)[name] = value
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
