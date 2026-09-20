"""payment_delay_v2 — indice de morosidad robusto (capa C de healthscore_v4).

QUE ES ESTA CAPA
----------------
Modulo NUEVO que convive con xray/payment_delay.py (v3) SIN modificarlo. v3
mide ``mora_ratio = vencido / exigible``: binario, saturado (mediana 0,17;
p75 0,75; p90 1,0) y solo calculable en 12.841 de 30.864 filas empresa-mes
(691 empresas). v2 mide el mismo fenomeno point-in-time con cuatro mecanismos
de robustez y publica, ademas de sus propios indices, la comparativa contra v3
en coverage.json. Esta capa solo MIDE: no implementa el scorer v4.

MECANISMO 1 — SEVERIDAD PONDERADA POR DIAS (no ratio binario)
-------------------------------------------------------------
La cartera vencida del corte se reparte por antiguedad del vencimiento
(``month_end - due_date_ok``) en los MISMOS cinco cortes de la capa A
(xray/debt_obligation.py): 1-30, 31-60, 61-90, 91-180 y 180+. Cada bucket
pesa mas que el anterior con las constantes ``BUCKET_PESOS``: 0,10 / 0,25 /
0,45 / 0,70 / 1,00. Justificacion: la probabilidad de recuperacion cae de
forma concava con la edad de la deuda; a 30 dias el impago suele ser un
desfase de tesoreria, a partir de 180 dias se aproxima a perdida. El tope
1,00 mantiene la severidad en [0, 1] y comparable con mora_ratio. Una factura
vencida con 0 dias (vence el mismo fin de mes, regla ``due <= fin de m``)
entra en el primer bucket, documentado para no perderla.

    severidad(m) = sum_bucket(peso_bucket * vencido_eur_bucket)
                   / exposicion_total_observable_eur

La exposicion es todo el exigible observable del lado en el corte (pagado a
tiempo + pagado tarde + vencido), de modo que severidad <= mora_ratio: los
pesos por si solos ya reducen la saturacion de la cola alta.

MECANISMO 2 — PERSISTENCIA TEMPORAL EWMA (solo el pasado de la empresa)
----------------------------------------------------------------------
    mora_robusta(m) = lambda * severidad_shrunk(m)
                      + (1 - lambda) * mora_robusta(m-1)

``LAMBDA_EWMA = 0,35`` es PROVISIONAL. Justificacion: el mes corriente pesa
~1/3 y hacen falta ~2-3 meses de mora sostenida para acercarse a la mitad del
nivel; un mes malo aislado no dispara el indice. En el primer corte
observable ``mora_robusta = severidad_shrunk``. Nunca se mira un mes posterior
a m (el bucle es ascendente y solo consume estado ya calculado). Los meses sin
cartera observable no publican indice (NULL, nunca 0) y no rompen la
persistencia: el siguiente corte observable encadena con el ultimo valor.

MECANISMO 3 — DOS LADOS SEPARADOS + INDICE COMBINADO DECLARADO
--------------------------------------------------------------
Nunca se funden pago y cobro en una sola cifra oculta:
- ``mora_pago_robusta``: lado AP (``flow_side='outflow'``), conducta de pago
  de la propia empresa.
- ``mora_cobro_robusta``: lado AR (``flow_side='inflow'``), tension de
  liquidez por impagos de terceros, NO mala conducta de la empresa.
Ambas se publican por separado. Ademas se publica ``mora_indice`` con pesos
declarados ``PESO_MORA_PAGO = 0,70`` y ``PESO_MORA_COBRO = 0,30``, PROVISIONAL
pendiente de calibracion. Justificacion: la conducta de pago es propia,
accionable y directamente penalizable; el retraso de cobro es riesgo externo
y mas ruidoso, por eso pesa menos. Si solo un lado es observable, el indice se
renormaliza a ese lado y se registra el flag ``mora_indice_solo_pago`` o
``mora_indice_solo_cobro``; nunca se inventa el lado ausente.

MECANISMO 4 — SHRINKAGE SOBRE CARTERA FINA (euros virtuales)
------------------------------------------------------------
    severidad_shrunk(m) = (exposicion(m) * severidad(m)
                           + kappa * severidad_hist(m))
                          / (exposicion(m) + kappa)

``KAPPA_EUR = 4178.45`` es PROVISIONAL. Justificacion: reutiliza el mismo
mecanismo de euros virtuales del score y el valor de k recomendado por la
calibracion medida en reports/calibration_k/report.md (4178,45 EUR), para que
ambas capas compartan escala. ``severidad_hist`` es la severidad acumulada de
la PROPIA empresa en sus cortes observables ANTERIORES, ponderada por su
exposicion. Si no hay historia se OMITE el termino kappa (no se inventa 0,5),
``severidad_shrunk = severidad`` y se registra el flag
``shrinkage_<lado>_sin_historia_previa``. Una cartera fina no publica 0 ni 1
en bruto: se mezcla con la historia propia.

DISCIPLINA POINT-IN-TIME (identica a v3, no se relaja)
------------------------------------------------------
- Fuente: ``clean/invoices.parquet`` con ``document_type_norm='invoice'``.
- Una factura cuenta como VENCIDA en el corte m si ``due_date_ok <= fin de m``
  y no estaba pagada al corte (``payment_date_ok`` nulo o posterior a fin de
  m). Una factura pagada DESPUES del corte cuenta como vencida EN ESE CORTE.
- ``payment_date_ok`` solo es fecha real con ``status_norm='paid'``; en otro
  status es fecha prevista y se trata como NULL.
- PROHIBIDO consumir columnas de stock reconstruido de
  marts/panel_cobro.parquet (ar_pct_vencido, ar_retraso_medio_dias y
  similares): son stock reconstruido hacia atras, es fuga. Solo se admiten
  las cohortes ``coh_pct_cobrado_Nd`` cuyo ``coh_Nd_available_at <= fin del
  corte`` (misma regla que v3).
- ``amount_eur`` nulo/ no finito o ``fx_ambiguous``: la factura NO se imputa
  como cero; cuenta en un contador aparte y degrada la confianza de la fila.
  La severidad se calcula solo sobre la exposicion conocida.

CONFIDENCE (criterio declarado, reproducido en coverage.json)
------------------------------------------------------------
Por lado: ``ninguna`` si no hay facturas exigibles en el corte; ``baja`` si
los agujeros son >= 50% de la cartera; ``media`` si hay algun agujero o si la
cartera conocida es fina (< MIN_FACTURAS_CONFIANZA_ALTA facturas o
< MIN_EXPOSICION_CONFIANZA_ALTA EUR); ``alta`` si no hay agujeros y la cartera
es suficientemente gruesa. La ``confidence`` de la fila es la peor de los
lados observables (la mas conservadora).

El motor (``compute_payment_delay_v2``) es PURO: sin IO, sin reloj, sin estado
mutable y no muta su entrada. El CLI escribe bajo ``reports/payment_delay_v2``
y ABORTA si la ruta de salida ya existe.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from xray import paths
from xray.payment_delay import CohortRecord, InvoiceRecord
from xray.period import FIRST_MONTH, LAST_MONTH

__all__ = [
    "CohortRecord",
    "CompanyMonthResult",
    "InvoiceRecord",
    "SideV2Stats",
    "closed_months",
    "compute_payment_delay_v2",
    "main",
]

DOC_INVOICE = "invoice"
DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "payment_delay_v2"
V3_DEFAULT_DIR = paths.ROOT / "reports" / "payment_delay"
COHORT_HORIZONS = (30, 60, 90)
COHORT_PANEL = "panel_cobro.parquet"

# --- constantes de diseno (PROVISIONALES, pendientes de calibracion) -------
# Mecanismo 1: cortes de bucket compartidos con la capa A (debt_obligation).
BUCKET_EDGES_DIAS = (30, 60, 90, 180)
BUCKET_LABELS = ("1_30", "31_60", "61_90", "91_180", "180_mas")
# Pesos crecientes y concavos: el impago joven es desfase de tesoreria; a
# partir de 180 dias se aproxima a perdida. Tope 1,00 => severidad en [0, 1].
BUCKET_PESOS = (0.10, 0.25, 0.45, 0.70, 1.00)
# Mecanismo 2: peso del mes corriente en la EWMA (PROVISIONAL).
LAMBDA_EWMA = 0.35
# Mecanismo 4: euros virtuales de shrinkage, alineados con la k calibrada
# del score (reports/calibration_k/report.md: 4178,45 EUR).
KAPPA_EUR = 4178.45
# Mecanismo 3: pesos del indice combinado (pago manda sobre cobro).
PESO_MORA_PAGO = 0.70
PESO_MORA_COBRO = 0.30
# Umbrales del criterio de confidence (cartera "gruesa").
MIN_EXPOSICION_CONFIANZA_ALTA = 2000.0
MIN_FACTURAS_CONFIANZA_ALTA = 3

LADO_PAGO = "outflow"
LADO_COBRO = "inflow"
PREFIJO = {LADO_PAGO: "pago", LADO_COBRO: "cobro"}

LIMITACION_RESIDUAL = (
    "La fuente no tiene fecha de importacion ni de cancelacion: no puede "
    "demostrarse que una factura estuviera REGISTRADA en el sistema en el "
    "corte m, solo que su vencimiento era anterior a m. Medida point-in-time "
    "por vencimiento, no por disponibilidad de registro certificada."
)

CRITERIO_CONFIDENCE = (
    "ninguna=sin cartera exigible en el corte; baja=agujeros >=50% de la "
    "cartera; media=algun agujero o cartera fina "
    f"(<{MIN_FACTURAS_CONFIANZA_ALTA} facturas o <{MIN_EXPOSICION_CONFIANZA_ALTA:g} EUR); "
    "alta=sin agujeros y cartera gruesa. confidence de fila = peor de los "
    "lados observables"
)

JUSTIFICACION_CONSTANTES = {
    "bucket_edges_dias": (
        "Mismos cinco cortes que la capa A (debt_obligation.py) para que ambas "
        "capas sean legibles juntas."
    ),
    "bucket_pesos": (
        "Curva concava de perdida esperada: 0,10/0,25/0,45/0,70/1,00 crece con "
        "la antiguedad del impago y satura en 1,00 a partir de 180 dias."
    ),
    "lambda_ewma": (
        "Peso del mes corriente ~1/3: amortigua picos aislados y exige 2-3 "
        "meses de mora sostenida para acercarse al nivel observado."
    ),
    "kappa_eur": (
        "Mismo mecanismo de euros virtuales que el score; valor de k "
        "recomendado por la calibracion medida (4178,45 EUR)."
    ),
    "peso_mora_pago": (
        "La conducta de pago es propia y accionable: pesa mas en el indice."
    ),
    "peso_mora_cobro": (
        "El retraso de cobro es riesgo externo y mas ruidoso: pesa menos."
    ),
    "estado": "PROVISIONAL pendiente de calibracion",
}


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class SideV2Stats:
    """Severidad observable de un lado (AP/AR) en un corte."""

    exposure_eur: float | None = None
    overdue_eur: float = 0.0
    weighted_overdue_eur: float = 0.0
    severity: float | None = None
    bucket_eur: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)
    n_cartera: int = 0
    n_huecos: int = 0
    n_huecos_sin_importe: int = 0
    n_huecos_fx: int = 0
    hueco_eur: float = 0.0
    confidence: str = "ninguna"

    def to_prefixed(self, prefix: str) -> dict:
        row: dict = {
            f"{prefix}_exposicion_eur": self.exposure_eur,
            f"{prefix}_vencido_eur": self.overdue_eur,
            f"{prefix}_vencido_ponderado_eur": self.weighted_overdue_eur,
            f"{prefix}_severidad": self.severity,
            f"{prefix}_n_facturas": self.n_cartera,
            f"{prefix}_n_huecos_eur": self.n_huecos,
            f"{prefix}_n_huecos_sin_importe": self.n_huecos_sin_importe,
            f"{prefix}_n_huecos_fx": self.n_huecos_fx,
            f"{prefix}_hueco_eur": self.hueco_eur,
        }
        for label, value in zip(BUCKET_LABELS, self.bucket_eur):
            row[f"{prefix}_vencido_{label}_eur"] = value
        return row


@dataclass(frozen=True)
class CompanyMonthResult:
    """Resultado (company_id, mes cerrado). NULL nunca es cero."""

    company_id: str
    month: date
    pago: SideV2Stats = field(default_factory=SideV2Stats)
    cobro: SideV2Stats = field(default_factory=SideV2Stats)
    # --- indices robustos por lado y combinado ---
    pago_severidad_shrunk: float | None = None
    mora_pago_robusta: float | None = None
    pago_shrinkage_aplicado: bool = False
    cobro_severidad_shrunk: float | None = None
    mora_cobro_robusta: float | None = None
    cobro_shrinkage_aplicado: bool = False
    mora_indice: float | None = None
    # --- honestidad y ausencia ---
    confidence_pago: str = "ninguna"
    confidence_cobro: str = "ninguna"
    confidence: str = "ninguna"
    reason: str | None = None
    n_cancel: int = 0
    n_vencimiento_desconocido: int = 0
    # --- cohortes (consumo condicionado por available_at) ---
    coh_pct_cobrado_30d: float | None = None
    coh_30d_available_at: date | None = None
    coh_pct_cobrado_60d: float | None = None
    coh_60d_available_at: date | None = None
    coh_pct_cobrado_90d: float | None = None
    coh_90d_available_at: date | None = None
    flags: tuple[str, ...] = ()

    def to_row(self) -> dict:
        row: dict = {"company_id": self.company_id, "month": self.month}
        row.update(self.pago.to_prefixed("pago"))
        row.update(self.cobro.to_prefixed("cobro"))
        row.update(
            {
                "pago_severidad_shrunk": self.pago_severidad_shrunk,
                "mora_pago_robusta": self.mora_pago_robusta,
                "pago_shrinkage_aplicado": self.pago_shrinkage_aplicado,
                "cobro_severidad_shrunk": self.cobro_severidad_shrunk,
                "mora_cobro_robusta": self.mora_cobro_robusta,
                "cobro_shrinkage_aplicado": self.cobro_shrinkage_aplicado,
                "mora_indice": self.mora_indice,
                "confidence_pago": self.confidence_pago,
                "confidence_cobro": self.confidence_cobro,
                "confidence": self.confidence,
                "reason": self.reason,
                "n_cancel": self.n_cancel,
                "n_vencimiento_desconocido": self.n_vencimiento_desconocido,
                "coh_pct_cobrado_30d": self.coh_pct_cobrado_30d,
                "coh_30d_available_at": self.coh_30d_available_at,
                "coh_pct_cobrado_60d": self.coh_pct_cobrado_60d,
                "coh_60d_available_at": self.coh_60d_available_at,
                "coh_pct_cobrado_90d": self.coh_pct_cobrado_90d,
                "coh_90d_available_at": self.coh_90d_available_at,
                "flags": list(self.flags),
            }
        )
        return row


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def closed_months(start: date = FIRST_MONTH, end: date = LAST_MONTH) -> list[date]:
    """Primer dia de cada mes cerrado entre start y end (ambos incluidos)."""
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _bucket_index(days: int) -> int:
    """Indice del bucket para ``days`` dias desde el vencimiento.

    El primer bucket absorbe 0 dias (vence exactamente el fin de mes): la
    regla point-in-time cuenta ``due_date_ok <= fin de m`` como exigible.
    """
    if days <= BUCKET_EDGES_DIAS[0]:
        return 0
    if days <= BUCKET_EDGES_DIAS[1]:
        return 1
    if days <= BUCKET_EDGES_DIAS[2]:
        return 2
    if days <= BUCKET_EDGES_DIAS[3]:
        return 3
    return 4


def _side_confidence(n_cartera: int, n_huecos: int, exposure: float | None) -> str:
    if n_cartera == 0:
        return "ninguna"
    if n_huecos / n_cartera >= 0.5:
        return "baja"
    if n_huecos > 0:
        return "media"
    if exposure is None or exposure < MIN_EXPOSICION_CONFIANZA_ALTA:
        return "media"
    if n_cartera < MIN_FACTURAS_CONFIANZA_ALTA:
        return "media"
    return "alta"


_CONF_ORDER = {"ninguna": 0, "baja": 1, "media": 2, "alta": 3}


def _row_confidence(conf_pago: str, conf_cobro: str) -> str:
    observables = [c for c in (conf_pago, conf_cobro) if c != "ninguna"]
    if not observables:
        return "ninguna"
    return min(observables, key=_CONF_ORDER.__getitem__)


def _side_stats(records: list[InvoiceRecord], month_end: date) -> SideV2Stats:
    """Severidad ponderada por dias observable en el corte para un lado.

    Solo facturas exigibles (``due_date <= month_end``) y no canceladas. Una
    factura pagada DESPUES del corte sigue vencida EN ESE CORTE. Los agujeros
    no se imputan como cero: se cuentan aparte y no entran en la exposicion.
    """
    exposure = 0.0
    overdue = 0.0
    weighted = 0.0
    buckets = [0.0, 0.0, 0.0, 0.0, 0.0]
    n_cartera = 0
    n_huecos = 0
    n_sin_importe = 0
    n_fx = 0
    hueco_eur = 0.0

    for r in records:
        if r.due_date is None or r.due_date > month_end:
            continue  # no exigible en el corte
        if r.status == "cancel":
            continue  # la obligacion desaparece: no es mora
        n_cartera += 1
        if r.amount_eur is None or not math.isfinite(r.amount_eur):
            n_huecos += 1
            n_sin_importe += 1  # magnitud desconocida: ni cero ni imputacion
            continue
        if r.fx_ambiguous:
            n_huecos += 1
            n_fx += 1
            hueco_eur += abs(r.amount_eur)
            continue
        amt = abs(r.amount_eur)
        exposure += amt
        real_payment = r.payment_date if r.status == "paid" else None
        if real_payment is not None and real_payment <= month_end:
            continue  # liquidada a fecha de corte: no vencida
        # Vencida y NO liquidada al corte (status <> 'paid' o pago posterior).
        idx = _bucket_index((month_end - r.due_date).days)
        buckets[idx] += amt
        overdue += amt
        weighted += BUCKET_PESOS[idx] * amt

    severity = (weighted / exposure) if exposure > 0 else None
    return SideV2Stats(
        exposure_eur=exposure if exposure > 0 else None,
        overdue_eur=overdue,
        weighted_overdue_eur=weighted,
        severity=severity,
        bucket_eur=tuple(buckets),  # type: ignore[arg-type]
        n_cartera=n_cartera,
        n_huecos=n_huecos,
        n_huecos_sin_importe=n_sin_importe,
        n_huecos_fx=n_fx,
        hueco_eur=hueco_eur,
        confidence=_side_confidence(n_cartera, n_huecos, exposure if exposure > 0 else None),
    )


def _shrink_severity(
    severity: float | None,
    exposure: float | None,
    hist_severity: float | None,
    has_history: bool,
) -> tuple[float | None, bool, bool]:
    """Shrinkage por euros virtuales.

    Devuelve (severidad_shrunk, shrinkage_aplicado, sin_historia). Si no hay
    historia se omite el termino kappa (no se inventa 0,5).
    """
    if severity is None or exposure is None:
        return None, False, not has_history
    if not has_history or hist_severity is None:
        return severity, False, True
    shrunk = (
        exposure * severity + KAPPA_EUR * hist_severity
    ) / (exposure + KAPPA_EUR)
    return shrunk, True, False


def compute_payment_delay_v2(
    invoices: list[InvoiceRecord],
    months: list[date],
    cohorts: list[CohortRecord] | None = None,
    companies: list[str] | None = None,
) -> list[CompanyMonthResult]:
    """Motor PURO: indice de morosidad robusto por (company_id, mes cerrado).

    - invoices: facturas document_type='invoice' (el llamador filtra el resto;
      cancel y due nulo los trata el motor y se cuentan aparte).
    - months: primeros de mes cerrado; el corte de cada mes es su fin de mes.
    - cohorts: cohortes de cobro; cada una se consume SOLO si su
      ``available_at <= fin de m`` (consumir antes seria fuga).
    - companies: rejilla completa de empresas (incluye las sin facturas).

    No muta la entrada; determinista; sin reloj ni IO; nunca mira meses
    posteriores al corte que calcula.
    """
    by_company_side: dict[str, dict[str, list[InvoiceRecord]]] = {}
    for inv in invoices:
        by_company_side.setdefault(inv.company_id, {}).setdefault(
            inv.flow_side, []
        ).append(inv)

    cohorts_by_company: dict[str, list[CohortRecord]] = {}
    horizontes_con_cohorte: set[tuple[str, int]] = set()
    for c in cohorts or []:
        horizontes_con_cohorte.add((c.company_id, c.horizon_days))
        if c.pct_cobrado is None:
            continue
        cohorts_by_company.setdefault(c.company_id, []).append(c)
    for lst in cohorts_by_company.values():
        lst.sort(key=lambda r: (r.cohort_month, r.horizon_days))

    ids = list(companies) if companies is not None else sorted(by_company_side)
    results: list[CompanyMonthResult] = []
    for cid in ids:
        sides = by_company_side.get(cid, {})
        pago_records = sides.get(LADO_PAGO, [])
        cobro_records = sides.get(LADO_COBRO, [])
        all_records = pago_records + cobro_records
        clist = cohorts_by_company.get(cid, [])
        j = 0  # cohortes ya disponibles (available_at <= fin de m)

        # Estado ascendente de la propia empresa: EWMA e historia bruta.
        prev_robust: dict[str, float | None] = {LADO_PAGO: None, LADO_COBRO: None}
        hist_num: dict[str, float] = {LADO_PAGO: 0.0, LADO_COBRO: 0.0}
        hist_den: dict[str, float] = {LADO_PAGO: 0.0, LADO_COBRO: 0.0}

        for m in months:
            month_end = _month_end(m)
            while j < len(clist) and clist[j].available_at <= month_end:
                j += 1
            activos = clist[:j]

            stats = {
                LADO_PAGO: _side_stats(pago_records, month_end),
                LADO_COBRO: _side_stats(cobro_records, month_end),
            }

            shrunk: dict[str, float | None] = {}
            aplicado: dict[str, bool] = {}
            sin_historia: dict[str, bool] = {}
            for side in (LADO_PAGO, LADO_COBRO):
                st = stats[side]
                has_hist = hist_den[side] > 0
                hist_sev = (
                    hist_num[side] / hist_den[side] if has_hist else None
                )
                s, ap, sh = _shrink_severity(
                    st.severity, st.exposure_eur, hist_sev, has_hist
                )
                shrunk[side] = s
                aplicado[side] = ap
                sin_historia[side] = sh

            robust: dict[str, float | None] = {}
            for side in (LADO_PAGO, LADO_COBRO):
                s = shrunk[side]
                if s is None:
                    robust[side] = None
                elif prev_robust[side] is None:
                    robust[side] = s  # primer corte observable
                else:
                    robust[side] = (
                        LAMBDA_EWMA * s + (1 - LAMBDA_EWMA) * prev_robust[side]
                    )

            # Estado se actualiza DESPUES de calcular m (nunca mira el futuro).
            for side in (LADO_PAGO, LADO_COBRO):
                st = stats[side]
                if st.severity is not None and st.exposure_eur is not None:
                    hist_num[side] += st.exposure_eur * st.severity
                    hist_den[side] += st.exposure_eur
                if robust[side] is not None:
                    prev_robust[side] = robust[side]

            mp = robust[LADO_PAGO]
            mc = robust[LADO_COBRO]
            flags: list[str] = []
            if mp is not None and mc is not None:
                indice = PESO_MORA_PAGO * mp + PESO_MORA_COBRO * mc
            elif mp is not None:
                indice = mp
                flags.append("mora_indice_solo_pago")
            elif mc is not None:
                indice = mc
                flags.append("mora_indice_solo_cobro")
            else:
                indice = None

            for side in (LADO_PAGO, LADO_COBRO):
                if sin_historia[side] and stats[side].severity is not None:
                    flags.append(f"shrinkage_{PREFIJO[side]}_sin_historia_previa")
                if stats[side].n_huecos > 0:
                    flags.append(f"agujeros_{PREFIJO[side]}_fx_o_eur_en_el_corte")

            n_cancel = sum(
                1
                for r in all_records
                if r.status == "cancel"
                and r.due_date is not None
                and r.due_date <= month_end
            )
            n_due_desconocido = sum(
                1
                for r in all_records
                if r.status != "cancel"
                and r.due_date is None
                and r.flow_side in (LADO_PAGO, LADO_COBRO)
            )

            conf_pago = stats[LADO_PAGO].confidence
            conf_cobro = stats[LADO_COBRO].confidence
            confidence = _row_confidence(conf_pago, conf_cobro)
            reason = "sin_cartera_observada" if confidence == "ninguna" else None

            # cohortes: la mas reciente disponible (available_at <= fin de m)
            coh_vals: dict[int, float | None] = {}
            coh_av: dict[int, date | None] = {}
            for h in COHORT_HORIZONS:
                best: CohortRecord | None = None
                for rec in reversed(activos):
                    if rec.horizon_days == h:
                        best = rec
                        break
                if best is not None:
                    coh_vals[h] = best.pct_cobrado
                    coh_av[h] = best.available_at
                    continue
                coh_vals[h] = None
                coh_av[h] = None
                if (cid, h) in horizontes_con_cohorte:
                    flags.append(f"coh_{h}d_no_disponible_en_el_corte")
                    flags.append("cohorte_no_disponible_en_el_corte")

            results.append(
                CompanyMonthResult(
                    company_id=cid,
                    month=m,
                    pago=stats[LADO_PAGO],
                    cobro=stats[LADO_COBRO],
                    pago_severidad_shrunk=shrunk[LADO_PAGO],
                    mora_pago_robusta=mp,
                    pago_shrinkage_aplicado=aplicado[LADO_PAGO],
                    cobro_severidad_shrunk=shrunk[LADO_COBRO],
                    mora_cobro_robusta=mc,
                    cobro_shrinkage_aplicado=aplicado[LADO_COBRO],
                    mora_indice=indice,
                    confidence_pago=conf_pago,
                    confidence_cobro=conf_cobro,
                    confidence=confidence,
                    reason=reason,
                    n_cancel=n_cancel,
                    n_vencimiento_desconocido=n_due_desconocido,
                    coh_pct_cobrado_30d=coh_vals[30],
                    coh_30d_available_at=coh_av[30],
                    coh_pct_cobrado_60d=coh_vals[60],
                    coh_60d_available_at=coh_av[60],
                    coh_pct_cobrado_90d=coh_vals[90],
                    coh_90d_available_at=coh_av[90],
                    flags=tuple(dict.fromkeys(flags)),
                )
            )
    return results


# --------------------------------------------------------------------- #
# Helpers de comparativa y coverage (CLI)
# --------------------------------------------------------------------- #
def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo))


def _distribution(values: list[float]) -> dict:
    s = sorted(values)
    return {
        "n": len(s),
        "mediana": _quantile(s, 0.5),
        "p75": _quantile(s, 0.75),
        "p90": _quantile(s, 0.9),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _average_ranks(values: list[float]) -> list[float]:
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


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def _assert_no_nan(results: list[CompanyMonthResult]) -> None:
    """Serializacion estricta: nan/inf prohibidos."""
    for r in results:
        for key, val in r.to_row().items():
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"NaN/inf en {key} de {r.company_id}/{r.month}")
            if isinstance(val, (list, tuple)):
                for item in val:
                    if isinstance(item, float) and not math.isfinite(item):
                        raise ValueError(
                            f"NaN/inf en {key} de {r.company_id}/{r.month}"
                        )


def _load_cohorts(con, marts_dir: Path) -> list[CohortRecord]:
    """Lee SOLO las columnas de cohorte del panel (nunca stock retrospectivo)."""
    panel = marts_dir / COHORT_PANEL
    if not panel.exists():
        return []
    rows = con.execute(
        f"""
        SELECT company_id, month,
               coh_pct_cobrado_30d, coh_30d_available_at,
               coh_pct_cobrado_60d, coh_60d_available_at,
               coh_pct_cobrado_90d, coh_90d_available_at
        FROM read_parquet('{panel}')
        WHERE coh_pct_cobrado_30d IS NOT NULL
           OR coh_pct_cobrado_60d IS NOT NULL
           OR coh_pct_cobrado_90d IS NOT NULL
        """
    ).fetchall()
    out: list[CohortRecord] = []
    for r in rows:
        for h, (pct, av) in zip(
            COHORT_HORIZONS, ((r[2], r[3]), (r[4], r[5]), (r[6], r[7]))
        ):
            if pct is not None and av is not None:
                out.append(
                    CohortRecord(
                        company_id=r[0],
                        cohort_month=r[1],
                        horizon_days=h,
                        pct_cobrado=pct,
                        available_at=av,
                    )
                )
    return out


def _load_v3(con, v3_dir: Path) -> tuple[list[tuple], dict | None]:
    """Filas (company_id, month, mora_ratio) de v3 y su coverage.json."""
    parquet = v3_dir / "payment_delay_monthly.parquet"
    rows: list[tuple] = []
    if parquet.exists():
        raw = con.execute(
            f"SELECT company_id, month, mora_ratio "
            f"FROM read_parquet('{parquet}') WHERE mora_ratio IS NOT NULL"
        ).fetchall()
        for cid, month, ratio in raw:
            if hasattr(month, "date"):
                month = month.date()
            rows.append((cid, month, ratio))
    coverage = None
    cov_path = v3_dir / "coverage.json"
    if cov_path.exists():
        try:
            coverage = json.loads(cov_path.read_text())
        except (OSError, ValueError):
            coverage = None
    return rows, coverage


def _build_coverage(
    results: list[CompanyMonthResult],
    n_months: int,
    n_empresas: int,
    workspace: str,
    v3_rows: list[tuple],
    v3_coverage: dict | None,
) -> dict:
    """coverage.json: alcance, comparativa con v3 y limitacion residual."""
    n_rows = len(results)

    # bloques de cobertura
    pago_rows = [r for r in results if r.mora_pago_robusta is not None]
    cobro_rows = [r for r in results if r.mora_cobro_robusta is not None]
    indice_rows = [r for r in results if r.mora_indice is not None]
    pago_vals = [r.mora_pago_robusta for r in pago_rows]
    cobro_vals = [r.mora_cobro_robusta for r in cobro_rows]
    indice_vals = [r.mora_indice for r in indice_rows]

    v3_decl_em = None
    v3_decl_emp = None
    if v3_coverage and isinstance(v3_coverage.get("mora_ratio_calculable"), dict):
        v3_decl_em = v3_coverage["mora_ratio_calculable"].get("empresa_mes")
        v3_decl_emp = v3_coverage["mora_ratio_calculable"].get("empresas")
    if v3_decl_em is None:
        v3_decl_em = len(v3_rows)
    if v3_decl_emp is None:
        v3_decl_emp = len({r[0] for r in v3_rows})

    ganancia_em = len(indice_rows) - v3_decl_em
    ganancia_emp = len({r.company_id for r in indice_rows}) - v3_decl_emp

    # confidence
    conf = {"alta": 0, "media": 0, "baja": 0, "ninguna": 0}
    for r in results:
        conf[r.confidence] = conf.get(r.confidence, 0) + 1

    # distribucion v3 medida en el parquet
    v3_vals = [r[2] for r in v3_rows]
    if v3_coverage and isinstance(v3_coverage.get("distribucion_mora_ratio"), dict):
        v3_dist = dict(v3_coverage["distribucion_mora_ratio"])
    else:
        v3_dist = _distribution(v3_vals)
    v3_dist["n"] = len(v3_vals) if v3_vals else v3_dist.get("n")

    dist_indice = _distribution(indice_vals)
    dist_pago = _distribution(pago_vals)
    dist_cobro = _distribution(cobro_vals)

    # correlacion mora_indice vs v3 mora_ratio en filas donde ambos existen
    v3_by_key = {(r[0], r[1]): r[2] for r in v3_rows}
    xs: list[float] = []
    ys: list[float] = []
    for r in results:
        v3v = v3_by_key.get((r.company_id, r.month))
        if r.mora_indice is not None and v3v is not None:
            xs.append(r.mora_indice)
            ys.append(v3v)
    correl = {
        "n": len(xs),
        "pearson": _pearson(xs, ys),
        "spearman": _spearman(xs, ys),
        "nota": "solo filas donde mora_indice y el mora_ratio de v3 son no nulos",
    }

    return {
        "capa": "healthscore_v4 · capa C · indice de morosidad robusto (payment_delay_v2)",
        "grano": "(company_id, month), meses cerrados",
        "workspace": workspace,
        "filas": n_rows,
        "meses": n_months,
        "empresas_grid": n_empresas,
        "constantes_diseno": {
            "bucket_edges_dias": list(BUCKET_EDGES_DIAS),
            "bucket_labels": list(BUCKET_LABELS),
            "bucket_pesos": list(BUCKET_PESOS),
            "lambda_ewma": LAMBDA_EWMA,
            "kappa_eur": KAPPA_EUR,
            "peso_mora_pago": PESO_MORA_PAGO,
            "peso_mora_cobro": PESO_MORA_COBRO,
            "justificacion": JUSTIFICACION_CONSTANTES,
        },
        "criterio_confidence": CRITERIO_CONFIDENCE,
        "cobertura": {
            "filas_calculables_mora_pago": {
                "empresa_mes": len(pago_rows),
                "empresas": len({r.company_id for r in pago_rows}),
            },
            "filas_calculables_mora_cobro": {
                "empresa_mes": len(cobro_rows),
                "empresas": len({r.company_id for r in cobro_rows}),
            },
            "filas_calculables_mora_indice": {
                "empresa_mes": len(indice_rows),
                "empresas": len({r.company_id for r in indice_rows}),
                "pct_sobre_filas": (
                    round(len(indice_rows) / n_rows, 4) if n_rows else None
                ),
            },
            "v3_referencia": {
                "mora_ratio_calculable_empresa_mes": v3_decl_em,
                "mora_ratio_calculable_empresas": v3_decl_emp,
            },
            "ganancia_vs_v3": {
                "empresa_mes_absoluta": ganancia_em,
                "empresa_mes_factor": (
                    round(len(indice_rows) / v3_decl_em, 3) if v3_decl_em else None
                ),
                "empresas_absoluta": ganancia_emp,
                "empresas_factor": (
                    round(len({r.company_id for r in indice_rows}) / v3_decl_emp, 3)
                    if v3_decl_emp
                    else None
                ),
                "nota": "mora_indice cubre filas con agujeros fx/eur y filas con "
                "un solo lado observable; v3 exigia cartera sin agujeros",
            },
        },
        "distribuciones": {
            "mora_pago_robusta": dist_pago,
            "mora_cobro_robusta": dist_cobro,
            "mora_indice": dist_indice,
            "v3_mora_ratio_referencia": v3_dist,
        },
        "reduccion_saturacion": {
            "v3_p75": v3_dist.get("p75"),
            "v3_p90": v3_dist.get("p90"),
            "mora_indice_p75": dist_indice["p75"],
            "mora_indice_p90": dist_indice["p90"],
            "mora_pago_p75": dist_pago["p75"],
            "mora_pago_p90": dist_pago["p90"],
            "mora_cobro_p75": dist_cobro["p75"],
            "mora_cobro_p90": dist_cobro["p90"],
            "lectura": "los pesos por antiguedad, el shrinkage y la EWMA bajan "
            "la cola alta (p75/p90) por debajo del 1,0 saturado de v3",
        },
        "correlacion_mora_indice_vs_v3_mora_ratio": correl,
        "confidence_reparto": conf,
        "confidence_mora_indice": {
            k: sum(1 for r in indice_rows if r.confidence == k)
            for k in ("alta", "media", "baja")
        },
        "agujeros_fx_eur": {
            "empresa_mes_con_agujeros": sum(
                1 for r in results if r.pago.n_huecos + r.cobro.n_huecos > 0
            ),
            "n_huecos_sin_importe": sum(
                r.pago.n_huecos_sin_importe + r.cobro.n_huecos_sin_importe
                for r in results
            ),
            "n_huecos_fx": sum(r.pago.n_huecos_fx + r.cobro.n_huecos_fx for r in results),
            "nota": "fx_ambiguous o amount_eur nulo: ni cero ni imputacion; "
            "se cuentan aparte y degradan la confidence de la fila. Los "
            "contadores n_huecos_* son sumas acumuladas sobre cortes "
            "point-in-time (una factura cuenta desde su vencimiento).",
        },
        "cancel_y_vencimiento_desconocido": {
            "cancel_empresa_mes_suma": sum(r.n_cancel for r in results),
            "vencimiento_desconocido_empresa_mes_suma": sum(
                r.n_vencimiento_desconocido for r in results
            ),
            "nota": "sumas acumuladas sobre cortes: una factura cuenta en todos "
            "los cortes desde su vencimiento (point-in-time)",
        },
        "limitacion_residual": LIMITACION_RESIDUAL,
        "fuente": "clean/invoices.parquet (document_type_norm='invoice')",
        "no_consumido": (
            "marts/panel_cobro: columnas de stock reconstruidas hacia atras "
            "(fuga); solo cohortes coh_pct_cobrado_Nd con "
            "coh_Nd_available_at <= fin de corte"
        ),
    }


def _resolve_clean_dir(workspace: Path | None) -> Path:
    """Acepta raiz de run (con data/clean) o el propio data/ (con clean/)."""
    base = Path(workspace) if workspace is not None else paths.WORKSPACE
    if (base / "data" / "clean").exists():
        return base / "data" / "clean"
    if (base / "clean").exists():
        return base
    raise FileNotFoundError(
        f"No encuentro clean/ bajo {base} (se esperaba <workspace>/data/clean "
        "o <data>/clean)"
    )


def _resolve_marts_dir(workspace: Path | None) -> Path:
    base = Path(workspace) if workspace is not None else paths.WORKSPACE
    if (base / "data" / "marts").exists():
        return base / "data" / "marts"
    if (base / "marts").exists():
        return base
    raise FileNotFoundError(
        f"No encuentro marts/ bajo {base} (se esperaba <workspace>/data/marts "
        "o <data>/marts)"
    )


def _load_invoices(con, clean_dir: Path) -> list[InvoiceRecord]:
    inv_path = clean_dir / "invoices.parquet"
    rows = con.execute(
        f"""
        SELECT company_id, flow_side, status_norm, due_date_ok,
               payment_date_ok, amount_eur, fx_ambiguous
        FROM read_parquet('{inv_path}')
        WHERE document_type_norm = '{DOC_INVOICE}'
          AND flow_side IN ('inflow', 'outflow')
        """
    ).fetchall()
    return [
        InvoiceRecord(
            company_id=r[0],
            flow_side=r[1],
            status=r[2],
            due_date=r[3].date() if r[3] is not None else None,
            payment_date=r[4].date() if r[4] is not None else None,
            amount_eur=r[5],
            fx_ambiguous=bool(r[6]),
        )
        for r in rows
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI: escribe en ruta nueva bajo reports/ y ABORTA si ya existe."""
    parser = argparse.ArgumentParser(
        prog="xray.payment_delay_v2",
        description=(
            "Indice de morosidad robusto (capa C de healthscore_v4): severidad "
            "ponderada por dias, EWMA, dos lados e shrinkage"
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="raiz de run (con data/clean y data/marts) o directorio data/; "
        "por defecto, la ejecucion activa de xray.paths",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directorio de salida (nuevo); por defecto reports/payment_delay_v2",
    )
    parser.add_argument(
        "--v3-dir",
        type=Path,
        default=V3_DEFAULT_DIR,
        help="directorio del informe v3 para la comparativa",
    )
    args = parser.parse_args(argv)

    if args.output_dir.exists():
        print(
            f"ABORTADO: la ruta de salida {args.output_dir} ya existe. "
            "Indica otra ruta con --output-dir para no destruir ejecuciones previas.",
            file=sys.stderr,
        )
        return 2

    clean_dir = _resolve_clean_dir(args.workspace)
    marts_dir = _resolve_marts_dir(args.workspace)

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        invoices = _load_invoices(con, clean_dir)
        companies = [
            r[0]
            for r in con.execute(
                f"SELECT company_id FROM read_parquet('{clean_dir / 'companies.parquet'}') "
                "ORDER BY company_id"
            ).fetchall()
        ]
        cohorts = _load_cohorts(con, marts_dir)
        v3_rows, v3_coverage = _load_v3(con, args.v3_dir)
    finally:
        con.close()

    months = closed_months()
    results = compute_payment_delay_v2(invoices, months, cohorts, companies)
    _assert_no_nan(results)

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in results])
    frame["month"] = pd.to_datetime(frame["month"])
    for c in ("coh_30d_available_at", "coh_60d_available_at", "coh_90d_available_at"):
        frame[c] = pd.to_datetime(frame[c])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    frame.to_parquet(args.output_dir / "payment_delay_v2_monthly.parquet", index=False)

    workspace = clean_dir.parent
    if workspace.is_relative_to(paths.ROOT):
        workspace = workspace.relative_to(paths.ROOT)
    coverage = _build_coverage(
        results, len(months), len(companies), str(workspace), v3_rows, v3_coverage
    )
    (args.output_dir / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False)
    )
    print(
        json.dumps(
            {
                "output": str(args.output_dir),
                "filas": coverage["filas"],
                "cobertura_mora_indice": coverage["cobertura"][
                    "filas_calculables_mora_indice"
                ],
                "distribucion_mora_indice": coverage["distribuciones"]["mora_indice"],
                "v3_referencia": coverage["cobertura"]["v3_referencia"],
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
