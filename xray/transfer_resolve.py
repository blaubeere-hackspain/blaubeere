"""Resolucion derivada de transferencias para el scorer v3 (capa de solo lectura).

Esta capa NO modifica la clasificacion de aguas arriba: xray.flows, los marts
publicados y reports/current.json quedan intactos. Produce un informe de
cobertura (EUR recuperado del bucket ambiguo 'transfer') y una etiqueta
derivada por movimiento que el scorer v3 podra consumir despues.

Reglas (en orden, sin solapamiento):
  R1 cash_withdrawal / pos_withdrawal -> operating_out: el dinero sale del
     perimetro observado, no es una transferencia interna.
  R2 emparejamiento voraz entrada/salida de la misma empresa con signo
     opuesto e importe EUR igual dentro de una tolerancia explicita:
       - estricta: product_id distinto y |diferencia de fechas| <= 3 dias.
       - laxa:     cualquier product_id y |diferencia de fechas| <= 7 dias.
     La laxa es cota superior y la estricta cota inferior del volumen
     realmente interno: el emparejamiento por importe y fecha produce falsos
     pares y counterparty_id no esta disponible para confirmarlos.
  R3 clasificacion por descripcion, SOLO sobre lo que sigue ambiguo tras
     R1 y R2 (la evidencia de un par emparejado manda sobre el texto). Tabla
     de patrones explicita y auditable (DESC_PATTERNS, mas abajo), orden
     declarado, primera coincidencia gana y ninguna fila puede ser tocada
     por dos patrones. Familia traspaso -> internal_transfer con confidence
     'media' (es la palabra del banco, no una prueba de titularidad interna:
     eso solo lo demuestra un par R2). DISP.ENTREG.EFECT.* y RETIRADA* ->
     operating_out (el efectivo sale del perimetro observado). STRIPE PAYOUT
     y equivalentes de pasarela -> operating_in (liquidacion de cobros).
     TRANSFERENCIA*/TRANSFER*/TRANSF* -> operativa segun el signo de
     amount_eur con confidence 'baja' (ver analisis de sensibilidad en
     coverage.json). Descripcion vacia o desconocida: sigue ambiguo.
     amount_eur nulo o fx_ambiguous no impide la clase (la categoria es
     evidencia de naturaleza, no de importe); su EUR no suma cuando falta.

Determinismo: el emparejamiento es voraz sobre un orden estable
(abs(amount_eur) DESC, date_ok ASC, transaction_id ASC); el candidato elegido
es el primero en (date_ok ASC, transaction_id ASC). Ningun movimiento se
empareja dos veces y dos ejecuciones dan el mismo resultado.

Tolerancia de importe: 0,01 EUR porque amount_eur es DOUBLE (residuos de
redondeo de la conversion); la comparacion admite un slack de 1e-9 para que
una diferencia de exactamente 0,01 EUR cuente como igual pese al redondeo
binario. No se emparejan filas con fx_ambiguous verdadero
o amount_eur nulo: sin EUR demostrable no hay igualdad demostrable.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from xray import paths

CASH_PRODUCT_TYPES = ('checking', 'saving', 'wallet')
CASH_CATEGORIES = frozenset({'cash_withdrawal', 'pos_withdrawal'})
R1_RULE = 'cash_withdrawal_sale_de_perimetro'
R2_STRICT_RULE = 'par_interno_estricto'
R2_LAX_RULE = 'par_interno_laxo'
R2_STRICT_MAX_DAYS = 3
R2_LAX_MAX_DAYS = 7
AMOUNT_TOLERANCE_EUR = 0.01
RESOLVED_CLASSES = ('operating_in', 'operating_out', 'internal_transfer', 'ambiguo')
R3_TRASPASO_RULE = 'desc:traspaso'
R3_EFECTIVO_RULE = 'desc:efectivo_fuera_de_perimetro'
R3_PASARELA_RULE = 'desc:pasarela_cobro'
R3_TERCERO_RULE = 'desc:transferencia_tercero'
R3_SIGN_CLASS = 'por_signo'

# --------------------------------------------------------------------- R3 ---
# R3.1: tabla de patrones por descripcion. Orden declarado: la primera
# coincidencia gana y ninguna fila puede ser tocada por dos patrones. Cada
# patron se evalua sobre la descripcion normalizada (mayusculas, sin
# acentos, espacios colapsados) y anclado al inicio con re.match.
#
# Derivada de la medicion de las descripciones normalizadas de los 116.304
# residuales (la medicion se publica en coverage.json junto a esta tabla):
# en castellano bancario 'traspaso' es entre cuentas propias y
# 'transferencia' es a un tercero; DISP.ENTREG.EFECT./RETIRADA son entrega o
# retirada de efectivo; STRIPE PAYOUT es liquidacion de una pasarela de
# cobro. Un 'traspaso' sin contraparte observada es la palabra del banco:
# confidence 'media', nunca 'alta'; solo un par R2 esta demostrado.
#
# Cada entrada: (patron, clase, confidence, justificacion en una linea).
# clase='por_signo' resuelve operating_in si amount_eur>0 y operating_out si
# amount_eur<0; sin signo utilizable la fila NO se fuerza y sigue ambiguo.
DESC_PATTERNS = (
    ('^TRANSF INTERNA', 'internal_transfer', 'media',
     'La descripcion declara literalmente transferencia interna entre cuentas propias.'),
    ('^TRASPASO', 'internal_transfer', 'media',
     "'Traspaso' bancario: movimiento entre cuentas del propio titular; "
     'sin contraparte observada es la palabra del banco, no una prueba.'),
    (r'^TRASP\. ', 'internal_transfer', 'media',
     "'TRASP.' (traspaso abreviado, p. ej. apuntes agrupados ORI/DST): entre cuentas propias."),
    ('^SCF-TRASPASO', 'internal_transfer', 'media',
     'SCF-TRASPASO (movimiento de fondos entre cuentas propias del mismo banco).'),
    ('^RETORNO TRASPASO', 'internal_transfer', 'media',
     'Retorno de un traspaso: la contrapartida de barrido de tesoreria vuelve a su cuenta origen.'),
    ('^PAGO TRASPASO', 'internal_transfer', 'media',
     'Pago de traspasos agrupados: movimiento entre cuentas propias.'),
    (r'^\[NUM\] RETORNO TRASPASO', 'internal_transfer', 'media',
     'Igual que RETORNO TRASPASO con una referencia numerica del banco delante; el termino semantico sigue siendo retorno de traspaso.'),
    (r'^\[NUM\] TRASPASO', 'internal_transfer', 'media',
     'Igual que TRASPASO con una referencia numerica del banco delante; el termino semantico sigue siendo traspaso.'),
    (r'^\[NUM\] [0-9]+ 04 TRASP\. CT', 'internal_transfer', 'media',
     'Referencia bancaria + TRASP. CT (traspaso de cuenta) + TRASPASO DESDE CTA en el cuerpo del texto.'),
    (r'^DISP\.ENTREG\.EFECT', 'operating_out', 'media',
     'Disponibilidad/entrega de efectivo: el dinero sale del perimetro observado, igual que R1.'),
    ('^RETIRADA', 'operating_out', 'media',
     'Retirada de fondos: salida de efectivo del perimetro observado, igual que R1.'),
    ('^STRIPE PAYOUT', 'operating_in', 'media',
     'Liquidacion de cobros de clientes de la pasarela Stripe (payout).'),
    ('^VIR SEPA RECU /FRM STRIPE', 'operating_in', 'media',
     'Transferencia SEPA entrante desde la pasarela Stripe: liquidacion de cobros (payout).'),
    ('^TRANSFERENCIA', R3_SIGN_CLASS, 'baja',
     'Transferencia a tercero: economica; la direccion la da el signo de amount_eur.'),
    ('^TRANSFER', R3_SIGN_CLASS, 'baja',
     'Transfer (transferencia a tercero): economica; la direccion la da el signo de amount_eur.'),
    ('^TRANSF', R3_SIGN_CLASS, 'baja',
     'Transf. (transferencia a tercero): economica; la direccion la da el signo de amount_eur.'),
)

PASS_SPECS = (
    (R2_STRICT_RULE, 'alta', R2_STRICT_MAX_DAYS, True),
    (R2_LAX_RULE, 'baja', R2_LAX_MAX_DAYS, False),
)


def _truthy(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in ('true', '1', 't'))


def _has_eur(value):
    return value is not None and not (isinstance(value, float) and value != value)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def normalize_description(value):
    """Normalizacion declarada en R3.1: mayusculas, sin acentos y espacios colapsados."""
    if value is None:
        return ''
    text = unicodedata.normalize('NFKD', str(value).upper())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', text).strip()


def _clean_text(value):
    if value is None:
        return ''
    return str(value).strip()


def _record(row):
    return {
        'id': row['transaction_id'],
        'company': _clean_text(row.get('company_id')),
        'product': _clean_text(row.get('product_id')),
        'date': _as_date(row.get('date_ok')),
        'eur': row.get('amount_eur'),
        'counterparty': _clean_text(row.get('counterparty_id')),
        'category': row.get('category_norm'),
        'fx': _truthy(row.get('fx_ambiguous')),
        'description': row.get('description'),
    }


def _sort_key(record):
    return (-abs(record['eur']), record['date'], record['id'])


def _candidate_key(record):
    return (record['date'], record['id'])


def _pairable(anchor, candidate, max_days, require_distinct_product):
    if anchor['eur'] * candidate['eur'] >= 0:
        return False
    if require_distinct_product and anchor['product'] == candidate['product']:
        return False
    if abs((candidate['date'] - anchor['date']).days) > max_days:
        return False
    if abs(abs(anchor['eur']) - abs(candidate['eur'])) > AMOUNT_TOLERANCE_EUR + 1e-9:
        return False
    if anchor['counterparty'] and candidate['counterparty'] and anchor['counterparty'] != candidate['counterparty']:
        return False
    return True


def resolve_transfers(rows):
    """Resuelve filas del bucket 'transfer' sin IO, sin reloj y sin estado.

    rows: iterable de mappings con al menos transaction_id, company_id,
    product_id, date_ok, amount_eur, fx_ambiguous, category_norm y
    counterparty_id. El perimetro (booked, productos de caja, meses cerrados)
    lo filtra el llamador: esta funcion clasifica lo que recibe.

    Devuelve una lista (en el orden de entrada) de dicts con las claves
    transaction_id, resolved_class, rule, confidence y matched_with.
    No muta su entrada.
    """
    records = [_record(row) for row in rows]
    results = {}
    pending = []
    for record in records:
        if record['category'] in CASH_CATEGORIES:
            results[record['id']] = {
                'transaction_id': record['id'], 'resolved_class': 'operating_out',
                'rule': R1_RULE, 'confidence': 'media', 'matched_with': None,
                'pattern': None,
            }
            continue
        results[record['id']] = {
            'transaction_id': record['id'], 'resolved_class': 'ambiguo',
            'rule': None, 'confidence': 'ninguna', 'matched_with': None,
            'pattern': None,
        }
        if _has_eur(record['eur']) and record['eur'] != 0 and record['date'] is not None \
                and not record['fx'] and record['company']:
            pending.append(record)
    paired = set()
    for rule, confidence, max_days, require_distinct in PASS_SPECS:
        free = [r for r in pending if r['id'] not in paired]
        # Cubetas (empresa, signo) recorridas SIEMPRE en el orden de eleccion
        # declarado (date_ok ASC, transaction_id ASC); los anclajes en el orden
        # voraz declarado. El bisect solo recorta la ventana de fechas, no
        # altera el orden en que se consideran los candidatos: dos ejecuciones
        # dan exactamente el mismo resultado.
        buckets = defaultdict(list)
        for candidate in sorted(free, key=_candidate_key):
            buckets[(candidate['company'], candidate['eur'] > 0)].append(candidate)
        date_keys = {key: [candidate['date'] for candidate in bucket]
                     for key, bucket in buckets.items()}
        for anchor in sorted(free, key=_sort_key):
            if anchor['id'] in paired:
                continue
            key = (anchor['company'], anchor['eur'] < 0)
            bucket = buckets.get(key)
            if not bucket:
                continue
            low = bisect_left(date_keys[key], anchor['date'] - timedelta(days=max_days))
            high = bisect_right(date_keys[key], anchor['date'] + timedelta(days=max_days))
            for candidate in bucket[low:high]:
                if candidate['id'] in paired or candidate['id'] == anchor['id']:
                    continue
                if _pairable(anchor, candidate, max_days, require_distinct):
                    for left, right in ((anchor, candidate), (candidate, anchor)):
                        results[left['id']] = {
                            'transaction_id': left['id'],
                            'resolved_class': 'internal_transfer',
                            'rule': rule, 'confidence': confidence,
                            'matched_with': right['id'], 'pattern': None,
                        }
                    paired.update({anchor['id'], candidate['id']})
                    break
    # ----------------------------------------------------------------- R3 ---
    # Solo lo que sigue ambiguo tras R1 y R2: la evidencia de un par
    # emparejado manda sobre el texto. Primera coincidencia en DESC_PATTERNS
    # gana; una fila no puede ser tocada por dos patrones. 'por_signo' sin
    # signo utilizable no se fuerza: sigue ambiguo.
    for record in records:
        result = results[record['id']]
        if result['resolved_class'] != 'ambiguo':
            continue
        description = normalize_description(record['description'])
        if not description:
            continue
        for patron, clase, confidence, _justificacion in DESC_PATTERNS:
            if not re.match(patron, description):
                continue
            if clase == R3_SIGN_CLASS:
                eur = record['eur']
                if not _has_eur(eur) or eur == 0:
                    result['pattern'] = patron
                    break
                resolved = 'operating_in' if eur > 0 else 'operating_out'
                rule = R3_TERCERO_RULE
            else:
                resolved = clase
                rule = {
                    'internal_transfer': R3_TRASPASO_RULE,
                    'operating_out': R3_EFECTIVO_RULE,
                    'operating_in': R3_PASARELA_RULE,
                }[resolved]
            result.update({
                'resolved_class': resolved, 'rule': rule, 'confidence': confidence,
                'matched_with': None, 'pattern': patron,
            })
            break
    return [results[record['id']] for record in records]


def _paired_ids(results):
    return {key for key, value in results.items() if value['resolved_class'] == 'internal_transfer'}


def _clase_label(clase):
    if clase == R3_SIGN_CLASS:
        return 'operating_in si amount_eur>0; operating_out si amount_eur<0; ambiguo si no hay signo utilizable'
    return clase


def _sensibilidad_tercero(merged, company_months):
    """R3.3: como cambian C (cobros) y P (pagos) del panel de empresa-mes entre
    el escenario (a), las filas desc:transferencia_tercero cuentan como
    operativo, y el escenario (b), se excluyen y siguen ambiguas.

    La salida v2 es el escenario (a); la diferencia frente a (b) se publica
    en EUR y en numero de empresa-mes cuyo C o P cambia de valor.
    """
    tercero = merged[merged['rule'] == R3_TERCERO_RULE]
    delta_c = float(tercero.loc[tercero['amount_eur'] > 0, 'amount_eur'].sum())
    delta_p = float(-tercero.loc[tercero['amount_eur'] < 0, 'amount_eur'].sum())
    afectadas = sum(
        1 for datos in company_months.values()
        if datos['sensibilidad_tercero']['delta_C_escenario_b_eur'] != 0
        or datos['sensibilidad_tercero']['delta_P_escenario_b_eur'] != 0)
    return {
        'filas': int(len(tercero)),
        'escenario_a_cuenta_como_operativo': {
            'delta_C_eur': round(delta_c, 2),
            'delta_P_eur': round(delta_p, 2),
        },
        'escenario_b_excluido_sigue_ambiguo': {
            'delta_C_eur': 0.0,
            'delta_P_eur': 0.0,
        },
        'diferencia_a_menos_b': {
            'delta_C_eur': round(delta_c, 2),
            'delta_P_eur': round(delta_p, 2),
            'empresa_mes_que_cambian_de_estado': afectadas,
            'total_empresa_mes': len(company_months),
        },
        'nota': 'confidence baja: la rubrica del repo dice que las categorias bancarias '
                'son evidencia y no verdad economica; el analisis permite descartar la '
                'regla sin recalcular si el revisor la considera discutible.',
    }


# ---------------------------------------------------------------- coverage ---

def compute_coverage(rows_df, results_df):
    """Informe de cobertura del bucket 'transfer' antes/despues de resolver."""
    import pandas as pd

    base = rows_df[['transaction_id', 'company_id', 'month', 'amount_eur']].copy()
    base['abs_eur'] = base['amount_eur'].abs()
    merged = base.merge(
        results_df[['transaction_id', 'resolved_class', 'rule', 'confidence', 'pattern']],
        on='transaction_id', how='left', validate='one_to_one')
    if merged['resolved_class'].isna().any():
        raise ValueError('resolve_transfers no devolvio una fila por transaction_id')

    def block(frame, mask):
        part = frame[mask]
        return {'rows': int(len(part)), 'eur': round(float(part['abs_eur'].sum()), 2)}

    before = block(merged, merged['resolved_class'].notna())
    before['rows_sin_eur'] = int(base['amount_eur'].isna().sum())
    before['eur_definicion'] = 'suma de abs(amount_eur); filas sin EUR no suman EUR y se cuentan aparte'
    r3_mask = merged['pattern'].notna() & (merged['resolved_class'] != 'ambiguo')
    recovered = {
        'r1_cash_withdrawal': block(merged, merged['rule'] == R1_RULE),
        'r2_estricta': block(merged, merged['rule'] == R2_STRICT_RULE),
        'r2_laxa': block(merged, merged['rule'] == R2_LAX_RULE),
        'r3_descripcion': block(merged, r3_mask),
    }
    recovered['r3_por_patron'] = {
        patron: block(merged, (merged['pattern'] == patron) & (merged['resolved_class'] != 'ambiguo'))
        for patron, _clase, _conf, _just in DESC_PATTERNS
    }
    sin_signo = merged[(merged['pattern'].notna()) & (merged['resolved_class'] == 'ambiguo')]
    recovered['r3_no_forzado_sin_signo'] = {
        'rows': int(len(sin_signo)),
        'eur': round(float(sin_signo['abs_eur'].sum()), 2),
        'definicion': 'filas cuyo patron es por_signo pero carecen de signo utilizable '
                      '(amount_eur nulo o 0): siguen ambiguas, no se fuerza la direccion',
    }
    after = block(merged, merged['resolved_class'] == 'ambiguo')

    grouped = merged.groupby(['company_id', 'month'], dropna=False)
    company_months = {}
    zeroed = 0
    residual = []
    for (company, month), part in grouped:
        before_eur = round(float(part['abs_eur'].sum()), 2)
        after_part = part[part['resolved_class'] == 'ambiguo']
        after_rows = int(len(after_part))
        after_eur = round(float(after_part['abs_eur'].sum()), 2)
        company_months[f'{company}|{pd_period(month)}'] = {
            'ambiguo_antes': {'rows': int(len(part)), 'eur': before_eur},
            'recuperado_r1': block(part, part['rule'] == R1_RULE),
            'recuperado_r2_estricta': block(part, part['rule'] == R2_STRICT_RULE),
            'recuperado_r2_laxa': block(part, part['rule'] == R2_LAX_RULE),
            'recuperado_r3': block(part, part['pattern'].notna() & (part['resolved_class'] != 'ambiguo')),
            'ambiguo_despues': {'rows': after_rows, 'eur': after_eur},
            'sensibilidad_tercero': {
                'delta_C_escenario_b_eur': round(-float(part.loc[(part['rule'] == R3_TERCERO_RULE) & (part['amount_eur'] > 0), 'amount_eur'].sum()), 2),
                'delta_P_escenario_b_eur': round(float(part.loc[(part['rule'] == R3_TERCERO_RULE) & (part['amount_eur'] < 0), 'amount_eur'].sum()), 2),
            },
        }
        if after_rows == 0:
            zeroed += 1
        if before_eur > 0:
            residual.append(after_eur * 100.0 / before_eur)
    residual_series = pd.Series(sorted(residual))
    if 'product_currency' in rows_df.columns:
        product_join = rows_df['product_currency'].notna().mean() * 100.0
    else:
        product_join = None
    return {
        'scope': {
            'descripcion': "filas is_booked, product_type en ('checking','saving','wallet'), "
                           "meses cerrados (NOT is_open_month) y flow_class='transfer'",
            'companies': int(rows_df['company_id'].nunique()),
            'months': int(rows_df['month'].nunique()),
            'rows': before['rows'],
            'product_in_banking_products_pct': round(product_join, 2) if product_join is not None else None,
            'notas': ["Solo se trabaja el bucket 'transfer'; las filas flow_class='unknown' "
                      "quedan fuera de esta capa y siguen ambiguas aguas arriba.",
                      'R1 aplica tambien a filas con amount_eur nulo (la categoria es evidencia '
                      'suficiente de salida del perimetro); su EUR solo suma filas con EUR.',
                      'R3 aplica tambien a filas con amount_eur nulo o fx_ambiguous (la '
                      'descripcion es evidencia de naturaleza, no de importe); su EUR solo '
                      'suma cuando existe.',
                      'Las filas de transferencia a tercero (confidence baja) pueden '
                      'descartarse con el bloque sensibilidad_transferencia_tercero sin '
                      'recalcular nada.'],
        },
        'metodo': {
            'tolerancia_importes_eur': AMOUNT_TOLERANCE_EUR,
            'tolerancia_justificacion': 'amount_eur es DOUBLE: 0,01 EUR cubre residuos de conversion',
            'orden_voraz': 'abs(amount_eur) DESC, date_ok ASC, transaction_id ASC; '
                           'candidato por date_ok ASC, transaction_id ASC',
            'pases': [f'{rule}: max {days} dias, product_id distinto={distinct}, confidence={conf}'
                      for rule, conf, days, distinct in PASS_SPECS],
            'r3': 'tabla DESC_PATTERNS sobre la descripcion normalizada (mayusculas, sin '
                  'acentos, espacios colapsados), anclada al inicio con re.match; primera '
                  'coincidencia gana; familia traspaso confidence media, transferencia a '
                  'tercero por signo confidence baja; ver patrones_r3 con su medicion',
            'exclusiones': 'no se emparejan filas con fx_ambiguous verdadero, amount_eur nulo o '
                           'importe 0, ni pares con counterparty_id informado e incoherente en ambos lados',
        },
        'ambiguo_antes': before,
        'recuperado': recovered,
        'ambiguo_despues': after,
        'patrones_r3': [
            {
                'orden': index + 1,
                'patron': patron,
                'clase_resultante': _clase_label(clase),
                'confidence': confidence,
                'justificacion': justificacion,
                'filas_tocadas': recovered['r3_por_patron'][patron]['rows'],
                'eur_tocados': recovered['r3_por_patron'][patron]['eur'],
            }
            for index, (patron, clase, confidence, justificacion) in enumerate(DESC_PATTERNS)
        ],
        'orden_r3': 'R1 -> R2 estricta -> R2 laxa -> R3; R3 solo toca lo que sigue ambiguo; '
                    'primera coincidencia en DESC_PATTERNS gana y ninguna fila puede ser '
                    'trocada por dos patrones',
        'sensibilidad_transferencia_tercero': _sensibilidad_tercero(merged, company_months),
        'empresa_mes_a_cero': {
            'count': zeroed,
            'definicion': 'empresa-mes con 0 filas ambiguas despues (antes tenian >0)',
            'total_empresa_mes': len(company_months),
        },
        'residual_pct_empresa_mes': {
            'definicion': 'ambiguo_despues.eur / ambiguo_antes.eur * 100 por empresa-mes con antes.eur > 0',
            'n': int(len(residual_series)),
            'mediana': round(float(residual_series.quantile(0.5)), 2) if len(residual_series) else None,
            'p75': round(float(residual_series.quantile(0.75)), 2) if len(residual_series) else None,
            'p90': round(float(residual_series.quantile(0.90)), 2) if len(residual_series) else None,
        },
        'cotas_volumen_interno': {
            'cota_inferior_estricta': recovered['r2_estricta'],
            'cota_superior_estricta_mas_laxa': {
                'rows': recovered['r2_estricta']['rows'] + recovered['r2_laxa']['rows'],
                'eur': round(recovered['r2_estricta']['eur'] + recovered['r2_laxa']['eur'], 2),
            },
            'nota': 'La regla laxa es cota superior y la estricta cota inferior del volumen '
                    'realmente interno: el emparejamiento por importe y fecha produce falsos '
                    'pares y ninguna de las dos cotas es verdad verificada.',
        },
        'honestidad': {
            'falsos_pares': 'No se puede medir la tasa de falsos positivos con los datos '
                            'disponibles: counterparty_id solo esta informado en ~2% de las '
                            'transferencias y no confirma pares. No se estima ni se inventa.',
            'counterparty_informado_pct': round(float(rows_df['counterparty_id'].notna().mean() * 100.0), 2)
                if 'counterparty_id' in rows_df.columns else None,
            'uso_recomendado': 'El scorer v3 debe tratar el volumen emparejado como intervalo '
                               '[cota inferior estricta, cota superior laxa], no como flujo operativo confirmado.',
        },
        'empresa_mes': company_months,
    }


def pd_period(month):
    if isinstance(month, datetime):
        return month.date().isoformat()
    if isinstance(month, date):
        return month.isoformat()
    return str(month)[:10]


# --------------------------------------------------------------------- CLI ---

def load_scope():
    """Lee las fuentes de solo lectura via xray.paths y devuelve el perimetro."""
    import duckdb

    from xray.marts.common import parquet

    con = duckdb.connect(':memory:')
    try:
        transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
        classified = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
        products = parquet(paths.CLEAN_DIR / 'banking_products.parquet')
        n, ids = con.execute(f'SELECT count(*), count(DISTINCT transaction_id) FROM {classified}').fetchone()
        if n != ids:
            raise ValueError('tx_flow_class: transaction_id duplicado')
        rows = con.execute(f'''
            SELECT t.transaction_id, t.company_id, t.product_id, t.date_ok, t.month,
                   t.amount, t.amount_eur, t.amount_eur_source, t.fx_ambiguous,
                   t.category_norm, t.direction, t.description, t.counterparty_id,
                   t.product_type, c.flow_class, c.flow_source, c.rule_id,
                   c.es_atipico, c.direction_conflict,
                   p.currency_norm AS product_currency
            FROM {transactions} t JOIN {classified} c USING (transaction_id)
            LEFT JOIN {products} p USING (product_id)
            WHERE t.is_booked AND t.product_type IN {CASH_PRODUCT_TYPES}
              AND NOT t.is_open_month AND c.flow_class = 'transfer'
            ORDER BY t.transaction_id''').fetchdf()
    finally:
        con.close()
    rows['fx_ambiguous'] = rows['fx_ambiguous'].fillna(False).astype(bool)
    return rows


def write_outputs(output, resolution_df, coverage):
    if output.exists():
        raise FileExistsError(f'La salida ya existe: {output}')
    output.mkdir(parents=True)
    resolution_df.to_parquet(output / 'resolution.parquet', index=False)
    (output / 'coverage.json').write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, default=str) + '\n')


def main(argv=None):
    import pandas as pd

    parser = argparse.ArgumentParser(
        description='Informe de cobertura de resolucion de transferencias; capa de solo lectura')
    parser.add_argument('--output', type=Path, default=paths.ROOT / 'reports' / 'transfer_resolution',
                        help='Ruta nueva bajo reports/; aborta si ya existe')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f'La salida ya existe; usa --output con una ruta nueva: {args.output}')
    rows = load_scope()
    results = pd.DataFrame(resolve_transfers(rows.to_dict('records')))
    coverage = compute_coverage(rows, results)
    write_outputs(args.output, results, coverage)
    summary = {
        'output': str(args.output),
        'resolution_rows': int(len(results)),
        'ambiguo_antes': coverage['ambiguo_antes'],
        'recuperado': coverage['recuperado'],
        'ambiguo_despues': coverage['ambiguo_despues'],
        'patrones_r3': coverage['patrones_r3'],
        'sensibilidad_transferencia_tercero': coverage['sensibilidad_transferencia_tercero'],
        'empresa_mes_a_cero': coverage['empresa_mes_a_cero'],
        'residual_pct_empresa_mes': coverage['residual_pct_empresa_mes'],
        'cotas_volumen_interno': coverage['cotas_volumen_interno'],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
