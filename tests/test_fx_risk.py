"""Tests de xray/fx_risk.py (fixtures sinteticas, sin datos reales).

Cubren los tres criterios de hecho del dispatch:
  1. no-fuga: ninguna cotizacion posterior al corte influye en el corte;
  2. ancla de valoracion: factura en divisa conocida y fecha conocida da el EUR
     esperado con el tipo del BCE de ese dia;
  3. convencion de intervalo: empresa con mezcla opaca sale con indice puntual
     NULL y [min, max] poblado.
"""

import unittest
from datetime import date

from xray.fx_risk import (
    ECB_VENDOR_PATH,
    EXTRA_RATES_PATH,
    AssessmentFx,
    CompanyMonthFxRisk,
    EcbQuote,
    ExtraRateRow,
    ExtraRateSeries,
    FxIndexEntry,
    FxIndexTable,
    FxInvoiceRecord,
    RateSeries,
    compute_counterfactual,
    compute_fx_risk,
    load_ecb_quotes,
    load_extra_rate_series,
    month_end,
    spearman,
    verify_ecb_vendor,
)

M2025_01 = date(2025, 1, 1)
M2025_02 = date(2025, 2, 1)


def entry(vol="volatil", der="estable", comb="volatil", cob=True):
    return FxIndexEntry(vol, der, comb, cob)


def index_table(currencies=("EUR", "USD", "COP")):
    entries = {}
    for cur in currencies:
        if cur == "EUR":
            entries[(cur, M2025_01)] = entry("estable", "estable", "estable")
        elif cur == "COP":
            entries[(cur, M2025_01)] = entry(
                "hipervolatil", None, "hipervolatil", cob=False
            )
        else:
            entries[(cur, M2025_01)] = entry("volatil", "estable", "volatil")
    return FxIndexTable(entries)


def inv(**kw) -> FxInvoiceRecord:
    defaults = dict(
        operation_id="op",
        company_id="C1",
        currency="USD",
        status="pending",
        issuance_date=date(2025, 1, 10),
        due_date=date(2025, 2, 10),
        payment_date=None,
        amount=100.0,
        amount_eur=None,
        amount_eur_source="unknown",
        fx_ambiguous=False,
    )
    defaults.update(kw)
    return FxInvoiceRecord(**defaults)


def by_company(rows):
    return {r.company_id: r for r in rows if r.n_facturas_vivas > 0}


class AnclaValoracionTests(unittest.TestCase):
    def test_1_ancla_dia_conocido(self):
        """100 USD emitidos el 10-ene con tipo BCE 1,10 -> 90,909 EUR."""
        rates = RateSeries(
            [
                EcbQuote(date(2025, 1, 10), "USD", 1.10),
                EcbQuote(date(2025, 1, 31), "USD", 1.20),
            ]
        )
        rows = compute_fx_risk([inv()], [M2025_01], rates, index_table(), ["C1"])
        r = rows[0]
        self.assertEqual(r.n_facturas_vivas, 1)
        self.assertAlmostEqual(r.importe_vivo_eur_emision, 100 / 1.10)
        self.assertAlmostEqual(r.importe_vivo_eur_corte, 100 / 1.20)
        self.assertAlmostEqual(r.perdida_eur, 100 / 1.10 - 100 / 1.20)
        self.assertGreater(r.perdida_eur, 0)  # la divisa se debilito

    def test_2_fin_de_semana_usa_ultimo_habil_anterior(self):
        """Emision en sabado 11-ene: usa el tipo del viernes 10-ene."""
        rates = RateSeries(
            [
                EcbQuote(date(2025, 1, 10), "USD", 1.10),
                EcbQuote(date(2025, 1, 13), "USD", 1.30),
            ]
        )
        rows = compute_fx_risk(
            [inv(issuance_date=date(2025, 1, 11))],
            [M2025_01],
            rates,
            index_table(),
            ["C1"],
        )
        self.assertAlmostEqual(rows[0].importe_vivo_eur_emision, 100 / 1.10)

    def test_3_nunca_usa_un_tipo_posterior_a_la_emision(self):
        """Si la divisa solo cotiza despues de emitir, no se valora (nunca 0)."""
        rates = RateSeries([EcbQuote(date(2025, 1, 20), "USD", 1.10)])
        rows = compute_fx_risk(
            [inv(issuance_date=date(2025, 1, 10))],
            [M2025_01],
            rates,
            index_table(),
            ["C1"],
        )
        self.assertIsNone(rows[0].importe_vivo_eur_emision)
        self.assertIsNone(rows[0].perdida_eur)
        # el corte si cotiza (20-ene <= 31-ene): el valor de corte existe y el
        # indice se pondera por el; lo que no existe es la perdida (sin emision)
        self.assertIsNotNone(rows[0].importe_vivo_eur_corte)

    def test_4_prioridad_reported_sobre_bce(self):
        """El tipo declarado por la factura manda sobre el BCE."""
        rates = RateSeries([EcbQuote(date(2025, 1, 10), "USD", 2.00)])
        rows = compute_fx_risk(
            [inv(amount_eur=90.0, amount_eur_source="reported")],
            [M2025_01],
            rates,
            index_table(),
            ["C1"],
        )
        self.assertAlmostEqual(rows[0].importe_vivo_eur_emision, 90.0)
        # a corte si usa BCE (mismo tipo): 100/2 = 50
        self.assertAlmostEqual(rows[0].importe_vivo_eur_corte, 50.0)
        self.assertAlmostEqual(rows[0].perdida_eur, 40.0)

    def test_5_revalorizacion_da_perdida_negativa(self):
        rates = RateSeries(
            [
                EcbQuote(date(2025, 1, 10), "USD", 1.20),
                EcbQuote(date(2025, 1, 31), "USD", 1.00),
            ]
        )
        rows = compute_fx_risk([inv()], [M2025_01], rates, index_table(), ["C1"])
        self.assertLess(rows[0].perdida_eur, 0)


