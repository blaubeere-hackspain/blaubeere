"""Medicion de los parametros libres k, alpha y beta de healthscore_v3.

NO es el scorer: este modulo no implementa el motor y no toca
xray/scoring_v3.py (solo lo usa el test de coherencia). Parte del parquet de
evaluaciones reports/score_v3/assessments.parquet (que produce
xray/scoring_io_v3.py) y, a partir de las columnas c6, t6, r_hist,
colchon_bruto y mora_ratio, RECALCULA H_final para cualquier terna
(k, alpha, beta) sin volver a los datos crudos.

Formula (identica a xray/scoring_v3.py):

  colchon_aplicable = 0 si colchon_bruto es None o negativo;
                      min(colchon_bruto, alpha * T6) en otro caso
  H       = 100 * (C6 + colchon_aplicable + k * R_hist)
                  / (C6 + colchon_aplicable + T6 + k)
            (con R_hist indefinida el termino k se omite por completo,
            como en el motor: sin euros virtuales ni en el denominador)
  H_final = H * (1 - beta * mora_ratio)
            (mora_ratio None = sin penalizacion, motivo registrado;
             mora_ratio 0 no penaliza, y es DISTINTO de None en motivo)

DISCIPLINA ANTI-LABEL: la calibracion es por SENSIBILIDAD Y ESTABILIDAD
unicamente. Este modulo NO lee marts/targets_proxy.parquet, ni
reports/label_review/, ni ninguna etiqueta ni objetivo de prediccion. El
objetivo es elegir los valores mas pequenos que hagan un trabajo real
(influencia demostrable) sin romper la estabilidad (p75 de |dH_final|
mensual < 8) ni aplanar la dispersion entre empresas. La declaracion
explicita se repite en report.md y en el JSON de resumen.

BARRIDO POR EJES, DECLARADO: se barre un eje cada vez con los otros dos
fijados en sus valores actuales (k=4178.45, alpha=3.0, beta=0.25). NO es un
barrido cartesiano conjunto; las interacciones de segundo orden entre
parametros no estan cubiertas y el informe lo declara.

Metricas por combinacion (ver funciones):
- ESTABILIDAD (objetivo p75 < 8): |dH_final| entre meses consecutivos de la
  misma empresa. Se excluyen los pares que involucran los dos primeros meses
  de cada empresa (misma convencion que xray/calibrate_k.py: pares (m-1, m)
  con ambos indices 0-based >= 2 en la serie ordenada de la empresa) y los
  pares con alguna nota no evaluable.
- APLANAMIENTO: std (poblacional) e IQR de H_final entre empresas por corte
  con >= 2 notas, resumidos con la mediana sobre cortes, y cuantas empresas
  caen en la banda estrecha 45-55 (mediana y maximo). Criterio de "no
  aplanamiento": std mediana >= 0.85 * referencia y mediana de empresas en
  banda <= 1.2 * referencia, con referencia = terna (0, 0, 0).
- INFLUENCIA de alpha: empresa-mes que cambian su nota mas de 1 punto al
  pasar de alpha=0 al valor probado, y magnitud mediana del cambio.
- INFLUENCIA de beta: lo mismo respecto a beta=0.
- CAMBIO DE ORDEN: correlacion de Spearman del ranking de empresas en el
  ultimo corte frente a (alpha=0, beta=0) con el mismo k (>= 3 empresas
  comparables). Spearman ~1 significa penalizacion decorativa para el orden.
- REACTIVIDAD de k: la misma medida de escalon sintetico de
  xray/calibrate_k.py (retardo hasta reflejar el 80% de un cambio sostenido
  de la ratio con volumen constante).
"""

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

from xray import paths
from xray.calibrate_k import quantile, reactivity as k_reactivity

VERSION = 'calibrate_params_v1'

DEFAULT_INPUT = paths.ROOT / 'reports' / 'score_v3' / 'assessments.parquet'
DEFAULT_OUTPUT = paths.ROOT / 'reports' / 'calibration_params'

MISSING_INPUT_MSG = ('falta reports/score_v3/assessments.parquet, ejecuta antes '
                     'xray.scoring_io_v3')

# Valores actuales: k medido pre-R3 (reports/calibration_k/report.md),
# alpha y beta provisionales sin medir. El barrido siempre los incluye
# como referencia y como punto de partida de cada eje.
CURRENT_K = 4178.45
CURRENT_ALPHA = 3.0
CURRENT_BETA = 0.25

P75_STABILITY_TARGET = 8.0    # objetivo p75 de |dH_final| mensual < 8 puntos
BAND = (45.0, 55.0)           # banda estrecha que delata aplanamiento
MIN_PAIR_INDEX = 2            # excluye pares que tocan los 2 primeros meses
DISPERSION_MIN_COMPANIES = 2  # dispersion por corte solo con >= 2 notas
MIN_RANK_COMPANIES = 3        # Spearman solo con >= 3 empresas comparables
INFLUENCE_POINT = 1.0         # umbral de "cambia la nota", en puntos
STD_FLOOR_RATIO = 0.85        # no aplanamiento: std >= 0.85 * referencia
BAND_CEIL_RATIO = 1.2         # no aplanamiento: banda <= 1.2 * referencia
BETA_BISECT_STEPS = 25        # afinado de beta tras la malla

# Columnas que este modulo necesita del parquet de evaluaciones.
LOAD_COLUMNS = ('company_id', 'group_id', 'month', 'health_score',
                'ventana_parcial', 'c6', 't6', 'r_hist', 'colchon_bruto',
                'mora_ratio', 'k', 'alpha', 'beta')

# Rango de k: reutiliza el barrido logaritmico de reports/calibration_k/
# report.md (inmutable), derivado a su vez del volumen mensual (C+T) del
# subconjunto de calibracion: p05/100 = 0.3 EUR y p95*100 = 1.511e8 EUR.
K_LOW = 0.3
K_HIGH = 1.511e8
K_LOG_POINTS = 22

