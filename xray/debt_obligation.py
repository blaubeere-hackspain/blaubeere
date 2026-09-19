"""debt_obligation — obligacion de deuda vencida y no atendida, point-in-time.

PROBLEMA QUE ABORDA
-------------------
En healthscore_v3 una empresa que NO paga sus deudas aparece como una empresa
sin gastos (P y D son flujos REALIZADOS, no obligaciones exigibles) y por
tanto puntua alto. Esta capa MIDE la obligacion vencida y no atendida a fecha
de corte, sin implementar todavia el scorer v4. Produce dos magnitudes:

  1. `obligacion_no_atendida_eur`: importe en euros que entrara en el
     denominador del score.
  2. `multiplicador_deuda` en (0, 1]: penalizacion creciente con la severidad
     (importe y antiguedad) del impago. Sin obligacion vencida es 1.0.

GRANO Y DISCIPLINA POINT-IN-TIME (corte = fin de mes cerrado m)
---------------------------------------------------------------
Grano (company_id, month) sobre los meses cerrados, misma rejilla que
reports/payment_delay/ (todas las empresas de clean/companies.parquet x los
meses de xray.period). Para cada corte m NADA depende de informacion posterior
a fin de m:

1. OBLIGACION VENCIDA POR FACTURA (clean/invoices.parquet)
   Universo: document_type_norm='invoice', flow_side='outflow',
   due_date_ok <= fin de m, status_norm <> 'cancel'. Una factura es "pagada al
   corte" solo si status_norm='paid' y payment_date_ok <= fin de m; en
   cualquier otro status payment_date_ok es una fecha PREVISTA que se trata
   como NULL (regla heredada de xray/payment_delay.py y xray/marts/cobro.py).
   Por tanto una factura pagada DESPUES del corte cuenta como vencida EN m.
   Exposicion = abs(amount_eur), repartida en buckets por dias desde
   due_date_ok: 1-30, 31-60, 61-90, 91-180, 180+ (el dia 0, vencimiento el
   ultimo dia del mes y aun impagado, cae en el primer bucket). Si amount_eur
   es nulo/no finito o fx_ambiguous la factura NO se imputa como cero: sube
   `n_sin_eur` y degrada la confianza; los buckets son entonces una COTA
   INFERIOR (nunca un cero inventado).
   Auditoria: coverage.json -> `criterio_impagada` compara este criterio
   status-aware con la lectura LITERAL de payment_date_ok (que daba por
   pagadas las facturas vencidas con payment_date_ok = due_date_ok) y publica
   ambas cifras y el reparto por status_norm.

2. DEFICIT DE SERVICIO DE DEUDA (marts/panel_deuda.parquet)
   `servicio_esperado(m)` = mediana de `servicio_deuda_eur` sobre los meses
   ESTRICTAMENTE ANTERIORES a m de la PROPIA empresa con servicio > 0.
   Requiere >= MIN_MESES_SERVICIO (=3) meses previos; con menos es NULL y el
   deficit NULL (no cero). `servicio_observado(m)` = servicio_deuda_eur en m.
   `deficit_servicio_eur(m)` = max(0, esperado - observado). Si el observado
   es NULL (sin actividad de deuda en el panel) el deficit es NULL: no se
   imputa un cero. Nunca se miran meses posteriores ni otras empresas.

3. AGREGADOS
   `obligacion_no_atendida_eur` = suma de los buckets + deficit_servicio_eur;
   los NULL se EXCLUYEN de la suma (nunca se tratan como cero). Si no hay
   ninguna componente medible, es NULL.

MULTIPLICADOR (DISENO PROVISIONAL, PENDIENTE DE CALIBRACION)
------------------------------------------------------------
`severidad = sum_b PESO_b * vencido_b_eur`, con pesos CRECIENTES por
antiguedad (un euro impagado hace medio ano es una senal de distress mas
fuerte que uno de hace una semana):

    PESO_1_30 = 1.0   PESO_31_60 = 1.5   PESO_61_90 = 2.0
    PESO_91_180 = 3.0 PESO_180_MAS = 5.0

Normalizacion contra una magnitud de la PROPIA empresa: se usa T6 (suma de
pagos operativos conocidos + servicio de deuda en la ventana movil de 6 meses
hasta m inclusive), el mismo orden de magnitud que usa el scorer v3. Se elige
T6 y no `servicio_esperado` porque este ultimo solo existe para empresas con
historial de deuda financiera (cobertura muy baja) y porque T6 expresa el
volumen de pagos propio demostrado, de modo que la presion es adimensional.
Si T6 no esta disponible (o es 0) se cae a NORMALIZER_MONTHS x
`servicio_esperado`; si tampoco existe, el multiplicador es NULL (desconocido,
no 1.0).

    presion        = severidad / magnitud
    multiplicador  = max(MULTIPLICADOR_MINIMO, 1 - presion)   si hay vencidas
                   = 1.0                                      si no hay vencidas
                   = NULL                                     si no hay magnitud

`MULTIPLICADOR_MINIMO = 0.5` es una COTA DE DISENO PROVISIONAL, pendiente de
calibracion medida: acota la penalizacion para que ningun impago, por grande y
antiguo que sea, lleve el multiplicador a 0; 0.5 deja entre 0 y 50% de castigo
segun severidad. NO es una cifra calibrada. Los pesos por bucket tambien son
provisionales. Monotono NO creciente: mas importe vencido o mas antiguedad
nunca reduce la severidad, y la normalizacion no depende de las facturas.

`multiplicador_deuda` es exactamente 1.0 cuando no hay ninguna factura vencida
impagada observable en el corte (incluido el caso sin cartera: sin evidencia
de impago no se inventa penalizacion; la confianza 'ninguna' lo declara).

CONFIDENCE (criterio declarado, reproducido en coverage.json)
-------------------------------------------------------------
  'ninguna': no hay facturas outflow exigibles en el corte (sin cartera
             observable); `obligacion_vencida_eur` NULL.
  'alta'   : hay cartera, sin agujeros de importe y con magnitud de
             normalizacion disponible.
  'media'  : hay cartera y agujeros de importe en < 50% de las vencidas.
  'baja'   : agujeros de importe en >= 50% de las vencidas, o hay vencidas
             pero no hay magnitud de normalizacion.

PROHIBICIONES DE METODO RESPETADAS
----------------------------------
No se usa interim/debt_products.parquet (stock sin fecha), ni
interim/debt_schedule_config.parquet, ni clean/balances.parquet (snapshot de
fecha unica) en NINGUNA cifra publicada, ni marts/targets_proxy.parquet ni
reports/label_review/. No se imputan nulos como cero.

LIMITACION RESIDUAL (honestidad obligatoria)
--------------------------------------------
La fuente no tiene fecha de importacion: no puede demostrarse que una factura
estuviera REGISTRADA en el sistema en el corte m, solo que su VENCIMIENTO era
anterior a m. Es point-in-time por vencimiento, no por disponibilidad de
registro certificada. Se declara tambien en coverage.json.

El motor (compute_debt_obligation) es PURO: sin IO, sin reloj, sin estado
mutable, no muta su entrada. El CLI (main) escribe bajo
reports/debt_obligation/ y ABORTA si la ruta de salida ya existe.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from xray import paths
from xray.period import FIRST_MONTH, LAST_MONTH

DOC_INVOICE = "invoice"
FLOW_SIDE_OUT = "outflow"
DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "debt_obligation"

# --- Constantes de diseno PROVISIONALES (pendientes de calibracion) -------
PESO_1_30 = 1.0
PESO_31_60 = 1.5
PESO_61_90 = 2.0
PESO_91_180 = 3.0
PESO_180_MAS = 5.0
BUCKET_WEIGHTS = (PESO_1_30, PESO_31_60, PESO_61_90, PESO_91_180, PESO_180_MAS)
BUCKET_COLUMNS = (
    "vencido_1_30_eur",
    "vencido_31_60_eur",
    "vencido_61_90_eur",
    "vencido_91_180_eur",
    "vencido_180_mas_eur",
)
MULTIPLICADOR_MINIMO = 0.5  # cota de diseno PROVISIONAL
NORMALIZER_MONTHS = 6       # ventana movil de T6 (misma que el scorer v3)
MIN_MESES_SERVICIO = 3      # meses previos con servicio > 0 para el esperado
CONFIDENCE_LEVELS = ("ninguna", "baja", "media", "alta")

CRITERIO_CONFIDENCE = (
    "ninguna=sin facturas outflow exigibles en el corte; alta=cartera sin "
    "agujeros de importe y con magnitud de normalizacion; media=agujeros en "
    "<50% de las vencidas; baja=agujeros en >=50% de las vencidas o sin "
    "magnitud de normalizacion"
)

LIMITACION_RESIDUAL = (
    "La fuente no tiene fecha de importacion: no puede demostrarse que una "
    "factura estuviera REGISTRADA en el sistema en el corte m, solo que su "
    "vencimiento era anterior a m. Medida point-in-time por vencimiento, no "
    "por disponibilidad de registro certificada."
)

JUSTIFICACION_CONSTANTES = {
    "pesos_bucket": {
        "1_30": PESO_1_30,
        "31_60": PESO_31_60,
        "61_90": PESO_61_90,
        "91_180": PESO_91_180,
        "180_mas": PESO_180_MAS,
        "justificacion": "pesos crecientes por antiguedad: un euro impagado "
        "hace mas tiempo es una senal de distress mas fuerte; provisionales",
    },
    "multiplicador_minimo": MULTIPLICADOR_MINIMO,
    "multiplicador_minimo_justificacion": "cota de diseno PROVISIONAL "
    "pendiente de calibracion: acota el castigo para que ningun impago lleve "
    "el multiplicador a 0; deja hasta un 50% de penalizacion",
    "normalizacion": "T6 = pagos operativos conocidos + servicio de deuda en "
    "la ventana movil de 6 meses hasta m (magnitud de la propia empresa, "
    "misma escala que el scorer v3); fallback 6x servicio_esperado",
    "min_meses_servicio": MIN_MESES_SERVICIO,
    "nota": "todas las constantes son PROVISIONALES y no calibradas",
}


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class PayableRecord:
    """Factura normalizada; el motor no ve el parquet."""
    company_id: str
    document_type: str
    flow_side: str
    status: str
    due_date: date | None
    payment_date: date | None  # fecha real de pago; solo fiable si status='paid'
    amount_eur: float | None
    fx_ambiguous: bool


@dataclass(frozen=True)
class FlowRecord:
    """Pagos operativos conocidos + servicio de deuda de un mes (paneles)."""
    company_id: str
    month: date
    pagos_operativos_eur: float | None
    servicio_deuda_eur: float | None
    available_at: date | None


@dataclass(frozen=True)
class DebtObligationRow:
    """Resultado (company_id, month); nulo nunca es cero."""
    company_id: str
    month: date
    # --- obligacion vencida por antiguedad (cotas inferiores si hay huecos) --
    vencido_1_30_eur: float = 0.0
    vencido_31_60_eur: float = 0.0
    vencido_61_90_eur: float = 0.0
    vencido_91_180_eur: float = 0.0
    vencido_180_mas_eur: float = 0.0
    obligacion_vencida_eur: float | None = None
    # --- deficit de servicio de deuda ---------------------------------------
    servicio_esperado_eur: float | None = None
    servicio_observado_eur: float | None = None
    deficit_servicio_eur: float | None = None
    # --- agregado y multiplicador -------------------------------------------
    obligacion_no_atendida_eur: float | None = None
    magnitud_normalizacion_eur: float | None = None
    fuente_normalizacion: str | None = None
    multiplicador_deuda: float | None = None
    # --- contadores y honestidad --------------------------------------------
    n_debido: int = 0
    n_vencidas: int = 0
    n_sin_eur: int = 0
    n_cancel: int = 0
    t6_meses_conocidos: int = 0
    confidence: str = "ninguna"
    reason: str | None = None
    flags: tuple[str, ...] = ()

    def to_row(self) -> dict:
        row = {
            "company_id": self.company_id,
            "month": self.month,
            "obligacion_vencida_eur": self.obligacion_vencida_eur,
            "servicio_esperado_eur": self.servicio_esperado_eur,
            "servicio_observado_eur": self.servicio_observado_eur,
            "deficit_servicio_eur": self.deficit_servicio_eur,
            "obligacion_no_atendida_eur": self.obligacion_no_atendida_eur,
            "magnitud_normalizacion_eur": self.magnitud_normalizacion_eur,
            "fuente_normalizacion": self.fuente_normalizacion,
            "multiplicador_deuda": self.multiplicador_deuda,
            "n_debido": self.n_debido,
            "n_vencidas": self.n_vencidas,
            "n_sin_eur": self.n_sin_eur,
            "n_cancel": self.n_cancel,
            "t6_meses_conocidos": self.t6_meses_conocidos,
            "confidence": self.confidence,
            "reason": self.reason,
            "flags": list(self.flags),
        }
        for column, value in zip(BUCKET_COLUMNS, self._buckets()):
            row[column] = value
        return row

    def _buckets(self) -> tuple[float, float, float, float, float]:
        return (
            self.vencido_1_30_eur,
            self.vencido_31_60_eur,
            self.vencido_61_90_eur,
            self.vencido_91_180_eur,
            self.vencido_180_mas_eur,
        )


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


def _month_index(d: date) -> int:
    return d.year * 12 + d.month


def _median(values: list[float]) -> float:
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def _bucket_index(days: int) -> int:
    """Bucket por dias desde el vencimiento; 0..4.

    1-30 -> 0, 31-60 -> 1, 61-90 -> 2, 91-179 -> 3, >=180 -> 4. El dia 0
    (vencimiento el ultimo dia del mes y aun impagado) cae en el bucket 0.
    """
    if days <= 30:
        return 0
    if days <= 60:
        return 1
    if days <= 90:
        return 2
    if days < 180:
        return 3
    return 4


def _fraccion(part: int, total: int) -> float:
    return (part / total) if total > 0 else 0.0


def _cut_from_invoices(
    records: list[PayableRecord], month_end: date
) -> tuple[list[float], int, int, int, int]:
    """Recorre la cartera outflow exigible en el corte.

    Devuelve (buckets, n_debido, n_vencidas, n_sin_eur, n_cancel). Solo
    'invoice'/'outflow' entran; 'cancel' se cuenta aparte; due_date nulo o
    posterior al corte queda fuera. Una factura 'paid' con pago > month_end
    sigue vencida EN el corte (point-in-time).
    """
    buckets = [0.0, 0.0, 0.0, 0.0, 0.0]
    n_debido = n_vencidas = n_sin_eur = n_cancel = 0
    for r in records:
        if r.document_type != DOC_INVOICE or r.flow_side != FLOW_SIDE_OUT:
            continue
        if r.due_date is None or r.due_date > month_end:
            continue  # no exigible en el corte
        if r.status == "cancel":
            n_cancel += 1
            continue  # la obligacion desaparece: no es mora
        n_debido += 1
        real_payment = r.payment_date if r.status == "paid" else None
        if real_payment is not None and real_payment <= month_end:
            continue  # pagada al corte
        n_vencidas += 1
        if (
            r.amount_eur is None
            or not math.isfinite(r.amount_eur)
            or r.fx_ambiguous
        ):
            n_sin_eur += 1  # agujero: ni cero ni imputacion
            continue
        buckets[_bucket_index((month_end - r.due_date).days)] += abs(r.amount_eur)
    return buckets, n_debido, n_vencidas, n_sin_eur, n_cancel


def _service_state(
    flows: list[FlowRecord], month: date, month_end: date
) -> tuple[float | None, float | None, float | None]:
    """(esperado, observado, deficit) de servicio de deuda en el corte.

    Solo meses <= m y con available_at <= fin de m. El esperado usa meses
    ESTRICTAMENTE anteriores a m (>= MIN_MESES_SERVICIO con servicio > 0).
    """
    observado: float | None = None
    previos_positivos: list[float] = []
    for f in flows:
        if f.month > month:
            continue  # nunca se mira el futuro
        if f.available_at is not None and f.available_at > month_end:
            continue  # aun no disponible en el corte
        if f.month == month:
            observado = f.servicio_deuda_eur
        if (
            f.month < month
            and f.servicio_deuda_eur is not None
            and f.servicio_deuda_eur > 0
        ):
            previos_positivos.append(f.servicio_deuda_eur)

    if len(previos_positivos) >= MIN_MESES_SERVICIO:
        esperado: float | None = _median(previos_positivos)
    else:
        esperado = None
    if esperado is None or observado is None:
        deficit: float | None = None
    else:
        deficit = max(0.0, esperado - observado)
    return esperado, observado, deficit


def _magnitud_normalizacion(
    flows: list[FlowRecord], month: date, month_end: date,
    servicio_esperado: float | None,
) -> tuple[float | None, str | None, int]:
    """T6 (o fallback 6x servicio_esperado) de la propia empresa."""
    ventana_inicio = _month_index(month) - (NORMALIZER_MONTHS - 1)
    t6 = 0.0
    t6_meses = 0
    for f in flows:
        if f.month > month:
            continue
        if f.available_at is not None and f.available_at > month_end:
            continue
        if _month_index(f.month) < ventana_inicio:
            continue
        conocido = False
        if f.pagos_operativos_eur is not None:
            t6 += f.pagos_operativos_eur
            conocido = True
        if f.servicio_deuda_eur is not None:
            t6 += f.servicio_deuda_eur
            conocido = True
        if conocido:
            t6_meses += 1
    if t6_meses > 0 and t6 > 0:
        return t6, "t6", t6_meses
    if servicio_esperado is not None and servicio_esperado > 0:
        return servicio_esperado * NORMALIZER_MONTHS, "servicio_esperado_x6", t6_meses
    if t6_meses > 0:
        return 0.0, "t6_cero", t6_meses
    return None, None, t6_meses


def _multiplicador(
    buckets: list[float], magnitud: float | None, n_vencidas: int,
    obligacion_vencida: float | None,
) -> float | None:
    """Multiplicador en [MULTIPLICADOR_MINIMO, 1], monotono no creciente."""
    if n_vencidas == 0 or obligacion_vencida == 0.0:
        return 1.0  # sin obligacion vencida observable
    if obligacion_vencida is None or magnitud is None:
        return None  # hay vencidas pero nada medible/normalizable
    severidad = sum(w * b for w, b in zip(BUCKET_WEIGHTS, buckets))
    if severidad <= 0:
        return 1.0
    if magnitud <= 0:
        return MULTIPLICADOR_MINIMO  # sin pagos propios: presion maxima
    return max(MULTIPLICADOR_MINIMO, 1.0 - severidad / magnitud)


def compute_debt_obligation(
    invoices: list[PayableRecord],
    flows: list[FlowRecord],
    months: list[date],
    companies: list[str] | None = None,
) -> list[DebtObligationRow]:
    """Motor PURO: obligacion de deuda vencida por (company_id, mes cerrado).

    - invoices: facturas normalizadas; el motor filtra document_type='invoice',
      flow_side='outflow', due<=fin de m, status<>'cancel' y el impago.
    - flows: filas (company_id, month) de pagos operativos + servicio de deuda
      de los paneles; el motor solo usa months <= m con available_at<=fin m.
    - months: primeros de mes cerrado.
    - companies: rejilla completa (incluye empresas sin facturas).

    No muta la entrada; determinista; sin reloj ni IO.
    """
    by_company: dict[str, list[PayableRecord]] = {}
    for inv in invoices:
        by_company.setdefault(inv.company_id, []).append(inv)
    flows_by_company: dict[str, list[FlowRecord]] = {}
    seen: set[tuple[str, date]] = set()
    for f in flows:
        if (f.company_id, f.month) in seen:
            raise ValueError(f"flujo duplicado para {f.company_id}/{f.month}")
        seen.add((f.company_id, f.month))
        flows_by_company.setdefault(f.company_id, []).append(f)

    ids = (
        list(companies)
        if companies is not None
        else sorted(set(by_company) | set(flows_by_company))
    )
    results: list[DebtObligationRow] = []
    for cid in ids:
        recs = by_company.get(cid, [])
        frecs = flows_by_company.get(cid, [])
        for m in months:
            month_end = _month_end(m)
            buckets, n_debido, n_vencidas, n_sin_eur, n_cancel = _cut_from_invoices(
                recs, month_end
            )
            esperado, observado, deficit = _service_state(frecs, m, month_end)
            magnitud, fuente, t6_meses = _magnitud_normalizacion(
                frecs, m, month_end, esperado
            )

            if n_vencidas == 0:
                obligacion_vencida: float | None = 0.0
            elif n_sin_eur >= n_vencidas:
                obligacion_vencida = None  # todas sin importe: nada medible
            else:
                obligacion_vencida = sum(buckets)

            partes = [v for v in (obligacion_vencida, deficit) if v is not None]
            obligacion_no_atendida = sum(partes) if partes else None
            multiplicador = _multiplicador(
                buckets, magnitud, n_vencidas, obligacion_vencida
            )

            flags: list[str] = []
            if n_debido == 0:
                confidence = "ninguna"
                reason = "sin_cartera_observada"
            elif n_sin_eur > 0:
                reason = "importes_desconocidos_en_el_corte"
                confidence = (
                    "baja" if _fraccion(n_sin_eur, n_vencidas) >= 0.5 else "media"
                )
            elif n_vencidas > 0 and magnitud is None:
                confidence = "baja"
                reason = "sin_magnitud_normalizacion"
            else:
                confidence = "alta"
                reason = None
            if n_vencidas > 0 and deficit is None:
                flags.append("deficit_servicio_no_calculable")
            if fuente == "servicio_esperado_x6":
                flags.append("normalizacion_fallback_servicio_esperado")
            if t6_meses < NORMALIZER_MONTHS and n_vencidas > 0:
                flags.append("ventana_t6_parcial")

            results.append(
                DebtObligationRow(
                    company_id=cid,
                    month=m,
                    vencido_1_30_eur=buckets[0],
                    vencido_31_60_eur=buckets[1],
                    vencido_61_90_eur=buckets[2],
                    vencido_91_180_eur=buckets[3],
                    vencido_180_mas_eur=buckets[4],
                    obligacion_vencida_eur=obligacion_vencida,
                    servicio_esperado_eur=esperado,
                    servicio_observado_eur=observado,
                    deficit_servicio_eur=deficit,
                    obligacion_no_atendida_eur=obligacion_no_atendida,
                    magnitud_normalizacion_eur=magnitud,
                    fuente_normalizacion=fuente,
                    multiplicador_deuda=multiplicador,
                    n_debido=n_debido,
                    n_vencidas=n_vencidas,
                    n_sin_eur=n_sin_eur,
                    n_cancel=n_cancel,
                    t6_meses_conocidos=t6_meses,
                    confidence=confidence,
                    reason=reason,
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


def _assert_no_nan(results: list[DebtObligationRow]) -> None:
    """Serializacion estricta: nan/inf prohibidos."""
    for r in results:
        for key, val in r.to_row().items():
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError(f"NaN/inf en {key} de {r.company_id}/{r.month}")


def _criterio_impagada(
    invoices: list[PayableRecord], month_end: date
) -> dict:
    """Auditoria del criterio de impago en el corte de referencia.

    Compara el criterio vigente status-aware con la lectura LITERAL de
    payment_date_ok y reparte el universo por status_norm. El criterio
    literal daba por pagadas las facturas status<>'paid' cuyo
    payment_date_ok es en realidad fecha prevista, reproduciendo el defecto.
    """
    literal = {"facturas": 0, "empresas": set(), "eur": 0.0}
    vigente = {"facturas": 0, "empresas": set(), "eur": 0.0}
    reparto: dict[str, dict] = {}
    for r in invoices:
        if r.document_type != DOC_INVOICE or r.flow_side != FLOW_SIDE_OUT:
            continue
        if r.due_date is None or r.due_date > month_end:
            continue
        if r.status == "cancel":
            continue
        eur = (
            abs(r.amount_eur)
            if r.amount_eur is not None and math.isfinite(r.amount_eur)
            else 0.0
        )
        if r.payment_date is None or r.payment_date > month_end:
            literal["facturas"] += 1
            literal["empresas"].add(r.company_id)
            literal["eur"] += eur
        paid_at_cut = (
            r.status == "paid"
            and r.payment_date is not None
            and r.payment_date <= month_end
        )
        if not paid_at_cut:
            vigente["facturas"] += 1
            vigente["empresas"].add(r.company_id)
            vigente["eur"] += eur
            slot = reparto.setdefault(
                r.status, {"facturas": 0, "empresas": set(), "eur": 0.0}
            )
            slot["facturas"] += 1
            slot["empresas"].add(r.company_id)
            slot["eur"] += eur
    return {
        "corte_referencia": month_end.isoformat(),
        "criterio_vigente": "impagada al corte = NOT(status_norm='paid' AND "
        "payment_date_ok IS NOT NULL AND payment_date_ok <= fin de m)",
        "criterio_literal_rechazado": "impagada = payment_date_ok IS NULL OR "
        "payment_date_ok > fin de m (sin mirar status_norm)",
        "motivo": "payment_date_ok solo es fecha REAL de pago si "
        "status_norm='paid'; en cualquier otro status es fecha PREVISTA. El "
        "criterio literal daba por pagadas las facturas vencidas con "
        "payment_date_ok = due_date_ok y reproducia el defecto que esta capa "
        "corrige (empresa que no paga leida como si pagase).",
        "status_aware": {
            "facturas": vigente["facturas"],
            "empresas": len(vigente["empresas"]),
            "eur": round(vigente["eur"], 2),
        },
        "literal": {
            "facturas": literal["facturas"],
            "empresas": len(literal["empresas"]),
            "eur": round(literal["eur"], 2),
        },
        "reparto_status_norm_vigente": {
            status: {
                "facturas": slot["facturas"],
                "empresas": len(slot["empresas"]),
                "eur": round(slot["eur"], 2),
            }
            for status, slot in sorted(reparto.items())
        },
    }


def _resolve_dirs(workspace: Path | None) -> tuple[Path, Path]:
    """Directorio clean y marts: por defecto los de la ejecucion activa.

    `--workspace` admite tanto un directorio que contiene clean/ y marts/
    (marts/panel_deuda.parquet) como el run root que contiene data/clean.
    """
    if workspace is None:
        return paths.CLEAN_DIR, paths.MARTS_DIR
    w = Path(workspace)
    if (w / "clean" / "invoices.parquet").exists():
        return w / "clean", w / "marts"
    if (w / "data" / "clean" / "invoices.parquet").exists():
        return w / "data" / "clean", w / "data" / "marts"
    raise SystemExit(f"--workspace no contiene clean/ ni data/clean/: {w}")


def _load_inputs(clean_dir: Path, marts_dir: Path, con):
    rows = con.execute(
        f"""
        SELECT company_id, document_type_norm, flow_side, status_norm,
               due_date_ok, payment_date_ok, amount_eur, fx_ambiguous
        FROM read_parquet('{clean_dir / "invoices.parquet"}')
        WHERE document_type_norm = '{DOC_INVOICE}'
          AND flow_side = '{FLOW_SIDE_OUT}'
        """
    ).fetchall()
    invoices = [
        PayableRecord(
            company_id=r[0],
            document_type=r[1],
            flow_side=r[2],
            status=r[3],
            due_date=r[4].date() if r[4] is not None else None,
            payment_date=r[5].date() if r[5] is not None else None,
            amount_eur=r[6],
            fx_ambiguous=bool(r[7]),
        )
        for r in rows
    ]
    flow_rows = con.execute(
        f"""
        SELECT coalesce(f.company_id, d.company_id),
               coalesce(f.month, d.month),
               f.pagos_operativos_conocido_eur,
               d.servicio_deuda_eur,
               coalesce(f.available_at, d.available_at)
        FROM read_parquet('{marts_dir / "panel_flujos.parquet"}') f
        FULL OUTER JOIN read_parquet('{marts_dir / "panel_deuda.parquet"}') d
          ON f.company_id = d.company_id AND f.month = d.month
        """
    ).fetchall()
    flows = [
        FlowRecord(
            company_id=r[0],
            month=r[1],
            pagos_operativos_eur=r[2],
            servicio_deuda_eur=r[3],
            available_at=r[4],
        )
        for r in flow_rows
    ]
    companies = [
        r[0]
        for r in con.execute(
            f"SELECT company_id FROM read_parquet('{clean_dir / 'companies.parquet'}') "
            "ORDER BY company_id"
        ).fetchall()
    ]
    return invoices, flows, companies


def _build_coverage(results: list[DebtObligationRow], n_months: int,
                    n_empresas_grid: int, criterio_impagada: dict | None = None) -> dict:
    n_rows = len(results)
    con_vencida = [r for r in results if r.n_vencidas > 0]
    calculables = [r for r in results if r.obligacion_no_atendida_eur is not None]
    conf: dict[str, int] = {level: 0 for level in CONFIDENCE_LEVELS}
    for r in results:
        conf[r.confidence] += 1
    obligaciones = sorted(r.obligacion_no_atendida_eur for r in calculables)
    multiplicadores = sorted(
        r.multiplicador_deuda
        for r in results
        if r.multiplicador_deuda is not None
    )
    con_deficit = [r for r in results if r.deficit_servicio_eur is not None
                   and r.deficit_servicio_eur > 0]
    return {
        "grano": "(company_id, month), meses cerrados",
        "filas": n_rows,
        "meses": n_months,
        "empresas_grid": n_empresas_grid,
        "obligacion_vencida": {
            "empresa_mes": len(con_vencida),
            "empresas": len({r.company_id for r in con_vencida}),
            "empresas_alguna_vez": len({r.company_id for r in con_vencida}),
            "pct_sobre_filas": round(len(con_vencida) / n_rows, 4) if n_rows else None,
        },
        "criterio_impagada": criterio_impagada,
        "obligacion_no_atendida_calculable": {
            "empresa_mes": len(calculables),
            "empresas": len({r.company_id for r in calculables}),
        },
        "confidence_reparto": conf,
        "criterio_confidence": CRITERIO_CONFIDENCE,
        "distribucion_obligacion_no_atendida_eur": {
            "n": len(obligaciones),
            "mediana": _quantile(obligaciones, 0.5),
            "p75": _quantile(obligaciones, 0.75),
            "p90": _quantile(obligaciones, 0.9),
        },
        "distribucion_multiplicador_deuda": {
            "n": len(multiplicadores),
            "mediana": _quantile(multiplicadores, 0.5),
            "p75": _quantile(multiplicadores, 0.75),
            "p90": _quantile(multiplicadores, 0.9),
            "min": multiplicadores[0] if multiplicadores else None,
            "max": multiplicadores[-1] if multiplicadores else None,
        },
        "deficit_servicio": {
            "empresa_mes_con_deficit_positivo": len(con_deficit),
            "empresas_con_deficit_positivo": len({r.company_id for r in con_deficit}),
            "empresa_mes_calculable": sum(
                1 for r in results if r.deficit_servicio_eur is not None
            ),
            "nota": "deficit>0 = venia pagando servicio de deuda y dejo de "
            "hacerlo (o lo redujo) respecto a su mediana previa",
        },
        "agujeros_importe": {
            "empresa_mes_con_n_sin_eur": sum(
                1 for r in results if r.n_sin_eur > 0
            ),
            "nota": "fx_ambiguous o amount_eur nulo: no se imputan como cero; "
            "los buckets son cotas inferiores y la confianza baja",
        },
        "constantes_diseno": JUSTIFICACION_CONSTANTES,
        "limitacion_residual": LIMITACION_RESIDUAL,
        "fuente": "clean/invoices.parquet (invoice/outflow) + "
        "marts/panel_deuda.parquet + marts/panel_flujos.parquet",
        "no_consumido": (
            "interim/debt_products.parquet (stock sin fecha), "
            "interim/debt_schedule_config.parquet, clean/balances.parquet "
            "(snapshot de fecha unica), marts/targets_proxy.parquet y "
            "reports/label_review/: NO entran en ninguna cifra publicada"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: escribe en ruta nueva bajo reports/ y ABORTA si ya existe."""
    parser = argparse.ArgumentParser(
        description="Obligacion de deuda vencida point-in-time (solo medicion)"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Ruta nueva; aborta si ya existe",
    )
    parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Workspace con clean/ y marts/ (por defecto la ejecucion activa)",
    )
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        print(
            f"ABORTADO: la ruta de salida {args.output_dir} ya existe. "
            "Indica otra ruta para no destruir ejecuciones previas.",
            file=sys.stderr,
        )
        return 2

    import duckdb
    import pandas as pd

    clean_dir, marts_dir = _resolve_dirs(args.workspace)
    con = duckdb.connect(":memory:")
    try:
        invoices, flows, companies = _load_inputs(clean_dir, marts_dir, con)
    finally:
        con.close()

    months = closed_months()
    results = compute_debt_obligation(invoices, flows, months, companies)
    _assert_no_nan(results)

    frame = pd.DataFrame([r.to_row() for r in results])
    frame["month"] = pd.to_datetime(frame["month"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    frame.to_parquet(
        args.output_dir / "debt_obligation_monthly.parquet", index=False
    )

    coverage = _build_coverage(
        results, len(months), len(companies),
        _criterio_impagada(invoices, _month_end(LAST_MONTH)),
    )
    (args.output_dir / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False)
    )
    print(json.dumps(coverage, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
