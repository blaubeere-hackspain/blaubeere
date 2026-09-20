"""Indice de inestabilidad FX por divisa-mes a partir de tipos del BCE (tarea F1).

QUE ES ESTA CAPA
----------------
Modulo NUEVO y aditivo. Trae una fuente EXTERNA vendorizada (tipos de
referencia diarios del BCE, `data/fx_ecb/eurofxref-hist.csv`) y la convierte en
un indice de inestabilidad por divisa y mes cerrado. NO toca la formula de
`healthscore_v4`, NO toca facturas ni empresas y NO toca `xray/marts/fx.py`.
La capa empresa-mes que consume este indice es la tarea F2.

Contexto obligado: `data/marts/fx_rates.parquet` NO contiene tipos de cambio
(`rate_por_eur` es NULL salvo EUR, politica `unknown_no_estimates`). Por eso la
serie de tipos es externa: el BCE, anclado a EUR por construccion.

DIVISAS SIN COBERTURA BCE = INESTABLES POR DEFECTO (decision de producto)
-------------------------------------------------------------------------
El BCE no publica COP, ARS, CLP, PEN, NAD, MZN, AED ni MAD; tampoco varias
divisas retiradas. Una divisa que no podemos observar NO queda neutra ni
`sin_indice_fx`: entra en el tramo MAS inestable (`hipervolatil`) con motivo
declarado y SIN estadistico (nulo, nunca cero). El razonamiento es de
prudencia: no observar una divisa no autoriza a suponer que es estable.
Prohibido estimar tipos con medianas internas de `exchange_rate` (spec, punto 8).

ESTADISTICO: VOLATILIDAD ROBUSTA ANUALIZADA
-------------------------------------------
Para cada divisa y mes se toman los rendimientos logaritmicos diarios
`r_t = ln(tipo_t / tipo_{t-1})` de EUR/divisa y se mide

    vol_anualizada = (IQR(r) / IQR_A_NORMAL) * sqrt(ANUALIZATION_DAYS)

donde `IQR(r) = q75(r) - q25(r)` y `IQR_A_NORMAL = 1.349` es el factor de
consistencia del rango intercuartilico para una normal (`2 * 0.6745`). Es una
escala robusta: no depende de un unico dia extrano (una devaluacion puntual o
una revalorizacion de un dia) y es comparable entre divisas porque se expresa
en terminos anualizados. Se prefiere al desvio tipico porque en FX la cola
concentra shocks idiosincraticos de un solo dia que inflarian la medida de un
mes entero. Se publica el numero de rendimientos usados (`n_rendimientos`)
para que la robustez sea auditable; por debajo de `MIN_RENDIMIENTOS` la medida
no se publica y la divisa cae al tramo inestable por prudencia.

DERIVA SOSTENIDA (ASIMETRICA)
-----------------------------
La volatilidad mide cuanto TIEMBLA una divisa y es SIMETRICA. Pero esta capa
mide exposicion sobre DINERO QUE NOS DEBEN, asi que el riesgo relevante es
ASIMETRICO: solo perjudica que la divisa se HUNDA. Una divisa de depreciacion
gestionada (la lira turca) cae mucho y sin sobresaltos: poca agitacion, perdida
enorme. Por eso se anade un segundo componente, `deriva`:

    deriva_valor = tipo(inicio_ventana) / tipo(fin_ventana) - 1
    deriva_componente = max(0, -deriva_valor)   (solo erosiona; nunca premia)

El BCE cotiza UNIDADES DE DIVISA POR EUR, asi que si el tipo SUBE la divisa se
DEBILITA: el cociente `tipo_inicio/tipo_fin` invierte el signo. Una divisa que
se REVALORICE da `deriva_valor > 0` y componente 0: no se premia (no resta
riesgo) pero tampoco se castiga.

VENTANA: 12 meses moviles hacia atras (`DERIVA_VENTANA_MESES`). Se eligio
midiendo cual separa mejor los casos: sobre la rejilla, el tamano de efecto
(Cohen d) entre el bloque estructuralmente debil (TRY, IDR, INR, JPY) y el
bloque apreciado (USD, GBP, BRL, MXN) es maximo a 12 meses (d~1.0); a 18/24
meses la mediana del bloque apreciado se vuelve NEGATIVA (la ventana alcanza
periodos previos de depreciacion y castigaria a BRL, justo el error que se
quiere corregir) y a 3/6 meses TRY solo es el mas castigado en 16 de 24 meses
(pierde la senal sostenida). El CSV del BCE llega a 1999-01-04, asi que la
ventana no tiene calentamiento ni siquiera en el primer mes de la rejilla.

Disciplina point-in-time identica a la de la volatilidad: la deriva del mes m
usa solo el ultimo tipo publicado en el cierre de m y el del cierre de m-12;
ninguna cotizacion posterior a m entra en m.

TRAMOS
------
El indice es por tramos (mas robusto ante cobertura desigual), no continuo:
`estable` / `volatil` / `hipervolatil`. Se publican TRES tramos: el de
volatilidad (`tramo_volatilidad`), el de deriva (`tramo_deriva`) y el combinado
(`tramo`), para que F3 pueda medir cual de los dos mueve la nota. El combinado
es el MAS SEVERO de los dos (regla de prudencia: una divisa es tan arriesgada
como su peor dimension; no se deja que un componente positivo compense al
otro). Cortes de volatilidad declarados en `CORTE_ESTABLE` y `CORTE_HIPER`
(fijados sobre la distribucion medida: 0.025 ~ p20, 0.10 ~ p95). Cortes de
deriva declarados en `CORTE_DERIVA_ESTABLE` y `CORTE_DERIVA_HIPER` (2.5% y 6%
de erosion acumulada en 12 meses; el 6% separa en el ultimo mes la cola de
depreciacion gestionada --IDR/INR/JPY/PHP, 6.5-7.8%-- del bloque leve --RON
3.5%, THB 1.8%, resto 0--). Las divisas sin cobertura BCE se asignan
directamente a `hipervolatil`.

DISCIPLINA POINT-IN-TIME (dura)
-------------------------------
Un rendimiento se asigna al mes de su cotizacion MAS RECIENTE. Como el BCE
publica el tipo del dia `T` ese mismo dia (~16:00 CET), el indice del mes `m`
solo consume tipos publicados hasta el cierre de `m`; ninguna cotizacion de
`m+1` puede entrar en `m`. Cada fila publica su `available_at` (instante en que
la fila es calculable) y `tests/test_fx_index.py` demuestra la no-fuga por
perturbacion y por truncado.

REPRODUCIBILIDAD
----------------
La funcion pura `compute_fx_index` no toca red ni disco: recibe cotizaciones y
devuelve filas. `main` carga el CSV vendorizado, comprueba su sha256 contra
`ECB_VENDOR_SHA256` y regenera el indice de forma determinista.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from xray import paths
from xray.period import FIRST_MONTH, LAST_MONTH

# --- Fuente vendorizada -----------------------------------------------------

ECB_VENDOR_PATH = paths.ROOT / "data" / "fx_ecb" / "eurofxref-hist.csv"
ECB_VENDOR_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
)
# sha256 del CSV crudo vendorizado; declarado tambien en
# data/fx_ecb/PROCEDENCIA.md. Si no coincide, `main` aborta: no se construye
# nada sobre una fuente que no es la auditada.
ECB_VENDOR_SHA256 = (
    "22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d"
)
# Columna de artefacto del CSV (coma final): no es una divisa.
ECB_NON_CURRENCY_COLUMNS = ("Date", "column42")
# El BCE publica el tipo de referencia del dia T ese mismo dia sobre las 16:00
# CET. Se modela la disponibilidad a las 16:00 locales del propio dia.
ECB_PUBLICATION_HOUR = 16

# --- Estadistico y tramos ---------------------------------------------------

ANUALIZATION_DAYS = 252
# 2 * 0.6745: el IQR de una normal es 1.349 * sigma.
IQR_A_NORMAL = 1.349
# Minimo de rendimientos diarios para publicar la medida de un mes.
MIN_RENDIMIENTOS = 10
# Cortes de tramo sobre la volatilidad robusta anualizada. Justificados por la
# distribucion medida en la ventana 2024-09..2026-08: 0.025 ~ p20 (divisas
# fijas/gestionadas) y 0.10 ~ p95 (cola de estres).
CORTE_ESTABLE = 0.025
CORTE_HIPER = 0.10

TRAMO_ESTABLE = "estable"
TRAMO_VOLATIL = "volatil"
TRAMO_HIPERVOLATIL = "hipervolatil"

# Derivada sostenida (asimetrica). Ventana movil de 12 meses; cortes sobre la
# erosion acumulada del valor de la divisa frente al EUR (componente >= 0).
DERIVA_VENTANA_MESES = 12
CORTE_DERIVA_ESTABLE = 0.025
CORTE_DERIVA_HIPER = 0.06

# Orden de severidad para combinar los dos tramos (el mayor gana).
TRAMO_ORDER = {TRAMO_ESTABLE: 0, TRAMO_VOLATIL: 1, TRAMO_HIPERVOLATIL: 2}

MOTIVO_ANCLA = "eur_divisa_ancla"
MOTIVO_BCE = "cobertura_bce"
MOTIVO_SIN_COBERTURA = "sin_cobertura_bce_asumida_inestable"
MOTIVO_COBERTURA_INSUFICIENTE = "cobertura_bce_insuficiente_asumida_inestable"

DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "fx_risk"

TRAMOS = (TRAMO_ESTABLE, TRAMO_VOLATIL, TRAMO_HIPERVOLATIL)


# --- Modelo -----------------------------------------------------------------


@dataclass(frozen=True)
class EcbQuote:
    """Una cotizacion diaria EUR/divisa publicada por el BCE."""

    day: date
    currency: str
    rate: float

    @property
    def available_at(self) -> datetime:
        return publication_timestamp(self.day)


@dataclass(frozen=True)
class DailyReturn:
    """Rendimiento logaritmico diario asignado al mes de su fecha mas reciente."""

    currency: str
    day: date
    month: date
    log_return: float
    available_at: datetime


@dataclass(frozen=True)
class FxIndexRow:
    """Fila divisa-mes del indice de inestabilidad FX.

    Publica los DOS ingredientes por separado y el tramo combinado:
    - volatilidad: `vol_anualizada` -> `tramo_volatilidad`;
    - deriva sostenida: `deriva_valor` / `deriva_componente` -> `tramo_deriva`;
    - `tramo` = el mas severo de los dos (regla de combinacion declarada).
    """

    currency: str
    month: date
    cobertura_bce: bool
    n_cotizaciones: int
    n_rendimientos: int
    vol_anualizada: float | None
    tramo_volatilidad: str
    deriva_ventana_meses: int | None
    deriva_valor: float | None
    deriva_componente: float | None
    tramo_deriva: str | None
    tramo: str
    motivo: str
    available_at: datetime

    def to_row(self) -> dict:
        return {
            "currency": self.currency,
            "month": self.month,
            "cobertura_bce": self.cobertura_bce,
            "n_cotizaciones": self.n_cotizaciones,
            "n_rendimientos": self.n_rendimientos,
            "vol_anualizada": self.vol_anualizada,
            "tramo_volatilidad": self.tramo_volatilidad,
            "deriva_ventana_meses": self.deriva_ventana_meses,
            "deriva_valor": self.deriva_valor,
            "deriva_componente": self.deriva_componente,
            "tramo_deriva": self.tramo_deriva,
            "tramo": self.tramo,
            "motivo": self.motivo,
            "available_at": self.available_at,
        }


# --- Utilidades de calendario ----------------------------------------------


def month_end(month: date) -> date:
    """Ultimo dia natural del mes de `month`."""
    first_next = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def month_end_datetime(month: date) -> datetime:
    return datetime.combine(month_end(month), time(23, 59, 59))


def publication_timestamp(day: date) -> datetime:
    return datetime.combine(day, time(ECB_PUBLICATION_HOUR, 0, 0))


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
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


# --- Estadistica robusta ----------------------------------------------------


def _quantile(sorted_values: list[float], q: float) -> float | None:
    """Cuantil con interpolacion lineal (equivalente a numpy.percentile)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lower = int(pos)
    upper = min(lower + 1, n - 1)
    frac = pos - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


