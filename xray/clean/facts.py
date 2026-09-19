"""Limpieza de hechos (T3): transactions e invoices.

Principios: ninguna fila se descarta, reordena ni fusiona (rows_in == rows_out,
`_src_row` se conserva tal cual). Los valores imposibles no se corrigen: la
columna original se conserva intacta, se anade una columna derivada con el
valor saneado (o NULL) y se marca con un flag del vocabulario cerrado
`xray.flags.Flag`. Nada de recortes, winsorizacion ni imputacion de importes:
un importe extremo se MARCA (Flag.AMOUNT_EXTREME), no se toca; decidir si se
recorta es de la capa de features.

Salida: data/clean/<tabla>.parquet (ZSTD) con `quality_flags LIST(VARCHAR)`
como ultima columna. Las 6 tablas de dimension las limpia T2
(xray/clean/dimensions.py); las funciones auxiliares de este modulo son
locales A PROPOSITO para no crear dependencias entre ambos workers.

Convencion FX verificada empiricamente (ver notes de cada CleanResult y
reports/quality/facts.json): exchange_rate es "unidades de moneda local por
1 unidad de la moneda destino", de modo que
    amount_destino = amount_local / exchange_rate.
"""

import json

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.flags import Flag
from xray.period import OPEN_MONTH

TABLES: tuple[str, ...] = ("transactions", "invoices")

# Ventana de fechas valida: [1990-01-01, 2030-12-31] inclusive. Para un
# TIMESTAMP, "2030-12-31 inclusive" es todo el dia, es decir < 2031-01-01.
_DATE_LO = "TIMESTAMP '1990-01-01'"
_DATE_HI = "TIMESTAMP '2031-01-01'"

# Mes de corte del dataset: septiembre 2026 no es un mes completo.
_OPEN_MONTH = f"DATE '{OPEN_MONTH}'"

# Umbral de importe extremo (solo se marca, nunca se modifica el importe).
_AMOUNT_EXTREME = "1e9"

# CONVENCION FX ELEGIDA (PASO 1, transactions): amount_eur = amount / exchange_rate,
# es decir, exchange_rate = unidades de moneda del producto por 1 EUR.
# Justificacion empirica (Q2 restringido a filas con rate <> 1, frente a la
# mediana de abs(amount) de las filas EUR = 498.22, Q3): la lectura por division
# (amount/rate) gana en 16 de 19 monedas discriminantes (31.569 filas) frente a
# 3 monedas (1.002 filas) para la multiplicacion, y es consistente con los
# niveles de cambio del mundo real: JPY~162, HKD~7.83, PLN~4.25, SEK~10.9,
# HUF~415, ILS~3.75, CLP~1048, ARS~1419, COP~4272 (unidades locales por EUR),
# y GBP~0.84 / USD~1.16 (unidades de GBP/USD por 1 EUR). La lectura inversa
# (amount*rate) daria ordenos de magnitud absurdos (p.ej. HUF med_mul=7.0e8
# frente a med_div=3978).
FX_CONVENTION_TX = "amount_eur = amount / exchange_rate (rate = moneda local por 1 EUR)"

# PASO 2 (invoices): mismo criterio mecanico sobre Q5 (currency <>
# accounting_currency, filas con rate <> 1 implicito). La lectura por division
# gana abrumadoramente: los rates son reciprocamente coherentes entre pares
# (USD->EUR 1.16 vs EUR->USD 0.86) y coinciden con el mundo real tratandolos
# como "unidades de `currency` por 1 unidad de `accounting_currency`":
# JPY->EUR 162.23, HKD->USD 7.83, NOK->EUR 11.0, SEK->EUR 11.11,
# CHF->USD 0.79 (1 CHF = 1.27 USD), GBP->USD 0.74 (1 GBP = 1.35 USD).
# La lectura por multiplicacion daria 1 JPY = 162 EUR, 1 HKD = 7.83 USD, etc.
FX_CONVENTION_INV = (
    "amount_acct = amount / exchange_rate con rate efectivo "
    "(rate = unidades de `currency` por 1 unidad de `accounting_currency`)"
)


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


def _cast_failed_term(qual: str = "") -> tuple[str, Flag]:
    col = f"{qual}._cast_failures" if qual else "_cast_failures"
    return (f"len({col}) > 0", Flag.CAST_FAILED)


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


def _norm_expr(col: str) -> str:
    """trim + minusculas; NULL/'' se resuelven aparte por el llamador."""
    return f"lower(trim({col}))"


