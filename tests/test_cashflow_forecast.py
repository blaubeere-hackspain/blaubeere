"""Tests del backtest walk-forward de flujo de caja (30/60/90 dias).

Cubre: criterio de densidad (incluida la ventana 60 que no esta en el censo
publicado), construccion de cortes, convenio de intervalo del target, los tres
baselines, la banda neutra de tendencia y el CLI/reproducibilidad. Incluye el
test OBLIGATORIO de NO-FUGA: la prediccion de un corte es bit-identica si se
eliminan o alteran todas las filas del panel posteriores a ese corte.

Los tests de integracion leen el panel diario ya consolidado
(`reports/daily_flows/panel_diario.parquet`) y `reports/score_v4/assessments.parquet`;
se saltan si no existen. Ningun test escribe en `reports/`.
"""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from xray import paths
from xray.cashflow_forecast import (
    CENSO_ESPERADO_EVALUABLES,
    DEFAULT_CENSO,
    DEFAULT_PANEL,
    HORIZONTES,
    VENTANA_MEDIA_MOVIL,
    _company_sufficient,
    _month_ends,
    _pos_le,
    _predict_company,
    _to_day,
    b3_by_corte,
    build_b3_pools,
    build_cortes,
    build_density_census,
    build_panel_index,
    build_prediction_frame,
    build_report,
    build_trios,
    derive_tendencia,
    density_membership,
    load_censo_json,
    load_evaluable_v4,
    load_panel,
    main,
    observed_targets,
    report_paths,
    verify_density_against_censo,
)

BASE = _to_day(date(2025, 1, 1))


def _arrays(days, flujo, saldo):
    return {
        'days': np.asarray(days, dtype='int64'),
        'flujo': np.asarray(flujo, dtype=float),
        'saldo': np.asarray(saldo, dtype=float),
    }


def make_panel(n_companies=4, start=date(2025, 1, 1), n_days=365):
    """Panel sintetico con las columnas que consume el modulo."""
    rows = []
    for number in range(n_companies):
        company_id = f'COMP_{number}'
        saldo = 100000.0
        for offset in range(n_days):
            day = start + timedelta(days=offset)
            flujo = float(((offset * 13 + number * 7) % 17) - 8) * 100.0
            saldo += flujo
            rows.append({
                'company_id': company_id,
                'day': pd.Timestamp(day),
                'available_at': pd.Timestamp(day),
                'es_dia_ancla': False,
                'tiene_movimiento': flujo != 0.0,
                'flujo_neto': flujo,
                'saldo_reversa_eur': saldo,
                'saldo_reversa_available_at': pd.Timestamp(day),
                'confidence': 'alta',
            })
    return pd.DataFrame(rows)


def predictions_for(panel, keys):
    """Predicciones point-in-time para claves (company_id, corte, h) explicitas.

    Reutiliza la misma maquinaria que el pipeline (B3 con origenes ya cerrados)
    sin depender de la poblacion por densidad: es lo que permite comparar el
    panel completo contra un panel truncado.
    """
    index = build_panel_index(panel)
    month_ends = _month_ends(panel['day'].min().date(), panel['day'].max().date())
    pools = build_b3_pools(index, month_ends)
    output = {}
    for company_id, corte, horizon in keys:
        b3 = b3_by_corte(pools[horizon], [corte], horizon)[_to_day(corte)]
        output[(company_id, corte, horizon)] = _predict_company(
            index[company_id], _to_day(corte), horizon, b3, VENTANA_MEDIA_MOVIL)
    return output