def robust_annualized_vol(returns: list[float]) -> float | None:
    """Volatilidad robusta anualizada: IQR/1.349 * sqrt(252)."""
    if len(returns) < MIN_RENDIMIENTOS:
        return None
    ordered = sorted(returns)
    q25 = _quantile(ordered, 0.25)
    q75 = _quantile(ordered, 0.75)
    assert q25 is not None and q75 is not None
    daily_scale = (q75 - q25) / IQR_A_NORMAL
    return daily_scale * (ANUALIZATION_DAYS**0.5)


def classify_tramo(vol: float | None) -> str:
    """Tramo de una volatilidad observada; None (sin medida) = hipervolatil."""
    if vol is None:
        return TRAMO_HIPERVOLATIL
    if vol < CORTE_ESTABLE:
        return TRAMO_ESTABLE
    if vol < CORTE_HIPER:
        return TRAMO_VOLATIL
    return TRAMO_HIPERVOLATIL


def classify_drift_tramo(componente: float | None) -> str | None:
    """Tramo de la deriva NEGATIVA (erosion acumulada); None si no hay medida.

    `componente = max(0, -deriva_valor)`: 0 significa que la divisa no se
    debilito (se aprecio o quedo plana), no que sea incierta. Por eso None
    (sin historia) y 0 (sin erosion) son cosas distintas.
    """
    if componente is None:
        return None
    if componente < CORTE_DERIVA_ESTABLE:
        return TRAMO_ESTABLE
    if componente < CORTE_DERIVA_HIPER:
        return TRAMO_VOLATIL
    return TRAMO_HIPERVOLATIL


def combine_tramos(*tramos: str | None) -> str:
    """Combinacion declarada: el tramo MAS SEVERO de los componentes presentes.

    Regla de prudencia y de unidad: una divisa es tan arriesgada como su peor
    dimension, asi que un componente benigno no compensa al otro. Si no hay
    ningun tramo (imposible salvo fila malformada) se cae a hipervolatil.
    """
    present = [t for t in tramos if t is not None]
    if not present:
        return TRAMO_HIPERVOLATIL
    return max(present, key=lambda t: TRAMO_ORDER[t])


# --- Deriva sostenida (asimetrica) -----------------------------------------


