"""Motor puro de puntuacion healthscore_v3.

Solo biblioteca estandar: sin IO, sin dependencias externas, sin CLI, sin
adaptador. Consume el contrato de entrada cerrado (monthly_rows) y produce un
dict serializable con json.dumps(..., allow_nan=False).

Formula (decision cerrada del usuario; no modificar aqui):

  Ventana: expansiva hasta 6 meses y luego movil de 6. Para el corte de fin de
  mes m la ventana son los meses cerrados max(primer_mes_observado, m-5) .. m.

  C6 = suma de cobros conocidos en la ventana
  T6 = suma de (pagos + servicio de deuda) conocidos en la ventana
       (los None se EXCLUYEN de la suma: nulo nunca es cero)

  R_hist = C_hist / (C_hist + T_hist)
       ratio acumulada de la PROPIA empresa desde su primer mes observado hasta
       m inclusive. Si C_hist + T_hist = 0 es indefinida: el termino k se omite
       y se registra el motivo; no se inventa 0.5 ni 50. Como el termino k es
       el mecanismo de euros virtuales emparejado con R_hist, omitirse
       equivale a ejecutar con k=0: sin R_hist no hay euros virtuales ni en el
       numerador ni en el denominador.

  colchon_aplicable = min(colchon, alpha * T6)   (saturacion contra los
       propios pagos de la ventana; 0 si el colchon es None o negativo)

  H       = 100 * (C6 + colchon_aplicable + k * R_hist)
                 / (C6 + colchon_aplicable + T6 + k)
  H_final = H * (1 - beta * mora_ratio)          (penalizacion SOLO a la baja;
       mora_ratio None = sin ajuste, con motivo registrado)

Guardas de no-nota, evaluadas en orden antes de calcular H (sin nota NO se
inventa ningun numero):
  1. Sin actividad de caja en todo el historial -> 'sin_actividad_de_caja_observada'.
  2. Denominador de H igual a 0 (nada de flujos observados en la ventana y
     sin colchon ni k) -> 'denominador_nulo'.

La mezcla va en EUROS (numerador y denominador), no promediando notas.
R_hist es de la propia empresa: el motor NUNCA mira otras empresas ni
normaliza entre ellas.

Parametros inyectados (defaults PROVISIONALES pendientes de calibracion
medida; el motor funciona con cualquier combinacion no negativa):
  k     = 1000.0  euros virtuales: con poca evidencia la nota se apoya en la
                  historia propia (k*R_hist domina) y segun entra volumen real
                  k se diluye. 1000 EUR se diluye con un solo mes de flujo
                  tipico y da soporte cuando los flujos observados son
                  pequenos. PENDIENTE DE CALIBRACION.
  alpha = 3.0     saturacion del colchon: la caja cuenta como cobertura de al
                  most alpha meses de los propios pagos T6, para que una
                  empresa con caja grande y negocio parado no puntue 100
                  para siempre. PENDIENTE DE CALIBRACION.
  beta  = 0.25    intensidad de la mora: con mora_ratio = 1 (peor caso) la
                  nota cae un 25% como maximo; mora 0 no sube nada. Es una
                  cota de diseno, no una medida. PENDIENTE DE CALIBRACION.
"""

import math
from calendar import monthrange
from collections.abc import Mapping
from datetime import date

VERSION = 'healthscore_v3'
MONTHS_TARGET = 6
CONFIDENCE_LEVELS = ('ninguna', 'baja', 'media', 'alta')
CONFIDENCE_INPUTS = ('alta', 'media', 'baja', 'ninguna')

DEFAULT_K = 1000.0
DEFAULT_ALPHA = 3.0
DEFAULT_BETA = 0.25

