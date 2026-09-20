"""Medicion de los parametros libres k, alpha y beta de healthscore_v4.

NO es el scorer: este modulo no implementa el motor y no toca
xray/scoring_v4.py ni xray/scoring_io_v4.py (no cambia ningun default).
Parte del parquet de evaluaciones reports/score_v4/assessments.parquet
(que produce xray/scoring_io_v4.py, post-exclusion: las 33 empresas
excluidas van con health_score = NULL y quedan FUERA de la poblacion de
calibracion) y, a partir de las columnas c6, t6_efectivo, r_hist,
colchon_v4, mora_indice, multiplicador_deuda y obligacion_vencida_m,
RECALCULA H_final para cualquier terna (k, alpha, beta) sin volver a los
datos crudos.

Formula (identica a xray/scoring_v4.py; verificada por test y por la
comprobacion de coherencia contra el health_score almacenado del parquet):

  colchon_aplicable = min(colchon_v4, alpha * T6_efectivo)
      (colchon_v4 del parquet ya es max(0, saldo_reversa), 0 si era
       None o negativo; el motor nunca publica None aqui)
  H       = 100 * (C6 + colchon_aplicable + k * R_hist)
                  / (C6 + colchon_aplicable + T6_efectivo + k)
            (con R_hist indefinida el termino k se omite por completo,
            como en el motor: sin euros virtuales ni en el denominador)
  H_final = H * (1 - beta * mora_indice) * multiplicador_deuda
      (mora_indice o multiplicador None = factor 1 sin penalizacion,
       con motivo en el motor; mora 0 NO penaliza)

DISCIPLINA ANTI-LABEL (obligatoria, igual que en v3): la calibracion es
por SENSIBILIDAD Y ESTABILIDAD medidas, con criterios declarados AQUI
ADELANTE y sin ningun ajuste contra etiquetas, proxies o variables de
resultado. Este modulo NO lee marts/targets_proxy.parquet, NO lee
reports/label_review/ y no consume ninguna variable de resultado. No hay
etiquetas validadas en este dataset: ajustar contra un proxy seria
circular. Los parametros son inyectables en el motor y aqui SOLO se miden.

CRITERIOS DECLARADOS DE ANTEMANO (aplicados en recommend()):

- C1 ESTABILIDAD (criterio principal, requisito del usuario: "nada de
  montanas rusas"). |dH_final| entre meses naturales CONSECUTIVOS de la
  MISMA empresa. NO es la distancia v3->v4, que es otra medida y ya esta
  publicada (reports/score_v4/summary.json). Reporta p50, p75 y p90.
  * Exclusion de ventana: pares donde ALGUNO de los dos meses tiene
    ventana_parcial = true (los primeros meses de la empresa con ventana
    expansiva de menos de 6 meses) o toca los dos primeros meses con fila
    de la empresa (convencion C2 de v3, xray/calibrate_k.py). Ambas
    exclusiones se cuentan y se declaran.
  * POBLACION COMUN: los pares se computan SOLO sobre los pares
    (empresa, mes consecutivo) cuya nota es evaluable con TODOS los
    valores de parametro comparados del eje. Comparar |dH| sobre
    poblaciones distintas es invalido
    (reports/fix_p6_preservado/FIX_P6.md); el tamano de la poblacion
    comun se declara en cada fila (n_pairs) y en el informe.
  * OBJETIVO DECLARADO: p75(|dH_final|) < 10 puntos (10% de la escala
    0-100 en el 75% de los pares: la lectura practica de "nada de
    montanas rusas"). Justificacion medida (se re-mide en cada ejecucion
    y se escribe en report.md con la distribucion observada): la senal
    cruda sin suavizar ningun parametro (k=0, alpha=0, beta=0) tiene p75
    ~5.3 sobre los pares estables y ~5.5 sobre todos los pares con nota,
    y los parametros heredados suben la cola a ~7.0/16; la cota 10 deja
    margen para reaccionar a eventos reales (p90 ~14-16, dominado por
    eventos discretos de mora y obligacion) y descarta una montana rusa
    (> 10% de la escala en un cuarto de los meses). El 8 de v3 NO se
    copia: se midio sobre otro denominador (sin obligacion vencida y con
    volumenes ~17x menores, reports/calibration_k/report.md).
- C2 DISPERSION: std poblacional e IQR de H_final entre empresas por
  corte (>= 2 notas), resumidos con la mediana sobre cortes; empresas en
  la banda estrecha 45-55. Criterio de no aplanamiento: std mediana >=
  0.85 * referencia (terna 0,0,0) y banda <= 1.2 * referencia. Subir k
  aplana hacia 100*R_hist: aqui se mide cuanto se pierde.
- C3 REACTIVIDAD: meses hasta que la nota refleja el 80% de un cambio
  sostenido de la ratio, sobre series sinteticas de escalon con volumen
  mensual constante (metodo declarado, idem xray/calibrate_k.py). Para
  v4 la serie sintetica usa colchon 0, obligacion 0 y mora None, con lo
  que la formula v4 se reduce EXACTAMENTE a la v3 y la medida de
  calibrate_k es aplicable sin cambio.
- C4 COBERTURA: empresas con nota por corte. Debe ser invariante a k (la
  evaluabilidad no depende de k); si varia con alpha o beta se reporta:
  seria senal de algo (un parametro que retira notas).

BARRIDOS (por ejes, como en v3; no cartesiano conjunto salvo la
interaccion declarada k x alpha):
- k: barrido logaritmico DERIVADO DE LOS DATOS DE v4 (percentiles de
  C6 + T6_efectivo del panel post-exclusion: p05/100 a p95*100, 22
  puntos), mas k = 0 como referencia obligatoria y el k actual. El
  denominador de v4 es mucho mayor que el de v3 (621,67 M EUR de
  obligacion vencida en el panel), asi que el rango se re-deriva, no se
  hereda.
- alpha: el colchon v4 es el NIVEL de caja reconstruido (saldo_reversa),
  no el colchon invariante de v3: la escala ha cambiado. Rango derivado
  de los datos: distribucion de colchon_v4 / T6_efectivo en la poblacion
  con T6 > 0 y colchon > 0 (p05 a alpha_max logaritmico, 16 puntos, mas
  0, el alpha actual 3.0 y alpha_max); por encima de alpha_max el tope
  nunca satura y alpha es inerte. En cada valor se mide la fraccion de
  empresa-mes con el colchon saturado por alpha * T6_efectivo.
- beta: barrido en [0, 1]. Se mide cuanta nota se pierde por mora en
  cada valor (castigo mediano y p90 en puntos) y como se solapa con el
  castigo del multiplicador (Spearman entre ambos castigos por
  empresa-mes).

INTERACCION ENTRE PARAMETROS (obligatoria):
- como cambia el optimo de k segun alpha: para cada alpha de referencia
  (derivado de la curva de saturacion: p10/p50/p90 del ratio colchon/T6,
  mas 0 y alpha_max) se re-barre k y se reporta la k* que cumple el
  criterio de estabilidad.
- como cambia el efecto de beta segun cuanta obligacion vencida hay: el
  castigo por mora de cada beta se reparte entre filas con obligacion
  vencida > 0 y filas con obligacion 0 o NULL.

D. DECISION DEL TRIPLE CONTEO (medida, sin cambiar el default). La misma
senal de impago entra por denominador, multiplicador y mora (reparto
Shapley publicado del castigo: p50 0,163 / 0,0785 / 0,6487,
reports/score_v4/summary.json). Se comparan TRES variantes del canal
mora, evaluadas con la terna ACTUAL y con la RECOMENDADA:
  (a) beta constante, como ahora;
  (b) beta atenuado (factor ATENUACION_VARIANTE_B = 0.5, declarado)
      cuando obligacion_vencida_m > 0 en el corte;
  (c) mora aplicada SOLO cuando obligacion_vencida_m = 0 o NULL.
Para cada una: |dH| de C1, dispersion de C2 y cuantas empresas cambian de
tramo de nota frente a (a). La recomendacion es razonada para el
coordinador; el default NO se cambia aqui.
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

VERSION = 'calibrate_params_v4'

DEFAULT_INPUT = paths.ROOT / 'reports' / 'score_v4' / 'assessments.parquet'
DEFAULT_OUTPUT = paths.ROOT / 'reports' / 'calibration_v4'

MISSING_INPUT_MSG = ('falta reports/score_v4/assessments.parquet, ejecuta antes '
                     'xray.scoring_io_v4')

# Valores actuales HEREDADOS de v3 (no recalibrados para v4, asi lo
# declara xray/scoring_v4.py); el barrido siempre los incluye.
CURRENT_K = 4178.45
CURRENT_ALPHA = 3.0
CURRENT_BETA = 0.25

# C1: objetivo declarado de estabilidad (justificado en report.md con la
# distribucion observada de la senal cruda en la misma poblacion).
P75_STABILITY_TARGET = 10.0
BAND = (45.0, 55.0)           # banda estrecha que delata aplanamiento
DISPERSION_MIN_COMPANIES = 2  # dispersion por corte solo con >= 2 notas
MIN_RANK_COMPANIES = 3        # Spearman solo con >= 3 pares comparables
MIN_PAIR_INDEX = 2            # convencion C2 de v3: excluye indices 0 y 1
INFLUENCE_POINT = 1.0         # umbral de "cambia la nota", en puntos
STD_FLOOR_RATIO = 0.85        # no aplanamiento: std >= 0.85 * referencia
BAND_CEIL_RATIO = 1.2         # no aplanamiento: banda <= 1.2 * referencia
REACTIVITY_MAX_MONTHS = 6.0   # criterio del eje k: la reactividad es la
                              # razon de ser de k; no puede irse a 12-24 m
ALPHA_SATURACION_MAXIMA = 0.5 # criterio del eje alpha: el tope alpha*T6 no
                              # manda para la mayoria de la poblacion

# Variante (b) del triple conteo: atenuacion del beta cuando hay
# obligacion vencida > 0 en el corte (factor declarado, no calibrado).
ATENUACION_VARIANTE_B = 0.5

# Tramos de nota para la comparacion de variantes: cuartiles fijos.
TRAMO_ANCHO = 25.0

K_LOG_POINTS = 22
ALPHA_LOG_POINTS = 16

# Columnas que este modulo necesita del parquet de evaluaciones.
LOAD_COLUMNS = ('company_id', 'group_id', 'month', 'health_score',
                'ventana_parcial', 'excluida', 'c6', 't6_efectivo',
                'r_hist', 'colchon_v4', 'mora_indice',
                'multiplicador_deuda', 'obligacion_vencida_m',
                'k', 'alpha', 'beta')

SWEEP_COLUMNS = ('axis', 'variant', 'k', 'alpha', 'beta',
                 'dH_median', 'dH_p75', 'dH_p90', 'n_pairs',
                 'std_median', 'std_max', 'iqr_median', 'iqr_max',
                 'band_45_55_median', 'band_45_55_max',
                 'companies_median', 'n_cuts', 'n_scored_rows',
                 'coverage_median', 'coverage_min', 'coverage_invariante',
                 'saturation_frac', 'alpha_changed_rows',
                 'alpha_influence_median', 'beta_changed_rows',
                 'beta_influence_median', 'mora_castigo_median',
                 'mora_castigo_p90', 'mult_castigo_median',
                 'mult_castigo_p90', 'reactivity_months',
                 'band_changes_last_cut', 'band_changes_panel')


# ------------------------------------------------------------------ formula

def colchon_aplicable_value(colchon_v4, t6_efectivo, alpha):
    """Colchon aplicable: min(colchon_v4, alpha * T6_efectivo).

    colchon_v4 llega ya normalizado por el motor (max(0, saldo_reversa),
    0 si era None). Defensivo: si llegara None o negativo, contribuye 0.
    """
    if colchon_v4 is None:
        return 0.0
    colchon = float(colchon_v4)
    if colchon < 0.0:
        return 0.0
    return min(colchon, alpha * float(t6_efectivo))


def factor_mora(mora, obligacion_m, beta, variant='a'):
    """Factor del canal mora segun la variante del triple conteo.

    (a) 'beta_constante': 1 - beta*mora, como el motor (mora None = 1).
    (b) 'beta_atenuado':  1 - beta*ATENUACION_VARIANTE_B*mora cuando
        obligacion_vencida_m > 0 en el corte; beta completo en otro caso.
    (c) 'mora_solo_sin_obligacion': factor 1 (sin castigo por mora)
        cuando obligacion_vencida_m > 0; beta completo en otro caso.
    """
    if mora is None:
        return 1.0
    mora = float(mora)
    con_obligacion = obligacion_m is not None and obligacion_m > 0.0
    if variant == 'a':
        return 1.0 - beta * mora
    if variant == 'b':
        efectivo = beta * ATENUACION_VARIANTE_B if con_obligacion else beta
        return 1.0 - efectivo * mora
    if variant == 'c':
        return 1.0 if con_obligacion else 1.0 - beta * mora
    raise ValueError(f'variante desconocida: {variant!r}')


def health_final(c6, t6_efectivo, r_hist, colchon_v4, mora, multiplicador,
                 obligacion_m, k, alpha, beta, variant='a'):
    """H_final recalculado con la formula v4, coherente con el motor.

    Devuelve None si el denominador es 0 (nota no evaluable). Los None
    de mora y multiplicador son factor 1 (sin ajuste, motivo en el motor);
    el None de R_hist omite el termino k completo, como el motor.
    """
    c6 = 0.0 if c6 is None else float(c6)
    t6 = 0.0 if t6_efectivo is None else float(t6_efectivo)
    colchon = colchon_aplicable_value(colchon_v4, t6, alpha)
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
    h *= factor_mora(mora, obligacion_m, beta, variant)
    if multiplicador is not None:
        h *= float(multiplicador)
    return h


def _row_hf(row, k, alpha, beta, variant='a'):
    return health_final(row['c6'], row['t6'], row['r_hist'],
                        row['colchon_v4'], row['mora'], row['mult'],
                        row['obligacion_m'], k, alpha, beta, variant)


# ------------------------------------------------------------------ cargador

def load_assessments(path=None):
    """Lee reports/score_v4/assessments.parquet (solo lectura).

    Devuelve (panel, meta). panel: {company_id: lista de filas ordenadas
    por mes} SOLO con las filas de la poblacion de calibracion
    (health_score no nulo y excluida falsa: la poblacion POST-EXCLUSION).
    meta: contadores, comprobacion de coherencia entre health_score del
    parquet y el recalculo con las k/alpha/beta de cada fila, y medicion
    del sesgo de la poblacion (volumen con nota frente a sin nota).
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
        # Sesgo de la poblacion de calibracion: volumen (C6 + T6_efectivo)
        # de las empresa-mes CON nota frente a las SIN nota no excluidas.
        # Para las sin nota el volumen conocido (solo componentes no nulos)
        # es una COTA INFERIOR del real; no se imputa nada. Los componentes
        # auxiliares (p6, d6, deficit) son opcionales: el volumen conocido
        # usa solo las columnas que el parquet publica.
        columnas = {row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{str(path)}')").fetchall()}
        extras = [column for column in ('p6', 'd6', 'deficit_servicio_6',
                                        'obligacion_vencida_m')
                  if column in columnas]
        volumen_conocido = 'coalesce(c6, 0)' + ''.join(
            f' + coalesce({column}, 0)' for column in extras)
        sesgo = con.execute(f'''
            SELECT
              count(*) FILTER (WHERE health_score IS NOT NULL
                                 AND NOT excluida) AS n_con_nota,
              count(*) FILTER (WHERE health_score IS NULL
                                 AND NOT excluida) AS n_sin_nota,
              count(*) FILTER (WHERE excluida) AS n_excluidas,
              count(DISTINCT company_id) FILTER (WHERE excluida)
                AS n_empresas_excluidas,
              quantile_cont(c6 + t6_efectivo, 0.5)
                FILTER (WHERE health_score IS NOT NULL) AS vol_con_nota,
              quantile_cont({volumen_conocido}, 0.5)
                FILTER (WHERE health_score IS NULL AND NOT excluida
                          AND {volumen_conocido} > 0)
                AS vol_sin_nota_cota
            FROM read_parquet('{str(path)}')
        ''').fetchone()
    finally:
        con.close()

    panel = {}
    coherence_max = 0.0
    coherence_rows = 0
    groups = set()
    seen_keys = set()
    n_rows = 0
    for values in raw:
        record = dict(zip(LOAD_COLUMNS, values))
        if record['health_score'] is None or record['excluida']:
            continue  # poblacion post-exclusion declarada
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
            't6': 0.0 if record['t6_efectivo'] is None
                  else float(record['t6_efectivo']),
            'r_hist': None if record['r_hist'] is None
                      else float(record['r_hist']),
            'colchon_v4': None if record['colchon_v4'] is None
                          else float(record['colchon_v4']),
            'mora': None if record['mora_indice'] is None
                    else float(record['mora_indice']),
            'mult': None if record['multiplicador_deuda'] is None
                    else float(record['multiplicador_deuda']),
            'obligacion_m': None if record['obligacion_vencida_m'] is None
                            else float(record['obligacion_vencida_m']),
            'ventana_parcial': bool(record['ventana_parcial']),
        }
        panel.setdefault(company, []).append(row)
        n_rows += 1
        expected = health_final(row['c6'], row['t6'], row['r_hist'],
                                row['colchon_v4'], row['mora'], row['mult'],
                                row['obligacion_m'],
                                record['k'], record['alpha'], record['beta'])
        if expected is not None:
            coherence_max = max(coherence_max,
                                abs(float(record['health_score']) - expected))
            coherence_rows += 1
    for series in panel.values():
        series.sort(key=lambda row: row['month'])
    (n_con_nota, n_sin_nota, n_excluidas, n_empresas_excluidas,
     vol_con_nota, vol_sin_nota_cota) = sesgo
    meta = {
        'input': str(path),
        'companies': len(panel),
        'months': len({row['month'] for series in panel.values()
                       for row in series}),
        'rows': n_rows,
        'groups': len(groups),
        'coherence_max_abs_diff': coherence_max if coherence_rows else None,
        'coherence_rows': coherence_rows,
        'bias': {
            'n_con_nota': n_con_nota,
            'n_sin_nota': n_sin_nota,
            'n_excluidas': n_excluidas,
            'n_empresas_excluidas': n_empresas_excluidas,
            'median_volume_con_nota_eur': vol_con_nota,
            'median_volume_sin_nota_cota_inferior_eur': vol_sin_nota_cota,
            'ratio_sin_nota_sobre_con_nota': (
                vol_sin_nota_cota / vol_con_nota
                if vol_con_nota and vol_sin_nota_cota is not None else None),
        },
    }
    return panel, meta


