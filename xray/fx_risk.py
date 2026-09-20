"""fx_risk — cartera de cobros valorada por fecha de emision + indice FX empresa-mes.

QUE ES ESTA CAPA (tarea F3 del spec docs/SPEC_FX_RISK_INDEX.md)
---------------------------------------------------------------
Es la UNION de las tres piezas anteriores y el CONTRAFACTUAL medido:

1. Tipos diarios del BCE vendorizados (F1, ``data/fx_ecb/eurofxref-hist.csv``).
2. Indice de inestabilidad FX por divisa-mes (F1b,
   ``reports/fx_risk/fx_index.parquet``), con volatilidad y deriva separadas.
3. Exposicion empresa-mes de la cartera viva de cobros (F2,
   ``reports/fx_risk/exposicion.parquet``).

NO se toca ``xray/scoring_v4.py`` ni la formula. La capa se construye FUERA del
motor y el efecto sobre la nota se SIMULA (camino B, capa + contrafactual
medido). La decision de meterlo en la formula es del usuario.

DECISION DE PRODUCTO: VALORACION POR FECHA DE EMISION
-----------------------------------------------------
Instruccion literal del usuario: "busca segun la fecha de factura cual era el
tipo de cambio aquel dia". Cada factura viva en divisa cubierta por el BCE se
valora en EUR con el tipo del DIA DE EMISION (``issuance_date_ok``). Si ese dia
no cotiza (fin de semana, festivo) se usa el ultimo dia habil ANTERIOR con
cotizacion. NUNCA se interpola hacia delante ni se usa un tipo posterior:
seria fuga. Esto rescata las facturas no-EUR que no declaran su propio
``amount_eur`` (~10.8k en el ultimo corte).

PRIORIDAD DE VALORACION (declarada)
-----------------------------------
  (a) ``amount_eur`` nativo cuando ``amount_eur_source = 'reported'`` y la
      conversion no es ambigua: el tipo que declara la propia factura manda
      sobre cualquier referencia externa;
  (b) tipo BCE del dia de emision (facturas sin conversion propia);
  (c) sin valorar (nunca se imputa como cero).

VALORACION A FECHA DE CORTE Y PERDIDA YA INCURRIDA
--------------------------------------------------
La misma factura se valora ademas con el tipo BCE al CIERRE DEL MES DE CORTE
(``month_end``). La diferencia

    perdida_eur = eur_emision - eur_corte

es lo que esa factura YA ha perdido (o ganado, signo negativo) por movimiento
de divisa desde que se emitio. No es riesgo teorico: es dinero. Se publica por
factura agregada a empresa-mes y por divisa.

DISCIPLINA POINT-IN-TIME
------------------------
Ambas fechas (emision y corte) son <= cierre de m, y ``RateSeries`` solo
devuelve cotizaciones con ``day <= fecha_pedida``: desde el corte m no se lee
NUNCA una cotizacion posterior a m. ``tests/test_fx_risk.py`` lo demuestra por
truncado y por perturbacion.

INDICE FX EMPRESA-MES PONDERADO POR DINERO
------------------------------------------
La ponderacion es por DINERO (valor EUR de la cartera viva), no por numero de
facturas (decision del usuario). El indice vive en [0, 1] mediante una escala
declarada de severidad por tramo combinado de F1b:

    estable -> 0.0 ; volatil -> 0.5 ; hipervolatil -> 1.0

Se publican por separado la contribucion por VOLATILIDAD y por DERIVA (F1b
dejo los tramos en columnas distintas justamente para medir cual pesa mas) y el
indice combinado. Ademas ``indice_fx_vol`` e ``indice_fx_deriva`` permiten
simular el contrafactual con un solo eje y medir el desglose del efecto.

DIVISAS OPACAS (sin cobertura BCE ni FMI)
-----------------------------------------
Una divisa sin tipo BCE **ni FMI** no se puede pasar a EUR y NO se imputa.
Convencion (la misma de ``xray/payment_delay_v2.py``: nulo nunca es cero):

  * empresa con SOLO divisas opacas -> proporcion de dinero inestable = 100%
    exacta, por construccion, SIN necesidad de tipo. Valor puntual 1.0, no
    intervalo.
  * empresa con MEZCLA de opacas y valorables -> la proporcion no se puede
    cerrar. ``indice_fx`` = NULL y ``[indice_fx_min, indice_fx_max]`` acota el
    caso en que lo opaco es despreciable (min: peso 0) y el caso en que lo
    domina (max: 1.0).

SEGUNDA FUENTE DE TIPOS (F1c, ENCHUFADA EN F6)
----------------------------------------------
ARS, COP, CLP y PEN ya NO son opacas. Se consume ``reports/fx_risk/
rates_extra.parquet`` (serie mensual FMI/IFS ``XDC_EUR``, generada por
``xray/fx_rates_extra.py``) y el enganche vive DENTRO de este modulo, no en un
adaptador aparte: duplicar la logica de corte en dos sitios es deuda que se
desincroniza en silencio. Reglas declaradas:

  * tipo ``fin_de_periodo`` (stock a una fecha de corte), no ``periodo_medio``;
  * prioridad de valoracion: (a) ``amount_eur`` nativo ``reported``;
    (b) tipo BCE del dia de emision; (c) tipo FMI point-in-time; (d) sin valorar;
  * seleccion point-in-time: para una fecha objetivo dentro del corte m se usa
    la observacion con ``available_at <= cierre de m`` mas cercana a esa fecha.
    Nunca se lee una fila publicada despues del corte (eso seria fuga). El
    desfase resultante, en meses, se publica en columnas por divisa y
    empresa-mes (``lag_*_fmi_*``) para que sea auditable: el IFS publica con
    ~2 meses de retardo (4 en COP).
  * ``beta_fx`` (0.05) y la formula NO se tocan: F6 cambia de que datos se
    alimenta la capa, no como se calcula el factor.

Con ``--sin-extra`` (o sin el parquet) el modulo reproduce EXACTAMENTE el
comportamiento anterior al enganche. ``--extra-rates`` (o
``XRAY_FX_EXTRA_RATES``) permite apuntar a otro parquet con el mismo esquema.

CONTRAFACTUAL (entregable central)
----------------------------------
Fuera del motor se simula

    H_fx = H_final * (1 - beta_fx * indice_fx)

sobre la poblacion evaluable de v4 (957 empresas; ``health_score`` no nulo y
``excluida = false``) para varios ``beta_fx``. Se mide |dH|, empresas afectadas
e intactas, correlacion de Spearman contra mora y multiplicador de deuda
(ortogonalidad: el mejor argumento a favor del factor) y el desglose
volatilidad/deriva.

El motor (``compute_fx_risk``) es PURO: sin IO, sin reloj, sin estado mutable,
no muta su entrada. El CLI escribe los tres artefactos de F3 bajo
``reports/fx_risk/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from xray import paths
from xray.period import FIRST_MONTH, LAST_MONTH

__all__ = [
    "AssessmentFx",
    "CompanyMonthFxRisk",
    "EcbQuote",
    "ExtraRateRow",
    "ExtraRateSeries",
    "ExtraSelection",
    "FxIndexEntry",
    "FxIndexTable",
    "FxInvoiceRecord",
    "RateSeries",
    "build_census",
    "closed_months",
    "compute_counterfactual",
    "compute_fx_risk",
    "load_ecb_quotes",
    "load_extra_quotes",
    "load_extra_rate_series",
    "load_fx_index",
    "load_invoices",
    "main",
    "measure_fmi_lags",
    "month_end",
    "spearman",
]

# --------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------- #
DOC_INVOICE = "invoice"
FLOW_SIDE_IN = "inflow"
STATUS_CANCEL = "cancel"
STATUS_PAID = "paid"

DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "fx_risk"
ECB_VENDOR_PATH = paths.ROOT / "data" / "fx_ecb" / "eurofxref-hist.csv"
ECB_VENDOR_SHA256 = (
    "22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d"
)
ECB_NON_CURRENCY_COLUMNS = ("Date", "column42")

# Segunda fuente de tipos (F1c): serie mensual FMI/IFS `XDC_EUR` para las cuatro
# divisas opacas de la cartera (ARS, COP, CLP, PEN). Se consume el parquet ya
# calculado por `xray/fx_rates_extra.py`; aqui NO se recalcula la serie, solo se
# selecciona point-in-time. Decision de F6: tipo `fin_de_periodo` (se valora un
# STOCK a una fecha de corte, no un flujo medio) y el enganche vive DENTRO de
# este modulo para no duplicar la logica de corte en dos sitios.
EXTRA_RATES_PATH = paths.ROOT / "reports" / "fx_risk" / "rates_extra.parquet"
EXTRA_TIPO = "fin_de_periodo"
FMI_FUENTE = "imf_ifs_er_xdc_eur"
# Divisa -> fuente. Solo documentacion de la convencion: la clasificacion real
# sale de `ExtraRateSeries.has_currency`.
DIVISAS_EXTRA = ("ARS", "CLP", "COP", "PEN")

ASSESSMENTS_PATH = paths.ROOT / "reports" / "score_v4" / "assessments.parquet"
INVOICES_PATH = paths.CLEAN_DIR / "invoices.parquet"

# Inputs producidos por los worktrees de F1b y F2. Se resuelven con variable
# de entorno (XRAY_FX_INDEX_PATH / XRAY_FX_EXPOSURE_PATH), luego con la ruta
# local (por si los artefactos ya estan en main) y por ultimo con la ruta
# absoluta del worktree hermano (dispatch de F3).
F1B_INDEX_PATH = Path(
    os.environ.get(
        "XRAY_FX_INDEX_PATH",
        "/Users/josemariaiznardosalar/orca/workspaces/embat-hack/"
        "f1-ecb/reports/fx_risk/fx_index.parquet",
    )
)
F2_EXPOSURE_PATH = Path(
    os.environ.get(
        "XRAY_FX_EXPOSURE_PATH",
        "/Users/josemariaiznardosalar/orca/workspaces/embat-hack/"
        "f2-exposicion/reports/fx_risk/exposicion.parquet",
    )
)
LOCAL_INDEX_PATH = DEFAULT_OUTPUT_DIR / "fx_index.parquet"
LOCAL_EXPOSURE_PATH = DEFAULT_OUTPUT_DIR / "exposicion.parquet"

# Tramos y escala de severidad declarada (F1b los publica como categorias).
TRAMO_ESTABLE = "estable"
TRAMO_VOLATIL = "volatil"
TRAMO_HIPERVOLATIL = "hipervolatil"
SEVERIDAD_TRAMO = {
    TRAMO_ESTABLE: 0.0,
    TRAMO_VOLATIL: 0.5,
    TRAMO_HIPERVOLATIL: 1.0,
}
# Una divisa sin cobertura BCE se asume inestable por prudencia (decision F1b).
SEVERIDAD_SIN_COBERTURA = 1.0

# Divisas que el BCE no publica en la ventana (verificado por F1b). Se listan
# solo como documentacion/verificacion de recuentos; la clasificacion real sale
# de si RateSeries tiene o no cotizacion para la divisa.
OPACAS_CONOCIDAS = ("AED", "ARS", "CLP", "COP", "MAD", "MZN", "NAD", "PEN")

# Confidence: mismos umbrales de cartera "gruesa" que payment_delay_v2 / F2.
MIN_FACTURAS_CONFIANZA_ALTA = 3
MIN_EUR_CONFIANZA_ALTA = 2000.0
CONFIDENCE_LEVELS = ("ninguna", "baja", "media", "alta")

# Betas del contrafactual (decision del usuario: varios escenarios).
BETAS_POR_DEFECTO = (0.05, 0.10, 0.25)
# Umbral de cambio MATERIAL por empresa-mes, en puntos de nota. Se declara y
# justifica en el informe: 1 punto sobre una escala 0-100 es el 1% de la nota y
# esta por encima de la mediana de las penalizaciones ya existentes (medido en
# la poblacion evaluable: 0,34 puntos de mora; 0,78 de multiplicador de deuda).
UMBRAL_MATERIAL_PUNTOS = 1.0

CRITERIO_UNIVERSO = (
    "flow_side='inflow', document_type_norm='invoice', status_norm<>'cancel', "
    "issuance_date_ok <= fin de m (cartera viva de cobros: incluye lo aun no "
    "vencido) y NOT (status_norm='paid' AND payment_date_ok <= fin de m). "
    "payment_date_ok solo es fecha REAL si status_norm='paid'; en otro estado "
    "es fecha PREVISTA y se ignora (criterio status-aware, identico a F2)."
)

CRITERIO_CONFIDENCE = (
    "ninguna=sin cartera viva de cobros; baja=facturas no valoradas al corte "
    ">=50% de la cartera (en numero); media=algun hueco de valoracion "
    "(incluye intervalo por divisas opacas) o cartera fina "
    f"(<{MIN_FACTURAS_CONFIANZA_ALTA} facturas o <{MIN_EUR_CONFIANZA_ALTA:g} "
    "EUR); alta=sin huecos y cartera gruesa"
)

LIMITACIONES = (
    "El agujero residual de las divisas opacas (AED, MAD, MZN, NAD): el BCE "
    "no las publica y el FMI/IFS tampoco, asi que no se imputa un tipo. Una "
    "empresa con solo opacas publica 100% inestable por construccion; una con "
    "mezcla publica un intervalo con el valor puntual a NULL. ARS, COP, CLP y "
    "PEN ya NO son opacas: las cubre la segunda fuente (FMI/IFS XDC_EUR) con "
    "regla point-in-time. La deriva PASADA no predice la "
    "deriva FUTURA: el componente de deriva mide erosion ya ocurrida en 12 "
    "meses, no una expectativa. El corte del 6% de deriva es una decision de "
    "politica, no un hueco natural de la distribucion (lo dice el informe de "
    "F1b): la distribucion de deriva es un continuo y el corte se eligio para "
    "separar la cola de depreciacion gestionada del bloque leve. La fuente no "
    "tiene fecha de importacion: la cartera viva se reconstruye por emision y "
    "pago, no por disponibilidad de registro certificada. El FMI/IFS publica "
    "con ~2 meses de retardo (4 en COP), asi que valorar a una fecha reciente "
    "usa un tipo mas antiguo; el desfase se publica en meses, no se esconde."
)


# --------------------------------------------------------------------- #
# Utilidades de calendario y estadistica
# --------------------------------------------------------------------- #
def month_end(month: date) -> date:
    """Ultimo dia natural del mes de ``month``."""
    first_next = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def closed_months(start: date = FIRST_MONTH, end: date = LAST_MONTH) -> list[date]:
    """Primer dia de cada mes cerrado entre ``start`` y ``end`` (inclusive)."""
    months: list[date] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def quantile(sorted_values: list[float], q: float) -> float | None:
    """Cuantil con interpolacion lineal (equivalente a numpy.percentile)."""
    if not sorted_values:
        return None
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo]))


def _rank(values: list[float]) -> list[float]:
    """Rangos con empates promediados (para Spearman)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Correlacion de Spearman sin scipy (rango con empates + Pearson)."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