# Malla fija multiplicativa de alpha, recortada al maximo derivado de los
# datos: alpha_max = max(colchon_bruto / T6) con T6 > 0 y colchon positivo.
# Por encima de alpha_max el tope alpha*T6 nunca satura y el parametro es
# inerte, asi que no tiene sentido barrir mas alla. Incluye 0 (el colchon
# no cuenta) y el 3.0 actual.
ALPHA_BASE_POINTS = (0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0,
                     8.0, 13.0, 20.0, 50.0, 100.0)

# beta en [0, 1]: 0 = sin penalizacion, 1 = mora total anula la nota.
BETA_POINTS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5,
               0.6, 0.75, 0.9, 1.0)

SWEEP_COLUMNS = ('axis', 'k', 'alpha', 'beta', 'dH_median', 'dH_p75',
                 'dH_p90', 'n_pairs', 'std_median', 'std_max', 'iqr_median',
                 'iqr_max', 'band_45_55_median', 'band_45_55_max',
                 'companies_median', 'n_cuts', 'spearman_last_cut',
                 'alpha_changed_rows', 'alpha_influence_median',
                 'beta_changed_rows', 'beta_influence_median',
                 'reactivity_months')


# ------------------------------------------------------------------ formula

def colchon_aplicable_value(colchon_bruto, t6, alpha):
    """Colchon aplicable: 0 si el colchon es None o negativo; si no,
    min(colchon_bruto, alpha * T6). Identico a xray/scoring_v3.py."""
    if colchon_bruto is None:
        return 0.0
    colchon = float(colchon_bruto)
    if colchon < 0.0:
        return 0.0
    return min(colchon, alpha * float(t6))


def mora_status(mora_ratio):
    """Motivo de la mora: None = sin cartera observable (NO es cero)."""
    if mora_ratio is None:
        return 'sin_mora_observable'
    return 'mora_cero' if float(mora_ratio) == 0.0 else 'mora_positiva'


def health_final(c6, t6, r_hist, colchon_bruto, mora_ratio, k, alpha, beta):
    """H_final recalculado con la formula v3, coherente con el motor.

    Devuelve None si el denominador es 0 (nota no evaluable). Los None de
    colchon y mora no son cero; el None de R_hist omite el termino k
    completo (numerador y denominador), exactamente como el motor.
    """
    c6 = 0.0 if c6 is None else float(c6)
    t6 = 0.0 if t6 is None else float(t6)
    colchon = colchon_aplicable_value(colchon_bruto, t6, alpha)
    if r_hist is None:
        k_term = 0.0
        k_denominador = 0.0
    else:
        k_term = k * float(r_hist)
        k_denominador = k
    numerador = c6 + colchon + k_term
    denominador = c6 + colchon + t6 + k_denominador
    if denominador <= 0:
        return None
    h = 100.0 * numerador / denominador
    if mora_ratio is None:
        return h
    return h * (1.0 - beta * float(mora_ratio))


def health(c6, t6, r_hist, colchon_bruto, k, alpha):
    """H antes de la penalizacion por mora (misma formula)."""
    return health_final(c6, t6, r_hist, colchon_bruto, None, k, alpha, 0.0)


# ------------------------------------------------------------------ cargador

def load_assessments(path=None):
    """Lee reports/score_v3/assessments.parquet (solo lectura).

    Devuelve (panel, meta). panel: {company_id: lista de filas ordenadas por
    mes} con c6, t6, r_hist, colchon_bruto, mora_ratio y ventana_parcial.
    meta: contadores y comprobacion de coherencia entre health_score del
    parquet y el recalculo con las k/alpha/beta declaradas en cada fila.
    Lanza FileNotFoundError con MISSING_INPUT_MSG si el fichero no existe.
    """
    path = Path(path) if path is not None else DEFAULT_INPUT
    if not path.exists():
        raise FileNotFoundError(MISSING_INPUT_MSG)
    columns_sql = ', '.join(LOAD_COLUMNS)
    con = duckdb.connect()
    try:
        try:
            raw = con.execute(
                f'SELECT {columns_sql} FROM read_parquet(\'{str(path)}\')'
            ).fetchall()
        except duckdb.BinderException as exc:
            raise ValueError(
                'assessments.parquet: no tiene las columnas esperadas '
                f'{columns_sql} ({exc})') from exc
    finally:
        con.close()

    panel = {}
    coherence_max = 0.0
    coherence_rows = 0
    groups = set()
    seen_keys = set()
    for values in raw:
        record = dict(zip(LOAD_COLUMNS, values))
        company = record['company_id']
        groups.add(record['group_id'])
        month = record['month']
        if isinstance(month, datetime):
            month = month.date()
        elif isinstance(month, str):
            month = date.fromisoformat(month)
        if not isinstance(month, date):
            raise ValueError('assessments.parquet: month no es una fecha')
        key = (company, month)
        if key in seen_keys:
            raise ValueError(f'clave empresa-mes duplicada: {key}')
        seen_keys.add(key)
        row = {
            'month': month,
            'c6': 0.0 if record['c6'] is None else float(record['c6']),
            't6': 0.0 if record['t6'] is None else float(record['t6']),
            'r_hist': None if record['r_hist'] is None
                      else float(record['r_hist']),
            'colchon_bruto': None if record['colchon_bruto'] is None
                             else float(record['colchon_bruto']),
            'mora_ratio': None if record['mora_ratio'] is None
                          else float(record['mora_ratio']),
            'ventana_parcial': bool(record['ventana_parcial']),
        }
        panel.setdefault(company, []).append(row)
        if record['health_score'] is not None:
            expected = health_final(row['c6'], row['t6'], row['r_hist'],
                                    row['colchon_bruto'], row['mora_ratio'],
                                    record['k'], record['alpha'],
                                    record['beta'])
            if expected is not None:
                coherence_max = max(coherence_max,
                                    abs(float(record['health_score'])
                                        - expected))
                coherence_rows += 1
    for series in panel.values():
        series.sort(key=lambda row: row['month'])
    meta = {
        'input': str(path),
        'companies': len(panel),
        'months': len({row['month'] for series in panel.values()
                       for row in series}),
        'rows': len(raw),
        'groups': len(groups),
        'coherence_max_abs_diff': coherence_max if coherence_rows else None,
        'coherence_rows': coherence_rows,
    }
    return panel, meta


# --------------------------------------------------------------- evaluacion

