"""Limpieza de dimensiones (T2).

Principios: ninguna fila se descarta, reordena ni fusiona (rows_in == rows_out,
`_src_row` se conserva tal cual). Los valores imposibles no se corrigen: se
conserva la columna original intacta, se anade una columna derivada con el
valor saneado (o NULL) y se marca con un flag del vocabulario cerrado
`xray.flags.Flag`. Nada de recortes, winsorizacion ni imputacion: eso es de la
capa de features.

Salida: data/clean/<tabla>.parquet (ZSTD) con `quality_flags LIST(VARCHAR)`
como ultima columna. Las tablas transactions/invoices las limpia T3 y no se
tocan aqui.
"""

import json
import unicodedata

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.flags import Flag

TABLES: tuple[str, ...] = (
    "groups",
    "companies",
    "banking_products",
    "debt_products",
    "debt_schedule_config",
    "balances",
)

# Ventana de fechas valida: [1990-01-01, 2030-12-31] inclusive. Para un
# TIMESTAMP, "2030-12-31 inclusive" es todo el dia, es decir < 2031-01-01.
_DATE_LO = "TIMESTAMP '1990-01-01'"
_DATE_HI = "TIMESTAMP '2031-01-01'"

# Auxiliares DENTRO de este modulo por consigna (nada compartido con facts.py).

# Nombres de pais (normalizados) -> ISO-3166 alfa-2. Los codigos alfa-2
# literales ya validos pasan por la regla de "2 letras" de _country_iso.
_NAME_TO_ISO = {
    "ESPANA": "ES",
    "ESPANYA": "ES",
    "SPAIN": "ES",
    "PORTUGAL": "PT",
    "ITALIA": "IT",
    "ALEMANIA": "DE",
    "MALAYSIA": "MY",
}


def _norm_country(raw: str) -> str:
    """trim + mayusculas + sin acentos, para usar como clave de mapeo."""
    s = raw.strip().upper()
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _country_iso(raw: str) -> str | None:
    """Mapea un valor literal de country a su codigo ISO-3166 alfa-2,
    o None si no se puede mapear (no se inventan codigos)."""
    key = _norm_country(raw)
    if key == "":
        return None
    if key in _NAME_TO_ISO:
        return _NAME_TO_ISO[key]
    if len(key) == 2 and key.isalpha() and key.isascii():
        return key
    return None


def _sql_str(raw: str) -> str:
    """Literal SQL entre comillas simples, escapando las comillas."""
    return "'" + raw.replace("'", "''") + "'"


def _flag_list(terms: list[tuple[str, Flag]]) -> str:
    """Construye una expresion LIST(VARCHAR) con los flags cuyas condiciones
    (SQL booleano) sean ciertas para la fila."""
    parts = [
        f"CASE WHEN {cond} THEN ['{flag.value}'] ELSE [] END" for cond, flag in terms
    ]
    if not parts:
        return "[]::VARCHAR[] AS quality_flags"
    return f"list_concat({', '.join(parts)})::VARCHAR[] AS quality_flags"


def _cast_failed_term() -> tuple[str, Flag]:
    return ("len(_cast_failures) > 0", Flag.CAST_FAILED)


def _date_ok_expr(col: str) -> str:
    """Columna derivada <col>_ok: el valor si cae en la ventana, si no NULL.
    La columna original no se toca."""
    return (
        f"CASE WHEN {col} IS NULL THEN NULL "
        f"WHEN {col} >= {_DATE_LO} AND {col} < {_DATE_HI} THEN {col} "
        f"ELSE NULL END"
    )


def _date_bad_cond(col: str) -> str:
    return f"{col} IS NOT NULL AND ({col} < {_DATE_LO} OR {col} >= {_DATE_HI})"


