"""Tests de xray/fx_exposure.py (F2: exposicion en divisa de cobros).

Fixtures temporales para el motor puro + un test de reproduccion contra la
fuente real (se salta si no hay clean/invoices.parquet). No define
XRAY_WORKSPACE y no toca tipos de cambio ni el indice FX (F1/F3).
"""

import json
import unittest
from datetime import date
from pathlib import Path

from xray import fx_exposure as fx
from xray import paths
from xray.fx_exposure import (
    CompanyMonthFx,
    CurrencyExposure,
    FxInvoiceRecord,
    build_census,
    closed_months,
    compute_fx_exposure,
)

M2025_01 = date(2025, 1, 1)  # corte 2025-01-31
M2025_02 = date(2025, 2, 1)
M2025_03 = date(2025, 3, 1)


def inv(**kw) -> FxInvoiceRecord:
    defaults = dict(
        company_id="C1",
        currency="EUR",
        status="overdue",
        issuance_date=date(2025, 1, 1),
        due_date=date(2025, 1, 20),
        payment_date=None,
        amount=100.0,
        amount_eur=100.0,
        amount_eur_source="identity",
        fx_ambiguous=False,
    )
    defaults.update(kw)
    return FxInvoiceRecord(**defaults)


def by_month(rows, months):
    return {r.month: r for r in rows if r.month in months}


class PointInTimeTests(unittest.TestCase):
    def test_no_fuga_pagada_despues_del_corte_sigue_viva(self):
        """Una factura pagada DESPUES del corte sigue viva en el corte."""
        rows = [
            inv(
                status="paid",
                payment_date=date(2025, 3, 15),
                amount=200.0,
                amount_eur=200.0,
            )
        ]
        res = by_month(
            compute_fx_exposure(rows, [M2025_01, M2025_02, M2025_03]),
            [M2025_01, M2025_02, M2025_03],
        )
        self.assertEqual(res[M2025_01].n_facturas_vivas, 1)
        self.assertEqual(res[M2025_02].n_facturas_vivas, 1)
        self.assertEqual(res[M2025_03].n_facturas_vivas, 0)
        # Reevaluar el corte de enero con el dataset completo no cambia enero.
        solo_ene = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(
            solo_ene.importe_vivo_eur_conocido,
            res[M2025_01].importe_vivo_eur_conocido,
        )

    def test_no_fuga_emitida_despues_del_corte_no_existe(self):
        rows = [inv(issuance_date=date(2025, 2, 10))]
        ene = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(ene.n_facturas_vivas, 0)
        self.assertEqual(ene.confidence, "ninguna")
        feb = compute_fx_exposure(rows, [M2025_02])[0]
        self.assertEqual(feb.n_facturas_vivas, 1)

    def test_status_aware_overdue_con_payment_date_prevista(self):
        """El test que evita el error ya cometido: overdue con
        payment_date_ok = due_date_ok anterior al corte sigue VIVA.

        El criterio LITERAL (payment_date <= corte => pagada) la borraria.
        """
        rows = [
            inv(
                status="overdue",
                issuance_date=date(2025, 1, 1),
                due_date=date(2025, 1, 10),
                payment_date=date(2025, 1, 10),  # fecha PREVISTA, no real
                amount=100.0,
                amount_eur=100.0,
            )
        ]
        ene = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(ene.n_facturas_vivas, 1)
        self.assertEqual(ene.importe_vivo_eur_conocido, 100.0)
        # misma factura, ya con pago REAL anterior al corte: no viva
        pagada = inv(
            status="paid",
            payment_date=date(2025, 1, 10),
            amount=100.0,
            amount_eur=100.0,
        )
        self.assertEqual(
            compute_fx_exposure([pagada], [M2025_01])[0].n_facturas_vivas, 0
        )

    def test_paid_con_payment_date_nulo_sigue_viva(self):
        rows = [inv(status="paid", payment_date=None)]
        self.assertEqual(
            compute_fx_exposure(rows, [M2025_01])[0].n_facturas_vivas, 1
        )

    def test_cancel_no_es_cartera(self):
        rows = [inv(status="cancel")]
        ene = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(ene.n_facturas_vivas, 0)
        self.assertEqual(ene.n_cancel_excluidas, 1)


