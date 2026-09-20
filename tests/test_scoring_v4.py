"""Tests del motor puro healthscore_v4 (xray/scoring_v4.py).

Sin IO: todo son fixtures en memoria. Incluye los tests obligatorios del
criterio de hecho: el stock de obligacion vencida NO se suma sobre la
ventana, la guarda P6 con y sin obligacion, D6 = 0 legitimo, H_final en
[0, 100] y mora/multiplicador NULL sin ajuste.
"""

import copy
import json
import unittest
from datetime import date

from xray.scoring_v4 import CONFIDENCE_LEVELS, score_company


def month(day):
    return date(2024, day, 1)


def row(mes, c=1000.0, p=400.0, d=100.0, actividad=True, saldo=500.0,
        deficit=None, oblig=None, mult=None, mora=None, confianza='alta'):
    return {
        'month': month(mes),
        'c_eur': c,
        'p_eur': p,
        'd_eur': d,
        'tiene_actividad': actividad,
        'saldo_reversa_eur': saldo,
        'deficit_servicio_eur': deficit,
        'obligacion_vencida_eur': oblig,
        'multiplicador_deuda': mult,
        'mora_indice': mora,
        'confianza_entradas': confianza,
    }


def full_rows(meses=(1, 2, 3, 4, 5, 6), **kwargs):
    return [row(m, **kwargs) for m in meses]


def con_fx(rows, indice, indice_min=None, intervalo=False):
    """Copia las filas anadiendo los campos FX opcionales de la capa F3."""
    return [dict(r, indice_fx=indice, indice_fx_min=indice_min,
                 indice_es_intervalo=intervalo) for r in rows]


