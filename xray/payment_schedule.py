"""payment_schedule — calendario point-in-time de PAGOS esperados (lado outflow).

PROBLEMA QUE ABORDA
-------------------
`reports/cashflow_forecast/report.md` (main) demuestra que extrapolar la serie
de flujo diario NO funciona: el mejor baseline es, en la practica, "predice
flujo neto cero". Este modulo cambia de fuente de senal: en vez de extrapolar
el pasado, lee el dinero YA COMPROMETIDO en la cartera de facturas de pago
(``flow_side='outflow'``), que determina mecanicamente la caja futura. Produce
un calendario fechado y un agregado por horizonte (30/60/90 dias) por empresa
y corte. NO implementa el scorer ni toca la formula.

CONVENIO DE SIGNO (dependencia declarada de la tarea de union)
--------------------------------------------------------------
``amount_eur`` es NEGATIVO para el lado de salida en esta fuente. Este modulo
CONSERVA el signo: ``importe_esperado_eur`` y ``importe_no_valorable_eur`` se
publican NEGATIVOS (salida de caja). Para comparar magnitudes usar ``abs``.
El lado de cobros (tarea hermana) publica el signo simetrico (positivo). La
suma pagos+cobros es una resta de signos opuestos; la union NO debe re-signar
nada.

METODO (identico al de la tarea hermana de cobros, aplicado al pie de la letra)
-------------------------------------------------------------------------------
1. FACTURA VIVA AL CORTE C:
       issuance_date_ok <= C
       AND NOT (status_norm = 'paid' AND payment_date_ok <= C)
       AND status_norm <> 'cancel'
   Trampa evitada: las facturas 'overdue' llevan ``payment_date_ok = due_date_ok``
   (fecha PROYECTADA, no pago real). El criterio status-aware las mantiene
   VIVAS; un filtro ingenuo por ``payment_date_ok <= C`` las daria por pagadas.
2. IMPORTE: ``amount_eur``. Nunca ``pending_amount`` (estado de HOY, no del
   corte: fuga de futuro). ``amount_eur`` nulo / no finito / ``fx_ambiguous`` no
   se imputa como cero: cuenta como NO VALORABLE con su motivo.
3. FECHA ESPERADA: ``due_date_ok + retraso_esperado(empresa)``, donde
   ``retraso_esperado`` es la MEDIANA de (``payment_date_ok - due_date_ok``) en
   dias de las facturas de esa empresa YA PAGADAS con pago <= C. Con menos de
   ``MIN_FACTURAS_RETRASO_EMPRESA`` (=10) pagadas antes del corte se usa la
   mediana global del lado y se registra ``retraso_global_por_muestra_corta``.
   La fecha esperada se acota a ``>= C + 1 dia`` para TODA factura viva: una
   factura viva en C no puede haberse pagado en C, luego su pago esperado es
   estrictamente posterior. (Generaliza la regla del metodo comun, enunciada
   para las ya vencidas, a todas las vivas; evita perder flujo por fechas
   esperadas retrocedidas.)
4. PROBABILIDAD DE PAGO POR ANTIGUEDAD. La cartera viva muy vencida no se paga
   entera. Se estima, SOLO con hechos anteriores al corte, la fraccion que
   historicamente acaba pagandose por tramo de antiguedad de vencimiento:
   ``no_vencida``, ``1_30``, ``31_90``, ``91_180``, ``180_mas``. El importe
   esperado de cada factura es ``amount_eur * p_tramo``. La tabla de
   probabilidades se publica: es un resultado interpretable por si mismo.
5. POINT-IN-TIME ESTRICTO: en el corte C solo se leen facturas emitidas <= C,
   pagos <= C, y retrasos/probabilidades estimados SOLO con esos pagos. Ningun
   hecho posterior a C (emision, pago o estado) influye en una cifra de C. Un
   test de no-fuga lo verifica.
6. SALIDA, dos granos: (a) ``calendario.parquet`` (company_id, corte, dia) con
   el importe esperado que cae cada dia; (b) ``horizontes.parquet``
   (company_id, corte, h), h en {30,60,90}, con el acumulado en (corte, corte+h],
   conteo de facturas y importe no valorable con motivos.
7. CORTES: los mismos origenes que ``xray/cashflow_forecast.py`` (fines de mes
   cerrado con ``corte + h <= 2026-08-31``; ``h=30: 21, h=60: 20, h=90: 19``)
   MAS el corte vivo 2026-08-31 (aunque su target no sea observable), que es el
   forecast que se entrega como producto. Cada fila de horizontes lleva
   ``objetivo_observable`` para que el backtest pueda filtrar.

ESTIMADOR DE PROBABILIDAD POR TRAMO (definicion exacta y PIT)
-------------------------------------------------------------
Sobre las facturas YA MADURAS al corte C, ``due_date_ok <= C - MADUREZ_DIAS``
(``MADUREZ_DIAS = 365``): se observa su retraso final ``D = pago - vencimiento``
en dias, y ``D = MADUREZ_DIAS + 1`` si no consta pago <= C dentro de la
madurez. Para el tramo con borde inferior ``lo`` (``no_vencida`` sin
condicionante; ``1_30`` lo=0; ``31_90`` lo=30; ``91_180`` lo=90; ``180_mas``
lo=180):
    p_tramo = P(D <= MADUREZ_DIAS | D > lo)
Es una curva de supervivencia: la probabilidad de acabar pagando dado que la
factura seguia impaga al alcanzar la edad ``lo``. Con menos de
``MIN_MUESTRA_PROBABILIDAD`` (=200) observaciones en riesgo se cae a la tasa
global de pago del lado y se registra ``probabilidad_global_por_muestra_corta``;
sin historia madura se usa el prior declarado ``p = 1.0`` y se registra
``probabilidad_prior_sin_historia``.

NOTA DE ASIMETRIA (lado de pagos)
---------------------------------
En cobros, la probabilidad por antiguedad modela "puede que este dinero no
llegue nunca". En pagos el sentido economico es distinto: una factura de
proveedor muy vencida sigue siendo una obligacion que probablemente habra que
atender. El estimador se aplica IGUAL (fraccion historicamente pagada por
tramo) pero se interpreta con ese matiz: los ``p`` de los tramos viejos son una
COTA INFERIOR de la obligacion economica, porque una factura puede quedar
registrada como impagada y no generar nunca un pago observado (condonacion,
compensacion, pago fuera de sistema). Por eso los tramos viejos salen mas bajos
que la intuicion de "obligacion": se publica y NO se maquilla.

LIMITACIONES DECLARADAS
-----------------------
- La fuente no tiene fecha de importacion ni de cancelacion: no puede
  demostrarse que una factura estuviera REGISTRADA en el sistema en el corte C,
  solo que su emision/vencimiento eran anteriores. Es point-in-time por fechas
  de emision/vencimiento, no por disponibilidad de registro certificada.
- El universo es ``flow_side='outflow'`` SIN filtro de ``document_type_norm``:
  es el unico filtro que reproduce el censo del coordinador (122.086 vivas /
  762 empresas / -1.427,9 M EUR al 2026-08-31). Incluye notas, recobros y
  documentos de pago ademas de facturas. Se declara porque difiere del filtro
  ``document_type_norm='invoice'`` de ``xray/debt_obligation.py``.
- El calendario publica TODAS las fechas esperadas (no se truncа a 90 dias);
  los horizontes acumulan solo (corte, corte+h].
- La mediana de retraso se estima por empresa en el lado de pagos. NO se
  reutiliza el shrinkage de ``xray/payment_delay_v2.py``: aquel encoge un ratio
  de severidad en EUROS virtuales (kappa=4178,45) sobre mora vencida, no una
  mediana de retraso en DIAS, y el metodo comun exige literalmente mediana de
  empresa + mediana global con umbral de 10 facturas. Se comparte la disciplina
  PIT y la regla status-aware, no el mecanismo de shrinkage.

El motor (``compute_payment_schedule``) es PURO: no hace IO, no usa reloj, no
muta su entrada. El CLI lee ``data/clean/invoices.parquet``, escribe bajo
``reports/payment_schedule/`` y es re-ejecutable (sobrescribe).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from xray import paths

__all__ = [
    "compute_payment_schedule",
    "normalize_records",
    "build_cutoffs",
    "closed_month_ends",
    "main",
    "HORIZONTES",
    "LAST_CUTOFF",
]

FLOW_SIDE = "outflow"
LAST_CUTOFF = date(2026, 8, 31)          # corte vivo (target no observable)
WARMUP_DIAS = 90                          # misma historia previa que cashflow_forecast
HORIZONTES = (30, 60, 90)

MADUREZ_DIAS = 365                        # ventana de observacion del pago final
MIN_FACTURAS_RETRASO_EMPRESA = 10         # umbral de la mediana por empresa
MIN_MUESTRA_PROBABILIDAD = 200            # minimo en riesgo por tramo
PRIOR_PROBABILIDAD = 1.0                  # sin historia madura: obligacion asumida

DEFAULT_OUTPUT_DIR = paths.ROOT / "reports" / "payment_schedule"
DEFAULT_CLEAN_DIR = paths.CLEAN_DIR
DEFAULT_PANEL = paths.ROOT / "reports" / "daily_flows" / "panel_diario.parquet"

# Tramos de antiguedad de vencimiento al corte (dias desde due_date_ok >= 0).
BUCKET_LABELS = ("no_vencida", "1_30", "31_90", "91_180", "180_mas")
BUCKET_LOWER = (None, 0, 30, 90, 180)     # borde inferior del tramo (None = sin condicion)
BUCKET_EDGES_JUSTIFICACION = (
    "Tramos alineados con la antiguedad de mora de debt_obligation.py y "
    "payment_delay_v2.py (30/90/180) mas 'no_vencida'. La masa de retrasos "
    "observada se concentra por debajo de 30 dias (p90 ~27 d), de modo que "
    "1_30 separa el desfase de tesoreria de la mora; 90 y 180 separan la mora "
    "persistente de la practicamente irrecuperable."
)

CONVENIO_SIGNO = (
    "amount_eur es NEGATIVO para flow_side='outflow'. importe_esperado_eur e "
    "importe_no_valorable_eur se publican con ese mismo signo (salida de caja). "
    "La tarea de union NO debe re-signar: el lado de cobros publica el signo "
    "opuesto (positivo)."
)

CRITERIO_FACTURA_VIVA = (
    "issuance_date_ok <= C AND NOT (status_norm='paid' AND payment_date_ok <= C) "
    "AND status_norm <> 'cancel'"
)

CRITERIO_NO_VALORABLE = (
    "amount_eur nulo/no finito/fx_ambiguous -> motivo 'sin_importe' (importe "
    "desconocido, nunca cero); due_date_ok nulo -> motivo 'sin_fecha_vencimiento' "
    "(importe conocido pero no fechable). Ninguno se imputa como cero."
)

FUENTE = "data/clean/invoices.parquet, flow_side='outflow' (sin filtro de document_type_norm)"

NO_CONSUMIDO = (
    "pending_amount (estado de HOY, no del corte): NO entra en ninguna cifra; "
    "debt_obligation.py/payment_delay_v2.py/cashflow_forecast.py/daily_flows.py: "
    "solo lectura, no se editan"
)

CANONICAL_COLUMNS = (
    "company_id", "issuance_date_ok", "due_date_ok", "payment_date_ok",
    "amount_eur", "status_norm", "flow_side", "fx_ambiguous",
)


# --------------------------------------------------------------------- #
# Utilidades de fechas y tramos
# --------------------------------------------------------------------- #
def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def closed_month_ends(first_day: date, last_day: date) -> list[date]:
    """Fines de mes (date) entre first_day y last_day, ambos inclusive."""
    out: list[date] = []
    year, month = first_day.year, first_day.month
    while True:
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        me = date.fromordinal(nxt.toordinal() - 1)
        if me > last_day:
            break
        if me >= first_day:
            out.append(me)
        year, month = nxt.year, nxt.month
    return out


def build_cutoffs(first_valid: date, last_closed: date = LAST_CUTOFF,
                  horizontes: tuple[int, ...] = HORIZONTES) -> dict:
    """Cortes por horizonte (mismos origenes que cashflow_forecast) + corte vivo.

    Devuelve un dict con ``origenes`` (union ordenada de cortes a computar),
    ``por_horizonte`` (cortes con target observable por h) y ``objetivo_observable``
    por (corte, h). El corte vivo ``last_closed`` se anade SIEMPRE para todos los
    horizontes aunque su target no sea observable.
    """
    month_ends = closed_month_ends(first_valid, last_closed)
    por_h: dict[str, list[date]] = {}
    for h in horizontes:
        por_h[str(h)] = [
            me for me in month_ends
            if me >= first_valid and me + timedelta(days=h) <= last_closed
        ]
    origenes = sorted({me for me in month_ends if me >= first_valid} | {last_closed})
    observabilidad = {
        f"{corte.isoformat()}|{h}": bool(corte + timedelta(days=h) <= last_closed)
        for corte in origenes for h in horizontes
    }
    return {
        "origenes": origenes,
        "por_horizonte": {
            key: [d.isoformat() for d in value] for key, value in por_h.items()
        },
        "objetivo_observable": observabilidad,
        "primer_corte_valido": first_valid.isoformat(),
        "criterio": (
            f"fin de mes cerrado >= primer dia con datos + {WARMUP_DIAS} dias "
            f"(historia previa) Y corte + h <= {last_closed.isoformat()} "
            f"(target observable); MAS el corte vivo {last_closed.isoformat()} "
            "para todos los horizontes"
        ),
    }


def _bucket_codes(age_days: np.ndarray) -> np.ndarray:
    """Codigo de tramo por edad (dias desde vencimiento): 0..4; -1 si edad NaN."""
    code = np.full(age_days.shape, -1, dtype=np.int64)
    finite = np.isfinite(age_days)
    a = age_days
    code[finite & (a < 0)] = 0
    code[finite & (a >= 0) & (a <= 30)] = 1
    code[finite & (a > 30) & (a <= 90)] = 2
    code[finite & (a > 90) & (a <= 180)] = 3
    code[finite & (a > 180)] = 4
    return code


def _median(values: np.ndarray) -> float | None:
    vals = values[np.isfinite(values)]
    if vals.size == 0:
        return None
    return float(np.median(vals))


# --------------------------------------------------------------------- #
# Estimadores PIT
# --------------------------------------------------------------------- #
def _delay_table(records: pd.DataFrame, corte: date) -> dict:
    """Mediana de retraso por empresa con pagos <= corte; fallback global.

    ``records`` debe traer ``paid_at`` (NaT si no pagada) y ``delay_dias``.
    Devuelve mapas por company_id y contadores de muestra corta.
    """
    c_ts = pd.Timestamp(corte)
    paid = records[
        records["paid_at"].notna()
        & (records["paid_at"] <= c_ts)
        & records["due_date_ok"].notna()
        & records["issuance_date_ok"].notna()
        & (records["issuance_date_ok"] <= c_ts)  # PIT: emitida hasta el corte
        & (~records["excluida"])
    ]
    counts = paid.groupby("company_id")["delay_dias"].size()
    medians = paid.groupby("company_id")["delay_dias"].median()
    global_median = _median(paid["delay_dias"].to_numpy(dtype=float))
    if global_median is None:
        global_median = 0.0
    uso_global = counts[counts < MIN_FACTURAS_RETRASO_EMPRESA].index
    media = medians.where(counts >= MIN_FACTURAS_RETRASO_EMPRESA, global_median)
    solidas = medians[counts >= MIN_FACTURAS_RETRASO_EMPRESA].to_numpy(dtype=float)
    cuantiles = (
        {q: float(np.quantile(solidas, q)) for q in (0.10, 0.25, 0.50, 0.75, 0.90)}
        if solidas.size else {}
    )
    return {
        "por_empresa": media.to_dict(),
        "mediana_global": float(global_median),
        "n_empresas_con_pagos": int(counts.size),
        "n_empresas_muestra_suficiente": int(solidas.size),
        "empresas_muestra_corta": set(uso_global.tolist()),
        "n_pagos_usados": int(len(paid)),
        "cuantiles_mediana_empresa_dias": cuantiles,
    }


def _probability_table(records: pd.DataFrame, corte: date) -> dict:
    """Curva de supervivencia P(pago <= madurez | impaga a edad 'lo') por tramo."""
    c_ts = pd.Timestamp(corte)
    mature = (
        records["due_date_ok"].notna()
        & (records["due_date_ok"] <= c_ts - pd.Timedelta(days=MADUREZ_DIAS))
        & records["issuance_date_ok"].notna()
        & (records["issuance_date_ok"] <= c_ts)  # PIT: emitida hasta el corte
        & (~records["excluida"].to_numpy(dtype=bool))
    )
    delay = records["delay_dias"].to_numpy(dtype=float)
    paid_within = (
        records["paid_at"].notna().to_numpy(dtype=bool)
        & (records["paid_at"] <= c_ts).to_numpy(dtype=bool)
        & np.isfinite(delay)
        & (delay <= MADUREZ_DIAS)
    )
    # D = retraso final si se pago dentro de la madurez y <= corte; si no, M+1.
    d_obs = np.where(paid_within, delay, float(MADUREZ_DIAS + 1))
    mature_arr = mature.to_numpy(dtype=bool)
    n_mature = int(mature_arr.sum())
    tasa_global = (
        float((mature_arr & (d_obs <= MADUREZ_DIAS)).sum() / n_mature)
        if n_mature > 0 else None
    )

    tabla: dict[str, dict] = {}
    for code, (label, lo) in enumerate(zip(BUCKET_LABELS, BUCKET_LOWER)):
        if lo is None:
            at_risk = mature_arr.copy()
        else:
            at_risk = mature_arr & (d_obs > lo)
        n_riesgo = int(at_risk.sum())
        n_paga = int((at_risk & (d_obs <= MADUREZ_DIAS)).sum())
        motivo = None
        if n_riesgo < MIN_MUESTRA_PROBABILIDAD:
            if tasa_global is not None:
                p = tasa_global
                motivo = "probabilidad_global_por_muestra_corta"
            else:
                p = PRIOR_PROBABILIDAD
                motivo = "probabilidad_prior_sin_historia"
        else:
            p = float(n_paga / n_riesgo)
        tabla[label] = {
            "p": p,
            "n_en_riesgo": n_riesgo,
            "n_pagadas_dentro_madurez": n_paga,
            "borde_inferior_dias": lo,
            "motivo": motivo,
        }
    return {
        "tabla": tabla,
        "n_facturas_maduras": n_mature,
        "tasa_global_pago": tasa_global,
        "madurez_dias": MADUREZ_DIAS,
    }


def _cut_state(records: pd.DataFrame, corte: date) -> dict:
    """Estado PIT de un corte: mascara viva, edades, tramos, retraso y p."""
    c_ts = pd.Timestamp(corte)
    paid_at = records["paid_at"]
    issuance = records["issuance_date_ok"]
    due = records["due_date_ok"]
    live = (
        issuance.notna()
        & (issuance <= c_ts)
        & ~(paid_at.notna() & (paid_at <= c_ts))
        & (~records["excluida"].to_numpy(dtype=bool))
    )
    age = (c_ts - due).dt.days.to_numpy(dtype=float)  # NaN si due NaT
    codes = _bucket_codes(age)

    retardos = _delay_table(records, corte)
    probabilidades = _probability_table(records, corte)

    live_arr = live.to_numpy(dtype=bool)
    delay_val = (
        records["company_id"].map(retardos["por_empresa"])
        .fillna(retardos["mediana_global"])
        .to_numpy(dtype=float)
    )

    p_val = np.full(len(records), PRIOR_PROBABILIDAD, dtype=float)
    for code, label in enumerate(BUCKET_LABELS):
        p_val[codes == code] = probabilidades["tabla"][label]["p"]

    return {
        "corte": corte,
        "live": live_arr,
        "age": age,
        "codes": codes,
        "delay_val": delay_val,
        "p_val": p_val,
        "retardos": retardos,
        "probabilidades": probabilidades,
    }


# --------------------------------------------------------------------- #
# Motor puro
# --------------------------------------------------------------------- #
def compute_payment_schedule(
    records: pd.DataFrame,
    cutoffs: list[date],
    horizontes: tuple[int, ...] = HORIZONTES,
    last_closed: date = LAST_CUTOFF,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Motor PURO del calendario de pagos esperados.

    - records: DataFrame normalizado (ver ``CANONICAL_COLUMNS``), no se muta.
    - cutoffs: lista de fechas de corte a computar.
    Devuelve (calendario, horizontes, metadatos). ``calendario``:
    (company_id, corte, dia, importe_esperado_eur, n_facturas). ``horizontes``:
    (company_id, corte, h, objetivo_observable, importe_esperado_eur,
    n_facturas_valoradas, n_no_valorable, importe_no_valorable_eur,
    n_no_valorable_sin_importe, n_no_valorable_sin_fecha).
    Sin IO, sin reloj, determinista.
    """
    df = records
    n = len(df)
    company = df["company_id"].to_numpy()
    amount = df["amount_eur"].to_numpy(dtype=float)
    fx_amb = df["fx_ambiguous"].to_numpy(dtype=bool)
    due = df["due_date_ok"]

    amount_ok = np.isfinite(amount) & (~fx_amb)
    due_null = ~due.notna().to_numpy(dtype=bool)

    cal_rows: list[dict] = []
    hor_rows: list[dict] = []
    meta_cortes: list[dict] = []
    for corte in sorted(cutoffs):
        st = _cut_state(df, corte)
        live = st["live"]
        codes = st["codes"]
        c_ts = pd.Timestamp(corte)

        valorable = live & amount_ok & (~due_null) & (codes >= 0)
        no_val_sin_importe = live & (~amount_ok)
        no_val_sin_fecha = live & amount_ok & due_null

        # --- fecha esperada (dias) y floor estricto a corte+1 -----------------
        delay = st["delay_val"]
        due_ts = due
        fecha = due_ts + pd.to_timedelta(np.round(delay), unit="D")
        floor_ts = c_ts + pd.Timedelta(days=1)
        fecha = fecha.where(fecha > floor_ts, floor_ts)
        fecha_arr = fecha

        p = st["p_val"]
        esperado = amount * p

        # --- calendario ------------------------------------------------------
        idx_cal = np.where(valorable)[0]
        if idx_cal.size:
            cal = pd.DataFrame({
                "company_id": company[idx_cal],
                "corte": corte,
                "dia": fecha_arr.to_numpy()[idx_cal],
                "importe_esperado_eur": esperado[idx_cal],
                "n_facturas": 1,
            })
            cal = (
                cal.groupby(["company_id", "corte", "dia"], as_index=False)
                .agg(importe_esperado_eur=("importe_esperado_eur", "sum"),
                     n_facturas=("n_facturas", "sum"))
            )
            cal_rows.append(cal)

        # --- no valorable por empresa (cuenta y eur conocido) ----------------
        nv = pd.DataFrame({
            "company_id": company[live],
            "sin_importe": no_val_sin_importe[live],
            "sin_fecha": no_val_sin_fecha[live],
            "importe_sin_fecha": np.where(no_val_sin_fecha[live], amount[live], 0.0),
        })
        if len(nv):
            nv_agg = (
                nv.groupby("company_id", as_index=False)
                .agg(n_no_valorable_sin_importe=("sin_importe", "sum"),
                     n_no_valorable_sin_fecha=("sin_fecha", "sum"),
                     importe_no_valorable_eur=("importe_sin_fecha", "sum"))
            )
        else:
            nv_agg = pd.DataFrame(columns=[
                "company_id", "n_no_valorable_sin_importe",
                "n_no_valorable_sin_fecha", "importe_no_valorable_eur"])

        # --- horizontes ------------------------------------------------------
        # Solo empresas con al menos una factura VIVA en el corte: una factura
        # posterior no puede crear filas nuevas en un corte anterior (no-fuga).
        base = pd.DataFrame({"company_id": pd.unique(company[live])})
        for h in horizontes:
            upper = c_ts + pd.Timedelta(days=h)
            in_win = valorable & (fecha_arr > c_ts).to_numpy(dtype=bool) & (
                fecha_arr <= upper).to_numpy(dtype=bool)
            val = pd.DataFrame({
                "company_id": company[valorable],
                "importe": np.where(in_win[valorable], esperado[valorable], 0.0),
                "n": in_win[valorable].astype(int),
            })
            agg = (
                val.groupby("company_id", as_index=False)
                .agg(importe_esperado_eur=("importe", "sum"),
                     n_facturas_valoradas=("n", "sum"))
            )
            agg = base.merge(agg, on="company_id", how="left")
            agg = agg.merge(nv_agg, on="company_id", how="left")
            agg["corte"] = corte
            agg["h"] = h
            agg["objetivo_observable"] = corte + timedelta(days=h) <= last_closed
            for col in ("n_facturas_valoradas", "n_no_valorable_sin_importe",
                        "n_no_valorable_sin_fecha"):
                agg[col] = agg[col].fillna(0).astype(int)
            agg["importe_esperado_eur"] = agg["importe_esperado_eur"].fillna(0.0)
            agg["importe_no_valorable_eur"] = agg["importe_no_valorable_eur"].fillna(0.0)
            agg["n_no_valorable"] = (
                agg["n_no_valorable_sin_importe"] + agg["n_no_valorable_sin_fecha"]
            )
            hor_rows.append(agg[[
                "company_id", "corte", "h", "objetivo_observable",
                "importe_esperado_eur", "n_facturas_valoradas",
                "n_no_valorable", "importe_no_valorable_eur",
                "n_no_valorable_sin_importe", "n_no_valorable_sin_fecha",
            ]])

        # --- censo del corte -------------------------------------------------
        meta_cortes.append(_corte_census(
            company, st, amount, amount_ok, due_null, valorable,
            no_val_sin_importe, no_val_sin_fecha, esperado, fecha_arr, n, fx_amb,
        ))

    calendario = (
        pd.concat(cal_rows, ignore_index=True)
        if cal_rows else pd.DataFrame(columns=[
            "company_id", "corte", "dia", "importe_esperado_eur", "n_facturas"])
    )
    horizontes_df = (
        pd.concat(hor_rows, ignore_index=True)
        if hor_rows else pd.DataFrame(columns=[
            "company_id", "corte", "h", "objetivo_observable",
            "importe_esperado_eur", "n_facturas_valoradas", "n_no_valorable",
            "importe_no_valorable_eur", "n_no_valorable_sin_importe",
            "n_no_valorable_sin_fecha"])
    )
    calendario = calendario.sort_values(
        ["corte", "company_id", "dia"]).reset_index(drop=True)
    calendario["corte"] = pd.to_datetime(calendario["corte"])
    calendario["dia"] = pd.to_datetime(calendario["dia"])
    horizontes_df = horizontes_df.sort_values(
        ["corte", "company_id", "h"]).reset_index(drop=True)
    horizontes_df["corte"] = pd.to_datetime(horizontes_df["corte"])
    meta = {"cortes": meta_cortes}
    return calendario, horizontes_df, meta


