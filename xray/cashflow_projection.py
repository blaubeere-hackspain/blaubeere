"""Proyeccion de caja por empresa: UNION de cobros y pagos + evaluacion + entrega.

Este modulo UNE las dos capas ya validadas por las tareas hermanas:
  - ``xray/collection_schedule.py`` (lado INFLOW, importes POSITIVOS),
  - ``xray/payment_schedule.py``    (lado OUTFLOW, importes NEGATIVOS),
y las mide contra los baselines del arnes ``xray/cashflow_forecast.py``.

El convenio de signo ya esta fijado y NO se re-firma: la union es una SUMA
DIRECTA (``neto = entradas + salidas`` con salidas negativas). Se verifica con
los datos antes de sumar.

Que hace, en orden:
  a) UNION
     - CALENDARIO NETO diario (company_id, corte, dia): entrada esperada,
       salida esperada, neto del dia, saldo acumulado y la columna
       ``dia_primer_saldo_negativo`` (nulo si nunca cruza).
     - AGREGADO por horizonte (company_id, corte, h), h en {30,60,90}:
       entradas esperadas, salidas esperadas, flujo neto esperado,
       ``saldo_proyectado`` = saldo del corte + flujo neto, y la etiqueta
       ``tendencia`` con banda neutra ex-ante.
     - COBERTURA ASIMETRICA declarada: la ausencia de un lado se trata como
       CERO CONOCIDO solo si la empresa existe en el censo del panel y no
       tiene facturas vivas de ese lado; en otro caso se marca con su motivo.
  b) EVALUACION walk-forward contra la verdad observada reutilizando el arnes
     (importado, nunca editado): sus cortes, su intervalo (corte, corte+h] y
     sus targets ``flujo_neto_acumulado_h`` y ``saldo_proyectado_h``. Se
     compara contra B3 "predice cero" (mediana global, el campeon actual) y B1
     persistencia sobre la MISMA poblacion comun. Se publican MAE, RMSE,
     calibracion agregada (ratio predicho/observado y sesgo) y acierto de signo
     con matriz de confusion frente a la regla trivial de la clase mayoritaria.
  c) TENDENCIA y ENTREGA: etiqueta ``tendencia`` por (empresa, corte, h) y el
     forecast del corte vivo 2026-08-31 por empresa, incluido el dia en que el
     saldo proyectado cruza a negativo.

Disciplina point-in-time: en el corte C solo influyen hechos conocidos en C.
Ambos lados ya lo garantizan con su test de no-fuga; la union no estima nada,
solo lee la fila del corte. El corte vivo y los cortes historicos nunca se
mezclan para estimar parametros (no hay parametros que estimar).

Alcance: lee ``reports/collection_schedule/*``, ``reports/payment_schedule/*``,
``reports/daily_flows/panel_diario.parquet``, ``reports/daily_flows/censo.json``
y ``reports/score_v4/assessments.parquet``; escribe
``reports/cashflow_projection/``. No modifica ningun fichero de las tareas
hermanas ni de daily_flows/cashflow_forecast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from xray import paths
from xray.cashflow_forecast import (
    BASELINES,
    HORIZONTES,
    LAST_CLOSED_DAY,
    _error_metrics,
    _month_ends,
    b3_by_corte,
    build_b3_pools,
    build_cortes,
    build_panel_index,
    build_prediction_frame,
    build_trios,
    density_membership,
    load_censo_json,
    load_evaluable_v4,
    load_panel,
)
from xray.daily_flows import build_density

# ---------------------------------------------------------------------------
# Contrato declarado
# ---------------------------------------------------------------------------
VERSION = 'cashflow_projection_v1'
GENERADOR = 'xray/cashflow_projection.py'

DEFAULT_COBROS_DIR = paths.ROOT / 'reports' / 'collection_schedule'
DEFAULT_PAGOS_DIR = paths.ROOT / 'reports' / 'payment_schedule'
DEFAULT_OUTPUT_DIR = paths.ROOT / 'reports' / 'cashflow_projection'
DEFAULT_PANEL = paths.ROOT / 'reports' / 'daily_flows' / 'panel_diario.parquet'
DEFAULT_CENSO_PANEL = paths.ROOT / 'reports' / 'daily_flows' / 'censo.json'
DEFAULT_ASSESSMENTS = paths.ROOT / 'reports' / 'score_v4' / 'assessments.parquet'

# Insumos copiados TAL CUAL de worktrees hermanos (no se editan). Los 12 primeros
# son los del encargo; los 5 del arnes G1 los autorizo el coordinador al resolver
# el choque spec-vs-realidad (el encargo decia que el arnes ya estaba en main y no
# lo estaba: vivia untracked en g1-cashflow-forecast).
INSUMOS_COPIADOS = {
    'cobros': [
        'xray/collection_schedule.py',
        'tests/test_collection_schedule.py',
        'reports/collection_schedule/calendario.parquet',
        'reports/collection_schedule/horizontes.parquet',
        'reports/collection_schedule/censo.json',
        'reports/collection_schedule/report.md',
    ],
    'pagos': [
        'xray/payment_schedule.py',
        'tests/test_payment_schedule.py',
        'reports/payment_schedule/calendario.parquet',
        'reports/payment_schedule/horizontes.parquet',
        'reports/payment_schedule/censo.json',
        'reports/payment_schedule/report.md',
    ],
    'arnes_g1_autorizado_por_el_coordinador': [
        'xray/cashflow_forecast.py',
        'tests/test_cashflow_forecast.py',
        'reports/cashflow_forecast/predicciones.parquet',
        'reports/cashflow_forecast/baselines.json',
        'reports/cashflow_forecast/report.md',
    ],
}

CORTE_VIVO = date(2026, 8, 31)
HORIZONTE_MAX = 90                 # el calendario de producto se trunca a (corte, corte+90]
BANDA_NEUTRA_FRACCION_DEFECTO = 0.10

VERSIONES_ESPERADAS = {
    'cobros': 'collection_schedule_v1',
    'pagos': 'payment_schedule_v1',
    'forecast': 'cashflow_forecast_v1',
}

# Referencias del corte vivo publicadas por las tareas hermanas (M EUR). Se
# reproducen desde los parquet; si no coinciden, el modulo aborta.
REFERENCIA_VIVO = {
    'cobros': {30: 263.6, 60: 309.7, 90: 321.1, 'empresas_calendario': 654},
    'pagos': {30: -356.3, 60: -417.8, 90: -439.9, 'empresas_calendario': 737},
}
TOLERANCIA_REFERENCIA_M = 0.1

ESTADOS_LADO = (
    'con_datos',
    'cero_conocido_sin_facturas_vivas',
    'desconocido_empresa_fuera_censo_panel',
)
ESTADOS_SALDO = ('con_saldo_corte', 'sin_saldo_corte_empresa_no_activa_en_panel')
CONOCIDOS = ('con_datos', 'cero_conocido_sin_facturas_vivas')

CONVENIO_SIGNO = (
    'Cobros publica importes POSITIVOS (entrada de caja) y pagos importes '
    'NEGATIVOS (salida de caja). La union es una SUMA DIRECTA sin re-firmar '
    'ningun lado: neto = entradas + salidas (salidas negativas).'
)

JUSTIFICACION_BANDA = (
    'Cada lado es una esperanza (P(pago) x importe) estimada por separado a partir '
    'de cohortes PIT con error de muestreo; un desequilibrio neto inferior a una '
    'fraccion del movimiento bruto no es distinguible del ruido de estimacion de '
    'las dos probabilidades. Se usa banda = fraccion * (entradas + |salidas|) con '
    f'fraccion por defecto {BANDA_NEUTRA_FRACCION_DEFECTO:.2f}. La banda es EX-ANTE '
    '(solo usa el forecast) y configurable por CLI con --banda-neutra.'
)

PUNTO_LEAK = (
    'La union no mezcla el corte vivo con los cortes historicos al estimar nada: '
    'no hay parametros que estimar, solo se lee la fila del corte C de cada lado. '
    'El corte vivo se publica como forecast de producto (sin target observable) y '
    'los cortes historicos se usan solo para medir.'
)

LIMITACIONES = [
    ('El calendario de producto se trunca a (corte, corte+90]. El calendario de '
     'cobros ya viene truncado a 90 dias; el de pagos publica fechas esperadas mas '
     'alla de 90 dias, que se descartan por quedar fuera del horizonte de producto. '
     'Se publica cuanto importe de pagos cae fuera de la ventana.'),
    ('El universo por corte son las empresas con horizontes en algun lado MAS las '
     'empresas con fila en el panel en ese corte. Una empresa del panel sin facturas '
     'vivas de un lado recibe 0 en ese lado (cero conocido); no se imputa el saldo.'),
    ('El forecast es de flujo inducido por facturas: los movimientos de caja sin '
     'factura (nominas, impuestos, transferencias, financiacion) no se modelan y no '
     'entran en el flujo esperado. Para empresas sin facturas vivas el forecast neto '
     'es 0 y el saldo proyectado queda igual al saldo del corte.'),
    ('SCOPE: la verdad observada del arnes es el flujo neto TOTAL del panel de '
     'transacciones, cuyo alcance NO coincide con el del dinero facturero. Por eso la '
     'comparativa de calibracion agregada es desfavorable por construccion del target; '
     'la pieza debe leerse como calendario de caja comprometida en facturas, no como '
     'predictor de la caja total.'),
    ('saldo_reversa_eur se lee de la fila del corte del panel; si la empresa no '
     'esta activa en el panel ese dia, el saldo proyectado es nulo (nunca cero).'),
    PUNTO_LEAK,
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _input_info(path) -> dict:
    path = Path(path)
    try:
        display = str(path.relative_to(paths.ROOT))
    except ValueError:
        display = str(path)
    if not path.exists():
        return {'path': display, 'existe': False}
    return {'path': display, 'existe': True, 'bytes': path.stat().st_size,
            'sha256': _file_sha256(path)}


def _as_day(series: pd.Series) -> pd.Series:
    """Normaliza a Timestamp a medianoche (dia de calendario)."""
    return pd.to_datetime(series).dt.normalize()


def _finite(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _suma_signos(entrada, salida):
    """Suma directa de los dos lados (None = lado desconocido)."""
    if entrada is None or salida is None:
        return None
    return float(entrada) + float(salida)


def saldo_proyectado(saldo_corte, flujo_neto):
    """Saldo del corte + flujo neto. NUNCA imputa: si falta el saldo, es nulo."""
    saldo_corte = _finite(saldo_corte)
    flujo_neto = _finite(flujo_neto)
    if saldo_corte is None or flujo_neto is None:
        return None
    return saldo_corte + flujo_neto


def clasificar_tendencia(entrada, salida, flujo_neto,
                         fraccion=BANDA_NEUTRA_FRACCION_DEFECTO):
    """Etiqueta {positiva, negativa, neutra} desde el signo del flujo neto.

    Banda neutra = fraccion * (entradas + |salidas|): un neto menor en valor
    absoluto que esa fraccion del movimiento bruto no declara direccion.
    """
    flujo_neto = _finite(flujo_neto)
    if flujo_neto is None:
        return None
    entrada = 0.0 if entrada is None else abs(float(entrada))
    salida = 0.0 if salida is None else abs(float(salida))
    banda = float(fraccion) * (entrada + salida)
    if flujo_neto > banda:
        return 'positiva'
    if flujo_neto < -banda:
        return 'negativa'
    return 'neutra'


def _serie_igual(left: pd.Series, right: pd.Series) -> bool:
    left = left.reset_index(drop=True)
    right = right.reset_index(drop=True)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        a_null, b_null = _is_null(a), _is_null(b)
        if a_null and b_null:
            continue
        if a_null != b_null:
            return False
        if isinstance(a, (pd.Timestamp, np.datetime64)) or isinstance(b, (pd.Timestamp, np.datetime64)):
            if pd.Timestamp(a) != pd.Timestamp(b):
                return False
        elif isinstance(a, (float, np.floating)) or isinstance(b, (float, np.floating)):
            if not math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6):
                return False
        elif a != b:
            return False
    return True


# ---------------------------------------------------------------------------
# 1. Carga de los dos lados (solo lectura, tal cual los publicaron sus tareas)
# ---------------------------------------------------------------------------
def load_lado(directorio, lado: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Lee calendario.parquet, horizontes.parquet y censo.json de un lado.

    Normaliza las fechas a dia de calendario y comprueba el convenio de signo
    (cobros >= 0, pagos <= 0) SIN re-firmar importes.
    """
    directorio = Path(directorio)
    calendario = pd.read_parquet(directorio / 'calendario.parquet')
    horizontes = pd.read_parquet(directorio / 'horizontes.parquet')
    censo = json.loads((directorio / 'censo.json').read_text(encoding='utf-8'))

    for frame in (calendario, horizontes):
        frame['company_id'] = frame['company_id'].astype(str)
        frame['corte'] = _as_day(frame['corte'])
    calendario['dia'] = _as_day(calendario['dia'])
    calendario = calendario[['company_id', 'corte', 'dia', 'importe_esperado_eur']].copy()
    horizontes['h'] = horizontes['h'].astype(int)
    columnas = ['company_id', 'corte', 'h', 'importe_esperado_eur']
    for extra in ('evaluable', 'objetivo_observable', 'n_facturas_en_ventana',
                  'n_facturas_valoradas', 'n_facturas_vivas', 'importe_vivo_eur',
                  'motivos_no_valorable'):
        if extra in horizontes.columns:
            columnas.append(extra)
    horizontes = horizontes[columnas].copy()

    minimo = float(calendario['importe_esperado_eur'].min())
    maximo = float(calendario['importe_esperado_eur'].max())
    if lado == 'cobros' and minimo < -1e-6:
        raise ValueError(f'cobros debe publicar importes positivos; minimo {minimo}')
    if lado == 'pagos' and maximo > 1e-6:
        raise ValueError(f'pagos debe publicar importes negativos; maximo {maximo}')
    return calendario, horizontes, censo