def evaluate_panel(panel, k, alpha, beta):
    """H_final recalculado para todo el panel con una terna fija.

    Devuelve {company_id: [{'index', 'month', 'hf'}, ...]}, con index la
    posicion 0-based del mes dentro de la serie ordenada de la empresa.
    """
    out = {}
    for company, series in panel.items():
        scored = []
        for index, row in enumerate(series):
            hf = health_final(row['c6'], row['t6'], row['r_hist'],
                              row['colchon_bruto'], row['mora_ratio'],
                              k, alpha, beta)
            scored.append({'index': index, 'month': row['month'], 'hf': hf})
        out[company] = scored
    return out


def stability(scores):
    """ESTABILIDAD: distribucion de |dH_final| entre meses consecutivos.

    Excluye los pares cuyo mes actual tiene indice <= MIN_PAIR_INDEX (toca
    los dos primeros meses de la empresa, ventana parcial; misma convencion
    que xray/calibrate_k.py) y los pares con alguna nota no evaluable.
    """
    deltas = []
    for series in scores.values():
        for previous, current in zip(series, series[1:]):
            if current['index'] <= MIN_PAIR_INDEX:
                continue
            if previous['hf'] is None or current['hf'] is None:
                continue
            deltas.append(abs(current['hf'] - previous['hf']))
    if not deltas:
        return {'n_pairs': 0, 'median': None, 'p75': None, 'p90': None}
    ordered = sorted(deltas)
    return {'n_pairs': len(deltas), 'median': quantile(ordered, 0.5),
            'p75': quantile(ordered, 0.75), 'p90': quantile(ordered, 0.9)}


def dispersion(scores):
    """APLANAMIENTO: dispersion de H_final entre empresas por corte.

    Mediana sobre cortes con >= 2 notas de la desviacion tipica poblacional
    y del IQR, y cuantas empresas caen en la banda estrecha 45-55.
    """
    cuts = {}
    for series in scores.values():
        for entry in series:
            if entry['hf'] is not None:
                cuts.setdefault(entry['month'], []).append(entry['hf'])
    stds, iqrs, band_counts, cut_sizes = [], [], [], []
    low, high = BAND
    for month in sorted(cuts):
        values = cuts[month]
        if len(values) < DISPERSION_MIN_COMPANIES:
            continue
        ordered = sorted(values)
        cut_sizes.append(len(ordered))
        stds.append(statistics.pstdev(ordered))
        iqrs.append(quantile(ordered, 0.75) - quantile(ordered, 0.25))
        band_counts.append(sum(1 for value in ordered if low <= value <= high))
    if not stds:
        return {'n_cuts': 0, 'median_std': None, 'std_max': None,
                'median_iqr': None, 'iqr_max': None,
                'band_45_55_median': None, 'band_45_55_max': None,
                'companies_median': None}
    return {'n_cuts': len(stds),
            'median_std': quantile(sorted(stds), 0.5), 'std_max': max(stds),
            'median_iqr': quantile(sorted(iqrs), 0.5), 'iqr_max': max(iqrs),
            'band_45_55_median': quantile(sorted(band_counts), 0.5),
            'band_45_55_max': max(band_counts),
            'companies_median': quantile(sorted(cut_sizes), 0.5)}


def influence(base_scores, test_scores):
    """INFLUENCIA de un parametro: empresa-mes que cambian su nota mas de
    INFLUENCE_POINT puntos respecto a la base, y magnitud mediana del cambio.

    Solo se cuentan filas con nota evaluable en las dos configuraciones.
    """
    n_rows = 0
    magnitudes = []
    for company, series in base_scores.items():
        for base_entry, test_entry in zip(series, test_scores[company]):
            if base_entry['hf'] is None or test_entry['hf'] is None:
                continue
            n_rows += 1
            magnitude = abs(test_entry['hf'] - base_entry['hf'])
            if magnitude > INFLUENCE_POINT:
                magnitudes.append(magnitude)
    return {'n_rows': n_rows, 'n_changed': len(magnitudes),
            'median_magnitude': (quantile(sorted(magnitudes), 0.5)
                                 if magnitudes else None)}


