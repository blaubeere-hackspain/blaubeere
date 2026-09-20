"""Segunda fuente de tipos de cambio EUR/divisa para ARS, COP, CLP y PEN (F1c).

QUE ES ESTA CAPA
----------------
Modulo NUEVO y aditivo. Trae una fuente EXTERNA vendorizada distinta del BCE
(los tipos del *International Financial Statistics* del FMI, fichero
`data/fx_extra/imf_er_xdc_eur_monthly.csv`) y la convierte en una serie mensual
EUR/divisa con disciplina point-in-time para las cuatro divisas de la cartera
viva que el BCE **no** publica: ARS, COP, CLP y PEN.

NO toca `data/fx_ecb/` ni `xray/fx_index.py` (son de F1/F1b), NO toca
`xray/fx_exposure.py` ni `reports/fx_risk/exposicion*` (son de F2), NO valora
facturas y NO toca la nota (eso es F3). Es una fuente de DATOS, no un indice.

POR QUE HACE FALTA
------------------
El BCE no cotiza ARS, COP, CLP ni PEN. El `exchange_rate` interno de las
facturas en esas divisas vale 1,00 cuando estan contabilizadas en su propia
moneda: es una identidad, no un tipo. Sin una segunda fuente esas divisas no se
pueden valorar en EUR y su peso relativo dentro de una empresa con mezcla de
divisas queda indeterminado.

LA FUENTE ELEGIDA (y por que)
-----------------------------
FMI, *Exchange Rates (ER)* del IFS, dataflow `IMF.STA:ER(4.0.1)`, indicador
`XDC_EUR` = **unidades de moneda nacional por 1 EUR**. Ventajas decisivas:

1. Es **directamente EUR/divisa**: NO hay que encadenar EUR/USD x USD/XXX, con
   lo que desaparece la ambiguedad de convencion. (Se vendoriza ademas `XDC_USD`
   solo para verificar el anclaje: `XDC_EUR / XDC_USD` debe reproducir el
   EUR/USD de mercado. Medido en la ventana: 1,0354-1,1824.)
2. Es una **unica** fuente para las cuatro divisas (URL, licencia y `sha256`
   unicos), a diferencia de encadenar cuatro bancos centrales.
3. Acceso declarado `PUBLIC_OPEN` en cada fila (`ACCESS_SHARING_LEVEL`), con
   atribucion al FMI. Ver `data/fx_extra/PROCEDENCIA.md`.
4. Historia larga (1999-M01 en `XDC_EUR`; 1957 en `XDC_USD`) y sin huecos dentro
   de la ventana salvo COP 2026-M08 (que se declara ausente, no se interpola).
5. Publica **vintages** con fecha de publicacion real, lo que permite medir (no
   suponer) el retardo con que cada mes pasa a estar disponible.

Fuentes descartadas, con motivo (detalle en `reports/fx_risk/report_rates_extra.md`):

- **BCRA (AR)**: API abierta (`api.bcra.gob.ar`), pero solo resuelve una fecha
  por peticion (`?fecha=`); los parametros de rango `fechadesde`/`fechahasta`
  devuelven 400, y cubrir 2024-09..2026-08 exigiria ~500 descargas, lo que es
  fragil para un fichero crudo auditable. Solo trae ARS, no las otras tres.
- **BCCh (CL)**: la API de la Base de Datos Estadisticos (`si3.bcentral.cl`)
  exige usuario y token, es decir, no es reproducible sin credenciales.
- **BanRep / datos.gov.co (CO)**: TRM diaria abierta y con licencia CC-BY-SA,
  buena candidata, pero solo COP y obligaria a mezclar cuatro licencias y
  cuatro convenciones de descarga. Se descarta por simplicidad, no por calidad.
- **BCRP (PE)**: API publica que funciona (USD/PEN diario), mismo argumento que
  BanRep: una fuente, una divisa, cuatro licencias.
- **FRED (St. Louis Fed)**: solo tiene serie mensual de Chile
  (`CCUSMA02CLM618N`); Colombia, Peru y Argentina dan 404, y las series diarias
  del H.10 para estas divisas estan retiradas.
- **Agregadores de tipo de cambio** (exchangerate.host, mindicador.cl, etc.):
  sin licencia clara y sin garantia de historia; descartados por el requisito 1.

CONVENCION Y DIRECCION
----------------------
`rate_por_eur` es **unidades de divisa por 1 EUR**, la MISMA convencion que el
BCE (`USD = 1.146` significa 1 EUR = 1,146 USD). El test ancla comprueba que los
ordenes de magnitud cuadran con la evidencia interna de las facturas
contabilizadas en EUR (`accounting_currency_norm = 'EUR'`): CLP ~1.000-1.100,
COP ~4.200-4.700, PEN ~3,9-4,0 por EUR.

NULO NUNCA ES CERO
------------------
Un mes sin dato (`OBS_VALUE` vacio en el fichero crudo, o mes inexistente para
esa divisa) se publica con `rate_por_eur = None`. Jamas se interpola, se
arrastra el mes anterior ni se imputa. Un tipo inventado contaminaria la
valoracion de facturas reales, que es peor que no tener tipo.

DISCIPLINA POINT-IN-TIME (medida, no supuesta)
----------------------------------------------
El IFS mensual no publica el tipo del mes `m` dentro del mes `m`. El retardo se
ha medido con dos vintages reales vendorizados en `data/fx_extra/`:

- `ER_2026_JAN_VINTAGE`, publicado 2026-02-01T06:25:48Z: trae hasta 2025-M12.
- `ER_2026_APR_VINTAGE`, publicado 2026-04-27T05:24:50Z: trae 2026-M02 (`PA_RT`)
  y 2026-M03 (`EOP_RT`) para ARG/CHL/PER, pero **solo hasta 2025-M12 en COP**.

Regla adoptada (conservadora y declarada):

    available_at(fila del mes m) = ultimo instante del mes m + LAG(divisa)
    LAG = 2 meses salvo COP, que usa 4.

Consecuencia honesta: para una decision al cierre del mes `M`, el dato mas
reciente utilizable es el de `M-2` (y `M-4` en COP). Es una limitacion real de
una fuente mensual, no un defecto del modulo. `select_point_in_time` implementa
la regla para que F3 no la reconstruya, y los tests demuestran la no-fuga.

REPRODUCIBILIDAD
----------------
`compute_fx_rates_extra` es PURA: recibe las observaciones crudas ya leidas y
devuelve filas; no toca red, disco ni reloj. `main` verifica el `sha256` del CSV
vendorizado contra el declarado y regenera el parquet y el informe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from xray import paths
from xray.period import FIRST_MONTH, LAST_MONTH

__all__ = [
    "CURRENCIES",
    "PIT_LAG_MESES",
    "PIT_LAG_MESES_POR_DIVISA",
    "TIPOS",
    "FxRateExtra",
    "ImfObservation",
    "available_at_for",
    "build_coverage",
    "compute_fx_rates_extra",
    "latest_available",
    "load_observations",
    "main",
    "parse_period",
    "select_point_in_time",
    "source_months",
    "verify_vendor",
]

# --- Fuente vendorizada -----------------------------------------------------

VENDOR_DIR = paths.ROOT / "data" / "fx_extra"
EUR_VENDOR_PATH = VENDOR_DIR / "imf_er_xdc_eur_monthly.csv"
USD_VENDOR_PATH = VENDOR_DIR / "imf_er_xdc_usd_monthly.csv"
JAN_VINTAGE_PATH = VENDOR_DIR / "imf_er_vintage_2026_jan.csv"
APR_VINTAGE_PATH = VENDOR_DIR / "imf_er_vintage_2026_apr.csv"
PROCEDENCIA_PATH = VENDOR_DIR / "PROCEDENCIA.md"

IMF_DATAFLOW = "IMF.STA:ER(4.0.1)"
IMF_INDICATOR_EUR = "XDC_EUR"
IMF_INDICATOR_USD = "XDC_USD"

# sha256 de cada fichero crudo; declarados tambien en PROCEDENCIA.md. Si no
# coinciden, `main` aborta: no se construye nada sobre una fuente no auditada.
VENDOR_SHA256 = {
    "imf_er_xdc_eur_monthly.csv": (
        "db424ef49706c4dd52de9dce3019134abb3489bca6478b9d108c2b38aa65b499"
    ),
    "imf_er_xdc_usd_monthly.csv": (
        "641c87d1b2574cdc62556269c13cf35340621347a56c09ec27f9a92340600a98"
    ),
    "imf_er_vintage_2026_jan.csv": (
        "20c2cd7719bf92c85f35eb709a6becdd3a3070dde67b07c8a0bc1dfc4a3d18c5"
    ),
    "imf_er_vintage_2026_apr.csv": (
        "09e0fcdf13d91a1b5179a0343b520714023a6fe41429685c6e19f7d587a8ab43"
    ),
}

# Codigo ISO3 del FMI -> divisa de la cartera.
CURRENCIES = {"ARG": "ARS", "COL": "COP", "CHL": "CLP", "PER": "PEN"}
ISO3_BY_CURRENCY = {v: k for k, v in CURRENCIES.items()}

# Tipo de transformacion del FMI -> nombre legible.
TIPO_PERIODO_MEDIO = "periodo_medio"
TIPO_FIN_DE_PERIODO = "fin_de_periodo"
TIPO_BY_IMF = {"PA_RT": TIPO_PERIODO_MEDIO, "EOP_RT": TIPO_FIN_DE_PERIODO}
TIPOS = (TIPO_PERIODO_MEDIO, TIPO_FIN_DE_PERIODO)

# --- Disponibilidad point-in-time (medida con los vintages) ------------------

# Meses de retardo entre el mes de referencia y la disponibilidad garantizada.
PIT_LAG_MESES = 2
PIT_LAG_MESES_POR_DIVISA = {"COP": 4}

EVIDENCIA_VINTAGES = (
    {
        "vintage": "ER_2026_JAN_VINTAGE",
        "publicado": "2026-02-01T06:25:48Z",
        "ultimo_mes_pa_rt": "2025-M12",
        "ultimo_mes_eop_rt": "2025-M12",
        "ultimo_mes_pa_rt_cop": "2025-M12",
    },
    {
        "vintage": "ER_2026_APR_VINTAGE",
        "publicado": "2026-04-27T05:24:50Z",
        "ultimo_mes_pa_rt": "2026-M02",
        "ultimo_mes_eop_rt": "2026-M03",
        "ultimo_mes_pa_rt_cop": "2025-M12",
    },
)

MOTIVO_VINTAGE = (
    "available_at modelado como el ultimo instante del mes m+LAG. LAG=2 medido "
    "en los vintages ER_2026_JAN_VINTAGE (pub 2026-02-01, trae hasta 2025-M12) "
    "y ER_2026_APR_VINTAGE (pub 2026-04-27, trae 2026-M02 PA_RT / 2026-M03 "
    "EOP_RT), y LAG=4 en COP porque el vintage de abril de 2026 seguia anclado "
    "en 2025-M12 para Colombia. Nunca una fila se declara disponible antes de "
    "ese instante."
)

FUENTE = "imf_ifs_er_xdc_eur"

DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "fx_risk"
REPORT_NAME = "report_rates_extra.md"
PARQUET_NAME = "rates_extra.parquet"

# Bandas plausibles de orden de magnitud usadas por el test ancla, derivadas de
# la evidencia interna (ver report_rates_extra.md). Se declaran aqui para que el
# test y el informe usen exactamente la misma definicion.
ANCHOR_BANDS = {
    "ARS": (900.0, 4000.0),
    "COP": (3500.0, 5200.0),
    "CLP": (900.0, 1300.0),
    "PEN": (3.3, 4.5),
}


# --- Modelo -----------------------------------------------------------------


@dataclass(frozen=True)
class ImfObservation:
    """Una observacion cruda del dataflow ER del FMI."""

    country: str
    indicator: str
    tipo_imf: str
    period: str
    value: float | None
    status: str = ""
    access_level: str = ""

    @property
    def currency(self) -> str:
        return CURRENCIES[self.country]

    @property
    def month(self) -> date:
        return parse_period(self.period)


@dataclass(frozen=True)
class FxRateExtra:
    """Fila divisa-mes-tipo de la serie EUR/divisa del FMI."""

    currency: str
    month: date
    tipo: str
    rate_por_eur: float | None
    country_iso3: str
    en_ventana: bool
    available_at: datetime
    fuente: str
    status_obs: str
    acceso: str

    @property
    def observado(self) -> bool:
        return self.rate_por_eur is not None

    def to_row(self) -> dict:
        return {
            "currency": self.currency,
            "month": self.month,
            "tipo": self.tipo,
            "rate_por_eur": self.rate_por_eur,
            "country_iso3": self.country_iso3,
            "en_ventana": self.en_ventana,
            "available_at": self.available_at,
            "fuente": self.fuente,
            "status_obs": self.status_obs,
            "acceso": self.acceso,
        }


# --- Utilidades de calendario ----------------------------------------------


def parse_period(period: str) -> date:
    """`2026-M08` -> `date(2026, 8, 1)`. Falla si el formato no es el esperado."""
    year_text, _, month_text = period.partition("-M")
    if not month_text:
        raise ValueError(f"periodo FMI no reconocido: {period!r}")
    return date(int(year_text), int(month_text), 1)


def format_period(month: date) -> str:
    return f"{month.year}-M{month.month:02d}"


def month_end(month: date) -> date:
    """Ultimo dia natural del mes de `month`."""
    first_next = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def month_end_datetime(month: date) -> datetime:
    return datetime.combine(month_end(month), time(23, 59, 59))


def shift_month(month: date, delta: int) -> date:
    """Primer dia del mes desplazado `delta` meses (delta puede ser negativo)."""
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def closed_months(start: date = FIRST_MONTH, end: date = LAST_MONTH) -> list[date]:
    """Mes cerrado inicial de cada mes de la rejilla, ambos inclusive."""
    months: list[date] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = shift_month(cursor, 1)
    return months


def pit_lag_meses(currency: str) -> int:
    """Retardo de publicacion (meses) de una divisa; ver EVIDENCIA_VINTAGES."""
    return PIT_LAG_MESES_POR_DIVISA.get(currency, PIT_LAG_MESES)


def available_at_for(month: date, currency: str) -> datetime:
    """Instante a partir del cual la fila del mes `month` es utilizable.

    Es el ultimo instante del mes `month + LAG(divisa)`. Conservador respecto a
    los dos vintages medidos (que publican el valor dentro de `m+2`, y en COP
    mas tarde). Nunca se declara disponible el valor del propio mes `m`.
    """
    return month_end_datetime(shift_month(month, pit_lag_meses(currency)))


def is_available_at(row: FxRateExtra, as_of: datetime) -> bool:
    """Una fila es utilizable en `as_of` si ya estaba publicada y tiene dato."""
    return row.observado and row.available_at <= as_of


def select_point_in_time(rows: list[FxRateExtra], as_of: datetime) -> list[FxRateExtra]:
    """Filas utilizables en `as_of`: observadas y publicadas antes del corte.

    Es el filtro que debe usar F3. Un mes del que no habia dato publicado en
    `as_of` NO aparece; jamas se adelanta un valor futuro ni se rellena.
    """
    return [row for row in rows if is_available_at(row, as_of)]


def latest_available(
    rows: list[FxRateExtra],
    currency: str,
    tipo: str,
    as_of: datetime,
) -> FxRateExtra | None:
    """Fila mas reciente de (divisa, tipo) utilizable en `as_of`; None si no hay.

    None es un resultado legitimo: significa "a ese corte no teniamos tipo". No
    se devuelve nunca una fila de un mes posterior ni un valor imputado.
    """
    candidates = [
        row
        for row in rows
        if row.currency == currency and row.tipo == tipo and is_available_at(row, as_of)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.month)


# --- Calculo de la serie ----------------------------------------------------


def source_months(observations: list[ImfObservation]) -> list[date]:
    """Meses que abarca la serie, del primero al ultimo **con dato**, inclusive.

    El FMI emite algunas filas pre-1999 de `XDC_EUR` con `OBS_VALUE` vacio (la
    serie real arranca en 1999-M01). Esas filas no definen el rango: si se
    usaran, se publicarian decadas de nulos para una serie que no existe ahi.
    Los meses INTERMEDIOS sin dato si se incluyen, para que un hueco real dentro
    del rango quede declarado como nulo en vez de desaparecer.
    """
    valued = [obs.month for obs in observations if obs.value is not None]
    if not valued:
        return []
    first, last = min(valued), max(valued)
    months: list[date] = []
    cursor = first
    while cursor <= last:
        months.append(cursor)
        cursor = shift_month(cursor, 1)
    return months


def compute_fx_rates_extra(
    observations: list[ImfObservation],
    currencies: tuple[str, ...] = tuple(sorted(CURRENCIES.values())),
    months: list[date] | None = None,
    tipos: tuple[str, ...] = TIPOS,
) -> list[FxRateExtra]:
    """Rejilla divisa x mes x tipo a partir de las observaciones crudas del FMI.

    PURA: sin IO, sin reloj, sin estado mutable. `months=None` publica TODA la
    historia de la fuente (1999-M01 a 2026-M08), no solo la rejilla del repo:
    hace falta para que la disciplina point-in-time funcione en los primeros
    cortes (el tipo utilizable en 2024-09 se publico en 2024-09 pero mide
    2024-07). El flag `en_ventana` marca la rejilla cerrada del repo. Un mes sin
    observacion queda a `None`; no se interpola ni se arrastra el mes anterior.
    Si hay mas de una observacion para la misma (divisa, mes, tipo) se toma la
    ultima encontrada y se exige que no se contradiga (mismo valor), para no
    elegir en silencio.
    """
    grid = source_months(observations) if months is None else list(months)
    wanted_months = {m.replace(day=1) for m in grid}
    wanted_currencies = set(currencies)
    wanted_tipos = set(tipos)

    values: dict[tuple[str, date, str], ImfObservation] = {}
    for obs in observations:
        if obs.indicator != IMF_INDICATOR_EUR:
            continue
        if obs.currency not in wanted_currencies:
            continue
        tipo = TIPO_BY_IMF.get(obs.tipo_imf)
        if tipo is None or tipo not in wanted_tipos:
            continue
        key = (obs.currency, obs.month, tipo)
        previous = values.get(key)
        if previous is not None and previous.value != obs.value:
            raise ValueError(
                f"observaciones contradictorias para {key}: "
                f"{previous.value!r} != {obs.value!r}"
            )
        values[key] = obs

    rows: list[FxRateExtra] = []
    for currency in sorted(wanted_currencies):
        for month in sorted(wanted_months):
            en_ventana = FIRST_MONTH <= month <= LAST_MONTH
            for tipo in tipos:
                obs = values.get((currency, month, tipo))
                rate = obs.value if obs is not None else None
                rows.append(
                    FxRateExtra(
                        currency=currency,
                        month=month,
                        tipo=tipo,
                        rate_por_eur=rate,
                        country_iso3=ISO3_BY_CURRENCY[currency],
                        en_ventana=en_ventana,
                        available_at=available_at_for(month, currency),
                        fuente=FUENTE,
                        status_obs=obs.status if obs is not None else "",
                        acceso=obs.access_level if obs is not None else "",
                    )
                )
    return rows


def assert_point_in_time(rows: list[FxRateExtra]) -> None:
    """Ninguna fila puede declararse disponible antes del cierre de su mes.

    Es la salvaguarda minima: `available_at` nunca puede ser anterior al cierre
    del mes que mide (eso seria afirmar que el IFS publica en tiempo real).
    """
    for row in rows:
        if row.available_at < month_end_datetime(row.month):
            raise AssertionError(
                f"available_at anterior al cierre de su propio mes: "
                f"{row.currency} {row.month} {row.available_at}"
            )
        if row.rate_por_eur == 0:
            raise AssertionError(f"tipo cero (deberia ser nulo): {row}")


# --- Carga de los CSV vendorizados -----------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_vendor(path: Path) -> str:
    """Comprueba el sha256 del fichero contra el declarado en PROCEDENCIA.md."""
    expected = VENDOR_SHA256.get(path.name)
    if expected is None:
        raise ValueError(f"fichero vendorizado no declarado: {path.name}")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"sha256 de {path.name} no coincide con PROCEDENCIA.md: "
            f"{actual} != {expected}"
        )
    return actual


def load_observations(path: Path = EUR_VENDOR_PATH) -> list[ImfObservation]:
    """Lee un CSV crudo del FMI y devuelve observaciones.

    `OBS_VALUE` vacio (o no parseable) se lee como `None`, nunca como cero. Se
    conserva `STATUS` y `ACCESS_SHARING_LEVEL` para que la ausencia sea
    auditable.
    """
    observations: list[ImfObservation] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            raw = (record.get("OBS_VALUE") or "").strip()
            value = float(raw) if raw else None
            observations.append(
                ImfObservation(
                    country=(record.get("COUNTRY") or "").strip(),
                    indicator=(record.get("INDICATOR") or "").strip(),
                    tipo_imf=(record.get("TYPE_OF_TRANSFORMATION") or "").strip(),
                    period=(record.get("TIME_PERIOD") or "").strip(),
                    value=value,
                    status=(record.get("STATUS") or "").strip(),
                    access_level=(record.get("ACCESS_SHARING_LEVEL") or "").strip(),
                )
            )
    return observations


# --- Cobertura --------------------------------------------------------------


def build_coverage(rows: list[FxRateExtra], months: list[date]) -> dict:
    """Cobertura real por divisa y por fecha, y cobertura point-in-time.

    Distingue dos cosas que no son lo mismo:

    - **Cobertura de la fuente**: para cuantos meses de la rejilla hay dato
      (independientemente de cuando se publico).
    - **Cobertura point-in-time**: para cada corte (cierre de cada mes de la
      rejilla) cual es el mes mas reciente con dato ya publicado en ese corte.
      Es lo que F3 puede usar sin fuga.
    """
    window_rows = [r for r in rows if r.en_ventana]
    per_currency: dict[str, dict] = {}
    for currency in sorted({r.currency for r in rows}):
        subset = [r for r in window_rows if r.currency == currency]
        if not subset:
            continue
        observed = [r for r in subset if r.observado]
        tipos_block: dict[str, dict] = {}
        for tipo in TIPOS:
            tipo_rows = [r for r in subset if r.tipo == tipo]
            tipo_obs = [r for r in tipo_rows if r.observado]
            missing = [format_period(r.month) for r in tipo_rows if not r.observado]
            tipos_block[tipo] = {
                "meses_rejilla": len(tipo_rows),
                "meses_con_dato": len(tipo_obs),
                "meses_ausentes": missing,
                "rate_min": min((r.rate_por_eur for r in tipo_obs), default=None),
                "rate_max": max((r.rate_por_eur for r in tipo_obs), default=None),
            }
        per_currency[currency] = {
            "country_iso3": subset[0].country_iso3,
            "lag_meses": pit_lag_meses(currency),
            "meses_con_algun_dato": len({r.month for r in observed}),
            "meses_con_dato_ambos_tipos": len(
                {r.month for r in observed if r.tipo == TIPO_PERIODO_MEDIO}
                & {r.month for r in observed if r.tipo == TIPO_FIN_DE_PERIODO}
            ),
            "primer_mes": format_period(min(r.month for r in observed)) if observed else None,
            "ultimo_mes": format_period(max(r.month for r in observed)) if observed else None,
            "por_tipo": tipos_block,
        }

    # Cobertura PIT: para cada corte, el mes mas reciente utilizable.
    pit_por_corte: dict[str, dict] = {}
    for month in months:
        as_of = month_end_datetime(month)
        block: dict[str, dict] = {}
        for currency in sorted({r.currency for r in window_rows}):
            latest_pa = latest_available(rows, currency, TIPO_PERIODO_MEDIO, as_of)
            latest_eop = latest_available(rows, currency, TIPO_FIN_DE_PERIODO, as_of)
            block[currency] = {
                "pa_rt": format_period(latest_pa.month) if latest_pa else None,
                "eop_rt": format_period(latest_eop.month) if latest_eop else None,
            }
        pit_por_corte[format_period(month)] = block

    # Resumen PIT al ultimo corte de la rejilla (lo que F3 tendria de verdad).
    last_cut = months[-1]
    last_as_of = month_end_datetime(last_cut)
    disponibles_al_ultimo_corte = {
        currency: {
            "pa_rt": (
                format_period(row.month) if (row := latest_available(
                    rows, currency, TIPO_PERIODO_MEDIO, last_as_of
                )) else None
            ),
            "eop_rt": (
                format_period(row.month) if (row := latest_available(
                    rows, currency, TIPO_FIN_DE_PERIODO, last_as_of
                )) else None
            ),
        }
        for currency in sorted({r.currency for r in window_rows})
    }

    return {
        "fuente": FUENTE,
        "dataflow": IMF_DATAFLOW,
        "indicador": IMF_INDICATOR_EUR,
        "convencion": "unidades de divisa por 1 EUR (igual que el BCE)",
        "meses_rejilla": [format_period(m) for m in months],
        "regla_available_at": {
            "lag_meses_base": PIT_LAG_MESES,
            "lag_meses_por_divisa": dict(PIT_LAG_MESES_POR_DIVISA),
            "motivo": MOTIVO_VINTAGE,
            "evidencia_vintages": list(EVIDENCIA_VINTAGES),
            "efecto": (
                "para una decision al cierre del mes M el dato mas reciente "
                "utilizable es M-2 (M-4 en COP); el tipo del propio mes M no se "
                "puede usar en M sin fuga"
            ),
        },
        "por_divisa": per_currency,
        "pit_por_corte": pit_por_corte,
        "disponible_al_ultimo_corte": {
            "corte": format_period(last_cut),
            **disponibles_al_ultimo_corte,
        },
        "bandas_plausibilidad": {k: list(v) for k, v in ANCHOR_BANDS.items()},
    }


# --- Informe ----------------------------------------------------------------


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def write_report_md(coverage: dict, path: Path) -> None:
    """Informe legible con la cobertura real y la lectura honesta."""
    lines: list[str] = []
    add = lines.append
    add("# F1c · Segunda fuente EUR/divisa para ARS, COP, CLP y PEN")
    add("")
    add(
        "Serie mensual EUR/divisa del **FMI (IFS, Exchange Rates, `XDC_EUR`)** "
        "vendorizada en `data/fx_extra/`, con disciplina point-in-time. Es la "
        "segunda fuente externa (la primera es el BCE, `data/fx_ecb/`). No valora "
        "facturas ni toca la nota: eso es F3."
    )
    add("")
    add("## Que se ha traido")
    add("")
    add(f"- **Fuente**: {coverage['dataflow']}, indicador `{coverage['indicador']}`.")
    add(f"- **Convencion**: {coverage['convencion']}.")
    add("- **Fichero crudo**: `data/fx_extra/imf_er_xdc_eur_monthly.csv` (con `sha256` verificado).")
    add("- **Historia de la fuente**: 1999-M01 a 2026-M08 (`XDC_EUR`).")
    add(
        "- **Parquet publicado**: la historia COMPLETA de la fuente (divisa x mes x "
        "tipo), para que la disciplina point-in-time funcione tambien en los "
        "primeros cortes. La columna `en_ventana` marca la rejilla del repo ("
        f"{coverage['meses_rejilla'][0]} a {coverage['meses_rejilla'][-1]})."
    )
    add("")
    add("## Cobertura real en la rejilla (por divisa y por fecha)")
    add("")
    add(
        "| Divisa | ISO3 | Meses con dato (`periodo_medio`) | Meses con dato (`fin_de_periodo`) | "
        "Rango `periodo_medio` | Meses ausentes |"
    )
    add("| --- | --- | --- | --- | --- | --- |")
    for currency, block in sorted(coverage["por_divisa"].items()):
        pa = block["por_tipo"][TIPO_PERIODO_MEDIO]
        eop = block["por_tipo"][TIPO_FIN_DE_PERIODO]
        missing = ", ".join(pa["meses_ausentes"]) or "ninguno"
        rng = f"{_fmt(pa['rate_min'])} – {_fmt(pa['rate_max'])}"
        add(
            f"| {currency} | {block['country_iso3']} | "
            f"{pa['meses_con_dato']}/{pa['meses_rejilla']} | "
            f"{eop['meses_con_dato']}/{eop['meses_rejilla']} | {rng} | {missing} |"
        )
    add("")
    add("### Cobertura point-in-time (lo que F3 puede usar sin fuga)")
    add("")
    add(f"- {coverage['regla_available_at']['motivo']}")
    add("")
    add("| Corte (cierre de mes) | " + " | ".join(
        f"{c} PA" for c in sorted(coverage["por_divisa"])
    ) + " |")
    add("| --- | " + " | ".join("---" for _ in coverage["por_divisa"]) + " |")
    for mes, block in coverage["pit_por_corte"].items():
        add(
            f"| {mes} | "
            + " | ".join(
                str(block[c]["pa_rt"] or "sin dato") for c in sorted(coverage["por_divisa"])
            )
            + " |"
        )
    add("")
    last = coverage["disponible_al_ultimo_corte"]
    add(
        f"**Al ultimo corte util ({last['corte']})**, el dato `periodo_medio` mas "
        "reciente utilizable es: "
        + ", ".join(
            f"{c}={last[c]['pa_rt'] or 'sin dato'}" for c in sorted(coverage["por_divisa"])
        )
        + "."
    )
    add("")
    add("## Lectura honesta")
    add("")
    add(
        "- La fuente es **mensual**, no diaria. Valorar una factura con fecha `d` "
        "con el promedio (o el cierre) de su mes `m` es una aproximacion declarada, "
        "no el tipo de `d`."
    )
    add(
        "- El **retardo de publicacion** es real (~2 meses, 4 en COP). No se finge "
        "disponibilidad en el propio mes: `available_at` lo modela con evidencia de "
        "vintages del propio FMI."
    )
    add(
        "- Un mes sin dato queda a **nulo**; no se interpola ni se arrastra el mes "
        "anterior. Nulo nunca es cero."
    )
    add(
        "- No se usan medianas internas de `exchange_rate` ni ninguna otra fuente "
        "interna para estimar tipos."
    )
    add("")
    add("## Test ancla de plausibilidad")
    add("")
    add(
        "Contra la evidencia interna de `clean/invoices.parquet` con "
        "`accounting_currency_norm = 'EUR'` (el unico `exchange_rate` que NO es una "
        "identidad), los ordenes de magnitud cuadran. Medido: 23 filas en total "
        "(9 CLP, 11 COP, 3 PEN, **0 ARS**; el dispatch hablaba de 10 facturas, "
        "5/2/3, que no se reproduce)."
    )
    add("")
    add("| Divisa | Evidencia interna (por EUR) | Banda de plausibilidad adoptada |")
    add("| --- | --- | --- |")
    evidencia = {
        "ARS": "0 filas (no hay ancla interna; el dispatch ya lo anticipaba)",
        "COP": "11 filas, ≈ 4.244 – 4.678 (una fila degenerada de 0,50 EUR con tipo 50 se excluye)",
        "CLP": "9 filas, ≈ 1.024 – 1.110",
        "PEN": "3 filas, ≈ 3,90 – 3,96",
    }
    for currency, banda in sorted(coverage["bandas_plausibilidad"].items()):
        add(f"| {currency} | {evidencia.get(currency, 'n/a')} | {banda[0]:g} – {banda[1]:g} |")
    add("")
    add(
        "Los tests de `tests/test_fx_rates_extra.py` comprueban ademas que "
        "`XDC_EUR / XDC_USD` reproduce un EUR/USD de mercado (1,03-1,19 en la "
        "ventana) y que la serie no esta invertida."
    )
    add("")
    add("## Fuentes evaluadas y por que")
    add("")
    add(
        "Se han evaluado seis familias de fuentes antes de elegir. Criterio: los "
        "cinco requisitos duros del dispatch (URL estable y auditable, licencia "
        "clara, cobertura 2024-09..2026-08, anclaje a EUR sin ambiguedad, y no "
        "inventar ni interpolar tipos)."
    )
    add("")
    add("| Fuente | Divisa(s) | Frecuencia | Veredicto | Motivo |")
    add("| --- | --- | --- | --- | --- |")
    add(
        "| **FMI · IFS Exchange Rates (`XDC_EUR`)** | ARS, COP, CLP, PEN | mensual | **ELEGIDA** | "
        "Una sola URL y licencia `PUBLIC_OPEN`; serie **directamente EUR/divisa** "
        "(sin cadena); historia 1999-M01..2026-M08; vintages con fecha de "
        "publicacion real; ordenes de magnitud validados contra la evidencia interna. |"
    )
    add(
        "| BCRA (AR) `api.bcra.gob.ar` | ARS (+otras, via ARS) | diaria | descartada | "
        "Solo resuelve una fecha por peticion (`?fecha=`); `fechadesde`/`fechahasta` "
        "devuelven 400. Cubrir la ventana exigiria ~500 descargas y no daria las "
        "otras tres divisas. |"
    )
    add(
        "| BCCh (CL) `si3.bcentral.cl` | CLP | diaria | descartada | "
        "La API de la Base de Datos Estadisticos exige usuario y **token**: no es "
        "reproducible sin credenciales (falla el requisito de auditabilidad). |"
    )
    add(
        "| BanRep vía `datos.gov.co` (CO) | COP | diaria | descartada (buena) | "
        "TRM diaria abierta y con licencia CC-BY-SA; se descarta por simplicidad: "
        "obligaria a mezclar cuatro fuentes y cuatro licencias. |"
    )
    add(
        "| BCRP (PE) `estadisticas.bcrp.gob.pe` | PEN | diaria | descartada (buena) | "
        "API publica que funciona (USD/PEN); mismo argumento: una divisa y una "
        "licencia por fuente. |"
    )
    add(
        "| FRED (St. Louis Fed) | CLP (solo) | mensual | descartada | "
        "Solo existe `CCUSMA02CLM618N`; COL/PER/ARG dan 404 y las series diarias "
        "del H.10 para estas divisas estan retiradas. |"
    )
    add(
        "| Agregadores (exchangerate.host, mindicador.cl, ...) | varias | diaria | descartados | "
        "Sin licencia clara ni garantia de historia; incumplen el requisito 1. |"
    )
    add("")
    add("## Verificacion de las cifras del dispatch (universo de empresas)")
    add("")
    add(
        "El dispatch afirma que ARS/COP/CLP/PEN afectan a 20 empresas de la cartera "
        "viva de cobros, 4 solo-opacas y 16 con mezcla. **No se reproduce con el "
        "universo de F2** (`flow_side='inflow'`, `document_type_norm='invoice'`, sin "
        "`cancel`, emitida hasta el cierre y no pagada al cierre):"
    )
    add("")
    add("| Definicion | Empresas | Solo opacas | Con mezcla |")
    add("| --- | --- | --- | --- |")
    add(
        "| F2 point-in-time, ultimo corte 2026-08 | **13** | 4 | 9 |"
    )
    add(
        "| F2 point-in-time, alguna vez en la ventana | **13** | 7 | 10 |"
    )
    add(
        "| Facturas inflow sin PIT (toda la historia) | **16** | 2 | 14 |"
    )
    add(
        "| Dispatch (referencia) | 20 | 4 | 16 |"
    )
    add("")
    add(
        "El unico numero que cuadra exactamente es el de **4 empresas solo-opacas** en "
        "el ultimo corte. Las cifras del dispatch parecen una cota superior (un "
        "universo mas ancho que el de F2). No bloquea F1c: la fuente sirve igual; la "
        "diferencia se reporta para que F3 no dimensione el impacto con el 20."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=EUR_VENDOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    verify_vendor(args.vendor)
    observations = load_observations(args.vendor)
    months = closed_months()
    # Se publica TODA la historia de la fuente y el informe se centra en la
    # rejilla del repo; sin la historia previa, los primeros cortes de la rejilla
    # no tendrian tipo point-in-time-utilizable.
    rows = compute_fx_rates_extra(observations)
    assert_point_in_time(rows)
    coverage = build_coverage(rows, months)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in rows])
    frame["month"] = pd.to_datetime(frame["month"])
    frame["available_at"] = pd.to_datetime(frame["available_at"])
    frame.sort_values(["currency", "tipo", "month"], inplace=True)
    frame.to_parquet(args.output_dir / PARQUET_NAME, index=False)
    write_report_md(coverage, args.output_dir / REPORT_NAME)
    print(
        json.dumps(
            {
                "output": str(args.output_dir),
                "filas": len(rows),
                "meses_publicados": len({r.month for r in rows}),
                "divisas": sorted(coverage["por_divisa"]),
                "disponible_al_ultimo_corte": coverage[
                    "disponible_al_ultimo_corte"
                ],
                "observaciones_leidas": len(observations),
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