def _corte_census(company, st, amount, amount_ok, due_null, valorable,
                  no_val_sin_importe, no_val_sin_fecha, esperado, fecha_arr, n,
                  fx_amb) -> dict:
    """Censo PIT de un corte (reproduce el censo del coordinador)."""
    live = st["live"]
    n_live = int(live.sum())
    suma = float(amount[live & amount_ok].sum())
    n_empresas = int(pd.unique(company[live]).size)
    age = st["age"]
    not_due = live & np.isfinite(age) & (age < 0)
    overdue = live & np.isfinite(age) & (age >= 0)
    # fecha esperada de las valorables, para reparto por horizonte
    tab = st["probabilidades"]["tabla"]
    val_esp = float(esperado[valorable].sum())
    # empresas vivas que usan el retraso global (muestra corta o sin pagos)
    live_companies = pd.unique(company[live])
    con_pagos = set(st["retardos"]["por_empresa"].keys())
    corta = st["retardos"]["empresas_muestra_corta"]
    n_global = int(sum(1 for cid in live_companies
                       if (cid not in con_pagos) or (cid in corta)))
    return {
        "corte": st["corte"].isoformat(),
        "n_vivas": n_live,
        "n_empresas": n_empresas,
        "amount_eur_sum": suma,
        "n_no_vencidas": int(not_due.sum()),
        "n_vencidas": int(overdue.sum()),
        "n_sin_vencimiento": int((live & due_null).sum()),
        "n_valorables": int(valorable.sum()),
        "n_sin_importe": int(no_val_sin_importe.sum()),
        "n_sin_importe_nulo": int((live & ~np.isfinite(amount)).sum()),
        "n_sin_importe_fx_ambiguo": int(
            (live & np.isfinite(amount) & fx_amb).sum()),
        "n_sin_fecha_vencimiento": int(no_val_sin_fecha.sum()),
        "n_no_valorable": int((no_val_sin_importe | no_val_sin_fecha).sum()),
        "importe_no_valorable_eur": float(amount[no_val_sin_fecha].sum()),
        "importe_esperado_eur": val_esp,
        "empresas_vivas_con_retraso_global": n_global,
        "retraso": {
            "mediana_global_dias": st["retardos"]["mediana_global"],
            "n_empresas_con_pagos": st["retardos"]["n_empresas_con_pagos"],
            "n_empresas_muestra_suficiente": st["retardos"][
                "n_empresas_muestra_suficiente"],
            "n_pagos_usados": st["retardos"]["n_pagos_usados"],
            "empresas_muestra_corta": len(st["retardos"]["empresas_muestra_corta"]),
            "cuantiles_mediana_empresa_dias": st["retardos"][
                "cuantiles_mediana_empresa_dias"],
        },
        "probabilidades": {
            label: {k: (round(v, 6) if isinstance(v, float) else v)
                    for k, v in slot.items()}
            for label, slot in tab.items()
        },
        "n_facturas_maduras": st["probabilidades"]["n_facturas_maduras"],
        "tasa_global_pago": st["probabilidades"]["tasa_global_pago"],
    }