def build_month_end_quotes(quotes: list[EcbQuote]) -> dict[str, list[EcbQuote]]:
    """Cotizaciones por divisa ordenadas por fecha (para busqueda binaria)."""
    series: dict[str, list[EcbQuote]] = {}
    for quote in quotes:
        series.setdefault(quote.currency, []).append(quote)
    for currency in series:
        series[currency].sort(key=lambda q: q.day)
    return series


def month_end_quote(
    series_by_currency: dict[str, list[EcbQuote]], currency: str, month: date
) -> EcbQuote | None:
    """Ultima cotizacion publicada DENTRO del mes; None si el mes no cotiza.

    Exigir que el tipo este dentro del mes (y no un tipo rancio de un mes
    anterior) evita que una divisa que deja de cotizar herede una deriva nula
    artificial. La disponibilidad de la cotizacion es la del propio dia.
    """
    series = series_by_currency.get(currency)
    if not series:
        return None
    start = month.replace(day=1)
    end = month_end(month)
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid].day <= end:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    quote = series[lo - 1]
    return quote if quote.day >= start else None


def sustained_drift(
    series_by_currency: dict[str, list[EcbQuote]],
    currency: str,
    month: date,
    window_months: int = DERIVA_VENTANA_MESES,
) -> float | None:
    """Variacion del VALOR de la divisa frente al EUR en la ventana movil.

    Positivo = la divisa se aprecio (1 unidad vale mas EUR); negativo = se
    debilito. Con el BCE cotizando unidades de divisa por EUR,
    `valor = 1 / tipo`, luego `deriva_valor = tipo_inicio / tipo_fin - 1`.
    Point-in-time: usa el cierre del mes m y el del mes m-W, ambos publicados
    antes del cierre de m. None si no hay cierre en alguno de los dos meses.
    """
    if window_months <= 0:
        return None
    end_quote = month_end_quote(series_by_currency, currency, month)
    start_quote = month_end_quote(
        series_by_currency, currency, shift_month(month, -window_months)
    )
    if end_quote is None or start_quote is None:
        return None
    if start_quote.rate <= 0 or end_quote.rate <= 0:
        return None
    if end_quote.day <= start_quote.day:
        return None
    return start_quote.rate / end_quote.rate - 1.0


# --- Calculo del indice -----------------------------------------------------


def daily_log_returns(quotes: list[EcbQuote]) -> list[DailyReturn]:
    """Rendimientos logaritmicos por divisa, asignados al mes de la fecha mayor.

    Recorre las cotizaciones en orden cronologico. El primer rendimiento de un
    mes usa la ultima cotizacion anterior, que ya estaba publicada antes del
    mes: no hay fuga hacia el futuro.
    """
    ordered = sorted(quotes, key=lambda q: (q.currency, q.day))
    out: list[DailyReturn] = []
    previous: dict[str, EcbQuote] = {}
    for quote in ordered:
        prior = previous.get(quote.currency)
        if prior is not None and prior.rate > 0 and quote.rate > 0 and quote.day > prior.day:
            out.append(
                DailyReturn(
                    currency=quote.currency,
                    day=quote.day,
                    month=quote.day.replace(day=1),
                    log_return=math.log(quote.rate / prior.rate),
                    available_at=quote.available_at,
                )
            )
        previous[quote.currency] = quote
    return out


def build_grid_currencies(
    ecb_currencies: list[str], portfolio_currencies: list[str]
) -> list[str]:
    """Union ordenada de divisas del BCE y de la cartera viva, con EUR.

    Toda divisa que aparezca en cualquiera de las dos fuentes tiene fila en la
    rejilla, de modo que F2 puede unir sin huecos y el censo queda completo.
    """
    grid = {c for c in ecb_currencies if c}
    grid.update(c for c in portfolio_currencies if c)
    grid.add("EUR")
    return sorted(grid)


def compute_fx_index(
    quotes: list[EcbQuote],
    currencies: list[str],
    months: list[date],
    ecb_currencies: frozenset[str] | None = None,
    deriva_ventana_meses: int = DERIVA_VENTANA_MESES,
) -> list[FxIndexRow]:
    """Rejilla divisa x mes con los dos componentes, sus tramos y el combinado.

    `ecb_currencies` es el conjunto de divisas con cobertura BCE efectiva en la
    ventana (cotizacion observada). Una divisa fuera de el cae al tramo
    inestable por defecto. EUR es el ancla: volatilidad 0 y deriva 0.

    Cada fila publica la volatilidad y la deriva por separado, su tramo
    respectivo y `tramo` = el mas severo de los dos. La deriva es asimetrica:
    solo la erosion (componente) cuenta; una revalorizacion da componente 0.
    """
    covered = frozenset(ecb_currencies) if ecb_currencies is not None else None
    returns = daily_log_returns(quotes)
    by_key: dict[tuple[str, date], list[DailyReturn]] = {}
    for ret in returns:
        by_key.setdefault((ret.currency, ret.month), []).append(ret)
    quotes_by_key: dict[tuple[str, date], int] = {}
    for quote in quotes:
        key = (quote.currency, quote.day.replace(day=1))
        quotes_by_key[key] = quotes_by_key.get(key, 0) + 1
    series = build_month_end_quotes(quotes)

    rows: list[FxIndexRow] = []
    for currency in sorted(set(currencies)):
        for month in months:
            month_returns = by_key.get((currency, month), [])
            n_cotizaciones = quotes_by_key.get((currency, month), 0)
            if currency == "EUR":
                rows.append(
                    FxIndexRow(
                        currency=currency,
                        month=month,
                        cobertura_bce=True,
                        n_cotizaciones=n_cotizaciones,
                        n_rendimientos=0,
                        vol_anualizada=0.0,
                        tramo_volatilidad=TRAMO_ESTABLE,
                        deriva_ventana_meses=deriva_ventana_meses,
                        deriva_valor=0.0,
                        deriva_componente=0.0,
                        tramo_deriva=TRAMO_ESTABLE,
                        tramo=TRAMO_ESTABLE,
                        motivo=MOTIVO_ANCLA,
                        available_at=month_end_datetime(month),
                    )
                )
                continue

            is_covered = covered is None or currency in covered
            vol = robust_annualized_vol([r.log_return for r in month_returns])
            vol_tramo = classify_tramo(vol)
            deriva_valor = sustained_drift(
                series, currency, month, deriva_ventana_meses
            )
            deriva_componente = (
                None if deriva_valor is None else max(0.0, -deriva_valor)
            )
            deriva_tramo = classify_drift_tramo(deriva_componente)
            available = None
            if month_returns:
                available = max(r.available_at for r in month_returns)
            end_quote = month_end_quote(series, currency, month)
            if end_quote is not None:
                available = (
                    end_quote.available_at
                    if available is None
                    else max(available, end_quote.available_at)
                )

            if not is_covered or vol is None:
                motivo = MOTIVO_SIN_COBERTURA if not is_covered else MOTIVO_COBERTURA_INSUFICIENTE
                rows.append(
                    _default_row(
                        currency,
                        month,
                        n_cotizaciones,
                        motivo,
                        deriva_ventana_meses,
                        deriva_valor,
                        deriva_componente,
                        deriva_tramo,
                    )
                )
            else:
                assert available is not None
                rows.append(
                    FxIndexRow(
                        currency=currency,
                        month=month,
                        cobertura_bce=True,
                        n_cotizaciones=n_cotizaciones,
                        n_rendimientos=len(month_returns),
                        vol_anualizada=vol,
                        tramo_volatilidad=vol_tramo,
                        deriva_ventana_meses=deriva_ventana_meses,
                        deriva_valor=deriva_valor,
                        deriva_componente=deriva_componente,
                        tramo_deriva=deriva_tramo,
                        tramo=combine_tramos(vol_tramo, deriva_tramo),
                        motivo=MOTIVO_BCE,
                        available_at=available,
                    )
                )
    return rows


