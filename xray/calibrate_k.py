"""Medicion del parametro libre k de healthscore_v3 (NO es el scorer).

La formula de v3, ya decidida, es H(m,k) = 100 * (C6 + k*R_hist) / (C6 + T6 + k), donde
C6 y T6 son las sumas de la ventana expansiva (hasta 6 meses cerrados terminando en el
corte m) y R_hist es la ratio acumulada de la propia empresa desde su primer mes
observado hasta m inclusive. k es un volumen virtual EN EUROS y el unico parametro
libre. Este modulo mide k barrilandolo y evaluando los criterios C2, C3, C4, C5 y
reactividad; no implementa el scorer y no toca xray/scoring.py.

Disciplina point-in-time: R_hist(m) y las sumas de ventana del corte m usan unicamente
meses <= m; nada posterior al corte puede influir en el valor de ese corte.

Criterios declarados:
- C2 estabilidad: p75 de |H(m,k) - H(m-1,k)| entre meses consecutivos de la misma
  empresa, excluyendo los dos primeros meses de cada empresa (ventana parcial). < 8.
- C5 dispersion: desviacion tipica y IQR de H entre empresas en cada corte, resumidos
  con la mediana sobre los cortes; mas empresas en la banda estrecha 45-55.
- C3 tendencia: % de empresas con >= 12 meses evaluables cuya serie de H tiene
  tendencia lineal detectable (|pendiente OLS| > 2 * error tipico de la pendiente).
- C4 sesgo adverso: volumen mediano de las empresa-mes con nota frente a las que
  quedan en intervalo (cota inferior con subtotales conocidos). Objetivo: dentro de 2x.
- Reactividad: retardo en meses hasta que un cambio sostenido de la ratio real se
  refleja en al menos el 80% de su magnitud en H, medido sobre series sinteticas de
  escalon con volumen mensual constante.
"""

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

import duckdb

from xray import paths
from xray.marts.common import parquet
from xray.period import FIRST_MONTH, LAST_MONTH

WINDOW = 6
P75_STABILITY_TARGET = 8.0  # C2: p75 de |dH| mensual por debajo de 8 puntos
BAND = (45.0, 55.0)  # C5: banda estrecha que delata aplanamiento
TREND_SNR = 2.0  # C3: |pendiente| > 2 * error tipico => tendencia detectable
TREND_MIN_MONTHS = 12  # C3: meses evaluables minimos por empresa
MIN_PAIR_INDEX = 2  # C2: excluye los indices 0 y 1 de cada empresa (ventana parcial)
DISPERSION_MIN_COMPANIES = 2  # C5: dispersion por corte solo con >= 2 notas
REACTIVITY_FRACTION = 0.8  # reactividad: 80% de la magnitud del cambio


# ---------------------------------------------------------------- motor puro

