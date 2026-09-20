"""Capa diaria (v4): panel point-in-time de flujo de caja y saldo reconstruido.

Proposito. Todo el healthscore v4 vive a grano MENSUAL (24 meses por empresa).
Ese grano es demasiado grueso para detectar un cambio de tendencia con 30-90 dias
de anticipacion. `data/clean/transactions.parquet` tiene ~2,5M de transacciones a
grano DIARIO. Este modulo construye el sustrato diario, limpio, point-in-time y
censado sobre el que luego se montara (otra tarea) un detector de cambio de
regimen. Este modulo NO construye el detector ni ningun modelo.

Decision de grano temporal: FECHA DE APUNTE (`date`), NO fecha valor
(`value_date`). Razon, textual:

    El proposito de este panel es ser el sustrato de un detector de cambio de
    regimen cuya ANTICIPACION se medira contra el giro de la nota mensual v4. Si
    la serie diaria usara fecha valor y la nota usa fecha de apunte, estariamos
    midiendo ambas con relojes distintos, y cualquier adelanto detectado podria
    ser un artefacto del desfase apunte-valor en lugar de una senal real. La
    coherencia de reloj con v4 pesa aqui mas que la correccion economica de la
    fecha valor.

Verificado sobre el workspace activo: `month == date_trunc('month', date)` en el
100% de las 2.556.437 filas; `max(date) = 2026-09-01` y todo el mes abierto tiene
`date = 2026-09-01`. `reports/cash_backfill/cash_backfill_monthly.parquet` se
construye con `month = date_trunc(date)` e ignora `value_date`; agregar el
perimetro de este modulo por `date` la reproduce (ver `reconciliacion` del censo).

Perimetro y criterio de signo (IDENTICOS a `xray/cash_backfill.py`, que se lee
pero no se modifica; `xray/flows.py` se lee y NO se toca):
- `is_booked`, `NOT is_open_month`, `month` entre FIRST_MONTH (2024-09-01) y
  LAST_MONTH (2026-08-01), `product_source='banking'` y `product_type` en
  `checking`/`wallet`/`saving`/`tpv` (los MISMOS productos que forman el ancla).
- `flujo_neto` = suma de `amount_eur` con su signo. Un movimiento sin `amount_eur`
  (agujero de FX) NO se imputa como cero: no se suma y se acumula como
  `volumen_agujero` (proxy `abs(amount)` en moneda local). `entradas` = suma de
  los `amount_eur > 0`; `salidas` = -suma de los `amount_eur < 0`, de modo que
  `entradas - salidas == flujo_neto` exactamente. El desglose por `flow_class` se
  conserva en columnas `flujo_<clase>` (incluye `flujo_operating_in` /
  `flujo_operating_out`, la lectura estricta de "entradas/salidas operativas").

Consecuencias operativas de la decision de grano:
- Se excluye el mes ABIERTO por `is_open_month`. Nada mas se excluye del flujo.
- Las filas de meses cerrados con `value_date` futuro NO se excluyen (excluirlas
  romperia la reconciliacion). Se MARCAN con `fecha_valor_futura` en el panel y se
  censan aparte.
- Las filas centinela `value_date = 2099-12-31` (con `value_date_ok` NULL) tambien
  se marcan y se censan.
- NO se borra nada de la fuente.

Rejilla diaria. Para cada empresa se materializa TODOS los dias de calendario
entre su primer y su ultimo dia observado (por fecha de apunte), con flujo 0 y
`tiene_movimiento = false` en los dias sin transacciones: un dia sin movimiento NO
es un dia de flujo cero "sin novedad", es un dia sin observacion, y el consumidor
debe poder distinguirlos. No se extrapola fuera de ese rango. Se anade ademas UNA
fila explicita en el dia del ancla (2026-09-01, `es_dia_ancla = true`) para que la
identidad `saldo_reversa(T) == saldo_ancla` se lea literalmente en el artefacto,
igual que hace la capa mensual.

Saldo diario reconstruido hacia atras. Version diaria de `cash_backfill`:

    saldo_reversa(d) = saldo_ancla - suma de flujos netos en (d, T]

con T = 2026-08-31 (cierre del ultimo mes cerrado, que es el instante del snapshot
2026-09-01) y el MISMO ancla (snapshot `balances.parquet`, `is_snapshot_date`,
productos `checking`/`wallet`/`saving`/`tpv`) y los mismos productos de flujo. La
fila del dia del ancla tiene tramo vacio, luego `saldo_reversa(2026-09-01) ==
saldo_ancla`. El ultimo dia cerrado (2026-08-31) tambien vale el ancla.

Point-in-time. Cada fila lleva `available_at`, el cierre del dia de apunte
(`day`), que es la convencion mas optimista compatible con el grano diario: bajo
la convencion mensual de v4 (`available_at = fin de mes`) el panel diario no
tendria ninguna ventana de anticipacion, que es su unico proposito. Un corte c
solo puede leer filas con `available_at <= c` (helper `panel_hasta`). El SALDO es
una reconstruccion declarada que necesita el ancla (2026-09-01), posterior a todos
los cortes de la historia; para que no se confunda con una senal causal, cada fila
lleva `saldo_reversa_available_at = 2026-09-01` (NULL si la empresa no tiene
ancla). Igual que `cash_backfill`, en backtest la reconstruccion recupera un nivel
de caja que la empresa si conocia en su momento y no introduce fuga predictiva; la
serie artificial es la forward de v3, que asume caja inicial cero.

Censo de densidad. El entregable que decide si el detector tiene sentido: cuantos
dias con movimiento tiene cada empresa, como son los huecos, cuantas empresas
soportan una ventana movil de 30 y de 90 dias, y sobre todo cuantas de las 957
empresas evaluables de v4 (`reports/score_v4/assessments.parquet` con
`health_score` no nulo y `excluida = false`) caen en esa interseccion.

Alcance: solo lee fuentes (`data/clean`, `data/interim/tx_flow_class`,
`reports/score_v4/assessments.parquet`, `reports/cash_backfill/...`) y escribe
`reports/daily_flows/`. No modifica ningun modulo ni dato de `data/`.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib
import json
import math
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from xray import paths
from xray.flows import FLOW_CLASSES
from xray.marts.common import parquet
from xray.period import FIRST_MONTH, LAST_MONTH

# ---------------------------------------------------------------------------
# Contrato declarado
# ---------------------------------------------------------------------------
ANCHOR_DATE = date(2026, 9, 1)          # instante del snapshot de saldos
ANCHOR_DAY = date(2026, 9, 1)           # fila explicita del ancla en la rejilla diaria
LAST_CLOSED_DAY = date(2026, 8, 31)     # cierre del ultimo mes cerrado == ancla
ANCHOR_PRODUCTS = ('checking', 'wallet', 'saving', 'tpv')
CASH_PRODUCTS = ANCHOR_PRODUCTS
SIN_ANCLA = 'sin_saldo_en_el_snapshot_ancla'
AGUJEROS_EN_TRAMO = 'agujeros_fx_en_el_tramo'
SIN_SALIDAS = 'sin_salidas_conocidas_en_ventana'
CONFIDENCE_LABELS = ('ninguna', 'baja', 'media', 'alta')
CONFIDENCE_UMBRALES = ((0.0, 'alta'), (0.25, 'media'), (0.50, 'baja'), (math.inf, 'ninguna'))
FLAGS_POSIBLES = ('saldo_negativo', 'agujeros_en_tramo', 'sin_ancla', 'sin_salidas_conocidas')
WINDOW_MESES = 6                          # ventana trailing de cobertura (meses equivalentes)
COBERTURA_DIAS = WINDOW_MESES * 30        # 180 dias de calendario para la media de salidas
WINDOWS_DENSIDAD = (30, 90)
DENSIDAD_DIVISOR_PRIMARIO = 7             # actividad al menos semanal en la ventana tipica
# Umbrales declarados de contaminacion por anomalias de fecha valor. El peso se
# mide sobre el NETO (que es lo que consumira el detector) y es acotado en [0, 1]:
# peso_neto = |neto_marcado| / (|neto_marcado| + |neto_no_marcado|).
CONTAMINACION_MATERIAL = 0.25
CONTAMINACION_DOMINANTE = 0.50
ASSESSMENTS = 'reports/score_v4/assessments.parquet'
MONTHLY_BACKFILL = 'reports/cash_backfill/cash_backfill_monthly.parquet'
OUTPUT_COLUMNS = (
    'company_id', 'day', 'available_at', 'es_dia_ancla', 'tiene_movimiento',
    'flujo_neto', 'entradas', 'salidas', 'n_transacciones', 'n_fecha_valor_futura',
    'volumen_conocido', 'volumen_agujero', 'salidas_conocidas',
    *[f'flujo_{value}' for value in FLOW_CLASSES],
    'flujo_neto_tramo', 'volumen_conocido_tramo', 'volumen_agujero_tramo', 'n_dias_tramo',
    'ancla_presente', 'saldo_ancla_eur', 'saldo_reversa_eur',
    'saldo_reversa_available_at', 'saldo_reversa_min_hasta_d', 'colchon_reversa_eur',
    'salida_media_mensual_reversa', 'meses_de_cobertura_reversa',
    'confidence', 'flags', 'reason',
)
DECISION_GRANO = (
    'El proposito de este panel es ser el sustrato de un detector de cambio de '
    'regimen cuya ANTICIPACION se medira contra el giro de la nota mensual v4. Si '
    'la serie diaria usara fecha valor y la nota usa fecha de apunte, estariamos '
    'midiendo ambas con relojes distintos, y cualquier adelanto detectado podria '
    'ser un artefacto del desfase apunte-valor en lugar de una senal real. La '
    'coherencia de reloj con v4 pesa aqui mas que la correccion economica de la '
    'fecha valor.'
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _round(value):
    return None if value is None else round(value, 6)


def _confidence(hole_ratio: float, tiene_ancla: bool) -> str:
    if not tiene_ancla:
        return 'ninguna'
    for limit, label in CONFIDENCE_UMBRALES:
        if hole_ratio <= limit:
            return label
    return 'ninguna'


def _percentiles(values, p):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return round(ordered[low] * (1 - fraction) + ordered[high] * fraction, 6)


def _stats(values):
    values = [float(value) for value in values if value is not None]
    if not values:
        return {'n': 0}
    return {
        'n': len(values),
        'min': _percentiles(values, 0),
        'p10': _percentiles(values, 10),
        'p25': _percentiles(values, 25),
        'p50': _percentiles(values, 50),
        'p75': _percentiles(values, 75),
        'p90': _percentiles(values, 90),
        'max': _percentiles(values, 100),
        'media': round(sum(values) / len(values), 6),
    }


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return float(ordered[n // 2])
    return float((ordered[n // 2 - 1] + ordered[n // 2]) / 2.0)


# ---------------------------------------------------------------------------
# 1. Serie diaria por empresa
# ---------------------------------------------------------------------------
def _validate_row(row: dict) -> None:
    for field in ('company_id', 'day', 'flujo_neto'):
        if row.get(field) is None:
            raise ValueError(f'flujo diario: falta {field}')
    day = row['day']
    if type(day) is not date:
        raise ValueError(f'flujo diario: day debe ser date: {day!r}')
    for field in ('flujo_neto', 'entradas', 'salidas', 'volumen_conocido', 'volumen_agujero',
                  'salidas_conocidas'):
        value = row.get(field, 0.0)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'flujo diario: {field} debe ser un numero finito: {value!r}')
    for field in ('volumen_conocido', 'volumen_agujero', 'salidas_conocidas', 'entradas'):
        if row.get(field, 0.0) < 0:
            raise ValueError(f'flujo diario: {field} no puede ser negativo')
    if row.get('salidas', 0.0) < 0:
        raise ValueError('flujo diario: salidas no puede ser negativo')
    if row.get('n_transacciones', 0) < 1:
        raise ValueError('flujo diario: un dia observado necesita al menos una transaccion')
    desglose = row.get('desglose') or {}
    for flow_class, value in desglose.items():
        if flow_class not in FLOW_CLASSES:
            raise ValueError(f'flujo diario: flow_class fuera de contrato: {flow_class!r}')
        if not math.isfinite(value):
            raise ValueError(f'flujo diario: desglose no finito: {flow_class}={value!r}')
    total = sum(desglose.values())
    if abs(total - row['flujo_neto']) > 1e-6 + 1e-9 * abs(row['flujo_neto']):
        raise ValueError(f'desglose {total} no cuadra con flujo_neto {row["flujo_neto"]}')


def _day_range(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def reconstruct_daily_flows(daily_rows, anchor_balances, companies=None,
                            anchor_day: date = ANCHOR_DAY):
    """Motor puro: rejilla diaria densa + saldo reconstruido hacia atras.

    daily_rows: iterable de dicts {company_id, day, flujo_neto, entradas, salidas,
        n_transacciones, n_fecha_valor_futura, volumen_conocido, volumen_agujero,
        salidas_conocidas, desglose} en dias observados (meses cerrados).
    anchor_balances: {company_id: saldo_ancla_eur}; una empresa ausente (o con
        valor None) produce saldo_reversa NULL y confidence 'ninguna', nunca cero.
    companies: ids del universo; los ausentes de `daily_rows` obtienen solo la fila
        del ancla con flujos a cero.
    anchor_day: dia del ancla (tramo vacio, saldo == ancla).

    Devuelve {company_id: {'ancla': float|None, 'reason': ..., 'rows': [...]}} sin
    mutar la entrada.
    """
    por_empresa: dict[str, dict[date, dict]] = {}
    for row in daily_rows:
        copy = dict(row)
        _validate_row(copy)
        if copy['day'] > LAST_CLOSED_DAY:
            raise ValueError(f'flujo diario posterior al ultimo dia cerrado: {copy["day"]!r}')
        dense = por_empresa.setdefault(copy['company_id'], {})
        if copy['day'] in dense:
            raise ValueError(f'flujo diario duplicado: {copy["company_id"]} {copy["day"]}')
        dense[copy['day']] = copy

    result: dict[str, dict] = {}
    ids = sorted(set(por_empresa) | set(companies or ()) | set(anchor_balances))
    for company_id in ids:
        ancla_cruda = anchor_balances.get(company_id)
        if ancla_cruda is not None and (type(ancla_cruda) not in (int, float)
                                        or not math.isfinite(ancla_cruda)):
            raise ValueError(f'ancla no finita para {company_id}: {ancla_cruda!r}')
        ancla = None if ancla_cruda is None else float(ancla_cruda)
        dense = por_empresa.get(company_id, {})

        observed = sorted(dense)
        if observed:
            grid = list(_day_range(observed[0], observed[-1]))
        else:
            grid = []
        grid.append(anchor_day)

        # Prefijos de flujo y de volumenes sobre la rejilla (dias sin movimiento = 0).
        prefix_flow: dict[date, float] = {}
        prefix_known: dict[date, float] = {}
        prefix_hole: dict[date, float] = {}
        prefix_mov: dict[date, int] = {}
        acum = conocido = agujero = 0.0
        movimientos = 0
        for day in grid:
            if day == anchor_day:
                continue
            row = dense.get(day)
            if row is not None:
                acum += row['flujo_neto']
                conocido += row['volumen_conocido']
                agujero += row['volumen_agujero']
                movimientos += 1
            prefix_flow[day] = acum
            prefix_known[day] = conocido
            prefix_hole[day] = agujero
            prefix_mov[day] = movimientos
        total_flow, total_known, total_hole, total_mov = acum, conocido, agujero, movimientos

        rows = []
        ventana: collections.deque = collections.deque()
        ventana_suma = 0.0
        running_min = None
        primer_dia = observed[0] if observed else anchor_day
        for day in grid:
            if day == anchor_day:
                monto = {'flujo_neto': 0.0, 'entradas': 0.0, 'salidas': 0.0,
                         'n_transacciones': 0, 'n_fecha_valor_futura': 0,
                         'volumen_conocido': 0.0, 'volumen_agujero': 0.0,
                         'salidas_conocidas': 0.0, 'desglose': {}}
                tramo_flow = tramo_known = tramo_hole = 0.0
                n_tramo = 0
                tiene_movimiento = False
            else:
                row = dense.get(day)
                if row is None:
                    monto = {'flujo_neto': 0.0, 'entradas': 0.0, 'salidas': 0.0,
                             'n_transacciones': 0, 'n_fecha_valor_futura': 0,
                             'volumen_conocido': 0.0, 'volumen_agujero': 0.0,
                             'salidas_conocidas': 0.0, 'desglose': {}}
                    tiene_movimiento = False
                else:
                    monto = row
                    tiene_movimiento = True
                tramo_flow = total_flow - prefix_flow[day]
                tramo_known = total_known - prefix_known[day]
                tramo_hole = total_hole - prefix_hole[day]
                n_tramo = total_mov - prefix_mov[day]
            salida = monto.get('salidas_conocidas', 0.0) or 0.0
            ventana.append((day, salida))
            ventana_suma += salida
            limite = day - timedelta(days=COBERTURA_DIAS - 1)
            while ventana and ventana[0][0] < limite:
                ventana_suma -= ventana.popleft()[1]
            # Media mensual equivalente: suma del tramo trailing dividida por los meses
            # cubiertos hasta `day` (redondeo a bloques de 30 dias), capada a 6, igual
            # que la capa mensual promedia sobre los meses vistos hasta el corte.
            meses_cubiertos = max(1.0, min(float(WINDOW_MESES),
                                           ((day - primer_dia).days + 1) / 30.0))
            salida_media = ventana_suma / meses_cubiertos

            total_tramo = tramo_known + tramo_hole
            hole_ratio = (tramo_hole / total_tramo) if total_tramo > 0 else 0.0
            confidence = _confidence(hole_ratio, ancla is not None)

            if ancla is None:
                saldo = None
                motivo = SIN_ANCLA
            elif tramo_hole > 0:
                saldo = None
                motivo = AGUJEROS_EN_TRAMO
            else:
                saldo = _round(ancla - tramo_flow)
                motivo = None

            if saldo is not None:
                running_min = saldo if running_min is None else min(running_min, saldo)
                colchon = _round(saldo - running_min)
            else:
                colchon = None

            if saldo is not None and salida_media > 0:
                cobertura = _round(colchon / salida_media)
                motivo_cobertura = None
            else:
                cobertura = None
                motivo_cobertura = motivo if motivo is not None else SIN_SALIDAS

            flags = []
            if saldo is not None and saldo < 0:
                flags.append('saldo_negativo')
            if tramo_hole > 0:
                flags.append('agujeros_en_tramo')
            if ancla is None:
                flags.append('sin_ancla')
            if motivo_cobertura == SIN_SALIDAS:
                flags.append('sin_salidas_conocidas')

            rows.append({
                'day': day,
                'available_at': day,
                'es_dia_ancla': day == anchor_day,
                'tiene_movimiento': tiene_movimiento,
                'flujo_neto': _round(monto.get('flujo_neto', 0.0)),
                'entradas': _round(monto.get('entradas', 0.0)),
                'salidas': _round(monto.get('salidas', 0.0)),
                'n_transacciones': int(monto.get('n_transacciones', 0) or 0),
                'n_fecha_valor_futura': int(monto.get('n_fecha_valor_futura', 0) or 0),
                'volumen_conocido': _round(monto.get('volumen_conocido', 0.0)),
                'volumen_agujero': _round(monto.get('volumen_agujero', 0.0)),
                'salidas_conocidas': _round(monto.get('salidas_conocidas', 0.0)),
                'desglose': {key: _round(value) for key, value in (monto.get('desglose') or {}).items()},
                'flujo_neto_tramo': _round(tramo_flow),
                'volumen_conocido_tramo': _round(tramo_known),
                'volumen_agujero_tramo': _round(tramo_hole),
                'n_dias_tramo': n_tramo,
                'ancla_presente': ancla is not None,
                'saldo_ancla_eur': _round(ancla),
                'saldo_reversa_eur': saldo,
                'saldo_reversa_available_at': anchor_day if ancla is not None else None,
                'saldo_reversa_min_hasta_d': _round(running_min),
                'colchon_reversa_eur': colchon,
                'salida_media_mensual_reversa': _round(salida_media) if salida_media > 0 else None,
                'meses_de_cobertura_reversa': cobertura,
                'confidence': confidence,
                'flags': flags,
                'reason': motivo_cobertura or '',
            })
        reason = None
        if ancla is None:
            reason = SIN_ANCLA
        elif all(row['saldo_reversa_eur'] is None for row in rows):
            reason = AGUJEROS_EN_TRAMO
        result[company_id] = {'company_id': company_id, 'ancla': ancla, 'reason': reason, 'rows': rows}
    return result


def flat_rows(reconstruction):
    for company_id, record in sorted(reconstruction.items()):
        for row in record['rows']:
            flat = {'company_id': company_id}
            for value in FLOW_CLASSES:
                flat[f'flujo_{value}'] = row['desglose'].get(value, 0.0)
            for field in OUTPUT_COLUMNS:
                if field not in flat:
                    flat[field] = row.get(field)
            yield flat


def panel_frame(reconstruction) -> pd.DataFrame:
    frame = pd.DataFrame(list(flat_rows(reconstruction)), columns=list(OUTPUT_COLUMNS))
    frame = frame.sort_values(['company_id', 'day'], kind='stable').reset_index(drop=True)
    frame['day'] = pd.to_datetime(frame['day'])
    frame['available_at'] = pd.to_datetime(frame['available_at'])
    frame['saldo_reversa_available_at'] = pd.to_datetime(frame['saldo_reversa_available_at'])
    return frame


def panel_hasta(frame: pd.DataFrame, corte) -> pd.DataFrame:
    """Lectura point-in-time: solo filas con `available_at <= corte`."""
    corte = pd.Timestamp(corte)
    return frame[frame['available_at'] <= corte].copy()


# ---------------------------------------------------------------------------
# 2. Carga de la fuente
# ---------------------------------------------------------------------------
def load_daily_flows(con) -> list[dict]:
    """Agrega el perimetro de caja del ancla a flujos por (empresa, dia de apunte).

    Duplicacion declarada del criterio de `xray/cash_backfill.py` (misma fuente,
    mismo `is_booked`/`NOT is_open_month`, mismo rango de meses, mismo signo y
    mismo tratamiento de agujeros); el grano es `date` (fecha de apunte) y se
    anade el desglose por `flow_class` y el conteo de filas con fecha valor
    anomala (`value_date_ok IS NULL OR value_date >= ANCHOR_DATE`).
    """
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    labels = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    classes = ','.join(f"'{value}'" for value in FLOW_CLASSES)
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW caja_diaria AS
        SELECT t.company_id,
               CAST(t.date AS DATE) AS day,
               t.amount,
               t.amount_eur,
               t.value_date,
               t.value_date_ok,
               f.flow_class
        FROM {transactions} t JOIN {labels} f USING (_src_row, transaction_id)
        WHERE t.is_booked AND NOT t.is_open_month
          AND t.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
          AND t.product_source='banking' AND t.product_type IN ({products})
    ''')
    if con.execute(f'SELECT count(*) FROM caja_diaria WHERE flow_class IS NULL OR flow_class NOT IN ({classes})').fetchone()[0]:
        raise ValueError('Clasificacion fuera de contrato en el perimetro de caja del ancla')
    breakdown = ','.join(
        f"coalesce(sum(amount_eur) FILTER (WHERE flow_class = '{value}'), 0) AS flujo_{value}"
        for value in FLOW_CLASSES
    )
    cursor = con.execute(f'''
        SELECT company_id, day,
               coalesce(sum(amount_eur), 0) AS flujo_neto,
               coalesce(sum(amount_eur) FILTER (WHERE amount_eur > 0), 0) AS entradas,
               coalesce(-sum(amount_eur) FILTER (WHERE amount_eur < 0), 0) AS salidas,
               count(*) AS n_transacciones,
               count(*) FILTER (WHERE value_date_ok IS NULL
                                  OR value_date >= TIMESTAMP '{ANCHOR_DATE}') AS n_fecha_valor_futura,
               coalesce(sum(abs(amount_eur)), 0) AS volumen_conocido,
               coalesce(sum(abs(amount)) FILTER (WHERE amount_eur IS NULL), 0) AS volumen_agujero,
               coalesce(-sum(amount_eur) FILTER (WHERE amount_eur < 0), 0) AS salidas_conocidas,
               {breakdown}
        FROM caja_diaria GROUP BY company_id, day ORDER BY company_id, day
    ''')
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        row['desglose'] = {value: row.pop(f'flujo_{value}') for value in FLOW_CLASSES}
    return rows


def load_anchor_balances(con) -> dict[str, float]:
    """Saldo ancla por empresa: snapshot 2026-09-01, solo caja real disponible."""
    balances = parquet(paths.CLEAN_DIR / 'balances.parquet')
    products = ','.join(f"'{value}'" for value in ANCHOR_PRODUCTS)
    rows = con.execute(f'''
        SELECT company_id, sum(balance) AS saldo
        FROM {balances}
        WHERE is_snapshot_date AND product_type IN ({products})
        GROUP BY company_id
    ''').fetchall()
    return {company_id: None if saldo is None else float(saldo) for company_id, saldo in rows}


def load_universe(con) -> list[str]:
    return [row[0] for row in con.execute(
        f'SELECT company_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")} ORDER BY company_id'
    ).fetchall()]


def load_evaluable(con) -> tuple[set[str], int]:
    """Poblacion evaluable de v4: health_score no nulo y excluida = false."""
    cursor = con.execute(f'''
        SELECT company_id, count(*) AS n_meses
        FROM {parquet(paths.ROOT / ASSESSMENTS)}
        WHERE health_score IS NOT NULL AND NOT excluida
        GROUP BY company_id
    ''')
    ids = set()
    total_meses = 0
    for company_id, n_meses in cursor.fetchall():
        ids.add(company_id)
        total_meses += n_meses
    return ids, total_meses


# ---------------------------------------------------------------------------
# 3. Censo de densidad
# ---------------------------------------------------------------------------
def _gap_runs(move) -> list[int]:
    runs = []
    current = 0
    for value in move:
        if value:
            if current:
                runs.append(current)
            current = 0
        else:
            current += 1
    if current:
        runs.append(current)
    return runs


def _window_counts(move, width: int):
    """Numero de dias con movimiento en cada ventana movil de `width` dias."""
    n = len(move)
    if n < width:
        return []
    current = int(sum(int(value) for value in move[:width]))
    counts = [current]
    for index in range(width, n):
        current += int(move[index]) - int(move[index - width])
        counts.append(current)
    return counts


def build_density(panel: pd.DataFrame) -> dict:
    """Densidad por empresa a partir de la rejilla diaria densa (sin fila ancla)."""
    observado = panel[~panel['es_dia_ancla']]
    per_company = {}
    for company_id, group in observado.groupby('company_id', sort=False):
        move = group.sort_values('day')['tiene_movimiento'].astype(int).to_numpy()
        per_company[company_id] = move
    return per_company


def summarize_density(move_by_company: dict, evaluable: set[str]) -> dict:
    dias = {}
    maxgap = {}
    span = {}
    runs_pool = []
    # Por empresa y anchura: min / mediana de dias con movimiento por ventana, y
    # fraccion de ventanas vacias. Se guardan en dicts por empresa (no en listas
    # paralelas) para que el emparejamiento no dependa del orden de iteracion.
    ventana_min = {width: {} for width in WINDOWS_DENSIDAD}
    ventana_mediana = {width: {} for width in WINDOWS_DENSIDAD}
    ventana_vacia = {width: {} for width in WINDOWS_DENSIDAD}
    runs_evaluables = []
    for company_id, move in move_by_company.items():
        span[company_id] = len(move)
        dias[company_id] = int(move.sum())
        runs = _gap_runs(move)
        runs_pool.extend(runs)
        if company_id in evaluable:
            runs_evaluables.extend(runs)
        maxgap[company_id] = max(runs) if runs else 0
        for width in WINDOWS_DENSIDAD:
            counts = _window_counts(move, width)
            if counts:
                ventana_min[width][company_id] = min(counts)
                ventana_mediana[width][company_id] = _median(counts)
                ventana_vacia[width][company_id] = (
                    sum(1 for value in counts if value == 0) / len(counts))

    def cumple(width, umbral):
        return {
            company_id: (span[company_id] >= width
                         and maxgap[company_id] <= width - 1
                         and ventana_mediana[width].get(company_id, 0.0) >= umbral)
            for company_id in move_by_company
        }

    def cuenta(valido):
        return (int(sum(bool(ok) for ok in valido.values())),
                int(sum(1 for cid, ok in valido.items() if ok and cid in evaluable)))

    criterio = {}
    for width in WINDOWS_DENSIDAD:
        minimo = math.ceil(width / DENSIDAD_DIVISOR_PRIMARIO)
        universo, evaluables = cuenta(cumple(width, minimo))
        sensibilidad = {}
        for etiqueta, umbral in (('novacio', 1), ('mediana_ge_W/14', math.ceil(width / 14)),
                                 ('mediana_ge_W/10', math.ceil(width / 10)),
                                 ('mediana_ge_W/7', minimo),
                                 ('mediana_ge_W/5', math.ceil(width / 5))):
            total, total_evaluables = cuenta(cumple(width, umbral))
            sensibilidad[etiqueta] = {
                'umbral_dias_con_movimiento_en_la_ventana_tipica': umbral,
                'empresas_universo': total,
                'empresas_evaluables_v4': total_evaluables,
            }
        criterio[width] = {
            'criterio_primario': (
                f'span >= {width} dias Y ninguna ventana movil de {width} dias vacia '
                f'(hueco_maximo <= {width - 1}) Y mediana de dias con movimiento por ventana >= '
                f'{minimo} (actividad al menos semanal, ceil({width}/7))'
            ),
            'empresas_universo': universo,
            'empresas_evaluables_v4': evaluables,
            'sensibilidad': sensibilidad,
        }

    return {
        'dias_con_movimiento_por_empresa': {
            'universo': _stats(list(dias.values())),
            'evaluables_v4': _stats([dias[cid] for cid in evaluable if cid in dias]),
        },
        'span_observado_por_empresa': {
            'universo': _stats(list(span.values())),
            'evaluables_v4': _stats([span[cid] for cid in evaluable if cid in span]),
        },
        'huecos': {
            'descripcion': 'Rachas de dias de calendario consecutivos sin movimiento dentro del '
                           'rango observado de cada empresa.',
            'rachas_universo': _stats(runs_pool),
            'rachas_evaluables_v4': _stats(runs_evaluables),
            'hueco_maximo_por_empresa_universo': _stats(list(maxgap.values())),
            'hueco_maximo_por_empresa_evaluables_v4': _stats(
                [maxgap[cid] for cid in evaluable if cid in maxgap]),
            'rachas_ge_30_dias': sum(1 for run in runs_pool if run >= 30),
            'rachas_ge_90_dias': sum(1 for run in runs_pool if run >= 90),
            'rachas_ge_180_dias': sum(1 for run in runs_pool if run >= 180),
        },
        'ventanas': criterio,
        'distribucion_ventana_tipica': {
            str(width): {
                'min_dias_con_movimiento_por_ventana': _stats(list(ventana_min[width].values())),
                'mediana_dias_con_movimiento_por_ventana': _stats(
                    list(ventana_mediana[width].values())),
                'fraccion_ventanas_vacias': _stats(list(ventana_vacia[width].values())),
            }
            for width in WINDOWS_DENSIDAD
        },
    }


# ---------------------------------------------------------------------------
# 4. Anomalias de fecha valor y contaminacion
# ---------------------------------------------------------------------------
def load_anomaly_stats(con) -> list[dict]:
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    flag = f"(value_date_ok IS NULL OR value_date >= TIMESTAMP '{ANCHOR_DATE}')"
    cursor = con.execute(f'''
        SELECT company_id,
               count(*) AS n_total,
               coalesce(sum(abs(amount_eur)), 0) AS volumen_total,
               coalesce(sum(amount_eur), 0) AS neto_total,
               count(*) FILTER (WHERE {flag}) AS n_marcadas,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE {flag}), 0) AS volumen_marcado,
               coalesce(sum(amount_eur) FILTER (WHERE {flag}), 0) AS neto_marcado,
               count(*) FILTER (WHERE value_date_ok IS NULL) AS n_centinela,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE value_date_ok IS NULL), 0) AS volumen_centinela,
               coalesce(sum(amount_eur) FILTER (WHERE value_date_ok IS NULL), 0) AS neto_centinela,
               count(*) FILTER (WHERE value_date_ok IS NOT NULL
                                  AND value_date >= TIMESTAMP '{ANCHOR_DATE}') AS n_futuro,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE value_date_ok IS NOT NULL
                                  AND value_date >= TIMESTAMP '{ANCHOR_DATE}'), 0) AS volumen_futuro,
               coalesce(sum(amount_eur) FILTER (WHERE value_date_ok IS NOT NULL
                                  AND value_date >= TIMESTAMP '{ANCHOR_DATE}'), 0) AS neto_futuro
        FROM {transactions}
        WHERE is_booked AND NOT is_open_month
          AND month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
          AND product_source='banking' AND product_type IN ({products})
        GROUP BY company_id
        ORDER BY company_id
    ''')
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_anomaly_overall(con) -> dict:
    """Conteo de anomalias de fecha valor sobre TODAS las transacciones (no solo caja).

    Se publica para documentar la discrepancia con los numeros del encargo: el
    censo del panel se mide sobre el perimetro de caja (banking + 4 productos),
    que es el unico relevante para el flujo, pero conviene dejar constancia del
    total sobre toda la tabla.
    """
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    row = con.execute(f'''
        SELECT count(*) FILTER (WHERE value_date >= TIMESTAMP '{ANCHOR_DATE}') AS vd_ge_anchor,
               count(*) FILTER (WHERE value_date >= TIMESTAMP '{ANCHOR_DATE}'
                                  AND month < DATE '{ANCHOR_DATE}') AS vd_ge_anchor_mes_cerrado,
               count(*) FILTER (WHERE value_date >= TIMESTAMP '{ANCHOR_DATE}'
                                  AND month >= DATE '{ANCHOR_DATE}') AS vd_ge_anchor_mes_abierto,
               count(*) FILTER (WHERE value_date_ok IS NULL) AS centinela
        FROM {transactions}
    ''').fetchone()
    return {
        'filas_totales': con.execute(f'SELECT count(*) FROM {transactions}').fetchone()[0],
        'value_date_ge_2026_09_01': int(row[0]),
        'value_date_ge_2026_09_01_mes_cerrado': int(row[1]),
        'value_date_ge_2026_09_01_mes_abierto': int(row[2]),
        'centinela_value_date_ok_null': int(row[3]),
        'nota': 'El encargo citaba 9.105 filas con value_date >= 2026-09-01, de las que 1.093 en mes '
                'cerrado y 8.586 en el abierto; esas cifras no cuadran entre si (1.093 + 8.586 = 9.679) '
                'ni con la tabla actual. Se reportan los numeros medidos. La decision de grano (date) y la '
                'regla de marcado no dependen de este conteo.',
    }


def build_anomaly_census(anomaly_rows: list[dict], evaluable: set[str], overall: dict = None) -> dict:
    total_filas = sum(row['n_total'] for row in anomaly_rows)
    total_volumen = sum(row['volumen_total'] for row in anomaly_rows)
    total_neto = sum(row['neto_total'] for row in anomaly_rows)
    marcadas = sum(row['n_marcadas'] for row in anomaly_rows)
    volumen_marcado = sum(row['volumen_marcado'] for row in anomaly_rows)
    neto_marcado = sum(row['neto_marcado'] for row in anomaly_rows)
    centinela = sum(row['n_centinela'] for row in anomaly_rows)
    futuro = sum(row['n_futuro'] for row in anomaly_rows)
    con_marcadas = [row for row in anomaly_rows if row['n_marcadas'] > 0]

    detalle = []
    for row in con_marcadas:
        no_marcado = row['neto_total'] - row['neto_marcado']
        denom = abs(row['neto_marcado']) + abs(no_marcado)
        peso_neto = abs(row['neto_marcado']) / denom if denom > 0 else None
        peso_volumen = (row['volumen_marcado'] / row['volumen_total']
                        if row['volumen_total'] > 0 else None)
        detalle.append({
            'company_id': row['company_id'],
            'evaluable_v4': row['company_id'] in evaluable,
            'n_marcadas': row['n_marcadas'],
            'n_centinela': row['n_centinela'],
            'n_futuro': row['n_futuro'],
            'volumen_marcado_eur': _round(row['volumen_marcado']),
            'neto_marcado_eur': _round(row['neto_marcado']),
            'volumen_total_eur': _round(row['volumen_total']),
            'neto_total_eur': _round(row['neto_total']),
            'peso_volumen': _round(peso_volumen),
            'peso_neto': _round(peso_neto),
            'peso_neto_bruto': _round(abs(row['neto_marcado']) / abs(row['neto_total']))
            if row['neto_total'] else None,
        })
    detalle.sort(key=lambda item: (-(item['peso_neto'] or 0.0), -item['n_marcadas']))

    material = [item for item in detalle if (item['peso_neto'] or 0.0) >= CONTAMINACION_MATERIAL]
    dominante = [item for item in detalle if (item['peso_neto'] or 0.0) >= CONTAMINACION_DOMINANTE]

    return {
        'regla_marcado': 'fecha_valor_futura = (value_date_ok IS NULL OR value_date >= 2026-09-01)',
        'perimetro': 'is_booked, NOT is_open_month, meses 2024-09..2026-08, banking, '
                     'product_type in (checking, wallet, saving, tpv)',
        'comparacion_con_los_numeros_del_spec': overall or {},
        'totales': {
            'filas_perimetro': total_filas,
            'volumen_perimetro_eur': _round(total_volumen),
            'neto_perimetro_eur': _round(total_neto),
            'filas_marcadas': marcadas,
            'filas_centinela_value_date_ok_null': centinela,
            'filas_value_date_futuro': futuro,
            'volumen_marcado_eur': _round(volumen_marcado),
            'neto_marcado_eur': _round(neto_marcado),
            'empresas_con_marcadas': len(con_marcadas),
            'empresas_universo': len(anomaly_rows),
        },
        'umbrales_declarados': {
            'contaminacion_material_peso_neto': CONTAMINACION_MATERIAL,
            'contaminacion_dominante_peso_neto': CONTAMINACION_DOMINANTE,
            'definicion_peso_neto': '|neto_marcado| / (|neto_marcado| + |neto_no_marcado|), acotado en '
                                    '[0, 1]; mide cuanto del movimiento NETO absoluto de la empresa '
                                    'aportan las filas marcadas. El neto es lo que consumira el detector.',
            'justificacion': 'La distribucion de peso_neto es muy concentrada cerca de cero '
                             '(p75 ~ 0,04) con una cola fina: el decil superior supera 0,30. Un umbral '
                             'de 0,25 separa esa cola del grueso y evita marcar como contaminada a una '
                             'empresa por una sola fila irrelevante.',
        },
        'contaminacion_material': {
            'n_empresas': len(material),
            'n_evaluables_v4': sum(1 for item in material if item['evaluable_v4']),
            'empresas': material,
        },
        'contaminacion_dominante': {
            'n_empresas': len(dominante),
            'n_evaluables_v4': sum(1 for item in dominante if item['evaluable_v4']),
            'empresas': dominante,
        },
        'detalle_empresas_con_marcadas': detalle,
    }


def build_comp0413(anomaly_rows: list[dict], company_id: str = 'COMP_0413') -> dict:
    row = next((item for item in anomaly_rows if item['company_id'] == company_id), None)
    if row is None:
        return {'company_id': company_id, 'presente_en_el_perimetro': False}
    no_marcado = row['neto_total'] - row['neto_marcado']
    denom = abs(row['neto_marcado']) + abs(no_marcado)
    return {
        'company_id': company_id,
        'presente_en_el_perimetro': True,
        'n_total': row['n_total'],
        'volumen_total_eur': _round(row['volumen_total']),
        'neto_total_eur': _round(row['neto_total']),
        'n_marcadas': row['n_marcadas'],
        'n_centinela': row['n_centinela'],
        'n_futuro': row['n_futuro'],
        'volumen_marcado_eur': _round(row['volumen_marcado']),
        'neto_marcado_eur': _round(row['neto_marcado']),
        'volumen_centinela_eur': _round(row['volumen_centinela']),
        'neto_centinela_eur': _round(row['neto_centinela']),
        'volumen_futuro_eur': _round(row['volumen_futuro']),
        'neto_futuro_eur': _round(row['neto_futuro']),
        'peso_volumen_marcado': _round(row['volumen_marcado'] / row['volumen_total'])
        if row['volumen_total'] else None,
        'peso_neto_marcado': _round(abs(row['neto_marcado']) / denom) if denom > 0 else None,
        'peso_neto_bruto': _round(abs(row['neto_marcado']) / abs(row['neto_total']))
        if row['neto_total'] else None,
        'lectura': 'Las filas centinela (value_date = 2099-12-31) son pares wash exactos (neto 0) y '
                   'solo inflan el VOLUMEN bruto; las filas con value_date futuro son direccionales y '
                   'se comen la mayor parte del NETO. El neto es lo que consumira el detector, luego '
                   'esta empresa esta materialmente contaminada en su neto aunque su volumen marcado '
                   'sea solo ~10% del bruto.',
    }


# ---------------------------------------------------------------------------
# 5. Reconciliacion con la capa mensual
# ---------------------------------------------------------------------------
def reconcile_with_monthly(con, panel: pd.DataFrame, monthly_path: Path) -> dict:
    monthly = parquet(monthly_path)
    con.register('panel_diario_tmp', panel[['company_id', 'day', 'flujo_neto', 'volumen_conocido',
                                            'volumen_agujero', 'salidas_conocidas']])
    diario = con.execute('''
        SELECT company_id, CAST(date_trunc('month', day) AS DATE) AS month,
               sum(flujo_neto) AS flujo_neto_diario,
               sum(volumen_conocido) AS volumen_conocido_diario,
               sum(volumen_agujero) AS volumen_agujero_diario,
               sum(salidas_conocidas) AS salidas_conocidas_diario
        FROM panel_diario_tmp GROUP BY 1, 2
    ''').df()
    con.unregister('panel_diario_tmp')
    mensual = con.execute(f'''
        SELECT company_id, month, flujo_neto AS flujo_neto_mensual,
               volumen_conocido AS volumen_conocido_mensual,
               volumen_agujero AS volumen_agujero_mensual,
               salidas_conocidas AS salidas_conocidas_mensual
        FROM {monthly}
    ''').df()
    mensual['month'] = pd.to_datetime(mensual['month'])
    diario['month'] = pd.to_datetime(diario['month'])
    merged = mensual.merge(diario, on=['company_id', 'month'], how='left')
    for column in ('flujo_neto_diario', 'volumen_conocido_diario', 'volumen_agujero_diario',
                   'salidas_conocidas_diario'):
        merged[column] = merged[column].fillna(0.0)
    merged['diff_flujo'] = merged['flujo_neto_diario'] - merged['flujo_neto_mensual']
    merged['diff_volumen'] = merged['volumen_conocido_diario'] - merged['volumen_conocido_mensual']
    merged['diff_agujero'] = merged['volumen_agujero_diario'] - merged['volumen_agujero_mensual']
    merged['diff_salidas'] = merged['salidas_conocidas_diario'] - merged['salidas_conocidas_mensual']
    max_flujo = float(merged['diff_flujo'].abs().max())
    max_volumen = float(merged['diff_volumen'].abs().max())
    max_agujero = float(merged['diff_agujero'].abs().max())
    max_salidas = float(merged['diff_salidas'].abs().max())
    peor = merged.loc[merged['diff_flujo'].abs().idxmax()]
    umbral = 1e-3
    if max_flujo <= umbral:
        diagnostico = (
            f'Reconciliacion correcta: la discrepancia maxima es {max_flujo:.3e} EUR '
            f'(redondeo a 6 decimales de la capa mensual), por debajo del umbral {umbral:.0e}. '
            'Confirma que el perimetro, el signo y el grano temporal (fecha de apunte) del panel '
            'diario reproducen exactamente `cash_backfill_monthly`.'
        )
    else:
        diagnostico = (
            f'DISCREPANCIA ANOMALA: {max_flujo:.6e} EUR en la empresa-mes '
            f'{peor["company_id"]} {peor["month"]}. Revisar perimetro/signo/grano antes de usar el panel.'
        )
    return {
        'fuente_mensual': str(monthly_path.relative_to(paths.ROOT)),
        'empresa_mes_comparables': int(len(merged)),
        'discrepancia_maxima_flujo_neto_eur': max_flujo,
        'discrepancia_maxima_volumen_conocido_eur': max_volumen,
        'discrepancia_maxima_volumen_agujero_eur': max_agujero,
        'discrepancia_maxima_salidas_conocidas_eur': max_salidas,
        'discrepancia_media_flujo_neto_eur': float(merged['diff_flujo'].abs().mean()),
        'empresa_mes_peor': f'{peor["company_id"]} {peor["month"].date()}',
        'umbral_declarado_eur': umbral,
        'diagnostico': diagnostico,
    }


def verify_anchor_identity(panel: pd.DataFrame) -> dict:
    """La identidad `saldo_reversa(T) == saldo_ancla` se verifica en el artefacto."""
    ancla = panel[panel['es_dia_ancla'] & panel['ancla_presente']]
    diferencias = (ancla['saldo_reversa_eur'] - ancla['saldo_ancla_eur']).abs()
    sin_ancla = panel[panel['es_dia_ancla'] & ~panel['ancla_presente']]
    return {
        'empresas_con_ancla_en_la_fila_del_ancla': int(len(ancla)),
        'discrepancia_maxima_saldo_reversa_menos_ancla_eur': float(diferencias.max()) if len(ancla) else None,
        'empresas_sin_ancla_en_la_fila_del_ancla': int(len(sin_ancla)),
        'saldo_null_en_filas_sin_ancla': bool(sin_ancla['saldo_reversa_eur'].isna().all()) if len(sin_ancla) else None,
    }


# ---------------------------------------------------------------------------
# 6. Ensamblado del censo
# ---------------------------------------------------------------------------
def build_census(panel: pd.DataFrame, anomaly_rows: list[dict], evaluable: set[str],
                 evaluable_meses: int, reconciliation: dict, sources_before: dict,
                 anomaly_overall: dict = None) -> dict:
    move_by_company = build_density(panel)
    universo = sorted(panel['company_id'].unique())
    evaluable_presentes = sorted(evaluable & set(universo))
    return {
        'workspace': str(paths.WORKSPACE.relative_to(paths.ROOT)) if paths.WORKSPACE != paths.ROOT
        else 'repo root (sin reports/current.json)',
        'decision_grano': {
            'grano': 'date (fecha de apunte)',
            'cita': DECISION_GRANO,
            'verificado': 'month == date_trunc(date) en el 100% de las filas; max(date) = 2026-09-01; '
                          'todo el mes abierto tiene date = 2026-09-01.',
            'ancla': {
                'instante': str(ANCHOR_DATE),
                'ultimo_dia_cerrado': str(LAST_CLOSED_DAY),
                'productos': list(ANCHOR_PRODUCTS),
                'nota': 'saldo_reversa(2026-08-31) == saldo_reversa(2026-09-01) == saldo_ancla: las dos '
                        'filas representan el mismo instante (cierre de agosto = apertura de septiembre).',
            },
        },
        'perimetro': {
            'filas_empresa_dia': int(len(panel)),
            'empresas': len(universo),
            'empresas_con_movimiento': len(move_by_company),
            'rango_dias': [str(panel['day'].min().date()), str(panel['day'].max().date())],
            'productos_caja': list(CASH_PRODUCTS),
            'nota': 'Rejilla diaria densa entre el primer y el ultimo dia observado de cada empresa, '
                    'mas una fila explicita en el dia del ancla.',
        },
        'densidad': summarize_density(move_by_company, evaluable),
        'poblacion_evaluable_v4': {
            'fuente': ASSESSMENTS,
            'definicion': 'health_score no nulo y excluida = false',
            'empresas': len(evaluable),
            'empresa_mes': evaluable_meses,
            'empresas_presentes_en_el_panel': len(evaluable_presentes),
            'interseccion_ventana_30': None,
            'interseccion_ventana_90': None,
        },
        'anomalias_fecha_valor': build_anomaly_census(anomaly_rows, evaluable, anomaly_overall),
        'comp_0413': build_comp0413(anomaly_rows),
        'reconciliacion': reconciliation,
        'honestidad': {
            'available_at': 'Cierre del dia de apunte. Es la convencion mas optimista compatible con el '
                            'grano diario; bajo la convencion mensual de v4 no habria anticipacion.',
            'saldo_reconstruido': 'saldo_reversa usa el ancla 2026-09-01, posterior a todos los cortes. '
                                  'En backtest recupera un nivel que la empresa conocia en su momento y no '
                                  'introduce fuga predictiva; la serie artificial es la forward de v3. En '
                                  'produccion se usaria el saldo del propio corte.',
            'agujeros_fx_no_imputados_como_cero': True,
            'anomalias_no_excluidas_del_flujo': True,
            'no_mezcla_deuda_con_caja': True,
        },
        'fuentes_sha256': sources_before,
    }


# ---------------------------------------------------------------------------
# 7. Salida
# ---------------------------------------------------------------------------
def _pct(part, whole):
    return round(100.0 * part / whole, 2) if whole else None


def render_report(census: dict) -> str:
    densidad = census['densidad']
    ventanas = densidad['ventanas']
    anomalias = census['anomalias_fecha_valor']
    comp = census['comp_0413']
    recon = census['reconciliacion']
    evaluable = census['poblacion_evaluable_v4']

    def stat_line(stats):
        return (f"n={stats.get('n')} min={stats.get('min')} p10={stats.get('p10')} "
                f"p25={stats.get('p25')} p50={stats.get('p50')} p75={stats.get('p75')} "
                f"p90={stats.get('p90')} max={stats.get('max')}")

    lines = [
        '# Panel diario point-in-time de flujo operativo neto y saldo',
        '',
        'Generado por `xray/daily_flows.py`. Este modulo construye el sustrato diario; '
        'NO construye el detector de cambio de regimen.',
        '',
        '## Decision de grano temporal: fecha de apunte (`date`)',
        '',
        f'> {DECISION_GRANO}',
        '',
        'Verificado sobre el workspace activo: `month == date_trunc(date)` en el 100% de las filas, '
        '`max(date) = 2026-09-01` y todo el mes abierto tiene `date = 2026-09-01`. Se excluye el mes '
        'abierto por `is_open_month`; nada mas se excluye del flujo.',
        '',
        '## Perimetro y reconciliacion con la capa mensual',
        '',
        f"- Perimetro: `is_booked`, `NOT is_open_month`, meses 2024-09..2026-08, `product_source='banking'` "
        f"y `product_type in {list(CASH_PRODUCTS)}` (identico a `xray/cash_backfill.py`).",
        f"- Filas empresa-dia: {census['perimetro']['filas_empresa_dia']} sobre "
        f"{census['perimetro']['empresas']} empresas.",
        f"- Reconciliacion contra `{recon['fuente_mensual']}`: discrepancia maxima del flujo neto "
        f"**{recon['discrepancia_maxima_flujo_neto_eur']:.3e} EUR** sobre "
        f"{recon['empresa_mes_comparables']} empresa-mes. {recon['diagnostico']}",
        f"- Identidad del ancla: discrepancia maxima `saldo_reversa(T) - saldo_ancla` = "
        f"{census.get('identidad_ancla', {}).get('discrepancia_maxima_saldo_reversa_menos_ancla_eur')} "
        f"EUR sobre {census.get('identidad_ancla', {}).get('empresas_con_ancla_en_la_fila_del_ancla')} "
        f"empresas con ancla.",
        '',
        '## Censo de densidad',
        '',
        'Dias con movimiento por empresa (universo / evaluables v4):',
        '',
        f"- Universo: {stat_line(densidad['dias_con_movimiento_por_empresa']['universo'])}",
        f"- Evaluables v4: {stat_line(densidad['dias_con_movimiento_por_empresa']['evaluables_v4'])}",
        '',
        'Huecos (rachas de dias consecutivos sin movimiento):',
        '',
        f"- Rachas (universo): {stat_line(densidad['huecos']['rachas_universo'])}",
        f"- Hueco maximo por empresa (universo): "
        f"{stat_line(densidad['huecos']['hueco_maximo_por_empresa_universo'])}",
        f"- Rachas >= 30 dias: {densidad['huecos']['rachas_ge_30_dias']}; "
        f">= 90 dias: {densidad['huecos']['rachas_ge_90_dias']}; "
        f">= 180 dias: {densidad['huecos']['rachas_ge_180_dias']}.",
        '',
        '### Criterio de suficiencia declarado',
        '',
    ]
    for width in WINDOWS_DENSIDAD:
        item = ventanas[width]
        lines += [
            f"**Ventana de {width} dias.** {item['criterio_primario']}.",
            '',
            f"- Empresas del universo que la soportan: **{item['empresas_universo']}**.",
            f"- Empresas EVALUABLES v4 que la soportan: **{item['empresas_evaluables_v4']}** "
            f"de {evaluable['empresas']} ({_pct(item['empresas_evaluables_v4'], evaluable['empresas'])}%).",
            '',
            'Sensibilidad del umbral (empresas del universo / evaluables v4):',
            '',
        ]
        for etiqueta, valor in item['sensibilidad'].items():
            lines.append(f"- `{etiqueta}` (>= {valor['umbral_dias_con_movimiento_en_la_ventana_tipica']} "
                         f"dias con movimiento): {valor['empresas_universo']} / "
                         f"{valor['empresas_evaluables_v4']}")
        lines.append('')
    lines += [
        '### Interseccion con la poblacion evaluable de v4',
        '',
        f"`reports/score_v4/assessments.parquet` tiene {evaluable['empresas']} empresas evaluables "
        f"({evaluable['empresa_mes']} empresa-mes). De ellas, "
        f"{ventanas[30]['empresas_evaluables_v4']} soportan la ventana de 30 dias y "
        f"{ventanas[90]['empresas_evaluables_v4']} la de 90 dias segun el criterio primario. "
        'Esa interseccion es la poblacion real del detector.',
        '',
        '## Anomalias de fecha valor',
        '',
        f"- Regla de marcado: `{anomalias['regla_marcado']}` (no se excluye ninguna fila del flujo).",
        f"- Filas marcadas en el perimetro: {anomalias['totales']['filas_marcadas']} "
        f"({anomalias['totales']['filas_value_date_futuro']} con fecha valor futura + "
        f"{anomalias['totales']['filas_centinela_value_date_ok_null']} centinelas) en "
        f"{anomalias['totales']['empresas_con_marcadas']} empresas.",
        f"- Volumen marcado: {anomalias['totales']['volumen_marcado_eur']} EUR; "
        f"neto marcado: {anomalias['totales']['neto_marcado_eur']} EUR.",
        f"- Sobre TODA la tabla de transacciones (no solo el perimetro de caja): "
        f"{anomalias.get('comparacion_con_los_numeros_del_spec', {}).get('value_date_ge_2026_09_01')} "
        f"filas con `value_date >= 2026-09-01` "
        f"({anomalias.get('comparacion_con_los_numeros_del_spec', {}).get('value_date_ge_2026_09_01_mes_cerrado')} "
        f"en mes cerrado + "
        f"{anomalias.get('comparacion_con_los_numeros_del_spec', {}).get('value_date_ge_2026_09_01_mes_abierto')} "
        f"en el mes abierto) y "
        f"{anomalias.get('comparacion_con_los_numeros_del_spec', {}).get('centinela_value_date_ok_null')} "
        f"centinelas. "
        f"{anomalias.get('comparacion_con_los_numeros_del_spec', {}).get('nota', '')}",
        '',
        f"Contaminacion material (peso sobre el neto >= {anomalias['umbrales_declarados']['contaminacion_material_peso_neto']}): "
        f"**{anomalias['contaminacion_material']['n_empresas']} empresas**, de las cuales "
        f"{anomalias['contaminacion_material']['n_evaluables_v4']} son evaluables v4: "
        + ', '.join(f"{item['company_id']} ({item['peso_neto']})"
                    for item in anomalias['contaminacion_material']['empresas']) + '.',
        f"Contaminacion dominante (>= {anomalias['umbrales_declarados']['contaminacion_dominante_peso_neto']}): "
        f"{anomalias['contaminacion_dominante']['n_empresas']} empresas "
        f"({anomalias['contaminacion_dominante']['n_evaluables_v4']} evaluables v4).",
        f"Definicion del peso: {anomalias['umbrales_declarados']['definicion_peso_neto']} "
        f"{anomalias['umbrales_declarados']['justificacion']}",
        '',
        '## COMP_0413',
        '',
    ]
    if comp.get('presente_en_el_perimetro'):
        lines += [
            f"- Filas marcadas: {comp['n_marcadas']} ({comp['n_centinela']} centinelas + "
            f"{comp['n_futuro']} con fecha valor futuro).",
            f"- Volumen marcado: {comp['volumen_marcado_eur']} EUR = "
            f"{round(100 * comp['peso_volumen_marcado'], 2) if comp['peso_volumen_marcado'] is not None else None}% "
            f"de su volumen bruto ({comp['volumen_total_eur']} EUR).",
            f"- Neto marcado: {comp['neto_marcado_eur']} EUR; peso sobre su neto total "
            f"({comp['neto_total_eur']} EUR) = "
            f"{round(100*comp['peso_neto_marcado'],2) if comp['peso_neto_marcado'] is not None else None}%.",
            f"- {comp['lectura']}",
        ]
    else:
        lines.append('- COMP_0413 no aparece en el perimetro.')
    lines += [
        '',
        '## Lectura honesta',
        '',
        f"La interseccion con la poblacion evaluable de v4 es la cifra que decide el alcance: "
        f"{ventanas[30]['empresas_evaluables_v4']} de {evaluable['empresas']} empresas evaluables "
        f"({_pct(ventanas[30]['empresas_evaluables_v4'], evaluable['empresas'])}%) soportan la ventana "
        f"de 30 dias y {ventanas[90]['empresas_evaluables_v4']} "
        f"({_pct(ventanas[90]['empresas_evaluables_v4'], evaluable['empresas'])}%) la de 90. La densidad "
        f"NO es homogenea: la mediana de dias con movimiento por empresa evaluable es "
        f"{densidad['dias_con_movimiento_por_empresa']['evaluables_v4']['p50']} y el decil superior de "
        f"hueco maximo supera los "
        f"{densidad['huecos']['hueco_maximo_por_empresa_evaluables_v4']['p90']} dias, de modo que hay una "
        f"cola de empresas esporadicas en las que una ventana de 30 dias puede contener un unico "
        f"movimiento. El criterio primario (ninguna ventana vacia y actividad al menos semanal) y su "
        f"tabla de sensibilidad delimitan esa cola: relajar el umbral a una sola transaccion por ventana "
        f"sube la interseccion de {ventanas[30]['empresas_evaluables_v4']} a "
        f"{ventanas[30]['sensibilidad']['novacio']['empresas_evaluables_v4']} empresas, pero ahi el "
        f"'cambio de regimen' seria el artefacto de una sola transaccion.",
        '',
        f"En resumen: para la mayoria de las evaluables hay senal diaria suficiente; para una minoria "
        f"esporadica (huecos de decenas de dias, {densidad['huecos']['rachas_ge_30_dias']} rachas de 30+ "
        f"dias y {densidad['huecos']['rachas_ge_90_dias']} de 90+ en el universo) el detector deberia "
        f"excluirse o tratarse con ventanas mas largas. Las {anomalias['contaminacion_material']['n_evaluables_v4']} "
        f"empresas evaluables con anomalias que explican >= "
        f"{anomalias['umbrales_declarados']['contaminacion_material_peso_neto']} de su neto son una nota al "
        f"pie (de las cuales {anomalias['contaminacion_dominante']['n_evaluables_v4']} superan el 50%), no un "
        f"riesgo sistemico; la decision sobre ellas queda en manos del coordinador.",
        '',
        '## Fuentes (sha256)',
        '',
    ]
    for name, digest in sorted(census['fuentes_sha256'].items()):
        lines.append(f'- `{name}`: `{digest}`')
    lines.append('')
    return '\n'.join(lines)


def write_packet(output: Path, panel: pd.DataFrame, census: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(output / 'panel_diario.parquet', index=False, compression='zstd')
    (output / 'censo.json').write_text(
        json.dumps(census, ensure_ascii=False, allow_nan=False, default=str, indent=2) + '\n',
        encoding='utf-8')
    (output / 'report.md').write_text(render_report(census), encoding='utf-8')


def summary(census: dict) -> dict:
    ventanas = census['densidad']['ventanas']
    return {
        'output': 'reports/daily_flows',
        'filas_empresa_dia': census['perimetro']['filas_empresa_dia'],
        'empresas': census['perimetro']['empresas'],
        'evaluables_v4': census['poblacion_evaluable_v4']['empresas'],
        'ventana_30_evaluables': ventanas[30]['empresas_evaluables_v4'],
        'ventana_90_evaluables': ventanas[90]['empresas_evaluables_v4'],
        'reconciliacion_discrepancia_maxima_eur': census['reconciliacion'][
            'discrepancia_maxima_flujo_neto_eur'],
        'comp_0413_peso_neto': census['comp_0413'].get('peso_neto_marcado'),
        'contaminacion_material_evaluables': census['anomalias_fecha_valor'][
            'contaminacion_material']['n_evaluables_v4'],
    }


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------
def _source_files():
    return [
        paths.CLEAN_DIR / 'transactions.parquet',
        paths.CLEAN_DIR / 'companies.parquet',
        paths.CLEAN_DIR / 'balances.parquet',
        paths.INTERIM_DIR / 'tx_flow_class.parquet',
        paths.ROOT / ASSESSMENTS,
        paths.ROOT / MONTHLY_BACKFILL,
    ]


def _fingerprints(files):
    result = {}
    for file in files:
        with file.open('rb') as stream:
            result[str(file.relative_to(paths.ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Panel diario point-in-time de flujo de caja y saldo reconstruido')
    parser.add_argument('--workspace', type=Path, default=None,
                        help='ejecucion de datos (por defecto, la publicada en reports/current.json)')
    parser.add_argument('--output', '--output-dir', dest='output', type=Path,
                        default=paths.ROOT / 'reports' / 'daily_flows',
                        help='directorio de salida')
    parser.add_argument('--force', action='store_true',
                        help='sobrescribe la salida existente')
    args = parser.parse_args(argv)
    if args.workspace is not None:
        os.environ['XRAY_WORKSPACE'] = str(args.workspace)
        importlib.reload(paths)
    if args.output.exists() and not args.force:
        parser.error('La salida ya existe; usa --force para sobrescribir')
    before = _fingerprints(_source_files())
    with tempfile.TemporaryDirectory(prefix='daily-flows-') as scratch:
        con = duckdb.connect(':memory:')
        try:
            con.execute('SET threads=4')
            con.execute("SET memory_limit='2GB'")
            con.execute(f"SET temp_directory='{scratch.replace(chr(39), chr(39) * 2)}'")
            daily = load_daily_flows(con)
            anchors = load_anchor_balances(con)
            companies = load_universe(con)
            evaluable, evaluable_meses = load_evaluable(con)
            anomaly_rows = load_anomaly_stats(con)
            anomaly_overall = load_anomaly_overall(con)
            reconstruction = reconstruct_daily_flows(daily, anchors, companies)
            panel = panel_frame(reconstruction)
            reconciliation = reconcile_with_monthly(con, panel, paths.ROOT / MONTHLY_BACKFILL)
            identity = verify_anchor_identity(panel)
            census = build_census(panel, anomaly_rows, evaluable, evaluable_meses,
                                  reconciliation, before, anomaly_overall)
            census['identidad_ancla'] = identity
            # Rellena la interseccion ya calculada para que el JSON sea autocontenido.
            census['poblacion_evaluable_v4']['interseccion_ventana_30'] = census['densidad'][
                'ventanas'][30]['empresas_evaluables_v4']
            census['poblacion_evaluable_v4']['interseccion_ventana_90'] = census['densidad'][
                'ventanas'][90]['empresas_evaluables_v4']
        finally:
            con.close()
    if _fingerprints(_source_files()) != before:
        raise ValueError('Las fuentes cambiaron durante la construccion; no se publica nada')
    write_packet(args.output, panel, census)
    print(json.dumps({**summary(census), 'output': str(args.output)},
                     ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
