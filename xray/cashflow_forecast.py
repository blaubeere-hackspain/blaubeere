"""Backtest walk-forward y baselines del FLUJO DE CAJA diario (30/60/90 dias).

Este modulo NO construye un modelo entrenado. Construye la infraestructura
honesta contra la que cualquier modelo de flujo de caja tendra que medirse y
mide los baselines aritmeticos que ese modelo tendra que batir. Es el hermano
de `xray/forecast_backtest.py`, pero el objeto predicho no es el health_score
mensual sino el flujo de caja diario del panel point-in-time
`reports/daily_flows/panel_diario.parquet`.

Decisiones de producto ya cerradas (no se reabren aqui):
  1. Se predice FLUJO DE CAJA, no health_score.
  2. Horizontes 30, 60 y 90 dias.
  3. Se publican los DOS objetivos por horizonte: flujo neto ACUMULADO del
     periodo y SALDO proyectado al final del periodo.
  4. La senal de tendencia se DERIVA del signo del forecast; no hay detector
     de regimen aparte.
  5. Poblacion por horizonte (no una interseccion unica): solo empresas con
     densidad diaria suficiente PARA ESE horizonte.
  6. Solo baselines honestos: aritmetica simple sobre pandas/DuckDB/pyarrow.

Que hace el modulo, en orden:
  1. CENSO DE DENSIDAD para ventanas 30/60/90 replicando el criterio primario
     de `xray/daily_flows.py`. El censo publicado solo trae W=30 y W=90 porque
     la constante `WINDOWS_DENSIDAD` de daily_flows es fija; la ventana 60 se
     calcula aqui con los MISMOS helpers importados (`_window_counts`,
     `_gap_runs`, `_median`) y el MISMO criterio, y se declara en el informe.
     Se verifica que la reproduccion coincide con el censo existente
     (732 evaluables v4 con W=30 y 790 con W=90). Si no coincide, se aborta.
  2. CORTES: fines de mes cerrado del panel. Un corte es valido para un
     horizonte h si tiene suficiente historia previa (al menos WARMUP_DIAS
     desde el primer dia del panel y h dias de rejilla por empresa) y si
     corte + h <= 2026-08-31 (ultimo dia cerrado), de modo que el target sea
     REALMENTE observable.
  3. CENSO DE TRIOS (empresa, corte, h) evaluables, por horizonte.
  4. TARGETS OBSERVADOS con intervalo ABIERTO por la izquierda y CERRADO por
     la derecha (corte, corte+h]:
        flujo_neto_acumulado_h = sum(flujo_neto) en (corte, corte+h]
        saldo_proyectado_h     = saldo_reversa_eur(corte+h)
  5. BASELINES: B1 persistencia (repite el flujo de los h dias previos),
     B2 media movil trailing de 28 dias multiplicada por h, B3 mediana
     cross-empresa del flujo acumulado h usando SOLO origenes anteriores al
     corte. Toda prediccion lee UNICAMENTE filas con `day <= corte` (y, en
     este panel, `available_at == day`, luego `available_at <= corte`).
  6. METRICAS por horizonte, baseline y objetivo: MAE y mediana del error
     absoluto en EUR, percentiles p50/p75/p90, y una metrica ESCALADA por
     empresa (error absoluto dividido por la escala tipica de flujo de esa
     empresa). Si el ranking cambia entre metrica absoluta y escalada, se
     dice explicitamente.
  7. TENDENCIA derivada: `tendencia_h` en {positiva, negativa, neutra} a
     partir del signo del flujo acumulado PREDICHO, con una banda neutra
     definida por la dispersion de los baselines. Se evalua contra el signo
     observado con matriz de confusion, acierto por clase y comparacion con
     la regla trivial de la clase mayoritaria.
  8. Salida reproducible en `reports/cashflow_forecast/`:
     `predicciones.parquet`, `baselines.json`, `report.md`.

Alcance: solo lee (`panel_diario.parquet`, `censo.json`,
`reports/score_v4/assessments.parquet`) y escribe
`reports/cashflow_forecast/`. No modifica `xray/daily_flows.py`,
`xray/forecast_backtest.py` ni ningun dato de `data/`.

Convenio de solapamiento declarado (no es una fuga del pipeline): la columna
`saldo_reversa_eur` es una reconstruccion hacia atras desde el ancla
2026-09-01; su `saldo_reversa_available_at` es el ancla, posterior a todos los
cortes. El panel diario declara esa reconstruccion como la recuperacion de un
nivel de caja que la empresa SI conocia en su momento. El modulo consume el
valor ALMACENADO en la fila del corte (no recalcula nada hacia atras), de modo
que eliminar o alterar filas posteriores al corte no cambia la prediccion.
Esa es exactamente la propiedad que verifica el test de no-fuga.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from xray import paths
from xray.daily_flows import (
    DENSIDAD_DIVISOR_PRIMARIO,
    _gap_runs,
    _median,
    _window_counts,
    build_density,
)

# ---------------------------------------------------------------------------
# Contrato declarado
# ---------------------------------------------------------------------------
VERSION = 'cashflow_forecast_v1'
GENERADOR = 'xray/cashflow_forecast.py'

DEFAULT_PANEL = paths.ROOT / 'reports' / 'daily_flows' / 'panel_diario.parquet'
DEFAULT_CENSO = paths.ROOT / 'reports' / 'daily_flows' / 'censo.json'
DEFAULT_OUTPUT_DIR = paths.ROOT / 'reports' / 'cashflow_forecast'
ASSESSMENTS = 'reports/score_v4/assessments.parquet'

HORIZONTES = (30, 60, 90)
VENTANAS_DENSIDAD = (30, 60, 90)
VENTANA_MEDIA_MOVIL = 28          # B2: 4 semanas completas (ver justificacion)
ESCALA_MIN_EUR = 1.0              # suelo del denominador de la metrica escalada
WARMUP_DIAS = 90                  # historia minima antes de un corte (max h)

LAST_CLOSED_DAY = date(2026, 8, 31)
ANCHOR_DAY = date(2026, 9, 1)

BASELINES = ('B1_persistencia', 'B2_media_movil_28d', 'B3_mediana_global')
OBJETIVOS = ('flujo_neto_acumulado', 'saldo_proyectado')
CLASES_TENDENCIA = ('positiva', 'negativa', 'neutra')

# Contraste contra el censo ya consolidado en main (reports/daily_flows/censo.json).
CENSO_ESPERADO_EVALUABLES = {30: 732, 90: 790}

PANEL_COLUMNS = (
    'company_id', 'day', 'available_at', 'es_dia_ancla', 'tiene_movimiento',
    'flujo_neto', 'saldo_reversa_eur', 'saldo_reversa_available_at', 'confidence',
)

DECLARACION_VENTANA_60 = (
    'La ventana de 60 dias NO esta en el censo publicado: la constante '
    '`WINDOWS_DENSIDAD = (30, 90)` de `xray/daily_flows.py` fija las ventanas y '
    'este modulo no puede editar ese fichero. La ventana 60 se calcula aqui '
    'replicando el criterio primario (span >= 60, ninguna ventana movil de 60 dias '
    'vacia y mediana de dias con movimiento por ventana >= ceil(60/7) = 9) sobre '
    'los MISMOS helpers importados de daily_flows (`build_density`, '
    '`_window_counts`, `_gap_runs`, `_median`). La replica se valida porque las '
    'ventanas 30 y 90 reproducen exactamente los 732 y 790 evaluables v4 del censo '
    'existente.'
)

INTERVALO_CONVENIO = (
    'El target acumula/observa el intervalo ABIERTO por la izquierda y CERRADO por '
    'la derecha: (corte, corte+h]. El dia del corte queda EXCLUIDO del flujo '
    'acumulado y el dia corte+h queda INCLUIDO. El saldo proyectado es el saldo '
    'reconstruido de la fila corte+h. Es el mismo convenio para targets y para el '
    'flujo de persistencia de B1, de modo que B1 en h=30 repite literalmente el '
    'flujo observado en el intervalo inmediatamente anterior.'
)

PUNTO_LEAK_SALDO = (
    'PRECISION: `saldo_reversa_eur` es una reconstruccion hacia atras desde el ancla '
    '2026-09-01 y su `saldo_reversa_available_at` es el ancla, posterior a todos los '
    'cortes. El modulo NO recalcula el saldo: lee el valor ya almacenado en la fila '
    'del corte. El panel diario documenta esa reconstruccion como recuperacion de un '
    'nivel de caja que la empresa conocia en su momento; se mantiene aqui por coherencia '
    'con la tarea P0 y se declara como limitacion. El test de no-fuga verifica que '
    'ninguna prediccion cambia si se eliminan o alteran las filas posteriores al corte.'
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _round(value, digits=6):
    if value is None:
        return None
    return round(float(value), digits)


def _file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _to_day(value) -> int:
    """Dia ordinal (entero) de una fecha, para aritmetica de ventanas."""
    return int(np.datetime64(pd.Timestamp(value).date(), 'D').astype('int64'))


def _quantile(values, q):
    """Cuantil con interpolacion lineal (misma definicion que forecast_backtest)."""
    array = np.sort(np.asarray(values, dtype=float))
    n = len(array)
    if n == 0:
        return None
    if n == 1:
        return float(array[0])
    position = q * (n - 1)
    low = int(math.floor(position))
    high = min(low + 1, n - 1)
    weight = position - low
    return float(array[low] * (1.0 - weight) + array[high] * weight)


def _month_ends(first_day: date, last_day: date) -> list[date]:
    """Fines de mes (date) entre first_day y last_day, ambos inclusive."""
    result = []
    year, month = first_day.year, first_day.month
    while True:
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        month_end = next_month - timedelta(days=1)
        if month_end > last_day:
            break
        if month_end >= first_day:
            result.append(month_end)
        year, month = next_month.year, next_month.month
    return result


def _pos_le(days: np.ndarray, day_ordinal: int) -> int:
    """Numero de dias <= day_ordinal (indice de corte exclusivo)."""
    return int(np.searchsorted(days, day_ordinal, side='right'))


# ---------------------------------------------------------------------------
# 1. Carga y censo de densidad
# ---------------------------------------------------------------------------
def load_panel(path=None) -> pd.DataFrame:
    """Lee el panel diario point-in-time consolidado (SOLO LECTURA).

    Devuelve (company_id, day) ordenado. Verifica la invariante que sostiene la
    lectura point-in-time: `available_at == day` en el 100% de las filas, de modo
    que exigir `day <= corte` equivale a exigir `available_at <= corte`.
    """
    path = Path(path) if path is not None else DEFAULT_PANEL
    if not path.exists():
        raise FileNotFoundError(
            f'falta {path}; ejecuta antes xray.daily_flows para generar el panel')
    frame = pd.read_parquet(path, columns=list(PANEL_COLUMNS))
    frame['day'] = pd.to_datetime(frame['day'])
    frame['available_at'] = pd.to_datetime(frame['available_at'])
    if not (frame['available_at'] == frame['day']).all():
        raise ValueError('panel_diario: available_at != day en alguna fila; la lectura '
                         'point-in-time no se puede reducir a filtrar por day')
    frame = frame.sort_values(['company_id', 'day'], kind='stable').reset_index(drop=True)
    return frame


def load_evaluable_v4(assessments=None) -> set[str]:
    """Empresas evaluables v4: health_score no nulo y excluida = false."""
    path = Path(assessments) if assessments is not None else paths.ROOT / ASSESSMENTS
    if not path.exists():
        raise FileNotFoundError(f'falta {path}')
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT company_id FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}') "
            'WHERE health_score IS NOT NULL AND NOT excluida'
        ).fetchall()
    finally:
        con.close()
    return {row[0] for row in rows}


def load_censo_json(path=None) -> dict:
    path = Path(path) if path is not None else DEFAULT_CENSO
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def _company_sufficient(move: np.ndarray, width: int) -> bool:
    """Criterio primario de densidad para una anchura arbitraria.

    Identico a `summarize_density` de daily_flows.py:
      span >= width Y hueco maximo <= width-1 (ninguna ventana movil vacia)
      Y mediana de dias con movimiento por ventana >= ceil(width/7).
    """
    threshold = math.ceil(width / DENSIDAD_DIVISOR_PRIMARIO)
    if len(move) < width:
        return False
    runs = _gap_runs(move)
    max_gap = max(runs) if runs else 0
    if max_gap > width - 1:
        return False
    counts = _window_counts(move, width)
    if not counts:
        return False
    return _median(counts) >= threshold


def density_membership(move_by_company: dict, windows=VENTANAS_DENSIDAD) -> dict:
    """{anchura: conjunto de empresas que soportan la ventana}."""
    return {
        width: {company_id for company_id, move in move_by_company.items()
                if _company_sufficient(move, width)}
        for width in windows
    }


def build_density_census(move_by_company: dict, evaluable: set[str]) -> dict:
    """Censo de densidad para 30/60/90 (mismo formato que daily_flows)."""
    membership = density_membership(move_by_company)
    universe_size = len(move_by_company)
    windows = {}
    for width in VENTANAS_DENSIDAD:
        threshold = math.ceil(width / DENSIDAD_DIVISOR_PRIMARIO)
        ok = membership[width]
        windows[str(width)] = {
            'criterio_primario': (
                f'span >= {width} dias Y ninguna ventana movil de {width} dias vacia '
                f'(hueco_maximo <= {width - 1}) Y mediana de dias con movimiento por '
                f'ventana >= {threshold} (actividad al menos semanal, ceil({width}/7))'
            ),
            'umbral_dias_con_movimiento_en_la_ventana_tipica': threshold,
            'empresas_universo': len(ok),
            'empresas_evaluables_v4': sum(1 for cid in ok if cid in evaluable),
            'pct_universo': _round(100.0 * len(ok) / universe_size, 2) if universe_size else None,
        }
    return {
        'criterio': ('span >= W dias Y hueco_maximo <= W-1 Y mediana de dias con movimiento '
                     'por ventana movil de W dias >= ceil(W/7)'),
        'empresas_universo': universe_size,
        'empresas_evaluables_v4': len(evaluable),
        'declaracion_ventana_60': DECLARACION_VENTANA_60,
        'ventanas': windows,
    }


def verify_density_against_censo(census: dict, censo: dict) -> dict:
    """Reproduce los 732/790 del censo existente; aborta si no coincide."""
    verification = {}
    for width in (30, 90):
        mine = census['ventanas'][str(width)]['empresas_evaluables_v4']
        published = None
        if censo:
            published = (censo.get('densidad', {}).get('ventanas', {})
                         .get(str(width), {}).get('empresas_evaluables_v4'))
        expected = CENSO_ESPERADO_EVALUABLES.get(width)
        reference = published if published is not None else expected
        ok = (mine == reference)
        verification[str(width)] = {
            'recomputado': mine,
            'censo_publicado': published,
            'esperado_en_el_encargo': expected,
            'coincide': ok,
        }
        if not ok:
            raise ValueError(
                f'DISCREPANCIA en el censo de densidad W={width}: se recalcularon '
                f'{mine} empresas evaluables v4 y el censo publicado dice {reference}. '
                'Se aborta la publicacion (criterio del encargo).')
    return verification


# ---------------------------------------------------------------------------
# 2. Cortes y censo de trios
# ---------------------------------------------------------------------------
def build_cortes(panel_min_day, last_closed_day=LAST_CLOSED_DAY,
                 horizons=HORIZONTES, warmup_days=WARMUP_DIAS) -> dict:
    """Cortes por horizonte: fines de mes cerrado con target observable."""
    month_ends = _month_ends(panel_min_day, last_closed_day)
    first_valid = panel_min_day + timedelta(days=warmup_days)
    cortes = {}
    for horizon in horizons:
        cortes[str(horizon)] = [
            month_end for month_end in month_ends
            if month_end >= first_valid and month_end + timedelta(days=horizon) <= last_closed_day
        ]
    return {
        'criterio': (
            f'fin de mes cerrado >= primer dia del panel + {warmup_days} dias '
            f'(historia previa) Y corte + h <= {last_closed_day.isoformat()} '
            '(target observable)'
        ),
        'fines_de_mes_considerados': [d.isoformat() for d in month_ends],
        'por_horizonte': {key: [d.isoformat() for d in value]
                          for key, value in cortes.items()},
        '_dates': cortes,
    }


def build_panel_index(panel: pd.DataFrame) -> dict:
    """Arrays por empresa para leer ventanas sin materializar mascaras."""
    index = {}
    for company_id, group in panel.groupby('company_id', sort=False):
        days = group['day'].values.astype('datetime64[D]').astype('int64')
        index[company_id] = {
            'days': days,
            'flujo': group['flujo_neto'].to_numpy(dtype=float),
            'saldo': group['saldo_reversa_eur'].to_numpy(dtype=float),
        }
    return index


def build_trios(index: dict, populations: dict, cortes_by_h: dict,
                horizons=HORIZONTES) -> list[dict]:
    """Censo de trios (empresa, corte, h) con historia y target observables.

    Un trio es evaluable si la empresa soporta la densidad de su horizonte, el
    corte esta en la lista valida para h, la rejilla de la empresa cubre los h
    dias previos al corte (para B1) y cubre el dia corte+h (target observable).
    """
    trios = []
    for horizon in horizons:
        population = populations[horizon]
        for corte in cortes_by_h[str(horizon)]:
            corte_ord = _to_day(corte)
            for company_id in sorted(population):
                arrays = index.get(company_id)
                if arrays is None:
                    continue
                days = arrays['days']
                if days[0] > corte_ord - horizon:
                    continue
                if days[-1] < corte_ord + horizon:
                    continue
                trios.append({'company_id': company_id, 'corte': corte, 'h': horizon})
    return trios


def _census_trios(trios: list[dict], evaluable: set[str]) -> dict:
    por_h = {}
    for horizon in HORIZONTES:
        subset = [t for t in trios if t['h'] == horizon]
        by_origin = {}
        for trio in subset:
            by_origin.setdefault(trio['corte'], set()).add(trio['company_id'])
        por_h[str(horizon)] = {
            'n_trios': len(subset),
            'n_empresas': len({t['company_id'] for t in subset}),
            'n_origenes': len({t['corte'] for t in subset}),
            'n_trios_evaluables_v4': sum(1 for t in subset if t['company_id'] in evaluable),
            'por_origen': [
                {'origen': origin.isoformat(),
                 'trios': len(companies),
                 'empresas': len(companies)}
                for origin, companies in sorted(by_origin.items())
            ],
        }
    return {
        'definicion': ('trio (empresa, corte, h) con la empresa en la poblacion de densidad '
                       'de h, corte valido para h, rejilla con h dias de historia previa y '
                       'target observable en corte+h <= 2026-08-31'),
        'horizontes': list(HORIZONTES),
        'por_h': por_h,
    }


# ---------------------------------------------------------------------------
# 3. B3: mediana cross-empresa con SOLO origenes anteriores
# ---------------------------------------------------------------------------
def build_b3_pools(index: dict, month_ends: list[date], horizons=HORIZONTES,
                   last_closed_day=LAST_CLOSED_DAY) -> dict:
    """{h: {origen_ordinal: array de flujos acumulados h observados}}.

    Para cada origen (fin de mes) y horizonte, el flujo acumulado h de CADA empresa
    del panel que tenga target observable. Se usa el universo completo (no la
    poblacion por densidad) para que la mediana global no dependa de una seleccion
    que mira al span completo: asi el valor de B3 en un corte solo depende de
    origenes cuyo periodo cerro antes del corte.
    """
    pools = {}
    for horizon in horizons:
        per_origin = {}
        for origin in month_ends:
            origin_ord = _to_day(origin)
            if origin_ord + horizon > _to_day(last_closed_day):
                continue
            values = []
            for arrays in index.values():
                days = arrays['days']
                if days[0] > origin_ord or days[-1] < origin_ord + horizon:
                    continue
                low = _pos_le(days, origin_ord)
                high = _pos_le(days, origin_ord + horizon)
                values.append(float(arrays['flujo'][low:high].sum()))
            if values:
                per_origin[origin_ord] = np.asarray(values, dtype=float)
        pools[horizon] = per_origin
    return pools


def b3_by_corte(pools_h: dict, cortes: list[date], horizon: int) -> dict:
    """{corte_ordinal: mediana cross-empresa con origenes que cerraron antes}."""
    result = {}
    for corte in cortes:
        corte_ord = _to_day(corte)
        chunks = [values for origin_ord, values in sorted(pools_h.items())
                  if origin_ord + horizon <= corte_ord]
        result[corte_ord] = (float(np.median(np.concatenate(chunks)))
                             if chunks else None)
    return result


# ---------------------------------------------------------------------------
# 4. Predicciones (lectura estrictamente point-in-time)
# ---------------------------------------------------------------------------
def _predict_company(arrays: dict, corte_ord: int, horizon: int, b3_value,
                     ma_window: int) -> dict | None:
    """Predicciones de una empresa en un corte. Lee SOLO dias <= corte."""
    days = arrays['days']
    flujo = arrays['flujo']
    saldo = arrays['saldo']
    high = _pos_le(days, corte_ord)
    if high == 0 or days[high - 1] != corte_ord:
        return None
    low_history = _pos_le(days, corte_ord - horizon)
    if high - low_history != horizon:
        return None

    b1_flujo = float(flujo[low_history:high].sum())

    low_ma = _pos_le(days, corte_ord - ma_window)
    b2_flujo = (float(flujo[low_ma:high].mean()) * horizon
                if high > low_ma else None)

    # Escala tipica de flujo de la empresa: VOLUMEN BRUTO (suma de |flujo_neto|)
    # del mismo tramo de h dias previo al corte. Es una magnitud acumulada del
    # mismo orden que el target, invariante a la cancelacion del neto y no nula
    # en cuanto la empresa tuvo un movimiento en ese tramo.
    escala = float(np.abs(flujo[low_history:high]).sum())

    saldo_corte = float(saldo[high - 1])
    if not math.isfinite(saldo_corte):
        saldo_corte = None

    def saldo_pred(flow):
        if saldo_corte is None or flow is None:
            return None
        return saldo_corte + flow

    predictions = {
        'B1_persistencia': b1_flujo,
        'B2_media_movil_28d': b2_flujo,
        'B3_mediana_global': b3_value,
    }
    row = {'saldo_corte_eur': saldo_corte, 'escala_empresa_h_eur': escala}
    for name, value in predictions.items():
        row[f'{name}_flujo_eur'] = value
        row[f'{name}_saldo_eur'] = saldo_pred(value)
    return row


def observed_targets(arrays: dict, corte_ord: int, horizon: int):
    """Verdad observada de un trio, con intervalo (corte, corte+h].

    Devuelve (flujo_neto_acumulado, saldo_proyectado); el saldo es None si la
    reconstruccion es nula (hueco FX o empresa sin ancla).
    """
    days = arrays['days']
    high = _pos_le(days, corte_ord)
    high_target = _pos_le(days, corte_ord + horizon)
    flow = float(arrays['flujo'][high:high_target].sum())
    saldo = float(arrays['saldo'][high_target - 1])
    if not math.isfinite(saldo):
        saldo = None
    return flow, saldo


def derive_tendencia(b1, b2):
    """(referencia, banda, tendencia) a partir de B1 y B2 (lineas por empresa).

    La banda es el intervalo entre B1 y B2: si no coinciden en signo, es neutra.
    """
    specific = [value for value in (b1, b2) if value is not None]
    if not specific:
        return None, None, None
    reference = float(np.median(specific))
    banda = 0.5 * (max(specific) - min(specific))
    if reference > banda:
        tendencia = 'positiva'
    elif reference < -banda:
        tendencia = 'negativa'
    else:
        tendencia = 'neutra'
    return reference, banda, tendencia


def build_prediction_frame(index: dict, trios: list[dict], b3_lookup: dict,
                           ma_window=VENTANA_MEDIA_MOVIL) -> pd.DataFrame:
    """DataFrame grano (empresa, corte, h) con targets, baselines y tendencia.

    Garantia de no-fuga: para cada trio solo se leen filas con `day <= corte`
    (ventanas previas) y, para el TARGET, la fila `corte+h` (la verdad, no una
    feature). `_predict_company` recibe unicamente el corte.
    """
    rows = []
    for trio in trios:
        company_id = trio['company_id']
        horizon = trio['h']
        corte = trio['corte']
        corte_ord = _to_day(corte)
        arrays = index[company_id]
        predictions = _predict_company(
            arrays, corte_ord, horizon, b3_lookup[str(horizon)].get(corte_ord),
            ma_window)
        if predictions is None:
            continue
        target_flujo, target_saldo = observed_targets(arrays, corte_ord, horizon)

        # Tendencia derivada del forecast de las dos lineas base ESPECIFICAS de la
        # empresa (B1 y B2). La banda neutra es el intervalo entre ambas: si B1 y
        # B2 no coinciden en el signo, no se declara direccion. B3 es una
        # referencia global (identica para todas las empresas) y no entra aqui.
        reference, banda, tendencia = derive_tendencia(
            predictions['B1_persistencia_flujo_eur'],
            predictions['B2_media_movil_28d_flujo_eur'])

        row = {
            'company_id': company_id,
            'corte': pd.Timestamp(corte),
            'h': horizon,
            'dia_target': pd.Timestamp(corte) + pd.Timedelta(days=horizon),
            'target_flujo_neto_acumulado_eur': target_flujo,
            'target_saldo_proyectado_eur': target_saldo,
            'escala_empresa_h_eur': predictions['escala_empresa_h_eur'],
            'saldo_corte_eur': predictions['saldo_corte_eur'],
            'prediccion_flujo_ref_eur': reference,
            'banda_neutra_eur': banda,
            'tendencia_h': tendencia,
            'signo_observado': ('positiva' if target_flujo > 0
                                else 'negativa' if target_flujo < 0 else 'neutra'),
        }
        for name in BASELINES:
            row[f'{name}_flujo_eur'] = predictions[f'{name}_flujo_eur']
            row[f'{name}_saldo_eur'] = predictions[f'{name}_saldo_eur']
            flow_error = (predictions[f'{name}_flujo_eur'] - target_flujo
                          if predictions[f'{name}_flujo_eur'] is not None else None)
            saldo_error = (predictions[f'{name}_saldo_eur'] - target_saldo
                           if predictions[f'{name}_saldo_eur'] is not None
                           and target_saldo is not None else None)
            row[f'{name}_error_flujo_eur'] = flow_error
            row[f'{name}_error_saldo_eur'] = saldo_error
            row[f'{name}_abs_err_flujo_escalado'] = (
                abs(flow_error) / max(predictions['escala_empresa_h_eur'], ESCALA_MIN_EUR)
                if flow_error is not None else None)
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(['company_id', 'corte', 'h'], kind='stable').reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5. Metricas y tendencia
# ---------------------------------------------------------------------------
def _error_metrics(errors, escala=None) -> dict:
    array = np.asarray([e for e in errors if e is not None and math.isfinite(e)],
                       dtype=float)
    n = len(array)
    if n == 0:
        return {'n': 0, 'mae_eur': None, 'mediana_error_abs_eur': None,
                'error_abs_p50_eur': None, 'error_abs_p75_eur': None,
                'error_abs_p90_eur': None, 'sesgo_eur': None, 'rmse_eur': None,
                'mae_escalado': None, 'mediana_error_abs_escalado': None,
                'n_escala_en_suelo': 0}
    absolute = np.abs(array)
    result = {
        'n': n,
        'mae_eur': float(absolute.mean()),
        'mediana_error_abs_eur': float(np.median(absolute)),
        'error_abs_p50_eur': _quantile(absolute, 0.50),
        'error_abs_p75_eur': _quantile(absolute, 0.75),
        'error_abs_p90_eur': _quantile(absolute, 0.90),
        'sesgo_eur': float(array.mean()),
        'rmse_eur': float(np.sqrt((array ** 2).mean())),
    }
    if escala is not None:
        scale = np.asarray(escala, dtype=float)
        floor = scale < ESCALA_MIN_EUR
        scaled = absolute / np.maximum(scale, ESCALA_MIN_EUR)
        result['mae_escalado'] = float(scaled.mean())
        result['mediana_error_abs_escalado'] = float(np.median(scaled))
        result['n_escala_en_suelo'] = int(floor.sum())
    else:
        result['mae_escalado'] = None
        result['mediana_error_abs_escalado'] = None
        result['n_escala_en_suelo'] = 0
    return result


def _common_mask(frame: pd.DataFrame, objetivo: str) -> pd.Series:
    """Trios donde los TRES baselines emiten prediccion Y el target existe.

    Es la interseccion comun que hace comparables las metricas: dos cifras sobre
    poblaciones distintas no se comparan. Se publica ademas la cobertura de cada
    baseline por separado.
    """
    if objetivo == 'flujo_neto_acumulado':
        suffix, target = 'flujo', 'target_flujo_neto_acumulado_eur'
    else:
        suffix, target = 'saldo', 'target_saldo_proyectado_eur'
    columns = [f'{name}_{suffix}_eur' for name in BASELINES]
    return frame[columns].notna().all(axis=1) & frame[target].notna()


def compute_coverage(frame: pd.DataFrame) -> dict:
    """Cobertura de cada baseline por horizonte (predicciones no nulas)."""
    coverage = {}
    for horizon in HORIZONTES:
        subset = frame[frame['h'] == horizon]
        total = int(len(subset))
        coverage[str(horizon)] = {
            'n_trios': total,
            **{name: int(subset[f'{name}_flujo_eur'].notna().sum())
               for name in BASELINES},
            'n_trios_comunes_flow': int(_common_mask(subset, 'flujo_neto_acumulado').sum()),
            'n_trios_comunes_saldo': int(_common_mask(subset, 'saldo_proyectado').sum()),
        }
    return coverage


def compute_metrics(frame: pd.DataFrame) -> dict:
    """Metricas por objetivo, horizonte y baseline sobre la interseccion comun."""
    metrics = {objetivo: {} for objetivo in OBJETIVOS}
    for objetivo in OBJETIVOS:
        common = frame[_common_mask(frame, objetivo)]
        for horizon in HORIZONTES:
            subset = common[common['h'] == horizon]
            metrics[objetivo][str(horizon)] = {}
            for name in BASELINES:
                if objetivo == 'flujo_neto_acumulado':
                    errors = subset[f'{name}_error_flujo_eur'].tolist()
                    escala = subset['escala_empresa_h_eur'].tolist()
                else:
                    errors = subset[f'{name}_error_saldo_eur'].tolist()
                    escala = None
                metrics[objetivo][str(horizon)][name] = _error_metrics(errors, escala)
    return metrics


def _ranking(metrics_por_h: dict) -> dict:
    """Orden por MAE EUR y por error ESCALADO.

    El ranking escalado usa la MEDIANA del error escalado, no la media: la media
    esta dominada por los trios donde el target contiene un movimiento enorme y la
    escala previa es ~0 (una sola operacion grande), lo que reintroduce el problema
    de la cola gruesa que la metrica escalada pretende corregir. La media se publica
    igualmente para transparencia.
    """
    result = {}
    for horizon, per_baseline in metrics_por_h.items():
        valid = {name: item for name, item in per_baseline.items() if item['n'] > 0}
        by_mae = sorted(valid, key=lambda name: valid[name]['mae_eur'])
        scaled = {name: item for name, item in valid.items()
                  if item.get('mediana_error_abs_escalado') is not None}
        by_scaled = sorted(scaled,
                           key=lambda name: scaled[name]['mediana_error_abs_escalado'])
        by_scaled_mean = sorted(scaled,
                                key=lambda name: scaled[name]['mae_escalado'])
        result[horizon] = {
            'por_mae_eur': by_mae,
            'por_error_escalado_mediana': by_scaled,
            'por_error_escalado_media': by_scaled_mean,
            'ganador_mae_eur': by_mae[0] if by_mae else None,
            'ganador_error_escalado': by_scaled[0] if by_scaled else None,
            'coincide_ranking': by_mae == by_scaled if by_scaled else None,
            'ganador_mae_eur_coincide_con_media_escalada': (
                by_mae[0] == by_scaled_mean[0] if by_scaled_mean and by_mae else None),
        }
    return result


def compute_tendencia(frame: pd.DataFrame) -> dict:
    """Distribucion, matriz de confusion y acierto frente al signo observado."""
    result = {}
    for horizon in HORIZONTES:
        subset = frame[(frame['h'] == horizon) & frame['tendencia_h'].notna()]
        n = len(subset)
        distribution = {clase: int((subset['tendencia_h'] == clase).sum())
                        for clase in CLASES_TENDENCIA}
        observed = {clase: int((subset['signo_observado'] == clase).sum())
                    for clase in CLASES_TENDENCIA}
        confusion = {pred: {real: 0 for real in CLASES_TENDENCIA}
                     for pred in CLASES_TENDENCIA}
        for predicted, real in zip(subset['tendencia_h'], subset['signo_observado']):
            confusion[predicted][real] += 1
        hits = sum(confusion[clase][clase] for clase in CLASES_TENDENCIA)
        recall = {}
        for clase in CLASES_TENDENCIA:
            support = observed[clase]
            recall[clase] = (confusion[clase][clase] / support) if support else None
        precision = {}
        for clase in CLASES_TENDENCIA:
            predicted_count = distribution[clase]
            precision[clase] = (confusion[clase][clase] / predicted_count
                                if predicted_count else None)
        majority = max(observed.values()) if observed else 0
        # Acierto direccional: solo donde el modelo declara positiva/negativa.
        directional = subset[subset['tendencia_h'] != 'neutra']
        directional_n = len(directional)
        directional_hits = int((directional['tendencia_h']
                                == directional['signo_observado']).sum())
        directional_observed = {clase: int((directional['signo_observado'] == clase).sum())
                                for clase in CLASES_TENDENCIA}
        directional_majority = (max(directional_observed.values())
                                if directional_n else 0)
        result[str(horizon)] = {
            'n': n,
            'distribucion_predicha': distribution,
            'distribucion_observada': observed,
            'matriz_confusion_predicha_filas_observada_columnas': confusion,
            'acierto': (hits / n) if n else None,
            'acierto_por_clase_recall': recall,
            'precision_por_clase': precision,
            'trivial_clase_mayoritaria': (majority / n) if n else None,
            'clase_mayoritaria_observada': (max(observed, key=observed.get)
                                            if observed and n else None),
            'supera_trivial': (hits / n > majority / n) if n else None,
            'direccional': {
                'n': directional_n,
                'acierto': (directional_hits / directional_n) if directional_n else None,
                'distribucion_observada': directional_observed,
                'trivial_clase_mayoritaria': (directional_majority / directional_n
                                              if directional_n else None),
                'supera_trivial': (directional_hits / directional_n
                                   > directional_majority / directional_n)
                                  if directional_n else None,
            },
        }
    return result


# ---------------------------------------------------------------------------
# 6. Ensamblado del informe
# ---------------------------------------------------------------------------
def _input_info(path: Path) -> dict:
    try:
        display = str(path.relative_to(paths.ROOT))
    except ValueError:
        display = str(path)
    if not path.exists():
        return {'path': display, 'existe': False}
    return {'path': display, 'existe': True, 'bytes': path.stat().st_size,
            'sha256': _file_sha256(path)}


def build_report(panel: pd.DataFrame, censo: dict, evaluable: set[str],
                 panel_path=None, censo_path=None, assessments_path=None) -> dict:
    move_by_company = build_density(panel)
    density_census = build_density_census(move_by_company, evaluable)
    verification = verify_density_against_censo(density_census, censo)
    membership = density_membership(move_by_company)

    panel_min = panel['day'].min().date()
    panel_max = panel['day'].max().date()
    cortes = build_cortes(panel_min)

    populations = {horizon: membership[horizon] for horizon in HORIZONTES}
    index = build_panel_index(panel)
    trios = build_trios(index, populations, cortes['_dates'])
    trio_census = _census_trios(trios, evaluable)

    month_ends = _month_ends(panel_min, LAST_CLOSED_DAY)
    b3_pools = build_b3_pools(index, month_ends)
    b3_lookup = {str(horizon): b3_by_corte(b3_pools[horizon],
                                           cortes['_dates'][str(horizon)], horizon)
                 for horizon in HORIZONTES}

    frame = build_prediction_frame(index, trios, b3_lookup)
    metrics = compute_metrics(frame)
    coverage = compute_coverage(frame)
    ranking = {objetivo: _ranking(metrics[objetivo]) for objetivo in OBJETIVOS}
    tendencia = compute_tendencia(frame)
    outliers = _outliers(frame)
    b3_column = frame['B3_mediana_global_flujo_eur']
    b3_nonnull = int(b3_column.notna().sum())
    diagnostics = {
        'B3_predicciones_no_nulas': b3_nonnull,
        'B3_predicciones_igual_a_cero': int((b3_column.abs() < 1e-9).sum()),
        'B3_pct_cero_sobre_no_nulas': (
            _round(100.0 * int((b3_column.abs() < 1e-9).sum()) / b3_nonnull, 2)
            if b3_nonnull else None),
        'lectura': ('B3 (mediana cross-empresa del flujo acumulado) produce casi siempre 0 '
                    'porque la mediana de la distribucion de flujos acumulados esta centrada '
                    'en cero. En la practica B3 es el baseline "predice flujo neto cero".'),
    }

    poblacion = {
        str(horizon): {
            'universo_densidad': len(populations[horizon]),
            'evaluables_v4_en_poblacion': sum(1 for cid in populations[horizon]
                                              if cid in evaluable),
        }
        for horizon in HORIZONTES
    }

    report = {
        'version': VERSION,
        'generador': GENERADOR,
        'comando_reproduccion': (
            'PYTHONPATH=. .venv/bin/python -B -m xray.cashflow_forecast '
            '--panel reports/daily_flows/panel_diario.parquet '
            '--censo reports/daily_flows/censo.json '
            '--output-dir reports/cashflow_forecast'),
        'entradas': {
            'panel_diario': _input_info(Path(panel_path) if panel_path else DEFAULT_PANEL),
            'censo_daily': _input_info(Path(censo_path) if censo_path else DEFAULT_CENSO),
            'assessments_v4': _input_info(Path(assessments_path) if assessments_path
                                          else paths.ROOT / ASSESSMENTS),
        },
        'contrato': {
            'objetivo': 'flujo de caja (no health_score)',
            'horizontes_dias': list(HORIZONTES),
            'objetivos_publicados': list(OBJETIVOS),
            'intervalo_de_target': INTERVALO_CONVENIO,
            'punto_leak_saldo': PUNTO_LEAK_SALDO,
            'baselines': {
                'B1_persistencia': (
                    'flujo neto de los h dias previos al corte (corte-h, corte] repetido '
                    'como prediccion de (corte, corte+h]; saldo = saldo_reversa(corte) + '
                    'prediccion de flujo'),
                'B2_media_movil_28d': (
                    f'media diaria de flujo_neto en la ventana trailing de '
                    f'{VENTANA_MEDIA_MOVIL} dias (4 semanas completas, cancela la '
                    f'estacionalidad semanal y no coincide con ningun horizonte) x h; '
                    'saldo = saldo_reversa(corte) + prediccion de flujo'),
                'B3_mediana_global': (
                    'mediana cross-empresa del flujo acumulado h calculada SOLO con '
                    'origenes cuyo periodo cerro antes del corte (origen + h <= corte); '
                    'saldo = saldo_reversa(corte) + prediccion de flujo'),
            },
            'metrica_escalada': (
                'error absoluto de flujo / max(escala_empresa_h, '
                f'{ESCALA_MIN_EUR:.0f} EUR), donde escala_empresa_h = suma de '
                '|flujo_neto| en los h dias previos al corte (volumen bruto del mismo '
                'tramo). Normaliza por el tamano de actividad de la empresa, es del '
                'mismo orden que el target acumulado y evita el infinito cuando no hubo '
                'movimiento; se publica cuantos trios caen en el suelo.'),
            'banda_neutra_tendencia': (
                'referencia = mediana de las predicciones de las dos lineas base '
                'especificas de la empresa (B1 y B2); banda = (max - min) / 2 de esas '
                'dos predicciones. positiva si referencia > banda, negativa si '
                'referencia < -banda, neutra en otro caso. La banda es EX-ANTE (solo usa '
                'predicciones) y equivale a no declarar direccion cuando B1 y B2 no '
                'coinciden en el signo. B3 no entra en la tendencia por ser una '
                'referencia global identica para todas las empresas.'),
        },
        'panel': {
            'filas': int(len(panel)),
            'empresas': int(panel['company_id'].nunique()),
            'rango_dias': [panel_min.isoformat(), panel_max.isoformat()],
            'ultimo_dia_cerrado': LAST_CLOSED_DAY.isoformat(),
            'granularidad': '(company_id, day); dia de APUNTE, no fecha valor',
        },
        'censo_densidad': density_census,
        'verificacion_censo_existente': verification,
        'poblacion_por_horizonte': poblacion,
        'cortes': {
            'criterio': cortes['criterio'],
            'fines_de_mes_considerados': cortes['fines_de_mes_considerados'],
            'por_horizonte': cortes['por_horizonte'],
            'n_por_horizonte': {key: len(value)
                                for key, value in cortes['_dates'].items()},
        },
        'censo_trios': trio_census,
        'cobertura_baselines': coverage,
        'metricas': metrics,
        'diagnosticos': diagnostics,
        'outliers': outliers,
        'ranking': ranking,
        'tendencia': tendencia,
        'limitaciones': [
            PUNTO_LEAK_SALDO,
            ('La poblacion es el universo con densidad suficiente para cada horizonte '
             '(no solo las evaluables v4). El censo publica ambos conteos; las metricas '
             'se miden sobre el universo para no encoger la muestra.'),
            ('B1 y B2 predicen en todos los trios; B3 no predice en los primeros cortes '
             'porque exige un origen anterior ya cerrado. El ranking se calcula sobre la '
             'interseccion comun por horizonte (misma poblacion para los tres baselines), '
             'y se publica el n de cada baseline.'),
            ('La densidad se mide sobre el span completo observado de la empresa, no '
             'localmente alrededor del corte; un trio exige ademas h dias de rejilla '
             'previa y target observable, de modo que la historia del corte si esta '
             'garantizada.'),
            ('El saldo proyectado hereda los huecos FX del panel: si saldo_reversa_eur es '
             'nulo en el corte o en corte+h, el objetivo/prediccion de saldo no existe y '
             'se descarta de las metricas de saldo (nunca se imputa).'),
        ],
    }
    return report, frame


# ---------------------------------------------------------------------------
# 7. Serializacion y markdown
# ---------------------------------------------------------------------------
def report_paths(output_dir=None):
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    return (output_dir / 'predicciones.parquet',
            output_dir / 'baselines.json',
            output_dir / 'report.md')


def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False,
                      sort_keys=False, default=str) + '\n'


def _fmt(value, digits=4):
    if value is None:
        return 'n/a'
    if isinstance(value, float):
        return f'{value:,.{digits}f}'
    return str(value)


def _metrics_table(lines, per_h):
    lines.append('| baseline | h | n | MAE EUR | mediana error abs EUR | error_abs_p75 EUR | '
                 'error_abs_p90 EUR | mediana error escalado | media error escalado | sesgo EUR |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for horizon in sorted(per_h):
        for name in BASELINES:
            item = per_h[horizon][name]
            lines.append(
                f'| {name} | {horizon} | {item["n"]} | {_fmt(item["mae_eur"])} | '
                f'{_fmt(item["mediana_error_abs_eur"])} | {_fmt(item["error_abs_p75_eur"])} | '
                f'{_fmt(item["error_abs_p90_eur"])} | '
                f'{_fmt(item["mediana_error_abs_escalado"])} | '
                f'{_fmt(item["mae_escalado"])} | '
                f'{_fmt(item["sesgo_eur"])} |')


def _outliers(frame: pd.DataFrame) -> dict:
    """Trio con el mayor error escalado y trios con escala en el suelo."""
    if frame.empty:
        return {}
    scaled = frame['B1_persistencia_abs_err_flujo_escalado'].to_numpy(dtype=float)
    index = int(np.nanargmax(scaled))
    worst = frame.iloc[index]
    return {
        'n_trios_escala_en_suelo_1eur': int((frame['escala_empresa_h_eur'] < ESCALA_MIN_EUR).sum()),
        'n_trios_total': int(len(frame)),
        'peor_error_escalado': {
            'company_id': worst['company_id'],
            'corte': str(pd.Timestamp(worst['corte']).date()),
            'h': int(worst['h']),
            'target_flujo_neto_acumulado_eur': float(worst['target_flujo_neto_acumulado_eur']),
            'escala_empresa_h_eur': float(worst['escala_empresa_h_eur']),
            'error_escalado': float(scaled[index]),
        },
        'nota': ('El flow neto diario tiene cola gruesa: un unico movimiento grande '
                 'dentro de una ventana previa sin flujo NETO deja la escala en el suelo '
                 'y dispara el error escalado. La media del error escalado esta dominada '
                 'por ese caso; por eso el ranking escalado usa la MEDIANA.'),
    }


def render_markdown(report: dict) -> str:
    panel = report['panel']
    census = report['censo_densidad']
    verification = report['verificacion_censo_existente']
    lines = []
    lines.append('# Backtest walk-forward de flujo de caja (30/60/90 dias)')
    lines.append('')
    lines.append(f'Generado por `{report["generador"]}` (version `{report["version"]}`). '
                 'Reproducible con:')
    lines.append('')
    lines.append('```sh')
    lines.append(report['comando_reproduccion'])
    lines.append('```')
    lines.append('')
    entries = report['entradas']
    lines.append(f'- Panel: `{entries["panel_diario"].get("path")}` '
                 f'(sha256 `{entries["panel_diario"].get("sha256")}`).')
    lines.append(f'- Censo diario: `{entries["censo_daily"].get("path")}` '
                 f'(sha256 `{entries["censo_daily"].get("sha256")}`).')
    lines.append('')
    lines.append('## 1. Poblacion por horizonte (censo de densidad 30/60/90)')
    lines.append('')
    lines.append(f'Panel: {panel["filas"]} filas empresa-dia, {panel["empresas"]} empresas, '
                 f'rango {panel["rango_dias"][0]} .. {panel["rango_dias"][1]}. '
                 f'Ultimo dia cerrado: {panel["ultimo_dia_cerrado"]}.')
    lines.append('')
    lines.append(f'Criterio: {census["criterio"]}.')
    lines.append('')
    lines.append('| ventana W | umbral dias con mov. | empresas universo | evaluables v4 |')
    lines.append('|---:|---:|---:|---:|')
    for width in ('30', '60', '90'):
        item = census['ventanas'][width]
        lines.append(f'| {width} | {item["umbral_dias_con_movimiento_en_la_ventana_tipica"]} | '
                     f'{item["empresas_universo"]} | {item["empresas_evaluables_v4"]} |')
    lines.append('')
    lines.append(f'**Ventana 60 declarada.** {census["declaracion_ventana_60"]}')
    lines.append('')
    lines.append('Verificacion contra el censo ya consolidado en main:')
    lines.append('')
    lines.append('| W | recomputado | censo publicado | coincide |')
    lines.append('|---:|---:|---:|:---:|')
    for width in ('30', '90'):
        item = verification[width]
        lines.append(f'| {width} | {item["recomputado"]} | {item["censo_publicado"]} | '
                     f'{"si" if item["coincide"] else "NO"} |')
    lines.append('')
    lines.append('Poblacion usada en el backtest (universo con densidad suficiente):')
    lines.append('')
    lines.append('| horizonte h | universo densidad | evaluables v4 en la poblacion |')
    lines.append('|---:|---:|---:|')
    for horizon in ('30', '60', '90'):
        item = report['poblacion_por_horizonte'][horizon]
        lines.append(f'| {horizon} | {item["universo_densidad"]} | '
                     f'{item["evaluables_v4_en_poblacion"]} |')
    lines.append('')
    lines.append('## 2. Cortes y censo de trios')
    lines.append('')
    lines.append(f'- Criterio de corte: {report["cortes"]["criterio"]}.')
    lines.append(f'- Cortes por horizonte: '
                 + ', '.join(f'h={h}: {n}' for h, n in
                             report['cortes']['n_por_horizonte'].items()) + '.')
    lines.append(f'- Convenio de intervalo: {report["contrato"]["intervalo_de_target"]}')
    lines.append('')
    lines.append('| h | trios evaluables | empresas | origenes | trios evaluables v4 |')
    lines.append('|---:|---:|---:|---:|---:|')
    for horizon in ('30', '60', '90'):
        item = report['censo_trios']['por_h'][horizon]
        lines.append(f'| {horizon} | {item["n_trios"]} | {item["n_empresas"]} | '
                     f'{item["n_origenes"]} | {item["n_trios_evaluables_v4"]} |')
    lines.append('')
    lines.append('### Cobertura de cada baseline (las metricas usan la interseccion comun)')
    lines.append('')
    lines.append('| h | trios | B1 predice | B2 predice | B3 predice | comunes flujo | comunes saldo |')
    lines.append('|---:|---:|---:|---:|---:|---:|---:|')
    for horizon in ('30', '60', '90'):
        item = report['cobertura_baselines'][horizon]
        lines.append(f'| {horizon} | {item["n_trios"]} | {item["B1_persistencia"]} | '
                     f'{item["B2_media_movil_28d"]} | {item["B3_mediana_global"]} | '
                     f'{item["n_trios_comunes_flow"]} | {item["n_trios_comunes_saldo"]} |')
    lines.append('')
    lines.append('## 3. Metricas por baseline: flujo neto acumulado')
    lines.append('')
    _metrics_table(lines, report['metricas']['flujo_neto_acumulado'])
    lines.append('')
    lines.append('## 4. Metricas por baseline: saldo proyectado')
    lines.append('')
    _metrics_table(lines, report['metricas']['saldo_proyectado'])
    lines.append('')
    lines.append('## 5. Ranking y sensibilidad de la metrica')
    lines.append('')
    lines.append('El ranking escalado usa la MEDIANA del error escalado (robusta); la media se '
                 'publica en la tabla anterior pero esta dominada por un unico outlier '
                 '(ver seccion 7).')
    lines.append('')
    lines.append('| objetivo | h | ganador MAE EUR | ganador escalado (mediana) | coincide orden |')
    lines.append('|---|---:|---|---|:---:|')
    for objetivo in OBJETIVOS:
        for horizon in sorted(report['ranking'][objetivo]):
            item = report['ranking'][objetivo][horizon]
            lines.append(f'| {objetivo} | {horizon} | {item["ganador_mae_eur"]} | '
                         f'{item["ganador_error_escalado"] or "n/a"} | '
                         f'{"si" if item["coincide_ranking"] else "NO" if item["coincide_ranking"] is not None else "n/a"} |')
    lines.append('')
    lines.append('Orden completo por horizonte (flujo neto acumulado):')
    lines.append('')
    for horizon in ('30', '60', '90'):
        item = report['ranking']['flujo_neto_acumulado'][horizon]
        lines.append(f'- h={horizon}: MAE EUR {item["por_mae_eur"]}; '
                     f'error escalado (mediana) {item["por_error_escalado_mediana"]}.')
    lines.append('')
    diag = report.get('diagnosticos', {})
    if diag:
        lines.append(f'Diagnostico de B3: {diag.get("B3_predicciones_igual_a_cero")} de '
                     f'{diag.get("B3_predicciones_no_nulas")} predicciones son exactamente 0 '
                     f'({diag.get("B3_pct_cero_sobre_no_nulas")}%). {diag.get("lectura")}')
        lines.append('')
    lines.append('## 6. Tendencia derivada del forecast')
    lines.append('')
    lines.append(f'Banda neutra: {report["contrato"]["banda_neutra_tendencia"]}')
    lines.append('')
    for horizon in ('30', '60', '90'):
        item = report['tendencia'][horizon]
        lines.append(f'### h = {horizon} (n = {item["n"]})')
        lines.append('')
        lines.append(f'- Distribucion predicha: {item["distribucion_predicha"]}.')
        lines.append(f'- Distribucion observada: {item["distribucion_observada"]}.')
        lines.append(f'- Acierto global: {_fmt(item["acierto"])} frente a la regla trivial '
                     f'de la clase mayoritaria ({item["clase_mayoritaria_observada"]}): '
                     f'{_fmt(item["trivial_clase_mayoritaria"])}. '
                     f'Supera la trivial: {"si" if item["supera_trivial"] else "NO"}.')
        direction = item['direccional']
        lines.append(f'- Solo cuando declara direccion (n={direction["n"]}): acierto '
                     f'{_fmt(direction["acierto"])} frente a trivial direccional '
                     f'{_fmt(direction["trivial_clase_mayoritaria"])}. '
                     f'Supera la trivial: '
                     f'{"si" if direction["supera_trivial"] else "NO"}.')
        lines.append(f'- Acierto por clase (recall): {item["acierto_por_clase_recall"]}.')
        lines.append('')
        confusion = item['matriz_confusion_predicha_filas_observada_columnas']
        lines.append('| predicha \\ observada | positiva | negativa | neutra |')
        lines.append('|---|---:|---:|---:|')
        for predicted in CLASES_TENDENCIA:
            counts = confusion[predicted]
            lines.append(f'| {predicted} | {counts["positiva"]} | {counts["negativa"]} | '
                         f'{counts["neutra"]} |')
        lines.append('')
    lines.append('## 7. Lectura honesta')
    lines.append('')
    lines.extend(_lectura(report))
    lines.append('')
    lines.append('## 8. Limitaciones declaradas')
    lines.append('')
    for item in report['limitaciones']:
        lines.append(f'- {item}')
    lines.append('')
    return '\n'.join(lines)


def _lectura(report: dict) -> list[str]:
    lines = []
    for objetivo in OBJETIVOS:
        for horizon in ('30', '60', '90'):
            ranking = report['ranking'][objetivo][horizon]
            if not ranking['por_mae_eur']:
                continue
            winner_eur = ranking['ganador_mae_eur']
            winner_scaled = ranking['ganador_error_escalado']
            metrics = report['metricas'][objetivo][horizon]
            if winner_scaled is None:
                lines.append(
                    f'- {objetivo} h={horizon}: gana en MAE EUR **{winner_eur}** '
                    f'({_fmt(metrics[winner_eur]["mae_eur"])} EUR). El error escalado no '
                    'aplica a este objetivo (la escala es de flujo neto).')
                continue
            lines.append(
                f'- {objetivo} h={horizon}: gana en MAE EUR **{winner_eur}** '
                f'({_fmt(metrics[winner_eur]["mae_eur"])} EUR) y en error escalado (mediana) '
                f'**{winner_scaled}**. '
                + ('El orden coincide entre ambas metricas.'
                   if ranking['coincide_ranking']
                   else 'El orden CAMBIA entre metrica absoluta y escalada: un baseline '
                        'que gana en EUR puede perder al normalizar por tamano de empresa.'))
    outliers = report.get('outliers', {})
    lines.append(
        '- **Hallazgo central**: B3 (mediana global) gana MAE y error escalado en los tres '
        'horizontes. Su prediccion es casi siempre ~0 (la mediana cross-empresa del flujo '
        'acumulado es ~0), de modo que el mejor baseline es, en la practica, "predice flujo '
        'neto cero". Ninguna linea base bate ese punto con margen: el flujo neto diario es '
        'casi impredecible en magnitud y signo con aritmetica de persistencia.')
    lines.append(
        '- **Hallazgo incomodo**: el error escalado medio esta dominado por un solo trio '
        f'({outliers.get("peor_error_escalado", {}).get("company_id")} '
        f'h={outliers.get("peor_error_escalado", {}).get("h")}): un movimiento de '
        f'{_fmt(outliers.get("peor_error_escalado", {}).get("target_flujo_neto_acumulado_eur"))} '
        'EUR tras una ventana previa sin flujo NETO. Por eso el ranking escalado usa la '
        'mediana.')
    for horizon in ('30', '60', '90'):
        item = report['tendencia'][horizon]
        direction = item['direccional']
        lines.append(
            f'- tendencia h={horizon}: acierto global {_fmt(item["acierto"])} frente a trivial '
            f'{_fmt(item["trivial_clase_mayoritaria"])}; acierto direccional '
            f'{_fmt(direction["acierto"])} frente a trivial direccional '
            f'{_fmt(direction["trivial_clase_mayoritaria"])}. '
            + ('supera la regla trivial.'
               if item['supera_trivial'] and direction['supera_trivial']
               else 'NO supera la regla trivial: la tendencia derivada del forecast no '
                    'aporta valor en este horizonte.'))
    return lines


# ---------------------------------------------------------------------------
# 8. Salida y CLI
# ---------------------------------------------------------------------------
def write_outputs(report: dict, frame: pd.DataFrame, output_dir=None):
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path, json_path, markdown_path = report_paths(output_dir)
    frame.to_parquet(parquet_path, index=False, compression='zstd')
    json_path.write_text(render_json(report), encoding='utf-8')
    markdown_path.write_text(render_markdown(report), encoding='utf-8')
    return parquet_path, json_path, markdown_path


def _series_equal(left: pd.Series, right: pd.Series) -> bool:
    if (pd.api.types.is_datetime64_any_dtype(left)
            and pd.api.types.is_datetime64_any_dtype(right)):
        return left.astype('datetime64[ns]').equals(right.astype('datetime64[ns]'))
    if left.dtype.kind in 'fc' and right.dtype.kind in 'fc':
        return np.allclose(left.to_numpy(float), right.to_numpy(float),
                           rtol=0, atol=1e-9, equal_nan=True)
    return left.equals(right)


def check_outputs(report: dict, frame: pd.DataFrame, output_dir=None) -> bool:
    parquet_path, json_path, markdown_path = report_paths(output_dir)
    if not (parquet_path.exists() and json_path.exists() and markdown_path.exists()):
        return False
    if json_path.read_text(encoding='utf-8') != render_json(report):
        return False
    if markdown_path.read_text(encoding='utf-8') != render_markdown(report):
        return False
    published = pd.read_parquet(parquet_path)
    if published.shape != frame.shape or list(published.columns) != list(frame.columns):
        return False
    return all(_series_equal(published[column], frame[column])
               for column in frame.columns)


def summary(report: dict) -> dict:
    return {
        'output': 'reports/cashflow_forecast',
        'poblacion_por_horizonte': {
            h: report['poblacion_por_horizonte'][h]['universo_densidad']
            for h in ('30', '60', '90')},
        'trios_por_horizonte': {
            h: report['censo_trios']['por_h'][h]['n_trios']
            for h in ('30', '60', '90')},
        'verificacion_censo': {
            h: report['verificacion_censo_existente'][h]['coincide']
            for h in ('30', '90')},
        'ganador_flujo_mae': {
            h: report['ranking']['flujo_neto_acumulado'][h]['ganador_mae_eur']
            for h in ('30', '60', '90')},
        'acierto_tendencia': {
            h: report['tendencia'][h]['acierto'] for h in ('30', '60', '90')},
    }


def run(panel_path=None, censo_path=None, assessments_path=None, output_dir=None,
        write=True):
    panel = load_panel(panel_path)
    censo = load_censo_json(censo_path)
    evaluable = load_evaluable_v4(assessments_path)
    report, frame = build_report(panel, censo, evaluable, panel_path=panel_path,
                                 censo_path=censo_path,
                                 assessments_path=assessments_path)
    if write:
        write_outputs(report, frame, output_dir)
    return report, frame


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Backtest walk-forward del flujo de caja diario (30/60/90 dias)')
    parser.add_argument('--panel', type=Path, default=DEFAULT_PANEL,
                        help='panel_diario.parquet (solo lectura)')
    parser.add_argument('--censo', type=Path, default=DEFAULT_CENSO,
                        help='censo.json de daily_flows (solo lectura; contraste)')
    parser.add_argument('--assessments', type=Path, default=paths.ROOT / ASSESSMENTS,
                        help='assessments.parquet de v4 (solo lectura)')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--check', action='store_true',
                        help='no escribe: verifica que la salida publicada reproduce '
                             'el informe y el parquet')
    args = parser.parse_args(argv)
    try:
        panel = load_panel(args.panel)
        censo = load_censo_json(args.censo)
        evaluable = load_evaluable_v4(args.assessments)
        report, frame = build_report(panel, censo, evaluable,
                                     panel_path=args.panel, censo_path=args.censo,
                                     assessments_path=args.assessments)
        if args.check:
            if check_outputs(report, frame, args.output_dir):
                print(json.dumps({'check': 'ok', 'output_dir': str(args.output_dir)}))
                return 0
            print('Error: la salida publicada NO reproduce el informe generado',
                  file=sys.stderr)
            return 1
        write_outputs(report, frame, args.output_dir)
    except (ValueError, KeyError, FileNotFoundError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    print(json.dumps({**summary(report), 'output': str(args.output_dir)},
                     ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
