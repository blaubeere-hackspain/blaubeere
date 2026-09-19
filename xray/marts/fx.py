"""Registro de evidencia FX, no tabla de cambios estimados.

Politica unknown_no_estimates: solo EUR tiene rate_por_eur=1 por identidad.
Para otras monedas se conserva el recuento de observaciones de cada mes,
con rate_por_eur=NULL y fuente='unknown'. No se rellena con evidencia futura.
La limpieza usa exclusivamente identidad o el cambio reportado en cada hecho;
los tipos ambiguos permanecen desconocidos. panel_cobro no consume esta tabla.
"""

from __future__ import annotations

import json

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.period import FIRST_MONTH, LAST_MONTH

FX_PATH = paths.MARTS_DIR / "fx_rates.parquet"
REPORT_PATH = paths.REPORTS_DIR / "fx_rates.json"

# Meses cerrados del dataset (mismos que panel_cobro).
INICIO = f"DATE '{FIRST_MONTH}'"
FIN = f"DATE '{LAST_MONTH}'"

# Umbral descriptivo heredado para el resumen de observaciones mensuales.
# No autoriza a estimar tipos ni a completar una conversion desconocida.
MIN_OBS_MES = 10
# Umbral descriptivo del volumen de evidencia en el informe de diagnostico.
# No condiciona las filas mensuales publicadas ni permite usar datos futuros
# para asignar un tipo de cambio.
MIN_OBS_MONEDA = 10


