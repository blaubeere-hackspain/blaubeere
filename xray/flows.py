"""V0' - Clasificador de flujos economicos por reglas decididas.

Lee data/clean/transactions.parquet y escribe data/interim/tx_flow_class.parquet
con una fila por transaccion: _src_row, transaction_id, flow_class, flow_source,
rule_id, es_atipico.
"""

from pathlib import Path

from xray import paths
from xray.contracts import CleanResult

FLOW_CLASSES = (
    "operating_in",
    "operating_out",
    "financing_in",
    "financing_out",
    "investment_in",
    "investment_out",
    "transfer",
    "non_economic",
    "unknown",
)

CAT_IN = "'collection','bulk_collection','pos_settlement','cash_settlement','cash_settlements','tax_refund'"
CAT_OUT = "'payment','bulk_payment','utility','salary','tax','social_security','fee'"
CAT_REFUND = "'collection_refund','payment_refund'"
CAT_FIN_OUT = "'debt_repayment','interest_charge'"

# Paso 2: reglas de description, en orden, primera que case gana.
_DESC_RULES = [
    # (flow_class, rule_id, sql_condition sobre d = upper(trim(description)))
    # NOTA V0''': destino 'transfer' (antes 'non_economic'): el banco etiqueta
    # con 'transfer' los patrones hermanos de 'SCF-AJUS.SALDO%' que si conocia
    # (4.152 filas); se aplica la misma lectura al patron sin etiqueta.
    ("transfer", "desc:ajuste_saldo", "d LIKE 'SCF-AJUS.SALDO%'"),
    (
        "non_economic",
        "desc:compensacion_conciliacion",
        "d LIKE '%COMPENSATION FOR MISSING OR EXCESS%'",
    ),
    (
        "non_economic",
        "desc:ajuste_retencion",
        "d LIKE '%MANUAL QUITAR RETENCION%' OR d LIKE 'AJUSTE RETENCION%'",
    ),
    ("transfer", "desc:disposicion_efectivo", "d LIKE 'DISP.ENTREG.EFECT.%'"),
    (
        "operating_out",
        "desc:comision",
        "d LIKE '%STRIPE_FEE%' OR d LIKE '% FEE%'",
    ),
    ("operating_in", "desc:venta", "d LIKE 'VT.A %'"),
    ("operating_out", "desc:compra", "d LIKE 'PR.A %'"),
    (
        "operating_in",
        "desc:cierre_tpv",
        "d LIKE 'FECHO POS-PERIODO%' OR d LIKE 'FECHO TPA%'",
    ),
    ("operating_in", "desc:liquidacion", "d LIKE 'LIQUIDACION EFECTUADA EL%'"),
    ("operating_in", "desc:abono_menor", "d LIKE 'AB.NT.BZ%'"),
    (
        "operating_in",
        "desc:cobro_menor",
        "d LIKE 'ON %' AND direction = 'in'",
    ),
    (
        "operating_in",
        "desc:cobro_deudor",
        "d LIKE '%DEBTOR NUMBER%'",
    ),
    (
        "operating_out",
        "desc:comision_banco",
        "d LIKE '%CHARGE FOR%'",
    ),
    (
        "operating_in",
        "desc:cobro_pasarela",
        "d LIKE '% CHARGE'",
    ),
    (
        "operating_in",
        "desc:cobro_pasarela_pago",
        "d LIKE '% PAYMENT'",
    ),
    (
        "transfer",
        "desc:barrido_pasarela",
        "d LIKE '% PAYOUT'",
    ),
    (
        "operating_in",
        "desc:cobro_tarjeta",
        "d LIKE '%******%'",
    ),
]

def _cat_class_expr(cat: str, direction: str) -> str:
    """Expresion CASE categoria -> flow_class, unica fuente del mapeo.

    La usan el paso 1 (category_norm de la fila) y el paso 1.5 (categoria
    transferida del patron), de modo que no puedan divergir.
    """
    return f"""CASE
                    WHEN {cat} IS NULL THEN NULL
                    WHEN {cat} IN ({CAT_IN}) THEN 'operating_in'
                    WHEN {cat} IN ({CAT_OUT}) THEN 'operating_out'
                    WHEN {cat} IN ({CAT_REFUND}) THEN
                        CASE WHEN {direction} = 'in' THEN 'operating_in' ELSE 'operating_out' END
                    WHEN {cat} IN ({CAT_FIN_OUT}) THEN 'financing_out'
                    WHEN {cat} = 'investment_return' THEN 'investment_in'
                    WHEN {cat} = 'investment_deployment' THEN 'investment_out'
                    WHEN {cat} IN ('transfer', 'cash_withdrawal', 'pos_withdrawal') THEN 'transfer'
                    ELSE 'unknown'
                END"""


