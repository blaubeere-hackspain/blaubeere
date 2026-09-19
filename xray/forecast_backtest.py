"""Banco de pruebas walk-forward y baselines del forecast de health_score v4.

Este modulo NO construye el modelo de forecast. Construye la INFRAESTRUCTURA
contra la que ese modelo se medira y mide los baselines que tendra que batir:

  1. Congela la POBLACION EVALUABLE (la publica con digest reproducible):
     empresa-mes con `health_score` no nulo y `excluida = false`.
  2. Levanta el CENSO walk-forward: para cada origen m y cada horizonte
     h en {1, 2, 3}, cuantos pares (empresa, origen, h) son evaluables
     (la empresa tiene nota en m Y en m+h) y cuantos se pierden porque
     la guarda retiro la nota en m+h (eso NO se imputa NUNCA).
  3. Implementa el EVALUADOR: dada una lista de predicciones con grano
     (company_id, origen, h, prediccion) devuelve MAE, RMSE, percentiles del
     error absoluto y el tamaño de la interseccion por horizonte. La regla
     dura: toda metrica se calcula sobre la interseccion de pares donde
     prediccion Y verdad existen, y esa interseccion se reporta junto a la
     cifra. Dos metricas sobre poblaciones distintas NO son comparables.
  4. Mide los baselines sobre el censo congelado:
       B1 PERSISTENCIA  prediccion(m, h) = health_score(m)
       B2 MEDIA MOVIL   media de las ultimas 3 notas disponibles hasta m
       B3 MEDIANA GLOBAL mediana de las notas del origen m (referencia tonta)
     y la AUTOCORRELACION de la nota por h, que calibra cuanto margen deja B1.

Decisiones de interpretacion (explicitas para que no se reabran a conveniencia):

- La poblacion congelada se define SOLO con la regla del enunciado
  (`health_score` no nulo y `excluida = false`). No se recorta por
  `ventana_parcial`: el censo entero se mide y se publica.
- Un par es evaluable si la empresa tiene nota en el origen m Y en m+h.
  Como los baselines se miden "sobre el censo completo", los tres emiten
  prediccion exactamente para los origenes del censo (meses m con
  m+1, m+2 y m+3 dentro del panel). Asi los tres se comparan sobre la MISMA
  poblacion de pares y el evaluador lo verifica (n_pares identico al censo).
- La clausula de B2 "si no hay ninguna nota, no hay prediccion" es defensiva:
  en el censo congelado el origen m siempre tiene nota, de modo que la rama
  sin historia no se ejerce. Se prueba en tests con la funcion pura
  `media_ultimas` y se mide aparte, declarado como diagnostico, una variante
  de B2 que predice tambien en meses sin nota pero con historia.
- El recorte de calentamiento NO se impone. Se mide el censo completo y se
  publica ademas la subpoblacion "madura" con una regla con nombre: los
  origenes anteriores a que el panel tenga los 6 meses que exige la ventana
  del scorer son de calentamiento. La cifra que lo motiva: el 100% de sus
  filas tienen `ventana_parcial = true`.
"""

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

from xray import paths

VERSION = 'forecast_backtest_v1'
GENERADOR = 'xray/forecast_backtest.py'

DEFAULT_INPUT = paths.ROOT / 'reports' / 'score_v4' / 'assessments.parquet'
DEFAULT_OUTPUT_DIR = paths.ROOT / 'reports' / 'forecast_backtest'

MISSING_INPUT_MSG = ('falta reports/score_v4/assessments.parquet, ejecuta antes '
                     'xray.scoring_io_v4')

HORIZONTES = (1, 2, 3)
VENTANA_MEDIA_MOVIL = 3      # B2: ultimas 3 notas disponibles
VENTANA_SCORER_MESES = 6     # ventana movil del scorer v4 (justifica madurez)
POBLACION_REGLA = 'health_score no nulo y excluida = false'
CENSO_DEFINICION = ('par (empresa, origen m, h) con nota en m Y en m+h; '
                    'origen = mes con m+1, m+2 y m+3 dentro del panel')
NOMBRE_SUBPOBLACION_MADURA = 'subpoblacion_madura'

CORE_COLUMNS = ('company_id', 'month', 'health_score', 'excluida')
OPTIONAL_COLUMNS = ('ventana_parcial',)


# --------------------------------------------------------------- utilidades

def _to_month(value):
    """Normaliza a primer dia de mes (date). Acepta date, datetime o ISO."""
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    if isinstance(value, str):
        return date.fromisoformat(value[:10]).replace(day=1)
    raise ValueError(f'mes invalido: {value!r}')


def _month_index(month):
    return month.year * 12 + (month.month - 1)