def verificar_version(censo: dict, lado: str) -> str:
    version = censo.get('version')
    esperada = VERSIONES_ESPERADAS[lado]
    if version != esperada:
        raise ValueError(f'version inesperada del censo de {lado}: {version!r} != {esperada!r}')
    return version


def _cortes_de_los_lados(cobros_hor: pd.DataFrame, pagos_hor: pd.DataFrame) -> list:
    cortes = set(cobros_hor['corte']).union(set(pagos_hor['corte']))
    return sorted(pd.Timestamp(c) for c in cortes)


# ---------------------------------------------------------------------------
# 2. Union: calendario neto diario
# ---------------------------------------------------------------------------
def _primer_dia_negativo(dias, netos, saldo_corte):
    """Primer dia con saldo acumulado < 0, o None.

    None si falta el saldo del corte o si ya era negativo en el corte (en ese
    caso no hay cruce de cero dentro de la ventana).
    """
    saldo_corte = _finite(saldo_corte)
    if saldo_corte is None or saldo_corte < 0:
        return None
    acumulado = saldo_corte
    for dia, neto in zip(dias, netos):
        acumulado += 0.0 if _is_null(neto) else float(neto)
        if acumulado < 0:
            return dia
    return None


def construir_calendario_neto(cobros_cal: pd.DataFrame, pagos_cal: pd.DataFrame,
                              estado_cobros: dict, estado_pagos: dict,
                              saldo_lookup: dict, horizonte_max: int = HORIZONTE_MAX,
                              ) -> pd.DataFrame:
    """Union diaria de los dos calendarios en (corte, corte+horizonte_max].

    ``estado_cobros``/``estado_pagos``: {(company_id, corte): estado}. Un lado
    ausente en un dia se imputa 0 SOLO si su estado es conocido; si el lado es
    desconocido, el neto es nulo. ``saldo_lookup``: {(company_id, corte): saldo}.
    """
    cobros = cobros_cal.rename(columns={'importe_esperado_eur': 'entrada_esperada_eur'})
    pagos = pagos_cal.rename(columns={'importe_esperado_eur': 'salida_esperada_eur'})
    # Grano de dia de calendario: el calendario de pagos trae marcas de tiempo
    # sub-diarias; se suman por dia antes de unir para no multiplicar filas.
    cobros = (cobros.groupby(['company_id', 'corte', 'dia'], as_index=False)
              ['entrada_esperada_eur'].sum())
    pagos = (pagos.groupby(['company_id', 'corte', 'dia'], as_index=False)
             ['salida_esperada_eur'].sum())
    delta = pd.Timedelta(days=horizonte_max)
    cobros = cobros[(cobros['dia'] > cobros['corte']) & (cobros['dia'] <= cobros['corte'] + delta)]
    pagos = pagos[(pagos['dia'] > pagos['corte']) & (pagos['dia'] <= pagos['corte'] + delta)]
    cobros = cobros[['company_id', 'corte', 'dia', 'entrada_esperada_eur']]
    pagos = pagos[['company_id', 'corte', 'dia', 'salida_esperada_eur']]

    marco = pd.concat([cobros[['company_id', 'corte', 'dia']],
                       pagos[['company_id', 'corte', 'dia']]]).drop_duplicates()
    diario = marco.merge(cobros, on=['company_id', 'corte', 'dia'], how='left')
    diario = diario.merge(pagos, on=['company_id', 'corte', 'dia'], how='left')

    claves = list(zip(diario['company_id'], diario['corte']))
    diario['estado_cobros'] = [estado_cobros.get(k, ESTADOS_LADO[2]) for k in claves]
    diario['estado_pagos'] = [estado_pagos.get(k, ESTADOS_LADO[2]) for k in claves]
    entrada = diario['entrada_esperada_eur'].to_numpy(dtype=float, na_value=np.nan)
    salida = diario['salida_esperada_eur'].to_numpy(dtype=float, na_value=np.nan)
    entrada_ok = diario['estado_cobros'].isin(CONOCIDOS).to_numpy()
    salida_ok = diario['estado_pagos'].isin(CONOCIDOS).to_numpy()
    diario['entrada_esperada_eur'] = np.where(~np.isnan(entrada), entrada,
                                              np.where(entrada_ok, 0.0, np.nan))
    diario['salida_esperada_eur'] = np.where(~np.isnan(salida), salida,
                                             np.where(salida_ok, 0.0, np.nan))
    diario['neto_dia_eur'] = diario['entrada_esperada_eur'] + diario['salida_esperada_eur']
    diario['saldo_corte_eur'] = [saldo_lookup.get(k) for k in claves]
    diario = diario.sort_values(['company_id', 'corte', 'dia'], kind='stable').reset_index(drop=True)

    claves = list(zip(diario['company_id'], diario['corte']))
    acumulado = diario.groupby(['company_id', 'corte'], sort=False)['neto_dia_eur'].cumsum()
    diario['saldo_acumulado_eur'] = diario['saldo_corte_eur'] + acumulado

    primer_dia = {}
    for (company_id, corte), grupo in diario.groupby(['company_id', 'corte'], sort=False):
        primer_dia[(company_id, corte)] = _primer_dia_negativo(
            list(grupo['dia']), list(grupo['neto_dia_eur']), saldo_lookup.get((company_id, corte)))
    diario['dia_primer_saldo_negativo'] = [primer_dia.get(k) for k in claves]
    diario['saldo_corte_negativo'] = [
        bool(_finite(saldo_lookup.get(k)) is not None and _finite(saldo_lookup.get(k)) < 0)
        for k in claves]
    orden = ['company_id', 'corte', 'dia', 'entrada_esperada_eur', 'salida_esperada_eur',
             'neto_dia_eur', 'saldo_corte_eur', 'saldo_acumulado_eur',
             'dia_primer_saldo_negativo', 'saldo_corte_negativo',
             'estado_cobros', 'estado_pagos']
    return diario[orden].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Union: agregado por horizonte
