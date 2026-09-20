"""Tests de xray/fx_index.py (fixtures sinteticas + vendorizado real).

Cubren el criterio de hecho de la tarea F1 y F1b:
- el CSV del BCE esta vendorizado y su sha256 coincide con PROCEDENCIA.md;
- el indice no filtra informacion posterior al cierre del mes (point-in-time),
  demostrado por perturbacion y por truncado, para volatilidad Y deriva;
- el indice reproduce de forma determinista desde el CSV vendorizado;
- las divisas sin cobertura BCE caen al tramo mas inestable sin estadistico;
- la deriva es ASIMETRICA y con el SIGNO correcto: el BCE cotiza unidades de
  divisa por EUR, asi que un tipo que sube debilita la divisa. Ancla explicita
  sobre el CSV real: TRY es la mas castigada por deriva en la ventana completa
  y BRL no sale castigada.
"""

import functools
import json
import math
import statistics
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from xray import fx_index as fx
from xray.fx_index import (
    CORTE_DERIVA_ESTABLE,
    CORTE_DERIVA_HIPER,
    CORTE_ESTABLE,
    CORTE_HIPER,
    DERIVA_VENTANA_MESES,
    ECB_VENDOR_PATH,
    ECB_VENDOR_SHA256,
    EcbQuote,
    MOTIVO_ANCLA,
    MOTIVO_BCE,
    MOTIVO_COBERTURA_INSUFICIENTE,
    MOTIVO_SIN_COBERTURA,
    TRAMO_ESTABLE,
    TRAMO_HIPERVOLATIL,
    TRAMO_VOLATIL,
    assert_point_in_time,
    build_grid_currencies,
    build_month_end_quotes,
    classify_drift_tramo,
    classify_tramo,
    closed_months,
    combine_tramos,
    compute_fx_index,
    daily_log_returns,
    month_end,
    month_end_datetime,
    month_end_quote,
    robust_annualized_vol,
    sha256_file,
    shift_month,
    sustained_drift,
)

M2025_01 = date(2025, 1, 1)
M2025_02 = date(2025, 2, 1)
M2025_03 = date(2025, 3, 1)
M2025_04 = date(2025, 4, 1)
MONTHS = [M2025_01, M2025_02, M2025_03, M2025_04]


def q(currency: str, day: str, rate: float) -> EcbQuote:
    return EcbQuote(day=date.fromisoformat(day), currency=currency, rate=rate)