def add_months(month, delta):
    """Suma `delta` meses a un primer dia de mes."""
    index = _month_index(_to_month(month)) + delta
    return date(index // 12, index % 12 + 1, 1)


def quantile(sorted_values, q):
    """Cuantil con interpolacion lineal sobre una lista ya ordenada.

    Misma definicion que xray.calibrate_k.quantile (interpolacion lineal,
    equivalente al metodo por defecto de numpy), para que las cifras sean
    comparables bit a bit con las del resto del repo.
    """
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
    return float(sorted_values[low] * (1.0 - weight)
                 + sorted_values[high] * weight)


def _pearson(xs, ys):
    """Correlacion de Pearson; None si alguna serie es constante o n < 2."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0.0 or syy == 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _rank(values):
    """Rangos con empates promediados (para Spearman)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def _file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------ carga

def load_assessments(path=None):
    """Lee reports/score_v4/assessments.parquet (SOLO LECTURA).

    Devuelve una lista de dicts con company_id, month (date, primer dia de
    mes), health_score (float o None), excluida (bool) y ventana_parcial
    (bool, False si la columna no existe), ordenada por (company_id, month).
    Nunca rellena ni recalcula la nota: la nota v4 es un dato de entrada.
    """
    path = Path(path) if path is not None else DEFAULT_INPUT
    if not path.exists():
        raise FileNotFoundError(MISSING_INPUT_MSG)
    con = duckdb.connect()
    try:
        disponibles = {row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(path)}')"
        ).fetchall()}
        faltan = [c for c in CORE_COLUMNS if c not in disponibles]
        if faltan:
            raise ValueError('assessments.parquet: no tiene las columnas '
                             f'esperadas {", ".join(CORE_COLUMNS)} '
                             f'(faltan {", ".join(faltan)})')
        columnas = list(CORE_COLUMNS) + [c for c in OPTIONAL_COLUMNS
                                         if c in disponibles]
        raw = con.execute(
            f"SELECT {', '.join(columnas)} FROM read_parquet('{_sql_path(path)}')"
        ).fetchall()
    finally:
        con.close()
    rows = []
    for values in raw:
        record = dict(zip(columnas, values))
        rows.append({
            'company_id': record['company_id'],
            'month': _to_month(record['month']),
            'health_score': (None if record['health_score'] is None
                             else float(record['health_score'])),
            'excluida': bool(record.get('excluida')),
            'ventana_parcial': bool(record.get('ventana_parcial')),
        })
    rows.sort(key=lambda r: (r['company_id'], r['month']))
    return rows


def _sql_path(path):
    return str(path).replace("'", "''")


# ------------------------------------------------------- poblacion congelada

def population_rows(rows):
    """Filas de la poblacion evaluable (regla del enunciado)."""
    return [r for r in rows if not r['excluida'] and r['health_score'] is not None]


def _digest_verdad(verdad):
    """sha256 canonico sobre (company_id, month, health_score) ordenado."""
    digest = hashlib.sha256()
    for company, month in sorted(verdad):
        digest.update(f'{company}\t{month.isoformat()}\t'
                      f'{verdad[(company, month)]!r}\n'.encode('utf-8'))
    return digest.hexdigest()


def freeze_population(rows):
    """Congela la poblacion evaluable y su verdad (company_id, month) -> nota.

    Devuelve un dict serializable SALVO la clave `_verdad` (mapa en memoria
    que consume el evaluador). El resto viaja al artefacto de salida y deja
    la poblacion bloqueada: las tareas posteriores no pueden redefinirla sin
    que cambie `digest_sha256`.
    """
    verdad = {}
    for row in population_rows(rows):
        value = row['health_score']
        if not math.isfinite(value):
            raise ValueError(f'health_score no finito en '
                             f'{row["company_id"]} {row["month"]}: {value!r}')
        key = (row['company_id'], row['month'])
        if key in verdad:
            raise ValueError(f'clave empresa-mes duplicada en la poblacion: {key}')
        verdad[key] = value
    pares = sorted(verdad)
    empresas = sorted({company for company, _ in pares})
    calendario = sorted({month for _, month in pares})
    indice = {month: i for i, month in enumerate(calendario)}
    empresa_mes = {company: [] for company in empresas}
    for company, month in pares:
        empresa_mes[company].append(indice[month])
    return {
        'regla': POBLACION_REGLA,
        'n_empresas': len(empresas),
        'n_empresa_mes': len(pares),
        'meses_panel': [m.isoformat() for m in calendario],
        'empresas': empresas,
        'empresa_mes': {c: empresa_mes[c] for c in empresas},
        'digest_sha256': _digest_verdad(verdad),
        '_verdad': verdad,
    }


def poblacion_congelada(path=None):
    """Poblacion congelada lista para usar: load_assessments + freeze."""
    return freeze_population(load_assessments(path))


def verifica_digest(population):
    """True si el digest declarado reproduce la verdad en memoria."""
    verdad = population.get('_verdad')
    if verdad is None:
        raise ValueError('poblacion sin verdad en memoria; no se puede verificar')
    return population['digest_sha256'] == _digest_verdad(verdad)


def poblacion_serializable(population):
    """Copia sin `_verdad` (lo unico no serializable del dict de poblacion)."""
    return {k: v for k, v in population.items() if k != '_verdad'}


# ------------------------------------------------------------------- censo

def _empresas_por_mes(verdad):
    por_mes = {}
    for company, month in verdad:
        por_mes.setdefault(month, set()).add(company)
    return por_mes


def census_origins(calendario, horizontes=HORIZONTES):
    """Origenes del censo: meses m con m+1..m+max(h) dentro del panel."""
    if not calendario:
        return []
    ultimo = max(calendario)
    return [m for m in sorted(calendario)
            if add_months(m, max(horizontes)) <= ultimo]


