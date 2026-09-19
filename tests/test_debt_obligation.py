"""Tests de xray/debt_obligation.py (fixtures temporales, sin datos reales)."""

import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from xray import debt_obligation as do_mod
from xray.debt_obligation import (
    BUCKET_WEIGHTS,
    MULTIPLICADOR_MINIMO,
    DebtObligationRow,
    FlowRecord,
    PayableRecord,
    closed_months,
    compute_debt_obligation,
)

M2024_10 = date(2024, 10, 1)   # corte 2024-10-31
M2024_11 = date(2024, 11, 1)
M2024_12 = date(2024, 12, 1)
M2025_01 = date(2025, 1, 1)    # corte 2025-01-31
M2025_02 = date(2025, 2, 1)    # corte 2025-02-28
M2025_03 = date(2025, 3, 1)    # corte 2025-03-31


def pay(**kw) -> PayableRecord:
    defaults = dict(
        company_id="C1",
        document_type="invoice",
        flow_side="outflow",
        status="pending",
        due_date=date(2025, 1, 10),
        payment_date=None,
        amount_eur=100.0,
        fx_ambiguous=False,
    )
    defaults.update(kw)
    return PayableRecord(**defaults)


def flow(month: date, pagos: float | None = None, servicio: float | None = None,
         available_at: date | None = None, cid: str = "C1") -> FlowRecord:
    return FlowRecord(
        company_id=cid, month=month, pagos_operativos_eur=pagos,
        servicio_deuda_eur=servicio, available_at=available_at,
    )


def by_month(rows, months):
    return {r.month: r for r in rows if r.month in months}


class PointInTimeTests(unittest.TestCase):
    def test_1_pagada_despues_del_corte_cuenta_vencida_en_m(self):
        """Factura vencida en m y pagada en m+2: vencida en m, no en m+2."""
        rows = [pay(status="paid", payment_date=date(2025, 3, 15), amount_eur=200.0)]
        flows = [flow(M2025_01, pagos=1000.0), flow(M2025_02, pagos=1000.0),
                 flow(M2025_03, pagos=1000.0)]
        res = by_month(compute_debt_obligation(rows, flows, [M2025_01, M2025_02, M2025_03]),
                       [M2025_01, M2025_02, M2025_03])
        ene, feb, mar = res[M2025_01], res[M2025_02], res[M2025_03]
        self.assertEqual(ene.n_vencidas, 1)
        self.assertEqual(ene.obligacion_vencida_eur, 200.0)
        self.assertEqual(feb.obligacion_vencida_eur, 200.0)
        self.assertEqual(mar.n_vencidas, 0)
        self.assertEqual(mar.obligacion_vencida_eur, 0.0)
        self.assertEqual(mar.multiplicador_deuda, 1.0)
        # re-evaluar el corte de enero con el dataset completo no cambia enero
        solo_ene = compute_debt_obligation(rows, flows, [M2025_01])[0]
        self.assertEqual(solo_ene.obligacion_vencida_eur, ene.obligacion_vencida_eur)
        self.assertEqual(solo_ene.multiplicador_deuda, ene.multiplicador_deuda)

    def test_2_due_posterior_al_corte_no_cuenta(self):
        rows = [pay(due_date=date(2025, 2, 10), status="overdue", amount_eur=999.0)]
        ene = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(ene.n_debido, 0)
        self.assertEqual(ene.n_vencidas, 0)
        self.assertEqual(ene.obligacion_vencida_eur, 0.0)
        self.assertEqual(ene.multiplicador_deuda, 1.0)
        self.assertEqual(ene.confidence, "ninguna")
        # en febrero ya es exigible
        feb = compute_debt_obligation(rows, [], [M2025_02])[0]
        self.assertEqual(feb.n_vencidas, 1)

    def test_3_payment_date_prevista_en_status_no_paid_es_null(self):
        """En status <> 'paid' payment_date es prevista: la factura sigue vencida."""
        rows = [
            pay(status="overdue", payment_date=date(2025, 1, 15)),  # "pagaria" tras vencer
            pay(status="pending", payment_date=date(2025, 1, 5)),   # "pagaria" antes de vencer
        ]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.n_vencidas, 2)
        self.assertEqual(res.obligacion_vencida_eur, 200.0)

    def test_4_due_igual_al_corte_cuenta_en_el_bucket_1_30(self):
        rows = [pay(due_date=date(2025, 1, 31), status="overdue", amount_eur=50.0)]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.vencido_1_30_eur, 50.0)
        self.assertEqual(res.n_vencidas, 1)


