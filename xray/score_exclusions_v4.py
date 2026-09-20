"""Exclusion auditable de empresas con nota 0 persistente (healthscore_v4).

DECISION DE PRODUCTO DEL USUARIO (no reabrir): las empresas con nota 0
persistente se EXCLUYEN del algoritmo v4. Este modulo CALCULA la lista de
excluidas y la DOCUMENTA; la aplicacion en el adaptador vive en
xray/scoring_io_v4.py (flag --excluir-cero-persistente, activado por
defecto; reversible con --no-excluir-cero-persistente).

CRITERIO LITERAL (cifras verificadas en el workspace canonico
data/runs/20260919T022951Z-83143e8d sobre reports/score_v4/assessments.parquet:
criterio A = 30 empresas, criterio B = 12, union = 33; efecto en el corte
2026-08-31: empresas con nota 904 -> 876, mediana 36,89 -> 37,79):

  Una empresa queda EXCLUIDA si cumple A o B:
    A. tiene al menos un mes con nota, y en >= 95% de sus meses CON nota su
       health_score < 1.0
    B. su health_score es exactamente 0 en el corte de referencia
       (mes 2026-08-01, cutoff 2026-08-31)

Los umbrales y el corte de referencia son CONSTANTES CON NOMBRE (nada de
numeros magicos incrustados). La exclusion es de PERIMETRO, no de formula:
el motor puro (xray/scoring_v4.py) NO se toca.

La exclusion NUNCA borra filas: el adaptador conserva las filas de las
empresas excluidas con health_score = NULL, columna booleana `excluida`
= true y el motivo en `reasons`; eso es lo que la hace reversible y
auditable.

Este modulo tambien emite el paquete reports/score_v4/exclusiones.json con,
para CADA empresa excluida, el criterio que dispara, sus cifras del criterio
y el CONTEXTO que documenta por que la exclusion es discutible (c6, t6,
confidence, p6, obligacion vencida del corte y su actividad real en
clean/transactions.parquet), mas la DECLARACION obligatoria sobre la causa
(observabilidad asimetrica, no necesariamente mala salud financiera).
"""

import argparse
import json
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

import duckdb

from xray import paths
from xray.marts.common import parquet

# --- Umbrales y corte de referencia: constantes con nombre ----------------
UMBRAL_PROPORCION = 0.95          # criterio A: >= 95% de los meses con nota
UMBRAL_NOTA_BAJA = 1.0            # criterio A: health_score < 1.0 cuenta como nota 0
MES_CORTE_REFERENCIA = date(2026, 8, 1)   # corte de referencia del criterio B

MOTIVO_EXCLUSION = 'excluida_nota_cero_persistente'
FLAG_ACTIVAR = '--excluir-cero-persistente'
FLAG_DESACTIVAR = '--no-excluir-cero-persistente'
DECLARACION_PLANTILLA = (
    'Exclusion por decision de producto. Las empresas excluidas por nota 0 '
    'persistente SI tienen actividad real: mediana de {n} transacciones y '
    '{m} entradas de dinero por un total de {x} EUR. Su nota 0 se origina en '
    'c6 = 0 (cobros no clasificados) combinado con deuda observable por el '
    'lado de facturas, es decir observabilidad asimetrica, no necesariamente '
    'mala salud financiera. La exclusion las retira del algoritmo pero no '
    'corrige la causa; es reversible con ' + FLAG_DESACTIVAR + '.')


def _end_of_month(mes):
    """Cutoff (ultimo dia) del mes de referencia (primer dia de mes)."""
    return mes.replace(day=monthrange(mes.year, mes.month)[1])


def corte_referencia(mes=None):
    """Cutoff del criterio B: ultimo dia del mes de referencia."""
    return _end_of_month(MES_CORTE_REFERENCIA if mes is None else mes)


