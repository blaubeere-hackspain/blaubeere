"""Mart: observabilidad del objetivo de deficit operativo a 3 meses.

Grano: (company_id, month) para las 1.286 empresas x los 24 meses cerrados
2024-09..2026-08 = 30.864 filas. Las empresas sin transacciones contabilizadas
en meses cerrados salen igualmente, con tiene_actividad = FALSE: su ausencia
de datos es informacion, no un hueco que tapar.

Que calcula: DONDE el objetivo a 3 meses SERIA observable, no el objetivo en
si (eso requiere panel_flujos, que no existe todavia). Una fila es
objetivo_observable cuando la empresa tiene actividad en el mes m, los tres
meses futuros (m+1, m+2, m+3) estan dentro del periodo cerrado Y esa misma
empresa tiene actividad en los tres, y la empresa ya tiene >= 3 meses de
historia (no es calentamiento). Con datos cerrados hasta 2026-08, el ultimo
mes evaluable es 2026-05: junio, julio y agosto de 2026 nunca tienen
horizonte completo.

Fuente: data/clean/transactions.parquet (meses cerrados, is_booked),
data/interim/tx_flow_class.parquet (via _src_row) y data/clean/companies.parquet.
Salida: data/marts/observabilidad.parquet y reports/quality/observabilidad.json.
"""

from __future__ import annotations

import json

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.period import MONTHS_SQL

OUT_PATH = paths.MARTS_DIR / "observabilidad.parquet"
REPORT_PATH = paths.REPORTS_DIR / "observabilidad.json"


def _percentile(sorted_vals: list[int], q: float) -> int | None:
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo, hi = int(idx // 1), int(-(-idx // 1))  # floor y ceil
    if lo == hi:
        return sorted_vals[lo]
    return int(round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)))


