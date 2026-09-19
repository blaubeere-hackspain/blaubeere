"""Tests de la exclusion auditable de nota 0 persistente (healthscore_v4).

Todo con fixtures temporales: ningun test escribe en reports/. El criterio
se prueba literal (A, B y ambos), incluido el caso de empresa SIN ningun mes
con nota (que NO se excluye: el criterio A exige al menos un mes con nota).
"""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import duckdb

from xray import paths
from xray.score_exclusions_v4 import (MES_CORTE_REFERENCIA, MOTIVO_EXCLUSION,
                                      UMBRAL_NOTA_BAJA, UMBRAL_PROPORCION,
                                      activity_stats, build_exclusion_report,
                                      compute_exclusion_stats, excluded_ids,
                                      main, write_exclusions_json)

MESES = [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1),
         date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)]
CORTE = date(2026, 8, 31)


def fila(company_id, month, health_score, c6=None, p6=None, t6=None,
         confidence='ninguna', obligacion=None):
    """Fila de assessments con contexto solo en el mes de referencia."""
    es_corte = month == MESES[-1]
    return {
        'company_id': company_id, 'month': month,
        'health_score': health_score,
        'c6': c6 if es_corte else None,
        'p6': p6 if es_corte else None,
        't6_efectivo': t6 if es_corte else None,
        'confidence': confidence if es_corte else None,
        'obligacion_vencida_m': obligacion if es_corte else None,
    }


def filas_fixture():
    rows = []
    # PERSISTENTE_A: nota en los 6 meses, todas < 1.0 -> criterio A.
    for month in MESES:
        rows.append(fila('PERSISTENTE_A', month, 0.4, c6=0.0, t6=0.0,
                         obligacion=500.0))
    # CERO_B: nota en los 6 meses; solo el corte vale exactamente 0 -> B.
    for month in MESES[:-1]:
        rows.append(fila('CERO_B', month, 40.0))
    rows.append(fila('CERO_B', MESES[-1], 0.0, c6=0.0, t6=0.0, obligacion=900.0))
    # AMBAS: 5 meses con nota < 1.0 y el corte exactamente 0 -> A y B.
    for month in MESES[:-1]:
        rows.append(fila('AMBAS', month, 0.2))
    rows.append(fila('AMBAS', MESES[-1], 0.0, c6=0.0, t6=0.0, obligacion=100.0))
    # NORMAL: notas altas, corte 55 -> sin exclusion.
    for month in MESES:
        rows.append(fila('NORMAL', month, 55.0))
    # SIN_NOTA: ningun mes con nota -> sin exclusion (A exige >= 1 mes con nota).
    for month in MESES:
        rows.append(fila('SIN_NOTA', month, None))
    return rows


