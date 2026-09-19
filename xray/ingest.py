"""Capa de ingesta: CSV crudo -> parquet interim tipado de forma tolerante.

Principio rector: la ingesta NO aplica ninguna regla de negocio. Solo lee,
tipa con TRY_CAST y deja constancia de donde el tipado fallo en
_cast_failures. Ninguna fila se pierde nunca: rows_in == rows_out.
"""

import time

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.schema import TABLES, ColumnSpec, TableSpec


def _cast_expr(col: ColumnSpec) -> str:
    q = f'"{col.name}"'
    if col.dtype == "VARCHAR":
        # Ya viene como VARCHAR por all_varchar=true; no puede fallar el cast.
        return q
    if col.dtype == "TIMESTAMP":
        return (
            f"COALESCE("
            f"TRY_CAST({q} AS TIMESTAMP), "
            f"try_strptime({q}, '%Y-%m-%d %H:%M:%S'), "
            f"try_strptime({q}, '%Y-%m-%d'))"
        )
    return f"TRY_CAST({q} AS {col.dtype})"


def _is_present(col: str) -> str:
    # NULL y cadena vacia son ausencia legitima, no fallo de cast.
    return f"({col} IS NOT NULL AND {col} <> '')"


def ingest_table(con: duckdb.DuckDBPyConnection, spec: TableSpec) -> CleanResult:
    csv_path = paths.RAW_DIR / spec.filename
    out_path = paths.INTERIM_DIR / f"{spec.name}.parquet"

    # 1-2) Lectura cruda todo-VARCHAR con numero de fila de origen (1-indexado
    # sobre filas de datos, sin cabecera).
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _raw AS
        SELECT row_number() OVER () AS _src_row, *
        FROM read_csv('{csv_path.as_posix()}',
                      header = true,
                      all_varchar = true,
                      sample_size = -1)
        """
    )

    # 3-4) Columnas tipadas + _cast_failures.
    select_parts: list[str] = []
    failure_terms: list[str] = []
    for col in spec.columns:
        expr = _cast_expr(col)
        select_parts.append(f"{expr} AS \"{col.name}\"")
        if col.dtype != "VARCHAR":
            failure_terms.append(
                f"CASE WHEN {_is_present(f'\"{col.name}\"')} "
                f"AND ({expr}) IS NULL THEN ['{col.name}'] ELSE [] END"
            )
    select_parts.append("_src_row")
    select_parts.append(
        "list_concat(" + ", ".join(failure_terms) + ") AS _cast_failures"
        if failure_terms
        else "[]::VARCHAR[] AS _cast_failures"
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE _typed AS
        SELECT {", ".join(select_parts)}
        FROM _raw
        """
    )

    rows_in = con.execute("SELECT count(*) FROM _raw").fetchone()[0]
    rows_out = con.execute("SELECT count(*) FROM _typed").fetchone()[0]

    # flag_counts por columna con fallos de cast: "cast_failed:<columna>".
    flag_counts: dict[str, int] = {}
    rows = con.execute(
        """
        SELECT flag, count(*) AS n
        FROM (SELECT unnest(_cast_failures) AS flag FROM _typed)
        GROUP BY flag ORDER BY flag
        """
    ).fetchall()
    for flag, n in rows:
        flag_counts[f"cast_failed:{flag}"] = int(n)

    # 5) Escritura parquet.
    con.execute(
        f"""
        COPY _typed TO '{out_path.as_posix()}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    con.execute("DROP TABLE IF EXISTS _typed")
    con.execute("DROP TABLE IF EXISTS _raw")

    result = CleanResult(
        table=spec.name,
        rows_in=int(rows_in),
        rows_out=int(rows_out),
        output_path=out_path,
        flag_counts=flag_counts,
        notes=[],
    )
    result.assert_no_row_loss()
    return result


def ingest_all(con: duckdb.DuckDBPyConnection) -> dict[str, CleanResult]:
    paths.ensure_dirs()
    results: dict[str, CleanResult] = {}
    for spec in TABLES.values():
        results[spec.name] = ingest_table(con, spec)
    return results


def run_ingest() -> dict[str, CleanResult]:
    """Entry point para el CLI: conecta, ingesta y mide el tiempo total."""
    from xray.db import connect

    t0 = time.monotonic()
    con = connect()
    try:
        results = ingest_all(con)
    finally:
        con.close()
    elapsed = time.monotonic() - t0
    for r in results.values():
        r.notes.append(f"ingest_seconds={elapsed:.2f}")
    return results