# ---------------------------------------------------------------------------
def construir_horizontes_union(cobros_hor: pd.DataFrame, pagos_hor: pd.DataFrame,
                               panel: pd.DataFrame, censo_panel: set,
                               horizonte_max: int = HORIZONTE_MAX) -> pd.DataFrame:
    """Agregado (empresa, corte, h) con signos sumados y cobertura declarada.

    Universo por corte: empresas con horizontes en cualquier lado MAS empresas
    con fila en el panel en ese corte. La ausencia de un lado es cero conocido si
    la empresa esta en el censo del panel; si no, se marca con su motivo.
    """
    cortes = _cortes_de_los_lados(cobros_hor, pagos_hor)
    panel_dia = panel[['company_id', 'day']].copy()
    panel_dia['corte'] = _as_day(panel_dia['day'])
    panel_dia = panel_dia[panel_dia['corte'].isin(cortes)][['company_id', 'corte']]
    saldo_panel = panel[['company_id', 'day', 'saldo_reversa_eur']].copy()
    saldo_panel['corte'] = _as_day(saldo_panel['day'])
    saldo_lookup = {(str(c), t): _finite(v) for c, t, v in
                    zip(saldo_panel['company_id'], saldo_panel['corte'],
                        saldo_panel['saldo_reversa_eur'])}

    lados_cobros = cobros_hor[['company_id', 'corte']].drop_duplicates()
    lados_pagos = pagos_hor[['company_id', 'corte']].drop_duplicates()
    universo = pd.concat([lados_cobros, lados_pagos, panel_dia]).drop_duplicates()
    universo = universo.sort_values(['company_id', 'corte'], kind='stable').reset_index(drop=True)

    en_cobros = set(map(tuple, lados_cobros[['company_id', 'corte']].values))
    en_pagos = set(map(tuple, lados_pagos[['company_id', 'corte']].values))

    def _cobertura(clave):
        c, p = clave in en_cobros, clave in en_pagos
        if c and p:
            return 'ambos'
        if c:
            return 'solo_cobros'
        if p:
            return 'solo_pagos'
        return 'ninguno'

    def _estado(clave, presente):
        if presente:
            return ESTADOS_LADO[0]
        return ESTADOS_LADO[1] if clave[0] in censo_panel else ESTADOS_LADO[2]

    claves = list(zip(universo['company_id'], universo['corte']))
    universo['cobertura_lado'] = [_cobertura(k) for k in claves]
    universo['estado_cobros'] = [_estado(k, k in en_cobros) for k in claves]
    universo['estado_pagos'] = [_estado(k, k in en_pagos) for k in claves]
    universo['saldo_corte_eur'] = [saldo_lookup.get(k) for k in claves]
    universo['estado_saldo'] = np.where(universo['saldo_corte_eur'].notna(),
                                        ESTADOS_SALDO[0], ESTADOS_SALDO[1])

    filas = []
    for horizonte in HORIZONTES:
        ch = cobros_hor[cobros_hor['h'] == horizonte][
            ['company_id', 'corte', 'importe_esperado_eur']].rename(
            columns={'importe_esperado_eur': 'entrada_esperada_eur'})
        ph = pagos_hor[pagos_hor['h'] == horizonte][
            ['company_id', 'corte', 'importe_esperado_eur']].rename(
            columns={'importe_esperado_eur': 'salida_esperada_eur'})
        bloque = universo.merge(ch, on=['company_id', 'corte'], how='left')
        bloque = bloque.merge(ph, on=['company_id', 'corte'], how='left')
        entrada = bloque['entrada_esperada_eur'].to_numpy(dtype=float, na_value=np.nan)
        salida = bloque['salida_esperada_eur'].to_numpy(dtype=float, na_value=np.nan)
        entrada_ok = bloque['estado_cobros'].isin(CONOCIDOS).to_numpy()
        salida_ok = bloque['estado_pagos'].isin(CONOCIDOS).to_numpy()
        bloque['entrada_esperada_eur'] = np.where(~np.isnan(entrada), entrada,
                                                  np.where(entrada_ok, 0.0, np.nan))
        bloque['salida_esperada_eur'] = np.where(~np.isnan(salida), salida,
                                                 np.where(salida_ok, 0.0, np.nan))
        bloque['flujo_neto_esperado_eur'] = (bloque['entrada_esperada_eur']
                                             + bloque['salida_esperada_eur'])
        bloque['saldo_proyectado_eur'] = [
            saldo_proyectado(s, f) for s, f in
            zip(bloque['saldo_corte_eur'], bloque['flujo_neto_esperado_eur'])]
        bloque['tendencia'] = [
            clasificar_tendencia(e, s, f) for e, s, f in
            zip(bloque['entrada_esperada_eur'], bloque['salida_esperada_eur'],
                bloque['flujo_neto_esperado_eur'])]
        bloque['h'] = horizonte
        filas.append(bloque)

    agregado = pd.concat(filas, ignore_index=True)
    orden = ['company_id', 'corte', 'h', 'entrada_esperada_eur', 'salida_esperada_eur',
             'flujo_neto_esperado_eur', 'saldo_corte_eur', 'saldo_proyectado_eur',
             'tendencia', 'cobertura_lado', 'estado_cobros', 'estado_pagos',
             'estado_saldo']
    return agregado[orden].sort_values(['company_id', 'corte', 'h'],
                                       kind='stable').reset_index(drop=True)


def construir_forecast_vivo(agregado: pd.DataFrame, calendario: pd.DataFrame,
                            corte_vivo: date = CORTE_VIVO) -> pd.DataFrame:
    """Forecast del corte vivo con el primer dia de saldo negativo incorporado."""
    corte = pd.Timestamp(corte_vivo)
    vivo = agregado[agregado['corte'] == corte].copy()
    primeros = (calendario[calendario['corte'] == corte]
                [['company_id', 'dia_primer_saldo_negativo', 'saldo_corte_negativo']]
                .drop_duplicates('company_id'))
    vivo = vivo.merge(primeros, on='company_id', how='left')
    return vivo.sort_values(['company_id', 'h'], kind='stable').reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. Reproduccion de las cifras de referencia del corte vivo
# ---------------------------------------------------------------------------
def reproducir_referencias(cobros_hor: pd.DataFrame, pagos_hor: pd.DataFrame,
                           cobros_cal: pd.DataFrame, pagos_cal: pd.DataFrame,
                           corte_vivo: date = CORTE_VIVO) -> dict:
    """Comprueba las cifras de referencia del corte vivo (M EUR y empresas)."""
    corte = pd.Timestamp(corte_vivo)
    resultado = {}
    for lado, frame in (('cobros', cobros_hor), ('pagos', pagos_hor)):
        sub = frame[frame['corte'] == corte]
        resultado[lado] = {
            'por_h_m_eur': {str(horizonte): float(sub.loc[sub['h'] == horizonte,
                                                          'importe_esperado_eur'].sum()) / 1e6
                            for horizonte in HORIZONTES},
            'empresas_horizontes': int(sub['company_id'].nunique()),
        }
    resultado['cobros']['empresas_calendario'] = int(
        cobros_cal.loc[cobros_cal['corte'] == corte, 'company_id'].nunique())
    resultado['pagos']['empresas_calendario'] = int(
        pagos_cal.loc[pagos_cal['corte'] == corte, 'company_id'].nunique())
    for lado, esperado in REFERENCIA_VIVO.items():
        for horizonte in HORIZONTES:
            medido = resultado[lado]['por_h_m_eur'][str(horizonte)]
            if abs(medido - esperado[horizonte]) > TOLERANCIA_REFERENCIA_M:
                raise ValueError(
                    f'no se reproduce la referencia del corte vivo {lado} h={horizonte}: '
                    f'medido {medido:.3f} M EUR vs esperado {esperado[horizonte]:.1f} M EUR')
        if resultado[lado]['empresas_calendario'] != esperado['empresas_calendario']:
            raise ValueError(
                f'no se reproduce el censo de empresas con calendario de {lado}: '
                f"{resultado[lado]['empresas_calendario']} vs "
                f"{esperado['empresas_calendario']}")
    return resultado


# ---------------------------------------------------------------------------
# 5. Evaluacion walk-forward reutilizando el arnes
# ---------------------------------------------------------------------------
def construir_frame_arnes(panel: pd.DataFrame, evaluable: set) -> tuple[pd.DataFrame, dict]:
    """Reutiliza el arnes importado y devuelve su frame de predicciones."""
    move_by_company = build_density(panel)
    membership = density_membership(move_by_company)
    panel_min = panel['day'].min().date()
    cortes = build_cortes(panel_min)
    populations = {horizonte: membership[horizonte] for horizonte in HORIZONTES}
    index = build_panel_index(panel)
    trios = build_trios(index, populations, cortes['_dates'])
    month_ends = _month_ends(panel_min, LAST_CLOSED_DAY)
    b3_pools = build_b3_pools(index, month_ends)
    b3_lookup = {str(horizonte): b3_by_corte(b3_pools[horizonte],
                                             cortes['_dates'][str(horizonte)], horizonte)
                 for horizonte in HORIZONTES}
    frame = build_prediction_frame(index, trios, b3_lookup)
    return frame, {'cortes': cortes, 'poblaciones': populations, 'trios': trios}