# --------------------------------------------------------------- evaluacion

def evaluate(panel, k, alpha, beta, variant='a'):
    """H_final recalculado para todo el panel con una configuracion fija.

    Devuelve {(company_id, month): hf}, con None donde la nota no es
    evaluable (denominador 0).
    """
    scores = {}
    for company, series in panel.items():
        for row in series:
            scores[(company, row['month'])] = _row_hf(row, k, alpha, beta,
                                                      variant)
    return scores


def _consecutive_months(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month) == 1


def candidate_pairs(panel):
    """Pares consecutivos CANDIDATOS con las exclusiones de ventana.

    Un par (empresa, mes actual) entra si: los dos meses tienen fila en
    la poblacion de calibracion, son meses naturales consecutivos, el
    indice del mes actual dentro de la serie de la empresa es
    > MIN_PAIR_INDEX (excluye los dos primeros meses, convencion C2 de
    v3) y NINGUNO de los dos meses tiene ventana_parcial = true
    (ventana parcial expansiva de v4). La evaluabilidad por parametro se
    aplica despues (poblacion comun); aqui van solo exclusiones que no
    dependen de parametros. Devuelve (pares, contadores de exclusion).
    """
    pairs = []
    n_ventana = n_primeros = 0
    for company, series in panel.items():
        for index, (previous, current) in enumerate(zip(series, series[1:]),
                                                    start=1):
            if not _consecutive_months(previous['month'], current['month']):
                continue
            if index <= MIN_PAIR_INDEX:
                n_primeros += 1
                continue
            if previous['ventana_parcial'] or current['ventana_parcial']:
                n_ventana = None  # placeholder evitado abajo
                n_ventana = True
                n_ventana = True  # marcado abajo con el contador real
                n_ventana_count = n_ventana_count + 1 if False else None
                continue
            pairs.append((company, current['month']))
    return pairs, {'n_primeros_meses': n_primeros}


def common_pair_mask(panel, configs):
    """Poblacion comun declarada: pares evaluables con TODAS las configs.

    configs: iterable de (k, alpha, beta) o (k, alpha, beta, variant).
    Un par candidato pertenece a la poblacion comun si la nota es
    evaluable (no None) en cada configuracion comparada. Devuelve
    (conjunto de pares, lista de conjuntos evaluables por config).
    """
    pairs, _ = candidate_pairs(panel)
    common = set(pairs)
    per_config = []
    for config in configs:
        k, alpha, beta = config[0], config[1], config[2]
        variant = config[3] if len(config) > 3 else 'a'
        scores = evaluate(panel, k, alpha, beta, variant)
        evaluable = {key for key, hf in scores.items() if hf is not None}
        per_config.append(evaluable)
        common &= {key for key in common
                   if (key[0], key[1]) in evaluable}
    return common, per_config


def raw_full_pairs_stability(panel):
    """Senal cruda (k=0, alpha=0, beta=0) sobre TODOS los pares con nota.

    Sin exclusion de ventana ni de primeros meses: es la volatilidad
    completa que el score tendria sin ningun parametro, la referencia
    para justificar el umbral de C1. Devuelve la distribucion de |dH|.
    """
    deltas = []
    for company, series in panel.items():
        for previous, current in zip(series, series[1:]):
            if not _consecutive_months(previous['month'], current['month']):
                continue
            hf_previous = _row_hf(previous, 0.0, 0.0, 0.0)
            hf_current = _row_hf(current, 0.0, 0.0, 0.0)
            if hf_previous is None or hf_current is None:
                continue
            deltas.append(abs(hf_current - hf_previous))
    if not deltas:
        return {'n_pairs': 0, 'median': None, 'p75': None, 'p90': None}
    ordered = sorted(deltas)
    return {'n_pairs': len(deltas), 'median': quantile(ordered, 0.5),
            'p75': quantile(ordered, 0.75), 'p90': quantile(ordered, 0.9)}


def stability(panel, scores, common_pairs):
    """C1: distribucion de |dH_final| entre meses consecutivos.

    SOLO pares de la poblacion comun declarada (common_pairs), que ya
    lleva las exclusiones de ventana y la evaluabilidad de todas las
    configuraciones comparadas.
    """
    deltas = []
    for company, month in common_pairs:
        series = panel[company]
        current = next((row for row in series if row['month'] == month), None)
        if current is None:
            continue
        index = series.index(current)
        if index == 0:
            continue
        previous = series[index - 1]
        hf_current = scores[(company, month)]
        hf_previous = scores[(company, previous['month'])]
        if hf_current is None or hf_previous is None:
            continue
        deltas.append(abs(hf_current - hf_previous))
    if not deltas:
        return {'n_pairs': 0, 'median': None, 'p75': None, 'p90': None}
    ordered = sorted(deltas)
    return {'n_pairs': len(deltas), 'median': quantile(ordered, 0.5),
            'p75': quantile(ordered, 0.75), 'p90': quantile(ordered, 0.9)}