def _default_row(
    currency: str,
    month: date,
    n_cotizaciones: int,
    motivo: str,
    deriva_ventana_meses: int | None = None,
    deriva_valor: float | None = None,
    deriva_componente: float | None = None,
    deriva_tramo: str | None = None,
) -> FxIndexRow:
    """Fila del tramo inestable por defecto: sin volatilidad (None, no cero).

    Se conserva la politica de prudencia (sin medida -> hipervolatil). Si la
    deriva es calculable se publica igualmente, pero NO rebaja el tramo: el
    motivo declara por que la fila cae al tramo inestable.
    """
    return FxIndexRow(
        currency=currency,
        month=month,
        cobertura_bce=False,
        n_cotizaciones=n_cotizaciones,
        n_rendimientos=0,
        vol_anualizada=None,
        tramo_volatilidad=TRAMO_HIPERVOLATIL,
        deriva_ventana_meses=deriva_ventana_meses,
        deriva_valor=deriva_valor,
        deriva_componente=deriva_componente,
        tramo_deriva=deriva_tramo,
        tramo=TRAMO_HIPERVOLATIL,
        motivo=motivo,
        available_at=month_end_datetime(month),
    )


def assert_point_in_time(rows: list[FxIndexRow]) -> None:
    """Ninguna fila puede depender de informacion posterior al cierre de su mes."""
    for row in rows:
        if row.available_at > month_end_datetime(row.month):
            raise AssertionError(
                f"fuga point-in-time: {row.currency} {row.month} "
                f"available_at={row.available_at}"
            )


# --- Carga del CSV vendorizado ---------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_vendor(path: Path = ECB_VENDOR_PATH) -> str:
    actual = sha256_file(path)
    if actual != ECB_VENDOR_SHA256:
        raise ValueError(
            f"sha256 del CSV vendorizado no coincide con PROCEDENCIA.md: "
            f"{actual} != {ECB_VENDOR_SHA256}"
        )
    return actual


def load_ecb_quotes(con, csv_path: Path = ECB_VENDOR_PATH) -> tuple[list[EcbQuote], list[str]]:
    """Carga el CSV crudo del BCE a (cotizaciones, divisas del fichero).

    Usa duckdb UNPIVOT sobre las columnas de divisa; ignora celdas `N/A`.
    """
    columns = [
        row[0]
        for row in con.execute(
            f"SELECT * FROM read_csv('{csv_path}', header=true, all_varchar=true) LIMIT 1"
        ).description
    ]
    currency_columns = [c for c in columns if c not in ECB_NON_CURRENCY_COLUMNS]
    if not currency_columns:
        raise ValueError(f"el CSV {csv_path} no tiene columnas de divisa")
    on_list = ", ".join(f"'{c}'" for c in currency_columns)
    query = f"""
        SELECT currency, CAST(Date AS DATE) AS day, CAST(rate AS DOUBLE) AS rate
        FROM (
            UNPIVOT (
                SELECT Date, {", ".join(f'"{c}"' for c in currency_columns)}
                FROM read_csv('{csv_path}', header=true, all_varchar=true)
            ) ON {on_list} INTO NAME currency VALUE rate
        )
        WHERE rate IS NOT NULL AND rate <> 'N/A' AND CAST(rate AS DOUBLE) > 0
        ORDER BY currency, day
    """
    quotes = [EcbQuote(day=d, currency=c, rate=float(r)) for c, d, r in con.execute(query).fetchall()]
    return quotes, currency_columns


# --- Censo de cobertura -----------------------------------------------------

LIVE_PORTFOLIO_FILTER = (
    "document_type_norm = 'invoice' "
    "AND status_norm IN ('overdue', 'pending') "
    "AND flow_side = 'inflow'"
)


def _ecb_currencies_in_window(
    quotes: list[EcbQuote], months: list[date]
) -> list[str]:
    first, last = months[0], month_end(months[-1])
    return sorted({q.currency for q in quotes if first <= q.day <= last})


def portfolio_census(
    con, invoices_path: Path, covered: set[str], index_rows: list[FxIndexRow], month: date
) -> dict:
    """Cruce de las divisas de la cartera viva con la cobertura BCE."""
    db_rows = con.execute(
        f"""
        SELECT currency_norm, COUNT(*) AS filas,
               COUNT(DISTINCT company_id) AS empresas
        FROM read_parquet('{invoices_path}')
        WHERE {LIVE_PORTFOLIO_FILTER}
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    total_filas = sum(r[1] for r in db_rows)
    companies = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT company_id FROM read_parquet('{invoices_path}') "
            f"WHERE {LIVE_PORTFOLIO_FILTER}"
        ).fetchall()
    }
    rows = [row for row in db_rows if row[0] is not None]

    def group(currency_list: list[str]) -> dict:
        selected = [r for r in rows if r[0] in currency_list]
        comps = set()
        if selected:
            literals = ", ".join(f"'{r[0]}'" for r in selected)
            comps = {
                r[0]
                for r in con.execute(
                    f"SELECT DISTINCT company_id FROM read_parquet('{invoices_path}') "
                    f"WHERE {LIVE_PORTFOLIO_FILTER} AND currency_norm IN ({literals})"
                ).fetchall()
            }
        return {
            "divisas": sorted(r[0] for r in selected),
            "n_divisas": len(selected),
            "empresas": len(comps),
            "filas_cartera_viva": sum(r[1] for r in selected),
        }

    covered_list = sorted(c for c in covered if any(r[0] == c for r in rows))
    uncovered_list = sorted(r[0] for r in rows if r[0] not in covered)
    covered_group = group(covered_list)
    uncovered_group = group(uncovered_list)
    por_divisa = {
        r[0]: {
            "filas_cartera_viva": r[1],
            "empresas": r[2],
            "cobertura_bce": r[0] in covered,
        }
        for r in rows
    }
    both = con.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT company_id FROM read_parquet('{invoices_path}')
            WHERE {LIVE_PORTFOLIO_FILTER}
            GROUP BY 1
            HAVING COUNT(DISTINCT CASE WHEN currency_norm IN ({_literals(covered_list)}) THEN 1 END) > 0
               AND COUNT(DISTINCT CASE WHEN currency_norm IN ({_literals(uncovered_list)}) THEN 1 END) > 0
        )
        """
    ).fetchone()[0]

    def tramo_map(attr: str) -> dict[str, str]:
        return {
            r.currency: getattr(r, attr)
            for r in index_rows
            if r.month == month and r.currency in por_divisa
        }

    # ANTES: tramo solo-volatilidad. DESPUES: tramo combinado (vol + deriva).
    tramo_vol_by_currency = tramo_map("tramo_volatilidad")
    tramo_comb_by_currency = tramo_map("tramo")
    for currency, info in por_divisa.items():
        if currency in tramo_vol_by_currency:
            info["tramo_volatilidad"] = tramo_vol_by_currency[currency]
            info["tramo"] = tramo_comb_by_currency.get(currency)
    empresas_por_tramo = _distinct_companies_by_tramo(
        con, invoices_path, tramo_comb_by_currency
    )
    empresas_por_tramo_vol = _distinct_companies_by_tramo(
        con, invoices_path, tramo_vol_by_currency
    )
    cambios = {
        currency: {
            "tramo_volatilidad": tramo_vol_by_currency[currency],
            "tramo_combinado": tramo_comb_by_currency.get(currency),
        }
        for currency in sorted(tramo_vol_by_currency)
        if tramo_comb_by_currency.get(currency) != tramo_vol_by_currency[currency]
    }
    divisas_ventana = sorted(
        {
            r.currency
            for r in index_rows
            if r.tramo != r.tramo_volatilidad
        }
    )
    return {
        "filtro": LIVE_PORTFOLIO_FILTER,
        "n_filas": total_filas,
        "n_empresas": len(companies),
        "n_divisas": len(rows),
        "empresas_ambos_grupos": both,
        "cubiertas_bce": covered_group,
        "sin_cobertura_bce": uncovered_group,
        "por_divisa": por_divisa,
        "empresas_por_tramo_ultimo_mes": {"mes": str(month), **empresas_por_tramo},
        "empresas_por_tramo_volatilidad_ultimo_mes": {
            "mes": str(month),
            **empresas_por_tramo_vol,
        },
        "cambio_de_tramo_ultimo_mes": {
            "mes": str(month),
            "n_divisas": len(cambios),
            "divisas": sorted(cambios),
            "detalle": cambios,
            "empresas_afectadas": _companies_for_currencies(
                con, invoices_path, list(cambios)
            ),
        },
        "cambio_de_tramo_en_la_ventana": {
            "n_divisas": len(divisas_ventana),
            "divisas": divisas_ventana,
            "empresas_afectadas": _companies_for_currencies(
                con, invoices_path, divisas_ventana
            ),
        },
        "sensibilidad_corte_deriva": _sensitivity_drift_cuts(
            con, invoices_path, index_rows, month
        ),
    }


