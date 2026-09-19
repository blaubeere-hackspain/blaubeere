"""Motor puro de puntuacion healthscore_v4.

Solo biblioteca estandar: sin IO, sin dependencias externas, sin CLI, sin
adaptador. Consume el contrato de entrada cerrado (monthly_rows) y produce un
dict serializable con json.dumps(..., allow_nan=False). Mismo contrato de
pureza que xray/scoring_v3.py, que se lee como referencia; v3 CONVIVE intacto.

Formula v4 (decision cerrada del usuario; no modificar aqui):

  Ventana: la misma de v3 (expansiva hasta 6 meses, luego movil de 6).
  C6 = cobros conocidos en la ventana (igual que v3)
  P6 = pagos operativos conocidos en la ventana (igual que v3)
  D6 = servicio de deuda conocido en la ventana (igual que v3)

  deficit_servicio_6 = SUMA del deficit_servicio_eur de la ventana
      (es un FLUJO mensual: se suma).

  obligacion_vencida_m = obligacion_vencida_eur EN EL CORTE m, NO sumada sobre
      la ventana. CRITICO: la obligacion vencida es un STOCK, no un flujo; una
      misma factura vencida aparece en los 6 cortes de la ventana y sumarla
      seria contarla seis veces. Se toma el valor del corte m una sola vez.

  T6_efectivo = P6 + D6 + deficit_servicio_6 + obligacion_vencida_m
      Lectura: lo que la empresa pago, mas lo que debia haber pagado y no
      pago. Los None se EXCLUYEN de la suma; nulo nunca es cero.

  colchon_v4 = max(0, saldo_reversa_eur en el corte m)
      Cambio respecto de v3: v3 usaba el `colchon` de cash_position (nivel
      menos minimo historico), invariante al ancla y por eso sin lectura de
      liquidez real. Aqui se usa el NIVEL de caja reconstruido en reversa
      (xray/cash_backfill.py). saldo_reversa_eur NULL -> colchon_v4 = 0 con
      motivo registrado; nunca se inventa.

  colchon_aplicable = min(colchon_v4, alpha * T6_efectivo)  (igual que v3)

  R_hist = C_hist / (C_hist + T_hist_efectivo)
      Ratio historica de la PROPIA empresa, igual que v3, pero sobre el
      denominador efectivo nuevo: T_hist_efectivo = suma de (pagos + servicio
      de deuda + deficit de servicio) de TODO el historial + la obligacion
      vencida del corte m (el stock, una sola vez: exactamente la misma
      estructura que la ventana, extendida a todo el historial). La
      obligacion es stock y no se suma mes a mes por el historial.
      Si C_hist + T_hist_efectivo = 0 es indefinida: el termino k se omite y
      se registra el motivo; no se inventa 0.5 ni 50.

  H       = 100 * (C6 + colchon_aplicable + k * R_hist)
                 / (C6 + colchon_aplicable + T6_efectivo + k)
  H_final = H * (1 - beta * mora_indice) * multiplicador_deuda
      Ambos factores SOLO a la baja y acotados: mora_indice y
      multiplicador_deuda estan en [0, 1]; None = sin ajuste, con motivo
      registrado, nunca ajuste a cero. H_final queda siempre en [0, 100] (H
      no puede pasar de 100 por construccion: el colchon aplicable esta
      acotado por alpha*T6 y R_hist <= 1; se acota defensivamente y con
      motivo si un caso limite escapa).

Guardas de no-nota, evaluadas en orden antes de calcular H (sin nota NO se
inventa ningun numero):
  1. Sin actividad de caja en todo el historial -> 'sin_actividad_de_caja_observada'.
  2. c6 = 0 y t6_efectivo = 0 -> 'sin_flujos_observados_en_la_ventana'
     (sustituye al 'denominador_nulo' de v3).
  3. p6 <= 0 Y (obligacion_vencida_m es NULL o 0) -> 'pagos_operativos_no_demostrados'.
     Si P6 = 0 pero hay obligacion vencida > 0, ya NO es un hueco de datos: es
     evidencia positiva de que la empresa debe y no paga; T6_efectivo > 0, la
     formula funciona y la empresa recibe una nota BAJA, no queda sin nota.
     D6 = 0 es legitimo y NO dispara ninguna guarda (una empresa sin deuda
     esta bien).

Confianza: mismo criterio declarado que v3 (la peor confianza_entradas de los
meses activos de la ventana, menos un nivel por deduccion), con DOS caminos
aplicados del fix P6 preservado (reports/fix_p6_preservado/FIX_P6.md):
  - D queda FUERA del cubo de deduccion 'entradas_desconocidas': la deduccion
    solo mira C y P. D desconocido se registra como reason informativo
    'd_eur_desconocido_en_N_meses' sin restar nivel.
  - el saldo_reversa ausente en el corte si deduce nivel ('sin_caja_reconstruida').

La mezcla va en EUROS (numerador y denominador), no promediando notas.
R_hist es de la propia empresa: el motor NUNCA mira otras empresas ni
normaliza entre ellas.

RIESGO DE TRIPLE CONTEO (medido, no ignorado). Las facturas vencidas
impagadas alimentan TRES sitios a la vez: el denominador
(obligacion_vencida_m), el multiplicador_deuda y la mora_indice (lado pago,
viala xray/payment_delay_v2.py). Apilar tres castigos sobre la misma senal
puede ser excesivo; el adaptador mide la correlacion entre los tres canales y
descompone el castigo por canal (ver reports/score_v4/summary.json,
'riesgo_triple_conteo', y reports/score_v4/INFORME.md). La formula NO se
cambia aqui sin decision de producto.

Parametros inyectados (defaults PROVISIONALES pendientes de recalibracion
para v4; el motor funciona con cualquier combinacion no negativa):
  k     = 4178.45 euros virtuales: valor medido en reports/calibration_k/
          report.md sobre la rejilla v3. PENDIENTE DE RECALIBRAR PARA v4.
  alpha = 3.0     saturacion del colchon contra los propios pagos
                  T6_efectivo. PENDIENTE DE CALIBRACION.
  beta  = 0.25    intensidad de la mora: con mora_indice = 1 la nota cae un
                  25% como maximo por ese canal. PENDIENTE DE CALIBRACION.
"""