class BucketTests(unittest.TestCase):
    def test_5_buckets_por_antiguedad(self):
        rows = [
            pay(due_date=date(2025, 1, 20), amount_eur=10.0),   # 11 dias -> 1-30
            pay(due_date=date(2024, 12, 15), amount_eur=20.0),  # 47 dias -> 31-60
            pay(due_date=date(2024, 11, 15), amount_eur=30.0),  # 77 dias -> 61-90
            pay(due_date=date(2024, 10, 15), amount_eur=40.0),  # 108 dias -> 91-180
            pay(due_date=date(2024, 6, 1), amount_eur=50.0),    # 244 dias -> 180+
        ]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(
            (res.vencido_1_30_eur, res.vencido_31_60_eur, res.vencido_61_90_eur,
             res.vencido_91_180_eur, res.vencido_180_mas_eur),
            (10.0, 20.0, 30.0, 40.0, 50.0),
        )
        self.assertEqual(res.obligacion_vencida_eur, 150.0)

    def test_6_cancel_no_es_obligacion(self):
        rows = [pay(status="cancel", amount_eur=300.0), pay(amount_eur=100.0)]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.n_cancel, 1)
        self.assertEqual(res.n_debido, 1)
        self.assertEqual(res.obligacion_vencida_eur, 100.0)


class NullNeverZeroTests(unittest.TestCase):
    def test_7_amount_nulo_no_es_cero(self):
        rows = [pay(amount_eur=None)]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.n_vencidas, 1)
        self.assertEqual(res.n_sin_eur, 1)
        self.assertIsNone(res.obligacion_vencida_eur)   # nada medible
        self.assertIsNone(res.obligacion_no_atendida_eur)
        self.assertIsNone(res.multiplicador_deuda)
        self.assertEqual(res.confidence, "baja")
        self.assertEqual(res.reason, "importes_desconocidos_en_el_corte")
        self.assertEqual(res.vencido_1_30_eur, 0.0)     # no se imputa el hueco

    def test_8_fx_ambiguous_degrada_confianza(self):
        rows = [pay(amount_eur=100.0), pay(amount_eur=50.0, fx_ambiguous=True,
                                           due_date=date(2025, 1, 20))]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.n_vencidas, 2)
        self.assertEqual(res.n_sin_eur, 1)
        self.assertEqual(res.obligacion_vencida_eur, 100.0)  # el hueco no suma
        self.assertEqual(res.confidence, "baja")             # 1/2 = 50%

    def test_9_mixto_agujero_minoria_confianza_media(self):
        rows = [pay(amount_eur=100.0), pay(amount_eur=200.0, due_date=date(2025, 1, 5)),
                pay(amount_eur=None, due_date=date(2025, 1, 15))]
        res = compute_debt_obligation(rows, [], [M2025_01])[0]
        self.assertEqual(res.n_vencidas, 3)
        self.assertEqual(res.n_sin_eur, 1)
        self.assertEqual(res.confidence, "media")            # 1/3 < 50%
        self.assertEqual(res.obligacion_vencida_eur, 300.0)


