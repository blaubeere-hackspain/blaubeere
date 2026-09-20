"""Tests del adaptador IO de healthscore_v4 (xray/scoring_io_v4.py).

El test point-in-time es el mas importante: los movimientos y capas
posteriores al corte no pueden cambiar la nota de ese corte. Incluye el
test obligatorio del criterio de hecho: el join de las tres capas no pierde
filas (30.864 pares en el workspace canonico; verificado tambien contra los
parquet reales si existen).
"""

import hashlib
import json
import tempfile
import unittest
from calendar import monthrange
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import duckdb

from xray import paths
from xray.scoring_io_v4 import (ASSESSMENT_SCHEMA, N_PAIRS_ESPERADOS,
                                _check_join_sets, assess_at_v4, run_score_v4)


class ScoringIoV4Test(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mapping = {'ROOT': self.root, 'WORKSPACE': self.root,
                   'MARTS_DIR': self.root / 'data/marts',
                   'CLEAN_DIR': self.root / 'data/clean',
                   'INTERIM_DIR': self.root / 'data/interim'}
        for folder in mapping.values():
            folder.mkdir(parents=True, exist_ok=True)
        patcher = patch.multiple(paths, **mapping)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)
        self.next_src_row = 0
        self.before = self.fingerprint()

    def fingerprint(self):
        directories = [paths.CLEAN_DIR, paths.INTERIM_DIR, paths.MARTS_DIR]
        return {str(file): hashlib.sha256(file.read_bytes()).hexdigest()
                for directory in directories for file in sorted(directory.rglob('*'))
                if file.is_file()}

    def parquet(self, relative, columns, rows):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        self.con.execute('CREATE OR REPLACE TEMP TABLE fixture (' + ','.join(
            f'{name} {dtype}' for name, dtype in columns) + ')')
        if rows:
            self.con.executemany(
                'INSERT INTO fixture VALUES (' + ','.join('?' for _ in columns) + ')',
                [tuple(row.get(name) for name, _ in columns) for row in rows])
        self.con.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)")

    def parquet_or_append(self, relative, columns, row,
                          key=('company_id', 'month')):
        file = self.root / relative
        if file.exists():
            existing = self.con.execute(f"SELECT * FROM read_parquet('{file}')").fetchall()
            names = [name for name, _ in columns]
            rows = [dict(zip(names, values)) for values in existing]
            if key is not None:
                def key_of(old):
                    return tuple(getattr(old.get(name), 'date', lambda: old.get(name))()
                                 for name in key)
                rows = [old for old in rows if key_of(old) != key_of(row)]
            rows.append(row)
        else:
            rows = [row]
        self.parquet(relative, columns, rows)

    def transaction(self, company_id, month, flow_class, amount, direction,
                    resolved_class=None):
        self.next_src_row += 1
        transaction_id = f'tx_{self.next_src_row:03d}'
        self.parquet_or_append('data/clean/transactions.parquet', [
            ('transaction_id', 'VARCHAR'), ('company_id', 'VARCHAR'),
            ('_src_row', 'BIGINT'), ('month', 'DATE'), ('is_booked', 'BOOLEAN'),
            ('is_open_month', 'BOOLEAN'), ('product_source', 'VARCHAR'),
            ('product_type', 'VARCHAR'), ('direction', 'VARCHAR'),
            ('amount_eur', 'DOUBLE')], {
            'transaction_id': transaction_id, 'company_id': company_id,
            '_src_row': self.next_src_row, 'month': month, 'is_booked': True,
            'is_open_month': False, 'product_source': 'banking',
            'product_type': 'checking', 'direction': direction,
            'amount_eur': amount}, key=None)
        self.parquet_or_append('data/interim/tx_flow_class.parquet', [
            ('_src_row', 'BIGINT'), ('transaction_id', 'VARCHAR'),
            ('flow_class', 'VARCHAR')], {
            '_src_row': self.next_src_row, 'transaction_id': transaction_id,
            'flow_class': flow_class}, key=None)
        if resolved_class is not None:
            self.parquet_or_append(
                'reports/transfer_resolution_v2/resolution.parquet', [
                ('transaction_id', 'VARCHAR'), ('resolved_class', 'VARCHAR'),
                ('rule', 'VARCHAR'), ('confidence', 'VARCHAR'),
                ('matched_with', 'VARCHAR')], {
                'transaction_id': transaction_id,
                'resolved_class': resolved_class, 'rule': 'test',
                'confidence': 'media', 'matched_with': transaction_id},
                key=('transaction_id',))

    def panel_row(self, company_id, group_id, month, activity=True,
                  primer_mes=None, volumen=0.0, servicio=None):
        end = month.replace(day=monthrange(month.year, month.month)[1])
        self.parquet_or_append('data/marts/panel_flujos.parquet', [
            ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
            ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
            ('primer_mes_caja', 'DATE'),
            ('volumen_caja_conocido_eur', 'DOUBLE')], {
            'company_id': company_id, 'group_id': group_id, 'month': month,
            'available_at': end, 'tiene_actividad_caja': activity,
            'primer_mes_caja': primer_mes,
            'volumen_caja_conocido_eur': volumen})
        self.parquet_or_append('data/marts/panel_deuda.parquet', [
            ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
            ('available_at', 'DATE'), ('servicio_deuda_eur', 'DOUBLE')], {
            'company_id': company_id, 'group_id': group_id, 'month': month,
            'available_at': end, 'servicio_deuda_eur': servicio})

    def debt_layer_row(self, company_id, month, obligacion=None, deficit=None,
                       multiplicador=None, confidence='ninguna',
                       month_as_string=False):
        """Capa A: month como TIMESTAMP (o STRING si month_as_string)."""
        self.parquet_or_append('reports/debt_obligation/debt_obligation_monthly.parquet', [
            ('company_id', 'VARCHAR'), ('month', 'TIMESTAMP'),
            ('obligacion_vencida_eur', 'DOUBLE'),
            ('deficit_servicio_eur', 'DOUBLE'),
            ('multiplicador_deuda', 'DOUBLE'), ('confidence', 'VARCHAR')], {
            'company_id': company_id, 'month': month, 'obligacion_vencida_eur': obligacion,
            'deficit_servicio_eur': deficit, 'multiplicador_deuda': multiplicador,
            'confidence': confidence})

    def cash_layer_row(self, company_id, month, saldo=1000.0, salida=400.0,
                       confidence='alta', month_as_string=False):
        """Capa B: month con dtype distinto (la capa lo guardo como STRING)."""
        columns = [('company_id', 'VARCHAR'),
                   ('month', 'VARCHAR' if month_as_string else 'DATE'),
                   ('saldo_reversa_eur', 'DOUBLE'),
                   ('salida_media_mensual_reversa', 'DOUBLE'),
                   ('confidence', 'VARCHAR')]
        self.parquet_or_append('reports/cash_backfill/cash_backfill_monthly.parquet',
                               columns, {
            'company_id': company_id,
            'month': month.isoformat() if month_as_string else month,
            'saldo_reversa_eur': saldo,
            'salida_media_mensual_reversa': salida,
            'confidence': confidence})

    def delay_layer_row(self, company_id, month, mora=None, confidence='alta'):
        """Capa C: month como TIMESTAMP, con mora_pago_robusta para el D3."""
        self.parquet_or_append(
            'reports/payment_delay_v2/payment_delay_v2_monthly.parquet', [
            ('company_id', 'VARCHAR'), ('month', 'TIMESTAMP'),
            ('mora_indice', 'DOUBLE'), ('mora_pago_robusta', 'DOUBLE'),
            ('confidence', 'VARCHAR')], {
            'company_id': company_id, 'month': month, 'mora_indice': mora,
            'mora_pago_robusta': mora, 'confidence': confidence})

    def fx_layer_row(self, company_id, month, indice=None, indice_min=None,
                     intervalo=False, confidence='ninguna'):
        """Capa F3 (factor FX): month como TIMESTAMP y columna extra.

        indice=None + intervalo=False -> exposicion desconocida (nulo nunca
        castiga). indice=0.0 -> empresa sin exposicion no-EUR (factor 1.0
        exacto). intervalo=True -> se usa indice_min (castigo minimo).
        """
        self.parquet_or_append('reports/fx_risk/fx_risk_monthly.parquet', [
            ('company_id', 'VARCHAR'), ('month', 'TIMESTAMP'),
            ('indice_fx', 'DOUBLE'), ('indice_fx_min', 'DOUBLE'),
            ('indice_es_intervalo', 'BOOLEAN'), ('confidence', 'VARCHAR')], {
            'company_id': company_id, 'month': month, 'indice_fx': indice,
            'indice_fx_min': indice_min, 'indice_es_intervalo': intervalo,
            'confidence': confidence})

    def layer_rows(self, company_id, month, obligacion=None, deficit=None,
                   multiplicador=None, mora=None):
        self.debt_layer_row(company_id, month, obligacion=obligacion,
                            deficit=deficit, multiplicador=multiplicador)
        self.cash_layer_row(company_id, month, month_as_string=True)
        self.delay_layer_row(company_id, month, mora=mora)
        self.fx_layer_row(company_id, month)

    def fixture(self, months):
        """Universo con una empresa activa (A) y otra sin actividad (B)."""
        self.parquet('data/clean/companies.parquet', [
            ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR')],
            [{'company_id': 'A', 'group_id': 'G'}, {'company_id': 'B', 'group_id': 'H'}])
        first = months[0]
        for month in months:
            self.panel_row('A', 'G', month, activity=True, primer_mes=first,
                           volumen=1000.0, servicio=100.0)
            self.panel_row('B', 'H', month, activity=False, primer_mes=None,
                           volumen=0.0, servicio=None)
            for target in ('A', 'B'):
                self.layer_rows(target, month)

    # --- EL MAS IMPORTANTE: point-in-time -----------------------------------
    def test_point_in_time_future_layers_do_not_change_score(self):
        july = date(2026, 7, 1)
        self.fixture([july])
        self.transaction('A', july, 'operating_in', 400.0, 'in', 'operating_in')
        self.transaction('A', july, 'operating_out', 250.0, 'out', 'operating_out')
        before = assess_at_v4(self.con, '2026-07-31')
        self.assertIsNotNone(before['assessments'][0]['health_score'])

        # Aparecen movimientos y capas DERIVADAS de agosto (futuro para el corte).
        august = date(2026, 8, 1)
        self.transaction('A', august, 'operating_in', 10_000_000.0, 'in', 'operating_in')
        self.transaction('A', august, 'operating_out', 9_000_000.0, 'out', 'operating_out')
        self.panel_row('A', 'G', august, activity=True, primer_mes=july,
                       volumen=27_000_000.0, servicio=100.0)
        self.layer_rows('A', august, obligacion=10_000_000.0,
                        multiplicador=0.5, mora=1.0)
        self.panel_row('B', 'H', august, activity=False, primer_mes=None,
                       volumen=0.0, servicio=None)
        self.layer_rows('B', august)
        after = assess_at_v4(self.con, '2026-07-31')
        self.assertEqual(before['assessments'], after['assessments'])
        self.assertEqual(before['summary'], after['summary'])

    # --- Join de capas: no pierde filas (obligatorio) ------------------------
    def test_join_layers_normalizes_month_dtype_and_keeps_all_pairs(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.debt_layer_row('A', month, obligacion=100.0)
        self.cash_layer_row('A', month, month_as_string=True)
        self.delay_layer_row('A', month, mora=0.2)
        self.debt_layer_row('B', month)
        self.cash_layer_row('B', month, month_as_string=True)
        self.delay_layer_row('B', month)
        result = assess_at_v4(self.con, '2026-08-31')
        # El join no pierde filas: la rejilla comun de meses cerrados es 2x1.
        self.assertEqual(result['summary']['pares_capas_join'], 2)
        # El dtype STRING de cash_backfill se normaliza: el saldo entra.
        for row in result['assessments']:
            if row['company_id'] == 'A':
                self.assertEqual(row['colchon_v4'], 1000.0)

    def test_check_join_sets_detects_missing_pairs(self):
        month = date(2026, 8, 1)
        layers = {'a': {('A', month): 1, ('B', month): 2},
                  'b': {('A', month): 3},
                  'c': {('A', month): 4, ('B', month): 5}}
        with self.assertRaises(ValueError) as context:
            _check_join_sets(layers)
        message = str(context.exception)
        self.assertIn('pierde filas', message)
        self.assertIn('b', message)

    def test_check_join_sets_accepts_identical_grids(self):
        month = date(2026, 8, 1)
        layers = {'a': {('A', month): 1}, 'b': {('A', month): 2}}
        self.assertEqual(_check_join_sets(layers), 1)

    def test_join_against_real_workspace_keeps_30864_pairs(self):
        """El join de las CUATRO capas reales conserva la rejilla completa."""
        repo = Path(__file__).resolve().parents[1]
        debt_path = repo / 'reports/debt_obligation/debt_obligation_monthly.parquet'
        cash_path = repo / 'reports/cash_backfill/cash_backfill_monthly.parquet'
        delay_path = repo / 'reports/payment_delay_v2/payment_delay_v2_monthly.parquet'
        fx_path = repo / 'reports/fx_risk/fx_risk_monthly.parquet'
        if not all(path.exists() for path in (debt_path, cash_path, delay_path)):
            self.skipTest('las capas v4 consolidadas no estan disponibles')
        con = duckdb.connect(':memory:')
        self.addCleanup(con.close)
        for path in (debt_path, cash_path, delay_path):
            n = con.execute(f'''
                SELECT count(*) FROM read_parquet('{path}')
                WHERE CAST(month AS DATE) <= DATE '2026-08-31'
            ''').fetchone()[0]
            self.assertEqual(n, N_PAIRS_ESPERADOS,
                             f'{path.name}: la capa perdio filas de la rejilla')
        rows = con.execute(f'''
            SELECT count(*) FROM (
                SELECT company_id, CAST(month AS DATE) AS month
                FROM read_parquet('{debt_path}')
                INTERSECT
                SELECT company_id, CAST(month AS DATE) AS month
                FROM read_parquet('{cash_path}')
                INTERSECT
                SELECT company_id, CAST(month AS DATE) AS month
                FROM read_parquet('{delay_path}')
            )
        ''').fetchone()[0]
        self.assertEqual(rows, N_PAIRS_ESPERADOS)
        # La capa F3 (factor FX) entra en el MISMO join con la MISMA rejilla:
        # 30.864 pares identicos, sin perder ni una fila.
        if not fx_path.exists():
            self.skipTest('la capa F3 de riesgo de divisa no esta disponible')
        n_fx = con.execute(f'''
            SELECT count(*) FROM read_parquet('{fx_path}')
            WHERE CAST(month AS DATE) <= DATE '2026-08-31'
        ''').fetchone()[0]
        self.assertEqual(n_fx, N_PAIRS_ESPERADOS)
        rows_fx = con.execute(f'''
            SELECT count(*) FROM (
                SELECT company_id, CAST(month AS DATE) AS month
                FROM read_parquet('{fx_path}')
                INTERSECT
                SELECT company_id, CAST(month AS DATE) AS month
                FROM read_parquet('{debt_path}')
            )
        ''').fetchone()[0]
        self.assertEqual(rows_fx, N_PAIRS_ESPERADOS)

    # --- Recomputo C/P y entrada al motor ------------------------------------
    def test_operating_in_and_out_add_to_c_and_p(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 400.0, 'in', 'operating_in')
        self.transaction('A', month, 'transfer', 300.0, 'out', 'operating_out')
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 400.0)
        self.assertEqual(result['p6'], 300.0)

    def test_internal_transfer_excluded_from_c_and_p(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 700.0, 'in', 'internal_transfer')
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)

    def test_unknown_inputs_passed_as_none_never_zero(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', None, 'in', 'operating_in')
        self.transaction('A', month, 'operating_out', None, 'out', 'operating_out')
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertIn('c_eur_desconocido_en_1_meses', result['reasons'])
        self.assertIn('p_eur_desconocido_en_1_meses', result['reasons'])
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)

    def test_v4_layers_reach_the_engine(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.transaction('A', month, 'operating_out', 250.0, 'out', 'operating_out')
        self.debt_layer_row('A', month, obligacion=500.0, deficit=10.0,
                            multiplicador=0.5)
        self.cash_layer_row('A', month, saldo=100.0, month_as_string=True)
        self.delay_layer_row('A', month, mora=0.2)
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['obligacion_vencida_m'], 500.0)
        self.assertEqual(result['deficit_servicio_6'], 10.0)
        self.assertEqual(result['multiplicador_deuda'], 0.5)
        self.assertEqual(result['colchon_v4'], 100.0)
        self.assertEqual(result['mora_indice'], 0.2)
        self.assertAlmostEqual(result['t6_efectivo'],
                               250.0 + 100.0 + 10.0 + 500.0)
        # Solo a la baja: mora 0.2 quita 5% y el multiplicador 0.5 la mitad.
        h = result['h_antes_de_ajustes']
        self.assertAlmostEqual(result['health_score'], h * 0.95 * 0.5)
        self.assertAlmostEqual(result['penalizacion_mora_puntos'], h * 0.05)
        self.assertAlmostEqual(result['penalizacion_multiplicador_puntos'],
                               h * 0.95 * 0.5)
        # B sin actividad y sin flujos: sin nota por falta de actividad.
        b = assess_at_v4(self.con, '2026-08-31')['assessments'][1]
        self.assertIsNone(b['health_score'])

    # --- Factor FX (capa F3): aplicacion, invariancia y flag ----------------
    def test_con_fx_aplica_factor_y_lo_declara(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.debt_layer_row('A', month, obligacion=500.0, multiplicador=0.5)
        self.delay_layer_row('A', month, mora=0.2)
        self.fx_layer_row('A', month, indice=1.0)
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['indice_fx'], 1.0)
        self.assertEqual(result['indice_fx_aplicado'], 1.0)
        self.assertFalse(result['indice_es_intervalo'])
        self.assertEqual(result['beta_fx'], 0.05)
        h = result['h_antes_de_ajustes']
        # mora 0.2 -> 5%; multiplicador 0.5 -> mitad; FX 1.0 -> 5% menos.
        self.assertAlmostEqual(result['health_score'], h * 0.95 * 0.5 * 0.95)
        self.assertAlmostEqual(result['penalizacion_fx_puntos'],
                               h * 0.95 * 0.5 * 0.05)
        self.assertIn('castigo_fx_indice_1.0000', result['reasons'])
        self.assertIn('castigo_fx_indice_1.0000',
                      assess_at_v4(self.con, '2026-08-31')['summary']['motivos'])

    def test_empresa_sin_exposicion_no_eur_bit_identica_con_fx(self):
        # Test de invariancia (el mas importante): con indice_fx = 0.0 la nota
        # es BIT-IDENTICA a la v4 sin capa FX; con indice nulo tambien (nulo
        # nunca castiga), aunque se registre el motivo.
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.debt_layer_row('A', month, obligacion=500.0, multiplicador=0.5)
        self.delay_layer_row('A', month, mora=0.2)
        self.fx_layer_row('A', month, indice=0.0, indice_min=0.0)
        con_cero = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(con_cero['indice_fx_aplicado'], 0.0)
        self.assertEqual(con_cero['penalizacion_fx_puntos'], 0.0)
        self.fx_layer_row('A', month, indice=None, indice_min=None)
        con_nulo = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertIsNone(con_nulo['indice_fx_aplicado'])
        self.assertEqual(con_nulo['penalizacion_fx_puntos'], 0.0)
        self.assertIn('indice_fx_desconocido', con_nulo['reasons'])
        sin_fx = assess_at_v4(self.con, '2026-08-31', con_fx=False)['assessments'][0]
        self.assertEqual(con_cero['health_score'], sin_fx['health_score'])
        self.assertEqual(con_nulo['health_score'], sin_fx['health_score'])

    def test_rama_intervalo_usa_indice_fx_min(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.transaction('A', month, 'operating_out', 250.0, 'out', 'operating_out')
        self.fx_layer_row('A', month, indice=None, indice_min=0.4,
                          intervalo=True)
        result = assess_at_v4(self.con, '2026-08-31')['assessments'][0]
        self.assertTrue(result['indice_es_intervalo'])
        self.assertIsNone(result['indice_fx'])
        self.assertEqual(result['indice_fx_min'], 0.4)
        self.assertEqual(result['indice_fx_aplicado'], 0.4)
        self.assertIn('castigo_fx_acotado_por_intervalo', result['reasons'])
        h = result['h_antes_de_ajustes']
        self.assertAlmostEqual(result['penalizacion_fx_puntos'], h * 0.05 * 0.4)

    def test_sin_fx_no_exige_la_capa_y_reproduce_la_nota(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.debt_layer_row('A', month, obligacion=500.0, multiplicador=0.5)
        self.delay_layer_row('A', month, mora=0.2)
        self.fx_layer_row('A', month, indice=1.0)
        antes = assess_at_v4(self.con, '2026-08-31', con_fx=False)
        self.assertEqual(antes['assessments'][0]['penalizacion_fx_puntos'], 0.0)
        self.assertIsNone(antes['assessments'][0]['indice_fx_aplicado'])
        # Sin la capa F3, --sin-fx sigue funcionando y da la MISMA nota.
        (self.root / 'reports/fx_risk/fx_risk_monthly.parquet').unlink()
        despues = assess_at_v4(self.con, '2026-08-31', con_fx=False)
        self.assertEqual(antes['assessments'], despues['assessments'])
        # Con --con-fx (por defecto) la ausencia de la capa SI es un error.
        with self.assertRaises(ValueError) as context:
            assess_at_v4(self.con, '2026-08-31')
        self.assertIn('Capa v4 ausente', str(context.exception))

    def test_summary_declara_factor_fx_y_beta(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        con_fx_output = self.root / 'reports/score_v4'
        report = run_score_v4(con_fx_output)
        self.assertTrue(report['factor_fx']['activado'])
        self.assertEqual(report['factor_fx']['beta_fx'], 0.05)
        self.assertEqual(report['params']['beta_fx'], 0.05)
        self.assertIn('- beta_fx*indice_fx', report['factor_fx']['formula'])
        self.assertIn('--sin-fx', report['factor_fx']['flag_desactivar'])
        sin_fx_output = self.root / 'reports/score_sin_fx'
        report_sin = run_score_v4(sin_fx_output, con_fx=False)
        self.assertFalse(report_sin['factor_fx']['activado'])
        rows = self.con.execute(
            f"SELECT count(DISTINCT beta_fx), count(indice_fx_aplicado) "
            f"FROM read_parquet('{sin_fx_output / 'assessments.parquet'}')"
        ).fetchone()
        self.assertEqual(rows, (1, 0))

    # --- Salida: esquema, resumen, CLI ---------------------------------------
    def test_parquet_schema_is_exactly_the_declared_one(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        output = self.root / 'reports/score_v4'
        run_score_v4(output)
        described = self.con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{output / 'assessments.parquet'}')"
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in described],
                         [(name, dtype) for name, dtype in ASSESSMENT_SCHEMA])

    def test_summary_counts_add_up_to_total(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        result = assess_at_v4(self.con, '2026-08-31')
        summary = result['summary']
        self.assertEqual(summary['total_companies'], 2)
        self.assertEqual(summary['n_con_nota']
                         + summary['n_sin_nota_por_falta_de_actividad']
                         + summary['n_sin_nota_por_pagos_no_demostrados']
                         + summary['n_sin_nota_por_sin_flujos'],
                         summary['total_companies'])
        self.assertEqual(sum(summary['reparto_confidence'].values()),
                         summary['total_companies'])
        # A cobra y no paga nada (p6 = 0) sin obligacion vencida: sin nota
        # por pagos_operativos_no_demostrados (fix P6 reaplicado). B sin
        # actividad: falta_de_actividad.
        self.assertEqual(summary['n_con_nota'], 0)
        self.assertEqual(summary['n_sin_nota_por_pagos_no_demostrados'], 1)
        self.assertEqual(summary['n_sin_nota_por_falta_de_actividad'], 1)

    def test_summary_counts_nueva_guarda_with_debt(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        self.debt_layer_row('A', month, obligacion=500.0, multiplicador=0.5)
        result = assess_at_v4(self.con, '2026-08-31')
        summary = result['summary']
        # Con la nueva guarda, p6 = 0 con obligacion vencida > 0 NO es sin
        # nota: la empresa debe y no paga, recibe una nota BAJA.
        self.assertEqual(summary['n_con_nota'], 1)
        self.assertEqual(summary['n_sin_nota_por_pagos_no_demostrados'], 0)
        guardada = result['assessments'][0]
        self.assertIsNotNone(guardada['health_score'])
        self.assertEqual(guardada['p6'], 0.0)
        self.assertEqual(guardada['obligacion_vencida_m'], 500.0)
        self.assertLess(guardada['health_score'], 50.0)

    def test_cli_aborts_when_output_exists(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        from xray.scoring_io_v4 import main
        output = self.root / 'reports/score_v4'
        errors, stdout = StringIO(), StringIO()
        with redirect_stderr(errors), redirect_stdout(stdout):
            self.assertEqual(main(['--output', str(output)]), 0)
        self.assertTrue((output / 'assessments.parquet').exists())
        (output / 'sentinel.txt').write_text('previo')
        errors, stdout = StringIO(), StringIO()
        with redirect_stderr(errors), redirect_stdout(stdout):
            self.assertEqual(main(['--output', str(output)]), 1)
        self.assertNotIn('Traceback', errors.getvalue())
        self.assertEqual((output / 'sentinel.txt').read_text(), 'previo')

    def test_cli_does_not_modify_sources(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        from xray.scoring_io_v4 import main
        before = self.fingerprint()
        errors, stdout = StringIO(), StringIO()
        with redirect_stderr(errors), redirect_stdout(stdout):
            self.assertEqual(main(['--output', str(self.root / 'reports/score_v4')]), 0)
        self.assertEqual(self.fingerprint(), before)

    def test_summary_is_strictly_serializable(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        output = self.root / 'reports/score_v4'
        run_score_v4(output)
        result = assess_at_v4(self.con, '2026-08-31')
        json.dumps(result['summary'], allow_nan=False)
        report = json.loads((output / 'summary.json').read_text())
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['model_version'], 'healthscore_v4')
        self.assertIn('PROVISIONALES', report['advertencia'])

    # --- Exclusion de nota 0 persistente (perimetro, reversible) -------------
    def fixture_excluible(self, months):
        """A recibe nota 0 exacta (multiplicador 0) en todos los meses: A y B.
        B sigue sin actividad (sin nota, SIN exclusion)."""
        self.fixture(months)
        for month in months:
            self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
            self.transaction('A', month, 'operating_out', 250.0, 'out', 'operating_out')
            self.debt_layer_row('A', month, multiplicador=0.0)

    def test_exclusion_applied_by_default_and_no_rows_deleted(self):
        months = [date(2026, 7, 1), date(2026, 8, 1)]
        self.fixture_excluible(months)
        output = self.root / 'reports/score_v4'
        report = run_score_v4(output)
        parquet = output / 'assessments.parquet'
        rows = self.con.execute(
            f"SELECT company_id, month, health_score, excluida, reasons "
            f"FROM read_parquet('{parquet}') ORDER BY company_id, month").fetchall()
        # NINGUNA fila se borra: 24 cortes x 2 empresas (todas las filas del grid).
        self.assertEqual(len(rows), 48)
        for company_id, month, health, excluida, reasons in rows:
            if company_id == 'A':
                # health_score = NULL, columna excluida = true y motivo en reasons.
                self.assertIsNone(health)
                self.assertTrue(excluida)
                self.assertIn('excluida_nota_cero_persistente', list(reasons))
            else:
                self.assertFalse(excluida)
        exclusion = report['exclusion']
        self.assertTrue(exclusion['activada'])
        self.assertEqual(exclusion['n_empresas_excluidas'], 1)
        self.assertEqual(exclusion['cifras_agregadas']['union_excluidas'], 1)
        self.assertIn('Exclusion por decision de producto', exclusion['declaracion'])
        self.assertIn('--no-excluir-cero-persistente', exclusion['declaracion'])
        # summary.json declara la poblacion y TODAS las cifras sin las excluidas.
        ultimo = report['ultimo_corte']
        self.assertEqual(ultimo['n_con_nota'], 0)
        self.assertEqual(ultimo['n_excluidas'], 1)
        self.assertEqual(ultimo['total_companies'], 2)
        self.assertIn('TODAS las cifras', ultimo['poblacion'])
        self.assertIn('declaracion_exclusion', ultimo)
        # por_corte tambien declara poblacion y excluidas por corte.
        for as_of, summary in report['por_corte'].items():
            self.assertEqual(summary['n_excluidas'], 1)
            self.assertIn('poblacion', summary)
        # exclusiones.json se escribe junto al parquet.
        exclusiones = json.loads((output / 'exclusiones.json').read_text())
        self.assertEqual(exclusiones['excluidas'][0]['company_id'], 'A')
        self.assertEqual(exclusiones['excluidas'][0]['criterio'], 'A y B')

    def test_reversibility_no_exclusion_reproduces_engine_output(self):
        months = [date(2026, 7, 1), date(2026, 8, 1)]
        self.fixture_excluible(months)
        base = assess_at_v4(self.con, '2026-08-31')
        output_a = self.root / 'reports/sin_exclusion_a'
        output_b = self.root / 'reports/sin_exclusion_b'
        report = run_score_v4(output_a, excluir_cero_persistente=False)
        run_score_v4(output_b, excluir_cero_persistente=False)
        # Determinista: dos runs sin exclusion, bytes identicos.
        self.assertEqual((output_a / 'assessments.parquet').read_bytes(),
                         (output_b / 'assessments.parquet').read_bytes())
        con = duckdb.connect(':memory:')
        self.addCleanup(con.close)
        rows = con.execute(f'''SELECT company_id, CAST(month AS DATE) AS month,
            health_score, excluida, CAST(as_of AS DATE) AS as_of
            FROM read_parquet('{output_a / 'assessments.parquet'}')''').fetchall()
        esperado = {(row['company_id'], row['month']): row['health_score']
                    for row in base['assessments']}
        self.assertEqual(len(rows), 48)
        for company_id, month, health, excluida, as_of in rows:
            self.assertFalse(excluida)
            if as_of == date(2026, 8, 31):
                self.assertEqual(health, esperado[(company_id, month)])
        # En el corte: mismas empresas con nota que sin el flag (A tenia nota 0).
        self.assertEqual(report['ultimo_corte']['n_con_nota'], 1)
        self.assertEqual(report['ultimo_corte']['n_excluidas'], 0)
        self.assertFalse(report['exclusion']['activada'])
        self.assertNotIn('declaracion_exclusion', report['ultimo_corte'])

    def test_cli_flags_toggle_exclusion(self):
        months = [date(2026, 7, 1), date(2026, 8, 1)]
        self.fixture_excluible(months)
        from xray.scoring_io_v4 import main
        sin = self.root / 'reports/sin'
        con = self.root / 'reports/con'
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(['--output', str(sin),
                                   '--no-excluir-cero-persistente']), 0)
            self.assertEqual(main(['--output', str(con)]), 0)
        def n_con_nota(path):
            con = duckdb.connect(':memory:')
            try:
                return con.execute(f'''SELECT count(health_score),
                    count(*) FILTER (WHERE excluida) FROM read_parquet('{path / 'assessments.parquet'}')
                    WHERE as_of = DATE '2026-08-31' ''').fetchone()
            finally:
                con.close()
        # Sin exclusion: nota para A (0.0), ninguna excluida.
        self.assertEqual(n_con_nota(sin), (1, 0))
        # Con exclusion (por defecto): health NULL, 1 excluida en el corte.
        self.assertEqual(n_con_nota(con), (0, 1))
        self.assertFalse((sin / 'exclusiones.json').exists())
        self.assertTrue((con / 'exclusiones.json').exists())

    def test_exclusion_matches_real_workspace_cifras(self):
        """Criterio de hecho: 30 A / 12 B / 33 union y efecto 904 -> 876."""
        repo = Path(__file__).resolve().parents[1]
        exclusiones_path = repo / 'reports/score_v4/exclusiones.json'
        parquet_path = repo / 'reports/score_v4/assessments.parquet'
        if not (exclusiones_path.exists() and parquet_path.exists()):
            self.skipTest('los artefactos v4 con exclusion no estan disponibles')
        report = json.loads(exclusiones_path.read_text())
        self.assertEqual(report['cifras_agregadas'], {
            'criterio_A': 30, 'criterio_B': 12, 'ambos': 9,
            'union_excluidas': 33,
            'fuente': 'reports/score_v4/assessments.parquet (pre-exclusion)'})
        efecto = report['efecto_corte_referencia']
        self.assertEqual(efecto['n_con_nota_antes'], 904)
        self.assertEqual(efecto['n_con_nota_despues'], 876)
        # La mediana pre-exclusion baja con el factor FX (beta_fx = 0.05);
        # la de despues no cambia porque la mediana del corte no tiene
        # exposicion no-EUR. Valores reproducidos al regenerar con --con-fx.
        self.assertAlmostEqual(efecto['mediana_antes'], 36.79072459519959, places=5)
        self.assertAlmostEqual(efecto['mediana_despues'], 37.79383963168285, places=5)
        con = duckdb.connect(':memory:')
        self.addCleanup(con.close)
        total = con.execute(
            f"SELECT count(*) FROM read_parquet('{parquet_path}')").fetchone()[0]
        self.assertEqual(total, 30864)
        corte = con.execute(f'''SELECT count(health_score), count(*) FILTER (WHERE excluida)
            FROM read_parquet('{parquet_path}') WHERE as_of = DATE '2026-08-31' ''').fetchone()
        self.assertEqual(corte, (876, 33))


if __name__ == '__main__':
    unittest.main()