def _currency_norm_expr(col: str = "currency") -> str:
    """trim + mayusculas; si no son exactamente 3 letras A-Z -> NULL
    (el flag CAST_FAILED lo anade el llamador con _currency_bad_cond)."""
    u = f"trim(upper({col}))"
    return (
        f"CASE WHEN {col} IS NULL OR trim({col}) = '' THEN NULL "
        f"WHEN NOT regexp_matches({u}, '^[A-Z]{{3}}$') THEN NULL "
        f"ELSE {u} END"
    )


def _currency_bad_cond(col: str = "currency") -> str:
    return (
        f"{col} IS NOT NULL AND trim({col}) <> '' "
        f"AND NOT regexp_matches(trim(upper({col})), '^[A-Z]{{3}}$')"
    )


def _erp_norm_expr(col: str = "erp") -> str:
    return f"CASE WHEN {col} IS NULL OR trim({col}) = '' THEN NULL ELSE trim({col}) END"


def _write_table(
    con: duckdb.DuckDBPyConnection,
    table: str,
    select_sql: str,
    notes: list[str],
) -> CleanResult:
    """Ejecuta select_sql sobre data/interim/<t>.parquet, escribe
    data/clean/<t>.parquet (ZSTD) y mide filas y flags."""
    src = (paths.INTERIM_DIR / f"{table}.parquet").as_posix()
    out_path = paths.CLEAN_DIR / f"{table}.parquet"

    con.execute(f"CREATE OR REPLACE TEMP TABLE _clean AS {select_sql}")

    rows_in = con.execute(f"SELECT count(*) FROM read_parquet('{src}')").fetchone()[0]
    rows_out = con.execute("SELECT count(*) FROM _clean").fetchone()[0]

    con.execute(
        f"COPY _clean TO '{out_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    flag_counts: dict[str, int] = {}
    for flag, n in con.execute(
        "SELECT flag, count(*) FROM (SELECT unnest(quality_flags) AS flag FROM _clean) GROUP BY flag ORDER BY flag"
    ).fetchall():
        flag_counts[flag] = int(n)

    con.execute("DROP TABLE IF EXISTS _clean")

    result = CleanResult(
        table=table,
        rows_in=int(rows_in),
        rows_out=int(rows_out),
        output_path=out_path,
        flag_counts=flag_counts,
        notes=notes,
    )
    result.assert_no_row_loss()
    return result


def _clean_groups(con: duckdb.DuckDBPyConnection) -> CleanResult:
    cols = "group_id, erp, n_companies_in_sample, _src_row, _cast_failures"
    select_sql = f"""
        SELECT
        {cols},
        {_erp_norm_expr('erp')} AS erp_norm,
        {_flag_list([
            _cast_failed_term(),
            (
                "n_companies_in_sample IS NULL OR n_companies_in_sample <= 0",
                Flag.AMOUNT_MISSING,
            ),
        ])}
        FROM read_parquet('{(paths.INTERIM_DIR / "groups.parquet").as_posix()}')
    """
    notes: list[str] = []
    return _write_table(con, "groups", select_sql, notes)


def _clean_companies(con: duckdb.DuckDBPyConnection) -> CleanResult:
    # Mapeo de paises: se leen los valores distintos del interim y se mapean
    # todos en Python (trim + upper + sin acentos) a ISO-3166 alfa-2.
    src = (paths.INTERIM_DIR / "companies.parquet").as_posix()
    counts: dict[str, int] = dict(
        con.execute(
            f"SELECT country, count(*) FROM read_parquet('{src}') "
            "WHERE country IS NOT NULL AND trim(country) <> '' GROUP BY 1"
        ).fetchall()
    )
    mapping: dict[str, str | None] = {
        raw: _country_iso(raw) for raw in sorted(counts)
    }

    con.execute("CREATE OR REPLACE TEMP TABLE _country_map (raw VARCHAR, iso VARCHAR)")
    if mapping:
        rows_sql = ", ".join(
            f"({_sql_str(raw)}, {(_sql_str(iso) if iso else 'NULL')})"
            for raw, iso in mapping.items()
        )
        con.execute(f"INSERT INTO _country_map VALUES {rows_sql}")

    unmapped = [k for k, v in mapping.items() if v is None]
    mapped_rows = sum(n for k, n in counts.items() if mapping.get(k))
    unknown_rows = sum(counts.get(k, 0) for k in unmapped)
    empty_rows = con.execute(
        f"SELECT count(*) FROM read_parquet('{src}') WHERE country IS NULL OR trim(country) = ''"
    ).fetchone()[0]

    map_parts = [f"{raw!r}->{iso}" for raw, iso in mapping.items()]
    notes = [
        "country_mapping: " + ("; ".join(map_parts) if map_parts else "vacio"),
        f"country_rows: mapped={mapped_rows} unknown={unknown_rows} empty_or_null={empty_rows}",
    ]

    select_sql = f"""
        SELECT
        c.company_id, c.group_id, c.country, c.currency, c.erp, c.created_at,
        c._src_row, c._cast_failures,
        CASE WHEN c.country IS NULL OR trim(c.country) = '' THEN NULL
             ELSE m.iso END AS country_norm,
        {_currency_norm_expr('c.currency')} AS currency_norm,
        {_erp_norm_expr('c.erp')} AS erp_norm,
        {_date_ok_expr('c.created_at')} AS created_at_ok,
        {_flag_list([
            _cast_failed_term(),
            (_date_bad_cond("c.created_at"), Flag.DATE_OUT_OF_RANGE),
            (
                "c.country IS NOT NULL AND trim(c.country) <> '' AND m.iso IS NOT NULL "
                "AND c.country <> m.iso",
                Flag.COUNTRY_NORMALIZED,
            ),
            (
                "c.country IS NOT NULL AND trim(c.country) <> '' AND m.iso IS NULL",
                Flag.COUNTRY_UNKNOWN,
            ),
            (_currency_bad_cond("c.currency"), Flag.CAST_FAILED),
        ])}
        FROM read_parquet('{src}') c
        LEFT JOIN _country_map m ON m.raw = c.country
    """
    result = _write_table(con, "companies", select_sql, notes)
    con.execute("DROP TABLE IF EXISTS _country_map")
    return result


def _clean_banking_products(con: duckdb.DuckDBPyConnection) -> CleanResult:
    cols = (
        "product_id, company_id, label, type, bank_name, service, currency, "
        "created_at, _src_row, _cast_failures"
    )
    tn = "lower(trim(type))"
    select_sql = f"""
        SELECT
        {cols},
        {_currency_norm_expr('currency')} AS currency_norm,
        CASE WHEN type IS NULL THEN NULL ELSE {tn} END AS type_norm,
        (bank_name = 'Other (customer-defined)') AS is_customer_defined,
        CASE WHEN {tn} IN ('checking', 'saving', 'wallet') THEN 'cash'
             WHEN {tn} = 'tpv' THEN 'collection'
             WHEN {tn} IN ('card', 'expensesplatform') THEN 'card'
             WHEN {tn} = 'investment' THEN 'investment'
             WHEN {tn} IN ('risk', 'lineofcomex') THEN 'other'
             ELSE 'unknown' END AS liquidity_class,
        {_date_ok_expr('created_at')} AS created_at_ok,
        {_flag_list([
            _cast_failed_term(),
            (_date_bad_cond('created_at'), Flag.DATE_OUT_OF_RANGE),
            (_currency_bad_cond('currency'), Flag.CAST_FAILED),
        ])}
        FROM read_parquet('{(paths.INTERIM_DIR / "banking_products.parquet").as_posix()}')
    """
    src = (paths.INTERIM_DIR / "banking_products.parquet").as_posix()
    unknown = con.execute(
        f"SELECT {tn}, count(*) FROM read_parquet('{src}') "
        f"WHERE {tn} IS NULL OR {tn} NOT IN ('checking','saving','wallet','tpv',"
        f"'card','expensesplatform','investment','risk','lineofcomex') "
        f"GROUP BY 1 ORDER BY 1"
    ).fetchall()
    notes = [
        f"liquidity_class_unknown: {unknown}" if unknown else "liquidity_class_unknown: ninguno",
    ]
    return _write_table(con, "banking_products", select_sql, notes)


def _clean_debt_products(con: duckdb.DuckDBPyConnection) -> CleanResult:
    cols = (
        "product_id, company_id, label, type, bank_name, service, currency, "
        "created_at, granted, outstanding, liquidity, _src_row, _cast_failures"
    )
    tn = "lower(trim(type))"
    # granted y outstanding se conservan CON SU SIGNO: los *_abs son columnas
    # nuevas derivadas, nunca sustituyen a las originales.
    select_sql = f"""
        SELECT
        {cols},
        CASE WHEN granted IS NULL THEN NULL ELSE abs(granted) END AS granted_abs,
        CASE WHEN outstanding IS NULL THEN NULL ELSE abs(outstanding) END AS outstanding_abs,
        CASE WHEN outstanding IS NULL THEN 'missing'
             WHEN outstanding < 0 THEN 'negative'
             WHEN outstanding = 0 THEN 'zero'
             ELSE 'positive' END AS outstanding_sign,
        (outstanding IS NOT NULL AND abs(outstanding) > 0.01) AS is_drawn,
        CASE WHEN {tn} IN ('loan', 'leasing', 'mortgage', 'renting') THEN 'term'
             WHEN {tn} = 'lineofcredit' THEN 'revolving'
             WHEN {tn} IN ('factoring', 'confirming') THEN 'receivables'
             WHEN {tn} = 'guarantee' THEN 'offbalance'
             ELSE 'unknown' END AS debt_class,
        {_currency_norm_expr('currency')} AS currency_norm,
        {_date_ok_expr('created_at')} AS created_at_ok,
        {_flag_list([
            _cast_failed_term(),
            (_date_bad_cond('created_at'), Flag.DATE_OUT_OF_RANGE),
            ("granted IS NULL", Flag.AMOUNT_MISSING),
            (_currency_bad_cond('currency'), Flag.CAST_FAILED),
        ])}
        FROM read_parquet('{(paths.INTERIM_DIR / "debt_products.parquet").as_posix()}')
    """
    src = (paths.INTERIM_DIR / "debt_products.parquet").as_posix()
    unknown = con.execute(
        f"SELECT {tn}, count(*) FROM read_parquet('{src}') "
        f"WHERE {tn} IS NULL OR {tn} NOT IN ('loan','leasing','mortgage','renting',"
        f"'lineofcredit','factoring','confirming','guarantee') "
        f"GROUP BY 1 ORDER BY 1"
    ).fetchall()
    signs = con.execute(
        f"SELECT CASE WHEN outstanding IS NULL THEN 'missing' WHEN outstanding < 0 "
        f"THEN 'negative' WHEN outstanding = 0 THEN 'zero' ELSE 'positive' END AS s, "
        f"count(*) FROM read_parquet('{src}') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    notes = [
        f"debt_class_unknown: {unknown}" if unknown else "debt_class_unknown: ninguno",
        f"outstanding_sign_counts: {dict(signs)}",
    ]
    return _write_table(con, "debt_products", select_sql, notes)


def _clean_debt_schedule_config(con: duckdb.DuckDBPyConnection) -> CleanResult:
    cols = (
        "product_id, company_id, settlement_product_id, currency, amortization_type, "
        "interest_calc_method, amortising_frequency, granted_balance, outstanding_balance, "
        "total_periods, next_payment_date, last_payment_date, "
        "annual_interest_rate_or_spread, interest_type, _src_row, _cast_failures"
    )
    bp = (paths.INTERIM_DIR / "banking_products.parquet").as_posix()
    select_sql = f"""
        SELECT
        dsc.product_id, dsc.company_id, dsc.settlement_product_id, dsc.currency,
        dsc.amortization_type, dsc.interest_calc_method, dsc.amortising_frequency,
        dsc.granted_balance, dsc.outstanding_balance, dsc.total_periods,
        dsc.next_payment_date, dsc.last_payment_date,
        dsc.annual_interest_rate_or_spread, dsc.interest_type,
        dsc._src_row, dsc._cast_failures,
        {_date_ok_expr('dsc.next_payment_date')} AS next_payment_date_ok,
        {_date_ok_expr('dsc.last_payment_date')} AS last_payment_date_ok,
        CASE WHEN dsc.annual_interest_rate_or_spread IS NULL THEN NULL
             WHEN dsc.annual_interest_rate_or_spread >= 0
                  AND dsc.annual_interest_rate_or_spread <= 0.5
             THEN dsc.annual_interest_rate_or_spread ELSE NULL END AS interest_rate_ok,
        CASE WHEN dsc.settlement_product_id IS NULL THEN FALSE
             ELSE EXISTS (
                 SELECT 1 FROM read_parquet('{bp}') b
                 WHERE b.product_id = dsc.settlement_product_id
             ) END AS settlement_product_found,
        {_flag_list([
            _cast_failed_term(),
            (_date_bad_cond("dsc.next_payment_date"), Flag.DATE_OUT_OF_RANGE),
            (_date_bad_cond("dsc.last_payment_date"), Flag.DATE_OUT_OF_RANGE),
            (
                "dsc.annual_interest_rate_or_spread IS NOT NULL AND "
                "(dsc.annual_interest_rate_or_spread < 0 OR "
                "dsc.annual_interest_rate_or_spread > 0.5)",
                Flag.AMOUNT_EXTREME,
            ),
            (
                "dsc.total_periods IS NULL OR dsc.total_periods <= 0 OR dsc.total_periods > 600",
                Flag.AMOUNT_MISSING,
            ),
            (
                "dsc.settlement_product_id IS NULL OR NOT EXISTS ("
                f"SELECT 1 FROM read_parquet('{bp}') b "
                "WHERE b.product_id = dsc.settlement_product_id)",
                Flag.FK_ORPHAN,
            ),
        ])}
        FROM read_parquet('{(paths.INTERIM_DIR / "debt_schedule_config.parquet").as_posix()}') dsc
    """
    src = (paths.INTERIM_DIR / "debt_schedule_config.parquet").as_posix()
    orphans = con.execute(
        f"SELECT count(*) FROM read_parquet('{src}') dsc "
        f"WHERE dsc.settlement_product_id IS NULL OR NOT EXISTS ("
        f"SELECT 1 FROM read_parquet('{bp}') b WHERE b.product_id = dsc.settlement_product_id)"
    ).fetchone()[0]
    notes = [f"settlement_product_orphans: {orphans}"]
    return _write_table(con, "debt_schedule_config", select_sql, notes)


def _clean_balances(con: duckdb.DuckDBPyConnection) -> CleanResult:
    bp = (paths.INTERIM_DIR / "banking_products.parquet").as_posix()
    dp = (paths.INTERIM_DIR / "debt_products.parquet").as_posix()
    src = (paths.INTERIM_DIR / "balances.parquet").as_posix()
    # product_id de balances apunta a banking_products o debt_products (espacios
    # de id disjuntos): un LEFT JOIN a cada uno deja como mucho una coincidencia.
    select_sql = f"""
        SELECT
        ba.product_id, ba.company_id, ba.date, ba.balance, ba.available, ba.granted,
        ba.liquidity, ba.countable, ba._src_row, ba._cast_failures,
        {_date_ok_expr('ba.date')} AS date_ok,
        CASE WHEN ba.balance IS NOT NULL
                  AND (ba.balance <= -999999999 OR ba.balance >= 1000000000)
             THEN NULL ELSE ba.balance END AS balance_ok,
        (CAST(ba.date AS DATE) = DATE '2026-09-01') AS is_snapshot_date,
        CASE WHEN b.product_id IS NOT NULL THEN 'banking'
             WHEN d.product_id IS NOT NULL THEN 'debt'
             ELSE 'orphan' END AS product_source,
        COALESCE(b.type, d.type) AS product_type,
        COALESCE(b.currency, d.currency) AS product_currency,
        {_flag_list([
            ("len(ba._cast_failures) > 0", Flag.CAST_FAILED),
            (_date_bad_cond("ba.date"), Flag.DATE_OUT_OF_RANGE),
            (
                "ba.balance IS NOT NULL AND (ba.balance <= -999999999 OR ba.balance >= 1000000000)",
                Flag.SENTINEL_HIDDEN_BALANCE,
            ),
            (
                "b.product_id IS NULL AND d.product_id IS NULL",
                Flag.FK_ORPHAN,
            ),
        ])}
        FROM read_parquet('{src}') ba
        LEFT JOIN read_parquet('{bp}') b ON b.product_id = ba.product_id
        LEFT JOIN read_parquet('{dp}') d ON d.product_id = ba.product_id
    """

    sentinels = con.execute(
        f"SELECT balance, count(*) FROM read_parquet('{src}') "
        f"WHERE balance <= -999999999 OR balance >= 1000000000 GROUP BY 1 ORDER BY 1"
    ).fetchall()
    orphans = con.execute(
        f"SELECT count(*) FROM read_parquet('{src}') ba WHERE NOT EXISTS ("
        f"SELECT 1 FROM read_parquet('{bp}') b WHERE b.product_id = ba.product_id) AND NOT EXISTS ("
        f"SELECT 1 FROM read_parquet('{dp}') d WHERE d.product_id = ba.product_id)"
    ).fetchone()[0]
    sources = con.execute(
        f"SELECT CASE WHEN EXISTS (SELECT 1 FROM read_parquet('{bp}') b WHERE b.product_id = ba.product_id) THEN 'banking' "
        f"WHEN EXISTS (SELECT 1 FROM read_parquet('{dp}') d WHERE d.product_id = ba.product_id) THEN 'debt' ELSE 'orphan' END AS s, count(*) "
        f"FROM read_parquet('{src}') ba GROUP BY 1 ORDER BY 1"
    ).fetchall()
    notes = [
        f"balance_sentinels: {sum(n for _, n in sentinels)} filas, valores={dict(sentinels)}",
        f"product_source: {dict(sources)} (orphans={orphans})",
    ]
    return _write_table(con, "balances", select_sql, notes)


def clean_dimensions(con: duckdb.DuckDBPyConnection) -> dict[str, CleanResult]:
    """Lee data/interim/<t>.parquet para t en TABLES y escribe
    data/clean/<t>.parquet anadiendo quality_flags LIST(VARCHAR).
    Implementa T2. No toca transactions ni invoices."""
    paths.ensure_dirs()
    results: dict[str, CleanResult] = {
        "groups": _clean_groups(con),
        "companies": _clean_companies(con),
        "banking_products": _clean_banking_products(con),
        "debt_products": _clean_debt_products(con),
        "debt_schedule_config": _clean_debt_schedule_config(con),
        "balances": _clean_balances(con),
    }
    report = {
        t: {
            "rows_in": r.rows_in,
            "rows_out": r.rows_out,
            "output_path": r.output_path.relative_to(paths.WORKSPACE).as_posix(),
            "flag_counts": dict(sorted(r.flag_counts.items())),
            "notes": list(r.notes),
        }
        for t, r in sorted(results.items())
    }
    report_path = paths.REPORTS_DIR / "dimensions.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return results
