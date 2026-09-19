"""Tests del banco de pruebas walk-forward de health_score v4.

Todo con fixtures en memoria o parquet temporal: ningun test escribe en
reports/ ni toca data/. Se prueban los contratos que sostienen la
comparabilidad: poblacion congelada con digest reproducible, censo completo
sin imputacion, evaluador sobre la interseccion prediccion Y verdad, y los
tres baselines (B1, B2, B3) mas la autocorrelacion.
"""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import duckdb

from xray.forecast_backtest import (HORIZONTES, NOMBRE_SUBPOBLACION_MADURA,
                                    VENTANA_MEDIA_MOVIL, VENTANA_SCORER_MESES,
                                    add_months, autocorrelacion,
                                    baseline_media_movil,
                                    baseline_media_movil_historia,
                                    baseline_mediana_global,
                                    baseline_persistencia, build_origin_census,
                                    build_report, census_origins,
                                    compute_baselines, evaluate_predictions,
                                    freeze_population, load_assessments, main,
                                    media_ultimas, normaliza_predicciones,
                                    poblacion_serializable, quantile,
                                    render_markdown, verifica_digest,
                                    write_report)

M0 = date(2024, 9, 1)
M1 = date(2024, 10, 1)
M2 = date(2024, 11, 1)
M3 = date(2024, 12, 1)
M4 = date(2025, 1, 1)


def fila(company_id, month, health_score, excluida=False,
         ventana_parcial=False):
    return {'company_id': company_id, 'month': month,
            'health_score': health_score, 'excluida': excluida,
            'ventana_parcial': ventana_parcial}


def panel_corto():
    """Panel de 5 meses con huecos, una excluida y una sin nota."""
    rows = []
    for month, value in zip([M0, M1, M2, M3, M4], [10.0, 20.0, 30.0, 40.0, 50.0]):
        rows.append(fila('COMP_A', month, value))
    for month, value in zip([M0, M1, M2, M3], [5.0, 15.0, 25.0, 35.0]):
        rows.append(fila('COMP_B', month, value))
    rows.append(fila('COMP_C', M0, 99.0))
    rows.append(fila('COMP_F', M1, 7.0))
    rows.append(fila('COMP_D', M0, 1.0, excluida=True))
    rows.append(fila('COMP_D', M1, 2.0, excluida=True))
    for month in (M0, M1, M2, M3, M4):
        rows.append(fila('COMP_E', month, None))
    return rows


def panel_madurez():
    """Panel de 9 meses completo para ejercer la subpoblacion madura."""
    meses = [add_months(M0, i) for i in range(9)]
    rows = []
    for i, month in enumerate(meses):
        rows.append(fila('COMP_X', month, float(i),
                         ventana_parcial=i < VENTANA_SCORER_MESES - 1))
    return rows


class DateHelperTest(unittest.TestCase):
    def test_add_months_cruza_el_ano(self):
        self.assertEqual(add_months(date(2024, 11, 1), 3), date(2025, 2, 1))
        self.assertEqual(add_months(date(2025, 1, 1), -1), date(2024, 12, 1))
        self.assertEqual(add_months(date(2024, 9, 1), 12), date(2025, 9, 1))

    def test_acepta_datetime_y_texto(self):
        self.assertEqual(add_months(datetime(2024, 9, 15, 10, 0), 1),
                         date(2024, 10, 1))
        self.assertEqual(add_months('2024-09-15', 2), date(2024, 11, 1))


class QuantileTest(unittest.TestCase):
    def test_interpolacion_lineal(self):
        self.assertEqual(quantile([10.0], 0.5), 10.0)
        self.assertAlmostEqual(quantile([0.0, 10.0], 0.5), 5.0)
        self.assertAlmostEqual(quantile([0.0, 10.0, 20.0], 0.5), 10.0)
        self.assertAlmostEqual(quantile([0.0, 10.0, 20.0, 30.0], 0.75), 22.5)
        self.assertIsNone(quantile([], 0.5))