LIMITACIONES = (
    'Parametros k, alpha y beta provisionales pendientes de calibracion '
    'medida; la escala no es comparable hasta que se calibren.',
    'Indice determinista de salud de flujo de caja; no es score oficial, '
    'probabilidad de impago ni salud financiera integral.',
    'Los importes None se tratan como desconocidos, nunca como cero; la nota '
    'se calcula solo con lo conocido y la confianza lo declara.',
    'R_hist es la ratio historica de la propia empresa; el motor no compara '
    'ni normaliza entre empresas.',
    'La mora aplicada es la del mes de corte; no agrega el historial de '
    'retrasos de la ventana.',
    'El colchon se satura contra los pagos de la ventana (alpha * T6); la '
    'caja por encima de ese tope no mejora la nota.',
    'Con el colchon sin saturar, un aumento de pagos puede elevar la nota al '
    'crecer el colchon aplicable; la monotonia estricta frente a pagos solo '
    'esta garantizada con el colchon fijo o saturado.',
    'Los meses sin actividad dentro de la ventana no se imputan; la nota '
    'puede apoyarse en pocos meses efectivos y la confianza lo declara.',
)


def _param(value, name):
    """Parametro inyectado: numero real finito y no negativo."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name}: se esperaba un numero')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f'{name}: debe ser finito y no negativo')
    return result


def _eur(value, field):
    """Importe no negativo o None (desconocido; nulo nunca es cero)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: importe o numero invalido')
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f'{field}: valor negativo o no finito')
    return result


def _amount(value, field):
    """Importe finito de cualquier signo (colchon, salida media) o None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: importe o numero invalido')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{field}: valor no finito')
    return result


def _ratio(value, field):
    """Ratio en [0, 1] o None (sin dato observable; nulo nunca es cero)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field}: se esperaba un numero')
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f'{field}: debe estar entre 0 y 1')
    return result


def _confianza(value):
    if value not in CONFIDENCE_INPUTS:
        raise ValueError("confianza_entradas: debe ser 'alta', 'media', "
                         "'baja' o 'ninguna'")
    return value


def _end_of_month(value):
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ValueError('as_of: se esperaba una fecha ISO valida o date') from None
    if not isinstance(value, date):
        raise ValueError('as_of: se esperaba una fecha ISO valida o date')
    if value.day != monthrange(value.year, value.month)[1]:
        raise ValueError('as_of debe ser el ultimo dia de un mes cerrado')
    return value


def _month_index(value):
    return value.year * 12 + value.month