class ComposicionDivisaTests(unittest.TestCase):
    def test_eur_identity_y_usd_reportado(self):
        rows = [
            inv(amount=100.0, amount_eur=100.0, amount_eur_source="identity"),
            inv(
                currency="USD",
                amount=200.0,
                amount_eur=172.4,
                amount_eur_source="reported",
            ),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.n_facturas_vivas, 2)
        self.assertEqual(r.n_valorables, 2)
        self.assertEqual(r.n_no_valorables, 0)
        self.assertAlmostEqual(r.importe_vivo_eur_eur, 100.0)
        self.assertAlmostEqual(r.importe_vivo_no_eur_valorable_eur, 172.4)
        self.assertAlmostEqual(r.importe_vivo_eur_conocido, 272.4)
        self.assertEqual(r.n_divisas, 2)
        usd = next(c for c in r.por_divisa if c.currency == "USD")
        self.assertEqual(usd.n_eur_reported, 1)
        self.assertEqual(usd.importe_original, 200.0)

    def test_no_valorable_no_es_cero(self):
        rows = [
            inv(currency="USD", amount=500.0, amount_eur=None,
                amount_eur_source="unknown"),
            inv(amount=100.0, amount_eur=100.0),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.n_facturas_vivas, 2)
        self.assertEqual(r.n_no_valorables, 1)
        self.assertEqual(r.n_valorables, 1)
        self.assertAlmostEqual(r.importe_vivo_eur_conocido, 100.0)
        self.assertIn("USD", r.divisas_no_valorables)
        usd = next(c for c in r.por_divisa if c.currency == "USD")
        self.assertEqual(usd.n_no_valorable, 1)
        self.assertAlmostEqual(usd.importe_no_valorable_original, 500.0)
        self.assertAlmostEqual(usd.importe_eur, 0.0)

    def test_fx_ambiguous_no_eur_es_no_valorable_pero_eur_identidad_no(self):
        rows = [
            inv(currency="GBP", amount=300.0, amount_eur=250.0,
                amount_eur_source="reported", fx_ambiguous=True),
            inv(amount=100.0, amount_eur=100.0, currency="EUR",
                fx_ambiguous=True),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        gbp = next(c for c in r.por_divisa if c.currency == "GBP")
        self.assertEqual(gbp.n_no_valorable, 1)
        self.assertEqual(gbp.n_fx_ambiguous, 1)
        # la identidad EUR no se invalida por fx_ambiguous
        self.assertEqual(r.n_no_valorables, 1)
        self.assertAlmostEqual(r.importe_vivo_eur_eur, 100.0)

    def test_divisa_desconocida_tiene_bucket_propio(self):
        rows = [
            inv(currency=None, amount=50.0, amount_eur=50.0,
                amount_eur_source="reported"),
            inv(amount=100.0, amount_eur=100.0),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.n_divisa_desconocida, 1)
        self.assertEqual(r.n_divisas, 1)  # solo EUR
        self.assertIn("divisa_desconocida_en_el_corte", r.reasons)
        self.assertEqual(r.confidence, "media")  # deduce confianza, cap media


class ConfianzaTests(unittest.TestCase):
    def test_sin_cartera(self):
        r = compute_fx_exposure([], [M2025_01], companies=["C1"])[0]
        self.assertEqual(r.confidence, "ninguna")
        self.assertIsNone(r.importe_vivo_eur_conocido)
        self.assertIn("sin_cartera_viva_de_cobros", r.reasons)

    def test_alta_cartera_gruesa_sin_huecos(self):
        rows = [
            inv(amount=5000.0, amount_eur=5000.0),
            inv(amount=5000.0, amount_eur=5000.0),
            inv(amount=5000.0, amount_eur=5000.0),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.confidence, "alta")
        self.assertIn("solo_eur", r.flags)

    def test_baja_si_mayoria_no_valorable(self):
        rows = [
            inv(currency="USD", amount=10.0, amount_eur=None,
                amount_eur_source="unknown"),
            inv(currency="USD", amount=10.0, amount_eur=None,
                amount_eur_source="unknown"),
            inv(amount=3000.0, amount_eur=3000.0),
        ]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.confidence, "baja")

    def test_media_si_cartera_fina(self):
        rows = [inv(amount=100.0, amount_eur=100.0)]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        self.assertEqual(r.confidence, "media")
        self.assertIn("cartera_de_cobros_fina", r.reasons)