# --------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class EcbQuote:
    """Una cotizacion diaria EUR/divisa (unidades de divisa por 1 EUR)."""

    day: date
    currency: str
    rate: float


@dataclass(frozen=True)
class FxInvoiceRecord:
    """Factura de cobro normalizada; el motor no ve el parquet."""

    operation_id: str
    company_id: str
    currency: str | None
    status: str
    issuance_date: date | None
    due_date: date | None
    payment_date: date | None
    amount: float | None
    amount_eur: float | None
    amount_eur_source: str
    fx_ambiguous: bool


class RateSeries:
    """Serie de tipos EUR/divisa con busqueda "ultimo dia habil <= fecha".

    Nunca devuelve una cotizacion POSTERIOR a la fecha pedida: es la garantia
    de no-fuga. Si la divisa no tiene ninguna cotizacion <= fecha, devuelve
    None (no se imputa).
    """

    def __init__(self, quotes) -> None:
        acc: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for q in quotes:
            acc[q.currency].append((q.day, float(q.rate)))
        self._days: dict[str, list[date]] = {}
        self._rates: dict[str, list[float]] = {}
        for cur, items in acc.items():
            items.sort(key=lambda t: t[0])
            self._days[cur] = [d for d, _ in items]
            self._rates[cur] = [r for _, r in items]

    def has_currency(self, currency: str | None) -> bool:
        return currency in self._days

    def rate_on_or_before(self, currency: str | None, day: date) -> float | None:
        if currency is None or currency not in self._days:
            return None
        idx = bisect_right(self._days[currency], day) - 1
        if idx < 0:
            return None
        return self._rates[currency][idx]

    def day_on_or_before(self, currency: str | None, day: date) -> date | None:
        if currency is None or currency not in self._days:
            return None
        idx = bisect_right(self._days[currency], day) - 1
        if idx < 0:
            return None
        return self._days[currency][idx]

    @property
    def currencies(self) -> set[str]:
        return set(self._days)


@dataclass(frozen=True)
class ExtraRateRow:
    """Fila mensual de la segunda fuente (FMI) ya normalizada.

    `month` es el primer dia del mes de referencia; `dia_representativo` su
    cierre (el valor `fin_de_periodo` mide el tipo al cierre de ese mes) y
    `available_at` la fecha a partir de la cual el IFS ya habia publicado la
    fila (modelada en `xray/fx_rates_extra.py` con evidencia de vintages).
    """

    currency: str
    month: date
    rate: float
    dia_representativo: date
    available_at: date


@dataclass(frozen=True)
class ExtraSelection:
    """Tipo extra elegido point-in-time para una fecha objetivo."""

    rate: float
    month: date
    lag_meses: int


class ExtraRateSeries:
    """Serie mensual extra (FMI) con seleccion point-in-time sin fuga.

    Regla de F6 (declarada en el dispatch): para valorar a una fecha objetivo
    dentro del corte m se toma la observacion con ``available_at <= cierre de
    m`` **mas cercana** a esa fecha objetivo. Nunca se usa una fila con
    ``available_at`` posterior al corte: eso seria fuga. Usar un tipo de hace
    dos meses (o cuatro en COP) es correcto y se publica como `lag_meses` para
    que sea auditable.
    """

    def __init__(self, rows: list[ExtraRateRow]) -> None:
        acc: dict[str, list[ExtraRateRow]] = defaultdict(list)
        for row in rows:
            acc[row.currency].append(row)
        self._rep: dict[str, list[date]] = {}
        self._avail: dict[str, list[date]] = {}
        self._rate: dict[str, list[float]] = {}
        self._month: dict[str, list[date]] = {}
        for cur, items in acc.items():
            items.sort(key=lambda r: r.month)
            self._rep[cur] = [r.dia_representativo for r in items]
            self._avail[cur] = [r.available_at for r in items]
            self._rate[cur] = [r.rate for r in items]
            self._month[cur] = [r.month for r in items]

    def has_currency(self, currency: str | None) -> bool:
        return currency in self._rate

    @property
    def currencies(self) -> set[str]:
        return set(self._rate)

    def select_point_in_time(
        self, currency: str | None, target: date, cut: date
    ) -> ExtraSelection | None:
        """Observacion publicada al cierre de `cut` mas cercana a `target`.

        `cut` es el cierre del mes de corte m. El prefijo de filas publicadas
        (`available_at <= cut`) es contiguo porque `available_at` crece con el
        mes; dentro de el se elige el cierre de mes mas cercano al objetivo y,
        en empate, el ANTERIOR al objetivo (nunca uno posterior si hay uno
        anterior igual de cerca). Devuelve None si no hay nada publicado: nulo
        nunca se rellena.
        """
        rep = self._rep.get(currency) if currency is not None else None
        if not rep:
            return None
        hi = bisect_right(self._avail[currency], cut)
        if hi == 0:
            return None
        idx = bisect_left(rep, target, 0, hi)
        prev_i = idx - 1
        next_i = idx if idx < hi else None
        if prev_i < 0 and next_i is None:
            return None
        if prev_i < 0:
            best = next_i
        elif next_i is None:
            best = prev_i
        else:
            d_prev = abs((rep[prev_i] - target).days)
            d_next = abs((rep[next_i] - target).days)
            # Empate -> el anterior (no adelanta un cierre de mes futuro).
            best = prev_i if d_prev <= d_next else next_i
        month = self._month[currency][best]
        lag = (target.year * 12 + target.month) - (
            month.year * 12 + month.month
        )
        return ExtraSelection(
            rate=self._rate[currency][best],
            month=month,
            lag_meses=lag,
        )


@dataclass(frozen=True)
class FxIndexEntry:
    """Fila divisa-mes del indice de F1b (solo lo que F3 consume)."""

    tramo_volatilidad: str | None
    tramo_deriva: str | None
    tramo: str
    cobertura_bce: bool


class FxIndexTable:
    """Indice divisa-mes de F1b indexado por (divisa, primer dia de mes)."""

    def __init__(self, entries: dict[tuple[str, date], FxIndexEntry]) -> None:
        self._entries = entries

    def get(self, currency: str | None, month: date) -> FxIndexEntry | None:
        if currency is None:
            return None
        return self._entries.get((currency, month.replace(day=1)))

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class _CurrencyAcc:
    """Acumulador por divisa dentro de una empresa-mes (mutable, interno)."""

    currency: str | None
    es_opaca: bool
    tramo_vol: str | None
    tramo_der: str | None
    tramo_comb: str | None
    n: int = 0
    n_val_corte: int = 0
    eur_corte: float = 0.0
    n_val_emision: int = 0
    eur_emision: float = 0.0
    n_rescatada: int = 0
    n_perdida: int = 0
    perdida: float = 0.0
    importe_original: float = 0.0
    n_opaca: int = 0
    importe_opaco_original: float = 0.0
    importe_opaco_eur: float = 0.0
    n_opaca_eur: int = 0
    n_rescatada_fmi: int = 0
    lag_emision_fmi: list[int] = field(default_factory=list)
    lag_corte_fmi: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        def mediana(values: list[int]) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            return quantile([float(v) for v in ordered], 0.50)

        return {
            "divisa": self.currency,
            "es_opaca": self.es_opaca,
            "tramo_volatilidad": self.tramo_vol,
            "tramo_deriva": self.tramo_der,
            "tramo": self.tramo_comb,
            "n_facturas": self.n,
            "importe_original": round(self.importe_original, 6),
            "n_valorables_corte": self.n_val_corte,
            "eur_corte": round(self.eur_corte, 6),
            "n_valorables_emision": self.n_val_emision,
            "eur_emision": round(self.eur_emision, 6),
            "n_rescatadas_bce": self.n_rescatada,
            "n_rescatadas_fmi": self.n_rescatada_fmi,
            "n_valorables_emision_fmi": len(self.lag_emision_fmi),
            "lag_emision_fmi_mediano_meses": mediana(self.lag_emision_fmi),
            "lag_emision_fmi_max_meses": (
                max(self.lag_emision_fmi) if self.lag_emision_fmi else None
            ),
            "n_valorables_corte_fmi": len(self.lag_corte_fmi),
            "lag_corte_fmi_mediano_meses": mediana(self.lag_corte_fmi),
            "lag_corte_fmi_max_meses": (
                max(self.lag_corte_fmi) if self.lag_corte_fmi else None
            ),
            "n_con_perdida": self.n_perdida,
            "perdida_eur": (
                round(self.perdida, 6) if self.n_perdida > 0 else None
            ),
            "n_opacas": self.n_opaca,
            "importe_opaco_original": round(self.importe_opaco_original, 6),
            "importe_opaco_eur_reportado": round(self.importe_opaco_eur, 6),
            "n_opacas_eur_reportado": self.n_opaca_eur,
        }


