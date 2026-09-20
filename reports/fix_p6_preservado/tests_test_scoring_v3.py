"""Tests del motor puro healthscore_v3 (xray/scoring_v3.py).

Sin IO: todo son fixtures en memoria. El test point-in-time es el mas
importante del fichero.
"""

import copy
import json
import unittest
from datetime import date

from xray.scoring_v3 import score_company


def month(day):
    return date(2024, day, 1)


def row(mes, c=1000.0, p=400.0, d=100.0, actividad=True, colchon=500.0,
        salida_media=None, mora=None, confianza='alta'):
    return {
        'month': month(mes),
        'c_eur': c,
        'p_eur': p,
        'd_eur': d,
        'tiene_actividad': actividad,
        'colchon_eur': colchon,
        'salida_media_mensual_eur': salida_media,
        'mora_ratio': mora,
        'confianza_entradas': confianza,
    }


def full_rows(meses=(1, 2, 3, 4, 5, 6), **kwargs):
    return [row(m, **kwargs) for m in meses]


class ScoringV3Test(unittest.TestCase):
    def score(self, rows=None, as_of='2024-06-30', **kwargs):
        return score_company('A', 'G', full_rows() if rows is None else rows,
                             as_of, **kwargs)

    # 1. POINT-IN-TIME: el test mas importante.
    def test_point_in_time_future_rows_do_not_influence(self):
        base = self.score()
        future = full_rows((1, 2, 3, 4, 5, 6))
        # Filas posteriores al corte con valores extremos: si influyeran,
        # la nota cambiaría.
        future.append(row(7, c=10_000_000.0, p=0.0, d=0.0, mora=1.0,
                          colchon=10_000_000.0))
        future.append(row(8, c=0.0, p=10_000_000.0, d=0.0, mora=1.0,
                          colchon=-10_000_000.0))
        after = self.score(future)
        self.assertEqual(base['health_score'], after['health_score'])
        self.assertEqual(base['inputs'], after['inputs'])
        self.assertEqual(base['window'], after['window'])
        self.assertEqual(base['reasons'], after['reasons'])
        # Variante: corte anterior con la misma lista de filas.
        early = self.score(full_rows((1, 2, 3, 4, 5, 6)), as_of='2024-03-31')
        only_past = self.score(full_rows((1, 2, 3)), as_of='2024-03-31')
        self.assertEqual(early['health_score'], only_past['health_score'])
        self.assertEqual(early['inputs'], only_past['inputs'])

    # 2. k=0 reduce H a la ratio de la ventana con colchon.
    def test_k_zero_is_window_ratio_with_cushion(self):
        result = self.score(k=0.0)
        c6, t6 = 6000.0, 3000.0
        colchon = 500.0  # < alpha * T6 con alpha=3 -> no saturado
        esperado = 100.0 * (c6 + colchon) / (c6 + colchon + t6)
        self.assertAlmostEqual(result['health_score'], esperado)
        self.assertAlmostEqual(result['adjustments']['h_antes_de_mora'], esperado)

    # 3. k grande empuja H hacia 100 * R_hist (tendencia, no valor magico).
    def test_large_k_pushes_towards_hist_ratio(self):
        rows = full_rows()
        c_hist, t_hist = 6000.0, 3000.0
        objetivo = 100.0 * c_hist / (c_hist + t_hist)
        distancias = []
        for k in (0.0, 1e3, 1e5, 1e7, 1e9):
            nota = self.score(rows, k=k)['health_score']
            distancias.append(abs(nota - objetivo))
        for menor, mayor in zip(distancias, distancias[1:]):
            self.assertLessEqual(mayor, menor)
        self.assertLess(distancias[-1], distancias[0])

    # 4. Monotonia.
    def test_monotony(self):
        base = self.score()
        # Subir C6 no baja H.
        mas_cobros = [dict(r, c_eur=r['c_eur'] + 500.0) for r in full_rows()]
        self.assertGreaterEqual(self.score(mas_cobros)['health_score'],
                                base['health_score'])
        # Subir P6 no sube H.
        mas_pagos = [dict(r, p_eur=r['p_eur'] + 500.0) for r in full_rows()]
        self.assertLessEqual(self.score(mas_pagos)['health_score'],
                             base['health_score'])
        # Subir D6 no sube H.
        mas_deuda = [dict(r, d_eur=r['d_eur'] + 500.0) for r in full_rows()]
        self.assertLessEqual(self.score(mas_deuda)['health_score'],
                             base['health_score'])
        # Subir mora_ratio no sube H_final.
        con_mora_baja = [dict(r, mora=0.2) for r in full_rows()]
        con_mora_alta = [dict(r, mora=0.8) for r in full_rows()]
        self.assertLessEqual(self.score(con_mora_alta)['health_score'],
                             self.score(con_mora_baja)['health_score'])

    # 5. Invariancia a escala monetaria.
    def test_scale_invariance(self):
        rows = full_rows(mora=0.3)
        factor = 1000.0
        escalado = [dict(r, c_eur=r['c_eur'] * factor, p_eur=r['p_eur'] * factor,
                         d_eur=r['d_eur'] * factor, colchon_eur=r['colchon_eur'] * factor,
                         salida_media_mensual_eur=(r['salida_media_mensual_eur'] * factor
                                                   if r['salida_media_mensual_eur'] is not None
                                                   else None))
                    for r in rows]
        base = self.score(rows, k=250.0, alpha=3.0, beta=0.4)
        esc = self.score(escalado, k=250.0 * factor, alpha=3.0, beta=0.4)
        self.assertAlmostEqual(base['health_score'], esc['health_score'], places=6)
        # Tambien con colchon gigante que satura.
        saturado = [dict(r, colchon_eur=1e9) for r in rows]
        saturado_esc = [dict(r, colchon_eur=1e9 * factor) for r in escalado]
        base2 = self.score(saturado, k=250.0)
        esc2 = self.score(saturado_esc, k=250.0 * factor)
        self.assertAlmostEqual(base2['health_score'], esc2['health_score'], places=6)

    # 6. Ventana expansiva.
    def test_expansive_window(self):
        rows = full_rows((1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(self.score([rows[0]], as_of='2024-01-31')['window']['n_meses_ventana'], 1)
        self.assertEqual(self.score(rows[:4], as_of='2024-04-30')['window']['n_meses_ventana'], 4)
        self.assertEqual(self.score(rows[:6], as_of='2024-06-30')['window']['n_meses_ventana'], 6)
        self.assertEqual(self.score(rows, as_of='2024-07-31')['window']['n_meses_ventana'], 6)
        self.assertEqual(self.score(rows, as_of='2024-08-31')['window']['n_meses_ventana'], 6)
        self.assertFalse(self.score(rows[:6])['window']['ventana_parcial'])
        self.assertTrue(self.score(rows[:4], as_of='2024-04-30')['window']['ventana_parcial'])

    # 7. Saturacion del colchon.
    def test_cushion_saturation(self):
        rows = full_rows(colchon=1e9)
        result = self.score(rows, k=0.0)
        c6, t6 = 6000.0, 3000.0
        # colchon_aplicable queda acotado por alpha * T6 = 3 * 3000.
        esperado = 100.0 * (c6 + 3.0 * t6) / (c6 + 3.0 * t6 + t6)
        self.assertAlmostEqual(result['health_score'], esperado)
        self.assertLess(result['health_score'], 100.0)
        self.assertAlmostEqual(result['inputs']['colchon_aplicable'], 3.0 * t6)
        self.assertIn('colchon_saturado', result['reasons'])
        # Sin saturar, el mismo tope no actua.
        sin_saturar = self.score(full_rows(colchon=500.0), k=0.0)
        self.assertNotIn('colchon_saturado', sin_saturar['reasons'])

    # 8. Colchon negativo.
    def test_negative_cushion(self):
        result = self.score(full_rows(colchon=-8000.0))
        self.assertEqual(result['inputs']['colchon_aplicable'], 0.0)
        self.assertEqual(result['inputs']['colchon_bruto'], -8000.0)
        self.assertIn('colchon_negativo', result['reasons'])
        esperado = 100.0 * (6000.0 + 1000.0 * 6000.0 / 9000.0) / (6000.0 + 3000.0 + 1000.0)
        self.assertAlmostEqual(result['health_score'], esperado)

    # 9. mora 0 vs None: misma nota, reasons distintos.
    def test_mora_zero_vs_none(self):
        con_cero = self.score(full_rows(mora=0.0))
        con_none = self.score(full_rows(mora=None))
        self.assertAlmostEqual(con_cero['health_score'], con_none['health_score'])
        self.assertIn('sin_mora_observable', con_none['reasons'])
        self.assertNotIn('sin_mora_observable', con_cero['reasons'])

    # 10. beta=0 desactiva la penalizacion.
    def test_beta_zero_disables_penalty(self):
        con_mora = self.score(full_rows(mora=1.0), beta=0.0)
        sin_mora = self.score(full_rows(mora=None), beta=0.0)
        self.assertAlmostEqual(con_mora['health_score'], sin_mora['health_score'])
        self.assertEqual(con_mora['adjustments']['penalizacion_mora_puntos'], 0.0)
        # Y con beta>0 si penaliza.
        penalizado = self.score(full_rows(mora=1.0), beta=0.5)
        self.assertAlmostEqual(penalizado['health_score'],
                               0.5 * penalizado['adjustments']['h_antes_de_mora'])

    # 11. Nulos: no son cero y bajan confidence.
    def test_nones_are_not_zero_and_lower_confidence(self):
        completa = self.score()
        # Mes 6 sin c_eur ni p_eur: el nulo NO se cuenta como cero.
        con_nulos = [dict(r, c_eur=None, p_eur=None) if r['month'] == month(6) else r
                     for r in full_rows()]
        parcial = self.score(con_nulos)
        self.assertAlmostEqual(parcial['inputs']['c6'], 5000.0)
        # d_eur sigue conocido en los 6 meses: t6 = 5*(400+100) + 100.
        self.assertAlmostEqual(parcial['inputs']['t6'], 2600.0)
        self.assertIn('c_eur_desconocido_en_1_meses', parcial['reasons'])
        self.assertIn('p_eur_desconocido_en_1_meses', parcial['reasons'])
        self.assertLess(parcial['confidence'], completa['confidence'])

    # 12. Situaciones con health_score None: sin actividad en todo el
    # historial, sin flujos observados en la ventana y sin pagos operativos
    # demostrados.
    def test_no_activity_is_the_only_none(self):
        rows = [row(m, actividad=False, c=None, p=None, d=None, colchon=None)
                for m in (1, 2, 3)]
        result = self.score(rows)
        self.assertIsNone(result['health_score'])
        self.assertIn('sin_actividad_de_caja_observada', result['reasons'])
        # Sin pagos operativos demostrados (p=None) NO hay nota, aunque C sea
        # conocido y k=0: es el bug de la nota 100 sin pagos operativos,
        # bloqueado aqui (guarda sobre P6, no sobre T6).
        con_una_activa = rows + [row(4, c=100.0, p=None, d=None, colchon=0.0)]
        sin_pagos = self.score(con_una_activa, k=0.0)
        self.assertIsNone(sin_pagos['health_score'])
        self.assertIn('pagos_operativos_no_demostrados', sin_pagos['reasons'])
        # Con un pago operativo conocido SI hay nota aunque el resto sean None
        # (el termino k se omite si r_hist es indefinida).
        con_pagos = rows + [row(4, c=100.0, p=50.0, d=None, colchon=0.0)]
        con_nota = self.score(con_pagos, k=0.0)
        self.assertIsNotNone(con_nota['health_score'])
        self.assertAlmostEqual(con_nota['health_score'], 100.0 * 100.0 / 150.0)
        self.assertNotIn('pagos_operativos_no_demostrados', con_nota['reasons'])

    # 13. Sin flujos observados en la ventana (c6=0 y t6=0): None con reason,
    # nunca 100. Es el escenario que antes caia en 'denominador_nulo',
    # inalcanzable desde la guarda de P6.
    def test_null_denominator(self):
        result = self.score([row(1, c=0.0, p=0.0, d=0.0, colchon=0.0)], k=0.0)
        self.assertIsNone(result['health_score'])
        self.assertIn('sin_flujos_observados_en_la_ventana', result['reasons'])
        self.assertNotIn('sin_actividad_de_caja_observada', result['reasons'])

    # 14. El motor no muta monthly_rows.
    def test_does_not_mutate_input_rows(self):
        rows = full_rows(mora=0.5, salida_media=100.0)
        original = copy.deepcopy(rows)
        self.score(rows)
        self.assertEqual(rows, original)

    # 15. Serializacion estricta.
    def test_strict_serialization(self):
        for result in (self.score(), self.score(full_rows(colchon=None, mora=None,
                                                          salida_media=None)),
                       self.score([row(1, actividad=False, c=None, p=None, d=None,
                                       colchon=None)]),
                       self.score([row(1, c=0.0, p=0.0, d=0.0, colchon=0.0)], k=0.0)):
            json.dumps(result, allow_nan=False)

    # 16. Determinismo.
    def test_determinism(self):
        self.assertEqual(self.score(), self.score())
        rows = full_rows(mora=0.4, colchon=None)
        self.assertEqual(score_company('A', 'G', rows, '2024-06-30', k=500.0,
                                       alpha=2.0, beta=0.3),
                         score_company('A', 'G', copy.deepcopy(rows), '2024-06-30',
                                       k=500.0, alpha=2.0, beta=0.3))

    # 17. Dos empresas, mismas filas: misma nota.
    def test_no_cross_company_dependence(self):
        rows = full_rows(mora=0.2)
        a = score_company('empresa_a', 'grupo_1', copy.deepcopy(rows), '2024-06-30')
        b = score_company('empresa_b', 'grupo_2', copy.deepcopy(rows), '2024-06-30')
        self.assertEqual(a['health_score'], b['health_score'])
        self.assertEqual(a['inputs'], b['inputs'])

    # Casos limite de contrato: errores explicitos.
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
            self.score([dict(row(1), colchon_eur=float('nan'))])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), mora_ratio=1.5)])
        with self.assertRaises(ValueError):
            self.score([dict(row(1), mora_ratio=-0.1)])
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

    # Extras de comportamiento declarado.
    def test_r_hist_undefined_omits_k_term(self):
        # Historia sin ningun importe conocido: R_hist indefinida, el termino
        # k se omite por completo (equivale a k=0, sin euros virtuales) y se
        # registra el motivo.
        rows = full_rows(c=None, p=None, d=None, colchon=None, salida_media=None)
        con_k = self.score(rows, k=1e9)
        sin_k = self.score(rows, k=0.0)
        self.assertIn('r_hist_indefinida', con_k['reasons'])
        self.assertIsNone(con_k['inputs']['r_hist'])
        # Sin importes conocidos c6=0 y t6=0: sin flujos observados en la
        # ventana, no hay nota, con k grande ni con k=0.
        self.assertIsNone(con_k['health_score'])
        self.assertIsNone(sin_k['health_score'])
        self.assertIn('sin_flujos_observados_en_la_ventana', con_k['reasons'])

    def test_missing_cushion_and_mora_reasons(self):
        result = self.score(full_rows(colchon=None, mora=None))
        self.assertEqual(result['inputs']['colchon_aplicable'], 0.0)
        self.assertIn('sin_colchon_estimado', result['reasons'])
        self.assertIn('sin_mora_observable', result['reasons'])

    def test_confidence_scale(self):
        # Ventana completa, todo conocido, mora y colchon presentes -> alta.
        self.assertEqual(self.score(full_rows(mora=0.0))['confidence'], 'alta')
        # Mora y colchon ausentes: dos deducciones (3-2) -> baja.
        self.assertEqual(self.score(full_rows(colchon=None, mora=None))['confidence'],
                         'baja')
        # Entradas 'baja' en la ventana: base baja, deducciones -> ninguna.
        self.assertEqual(self.score(full_rows(confianza='baja', colchon=None,
                                             mora=None))['confidence'], 'ninguna')
        # Mes de corte sin actividad: mora y colchon no observables -> 3-2 = baja.
        sin_actividad_mes = full_rows()[:-1] + [row(6, actividad=False, c=None, p=None,
                                                    d=None, colchon=None, mora=None)]
        self.assertEqual(self.score(sin_actividad_mes)['confidence'], 'baja')


if __name__ == '__main__':
    unittest.main()
