"""Tests de la grafica healthscore_v4 con fixtures sinteticas.

No dependen de reports/score_v4/assessments.parquet real: cada test monta su
propio parquet de v4, su summary.json y (cuando toca) un parquet de v3 para la
comparativa A-B, en un directorio temporal.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import duckdb

from xray import paths
from xray.score_chart_v4 import (ASSESSMENTS_NAME, SCHEMA, SUMMARY_NAME, build_payload,
                                 load_v3_scores, main, render_chart, score_segments)


def write_parquet(path, rows):
    con = duckdb.connect(':memory:')
    try:
        columns = ', '.join(f'{name} {kind}' for name, kind in SCHEMA)
        con.execute(f'CREATE TABLE t ({columns})')
        placeholders = ', '.join('?' for _ in SCHEMA)
        for row in rows:
            con.execute(f'INSERT INTO t VALUES ({placeholders})',
                        [row.get(name) for name, _ in SCHEMA])
        con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


def write_v3_parquet(path, pairs):
    """Parquet minimal de v3: (company_id, month, health_score, version).

    Los pares con score None van igualmente (el lector los ignora: nulo nunca
    es cero), igual que ocurre en el parquet real de v3.
    """
    con = duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE t (company_id VARCHAR, month DATE, '
                    'health_score DOUBLE, version VARCHAR)')
        for company, month, score in pairs:
            con.execute('INSERT INTO t VALUES (?, ?, ?, ?)',
                        [company, month, score, 'healthscore_v3'])
        con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


def fixture_row(company, month, score, confidence, reasons, penalties=(0.0, 0.0),
                excluida=False):
    row = {name: None for name, _ in SCHEMA}
    row.update({
        'version': 'healthscore_v4', 'company_id': company, 'group_id': 'G1',
        'month': month, 'as_of': month, 'health_score': score,
        'confidence': confidence, 'ventana_parcial': False,
        'excluida': excluida,
        't6_efectivo': 12000.0, 'colchon_v4': 4000.0,
        'mora_indice': 0.0 if confidence != 'ninguna' else None,
        'multiplicador_deuda': 1.0 if confidence != 'ninguna' else None,
        'obligacion_vencida_m': None, 'r_hist': 0.2,
        'k': 777.5, 'alpha': 1.25, 'beta': 0.4, 'reasons': reasons,
    })
    if score is not None:
        row['h_antes_de_ajustes'] = score + penalties[0] + penalties[1]
        row['penalizacion_mora_puntos'] = penalties[0]
        row['penalizacion_multiplicador_puntos'] = penalties[1]
    return row


def fixture_rows():
    rows = []
    for index, month in enumerate(('2026-07-01', '2026-08-01', '2026-09-01')):
        rows.append(fixture_row('ALTA', month, 62.0 + index, 'alta', ['colchon_saturado'],
                                penalties=(2.0, 1.0)))
        rows.append(fixture_row('SINNOTA', month, None, 'ninguna',
                                ['sin_actividad_de_caja_observada']))
        rows.append(fixture_row('CASTIGADA', month, 20.0 + index, 'media',
                                ['sin_mora_observable'], penalties=(8.0, 33.0)))
    # HUECO: nota, mes sin nota, nota -> ningun tramo cruza el mes vacio.
    rows.append(fixture_row('HUECO', '2026-07-01', 40.0, 'media', []))
    rows.append(fixture_row('HUECO', '2026-08-01', None, 'ninguna',
                            ['sin_flujos_observados_en_la_ventana']))
    rows.append(fixture_row('HUECO', '2026-09-01', 70.0, 'media', []))
    # MEDIA tiene nota solo en v4 (sin nota v3): caso 'solo v4'.
    rows.append(fixture_row('MEDIA', '2026-07-01', 75.0, 'baja', ['ventana_parcial']))
    rows.append(fixture_row('MEDIA', '2026-08-01', 80.0, 'media', []))
    return rows


def fixture_v3_pairs():
    return [
        ('ALTA', '2026-07-01', 60.0), ('ALTA', '2026-08-01', 62.0), ('ALTA', '2026-09-01', 70.0),
        ('SINNOTA', '2026-08-01', 55.0),  # solo v3 en ese mes: v4 no tiene nota
        ('HUECO', '2026-07-01', 40.0), ('HUECO', '2026-08-01', 45.0), ('HUECO', '2026-09-01', 72.0),
    ]


def fixture_summary(extra=None):
    summary = {
        'model_version': 'healthscore_v4',
        'formula': 'H = fixture',
        'data_workspace': 'data/runs/fixture',
        'params': {'k': 777.5, 'alpha': 1.25, 'beta': 0.4},
        'params_calibrados': {'k': False, 'alpha': False, 'beta': False},
        'advertencia': 'alpha y beta son PROVISIONALES sin recalibrar para v4',
        'limitaciones': ['alpha y beta NO estan calibrados', 'no es un rating crediticio'],
    }
    if extra:
        summary.update(extra)
    return summary


def fixture_payload(root, with_v3=True, summary_extra=None):
    source = root / 'reports' / 'score_v4'
    source.mkdir(parents=True, exist_ok=True)
    write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
    (source / SUMMARY_NAME).write_text(
        json.dumps(fixture_summary(summary_extra), ensure_ascii=False), encoding='utf-8')
    v3_path = None
    if with_v3:
        v3_dir = root / 'reports' / 'score_v3'
        v3_dir.mkdir(parents=True, exist_ok=True)
        write_v3_parquet(v3_dir / ASSESSMENTS_NAME, fixture_v3_pairs())
        v3_path = v3_dir / ASSESSMENTS_NAME
    return build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME, v3_path)


def fixture_rows_con_excluida():
    """Las filas base mas una empresa EXCL por nota 0 persistente: su fila se
    conserva con health_score = NULL, excluida = true y el motivo en reasons
    (en los 3 meses: la exclusion de perimetro aplica a todo el panel)."""
    rows = fixture_rows()
    for month in ('2026-07-01', '2026-08-01', '2026-09-01'):
        rows.append(fixture_row('EXCL', month, None, 'ninguna',
                                ['excluida_nota_cero_persistente'],
                                excluida=True))
    return rows


class ExcluidasTest(unittest.TestCase):
    """Conmutador 'mostrar excluidas': fuera por defecto, consultables al activarlo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        source = root / 'reports' / 'score_v4'
        source.mkdir(parents=True, exist_ok=True)
        write_parquet(source / ASSESSMENTS_NAME, fixture_rows_con_excluida())
        (source / SUMMARY_NAME).write_text(
            json.dumps(fixture_summary(), ensure_ascii=False), encoding='utf-8')
        cls.payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_excluida_fuera_de_las_distribuciones_por_defecto(self):
        cut = self.payload['cuts']['2026-09-01']
        # Por defecto: ALTA, SINNOTA, CASTIGADA, HUECO (EXCL fuera).
        self.assertEqual(cut['total'], 4)
        self.assertEqual(cut['excluidas'], 1)
        self.assertEqual(sum(sum(bin.values()) for bin in cut['bins'])
                         + sum(cut['null_motivos'].values()), cut['total'])
        # Con el conmutador: la variante 'todas' si la incluye.
        todas = cut['todas']
        self.assertEqual(todas['total'], 5)
        self.assertEqual(sum(todas['confidence'].values()), todas['total'])
        self.assertEqual(sum(sum(bin.values()) for bin in todas['bins'])
                         + sum(todas['null_motivos'].values()), todas['total'])
        self.assertIn('excluida_nota_cero_persistente', ' '.join(todas['null_motivos']))

    def test_excluida_marcada_y_consultable(self):
        page = render_chart(self.payload)
        embedded = embedded_payload(page)
        self.assertEqual(embedded['n_excluidas'], 1)
        self.assertTrue(embedded['exclusion_activada'])
        excl = next(c for c in embedded['companies'] if c['id'] == 'EXCL')
        self.assertTrue(excl['excluida'])
        for point in excl['points']:
            self.assertNotIn('s', point)  # sin nota: NULL, nunca 0
            motivos = embedded['reasons_sets'][point['r']]
            self.assertIn('excluida_nota_cero_persistente', motivos)

    def test_conmutador_y_declaracion_en_la_cabecera(self):
        page = render_chart(self.payload)
        self.assertIn('mostrar-excluidas', page)
        self.assertIn('Mostrar excluidas', page)
        # La caja de la cabecera existe y el recuento y el motivo viajan
        # embebidos (el JS los pinta al cargar).
        self.assertIn('id="exclusion-box"', page)
        self.assertIn('id="exclusion-count"', page)
        self.assertIn('excluida_nota_cero_persistente', page)

    def test_declaracion_viene_de_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declaracion = 'Declaracion de prueba con las cifras medidas.'
            source = root / 'reports' / 'score_v4'
            source.mkdir(parents=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows_con_excluida())
            (source / SUMMARY_NAME).write_text(json.dumps(fixture_summary({
                'exclusion': {'activada': True, 'declaracion': declaracion}}),
                ensure_ascii=False), encoding='utf-8')
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            self.assertEqual(payload['declaracion_exclusion'], declaracion)
            embedded = embedded_payload(render_chart(payload))
            self.assertEqual(embedded['declaracion_exclusion'], declaracion)

    def test_poblacion_y_conteo_de_excluidas(self):
        self.assertEqual(self.payload['total_companies'], 6)
        self.assertEqual(self.payload['n_excluidas'], 1)
        ids = [company['id'] for company in self.payload['companies']]
        self.assertIn('EXCL', ids)

    def test_sin_excluidas_no_hay_conmutador(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = fixture_payload(Path(directory))
            self.assertEqual(payload['n_excluidas'], 0)
            self.assertFalse(payload['exclusion_activada'])
            self.assertNotIn('EMPRESAS EXCLUIDAS: 0', render_chart(payload))
            embedded = embedded_payload(render_chart(payload))
            self.assertFalse(embedded['exclusion_activada'])
            for cut in embedded['cuts'].values():
                self.assertNotIn('todas', cut)


def embedded_payload(page):
    return json.loads(re.search(
        r'<script id="payload-data" type="application/json">(.*?)</script>',
        page, re.S).group(1))


class PayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.payload = fixture_payload(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_page_is_self_contained(self):
        page = render_chart(self.payload)
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('<script src=', page)
        self.assertNotIn('href=', page)
        self.assertNotIn('@import', page)
        self.assertIn('application/json', page)

    def test_company_without_note_is_counted_and_never_omitted(self):
        cut = self.payload['cuts']['2026-09-01']
        # En 2026-09 MEDIA no tiene fila (no existe en el corte): 4 de 5 empresas.
        self.assertEqual(cut['total'], 4)
        self.assertEqual(cut['confidence']['ninguna'], 1)
        self.assertIn('sin_actividad_de_caja_observada', ' '.join(cut['null_motivos']))
        ids = [company['id'] for company in self.payload['companies']]
        self.assertIn('SINNOTA', ids)
        self.assertEqual(len(ids), 5)
    def test_confidence_counts_sum_to_cut_total(self):
        for month, cut in self.payload['cuts'].items():
            with self.subTest(month=month):
                self.assertEqual(sum(cut['confidence'].values()), cut['total'])
                from_bins = sum(sum(bin.values()) for bin in cut['bins'])
                from_null = sum(cut['null_motivos'].values())
                self.assertEqual(from_bins + from_null, cut['total'])

    def test_month_without_score_is_not_interpolated(self):
        hueco = next(row for row in self.payload['companies'] if row['id'] == 'HUECO')
        self.assertEqual(hueco['segments'], [[[0, 40.0]], [[2, 70.0]]])
        alta = next(row for row in self.payload['companies'] if row['id'] == 'ALTA')
        self.assertEqual(alta['segments'], [[[0, 62.0], [1, 63.0], [2, 64.0]]])

    def test_month_without_score_is_never_zero_in_embedded_data(self):
        """Criterio 4: la empresa sin nota en un mes NO vale 0 en el embebido."""
        page = render_chart(self.payload)
        embedded = embedded_payload(page)
        hueco = next(c for c in embedded['companies'] if c['id'] == 'HUECO')
        gap = hueco['points'][1]
        self.assertNotIn('s', gap)
        self.assertNotIn(0.0, [value for key, value in gap.items()
                               if key in ('s', 'h', 'pm', 'pd')])
        # La nota ausente no aparece como 0 en ningun sitio del punto:
        self.assertEqual(gap.get('s', None), None)
        for segment in hueco['segments']:
            indexes = [point[0] for point in segment]
            self.assertEqual(indexes, list(range(indexes[0], indexes[0] + len(indexes))))
        # Y el motivo sigue embebido y alcanzable (catalogo de reasons).
        motivos = embedded['reasons_sets'][gap['r']]
        self.assertIn('sin_flujos_observados_en_la_ventana', motivos)
        # La vista 1 declara el hueco con marcador, no un valor cero.
        self.assertIn('SIN NOTA (hueco, no cero)', page)
        # El corte 2026-08 cuenta la empresa sin nota y su motivo.
        self.assertEqual(embedded['cuts']['2026-08-01']['null_motivos']
                         ['sin_flujos_observados_en_la_ventana'], 1)

    def test_vista4_inputs_are_embedded_for_scored_rows(self):
        page = render_chart(self.payload)
        embedded = embedded_payload(page)
        castigada = next(c for c in embedded['companies'] if c['id'] == 'CASTIGADA')
        for point in castigada['points']:
            self.assertIn('s', point)
            self.assertAlmostEqual(point['h'], point['s'] + point['pm'] + point['pd'])
            self.assertIn('ob', point)
            self.assertIn('mi', point)
            self.assertIn('md', point)
            self.assertIn('t6', point)
            self.assertIn('cv', point)

    def test_comparativa_ab_is_embedded_from_v3_parquet(self):
        payload = self.payload
        self.assertTrue(payload['v3_disponible'])
        alta = next(c for c in payload['companies'] if c['id'] == 'ALTA')
        self.assertEqual([point.get('v3') for point in alta['points']], [60.0, 62.0, 70.0])
        sinnota = next(c for c in payload['companies'] if c['id'] == 'SINNOTA')
        # Caso 'solo v3': v3 55 en agosto, v4 sin nota; los otros meses sin v3.
        self.assertIsNone(sinnota['points'][0].get('v3'))
        self.assertEqual(sinnota['points'][1].get('v3'), 55.0)
        media = next(c for c in payload['companies'] if c['id'] == 'MEDIA')
        self.assertTrue(all('v3' not in point for point in media['points']))

    def test_comparativa_degrades_without_v3_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = fixture_payload(Path(directory), with_v3=False)
            self.assertFalse(payload['v3_disponible'])
            page = render_chart(payload)
            self.assertIn('Comparativa no disponible', page)
            embedded = embedded_payload(page)
            self.assertFalse(embedded['v3_disponible'])
            self.assertFalse(any('v3' in point for company in embedded['companies']
                                 for point in company['points']))

    def test_comparativa_rejects_other_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reports' / 'score_v4'
            source.mkdir(parents=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            other = Path(directory) / 'v3_falsa.parquet'
            con = duckdb.connect(':memory:')
            try:
                con.execute('CREATE TABLE t (company_id VARCHAR, month DATE, '
                            'health_score DOUBLE, version VARCHAR)')
                con.execute("INSERT INTO t VALUES ('ALTA', DATE '2026-07-01', 60.0, "
                            "'healthscore_v9')")
                con.execute(f"COPY t TO '{other}' (FORMAT PARQUET)")
            finally:
                con.close()
            with self.assertRaises(ValueError):
                build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME, other)

    def test_cli_aborts_when_output_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'index.html'
            root = Path(self.tmp.name)
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--output', str(output)]), 0)
            first = output.read_bytes()
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(['--output', str(output)])
            self.assertEqual(output.read_bytes(), first)

    def test_cli_fails_with_clear_message_without_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory)
            with patch.object(paths, 'ROOT', empty), \
                 redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
                main(['--output', str(empty / 'out.html')])
            self.assertIn('falta reports/score_v4/assessments.parquet', str(raised.exception))
            self.assertIn('xray.scoring_io_v4', str(raised.exception))
            self.assertFalse((empty / 'out.html').exists())

    def test_header_params_come_from_the_file_not_module_constants(self):
        payload = self.payload
        self.assertEqual(payload['k'], 777.5)
        self.assertEqual(payload['alpha'], 1.25)
        self.assertEqual(payload['beta'], 0.4)
        page = render_chart(payload)
        embedded = embedded_payload(page)
        self.assertEqual(embedded['k'], 777.5)
        self.assertEqual(embedded['formula'], 'H = fixture')
        self.assertEqual(embedded['data_workspace'], 'data/runs/fixture')
        self.assertEqual(embedded['model_version'], 'healthscore_v4')

    def test_params_provisional_are_declared_in_the_page(self):
        page = render_chart(self.payload)
        self.assertIn('PROVISIONALES', page)
        self.assertIn('alpha y beta son PROVISIONALES sin recalibrar para v4', page)

    def test_shapley_top_is_embedded_and_sourced_from_summary(self):
        summary_extra = {
            'riesgo_triple_conteo': {
                'descomposicion_castigo': {
                    'corte': '2026-09-30',
                    'top_penalizadas': [{
                        'company_id': 'CASTIGADA', 'nota_final': 21.0,
                        'nota_sin_castigos': 99.0, 'castigo_total_puntos': 78.0,
                        'castigo_obligacion_en_denominador_puntos': 37.0,
                        'castigo_mora_indice_puntos': 8.0,
                        'castigo_multiplicador_deuda_puntos': 33.0,
                    }],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            payload = fixture_payload(Path(directory), summary_extra=summary_extra)
            self.assertEqual(len(payload['shapley_top']), 1)
            self.assertEqual(payload['shapley_top'][0]['id'], 'CASTIGADA')
            page = render_chart(payload)
            self.assertIn('summary.json', page)
            self.assertIn('CASTIGADA', page)
            self.assertIn('78', page)

    def test_payload_serialization_rejects_nan_by_sanitizing(self):
        row = fixture_row('NAN', '2026-07-01', float('nan'), 'media', [])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows() + [row])
            (source / SUMMARY_NAME).write_text(
                json.dumps(fixture_summary(), ensure_ascii=False), encoding='utf-8')
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            company = next(c for c in payload['companies'] if c['id'] == 'NAN')
            self.assertNotIn('s', company['points'][0])
            page = render_chart(payload)
        self.assertNotIn('NaN', page)
        self.assertNotIn('Infinity', page)

    def test_determinism_same_input_same_html(self):
        first = render_chart(self.payload)
        second = render_chart(self.payload)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            for run in range(2):
                output = Path(directory) / f'run{run}' / 'index.html'
                with patch.object(paths, 'ROOT', Path(self.tmp.name)), \
                     redirect_stdout(StringIO()):
                    self.assertEqual(main(['--output', str(output)]), 0)
                outputs.append(output.read_text(encoding='utf-8'))
            self.assertEqual(outputs[0], outputs[1])

    def test_segments_helper_splits_at_null(self):
        self.assertEqual(score_segments([10.0, None, None, 20.0, 30.0]),
                         [[[0, 10.0]], [[3, 20.0], [4, 30.0]]])
        self.assertEqual(score_segments([]), [])
        self.assertEqual(score_segments([None]), [])

    @unittest.skipUnless(shutil.which('node'), 'Node no disponible')
    def test_javascript_syntax(self):
        page = render_chart(self.payload)
        script = re.search(r'<script id="chart-code">(.*?)</script>', page, re.S).group(1)
        check = subprocess.run(['node', '--check'], input=script, text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr)


class BannerTest(unittest.TestCase):
    """Banner y titulo DEMO SINTETICA / banner_texto configurable."""

    @staticmethod
    def _render_with_summary(extra):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reports' / 'score_v4'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            summary = fixture_summary(extra)
            (source / SUMMARY_NAME).write_text(json.dumps(summary, ensure_ascii=False),
                                               encoding='utf-8')
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            return render_chart(payload)

    def test_banner_and_demo_title_with_flag(self):
        page = self._render_with_summary({'es_demo_sintetica': True})
        self.assertTrue(page.split('<title>', 1)[1].startswith('DEMO SINTETICA'))
        self.assertIn('demo-sintetica-banner', page)
        self.assertIn('DEMOSTRACIÓN CON DATOS SINTÉTICOS GENERADOS ALEATORIAMENTE', page)
        self.assertLess(page.index('demo-sintetica-banner'), page.index('<main>'))
        self.assertIn('position:sticky;top:0', page)

    def test_no_banner_without_the_flag(self):
        page = self._render_with_summary({})
        self.assertNotIn('demo-sintetica-banner', page)
        self.assertNotIn('DEMOSTRACIÓN CON DATOS SINTÉTICOS', page)

    def test_banner_text_is_configurable(self):
        texto = 'DATOS REALES CON DEFECTOS CONOCIDOS. Sirve para revisar, no para decidir.'
        page = self._render_with_summary({'banner_texto': texto})
        self.assertIn('aviso-banner', page)
        self.assertIn(texto, page)
        self.assertLess(page.index('aviso-banner'), page.index('<main>'))
        self.assertIn('position:sticky;top:0', page)

    def test_no_banner_without_aviso_field(self):
        page = self._render_with_summary({})
        self.assertNotIn('aviso-banner', page)


class InputDirTest(unittest.TestCase):
    """--input-dir / --v3-dir con el comportamiento por defecto intacto."""

    def test_default_input_dir_still_reports_score_v4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(paths, 'ROOT', root), redirect_stderr(StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                main(['--output', str(root / 'out.html')])
            self.assertIn('falta reports/score_v4/assessments.parquet', str(raised.exception))
            self.assertIn('xray.scoring_io_v4', str(raised.exception))
            self.assertFalse((root / 'out.html').exists())

    def test_default_input_dir_reads_reports_score_v4_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'reports' / 'score_v4'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            (source / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v4'}, ensure_ascii=False), encoding='utf-8')
            out = root / 'salidas' / 'index.html'
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--output', str(out)]), 0)
            self.assertIn('CASTIGADA', out.read_text(encoding='utf-8'))

    def test_input_dir_reads_from_the_given_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / 'datos_demo'
            custom.mkdir()
            write_parquet(custom / ASSESSMENTS_NAME, fixture_rows())
            (custom / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v4'}, ensure_ascii=False), encoding='utf-8')
            out = root / 'salidas' / 'index.html'
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--input-dir', str(custom), '--output', str(out)]), 0)
            self.assertIn('HUECO', out.read_text(encoding='utf-8'))

    def test_input_dir_error_mentions_the_used_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / 'datos_demo'
            custom.mkdir()
            with patch.object(paths, 'ROOT', root), redirect_stderr(StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                main(['--input-dir', str(custom), '--output', str(root / 'out.html')])
            self.assertIn(str(custom / ASSESSMENTS_NAME), str(raised.exception))

    def test_missing_v3_dir_still_generates_with_declared_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'reports' / 'score_v4'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            (source / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v4'}, ensure_ascii=False), encoding='utf-8')
            out = root / 'salidas' / 'index.html'
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--output', str(out)]), 0)
            page = out.read_text(encoding='utf-8')
            self.assertIn('Comparativa no disponible', page)
            self.assertIn('reports/score_v3/assessments.parquet', page)

    def test_custom_v3_dir_is_used_for_the_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'reports' / 'score_v4'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            (source / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v4'}, ensure_ascii=False), encoding='utf-8')
            custom_v3 = root / 'otra_v3'
            custom_v3.mkdir()
            write_v3_parquet(custom_v3 / ASSESSMENTS_NAME, fixture_v3_pairs())
            out = root / 'salidas' / 'index.html'
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--v3-dir', str(custom_v3), '--output', str(out)]), 0)
            embedded = embedded_payload(out.read_text(encoding='utf-8'))
            self.assertTrue(embedded['v3_disponible'])
            self.assertIn(str(custom_v3 / ASSESSMENTS_NAME), embedded['v3_ruta'])


def synthetic_month_list(n_months):
    months = []
    year, month = 2024, 10
    for _ in range(n_months):
        months.append(__import__('datetime').date(year, month, 1))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return months


class FullRunSmokeTest(unittest.TestCase):
    """Un run completo con el tamano real declarado, en temporal (no en reports/)."""

    def test_full_grid_payload_stays_reasonable(self):
        import datetime
        companies, months_count = 60, 12
        months = synthetic_month_list(months_count)
        rows = []
        for company_index in range(1, companies + 1):
            company_id = f'SINT-{company_index:04d}'
            for month in months:
                if company_id == 'SINT-0007' and month == months[5]:
                    rows.append(fixture_row(company_id, month, None, 'ninguna',
                                            ['sin_flujos_observados_en_la_ventana']))
                    continue
                rows.append(fixture_row(company_id, month, 40.0 + company_index % 50,
                                        'media', [], penalties=(1.0, 2.0)))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            write_parquet(source / ASSESSMENTS_NAME, rows)
            (source / SUMMARY_NAME).write_text(json.dumps(fixture_summary(), ensure_ascii=False),
                                               encoding='utf-8')
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            self.assertEqual(len(payload['months']), months_count)
            self.assertEqual(payload['total_companies'], companies)
            page = render_chart(payload)
            self.assertIn('sin_flujos_observados_en_la_ventana', page)
            embedded = embedded_payload(page)
            sint7 = next(c for c in embedded['companies'] if c['id'] == 'SINT-0007')
            self.assertNotIn('s', sint7['points'][5])
            self.assertNotEqual(sint7['points'][5].get('s'), 0)
        # datetime importado a nivel de modulo para el generador de meses:
        self.assertTrue(datetime.date(2024, 10, 1))


if __name__ == '__main__':
    unittest.main()