def build_fx_rates(con: duckdb.DuckDBPyConnection) -> CleanResult:
    """Publica evidencia mensual sin estimar tipos de cambio."""
    B = paths.CLEAN_DIR / "banking_products.parquet"
    D = paths.CLEAN_DIR / "debt_products.parquet"
    T = paths.CLEAN_DIR / "transactions.parquet"
    FX_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. Observaciones validas: moneda del producto unida por product_id
    #    (banking y debt tienen espacios de id disjuntos), filtro critico
    #    rate > 0, rate <> 1, is_booked, meses cerrados.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fx_txn AS
        SELECT coalesce(b.currency_norm, d.currency_norm) AS moneda,
               t.month,
               t.exchange_rate
        FROM read_parquet('{T}') t
        LEFT JOIN read_parquet('{B}') b ON t.product_id = b.product_id
        LEFT JOIN read_parquet('{D}') d ON t.product_id = d.product_id
        WHERE t.is_booked
          AND t.exchange_rate > 0
          AND t.exchange_rate <> 1
          AND coalesce(b.currency_norm, d.currency_norm) IS NOT NULL
          AND t.month >= {INICIO} AND t.month <= {FIN}
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE months AS
        SELECT CAST(g.m AS DATE) AS month
        FROM generate_series({INICIO}, {FIN}, INTERVAL 1 MONTH) g(m)
        """
    )

    # 2. Recuentos mensuales y globales; los tipos permanecen desconocidos
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE fx_mensual AS
        SELECT moneda, month,
               NULL::DOUBLE AS rate_mes,
               count(*) AS n_mes
        FROM fx_txn
        GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE fx_global AS
        SELECT moneda,
               NULL::DOUBLE AS rate_global,
               count(*) AS n_obs_global
        FROM fx_txn
        GROUP BY 1
        """
    )

    # 3. Solo evidencia del propio mes para monedas extranjeras, sin relleno.
    #    EUR va por identidad en el calendario; no se estima a partir de otros
    #    movimientos ni del volumen de observaciones futuras.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fx_rates AS
        SELECT moneda, month, NULL::DOUBLE AS rate_por_eur,
               n_mes::BIGINT AS n_obs, 'unknown'::VARCHAR AS fuente,
               last_day(month) AS available_at
        FROM fx_mensual WHERE moneda <> 'EUR'
        UNION ALL
        SELECT 'EUR', g.month, 1.0, 0, 'identidad', g.month
        FROM months g
        ORDER BY moneda, month
        """
    )

    rows_out = con.execute("SELECT count(*) FROM fx_rates").fetchone()[0]

    con.execute(f"COPY (SELECT * FROM fx_rates) TO '{FX_PATH}' (FORMAT PARQUET)")

    # ------------------------------------------------------------------ #
    # 4. Informe de calidad
    # ------------------------------------------------------------------ #
    monedas = con.execute(
        f"""
        SELECT g.moneda, g.rate_global, g.n_obs_global,
               sum(CASE WHEN f.fuente = 'mensual' THEN 1 ELSE 0 END) AS meses_mensual,
               sum(CASE WHEN f.fuente = 'global'  THEN 1 ELSE 0 END) AS meses_global
        FROM fx_global g
        LEFT JOIN fx_rates f ON f.moneda = g.moneda
        WHERE g.n_obs_global >= {MIN_OBS_MONEDA} OR g.moneda = 'EUR'
        GROUP BY 1, 2, 3
        ORDER BY 2
        """
    ).fetchall()
    # EUR va por identidad: su mediana 'global' no es un tipo deducido
    monedas = [(m, 1.0, n, mm, mg) if m == 'EUR' else (m, r, n, mm, mg)
               for (m, r, n, mm, mg) in monedas]

    excluidas = con.execute(
        f"""
        SELECT g.moneda, g.n_obs_global
        FROM fx_global g
        WHERE g.n_obs_global < {MIN_OBS_MONEDA}
        ORDER BY 2 DESC
        """
    ).fetchall()

    # Checks del criterio de hecho
    eur_ok = con.execute(
        """
        SELECT count(*) = 24 FROM fx_rates
        WHERE moneda = 'EUR' AND rate_por_eur = 1.0
        """
    ).fetchone()[0]
    rate1_extranjero = con.execute(
        """
        SELECT count(*) FROM (
            SELECT moneda, median(exchange_rate) AS med
            FROM fx_txn GROUP BY 1
            HAVING moneda <> 'EUR' AND med = 1.0
        )
        """
    ).fetchone()[0]

    report = {
        "tabla": {
            "filas": rows_out,
            "grano": "(moneda, month), identidad EUR y evidencia mensual sin estimaciones",
            "columnas": ["moneda", "month", "rate_por_eur", "n_obs", "fuente", "available_at"],
        },
        "politica": "unknown_no_estimates",
        "advertencia": "Solo identidad EUR. Las monedas extranjeras no tienen un tipo "
                       "aplicable estimado; panel_cobro no consume esta tabla.",
        "monedas_observadas": [
            {"moneda": row[0], "n_obs": row[1]}
            for row in con.execute(
                "SELECT moneda, sum(n_obs) FROM fx_rates GROUP BY 1 ORDER BY 1"
            ).fetchall()
        ],
        "checks": {
            "eur_identidad_24_meses": bool(eur_ok),
            "tipos_extranjeros_estimados": con.execute(
                "SELECT count(*) FROM fx_rates WHERE moneda<>'EUR' AND rate_por_eur IS NOT NULL"
            ).fetchone()[0],
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    return CleanResult(
        table="fx_rates",
        row_preserving=False,
        rows_in=con.execute("SELECT count(*) FROM fx_txn").fetchone()[0],
        rows_out=rows_out,
        output_path=FX_PATH,
        flag_counts={
            "monedas_con_10_observaciones": len(monedas),
            "monedas_con_pocas_observaciones": len(excluidas),
            "rows_unknown": con.execute(
                "SELECT count(*) FROM fx_rates WHERE fuente = 'unknown'"
            ).fetchone()[0],
            "rows_identidad_eur": con.execute(
                "SELECT count(*) FROM fx_rates WHERE fuente = 'identidad'"
            ).fetchone()[0],
        },
        notes=[
            "Politica unknown_no_estimates: solo identidad EUR",
            "Las observaciones extranjeras conservan recuento mensual, nunca un tipo estimado",
            "No hay relleno global ni conversion de facturas desde esta tabla",
        ],
    )