class DensityCriterionTest(unittest.TestCase):
    def test_ventana_llena_pasa(self):
        self.assertTrue(_company_sufficient(np.ones(60, dtype=int), 30))
        self.assertTrue(_company_sufficient(np.ones(60, dtype=int), 60))

    def test_hueco_mayor_que_la_ventana_falla_en_30(self):
        move = np.ones(60, dtype=int)
        move[10:40] = 0                      # racha de 30 dias sin movimiento
        self.assertFalse(_company_sufficient(move, 30))
        # Con W=60 la unica ventana tiene 30 movimientos >= ceil(60/7)=9.
        self.assertTrue(_company_sufficient(move, 60))

    def test_umbral_mediana_W60_es_9(self):
        # 60 dias con 8 movimientos: mediana por ventana = 8 < 9 -> falla.
        move = np.zeros(60, dtype=int)
        move[[0, 8, 16, 24, 32, 40, 48, 56]] = 1
        self.assertFalse(_company_sufficient(move, 60))
        move[7] = 1                          # 9 movimientos -> pasa.
        self.assertTrue(_company_sufficient(move, 60))

    def test_span_menor_que_la_ventana_falla(self):
        self.assertFalse(_company_sufficient(np.ones(29, dtype=int), 30))


class MonthEndsAndCortesTest(unittest.TestCase):
    def test_month_ends_cubre_todo_el_rango(self):
        ends = _month_ends(date(2024, 9, 1), date(2026, 8, 31))
        self.assertEqual(len(ends), 24)
        self.assertEqual(ends[0], date(2024, 9, 30))
        self.assertEqual(ends[-1], date(2026, 8, 31))

    def test_cortes_por_horizonte_respetan_warmup_y_target(self):
        cortes = build_cortes(date(2024, 9, 1))
        self.assertEqual(cortes['_dates']['30'][0], date(2024, 11, 30))
        self.assertEqual(cortes['_dates']['30'][-1], date(2026, 7, 31))
        self.assertEqual(cortes['_dates']['90'][-1], date(2026, 5, 31))
        for horizon in HORIZONTES:
            for corte in cortes['_dates'][str(horizon)]:
                self.assertLessEqual(corte + timedelta(days=horizon), date(2026, 8, 31))
                self.assertGreaterEqual(corte, date(2024, 11, 30))


class TargetConventionTest(unittest.TestCase):
    def test_intervalo_abierto_izquierda_cerrado_derecha(self):
        days = BASE + np.arange(10)
        flujo = np.arange(1, 11, dtype=float)
        saldo = 1000.0 + np.arange(10, dtype=float)
        flow, target_saldo = observed_targets(_arrays(days, flujo, saldo), BASE + 3, 3)
        # (3, 6] -> dias 4, 5, 6 -> flujo 5 + 6 + 7 = 18. El dia del corte NO entra.
        self.assertEqual(flow, 18.0)
        self.assertEqual(target_saldo, saldo[6])

    def test_saldo_nulo_se_propaga_como_none(self):
        days = BASE + np.arange(10)
        saldo = np.full(10, np.nan)
        _, target_saldo = observed_targets(_arrays(days, np.ones(10), saldo), BASE + 3, 3)
        self.assertIsNone(target_saldo)


