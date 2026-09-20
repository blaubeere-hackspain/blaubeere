"""fx_exposure — exposicion en divisa de la cartera viva de COBROS (empresa-mes).

QUE ES ESTA CAPA (tarea F2 del spec docs/SPEC_FX_RISK_INDEX.md)
---------------------------------------------------------------
Construye el LADO EMPRESA del factor de riesgo de divisa: que parte de lo que
a cada empresa le deben esta en cada divisa, mes a mes, con disciplina
point-in-time. NO calcula inestabilidad de tipos de cambio, NO toca tipos de
cambio, NO une con el indice FX (eso es F3) y NO modifica el score.

DECISIONES DE PRODUCTO APLICADAS
--------------------------------
1. SOLO COBROS: ``flow_side = 'inflow'`` (dinero que nos deben). Los pagos
   quedan fuera.
2. Cartera viva de cobros: ``document_type_norm = 'invoice'``, sin
   ``status_norm = 'cancel'``. El STATUS SE EVALUA POINT-IN-TIME (ver abajo),
   porque ``status_norm`` es el estado TERMINAL del dataset (2026-09-01), no
   el estado en el corte m.
3. Ponderacion por DINERO, no por numero de facturas: los agregados de
   exposicion son monetarios; el numero de facturas se publica como conteo
   auxiliar.
4. Nulo nunca es cero: lo que no se puede valorar en EUR se publica como
   magnitud APARTE (conteo + divisas), jamas se imputa ni se suma como cero.

DISCIPLINA POINT-IN-TIME (el punto mas importante)
--------------------------------------------------
``status_norm`` es TERMINAL: una factura ``paid`` lo es al final del dataset,
no en todos los cortes. Por eso la cartera viva al cierre de m se reconstruye:

    viva(m) = emitida hasta el cierre de m (issuance_date_ok <= fin de m)
              Y NOT (status_norm = 'paid' AND payment_date_ok <= fin de m)

Una factura pagada DESPUES del corte sigue VIVA en el corte. Una factura
emitida despues del corte NO existe en el corte. Estas dos reglas son el test
de no-fuga.

OJO CON EL DEFECTO YA COMETIDO EN ESTE REPO: las facturas ``overdue`` llevan
``payment_date_ok = due_date_ok``, que es la fecha PREVISTA de pago, no la
real. Un criterio LITERAL (``impagada = payment_date_ok > fin de m``) daria por
pagadas las facturas vencidas y las haria desaparecer de la cartera. El
criterio vigente es STATUS-AWARE: ``payment_date_ok`` solo es fecha REAL si
``status_norm = 'paid'``; en cualquier otro estado se ignora. Se sigue la
convencion de ``xray/debt_obligation.py`` y ``xray/payment_delay_v2.py``.

DIVERGENCIA DECLARADA CON debt_obligation.py
--------------------------------------------
``debt_obligation`` filtra ``due_date_ok <= fin de m`` porque mide OBLIGACION
VENCIDA. Esta capa mide la CARTERA VIVA DE COBROS, que incluye lo emitido y
aun no vencido, por lo que su formula (dada en el dispatch) filtra por
``issuance_date_ok <= fin de m``. El criterio de impago status-aware es
IDENTICO. El subconjunto "emitido y aun no vencido" se mide en el censo para
que la diferencia sea auditable (la constante ``FILTRO_EMISION`` controla la
eleccion; con ``False`` se replica exactamente el filtro por vencimiento de
debt_obligation).

IMPORTE VIVO: ``amount``, NO ``pending_amount`` (hallazgo medido)
-----------------------------------------------------------------
``pending_amount`` es el saldo TERMINAL del snapshot (= 0 en cuanto la factura
esta pagada), no el saldo en el corte. Medido en la fuente: de las 7.016
facturas terminal-``paid`` que estan vivas en el ultimo corte, las 7.016
tienen ``pending_amount = 0``. Usarlo para reconstruir cortes pasados pondria a
CERO la exposicion de todo lo que se cobro despues del corte: fuga pura. El
unico importe PIT-safe es ``amount`` (nominal emitido, atributo fijo conocido
en la emision). Es una COTA SUPERIOR de lo que queda por cobrar cuando hubo
cobro parcial, y asi se declara.

La valoracion en EUR usa ``amount_eur`` tal cual (derivada de ``amount`` por la
propia factura: identidad si la divisa es EUR, conversion reportada si no).
NUNCA se estima un tipo: ni medianas internas de ``exchange_rate`` (prohibido
por el spec, punto 8) ni ninguna otra imputacion. ``fx_ambiguous`` o
``amount_eur`` nulo/no finito => la factura es NO VALORABLE y se cuenta aparte.

GRANO Y SALIDA
--------------
Grano (company_id, month) sobre los 24 meses cerrados de ``xray.period``. Cada
fila lleva la composicion por divisa en la columna JSON ``por_divisa`` (lista
de objetos, uno por ``currency_norm``) y los agregados por empresa-mes.

CONFIDENCE (criterio declarado, reproducido en el censo)
--------------------------------------------------------
  'ninguna': no hay cartera viva de cobros observable en el corte.
  'baja'   : facturas no valorables >= 50% de la cartera viva (en numero).
  'media'  : hay algun hueco de valoracion, o la cartera valorable es fina
             (< MIN_FACTURAS_CONFIANZA_ALTA facturas o
             < MIN_EXPOSICION_CONFIANZA_ALTA EUR), o hay divisa desconocida.
  'alta'   : sin huecos, cartera gruesa y sin divisa desconocida.

MOTIVOS: lista legible ``reasons`` al estilo de ``reports/score_v4`` y del
resto de capas; ``flags`` para hechos estructurales de la fila.

El motor (``compute_fx_exposure``) es PURO: sin IO, sin reloj, sin estado
mutable, no muta su entrada y solo consume fechas <= cierre de m. El CLI
escribe los tres artefactos de F2 bajo ``reports/fx_risk/`` (directorio
COMPARTIDO con F1: solo aborta si SUS ficheros ya existen).
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
from xray.period import FIRST_MONTH, LAST_MONTH

__all__ = [
    "CurrencyExposure",
    "CompanyMonthFx",
    "FxInvoiceRecord",
    "compute_fx_exposure",
    "closed_months",
    "build_census",
    "main",
]

DOC_INVOICE = "invoice"
FLOW_SIDE_IN = "inflow"
STATUS_CANCEL = "cancel"
STATUS_PAID = "paid"
MONEDA_DESCONOCIDA = (None, "unknown")
DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "fx_risk"

# Filtro temporal del universo: True = emitida hasta el cierre (formula del
# dispatch, cartera viva de cobros); False = vencida hasta el cierre (misma
# convencion que debt_obligation.py, solo obligacion vencida).
FILTRO_EMISION = True

# Confidence (mismos umbrales de cartera "gruesa" que payment_delay_v2).
MIN_FACTURAS_CONFIANZA_ALTA = 3
MIN_EXPOSICION_CONFIANZA_ALTA = 2000.0
CONFIDENCE_LEVELS = ("ninguna", "baja", "media", "alta")

CRITERIO_CONFIDENCE = (
    "ninguna=sin cartera viva de cobros en el corte; baja=facturas no "
    "valorables >=50% de la cartera (en numero); media=algun hueco de "
    "valoracion, o cartera valorable fina "
    f"(<{MIN_FACTURAS_CONFIANZA_ALTA} facturas o "
    f"<{MIN_EXPOSICION_CONFIANZA_ALTA:g} EUR), o divisa desconocida; "
    "alta=sin huecos y cartera gruesa"
)

CRITERIO_UNIVERSO = (
    "flow_side='inflow', document_type_norm='invoice', status_norm<>'cancel', "
    "issuance_date_ok <= fin de m (cartera viva de cobros: incluye lo aun no "
    "vencido) y NOT (status_norm='paid' AND payment_date_ok <= fin de m). "
    "payment_date_ok solo es fecha REAL si status_norm='paid'; en otro estado "
    "es fecha PREVISTA y se ignora (criterio status-aware)."
)

LIMITACION_RESIDUAL = (
    "La fuente no tiene fecha de importacion: no puede demostrarse que una "
    "factura estuviera REGISTRADA en el sistema en el corte m, solo que su "
    "emision era anterior a m. Medida point-in-time por emision, no por "
    "disponibilidad de registro certificada."
)

MOTIVO_AMOUNT = (
    "amount (nominal emitido) y NO pending_amount: pending_amount es el saldo "
    "TERMINAL del snapshot (=0 si la factura ya esta pagada) y usarlo en "
    "cortes pasados pondria a cero la exposicion de todo lo cobrado despues "
    "del corte (fuga). Medido: las 7.016 facturas terminal-paid vivas en el "
    "ultimo corte tienen pending_amount=0. amount es cota superior si hubo "
    "cobro parcial."
)


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class FxInvoiceRecord:
    """Factura normalizada de cobro; el motor no ve el parquet.

    ``amount`` es el nominal emitido (PIT-safe). ``amount_eur`` es la
    valoracion declarada por la propia factura (identidad si la divisa es EUR,
    conversion reportada si no); ``amount_eur_source`` la etiqueta.
    ``fx_ambiguous`` marca conversiones contradictorias (divisa <> contable con
    tipo 1): se tratan como no valorables.
    """

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


@dataclass(frozen=True)
class CurrencyExposure:
    """Composicion de una divisa dentro de la cartera viva de un empresa-mes."""

    currency: str | None
    n_facturas: int = 0
    importe_original: float = 0.0
    importe_eur: float = 0.0
    n_eur_identity: int = 0
    n_eur_reported: int = 0
    n_no_valorable: int = 0
    importe_no_valorable_original: float = 0.0
    n_fx_ambiguous: int = 0
    divisa_desconocida: bool = False

    def to_dict(self) -> dict:
        return {
            "divisa": self.currency,
            "divisa_desconocida": self.divisa_desconocida,
            "n_facturas": self.n_facturas,
            "importe_original": round(self.importe_original, 6),
            "importe_eur": round(self.importe_eur, 6),
            "n_eur_identity": self.n_eur_identity,
            "n_eur_reported": self.n_eur_reported,
            "n_no_valorable": self.n_no_valorable,
            "importe_no_valorable_original": round(
                self.importe_no_valorable_original, 6
            ),
            "n_fx_ambiguous": self.n_fx_ambiguous,
        }


@dataclass(frozen=True)
class CompanyMonthFx:
    """Resultado (company_id, mes cerrado); nulo nunca es cero."""

    company_id: str
    month: date
    por_divisa: tuple[CurrencyExposure, ...] = ()
    n_facturas_vivas: int = 0
    importe_vivo_eur_conocido: float | None = None
    importe_vivo_eur_eur: float | None = None
    importe_vivo_no_eur_valorable_eur: float | None = None
    n_valorables: int = 0
    n_no_valorables: int = 0
    n_divisas: int = 0
    n_divisas_no_valorables: int = 0
    divisas_no_valorables: tuple[str, ...] = ()
    n_divisa_desconocida: int = 0
    confidence: str = "ninguna"
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    n_cancel_excluidas: int = 0
    n_emitidas_no_vencidas: int = 0

    def to_row(self) -> dict:
        def _money(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        return {
            "company_id": self.company_id,
            "month": self.month,
            "n_facturas_vivas": self.n_facturas_vivas,
            "importe_vivo_eur_conocido": _money(self.importe_vivo_eur_conocido),
            "importe_vivo_eur_eur": _money(self.importe_vivo_eur_eur),
            "importe_vivo_no_eur_valorable_eur": _money(
                self.importe_vivo_no_eur_valorable_eur
            ),
            "n_valorables": self.n_valorables,
            "n_no_valorables": self.n_no_valorables,
            "n_divisas": self.n_divisas,
            "n_divisas_no_valorables": self.n_divisas_no_valorables,
            "divisas_no_valorables": list(self.divisas_no_valorables),
            "n_divisa_desconocida": self.n_divisa_desconocida,
            "por_divisa": json.dumps(
                [c.to_dict() for c in self.por_divisa],
                ensure_ascii=False,
                allow_nan=False,
            ),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "flags": list(self.flags),
            "n_cancel_excluidas": self.n_cancel_excluidas,
            "n_emitidas_no_vencidas": self.n_emitidas_no_vencidas,
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


def _es_divisa_desconocida(currency: str | None) -> bool:
    return currency in MONEDA_DESCONOCIDA


def _importe_valorable(
    amount_eur: float | None, fx_ambiguous: bool, currency: str | None
) -> float | None:
    """Importe en EUR SOLO si se conoce y es fiable; si no, None.

    Nunca se estima: nulo/ambiguo no es cero. ``fx_ambiguous`` invalida la
    conversion reportada de una divisa no-EUR (tipo 1 contradictorio), pero NO
    la identidad EUR (``amount_eur = amount`` es exacta aunque la moneda
    contable sea otra).
    """
    if amount_eur is None or not math.isfinite(amount_eur):
        return None
    if fx_ambiguous and currency != "EUR":
        return None
    return amount_eur


def _cut_currency_stats(
    records: list[FxInvoiceRecord], month_end: date
) -> tuple[list[CurrencyExposure], int, int, int]:
    """Composicion por divisa de la cartera viva del corte.

    Devuelve (filas_por_divisa, n_emitidas_no_vencidas, n_cancel, n_vivas).
    Solo usa fechas <= cierre de m; nada posterior influye.
    """
    acc: dict[str | None, dict] = {}
    n_emitidas_no_vencidas = 0
    n_cancel = 0
    n_vivas = 0

    for r in records:
        # Referencia temporal del universo segun el filtro declarado.
        ref = r.issuance_date if FILTRO_EMISION else r.due_date
        if r.status == STATUS_CANCEL:
            if ref is not None and ref <= month_end:
                n_cancel += 1
            continue
        # Fuera del universo point-in-time: aun no existe (o no es exigible).
        if ref is None or ref > month_end:
            continue
        # Criterio status-aware: payment_date solo es real si status='paid'.
        if (
            r.status == STATUS_PAID
            and r.payment_date is not None
            and r.payment_date <= month_end
        ):
            continue  # cobrada al corte: no viva
        n_vivas += 1
        if (
            r.issuance_date is not None
            and r.issuance_date <= month_end
            and r.due_date is not None
            and r.due_date > month_end
        ):
            n_emitidas_no_vencidas += 1  # no vencida pero ya emitida (cartera)

        slot = acc.get(r.currency)
        if slot is None:
            slot = acc[r.currency] = {
                "n": 0,
                "original": 0.0,
                "eur": 0.0,
                "n_identity": 0,
                "n_reported": 0,
                "n_no_val": 0,
                "no_val_original": 0.0,
                "n_fx_amb": 0,
            }
        slot["n"] += 1

        original = r.amount
        if original is not None and math.isfinite(original):
            slot["original"] += original

        valor = _importe_valorable(r.amount_eur, r.fx_ambiguous, r.currency)
        if valor is None:
            slot["n_no_val"] += 1
            if original is not None and math.isfinite(original):
                slot["no_val_original"] += original
            if r.fx_ambiguous:
                slot["n_fx_amb"] += 1
        else:
            slot["eur"] += valor
            if r.amount_eur_source == "reported":
                slot["n_reported"] += 1
            else:
                slot["n_identity"] += 1

    rows = [
        CurrencyExposure(
            currency=cur,
            n_facturas=s["n"],
            importe_original=s["original"],
            importe_eur=s["eur"],
            n_eur_identity=s["n_identity"],
            n_eur_reported=s["n_reported"],
            n_no_valorable=s["n_no_val"],
            importe_no_valorable_original=s["no_val_original"],
            n_fx_ambiguous=s["n_fx_amb"],
            divisa_desconocida=_es_divisa_desconocida(cur),
        )
        for cur, s in acc.items()
    ]
    rows.sort(key=lambda c: (c.divisa_desconocida, c.currency or ""))
    return rows, n_emitidas_no_vencidas, n_cancel, n_vivas


def _resumen_empresa_mes(
    company_id: str,
    month: date,
    rows: list[CurrencyExposure],
    n_no_vencidas: int,
    n_cancel: int,
    n_vivas: int,
) -> CompanyMonthFx:
    """Agrega la composicion por divisa a la fila empresa-mes + confidence."""
    if n_vivas == 0:
        return CompanyMonthFx(
            company_id=company_id,
            month=month,
            por_divisa=tuple(rows),
            n_facturas_vivas=0,
            confidence="ninguna",
            reasons=("sin_cartera_viva_de_cobros",),
            n_cancel_excluidas=n_cancel,
            n_emitidas_no_vencidas=n_no_vencidas,
        )

    n_valorables = sum(c.n_facturas - c.n_no_valorable for c in rows)
    n_no_valorables = sum(c.n_no_valorable for c in rows)
    eur_n = sum(
        c.n_facturas - c.n_no_valorable for c in rows if c.currency == "EUR"
    )
    no_eur_val_n = sum(
        c.n_facturas - c.n_no_valorable
        for c in rows
        if c.currency != "EUR" and not c.divisa_desconocida
    )
    eur_eur = sum(c.importe_eur for c in rows if c.currency == "EUR")
    eur_no_eur = sum(
        c.importe_eur
        for c in rows
        if c.currency != "EUR" and not c.divisa_desconocida
    )
    eur_total = eur_eur + eur_no_eur
    n_divisas = len({c.currency for c in rows if c.currency is not None})
    no_val_currencies = tuple(
        sorted(c.currency for c in rows if c.n_no_valorable > 0 and c.currency)
    )
    n_divisas_no_val = len(no_val_currencies)
    n_divisa_desconocida = sum(c.n_facturas for c in rows if c.divisa_desconocida)

    reasons: list[str] = []
    flags: list[str] = []
    if n_no_valorables > 0:
        reasons.append("facturas_vivas_sin_valor_en_eur")
        flags.append("exposicion_no_valorable")
    if n_divisas_no_val > 0:
        flags.append("exposicion_en_divisa_no_eur")
    if any(c.currency == "EUR" for c in rows) and n_divisas > 1:
        flags.append("cartera_multidivisa")
    if all(c.currency == "EUR" for c in rows if c.n_facturas > 0):
        flags.append("solo_eur")
    if eur_no_eur > 0:
        flags.append("exposicion_no_eur_valorable")
    if n_divisa_desconocida > 0:
        reasons.append("divisa_desconocida_en_el_corte")
        flags.append("divisa_desconocida")
    if any(c.n_eur_reported > 0 for c in rows):
        flags.append("conversion_reportada_propia")

    if n_no_valorables / n_vivas >= 0.5:
        confidence = "baja"
    elif n_no_valorables > 0:
        confidence = "media"
    elif eur_total < MIN_EXPOSICION_CONFIANZA_ALTA or n_valorables < MIN_FACTURAS_CONFIANZA_ALTA:
        confidence = "media"
        reasons.append("cartera_de_cobros_fina")
    else:
        confidence = "alta"
    if n_divisa_desconocida > 0 and confidence == "alta":
        confidence = "media"

    return CompanyMonthFx(
        company_id=company_id,
        month=month,
        por_divisa=tuple(rows),
        n_facturas_vivas=n_vivas,
        importe_vivo_eur_conocido=eur_total if n_valorables > 0 else None,
        importe_vivo_eur_eur=eur_eur if eur_n > 0 else None,
        importe_vivo_no_eur_valorable_eur=eur_no_eur if no_eur_val_n > 0 else None,
        n_valorables=n_valorables,
        n_no_valorables=n_no_valorables,
        n_divisas=n_divisas,
        n_divisas_no_valorables=n_divisas_no_val,
        divisas_no_valorables=no_val_currencies,
        n_divisa_desconocida=n_divisa_desconocida,
        confidence=confidence,
        reasons=tuple(dict.fromkeys(reasons)),
        flags=tuple(dict.fromkeys(flags)),
        n_cancel_excluidas=n_cancel,
        n_emitidas_no_vencidas=n_no_vencidas,
    )


def compute_fx_exposure(
    invoices: list[FxInvoiceRecord],
    months: list[date],
    companies: list[str] | None = None,
) -> list[CompanyMonthFx]:
    """Motor PURO: exposicion por divisa de la cartera viva (company_id, mes).

    - invoices: facturas ya filtradas a document_type='invoice' y
      flow_side='inflow' (el llamador filtra el resto). El motor aplica el
      criterio status-aware y de emision, y cuenta aparte las canceladas.
    - months: primeros de mes cerrado; el corte de cada mes es su fin de mes.
    - companies: rejilla completa (incluye empresas sin facturas).

    Nunca mira un mes posterior al corte que calcula; no muta la entrada;
    determinista; sin reloj ni IO.
    """
    by_company: dict[str, list[FxInvoiceRecord]] = {}
    for inv in invoices:
        by_company.setdefault(inv.company_id, []).append(inv)

    ids = list(companies) if companies is not None else sorted(by_company)
    results: list[CompanyMonthFx] = []
    for cid in ids:
        recs = by_company.get(cid, [])
        for m in months:
            month_end = _month_end(m)
            rows, n_no_vencidas, n_cancel, n_vivas = _cut_currency_stats(
                recs, month_end
            )
            results.append(
                _resumen_empresa_mes(
                    cid, m, rows, n_no_vencidas, n_cancel, n_vivas
                )
            )
    return results


# --------------------------------------------------------------------- #
# Censo
# --------------------------------------------------------------------- #
def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo))


def _corte_stats(row: CompanyMonthFx) -> dict:
    """Reparto EUR / no-EUR / no valorable de una fila, en dinero y facturas."""
    eur_n = eur_importe = 0
    no_eur_n = no_eur_importe = 0
    no_val_n = 0
    desc_n = 0
    divisas: set[str] = set()
    divisas_no_val: set[str] = set()
    for c in row.por_divisa:
        val = c.n_facturas - c.n_no_valorable
        if c.divisa_desconocida:
            desc_n += c.n_facturas
        elif c.currency == "EUR":
            eur_n += val
            eur_importe += c.importe_eur
        else:
            no_eur_n += val
            no_eur_importe += c.importe_eur
            if val > 0:
                divisas.add(c.currency or "")
        no_val_n += c.n_no_valorable
        if c.n_no_valorable > 0 and c.currency and not c.divisa_desconocida:
            divisas_no_val.add(c.currency)
    return {
        "n_facturas_vivas": row.n_facturas_vivas,
        "n_eur": eur_n,
        "importe_eur": round(eur_importe, 2),
        "n_no_eur_valorable": no_eur_n,
        "importe_no_eur_valorable_eur": round(no_eur_importe, 2),
        "n_no_valorable": no_val_n,
        "n_divisa_desconocida": desc_n,
        "n_divisas_valorables": len(divisas),
        "divisas_valorables": sorted(divisas),
        "n_divisas_no_valorables": len(divisas_no_val),
        "divisas_no_valorables": sorted(divisas_no_val),
    }


def _sum_por_divisa(rows: list[CompanyMonthFx]) -> dict:
    """Importe original y EUR por divisa sobre varias filas del mismo corte."""
    acc: dict[str, dict] = {}
    for r in rows:
        for c in r.por_divisa:
            key = c.currency if c.currency is not None else "unknown"
            slot = acc.setdefault(
                key, {"n_facturas": 0, "importe_original": 0.0,
                      "importe_eur": 0.0, "n_no_valorable": 0,
                      "importe_no_valorable_original": 0.0}
            )
            slot["n_facturas"] += c.n_facturas
            slot["importe_original"] += c.importe_original
            slot["importe_eur"] += c.importe_eur
            slot["n_no_valorable"] += c.n_no_valorable
            slot["importe_no_valorable_original"] += c.importe_no_valorable_original
    return {
        k: {
            "n_facturas": v["n_facturas"],
            "importe_original": round(v["importe_original"], 2),
            "importe_eur": round(v["importe_eur"], 2),
            "n_no_valorable": v["n_no_valorable"],
            "importe_no_valorable_original": round(
                v["importe_no_valorable_original"], 2
            ),
        }
        for k, v in sorted(acc.items())
    }


def build_census(
    results: list[CompanyMonthFx],
    months: list[date],
    n_empresas_grid: int,
    evaluables: list[str] | None = None,
    corte_censo: date | None = None,
) -> dict:
    """Censo del reparto por divisa y cruce con la poblacion evaluable de v4.

    ``evaluables`` son los company_id de reports/score_v4/assessments.parquet
    con health_score no nulo y excluida=false (957 empresas).
    """
    n_rows = len(results)
    con_cartera = [r for r in results if r.n_facturas_vivas > 0]
    conf: dict[str, int] = {level: 0 for level in CONFIDENCE_LEVELS}
    for r in results:
        conf[r.confidence] = conf.get(r.confidence, 0) + 1

    # Cifra titular: el ultimo corte cerrado disponible (o el indicado).
    if corte_censo is None and months:
        corte_censo = months[-1]
    titulares = [r for r in results if r.month == corte_censo]
    titular = {
        "corte": corte_censo.isoformat() if corte_censo else None,
        "empresas_con_cartera": len({r.company_id for r in titulares if r.n_facturas_vivas > 0}),
        **(lambda agg: {
            "n_facturas_vivas": sum(_corte_stats(r)["n_facturas_vivas"] for r in titulares),
            "n_eur": agg["n_eur"],
            "importe_eur": round(agg["importe_eur"], 2),
            "n_no_eur_valorable": agg["n_no_eur_valorable"],
            "importe_no_eur_valorable_eur": round(agg["importe_no_eur_valorable_eur"], 2),
            "n_no_valorable": agg["n_no_valorable"],
            "n_divisa_desconocida": agg["n_divisa_desconocida"],
            "divisas_valorables": sorted({
                d for r in titulares for d in _corte_stats(r)["divisas_valorables"]
            }),
            "n_divisas_valorables": len({
                d for r in titulares for d in _corte_stats(r)["divisas_valorables"]
            }),
            "divisas_no_valorables": sorted({
                d for r in titulares for d in _corte_stats(r)["divisas_no_valorables"]
            }),
            "n_divisas_no_valorables": len({
                d for r in titulares for d in _corte_stats(r)["divisas_no_valorables"]
            }),
            "n_no_eur_total": agg["n_no_eur_valorable"] + agg["n_no_valorable"],
            "pct_no_eur_valorable": (
                round(
                    agg["n_no_eur_valorable"]
                    / (agg["n_no_eur_valorable"] + agg["n_no_valorable"]),
                    4,
                )
                if (agg["n_no_eur_valorable"] + agg["n_no_valorable"]) > 0
                else None
            ),
            "n_emitidas_no_vencidas": agg["n_emitidas_no_vencidas"],
            "por_divisa": _sum_por_divisa(titulares),
        })(_sum_cortes(titulares)),
    }

    # Serie mensual (dinero y facturas) para auditar la evolucion.
    serie = []
    for m in months:
        ms = [r for r in results if r.month == m]
        agg = _sum_cortes(ms)
        serie.append({
            "month": m.isoformat(),
            "empresas_con_cartera": len({r.company_id for r in ms if r.n_facturas_vivas > 0}),
            "n_facturas_vivas": agg["n_facturas_vivas"],
            "n_eur": agg["n_eur"],
            "importe_eur": round(agg["importe_eur"], 2),
            "n_no_eur_valorable": agg["n_no_eur_valorable"],
            "importe_no_eur_valorable_eur": round(agg["importe_no_eur_valorable_eur"], 2),
            "n_no_valorable": agg["n_no_valorable"],
            "n_divisa_desconocida": agg["n_divisa_desconocida"],
            "n_emitidas_no_vencidas": agg["n_emitidas_no_vencidas"],
        })

    # Cruce con la poblacion evaluable de v4.
    cruce = None
    if evaluables is not None:
        ev = set(evaluables)
        ult = [r for r in results if r.month == corte_censo]
        con = [r for r in ult if r.company_id in ev]
        con_cart = [r for r in con if r.n_facturas_vivas > 0]
        no_eur = [r for r in con_cart if any(
            c.n_facturas - c.n_no_valorable > 0
            and c.currency != "EUR" and not c.divisa_desconocida
            for c in r.por_divisa
        )]
        no_val = [r for r in con_cart if r.n_no_valorables > 0]
        ever_no_eur: set[str] = set()
        ever_no_val: set[str] = set()
        ever_cart: set[str] = set()
        for r in results:
            if r.company_id not in ev or r.n_facturas_vivas == 0:
                continue
            ever_cart.add(r.company_id)
            if any(
                c.n_facturas - c.n_no_valorable > 0
                and c.currency != "EUR" and not c.divisa_desconocida
                for c in r.por_divisa
            ):
                ever_no_eur.add(r.company_id)
            if r.n_no_valorables > 0:
                ever_no_val.add(r.company_id)
        cruce = {
            "corte": corte_censo.isoformat() if corte_censo else None,
            "evaluables_v4": len(ev),
            "evaluables_con_cartera_en_el_corte": len(con_cart),
            "evaluables_con_exposicion_no_eur": len(no_eur),
            "evaluables_con_exposicion_no_valorable": len(no_val),
            "evaluables_con_cartera_alguna_vez": len(ever_cart),
            "evaluables_con_exposicion_no_eur_alguna_vez": len(ever_no_eur),
            "evaluables_con_exposicion_no_valorable_alguna_vez": len(ever_no_val),
            "nota": "no-EUR = factura valorable en divisa distinta de EUR; "
            "no valorable = factura viva sin amount_eur conocido o con "
            "fx_ambiguous. Cruce contra reports/score_v4/assessments.parquet "
            "con health_score no nulo y excluida=false.",
        }

    return {
        "capa": "healthscore_v4 · factor de riesgo FX · F2 exposicion empresa-mes",
        "grano": "(company_id, month), meses cerrados",
        "filas": n_rows,
        "meses": len(months),
        "empresas_grid": n_empresas_grid,
        "empresas_con_cartera_viva_alguna_vez": len({r.company_id for r in con_cartera}),
        "criterio_universo": CRITERIO_UNIVERSO,
        "filtro_emision": (
            "emision frente a vencimiento: esta capa filtra por "
            "issuance_date_ok <= fin de m (la cartera viva incluye lo aun no "
            "vencido); debt_obligation.py filtra due_date_ok <= fin de m "
            "porque mide obligacion vencida. El criterio de impago "
            "status-aware es identico. `n_emitidas_no_vencidas` mide el "
            "subconjunto vivo, emitido y aun no vencido, en cada corte."
        ),
        "criterio_confidence": CRITERIO_CONFIDENCE,
        "motivo_importe": MOTIVO_AMOUNT,
        "confidence_reparto": conf,
        "titular_ultimo_corte": titular,
        "serie_mensual": serie,
        "cruce_evaluables_v4": cruce,
        "agujeros_valoracion": {
            "empresa_mes_con_no_valorables": sum(
                1 for r in results if r.n_no_valorables > 0
            ),
            "facturas_no_valorables_acumuladas": sum(
                r.n_no_valorables for r in results
            ),
            "facturas_fx_ambiguous_acumuladas": sum(
                c.n_fx_ambiguous for r in results for c in r.por_divisa
            ),
            "nota": "sumas acumuladas sobre cortes point-in-time: una factura "
            "cuenta en todos los cortes desde su emision hasta su cobro. "
            "fx_ambiguous invalida la conversion reportada de una divisa no-EUR "
            "(tipo 1 contradictorio); la identidad EUR no se invalida.",
        },
        "limitacion_residual": LIMITACION_RESIDUAL,
        "fuente": "clean/invoices.parquet (document_type_norm='invoice', "
        "flow_side='inflow') + reports/score_v4/assessments.parquet (cruce)",
        "no_consumido": "tipos de cambio, xray/fx_index.py, marts de stock "
        "reconstruido: NO entran en ninguna cifra publicada (los tipos son de "
        "F1; la union es de F3)",
    }


def _sum_cortes(rows: list[CompanyMonthFx]) -> dict:
    """Suma el reparto de varias filas empresa-mes (mismo corte)."""
    out = {
        "n_facturas_vivas": 0,
        "n_eur": 0,
        "importe_eur": 0.0,
        "n_no_eur_valorable": 0,
        "importe_no_eur_valorable_eur": 0.0,
        "n_no_valorable": 0,
        "n_divisa_desconocida": 0,
        "n_emitidas_no_vencidas": 0,
    }
    for r in rows:
        s = _corte_stats(r)
        for k in out:
            if k == "n_emitidas_no_vencidas":
                out[k] += r.n_emitidas_no_vencidas
            else:
                out[k] += s[k]
    return out


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def _assert_no_nan(results: list[CompanyMonthFx]) -> None:
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


def _load_inputs(clean_dir: Path, con):
    rows = con.execute(
        f"""
        SELECT company_id, currency_norm, status_norm, issuance_date_ok,
               due_date_ok, payment_date_ok, amount, amount_eur,
               amount_eur_source, coalesce(fx_ambiguous, FALSE)
        FROM read_parquet('{clean_dir / "invoices.parquet"}')
        WHERE document_type_norm = '{DOC_INVOICE}'
          AND flow_side = '{FLOW_SIDE_IN}'
        """
    ).fetchall()
    invoices = [
        FxInvoiceRecord(
            company_id=r[0],
            currency=r[1],
            status=r[2],
            issuance_date=r[3].date() if r[3] is not None else None,
            due_date=r[4].date() if r[4] is not None else None,
            payment_date=r[5].date() if r[5] is not None else None,
            amount=r[6],
            amount_eur=r[7],
            amount_eur_source=r[8] or "unknown",
            fx_ambiguous=bool(r[9]),
        )
        for r in rows
    ]
    companies = [
        r[0]
        for r in con.execute(
            f"SELECT company_id FROM read_parquet('{clean_dir / 'companies.parquet'}') "
            "ORDER BY company_id"
        ).fetchall()
    ]
    return invoices, companies


def _load_evaluables(assessments: Path, con) -> list[str]:
    if not assessments.exists():
        return []
    rows = con.execute(
        f"""
        SELECT DISTINCT company_id
        FROM read_parquet('{assessments}')
        WHERE health_score IS NOT NULL AND excluida = FALSE
        ORDER BY company_id
        """
    ).fetchall()
    return [r[0] for r in rows]


def _write_report(census: dict, out: Path) -> None:
    t = census["titular_ultimo_corte"]
    cruce = census["cruce_evaluables_v4"] or {}
    lines = [
        "# F2 · Exposicion en divisa de la cartera viva de COBROS (empresa-mes)",
        "",
        "Capa F2 del `docs/SPEC_FX_RISK_INDEX.md`. Construye el LADO EMPRESA: "
        "que parte de lo que a cada empresa le deben esta en cada divisa, mes a "
        "mes, point-in-time. NO calcula inestabilidad ni toca tipos de cambio "
        "(F1); NO une con el indice ni toca la nota (F3).",
        "",
        "## Universo y criterio",
        "",
        f"- {census['criterio_universo']}",
        f"- Importe vivo: {census['motivo_importe']}",
        f"- Valoracion EUR: `amount_eur` de la propia factura (identidad si EUR, "
        "conversion reportada si no). Sin tipos estimados.",
        f"- Confidence: {census['criterio_confidence']}",
        "",
        "## Reparto en el ultimo corte cerrado ("
        f"{t['corte']})",
        "",
        f"- Empresas con cartera viva de cobros: {t['empresas_con_cartera']}",
        f"- Facturas vivas: {t['n_facturas_vivas']}",
        f"- **EUR**: {t['n_eur']} facturas · {t['importe_eur']:,.2f} EUR",
        f"- **No-EUR valorable**: {t['n_no_eur_valorable']} facturas · "
        f"{t['importe_no_eur_valorable_eur']:,.2f} EUR "
        f"({t['n_divisas_valorables']} divisas: {', '.join(t['divisas_valorables'])})",
        f"- **No valorable** (sin EUR conocido): {t['n_no_valorable']} facturas · "
        f"{t['n_divisas_no_valorables']} divisas: {', '.join(t['divisas_no_valorables'])}",
        f"- Divisa desconocida: {t['n_divisa_desconocida']} facturas",
        "",
        f"- No-EUR total: {t['n_no_eur_total']} facturas, de las que solo el "
        f"{100 * (t['pct_no_eur_valorable'] or 0):.1f}% son valorables en EUR "
        f"({t['n_no_eur_valorable']} de {t['n_no_eur_total']})",
        f"- Emitidas y aun no vencidas en el corte (incluidas por el filtro de "
        f"emision): {t['n_emitidas_no_vencidas']} facturas",
        "",
        "El importe no valorable NO se suma a la exposicion en EUR ni se imputa "
        "como cero: se publica con su propio conteo y sus propias divisas.",
        "",
        "## Cruce con la poblacion evaluable de v4 (957)",
        "",
    ]
    if cruce:
        lines += [
            f"- Evaluables con cartera viva en el corte "
            f"({cruce['corte']}): {cruce['evaluables_con_cartera_en_el_corte']}",
            f"- Evaluables con **exposicion no-EUR**: "
            f"{cruce['evaluables_con_exposicion_no_eur']}",
            f"- Evaluables con **exposicion no valorable**: "
            f"{cruce['evaluables_con_exposicion_no_valorable']}",
            f"- Alguna vez (algun corte): no-EUR "
            f"{cruce['evaluables_con_exposicion_no_eur_alguna_vez']}, "
            f"no valorable "
            f"{cruce['evaluables_con_exposicion_no_valorable_alguna_vez']}",
        ]
    lines += [
        "",
        "## Lectura honesta de viabilidad",
        "",
        "- La ponderacion por dinero es viable para la parte EUR y no-EUR "
        "valorable, pero queda coja para la parte no valorable: no se puede "
        "expresar en EUR sin inventar tipos (prohibido). Por eso la no "
        "valorable se publica como magnitud aparte y la `confidence` la "
        "degrada.",
        "- El importe vivo usa `amount` (nominal emitido) porque "
        "`pending_amount` es terminal y pondria a cero lo cobrado despues del "
        "corte.",
        "",
        f"- Limitacion residual: {census['limitacion_residual']}",
        "",
    ]
    (out / "report_exposicion.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xray.fx_exposure",
        description="Exposicion en divisa de la cartera viva de cobros (F2)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="directorio de salida compartido con F1; solo aborta si los "
        "ficheros de F2 ya existen",
    )
    parser.add_argument(
        "--workspace", type=Path, default=None,
        help="workspace con clean/ y marts/ (por defecto la ejecucion activa)",
    )
    parser.add_argument(
        "--assessments", type=Path,
        default=paths.ROOT / "reports" / "score_v4" / "assessments.parquet",
        help="parquet de la poblacion evaluable de v4 (cruce)",
    )
    args = parser.parse_args(argv)

    targets = (
        args.output_dir / "exposicion.parquet",
        args.output_dir / "exposicion_censo.json",
        args.output_dir / "report_exposicion.md",
    )
    if all(t.exists() for t in targets):
        print(
            f"ABORTADO: los ficheros de F2 ya existen en {args.output_dir}. "
            "Indica --output-dir nuevo para no destruir ejecuciones previas.",
            file=sys.stderr,
        )
        return 2

    if args.workspace is None:
        clean_dir = paths.CLEAN_DIR
    else:
        w = Path(args.workspace)
        clean_dir = w if (w / "invoices.parquet").exists() else w / "data" / "clean"

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        invoices, companies = _load_inputs(clean_dir, con)
        evaluables = _load_evaluables(args.assessments, con)
    finally:
        con.close()

    months = closed_months()
    results = compute_fx_exposure(invoices, months, companies)
    _assert_no_nan(results)

    import pandas as pd

    frame = pd.DataFrame([r.to_row() for r in results])
    frame["month"] = pd.to_datetime(frame["month"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output_dir / "exposicion.parquet", index=False)

    census = build_census(results, months, len(companies), evaluables)
    (args.output_dir / "exposicion_censo.json").write_text(
        json.dumps(census, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    _write_report(census, args.output_dir)

    t = census["titular_ultimo_corte"]
    print(json.dumps({
        "output": str(args.output_dir),
        "filas": census["filas"],
        "titular": {
            "corte": t["corte"],
            "n_facturas_vivas": t["n_facturas_vivas"],
            "n_eur": t["n_eur"],
            "importe_eur": t["importe_eur"],
            "n_no_eur_valorable": t["n_no_eur_valorable"],
            "importe_no_eur_valorable_eur": t["importe_no_eur_valorable_eur"],
            "n_no_valorable": t["n_no_valorable"],
        },
        "cruce_evaluables_v4": census["cruce_evaluables_v4"],
    }, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