class NoFugaTests(unittest.TestCase):
    def test_6_truncado_no_cambia_el_corte(self):
        """Quitar todo lo posterior al corte deja enero identico."""
        full = RateSeries(
            [
                EcbQuote(date(2025, 1, 10), "USD", 1.10),
                EcbQuote(date(2025, 1, 31), "USD", 1.20),
                EcbQuote(date(2025, 2, 3), "USD", 99.0),  # posterior
            ]
        )
        trunc = RateSeries(
            [
                EcbQuote(date(2025, 1, 10), "USD", 1.10),
                EcbQuote(date(2025, 1, 31), "USD", 1.20),
            ]
        )
        a = compute_fx_risk([inv()], [M2025_01], full, index_table(), ["C1"])[0]
        b = compute_fx_risk([inv()], [M2025_01], trunc, index_table(), ["C1"])[0]
        self.assertEqual(a.to_row(), b.to_row())
        self.assertAlmostEqual(a.importe_vivo_eur_corte, 100 / 1.20)

    def test_7_perturbacion_posterior_al_corte_no_influye(self):
        """Un shock enorme despues del corte no toca la valoracion de enero."""
        base = compute_fx_risk(
            [inv()],
            [M2025_01],
            RateSeries([EcbQuote(date(2025, 1, 31), "USD", 1.20)]),
            index_table(),
            ["C1"],
        )[0]
        shock = compute_fx_risk(
            [inv()],
            [M2025_01],
            RateSeries(
                [
                    EcbQuote(date(2025, 1, 31), "USD", 1.20),
                    EcbQuote(date(2025, 2, 1), "USD", 0.001),
                ]
            ),
            index_table(),
            ["C1"],
        )[0]
        self.assertEqual(base.to_row(), shock.to_row())

    def test_8_factura_emitida_despues_del_corte_no_existe(self):
        rows = compute_fx_risk(
            [inv(issuance_date=date(2025, 2, 15))],
            [M2025_01],
            RateSeries([EcbQuote(date(2025, 2, 15), "USD", 1.10)]),
            index_table(),
            ["C1"],
        )
        self.assertEqual(rows[0].n_facturas_vivas, 0)
        self.assertEqual(rows[0].confidence, "ninguna")

    def test_9_pagada_despues_del_corte_sigue_viva_en_el_corte(self):
        rates = RateSeries([EcbQuote(date(2025, 1, 31), "USD", 1.10)])
        rows = compute_fx_risk(
            [inv(status="paid", payment_date=date(2025, 3, 1))],
            [M2025_01],
            rates,
            index_table(),
            ["C1"],
        )
        self.assertEqual(rows[0].n_facturas_vivas, 1)