class MultiplicadorTests(unittest.TestCase):
    def _mult(self, rows, flows):
        return compute_debt_obligation(
            rows, flows, [M2025_01], companies=["C1"]
        )[0].multiplicador_deuda

    def test_10_sin_vencidas_es_exactamente_1(self):
        flows = [flow(M2025_01, pagos=1000.0)]
        self.assertEqual(self._mult([], flows), 1.0)
        self.assertEqual(self._mult([pay(status="paid", payment_date=date(2025, 1, 5))],
                                    flows), 1.0)
        # sin magnitud de normalizacion tampoco hay penalizacion si no hay vencidas
        self.assertEqual(self._mult([], []), 1.0)

    def test_11_monotono_en_importe(self):
        flows = [flow(M2025_01, pagos=10000.0)]
        m100 = self._mult([pay(amount_eur=100.0)], flows)
        m200 = self._mult([pay(amount_eur=200.0)], flows)
        self.assertLessEqual(m200, m100)
        self.assertAlmostEqual(m100, 1.0 - 100.0 / 10000.0)
        self.assertAlmostEqual(m200, 1.0 - 200.0 / 10000.0)

    def test_12_monotono_en_antiguedad(self):
        flows = [flow(M2025_01, pagos=10000.0)]
        joven = self._mult([pay(due_date=date(2025, 1, 20), amount_eur=100.0)], flows)
        vieja = self._mult([pay(due_date=date(2024, 6, 1), amount_eur=100.0)], flows)
        self.assertLessEqual(vieja, joven)
        self.assertAlmostEqual(joven, 1.0 - BUCKET_WEIGHTS[0] * 100.0 / 10000.0)
        self.assertAlmostEqual(vieja, 1.0 - BUCKET_WEIGHTS[4] * 100.0 / 10000.0)

    def test_13_saturacion_en_la_cota_minima(self):
        flows = [flow(M2025_01, pagos=1000.0)]
        mult = self._mult([pay(due_date=date(2024, 6, 1), amount_eur=10_000_000.0)], flows)
        self.assertEqual(mult, MULTIPLICADOR_MINIMO)

    def test_14_t6_entre_0_y_1(self):
        flows = [flow(M2025_01, pagos=500.0, servicio=500.0)]
        mult = self._mult([pay(amount_eur=100.0, due_date=date(2025, 1, 20))], flows)
        self.assertGreaterEqual(mult, MULTIPLICADOR_MINIMO)
        self.assertLessEqual(mult, 1.0)

    def test_15_normalizacion_fallback_servicio_esperado(self):
        # sin T6 en la ventana (los meses con servicio positivo son mas
        # antiguos que la ventana movil) pero con historia: magnitud =
        # 6 x mediana(100,200,300) = 1200
        flows = [
            flow(date(2024, 1, 1), servicio=100.0),
            flow(date(2024, 2, 1), servicio=200.0),
            flow(date(2024, 3, 1), servicio=300.0),
            flow(M2025_01, servicio=0.0),
        ]
        res = compute_debt_obligation([pay(amount_eur=120.0, due_date=date(2025, 1, 20))],
                                      flows, [M2025_01])[0]
        self.assertEqual(res.magnitud_normalizacion_eur, 1200.0)
        self.assertEqual(res.fuente_normalizacion, "servicio_esperado_x6")
        self.assertAlmostEqual(res.multiplicador_deuda, 1.0 - 120.0 / 1200.0)

    def test_16_sin_magnitud_el_multiplicador_es_null(self):
        res = compute_debt_obligation([pay(amount_eur=100.0)], [], [M2025_01])[0]
        self.assertIsNone(res.magnitud_normalizacion_eur)
        self.assertIsNone(res.multiplicador_deuda)
        self.assertEqual(res.confidence, "baja")
        self.assertEqual(res.reason, "sin_magnitud_normalizacion")


class DeficitTests(unittest.TestCase):
    def test_17_deficit_con_historia_suficiente(self):
        flows = [
            flow(M2024_10, servicio=100.0),
            flow(M2024_11, servicio=200.0),
            flow(M2024_12, servicio=300.0),
            flow(M2025_01, servicio=0.0),
        ]
        res = compute_debt_obligation([], flows, [M2025_01])[0]
        self.assertEqual(res.servicio_esperado_eur, 200.0)
        self.assertEqual(res.servicio_observado_eur, 0.0)
        self.assertEqual(res.deficit_servicio_eur, 200.0)
        self.assertEqual(res.obligacion_no_atendida_eur, 200.0)

    def test_18_deficit_requiere_tres_meses_previos(self):
        flows = [flow(M2024_11, servicio=100.0), flow(M2024_12, servicio=200.0),
                 flow(M2025_01, servicio=0.0)]
        res = compute_debt_obligation([], flows, [M2025_01])[0]
        self.assertIsNone(res.servicio_esperado_eur)
        self.assertIsNone(res.deficit_servicio_eur)

    def test_19_deficit_no_mira_el_futuro(self):
        flows = [
            flow(M2024_10, servicio=100.0),
            flow(M2024_11, servicio=200.0),
            flow(M2024_12, servicio=300.0),
            flow(M2025_01, servicio=0.0),
            flow(M2025_02, servicio=9999.0),
        ]
        ene = compute_debt_obligation([], flows, [M2025_01])[0]
        self.assertEqual(ene.servicio_esperado_eur, 200.0)  # no incluye Feb
        # en Feb el esperado si incluye los meses previos a Feb, no Feb mismo
        feb = compute_debt_obligation([], flows, [M2025_02])[0]
        self.assertEqual(feb.servicio_esperado_eur, 200.0)

    def test_20_servicio_observado_nulo_no_es_cero(self):
        flows = [
            flow(M2024_10, servicio=100.0),
            flow(M2024_11, servicio=200.0),
            flow(M2024_12, servicio=300.0),
            # sin fila para enero -> observado None
        ]
        res = compute_debt_obligation([], flows, [M2025_01])[0]
        self.assertEqual(res.servicio_esperado_eur, 200.0)
        self.assertIsNone(res.servicio_observado_eur)
        self.assertIsNone(res.deficit_servicio_eur)  # nulo nunca es cero

    def test_21_available_at_futuro_no_se_consume(self):
        flows = [flow(M2025_01, pagos=1000.0, available_at=date(2025, 2, 15))]
        res = compute_debt_obligation([pay(amount_eur=100.0)], flows, [M2025_01])[0]
        self.assertIsNone(res.magnitud_normalizacion_eur)  # aun no disponible


