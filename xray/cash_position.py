"""Reconstruccion point-in-time de la posicion de caja observada, sin ancla.

Acumula los movimientos de caja observados (todo lo que mueve caja: cobros,
pagos operativos, servicio de deuda, financiacion, inversion, transferencias)
dentro del perimetro comun del pipeline (is_booked, product_type en
checking/saving/wallet, meses cerrados). No hay ancla en balances: la salida es
una posicion acumulada RELATIVA al inicio de la historia observada de cada
empresa (mes_origen), NO el saldo bancario. Se llama posicion_acumulada en todo
el modulo y en toda la salida; esta PROHIBIDO presentarlo como el dinero que
la empresa tiene en el banco.

Propiedades declaradas:
- Point-in-time: para el corte del mes m solo se usan movimientos con
  fecha/mes <= fin de m. Ningun dato posterior altera el valor de un corte.
- Origen cero: posicion_acumulada(mes_origen) = flujo_neto(mes_origen); todo lo
  anterior al primer mes con actividad esta fuera del dominio (no se emiten
  filas para esos meses).
- Agujeros: un movimiento con fx_ambiguous o amount_eur nulo no se puede sumar
  y no es cero. Ensancha la banda [posicion_min, posicion_max] acumulando un
  volumen de agujero (proxy de magnitud: abs(amount) en moneda local; no es una
  conversion EUR y no entra en posicion_acumulada ni en meses_de_cobertura).
- El colchon y meses_de_cobertura son adimensionales respecto del tamano de la
  empresa: todo se compara con la propia historia, nunca entre empresas. Los
  EUR absolutos se publican, pero la magnitud para el scorer es la ratio.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from xray import paths
from xray.flows import FLOW_CLASSES
from xray.marts.common import parquet
from xray.period import FIRST_MONTH, LAST_MONTH

CASH_PRODUCTS = ('checking', 'saving', 'wallet')
WINDOW_MESES = 6
SIN_ACTIVIDAD = 'sin_actividad_de_caja_observada'
SIN_SALIDAS = 'sin_salidas_conocidas_en_ventana'
# Criterio declarado y determinista para confidence: porcentaje del volumen del
# tramo acumulado que es agujero (volumen_agujero / (volumen_conocido +
# volumen_agujero)). 'ninguna' ademas cuando el tramo acumulado no tiene
# volumen alguno conocido.
CONFIDENCE_UMBRALES = ((0.05, 'alta'), (0.25, 'media'), (0.50, 'baja'), (math.inf, 'ninguna'))
CONFIDENCE_LABELS = ('alta', 'media', 'baja', 'ninguna')
FLAGS_POSIBLES = ('posicion_negativa', 'agujeros_en_tramo', 'sin_salidas_conocidas')
OUTPUT_COLUMNS = ('company_id', 'month', 'flujo_neto',
                  *[f'flujo_{value}' for value in FLOW_CLASSES],
                  'volumen_conocido', 'volumen_agujero',
                  'posicion_acumulada', 'posicion_min', 'posicion_max',
                  'minimo_acumulado_hasta_m', 'colchon', 'salida_media_mensual',
                  'meses_de_cobertura', 'motivo_cobertura', 'confidence',
                  'mes_origen', 'n_meses_acumulados', 'n_meses_con_actividad',
                  'flags', 'reason')


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _round(value):
    return None if value is None else round(value, 6)


def _confidence(volumen_conocido: float, volumen_agujero: float) -> str:
    total = volumen_conocido + volumen_agujero
    if total <= 0:
        return 'ninguna'
    ratio = volumen_agujero / total
    for limit, label in CONFIDENCE_UMBRALES:
        if ratio <= limit:
            return label
    return 'ninguna'


def _validate_row(row: dict) -> None:
    for field in ('company_id', 'month', 'flujo_neto'):
        if row.get(field) is None:
            raise ValueError(f'flujo mensual: falta {field}')
    month = row['month']
    if type(month) is not date or month.day != 1:
        raise ValueError(f'flujo mensual: month debe ser el primer dia del mes: {month!r}')
    for field in ('flujo_neto', 'volumen_conocido', 'volumen_agujero', 'salidas_conocidas'):
        value = row.get(field, 0.0)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'flujo mensual: {field} debe ser un numero finito: {value!r}')
    if row.get('volumen_conocido', 0) < 0 or row.get('volumen_agujero', 0) < 0 or row.get('salidas_conocidas', 0) < 0:
        raise ValueError('flujo mensual: volumenes y salidas no pueden ser negativos')
    desglose = row.get('desglose') or {}
    for flow_class, value in desglose.items():
        if flow_class not in FLOW_CLASSES:
            raise ValueError(f'flujo mensual: flow_class fuera de contrato: {flow_class!r}')
        if not math.isfinite(value):
            raise ValueError(f'flujo mensual: desglose no finito: {flow_class}={value!r}')
    total = sum(desglose.values())
    # Tolerancia relativa: la suma del desglose acumula el redondeo float de
    # miles de terminos; solo se rechaza una inconsistencia estructural.
    if abs(total - row['flujo_neto']) > 1e-6 + 1e-9 * abs(row['flujo_neto']):
        raise ValueError(f'desglose {total} no cuadra con flujo_neto {row["flujo_neto"]}')


def reconstruct_cash_position(monthly_rows, companies=None):
    """Motor puro: acumula flujos mensuales en posicion de caja relativa.

    monthly_rows: iterable de dicts con company_id, month (primer dia del mes
    cerrado), flujo_neto (EUR, signo respetado), desglose por flow_class,
    volumen_conocido (sum abs amount_eur), volumen_agujero (proxy abs(amount)
    de los agujeros) y salidas_conocidas (sum abs de amount_eur negativos).
    companies: ids del universo; los que no tienen filas obtienen la razon
    SIN_ACTIVIDAD sin cifras inventadas.

    Devuelve {company_id: {'reason': ..., 'rows': [...]}} sin mutar la entrada.
    """
    por_empresa: dict[str, list[dict]] = {}
    for row in monthly_rows:
        copy = dict(row)
        _validate_row(copy)
        por_empresa.setdefault(copy['company_id'], []).append(copy)
    for rows in por_empresa.values():
        rows.sort(key=lambda row: row['month'])

    result: dict[str, dict] = {}
    for company_id in sorted(set(por_empresa) | set(companies or ())):
        rows = por_empresa.get(company_id)
        if not rows:
            result[company_id] = {'company_id': company_id, 'reason': SIN_ACTIVIDAD, 'rows': []}
            continue
        dense = {row['month']: row for row in rows}
        mes_origen = rows[0]['month']
        ultimo = rows[-1]['month']
        acumulado = 0.0
        minimo_acumulado = None
        known_span = 0.0
        hole_span = 0.0
        salidas_recientes: list[float] = []
        n_actividad = 0
        out_rows = []
        month = mes_origen
        while month <= ultimo:
            row = dense.get(month)
            if row is None:
                # Mes sin actividad dentro de la serie: cero real observado
                # (no hubo movimientos), no ausencia de dato.
                flujo = 0.0
                desglose = {}
                volumen_conocido = 0.0
                volumen_agujero = 0.0
                salidas = 0.0
            else:
                n_actividad += 1
                flujo = row['flujo_neto']
                desglose = {key: value for key, value in (row.get('desglose') or {}).items() if value}
                volumen_conocido = row.get('volumen_conocido', 0.0)
                volumen_agujero = row.get('volumen_agujero', 0.0)
                salidas = row.get('salidas_conocidas', 0.0)
            acumulado += flujo
            known_span += volumen_conocido
            hole_span += volumen_agujero
            minimo_acumulado = acumulado if minimo_acumulado is None else min(minimo_acumulado, acumulado)
            colchon = acumulado - minimo_acumulado
            salidas_recientes.append(salidas)
            ventana = salidas_recientes[-WINDOW_MESES:]
            salida_media = sum(ventana) / len(ventana)
            if salida_media > 0:
                cobertura = round(colchon / salida_media, 6)
                motivo = None
            else:
                # Salida media mensual nula: cobertura null con motivo
                # explicito; nunca infinito ni un numero inventado.
                cobertura = None
                motivo = SIN_SALIDAS
            flags = []
            if acumulado < 0:
                flags.append('posicion_negativa')
            if hole_span > 0:
                flags.append('agujeros_en_tramo')
            if cobertura is None:
                flags.append('sin_salidas_conocidas')
            out_rows.append({
                'month': month,
                'flujo_neto': round(flujo, 6),
                'desglose': {key: round(value, 6) for key, value in desglose.items()},
                'volumen_conocido': round(volumen_conocido, 6),
                'volumen_agujero': round(volumen_agujero, 6),
                'posicion_acumulada': round(acumulado, 6),
                'posicion_min': round(acumulado - hole_span, 6),
                'posicion_max': round(acumulado + hole_span, 6),
                'minimo_acumulado_hasta_m': round(minimo_acumulado, 6),
                'colchon': round(colchon, 6),
                'salida_media_mensual': round(salida_media, 6) if salida_media > 0 else None,
                'meses_de_cobertura': cobertura,
                'motivo_cobertura': motivo,
                'confidence': _confidence(known_span, hole_span),
                'mes_origen': mes_origen,
                'n_meses_acumulados': len(out_rows) + 1,
                'n_meses_con_actividad': n_actividad,
                'flags': flags,
                'reason': '' if cobertura is not None else SIN_SALIDAS,
            })
            month = _next_month(month)
        result[company_id] = {'company_id': company_id, 'reason': None, 'rows': out_rows}
    return result


def _fingerprints(files):
    result = {}
    for file in files:
        with file.open('rb') as stream:
            result[str(file.relative_to(paths.ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def load_monthly_flows(con) -> list[dict]:
    """Agrega el perimetro de caja a flujos mensuales por empresa (solo IO)."""
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    labels = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    classes = ','.join(f"'{value}'" for value in FLOW_CLASSES)
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW caja AS
        SELECT t.company_id, t.month, t.amount, t.amount_eur, f.flow_class
        FROM {transactions} t JOIN {labels} f USING (_src_row, transaction_id)
        WHERE t.is_booked AND NOT t.is_open_month
          AND t.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
          AND t.product_source='banking' AND t.product_type IN ({products})
    ''')
    if con.execute(f'SELECT count(*) FROM caja WHERE flow_class IS NULL OR flow_class NOT IN ({classes})').fetchone()[0]:
        raise ValueError('Clasificacion fuera de contrato en el perimetro de caja')
    breakdown = ','.join(
        f"coalesce(sum(amount_eur) FILTER (WHERE flow_class = '{value}'), 0) AS flujo_{value}"
        for value in FLOW_CLASSES
    )
    cursor = con.execute(f'''
        SELECT company_id, month,
               coalesce(sum(amount_eur), 0) AS flujo_neto,
               coalesce(sum(abs(amount_eur)), 0) AS volumen_conocido,
               coalesce(sum(abs(amount)) FILTER (WHERE amount_eur IS NULL), 0) AS volumen_agujero,
               coalesce(-sum(amount_eur) FILTER (WHERE amount_eur < 0), 0) AS salidas_conocidas,
               {breakdown}
        FROM caja GROUP BY company_id, month ORDER BY company_id, month
    ''')
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        row['desglose'] = {value: row.pop(f'flujo_{value}') for value in FLOW_CLASSES}
    return rows


def _percentiles(values, p):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return round(ordered[low] * (1 - fraction) + ordered[high] * fraction, 6)


def build_diagnostico_balances(con, ultimo):
    """C6: diagnostico de cordura contra el snapshot de balances. Solo informe.

    Compara la posicion acumulada reconstruida (relativa al mes_origen) con el
    saldo total de productos de caja en el snapshot 2026-09-01 (absoluto). No
    son la misma magnitud y no pueden coincidir; este diagnostico no entra en
    ninguna cifra de la reconstruccion ni del scorer.
    """
    balances = parquet(paths.CLEAN_DIR / 'balances.parquet')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    snapshot = {row[0]: float(row[1]) for row in con.execute(f'''
        SELECT company_id, sum(balance) FROM {balances}
        WHERE is_snapshot_date AND product_type IN ({products})
        GROUP BY company_id
    ''').fetchall()}
    comparables = {cid: (row['posicion_acumulada'], snapshot[cid])
                   for cid, row in ultimo.items() if cid in snapshot}
    if not comparables:
        raise ValueError('Diagnostico: ninguna empresa comparable contra el snapshot')
    absolutas = [abs(posicion - saldo) for posicion, saldo in comparables.values()]
    con_saldo = [(posicion, saldo) for posicion, saldo in comparables.values() if saldo != 0]
    relativas = [abs(posicion - saldo) / abs(saldo) for posicion, saldo in con_saldo]
    n = len(comparables)
    media_pos = sum(posicion for posicion, _ in comparables.values()) / n
    media_sal = sum(saldo for _, saldo in comparables.values()) / n
    var_pos = sum((posicion - media_pos) ** 2 for posicion, _ in comparables.values())
    var_sal = sum((saldo - media_sal) ** 2 for _, saldo in comparables.values())
    covar = sum((posicion - media_pos) * (saldo - media_sal) for posicion, saldo in comparables.values())
    correlation = covar / math.sqrt(var_pos * var_sal) if var_pos > 0 and var_sal > 0 else None
    acuerdo_signo = sum(1 for posicion, saldo in comparables.values()
                        if (posicion > 0) == (saldo > 0))
    return {
        'nota': 'Diagnostico solo informe: la posicion reconstruida es relativa al mes_origen y el '
                'snapshot de balances es un saldo absoluto a 2026-09-01; no son la misma magnitud '
                'y no deben coincidir. Este diagnostico no entra en ninguna cifra.',
        'snapshot_date': '2026-09-01',
        'empresas_snapshot': len(snapshot),
        'empresas_comparadas': n,
        'discrepancia_absoluta_eur': {'p25': _percentiles(absolutas, 25), 'mediana': _percentiles(absolutas, 50),
                                      'p75': _percentiles(absolutas, 75)},
        'discrepancia_relativa_al_snapshot': {'p25': _percentiles(relativas, 25),
                                              'mediana': _percentiles(relativas, 50),
                                              'p75': _percentiles(relativas, 75)},
        'correlacion_pearson_posicion_snapshot': None if correlation is None else round(correlation, 6),
        'acuerdo_de_signo': acuerdo_signo,
    }


def build_coverage_report(con, reconstruction, sources_before):
    companies = {row[0] for row in con.execute(
        f'SELECT company_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")}'
    ).fetchall()}
    con_serie = [cid for cid, record in reconstruction.items() if record['rows']]
    sin_actividad = sorted(cid for cid, record in reconstruction.items() if record['reason'] == SIN_ACTIVIDAD)
    rows = [row for record in reconstruction.values() for row in record['rows']]
    conf_empresa_mes = {label: sum(1 for row in rows if row['confidence'] == label)
                        for label in CONFIDENCE_LABELS}
    ultimo = {cid: record['rows'][-1] for cid, record in reconstruction.items() if record['rows']}
    conf_ultimo = {label: sum(1 for row in ultimo.values() if row['confidence'] == label)
                   for label in CONFIDENCE_LABELS}

    def coverage_stats(values):
        return {'n_no_nulos': len(values), 'p25': _percentiles(values, 25), 'mediana': _percentiles(values, 50),
                'p75': _percentiles(values, 75), 'p90': _percentiles(values, 90)}

    cobertura_todos = [row['meses_de_cobertura'] for row in rows if row['meses_de_cobertura'] is not None]
    cobertura_ultimo = [row['meses_de_cobertura'] for row in ultimo.values()
                        if row['meses_de_cobertura'] is not None]
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    agujeros = con.execute(f'''
        SELECT count(*), coalesce(sum(abs(amount)), 0) FROM {transactions} t
        WHERE t.is_booked AND NOT t.is_open_month
          AND t.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
          AND t.product_source='banking' AND t.product_type IN ({products})
          AND t.amount_eur IS NULL
    ''').fetchone()
    return {
        'workspace': str(paths.WORKSPACE.relative_to(paths.ROOT)),
        'perimetro': {'filas_empresa_mes': len(rows), 'mes_inicio': str(FIRST_MONTH), 'mes_fin': str(LAST_MONTH)},
        'empresas': {'universo': len(companies), 'con_serie': len(con_serie),
                     'sin_actividad_de_caja_observada': len(sin_actividad),
                     'ids_sin_actividad': sin_actividad},
        'reparto_confianza': {'empresa_mes': conf_empresa_mes, 'empresa_ultimo_mes': conf_ultimo},
        'meses_de_cobertura': {'empresa_mes': coverage_stats(cobertura_todos),
                               'empresa_ultimo_mes': coverage_stats(cobertura_ultimo)},
        'posicion_acumulada_negativa': {'empresa_mes': sum(1 for row in rows if row['posicion_acumulada'] < 0),
                                        'empresa_ultimo_mes': sum(1 for row in ultimo.values()
                                                                  if row['posicion_acumulada'] < 0)},
        'agujeros_fx': {'filas': agujeros[0], 'volumen_proxy_moneda_local': round(float(agujeros[1]), 6)},
        'diagnostico_balances': build_diagnostico_balances(con, ultimo),
        'fuentes_sha256': sources_before,
    }


def flat_rows(reconstruction):
    for record in reconstruction.values():
        for row in record['rows']:
            flat = {'company_id': record['company_id']}
            for value in FLOW_CLASSES:
                flat[f'flujo_{value}'] = row['desglose'].get(value, 0.0)
            for field in OUTPUT_COLUMNS:
                if field not in flat:
                    flat[field] = row.get(field)
            yield flat


def write_packet(output: Path, reconstruction, coverage) -> None:
    output.mkdir(parents=True, exist_ok=False)
    frame = pd.DataFrame(list(flat_rows(reconstruction)), columns=list(OUTPUT_COLUMNS))
    frame = frame.sort_values(['company_id', 'month'], kind='stable').reset_index(drop=True)
    frame.to_parquet(output / 'cash_position_monthly.parquet', index=False)
    (output / 'coverage.json').write_text(
        json.dumps(coverage, ensure_ascii=False, allow_nan=False, default=str, indent=2) + '\n',
        encoding='utf-8')


def summary(reconstruction, coverage):
    ultimo = {cid: record['rows'][-1] for cid, record in reconstruction.items() if record['rows']}
    cobertura_ultimo = [row['meses_de_cobertura'] for row in ultimo.values()
                        if row['meses_de_cobertura'] is not None]
    return {
        'output': 'reports/cash_position',
        'filas_empresa_mes': sum(len(record['rows']) for record in reconstruction.values()),
        'empresas_con_serie': coverage['empresas']['con_serie'],
        'empresas_sin_actividad_de_caja_observada': coverage['empresas']['sin_actividad_de_caja_observada'],
        'reparto_confianza_empresa_ultimo_mes': coverage['reparto_confianza']['empresa_ultimo_mes'],
        'meses_de_cobertura_ultimo_mes': coverage['meses_de_cobertura']['empresa_ultimo_mes'],
        'posicion_acumulada_negativa_empresa_mes': coverage['posicion_acumulada_negativa']['empresa_mes'],
        'diagnostico_correlacion_posicion_snapshot':
            coverage['diagnostico_balances']['correlacion_pearson_posicion_snapshot'],
    }


def _source_files():
    sources = [paths.CLEAN_DIR / f'{name}.parquet'
               for name in ('transactions', 'companies', 'banking_products', 'balances')]
    sources += [paths.INTERIM_DIR / f'{name}.parquet' for name in
                ('transactions', 'companies', 'banking_products', 'debt_products', 'debt_schedule_config',
                 'groups', 'invoices', 'tx_flow_class')]
    sources += [paths.MARTS_DIR / f'{name}.parquet' for name in
                ('fx_rates', 'observabilidad', 'panel_cobro', 'panel_deuda', 'panel_evidencia',
                 'panel_flujos', 'targets_proxy')]
    return sources


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Reconstruccion point-in-time de la posicion de caja observada, sin ancla')
    parser.add_argument('--output', type=Path, default=paths.ROOT / 'reports/cash_position')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('La salida ya existe; usa --output con una ruta nueva para conservar artefactos previos')
    before = _fingerprints(_source_files())
    with tempfile.TemporaryDirectory(prefix='cash-position-') as scratch:
        con = duckdb.connect(':memory:')
        try:
            con.execute('SET threads=4')
            con.execute("SET memory_limit='2GB'")
            con.execute(f"SET temp_directory='{scratch.replace(chr(39), chr(39) * 2)}'")
            monthly = load_monthly_flows(con)
            companies = [row[0] for row in con.execute(
                f'SELECT company_id FROM {parquet(paths.CLEAN_DIR / "companies.parquet")} ORDER BY company_id'
            ).fetchall()]
            reconstruction = reconstruct_cash_position(monthly, companies)
            coverage = build_coverage_report(con, reconstruction, before)
            rows = list(flat_rows(reconstruction))
        finally:
            con.close()
    if _fingerprints(_source_files()) != before:
        raise ValueError('Las fuentes cambiaron durante la reconstruccion; no se publica nada')
    write_packet(args.output, reconstruction, coverage)
    print(json.dumps({**summary(reconstruction, coverage), 'output': str(args.output)},
                     ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