def _companies_for_currencies(con, invoices_path: Path, currencies: list[str]) -> int:
    """Empresas distintas de la cartera viva con alguna divisa de la lista."""
    values = sorted({c for c in currencies if c})
    if not values:
        return 0
    return con.execute(
        f"SELECT COUNT(DISTINCT company_id) FROM read_parquet('{invoices_path}') "
        f"WHERE {LIVE_PORTFOLIO_FILTER} AND currency_norm IN ({_literals(values)})"
    ).fetchone()[0]


def _sensitivity_drift_cuts(
    con,
    invoices_path: Path,
    index_rows: list[FxIndexRow],
    month: date,
    cuts: tuple[float, ...] = (0.03, 0.05, 0.06, 0.08, 0.10),
) -> dict:
    """Sensibilidad del censo al corte de deriva (justifica el 6% elegido)."""
    rows = [r for r in index_rows if r.month == month]
    out: dict[str, dict] = {}
    for cut in cuts:
        changed: list[str] = []
        for row in rows:
            if row.deriva_componente is None:
                continue
            if row.deriva_componente >= cut:
                deriva_tramo = TRAMO_HIPERVOLATIL
            elif row.deriva_componente >= CORTE_DERIVA_ESTABLE:
                deriva_tramo = TRAMO_VOLATIL
            else:
                deriva_tramo = TRAMO_ESTABLE
            if TRAMO_ORDER[deriva_tramo] > TRAMO_ORDER[row.tramo_volatilidad]:
                changed.append(row.currency)
        out[f"{cut:.3f}"] = {
            "corte_deriva_hiper": cut,
            "divisas_que_cambian": sorted(changed),
            "n_divisas": len(changed),
            "empresas_afectadas": _companies_for_currencies(
                con, invoices_path, changed
            ),
        }
    return out


def _distinct_companies_by_tramo(
    con, invoices_path: Path, tramo_by_currency: dict[str, str]
) -> dict:
    """Empresas distintas y divisas por tramo (tramo de la divisa en un mes).

    Es un recuento de empresas distintas, no una suma de recuentos por divisa:
    una empresa con dos divisas del mismo tramo cuenta una sola vez (aunque
    sigue apareciendo en cada tramo en el que tenga alguna divisa).
    """
    mapping = {c: t for c, t in tramo_by_currency.items() if c}
    out: dict[str, dict] = {t: {"divisas": [], "empresas": 0} for t in TRAMOS}
    for currency, tramo in mapping.items():
        out[tramo]["divisas"].append(currency)
    for tramo, block in out.items():
        block["divisas"].sort()
        if not block["divisas"]:
            continue
        case = (
            "CASE currency_norm "
            + " ".join(f"WHEN '{c}' THEN '{mapping[c]}'" for c in block["divisas"])
            + " END"
        )
        block["empresas"] = con.execute(
            f"SELECT COUNT(DISTINCT company_id) FROM read_parquet('{invoices_path}') "
            f"WHERE {LIVE_PORTFOLIO_FILTER} AND currency_norm IS NOT NULL "
            f"AND {case} = '{tramo}'"
        ).fetchone()[0]
    return out


def _literals(values: list[str]) -> str:
    if not values:
        return "NULL"
    return ", ".join(f"'{v}'" for v in values)


# --- Informe ---------------------------------------------------------------


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p10": _quantile(ordered, 0.10),
        "p20": _quantile(ordered, 0.20),
        "p25": _quantile(ordered, 0.25),
        "p50": _quantile(ordered, 0.50),
        "p75": _quantile(ordered, 0.75),
        "p90": _quantile(ordered, 0.90),
        "p95": _quantile(ordered, 0.95),
        "p99": _quantile(ordered, 0.99),
        "max": ordered[-1],
    }


def _per_currency_summary(rows: list[FxIndexRow]) -> dict:
    """Resumen por divisa: ambos componentes, sus tramos y el combinado modal."""
    summary: dict[str, dict] = {}
    for currency in sorted({r.currency for r in rows}):
        subset = [r for r in rows if r.currency == currency]
        vols = [r.vol_anualizada for r in subset if r.vol_anualizada is not None]
        deriva_comps = [
            r.deriva_componente
            for r in subset
            if r.deriva_componente is not None
        ]
        deriva_valores = [r.deriva_valor for r in subset if r.deriva_valor is not None]

        def modal(attr: str) -> str | None:
            counts: dict[str, int] = {}
            for row in subset:
                value = getattr(row, attr)
                if value is not None:
                    counts[value] = counts.get(value, 0) + 1
            if not counts:
                return None
            # En empate se prefiere el tramo MENOS severo: el resumen modal no
            # debe exagerar el riesgo cuando dos tramos empatan en meses.
            return max(counts, key=lambda t: (counts[t], -TRAMO_ORDER[t]))

        summary[currency] = {
            "vol_mediana": _quantile(sorted(vols), 0.5),
            "vol_min": min(vols) if vols else None,
            "vol_max": max(vols) if vols else None,
            "meses_con_medida": len(vols),
            "deriva_mediana": _quantile(sorted(deriva_valores), 0.5),
            "deriva_componente_mediana": _quantile(sorted(deriva_comps), 0.5),
            "deriva_componente_max": max(deriva_comps) if deriva_comps else None,
            "meses_con_deriva": len(deriva_comps),
            "meses": len(subset),
            "meses_cobertura_bce": sum(1 for r in subset if r.cobertura_bce),
            "tramo_volatilidad_modal": modal("tramo_volatilidad"),
            "tramo_deriva_modal": modal("tramo_deriva"),
            "tramo_modal": modal("tramo"),
            "cobertura_bce": any(r.cobertura_bce for r in subset),
        }
    return summary


