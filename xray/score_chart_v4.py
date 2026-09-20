"""Grafica HTML autonoma para healthscore_v4 (solo healthscore_v4).

Lee reports/score_v4/assessments.parquet y summary.json (flag --input-dir) y,
para la comparativa A-B, reports/score_v3/assessments.parquet (flag --v3-dir;
si falta, la vista 3 se degrada con un aviso declarado en la pagina). NO genera
ni modifica parquets: si falta el de v4, falla con un mensaje claro.

CINCO vistas en un unico HTML sin dependencias externas (pestañas):
  1. Evolucion mensual: varias empresas superpuestas (hasta 8), buscador por
     company_id, huecos declarados para los meses sin nota (con su motivo,
     marcador gris en el carril inferior; nunca cero ni interpolacion).
  2. Distribucion en un corte: histograma bins de 5 puntos apilado por
     confidence + barra aparte de empresas SIN nota etiquetada por motivo
     (en el ultimo corte son 382 empresas sin nota: no deben quedar invisibles).
  3. Comparacion A-B contra v3: v3 y v4 lado a lado, ordenable por v3, por v4
     y por la diferencia; tres casos marcados (solo v3 / solo v4 / ambas);
     dispersion de la diferencia SOLO sobre la poblacion comun de pares
     (company_id, month) con nota en ambas, con los tamanos de poblacion
     declarados en pantalla.
  4. Desglose del castigo por empresa y corte: de h_antes_de_ajustes a
     health_score via penalizacion_mora_puntos y
     penalizacion_multiplicador_puntos (barra descompuesta), con los insumos
     obligacion_vencida_m, mora_indice, multiplicador_deuda, t6_efectivo y
     colchon_v4 al lado; orden inicial castigo total descendente. Nota
     declarada en la pagina: el tercer canal de la obligacion vencida
     (denominador) esta DENTRO de h_antes_de_ajustes y t6_efectivo; la
     descomposicion Shapley de summary.json se muestra como referencia
     aparte, claramente etiquetada.
  5. Riesgo de divisa (capa FX): el caso concreto que ningun otro canal ve.
     Ranking de empresas por perdida YA INCURRIDA por movimiento de divisa
     (perdida_eur = eur_emision - eur_corte; positivo = perdida, negativo =
     ganancia), con perdidas y ganancias separadas visualmente; el recuento
     honesto de a cuantas empresas toca el factor (135 de 957 cambian de
     nota, 36 de forma material, 822 intactas: la poblacion se declara); el
     mapa de divisas de la cartera con su volatilidad y su deriva (marca las
     divisas sin cobertura BCE, hipervolatiles por decision, no por medicion);
     el reparto del castigo entre volatilidad y deriva; y las empresas que
     caen en la rama de intervalo (castigo minimo, proporcion no cerrable).
     Datos de la capa F3 (reports/fx_risk/fx_risk_monthly.parquet) y del
     indice F1b (reports/fx_risk/fx_index.parquet); si faltan, la vista 5 se
     degrada con un aviso declarado y NADA se inventa.

Cabecera desde el parquet y summary.json (k, alpha, beta, params_calibrados,
advertencia), nunca de constantes escritas a mano. Serializacion con
json.dumps(..., allow_nan=False), determinista. Sin nota es sin nota: el
payload jamas convierte un mes sin nota en 0. Si el payload supera el tope de
tamano se recortan los meses mas antiguos (nunca empresas) y se avisa en
pantalla; con los datos reales (30.864 filas) no hay agregacion y se declara.
"""

import argparse
import copy
import html
import json
import math
from pathlib import Path

import duckdb

from xray import paths
from xray.scoring_v4 import CONFIDENCE_LEVELS, LIMITACIONES, VERSION

SCORE_V4_DIR = paths.ROOT / 'reports' / 'score_v4'
SCORE_V3_DIR = paths.ROOT / 'reports' / 'score_v3'
FX_RISK_DIR = paths.ROOT / 'reports' / 'fx_risk'
ASSESSMENTS_NAME = 'assessments.parquet'
SUMMARY_NAME = 'summary.json'
FX_RISK_NAME = 'fx_risk_monthly.parquet'
FX_INDEX_NAME = 'fx_index.parquet'
FX_MATERIAL_THRESHOLD = 1.0
FX_INTERVAL_REASON = 'castigo_fx_acotado_por_intervalo'
MISSING_MESSAGE = (f'falta reports/score_v4/{ASSESSMENTS_NAME}, '
                   'ejecuta antes xray.scoring_io_v4')
DEFAULT_TITLE = 'Blaubeere · Healthscore v4 · notas, confianza y castigos'
DEMO_TITLE = 'DEMO SINTETICA · Blaubeere · Healthscore v4 · notas y castigos'
DEMO_BANNER = (
    '<div id="demo-sintetica-banner" role="alert" '
    'style="position:sticky;top:0;z-index:1000;background:#8f1d1d;color:#ffffff;'
    'padding:14px 22px;font-weight:700;font-size:15px;text-align:center;'
    'letter-spacing:.3px;box-shadow:0 2px 10px rgba(0,0,0,.35)">'
    '&#9888; DEMOSTRACIÓN CON DATOS SINTÉTICOS GENERADOS ALEATORIAMENTE. '
    'Ninguna cifra de esta página corresponde a una empresa real. '
    'Sirve únicamente para revisar el diseño de la gráfica.</div>')
MAX_BYTES = 25 * 1024 * 1024
TARGET_BYTES = 23 * 1024 * 1024
V4_FORMULA = ('H_antes_de_ajustes = 100 * (C6 + colchon_aplicable + k*R_hist) / '
              '(C6 + colchon_aplicable + T6_efectivo + k); '
              'T6_efectivo = P6 + D6 + deficit_servicio_6 + obligacion_vencida_m '
              '(los None se excluyen, nulo nunca es cero); '
              'colchon_aplicable = min(colchon_v4, alpha * T6_efectivo); '
              'health_score = h_antes_de_ajustes * (1 - beta * mora_indice) * '
              'multiplicador_deuda = h_antes_de_ajustes - '
              'penalizacion_mora_puntos - penalizacion_multiplicador_puntos')
SCHEMA = (
    ('version', 'VARCHAR'), ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'),
    ('month', 'DATE'), ('as_of', 'DATE'), ('health_score', 'DOUBLE'),
    ('confidence', 'VARCHAR'), ('n_meses_ventana', 'INTEGER'),
    ('n_meses_con_actividad', 'INTEGER'), ('ventana_parcial', 'BOOLEAN'),
    ('c6', 'DOUBLE'), ('p6', 'DOUBLE'), ('d6', 'DOUBLE'),
    ('deficit_servicio_6', 'DOUBLE'), ('obligacion_vencida_m', 'DOUBLE'),
    ('t6_efectivo', 'DOUBLE'), ('r_hist', 'DOUBLE'), ('colchon_v4', 'DOUBLE'),
    ('colchon_aplicable', 'DOUBLE'), ('mora_indice', 'DOUBLE'),
    ('multiplicador_deuda', 'DOUBLE'), ('h_antes_de_ajustes', 'DOUBLE'),
    ('penalizacion_mora_puntos', 'DOUBLE'),
    ('penalizacion_multiplicador_puntos', 'DOUBLE'),
    ('volumen_ambiguo_eur', 'DOUBLE'), ('volumen_ambiguo_pct', 'DOUBLE'),
    ('indice_fx', 'DOUBLE'), ('indice_fx_min', 'DOUBLE'),
    ('indice_es_intervalo', 'BOOLEAN'), ('indice_fx_aplicado', 'DOUBLE'),
    ('penalizacion_fx_puntos', 'DOUBLE'), ('beta_fx', 'DOUBLE'),
    ('k', 'DOUBLE'), ('alpha', 'DOUBLE'), ('beta', 'DOUBLE'),
    ('reasons', 'VARCHAR[]'), ('excluida', 'BOOLEAN'),
)
# Campos OPCIONALES del parquet: sin ellos el lector no falla (parquets
# anteriores a la capa FX o a la exclusion v4 se siguen leyendo). La clave
# ausente es dato ausente, nunca cero.
OPTIONAL_FIELDS = ('excluida', 'indice_fx', 'indice_fx_min',
                   'indice_es_intervalo', 'indice_fx_aplicado',
                   'penalizacion_fx_puntos', 'beta_fx')