# --------------------------------------------------------------------- #
# Carga y normalizacion
# --------------------------------------------------------------------- #
def normalize_records(df: pd.DataFrame) -> pd.DataFrame:
    """Anade las columnas derivadas PIT (``excluida``, ``paid_at``, ``delay_dias``).

    Acepta un DataFrame con ``CANONICAL_COLUMNS`` (fechas datetime-like,
    ``amount_eur`` numerico) y devuelve una copia normalizada. No muta la entrada.
    """
    out = df.copy()
    for col in ("issuance_date_ok", "due_date_ok", "payment_date_ok"):
        out[col] = pd.to_datetime(out[col])
    out["amount_eur"] = pd.to_numeric(out["amount_eur"], errors="coerce")
    out["fx_ambiguous"] = out["fx_ambiguous"].fillna(False).astype(bool)
    status_isna = out["status_norm"].isna()
    out["status_norm"] = out["status_norm"].fillna("")
    # Semantica SQL del criterio (la que reproduce el censo del coordinador):
    #   - status_norm <> 'cancel' es NULL (excluido) si status_norm es nulo;
    #   - NOT (status='paid' AND payment_date <= C) es NULL (excluido) si la
    #     factura esta marcada 'paid' pero payment_date_ok es nulo.
    # Ambas son condiciones independientes del corte, luego van en `excluida`.
    out["excluida"] = (
        status_isna
        | out["status_norm"].eq("cancel")
        | (out["status_norm"].eq("paid") & out["payment_date_ok"].isna())
    )
    out["paid_at"] = out["payment_date_ok"].where(
        out["status_norm"].eq("paid"), pd.NaT)
    out["paid_at"] = pd.to_datetime(out["paid_at"])
    out["delay_dias"] = (
        out["payment_date_ok"] - out["due_date_ok"]
    ).dt.days.astype("float64")
    return out