_DESC_CASE_RULE = "\n".join(
    f"        WHEN {cond} THEN '{rid}'" for _, rid, cond in _DESC_RULES
)
_DESC_CLASS_OF_RULE = "\n".join(
    f"                WHEN desc_rule = '{rid}' THEN '{fc}'" for fc, rid, _ in _DESC_RULES
)


def classify_flows(con) -> CleanResult:
    # Estado previo del informe (para comparativas antes/despues)
    before = None
    try:
        import json as _json

        with open(paths.REPORTS_DIR / "flows.json") as _f:
            before = _json.load(_f)
    except (OSError, ValueError):
        before = None

    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW tx AS
        SELECT * FROM read_parquet('{paths.CLEAN_DIR / 'transactions.parquet'}')
        """
    )
    rows_in = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE classified AS
        WITH base AS (
            SELECT
                t._src_row,
                t.transaction_id,
                t.company_id,
                t.month,
                t.category_norm,
                t.direction,
                t.product_source,
                upper(trim(t.description)) AS d,
                regexp_replace(upper(trim(coalesce(t.description, ''))), '[0-9]+', '#', 'g') AS patron,
                abs(t.amount_eur) >= 10000000.0 AS es_atipico
            FROM tx t
        ),
        step1 AS (
            SELECT
                b.*,
                {_cat_class_expr('b.category_norm', 'b.direction')} AS flow_class,
                CASE
                    WHEN b.category_norm IS NULL THEN NULL
                    WHEN b.category_norm IN ({CAT_IN}, {CAT_OUT}, {CAT_REFUND}, {CAT_FIN_OUT})
                        OR b.category_norm IN ('investment_return', 'investment_deployment',
                                               'transfer', 'cash_withdrawal', 'pos_withdrawal')
                        THEN 'cat:' || b.category_norm
                    ELSE 'cat:no_mapeada'
                END AS rule_id,
                CASE WHEN b.category_norm IS NULL THEN NULL ELSE 'category' END AS flow_source
            FROM base b
        ),
        -- Paso 1.5: tabla de consulta patron -> categoria, construida SOLO con
        -- evidencia del banco de la MISMA empresa y direccion, meses anteriores,
        -- y patrones con exactamente UNA categoria distinta en ese corte.
        -- Se excluye el patron vacio; no se aprende de otras empresas ni del futuro.
        patron_cat AS (
            SELECT s._src_row, MIN(e.category_norm) AS category_norm,
                   max(e.first_month) AS evidence_month
            FROM step1 s
            JOIN (
                SELECT t.company_id, t.direction,
                    regexp_replace(upper(trim(coalesce(t.description, ''))), '[0-9]+', '#', 'g') AS patron,
                    t.category_norm, min(t.month) AS first_month
                FROM tx t
                WHERE NOT t.is_open_month AND t.is_booked AND t.category_norm IS NOT NULL
                GROUP BY 1, 2, 3, 4
            ) e ON e.company_id = s.company_id AND e.direction = s.direction
               AND e.patron = s.patron AND e.first_month < s.month
            WHERE s.category_norm IS NULL AND s.patron <> ''
            GROUP BY s._src_row
            HAVING COUNT(DISTINCT e.category_norm) = 1
        ),
        -- Paso 1.5: donde el banco ya categorizo el patron de forma inequivoca,
        -- su etiqueta tiene precedencia sobre las reglas manuales de description
        -- (que se aplican despues, en el paso 2, solo a lo que siga en unknown).
        step1_5 AS (
            SELECT
                s.* EXCLUDE (flow_class, rule_id, flow_source),
                CASE
                    WHEN s.category_norm IS NULL AND pc.category_norm IS NOT NULL
                        THEN {_cat_class_expr('pc.category_norm', 's.direction')}
                    ELSE s.flow_class
                END AS flow_class,
                CASE
                    WHEN s.category_norm IS NULL AND pc.category_norm IS NOT NULL
                        THEN 'transf:' || pc.category_norm
                    ELSE s.rule_id
                END AS rule_id,
                CASE
                    WHEN s.category_norm IS NULL AND pc.category_norm IS NOT NULL
                        THEN 'transferida'
                    ELSE s.flow_source
                END AS flow_source,
                pc.evidence_month
            FROM step1 s
            LEFT JOIN patron_cat pc USING (_src_row)
        ),
        step2 AS (
            SELECT
                s.*,
                CASE
                    WHEN s.flow_source IS NOT NULL THEN NULL
{_DESC_CASE_RULE}
                    ELSE NULL
                END AS desc_rule
            FROM step1_5 s
        ),
        step3 AS (
            SELECT
                CASE
                    WHEN flow_source IS NOT NULL THEN flow_class
                    WHEN desc_rule IS NOT NULL THEN CASE
{_DESC_CLASS_OF_RULE}
                    END
                    ELSE 'unknown'
                END AS flow_class,
                CASE
                    WHEN flow_source IS NOT NULL THEN rule_id
                    WHEN desc_rule IS NOT NULL THEN desc_rule
                    ELSE 'unknown'
                END AS rule_id,
                CASE
                    WHEN flow_source IS NOT NULL THEN flow_source
                    WHEN desc_rule IS NOT NULL THEN 'rule'
                    ELSE NULL
                END AS flow_source,
                product_source,
                direction,
                _src_row,
                transaction_id,
                es_atipico,
                evidence_month
            FROM step2
        ),
        step4 AS (
            SELECT *,
                coalesce((flow_class LIKE '%_in' AND direction <> 'in')
                      OR (flow_class LIKE '%_out' AND direction <> 'out'), false)
                    AS direction_conflict
            FROM step3
        )
        SELECT
            _src_row,
            transaction_id,
            CASE WHEN flow_class LIKE '%_in' OR flow_class LIKE '%_out'
                 THEN CASE WHEN direction IN ('in', 'out')
                           THEN split_part(flow_class, '_', 1) || '_' || direction
                           ELSE 'unknown' END
                 ELSE flow_class END AS flow_class,
            flow_source,
            rule_id,
            es_atipico,
            direction_conflict,
            evidence_month
        FROM step4
        """
    )

    out_path = paths.INTERIM_DIR / "tx_flow_class.parquet"
    con.execute(
        f"""
        COPY (
            SELECT
                _src_row::BIGINT AS _src_row,
                transaction_id::VARCHAR AS transaction_id,
                flow_class::VARCHAR AS flow_class,
                flow_source::VARCHAR AS flow_source,
                rule_id::VARCHAR AS rule_id,
                es_atipico::BOOLEAN AS es_atipico,
                direction_conflict::BOOLEAN AS direction_conflict,
                evidence_month::DATE AS evidence_month
            FROM classified
            ORDER BY _src_row
        ) TO '{out_path}' (FORMAT PARQUET)
        """
    )

    rows_out = con.execute("SELECT COUNT(*) FROM classified").fetchone()[0]

    # Categorias no mapeadas (para el informe/notas)
    uncat = [
        r[0]
        for r in con.execute(
            """
            SELECT DISTINCT category_norm FROM tx
            WHERE category_norm IS NOT NULL
              AND category_norm NOT IN (
                'collection','bulk_collection','pos_settlement','cash_settlement',
                'cash_settlements','tax_refund',
                'payment','bulk_payment','utility','salary','tax','social_security','fee',
                'collection_refund','payment_refund',
                'debt_repayment','interest_charge',
                'investment_return','investment_deployment',
                'transfer','cash_withdrawal','pos_withdrawal')
            ORDER BY 1
            """
        ).fetchall()
        if r[0] is not None
    ]

    result = CleanResult(
        table="tx_flow_class",
        rows_in=rows_in,
        rows_out=rows_out,
        output_path=Path(out_path),
        notes=(
            [f"categorias sin mapear -> unknown: {', '.join(uncat)}"] if uncat else []
        ),
    )
    result.assert_no_row_loss()

    _write_report(con, before)
    return result


