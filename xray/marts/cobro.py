"""Mart: panel_cobro — cartera de cobro (AR) y pago (AP) reconstruida mes a mes.

Grano: (company_id, month) para los 24 meses cerrados 2024-09..2026-08 y las
1.286 empresas. Las empresas sin facturas salen con metricas NULL y
tiene_erp = FALSE (NULL = "no observado", 0 = "no debe nada").

Regla critica: payment_date solo es fecha REAL de cobro/pago si
status_norm = 'paid'; en cualquier otro status es la fecha PREVISTA y se
trata como NULL (fecha_cobro_real). Todo el calculo de retraso y de cartera
usa fecha_cobro_real, nunca payment_date_ok crudo.

Fuente: data/clean/invoices.parquet (+ companies para group_id).
Salida: data/marts/panel_cobro.parquet y reports/quality/panel_cobro.json.

Conversion conservadora: solo amount_eur de la capa limpia, por identidad
o cambio reportado valido. Nunca se estiman tipos. Los totales incompletos
son NULL; los subtotales conocidos y la cobertura por filas van separados.
Las fechas invalidas afectan a su metrica, no eliminan el importe emitido.
La reconstruccion de stock es retrospectiva: faltan fechas de importacion,
cancelacion e historial de pagos parciales; no se presenta como snapshot
historico certificado. Solo las cohortes maduras pueden consultarse como
predictores mediante cohort_features_at, con una fecha de corte explicita.

Disponibilidad punto-en-el-tiempo (Cambio 3): cada metrica de cohorte lleva
ademas su fecha de disponibilidad, coh_{30,60,90}d_available_at =
fin_de_mes(month) + N dias. Regla conservadora: la cohorte mensual completa
no esta disponible hasta que la ultima factura del mes ha madurado, es
decir, fin de mes + horizonte. Estas columnas NO filtran nada por si solas:
son el CONTRATO para la capa de features, que debe consumir solo filas con
available_at <= fecha_de_corte (la fecha en la que se supone que el modelo
ve los datos); consumir una cohorte antes de su available_at seria fuga de
informacion: el resultado a 30d de una factura emitida el 31 de agosto no
se conoce hasta el 30 de septiembre. La censura al cierre del dataset
(NULL cuando fin_de_mes + N > fin del dataset) se mantiene: son dos
controles distintos y hacen falta los dos.
"""

from __future__ import annotations

import json
import math

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.period import DATASET_END, MONTHS_SQL

PANEL_PATH = paths.MARTS_DIR / "panel_cobro.parquet"
REPORT_PATH = paths.REPORTS_DIR / "panel_cobro.json"

# Facturas comerciales al uso: unico document_type que entra en la cartera.
DOC_INVOICE = "invoice"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = q * (len(s) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (s[hi] - s[lo]) * (idx - lo))


