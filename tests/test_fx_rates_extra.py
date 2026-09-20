"""Tests de xray/fx_rates_extra.py (fixtures sinteticas + vendorizado real).

Cubren el criterio de hecho de la tarea F1c:

- el CSV del FMI esta vendorizado y su sha256 coincide con `PROCEDENCIA.md`;
- la serie esta anclada a EUR por construccion (`XDC_EUR`) y la direccion es la
  correcta (misma convencion que el BCE: unidades de divisa por 1 EUR),
  verificado con la evidencia interna de `clean/invoices.parquet`;
- los ordenes de magnitud cuadran con el ancla interna (CLP ~1.000, COP
  ~4.200-4.800, PEN ~3,9-4,2 por EUR) y con el EUR/USD implicito
  (`XDC_EUR / XDC_USD` ~ 1,03-1,19);
- nulo nunca es cero: un mes sin dato queda a `None` y no se interpola;
- la disciplina point-in-time no filtra informacion futura, demostrado por
  perturbacion y por truncado, y localiza el mes mas reciente utilizable en cada
  corte.
"""

import dataclasses
import functools
import unittest
from datetime import date
from pathlib import Path

from xray import paths
from xray.fx_rates_extra import (
    ANCHOR_BANDS,
    APR_VINTAGE_PATH,
    CURRENCIES,
    EUR_VENDOR_PATH,
    JAN_VINTAGE_PATH,
    PIT_LAG_MESES,
    PIT_LAG_MESES_POR_DIVISA,
    TIPO_FIN_DE_PERIODO,
    TIPO_PERIODO_MEDIO,
    USD_VENDOR_PATH,
    VENDOR_SHA256,
    FxRateExtra,
    ImfObservation,
    assert_point_in_time,
    available_at_for,
    build_coverage,
    closed_months,
    compute_fx_rates_extra,
    latest_available,
    load_observations,
    month_end_datetime,
    parse_period,
    select_point_in_time,
    sha256_file,
    source_months,
    verify_vendor,
)

INVOICES_PATH = paths.CLEAN_DIR / "invoices.parquet"

# Bandas de plausibilidad del ancla interna: el `exchange_rate` de las facturas
# contabilizadas en EUR (el unico que NO es una identidad) es un tipo de mercado
# observado, y debe parecerse al tipo mensual del FMI del mismo mes.
ANCHOR_RATIO_BANDA = (0.85, 1.15)
ANCHOR_RATIO_MEDIANA = (0.90, 1.10)
ANCHOR_FRACCION_MINIMA = 0.80
EUR_USD_IMPLICITO_BANDA = (0.95, 1.30)


def obs(country: str, period: str, value: float | None, tipo: str = "PA_RT") -> ImfObservation:
    return ImfObservation(
        country=country,
        indicator="XDC_EUR",
        tipo_imf=tipo,
        period=period,
        value=value,
        status="" if value is not None else "T",
        access_level="PUBLIC_OPEN",
    )


@functools.lru_cache(maxsize=1)
def real_rows() -> tuple[FxRateExtra, ...]:
    """Serie completa construida desde el CSV vendorizado (cacheada)."""
    observations = load_observations(EUR_VENDOR_PATH)
    return tuple(compute_fx_rates_extra(observations))


@functools.lru_cache(maxsize=1)
def real_usd_observations() -> tuple[ImfObservation, ...]:
    return tuple(load_observations(USD_VENDOR_PATH))


def by_key(rows) -> dict[tuple[str, date, str], FxRateExtra]:
    return {(r.currency, r.month, r.tipo): r for r in rows}


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