def compute_exclusion_stats(rows, cutoff):
    """Estadisticas del criterio por empresa sobre filas de assessments.

    `rows` son dicts con company_id, month (date, primer dia del mes) y
    health_score; cuando la fila es del corte de referencia tambien se
    retienen c6, p6, t6_efectivo, confidence y obligacion_vencida_m como
    contexto de la exclusion. NO muta rows. Devuelve (stats, union) donde
    stats mapea company_id -> record del criterio (solo empresas excluidas)
    y union es el conjunto de company_id excluidos.
    """
    cutoff = corte_referencia() if cutoff is None else cutoff
    ref_month = cutoff.replace(day=1)
    stats = {}
    for row in rows:
        company = row['company_id']
        entry = stats.setdefault(company, {
            'n_meses_con_nota': 0,
            'n_meses_con_nota_menor_1': 0,
            'corte': None,
        })
        health = row['health_score']
        if health is not None:
            entry['n_meses_con_nota'] += 1
            if health < UMBRAL_NOTA_BAJA:
                entry['n_meses_con_nota_menor_1'] += 1
        if row['month'] == ref_month and entry['corte'] is None:
            entry['corte'] = {
                'health_score': health,
                'c6': row.get('c6'),
                'p6': row.get('p6'),
                't6_efectivo': row.get('t6_efectivo'),
                'confidence': row.get('confidence'),
                'obligacion_vencida_m': row.get('obligacion_vencida_m'),
            }
    excluidos = {}
    for company in sorted(stats):
        entry = stats[company]
        n_con_nota = entry['n_meses_con_nota']
        n_menor_1 = entry['n_meses_con_nota_menor_1']
        proporcion = (n_menor_1 / n_con_nota) if n_con_nota else None
        dispara_a = n_con_nota > 0 and proporcion >= UMBRAL_PROPORCION
        corte = entry['corte'] or {}
        health_corte = corte.get('health_score')
        dispara_b = health_corte is not None and health_corte == 0.0
        if not (dispara_a or dispara_b):
            continue
        criterio = ('A y B' if dispara_a and dispara_b
                    else 'A' if dispara_a else 'B')
        excluidos[company] = {
            'company_id': company,
            'criterio': criterio,
            'n_meses_con_nota': n_con_nota,
            'n_meses_con_nota_menor_1': n_menor_1,
            'proporcion': proporcion,
            'health_score_corte_referencia': corte.get('health_score'),
            'c6_corte': corte.get('c6'),
            'p6_corte': corte.get('p6'),
            't6_efectivo_corte': corte.get('t6_efectivo'),
            'confidence_corte': corte.get('confidence'),
            'obligacion_vencida_m_corte': corte.get('obligacion_vencida_m'),
        }
    return excluidos


def activity_stats(con, company_ids, transactions_path=None):
    """Actividad real por empresa en clean/transactions.parquet.

    Para cada empresa: numero TOTAL de movimientos del parquet (sin filtros:
    es la actividad bruta de la fuente, la que documenta que las excluidas
    NO son empresas sin actividad), numero de entradas de dinero
    (direction='in') y su importe total en EUR (los amount_eur nulos no
    suman; nulo nunca es cero).
    """
    if not company_ids:
        return {}
    source = parquet(transactions_path or (paths.CLEAN_DIR / 'transactions.parquet'))
    ids = ','.join('?' for _ in company_ids)
    rows = con.execute(
        f'''SELECT company_id, count(*) AS n_transacciones,
                   count(*) FILTER (WHERE direction='in') AS n_entradas,
                   coalesce(sum(amount_eur) FILTER (WHERE direction='in'), 0.0)
                       AS importe_entradas_eur
            FROM {source} WHERE company_id IN ({ids})
            GROUP BY company_id''',
        list(company_ids)).fetchall()
    return {row[0]: {'n_transacciones_total': row[1],
                     'n_entradas_dinero': row[2],
                     'importe_entradas_dinero_eur': float(row[3])}
            for row in rows}


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) / 2
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    value = ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower)
    # Sin redondeo: la mediana es una cifra medida y debe poder compararse
    # bit a bit contra la del parquet (solo se redondea el print humano).
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def build_exclusion_report(rows, con, cutoff=None, transactions_path=None):
    """Construye el contenido completo de exclusiones.json.

    `rows` son las filas de assessments PRE-exclusion (con health_score
    original). Devuelve un dict serializable con allow_nan=False.
    """
    cutoff = corte_referencia() if cutoff is None else cutoff
    excluidos = compute_exclusion_stats(rows, cutoff)
    records = list(excluidos.values())
    n_a = sum(1 for r in records if r['criterio'] in ('A', 'A y B'))
    n_b = sum(1 for r in records if r['criterio'] in ('B', 'A y B'))
    n_ambos = sum(1 for r in records if r['criterio'] == 'A y B')
    actividad = activity_stats(con, sorted(excluidos), transactions_path)
    for company, extra in actividad.items():
        excluidos[company].update(extra)
    n_tx = [r['n_transacciones_total'] for r in records if 'n_transacciones_total' in r]
    n_en = [r['n_entradas_dinero'] for r in records if 'n_entradas_dinero' in r]
    x_total = sum(r.get('importe_entradas_dinero_eur', 0.0) or 0.0 for r in records)
    declaracion = DECLARACION_PLANTILLA.format(
        n=_median(n_tx), m=_median(n_en), x=round(x_total, 2))

    # Efecto en el corte de referencia: antes vs despues de la exclusion.
    ref_month = cutoff.replace(day=1)
    filas_corte = [row for row in rows if row['month'] == ref_month]
    con_nota_antes = sorted(row['health_score'] for row in filas_corte
                            if row['health_score'] is not None)
    ids_excluidos = set(excluidos)
    con_nota_despues = sorted(row['health_score'] for row in filas_corte
                              if row['health_score'] is not None
                              and row['company_id'] not in excluidos)
    report = {
        'model_version': 'healthscore_v4',
        'generador': 'xray/score_exclusions_v4.py',
        'criterio_literal': (
            'Una empresa queda EXCLUIDA si cumple A o B: '
            'A. tiene al menos un mes con nota, y en >= 95% de sus meses CON '
            'nota su health_score < 1.0. '
            'B. su health_score es exactamente 0 en el corte de referencia '
            f'({MES_CORTE_REFERENCIA.isoformat()}, cutoff '
            f'{cutoff.isoformat()}).'),
        'umbrales': {
            'proporcion_minima_criterio_a': UMBRAL_PROPORCION,
            'nota_baja_criterio_a': UMBRAL_NOTA_BAJA,
            'mes_corte_referencia_criterio_b': MES_CORTE_REFERENCIA.isoformat(),
            'cutoff_referencia': cutoff.isoformat(),
        },
        'cifras_agregadas': {
            'criterio_A': n_a,
            'criterio_B': n_b,
            'ambos': n_ambos,
            'union_excluidas': len(records),
            'fuente': 'reports/score_v4/assessments.parquet (pre-exclusion)',
        },
        'efecto_corte_referencia': {
            'n_con_nota_antes': len(con_nota_antes),
            'n_con_nota_despues': len(con_nota_despues),
            'mediana_antes': _median(con_nota_antes),
            'mediana_despues': _median(con_nota_despues),
        },
        'actividad_real_excluidas': {
            'poblacion': f'las {len(records)} empresas excluidas',
            'mediana_transacciones_por_empresa': _median(n_tx),
            'mediana_entradas_dinero_por_empresa': _median(n_en),
            'importe_total_entradas_eur': round(x_total, 2),
            'definicion': 'transacciones: TODAS las filas de la empresa en '
                          "clean/transactions.parquet (sin filtros); entradas "
                          "de dinero: filas con direction='in'; importe: suma "
                          'de amount_eur de esas filas (los nulos no suman).',
        },
        'declaracion': declaracion,
        'flag': {'activar': FLAG_ACTIVAR, 'desactivar': FLAG_DESACTIVAR,
                 'valor_por_defecto': 'activada (decision de producto del usuario)'},
        'motivo_reasons': MOTIVO_EXCLUSION,
        'excluidas': [excluidos[company] for company in sorted(excluidos)],
    }
    json.dumps(report, allow_nan=False)
    return report


