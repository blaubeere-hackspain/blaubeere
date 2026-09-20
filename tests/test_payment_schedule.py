"""Tests del calendario point-in-time de PAGOS (xray/payment_schedule.py).

Incluye los dos tests obligatorios del encargo:
  (a) NO-FUGA: las cifras de un corte no cambian si se eliminan o alteran
      facturas y pagos posteriores a ese corte.
  (b) STATUS-AWARE: una factura 'overdue' con payment_date_ok = due_date_ok
      <= corte sigue contando como VIVA.

Se ejecuta con unittest (convencion del repo):
    PYTHONPATH=. .venv/bin/python -B -m unittest tests.test_payment_schedule -v
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import pandas as pd

from xray import payment_schedule as ps

CUT = date(2026, 6, 30)
MADUREZ = ps.MADUREZ_DIAS


def _mk(company, issuance, due, payment, amount, status):
    return {
        "company_id": company,
        "issuance_date_ok": issuance,
        "due_date_ok": due,
        "payment_date_ok": payment,
        "amount_eur": amount,
        "status_norm": status,
        "flow_side": "outflow",
        "fx_ambiguous": False,
    }


def _mature_history():
    """100 facturas maduras al corte con curva de pago conocida."""
    rows = []
    due = "2025-01-01"
    for i in range(60):
        rows.append(_mk(f"E{i % 3}", due, due, due, -100.0, "paid"))  # delay 0
    for i in range(20):
        rows.append(_mk(f"E{i % 3}", due, due, "2025-01-11", -100.0, "paid"))  # 10
    for i in range(10):
        rows.append(_mk(f"E{i % 3}", due, due, "2025-02-15", -100.0, "paid"))  # 45
    for i in range(5):
        rows.append(_mk(f"E{i % 3}", due, due, "2025-05-01", -100.0, "paid"))  # 120
    for i in range(5):
        rows.append(_mk(f"E{i % 3}", due, due, due, -100.0, "overdue"))  # impaga
    return rows


def _scenario():
    rows = _mature_history()
    rows += [
        # viva vencida 10 dias
        _mk("L1", "2026-01-01", "2026-06-20", "2026-06-20", -1000.0, "overdue"),
        # viva aun no vencida
        _mk("L2", "2026-06-01", "2026-08-15", None, -2000.0, "pending"),
        # viva sin fecha de vencimiento (importe conocido)
        _mk("L2", "2026-06-25", None, None, -500.0, "pending"),
        # viva con importe desconocido
        _mk("L3", "2026-06-25", "2026-07-05", None, None, "pending"),
        # viva pagada DESPUES del corte (sigue viva en el corte)
        _mk("L4", "2026-03-01", "2026-06-01", "2026-09-01", -700.0, "paid"),
        # factura POSTERIOR al corte (no debe influir)
        _mk("L5", "2026-07-15", "2026-08-01", None, -99999.0, "pending"),
    ]
    return ps.normalize_records(pd.DataFrame(rows))


def _run(records, cutoffs=(CUT,)):
    with mock.patch.object(ps, "MIN_MUESTRA_PROBABILIDAD", 2):
        return ps.compute_payment_schedule(records, list(cutoffs))


class TestStatusAware(unittest.TestCase):
    def test_overdue_con_payment_igual_a_due_sigue_viva(self):
        """(b) overdue con payment_date_ok = due_date_ok <= corte es VIVA."""
        rows = [
            _mk("A", "2026-01-01", "2026-06-01", "2026-06-01", -1000.0, "overdue"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        cal, hor, meta = _run(rec)
        censo = meta["cortes"][0]
        self.assertEqual(censo["n_vivas"], 1)
        self.assertEqual(censo["n_vencidas"], 1)
        # una lectura ingenua por payment_date_ok la daria por pagada
        self.assertEqual(censo["importe_esperado_eur"] < 0, True)

    def test_paid_con_payment_previo_no_esta_viva(self):
        rows = [
            _mk("A", "2026-01-01", "2026-06-01", "2026-06-01", -1000.0, "paid"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        _, _, meta = _run(rec)
        self.assertEqual(meta["cortes"][0]["n_vivas"], 0)

    def test_cancel_no_esta_viva(self):
        rows = [
            _mk("A", "2026-01-01", "2026-06-01", None, -1000.0, "cancel"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        _, _, meta = _run(rec)
        self.assertEqual(meta["cortes"][0]["n_vivas"], 0)


class TestNoLeak(unittest.TestCase):
    def _cut_slice(self, cal, hor, meta):
        corte = pd.Timestamp(CUT)
        c = cal[cal["corte"] == corte].reset_index(drop=True)
        h = hor[hor["corte"] == corte].reset_index(drop=True)
        censo = next(m for m in meta["cortes"] if m["corte"] == CUT.isoformat())
        return c, h, censo

    def test_cifras_del_corte_no_cambian_con_futuro_alterado(self):
        """(a) NO-FUGA: borrar facturas futuras y alterar pagos > corte."""
        rec = _scenario()
        base = _run(rec)

        mut = rec[~(rec["issuance_date_ok"] > pd.Timestamp(CUT))].copy()
        # alterar todos los pagos posteriores al corte
        post = mut["payment_date_ok"] > pd.Timestamp(CUT)
        mut.loc[post, "payment_date_ok"] = mut.loc[post, "payment_date_ok"] + pd.Timedelta(days=500)
        # anadir facturas futuras basura
        extra = ps.normalize_records(pd.DataFrame([
            _mk("ZX", "2026-12-01", "2026-12-15", None, -123456.0, "pending"),
            _mk("ZY", "2027-01-01", "2027-01-01", "2027-01-01", -50.0, "paid"),
        ]))
        mut = ps.normalize_records(pd.concat([mut, extra], ignore_index=True))
        alt = _run(mut)

        b = self._cut_slice(*base)
        a = self._cut_slice(*alt)
        pd.testing.assert_frame_equal(b[0], a[0])
        pd.testing.assert_frame_equal(b[1], a[1])
        self.assertEqual(b[2], a[2])

    def test_no_muta_la_entrada(self):
        rec = _scenario()
        before = rec.copy(deep=True)
        _run(rec)
        pd.testing.assert_frame_equal(rec, before)


class TestValoracion(unittest.TestCase):
    def test_importe_nulo_no_es_cero(self):
        rows = [
            _mk("A", "2026-06-01", "2026-07-01", None, None, "pending"),
            _mk("A", "2026-06-01", "2026-07-01", None, -100.0, "pending"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        cal, hor, meta = _run(rec)
        censo = meta["cortes"][0]
        self.assertEqual(censo["n_vivas"], 2)
        self.assertEqual(censo["n_sin_importe"], 1)
        # el importe desconocido no se imputa como cero: solo entra la conocida
        last = hor[hor["h"] == 30]
        self.assertAlmostEqual(float(last["importe_esperado_eur"].sum()), -100.0)
        self.assertEqual(int(last["n_facturas_valoradas"].sum()), 1)
        self.assertEqual(int(last["n_no_valorable_sin_importe"].sum()), 1)

    def test_sin_fecha_vencimiento_publica_importe_conocido(self):
        rows = [
            _mk("A", "2026-06-01", None, None, -1000.0, "pending"),
            _mk("A", "2026-06-01", "2026-07-01", None, -200.0, "pending"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        _, hor, meta = _run(rec)
        last = hor[hor["h"] == 30]
        self.assertEqual(int(last["n_no_valorable_sin_fecha"].sum()), 1)
        self.assertAlmostEqual(float(last["importe_no_valorable_eur"].sum()), -1000.0)
        self.assertAlmostEqual(float(last["importe_esperado_eur"].sum()), -200.0)

    def test_fx_ambiguous_no_es_valorable(self):
        rows = [
            _mk("A", "2026-06-01", "2026-07-01", None, -100.0, "pending"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        rec["fx_ambiguous"] = True
        _, _, meta = _run(rec)
        self.assertEqual(meta["cortes"][0]["n_sin_importe"], 1)


class TestSignoYHorizonte(unittest.TestCase):
    def test_signo_negativo_pagos(self):
        rows = [_mk("A", "2026-06-01", "2026-07-05", None, -100.0, "pending")]
        rec = ps.normalize_records(pd.DataFrame(rows))
        cal, hor, _ = _run(rec)
        self.assertTrue((cal[cal["corte"] == pd.Timestamp(CUT)]["importe_esperado_eur"] < 0).all())
        self.assertTrue((hor["importe_esperado_eur"] <= 0).all())

    def test_intervalo_abierto_izquierda_cerrado_derecha(self):
        """Un pago en corte+h entra; uno en corte (floor) entra en el primer dia."""
        rows = [
            _mk("A", "2026-06-01", "2026-07-30", None, -100.0, "pending"),  # C+30
            _mk("B", "2026-06-01", "2026-06-01", None, -100.0, "overdue"),  # vencida -> C+1
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        cal, hor, _ = _run(rec)
        corte = pd.Timestamp(CUT)
        h30 = hor[(hor["corte"] == corte) & (hor["h"] == 30)]
        h60 = hor[(hor["corte"] == corte) & (hor["h"] == 60)]
        # ambos caen dentro de 30 (el vencido floored a C+1, el otro justo en C+30)
        self.assertAlmostEqual(float(h30["importe_esperado_eur"].sum()), -200.0)
        self.assertAlmostEqual(float(h60["importe_esperado_eur"].sum()), -200.0)
        dias = set(cal[cal["corte"] == corte]["dia"])
        self.assertIn(pd.Timestamp(CUT) + pd.Timedelta(days=30), dias)

    def test_vencida_se_acota_a_corte_mas_uno(self):
        rows = [_mk("A", "2025-01-01", "2025-06-01", "2025-06-01", -100.0, "overdue")]
        rec = ps.normalize_records(pd.DataFrame(rows))
        cal, _, _ = _run(rec)
        dia = cal[cal["corte"] == pd.Timestamp(CUT)]["dia"].iloc[0]
        self.assertEqual(dia, pd.Timestamp(CUT) + pd.Timedelta(days=1))


class TestEstimadores(unittest.TestCase):
    def test_probabilidad_decreciente_con_antiguedad(self):
        rec = _scenario()
        with mock.patch.object(ps, "MIN_MUESTRA_PROBABILIDAD", 2):
            tabla = ps._probability_table(rec, CUT)["tabla"]
        ps_vals = [tabla[k]["p"] for k in ps.BUCKET_LABELS]
        self.assertEqual(ps_vals, sorted(ps_vals, reverse=True))
        self.assertGreater(ps_vals[0], ps_vals[-1])

    def test_probabilidades_calculadas_sobre_historia_madura(self):
        rec = _scenario()
        with mock.patch.object(ps, "MIN_MUESTRA_PROBABILIDAD", 2):
            tabla = ps._probability_table(rec, CUT)["tabla"]
        self.assertAlmostEqual(tabla["no_vencida"]["p"], 0.95, places=6)
        self.assertAlmostEqual(tabla["1_30"]["p"], 35 / 40, places=6)
        self.assertAlmostEqual(tabla["31_90"]["p"], 15 / 20, places=6)
        self.assertAlmostEqual(tabla["91_180"]["p"], 5 / 10, places=6)
        self.assertAlmostEqual(tabla["180_mas"]["p"], 0.0, places=6)

    def test_muestra_corta_usa_retraso_global(self):
        rows = _mature_history()
        # empresa N con solo 2 pagos (muestra corta) y una factura viva
        rows += [
            _mk("N", "2025-01-01", "2025-01-01", "2025-01-21", -100.0, "paid"),
            _mk("N", "2025-01-01", "2025-01-01", "2025-01-21", -100.0, "paid"),
            _mk("N", "2026-06-01", "2026-06-10", None, -1000.0, "overdue"),
        ]
        rec = ps.normalize_records(pd.DataFrame(rows))
        _, _, meta = _run(rec)
        censo = meta["cortes"][0]
        self.assertGreaterEqual(censo["retraso"]["empresas_muestra_corta"], 1)

    def test_sin_historia_madura_usa_prior(self):
        rows = [_mk("A", "2026-06-01", "2026-06-10", None, -100.0, "overdue")]
        rec = ps.normalize_records(pd.DataFrame(rows))
        with mock.patch.object(ps, "MIN_MUESTRA_PROBABILIDAD", 2):
            tabla = ps._probability_table(rec, CUT)["tabla"]
        self.assertEqual(tabla["1_30"]["motivo"], "probabilidad_prior_sin_historia")
        self.assertEqual(tabla["1_30"]["p"], ps.PRIOR_PROBABILIDAD)


class TestCortes(unittest.TestCase):
    def test_cortes_alineados_con_cashflow_forecast(self):
        info = ps.build_cutoffs(date(2024, 11, 30), date(2026, 8, 31))
        self.assertEqual(len(info["por_horizonte"]["30"]), 21)
        self.assertEqual(len(info["por_horizonte"]["60"]), 20)
        self.assertEqual(len(info["por_horizonte"]["90"]), 19)
        self.assertIn(date(2026, 8, 31), info["origenes"])
        self.assertTrue(info["objetivo_observable"]["2026-08-31|90"] is False)
        self.assertTrue(info["objetivo_observable"]["2026-07-31|30"] is True)


@unittest.skipUnless(
    (ps.DEFAULT_CLEAN_DIR / "invoices.parquet").exists(),
    "requiere data/clean/invoices.parquet",
)
class TestReproduccionReal(unittest.TestCase):
    def test_censo_corte_vivo_2026_08_31(self):
        rec = ps.load_records(ps.DEFAULT_CLEAN_DIR)
        cal, hor, meta = ps.compute_payment_schedule(rec, [date(2026, 8, 31)])
        censo = meta["cortes"][0]
        self.assertEqual(censo["n_vivas"], 122086)
        self.assertEqual(censo["n_empresas"], 762)
        self.assertEqual(censo["n_no_vencidas"], 25437)
        self.assertEqual(censo["n_vencidas"], 96547)
        self.assertAlmostEqual(censo["amount_eur_sum"] / 1e6, -1427.9, places=1)

    def test_no_fuga_sobre_datos_reales(self):
        """(a) refuerzo: no-fuga con datos reales en un corte historico."""
        cut = date(2026, 6, 30)
        rec = ps.load_records(ps.DEFAULT_CLEAN_DIR)
        base = ps.compute_payment_schedule(rec, [cut])

        mut = rec[~(rec["issuance_date_ok"] > pd.Timestamp(cut))].copy()
        post = mut["payment_date_ok"] > pd.Timestamp(cut)
        mut.loc[post, "payment_date_ok"] = (
            mut.loc[post, "payment_date_ok"] + pd.Timedelta(days=500))
        mut = ps.normalize_records(mut)
        alt = ps.compute_payment_schedule(mut, [cut])

        pd.testing.assert_frame_equal(base[0], alt[0])
        pd.testing.assert_frame_equal(base[1], alt[1])
        self.assertEqual(base[2]["cortes"], alt[2]["cortes"])

    def test_cli_regenera_artefactos_reproducibles(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            d1 = Path(tmp) / "a"
            d2 = Path(tmp) / "b"
            self.assertEqual(ps.main(["--output-dir", str(d1)]), 0)
            self.assertEqual(ps.main(["--output-dir", str(d2)]), 0)
            for name, reader in (
                ("calendario.parquet", pd.read_parquet),
                ("horizontes.parquet", pd.read_parquet),
            ):
                pd.testing.assert_frame_equal(reader(d1 / name), reader(d2 / name))
            self.assertEqual(
                json.loads((d1 / "censo.json").read_text()),
                json.loads((d2 / "censo.json").read_text()),
            )
            self.assertEqual(
                (d1 / "report.md").read_text(), (d2 / "report.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
