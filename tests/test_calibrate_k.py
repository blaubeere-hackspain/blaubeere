"""Tests de xray/calibrate_k: motor puro, point-in-time y CLI.

Los fixtures son temporales; ningun test toca datos reales del repo.
"""

import csv
import hashlib
import json
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import duckdb

from xray import paths
from xray.calibrate_k import (adverse_bias, company_scores, cumulative_ratio, h_value,
                              k_grid, load_panel, main, operating_volume, panel_scores,
                              prepare_panel, quantile, recommend, reactivity,
                              stability, dispersion, trend, subset_volume_distribution,
                              window_sums)


def month(i):
    """Mes i-esimo del dataset (0 = septiembre 2024)."""
    position = 8 + i
    return date(2024 + position // 12, position % 12 + 1, 1)


def record(i, c, p, d, activity=True):
    return (month(i), activity, c, p, d)


def series_simple(volumes, ratios, start=0):
    """Serie sintetica: volumen constante y ratio por mes (D = 0)."""
    return tuple(record(start + i, ratio * volume, (1.0 - ratio) * volume, 0.0)
                 for i, (volume, ratio) in enumerate(zip(volumes, ratios)))


class EngineTest(unittest.TestCase):
    def test_window_expands_to_six_months(self):
        series = tuple(record(i, 100.0, 100.0, 0.0) for i in range(8))
        # mes 1 (indice 0): ventana de 1 mes
        self.assertEqual(window_sums(series, 0), (100.0, 100.0, 0.0, 1))
        # mes 4 (indice 3): ventana de 4 meses
        c6, p6, d6, n = window_sums(series, 3)
        self.assertEqual((c6, p6, d6, n), (400.0, 400.0, 0.0, 4))
        # del 6 en adelante (indice 5+): ventana de 6 meses
        c6, p6, d6, n = window_sums(series, 5)
        self.assertEqual((c6, p6, d6, n), (600.0, 600.0, 0.0, 6))
        c6, p6, d6, n = window_sums(series, 7)
        self.assertEqual((c6, p6, d6, n), (600.0, 600.0, 0.0, 6))

    def test_window_moves_after_six_months(self):
        series = list(record(i, 100.0, 0.0, 0.0) for i in range(8))
        series[7] = record(7, 700.0, 0.0, 0.0)  # solo el mes corriente cambia
        c6, _, _, _ = window_sums(tuple(series), 7)
        self.assertEqual(c6, 100.0 * 5 + 700.0)  # 5 meses previos + mes corriente

    def test_month_without_activity_not_imputed_as_zero(self):
        series = (record(0, 100.0, 50.0, 0.0),
                  record(1, None, None, None, activity=False),
                  record(2, 30.0, 10.0, 0.0))
        c6, p6, d6, n_active = window_sums(series, 2)
        self.assertEqual((c6, p6, d6), (130.0, 60.0, 0.0))
        self.assertEqual(n_active, 2)  # el mes sin actividad no cuenta ni imputa
        scores = company_scores(series, 0.0)
        self.assertFalse(scores[1]['evaluable'])  # sin actividad => fuera del subconjunto
        self.assertTrue(scores[2]['evaluable'])

    def test_unknown_inputs_leave_month_out_of_subset(self):
        series = (record(0, 100.0, 50.0, 0.0),
                  record(1, None, 50.0, 0.0),  # C desconocido
                  record(2, 100.0, 50.0, None),  # D desconocido
                  record(3, 100.0, 50.0, 0.0))
        scores = company_scores(series, 0.0)
        self.assertTrue(scores[0]['evaluable'])
        self.assertFalse(scores[1]['evaluable'])
        self.assertFalse(scores[2]['evaluable'])
        self.assertTrue(scores[3]['evaluable'])
        self.assertIsNone(scores[1]['h'])
        self.assertIsNone(scores[2]['h'])

    def test_point_in_time_future_months_do_not_change_history(self):
        """El test mas importante: nada posterior al corte influye en el corte."""
        base = (record(0, 100.0, 50.0, 0.0),
                record(1, 80.0, 40.0, 0.0),
                record(2, 60.0, 60.0, 0.0))
        extended = base + (record(3, 0.0, 500.0, 0.0),
                           record(4, 10.0, 400.0, 0.0))
        for k in (0.0, 1234.0, 1.0e6):
            before = company_scores(base, k)
            after = company_scores(extended, k)
            for i in range(len(base)):
                self.assertEqual(after[i]['h'], before[i]['h'],
                                 f'H({i},{k}) cambio al anadir meses posteriores')
                self.assertEqual(after[i]['r_hist'], before[i]['r_hist'],
                                 f'R_hist({i}) cambio al anadir meses posteriores')
        # y la ratio acumulada hasta el corte 2 es exactamente la de los meses <= 2
        self.assertAlmostEqual(cumulative_ratio(base, 2), 240.0 / 390.0)
        self.assertAlmostEqual(cumulative_ratio(extended, 2), 240.0 / 390.0)

    def test_k_zero_returns_exact_window_ratio(self):
        series = (record(0, 100.0, 50.0, 0.0), record(1, 80.0, 40.0, 0.0),
                  record(2, 60.0, 60.0, 0.0), record(3, 90.0, 30.0, 0.0))
        for entry in company_scores(series, 0.0):
            if entry['evaluable']:
                self.assertAlmostEqual(entry['h'], 100.0 * entry['c6'] / (entry['c6'] + entry['t6']))
                self.assertAlmostEqual(entry['r_hist'] * 100.0, entry['h'])  # historial uniforme

    def test_k_zero_ignores_r_hist_when_histories_differ(self):
        # R_hist distinta de la ratio de ventana: con k=0 no debe influir
        series = (record(0, 1000.0, 100.0, 0.0),  # ratio alta en historial
                  record(1, 10.0, 90.0, 0.0))     # mes corriente casi en cero
        entry = company_scores(series, 0.0)[1]
        # la ventana del mes 1 es C6=1010, T6=190; una R_hist distinta no influye con k=0
        self.assertAlmostEqual(entry['h'], 100.0 * 1010.0 / 1200.0)

    def test_large_k_tends_to_r_hist(self):
        series = (record(0, 900.0, 100.0, 0.0), record(1, 900.0, 100.0, 0.0),
                  record(2, 900.0, 100.0, 0.0), record(3, 900.0, 100.0, 0.0),
                  record(4, 900.0, 100.0, 0.0), record(5, 100.0, 900.0, 0.0))
        r_hist = cumulative_ratio(series, 5)
        gaps = []
        previous = None
        for k in (1.0, 10.0, 1000.0, 1.0e5, 1.0e7, 1.0e9):
            h = company_scores(series, k)[5]['h']
            gap = abs(h - 100.0 * r_hist)
            gaps.append(gap)
            self.assertLessEqual(gap, previous if previous is not None else gap + 1)
            previous = gap
        self.assertLess(gaps[-1], 1.0)  # tendencia verificada, sin valor magico

    def test_monotonic_in_k_between_window_ratio_and_r_hist(self):
        # 6 meses con ratio alta y luego 3 con ratio baja: la ratio de ventana < R_hist
        series = tuple(record(i, 100.0, 10.0, 0.0) for i in range(6)) + \
                 tuple(record(i, 10.0, 100.0, 0.0) for i in range(6, 9))
        entry = company_scores(series, 0.0)[8]
        window_ratio = 100.0 * entry['c6'] / (entry['c6'] + entry['t6'])
        r_hist = 100.0 * entry['r_hist']
        self.assertLess(window_ratio, r_hist)  # el intervalo es no degenerado
        previous = window_ratio
        for k in (0.0, 1.0, 10.0, 100.0, 1000.0, 1.0e6):
            h = company_scores(series, k)[8]['h']
            self.assertGreaterEqual(h, previous - 1e-9)  # monotonia no decreciente
            previous = h
        self.assertLessEqual(previous, r_hist + 1e-9)

    def test_scale_invariance(self):
        series = (record(0, 123.0, 47.0, 11.0), record(1, 210.0, 90.0, 5.0),
                  record(2, 300.0, 150.0, 0.0))
        factor = 1.0e6
        scaled = tuple(record(i, c * factor, p * factor, d * factor)
                       for i, (_, _, c, p, d) in enumerate(series))
        k = 555.0
        for k_scale in (0.0, k, k * factor):
            base = company_scores(series, k_scale)[-1]['h']
            base_scaled = company_scores(scaled, k_scale * factor)[-1]['h']
            self.assertAlmostEqual(base, base_scaled, places=9)

    def test_h_value_pure_formula(self):
        # C6 + k*R_hist sobre C6+T6+k: con R_hist = 1 el H es 100
        self.assertAlmostEqual(h_value(0.0, 0.0, 1.0, 100.0), 100.0)
        self.assertIsNone(h_value(0.0, 0.0, None, 100.0))
        self.assertIsNone(h_value(0.0, 0.0, None, 0.0))

    def test_engine_does_not_mutate_input(self):
        rows = [{'company_id': 'a', 'month': month(0), 'tiene_actividad_caja': True,
                 'cobros_operativos_eur': 10.0, 'pagos_operativos_eur': 5.0,
                 'servicio_deuda_eur': 0.0},
                {'company_id': 'a', 'month': month(1), 'tiene_actividad_caja': True,
                 'cobros_operativos_eur': None, 'pagos_operativos_eur': 5.0,
                 'servicio_deuda_eur': 0.0}]
        snapshot = json.dumps(rows, default=str)
        panel = prepare_panel(rows)
        panel_scores(panel, 100.0)
        trend(panel, 100.0)
        adverse_bias(panel, {('a', month(0)): 15.0, ('a', month(1)): 5.0})
        subset_volume_distribution(panel)
        self.assertEqual(json.dumps(rows, default=str), snapshot)

    def test_quantile_interpolates(self):
        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 0.0), 1.0)
        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 1.0), 4.0)
        self.assertEqual(quantile([7.0], 0.25), 7.0)