def _comparativa(frame: pd.DataFrame, base_mask: pd.Series) -> dict:
    """Metricas por objetivo/horizonte/modelo sobre una mascara de poblacion."""
    comparativa = {}
    for objetivo, sufijo, objetivo_col in (
            ('flujo_neto_acumulado', 'flujo', 'target_flujo_neto_acumulado_eur'),
            ('saldo_proyectado', 'saldo', 'target_saldo_proyectado_eur')):
        comparativa[objetivo] = {}
        columnas = [f'{nombre}_{sufijo}_eur' for nombre in BASELINES] + [f'union_{sufijo}_eur']
        comun = base_mask & frame[columnas].notna().all(axis=1) & frame[objetivo_col].notna()
        base = frame[comun]
        for horizonte in HORIZONTES:
            sub = base[base['h'] == horizonte]
            bloque = {}
            for nombre in [*BASELINES, 'union']:
                if sufijo == 'flujo':
                    errores = sub[f'{nombre}_error_flujo_eur'].tolist()
                    escala = sub['escala_empresa_h_eur'].tolist()
                else:
                    col = ('union_error_saldo_eur' if nombre == 'union'
                           else f'{nombre}_error_saldo_eur')
                    errores = sub[col].tolist()
                    escala = None
                bloque[nombre] = _error_metrics(errores, escala)
            comparativa[objetivo][str(horizonte)] = bloque
        comparativa[objetivo]['n_comun'] = int(comun.sum())
        comparativa[objetivo]['n_comun_por_h'] = {
            str(horizonte): int((comun & (frame['h'] == horizonte)).sum())
            for horizonte in HORIZONTES}
    return comparativa


def _correlaciones(frame: pd.DataFrame) -> dict:
    """Correlacion lineal y de rangos del forecast union con el target observado.

    Se calcula el rango con pandas (sin scipy, que no esta en el venv canonico).
    """
    target = 'target_flujo_neto_acumulado_eur'
    columnas = [f'{b}_flujo_eur' for b in BASELINES] + ['union_flujo_eur']
    comun = frame[columnas].notna().all(axis=1) & frame[target].notna()
    base = frame[comun]
    resultado = {}
    for etiqueta, sub in (('poblacion_comun', base),
                          ('poblacion_informada', base[base['union_flujo_eur'].abs() > 1e-9])):
        resultado[etiqueta] = {}
        for horizonte in HORIZONTES:
            s = sub[sub['h'] == horizonte]
            if len(s) < 3:
                resultado[etiqueta][str(horizonte)] = {
                    'n': int(len(s)), 'pearson': None, 'spearman': None}
                continue
            pearson = s['union_flujo_eur'].corr(s[target])
            rank = s['union_flujo_eur'].rank().corr(s[target].rank())
            resultado[etiqueta][str(horizonte)] = {
                'n': int(len(s)),
                'pearson': float(pearson) if pearson == pearson else None,
                'spearman': float(rank) if rank == rank else None,
            }
    return resultado


def evaluar(frame_arnes: pd.DataFrame, agregado: pd.DataFrame) -> dict:
    """Anade el forecast union al frame del arnes y calcula la comparativa."""
    mio = agregado[['company_id', 'corte', 'h', 'entrada_esperada_eur',
                    'salida_esperada_eur', 'flujo_neto_esperado_eur',
                    'saldo_proyectado_eur', 'tendencia']].rename(columns={
        'entrada_esperada_eur': 'union_entrada_eur',
        'salida_esperada_eur': 'union_salida_eur',
        'flujo_neto_esperado_eur': 'union_flujo_eur',
        'saldo_proyectado_eur': 'union_saldo_agregado_eur',
        'tendencia': 'union_tendencia'})
    frame = frame_arnes.copy()
    frame['corte'] = _as_day(frame['corte'])
    frame = frame.merge(mio, on=['company_id', 'corte', 'h'], how='left')
    frame['union_flujo_eur'] = frame['union_flujo_eur'].fillna(0.0)
    frame['union_tendencia'] = frame['union_tendencia'].fillna('neutra')
    # El saldo proyectado se calcula con el saldo del corte del ARNES (mismo panel).
    frame['union_saldo_eur'] = [
        saldo_proyectado(s, f) for s, f in
        zip(frame['saldo_corte_eur'], frame['union_flujo_eur'])]

    target = 'target_flujo_neto_acumulado_eur'
    frame['union_error_flujo_eur'] = frame['union_flujo_eur'] - frame[target]
    frame['union_error_saldo_eur'] = (frame['union_saldo_eur']
                                      - frame['target_saldo_proyectado_eur'])
    frame['union_signo'] = np.where(frame['union_flujo_eur'] > 0, 'positiva',
                                    np.where(frame['union_flujo_eur'] < 0, 'negativa',
                                             'neutra'))
    frame['union_tendencia'] = frame['union_tendencia'].fillna('neutra')

    todas = pd.Series(True, index=frame.index)
    informada = frame['union_flujo_eur'].abs() > 1e-9
    comparativa = _comparativa(frame, todas)
    comparativa_informada = _comparativa(frame, informada)

    target = 'target_flujo_neto_acumulado_eur'
    columnas = [f'{b}_flujo_eur' for b in BASELINES] + ['union_flujo_eur']
    comun = frame[columnas].notna().all(axis=1) & frame[target].notna()
    base = frame[comun]
    outliers = base.reindex(base[target].abs().sort_values(ascending=False).index)[
        ['company_id', 'corte', 'h', target, 'union_flujo_eur']].head(10)
    outliers = [{'company_id': row['company_id'], 'corte': pd.Timestamp(row['corte']).date().isoformat(),
                 'h': int(row['h']), 'observado_eur': float(row[target]),
                 'union_flujo_eur': float(row['union_flujo_eur'])}
                for _, row in outliers.iterrows()]

    return {'frame': frame, 'comparativa': comparativa,
            'comparativa_informada': comparativa_informada,
            'correlaciones': _correlaciones(frame),
            'outliers_observado': outliers}


def _calibracion(frame: pd.DataFrame, columna: str, nombre: str,
                 solo_informados: bool = False) -> dict:
    """Suma forecast vs suma observada por corte y horizonte (poblacion comun)."""
    target = 'target_flujo_neto_acumulado_eur'
    columnas = [f'{b}_flujo_eur' for b in BASELINES]
    comun = (frame[columnas].notna().all(axis=1) & frame[target].notna()
             & frame[columna].notna())
    if solo_informados:
        comun = comun & (frame['union_flujo_eur'].abs() > 1e-9)
    base = frame[comun]
    detalle = []
    for (corte, horizonte), grupo in base.groupby(['corte', 'h'], sort=True):
        predicho = float(grupo[columna].sum())
        observado = float(grupo[target].sum())
        detalle.append({
            'corte': pd.Timestamp(corte).date().isoformat(),
            'h': int(horizonte),
            'n': int(len(grupo)),
            'predicho_eur': predicho,
            'observado_eur': observado,
            'ratio_predicho_observado': (predicho / observado) if observado else None,
            'sesgo_eur': predicho - observado,
        })
    por_h = {}
    for horizonte in HORIZONTES:
        sub = base[base['h'] == horizonte]
        predicho = float(sub[columna].sum())
        observado = float(sub[target].sum())
        por_h[str(horizonte)] = {
            'n': int(len(sub)),
            'predicho_eur': predicho,
            'observado_eur': observado,
            'ratio_predicho_observado': (predicho / observado) if observado else None,
            'sesgo_eur': predicho - observado,
        }
    return {'modelo': nombre, 'por_horizonte': por_h, 'por_corte_y_horizonte': detalle}


def _confusion(sub: pd.DataFrame, pred_col: str, obs_col: str) -> dict:
    clases = ('positiva', 'negativa', 'neutra')
    n = len(sub)
    predicha = {c: int((sub[pred_col] == c).sum()) for c in clases}
    observada = {c: int((sub[obs_col] == c).sum()) for c in clases}
    matriz = {p: {o: 0 for o in clases} for p in clases}
    for p, o in zip(sub[pred_col], sub[obs_col]):
        matriz[p][o] += 1
    aciertos = sum(matriz[c][c] for c in clases)
    mayoritaria = max(observada.values()) if observada else 0
    direccional = sub[sub[pred_col] != 'neutra']
    return {
        'n': n,
        'distribucion_predicha': predicha,
        'distribucion_observada': observada,
        'matriz_confusion_predicha_filas_observada_columnas': matriz,
        'acierto': (aciertos / n) if n else None,
        'trivial_clase_mayoritaria': (mayoritaria / n) if n else None,
        'clase_mayoritaria_observada': (max(observada, key=observada.get) if n else None),
        'supera_trivial': (aciertos / n > mayoritaria / n) if n else None,
        'direccional': {
            'n': int(len(direccional)),
            'acierto': (float((direccional[pred_col] == direccional[obs_col]).mean())
                        if len(direccional) else None),
            'trivial_clase_mayoritaria': (
                float(direccional[obs_col].value_counts().iloc[0] / len(direccional))
                if len(direccional) else None),
        },
    }


def calcular_signo(frame: pd.DataFrame) -> dict:
    """Acierto de signo (banda y estricto) frente al signo observado."""
    target = 'target_flujo_neto_acumulado_eur'
    columnas = [f'{b}_flujo_eur' for b in BASELINES]
    comun = (frame[columnas].notna().all(axis=1) & frame[target].notna()
             & frame['union_flujo_eur'].notna())
    base = frame[comun]
    resultado = {'poblacion_comun': int(comun.sum()), 'por_horizonte': {}}
    for horizonte in HORIZONTES:
        sub = base[base['h'] == horizonte]
        resultado['por_horizonte'][str(horizonte)] = {
            'tendencia_con_banda': _confusion(sub, 'union_tendencia', 'signo_observado'),
            'signo_estricto': _confusion(sub, 'union_signo', 'signo_observado'),
        }
    return resultado