class IntervaloOpacasTests(unittest.TestCase):
    def test_10_solo_opacas_es_puntual_1(self):
        rows = compute_fx_risk(
            [inv(currency="COP")],
            [M2025_01],
            RateSeries([]),
            index_table(),
            ["C1"],
        )
        r = rows[0]
        self.assertTrue(r.solo_opacas)
        self.assertFalse(r.indice_es_intervalo)
        self.assertEqual(r.indice_fx, 1.0)
        self.assertIsNone(r.importe_vivo_eur_corte)  # nulo, nunca cero

    def test_11_mezcla_opaca_puntual_null_e_intervalo(self):
        rows = compute_fx_risk(
            [
                inv(operation_id="a", currency="EUR", amount=100.0,
                    amount_eur_source="identity"),
                inv(operation_id="b", currency="COP", amount=500000.0),
            ],
            [M2025_01],
            RateSeries([]),
            index_table(),
            ["C1"],
        )
        r = rows[0]
        self.assertTrue(r.tiene_opacas)
        self.assertFalse(r.solo_opacas)
        self.assertTrue(r.indice_es_intervalo)
        self.assertIsNone(r.indice_fx)
        self.assertEqual(r.indice_fx_min, 0.0)
        self.assertEqual(r.indice_fx_max, 1.0)

    def test_12_no_imputa_cero_a_lo_que_no_puede_valorar(self):
        rows = compute_fx_risk(
            [inv(currency="COP")],
            [M2025_01],
            RateSeries([]),
            index_table(),
            ["C1"],
        )
        r = rows[0]
        self.assertIsNone(r.importe_vivo_eur_corte)
        self.assertEqual(r.importe_opaco_original, 100.0)
        self.assertIn("exposicion_divisa_opaca", r.flags)


class SpearmanTests(unittest.TestCase):
    def test_13_monotono_perfecto(self):
        self.assertAlmostEqual(
            spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0
        )
        self.assertAlmostEqual(
            spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0
        )

    def test_14_empates_y_pocos_datos(self):
        self.assertIsNone(spearman([1, 2], [1, 2]))
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))


class ContrafactualTests(unittest.TestCase):
    def _row(self, cid, idx, vol=None, der=None):
        return CompanyMonthFxRisk(
            company_id=cid,
            month=M2025_01,
            n_facturas_vivas=1,
            indice_fx=idx,
            indice_fx_vol=idx if vol is None else vol,
            indice_fx_deriva=idx if der is None else der,
        )

    def test_15_dh_escala_con_beta_y_h(self):
        rows = [self._row("C1", 0.5)]
        a = [AssessmentFx("C1", M2025_01, 80.0, 0.1, 0.9, 2.0, 8.0, 80.0)]
        cf = compute_counterfactual(a, rows, (0.10,))
        block = cf["por_beta"]["0.1"]
        self.assertAlmostEqual(block["dh_abs_cambian"]["max"], 0.10 * 80.0 * 0.5)
        self.assertEqual(block["empresas_con_cambio"], 1)
        self.assertEqual(cf["poblacion"]["empresas_intactas_sin_exposicion_no_eur"], 0)

    def test_16_empresa_sin_exposicion_queda_intacta(self):
        rows = [self._row("C1", 0.0)]
        a = [AssessmentFx("C1", M2025_01, 80.0, 0.1, 0.9, 2.0, 8.0, 80.0)]
        cf = compute_counterfactual(a, rows, (0.25,))
        block = cf["por_beta"]["0.25"]
        self.assertEqual(block["empresas_mes_con_cambio"], 0)
        self.assertEqual(
            cf["poblacion"]["empresas_intactas_sin_exposicion_no_eur"], 1
        )

    def test_17_intervalo_usa_limites(self):
        r = CompanyMonthFxRisk(
            company_id="C1",
            month=M2025_01,
            n_facturas_vivas=1,
            indice_fx=None,
            indice_fx_min=0.2,
            indice_fx_max=1.0,
            indice_es_intervalo=True,
            indice_fx_vol_min=0.2,
            indice_fx_vol_max=1.0,
            indice_fx_deriva_min=0.2,
            indice_fx_deriva_max=1.0,
        )
        a = [AssessmentFx("C1", M2025_01, 100.0, None, None, None, None, 100.0)]
        cf = compute_counterfactual(a, [r], (0.10,))
        block = cf["por_beta"]["0.1"]
        self.assertAlmostEqual(block["dh_abs_intervalo_superior"]["max"], 10.0)
        self.assertAlmostEqual(block["dh_abs_intervalo_inferior"]["max"], 2.0)
        self.assertEqual(block["empresas_mes_con_indice_intervalo"], 1)