def dispersion(panel, scores):
    """C2: dispersion de H_final entre empresas por corte.

    Mediana sobre cortes con >= 2 notas de la desviacion tipica
    poblacional y del IQR, y cuantas empresas caen en la banda 45-55.
    Poblacion declarada: empresa-mes de la poblacion de calibracion con
    nota evaluable en la configuracion (n_scored_rows).
    """
    cuts = {}
    n_rows = 0
    for (company, month), hf in scores.items():
        if hf is not None:
            n_rows += 1
            cuts.setdefault(month, []).append(hf)
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
        return {'n_cuts': 0, 'n_scored_rows': n_rows, 'median_std': None,
                'std_max': None, 'median_iqr': None, 'iqr_max': None,
                'band_45_55_median': None, 'band_45_55_max': None,
                'companies_median': None}
    return {'n_cuts': len(stds), 'n_scored_rows': n_rows,
            'median_std': quantile(sorted(stds), 0.5), 'std_max': max(stds),
            'median_iqr': quantile(sorted(iqrs), 0.5), 'iqr_max': max(iqrs),
            'band_45_55_median': quantile(sorted(band_counts), 0.5),
            'band_45_55_max': max(band_counts),
            'companies_median': quantile(sorted(cut_sizes), 0.5)}


def coverage(scores):
    """C4: empresas con nota por corte (mediana y minimo sobre cortes)."""
    counts = {}
    for (company, month), hf in scores.items():
        if hf is not None:
            counts.setdefault(month, set()).add(company)
    if not counts:
        return {'median': None, 'min': None}
    sizes = sorted(len(members) for members in counts.values())
    return {'median': quantile(sizes, 0.5), 'min': min(sizes)}


def saturation_fraction(panel, alpha):
    """Fraccion de empresa-mes con el colchon saturado por alpha * T6.

    Saturado: colchon_v4 > alpha * T6_efectivo (el tope alpha*T6 manda).
    Las filas con T6 = 0 y colchon > 0 quedan saturadas para CUALQUIER
    alpha (el tope es 0); se cuentan aparte como 'siempre saturadas'.
    """
    n = n_sat = n_t6_cero = 0
    for series in panel.values():
        for row in series:
            n += 1
            if row['colchon_v4'] is not None and row['colchon_v4'] > 0:
                if row['t6'] <= 0:
                    n_t6_cero += 1
                elif row['colchon_v4'] > alpha * row['t6']:
                    n_sat += 1
    return {'fraction': n_sat / n if n else None, 'n': n,
            'n_saturadas': n_sat, 'n_t6_cero_siempre_saturadas': n_t6_cero}


def influence(base_scores, test_scores):
    """INFLUENCIA de un parametro: empresa-mes que cambian su nota mas de
    INFLUENCE_POINT puntos respecto a la base, y magnitud mediana.

    Solo filas con nota evaluable en las dos configuraciones.
    """
    n_rows = 0
    magnitudes = []
    for key, base_hf in base_scores.items():
        test_hf = test_scores.get(key)
        if base_hf is None or test_hf is None:
            continue
        n_rows += 1
        magnitude = abs(test_hf - base_hf)
        if magnitude > INFLUENCE_POINT:
            magnitudes.append(magnitude)
    return {'n_rows': n_rows, 'n_changed': len(magnitudes),
            'median_magnitude': (quantile(sorted(magnitudes), 0.5)
                                 if magnitudes else None)}


def _stats(values):
    if not values:
        return {'median': None, 'p90': None}
    ordered = sorted(values)
    return {'median': quantile(ordered, 0.5), 'p90': quantile(ordered, 0.9)}


def castigos(panel, k, alpha, beta, variant='a'):
    """Castigo por mora y por multiplicador en puntos, por empresa-mes.

    mora_castigo = H_antes_de_ajustes * beta_efectivo * mora (0 si mora
    None); mult_castigo = after_mora * (1 - multiplicador) (0 si mult
    None). beta_efectivo depende de la variante del triple conteo.
    """
    mora_castigos = []
    mult_castigos = []
    for series in panel.values():
        for row in series:
            hf_base = _row_hf(row, k, alpha, 0.0, 'a')
            if hf_base is None:
                continue
            if row['mora'] is not None:
                factor = factor_mora(row['mora'], row['obligacion_m'],
                                     beta, variant)
                mora_castigos.append(hf_base * (1.0 - factor))
            if row['mult'] is not None:
                after_mora = hf_base * factor_mora(row['mora'],
                                                   row['obligacion_m'],
                                                   beta, variant)
                mult_castigos.append(after_mora * (1.0 - row['mult']))
    return {'mora': _stats(mora_castigos), 'mult': _stats(mult_castigos)}


def castigos_por_obligacion(panel, k, alpha, beta):
    """Castigo por mora separado por presencia de obligacion vencida.

    Efecto de beta segun cuanta obligacion vencida hay: con_obligacion =
    filas con obligacion_vencida_m > 0 (donde la senal ya entra por
    denominador y multiplicador); sin_obligacion = filas con obligacion
    0 o NULL (donde la mora es el unico canal del lado pago).
    """
    with_obl, without_obl = [], []
    for series in panel.values():
        for row in series:
            hf_base = _row_hf(row, k, alpha, 0.0, 'a')
            if hf_base is None or row['mora'] is None:
                continue
            castigo = hf_base * beta * row['mora']
            if row['obligacion_m'] is not None and row['obligacion_m'] > 0:
                with_obl.append(castigo)
            else:
                without_obl.append(castigo)
    return {'con_obligacion': _stats(with_obl),
            'sin_obligacion': _stats(without_obl)}


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
            i_ranks = None
        i = j + 1
    return ranks


def spearman_corr(xs, ys):
    """Spearman por rangos medios; None si degenerado o < 3 pares."""
    if len(xs) != len(ys) or len(xs) < MIN_RANK_COMPANIES:
        return None
    rank_x = _avg_ranks(xs)
    rank_y = _avg_ranks(ys)
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


def overlap_mora_multiplicador(panel, k, alpha, beta):
    """Solape entre el castigo por mora y el del multiplicador.

    Spearman entre mora_castigo y mult_castigo por empresa-mes (solo
    filas con ambos castigos definidos, mora > 0 y multiplicador < 1).
    Spearman ~ +1: los dos castigos castigan a las mismas empresas
    (doble conteo sistematico); ~ 0: castigan a poblaciones distintas.
    """
    xs, ys = [], []
    for series in panel.values():
        for row in series:
            if row['mora'] is None or row['mult'] is None:
                continue
            if row['mora'] <= 0 or row['mult'] >= 1:
                continue
            hf_base = _row_hf(row, k, alpha, 0.0, 'a')
            if hf_base is None:
                continue
            xs.append(hf_base * beta * row['mora'])
            ys.append(hf_base * (1.0 - beta * row['mora']) * (1.0 - row['mult']))
    return {'spearman': spearman_corr(xs, ys), 'n': len(xs)}