def build_coverage(
    rows: list[FxIndexRow],
    ecb_currencies: list[str],
    portfolio: dict,
    quotes: list[EcbQuote],
    months: list[date],
) -> dict:
    observed = [r.vol_anualizada for r in rows if r.vol_anualizada is not None]
    observed_deriva = [
        r.deriva_componente for r in rows if r.deriva_componente is not None
    ]
    tramo_counts = {t: sum(1 for r in rows if r.tramo == t) for t in TRAMOS}
    tramo_vol_counts = {t: sum(1 for r in rows if r.tramo_volatilidad == t) for t in TRAMOS}
    tramo_deriva_counts = {
        t: sum(1 for r in rows if r.tramo_deriva == t) for t in TRAMOS
    }
    motivo_counts: dict[str, int] = {}
    for row in rows:
        motivo_counts[row.motivo] = motivo_counts.get(row.motivo, 0) + 1

    first, last = months[0], month_end(months[-1])
    quote_ranges = {}
    for currency in sorted({q.currency for q in quotes}):
        days = [q.day for q in quotes if q.currency == currency]
        quote_ranges[currency] = {
            "primera_cotizacion": str(min(days)),
            "ultima_cotizacion": str(max(days)),
            "n_cotizaciones_ventana": sum(1 for d in days if first <= d <= last),
            "cubre_ventana": any(first <= d <= last for d in days),
        }

    sin_cobertura = sorted({r.currency for r in rows if r.motivo == MOTIVO_SIN_COBERTURA})
    cobertura_insuficiente = sorted(
        {r.currency for r in rows if r.motivo == MOTIVO_COBERTURA_INSUFICIENTE}
    )
    # Meses por divisa en que la deriva sola ya la lleva a hipervolatil (para
    # medir cuanto castiga el componente nuevo a lo largo de la ventana).
    deriva_hiper_meses: dict[str, int] = {}
    for row in rows:
        if row.deriva_componente is not None and row.deriva_componente >= CORTE_DERIVA_HIPER:
            deriva_hiper_meses[row.currency] = (
                deriva_hiper_meses.get(row.currency, 0) + 1
            )
    return {
        "fuente": {
            "url": ECB_VENDOR_URL,
            "sha256": ECB_VENDOR_SHA256,
            "fichero": str(ECB_VENDOR_PATH.relative_to(paths.ROOT)),
            "publicacion_bce_hora_local": ECB_PUBLICATION_HOUR,
        },
        "ventana": {"primer_mes": str(months[0]), "ultimo_mes": str(months[-1]), "n_meses": len(months)},
        "rejilla": {
            "divisas": sorted({r.currency for r in rows}),
            "filas": len(rows),
        },
        "estadistico": {
            "nombre": "volatilidad_robusta_anualizada",
            "formula": "(IQR(rendimientos_log_diarios) / 1.349) * sqrt(252)",
            "iqr_a_normal": IQR_A_NORMAL,
            "dias_anualizacion": ANUALIZATION_DAYS,
            "min_rendimientos": MIN_RENDIMIENTOS,
            "justificacion": (
                "Escala robusta: un unico dia extremo (devaluacion puntual) no "
                "domina el mes; expresada en terminos anualizados para ser "
                "comparable entre divisas."
            ),
        },
        "estadistico_deriva": {
            "nombre": "deriva_sostenida_asimetrica",
            "formula": (
                "deriva_valor = tipo(cierre m-W) / tipo(cierre m) - 1; "
                "deriva_componente = max(0, -deriva_valor)"
            ),
            "ventana_meses": DERIVA_VENTANA_MESES,
            "signo": (
                "El BCE cotiza unidades de divisa por EUR: si el tipo SUBE la "
                "divisa se DEBILITA. El cociente invierte el signo, de modo que "
                "deriva_valor < 0 = perdida para quien va a cobrar en esa divisa."
            ),
            "asimetria": (
                "Solo cuenta la deriva negativa. Una revalorizacion da "
                "componente 0 (ni premia ni castiga)."
            ),
            "justificacion_ventana": (
                "12 meses maximiza la separacion medida (Cohen d ~1.0) entre el "
                "bloque estructuralmente debil (TRY, IDR, INR, JPY) y el "
                "apreciado (USD, GBP, BRL, MXN); a 18/24 meses la mediana del "
                "bloque apreciado se vuelve negativa (castigaria a BRL) y a "
                "3/6 meses TRY solo es el mas castigado en 16/24 meses. El CSV "
                "llega a 1999-01-04, asi que no hay calentamiento."
            ),
        },
        "tramos": {
            "orden": list(TRAMOS),
            "combinacion": "mas_severo_de_los_dos",
            "combinacion_justificacion": (
                "Una divisa es tan arriesgada como su peor dimension; un "
                "componente benigno no compensa al otro. Se publican los dos "
                "tramos por separado para que F3 mida cual mueve la nota."
            ),
            "cortes": {
                "vol_estable": f"vol < {CORTE_ESTABLE}",
                "vol_volatil": f"{CORTE_ESTABLE} <= vol < {CORTE_HIPER}",
                "vol_hipervolatil": f"vol >= {CORTE_HIPER} o sin cobertura BCE",
                "deriva_estable": f"componente < {CORTE_DERIVA_ESTABLE}",
                "deriva_volatil": (
                    f"{CORTE_DERIVA_ESTABLE} <= componente < {CORTE_DERIVA_HIPER}"
                ),
                "deriva_hipervolatil": f"componente >= {CORTE_DERIVA_HIPER}",
            },
            "justificacion": (
                "Volatilidad: cortes sobre la distribucion medida (0.025 ~ p20, "
                "0.10 ~ p95). Deriva: 2.5% de erosion es el borde de una banda "
                "gestionada y 6% acumulado en 12 meses separa en el ultimo mes "
                "la cola de depreciacion gestionada (IDR/INR/JPY/PHP, 6.5-7.8%) "
                "del bloque leve (RON 3.5%, THB 1.8%, resto 0)."
            ),
            "conteo_filas": tramo_counts,
            "conteo_filas_volatilidad": tramo_vol_counts,
            "conteo_filas_deriva": tramo_deriva_counts,
        },
        "distribucion_vol_anualizada": _distribution(observed),
        "distribucion_deriva_componente": _distribution(observed_deriva),
        "meses_deriva_hiper_por_divisa": dict(
            sorted(deriva_hiper_meses.items(), key=lambda item: (-item[1], item[0]))
        ),
        "resumen_por_divisa": _per_currency_summary(rows),
        "empresas_por_tramo_ultimo_mes": portfolio["empresas_por_tramo_ultimo_mes"],
        "empresas_por_tramo_volatilidad_ultimo_mes": portfolio[
            "empresas_por_tramo_volatilidad_ultimo_mes"
        ],
        "cambio_de_tramo_ultimo_mes": portfolio["cambio_de_tramo_ultimo_mes"],
        "cambio_de_tramo_en_la_ventana": portfolio["cambio_de_tramo_en_la_ventana"],
        "sensibilidad_corte_deriva": portfolio["sensibilidad_corte_deriva"],
        "motivos": motivo_counts,
        "divisas_sin_cobertura_bce": sin_cobertura,
        "divisas_con_cobertura_insuficiente": cobertura_insuficiente,
        "divisas_hipervolatiles_por_defecto": sorted(set(sin_cobertura) | set(cobertura_insuficiente)),
        "divisas_bce": {
            "n_columnas": len(ecb_currencies),
            "cubren_ventana": _ecb_currencies_in_window(quotes, months),
            "rango_por_divisa": quote_ranges,
        },
        "cartera_viva": portfolio,
    }


