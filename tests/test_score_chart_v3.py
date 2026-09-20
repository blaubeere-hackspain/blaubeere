"""Tests de la grafica healthscore_v3 con fixtures sinteticas.

No dependen de reports/score_v3/assessments.parquet real: cada test monta su
propio parquet y summary.json en un directorio temporal.
"""

import json
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import duckdb

from xray import paths
from xray.score_chart_v3 import (ASSESSMENTS_NAME, SCHEMA, SUMMARY_NAME, build_payload,
                                 main, render_chart, score_segments)


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


def fixture_row(company, month, score, confidence, reasons):
    row = {name: None for name, _ in SCHEMA}
    row.update({
        'version': 'healthscore_v3', 'company_id': company, 'group_id': 'G1',
        'month': month, 'as_of': month, 'health_score': score,
        'confidence': confidence, 'ventana_parcial': False,
        'k': 777.5, 'alpha': 1.25, 'beta': 0.4, 'reasons': reasons,
    })
    return row


def fixture_rows():
    rows = []
    for index, month in enumerate(('2026-07-01', '2026-08-01', '2026-09-01')):
        rows.append(fixture_row('ALTA', month, 62.0 + index, 'alta', ['colchon_saturado']))
        rows.append(fixture_row('SINNOTA', month, None, 'ninguna',
                                ['sin_actividad_de_caja_observada']))
    # HUECO: nota, mes sin nota, nota -> ningun tramo cruza el mes vacio.
    rows.append(fixture_row('HUECO', '2026-07-01', 40.0, 'media', []))
    rows.append(fixture_row('HUECO', '2026-08-01', None, 'baja', ['denominador_nulo']))
    rows.append(fixture_row('HUECO', '2026-09-01', 70.0, 'media', []))
    # MEDIA no tiene fila en el tercer mes: no existe en ese corte.
    rows.append(fixture_row('MEDIA', '2026-07-01', 75.0, 'baja', ['ventana_parcial']))
    rows.append(fixture_row('MEDIA', '2026-08-01', 80.0, 'media', []))
    return rows


def fixture_payload(root):
    source = root / 'reports' / 'score_v3'
    source.mkdir(parents=True, exist_ok=True)
    write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
    (source / SUMMARY_NAME).write_text(json.dumps({
        'model_version': 'healthscore_v3',
        'formula': 'H = fixture',
        'data_workspace': 'data/runs/fixture',
        'limitaciones': ['alpha y beta NO estan calibrados', 'no es un rating crediticio'],
    }, ensure_ascii=False), encoding='utf-8')
    return build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)


class PayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.payload = fixture_payload(root)

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
        self.assertIn('--ink:#142c42', page)
        self.assertIn('application/json', page)

    def test_company_without_note_is_counted_and_never_omitted(self):
        cut = self.payload['cuts']['2026-09-01']
        self.assertEqual(cut['total'], 3)
        self.assertEqual(cut['confidence']['ninguna'], 1)
        self.assertIn('sin_actividad_de_caja_observada', ' '.join(cut['null_motivos']))
        ids = [company['id'] for company in self.payload['companies']]
        self.assertIn('SINNOTA', ids)
        self.assertEqual(len(ids), 4)

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
        # Y la pagina no pinta un unico trazo cruzando el hueco.
        page = render_chart(self.payload)
        embedded = json.loads(re.search(
            r'<script id="payload-data" type="application/json">(.*?)</script>',
            page, re.S).group(1))
        segments = next(c['segments'] for c in embedded['companies'] if c['id'] == 'HUECO')
        for segment in segments:
            indexes = [point[0] for point in segment]
            self.assertEqual(indexes, list(range(indexes[0], indexes[0] + len(indexes))))

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
            self.assertIn('falta reports/score_v3/assessments.parquet', str(raised.exception))
            self.assertIn('xray.scoring_io_v3', str(raised.exception))
            self.assertFalse((empty / 'out.html').exists())

    def test_header_params_come_from_the_file_not_module_constants(self):
        payload = self.payload
        self.assertEqual(payload['k'], 777.5)
        self.assertEqual(payload['alpha'], 1.25)
        self.assertEqual(payload['beta'], 0.4)
        self.assertNotEqual(payload['k'], 1000.0)
        page = render_chart(payload)
        embedded = json.loads(re.search(r'<script id="payload-data"[^>]*>(.*?)</script>',
                                        page, re.S).group(1))
        self.assertEqual(embedded['k'], 777.5)
        self.assertEqual(embedded['formula'], 'H = fixture')
        self.assertEqual(embedded['data_workspace'], 'data/runs/fixture')
        self.assertEqual(embedded['model_version'], 'healthscore_v3')

    def test_payload_serialization_rejects_nan_by_sanitizing(self):
        row = fixture_row('NAN', '2026-07-01', float('nan'), 'media', [])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows() + [row])
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            company = next(c for c in payload['companies'] if c['id'] == 'NAN')
            self.assertIsNone(company['points'][0]['score'])
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