def _norm_missing_cond(col: str) -> str:
    return f"{col} IS NULL OR trim({col}) = ''"


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
        "SELECT flag, count(*) FROM (SELECT unnest(quality_flags) AS flag FROM _clean) "
        "GROUP BY flag ORDER BY flag"
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


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------


def _clean_transactions(con: duckdb.DuckDBPyConnection) -> CleanResult:
    bp = (paths.INTERIM_DIR / "banking_products.parquet").as_posix()
    dp = (paths.INTERIM_DIR / "debt_products.parquet").as_posix()
    src = (paths.INTERIM_DIR / "transactions.parquet").as_posix()

    status_n = _norm_expr("status")
    cat_n = _norm_expr("category")
    # La moneda del producto es la moneda del hecho; se normaliza (trim+upper)
    # solo para comparar, y se publica ya normalizada. banking y debt tienen
    # espacios de product_id disjuntos: coalesce toma la moneda del producto
    # resuelto (para filas debt, b.currency es NULL).
    prod_cur = "trim(upper(coalesce(b.currency, d.currency)))"

    # amount_eur: SOLO cuando hay producto resuelto y el rate es valido.
    # EUR -> amount directamente (no necesita rate). Nunca se modifica `amount`.
    amount_eur = f"""
        CASE
            WHEN {prod_cur} IS NULL THEN NULL
            WHEN {prod_cur} = 'EUR' THEN t.amount
            WHEN t.exchange_rate IS NULL OR NOT isfinite(t.exchange_rate)
                 OR t.exchange_rate <= 0 OR t.exchange_rate = 1 THEN NULL
            ELSE t.amount / t.exchange_rate
        END"""

    # FX_NOT_CONVERTIBLE: producto huerfano (sin moneda conocida) o moneda no
    # EUR con rate ausente/invalido o ambiguo (1). Tambien cubre AMOUNT NULL
    # via el calculo: el flag coincide exactamente con amount_eur desconocido.
    fx_not_conv = f"({amount_eur}) IS NULL"

    select_sql = f"""
        SELECT
        t.transaction_id, t.company_id, t.product_id, t.date, t.value_date,
        t.amount, t.exchange_rate, t.status, t.accounting_status, t.category,
        t.description, t.counterparty_id, t._src_row, t._cast_failures,
        {_date_ok_expr('t.date')} AS date_ok,
        {_date_ok_expr('t.value_date')} AS value_date_ok,
        CAST(date_trunc('month', {_date_ok_expr('t.date')}) AS DATE) AS month,
        (CAST(date_trunc('month', {_date_ok_expr('t.date')}) AS DATE) >= {_OPEN_MONTH})
            AS is_open_month,
        CASE WHEN {_norm_missing_cond('t.status')} THEN 'unknown'
             ELSE {status_n} END AS status_norm,
        (CASE WHEN {_norm_missing_cond('t.status')} THEN 'unknown'
              ELSE {status_n} END) = 'booked' AS is_booked,
        CASE WHEN t.category IS NULL OR trim(t.category) = ''
                  OR trim(t.category) = '-' THEN NULL
             ELSE {cat_n} END AS category_norm,
        CASE WHEN t.amount IS NULL THEN NULL
             WHEN t.amount > 0 THEN 'in'
             WHEN t.amount < 0 THEN 'out'
             ELSE 'zero' END AS direction,
        CASE
            WHEN b.product_id IS NOT NULL THEN 'banking'
            WHEN d.product_id IS NOT NULL THEN 'debt'
            ELSE 'orphan'
        END AS product_source,
        CASE WHEN b.product_id IS NOT NULL THEN lower(trim(b.type))
             WHEN d.product_id IS NOT NULL THEN lower(trim(d.type))
             ELSE NULL END AS product_type,
        {prod_cur} AS product_currency,
        {amount_eur} AS amount_eur,
        CASE WHEN ({amount_eur}) IS NULL THEN 'unknown'
             WHEN {prod_cur} = 'EUR' THEN 'identity'
             ELSE 'reported' END AS amount_eur_source,
        ({prod_cur} <> 'EUR' AND t.exchange_rate = 1) AS fx_ambiguous,
        {_flag_list([
            _cast_failed_term("t"),
            (_date_bad_cond('t.date'), Flag.DATE_OUT_OF_RANGE),
            (_date_bad_cond('t.value_date'), Flag.DATE_OUT_OF_RANGE),
            (
                f"CAST(date_trunc('month', {_date_ok_expr('t.date')}) AS DATE) "
                f">= {_OPEN_MONTH}",
                Flag.OPEN_MONTH,
            ),
            (_norm_missing_cond('t.status'), Flag.STATUS_MISSING),
            (
                f"t.category IS NOT NULL AND (trim(t.category) = '' "
                f"OR {cat_n} = '-' OR trim(t.category) = '-')",
                Flag.CATEGORY_MISSING,
            ),
            ("t.amount IS NULL", Flag.AMOUNT_MISSING),
            (f"abs(t.amount) >= {_AMOUNT_EXTREME}", Flag.AMOUNT_EXTREME),
            (
                "t.exchange_rate IS NOT NULL AND t.exchange_rate <= 0",
                Flag.FX_RATE_INVALID,
            ),
            (fx_not_conv, Flag.FX_NOT_CONVERTIBLE),
            ("b.product_id IS NULL AND d.product_id IS NULL", Flag.FK_ORPHAN),
        ])}
        FROM read_parquet('{src}') t
        LEFT JOIN read_parquet('{bp}') b ON b.product_id = t.product_id
        LEFT JOIN read_parquet('{dp}') d ON d.product_id = t.product_id
    """

    notes = [
        f"fx_convention: {FX_CONVENTION_TX}",
        "fx_evidence: Q1 EUR-producto rate=1 en 2295765/2306077 (99.55%), med=1.0; "
        "Q2 (rate<>1) division gana en 16/19 monedas discriminantes (31569 filas) "
        "vs multiplicacion 3/19 (1002 filas); Q3 mediana abs(amount) EUR = 498.22",
        "fx_not_convertible_huerfano: filas con product_id huerfano no convertidas",
    ]

    # product_source: reparto banking/debt/orphan (hecho conocido ~92.8/7.2).
    dist = con.execute(
        f"""
        SELECT CASE WHEN b.product_id IS NOT NULL THEN 'banking'
                    WHEN d.product_id IS NOT NULL THEN 'debt'
                    ELSE 'orphan' END AS s, count(*)
        FROM read_parquet('{src}') t
        LEFT JOIN read_parquet('{bp}') b ON b.product_id = t.product_id
        LEFT JOIN read_parquet('{dp}') d ON d.product_id = t.product_id
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    notes.append(
        "product_source_counts: "
        + "; ".join(f"{k}={v}" for k, v in dist)
    )

    fx_nc_by_reason = con.execute(
        f"""
        SELECT
        count(*) FILTER (WHERE b.product_id IS NULL AND d.product_id IS NULL),
        count(*) FILTER (WHERE (b.product_id IS NOT NULL OR d.product_id IS NOT NULL)
            AND {prod_cur} <> 'EUR'
            AND (t.exchange_rate IS NULL OR t.exchange_rate <= 0))
        FROM read_parquet('{src}') t
        LEFT JOIN read_parquet('{bp}') b ON b.product_id = t.product_id
        LEFT JOIN read_parquet('{dp}') d ON d.product_id = t.product_id
        """
    ).fetchone()
    notes.append(
        "fx_not_convertible_reasons: "
        f"huerfano={fx_nc_by_reason[0]} rate_invalido_no_eur={fx_nc_by_reason[1]}"
    )

    return _write_table(con, "transactions", select_sql, notes)


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------


def _clean_invoices(con: duckdb.DuckDBPyConnection) -> CleanResult:
    src = (paths.INTERIM_DIR / "invoices.parquet").as_posix()

    status_n = _norm_expr("status")
    doc_n = _norm_expr("document_type")

    # Monedas normalizadas (trim+upper) solo para comparar/publicar.
    cur_n = "trim(upper(currency))"
    acct_n = "trim(upper(accounting_currency))"

    # Rate efectivo:
    # - misma moneda y rate NULL o <= 0 -> 1.0 (misma moneda; el tipo solo
    #   puede ser 1), pero se marca Flag.FX_RATE_INVALID para dejar constancia.
    # - misma moneda: identidad; un rate distinto de 1 queda marcado invalido.
    #   El importe original y el rate contradictorio se conservan sin modificar.
    # - distinta moneda y rate ausente, no finito, <= 0 o 1 -> desconocido.
    eff_rate = f"""
        CASE
            WHEN {cur_n} IS NULL OR {acct_n} IS NULL THEN NULL
            WHEN {cur_n} = {acct_n} THEN 1.0
            WHEN exchange_rate IS NULL OR NOT isfinite(exchange_rate)
                 OR exchange_rate <= 0 OR exchange_rate = 1 THEN NULL
            ELSE exchange_rate
        END"""

    # amount_acct: amount convertido a accounting_currency (convencion DIV).
    # Nunca se modifica `amount`.
    amount_acct = f"""
        CASE WHEN {eff_rate} IS NULL THEN NULL
             ELSE amount / {eff_rate} END"""

    # amount_eur: identidad si currency = EUR; si no, conversion reportada a EUR.
    # Una moneda contable distinta no invalida un nominal que ya estaba en EUR.
    # Sin conversion demostrada queda NULL; no se usan tipos estimados.
    amount_eur = f"""
        CASE WHEN {cur_n} = 'EUR' THEN amount
             WHEN {acct_n} = 'EUR' THEN {amount_acct} ELSE NULL END"""

    # FX_NOT_CONVERTIBLE: no se pudo llegar a EUR / a la moneda de contabilidad.
    fx_not_conv = f"({amount_eur}) IS NULL"

    select_sql = f"""
        SELECT
        i.operation_id, i.company_id, i.document_type, i.issuance_date,
        i.due_date, i.payment_date, i.amount, i.pending_amount, i.currency,
        i.accounting_currency, i.exchange_rate, i.status, i.concept,
        i.counterparty_id, i._src_row, i._cast_failures,
        {_date_ok_expr('i.issuance_date')} AS issuance_date_ok,
        {_date_ok_expr('i.due_date')} AS due_date_ok,
        {_date_ok_expr('i.payment_date')} AS payment_date_ok,
        CAST(date_trunc('month', {_date_ok_expr('i.issuance_date')}) AS DATE) AS month,
        (CAST(date_trunc('month', {_date_ok_expr('i.issuance_date')}) AS DATE)
            >= {_OPEN_MONTH}) AS is_open_month,
        CASE WHEN {_norm_missing_cond('status')} THEN 'unknown'
             ELSE {status_n} END AS status_norm,
        CASE WHEN i.document_type IS NULL THEN NULL
             ELSE {doc_n} END AS document_type_norm,
        CASE WHEN due_date_ok >= issuance_date_ok
             THEN date_diff('day', issuance_date_ok, due_date_ok) END AS days_to_due,
        CASE WHEN status_norm = 'paid' AND payment_date_ok >= issuance_date_ok
                  AND coalesce(pending_amount, 0) = 0
             THEN date_diff('day', issuance_date_ok, payment_date_ok) END AS days_to_payment,
        CASE WHEN i.amount IS NULL THEN NULL
             WHEN i.amount > 0 THEN 'inflow'
             WHEN i.amount < 0 THEN 'outflow'
             ELSE 'zero' END AS flow_side,
        {amount_acct} AS amount_acct,
        {amount_eur} AS amount_eur,
        {cur_n} AS currency_norm,
        {acct_n} AS accounting_currency_norm,
        CASE WHEN ({amount_eur}) IS NULL THEN 'unknown'
             WHEN {cur_n} = 'EUR' THEN 'identity'
             ELSE 'reported' END AS amount_eur_source,
        ({cur_n} <> {acct_n} AND exchange_rate = 1) AS fx_ambiguous,
        CASE WHEN status_norm = 'paid'
                  AND payment_date_ok >= issuance_date_ok
                  AND coalesce(pending_amount, 0) = 0
             THEN payment_date_ok END AS settlement_date_ok,
        CASE WHEN due_date_ok >= issuance_date_ok THEN due_date_ok END AS maturity_date_ok,
        {_flag_list([
            _cast_failed_term(),
            (_date_bad_cond('i.issuance_date'), Flag.DATE_OUT_OF_RANGE),
            (_date_bad_cond('i.due_date'), Flag.DATE_OUT_OF_RANGE),
            (_date_bad_cond('i.payment_date'), Flag.DATE_OUT_OF_RANGE),
            (
                f"({_date_ok_expr('i.due_date')} < {_date_ok_expr('i.issuance_date')}) "
                f"OR ({_date_ok_expr('i.payment_date')} < {_date_ok_expr('i.issuance_date')})",
                Flag.DATE_ORDER_INVALID,
            ),
            (
                f"CAST(date_trunc('month', {_date_ok_expr('i.issuance_date')}) AS DATE) "
                f">= {_OPEN_MONTH}",
                Flag.OPEN_MONTH,
            ),
            (_norm_missing_cond('status'), Flag.STATUS_MISSING),
            ("status_norm = 'paid' AND pending_amount <> 0", Flag.PAID_WITH_PENDING),
            ("i.amount IS NULL", Flag.AMOUNT_MISSING),
            (f"abs(i.amount) >= {_AMOUNT_EXTREME}", Flag.AMOUNT_EXTREME),
            (
                # FX_RATE_INVALID: rate NULL o <=0 (incluye el caso especial
                # misma moneda, donde el rate efectivo pasa a ser 1.0).
                f"i.exchange_rate IS NULL OR NOT isfinite(i.exchange_rate) "
                f"OR i.exchange_rate <= 0 OR ({cur_n} = {acct_n} AND i.exchange_rate <> 1)",
                Flag.FX_RATE_INVALID,
            ),
            (fx_not_conv, Flag.FX_NOT_CONVERTIBLE),
        ])}
        FROM read_parquet('{src}') i
    """

    notes = [
        f"fx_convention: {FX_CONVENTION_INV}",
        "fx_evidence: Q5 (currency<>accounting) los rates son coherentes como "
        "'unidades de currency por unidad de accounting' y mutuamente "
        "reciprocos entre pares (USD->EUR 1.16 vs EUR->USD 0.86; GBP->USD 0.74 "
        "vs USD->GBP 1.34), y coinciden con niveles reales (JPY 162, HKD 7.83, "
        "NOK 11.0, SEK 11.11); la lectura por division es la consistente con la "
        "elegida en transactions (PASO 1)",
        "fx_same_currency_rate_1: filas currency=accounting_currency con rate "
        "NULL o <=0 usan rate efectivo 1.0 y marcan fx_rate_invalid",
        "fx_same_currency: identidad; rates contradictorios marcados fx_rate_invalid",
        "fx_conservative: nominal EUR por identidad; conversion reportada hacia EUR "
        "solo con rate positivo finito distinto de 1; resto desconocido",
        "settlement_date_ok: solo paid sin pendiente y pago no anterior a emision; "
        "maturity_date_ok excluye vencimientos anteriores a emision",
        "signos: flow_side es solo el signo de amount (inflow/outflow/zero); "
        "no se asume ninguna semantica de venta/compra (verificacion en PASO 2, "
        "decision del coordinador)",
        "amounts: ni `amount` ni `pending_amount` se modifican; los flags "
        "amount_missing/amount_extreme se aplican a `amount`",
    ]

    # Recuentos para notes: FX rate invalidos same-currency y rate>0<>1.
    same_invalid, same_pos_ne1 = con.execute(
        f"""
        SELECT
        count(*) FILTER (WHERE {cur_n} = {acct_n}
            AND (i.exchange_rate IS NULL OR i.exchange_rate <= 0)),
        count(*) FILTER (WHERE {cur_n} = {acct_n}
            AND i.exchange_rate > 0 AND i.exchange_rate <> 1)
        FROM read_parquet('{src}') i
        """
    ).fetchone()
    notes.append(
        "fx_rate_invalid_counts: "
        f"same_currency_null_or_le0={same_invalid} same_currency_pos_ne1={same_pos_ne1}"
    )

    fx_nc_reasons = con.execute(
        f"""
        SELECT
        count(*) FILTER (WHERE {acct_n} IS NULL OR {acct_n} <> 'EUR'),
        count(*) FILTER (WHERE {acct_n} = 'EUR' AND {cur_n} IS NULL),
        count(*) FILTER (WHERE {acct_n} = 'EUR' AND {cur_n} IS NOT NULL
            AND {cur_n} <> {acct_n}
            AND (i.exchange_rate IS NULL OR i.exchange_rate <= 0))
        FROM read_parquet('{src}') i
        """
    ).fetchone()
    notes.append(
        "fx_not_convertible_reasons: "
        f"acct_no_eur={fx_nc_reasons[0]} "
        f"currency_ausente={fx_nc_reasons[1]} "
        f"rate_invalido_cross={fx_nc_reasons[2]}"
    )

    return _write_table(con, "invoices", select_sql, notes)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def clean_facts(con: duckdb.DuckDBPyConnection | None) -> dict[str, CleanResult]:
    """Lee data/interim/<t>.parquet para t en TABLES y escribe
    data/clean/<t>.parquet anadiendo quality_flags LIST(VARCHAR).
    No toca las tablas de dimension (las limpia T2)."""
    if con is None:
        # El CLI puede invocar sin conexion abierta: se crea una local.
        con = duckdb.connect(database=":memory:")
        con.execute("SET threads=4")
        con.execute("SET memory_limit='6GB'")
    paths.ensure_dirs()
    results: dict[str, CleanResult] = {
        "transactions": _clean_transactions(con),
        "invoices": _clean_invoices(con),
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
    report_path = paths.REPORTS_DIR / "facts.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return results
