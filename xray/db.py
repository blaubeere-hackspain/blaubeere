"""Conexion DuckDB compartida."""

import duckdb

from xray import paths


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("SET threads=4")
    con.execute("SET memory_limit='6GB'")
    paths.ensure_dirs()
    return con