import math
from calendar import monthrange
from collections.abc import Mapping
from datetime import date

VERSION = 'healthscore_v4'
MONTHS_TARGET = 6
CONFIDENCE_LEVELS = ('ninguna', 'baja', 'media', 'alta')
CONFIDENCE_INPUTS = ('alta', 'media', 'baja', 'ninguna')

DEFAULT_K = 4178.45
DEFAULT_ALPHA = 3.0
DEFAULT_BETA = 0.25

LIMITACIONES = (
    'Parametros k, alpha y beta PROVISIONALES pendientes de recalibracion '
    'para v4; solo k esta medido y sobre la rejilla v3 '
    '(reports/calibration_k/report.md). La escala no es comparable hasta que '
    'se recalibren.',
    'Indice determinista de salud de flujo de caja; no es score oficial, '
    'probabilidad de impago ni salud financiera integral.',
    'Los importes None se tratan como desconocidos, nunca como cero; la nota '
    'se calcula solo con lo conocido y la confianza lo declara.',
    'R_hist es la ratio historica de la propia empresa; el motor no compara '
    'ni normaliza entre empresas.',
    'La obligacion vencida es un STOCK del corte m: entra una sola vez en '
    'T6_efectivo y no se suma sobre la ventana.',
    'La obligacion vencida alimenta tres canales a la vez (denominador, '
    'multiplicador de deuda y mora del lado pago): riesgo de triple conteo '
    'medido en el informe, pendiente de decision de calibracion.',
    'La mora y el multiplicador aplicados son los del mes de corte; no '
    'agregan el historial de la ventana.',
    'El colchon es el NIVEL de caja reconstruido en reversa (saldo_reversa); '
    'el ancla de reconstruccion es posterior a todos los cortes y la '
    'medida point-in-time de las facturas es por vencimiento, no por '
    'disponibilidad de registro certificada (limitacion heredada de las '
    'capas A y C).',
    'El colchon se satura contra T6_efectivo (alpha); la caja por encima de '
    'ese tope no mejora la nota.',
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
    """Importe finito de cualquier signo (nivel de caja) o None."""
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
    """Puntua una empresa con la formula healthscore_v4.

    Contrato de entrada (cerrado):
      monthly_rows: secuencia de dicts, uno por mes cerrado de la empresa, con
        month (date, primer dia del mes), c_eur/p_eur/d_eur (float >= 0 o
        None; None = desconocido, NO cero), tiene_actividad (bool),
        saldo_reversa_eur (float de cualquier signo o None; nivel de caja
        reconstruido del mes), deficit_servicio_eur (float >= 0 o None; FLUJO
        mensual del mes), obligacion_vencida_eur (float >= 0 o None; STOCK
        exigible al cierre del mes), multiplicador_deuda (float en [0,1] o
        None), mora_indice (float en [0,1] o None; None = sin cartera
        observable, NO cero) y confianza_entradas
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
            'saldo_reversa_eur': _amount(item.get('saldo_reversa_eur'),
                                         'saldo_reversa_eur'),
            'deficit_servicio_eur': _eur(item.get('deficit_servicio_eur'),
                                         'deficit_servicio_eur'),
            'obligacion_vencida_eur': _eur(item.get('obligacion_vencida_eur'),
                                           'obligacion_vencida_eur'),
            'multiplicador_deuda': _ratio(item.get('multiplicador_deuda'),
                                          'multiplicador_deuda'),
            'mora_indice': _ratio(item.get('mora_indice'), 'mora_indice'),
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
    deficit_servicio_6 = _sum_field(window_rows, 'deficit_servicio_eur')

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

    # --- Obligacion vencida: STOCK del corte m, una sola vez ---------------
    as_of_month = cutoff.replace(day=1)
    as_of_row = next((row for row in rows if row['month'] == as_of_month), None)
    obligacion_m = (as_of_row['obligacion_vencida_eur']
                    if as_of_row is not None else None)
    if obligacion_m is None:
        reasons.append('obligacion_vencida_desconocida')

    # --- T6 efectivo: pagos + servicio + deficit + stock del corte ---------
    t6_efectivo = p6 + d6 + deficit_servicio_6
    if obligacion_m is not None:
        t6_efectivo += obligacion_m
    # Los None ya quedaron excluidos de cada suma: nulo nunca es cero.

    # --- R_hist: historia propia acumulada hasta as_of inclusive -----------
    c_hist = _sum_field(rows, 'c_eur')
    # Coherente con el nuevo denominador: flujos de todo el historial mas el
    # stock del corte, una sola vez (la misma estructura que la ventana).
    t_hist_efectivo = _sum_field(rows, 'p_eur') + _sum_field(rows, 'd_eur') \
        + _sum_field(rows, 'deficit_servicio_eur')
    if obligacion_m is not None:
        t_hist_efectivo += obligacion_m
    if c_hist + t_hist_efectivo > 0:
        r_hist = c_hist / (c_hist + t_hist_efectivo)
    else:
        r_hist = None
        reasons.append('r_hist_indefinida')

    # --- Colchon: NIVEL de caja reconstruida en el corte m -----------------
    saldo_reversa = as_of_row['saldo_reversa_eur'] if as_of_row is not None else None
    if saldo_reversa is None:
        colchon_v4 = 0.0
        reasons.append('sin_caja_reconstruida')
    elif saldo_reversa < 0:
        # Un saldo negativo no da capacidad de cobertura; no se resta: la
        # penalizacion por quemar caja ya esta en C6/T6.
        colchon_v4 = 0.0
        reasons.append('saldo_reversa_negativo')
    else:
        colchon_v4 = saldo_reversa

    mora = as_of_row['mora_indice'] if as_of_row is not None else None
    if mora is None:
        reasons.append('sin_mora_observable')
    multiplicador = (as_of_row['multiplicador_deuda']
                     if as_of_row is not None else None)
    if multiplicador is None:
        reasons.append('multiplicador_deuda_desconocido')

    colchon_aplicable = 0.0
    cap = alpha * t6_efectivo
    if colchon_v4 > cap:
        colchon_aplicable = cap
        reasons.append('colchon_saturado')
    else:
        colchon_aplicable = colchon_v4

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
    denominador = c6 + colchon_aplicable + t6_efectivo + k_denominador

    health_score = None
    h_antes_de_ajustes = None
    after_mora = None
    after_mult = None
    penalizacion_mora = 0.0
    penalizacion_mult = 0.0
    if not any_activity:
        reasons.insert(0, 'sin_actividad_de_caja_observada')
        if c6 == 0.0 and t6_efectivo == 0.0:
            reasons.append('sin_flujos_observados_en_la_ventana')
    elif c6 == 0.0 and t6_efectivo == 0.0:
        # Nada de flujos observados en la ventana: sin nota. Sustituye al
        # antiguo 'denominador_nulo' de v3, que con la guarda de P6 era
        # inalcanzable.
        reasons.insert(0, 'sin_flujos_observados_en_la_ventana')
    elif p6 <= 0.0 and (obligacion_m is None or obligacion_m <= 0.0):
        # Guarda del fix P6 reaplicada: sin pagos operativos demostrados NO
        # hay nota, salvo que haya obligacion vencida > 0 (entonces la
        # empresa debe y no paga: evidencia positiva, nota BAJA abajo).
        reasons.insert(0, 'pagos_operativos_no_demostrados')
    else:
        h_antes_de_ajustes = 100.0 * numerador / denominador
        after_mora = (h_antes_de_ajustes * (1.0 - beta * mora)
                      if mora is not None else h_antes_de_ajustes)
        penalizacion_mora = h_antes_de_ajustes - after_mora
        after_mult = (after_mora * multiplicador
                      if multiplicador is not None else after_mora)
        penalizacion_mult = after_mora - after_mult
        health_score = after_mult
        if health_score < 0.0 or health_score > 100.0:
            # Defensivo: por construccion no deberia ocurrir (colchon
            # aplicable <= alpha*T6 y ambos factores en [0,1]). Si un caso
            # limite escapa por punto flotante, se acota con motivo.
            reasons.append('nota_acotada_al_rango_0_100')
            health_score = min(100.0, max(0.0, health_score))
        penalizacion_mult = after_mora - health_score

    # --- Confianza: criterio declarado y determinista -----------------------
    # Base: la peor confianza_entradas declarada entre los meses con actividad
    # de la ventana ('ninguna' si no hay actividad en la ventana). Se resta un
    # nivel por cada limitacion conocida: ventana parcial, C o P desconocidos
    # en meses activos, mora no observable y caja reconstruida ausente en el
    # corte. D desconocido NO deduce nivel (reason informativo); la calidad de
    # la capa de obligacion se declara en las capas, no aqui.
    if active_window:
        base_confidence = min((row['confianza_entradas'] for row in active_window),
                              key=CONFIDENCE_LEVELS.index)
    else:
        base_confidence = 'ninguna'
    deducciones = []
    if ventana_parcial:
        deducciones.append('ventana_parcial')
    if 'c_eur' in missing_counts or 'p_eur' in missing_counts:
        deducciones.append('entradas_desconocidas')
    if mora is None:
        deducciones.append('sin_mora_observable')
    if saldo_reversa is None:
        deducciones.append('sin_caja_reconstruida')
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
            'p6': p6,
            'd6': d6,
            'deficit_servicio_6': deficit_servicio_6,
            'obligacion_vencida_m': obligacion_m,
            't6_efectivo': t6_efectivo,
            'r_hist': r_hist,
            'colchon_v4': colchon_v4,
            'colchon_aplicable': colchon_aplicable,
            'saldo_reversa_m': saldo_reversa,
            'mora_indice': mora,
            'multiplicador_deuda': multiplicador,
        },
        'params': {'k': k, 'alpha': alpha, 'beta': beta},
        'adjustments': {
            'h_antes_de_ajustes': h_antes_de_ajustes,
            'after_mora': after_mora,
            'penalizacion_mora_puntos': penalizacion_mora,
            'penalizacion_multiplicador_puntos': penalizacion_mult,
        },
        'reasons': reasons,
        'limitaciones': list(LIMITACIONES),
    }