def business_days(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days


def synthetic_quotes(currency: str = "AAA", wobble: float = 0.01) -> list[EcbQuote]:
    """Cotizaciones diarias con una onda determinista (sin aleatoriedad)."""
    quotes = []
    for i, day in enumerate(business_days(date(2024, 12, 1), date(2025, 4, 30))):
        rate = 1.0 + wobble * math.sin(i / 3.0)
        quotes.append(EcbQuote(day=day, currency=currency, rate=rate))
    return quotes


def rows_by(rows):
    return {(r.currency, r.month): r for r in rows}


def trend_quotes(
    currency: str = "AAA",
    first_day: str = "2024-12-02",
    last_day: str = "2025-02-28",
    daily_drift: float = 0.0,
) -> list[EcbQuote]:
    """Cotizaciones diarias con tendencia determinista en el tipo EUR/divisa.

    `daily_drift > 0` hace SUBIR el tipo EUR/divisa (la divisa se debilita);
    los rendimientos son constantes, de modo que la volatilidad robusta es 0 y
    cualquier tramo distinto de `estable` viene de la deriva.
    """
    rate = 1.0
    quotes = []
    for day in business_days(date.fromisoformat(first_day), date.fromisoformat(last_day)):
        rate *= 1.0 + daily_drift
        quotes.append(EcbQuote(day=day, currency=currency, rate=rate))
    return quotes


@functools.lru_cache(maxsize=1)
def real_index():
    """Indice completo construido desde el CSV vendorizado (cacheado)."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        quotes, _ = fx.load_ecb_quotes(con, ECB_VENDOR_PATH)
    finally:
        con.close()
    months = closed_months()
    active = fx._ecb_currencies_in_window(quotes, months)
    grid = build_grid_currencies(active, [])
    rows = compute_fx_index(quotes, grid, months, frozenset(active) | {"EUR"})
    return quotes, months, tuple(rows)


class CalendarTests(unittest.TestCase):
    def test_month_end_handles_leap_and_length(self):
        self.assertEqual(month_end(date(2024, 2, 1)), date(2024, 2, 29))
        self.assertEqual(month_end(date(2025, 2, 1)), date(2025, 2, 28))
        self.assertEqual(month_end(date(2025, 4, 1)), date(2025, 4, 30))

    def test_closed_months_is_the_24_month_grid(self):
        months = closed_months()
        self.assertEqual(len(months), 24)
        self.assertEqual(months[0], date(2024, 9, 1))
        self.assertEqual(months[-1], date(2026, 8, 1))


class StatisticTests(unittest.TestCase):
    def test_robust_vol_of_symmetric_returns_is_zero(self):
        self.assertEqual(robust_annualized_vol([0.01] * 20), 0.0)

    def test_robust_vol_requires_minimum_observations(self):
        self.assertIsNone(robust_annualized_vol([0.01] * (fx.MIN_RENDIMIENTOS - 1)))

    def test_robust_vol_scales_with_dispersion(self):
        narrow = robust_annualized_vol([-0.001, 0.0, 0.001] * 8)
        wide = robust_annualized_vol([-0.02, 0.0, 0.02] * 8)
        self.assertIsNotNone(narrow)
        self.assertIsNotNone(wide)
        self.assertGreater(wide, narrow)

    def test_classify_tramo_boundaries(self):
        self.assertEqual(classify_tramo(None), TRAMO_HIPERVOLATIL)
        self.assertEqual(classify_tramo(0.0), TRAMO_ESTABLE)
        self.assertEqual(classify_tramo(CORTE_ESTABLE - 1e-9), TRAMO_ESTABLE)
        self.assertEqual(classify_tramo(CORTE_ESTABLE), TRAMO_VOLATIL)
        self.assertEqual(classify_tramo(CORTE_HIPER - 1e-9), TRAMO_VOLATIL)
        self.assertEqual(classify_tramo(CORTE_HIPER), TRAMO_HIPERVOLATIL)


class ReturnAssignmentTests(unittest.TestCase):
    def test_first_return_of_a_month_uses_previous_month_rate(self):
        quotes = [q("AAA", "2025-01-30", 1.0), q("AAA", "2025-02-03", 1.1)]
        returns = daily_log_returns(quotes)
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].month, M2025_02)
        self.assertAlmostEqual(returns[0].log_return, math.log(1.1))

    def test_returns_are_sorted_and_never_use_a_later_quote(self):
        quotes = synthetic_quotes()
        returns = daily_log_returns(quotes)
        for ret in returns:
            self.assertLessEqual(ret.available_at, month_end_datetime(ret.month))


class PointInTimeTests(unittest.TestCase):
    def test_available_at_never_exceeds_month_end(self):
        quotes = synthetic_quotes()
        rows = compute_fx_index(quotes, ["AAA"], MONTHS, frozenset({"AAA"}))
        assert_point_in_time(rows)  # no lanza

    def test_future_shock_does_not_change_earlier_months(self):
        base_quotes = synthetic_quotes()
        base = rows_by(compute_fx_index(base_quotes, ["AAA"], MONTHS, frozenset({"AAA"})))

        # Shock extremo el primer dia de abril: solo puede afectar a abril.
        shocked_quotes = base_quotes + [q("AAA", "2025-04-01", 5.0)]
        shocked = rows_by(compute_fx_index(shocked_quotes, ["AAA"], MONTHS, frozenset({"AAA"})))

        for month in (M2025_01, M2025_02, M2025_03):
            self.assertEqual(base[("AAA", month)], shocked[("AAA", month)],
                             f"el shock de abril cambio {month}")
        self.assertNotEqual(base[("AAA", M2025_04)], shocked[("AAA", M2025_04)])

    def test_truncating_quotes_at_month_end_reproduces_the_row(self):
        quotes = synthetic_quotes()
        base = rows_by(compute_fx_index(quotes, ["AAA"], MONTHS, frozenset({"AAA"})))
        for month in MONTHS:
            cutoff = month_end_datetime(month)
            truncated = [quote for quote in quotes if quote.available_at <= cutoff]
            got = rows_by(compute_fx_index(truncated, ["AAA"], MONTHS, frozenset({"AAA"})))
            for earlier in MONTHS:
                if earlier > month:
                    continue
                self.assertEqual(base[("AAA", earlier)], got[("AAA", earlier)],
                                 f"truncar en {month} altero {earlier}")

    def test_row_available_at_is_the_last_publication_used(self):
        quotes = synthetic_quotes()
        rows = rows_by(compute_fx_index(quotes, ["AAA"], MONTHS, frozenset({"AAA"})))
        january = rows[("AAA", M2025_01)]
        last_january_quote = max(
            (quote for quote in quotes if quote.day.replace(day=1) == M2025_01),
            key=lambda quote: quote.day,
        )
        self.assertEqual(january.available_at, last_january_quote.available_at)
        # La disponibilidad de la fila es <= cierre del mes y estrictamente
        # posterior a cualquier dato del mes anterior (enero tiene datos).
        self.assertLessEqual(january.available_at, month_end_datetime(M2025_01))
        self.assertGreater(january.available_at, month_end_datetime(date(2024, 12, 1)))


class DefaultUnstableTests(unittest.TestCase):
    def test_uncovered_currency_is_hypervolatile_with_null_statistic(self):
        quotes = synthetic_quotes("AAA")
        rows = rows_by(
            compute_fx_index(quotes, ["AAA", "ZZZ"], MONTHS, frozenset({"AAA"}))
        )
        for month in MONTHS:
            row = rows[("ZZZ", month)]
            self.assertEqual(row.tramo, TRAMO_HIPERVOLATIL)
            self.assertIsNone(row.vol_anualizada)
            self.assertEqual(row.motivo, MOTIVO_SIN_COBERTURA)
            self.assertFalse(row.cobertura_bce)

    def test_covered_currency_without_data_is_hypervolatile_and_flagged(self):
        # BBB solo cotiza en enero: en febrero esta "cubierta" por el set pero
        # sin datos, asi que cae al tramo inestable con motivo distinguible.
        quotes = [q("BBB", "2025-01-02", 1.0), q("BBB", "2025-01-03", 1.02)]
        rows = rows_by(compute_fx_index(quotes, ["BBB"], MONTHS, frozenset({"BBB"})))
        february = rows[("BBB", M2025_02)]
        self.assertEqual(february.tramo, TRAMO_HIPERVOLATIL)
        self.assertIsNone(february.vol_anualizada)
        self.assertEqual(february.motivo, MOTIVO_COBERTURA_INSUFICIENTE)
        self.assertFalse(february.cobertura_bce)

    def test_eur_is_the_anchor_with_zero_volatility(self):
        rows = rows_by(compute_fx_index([], ["EUR"], MONTHS, frozenset({"EUR"})))
        for month in MONTHS:
            row = rows[("EUR", month)]
            self.assertEqual(row.tramo, TRAMO_ESTABLE)
            self.assertEqual(row.vol_anualizada, 0.0)
            self.assertEqual(row.motivo, MOTIVO_ANCLA)
            # El ancla tambien es deriva 0 por identidad (no None).
            self.assertEqual(row.deriva_valor, 0.0)
            self.assertEqual(row.deriva_componente, 0.0)
            self.assertEqual(row.tramo_deriva, TRAMO_ESTABLE)


class DriftPrimitiveTests(unittest.TestCase):
    def test_shift_month_handles_year_boundaries(self):
        self.assertEqual(shift_month(date(2025, 1, 1), -1), date(2024, 12, 1))
        self.assertEqual(shift_month(date(2025, 1, 1), -12), date(2024, 1, 1))
        self.assertEqual(shift_month(date(2025, 3, 1), -14), date(2024, 1, 1))
        self.assertEqual(shift_month(date(2024, 12, 1), 1), date(2025, 1, 1))

    def test_month_end_quote_requires_a_quote_inside_the_month(self):
        quotes = [q("AAA", "2025-01-31", 1.0), q("AAA", "2025-03-05", 1.1)]
        series = build_month_end_quotes(quotes)
        self.assertEqual(month_end_quote(series, "AAA", M2025_01).day, date(2025, 1, 31))
        # Febrero no cotiza: no se hereda el tipo rancio de enero.
        self.assertIsNone(month_end_quote(series, "AAA", M2025_02))
        self.assertEqual(month_end_quote(series, "AAA", M2025_03).rate, 1.1)
        self.assertIsNone(month_end_quote(series, "NOEXISTE", M2025_01))

    def test_drift_sign_weakens_currency_when_rate_rises(self):
        # El BCE cotiza unidades de divisa por EUR: si el tipo SUBE, la divisa
        # AAA se DEBILITA, luego la deriva debe ser NEGATIVA.
        quotes = [q("AAA", "2024-12-31", 1.0), q("AAA", "2025-01-31", 1.10)]
        series = build_month_end_quotes(quotes)
        drift = sustained_drift(series, "AAA", M2025_01, window_months=1)
        self.assertLess(drift, 0)
        self.assertAlmostEqual(drift, 1.0 / 1.10 - 1.0)
        componente = max(0.0, -drift)
        self.assertAlmostEqual(componente, 0.090909, places=5)
        self.assertEqual(classify_drift_tramo(componente), TRAMO_HIPERVOLATIL)

    def test_drift_sign_strengthening_currency_is_not_punished(self):
        quotes = [q("AAA", "2024-12-31", 1.0), q("AAA", "2025-01-31", 0.90)]
        series = build_month_end_quotes(quotes)
        drift = sustained_drift(series, "AAA", M2025_01, window_months=1)
        self.assertGreater(drift, 0)
        self.assertEqual(max(0.0, -drift), 0.0)
        self.assertEqual(classify_drift_tramo(0.0), TRAMO_ESTABLE)
        self.assertIsNone(classify_drift_tramo(None))

    def test_classify_drift_tramo_boundaries(self):
        self.assertEqual(classify_drift_tramo(0.0), TRAMO_ESTABLE)
        self.assertEqual(
            classify_drift_tramo(CORTE_DERIVA_ESTABLE - 1e-9), TRAMO_ESTABLE
        )
        self.assertEqual(classify_drift_tramo(CORTE_DERIVA_ESTABLE), TRAMO_VOLATIL)
        self.assertEqual(
            classify_drift_tramo(CORTE_DERIVA_HIPER - 1e-9), TRAMO_VOLATIL
        )
        self.assertEqual(classify_drift_tramo(CORTE_DERIVA_HIPER), TRAMO_HIPERVOLATIL)

    def test_drift_requires_both_window_ends(self):
        quotes = [q("AAA", "2025-01-31", 1.0), q("AAA", "2025-02-28", 1.1)]
        series = build_month_end_quotes(quotes)
        self.assertIsNone(sustained_drift(series, "AAA", M2025_02, window_months=3))

    def test_combine_tramos_takes_the_most_severe(self):
        self.assertEqual(
            combine_tramos(TRAMO_VOLATIL, TRAMO_HIPERVOLATIL), TRAMO_HIPERVOLATIL
        )
        self.assertEqual(combine_tramos(TRAMO_ESTABLE, TRAMO_VOLATIL), TRAMO_VOLATIL)
        self.assertEqual(combine_tramos(TRAMO_VOLATIL, None), TRAMO_VOLATIL)
        self.assertEqual(combine_tramos(None, None), TRAMO_HIPERVOLATIL)


class DriftIndexTests(unittest.TestCase):
    def test_combined_tramo_follows_drift_even_with_zero_volatility(self):
        # Tipo creciente => divisa debil => vol robusta 0 pero deriva alta:
        # exactamente el caso que la volatilidad sola no ve.
        quotes = trend_quotes("AAA", daily_drift=0.01)
        rows = rows_by(
            compute_fx_index(
                quotes, ["AAA"], [M2025_01, M2025_02], frozenset({"AAA"}),
                deriva_ventana_meses=1,
            )
        )
        january = rows[("AAA", M2025_01)]
        self.assertEqual(january.tramo_volatilidad, TRAMO_ESTABLE)
        self.assertGreater(january.deriva_componente, CORTE_DERIVA_HIPER)
        self.assertEqual(january.tramo_deriva, TRAMO_HIPERVOLATIL)
        self.assertEqual(january.tramo, TRAMO_HIPERVOLATIL)

    def test_appreciating_currency_keeps_its_volatility_tramo(self):
        quotes = trend_quotes("AAA", daily_drift=-0.01)
        rows = rows_by(
            compute_fx_index(
                quotes, ["AAA"], [M2025_01, M2025_02], frozenset({"AAA"}),
                deriva_ventana_meses=1,
            )
        )
        january = rows[("AAA", M2025_01)]
        self.assertEqual(january.deriva_componente, 0.0)
        self.assertEqual(january.tramo_deriva, TRAMO_ESTABLE)
        self.assertEqual(january.tramo, january.tramo_volatilidad)

    def test_index_publishes_both_components_separately(self):
        quotes = trend_quotes("AAA", daily_drift=0.01)
        rows = rows_by(
            compute_fx_index(
                quotes, ["AAA"], [M2025_01], frozenset({"AAA"}),
                deriva_ventana_meses=1,
            )
        )
        row = rows[("AAA", M2025_01)]
        self.assertIsNotNone(row.vol_anualizada)
        self.assertIsNotNone(row.deriva_valor)
        self.assertIsNotNone(row.deriva_componente)
        self.assertEqual(row.deriva_ventana_meses, 1)
        self.assertNotEqual(row.tramo_volatilidad, row.tramo_deriva)


class DriftPointInTimeTests(unittest.TestCase):
    def test_shock_after_the_month_does_not_change_earlier_drift(self):
        quotes = trend_quotes("AAA", daily_drift=0.005)
        months = [M2025_01, M2025_02]
        base = rows_by(
            compute_fx_index(
                quotes, ["AAA"], months, frozenset({"AAA"}), deriva_ventana_meses=1
            )
        )
        shocked_quotes = quotes + [q("AAA", "2025-02-03", 50.0)]
        shocked = rows_by(
            compute_fx_index(
                shocked_quotes, ["AAA"], months, frozenset({"AAA"}),
                deriva_ventana_meses=1,
            )
        )
        self.assertEqual(base[("AAA", M2025_01)], shocked[("AAA", M2025_01)])

    def test_truncating_history_reproduces_drift_rows(self):
        quotes = trend_quotes("AAA", daily_drift=0.004)
        months = [M2025_01, M2025_02, M2025_03]
        base = rows_by(
            compute_fx_index(
                quotes, ["AAA"], months, frozenset({"AAA"}), deriva_ventana_meses=1
            )
        )
        for month in months:
            cutoff = month_end_datetime(month)
            truncated = [quote for quote in quotes if quote.available_at <= cutoff]
            got = rows_by(
                compute_fx_index(
                    truncated, ["AAA"], months, frozenset({"AAA"}),
                    deriva_ventana_meses=1,
                )
            )
            for earlier in months:
                if earlier <= month:
                    self.assertEqual(
                        base[("AAA", earlier)],
                        got[("AAA", earlier)],
                        f"truncar en {month} altero {earlier}",
                    )


class DriftRealDataAnchorTests(unittest.TestCase):
    """Ancla explicita sobre el CSV vendorizado real (criterio de hecho F1b)."""

    def test_reference_currency_variations_reproduce(self):
        quotes, _, _ = real_index()
        first, last = date(2024, 9, 2), date(2026, 8, 31)
        by_currency: dict[str, list[EcbQuote]] = {}
        for quote in quotes:
            if first <= quote.day <= last:
                by_currency.setdefault(quote.currency, []).append(quote)
        expected = {
            "TRY": -0.328,
            "IDR": -0.163,
            "INR": -0.159,
            "JPY": -0.122,
            "USD": -0.046,
            "GBP": -0.017,
            "BRL": 0.037,
            "MXN": 0.103,
        }
        for currency, want in expected.items():
            series = sorted(by_currency[currency], key=lambda quote: quote.day)
            got = series[0].rate / series[-1].rate - 1.0
            self.assertAlmostEqual(got, want, delta=0.002, msg=currency)

    def test_full_window_try_is_the_most_punished_and_brl_is_not(self):
        quotes, months, rows = real_index()
        # Veredicto de la ventana COMPLETA: TRY la mas erosionada y BRL positiva.
        first, last = date(2024, 9, 2), date(2026, 8, 31)
        by_currency: dict[str, list[EcbQuote]] = {}
        for quote in quotes:
            if first <= quote.day <= last:
                by_currency.setdefault(quote.currency, []).append(quote)
        variation = {
            currency: sorted(series, key=lambda quote: quote.day)[0].rate
            / sorted(series, key=lambda quote: quote.day)[-1].rate
            - 1.0
            for currency, series in by_currency.items()
        }
        self.assertEqual(min(variation, key=variation.get), "TRY")
        self.assertGreater(variation["BRL"], 0.0)

        # En el mes de censo (fin de la ventana), TRY es la mas castigada por
        # deriva y BRL tiene componente 0 (no castigada).
        census = months[-1]
        componente = {
            row.currency: (row.deriva_componente or 0.0)
            for row in rows
            if row.month == census and row.cobertura_bce
        }
        self.assertEqual(max(componente, key=componente.get), "TRY")
        self.assertEqual(componente["BRL"], 0.0)
        brl_row = next(
            row for row in rows if row.currency == "BRL" and row.month == census
        )
        self.assertEqual(brl_row.tramo_deriva, TRAMO_ESTABLE)

    def test_try_has_the_largest_median_drift_over_the_grid(self):
        _, _, rows = real_index()
        by_currency: dict[str, list[float]] = {}
        for row in rows:
            if row.cobertura_bce and row.deriva_componente is not None:
                by_currency.setdefault(row.currency, []).append(row.deriva_componente)
        medians = {
            currency: statistics.median(values)
            for currency, values in by_currency.items()
        }
        self.assertEqual(max(medians, key=medians.get), "TRY")


class GridTests(unittest.TestCase):
    def test_grid_is_union_of_sources_plus_eur(self):
        grid = build_grid_currencies(["USD", "JPY"], ["USD", "COP"])
        self.assertEqual(grid, ["COP", "EUR", "JPY", "USD"])

    def test_observed_currency_publishes_statistic_and_reason(self):
        quotes = synthetic_quotes("AAA")
        rows = rows_by(compute_fx_index(quotes, ["AAA"], MONTHS, frozenset({"AAA"})))
        january = rows[("AAA", M2025_01)]
        self.assertEqual(january.motivo, MOTIVO_BCE)
        self.assertTrue(january.cobertura_bce)
        self.assertIsNotNone(january.vol_anualizada)
        self.assertEqual(january.n_cotizaciones, 23)
        self.assertEqual(january.n_rendimientos, 23)


class VendorTests(unittest.TestCase):
    def test_vendored_csv_sha256_matches_constant(self):
        self.assertTrue(ECB_VENDOR_PATH.exists(), f"falta {ECB_VENDOR_PATH}")
        self.assertEqual(sha256_file(ECB_VENDOR_PATH), ECB_VENDOR_SHA256)

    def test_procedencia_declares_the_same_sha256(self):
        text = (ECB_VENDOR_PATH.parent / "PROCEDENCIA.md").read_text(encoding="utf-8")
        self.assertIn(ECB_VENDOR_SHA256, text)
        self.assertIn("eurofxref-hist.zip", text)

    def test_rebuild_is_deterministic_from_vendor(self):
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            quotes, columns = fx.load_ecb_quotes(con, ECB_VENDOR_PATH)
        finally:
            con.close()
        self.assertGreater(len(quotes), 100_000)
        self.assertIn("USD", columns)
        self.assertNotIn("Date", columns)

        months = closed_months()
        active = fx._ecb_currencies_in_window(quotes, months)
        grid = build_grid_currencies(active, ["USD", "COP", "ARS", "EUR"])
        first = compute_fx_index(quotes, grid, months, frozenset(active) | {"EUR"})
        second = compute_fx_index(quotes, grid, months, frozenset(active) | {"EUR"})
        self.assertEqual([r.to_row() for r in first], [r.to_row() for r in second])
        assert_point_in_time(first)

        rows = rows_by(first)
        # COP no tiene cobertura BCE en la ventana.
        self.assertEqual(rows[("COP", date(2025, 6, 1))].tramo, TRAMO_HIPERVOLATIL)
        self.assertIsNone(rows[("COP", date(2025, 6, 1))].vol_anualizada)
        # USD si, y publica estadistico.
        self.assertIsNotNone(rows[("USD", date(2025, 6, 1))].vol_anualizada)

    def test_real_data_truncation_point_in_time(self):
        """Sobre el CSV real: truncar en el cierre de m no altera ningun mes <= m."""
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            quotes, _ = fx.load_ecb_quotes(con, ECB_VENDOR_PATH)
        finally:
            con.close()
        months = closed_months()
        active = fx._ecb_currencies_in_window(quotes, months)
        grid = build_grid_currencies(active, [])
        covered = frozenset(active) | {"EUR"}
        base = rows_by(compute_fx_index(quotes, grid, months, covered))
        for month in (months[0], months[5], months[12], months[-2]):
            cutoff = month_end_datetime(month)
            truncated = [quote for quote in quotes if quote.available_at <= cutoff]
            got = rows_by(compute_fx_index(truncated, grid, months, covered))
            for earlier in months:
                if earlier > month:
                    continue
                for currency in grid:
                    self.assertEqual(
                        base[(currency, earlier)], got[(currency, earlier)],
                        f"truncar en {month} altero {currency} {earlier}",
                    )

    def test_committed_report_reproduces_from_vendor(self):
        report = fx.DEFAULT_OUTPUT_DIR / "fx_index.parquet"
        if not report.exists():
            self.skipTest("el informe aun no se ha generado")
        import duckdb

        con = duckdb.connect(":memory:")
        try:
            quotes, _ = fx.load_ecb_quotes(con, ECB_VENDOR_PATH)
            months = closed_months()
            active = fx._ecb_currencies_in_window(quotes, months)
            portfolio = [
                r[0]
                for r in con.execute(
                    f"SELECT DISTINCT currency_norm FROM read_parquet("
                    f"'{fx.paths.CLEAN_DIR / 'invoices.parquet'}') "
                    f"WHERE {fx.LIVE_PORTFOLIO_FILTER}"
                ).fetchall()
                if r[0]
            ]
            grid = build_grid_currencies(active, portfolio)
            fresh = compute_fx_index(
                quotes, grid, months, frozenset(active) | {"EUR"}
            )
            committed = con.execute(
                f"SELECT currency, month, cobertura_bce, n_cotizaciones, "
                f"n_rendimientos, vol_anualizada, tramo_volatilidad, "
                f"deriva_ventana_meses, deriva_valor, deriva_componente, "
                f"tramo_deriva, tramo, motivo, "
                f"CAST(available_at AS TIMESTAMP) "
                f"FROM read_parquet('{report}') ORDER BY currency, month"
            ).fetchall()
        finally:
            con.close()
        expected = sorted(
            (
                r.currency,
                datetime.combine(r.month, time(0, 0)),
                r.cobertura_bce,
                r.n_cotizaciones,
                r.n_rendimientos,
                r.vol_anualizada,
                r.tramo_volatilidad,
                r.deriva_ventana_meses,
                r.deriva_valor,
                r.deriva_componente,
                r.tramo_deriva,
                r.tramo,
                r.motivo,
                r.available_at,
            )
            for r in fresh
        )
        self.assertEqual(len(committed), len(expected))
        float_positions = {5, 8, 9}
        date_positions = {1, 13}
        for got, want in zip(committed, expected):
            for position, (got_value, want_value) in enumerate(zip(got, want)):
                if want_value is None:
                    self.assertIsNone(got_value)
                elif position in float_positions:
                    self.assertAlmostEqual(got_value, want_value, places=12)
                elif position in date_positions:
                    self.assertEqual(got_value, want_value)
                else:
                    self.assertEqual(got_value, want_value)
            self.assertEqual(got[1].date(), want[1].date())


class DriftConfigTests(unittest.TestCase):
    def test_no_warmup_for_covered_currencies(self):
        _, _, rows = real_index()
        # Toda fila con volatilidad publicada tiene tambien deriva: la historia
        # del CSV llega a 1999, asi que la ventana de 12 meses no se recorta.
        with_vol = [
            row
            for row in rows
            if row.cobertura_bce and row.vol_anualizada is not None
        ]
        self.assertTrue(with_vol)
        for row in with_vol:
            self.assertIsNotNone(
                row.deriva_componente,
                f"{row.currency} {row.month} sin deriva pese a tener volatilidad",
            )
            self.assertEqual(row.deriva_ventana_meses, DERIVA_VENTANA_MESES)

    def test_committed_coverage_exposes_both_components(self):
        coverage = json.loads(
            (fx.DEFAULT_OUTPUT_DIR / "cobertura.json").read_text(encoding="utf-8")
        )
        self.assertIn("estadistico_deriva", coverage)
        self.assertIn("distribucion_deriva_componente", coverage)
        self.assertIn("sensibilidad_corte_deriva", coverage)
        cambio = coverage["cambio_de_tramo_ultimo_mes"]
        self.assertIn("divisas", cambio)
        self.assertIn("empresas_afectadas", cambio)
        # BRL no puede aparecer como castigada por el cambio de tramo.
        self.assertNotIn("BRL", cambio["divisas"])


if __name__ == "__main__":
    unittest.main()