def quantile(sorted_values, q):
    """Cuantil con interpolacion lineal sobre una lista ya ordenada."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(sorted_values[low])
    weight = position - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def _amount(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: importe invalido')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f'{field}: importe no finito o negativo')
    return result


def prepare_panel(rows):
    """Agrupa filas empresa-mes por empresa en series ordenadas por mes.

    rows: iterable de mappings con company_id, month (date), tiene_actividad_caja y
    los importes C/P/D (None = desconocido, no se imputa). Devuelve
    {company_id: tupla de registros (month, activity, C, P, D)}. No muta rows.
    """
    grouped = {}
    for row in rows:
        company = row['company_id']
        month = row['month']
        if not isinstance(company, str) or not company:
            raise ValueError('company_id invalido')
        if type(month) is not date:
            raise ValueError('month debe ser date')
        key = (company, month)
        if key in grouped:
            raise ValueError(f'clave empresa-mes duplicada: {key}')
        grouped[key] = (month, bool(row.get('tiene_actividad_caja')),
                        _amount(row.get('cobros_operativos_eur'), 'C'),
                        _amount(row.get('pagos_operativos_eur'), 'P'),
                        _amount(row.get('servicio_deuda_eur'), 'D'))
    panel = {}
    for (company, _), record in grouped.items():
        panel.setdefault(company, []).append(record)
    for company, series in panel.items():
        series.sort(key=lambda record: record[0])
    return {company: tuple(series) for company, series in panel.items()}


def determined(record):
    """True si la empresa-mes tiene actividad y C, P y D determinados.

    Un importe desconocido (None) deja la entrada fuera del subconjunto de
    calibracion; no se imputa el punto medio de ningun intervalo.
    """
    _, activity, c, p, d = record
    return bool(activity) and c is not None and p is not None and d is not None


def window_sums(series, i):
    """Sumas de la ventana expansiva/movil de 6 meses que termina en el indice i.

    La ventana son los meses cerrados max(0, i-5)..i. Un mes sin actividad o con
    importe desconocido NO se imputa como cero: solo se suma lo observado.
    Devuelve (C6, P6, D6, n_meses_con_actividad).
    """
    start = max(0, i - (WINDOW - 1))
    c6 = p6 = d6 = 0.0
    n_active = 0
    for record in series[start:i + 1]:
        _, activity, c, p, d = record
        if activity:
            n_active += 1
        if c is not None:
            c6 += c
        if p is not None:
            p6 += p
        if d is not None:
            d6 += d
    return c6, p6, d6, n_active


def cumulative_ratio(series, i):
    """R_hist(m): ratio acumulada de la empresa desde su primer mes observado hasta i.

    Point-in-time por construccion: solo lee series[j] con j <= i. Devuelve None si el
    volumen acumulado observado es 0 (C_hist + T_hist = 0).
    """
    c_hist = t_hist = 0.0
    for record in series[:i + 1]:
        _, _, c, p, d = record
        if c is not None:
            c_hist += c
        if p is not None:
            t_hist += p
        if d is not None:
            t_hist += d
    total = c_hist + t_hist
    if total <= 0:
        return None
    return c_hist / total


def h_value(c6, t6, r_hist, k):
    """Formula v3 pura. Devuelve None si el denominador es 0 o falta R_hist."""
    if c6 is None or t6 is None or r_hist is None:
        return None
    denominator = c6 + t6 + k
    if denominator <= 0:
        return None
    return 100.0 * (c6 + k * r_hist) / denominator


def company_scores(series, k):
    """Serie de H(m,k) de una empresa, un punto por mes del panel.

    H solo es evaluable en empresa-mes del subconjunto de calibracion (C, P y D
    determinados con actividad) y con R_hist definible.
    """
    scores = []
    for i, record in enumerate(series):
        month = record[0]
        c6, p6, d6, n_active = window_sums(series, i)
        t6 = p6 + d6
        r_hist = cumulative_ratio(series, i)
        evaluable = determined(record) and r_hist is not None
        h = h_value(c6, t6, r_hist, k) if evaluable else None
        scores.append({'index': i, 'month': month, 'h': h, 'evaluable': evaluable,
                       'c6': c6, 't6': t6, 'r_hist': r_hist, 'k': k,
                       'n_meses_con_actividad': n_active})
    return scores


def panel_scores(panel, k):
    return {company: company_scores(series, k) for company, series in panel.items()}


def operating_volume(record):
    """Volumen mensual (C + T = C + P + D); None si hay actividad falsa o algun
    componente desconocido."""
    _, activity, c, p, d = record
    if not activity or c is None or p is None or d is None:
        return None
    return c + p + d


# ------------------------------------------------------------- metricas (K2)

def stability(panel_scores):
    """C2: distribucion de |H(m,k) - H(m-1,k)| entre meses consecutivos.

    Excluye los dos primeros meses de cada empresa (indices 0 y 1, ventana parcial):
    solo se computan pares (m-1, m) con ambos indices >= MIN_PAIR_INDEX y con H
    evaluable en los dos meses.
    """
    deltas = []
    for scores in panel_scores.values():
        for previous, current in zip(scores, scores[1:]):
            if current['index'] <= MIN_PAIR_INDEX:
                continue
            if previous['h'] is None or current['h'] is None:
                continue
            deltas.append(abs(current['h'] - previous['h']))
    if not deltas:
        return {'n_pairs': 0, 'median': None, 'p75': None, 'p90': None}
    ordered = sorted(deltas)
    return {'n_pairs': len(deltas), 'median': quantile(ordered, 0.5),
            'p75': quantile(ordered, 0.75), 'p90': quantile(ordered, 0.9)}


def dispersion(panel_scores):
    """C5: dispersion de H entre empresas en cada corte, resumida sobre los cortes.

    Detecta aplanamiento: con k enorme todas las notas tienden a 100*R_hist y la nota
    deja de discriminar. Reporta mediana de la desviacion tipica y del IQR, y cuantas
    empresas caen en la banda estrecha 45-55 (mediana y maximo sobre los cortes).
    """
    cuts = {}
    for scores in panel_scores.values():
        for entry in scores:
            if entry['h'] is not None:
                cuts.setdefault(entry['month'], []).append(entry['h'])
    stds, iqrs, band_counts, cut_sizes = [], [], [], []
    for month in sorted(cuts):
        values = cuts[month]
        if len(values) < DISPERSION_MIN_COMPANIES:
            continue
        ordered = sorted(values)
        cut_sizes.append(len(ordered))
        stds.append(statistics.pstdev(ordered))
        iqrs.append(quantile(ordered, 0.75) - quantile(ordered, 0.25))
        low, high = BAND
        band_counts.append(sum(1 for value in ordered if low <= value <= high))
    if not stds:
        return {'n_cuts': 0, 'median_std': None, 'median_iqr': None, 'std_max': None,
                'iqr_max': None, 'band_45_55_median': None, 'band_45_55_max': None,
                'companies_median': None}
    return {'n_cuts': len(stds), 'median_std': quantile(sorted(stds), 0.5),
            'median_iqr': quantile(sorted(iqrs), 0.5), 'std_max': max(stds),
            'iqr_max': max(iqrs),
            'band_45_55_median': quantile(sorted(band_counts), 0.5),
            'band_45_55_max': max(band_counts),
            'companies_median': quantile(sorted(cut_sizes), 0.5)}


def _ols_slope(values, times):
    """Ajuste lineal OLS; devuelve (pendiente, error tipico de la pendiente)."""
    n = len(values)
    mean_t = sum(times) / n
    mean_v = sum(values) / n
    sxx = sum((t - mean_t) ** 2 for t in times)
    if sxx <= 0:
        raise ValueError('serie sin dispersion temporal; no se puede ajustar')
    sxy = sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values))
    slope = sxy / sxx
    intercept = mean_v - slope * mean_t
    residuals = [v - (intercept + slope * t) for v, t in zip(values, times)]
    mse = sum(residual ** 2 for residual in residuals) / (n - 2) if n > 2 else 0.0
    se = math.sqrt(mse / sxx)
    return slope, se


def trend(panel, k):
    """C3: % de empresas con >= TREND_MIN_MONTHS meses evaluables con tendencia lineal
    detectable en H.

    Criterio declarado: OLS de H contra el indice absoluto de mes; la tendencia es
    detectable cuando |b| > TREND_SNR * se_b (deriva significativa frente al residuo).
    """
    detected = total = 0
    for series in panel.values():
        usable = [entry for entry in company_scores(series, k) if entry['h'] is not None]
        if len(usable) < TREND_MIN_MONTHS:
            continue
        total += 1
        values = [entry['h'] for entry in usable]
        times = [entry['month'].year * 12 + entry['month'].month - 1 for entry in usable]
        slope, se = _ols_slope(values, times)
        if abs(slope) > TREND_SNR * se:
            detected += 1
    return {'n_companies': total, 'n_detected': detected,
            'pct_detectable': 100.0 * detected / total if total else None}


def subset_volume_distribution(panel):
    """Distribucion del volumen mensual (C+T) por empresa-mes del subconjunto."""
    volumes = sorted(volume for series in panel.values()
                     for volume in (operating_volume(record) for record in series)
                     if volume is not None)
    if not volumes:
        raise ValueError('subconjunto de calibracion vacio')
    return volumes


def k_grid(volumes, points=22):
    """Rango de k derivado de los datos, no inventado.

    Parte de la distribucion del volumen mensual (C+T) del subconjunto y barre k en
    escala logaritmica desde p05/100 (muy por debajo del p05) hasta p95*100 (muy por
    encima del p95), mas k = 0 como referencia obligatoria (v3 sin shrinkage).
    Devuelve (lista de k, informacion del rango).
    """
    p05 = quantile(volumes, 0.05)
    p95 = quantile(volumes, 0.95)
    p50 = quantile(volumes, 0.5)
    low = p05 / 100.0
    high = p95 * 100.0
    if low <= 0 or high <= low:
        raise ValueError('distribucion de volumen degenerada; no se puede barrir')
    info = {'p05': p05, 'p50': p50, 'p95': p95, 'k_min': low, 'k_max': high,
            'n_log_points': points}
    step = (math.log10(high) - math.log10(low)) / (points - 1)
    grid = [0.0] + [10.0 ** (math.log10(low) + step * j) for j in range(points)]
    return grid, info


def adverse_bias(panel, known_volumes=None):
    """C4: sesgo adverso del subconjunto de calibracion.

    Compara el volumen mensual mediano (C+T) de las empresa-mes con nota exacta
    (C, P y D determinados) frente a las que quedan en intervalo (algun componente
    desconocido, con actividad). Para estas ultimas se usa el volumen conocido
    (subtotales convertidos a EUR), que es una COTA INFERIOR del volumen real, de modo
    que la ratio reportada es una cota inferior del sesgo. No se imputa nada.
    """
    subset, others = [], []
    for company, series in panel.items():
        for record in series:
            volume = operating_volume(record)
            if volume is not None:
                subset.append(volume)
                continue
            if not record[1]:
                continue  # mes sin actividad: no representa volumen operativo
            if known_volumes is not None:
                proxy = known_volumes.get((company, record[0]))
                if proxy is not None:
                    others.append(proxy)
    if not subset or not others:
        raise ValueError('subconjunto o contraste vacios; no se puede medir el sesgo')
    median_subset = quantile(sorted(subset), 0.5)
    median_others = quantile(sorted(others), 0.5)
    return {'n_subset': len(subset), 'n_others': len(others),
            'median_volume_subset_eur': median_subset,
            'median_volume_others_eur': median_others,
            'ratio_others_over_subset': median_others / median_subset}


def reactivity(k, volume, scenarios=None):
    """Retardo en meses hasta que un cambio sostenido de la ratio real se refleja en
    al menos el 80% de su magnitud en H.

    Metodo declarado: series SINTETICAS DE ESCALON. Una empresa con volumen mensual
    constante `volume` mantiene la ratio r0 con la ventana llena (WINDOW meses) y
    despues cambia de forma sostenida a r1. En regimen estacionario H converge a
    100*r (C6 = WINDOW*r*V y R_hist = r), asi que el 80% de la magnitud del cambio es
    100*(r0 + 0.8*(r1 - r0)); se mide el primer mes posterior al escalon en que H
    alcanza ese umbral.
    """
    if scenarios is None:
        scenarios = tuple((r0, r0 + delta) for r0 in (0.35, 0.45, 0.55)
                          for delta in (-0.25, -0.15, 0.15, 0.25))
    pre = WINDOW  # meses con la ratio inicial y ventana ya llena
    post = 60  # horizonte largo: con k enorme el retardo puede acercarse a 24 meses
    delays = []
    for r0, r1 in scenarios:
        if not 0.0 <= r0 <= 1.0 or not 0.0 <= r1 <= 1.0:
            raise ValueError('ratios del escenario fuera de [0, 1]')
        series = tuple((None, True, ratio * volume, (1.0 - ratio) * volume, 0.0)
                       for ratio in (r0,) * pre + (r1,) * post)
        scores = company_scores(series, k)
        threshold = 100.0 * (r0 + REACTIVITY_FRACTION * (r1 - r0))
        span = threshold - 100.0 * r0
        if span == 0:
            delays.append(0)
            continue
        direction = math.copysign(1.0, span)
        delay = None
        for entry in scores[pre:]:
            if (entry['h'] - threshold) * direction >= 0:
                delay = entry['index'] - (pre - 1)
                break
        if delay is None:
            raise ValueError('el escalon sintetico no converge en el horizonte')
        delays.append(delay)
    return {'method': 'escalon_sintetico_volumen_constante', 'scenarios': len(delays),
            'median_delay_months': quantile(sorted(delays), 0.5),
            'max_delay_months': max(delays), 'volume_eur': volume}


# ------------------------------------------------------------- recomendacion

def recommend(rows):
    """Elige la k que cumple C2 (p75 < 8) con el menor coste en C5 y reactividad.

    Subir k siempre estabiliza (C2 mejora) pero aplana (C5) y retarda (reactividad),
    de modo que entre las k que cumplen el objetivo se elige la MAS PEQUENA. Si
    ninguna cumple, devuelve la de menor p75 y declara el incumplimiento.
    """
    if not rows:
        return None, False
    candidates = [row for row in rows if row['dH_p75'] is not None
                  and row['dH_p75'] < P75_STABILITY_TARGET]
    if candidates:
        return min(candidates, key=lambda row: row['k']), True
    scored = [row for row in rows if row['dH_p75'] is not None]
    if not scored:
        return None, False
    return min(scored, key=lambda row: (row['dH_p75'], row['k'])), False


def summarize_row(k, stability_row, dispersion_row, trend_row, bias_row, reactivity_row):
    return {'k': k, 'dH_median': stability_row['median'], 'dH_p75': stability_row['p75'],
            'dH_p90': stability_row['p90'], 'n_pairs': stability_row['n_pairs'],
            'std_median': dispersion_row['median_std'],
            'std_max': dispersion_row['std_max'],
            'iqr_median': dispersion_row['median_iqr'],
            'iqr_max': dispersion_row['iqr_max'],
            'band_45_55_median': dispersion_row['band_45_55_median'],
            'band_45_55_max': dispersion_row['band_45_55_max'],
            'companies_median': dispersion_row['companies_median'],
            'trend_pct': trend_row['pct_detectable'],
            'trend_n_companies': trend_row['n_companies'],
            'trend_n_detected': trend_row['n_detected'],
            'bias_ratio_others_over_subset': bias_row['ratio_others_over_subset'],
            'reactivity_months': reactivity_row['median_delay_months']}


# ------------------------------------------------------------------ cargador

def load_panel(marts_dir=None):
    """Lee los marts publicados (solo lectura; nunca escribe parquets).

    Devuelve (filas para el motor, volumenes conocidos por empresa-mes). Las entradas
    con tiene_actividad_caja falsa o importes nulos pasan tal cual (None) para que el
    motor decida; el cargador no imputa.
    """
    marts = Path(marts_dir) if marts_dir is not None else paths.MARTS_DIR
    flows = parquet(marts / 'panel_flujos.parquet')
    debt = parquet(marts / 'panel_deuda.parquet')
    con = duckdb.connect()
    try:
        rows = con.execute(f'''
            SELECT fl.company_id, fl.month, fl.tiene_actividad_caja,
                   fl.cobros_operativos_eur, fl.pagos_operativos_eur,
                   de.servicio_deuda_eur,
                   coalesce(fl.cobros_operativos_conocido_eur, 0.0)
                     + coalesce(fl.pagos_operativos_conocido_eur, 0.0)
                     + coalesce(fl.ambiguo_entradas_conocido_eur, 0.0)
                     + coalesce(fl.ambiguo_salidas_conocido_eur, 0.0)
                     + coalesce(de.servicio_deuda_conocido_eur, 0.0)
                     + coalesce(de.financiacion_no_desglosada_conocido_eur, 0.0) AS known_volume
            FROM {flows} fl JOIN {debt} de USING (company_id, month)
            WHERE fl.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
        ''').fetchall()
    finally:
        con.close()
    records = [{'company_id': row[0], 'month': row[1], 'tiene_actividad_caja': row[2],
                'cobros_operativos_eur': row[3], 'pagos_operativos_eur': row[4],
                'servicio_deuda_eur': row[5]} for row in rows]
    known = {(row[0], row[1]): row[6] for row in rows}
    return records, known


# -------------------------------------------------------------------- report

def fmt_value(value, spec):
    return '-' if value is None else spec.format(value)


def fmt_int(value):
    return '-' if value is None else f'{value:.0f}'


def fmt_pct(value):
    return '-' if value is None else f'{value:.1f}%'


def sweep_table(rows):
    header = ('| k (EUR) | |dH| p50 | |dH| p75 | |dH| p90 | std p50 (C5) | IQR p50 (C5) '
              '| empresas 45-55 p50/max | tendencia % (C3) | reactividad (meses) |')
    lines = [header, '|' + '---|' * 9]
    for row in rows:
        k_display = '0 (sin shrinkage)' if row['k'] == 0 else f"{row['k']:.4g}"
        p75_mark = ' **cumple C2**' if row['dH_p75'] is not None and row['dH_p75'] < P75_STABILITY_TARGET else ''
        band = ('-' if row['band_45_55_median'] is None
                else f"{fmt_int(row['band_45_55_median'])}/{fmt_int(row['band_45_55_max'])}")
        reactivity = fmt_value(row['reactivity_months'], '{:.1f}')
        lines.append(
            f"| {k_display}{p75_mark} | {fmt_value(row['dH_median'], '{:.2f}')} "
            f"| {fmt_value(row['dH_p75'], '{:.2f}')} | {fmt_value(row['dH_p90'], '{:.2f}')} "
            f"| {fmt_value(row['std_median'], '{:.2f}')} "
            f"| {fmt_value(row['iqr_median'], '{:.2f}')} | {band} "
            f"| {fmt_pct(row['trend_pct'])} | {reactivity} |")
    return '\n'.join(lines)


def build_report(rows, best, meets, bias, grid_info, reactivity_info):
    k_display = '0 (sin shrinkage)' if best['k'] == 0 else f"{best['k']:.6g}"
    lines = [
        '# Calibracion de k para healthscore_v3 (medicion, no scorer)',
        '',
        'Formula medida: H(m,k) = 100 * (C6 + k*R_hist) / (C6 + T6 + k), con C6/T6 las',
        'sumas de la ventana expansiva hasta 6 meses cerrados max(primer_mes, m-5)..m y',
        'R_hist la ratio acumulada de la propia empresa hasta m. k es un volumen virtual',
        'en euros y el unico parametro libre. H es evaluable solo en empresa-mes del',
        'subconjunto de calibracion (C, P y D determinados y con actividad observada);',
        'la ventana suma unicamente lo observado y un mes sin actividad no se imputa',
        'como cero.',
        '',
        '## Subconjunto de calibracion y su sesgo',
        '',
        f"- Empresa-mes con C, P y D determinados: {bias['n_subset']} de "
        f"{bias['n_subset'] + bias['n_others']} meses con actividad "
        f"({100.0 * bias['n_subset'] / (bias['n_subset'] + bias['n_others']):.1f}%).",
        f"- Volumen mensual mediano (C+T) del subconjunto: "
        f"{bias['median_volume_subset_eur']:,.2f} EUR.",
        f"- Volumen mediano del resto de meses con actividad (cota inferior conocida): "
        f"{bias['median_volume_others_eur']:,.2f} EUR.",
        f"- **Sesgo adverso: el volumen mediano de las empresa-mes con nota es "
        f"{bias['ratio_others_over_subset']:.1f} veces menor** que el de las que quedan",
        '  en intervalo. Como el volumen del resto se mide con los subtotales conocidos',
        '  (cota inferior), esta cifra es una cota inferior del sesgo real.',
        '- **Advertencia de provisionalidad**: este subconjunto esta sesgado hacia',
        '  empresas pequenas y bien clasificadas; la k recomendada es PROVISIONAL y debe',
        '  revalidarse cuando se resuelva la ambiguedad de clasificacion (revision humana',
        '  del paquete label_review). No se ha imputado el punto medio de ningun intervalo.',
        '',
        '## Rango del barrido de k (derivado de los datos)',
        '',
        f"- Volumen mensual (C+T) del subconjunto: p05 = {grid_info['p05']:.4g} EUR, "
        f"p50 = {grid_info['p50']:.4g} EUR, p95 = {grid_info['p95']:.4g} EUR.",
        f"- Barrido logaritmico de {grid_info['n_log_points']} puntos desde "
        f"k = {grid_info['k_min']:.4g} EUR (p05/100, muy por debajo del p05) hasta",
        f"k = {grid_info['k_max']:.4g} EUR (p95*100, muy por encima del p95), mas k = 0",
        '  como referencia obligatoria (k = 0 es v3 sin shrinkage, la ratio de la ventana).',
        '',
        '## Tabla del barrido',
        '',
        'Empresas en banda 45-55: mediana/maximo sobre los cortes con >= 2 notas. La',
        'columna de tendencia usa empresas con >= 12 meses evaluables; la linea base con',
        'mes natural es 22,7%.',
        '',
        sweep_table(rows),
        '',
        '## Recomendacion',
        '',
    ]
    if meets:
        lines += [
            f"**k recomendada = {k_display} EUR.**",
            '',
            f"Cumple C2 (p75 de |dH| = {best['dH_p75']:.2f} < {P75_STABILITY_TARGET:g}) y es la k",
            'mas pequena del barrido que lo hace: la de menor coste en dispersion (C5) y',
            'en reactividad. Sus cifras: |dH| p50 = '
            f"{fmt_value(best['dH_median'], '{:.2f}')}, p90 = "
            f"{fmt_value(best['dH_p90'], '{:.2f}')}; std p50 = "
            f"{fmt_value(best['std_median'], '{:.2f}')}; IQR p50 = "
            f"{fmt_value(best['iqr_median'], '{:.2f}')}; empresas en 45-55 "
            f"{fmt_int(best['band_45_55_median'])}/{fmt_int(best['band_45_55_max'])}; "
            f"tendencia = {fmt_pct(best['trend_pct'])}; reactividad = "
            f"{fmt_value(best['reactivity_months'], '{:.0f}')} meses; sesgo adverso "
            f"(independiente de k) = {best['bias_ratio_others_over_subset']:.1f}x.",
        ]
    else:
        lines += [
            f"**Ninguna k del barrido cumple C2 (p75 de |dH| < {P75_STABILITY_TARGET:g}).**",
            '',
            f"Mejor disponible: k = {k_display} EUR con |dH| p50 = "
            f"{fmt_value(best['dH_median'], '{:.2f}')}, p75 = "
            f"{fmt_value(best['dH_p75'], '{:.2f}')}, p90 = "
            f"{fmt_value(best['dH_p90'], '{:.2f}')}; std p50 = "
            f"{fmt_value(best['std_median'], '{:.2f}')}; IQR p50 = "
            f"{fmt_value(best['iqr_median'], '{:.2f}')}; empresas en 45-55 "
            f"{fmt_int(best['band_45_55_median'])}/{fmt_int(best['band_45_55_max'])}; "
            f"tendencia = {fmt_pct(best['trend_pct'])}; reactividad = "
            f"{fmt_value(best['reactivity_months'], '{:.0f}')} meses.",
            '',
            'No se fuerza una recomendacion que los numeros no sostienen: la estabilidad',
            'mensual exigida no se alcanza ni con shrinkage extremo.',
        ]
    zero = rows[0]
    extreme = rows[-1]
    lines += [
        '',
        '## Compromiso: que se gana y que se pierde al subir k',
        '',
        '| k | |dH| p75 (estabilidad C2) | std p50 (dispersion C5) | IQR p50 | empresas 45-55 p50 | reactividad (meses) |',
        '|---|---|---|---|---|---|',
        f"| k = 0 | {fmt_value(zero['dH_p75'], '{:.2f}')} | "
        f"{fmt_value(zero['std_median'], '{:.2f}')} | "
        f"{fmt_value(zero['iqr_median'], '{:.2f}')} | "
        f"{fmt_int(zero['band_45_55_median'])} | "
        f"{fmt_value(zero['reactivity_months'], '{:.0f}')} |",
        f"| k = {extreme['k']:.4g} | {fmt_value(extreme['dH_p75'], '{:.2f}')} | "
        f"{fmt_value(extreme['std_median'], '{:.2f}')} | "
        f"{fmt_value(extreme['iqr_median'], '{:.2f}')} | "
        f"{fmt_int(extreme['band_45_55_median'])} | "
        f"{fmt_value(extreme['reactivity_months'], '{:.0f}')} |",
        '',
        'Grafico ASCII del compromiso (p75 de |dH| frente a k, escala log):',
        '',
        '```',
        *_stability_ascii(rows),
        '```',
        '',
        'Al subir k: gana estabilidad (baja el p75 de |dH|), pierde dispersion (las notas',
        'se aplanan hacia 100*R_hist, con menor std/IQR entre empresas) y pierde',
        'reactividad (mas meses hasta reflejar un cambio sostenido de la ratio).',
        '',
        '## Reactividad (metodo declarado)',
        '',
        f"Medida sobre series sinteticas de escalon ({reactivity_info['scenarios']} escenarios:",
        'ratios iniciales 0,35/0,45/0,55 con saltos sostenidos de +/-0,15 y +/-0,25,',
        f"volumen mensual constante = {reactivity_info['volume_eur']:.4g} EUR, el mediano del",
        f"subconjunto). Retardo mediano: {reactivity_info['median_delay_months']:.1f} meses; "
        f"peor caso {reactivity_info['max_delay_months']} meses.",
        '',
        '## Notas',
        '',
        '- C2 excluye los dos primeros meses de cada empresa (ventana parcial expansiva).',
        '- C4 no depende de k (la evaluabilidad de H no cambia con k); se reporta igual',
        '  en cada fila para trazabilidad.',
        '- Los criterios exactos estan declarados en la cabecera de xray/calibrate_k.py;',
        '  este modulo solo mide, no implementa el scorer v3.',
    ]
    return '\n'.join(lines) + '\n'


def _stability_ascii(rows, width=48):
    """Barras ASCII del p75 de |dH| por k (escala log de k, longitud proporcional)."""
    usable = [row for row in rows if row['dH_p75'] is not None]
    if not usable:
        return ['(sin pares consecutivos evaluables)']
    top = max(row['dH_p75'] for row in usable)
    lines = []
    for row in usable:
        label = f"k={row['k']:.4g}".ljust(14)
        bar = '#' * max(1, round(width * row['dH_p75'] / top)) if row['dH_p75'] > 0 else '.'
        marker = f"  <- objetivo p75<{P75_STABILITY_TARGET:g} " \
                 f"{'CUMPLE' if row['dH_p75'] < P75_STABILITY_TARGET else 'no cumple'}"
        lines.append(f"{label}|{bar}{marker}")
    return lines


# ----------------------------------------------------------------------- CLI

SWEEP_COLUMNS = ('k', 'dH_median', 'dH_p75', 'dH_p90', 'n_pairs', 'std_median',
                 'std_max', 'iqr_median', 'iqr_max', 'band_45_55_median',
                 'band_45_55_max', 'companies_median', 'trend_pct',
                 'trend_n_companies', 'trend_n_detected',
                 'bias_ratio_others_over_subset', 'reactivity_months')


def run(output=None, marts_dir=None):
    """Ejecuta el barrido completo y escribe sweep.csv y report.md en una ruta nueva."""
    output = Path(output) if output is not None else paths.ROOT / 'reports' / 'calibration_k'
    records, known = load_panel(marts_dir)
    panel = prepare_panel(records)
    volumes = subset_volume_distribution(panel)
    grid, grid_info = k_grid(volumes)
    bias = adverse_bias(panel, known)
    rows = []
    reactivity_rows = []
    for k in grid:
        scores = panel_scores(panel, k)
        stability_row = stability(scores)
        dispersion_row = dispersion(scores)
        trend_row = trend(panel, k)
        reactivity_row = reactivity(k, grid_info['p50'])
        reactivity_rows.append(reactivity_row)
        rows.append(summarize_row(k, stability_row, dispersion_row, trend_row,
                                  bias, reactivity_row))
    best, meets = recommend(rows)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'sweep.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(SWEEP_COLUMNS),
                                extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    (output / 'report.md').write_text(
        build_report(rows, best, meets, bias, grid_info, reactivity_rows[-1]),
        encoding='utf-8')
    summary = {
        'output': str(output),
        'panel_companies': len(panel),
        'panel_months': len({row['month'] for row in records}),
        'subset_months': bias['n_subset'],
        'subset_companies': len([series for series in panel.values()
                                 if any(determined(record) for record in series)]),
        'bias_median_volume_subset_eur': bias['median_volume_subset_eur'],
        'bias_median_volume_others_eur_lower_bound': bias['median_volume_others_eur'],
        'bias_ratio_others_over_subset_lower_bound': bias['ratio_others_over_subset'],
        'k_grid': {'k_min_eur': grid_info['k_min'], 'k_max_eur': grid_info['k_max'],
                   'n_log_points': grid_info['n_log_points'],
                   'volume_p05': grid_info['p05'], 'volume_p50': grid_info['p50'],
                   'volume_p95': grid_info['p95']},
        'recommended_k_eur': None if best is None else best['k'],
        'meets_p75_target': meets,
        'recommended_metrics': None if best is None else {
            key: best[key] for key in ('dH_median', 'dH_p75', 'dH_p90', 'std_median',
                                       'iqr_median', 'band_45_55_median',
                                       'band_45_55_max', 'trend_pct',
                                       'reactivity_months')},
        'sweep_rows': len(rows),
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m xray.calibrate_k',
        description='Mide el parametro libre k de healthscore_v3 (barrido y criterios '
                    'C2-C5); no implementa el scorer.')
    parser.add_argument('--output', type=Path,
                        default=paths.ROOT / 'reports' / 'calibration_k',
                        help='ruta NUEVA bajo reports/; aborta si ya existe')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva para conservar '
                     'las mediciones previas')
    summary = run(args.output)
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, allow_nan=False,
                                sort_keys=True, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