# ---------------------------------------------------------------------------
# 6. Resumenes de producto del corte vivo y cobertura
# ---------------------------------------------------------------------------
def resumen_cobertura(agregado: pd.DataFrame) -> dict:
    """Conteo de casos de cobertura por corte (universo, lados y saldo)."""
    por_corte = []
    for corte, grupo in agregado.groupby('corte', sort=True):
        unico = grupo.drop_duplicates('company_id')
        por_corte.append({
            'corte': pd.Timestamp(corte).date().isoformat(),
            'n_empresas': int(len(unico)),
            'cobertura_lado': {clase: int((unico['cobertura_lado'] == clase).sum())
                               for clase in ('ambos', 'solo_cobros', 'solo_pagos', 'ninguno')},
            'estado_cobros': {clase: int((unico['estado_cobros'] == clase).sum())
                              for clase in ESTADOS_LADO},
            'estado_pagos': {clase: int((unico['estado_pagos'] == clase).sum())
                             for clase in ESTADOS_LADO},
            'estado_saldo': {clase: int((unico['estado_saldo'] == clase).sum())
                             for clase in ESTADOS_SALDO},
        })
    return {'por_corte': por_corte}


def resumen_corte_vivo(agregado: pd.DataFrame, calendario: pd.DataFrame,
                       corte_vivo: date = CORTE_VIVO) -> dict:
    """Totales del corte vivo, cobertura y cruces a saldo negativo."""
    corte = pd.Timestamp(corte_vivo)
    sub = agregado[agregado['corte'] == corte]
    totales = {}
    for horizonte in HORIZONTES:
        bloque = sub[sub['h'] == horizonte]
        totales[str(horizonte)] = {
            'n_empresas': int(bloque['company_id'].nunique()),
            'entradas_esperadas_eur': float(bloque['entrada_esperada_eur'].sum()),
            'salidas_esperadas_eur': float(bloque['salida_esperada_eur'].sum()),
            'flujo_neto_esperado_eur': float(bloque['flujo_neto_esperado_eur'].sum()),
            'n_saldo_proyectado': int(bloque['saldo_proyectado_eur'].notna().sum()),
            'suma_saldo_proyectado_eur': float(bloque['saldo_proyectado_eur'].sum()),
            'tendencia': {clase: int((bloque['tendencia'] == clase).sum())
                          for clase in ('positiva', 'negativa', 'neutra')},
        }
    unico = sub.drop_duplicates('company_id')
    cal_vivo = calendario[calendario['corte'] == corte]
    primeros = (cal_vivo.groupby('company_id')['dia_primer_saldo_negativo'].first()
                .reset_index())
    base = unico[['company_id']].merge(primeros, on='company_id', how='left')
    cruzan = base[base['dia_primer_saldo_negativo'].notna()]
    dias = [(pd.Timestamp(d) - corte).days for d in cruzan['dia_primer_saldo_negativo']]
    saldo_corte = unico.set_index('company_id')['saldo_corte_eur']
    ya_negativos = int((saldo_corte < 0).sum())
    return {
        'corte': corte.date().isoformat(),
        'totales_por_horizonte': totales,
        'cobertura': {
            'n_empresas_universo': int(len(unico)),
            'cobertura_lado': {clase: int((unico['cobertura_lado'] == clase).sum())
                               for clase in ('ambos', 'solo_cobros', 'solo_pagos', 'ninguno')},
            'estado_cobros': {clase: int((unico['estado_cobros'] == clase).sum())
                              for clase in ESTADOS_LADO},
            'estado_pagos': {clase: int((unico['estado_pagos'] == clase).sum())
                             for clase in ESTADOS_LADO},
            'estado_saldo': {clase: int((unico['estado_saldo'] == clase).sum())
                             for clase in ESTADOS_SALDO},
            'n_con_saldo_corte': int(unico['saldo_corte_eur'].notna().sum()),
        },
        'cruces_saldo_negativo_90d': {
            'n_empresas_cruzan': int(len(cruzan)),
            'dia_mediano_dias_desde_corte': (float(np.median(dias)) if dias else None),
            'dia_mediano_fecha': (corte + timedelta(days=int(np.median(dias)))).date().isoformat()
            if dias else None,
            'dia_p25_dias': (float(np.percentile(dias, 25)) if dias else None),
            'dia_p75_dias': (float(np.percentile(dias, 75)) if dias else None),
            'n_empresas_ya_negativas_en_el_corte': ya_negativos,
        },
    }


# ---------------------------------------------------------------------------
# 7. Ensamblado del informe
# ---------------------------------------------------------------------------
def build_report(cobros_dir, pagos_dir, panel_path=None, censo_panel_path=None,
                 assessments_path=None, banda_fraccion=BANDA_NEUTRA_FRACCION_DEFECTO,
                 corte_vivo=CORTE_VIVO):
    panel_path = Path(panel_path) if panel_path else DEFAULT_PANEL
    censo_panel_path = Path(censo_panel_path) if censo_panel_path else DEFAULT_CENSO_PANEL
    assessments_path = Path(assessments_path) if assessments_path else DEFAULT_ASSESSMENTS

    cobros_cal, cobros_hor, cobros_censo = load_lado(cobros_dir, 'cobros')
    pagos_cal, pagos_hor, pagos_censo = load_lado(pagos_dir, 'pagos')
    versiones = {
        'cobros': verificar_version(cobros_censo, 'cobros'),
        'pagos': verificar_version(pagos_censo, 'pagos'),
    }

    panel = load_panel(panel_path)
    censo_panel_json = load_censo_json(censo_panel_path)
    evaluable = load_evaluable_v4(assessments_path)
    censo_panel = set(panel['company_id'].astype(str))
    dias_panel = _as_day(panel['day'])
    saldo_lookup = {(str(c), d): _finite(v) for c, d, v in
                    zip(panel['company_id'].astype(str), dias_panel,
                        panel['saldo_reversa_eur'])}

    referencias = reproducir_referencias(cobros_hor, pagos_hor, cobros_cal, pagos_cal,
                                         corte_vivo)
    delta_max = pd.Timedelta(days=HORIZONTE_MAX)
    pagos_fuera = pagos_cal[pagos_cal['dia'] > pagos_cal['corte'] + delta_max]
    corte_ts = pd.Timestamp(corte_vivo)
    pagos_fuera_ventana = {
        'total_eur': float(pagos_fuera['importe_esperado_eur'].sum()),
        'corte_vivo_eur': float(pagos_fuera.loc[pagos_fuera['corte'] == corte_ts,
                                                'importe_esperado_eur'].sum()),
        'lectura': ('importe esperado de PAGOS con fecha mas alla de corte+90 que se '
                    'descarta del calendario y de los horizontes de producto'),
    }

    en_cobros = set(map(tuple, cobros_hor[['company_id', 'corte']].drop_duplicates().values))
    en_pagos = set(map(tuple, pagos_hor[['company_id', 'corte']].drop_duplicates().values))

    def _estado(clave, presente):
        if presente:
            return ESTADOS_LADO[0]
        return ESTADOS_LADO[1] if clave[0] in censo_panel else ESTADOS_LADO[2]

    estado_cobros = {k: _estado(k, k in en_cobros)
                     for k in set(en_cobros).union(en_pagos)}
    estado_pagos = {k: _estado(k, k in en_pagos)
                    for k in set(en_cobros).union(en_pagos)}

    agregado = construir_horizontes_union(cobros_hor, pagos_hor, panel, censo_panel)
    calendario = construir_calendario_neto(cobros_cal, pagos_cal, estado_cobros,
                                           estado_pagos, saldo_lookup)
    forecast_vivo = construir_forecast_vivo(agregado, calendario, corte_vivo)

    frame_arnes, arnes_info = construir_frame_arnes(panel, evaluable)
    evaluacion = evaluar(frame_arnes, agregado)
    frame = evaluacion['frame']
    calibracion = {
        'forecast_union': _calibracion(frame, 'union_flujo_eur', 'forecast_union'),
        'B1_persistencia': _calibracion(frame, 'B1_persistencia_flujo_eur', 'B1_persistencia'),
        'B3_mediana_global': _calibracion(frame, 'B3_mediana_global_flujo_eur',
                                          'B3_mediana_global'),
    }
    calibracion_informada = {
        'forecast_union': _calibracion(frame, 'union_flujo_eur', 'forecast_union',
                                       solo_informados=True),
        'B3_mediana_global': _calibracion(frame, 'B3_mediana_global_flujo_eur',
                                          'B3_mediana_global', solo_informados=True),
    }
    signo = calcular_signo(frame)

    # Verificacion de consistencia: el calendario neto trunco a h debe reproducir
    # el agregado por horizonte.
    chequeo_diario = _cross_check_calendario(agregado, calendario)

    cobertura = resumen_cobertura(agregado)
    vivo = resumen_corte_vivo(agregado, calendario, corte_vivo)

    b3_col = frame['B3_mediana_global_flujo_eur']
    no_nulas = int(b3_col.notna().sum())
    b3_cero = int((b3_col.abs() < 1e-9).sum())
    diagnostico_b3 = {
        'B3_predicciones_no_nulas': no_nulas,
        'B3_predicciones_igual_a_cero': b3_cero,
        'B3_pct_cero_sobre_no_nulas': round(100.0 * b3_cero / no_nulas, 2) if no_nulas else None,
        'lectura': ('B3 (mediana cross-empresa del flujo acumulado) es casi siempre 0 '
                    'porque la mediana de la distribucion de flujos acumulados esta '
                    'centrada en cero: en la practica es el baseline "predice cero".'),
    }

    report = {
        'version': VERSION,
        'generador': GENERADOR,
        'comando_reproduccion': (
            'PYTHONPATH=. .venv/bin/python -B -m xray.cashflow_projection '
            '--cobros-dir reports/collection_schedule '
            '--pagos-dir reports/payment_schedule '
            '--panel reports/daily_flows/panel_diario.parquet '
            '--censo-panel reports/daily_flows/censo.json '
            '--output-dir reports/cashflow_projection'),
        'entradas': {
            'cobros_calendario': _input_info(Path(cobros_dir) / 'calendario.parquet'),
            'cobros_horizontes': _input_info(Path(cobros_dir) / 'horizontes.parquet'),
            'cobros_censo': _input_info(Path(cobros_dir) / 'censo.json'),
            'pagos_calendario': _input_info(Path(pagos_dir) / 'calendario.parquet'),
            'pagos_horizontes': _input_info(Path(pagos_dir) / 'horizontes.parquet'),
            'pagos_censo': _input_info(Path(pagos_dir) / 'censo.json'),
            'panel_diario': _input_info(panel_path),
            'censo_panel': _input_info(censo_panel_path),
            'assessments_v4': _input_info(assessments_path),
        },
        'versiones_lados': versiones,
        'contrato': {
            'objetivo': 'proyeccion de caja inducida por facturas (flujo neto y saldo)',
            'horizontes_dias': list(HORIZONTES),
            'intervalo_de_target': ('el horizonte acumula (corte, corte+h]: el dia del '
                                    'corte queda excluido y el dia corte+h incluido; mismo '
                                    'convenio que xray/cashflow_forecast.py'),
            'convenio_signo': CONVENIO_SIGNO,
            'banda_neutra': {
                'fraccion': float(banda_fraccion),
                'formula': 'banda = fraccion * (entradas_esperadas + |salidas_esperadas|)',
                'justificacion': JUSTIFICACION_BANDA,
            },
            'baselines': {
                'B1_persistencia': 'flujo neto de los h dias previos al corte repetido',
                'B3_mediana_global': ('mediana cross-empresa del flujo acumulado h con '
                                      'origenes anteriores al corte; en la practica '
                                      '"predice cero"'),
            },
        },
        'reproduccion_referencias_corte_vivo': referencias,
        'pagos_fuera_de_ventana_90d': pagos_fuera_ventana,
        'insumos_copiados': {
            grupo: [_input_info(paths.ROOT / ruta) for ruta in rutas]
            for grupo, rutas in INSUMOS_COPIADOS.items()},
        'nota_insumos_copiados': (
            'Copiados tal cual, sin editar. El arnes xray/cashflow_forecast.py se '
            'importa (nunca se reimplementa ni se edita) y sus artefactos y tests '
            'viajan con el por indicacion del coordinador.'),
        'diagnostico_B3': diagnostico_b3,
        'cobertura': cobertura,
        'corte_vivo': vivo,
        'consistencia_calendario_vs_horizontes': chequeo_diario,
        'evaluacion': {
            'arnes': {
                'cortes_por_horizonte': {k: len(v) for k, v in
                                         arnes_info['cortes']['_dates'].items()},
                'interpretacion': ('el arnes xray/cashflow_forecast.py se importa sin '
                                   'editar; sus cortes, convenio y targets son la verdad '
                                   'observada'),
            },
            'comparativa': evaluacion['comparativa'],
            'comparativa_informada': evaluacion['comparativa_informada'],
            'calibracion_agregada': calibracion,
            'calibracion_agregada_informada': calibracion_informada,
            'correlaciones': evaluacion['correlaciones'],
            'outliers_observado': evaluacion['outliers_observado'],
            'signo': signo,
        },
        'tendencia': {
            'definicion': JUSTIFICACION_BANDA,
            'distribucion_corte_vivo': vivo['totales_por_horizonte'],
        },
        'limitaciones': LIMITACIONES,
    }
    return report, agregado, calendario, forecast_vivo, frame


