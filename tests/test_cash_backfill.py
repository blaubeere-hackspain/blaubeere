"""Tests de la reconstruccion de caja hacia atras desde el saldo ancla (Capa B v4)."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from xray import paths
from xray.cash_backfill import (
    AGUJEROS_EN_TRAMO,
    ANCHOR_PRODUCTS,
    BACKFILL_CASH_PRODUCTS,
    SIN_ANCLA,
    SIN_SALIDAS,
    reconstruct_cash_backfill,
)


def flujo(company, month, neto, desglose=None, conocido=None, agujero=0.0, salidas=0.0):
    desglose = dict(desglose) if desglose else {}
    return {
        'company_id': company,
        'month': date(*month),
        'flujo_neto': neto,
        'desglose': desglose,
        'volumen_conocido': conocido if conocido is not None else sum(abs(v) for v in desglose.values()),
        'volumen_agujero': agujero,
        'salidas_conocidas': salidas,
    }


class IdentidadAlgebraicaTest(unittest.TestCase):
    """C4: con flujos y ancla conocidos, saldo_reversa coincide con el calculo a mano."""

    def _serie(self):
        rows = [
            flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, salidas=0.0),
            flujo('A', (2025, 2, 1), -300.0, {'operating_out': -300.0}, salidas=300.0),
            flujo('A', (2025, 3, 1), 200.0, {'operating_in': 200.0}, salidas=0.0),
            flujo('A', (2025, 4, 1), -500.0, {'operating_out': -500.0}, salidas=500.0),
        ]
        return reconstruct_cash_backfill(
            rows, {'A': 10000.0}, companies=['A'],
            first_month=date(2025, 1, 1), anchor_month=date(2025, 5, 1))['A']['rows']

    def test_saldo_reversa_ancla_y_valores_a_mano(self):
        serie = self._serie()
        self.assertEqual([row['month'] for row in serie],
                         [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1),
                          date(2025, 4, 1), date(2025, 5, 1)])
        # saldo_reversa(T) == saldo_ancla en el bucket del ancla.
        self.assertEqual(serie[-1]['saldo_reversa_eur'], 10000.0)
        self.assertEqual(serie[-1]['saldo_reversa_eur'], serie[-1]['saldo_ancla_eur'])
        # Valores a mano: ancla - suma de flujos en (t, T].
        self.assertEqual([row['saldo_reversa_eur'] for row in serie],
                         [10600.0, 10300.0, 10500.0, 10000.0, 10000.0])
        # El ancla es el cierre del ultimo mes cerrado: el ultimo mes cerrado tambien vale el ancla.
        self.assertEqual(serie[-2]['saldo_reversa_eur'], 10000.0)
        # Y la identidad con la definicion forward: reversa = ancla - (F(Apr) - F(m)).
        self.assertEqual(serie[0]['flujo_neto_tramo'], -600.0)
        self.assertEqual(serie[3]['flujo_neto_tramo'], 0.0)

    def test_colchon_y_cobertura_a_mano(self):
        serie = self._serie()
        self.assertEqual([row['colchon_reversa_eur'] for row in serie],
                         [0.0, 0.0, 200.0, 0.0, 0.0])
        self.assertIsNone(serie[0]['meses_de_cobertura_reversa'])
        self.assertEqual(serie[0]['motivo_cobertura'], SIN_SALIDAS)
        # salidas de enero..marzo = [0, 300, 0]: media 100 EUR; colchon 200 -> 2,0 meses.
        self.assertEqual(serie[2]['meses_de_cobertura_reversa'], round(200.0 / 100.0, 6))
        for row in serie:
            self.assertEqual(row['confidence'], 'alta')


class EmpresaFueraDelAnclaTest(unittest.TestCase):
    """C5: sin ancla no hay saldo inventado: NULL y confidence 'ninguna', nunca cero."""

    def test_ancla_ausente_produce_null(self):
        rows = [
            flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}),
            flujo('A', (2025, 2, 1), -400.0, {'operating_out': -400.0}, salidas=400.0),
        ]
        out = reconstruct_cash_backfill(
            rows, {'A': 5000.0}, companies=['A', 'Z'],
            first_month=date(2025, 1, 1), anchor_month=date(2025, 3, 1))
        self.assertEqual(out['A']['ancla'], 5000.0)
        registro = out['Z']
        self.assertEqual(registro['reason'], SIN_ANCLA)
        self.assertEqual(registro['ancla'], None)
        for row in registro['rows']:
            self.assertIsNone(row['saldo_reversa_eur'])
            self.assertIsNone(row['colchon_reversa_eur'])
            self.assertIsNone(row['meses_de_cobertura_reversa'])
            self.assertEqual(row['confidence'], 'ninguna')
            self.assertEqual(row['motivo_cobertura'], SIN_ANCLA)
            self.assertIn('sin_ancla', row['flags'])
            self.assertNotEqual(row['saldo_reversa_eur'], 0.0)
        json.dumps(registro, allow_nan=False, default=str)

    def test_ancla_nulo_se_trata_como_ausente(self):
        rows = [flujo('A', (2025, 1, 1), 10.0, {'operating_in': 10.0})]
        out = reconstruct_cash_backfill(
            rows, {'A': None}, first_month=date(2025, 1, 1), anchor_month=date(2025, 2, 1))
        self.assertIsNone(out['A']['rows'][0]['saldo_reversa_eur'])
        self.assertEqual(out['A']['rows'][0]['confidence'], 'ninguna')


class AgujerosFxTest(unittest.TestCase):
    """C4/C5: un agujero de FX en el tramo deja el saldo en NULL y lo declara."""

    def _serie(self):
        rows = [
            flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, conocido=1000.0, salidas=0.0),
            flujo('A', (2025, 2, 1), -300.0, {'operating_out': -300.0}, conocido=300.0, salidas=300.0),
            flujo('A', (2025, 3, 1), 200.0, {'operating_in': 200.0}, conocido=200.0, agujero=50.0),
            flujo('A', (2025, 4, 1), -500.0, {'operating_out': -500.0}, conocido=500.0, salidas=500.0),
        ]
        return reconstruct_cash_backfill(
            rows, {'A': 10000.0}, first_month=date(2025, 1, 1), anchor_month=date(2025, 5, 1))['A']['rows']

    def test_agujero_anula_el_saldo_del_tramo(self):
        serie = self._serie()
        # El agujero esta en 2025-03: los tramos de enero y febrero lo incluyen -> NULL.
        for row in serie[:2]:
            self.assertIsNone(row['saldo_reversa_eur'])
            self.assertEqual(row['motivo_cobertura'], AGUJEROS_EN_TRAMO)
            self.assertIn('agujeros_en_tramo', row['flags'])
            self.assertIn(row['confidence'], ('media', 'baja', 'ninguna'))
            self.assertNotEqual(row['confidence'], 'alta')
        # Desde 2025-03 el agujero queda fuera del tramo -> saldo conocido.
        self.assertIsNotNone(serie[2]['saldo_reversa_eur'])
        self.assertEqual(serie[2]['confidence'], 'alta')
        self.assertEqual(serie[4]['saldo_reversa_eur'], 10000.0)
        for row in serie:
            json.dumps(row, allow_nan=False, default=str)
            self.assertNotIn('inf', json.dumps(row, default=str).lower())


class ContratoProductosTest(unittest.TestCase):
    def test_ancla_solo_caja_real(self):
        self.assertEqual(ANCHOR_PRODUCTS, ('checking', 'wallet', 'saving', 'tpv'))
        self.assertEqual(BACKFILL_CASH_PRODUCTS, ANCHOR_PRODUCTS)
        for deuda in ('loan', 'card', 'lineofcredit', 'confirming', 'investment', 'leasing'):
            self.assertNotIn(deuda, ANCHOR_PRODUCTS)


class PurezaTest(unittest.TestCase):
    def test_no_muta_entrada(self):
        rows = [flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0})]
        original = json.dumps(rows, sort_keys=True, default=str)
        out = reconstruct_cash_backfill(
            rows, {'A': 10.0}, first_month=date(2025, 1, 1), anchor_month=date(2025, 2, 1))
        self.assertEqual(json.dumps(rows, sort_keys=True, default=str), original)
        json.dumps(out, allow_nan=False, default=str)

    def test_mes_fuera_del_tramo_cerrado_falla(self):
        rows = [flujo('A', (2025, 6, 1), 1000.0, {'operating_in': 1000.0})]
        with self.assertRaises(ValueError):
            reconstruct_cash_backfill(
                rows, {'A': 1.0}, first_month=date(2025, 1, 1), anchor_month=date(2025, 3, 1))


class CliTest(unittest.TestCase):
    """El CLI genera artefactos, aborta sobre rutas existentes y no toca fuentes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cash-backfill-test-')
        cls.output = Path(cls.tmp.name) / 'cash_backfill'
        fuentes = [paths.CLEAN_DIR / f'{n}.parquet'
                   for n in ('transactions', 'companies', 'banking_products', 'balances')]
        fuentes += [paths.INTERIM_DIR / f'{n}.parquet' for n in
                    ('transactions', 'companies', 'banking_products', 'debt_products', 'debt_schedule_config',
                     'groups', 'invoices', 'tx_flow_class')]
        fuentes += [paths.MARTS_DIR / f'{n}.parquet' for n in
                    ('fx_rates', 'observabilidad', 'panel_cobro', 'panel_deuda', 'panel_evidencia', 'panel_flujos')]
        fuentes += [paths.ROOT / 'reports/cash_position/cash_position_monthly.parquet']
        cls.fuentes = fuentes

        def huellas():
            def sha(file):
                with file.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            return {f.name: sha(f) for f in fuentes}

        cls.antes = huellas()
        resultado = subprocess.run(
            [sys.executable, '-B', '-m', 'xray.cash_backfill', '--output', str(cls.output)],
            capture_output=True, text=True, cwd=paths.ROOT, timeout=900)
        cls.resumen = None
        if resultado.returncode == 0:
            cls.resumen = json.loads(resultado.stdout)
        else:
            cls.fallo = resultado.stderr
        cls.despues = huellas()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cli_genera_y_termina_cero(self):
        self.assertIsNone(getattr(self, 'fallo', None))
        self.assertTrue(self.output.exists())
        self.assertTrue((self.output / 'cash_backfill_monthly.parquet').exists())
        coverage = json.loads((self.output / 'coverage.json').read_text())
        self.assertEqual(coverage['empresas']['universo'], 1286)
        self.assertGreater(coverage['empresas']['en_el_ancla'], 0)
        self.assertEqual(coverage['ancla']['productos'], list(ANCHOR_PRODUCTS))
        diagnostic = coverage['diagnostico_comparativo_v3']
        self.assertTrue(diagnostic['disponible'])
        self.assertGreater(diagnostic['empresa_mes_comparables'], 0)
        self.assertIn('cambio_de_signo_nivel', diagnostic)
        self.assertIn('honestidad', coverage)
        self.assertIn('ancla futura', coverage['honestidad']['texto'])
        self.assertIsNotNone(self.resumen)

    def test_cli_aborta_si_la_salida_existe(self):
        repeticion = subprocess.run(
            [sys.executable, '-B', '-m', 'xray.cash_backfill', '--output', str(self.output)],
            capture_output=True, text=True, cwd=paths.ROOT, timeout=900)
        self.assertNotEqual(repeticion.returncode, 0)
        self.assertIn('ya existe', repeticion.stderr)

    def test_cli_no_cambia_las_fuentes(self):
        self.assertEqual(self.antes, self.despues)


if __name__ == '__main__':
    unittest.main()