@dataclass(frozen=True)
class CompanyMonthFxRisk:
    """Resultado (company_id, mes cerrado). Nulo nunca es cero."""

    company_id: str
    month: date
    n_facturas_vivas: int = 0
    n_cancel_excluidas: int = 0
    importe_vivo_original: float = 0.0
    importe_vivo_eur_corte: float | None = None
    importe_vivo_eur_emision: float | None = None
    importe_opaco_original: float = 0.0
    importe_opaco_eur_reportado: float | None = None
    perdida_eur: float | None = None
    n_perdida: int = 0
    n_valorables_corte: int = 0
    n_no_valorables_corte: int = 0
    n_rescatadas_bce: int = 0
    n_rescatadas_fmi: int = 0
    n_valorables_corte_fmi: int = 0
    n_valorables_emision_fmi: int = 0
    lag_fmi_emision_mediano_meses: float | None = None
    lag_fmi_emision_max_meses: int | None = None
    lag_fmi_corte_mediano_meses: float | None = None
    lag_fmi_corte_max_meses: int | None = None
    n_opacas: int = 0
    pct_estable: float | None = None
    pct_volatil: float | None = None
    pct_hipervolatil: float | None = None
    pct_dinero_inestable: float | None = None
    contrib_volatilidad: float | None = None
    contrib_deriva: float | None = None
    indice_fx: float | None = None
    indice_fx_vol: float | None = None
    indice_fx_deriva: float | None = None
    indice_fx_min: float | None = None
    indice_fx_max: float | None = None
    indice_fx_vol_min: float | None = None
    indice_fx_vol_max: float | None = None
    indice_fx_deriva_min: float | None = None
    indice_fx_deriva_max: float | None = None
    tiene_opacas: bool = False
    solo_opacas: bool = False
    indice_es_intervalo: bool = False
    confidence: str = "ninguna"
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    por_divisa: tuple[dict, ...] = ()

    def to_row(self) -> dict:
        def money(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        return {
            "company_id": self.company_id,
            "month": self.month,
            "n_facturas_vivas": self.n_facturas_vivas,
            "importe_vivo_original": round(self.importe_vivo_original, 6),
            "importe_vivo_eur_corte": money(self.importe_vivo_eur_corte),
            "importe_vivo_eur_emision": money(self.importe_vivo_eur_emision),
            "importe_opaco_original": round(self.importe_opaco_original, 6),
            "importe_opaco_eur_reportado": money(self.importe_opaco_eur_reportado),
            "perdida_eur": money(self.perdida_eur),
            "n_perdida": self.n_perdida,
            "n_valorables_corte": self.n_valorables_corte,
            "n_no_valorables_corte": self.n_no_valorables_corte,
            "n_rescatadas_bce": self.n_rescatadas_bce,
            "n_rescatadas_fmi": self.n_rescatadas_fmi,
            "n_valorables_corte_fmi": self.n_valorables_corte_fmi,
            "n_valorables_emision_fmi": self.n_valorables_emision_fmi,
            "lag_fmi_emision_mediano_meses": self.lag_fmi_emision_mediano_meses,
            "lag_fmi_emision_max_meses": self.lag_fmi_emision_max_meses,
            "lag_fmi_corte_mediano_meses": self.lag_fmi_corte_mediano_meses,
            "lag_fmi_corte_max_meses": self.lag_fmi_corte_max_meses,
            "n_opacas": self.n_opacas,
            "pct_estable": self.pct_estable,
            "pct_volatil": self.pct_volatil,
            "pct_hipervolatil": self.pct_hipervolatil,
            "pct_dinero_inestable": self.pct_dinero_inestable,
            "contrib_volatilidad": self.contrib_volatilidad,
            "contrib_deriva": self.contrib_deriva,
            "indice_fx": self.indice_fx,
            "indice_fx_vol": self.indice_fx_vol,
            "indice_fx_deriva": self.indice_fx_deriva,
            "indice_fx_min": self.indice_fx_min,
            "indice_fx_max": self.indice_fx_max,
            "indice_fx_vol_min": self.indice_fx_vol_min,
            "indice_fx_vol_max": self.indice_fx_vol_max,
            "indice_fx_deriva_min": self.indice_fx_deriva_min,
            "indice_fx_deriva_max": self.indice_fx_deriva_max,
            "tiene_opacas": self.tiene_opacas,
            "solo_opacas": self.solo_opacas,
            "indice_es_intervalo": self.indice_es_intervalo,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "flags": list(self.flags),
            "n_cancel_excluidas": self.n_cancel_excluidas,
            "por_divisa": json.dumps(
                list(self.por_divisa), ensure_ascii=False, allow_nan=False
            ),
        }


# --------------------------------------------------------------------- #
# Valoracion
# --------------------------------------------------------------------- #
def _valorar_emision(
    rec: FxInvoiceRecord,
    rates: RateSeries,
    extra: ExtraRateSeries | None = None,
    cut: date | None = None,
) -> tuple[float | None, str | None, bool]:
    """Valor en EUR a fecha de emision con la prioridad declarada.

    Prioridad (dispatch F6): (a) `amount_eur` nativo si `reported`; (b) tipo BCE
    del dia de emision; (c) tipo FMI segun la regla point-in-time (observacion
    con `available_at <= cierre de m` mas cercana a la fecha objetivo); (d) sin
    valorar. ``rescatada_bce`` es True si el valor procede de un tipo externo
    (BCE o FMI) y la factura NO tenia conversion propia.
    """
    eur, _fuente, rescatada, _sel = _valorar_emision_detalle(rec, rates, extra, cut)
    return eur, _fuente, rescatada


def _valorar_emision_detalle(
    rec: FxInvoiceRecord,
    rates: RateSeries,
    extra: ExtraRateSeries | None = None,
    cut: date | None = None,
) -> tuple[float | None, str | None, bool, ExtraSelection | None]:
    """Como `_valorar_emision` pero devuelve tambien la seleccion FMI."""
    if rec.currency == "EUR":
        value = rec.amount if _finite(rec.amount) else rec.amount_eur
        return (value, "identity" if _finite(value) else None, False, None)
    if (
        _finite(rec.amount_eur)
        and rec.amount_eur_source == "reported"
        and not rec.fx_ambiguous
    ):
        return (rec.amount_eur, "reported", False, None)
    if rec.issuance_date is not None and rec.currency is not None and _finite(rec.amount):
        rate = rates.rate_on_or_before(rec.currency, rec.issuance_date)
        if rate is not None and rate > 0:
            return (rec.amount / rate, "bce_emision", True, None)
        if extra is not None and cut is not None:
            sel = extra.select_point_in_time(rec.currency, rec.issuance_date, cut)
            if sel is not None:
                return (rec.amount / sel.rate, "fmi_emision", True, sel)
    return (None, None, False, None)


def _valorar_corte_detalle(
    rec: FxInvoiceRecord,
    rates: RateSeries,
    cut: date,
    extra: ExtraRateSeries | None = None,
) -> tuple[float | None, ExtraSelection | None]:
    """Valor en EUR al cierre del mes de corte y la seleccion FMI si aplica.

    Prioridad: tipo BCE al corte si la divisa esta cubierta; si no, tipo FMI
    point-in-time (observacion publicada al cierre del corte mas cercana al
    cierre del mes).
    """
    if rec.currency == "EUR":
        value = rec.amount if _finite(rec.amount) else rec.amount_eur
        return (value if _finite(value) else None, None)
    if rec.currency is None or not _finite(rec.amount):
        return (None, None)
    rate = rates.rate_on_or_before(rec.currency, cut)
    if rate is not None and rate > 0:
        return (rec.amount / rate, None)
    if extra is not None:
        sel = extra.select_point_in_time(rec.currency, cut, cut)
        if sel is not None:
            return (rec.amount / sel.rate, sel)
    return (None, None)


def _valorar_corte(
    rec: FxInvoiceRecord,
    rates: RateSeries,
    cut: date,
    extra: ExtraRateSeries | None = None,
) -> float | None:
    """Valor en EUR al cierre del mes de corte (tipo BCE <= cut o tipo FMI)."""
    return _valorar_corte_detalle(rec, rates, cut, extra)[0]


def _valorar_emision_fmi(
    rec: FxInvoiceRecord,
    extra: ExtraRateSeries | None,
    cut: date,
) -> tuple[float | None, ExtraSelection | None]:
    """Rescate FMI a fecha de emision; (None, None) si no aplica o no hay tipo."""
    if (
        extra is None
        or rec.currency is None
        or rec.currency == "EUR"
        or rec.issuance_date is None
        or not _finite(rec.amount)
    ):
        return (None, None)
    sel = extra.select_point_in_time(rec.currency, rec.issuance_date, cut)
    if sel is None:
        return (None, None)
    return (rec.amount / sel.rate, sel)


def _severidad(tramo: str | None, sin_cobertura: bool) -> float:
    if sin_cobertura:
        return SEVERIDAD_SIN_COBERTURA
    if tramo is None:
        return SEVERIDAD_SIN_COBERTURA
    return SEVERIDAD_TRAMO.get(tramo, SEVERIDAD_SIN_COBERTURA)


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
def compute_fx_risk(
    invoices: list[FxInvoiceRecord],
    months: list[date],
    rates: RateSeries,
    index: FxIndexTable,
    companies: list[str] | None = None,
    extra_rates: ExtraRateSeries | None = None,
) -> list[CompanyMonthFxRisk]:
    """Motor PURO: valoracion por emision/corte + indice FX empresa-mes.

    - ``invoices``: facturas ya filtradas a document_type='invoice' y
      flow_side='inflow'. El motor aplica el criterio status-aware y de
      emision, y cuenta aparte las canceladas.
    - ``months``: primeros de mes cerrado; el corte de cada mes es su fin.
    - ``rates``: cotizaciones BCE; solo se consultan fechas <= corte.
    - ``index``: tramos de F1b por divisa-mes.
    - ``extra_rates``: segunda fuente mensual (FMI, F1c) con disponibilidad
      point-in-time. Si es None el motor reproduce exactamente el
      comportamiento anterior al enganche. Cuando existe, el tipo BCE manda
      sobre el FMI (prioridad declarada) y el FMI solo se usa si el BCE no
      cubre la divisa o no tiene cotizacion <= fecha.

    Nunca mira un mes posterior al corte que calcula; no muta la entrada;
    determinista; sin reloj ni IO.
    """
    by_company: dict[str, list[FxInvoiceRecord]] = defaultdict(list)
    for inv in invoices:
        by_company[inv.company_id].append(inv)

    ids = list(companies) if companies is not None else sorted(by_company)
    results: list[CompanyMonthFxRisk] = []
    for cid in ids:
        recs = by_company.get(cid, [])
        if recs:
            recs = sorted(recs, key=lambda r: (r.issuance_date or date.min))
        # La valoracion BCE (y la conversion propia) no depende del corte: se
        # precalcula una vez. El rescate FMI si depende del corte (su
        # disponibilidad point-in-time), asi que se resuelve mes a mes.
        emisiones = [_valorar_emision(r, rates) for r in recs]
        for month in months:
            results.append(
                _empresa_mes(cid, month, recs, emisiones, rates, index,
                             extra_rates)
            )
    return results


def _empresa_mes(
    company_id: str,
    month: date,
    recs: list[FxInvoiceRecord],
    emisiones: list[tuple[float | None, str | None, bool]],
    rates: RateSeries,
    index: FxIndexTable,
    extra_rates: ExtraRateSeries | None = None,
) -> CompanyMonthFxRisk:
    cut = month_end(month)
    acc: dict[str | None, _CurrencyAcc] = {}
    n_vivas = 0
    n_cancel = 0
    n_perdida = 0
    perdida_total = 0.0
    lags_emision_fmi: list[int] = []
    lags_corte_fmi: list[int] = []

    for rec, (eur_emision, _fuente, rescatada) in zip(recs, emisiones):
        if rec.status == STATUS_CANCEL:
            if rec.issuance_date is not None and rec.issuance_date <= cut:
                n_cancel += 1
            continue
        if rec.issuance_date is None or rec.issuance_date > cut:
            continue
        if (
            rec.status == STATUS_PAID
            and rec.payment_date is not None
            and rec.payment_date <= cut
        ):
            continue
        n_vivas += 1

        entry = index.get(rec.currency, month)
        cubierta_bce = rates.has_currency(rec.currency)
        cubierta_extra = (
            extra_rates is not None and extra_rates.has_currency(rec.currency)
        )
        es_opaca = rec.currency not in (None, "EUR") and not (
            cubierta_bce or cubierta_extra
        )
        cur = acc.get(rec.currency)
        if cur is None:
            cur = acc[rec.currency] = _CurrencyAcc(
                currency=rec.currency,
                es_opaca=es_opaca,
                tramo_vol=entry.tramo_volatilidad if entry else None,
                tramo_der=entry.tramo_deriva if entry else None,
                tramo_comb=entry.tramo if entry else None,
            )
        cur.n += 1
        if _finite(rec.amount):
            cur.importe_original += rec.amount

        # Prioridad de valoracion a emision: lo BCE/reportado ya viene
        # precalculado; si no hay valor, se intenta el rescate FMI.
        if eur_emision is None:
            eur_fmi, sel_emision = _valorar_emision_fmi(rec, extra_rates, cut)
            if eur_fmi is not None:
                eur_emision = eur_fmi
                rescatada = True
                cur.n_rescatada_fmi += 1
                if sel_emision is not None:
                    cur.lag_emision_fmi.append(sel_emision.lag_meses)
                    lags_emision_fmi.append(sel_emision.lag_meses)

        eur_corte, sel_corte = _valorar_corte_detalle(
            rec, rates, cut, extra_rates
        )
        if eur_corte is not None:
            cur.n_val_corte += 1
            cur.eur_corte += eur_corte
            if sel_corte is not None:
                cur.lag_corte_fmi.append(sel_corte.lag_meses)
                lags_corte_fmi.append(sel_corte.lag_meses)

        if eur_emision is not None:
            cur.n_val_emision += 1
            cur.eur_emision += eur_emision
            if rescatada:
                cur.n_rescatada += 1

        if es_opaca:
            cur.n_opaca += 1
            if _finite(rec.amount):
                cur.importe_opaco_original += rec.amount
            if eur_emision is not None:
                cur.importe_opaco_eur += eur_emision
                cur.n_opaca_eur += 1

        if eur_emision is not None and eur_corte is not None:
            cur.n_perdida += 1
            cur.perdida += eur_emision - eur_corte
            n_perdida += 1
            perdida_total += eur_emision - eur_corte

    rows = [c for c in acc.values() if c.n > 0]
    rows.sort(key=lambda c: (c.es_opaca, c.currency or ""))
    return _resumen_empresa_mes(
        company_id, month, rows, n_vivas, n_cancel, n_perdida, perdida_total,
        lags_emision_fmi, lags_corte_fmi,
    )


def _resumen_empresa_mes(
    company_id: str,
    month: date,
    rows: list[_CurrencyAcc],
    n_vivas: int,
    n_cancel: int,
    n_perdida: int,
    perdida_total: float,
    lags_emision_fmi: list[int] | None = None,
    lags_corte_fmi: list[int] | None = None,
) -> CompanyMonthFxRisk:
    if n_vivas == 0:
        return CompanyMonthFxRisk(
            company_id=company_id,
            month=month,
            n_facturas_vivas=0,
            n_cancel_excluidas=n_cancel,
            confidence="ninguna",
            reasons=("sin_cartera_viva_de_cobros",),
        )

    tiene_opacas = any(c.es_opaca for c in rows)
    n_opacas = sum(c.n_opaca for c in rows)
    n_no_val_corte = sum(c.n - c.n_val_corte for c in rows)
    n_val_corte = n_vivas - n_no_val_corte
    importe_opaco_original = sum(
        c.importe_opaco_original for c in rows if c.es_opaca
    )
    importe_opaco_eur = sum(c.importe_opaco_eur for c in rows if c.es_opaca)
    tiene_opaco_eur = any(c.n_opaca_eur > 0 for c in rows if c.es_opaca)

    # Pesos del indice: SOLO divisas no opacas. Peso = valor al corte si se
    # conoce; si no (hueco de cotizacion al cierre), valor a emision como
    # proxy declarado. Las opacas NO entran en el peso (convencion de intervalo).
    pesos: list[tuple[_CurrencyAcc, float]] = []
    for c in rows:
        if c.es_opaca:
            continue
        peso = c.eur_corte
        if c.n_val_corte == 0 and c.n_val_emision > 0:
            peso = c.eur_emision
        if peso > 0:
            pesos.append((c, peso))
    total_peso = sum(p for _, p in pesos)

    def weighted(selector) -> float | None:
        if total_peso <= 0:
            return None
        return sum(selector(c) * p for c, p in pesos) / total_peso

    sev_comb = lambda c: _severidad(c.tramo_comb, c.es_opaca)  # noqa: E731
    sev_vol = lambda c: _severidad(c.tramo_vol, c.es_opaca)  # noqa: E731
    sev_der = lambda c: _severidad(c.tramo_der, c.es_opaca)  # noqa: E731

    idx_comb = weighted(sev_comb)
    idx_vol = weighted(sev_vol)
    idx_der = weighted(sev_der)

    # Reparto del dinero por tramo combinado (solo no opacas).
    pct = {TRAMO_ESTABLE: 0.0, TRAMO_VOLATIL: 0.0, TRAMO_HIPERVOLATIL: 0.0}
    pct_inestable = 0.0
    if total_peso > 0:
        for c, p in pesos:
            tramo = c.tramo_comb if c.tramo_comb in pct else TRAMO_HIPERVOLATIL
            pct[tramo] += p / total_peso
            if tramo != TRAMO_ESTABLE:
                pct_inestable += p / total_peso

    solo_opacas = tiene_opacas and total_peso <= 0
    indice = None
    ind_min = ind_max = None
    vol_min = vol_max = None
    der_min = der_max = None
    es_intervalo = False

    if total_peso > 0:
        if tiene_opacas:
            indice = None
            ind_min, ind_max = idx_comb, 1.0
            vol_min, vol_max = idx_vol, 1.0
            der_min, der_max = idx_der, 1.0
            es_intervalo = True
        else:
            indice = idx_comb
            ind_min = ind_max = idx_comb
            vol_min = vol_max = idx_vol
            der_min = der_max = idx_der
    elif tiene_opacas:
        # Solo opacas: 100% inestable por construccion, exacto, sin tipo.
        indice = ind_min = ind_max = 1.0
        vol_min = vol_max = der_min = der_max = 1.0

    reasons: list[str] = []
    flags: list[str] = []
    if n_no_val_corte > 0:
        reasons.append("facturas_vivas_sin_valor_en_eur")
        flags.append("exposicion_no_valorable")
    if n_opacas > 0:
        flags.append("exposicion_divisa_opaca")
        reasons.append("divisas_opacas_sin_cobertura_bce")
    if tiene_opacas and not solo_opacas:
        flags.append("indice_fx_intervalo")
    if solo_opacas:
        flags.append("solo_divisas_opacas")
    if any(c.currency == "EUR" for c in rows) and len(rows) > 1:
        flags.append("cartera_multidivisa")
    if all(c.currency == "EUR" for c in rows):
        flags.append("solo_eur")
    if any(c.n_rescatada > 0 for c in rows):
        flags.append("valoracion_bce_por_emision")
    if any(c.n_perdida > 0 for c in rows):
        flags.append("perdida_fx_ya_incurrida")
    if total_peso <= 0 and not tiene_opacas:
        reasons.append("sin_valoracion_en_el_corte")

    if n_no_val_corte / n_vivas >= 0.5:
        confidence = "baja"
    elif n_no_val_corte > 0:
        confidence = "media"
    elif (
        (sum(c.eur_corte for c in rows) < MIN_EUR_CONFIANZA_ALTA)
        or n_val_corte < MIN_FACTURAS_CONFIANZA_ALTA
    ):
        confidence = "media"
        reasons.append("cartera_de_cobros_fina")
    else:
        confidence = "alta"

    lags_emision = lags_emision_fmi or []
    lags_corte = lags_corte_fmi or []

    def _lag_stats(values: list[int]) -> tuple[float | None, int | None]:
        if not values:
            return (None, None)
        ordered = sorted(values)
        return (quantile([float(v) for v in ordered], 0.50), max(ordered))

    lag_em_mediano, lag_em_max = _lag_stats(lags_emision)
    lag_ct_mediano, lag_ct_max = _lag_stats(lags_corte)

    return CompanyMonthFxRisk(
        company_id=company_id,
        month=month,
        n_facturas_vivas=n_vivas,
        n_cancel_excluidas=n_cancel,
        importe_vivo_original=sum(c.importe_original for c in rows),
        importe_vivo_eur_corte=(
            sum(c.eur_corte for c in rows) if n_val_corte > 0 else None
        ),
        importe_vivo_eur_emision=(
            sum(c.eur_emision for c in rows)
            if any(c.n_val_emision > 0 for c in rows)
            else None
        ),
        importe_opaco_original=importe_opaco_original,
        importe_opaco_eur_reportado=importe_opaco_eur if tiene_opaco_eur else None,
        perdida_eur=perdida_total if n_perdida > 0 else None,
        n_perdida=n_perdida,
        n_valorables_corte=n_val_corte,
        n_no_valorables_corte=n_no_val_corte,
        n_rescatadas_bce=sum(c.n_rescatada for c in rows),
        n_rescatadas_fmi=sum(c.n_rescatada_fmi for c in rows),
        n_valorables_corte_fmi=len(lags_corte),
        n_valorables_emision_fmi=len(lags_emision),
        lag_fmi_emision_mediano_meses=lag_em_mediano,
        lag_fmi_emision_max_meses=lag_em_max,
        lag_fmi_corte_mediano_meses=lag_ct_mediano,
        lag_fmi_corte_max_meses=lag_ct_max,
        n_opacas=n_opacas,
        pct_estable=pct[TRAMO_ESTABLE] if total_peso > 0 else None,
        pct_volatil=pct[TRAMO_VOLATIL] if total_peso > 0 else None,
        pct_hipervolatil=pct[TRAMO_HIPERVOLATIL] if total_peso > 0 else None,
        pct_dinero_inestable=pct_inestable if total_peso > 0 else None,
        contrib_volatilidad=idx_vol,
        contrib_deriva=idx_der,
        indice_fx=indice,
        indice_fx_vol=idx_vol,
        indice_fx_deriva=idx_der,
        indice_fx_min=ind_min,
        indice_fx_max=ind_max,
        indice_fx_vol_min=vol_min,
        indice_fx_vol_max=vol_max,
        indice_fx_deriva_min=der_min,
        indice_fx_deriva_max=der_max,
        tiene_opacas=tiene_opacas,
        solo_opacas=solo_opacas,
        indice_es_intervalo=es_intervalo,
        confidence=confidence,
        reasons=tuple(dict.fromkeys(reasons)),
        flags=tuple(dict.fromkeys(flags)),
        por_divisa=tuple(c.to_dict() for c in rows),
    )


# --------------------------------------------------------------------- #
# Contrafactual
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class AssessmentFx:
    """Fila de la poblacion evaluable de v4 que consume el contrafactual."""

    company_id: str
    month: date
    health_score: float
    mora_indice: float | None
    multiplicador_deuda: float | None
    penalizacion_mora_puntos: float | None
    penalizacion_multiplicador_puntos: float | None
    h_antes_de_ajustes: float | None


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p50": quantile(ordered, 0.50),
        "p90": quantile(ordered, 0.90),
        "p99": quantile(ordered, 0.99),
        "max": ordered[-1],
        "media": sum(ordered) / len(ordered),
    }