class PoblacionTest(unittest.TestCase):
    def test_freezing_excluye_nulos_y_excluidas(self):
        population = freeze_population(panel_corto())
        self.assertEqual(population['n_empresas'], 4)
        self.assertEqual(population['n_empresa_mes'], 11)
        self.assertEqual(population['empresas'],
                         ['COMP_A', 'COMP_B', 'COMP_C', 'COMP_F'])
        self.assertEqual(population['regla'],
                         'health_score no nulo y excluida = false')
        self.assertEqual(population['meses_panel'],
                         ['2024-09-01', '2024-10-01', '2024-11-01',
                          '2024-12-01', '2025-01-01'])
        self.assertNotIn(('COMP_D', M0), population['_verdad'])
        self.assertNotIn(('COMP_E', M0), population['_verdad'])

    def test_digest_es_reproducible_y_sensible(self):
        primero = freeze_population(panel_corto())
        segundo = freeze_population(panel_corto())
        self.assertEqual(primero['digest_sha256'], segundo['digest_sha256'])
        self.assertTrue(verifica_digest(primero))
        alterado = panel_corto()
        for row in alterado:
            if row['company_id'] == 'COMP_A' and row['month'] == M0:
                row['health_score'] = 10.5
        self.assertNotEqual(freeze_population(alterado)['digest_sha256'],
                            primero['digest_sha256'])

    def test_clave_duplicada_es_error(self):
        rows = panel_corto() + [fila('COMP_A', M0, 11.0)]
        with self.assertRaises(ValueError):
            freeze_population(rows)

    def test_nota_no_finita_es_error(self):
        rows = [fila('COMP_A', M0, float('nan'))]
        with self.assertRaises(ValueError):
            freeze_population(rows)

    def test_poblacion_serializable_no_expone_la_verdad(self):
        payload = poblacion_serializable(freeze_population(panel_corto()))
        self.assertNotIn('_verdad', payload)
        json.dumps(payload, allow_nan=False)


class CensoTest(unittest.TestCase):
    def test_censo_cuenta_pares_y_perdidos(self):
        census = build_origin_census(rows=panel_corto())
        self.assertEqual(census['n_origenes'], 2)
        self.assertEqual(census['origenes'], ['2024-09-01', '2024-10-01'])
        origen_m0 = census['por_origen'][0]
        self.assertEqual(origen_m0['por_h']['1'],
                         {'pares': 2, 'empresas': 2,
                          'perdidos_nota_ausente_en_mas_h': 1})
        self.assertEqual(origen_m0['por_h']['3']['pares'], 2)
        origen_m1 = census['por_origen'][1]
        self.assertEqual(origen_m1['por_h']['3'],
                         {'pares': 1, 'empresas': 1,
                          'perdidos_nota_ausente_en_mas_h': 2})
        self.assertEqual(census['total_por_h']['1'],
                         {'pares': 4, 'empresas': 2,
                          'perdidos_nota_ausente_en_mas_h': 2})
        self.assertEqual(census['total_por_h']['3']['pares'], 3)
        self.assertEqual(census['total_por_h']['3']
                         ['perdidos_nota_ausente_en_mas_h'], 3)
        self.assertEqual(census['origenes_utilizables'],
                         ['2024-09-01', '2024-10-01'])

    def test_no_imputa_nota_ausente(self):
        census = build_origin_census(rows=panel_corto())
        # COMP_C tiene nota en M0 y no en M1: el par (COMP_C, M0, 1) se pierde.
        self.assertEqual(census['por_origen'][0]['por_h']['1']['pares'], 2)

    def test_sin_recorte_de_calentamiento_pero_regla_declarada(self):
        census = build_origin_census(rows=panel_madurez())
        self.assertEqual(census['n_origenes'], 6)
        self.assertEqual(census['calentamiento']['origenes_descartados'],
                         ['2024-09-01', '2024-10-01', '2024-11-01',
                          '2024-12-01', '2025-01-01'])
        self.assertEqual(census['calentamiento']['pct_ventana_parcial'], 1.0)
        madura = census[NOMBRE_SUBPOBLACION_MADURA]
        self.assertEqual(madura['n_origenes'], 1)
        self.assertEqual(madura['origenes'], ['2025-02-01'])
        self.assertEqual(madura['total_por_h']['1'], 1)
        self.assertTrue(madura['digest_sha256'])

    def test_talon_de_meses_sin_m_mas_3_se_publica(self):
        census = build_origin_census(rows=panel_madurez())
        talon = {fila['mes']: fila['por_h']
                 for fila in census['talon_no_utilizable_como_origen']}
        self.assertEqual(sorted(talon),
                         ['2025-03-01', '2025-04-01', '2025-05-01'])
        self.assertEqual(talon['2025-03-01']['1']['pares'], 1)
        self.assertEqual(talon['2025-03-01']['2']['pares'], 1)
        self.assertIsNone(talon['2025-03-01']['3'])
        self.assertIsNone(talon['2025-05-01']['1'])

    def test_census_origins_respeta_horizonte_maximo(self):
        calendario = [add_months(M0, i) for i in range(5)]
        self.assertEqual(census_origins(calendario), [M0, M1])