def write_exclusions_json(path, report):
    """Escribe exclusiones.json (ruta nueva; aborta si existe)."""
    path = Path(path)
    if path.exists():
        raise ValueError(f'la salida ya existe: {path}; usa --output con una '
                         'ruta nueva para conservar revisiones previas')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False,
                               indent=2) + '\n', encoding='utf-8')
    return path


def excluded_ids(report):
    """company_id excluidos desde el reporte (o el dict de records)."""
    if 'excluidas' in report:
        return {record['company_id'] for record in report['excluidas']}
    return set(report)


def load_assessments_rows(parquet_path, con=None):
    """Lee un assessments.parquet como dicts (para el CLI de este modulo)."""
    owned = con is None
    con = duckdb.connect(':memory:') if owned else con
    try:
        cursor = con.execute(f'SELECT * FROM {parquet(parquet_path)}')
        columns = [column[0] for column in cursor.description]
        raw = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        if owned:
            con.close()
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Calcula la lista de empresas excluidas por nota 0 '
                    'persistente (healthscore_v4) y escribe exclusiones.json')
    parser.add_argument('--input', type=Path, default=paths.ROOT / 'reports/score_v4/assessments.parquet',
                        help='assessments.parquet PRE-exclusion (con health_score original)')
    parser.add_argument('--output', type=Path, default=paths.ROOT / 'reports/score_v4/exclusiones.json')
    args = parser.parse_args(argv)
    if not args.input.exists():
        print(f'Error: falta {args.input}; ejecuta antes xray.scoring_io_v4',
              file=sys.stderr)
        return 1
    con = duckdb.connect(':memory:')
    try:
        con.execute('SET threads=4')
        if excluida_cols_check(args.input, con):
            print('Error: el parquet de entrada YA tiene la exclusion aplicada '
                  '(columna excluida con valores true); el criterio necesita el '
                  'health_score original. Usa un parquet generado con '
                  '--no-excluir-cero-persistente.', file=sys.stderr)
            return 1
        rows = load_assessments_rows(args.input, con)
        report = build_exclusion_report(rows, con)
    except (ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    finally:
        con.close()
    try:
        write_exclusions_json(args.output, report)
    except ValueError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    agregado = report['cifras_agregadas']
    print(json.dumps({'output': str(args.output), 'excluidas': agregado['union_excluidas'],
                      'criterio_A': agregado['criterio_A'], 'criterio_B': agregado['criterio_B'],
                      'ambos': agregado['ambos']}, ensure_ascii=False))
    return 0


def excluida_cols_check(input_path, con):
    """True si el parquet ya trae filas con excluida = true."""
    try:
        count = con.execute(f'''SELECT count(*) FROM {parquet(input_path)}
            WHERE excluida''').fetchone()[0]
    except duckdb.Error:
        return False
    return count > 0


if __name__ == '__main__':
    sys.exit(main())