def _cross_check_calendario(agregado: pd.DataFrame, calendario: pd.DataFrame) -> dict:
    """Comprueba que el calendario diario reproduce las entradas/salidas por h.

    Diferencias esperadas y declaradas: el calendario de PAGOS trae marcas de
    tiempo sub-diarias y su modulo de horizontes acumula con cota superior
    estricta de timestamp, de modo que la parte intra-dia del dia frontera
    (corte+h) queda fuera de sus horizontes. Al llevar el calendario a grano de
    dia esa parte SI entra, asi que la suma diaria puede ser algo mayor en valor
    absoluto. Se publica la discrepancia en vez de esconderla.
    """
    if calendario.empty or agregado.empty:
        return {'max_abs_diff_eur': 0.0, 'ok': True, 'por_horizonte': []}
    diario = calendario.copy()
    sumas = []
    for horizonte in HORIZONTES:
        delta = pd.Timedelta(days=horizonte)
        sub = diario[diario['dia'] <= diario['corte'] + delta]
        agg = sub.groupby(['company_id', 'corte']).agg(
            entrada=('entrada_esperada_eur', 'sum'),
            salida=('salida_esperada_eur', 'sum')).reset_index()
        agg['h'] = horizonte
        sumas.append(agg)
    diario_h = pd.concat(sumas, ignore_index=True)
    comparado = agregado.merge(diario_h, on=['company_id', 'corte', 'h'], how='inner',
                               suffixes=('', '_dia'))
    detalle = []
    for horizonte in HORIZONTES:
        sub = comparado[comparado['h'] == horizonte]
        dif_entrada = (sub['entrada_esperada_eur'] - sub['entrada']).abs().max()
        dif_salida = (sub['salida_esperada_eur'] - sub['salida']).abs().max()
        total_ag = float(sub['salida_esperada_eur'].sum()) + float(sub['entrada_esperada_eur'].sum())
        total_dia = float(sub['salida'].sum()) + float(sub['entrada'].sum())
        detalle.append({
            'h': horizonte,
            'max_abs_diff_entrada_eur': float(dif_entrada),
            'max_abs_diff_salida_eur': float(dif_salida),
            'total_agregado_eur': total_ag,
            'total_calendario_dia_eur': total_dia,
            'diff_total_eur': total_dia - total_ag,
            'diff_total_rel': (abs(total_dia - total_ag) / abs(total_ag)) if total_ag else None,
        })
    maximo = max(item['max_abs_diff_entrada_eur'] + item['max_abs_diff_salida_eur']
                 for item in detalle)
    peor_rel = max((item['diff_total_rel'] or 0.0) for item in detalle)
    return {
        'max_abs_diff_eur': float(maximo),
        'peor_diff_total_rel': float(peor_rel),
        'tol_rel_declarada': 0.01,
        'ok': peor_rel < 0.01,
        'n_comparados': int(len(comparado)),
        'por_horizonte': detalle,
        'lectura': ('El agregado por horizonte usa los horizontes de cada lado ' 
                    '(autoridad para reproducir las referencias del encargo). El ' 
                    'calendario diario se lleva a grano de dia; en PAGOS esto ' 
                    'incluye la parte intra-dia del dia frontera que sus horizontes ' 
                    'excluyen, de ahi una discrepancia pequena (declarada).'),
    }


# ---------------------------------------------------------------------------
# 8. Serializacion y markdown
# ---------------------------------------------------------------------------
def report_paths(output_dir=None) -> dict:
    output_dir = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
    return {
        'output_dir': output_dir,
        'calendario': output_dir / 'calendario_neto.parquet',
        'horizontes': output_dir / 'horizontes.parquet',
        'forecast_vivo': output_dir / 'forecast_vivo.parquet',
        'metricas': output_dir / 'metricas.json',
        'report': output_dir / 'report.md',
    }


def _canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      default=str, indent=1)


def write_outputs(report, agregado, calendario, forecast_vivo, output_dir=None) -> dict:
    paths_out = report_paths(output_dir)
    paths_out['output_dir'].mkdir(parents=True, exist_ok=True)
    calendario.to_parquet(paths_out['calendario'], index=False)
    agregado.to_parquet(paths_out['horizontes'], index=False)
    forecast_vivo.to_parquet(paths_out['forecast_vivo'], index=False)
    paths_out['metricas'].write_text(_canonical_json(report), encoding='utf-8')
    paths_out['report'].write_text(render_markdown(report), encoding='utf-8')
    return paths_out


def check_outputs(report, agregado, calendario, forecast_vivo, output_dir=None) -> bool:
    paths_out = report_paths(output_dir)
    try:
        leido = pd.read_parquet(paths_out['calendario'])
        publicado = pd.read_parquet(paths_out['horizontes'])
        vivo = pd.read_parquet(paths_out['forecast_vivo'])
        metricas = json.loads(paths_out['metricas'].read_text(encoding='utf-8'))
    except (FileNotFoundError, OSError):
        return False
    if _canonical_json(metricas) != _canonical_json(json.loads(_canonical_json(report))):
        return False
    for columna in ('company_id', 'corte', 'dia', 'neto_dia_eur'):
        if not _serie_igual(leido[columna], calendario[columna]):
            return False
    for columna in ('company_id', 'corte', 'h', 'flujo_neto_esperado_eur'):
        if not _serie_igual(publicado[columna], agregado[columna]):
            return False
    for columna in ('company_id', 'h', 'flujo_neto_esperado_eur'):
        if not _serie_igual(vivo[columna], forecast_vivo[columna]):
            return False
    return True


def _fmt_millones(value, digits=1):
    if value is None:
        return 'n/a'
    return f'{value / 1e6:,.{digits}f}'