def build_origin_census(rows=None, population=None, horizontes=HORIZONTES):
    """Censo walk-forward completo, sin recorte de calentamiento.

    Devuelve por origen y horizonte el numero de pares evaluables, empresas
    distintas y pares perdidos porque la empresa tiene nota en m pero no en
    m+h. Incluye el total por horizonte (empresas = union, no suma) y la
    subpoblacion madura con la regla y la cifra que la motiva.
    """
    if population is None:
        if rows is None:
            raise ValueError('build_origin_census necesita rows o population')
        population = freeze_population(rows)
    verdad = population['_verdad']
    por_mes = _empresas_por_mes(verdad)
    calendario = sorted(por_mes)
    origenes = census_origins(calendario, horizontes)

    parcial = {}
    if rows is not None:
        for row in population_rows(rows):
            if row['month'] in por_mes:
                count = parcial.setdefault(row['month'], [0, 0])
                count[0] += 1
                if row['ventana_parcial']:
                    count[1] += 1

    por_origen = []
    totales = {h: {'pares': 0, 'perdidos': 0, 'empresas': set()}
               for h in horizontes}
    for month in origenes:
        presentes = por_mes[month]
        fila = {'origen': month.isoformat(), 'por_h': {}}
        if month in parcial:
            n, n_parcial = parcial[month]
            fila['filas_poblacion_en_m'] = n
            fila['filas_ventana_parcial_en_m'] = n_parcial
            fila['pct_ventana_parcial_en_m'] = (n_parcial / n) if n else None
        for h in horizontes:
            destino = por_mes.get(add_months(month, h), set())
            pares = presentes & destino
            perdidos = len(presentes - destino)
            fila['por_h'][str(h)] = {
                'pares': len(pares),
                'empresas': len(pares),
                'perdidos_nota_ausente_en_mas_h': perdidos,
            }
            totales[h]['pares'] += len(pares)
            totales[h]['perdidos'] += perdidos
            totales[h]['empresas'] |= pares
        por_origen.append(fila)

    total_por_h = {str(h): {'pares': totales[h]['pares'],
                            'empresas': len(totales[h]['empresas']),
                            'perdidos_nota_ausente_en_mas_h': totales[h]['perdidos']}
                   for h in horizontes}

    # Meses del talon que NO pueden ser origen completo (falta m+3): se
    # publican con los pares que aportarian para que el recorte no oculte nada.
    talon = []
    for month in calendario:
        if month in set(origenes):
            continue
        fila = {'mes': month.isoformat(), 'por_h': {}}
        presentes = por_mes[month]
        for h in horizontes:
            destino = por_mes.get(add_months(month, h))
            if destino is None:
                fila['por_h'][str(h)] = None
                continue
            pares = presentes & destino
            fila['por_h'][str(h)] = {
                'pares': len(pares),
                'empresas': len(pares),
                'perdidos_nota_ausente_en_mas_h': len(presentes - destino),
            }
        talon.append(fila)

    primer_mes = calendario[0] if calendario else None
    primer_maduro = (add_months(primer_mes, VENTANA_SCORER_MESES - 1)
                     if primer_mes else None)
    maduros = [m for m in origenes if primer_maduro is not None and m >= primer_maduro]
    calentamiento = [m for m in origenes if m not in set(maduros)]

    n_filas_calent = sum(parcial.get(m, [0, 0])[0] for m in calentamiento)
    n_parcial_calent = sum(parcial.get(m, [0, 0])[1] for m in calentamiento)

    utilizables = [fila['origen'] for fila in por_origen
                   if all(fila['por_h'][str(h)]['pares'] > 0 for h in horizontes)]

    return {
        'definicion': CENSO_DEFINICION,
        'horizontes': list(horizontes),
        'n_origenes': len(origenes),
        'origenes': [m.isoformat() for m in origenes],
        'origenes_utilizables': utilizables,
        'por_origen': por_origen,
        'total_por_h': total_por_h,
        'talon_no_utilizable_como_origen': talon,
        'calentamiento': {
            'criterio': (f'origen anterior a que el panel tenga los '
                         f'{VENTANA_SCORER_MESES} meses que exige la ventana del '
                         f'scorer; primer origen maduro = '
                         f'{primer_maduro.isoformat() if primer_maduro else None}'),
            'origenes_descartados': [m.isoformat() for m in calentamiento],
            'filas_poblacion': n_filas_calent,
            'filas_ventana_parcial': n_parcial_calent,
            'pct_ventana_parcial': (n_parcial_calent / n_filas_calent)
                                   if n_filas_calent else None,
            'recomendacion': ('NO se recorta el censo publicado; para seleccion de '
                              'modelo se recomienda reportar tambien la '
                              f'{NOMBRE_SUBPOBLACION_MADURA}, que excluye esos '
                              'origenes porque la nota de origen es parcial.'),
        },
        NOMBRE_SUBPOBLACION_MADURA: {
            'regla': (f'origen >= {primer_maduro.isoformat() if primer_maduro else None} '
                      f'(panel con >= {VENTANA_SCORER_MESES} meses de historia)'),
            'origenes': [m.isoformat() for m in maduros],
            'n_origenes': len(maduros),
            'total_por_h': _totales_por_h(verdad, maduros),
            'digest_sha256': _digest_origenes(verdad, set(maduros)),
        },
    }
    # La subpoblacion de calentamiento tambien se publica con sus totales.
    census['calentamiento']['total_por_h'] = _totales_por_h(verdad, calentamiento)
    census['calentamiento']['digest_sha256'] = _digest_origenes(
        verdad, set(calentamiento))
    return census


# --------------------------------------------------------------- evaluador

def _normaliza_h(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'horizonte invalido (entero positivo): {value!r}')
    if value <= 0:
        raise ValueError(f'horizonte invalido (entero positivo): {value!r}')
    return value


