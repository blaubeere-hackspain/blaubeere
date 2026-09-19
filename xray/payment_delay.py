"""payment_delay — senal point-in-time de puntualidad de pago (AP) y cobro (AR).

PROBLEMA QUE ABORDA
-------------------
En el healthscore actual, P y D son pagos REALIZADOS (flujos observados), no
obligaciones exigibles: una empresa que deja de pagar ve bajar sus salidas y
por tanto MEJORA su nota. Esta senal corrige esa fisura aportando la cara de
obligacion: de lo que la empresa YA DEBIA a fecha del corte (facturas con
vencimiento <= fin de mes cerrado m), cuanta parte estaba en mora EN ESE
CORTE. Dejar de pagar => sube mora_ratio => la penalizacion asimetrica de la
capa de scoring v3 (disenada en otra tarea) tendra donde caer. Esta tarea solo
PRODUCE la senal y su cobertura; no implementa la penalizacion.

DISCIPLINA POINT-IN-TIME (corte = fin de mes cerrado m)
-------------------------------------------------------
Para cada corte solo influye lo observable en el corte:

- Universo por corte: facturas 'invoice' con due_date_ok <= fin de m (lo ya
  exigible a esa fecha).
- pagado_a_tiempo: status_norm='paid' y payment_date_ok <= due_date_ok.
  payment_date_ok solo es fecha REAL con status 'paid'; en cualquier otro
  status es fecha PREVISTA y se trata como NULL (regla heredada de
  xray/marts/cobro.py).
- pagado_tarde: status_norm='paid' y due_date_ok < payment_date_ok <= fin de m.
- en_mora_en_el_corte: vencida y NO liquidada a fecha de corte:
  status_norm <> 'paid'  O  payment_date_ok > fin de m. Una factura pagada
  DESPUES del corte cuenta como en mora EN ESE CORTE aunque hoy sepamos que
  acabo pagandose: eso es exactamente la medida point-in-time (test 1).
- mora_ratio = en_mora / debido, en [0, 1]. debido_eur = 0 -> NULL con
  motivo; NULL nunca es 0 ni 1.
- retraso_medio_dias_pagado: media de (payment_date_ok - due_date_ok) sobre
  lo pagado y observable en el corte, ponderada por amount_eur.

El mismo calculo se aplica al lado COBRO (flow_side='inflow') con prefijo
cobro_: quien cobra tarde tiene tension de liquidez aunque pague bien.

DESCARTES DECLARADOS
--------------------
- document_type_norm: solo 'invoice' entra en la cartera, igual que el mart
  (DOC_INVOICE). Quedan fuera note (abonos/notas, no obligacion comercial
  original), deposit (anticipo sin ciclo due/pago propio), paymentdocument
  (liquidacion, no factura exigible), invoicegroup, deliverynote,
  purchaseorder y demas: no son facturas con vencimiento exigible comparable;
  mezclarlos distorsionaria el ratio.
- status_norm='cancel' no es mora (la obligacion desaparece): se excluye del
  calculo y se cuenta aparte en n_cancel.
- due_date_ok nulo: la factura no puede evaluarse por vencimiento; va a
  n_vencimiento_desconocido y NO entra en debido_eur.
- flow_side='zero' (amount=0): sin lado ni obligacion; fuera.

INCOGNITAS Y AGUJEROS (nulo nunca es cero)
------------------------------------------
- Facturas con fx_ambiguous o amount_eur nulo son agujeros: no se suman como
  cero y no se imputan a ningun bucket. Cuando hay agujeros en el corte no hay
  valor unico: mora_ratio es NULL y se publica el intervalo
  [mora_ratio_min, mora_ratio_max] con politica determinista:
    * min = mora conocida / (exigible conocido + importe de agujeros con EUR
      conocido): los agujeros se pagan puntual en el escenario optimista (los
      sin importe conocido no se suman a nada).
    * max = 1.0 si hay agujeros sin importe conocido (la mora podria ser
      total: no se puede acotar mejor); si todos los agujeros tienen EUR
      conocido, max = (mora conocida + agujeros no pagados a corte) /
      (exigible conocido + agujeros).
  Sin agujeros, mora_ratio = min = max y no hay ambiguedad.
- confidence categorica determinista: 'ninguna' (sin cartera observable en el
  corte), 'alta' (cartera sin agujeros), 'media' (agujeros en <50% de las
  facturas en cartera), 'baja' (agujeros en >=50%).
- Empresa sin facturas exigibles en el corte -> reason='sin_cartera_observada'
  y todo NULL: NO significa que pague puntual.
- Cohortes de cobro (coh_pct_cobrado_30/60/90d) leidas de marts/panel_cobro,
  consumidas SOLO cuando coh_Nd_available_at <= fin de m; en caso contrario
  NULL con flag 'coh_Nd_no_disponible_en_el_corte' (consumirlas antes seria
  fuga; ver docstring de xray/marts/cobro.py). NINGUNA otra columna del panel
  se consume: el stock del panel esta reconstruido hacia atras
  (es_reconstruccion_retrospectiva) y su uso en series historicas es fuga;
  esta capa deriva todo de clean/invoices.parquet.

LIMITACION RESIDUAL (P5, honestidad obligatoria)
------------------------------------------------
La fuente no tiene fecha de importacion ni de cancelacion. Por tanto NO puede
demostrarse que una factura estuviera REGISTRADA en el sistema en el corte m:
lo unico certificable es que su VENCIMIENTO era anterior a m. Esta medida es
point-in-time POR VENCIMIENTO, no por disponibilidad de registro certificada.
Una factura incorporada tarde al ERP con vencimiento antiguo se cuenta en
cortes en los que quizá aun no existia en el sistema. Toda lectura de
mora_ratio debe hacerse con esta salvedad, declarada tambien en coverage.json.

El motor (compute_payment_delay) es PURO: sin IO, sin reloj, sin estado
mutable, no muta su entrada. El CLI (main) escribe bajo reports/payment_delay/
y ABORTA si la ruta de salida ya existe.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from xray import paths
from xray.period import FIRST_MONTH, LAST_MONTH

DOC_INVOICE = "invoice"
DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "payment_delay"
COHORT_HORIZONS = (30, 60, 90)
COHORT_PANEL = "panel_cobro.parquet"

LIMITACION_RESIDUAL = (
    "La fuente no tiene fecha de importacion ni de cancelacion: no puede "
    "demostrarse que una factura estuviera REGISTRADA en el sistema en el "
    "corte m, solo que su vencimiento era anterior a m. Medida point-in-time "
    "por vencimiento, no por disponibilidad de registro certificada."
)


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class InvoiceRecord:
    """Factura normalizada; el motor no ve el parquet."""
    company_id: str
    flow_side: str  # 'outflow' (AP, la empresa paga) | 'inflow' (AR, le cobran)
    status: str
    due_date: date | None
    payment_date: date | None  # fecha real de pago; solo fiable si status='paid'
    amount_eur: float | None
    fx_ambiguous: bool


@dataclass(frozen=True)
class CohortRecord:
    """Cohorte de cobro del panel con su contrato de disponibilidad."""
    company_id: str
    cohort_month: date
    horizon_days: int
    pct_cobrado: float | None
    available_at: date


@dataclass(frozen=True)
class SideDelayStats:
    """Puntualidad observable en un corte para un lado (AP o AR)."""
    debido_eur: float | None = None
    pagado_a_tiempo_eur: float | None = None
    pagado_tarde_eur: float | None = None
    en_mora_en_el_corte_eur: float | None = None
    mora_ratio: float | None = None
    mora_ratio_min: float | None = None
    mora_ratio_max: float | None = None
    retraso_medio_dias_pagado: float | None = None
    n_en_cartera: int = 0
    n_huecos_eur: int = 0
    n_huecos_sin_importe: int = 0
    hueco_eur_total: float = 0.0
    hueco_eur_abierto: float = 0.0


@dataclass(frozen=True)
class CompanyMonthResult:
    """Resultado (company_id, month); nulo nunca es cero."""
    company_id: str
    month: date
    # --- lado pago (AP, flow_side='outflow') ---
    debido_eur: float | None = None
    pagado_a_tiempo_eur: float | None = None
    pagado_tarde_eur: float | None = None
    en_mora_en_el_corte_eur: float | None = None
    mora_ratio: float | None = None
    mora_ratio_min: float | None = None
    mora_ratio_max: float | None = None
    retraso_medio_dias_pagado: float | None = None
    # --- lado cobro (AR, flow_side='inflow') ---
    cobro_debido_eur: float | None = None
    cobro_pagado_a_tiempo_eur: float | None = None
    cobro_pagado_tarde_eur: float | None = None
    cobro_en_mora_en_el_corte_eur: float | None = None
    cobro_mora_ratio: float | None = None
    cobro_mora_ratio_min: float | None = None
    cobro_mora_ratio_max: float | None = None
    cobro_retraso_medio_dias_pagado: float | None = None
    # --- cohortes (consumo condicionado por available_at) ---
    coh_pct_cobrado_30d: float | None = None
    coh_30d_available_at: date | None = None
    coh_pct_cobrado_60d: float | None = None
    coh_60d_available_at: date | None = None
    coh_pct_cobrado_90d: float | None = None
    coh_90d_available_at: date | None = None
    # --- honestidad y ausencia ---
    confidence: str = "ninguna"
    reason: str | None = None
    n_cancel: int = 0
    n_vencimiento_desconocido: int = 0
    n_huecos_eur: int = 0
    flags: tuple[str, ...] = ()

    def to_row(self) -> dict:
        return {
            "company_id": self.company_id,
            "month": self.month,
            "debido_eur": self.debido_eur,
            "pagado_a_tiempo_eur": self.pagado_a_tiempo_eur,
            "pagado_tarde_eur": self.pagado_tarde_eur,
            "en_mora_en_el_corte_eur": self.en_mora_en_el_corte_eur,
            "mora_ratio": self.mora_ratio,
            "mora_ratio_min": self.mora_ratio_min,
            "mora_ratio_max": self.mora_ratio_max,
            "retraso_medio_dias_pagado": self.retraso_medio_dias_pagado,
            "cobro_debido_eur": self.cobro_debido_eur,
            "cobro_pagado_a_tiempo_eur": self.cobro_pagado_a_tiempo_eur,
            "cobro_pagado_tarde_eur": self.cobro_pagado_tarde_eur,
            "cobro_en_mora_en_el_corte_eur": self.cobro_en_mora_en_el_corte_eur,
            "cobro_mora_ratio": self.cobro_mora_ratio,
            "cobro_mora_ratio_min": self.cobro_mora_ratio_min,
            "cobro_mora_ratio_max": self.cobro_mora_ratio_max,
            "cobro_retraso_medio_dias_pagado": self.cobro_retraso_medio_dias_pagado,
            "coh_pct_cobrado_30d": self.coh_pct_cobrado_30d,
            "coh_30d_available_at": self.coh_30d_available_at,
            "coh_pct_cobrado_60d": self.coh_pct_cobrado_60d,
            "coh_60d_available_at": self.coh_60d_available_at,
            "coh_pct_cobrado_90d": self.coh_pct_cobrado_90d,
            "coh_90d_available_at": self.coh_90d_available_at,
            "confidence": self.confidence,
            "reason": self.reason,
            "n_cancel": self.n_cancel,
            "n_vencimiento_desconocido": self.n_vencimiento_desconocido,
            "n_huecos_eur": self.n_huecos_eur,
            "flags": list(self.flags),
        }


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


def _bucketize(records: list[InvoiceRecord], month_end: date) -> SideDelayStats:
    """Puntualidad observable en el corte month_end para un lado.

    Solo facturas con due_date <= month_end (exigibles en el corte) y
    status <> 'cancel'. Una factura pagada DESPUES del corte cuenta como en
    mora EN ESE CORTE (point-in-time). Los agujeros (fx_ambiguous o
    amount_eur nulo) no se suman como cero: se cuentan en n_huecos_eur.
    """
    debido = a_tiempo = tarde = en_mora = 0.0
    n_cartera = n_huecos = n_huecos_sin_importe = 0
    hueco_eur_total = hueco_eur_abierto = 0.0
    retraso_pond = retraso_peso = 0.0

    for r in records:
        if r.due_date is None or r.due_date > month_end:
            continue  # no exigible en el corte
        if r.status == "cancel":
            continue  # no es mora; se cuenta aparte en n_cancel
        n_cartera += 1
        real_payment = r.payment_date if r.status == "paid" else None
        if r.amount_eur is None or not math.isfinite(r.amount_eur):
            n_huecos += 1
            n_huecos_sin_importe += 1  # magnitud desconocida: ni cero ni imputacion
            continue
        if r.fx_ambiguous:
            n_huecos += 1
            hueco_eur_total += abs(r.amount_eur)
            pagado_a_corte = (
                real_payment is not None and real_payment <= month_end
            )
            if not pagado_a_corte:
                hueco_eur_abierto += abs(r.amount_eur)
            continue
        amt = abs(r.amount_eur)
        debido += amt
        if real_payment is not None and real_payment <= r.due_date:
            a_tiempo += amt
        elif real_payment is not None and real_payment <= month_end:
            tarde += amt
            retraso_pond += amt * (real_payment - r.due_date).days
            retraso_peso += amt
        else:
            # vencida y NO liquidada a fecha de corte: status <> 'paid' (su
            # payment_date es PREVISTA, tratada como NULL) o paid con pago
            # posterior al corte.
            en_mora += amt

    known = debido  # euros exigibles con importe conocido (sin agujeros)
    if n_huecos == 0:
        ratio = (en_mora / known) if known > 0 else None
        minimo = maximo = ratio
    else:
        den = known + hueco_eur_total
        ratio = None
        minimo = (en_mora / den) if den > 0 else (0.0 if hueco_eur_total > 0 or n_huecos else None)
        if n_huecos_sin_importe > 0:
            maximo = 1.0  # magnitud desconocida: la mora podria ser total
        elif den > 0:
            maximo = (en_mora + hueco_eur_abierto) / den
        else:
            maximo = 1.0
    return SideDelayStats(
        debido_eur=debido if known > 0 else None,
        pagado_a_tiempo_eur=a_tiempo if a_tiempo > 0 else None,
        pagado_tarde_eur=tarde if tarde > 0 else None,
        en_mora_en_el_corte_eur=en_mora if en_mora > 0 else None,
        mora_ratio=ratio,
        mora_ratio_min=minimo,
        mora_ratio_max=maximo,
        retraso_medio_dias_pagado=(
            retraso_pond / retraso_peso if retraso_peso > 0 else None
        ),
        n_en_cartera=n_cartera,
        n_huecos_eur=n_huecos,
        n_huecos_sin_importe=n_huecos_sin_importe,
        hueco_eur_total=hueco_eur_total,
        hueco_eur_abierto=hueco_eur_abierto,
    )


def compute_payment_delay(
    invoices: list[InvoiceRecord],
    months: list[date],
    cohorts: list[CohortRecord] | None = None,
    companies: list[str] | None = None,
) -> list[CompanyMonthResult]:
    """Motor PURO: senal point-in-time por (company_id, mes cerrado).

    - invoices: facturas document_type='invoice' (el llamador filtra el resto
      de tipos; cancel y due nulo los trata el motor, contados aparte).
    - months: primeros de mes cerrado; el corte de cada mes es su fin de mes.
    - cohorts: cohortes de cobro del panel; cada una se consume SOLO si su
      available_at <= fin de m; si no, NULL con flag explicito.
    - companies: grid completo de empresas (incluye las sin facturas).

    No muta la entrada; determinista; sin reloj ni IO.
    """
    by_company: dict[str, list[InvoiceRecord]] = {}
    for inv in invoices:
        by_company.setdefault(inv.company_id, []).append(inv)

    # cohortes por empresa, ordenadas por mes de cohorte; cada una se vuelve
    # consumible en el primer corte cuyo fin de mes >= su available_at
    cohorts_by_company: dict[str, list[CohortRecord]] = {}
    horizontes_con_cohorte: set[tuple[str, int]] = set()
    for c in cohorts or []:
        horizontes_con_cohorte.add((c.company_id, c.horizon_days))
        if c.pct_cobrado is None:
            continue
        cohorts_by_company.setdefault(c.company_id, []).append(c)
    for lst in cohorts_by_company.values():
        lst.sort(key=lambda r: (r.cohort_month, r.horizon_days))

    ids = list(companies) if companies is not None else sorted(by_company)
    results: list[CompanyMonthResult] = []
    for cid in ids:
        recs = by_company.get(cid, [])
        clist = cohorts_by_company.get(cid, [])
        j = 0  # puntero de cohortes ya disponibles (available_at <= fin de m)
        for m in months:
            month_end = _month_end(m)
            while j < len(clist) and clist[j].available_at <= month_end:
                j += 1
            activos = clist[:j]
            pay = _bucketize([r for r in recs if r.flow_side == "outflow"], month_end)
            col = _bucketize([r for r in recs if r.flow_side == "inflow"], month_end)

            n_cancel = sum(
                1
                for r in recs
                if r.status == "cancel"
                and r.due_date is not None
                and r.due_date <= month_end
            )
            n_due_desconocido = sum(
                1
                for r in recs
                if r.status != "cancel"
                and r.due_date is None
                and r.flow_side in ("inflow", "outflow")
            )

            tiene_cartera = pay.n_en_cartera > 0 or col.n_en_cartera > 0
            huecos = pay.n_huecos_eur + col.n_huecos_eur
            cartera_n = pay.n_en_cartera + col.n_en_cartera

            if not tiene_cartera:
                confidence = "ninguna"
            elif huecos == 0:
                confidence = "alta"
            elif huecos / cartera_n >= 0.5:
                confidence = "baja"
            else:
                confidence = "media"

            if not tiene_cartera:
                reason = "sin_cartera_observada"
            elif pay.n_en_cartera > 0 and pay.mora_ratio_min is None:
                reason = "sin_importe_conocido_en_el_corte"  # debido = 0
            elif huecos > 0:
                reason = "agujeros_fx_o_eur_en_el_corte"
            else:
                reason = None

            # cohortes: la mas reciente disponible (available_at <= fin de m);
            # consumir antes de available_at seria fuga de informacion
            flags: list[str] = []
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
                    debido_eur=pay.debido_eur,
                    pagado_a_tiempo_eur=pay.pagado_a_tiempo_eur,
                    pagado_tarde_eur=pay.pagado_tarde_eur,
                    en_mora_en_el_corte_eur=pay.en_mora_en_el_corte_eur,
                    mora_ratio=pay.mora_ratio,
                    mora_ratio_min=pay.mora_ratio_min,
                    mora_ratio_max=pay.mora_ratio_max,
                    retraso_medio_dias_pagado=pay.retraso_medio_dias_pagado,
                    cobro_debido_eur=col.debido_eur,
                    cobro_pagado_a_tiempo_eur=col.pagado_a_tiempo_eur,
                    cobro_pagado_tarde_eur=col.pagado_tarde_eur,
                    cobro_en_mora_en_el_corte_eur=col.en_mora_en_el_corte_eur,
                    cobro_mora_ratio=col.mora_ratio,
                    cobro_mora_ratio_min=col.mora_ratio_min,
                    cobro_mora_ratio_max=col.mora_ratio_max,
                    cobro_retraso_medio_dias_pagado=col.retraso_medio_dias_pagado,
                    coh_pct_cobrado_30d=coh_vals[30],
                    coh_30d_available_at=coh_av[30],
                    coh_pct_cobrado_60d=coh_vals[60],
                    coh_60d_available_at=coh_av[60],
                    coh_pct_cobrado_90d=coh_vals[90],
                    coh_90d_available_at=coh_av[90],
                    confidence=confidence,
                    reason=reason,
                    n_cancel=n_cancel,
                    n_vencimiento_desconocido=n_due_desconocido,
                    n_huecos_eur=huecos,
                    flags=tuple(dict.fromkeys(flags)),
                )
            )
    return results


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo))


def _assert_no_nan(results: list[CompanyMonthResult]) -> None:
    """Serializacion estricta: nan/inf prohibidos (test 10)."""
    for r in results:
        for key, val in r.to_row().items():
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"NaN/inf en {key} de {r.company_id}/{r.month}")


def _load_cohorts(con) -> list[CohortRecord]:
    """Lee SOLO las columnas de cohorte del panel (nunca stock retrospectivo)."""
    panel = paths.MARTS_DIR / COHORT_PANEL
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
        for h, (pct, av) in zip(COHORT_HORIZONS, ((r[2], r[3]), (r[4], r[5]), (r[6], r[7]))):
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


def _write_coverage(
    results: list[CompanyMonthResult],
    n_months: int,
    n_empresas_grid: int,
    universo: dict | None = None,
) -> dict:
    """coverage.json: alcance real de la senal + limitacion residual P5."""
    n_rows = len(results)
    calc = [r for r in results if r.mora_ratio is not None]
    con_calc = [r for r in results if r.cobro_mora_ratio is not None]
    sin_cartera = [r for r in results if r.reason == "sin_cartera_observada"]
    empresas_con_cartera = {r.company_id for r in results if r.reason is not None and r.reason != "sin_cartera_observada"} | {
        r.company_id for r in calc
    }
    conf: dict[str, int] = {"alta": 0, "media": 0, "baja": 0, "ninguna": 0}
    conf_calc: dict[str, int] = {"alta": 0, "media": 0, "baja": 0, "ninguna": 0}
    for r in results:
        conf[r.confidence] = conf.get(r.confidence, 0) + 1
    for r in calc:
        conf_calc[r.confidence] = conf_calc.get(r.confidence, 0) + 1
    moras = sorted(r.mora_ratio for r in calc)
    cobros = sorted(r.cobro_mora_ratio for r in con_calc)
    retrasos = sorted(
        r.retraso_medio_dias_pagado
        for r in results
        if r.retraso_medio_dias_pagado is not None
    )
    return {
        "grano": "(company_id, month), meses cerrados",
        "filas": n_rows,
        "meses": n_months,
        "empresas_grid": n_empresas_grid,
        "mora_ratio_calculable": {
            "empresa_mes": len(calc),
            "empresas": len({r.company_id for r in calc}),
            "pct_sobre_filas": round(len(calc) / n_rows, 4) if n_rows else None,
            "confidence_reparto_en_calculables": conf_calc,
            "nota": "esta cifra decide el alcance de la penalizacion v3",
        },
        "cobro_mora_ratio_calculable": {
            "empresa_mes": len(con_calc),
            "empresas": len({r.company_id for r in con_calc}),
        },
        "confidence_reparto": conf,
        "criterio_confidence": (
            "ninguna=sin cartera observable; alta=cartera sin agujeros; "
            "media=agujeros en <50% de la cartera; baja=en >=50%"
        ),
        "distribucion_mora_ratio": {
            "n": len(moras),
            "mediana": _quantile(moras, 0.5),
            "p75": _quantile(moras, 0.75),
            "p90": _quantile(moras, 0.9),
        },
        "distribucion_cobro_mora_ratio": {
            "n": len(cobros),
            "mediana": _quantile(cobros, 0.5),
            "p75": _quantile(cobros, 0.75),
            "p90": _quantile(cobros, 0.9),
        },
        "distribucion_retraso_medio_dias_pagado": {
            "n": len(retrasos),
            "mediana": _quantile(retrasos, 0.5),
            "p75": _quantile(retrasos, 0.75),
            "p90": _quantile(retrasos, 0.9),
        },
        "sin_cartera_observada": {
            "empresa_mes": len(sin_cartera),
            "empresas_nunca_con_cartera": len(
                {r.company_id for r in sin_cartera} - empresas_con_cartera
            ),
            "empresas_con_cartera_alguna_vez": len(empresas_con_cartera),
            "nota": "NULL, no significa que pague puntual",
        },
        "agujeros_fx_eur": {
            "empresa_mes_con_agujeros": sum(1 for r in results if r.n_huecos_eur > 0),
            "nota": "fx_ambiguous o amount_eur nulo: ni cero ni imputacion; "
            "se publica [mora_ratio_min, mora_ratio_max] y mora_ratio NULL",
        },
        "cancel_y_vencimiento_desconocido": {
            "cancel_empresa_mes_suma": sum(r.n_cancel for r in results),
            "vencimiento_desconocido_empresa_mes_suma": sum(
                r.n_vencimiento_desconocido for r in results
            ),
            "nota": "sumas acumuladas sobre cortes: una factura cuenta en "
            "todos los cortes desde su vencimiento (point-in-time)",
        },
        "anomalia_panel_cobro": {
            "reportado_en_mart": {
                "ar_pct_vencido_mediana": 0.839,
                "ar_retraso_medio_dias_mediana": 0.0,
                "incoherencia": "pct_vencido altisimo con retraso medio 0",
            },
            "medido_aqui": {
                "mora_ratio_pago_mediana": _quantile(moras, 0.5),
                "mora_ratio_cobro_mediana": _quantile(cobros, 0.5),
                "retraso_medio_dias_pagado_mediana": _quantile(retrasos, 0.5),
            },
            "lectura": "Esta capa se construye desde clean/invoices.parquet "
            "con disciplina de corte; NO hereda ni ajusta al mart. La mora "
            "pago mediana no reproduce el 0,839 del mart: el pct_vencido del "
            "panel es stock reconstruido (vencido sobre ABIERTO, con pagos "
            "posteriores al corte ya descartados de forma retrospectiva), no "
            "una medida point-in-time comparable.",
        },
        "limitacion_residual": LIMITACION_RESIDUAL,
        "fuente": "clean/invoices.parquet (document_type_norm='invoice')",
        "no_consumido": (
            "marts/panel_cobro: columnas de stock reconstruidas hacia atras "
            "(fuga); solo cohortes coh_pct_cobrado_Nd con "
            "coh_Nd_available_at <= fin de corte"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: escribe en ruta nueva bajo reports/ y ABORTA si ya existe."""
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = Path(args[0]) if args else DEFAULT_OUTPUT_DIR
    if out_dir.exists():
        print(
            f"ABORTADO: la ruta de salida {out_dir} ya existe. "
            "Indica otra ruta para no destruir ejecuciones previas.",
            file=sys.stderr,
        )
        return 2

    import duckdb

    con = duckdb.connect(":memory:")
    inv_path = paths.CLEAN_DIR / "invoices.parquet"
    rows = con.execute(
        f"""
        SELECT company_id, flow_side, status_norm, due_date_ok,
               payment_date_ok, amount_eur, fx_ambiguous
        FROM read_parquet('{inv_path}')
        WHERE document_type_norm = '{DOC_INVOICE}'
          AND flow_side IN ('inflow', 'outflow')
        """
    ).fetchall()
    invoices = [
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
    companies = [
        r[0]
        for r in con.execute(
            f"SELECT company_id FROM read_parquet('{paths.CLEAN_DIR / 'companies.parquet'}') "
            "ORDER BY company_id"
        ).fetchall()
    ]
    cohorts = _load_cohorts(con)
    con.close()

    months = closed_months()
    results = compute_payment_delay(invoices, months, cohorts, companies)
    _assert_no_nan(results)

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in results])
    frame["month"] = pd.to_datetime(frame["month"])
    for c in ("coh_30d_available_at", "coh_60d_available_at", "coh_90d_available_at"):
        frame[c] = pd.to_datetime(frame[c])
    out_dir.mkdir(parents=True, exist_ok=False)
    frame.to_parquet(out_dir / "payment_delay_monthly.parquet", index=False)

    coverage = _write_coverage(results, len(months), len(companies))
    (out_dir / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False)
    )
    print(json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