def compute_counterfactual(
    assessments: list[AssessmentFx],
    rows: list[CompanyMonthFxRisk],
    betas: tuple[float, ...] = BETAS_POR_DEFECTO,
    umbral_material: float = UMBRAL_MATERIAL_PUNTOS,
) -> dict:
    """Simula H_fx = H_final * (1 - beta_fx * indice_fx) fuera del motor.

    ``rows`` son las filas empresa-mes de F3. La poblacion son las filas de
    ``assessments`` (health_score no nulo y excluida=false). Para empresas con
    intervalo de indice (mezcla opaca) se publican los limites inferior y
    superior del efecto.
    """
    by_cm: dict[tuple[str, date], CompanyMonthFxRisk] = {
        (r.company_id, r.month): r for r in rows
    }

    # Universo evaluable: empresas con al menos una fila.
    empresas_eval = {a.company_id for a in assessments}

    por_beta: dict[str, dict] = {}
    top_por_beta: dict[str, list] = {}
    for beta in betas:
        dh_todos: list[float] = []
        dh_cambian: list[float] = []
        dh_abs_min: list[float] = []
        dh_abs_max: list[float] = []
        dh_vol: list[float] = []
        dh_der: list[float] = []
        affected_cm = 0
        affected_cm_interval = 0
        material_cm = 0
        affected_companies: set[str] = set()
        material_companies: set[str] = set()
        interval_cm = 0
        max_dh_company: dict[str, float] = defaultdict(float)

        for a in assessments:
            row = by_cm.get((a.company_id, a.month))
            h = a.health_score
            if row is None:
                continue
            if row.indice_fx is not None:
                dh = beta * h * row.indice_fx
                dh_todos.append(dh)
                if dh > 0:
                    dh_cambian.append(dh)
                    affected_cm += 1
                    affected_companies.add(a.company_id)
                if dh >= umbral_material:
                    material_cm += 1
                    material_companies.add(a.company_id)
                max_dh_company[a.company_id] = max(
                    max_dh_company[a.company_id], dh
                )
            elif row.indice_es_intervalo:
                interval_cm += 1
                lo = beta * h * (row.indice_fx_min or 0.0)
                hi = beta * h * (row.indice_fx_max or 0.0)
                dh_abs_min.append(lo)
                dh_abs_max.append(hi)
                if hi > 0:
                    affected_cm_interval += 1
                    affected_companies.add(a.company_id)
                if hi >= umbral_material:
                    material_cm += 1
                    material_companies.add(a.company_id)
                max_dh_company[a.company_id] = max(
                    max_dh_company[a.company_id], hi
                )
            # Desglose por eje (con la cota inferior si hay intervalo).
            if row.indice_es_intervalo:
                dh_vol.append(beta * h * (row.indice_fx_vol_min or 0.0))
                dh_der.append(beta * h * (row.indice_fx_deriva_min or 0.0))
            elif row.indice_fx_vol is not None:
                dh_vol.append(beta * h * row.indice_fx_vol)
                dh_der.append(beta * h * row.indice_fx_deriva)

        top = sorted(max_dh_company.items(), key=lambda kv: -kv[1])[:10]
        top_por_beta[f"{beta:g}"] = [
            {"company_id": c, "max_dh_puntos": round(v, 4)}
            for c, v in top
        ]
        por_beta[f"{beta:g}"] = {
            "beta_fx": beta,
            "empresas_mes_evaluables": len(assessments),
            "empresas_mes_con_cambio": affected_cm,
            "empresas_mes_con_cambio_incluyendo_intervalo": affected_cm
            + affected_cm_interval,
            "empresas_mes_materiales": material_cm,
            "empresas_con_cambio": len(affected_companies),
            "empresas_materiales": len(material_companies),
            "empresas_mes_con_indice_intervalo": interval_cm,
            "dh_abs_todos": _percentiles(dh_todos),
            "dh_abs_cambian": _percentiles(dh_cambian),
            "dh_abs_intervalo_inferior": _percentiles(dh_abs_min),
            "dh_abs_intervalo_superior": _percentiles(dh_abs_max),
            "efecto_total_puntos": sum(dh_cambian),
            "efecto_total_intervalo_superior_puntos": sum(dh_abs_max),
            "efecto_volatilidad_puntos": sum(dh_vol),
            "efecto_deriva_puntos": sum(dh_der),
            "pct_efecto_volatilidad": (
                sum(dh_vol) / (sum(dh_vol) + sum(dh_der))
                if (sum(dh_vol) + sum(dh_der)) > 0
                else None
            ),
        }

    # Correlaciones de Spearman (independientes de beta por ser monotono).
    xs_fx: list[float] = []
    ys_mora: list[float] = []
    xs_idx: list[float] = []
    ys_mult: list[float] = []
    for a in assessments:
        row = by_cm.get((a.company_id, a.month))
        if row is None or row.indice_fx is None:
            continue
        castigo_fx = a.health_score * row.indice_fx
        if a.penalizacion_mora_puntos is not None:
            xs_fx.append(castigo_fx)
            ys_mora.append(a.penalizacion_mora_puntos)
        if a.penalizacion_multiplicador_puntos is not None:
            xs_idx.append(castigo_fx)
            ys_mult.append(a.penalizacion_multiplicador_puntos)

    # Pares crudos indice vs factor (alineados).
    pairs_mora_idx = [
        (row.indice_fx, a.mora_indice)
        for a in assessments
        if (row := by_cm.get((a.company_id, a.month))) is not None
        and row.indice_fx is not None
        and a.mora_indice is not None
    ]
    pairs_mult_raw = [
        (row.indice_fx, a.multiplicador_deuda)
        for a in assessments
        if (row := by_cm.get((a.company_id, a.month))) is not None
        and row.indice_fx is not None
        and a.multiplicador_deuda is not None
    ]

    spearman_block = {
        "castigo_fx_vs_penalizacion_mora_puntos": spearman(xs_fx, ys_mora),
        "castigo_fx_vs_penalizacion_multiplicador_puntos": spearman(xs_idx, ys_mult),
        "indice_fx_vs_mora_indice": spearman(
            [x for x, _ in pairs_mora_idx], [y for _, y in pairs_mora_idx]
        ),
        "indice_fx_vs_multiplicador_deuda": spearman(
            [x for x, _ in pairs_mult_raw], [y for _, y in pairs_mult_raw]
        ),
        "n_pares_castigo_mora": len(xs_fx),
        "n_pares_castigo_multiplicador": len(xs_idx),
        "nota": (
            "Spearman del castigo FX en puntos (H_final * indice_fx) contra las "
            "penalizaciones ya existentes del motor v4. Beta_fx no afecta al "
            "rango, por lo que el coeficiente no depende del escenario."
        ),
    }

    # Empresas intactas: dentro de los evaluables, nunca con exposicion no-EUR.
    meses_por_empresa: dict[str, list[CompanyMonthFxRisk]] = defaultdict(list)
    for r in rows:
        meses_por_empresa[r.company_id].append(r)

    def _positiva(r: CompanyMonthFxRisk) -> bool:
        return (r.indice_fx is not None and r.indice_fx > 0) or (
            r.indice_es_intervalo and (r.indice_fx_max or 0) > 0
        )

    afectadas_ever_eval = {
        cid
        for cid in empresas_eval
        if any(_positiva(r) for r in meses_por_empresa.get(cid, []))
    }
    afectadas_ever_todas = {
        cid for cid, rs in meses_por_empresa.items() if any(_positiva(r) for r in rs)
    }
    intactas = empresas_eval - afectadas_ever_eval

    # Recuento en el ultimo corte cerrado (solo evaluables).
    corte: date | None = None
    no_eur_ult_eval: set[str] = set()
    if rows:
        corte = max(r.month for r in rows)
        for r in rows:
            if r.month == corte and r.company_id in empresas_eval and _positiva(r):
                no_eur_ult_eval.add(r.company_id)

    return {
        "poblacion": {
            "empresas_evaluables_v4": len(empresas_eval),
            "empresas_mes_evaluables": len(assessments),
            "empresas_evaluables_con_indice_positivo_alguna_vez": len(
                afectadas_ever_eval
            ),
            "empresas_grid_con_indice_positivo_alguna_vez": len(
                afectadas_ever_todas
            ),
            "empresas_intactas_sin_exposicion_no_eur": len(intactas),
            "empresas_evaluables_con_exposicion_no_eur_ultimo_corte": len(
                no_eur_ult_eval
            ),
            "corte_ultimo": corte.isoformat() if corte else None,
        },
        "por_beta": por_beta,
        "top_castigo_por_beta": top_por_beta,
        "spearman": spearman_block,
        "materialidad": {
            "umbral_puntos": umbral_material,
            "justificacion": (
                "1 punto sobre la escala 0-100 es el 1% de la nota y esta por "
                "encima de la mediana de las penalizaciones existentes "
                "(medido en la poblacion evaluable: 0,34 puntos de mora; 0,78 "
                "de multiplicador de deuda). Se publica ademas cuantas "
                "empresas-mes cambian con cualquier |dH|>0."
            ),
        },
    }