class CalendarioTests(unittest.TestCase):
    def test_18_month_end(self):
        self.assertEqual(month_end(date(2025, 2, 1)), date(2025, 2, 28))
        self.assertEqual(month_end(date(2024, 2, 1)), date(2024, 2, 29))
        self.assertEqual(month_end(date(2025, 12, 1)), date(2025, 12, 31))


class AnclaFmiPointInTimeTests(unittest.TestCase):
    """Ancla F6: la segunda fuente (FMI, F1c) valora sin fuga point-in-time.

    Criterio de hecho 1 del dispatch F6: una factura en ARS en una fecha
    conocida se valora con el tipo FMI correcto y NUNCA se usa un tipo con
    `available_at` posterior al corte.
    """

    CUT_2026_08 = date(2026, 8, 1)

    def setUp(self):
        if not EXTRA_RATES_PATH.exists():
            self.skipTest("parquet rates_extra.parquet no disponible")
        self.extra = load_extra_rate_series(EXTRA_RATES_PATH)
        if self.extra is None:
            self.skipTest("rates_extra.parquet sin columnas esperadas")

    def test_22_ancla_ars_fecha_conocida_sin_fuga(self):
        """ARS emitida 2026-04-20: emision 2026-04, corte 2026-06 (lag 2).

        El tipo de 2026-07 (available_at 2026-09-30) NO se puede usar en el
        corte de 2026-08: seria fuga. Los valores estan tomados del parquet
        vendorizado (fin_de_periodo).
        """
        r_emision = 1622.4823  # ARS 2026-04, available_at 2026-06-30
        r_corte = 1683.4635    # ARS 2026-06, available_at 2026-08-31
        r_futuro = 1700.35425  # ARS 2026-07, available_at 2026-09-30
        factura = inv(currency="ARS", amount=r_emision,
                      issuance_date=date(2026, 4, 20))
        rows = compute_fx_risk(
            [factura], [self.CUT_2026_08], RateSeries([]), index_table(),
            ["C1"], extra_rates=self.extra,
        )
        r = rows[0]
        self.assertAlmostEqual(r.importe_vivo_eur_emision, 1.0)
        self.assertAlmostEqual(r.importe_vivo_eur_corte, r_emision / r_corte)
        self.assertAlmostEqual(r.perdida_eur, 1.0 - r_emision / r_corte)
        self.assertGreater(r.perdida_eur, 0)  # ARS se deprecio
        # La fila futura habria dado otro valor: no se usa.
        self.assertNotAlmostEqual(
            r.importe_vivo_eur_corte, r_emision / r_futuro
        )
        self.assertFalse(r.tiene_opacas)
        self.assertFalse(r.indice_es_intervalo)
        self.assertEqual(r.n_rescatadas_fmi, 1)
        self.assertEqual(r.lag_fmi_emision_max_meses, 0)
        self.assertEqual(r.lag_fmi_corte_max_meses, 2)

    def test_23_fila_no_publicada_no_es_utilizable(self):
        """`select_point_in_time` nunca adelanta una fila no publicada."""
        extra = ExtraRateSeries([
            ExtraRateRow("ARS", date(2026, 6, 1), 1000.0,
                         date(2026, 6, 30), date(2026, 8, 31)),
            ExtraRateRow("ARS", date(2026, 7, 1), 99999.0,
                         date(2026, 7, 31), date(2026, 9, 30)),
        ])
        sel = extra.select_point_in_time("ARS", date(2026, 8, 31),
                                         date(2026, 8, 31))
        self.assertIsNotNone(sel)
        self.assertEqual(sel.rate, 1000.0)
        self.assertEqual(sel.lag_meses, 2)
        # Un corte anterior a la publicacion no ve ninguna fila.
        self.assertIsNone(
            extra.select_point_in_time("ARS", date(2026, 7, 31),
                                       date(2026, 6, 30))
        )

    def test_24_mezcla_con_fmi_pasa_a_puntual(self):
        """Con tipo FMI, la empresa de mezcla deja de caer en intervalo."""
        extra = ExtraRateSeries([
            ExtraRateRow("COP", M2025_01, 4000.0,
                         date(2025, 1, 31), date(2025, 1, 31)),
        ])
        rows = compute_fx_risk(
            [
                inv(operation_id="a", currency="EUR", amount=100.0,
                    amount_eur_source="identity"),
                inv(operation_id="b", currency="COP", amount=400000.0),
            ],
            [M2025_01], RateSeries([]), index_table(), ["C1"],
            extra_rates=extra,
        )
        r = rows[0]
        self.assertFalse(r.tiene_opacas)
        self.assertFalse(r.indice_es_intervalo)
        self.assertIsNotNone(r.indice_fx)
        self.assertAlmostEqual(r.importe_vivo_eur_corte, 200.0)
        self.assertEqual(r.n_rescatadas_fmi, 1)

    def test_25_sin_extra_reproduce_el_estado_anterior(self):
        """Sin segunda fuente, ARS sigue siendo opaca (nulo, nunca cero)."""
        factura = inv(currency="ARS", amount=1622.4823,
                      issuance_date=date(2026, 4, 20))
        rows = compute_fx_risk(
            [factura], [self.CUT_2026_08], RateSeries([]), index_table(),
            ["C1"],
        )
        r = rows[0]
        self.assertTrue(r.solo_opacas)
        self.assertIsNone(r.importe_vivo_eur_corte)
        self.assertIsNone(r.perdida_eur)