class DemoBannerTest(unittest.TestCase):
    """Banner y titulo DEMO SINTETICA solo con es_demo_sintetica=true."""

    @staticmethod
    def _render_with_summary(extra):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reports' / 'score_v3'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            summary = {'model_version': 'healthscore_v3', 'formula': 'H = fixture',
                       'data_workspace': 'data/runs/fixture', 'limitaciones': ['x']}
            summary.update(extra)
            (source / SUMMARY_NAME).write_text(json.dumps(summary, ensure_ascii=False),
                                               encoding='utf-8')
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            return render_chart(payload)

    def test_banner_and_demo_title_with_flag(self):
        page = self._render_with_summary({'es_demo_sintetica': True})
        self.assertTrue(page.split('<title>', 1)[1].startswith('DEMO SINTETICA'))
        self.assertIn('demo-sintetica-banner', page)
        self.assertIn('DEMOSTRACIÓN CON DATOS SINTÉTICOS GENERADOS ALEATORIAMENTE', page)
        # Banner fijo y en lo alto: antes del contenido principal.
        self.assertLess(page.index('demo-sintetica-banner'), page.index('<main>'))
        self.assertIn('position:sticky;top:0', page)

    def test_no_banner_without_the_flag(self):
        page = self._render_with_summary({})
        self.assertNotIn('demo-sintetica-banner', page)
        self.assertNotIn('DEMOSTRACIÓN CON DATOS SINTÉTICOS', page)
        self.assertTrue(page.split('<title>', 1)[1].startswith('Blaubeere · Healthscore v3'))

    def test_banner_text_is_configurable(self):
        texto = ('DATOS REALES CON DEFECTOS CONOCIDOS, PENDIENTES DE CORRECCION. '
                 'Sirve para revisar, no para decidir.')
        page = self._render_with_summary({'banner_texto': texto})
        self.assertIn('aviso-banner', page)
        self.assertIn(texto, page)
        self.assertNotIn('demo-sintetica-banner', page)
        self.assertNotIn('DEMOSTRACIÓN CON DATOS SINTÉTICOS', page)
        # Mismo mecanismo: banner fijo y en lo alto, antes del contenido principal.
        self.assertLess(page.index('aviso-banner'), page.index('<main>'))
        self.assertIn('position:sticky;top:0', page)

    def test_no_banner_without_aviso_field(self):
        page = self._render_with_summary({})
        self.assertNotIn('aviso-banner', page)
        self.assertNotIn('&#9888;', page)
        self.assertTrue(page.split('<title>', 1)[1].startswith('Blaubeere · Healthscore v3'))


def synthetic_month_list(n_months):
    months = []
    year, month = 2024, 10
    for _ in range(n_months):
        months.append(date(year, month, 1))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return months


# Semilla fija y declarada de la demo; generador reproducible:
#   .venv/bin/python -B -c "from pathlib import Path; \
#     from tests.test_score_chart_v3 import generate_synthetic; \
#     generate_synthetic(Path('/tmp/demo_score_v3'))"
SYNTHETIC_SEED = 20260917
SYNTHETIC_COMPANIES = 1286
SYNTHETIC_MONTHS = 24
SYNTHETIC_PARAMS = {'k': 4178.45, 'alpha': 3.0, 'beta': 0.25}


def _synthetic_score(rng):
    """Masa cerca de 50 con colas en 0 y 100, repartido por todo 0..100."""
    roll = rng.random()
    if roll < 0.68:
        score = 50.0 + rng.gauss(0.0, 12.0)
    elif roll < 0.80:
        score = rng.uniform(0.0, 100.0)
    elif roll < 0.90:
        score = abs(rng.gauss(0.0, 6.0))
    else:
        score = 100.0 - abs(rng.gauss(0.0, 6.0))
    return round(min(100.0, max(0.0, score)), 1)