def _index_month(index):
    return date(index // 12, index % 12 + 1, 1)


def _sum_field(rows, field):
    total = 0.0
    for row in rows:
        value = row[field]
        if value is not None:
            total += value
    return total


def score_company(company_id, group_id, monthly_rows, as_of, *,
                  k=DEFAULT_K, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """Puntua una empresa con la formula healthscore_v3.

    Contrato de entrada (cerrado):
      monthly_rows: secuencia de dicts, uno por mes cerrado de la empresa, con
        month (date, primer dia del mes), c_eur/p_eur/d_eur (float >= 0 o
        None; None = desconocido, NO cero), tiene_actividad (bool),
        colchon_eur (float o None, puede ser negativo),
        salida_media_mensual_eur (float o None), mora_ratio (float en [0,1] o
        None; None = sin cartera observable, NO cero) y confianza_entradas
        ('alta'|'media'|'baja'|'ninguna').
      as_of: date, ultimo dia de un mes cerrado. Las filas con month posterior
        a as_of se ignoran por completo (disciplina point-in-time).
      k, alpha, beta: parametros inyectados, finitos y no negativos.

    Devuelve un dict serializable con json.dumps(..., allow_nan=False).
    No muta monthly_rows.
    """
    cutoff = _end_of_month(as_of)
    k = _param(k, 'k')
    alpha = _param(alpha, 'alpha')
    beta = _param(beta, 'beta')

    try:
        raw_rows = list(monthly_rows)
    except TypeError:
        raise ValueError('monthly_rows debe ser una secuencia de mappings') from None

    cutoff_index = _month_index(cutoff)
    rows = []
    seen_months = set()
    for item in raw_rows:
        if not isinstance(item, Mapping):
            raise ValueError('monthly_rows debe contener mappings')
        month = item.get('month')
        if isinstance(month, str):
            try:
                month = date.fromisoformat(month)
            except ValueError:
                raise ValueError('month: se esperaba una fecha ISO valida o date') from None
        if not isinstance(month, date):
            raise ValueError('month: se esperaba una fecha ISO valida o date')
        if month.day != 1:
            raise ValueError('month: debe ser el primer dia del mes')
        if month > cutoff:
            # Disciplina point-in-time: una fila futura se ignora por
            # completo; ni su contenido ni su validez pueden influir.
            continue
        if month in seen_months:
            raise ValueError(f'mes duplicado en monthly_rows: {month.isoformat()}')
        seen_months.add(month)
        tiene_actividad = item.get('tiene_actividad')
        if type(tiene_actividad) is not bool:
            raise ValueError('tiene_actividad: debe ser booleano')
        rows.append({
            'month': month,
            'c_eur': _eur(item.get('c_eur'), 'c_eur'),
            'p_eur': _eur(item.get('p_eur'), 'p_eur'),
            'd_eur': _eur(item.get('d_eur'), 'd_eur'),
            'tiene_actividad': tiene_actividad,
            'colchon_eur': _amount(item.get('colchon_eur'), 'colchon_eur'),
            'salida_media_mensual_eur': _amount(item.get('salida_media_mensual_eur'),
                                                'salida_media_mensual_eur'),
            'mora_ratio': _ratio(item.get('mora_ratio'), 'mora_ratio'),
            'confianza_entradas': _confianza(item.get('confianza_entradas')),
        })

    reasons = []

    # --- Ventana expansiva hasta 6, luego movil de 6 -----------------------
    any_activity = any(row['tiene_actividad'] for row in rows)
    if rows:
        first_index = min(_month_index(row['month']) for row in rows)
        window_start = max(first_index, cutoff_index - (MONTHS_TARGET - 1))
        n_meses_ventana = cutoff_index - window_start + 1
        window_rows = [row for row in rows if _month_index(row['month']) >= window_start]
    else:
        window_start = None
        n_meses_ventana = 0
        window_rows = []
    ventana_parcial = n_meses_ventana < MONTHS_TARGET
    active_window = [row for row in window_rows if row['tiene_actividad']]

    # --- Sumas de la ventana: solo lo conocido; nulo nunca es cero ---------
    c6 = _sum_field(window_rows, 'c_eur')
    p6 = _sum_field(window_rows, 'p_eur')
    d6 = _sum_field(window_rows, 'd_eur')
    t6 = p6 + d6

    missing_counts = {}
    for field in ('c_eur', 'p_eur', 'd_eur'):
        count = sum(1 for row in active_window if row[field] is None)
        if count:
            missing_counts[field] = count
            reasons.append(f'{field}_desconocido_en_{count}_meses')
    if rows and not active_window:
        reasons.append('sin_actividad_en_ventana')
    if ventana_parcial:
        reasons.append('ventana_parcial')

    # --- R_hist: historia propia acumulada hasta as_of inclusive -----------
    c_hist = _sum_field(rows, 'c_eur')
    t_hist = _sum_field(rows, 'p_eur') + _sum_field(rows, 'd_eur')
    if c_hist + t_hist > 0:
        r_hist = c_hist / (c_hist + t_hist)
    else:
        r_hist = None
        reasons.append('r_hist_indefinida')

    # --- Colchon y mora del mes de corte ------------------------------------
    as_of_month = cutoff.replace(day=1)
    as_of_row = next((row for row in rows if row['month'] == as_of_month), None)
    colchon_bruto = as_of_row['colchon_eur'] if as_of_row is not None else None
    if colchon_bruto is None:
        colchon_aplicable = 0.0
        reasons.append('sin_colchon_estimado')
    elif colchon_bruto < 0:
        # Un colchon negativo no da capacidad de cobertura; no se resta: la
        # penalizacion por quemar caja ya esta en C6/T6.
        colchon_aplicable = 0.0
        reasons.append('colchon_negativo')
    else:
        cap = alpha * t6
        if colchon_bruto > cap:
            colchon_aplicable = cap
            reasons.append('colchon_saturado')
        else:
            colchon_aplicable = colchon_bruto

    mora = as_of_row['mora_ratio'] if as_of_row is not None else None
    if mora is None:
        reasons.append('sin_mora_observable')

    # --- Nota ---------------------------------------------------------------
    if r_hist is None:
        # El termino k se omite; equivale a ejecutar con k=0: sin R_hist no
        # hay euros virtuales, tampoco en el denominador. No se inventa 0.5.
        k_term = 0.0
        k_denominador = 0.0
    else:
        k_term = k * r_hist
        k_denominador = k
    numerador = c6 + colchon_aplicable + k_term
    denominador = c6 + colchon_aplicable + t6 + k_denominador

    if not any_activity:
        health_score = None
        h_antes_de_mora = None
        penalizacion = 0.0
        reasons.insert(0, 'sin_actividad_de_caja_observada')
    elif denominador == 0.0:
        # El denominador de H acabaria en 0: sin nota, con motivo.
        health_score = None
        h_antes_de_mora = None
        penalizacion = 0.0
        reasons.append('denominador_nulo')
    else:
        h_antes_de_mora = 100.0 * numerador / denominador
        if mora is None:
            health_score = h_antes_de_mora
        else:
            health_score = h_antes_de_mora * (1.0 - beta * mora)
        penalizacion = h_antes_de_mora - health_score

    # --- Confianza: criterio declarado y determinista -----------------------
    # Base: la peor confianza_entradas declarada entre los meses con actividad
    # de la ventana ('ninguna' si no hay actividad en la ventana). Se resta un
    # nivel por cada limitacion conocida: ventana parcial, cobros, pagos o
    # servicio de deuda desconocidos en meses activos, mora no observable y
    # colchon no estimado.
    if active_window:
        base_confidence = min((row['confianza_entradas'] for row in active_window),
                              key=CONFIDENCE_LEVELS.index)
    else:
        base_confidence = 'ninguna'
    deducciones = []
    if ventana_parcial:
        deducciones.append('ventana_parcial')
    if 'c_eur' in missing_counts or 'p_eur' in missing_counts \
            or 'd_eur' in missing_counts:
        deducciones.append('entradas_desconocidas')
    if mora is None:
        deducciones.append('sin_mora_observable')
    if colchon_bruto is None:
        deducciones.append('sin_colchon_estimado')
    rank = max(0, CONFIDENCE_LEVELS.index(base_confidence) - len(deducciones))
    confidence = CONFIDENCE_LEVELS[rank]

    return {
        'version': VERSION,
        'company_id': company_id,
        'group_id': group_id,
        'as_of': cutoff.isoformat(),
        'health_score': health_score,
        'confidence': confidence,
        'confidence_detail': {'base': base_confidence, 'deducciones': deducciones},
        'window': {
            'months_target': MONTHS_TARGET,
            'n_meses_ventana': n_meses_ventana,
            'n_meses_con_actividad': len(active_window),
            'ventana_parcial': ventana_parcial,
            'inicio': _index_month(window_start).isoformat() if window_start is not None else None,
            'fin': as_of_month.isoformat(),
        },
        'inputs': {
            'c6': c6,
            't6': t6,
            'p6': p6,
            'd6': d6,
            'r_hist': r_hist,
            'colchon_aplicable': colchon_aplicable,
            'colchon_bruto': colchon_bruto,
            'mora_ratio': mora,
        },
        'params': {'k': k, 'alpha': alpha, 'beta': beta},
        'adjustments': {
            'h_antes_de_mora': h_antes_de_mora,
            'penalizacion_mora_puntos': penalizacion,
        },
        'reasons': reasons,
        'limitaciones': list(LIMITACIONES),
    }