# --------------------------------------------------------------------- #
# Censo (cifras titulares y verificacion de recuentos)
# --------------------------------------------------------------------- #
def _lag_block(values: list[int]) -> dict:
    """Resumen auditable de una lista de desfases en meses."""
    if not values:
        return {"n": 0, "mediano_meses": None, "p90_meses": None,
                "max_meses": None, "min_meses": None,
                "distribucion_meses": {}}
    ordered = sorted(values)
    dist: dict[str, int] = {}
    for value in ordered:
        dist[str(value)] = dist.get(str(value), 0) + 1
    return {
        "n": len(ordered),
        "mediano_meses": quantile([float(v) for v in ordered], 0.50),
        "p90_meses": quantile([float(v) for v in ordered], 0.90),
        "max_meses": max(ordered),
        "min_meses": min(ordered),
        "distribucion_meses": dist,
    }


def build_census(
    invoices: list[FxInvoiceRecord],
    rates: RateSeries,
    index: FxIndexTable,
    rows: list[CompanyMonthFxRisk],
    evaluables: list[str] | None = None,
    corte: date | None = None,
    extra: ExtraRateSeries | None = None,
) -> dict:
    """Cifras del informe: rescates, perdida ya incurrida y cobertura opaca."""
    if corte is None and rows:
        corte = max(r.month for r in rows)
    cut = month_end(corte) if corte else None

    rescatadas_bce = 0
    rescatadas_bce_eur = 0.0
    rescatadas_fmi = 0
    rescatadas_fmi_eur = 0.0
    sin_eur = 0
    opacas_sin_eur = 0
    perdida_total = 0.0
    perdida_positiva = 0.0
    perdida_negativa = 0.0
    n_perdida = 0
    por_divisa: dict[str, dict] = {}
    total_no_eur_original = 0.0
    n_no_eur = 0
    lags_emision_fmi: list[int] = []
    lags_corte_fmi: list[int] = []

    for rec in invoices:
        if cut is None:
            break
        if rec.status == STATUS_CANCEL:
            continue
        if rec.issuance_date is None or rec.issuance_date > cut:
            continue
        if (
            rec.status == STATUS_PAID
            and rec.payment_date is not None
            and rec.payment_date <= cut
        ):
            continue
        if rec.currency == "EUR":
            continue
        n_no_eur += 1
        if _finite(rec.amount):
            total_no_eur_original += rec.amount
        eur_emision, fuente, rescatada, sel_emision = _valorar_emision_detalle(
            rec, rates, extra, cut
        )
        if sel_emision is not None:
            lags_emision_fmi.append(sel_emision.lag_meses)
        if rec.amount_eur is None or rec.fx_ambiguous:
            sin_eur += 1
            if rescatada:
                if fuente == "fmi_emision":
                    rescatadas_fmi += 1
                    if eur_emision is not None:
                        rescatadas_fmi_eur += eur_emision
                else:
                    rescatadas_bce += 1
                    if eur_emision is not None:
                        rescatadas_bce_eur += eur_emision
            cubierta = rates.has_currency(rec.currency) or (
                extra is not None and extra.has_currency(rec.currency)
            )
            if not cubierta:
                opacas_sin_eur += 1
        eur_corte, sel_corte = _valorar_corte_detalle(rec, rates, cut, extra)
        if sel_corte is not None:
            lags_corte_fmi.append(sel_corte.lag_meses)
        if eur_emision is not None and eur_corte is not None:
            delta = eur_emision - eur_corte
            n_perdida += 1
            perdida_total += delta
            if delta >= 0:
                perdida_positiva += delta
            else:
                perdida_negativa += delta
            key = rec.currency or "unknown"
            slot = por_divisa.setdefault(
                key,
                {"n": 0, "perdida_eur": 0.0, "eur_emision": 0.0, "eur_corte": 0.0},
            )
            slot["n"] += 1
            slot["perdida_eur"] += delta
            slot["eur_emision"] += eur_emision
            slot["eur_corte"] += eur_corte

    por_divisa_out = {
        k: {
            "n": v["n"],
            "perdida_eur": round(v["perdida_eur"], 2),
            "eur_emision": round(v["eur_emision"], 2),
            "eur_corte": round(v["eur_corte"], 2),
            "perdida_pct": (
                round(v["perdida_eur"] / v["eur_emision"], 4)
                if v["eur_emision"]
                else None
            ),
        }
        for k, v in sorted(
            por_divisa.items(), key=lambda kv: -abs(kv[1]["perdida_eur"])
        )
    }

    # Recuento de opacas en la cartera viva del ultimo corte (empresas).
    ult = [r for r in rows if r.month == corte]
    solo_opacas = sum(1 for r in ult if r.solo_opacas)
    mixtas = sum(1 for r in ult if r.tiene_opacas and not r.solo_opacas)
    con_cartera = sum(1 for r in ult if r.n_facturas_vivas > 0)

    cruce = None
    if evaluables is not None:
        ev = set(evaluables)
        con_ult = [r for r in ult if r.company_id in ev and r.n_facturas_vivas > 0]
        no_eur = [
            r
            for r in con_ult
            if (r.indice_fx is not None and r.indice_fx > 0)
            or r.indice_es_intervalo
        ]
        ever: set[str] = set()
        for r in rows:
            if r.company_id not in ev or r.n_facturas_vivas == 0:
                continue
            if (r.indice_fx is not None and r.indice_fx > 0) or (
                r.indice_es_intervalo and (r.indice_fx_max or 0) > 0
            ):
                ever.add(r.company_id)
        cruce = {
            "corte": corte.isoformat() if corte else None,
            "empresas_evaluables": len(ev),
            "evaluables_con_cartera_en_el_corte": len(con_ult),
            "evaluables_con_exposicion_no_eur_en_el_corte": len(no_eur),
            "evaluables_con_exposicion_no_eur_alguna_vez": len(ever),
        }

    return {
        "corte": corte.isoformat() if corte else None,
        "rescate_por_emision": {
            "facturas_no_eur_sin_eur_propio_en_el_corte": sin_eur,
            "facturas_rescatadas_con_tipo_bce_de_emision": rescatadas_bce,
            "eur_rescatados_bce": round(rescatadas_bce_eur, 2),
            "facturas_rescatadas_con_tipo_fmi_de_emision": rescatadas_fmi,
            "eur_rescatados_fmi": round(rescatadas_fmi_eur, 2),
            "facturas_rescatadas_total": rescatadas_bce + rescatadas_fmi,
            "eur_rescatados_total": round(
                rescatadas_bce_eur + rescatadas_fmi_eur, 2
            ),
            "facturas_opacas_sin_eur_propio": opacas_sin_eur,
            "facturas_no_eur_vivas": n_no_eur,
            "importe_original_no_eur": round(total_no_eur_original, 2),
            "desfase_fmi": {
                "emision": _lag_block(lags_emision_fmi),
                "corte": _lag_block(lags_corte_fmi),
            },
        },
        "perdida_ya_incurrida": {
            "corte": corte.isoformat() if corte else None,
            "facturas_con_perdida_calculable": n_perdida,
            "perdida_neta_eur": round(perdida_total, 2),
            "perdida_positiva_eur": round(perdida_positiva, 2),
            "perdida_negativa_eur": round(perdida_negativa, 2),
            "por_divisa": por_divisa_out,
        },
        "opacas_ultimo_corte": {
            "empresas_con_cartera": con_cartera,
            "empresas_solo_opacas": solo_opacas,
            "empresas_mezcla_opaca_y_valorable": mixtas,
            "nota": (
                "solo opacas = todas sus divisas vivas son opacas (sin BCE); "
                "su indice es 1.0 exacto. mezcla = tiene opacas y valorables; "
                "su indice puntual es NULL y se publica [min, max]."
            ),
        },
        "cruce_evaluables_v4": cruce,
        "universo": CRITERIO_UNIVERSO,
        "criterio_confidence": CRITERIO_CONFIDENCE,
        "limitaciones": LIMITACIONES,
    }


