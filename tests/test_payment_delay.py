"""Tests de xray/payment_delay.py (fixtures temporales, sin datos reales)."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest import mock

from xray import payment_delay as pd_mod
from xray.payment_delay import (
    CohortRecord,
    CompanyMonthResult,
    InvoiceRecord,
    closed_months,
    compute_payment_delay,
)

M2025_01 = date(2025, 1, 1)          # corte: 2025-01-31
M2025_02 = date(2025, 2, 1)          # corte: 2025-02-28
M2025_03 = date(2025, 3, 1)          # corte: 2025-03-31


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


class PuntualidadPagoTests(unittest.TestCase):
    def test_1_point_in_time_vencida_en_m_pagada_en_m2(self):
        """La factura vencida en m y pagada en m+2: en mora en m, tarde en m+2."""
        # vence 2025-01-10; se paga 2025-03-15
        rows = [inv(status="paid", payment_date=date(2025, 3, 15), amount_eur=200.0)]
        res = {r.month: r for r in compute_payment_delay(rows, [M2025_01, M2025_02, M2025_03])}
        ene, feb, mar = res[M2025_01], res[M2025_02], res[M2025_03]
        # en el corte de enero esta vencida y NO pagada -> mora
        self.assertEqual(ene.en_mora_en_el_corte_eur, 200.0)
        self.assertAlmostEqual(ene.mora_ratio, 1.0)
        self.assertIsNone(ene.pagado_tarde_eur)
        self.assertIsNone(ene.pagado_tarde_eur)
        self.assertAlmostEqual(feb.mora_ratio, 1.0)  # sigue en mora en feb
        # en el corte de marzo ya consta el pago tardio
        self.assertEqual(mar.pagado_tarde_eur, 200.0)
        self.assertIsNone(mar.en_mora_en_el_corte_eur)
        self.assertAlmostEqual(mar.mora_ratio, 0.0)
        self.assertAlmostEqual(mar.retraso_medio_dias_pagado, 64.0)  # 10 ene -> 15 mar
        # la informacion posterior NO modifica el valor de m (re-evaluar enero
        # con el dataset completo da lo mismo que el corte de enero)
        res2 = {r.month: r for r in compute_payment_delay(rows, [M2025_01])}
        self.assertEqual(res2[M2025_01].mora_ratio, ene.mora_ratio)
        self.assertEqual(res2[M2025_01].en_mora_en_el_corte_eur, 200.0)

    def test_2_payment_date_fuera_de_paid_es_prevista(self):
        """payment_date con status <> 'paid' se trata como NULL."""
        rows = [
            inv(status="overdue", payment_date=date(2025, 1, 9)),  # "pagaria" antes de vencer
            inv(status="pending", payment_date=date(2025, 1, 9)),
        ]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertIsNone(res.pagado_a_tiempo_eur)      # nada 'pagado a tiempo'
        self.assertEqual(res.en_mora_en_el_corte_eur, 200.0)
        self.assertAlmostEqual(res.mora_ratio, 1.0)
        self.assertIsNone(res.retraso_medio_dias_pagado)

    def test_3_cancel_no_es_mora(self):
        rows = [
            inv(status="cancel", amount_eur=300.0),
            inv(amount_eur=100.0),  # pagada a tiempo
        ]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertEqual(res.n_cancel, 1)
        self.assertEqual(res.debido_eur, 100.0)          # la cancelada no suma
        self.assertAlmostEqual(res.mora_ratio, 0.0)
        self.assertEqual(res.en_mora_en_el_corte_eur, None)

    def test_4_debido_cero_ratio_null_con_motivo(self):
        rows = [inv(status="pending", amount_eur=0.0)]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertIsNone(res.debido_eur)
        self.assertIsNone(res.mora_ratio)
        self.assertNotEqual(res.mora_ratio, 0.0)
        self.assertNotEqual(res.mora_ratio, 1.0)
        self.assertIsNotNone(res.reason)                 # motivo declarado

    def test_5_fx_ambiguous_ensancha_intervalo(self):
        rows = [
            inv(amount_eur=100.0, status="pending"),              # mora conocida
            inv(amount_eur=None, status="pending"),               # agujero
            inv(amount_eur=50.0, fx_ambiguous=True, status="pending"),  # agujero
        ]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertIsNone(res.mora_ratio)                # no hay valor unico
        self.assertAlmostEqual(res.mora_ratio_min, 100.0 / 150.0)
        self.assertAlmostEqual(res.mora_ratio_max, 1.0)  # los agujeros podrian ser mora
        self.assertLess(res.mora_ratio_min, res.mora_ratio_max)
        self.assertEqual(res.n_huecos_eur, 2)
        self.assertEqual(res.confidence, "baja")  # 2 agujeros de 3 facturas

    def test_6_sin_cartera_observada(self):
        res = compute_payment_delay([], [M2025_01], companies=["C1"])[0]
        self.assertEqual(res.reason, "sin_cartera_observada")
        self.assertIsNone(res.mora_ratio)
        self.assertIsNone(res.debido_eur)
        self.assertEqual(res.confidence, "ninguna")

    def test_7_cohorte_no_disponible_en_el_corte(self):
        # cohorte de enero con available_at 2025-03-02: no consumible en feb
        rows = [inv(flow_side="inflow", status="pending", amount_eur=80.0)]
        cohorts = [coh(30, available=date(2025, 3, 2), pct=0.7)]
        res = {r.month: r for r in compute_payment_delay(rows, [M2025_01, M2025_02], cohorts)}
        feb = res[M2025_02]
        self.assertIsNone(feb.coh_pct_cobrado_30d)
        self.assertIn("coh_30d_no_disponible_en_el_corte", feb.flags)
        self.assertIn("cohorte_no_disponible_en_el_corte", feb.flags)
        mar = compute_payment_delay(rows, [M2025_03], cohorts)[0]
        self.assertAlmostEqual(mar.coh_pct_cobrado_30d, 0.7)  # ya disponible
        self.assertNotIn("coh_30d_no_disponible_en_el_corte", mar.flags)

    def test_8_due_nulo_va_a_vencimiento_desconocido(self):
        rows = [
            inv(due_date=None, status="paid", payment_date=date(2025, 1, 5)),
            inv(amount_eur=100.0, status="pending"),
        ]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertEqual(res.n_vencimiento_desconocido, 1)
        self.assertEqual(res.debido_eur, 100.0)          # la sin due no entra
        self.assertAlmostEqual(res.mora_ratio, 1.0)

    def test_9_invariancia_a_escala_monetaria(self):
        rows = [
            inv(status="pending", amount_eur=100.0),
            inv(amount_eur=300.0, payment_date=date(2025, 1, 20)),  # tarde
        ]
        escala = [1000.0]
        rows_x = [
            inv(status=r.status, due_date=r.due_date, payment_date=r.payment_date,
                amount_eur=r.amount_eur * escala[0], fx_ambiguous=r.fx_ambiguous)
            for r in rows
        ]
        a = compute_payment_delay(rows, [M2025_01])[0]
        b = compute_payment_delay(rows_x, [M2025_01])[0]
        self.assertAlmostEqual(a.mora_ratio, b.mora_ratio)
        self.assertAlmostEqual(a.retraso_medio_dias_pagado, b.retraso_medio_dias_pagado)
        self.assertAlmostEqual(b.debido_eur, a.debido_eur * 1000.0)

    def test_10_motor_puro_no_muta_y_serializacion_estricta(self):
        rows = [
            inv(status="pending"),
            inv(status="paid", payment_date=date(2025, 2, 5), amount_eur=float("nan")),
        ]
        snapshot_repr = [repr(r) for r in rows]
        res = compute_payment_delay(rows, [M2025_01, M2025_02])
        self.assertEqual([repr(r) for r in rows], snapshot_repr)  # no muta (nan!=nan)
        # un importe no finito es un agujero: nunca entra en las sumas
        ene = res[0]
        self.assertEqual(ene.n_huecos_eur, 1)
        self.assertIsNone(ene.mora_ratio)
        # serializacion estricta: nan/inf prohibidos
        malo = CompanyMonthResult(company_id="X", month=M2025_01, mora_ratio=float("nan"))
        with self.assertRaises(ValueError):
            pd_mod._assert_no_nan([malo])
        ok = compute_payment_delay([inv()], [M2025_01])
        json.dumps([r.to_row() for r in ok], allow_nan=False, default=str)

    def test_10b_confidence_determinista(self):
        base = [inv(amount_eur=100.0)]
        self.assertEqual(compute_payment_delay(base, [M2025_01])[0].confidence, "alta")
        # 1 hueco de 3 facturas en cartera -> media
        mix = base + [inv(amount_eur=None), inv(amount_eur=40.0, due_date=date(2025, 1, 20))]
        self.assertEqual(compute_payment_delay(mix, [M2025_01])[0].confidence, "media")
        # 2 huecos de 3 -> baja
        mix2 = base + [inv(amount_eur=None), inv(amount_eur=None, due_date=date(2025, 1, 20))]
        self.assertEqual(compute_payment_delay(mix2, [M2025_01])[0].confidence, "baja")

    def test_10c_meses_cerrados_y_corte_fin_de_mes(self):
        ms = closed_months(date(2025, 1, 1), date(2025, 3, 1))
        self.assertEqual(ms, [M2025_01, M2025_02, M2025_03])
        # vence el 31 de enero: exigible en el corte de enero (fin de mes)
        rows = [inv(due_date=date(2025, 1, 31), status="pending")]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertEqual(res.debido_eur, 100.0)


class CliTests(unittest.TestCase):
    """Tests 11 y 12 del spec: abort en ruta existente e integridad de datos."""

    def test_11_cli_aborta_si_la_ruta_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "salida"
            existing.mkdir()
            rc = pd_mod.main([str(existing)])
            self.assertEqual(rc, 2)

    def test_12_cli_no_altera_los_parquet(self):
        from xray import paths

        def hashes():
            out = {}
            for d in (paths.CLEAN_DIR, paths.INTERIM_DIR, paths.MARTS_DIR):
                for p in sorted(Path(d).glob("*.parquet")):
                    out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
            return out

        before = hashes()
        with tempfile.TemporaryDirectory() as tmp:
            rc = pd_mod.main([str(Path(tmp) / "pd_test")])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "pd_test" / "payment_delay_monthly.parquet").exists())
            coverage = json.loads((Path(tmp) / "pd_test" / "coverage.json").read_text())
            self.assertIn("limitacion_residual", coverage)
            self.assertIn("mora_ratio_calculable", coverage)
        self.assertEqual(hashes(), before)


class PureHelpersTests(unittest.TestCase):
    def test_cobro_side_independiente(self):
        rows = [
            inv(flow_side="inflow", status="pending", amount_eur=100.0),
            inv(flow_side="outflow", amount_eur=50.0),
        ]
        res = compute_payment_delay(rows, [M2025_01])[0]
        self.assertAlmostEqual(res.cobro_mora_ratio, 1.0)
        self.assertAlmostEqual(res.mora_ratio, 0.0)
        self.assertEqual(res.cobro_debido_eur, 100.0)
        self.assertEqual(res.debido_eur, 50.0)

    def test_resultado_es_dataclass_congelado(self):
        r = CompanyMonthResult(company_id="C", month=M2025_01)
        with self.assertRaises(Exception):
            r.mora_ratio = 0.5  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