class ScoringV4Test(unittest.TestCase):
    def score(self, rows=None, as_of='2024-06-30', **kwargs):
        return score_company('A', 'G', full_rows() if rows is None else rows,
                             as_of, **kwargs)

    # --- Obligatorio 1: la obligacion vencida es STOCK, no flujo -----------
    def test_obligacion_no_se_suma_sobre_la_ventana(self):
        # La MISMA factura vencida aparece en los 6 cortes de la ventana:
        # el T6_efectivo debe ser identico al caso de 1 solo corte, nunca
        # seis veces mayor.
        mismos_cortes = full_rows(oblig=1000.0)
        solo_corte = [row(m, oblig=1000.0 if m == 6 else None)
                      for m in (1, 2, 3, 4, 5, 6)]
        a = self.score(mismos_cortes, k=0.0)
        b = self.score(solo_corte, k=0.0)
        self.assertEqual(a['inputs']['t6_efectivo'], b['inputs']['t6_efectivo'])
        # P6+D6+deficit = 6*(400+100) = 3000, mas el stock 1000 UNA vez.
        self.assertEqual(a['inputs']['t6_efectivo'], 4000.0)
        self.assertNotEqual(a['inputs']['t6_efectivo'], 9000.0)
        # Las notas coinciden: contar el stock seis veces seria un bug.
        self.assertEqual(a['health_score'], b['health_score'])

    def test_deficit_servicio_si_es_flujo_y_se_suma(self):
        # El deficit de servicio SI es flujo mensual: se suma sobre la
        # ventana (contraste con el stock de arriba).
        result = self.score(full_rows(deficit=50.0), k=0.0)
        self.assertEqual(result['inputs']['deficit_servicio_6'], 300.0)
        self.assertEqual(result['inputs']['t6_efectivo'], 6 * 500.0 + 300.0)

    # --- Obligatorio 2: guarda P6 = 0 con y sin obligacion -----------------
    def test_p6_cero_sin_obligacion_es_sin_nota(self):
        rows = [row(1, p=0.0), row(2, p=0.0), row(3, p=0.0)]
        result = self.score(rows, as_of='2024-03-31')
        self.assertIsNone(result['health_score'])
        self.assertIn('pagos_operativos_no_demostrados', result['reasons'])
        # Tambien con P desconocido en todos los meses (p6 = 0).
        rows_desconocido = [row(m, p=None) for m in (1, 2, 3)]
        result = self.score(rows_desconocido, as_of='2024-03-31')
        self.assertIsNone(result['health_score'])
        self.assertIn('pagos_operativos_no_demostrados', result['reasons'])

    def test_p6_cero_con_obligacion_recibe_nota_baja_no_none(self):
        # p6 = 0 pero hay obligacion vencida > 0: evidencia positiva de que
        # la empresa debe y no paga -> nota BAJA, no None.
        rows = [row(m, p=0.0, oblig=50_000.0, saldo=0.0) for m in (1, 2, 3)]
        result = self.score(rows, as_of='2024-03-31', k=0.0)
        self.assertIsNotNone(result['health_score'])
        self.assertLess(result['health_score'], 20.0)
        self.assertNotIn('pagos_operativos_no_demostrados', result['reasons'])
        # T6_efectivo = 0 + 300 (D6) + obligacion 50.000 (stock, una vez).
        self.assertEqual(result['inputs']['t6_efectivo'], 50_300.0)
        self.assertEqual(result['inputs']['obligacion_vencida_m'], 50_000.0)
        # Con mora y multiplicador conocidos, la nota cae mas y sigue baja.
        con_castigos = self.score(
            [dict(r, mora_indice=0.5, multiplicador_deuda=0.5) for r in rows],
            as_of='2024-03-31', k=0.0)
        self.assertLess(con_castigos['health_score'], result['health_score'])
        self.assertGreaterEqual(con_castigos['health_score'], 0.0)

    # --- Obligatorio 3: D6 = 0 legitimo, D desconocido no degrada ----------
    def test_d6_cero_con_p6_positivo_conserva_nota(self):
        # D6 = 0 es legitimo y NO dispara ninguna guarda: hay nota.
        sin_deuda = self.score(full_rows(d=0.0, mora=0.0))
        self.assertIsNotNone(sin_deuda['health_score'])
        self.assertGreater(sin_deuda['health_score'], 0.0)
        self.assertNotIn('pagos_operativos_no_demostrados',
                         sin_deuda['reasons'])
        # D = 0 y D desconocido dan la MISMA nota (t6_efectivo igual), pero
        # solo el None registra el motivo informativo.
        sin_dato = self.score([row(m, d=None, mora=0.0)
                               for m in (1, 2, 3, 4, 5, 6)])
        self.assertAlmostEqual(sin_deuda['health_score'], sin_dato['health_score'])
        self.assertIn('d_eur_desconocido_en_6_meses', sin_dato['reasons'])
        # La confianza no se degrada por D: con d=0 y todo lo demas
        # conocido, sin deduccion por entradas.
        self.assertEqual(sin_deuda['confidence_detail']['deducciones'], [])
        self.assertEqual(sin_deuda['confidence'], 'alta')

    def test_d_desconocido_no_degrada_confianza(self):
        # Fix P6 defecto 2: D fuera del cubo 'entradas_desconocidas';
        # reason informativo sin restar nivel.
        con_d = self.score(full_rows(mora=0.0))
        sin_d = self.score([row(m, d=None, mora=0.0) for m in (1, 2, 3, 4, 5, 6)])
        self.assertIn('d_eur_desconocido_en_6_meses', sin_d['reasons'])
        # El sumo de lo conocido es 0, pero nulo nunca es cero: el motivo
        # lo declara y C y P siguen contando.
        self.assertEqual(sin_d['inputs']['d6'], 0.0)
        self.assertNotIn('entradas_desconocidas',
                         sin_d['confidence_detail']['deducciones'])
        self.assertEqual(con_d['confidence'], sin_d['confidence'])
        # En cambio, C o P desconocidos SI deducen nivel.
        con_c_nulo = self.score([row(m, c=None, mora=0.0)
                                 for m in (1, 2, 3, 4, 5, 6)])
        self.assertIn('entradas_desconocidas',
                      con_c_nulo['confidence_detail']['deducciones'])
        self.assertLess(CONFIDENCE_LEVELS.index(con_c_nulo['confidence']),
                        CONFIDENCE_LEVELS.index(sin_d['confidence']))

    # --- Obligatorio 4: H_final siempre en [0, 100] ------------------------
    def test_h_final_siempre_en_0_100(self):
        casos = []
        for mora in (None, 0.0, 0.3, 1.0):
            for mult in (None, 0.5, 0.75, 1.0):
                for oblig in (None, 0.0, 1_000.0, 10**9):
                    for saldo in (None, -10_000.0, 0.0, 10**9):
                        casos.append(full_rows(mora=mora, mult=mult,
                                               oblig=oblig, saldo=saldo))
        casos.append([row(1, c=0.0, p=100.0, d=0.0, saldo=0.0, oblig=0.0)])
        for rows in casos:
            for k in (0.0, 4178.45, 1e9):
                for alpha in (0.0, 3.0, 100.0):
                    for beta in (0.0, 0.25, 1.0):
                        result = score_company('A', 'G', rows, '2024-06-30',
                                               k=k, alpha=alpha, beta=beta)
                        if result['health_score'] is not None:
                            self.assertGreaterEqual(result['health_score'], 0.0)
                            self.assertLessEqual(result['health_score'], 100.0)

    # --- Obligatorio 6: invariancia FX (empresa sin exposicion no-EUR) -----
    def test_fx_invariancia_sin_exposicion_bit_identico(self):
        # La empresa sin exposicion no-EUR (indice 0.0) y la de exposicion
        # DESCONOCIDA (NULL) tienen health_score BIT-IDENTICO al de la v4 sin
        # capa FX: el factor debe ser exactamente 1.0, sin aritmetica que
        # arrastre error. Si una sola empresa cambia, hay un bug.
        base = full_rows(mora=0.3, mult=0.8, oblig=1500.0, deficit=75.0)
        sin_capa = self.score(base)
        cero = self.score(con_fx(base, 0.0, indice_min=0.0))
        nulo = self.score(con_fx(base, None, None))
        self.assertEqual(cero['health_score'], sin_capa['health_score'])
        self.assertEqual(nulo['health_score'], sin_capa['health_score'])
        self.assertEqual(cero['adjustments']['penalizacion_fx_puntos'], 0.0)
        self.assertEqual(nulo['adjustments']['penalizacion_fx_puntos'], 0.0)
        # Cero conocido no mete motivo FX; NULL desconocido SI lo declara y
        # aun asi no castiga (nulo nunca es cero y nunca penaliza).
        self.assertFalse(any('indice_fx' in r for r in cero['reasons']))
        self.assertIn('indice_fx_desconocido', nulo['reasons'])
        # Sin la clave 'indice_fx' (--sin-fx) no hay ningun motivo FX.
        self.assertFalse(any('indice_fx' in r for r in sin_capa['reasons']))
        self.assertFalse(any('fx' in r for r in sin_capa['reasons']))

    def test_fx_factor_acotado_h_final_en_0_100(self):
        base = full_rows(mora=0.5, mult=0.7, oblig=10_000.0, deficit=250.0)
        for indice in (0.0, 0.25, 0.5, 1.0):
            rows = con_fx(base, indice)
            for beta_fx in (0.0, 0.05, 0.5, 1.0, 10.0):
                result = self.score(rows, beta_fx=beta_fx)
                h = result['health_score']
                self.assertIsNotNone(h)
                self.assertGreaterEqual(h, 0.0)
                self.assertLessEqual(h, 100.0)

    def test_fx_nulo_no_castiga_y_mete_motivo(self):
        rows = con_fx(full_rows(mora=0.2, mult=0.9), None, None)
        result = self.score(rows)
        self.assertIn('indice_fx_desconocido', result['reasons'])
        self.assertEqual(result['adjustments']['penalizacion_fx_puntos'], 0.0)
        self.assertAlmostEqual(result['health_score'],
                               result['adjustments']['after_multiplicador'])

    def test_fx_intervalo_usa_indice_min_y_no_el_max(self):
        # Mezcla de divisas opacas: no hay valor puntual. El motor SOLO ve
        # indice_fx_min; el castigo es el MINIMO compatible y queda acotado.
        rows = con_fx(full_rows(mora=0.2, mult=0.9), None, indice_min=0.4,
                      intervalo=True)
        result = self.score(rows)
        self.assertIn('castigo_fx_acotado_por_intervalo', result['reasons'])
        self.assertEqual(result['inputs']['indice_fx_aplicado'], 0.4)
        self.assertEqual(result['inputs']['indice_es_intervalo'], True)
        after_mult = result['adjustments']['after_multiplicador']
        self.assertAlmostEqual(result['health_score'], after_mult * (1 - 0.05 * 0.4))
        self.assertAlmostEqual(result['adjustments']['penalizacion_fx_puntos'],
                               after_mult * 0.05 * 0.4)

    def test_fx_aplicado_registra_el_valor_en_reasons(self):
        rows = con_fx(full_rows(mora=0.2, mult=0.9), 0.75)
        result = self.score(rows)
        self.assertIn('castigo_fx_indice_0.7500', result['reasons'])
        after_mult = result['adjustments']['after_multiplicador']
        self.assertAlmostEqual(result['health_score'], after_mult * (1 - 0.05 * 0.75))
        self.assertAlmostEqual(result['adjustments']['penalizacion_fx_puntos'],
                               after_mult * 0.05 * 0.75)
        # La descomposicion de los TRES canales suma la perdida total.
        h = result['adjustments']['h_antes_de_ajustes']
        total = (result['adjustments']['penalizacion_mora_puntos']
                 + result['adjustments']['penalizacion_multiplicador_puntos']
                 + result['adjustments']['penalizacion_fx_puntos'])
        self.assertAlmostEqual(h - result['health_score'], total)

    def test_fx_contrato_invalido_y_beta_fx(self):
        with self.assertRaises(ValueError):
            self.score(con_fx(full_rows(), 1.5))
        with self.assertRaises(ValueError):
            self.score(con_fx(full_rows(), -0.1))
        with self.assertRaises(ValueError):
            self.score(con_fx(full_rows(), 0.5, indice_min=2.0, intervalo=True))
        with self.assertRaises(ValueError):
            self.score(full_rows(), beta_fx=-0.1)
        with self.assertRaises(ValueError):
            self.score(full_rows(), beta_fx=float('inf'))

    # --- Obligatorio 5: mora NULL / multiplicador NULL sin ajuste -----------
    def test_mora_null_y_multiplicador_null_sin_ajuste(self):
        result = self.score(full_rows(mora=None, mult=None))
        self.assertIsNotNone(result['health_score'])
        self.assertAlmostEqual(result['health_score'],
                               result['adjustments']['h_antes_de_ajustes'])
        self.assertIn('sin_mora_observable', result['reasons'])
        self.assertIn('multiplicador_deuda_desconocido', result['reasons'])
        self.assertEqual(result['adjustments']['penalizacion_mora_puntos'], 0.0)
        self.assertEqual(
            result['adjustments']['penalizacion_multiplicador_puntos'], 0.0)
        # Nulo nunca es ajuste a cero: la nota sigue en pie.
        self.assertGreater(result['health_score'], 0.0)

    def test_mora_cero_y_multiplicador_uno_no_castigan(self):
        neutra = self.score(full_rows(mora=0.0, mult=1.0))
        nula = self.score(full_rows(mora=None, mult=None))
        self.assertAlmostEqual(neutra['health_score'], nula['health_score'])
        self.assertNotIn('sin_mora_observable', neutra['reasons'])
        self.assertNotIn('multiplicador_deuda_desconocido', neutra['reasons'])

    def test_penalizaciones_descompuestas_en_puntos(self):
        result = self.score(full_rows(mora=0.4, mult=0.8))
        h = result['adjustments']['h_antes_de_ajustes']
        after_mora = result['adjustments']['after_mora']
        self.assertAlmostEqual(h - after_mora,
                               result['adjustments']['penalizacion_mora_puntos'])
        self.assertAlmostEqual(
            after_mora - result['health_score'],
            result['adjustments']['penalizacion_multiplicador_puntos'])
        # SOLO a la baja: ambas penalizaciones no negativas.
        self.assertGreaterEqual(result['adjustments']['penalizacion_mora_puntos'], 0.0)
        self.assertGreaterEqual(
            result['adjustments']['penalizacion_multiplicador_puntos'], 0.0)

    # --- Colchon v4: nivel de caja reconstruida ----------------------------
    def test_colchon_es_max_0_saldo_reversa(self):
        positivo = self.score(full_rows(saldo=8_000.0), k=0.0)
        self.assertEqual(positivo['inputs']['colchon_v4'], 8_000.0)
        negativo = self.score(full_rows(saldo=-8_000.0), k=0.0)
        self.assertEqual(negativo['inputs']['colchon_v4'], 0.0)
        self.assertIn('saldo_reversa_negativo', negativo['reasons'])
        nulo = self.score(full_rows(saldo=None), k=0.0)
        self.assertEqual(nulo['inputs']['colchon_v4'], 0.0)
        self.assertIn('sin_caja_reconstruida', nulo['reasons'])
        # Nunca se inventa: el motivo y la deduccion de confianza van juntos.
        self.assertIn('sin_caja_reconstruida',
                      nulo['confidence_detail']['deducciones'])

    def test_colchon_satura_contra_t6_efectivo(self):
        rows = full_rows(saldo=10**9, oblig=1000.0)
        result = self.score(rows, k=0.0)
        # colchon_aplicable = alpha * T6_efectivo = 3 * (3000 + 1000).
        self.assertAlmostEqual(result['inputs']['colchon_aplicable'], 12_000.0)
        self.assertIn('colchon_saturado', result['reasons'])

    # --- R_hist sobre el denominador efectivo ------------------------------
    def test_r_hist_sobre_t_hist_efectivo(self):
        # El stock del corte entra en T_hist_efectivo una sola vez: la ratio
        # historica refleja el mismo denominador que la ventana.
        sin_stock = self.score(full_rows(oblig=0.0), k=0.0)
        con_stock = self.score(full_rows(oblig=20_000.0), k=0.0)
        self.assertLess(con_stock['health_score'], sin_stock['health_score'])
        self.assertAlmostEqual(con_stock['inputs']['r_hist'],
                               6000.0 / (6000.0 + 3000.0 + 20_000.0))

    # --- Ventana y disciplina point-in-time --------------------------------
    def test_point_in_time_future_rows_do_not_influence(self):
        base = self.score()
        future = full_rows()
        future.append(row(7, c=10_000_000.0, p=0.0, d=0.0, mora=1.0,
                          saldo=10_000_000.0, oblig=10_000_000.0, mult=0.5,
                          deficit=10_000.0))
        after = self.score(future)
        self.assertEqual(base['health_score'], after['health_score'])
        self.assertEqual(base['inputs'], after['inputs'])
        self.assertEqual(base['window'], after['window'])
        self.assertEqual(base['reasons'], after['reasons'])
        early = self.score(full_rows((1, 2, 3, 4, 5, 6)), as_of='2024-03-31')
        only_past = self.score(full_rows((1, 2, 3)), as_of='2024-03-31')
        self.assertEqual(early['health_score'], only_past['health_score'])
        self.assertEqual(early['inputs'], only_past['inputs'])

    def test_expansive_window(self):
        rows = full_rows((1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(self.score([rows[0]], as_of='2024-01-31')['window']['n_meses_ventana'], 1)
        self.assertEqual(self.score(rows[:4], as_of='2024-04-30')['window']['n_meses_ventana'], 4)
        self.assertEqual(self.score(rows[:6], as_of='2024-06-30')['window']['n_meses_ventana'], 6)
        self.assertEqual(self.score(rows, as_of='2024-07-31')['window']['n_meses_ventana'], 6)
        self.assertFalse(self.score(rows[:6])['window']['ventana_parcial'])
        self.assertTrue(self.score(rows[:4], as_of='2024-04-30')['window']['ventana_parcial'])

    # --- Guarda 1 de v3 intacta: sin actividad en todo el historial ---------
    def test_sin_actividad_de_caja_observada(self):
        rows = [row(m, actividad=False, c=None, p=None, d=None, saldo=None,
                    deficit=None, oblig=None) for m in (1, 2, 3)]
        result = self.score(rows, as_of='2024-03-31')
        self.assertIsNone(result['health_score'])
        self.assertIn('sin_actividad_de_caja_observada', result['reasons'])
        # Gana sobre la guarda de P6: no hay actividad, no hay pagos.
        self.assertNotIn('pagos_operativos_no_demostrados', result['reasons'])

    # --- Guarda 3: sin flujos observados en la ventana ----------------------
    def test_sin_flujos_observados_en_la_ventana(self):
        result = self.score([row(1, c=0.0, p=0.0, d=0.0, saldo=0.0, oblig=0.0)], k=0.0)
        self.assertIsNone(result['health_score'])
        self.assertIn('sin_flujos_observados_en_la_ventana', result['reasons'])
        self.assertNotIn('denominador_nulo', result['reasons'])
        # Con p6 > 0 el caso no se da (t6_efectivo > 0 por p6); con c6 = 0
        # y sin colchon ni euros virtuales el numerador es 0: nota 0.
        con_pagos = self.score([row(1, c=0.0, p=50.0, d=0.0, saldo=0.0, oblig=0.0)], k=0.0)
        self.assertIsNotNone(con_pagos['health_score'])
        self.assertAlmostEqual(con_pagos['health_score'], 0.0)

    # --- General: contrato, pureza, serializacion ---------------------------
    def test_does_not_mutate_input_rows(self):
        rows = full_rows(mora=0.5, mult=0.9, deficit=100.0, oblig=200.0)
        original = copy.deepcopy(rows)
        self.score(rows)
        self.assertEqual(rows, original)

    def test_strict_serialization(self):
        for result in (self.score(), self.score(full_rows(saldo=None, mora=None)),
                       self.score([row(1, actividad=False, c=None, p=None, d=None,
                                       saldo=None)]),
                       self.score([row(1, c=0.0, p=0.0, d=0.0, saldo=0.0, oblig=0.0)],
                                  k=0.0),
                       self.score([row(1, p=0.0, oblig=5000.0)])):
            json.dumps(result, allow_nan=False)

    def test_determinism(self):
        self.assertEqual(self.score(), self.score())
        rows = full_rows(mora=0.4, mult=0.7, saldo=None)
        self.assertEqual(score_company('A', 'G', rows, '2024-06-30', k=500.0,
                                       alpha=2.0, beta=0.3),
                         score_company('A', 'G', copy.deepcopy(rows), '2024-06-30',
                                       k=500.0, alpha=2.0, beta=0.3))

    def test_no_cross_company_dependence(self):
        rows = full_rows(mora=0.2, mult=0.9)
        a = score_company('empresa_a', 'grupo_1', copy.deepcopy(rows), '2024-06-30')
        b = score_company('empresa_b', 'grupo_2', copy.deepcopy(rows), '2024-06-30')
        self.assertEqual(a['health_score'], b['health_score'])
        self.assertEqual(a['inputs'], b['inputs'])

    def test_contract_violations_raise(self):
        with self.assertRaises(ValueError):
            self.score([dict(row(1), month=date(2024, 1, 15))])
        with self.assertRaises(ValueError):
            self.score([row(1), row(1)])  # mes duplicado
        with self.assertRaises(ValueError):
            self.score([dict(row(1), c_eur=-1.0)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), p_eur=float('nan'))])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), d_eur=float('inf'))])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), saldo_reversa_eur=float('nan'))])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), deficit_servicio_eur=-1.0)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), obligacion_vencida_eur=-1.0)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), multiplicador_deuda=1.5)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), multiplicador_deuda=-0.1)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), mora_indice=1.5)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), mora_indice=-0.1)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), tiene_actividad='si')])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), confianza_entradas='regular')])
        with self.assertRaises(ValueError):
            self.score(as_of='2024-06-15')  # no es fin de mes
        with self.assertRaises(ValueError):
            self.score(as_of='no-es-fecha')
        with self.assertRaises(ValueError):
            self.score(k=-1.0)
        with self.assertRaises(ValueError):
            self.score(alpha=-0.5)
        with self.assertRaises(ValueError):
            self.score(beta=-0.1)
        with self.assertRaises(ValueError):
            self.score(k=float('inf'))

    def test_r_hist_indefinida_omite_termino_k(self):
        # Sin ningun importe conocido en todo el historial ni stock: R_hist
        # indefinida, el termino k se omite por completo y no hay nota.
        rows = full_rows(c=None, p=None, d=None, saldo=None, deficit=None,
                         oblig=None)
        con_k = self.score(rows, k=1e9)
        self.assertIn('r_hist_indefinida', con_k['reasons'])
        self.assertIsNone(con_k['inputs']['r_hist'])
        self.assertIsNone(con_k['health_score'])
        # La guarda que dispara es la de flujos (c6 = 0 y t6 = 0), que va
        # antes que la de pagos.
        self.assertIn('sin_flujos_observados_en_la_ventana', con_k['reasons'])
        # Con stock en el corte y c6 = 0 la ratio es 0/(0+stock): definida
        # y la nota cae a 0 (sin euros virtuales que la sostengan).
        con_stock = self.score(full_rows(c=None, p=0.0, d=0.0, saldo=0.0,
                                         oblig=10_000.0), k=1e9)
        self.assertAlmostEqual(con_stock['inputs']['r_hist'], 0.0)
        self.assertAlmostEqual(con_stock['health_score'], 0.0)

    def test_version_y_serializacion_inputs(self):
        result = self.score(full_rows(mora=0.3, mult=0.8, oblig=1500.0,
                                      deficit=75.0))
        self.assertEqual(result['version'], 'healthscore_v4')
        inputs = result['inputs']
        self.assertEqual(inputs['c6'], 6000.0)
        self.assertEqual(inputs['p6'], 2400.0)
        self.assertEqual(inputs['d6'], 600.0)
        self.assertEqual(inputs['deficit_servicio_6'], 450.0)
        self.assertEqual(inputs['obligacion_vencida_m'], 1500.0)
        # t6_efectivo = 2400 + 600 + 450 + 1500 (stock, una sola vez).
        self.assertAlmostEqual(inputs['t6_efectivo'], 4950.0)
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