class AnclaBceVendorizadoTests(unittest.TestCase):
    """Ancla contra el CSV REAL del BCE vendorizado (reproducible)."""

    def setUp(self):
        if not ECB_VENDOR_PATH.exists():
            self.skipTest("CSV BCE vendorizado no disponible")

    def test_19_sha256_del_vendorizado(self):
        self.assertEqual(
            verify_ecb_vendor(ECB_VENDOR_PATH),
            "22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d",
        )

    def test_20_ancla_y_fin_de_semana_con_datos_reales(self):
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            rates = RateSeries(load_ecb_quotes(con, ECB_VENDOR_PATH))
        finally:
            con.close()
        # Ultima publicacion del CSV: 2026-09-18, USD = 1,146 (PROCEDENCIA.md).
        self.assertAlmostEqual(
            rates.rate_on_or_before("USD", date(2026, 9, 18)), 1.146
        )
        # Sabado y domingo posteriores: ultimo habil anterior (viernes 18).
        self.assertAlmostEqual(
            rates.rate_on_or_before("USD", date(2026, 9, 19)), 1.146
        )
        self.assertAlmostEqual(
            rates.rate_on_or_before("USD", date(2026, 9, 20)), 1.146
        )
        # Divisa opaca: el BCE no publica COP -> None (nunca un tipo inventado).
        self.assertIsNone(rates.rate_on_or_before("COP", date(2026, 9, 18)))

    def test_21_factura_real_con_tipo_bce_del_dia(self):
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            rates = RateSeries(load_ecb_quotes(con, ECB_VENDOR_PATH))
        finally:
            con.close()
        # 1.146 USD/EUR el 18-sep-2026 -> 1146 USD son 1000 EUR.
        factura = inv(
            amount=1146.0,
            issuance_date=date(2026, 9, 18),
            currency="USD",
        )
        rows = compute_fx_risk(
            [factura],
            [date(2026, 9, 1)],
            rates,
            index_table(),
            ["C1"],
        )
        self.assertAlmostEqual(rows[0].importe_vivo_eur_emision, 1000.0)


if __name__ == "__main__":
    unittest.main()
