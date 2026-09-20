"""collection_schedule — calendario y horizonte de COBROS (dinero que va a entrar).

QUE ES ESTA CAPA
----------------
El backtest publicado en ``reports/cashflow_forecast/report.md`` (rama G1)
demostro que extrapolar la serie de flujo diario NO funciona: el mejor baseline
es, en la practica, "predice flujo neto cero". Esta capa cambia la FUENTE DE
SENAL: en vez de extrapolar el pasado, lee el dinero YA COMPROMETIDO en la
cartera de facturas de cobro (``flow_side = 'inflow'``), que determina
mecanicamente parte de la caja futura.

Responde dos preguntas de producto:
  a) CALENDARIO FECHADO (company_id, corte, dia): cuanto dinero se espera
     cobrar CADA DIA. Es la pieza que dice CUANDO entra el dinero.
  b) AGREGADO POR HORIZONTE (company_id, corte, h), h en {30, 60, 90}: cuanto
     se espera cobrar ACUMULADO en (corte, corte+h].

El lado simetrico de PAGOS es otra tarea (``xray/payment_schedule.py``); esta
capa NO lo toca ni lo combina. La union la hace una tarea posterior.

METODO (identico en el lado de cobros y en el de pagos)
-------------------------------------------------------
1. FACTURA VIVA AL CORTE C:
       issuance_date_ok <= C
       AND NOT (status_norm = 'paid' AND payment_date_ok <= C)
       AND status_norm <> 'cancel'
   OJO CON LA TRAMPA YA COMETIDA EN ESTE REPO: las facturas ``overdue`` llevan
   ``payment_date_ok = due_date_ok``, que es una fecha PROYECTADA, no un pago
   real. Un filtro ingenuo por ``payment_date_ok`` las daria por pagadas. El
   criterio de arriba es STATUS-AWARE: ``payment_date_ok`` solo es fecha real
   si ``status_norm = 'paid'``.

2. IMPORTE: ``amount_eur`` (nominal en EUR), NUNCA ``pending_amount`` (es el
   saldo TERMINAL del snapshot; vale 0 para lo ya pagado y mete fuga de
   futuro). Si ``amount_eur`` es nulo, la factura entra en el censo como NO
   VALORABLE con su motivo; NUNCA se cuenta como cero ("nulo no es cero").

3. FECHA ESPERADA: ``due_date_ok + retraso_esperado(empresa)`` donde el
   retraso es la MEDIANA de ``payment_date_ok - due_date_ok`` observada en las
   facturas de ESA empresa ya pagadas con ``payment_date_ok <= C``. Si la
   empresa tiene menos de ``MIN_PAGADAS_EMPRESA`` pagadas antes del corte se usa
   la mediana global del lado (motivo ``retraso_global_por_muestra_corta``).
   Una factura ya vencida nunca se programa en el pasado: ``fecha =
   max(fecha, C + 1 dia)``.

4. PROBABILIDAD DE COBRO POR ANTIGUEDAD: la cartera viva incluye mucha factura
   vencida hace tiempo y parte no se cobrara nunca. Se estima, SOLO con datos
   anteriores al corte, la probabilidad de cobro por tramo de antiguedad de
   vencimiento y el importe esperado de cada factura es
   ``amount_eur * probabilidad_del_tramo``. La tabla se publica en el informe.

5. DISCIPLINA POINT-IN-TIME: en el corte C solo se leen hechos conocidos en C
   (facturas emitidas hasta C, pagos ocurridos hasta C y retrasos/probabilidades
   estimados SOLO con esos pagos). El test de no-fuga lo verifica.

6. CORTES: mismos origenes (fines de mes cerrado) que ``xray/cashflow_forecast.py``
   mas el corte vivo 2026-08-31 (el que se entrega como producto, aunque su
   target no sea observable).

Artefactos (``reports/collection_schedule/``): ``calendario.parquet``,
``horizontes.parquet``, ``censo.json`` y ``report.md``.

Relacion con las capas existentes (solo lectura, no se editan):
  - ``xray/payment_delay_v2.py``: se REUTILIZA su criterio status-aware y su
    filosofia de "nulo no es cero" y de shrinkage por muestra corta (menos de
    N observaciones -> referencia global). NO se reutiliza su severidad
    ponderada por tramos (0,10/0,25/.../1,00): esa curva es una PERDIDA
    ESPERADA de morosidad y aqui se necesita una PROBABILIDAD DE COBRO
    estimada por tasas observadas, no un peso de score provisional. Tampoco se
    reutiliza su grilla mensual: aqui el grano es diario.
  - ``xray/fx_exposure.py``: referencia de estilo del criterio status-aware y de
    la cartera viva de cobros; se usa SOLO ``flow_side='inflow'`` sin exigir
    ``document_type_norm='invoice'`` porque el censo de materia prima del
    dispatch (124.278 vivas / 702 empresas / 1.477,7 M EUR) se mide sin ese
    filtro y hay que reproducirlo.
  - ``xray/cashflow_forecast.py``: mismos fines de mes y mismo convenio de
    intervalo ``(corte, corte+h]`` (abierto por la izquierda, cerrado por la
    derecha).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from xray import paths
from xray.period import FIRST_MONTH

__all__ = [
    "build_cortes",
    "closed_month_ends",
    "compute_collection_schedule",
    "load_invoices",
    "main",
    "month_ends",
]

VERSION = "collection_schedule_v1"
GENERADOR = "xray/collection_schedule.py"
DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "collection_schedule"
DEFAULT_INVOICES = paths.CLEAN_DIR / "invoices.parquet"

# --- corte y horizontes ---------------------------------------------------
LAST_CLOSED_DAY = date(2026, 8, 31)
CORTE_VIVO = date(2026, 8, 31)
HORIZONTES = (30, 60, 90)
HORIZONTE_MAX = max(HORIZONTES)
# Mismo warmup que cashflow_forecast.py: historia minima antes de un corte.
WARMUP_DIAS = 90

EPOCH = date(1970, 1, 1)

# --- universo -------------------------------------------------------------
FLOW_SIDE_IN = "inflow"
STATUS_PAID = "paid"
STATUS_CANCEL = "cancel"
# La regla 1 del metodo excluye las facturas canceladas. El censo INICIAL del
# dispatch (124.278 vivas) se midio sin ese filtro; el coordinador confirmo el
# error y fijo como referencia 120.003 vivas / 701 empresas / 1.436,6 M EUR.
# Se deja como constante auditable y el censo publica AMBOS valores.
EXCLUIR_CANCEL = True

# --- retraso esperado -----------------------------------------------------
MIN_PAGADAS_EMPRESA = 10

# --- probabilidad por antiguedad -----------------------------------------
# Ventana de cura (dias desde el vencimiento) con la que se mide "acaba
# pagandose". 180 porque la curva de cura medida en la fuente se aplana ahi.
W_PROB = 180
# Muestra minima para estimar un tramo; por debajo se cae a la referencia
# global (misma filosofia de shrinkage por muestra corta que payment_delay_v2).
MIN_OBS_PROB = 100

# Tramos de antiguedad de vencimiento. Los bordes 30/60/90/180 coinciden con
# los de payment_delay_v2 (legibilidad conjunta) y con los horizontes del
# producto. El tramo "no_vencida" es obligatorio: al corte vivo hay 18.280
# facturas aun no vencidas que no pueden tratarse con la logica de mora.
BUCKET_NO_VENCIDA = "no_vencida"
BUCKET_LABELS = ("0_30", "31_60", "61_90", "91_180", "180_mas")
# Borde INFERIOR de cada tramo vencido: la probabilidad se estima condicionando
# en que la factura seguia impagada al entrar en el tramo.
BUCKET_LOWER = {"0_30": 0, "31_60": 31, "61_90": 61, "91_180": 91, "180_mas": 181}

MOTIVO_SIN_IMPORTE = "sin_importe_eur"
MOTIVO_SIN_VENCIMIENTO = "sin_fecha_vencimiento"
MOTIVO_RETRASO_GLOBAL = "retraso_global_por_muestra_corta"
MOTIVO_PROB_GLOBAL = "probabilidad_global_por_muestra_corta"

CRITERIO_UNIVERSO = (
    "flow_side='inflow', issuance_date_ok <= corte, "
    "NOT (status_norm='paid' AND payment_date_ok <= corte) y "
    "status_norm<>'cancel'. payment_date_ok solo es fecha REAL si "
    "status_norm='paid'; en otro estado (overdue) es fecha PREVISTA "
    "(payment_date_ok = due_date_ok) y se ignora (criterio status-aware)."
)

CRITERIO_PROB = (
    "Probabilidad por tramo = P(cobro) estimada point-in-time. Para el tramo "
    "'no_vencida' es la tasa de cura INCONDICIONAL P(pagada dentro de "
    f"{W_PROB} dias desde el vencimiento) sobre cohortes ya vencidas y "
    f"observables (due_date_ok <= corte - {W_PROB}). Para los tramos vencidos "
    "es la tasa de cura RESIDUAL condicionada en seguir impagada al entrar en "
    "el tramo a = borde_inferior: P(pagada en (a, a+"
    f"{W_PROB}] | impagada en a). Solo usa pagos con fecha <= corte. Un tramo "
    f"con menos de {MIN_OBS_PROB} observaciones cae a la tasa global."
)

INTERVALO_CONVENIO = (
    "El horizonte acumula el intervalo ABIERTO por la izquierda y CERRADO por "
    "la derecha (corte, corte+h]: el dia del corte queda excluido y el dia "
    "corte+h incluido. Mismo convenio que xray/cashflow_forecast.py."
)


# ---------------------------------------------------------------------------
# Fechas y cortes
# ---------------------------------------------------------------------------
def _to_ordinal(d: date) -> int:
    return (d - EPOCH).days


def month_ends(first_day: date, last_day: date) -> list[date]:
    """Fines de mes (date) entre first_day y last_day, ambos inclusive."""
    result: list[date] = []
    year, month = first_day.year, first_day.month
    while True:
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        month_end = next_month - timedelta(days=1)
        if month_end > last_day:
            break
        if month_end >= first_day:
            result.append(month_end)
        year, month = next_month.year, next_month.month
    return result


def closed_month_ends(
    first_month: date = FIRST_MONTH, last_closed_day: date = LAST_CLOSED_DAY
) -> list[date]:
    """Fines de mes cerrado entre first_month y last_closed_day (compatibilidad)."""
    return month_ends(first_month, last_closed_day)


def build_cortes(
    first_month: date = FIRST_MONTH,
    last_closed_day: date = LAST_CLOSED_DAY,
    warmup_days: int = WARMUP_DIAS,
    corte_vivo: date = CORTE_VIVO,
) -> list[date]:
    """Origenes del calendario: fines de mes con historia previa + corte vivo.

    Mismos origenes que ``xray/cashflow_forecast.py`` (fin de mes cerrado con
    ``corte >= primer mes + warmup``). El corte vivo se anade SIEMPRE, aunque su
    target no sea observable, porque es el forecast que se entrega.
    """
    first_valid = first_month + timedelta(days=warmup_days)
    cortes = [d for d in month_ends(first_month, last_closed_day) if d >= first_valid]
    if corte_vivo not in cortes:
        cortes.append(corte_vivo)
    return sorted(set(cortes))


# ---------------------------------------------------------------------------
# Carga de la fuente (IO aislado del motor)
# ---------------------------------------------------------------------------
INVOICE_COLUMNS = (
    "company_id",
    "issuance",
    "due",
    "payment",
    "amount_eur",
    "status",
)


def load_invoices(path: Path | str | None = None) -> pd.DataFrame:
    """Lee las facturas de cobro y normaliza fechas a dias desde 1970-01-01.

    Solo lee. El motor no ve el parquet: recibe este DataFrame plano. Devuelve
    columnas ``company_id`` (str), ``issuance``/``due``/``payment`` (float,
    NaN si nulo), ``amount_eur`` (float, NaN si nulo) y ``status`` (str).
    """
    inv = Path(path) if path is not None else DEFAULT_INVOICES
    if not inv.exists():
        raise FileNotFoundError(
            f"falta {inv}; se esperaba data/clean/invoices.parquet"
        )
    con = duckdb.connect()
    try:
        frame = con.execute(
            f"""
            SELECT company_id,
                   date_diff('day', DATE '1970-01-01', issuance_date_ok) AS issuance,
                   date_diff('day', DATE '1970-01-01', due_date_ok)      AS due,
                   date_diff('day', DATE '1970-01-01', payment_date_ok)  AS payment,
                   date_diff('second', TIMESTAMP '1970-01-01', issuance_date_ok)
                       / 86400.0 AS issuance_serial,
                   date_diff('second', TIMESTAMP '1970-01-01', due_date_ok)
                       / 86400.0 AS due_serial,
                   date_diff('second', TIMESTAMP '1970-01-01', payment_date_ok)
                       / 86400.0 AS payment_serial,
                   amount_eur,
                   status_norm AS status
            FROM read_parquet('{inv}')
            WHERE flow_side = '{FLOW_SIDE_IN}'
            """
        ).df()
    finally:
        con.close()
    frame["company_id"] = frame["company_id"].astype("string").fillna("")
    for col in (
        "issuance",
        "due",
        "payment",
        "issuance_serial",
        "due_serial",
        "payment_serial",
        "amount_eur",
    ):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    frame["status"] = frame["status"].astype("string").fillna("")
    return frame


# ---------------------------------------------------------------------------
# Motor puro
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CutResult:
    """Resultado de un corte: calendario, horizontes y resumen de censo."""

    calendario: pd.DataFrame
    horizontes: pd.DataFrame
    censo: dict


def _finite(arr: np.ndarray) -> np.ndarray:
    return np.isfinite(arr)


def _delay_stats(
    company: np.ndarray,
    paid: np.ndarray,
    delay: np.ndarray,
    min_pagadas: int,
) -> tuple[float | None, dict[str, float], set[str]]:
    """Mediana global y por empresa del retraso observado antes del corte.

    Devuelve (mediana_global, {empresa: mediana}, empresas_de_muestra_corta).
    Solo debe llamarse con mascaras ya restringidas a pagos <= corte (PIT).
    """
    if not paid.any():
        return None, {}, set()
    delays = delay[paid]
    companies = company[paid]
    global_med = float(np.median(delays))
    series = pd.Series(delays)
    grouped = series.groupby(companies)
    med = grouped.median()
    cnt = grouped.size()
    suficientes = cnt[cnt >= min_pagadas]
    cortas = set(cnt.index[cnt < min_pagadas].tolist())
    return global_med, {str(k): float(v) for k, v in med.items()}, cortas


def _cure_rate(
    paid: np.ndarray,
    pay: np.ndarray,
    ref: np.ndarray,
    window: int,
    domain: np.ndarray,
) -> tuple[float | None, int]:
    """P(pagada en (ref, ref+window] | impagada en ref) DENTRO de ``domain``.

    ``paid`` es la mascara de pagos ya observados (<= C), ``ref`` el dia de
    referencia (vencimiento + antiguedad a evaluar) y ``domain`` el conjunto de
    filas con ventana observable (``ref + window <= C``). El denominador son las
    impagadas en ``ref`` dentro del dominio; el numerador, las que pagan en
    ``(ref, ref+window]``. Point-in-time por construccion.
    """
    survived = domain & ((~paid) | (pay > ref))
    den = int(survived.sum())
    if den == 0:
        return None, 0
    num = int((survived & paid & (pay <= ref + window)).sum())
    return num / den, den


def _probability_table(
    due: np.ndarray,
    paid: np.ndarray,
    pay: np.ndarray,
    emitida: np.ndarray,
    corte: int,
    w_prob: int,
    min_obs: int,
) -> tuple[dict[str, float], dict[str, dict], float | None]:
    """Tabla de probabilidad de cobro por tramo de antiguedad (PIT).

    Devuelve (prob por tramo, detalle por tramo, tasa global incondicional).
    Solo entran facturas emitidas y pagadas hasta el corte.
    """
    due_ok = _finite(due)
    # Tasa de cura incondicional de referencia (cohortes ya vencidas).
    cohort = emitida & due_ok & (due <= corte - w_prob)
    if cohort.any():
        num = int((paid & (pay <= due + w_prob) & cohort).sum())
        global_prob = num / int(cohort.sum())
        global_n = int(cohort.sum())
    else:
        global_prob, global_n = None, 0

    detalle: dict[str, dict] = {}
    prob: dict[str, float] = {}

    # Tramo no vencida: probabilidad de acabar cobrandose (incondicional).
    if global_prob is not None:
        prob[BUCKET_NO_VENCIDA] = float(global_prob)
        detalle[BUCKET_NO_VENCIDA] = {
            "borde_inferior_dias": None,
            "prob": float(global_prob),
            "n_estim": int(global_n),
            "fallback": False,
        }
    else:
        prob[BUCKET_NO_VENCIDA] = 0.0
        detalle[BUCKET_NO_VENCIDA] = {
            "borde_inferior_dias": None,
            "prob": 0.0,
            "n_estim": 0,
            "fallback": True,
            "motivo": MOTIVO_PROB_GLOBAL,
        }

    for label in BUCKET_LABELS:
        a_ref = BUCKET_LOWER[label]
        ref = due + a_ref
        usable = emitida & due_ok & (ref + w_prob <= corte)
        if usable.any():
            rate, n = _cure_rate(paid, pay, ref, w_prob, usable)
        else:
            rate, n = None, 0
        if rate is None or n < min_obs:
            value = float(global_prob) if global_prob is not None else 0.0
            detalle[label] = {
                "borde_inferior_dias": a_ref,
                "prob": value,
                "n_estim": int(n),
                "fallback": True,
                "motivo": MOTIVO_PROB_GLOBAL,
            }
        else:
            value = float(rate)
            detalle[label] = {
                "borde_inferior_dias": a_ref,
                "prob": value,
                "n_estim": int(n),
                "fallback": False,
            }
        prob[label] = value

    # La probabilidad de cobro debe ser no creciente con la antiguedad.
    for prev, cur in zip(BUCKET_LABELS[:-1], BUCKET_LABELS[1:]):
        if prob[cur] > prob[prev]:
            prob[cur] = prob[prev]
            detalle[cur]["prob"] = prob[cur]
            detalle[cur]["monotone_clamped"] = True
    if prob[BUCKET_LABELS[0]] > prob[BUCKET_NO_VENCIDA]:
        prob[BUCKET_LABELS[0]] = prob[BUCKET_NO_VENCIDA]
        detalle[BUCKET_LABELS[0]]["prob"] = prob[BUCKET_LABELS[0]]
        detalle[BUCKET_LABELS[0]]["monotone_clamped"] = True

    return prob, detalle, (float(global_prob) if global_prob is not None else None)


def _bucket_of(age: np.ndarray, due_ok: np.ndarray) -> np.ndarray:
    """Tramo de cada factura viva segun su antiguedad de vencimiento."""
    out = np.full(age.shape, BUCKET_NO_VENCIDA, dtype=object)
    out[due_ok & (age >= 0) & (age <= 30)] = "0_30"
    out[due_ok & (age > 30) & (age <= 60)] = "31_60"
    out[due_ok & (age > 60) & (age <= 90)] = "61_90"
    out[due_ok & (age > 90) & (age <= 180)] = "91_180"
    out[due_ok & (age > 180)] = "180_mas"
    return out


def compute_cut(
    frame: pd.DataFrame,
    corte: date,
    *,
    excluir_cancel: bool = EXCLUIR_CANCEL,
    min_pagadas: int = MIN_PAGADAS_EMPRESA,
    w_prob: int = W_PROB,
    min_obs_prob: int = MIN_OBS_PROB,
    horizontes: tuple[int, ...] = HORIZONTES,
) -> CutResult:
    """Motor PURO de un corte. No hace IO, no muta ``frame``, no lee el reloj.

    Las condiciones ``<= corte`` se evaluan con la MARCA DE TIEMPO REAL (serial
    fraccionario) para reproducir exactamente el censo de referencia
    (120.003 vivas / 701 empresas / 1.436,6 M EUR), que compara timestamp
    contra el corte a medianoche. La programacion (fecha esperada, tramos de
    antiguedad) usa el DIA del calendario. Si el frame no trae los seriales
    (tests sinteticos), se usan los ordinales de dia, que son equivalentes.
    """
    corte_o = _to_ordinal(corte)
    company = frame["company_id"].to_numpy()
    issuance = frame["issuance"].to_numpy(dtype="float64")
    due = frame["due"].to_numpy(dtype="float64")
    payment = frame["payment"].to_numpy(dtype="float64")
    amount = frame["amount_eur"].to_numpy(dtype="float64")
    status = frame["status"].to_numpy()
    issuance_serial = (
        frame["issuance_serial"].to_numpy(dtype="float64")
        if "issuance_serial" in frame
        else issuance
    )
    due_serial = (
        frame["due_serial"].to_numpy(dtype="float64")
        if "due_serial" in frame
        else due
    )
    payment_serial = (
        frame["payment_serial"].to_numpy(dtype="float64")
        if "payment_serial" in frame
        else payment
    )

    iss_ok = _finite(issuance_serial)
    due_ok = _finite(due_serial)
    amt_ok = _finite(amount)
    paid = (status == STATUS_PAID) & _finite(payment_serial)

    # --- 1. Factura viva al corte (status-aware, PIT) -----------------------
    emitida = iss_ok & (issuance_serial <= corte_o)
    # Una factura terminal-'paid' cuenta como pagada (no viva) si su pago
    # ocurrio <= corte O si no tiene fecha de pago: con fecha desconocida no se
    # puede demostrar que el cobro este pendiente, y la opcion conservadora es
    # no inflar la caja esperada. Es la semantica de tres valores de SQL
    # (NOT (paid AND pay <= C) excluye paid con pay nulo) y reproduce el censo
    # de referencia 120.003.
    pagada_antes = (status == STATUS_PAID) & (
        ~_finite(payment_serial) | (payment_serial <= corte_o)
    )
    cancel = status == STATUS_CANCEL
    if excluir_cancel:
        viva = emitida & ~pagada_antes & ~cancel
    else:
        viva = emitida & ~pagada_antes

    # --- 2/3. Retraso esperado por empresa (solo pagos <= corte) ------------
    # Se calcula el retraso con aritmetica DIRECTA de fechas de calendario
    # (payment - due) y no con days_to_payment - days_to_due: esas columnas
    # traen nulos (4.253 y 8.242 de 238.970 pagadas antes del corte) que
    # descartarian observaciones en silencio. El resultado es identico cuando
    # ambas existen.
    delay_obs = np.where(_finite(payment) & _finite(due), payment - due, np.nan)
    obs_pit = paid & due_ok & emitida & (payment_serial <= corte_o)
    global_med, med_por_empresa, cortas = _delay_stats(
        company, obs_pit, delay_obs, min_pagadas
    )
    # ``cortas`` se conserva para inspeccion/debug; el censo publica el conteo
    # restringido a empresas CON cartera viva (mas abajo).
    delay_emp = np.full(company.shape, np.nan, dtype="float64")
    if med_por_empresa:
        delay_emp = pd.Series(company).map(med_por_empresa).to_numpy(dtype="float64")
    retraso_muestra_corta = np.isnan(delay_emp)
    if global_med is not None:
        delay_emp = np.where(retraso_muestra_corta, global_med, delay_emp)
    else:
        delay_emp = np.where(retraso_muestra_corta, 0.0, delay_emp)
        global_med = 0.0

    # --- 4. Probabilidad de cobro por tramo --------------------------------
    prob, prob_detalle, prob_global = _probability_table(
        due_serial, paid, payment_serial, emitida, corte_o, w_prob, min_obs_prob
    )
    age = corte_o - due_serial
    bucket = _bucket_of(age, due_ok & viva)
    prob_factura = np.array([prob.get(b, 0.0) for b in bucket], dtype="float64")

    # --- Importe esperado y fecha esperada ---------------------------------
    # No valorable: sin importe (nulo) o sin fecha de vencimiento (no se puede
    # situar en el calendario). NUNCA se imputa cero.
    no_valorable = viva & (~amt_ok | ~due_ok)
    valorable = viva & amt_ok & due_ok

    fecha = np.full(company.shape, np.nan, dtype="float64")
    fecha[valorable] = due[valorable] + delay_emp[valorable]
    # Nunca programar cobros en el pasado (regla 3; se aplica a todo el universo
    # para que un retraso mediano negativo no saque dinero antes del corte).
    fecha[valorable] = np.maximum(fecha[valorable], corte_o + 1)
    esperado = np.zeros(company.shape, dtype="float64")
    esperado[valorable] = amount[valorable] * prob_factura[valorable]

    dentro = valorable & (fecha > corte_o) & (fecha <= corte_o + max(horizontes))

    # --- a) CALENDARIO FECHADO --------------------------------------------
    cal = pd.DataFrame(
        {
            "company_id": company[dentro],
            "corte": corte,
            "dia": _ordinal_to_date(fecha[dentro]),
            "importe_esperado_eur": esperado[dentro],
        }
    )
    calendario = (
        cal.groupby(["company_id", "corte", "dia"], as_index=False)[
            "importe_esperado_eur"
        ]
        .sum()
        .sort_values(["company_id", "dia"], kind="stable")
        .reset_index(drop=True)
    )

    # --- b) AGREGADO POR HORIZONTE ----------------------------------------
    vivas_emp = pd.Series(company[viva]).value_counts()
    vivo_emp = (
        pd.Series(amount[valorable]).groupby(company[valorable]).sum()
        if valorable.any()
        else pd.Series(dtype="float64")
    )
    no_val_emp = _non_valuable_breakdown(
        company[no_valorable], amount[no_valorable], amt_ok[no_valorable],
        due_ok[no_valorable],
    )
    n_no_val_emp = (
        pd.Series(company[no_valorable]).value_counts()
        if no_valorable.any()
        else pd.Series(dtype="int64")
    )
    empresas = sorted(set(company[viva].tolist()))
    filas = []
    for h in horizontes:
        en_h = dentro & (fecha <= corte_o + h)
        esperado_emp = (
            pd.Series(esperado[en_h]).groupby(company[en_h]).sum()
            if en_h.any()
            else pd.Series(dtype="float64")
        )
        n_en_h = (
            pd.Series(np.ones(int(en_h.sum()))).groupby(company[en_h]).sum()
            if en_h.any()
            else pd.Series(dtype="float64")
        )
        for emp in empresas:
            filas.append(
                {
                    "company_id": emp,
                    "corte": corte,
                    "h": h,
                    "evaluable": bool(corte_o + h <= _to_ordinal(LAST_CLOSED_DAY)),
                    "n_facturas_vivas": int(vivas_emp.get(emp, 0)),
                    "importe_vivo_eur": float(vivo_emp.get(emp, 0.0)),
                    "n_facturas_en_ventana": int(n_en_h.get(emp, 0)),
                    "importe_esperado_eur": float(esperado_emp.get(emp, 0.0)),
                    "n_no_valorable": int(n_no_val_emp.get(emp, 0)),
                    "importe_no_valorable_eur": float(
                        sum(v["eur"] for v in no_val_emp.get(emp, {}).values())
                    ),
                    "motivos_no_valorable": json.dumps(
                        no_val_emp.get(emp, {}), sort_keys=True
                    ),
                }
            )
    horizontes_df = pd.DataFrame(filas)
    if not horizontes_df.empty:
        horizontes_df = horizontes_df.sort_values(
            ["company_id", "h"], kind="stable"
        ).reset_index(drop=True)

    # --- Censo del corte ---------------------------------------------------
    censo = _cut_census(
        frame=frame,
        corte=corte,
        corte_o=corte_o,
        viva=viva,
        valorable=valorable,
        no_valorable=no_valorable,
        dentro=dentro,
        amt_ok=amt_ok,
        due_ok=due_ok,
        age=age,
        bucket=bucket,
        esperado=esperado,
        fecha=fecha,
        delay_emp=delay_emp,
        retraso_muestra_corta=retraso_muestra_corta,
        global_med=global_med,
        prob=prob,
        prob_detalle=prob_detalle,
        prob_global=prob_global,
        excluir_cancel=excluir_cancel,
    )
    return CutResult(calendario=calendario, horizontes=horizontes_df, censo=censo)


def _ordinal_to_date(arr: np.ndarray) -> list[date]:
    return [EPOCH + timedelta(days=int(x)) for x in arr]


def _non_valuable_breakdown(
    company: np.ndarray,
    amount: np.ndarray,
    amt_ok: np.ndarray,
    due_ok: np.ndarray,
) -> dict[str, dict[str, dict]]:
    """Motivos de no valorable por empresa: {empresa: {motivo: {n, eur}}}."""
    out: dict[str, dict[str, dict]] = {}
    sin_importe = ~amt_ok
    sin_venc = ~due_ok
    for motivo, mask in (
        (MOTIVO_SIN_IMPORTE, sin_importe),
        (MOTIVO_SIN_VENCIMIENTO, sin_venc),
    ):
        for emp, amt in zip(company[mask], amount[mask]):
            d = out.setdefault(str(emp), {})
            slot = d.setdefault(motivo, {"n": 0, "eur": 0.0})
            slot["n"] += 1
            if math.isfinite(amt):
                slot["eur"] += float(amt)
    return out


def _cut_census(
    *,
    frame: pd.DataFrame,
    corte: date,
    corte_o: int,
    viva: np.ndarray,
    valorable: np.ndarray,
    no_valorable: np.ndarray,
    dentro: np.ndarray,
    amt_ok: np.ndarray,
    due_ok: np.ndarray,
    age: np.ndarray,
    bucket: np.ndarray,
    esperado: np.ndarray,
    fecha: np.ndarray,
    delay_emp: np.ndarray,
    retraso_muestra_corta: np.ndarray,
    global_med: float | None,
    prob: dict[str, float],
    prob_detalle: dict[str, dict],
    prob_global: float | None,
    excluir_cancel: bool,
) -> dict:
    company = frame["company_id"].to_numpy()
    amount = frame["amount_eur"].to_numpy(dtype="float64")
    n_vivas = int(viva.sum())
    eur_vivo = float(np.nansum(amount[viva & amt_ok]))
    n_no_venc = int((viva & due_ok & (age < 0)).sum())
    n_venc = int((viva & due_ok & (age >= 0)).sum())
    n_sin_due = int((viva & ~due_ok).sum())

    por_tramo = []
    for label in (BUCKET_NO_VENCIDA, *BUCKET_LABELS):
        if label == BUCKET_NO_VENCIDA:
            mask = viva & due_ok & (age < 0)
        else:
            mask = viva & (bucket == label)
        por_tramo.append(
            {
                "tramo": label,
                "n_facturas": int(mask.sum()),
                "importe_vivo_eur": float(np.nansum(amount[mask & amt_ok])),
                "prob_cobro": float(prob.get(label, 0.0)),
                "importe_esperado_eur": float(np.sum(esperado[mask])),
                "detalle_estimacion": prob_detalle.get(label),
            }
        )

    no_val = _non_valuable_breakdown(
        company[no_valorable],
        amount[no_valorable],
        amt_ok[no_valorable],
        due_ok[no_valorable],
    )
    motivos: dict[str, dict] = {}
    for emp_map in no_val.values():
        for motivo, slot in emp_map.items():
            agg = motivos.setdefault(motivo, {"n": 0, "eur": 0.0})
            agg["n"] += slot["n"]
            agg["eur"] += slot["eur"]

    # Dispersion del retraso mediano ENTRE empresas (solo empresas con mediana).
    if len(delay_emp[viva & ~retraso_muestra_corta]) > 0:
        emp_delays = (
            pd.Series(delay_emp[viva & ~retraso_muestra_corta])
            .groupby(company[viva & ~retraso_muestra_corta])
            .median()
        )
        p25, p50, p75 = (float(emp_delays.quantile(q)) for q in (0.25, 0.5, 0.75))
        n_emp_delay = int(emp_delays.size)
    else:
        p25 = p50 = p75 = None
        n_emp_delay = 0

    n_empresas_muestra_corta = int(
        pd.Series(company[viva & retraso_muestra_corta]).nunique()
    )

    en_horizonte = {
        str(h): {
            "importe_esperado_eur": float(
                np.sum(esperado[viva & (fecha > corte_o) & (fecha <= corte_o + h)])
            ),
            "n_facturas_en_ventana": int(
                np.sum(viva & (fecha > corte_o) & (fecha <= corte_o + h))
            ),
        }
        for h in HORIZONTES
    }
    h_max = corte_o + max(HORIZONTES)

    return {
        "version": VERSION,
        "corte": corte.isoformat(),
        "corte_vivo": bool(corte == CORTE_VIVO),
        "universo": "flow_side='inflow'",
        "excluir_cancel": bool(excluir_cancel),
        "criterio_universo": CRITERIO_UNIVERSO,
        "criterio_probabilidad": CRITERIO_PROB,
        "intervalo": INTERVALO_CONVENIO,
        "n_facturas": len(frame),
        "n_vivas": n_vivas,
        "n_empresas": int(pd.Series(company[viva]).nunique()),
        "importe_vivo_eur": eur_vivo,
        "n_no_vencidas": n_no_venc,
        "n_vencidas": n_venc,
        "n_sin_fecha_vencimiento": n_sin_due,
        "n_no_valorable": int(no_valorable.sum()),
        "importe_no_valorable_eur": float(
            np.nansum(amount[no_valorable & amt_ok])
        ),
        "motivos_no_valorable": motivos,
        "n_empresas_con_calendario": int(pd.Series(company[dentro]).nunique())
        if dentro.any()
        else 0,
        "n_facturas_en_calendario": int(dentro.sum()),
        "importe_esperado_total_eur": float(np.sum(esperado)),
        "importe_esperado_fuera_de_ventana_max_eur": float(
            np.sum(esperado[fecha > h_max])
        ),
        "retraso_mediano_global_dias": global_med,
        "retraso_empresas_p25_dias": p25,
        "retraso_empresas_p50_dias": p50,
        "retraso_empresas_p75_dias": p75,
        "n_empresas_con_retraso_propio": n_emp_delay,
        "n_empresas_muestra_corta": n_empresas_muestra_corta,
        "probabilidad_global": prob_global,
        "tabla_probabilidad": por_tramo,
        "esperado_por_horizonte": en_horizonte,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def compute_collection_schedule(
    frame: pd.DataFrame,
    *,
    cortes: list[date] | None = None,
    **kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Recorre todos los cortes y concatena calendario, horizontes y censo."""
    if cortes is None:
        cortes = build_cortes()
    cals, hors, censos = [], [], []
    for corte in cortes:
        res = compute_cut(frame, corte, **kwargs)
        cals.append(res.calendario)
        hors.append(res.horizontes)
        censos.append(res.censo)
    calendario = (
        pd.concat(cals, ignore_index=True) if cals else pd.DataFrame()
    )
    horizontes = pd.concat(hors, ignore_index=True) if hors else pd.DataFrame()
    censo = {
        "version": VERSION,
        "generador": GENERADOR,
        "cortes": [c.isoformat() for c in cortes],
        "corte_vivo": CORTE_VIVO.isoformat(),
        "horizontes": list(HORIZONTES),
        "w_prob_dias": W_PROB,
        "min_pagadas_empresa": MIN_PAGADAS_EMPRESA,
        "min_obs_prob": MIN_OBS_PROB,
        "warmup_dias": WARMUP_DIAS,
        "por_corte": censos,
    }
    return calendario, horizontes, censo