class VendorTest(unittest.TestCase):
    """El fichero crudo es el auditado: si cambia, todo lo demas deja de valer."""

    def test_sha256_declarados_y_presentes(self):
        declared = set(VENDOR_SHA256)
        self.assertEqual(
            declared,
            {
                "imf_er_xdc_eur_monthly.csv",
                "imf_er_xdc_usd_monthly.csv",
                "imf_er_vintage_2026_jan.csv",
                "imf_er_vintage_2026_apr.csv",
            },
        )
        for name in declared:
            path = EUR_VENDOR_PATH.parent / name
            self.assertTrue(path.exists(), f"falta el fichero vendorizado {name}")

    def test_sha256_coincide_con_procedencia(self):
        for path in (
            EUR_VENDOR_PATH,
            USD_VENDOR_PATH,
            JAN_VINTAGE_PATH,
            APR_VINTAGE_PATH,
        ):
            with self.subTest(path=path.name):
                self.assertEqual(verify_vendor(path), VENDOR_SHA256[path.name])
                self.assertEqual(sha256_file(path), VENDOR_SHA256[path.name])

    def test_vendor_aborta_si_el_sha256_no_coincide(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "imf_er_xdc_eur_monthly.csv"
            fake.write_text("COUNTRY,INDICATOR\nARG,XDC_EUR\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_vendor(fake)

    def test_procedencia_declara_la_fuente_y_la_licencia(self):
        text = (EUR_VENDOR_PATH.parent / "PROCEDENCIA.md").read_text(encoding="utf-8")
        for needle in (
            "api.imf.org",
            "XDC_EUR",
            "PUBLIC_OPEN",
            "XDC_USD",
            "sha256",
        ):
            self.assertIn(needle, text)

    def test_metadatos_de_acceso_publico_en_todas_las_filas(self):
        rows = list(load_observations(EUR_VENDOR_PATH))
        self.assertTrue(rows)
        self.assertEqual({r.access_level for r in rows}, {"PUBLIC_OPEN"})
        self.assertEqual({r.indicator for r in rows}, {"XDC_EUR"})
        self.assertEqual({r.country for r in rows}, set(CURRENCIES))


class PureComputationTest(unittest.TestCase):
    """El motor es puro y trata el nulo como nulo, nunca como cero."""

    def test_parse_period(self):
        self.assertEqual(parse_period("2024-M09"), date(2024, 9, 1))
        self.assertEqual(parse_period("2026-M12"), date(2026, 12, 1))
        with self.assertRaises(ValueError):
            parse_period("2026-08")

    def test_nulo_nunca_cero_y_sin_interpolar(self):
        observations = [
            obs("CHL", "2024-M09", 1000.0),
            obs("CHL", "2024-M10", None),
            obs("CHL", "2024-M11", 1100.0),
        ]
        months = [date(2024, 9, 1), date(2024, 10, 1), date(2024, 11, 1)]
        rows = compute_fx_rates_extra(
            observations,
            currencies=("CLP",),
            months=months,
            tipos=(TIPO_PERIODO_MEDIO,),
        )
        table = {(r.month, r.tipo): r.rate_por_eur for r in rows}
        self.assertEqual(table[(date(2024, 9, 1), TIPO_PERIODO_MEDIO)], 1000.0)
        self.assertIsNone(table[(date(2024, 10, 1), TIPO_PERIODO_MEDIO)])
        self.assertEqual(table[(date(2024, 11, 1), TIPO_PERIODO_MEDIO)], 1100.0)
        # No se ha arrastrado el mes anterior ni se ha puesto cero.
        self.assertNotIn(0.0, [v for v in table.values() if v is not None])
        self.assertEqual(len(rows), 3)

    def test_mes_ausente_de_la_fuente_se_publica_nulo(self):
        observations = [obs("COL", "2024-M09", 4500.0)]
        months = [date(2024, 9, 1), date(2024, 10, 1)]
        rows = compute_fx_rates_extra(
            observations, currencies=("COP",), months=months
        )
        table = by_key(rows)
        self.assertEqual(table[("COP", date(2024, 9, 1), TIPO_PERIODO_MEDIO)].rate_por_eur, 4500.0)
        self.assertIsNone(
            table[("COP", date(2024, 10, 1), TIPO_PERIODO_MEDIO)].rate_por_eur
        )

    def test_ambos_tipos_se_publican_por_separado(self):
        observations = [
            obs("PER", "2024-M09", 4.10, "PA_RT"),
            obs("PER", "2024-M09", 4.12, "EOP_RT"),
        ]
        rows = compute_fx_rates_extra(
            observations, currencies=("PEN",), months=[date(2024, 9, 1)]
        )
        table = by_key(rows)
        self.assertEqual(table[("PEN", date(2024, 9, 1), TIPO_PERIODO_MEDIO)].rate_por_eur, 4.10)
        self.assertEqual(table[("PEN", date(2024, 9, 1), TIPO_FIN_DE_PERIODO)].rate_por_eur, 4.12)

    def test_observaciones_contradictorias_abortan(self):
        observations = [
            obs("CHL", "2024-M09", 1000.0),
            obs("CHL", "2024-M09", 2000.0),
        ]
        with self.assertRaises(ValueError):
            compute_fx_rates_extra(
                observations, currencies=("CLP",), months=[date(2024, 9, 1)]
            )

    def test_no_muta_la_entrada(self):
        observations = [obs("CHL", "2024-M09", 1000.0)]
        snapshot = list(observations)
        compute_fx_rates_extra(
            observations, currencies=("CLP",), months=[date(2024, 9, 1)]
        )
        self.assertEqual(observations, snapshot)

    def test_source_months_incluye_huecos_dentro_del_rango(self):
        observations = [
            obs("CHL", "2024-M09", 1000.0),
            obs("CHL", "2024-M10", None),
            obs("CHL", "2024-M11", 1100.0),
        ]
        self.assertEqual(
            source_months(observations),
            [date(2024, 9, 1), date(2024, 10, 1), date(2024, 11, 1)],
        )

    def test_source_months_ignora_filas_sin_valor_fuera_del_rango(self):
        """Filas pre-1999 con valor vacio no estiran la rejilla publicada."""
        observations = [
            obs("PER", "1960-M02", None),
            obs("PER", "1999-M01", 5.0),
            obs("PER", "1999-M02", 5.1),
        ]
        self.assertEqual(
            source_months(observations), [date(1999, 1, 1), date(1999, 2, 1)]
        )

    def test_source_months_vacio_sin_datos(self):
        self.assertEqual(source_months([obs("PER", "1960-M02", None)]), [])


class PointInTimeTest(unittest.TestCase):
    """La disponibilidad se modela con evidencia y no filtra el futuro."""

    def synthetic(self) -> list[FxRateExtra]:
        months = [
            date(2024, 6, 1),
            date(2024, 7, 1),
            date(2024, 8, 1),
            date(2024, 9, 1),
            date(2024, 10, 1),
        ]
        observations = [
            obs("CHL", f"{m.year}-M{m.month:02d}", 1000.0 + i)
            for i, m in enumerate(months)
        ]
        return compute_fx_rates_extra(
            observations, currencies=("CLP",), months=months
        )

    def test_available_at_es_el_fin_del_mes_mas_el_lag(self):
        month = date(2025, 3, 1)
        row = FxRateExtra(
            currency="CLP",
            month=month,
            tipo=TIPO_PERIODO_MEDIO,
            rate_por_eur=1000.0,
            country_iso3="CHL",
            en_ventana=True,
            available_at=available_at_for(month, "CLP"),
            fuente="test",
            status_obs="",
            acceso="PUBLIC_OPEN",
        )
        self.assertEqual(row.available_at, month_end_datetime(date(2025, 5, 1)))
        self.assertGreaterEqual(row.available_at, month_end_datetime(month))

    def test_cop_tiene_mas_retardo_que_el_resto(self):
        month = date(2025, 3, 1)
        self.assertEqual(
            available_at_for(month, "COP"),
            month_end_datetime(date(2025, 3 + PIT_LAG_MESES_POR_DIVISA["COP"], 1)),
        )
        for currency in ("ARS", "CLP", "PEN"):
            self.assertLess(
                available_at_for(month, currency), available_at_for(month, "COP")
            )
        self.assertEqual(PIT_LAG_MESES, 2)

    def test_assert_point_in_time_rechaza_disponibilidad_pasada(self):
        row = FxRateExtra(
            currency="CLP",
            month=date(2025, 3, 1),
            tipo=TIPO_PERIODO_MEDIO,
            rate_por_eur=1000.0,
            country_iso3="CHL",
            en_ventana=True,
            available_at=month_end_datetime(date(2025, 2, 1)),
            fuente="test",
            status_obs="",
            acceso="PUBLIC_OPEN",
        )
        with self.assertRaises(AssertionError):
            assert_point_in_time([row])

    def test_assert_point_in_time_rechaza_tipo_cero(self):
        month = date(2025, 3, 1)
        row = FxRateExtra(
            currency="CLP",
            month=month,
            tipo=TIPO_PERIODO_MEDIO,
            rate_por_eur=0.0,
            country_iso3="CHL",
            en_ventana=True,
            available_at=available_at_for(month, "CLP"),
            fuente="test",
            status_obs="",
            acceso="PUBLIC_OPEN",
        )
        with self.assertRaises(AssertionError):
            assert_point_in_time([row])

    def test_no_fuga_por_truncado(self):
        rows = self.synthetic()
        as_of_oct = month_end_datetime(date(2024, 10, 1))
        as_of_dic = month_end_datetime(date(2024, 12, 1))
        early = select_point_in_time(rows, as_of_oct)
        late = select_point_in_time(rows, as_of_dic)
        self.assertTrue(set(early).issubset(set(late)))
        # Con lag 2, en el cierre de octubre esta publicado hasta agosto:
        # {junio, julio, agosto}. Nunca nada posterior.
        self.assertEqual(
            {r.month for r in early},
            {date(2024, 6, 1), date(2024, 7, 1), date(2024, 8, 1)},
        )
        self.assertEqual(
            {r.month for r in late},
            {
                date(2024, 6, 1),
                date(2024, 7, 1),
                date(2024, 8, 1),
                date(2024, 9, 1),
                date(2024, 10, 1),
            },
        )

    def test_no_fuga_por_perturbacion(self):
        rows = self.synthetic()
        as_of = month_end_datetime(date(2024, 10, 1))
        baseline = select_point_in_time(rows, as_of)
        perturbed = [
            dataclasses.replace(row, rate_por_eur=1e9)
            if row.month >= date(2024, 9, 1)
            else row
            for row in rows
        ]
        self.assertEqual(select_point_in_time(perturbed, as_of), baseline)

    def test_latest_available_encuentra_el_mes_mas_reciente_publicado(self):
        rows = self.synthetic()
        as_of = month_end_datetime(date(2024, 12, 1))
        latest = latest_available(rows, "CLP", TIPO_PERIODO_MEDIO, as_of)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.month, date(2024, 10, 1))
        # Un corte demasiado pronto no tiene nada: None, no un valor imputado.
        self.assertIsNone(
            latest_available(
                rows, "CLP", TIPO_PERIODO_MEDIO, month_end_datetime(date(2024, 1, 1))
            )
        )

    def test_perturbacion_de_un_mes_futuro_no_cambia_el_pasado(self):
        rows = real_rows()
        as_of = month_end_datetime(date(2025, 6, 1))
        baseline = select_point_in_time(list(rows), as_of)
        perturbed = [
            dataclasses.replace(row, rate_por_eur=1e9)
            if row.month > date(2025, 6, 1)
            else row
            for row in rows
        ]
        self.assertEqual(select_point_in_time(perturbed, as_of), baseline)


class RealSeriesTest(unittest.TestCase):
    """Anclas sobre el fichero vendorizado real (sin red)."""

    def setUp(self):
        self.rows = list(real_rows())
        self.table = by_key(self.rows)

    def test_rejilla_completa_y_ventana_marcada(self):
        window = closed_months()
        self.assertEqual(window[0], date(2024, 9, 1))
        self.assertEqual(window[-1], date(2026, 8, 1))
        self.assertEqual(len(window), 24)
        for currency in CURRENCIES.values():
            for tipo in (TIPO_PERIODO_MEDIO, TIPO_FIN_DE_PERIODO):
                for month in window:
                    row = self.table[(currency, month, tipo)]
                    self.assertTrue(row.en_ventana)
        # La historia previa tambien se publica (hace falta para el PIT).
        self.assertIn(("CLP", date(1999, 1, 1), TIPO_PERIODO_MEDIO), self.table)

    def test_cobertura_ventana_y_hueco_declarado(self):
        window = closed_months()
        expected_missing = {"COP": {"2026-M08"}}
        for currency in CURRENCIES.values():
            missing = {
                f"{m.year}-M{m.month:02d}"
                for m in window
                if self.table[(currency, m, TIPO_PERIODO_MEDIO)].rate_por_eur is None
            }
            with self.subTest(currency=currency):
                self.assertEqual(missing, expected_missing.get(currency, set()))

    def test_ninguna_fila_con_tipo_cero_o_invertido(self):
        assert_point_in_time(self.rows)
        for row in self.rows:
            if row.rate_por_eur is not None:
                self.assertGreater(row.rate_por_eur, 0.0)

    def test_ordenes_de_magnitud_por_divisa_en_la_ventana(self):
        for currency, (low, high) in ANCHOR_BANDS.items():
            values = [
                r.rate_por_eur
                for r in self.rows
                if r.currency == currency and r.en_ventana and r.observado
            ]
            with self.subTest(currency=currency):
                self.assertTrue(values)
                self.assertGreaterEqual(min(values), low)
                self.assertLessEqual(max(values), high)

    def test_eur_a_usd_implicito_es_de_mercado(self):
        """`XDC_EUR / XDC_USD` debe reproducir el EUR/USD (~1,03-1,19)."""
        usd = {
            (r.country, r.month): r.value
            for r in real_usd_observations()
            if r.indicator == "XDC_USD" and r.value is not None
        }
        checked = 0
        for row in self.rows:
            if not row.en_ventana or row.tipo != TIPO_PERIODO_MEDIO:
                continue
            if row.rate_por_eur is None:
                continue
            u = usd.get((row.country_iso3, row.month))
            if u is None:
                continue
            implied = row.rate_por_eur / u
            with self.subTest(currency=row.currency, month=row.month):
                self.assertGreaterEqual(implied, EUR_USD_IMPLICITO_BANDA[0])
                self.assertLessEqual(implied, EUR_USD_IMPLICITO_BANDA[1])
            checked += 1
        self.assertGreater(checked, 80)

    def test_cobertura_point_in_time_del_ultimo_corte(self):
        rows = list(real_rows())
        coverage = build_coverage(rows, closed_months())
        last = coverage["disponible_al_ultimo_corte"]
        self.assertEqual(last["corte"], "2026-M08")
        self.assertEqual(last["CLP"]["pa_rt"], "2026-M06")
        self.assertEqual(last["PEN"]["pa_rt"], "2026-M06")
        self.assertEqual(last["ARS"]["pa_rt"], "2026-M06")
        self.assertEqual(last["COP"]["pa_rt"], "2026-M04")
        # El tipo del propio mes del corte nunca es utilizable en el corte.
        for currency in CURRENCIES.values():
            self.assertNotEqual(last[currency]["pa_rt"], "2026-M08")

    def test_primer_corte_de_la_rejilla_ya_tiene_tipo_utilizable(self):
        rows = list(real_rows())
        as_of = month_end_datetime(date(2024, 9, 1))
        # Con lag 2 el corte de 2024-09 usa 2024-07; COP, con lag 4, usa 2024-05.
        esperado = {"ARS": date(2024, 7, 1), "CLP": date(2024, 7, 1), "PEN": date(2024, 7, 1),
                    "COP": date(2024, 5, 1)}
        for currency in CURRENCIES.values():
            latest = latest_available(rows, currency, TIPO_PERIODO_MEDIO, as_of)
            with self.subTest(currency=currency):
                self.assertIsNotNone(latest)
                self.assertEqual(latest.month, esperado[currency])


class AnchorInternalEvidenceTest(unittest.TestCase):
    """Ancla de plausibilidad contra la evidencia interna que SI tenemos.

    `clean/invoices.parquet` con `accounting_currency_norm = 'EUR'` es el unico
    `exchange_rate` que no es una identidad: son facturas contabilizadas en EUR
    cuya divisa original era ARS/COP/CLP/PEN, con el tipo de mercado anotado.
    """

    @classmethod
    def setUpClass(cls):
        if not INVOICES_PATH.exists():
            raise unittest.SkipTest(f"no existe {INVOICES_PATH}")
        try:
            import duckdb
        except ImportError:  # pragma: no cover - entorno sin duckdb
            raise unittest.SkipTest("duckdb no disponible")
        cls.con = duckdb.connect(":memory:")
        cls.rows = cls.con.execute(
            f"""
            SELECT currency_norm,
                   strftime(issuance_date_ok, '%Y-M%m') AS period,
                   exchange_rate
            FROM read_parquet('{INVOICES_PATH}')
            WHERE accounting_currency_norm = 'EUR'
              AND currency_norm IN ('ARS', 'COP', 'CLP', 'PEN')
            """
        ).fetchall()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "con"):
            cls.con.close()

    def test_no_hay_ars_contabilizado_en_eur(self):
        self.assertEqual([r for r in self.rows if r[0] == "ARS"], [])

    def test_hay_evidencia_interna_para_las_otras_tres(self):
        present = {r[0] for r in self.rows}
        self.assertEqual(present, {"COP", "CLP", "PEN"})

    def _filas_no_degeneradas(self):
        """Filas internas cuyo tipo no esta a mas de 10x de la mediana de su divisa.

        Es un criterio puramente INTERNO (no usa el FMI) que aisla la unica fila
        degenerada conocida: una factura de 0,50 EUR con `exchange_rate = 50` en
        COP, dos ordenes de magnitud por debajo de las otras diez observaciones
        COP. No se descarta ninguna otra fila.
        """
        por_divisa: dict[str, list[float]] = {}
        for currency, _period, rate in self.rows:
            if rate is not None and rate > 0:
                por_divisa.setdefault(currency, []).append(rate)
        medianas = {c: median(v) for c, v in por_divisa.items()}
        limpias = []
        for row in self.rows:
            currency, _period, rate = row
            if rate is None or rate <= 0:
                continue
            base = medianas[currency]
            if 0.1 * base <= rate <= 10.0 * base:
                limpias.append(row)
        return limpias

    def test_descarta_solo_la_fila_degenerada_conocida(self):
        self.assertEqual(len(self.rows) - len(self._filas_no_degeneradas()), 1)
        descartada = next(
            row for row in self.rows if row not in self._filas_no_degeneradas()
        )
        self.assertEqual(descartada[0], "COP")
        self.assertEqual(descartada[2], 50.0)

    def test_ratios_cuadran_con_el_orden_de_magnitud(self):
        table = by_key(real_rows())
        ratios: dict[str, list[float]] = {}
        for currency, period, rate in self._filas_no_degeneradas():
            if rate is None or rate <= 0:
                continue
            row = table.get((currency, parse_period(period), TIPO_PERIODO_MEDIO))
            if row is None or row.rate_por_eur is None:
                continue
            ratios.setdefault(currency, []).append(rate / row.rate_por_eur)
        self.assertEqual(set(ratios), {"COP", "CLP", "PEN"})
        for currency, values in ratios.items():
            with self.subTest(currency=currency):
                within = sum(
                    1
                    for v in values
                    if ANCHOR_RATIO_BANDA[0] <= v <= ANCHOR_RATIO_BANDA[1]
                )
                self.assertGreaterEqual(within / len(values), ANCHOR_FRACCION_MINIMA)
                med = median(values)
                self.assertGreaterEqual(med, ANCHOR_RATIO_MEDIANA[0])
                self.assertLessEqual(med, ANCHOR_RATIO_MEDIANA[1])

    def test_la_direccion_no_esta_invertida(self):
        """Si la fuente cotizara al reves, el ratio interno seria ~1000x o ~0,001x."""
        table = by_key(real_rows())
        for currency, period, rate in self._filas_no_degeneradas():
            if rate is None or rate <= 0:
                continue
            row = table.get((currency, parse_period(period), TIPO_PERIODO_MEDIO))
            if row is None or row.rate_por_eur is None:
                continue
            ratio = rate / row.rate_por_eur
            with self.subTest(currency=currency):
                self.assertGreater(ratio, 0.05)
                self.assertLess(ratio, 20.0)


class ReportArtifactsTest(unittest.TestCase):
    """Los artefactos de la tarea existen y tienen el grano declarado."""

    def test_parquet_y_reporte(self):
        parquet = paths.ROOT / "reports" / "fx_risk" / "rates_extra.parquet"
        report = paths.ROOT / "reports" / "fx_risk" / "report_rates_extra.md"
        self.assertTrue(parquet.exists(), "falta reports/fx_risk/rates_extra.parquet")
        self.assertTrue(report.exists(), "falta reports/fx_risk/report_rates_extra.md")
        try:
            import duckdb
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("duckdb no disponible")
        con = duckdb.connect(":memory:")
        try:
            columns = {
                row[0]
                for row in con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{parquet}')"
                ).fetchall()
            }
            assert columns >= {
                "currency",
                "month",
                "tipo",
                "rate_por_eur",
                "available_at",
                "en_ventana",
                "fuente",
            }
            n, currencies = con.execute(
                f"SELECT count(*), count(DISTINCT currency) "
                f"FROM read_parquet('{parquet}')"
            ).fetchone()
            self.assertGreater(n, 0)
            self.assertEqual(currencies, 4)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
