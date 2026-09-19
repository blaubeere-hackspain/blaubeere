"""T4: informe de calidad consolidado.

Fusiona reports/quality/dimensions.json y facts.json, calcula metricas de
cobertura sobre data/clean/*.parquet y escribe summary.json + summary.md.
Todos los numeros del informe salen de consultas sobre los parquet: nada
se inventa.
"""

import json

import duckdb

from xray import paths

EXPECTED_ROWS: dict[str, int] = {
    "groups": 250,
    "companies": 1286,
    "banking_products": 5987,
    "debt_products": 2239,
    "debt_schedule_config": 87,
    "balances": 7996,
    "transactions": 2556437,
    "invoices": 897894,
}

TABLE_ORDER = list(EXPECTED_ROWS)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _count(con: duckdb.DuckDBPyConnection, stage: str, table: str) -> int:
    return con.execute(
        f"select count(*) from read_parquet('{getattr(paths, stage.upper() + '_DIR') / f'{table}.parquet'}')"
    ).fetchone()[0]


def _distinct_companies(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return con.execute(
        f"select count(distinct company_id) from read_parquet('{paths.CLEAN_DIR / f'{table}.parquet'}')"
    ).fetchone()[0]


def _build_report(con: duckdb.DuckDBPyConnection) -> dict:
    dimensions = _read_json(paths.REPORTS_DIR / "dimensions.json")
    facts = _read_json(paths.REPORTS_DIR / "facts.json")
    clean_reports = {**dimensions, **facts}
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW _quality_transactions AS "
        f"SELECT * FROM read_parquet('{paths.CLEAN_DIR / 'transactions.parquet'}')"
    )

    # --- 1. Recuento de filas: interim vs clean vs esperado -----------------
    row_counts: dict[str, dict] = {}
    mismatches = []
    for table in TABLE_ORDER:
        interim = _count(con, "interim", table)
        clean = _count(con, "clean", table)
        expected = EXPECTED_ROWS[table]
        ok = interim == clean == expected
        if not ok:
            mismatches.append(table)
        row_counts[table] = {
            "interim": interim,
            "clean": clean,
            "expected": expected,
            "ok": ok,
        }

    # --- 2. Flags por tabla (de los informes de limpieza, ordenados) --------
    flags = {
        t: dict(sorted(clean_reports[t]["flag_counts"].items(), key=lambda kv: -kv[1]))
        for t in clean_reports
    }

    # --- 3. Cobertura cruzada de empresas -----------------------------------
    total_companies = _count(con, "clean", "companies")
    sources = {
        "transacciones": _distinct_companies(con, "transactions"),
        "cuentas_bancarias": _distinct_companies(con, "banking_products"),
        "saldos": _distinct_companies(con, "balances"),
        "facturas": _distinct_companies(con, "invoices"),
        "deuda": _distinct_companies(con, "debt_products"),
    }
    coverage = {
        "total_companies": total_companies,
        "sources": {
            src: {
                "companies": n,
                "pct": round(100.0 * n / total_companies, 2) if total_companies else 0.0,
            }
            for src, n in sources.items()
        },
    }

    # --- 4. Meses de historia por empresa (solo meses cerrados) -------------
    hist = con.execute(
        """
        with m as (
            select company_id, count(distinct month) as n
            from _quality_transactions
            where not is_open_month
            group by company_id
        )
        select
            min(n), quantile_cont(n, 0.25), median(n), quantile_cont(n, 0.75),
            max(n), count(*),
            count(*) filter (where n < 6)
        from m
        """
    ).fetchone()
    history = {
        "min_closed_months": int(hist[0]) if hist[0] is not None else 0,
        "p25_closed_months": float(hist[1]) if hist[1] is not None else 0.0,
        "median_closed_months": float(hist[2]) if hist[2] is not None else 0.0,
        "p75_closed_months": float(hist[3]) if hist[3] is not None else 0.0,
        "max_closed_months": int(hist[4]) if hist[4] is not None else 0,
        "companies_with_history": int(hist[5]) if hist[5] is not None else 0,
        "companies_lt_6_closed_months": int(hist[6]) if hist[6] is not None else 0,
    }

    # --- 5. Rango de meses cerrados -----------------------------------------
    first_month, last_month = con.execute(
        """
        select min(month), max(month)
        from _quality_transactions
        where not is_open_month
        """
    ).fetchone()
    open_month = con.execute(
        """
        select distinct month
        from _quality_transactions
        where is_open_month
        """
    ).fetchall()

    # --- 6. Actividad por mes cerrado ---------------------------------------
    monthly = con.execute(
        """
        select
            month::VARCHAR,
            count(distinct company_id),
            count(*)
        from _quality_transactions
        where not is_open_month
        group by month
        order by month
        """
    ).fetchall()
    monthly_activity = [
        {"month": m, "companies": int(c), "transactions": int(n)} for m, c, n in monthly
    ]

    # --- 7. Huecos de datos en transacciones --------------------------------
    tx_total = row_counts["transactions"]["clean"]
    no_cat, no_eur = con.execute(
        """
        select
            count(*) filter (where category_norm is null),
            count(*) filter (where amount_eur is null)
        from _quality_transactions
        """
    ).fetchone()
    data_gaps = {
        "transactions_without_category_pct": round(100.0 * no_cat / tx_total, 2) if tx_total else 0.0,
        "transactions_without_category_rows": int(no_cat),
        "transactions_without_amount_eur_pct": round(100.0 * no_eur / tx_total, 2) if tx_total else 0.0,
        "transactions_without_amount_eur_rows": int(no_eur),
    }

    return {
        "row_counts": row_counts,
        "flags": flags,
        "coverage": coverage,
        "history_months": history,
        "closed_months_range": {
            "first": str(first_month) if first_month else None,
            "last": str(last_month) if last_month else None,
            "open_month": sorted(str(m) for (m,) in open_month),
        },
        "monthly_activity": monthly_activity,
        "data_gaps": data_gaps,
        "row_count_mismatches": mismatches,
    }