def _fmt(value, digits=2):
    if value is None:
        return 'n/a'
    return f'{value:,.{digits}f}'


def _tabla_metricas(lines, comparativa, objetivo):
    lines.append('| modelo | h | n | MAE EUR | RMSE EUR | mediana abs EUR | p75 abs EUR | sesgo EUR |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|---:|')
    for horizonte in ('30', '60', '90'):
        for nombre in [*BASELINES, 'union']:
            item = comparativa[objetivo][horizonte][nombre]
            lines.append(
                f"| {nombre} | {horizonte} | {item['n']:,} | "
                f"{_fmt(item['mae_eur'])} | {_fmt(item['rmse_eur'])} | "
                f"{_fmt(item['mediana_error_abs_eur'])} | {_fmt(item['error_abs_p75_eur'])} | "
                f"{_fmt(item['sesgo_eur'])} |")
    lines.append('')


def _tabla_calibracion(lines, calibracion):
    lines.append('| modelo | h | n | predicho (M EUR) | observado (M EUR) | ratio pred/obs | sesgo (M EUR) |')
    lines.append('|---|---:|---:|---:|---:|---:|---:|')
    for nombre, bloque in calibracion.items():
        for horizonte in ('30', '60', '90'):
            item = bloque['por_horizonte'][horizonte]
            lines.append(
                f"| {nombre} | {horizonte} | {item['n']:,} | "
                f"{_fmt_millones(item['predicho_eur'])} | {_fmt_millones(item['observado_eur'])} | "
                f"{_fmt(item['ratio_predicho_observado'], 3)} | {_fmt_millones(item['sesgo_eur'])} |")
    lines.append('')


def _tabla_confusion(lines, bloque):
    lines.append('| predicha \\ observada | positiva | negativa | neutra |')
    lines.append('|---|---:|---:|---:|')
    for predicha, fila in bloque['matriz_confusion_predicha_filas_observada_columnas'].items():
        lines.append(f"| {predicha} | {fila['positiva']:,} | {fila['negativa']:,} | {fila['neutra']:,} |")
    lines.append('')


def render_markdown(report: dict) -> str:
    vivo = report['corte_vivo']
    lineas = []
    a = lineas.append
    a('# Proyeccion de caja por empresa (union cobros + pagos, 30/60/90 dias)')
    a('')
    a(f"Generado por `{report['generador']}` (version `{report['version']}`). Reproducible con:")
    a('')
    a('```sh')
    a(report['comando_reproduccion'])
    a('```')
    a('')
    a('## 0. Que es esta pieza y de donde viene')
    a('')
    a('Un primer modelo extrapolaba la serie de flujo diario y fracaso: el mejor baseline')
    a('resulto ser literalmente "predice cero" (`reports/cashflow_forecast/report.md`).')
    a('Esta pieza cambia la fuente de senal: lee el dinero YA COMPROMETIDO en la cartera de')
    a('facturas. Une el lado de COBROS (importes positivos) y el de PAGOS (importes')
    a('negativos) con una SUMA DIRECTA, sin re-firmar ningun lado, y los mide.')
    a('')
    a(f"- **Convenio de signo**: {report['contrato']['convenio_signo']}")
    a(f"- **Intervalo**: {report['contrato']['intervalo_de_target']}.")
    a(f"- **Banda neutra**: {report['contrato']['banda_neutra']['formula']} con fraccion "
      f"{report['contrato']['banda_neutra']['fraccion']:.2f}.")
    a('')
    a('## 1. Reproduccion de las cifras de referencia del corte vivo 2026-08-31')
    a('')
    ref = report['reproduccion_referencias_corte_vivo']
    a('| lado | h=30 (M EUR) | h=60 (M EUR) | h=90 (M EUR) | empresas con calendario |')
    a('|---|---:|---:|---:|---:|')
    for lado in ('cobros', 'pagos'):
        fila = ref[lado]
        a(f"| {lado} | {fila['por_h_m_eur']['30']:,.1f} | {fila['por_h_m_eur']['60']:,.1f} | "
          f"{fila['por_h_m_eur']['90']:,.1f} | {fila['empresas_calendario']:,} |")
    a('')
    a('Referencias del encargo: cobros +263.6 / +309.7 / +321.1 M EUR (654 empresas);')
    a('pagos -356.3 / -417.8 / -439.9 M EUR (737 empresas). Coinciden.')
    a('')
    a('## 2. Forecast del corte vivo 2026-08-31')
    a('')
    a('| h | empresas | entradas (M EUR) | salidas (M EUR) | NETO (M EUR) | tendencia pos/neu/neg |')
    a('|---:|---:|---:|---:|---:|---|')
    for horizonte in ('30', '60', '90'):
        t = vivo['totales_por_horizonte'][horizonte]
        tend = t['tendencia']
        a(f"| {horizonte} | {t['n_empresas']:,} | {_fmt_millones(t['entradas_esperadas_eur'])} | "
          f"{_fmt_millones(t['salidas_esperadas_eur'])} | {_fmt_millones(t['flujo_neto_esperado_eur'])} | "
          f"{tend['positiva']:,} / {tend['neutra']:,} / {tend['negativa']:,} |")
    a('')
    cobertura = vivo['cobertura']
    a(f"- Universo del corte vivo: **{cobertura['n_empresas_universo']:,} empresas**.")
    a(f"- Cobertura de lados: ambos {cobertura['cobertura_lado']['ambos']:,}; "
      f"solo cobros {cobertura['cobertura_lado']['solo_cobros']:,}; "
      f"solo pagos {cobertura['cobertura_lado']['solo_pagos']:,}; "
      f"ninguno {cobertura['cobertura_lado']['ninguno']:,}.")
    a(f"- Estado del lado de cobros: {cobertura['estado_cobros']}.")
    a(f"- Estado del lado de pagos: {cobertura['estado_pagos']}.")
    a(f"- Estado del saldo del corte: {cobertura['estado_saldo']}.")
    fuera = report['pagos_fuera_de_ventana_90d']
    a(f"- Importe esperado de PAGOS descartado por caer mas alla de corte+90: "
      f"{_fmt_millones(fuera['total_eur'])} M EUR en total ({_fmt_millones(fuera['corte_vivo_eur'])} M EUR "
      f"en el corte vivo).")
    a('')
    cruces = vivo['cruces_saldo_negativo_90d']
    a('### Cruces a saldo negativo dentro de 90 dias')
    a('')
    a(f"- Empresas que cruzan a saldo negativo: **{cruces['n_empresas_cruzan']:,}**.")
    a(f"- Dia mediano del cruce: **{_fmt(cruces['dia_mediano_dias_desde_corte'], 1)} dias** "
      f"desde el corte ({cruces['dia_mediano_fecha']}); p25 "
      f"{_fmt(cruces['dia_p25_dias'], 1)} d, p75 {_fmt(cruces['dia_p75_dias'], 1)} d.")
    a(f"- Empresas ya negativas en el saldo del corte: "
      f"{cruces['n_empresas_ya_negativas_en_el_corte']:,}.")
    a('')
    a('AVISO: la mediana del dia de cruce esta INFLADA por el pico artificial del dia')
    a('corte+1. Ambos lados colapsan a `>= corte+1` toda factura vencida cuyo pago esperado')
    a('ya paso (regla 3 de las tareas hermanas), de modo que el primer dia del calendario')
    a('concentra la mora vencida. El numero de empresas que cruzan (118) si es robusto; el')
    a('dia mediano (1) NO debe leerse como estacionalidad, solo como "casi de inmediato".')
    a('')
    a('## 3. Evaluacion walk-forward contra la verdad observada')
    a('')
    ev = report['evaluacion']
    a(f"El arnes `xray/cashflow_forecast.py` se importa sin editar. Cortes por horizonte: "
      f"{ev['arnes']['cortes_por_horizonte']}. La comparativa usa la MISMA poblacion comun "
      f"(trios donde los tres modelos Y el target existen).")
    a('')
    a(f"`diagnostico_B3`: {report['diagnostico_B3']['B3_pct_cero_sobre_no_nulas']}% de las "
      f"predicciones de B3 son exactamente 0 ({report['diagnostico_B3']['B3_predicciones_igual_a_cero']:,} "
      f"de {report['diagnostico_B3']['B3_predicciones_no_nulas']:,}). B3 ES el baseline "
      f"\"predice cero\".")
    a('')
    a('### 3.1 Flujo neto acumulado: MAE y RMSE')
    a('')
    _tabla_metricas(lineas, ev['comparativa'], 'flujo_neto_acumulado')
    a('### 3.2 Saldo proyectado: MAE y RMSE')
    a('')
    _tabla_metricas(lineas, ev['comparativa'], 'saldo_proyectado')
    a('### 3.3 Calibracion agregada (suma del forecast vs suma observada)')
    a('')
    _tabla_calibracion(lineas, ev['calibracion_agregada'])
    a('')
    a('El agregado observado esta DOMINADO por unos pocos valores extremos no')
    a('factureros. Los 10 mayores |observado| de la poblacion comun y lo que predice la')
    a('union:')
    a('')
    a('| empresa | corte | h | observado (M EUR) | union (M EUR) |')
    a('|---|---|---:|---:|---:|')
    for fila in ev['outliers_observado']:
        a(f"| {fila['company_id']} | {fila['corte']} | {fila['h']} | "
          f"{_fmt_millones(fila['observado_eur'])} | {_fmt_millones(fila['union_flujo_eur'])} |")
    a('')
    a('### 3.4 Poblacion informada y correlacion')
    a('')
    a('La union solo emite un flujo distinto de cero para empresas con facturas vivas.')
    a('En esa subpoblacion informada la comparativa se repite y se anade la correlacion:')
    a('')
    _tabla_metricas(lineas, ev['comparativa_informada'], 'flujo_neto_acumulado')
    _tabla_calibracion(lineas, ev['calibracion_agregada_informada'])
    a('| poblacion | h | n | pearson | spearman |')
    a('|---|---:|---:|---:|---:|')
    for poblacion, bloques in ev['correlaciones'].items():
        for horizonte in ('30', '60', '90'):
            item = bloques[horizonte]
            a(f"| {poblacion} | {horizonte} | {item['n']:,} | "
              f"{_fmt(item['pearson'], 4)} | {_fmt(item['spearman'], 4)} |")
    a('')
    a('### 3.5 Acierto de signo del flujo neto')
    a('')
    for horizonte in ('30', '60', '90'):
        bloque = ev['signo']['por_horizonte'][horizonte]
        tend = bloque['tendencia_con_banda']
        estricto = bloque['signo_estricto']
        a(f"**h = {horizonte}** (n = {tend['n']:,})")
        a('')
        a(f"- Tendencia con banda: acierto {_fmt(tend['acierto'], 4)} frente a trivial de la "
          f"clase mayoritaria ({tend['clase_mayoritaria_observada']}) "
          f"{_fmt(tend['trivial_clase_mayoritaria'], 4)}; supera trivial: "
          f"{'SI' if tend['supera_trivial'] else 'NO'}.")
        a(f"- Signo estricto (sin banda): acierto {_fmt(estricto['acierto'], 4)} frente a trivial "
          f"{_fmt(estricto['trivial_clase_mayoritaria'], 4)}; supera trivial: "
          f"{'SI' if estricto['supera_trivial'] else 'NO'}.")
        a('- Matriz de confusion (tendencia con banda):')
        a('')
        _tabla_confusion(lineas, tend)
    a('## 4. Cobertura por corte')
    a('')
    a('| corte | empresas | ambos | solo cobros | solo pagos | ninguno | con saldo corte |')
    a('|---|---:|---:|---:|---:|---:|---:|')
    for fila in report['cobertura']['por_corte']:
        cb = fila['cobertura_lado']
        a(f"| {fila['corte']} | {fila['n_empresas']:,} | {cb['ambos']:,} | {cb['solo_cobros']:,} | "
          f"{cb['solo_pagos']:,} | {cb['ninguno']:,} | {fila['estado_saldo']['con_saldo_corte']:,} |")
    a('')
    a('## 5. Insumos copiados de los worktrees hermanos')
    a('')
    a('Copiados **tal cual, sin editar**; el arnes se importa y no se reimplementa.')
    a('')
    for grupo, lista in report['insumos_copiados'].items():
        a(f"- **{grupo}**: " + ', '.join(f"`{item['path']}`" for item in lista))
    a('')
    a(report['nota_insumos_copiados'])
    a('')
    a('## 6. Limitaciones declaradas')
    a('')
    for limitacion in report['limitaciones']:
        a(f'- {limitacion}')
    a('')
    a('## 7. Lectura honesta')
    a('')
    for lectura in _lectura(report):
        a(f'- {lectura}')
    a('')
    return '\n'.join(lineas) + '\n'