class BaselinesTest(unittest.TestCase):
    def setUp(self):
        self.days = BASE + np.arange(40)
        self.flujo = np.ones(40)
        self.saldo = 500.0 + np.arange(40, dtype=float)
        self.corte = int(self.days[-1])

    def test_b1_b2_b3_y_saldo_a_mano(self):
        prediction = _predict_company(
            _arrays(self.days, self.flujo, self.saldo), self.corte, 30, 7.0,
            VENTANA_MEDIA_MOVIL)
        self.assertEqual(prediction['B1_persistencia_flujo_eur'], 30.0)
        self.assertEqual(prediction['B2_media_movil_28d_flujo_eur'], 30.0)
        self.assertEqual(prediction['B3_mediana_global_flujo_eur'], 7.0)
        self.assertEqual(prediction['escala_empresa_h_eur'], 30.0)
        self.assertEqual(prediction['saldo_corte_eur'], self.saldo[-1])
        self.assertEqual(prediction['B1_persistencia_saldo_eur'], self.saldo[-1] + 30.0)
        self.assertEqual(prediction['B3_mediana_global_saldo_eur'], self.saldo[-1] + 7.0)

    def test_sin_historia_suficiente_no_predice(self):
        days = BASE + np.arange(10)
        prediction = _predict_company(
            _arrays(days, np.ones(10), np.ones(10)), int(days[-1]), 30, 1.0,
            VENTANA_MEDIA_MOVIL)
        self.assertIsNone(prediction)

    def test_saldo_nulo_da_prediccion_de_saldo_nula(self):
        days = BASE + np.arange(40)
        prediction = _predict_company(
            _arrays(days, np.ones(40), np.full(40, np.nan)), int(days[-1]), 30, 1.0,
            VENTANA_MEDIA_MOVIL)
        self.assertIsNone(prediction['B1_persistencia_saldo_eur'])
        self.assertEqual(prediction['B1_persistencia_flujo_eur'], 30.0)

    def test_b3_solo_usa_origenes_cerrados_antes_del_corte(self):
        pools = {0: np.array([1.0, 2.0, 3.0]), 100: np.array([10.0, 20.0, 30.0])}
        # Corte ordinal 120: el origen 100 aun no ha cerrado su periodo (100+30>120).
        corte_120 = date(1970, 1, 1) + timedelta(days=120)
        self.assertEqual(b3_by_corte(pools, [corte_120], 30)[_to_day(corte_120)], 2.0)
        corte_150 = date(1970, 1, 1) + timedelta(days=150)
        self.assertEqual(b3_by_corte(pools, [corte_150], 30)[_to_day(corte_150)], 6.5)


class TendenciaTest(unittest.TestCase):
    def test_regla_de_banda(self):
        self.assertEqual(derive_tendencia(10.0, 4.0), (7.0, 3.0, 'positiva'))
        self.assertEqual(derive_tendencia(-10.0, -4.0), (-7.0, 3.0, 'negativa'))
        self.assertEqual(derive_tendencia(5.0, -5.0), (0.0, 5.0, 'neutra'))
        self.assertEqual(derive_tendencia(1.0, None), (1.0, 0.0, 'positiva'))
        self.assertEqual(derive_tendencia(None, None), (None, None, None))


class NoLeakTest(unittest.TestCase):
    """OBLIGATORIO: prediccion bit-identica al borrar/alterar el futuro."""

    def setUp(self):
        self.panel = make_panel()
        self.corte = date(2025, 7, 15)
        self.keys = [(f'COMP_{number}', self.corte, 30) for number in range(4)]

    def test_prediccion_identica_si_se_elimina_el_futuro(self):
        completo = predictions_for(self.panel, self.keys)
        truncado = predictions_for(self.panel[self.panel['day'] <= pd.Timestamp(self.corte)],
                                   self.keys)
        self.assertEqual(completo, truncado)

    def test_prediccion_identica_si_se_altera_el_futuro(self):
        completo = predictions_for(self.panel, self.keys)
        alterado = self.panel.copy()
        mask = alterado['day'] > pd.Timestamp(self.corte)
        alterado.loc[mask, 'flujo_neto'] = 1.0e12
        alterado.loc[mask, 'saldo_reversa_eur'] = -1.0e12
        alterado.loc[mask, 'tiene_movimiento'] = True
        self.assertEqual(completo, predictions_for(alterado, self.keys))

    def test_la_prediccion_si_cambia_si_se_altera_el_pasado(self):
        # Control negativo: el test no es vacuo.
        completo = predictions_for(self.panel, self.keys)
        alterado = self.panel.copy()
        mask = alterado['day'] <= pd.Timestamp(self.corte)
        alterado.loc[mask, 'flujo_neto'] = alterado.loc[mask, 'flujo_neto'] + 1000.0
        self.assertNotEqual(completo, predictions_for(alterado, self.keys))

    def test_frame_completo_ignora_filas_posteriores(self):
        panel_truncado = self.panel[self.panel['day'] <= pd.Timestamp(self.corte)].copy()
        index = build_panel_index(self.panel)
        trios = [{'company_id': key[0], 'corte': key[1], 'h': key[2]}
                 for key in self.keys]
        pools = build_b3_pools(index, _month_ends(date(2025, 1, 1), date(2025, 12, 31)))
        lookup = {'30': b3_by_corte(pools[30], [self.corte], 30)}
        completo = build_prediction_frame(index, trios, lookup)
        alterado = self.panel.copy()
        mask = alterado['day'] > pd.Timestamp(self.corte)
        alterado.loc[mask, 'flujo_neto'] = 1.0e12
        index_alterado = build_panel_index(alterado)
        frame_alterado = build_prediction_frame(index_alterado, trios, lookup)
        # Las FEATURES (predicciones) son identicas; el target si cambia, es la verdad.
        for name in ('B1_persistencia', 'B2_media_movil_28d', 'B3_mediana_global'):
            pd.testing.assert_series_equal(
                completo[f'{name}_flujo_eur'], frame_alterado[f'{name}_flujo_eur'])
        self.assertNotEqual(len(panel_truncado), len(self.panel))