def _md_section_row_counts(rep: dict) -> str:
    lines = [
        "## Recuento de filas por tabla",
        "",
        "| tabla | interim | clean | esperado | estado |",
        "|---|---:|---:|---:|---|",
    ]
    for t in TABLE_ORDER:
        rc = rep["row_counts"][t]
        estado = "OK" if rc["ok"] else "MISMATCH"
        lines.append(
            f"| {t} | {rc['interim']} | {rc['clean']} | {rc['expected']} | {estado} |"
        )
    return "\n".join(lines)


def _md_section_flags(rep: dict) -> str:
    lines = ["## Flags de calidad por tabla (ordenados por frecuencia)", ""]
    for t in rep["flags"]:
        counts = rep["flags"][t]
        rows_in = rep["row_counts"].get(t)
        total = rows_in["clean"] if rows_in else None
        lines.append(f"### {t} ({total} filas)" if total is not None else f"### {t}")
        if not counts:
            lines.append("")
            lines.append("Sin flags.")
            continue
        lines += ["", "| flag | filas | % de la tabla |", "|---|---:|---:|"]
        for flag, n in counts.items():
            pct = f"{100.0 * n / total:.2f}%" if total else "-"
            lines.append(f"| `{flag}` | {n} | {pct} |")
    return "\n".join(lines)


def _md_section_coverage(rep: dict) -> str:
    cov = rep["coverage"]
    total = cov["total_companies"]
    lines = [
        "## Cobertura cruzada de empresas",
        "",
        f"Empresas totales en `companies`: **{total}**.",
        "",
        "Empresas distintas que aparecen en cada fuente de actividad:",
        "",
        "| fuente | empresas | % sobre el total |",
        "|---|---:|---:|",
    ]
    names = {
        "transacciones": "Transacciones",
        "cuentas_bancarias": "Cuentas bancarias (banking_products)",
        "saldos": "Saldos (balances)",
        "facturas": "Facturas (invoices)",
        "deuda": "Deuda (debt_products)",
    }
    for src, d in cov["sources"].items():
        lines.append(f"| {names[src]} | {d['companies']} | {d['pct']} % |")
    lines += [
        "",
        "Una empresa ausente en facturas o deuda **no significa cero facturacion o cero deuda**:"
        " significa que esa fuente no tiene registros para ella.",
    ]
    return "\n".join(lines)


def _md_section_history(rep: dict) -> str:
    h = rep["history_months"]
    r = rep["closed_months_range"]
    lines = [
        "## Distribucion de meses de historia por empresa",
        "",
        "Meses **cerrados** con transacciones por empresa (se excluye el mes abierto):",
        "",
        "| estadistico | meses cerrados |",
        "|---|---:|",
        f"| minimo | {h['min_closed_months']} |",
        f"| p25 | {h['p25_closed_months']} |",
        f"| mediana | {h['median_closed_months']} |",
        f"| p75 | {h['p75_closed_months']} |",
        f"| maximo | {h['max_closed_months']} |",
        "",
        f"- Empresas con historia: **{h['companies_with_history']}**.",
        f"- Empresas con **menos de 6 meses cerrados**: **{h['companies_lt_6_closed_months']}**.",
        "",
        f"Rango de meses cerrados del dataset: **{r['first']}** a **{r['last']}**."
        f" Mes abierto: **{', '.join(r['open_month'])}**.",
    ]
    return "\n".join(lines)