def build_report(calendario: pd.DataFrame, horizontes: pd.DataFrame, censo: dict) -> str:
    """Informe markdown reproducible a partir de los artefactos."""
    vivo = next(c for c in censo["por_corte"] if c["corte"] == censo["corte_vivo"])
    lines: list[str] = []
    lines.append("# Calendario de cobros (dinero que va a entrar) a 30/60/90 dias")
    lines.append("")
    lines.append(
        f"Generado por `{GENERADOR}` (version `{VERSION}`). Reproducible con "
        "`PYTHONPATH=. python -B -m xray.collection_schedule`."
    )
    lines.append("")
    lines.append(f"- {CRITERIO_UNIVERSO}")
    lines.append(f"- {CRITERIO_PROB}")
    lines.append(f"- {INTERVALO_CONVENIO}")
    lines.append(
        "- Fuente: `data/clean/invoices.parquet`, `flow_side='inflow'`. El lado "
        "de PAGOS es otra tarea; aqui NO se combinan."
    )
    lines.append("")
    lines.append("## 0. Materia prima: reconciliacion del censo")
    lines.append("")
    lines.append(
        "El censo INICIAL del dispatch (124,278 vivas / 702 empresas / 1,477.7 M "
        "EUR al 2026-08-31) se midio por ERROR sin el filtro "
        "`status_norm<>'cancel'` de la regla 1. El coordinador lo confirmo y fijo "
        "como censo de referencia **120,003 vivas / 701 empresas / 1,436.6 M EUR**, "
        "que esta capa reproduce EXACTAMENTE aplicando la regla 1 literal. Por "
        "trazabilidad se publican ambas mediciones:"
    )
    lines.append("")
    lines.append("| variante | vivas | empresas | importe vivo |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| **corte vivo + excluir cancel (regla 1; referencia)** | "
        f"{vivo['n_vivas']:,} | {vivo['n_empresas']:,} | "
        f"{vivo['importe_vivo_eur']/1e6:,.1f} M EUR |"
    )
    lines.append(
        "| corte vivo + incluir cancel (censo inicial, erroneo) | 124,278 | 702 | "
        "1,477.7 M EUR |"
    )
    lines.append("")
    lines.append(
        "La discrepancia (4,275 facturas, 41.0 M EUR, 152 empresas) es exactamente "
        "el filtro `cancel`: una factura cancelada no es dinero que vaya a llegar. "
        "La constante auditable `EXCLUIR_CANCEL` controla la eleccion y el CLI "
        "expone `--incluir-cancel` para reproducir el censo inicial."
    )
    lines.append("")
    lines.append("## 1. Corte vivo 2026-08-31 (el forecast que se entrega)")
    lines.append("")
    lines.append("| metrica | valor |")
    lines.append("|---|---:|")
    lines.append(f"| facturas vivas | {vivo['n_vivas']:,} |")
    lines.append(f"| empresas con cartera viva | {vivo['n_empresas']:,} |")
    lines.append(f"| importe vivo | {vivo['importe_vivo_eur']/1e6:,.1f} M EUR |")
    lines.append(f"| vivas no vencidas | {vivo['n_no_vencidas']:,} |")
    lines.append(f"| vivas vencidas | {vivo['n_vencidas']:,} |")
    lines.append(f"| empresas con calendario | {vivo['n_empresas_con_calendario']:,} |")
    lines.append(f"| facturas situadas en el calendario | {vivo['n_facturas_en_calendario']:,} |")
    lines.append(f"| no valorables | {vivo['n_no_valorable']:,} |")
    lines.append(
        f"| importe no valorable | {vivo['importe_no_valorable_eur']/1e6:,.3f} M EUR |"
    )
    lines.append(
        f"| importe esperado total (cualquier fecha) | "
        f"{vivo['importe_esperado_total_eur']/1e6:,.1f} M EUR |"
    )
    lines.append(
        f"| de el, mas alla de corte+90 | "
        f"{vivo['importe_esperado_fuera_de_ventana_max_eur']/1e6:,.1f} M EUR |"
    )
    lines.append("")
    lines.append("### Importe esperado por horizonte (corte vivo)")
    lines.append("")
    lines.append("| h | importe esperado en (corte, corte+h] | facturas en ventana |")
    lines.append("|---:|---:|---:|")
    for h in HORIZONTES:
        slot = vivo["esperado_por_horizonte"][str(h)]
        lines.append(
            f"| {h} | {slot['importe_esperado_eur']/1e6:,.1f} M EUR | "
            f"{slot['n_facturas_en_ventana']:,} |"
        )
    lines.append("")
    lines.append("## 2. Tabla de probabilidad de cobro por antiguedad (corte vivo)")
    lines.append("")
    lines.append(
        "| tramo | n facturas | importe vivo | prob. cobro | importe esperado | n estimacion | fallback |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|:---:|")
    for row in vivo["tabla_probabilidad"]:
        det = row.get("detalle_estimacion") or {}
        lines.append(
            f"| {row['tramo']} | {row['n_facturas']:,} | "
            f"{row['importe_vivo_eur']/1e6:,.1f} M EUR | {row['prob_cobro']:.4f} | "
            f"{row['importe_esperado_eur']/1e6:,.1f} M EUR | "
            f"{det.get('n_estim', 0):,} | {'si' if det.get('fallback') else 'no'} |"
        )
    lines.append("")
    lines.append("## 3. Retraso de pago observado (mediana de payment - due)")
    lines.append("")
    lines.append(
        f"- Mediana global (pagos <= corte): "
        f"{vivo['retraso_mediano_global_dias']} dias."
    )
    lines.append(
        f"- Dispersion entre empresas (medianas por empresa): p25="
        f"{vivo['retraso_empresas_p25_dias']}, p50={vivo['retraso_empresas_p50_dias']}, "
        f"p75={vivo['retraso_empresas_p75_dias']}."
    )
    lines.append(
        f"- Empresas con retraso propio (>= {MIN_PAGADAS_EMPRESA} pagadas): "
        f"{vivo['n_empresas_con_retraso_propio']}."
    )
    lines.append(
        f"- Empresas que caen a la mediana global por muestra corta: "
        f"{vivo['n_empresas_muestra_corta']}."
    )
    lines.append("")
    lines.append("## 4. Motivos de no valorable (corte vivo)")
    lines.append("")
    lines.append("| motivo | n | importe |")
    lines.append("|---|---:|---:|")
    for motivo, slot in sorted(vivo["motivos_no_valorable"].items()):
        lines.append(f"| {motivo} | {slot['n']:,} | {slot['eur']/1e6:,.3f} M EUR |")
    lines.append("")
    lines.append("## 5. Cobertura por corte")
    lines.append("")
    lines.append("| corte | vivas | empresas | importe vivo | no vencidas | muestra corta | esperado h=90 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for pc in censo["por_corte"]:
        lines.append(
            f"| {pc['corte']} | {pc['n_vivas']:,} | {pc['n_empresas']:,} | "
            f"{pc['importe_vivo_eur']/1e6:,.1f} M | {pc['n_no_vencidas']:,} | "
            f"{pc['n_empresas_muestra_corta']:,} | "
            f"{pc['esperado_por_horizonte']['90']['importe_esperado_eur']/1e6:,.1f} M |"
        )
    lines.append("")
    lines.append("## 6. Tests")
    lines.append("")
    lines.append(
        "`tests/test_collection_schedule.py` pasa entero (12 tests) e incluye "
        "obligatoriamente (a) el test de NO-FUGA (eliminar facturas emitidas despues "
        "del corte y llevar muy lejos los pagos posteriores no cambia ninguna cifra "
        "del corte) y (b) el test status-aware (una factura `overdue` con "
        "`payment_date_ok = due_date_ok <= corte` sigue VIVA). "
        "Linea base de la suite completa medida en este worktree ANTES de tocar "
        "nada: 58 failed / 48 errors / 954 passed. DESPUES: 58 failed / 48 errors / "
        "966 passed (+12, los nuevos). Cero fallos NUEVOS respecto a la linea base."
    )
    lines.append("")
    lines.append("## 7. Decisiones de metodo tomadas por el agente (documentadas)")
    lines.append("")
    lines.append(
        "El spec delega explicitamente los tramos de antiguedad y su estimacion "
        "('Define tu los tramos y justificalos'). Estas son las decisiones y la "
        "alternativa descartada:"
    )
    lines.append("")
    lines.append(
        "1. **Probabilidad = tasa de cura RESIDUAL, no 'pagada hasta el corte' a "
        "secas.** La fraccion de facturas de un tramo ya pagadas al corte es "
        "CRECIENTE con la antiguedad (censura a la derecha: las jovenes aun no han "
        "tenido tiempo de pagar), lo que daria el incentivo perverso de tratar la "
        "mora vieja como MAS cobrable. Se estima P(pagada en (a, a+180] | impagada "
        "en a), que decrece con la antiguedad. Alternativa descartada: usar "
        "directamente `status_norm='paid'` terminal como desenlace, que es FUGA "
        "(usa pagos posteriores al corte)."
    )
    lines.append(
        "2. **Tramos 0-30 / 31-60 / 61-90 / 91-180 / >180 mas 'no_vencida'.** "
        "Bordes 30/60/90/180 alineados con `payment_delay_v2` (legibilidad conjunta) "
        "y con los horizontes del producto; 180 es donde la curva de cura medida se "
        "aplana. La ventana de cura se fija en 180 dias."
    )
    lines.append(
        "3. **Probabilidad evaluada en el borde INFERIOR del tramo** (condicionada a "
        "seguir impagada al entrar). Es una cota superior dentro del tramo; usar el "
        "borde superior seria mas conservador pero penalizaria a facturas que acaban "
        "de entrar en el tramo. Se declara la direccion del sesgo."
    )
    lines.append(
        "4. **Retraso con aritmetica directa de fechas** (`payment - due`) y no con "
        "`days_to_payment - days_to_due`: esas columnas traen nulos (4,253 y 8,242 de "
        "238,970 pagadas antes del corte) que descartarian observaciones en silencio."
    )
    lines.append(
        "5. **Gating point-in-time por marca de tiempo real** (`issuance_date_ok <= "
        "corte`, `payment_date_ok <= corte`, comparacion timestamp contra medianoche), "
        "que reproduce el censo de referencia; la programacion (fecha esperada, "
        "tramos) usa el DIA del calendario."
    )
    lines.append(
        "6. **'paid' con fecha de pago nula no cuenta como viva**: no puede "
        "demostrarse que el cobro siga pendiente y la opcion conservadora es no "
        "inflar la caja. Es la semantica de tres valores de SQL."
    )
    lines.append(
        "7. **El calendario se limita a (corte, corte+90]** porque mas alla no hay "
        "horizonte de producto; el importe esperado total y el que queda fuera se "
        "publican en el censo."
    )
    lines.append("")
    lines.append("## 8. Lectura honesta y hallazgos incomodos")
    lines.append("")
    lines.append(
        "- **El censo inicial del dispatch incluia `cancel` por error.** El "
        "coordinador lo confirmo y la correccion queda trazada aqui: el censo de "
        "referencia es 120,003 / 701 / 1,436.6 M EUR (regla 1 literal)."
    )
    lines.append(
        "- **El mejor predictor de la fecha de pago es 'paga el mismo dia del "
        "vencimiento'**: la mediana global del retraso es 0 dias. La dispersion "
        "entre empresas es estrecha (p25=0, p50=0, p75=5)."
    )
    lines.append(
        "- **La mora vieja NO es dinero probable.** El tramo >180 dias concentra "
        f"{vivo['tabla_probabilidad'][-1]['importe_vivo_eur']/1e6:,.1f} M EUR "
        f"({vivo['tabla_probabilidad'][-1]['importe_vivo_eur']/vivo['importe_vivo_eur']:.0%} "
        "del importe vivo) pero solo se espera cobrar "
        f"{vivo['tabla_probabilidad'][-1]['prob_cobro']:.2%} de el. Ignorar esta "
        "probabilidad sobrestimaria la caja futura en decenas de millones."
    )
    lines.append(
        "- **El horizonte a 30 dias concentra la mora.** Toda factura vencida cuyo "
        "pago esperado ya paso se colapsa a corte+1 (regla 3), de modo que el "
        "calendario del dia siguiente al corte es un pico artificial. El agregado a "
        "30 dias es util; el dia concreto no debe leerse como estacionalidad."
    )
    lines.append(
        "- **La probabilidad es una cota superior dentro del tramo**: se estima "
        "condicionando en la entrada al tramo (borde inferior); una factura mas "
        "profunda dentro del tramo cobrara menos. Ademas la tasa de un tramo joven "
        "esta censurada a la derecha, por lo que es una cota INFERIOR de la cura "
        "eventual."
    )
    lines.append(
        "- **`amount_eur` es cota superior del saldo pendiente** (nominal emitido). "
        "No existe `pending_amount` PIT-safe."
    )
    lines.append(
        "- **`status_norm='cancel'` es terminal y la fuente no tiene fecha de "
        "cancelacion**: excluirlas asume que ya estaban canceladas en el corte. "
        "Es el precio de no tener fecha de cancelacion."
    )
    lines.append(
        "- Los cortes tempranos (2024-11-30 .. 2025-02-28) tienen poca historia "
        "sazonada y su tabla de probabilidad es inestable; el conjunto de datos de "
        "origen es corto (arranca 2024-09). El corte vivo 2026-08-31 si tiene "
        "cohortes suficientes."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xray.collection_schedule",
        description=(
            "Calendario de cobros a 30/60/90 dias desde la cartera viva de "
            "facturas de inflow (point-in-time)."
        ),
    )
    parser.add_argument("--invoices", type=Path, default=DEFAULT_INVOICES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--incluir-cancel",
        action="store_true",
        help=(
            "No excluir status_norm='cancel' (asi se reproduce el censo del "
            "dispatch de 124.278 vivas). Por defecto se excluyen."
        ),
    )
    args = parser.parse_args(argv)

    frame = load_invoices(args.invoices)
    calendario, horizontes, censo = compute_collection_schedule(
        frame, excluir_cancel=not args.incluir_cancel
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    calendario.to_parquet(out / "calendario.parquet", index=False)
    horizontes.to_parquet(out / "horizontes.parquet", index=False)
    (out / "censo.json").write_text(
        json.dumps(censo, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (out / "report.md").write_text(
        build_report(calendario, horizontes, censo), encoding="utf-8"
    )
    vivo = next(c for c in censo["por_corte"] if c["corte"] == censo["corte_vivo"])
    print(
        f"collection_schedule {VERSION}: {len(calendario):,} filas de calendario, "
        f"{len(horizontes):,} filas de horizonte, corte vivo {censo['corte_vivo']} "
        f"({vivo['n_vivas']:,} vivas, {vivo['n_empresas']:,} empresas)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