class EvaluadorTest(unittest.TestCase):
    def setUp(self):
        self.population = freeze_population(panel_corto())
        self.origenes = [M0, M1]

    def test_persistencia_mae_calculado_a_mano(self):
        predictions = baseline_persistencia(self.population, self.origenes)
        metricas = evaluate_predictions(predictions, population=self.population)
        m1 = metricas[1]
        self.assertEqual(m1['n_pares'], 4)
        self.assertEqual(m1['n_empresas'], 2)
        self.assertEqual(m1['n_descartadas_verdad_ausente'], 2)
        self.assertAlmostEqual(m1['mae'], 10.0, places=9)
        self.assertAlmostEqual(m1['sesgo'], -10.0, places=9)
        self.assertAlmostEqual(m1['rmse'], 10.0, places=9)
        self.assertAlmostEqual(m1['error_abs_p50'], 10.0, places=9)

    def test_interseccion_reportada_sin_imputar(self):
        predictions = [('COMP_C', M0, 1, 99.0), ('COMP_A', M0, 1, 10.0)]
        metricas = evaluate_predictions(predictions, population=self.population)
        self.assertEqual(metricas[1]['n_predicciones'], 2)
        self.assertEqual(metricas[1]['n_pares'], 1)
        self.assertEqual(metricas[1]['n_descartadas_verdad_ausente'], 1)

    def test_origen_fuera_de_poblacion_se_descarta(self):
        predictions = [('COMP_D', M0, 1, 1.0)]
        metricas = evaluate_predictions(predictions, population=self.population)
        self.assertEqual(metricas[1]['n_pares'], 0)
        self.assertEqual(metricas[1]['n_descartadas_origen_sin_nota'], 1)
        self.assertIsNone(metricas[1]['mae'])

    def test_modo_diagnostico_incluye_origenes_sin_nota(self):
        predictions = [('COMP_D', M0, 1, 1.0)]
        metricas = evaluate_predictions(predictions, population=self.population,
                                        exigir_nota_en_origen=False)
        self.assertEqual(metricas[1]['n_descartadas_origen_sin_nota'], 0)
        self.assertEqual(metricas[1]['n_descartadas_verdad_ausente'], 1)

    def test_normaliza_tuplas_dicts_y_none(self):
        normalizadas, sin_valor = normaliza_predicciones([
            ('COMP_A', '2024-09-01', 1, 10.0),
            {'company_id': 'COMP_B', 'origen': M0, 'h': 2, 'prediccion': 5.0},
            {'company_id': 'COMP_C', 'origen': M0, 'h': 1, 'prediccion': None},
        ])
        self.assertEqual(normalizadas,
                         {('COMP_A', M0, 1): 10.0, ('COMP_B', M0, 2): 5.0})
        self.assertEqual(sin_valor, 1)

    def test_prediccion_duplicada_es_error(self):
        with self.assertRaises(ValueError):
            normaliza_predicciones([('COMP_A', M0, 1, 1.0),
                                    ('COMP_A', M0, 1, 2.0)])

    def test_prediccion_no_finita_y_horizonte_invalido(self):
        with self.assertRaises(ValueError):
            normaliza_predicciones([('COMP_A', M0, 1, float('inf'))])
        with self.assertRaises(ValueError):
            normaliza_predicciones([('COMP_A', M0, 0, 1.0)])

    def test_evaluador_sin_poblacion_ni_rows_es_error(self):
        with self.assertRaises(ValueError):
            evaluate_predictions([])