def _md_section_monthly(rep: dict) -> str:
    lines = [
        "## Actividad por mes cerrado",
        "",
        "Empresas con actividad y numero de transacciones por mes cerrado."
        " Los bordes del periodo (primer y ultimo mes) pueden aparecer deprimidos"
        " si el extracto empezó o terminó a mitad de mes:",
        "",
        "| mes | empresas con actividad | transacciones |",
        "|---|---:|---:|",
    ]
    for row in rep["monthly_activity"]:
        lines.append(f"| {row['month']} | {row['companies']} | {row['transactions']} |")
    return "\n".join(lines)


def _md_section_gaps(rep: dict) -> str:
    g = rep["data_gaps"]
    lines = [
        "## Huecos de datos en transacciones",
        "",
        f"- Transacciones **sin categoria** (`category_norm` NULL): "
        f"**{g['transactions_without_category_rows']}** filas "
        f"(**{g['transactions_without_category_pct']} %** del total).",
        f"- Transacciones **sin `amount_eur`** (no convertibles a EUR): "
        f"**{g['transactions_without_amount_eur_rows']}** filas "
        f"(**{g['transactions_without_amount_eur_pct']} %** del total).",
    ]
    return "\n".join(lines)


def _md_section_avisos(rep: dict) -> str:
    cov = rep["coverage"]
    total = cov["total_companies"]
    facturas = cov["sources"]["facturas"]["pct"]
    deuda = cov["sources"]["deuda"]["pct"]
    r = rep["closed_months_range"]
    open_month = ", ".join(r["open_month"])
    lines = [
        "## Avisos para la capa de features",
        "",
        "Hechos que cualquier consumidor de estos datos DEBE conocer antes de usarlos:",
        "",
        f"- **{open_month} es un mes abierto y no comparable.** Solo contiene parte de la"
        " actividad (datos parciales); no debe mezclarse con los meses cerrados en"
        " agregaciones, ratios ni ventanas temporales.",
        "- **Los saldos son un unico snapshot, no una serie historica.** Toda la tabla"
        " `balances` corresponde a una sola fecha de corte (2026-09-01); no hay evolucion"
        " mensual de saldos y por tanto no se pueden calcular tendencias de saldo.",
        f"- **Cobertura parcial de facturas y deuda.** Solo el **{facturas} %** de las"
        f" {total} empresas tiene facturas y el **{deuda} %** tiene deuda registrada."
        " La ausencia de registros en esas tablas NO significa cero facturacion o cero"
        " deuda: significa que la fuente no aporta datos para esa empresa.",
        "- **Los importes extremos estan marcados pero NO recortados.** Las filas con"
        " `amount_extreme` (y los centinelas de saldo) conservan su valor original;"
        " cualquier agregacion sensible a outliers debe tratarlos explicitamente.",
        "- **FX conservador.** El nominal EUR se conserva por identidad. Una conversion"
        " reportada hacia EUR requiere tipo positivo, finito y distinto de 1 entre"
        " monedas diferentes. El resto queda desconocido, sin tipos estimados. Los"
        " agregados incompletos no deben presentarse como totales ni como ceros.",
        f"- **Un {rep['data_gaps']['transactions_without_category_pct']} % de las"
        " transacciones no tiene categoria** (`category_norm` NULL). Todo feature basado"
        " en categorías debe contemplar explícitamente el valor faltante.",
        "- Hay filas con claves huerfanas (`fk_orphan`): empresas, productos o"
        " contrapartes que no resuelven contra las tablas maestras. Estan marcadas, no"
        " eliminadas.",
    ]
    return "\n".join(lines)


def _build_markdown(rep: dict) -> str:
    parts = [
        "# Informe de calidad consolidado (T4)",
        "",
        "Fuente: `data/clean/*.parquet` + `reports/quality/dimensions.json` +"
        " `reports/quality/facts.json`.",
        "",
        _md_section_row_counts(rep),
        "",
        _md_section_flags(rep),
        "",
        _md_section_coverage(rep),
        "",
        _md_section_history(rep),
        "",
        _md_section_monthly(rep),
        "",
        _md_section_gaps(rep),
        "",
        _md_section_avisos(rep),
        "",
    ]
    return "\n".join(parts)


def build_quality_report(con: duckdb.DuckDBPyConnection) -> dict:
    """Construye el informe de calidad y escribe summary.json + summary.md."""
    report = _build_report(con)

    json_path = paths.REPORTS_DIR / "summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    md_path = paths.REPORTS_DIR / "summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_build_markdown(report))

    report["_written"] = [str(json_path), str(md_path)]
    return report