def normaliza_predicciones(predictions):
    """Normaliza a {(company_id, month, h): prediccion}.

    Acepta dicts con claves company_id, origen, h, prediccion, o tuplas
    (company_id, origen, h, prediccion). Las predicciones con valor None se
    descartan (SIN prediccion; nunca se imputa). Duplicados: error.
    """
    result = {}
    descartadas_sin_valor = 0
    for item in predictions:
        if isinstance(item, dict):
            company = item['company_id']
            month = _to_month(item['origen'])
            h = _normaliza_h(item['h'])
            value = item['prediccion']
        else:
            company, origen, h, value = item
            month = _to_month(origen)
            h = _normaliza_h(h)
        if value is None:
            descartadas_sin_valor += 1
            continue
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f'prediccion no finita: {company} {month} h={h} '
                             f'{value!r}')
        key = (company, month, h)
        if key in result:
            raise ValueError(f'prediccion duplicada: {key}')
        result[key] = value
    return result, descartadas_sin_valor


def evaluate_predictions(predictions, rows=None, population=None,
                         exigir_nota_en_origen=True):
    """Evalua predicciones por horizonte sobre la interseccion con la verdad.

    `predictions`: iterable de dicts/tuplas (company_id, origen, h, prediccion).
    `population`: poblacion congelada (freeze_population). Si es None se
    deriva de `rows`. `exigir_nota_en_origen=True` restringe a los pares del
    censo congelado (nota en m Y en m+h); con False solo exige verdad en m+h
    (modo diagnostico, declarado).

    Devuelve {h: metricas}. En cada horizonte:
      mae, rmse, sesgo (error con signo), error_abs_p50/p75/p90,
      n_pares y n_empresas (LA interseccion sobre la que se calcula),
      n_predicciones, n_descartadas_origen_sin_nota, n_descartadas_verdad_ausente.
    """
    if population is None:
        if rows is None:
            raise ValueError('evaluate_predictions necesita rows o population')
        population = freeze_population(rows)
    verdad = population['_verdad']
    normalizadas, descartadas_sin_valor = normaliza_predicciones(predictions)

    por_h = {}
    for (company, month, h), prediction in normalizadas.items():
        bucket = por_h.setdefault(h, {
            'errores': [], 'empresas': set(), 'n_predicciones': 0,
            'origen_sin_nota': 0, 'verdad_ausente': 0,
        })
        bucket['n_predicciones'] += 1
        if exigir_nota_en_origen and (company, month) not in verdad:
            bucket['origen_sin_nota'] += 1
            continue
        truth = verdad.get((company, add_months(month, h)))
        if truth is None:
            bucket['verdad_ausente'] += 1
            continue
        bucket['errores'].append(prediction - truth)
        bucket['empresas'].add(company)

    result = {}
    for h in sorted(por_h):
        bucket = por_h[h]
        errores = bucket['errores']
        absolutos = sorted(abs(e) for e in errores)
        n = len(errores)
        result[h] = {
            'h': h,
            'n_pares': n,
            'n_empresas': len(bucket['empresas']),
            'n_predicciones': bucket['n_predicciones'],
            'n_descartadas_origen_sin_nota': bucket['origen_sin_nota'],
            'n_descartadas_verdad_ausente': bucket['verdad_ausente'],
            'n_descartadas_sin_valor': descartadas_sin_valor,
            'mae': (sum(absolutos) / n) if n else None,
            'rmse': (math.sqrt(sum(e * e for e in errores) / n)) if n else None,
            'sesgo': (sum(errores) / n) if n else None,
            'error_abs_p50': quantile(absolutos, 0.50) if n else None,
            'error_abs_p75': quantile(absolutos, 0.75) if n else None,
            'error_abs_p90': quantile(absolutos, 0.90) if n else None,
        }
    return result


# --------------------------------------------------------------- baselines

def media_ultimas(serie, ventana=VENTANA_MEDIA_MOVIL):
    """Media de las ultimas `ventana` notas disponibles de una serie ordenada.

    `serie` es una lista de (month, value) ordenada por month. Toma los
    ultimos `ventana` elementos realmente disponibles (no imputa huecos).
    Devuelve None si la serie esta vacia: SIN prediccion.
    """
    if not serie:
        return None
    ultimos = serie[-ventana:]
    return sum(value for _, value in ultimos) / len(ultimos)


def _series_por_empresa(verdad):
    series = {}
    for (company, month), value in verdad.items():
        series.setdefault(company, []).append((month, value))
    for company in series:
        series[company].sort()
    return series


def baseline_persistencia(population, origenes, horizontes=HORIZONTES):
    """B1: prediccion(m, h) = health_score(m)."""
    verdad = population['_verdad']
    seleccion = set(origenes)
    predictions = []
    for (company, month), value in verdad.items():
        if month not in seleccion:
            continue
        for h in horizontes:
            predictions.append({'company_id': company, 'origen': month,
                                'h': h, 'prediccion': value})
    return predictions


def baseline_media_movil(population, origenes, horizontes=HORIZONTES,
                         ventana=VENTANA_MEDIA_MOVIL):
    """B2: media de las ultimas `ventana` notas disponibles hasta m (con m)."""
    series = _series_por_empresa(population['_verdad'])
    seleccion = set(origenes)
    predictions = []
    for company, serie in series.items():
        for i, (month, _) in enumerate(serie):
            if month not in seleccion:
                continue
            value = media_ultimas(serie[:i + 1], ventana)
            if value is None:
                continue
            for h in horizontes:
                predictions.append({'company_id': company, 'origen': month,
                                    'h': h, 'prediccion': value})
    return predictions