class BaselinesTest(unittest.TestCase):
    def setUp(self):
        self.population = freeze_population(panel_corto())
        self.origenes = [M0, M1]

    def test_media_ultimas_sin_historia_no_predice(self):
        self.assertIsNone(media_ultimas([]))
        self.assertEqual(media_ultimas([(M0, 1.0)]), 1.0)
        self.assertEqual(media_ultimas([(M0, 1.0), (M1, 2.0), (M2, 3.0)]), 2.0)
        self.assertEqual(
            media_ultimas([(M0, 1.0), (M1, 2.0), (M2, 3.0), (M3, 4.0)]), 3.0)
        self.assertEqual(VENTANA_MEDIA_MOVIL, 3)

    def test_media_movil_usa_las_ultimas_tres_incluida_m(self):
        predictions = baseline_media_movil(self.population, self.origenes)
        por_clave = {(p['company_id'], p['origen']): p['prediccion']
                     for p in predictions if p['h'] == 1}
        self.assertEqual(por_clave[('COMP_A', M0)], 10.0)
        self.assertEqual(por_clave[('COMP_A', M1)], 15.0)
        self.assertEqual(por_clave[('COMP_B', M0)], 5.0)
        self.assertEqual(por_clave[('COMP_B', M1)], 10.0)

    def test_mediana_global_por_origen(self):
        predictions = baseline_mediana_global(self.population, self.origenes)
        por_clave = {(p['company_id'], p['origen']): p['prediccion']
                     for p in predictions if p['h'] == 1}
        self.assertEqual(por_clave[('COMP_A', M0)], 10.0)   # med(10,5,99)
        self.assertEqual(por_clave[('COMP_B', M0)], 10.0)
        self.assertEqual(por_clave[('COMP_A', M1)], 15.0)   # med(20,15,7)

    def test_los_tres_baselines_caen_en_el_mismo_censo(self):
        census = build_origin_census(rows=panel_corto())
        metricas = compute_baselines(self.population, self.origenes)
        for name in metricas:
            for h in HORIZONTES:
                self.assertEqual(metricas[name][h]['n_pares'],
                                 census['total_por_h'][str(h)]['pares'],
                                 f'{name} h={h}')

    def test_b2_media_movil_mae_a_mano(self):
        metricas = compute_baselines(self.population, self.origenes)
        self.assertAlmostEqual(metricas['B2_media_movil_3'][1]['mae'], 12.5,
                               places=9)

    def test_diagnostico_b2_historia_es_poblacion_mayor(self):
        rows = [fila('COMP_A', M0, 10.0), fila('COMP_A', M1, 20.0),
                fila('COMP_A', M2, 30.0), fila('COMP_G', M0, 3.0),
                fila('COMP_G', M2, 4.0)]
        population = freeze_population(rows)
        origenes = [M0, M1]
        base = evaluate_predictions(
            baseline_media_movil(population, origenes), population=population)
        diagnostico = evaluate_predictions(
            baseline_media_movil_historia(population, origenes),
            population=population, exigir_nota_en_origen=False)
        # COMP_G no tiene nota en M1 pero si historia en M0 y verdad en M2.
        self.assertEqual(base[1]['n_pares'], 2)
        self.assertEqual(diagnostico[1]['n_pares'], 3)
        self.assertEqual(diagnostico[1]['n_descartadas_origen_sin_nota'], 0)


class AutocorrelacionTest(unittest.TestCase):
    def test_autocorrelacion_lineal_perfecta(self):
        rows = []
        for i, month in enumerate([M0, M1, M2]):
            rows.append(fila('COMP_A', month, float(i)))
        population = freeze_population(rows)
        resultado = autocorrelacion(population, [M0, M1])
        self.assertAlmostEqual(resultado[1]['pearson'], 1.0, places=9)
        self.assertAlmostEqual(resultado[1]['ols_pendiente'], 1.0, places=9)
        self.assertEqual(resultado[1]['n_pares'], 2)

    def test_autocorrelacion_serie_constante_no_definida(self):
        rows = [fila('COMP_A', m, 7.0) for m in (M0, M1)]
        population = freeze_population(rows)
        resultado = autocorrelacion(population, [M0])
        self.assertIsNone(resultado[1]['pearson'])