def _serie_por_mes(con: duckdb.DuckDBPyConnection) -> list[dict]:
    # Los 24 meses del grano, con ceros donde nadie es observable: la ausencia
    # tambien es informacion (2026-06..2026-08 no pueden serlo nunca).
    filas = con.execute(
        """
        SELECT strftime(g.month, '%Y-%m') AS mes,
               count(o.company_id) AS empresas_observables
        FROM (SELECT DISTINCT month FROM observabilidad) g
        LEFT JOIN observabilidad o
            ON o.month = g.month AND o.objetivo_observable
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [{"mes": m, "empresas_observables": n} for m, n in filas]


def _distribucion(con: duckdb.DuckDBPyConnection, col: str) -> dict:
    vals = [
        r[0]
        for r in con.execute(
            f"SELECT {col} FROM observabilidad "
            f"WHERE objetivo_observable AND {col} IS NOT NULL"
        ).fetchall()
    ]
    s = sorted(vals)
    return {
        "n": len(s),
        "min": s[0] if s else None,
        "p25": _percentile(s, 0.25),
        "mediana": _percentile(s, 0.50),
        "p75": _percentile(s, 0.75),
        "max": s[-1] if s else None,
    }


def build_observabilidad(con: duckdb.DuckDBPyConnection) -> CleanResult:
    """Construye la vista de cobertura del objetivo y su informe de calidad."""
    tx_path = paths.CLEAN_DIR / "transactions.parquet"
    fx_path = paths.INTERIM_DIR / "tx_flow_class.parquet"
    comp_path = paths.CLEAN_DIR / "companies.parquet"
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 1. Grano completo: empresa x mes cerrado, SIN filtrar empresas sin datos
    # ------------------------------------------------------------------ #
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE obs_grano AS
        WITH meses_cerrados AS (
            SELECT m::DATE AS month
            FROM {MONTHS_SQL} g(m)
        ),
        base AS (
            SELECT c.company_id, c.group_id, m.month
            FROM read_parquet('{comp_path}') c
            CROSS JOIN meses_cerrados m
        ),
        actividad AS (
            -- Cobertura observada en el mes m: solo meses cerrados y contabilizados.
            SELECT
                t.company_id,
                t.month,
                count(*) AS n_tx,
                count(DISTINCT t.product_id) AS n_cuentas_activas,
                100.0 * avg(CASE WHEN coalesce(f.flow_class, 'unknown') = 'unknown' THEN 1.0 ELSE 0.0 END) AS pct_unknown,
                100.0 * avg(CASE WHEN t.amount_eur IS NULL THEN 1.0 ELSE 0.0 END) AS pct_sin_convertir
            FROM read_parquet('{tx_path}') t
            LEFT JOIN read_parquet('{fx_path}') f USING (_src_row, transaction_id)
            WHERE NOT t.is_open_month AND t.is_booked
            GROUP BY 1, 2
        ),
        historia AS (
            SELECT company_id, min(month) AS primer_mes_con_tx, max(month) AS ultimo_mes_con_tx
            FROM actividad
            GROUP BY 1
        )
        SELECT
            b.company_id,
            b.group_id,
            b.month,
            COALESCE(a.n_tx, 0) AS n_tx,
            (COALESCE(a.n_tx, 0) > 0) AS tiene_actividad,
            COALESCE(a.n_cuentas_activas, 0) AS n_cuentas_activas,
            a.pct_unknown AS pct_filas_sin_clasificar,
            a.pct_sin_convertir AS pct_filas_sin_convertir,
            h.primer_mes_con_tx,
            h.ultimo_mes_con_tx,
            CASE
                WHEN h.primer_mes_con_tx IS NULL THEN NULL
                WHEN b.month < h.primer_mes_con_tx THEN NULL
                ELSE date_diff('month', h.primer_mes_con_tx, b.month)
            END AS meses_observados,
            (b.month >= h.primer_mes_con_tx) AS en_rango_hist
        FROM base b
        LEFT JOIN actividad a USING (company_id, month)
        LEFT JOIN historia h USING (company_id)
        """
    )

    # ------------------------------------------------------------------ #
    # 2. Historia acumulada y mirada hacia delante (ventanas sobre el grid
    #    completo, asi los lead corresponden a los meses futuros exactos)
    # ------------------------------------------------------------------ #
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE obs_ventanas AS
        SELECT
            *,
            sum(CASE WHEN tiene_actividad AND en_rango_hist THEN 1 ELSE 0 END)
                OVER (PARTITION BY company_id ORDER BY month
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                AS meses_con_actividad_hasta_m,
            lead(tiene_actividad, 1) OVER w AS act_m1,
            lead(tiene_actividad, 2) OVER w AS act_m2,
            lead(tiene_actividad, 3) OVER w AS act_m3
        FROM obs_grano
        WINDOW w AS (PARTITION BY company_id ORDER BY month)
        """
    )

    # ------------------------------------------------------------------ #
    # 3. Bandera final de observabilidad y motivo de no observabilidad.
    #    El fin del periodo cerrado viene de los datos (max mes cerrado).
    # ------------------------------------------------------------------ #
    con.execute(
        f"""
        COPY (
            WITH u AS (SELECT max(month) AS fin_cerrado FROM obs_grano)
            SELECT
                v.company_id,
                v.group_id,
                CAST(v.month AS DATE) AS month,
                CAST(v.n_tx AS BIGINT) AS n_tx,
                CAST(v.tiene_actividad AS BOOLEAN) AS tiene_actividad,
                CAST(v.n_cuentas_activas AS BIGINT) AS n_cuentas_activas,
                CAST(v.pct_filas_sin_clasificar AS DOUBLE) AS pct_filas_sin_clasificar,
                CAST(v.pct_filas_sin_convertir AS DOUBLE) AS pct_filas_sin_convertir,
                CAST(v.primer_mes_con_tx AS DATE) AS primer_mes_con_tx,
                CAST(v.ultimo_mes_con_tx AS DATE) AS ultimo_mes_con_tx,
                CAST(v.meses_observados AS BIGINT) AS meses_observados,
                CAST(v.meses_con_actividad_hasta_m AS BIGINT) AS meses_con_actividad_hasta_m,
                CAST(CASE WHEN v.meses_observados IS NULL THEN NULL
                          ELSE v.meses_observados < 3 END AS BOOLEAN) AS es_calentamiento,
                CAST(v.month <= u.fin_cerrado - INTERVAL 3 MONTH AS BOOLEAN) AS horizonte_completo,
                CAST(
                    v.month <= u.fin_cerrado - INTERVAL 3 MONTH
                    AND COALESCE(v.act_m1, FALSE)
                    AND COALESCE(v.act_m2, FALSE)
                    AND COALESCE(v.act_m3, FALSE)
                AS BOOLEAN) AS horizonte_con_actividad,
                CAST(
                    v.tiene_actividad
                    AND v.month <= u.fin_cerrado - INTERVAL 3 MONTH
                    AND COALESCE(v.act_m1, FALSE)
                    AND COALESCE(v.act_m2, FALSE)
                    AND COALESCE(v.act_m3, FALSE)
                    AND NOT COALESCE(CASE WHEN v.meses_observados IS NULL THEN NULL
                                          ELSE v.meses_observados < 3 END, FALSE)
                AS BOOLEAN) AS objetivo_observable,
                CASE
                    WHEN v.tiene_actividad
                         AND v.month <= u.fin_cerrado - INTERVAL 3 MONTH
                         AND COALESCE(v.act_m1, FALSE)
                         AND COALESCE(v.act_m2, FALSE)
                         AND COALESCE(v.act_m3, FALSE)
                         AND COALESCE(CASE WHEN v.meses_observados IS NULL THEN NULL
                                           ELSE v.meses_observados < 3 END, FALSE) = FALSE
                    THEN NULL
                    WHEN NOT v.tiene_actividad THEN 'sin_actividad'
                    WHEN COALESCE(CASE WHEN v.meses_observados IS NULL THEN NULL
                                       ELSE v.meses_observados < 3 END, TRUE)
                    THEN 'calentamiento'
                    WHEN NOT v.month <= u.fin_cerrado - INTERVAL 3 MONTH THEN 'sin_horizonte'
                    ELSE 'horizonte_incompleto'
                END AS motivo_no_observable
            FROM obs_ventanas v
            CROSS JOIN u
        ) TO '{OUT_PATH}' (FORMAT PARQUET)
        """
    )

    # Tabla de trabajo para el informe, desde el parquet recien escrito.
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE observabilidad AS "
        f"SELECT * FROM read_parquet('{OUT_PATH}')"
    )

    # ------------------------------------------------------------------ #
    # 4. Informe de calidad
    # ------------------------------------------------------------------ #
    n_total = con.execute("SELECT count(*) FROM observabilidad").fetchone()[0]
    n_observables = con.execute(
        "SELECT count(*) FROM observabilidad WHERE objetivo_observable"
    ).fetchone()[0]
    n_empresas_total = con.execute(
        "SELECT count(DISTINCT company_id) FROM observabilidad"
    ).fetchone()[0]
    n_empresas_observables = con.execute(
        "SELECT count(DISTINCT company_id) FROM observabilidad WHERE objetivo_observable"
    ).fetchone()[0]
    motivos = dict(
        con.execute(
            """
            SELECT motivo_no_observable, count(*)
            FROM observabilidad
            WHERE motivo_no_observable IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """
        ).fetchall()
    )

    reporte = {
        "mart": "observabilidad",
        "grano": "(company_id, month) — empresas x meses cerrados 2024-09..2026-08",
        "total_filas_empresa_mes": n_total,
        "filas_objetivo_observable": n_observables,
        "pct_objetivo_observable": round(100.0 * n_observables / n_total, 2),
        "empresas_totales": n_empresas_total,
        "empresas_con_alguna_fila_observable": n_empresas_observables,
        "reparto_motivo_no_observable": motivos,
        "serie_mensual_empresas_observables": _serie_por_mes(con),
        "distribucion_meses_con_actividad_en_observables": _distribucion(
            con, "meses_con_actividad_hasta_m"
        ),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(reporte, indent=2, ensure_ascii=False) + "\n")

    return CleanResult(
        table="observabilidad",
        row_preserving=False,
        rows_in=n_total,
        rows_out=n_total,
        output_path=OUT_PATH,
        flag_counts=motivos,
        notes=[
            "Vista de cobertura: NO calcula el objetivo, solo donde seria observable.",
            "Calendario centralizado en xray.period; horizonte futuro a 3 meses.",
        ],
    )