def baseline_mediana_global(population, origenes, horizontes=HORIZONTES):
    """B3: mediana de las notas del origen m sobre la poblacion evaluable."""
    verdad = population['_verdad']
    seleccion = set(origenes)
    valores_por_mes = {}
    empresas_por_mes = {}
    for (company, month), value in verdad.items():
        if month not in seleccion:
            continue
        valores_por_mes.setdefault(month, []).append(value)
        empresas_por_mes.setdefault(month, []).append(company)
    predictions = []
    for month, values in valores_por_mes.items():
        mediana = quantile(sorted(values), 0.50)
        for company in empresas_por_mes[month]:
            for h in horizontes:
                predictions.append({'company_id': company, 'origen': month,
                                    'h': h, 'prediccion': mediana})
    return predictions


def baseline_media_movil_historia(population, origenes, horizontes=HORIZONTES,
                                  ventana=VENTANA_MEDIA_MOVIL):
    """B2 diagnostico: predice tambien en meses sin nota pero con historia.

    NO es la medicion del censo congelado (el origen puede no tener nota):
    existe para cuantificar cuanto cambiaria B2 si se levantase la exigencia
    de nota en m. Se evalua con `exigir_nota_en_origen=False` y se declara.
    """
    verdad = population['_verdad']
    series = _series_por_empresa(verdad)
    calendario = sorted({month for _, month in verdad})
    seleccion = set(origenes)
    predictions = []
    for company, serie in series.items():
        meses_con_nota = {month for month, _ in serie}
        for month in calendario:
            if month not in seleccion:
                continue
            anterior = [(m, v) for m, v in serie if m <= month]
            if not anterior:
                continue
            value = media_ultimas(anterior, ventana)
            if value is None:
                continue
            for h in horizontes:
                predictions.append({'company_id': company, 'origen': month,
                                    'h': h, 'prediccion': value,
                                    'origen_con_nota': month in meses_con_nota})
    return predictions


def compute_baselines(population, origenes, horizontes=HORIZONTES):
    """Mide B1, B2 y B3 con el MISMO evaluador sobre la misma poblacion."""
    generadores = {
        'B1_persistencia': baseline_persistencia,
        'B2_media_movil_3': baseline_media_movil,
        'B3_mediana_global': baseline_mediana_global,
    }
    metricas = {}
    for name, generator in generadores.items():
        predictions = generator(population, origenes, horizontes)
        metricas[name] = evaluate_predictions(predictions, population=population)
    return metricas


# --------------------------------------------------------- autocorrelacion

def autocorrelacion(population, origenes, horizontes=HORIZONTES):
    """Cuanto se parece H(m+h) a H(m) por h sobre los pares del censo.

    Reporta Pearson, Spearman, la pendiente/intercepto OLS de H(m+h) sobre
    H(m) (mide regresion a la media) y el delta medio. La interseccion es la
    misma que la de B1: pares con nota en m Y en m+h.
    """
    verdad = population['_verdad']
    seleccion = set(origenes)
    por_h = {}
    for h in horizontes:
        xs, ys = [], []
        for (company, month), value in verdad.items():
            if month not in seleccion:
                continue
            future = verdad.get((company, add_months(month, h)))
            if future is None:
                continue
            xs.append(value)
            ys.append(future)
        n = len(xs)
        record = {'h': h, 'n_pares': n,
                  'n_empresas': len({company for company, month in verdad
                                     if month in seleccion
                                     and (company, add_months(month, h)) in verdad})}
        if n:
            deltas = [y - x for x, y in zip(xs, ys)]
            record.update({
                'pearson': _pearson(xs, ys),
                'spearman': _pearson(_rank(xs), _rank(ys)) if n >= 2 else None,
                'delta_medio': sum(deltas) / n,
                'delta_abs_medio': sum(abs(d) for d in deltas) / n,
                'delta_abs_p50': quantile(sorted(abs(d) for d in deltas), 0.50),
                'delta_abs_p90': quantile(sorted(abs(d) for d in deltas), 0.90),
                'media_h_origen': sum(xs) / n,
                'media_h_futuro': sum(ys) / n,
            })
            slope = _ols_slope(xs, ys)
            record['ols_pendiente'] = slope
            record['ols_intercepto'] = (record['media_h_futuro']
                                        - slope * record['media_h_origen']
                                        if slope is not None else None)
        else:
            record.update({k: None for k in (
                'pearson', 'spearman', 'delta_medio', 'delta_abs_medio',
                'delta_abs_p50', 'delta_abs_p90', 'media_h_origen',
                'media_h_futuro', 'ols_pendiente', 'ols_intercepto')})
        por_h[h] = record
    return por_h