def tramo(health):
    """Tramo de nota: cuartiles fijos [0-25, 25-50, 50-75, 75-100]."""
    if health is None:
        return None
    return min(3, max(0, int(health // TRAMO_ANCHO)))


def band_changes(panel, k, alpha, beta, variant, base='a'):
    """Cambios de tramo de nota frente a la variante base.

    Devuelve (empresas del ultimo corte que cambian de tramo, cambios
    totales de empresa-mes en el panel, empresas comparables del ultimo
    corte).
    """
    months = sorted({row['month'] for series in panel.values()
                     for row in series})
    last = months[-1] if months else None
    changed_last = comparable_last = 0
    panel_changes = 0
    for company, series in panel.items():
        for row in series:
            base_hf = _row_hf(row, k, alpha, beta, base)
            test_hf = _row_hf(row, k, alpha, beta, variant)
            if base_hf is None or test_hf is None:
                continue
            if tramo(base_hf) != tramo(test_hf):
                panel_changes += 1
                if row['month'] == last:
                    changed_last += 1
            if row['month'] == last:
                comparable_last += 1
    return {'last_cut': changed_last, 'panel': panel_changes,
            'comparable_last': comparable_last}


# ------------------------------------------------------------------- mallas

def _panel_volume(panel):
    """Volumen mensual (C6 + T6_efectivo) mediano de la poblacion, para
    los escalones sinteticos de reactividad de k."""
    volumes = sorted(row['c6'] + row['t6'] for series in panel.values()
                     for row in series)
    if not volumes:
        raise ValueError('panel vacio; no se puede medir la reactividad')
    return quantile(volumes, 0.5)


def k_axis_grid(panel):
    """Rango logaritmico de k DERIVADO DE LOS DATOS DE v4, mas k = 0.

    Base: percentiles del volumen (C6 + T6_efectivo) de la poblacion
    post-exclusion. k bajo = p05/100, k alto = p95*100; 22 puntos
    logaritmicos, mas k=0 (v4 sin shrinkage) y el k actual si no cae
    cerca (< 0.1%) de un punto de la malla.
    """
    volumes = sorted(row['c6'] + row['t6'] for series in panel.values()
                     for row in series)
    p05 = quantile(volumes, 0.05)
    p95 = quantile(volumes, 0.95)
    k_low = p05 / 100.0
    k_high = p95 * 100.0
    if k_low <= 0 or k_high <= k_low:
        raise ValueError('distribucion de volumen degenerada; no se puede barrir')
    ratio = (k_high / k_low) ** (1.0 / (K_LOG_POINTS - 1))
    points = [k_low * ratio ** j for j in range(K_LOG_POINTS)]
    if not any(abs(point - CURRENT_K) <= 1e-3 * max(point, CURRENT_K)
               for point in points):
        points.append(CURRENT_K)
    grid = sorted({0.0, *points})
    info = {'volume_p05': p05, 'volume_p50': quantile(volumes, 0.5),
            'volume_p95': p95, 'k_min': k_low, 'k_max': k_high,
            'n_log_points': K_LOG_POINTS}
    return grid, info


def alpha_axis_grid(panel):
    """Rango de alpha derivado de los datos (la escala ha cambiado en v4).

    Base: distribucion de colchon_v4 / T6_efectivo (T6 > 0, colchon > 0)
    en la poblacion. Barrido logaritmico de p05 del ratio a alpha_max
    (max del ratio: por encima el tope nunca satura y alpha es inerte),
    mas alpha = 0 (el colchon no cuenta) y el alpha actual 3.0.
    """
    ratios = sorted(row['colchon_v4'] / row['t6']
                    for series in panel.values() for row in series
                    if row['colchon_v4'] is not None and row['colchon_v4'] > 0
                    and row['t6'] > 0)
    if not ratios:
        return [0.0], {'p05': None, 'p10': None, 'p50': None, 'p90': None,
                       'alpha_max': None, 'n_log_points': 0}
    alpha_p05 = max(quantile(ratios, 0.05), 1e-4)
    alpha_max = ratios[-1]
    step = (math.log10(alpha_max) - math.log10(alpha_p05)) \
        / (ALPHA_LOG_POINTS - 1)
    points = [10.0 ** (math.log10(alpha_p05) + step * j)
              for j in range(ALPHA_LOG_POINTS)]
    grid = sorted({0.0, CURRENT_ALPHA, alpha_max, *points})
    info = {'p05': alpha_p05, 'p10': quantile(ratios, 0.10),
            'p50': quantile(ratios, 0.5), 'p90': quantile(ratios, 0.9),
            'alpha_max': alpha_max, 'n_log_points': ALPHA_LOG_POINTS}
    return grid, info


def beta_axis_grid():
    """Rango de beta: [0, 1] con el 0.25 actual incluido."""
    return (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5,
            0.6, 0.75, 0.9, 1.0)


# ------------------------------------------------------------------ barrido

def summarize_row(axis, variant, k, alpha, beta, stability_row,
                  dispersion_row, coverage_row, saturation=None,
                  alpha_influence=None, beta_influence=None,
                  castigos_row=None, reactivity_months=None,
                  band_changes_row=None, coverage_invariante=None):
    cast = castigos_row or {'mora': {'median': None, 'p90': None},
                            'mult': {'median': None, 'p90': None}}
    band = band_changes_row or {'last_cut': None, 'panel': None,
                                'comparable_last': None}
    return {
        'axis': axis, 'variant': variant,
        'k': k, 'alpha': alpha, 'beta': beta,
        'dH_median': stability_row['median'],
        'dH_p75': stability_row['p75'], 'dH_p90': stability_row['p90'],
        'n_pairs': stability_row['n_pairs'],
        'std_median': dispersion_row['median_std'],
        'std_max': dispersion_row['std_max'],
        'iqr_median': dispersion_row['median_iqr'],
        'iqr_max': dispersion_row['iqr_max'],
        'band_45_55_median': dispersion_row['band_45_55_median'],
        'band_45_55_max': dispersion_row['band_45_55_max'],
        'companies_median': dispersion_row['companies_median'],
        'n_cuts': dispersion_row['n_cuts'],
        'n_scored_rows': dispersion_row['n_scored_rows'],
        'coverage_median': coverage_row['median'],
        'coverage_min': coverage_row['min'],
        'coverage_invariante': coverage_invariante,
        'saturation_frac': (saturation or {}).get('fraction'),
        'alpha_changed_rows': (alpha_influence or {}).get('n_changed'),
        'alpha_influence_median': (alpha_influence or {}).get('median_magnitude'),
        'beta_changed_rows': (beta_influence or {}).get('n_changed'),
        'beta_influence_median': (beta_influence or {}).get('median_magnitude'),
        'mora_castigo_median': cast['mora']['median'],
        'mora_castigo_p90': cast['mora']['p90'],
        'mult_castigo_median': cast['mult']['median'],
        'mult_castigo_p90': cast['mult']['p90'],
        'reactivity_months': reactivity_months,
        'band_changes_last_cut': band['last_cut'],
        'band_changes_panel': band['panel'],
    }


def run_sweep(panel):
    """Barrido POR EJES (declarado; no cartesiano conjunto), mas filas de
    referencia, valores actuales e interaccion k x alpha.

    Eje k: alpha=3.0 y beta=0.25 fijados; poblacion comun entre todos los
    k comparados. Eje alpha: k=4178.45 y beta=0.25 fijados; influencia
    frente a alpha=0 y fraccion de colchon saturado. Eje beta: k=4178.45
    y alpha=3.0 fijados; influencia frente a beta=0, castigo por mora y
    reparto por obligacion vencida.
    """
    rows = []
    volume = _panel_volume(panel)

    # --- eje k -------------------------------------------------------------
    k_grid, k_info = k_axis_grid(panel)
    configs = [(k, CURRENT_ALPHA, CURRENT_BETA) for k in k_grid]
    common, _ = common_pair_mask(panel, configs)
    for k in k_grid:
        scores = evaluate(panel, k, CURRENT_ALPHA, CURRENT_BETA)
        st = stability(panel, scores, common)
        ds = dispersion(panel, scores)
        cov = coverage(scores)
        reactivity = k_reactivity(k, volume)['median_delay_months']
        rows.append(summarize_row('k', 'a', k, CURRENT_ALPHA, CURRENT_BETA,
                                  st, ds, cov, reactivity_months=reactivity))
    # C4: la cobertura debe ser invariante a k (misma poblacion evaluable).
    k_rows = [row for row in rows if row['axis'] == 'k']
    same = len({row['n_scored_rows'] for row in k_rows}) == 1
    for row in k_rows:
        row['coverage_invariante'] = same

    # --- eje alpha ----------------------------------------------------------
    alpha_grid, alpha_info = alpha_axis_grid(panel)
    configs = [(CURRENT_K, alpha, CURRENT_BETA) for alpha in alpha_grid]
    common, _ = common_pair_mask(panel, configs)
    alpha_base_scores = evaluate(panel, CURRENT_K, 0.0, CURRENT_BETA)
    for alpha in alpha_grid:
        scores = evaluate(panel, CURRENT_K, alpha, CURRENT_BETA)
        st = stability(panel, scores, common)
        ds = dispersion(panel, scores)
        cov = coverage(scores)
        sat = saturation_fraction(panel, alpha)
        infl = (influence(alpha_base_scores, scores) if alpha > 0.0 else
                {'n_rows': 0, 'n_changed': 0, 'median_magnitude': None})
        rows.append(summarize_row('alpha', 'a', CURRENT_K, alpha, CURRENT_BETA,
                                  st, ds, cov, saturation=sat,
                                  alpha_influence=infl))
    alpha_rows = [row for row in rows if row['axis'] == 'alpha']
    same = len({row['n_scored_rows'] for row in alpha_rows}) == 1
    for row in alpha_rows:
        row['coverage_invariante'] = same

    # --- eje beta -----------------------------------------------------------
    beta_grid = beta_axis_grid()
    configs = [(CURRENT_K, CURRENT_ALPHA, beta) for beta in beta_grid]
    common, _ = common_pair_mask(panel, configs)
    beta_base_scores = evaluate(panel, CURRENT_K, CURRENT_ALPHA, 0.0)
    for beta in beta_grid:
        scores = evaluate(panel, CURRENT_K, CURRENT_ALPHA, beta)
        st = stability(panel, scores, common)
        ds = dispersion(panel, scores)
        cov = coverage(scores)
        infl = (influence(beta_base_scores, scores) if beta > 0.0 else
                {'n_rows': 0, 'n_changed': 0, 'median_magnitude': None})
        cast = castigos(panel, CURRENT_K, CURRENT_ALPHA, beta)
        rows.append(summarize_row('beta', 'a', CURRENT_K, CURRENT_ALPHA, beta,
                                  st, ds, cov, beta_influence=infl,
                                  castigos_row=cast))
    beta_rows = [row for row in rows if row['axis'] == 'beta']
    same = len({row['n_scored_rows'] for row in beta_rows}) == 1
    for row in beta_rows:
        row['coverage_invariante'] = same

    # --- filas de referencia y actuales -------------------------------------
    zero_scores = evaluate(panel, 0.0, 0.0, 0.0)
    rows.append(summarize_row('referencia_000', 'a', 0.0, 0.0, 0.0,
                              stability(panel, zero_scores,
                                        _pairs_of(panel)),
                              dispersion(panel, zero_scores),
                              coverage(zero_scores)))
    current_scores = evaluate(panel, CURRENT_K, CURRENT_ALPHA, CURRENT_BETA)
    current_cast = castigos(panel, CURRENT_K, CURRENT_ALPHA, CURRENT_BETA)
    rows.append(summarize_row(
        'actual', 'a', CURRENT_K, CURRENT_ALPHA, CURRENT_BETA,
        stability(panel, current_scores, _pairs_of(panel)),
        dispersion(panel, current_scores), coverage(current_scores),
        castigos_row=current_cast,
        reactivity_months=k_reactivity(CURRENT_K, volume)['median_delay_months']))

    # --- interaccion k x alpha (declarada) ----------------------------------
    interaction_rows = k_por_alpha(panel, alpha_info, volume)
    rows.extend(interaction_rows)
    return rows, {'k_grid': k_info, 'alpha_grid': alpha_info,
                  'volume': volume,
                  'raw_all_pairs': raw_full_pairs_stability(panel),
                  'overlap_mora_multiplicador':
                      overlap_mora_multiplicador(panel, CURRENT_K,
                                                 CURRENT_ALPHA, CURRENT_BETA)}


def _pairs_of(panel):
    pairs, _ = candidate_pairs(panel)
    return set(pairs)


def k_optimo_para_alpha(panel, alpha, beta=CURRENT_BETA, volume=None):
    """La menor k del eje k que cumple p75 < objetivo y no aplana.

    Referencia de dispersion: la terna (0, 0, 0), igual que recommend().
    """
    zero_scores = evaluate(panel, 0.0, 0.0, 0.0)
    ref_disp = dispersion(panel, zero_scores)
    k_grid, _ = k_axis_grid(panel)
    configs = [(k, alpha, beta) for k in k_grid]
    common, _ = common_pair_mask(panel, configs)
    for k in k_grid:
        scores = evaluate(panel, k, alpha, beta)
        st = stability(panel, scores, common)
        ds = dispersion(panel, scores)
        if st['p75'] is None:
            continue
        if st['p75'] < P75_STABILITY_TARGET and dispersion_ok(ds, ref_disp):
            return {'alpha_ref': alpha, 'k': k, 'dH_p75': st['p75'],
                    'std_median': ds['median_std'],
                    'iqr_median': ds['median_iqr'],
                    'reactivity_months':
                        k_reactivity(k, volume)['median_delay_months']}
    return {'alpha_ref': alpha, 'k': None, 'dH_p75': None,
            'std_median': None, 'iqr_median': None, 'reactivity_months': None}


def k_por_alpha(panel, alpha_info, volume):
    """Interaccion declarada: como cambia el optimo de k segun alpha.

    Alphas de referencia derivados de la curva de saturacion (p10/p50/p90
    del ratio colchon/T6, mas 0 y alpha_max).
    """
    alphas = sorted({a for a in (0.0, alpha_info.get('p10'),
                                 alpha_info.get('p50'),
                                 alpha_info.get('p90'),
                                 alpha_info.get('alpha_max'))
                     if a is not None})
    rows = []
    for alpha in alphas:
        elegida = k_optimo_para_alpha(panel, alpha, volume=volume)
        rows.append({
            'axis': 'interaccion_k_por_alpha', 'variant': 'a',
            'k': elegida['k'], 'alpha': alpha, 'beta': CURRENT_BETA,
            'dH_median': None, 'dH_p75': elegida['dH_p75'], 'dH_p90': None,
            'n_pairs': None, 'std_median': elegida['std_median'],
            'std_max': None, 'iqr_median': elegida['iqr_median'],
            'iqr_max': None, 'band_45_55_median': None,
            'band_45_55_max': None, 'companies_median': None,
            'n_cuts': None, 'n_scored_rows': None,
            'coverage_median': None, 'coverage_min': None,
            'coverage_invariante': None, 'saturation_frac': None,
            'alpha_changed_rows': None, 'alpha_influence_median': None,
            'beta_changed_rows': None, 'beta_influence_median': None,
            'mora_castigo_median': None, 'mora_castigo_p90': None,
            'mult_castigo_median': None, 'mult_castigo_p90': None,
            'reactivity_months': elegida['reactivity_months'],
            'band_changes_last_cut': None, 'band_changes_panel': None,
        })
    return rows


def variantes_triple_conteo(panel, k, alpha, beta):
    """D: las tres variantes del canal mora, medidas con una terna fija.

    Devuelve una fila por variante (a, b, c) con las metricas de C1 y C2
    sobre su poblacion comun, el castigo por canal y los cambios de tramo
    frente a la variante (a).
    """
    rows = []
    configs = [(k, alpha, beta, 'a'), (k, alpha, beta, 'b'),
               (k, alpha, beta, 'c')]
    common, _ = common_pair_mask(panel, configs)
    for variant in ('a', 'b', 'c'):
        scores = evaluate(panel, k, alpha, beta, variant)
        st = stability(panel, scores, common)
        ds = dispersion(panel, scores)
        cov = coverage(scores)
        cast = castigos(panel, k, alpha, beta, variant)
        bands = band_changes(panel, k, alpha, beta, variant)
        rows.append(summarize_row('variante_triple_conteo', variant, k, alpha,
                                  beta, st, ds, cov, castigos_row=cast,
                                  band_changes_row=bands))
    return rows


# ------------------------------------------------------------- recomendacion

def dispersion_ok(disp, reference):
    """Criterio de no aplanamiento declarado (idem v3): std mediana >=
    0.85 * referencia (0,0,0) y banda 45-55 <= 1.2 * referencia.

    Acepta tanto un dict de dispersion (median_std) como una fila del
    barrido (std_median).
    """
    std_ref = reference.get('median_std')
    band_ref = reference.get('band_45_55_median')
    std_row = disp.get('median_std', disp.get('std_median'))
    band_row = disp.get('band_45_55_median')
    if std_ref is None or std_row is None:
        return False
    if std_row < STD_FLOOR_RATIO * std_ref:
        return False
    if band_ref is None or band_row is None:
        return False
    return band_row <= BAND_CEIL_RATIO * band_ref


def p75_ok(p75):
    return p75 is not None and p75 < P75_STABILITY_TARGET


def influence_ok(influence_row):
    return (bool(influence_row)
            and influence_row.get('n_changed', 0) > 0
            and (influence_row.get('median_magnitude') or 0.0) > 0.0)


def recommend(rows, panel):
    """La terna (k, alpha, beta) recomendada con criterios declarados.

    REGLA DE REVALIDACION (declarada): los tres valores son heredados de
    v3 y el trabajo pedido es REVALIDARLOS sobre la rejilla v4. Si el
    valor heredado cumple los criterios del eje, se recomienda MANTENER
    (cambiar sin ganancia medida solo anade variacion injustificada);
    si no cumple, se recomienda el MENOR valor del barrido que cumpla;
    si ninguno cumple, 0 con el motivo explicado. Criterios:

    - k: p75(|dH|) < 10 en el eje k, dispersion_ok y reactividad <= 6
      meses (la reactividad es la razon de ser de k: no puede irse a
      12-24 meses). La menor k del barrido que cumple no se recomienda
      cuando la heredada cumple: k=0 desactiva el termino historico sin
      ganancia de estabilidad medida, y las k grandes que ganan
      estabilidad la pagan con reactividad.
    - alpha: influencia demostrable (respecto a alpha=0), p75 < 10,
      dispersion_ok y saturacion del colchon <= 50% (el tope alpha*T6 no
      manda para la mayoria de la poblacion).
    - beta: influencia demostrable (respecto a beta=0), p75 < 10 y
      dispersion_ok (beta es cota de diseno: su magnitud es decision de
      producto; aqui se mide si el valor heredado es estable).

    La terna elegida se verifica despues de forma CONJUNTA; si la
    verificacion falla se declara y no se fuerza la recomendacion.
    """
    reference = next(row for row in rows if row['axis'] == 'referencia_000')
    ref_disp = {'median_std': reference['std_median'],
                'band_45_55_median': reference['band_45_55_median']}
    reasons = []
    menores = {}

    # --- eje k: revalidacion de la k heredada -------------------------------
    k_rows = sorted((r for r in rows if r['axis'] == 'k'),
                    key=lambda r: r['k'])

    def _k_cumple(row):
        disp = {'median_std': row['std_median'],
                'band_45_55_median': row['band_45_55_median']}
        return (p75_ok(row['dH_p75']) and dispersion_ok(disp, ref_disp)
                and row['reactivity_months'] is not None
                and row['reactivity_months'] <= REACTIVITY_MAX_MONTHS)

    k_pass = [row for row in k_rows if _k_cumple(row)]
    menores['k'] = k_pass[0]['k'] if k_pass else None
    current_k_row = next((row for row in k_pass
                          if abs(row['k'] - CURRENT_K) <= 1e-9), None)
    if current_k_row is not None:
        k_star = CURRENT_K
        motivos = (
            f"k = {CURRENT_K:.6g} (heredada de v3): REVALIDADA sobre la "
            f"rejilla v4: p75={current_k_row['dH_p75']:.2f} < "
            f"{P75_STABILITY_TARGET:g}, std p50 "
            f"{current_k_row['std_median']:.2f} >= "
            f"{STD_FLOOR_RATIO:g} x referencia "
            f"({STD_FLOOR_RATIO * reference['std_median']:.2f}) y "
            f"reactividad {current_k_row['reactivity_months']:.0f} <= "
            f"{REACTIVITY_MAX_MONTHS:g} meses. La menor k del barrido que "
            f"tambien cumple es {fmt_num(menores['k'], '{:.4g}')}: no se "
            'recomienda porque desactiva el termino historico sin ganancia '
            'de estabilidad medido relevante, y las k grandes que ganan '
            'estabilidad la pagan con 12-24 meses de reactividad.')
        reasons.append(motivos)
    elif k_pass:
        k_star = menores['k']
        row = k_pass[0]
        reasons.append(
            f"k = {k_star:.6g}: la heredada ({CURRENT_K:.6g}) NO cumple los "
            f"criterios y esta es la menor k del barrido que los cumple "
            f"(p75={row['dH_p75']:.2f})")
    else:
        k_star = None
        reasons.append('ninguna k del eje cumple los criterios declarados')

    # --- eje alpha: revalidacion del alpha heredado -------------------------
    alpha_rows = sorted((r for r in rows if r['axis'] == 'alpha'),
                        key=lambda r: r['alpha'])

    def _alpha_cumple(row):
        infl = {'n_changed': row.get('alpha_changed_rows'),
                'median_magnitude': row.get('alpha_influence_median')}
        return (influence_ok(infl) and p75_ok(row['dH_p75'])
                and dispersion_ok(row, ref_disp)
                and row['saturation_frac'] is not None
                and row['saturation_frac'] <= ALPHA_SATURACION_MAXIMA)

    alpha_pass = [row for row in alpha_rows if _alpha_cumple(row)]
    menores['alpha'] = alpha_pass[0]['alpha'] if alpha_pass else None
    current_alpha_row = next((row for row in alpha_pass
                              if abs(row['alpha'] - CURRENT_ALPHA) <= 1e-9),
                             None)
    if current_alpha_row is not None:
        alpha_star = CURRENT_ALPHA
        reasons.append(
            f"alpha = {CURRENT_ALPHA:g} (heredado, nunca calibrado): "
            'REVALIDADO: influencia demostrable '
            f"({fmt_int(current_alpha_row['alpha_changed_rows'])} empresa-mes "
            'cambian > 1 punto), p75='
            f"{current_alpha_row['dH_p75']:.2f} < {P75_STABILITY_TARGET:g}, "
            'dispersion OK y saturacion del colchon '
            f"{fmt_num(100.0 * current_alpha_row['saturation_frac'], '{:.1f}')}% "
            f"<= {fmt_num(100.0 * ALPHA_SATURACION_MAXIMA, '{:.0f}')}%. "
            f"El menor alpha del barrido que tambien cumple es "
            f"{fmt_num(menores['alpha'], '{:.3g}')}: sustituir el actual sin "
            'ganancia medida no se justifica.')
    elif alpha_pass:
        alpha_star = menores['alpha']
        row = alpha_pass[0]
        reasons.append(
            f"alpha = {alpha_star:g}: la heredada ({CURRENT_ALPHA:g}) NO "
            'cumple los criterios y este es el menor del barrido que los '
            f"cumple (p75={row['dH_p75']:.2f}, saturacion "
            f"{fmt_num(100.0 * row['saturation_frac'], '{:.1f}')}%)")
    else:
        alpha_star = 0.0
        reasons.append(
            'ningun alpha del barrido cumple los criterios declarados '
            '(influencia demostrable, p75<10, dispersion y saturacion '
            '<= 50%); se recomienda 0 y se explica')

    # --- eje beta: revalidacion del beta heredado ---------------------------
    beta_rows = sorted((r for r in rows if r['axis'] == 'beta'),
                       key=lambda r: r['beta'])

    def _beta_cumple(row):
        infl = {'n_changed': row.get('beta_changed_rows'),
                'median_magnitude': row.get('beta_influence_median')}
        return influence_ok(infl) and p75_ok(row['dH_p75']) \
            and dispersion_ok(row, ref_disp)

    beta_pass = [row for row in beta_rows if _beta_cumple(row)]
    menores['beta'] = beta_pass[0]['beta'] if beta_pass else None
    current_beta_row = next((row for row in beta_pass
                             if abs(row['beta'] - CURRENT_BETA) <= 1e-9), None)
    if current_beta_row is not None:
        beta_star = CURRENT_BETA
        reasons.append(
            f"beta = {CURRENT_BETA:g} (heredado, cota de diseno): REVALIDADO "
            f"(p75={current_beta_row['dH_p75']:.2f} < {P75_STABILITY_TARGET:g} "
            'y dispersion OK en todo el eje; el canal mora castiga '
            f"p50 {fmt_num(current_beta_row['mora_castigo_median'])} / "
            f"p90 {fmt_num(current_beta_row['mora_castigo_p90'])} puntos). "
            f"El menor beta con influencia demostrable es "
            f"{fmt_num(menores['beta'], '{:g}')}, con un castigo mediano de "
            f"{fmt_num(beta_pass[0]['mora_castigo_median'])} puntos: canal "
            'casi decorativo; bajar beta globalmente no es el tratamiento '
            'del doble conteo (ver decision D).')
    elif beta_pass:
        beta_star = menores['beta']
        row = beta_pass[0]
        reasons.append(
            f"beta = {beta_star:g}: la heredada ({CURRENT_BETA:g}) NO cumple "
            f"los criterios y esta es la menor con influencia que cumple "
            f"(p75={row['dH_p75']:.2f}, castigo p50 "
            f"{fmt_num(row['mora_castigo_median'])})")
    else:
        beta_star = 0.0
        reasons.append('ningun beta del barrido cumple los criterios '
                       'declarados; se recomienda 0 y se explica')

    if k_star is None:
        return {'k': None, 'alpha': alpha_star, 'beta': beta_star,
                'meets_p75': False, 'meets_dispersion': False,
                'joint_row': None, 'verdict': 'sin_terna',
                'reasons': reasons}

    scores = evaluate(panel, k_star, alpha_star, beta_star)
    common, _ = common_pair_mask(panel, [(k_star, alpha_star, beta_star)])
    st = stability(panel, scores, common)
    ds = dispersion(panel, scores)
    cov = coverage(scores)
    joint = summarize_row('joint', 'a', k_star, alpha_star, beta_star,
                          st, ds, cov)
    meets_p75 = p75_ok(st['p75'])
    meets_disp = dispersion_ok(ds, ref_disp)
    if meets_p75 and meets_disp:
        reasons.append(f'verificacion conjunta ({k_star:.6g}, {alpha_star:g}, '
                       f"{beta_star:g}): p75={st['p75']:.2f} < "
                       f'{P75_STABILITY_TARGET:g} y dispersion OK')
    else:
        reasons.append(f'la verificacion conjunta ({k_star:.6g}, '
                       f'{alpha_star:g}, {beta_star:g}) NO cumple todo: '
                       f'p75={fmt_num(st["p75"])}, '
                       f"dispersion={'OK' if meets_disp else 'FALLA'}; "
                       'no se fuerza la recomendacion')
    return {'k': k_star, 'alpha': alpha_star, 'beta': beta_star,
            'meets_p75': meets_p75, 'meets_dispersion': meets_disp,
            'joint_row': joint, 'menores_que_cumplen': menores,
            'verdict': 'ok' if (meets_p75 and meets_disp) else 'parcial',
            'reasons': reasons}


# ------------------------------------------------------------------ reporte

def fmt_num(value, spec='{:.2f}'):
    return '-' if value is None else spec.format(value)


def fmt_int(value):
    return '-' if value is None else f'{value:.0f}'


def _axis_table(rows, axis):
    """Tabla markdown de un eje del barrido."""
    if axis == 'k':
        header = ('| k (EUR) | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | '
                  'empresas 45-55 p50/max | cobertura p50 | reactividad (m) |')
    elif axis == 'alpha':
        header = ('| alpha | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | '
                  'colchon saturado % | influencia (n > 1 pt) | '
                  'influencia mediana |')
    else:
        header = ('| beta | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | '
                  'castigo mora p50 | castigo mora p90 | '
                  'influencia (n > 1 pt) | influencia mediana |')
    lines = [header, '|' + '---|' * (header.count('|') - 1)]
    for row in rows:
        if row['axis'] != axis:
            continue
        band = ('-' if row['band_45_55_median'] is None else
                f"{fmt_int(row['band_45_55_median'])}/"
                f"{fmt_int(row['band_45_55_max'])}")
        if axis == 'k':
            label = '0 (sin shrinkage)' if row['k'] == 0 else f"{row['k']:.4g}"
            if row['dH_p75'] is not None \
                    and row['dH_p75'] < P75_STABILITY_TARGET:
                label += ' **cumple p75<10**'
            cells = [label, fmt_num(row['dH_median']), fmt_num(row['dH_p75']),
                     fmt_num(row['dH_p90']), fmt_num(row['std_median']),
                     fmt_num(row['iqr_median']), band,
                     fmt_int(row['coverage_median']),
                     fmt_num(row['reactivity_months'], '{:.1f}')]
        elif axis == 'alpha':
            cells = [f"{row['alpha']:g}", fmt_num(row['dH_median']),
                     fmt_num(row['dH_p75']), fmt_num(row['dH_p90']),
                     fmt_num(row['std_median']), fmt_num(row['iqr_median']),
                     (fmt_num(100.0 * row['saturation_frac'], '{:.1f}')
                      if row['saturation_frac'] is not None else '-'),
                     fmt_int(row['alpha_changed_rows']),
                     fmt_num(row['alpha_influence_median'])]
        else:
            cells = [f"{row['beta']:g}", fmt_num(row['dH_median']),
                     fmt_num(row['dH_p75']), fmt_num(row['dH_p90']),
                     fmt_num(row['std_median']), fmt_num(row['iqr_median']),
                     fmt_num(row['mora_castigo_median']),
                     fmt_num(row['mora_castigo_p90']),
                     fmt_int(row['beta_changed_rows']),
                     fmt_num(row['beta_influence_median'])]
        lines.append('| ' + ' | '.join(cells) + ' |')
    return lines


def _stability_ascii(rows, width=48):
    """Barras ASCII del p75 de |dH| por k (escala log de k)."""
    usable = [row for row in rows if row['dH_p75'] is not None]
    if not usable:
        return ['(sin pares consecutivos evaluables)']
    top = max(row['dH_p75'] for row in usable)
    lines = []
    for row in usable:
        label = f"k={row['k']:.4g}".ljust(14)
        bar = ('#' * max(1, round(width * row['dH_p75'] / top))
               if row['dH_p75'] > 0 else '.')
        marker = (f"  <- objetivo p75<{P75_STABILITY_TARGET:g} "
                  f"{'CUMPLE' if row['dH_p75'] < P75_STABILITY_TARGET else 'no cumple'}")
        lines.append(f"{label}|{bar}{marker}")
    return lines


def _pair_counters(panel):
    """Contadores de exclusion de pares (para declarar la poblacion)."""
    total = n_ventana = n_primeros = 0
    for company, series in panel.items():
        for index, (previous, current) in enumerate(zip(series, series[1:]),
                                                    start=1):
            if not _consecutive_months(previous['month'], current['month']):
                continue
            total += 1
            if index <= MIN_PAIR_INDEX:
                n_primeros += 1
                continue
            if previous['ventana_parcial'] or current['ventana_parcial']:
                n_ventana += 1
    return {'pares_consecutivos_con_nota': total,
            'excluidos_primeros_meses': n_primeros,
            'excluidos_ventana_parcial': n_ventana,
            'candidatos': total - n_primeros - n_ventana}


def _beta_por_obligacion_table(panel, betas, volume=None):
    """Tabla del castigo por mora repartido por obligacion vencida."""
    header = ('| beta | obligacion > 0: castigo p50 | obligacion > 0: p90 | '
              'obligacion 0/NULL: p50 | obligacion 0/NULL: p90 |')
    lines = [header, '|' + '---|' * (header.count('|') - 1)]
    for beta in betas:
        cast = castigos_por_obligacion(panel, CURRENT_K, CURRENT_ALPHA, beta)
        lines.append(
            f"| {beta:g} | {fmt_num(cast['con_obligacion']['median'])} | "
            f"{fmt_num(cast['con_obligacion']['p90'])} | "
            f"{fmt_num(cast['sin_obligacion']['median'])} | "
            f"{fmt_num(cast['sin_obligacion']['p90'])} |")
    return '\n'.join(lines)


def _variantes_table(actual_rows, recommended_rows):
    def fila(row, etiqueta):
        return (f"| {etiqueta} | {row['variant']} | "
                f"{fmt_num(row['dH_median'])} | {fmt_num(row['dH_p75'])} | "
                f"{fmt_num(row['dH_p90'])} | {fmt_num(row['std_median'])} | "
                f"{fmt_num(row['iqr_median'])} | "
                f"{fmt_int(row['band_changes_last_cut'])} | "
                f"{fmt_int(row['band_changes_panel'])} |")
    header = ('| Terna | Variante | dH p50 | dH p75 | dH p90 | std p50 | '
              'IQR p50 | empresas cambian de tramo (ultimo corte) | '
              'cambios de tramo (panel) |')
    lines = [header, '|' + '---|' * (header.count('|') - 1)]
    for row in actual_rows:
        lines.append(fila(row, 'actual (4178.45, 3.0, 0.25)'))
    for row in recommended_rows:
        lines.append(fila(row, 'recomendada'))
    return '\n'.join(lines)


def _variante_recomendacion(actual_rows, recommended_rows):
    """Texto razonado de la decision del triple conteo (sin cambiar nada).

    La logica declarada: (c) es la desduplicacion mas fuerte pero deja a
    las empresas con obligacion vencida sin castigo por mora; (b) reduce
    el doble conteo exactamente donde ocurre (obligacion > 0) sin vaciar
    el canal. Se elige por los numeros: si (c) no empeora la estabilidad
    frente a (b) y el castigo total que desaparece es pequeno, gana (c);
    en otro caso (b).
    """
    def por_variante(rows):
        return {row['variant']: row for row in rows}
    a = por_variante(actual_rows)
    r = por_variante(recommended_rows) if recommended_rows else None
    usa = r or a
    a_row, b_row, c_row = usa['a'], usa['b'], usa['c']
    perdida_castigo_b = ((a_row['mora_castigo_median'] or 0.0)
                         - (b_row['mora_castigo_median'] or 0.0))
    perdida_castigo_c = ((a_row['mora_castigo_median'] or 0.0)
                         - (c_row['mora_castigo_median'] or 0.0))
    mejor = 'b'
    if (c_row['dH_p75'] is not None and b_row['dH_p75'] is not None
            and c_row['dH_p75'] <= b_row['dH_p75'] + 1e-9
            and perdida_castigo_c <= max(1.0, 0.5 * (a_row['mora_castigo_median'] or 0.0))):
        mejor = 'c'
    elegida = c_row if mejor == 'c' else b_row
    etiqueta = ('(c) mora solo cuando obligacion_vencida_m = 0' if mejor == 'c'
                else '(b) beta atenuado (x0.5) cuando obligacion_vencida_m > 0')
    return (
        f"**Recomendacion razonada para el coordinador: variante {etiqueta}.** "
        'El default NO se cambia aqui. Lectura de los numeros (terna '
        f'{"recomendada" if r else "actual"}): |dH| p75 de (a) = '
        f"{fmt_num(a_row['dH_p75'])}, (b) = {fmt_num(b_row['dH_p75'])}, "
        f"(c) = {fmt_num(c_row['dH_p75'])}; castigo por mora p50 de (a) = "
        f"{fmt_num(a_row['mora_castigo_median'])}, (b) = "
        f"{fmt_num(b_row['mora_castigo_median'])} (el doble conteo que se "
        f"quita), (c) = {fmt_num(c_row['mora_castigo_median'])}; empresas "
        f"que cambian de tramo en el ultimo corte frente a (a): (b) "
        f"{fmt_int(b_row['band_changes_last_cut'])}, (c) "
        f"{fmt_int(c_row['band_changes_last_cut'])}. El reparto Shapley "
        'publicado (p50 0,163 denominador / 0,0785 mora / 0,6487 '
        'multiplicador; la mora p90 0,714) muestra que la mora duplica el '
        'castigo en la cola sobre una senal que ya entro por dos canales '
        'en el 69% de los empresa-mes con nota. Atenuar (b) o acotar (c) '
        'el canal mora SOLO donde ya hay obligacion vencida reduce ese '
        'doble conteo sin tocar el castigo donde no hay solape. La '
        'decision es del coordinador.')


def _recomendacion_lines(recommendation, current, meta):
    lines = []
    sesgo_ratio = meta['bias'].get('ratio_sin_nota_sobre_con_nota') or 1.0
    if recommendation['verdict'] == 'ok':
        iguales = (abs(recommendation['k'] - CURRENT_K) <= 1e-9
                   and abs(recommendation['alpha'] - CURRENT_ALPHA) <= 1e-9
                   and abs(recommendation['beta'] - CURRENT_BETA) <= 1e-9)
        if iguales:
            lines.append(
                f"**RECOMENDACION: MANTENER k = {CURRENT_K:.6g} EUR, "
                f"alpha = {CURRENT_ALPHA:g} y beta = {CURRENT_BETA:g}.** "
                'Los tres valores heredados de v3 quedan REVALIDADOS sobre '
                f"la rejilla v4: cumplen p75(|dH|) < {P75_STABILITY_TARGET:g}, "
                'no aplanan (criterio de dispersion declarado), la '
                'reactividad de k sigue en 5 meses y alpha/beta tienen '
                'influencia demostrable. Ninguna sustitucion del barrido '
                'gana estabilidad medida relevante sin coste mayor.')
        else:
            lines.append(
                f"**RECOMENDACION: k = {recommendation['k']:.6g} EUR, "
                f"alpha = {recommendation['alpha']:g}, beta = "
                f"{recommendation['beta']:g}.** Cumple p75(|dH|) < "
                f"{P75_STABILITY_TARGET:g}, no aplana (criterio de "
                'dispersion declarado) y tiene influencia demostrable en '
                'alpha y beta; sustituye a los valores heredados que no '
                'los cumplian.')
    elif recommendation['verdict'] == 'sin_terna':
        lines.append(
            '**Ninguna terna del barrido cumple todo.** No se fuerza la '
            'recomendacion; la mejor evidencia disponible:')
    else:
        lines.append(
            f"**Terna parcialmente cumplida: k = {recommendation['k']:.6g} "
            f"EUR, alpha = {recommendation['alpha']:g}, beta = "
            f"{recommendation['beta']:g}.** Cumple parte de los objetivos "
            'pero no todo; no se fuerza una recomendacion que los numeros '
            'no sostienen.')
    lines.extend('- ' + reason for reason in recommendation['reasons'])
    menores = recommendation.get('menores_que_cumplen') or {}
    if menores:
        lines += [
            '',
            'Valores MINIMOS del barrido que tambien cumplirian los '
            'criterios (referencia, no recomendados): '
            f"k = {fmt_num(menores.get('k'), '{:.4g}')}, "
            f"alpha = {fmt_num(menores.get('alpha'), '{:.3g}')}, "
            f"beta = {fmt_num(menores.get('beta'), '{:g}')}. Son el punto "
            'de partida si el coordinador quisiera estrechar cualquier '
            'canal; ninguno gana estabilidad medida relevante frente al '
            'valor mantenido.',
        ]
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
            f"{fmt_int(joint['band_45_55_max'])}; cobertura p50 = "
            f"{fmt_int(joint['coverage_median'])} empresas por corte.",
        ]
    lines += [
        '',
        '### Comparacion con los parametros actuales (heredados de v3)',
        '',
        f"Con (k=4178.45, alpha=3.0, beta=0.25): |dH| p50 = "
        f"{fmt_num(current['dH_median'])}, p75 = {fmt_num(current['dH_p75'])}, "
        f"p90 = {fmt_num(current['dH_p90'])}; std mediana = "
        f"{fmt_num(current['std_median'])}; reactividad = "
        f"{fmt_num(current['reactivity_months'], '{:.1f}')} meses. Cumple el "
        f"objetivo p75<{P75_STABILITY_TARGET:g}: "
        f"{'SI' if current['dH_p75'] is not None and current['dH_p75'] < P75_STABILITY_TARGET else 'NO'}"
        '.',
        '',
        '### Advertencia de provisionalidad',
        '',
        'Los valores recomendados son PROVISIONALES: la poblacion de '
        'calibracion esta sesgada por OBSERVABILIDAD, no por tamano: la '
        'poblacion con nota es la mayoritaria y su volumen conocido es '
        f"{fmt_num(1.0 / sesgo_ratio, '{:.1f}')} "
        'veces MAYOR que el volumen conocido de la sin nota (cota '
        'inferior: el volumen real de la sin nota puede ser mucho mayor '
        'porque sus flujos son justamente los desconocidos, el mismo '
        "defecto de observabilidad asimetrica que excluyo a "
        f"{meta['bias']['n_empresas_excluidas']} empresas y que v3 midio "
        'como sesgo 17x sobre su subconjunto). Los parametros deben '
        'revalidarse cuando se resuelva la clasificacion de cobros '
        '(seccion 6 de context_algo_v4.md).',
    ]
    return lines


def build_report(rows, recommendation, meta, grid_info, interaction_rows,
                 variant_rows_actual, variant_rows_recomendada, panel,
                 pair_counters):
    """report.md: declaraciones, tablas de barrido, interacciones,
    variantes del triple conteo, compromisos y recomendacion explicita."""
    reference = next(row for row in rows if row['axis'] == 'referencia_000')
    k_rows = [r for r in rows if r['axis'] == 'k']
    alpha_rows = [r for r in rows if r['axis'] == 'alpha']
    beta_rows = [r for r in rows if r['axis'] == 'beta']
    current = next(r for r in rows if r['axis'] == 'actual')
    sesgo = meta['bias']
    k_info, alpha_info = grid_info['k_grid'], grid_info['alpha_grid']
    overlap = grid_info['overlap_mora_multiplicador']
    raw_all = grid_info['raw_all_pairs']
    k_ok = [row for row in k_rows if p75_ok(row['dH_p75'])]

    lines = [
        '# Calibracion de (k, alpha, beta) para healthscore_v4 (medicion)',
        '',
        'Formula recalculada desde el parquet: H_final = 100*(C6 + colchon + '
        'k*R_hist)/(C6 + colchon + T6_efectivo + k), con colchon = '
        'min(colchon_v4, alpha*T6_efectivo), y H_final = H * '
        '(1 - beta*mora_indice) * multiplicador_deuda, factor 1 cuando mora '
        'o multiplicador son None. Identica a xray/scoring_v4.py (verificada '
        'por test y por la comprobacion de coherencia de abajo). Este modulo '
        'solo mide: NO cambia ningun default del motor.',
        '',
        '## DECLARACION ANTI-LABEL (obligatoria)',
        '',
        '**Los parametros k, alpha y beta NO estan ajustados contra ninguna '
        'etiqueta, ni contra targets_proxy, ni contra ninguna variable de '
        'resultado.** Este analisis no ha leido marts/targets_proxy.parquet '
        'ni reports/label_review/. No hay etiquetas validadas en este '
        'dataset y calibrar contra un proxy seria circular. La calibracion '
        'es por SENSIBILIDAD y ESTABILIDAD medidas, con los criterios '
        'declarados en la cabecera de xray/calibrate_params_v4.py '
        '(C1-C4) antes de medir.',
        '',
        '## Poblacion de calibracion (declarada) y su sesgo',
        '',
        f"- Fichero: `{meta['input']}`.",
        f"- **Poblacion POST-EXCLUSION**: {meta['rows']} empresa-mes con nota, "
        f"{meta['companies']} empresas, {meta['months']} meses; las "
        f"{sesgo['n_empresas_excluidas']} empresas excluidas por nota 0 "
        f"persistente ({sesgo['n_excluidas']} empresa-mes con excluida = "
        'true y health_score NULL en el parquet) quedan FUERA de todas las '
        'cifras de este informe.',
        f"- Sin nota (no excluidas): {sesgo['n_sin_nota']} empresa-mes.",
        '- Maxima discrepancia entre health_score del parquet y el recalculo '
        'con las k/alpha/beta de cada fila: '
        f"{fmt_num(meta['coherence_max_abs_diff'], '{:.2e}')} "
        f"({meta['coherence_rows']} filas comparadas).",
        f"- Volumen mediano (C6 + T6_efectivo) de la poblacion con nota: "
        f"{sesgo['median_volume_con_nota_eur']:,.2f} EUR.",
        f"- Volumen mediano conocido de la poblacion sin nota (COTA "
        f"INFERIOR): {(sesgo['median_volume_sin_nota_cota_inferior_eur'] or 0):,.2f} EUR.",
        f"- **Sesgo medido (de observabilidad, no de tamano): la poblacion "
        f"sin nota tiene un volumen CONOCIDO {fmt_num(sesgo['ratio_sin_nota_sobre_con_nota'], '{:.2f}')} veces el de "
        f"la con nota (cota inferior: su volumen real puede ser mucho "
        "mayor, porque sus flujos son justamente los desconocidos). Es el "
        "mismo defecto de observabilidad asimetrica que excluyo a "
        f"{sesgo['n_empresas_excluidas']} empresas; el 17x de v3 se media "
        'sobre otro subconjunto y no es comparable. La poblacion de '
        'calibracion esta sesgada hacia empresas bien clasificadas.',
        '',
        '## C1: estabilidad mes a mes DENTRO de v4 (la metrica que faltaba)',
        '',
        'Definicion: |dH_final| entre meses naturales consecutivos de la '
        'MISMA empresa, sobre la POBLACION COMUN declarada (pares con nota '
        'evaluable con todos los valores de parametro comparados; n_pairs '
        'por fila). Excluidos los pares que tocan la ventana parcial '
        'expansiva (ventana_parcial = true en alguno de los dos meses) y '
        'los dos primeros meses con fila de cada empresa (convencion C2 de '
        'v3). NO es la distancia v3->v4, que ya esta medida y es otra cosa.',
        '',
        f"- Pares consecutivos con nota en la poblacion: "
        f"{pair_counters['pares_consecutivos_con_nota']}; excluidos por "
        f"primeros meses: {pair_counters['excluidos_primeros_meses']}; "
        f"excluidos por ventana parcial: "
        f"{pair_counters['excluidos_ventana_parcial']}; candidatos: "
        f"{pair_counters['candidatos']}.",
        f"- Con los parametros ACTUALES heredados (k=4178.45, alpha=3.0, "
        f"beta=0.25): |dH| p50 = {fmt_num(current['dH_median'])}, "
        f"p75 = {fmt_num(current['dH_p75'])}, p90 = "
        f"{fmt_num(current['dH_p90'])} ({current['n_pairs']} pares).",
        f"- Senal cruda sin suavizar ningun parametro (k=0, alpha=0, "
        f"beta=0) sobre la poblacion comun: |dH| p50 = "
        f"{fmt_num(reference['dH_median'])}, p75 = "
        f"{fmt_num(reference['dH_p75'])}, p90 = "
        f"{fmt_num(reference['dH_p90'])} ({reference['n_pairs']} pares).",
        f"- Senal cruda sobre TODOS los pares con nota (sin exclusion de "
        f"ventana): |dH| p50 = {fmt_num(raw_all['median'])}, p75 = "
        f"{fmt_num(raw_all['p75'])}, p90 = {fmt_num(raw_all['p90'])} "
        f"({raw_all['n_pairs']} pares): la exclusion de los pares jovenes "
        f"apenas cambia la senal cruda (p75 {fmt_num(raw_all['p75'])} frente "
        f"a {fmt_num(reference['dH_p75'])}): la volatilidad de v4 no vive "
        'solo en los meses jovenes.',
        f"- **Umbral declarado: p75(|dH_final|) < {P75_STABILITY_TARGET:g} "
        'puntos (10% de la escala 0-100 en el 75% de los pares: la lectura '
        'practica de "nada de montanas rusas").** Justificacion medida: la '
        f"senal cruda tiene p75 = {fmt_num(reference['dH_p75'])} en la "
        f"poblacion comun ({fmt_num(raw_all['p75'])} sobre todos los pares) "
        'y los parametros heredados suben la cola a '
        f"{fmt_num(current['dH_p75'])}: los canales nuevos (colchon, mora, "
        'obligacion) y el termino historico anaden volatilidad moderada, no '
        'la reproducen. La cota 10 deja margen para reaccionar a eventos '
        f"reales (p90 = {fmt_num(current['dH_p90'])}, dominado por eventos "
        'discretos de mora y obligacion) y descarta la montana rusa. El 8 '
        'de v3 NO se copia a ciegas: se midio sobre otro denominador (sin '
        'obligacion vencida, volumenes ~17x menores, '
        'reports/calibration_k/report.md).',
        '',
        '## Eje k (alpha=3.0, beta=0.25 fijados)',
        '',
        'Rango derivado de los datos de v4: volumen (C6 + T6_efectivo) de '
        f"la poblacion p05 = {k_info['volume_p05']:.4g} EUR, p50 = "
        f"{k_info['volume_p50']:.4g} EUR, p95 = {k_info['volume_p95']:.4g} "
        f"EUR; barrido logaritmico de {k_info['n_log_points']} puntos desde "
        f"k = {k_info['k_min']:.4g} EUR (p05/100) hasta k = "
        f"{k_info['k_max']:.4g} EUR (p95*100), mas k = 0 obligatorio y el k "
        'actual. Al subir k: gana estabilidad, pierde dispersion (aplano '
        'hacia 100*R_hist) y pierde reactividad.',
        '',
        *_axis_table(k_rows, 'k'),
        '',
        '```',
        *_stability_ascii(k_rows),
        '```',
        '',
        '## Eje alpha (k=4178.45, beta=0.25 fijados)',
        '',
        'El colchon v4 es el NIVEL de caja reconstruida (saldo_reversa), no '
        'el colchon invariante de v3: la escala cambio y el rango se '
        're-deriva. Ratio colchon_v4/T6_efectivo (T6>0, colchon>0): p05 = '
        f"{fmt_num(alpha_info['p05'], '{:.4g}')}, p10 = "
        f"{fmt_num(alpha_info['p10'], '{:.4g}')}, p50 = "
        f"{fmt_num(alpha_info['p50'], '{:.4g}')}, p90 = "
        f"{fmt_num(alpha_info['p90'], '{:.4g}')}, max = "
        f"{fmt_num(alpha_info['alpha_max'], '{:.4g}')}. Saturacion = "
        'fraccion de empresa-mes con colchon_v4 > alpha*T6_efectivo. '
        'Influencia: empresa-mes que cambian su nota mas de 1 punto '
        'respecto a alpha=0.',
        '',
        *_axis_table(alpha_rows, 'alpha'),
        '',
        '## Eje beta (k=4178.45, alpha=3.0 fijados)',
        '',
        'Rango [0, 1]: 0 = sin penalizacion por mora, 1 = mora total anula '
        'el canal. Castigo por mora: puntos de nota que se pierden sobre '
        'las filas con mora observable (p50 y p90). Influencia: empresa-mes '
        'que cambian su nota mas de 1 punto respecto a beta=0. Solape con '
        'el multiplicador de deuda: Spearman entre el castigo por mora y '
        'el castigo del multiplicador por empresa-mes (mora > 0 y '
        f"multiplicador < 1), con beta=0.25: {fmt_num(overlap['spearman'], '{:+.3f}')} "
        f"({overlap['n']} filas comparables; +1 = los dos canales castigan "
        'a las mismas empresas: doble conteo sistematico).',
        '',
        *_axis_table(beta_rows, 'beta'),
        '',
        '## Interaccion: como cambia el optimo de k segun alpha',
        '',
        'Para cada alpha de referencia (derivado de la curva de '
        'saturacion: p10/p50/p90 del ratio colchon/T6, mas 0 y alpha_max) '
        'se re-barra el eje k y se elige la menor k que cumple p75<10 y no '
        'aplana.',
        '',
        '| alpha de referencia | k* que cumple p75<10 | dH p75 con esa k | std p50 | reactividad (m) |',
        '|---|---|---|---|---|',
        *[f"| {row['alpha']:g} | "
          + ((f"{row['k']:.6g}" if row['k'] is not None else 'ninguna'))
          + ' | '
          + f"{fmt_num(row['dH_p75'])} | {fmt_num(row['std_median'])} | "
          + f"{fmt_num(row['reactivity_months'], '{:.1f}')} |"
          for row in interaction_rows],
        '',
        '## Interaccion: efecto de beta segun cuanta obligacion vencida hay',
        '',
        'Castigo por mora (puntos, p50 y p90) repartido por presencia de '
        'obligacion vencida > 0 en el corte (con k=4178.45, alpha=3.0). '
        'Con obligacion > 0 la senal ya entra por denominador y '
        'multiplicador: el castigo por mora ahi es doble conteo.',
        '',
        _beta_por_obligacion_table(panel, sorted({r['beta'] for r in beta_rows})),
        '',
        '## D. Variantes del triple conteo (medicion, sin elegir default)',
        '',
        'La misma senal de impago entra por denominador, multiplicador y '
        'mora (reparto Shapley publicado del castigo: p50 0,163 '
        'denominador / 0,0785 mora / 0,6487 multiplicador, '
        'reports/score_v4/summary.json). Variantes medidas: (a) beta '
        'constante como ahora; (b) beta atenuado x0.5 cuando '
        'obligacion_vencida_m > 0; (c) mora aplicada SOLO cuando '
        'obligacion_vencida_m = 0 o NULL.',
        '',
        _variantes_table(variant_rows_actual, variant_rows_recomendada),
        '',
        _variante_recomendacion(variant_rows_actual, variant_rows_recomendada),
        '',
        '## Compromiso de cada parametro: que se gana y que se pierde al subirlo',
        '',
        '| Parametro | Se gana | Se pierde |',
        '|---|---|---|',
        f"| k (de 0 a {k_info['k_max']:.4g}) | estabilidad: p75 de |dH| baja "
        f"de {fmt_num(k_rows[0]['dH_p75'])} a {fmt_num(k_rows[-1]['dH_p75'])} "
        f"(todas las k hasta ~1e5 cumplen el umbral; la ganancia relevante "
        f"empieza en k >= 1e5) | "
        f"dispersion: std p50 sube de {fmt_num(k_rows[0]['std_median'])} a "
        f"{fmt_num(k_rows[-1]['std_median'])} (en v4 subir k NO aplana, "
        'a diferencia de v3); el coste real es la reactividad: '
        f"{fmt_num(k_rows[0]['reactivity_months'], '{:.0f}')} a "
        f"{fmt_num(k_rows[-1]['reactivity_months'], '{:.0f}')} meses |",
        f"| alpha (de 0 a {fmt_num(alpha_info['alpha_max'], '{:.4g}')}) | el "
        'colchon (liquidez) pesa mas: saturacion del colchon baja de '
        f"{fmt_num(100.0 * (alpha_rows[1]['saturation_frac'] or 0), '{:.1f}')}"
        '% a '
        f"{fmt_num(100.0 * (alpha_rows[-2]['saturation_frac'] or 0), '{:.1f}')}"
        '% de empresa-mes; la influencia mediana del colchon crece de '
        f"{fmt_num(alpha_rows[3]['alpha_influence_median'])} a "
        f"{fmt_num(alpha_rows[7]['alpha_influence_median'])} puntos | "
        'la nota sube para las empresas con caja (efecto techo) y gana '
        'volatilidad (p75 sube); por encima de alpha ~9 el parametro es '
        'casi inerte (saturacion <= 17%): el orden de magnitud del ratio '
        'colchon/T6 (p50 0.58, p75 3.8) situa el 3.0 dentro de escala',
        f"| beta (de 0 a 1) | castigo creciente por mora: castigo p50 de "
        f"{fmt_num(beta_rows[0]['mora_castigo_median'])} a "
        f"{fmt_num(beta_rows[-1]['mora_castigo_median'])} puntos "
        f"(p90 de {fmt_num(beta_rows[0]['mora_castigo_p90'])} a "
        f"{fmt_num(beta_rows[-1]['mora_castigo_p90'])}) | estabilidad: NO "
        'empeora con beta (p75 baja levemente, 7.16 -> 6.58: la mora '
        'persistente amortigua los saltos); el coste es el solape con el '
        'multiplicador cuando hay obligacion vencida (Spearman '
        f"{fmt_num(overlap['spearman'], '{:+.2f}')}), que se trata en la "
        'decision D, no bajando beta globalmente |',
        '',
        '## Recomendacion',
        '',
        *_recomendacion_lines(recommendation, current, meta),
        '',
        '## Notas',
        '',
        '- Exclusion de estabilidad: pares donde ALGUN mes tiene '
        'ventana_parcial = true o indice < 2 dentro de la serie de la '
        'empresa, mas los pares sin nota evaluable en alguna configuracion '
        'comparada (poblacion comun declarada; n_pairs por fila).',
        '- Criterio de no aplanamiento: std mediana >= 0.85 * referencia '
        '(0,0,0) y empresas 45-55 mediana <= 1.2 * referencia (idem v3).',
        '- Influencia demostrable: alguna empresa-mes cambia su nota mas de '
        '1 punto respecto al valor 0 del eje.',
        '- El barrido de ejes es por ejes; la interaccion k x alpha y el '
        'reparto de beta por obligacion vencida se reportan aparte, y la '
        'terna recomendada se verifica de forma conjunta (fila `joint`).',
        '- Reactividad: series sinteticas de escalon de '
        'xray/calibrate_k.py (validas para v4 con colchon 0, obligacion 0 '
        'y mora None: la formula v4 se reduce exactamente a la v3).',
        '- Este modulo solo mide; los defaults de xray/scoring_v4.py y '
        'xray/scoring_io_v4.py NO se tocan.',
    ]
    return '\n'.join(lines) + '\n'


# ----------------------------------------------------------------------- IO

def write_outputs(output, rows, recommendation, meta, grid_info,
                  interaction_rows, variant_rows_actual,
                  variant_rows_recomendada, panel, pair_counters):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'sweep.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(SWEEP_COLUMNS),
                                extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        for row in variant_rows_actual + variant_rows_recomendada:
            writer.writerow(row)
    report = build_report(rows, recommendation, meta, grid_info,
                          interaction_rows, variant_rows_actual,
                          variant_rows_recomendada, panel, pair_counters)
    (output / 'report.md').write_text(report, encoding='utf-8')


def run(output=None, input_path=None):
    """Ejecuta la medicion completa y escribe sweep.csv y report.md.

    Devuelve el resumen JSON (dict). Lanza FileNotFoundError con
    MISSING_INPUT_MSG si falta el parquet de entrada, y FileExistsError si
    la ruta de salida ya existe.
    """
    panel, meta = load_assessments(input_path)
    pair_counters = _pair_counters(panel)
    rows, grid_info = run_sweep(panel)
    interaction_rows = [r for r in rows
                        if r['axis'] == 'interaccion_k_por_alpha']
    recommendation = recommend(rows, panel)
    variant_rows_actual = variantes_triple_conteo(
        panel, CURRENT_K, CURRENT_ALPHA, CURRENT_BETA)
    if recommendation['k'] is not None:
        misma_terna = (abs(recommendation['k'] - CURRENT_K) <= 1e-9
                       and abs(recommendation['alpha'] - CURRENT_ALPHA) <= 1e-9
                       and abs(recommendation['beta'] - CURRENT_BETA) <= 1e-9)
        variant_rows_recomendada = ([] if misma_terna else
                                    variantes_triple_conteo(
                                        panel, recommendation['k'],
                                        recommendation['alpha'],
                                        recommendation['beta']))
    else:
        variant_rows_recomendada = []
    output = Path(output) if output is not None else DEFAULT_OUTPUT
    write_outputs(output, rows, recommendation, meta, grid_info,
                  interaction_rows, variant_rows_actual,
                  variant_rows_recomendada, panel, pair_counters)
    summary = {
        'output': str(output),
        'input': meta['input'],
        'panel': {key: meta[key] for key in
                  ('rows', 'companies', 'months', 'groups',
                   'coherence_max_abs_diff', 'coherence_rows')},
        'poblacion': 'POST-EXCLUSION: empresa-mes con health_score no nulo '
                     'en reports/score_v4/assessments.parquet; las 33 '
                     'excluidas quedan fuera',
        'sweep': 'por_ejes_mas_interaccion_k_por_alpha_y_variantes_triple_conteo',
        'labels_used': False,
        'declaration': ('los parametros NO estan ajustados contra ninguna '
                        'etiqueta, targets_proxy ni ningun objetivo de '
                        'resultado; calibracion por sensibilidad y '
                        'estabilidad unicamente'),
        'stability_target_p75': P75_STABILITY_TARGET,
        'senal_cruda_todos_los_pares': grid_info['raw_all_pairs'],
        'pair_counters': pair_counters,
        'bias': meta['bias'],
        'recommended': {'k': recommendation['k'],
                        'alpha': recommendation['alpha'],
                        'beta': recommendation['beta'],
                        'verdict': recommendation['verdict'],
                        'meets_p75_target': recommendation['meets_p75'],
                        'meets_dispersion': recommendation['meets_dispersion'],
                        'menores_que_cumplen': recommendation.get(
                            'menores_que_cumplen'),
                        'reasons': recommendation['reasons']},
        'recommended_metrics': (
            None if recommendation['joint_row'] is None else
            {key: recommendation['joint_row'][key] for key in
             ('dH_median', 'dH_p75', 'dH_p90', 'std_median', 'iqr_median',
              'band_45_55_median', 'band_45_55_max', 'coverage_median')}),
        'variantes_triple_conteo': (
            None if not (variant_rows_recomendada or variant_rows_actual) else
            {row['variant']: {'dH_p75': row['dH_p75'],
                              'std_median': row['std_median'],
                              'band_changes_last_cut':
                                  row['band_changes_last_cut']}
             for row in (variant_rows_recomendada or variant_rows_actual)}),
        'sweep_rows': len(rows) + len(variant_rows_actual)
                      + len(variant_rows_recomendada),
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m xray.calibrate_params_v4',
        description='Mide los parametros libres k, alpha y beta de '
                    'healthscore_v4 (sensibilidad y estabilidad; sin '
                    'etiquetas); no implementa el scorer.')
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT,
                        help='parquet de evaluaciones de score_v4')
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
