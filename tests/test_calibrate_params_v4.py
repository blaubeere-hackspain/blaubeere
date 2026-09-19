"""Tests de xray/calibrate_params_v4.py con fixtures sinteticas.

Cubre: formula recalculada (alpha=0 anula el colchon, beta=0 anula la
penalizacion por mora, saturacion min(colchon, alpha*T6), mora None vs 0,
monotonia), coherencia con el motor v4 (xray/scoring_v4.py), las TRES
variantes del triple conteo, la POBLACION COMUN declarada de los |dH| (pares
evaluables con todas las configs; exclusion de ventana parcial y primeros
meses), COBERTURA invariante a k, disciplina anti-label (el modulo NO abre
marts/targets_proxy.parquet ni reports/label_review/), abortos del CLI y
determinismo del barrido.

Prohibido por disciplina anti-label: ninguna fixture ni test toca
marts/targets_proxy.parquet ni reports/label_review/.
"""

import builtins
import contextlib
import csv
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb

from xray import calibrate_params_v4 as cp
from xray.scoring_v4 import score_company

TOLERANCE = 1e-9  # tolerancia numerica declarada de coherencia


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

    7 meses de una empresa: cubre obligacion vencida (stock), deficit de
    servicio, saldo_reversa (incluido un mes None y uno negativo), mora
    con None (sin cartera observable) y multiplicador de deuda.
    """
    months = month_series(7)
    c = [800.0, 900.0, None, 1100.0, 1000.0, 1200.0, 1300.0]
    p = [700.0, 650.0, 600.0, 620.0, 640.0, 660.0, 680.0]
    d = [50.0, 55.0, 60.0, None, 70.0, 75.0, 80.0]
    saldo = [1000.0, 1200.0, None, 1600.0, -200.0, 2000.0, 2200.0]
    deficit = [0.0, 10.0, 0.0, 0.0, 30.0, 0.0, 0.0]
    obligacion = [0.0, 0.0, 500.0, 500.0, 800.0, 800.0, 0.0]
    multiplicador = [1.0, 1.0, 0.9, 0.9, 0.5, 0.5, 1.0]
    mora = [0.0, 0.1, None, 0.3, 0.0, 0.2, 0.4]
    return [{'month': month, 'c_eur': c[i], 'p_eur': p[i], 'd_eur': d[i],
             'tiene_actividad': True, 'saldo_reversa_eur': saldo[i],
             'deficit_servicio_eur': deficit[i],
             'obligacion_vencida_eur': obligacion[i],
             'multiplicador_deuda': multiplicador[i],
             'mora_indice': mora[i], 'confianza_entradas': 'alta'}
            for i, month in enumerate(months)]


ASSESSMENT_COLUMNS = ('company_id', 'group_id', 'month', 'health_score',
                      'ventana_parcial', 'excluida', 'c6', 't6_efectivo',
                      'r_hist', 'colchon_v4', 'mora_indice',
                      'multiplicador_deuda', 'obligacion_vencida_m',
                      'k', 'alpha', 'beta')


def assessment_row(company_id, month, c6, t6, r_hist, colchon_v4, mora,
                   multiplicador=1.0, obligacion_m=0.0,
                   k=cp.CURRENT_K, alpha=cp.CURRENT_ALPHA,
                   beta=cp.CURRENT_BETA, ventana_parcial=False,
                   excluida=False, health_score=None):
    if health_score is None:
        # la fixture se construye con la propia formula del modulo:
        # el parquet sintetico es coherente por construccion
        health_score = cp.health_final(c6, t6, r_hist, colchon_v4, mora,
                                       multiplicador, obligacion_m,
                                       k, alpha, beta)
    return {
        'company_id': company_id, 'group_id': 'g1', 'month': month,
        'health_score': health_score, 'ventana_parcial': ventana_parcial,
        'excluida': excluida, 'c6': c6, 't6_efectivo': t6, 'r_hist': r_hist,
        'colchon_v4': colchon_v4, 'mora_indice': mora,
        'multiplicador_deuda': multiplicador,
        'obligacion_vencida_m': obligacion_m,
        'k': k, 'alpha': alpha, 'beta': beta,
    }


def write_assessments(path, rows):
    """Escribe un parquet con las columnas que el modulo necesita."""
    path.parent.mkdir(parents=True, exist_ok=True)
    type_of = {'company_id': 'VARCHAR', 'group_id': 'VARCHAR',
               'month': 'DATE', 'ventana_parcial': 'BOOLEAN',
               'excluida': 'BOOLEAN'}
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


def synthetic_panel_rows(n_months=10):
    """Panel sintetico determinista para el barrido completo.

    - comp0: flujo estable, colchon pequeno, mora 0, sin obligacion.
    - comp1: colchon grande (satura con alpha ~ 1), mora que salta 0/1
      (montana rusa por el canal mora) y obligacion vencida > 0.
    - comp2: volumen 10x (invariancia de escala), mora y mult None.
    - comp3: colchon None, mora baja, sin obligacion.
    Los dos primeros meses de cada empresa llevan ventana_parcial = true
    (ejercitan la exclusion de C1).
    """
    rows = []
    months = month_series(n_months)
    for index, month in enumerate(months):
        parcial = index < 2
        rows.append(assessment_row(
            'comp0', month, c6=1000.0 + 10.0 * index,
            t6=800.0 + 5.0 * index, r_hist=0.6, colchon_v4=200.0,
            mora=0.0, ventana_parcial=parcial))
        rows.append(assessment_row(
            'comp1', month, c6=600.0 + 30.0 * index, t6=900.0,
            r_hist=0.45, colchon_v4=5000.0,
            mora=0.0 if index % 2 == 0 else 1.0,
            multiplicador=0.8, obligacion_m=1200.0,
            ventana_parcial=parcial))
        rows.append(assessment_row(
            'comp2', month, c6=1_000_000.0 + 1000.0 * index,
            t6=800_000.0, r_hist=0.55, colchon_v4=100_000.0, mora=None,
            multiplicador=None, obligacion_m=None,
            ventana_parcial=parcial))
        rows.append(assessment_row(
            'comp3', month, c6=1500.0 + 20.0 * index, t6=1000.0,
            r_hist=0.55, colchon_v4=None,
            mora=0.2 if index % 2 else 0.0, ventana_parcial=parcial))
    return rows


def panel_with_gap(n_months=10):
    """Panel con una empresa que pierde la nota en un mes intermedio.

    Los pares que cruzan el hueco NO son meses consecutivos con nota: no
    deben entrar en la poblacion comun de |dH|.
    """
    rows = []
    months = month_series(n_months)
    for index, month in enumerate(months):
        parcial = index < 2
        row = assessment_row(
            'comp4', month, c6=900.0 + index, t6=700.0 + index, r_hist=0.5,
            colchon_v4=100.0, mora=0.0, ventana_parcial=parcial)
        if index == 5:
            # mes sin nota en el parquet (no evaluable): rompe la adjacencia
            row['health_score'] = None
        rows.append(row)
    return rows


# ------------------------------------------------------------------- tests

class TestFormula(unittest.TestCase):
    """Formula pura recalculada desde las columnas del parquet."""

    def test_alpha_cero_anula_el_colchon(self):
        base = cp.health_final(1000.0, 800.0, 0.6, 300.0, None, None, 0.0,
                               4178.45, 3.0, 0.25)
        self.assertEqual(cp.colchon_aplicable_value(5000.0, 800.0, 0.0), 0.0)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 5000.0, None, None, 0.0,
                            4178.45, 0.0, 0.25),
            cp.health_final(1000.0, 800.0, 0.6, None, None, None, 0.0,
                            4178.45, 0.0, 0.25))
        self.assertNotEqual(
            cp.health_final(1000.0, 800.0, 0.6, 5000.0, None, None, 0.0,
                            4178.45, 3.0, 0.25), base)

    def test_beta_cero_anula_la_penalizacion_y_mora_none_es_factor_1(self):
        base = cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.5, 1.0, 0.0,
                               4178.45, 3.0, 0.0)
        self.assertEqual(base,
                         cp.health_final(1000.0, 800.0, 0.6, 300.0, None,
                                         1.0, 0.0, 4178.45, 3.0, 0.0))
        # mora 0 no penaliza con cualquier beta
        self.assertEqual(base,
                         cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.0,
                                         1.0, 0.0, 4178.45, 3.0, 0.9))
        self.assertNotEqual(
            base, cp.health_final(1000.0, 800.0, 0.6, 300.0, 0.5, 1.0, 0.0,
                                  4178.45, 3.0, 0.25))

    def test_colchon_saturado_por_alpha_T6(self):
        self.assertEqual(cp.colchon_aplicable_value(1e9, 800.0, 2.0), 1600.0)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 1e9, None, None, 0.0,
                            4178.45, 2.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, 1600.0, None, None, 0.0,
                            4178.45, 2.0, 0.0))
        # con el colchon ya saturado por el mismo T6 (tope 1600), un alpha
        # mayor no cambia el tope efectivo min(colchon, alpha*T6)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, 1600.0, None, None, 0.0,
                            4178.45, 2.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, 1600.0, None, None, 0.0,
                            4178.45, 50.0, 0.0))
        # pero con colchon bruto enorme el tope SI crece con alpha
        self.assertNotEqual(
            cp.health_final(1000.0, 800.0, 0.6, 1e9, None, None, 0.0,
                            4178.45, 2.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, 1e9, None, None, 0.0,
                            4178.45, 50.0, 0.0))

    def test_colchon_none_o_negativo_contribuye_cero(self):
        self.assertEqual(cp.colchon_aplicable_value(None, 800.0, 3.0), 0.0)
        self.assertEqual(cp.colchon_aplicable_value(-400.0, 800.0, 3.0), 0.0)
        self.assertEqual(
            cp.health_final(1000.0, 800.0, 0.6, None, None, None, 0.0,
                            4178.45, 3.0, 0.0),
            cp.health_final(1000.0, 800.0, 0.6, -400.0, None, None, 0.0,
                            4178.45, 3.0, 0.0))

    def test_r_hist_none_omite_el_termino_k_completo(self):
        con_historia = cp.health_final(1000.0, 800.0, None, 300.0, None,
                                       None, 0.0, 1e9, 3.0, 0.25)
        # con r_hist None, k=1e9 equivale a k=0 (termino omitido completo)
        self.assertEqual(con_historia,
                         cp.health_final(1000.0, 800.0, None, 300.0, None,
                                         None, 0.0, 0.0, 3.0, 0.25))
        # y difiere del mismo caso con r_hist definida
        self.assertNotEqual(
            con_historia,
            cp.health_final(1000.0, 800.0, 0.6, 300.0, None, None, 0.0,
                            1e9, 3.0, 0.25))

    def test_monotonia_en_alpha_y_beta(self):
        c6, t6, r, colchon, mora, mult = 1000.0, 800.0, 0.6, 500.0, 0.4, 1.0
        for alpha in (0.0, 0.5, 1.0, 2.0, 5.0, 20.0):
            h_prev = cp.health_final(c6, t6, r, colchon, mora, mult, 0.0,
                                     4178.45, alpha, 0.25)
            h_next = cp.health_final(c6, t6, r, colchon, mora, mult, 0.0,
                                     4178.45, alpha + 0.5, 0.25)
            # subir alpha no baja H (el colchon aplicable no disminuye)
            self.assertGreaterEqual(h_next, h_prev - TOLERANCE)
        for beta in (0.0, 0.1, 0.25, 0.5, 0.9):
            h_prev = cp.health_final(c6, t6, r, colchon, mora, mult, 0.0,
                                     4178.45, 3.0, beta)
            h_next = cp.health_final(c6, t6, r, colchon, mora, mult, 0.0,
                                     4178.45, 3.0, beta + 0.1)
            # subir beta no sube H_final
            self.assertLessEqual(h_next, h_prev + TOLERANCE)

    def test_coherencia_con_el_motor_v4(self):
        monthly = synthetic_monthly_rows()
        for k, alpha, beta in ((0.0, 3.0, 0.25), (4178.45, 0.0, 0.25),
                               (4178.45, 3.0, 0.25), (1e5, 2.0, 0.6),
                               (4178.45, 3.0, 1.0), (500.0, 1.5, 0.05)):
            result = score_company('emp', 'g1', monthly,
                                   date(2024, 7, 31),
                                   k=k, alpha=alpha, beta=beta)
            inputs = result['inputs']
            recomputed = cp.health_final(
                inputs['c6'], inputs['t6_efectivo'], inputs['r_hist'],
                inputs['colchon_v4'], inputs['mora_indice'],
                inputs['multiplicador_deuda'], inputs['obligacion_vencida_m'],
                k, alpha, beta)
            self.assertIsNotNone(recomputed)
            self.assertAlmostEqual(result['health_score'], recomputed,
                                   delta=TOLERANCE,
                                   msg=f'k={k} alpha={alpha} beta={beta}')

    def test_invariancia_a_escala_monetaria(self):
        base = cp.health_final(1000.0, 800.0, 0.6, 500.0, 0.3, 1.0, 0.0,
                               4178.45, 3.0, 0.25)
        for scale in (1e6, 1e-2, 1234.5):
            scaled = cp.health_final(1000.0 * scale, 800.0 * scale, 0.6,
                                     500.0 * scale, 0.3, 1.0, 0.0,
                                     4178.45 * scale, 3.0, 0.25)
            self.assertAlmostEqual(scaled, base,
                                   delta=TOLERANCE * max(1.0, abs(base)))


class TestVariantesTripleConteo(unittest.TestCase):
    """D: las tres variantes del canal mora se comportan como se declaran."""

    ROW = dict(c6=1000.0, t6=800.0, r_hist=0.6, colchon_v4=300.0, mora=0.5,
               multiplicador=0.8, k=4178.45, alpha=3.0, beta=0.25)

    def hf(self, variant, obligacion_m):
        row = self.ROW
        return cp.health_final(row['c6'], row['t6'], row['r_hist'],
                               row['colchon_v4'], row['mora'],
                               row['multiplicador'], obligacion_m,
                               row['k'], row['alpha'], row['beta'], variant)

    def test_variante_a_igual_al_default(self):
        self.assertEqual(self.hf('a', 1200.0), self.hf('a', 0.0))

    def test_variante_b_atenua_solo_con_obligacion(self):
        con = self.hf('b', 1200.0)
        sin = self.hf('b', 0.0)
        atenuada = cp.health_final(self.ROW['c6'], self.ROW['t6'],
                                   self.ROW['r_hist'], self.ROW['colchon_v4'],
                                   self.ROW['mora'], self.ROW['multiplicador'],
                                   1200.0, self.ROW['k'], self.ROW['alpha'],
                                   self.ROW['beta'] * cp.ATENUACION_VARIANTE_B)
        self.assertEqual(con, atenuada)
        self.assertEqual(sin, self.hf('a', 0.0))
        self.assertGreater(con, self.hf('a', 1200.0))
        # atenuar el castigo SUBE la nota: (b) con obligacion es mas alta
        # que (a) con obligacion, y mas baja que (b) sin obligacion
        self.assertGreater(con, sin)

    def test_variante_c_solo_castiga_sin_obligacion(self):
        con = self.hf('c', 1200.0)
        # con obligacion > 0, (c) no castiga por mora: factor 1, como si
        # no hubiera mora observable
        sin_castigo_mora = cp.health_final(
            self.ROW['c6'], self.ROW['t6'], self.ROW['r_hist'],
            self.ROW['colchon_v4'], None, self.ROW['multiplicador'], 1200.0,
            self.ROW['k'], self.ROW['alpha'], self.ROW['beta'])
        self.assertEqual(con, sin_castigo_mora)
        # sin obligacion, (c) castiga igual que (a)
        self.assertEqual(self.hf('c', 0.0), self.hf('a', 0.0))
        self.assertGreater(con, self.hf('c', 0.0))  # castigo completo sin ella

    def test_mora_none_factor_1_en_todas_las_variantes(self):
        for variant in ('a', 'b', 'c'):
            self.assertEqual(cp.factor_mora(None, 1200.0, 0.25, variant), 1.0)

    def test_variante_desconocida_aborta(self):
        with self.assertRaises(ValueError):
            cp.factor_mora(0.5, 0.0, 0.25, 'z')


class TestPoblacionComun(unittest.TestCase):
    """C1: los |dH| se calculan sobre la poblacion comun declarada."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.path = root / 'assessments.parquet'
        write_assessments(cls.path, synthetic_panel_rows(n_months=10)
                          + panel_with_gap(n_months=10))
        cls.panel, cls.meta = cp.load_assessments(cls.path)
        cls.pairs, _ = cp.candidate_pairs(cls.panel)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_nota_nula_rompe_la_adjacencia_del_par(self):
        # comp4: sin nota en junio (mes 5); el puente 4->6 no es un par
        # consecutivo con nota y NO entra en la poblacion comun
        company_months = {month for company, month in self.pairs
                          if company == 'comp4'}
        scored = sorted(row['month'] for row in self.panel['comp4'])
        self.assertNotIn(date(2024, 6, 1), scored)      # mes sin nota
        self.assertNotIn(date(2024, 6, 1), company_months)
        self.assertNotIn(date(2024, 7, 1), company_months)
        # los pares alrededor del hueco SI existen (abr->may y jul->ago)
        self.assertIn(date(2024, 5, 1), company_months)
        self.assertIn(date(2024, 8, 1), company_months)

    def test_exclusion_de_primeros_meses_y_ventana_parcial(self):
        # los dos primeros meses de cada empresa (ventana_parcial=True en
        # la fixture) no generan pares candidatos
        comp0 = sorted(month for company, month in self.pairs
                       if company == 'comp0')
        months = month_series(10)
        # par (0->1) y (1->2) excluidos por indice <= 2 y ventana parcial
        self.assertNotIn(months[1], comp0)
        self.assertNotIn(months[2], comp0)
        # el par (2->3) SI entra
        self.assertIn(months[3], comp0)

    def test_estabilidad_solo_cuenta_pares_de_la_poblacion_comun(self):
        scores = cp.evaluate(self.panel, 4178.45, 3.0, 0.25)
        full = cp.stability(self.panel, scores, set(self.pairs))
        self.assertEqual(full['n_pairs'], len(self.pairs))
        # quitar un par del conjunto declarado lo saca de la medicion
        reduced = set(self.pairs) - {next(iter(self.pairs))}
        st_reduced = cp.stability(self.panel, scores, reduced)
        self.assertEqual(st_reduced['n_pairs'], len(self.pairs) - 1)

    def test_n_pairs_del_barrido_es_la_poblacion_comun_declarada(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = cp.run(Path(tmp) / 'out', self.path)
            # todos los pares del panel del eje k son evaluables con todas
            # las k comparadas: n_pairs = poblacion comun declarada
            self.assertEqual(summary['pair_counters']['candidatos'],
                             len(self.pairs))
            rows = list(csv_rows(Path(summary['output']) / 'sweep.csv'))
        k_rows = [row for row in rows if row['axis'] == 'k']
        self.assertTrue(k_rows)
        self.assertEqual({row['n_pairs'] for row in k_rows},
                         {str(len(self.pairs))})


def csv_rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        yield from csv.DictReader(stream)


class TestCoberturaInvariante(unittest.TestCase):
    """C4: la cobertura debe ser invariante a k (y no variar con alpha)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.path = root / 'assessments.parquet'
        write_assessments(cls.path, synthetic_panel_rows())
        cls.panel, cls.meta = cp.load_assessments(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_evaluabilidad_no_depende_de_k_ni_de_beta(self):
        base = cp.evaluate(self.panel, 0.0, 3.0, 0.25)
        evaluable_base = {key for key, hf in base.items() if hf is not None}
        for k in (0.0, 13.7, 4178.45, 1e6, 1.375e9):
            scores = cp.evaluate(self.panel, k, 3.0, 0.25)
            evaluable = {key for key, hf in scores.items() if hf is not None}
            self.assertEqual(evaluable, evaluable_base, f'k={k}')
        for beta in (0.0, 0.25, 1.0):
            scores = cp.evaluate(self.panel, 4178.45, 3.0, beta)
            evaluable = {key for key, hf in scores.items() if hf is not None}
            self.assertEqual(evaluable, evaluable_base, f'beta={beta}')

    def test_run_declara_cobertura_invariante_en_el_eje_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = cp.run(Path(tmp) / 'out', self.path)
            # la fila del CSV del eje k declara coverage_invariante = true
            for row in csv_rows(Path(summary['output']) / 'sweep.csv'):
                if row['axis'] == 'k':
                    self.assertEqual(row['coverage_invariante'], 'True')
                if row['axis'] == 'referencia_000':
                    self.assertGreater(int(row['n_scored_rows']), 0)


class TestNoLabels(unittest.TestCase):
    """Disciplina anti-label: el modulo NO abre targets_proxy ni
    label_review, y su entrada declarada es solo el parquet de score_v4."""

    FORBIDDEN = ('targets_proxy', 'label_review')

    def test_entrada_declarada_es_el_parquet_de_score_v4(self):
        self.assertTrue(str(cp.DEFAULT_INPUT).endswith(
            'reports/score_v4/assessments.parquet'))
        self.assertNotIn('label_review', str(cp.DEFAULT_INPUT))
        self.assertNotIn('targets_proxy', str(cp.DEFAULT_INPUT))

    def test_run_no_abre_targets_proxy_ni_label_review(self):
        opened = []
        real_open = builtins.open

        def monitored_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        queries = []
        real_connect = cp.duckdb.connect

        class MonitoredConn:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, query, *args, **kwargs):
                queries.append(query)
                return self._conn.execute(query, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        def monitored_connect(*args, **kwargs):
            return MonitoredConn(real_connect(*args, **kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'assessments.parquet'
            write_assessments(path, synthetic_panel_rows())
            builtins_open = builtins.open
            duckdb_connect = cp.duckdb.connect
            builtins.open = monitored_open
            cp.duckdb.connect = monitored_connect
            try:
                summary = cp.run(root / 'out', path)
            finally:
                builtins.open = builtins_open
                cp.duckdb.connect = duckdb_connect
        self.assertFalse(summary['labels_used'])
        for route in opened:
            for forbidden in self.FORBIDDEN:
                self.assertNotIn(forbidden, route)
        for query in queries:
            for forbidden in self.FORBIDDEN:
                self.assertNotIn(forbidden, query.lower())


class TestCLIBehaviour(unittest.TestCase):
    """Abortos del CLI y determinismo del barrido."""

    def test_run_falla_con_mensaje_claro_si_falta_el_parquet(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            cp.run('no_debe_existir/out', 'no_existe/assessments.parquet')
        self.assertIn('falta', str(ctx.exception))
        self.assertIn('xray.scoring_io_v4', str(ctx.exception))

    def test_cli_falla_con_mensaje_claro_si_falta_el_parquet(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cp.main(['--output', 'no_debe_existir/inexistente',
                            '--input', 'no_existe/assessments.parquet'])
        self.assertEqual(code, 1)
        self.assertIn('falta reports/score_v4/assessments.parquet',
                      stderr.getvalue())

    def test_cli_aborta_si_la_ruta_de_salida_existe(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx, \
                    contextlib.redirect_stderr(stderr):
                cp.main(['--output', tmp])
            self.assertNotEqual(ctx.exception.code, 0)
            self.assertIn('ya existe', stderr.getvalue())

    def test_end_to_end_escribe_los_dos_ficheros(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input' / 'assessments.parquet'
            write_assessments(path, synthetic_panel_rows())
            summary = cp.run(Path(tmp) / 'out', path)
            out = Path(summary['output'])
            self.assertTrue((out / 'sweep.csv').exists())
            self.assertTrue((out / 'report.md').exists())
            report = (out / 'report.md').read_text(encoding='utf-8')
            self.assertIn('DECLARACION ANTI-LABEL', report)
            self.assertIn('POBLACION COMUN', report)
            self.assertIn('triple conteo', report)

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
        self.assertIn('POST-EXCLUSION', summary['poblacion'])
        self.assertIn('por_ejes', summary['sweep'])
        self.assertEqual(summary['stability_target_p75'],
                         cp.P75_STABILITY_TARGET)

    def test_coherencia_maxima_del_parquet_sintetico(self):
        # la fixture se construye con la misma formula: sin discrepancias
        self.assertLessEqual(self.meta['coherence_max_abs_diff'], TOLERANCE)

    def test_recomendacion_declarativa(self):
        recommended = self.summary['recommended']
        self.assertIn(recommended['verdict'], ('ok', 'parcial', 'sin_terna'))
        if recommended['verdict'] in ('ok', 'parcial'):
            self.assertIsNotNone(recommended['k'])
            joint = self.summary['recommended_metrics']
            self.assertIsNotNone(joint['dH_p75'])

    def test_variantes_del_triple_conteo_en_el_resumen(self):
        variantes = self.summary['variantes_triple_conteo']
        self.assertIsNotNone(variantes)
        self.assertEqual(set(variantes), {'a', 'b', 'c'})

    def test_reactividad_k_usa_escalones_sinteticos(self):
        k_rows = [row for row in csv_rows(
            Path(self.summary['output']) / 'sweep.csv')
            if row['axis'] == 'k']
        reactivities = {row['reactivity_months'] for row in k_rows}
        self.assertTrue(all(float(value) >= 0 for value in reactivities))


if __name__ == '__main__':
    unittest.main()