# --------------------------------------------------------------------- #
# Desfase point-in-time de la fuente extra (medido, no supuesto)
# --------------------------------------------------------------------- #
def measure_fmi_lags(
    invoices: list[FxInvoiceRecord],
    rates: RateSeries,
    extra: ExtraRateSeries | None,
    months: list[date],
) -> dict:
    """Distribucion de meses de desfase de los tipos FMI realmente aplicados.

    Recorre la misma cartera viva que el motor y, para cada factura valorada
    con un tipo FMI, mide la distancia en meses entre el mes de la fecha
    objetivo y el mes de la observacion usada. El desfase del BCE es 0 por
    construccion (cotizacion diaria); este bloque solo habla del FMI. Se publica
    por separado emision y corte, porque el corte es exactamente el retardo de
    publicacion de la fuente y la emision varia con la antiguedad de la factura.
    """
    if extra is None:
        return {"disponible": False, "motivo": "sin fuente extra cargada"}
    lags_emision: list[int] = []
    lags_corte: list[int] = []
    for month in months:
        cut = month_end(month)
        for rec in invoices:
            if rec.status == STATUS_CANCEL:
                continue
            if rec.issuance_date is None or rec.issuance_date > cut:
                continue
            if (
                rec.status == STATUS_PAID
                and rec.payment_date is not None
                and rec.payment_date <= cut
            ):
                continue
            if rec.currency in (None, "EUR"):
                continue
            eur, _fuente, _rescatada, sel = _valorar_emision_detalle(
                rec, rates, extra, cut
            )
            if sel is not None:
                lags_emision.append(sel.lag_meses)
            _eur_corte, sel_corte = _valorar_corte_detalle(rec, rates, cut, extra)
            if sel_corte is not None:
                lags_corte.append(sel_corte.lag_meses)

    def _block(values: list[int]) -> dict:
        return _lag_block(values)

    return {
        "disponible": True,
        "tipo": EXTRA_TIPO,
        "fuente": FMI_FUENTE,
        "unidad": "meses entre el mes de la fecha objetivo y el mes del tipo FMI usado",
        "emision": _block(lags_emision),
        "corte": _block(lags_corte),
        "nota": (
            "El desfase del corte es el retardo de publicacion de la fuente "
            "(2 meses; 4 en COP), no un defecto: se publica para que sea "
            "auditable. El de emision es 0 cuando la factura es anterior a la "
            "ventana de publicacion y sube hasta ese retardo en las recientes."
        ),
    }


# --------------------------------------------------------------------- #
# Carga de datos
# --------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_ecb_vendor(path: Path = ECB_VENDOR_PATH) -> str:
    actual = sha256_file(path)
    if actual != ECB_VENDOR_SHA256:
        raise ValueError(
            f"sha256 del CSV BCE no coincide: {actual} != {ECB_VENDOR_SHA256}"
        )
    return actual


def load_ecb_quotes(con, csv_path: Path = ECB_VENDOR_PATH) -> list[EcbQuote]:
    """Carga el CSV crudo del BCE a cotizaciones diarias (ignora celdas N/A)."""
    columns = [
        row[0]
        for row in con.execute(
            f"SELECT * FROM read_csv('{csv_path}', header=true, all_varchar=true) "
            "LIMIT 1"
        ).description
    ]
    currency_columns = [
        c for c in columns if c not in ECB_NON_CURRENCY_COLUMNS
    ]
    if not currency_columns:
        raise ValueError(f"el CSV {csv_path} no tiene columnas de divisa")
    quoted = ", ".join(f'"{c}"' for c in currency_columns)
    query = f"""
        SELECT currency, CAST(Date AS DATE) AS day, CAST(rate AS DOUBLE) AS rate
        FROM (
            UNPIVOT (
                SELECT Date, {quoted}
                FROM read_csv('{csv_path}', header=true, all_varchar=true)
            ) ON {", ".join(f"'{c}'" for c in currency_columns)}
            INTO NAME currency VALUE rate
        )
        WHERE rate IS NOT NULL AND rate <> 'N/A' AND CAST(rate AS DOUBLE) > 0
        ORDER BY currency, day
    """
    return [
        EcbQuote(day=d, currency=c, rate=float(r))
        for c, d, r in con.execute(query).fetchall()
    ]


def load_extra_quotes(path: Path) -> list[EcbQuote]:
    """Enchufe para una segunda fuente de tipos (tarea F1c).

    Acepta CSV o Parquet con columnas ``currency``/``cur``, ``day``/``date`` y
    ``rate``. Si el fichero no existe devuelve lista vacia (F1c puede fallar).
    """
    if not path or not Path(path).exists():
        return []
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        reader = (
            f"read_parquet('{path}')"
            if str(path).endswith(".parquet")
            else f"read_csv_auto('{path}')"
        )
        cols = {
            row[0].lower()
            for row in con.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()
        }
        cur = "cur" if "cur" in cols else "currency"
        day = "date" if "date" in cols else "day"
        rows = con.execute(
            f"SELECT {cur}, CAST({day} AS DATE), CAST(rate AS DOUBLE) "
            f"FROM {reader} WHERE rate IS NOT NULL AND CAST(rate AS DOUBLE) > 0"
        ).fetchall()
    finally:
        con.close()
    return [EcbQuote(day=d, currency=c, rate=float(r)) for c, d, r in rows]


def load_extra_rate_series(
    path: Path | None = EXTRA_RATES_PATH,
    tipo: str = EXTRA_TIPO,
) -> ExtraRateSeries | None:
    """Carga la serie mensual extra (FMI, F1c) desde su parquet.

    Solo consume el tipo declarado (`fin_de_periodo`: se valora un stock a una
    fecha de corte, no un flujo medio). Descarta los meses sin dato (`rate_por_eur
    IS NULL`, p.ej. COP 2026-M08): nulo nunca se rellena. Devuelve None si el
    fichero no existe o no tiene las columnas esperadas, para que la capa siga
    funcionando sin la segunda fuente en vez de romper.
    """
    if not path or not Path(path).exists():
        return None
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        columns = {
            row[0]
            for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}')"
            ).fetchall()
        }
        required = {"currency", "month", "tipo", "rate_por_eur", "available_at"}
        if not required.issubset(columns):
            return None
        rows = con.execute(
            f"""
            SELECT currency, CAST(month AS DATE), CAST(rate_por_eur AS DOUBLE),
                   CAST(available_at AS DATE)
            FROM read_parquet('{path}')
            WHERE tipo = ? AND rate_por_eur IS NOT NULL
              AND CAST(rate_por_eur AS DOUBLE) > 0
            """,
            [tipo],
        ).fetchall()
    finally:
        con.close()
    import calendar

    def _month_end(day: date) -> date:
        return date(
            day.year, day.month, calendar.monthrange(day.year, day.month)[1]
        )

    return ExtraRateSeries(
        [
            ExtraRateRow(
                currency=cur,
                month=month,
                rate=float(rate),
                dia_representativo=_month_end(month),
                available_at=avail,
            )
            for cur, month, rate, avail in rows
        ]
    )


def load_fx_index(path: Path, con) -> FxIndexTable:
    rows = con.execute(
        f"""
        SELECT currency, CAST(month AS DATE) AS month, cobertura_bce,
               tramo_volatilidad, tramo_deriva, tramo
        FROM read_parquet('{path}')
        """
    ).fetchall()
    entries: dict[tuple[str, date], FxIndexEntry] = {}
    for cur, month, cob, vol, der, comb in rows:
        entries[(cur, month.replace(day=1))] = FxIndexEntry(
            tramo_volatilidad=vol,
            tramo_deriva=der,
            tramo=comb,
            cobertura_bce=bool(cob),
        )
    return FxIndexTable(entries)


def load_invoices(path: Path, con) -> list[FxInvoiceRecord]:
    rows = con.execute(
        f"""
        SELECT operation_id, company_id, currency_norm, status_norm,
               issuance_date_ok, due_date_ok, payment_date_ok, amount,
               amount_eur, amount_eur_source, coalesce(fx_ambiguous, FALSE)
        FROM read_parquet('{path}')
        WHERE document_type_norm = '{DOC_INVOICE}'
          AND flow_side = '{FLOW_SIDE_IN}'
        """
    ).fetchall()
    return [
        FxInvoiceRecord(
            operation_id=r[0],
            company_id=r[1],
            currency=r[2],
            status=r[3],
            issuance_date=r[4].date() if r[4] is not None else None,
            due_date=r[5].date() if r[5] is not None else None,
            payment_date=r[6].date() if r[6] is not None else None,
            amount=r[7],
            amount_eur=r[8],
            amount_eur_source=r[9] or "unknown",
            fx_ambiguous=bool(r[10]),
        )
        for r in rows
    ]


def load_companies(con) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            f"SELECT company_id FROM read_parquet('{paths.CLEAN_DIR / 'companies.parquet'}') "
            "ORDER BY company_id"
        ).fetchall()
    ]


def load_assessments(path: Path, con) -> list[AssessmentFx]:
    rows = con.execute(
        f"""
        SELECT company_id, CAST(month AS DATE), health_score, mora_indice,
               multiplicador_deuda, penalizacion_mora_puntos,
               penalizacion_multiplicador_puntos, h_antes_de_ajustes
        FROM read_parquet('{path}')
        WHERE health_score IS NOT NULL AND excluida = FALSE
        """
    ).fetchall()
    return [
        AssessmentFx(
            company_id=r[0],
            month=r[1].replace(day=1),
            health_score=r[2],
            mora_indice=r[3],
            multiplicador_deuda=r[4],
            penalizacion_mora_puntos=r[5],
            penalizacion_multiplicador_puntos=r[6],
            h_antes_de_ajustes=r[7],
        )
        for r in rows
    ]


# --------------------------------------------------------------------- #
# Informe
# --------------------------------------------------------------------- #
def _fmt(value, places: int = 2) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.{places}f}"
    return str(value)


def _pct(value) -> str:
    return "NULL" if value is None else f"{100 * value:.1f}%"