class RejillaTests(unittest.TestCase):
    def test_rejilla_completa_incluye_empresas_sin_facturas(self):
        rows = [inv(company_id="C1")]
        res = compute_fx_exposure(rows, [M2025_01, M2025_02],
                                  companies=["C1", "C2"])
        self.assertEqual(len(res), 4)
        c2 = [r for r in res if r.company_id == "C2"]
        self.assertEqual(len(c2), 2)
        self.assertTrue(all(r.confidence == "ninguna" for r in c2))

    def test_closed_months_cubre_24_meses(self):
        months = closed_months()
        self.assertEqual(len(months), 24)
        self.assertEqual(months[0], date(2024, 9, 1))
        self.assertEqual(months[-1], date(2026, 8, 1))

    def test_to_row_serializa_por_divisa_y_listas(self):
        rows = [inv(currency="USD", amount=10.0, amount_eur=None,
                    amount_eur_source="unknown")]
        r = compute_fx_exposure(rows, [M2025_01])[0]
        row = r.to_row()
        comp = json.loads(row["por_divisa"])
        self.assertEqual(comp[0]["divisa"], "USD")
        self.assertEqual(row["divisas_no_valorables"], ["USD"])
        self.assertIsInstance(row["reasons"], list)
        self.assertIsNone(row["importe_vivo_eur_conocido"])


class CensoTests(unittest.TestCase):
    def test_censo_cruza_evaluables(self):
        rows = [
            inv(company_id="C1", currency="USD", amount=10.0, amount_eur=None,
                amount_eur_source="unknown"),
            inv(company_id="C2", amount=100.0, amount_eur=100.0),
        ]
        res = compute_fx_exposure(rows, [M2025_01], companies=["C1", "C2"])
        cen = build_census(res, [M2025_01], 2, evaluables=["C1", "C2"])
        self.assertEqual(cen["filas"], 2)
        cruce = cen["cruce_evaluables_v4"]
        self.assertEqual(cruce["evaluables_v4"], 2)
        self.assertEqual(cruce["evaluables_con_exposicion_no_valorable"], 1)
        self.assertEqual(cruce["evaluables_con_exposicion_no_eur"], 0)
        # se serializa sin nan/inf
        json.dumps(cen, allow_nan=False)

    def test_censo_no_eur_valorable_cruza(self):
        rows = [
            inv(company_id="C1", currency="USD", amount=10.0, amount_eur=8.0,
                amount_eur_source="reported"),
        ]
        res = compute_fx_exposure(rows, [M2025_01], companies=["C1"])
        cen = build_census(res, [M2025_01], 1, evaluables=["C1"])
        self.assertEqual(
            cen["cruce_evaluables_v4"]["evaluables_con_exposicion_no_eur"], 1
        )
        self.assertEqual(
            cen["cruce_evaluables_v4"]["evaluables_con_exposicion_no_valorable"],
            0,
        )


DATA_OK = (paths.CLEAN_DIR / "invoices.parquet").exists()


@unittest.skipUnless(DATA_OK, "sin clean/invoices.parquet")
class CensoReproduceFuenteTests(unittest.TestCase):
    """El censo reproduce contra la fuente (ultimo corte cerrado)."""

    def test_ultimo_corte_reproduce_en_sql(self):
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            invoices, companies = fx._load_inputs(paths.CLEAN_DIR, con)
            months = closed_months()
            corte = months[-1]
            res = compute_fx_exposure(invoices, [corte], companies)
            month_end = fx._month_end(corte)
            sql = f"""
                WITH v AS (
                    SELECT currency_norm, amount, amount_eur,
                           coalesce(fx_ambiguous, FALSE) AS fx
                    FROM read_parquet('{paths.CLEAN_DIR / "invoices.parquet"}')
                    WHERE document_type_norm = 'invoice'
                      AND flow_side = 'inflow'
                      AND status_norm <> 'cancel'
                      AND cast(issuance_date_ok AS DATE) <= DATE '{month_end}'
                      AND NOT (status_norm = 'paid'
                               AND payment_date_ok IS NOT NULL
                               AND cast(payment_date_ok AS DATE) <= DATE '{month_end}')
                )
                SELECT
                    count(*) AS n_vivas,
                    sum(CASE WHEN amount_eur IS NOT NULL
                              AND NOT (fx AND currency_norm <> 'EUR')
                             THEN 1 ELSE 0 END) AS n_valorables
                FROM v
            """
            n_vivas, n_val = con.execute(sql).fetchone()
        finally:
            con.close()

        self.assertEqual(sum(r.n_facturas_vivas for r in res), n_vivas)
        self.assertEqual(sum(r.n_valorables for r in res), n_val)
        self.assertEqual(
            sum(r.n_facturas_vivas for r in res),
            sum(r.n_valorables + r.n_no_valorables for r in res),
        )


if __name__ == "__main__":
    unittest.main()