def generate_synthetic(directory, seed=SYNTHETIC_SEED, companies=SYNTHETIC_COMPANIES,
                       months_count=SYNTHETIC_MONTHS):
    """Genera assessments.parquet y summary.json sinteticos con el esquema cerrado.

    Solo para la demo de la grafica: escribe en un directorio temporal FUERA del
    repo (nunca en reports/). Semilla fija y declarada, reproducible.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    months = synthetic_month_list(months_count)
    rows = []
    confidence_counts = {'alta': 0, 'media': 0, 'baja': 0, 'ninguna': 0}
    null_count = 0
    mora_present = 0
    ambiguous_positive = 0
    for company_index in range(1, companies + 1):
        company_id = f'SINT-{company_index:04d}'
        group_id = f'G{company_index % 43:02d}'
        # Algunas empresas arrancan mas tarde: sus primeros meses son ventana parcial.
        parcial_hasta = 0
        if rng.random() < 0.15:
            parcial_hasta = rng.randint(2, 4)
        for month_index in range(months_count):
            month = months[month_index]
            ventana_parcial = month_index < parcial_hasta
            n_meses_ventana = min(6, month_index + 1)
            n_meses_con_actividad = max(0, n_meses_ventana - (1 if rng.random() < 0.15 else 0))
            row = {name: None for name, _ in SCHEMA}
            row.update({
                'version': 'healthscore_v3', 'company_id': company_id, 'group_id': group_id,
                'month': month, 'as_of': month, 'ventana_parcial': ventana_parcial,
                'n_meses_ventana': n_meses_ventana,
                'n_meses_con_actividad': n_meses_con_actividad,
                'k': SYNTHETIC_PARAMS['k'], 'alpha': SYNTHETIC_PARAMS['alpha'],
                'beta': SYNTHETIC_PARAMS['beta'], 'reasons': [],
            })
            if rng.random() < 0.02:  # ~2% de empresa-mes sin nota, con su motivo
                row['confidence'] = 'ninguna'
                row['reasons'] = ['sin_actividad_de_caja_observada']
                null_count += 1
                confidence_counts['ninguna'] += 1
                rows.append(row)
                continue
            health = _synthetic_score(rng)
            confidence_roll = rng.random()
            # Los ~2% de nulos ya son 'ninguna': reparte el resto para cuadrar ~60/20/15/5.
            if confidence_roll < 0.6122:
                confidence = 'alta'
            elif confidence_roll < 0.8163:
                confidence = 'media'
            elif confidence_roll < 0.9694:
                confidence = 'baja'
            else:
                confidence = 'ninguna'
            confidence_counts[confidence] += 1
            if rng.random() < 0.58:
                mora_ratio = None  # empresas sin cartera
            else:
                mora_ratio = round(min(1.0, max(0.0, rng.betavariate(1.2, 5.9))), 4)
                mora_present += 1
            colchon_roll = rng.random()
            colchon_bruto = round(rng.expovariate(1 / 4000.0) if colchon_roll < 0.85
                                  else rng.expovariate(1 / 120000.0), 2)
            colchon_aplicable = round(colchon_bruto * rng.uniform(0.4, 1.0), 2)
            amb_pct = 0.0 if rng.random() < 0.75 else round(min(100.0, rng.expovariate(1 / 12.0)), 1)
            if amb_pct > 0:
                ambiguous_positive += 1
            c6 = round(health / 100.0 * rng.uniform(20000.0, 120000.0), 2)
            t6 = round(health / 100.0 * rng.uniform(10000.0, 90000.0), 2)
            p6 = round(c6 * rng.uniform(0.05, 0.35), 2)
            d6 = round(t6 * rng.uniform(0.05, 0.30), 2)
            r_hist = round(min(1.0, rng.betavariate(2.0, 5.0)), 4)
            if mora_ratio is None:
                h_antes = health
                penalizacion = 0.0
            else:
                h_antes = round(min(100.0, health / (1.0 - SYNTHETIC_PARAMS['beta'] * mora_ratio)), 1)
                penalizacion = round(h_antes - health, 1)
            row.update({
                'health_score': health, 'confidence': confidence,
                'c6': c6, 't6': t6, 'p6': p6, 'd6': d6, 'r_hist': r_hist,
                'colchon_bruto': colchon_bruto, 'colchon_aplicable': colchon_aplicable,
                'mora_ratio': mora_ratio, 'h_antes_de_mora': h_antes,
                'penalizacion_mora_puntos': penalizacion,
                'volumen_ambiguo_pct': amb_pct,
                'volumen_ambiguo_eur': round(amb_pct / 100.0 * (c6 + t6), 2),
                'reasons': ['ventana_parcial'] if ventana_parcial else [],
            })
            rows.append(row)
    write_parquet(directory / ASSESSMENTS_NAME, rows)
    total = len(rows)
    summary = {
        'model_version': 'healthscore_v3',
        'formula': ('H = 100 * (C6 + colchon_aplicable + k * R_hist) / '
                    '(C6 + colchon_aplicable + T6 + k); H_final = H * (1 - beta * mora_ratio)'),
        'data_workspace': f'demo_sintetica_seed_{seed}',
        'es_demo_sintetica': True,
        'semilla': seed,
        'generador': 'tests/test_score_chart_v3.py::generate_synthetic',
        'resumen': {
            'empresas': companies, 'meses': months_count, 'filas': total,
            'filas_sin_nota': null_count,
            'sin_nota_pct': round(100.0 * null_count / total, 2) if total else 0.0,
            'confidence': {level: round(100.0 * count / total, 1)
                           for level, count in confidence_counts.items()},
            'mora_ratio_con_valor': mora_present,
            'mora_ratio_nulo_pct': round(100.0 * (total - mora_present) / total, 1),
            'volumen_ambiguo_pct_positivo': ambiguous_positive,
            'volumen_ambiguo_pct_positivo_pct': round(100.0 * ambiguous_positive / total, 1),
        },
        'params': dict(SYNTHETIC_PARAMS),
        'limitaciones': [
            f'DATOS SINTÉTICOS GENERADOS ALEATORIAMENTE con la semilla {seed}; '
            'ninguna cifra corresponde a una empresa real.',
            'Sirve unicamente para revisar el diseño de la grafica healthscore_v3.',
            'alpha y beta NO estan calibrados; no es un rating crediticio.',
        ],
    }
    (directory / SUMMARY_NAME).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
    return directory


class InputDirTest(unittest.TestCase):
    """P1: --input-dir con el comportamiento por defecto intacto."""

    def test_default_input_dir_still_reports_score_v3(self):
        # Sin --input-dir se sigue leyendo reports/score_v3 bajo paths.ROOT:
        # si falta, el mensaje de siempre lo dice.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(paths, 'ROOT', root), redirect_stderr(StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                main(['--output', str(root / 'out.html')])
            self.assertIn('falta reports/score_v3/assessments.parquet', str(raised.exception))
            self.assertIn('xray.scoring_io_v3', str(raised.exception))
            self.assertFalse((root / 'out.html').exists())

    def test_default_input_dir_reads_reports_score_v3_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'reports' / 'score_v3'
            source.mkdir(parents=True, exist_ok=True)
            write_parquet(source / ASSESSMENTS_NAME, fixture_rows())
            (source / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v3'}, ensure_ascii=False), encoding='utf-8')
            out = root / 'salidas' / 'index.html'
            with patch.object(paths, 'ROOT', root), redirect_stdout(StringIO()):
                self.assertEqual(main(['--output', str(out)]), 0)
            self.assertIn('ALTA', out.read_text(encoding='utf-8'))

    def test_input_dir_reads_from_the_given_directory(self):
        # ROOT queda vacio: si el CLI leyera el default, fallaria.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / 'datos_demo'
            custom.mkdir()
            write_parquet(custom / ASSESSMENTS_NAME, fixture_rows())
            (custom / SUMMARY_NAME).write_text(json.dumps(
                {'model_version': 'healthscore_v3'}, ensure_ascii=False), encoding='utf-8')
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


class SyntheticGeneratorTest(unittest.TestCase):
    """El generador de la demo produce un payload cargable y con banner."""

    def test_generator_produces_loadable_demo_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = generate_synthetic(Path(directory), seed=7, companies=12, months_count=4)
            payload = build_payload(source / ASSESSMENTS_NAME, source / SUMMARY_NAME)
            self.assertTrue(payload['es_demo_sintetica'])
            self.assertEqual(payload['k'], 4178.45)
            self.assertEqual(len(payload['months']), 4)
            page = render_chart(payload)
            self.assertIn('<title>DEMO SINTETICA', page)
            self.assertIn('demo-sintetica-banner', page)

    def test_generator_full_run_shape(self):
        # Tamano completo declarado, pero en temporal: valida proporciones y esquema.
        with tempfile.TemporaryDirectory() as directory:
            source = generate_synthetic(Path(directory))
            summary = json.loads((source / SUMMARY_NAME).read_text(encoding='utf-8'))
            self.assertEqual(summary['es_demo_sintetica'], True)
            self.assertEqual(summary['semilla'], SYNTHETIC_SEED)
            self.assertEqual(summary['resumen']['filas'], SYNTHETIC_COMPANIES * SYNTHETIC_MONTHS)
            self.assertLessEqual(summary['resumen']['sin_nota_pct'], 4.0)
            self.assertGreater(summary['resumen']['sin_nota_pct'], 0.5)
            self.assertTrue(50 <= summary['resumen']['mora_ratio_nulo_pct'] <= 65)
            self.assertTrue(58 <= summary['resumen']['confidence']['alta'] <= 70)
            self.assertTrue(4 <= summary['resumen']['confidence']['ninguna'] <= 7)
            self.assertTrue(20 <= summary['resumen']['volumen_ambiguo_pct_positivo_pct'] <= 30)


if __name__ == '__main__':
    unittest.main()