def write_report(census: dict, cf: dict, path: Path) -> None:
    t = census
    r = t["rescate_por_emision"]
    p = t["perdida_ya_incurrida"]
    op = t["opacas_ultimo_corte"]
    pop = cf["poblacion"]
    esp = cf["spearman"]
    just = cf["materialidad"]["justificacion"]
    lines: list[str] = [
        "# F3 · Riesgo de divisa: valoracion por fecha de emision, indice FX y "
        "contrafactual medido",
        "",
        "Capa F3 del `docs/SPEC_FX_RISK_INDEX.md`. Une los tipos del BCE (F1), "
        "el indice por divisa-mes (F1b) y la exposicion empresa-mes (F2), y "
        "**mide** que le haria a la nota aplicar un factor de riesgo de divisa. "
        "NO se toca la formula de `xray/scoring_v4.py`: el efecto se simula "
        "fuera del motor (camino B, capa + contrafactual medido).",
        "",
        "## Resumen ejecutivo",
        "",
        f"- Facturas no-EUR vivas en el corte {t['corte']}: "
        f"{_fmt(r['facturas_no_eur_vivas'], 0)}.",
        f"- Facturas rescatadas valorandolas con el tipo BCE del **dia de "
        f"emision**: **{_fmt(r['facturas_rescatadas_con_tipo_bce_de_emision'], 0)}** "
        f"(de {_fmt(r['facturas_no_eur_sin_eur_propio_en_el_corte'], 0)} sin EUR "
        f"propio), por {_fmt(r['eur_rescatados_bce'])} EUR. La segunda fuente "
        f"(FMI/IFS `XDC_EUR`, F1c ya enchufada) rescata ademas "
        f"**{_fmt(r['facturas_rescatadas_con_tipo_fmi_de_emision'], 0)}** "
        f"facturas por {_fmt(r['eur_rescatados_fmi'])} EUR, y cierra la "
        "valoracion de ARS, COP, CLP y PEN.",
        f"- Perdida ya incurrida por movimiento de divisa (emision frente a "
        f"corte): **{_fmt(p['perdida_neta_eur'])} EUR NETOS**, con "
        f"{_fmt(p['perdida_positiva_eur'])} EUR de perdida bruta y "
        f"{_fmt(abs(p['perdida_negativa_eur']))} EUR de ganancia bruta, sobre "
        f"{_fmt(p['facturas_con_perdida_calculable'], 0)} facturas.",
        (
            "- Evaluables de v4 con exposicion no-EUR **medida** en el ultimo "
            f"corte: {(t['cruce_evaluables_v4'] or {}).get('evaluables_con_exposicion_no_eur_en_el_corte')} "
            f"de {(t['cruce_evaluables_v4'] or {}).get('evaluables_con_cartera_en_el_corte')} "
            "con cartera viva; con exposicion no-EUR **alguna vez**: "
            f"{(t['cruce_evaluables_v4'] or {}).get('evaluables_con_exposicion_no_eur_alguna_vez')}. "
            "El rescate por tipo de emision (BCE + FMI) amplia la poblacion "
            "afectada, no la inventa."
        ),
        f"- Poblacion evaluable v4: {_fmt(pop['empresas_evaluables_v4'], 0)} "
        "empresas. Con exposicion no-EUR **alguna vez**: "
        f"{_fmt(pop['empresas_evaluables_con_indice_positivo_alguna_vez'], 0)} "
        "(en toda la rejilla de empresas: "
        f"{_fmt(pop['empresas_grid_con_indice_positivo_alguna_vez'], 0)}); en el "
        "ultimo corte: "
        f"{_fmt(pop['empresas_evaluables_con_exposicion_no_eur_ultimo_corte'], 0)}. "
        f"**Intactas: {_fmt(pop['empresas_intactas_sin_exposicion_no_eur'], 0)} "
        f"de {_fmt(pop['empresas_evaluables_v4'], 0)}** (sin exposicion no-EUR en "
        "ningun corte).",
        f"- Spearman del castigo FX contra la penalizacion por mora: "
        f"**{_fmt(esp['castigo_fx_vs_penalizacion_mora_puntos'], 3)}**; contra "
        f"la penalizacion por multiplicador de deuda: "
        f"**{_fmt(esp['castigo_fx_vs_penalizacion_multiplicador_puntos'], 3)}**.",
        "",
        "## 1. Valoracion por fecha de emision (tipos del BCE)",
        "",
        "Instruccion del usuario: *\"busca segun la fecha de factura cual era "
        "el tipo de cambio aquel dia\"*. Cada factura viva en divisa cubierta "
        "por el BCE se valora en EUR con el tipo del dia de emision "
        "(`issuance_date_ok`). Si ese dia no cotiza (fin de semana, festivo) se "
        "usa el **ultimo dia habil ANTERIOR** con cotizacion. Nunca se "
        "interpola hacia delante ni se usa un tipo posterior a la fecha.",
        "",
        "Prioridad de valoracion (declarada):",
        "",
        "1. `amount_eur` nativo cuando `amount_eur_source='reported'` y la "
        "conversion no es ambigua (el tipo que declara la factura manda).",
        "2. Tipo BCE del dia de emision (ultimo habil anterior si no cotiza).",
        "3. Tipo FMI/IFS `XDC_EUR` (`fin_de_periodo`) **point-in-time**: la "
        "observacion con `available_at <= cierre del corte` mas cercana a la "
        "fecha objetivo. Solo entra cuando el BCE no cubre la divisa (ARS, COP, "
        "CLP, PEN); nunca una fila publicada despues del corte.",
        "4. Sin valorar (nunca se imputa como cero).",
        "",
        f"| Metrica | Valor |",
        f"| --- | ---: |",
        f"| Facturas no-EUR vivas en el corte | "
        f"{_fmt(r['facturas_no_eur_vivas'], 0)} |",
        f"| Sin EUR propio (a rescatar) | "
        f"{_fmt(r['facturas_no_eur_sin_eur_propio_en_el_corte'], 0)} |",
        f"| **Rescatadas con tipo BCE de emision** | "
        f"**{_fmt(r['facturas_rescatadas_con_tipo_bce_de_emision'], 0)}** |",
        f"| **Rescatadas con tipo FMI de emision** | "
        f"**{_fmt(r['facturas_rescatadas_con_tipo_fmi_de_emision'], 0)}** |",
        f"| EUR rescatados con BCE | {_fmt(r['eur_rescatados_bce'])} |",
        f"| EUR rescatados con FMI | {_fmt(r['eur_rescatados_fmi'])} |",
        f"| Sin EUR propio y divisa opaca (no rescatables) | "
        f"{_fmt(r['facturas_opacas_sin_eur_propio'], 0)} |",
        "",
        "### Desfase point-in-time de los tipos FMI aplicados",
        "",
        "El IFS es mensual y publica con retardo; el desfase (meses entre la "
        "fecha objetivo y el mes del tipo usado) se mide y se publica, no se "
        "esconde. Es correcto usar un tipo de hace dos meses; lo prohibido es "
        "usar uno con `available_at` posterior al corte.",
        "",
        "| Objetivo | Facturas | Desfase mediano | Desfase p90 | Desfase max |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    desfase = r.get("desfase_fmi", {})
    for etiqueta, clave in (("emision", "emision"), ("corte", "corte")):
        bloque = desfase.get(clave, {})
        lines.append(
            f"| {etiqueta} | {_fmt(bloque.get('n'), 0)} | "
            f"{_fmt(bloque.get('mediano_meses'), 2)} | "
            f"{_fmt(bloque.get('p90_meses'), 2)} | "
            f"{_fmt(bloque.get('max_meses'), 0)} |"
        )
    lines += [
        "",
        "## 2. Valoracion a fecha de corte y perdida ya incurrida",
        "",
        "La misma factura se valora con el tipo al **cierre del mes de "
        "corte**: el del BCE si lo hay y, si no, el FMI point-in-time. La "
        "diferencia `perdida_eur = eur_emision - eur_corte` es dinero "
        "ya perdido (o ganado) por movimiento de divisa, no riesgo teorico. "
        "Ambas fechas son <= corte, por lo que ambas son legitimas "
        "point-in-time.",
        "",
        f"Perdida neta en el corte {p['corte']}: **{_fmt(p['perdida_neta_eur'])} "
        "EUR**. Por divisa (mayor efecto absoluto; positivo = perdida, negativo "
        "= ganancia):",
        "",
        "| Divisa | Facturas | EUR emision | EUR corte | Perdida EUR | % |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cur, v in list(p["por_divisa"].items())[:15]:
        lines.append(
            f"| {cur} | {_fmt(v['n'], 0)} | {_fmt(v['eur_emision'])} | "
            f"{_fmt(v['eur_corte'])} | {_fmt(v['perdida_eur'])} | "
            f"{_pct(v['perdida_pct'])} |"
        )
    lines += [
        "",
        "## 3. Indice FX empresa-mes ponderado por dinero",
        "",
        "Ponderacion por **dinero** (valor EUR de la cartera viva), no por "
        "numero de facturas. Escala declarada de severidad por tramo combinado "
        "de F1b: `estable=0.0`, `volatil=0.5`, `hipervolatil=1.0`. El indice "
        "vive en [0, 1]. Se publican por separado la contribucion por "
        "**volatilidad** (`indice_fx_vol`) y por **deriva** "
        "(`indice_fx_deriva`), y el combinado (`indice_fx`).",
        "",
        "Columnas del parquet `reports/fx_risk/fx_risk_monthly.parquet`: "
        "`indice_fx`, `indice_fx_vol`, `indice_fx_deriva`, sus intervalos "
        "`[min, max]` para empresas con mezcla de divisas opacas, "
        "`pct_dinero_inestable`, `perdida_eur`, `importe_vivo_eur_corte`, "
        "`confidence` y `reasons`.",
        "",
        "## 4. Divisas opacas residuales (sin cobertura BCE ni FMI)",
        "",
        "Una divisa sin tipo BCE **ni FMI** no se puede pasar a EUR y **no se "
        "imputa**. Desde F6, ARS, COP, CLP y PEN ya NO estan aqui: las cubre la "
        "segunda fuente (FMI/IFS `XDC_EUR`, F1c) con regla point-in-time. El "
        "agujero residual es AED, MAD, MZN y NAD. Convencion (`nulo nunca es "
        "cero`, igual que `payment_delay_v2`):",
        "",
        "- Empresa con **solo** divisas opacas: proporcion de dinero inestable "
        "= **100% por construccion**, exacta, sin necesidad de tipo. Valor "
        "puntual 1.0.",
        "- Empresa con **mezcla** de opacas y valorables: no se puede cerrar la "
        "proporcion. `indice_fx` = NULL y se publica "
        "`[indice_fx_min, indice_fx_max]` = [caso opaco despreciable, caso "
        "opaco dominante].",
        "",
        f"Recuento en el corte {t['corte']} sobre las "
        f"{_fmt(op['empresas_con_cartera'], 0)} empresas con cartera viva: "
        f"**{op['empresas_solo_opacas']} con solo opacas** y "
        f"**{op['empresas_mezcla_opaca_y_valorable']} con mezcla** "
        "(AED, MAD, MZN y NAD).",
        "",
        "La segunda fuente se engancha DENTRO de `xray/fx_risk.py` "
        "(`--extra-rates`, por defecto `reports/fx_risk/rates_extra.parquet`; "
        "`--sin-extra` reproduce el estado anterior). Con tipos para "
        "ARS/COP/CLP/PEN, las empresas de mezcla que solo tenian esas divisas "
        "cierran su indice puntual y dejan de caer en la rama de intervalo.",
        "",
        "## 5. Contrafactual: que le haria a la nota",
        "",
        "Simulacion fuera del motor: `H_fx = H_final * (1 - beta_fx * "
        "indice_fx)`. Poblacion: filas con `health_score` no nulo y "
        "`excluida=false` de `reports/score_v4/assessments.parquet` "
        f"({_fmt(pop['empresas_evaluables_v4'], 0)} empresas, "
        f"{_fmt(pop['empresas_mes_evaluables'], 0)} empresa-mes).",
        "",
        "| beta_fx | empresa-mes con cambio | empresa-mes materiales | "
        "empresas con cambio | empresas materiales | p50 \\|dH\\| | p90 \\|dH\\| |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for beta, block in cf["por_beta"].items():
        d = block["dh_abs_cambian"]
        lines.append(
            f"| {beta} | {_fmt(block['empresas_mes_con_cambio'], 0)} | "
            f"{_fmt(block['empresas_mes_materiales'], 0)} | "
            f"{_fmt(block['empresas_con_cambio'], 0)} | "
            f"{_fmt(block['empresas_materiales'], 0)} | "
            f"{_fmt(d.get('p50'), 3)} | {_fmt(d.get('p90'), 3)} |"
        )
    lines += [
        "",
        f"Materialidad: umbral declarado de "
        f"{cf['materialidad']['umbral_puntos']} puntos. {just}",
        "",
        "### Ortogonalidad: correlacion de Spearman",
        "",
        "Esta es la cifra que decide si el factor es senal nueva o un cuarto "
        "castigo sobre la misma empresa:",
        "",
        f"- Castigo FX (puntos) vs penalizacion por **mora**: "
        f"**{_fmt(esp['castigo_fx_vs_penalizacion_mora_puntos'], 3)}**.",
        f"- Castigo FX (puntos) vs penalizacion por **multiplicador de deuda**: "
        f"**{_fmt(esp['castigo_fx_vs_penalizacion_multiplicador_puntos'], 3)}**.",
        f"- `indice_fx` vs `mora_indice`: "
        f"{_fmt(esp['indice_fx_vs_mora_indice'], 3)}.",
        f"- `indice_fx` vs `multiplicador_deuda`: "
        f"{_fmt(esp['indice_fx_vs_multiplicador_deuda'], 3)}.",
        "",
        f"Pares usados: {_fmt(esp['n_pares_castigo_mora'], 0)} (mora), "
        f"{_fmt(esp['n_pares_castigo_multiplicador'], 0)} (multiplicador).",
        "",
        "### Desglose volatilidad vs deriva",
        "",
        "| beta_fx | efecto total puntos | efecto volatilidad | efecto deriva | "
        "% volatilidad |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for beta, block in cf["por_beta"].items():
        lines.append(
            f"| {beta} | {_fmt(block['efecto_total_puntos'], 2)} | "
            f"{_fmt(block['efecto_volatilidad_puntos'], 2)} | "
            f"{_fmt(block['efecto_deriva_puntos'], 2)} | "
            f"{_pct(block['pct_efecto_volatilidad'])} |"
        )
    lines += [
        "",
        "Las cifras de la tabla son |dH| en puntos sobre las empresa-mes que "
        "**cambian** (excluye las de indice 0, que son mayoria): p50, p90 y el "
        "maximo. El efecto total es la suma de puntos de nota perdidos en toda "
        "la poblacion.",
        "",
        "### Empresas mas castigadas (max |dH| en el escenario mayor)",
        "",
        "| Empresa | max |dH| puntos |",
        "| --- | ---: |",
    ]
    last_key = list(cf["por_beta"])[-1]
    for row in cf["top_castigo_por_beta"].get(last_key, []):
        lines.append(
            f"| {row['company_id']} | {_fmt(row['max_dh_puntos'], 3)} |"
        )
    lines += [
        "",
        "## 6. Recomendacion honesta",
        "",
    ]
    rho_mora = esp["castigo_fx_vs_penalizacion_mora_puntos"]
    rho_mult = esp["castigo_fx_vs_penalizacion_multiplicador_puntos"]
    rhos = [abs(x) for x in (rho_mora, rho_mult) if x is not None]
    max_rho = max(rhos) if rhos else None
    last = cf["por_beta"][last_key]
    afectadas_eval = pop["empresas_evaluables_con_indice_positivo_alguna_vez"]
    intactas_n = pop["empresas_intactas_sin_exposicion_no_eur"]
    ultimo_corte = pop["empresas_evaluables_con_exposicion_no_eur_ultimo_corte"]
    if max_rho is None:
        veredicto = "no se pudo medir la ortogonalidad (faltan pares)."
    elif max_rho < 0.20:
        veredicto = (
            "las correlaciones son BAJAS (<0.20): el castigo FX no repite el "
            "de mora ni el de deuda, es senal nueva."
        )
    elif max_rho < 0.40:
        veredicto = (
            "las correlaciones son MODERADAS (0.20-0.40): hay solapamiento "
            "parcial; anade informacion pero golpea en parte a las mismas."
        )
    else:
        veredicto = (
            "las correlaciones son ALTAS (>=0.40): el castigo FX repite el de "
            "mora/deuda y no es senal nueva."
        )
    lines += [
        f"Con `beta_fx={last_key}`, el factor mueve a "
        f"{_fmt(last['empresas_con_cambio'], 0)} empresas "
        f"({_fmt(last['empresas_materiales'], 0)} de forma material) de las "
        f"957; {_fmt(intactas_n, 0)} quedan intactas. Solo "
        f"{_fmt(afectadas_eval, 0)} empresas evaluables tienen exposicion "
        f"no-EUR en algun corte ({_fmt(ultimo_corte, 0)} en el ultimo). En "
        f"terminos de ortogonalidad, {veredicto}",
        "",
        "Lectura de producto:",
        "",
        "- La ortogonalidad es el mejor argumento a favor del factor: las "
        "correlaciones medidas son muy bajas, de modo que NO es un cuarto "
        "castigo sobre la misma empresa que ya castigan mora y deuda.",
        "- En contra: el factor solo alcanza a una minoria de la poblacion "
        f"({_fmt(afectadas_eval, 0)} de {_fmt(pop['empresas_evaluables_v4'], 0)}) y la perdida ya incurrida NETA es "
        f"practicamente nula ({_fmt(p['perdida_neta_eur'])} EUR) porque las "
        "ganancias por divisa compensan las perdidas. La volatilidad pesa algo "
        f"mas que la deriva ({_pct(last['pct_efecto_volatilidad'])} del efecto "
        "bruto), y la deriva pasada no predice la futura.",
        "- Recomendacion: SI merece explorarse como capa, pero como factor "
        "ESPECIFICO de inestabilidad con beta PEQUENO (0.05-0.10) y acotado, "
        "no como penalizacion universal. El indice combinado (mas severo de "
        "volatilidad y deriva) es defendible; la deriva por si sola, no. El "
        "agujero de ARS/COP/CLP/PEN ya esta cerrado (F1c enchufado en F6): "
        "esas divisas, las mas inestables de la cartera, ya no caen en la rama "
        "de intervalo y su perdida ya incurrida es visible. El agujero residual "
        "es AED/MAD/MZN/NAD, fuera de la cobertura BCE y FMI.",
        "",
        "## 7. Limitaciones declaradas",
        "",
        f"- {LIMITACIONES}",
        "- La valoracion usa `amount` (nominal emitido) y no `pending_amount` "
        "(saldo terminal del snapshot): usarlo pondria a cero lo ya cobrado en "
        "cortes pasados (fuga). Es cota superior si hubo cobro parcial.",
        "- En facturas con conversion propia (`reported`), la perdida mezcla el "
        "tipo declarado por la factura con el tipo BCE del corte; se declara.",
        "- Calidad de fuente: 3 facturas HUF declaran una conversion propia con "
        "un `exchange_rate` ~100x menor que el del BCE, lo que infla su perdida "
        f"declarada (5.9k EUR de los {_fmt(p['perdida_neta_eur'])} EUR netos "
        "del corte). Se respeta la prioridad declarada (manda la factura) pero "
        "se senala el dato anomalo.",
        "",
        "## Artefactos",
        "",
        "- `reports/fx_risk/fx_risk_monthly.parquet` — empresa-mes con "
        "valoraciones, perdida, indice, intervalos y desfase FMI en meses.",
        "- `reports/fx_risk/contrafactual.json` — todos los escenarios, "
        "distribuciones, Spearman y desglose.",
        "- `reports/fx_risk/INFORME.md` — este informe.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def _resolve_input(explicit: Path | None, local: Path, sibling: Path) -> Path:
    if explicit is not None:
        return explicit
    if local.exists():
        return local
    return sibling


def _assert_no_nan(rows: list[CompanyMonthFxRisk]) -> None:
    for r in rows:
        for key, value in r.to_row().items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"NaN/inf en {key} de {r.company_id}/{r.month}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xray.fx_risk",
        description="Riesgo FX: valoracion por emision, indice empresa-mes y "
        "contrafactual (F3)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    parser.add_argument("--index", type=Path, default=None,
                        help="parquet del indice divisa-mes de F1b")
    parser.add_argument("--exposure", type=Path, default=None,
                        help="parquet de exposicion empresa-mes de F2 (cruce)")
    parser.add_argument("--assessments", type=Path, default=ASSESSMENTS_PATH)
    parser.add_argument("--invoices", type=Path, default=INVOICES_PATH)
    parser.add_argument("--ecb-csv", type=Path, default=ECB_VENDOR_PATH)
    parser.add_argument("--extra-rates", type=Path, default=None,
                        help="parquet de la serie mensual extra (F1c) con "
                             "columnas currency, month, tipo, rate_por_eur, "
                             "available_at; por defecto "
                             "reports/fx_risk/rates_extra.parquet")
    parser.add_argument("--sin-extra", dest="con_extra", action="store_false",
                        default=True,
                        help="NO engancha la segunda fuente de tipos (FMI) y "
                             "reproduce el comportamiento anterior")
    parser.add_argument("--betas", type=str, default=",".join(
        f"{b:g}" for b in BETAS_POR_DEFECTO))
    parser.add_argument("--material-threshold", type=float,
                        default=UMBRAL_MATERIAL_PUNTOS)
    parser.add_argument("--force", action="store_true",
                        help="sobrescribe artefactos existentes")
    args = parser.parse_args(argv)

    out = args.output_dir
    targets = (
        out / "fx_risk_monthly.parquet",
        out / "contrafactual.json",
        out / "INFORME.md",
    )
    if all(t.exists() for t in targets) and not args.force:
        print(
            f"ABORTADO: los ficheros de F3 ya existen en {out}. Usa --force "
            "para regenerarlos.",
            file=sys.stderr,
        )
        return 2

    index_path = _resolve_input(args.index, LOCAL_INDEX_PATH, F1B_INDEX_PATH)
    exposure_path = _resolve_input(
        args.exposure, LOCAL_EXPOSURE_PATH, F2_EXPOSURE_PATH
    )
    if not index_path.exists():
        print(f"ERROR: no existe el indice de F1b: {index_path}", file=sys.stderr)
        return 2
    if not args.invoices.exists():
        print(f"ERROR: no existe el parquet de facturas: {args.invoices}",
              file=sys.stderr)
        return 2

    verify_ecb_vendor(args.ecb_csv)

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        quotes = load_ecb_quotes(con, args.ecb_csv)
        rates = RateSeries(quotes)
        index = load_fx_index(index_path, con)
        invoices = load_invoices(args.invoices, con)
        companies = load_companies(con)
        assessments = load_assessments(args.assessments, con)
        evaluables = sorted({a.company_id for a in assessments})
        if exposure_path.exists():
            exposure_ok = con.execute(
                f"SELECT count(*) FROM read_parquet('{exposure_path}')"
            ).fetchone()[0]
        else:
            exposure_ok = None
    finally:
        con.close()

    # Segunda fuente (F1c): se carga FUERA de la conexion duckdb (abre la suya).
    extra_path: Path | None = None
    if args.con_extra:
        extra_path = args.extra_rates
        if extra_path is None:
            env_path = os.environ.get("XRAY_FX_EXTRA_RATES")
            extra_path = Path(env_path) if env_path else EXTRA_RATES_PATH
    extra_rates = load_extra_rate_series(extra_path) if extra_path else None
    if extra_path is not None and extra_rates is None:
        print(
            f"AVISO: no se pudo cargar la segunda fuente de tipos en "
            f"{extra_path}; se sigue SIN ella (ningun tipo se inventa).",
            file=sys.stderr,
        )

    months = closed_months()
    rows = compute_fx_risk(
        invoices, months, rates, index, companies, extra_rates=extra_rates
    )
    _assert_no_nan(rows)

    betas = tuple(float(x) for x in args.betas.split(",") if x.strip())
    census = build_census(
        invoices, rates, index, rows, evaluables, extra=extra_rates
    )
    census["desfase_fmi_panel"] = measure_fmi_lags(
        invoices, rates, extra_rates, months
    )
    census["fuente_extra"] = {
        "disponible": extra_rates is not None,
        "ruta": str(extra_path) if extra_path else None,
        "tipo": EXTRA_TIPO,
        "fuente": FMI_FUENTE,
        "divisas": sorted(extra_rates.currencies) if extra_rates else [],
        "regla": (
            "observacion con available_at <= cierre del corte mas cercana a la "
            "fecha objetivo; nunca una publicada despues del corte"
        ),
    }
    cf = compute_counterfactual(
        assessments, rows, betas, args.material_threshold
    )
    cf["f1b_index"] = str(index_path)
    cf["f2_exposure"] = str(exposure_path) if exposure_path.exists() else None
    cf["f2_exposure_filas"] = exposure_ok
    cf["extra_rates"] = str(extra_path) if extra_path else None
    cf["extra_rates_cargados"] = extra_rates is not None
    cf["extra_rates_divisas"] = (
        sorted(extra_rates.currencies) if extra_rates else []
    )

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in rows])
    frame["month"] = pd.to_datetime(frame["month"])
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "fx_risk_monthly.parquet", index=False)
    (out / "contrafactual.json").write_text(
        json.dumps({"censo": census, "contrafactual": cf}, indent=2,
                   ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    write_report(census, cf, out / "INFORME.md")

    print(json.dumps({
        "output": str(out),
        "filas": len(rows),
        "rescatadas_bce": census["rescate_por_emision"][
            "facturas_rescatadas_con_tipo_bce_de_emision"],
        "rescatadas_fmi": census["rescate_por_emision"][
            "facturas_rescatadas_con_tipo_fmi_de_emision"],
        "perdida_eur": census["perdida_ya_incurrida"]["perdida_neta_eur"],
        "opacas": census["opacas_ultimo_corte"],
        "fuente_extra": census["fuente_extra"],
        "desfase_fmi": census["rescate_por_emision"]["desfase_fmi"],
        "spearman": cf["spearman"],
        "intactas": cf["poblacion"][
            "empresas_intactas_sin_exposicion_no_eur"],
    }, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