class ReproducibilidadTest(unittest.TestCase):
    def test_informe_serializable_y_sin_nan(self):
        report = build_report(panel_corto())
        payload = json.dumps(report, allow_nan=False)
        self.assertIn('poblacion', json.loads(payload))
        self.assertTrue(report['poblacion']['digest_sha256'])

    def test_write_report_reproducible_byte_a_byte(self):
        report = build_report(panel_corto())
        with tempfile.TemporaryDirectory() as first:
            with tempfile.TemporaryDirectory() as second:
                write_report(report, first)
                write_report(report, second)
                self.assertEqual(
                    Path(first, 'baselines.json').read_bytes(),
                    Path(second, 'baselines.json').read_bytes())
                self.assertEqual(
                    Path(first, 'report.md').read_bytes(),
                    Path(second, 'report.md').read_bytes())

    def test_markdown_cita_la_poblacion_y_los_baselines(self):
        report = build_report(panel_corto())
        markdown = render_markdown(report)
        self.assertIn(report['poblacion']['digest_sha256'], markdown)
        self.assertIn('B1_persistencia', markdown)
        self.assertIn('B2_media_movil_3', markdown)
        self.assertIn('B3_mediana_global', markdown)
        self.assertIn('Autocorrelacion', markdown)

    def test_baselines_comparables_por_construccion(self):
        # Invariante dura: n_pares identico entre B1/B2/B3 por horizonte.
        population = freeze_population(panel_corto())
        metricas = compute_baselines(population, [M0, M1])
        for h in HORIZONTES:
            valores = {name: metricas[name][h]['n_pares'] for name in metricas}
            self.assertEqual(len(set(valores.values())), 1, valores)


class CliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.con = duckdb.connect(':memory:')
        self.addCleanup(self.con.close)

    def parquet(self, path, rows, con_ventana=True):
        columns = [('company_id', 'VARCHAR'), ('month', 'DATE'),
                   ('health_score', 'DOUBLE'), ('excluida', 'BOOLEAN')]
        if con_ventana:
            columns.append(('ventana_parcial', 'BOOLEAN'))
        self.con.execute('CREATE OR REPLACE TEMP TABLE a (' + ','.join(
            f'{name} {kind}' for name, kind in columns) + ')')
        placeholders = ','.join('?' for _ in columns)
        self.con.executemany(
            f'INSERT INTO a VALUES ({placeholders})',
            [tuple([r['company_id'], r['month'], r['health_score'],
                    r['excluida']] + ([r['ventana_parcial']] if con_ventana else []))
             for r in rows])
        self.con.execute(f"COPY a TO '{path}' (FORMAT PARQUET)")

    def test_load_assessments_ventana_parcial_opcional(self):
        source = self.root / 'sin_ventana.parquet'
        self.parquet(source, panel_corto(), con_ventana=False)
        rows = load_assessments(source)
        self.assertEqual(len(rows), len(panel_corto()))
        self.assertFalse(any(r['ventana_parcial'] for r in rows))

    def test_load_assessments_columna_faltante(self):
        source = self.root / 'incompleto.parquet'
        self.con.execute('CREATE OR REPLACE TEMP TABLE b (company_id VARCHAR, '
                         'month DATE, health_score DOUBLE)')
        self.con.execute("INSERT INTO b VALUES ('C', DATE '2024-09-01', 1.0)")
        self.con.execute(f"COPY b TO '{source}' (FORMAT PARQUET)")
        with self.assertRaises(ValueError):
            load_assessments(source)

    def test_cli_escribe_baselines_y_report(self):
        source = self.root / 'assessments.parquet'
        self.parquet(source, panel_corto())
        output = self.root / 'out'
        stdout, errors = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(errors):
            code = main(['--input', str(source), '--output-dir', str(output)])
        self.assertEqual(code, 0, errors.getvalue())
        report = json.loads(Path(output, 'baselines.json').read_text())
        self.assertEqual(report['poblacion']['n_empresas'], 4)
        self.assertEqual(report['censo']['total_por_h']['1']['pares'], 4)
        self.assertTrue(Path(output, 'report.md').exists())
        printed = json.loads(stdout.getvalue())
        self.assertEqual(printed['n_empresas'], 4)

    def test_cli_check_reproduce_y_detecta_drift(self):
        source = self.root / 'assessments.parquet'
        self.parquet(source, panel_corto())
        output = self.root / 'out'
        with redirect_stdout(StringIO()):
            self.assertEqual(main(['--input', str(source),
                                   '--output-dir', str(output)]), 0)
        with redirect_stdout(StringIO()):
            self.assertEqual(main(['--input', str(source),
                                   '--output-dir', str(output),
                                   '--check']), 0)
        target = Path(output, 'baselines.json')
        target.write_text(target.read_text().replace('COMP_A', 'COMP_Z'))
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            self.assertEqual(main(['--input', str(source),
                                   '--output-dir', str(output),
                                   '--check']), 1)
        self.assertIn('NO reproduce', errors.getvalue())

    def test_cli_falla_sin_input(self):
        errors = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(errors):
            code = main(['--input', str(self.root / 'no.parquet'),
                         '--output-dir', str(self.root / 'out')])
        self.assertEqual(code, 1)
        self.assertIn('falta', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
