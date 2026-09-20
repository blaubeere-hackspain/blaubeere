"""Tests de la reconstruccion point-in-time de la posicion de caja (sin ancla)."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from xray import paths
from xray.cash_position import reconstruct_cash_position, SIN_ACTIVIDAD, SIN_SALIDAS


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


class ManualCaseTest(unittest.TestCase):
    def test_caso_manual_tres_meses(self):
        """C1/C2: tres meses con flujos conocidos -> acumulado y colchon exactos."""
        rows = [
            flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, salidas=0.0),
            flujo('A', (2025, 2, 1), -1800.0, {'operating_out': -1800.0}, salidas=1800.0),
            flujo('A', (2025, 3, 1), 400.0, {'operating_in': 400.0}, salidas=0.0),
        ]
        out = reconstruct_cash_position(rows)
        serie = out['A']['rows']
        self.assertEqual([row['posicion_acumulada'] for row in serie], [1000.0, -800.0, -400.0])
        self.assertEqual([row['colchon'] for row in serie], [0.0, 0.0, 400.0])
        self.assertEqual([row['minimo_acumulado_hasta_m'] for row in serie], [1000.0, -800.0, -800.0])
        self.assertEqual(serie[0]['mes_origen'], date(2025, 1, 1))
        self.assertEqual([row['n_meses_acumulados'] for row in serie], [1, 2, 3])
        self.assertEqual([row['desglose'].get('operating_in', 0.0) for row in serie], [1000.0, 0.0, 400.0])
        self.assertEqual([row['desglose'].get('operating_out', 0.0) for row in serie], [0.0, -1800.0, 0.0])


class PointInTimeTest(unittest.TestCase):
    def test_movimientos_posteriores_no_cambian_cortes_anteriores(self):
        """C2 point-in-time: datos posteriores al corte no alteran el corte."""
        base = [
            flujo('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, conocido=1000.0, salidas=0.0),
            flujo('A', (2025, 2, 1), -300.0, {'operating_out': -300.0}, conocido=300.0, salidas=300.0),
        ]
        antes = reconstruct_cash_position(base)['A']['rows']
        posterior = base + [
            flujo('A', (2025, 3, 1), 99999.0, {'operating_in': 99999.0}, conocido=99999.0, salidas=0.0),
            flujo('A', (2025, 4, 1), -77777.0, {'operating_out': -77777.0}, conocido=77777.0, salidas=77777.0),
        ]
        despues = reconstruct_cash_position(posterior)['A']['rows']
        self.assertEqual(len(antes), 2)
        for fila_antes, fila_despues in zip(antes, despues):
            self.assertEqual(json.dumps(fila_antes, sort_keys=True, default=str),
                             json.dumps(fila_despues, sort_keys=True, default=str))
        self.assertEqual(len(despues), 4)


class AgujeroFxTest(unittest.TestCase):
    def test_agujero_ensancha_la_banda_y_no_es_cero(self):
        """C4: un agujero de FX ensancha [posicion_min, posicion_max]."""
        rows = [
            flujo('A', (2025, 1, 1), 100.0, {'operating_in': 100.0}, conocido=100.0),
            flujo('A', (2025, 2, 1), 50.0, {'operating_in': 50.0}, conocido=50.0, agujero=800.0),
        ]
        serie = reconstruct_cash_position(rows)['A']['rows']
        primera, segunda = serie
        self.assertEqual((primera['posicion_min'], primera['posicion_max']), (100.0, 100.0))
        # 150 conocidos acumulados; el agujero de 800 ensancha la banda, no suma.
        self.assertEqual(segunda['posicion_acumulada'], 150.0)
        self.assertEqual(segunda['posicion_min'], 150.0 - 800.0)
        self.assertEqual(segunda['posicion_max'], 150.0 + 800.0)
        self.assertIn('agujeros_en_tramo', segunda['flags'])
        self.assertEqual(segunda['confidence'], 'ninguna')


class PosicionNegativaTest(unittest.TestCase):
    def test_posicion_negativa_se_conserva(self):
        """C5: la posicion acumulada negativa no se recorta a cero."""
        rows = [
            flujo('A', (2025, 1, 1), 100.0, {'operating_in': 100.0}, conocido=100.0, salidas=0.0),
            flujo('A', (2025, 2, 1), -900.0, {'operating_out': -900.0}, conocido=900.0, salidas=900.0),
        ]
        serie = reconstruct_cash_position(rows)['A']['rows']
        self.assertEqual(serie[-1]['posicion_acumulada'], -800.0)
        self.assertEqual(serie[-1]['minimo_acumulado_hasta_m'], -800.0)
        self.assertIn('posicion_negativa', serie[-1]['flags'])


class SalidaCeroTest(unittest.TestCase):
    def test_salida_media_cero_cobertura_null_con_motivo(self):
        """C5: salida_media_mensual = 0 -> cobertura null con motivo, nunca infinito."""
        rows = [
            flujo('A', (2025, 1, 1), 500.0, {'operating_in': 500.0}, conocido=500.0, salidas=0.0),
        ]
        serie = reconstruct_cash_position(rows)['A']['rows']
        fila = serie[-1]
        self.assertIsNone(fila['meses_de_cobertura'])
        self.assertEqual(fila['motivo_cobertura'], SIN_SALIDAS)
        self.assertEqual(fila['reason'], SIN_SALIDAS)
        self.assertIn('sin_salidas_conocidas', fila['flags'])
        self.assertNotIn('inf', json.dumps(fila, default=str).lower())
        json.dumps(fila, allow_nan=False, default=str)


class SinActividadTest(unittest.TestCase):
    def test_empresa_sin_movimientos_razon_explicita(self):
        """C5: sin reconstruccion y sin cifras inventadas para el motivo dado."""
        rows = [flujo('A', (2025, 1, 1), 10.0, {'operating_in': 10.0}, conocido=10.0)]
        out = reconstruct_cash_position(rows, companies=['A', 'Z'])
        registro = out['Z']
        self.assertEqual(registro['reason'], SIN_ACTIVIDAD)
        self.assertEqual(registro['rows'], [])
        json.dumps(registro, allow_nan=False)


class ColchonTest(unittest.TestCase):
    def test_colchon_y_minimo_no_creciente(self):
        """C3/C5: colchon = posicion - minimo; el minimo es no creciente."""
        rows = [
            flujo('A', (2025, 1, 1), 100.0, {'operating_in': 100.0}, conocido=100.0, salidas=10.0),
            flujo('A', (2025, 2, 1), -300.0, {'operating_out': -300.0}, conocido=300.0, salidas=300.0),
            flujo('A', (2025, 3, 1), 0.0, {}, conocido=0.0, salidas=0.0),
            flujo('A', (2025, 4, 1), 900.0, {'operating_in': 900.0}, conocido=900.0, salidas=0.0),
            flujo('A', (2025, 5, 1), -200.0, {'operating_out': -200.0}, conocido=200.0, salidas=200.0),
        ]
        serie = reconstruct_cash_position(rows)['A']['rows']
        posiciones = [row['posicion_acumulada'] for row in serie]
        minimos = [row['minimo_acumulado_hasta_m'] for row in serie]
        self.assertEqual(len(serie), 5)
        self.assertEqual(serie[2]['n_meses_con_actividad'], 3)  # mes vacio contado aparte
        for row, posicion in zip(serie, posiciones):
            self.assertEqual(row['colchon'], round(posicion - row['minimo_acumulado_hasta_m'], 6))
        for antes, despues in zip(minimos, minimos[1:]):
            self.assertLessEqual(despues, antes)
        self.assertEqual(serie[3]['n_meses_acumulados'], 4)


class EscalaTest(unittest.TestCase):
    def test_invariancia_de_escala_en_cobertura(self):
        """C3: multiplicar importes por k no cambia meses_de_cobertura."""
        base = [
            flujo('A', (2025, 1, 1), 1200.0, {'operating_in': 1200.0}, conocido=1200.0, salidas=0.0),
            flujo('A', (2025, 2, 1), -800.0, {'operating_out': -800.0}, conocido=800.0, salidas=800.0),
            flujo('A', (2025, 3, 1), -200.0, {'operating_out': -200.0}, conocido=200.0, salidas=200.0),
        ]
        escalado = []
        for row in base:
            copia = flujo(row['company_id'], (row['month'].year, row['month'].month, 1),
                          row['flujo_neto'] * 1.3, {k: v * 1.3 for k, v in row['desglose'].items()},
                          conocido=row['volumen_conocido'] * 1.3, salidas=row['salidas_conocidas'] * 1.3)
            escalado.append(copia)
        original = reconstruct_cash_position(base)['A']['rows'][-1]
        grande = reconstruct_cash_position(escalado)['A']['rows'][-1]
        self.assertAlmostEqual(original['meses_de_cobertura'], grande['meses_de_cobertura'], places=6)
        # Los EUR absolutos si escalan; la ratio no.
        self.assertAlmostEqual(original['colchon'] * 1.3, grande['colchon'], places=6)


class PurezaTest(unittest.TestCase):
    def test_no_muta_entrada_y_serializa_estrecho(self):
        """C motor puro: no muta la entrada y serializa con allow_nan=False."""
        rows = [
            flujo('A', (2025, 1, 1), -1000.0, {'operating_out': -1000.0}, conocido=1000.0, salidas=1000.0),
            flujo('A', (2025, 2, 1), 800.0, {'operating_in': 800.0}, conocido=800.0, salidas=0.0),
            flujo('A', (2025, 3, 1), -200.0, {'operating_out': -200.0}, conocido=200.0, salidas=200.0),
        ]
        original = json.dumps(rows, sort_keys=True, default=str)
        out = reconstruct_cash_position(rows, ['A'])
        self.assertEqual(json.dumps(rows, sort_keys=True, default=str), original)
        json.dumps(out, allow_nan=False, default=str)
        # colchon 800 frente a salidas medias 500 -> 1.6; y 600/400 -> 1.5.
        self.assertEqual(out['A']['rows'][1]['meses_de_cobertura'], 1.6)
        self.assertEqual(out['A']['rows'][2]['meses_de_cobertura'], 1.5)


class CliTest(unittest.TestCase):
    """El CLI genera artefactos, aborta sobre rutas existentes y no toca fuentes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cash-position-test-')
        cls.output = Path(cls.tmp.name) / 'cash_position'
        fuentes = [paths.CLEAN_DIR / f'{n}.parquet' for n in ('transactions', 'companies', 'banking_products', 'balances')]
        fuentes += [paths.INTERIM_DIR / f'{n}.parquet' for n in
                    ('transactions', 'companies', 'banking_products', 'debt_products', 'debt_schedule_config',
                     'groups', 'invoices', 'tx_flow_class')]
        fuentes += [paths.MARTS_DIR / f'{n}.parquet' for n in
                    ('fx_rates', 'observabilidad', 'panel_cobro', 'panel_deuda', 'panel_evidencia',
                     'panel_flujos', 'targets_proxy')]
        cls.fuentes = fuentes

        def huellas():
            return {f.name: hashlib.file_digest(f.open('rb'), 'sha256').hexdigest() for f in fuentes}

        cls.antes = huellas()
        resultado = subprocess.run(
            [sys.executable, '-B', '-m', 'xray.cash_position', '--output', str(cls.output)],
            capture_output=True, text=True, cwd=paths.ROOT, timeout=600)
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
        self.assertTrue((self.output / 'cash_position_monthly.parquet').exists())
        coverage = json.loads((self.output / 'coverage.json').read_text())
        self.assertEqual(coverage['empresas']['universo'], 1286)
        self.assertGreater(coverage['empresas']['con_serie'], 0)
        self.assertIn('mediana', coverage['meses_de_cobertura']['empresa_mes'])
        self.assertIn('diagnostico_balances', coverage)
        self.assertIsNotNone(self.resumen)

    def test_cli_aborta_si_la_salida_existe(self):
        repeticion = subprocess.run(
            [sys.executable, '-B', '-m', 'xray.cash_position', '--output', str(self.output)],
            capture_output=True, text=True, cwd=paths.ROOT, timeout=600)
        self.assertNotEqual(repeticion.returncode, 0)
        self.assertIn('ya existe', repeticion.stderr)

    def test_cli_no_cambia_las_fuentes(self):
        self.assertEqual(self.antes, self.despues)


if __name__ == '__main__':
    unittest.main()