def load_records(clean_dir: Path) -> pd.DataFrame:
    """Lee clean/invoices.parquet (flow_side='outflow') y normaliza columnas."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        df = con.execute(
            f"""
            SELECT company_id, issuance_date_ok, due_date_ok, payment_date_ok,
                   amount_eur, status_norm, flow_side, fx_ambiguous
            FROM read_parquet('{clean_dir / "invoices.parquet"}')
            WHERE flow_side = '{FLOW_SIDE}'
            """
        ).df()
    finally:
        con.close()
    return normalize_records(df)


def resolve_first_valid(clean_dir: Path, panel: Path = DEFAULT_PANEL) -> date:
    """Primer corte valido = primer dia con datos + WARMUP_DIAS.

    Se toma el primer dia del panel diario (mismo origen que
    cashflow_forecast.py); si no existe, la emision mas antigua de facturas de
    pago. Coincide con `panel_min_day + 90 d = 2024-11-30` en esta fuente.
    """
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        if panel.exists():
            row = con.execute(
                f"SELECT min(day) FROM read_parquet('{panel}')"
            ).fetchone()
            min_day = row[0] if row and row[0] is not None else None
        else:
            min_day = None
        if min_day is None:
            row = con.execute(
                f"SELECT min(issuance_date_ok) FROM "
                f"read_parquet('{clean_dir / 'invoices.parquet'}') "
                f"WHERE flow_side = '{FLOW_SIDE}'"
            ).fetchone()
            min_day = row[0] if row else None
    finally:
        con.close()
    if min_day is None:
        min_day = date(2024, 9, 1)
    if hasattr(min_day, "date"):
        min_day = min_day.date()
    return min_day + timedelta(days=WARMUP_DIAS)


# --------------------------------------------------------------------- #
# Censo agregado y comparacion con el coordinador
# --------------------------------------------------------------------- #
def build_censo(meta: dict, calendario: pd.DataFrame, horizontes: pd.DataFrame,
                cutoffs_info: dict, last_cutoff: date, source_path: Path) -> dict:
    cortes = meta["cortes"]
    live = next((c for c in cortes if c["corte"] == last_cutoff.isoformat()), None)
    repro = None
    if live is not None:
        repro = {
            "esperado": {"n_vivas": 122086, "n_empresas": 762,
                         "amount_eur_sum": -1427.9e6,
                         "n_no_vencidas": 25437, "n_vencidas": 96547},
            "medido": {
                "n_vivas": live["n_vivas"],
                "n_empresas": live["n_empresas"],
                "amount_eur_sum": round(live["amount_eur_sum"], 2),
                "n_no_vencidas": live["n_no_vencidas"],
                "n_vencidas": live["n_vencidas"],
            },
            "coincide": (
                live["n_vivas"] == 122086 and live["n_empresas"] == 762
                and live["n_no_vencidas"] == 25437 and live["n_vencidas"] == 96547
            ),
        }
    # calendario: empresas con calendario por corte
    emp_cal = (
        calendario.groupby("corte")["company_id"].nunique().to_dict()
        if len(calendario) else {}
    )
    hor_last = horizontes[horizontes["corte"] == pd.Timestamp(last_cutoff)]
    return {
        "version": "payment_schedule_v1",
        "lado": "outflow (pagos)",
        "convenio_signo": CONVENIO_SIGNO,
        "criterio_factura_viva": CRITERIO_FACTURA_VIVA,
        "criterio_no_valorable": CRITERIO_NO_VALORABLE,
        "fuente": FUENTE,
        "no_consumido": NO_CONSUMIDO,
        "constantes": {
            "horizontes": list(HORIZONTES),
            "madurez_dias": MADUREZ_DIAS,
            "min_facturas_retraso_empresa": MIN_FACTURAS_RETRASO_EMPRESA,
            "min_muestra_probabilidad": MIN_MUESTRA_PROBABILIDAD,
            "prior_probabilidad": PRIOR_PROBABILIDAD,
            "tramos": list(BUCKET_LABELS),
            "justificacion_tramos": BUCKET_EDGES_JUSTIFICACION,
            "warmup_dias": WARMUP_DIAS,
        },
        "cortes": {**cutoffs_info,
                   "origenes": [d.isoformat() for d in cutoffs_info["origenes"]]},
        "censo_por_corte": cortes,
        "empresas_con_calendario_por_corte": {
            pd.Timestamp(k).date().isoformat(): int(v) for k, v in emp_cal.items()
        },
        "corte_vivo": {
            "corte": last_cutoff.isoformat(),
            "n_empresas_con_calendario": int(
                emp_cal.get(pd.Timestamp(last_cutoff), 0)),
            "horizontes": {
                str(int(r["h"])): {
                    "importe_esperado_eur": float(r["importe_esperado_eur"]),
                    "n_facturas_valoradas": int(r["n_facturas_valoradas"]),
                }
                for _, r in hor_last.groupby("h", as_index=False)[
                    ["importe_esperado_eur", "n_facturas_valoradas"]
                ].sum().iterrows()
            },
            "importe_no_valorable_eur": live["importe_no_valorable_eur"] if live else None,
            "n_no_valorable": live["n_no_valorable"] if live else None,
            "n_sin_importe": live["n_sin_importe"] if live else None,
            "n_sin_importe_nulo": live["n_sin_importe_nulo"] if live else None,
            "n_sin_importe_fx_ambiguo": (
                live["n_sin_importe_fx_ambiguo"] if live else None),
            "n_sin_fecha_vencimiento": live["n_sin_fecha_vencimiento"] if live else None,
            "empresas_vivas_con_retraso_global": (
                live["empresas_vivas_con_retraso_global"] if live else None),
        },
        "reproduccion_coordinador": repro,
        "limitaciones": [
            "point-in-time por fechas de emision/vencimiento, no por registro "
            "certificado (la fuente no trae fecha de importacion ni de cancelacion)",
            "universo sin filtro de document_type_norm (unico que reproduce el "
            "censo del coordinador); difiere de debt_obligation.py",
            "el calendario publica todas las fechas esperadas, no se trunca a 90 d",
            "no se reutiliza el shrinkage de payment_delay_v2.py (ratio de "
            "severidad en EUR, no mediana de retraso en dias)",
        ],
    }


# --------------------------------------------------------------------- #
# Informe
# --------------------------------------------------------------------- #
def _fmt_meur(x: float) -> str:
    return f"{x / 1e6:,.1f}"


def build_report(censo: dict) -> str:
    cv = censo["corte_vivo"]
    repro = censo["reproduccion_coordinador"]
    live = next(c for c in censo["censo_por_corte"] if c["corte"] == cv["corte"])
    tab = live["probabilidades"]
    ret = live["retraso"]
    lines: list[str] = []
    a = lines.append
    a("# Calendario de PAGOS esperados (lado outflow, 30/60/90 dias)")
    a("")
    a("Generado por `xray/payment_schedule.py` (`payment_schedule_v1`). "
      "Reproducible con:")
    a("")
    a("```sh")
    a("PYTHONPATH=. .venv/bin/python -B -m xray.payment_schedule "
      "--output-dir reports/payment_schedule")
    a("```")
    a("")
    a("Fuente: `data/clean/invoices.parquet`, `flow_side='outflow'` "
      "(SIN filtro de `document_type_norm`). Un worktree limpio no trae "
      "`data/interim/`; este modulo NO la necesita.")
    a("")
    a("## 1. Convenio de signo (dependencia de la union)")
    a("")
    a(censo["convenio_signo"])
    a("")
    a("## 2. Reproduccion del censo del coordinador al corte 2026-08-31")
    a("")
    a("| metrica | coordinador | medido | coincide |")
    a("|---|---:|---:|:---:|")
    a(f"| facturas vivas | {repro['esperado']['n_vivas']:,} | "
      f"{repro['medido']['n_vivas']:,} | {'si' if repro['coincide'] else 'NO'} |")
    a(f"| empresas | {repro['esperado']['n_empresas']} | "
      f"{repro['medido']['n_empresas']} | {'si' if repro['coincide'] else 'NO'} |")
    a(f"| amount_eur (M EUR) | {_fmt_meur(repro['esperado']['amount_eur_sum'])} | "
      f"{_fmt_meur(repro['medido']['amount_eur_sum'])} | "
      f"{'si' if repro['coincide'] else 'NO'} |")
    a(f"| no vencidas | {repro['esperado']['n_no_vencidas']:,} | "
      f"{repro['medido']['n_no_vencidas']:,} | {'si' if repro['coincide'] else 'NO'} |")
    a(f"| ya vencidas | {repro['esperado']['n_vencidas']:,} | "
      f"{repro['medido']['n_vencidas']:,} | {'si' if repro['coincide'] else 'NO'} |")
    a("")
    a(f"Ademas hay {live['n_sin_vencimiento']:,} facturas vivas con "
      "`due_date_ok` nulo (cuentan en el censo, NO se pueden fechar: "
      "`sin_fecha_vencimiento`).")
    a("")
    a("## 3. Forecast del corte vivo 2026-08-31")
    a("")
    a(f"- Empresas con calendario: **{cv['n_empresas_con_calendario']:,}**.")
    a("")
    a("| horizonte | importe esperado (M EUR) | facturas valoradas |")
    a("|---:|---:|---:|")
    for h in ("30", "60", "90"):
        slot = cv["horizontes"][h]
        a(f"| {h} | {_fmt_meur(slot['importe_esperado_eur'])} | "
          f"{slot['n_facturas_valoradas']:,} |")
    a("")
    a(f"- Importe no valorable: **{_fmt_meur(cv['importe_no_valorable_eur'])} M EUR** "
      f"(importe conocido de facturas sin fecha de vencimiento). "
      f"{live['n_sin_importe']:,} facturas con importe desconocido "
      f"(`sin_importe`: {live['n_sin_importe_nulo']:,} con `amount_eur` nulo + "
      f"{live['n_sin_importe_fx_ambiguo']:,} con `fx_ambiguous`, nunca contadas "
      "como cero) y "
      f"{live['n_sin_fecha_vencimiento']:,} sin fecha de vencimiento.")
    a("")
    a("Los importes con signo negativo son SALIDAS de caja (ver seccion 1). "
      "El calendario publica todas las fechas esperadas; los horizontes "
      "acumulan solo `(corte, corte+h]`.")
    a("")
    a("## 4. Probabilidad de pago por tramo de antiguedad (corte 2026-08-31)")
    a("")
    a("Curva de supervivencia P(pago <= madurez | seguia impaga a la edad del "
      f"borde inferior), madurez = {censo['constantes']['madurez_dias']} dias, "
      f"estimada SOLO con pagos <= corte. Facturas maduras: "
      f"{live['n_facturas_maduras']:,}.")
    a("")
    a("| tramo | p(pago) | n en riesgo | n pagadas | motivo |")
    a("|---|---:|---:|---:|---|")
    for label in ("no_vencida", "1_30", "31_90", "91_180", "180_mas"):
        s = tab[label]
        a(f"| {label} | {s['p']:.4f} | {s['n_en_riesgo']:,} | "
          f"{s['n_pagadas_dentro_madurez']:,} | {s['motivo'] or '-'} |")
    a("")
    a("### Lectura de la asimetria (lado de pagos)")
    a("")
    a("En el lado de cobros la probabilidad por antiguedad modela 'puede que "
      "este dinero no llegue nunca'. En PAGOS el sentido economico es distinto: "
      "una factura de proveedor muy vencida sigue siendo una obligacion que "
      "probablemente habra que atender. Se aplica el MISMO estimador "
      "(fraccion historicamente pagada por tramo) y NO se supone que la tabla "
      "deba parecerse a la de cobros. La tabla sale marcadamente decreciente: "
      "los tramos viejos son una COTA INFERIOR de la obligacion economica, "
      "porque una factura puede quedar registrada como impagada sin generar "
      "nunca un pago observado (condonacion, compensacion, pago fuera de "
      "sistema). Es informacion, no un fallo a maquillar.")
    a("")
    a("## 5. Retraso observado")
    a("")
    a(f"- Mediana global del lado (pago - vencimiento, dias): "
      f"**{ret['mediana_global_dias']:.1f}**.")
    a(f"- Empresas con algun pago antes del corte: {ret['n_empresas_con_pagos']:,}; "
      f"pagos usados: {ret['n_pagos_usados']:,}.")
    a(f"- Empresas vivas que usan el retraso global (sin pagos o con < "
      f"{censo['constantes']['min_facturas_retraso_empresa']} pagos antes del "
      f"corte): **{live['empresas_vivas_con_retraso_global']:,}** de "
      f"{live['n_empresas']:,}.")
    cuant = {str(k): v for k, v in
             (ret.get("cuantiles_mediana_empresa_dias") or {}).items()}
    a("- Dispersion entre empresas (mediana por empresa, empresas con >= "
      f"{censo['constantes']['min_facturas_retraso_empresa']} pagos; n="
      f"{ret['n_empresas_muestra_suficiente']:,}): "
      + ", ".join(f"p{int(float(q) * 100)}={cuant[q]:+.1f} d"
                  for q in ("0.1", "0.25", "0.5", "0.75", "0.9") if q in cuant)
      + ".")
    a("")
    a("## 6. Metodo y disciplina point-in-time")
    a("")
    a("- **Factura viva**: " + censo["criterio_factura_viva"] + ".")
    a("- **Importe**: `amount_eur` (nunca `pending_amount`). "
      + censo["criterio_no_valorable"])
    a("- **Fecha esperada**: `due_date_ok + mediana(retraso de la empresa)`, "
      "acotada a `>= corte + 1 dia` para toda factura viva. Con < 10 pagos "
      "previos se usa la mediana global (`retraso_global_por_muestra_corta`).")
    a("- **Probabilidad**: ver seccion 4; con muestra insuficiente se usa la "
      "tasa global del lado (`probabilidad_global_por_muestra_corta`).")
    a("- **No-fuga**: en el corte C solo se leen hechos <= C. Un test elimina y "
      "altera facturas y pagos posteriores a C y verifica que las cifras de C "
      "no cambian (`tests/test_payment_schedule.py`).")
    a("- **status-aware**: una factura `overdue` con "
      "`payment_date_ok = due_date_ok <= C` cuenta como VIVA (test dedicado).")
    a("")
    a("## 7. Cobertura y cortes")
    a("")
    a(f"- Origenes: `{censo['cortes']['criterio']}`.")
    a("- Cortes no vivos por horizonte (target observable): "
      + ", ".join(f"h={h}: {len(v)}"
                  for h, v in censo["cortes"]["por_horizonte"].items()) + ".")
    a(f"- Corte vivo anadido: {cv['corte']} (target no observable) para todos "
      "los horizontes.")
    a(f"- Filas de calendario: {len(censo['censo_por_corte'])} cortes; "
      "ver `calendario.parquet` y `horizontes.parquet`.")
    a("")
    a("## 8. Hallazgos y limitaciones")
    a("")
    a("- El metodo comun acota la fecha esperada a `>= corte+1`: como la mediana "
      "de retraso del lado de pagos es baja, la mayor parte de la cartera ya "
      "vencida se concentra en los primeros dias del calendario y los "
      "horizontes 30/60/90 se diferencian sobre todo por la cartera aun no "
      "vencida. Se reporta como consecuencia directa del metodo prescrito.")
    for lim in censo["limitaciones"]:
        a(f"- {lim}.")
    a("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calendario point-in-time de pagos esperados (outflow)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--clean-dir", type=Path, default=None,
                        help="Directorio clean/ (por defecto el de la ejecucion)")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL,
                        help="Panel diario para fijar el primer corte valido")
    parser.add_argument("--last-cutoff", type=str, default=LAST_CUTOFF.isoformat())
    args = parser.parse_args(argv)

    clean_dir = args.clean_dir or DEFAULT_CLEAN_DIR
    last_cutoff = date.fromisoformat(args.last_cutoff)
    records = load_records(clean_dir)
    first_valid = resolve_first_valid(clean_dir, args.panel)
    cutoffs_info = build_cutoffs(first_valid, last_cutoff)
    calendario, horizontes_df, meta = compute_payment_schedule(
        records, cutoffs_info["origenes"], HORIZONTES, last_cutoff)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    calendario.to_parquet(args.output_dir / "calendario.parquet", index=False)
    horizontes_df.to_parquet(args.output_dir / "horizontes.parquet", index=False)

    censo = build_censo(meta, calendario, horizontes_df, cutoffs_info,
                        last_cutoff, clean_dir / "invoices.parquet")
    (args.output_dir / "censo.json").write_text(
        json.dumps(censo, indent=2, ensure_ascii=False, allow_nan=False))
    (args.output_dir / "report.md").write_text(build_report(censo))
    print(json.dumps({
        "corte_vivo": censo["corte_vivo"],
        "reproduccion_coordinador": censo["reproduccion_coordinador"],
        "calendario_filas": int(len(calendario)),
        "horizontes_filas": int(len(horizontes_df)),
        "output_dir": str(args.output_dir),
    }, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
