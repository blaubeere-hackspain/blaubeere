"""Tests de xray/calibrate_params.py con fixtures sinteticas.

Cubre: anulacion de colchon con alpha=0, anulacion de penalizacion con
beta=0, saturacion del colchon, colchon None/negativo, mora None vs 0,
monotonia, coherencia con xray/scoring_v3.py, invariancia a escala
monetaria, abortos del CLI y determinismo del barrido.

Prohibido por disciplina anti-label: ninguna fixture ni test toca
marts/targets_proxy.parquet ni reports/label_review/.
"""

import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb

from xray import calibrate_params as cp
from xray.scoring_v3 import score_company

TOLERANCE = 1e-9  # tolerancia numerica declarada del test de coherencia


# ---------------------------------------------------------------- fixtures

def month_series(n, start_year=2024, start_month=1):
    """Los primeros n meses como primer dia de mes."""
    months = []
    index = start_year * 12 + (start_month - 1)
    for offset in range(n):
        value = index + offset
        months.append(date(value // 12, value % 12 + 1, 1))
    return months


def synthetic_monthly_rows():
    """Filas mensuales sinteticas para el test de coherencia con el motor.

    7 meses de una empresa con actividad, un cobro None (desconocido), un
    colchon estimado y una mora observable: cubre los caminos del motor.
    """
    months = month_series(7)
    c = [800.0, 900.0, None, 1100.0, 1000.0, 1200.0, 1300.0]
    p = [700.0, 650.0, 600.0, 620.0, 640.0, 660.0, 680.0]
    d = [50.0, 55.0, 60.0, None, 70.0, 75.0, 80.0]
    colchon = [1000.0, 1200.0, 1400.0, 1600.0, 1800.0, 2000.0, 2200.0]
    mora = [0.0, 0.1, None, 0.3, 0.0, 0.2, 0.4]
    return [{'month': month, 'c_eur': c[i], 'p_eur': p[i], 'd_eur': d[i],
             'tiene_actividad': True, 'colchon_eur': colchon[i],
             'salida_media_mensual_eur': None, 'mora_ratio': mora[i],
             'confianza_entradas': 'alta'}
            for i, month in enumerate(months)]


ASSESSMENT_COLUMNS = (
    'company_id', 'group_id', 'month', 'health_score', 'confidence',
    'n_meses_ventana', 'ventana_parcial', 'c6', 't6', 'p6', 'd6',
    'r_hist', 'colchon_bruto', 'colchon_aplicable', 'mora_ratio',
    'volumen_ambiguo_pct', 'k', 'alpha', 'beta')


def assessment_row(company_id, month, c6, t6, r_hist, colchon_bruto,
                   mora_ratio, k=cp.CURRENT_K,
                   alpha=cp.CURRENT_ALPHA, beta=cp.CURRENT_BETA,
                   ventana_parcial=False, health_score=None):
    colchon = cp.colchon_aplicable_value(colchon_bruto, t6, alpha)
    if health_score is None:
        # las fixtures se construyen con la propia formula del modulo:
        # el parquet sintetico es coherente por construccion
        health_score = cp.health_final(c6, t6, r_hist, colchon_bruto,
                                       mora_ratio, k, alpha, beta)
    return {
        'company_id': company_id, 'group_id': 'g1', 'month': month,
        'health_score': health_score, 'confidence': 'alta',
        'n_meses_ventana': 6, 'ventana_parcial': ventana_parcial,
        'c6': c6, 't6': t6, 'p6': t6 / 2.0, 'd6': t6 / 2.0,
        'r_hist': r_hist, 'colchon_bruto': colchon_bruto,
        'colchon_aplicable': colchon, 'mora_ratio': mora_ratio,
        'volumen_ambiguo_pct': 0.0, 'k': k, 'alpha': alpha, 'beta': beta,
    }


def write_assessments(path, rows):
    """Escribe un parquet con el esquema completo de assessments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    type_of = {'company_id': 'VARCHAR', 'group_id': 'VARCHAR',
               'month': 'DATE', 'confidence': 'VARCHAR',
               'n_meses_ventana': 'INTEGER', 'ventana_parcial': 'BOOLEAN'}
    columns = ', '.join(f'{name} {type_of.get(name, "DOUBLE")}'
                        for name in ASSESSMENT_COLUMNS)
    con = duckdb.connect()
    try:
        con.execute(f'CREATE TABLE assess ({columns})')
        placeholders = ', '.join('?' * len(ASSESSMENT_COLUMNS))
        con.executemany(
            f'INSERT INTO assess VALUES ({placeholders})',
            [tuple(row[name] for name in ASSESSMENT_COLUMNS) for row in rows])
        con.execute(f"COPY assess TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


def synthetic_panel_rows(n_companies=4, n_months=10):
    """Panel sintetico para el barrido completo (determinista).

    - Empresa 0: flujo estable, colchon pequeno y mora 0.
    - Empresa 1: flujo creciente, colchon grande (satura) y mora que salta
      de 0 a 1 (para el aviso M3).
    - Empresa 2: volumen 10x (invariancia de escala entre empresas).
    - Empresa 3: colchon None (sin colchon estimado).
    """
    rows = []
    months = month_series(n_months)
    for index, month in enumerate(months):
        rows.append(assessment_row(
            'comp0', month,
            c6=1000.0 + 10.0 * index, t6=800.0 + 5.0 * index,
            r_hist=0.6, colchon_bruto=200.0, mora_ratio=0.0))
        rows.append(assessment_row(
            'comp1', month,
            c6=600.0 + 30.0 * index, t6=900.0,
            r_hist=0.45, colchon_bruto=5000.0,
            mora_ratio=0.0 if index % 2 == 0 else 1.0))
        rows.append(assessment_row(
            'comp2', month,
            c6=1_000_000.0 + 1000.0 * index, t6=800_000.0,
            r_hist=0.55, colchon_bruto=100_000.0, mora_ratio=None))
        rows.append(assessment_row(
            'comp3', month,
            c6=1500.0 + 20.0 * index, t6=1000.0,
            r_hist=0.55, colchon_bruto=None,
            mora_ratio=0.2 if index % 2 else 0.0))
    return rows


# ------------------------------------------------------------------- tests

class TestFormula(unittest.TestCase):
    """Tests 1-6 y 8: formula pura recalculada desde las columnas."""

    def test_alpha_cero_anula_el_colchon(self):
        # alpha=0: colchon_aplicable = min(colchon, 0*T6) = 0 siempre.
        base = cp.health_final(1000.0, 800.0, 0.6, None, 0.1, 4178.45, 3.0, 0.25)
        self.assertEqual(cp.colchon_aplicable_value(5000.0, 800.0, 0.0), 0.0)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 5000.0, 0.1, 4178.45, 0.0, 0.25),
            cp.health_final(1000.0, 800.0, 0.6, None, 0.1, 4178.45, 0.0, 0.25))
        self.assertNotEqual(
            cp.health_final(1000.0, 800.0, 0.6, 5000.0, 0.1, 4178.45, 3.0, 0.25),
            base)

    def test_beta_cero_anula_la_penalizacion(self):
        base = cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.5, 4178.45, 3.0, 0.0)
        self.assertEqual(base,
                         cp.health_final(1000.0, 800.0, 0.6, 300.0, None,
                                         4178.45, 3.0, 0.0))
        # cualquier beta con mora 0 tampoco penaliza
        self.assertEqual(base,
                         cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.0,
                                         4178.45, 3.0, 0.9))
        self.assertNotEqual(base,
                            cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.5,
                                            4178.45, 3.0, 0.25))

    def test_colchon_saturado_por_alpha_T6(self):
        # colchon bruto enorme queda acotado a alpha * T6
        capped = cp.colchon_aplicable_value(1e9, 800.0, 2.0)
        self.assertEqual(capped, 1600.0)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 1e9, None, 4178.45, 2.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, 1600.0, None, 4178.45, 2.0, 0.0))
        # una vez saturado por el mismo T6, un alpha mayor con colchon
        # tambien saturado no cambia el tope efectivo: min(colchon, alpha*T6)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 1e9, None, 4178.45, 2.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, 1600.0, None, 4178.45,
                            50.0, 0.0))

    def test_colchon_none_o_negativo_contribuye_cero(self):
        without = cp.health_final(1000.0, 800.0, 0.6, None, None,
                                  4178.45, 3.0, 0.0)
        self.assertEqual(cp.colchon_aplicable_value(None, 800.0, 3.0), 0.0)
        self.assertEqual(cp.colchon_aplicable_value(-400.0, 800.0, 3.0), 0.0)
        self.assertEqual(without,
                         cp.health_final(1000.0, 800.0, 0.6, -400.0, None,
                                         4178.45, 3.0, 0.0))

    def test_mora_none_sin_penalizacion_distinta_de_cero(self):
        base = cp.health_final(1000.0, 800.0, 0.6, 300.0, None,
                               4178.45, 3.0, 0.25)
        # misma nota numerica con mora None y mora 0...
        self.assertEqual(base,
                         cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.0,
                                         4178.45, 3.0, 0.25))
        # ...pero el MOTIVO es distinto: None = sin cartera observable
        self.assertEqual(cp.mora_status(None), 'sin_mora_observable')
        self.assertEqual(cp.mora_status(0.0), 'mora_cero')
        # y mora positiva SI penaliza con beta > 0
        self.assertLess(cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.5,
                                        4178.45, 3.0, 0.25), base)

    def test_monotonia_en_alpha_y_beta(self):
        c6, t6, r, colchon, mora = 1000.0, 800.0, 0.6, 500.0, 0.4
        for base in (cp.health_final(c6, t6, r, colchon, mora, 4178.45,
                                     alpha, beta)
                     for alpha in (0.0, 1.0, 3.0, 10.0)
                     for beta in (0.0, 0.25, 0.5, 1.0)):
            self.assertIsNotNone(base)
        for alpha in (0.0, 0.5, 1.0, 2.0, 5.0, 20.0):
            h_prev = cp.health_final(c6, t6, r, colchon, mora, 4178.45,
                                     alpha, 0.25)
            h_next = cp.health_final(c6, t6, r, colchon, mora, 4178.45,
                                     alpha + 0.5, 0.25)
            # subir alpha no baja H (el colchon aplicable no disminuye)
            self.assertGreaterEqual(h_next, h_prev - TOLERANCE)
        for beta in (0.0, 0.1, 0.25, 0.5, 0.9):
            h_prev = cp.health_final(c6, t6, r, colchon, mora, 4178.45, 3.0,
                                     beta)
            h_next = cp.health_final(c6, t6, r, colchon, mora, 4178.45, 3.0,
                                     beta + 0.1)
            # subir beta no sube H_final
            self.assertLessEqual(h_next, h_prev + TOLERANCE)

    def test_coherencia_con_el_motor(self):
        monthly = synthetic_monthly_rows()
        for k, alpha, beta in ((0.0, 3.0, 0.25), (4178.45, 0.0, 0.25),
                               (4178.45, 3.0, 0.25), (1e5, 2.0, 0.6),
                               (4178.45, 3.0, 1.0), (500.0, 1.5, 0.05)):
            result = score_company('emp', 'g1', monthly,
                                   date(2024, 7, 31),
                                   k=k, alpha=alpha, beta=beta)
            inputs = result['inputs']
            recomputed = cp.health_final(
                inputs['c6'], inputs['t6'], inputs['r_hist'],
                inputs['colchon_bruto'], inputs['mora_ratio'],
                k, alpha, beta)
            self.assertAlmostEqual(result['health_score'], recomputed,
                                   delta=TOLERANCE,
                                   msg=f'k={k} alpha={alpha} beta={beta}')
            # la formula antes de mora tambien coincide
            self.assertAlmostEqual(
                result['adjustments']['h_antes_de_mora'],
                cp.health(inputs['c6'], inputs['t6'], inputs['r_hist'],
                          inputs['colchon_bruto'], k, alpha),
                delta=TOLERANCE)

    def test_invariancia_a_escala_monetaria(self):
        # escalar TODAS las magnitudes monetarias (c6, t6, colchon, k) no
        # cambia H_final: la formula es homogenea en euros
        base = cp.health_final(1000.0, 800.0, 0.6, 500.0, 0.3, 4178.45,
                               3.0, 0.25)
        for scale in (1e6, 1e-2, 1234.5):
            scaled = cp.health_final(1000.0 * scale, 800.0 * scale, 0.6,
                                     500.0 * scale, 0.3, 4178.45 * scale,
                                     3.0, 0.25)
            self.assertAlmostEqual(scaled, base,
                                   delta=TOLERANCE * max(1.0, abs(base)))


class TestCLIBehaviour(unittest.TestCase):
    """Tests 9 y 10: abortos del CLI y determinismo del barrido."""

    def test_run_falla_con_mensaje_claro_si_falta_el_parquet(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            cp.run('no_debe_existir/out', 'no_existe/assessments.parquet')
        self.assertIn('falta', str(ctx.exception))
        self.assertIn('xray.scoring_io_v3', str(ctx.exception))

    def test_cli_falla_con_mensaje_claro_si_falta_el_parquet(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cp.main(['--output', 'no_debe_existir/inexistente',
                            '--input', 'no_existe/assessments.parquet'])
        self.assertEqual(code, 1)
        self.assertIn('falta reports/score_v3/assessments.parquet',
                      stderr.getvalue())

    def test_cli_aborta_si_la_ruta_de_salida_existe(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx, \
                    contextlib.redirect_stderr(stderr):
                cp.main(['--output', tmp])
            self.assertNotEqual(ctx.exception.code, 0)
            self.assertIn('ya existe', stderr.getvalue())

    def test_determinismo_del_barrido(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = tmp / 'input' / 'assessments.parquet'
            write_assessments(path, synthetic_panel_rows())
            first = cp.run(tmp / 'first', path)
            second = cp.run(tmp / 'second', path)
            self.assertEqual((tmp / 'first' / 'sweep.csv').read_bytes(),
                             (tmp / 'second' / 'sweep.csv').read_bytes())
            self.assertEqual((tmp / 'first' / 'report.md').read_bytes(),
                             (tmp / 'second' / 'report.md').read_bytes())
            self.assertEqual(first['recommended'], second['recommended'])


class TestSweepOnFixture(unittest.TestCase):
    """Comprobaciones de contenido del barrido sobre la fixture sintetica."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.path = root / 'assessments.parquet'
        write_assessments(cls.path, synthetic_panel_rows())
        cls.summary = cp.run(root / 'out', cls.path)
        cls.panel, cls.meta = cp.load_assessments(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_resumen_declarativo(self):
        summary = self.summary
        self.assertFalse(summary['labels_used'])
        self.assertIn('NO estan ajustados contra ninguna etiqueta',
                      summary['declaration'])
        self.assertIn('por_ejes', summary['sweep'])

    def test_recomendacion_coherente_con_su_eje(self):
        recommended = self.summary['recommended']
        if recommended['verdict'] in ('ok', 'parcial'):
            self.assertIsNotNone(recommended['k'])
            joint = self.summary['recommended_metrics']
            self.assertIsNotNone(joint['dH_p75'])

    def test_coherencia_maxima_del_parquet_sintetico(self):
        # las filas de la fixture se construyen con la misma formula:
        # health_score None y recalculo deben cuadrar (sin discrepancias)
        self.assertLessEqual(self.meta['coherence_max_abs_diff'], TOLERANCE)


if __name__ == '__main__':
    unittest.main()