def _clean(value):
    """ float finito o None: NaN/inf no se serializan ni se inventan datos."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _reasons_text(reasons):
    return ' · '.join(str(reason) for reason in reasons) if reasons else 'sin motivo registrado'


def _missing_assessments_message(directory):
    """Mensaje de fichero ausente; con --input-dir propio menciona la ruta usada."""
    if directory.name == 'score_v4' and directory.parent.name == 'reports':
        # Directorio por defecto (el modulo resuelve paths.ROOT en cada llamada,
        # tambien en tests que lo parchean): mensaje literal de siempre.
        return MISSING_MESSAGE
    return (f'falta {directory / ASSESSMENTS_NAME}, ejecuta antes xray.scoring_io_v4 '
            f'(directorio de entrada: {directory})')


def load_assessments(parquet_path):
    """Lee el parquet v4, valida version y parametros unicos, sanea no finitos."""
    if not parquet_path.exists():
        raise SystemExit(_missing_assessments_message(parquet_path.parent))
    con = duckdb.connect(':memory:')
    try:
        cursor = con.execute('SELECT * FROM read_parquet(?)', [str(parquet_path)])
        columns = [column[0] for column in cursor.description]
        # 'excluida' es OPCIONAL (parquets anteriores a la exclusion v4): sin
        # la columna se asume que no hay ninguna empresa excluida.
        required = [name for name, _ in SCHEMA if name not in OPTIONAL_FIELDS]
        missing = [name for name in required if name not in columns]
        if missing:
            raise ValueError(f'assessments.parquet sin columnas esperadas: {", ".join(missing)}')
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        con.close()
    if not raw:
        raise ValueError('assessments.parquet esta vacio')
    versions = {row['version'] for row in raw}
    if len(versions) != 1 or versions.pop() != VERSION:
        raise ValueError('la grafica solo admite healthscore_v4; no se mezclan versiones')
    for field in ('k', 'alpha', 'beta'):
        values = {_clean(row[field]) for row in raw}
        if len(values) != 1 or next(iter(values)) is None:
            raise ValueError(f'el parquet debe declarar un unico {field} finito')
    rows = []
    for row in raw:
        reasons = tuple(str(reason) for reason in (row['reasons'] or []))
        rows.append({
            'company_id': str(row['company_id']), 'group_id': row['group_id'],
            'month': row['month'], 'health_score': _clean(row['health_score']),
            'confidence': row['confidence'] if row['confidence'] in CONFIDENCE_LEVELS else 'ninguna',
            'ventana_parcial': bool(row['ventana_parcial']),
            'excluida': bool(row.get('excluida')),
            'obligacion_vencida_m': _clean(row['obligacion_vencida_m']),
            't6_efectivo': _clean(row['t6_efectivo']),
            'r_hist': _clean(row['r_hist']),
            'colchon_v4': _clean(row['colchon_v4']),
            'indice_fx': _clean(row.get('indice_fx')),
            'indice_fx_min': _clean(row.get('indice_fx_min')),
            'indice_es_intervalo': bool(row.get('indice_es_intervalo')),
            'indice_fx_aplicado': _clean(row.get('indice_fx_aplicado')),
            'penalizacion_fx_puntos': _clean(row.get('penalizacion_fx_puntos')),
            'beta_fx': _clean(row.get('beta_fx')),
            'colchon_aplicable': _clean(row['colchon_aplicable']),
            'mora_indice': _clean(row['mora_indice']),
            'multiplicador_deuda': _clean(row['multiplicador_deuda']),
            'h_antes_de_ajustes': _clean(row['h_antes_de_ajustes']),
            'penalizacion_mora_puntos': _clean(row['penalizacion_mora_puntos']),
            'penalizacion_multiplicador_puntos': _clean(row['penalizacion_multiplicador_puntos']),
            'k': _clean(row['k']), 'alpha': _clean(row['alpha']), 'beta': _clean(row['beta']),
            'reasons': reasons,
        })
    return rows


def load_v3_scores(parquet_path):
    """Notas v3 por (company_id, month) para la comparativa A-B.

    Devuelve None si el fichero no existe (la vista 3 se degrada con aviso);
    lanza ValueError si existe pero no es healthscore_v3 (no se mezclan
    versiones ni se inventan comparativas).
    """
    if not parquet_path.exists():
        return None, str(parquet_path)
    con = duckdb.connect(':memory:')
    try:
        cursor = con.execute(
            'SELECT company_id, month, health_score, version FROM read_parquet(?)',
            [str(parquet_path)])
        raw = cursor.fetchall()
    finally:
        con.close()
    versions = {row[3] for row in raw}
    if len(versions) != 1 or versions.pop() != 'healthscore_v3':
        raise ValueError('la comparativa A-B requiere healthscore_v3 en '
                         f'{parquet_path}; no se mezclan versiones')
    scores = {}
    for company_id, month, score, _version in raw:
        if score is None or not isinstance(score, float) or not math.isfinite(score):
            continue
        scores[(str(company_id), month.isoformat())] = score
    return scores, str(parquet_path)


def _summary_get(summary, keys):
    """Primer valor presente en summary.json (nivel superior o anidado)."""
    if not isinstance(summary, dict):
        return None
    for key in keys:
        if summary.get(key) is not None:
            return summary[key]
    for value in summary.values():
        if isinstance(value, dict):
            for key in keys:
                if value.get(key) is not None:
                    return value[key]
    return None


def _formula_text(summary):
    formula = _summary_get(summary, ('formula', 'formulas', 'formula_text'))
    if formula is None:
        # Fallback: formula cerrada del motor (xray/scoring_v4.py). No es un
        # parametro calibrado: es la definicion del indice.
        formula = V4_FORMULA
    return str(formula)


def score_segments(scores):
    """Trazos de la serie: cada mes sin nota parte la linea, nunca se une."""
    segments = []
    current = []
    for index, score in enumerate(scores):
        if score is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append([index, score])
    if current:
        segments.append(current)
    return segments


def _shapley_top(summary):
    """Top de empresas mas penalizadas segun la descomposicion Shapley del
    summary (riesgo_triple_conteo.descomposicion_castigo.top_penalizadas).

    NO es la misma medida que el castigo del parquet (que solo cubre mora y
    multiplicador); se embebe aparte y en pantalla se etiqueta su origen.
    """
    castigo = _summary_get(summary, ('descomposicion_castigo',)) or {}
    top = castigo.get('top_penalizadas') or []
    out = []
    for item in top:
        if not isinstance(item, dict):
            continue
        out.append({
            'id': str(item.get('company_id', '')),
            'final': _clean(item.get('nota_final')),
            'sin_castigo': _clean(item.get('nota_sin_castigos')),
            'castigo': _clean(item.get('castigo_total_puntos')),
            'denominador': _clean(item.get('castigo_obligacion_en_denominador_puntos')),
            'mora': _clean(item.get('castigo_mora_indice_puntos')),
            'multiplicador': _clean(item.get('castigo_multiplicador_deuda_puntos')),
        })
    return out


def load_fx_risk(parquet_path):
    """Capa FX empresa-mes de F3: perdida ya incurrida, indice e intervalos.

    Devuelve (filas, cartera): filas indexadas por (company_id, mes ISO) y
    agregado por divisa y corte. Si el fichero no existe devuelve (None, None)
    y la vista 5 se degrada con un aviso declarado; NUNCA se estima un valor.
    """
    if not parquet_path.exists():
        return None, None
    con = duckdb.connect(':memory:')
    try:
        cursor = con.execute(
            'SELECT company_id, month, perdida_eur, importe_vivo_eur_corte, '
            'indice_fx, indice_fx_vol, indice_fx_deriva, indice_fx_min, '
            'indice_es_intervalo, tiene_opacas, solo_opacas, por_divisa '
            'FROM read_parquet(?)', [str(parquet_path)])
        columns = [column[0] for column in cursor.description]
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        con.close()
    rows = {}
    cartera = {}
    for row in raw:
        month = row['month'].date() if hasattr(row['month'], 'date') else row['month']
        month_key = month.isoformat()
        por_divisa = row['por_divisa']
        if isinstance(por_divisa, str):
            por_divisa = json.loads(por_divisa or '[]')
        por_divisa = por_divisa or []
        monedas = tuple(sorted({str(item.get('divisa')) for item in por_divisa
                                if item.get('divisa') != 'EUR'
                                and (item.get('n_facturas') or 0) > 0}))
        rows[(str(row['company_id']), month_key)] = {
            'perdida_eur': _clean(row['perdida_eur']),
            'importe_vivo_eur_corte': _clean(row['importe_vivo_eur_corte']),
            'indice_fx': _clean(row['indice_fx']),
            'indice_fx_vol': _clean(row['indice_fx_vol']),
            'indice_fx_deriva': _clean(row['indice_fx_deriva']),
            'indice_fx_min': _clean(row['indice_fx_min']),
            'indice_es_intervalo': bool(row['indice_es_intervalo']),
            'tiene_opacas': bool(row['tiene_opacas']),
            'solo_opacas': bool(row['solo_opacas']),
            'monedas': monedas,
        }
        # Agregado por divisa y corte: dinero ya perdido/ganado por divisa y
        # recuento de facturas y empresas. Es la unica fuente del mapa de
        # divisas; no se estima ninguna cifra que no este en el parquet.
        stats = cartera.setdefault(month_key, {})
        for item in por_divisa:
            divisa = str(item.get('divisa'))
            slot = stats.setdefault(divisa, {
                'divisa': divisa, 'perdida_eur': 0.0, 'n_facturas': 0,
                'n_empresas': 0, 'importe_original': 0.0, 'n_opacas': 0,
                'n_valorables_corte': 0, 'n_valorables_emision': 0,
                'n_rescatadas_fmi': 0, 'n_con_perdida': 0,
                'perdida_conocida': False})
            loss = item.get('perdida_eur')
            if isinstance(loss, float) and math.isfinite(loss):
                slot['perdida_eur'] += loss
            # Una divisa con al menos una factura con perdida calculable tiene
            # tipo (BCE o FMI); sin ninguna, su perdida es DESCONOCIDA, no cero.
            # La bandera separa '0,00 EUR medido' de 'sin dato'.
            if int(item.get('n_con_perdida') or 0) > 0:
                slot['perdida_conocida'] = True
            slot['n_facturas'] += int(item.get('n_facturas') or 0)
            slot['n_empresas'] += 1
            slot['n_opacas'] += int(item.get('n_opacas') or 0)
            slot['n_valorables_corte'] += int(
                item.get('n_valorables_corte') or 0)
            slot['n_valorables_emision'] += int(
                item.get('n_valorables_emision') or 0)
            slot['n_rescatadas_fmi'] += int(item.get('n_rescatadas_fmi') or 0)
            slot['n_con_perdida'] += int(item.get('n_con_perdida') or 0)
            amount = item.get('importe_original')
            if isinstance(amount, float) and math.isfinite(amount):
                slot['importe_original'] += amount
    for stats in cartera.values():
        for slot in stats.values():
            slot['perdida_eur'] = _clean(slot['perdida_eur'])
            slot['importe_original'] = _clean(slot['importe_original'])
    return rows, cartera


def load_fx_index(parquet_path):
    """Indice FX por divisa-mes de F1b (volatilidad, deriva y tramos).

    Devuelve None si falta el fichero: el mapa de divisas se declara no
    disponible y no se dibuja ningun punto sin medicion detras.
    """
    if not parquet_path.exists():
        return None
    con = duckdb.connect(':memory:')
    try:
        raw = con.execute(
            'SELECT currency, month, cobertura_bce, vol_anualizada, '
            'deriva_valor, deriva_componente, tramo_volatilidad, tramo_deriva, '
            'tramo, motivo FROM read_parquet(?)', [str(parquet_path)]).fetchall()
    finally:
        con.close()
    rows = []
    for currency, month, bce, vol, dval, dcomp, tv, td, tramo, motivo in raw:
        if hasattr(month, 'date'):
            month = month.date()
        rows.append({
            'c': str(currency), 'm': month.isoformat(), 'bce': bool(bce),
            'vol': _clean(vol), 'dv': _clean(dval), 'dc': _clean(dcomp),
            'tv': tv, 'td': td, 't': tramo, 'motivo': motivo,
        })
    return rows


def _fx_relevant(fx):
    """La fila FX merece ir embebida solo si aporta algo (no EUR sin efecto)."""
    return (fx['perdida_eur'] is not None or fx['tiene_opacas']
            or fx['indice_es_intervalo'] or (fx['indice_fx'] or 0.0) > 0.0)


def _fx_block(assessments_rows, fx_rows, cartera, fx_index, fx_source, fx_index_source):
    """Resumen de la capa FX con SU poblacion declarada en cada cifra.

    Todas las cifras se miden aqui contra el parquet; ninguna se copia de un
    informe. Si falta la capa F3, devuelve el bloque degradado declarado.
    """
    if fx_rows is None:
        return {
            'fx_disponible': False, 'fx_ruta': fx_source,
            'fx_index_disponible': fx_index is not None,
            'fx_index_ruta': fx_index_source,
            'fx_resumen': None, 'fx_cortes': {}, 'fx_divisas': [],
            'fx_cartera': {}, 'fx_nota': None,
        }
    evaluables = [row for row in assessments_rows
                  if not row['excluida'] and row['health_score'] is not None]
    change_rows = [row for row in evaluables
                   if (row['penalizacion_fx_puntos'] or 0.0) > 0.0]
    evaluable_companies = {row['company_id'] for row in evaluables}
    change_companies = {row['company_id'] for row in change_rows}
    material_companies = {row['company_id'] for row in evaluables
                          if (row['penalizacion_fx_puntos'] or 0.0) >= FX_MATERIAL_THRESHOLD}
    interval_rows = [row for row in evaluables if row['indice_es_intervalo']]
    exposure_companies = {company for (company, _month), fx in fx_rows.items()
                          if fx['monedas']} & evaluable_companies
    betas = {row['beta_fx'] for row in assessments_rows if row['beta_fx'] is not None}
    beta_fx = next(iter(betas)) if len(betas) == 1 else None
    # Desglose volatilidad vs deriva: puntos atribuidos a cada componente sobre
    # las empresa-mes CON castigo (misma poblacion que el efecto total). El
    # indice combinado es el mas severo de los dos; se publican los dos efectos
    # por separado y su reparto, no una suma aditiva.
    volume_points = 0.0
    drift_points = 0.0
    if beta_fx is not None:
        for row in change_rows:
            fx = fx_rows.get((row['company_id'], row['month'].isoformat()))
            if fx is None:
                continue
            after_mult = (row['health_score'] or 0.0) + (row['penalizacion_fx_puntos'] or 0.0)
            if fx['indice_fx_vol'] is not None:
                volume_points += after_mult * beta_fx * fx['indice_fx_vol']
            if fx['indice_fx_deriva'] is not None:
                drift_points += after_mult * beta_fx * fx['indice_fx_deriva']
    attributable = volume_points + drift_points
    resumen = {
        'poblacion_evaluable': 'filas con health_score no nulo y excluida=false de assessments.parquet',
        'evaluable_empresas': len(evaluable_companies),
        'evaluable_empresa_mes': len(evaluables),
        'cambian_empresas': len(change_companies),
        'cambian_empresa_mes': len(change_rows),
        'materiales_empresas': len(material_companies),
        'materiales_empresa_mes': sum(1 for row in evaluables
                                      if (row['penalizacion_fx_puntos'] or 0.0) >= FX_MATERIAL_THRESHOLD),
        'umbral_material': FX_MATERIAL_THRESHOLD,
        'intactas_empresas': len(evaluable_companies) - len(change_companies),
        'exposicion_empresas': len(exposure_companies),
        'sin_exposicion_empresas': len(evaluable_companies) - len(exposure_companies),
        'intervalo_empresas': len({row['company_id'] for row in interval_rows}),
        'intervalo_empresa_mes': len(interval_rows),
        'beta_fx': beta_fx,
        'vol_puntos': _clean(volume_points),
        'deriva_puntos': _clean(drift_points),
        'vol_reparto': _clean(volume_points / attributable) if attributable else None,
        'deriva_reparto': _clean(drift_points / attributable) if attributable else None,
        'desglose_poblacion': 'empresa-mes del panel con health_score no nulo, excluida=false y penalizacion_fx_puntos>0',
    }
    cortes = {}
    for (company, month), fx in fx_rows.items():
        slot = cortes.setdefault(month, {
            'n': 0, 'con_dato': 0, 'perdiendo': 0, 'ganando': 0, 'sin_efecto': 0, 'sin_dato': 0,
            'perdida_neta': 0.0, 'perdida_bruta': 0.0, 'ganancia_bruta': 0.0,
            'indice_positivo': 0, 'intervalo': 0, 'opacas': 0, 'solo_opacas': 0})
        slot['n'] += 1
        loss = fx['perdida_eur']
        if loss is None:
            slot['sin_dato'] += 1
        elif loss > 0:
            slot['con_dato'] += 1
            slot['perdiendo'] += 1
            slot['perdida_neta'] += loss
            slot['perdida_bruta'] += loss
        elif loss < 0:
            slot['con_dato'] += 1
            slot['ganando'] += 1
            slot['perdida_neta'] += loss
            slot['ganancia_bruta'] += loss
        else:
            slot['con_dato'] += 1
            slot['sin_efecto'] += 1
        if (fx['indice_fx'] or 0.0) > 0:
            slot['indice_positivo'] += 1
        if fx['indice_es_intervalo']:
            slot['intervalo'] += 1
        if fx['tiene_opacas']:
            slot['opacas'] += 1
        if fx['solo_opacas']:
            slot['solo_opacas'] += 1
    for slot in cortes.values():
        for field in ('perdida_neta', 'perdida_bruta', 'ganancia_bruta'):
            slot[field] = _clean(slot[field])
    cartera_out = {month: sorted(stats.values(), key=lambda item: abs(item['perdida_eur'] or 0.0),
                                 reverse=True)
                   for month, stats in cartera.items()}
    # Nota de verificacion del ultimo corte: la cifra que mas circula (el mayor
    # movimiento absoluto) puede ser una GANANCIA; se declara con el signo leido
    # del parquet para que nadie la lea al reves.
    last_month = max(cortes) if cortes else None
    nota = None
    if last_month is not None:
        candidates = [(fx['perdida_eur'], company) for (company, month), fx in fx_rows.items()
                      if month == last_month and fx['perdida_eur'] is not None]
        if candidates:
            biggest = max(candidates, key=lambda item: abs(item[0]))
            if biggest[0] < 0:
                nota = (f'Convencion de signo: perdida_eur = eur_emision - eur_corte; '
                        f'positivo = perdida, negativo = ganancia. El mayor movimiento '
                        f'absoluto del corte {last_month} es {biggest[1]} con '
                        f'{abs(biggest[0]):,.2f} EUR de GANANCIA, no de perdida. '
                        'La mayor PERDIDA del corte es la primera fila del ranking de abajo.')
    return {
        'fx_disponible': True, 'fx_ruta': fx_source,
        'fx_index_disponible': fx_index is not None, 'fx_index_ruta': fx_index_source,
        'fx_resumen': resumen, 'fx_cortes': cortes, 'fx_divisas': fx_index or [],
        'fx_cartera': cartera_out, 'fx_nota': nota,
    }


def build_payload(parquet_path, summary_path, v3_path=None, fx_path=None, fx_index_path=None):
    rows = load_assessments(parquet_path)
    summary = {}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
    v3_scores, v3_source = (None, None)
    if v3_path is not None:
        v3_scores, v3_source = load_v3_scores(v3_path)
    fx_rows, fx_cartera = (None, None)
    if fx_path is not None:
        fx_rows, fx_cartera = load_fx_risk(fx_path)
    fx_index = load_fx_index(fx_index_path) if fx_index_path is not None else None
    months = sorted({row['month'] for row in rows})
    params = {field: rows[0][field] for field in ('k', 'alpha', 'beta')}
    # Catalogo de conjuntos de motivos: los meses sin nota comparten patrones
    # de reasons; guardar un indice (y no la lista completa) por fila mantiene
    # el payload razonable sin perder ni un solo motivo.
    reasons_sets = []

    def reasons_index(reasons):
        key = tuple(reasons)
        try:
            return reasons_sets.index(key)
        except ValueError:
            reasons_sets.append(key)
            return len(reasons_sets) - 1

    companies = {}
    cuts = {month: {'total': 0, 'excluidas': 0, 'confidence': {level: 0 for level in CONFIDENCE_LEVELS},
                    'bins': [{level: 0 for level in CONFIDENCE_LEVELS} for _ in range(20)],
                    'null_motivos': {}} for month in months}
    full_cuts = {month: {'total': 0, 'confidence': {level: 0 for level in CONFIDENCE_LEVELS},
                         'bins': [{level: 0 for level in CONFIDENCE_LEVELS} for _ in range(20)],
                         'null_motivos': {}} for month in months}
    for row in rows:
        company = companies.setdefault(row['company_id'], {
            'id': row['company_id'], 'group_id': row['group_id'], 'points': {},
            'excluida': False})
        if company['group_id'] != row['group_id']:
            raise ValueError(f"el grupo de {company['id']} cambio entre meses")
        if row['excluida']:
            company['excluida'] = True
        company['points'][row['month']] = row
        cut = cuts[row['month']]
        # Distribucion por defecto del corte: SIN las excluidas. La variante
        # 'todas' acumula TODAS las filas del corte; solo se publica si hay
        # excluidas. Dos poblaciones distintas, nunca mezcladas.
        targets = [full_cuts[row['month']]]
        if row['excluida']:
            cut['excluidas'] += 1
        else:
            targets.append(cut)
        for target in targets:
            target['total'] += 1
            target['confidence'][row['confidence']] += 1
            score = row['health_score']
            if score is None:
                motivo = _reasons_text(row['reasons'])
                target['null_motivos'][motivo] = target['null_motivos'].get(motivo, 0) + 1
            else:
                target['bins'][min(19, int(score // 5))][row['confidence']] += 1
    company_rows = []
    for company_id in sorted(companies, key=lambda key: companies[key]['id']):
        company = companies[company_id]
        points = []
        for month in months:
            row = company['points'].get(month)
            if row is None:
                # El corte no existe para esta empresa: hueco absoluto ({}).
                points.append({})
                continue
            point = {
                'c': CONFIDENCE_LEVELS.index(row['confidence']),
                'x': 1 if row['ventana_parcial'] else 0,
                'r': reasons_index(row['reasons']),
            }
            if fx_rows is not None:
                fx = fx_rows.get((company['id'], month.isoformat()))
                if fx is not None and _fx_relevant(fx):
                    # Solo se embebe lo que aporta: perdida ya incurrida (signo
                    # del parquet), indice aplicado e indices de cada
                    # componente, y las banderas de intervalo y de opacas.
                    point['pf'] = fx['perdida_eur']
                    point['vi'] = fx['importe_vivo_eur_corte']
                    point['ix'] = fx['indice_fx']
                    point['fi'] = 1 if fx['indice_es_intervalo'] else 0
                    point['fo'] = 1 if fx['tiene_opacas'] else 0
                    point['so'] = 1 if fx['solo_opacas'] else 0
                    point['fv'] = fx['indice_fx_vol']
                    point['fd'] = fx['indice_fx_deriva']
            if v3_scores is not None:
                v3_score = v3_scores.get((company['id'], month.isoformat()))
                if v3_score is not None:
                    point['v3'] = v3_score
            if row['health_score'] is not None:
                point.update({
                    's': row['health_score'],
                    'h': row['h_antes_de_ajustes'],
                    'pm': row['penalizacion_mora_puntos'],
                    'pd': row['penalizacion_multiplicador_puntos'],
                    'ob': row['obligacion_vencida_m'],
                    'mi': row['mora_indice'],
                    'md': row['multiplicador_deuda'],
                    't6': row['t6_efectivo'],
                    'cv': row['colchon_v4'],
                    'rh': row['r_hist'],
                    'fxp': row['penalizacion_fx_puntos'],
                    'fa': row['indice_fx_aplicado'],
                })
            points.append(point)
        scores = [point.get('s') for point in points]
        company_rows.append({
            'id': company['id'], 'group_id': company['group_id'],
            'excluida': company['excluida'],
            'scored_months': sum(score is not None for score in scores),
            'segments': score_segments(scores), 'points': points,
        })
    company_rows.sort(key=lambda row: (-row['scored_months'], row['id']))
    total_rows = sum(bool(point) for company in company_rows for point in company['points'])
    params_calibrados = summary.get('params_calibrados')
    exclusion_summary = summary.get('exclusion') if isinstance(summary.get('exclusion'), dict) else {}
    n_excluidas = sum(1 for company in company_rows if company['excluida'])
    fx = _fx_block(rows, fx_rows, fx_cartera, fx_index, str(fx_path) if fx_path is not None else None,
                   str(fx_index_path) if fx_index_path is not None else None)
    return {
        'model_version': str(_summary_get(summary, ('model_version', 'version')) or VERSION),
        'formula': _formula_text(summary),
        'data_workspace': str(_summary_get(summary, ('data_workspace', 'workspace')) or 'desconocido'),
        'k': params['k'], 'alpha': params['alpha'], 'beta': params['beta'],
        'params_calibrados': (params_calibrados if isinstance(params_calibrados, dict) else {}),
        'advertencia': _summary_get(summary, ('advertencia', 'aviso_params')),
        'total_companies': len(company_rows),
        'n_excluidas': n_excluidas,
        'motivo_exclusion': 'excluida_nota_cero_persistente',
        'declaracion_exclusion': exclusion_summary.get('declaracion'),
        'exclusion_activada': bool(exclusion_summary.get('activada')) if exclusion_summary else bool(n_excluidas),
        'limitations': list(_summary_get(summary, ('limitaciones', 'limitations',
                                                   'supuestos_y_limitaciones')) or LIMITACIONES),
        'months': [month.isoformat() for month in months],
        'reasons_sets': [list(reasons) for reasons in reasons_sets],
        'companies': company_rows,
        'cuts': {month.isoformat(): ({**cut, 'todas': full_cuts[month]}
                                     if cut['excluidas'] else cut)
                 for month, cut in cuts.items()},
        'v3_disponible': v3_scores is not None,
        'v3_ruta': v3_source,
        'shapley_top': _shapley_top(summary),
        'shapley_corte': str(_summary_get(summary, ('corte',)) or 'corte declarado en summary.json'),
        'filas_parquet': total_rows,
        'agregacion': ('Sin agregacion: cada fila del parquet (empresa x mes) va embebida '
                       'con su nota, su confianza y sus insumos; los meses sin nota van '
                       'huecos, con su motivo, nunca con valor 0.'),
        'reduced_months': False,
        'es_demo_sintetica': bool(_summary_get(summary, ('es_demo_sintetica',
                                                         'demo_sintetica'))),
        'banner_texto': _summary_get(summary, ('banner_texto', 'aviso_banner')),
        **fx,
    }


def _embed(payload):
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return data.replace('<', '\\u003c')


def _aviso_banner(texto):
    texto = html.escape(str(texto), quote=False)
    return ('<div id="aviso-banner" role="alert" '
            'style="position:sticky;top:0;z-index:1000;background:#8f1d1d;color:#ffffff;'
            'padding:14px 22px;font-weight:700;font-size:15px;text-align:center;'
            'letter-spacing:.3px;box-shadow:0 2px 10px rgba(0,0,0,.35)">'
            f'&#9888; {texto}</div>')


def render_chart(payload):
    page = PAGE.replace('__PAYLOAD_DATA__', _embed(payload))
    if payload.get('es_demo_sintetica'):
        # Banner fijo y en lo alto solo cuando los datos son sinteticos: una
        # grafica con numeros inventados sin marcar es peor que no tener grafica.
        page = page.replace(f'<title>{DEFAULT_TITLE}</title>', f'<title>{DEMO_TITLE}</title>')
        page = page.replace('<body>\n', '<body>\n' + DEMO_BANNER + '\n', 1)
    elif payload.get('banner_texto'):
        # Mismo mecanismo de banner fijo, con texto configurable desde summary.json
        # (campo banner_texto): para avisos sobre datos reales, p.ej. defectos conocidos.
        page = page.replace('<body>\n', '<body>\n' + _aviso_banner(payload['banner_texto']) + '\n', 1)
    return page


def _reduced_payload(parquet_path, summary_path, v3_path=None, limit=TARGET_BYTES,
                     fx_path=None, fx_index_path=None):
    """Payload completo; si supera el tope, recorta meses antiguos (nunca empresas)."""
    payload = build_payload(parquet_path, summary_path, v3_path, fx_path, fx_index_path)
    if len(_embed(payload).encode('utf-8')) <= limit:
        return payload
    total_months = len(payload['months'])
    keep = total_months
    while keep > 1:
        keep = max(1, keep // 2)
        offset = total_months - keep
        trimmed = copy.deepcopy(payload)
        trimmed['months'] = trimmed['months'][offset:]
        kept = set(trimmed['months'])
        for company in trimmed['companies']:
            company['points'] = company['points'][offset:]
            company['segments'] = [[[index - offset, score] for index, score in segment]
                                    for segment in company['segments'] if segment[0][0] >= offset]
            company['scored_months'] = sum(point is not None and point.get('s') is not None
                                           for point in company['points'])
        trimmed['cuts'] = {month: cut for month, cut in trimmed['cuts'].items() if month in kept}
        # La capa FX se recorta con la misma disciplina: nunca se recortan
        # empresas, solo meses; las divisas y agregados que quedan fuera se
        # eliminan enteros (jamas se mezclan meses de poblaciones distintas).
        trimmed['fx_divisas'] = [row for row in trimmed['fx_divisas'] if row['m'] in kept]
        trimmed['fx_cartera'] = {month: stats for month, stats in trimmed['fx_cartera'].items()
                                 if month in kept}
        trimmed['fx_cortes'] = {month: slot for month, slot in trimmed['fx_cortes'].items()
                                if month in kept}
        trimmed['reduced_months'] = True
        trimmed['agregacion'] = ('RESTRICCION DE TAMANO: los meses mas antiguos del parquet se '
                                 'recortaron para que el HTML quepa; solo se embeben los '
                                 f'{keep} meses mas recientes (nunca se recortan empresas).')
        if len(_embed(trimmed).encode('utf-8')) <= limit:
            return trimmed
    raise ValueError('el payload no cabe ni con un solo mes; no se muestrean empresas')


PAGE = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blaubeere · Healthscore v4 · notas, confianza y castigos</title>
<style>
:root{color-scheme:light;--ink:#142c42;--muted:#607183;--blue:#186c98;--orange:#b85517;--red:#8f1d1d;--line:#dce5eb;--bg:#f3f6f8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,sans-serif}main{max-width:1280px;margin:0 auto;padding:36px 28px}h1{font-size:clamp(26px,4vw,38px);letter-spacing:-1px;margin:6px 0}h2{font-size:18px;margin:0 0 10px}p{margin:6px 0}.eyebrow{font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--blue)}.muted{color:var(--muted)}.panel{background:white;border:1px solid var(--line);border-radius:16px;padding:24px;margin-top:22px}.controls{display:grid;grid-template-columns:1fr 1fr .9fr;gap:18px}label{font-size:13px;font-weight:650;display:block}select,input{display:block;width:100%;font:inherit;color:var(--ink);border:1px solid #c9d6df;border-radius:8px;background:#fff;margin-top:6px;padding:10px}select:focus,input:focus{outline:3px solid #8dcae9;outline-offset:2px}.legend{display:flex;gap:20px;flex-wrap:wrap;margin:18px 0 4px;font-weight:650;font-size:13px}.swatch{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:7px;vertical-align:-1px}.chart-scroll{overflow-x:auto}svg{display:block;width:100%;min-width:640px;height:auto}svg text{font-family:inherit}.note{background:#eef5f9;border-radius:8px;padding:12px 16px;font-size:13px;margin-top:14px}.warning{background:#fff4df;color:#744915;border:1px solid #eed3a1}.slider-row{display:grid;grid-template-columns:220px 1fr;align-items:center;gap:20px;margin:20px 0 12px}input[type=range]{width:100%;margin-top:0;accent-color:var(--blue)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.card{border:1px solid var(--line);border-top:3px solid var(--blue);border-radius:10px;padding:18px;min-width:0}.score{font-size:32px;line-height:1.2;font-weight:700;margin:8px 0}.small{font-size:12px}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.pill{background:var(--bg);padding:5px 9px;border-radius:5px;font-size:12px}.histo-grid{display:grid;grid-template-columns:1.7fr 1fr;gap:20px;align-items:start}.table-scroll{overflow:auto;max-height:520px}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{background:#f1f5f8;position:sticky;top:0}td.n,th.n{text-align:right}.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:center;padding-top:22px}.filters label{display:flex;align-items:center;gap:6px;font-weight:600}.filters input{width:auto;margin-top:0;accent-color:var(--blue)}.pager{display:flex;gap:14px;align-items:center;margin-top:14px}.pager button{font:inherit;font-weight:650;border:1px solid #c9d6df;background:#fff;border-radius:8px;padding:8px 16px;cursor:pointer}.pager button:disabled{opacity:.4;cursor:default}.footer-note{font-size:12px;color:var(--muted);margin-top:22px;overflow-wrap:anywhere}.source,code{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}.formula{background:var(--bg);padding:10px 12px;border-radius:8px;font-size:13px}.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#fff}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}.tabs button{font:inherit;font-weight:700;border:1px solid #c9d6df;background:#fff;border-radius:999px;padding:9px 18px;cursor:pointer}.tabs button[aria-selected=true]{background:var(--blue);border-color:var(--blue);color:#fff}section[hidden]{display:none}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.chip{display:inline-flex;align-items:center;gap:6px;background:#e8f1f7;border:1px solid #bcd6e4;border-radius:999px;padding:5px 10px;font-size:13px;font-weight:650}.chip button{border:0;background:transparent;font-weight:700;cursor:pointer;padding:0 2px;color:var(--muted)}.dropdown{position:relative}.dropdown-list{position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #c9d6df;border-radius:8px;max-height:260px;overflow:auto;z-index:20;box-shadow:0 8px 22px rgba(20,44,66,.12)}.dropdown-list div{padding:8px 12px;cursor:pointer;font-size:13px}.dropdown-list div:hover{background:#eef5f9}.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}.stat{border:1px solid var(--line);border-radius:10px;padding:10px 12px}.stat b{display:block;font-size:20px}.bar-decomp{display:flex;height:16px;border-radius:4px;overflow:hidden;background:#eef2f5;min-width:120px}
@media(max-width:760px){main{padding:20px 12px}.panel{padding:16px}.controls{grid-template-columns:1fr}.slider-row{grid-template-columns:1fr;gap:6px}}
</style>
</head>
<body>
<main>
<header>
<div class="eyebrow">Blaubeere · Estado observado, no predicción</div>
<h1>Healthscore de flujo mensual · v4</h1>
<p class="muted">Una nota por empresa y mes cerrado, con su confianza, la comparativa contra v3 y el desglose de los castigos de mora y multiplicador de deuda.</p>
<div class="formula" id="formula"></div>
<div class="note" id="exclusion-box" hidden>
<strong id="exclusion-titulo"></strong>
<label style="display:flex;align-items:center;gap:8px;margin-top:8px;font-weight:650"><input type="checkbox" id="mostrar-excluidas" style="width:auto;margin-top:0;accent-color:var(--blue)"> Mostrar excluidas (<span id="exclusion-count"></span>)</label>
<p class="small" id="exclusion-declaracion" style="margin:8px 0 0"></p>
</div>
</header>
<div class="note warning" role="note"><strong>Aviso permanente:</strong> los parámetros k, alpha y beta son <strong>PROVISIONALES y NO están calibrados</strong> para v4 (<span id="params-estado"></span>); esta nota <strong>no es una calificación crediticia</strong>.<div id="advertencia" class="small" style="margin-top:8px"></div>Limitaciones declaradas por el generador:<ul id="limitations" class="small" style="margin:8px 0 0;padding-left:18px"></ul></div>

<nav class="tabs" role="tablist" aria-label="Vistas">
<button id="tab-1" aria-selected="true" data-view="view-1" type="button">1 · Evolución mensual</button>
<button id="tab-2" aria-selected="false" data-view="view-2" type="button">2 · Distribución</button>
<button id="tab-3" aria-selected="false" data-view="view-3" type="button">3 · Comparación A-B vs v3</button>
<button id="tab-4" aria-selected="false" data-view="view-4" type="button">4 · Desglose del castigo</button>
<button id="tab-5" aria-selected="false" data-view="view-5" type="button">5 · Riesgo de divisa</button>
</nav>

<section class="panel" id="view-1" aria-label="Evolución mensual">
<h2>1 · Evolución mensual por empresa (varias superpuestas)</h2>
<p class="small muted">Busca por company_id o grupo y añade hasta 8 empresas para comparar trayectorias. Un punto por mes y empresa; 50 = equilibrio (línea marcada). Punto sólido: confianza alta · semitransparente: media · hueco: baja · gris: ninguna. Un mes sin nota queda VACÍO y la línea no lo cruza: no se interpola ni vale 0; el hueco se declara con un marcador gris en el carril inferior, cuyo tooltip muestra el motivo (reasons).</p>
<div class="controls" style="grid-template-columns:1fr 1fr">
<div class="dropdown"><label for="company-search">Buscar empresa (company_id o grupo)</label><input id="company-search" type="search" placeholder="p. ej. COMP_0518…" autocomplete="off"><div id="company-dropdown" class="dropdown-list" hidden></div></div>
<div><span class="small muted" id="selection-count"></span><div id="chips" class="chips"></div></div>
</div>
<div id="legend" class="legend"></div>
<div class="chart-scroll"><svg id="chart" viewBox="0 0 1100 470" role="img" aria-label="Evolución mensual del healthscore entre cero y cien; los meses sin nota quedan vacíos, sin unir y sin valer cero"></svg></div>
<div class="slider-row"><label for="inspect">Ficha del mes: <span id="inspect-label"></span></label><input id="inspect" type="range" min="0" step="1" aria-label="Mes para la ficha de empresa"></div>
<div id="cards" class="cards" aria-live="polite"></div>
</section>

<section class="panel" id="view-2" hidden aria-label="Distribución del corte">
<h2>2 · Distribución de la cartera en un corte</h2>
<div class="controls" style="grid-template-columns:1fr"><label>Corte (compartido con las vistas 3 y 4)<select id="cut"></select></label></div>
<div class="histo-grid">
<div>
<div class="chart-scroll"><svg id="histo" viewBox="0 0 800 390" role="img" aria-label="Histograma del healthscore en bins de 5 puntos, apilado por nivel de confianza, con barra aparte para empresas sin nota"></svg></div>
<div id="histo-legend" class="legend"></div>
</div>
<div><p class="small muted" style="margin-top:0">Recuentos por confianza en el corte (cuadran con el universo de empresas del corte):</p><table id="cut-counts"></table><p class="small muted" id="null-note"></p></div>
</div>
<p class="small muted">Bins de 5 puntos sobre 0..100, apilados por confianza: importa cuánta de la distribución es medición (alta) y cuánta inferencia. La barra separada de la derecha, fuera del eje 0..100, cuenta las empresas del corte SIN nota, etiquetadas por su motivo. Nunca se omiten.</p>
</section>

<section class="panel" id="view-3" hidden aria-label="Comparación A-B contra v3">
<h2>3 · Comparación A-B contra v3</h2>
<div id="v3-status"></div>
<p class="small muted" id="v3-poblaciones"></p>
<div class="statgrid" id="v3-stats"></div>
<p class="small muted">La dispersión de la diferencia se calcula SOLO sobre la población común (pares company_id · mes con nota en v3 y en v4); los tamaños de cada población se declaran arriba y no se mezclan. dH = v4 − v3.</p>
<div class="controls rank-controls">
<label>Buscar por empresa o grupo<input id="ab-search" type="search" placeholder="company_id o group_id…"></label>
<label>Ordenar por<select id="ab-sort">
<option value="dif">Diferencia dH (v4−v3) ↓</option>
<option value="v3">Nota v3 ↓</option>
<option value="v4">Nota v4 ↓</option>
<option value="dif-asc">Diferencia dH ↑</option>
<option value="id">company_id A→Z</option>
</select></label>
</div>
<div class="table-scroll"><table id="ab-table"></table></div>
<div class="pager"><button id="ab-prev" type="button">← Anterior</button><span id="ab-page" class="small muted"></span><button id="ab-next" type="button">Siguiente →</button></div>
</section>

<section class="panel" id="view-4" hidden aria-label="Desglose del castigo">
<h2>4 · Desglose del castigo por empresa y corte</h2>
<p class="small muted">Cómo se llega de <strong>h_antes_de_ajustes</strong> a <strong>health_score</strong> restando los dos castigos del parquet: <strong>penalizacion_mora_puntos</strong> y <strong>penalizacion_multiplicador_puntos</strong>. Barra azul = nota final · naranja = puntos perdidos por mora · rojo = puntos perdidos por multiplicador de deuda. Orden inicial: castigo total descendente. Declaración: el tercer canal de la obligación vencida (el denominador) NO es separable en el parquet; está dentro de h_antes_de_ajustes y de t6_efectivo, por eso los insumos (obligacion_vencida_m, t6_efectivo, colchon_v4…) se muestran a su lado. La columna "Castigo Shapley (ref.)", tomada de summary.json, añade ese canal y solo cubre el top de empresas más penalizadas del resumen; las demás muestran —.</p>
<p class="small muted" id="castigo-poblaciones"></p>
<div class="controls rank-controls">
<label>Buscar por empresa o grupo<input id="cas-search" type="search" placeholder="company_id o group_id…"></label>
<label>Ordenar por<select id="cas-sort">
<option value="castigo">Castigo total (h0 − nota) ↓</option>
<option value="shapley">Castigo Shapley con denominador ↓ (ref. summary)</option>
<option value="mora">Castigo por mora ↓</option>
<option value="mult">Castigo por multiplicador ↓</option>
<option value="obligacion">obligacion_vencida_m ↓</option>
<option value="nota">health_score ↓</option>
<option value="h0">h_antes_de_ajustes ↓</option>
<option value="id">company_id A→Z</option>
</select></label>
</div>
<div class="table-scroll"><table id="cas-table"></table></div>
<div class="pager"><button id="cas-prev" type="button">← Anterior</button><span id="cas-page" class="small muted"></span><button id="cas-next" type="button">Siguiente →</button></div>
<div id="shapley-box"></div>
</section>

<section class="panel" id="view-5" hidden aria-label="Riesgo de divisa">
<h2>5 · Riesgo de divisa (capa FX)</h2>
<div id="fx-status"></div>
<div class="note"><strong>Convención de signo:</strong> <code>perdida_eur = eur_emision − eur_corte</code>. Positivo = dinero <strong>ya perdido</strong> por moverse la divisa entre la emisión y el corte; negativo = <strong>ganancia</strong>. Es dinero incurrido, no riesgo teórico. Cada cifra de esta vista declara su población; nada se estima.</div>
<div id="fx-nota-verificacion"></div>
<h2>El caso concreto: pérdida ya incurrida por empresa</h2>
<p class="small muted">La cabecera de este ranking es el mensaje de la capa: hay empresas que ya han perdido cientos de miles de euros por movimiento de divisa y <strong>ningún otro canal del motor mide esa pérdida</strong> (mora y multiplicador miden impago y deuda, no divisa). Las pérdidas y las ganancias se separan visualmente: no se compensan en la misma barra.</p>
<div class="cards" id="fx-headline"></div>
<div class="statgrid" id="fx-stats"></div>
<p class="small muted" id="fx-poblaciones"></p>
<div class="controls" style="grid-template-columns:1fr 1fr 1fr">
<label for="fx-cut">Corte (sincronizado con la vista 2)<select id="fx-cut"></select></label>
<label for="fx-search">Buscar empresa o grupo<input id="fx-search" type="search" placeholder="company_id o group_id…"></label>
<label for="fx-sort">Ordenar la tabla por<select id="fx-sort">
<option value="perdida">Pérdida ya incurrida ↓</option>
<option value="ganancia">Ganancia ya incurrida ↓</option>
<option value="abs">Movimiento absoluto ↓</option>
<option value="castigo">Puntos de castigo FX ↓</option>
<option value="id">company_id A→Z</option>
</select></label>
</div>
<p class="small muted" id="fx-poblacion-corte"></p>
<div class="chart-scroll"><svg id="fx-bars" viewBox="0 0 1100 560" role="img" aria-label="Barras divergentes: pérdida ya incurrida a la izquierda en rojo y ganancia a la derecha en azul, por empresa"></svg></div>
<div class="legend"><span><span class="swatch" style="background:#8f1d1d;border-radius:2px"></span>Pérdida ya incurrida</span><span><span class="swatch" style="background:#186c98;border-radius:2px"></span>Ganancia ya incurrida</span><span><span class="swatch" style="background:#fff;border:2px solid #b85517;border-radius:2px"></span>Empresa en rama de intervalo (castigo mínimo, proporción no cerrable)</span></div>
<div class="table-scroll"><table id="fx-table"></table></div>
<div class="pager"><button id="fx-prev" type="button">← Anterior</button><span id="fx-page" class="small muted"></span><button id="fx-next" type="button">Siguiente →</button></div>

<h2 style="margin-top:26px">Mapa de divisas de la cartera: volatilidad frente a deriva</h2>
<p class="small muted">Cada punto es una divisa con cartera viva en el corte. Eje X = volatilidad robusta anualizada (medida, F1b); eje Y = deriva del último año convertida en erosión (solo cuenta la depreciación). El tramo combinado es el más severo de los dos. Se ve la intuición central de la capa: <strong>TRY</strong> tiene volatilidad baja pero una deriva enorme, mientras <strong>BRL</strong> es al revés (más volatilidad, deriva positiva = no erosiona).</p>
<div class="chart-scroll"><svg id="fx-map" viewBox="0 0 1000 520" role="img" aria-label="Dispersión de volatilidad frente a deriva por divisa; aparte, las divisas con tipo FMI sin volatilidad medida y las divisas sin tipo en ninguna fuente (pérdida desconocida, no cero)"></svg></div>
<div id="fx-map-note" class="note"></div>
<div class="table-scroll"><table id="fx-map-table"></table></div>

<h2 style="margin-top:26px">¿De dónde viene el castigo: volatilidad o deriva?</h2>
<div class="chart-scroll"><svg id="fx-decomp" viewBox="0 0 900 150" role="img" aria-label="Reparto del castigo FX entre volatilidad y deriva"></svg></div>
<div id="fx-decomp-note" class="note"></div>

<h2 style="margin-top:26px">Lo que no sabemos: la rama de intervalo</h2>
<div id="fx-interval"></div>
</section>

<footer class="footer-note"><strong>Salud del flujo observado; no es score crediticio, ni probabilidad de impago.</strong> Esta gráfica solo lee healthscore_v4 (y, solo para comparar, las notas de healthscore_v3): está prohibido mezclar series de versiones en la misma nota.<p id="source" class="source"></p></footer>
<noscript><p class="note warning">Activa JavaScript para usar los selectores y las vistas.</p></noscript>
</main>
<script id="payload-data" type="application/json">__PAYLOAD_DATA__</script>
<script id="chart-code">
function init(){
  var data=JSON.parse(document.getElementById('payload-data').textContent);
  var NS='http:'+'//www.w3.org/2000/svg';
  var confNames=['ninguna','baja','media','alta'];
  var confLabel={0:'ninguna',1:'baja · inferida',2:'media',3:'alta · medida'};
  var confColor={0:'#9fb5c4',1:'#b85517',2:'#5d95b8',3:'#186c98'};
  var confStack=['alta','media','baja','ninguna']; // orden de apilado: alta abajo
  var palette=['#186c98','#b85517','#5d8a3c','#7a4ba0','#a03a5e','#2b7f8e','#8a6d1f','#55618f'];
  var MAX_PICK=8;
  var element=function(tag,text,cls){var n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  var number=function(v,d){d=d===undefined?1:d;return v===null||v===undefined?'—':Number(v).toLocaleString('es-ES',{minimumFractionDigits:d,maximumFractionDigits:d});};
  var money=function(v){return v===null||v===undefined?'—':Number(v).toLocaleString('es-ES',{style:'currency',currency:'EUR'});};
  var monthDates=data.months.map(function(m){return new Date(m+'T12:00:00Z');});
  var shortDate=function(i){return monthDates[i].toLocaleDateString('es-ES',{month:'short',year:'2-digit',timeZone:'UTC'});};
  var longDate=function(i){return monthDates[i].toLocaleDateString('es-ES',{month:'long',year:'numeric',timeZone:'UTC'});};
  var byId=new Map(data.companies.map(function(c){return [c.id,c];}));
  var reasonsText=function(idx){var set=data.reasons_sets[idx];return set&&set.length?set.join(' · '):'sin motivo registrado';};

  // --- Cabecera: todo desde el fichero / summary.json ---
  var formula=document.getElementById('formula');
  formula.appendChild(element('strong',data.model_version+' · '+data.total_companies+' empresas · '+data.months.length+' meses'));
  formula.appendChild(document.createElement('br'));
  formula.appendChild(element('code',data.formula));
  formula.appendChild(document.createElement('br'));
  var cal=data.params_calibrados||{};
  var estado=(cal.k||cal.alpha||cal.beta)?'calibrados: '+['k','alpha','beta'].filter(function(f){return cal[f];}).join(', '):'ninguno calibrado';
  document.getElementById('params-estado').textContent=estado;
  formula.appendChild(element('code','k = '+number(data.k,2)+' · alpha = '+number(data.alpha,2)+' · beta = '+number(data.beta,2)+' · PROVISIONALES pendientes de calibración para v4 · '+data.data_workspace));
  if(data.advertencia)document.getElementById('advertencia').textContent=data.advertencia+' ';
  data.limitations.forEach(function(text){var li=document.createElement('li');li.textContent=text;document.getElementById('limitations').appendChild(li);});
  document.getElementById('source').textContent=data.model_version+' · '+data.data_workspace+' · '+data.agregacion+(data.reduced_months?' · MESES RECORTADOS por tamaño: solo los más recientes.':'');

  // --- Conmutador de excluidas (cabecera de la pagina) ---
  var mostrarExcluidas=false;
  var visibleCompany=function(company){return !company.excluida||mostrarExcluidas;};
  var exclusionBox=document.getElementById('exclusion-box');
  if(data.n_excluidas>0){
    exclusionBox.hidden=false;
    document.getElementById('exclusion-count').textContent=data.n_excluidas;
    document.getElementById('exclusion-declaracion').textContent=data.declaracion_exclusion||('Empresas excluidas del algoritmo por nota 0 persistente (motivo: '+data.motivo_exclusion+'); sus filas se conservan con health_score = NULL y son consultables con este conmutador.');
    document.getElementById('exclusion-declaracion').style.display='none';
    if(data.declaracion_exclusion)document.getElementById('exclusion-declaracion').style.display='';
    var exclTitulo=element('strong','EMPRESAS EXCLUIDAS: '+data.n_excluidas+' de '+data.total_companies+' (motivo: '+data.motivo_exclusion+')');
    exclTitulo.style.color='var(--red)';
    exclusionBox.replaceChild(exclTitulo,document.getElementById('exclusion-titulo'));
    var exCheck=document.getElementById('mostrar-excluidas');
    exCheck.addEventListener('change',function(){mostrarExcluidas=exCheck.checked;renderAll();});
  }

  // --- Pestañas ---
  var tabs=document.querySelectorAll('.tabs button');
  tabs.forEach(function(tab){
    tab.addEventListener('click',function(){
      tabs.forEach(function(t){t.setAttribute('aria-selected',String(t===tab));});
      ['view-1','view-2','view-3','view-4','view-5'].forEach(function(id){document.getElementById(id).hidden=(id!==tab.dataset.view);});
    });
  });

  // --- Controles comunes ---
  var cut=document.getElementById('cut');
  for(var m=data.months.length-1;m>=0;m--) cut.appendChild(new Option(longDate(m),data.months[m]));
  cut.value=data.months[data.months.length-1];
  var inspect=document.getElementById('inspect');
  inspect.max=data.months.length-1;inspect.value=data.months.length-1;
  document.getElementById('inspect-label').textContent=longDate(Number(inspect.value));

  function svgInto(parent){
    return function(tag,attrs,text){
      var n=document.createElementNS(NS,tag);
      for(var key in attrs)n.setAttribute(key,attrs[key]);
      if(text!==undefined)n.textContent=text;
      parent.appendChild(n);return n;
    };
  }

  // ===================== VISTA 1 =====================
  var selected=[],search=document.getElementById('company-search'),dropdown=document.getElementById('company-dropdown');
  var chips=document.getElementById('chips'),chart=document.getElementById('chart');
  data.companies.forEach(function(company){
    if(selected.length<2&&!company.excluida)selected.push(company.id); // inicial: mas meses con nota, nunca por nota alta ni excluidas
  });
  function renderChips(){
    chips.replaceChildren();
    selected.forEach(function(id){
      var company=byId.get(id);
      var chip=element('span',undefined,'chip');
      var sw=element('span','','swatch');sw.style.background=palette[selected.indexOf(id)%palette.length];sw.style.borderRadius='3px';
      chip.appendChild(sw);
      chip.appendChild(document.createTextNode(id+(company.excluida?' · excluida':'')));
      var del=element('button','×');del.type='button';del.setAttribute('aria-label','Quitar '+id);
      del.addEventListener('click',function(){selected=selected.filter(function(x){return x!==id;});renderChips();renderSeries();});
      chip.appendChild(del);chips.appendChild(chip);
    });
    document.getElementById('selection-count').textContent=selected.length+' de '+MAX_PICK+' máx. empresas seleccionadas';
  }
  search.addEventListener('input',function(){
    var query=search.value.trim().toLowerCase();
    if(!query){dropdown.hidden=true;return;}
    var matches=data.companies.filter(function(c){
      return visibleCompany(c)&&(c.id+' '+(c.group_id||'')).toLowerCase().indexOf(query)>=0;
    }).slice(0,40);
    dropdown.replaceChildren();
    matches.forEach(function(c){
      var opt=element('div',c.id+' · '+(c.group_id||'Grupo desconocido')+' · '+c.scored_months+' notas'+(c.excluida?' · EXCLUIDA (activo el conmutador para buscarla)':''));
      opt.addEventListener('click',function(){
        if(selected.length>=MAX_PICK)return;
        if(selected.indexOf(c.id)<0)selected.push(c.id);
        renderChips();renderSeries();
        search.value='';dropdown.hidden=true;
      });
      dropdown.appendChild(opt);
    });
    dropdown.hidden=!matches.length;
  });
  search.addEventListener('blur',function(){setTimeout(function(){dropdown.hidden=true;},150);});
  var legend=document.getElementById('legend');
  confNames.forEach(function(conf,ci){
    var item=element('span'),sw=element('span','','swatch');
    if(ci===1){sw.style.background='#fff';sw.style.border='2px solid '+confColor[ci];}
    else{sw.style.background=confColor[ci];if(ci===2)sw.style.opacity=0.55;}
    item.appendChild(sw);item.appendChild(document.createTextNode('Confianza '+confLabel[ci]));
    legend.appendChild(item);
  });
  (function(){
    var item=element('span'),sw=element('span','','swatch');
    sw.style.background='#c7d3dc';sw.style.borderRadius='2px';
    item.appendChild(sw);item.appendChild(document.createTextNode('Mes sin nota (hueco declarado, con motivo en el tooltip y en la ficha)'));
    legend.appendChild(item);
  })();
  function renderSeries(){
    var selectedIdx=Number(inspect.value);
    document.getElementById('inspect-label').textContent=longDate(selectedIdx);
    chart.replaceChildren();
    var draw=svgInto(chart);
    var x=function(i){return 62+i*1006/Math.max(1,monthDates.length-1);};
    var y=function(s){return 330-s*3;};
    for(var v=0;v<=100;v+=25){
      draw('line',{x1:62,x2:1068,y1:y(v),y2:y(v),stroke:v===50?'#9fb5c4':'#dce5eb','stroke-width':v===50?2:1});
      draw('text',{x:48,y:y(v)+4,'text-anchor':'end',fill:'#607183','font-size':12},v);
    }
    draw('text',{x:1072,y:y(50)-6,'text-anchor':'end',fill:'#607183','font-size':11},'50 · equilibrio');
    monthDates.forEach(function(_,i){
      if(i%2===0||i===monthDates.length-1)
        draw('text',{x:x(i),y:435,'text-anchor':'end',transform:'rotate(-35 '+x(i)+' 435)',fill:'#607183','font-size':12},shortDate(i));
    });
    draw('text',{x:40,y:356,'text-anchor':'end',fill:'#607183','font-size':10},'sin nota');
    draw('line',{x1:x(selectedIdx),x2:x(selectedIdx),y1:20,y2:368,stroke:'#a9b9c5','stroke-dasharray':'4 5'});
    selected.forEach(function(id,slot){
      var company=byId.get(id);if(!company)return;
      var color=palette[slot%palette.length];
      company.segments.forEach(function(segment){
        if(segment.length>1)
          draw('polyline',{points:segment.map(function(pt){return x(pt[0])+','+y(pt[1]);}).join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round'});
      });
      company.points.forEach(function(point,i){
        if(!point)return;
        var label=company.id+' · '+longDate(i)+' · confianza '+confLabel[point.c];
        if(point.s===null||point.s===undefined){
          // Hueco declarado: marcador en el carril inferior, con el motivo.
          var gap=draw('rect',{x:x(i)-4,y:352,width:8,height:8,fill:'#c7d3dc',stroke:'#ffffff','stroke-width':1,tabindex:0,role:'button','aria-label':label+' · sin nota'});
          var gt=document.createElementNS(NS,'title');
          gt.textContent=label+' · SIN NOTA (hueco, no cero)\nmotivo: '+reasonsText(point.r);
          gap.appendChild(gt);
          var go=function(){inspect.value=i;renderSeries();};
          gap.addEventListener('click',go);
          return;
        }
        var ci=point.c;
        var mark;
        if(ci===1){
          mark=draw('circle',{cx:x(i),cy:y(point.s),r:5,fill:'#ffffff',stroke:color,'stroke-width':2,tabindex:0,role:'button','aria-label':label});
        }else{
          mark=draw('circle',{cx:x(i),cy:y(point.s),r:5,fill:ci===0?'#9fb5c4':color,'fill-opacity':ci===2?0.55:1,stroke:'#ffffff','stroke-width':1.5,tabindex:0,role:'button','aria-label':label});
        }
        var title=document.createElementNS(NS,'title');
        title.textContent=label+'\nnota '+number(point.s)+' · confianza '+confLabel[ci]+'\noblig_vencida '+money(point.ob)+' · t6_efectivo '+money(point.t6)+' · colchon_v4 '+money(point.cv)+' · mora '+number(point.mi,3)+' · multiplicador '+number(point.md,3);
        mark.appendChild(title);
        var goTo=function(){inspect.value=i;renderSeries();};
        mark.addEventListener('click',goTo);
        mark.addEventListener('keydown',function(event){if(event.key==='Enter'||event.key===' '){event.preventDefault();goTo();}});
      });
    });
    var cards=document.getElementById('cards');cards.replaceChildren();
    selected.forEach(function(id,slot){
      var company=byId.get(id);if(!company)return;
      var point=company.points[selectedIdx];
      var card=element('article',undefined,'card');card.style.borderTopColor=palette[slot%palette.length];
      card.appendChild(element('h2',company.id));
      card.appendChild(element('p',(company.group_id||'Grupo desconocido')+' · '+longDate(selectedIdx),'small muted'));
      if(company.excluida){
        card.appendChild(element('p','EXCLUIDA del algoritmo por nota 0 persistente. Su fila se conserva con health_score = NULL y el motivo en reasons. Reversible con --no-excluir-cero-persistente.','note warning'));
      }
      if(!point||(point.s===null||point.s===undefined)){
        card.appendChild(element('p','Sin nota este mes','score muted'));
        card.appendChild(element('p','El mes queda vacío en la serie: no se interpola, no vale 0. Motivo: '+(!point?'la empresa no tiene fila en este corte (no existe en el corte)':reasonsText(point.r)),'note'));
      }else{
        card.appendChild(element('p','Confianza '+confLabel[point.c],'small muted'));
        card.appendChild(element('div',number(point.s)+' / 100','score'));
        card.appendChild(element('p','h_antes_de_ajustes: '+number(point.h,2)+' → nota tras castigos: '+number(point.s,2),'small'));
        var pills=element('div',undefined,'pills');
        [['obligacion_vencida_m',money(point.ob)],['t6_efectivo',money(point.t6)],
         ['colchon_v4',money(point.cv)],['mora_indice',number(point.mi,3)],
         ['multiplicador_deuda',number(point.md,3)],['pen_mora_pts',number(point.pm,2)],
         ['pen_mult_pts',number(point.pd,2)],['r_hist',number(point.rh,3)],
         ['nota v3',point.v3===undefined?'—':number(point.v3)]]
          .forEach(function(pair){pills.appendChild(element('span',pair[0]+': '+pair[1],'pill'));});
        card.appendChild(pills);
        if(point.x)card.appendChild(element('p','Ventana parcial: menos meses de los objetivo.','note warning'));
      }
      if(point&&point.r!==undefined)
        card.appendChild(element('p','Motivos: '+reasonsText(point.r),'small muted'));
      cards.appendChild(card);
    });
  }
  inspect.addEventListener('input',renderSeries);

  // ===================== VISTA 2: histograma =====================
  var histo=document.getElementById('histo');
  function renderHisto(){
    var info=data.cuts[cut.value];
    if(!info)return;
    if(mostrarExcluidas&&info.todas){
      // Conmutador activo: la distribucion pasa a la poblacion completa.
      // Dos poblaciones distintas, nunca mezcladas: se declara en pantalla.
      info=Object.assign({},info.todas,{excluidas:info.excluidas});
      document.getElementById('null-note').setAttribute('data-con-excluidas','1');
    }else{
      document.getElementById('null-note').removeAttribute('data-con-excluidas');
    }
    histo.replaceChildren();
    var draw=svgInto(histo);
    var bins=info.bins,nullTotal=0;
    Object.keys(info.null_motivos).forEach(function(key){nullTotal+=info.null_motivos[key];});
    var binTotals=bins.map(function(bin){return confStack.reduce(function(sum,conf){return sum+bin[conf];},0);});
    var maxBar=Math.max(1,Math.max.apply(null,binTotals),nullTotal);
    var x0=46,binW=28,plotW=20*binW,nullX=x0+plotW+40,nullW=90;
    var y=function(v){return 330-v*290/maxBar;};
    var scale=function(v){return v*290/maxBar;};
    for(var v=0;v<=100;v+=25){
      var gx=x0+v/100*plotW;
      draw('line',{x1:gx,x2:gx,y1:30,y2:330,stroke:v===50?'#9fb5c4':'#dce5eb','stroke-width':v===50?2:1});
      draw('text',{x:gx,y:348,'text-anchor':'middle',fill:'#607183','font-size':12},v);
    }
    draw('text',{x:x0+25/100*plotW,y:342,'text-anchor':'middle',fill:'#607183','font-size':11},'50 · equilibrio');
    bins.forEach(function(bin,bi){
      var bottom=330,top;
      confStack.forEach(function(conf){
        var count=bin[conf];
        if(!count)return;
        var height=scale(count);
        top=bottom-height;
        draw('rect',{x:x0+bi*binW+2,y:top,width:binW-4,height:height,fill:confColor[conf],stroke:'#ffffff','stroke-width':0.5});
        bottom=top;
      });
      if(binTotals[bi]>0)
        draw('text',{x:x0+bi*binW+binW/2,y:top-4,'text-anchor':'middle',fill:'#607183','font-size':10},binTotals[bi]);
    });
    // Barra aparte, fuera del eje 0..100: empresas sin nota, por motivo.
    draw('line',{x1:nullX-8,x2:nullX-8,y1:30,y2:330,stroke:'#dce5eb'});
    var motivos=Object.keys(info.null_motivos).sort(function(a,c){return info.null_motivos[c]-info.null_motivos[a];});
    var slotW=nullW/Math.max(1,motivos.length);
    motivos.forEach(function(motivo,mi){
      var count=info.null_motivos[motivo],height=scale(count),top=330-height;
      draw('rect',{x:nullX+mi*slotW+2,y:top,width:Math.max(4,slotW-6),height:height,fill:'#9fb5c4',stroke:'#ffffff','stroke-width':0.5});
      draw('text',{x:nullX+mi*slotW+slotW/2,y:top-4,'text-anchor':'middle',fill:'#607183','font-size':10},count);
      var short=motivo.length>34?motivo.slice(0,33)+'…':motivo;
      var text=draw('text',{x:nullX+mi*slotW+slotW/2,y:368,'text-anchor':'end',fill:'#607183','font-size':10,transform:'rotate(-35 '+(nullX+mi*slotW+slotW/2)+' 368)'},short);
      var title=document.createElementNS(NS,'title');title.textContent=motivo;text.appendChild(title);
    });
    draw('text',{x:nullX+nullW/2,y:30,'text-anchor':'middle',fill:'#607183','font-size':11},'sin nota');
    var counts=document.getElementById('cut-counts');counts.replaceChildren();
    var head=document.createElement('thead'),hrow=document.createElement('tr');
    ['Confianza','Empresas','%'].forEach(function(text){var th=document.createElement('th');th.textContent=text;hrow.appendChild(th);});
    head.appendChild(hrow);
    counts.appendChild(head);
    var body=document.createElement('tbody'),total=info.total;
    confStack.slice().reverse().forEach(function(conf){
      var ci=confNames.indexOf(conf);
      var row=document.createElement('tr');
      [confLabel[ci],info.confidence[conf],Math.round(info.confidence[conf]/total*1000)/10+'%'].forEach(function(text,ci2){
        var td=document.createElement('td');td.textContent=String(text);if(ci2)td.className='n';row.appendChild(td);
      });
      body.appendChild(row);
    });
    var nullRow=document.createElement('tr');
    ['Sin nota (barra aparte)',nullTotal,nullTotal?'—':'—'].forEach(function(text,ci2){
      var td=document.createElement('td');td.textContent=String(text);if(ci2)td.className='n';nullRow.appendChild(td);
    });
    body.appendChild(nullRow);
    var totalRow=document.createElement('tr');
    ['Total del corte',total,'100%'].forEach(function(text,ci2){
      var td=document.createElement('td');td.textContent=String(text);
      if(ci2)td.className='n';
      td.style.fontWeight='700';
      totalRow.appendChild(td);
    });
    body.appendChild(totalRow);
    if(info.excluidas){
      var exRow=document.createElement('tr');
      ['Excluidas por nota 0 persistente (fuera de las cifras de arriba)',info.excluidas,'—'].forEach(function(text,ci2){
        var td=document.createElement('td');td.textContent=String(text);if(ci2)td.className='n';exRow.appendChild(td);
      });
      body.appendChild(exRow);
    }
    counts.appendChild(body);
    document.getElementById('null-note').textContent=nullTotal?('Empresas sin nota en este corte: '+nullTotal+' de '+total+'. Sus motivos (barra aparte): '+motivos.map(function(m){return info.null_motivos[m]+' · '+m;}).join('; ')+'.'):'Todas las empresas del corte tienen nota.';
    if(info.excluidas)document.getElementById('null-note').textContent+=' · Poblacion: '+(document.getElementById('null-note').getAttribute('data-con-excluidas')?'CON las ':'sin las ')+info.excluidas+' excluidas por nota 0 persistente (conmutador "mostrar excluidas").';
  }

  // ===================== utilidades vistas 3 y 4 =====================
  var percentile=function(sorted,q){
    if(!sorted.length)return null;
    var pos=(sorted.length-1)*q,base=Math.floor(pos),rest=pos-base;
    return sorted[base]+(sorted[base+1]!==undefined?rest*(sorted[base+1]-sorted[base]):0);
  };
  var descending=function(key){
    return function(a,b){
      var va=key(a),vb=key(b);
      if(va===null&&vb===null)return 0;
      if(va===null)return 1;
      if(vb===null)return -1;
      return vb-va;
    };
  };
  var byIdAsc=function(a,b){return a.id<b.id?-1:a.id>b.id?1:0;};
  var pageSize=100;

  // ===================== VISTA 3: comparacion A-B =====================
  var abSearch=document.getElementById('ab-search'),abSort=document.getElementById('ab-sort'),abPage=0;
  var abTable=document.getElementById('ab-table');
  var stateTag=function(state){
    var colors={ambas:'#186c98','solo v3':'#8a6d1f','solo v4':'#2b7f8e','sin nota en ninguna':'#9fb5c4','excluida':'#7a4ba0'};
    var tag=element('span',state,'tag');tag.style.background=colors[state]||'#9fb5c4';return tag;
  };
  function renderAbStats(){
    var status=document.getElementById('v3-status');
    status.replaceChildren();
    if(!data.v3_disponible){
      status.appendChild(element('div',undefined,'note warning'));
      status.lastChild.textContent=('Comparativa no disponible: no se encontró '+data.v3_ruta+'. La vista 3 queda vacía y NADA se compara ni se inventa; genera reports/score_v3/assessments.parquet con xray.scoring_io_v3 para activarla.');
      return;
    }
    var index=data.months.indexOf(cut.value);
    var nV3=0,nV4=0,nAmbas=0,nSoloV3=0,nSoloV4=0,diffs=[],signed=[];
    data.companies.forEach(function(company){
      if(!visibleCompany(company))return;
      var point=company.points[index];
      if(!point)return;
      var v4=point.s,v3=point.v3;
      if(v3!==undefined)nV3++;
      if(v4!==null&&v4!==undefined)nV4++;
      if(v4!==null&&v4!==undefined&&v3!==undefined){nAmbas++;var d=v4-v3;signed.push(d);diffs.push(Math.abs(d));}
      else if(v3!==undefined&&(v4===null||v4===undefined))nSoloV3++;
      else if(v4!==null&&v4!==undefined&&v3===undefined)nSoloV4++;
    });
    var signedAll=[],absAll=[];
    data.companies.forEach(function(company){
      company.points.forEach(function(point){
        if(!point||point.s===null||point.s===undefined||point.v3===undefined)return;
        signedAll.push(point.s-point.v3);absAll.push(Math.abs(point.s-point.v3));
      });
    });
    absAll.sort(function(a,b){return a-b;});signed.sort(function(a,b){return a-b;});signedAll.sort(function(a,b){return a-b;});
    var stats=document.getElementById('v3-stats');stats.replaceChildren();
    [['Empresas en el corte',data.cuts[cut.value]?data.cuts[cut.value].total:0],
     ['Con nota v3',nV3],['Con nota v4',nV4],['Ambas (población común)',nAmbas],
     ['Solo v3',nSoloV3],['Solo v4',nSoloV4],
     ['|dH| p50 común',percentile(diffs,0.5)],['|dH| p75 común',percentile(diffs,0.75)],['|dH| p90 común',percentile(diffs,0.90)],
     ['dH p50 común (v4−v3)',percentile(signed,0.5)],
     ['Pares comunes en TODO el panel',signedAll.length],['|dH| p50 panel',percentile(absAll,0.5)]]
      .forEach(function(pair){
        var stat=element('div',undefined,'stat');
        var b=element('b');b.textContent=number(pair[1],2);stat.appendChild(b);
        stat.appendChild(element('span',pair[0],'small muted'));
        stats.appendChild(stat);
      });
    if(data.n_excluidas){
      var statEx=element('div',undefined,'stat');statEx.style.borderTopColor='var(--red)';
      var bEx=element('b');bEx.textContent=data.n_excluidas;statEx.appendChild(bEx);
      statEx.appendChild(element('span','Excluidas por nota 0 persistente (fuera del ranking)','small muted'));
      stats.appendChild(statEx);
    }
    document.getElementById('v3-poblaciones').textContent='Tamaños declarados: el corte seleccionado y el panel completo son poblaciones DISTINTAS y se etiquetan por separado. Un par solo entra en la dispersión si tiene nota no nula en v3 y en v4 en el mismo mes.';
  }
  function abRows(){
    if(!data.v3_disponible)return [];
    var index=data.months.indexOf(cut.value);
    var query=abSearch.value.trim().toLowerCase();
    var list=[];
    data.companies.forEach(function(company){
      if(!visibleCompany(company))return;
      var point=company.points[index];
      if(!point)return;
      if(query&&((company.id+' '+(company.group_id||'')).toLowerCase().indexOf(query)<0))return;
      if(company.excluida){
        list.push({id:company.id,group:company.group_id,v3:point.v3===undefined?null:point.v3,v4:null,dif:null,conf:point.c,state:'excluida'});
        return;
      }
      var v4=point.s,v3=point.v3;
      var has4=v4!==null&&v4!==undefined;
      var state=!has4&&v3===undefined?'sin nota en ambas':has4&&v3===undefined?'solo v4':!has4?'solo v3':'ambas';
      list.push({id:company.id,group:company.group_id,v3:v3===undefined?null:v3,v4:has4?v4:null,dif:has4&&v3!==undefined?v4-v3:null,conf:point.c,state:state});
    });
    var sorters={
      dif:descending(function(r){return r.dif;})||byIdAsc,
      'dif-asc':function(a,b){var va=a.dif,vb=b.dif;if(va===null&&vb===null)return 0;if(va===null)return 1;if(vb===null)return -1;return va-vb||byIdAsc(a,b);},
      v3:descending(function(r){return r.v3;})||byIdAsc,
      v4:descending(function(r){return r.v4;})||byIdAsc,
      id:byIdAsc
    };
    list.sort(sorters[abSort.value]||sorters.dif);
    return list;
  }
  function renderAbTable(){
    var rows=abRows();
    var pages=Math.max(1,Math.ceil(rows.length/pageSize));
    if(abPage>=pages)abPage=pages-1;
    var start=abPage*pageSize,visible=rows.slice(start,start+pageSize);
    abTable.replaceChildren();
    var head=document.createElement('thead'),tr=document.createElement('tr');
    ['#','Empresa','Grupo','Nota v3','Nota v4','dH (v4−v3)','Caso','Confianza v4'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci===0||ci>=3)th.className='n';
      tr.appendChild(th);
    });
    head.appendChild(tr);
    abTable.appendChild(head);
    var body=document.createElement('tbody');
    visible.forEach(function(row,i){
      var tr=document.createElement('tr');
      [String(start+i+1),row.id,row.group||'—',number(row.v3),number(row.v4),
       row.dif===null?'—':(row.dif>=0?'+':'')+number(row.dif,2)].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;
        if(ci===0||ci>=3)td.className='n';
        tr.appendChild(td);
      });
      var tagTd=document.createElement('td');tagTd.appendChild(stateTag(row.state));tr.appendChild(tagTd);
      var confTd=document.createElement('td');confTd.className='n';confTd.textContent=confLabel[row.conf];tr.appendChild(confTd);
      body.appendChild(tr);
    });
    abTable.appendChild(body);
    document.getElementById('ab-page').textContent='Página '+(abPage+1)+' de '+pages+' · empresas en el filtro: '+rows.length;
    document.getElementById('ab-prev').disabled=abPage===0;
    document.getElementById('ab-next').disabled=abPage>=pages-1;
  }
  abSearch.addEventListener('input',function(){abPage=0;renderAbTable();});
  abSort.addEventListener('change',function(){abPage=0;renderAbTable();});

  // ===================== VISTA 4: desglose del castigo =====================
  var casSearch=document.getElementById('cas-search'),casSort=document.getElementById('cas-sort'),casPage=0;
  var casTable=document.getElementById('cas-table');
  var shapleyById=new Map(data.shapley_top.map(function(item){return [item.id,item];}));
  function castRows(){
    var index=data.months.indexOf(cut.value);
    var query=casSearch.value.trim().toLowerCase();
    var list=[];
    data.companies.forEach(function(company){
      var point=company.points[index];
      if(!point||point.s===null||point.s===undefined)return;
      if(query&&((company.id+' '+(company.group_id||'')).toLowerCase().indexOf(query)<0))return;
      list.push({id:company.id,group:company.group_id,point:point,
                 castigo:(point.h!==null&&point.h!==undefined)?point.h-point.s:null,
                 shapley:shapleyById.has(company.id)?shapleyById.get(company.id).castigo:null});
    });
    var sorters={
      castigo:descending(function(r){return r.castigo;})||byIdAsc,
      shapley:descending(function(r){return r.shapley;})||byIdAsc,
      mora:descending(function(r){return r.point.pm;})||byIdAsc,
      multiplicador:descending(function(r){return r.point.pd;})||byIdAsc,
      obligacion:descending(function(r){return r.point.ob;})||byIdAsc,
      nota:descending(function(r){return r.point.s;})||byIdAsc,
      h0:descending(function(r){return (r.point.h===undefined)?null:r.point.h;})||byIdAsc,
      id:byIdAsc
    };
    list.sort(sorters[casSort.value]||sorters.castigo);
    return list;
  }
  function renderCasTable(){
    var index=data.months.indexOf(cut.value);
    var info=data.cuts[cut.value];
    var nConNota=0;
    data.companies.forEach(function(company){
      var pt=company.points[index];
      if(pt&&pt.s!==null&&pt.s!==undefined)nConNota++;
    });
    var rows=castRows();
    var pages=Math.max(1,Math.ceil(rows.length/pageSize));
    if(casPage>=pages)casPage=pages-1;
    var start=casPage*pageSize,visible=rows.slice(start,start+pageSize);
    casTable.replaceChildren();
    var head=document.createElement('thead'),htr=document.createElement('tr');
    ['#','Empresa','Grupo','h_antes_de_ajustes','Nota final','Castigo total','Castigo mora','Castigo mult.','Castigo Shapley (ref.)','Desglose (barra)','obligacion_vencida_m','mora_indice','multiplicador_deuda','t6_efectivo','colchon_v4'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci===0||ci>=3)th.className='n';
      htr.appendChild(th);
    });
    head.appendChild(htr);
    casTable.appendChild(head);
    var body=document.createElement('tbody');
    visible.forEach(function(row,i){
      var point=row.point,tr=document.createElement('tr');
      if(row.shapley!==null)tr.title='En el top Shapley de summary.json (canal denominador incluido): castigo total '+number(row.shapley,2)+' puntos.';
      [String(start+i+1),row.id,row.group||'—',number(point.h,2),number(point.s,2),
       number(row.castigo,2),number(point.pm,2),number(point.pd,2),
       row.shapley===null?'—':number(row.shapley,2)].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;
        if(ci===0||ci>=3)td.className='n';
        tr.appendChild(td);
      });
      var barTd=document.createElement('td');
      var bar=element('div',undefined,'bar');
      var h0=(point.h!==null&&point.h!==undefined)?Math.max(0,Math.min(100,point.h)):100;
      var wNota=(point.s>=0&&point.h)?Math.max(0,point.s)/h0*100:0;
      var wMora=(point.pm>0)?Math.max(0,point.pm)/h0*100:0;
      var wMult=(point.pd>0)?Math.max(0,point.pd)/h0*100:0;
      var segNota=element('div');segNota.style.width=Math.max(0,wNota)+'%';segNota.style.background='#186c98';
      var segMora=element('div');segMora.style.width=wMora+'%';segMora.style.background='#b85517';
      var segMult=element('div');segMult.style.width=wMult+'%';segMult.style.background='#8f1d1d';
      bar.appendChild(segNota);bar.appendChild(segMora);bar.appendChild(segMult);
      bar.className='bar bar-decomp';
      barTd.appendChild(bar);
      tr.appendChild(barTd);
      [[money(point.ob)],[number(point.mi,3)],[number(point.md,3)],[money(point.t6)],[money(point.cv)]].forEach(function(pair,ci){
        var td=document.createElement('td');td.textContent=pair[0];td.className='n';tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    casTable.appendChild(body);
    document.getElementById('cas-page').textContent='Página '+(casPage+1)+' de '+pages+' · empresas con nota en el filtro: '+rows.length+(info?' · con nota en el corte: '+nConNota+' de '+info.total+' · sin nota: '+(info.total-nConNota)+' (ver vista 2)':'');
    document.getElementById('cas-prev').disabled=casPage===0;
    document.getElementById('cas-next').disabled=casPage>=pages-1;
  }
  casSearch.addEventListener('input',function(){casPage=0;renderCasTable();});
  casSort.addEventListener('change',function(){casPage=0;renderCasTable();});

  // Referencia Shapley (desde summary.json, NO del parquet): el canal
  // 'denominador' no es separable en el parquet; se etiqueta la fuente.
  var shapleyBox=document.getElementById('shapley-box');
  function renderShapley(){
    shapleyBox.replaceChildren();
    if(!data.shapley_top.length)return;
    var note=element('p',undefined,'note warning');
    note.textContent='Referencia aparte, fuente summary.json (descomposición Shapley sobre 3 canales binarios, corte '+data.shapley_corte+'): castigo total incluyendo el canal "obligación en denominador", que NO es separable en el parquet. El castigo de la tabla de arriba solo suma mora y multiplicador.';
    shapleyBox.appendChild(note);
    var head=element('h2','Empresas más penalizadas según Shapley (referencia)');
    shapleyBox.appendChild(head);
    var table=element('table');
    var thead=document.createElement('thead'),tr=document.createElement('tr');
    ['Empresa','Nota sin castigos','Nota final','Castigo total','Canal denominador','Canal mora','Canal multiplicador'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci)th.className='n';tr.appendChild(th);
    });
    thead.appendChild(tr);table.appendChild(thead);
    var tbody=document.createElement('tbody');
    data.shapley_top.forEach(function(item){
      var tr=document.createElement('tr');
      [item.id,number(item.sin_castigo,2),number(item.final,2),number(item.castigo,2),
       number(item.denominador,2),number(item.mora,2),number(item.multiplicador,2)].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=String(text);if(ci)td.className='n';tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var scroll=element('div',undefined,'table-scroll');scroll.appendChild(table);
    shapleyBox.appendChild(scroll);
  }

  // ===================== VISTA 5: riesgo de divisa =====================
  // Capa FX de F3. Convencion de signo del parquet: perdida_eur positivo =
  // perdida ya incurrida, negativo = ganancia. Nada se estima: lo que no
  // esta medido (divisas sin BCE) se declara como hueco, no como cero.
  var fxSearch=document.getElementById('fx-search'),fxSort=document.getElementById('fx-sort'),fxPage=0;
  var fxCut=document.getElementById('fx-cut');
  for(var fxM=data.months.length-1;fxM>=0;fxM--) fxCut.appendChild(new Option(longDate(fxM),data.months[fxM]));
  fxCut.value=data.months[data.months.length-1];
  var fxPageSize=100;
  var tramoColor={estable:'#5d8a3c',volatil:'#186c98',hipervolatil:'#8f1d1d'};

  function fxRows(){
    var index=data.months.indexOf(fxCut.value);
    var query=fxSearch.value.trim().toLowerCase();
    var list=[];
    data.companies.forEach(function(company){
      var point=company.points[index];
      if(!point||point.pf===undefined)return;
      if(query&&((company.id+' '+(company.group_id||'')).toLowerCase().indexOf(query)<0))return;
      list.push({id:company.id,group:company.group_id,perdida:point.pf,vivo:point.vi,
                 indice:point.ix,intervalo:point.fi===1,opacas:point.fo===1,
                 nota:(point.s===undefined?null:point.s),
                 fxp:(point.fxp===undefined?null:point.fxp),
                 mora:(point.pm===undefined?null:point.pm),
                 mult:(point.pd===undefined?null:point.pd)});
    });
    return list;
  }
  function fxSorted(rows){
    var mode=fxSort.value,copy=rows.slice();
    copy.sort(function(a,b){
      if(mode==='ganancia')return a.perdida-b.perdida;
      if(mode==='abs')return Math.abs(b.perdida)-Math.abs(a.perdida);
      if(mode==='castigo')return ((b.fxp===null)?-1:b.fxp)-((a.fxp===null)?-1:a.fxp);
      if(mode==='id')return a.id<b.id?-1:a.id>b.id?1:0;
      return b.perdida-a.perdida;
    });
    return copy;
  }
  function fxCard(titulo,valor,color,lineas){
    var c=element('article',undefined,'card');c.style.borderTopColor=color;
    c.appendChild(element('h2',titulo));
    var big=element('div',valor,'score');big.style.color=color;c.appendChild(big);
    lineas.forEach(function(text){c.appendChild(element('p',text,'small'));});
    return c;
  }
  function renderFxHeadline(rows){
    var box=document.getElementById('fx-headline');box.replaceChildren();
    var res=data.fx_resumen||{};
    var losses=rows.filter(function(r){return r.perdida>0;}).sort(function(a,b){return b.perdida-a.perdida;});
    var gains=rows.filter(function(r){return r.perdida<0;}).sort(function(a,b){return a.perdida-b.perdida;});
    if(losses.length){
      var top=losses[0];
      box.appendChild(fxCard('Mayor pérdida ya incurrida',money(top.perdida),'#8f1d1d',
        [top.id+' · '+(top.group||'grupo desconocido'),
         'Importe vivo '+money(top.vivo)+' · índice FX '+(top.indice===null||top.indice===undefined?'—':number(top.indice,3)),
         'Castigo FX '+(top.fxp===null?'—':number(top.fxp,2))+' pts · mora '+(top.mora===null?'—':number(top.mora,2))+' · multiplicador '+(top.mult===null?'—':number(top.mult,2)),
         'Ningún otro canal del motor mide esta pérdida: mora y multiplicador miden impago y deuda, no movimiento de divisa.']));
    }
    if(gains.length){
      var topg=gains[0];
      box.appendChild(fxCard('Mayor ganancia (ojo al signo)',money(-topg.perdida),'#186c98',
        [topg.id+' · '+(topg.group||'grupo desconocido'),'Importe vivo '+money(topg.vivo),
         'El mayor movimiento en valor absoluto de un corte puede ser una GANANCIA: perdida_eur negativo significa que la divisa se movió a favor de la empresa.']));
    }
    box.appendChild(fxCard('A cuántas empresas toca el factor',String(res.cambian_empresas)+' de '+String(res.evaluable_empresas),'#186c98',
      [String(res.materiales_empresas)+' cambian de forma material (≥ '+number(res.umbral_material,0)+' punto de nota)',
       String(res.intactas_empresas)+' NO cambian de nota: no queremos que parezca que el factor mueve más de lo que mueve.',
       'Población: '+res.poblacion_evaluable+'.']));
  }
  function renderFxStats(slot){
    var grid=document.getElementById('fx-stats');grid.replaceChildren();
    var res=data.fx_resumen||{};
    [['Empresas evaluables v4',res.evaluable_empresas],
     ['Empresa-mes evaluables',res.evaluable_empresa_mes],
     ['Empresas que cambian de nota',res.cambian_empresas],
     ['Empresa-mes que cambian',res.cambian_empresa_mes],
     ['Cambian de forma material (≥1 pt)',res.materiales_empresas],
     ['Intactas (no cambian)',res.intactas_empresas],
     ['Con exposición no-EUR (panel)',res.exposicion_empresas],
     ['Sin exposición no-EUR (panel)',res.sin_exposicion_empresas],
     ['En rama de intervalo (panel)',res.intervalo_empresas+' emp · '+res.intervalo_empresa_mes+' e-m']
    ].forEach(function(pair){
      var stat=element('div',undefined,'stat');var b=element('b');b.textContent=String(pair[1]);
      stat.appendChild(b);stat.appendChild(element('span',pair[0],'small muted'));grid.appendChild(stat);
    });
    document.getElementById('fx-poblaciones').textContent='Poblaciones declaradas: el panel (todos los cortes) y el corte seleccionado son distintos y se etiquetan por separado. La materialidad usa el umbral declarado de '+number(res.umbral_material,0)+' punto(s) sobre 100.';
    var c=slot||{};
    document.getElementById('fx-poblacion-corte').textContent='Corte '+longDate(data.months.indexOf(fxCut.value))+' · empresas con fila en la capa F3: '+c.n+' · con dato de pérdida: '+c.con_dato+' (perdiendo '+c.perdiendo+', ganando '+c.ganando+', sin efecto '+c.sin_efecto+') · sin dato (sin divisa no-EUR o no calculable): '+c.sin_dato+' · índice FX positivo: '+c.indice_positivo+' · rama de intervalo: '+c.intervalo+' · con divisas opacas: '+c.opacas+' (solo opacas: '+c.solo_opacas+'). Pérdida neta del corte: '+money(c.perdida_neta)+' · pérdida bruta '+money(c.perdida_bruta)+' · ganancia bruta '+money(c.ganancia_bruta)+'.';
  }
  function renderFxBars(rows){
    var svg=document.getElementById('fx-bars');svg.replaceChildren();
    var draw=svgInto(svg);
    var losses=rows.filter(function(r){return r.perdida>0;}).sort(function(a,b){return b.perdida-a.perdida;}).slice(0,16);
    var gains=rows.filter(function(r){return r.perdida<0;}).sort(function(a,b){return a.perdida-b.perdida;}).slice(0,16);
    if(!losses.length&&!gains.length){draw('text',{x:20,y:40,fill:'#607183','font-size':13},'Ninguna empresa de este corte tiene pérdida ni ganancia calculable por divisa.');return;}
    var maxP=1;
    losses.forEach(function(r){maxP=Math.max(maxP,r.perdida);});
    gains.forEach(function(r){maxP=Math.max(maxP,-r.perdida);});
    var cx=560,half=440,top=44,rowH=15;
    var y=function(i){return top+i*rowH;};
    var nRows=Math.max(losses.length,gains.length);
    draw('line',{x1:cx,x2:cx,y1:24,y2:y(nRows)+6,stroke:'#9fb5c4','stroke-width':2});
    draw('text',{x:cx-8,y:20,'text-anchor':'end',fill:'#8f1d1d','font-size':12,'font-weight':700},'← Pérdida ya incurrida (EUR)');
    draw('text',{x:cx+8,y:20,'text-anchor':'start',fill:'#186c98','font-size':12,'font-weight':700},'Ganancia ya incurrida (EUR) →');
    losses.forEach(function(r,i){
      var w=Math.max(1,r.perdida/maxP*half),yy=y(i);
      var rect=draw('rect',{x:cx-w,y:yy-5,width:w,height:10,fill:'#8f1d1d',rx:2});
      if(r.intervalo){rect.setAttribute('stroke','#b85517');rect.setAttribute('stroke-width','2');}
      var t=document.createElementNS(NS,'title');
      t.textContent=r.id+' · pérdida '+money(r.perdida)+' · índice FX '+number(r.indice,3)+' · castigo FX '+(r.fxp===null?'—':number(r.fxp,2)+' pts')+(r.intervalo?' · RAMA DE INTERVALO (castigo mínimo)':'');
      rect.appendChild(t);
      draw('text',{x:cx-w-6,y:yy+4,'text-anchor':'end',fill:'#142c42','font-size':11},r.id+' · '+money(r.perdida));
    });
    gains.forEach(function(r,i){
      var w=Math.max(1,-r.perdida/maxP*half),yy=y(i);
      var rect=draw('rect',{x:cx,y:yy-5,width:w,height:10,fill:'#186c98',rx:2});
      var t=document.createElementNS(NS,'title');t.textContent=r.id+' · ganancia '+money(-r.perdida);rect.appendChild(t);
      draw('text',{x:cx+w+6,y:yy+4,'text-anchor':'start',fill:'#142c42','font-size':11},r.id+' · '+money(-r.perdida));
    });
  }
  function renderFxTable(rows){
    var sorted=fxSorted(rows);
    var pages=Math.max(1,Math.ceil(sorted.length/fxPageSize));
    if(fxPage>=pages)fxPage=pages-1;
    var start=fxPage*fxPageSize,visible=sorted.slice(start,start+fxPageSize);
    var table=document.getElementById('fx-table');table.replaceChildren();
    var head=document.createElement('thead'),htr=document.createElement('tr');
    ['#','Empresa','Grupo','Pérdida/ganancia EUR','Importe vivo EUR','Índice FX','Castigo FX pts','Castigo mora','Castigo mult.','Nota v4','Rama'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci===0||ci>=3)th.className='n';htr.appendChild(th);
    });
    head.appendChild(htr);table.appendChild(head);
    var body=document.createElement('tbody');
    visible.forEach(function(row,i){
      var tr=document.createElement('tr');
      var celdas=[String(start+i+1),row.id,row.group||'—',(row.perdida>0?'+':'')+money(row.perdida),
                  money(row.vivo),number(row.indice,3),number(row.fxp,2),number(row.mora,2),
                  number(row.mult,2),number(row.nota,2),
                  row.intervalo?'intervalo (castigo mínimo)':(row.opacas?'con opacas':'—')];
      celdas.forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;if(ci===0||ci>=3)td.className='n';
        if(ci===3)td.style.color=row.perdida>0?'#8f1d1d':(row.perdida<0?'#186c98':'inherit');
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    document.getElementById('fx-page').textContent='Página '+(fxPage+1)+' de '+pages+' · empresas con dato de divisa en el filtro: '+sorted.length;
    document.getElementById('fx-prev').disabled=fxPage===0;
    document.getElementById('fx-next').disabled=fxPage>=pages-1;
  }
  function renderFxMap(){
    var svg=document.getElementById('fx-map');svg.replaceChildren();
    var draw=svgInto(svg);
    var month=fxCut.value,byC={};
    data.fx_divisas.forEach(function(d){if(d.m===month)byC[d.c]=d;});
    var cover=[],sinMedicion=[],sinTipo=[];
    (data.fx_cartera[month]||[]).forEach(function(item){
      var d=byC[item.divisa];if(!d)return;
      if(d.bce===true&&d.vol!==null&&d.vol!==undefined)cover.push({item:item,d:d});
      else if(item.n_valorables_corte>0||item.perdida_conocida)sinMedicion.push({item:item,d:d});
      else sinTipo.push({item:item,d:d});
    });
    var x0=80,x1=660,y0=430,y1=44,maxVol=0.2,maxDer=0.3;
    var X=function(v){return x0+Math.max(0,Math.min(1,v/maxVol))*(x1-x0);};
    var Y=function(v){return y0-Math.max(0,Math.min(1,v/maxDer))*(y0-y1);};
    for(var g=0;g<=maxVol+0.0001;g+=0.05){
      draw('line',{x1:X(g),x2:X(g),y1:y1,y2:y0,stroke:'#e4ecf1'});
      draw('text',{x:X(g),y:y0+18,'text-anchor':'middle',fill:'#607183','font-size':11},number(g*100,0)+'%');
    }
    for(var h=0;h<=maxDer+0.0001;h+=0.05){
      draw('line',{x1:x0,x2:x1,y1:Y(h),y2:Y(h),stroke:'#e4ecf1'});
      draw('text',{x:x0-8,y:Y(h)+4,'text-anchor':'end',fill:'#607183','font-size':11},number(h*100,0)+'%');
    }
    draw('text',{x:(x0+x1)/2,y:y0+40,'text-anchor':'middle',fill:'#142c42','font-size':12,'font-weight':700},'Volatilidad robusta anualizada (medida)');
    var mid=(y0+y1)/2;
    draw('text',{x:20,y:mid,'text-anchor':'middle',fill:'#142c42','font-size':12,'font-weight':700,transform:'rotate(-90 20 '+mid+')'},'Deriva 12m (erosión)');
    cover.forEach(function(entry){
      var d=entry.d,vol=d.vol,der=(d.dc===null||d.dc===undefined)?0:d.dc;
      var color=tramoColor[d.t]||'#607183',cx=X(vol),cy=Y(der);
      var c=draw('circle',{cx:cx,cy:cy,r:6,fill:color,'fill-opacity':0.85,stroke:'#ffffff','stroke-width':1.5,tabindex:0,role:'button','aria-label':d.c});
      var t=document.createElementNS(NS,'title');
      t.textContent=d.c+' · volatilidad '+number(vol*100,2)+'% · deriva (erosión) '+number(der*100,2)+'% · tramo '+d.t+' · cartera: pérdida neta '+money(entry.item.perdida_eur)+' · '+entry.item.n_facturas+' facturas en '+entry.item.n_empresas+' empresa(s)';c.appendChild(t);
      draw('text',{x:cx+8,y:cy+4,fill:'#142c42','font-size':11,'font-weight':700},d.c);
    });
    var bx=800,cy=y1+50;
    draw('text',{x:bx,y:y1-10,fill:'#b85517','font-size':12,'font-weight':700},'Con tipo FMI (sin volatilidad medida)');
    draw('text',{x:bx,y:y1+6,fill:'#607183','font-size':11},'hipervolátil por decisión, no por');
    draw('text',{x:bx,y:y1+20,fill:'#607183','font-size':11},'medición: la pérdida SÍ está medida.');
    sinMedicion.sort(function(a,b){return a.d.c<b.d.c?-1:1;});
    sinMedicion.forEach(function(entry){
      var c=draw('rect',{x:bx-6,y:cy-6,width:12,height:12,fill:'#b85517',transform:'rotate(45 '+bx+' '+cy+')',tabindex:0,role:'button','aria-label':entry.d.c});
      var t=document.createElementNS(NS,'title');t.textContent=entry.d.c+' · tipo de la segunda fuente (FMI/IFS XDC_EUR): la pérdida ya incurrida SÍ está valorada ('+money(entry.item.perdida_eur)+'), pero no hay volatilidad ni deriva medidas al no tener serie BCE. Cartera: '+entry.item.n_facturas+' facturas en '+entry.item.n_empresas+' empresa(s).';c.appendChild(t);
      draw('text',{x:bx+14,y:cy+4,fill:'#142c42','font-size':12,'font-weight':700},entry.d.c);
      cy+=26;
    });
    cy+=16;
    draw('text',{x:bx,y:cy,fill:'#8f1d1d','font-size':12,'font-weight':700},'Sin tipo (ni BCE ni FMI)');
    draw('text',{x:bx,y:cy+16,fill:'#607183','font-size':11},'pérdida DESCONOCIDA, no cero:');
    draw('text',{x:bx,y:cy+30,fill:'#607183','font-size':11},'no es que no hayan perdido, es que no se sabe.');
    cy+=54;
    sinTipo.sort(function(a,b){return a.d.c<b.d.c?-1:1;});
    sinTipo.forEach(function(entry){
      var c=draw('rect',{x:bx-6,y:cy-6,width:12,height:12,fill:'#8f1d1d',transform:'rotate(45 '+bx+' '+cy+')',tabindex:0,role:'button','aria-label':entry.d.c});
      var t=document.createElementNS(NS,'title');t.textContent=entry.d.c+' · sin tipo BCE ni FMI: la pérdida NO es calculable y se muestra como "sin dato", nunca como 0,00 EUR. Cartera: '+entry.item.n_facturas+' facturas en '+entry.item.n_empresas+' empresa(s).';c.appendChild(t);
      draw('text',{x:bx+14,y:cy+4,fill:'#142c42','font-size':12,'font-weight':700},entry.d.c);
      cy+=26;
    });
  }
  function renderFxMapTable(){
    var month=fxCut.value,byC={};
    data.fx_divisas.forEach(function(d){if(d.m===month)byC[d.c]=d;});
    var cartera=(data.fx_cartera[month]||[]).slice();
    cartera.sort(function(a,b){return Math.abs(b.perdida_eur||0)-Math.abs(a.perdida_eur||0);});
    var table=document.getElementById('fx-map-table');table.replaceChildren();
    var head=document.createElement('thead'),htr=document.createElement('tr');
    ['Divisa','Cobertura de tipo','Volatilidad anual','Deriva 12m (erosión)','Tramo vol.','Tramo deriva','Tramo combinado','Pérdida EUR cartera','Facturas','Empresas'].forEach(function(text,ci){
      var th=document.createElement('th');th.textContent=text;if(ci>=2)th.className='n';htr.appendChild(th);
    });
    head.appendChild(htr);table.appendChild(head);
    var body=document.createElement('tbody');
    cartera.forEach(function(item){
      var d=byC[item.divisa]||{},tr=document.createElement('tr');
      var sinTipo=(d.bce===false&&!(item.n_valorables_corte>0)&&!item.perdida_conocida);
      var cobertura=d.bce===true?'sí (BCE)':((item.n_valorables_corte>0)?'sin BCE (tipo FMI)':(d.bce===false?'sin tipo (ni BCE ni FMI)':'—'));
      var perdidaTxt=item.perdida_conocida?money(item.perdida_eur):'sin dato';
      var td0=document.createElement('td');td0.textContent=item.divisa;if(sinTipo)td0.style.color='#8f1d1d';tr.appendChild(td0);
      [cobertura,
       (d.vol===null||d.vol===undefined)?'—':number(d.vol*100,2)+'%',
       (d.dc===null||d.dc===undefined)?'—':number(d.dc*100,2)+'%',
       d.tv||'—',d.td||'—',d.t||'—',perdidaTxt,String(item.n_facturas),String(item.n_empresas)
      ].forEach(function(text,ci){
        var td=document.createElement('td');td.textContent=text;if(ci>=1)td.className='n';
        if(ci===6&&!item.perdida_conocida){td.style.color='#8f1d1d';td.style.fontStyle='italic';}
        else if(ci===6){td.style.color=item.perdida_eur>0?'#8f1d1d':(item.perdida_eur<0?'#186c98':'inherit');}
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    var note=document.getElementById('fx-map-note');
    if(note)note.textContent='Regla de la casa: nulo nunca es cero. "sin dato" significa que la pérdida NO es calculable porque la divisa no tiene tipo en ninguna fuente (ni BCE ni FMI/IFS); es distinto de un 0,00 EUR medido (divisa con tipo cuyo movimiento neto fue nulo). ARS, COP, CLP y PEN ya tienen tipo (FMI) desde F6; AED, MAD, MZN y NAD siguen sin dato.';
  }
  function renderFxDecomp(){
    var svg=document.getElementById('fx-decomp');svg.replaceChildren();
    var draw=svgInto(svg),res=data.fx_resumen||{};
    var v=res.vol_reparto,d=res.deriva_reparto;
    var note=document.getElementById('fx-decomp-note');
    if(v===null||v===undefined||d===null||d===undefined){
      draw('text',{x:20,y:40,fill:'#607183','font-size':13},'Desglose no disponible: falta beta_fx o los índices por componente.');
      note.textContent='';return;
    }
    var x0=40,x1=760,y=54,hh=36,wv=(x1-x0)*v,wd=(x1-x0)*d;
    draw('rect',{x:x0,y:y,width:wv,height:hh,fill:'#186c98'});
    draw('rect',{x:x0+wv,y:y,width:wd,height:hh,fill:'#b85517'});
    draw('text',{x:x0+wv/2,y:y+hh/2+5,'text-anchor':'middle',fill:'#ffffff','font-size':14,'font-weight':700},'Volatilidad '+number(v*100,1)+'%');
    draw('text',{x:x0+wv+wd/2,y:y+hh/2+5,'text-anchor':'middle',fill:'#ffffff','font-size':14,'font-weight':700},'Deriva '+number(d*100,1)+'%');
    draw('text',{x:x0,y:y-12,fill:'#142c42','font-size':12},'Efecto medido: '+number(res.vol_puntos,1)+' pts por volatilidad · '+number(res.deriva_puntos,1)+' pts por deriva');
    note.textContent='Población: '+res.desglose_poblacion+' (beta_fx = '+number(res.beta_fx,2)+'). El índice combinado toma el más severo de los dos componentes, así que los dos efectos se publican por separado (cada uno como si fuera el único) y su reparto porcentual; no se suman como si fueran independientes.';
  }
  function renderFxInterval(rows){
    var box=document.getElementById('fx-interval');box.replaceChildren();
    var res=data.fx_resumen||{},corte=data.fx_cortes[fxCut.value]||{};
    var inCut=rows.filter(function(r){return r.intervalo;});
    var p=element('p',undefined,'note warning');
    p.textContent='Estas empresas tienen facturas en divisas sin tipo en NINGUNA fuente (AED, MAD, MZN, NAD) mezcladas con divisas valorables: no se puede cerrar qué proporción de su cartera es inestable. En vez de inventar un número, el motor les aplica el castigo MÍNIMO compatible (indice_fx_min) y las marca con el motivo castigo_fx_acotado_por_intervalo; se ven con borde naranja en el gráfico y etiquetadas en la tabla. ARS, COP, CLP y PEN YA NO están aquí: las cubre la segunda fuente (FMI/IFS XDC_EUR). Población: en el panel, '+res.intervalo_empresas+' empresas ('+res.intervalo_empresa_mes+' empresa-mes) caen en esta rama; en el corte seleccionado, '+corte.intervalo+' filas de la capa F3 ('+inCut.length+' con nota en esta vista). Un hueco declarado vale más que un número inventado.';
    box.appendChild(p);
    if(inCut.length){
      box.appendChild(element('p','Empresas en la rama de intervalo en este corte: '+inCut.map(function(r){return r.id+' ('+money(r.vivo)+' vivos)';}).join(' · '),'small'));
    }else{
      box.appendChild(element('p','Ninguna empresa con nota de este corte cae en la rama de intervalo.','small muted'));
    }
  }
  function renderFx(){
    var status=document.getElementById('fx-status');
    if(!data.fx_disponible){
      status.replaceChildren();
      var w=element('div',undefined,'note warning');
      w.textContent='Vista de divisa no disponible: no se encontró '+(data.fx_ruta||'reports/fx_risk/fx_risk_monthly.parquet')+'. Los datos FX se quedan fuera y NADA se estima; genera reports/fx_risk/fx_risk_monthly.parquet con xray.fx_risk para activarla.';
      status.appendChild(w);
      ['fx-headline','fx-stats','fx-bars','fx-table','fx-map','fx-map-table','fx-decomp','fx-interval','fx-page','fx-poblacion-corte','fx-poblaciones','fx-nota-verificacion'].forEach(function(id){var n=document.getElementById(id);if(n)n.replaceChildren();});
      return;
    }
    status.replaceChildren();
    var rows=fxRows();
    renderFxHeadline(rows);
    renderFxStats(data.fx_cortes[fxCut.value]);
    renderFxBars(rows);
    renderFxTable(rows);
    renderFxMap();
    renderFxMapTable();
    renderFxDecomp();
    renderFxInterval(rows);
    var nota=document.getElementById('fx-nota-verificacion');nota.replaceChildren();
    if(data.fx_nota)nota.appendChild(element('p',data.fx_nota,'note'));
    if(!data.fx_index_disponible)nota.appendChild(element('p','Índice por divisa-mes no disponible ('+(data.fx_index_ruta||'fx_index.parquet')+'): el mapa de divisas no se dibuja y ninguna volatilidad se inventa.','note warning'));
  }
  fxSearch.addEventListener('input',function(){fxPage=0;renderFx();});
  fxSort.addEventListener('change',function(){fxPage=0;renderFxTable(fxRows());});
  fxCut.addEventListener('change',function(){
    cut.value=fxCut.value;fxPage=0;abPage=0;casPage=0;
    renderHisto();renderAbStats();renderAbTable();renderCasTable();renderFx();
  });
  document.getElementById('fx-prev').addEventListener('click',function(){if(fxPage>0){fxPage--;renderFxTable(fxRows());}});
  document.getElementById('fx-next').addEventListener('click',function(){fxPage++;renderFxTable(fxRows());});

  function renderAll(){renderChips();renderSeries();renderHisto();renderAbStats();renderAbTable();renderCasTable();renderShapley();renderFx();}
  cut.addEventListener('change',function(){renderHisto();abPage=0;casPage=0;renderAbStats();renderAbTable();renderCasTable();fxCut.value=cut.value;fxPage=0;renderFx();});
  renderAll();
}
if(typeof document!=='undefined')init();
</script>
</body>
</html>
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Grafica HTML autonoma de healthscore_v4, sin dependencias externas')
    parser.add_argument('--output', type=Path,
                        default=paths.ROOT / 'reports/score_charts/healthscore_v4/index.html')
    parser.add_argument('--input-dir', type=Path, default=None,
                        help='directorio con assessments.parquet y summary.json '
                             '(por defecto reports/score_v4)')
    parser.add_argument('--v3-dir', type=Path, default=None,
                        help='directorio con el assessments.parquet de healthscore_v3 '
                             'para la comparativa A-B (por defecto reports/score_v3; '
                             'si no existe, la vista 3 se degrada con aviso)')
    parser.add_argument('--fx-dir', type=Path, default=None,
                        help='directorio con fx_risk_monthly.parquet (capa F3) y '
                             'fx_index.parquet (indice F1b) para la vista 5 '
                             '(por defecto reports/fx_risk; si faltan, la vista 5 se '
                             'degrada con aviso declarado)')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva para conservarla')
    score_dir = args.input_dir if args.input_dir is not None else paths.ROOT / 'reports' / 'score_v4'
    v3_dir = args.v3_dir if args.v3_dir is not None else paths.ROOT / 'reports' / 'score_v3'
    fx_dir = args.fx_dir if args.fx_dir is not None else paths.ROOT / 'reports' / 'fx_risk'
    payload = _reduced_payload(score_dir / ASSESSMENTS_NAME, score_dir / SUMMARY_NAME,
                               v3_dir / ASSESSMENTS_NAME,
                               fx_path=fx_dir / FX_RISK_NAME,
                               fx_index_path=fx_dir / FX_INDEX_NAME)
    page = render_chart(payload)
    size = len(page.encode('utf-8'))
    if size > MAX_BYTES:
        raise ValueError(f'el HTML supera el tope de 25 MB ({size} bytes); no se muestrean empresas')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(page)
    print(json.dumps({'output': str(args.output), 'input_dir': str(score_dir),
                      'v3_dir': str(v3_dir), 'v3_disponible': payload['v3_disponible'],
                      'fx_dir': str(fx_dir), 'fx_disponible': payload['fx_disponible'],
                      'model_version': payload['model_version'],
                      'months': len(payload['months']), 'companies': payload['total_companies'],
                      'reduced_months': payload['reduced_months'], 'bytes': size}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