class AgregadoTests(unittest.TestCase):
    def test_22_obligacion_suma_buckets_y_deficit(self):
        flows = [
            flow(M2024_10, servicio=100.0),
            flow(M2024_11, servicio=100.0),
            flow(M2024_12, servicio=100.0),
            flow(M2025_01, servicio=0.0),
        ]
        rows = [pay(amount_eur=100.0, due_date=date(2025, 1, 20))]
        res = compute_debt_obligation(rows, flows, [M2025_01])[0]
        self.assertEqual(res.obligacion_vencida_eur, 100.0)
        self.assertEqual(res.deficit_servicio_eur, 100.0)
        self.assertEqual(res.obligacion_no_atendida_eur, 200.0)

    def test_23_sin_componentes_medibles_agregado_null(self):
        res = compute_debt_obligation([pay(amount_eur=None)], [], [M2025_01])[0]
        self.assertIsNone(res.obligacion_no_atendida_eur)

    def test_24_deficit_null_no_cuenta_como_cero(self):
        # hay vencida y deficit no calculable: el agregado es la vencida
        res = compute_debt_obligation([pay(amount_eur=100.0)], [], [M2025_01])[0]
        self.assertEqual(res.deficit_servicio_eur, None)
        self.assertEqual(res.obligacion_no_atendida_eur, 100.0)


class MotorPuroTests(unittest.TestCase):
    def test_25_no_muta_la_entrada_y_serializa_estricto(self):
        rows = [pay(status="pending"), pay(amount_eur=None, due_date=date(2025, 1, 20))]
        flows = [flow(M2025_01, pagos=1000.0)]
        snap_inv = [repr(r) for r in rows]
        snap_flow = [repr(f) for f in flows]
        res = compute_debt_obligation(rows, flows, [M2025_01, M2025_02])
        self.assertEqual([repr(r) for r in rows], snap_inv)
        self.assertEqual([repr(f) for f in flows], snap_flow)
        json.dumps([r.to_row() for r in res], allow_nan=False, default=str)

    def test_26_flujo_duplicado_falla(self):
        flows = [flow(M2025_01, pagos=100.0), flow(M2025_01, pagos=200.0)]
        with self.assertRaises(ValueError):
            compute_debt_obligation([], flows, [M2025_01])

    def test_27_assert_no_nan(self):
        malo = DebtObligationRow(company_id="X", month=M2025_01,
                                 obligacion_vencida_eur=float("nan"))
        with self.assertRaises(ValueError):
            do_mod._assert_no_nan([malo])
        ok = compute_debt_obligation([pay()], [flow(M2025_01, pagos=1000.0)], [M2025_01])
        do_mod._assert_no_nan(ok)

    def test_28_meses_cerrados_y_rejilla(self):
        ms = closed_months(date(2025, 1, 1), date(2025, 3, 1))
        self.assertEqual(ms, [M2025_01, M2025_02, M2025_03])
        res = compute_debt_obligation([], [flow(M2025_01, pagos=10.0)],
                                      [M2025_01, M2025_02], companies=["C1", "C2"])
        self.assertEqual(len(res), 4)
        self.assertEqual({r.company_id for r in res}, {"C1", "C2"})


class CliTests(unittest.TestCase):
    def test_29_cli_aborta_si_la_ruta_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "salida"
            existing.mkdir()
            rc = do_mod.main(["--output-dir", str(existing)])
            self.assertEqual(rc, 2)

    def test_30_workspace_invalido_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                do_mod._resolve_dirs(Path(tmp))


class CriterioImpagadaTests(unittest.TestCase):
    def test_31_auditoria_literal_vs_status_aware(self):
        rows = [
            pay(status="overdue", payment_date=date(2025, 1, 10), amount_eur=100.0),
            pay(status="paid", payment_date=date(2025, 1, 5), amount_eur=200.0),
            pay(status="paid", payment_date=date(2025, 2, 10), amount_eur=50.0),
            pay(status="cancel", amount_eur=999.0),
        ]
        d = do_mod._criterio_impagada(rows, date(2025, 1, 31))
        # literal: solo el pago posterior al corte (la overdue con pd=due se
        # daria por pagada, que es el defecto)
        self.assertEqual(d["literal"]["facturas"], 1)
        # status-aware: la overdue + el pago posterior al corte
        self.assertEqual(d["status_aware"]["facturas"], 2)
        self.assertEqual(d["status_aware"]["eur"], 150.0)
        self.assertEqual(set(d["reparto_status_norm_vigente"]),
                         {"overdue", "paid"})


if __name__ == "__main__":
    unittest.main()