def _ols_slope(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0.0:
        return None
    return (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx)


# ----------------------------------------------------------------- informe

def build_report(rows, input_path=None):
    """Construye el informe completo (dict serializable) del backtest."""
    population = freeze_population(rows)
    if not verifica_digest(population):
        raise ValueError('el digest de la poblacion no reproduce la verdad')
    census = build_origin_census(rows=rows, population=population)
    origenes = [_to_month(m) for m in census['origenes']]
    maduros = [_to_month(m) for m in census[NOMBRE_SUBPOBLACION_MADURA]['origenes']]
    calentamiento = [_to_month(m)
                     for m in census['calentamiento']['origenes_descartados']]

    metricas_censo = compute_baselines(population, origenes)
    _verifica_poblacion_identica(metricas_censo, census['total_por_h'])
    metricas_maduro = compute_baselines(population, maduros)
    metricas_calentamiento = (compute_baselines(population, calentamiento)
                              if calentamiento else {})

    autocorr = autocorrelacion(population, origenes)

    predictions_b2 = baseline_media_movil_historia(population, origenes)
    b2_historia = evaluate_predictions(predictions_b2, population=population,
                                       exigir_nota_en_origen=False)

    input_info = _input_info(input_path)

    return {
        'version': VERSION,
        'generador': GENERADOR,
        'comando_reproduccion': ('.venv/bin/python -B -m xray.forecast_backtest '
                                 '--input reports/score_v4/assessments.parquet '
                                 '--output-dir reports/forecast_backtest'),
        'input': input_info,
        'poblacion': poblacion_serializable(population),
        'censo': census,
        'metricas_censo': metricas_censo,
        'metricas_subpoblacion_madura': metricas_maduro,
        'metricas_origenes_calentamiento': metricas_calentamiento,
        'autocorrelacion': autocorr,
        'diagnostico_b2_origenes_sin_nota': {
            'descripcion': ('B2 emitiendo prediccion tambien en meses sin nota '
                            'pero con historia, evaluado con exigir_nota_en_'
                            'origen=False; NO comparable con metricas_censo '
                            'porque la poblacion de pares es mayor.'),
            'metricas': b2_historia,
        },
        'notas_metodologicas': _notas_metodologicas(),
    }


def _verifica_poblacion_identica(metricas_censo, total_por_h):
    """Invariante: los tres baselines caen en el mismo censo por horizonte."""
    for h in HORIZONTES:
        esperado = total_por_h[str(h)]['pares']
        for name, por_h in metricas_censo.items():
            if por_h[h]['n_pares'] != esperado:
                raise ValueError(
                    f'{name} h={h}: n_pares={por_h[h]["n_pares"]} != censo '
                    f'{esperado}; los baselines no son comparables')


def _totales_por_h(verdad, origenes):
    """Pares evaluables por horizonte para un subconjunto de origenes."""
    seleccion = set(origenes)
    totales = {str(h): 0 for h in HORIZONTES}
    for (company, month) in verdad:
        if month not in seleccion:
            continue
        for h in HORIZONTES:
            if (company, add_months(month, h)) in verdad:
                totales[str(h)] += 1
    return totales


def _digest_origenes(verdad, origenes):
    """Digest de los pares (empresa, m, h) de un subconjunto de origenes."""
    digest = hashlib.sha256()
    for h in HORIZONTES:
        pares = []
        for (company, month), _ in verdad.items():
            if month in origenes and (company, add_months(month, h)) in verdad:
                pares.append((company, month))
        for company, month in sorted(pares):
            digest.update(f'{h}\t{company}\t{month.isoformat()}\n'.encode('utf-8'))
    return digest.hexdigest()


def _input_info(input_path):
    if input_path is None:
        return None
    path = Path(input_path)
    try:
        display = str(path.relative_to(paths.ROOT))
    except ValueError:
        display = str(path)
    if not path.exists():
        return {'path': display, 'existe': False}
    return {
        'path': display,
        'existe': True,
        'bytes': path.stat().st_size,
        'sha256': _file_sha256(path),
    }


def _notas_metodologicas():
    return [
        POBLACION_REGLA + ': nunca se imputa una nota ausente (ni cero, ni la '
        'anterior, ni la mediana). Un par sin nota en m o en m+h no es evaluable.',
        'Los tres baselines se evaluan sobre los mismos pares del censo '
        '(origenes m con m+1, m+2 y m+3 dentro del panel); el evaluador verifica '
        'que n_pares coincide con el censo por horizonte.',
        'B2 usa las ultimas 3 notas disponibles hasta m inclusive; los huecos '
        'no se rellenan. La rama sin historia no se ejerce en el censo porque el '
        'origen siempre tiene nota; se prueba con media_ultimas y se mide aparte '
        'la variante diagnostica.',
        'El recorte de calentamiento no se impone: el censo publicado es completo '
        f'({VENTANA_SCORER_MESES} meses de ventana del scorer motivan la '
        'subpoblacion madura, declarada con su cifra).',
    ]


# -------------------------------------------------------------- markdown

def _fmt(value, digits=4):
    if value is None:
        return 'n/a'
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value)