class SyntheticPipelineTest(unittest.TestCase):
    def test_censo_trios_y_frame(self):
        panel = make_panel(n_companies=4, start=date(2025, 1, 1), n_days=365)
        move = {company_id: group.sort_values('day')['tiene_movimiento'].astype(int).to_numpy()
                for company_id, group in panel.groupby('company_id')}
        membership = density_membership(move)
        index = build_panel_index(panel)
        cortes = build_cortes(date(2025, 1, 1), last_closed_day=date(2025, 12, 31))
        # Con warmup de 90 dias sobre 2025-01-01, el primer corte valido es 2025-04-30.
        self.assertEqual(cortes['_dates']['30'][0], date(2025, 4, 30))
        trios = build_trios(index, {h: membership[h] for h in HORIZONTES},
                            cortes['_dates'], horizons=(30,))
        self.assertTrue(trios)
        month_ends = _month_ends(date(2025, 1, 1), date(2025, 12, 31))
        pools = build_b3_pools(index, month_ends)
        lookup = {'30': b3_by_corte(pools[30], cortes['_dates']['30'], 30)}
        frame = build_prediction_frame(index, trios, lookup)
        self.assertFalse(frame.empty)
        self.assertTrue(frame['tendencia_h'].isin(['positiva', 'negativa', 'neutra']).all())
        self.assertTrue((frame['dia_target'] > frame['corte']).all())


@unittest.skipUnless(DEFAULT_PANEL.exists(), 'falta el panel diario consolidado')
@unittest.skipUnless((paths.ROOT / 'reports/score_v4/assessments.parquet').exists(),
                     'falta assessments.parquet de v4')
class RealPanelIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = load_panel(DEFAULT_PANEL)
        cls.evaluable = load_evaluable_v4()
        cls.censo = load_censo_json(DEFAULT_CENSO)
        from xray.daily_flows import build_density
        cls.move = build_density(cls.panel)

    def test_reproduce_censo_732_y_790(self):
        census = build_density_census(self.move, self.evaluable)
        verification = verify_density_against_censo(census, self.censo)
        self.assertTrue(verification['30']['coincide'])
        self.assertTrue(verification['90']['coincide'])
        self.assertEqual(census['ventanas']['30']['empresas_evaluables_v4'],
                         CENSO_ESPERADO_EVALUABLES[30])
        self.assertEqual(census['ventanas']['90']['empresas_evaluables_v4'],
                         CENSO_ESPERADO_EVALUABLES[90])
        # Ventana 60 calculada localmente (no esta en el censo publicado).
        self.assertGreater(census['ventanas']['60']['empresas_evaluables_v4'], 0)
        self.assertLessEqual(census['ventanas']['60']['empresas_universo'],
                             census['ventanas']['90']['empresas_universo'])

    def test_informe_completo_coherente(self):
        report, frame = build_report(self.panel, self.censo, self.evaluable,
                                     panel_path=DEFAULT_PANEL, censo_path=DEFAULT_CENSO)
        self.assertTrue(report['verificacion_censo_existente']['30']['coincide'])
        self.assertTrue(report['verificacion_censo_existente']['90']['coincide'])
        for horizon in HORIZONTES:
            self.assertGreater(report['censo_trios']['por_h'][str(horizon)]['n_trios'], 0)
        # Todas las metricas de flujo caen en la interseccion comun.
        for horizon in ('30', '60', '90'):
            counts = {name: report['metricas']['flujo_neto_acumulado'][horizon][name]['n']
                      for name in report['cobertura_baselines'][horizon]
                      if name.startswith('B')}
            self.assertEqual(len(set(counts.values())), 1, counts)
        json.dumps(report, allow_nan=False)

    def test_no_fuga_sobre_datos_reales(self):
        corte = date(2026, 5, 31)
        corte_ts = pd.Timestamp(corte)
        panel = self.panel
        companies = []
        for company_id in sorted(panel['company_id'].unique()):
            days = panel.loc[panel['company_id'] == company_id, 'day']
            if (days == corte_ts).any() and (days <= corte_ts - pd.Timedelta(days=30)).any():
                companies.append(company_id)
            if len(companies) == 3:
                break
        self.assertTrue(companies, 'no hay empresas con historia en el corte de prueba')
        keys = [(company_id, corte, 30) for company_id in companies]
        completo = predictions_for(panel, keys)
        self.assertTrue(any(value is not None for value in completo.values()))
        truncado = predictions_for(panel[panel['day'] <= corte_ts], keys)
        alterado_panel = panel.copy()
        mask = alterado_panel['day'] > corte_ts
        alterado_panel.loc[mask, 'flujo_neto'] = 1.0e12
        alterado_panel.loc[mask, 'saldo_reversa_eur'] = -1.0e12
        alterado = predictions_for(alterado_panel, keys)
        self.assertEqual(completo, truncado)
        self.assertEqual(completo, alterado)

    def test_cli_escribe_tres_artefactos_y_check_reproduce(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            stdout, errors = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(errors):
                code = main(['--panel', str(DEFAULT_PANEL), '--censo', str(DEFAULT_CENSO),
                             '--output-dir', str(output)])
            self.assertEqual(code, 0, errors.getvalue())
            parquet_path, json_path, markdown_path = report_paths(output)
            self.assertTrue(parquet_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            report = json.loads(json_path.read_text(encoding='utf-8'))
            self.assertEqual(report['version'], 'cashflow_forecast_v1')
            printed = json.loads(stdout.getvalue())
            self.assertEqual(printed['verificacion_censo']['30'], True)
            frame = pd.read_parquet(parquet_path)
            self.assertIn('tendencia_h', frame.columns)
            self.assertIn('target_flujo_neto_acumulado_eur', frame.columns)
            # --check no escribe y reproduce la salida publicada.
            with redirect_stdout(StringIO()), redirect_stderr(errors):
                self.assertEqual(main(['--panel', str(DEFAULT_PANEL),
                                       '--censo', str(DEFAULT_CENSO),
                                       '--output-dir', str(output), '--check']), 0)
            # Drift detectado.
            json_path.write_text(json_path.read_text(encoding='utf-8')
                                 .replace('cashflow_forecast_v1', 'drift'), encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(errors):
                self.assertEqual(main(['--panel', str(DEFAULT_PANEL),
                                       '--censo', str(DEFAULT_CENSO),
                                       '--output-dir', str(output), '--check']), 1)
            self.assertIn('NO reproduce', errors.getvalue())


class SyntheticCliErrorTest(unittest.TestCase):
    def test_cli_falla_con_panel_inexistente(self):
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            code = main(['--panel', '/no/existe/panel.parquet',
                         '--output-dir', '/tmp/no-importa'])
        self.assertEqual(code, 1)
        self.assertIn('falta', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
