"""Tests del adaptador IO de healthscore_v3 (xray/scoring_io_v3.py).

El test point-in-time es el mas importante: los movimientos posteriores al
corte no pueden cambiar la nota de ese corte.
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
from xray.scoring_io_v3 import ASSESSMENT_SCHEMA, assess_at_v3, run_score_v3


class ScoringIoV3Test(unittest.TestCase):
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
            self.con.executemany('INSERT INTO fixture VALUES (' + ','.join('?' for _ in columns) + ')',
                                 [tuple(row.get(name) for name, _ in columns) for row in rows])
        self.con.execute(f"COPY fixture TO '{file}' (FORMAT PARQUET)")

    def parquet_or_append(self, relative, columns, row):
        file = self.root / relative
        if file.exists():
            existing = self.con.execute(f"SELECT * FROM read_parquet('{file}')").fetchall()
            names = [name for name, _ in columns]
            rows = [dict(zip(names, values)) for values in existing] + [row]
        else:
            rows = [row]
        self.parquet(relative, columns, rows)

    def transaction(self, company_id, month, flow_class, amount, direction,
                    resolved_class=None, resolution_confidence='media'):
        self.next_src_row += 1
        transaction_id = f'tx_{self.next_src_row:03d}'
        self.parquet_or_append('data/clean/transactions.parquet', [
            ('transaction_id', 'VARCHAR'), ('company_id', 'VARCHAR'), ('_src_row', 'BIGINT'),
            ('month', 'DATE'), ('is_booked', 'BOOLEAN'), ('is_open_month', 'BOOLEAN'),
            ('product_source', 'VARCHAR'), ('product_type', 'VARCHAR'),
            ('direction', 'VARCHAR'), ('amount_eur', 'DOUBLE')], {
            'transaction_id': transaction_id, 'company_id': company_id,
            '_src_row': self.next_src_row, 'month': month, 'is_booked': True,
            'is_open_month': False, 'product_source': 'banking', 'product_type': 'checking',
            'direction': direction, 'amount_eur': amount})
        self.parquet_or_append('data/interim/tx_flow_class.parquet', [
            ('_src_row', 'BIGINT'), ('transaction_id', 'VARCHAR'), ('flow_class', 'VARCHAR')], {
            '_src_row': self.next_src_row, 'transaction_id': transaction_id,
            'flow_class': flow_class})
        if resolved_class is not None:
            self.parquet_or_append('reports/transfer_resolution_v2/resolution.parquet', [
                ('transaction_id', 'VARCHAR'), ('resolved_class', 'VARCHAR'),
                ('rule', 'VARCHAR'), ('confidence', 'VARCHAR'), ('matched_with', 'VARCHAR')], {
                'transaction_id': transaction_id, 'resolved_class': resolved_class,
                'rule': 'test', 'confidence': resolution_confidence,
                'matched_with': transaction_id})

    def panel_row(self, company_id, group_id, month, activity=True, primer_mes=None,
                  volumen=0.0, servicio=None):
        end = month.replace(day=monthrange(month.year, month.month)[1])
        self.parquet_or_append('data/marts/panel_flujos.parquet', [
            ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
            ('available_at', 'DATE'), ('tiene_actividad_caja', 'BOOLEAN'),
            ('primer_mes_caja', 'DATE'), ('volumen_caja_conocido_eur', 'DOUBLE')], {
            'company_id': company_id, 'group_id': group_id, 'month': month,
            'available_at': end, 'tiene_actividad_caja': activity,
            'primer_mes_caja': primer_mes, 'volumen_caja_conocido_eur': volumen})
        self.parquet_or_append('data/marts/panel_deuda.parquet', [
            ('company_id', 'VARCHAR'), ('group_id', 'VARCHAR'), ('month', 'DATE'),
            ('available_at', 'DATE'), ('servicio_deuda_eur', 'DOUBLE')], {
            'company_id': company_id, 'group_id': group_id, 'month': month,
            'available_at': end, 'servicio_deuda_eur': servicio})

    def layer_rows(self, company_id, month, colchon=1000.0, salida=400.0,
                   cash_confidence='alta', mora=0.1, delay_confidence='alta'):
        self.parquet_or_append('reports/cash_position/cash_position_monthly.parquet', [
            ('company_id', 'VARCHAR'), ('month', 'DATE'), ('colchon', 'DOUBLE'),
            ('salida_media_mensual', 'DOUBLE'), ('confidence', 'VARCHAR')], {
            'company_id': company_id, 'month': month, 'colchon': colchon,
            'salida_media_mensual': salida, 'confidence': cash_confidence})
        self.parquet_or_append('reports/payment_delay/payment_delay_monthly.parquet', [
            ('company_id', 'VARCHAR'), ('month', 'TIMESTAMP'), ('mora_ratio', 'DOUBLE'),
            ('confidence', 'VARCHAR')], {
            'company_id': company_id, 'month': month, 'mora_ratio': mora,
            'confidence': delay_confidence})

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

    def test_point_in_time_future_movements_do_not_change_score(self):
        """EL MAS IMPORTANTE: lo posterior al corte no cambia la nota del corte."""
        july = date(2026, 7, 1)
        self.fixture([july])
        self.transaction('A', july, 'operating_in', 400.0, 'in', 'operating_in')
        self.transaction('A', july, 'operating_out', 250.0, 'out', 'operating_out')
        before = assess_at_v3(self.con, '2026-07-31')
        self.assertIsNotNone(before['assessments'][0]['health_score'])

        # Aparecen movimientos y capas DERIVADAS de agosto (futuro para el corte).
        august = date(2026, 8, 1)
        self.transaction('A', august, 'operating_in', 10_000_000.0, 'in', 'operating_in')
        self.transaction('A', august, 'operating_out', 9_000_000.0, 'out', 'operating_out')
        self.transaction('A', august, 'transfer', 8_000_000.0, 'in', 'ambiguo')
        self.panel_row('A', 'G', august, activity=True, primer_mes=july,
                       volumen=27_000_000.0, servicio=100.0)
        self.layer_rows('A', august, colchon=10_000_000.0, mora=1.0)
        self.panel_row('B', 'H', august, activity=False, primer_mes=None,
                       volumen=0.0, servicio=None)
        self.layer_rows('B', august)
        after = assess_at_v3(self.con, '2026-07-31')
        self.assertEqual(before['assessments'], after['assessments'])
        self.assertEqual(before['summary'], after['summary'])

    def test_internal_transfer_excluded_from_c_and_p(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 700.0, 'in', 'internal_transfer')
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)
        self.assertEqual(result['volumen_ambiguo_eur'], 0.0)

    def test_operating_in_and_out_add_to_c_and_p(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 400.0, 'in', 'operating_in')
        self.transaction('A', month, 'transfer', 300.0, 'out', 'operating_out')
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 400.0)
        self.assertEqual(result['p6'], 300.0)

    def test_ambiguous_accumulates_volume_and_no_total(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 200.0, 'in', 'ambiguo')
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)
        self.assertEqual(result['volumen_ambiguo_eur'], 200.0)
        self.assertAlmostEqual(result['volumen_ambiguo_pct'], 200.0 / 1000.0)

    def test_transaction_absent_from_resolution_keeps_original_class(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', None)
        self.transaction('A', month, 'financing_out', 300.0, 'out', None)
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        self.assertEqual(result['c6'], 400.0)
        self.assertEqual(result['p6'], 0.0)
        self.assertEqual(result['volumen_ambiguo_eur'], 0.0)

    def test_unresolved_transfer_keeps_original_class_like_today(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'transfer', 100.0, 'in', None)
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        # Clase original conservada: no suma a C ni a P, y como hoy deja C
        # desconocido (nunca cero). Solo las resueltas 'ambiguo' acumulan volumen.
        self.assertIn('c_eur_desconocido_en_1_meses', result['reasons'])
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)
        self.assertEqual(result['volumen_ambiguo_eur'], 0.0)

    def test_unknown_inputs_passed_as_none_never_zero(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', None, 'in', 'operating_in')
        self.transaction('A', month, 'operating_out', None, 'out', 'operating_out')
        result = assess_at_v3(self.con, '2026-08-31')['assessments'][0]
        # Si C o P se hubieran imputado a 0, el motor no registraria el motivo.
        self.assertIn('c_eur_desconocido_en_1_meses', result['reasons'])
        self.assertIn('p_eur_desconocido_en_1_meses', result['reasons'])
        self.assertEqual(result['c6'], 0.0)
        self.assertEqual(result['p6'], 0.0)
        # La confianza baja por entradas desconocidas (deduccion del motor).
        self.assertEqual(result['confidence'], 'ninguna')

    def test_parquet_schema_is_exactly_the_declared_one(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        output = self.root / 'reports/score_v3'
        run_score_v3(output)
        described = self.con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{output / 'assessments.parquet'}')"
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in described],
                         [(name, dtype) for name, dtype in ASSESSMENT_SCHEMA])

    def test_summary_counts_add_up_to_total(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        result = assess_at_v3(self.con, '2026-08-31')
        summary = result['summary']
        self.assertEqual(summary['total_companies'], 2)
        self.assertEqual(summary['n_con_nota']
                         + summary['n_sin_nota_por_falta_de_actividad']
                         + summary['n_sin_nota_por_denominador_nulo'],
                         summary['total_companies'])
        self.assertEqual(sum(summary['reparto_confidence'].values()),
                         summary['total_companies'])
        # A tiene un unico cobro y ningun pago ni servicio de deuda en la
        # ventana recomputada: con el bug pre-fix puntua (la nota 100 sin
        # pagos operativos que el fix de P6 bloquearia). B no tiene actividad
        # ni flujos: falta_de_actividad.
        self.assertEqual(summary['n_con_nota'], 1)
        self.assertEqual(summary['n_sin_nota_por_falta_de_actividad'], 1)
        self.assertEqual(summary['n_sin_nota_por_denominador_nulo'], 0)

    def test_cli_aborts_when_output_exists(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        from xray.scoring_io_v3 import main
        output = self.root / 'reports/score_v3'
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
        from xray.scoring_io_v3 import main
        before = self.fingerprint()
        errors, stdout = StringIO(), StringIO()
        with redirect_stderr(errors), redirect_stdout(stdout):
            self.assertEqual(main(['--output', str(self.root / 'reports/score_v3')]), 0)
        self.assertEqual(self.fingerprint(), before)

    def test_summary_is_strictly_serializable(self):
        month = date(2026, 8, 1)
        self.fixture([month])
        self.transaction('A', month, 'operating_in', 400.0, 'in', 'operating_in')
        output = self.root / 'reports/score_v3'
        run_score_v3(output)
        result = assess_at_v3(self.con, '2026-08-31')
        json.dumps(result['summary'], allow_nan=False)
        report = json.loads((output / 'summary.json').read_text())
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['model_version'], 'healthscore_v3')
        self.assertIn('sin calibrar', report['advertencia'])