def render_markdown(report):
    """Informe humano. Toda cifra sale del dict reproducible."""
    pop = report['poblacion']
    census = report['censo']
    lines = []
    lines.append('# Backtest walk-forward y baselines del health_score v4')
    lines.append('')
    lines.append(f'Generado por `{report["generador"]}` (version '
                 f'`{report["version"]}`). Reproducible con:')
    lines.append('')
    lines.append('```sh')
    lines.append(report['comando_reproduccion'])
    lines.append('```')
    lines.append('')
    info = report['input'] or {}
    lines.append(f'- Fuente: `{info.get("path")}` '
                 f'(sha256 `{info.get("sha256")}`, {info.get("bytes")} bytes).')
    lines.append('')
    lines.append('## 1. Poblacion evaluable congelada')
    lines.append('')
    lines.append(f'- Regla: {pop["regla"]}.')
    lines.append(f'- Empresas: **{pop["n_empresas"]}**.')
    lines.append(f'- Empresa-mes con nota: **{pop["n_empresa_mes"]}**.')
    lines.append(f'- Digest de `(company_id, month, health_score)`: '
                 f'`{pop["digest_sha256"]}`.')
    lines.append('- Las tareas posteriores deben evaluar sobre esta poblacion: '
                 'si el digest no coincide, la poblacion cambio.')
    lines.append('')
    lines.append('## 2. Censo walk-forward (sin recorte de calentamiento)')
    lines.append('')
    lines.append(f'- Origenes (m con m+1, m+2 y m+3 dentro del panel): '
                 f'**{census["n_origenes"]}** '
                 f'({census["origenes"][0]} .. {census["origenes"][-1]}).')
    lines.append(f'- Origenes utilizables (>= 1 par en los 3 horizontes): '
                 f'**{len(census["origenes_utilizables"])}**.')
    lines.append('')
    lines.append('| origen | pares h=1 | perdidos h=1 | pares h=2 | perdidos h=2 '
                 '| pares h=3 | perdidos h=3 | ventana parcial |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|---:|')
    for fila in census['por_origen']:
        pct = fila.get('pct_ventana_parcial_en_m')
        pct_txt = f'{100 * pct:.1f}%' if pct is not None else 'n/a'
        cells = [fila['origen']]
        for h in ('1', '2', '3'):
            item = fila['por_h'][h]
            cells.append(str(item['pares']))
            cells.append(str(item['perdidos_nota_ausente_en_mas_h']))
        cells.append(pct_txt)
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines.append('')
    lines.append('### Totales por horizonte')
    lines.append('')
    lines.append('| h | pares evaluables | empresas distintas | perdidos '
                 '(nota en m, no en m+h) |')
    lines.append('|---:|---:|---:|---:|')
    for h in map(str, census['horizontes']):
        item = census['total_por_h'][h]
        lines.append(f'| {h} | {item["pares"]} | {item["empresas"]} | '
                     f'{item["perdidos_nota_ausente_en_mas_h"]} |')
    lines.append('')
    lines.append('### Meses del talon (no son origen completo: falta m+3)')
    lines.append('')
    lines.append('| mes | pares h=1 | pares h=2 | pares h=3 |')
    lines.append('|---|---:|---:|---:|')
    for fila in census['talon_no_utilizable_como_origen']:
        celdas = [fila['mes']]
        for h in ('1', '2', '3'):
            item = fila['por_h'].get(h)
            celdas.append('n/a' if item is None else str(item['pares']))
        lines.append('| ' + ' | '.join(celdas) + ' |')
    lines.append('')
    calent = census['calentamiento']
    lines.append('### Calentamiento (no se recorta el censo)')
    lines.append('')
    lines.append(f'- Criterio: {calent["criterio"]}.')
    lines.append(f'- Origenes de calentamiento: '
                 f'{", ".join(calent["origenes_descartados"]) or "ninguno"}.')
    lines.append(f'- Filas de poblacion en esos origenes: '
                 f'{calent["filas_poblacion"]}; con `ventana_parcial = true`: '
                 f'{calent["filas_ventana_parcial"]} '
                 f'({_fmt(100 * calent["pct_ventana_parcial"], 1) if calent["pct_ventana_parcial"] is not None else "n/a"}%).')
    lines.append(f'- {calent["recomendacion"]}')
    sub = census[NOMBRE_SUBPOBLACION_MADURA]
    lines.append(f'- Subpoblacion madura: {sub["regla"]}; '
                 f'{sub["n_origenes"]} origenes, digest `{sub["digest_sha256"]}`.')
    lines.append('')
    lines.append('## 3. Baselines sobre el censo completo')
    lines.append('')
    lines.append('Metricas por horizonte sobre la interseccion prediccion Y '
                 'verdad (misma poblacion para los tres baselines).')
    lines.append('')
    _render_metrics_table(lines, report['metricas_censo'])
    lines.append('')
    lines.append('## 4. Metricas sobre la subpoblacion madura (sensibilidad)')
    lines.append('')
    _render_metrics_table(lines, report['metricas_subpoblacion_madura'])
    lines.append('')
    if report['metricas_origenes_calentamiento']:
        lines.append('### Metricas sobre los origenes de calentamiento')
        lines.append('')
        _render_metrics_table(lines, report['metricas_origenes_calentamiento'])
        lines.append('')
    lines.append('## 5. Autocorrelacion de la nota')
    lines.append('')
    lines.append('| h | pares | Pearson | Spearman | delta medio | '
                 'delta abs medio | delta abs p50 | delta abs p90 | '
                 'pendiente OLS |')
    lines.append('|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for h in sorted(report['autocorrelacion']):
        item = report['autocorrelacion'][h]
        lines.append(
            f'| {h} | {item["n_pares"]} | {_fmt(item["pearson"])} | '
            f'{_fmt(item["spearman"])} | {_fmt(item["delta_medio"])} | '
            f'{_fmt(item["delta_abs_medio"])} | {_fmt(item["delta_abs_p50"])} | '
            f'{_fmt(item["delta_abs_p90"])} | {_fmt(item["ols_pendiente"])} |')
    lines.append('')
    diag = report['diagnostico_b2_origenes_sin_nota']
    lines.append('## 6. Diagnostico: B2 en meses sin nota (poblacion distinta)')
    lines.append('')
    lines.append(diag['descripcion'])
    lines.append('')
    lines.append('| h | pares | empresas | MAE | RMSE |')
    lines.append('|---:|---:|---:|---:|---:|')
    for h in sorted(diag['metricas']):
        item = diag['metricas'][h]
        lines.append(f'| {h} | {item["n_pares"]} | {item["n_empresas"]} | '
                     f'{_fmt(item["mae"])} | {_fmt(item["rmse"])} |')
    lines.append('')
    lines.append('## 7. Lectura')
    lines.append('')
    lines.extend(_lectura(report))
    lines.append('')
    lines.append('## 8. Notas metodologicas')
    lines.append('')
    for nota in report['notas_metodologicas']:
        lines.append(f'- {nota}')
    lines.append('')
    return '\n'.join(lines)


def _render_metrics_table(lines, metricas):
    lines.append('| baseline | h | pares | empresas | MAE | RMSE | sesgo | '
                 'error_abs_p50 | error_abs_p75 | error_abs_p90 |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for name in sorted(metricas):
        por_h = metricas[name]
        for h in sorted(por_h):
            item = por_h[h]
            lines.append(
                f'| {name} | {h} | {item["n_pares"]} | {item["n_empresas"]} | '
                f'{_fmt(item["mae"])} | {_fmt(item["rmse"])} | '
                f'{_fmt(item["sesgo"])} | {_fmt(item["error_abs_p50"])} | '
                f'{_fmt(item["error_abs_p75"])} | '
                f'{_fmt(item["error_abs_p90"])} |')


def _lectura(report):
    censo = report['metricas_censo']
    auto = report['autocorrelacion']
    b1 = {h: censo['B1_persistencia'][h] for h in censo['B1_persistencia']}
    b2 = {h: censo['B2_media_movil_3'][h] for h in censo['B2_media_movil_3']}
    b3 = {h: censo['B3_mediana_global'][h] for h in censo['B3_mediana_global']}
    lines = []
    for h in sorted(b1):
        mejora_b2 = 100 * (b2[h]['mae'] - b1[h]['mae']) / b2[h]['mae']
        mejora_b3 = 100 * (b3[h]['mae'] - b1[h]['mae']) / b3[h]['mae']
        lines.append(
            f'- h={h}: B1 persistencia MAE {_fmt(b1[h]["mae"])} frente a '
            f'B2 {_fmt(b2[h]["mae"])} y B3 {_fmt(b3[h]["mae"])}; '
            f'autocorrelacion Pearson {_fmt(auto[h]["pearson"])}. '
            f'B1 reduce el MAE de B2 un {_fmt(mejora_b2, 1)}% y el de B3 un '
            f'{_fmt(mejora_b3, 1)}%.')
    lines.append('- B1 (persistencia) gana a la media movil en los tres '
                 'horizontes: la nota es inercial y promediar introduce retardo. '
                 'El rival a batir es B1, no B2.')
    lines.append('- El margen real de un modelo es la diferencia B1 - modelo, no '
                 'B1 - B3. B3 es una referencia tonta deliberada.')
    return lines


# ------------------------------------------------------------------ salida

def write_report(report, output_dir=None):
    """Escribe baselines.json y report.md de forma reproducible (sin fechas)."""
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = report_paths(output_dir)
    json_path.write_text(render_json(report), encoding='utf-8')
    markdown_path.write_text(render_markdown(report), encoding='utf-8')
    return json_path, markdown_path


def report_paths(output_dir):
    output_dir = Path(output_dir)
    return output_dir / 'baselines.json', output_dir / 'report.md'


def render_json(report):
    """Serializacion canonica del informe (misma que escribe write_report)."""
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False,
                      sort_keys=False) + '\n'


def check_report(report, output_dir=None):
    """True si la salida publicada reproduce exactamente el informe."""
    json_path, markdown_path = report_paths(
        output_dir if output_dir is not None else DEFAULT_OUTPUT_DIR)
    if not json_path.exists() or not markdown_path.exists():
        return False
    return (json_path.read_text(encoding='utf-8') == render_json(report)
            and markdown_path.read_text(encoding='utf-8')
            == render_markdown(report))


def run(input_path=None, output_dir=None):
    """Pipeline completo: carga, informe y escritura. Devuelve el informe."""
    path = Path(input_path) if input_path is not None else DEFAULT_INPUT
    rows = load_assessments(path)
    report = build_report(rows, input_path=path)
    write_report(report, output_dir)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Backtest walk-forward y baselines del forecast de '
                    'health_score v4 (escribe baselines.json y report.md)')
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT,
                        help='assessments.parquet de entrada (solo lectura)')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR,
                        help='directorio de salida (baselines.json y report.md)')
    parser.add_argument('--check', action='store_true',
                        help='no escribe: verifica que la salida publicada '
                             'reproduce el informe contra el parquet de entrada')
    args = parser.parse_args(argv)
    if not args.input.exists():
        print(f'Error: {MISSING_INPUT_MSG}', file=sys.stderr)
        return 1
    try:
        rows = load_assessments(args.input)
        report = build_report(rows, input_path=args.input)
        if args.check:
            if check_report(report, args.output_dir):
                print(json.dumps({'check': 'ok',
                                  'output_dir': str(args.output_dir)},))
                return 0
            print('Error: la salida publicada NO reproduce el informe generado '
                  'desde el parquet de entrada', file=sys.stderr)
            return 1
        write_report(report, args.output_dir)
    except (ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    print(json.dumps({
        'output_dir': str(args.output_dir),
        'digest_poblacion': report['poblacion']['digest_sha256'],
        'n_empresas': report['poblacion']['n_empresas'],
        'n_pares_por_h': {h: report['censo']['total_por_h'][h]['pares']
                          for h in report['censo']['total_por_h']},
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