def _serie_temporal(con: duckdb.DuckDBPyConnection) -> list[dict]:
    filas = con.execute(
        """
        SELECT strftime(month, '%Y-%m') AS mes,
               sum(ar_abierto_eur) AS ar_abierto_eur,
               median(ar_abierto_norm) AS ar_abierto_norm_mediana,
               CASE WHEN sum(ar_abierto_eur) > 0
                    THEN sum(ar_vencido_eur) / sum(ar_abierto_eur) END AS ar_pct_vencido,
               sum(ap_abierto_eur) AS ap_abierto_eur,
               CASE WHEN sum(ap_abierto_eur) > 0
                    THEN sum(ap_vencido_eur) / sum(ap_abierto_eur) END AS ap_pct_vencido
        FROM panel_cobro
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [
        {
            "mes": r[0],
            "ar_abierto_eur": r[1],
            "ar_abierto_norm_mediana": r[2],
            "ar_pct_vencido": r[3],
            "ap_abierto_eur": r[4],
            "ap_pct_vencido": r[5],
        }
        for r in filas
    ]


def _distribucion(con: duckdb.DuckDBPyConnection, col: str) -> dict:
    vals = [
        r[0]
        for r in con.execute(
            f"SELECT {col} FROM panel_cobro WHERE {col} IS NOT NULL"
        ).fetchall()
    ]
    return {
        "n": len(vals),
        "min": min(vals) if vals else None,
        "p25": _percentile(vals, 0.25),
        "mediana": _percentile(vals, 0.50),
        "p75": _percentile(vals, 0.75),
        "max": max(vals) if vals else None,
    }


# Fin del dataset: una cohorte solo puede evaluarse a N dias si ha tenido N
# dias completos para cobrarse desde el fin de su mes de emision.
FIN_DATASET = f"DATE '{DATASET_END}'"


def _complete_panel(con):
    cols = [row[0] for row in con.execute('DESCRIBE panel_cobro').fetchall()]
    protected = {'company_id', 'group_id', 'month', 'tiene_erp', 'meses_observados'}
    protected.update(f'coh_{n}d_available_at' for n in (30, 60, 90))
    replacements = {
        col: f'CASE WHEN p.meses_observados >= 0 THEN p.{col} END'
        for col in cols if col not in protected
    }
    joins = []
    extra = ['coalesce(p.meses_observados >= 0, false) AS observacion_erp_inferida',
             'true AS es_reconstruccion_retrospectiva']
    for side in ('ar', 'ap'):
        ca, em, li = f'c_{side}', f'e_{side}', f'l_{side}'
        for table, alias in [('cartera', ca), ('emitido', em), ('liquidado', li)]:
            joins.append(f"LEFT JOIN {table} {alias} ON {alias}.company_id=p.company_id "
                         f"AND {alias}.month=p.month AND {alias}.side='{side}'")
        stock_ok = (f'coalesce({ca}.n_posibles,0)=coalesce({ca}.n_eur,0) '
                    f'AND coalesce({ca}.n_estado_incierto,0)=0')
        aging_ok = f'{stock_ok} AND coalesce({ca}.n_due_desconocido,0)=0'
        conditions = {
            'abierto_eur': stock_ok,
            'emitido_eur': f'coalesce({em}.n_documentos_eur,0)=coalesce({em}.n_emitidas,0)',
            'liquidado_eur': (f'coalesce({li}.n_eur,0)=coalesce({li}.n_liquidadas,0) '
                             f'AND coalesce({ca}.n_estado_incierto,0)=0'),
            'vencido_eur': aging_ok,
            'mora_mas_180d_eur': aging_ok,
        }
        for col in cols:
            if col.startswith(f'{side}_aging_'):
                conditions[col[len(side)+1:]] = aging_ok
        for suffix, condition in conditions.items():
            col = f'{side}_{suffix}'
            replacements[col] = (f'CASE WHEN p.meses_observados>=0 AND {condition} '
                                 f'THEN p.{col} END')
        replacements[f'{side}_pct_vencido'] = (
            f'CASE WHEN p.meses_observados>=0 AND {aging_ok} AND p.{side}_abierto_eur>0 '
            f'THEN coalesce(p.{side}_vencido_eur,0)/p.{side}_abierto_eur END'
        )
        replacements[f'{side}_cobertura_eur_pct'] = (
            f'CASE WHEN p.meses_observados>=0 THEN '
            f'{em}.n_documentos_eur::DOUBLE/nullif({em}.n_emitidas,0) END'
        )
        extra.extend([
            f'CASE WHEN p.meses_observados>=0 THEN coalesce({ca}.abierto_eur,0) END AS {side}_abierto_conocido_eur',
            f'CASE WHEN p.meses_observados>=0 THEN coalesce({em}.emitido_eur,0) END AS {side}_emitido_conocido_eur',
            f'CASE WHEN p.meses_observados>=0 THEN coalesce({ca}.n_eur::DOUBLE/nullif({ca}.n_posibles,0),1) END AS {side}_abierto_cobertura_eur_pct',
            f'CASE WHEN p.meses_observados>=0 THEN coalesce({ca}.n_estado_incierto,0) END AS {side}_n_estado_incierto',
            f'CASE WHEN p.meses_observados>=0 THEN coalesce({ca}.n_due_desconocido,0) END AS {side}_n_vencimiento_desconocido',
        ])
        if side == 'ar':
            replacements['ar_top1_pct'] = (
                f'CASE WHEN p.meses_observados>=0 AND {stock_ok} THEN p.ar_top1_pct END'
            )
    joins.append('LEFT JOIN cohorte h ON h.company_id=p.company_id AND h.month=p.month')
    for n in (30, 60, 90):
        col = f'coh_pct_cobrado_{n}d'
        replacements[col] = (
            f'CASE WHEN p.meses_observados>=0 AND h.coh_n_eur=h.coh_n_emitidas '
            f'AND h.coh_n_estado_incierto=0 THEN p.{col} END'
        )
    replacements['coh_emitido_eur'] = (
        'CASE WHEN p.meses_observados>=0 AND coalesce(h.coh_n_eur,0)='
        'coalesce(h.coh_n_emitidas,0) THEN p.coh_emitido_eur END'
    )
    extra.append('h.coh_n_eur::DOUBLE/nullif(h.coh_n_emitidas,0) AS coh_cobertura_eur_pct')
    extra.append('h.coh_n_estado_incierto AS coh_n_estado_incierto')
    expressions = ', '.join(f'{expr} AS {col}' for col, expr in replacements.items())
    con.execute(f"CREATE OR REPLACE TEMP TABLE panel_cobro AS "
                f"SELECT p.* REPLACE ({expressions}), {', '.join(extra)} "
                f"FROM panel_cobro p {' '.join(joins)}")
    con.execute('''
        CREATE OR REPLACE TEMP TABLE panel_cobro AS
        WITH history AS (
            SELECT *, (ar_emitido_eur + lag(ar_emitido_eur,1) OVER w
                       + lag(ar_emitido_eur,2) OVER w)/3.0 AS emision_media
            FROM panel_cobro WINDOW w AS (PARTITION BY company_id ORDER BY month)
        )
        SELECT * EXCLUDE (emision_media) REPLACE (
            CASE WHEN meses_observados>=2 AND emision_media>0
                 THEN ar_abierto_eur/emision_media END AS ar_abierto_norm
        ) FROM history ORDER BY company_id, month
    ''')


def cohort_features_at(con, as_of, panel_path=None):
    from datetime import date

    cutoff = date.fromisoformat(str(as_of))
    file = str(panel_path or PANEL_PATH).replace("'", "''")
    selects = [
        f"SELECT company_id, month AS cohort_month, {n} AS horizon_days, "
        f"coh_pct_cobrado_{n}d AS value, coh_{n}d_available_at AS available_at "
        f"FROM read_parquet('{file}') WHERE coh_{n}d_available_at<=? "
        f"AND coh_pct_cobrado_{n}d IS NOT NULL"
        for n in (30, 60, 90)
    ]
    return con.sql(' UNION ALL '.join(selects), params=[cutoff] * 3)


def build_panel_cobro(con: duckdb.DuckDBPyConnection) -> CleanResult:
    """Construye el panel de cartera de cobro/pago y su informe de calidad."""
    # Principio: el stock no se trunca ni se filtra. Una cuenta a cobrar no
    # deja de existir porque envejezca: la antiguedad se conserva y se expone
    # en tramos de aging (hasta 360+ dias) y en mora_mas_180d (informativa,
    # contenida en el abierto; no resta del stock).
    inv_path = paths.CLEAN_DIR / "invoices.parquet"
    comp_path = paths.CLEAN_DIR / "companies.parquet"
    fx_path = paths.MARTS_DIR / "fx_rates.parquet"
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 1. Recuentos de exclusion sobre el dataset completo (funnel secuencial)
    # ------------------------------------------------------------------ #
    rows_in = con.execute(
        f"SELECT count(*) FROM read_parquet('{inv_path}')"
    ).fetchone()[0]

    n_cancel, eur_cancel = con.execute(
        f"""
        SELECT count(*), sum(abs(amount_eur))
        FROM read_parquet('{inv_path}') WHERE status_norm = 'cancel'
        """
    ).fetchone()

    n_doc, eur_doc = con.execute(
        f"""
        SELECT count(*), sum(abs(amount_eur))
        FROM read_parquet('{inv_path}')
        WHERE status_norm <> 'cancel' AND document_type_norm <> '{DOC_INVOICE}'
        """
    ).fetchone()

    n_fecha, eur_fecha = con.execute(
        f"""
        SELECT count(*), sum(abs(amount_eur))
        FROM read_parquet('{inv_path}')
        WHERE status_norm <> 'cancel'
          AND document_type_norm = '{DOC_INVOICE}'
          AND issuance_date_ok IS NULL
        """
    ).fetchone()

    n_sin_eur, eur_sin_eur = con.execute(
        f"""
        SELECT count(*), sum(abs(amount_eur))
        FROM read_parquet('{inv_path}')
        WHERE status_norm <> 'cancel'
          AND document_type_norm = '{DOC_INVOICE}'
          AND issuance_date_ok IS NOT NULL
          AND amount_eur IS NULL
        """
    ).fetchone()

    n_parciales = con.execute(
        f"""
        SELECT count(*)
        FROM read_parquet('{inv_path}')
        WHERE status_norm = 'paid' AND document_type_norm = '{DOC_INVOICE}'
          AND coalesce(pending_amount, 0) <> 0
        """
    ).fetchone()[0]

    n_cero = con.execute(
        f"""
        SELECT count(*)
        FROM read_parquet('{inv_path}')
        WHERE status_norm <> 'cancel'
          AND document_type_norm = '{DOC_INVOICE}'
          AND issuance_date_ok IS NOT NULL
          AND amount = 0
        """
    ).fetchone()[0]

    universo_n, universo_eur = con.execute(
        f"""
        SELECT count(*), sum(abs(amount_eur))
        FROM read_parquet('{inv_path}')
        WHERE status_norm <> 'cancel'
          AND document_type_norm = '{DOC_INVOICE}'
          AND issuance_date_ok IS NOT NULL
        """
    ).fetchone()

    # ------------------------------------------------------------------ #
    # 2. Facturas del universo con fecha_cobro_real (la trampa del spec)
    #    y euros conocidos de clean, sin completar mediante estimaciones.
    #    Las fechas de liquidacion y vencimiento tienen validacion propia.
    # ------------------------------------------------------------------ #
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE inv AS
        SELECT
            i.company_id,
            CAST(i.issuance_date_ok AS DATE)                     AS issu_d,
            CAST(i.maturity_date_ok AS DATE)                     AS due_d,
            CAST(i.settlement_date_ok AS DATE)                   AS cobro_d,
            (i.status_norm = 'unknown'
             OR (i.status_norm = 'paid' AND i.settlement_date_ok IS NULL)
             OR (i.status_norm <> 'paid' AND i.pending_amount IS NOT NULL
                 AND abs(i.pending_amount) < abs(i.amount)))
                                                                AS estado_incierto,
            i.amount,
            i.amount_eur,
            i.accounting_currency,
            i.amount_acct,
            i.counterparty_id,
            CASE WHEN i.amount > 0 THEN 'ar'
                 WHEN i.amount < 0 THEN 'ap' END                 AS side,
            /* importe_eur: solo identidad o conversion reportada demostrable.
               Sin conversion permanece NULL; los totales incompletos no se
               publican como cero ni se completan con tipos estimados. */
            i.amount_eur                                        AS importe_eur,
            i.amount_eur IS NOT NULL                             AS eur_original,
            NULL::DOUBLE                                        AS eur_rescatado
        FROM read_parquet('{inv_path}') i
        WHERE i.status_norm <> 'cancel'
          AND i.document_type_norm = '{DOC_INVOICE}'
          AND i.issuance_date_ok IS NOT NULL
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE companies AS
        SELECT company_id, group_id
        FROM read_parquet('{comp_path}')
        """
    )

    # ------------------------------------------------------------------ #
    # 3. Facturas ABIERTAS a cierre de cada mes cerrado
    #    abierta: issu_d <= fin_de_mes(m) AND (cobro_d IS NULL
    #                                          OR cobro_d > fin_de_mes(m))
    #    vencida: abierta AND due_d < fin_de_mes(m)
    # ------------------------------------------------------------------ #
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE months AS
        SELECT CAST(g.m AS DATE) AS month, last_day(CAST(g.m AS DATE)) AS month_end
        FROM {MONTHS_SQL} g(m)
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE open_rows AS
        SELECT i.company_id, m.month, m.month_end, i.side,
               i.importe_eur, i.due_d, i.counterparty_id, i.estado_incierto,
               date_diff('day', i.due_d, m.month_end) AS dias_desde_due
        FROM inv i
        JOIN months m
          ON i.issu_d <= m.month_end
         AND (i.cobro_d IS NULL OR i.cobro_d > m.month_end)
        """
    )

    # Agregados de cartera por empresa-mes-lado
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE cartera AS
        SELECT
            company_id, month, side,
            sum(abs(importe_eur)) FILTER (WHERE NOT estado_incierto) AS abierto_eur,
            count(*) FILTER (WHERE NOT estado_incierto) AS n_abiertas,
            count(*) AS n_posibles,
            count(importe_eur) AS n_eur,
            count(*) FILTER (WHERE estado_incierto) AS n_estado_incierto,
            count(*) FILTER (WHERE due_d IS NULL) AS n_due_desconocido,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end) AS vencido_eur,
            /* mora > 180 dias: observacion, no juicio contable. Informativa
               y contenida en abierto_eur; NO resta del stock */
            sum(abs(importe_eur)) FILTER (WHERE dias_desde_due > 180)
                AS mora_mas_180d_eur,
            /* tramos de aging sobre el VENCIDO (lo no vencido no esta en
               ningun tramo); due_d NULL no vence nunca: sin tramo */
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due <= 30) AS aging_0_30_eur,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due BETWEEN 31 AND 60) AS aging_31_60_eur,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due BETWEEN 61 AND 90) AS aging_61_90_eur,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due BETWEEN 91 AND 180) AS aging_91_180_eur,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due BETWEEN 181 AND 360) AS aging_181_360_eur,
            sum(abs(importe_eur)) FILTER (WHERE due_d < month_end
                                    AND dias_desde_due > 360) AS aging_360_mas_eur,
            count(DISTINCT counterparty_id) AS n_contrapartes
        FROM open_rows
        GROUP BY company_id, month, side
        """
    )

    # Concentracion AR: saldo abierto por contraparte y top-1
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE ar_top1 AS
        SELECT company_id, month, max(saldo_cp) AS top1_eur
        FROM (
            SELECT company_id, month, counterparty_id, sum(abs(importe_eur)) AS saldo_cp
            FROM open_rows
            WHERE side = 'ar' AND counterparty_id IS NOT NULL
            GROUP BY company_id, month, counterparty_id
        )
        GROUP BY company_id, month
        """
    )

    # ------------------------------------------------------------------ #
    # 4. Flujos del mes: emitido y liquidado
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE emitido AS
        SELECT company_id, date_trunc('month', issu_d)::DATE AS month, side,
               sum(abs(importe_eur)) AS emitido_eur, count(*) AS n_emitidas,
               count(importe_eur) AS n_documentos_eur,
               count(*) AS n_documentos,
               /* euros que entran por conversion implicita y no por el
                  amount_eur original (trazabilidad del rescate fx) */
               coalesce(sum(eur_rescatado), 0) AS eur_rescatado
        FROM inv
        GROUP BY company_id, month, side
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE liquidado AS
        SELECT company_id, date_trunc('month', cobro_d)::DATE AS month, side,
               sum(abs(importe_eur)) AS liquidado_eur,
               count(*) AS n_liquidadas, count(importe_eur) AS n_eur
        FROM inv
        WHERE cobro_d IS NOT NULL
        GROUP BY company_id, month, side
        """
    )

    # ------------------------------------------------------------------ #
    # 4b. Metricas de cohorte de emision (senal principal de disciplina:
    #     inmunes a la acumulacion del stock y a la rampa de alta).
    #     Solo facturas de venta (side='ar') emitidas dentro del mes m.
    #     pct = importe cobrado (paid) en <= N dias / importe emitido en m.
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE cohorte AS
        SELECT
            company_id,
            date_trunc('month', issu_d)::DATE AS month,
            count(*)        AS coh_n_emitidas,
            count(importe_eur) AS coh_n_eur,
            count(*) FILTER (WHERE estado_incierto) AS coh_n_estado_incierto,
            sum(abs(importe_eur)) AS coh_emitido_eur,
            sum(abs(importe_eur)) FILTER (
                WHERE cobro_d IS NOT NULL
                  AND date_diff('day', issu_d, cobro_d) <= 30
            ) AS cobrado_30d,
            sum(abs(importe_eur)) FILTER (
                WHERE cobro_d IS NOT NULL
                  AND date_diff('day', issu_d, cobro_d) <= 60
            ) AS cobrado_60d,
            sum(abs(importe_eur)) FILTER (
                WHERE cobro_d IS NOT NULL
                  AND date_diff('day', issu_d, cobro_d) <= 90
            ) AS cobrado_90d
        FROM inv
        WHERE side = 'ar'
        GROUP BY company_id, date_trunc('month', issu_d)::DATE
        """
    )

    # ------------------------------------------------------------------ #
    # 4c. Calentamiento: primer mes con factura emitida por empresa
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE primer_mes AS
        SELECT company_id, min(month) AS primer_mes
        FROM emitido
        GROUP BY company_id
        """
    )

    # ------------------------------------------------------------------ #
    # 5. Disciplina de cobro: ventanas de 3 meses que acaban en m
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE dso AS
        SELECT
            i.company_id,
            m.month,
            median(date_diff('day', i.issu_d, i.cobro_d)) AS ar_dso_dias,
            median(date_diff('day', i.due_d, i.cobro_d))  AS ar_retraso_medio_dias,
            count(*) AS n_liquidadas,
            count(i.due_d) AS n_con_due
        FROM inv i
        JOIN months m
          ON i.cobro_d >= m.month - INTERVAL 2 MONTH
         AND i.cobro_d <= m.month_end
        WHERE i.side = 'ar'
        GROUP BY i.company_id, m.month
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE inv_companies AS
        /* tiene_erp = la empresa aparece en invoices (cualquier doc/status):
           785 empresas; la cartera usa solo 'invoice' no cancelado (784) */
        SELECT company_id, min(date_trunc('month', issuance_date_ok)) AS primer_mes_erp
        FROM read_parquet('{inv_path}') GROUP BY company_id
        """
    )

    # ------------------------------------------------------------------ #
    # 6. Panel final: 1.286 empresas x 24 meses, NULL si no hay datos
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE panel_cobro AS
        WITH grid AS (
            SELECT c.company_id, c.group_id, m.month
            FROM companies c CROSS JOIN months m
        ),
        erp AS (
            SELECT * FROM inv_companies
        ),
        car_ar AS (
            SELECT * FROM cartera WHERE side = 'ar'
        ),
        car_ap AS (
            SELECT * FROM cartera WHERE side = 'ap'
        ),
        emi_ar AS (
            SELECT company_id, month, emitido_eur, n_documentos, n_documentos_eur, eur_rescatado
            FROM emitido WHERE side = 'ar'
        ),
        emi_ap AS (
            SELECT company_id, month, emitido_eur, n_documentos, n_documentos_eur, eur_rescatado
            FROM emitido WHERE side = 'ap'
        ),
        emi_tot AS (
            SELECT company_id, month,
                   sum(n_emitidas)     AS n_emitidas,
                   sum(n_documentos_eur) AS n_documentos_eur,
                   sum(n_documentos)   AS n_documentos
            FROM emitido
            GROUP BY company_id, month
        ),
        liq_ar AS (
            SELECT company_id, month, liquidado_eur FROM liquidado WHERE side = 'ar'
        ),
        liq_ap AS (
            SELECT company_id, month, liquidado_eur FROM liquidado WHERE side = 'ap'
        ),
        coh AS (
            SELECT * FROM cohorte
        ),
        pm AS (
            SELECT * FROM primer_mes
        )
        SELECT
            g.company_id,
            g.group_id,
            g.month,
            coalesce(g.month >= e.primer_mes_erp, false) AS tiene_erp,

            /* ---------- lado COBRO (AR) ---------- */
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.abierto_eur, 0) END   AS ar_abierto_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.vencido_eur, 0) END   AS ar_vencido_eur,
            CASE WHEN coalesce(car_ar.abierto_eur, 0) > 0
                 THEN car_ar.vencido_eur / car_ar.abierto_eur END AS ar_pct_vencido,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.n_abiertas, 0) END    AS ar_n_abiertas,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_0_30_eur, 0) END AS ar_aging_0_30_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_31_60_eur, 0) END AS ar_aging_31_60_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_61_90_eur, 0) END AS ar_aging_61_90_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_91_180_eur, 0) END AS ar_aging_91_180_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_181_360_eur, 0) END AS ar_aging_181_360_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.aging_360_mas_eur, 0) END AS ar_aging_360_mas_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(emi_ar.emitido_eur, 0) END   AS ar_emitido_eur,
            /* cobertura: fraccion de documentos emitidos convertibles a EUR
               (no se mezclan nominales de distintas monedas) */
            CASE WHEN coalesce(emi_ar.n_documentos, 0) > 0
                 THEN emi_ar.n_documentos_eur::DOUBLE / emi_ar.n_documentos END
                                                        AS ar_cobertura_eur_pct,
            /* Campo de compatibilidad: la fraccion estimada es siempre cero
               bajo la politica conservadora, o NULL sin observacion. */
            CASE WHEN coalesce(emi_ar.emitido_eur, 0) > 0
                 THEN emi_ar.eur_rescatado / emi_ar.emitido_eur END
                                                        AS ar_eur_rescatado_pct,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(liq_ar.liquidado_eur, 0) END AS ar_liquidado_eur,

            /* ---------- lado PAGO (AP) ---------- */
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.abierto_eur, 0) END   AS ap_abierto_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.vencido_eur, 0) END   AS ap_vencido_eur,
            CASE WHEN coalesce(car_ap.abierto_eur, 0) > 0
                 THEN car_ap.vencido_eur / car_ap.abierto_eur END AS ap_pct_vencido,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.n_abiertas, 0) END    AS ap_n_abiertas,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_0_30_eur, 0) END AS ap_aging_0_30_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_31_60_eur, 0) END AS ap_aging_31_60_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_61_90_eur, 0) END AS ap_aging_61_90_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_91_180_eur, 0) END AS ap_aging_91_180_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_181_360_eur, 0) END AS ap_aging_181_360_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.aging_360_mas_eur, 0) END AS ap_aging_360_mas_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(emi_ap.emitido_eur, 0) END   AS ap_emitido_eur,
            CASE WHEN coalesce(emi_ap.n_documentos, 0) > 0
                 THEN emi_ap.n_documentos_eur::DOUBLE / emi_ap.n_documentos END
                                                        AS ap_cobertura_eur_pct,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(liq_ap.liquidado_eur, 0) END AS ap_liquidado_eur,

            /* ---------- disciplina de cobro (solo AR) ---------- */
            CASE WHEN dso.n_liquidadas >= 5 THEN dso.ar_dso_dias END          AS ar_dso_dias,
            CASE WHEN dso.n_con_due >= 5 THEN dso.ar_retraso_medio_dias END
                                                           AS ar_retraso_medio_dias,

            /* ---------- concentracion (solo AR) ---------- */
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.n_contrapartes, 0) END AS ar_n_contrapartes,
            CASE WHEN coalesce(car_ar.abierto_eur, 0) > 0 AND t.top1_eur IS NOT NULL
                 THEN t.top1_eur / car_ar.abierto_eur END   AS ar_top1_pct,

            /* ---------- mora > 180d (informativa, no resta) y cohorte
               de emision: metricas inmunes a la acumulacion ---------- */
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ar.mora_mas_180d_eur, 0) END
                                                        AS ar_mora_mas_180d_eur,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(car_ap.mora_mas_180d_eur, 0) END
                                                        AS ap_mora_mas_180d_eur,

            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(coh.coh_n_emitidas, 0) END   AS coh_n_emitidas,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(coh.coh_emitido_eur, 0) END  AS coh_emitido_eur,
            /* censura por la derecha: la cohorte solo puede evaluarse a N dias
               si fin_de_mes(m) + N <= fin del dataset; sin horizonte = NULL,
               NUNCA cero (un cero fabricaria un desplome falso al final) */
            CASE WHEN e.company_id IS NOT NULL
                  AND coh.coh_emitido_eur > 0
                  AND last_day(g.month) + INTERVAL 30 DAY <= {FIN_DATASET}
                 THEN coalesce(coh.cobrado_30d, 0) / coh.coh_emitido_eur END
                                                        AS coh_pct_cobrado_30d,
            CASE WHEN e.company_id IS NOT NULL
                  AND coh.coh_emitido_eur > 0
                  AND last_day(g.month) + INTERVAL 60 DAY <= {FIN_DATASET}
                 THEN coalesce(coh.cobrado_60d, 0) / coh.coh_emitido_eur END
                                                        AS coh_pct_cobrado_60d,
            CASE WHEN e.company_id IS NOT NULL
                  AND coh.coh_emitido_eur > 0
                  AND last_day(g.month) + INTERVAL 90 DAY <= {FIN_DATASET}
                 THEN coalesce(coh.cobrado_90d, 0) / coh.coh_emitido_eur END
                                                        AS coh_pct_cobrado_90d,

            /* ---------- disponibilidad punto-en-el-tiempo -------------
               contrato para la capa de features (NO filtran nada por si
               solas): la cohorte del mes m solo esta disponible cuando su
               ultima factura ha madurado, fin_de_mes(m) + N dias. Consumir
               solo filas con available_at <= fecha_de_corte; ver docstring. */
            CAST(last_day(g.month) + INTERVAL 30 DAY AS DATE) AS coh_30d_available_at,
            CAST(last_day(g.month) + INTERVAL 60 DAY AS DATE) AS coh_60d_available_at,
            CAST(last_day(g.month) + INTERVAL 90 DAY AS DATE) AS coh_90d_available_at,

            /* ---------- normalizacion por escala -----------------------
               emision media mensual de los 3 meses que acaban en m */
            CASE WHEN (coalesce(emi_ar.emitido_eur, 0)
                        + coalesce(em1.emitido_eur, 0)
                        + coalesce(em2.emitido_eur, 0)) / 3.0 > 0
                 THEN coalesce(car_ar.abierto_eur, 0)
                      / ((coalesce(emi_ar.emitido_eur, 0)
                          + coalesce(em1.emitido_eur, 0)
                          + coalesce(em2.emitido_eur, 0)) / 3.0) END
                                                        AS ar_abierto_norm,

            /* ---------- calentamiento ----------------------------------
               meses_observados = 0 en el primer mes con factura */
            CASE WHEN pm.primer_mes IS NOT NULL
                 THEN date_diff('month', pm.primer_mes, g.month) END
                                                        AS meses_observados,
            CASE WHEN pm.primer_mes IS NOT NULL
                 THEN date_diff('month', pm.primer_mes, g.month) < 3 END
                                                        AS es_calentamiento,

            /* ---------- calidad ---------- */
            CASE WHEN emi_tot.n_emitidas > 0 AND emi_tot.n_documentos > 0
                 THEN emi_tot.n_documentos_eur / emi_tot.n_documentos END
                                                           AS cobertura_eur_pct,
            CASE WHEN e.company_id IS NOT NULL
                 THEN coalesce(emi_tot.n_emitidas, 0) END   AS n_facturas_mes
        FROM grid g
        LEFT JOIN erp e     ON e.company_id = g.company_id
        LEFT JOIN car_ar    ON car_ar.company_id = g.company_id
                           AND car_ar.month = g.month
        LEFT JOIN car_ap    ON car_ap.company_id = g.company_id
                           AND car_ap.month = g.month
        LEFT JOIN emi_ar    ON emi_ar.company_id = g.company_id
                           AND emi_ar.month = g.month
        LEFT JOIN emi_ap    ON emi_ap.company_id = g.company_id
                           AND emi_ap.month = g.month
        LEFT JOIN liq_ar    ON liq_ar.company_id = g.company_id
                           AND liq_ar.month = g.month
        LEFT JOIN liq_ap    ON liq_ap.company_id = g.company_id
                           AND liq_ap.month = g.month
        LEFT JOIN dso       ON dso.company_id = g.company_id
                           AND dso.month = g.month
        LEFT JOIN ar_top1 t ON t.company_id = g.company_id
                           AND t.month = g.month
        LEFT JOIN emi_tot   ON emi_tot.company_id = g.company_id
                           AND emi_tot.month = g.month
        /* emision AR de los 2 meses previos, para la media movil de 3m */
        LEFT JOIN emi_ar em1 ON em1.company_id = g.company_id
                            AND em1.month = g.month - INTERVAL 1 MONTH
        LEFT JOIN emi_ar em2 ON em2.company_id = g.company_id
                            AND em2.month = g.month - INTERVAL 2 MONTH
        LEFT JOIN coh        ON coh.company_id = g.company_id
                            AND coh.month = g.month
        LEFT JOIN pm         ON pm.company_id = g.company_id
        ORDER BY g.company_id, g.month
        """.replace("{FIN_DATASET}", FIN_DATASET)
    )

    _complete_panel(con)
    rows_out = con.execute("SELECT count(*) FROM panel_cobro").fetchone()[0]
    n_emp_erp = con.execute(
        "SELECT count(DISTINCT company_id) FROM panel_cobro WHERE tiene_erp"
    ).fetchone()[0]
    n_emp_sin = con.execute(
        "SELECT count(*) FROM companies WHERE company_id NOT IN "
        "(SELECT company_id FROM panel_cobro WHERE tiene_erp)"
    ).fetchone()[0]
    dup = con.execute(
        """
        SELECT count(*) FROM (
            SELECT company_id, month FROM panel_cobro
            GROUP BY company_id, month HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    if dup:
        raise ValueError(f"panel_cobro: {dup} claves (company_id, month) duplicadas")

    con.execute(
        f"COPY (SELECT * FROM panel_cobro) TO '{PANEL_PATH}' (FORMAT PARQUET)"
    )

    # ------------------------------------------------------------------ #
    # 7. Informe de calidad
    # ------------------------------------------------------------------ #
    n_dso_null = con.execute(
        "SELECT count(*) FROM panel_cobro WHERE ar_dso_dias IS NULL"
    ).fetchone()[0]
    n_dso_null_sin_erp = con.execute(
        "SELECT count(*) FROM panel_cobro WHERE ar_dso_dias IS NULL AND NOT tiene_erp"
    ).fetchone()[0]
    # Rescate fx: facturas que entran en euros por conversion implicita
    fx_rescatadas, fx_rescatado_eur = con.execute(
        """
        SELECT count(*), sum(eur_rescatado) FROM inv WHERE eur_rescatado IS NOT NULL
        """
    ).fetchone()
    # mismo rescate sobre el DATASET COMPLETO (incluye cancel / no invoice /
    # fecha invalida, que nunca entran en el panel): referencia del criterio
    fx_ds = (0, 0.0)
    fx_sin_tipo = con.execute(
        """
        SELECT accounting_currency, count(*) AS n, sum(abs(amount_acct)) AS abs_local
        FROM inv
        WHERE importe_eur IS NULL AND amount_eur IS NULL
          AND amount_acct IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    fx_sin_acct = con.execute(
        """
        SELECT count(*) FROM inv
        WHERE importe_eur IS NULL AND amount_eur IS NULL AND amount_acct IS NULL
        """
    ).fetchone()[0]

    n_dso_null_menos5 = n_dso_null - n_dso_null_sin_erp

    # Cohortes: media entre empresas de cada pct y recuento de empresas-mes
    # con NULL POR CENSURA (horizonte sin completar), no por falta de emision
    serie_coh = con.execute(
        f"""
        SELECT strftime(month, '%Y-%m') AS mes,
               avg(coh_pct_cobrado_30d) AS pct_30d,
               count(*) FILTER (WHERE coh_emitido_eur > 0
                                 AND coh_30d_available_at > {FIN_DATASET}) AS null_30d,
               avg(coh_pct_cobrado_60d) AS pct_60d,
               count(*) FILTER (WHERE coh_emitido_eur > 0
                                 AND coh_60d_available_at > {FIN_DATASET}) AS null_60d,
               avg(coh_pct_cobrado_90d) AS pct_90d,
               count(*) FILTER (WHERE coh_emitido_eur > 0
                                 AND coh_90d_available_at > {FIN_DATASET}) AS null_90d
        FROM panel_cobro
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    serie_cohortes = [
        {
            "mes": r[0],
            "coh_pct_cobrado_30d": r[1], "n_null_censura_30d": r[2],
            "coh_pct_cobrado_60d": r[3], "n_null_censura_60d": r[4],
            "coh_pct_cobrado_90d": r[5], "n_null_censura_90d": r[6],
        }
        for r in serie_coh
    ]

    # Reparto por tramos de antiguedad en el ultimo mes (eur y % del vencido)
    tramos_ultimo = con.execute(
        """
        WITH agg AS (
            SELECT
                sum(ar_vencido_eur) AS ar_vencido,
                sum(ar_aging_0_30_eur)   AS ar_t1,
                sum(ar_aging_31_60_eur)  AS ar_t2,
                sum(ar_aging_61_90_eur)  AS ar_t3,
                sum(ar_aging_91_180_eur) AS ar_t4,
                sum(ar_aging_181_360_eur) AS ar_t5,
                sum(ar_aging_360_mas_eur) AS ar_t6,
                sum(ap_vencido_eur) AS ap_vencido,
                sum(ap_aging_0_30_eur)   AS ap_t1,
                sum(ap_aging_31_60_eur)  AS ap_t2,
                sum(ap_aging_61_90_eur)  AS ap_t3,
                sum(ap_aging_91_180_eur) AS ap_t4,
                sum(ap_aging_181_360_eur) AS ap_t5,
                sum(ap_aging_360_mas_eur) AS ap_t6
            FROM panel_cobro
            WHERE month = (SELECT max(month) FROM panel_cobro)
        )
        SELECT ar_vencido, ar_t1, ar_t2, ar_t3, ar_t4, ar_t5, ar_t6,
               ap_vencido, ap_t1, ap_t2, ap_t3, ap_t4, ap_t5, ap_t6
        FROM agg
        """
    ).fetchone()

    def _tramos(prefix: str, vals: tuple) -> dict:
        vencido = vals[0]
        nombres = ["aging_0_30", "aging_31_60", "aging_61_90",
                   "aging_91_180", "aging_181_360", "aging_360_mas"]
        out = {"vencido_eur": vencido}
        for i, n in enumerate(nombres, start=1):
            eur = vals[i]
            out[f"{n}_eur"] = eur
            out[f"{n}_pct"] = (eur / vencido) if vencido else None
        return out

    report = {
        "panel": {
            "filas": rows_out,
            "empresas": con.execute('SELECT count(*) FROM companies').fetchone()[0],
            "meses": con.execute('SELECT count(*) FROM months').fetchone()[0],
            "grano": "(company_id, month), meses cerrados 2024-09..2026-08",
            "duplicados_clave": dup,
        },
        "empresas": {
            "con_erp": n_emp_erp,
            "sin_erp": n_emp_sin,
            "sin_erp_nota": "metricas NULL y tiene_erp=FALSE; no se rellenan con ceros",
        },
        "facturas": {
            "rows_in_dataset": rows_in,
            "universo_cartera": universo_n,
            "universo_eur_conocido_abs": universo_eur,
            "excluidas": {
                "cancel": {"n": n_cancel, "eur_conocido_abs": eur_cancel,
                           "motivo": "factura cancelada: ni cobro ni deuda"},
                "tipo_no_invoice": {"n": n_doc, "eur_conocido_abs": eur_doc,
                                    "motivo": "document_type_norm <> 'invoice'"},
                "fecha_invalida": {"n": n_fecha, "eur_conocido_abs": eur_fecha,
                                   "motivo": "issuance_date_ok NULL; otras fechas no eliminan la factura"},
                "sin_amount_eur": {"n": n_sin_eur, "eur_conocido_abs": eur_sin_eur,
                                   "motivo": "sin conversion demostrable; totales monetarios incompletos a NULL, "
                                             "subtotales conocidos y conteos separados"},
                "amount_cero": {"n": n_cero,
                                "motivo": "amount = 0: sin lado AR/AP; solo cuenta en "
                                          "n_facturas_mes"},
            },
            "pagos_parciales_marcados": {
                "n": n_parciales,
                "nota": "status paid con pending_amount <> 0: fecha de liquidacion desconocida, "
                        "sin inventar liquidacion unica",
            },
        },
        "serie_mensual": _serie_temporal(con),
        "aging_ultimo_mes": {
            "mes": str(con.execute(
                "SELECT strftime(max(month), '%Y-%m') FROM panel_cobro"
            ).fetchone()[0]),
            "ar": _tramos("ar", tramos_ultimo[:7]),
            "ap": _tramos("ap", tramos_ultimo[7:]),
            "nota": "los seis tramos de aging suman el importe VENCIDO del lado "
                    "(lo no vencido no esta en ningun tramo)",
        },
        "cohortes_cobro": {
            "definicion": "por (company_id, mes de emision): pct del importe "
                          "emitido en m cobrado (status paid) en <= N dias desde "
                          "emision; facturas de venta, doc_type invoice, no cancel",
            "censura": "NULL si fin_de_mes(emision) + N dias > 2026-08-31 "
                       "(sin horizonte completo); NULL tambien si "
                       "coh_emitido_eur = 0 (sin amount_eur); NUNCA cero por censura",
            "disponibilidad": "columnas coh_{30,60,90}d_available_at = "
                              "fin_de_mes(emision) + N dias: no filtran nada, "
                              "contrato para que la capa de features consuma solo "
                              "lo que cumpla available_at <= fecha_de_corte; la "
                              "censura al cierre se mantiene aparte",
            "serie_mensual": serie_cohortes,
        },
        "disciplina_cobro": {
            "ar_dso_dias": _distribucion(con, "ar_dso_dias"),
            "ar_retraso_medio_dias": _distribucion(con, "ar_retraso_medio_dias"),
            "filas_con_ar_dso_null": n_dso_null,
            "motivos_null": {
                "empresa_sin_erp": n_dso_null_sin_erp,
                "menos_de_5_liquidadas_en_ventana_3m": n_dso_null_menos5,
            },
        },
        "conversion": {
            "politica": "unknown_no_estimates",
            "coberturas": "fraccion de filas convertibles, no ponderacion entre distintas monedas",
            "sin_eur_por_moneda_contable": [
                {"moneda": r[0], "n": r[1], "nominal_contable_abs": r[2]}
                for r in fx_sin_tipo
            ],
            "sin_amount_acct": fx_sin_acct,
        },
        "notas": [
            "Totales NULL cuando hay importes o estados desconocidos; subtotales conocidos separados.",
            "Las sumas de la serie y del aging cubren solo empresas-mes con el total observable; no son el universo completo.",
            "Vencimiento desconocido no equivale a no vencido: invalida el total de aging, no la emision.",
            "La continuidad de ERP tras la primera factura es inferida, no una prueba de conexion bancaria o ERP.",
            "Reconstruccion retrospectiva: faltan versiones de cancelaciones, fecha de importacion y pagos parciales.",
            "Los stocks historicos no se exportan como features point-in-time; las cohortes se consultan con cohort_features_at.",
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    return CleanResult(
        table="panel_cobro",
        row_preserving=False,
        rows_in=rows_in,
        rows_out=rows_out,
        output_path=PANEL_PATH,
        flag_counts={
            "excluidas_cancel": n_cancel,
            "excluidas_tipo_no_invoice": n_doc,
            "excluidas_fecha_invalida": n_fecha,
            "sin_amount_eur": n_sin_eur,
            "amount_cero": n_cero,
            "pagos_parciales_marcados": n_parciales,
        },
        notes=[
            "fecha_cobro_real = settlement_date_ok: solo paid sin pendiente y fecha coherente; "
            "payment_date_ok crudo nunca se usa para retraso ni cartera",
            "cartera limitada a document_type_norm='invoice'; el resto de tipos queda "
            "fuera a la espera de decision del coordinador",
            "grano completo 1286 empresas x 24 meses; empresas sin facturas con "
            "metricas NULL y tiene_erp=FALSE",
            "FX conservador: sin tipos estimados. Totales incompletos NULL y "
            "subtotales conocidos separados; cobertura por numero de documentos",
            "coh_{30,60,90}d_available_at: contrato de disponibilidad para la "
            "capa de features (no filtran); la censura al cierre se mantiene",
        ],
    )
