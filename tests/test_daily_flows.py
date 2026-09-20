"""Tests del panel diario point-in-time de flujo y saldo (Capa diaria v4)."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from xray import paths
from xray.daily_flows import (
    AGUJEROS_EN_TRAMO,
    ANCHOR_DAY,
    ANCHOR_PRODUCTS,
    CASH_PRODUCTS,
    CONTAMINACION_MATERIAL,
    DECISION_GRANO,
    SIN_ANCLA,
    build_anomaly_census,
    build_comp0413,
    load_anchor_balances,
    panel_hasta,
    reconstruct_daily_flows,
    render_report,
    summarize_density,
)


def fila(company, day, neto, desglose=None, conocido=None, agujero=0.0, salidas=0.0,
         n_transacciones=1, n_futura=0):
    desglose = dict(desglose) if desglose else {}
    entradas = sum(value for value in desglose.values() if value > 0)
    salidas_desglose = -sum(value for value in desglose.values() if value < 0)
    return {
        'company_id': company,
        'day': date(*day),
        'flujo_neto': neto,
        'entradas': entradas,
        'salidas': salidas_desglose if salidas == 0 else salidas,
        'n_transacciones': n_transacciones,
        'n_fecha_valor_futura': n_futura,
        'volumen_conocido': conocido if conocido is not None else sum(abs(v) for v in desglose.values()),
        'volumen_agujero': agujero,
        'salidas_conocidas': salidas if salidas else salidas_desglose,
        'desglose': desglose,
    }


def _serie():
    rows = [
        fila('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, salidas=0.0),
        fila('A', (2025, 1, 3), -300.0, {'operating_out': -300.0}, salidas=300.0),
        fila('A', (2025, 1, 4), 200.0, {'operating_in': 200.0}, salidas=0.0),
    ]
    return reconstruct_daily_flows(
        rows, {'A': 10000.0}, companies=['A'],
        anchor_day=date(2025, 1, 5))['A']['rows']


class IdentidadAlgebraicaTest(unittest.TestCase):
    def test_saldo_reversa_y_colchon_a_mano(self):
        serie = _serie()
        self.assertEqual([row['day'] for row in serie],
                         [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3),
                          date(2025, 1, 4), date(2025, 1, 5)])
        # saldo_reversa(d) = ancla - suma de flujos en (d, T].
        self.assertEqual([row['saldo_reversa_eur'] for row in serie],
                         [10100.0, 10100.0, 9800.0, 10000.0, 10000.0])
        self.assertEqual([row['flujo_neto_tramo'] for row in serie],
                         [-100.0, -100.0, 200.0, 0.0, 0.0])
        # Identidad del ancla leida literalmente en el artefacto.
        self.assertEqual(serie[-1]['saldo_reversa_eur'], serie[-1]['saldo_ancla_eur'])
        self.assertEqual(serie[-1]['saldo_reversa_eur'], 10000.0)
        self.assertTrue(serie[-1]['es_dia_ancla'])
        # Colchon = saldo - minimo hasta d.
        self.assertEqual([row['colchon_reversa_eur'] for row in serie],
                         [0.0, 0.0, 0.0, 200.0, 200.0])
        for row in serie:
            self.assertEqual(row['confidence'], 'alta')

    def test_entradas_menos_salidas_es_el_flujo_neto(self):
        for row in _serie():
            if row['tiene_movimiento']:
                self.assertAlmostEqual(row['entradas'] - row['salidas'], row['flujo_neto'], places=9)


class RejillaDensaTest(unittest.TestCase):
    def test_dias_sin_movimiento_no_son_flujo_cero_con_movimiento(self):
        serie = _serie()
        dia_2 = serie[1]
        self.assertEqual(dia_2['day'], date(2025, 1, 2))
        self.assertFalse(dia_2['tiene_movimiento'])
        self.assertEqual(dia_2['flujo_neto'], 0.0)
        self.assertEqual(dia_2['n_transacciones'], 0)
        self.assertFalse(dia_2['es_dia_ancla'])
        # No se extrapola fuera del rango observado: no hay dia 2024-12-31 ni 2025-01-06.
        dias = {row['day'] for row in serie}
        self.assertNotIn(date(2024, 12, 31), dias)
        self.assertNotIn(date(2025, 1, 6), dias)

    def test_rejilla_completa_entre_primer_y_ultimo_dia(self):
        serie = _serie()
        observados = [row['day'] for row in serie if not row['es_dia_ancla']]
        self.assertEqual(observados, [date(2025, 1, 1), date(2025, 1, 2),
                                      date(2025, 1, 3), date(2025, 1, 4)])


class SinAnclaTest(unittest.TestCase):
    def test_ancla_ausente_produce_null(self):
        rows = [fila('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0})]
        out = reconstruct_daily_flows(rows, {'A': 5000.0}, companies=['A', 'Z'],
                                      anchor_day=date(2025, 1, 3))
        self.assertEqual(out['A']['ancla'], 5000.0)
        registro = out['Z']
        self.assertEqual(registro['reason'], SIN_ANCLA)
        for row in registro['rows']:
            self.assertIsNone(row['saldo_reversa_eur'])
            self.assertIsNone(row['colchon_reversa_eur'])
            self.assertIsNone(row['saldo_reversa_available_at'])
            self.assertEqual(row['confidence'], 'ninguna')
            self.assertIn('sin_ancla', row['flags'])
        json.dumps(registro, allow_nan=False, default=str)


class AgujerosFxTest(unittest.TestCase):
    def test_agujero_en_el_tramo_anula_el_saldo(self):
        rows = [
            fila('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0}, salidas=0.0),
            fila('A', (2025, 1, 2), -300.0, {'operating_out': -300.0}, salidas=300.0, agujero=50.0),
            fila('A', (2025, 1, 3), 200.0, {'operating_in': 200.0}, salidas=0.0),
        ]
        serie = reconstruct_daily_flows(rows, {'A': 10000.0},
                                        anchor_day=date(2025, 1, 4))['A']['rows']
        self.assertIsNone(serie[0]['saldo_reversa_eur'])
        self.assertEqual(serie[0]['confidence'], 'media')
        self.assertIn('agujeros_en_tramo', serie[0]['flags'])
        # El dia del agujero queda fuera del tramo de si mismo -> saldo conocido.
        self.assertIsNotNone(serie[1]['saldo_reversa_eur'])
        self.assertEqual(serie[1]['confidence'], 'alta')
        # El ancla siempre se lee.
        self.assertEqual(serie[-1]['saldo_reversa_eur'], 10000.0)


class PointInTimeTest(unittest.TestCase):
    def test_corte_no_lee_filas_posteriores(self):
        serie = _serie()
        frame = pd.DataFrame([{**row, 'company_id': 'A'} for row in serie])
        frame['day'] = pd.to_datetime(frame['day'])
        frame['available_at'] = pd.to_datetime(frame['available_at'])
        for corte in (date(2025, 1, 1), date(2025, 1, 3), date(2025, 1, 5)):
            visible = panel_hasta(frame, corte)
            self.assertTrue((visible['day'] <= pd.Timestamp(corte)).all())
            self.assertTrue((visible['available_at'] <= pd.Timestamp(corte)).all())
        # Antes del dia del ancla, la fila del ancla no es legible.
        antes = panel_hasta(frame, date(2025, 1, 4))
        self.assertFalse(antes['es_dia_ancla'].any())
        despues = panel_hasta(frame, date(2025, 1, 5))
        self.assertTrue(despues['es_dia_ancla'].any())


class ValidacionTest(unittest.TestCase):
    def test_dia_posterior_al_ultimo_cerrado_falla(self):
        rows = [fila('A', (2026, 9, 1), 1000.0, {'operating_in': 1000.0})]
        with self.assertRaises(ValueError):
            reconstruct_daily_flows(rows, {'A': 1.0})

    def test_dia_duplicado_falla(self):
        rows = [fila('A', (2025, 1, 1), 10.0, {'operating_in': 10.0}),
                fila('A', (2025, 1, 1), 20.0, {'operating_in': 20.0})]
        with self.assertRaises(ValueError):
            reconstruct_daily_flows(rows, {'A': 1.0})

    def test_desglose_incoherente_falla(self):
        rows = [fila('A', (2025, 1, 1), 1000.0, {'operating_in': 10.0})]
        with self.assertRaises(ValueError):
            reconstruct_daily_flows(rows, {'A': 1.0})

    def test_productos_del_ancla(self):
        self.assertEqual(ANCHOR_PRODUCTS, ('checking', 'wallet', 'saving', 'tpv'))
        self.assertEqual(CASH_PRODUCTS, ANCHOR_PRODUCTS)


class PurezaTest(unittest.TestCase):
    def test_no_muta_la_entrada(self):
        rows = [fila('A', (2025, 1, 1), 1000.0, {'operating_in': 1000.0})]
        original = json.dumps(rows, sort_keys=True, default=str)
        out = reconstruct_daily_flows(rows, {'A': 10.0}, anchor_day=date(2025, 1, 2))
        self.assertEqual(json.dumps(rows, sort_keys=True, default=str), original)
        json.dumps(out, allow_nan=False, default=str)


class DensidadTest(unittest.TestCase):
    def _move(self, pattern):
        return np.array(pattern, dtype=int)

    def test_dias_huecos_y_ventanas(self):
        lleno = self._move([1] * 30)
        unico = self._move([1] + [0] * 29)
        hueco_30 = self._move([1] * 20 + [0] * 30 + [1] * 40)  # span 90, hueco 30
        move = {'lleno': lleno, 'unico': unico, 'hueco30': hueco_30}
        resumen = summarize_density(move, evaluable={'lleno', 'unico', 'hueco30'})
        dias = resumen['dias_con_movimiento_por_empresa']['universo']
        self.assertEqual(dias['min'], 1.0)
        self.assertEqual(dias['max'], 60.0)
        self.assertEqual(resumen['huecos']['rachas_ge_30_dias'], 1)
        # W=30: 'lleno' cumple (30 dias, sin huecos, mediana 30); 'unico' no (mediana 1);
        # 'hueco30' no (hueco 30 > 29).
        self.assertEqual(resumen['ventanas'][30]['empresas_universo'], 1)
        self.assertEqual(resumen['ventanas'][30]['empresas_evaluables_v4'], 1)
        # W=90: 'hueco30' tiene span 90 pero hueco 30 <= 89 y mediana >= 13 -> cumple;
        # 'lleno' y 'unico' tienen span < 90.
        self.assertEqual(resumen['ventanas'][90]['empresas_universo'], 1)

    def test_ventana_vacia_impide_suficiencia(self):
        # 30 dias sin movimiento al principio: la primera ventana de 30 esta vacia.
        patron = [0] * 30 + [1] * 30
        resumen = summarize_density({'X': self._move(patron)}, evaluable={'X'})
        self.assertEqual(resumen['huecos']['rachas_ge_30_dias'], 1)
        self.assertEqual(resumen['ventanas'][30]['empresas_universo'], 0)


class AnomaliasTest(unittest.TestCase):
    def _rows(self):
        return [
            {'company_id': 'COMP_0413', 'n_total': 100, 'volumen_total': 1000.0, 'neto_total': 100.0,
             'n_marcadas': 10, 'volumen_marcado': 400.0, 'neto_marcado': 90.0,
             'n_centinela': 4, 'volumen_centinela': 200.0, 'neto_centinela': 0.0,
             'n_futuro': 6, 'volumen_futuro': 200.0, 'neto_futuro': 90.0},
            {'company_id': 'COMP_XXXX', 'n_total': 100, 'volumen_total': 1000.0, 'neto_total': 100.0,
             'n_marcadas': 1, 'volumen_marcado': 5.0, 'neto_marcado': 0.0,
             'n_centinela': 1, 'volumen_centinela': 5.0, 'neto_centinela': 0.0,
             'n_futuro': 0, 'volumen_futuro': 0.0, 'neto_futuro': 0.0},
            {'company_id': 'COMP_LIMPIA', 'n_total': 100, 'volumen_total': 1000.0, 'neto_total': 100.0,
             'n_marcadas': 0, 'volumen_marcado': 0.0, 'neto_marcado': 0.0,
             'n_centinela': 0, 'volumen_centinela': 0.0, 'neto_centinela': 0.0,
             'n_futuro': 0, 'volumen_futuro': 0.0, 'neto_futuro': 0.0},
        ]

    def test_contaminacion_sobre_el_neto(self):
        censo = build_anomaly_census(self._rows(), evaluable={'COMP_0413'})
        self.assertEqual(censo['totales']['filas_marcadas'], 11)
        self.assertEqual(censo['totales']['empresas_con_marcadas'], 2)
        material = censo['contaminacion_material']['empresas']
        self.assertEqual([item['company_id'] for item in material], ['COMP_0413'])
        self.assertEqual(censo['contaminacion_material']['n_evaluables_v4'], 1)
        # El wash (neto_marcado 0) no es contaminacion material aunque tenga volumen.
        self.assertNotIn('COMP_XXXX', [item['company_id'] for item in material])
        self.assertEqual(material[0]['peso_neto'], round(90.0 / (90.0 + 10.0), 6))

    def test_comp0413_peso_neto_y_volumen(self):
        comp = build_comp0413(self._rows())
        self.assertTrue(comp['presente_en_el_perimetro'])
        self.assertEqual(comp['n_marcadas'], 10)
        self.assertEqual(comp['n_centinela'], 4)
        self.assertEqual(comp['n_futuro'], 6)
        self.assertEqual(comp['peso_neto_marcado'], 0.9)
        self.assertEqual(comp['peso_volumen_marcado'], 0.4)


class ReporteTest(unittest.TestCase):
    def test_el_reporte_cita_la_decision_de_grano(self):
        censo = {
            'densidad': {'ventanas': {
                30: {'empresas_universo': 0, 'empresas_evaluables_v4': 0,
                     'criterio_primario': 'x',
                     'sensibilidad': {'novacio': {'empresas_evaluables_v4': 0, 'empresas_universo': 0,
                                                  'umbral_dias_con_movimiento_en_la_ventana_tipica': 1}}},
                90: {'empresas_universo': 0, 'empresas_evaluables_v4': 0,
                     'criterio_primario': 'x', 'sensibilidad': {}}},
                         'dias_con_movimiento_por_empresa': {'universo': {'p50': 0},
                                                             'evaluables_v4': {'p50': 0}},
                         'huecos': {'rachas_universo': {}, 'hueco_maximo_por_empresa_universo': {},
                                    'hueco_maximo_por_empresa_evaluables_v4': {'p90': 0},
                                    'rachas_ge_30_dias': 0, 'rachas_ge_90_dias': 0,
                                    'rachas_ge_180_dias': 0}},
            'perimetro': {'filas_empresa_dia': 0, 'empresas': 0},
            'poblacion_evaluable_v4': {'empresas': 0, 'empresa_mes': 0},
            'anomalias_fecha_valor': {
                'regla_marcado': 'x', 'totales': {'filas_marcadas': 0, 'filas_value_date_futuro': 0,
                                                  'filas_centinela_value_date_ok_null': 0,
                                                  'empresas_con_marcadas': 0, 'volumen_marcado_eur': 0,
                                                  'neto_marcado_eur': 0},
                'umbrales_declarados': {'contaminacion_material_peso_neto': CONTAMINACION_MATERIAL,
                                        'contaminacion_dominante_peso_neto': 0.5,
                                        'definicion_peso_neto': 'x', 'justificacion': 'x'},
                'contaminacion_material': {'n_empresas': 0, 'n_evaluables_v4': 0, 'empresas': []},
                'contaminacion_dominante': {'n_empresas': 0, 'n_evaluables_v4': 0},
                'comparacion_con_los_numeros_del_spec': {}},
            'comp_0413': {'presente_en_el_perimetro': False},
            'reconciliacion': {'fuente_mensual': 'x', 'discrepancia_maxima_flujo_neto_eur': 0.0,
                               'empresa_mes_comparables': 0, 'diagnostico': 'x'},
            'identidad_ancla': {'discrepancia_maxima_saldo_reversa_menos_ancla_eur': 0.0,
                                'empresas_con_ancla_en_la_fila_del_ancla': 0},
            'fuentes_sha256': {},
        }
        texto = render_report(censo)
        self.assertIn(DECISION_GRANO[:60], texto)
        self.assertIn('COMP_0413', texto)


class CliTest(unittest.TestCase):
    """El CLI genera artefactos, reconcilia, no fuga y no toca las fuentes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='daily-flows-test-')
        cls.output = Path(cls.tmp.name) / 'daily_flows'
        fuentes = [paths.CLEAN_DIR / f'{name}.parquet'
                   for name in ('transactions', 'companies', 'balances')]
        fuentes += [paths.INTERIM_DIR / 'tx_flow_class.parquet']
        fuentes += [paths.ROOT / 'reports/score_v4/assessments.parquet',
                    paths.ROOT / 'reports/cash_backfill/cash_backfill_monthly.parquet']
        cls.fuentes = fuentes

        def huellas():
            def sha(file):
                with file.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            return {str(f.relative_to(paths.ROOT)): sha(f) for f in fuentes}

        cls.antes = huellas()
        resultado = subprocess.run(
            [sys.executable, '-B', '-m', 'xray.daily_flows', '--output', str(cls.output)],
            capture_output=True, text=True, cwd=paths.ROOT, timeout=900)
        cls.resumen = json.loads(resultado.stdout) if resultado.returncode == 0 else None
        cls.fallo = None if resultado.returncode == 0 else resultado.stderr
        cls.despues = huellas()
        if resultado.returncode == 0:
            cls.panel = pd.read_parquet(cls.output / 'panel_diario.parquet')
            cls.censo = json.loads((cls.output / 'censo.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cli_genera_los_tres_artefactos(self):
        self.assertIsNone(self.fallo)
        for nombre in ('panel_diario.parquet', 'censo.json', 'report.md'):
            self.assertTrue((self.output / nombre).exists(), nombre)
        self.assertIsNotNone(self.resumen)

    def test_cli_no_cambia_las_fuentes(self):
        self.assertEqual(self.antes, self.despues)

    def test_reconciliacion_con_la_capa_mensual(self):
        mensual = pd.read_parquet(paths.ROOT / 'reports/cash_backfill/cash_backfill_monthly.parquet')
        mensual['month'] = pd.to_datetime(mensual['month'])
        diario = self.panel[['company_id', 'day', 'flujo_neto']].copy()
        diario['month'] = diario['day'].dt.to_period('M').dt.to_timestamp()
        agregado = diario.groupby(['company_id', 'month'], as_index=False)['flujo_neto'].sum()
        merged = mensual.merge(agregado, on=['company_id', 'month'], how='left',
                               suffixes=('_mensual', '_diario'))
        merged['flujo_neto_diario'] = merged['flujo_neto_diario'].fillna(0.0)
        discrepancia = (merged['flujo_neto_diario'] - merged['flujo_neto_mensual']).abs()
        self.assertLess(float(discrepancia.max()), 1e-4)
        self.assertLess(float(discrepancia.mean()), 1e-6)
        # El censo publica la misma cifra.
        self.assertLess(self.censo['reconciliacion']['discrepancia_maxima_flujo_neto_eur'], 1e-4)

    def test_identidad_del_ancla_en_el_artefacto(self):
        ancla = self.panel[self.panel['es_dia_ancla'] & self.panel['ancla_presente']]
        self.assertGreater(len(ancla), 0)
        self.assertTrue((ancla['saldo_reversa_eur'] - ancla['saldo_ancla_eur']).abs().max() < 1e-9)
        self.assertGreater(self.censo['identidad_ancla']['empresas_con_ancla_en_la_fila_del_ancla'], 0)

    def test_no_fuga_point_in_time(self):
        for corte in ('2025-01-01', '2025-06-15', '2026-08-31', '2026-09-01'):
            visible = panel_hasta(self.panel, corte)
            self.assertTrue((visible['available_at'] <= pd.Timestamp(corte)).all())
            self.assertTrue((visible['day'] <= pd.Timestamp(corte)).all())
            # La lectura PIT es exactamente el prefijo por dia (available_at == day).
            esperado = self.panel[self.panel['day'] <= pd.Timestamp(corte)]
            self.assertEqual(len(visible), len(esperado))
        # La fila del ancla solo aparece a partir del 2026-09-01.
        self.assertFalse(panel_hasta(self.panel, '2026-08-31')['es_dia_ancla'].any())
        self.assertTrue(panel_hasta(self.panel, '2026-09-01')['es_dia_ancla'].any())

    def test_rejilla_densa_y_flujo_coherente(self):
        # Cada empresa tiene todos los dias de calendario entre su primer y ultimo dia observado.
        muestra = self.panel[self.panel['company_id']
                            == self.panel[self.panel['tiene_movimiento']]['company_id'].iloc[0]]
        observado = muestra[~muestra['es_dia_ancla']]
        esperado = (observado['day'].max() - observado['day'].min()).days + 1
        self.assertEqual(len(observado), esperado)
        # entradas - salidas == flujo_neto y el desglose por clase cuadra con el total.
        con_movimiento = self.panel[self.panel['tiene_movimiento']]
        self.assertTrue((con_movimiento['entradas'] - con_movimiento['salidas']
                         - con_movimiento['flujo_neto']).abs().max() < 1e-5)
        clases = [column for column in self.panel.columns
                  if column.startswith('flujo_') and column not in ('flujo_neto', 'flujo_neto_tramo')]
        self.assertTrue((con_movimiento[clases].sum(axis=1)
                         - con_movimiento['flujo_neto']).abs().max() < 1e-5)

    def test_censo_cruce_con_evaluables_v4(self):
        poblacion = self.censo['poblacion_evaluable_v4']
        self.assertEqual(poblacion['empresas'], 957)
        self.assertEqual(poblacion['empresa_mes'], 15116)
        self.assertEqual(poblacion['interseccion_ventana_30'],
                         self.censo['densidad']['ventanas']['30']['empresas_evaluables_v4'])
        self.assertGreater(poblacion['interseccion_ventana_30'], 0)
        self.assertLessEqual(poblacion['interseccion_ventana_30'], 957)
        self.assertGreaterEqual(poblacion['interseccion_ventana_90'],
                                poblacion['interseccion_ventana_30'])

    def test_comp0413_tiene_seccion_propia(self):
        comp = self.censo['comp_0413']
        self.assertTrue(comp['presente_en_el_perimetro'])
        self.assertEqual(comp['n_marcadas'], comp['n_centinela'] + comp['n_futuro'])
        self.assertGreater(comp['n_marcadas'], 0)
        self.assertIsNotNone(comp['peso_neto_marcado'])
        self.assertIn('COMP_0413', (self.output / 'report.md').read_text())


if __name__ == '__main__':
    unittest.main()