class CalibrationFlowTest(unittest.TestCase):
    def build_panel(self):
        # dos empresas con historiales y ratios distintos
        return {
            'a': tuple(record(i, 600.0, 300.0, 100.0) for i in range(12)),
            'b': tuple(record(i, 100.0, 500.0, 0.0) for i in range(8)),
        }

    def test_stability_excludes_first_two_months(self):
        panel = self.build_panel()
        result = stability(panel_scores(panel, 0.0))
        # maximo de pares posibles: empresa a indices 3..23, empresa b indices 3..7
        self.assertLessEqual(result['n_pairs'], (24 - 3) + (8 - 3))
        self.assertGreater(result['n_pairs'], 0)
        self.assertIsNotNone(result['p75'])

    def test_dispersion_reports_band_and_spread(self):
        result = dispersion(panel_scores(self.build_panel(), 0.0))
        self.assertGreater(result['n_cuts'], 0)
        self.assertGreater(result['median_std'], 0.0)
        self.assertEqual(result['band_45_55_median'], 0)  # a=67, b=17 fuera de 45-55
        self.assertEqual(result['band_45_55_max'], 0)

    def test_dispersion_flattens_with_large_k(self):
        # dos empresas con historia comun y ultimo mes divergente: k enorme aplana
        shared = tuple(record(i, 500.0, 500.0, 0.0) for i in range(20))
        panel = {name: shared + (record(20, c, 1000.0 - c, 0.0),)
                 for name, c in zip('abcd', (100.0, 300.0, 700.0, 900.0))}
        small = dispersion(panel_scores(panel, 0.0))
        large = dispersion(panel_scores(panel, 1.0e9))
        self.assertLess(large['std_max'], small['std_max'])
        self.assertLess(large['iqr_max'], small['iqr_max'])

    def test_trend_counts_long_series(self):
        panel = self.build_panel()
        result = trend(panel, 0.0)
        self.assertEqual(result['n_companies'], 1)  # solo 'a' tiene >= 12 meses
        self.assertIn(result['pct_detectable'], (0.0, 100.0))

    def test_trend_detects_real_slope(self):
        rising = tuple(record(i, 100.0 + 10.0 * i, 500.0, 0.0) for i in range(14))
        result = trend({'a': rising}, 0.0)
        self.assertEqual(result['n_detected'], 1)
        self.assertEqual(result['pct_detectable'], 100.0)

    def test_adverse_bias_reports_ratio(self):
        panel = {'a': (record(0, 100.0, 100.0, 0.0),),  # volumen 200
                 'b': (record(0, None, None, None),  # con actividad, en intervalo
                       record(1, None, None, None))}
        result = adverse_bias(panel, {('b', month(0)): 1.0e6, ('b', month(1)): 5.0e5})
        self.assertEqual(result['n_subset'], 1)
        self.assertEqual(result['n_others'], 2)
        self.assertAlmostEqual(result['median_volume_subset_eur'], 200.0)
        self.assertAlmostEqual(result['median_volume_others_eur'], 750000.0)
        self.assertAlmostEqual(result['ratio_others_over_subset'], 3750.0)

    def test_adverse_bias_ignores_months_without_activity(self):
        panel = {'a': (record(0, 100.0, 100.0, 0.0),),
                 'b': (record(0, None, None, None, activity=False),)}
        with self.assertRaises(ValueError):
            adverse_bias(panel, {('b', month(0)): 1.0e6})

    def test_reactivity_delay_between_zero_and_window(self):
        small = reactivity(0.0, 1000.0)
        large = reactivity(1.0e9, 1000.0)
        # con k=0 la ventana de 6 meses refleja el 80% en 5 meses
        self.assertLessEqual(small['median_delay_months'], 6)
        # con k enorme la nota sigue a R_hist y el retardo crece bastante
        self.assertLess(small['median_delay_months'], large['median_delay_months'])
        self.assertLessEqual(large['max_delay_months'], 60)

    def test_k_grid_covers_volume_distribution(self):
        grid, info = k_grid([1.0, 10.0, 100.0, 1000.0, 10000.0], points=11)
        self.assertEqual(grid[0], 0.0)
        self.assertLessEqual(grid[1], info['p05'] * 1.01)  # muy por debajo del p05
        self.assertGreaterEqual(grid[-1], info['p95'] * 99.0)  # muy por encima del p95
        self.assertTrue(all(math.isfinite(value) and value > 0 for value in grid[1:]))
        self.assertEqual(len(grid), 12)

    def test_recommend_prefers_smallest_k_meeting_target(self):
        rows = [{'k': 0.0, 'dH_p75': 10.0}, {'k': 100.0, 'dH_p75': 7.0},
                {'k': 1000.0, 'dH_p75': 5.0}]
        best, meets = recommend(rows)
        self.assertTrue(meets)
        self.assertEqual(best['k'], 100.0)

    def test_recommend_declares_failure_when_no_k_meets(self):
        rows = [{'k': 0.0, 'dH_p75': 12.0}, {'k': 100.0, 'dH_p75': 9.5},
                {'k': 1000.0, 'dH_p75': 8.5}]
        best, meets = recommend(rows)
        self.assertFalse(meets)
        self.assertEqual(best['dH_p75'], 8.5)


class CliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mapping = {'ROOT': self.root, 'WORKSPACE': self.root,
                   'MARTS_DIR': self.root / 'data/marts', 'CLEAN_DIR': self.root / 'data/clean',
                   'INTERIM_DIR': self.root / 'data/interim'}
        for folder in mapping.values():
            folder.mkdir(parents=True, exist_ok=True)
        patcher = patch.multiple(paths, **mapping)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)
        self.before = self.snapshot()

    def snapshot(self):
        return {str(file.relative_to(self.root)): hashlib.sha256(file.read_bytes()).hexdigest()
                for file in sorted(self.root.rglob('*')) if file.suffix == '.parquet'}

    def parquet(self, name, columns, rows, folder):
        file = folder / f'{name}.parquet'
        self.con.execute('CREATE OR REPLACE TEMP TABLE fixture (' + ','.join(
            f'{name} {dtype}' for name, dtype in columns) + ')')
        if rows:
            self.con.executemany('INSERT INTO fixture VALUES (' + ','.join('?' for _ in columns) + ')',
                                 [tuple(row.get(key) for key, _ in columns) for row in rows])
        self.con.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)")
        self.before = self.snapshot()

    def load_fixtures(self):
        flows = [('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
                 ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
                 ('cobros_operativos_eur', 'DOUBLE'), ('pagos_operativos_eur', 'DOUBLE'),
                 ('cobros_operativos_conocido_eur', 'DOUBLE'),
                 ('pagos_operativos_conocido_eur', 'DOUBLE'),
                 ('ambiguo_entradas_conocido_eur', 'DOUBLE'),
                 ('ambiguo_salidas_conocido_eur', 'DOUBLE')]
        rows = []
        # empresa 'a': 10 meses determinados; ratio 0,9 sostenida y luego 0,1
        for i in range(10):
            c = 900.0 if i < 7 else 100.0
            rows.append({'company_id': 'a', 'group_id': 'g', 'month': month(i),
                         'available_at': month(i).replace(day=28), 'tiene_actividad_caja': True,
                         'cobros_operativos_eur': c, 'pagos_operativos_eur': 1000.0 - c,
                         'cobros_operativos_conocido_eur': c,
                         'pagos_operativos_conocido_eur': 1000.0 - c,
                         'ambiguo_entradas_conocido_eur': 0.0,
                         'ambiguo_salidas_conocido_eur': 0.0})
        # empresa 'b': meses 0-1 con C desconocido (en intervalo) y 2-9 determinados
        # con ratio oscilante, para que el contraste de C2 no venga solo de 'a'
        for i in range(10):
            known = i >= 2
            c = (50.0 if i % 2 == 0 else 450.0) if known else None
            p = 500.0 - c if known else 450.0
            rows.append({'company_id': 'b', 'group_id': 'g', 'month': month(i),
                         'available_at': month(i).replace(day=28), 'tiene_actividad_caja': True,
                         'cobros_operativos_eur': c, 'pagos_operativos_eur': p,
                         'cobros_operativos_conocido_eur': 50000.0,
                         'pagos_operativos_conocido_eur': p,
                         'ambiguo_entradas_conocido_eur': 0.0,
                         'ambiguo_salidas_conocido_eur': 0.0})
        self.parquet('panel_flujos', flows, rows, paths.MARTS_DIR)
        debt = [('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
                ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
                ('servicio_deuda_eur', 'DOUBLE'), ('servicio_deuda_conocido_eur', 'DOUBLE'),
                ('financiacion_no_desglosada_conocido_eur', 'DOUBLE')]
        debt_rows = [{'company_id': row['company_id'], 'group_id': 'g',
                      'month': row['month'], 'available_at': row['available_at'],
                      'tiene_actividad_caja': True, 'servicio_deuda_eur': 0.0,
                      'servicio_deuda_conocido_eur': 0.0,
                      'financiacion_no_desglosada_conocido_eur': 0.0} for row in rows]
        self.parquet('panel_deuda', debt, debt_rows, paths.MARTS_DIR)

    def test_cli_writes_new_output_and_is_read_only(self):
        self.load_fixtures()
        output = self.root / 'reports' / 'calibration_k'
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(['--output', str(output)])
        self.assertEqual(exit_code, 0)
        self.assertTrue((output / 'sweep.csv').is_file())
        self.assertTrue((output / 'report.md').is_file())
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary['output'], str(output))
        self.assertEqual(summary['subset_months'], 18)
        self.assertGreater(summary['recommended_k_eur'], 0.0)
        self.assertIn('meets_p75_target', summary)
        # serializacion estricta: el resumen no admite NaN
        with self.assertRaises(ValueError):
            json.dumps({'x': float('nan')}, allow_nan=False)
        # el CLI no toca ningun parquet (clean/interim/marts intactos)
        self.assertEqual(self.snapshot(), self.before)

    def test_cli_aborts_when_output_exists(self):
        self.load_fixtures()
        output = self.root / 'reports' / 'calibration_k'
        output.mkdir(parents=True)
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            main(['--output', str(output)])
        self.assertIn('ya existe', stderr.getvalue())

    def test_cli_default_output_is_reports_calibration_k(self):
        self.load_fixtures()
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main([])
        self.assertEqual(exit_code, 0)
        output = self.root / 'reports' / 'calibration_k'
        self.assertTrue((output / 'sweep.csv').is_file())
        with (output / 'sweep.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(float(rows[0]['k']), 0.0)  # k=0 referencia obligatoria
        self.assertIn('k recomendada', (output / 'report.md').read_text())
        self.assertEqual(self.snapshot(), self.before)


class LoaderTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'marts').mkdir(parents=True)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)

    def test_load_panel_reads_marts_and_flags_unknowns(self):
        month0 = date(2025, 1, 1)
        flows = [('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
                 ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
                 ('cobros_operativos_eur', 'DOUBLE'), ('pagos_operativos_eur', 'DOUBLE'),
                 ('cobros_operativos_conocido_eur', 'DOUBLE'),
                 ('pagos_operativos_conocido_eur', 'DOUBLE'),
                 ('ambiguo_entradas_conocido_eur', 'DOUBLE'),
                 ('ambiguo_salidas_conocido_eur', 'DOUBLE')]
        flow_rows = [{'company_id': 'a', 'group_id': 'g', 'month': month0,
                      'available_at': month0.replace(day=31), 'tiene_actividad_caja': True,
                      'cobros_operativos_eur': None, 'pagos_operativos_eur': 400.0,
                      'cobros_operativos_conocido_eur': 900.0,
                      'pagos_operativos_conocido_eur': 400.0,
                      'ambiguo_entradas_conocido_eur': 0.0,
                      'ambiguo_salidas_conocido_eur': 0.0}]
        self.parquet('panel_flujos', flows, flow_rows)
        debt = [('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
                ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
                ('servicio_deuda_eur', 'DOUBLE'), ('servicio_deuda_conocido_eur', 'DOUBLE'),
                ('financiacion_no_desglosada_conocido_eur', 'DOUBLE')]
        debt_rows = [{'company_id': 'a', 'group_id': 'g', 'month': month0,
                      'available_at': month0.replace(day=31), 'tiene_actividad_caja': True,
                      'servicio_deuda_eur': 0.0, 'servicio_deuda_conocido_eur': 0.0,
                      'financiacion_no_desglosada_conocido_eur': 0.0}]
        self.parquet('panel_deuda', debt, debt_rows)
        records, known = load_panel(self.root / 'marts')
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]['cobros_operativos_eur'])
        self.assertEqual(known[('a', month0)], 1300.0)  # cota inferior del volumen

    def parquet(self, name, columns, rows):
        file = self.root / 'marts' / f'{name}.parquet'
        self.con.execute('CREATE OR REPLACE TEMP TABLE fixture (' + ','.join(
            f'{name} {dtype}' for name, dtype in columns) + ')')
        if rows:
            self.con.executemany('INSERT INTO fixture VALUES (' + ','.join('?' for _ in columns) + ')',
                                 [tuple(row.get(key) for key, _ in columns) for row in rows])
        self.con.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)")


if __name__ == '__main__':
    unittest.main()