def write_report_md(coverage: dict, path: Path) -> None:
    def fmt(value, places: int = 4) -> str:
        return "-" if value is None else f"{value:.{places}f}"

    lines = [
        "# Indice de inestabilidad FX por divisa-mes (F1)",
        "",
        "Fuente externa vendorizada: tipos de referencia diarios del BCE.",
        "",
        f"- URL: `{coverage['fuente']['url']}`",
        f"- sha256: `{coverage['fuente']['sha256']}`",
        f"- Fichero: `{coverage['fuente']['fichero']}`",
        f"- Ventana: {coverage['ventana']['primer_mes']} a {coverage['ventana']['ultimo_mes']} "
        f"({coverage['ventana']['n_meses']} meses)",
        f"- Filas de rejilla: {coverage['rejilla']['filas']} "
        f"({len(coverage['rejilla']['divisas'])} divisas)",
        "",
        "## Columnas del parquet (para F2/F3)",
        "",
        "`fx_index.parquet` publica los dos ingredientes por separado. ATENCION: "
        "`tramo` pasa a ser el tramo COMBINADO; el tramo de solo-volatilidad de "
        "F1 se conserva en `tramo_volatilidad`.",
        "",
        "| Columna | Significado |",
        "| --- | --- |",
        "| `vol_anualizada` | Volatilidad robusta anualizada (componente 1). |",
        "| `tramo_volatilidad` | Tramo del componente 1 (el `tramo` de F1). |",
        "| `deriva_ventana_meses` | Ventana movil de la deriva (12). |",
        "| `deriva_valor` | Variacion firmada del valor frente al EUR (>0 aprecia). |",
        "| `deriva_componente` | `max(0, -deriva_valor)`: solo erosion. |",
        "| `tramo_deriva` | Tramo del componente 2. |",
        "| `tramo` | Combinado: el mas severo de los dos. |",
        "",
        "## Componente 1: volatilidad robusta anualizada",
        "",
        coverage["estadistico"]["formula"],
        "",
        coverage["estadistico"]["justificacion"],
        "",
        "## Componente 2: deriva sostenida (asimetrica)",
        "",
        coverage["estadistico_deriva"]["formula"],
        "",
        f"Ventana: {coverage['estadistico_deriva']['ventana_meses']} meses moviles.",
        "",
        coverage["estadistico_deriva"]["signo"],
        "",
        coverage["estadistico_deriva"]["asimetria"],
        "",
        coverage["estadistico_deriva"]["justificacion_ventana"],
        "",
        "## Combinacion de los dos componentes",
        "",
        f"Regla: **{coverage['tramos']['combinacion']}**. "
        + coverage["tramos"]["combinacion_justificacion"],
        "",
        "## Tramos",
        "",
    ]
    for name, cut in coverage["tramos"]["cortes"].items():
        lines.append(f"- `{name}`: {cut}")
    lines.append("")
    lines.append(coverage["tramos"]["justificacion"])
    lines.append("")
    lines.append("Filas por tramo combinado: " + ", ".join(
        f"{t}={coverage['tramos']['conteo_filas'].get(t, 0)}" for t in ("estable", "volatil", "hipervolatil")
    ))
    lines.append("Filas por tramo de volatilidad: " + ", ".join(
        f"{t}={coverage['tramos']['conteo_filas_volatilidad'].get(t, 0)}" for t in ("estable", "volatil", "hipervolatil")
    ))
    lines.append("Filas por tramo de deriva: " + ", ".join(
        f"{t}={coverage['tramos']['conteo_filas_deriva'].get(t, 0)}" for t in ("estable", "volatil", "hipervolatil")
    ))
    lines.append("")
    lines.append("## Distribucion de la volatilidad robusta anualizada")
    lines.append("")
    dist = coverage["distribucion_vol_anualizada"]
    for key in ("n", "min", "p10", "p20", "p25", "p50", "p75", "p90", "p95", "p99", "max"):
        value = dist.get(key)
        if value is None:
            continue
        lines.append(f"- {key}: {value:.4f}" if key != "n" else f"- {key}: {value}")
    lines.append("")
    lines.append("## Distribucion del componente de deriva (erosion acumulada 12m)")
    lines.append("")
    dist = coverage["distribucion_deriva_componente"]
    for key in ("n", "min", "p10", "p20", "p25", "p50", "p75", "p90", "p95", "p99", "max"):
        value = dist.get(key)
        if value is None:
            continue
        lines.append(f"- {key}: {value:.4f}" if key != "n" else f"- {key}: {value}")
    lines.append("")
    lines.append("## ANTES / DESPUES: efecto de anadir la deriva al tramo")
    lines.append("")
    cambio = coverage["cambio_de_tramo_ultimo_mes"]
    lines.append(f"Mes de censo: {cambio['mes']}.")
    lines.append(
        f"Divisas que cambian de tramo al anadir la deriva: {cambio['n_divisas']} "
        f"({', '.join(cambio['divisas']) or '-'}). "
        f"Empresas de la cartera viva afectadas: {cambio['empresas_afectadas']}."
    )
    lines.append("")
    if cambio["detalle"]:
        lines.append("| Divisa | Tramo volatilidad (antes) | Tramo combinado (despues) | Deriva comp. |")
        lines.append("| --- | --- | --- | --- |")
        for currency, det in cambio["detalle"].items():
            comp = coverage["resumen_por_divisa"].get(currency, {}).get("deriva_componente_mediana")
            lines.append(
                f"| {currency} | {det['tramo_volatilidad']} | {det['tramo_combinado']} | {fmt(comp)} |"
            )
        lines.append("")
    ventana = coverage["cambio_de_tramo_en_la_ventana"]
    lines.append(
        f"En toda la ventana, {ventana['n_divisas']} divisas cambian de tramo en algun mes "
        f"({', '.join(ventana['divisas']) or '-'}); las empresas de la cartera viva que tienen "
        f"alguna de esas divisas son {ventana['empresas_afectadas']}."
    )
    lines.append("")
    lines.append(
        "Nota: la cifra de ventana es una COTA SUPERIOR (una divisa puede cambiar "
        "en un mes en que la empresa no tenga factura viva); el censo de empresas "
        "afectadas del mes de cierre es la medida principal."
    )
    lines.append("")
    lines.append("### Sensibilidad al corte de deriva")
    lines.append("")
    lines.append(
        "| Corte deriva hiper | Divisas que cambian | Empresas afectadas |"
    )
    lines.append("| --- | --- | --- |")
    for key, block in coverage["sensibilidad_corte_deriva"].items():
        lines.append(
            f"| {block['corte_deriva_hiper']:.0%} | "
            f"{', '.join(block['divisas_que_cambian']) or '-'} | "
            f"{block['empresas_afectadas']} |"
        )
    lines.append("")
    lines.append("## Cobertura de la cartera viva")
    lines.append("")
    portfolio = coverage["cartera_viva"]
    lines.append(f"- Filtro: `{portfolio['filtro']}`")
    lines.append(f"- Filas: {portfolio['n_filas']}; empresas: {portfolio['n_empresas']}; "
                 f"divisas: {portfolio['n_divisas']}")
    for grupo in ("cubiertas_bce", "sin_cobertura_bce"):
        block = portfolio[grupo]
        lines.append(
            f"- {grupo}: {block['n_divisas']} divisas ({', '.join(block['divisas']) or '-'}), "
            f"{block['empresas']} empresas"
        )
    lines.append(f"- Empresas en ambos grupos: {portfolio['empresas_ambos_grupos']}")
    lines.append("")
    lines.append("## Empresas de la cartera viva por tramo combinado (ultimo mes cerrado)")
    lines.append("")
    by_tramo = coverage["empresas_por_tramo_ultimo_mes"]
    before = coverage["empresas_por_tramo_volatilidad_ultimo_mes"]
    lines.append(f"Mes: {by_tramo['mes']}")
    lines.append("")
    lines.append("| Tramo | Empresas (solo volatilidad) | Empresas (combinado) |")
    lines.append("| --- | --- | --- |")
    for tramo in ("estable", "volatil", "hipervolatil"):
        block = by_tramo[tramo]
        lines.append(
            f"| {tramo} | {before[tramo]['empresas']} | {block['empresas']} |"
        )
    lines.append("")
    for tramo in ("estable", "volatil", "hipervolatil"):
        block = by_tramo[tramo]
        lines.append(
            f"- {tramo}: {block['empresas']} empresas distintas en divisas "
            f"({', '.join(block['divisas']) or '-'})"
        )
    lines.append("")
    lines.append(
        "Nota: una empresa con divisas en varios tramos aparece en cada uno; "
        "la suma no es el censo de empresas."
    )
    lines.append("")
    lines.append("## Divisas hipervolatiles por defecto")
    lines.append("")
    lines.append("Sin cobertura BCE en la ventana: "
                 + (", ".join(coverage["divisas_sin_cobertura_bce"]) or "-"))
    lines.append("Con cobertura BCE insuficiente en algun mes: "
                 + (", ".join(coverage["divisas_con_cobertura_insuficiente"]) or "-"))
    lines.append("")
    lines.append("## Resumen por divisa")
    lines.append("")
    lines.append(
        "| Divisa | Vol mediana | Deriva mediana | Deriva comp. mediana | "
        "Deriva comp. max | Meses deriva | Tramo vol modal | Tramo deriva modal | "
        "Tramo combinado modal | Meses BCE |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for currency, info in coverage["resumen_por_divisa"].items():
        lines.append(
            f"| {currency} | {fmt(info['vol_mediana'])} | {fmt(info['deriva_mediana'])} | "
            f"{fmt(info['deriva_componente_mediana'])} | {fmt(info['deriva_componente_max'])} | "
            f"{info['meses_con_deriva']}/{info['meses']} | {info['tramo_volatilidad_modal'] or '-'} | "
            f"{info['tramo_deriva_modal'] or '-'} | {info['tramo_modal']} | "
            f"{info['meses_cobertura_bce']}/{info['meses']} |"
        )
    lines.append("")
    lines.append("## Lectura honesta de la limitacion")
    lines.append("")
    linea = coverage["distribucion_deriva_componente"]
    lines.append(
        "La distribucion del componente de deriva es un CONTINUO, no dos "
        "bloques separados: p50="
        f"{fmt(linea.get('p50'))}, p75={fmt(linea.get('p75'))}, p90={fmt(linea.get('p90'))}, "
        f"max={fmt(linea.get('max'))}. Solo TRY es un valor atipico claro."
    )
    lines.append("")
    lines.append(
        "Meses por divisa en que la deriva sola ya da hipervolatil (de 24): "
        + ", ".join(
            f"{currency}={months}"
            for currency, months in coverage["meses_deriva_hiper_por_divisa"].items()
        )
    )
    lines.append("")
    lines.append(
        "Consecuencia: el corte del 6% es una decision de politica, no un hueco "
        "natural de la distribucion. En el mes de cierre arregla la inversion "
        "descrita (TRY la mas castigada, BRL sin erosion y sin subir de tramo), "
        "pero dentro de la ventana BRL, USD, HKD, NZD, CAD, KRW y PHP tuvieron "
        "episodios de depreciacion sostenida de 12 meses en 2024-2025 y la "
        "deriva los marca como hipervolatiles en esos meses. Eso es point-in-time "
        "correcto (en esos cortes SI perdian valor), pero significa que el indice "
        "sigue castigando a BRL en parte de la ventana. BRL y USD comparten el "
        "tramo volatil antes y despues: el indice por tramos no los separa aunque "
        "la volatilidad cruda de BRL sea mayor (0.087 frente a 0.055)."
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# --- CLI --------------------------------------------------------------------


def _resolve_invoices() -> Path:
    return paths.CLEAN_DIR / "invoices.parquet"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", type=Path, default=ECB_VENDOR_PATH)
    parser.add_argument("--invoices", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    import duckdb

    verify_vendor(args.vendor)
    invoices = args.invoices or _resolve_invoices()
    if not invoices.exists():
        print(f"ABORTADO: no existe {invoices}", file=sys.stderr)
        return 2

    con = duckdb.connect(":memory:")
    try:
        quotes, ecb_columns = load_ecb_quotes(con, args.vendor)
        months = closed_months()
        ecb_in_window = _ecb_currencies_in_window(quotes, months)
        portfolio_rows = con.execute(
            f"SELECT DISTINCT currency_norm FROM read_parquet('{invoices}') "
            f"WHERE {LIVE_PORTFOLIO_FILTER}"
        ).fetchall()
        portfolio_currencies = [r[0] for r in portfolio_rows if r[0]]
        grid = build_grid_currencies(ecb_in_window, portfolio_currencies)
        covered = set(ecb_in_window) | {"EUR"}
        rows = compute_fx_index(quotes, grid, months, frozenset(covered))
        assert_point_in_time(rows)
        portfolio = portfolio_census(con, invoices, covered, rows, months[-1])
    finally:
        con.close()

    coverage = build_coverage(rows, ecb_columns, portfolio, quotes, months)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in rows])
    frame["month"] = pd.to_datetime(frame["month"])
    frame["available_at"] = pd.to_datetime(frame["available_at"])
    frame.sort_values(["currency", "month"], inplace=True)
    frame.to_parquet(args.output_dir / "fx_index.parquet", index=False)
    (args.output_dir / "cobertura.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_report_md(coverage, args.output_dir / "report_index.md")
    print(
        json.dumps(
            {
                "output": str(args.output_dir),
                "filas": len(rows),
                "divisas": len(coverage["rejilla"]["divisas"]),
                "tramos": coverage["tramos"]["conteo_filas"],
                "tramos_volatilidad": coverage["tramos"]["conteo_filas_volatilidad"],
                "tramos_deriva": coverage["tramos"]["conteo_filas_deriva"],
                "deriva_ventana_meses": coverage["estadistico_deriva"]["ventana_meses"],
                "cambio_tramo_ultimo_mes": coverage["cambio_de_tramo_ultimo_mes"],
                "sin_cobertura_bce": coverage["cartera_viva"]["sin_cobertura_bce"]["divisas"],
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