def _lectura(report: dict) -> list:
    ev = report['evaluacion']
    lecturas = []
    for horizonte in ('30', '60', '90'):
        flujo = ev['comparativa']['flujo_neto_acumulado'][horizonte]
        ganador_mae = min(flujo, key=lambda n: flujo[n]['mae_eur'])
        ganador_rmse = min(flujo, key=lambda n: flujo[n]['rmse_eur'])
        cal = ev['calibracion_agregada']
        ratio_union = cal['forecast_union']['por_horizonte'][horizonte]['ratio_predicho_observado']
        ratio_b3 = cal['B3_mediana_global']['por_horizonte'][horizonte]['ratio_predicho_observado']
        corr = ev['correlaciones']['poblacion_comun'][horizonte]
        lecturas.append(
            f'h={horizonte}: mejor MAE **{ganador_mae}**, mejor RMSE **{ganador_rmse}**. '
            f'Calibracion agregada ratio pred/obs: union {_fmt(ratio_union, 3)} vs B3 '
            f'{_fmt(ratio_b3, 3)}. Correlacion union-target (pearson/spearman): '
            f"{_fmt(corr['pearson'], 3)} / {_fmt(corr['spearman'], 3)}.")
    lecturas.append(
        'RESULTADO PRINCIPAL, SIN MAQUILLAR: el forecast union PIERDE frente a "predice '
        'cero" (B3) en MAE Y en RMSE en los tres horizontes, y NO gana la calibracion '
        'agregada: su ratio predicho/observado es erratico y de signo opuesto al observado '
        'en varios cortes, y su sesgo agregado es mayor en valor absoluto que el de B3 '
        '(que predice 0). La correlacion con el target observado es ~0 y el acierto de '
        'signo (~0.31-0.35) queda por debajo de la regla trivial de la clase mayoritaria. '
        'Esto ocurre tambien en la subpoblacion "informada" (empresas con facturas vivas).')
    lecturas.append(
        'POR QUE: el target observado del arnes es el flujo neto TOTAL del panel de '
        'transacciones, no el flujo facturero. Esta dominado por (i) movimientos no '
        'factureros (transferencias, financiacion, no economicos) de magnitud bruta enorme '
        'y (ii) unos pocos valores extremos de una sola empresa-corte (p.ej. +999.2 M EUR '
        'o +3,998.6 M EUR en 30/60 dias) para empresas que la union predice ~0 porque no '
        'tienen facturas vivas. La union mide dinero COMPROMETIDO en facturas; el target '
        'mide toda la caja. Son objetos distintos y la comparacion es desfavorable por '
        'construccion del target, no por un error de signo del pipeline.')
    lecturas.append(
        'VALOR REAL DE LA PIEZA: no es un predictor del flujo total de caja. Es un '
        'CALENDARIO DE CAJA COMPROMETIDA en facturas, point-in-time, con signo correcto, '
        'desglose entradas/salidas, dia de movimiento y cruce a saldo negativo. Sirve para '
        'saber CUANDO y CUANTO dinero facturero se mueve y quien se queda sin colchon, no '
        'para acertar la caja total. El MAE, por ser L1, lo gana la mediana (cero) casi por '
        'construccion matematica; ninguna metrica pedida favorece aqui a la esperanza.')
    lecturas.append(
        'HALLAZGO INCOMODO ADICIONAL: los horizontes publicados por las tareas hermanas '
        'excluyen parte del dia frontera corte+h (cobros por retraso fraccionario; pagos '
        'por marcas de tiempo sub-diarias) mientras su calendario si lo incluye. La union '
        'reproduce las referencias con los horizontes (convenio timestamp) y construye el '
        'calendario a grano de dia; la discrepancia queda medida y publicada.')
    return lecturas


def summary(report: dict) -> dict:
    vivo = report['corte_vivo']
    return {
        'version': report['version'],
        'corte_vivo': vivo['corte'],
        'neto_30_60_90_m_eur': {
            h: round(vivo['totales_por_horizonte'][h]['flujo_neto_esperado_eur'] / 1e6, 1)
            for h in ('30', '60', '90')},
        'cobertura_vivo': vivo['cobertura']['cobertura_lado'],
        'cruces_negativos_90d': vivo['cruces_saldo_negativo_90d'],
        'consistencia_calendario': report['consistencia_calendario_vs_horizontes']['ok'],
    }


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------
def run(cobros_dir=None, pagos_dir=None, panel_path=None, censo_panel_path=None,
        assessments_path=None, output_dir=None, banda_fraccion=BANDA_NEUTRA_FRACCION_DEFECTO,
        write=True):
    report, agregado, calendario, forecast_vivo, frame = build_report(
        cobros_dir or DEFAULT_COBROS_DIR, pagos_dir or DEFAULT_PAGOS_DIR,
        panel_path, censo_panel_path, assessments_path, banda_fraccion)
    if write:
        write_outputs(report, agregado, calendario, forecast_vivo, output_dir)
    return report, agregado, calendario, forecast_vivo, frame


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Proyeccion de caja por empresa: union de cobros y pagos (30/60/90 d)')
    parser.add_argument('--cobros-dir', type=Path, default=DEFAULT_COBROS_DIR)
    parser.add_argument('--pagos-dir', type=Path, default=DEFAULT_PAGOS_DIR)
    parser.add_argument('--panel', type=Path, default=DEFAULT_PANEL)
    parser.add_argument('--censo-panel', type=Path, default=DEFAULT_CENSO_PANEL)
    parser.add_argument('--assessments', type=Path, default=DEFAULT_ASSESSMENTS)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--banda-neutra', type=float, default=BANDA_NEUTRA_FRACCION_DEFECTO,
                        help='fraccion del movimiento bruto que define la banda neutra')
    parser.add_argument('--check', action='store_true',
                        help='no escribe: verifica que la salida publicada reproduce lo generado')
    args = parser.parse_args(argv)
    try:
        report, agregado, calendario, forecast_vivo, _ = build_report(
            args.cobros_dir, args.pagos_dir, args.panel, args.censo_panel,
            args.assessments, args.banda_neutra)
        if args.check:
            if check_outputs(report, agregado, calendario, forecast_vivo, args.output_dir):
                print(json.dumps({'check': 'ok', 'output_dir': str(args.output_dir)}))
                return 0
            print('Error: la salida publicada NO reproduce el informe generado',
                  file=sys.stderr)
            return 1
        write_outputs(report, agregado, calendario, forecast_vivo, args.output_dir)
    except (ValueError, KeyError, FileNotFoundError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    print(json.dumps({**summary(report), 'output': str(args.output_dir)},
                     ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
