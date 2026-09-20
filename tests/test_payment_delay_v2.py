"""Tests de xray/payment_delay_v2.py (fixtures temporales, sin datos reales)."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from xray import payment_delay_v2 as pd2
from xray.payment_delay_v2 import (
    BUCKET_PESOS,
    CohortRecord,
    CompanyMonthResult,
    InvoiceRecord,
    KAPPA_EUR,
    LAMBDA_EWMA,
    PESO_MORA_COBRO,
    PESO_MORA_PAGO,
    closed_months,
    compute_payment_delay_v2,
)

M2024_11 = date(2024, 11, 1)
M2024_12 = date(2024, 12, 1)
M2025_01 = date(2025, 1, 1)
M2025_02 = date(2025, 2, 1)
M2025_03 = date(2025, 3, 1)
M2025_04 = date(2025, 4, 1)
M2025_06 = date(2025, 6, 1)


def inv(**kw) -> InvoiceRecord:
    defaults = dict(
        company_id="C1",
        flow_side="outflow",
        status="paid",
        due_date=date(2025, 1, 10),
        payment_date=date(2025, 1, 5),
        amount_eur=100.0,
        fx_ambiguous=False,
    )
    defaults.update(kw)
    return InvoiceRecord(**defaults)


def coh(horizon: int, available: date, pct: float = 0.5, cid: str = "C1",
        month: date = M2025_01) -> CohortRecord:
    return CohortRecord(
        company_id=cid, cohort_month=month, horizon_days=horizon,
        pct_cobrado=pct, available_at=available,
    )


def by_month(results):
    return {r.month: r for r in results}


class NoFugaTests(unittest.TestCase):
    def test_1_pagada_despues_del_corte_cuenta_como_vencida_en_m(self):
        """Factura vencida en enero y pagada en marzo: vencida en enero."""
        rows = [inv(status="paid", payment_date=date(2025, 3, 15),
                    due_date=date(2025, 1, 10), amount_eur=200.0)]
        res = by_month(compute_payment_delay_v2(rows, [M2025_01, M2025_02, M2025_03]))
        ene, feb, mar = res[M2025_01], res[M2025_02], res[M2025_03]
        self.assertEqual(ene.pago.exposure_eur, 200.0)
        self.assertEqual(ene.pago.overdue_eur, 200.0)
        # 21 dias -> primer bucket (peso 0,10); primer corte observable
        self.assertAlmostEqual(ene.pago.severity, 0.10)
        self.assertAlmostEqual(ene.mora_pago_robusta, 0.10)
        # en febrero sigue sin pagar al corte (pago 15-mar > 28-feb)
        self.assertEqual(feb.pago.overdue_eur, 200.0)
        # en marzo ya consta el pago: no vencida en el corte
        self.assertEqual(mar.pago.overdue_eur, 0.0)
        self.assertAlmostEqual(mar.pago.severity, 0.0)

    def test_2_due_posterior_no_cuenta_en_m(self):
        """Una factura con vencimiento posterior al corte no entra en m."""
        rows = [inv(status="pending", payment_date=None,
                    due_date=date(2025, 2, 20), amount_eur=100.0)]
        res = by_month(compute_payment_delay_v2(rows, [M2025_01, M2025_02]))
        ene, feb = res[M2025_01], res[M2025_02]
        self.assertEqual(ene.pago.n_cartera, 0)
        self.assertIsNone(ene.pago.exposure_eur)
        self.assertIsNone(ene.mora_pago_robusta)
        self.assertEqual(ene.confidence, "ninguna")
        self.assertEqual(ene.reason, "sin_cartera_observada")
        # en febrero ya es exigible
        self.assertEqual(feb.pago.exposure_eur, 100.0)
        self.assertEqual(feb.pago.overdue_eur, 100.0)
        self.assertAlmostEqual(feb.pago.severity, 0.10)

    def test_3_no_mira_meses_posteriores(self):
        """Calcular m con o sin meses futuros da el mismo resultado en m."""
        rows = [inv(status="pending", payment_date=None, amount_eur=200.0)]
        con_futuro = by_month(
            compute_payment_delay_v2(rows, [M2025_01, M2025_02, M2025_03])
        )
        solo_enero = by_month(compute_payment_delay_v2(rows, [M2025_01]))
        self.assertEqual(
            con_futuro[M2025_01].mora_pago_robusta,
            solo_enero[M2025_01].mora_pago_robusta,
        )
        self.assertEqual(
            con_futuro[M2025_01].pago.severity, solo_enero[M2025_01].pago.severity
        )


class PersistenciaTests(unittest.TestCase):
    @staticmethod
    def _serie(meses_malos: set):
        rows = []
        for m in (M2025_01, M2025_02, M2025_03, M2025_04):
            if m in meses_malos:
                rows.append(inv(company_id="C", status="pending", payment_date=None,
                                due_date=m, amount_eur=100.0))
            else:
                rows.append(inv(company_id="C", status="paid",
                                due_date=m, payment_date=m, amount_eur=100.0))
        return compute_payment_delay_v2(rows, [M2025_01, M2025_02, M2025_03, M2025_04])

    def test_4_persistencia_pico_aislado_menor_que_sostenido(self):
        aislado = self._serie({M2025_02})
        sostenido = self._serie({M2025_02, M2025_03, M2025_04})
        pico_aislado = max(r.mora_pago_robusta for r in aislado if r.mora_pago_robusta is not None)
        pico_sostenido = max(
            r.mora_pago_robusta for r in sostenido if r.mora_pago_robusta is not None
        )
        self.assertLess(pico_aislado, pico_sostenido)

    def test_5_primer_corte_observable_es_la_severidad(self):
        rows = [inv(status="pending", payment_date=None, amount_eur=100.0)]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertFalse(r.pago_shrinkage_aplicado)
        self.assertAlmostEqual(r.mora_pago_robusta, r.pago.severity)
        self.assertIn("shrinkage_pago_sin_historia_previa", r.flags)


class ShrinkageTests(unittest.TestCase):
    def test_6_cartera_fina_no_publica_1_y_queda_entre_historia_y_1(self):
        """Una sola factura vencida no recibe mora 1,0 con historia previa."""
        # mes de historia: factura pagada a tiempo (severidad 0) antes del
        # vencimiento de la factura vencida, para que esta no exista aun.
        rows = [
            inv(company_id="S", due_date=M2024_11, status="paid",
                payment_date=M2024_11, amount_eur=1.0),
            # vence 2024-12-01; en el corte de 2025-06-30 lleva 211 dias (>180)
            inv(company_id="S", due_date=M2024_12, status="pending",
                payment_date=None, amount_eur=1000.0),
        ]
        res = by_month(compute_payment_delay_v2(rows, [M2024_11, M2025_06]))
        hist = res[M2024_11]
        corte = res[M2025_06]
        self.assertTrue(corte.pago_shrinkage_aplicado)
        # la severidad bruta del corte casi es 1 (1000 vencido / 1001 expuesto)
        self.assertGreater(corte.pago.severity, 0.99)
        self.assertLess(corte.pago_severidad_shrunk, 1.0)
        self.assertLess(corte.mora_pago_robusta, 1.0)
        # estrictamente entre la historia (0) y 1,0
        self.assertEqual(hist.mora_pago_robusta, 0.0)
        self.assertGreater(corte.mora_pago_robusta, hist.mora_pago_robusta)
        self.assertLess(corte.mora_pago_robusta, 1.0)

    def test_7_kappa_y_ewma_son_las_constantes_declaradas(self):
        exposure, severity = 1000.0, 0.5
        hist = 0.1
        esperado_shrunk = (exposure * severity + KAPPA_EUR * hist) / (exposure + KAPPA_EUR)
        shrunk, aplicado, _ = pd2._shrink_severity(severity, exposure, hist, True)
        self.assertTrue(aplicado)
        self.assertAlmostEqual(shrunk, esperado_shrunk)
        self.assertAlmostEqual(LAMBDA_EWMA, 0.35)
        self.assertAlmostEqual(PESO_MORA_PAGO + PESO_MORA_COBRO, 1.0)


class MecanismosTests(unittest.TestCase):
    def test_8_buckets_ponderados_crecen_con_los_dias(self):
        reciente = [inv(status="pending", payment_date=None,
                        due_date=date(2025, 1, 20), amount_eur=100.0)]
        antiguo = [inv(status="pending", payment_date=None,
                       due_date=date(2024, 7, 1), amount_eur=100.0)]
        r_rec = compute_payment_delay_v2(reciente, [M2025_01])[0]
        r_ant = compute_payment_delay_v2(antiguo, [M2025_01])[0]
        self.assertAlmostEqual(r_rec.pago.bucket_eur[0], 100.0)
        self.assertGreater(r_ant.pago.bucket_eur[4], 0.0)
        self.assertGreater(r_ant.pago.severity, r_rec.pago.severity)
        self.assertAlmostEqual(r_ant.pago.severity, BUCKET_PESOS[4])
        self.assertAlmostEqual(r_rec.pago.severity, BUCKET_PESOS[0])

    def test_9_dos_lados_separados_y_pesos_del_indice(self):
        rows = [
            inv(company_id="D", flow_side="outflow", status="pending",
                payment_date=None, amount_eur=100.0),
            inv(company_id="D", flow_side="inflow", status="pending",
                payment_date=None, amount_eur=200.0),
        ]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertEqual(r.pago.exposure_eur, 100.0)
        self.assertEqual(r.cobro.exposure_eur, 200.0)
        self.assertAlmostEqual(r.mora_pago_robusta, 0.10)
        self.assertAlmostEqual(r.mora_cobro_robusta, 0.10)
        esperado = PESO_MORA_PAGO * r.mora_pago_robusta + PESO_MORA_COBRO * r.mora_cobro_robusta
        self.assertAlmostEqual(r.mora_indice, esperado)

    def test_10_indice_renormaliza_a_un_solo_lado(self):
        rows = [inv(company_id="E", flow_side="outflow", status="pending",
                    payment_date=None, amount_eur=100.0)]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertAlmostEqual(r.mora_indice, r.mora_pago_robusta)
        self.assertIn("mora_indice_solo_pago", r.flags)

    def test_11_huecos_no_son_cero_y_degradan_confidence(self):
        rows = [
            inv(status="pending", payment_date=None, amount_eur=100.0),
            inv(status="pending", payment_date=None, amount_eur=None),
            inv(status="pending", payment_date=None, amount_eur=50.0, fx_ambiguous=True),
        ]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertEqual(r.pago.n_cartera, 3)
        self.assertEqual(r.pago.n_huecos, 2)
        self.assertEqual(r.pago.n_huecos_sin_importe, 1)
        self.assertEqual(r.pago.n_huecos_fx, 1)
        self.assertEqual(r.pago.hueco_eur, 50.0)
        # la severidad solo usa la exposicion conocida (100), agujeros fuera
        self.assertEqual(r.pago.exposure_eur, 100.0)
        self.assertAlmostEqual(r.pago.severity, 0.10)
        # 2 agujeros de 3 facturas -> baja
        self.assertEqual(r.pago.confidence, "baja")
        self.assertEqual(r.confidence, "baja")
        self.assertIn("agujeros_pago_fx_o_eur_en_el_corte", r.flags)
        # el agujero sin importe no se imputa como cero a la exposicion
        self.assertNotEqual(r.pago.exposure_eur, 150.0)

    def test_12_cancel_no_es_mora(self):
        rows = [
            inv(status="cancel", amount_eur=300.0),
            inv(status="paid", payment_date=date(2025, 1, 5), amount_eur=100.0),
        ]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertEqual(r.n_cancel, 1)
        self.assertEqual(r.pago.exposure_eur, 100.0)
        self.assertEqual(r.pago.overdue_eur, 0.0)
        self.assertAlmostEqual(r.pago.severity, 0.0)

    def test_13_sin_cartera_observada(self):
        r = compute_payment_delay_v2([], [M2025_01], companies=["C1"])[0]
        self.assertEqual(r.reason, "sin_cartera_observada")
        self.assertEqual(r.confidence, "ninguna")
        self.assertIsNone(r.mora_pago_robusta)
        self.assertIsNone(r.mora_indice)

    def test_14_cohorte_no_disponible_en_el_corte(self):
        rows = [inv(flow_side="inflow", status="pending", payment_date=None,
                    amount_eur=80.0)]
        cohorts = [coh(30, available=date(2025, 3, 2), pct=0.7)]
        res = by_month(compute_payment_delay_v2(rows, [M2025_01, M2025_02], cohorts))
        feb = res[M2025_02]
        self.assertIsNone(feb.coh_pct_cobrado_30d)
        self.assertIn("coh_30d_no_disponible_en_el_corte", feb.flags)
        self.assertIn("cohorte_no_disponible_en_el_corte", feb.flags)
        mar = compute_payment_delay_v2(rows, [M2025_03], cohorts)[0]
        self.assertAlmostEqual(mar.coh_pct_cobrado_30d, 0.7)
        self.assertNotIn("coh_30d_no_disponible_en_el_corte", mar.flags)


class PurezaTests(unittest.TestCase):
    def test_15_motor_puro_no_muta_y_serializacion_estricta(self):
        rows = [
            inv(status="pending", payment_date=None),
            inv(status="paid", payment_date=date(2025, 2, 5), amount_eur=float("nan")),
        ]
        snapshot = [repr(r) for r in rows]
        results = compute_payment_delay_v2(rows, [M2025_01, M2025_02])
        self.assertEqual([repr(r) for r in rows], snapshot)
        ene = results[0]
        self.assertEqual(ene.pago.n_huecos_sin_importe, 1)
        # el importe no finito NO entra en la exposicion: severidad sobre lo conocido
        self.assertEqual(ene.pago.exposure_eur, 100.0)
        self.assertAlmostEqual(ene.pago.severity, 0.10)
        malo = CompanyMonthResult(company_id="X", month=M2025_01,
                                  mora_indice=float("nan"))
        with self.assertRaises(ValueError):
            pd2._assert_no_nan([malo])
        ok = compute_payment_delay_v2([inv()], [M2025_01])
        json.dumps([r.to_row() for r in ok], allow_nan=False, default=str)

    def test_16_meses_cerrados_y_corte_fin_de_mes(self):
        ms = closed_months(date(2025, 1, 1), date(2025, 3, 1))
        self.assertEqual(ms, [M2025_01, M2025_02, M2025_03])
        rows = [inv(due_date=date(2025, 1, 31), status="pending", payment_date=None)]
        r = compute_payment_delay_v2(rows, [M2025_01])[0]
        self.assertEqual(r.pago.exposure_eur, 100.0)


class CliTests(unittest.TestCase):
    def test_17_cli_aborta_si_la_ruta_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "salida"
            existing.mkdir()
            rc = pd2.main(["--output-dir", str(existing)])
            self.assertEqual(rc, 2)

    def test_18_cli_end_to_end_con_workspace_fixture(self):
        import pandas as pd

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "data" / "clean"
            (root / "data" / "marts").mkdir(parents=True)
            clean.mkdir(parents=True)
            pd.DataFrame(
                {
                    "company_id": ["C1"],
                    "flow_side": ["outflow"],
                    "status_norm": ["pending"],
                    "due_date_ok": [pd.Timestamp("2025-01-10")],
                    "payment_date_ok": [pd.NaT],
                    "amount_eur": [100.0],
                    "fx_ambiguous": [False],
                    "document_type_norm": ["invoice"],
                }
            ).to_parquet(clean / "invoices.parquet", index=False)
            pd.DataFrame({"company_id": ["C1"]}).to_parquet(
                clean / "companies.parquet", index=False
            )
            out = root / "out"
            rc = pd2.main(
                [
                    "--workspace", str(root),
                    "--output-dir", str(out),
                    "--v3-dir", str(root / "v3_inexistente"),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((out / "payment_delay_v2_monthly.parquet").exists())
            coverage = json.loads((out / "coverage.json").read_text())
            self.assertIn("constantes_diseno", coverage)
            self.assertIn("cobertura", coverage)
            self.assertEqual(coverage["filas"], 24)
            self.assertIn("reduccion_saturacion", coverage)


if __name__ == "__main__":
    unittest.main()