class ComputeExclusionStatsTest(unittest.TestCase):
    def test_criterios_a_b_y_ambos(self):
        excluidos = compute_exclusion_stats(filas_fixture(), CORTE)
        self.assertEqual(excluidos['PERSISTENTE_A']['criterio'], 'A')
        self.assertEqual(excluidos['CERO_B']['criterio'], 'B')
        self.assertEqual(excluidos['AMBAS']['criterio'], 'A y B')
        self.assertEqual(sorted(excluidos), ['AMBAS', 'CERO_B', 'PERSISTENTE_A'])

    def test_cifras_del_criterio(self):
        excluidos = compute_exclusion_stats(filas_fixture(), CORTE)
        a = excluidos['PERSISTENTE_A']
        self.assertEqual(a['n_meses_con_nota'], 6)
        self.assertEqual(a['n_meses_con_nota_menor_1'], 6)
        self.assertEqual(a['proporcion'], 1.0)
        b = excluidos['CERO_B']
        self.assertEqual(b['n_meses_con_nota'], 6)
        self.assertEqual(b['n_meses_con_nota_menor_1'], 1)
        self.assertAlmostEqual(b['proporcion'], 1 / 6)
        self.assertEqual(excluidos['CERO_B']['health_score_corte_referencia'], 0.0)

    def test_contexto_del_corte_se_retienen(self):
        excluidos = compute_exclusion_stats(filas_fixture(), CORTE)
        a = excluidos['PERSISTENTE_A']
        self.assertEqual(a['c6_corte'], 0.0)
        self.assertEqual(a['t6_efectivo_corte'], 0.0)
        self.assertEqual(a['confidence_corte'], 'ninguna')
        self.assertEqual(a['obligacion_vencida_m_corte'], 500.0)
        self.assertIsNone(a['p6_corte'])

    def test_sin_ningun_mes_con_nota_no_se_excluye(self):
        excluidos = compute_exclusion_stats(filas_fixture(), CORTE)
        self.assertNotIn('SIN_NOTA', excluidos)
        self.assertNotIn('NORMAL', excluidos)

    def test_umbrales_son_constantes_con_nombre(self):
        self.assertEqual(UMBRAL_PROPORCION, 0.95)
        self.assertEqual(UMBRAL_NOTA_BAJA, 1.0)
        self.assertEqual(MES_CORTE_REFERENCIA, date(2026, 8, 1))
        self.assertIn('excluida_nota_cero_persistente', MOTIVO_EXCLUSION)

    def test_proporcion_igual_al_umbral_excluye(self):
        # 19 de 20 meses con nota < 1.0 = 0.95 exacto: el criterio dice >= 95%.
        meses20 = [date(2024, m, 1) for m in range(1, 13)] + \
                  [date(2025, m, 1) for m in range(1, 9)]
        filas20 = [fila('LIMITE95', month, 0.4 if index < 19 else 30.0)
                   for index, month in enumerate(meses20)]
        excluidos = compute_exclusion_stats(filas20, date(2024, 8, 31))
        self.assertIn('LIMITE95', excluidos)
        self.assertEqual(excluidos['LIMITE95']['criterio'], 'A')

    def test_bajo_el_umbral_no_excluye(self):
        # 11 de 12 meses con nota < 1.0 = 0.9166 < 0.95 y corte != 0: nada.
        months = [date(2024, m, 1) for m in range(10, 13)] + \
                 [date(2025, m, 1) for m in range(1, 10)]
        rows = [fila('BAJO', month, 0.5) for month in months[:11]]
        rows.append(fila('BAJO', months[11], 30.0))
        excluidos = compute_exclusion_stats(rows, date(2025, 9, 30))
        self.assertNotIn('BAJO', excluidos)

    def test_el_corte_referencia_aporta_el_contexto(self):
        rows = filas_fixture()
        # El 0 de CERO_B esta en agosto; con corte de referencia julio el
        # criterio B no dispara (aun no hay fila del corte con nota 0) y
        # CERO_B ni siquiera entra: en julio tenia notas de 40.
        excluidos = compute_exclusion_stats(rows, date(2026, 7, 31))
        self.assertNotIn('CERO_B', excluidos)
        # AMBAS y PERSISTENTE_A siguen excluidas por el criterio A.
        self.assertEqual(excluidos['AMBAS']['criterio'], 'A')
        self.assertEqual(excluidos['PERSISTENTE_A']['criterio'], 'A')


class ActivityStatsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'data/clean').mkdir(parents=True)
        patcher = patch.multiple(paths, CLEAN_DIR=self.root / 'data/clean')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)

    def transactions(self, rows):
        file = self.root / 'data/clean/transactions.parquet'
        self.con.execute('CREATE OR REPLACE TEMP TABLE tx (company_id VARCHAR, '
                         'direction VARCHAR, amount_eur DOUBLE)')
        self.con.executemany('INSERT INTO tx VALUES (?, ?, ?)',
                             [(r[0], r[1], r[2]) for r in rows])
        self.con.execute(f"COPY tx TO '{file}' (FORMAT PARQUET)")
        return file

    def test_cuenta_transacciones_y_entradas(self):
        file = self.transactions([
            ('A', 'in', 100.0), ('A', 'in', 250.5), ('A', 'out', -80.0),
            ('B', 'out', -10.0), ('A', 'in', None)])
        stats = activity_stats(self.con, ['A', 'B', 'C'], file)
        self.assertEqual(stats['A']['n_transacciones_total'], 4)
        self.assertEqual(stats['A']['n_entradas_dinero'], 3)
        # El amount nulo no suma (nulo nunca es cero), pero SI cuenta como entrada.
        self.assertAlmostEqual(stats['A']['importe_entradas_dinero_eur'], 350.5)
        self.assertEqual(stats['B']['n_transacciones_total'], 1)
        self.assertAlmostEqual(stats['B']['importe_entradas_dinero_eur'], 0.0)
        self.assertNotIn('C', stats)


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'data/clean').mkdir(parents=True)
        patcher = patch.multiple(paths, CLEAN_DIR=self.root / 'data/clean')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)
        file = self.root / 'data/clean/transactions.parquet'
        self.con.execute('CREATE OR REPLACE TEMP TABLE tx (company_id VARCHAR, '
                         'direction VARCHAR, amount_eur DOUBLE)')
        self.con.executemany('INSERT INTO tx VALUES (?, ?, ?)', [
            ('PERSISTENTE_A', 'in', 1200.0), ('PERSISTENTE_A', 'out', -300.0),
            ('PERSISTENTE_A', 'in', 800.0), ('CERO_B', 'in', 50.0),
            ('AMBAS', 'in', 7000.0), ('AMBAS', 'in', 100.0),
            ('AMBAS', 'in', 200.0), ('AMBAS', 'in', 300.0),
            ('AMBAS', 'in', 400.0), ('AMBAS', 'out', -50.0),
            ('NORMAL', 'in', 10.0), ('SIN_NOTA', 'in', 5.0),
        ])
        self.con.execute(f"COPY tx TO '{file}' (FORMAT PARQUET)")

    def test_reporte_completo(self):
        report = build_exclusion_report(filas_fixture(), self.con)
        self.assertEqual(report['cifras_agregadas']['criterio_A'], 2)  # A + ambos
        self.assertEqual(report['cifras_agregadas']['criterio_B'], 2)  # B + ambos
        self.assertEqual(report['cifras_agregadas']['ambos'], 1)
        self.assertEqual(report['cifras_agregadas']['union_excluidas'], 3)
        self.assertEqual(len(report['excluidas']), 3)
        record = report['excluidas'][0]
        for field in ('company_id', 'criterio', 'n_meses_con_nota',
                      'n_meses_con_nota_menor_1', 'proporcion',
                      'health_score_corte_referencia', 'c6_corte', 'p6_corte',
                      't6_efectivo_corte', 'confidence_corte',
                      'obligacion_vencida_m_corte', 'n_transacciones_total',
                      'n_entradas_dinero', 'importe_entradas_dinero_eur'):
            self.assertIn(field, record)
        # Efecto en el corte de referencia, ANTES vs DESPUES.
        efecto = report['efecto_corte_referencia']
        self.assertEqual(efecto['n_con_nota_antes'], 4)      # A, B, AMBAS, NORMAL
        self.assertEqual(efecto['n_con_nota_despues'], 1)    # solo NORMAL
        self.assertEqual(efecto['mediana_despues'], 55.0)
        # Actividad real de las excluidas (N, M, X medidos sobre las 3).
        actividad = report['actividad_real_excluidas']
        self.assertEqual(actividad['mediana_transacciones_por_empresa'], 3)
        self.assertEqual(actividad['mediana_entradas_dinero_por_empresa'], 2)
        self.assertAlmostEqual(actividad['importe_total_entradas_eur'],
                               1200.0 + 800.0 + 50.0 + 7000.0 + 100.0 + 200.0
                               + 300.0 + 400.0)
        # Declaracion obligatoria con las cifras medidas.
        declaracion = report['declaracion']
        self.assertIn('Exclusion por decision de producto', declaracion)
        self.assertIn('SI tienen actividad real', declaracion)
        self.assertIn('observabilidad asimetrica', declaracion)
        self.assertIn('--no-excluir-cero-persistente', declaracion)
        self.assertIn(str(actividad['mediana_transacciones_por_empresa']),
                      declaracion)
        json.dumps(report, allow_nan=False)

    def test_reporte_sin_excluidas(self):
        rows = [fila('NORMAL', month, 55.0) for month in MESES]
        report = build_exclusion_report(rows, self.con)
        self.assertEqual(report['cifras_agregadas']['union_excluidas'], 0)
        self.assertEqual(report['excluidas'], [])
        self.assertIsNone(report['actividad_real_excluidas']
                          ['mediana_transacciones_por_empresa'])

    def test_write_aborts_if_exists_and_creates_parents(self):
        report = build_exclusion_report(filas_fixture(), self.con)
        target = self.root / 'anidado' / 'exclusiones.json'
        write_exclusions_json(target, report)
        self.assertEqual(json.loads(target.read_text())['cifras_agregadas']
                         ['union_excluidas'], 3)
        with self.assertRaises(ValueError):
            write_exclusions_json(target, report)

    def test_excluded_ids(self):
        report = build_exclusion_report(filas_fixture(), self.con)
        self.assertEqual(excluded_ids(report),
                         {'PERSISTENTE_A', 'CERO_B', 'AMBAS'})


class CliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)

    def parquet(self, path, rows, with_excluida=False):
        columns = [('version', 'VARCHAR'), ('company_id', 'VARCHAR'),
                   ('month', 'DATE'), ('as_of', 'DATE'),
                   ('health_score', 'DOUBLE'), ('c6', 'DOUBLE'),
                   ('p6', 'DOUBLE'), ('t6_efectivo', 'DOUBLE'),
                   ('confidence', 'VARCHAR'), ('obligacion_vencida_m', 'DOUBLE')]
        if with_excluida:
            columns.append(('excluida', 'BOOLEAN'))
        self.con.execute('CREATE OR REPLACE TEMP TABLE a (' + ','.join(
            f'{name} {kind}' for name, kind in columns) + ')')
        placeholders = ','.join('?' for _ in columns)
        self.con.executemany(
            f'INSERT INTO a VALUES ({placeholders})',
            [tuple([r.get('version', 'healthscore_v4'), r['company_id'],
                    r['month'], r['month'].replace(day=28), r['health_score'],
                    r.get('c6'), r.get('p6'), r.get('t6_efectivo'),
                    r.get('confidence'), r.get('obligacion_vencida_m')]
                   + ([r.get('excluida')] if with_excluida else []))
             for r in rows])
        self.con.execute(f"COPY a TO '{path}' (FORMAT PARQUET)")

    def test_cli_writes_exclusiones_json(self):
        source = self.root / 'assessments.parquet'
        self.parquet(source, filas_fixture())
        output = self.root / 'exclusiones.json'
        stdout, errors = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(errors):
            self.assertEqual(main(['--input', str(source),
                                   '--output', str(output)]), 0)
        report = json.loads(output.read_text())
        self.assertEqual(report['cifras_agregadas']['union_excluidas'], 3)
        printed = json.loads(stdout.getvalue())
        self.assertEqual(printed['excluidas'], 3)

    def test_cli_aborts_if_output_exists(self):
        source = self.root / 'assessments.parquet'
        self.parquet(source, filas_fixture())
        output = self.root / 'exclusiones.json'
        with redirect_stdout(StringIO()):
            main(['--input', str(source), '--output', str(output)])
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            self.assertEqual(main(['--input', str(source),
                                   '--output', str(output)]), 1)
        self.assertIn('ya existe', errors.getvalue())

    def test_cli_rejects_parquet_already_excluded(self):
        source = self.root / 'excluido.parquet'
        rows = filas_fixture()
        for row in rows:
            row['excluida'] = row['company_id'] == 'PERSISTENTE_A'
        self.parquet(source, rows, with_excluida=True)
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            self.assertEqual(main(['--input', str(source),
                                   '--output', str(self.root / 'out.json')]), 1)
        self.assertIn('YA tiene la exclusion aplicada', errors.getvalue())

    def test_cli_fails_without_input(self):
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            self.assertEqual(main(['--input', str(self.root / 'no.parquet'),
                                   '--output', str(self.root / 'out.json')]), 1)
        self.assertIn('falta', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
