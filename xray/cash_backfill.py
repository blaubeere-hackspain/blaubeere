"""Capa B (v4): reconstruccion de la caja HACIA ATRAS desde un saldo ancla real.

Motivo. `xray/cash_position.py` (v3) reconstruye la posicion de caja acumulando
los flujos hacia DELANTE desde el primer mes observado de cada empresa. Esa serie
asume implicitamente caja inicial cero, asi que cualquier empresa que ya tenia
saldo al empezar la historia aparece con una posicion artificialmente baja: 610
empresas (empresa-ultimo-mes) tienen posicion acumulada negativa POR CONSTRUCCION,
no por realidad. Este modulo NO toca v3 (`xray/cash_position.py` se lee, no se
modifica): reconstruye una serie nueva hacia atras y permite comparar ambas.

Formula (declarada, con T = 2026-09-01):

    saldo_reversa(t) = saldo_ancla(T) - suma de flujos netos de caja en (t, T]

El ancla es el snapshot `clean/balances.parquet` con `is_snapshot_date = true`
(fecha 2026-09-01) y SOLO los productos de caja real disponibles:
`checking`, `wallet`, `saving`, `tpv`. Quedan fuera loan, card, lineofcredit,
confirming, investment, leasing, guarantee, mortgage, renting, risk,
expensesPlatform, factoring, lineofcomex y product_type nulo: son deuda o no son
caja disponible y mezclarlos haria que una linea de credito pareciera colchon.
El importe del ancla es `sum(balance)` de esas filas; ninguna fila del snapshot
con esos cuatro productos tiene `balance` nulo (verificado sobre el workspace).

Convencion temporal (importante). El snapshot de 2026-09-01 es el CIERRE del
ultimo mes cerrado (2026-08-01): en la convencion de point-in-time de
`xray/cash_position.py`, la posicion del mes m incluye ya los flujos del mes m,
es decir, es el estado al cierre de m. Por eso el intervalo `(t, T]` se materializa
como los meses cerrados k con `t < k <= LAST_MONTH` y el ancla corresponde al
cierre de `LAST_MONTH` (2026-08-01). La tabla emite ademas una fila explicita en
`ANCHOR_MONTH = 2026-09-01` con `saldo_reversa = saldo_ancla` (tramo vacio), de
modo que la identidad `saldo_reversa(T) == saldo_ancla` se puede leer literalmente
en el artefacto. El mes abierto 2026-09 se excluye de los flujos: el snapshot es
anterior a la actividad de septiembre.

Criterio de signo de los flujos (duplicado a proposito desde
`xray/cash_position.py`; se declara aqui en vez de importarlo para no acoplar la
capa nueva a un modulo que debe quedar intacto):
- Perimetro: `is_booked`, `NOT is_open_month`, `month` entre FIRST_MONTH y
  LAST_MONTH, `product_source='banking'` y `product_type` en
  `checking`/`wallet`/`saving`/`tpv` (los MISMOS productos que forman el ancla).
- `flujo_neto` = suma de `amount_eur` con su signo (ingresos positivos, salidas
  negativas). Un movimiento sin `amount_eur` (agujero de FX) NO se imputa como
  cero: no se suma y se acumula como `volumen_agujero` (proxy `abs(amount)` en
  moneda local).
- `volumen_conocido` = suma de `abs(amount_eur)`; `salidas_conocidas` = suma de
  `-amount_eur` de los movimientos negativos.
La unica diferencia de perimetro respecto de v3 es `tpv`, que el ancla incluye.

Confidence (criterio determinista declarado; se publica la razon):
- `ninguna`: la empresa no aparece en el snapshot ancla (o su saldo es nulo). En
  ese caso `saldo_reversa_eur` es NULL: no se inventa un saldo.
- Sobre el tramo `(t, T]` se mide
  `hole_ratio = volumen_agujero_tramo / (volumen_conocido_tramo + volumen_agujero_tramo)`.
  Si hay CUALQUIER agujero de FX en el tramo (`volumen_agujero_tramo > 0`), el
  flujo neto del tramo esta incompleto y `saldo_reversa_eur` es NULL (prohibido
  imputar el agujero como cero). La confianza declara la magnitud del agujero:
  - `hole_ratio == 0` -> `alta` (tramo sin agujeros; el nivel es exacto).
  - `0 < hole_ratio <= 0.25` -> `media` (saldo NULL).
  - `0.25 < hole_ratio <= 0.50` -> `baja` (saldo NULL).
  - `hole_ratio > 0.50` -> `ninguna` (saldo NULL).
- `meses_de_cobertura_reversa = colchon_reversa_eur / salida_media_mensual_reversa`,
  con la misma definicion adimensional de v3: `salida_media_mensual_reversa` es la
  media de `salidas_conocidas` de los ultimos `WINDOW_MESES = 6` meses cerrados
  hasta t (ventana trailing point-in-time, no mira el futuro de t) y
  `colchon_reversa_eur = saldo_reversa_eur - min(saldo_reversa_eur hasta t)`, es
  decir el mismo colchon de v3 calculado sobre el nivel reconstruido. Si no hay
  salidas conocidas, la cobertura es NULL con motivo explicito (nunca infinito).
  Nota declarada: como `saldo_reversa(t) = posicion_acumulada_v3(t) + (ancla - F(LAST_MONTH))`
  para los meses en que ambas series existen, el colchon de v3 y el colchon
  reversa coinciden mes a mes (la constante se cancela en la resta); lo que cambia
  de signo es el NIVEL, y por eso el diagnostico de cambio de signo se reporta
  sobre el nivel (ver `coverage.json`).

Declaracion de honestidad. El ancla es de 2026-09-01, posterior a todos los cortes
del dataset (2024-09 .. 2026-08). En backtest esta reconstruccion RECUPERA un nivel
de caja que la empresa si conocia en su momento (su saldo real al cierre), por lo
que NO introduce fuga predictiva: la serie artificial es la forward de v3, que
asume caja inicial cero. En produccion se usaria el saldo del propio corte (el
ultimo saldo conocido a esa fecha) y no un ancla futura. Ambas series se conservan;
esta capa solo MIDE y no entra en el scorer v4.

Alcance: solo lee fuentes y la serie forward publicada de v3 como referencia de
comparacion. No modifica `xray/cash_position.py` ni ningun mart ni dato de `data/`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import sys
import tempfile
from datetime import date
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
ANCHOR_DATE = date(2026, 9, 1)
ANCHOR_MONTH = ANCHOR_DATE  # bucket explicito de la fila ancla
ANCHOR_PRODUCTS = ('checking', 'wallet', 'saving', 'tpv')
BACKFILL_CASH_PRODUCTS = ANCHOR_PRODUCTS
WINDOW_MESES = 6
SIN_ANCLA = 'sin_saldo_en_el_snapshot_ancla'
AGUJEROS_EN_TRAMO = 'agujeros_fx_en_el_tramo'
SIN_SALIDAS = 'sin_salidas_conocidas_en_ventana'
CONFIDENCE_LABELS = ('ninguna', 'baja', 'media', 'alta')
# (limite superior del hole_ratio, etiqueta); solo aplica con ancla presente.
CONFIDENCE_UMBRALES = ((0.0, 'alta'), (0.25, 'media'), (0.50, 'baja'), (math.inf, 'ninguna'))
FLAGS_POSIBLES = ('saldo_negativo', 'agujeros_en_tramo', 'sin_ancla', 'sin_salidas_conocidas')
OUTPUT_COLUMNS = (
    'company_id', 'month', 'flujo_neto',
    *[f'flujo_{value}' for value in FLOW_CLASSES],
    'volumen_conocido', 'volumen_agujero', 'salidas_conocidas',
    'flujo_neto_tramo', 'volumen_conocido_tramo', 'volumen_agujero_tramo',
    'n_meses_tramo', 'ancla_presente', 'saldo_ancla_eur',
    'saldo_reversa_eur', 'saldo_reversa_min_hasta_m', 'colchon_reversa_eur',
    'salida_media_mensual_reversa', 'meses_de_cobertura_reversa',
    'motivo_cobertura', 'confidence', 'flags', 'reason',
)
FORWARD_MONTHLY = 'reports/cash_position/cash_position_monthly.parquet'


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _prev_month(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def _month_range(first: date, last: date) -> list[date]:
    months = []
    month = first
    while month <= last:
        months.append(month)
        month = _next_month(month)
    return months


def _round(value):
    return None if value is None else round(value, 6)


def _confidence(hole_ratio: float, tiene_ancla: bool) -> str:
    if not tiene_ancla:
        return 'ninguna'
    for limit, label in CONFIDENCE_UMBRALES:
        if hole_ratio <= limit:
            return label
    return 'ninguna'


def _validate_row(row: dict) -> None:
    for field in ('company_id', 'month', 'flujo_neto'):
        if row.get(field) is None:
            raise ValueError(f'flujo mensual: falta {field}')
    month = row['month']
    if type(month) is not date or month.day != 1:
        raise ValueError(f'flujo mensual: month debe ser el primer dia del mes: {month!r}')
    for field in ('flujo_neto', 'volumen_conocido', 'volumen_agujero', 'salidas_conocidas'):
        value = row.get(field, 0.0)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'flujo mensual: {field} debe ser un numero finito: {value!r}')
    if row.get('volumen_conocido', 0) < 0 or row.get('volumen_agujero', 0) < 0:
        raise ValueError('flujo mensual: los volumenes no pueden ser negativos')
    if row.get('salidas_conocidas', 0) < 0:
        raise ValueError('flujo mensual: salidas_conocidas no puede ser negativo')
    desglose = row.get('desglose') or {}
    for flow_class, value in desglose.items():
        if flow_class not in FLOW_CLASSES:
            raise ValueError(f'flujo mensual: flow_class fuera de contrato: {flow_class!r}')
        if not math.isfinite(value):
            raise ValueError(f'flujo mensual: desglose no finito: {flow_class}={value!r}')
    total = sum(desglose.values())
    if abs(total - row['flujo_neto']) > 1e-6 + 1e-9 * abs(row['flujo_neto']):
        raise ValueError(f'desglose {total} no cuadra con flujo_neto {row["flujo_neto"]}')


def reconstruct_cash_backfill(monthly_rows, anchor_balances, companies=None,
                              first_month: date = FIRST_MONTH,
                              anchor_month: date = ANCHOR_MONTH):
    """Motor puro: reconstruye la caja hacia atras desde el ancla.

    monthly_rows: iterable de dicts {company_id, month, flujo_neto, desglose,
        volumen_conocido, volumen_agujero, salidas_conocidas} en meses cerrados.
        Criterio de signo y de agujeros: el declarado en la cabecera.
    anchor_balances: {company_id: saldo_ancla_eur}; una empresa ausente (o con
        valor None) produce saldo_reversa NULL y confidence 'ninguna', nunca cero.
    companies: ids del universo; los ausentes de `monthly_rows` y del ancla
        obtienen filas con flujos a cero y saldo NULL declarado.
    first_month/anchor_month: grid temporal. Los meses cerrados son
        [first_month, prev(anchor_month)] y el ancla cierra el ultimo de ellos.

    Devuelve {company_id: {'ancla': float|None, 'reason': ..., 'rows': [...]}}
    sin mutar la entrada.
    """
    closed_last = _prev_month(anchor_month)
    grid = _month_range(first_month, anchor_month)
    closed = grid[:-1]
    closed_index = {month: position for position, month in enumerate(closed)}
    validos = set(closed)

    por_empresa: dict[str, dict[date, dict]] = {}
    for row in monthly_rows:
        copy = dict(row)
        _validate_row(copy)
        if copy['month'] not in validos:
            raise ValueError(f'flujo mensual fuera del tramo cerrado: {copy["month"]!r}')
        por_empresa.setdefault(copy['company_id'], {})[copy['month']] = copy

    result: dict[str, dict] = {}
    ids = sorted(set(por_empresa) | set(companies or ()) | set(anchor_balances))
    for company_id in ids:
        ancla_cruda = anchor_balances.get(company_id)
        if ancla_cruda is not None and (type(ancla_cruda) not in (int, float)
                                        or not math.isfinite(ancla_cruda)):
            raise ValueError(f'ancla no finita para {company_id}: {ancla_cruda!r}')
        ancla = None if ancla_cruda is None else float(ancla_cruda)
        dense = por_empresa.get(company_id, {})

        prefix_flow: dict[date, float] = {}
        prefix_known: dict[date, float] = {}
        prefix_hole: dict[date, float] = {}
        montos: dict[date, dict] = {}
        acumulado = 0.0
        conocido = 0.0
        agujero = 0.0
        for month in closed:
            row = dense.get(month)
            if row is None:
                flujo = 0.0
                desglose = {}
                vol_conocido = 0.0
                vol_agujero = 0.0
                salidas = 0.0
            else:
                flujo = row['flujo_neto']
                desglose = {key: value for key, value in (row.get('desglose') or {}).items() if value}
                vol_conocido = row.get('volumen_conocido', 0.0)
                vol_agujero = row.get('volumen_agujero', 0.0)
                salidas = row.get('salidas_conocidas', 0.0)
            acumulado += flujo
            conocido += vol_conocido
            agujero += vol_agujero
            prefix_flow[month] = acumulado
            prefix_known[month] = conocido
            prefix_hole[month] = agujero
            montos[month] = {'flujo_neto': flujo, 'desglose': desglose,
                             'volumen_conocido': vol_conocido, 'volumen_agujero': vol_agujero,
                             'salidas_conocidas': salidas}
        total_flow = acumulado
        total_known = conocido
        total_hole = agujero

        rows = []
        salidas_ventana: list[float] = []
        running_min = None
        for month in grid:
            if month == anchor_month:
                tramo_flow = 0.0
                tramo_known = 0.0
                tramo_hole = 0.0
                n_tramo = 0
                monto = {'flujo_neto': 0.0, 'desglose': {}, 'volumen_conocido': 0.0,
                         'volumen_agujero': 0.0, 'salidas_conocidas': 0.0}
                salida = 0.0
            else:
                tramo_flow = total_flow - prefix_flow[month]
                tramo_known = total_known - prefix_known[month]
                tramo_hole = total_hole - prefix_hole[month]
                n_tramo = closed_index[closed_last] - closed_index[month]
                monto = montos[month]
                salida = monto['salidas_conocidas']
            salidas_ventana.append(salida)
            ventana = salidas_ventana[-WINDOW_MESES:]
            salida_media = sum(ventana) / len(ventana)
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
                'month': month,
                'flujo_neto': _round(monto['flujo_neto']),
                'desglose': {key: _round(value) for key, value in monto['desglose'].items()},
                'volumen_conocido': _round(monto['volumen_conocido']),
                'volumen_agujero': _round(monto['volumen_agujero']),
                'salidas_conocidas': _round(monto['salidas_conocidas']),
                'flujo_neto_tramo': _round(tramo_flow),
                'volumen_conocido_tramo': _round(tramo_known),
                'volumen_agujero_tramo': _round(tramo_hole),
                'n_meses_tramo': n_tramo,
                'ancla_presente': ancla is not None,
                'saldo_ancla_eur': _round(ancla),
                'saldo_reversa_eur': saldo,
                'saldo_reversa_min_hasta_m': _round(running_min),
                'colchon_reversa_eur': colchon,
                'salida_media_mensual_reversa': _round(salida_media) if salida_media > 0 else None,
                'meses_de_cobertura_reversa': cobertura,
                'motivo_cobertura': motivo_cobertura,
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


def load_monthly_flows(con) -> list[dict]:
    """Agrega el perimetro de caja del ancla a flujos mensuales por empresa.

    Duplicacion declarada del criterio de `xray/cash_position.py` (misma fuente,
    mismo `is_booked`/`NOT is_open_month`, mismo rango de meses, mismo signo y
    mismo tratamiento de agujeros); la unica diferencia es que el perimetro de
    productos es el del ancla, que anade `tpv`.
    """
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    labels = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
    products = ','.join(f"'{value}'" for value in BACKFILL_CASH_PRODUCTS)
    classes = ','.join(f"'{value}'" for value in FLOW_CLASSES)
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW caja_backfill AS
        SELECT t.company_id, t.month, t.amount, t.amount_eur, f.flow_class
        FROM {transactions} t JOIN {labels} f USING (_src_row, transaction_id)
        WHERE t.is_booked AND NOT t.is_open_month
          AND t.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
          AND t.product_source='banking' AND t.product_type IN ({products})
    ''')
    if con.execute(f'SELECT count(*) FROM caja_backfill WHERE flow_class IS NULL OR flow_class NOT IN ({classes})').fetchone()[0]:
        raise ValueError('Clasificacion fuera de contrato en el perimetro de caja del ancla')
    breakdown = ','.join(
        f"coalesce(sum(amount_eur) FILTER (WHERE flow_class = '{value}'), 0) AS flujo_{value}"
        for value in FLOW_CLASSES
    )
    cursor = con.execute(f'''
        SELECT company_id, month,
               coalesce(sum(amount_eur), 0) AS flujo_neto,
               coalesce(sum(abs(amount_eur)), 0) AS volumen_conocido,
               coalesce(sum(abs(amount)) FILTER (WHERE amount_eur IS NULL), 0) AS volumen_agujero,
               coalesce(-sum(amount_eur) FILTER (WHERE amount_eur < 0), 0) AS salidas_conocidas,
               {breakdown}
        FROM caja_backfill GROUP BY company_id, month ORDER BY company_id, month
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
    ancla = {}
    for company_id, saldo in rows:
        ancla[company_id] = None if saldo is None else float(saldo)
    return ancla


def _percentiles(values, p):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return round(ordered[low] * (1 - fraction) + ordered[high] * fraction, 6)


def _stats(values):
    return {'n_no_nulos': len(values), 'p25': _percentiles(values, 25),
            'mediana': _percentiles(values, 50), 'p75': _percentiles(values, 75),
            'p90': _percentiles(values, 90)}


def _pearson(pairs):
    if not pairs:
        return None
    n = len(pairs)
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    var_x = sum((x - mean_x) ** 2 for x, _ in pairs)
    var_y = sum((y - mean_y) ** 2 for _, y in pairs)
    covar = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    if var_x <= 0 or var_y <= 0:
        return None
    return round(covar / math.sqrt(var_x * var_y), 6)


def _load_forward(con, ruta: Path):
    """Serie forward de v3 (solo lectura) para el diagnostico comparativo."""
    if not ruta.exists():
        return None
    cursor = con.execute(f'''
        SELECT company_id, month, posicion_acumulada, colchon, confidence
        FROM {parquet(ruta)}
    ''')
    forward = {}
    for company_id, month, posicion, colchon, confidence in cursor.fetchall():
        forward[(company_id, month)] = {
            'posicion_acumulada': None if posicion is None else float(posicion),
            'colchon': None if colchon is None else float(colchon),
            'confidence': confidence,
        }
    return forward


def build_comparative_diagnostic(con, reconstruction, forward_path: Path) -> dict:
    """Diagnostico comparativo obligatorio contra la serie forward de v3."""
    forward = _load_forward(con, forward_path)
    reverse = {}
    for company_id, record in reconstruction.items():
        for row in record['rows']:
            reverse[(company_id, row['month'])] = row
    if forward is None:
        return {
            'serie_forward': str(forward_path.relative_to(paths.ROOT)),
            'disponible': False,
            'motivo': 'La serie forward de v3 no existe en la ruta publicada; no se puede comparar.',
        }

    pairs = []
    colchon_pairs = []
    cambio = {'forward_negativo_reversa_positivo': 0, 'forward_positivo_reversa_negativo': 0,
              'ambos_negativos': 0, 'ambos_positivos': 0, 'con_cero': 0}
    colchon_cambio = {'forward_negativo_reversa_positivo': 0, 'forward_positivo_reversa_negativo': 0,
                      'ambos_negativos': 0, 'ambos_positivos': 0, 'con_cero': 0}
    for (company_id, month), fwd in forward.items():
        rev = reverse.get((company_id, month))
        if rev is None or rev['saldo_reversa_eur'] is None:
            continue
        posicion = fwd['posicion_acumulada']
        if posicion is None:
            continue
        saldo = rev['saldo_reversa_eur']
        pairs.append((posicion, saldo))
        if posicion < 0 and saldo > 0:
            cambio['forward_negativo_reversa_positivo'] += 1
        elif posicion > 0 and saldo < 0:
            cambio['forward_positivo_reversa_negativo'] += 1
        elif posicion < 0 and saldo < 0:
            cambio['ambos_negativos'] += 1
        elif posicion > 0 and saldo > 0:
            cambio['ambos_positivos'] += 1
        else:
            cambio['con_cero'] += 1
        colchon_fwd = fwd['colchon']
        colchon_rev = rev['colchon_reversa_eur']
        if colchon_fwd is None or colchon_rev is None:
            continue
        colchon_pairs.append((colchon_fwd, colchon_rev))
        if colchon_fwd < 0 and colchon_rev > 0:
            colchon_cambio['forward_negativo_reversa_positivo'] += 1
        elif colchon_fwd > 0 and colchon_rev < 0:
            colchon_cambio['forward_positivo_reversa_negativo'] += 1
        elif colchon_fwd < 0 and colchon_rev < 0:
            colchon_cambio['ambos_negativos'] += 1
        elif colchon_fwd > 0 and colchon_rev > 0:
            colchon_cambio['ambos_positivos'] += 1
        else:
            colchon_cambio['con_cero'] += 1

    diferencias = sorted(saldo - posicion for posicion, saldo in pairs)
    diferencia_mediana = _percentiles(diferencias, 50)
    diferencia_media = round(sum(diferencias) / len(diferencias), 6) if diferencias else None

    # Ultimo mes cerrado comparable por empresa (el ultimo mes presente en forward).
    ultimo_forward = {}
    for (company_id, month), fwd in forward.items():
        if company_id not in ultimo_forward or month > ultimo_forward[company_id][0]:
            ultimo_forward[company_id] = (month, fwd)
    ultimo_cambio = {'forward_negativo_reversa_positivo': 0, 'forward_positivo_reversa_negativo': 0,
                     'empresas_comparadas': 0, 'empresas_recuperadas': 0}
    recuperadas = []
    for company_id, (month, fwd) in ultimo_forward.items():
        rev = reverse.get((company_id, month))
        if rev is None or rev['saldo_reversa_eur'] is None or fwd['posicion_acumulada'] is None:
            continue
        ultimo_cambio['empresas_comparadas'] += 1
        posicion = fwd['posicion_acumulada']
        saldo = rev['saldo_reversa_eur']
        if posicion < 0 and saldo > 0:
            ultimo_cambio['forward_negativo_reversa_positivo'] += 1
            ultimo_cambio['empresas_recuperadas'] += 1
            recuperadas.append(company_id)
        elif posicion > 0 and saldo < 0:
            ultimo_cambio['forward_positivo_reversa_negativo'] += 1

    return {
        'serie_forward': str(forward_path.relative_to(paths.ROOT)),
        'disponible': True,
        'nota': 'Cambio de signo sobre el NIVEL de caja (posicion_acumulada de v3 frente a '
                'saldo_reversa_eur). El `colchon` de v3 (nivel menos minimo historico) es no negativo '
                'por construccion y ademas coincide mes a mes con colchon_reversa (la constante del ancla '
                'se cancela); lo que cambia de signo es el nivel, que es la cifra que justifica la capa.',
        'empresa_mes_comparables': len(pairs),
        'cambio_de_signo_nivel': cambio,
        'cambio_de_signo_nivel_destacado': cambio['forward_negativo_reversa_positivo'],
        'cambio_de_signo_colchon': colchon_cambio,
        'cambio_de_signo_ultimo_mes_por_empresa': ultimo_cambio,
        'ids_empresas_recuperadas_ultimo_mes': sorted(recuperadas),
        'correlacion_pearson_nivel_v3_vs_reversa': _pearson(pairs),
        'diferencia_mediana_reversa_menos_v3_eur': diferencia_mediana,
        'diferencia_media_reversa_menos_v3_eur': diferencia_media,
    }


def build_coverage_report(con, reconstruction, sources_before) -> dict:
    companies = {row[0] for row in con.execute(
        f'SELECT company_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")}'
    ).fetchall()}
    anchor = load_anchor_balances(con)
    ancla_ids = {cid for cid, value in anchor.items() if value is not None}
    sin_ancla = sorted(cid for cid in companies if cid not in ancla_ids)

    rows = [row for record in reconstruction.values() for row in record['rows']]
    cerrados = [row for record in reconstruction.values() for row in record['rows']
                if row['month'] != ANCHOR_MONTH]
    conf_empresa_mes = {label: sum(1 for row in rows if row['confidence'] == label)
                        for label in CONFIDENCE_LABELS}
    ultimo = {cid: record['rows'][-1] for cid, record in reconstruction.items() if record['rows']}
    conf_ultimo = {label: sum(1 for row in ultimo.values() if row['confidence'] == label)
                   for label in CONFIDENCE_LABELS}

    saldos = [row['saldo_reversa_eur'] for row in cerrados if row['saldo_reversa_eur'] is not None]
    coberturas = [row['meses_de_cobertura_reversa'] for row in cerrados
                  if row['meses_de_cobertura_reversa'] is not None]
    saldos_ultimo = [row['saldo_reversa_eur'] for row in ultimo.values()
                     if row['saldo_reversa_eur'] is not None]
    coberturas_ultimo = [row['meses_de_cobertura_reversa'] for row in ultimo.values()
                         if row['meses_de_cobertura_reversa'] is not None]

    sin_saldo = sorted(cid for cid, record in reconstruction.items()
                       if all(row['saldo_reversa_eur'] is None for row in record['rows']))
    agujeros_tramo = sorted(cid for cid, record in reconstruction.items()
                            if record['reason'] == AGUJEROS_EN_TRAMO)

    diagnostic = build_comparative_diagnostic(con, reconstruction, paths.ROOT / FORWARD_MONTHLY)

    return {
        'workspace': str(paths.WORKSPACE.relative_to(paths.ROOT)),
        'ancla': {
            'instant': str(ANCHOR_DATE),
            'mes_equivalente_en_la_grid': str(LAST_MONTH),
            'productos': list(ANCHOR_PRODUCTS),
            'productos_excluidos_motivo': 'loan, card, lineofcredit, confirming, investment, leasing, '
                                          'guarantee, mortgage, renting, risk, expensesPlatform, factoring, '
                                          'lineofcomex y product_type nulo: son deuda o no son caja disponible.',
            'empresas_con_ancla': len(ancla_ids),
            'nota': 'El snapshot de 2026-09-01 es el cierre del ultimo mes cerrado (2026-08-01). '
                    'saldo_reversa(2026-08-01) = saldo_reversa(2026-09-01) = saldo_ancla; las dos filas '
                    'representan el mismo instante (cierre de agosto = apertura de septiembre).',
        },
        'perimetro': {
            'filas_empresa_mes': len(rows),
            'filas_empresa_mes_cerrados': len(cerrados),
            'mes_inicio': str(FIRST_MONTH),
            'mes_fin_cerrado': str(LAST_MONTH),
            'mes_ancla': str(ANCHOR_MONTH),
            'productos_caja': list(BACKFILL_CASH_PRODUCTS),
        },
        'empresas': {
            'universo': len(companies),
            'en_el_ancla': len(ancla_ids),
            'fuera_del_ancla': len(sin_ancla),
            'ids_fuera_del_ancla': sin_ancla,
            'sin_saldo_reversa': len(sin_saldo),
            'sin_saldo_reversa_por_ancla_ausente': len([cid for cid in sin_saldo if cid not in ancla_ids]),
            'sin_saldo_reversa_por_agujeros_fx_en_tramo': len([cid for cid in sin_saldo
                                                               if cid in ancla_ids]),
            'empresa_mes_saldo_reversa_null': sum(1 for row in rows if row['saldo_reversa_eur'] is None),
            'empresa_mes_saldo_reversa_null_por_ancla_ausente':
                sum(1 for row in rows if row['motivo_cobertura'] == SIN_ANCLA),
            'empresa_mes_saldo_reversa_null_por_agujeros_fx_en_tramo':
                sum(1 for row in rows if row['saldo_reversa_eur'] is None
                    and row['motivo_cobertura'] == AGUJEROS_EN_TRAMO),
            'ids_con_agujeros_fx_en_tramo': agujeros_tramo,
        },
        'reparto_confianza': {
            'empresa_mes': conf_empresa_mes,
            'empresa_ultimo_mes': conf_ultimo,
        },
        'saldo_reversa_eur': {
            'empresa_mes_cerrados': _stats(saldos),
            'empresa_ultimo_mes': _stats(saldos_ultimo),
        },
        'meses_de_cobertura_reversa': {
            'empresa_mes_cerrados': _stats(coberturas),
            'empresa_ultimo_mes': _stats(coberturas_ultimo),
        },
        'diagnostico_comparativo_v3': diagnostic,
        'honestidad': {
            'ancla_posterior_a_todos_los_cortes': True,
            'texto': 'El ancla es de 2026-09-01, posterior a todos los cortes (2024-09 .. 2026-08). '
                     'En backtest esta reconstruccion recupera un nivel de caja que la empresa si conocia '
                     'en su momento (su saldo real al cierre), no introduce fuga predictiva; la serie '
                     'forward de v3 es la artificial, porque asume caja inicial cero. En produccion se '
                     'usaria el saldo del propio corte y no un ancla futura. Ambas series se conservan y '
                     'se comparan; esta capa solo mide y no entra en el scorer v4.',
            'agujeros_fx_no_imputados_como_cero': True,
            'no_mezcla_deuda_con_caja': True,
        },
        'fuentes_sha256': sources_before,
    }


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


def write_packet(output: Path, reconstruction, coverage) -> None:
    output.mkdir(parents=True, exist_ok=False)
    frame = pd.DataFrame(list(flat_rows(reconstruction)), columns=list(OUTPUT_COLUMNS))
    frame = frame.sort_values(['company_id', 'month'], kind='stable').reset_index(drop=True)
    frame.to_parquet(output / 'cash_backfill_monthly.parquet', index=False)
    (output / 'coverage.json').write_text(
        json.dumps(coverage, ensure_ascii=False, allow_nan=False, default=str, indent=2) + '\n',
        encoding='utf-8')


def summary(coverage):
    diagnostic = coverage['diagnostico_comparativo_v3']
    return {
        'output': 'reports/cash_backfill',
        'filas_empresa_mes': coverage['perimetro']['filas_empresa_mes'],
        'empresas_en_el_ancla': coverage['empresas']['en_el_ancla'],
        'empresas_fuera_del_ancla': coverage['empresas']['fuera_del_ancla'],
        'empresas_sin_saldo_reversa': coverage['empresas']['sin_saldo_reversa'],
        'reparto_confianza_empresa_ultimo_mes': coverage['reparto_confianza']['empresa_ultimo_mes'],
        'saldo_reversa_ultimo_mes': coverage['saldo_reversa_eur']['empresa_ultimo_mes'],
        'meses_de_cobertura_reversa_ultimo_mes': coverage['meses_de_cobertura_reversa']['empresa_ultimo_mes'],
        'cambio_de_signo_nivel_destacado': diagnostic.get('cambio_de_signo_nivel_destacado')
        if diagnostic.get('disponible') else None,
        'correlacion_nivel_v3_vs_reversa': diagnostic.get('correlacion_pearson_nivel_v3_vs_reversa')
        if diagnostic.get('disponible') else None,
    }


def _source_files():
    sources = [paths.CLEAN_DIR / f'{name}.parquet'
               for name in ('transactions', 'companies', 'banking_products', 'balances')]
    sources += [paths.INTERIM_DIR / f'{name}.parquet' for name in
                ('transactions', 'companies', 'banking_products', 'debt_products', 'debt_schedule_config',
                 'groups', 'invoices', 'tx_flow_class')]
    # targets_proxy se lee en v3 pero aqui esta PROHIBIDO leerlo; no entra en las huellas.
    sources += [paths.MARTS_DIR / f'{name}.parquet' for name in
                ('fx_rates', 'observabilidad', 'panel_cobro', 'panel_deuda', 'panel_evidencia', 'panel_flujos')]
    sources += [paths.ROOT / FORWARD_MONTHLY]
    return sources


def _fingerprints(files):
    result = {}
    for file in files:
        with file.open('rb') as stream:
            result[str(file.relative_to(paths.ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Reconstruccion de la caja hacia atras desde el saldo ancla 2026-09-01')
    parser.add_argument('--workspace', type=Path, default=None,
                        help='ejecucion de datos (por defecto, la publicada en reports/current.json)')
    parser.add_argument('--output', '--output-dir', dest='output', type=Path,
                        default=paths.ROOT / 'reports' / 'cash_backfill',
                        help='directorio de salida (debe no existir para conservar artefactos previos)')
    args = parser.parse_args(argv)
    if args.workspace is not None:
        os.environ['XRAY_WORKSPACE'] = str(args.workspace)
        importlib.reload(paths)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva para conservar artefactos previos')
    before = _fingerprints(_source_files())
    with tempfile.TemporaryDirectory(prefix='cash-backfill-') as scratch:
        con = duckdb.connect(':memory:')
        try:
            con.execute('SET threads=4')
            con.execute("SET memory_limit='2GB'")
            con.execute(f"SET temp_directory='{scratch.replace(chr(39), chr(39) * 2)}'")
            monthly = load_monthly_flows(con)
            anchor_balances = load_anchor_balances(con)
            companies = [row[0] for row in con.execute(
                f'SELECT company_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")} ORDER BY company_id'
            ).fetchall()]
            reconstruction = reconstruct_cash_backfill(monthly, anchor_balances, companies)
            coverage = build_coverage_report(con, reconstruction, before)
        finally:
            con.close()
    if _fingerprints(_source_files()) != before:
        raise ValueError('Las fuentes cambiaron durante la reconstruccion; no se publica nada')
    write_packet(args.output, reconstruction, coverage)
    print(json.dumps({**summary(coverage), 'output': str(args.output)},
                     ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