def _write_report(con, before: dict | None = None) -> None:
    import json

    closed_booked = (
        "tx t JOIN classified c USING (_src_row) "
        "WHERE NOT t.is_open_month AND t.is_booked"
    )

    q = lambda suffix: f"FROM {closed_booked}{suffix}"

    by_class = con.execute(
        f"""
        SELECT c.flow_class, COUNT(*) AS n, SUM(abs(t.amount_eur)) AS eur
        FROM {closed_booked}
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    total_rows = sum(r[1] for r in by_class)
    total_eur = sum(r[2] or 0 for r in by_class)
    unk = next((r for r in by_class if r[0] == "unknown"), None)
    unk_rows = unk[1] if unk else 0
    unk_eur = (unk[2] or 0) if unk else 0

    by_rule = con.execute(
        f"""
        SELECT c.rule_id,
               string_agg(DISTINCT c.flow_class, ',' ORDER BY c.flow_class),
               COUNT(*) AS n, SUM(abs(t.amount_eur)) AS eur
        FROM {closed_booked}
        GROUP BY 1 ORDER BY n DESC
        """
    ).fetchall()

    by_source = con.execute(
        f"""
        SELECT COALESCE(c.flow_source, 'unknown') AS flow_source, COUNT(*) AS n,
               SUM(abs(t.amount_eur)) AS eur
        FROM {closed_booked}
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    before_rules = (before or {}).get("by_rule_id", {})
    rules_before_after = {}
    for rid, _fc, n, _eur in by_rule:
        rules_before_after[rid] = {
            "before": (before_rules.get(rid) or {}).get("rows"),
            "after": n,
        }
    for rid, info in before_rules.items():
        if rid not in rules_before_after:
            rules_before_after[rid] = {"before": info.get("rows"), "after": 0}

    atipico = con.execute(
        f"""
        SELECT c.flow_class, COUNT(*) AS n
        FROM {closed_booked} AND c.es_atipico = TRUE
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    atipico_total = con.execute(
        f"SELECT COUNT(*) FROM {closed_booked} AND c.es_atipico = TRUE"
    ).fetchone()[0]

    per_company = con.execute(
        f"""
        WITH per_co AS (
            SELECT t.company_id, COUNT(*) AS n,
                   SUM(CASE WHEN c.flow_class = 'unknown' THEN 1 ELSE 0 END) AS unk_n
            FROM {closed_booked}
            GROUP BY 1
        )
        SELECT MIN(unk_n * 100.0 / n), quantile_cont(unk_n * 100.0 / n, 0.25),
               quantile_cont(unk_n * 100.0 / n, 0.5),
               quantile_cont(unk_n * 100.0 / n, 0.75),
               MAX(unk_n * 100.0 / n)
        FROM per_co
        """
    ).fetchone()

    n_companies = con.execute(
        f"SELECT COUNT(DISTINCT t.company_id) FROM {closed_booked}"
    ).fetchone()[0]
    companies_gt40 = con.execute(
        f"""
        WITH per_co AS (
            SELECT t.company_id, COUNT(*) AS n,
                   SUM(CASE WHEN c.flow_class = 'unknown' THEN 1 ELSE 0 END) AS unk_n
            FROM {closed_booked}
            GROUP BY 1
        )
        SELECT COUNT(*) FROM per_co WHERE unk_n * 100.0 / n > 40
        """
    ).fetchone()[0]

    report = {
        "scope": "meses cerrados + booked; EUR = volumen absoluto convertible, no neto",
        "transfer_policy": "misma empresa y direccion, evidencia de meses anteriores",
        "fx_unknown_rows": con.execute(
            f"SELECT count(*) FROM {closed_booked} AND t.amount_eur IS NULL"
        ).fetchone()[0],
        "direction_conflicts": con.execute(
            f"SELECT count(*) FROM {closed_booked} AND c.direction_conflict"
        ).fetchone()[0],
        "total_rows": total_rows,
        "total_eur": total_eur,
        "by_flow_class": {
            fc: {"rows": n, "eur": eur}
            for fc, n, eur in by_class
        },
        "unknown_pct_rows": unk_rows * 100.0 / total_rows if total_rows else 0,
        "unknown_pct_eur": unk_eur * 100.0 / total_eur if total_eur else 0,
        "by_flow_source": {
            src: {"rows": n, "eur": eur} for src, n, eur in by_source
        },
        "by_rule_id": {
            rid: {"flow_class": fc, "rows": n, "eur": eur}
            for rid, fc, n, eur in by_rule
        },
        "by_rule_id_before_after": rules_before_after,
        "atipico_by_flow_class": {fc: n for fc, n in atipico},
        "atipico_total": atipico_total,
        "unknown_per_company_pct": {
            "min": per_company[0],
            "p25": per_company[1],
            "median": per_company[2],
            "p75": per_company[3],
            "max": per_company[4],
            "companies_gt_40pct": companies_gt40,
            "companies_gt_40pct_before": ((before or {}).get("unknown_per_company_pct") or {}).get(
                "companies_gt_40pct"
            ),
            "n_companies": n_companies,
        },
    }

    paths.ensure_dirs()
    with open(paths.REPORTS_DIR / "flows.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