def _avg_ranks(values):
    """Rangos medios (empates con el promedio de posiciones)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for position in range(i, j + 1):
            ranks[order[position]] = average
        i = j + 1
    return ranks


def _hf_at(series, month):
    for entry in series:
        if entry['month'] == month:
            return entry['hf']
    return None


def spearman_last_cut(base_scores, test_scores):
    """CAMBIO DE ORDEN: Spearman del ranking de empresas en el ultimo corte
    con notas comparables, frente a la configuracion de referencia.

    Devuelve None si hay menos de MIN_RANK_COMPANIES empresas comparables o
    si algun ranking es degenerado (dispersion nula).
    """
    months_with_hf = [entry['month'] for series in base_scores.values()
                      for entry in series if entry['hf'] is not None]
    if not months_with_hf:
        return None
    last_month = max(months_with_hf)
    pairs_base, pairs_test = [], []
    for company in sorted(base_scores):
        base_hf = _hf_at(base_scores[company], last_month)
        test_hf = _hf_at(test_scores[company], last_month)
        if base_hf is not None and test_hf is not None:
            pairs_base.append(base_hf)
            pairs_test.append(test_hf)
    if len(pairs_base) < MIN_RANK_COMPANIES:
        return None
    rank_x = _avg_ranks(pairs_base)
    rank_y = _avg_ranks(pairs_test)
    mean_x = sum(rank_x) / len(rank_x)
    mean_y = sum(rank_y) / len(rank_y)
    covariance = sum((x - mean_x) * (y - mean_y)
                     for x, y in zip(rank_x, rank_y))
    variance_x = sum((x - mean_x) ** 2 for x in rank_x)
    variance_y = sum((y - mean_y) ** 2 for y in rank_y)
    denominator = math.sqrt(variance_x * variance_y)
    if denominator == 0:
        return None
    return covariance / denominator


def mora_jump_stats(panel):
    """M3: distribucion de |d mora_ratio| entre meses consecutivos.

    Solo pares con mora observable en los dos meses. Cuenta ademas los
    saltos >= 0.5 (del tipo 0 -> 1, que entra directo en la nota).
    """
    deltas = []
    for series in panel.values():
        for previous, current in zip(series, series[1:]):
            if previous['mora_ratio'] is None or current['mora_ratio'] is None:
                continue
            deltas.append(abs(current['mora_ratio'] - previous['mora_ratio']))
    if not deltas:
        return {'n_pairs': 0, 'median': None, 'p75': None, 'p90': None,
                'max': None, 'n_jumps_ge_05': None, 'pct_jumps_ge_05': None}
    ordered = sorted(deltas)
    jumps = sum(1 for value in ordered if value >= 0.5)
    return {'n_pairs': len(deltas), 'median': quantile(ordered, 0.5),
            'p75': quantile(ordered, 0.75), 'p90': quantile(ordered, 0.9),
            'max': ordered[-1], 'n_jumps_ge_05': jumps,
            'pct_jumps_ge_05': 100.0 * jumps / len(ordered)}


# ------------------------------------------------------------------- mallas

def k_axis_grid():
    """Rango logaritmico de reports/calibration_k/report.md mas k=0
    (obligatorio: v3 sin shrinkage) y el k actual (omitido si coincide con
    un punto de la malla a menos del 0.1%)."""
    ratio = (K_HIGH / K_LOW) ** (1.0 / (K_LOG_POINTS - 1))
    points = [K_LOW * ratio ** j for j in range(K_LOG_POINTS)]
    if not any(abs(point - CURRENT_K) <= 1e-3 * max(point, CURRENT_K)
               for point in points):
        points.append(CURRENT_K)
    grid = {0.0, *points}
    return sorted(grid)


def alpha_axis_grid(panel):
    """Rango de alpha derivado de los datos: malla fija hasta alpha_max.

    alpha_max = max(colchon_bruto / T6) con T6 > 0 y colchon positivo; por
    encima el tope alpha*T6 nunca satura y el parametro no hace nada. Si no
    hay ningun colchon observable, la malla es solo [0] (inerte).
    Incluye 0 (el colchon no cuenta) y el 3.0 actual.
    """
    alpha_max = 0.0
    found = False
    for series in panel.values():
        for row in series:
            if (row['colchon_bruto'] is not None and row['colchon_bruto'] > 0
                    and row['t6'] > 0):
                found = True
                alpha_max = max(alpha_max, row['colchon_bruto'] / row['t6'])
    if not found:
        return [0.0]
    grid = {point for point in ALPHA_BASE_POINTS if point <= alpha_max}
    grid.update((0.0, CURRENT_ALPHA, alpha_max))
    return sorted(grid)


def beta_axis_grid():
    """Rango de beta: [0, 1] con el 0.25 actual incluido."""
    return sorted(BETA_POINTS)


# ------------------------------------------------------------------ barrido

def _panel_volume(panel):
    """Volumen mensual (C6 + T6) mediano del panel, para los escalones
    sinteticos de reactividad de k."""
    volumes = sorted(row['c6'] + row['t6'] for series in panel.values()
                     for row in series)
    if not volumes:
        raise ValueError('panel vacio; no se puede medir la reactividad')
    return quantile(volumes, 0.5)


def summarize_row(axis, k, alpha, beta, scores, ref_scores,
                  alpha_influence=None, beta_influence=None,
                  reactivity_months=None):
    st = stability(scores)
    ds = dispersion(scores)
    return {
        'axis': axis, 'k': k, 'alpha': alpha, 'beta': beta,
        'dH_median': st['median'], 'dH_p75': st['p75'], 'dH_p90': st['p90'],
        'n_pairs': st['n_pairs'],
        'std_median': ds['median_std'], 'std_max': ds['std_max'],
        'iqr_median': ds['median_iqr'], 'iqr_max': ds['iqr_max'],
        'band_45_55_median': ds['band_45_55_median'],
        'band_45_55_max': ds['band_45_55_max'],
        'companies_median': ds['companies_median'], 'n_cuts': ds['n_cuts'],
        'spearman_last_cut': (spearman_last_cut(ref_scores, scores)
                              if ref_scores is not None else None),
        'alpha_changed_rows': (alpha_influence or {}).get('n_changed'),
        'alpha_influence_median': (alpha_influence or {}).get('median_magnitude'),
        'beta_changed_rows': (beta_influence or {}).get('n_changed'),
        'beta_influence_median': (beta_influence or {}).get('median_magnitude'),
        'reactivity_months': reactivity_months,
    }


def run_sweep(panel):
    """Barrido POR EJES (declarado; no cartesiano conjunto).

    Eje k: alpha=3.0 y beta=0.25 fijados; Spearman frente a (k, 0, 0).
    Eje alpha: k=4178.45 y beta=0.25 fijados; influencia frente a alpha=0.
    Eje beta: k=4178.45 y alpha=3.0 fijados; influencia frente a beta=0.
    Ademas: fila de referencia (0, 0, 0) y fila con los valores actuales.
    """
    rows = []
    volume = _panel_volume(panel)

    for k in k_axis_grid():
        scores = evaluate_panel(panel, k, CURRENT_ALPHA, CURRENT_BETA)
        ref = evaluate_panel(panel, k, 0.0, 0.0)
        reactivity = k_reactivity(k, volume)['median_delay_months']
        rows.append(summarize_row('k', k, CURRENT_ALPHA, CURRENT_BETA,
                                  scores, ref, reactivity_months=reactivity))

    alpha_base = evaluate_panel(panel, CURRENT_K, 0.0, CURRENT_BETA)
    for alpha in alpha_axis_grid(panel):
        scores = evaluate_panel(panel, CURRENT_K, alpha, CURRENT_BETA)
        infl = (influence(alpha_base, scores) if alpha > 0.0 else
                {'n_rows': 0, 'n_changed': 0, 'median_magnitude': None})
        rows.append(summarize_row('alpha', CURRENT_K, alpha, CURRENT_BETA,
                                  scores, alpha_base, alpha_influence=infl))

    beta_base = evaluate_panel(panel, CURRENT_K, CURRENT_ALPHA, 0.0)
    for beta in beta_axis_grid():
        scores = evaluate_panel(panel, CURRENT_K, CURRENT_ALPHA, beta)
        infl = (influence(beta_base, scores) if beta > 0.0 else
                {'n_rows': 0, 'n_changed': 0, 'median_magnitude': None})
        rows.append(summarize_row('beta', CURRENT_K, CURRENT_ALPHA, beta,
                                  scores, beta_base, beta_influence=infl))

    zero = evaluate_panel(panel, 0.0, 0.0, 0.0)
    rows.append(summarize_row('referencia_000', 0.0, 0.0, 0.0, zero, zero))
    current = evaluate_panel(panel, CURRENT_K, CURRENT_ALPHA, CURRENT_BETA)
    current_ref = evaluate_panel(panel, CURRENT_K, 0.0, 0.0)
    rows.append(summarize_row(
        'actual', CURRENT_K, CURRENT_ALPHA, CURRENT_BETA, current,
        current_ref,
        reactivity_months=k_reactivity(CURRENT_K, volume)['median_delay_months']))
    return rows


def stability_p75(panel, k, alpha, beta):
    return stability(evaluate_panel(panel, k, alpha, beta))['p75']


def beta_break_target(panel):
    """M3: el beta a partir del cual el objetivo p75 < 8 se rompe.

    Metodo declarado: primero la malla de beta; si entre dos puntos
    consecutivos el p75 pasa de cumplir a no cumplir, se afina con
    biseccion (BETA_BISECT_STEPS pasos) bajo el supuesto de direccion
    (el p75 empeora al subir beta; el p75 no es estrictamente monótono,
    la cifra afinada es orientativa y la malla es la cota verificada).
    Devuelve (beta_maximo_que_cumple o None, detalle).
    """
    detail = {'p75_beta_0': stability_p75(panel, CURRENT_K,
                                          CURRENT_ALPHA, 0.0)}
    if detail['p75_beta_0'] is None or detail['p75_beta_0'] >= P75_STABILITY_TARGET:
        detail['note'] = ('el objetivo p75<8 ya falla con beta=0: lo rompe '
                          'la base, no la penalizacion por mora')
        return None, detail
    last_ok = 0.0
    for beta in beta_axis_grid():
        if beta <= 0.0:
            continue
        p = stability_p75(panel, CURRENT_K, CURRENT_ALPHA, beta)
        detail[f'p75_beta_{beta:g}'] = p
        if p is None or p >= P75_STABILITY_TARGET:
            low, high = last_ok, beta
            for _ in range(BETA_BISECT_STEPS):
                mid = (low + high) / 2.0
                p_mid = stability_p75(panel, CURRENT_K, CURRENT_ALPHA, mid)
                if p_mid is None or p_mid >= P75_STABILITY_TARGET:
                    high = mid
                else:
                    low = mid
            detail['bisection_low'] = low
            detail['bisection_high'] = high
            return low, detail
        last_ok = beta
    detail['note'] = ('el objetivo p75<8 se mantiene para todo beta de la '
                      'malla hasta 1.0')
    return None, detail


# ------------------------------------------------------------- recomendacion

def dispersion_ok(row, reference):
    """Criterio de no aplanamiento declarado: la std mediana de H_final no
    cae por debajo del 85% de la referencia (0, 0, 0) y la mediana de
    empresas en la banda 45-55 no crece por encima del 120%."""
    std_ref = reference['std_median']
    band_ref = reference['band_45_55_median']
    std_row = row['std_median']
    band_row = row['band_45_55_median']
    if std_ref is None or std_row is None:
        return False
    if std_row < STD_FLOOR_RATIO * std_ref:
        return False
    if band_ref is None or band_row is None:
        return False
    return band_row <= BAND_CEIL_RATIO * band_ref


def p75_ok(row):
    return row['dH_p75'] is not None and row['dH_p75'] < P75_STABILITY_TARGET


def influence_ok(influence_row):
    """Influencia demostrable: alguna empresa-mes cambia su nota mas de
    1 punto y la magnitud mediana del cambio es > 0."""
    return (bool(influence_row)
            and influence_row.get('n_changed', 0) > 0
            and (influence_row.get('median_magnitude') or 0.0) > 0.0)


def _pick_axis_star(axis_rows, reference):
    """El menor valor del eje con influencia demostrable que cumple p75 < 8
    y dispersion. Devuelve (valor o 0.0, motivo).

    - Sin influencia a ningun valor probado -> 0: un parametro que no hace
      nada es peor que ninguno, porque sugiere un control que no existe.
    - Influencia solo a valores que rompen p75<8 o la dispersion -> 0.
    """
    influence_rows = [row for row in axis_rows if influence_ok(row)]
    if not influence_rows:
        return 0.0, ('SIN INFLUENCIA demostrable: ningun valor probado cambia '
                     'alguna nota mas de 1 punto; se recomienda 0')
    for row in sorted(influence_rows, key=lambda row: row['alpha']):
        if p75_ok(row) and dispersion_ok(row, reference):
            return row['alpha'], (f"{row['alpha']:g}: la menor influencia "
                                  'demostrable que cumple p75<8 y dispersion')
    return 0.0, ('tiene influencia solo a valores que rompen p75<8 o el '
                 'criterio de dispersion; se recomienda 0 y se explica')


def _pick_beta_star(axis_rows, reference):
    """Igual que _pick_axis_star pero sobre el eje beta."""
    influence_rows = [row for row in axis_rows if influence_ok(row)]
    if not influence_rows:
        return 0.0, ('SIN INFLUENCIA demostrable: ningun beta probado cambia '
                     'alguna nota mas de 1 punto (mora nula o inexistente); '
                     'se recomienda 0')
    for row in sorted(influence_rows, key=lambda row: row['beta']):
        if p75_ok(row) and dispersion_ok(row, reference):
            return row['beta'], (f'{row['beta']:g}: la menor influencia '
                                 'demostrable que cumple p75<8 y dispersion')
    return 0.0, ('tiene influencia solo a valores que rompen p75<8 o el '
                 'criterio de dispersion; se recomienda 0 y se explica')


def recommend(rows, panel):
    """M4: la terna (k, alpha, beta) mas pequena que cumple los objetivos.

    Eleccion por ejes (declarado):
    - k* = la menor k del eje k que cumple p75 < 8 y no aplana.
    - alpha* = la menor alpha con influencia demostrable (respecto a
      alpha=0) que ademas cumple p75<8 y dispersion en su eje.
    - beta* = lo mismo respecto a beta=0.
    Si un parametro no tiene influencia demostrable a ningun valor razonable,
    se recomienda 0 y se explica: un parametro que no hace nada es peor que
    ninguno, porque sugiere un control que no existe.
    La terna elegida se verifica despues de forma CONJUNTA; si la
    verificacion conjunta falla, se declara y no se fuerza la recomendacion.
    """
    reference = next(row for row in rows if row['axis'] == 'referencia_000')
    reasons = []

    k_star = None
    for row in sorted((r for r in rows if r['axis'] == 'k'),
                      key=lambda row: row['k']):
        if p75_ok(row) and dispersion_ok(row, reference):
            k_star = row['k']
            reasons.append(
                f"k*={k_star:.6g}: la menor k del eje que cumple p75<8 "
                f"({row['dH_p75']:.2f}) y no aplana")
            break
    else:
        reasons.append('ninguna k del eje cumple p75<8 sin aplanar')

    alpha_star, alpha_reason = _pick_axis_star(
        [row for row in rows if row['axis'] == 'alpha'], reference)
    reasons.append(f'alpha*: {alpha_reason}')

    beta_star, beta_reason = _pick_beta_star(
        [row for row in rows if row['axis'] == 'beta'], reference)
    reasons.append(f'beta*: {beta_reason}')

    if k_star is None:
        return {'k': None, 'alpha': alpha_star, 'beta': beta_star,
                'meets_p75': False, 'meets_dispersion': False,
                'joint_row': None, 'verdict': 'sin_terna',
                'reasons': reasons}

    joint_scores = evaluate_panel(panel, k_star, alpha_star, beta_star)
    joint = summarize_row('joint', k_star, alpha_star, beta_star,
                          joint_scores, joint_scores)
    meets_p75 = p75_ok(joint)
    meets_disp = dispersion_ok(joint, reference)
    if meets_p75 and meets_disp:
        reasons.append(f'verificacion conjunta ({k_star:.6g}, {alpha_star:g}, '
                       f"{beta_star:g}): p75={joint['dH_p75']:.2f} < 8 y "
                       'dispersion OK')
    else:
        reasons.append(f'la verificacion conjunta ({k_star:.6g}, '
                       f'{alpha_star:g}, {beta_star:g}) NO cumple todo: '
                       f'p75={fmt_num(joint["dH_p75"])}, '
                       f"dispersion={'OK' if meets_disp else 'FALLA'}; "
                       'no se fuerza la recomendacion')
    return {'k': k_star, 'alpha': alpha_star, 'beta': beta_star,
            'meets_p75': meets_p75, 'meets_dispersion': meets_disp,
            'joint_row': joint,
            'verdict': 'ok' if (meets_p75 and meets_disp) else 'parcial',
            'reasons': reasons}


# ------------------------------------------------------------------ reporte

def fmt_num(value, spec='{:.2f}'):
    return '-' if value is None else spec.format(value)


def fmt_int(value):
    return '-' if value is None else f'{value:.0f}'


def fmt_spearman(value):
    return '-' if value is None else f'{value:+.3f}'


def axis_table(rows, axis):
    """Tabla markdown de un eje del barrido."""
    if axis == 'k':
        header = ('| k (EUR) | dH p50 | dH p75 | dH p90 | std p50 | '
                  'IQR p50 | empresas 45-55 p50/max | reactividad (meses) | '
                  'Spearman |')
    else:
        name = 'alpha' if axis == 'alpha' else 'beta'
        header = (f'| {name} | dH_final p50 | dH_final p75 | '
                  'dH_final p90 | std p50 | IQR p50 | '
                  'empresas 45-55 p50/max | influencia (n > 1 pt) | '
                  'influencia mediana | Spearman |')
    n_cells = header.count('|') - 1
    lines = [header, '|' + '---|' * n_cells]
    for row in rows:
        if row['axis'] != axis:
            continue
        band = ('-' if row['band_45_55_median'] is None else
                f"{fmt_int(row['band_45_55_median'])}/"
                f"{fmt_int(row['band_45_55_max'])}")
        p75_mark = ''
        if axis == 'k':
            cells = [
                ('0 (sin shrinkage)' if row['k'] == 0
                 else f"{row['k']:.4g}"),
                fmt_num(row['dH_median']), fmt_num(row['dH_p75']),
                fmt_num(row['dH_p90']), fmt_num(row['std_median']),
                fmt_num(row['iqr_median']), band,
                fmt_num(row['reactivity_months'], '{:.1f}'),
                fmt_num(row['spearman_last_cut'], '{:+.3f}'),
            ]
            if row['dH_p75'] is not None \
                    and row['dH_p75'] < P75_STABILITY_TARGET:
                cells[0] += ' **cumple p75<8**'
        else:
            changed = (row['alpha_changed_rows'] if axis == 'alpha'
                       else row['beta_changed_rows'])
            median = (row['alpha_influence_median'] if axis == 'alpha'
                      else row['beta_influence_median'])
            cells = [
                (f"{row['alpha']:g}" if axis == 'alpha'
                 else f"{row['beta']:g}"),
                fmt_num(row['dH_median']), fmt_num(row['dH_p75']),
                fmt_num(row['dH_p90']), fmt_num(row['std_median']),
                fmt_num(row['iqr_median']), band,
                fmt_int(changed), fmt_num(median),
                fmt_num(row['spearman_last_cut'], '{:+.3f}'),
            ]
        lines.append('| ' + ' | '.join(cells) + ' |' + p75_mark)
    return lines


def build_report(rows, recommendation, meta, mora_stats, beta_break,
                 beta_break_detail):
    """report.md: tablas por eje, aviso M3, recomendacion M4 y la declaracion
    explicita de que los parametros NO estan ajustados contra ninguna
    etiqueta ni objetivo."""
    reference = next(row for row in rows if row['axis'] == 'referencia_000')
    k_rows = [row for row in rows if row['axis'] == 'k']
    alpha_rows = [row for row in rows if row['axis'] == 'alpha']
    beta_rows = [row for row in rows if row['axis'] == 'beta']
    current = next(row for row in rows if row['axis'] == 'actual')

    lines = [
        '# Calibracion de (k, alpha, beta) para healthscore_v3 (medicion)',
        '',
        'Formula recalculada desde el parquet: H_final = 100*(C6 + colchon + '
        'k*R_hist)/(C6 + colchon + T6 + k), con colchon = min(colchon_bruto, '
        'alpha*T6) y 0 si el colchon es None o negativo, y '
        'H_final = H * (1 - beta*mora_ratio) sin penalizacion si mora_ratio '
        'es None. Identica a xray/scoring_v3.py (verificada por test).',
        '',
        '## DECLARACION ANTI-LABEL (obligatoria)',
        '',
        '**Los parametros k, alpha y beta NO estan ajustados contra ninguna '
        'etiqueta, ni contra targets_proxy, ni contra '
        'deterioro_cobro_60d_pp, ni contra cualquier otro objetivo de '
        'prediccion.** Este analisis no ha leido marts/targets_proxy.parquet '
        'ni reports/label_review/. La calibracion es por sensibilidad y '
        'estabilidad unicamente: elegir los valores mas pequenos que hagan '
        'un trabajo real (influencia demostrable en la nota) sin romper la '
        'estabilidad (p75 de |dH_final| < 8) ni aplanar la dispersion. El '
        'objetivo NO es predecir nada.',
        '',
        '## Entrada y barrido',
        '',
        f"- Fichero: `{meta['input']}` ({meta['rows']} filas, "
        f"{meta['companies']} empresas, {meta['months']} meses, "
        f"{meta['groups']} grupos).",
        '- Maxima discrepancia entre health_score del parquet y el recalculo '
        'con las k/alpha/beta de cada fila: '
        f"{fmt_num(meta['coherence_max_abs_diff'], '{:.2e}')} "
        f"({meta['coherence_rows']} filas comparadas).",
        '- **Barrido POR EJES, no conjunto**: se barre un parametro cada vez '
        'con los otros dos fijados en sus valores actuales (k=4178.45, '
        'alpha=3.0, beta=0.25). Las interacciones de segundo orden entre '
        'parametros NO estan cubiertas.',
        '- Estabilidad: p75 de |dH_final| entre meses consecutivos, '
        'excluyendo los pares que tocan los dos primeros meses de cada '
        'empresa (ventana parcial).',
        f'- Dispersion de referencia (terna 0,0,0): std mediana = '
        f"{fmt_num(reference['std_median'])}, IQR mediano = "
        f"{fmt_num(reference['iqr_median'])}, "
        f"empresas 45-55 p50/max = "
        f"{fmt_int(reference['band_45_55_median'])}/"
        f"{fmt_int(reference['band_45_55_max'])}.",
        '',
        '## Eje k (alpha=3.0, beta=0.25 fijados)',
        '',
        'Rango reutilizado de reports/calibration_k/report.md (inmutable): '
        f'{K_LOG_POINTS} puntos logaritmicos desde k={K_LOW:g} EUR (p05/100) '
        f'hasta k={K_HIGH:.4g} EUR (p95*100), mas k=0 obligatorio y el k '
        'actual 4178.45.',
        '',
        *axis_table(k_rows, 'k'),
        '',
        '## Eje alpha (k=4178.45, beta=0.25 fijados)',
        '',
        'Rango derivado de los datos: alpha_max = max(colchon_bruto/T6) con '
        'T6>0 y colchon positivo; por encima el tope nunca satura y el '
        'parametro es inerte. 0 = el colchon no cuenta. Influencia: '
        'empresa-mes que cambian su nota mas de 1 punto respecto a alpha=0.',
        '',
        *axis_table(alpha_rows, 'alpha'),
        '',
        '## Eje beta (k=4178.45, alpha=3.0 fijados)',
        '',
        'Rango [0, 1]: 0 = sin penalizacion, 1 = mora total anula la nota. '
        'Influencia: empresa-mes que cambian su nota mas de 1 punto respecto '
        'a beta=0.',
        '',
        *axis_table(beta_rows, 'beta'),
        '',
        '## AVISO CRITICO M3: saltos de mora_ratio y estabilidad',
        '',
        'mora_ratio puede saltar de 0 a 1 entre meses consecutivos; al '
        'multiplicar por (1 - beta*mora_ratio) esos saltos entran DIRECTOS '
        'en la nota y beta puede DESHACER la estabilidad que da la ventana '
        'de 6 meses.',
        '',
        f"- Pares consecutivos con mora observable: {mora_stats['n_pairs']}.",
        f"- |d mora_ratio|: mediana = {fmt_num(mora_stats['median'])}, "
        f"p75 = {fmt_num(mora_stats['p75'])}, p90 = "
        f"{fmt_num(mora_stats['p90'])}, max = {fmt_num(mora_stats['max'])}.",
        f"- Saltos >= 0.5: {fmt_int(mora_stats['n_jumps_ge_05'])} de "
        f"{mora_stats['n_pairs']} ({fmt_num(mora_stats['pct_jumps_ge_05'], '{:.1f}')}%).",
        f"- p75 de |dH_final| con beta=0: {fmt_num(beta_break_detail['p75_beta_0'])}.",
        '',
        'Evolucion del p75 de |dH_final| con beta (eje beta del barrido):',
        '',
        '| beta | |dH_final| p75 | cumple p75<8 |',
        '|---|---|---|',
        *[f"| {row['beta']:g} | {fmt_num(row['dH_p75'])} | "
          f"{'SI' if row['dH_p75'] is not None and row['dH_p75'] < P75_STABILITY_TARGET else 'NO'} |"
          for row in beta_rows],
        '',
    ]
    if beta_break is not None:
        lines += [
            f"**El objetivo p75 < 8 se rompe a partir de beta = "
            f"{beta_break:.6g}** (afinado por biseccion entre los puntos de "
            f"malla que cumplen y dejan de cumplir; el p75 no es "
            "estrictamente monótono en beta, la cifra es orientativa y la "
            "malla es la cota verificada).",
        ]
    else:
        lines += [f"**{beta_break_detail['note']}**"]
    lines += [
        '',
        '## Recomendacion M4',
        '',
    ]
    if recommendation['verdict'] == 'ok':
        lines += [
            f"**Terna recomendada: k = {recommendation['k']:.6g} EUR, "
            f"alpha = {recommendation['alpha']:g}, beta = "
            f"{recommendation['beta']:g}.** Es la terna mas pequena que "
            'cumple p75 < 8, no aplana (criterio de dispersion declarado) y '
            'tiene influencia demostrable en alpha y beta.',
            '',
        ]
    elif recommendation['verdict'] == 'sin_terna':
        lines += [
            '**Ninguna terna del barrido cumple todo.** No se fuerza la '
            'recomendacion; la mejor evidencia disponible:',
            '',
        ]
    else:
        lines += [
            f"**Terna parcialmente cumplida: k = {recommendation['k']:.6g} "
            f"EUR, alpha = {recommendation['alpha']:g}, beta = "
            f"{recommendation['beta']:g}.** Cumple parte de los objetivos "
            'pero no todo; no se fuerza una recomendacion que los numeros '
            'no sostienen.',
            '',
        ]
    lines += ['- ' + reason for reason in recommendation['reasons']]
    joint = recommendation.get('joint_row')
    if joint is not None:
        lines += [
            '',
            f"Cifras de la terna conjunta: |dH_final| p50 = "
            f"{fmt_num(joint['dH_median'])}, p75 = {fmt_num(joint['dH_p75'])}, "
            f"p90 = {fmt_num(joint['dH_p90'])}; std mediana = "
            f"{fmt_num(joint['std_median'])}; IQR mediano = "
            f"{fmt_num(joint['iqr_median'])}; empresas 45-55 p50/max = "
            f"{fmt_int(joint['band_45_55_median'])}/"
            f"{fmt_int(joint['band_45_55_max'])}; Spearman del ranking "
            f"frente a (alpha=0, beta=0) = "
            f"{fmt_num(joint['spearman_last_cut'], '{:+.3f}')}.",
        ]
    lines += [
        '',
        '## Notas',
        '',
        '- Exclusion de estabilidad: pares (m-1, m) con ambos indices '
        '>= 2 (0-based dentro de cada empresa), misma convencion que '
        'xray/calibrate_k.py.',
        '- Criterio de no aplanamiento: std mediana >= 0.85 * referencia '
        '(0,0,0) y empresas 45-55 mediana <= 1.2 * referencia.',
        '- Influencia demostrable: alguna empresa-mes cambia su nota mas de '
        '1 punto y la magnitud mediana del cambio es > 0.',
        '- Si un parametro no hace nada a ningun valor razonable se '
        'recomienda 0: un parametro inerte sugiere un control que no existe.',
        '- Este modulo solo mide; no implementa el scorer y los parametros '
        'NO estan ajustados contra ninguna etiqueta ni objetivo.',
        '- El barrido es por ejes; la verificacion conjunta de la terna '
        'recomendada se incluye como fila `joint` en sweep.csv.',
    ]
    return '\n'.join(lines) + '\n'


# ----------------------------------------------------------------------- IO

def write_outputs(output, rows, recommendation, meta, mora_stats,
                  beta_break, beta_break_detail):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'sweep.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(SWEEP_COLUMNS),
                                extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    report = build_report(rows, recommendation, meta, mora_stats, beta_break,
                          beta_break_detail)
    (output / 'report.md').write_text(report, encoding='utf-8')


def run(output=None, input_path=None):
    """Ejecuta el barrido completo y escribe sweep.csv y report.md.

    Devuelve el resumen JSON (dict). Lanza FileNotFoundError con
    MISSING_INPUT_MSG si falta el parquet de entrada, y FileExistsError si
    la ruta de salida ya existe.
    """
    panel, meta = load_assessments(input_path)
    mora_stats = mora_jump_stats(panel)
    beta_break, beta_break_detail = beta_break_target(panel)
    rows = run_sweep(panel)
    recommendation = recommend(rows, panel)
    output = Path(output) if output is not None else DEFAULT_OUTPUT
    write_outputs(output, rows, recommendation, meta, mora_stats,
                  beta_break, beta_break_detail)
    summary = {
        'output': str(output),
        'input': meta['input'],
        'panel': {key: meta[key] for key in
                  ('rows', 'companies', 'months', 'groups',
                   'coherence_max_abs_diff', 'coherence_rows')},
        'sweep': 'por_ejes_otros_parametros_fijados_en_su_valor_actual',
        'labels_used': False,
        'declaration': ('los parametros NO estan ajustados contra ninguna '
                        'etiqueta, targets_proxy, deterioro_cobro_60d_pp ni '
                        'ningun objetivo; calibracion por sensibilidad y '
                        'estabilidad unicamente'),
        'mora_delta': mora_stats,
        'beta_break_target': beta_break,
        'beta_break_detail': {key: value for key, value
                              in beta_break_detail.items()},
        'recommended': {'k': recommendation['k'],
                        'alpha': recommendation['alpha'],
                        'beta': recommendation['beta'],
                        'verdict': recommendation['verdict'],
                        'meets_p75_target': recommendation['meets_p75'],
                        'meets_dispersion': recommendation['meets_dispersion'],
                        'reasons': recommendation['reasons']},
        'recommended_metrics': (
            None if recommendation['joint_row'] is None else
            {key: recommendation['joint_row'][key] for key in
             ('dH_median', 'dH_p75', 'dH_p90', 'std_median', 'iqr_median',
              'band_45_55_median', 'band_45_55_max', 'spearman_last_cut')}),
        'sweep_rows': len(rows),
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m xray.calibrate_params',
        description='Mide los parametros libres k, alpha y beta de '
                    'healthscore_v3 (sensibilidad y estabilidad; sin '
                    'etiquetas); no implementa el scorer.')
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT,
                        help='parquet de evaluaciones de score_v3')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                        help='ruta NUEVA bajo reports/; aborta si ya existe')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva '
                     'para conservar las mediciones previas')
    try:
        summary = run(args.output, args.input)
    except FileNotFoundError as exc:
        sys.stderr.write(f'ERROR: {exc}\n')
        return 1
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, allow_nan=False,
                                sort_keys=True, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
