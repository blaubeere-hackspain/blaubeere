"""Tests de la union cobros+pagos (`xray/cashflow_projection.py`).

Cubren, como exige el encargo:
  (a) suma de signos: cobros y pagos se compensan y el neto sale exactamente cero;
  (b) no imputacion: saldo de corte nulo -> saldo proyectado nulo, nunca cero;
  (c) `dia_primer_saldo_negativo` correcto en un caso construido a mano.
Ademas: convenio de signo, no imputacion de un lado desconocido, cobertura
asimetrica y reproduccion de las cifras de referencia del corte vivo.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import pandas as pd

from xray import cashflow_projection as cp

CORTE = pd.Timestamp('2026-01-31')


def _calendario(filas):
    frame = pd.DataFrame(filas, columns=['company_id', 'corte', 'dia', 'importe_esperado_eur'])
    frame['corte'] = pd.to_datetime(frame['corte']).dt.normalize()
    frame['dia'] = pd.to_datetime(frame['dia']).dt.normalize()
    return frame


def _horizontes(filas):
    frame = pd.DataFrame(filas, columns=['company_id', 'corte', 'h', 'importe_esperado_eur'])
    frame['corte'] = pd.to_datetime(frame['corte']).dt.normalize()
    frame['h'] = frame['h'].astype(int)
    return frame


def _panel(filas):
    frame = pd.DataFrame(filas, columns=['company_id', 'day', 'saldo_reversa_eur'])
    frame['day'] = pd.to_datetime(frame['day']).dt.normalize()
    return frame


class SumaDeSignosTest(unittest.TestCase):
    def test_cobros_y_pagos_se_compensan_a_cero(self):
        cobros = _calendario([('COMP_A', CORTE, '2026-02-01', 100.0)])
        pagos = _calendario([('COMP_A', CORTE, '2026-02-01', -100.0)])
        estado = {('COMP_A', CORTE): 'con_datos'}
        saldo = {('COMP_A', CORTE): 1000.0}

        diario = cp.construir_calendario_neto(cobros, pagos, estado, estado, saldo)

        self.assertEqual(len(diario), 1)
        fila = diario.iloc[0]
        self.assertAlmostEqual(fila['entrada_esperada_eur'], 100.0)
        self.assertAlmostEqual(fila['salida_esperada_eur'], -100.0)
        self.assertEqual(fila['neto_dia_eur'], 0.0)
        self.assertAlmostEqual(fila['saldo_acumulado_eur'], 1000.0)

    def test_convenio_de_signo_no_re_firma(self):
        # cobros positivo, pagos negativo: la union es suma directa
        cobros = _calendario([('COMP_A', CORTE, '2026-02-01', 250.0)])
        pagos = _calendario([('COMP_A', CORTE, '2026-02-01', -100.0)])
        diario = cp.construir_calendario_neto(
            cobros, pagos, {('COMP_A', CORTE): 'con_datos'},
            {('COMP_A', CORTE): 'con_datos'}, {('COMP_A', CORTE): 0.0})
        self.assertAlmostEqual(diario.iloc[0]['neto_dia_eur'], 150.0)

    def test_suma_de_signos_con_none(self):
        self.assertEqual(cp._suma_signos(100.0, -100.0), 0.0)
        self.assertIsNone(cp._suma_signos(None, -100.0))


class NoImputacionTest(unittest.TestCase):
    def test_saldo_proyectado_nulo_cuando_saldo_de_corte_es_nulo(self):
        cobros = _horizontes([('COMP_A', CORTE, h, 100.0) for h in (30, 60, 90)])
        pagos = _horizontes([('COMP_A', CORTE, h, -40.0) for h in (30, 60, 90)])
        # La empresa esta en el panel (es activa) pero su saldo de corte es nulo.
        panel = _panel([('COMP_A', CORTE, None)])

        agregado = cp.construir_horizontes_union(cobros, pagos, panel, {'COMP_A'})

        self.assertEqual(len(agregado), 3)
        self.assertTrue(agregado['saldo_corte_eur'].isna().all())
        self.assertTrue(agregado['saldo_proyectado_eur'].isna().all())
        for valor in agregado['saldo_proyectado_eur']:
            self.assertFalse(valor == 0)
        # El flujo neto si existe: no se confunde "saldo desconocido" con "cero".
        self.assertTrue((agregado['flujo_neto_esperado_eur'] == 60.0).all())

    def test_saldo_proyectado_es_nulo_no_cero_en_el_calendario(self):
        cobros = _calendario([('COMP_A', CORTE, '2026-02-01', 100.0)])
        diario = cp.construir_calendario_neto(
            cobros, cobros.iloc[0:0], {('COMP_A', CORTE): 'con_datos'},
            {('COMP_A', CORTE): 'cero_conocido_sin_facturas_vivas'},
            {('COMP_A', CORTE): None})
        self.assertTrue(pd.isna(diario.iloc[0]['saldo_acumulado_eur']))

    def test_lado_desconocido_no_se_imputa_cero(self):
        # COMP_B no esta en el censo del panel: su lado de cobros ausente es
        # DESCONOCIDO, no un cero conocido.
        cobros = _horizontes([('COMP_A', CORTE, 30, 100.0)])
        pagos = _horizontes([('COMP_A', CORTE, 30, -40.0),
                             ('COMP_B', CORTE, 30, -70.0)])
        panel = _panel([('COMP_A', CORTE, 500.0)])
        agregado = cp.construir_horizontes_union(cobros, pagos, panel, {'COMP_A'})
        fila_b = agregado[agregado['company_id'] == 'COMP_B'].iloc[0]
        self.assertEqual(fila_b['estado_cobros'],
                         'desconocido_empresa_fuera_censo_panel')
        self.assertTrue(pd.isna(fila_b['entrada_esperada_eur']))
        self.assertTrue(pd.isna(fila_b['flujo_neto_esperado_eur']))


class CoberturaAsimetricaTest(unittest.TestCase):
    def test_ausencia_conocida_en_un_lado_es_cero(self):
        cobros = _horizontes([('COMP_A', CORTE, 30, 100.0)])
        pagos = _horizontes([('COMP_A', CORTE, 30, -40.0),
                             ('COMP_B', CORTE, 30, -70.0)])
        panel = _panel([('COMP_A', CORTE, 500.0), ('COMP_B', CORTE, 200.0)])
        agregado = cp.construir_horizontes_union(cobros, pagos, panel,
                                                 {'COMP_A', 'COMP_B'})
        fila_b = agregado[agregado['company_id'] == 'COMP_B'].iloc[0]
        self.assertEqual(fila_b['cobertura_lado'], 'solo_pagos')
        self.assertEqual(fila_b['estado_cobros'], 'cero_conocido_sin_facturas_vivas')
        self.assertEqual(fila_b['entrada_esperada_eur'], 0.0)
        self.assertAlmostEqual(fila_b['flujo_neto_esperado_eur'], -70.0)


class PrimerDiaNegativoTest(unittest.TestCase):
    def test_cruce_en_el_segundo_dia(self):
        cobros = _calendario([('COMP_A', CORTE, '2026-02-01', 0.0),
                              ('COMP_A', CORTE, '2026-02-02', 0.0)])
        pagos = _calendario([('COMP_A', CORTE, '2026-02-01', -400.0),
                             ('COMP_A', CORTE, '2026-02-02', -700.0)])
        diario = cp.construir_calendario_neto(
            cobros, pagos, {('COMP_A', CORTE): 'con_datos'},
            {('COMP_A', CORTE): 'con_datos'}, {('COMP_A', CORTE): 1000.0})
        dias = list(diario['dia_primer_saldo_negativo'])
        self.assertEqual(set(dias), {pd.Timestamp('2026-02-02')})
        # saldo: 1000 - 400 = 600 (positivo), 600 - 700 = -100 (negativo)
        acumulado = diario.sort_values('dia')['saldo_acumulado_eur'].tolist()
        self.assertAlmostEqual(acumulado[0], 600.0)
        self.assertAlmostEqual(acumulado[1], -100.0)

    def test_no_cruza_devuelve_nulo(self):
        cobros = _calendario([('COMP_A', CORTE, '2026-02-01', 500.0)])
        diario = cp.construir_calendario_neto(
            cobros, cobros.iloc[0:0], {('COMP_A', CORTE): 'con_datos'},
            {('COMP_A', CORTE): 'cero_conocido_sin_facturas_vivas'},
            {('COMP_A', CORTE): 100.0})
        self.assertTrue(all(pd.isna(d) for d in diario['dia_primer_saldo_negativo']))

    def test_saldo_corte_ya_negativo_no_es_un_cruce(self):
        self.assertIsNone(cp._primer_dia_negativo(
            [pd.Timestamp('2026-02-01')], [-10.0], -5.0))
        self.assertEqual(
            cp._primer_dia_negativo([pd.Timestamp('2026-02-01')], [-10.0], 5.0),
            pd.Timestamp('2026-02-01'))


class TendenciaTest(unittest.TestCase):
    def test_banda_neutra(self):
        # movimiento bruto 200 -> banda 20 al 10%
        self.assertEqual(cp.clasificar_tendencia(100.0, -100.0, 0.0), 'neutra')
        self.assertEqual(cp.clasificar_tendencia(100.0, -100.0, 15.0), 'neutra')
        self.assertEqual(cp.clasificar_tendencia(100.0, -100.0, 25.0), 'positiva')
        self.assertEqual(cp.clasificar_tendencia(100.0, -100.0, -25.0), 'negativa')
        self.assertIsNone(cp.clasificar_tendencia(100.0, -100.0, None))


class ReferenciasCorteVivoTest(unittest.TestCase):
    def test_reproduce_las_referencias_del_corte_vivo(self):
        if not (Path(cp.DEFAULT_COBROS_DIR) / 'horizontes.parquet').exists():
            self.skipTest('faltan los horizontes de cobros copiados')
        cobros_cal, cobros_hor, _ = cp.load_lado(cp.DEFAULT_COBROS_DIR, 'cobros')
        pagos_cal, pagos_hor, _ = cp.load_lado(cp.DEFAULT_PAGOS_DIR, 'pagos')
        # no lanza si reproduce las referencias del encargo
        resultado = cp.reproducir_referencias(cobros_hor, pagos_hor,
                                              cobros_cal, pagos_cal)
        self.assertAlmostEqual(resultado['cobros']['por_h_m_eur']['30'], 263.6, delta=0.1)
        self.assertAlmostEqual(resultado['pagos']['por_h_m_eur']['90'], -439.9, delta=0.1)
        self.assertEqual(resultado['cobros']['empresas_calendario'], 654)
        self.assertEqual(resultado['pagos']['empresas_calendario'], 737)


if __name__ == '__main__':
    unittest.main()
