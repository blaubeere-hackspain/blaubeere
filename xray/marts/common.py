import json

from xray import paths
from xray.contracts import CleanResult
from xray.flows import FLOW_CLASSES
from xray.period import FIRST_MONTH, LAST_MONTH, MONTHS_SQL

CASH_PRODUCTS = ('checking', 'saving', 'wallet')
AMBIGUOUS_CLASSES = ('unknown', 'transfer', 'non_economic')
VIEW_CONTRACT_VERSION = 1


def parquet(path):
    return "read_parquet('" + str(path).replace("'", "''") + "')"


def company_months(con):
    companies = parquet(paths.CLEAN_DIR / 'companies.parquet')
    n, unique, present = con.execute(
        f'SELECT count(*), count(DISTINCT company_id), count(company_id) FROM {companies}'
    ).fetchone()
    if n != unique or n != present:
        raise ValueError('companies: claves nulas o duplicadas')
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW mart_grid AS
        SELECT c.company_id, c.group_id, m::DATE AS month, last_day(m) AS available_at
        FROM {companies} c CROSS JOIN {MONTHS_SQL} g(m)
    ''')


def cash_transactions(con):
    transactions = parquet(paths.CLEAN_DIR / 'transactions.parquet')
    labels = parquet(paths.INTERIM_DIR / 'tx_flow_class.parquet')
    for source in (transactions, labels):
        n, row_ids, ids = con.execute(
            f'SELECT count(*), count(DISTINCT _src_row), count(DISTINCT transaction_id) FROM {source}'
        ).fetchone()
        if n != row_ids or n != ids:
            raise ValueError('transactions/clasificacion: claves nulas o duplicadas')
    missing = con.execute(f'''
        SELECT count(*) FROM {transactions} t FULL OUTER JOIN {labels} f
        USING (_src_row, transaction_id)
        WHERE t.transaction_id IS NULL OR f.transaction_id IS NULL
    ''').fetchone()[0]
    if missing:
        raise ValueError(f'Clasificacion no alineada con transactions: {missing} filas')
    classes = ','.join(f"'{value}'" for value in FLOW_CLASSES)
    if con.execute(f'SELECT count(*) FROM {labels} WHERE flow_class IS NULL OR rule_id IS NULL OR flow_class NOT IN ({classes})').fetchone()[0]:
        raise ValueError('Clasificacion fuera de contrato')
    products = ','.join(f"'{value}'" for value in CASH_PRODUCTS)
    ambiguous = ','.join(f"'{value}'" for value in AMBIGUOUS_CLASSES)
    con.execute(f'''
        CREATE OR REPLACE TEMP VIEW mart_tx AS
        SELECT t.*, f.flow_class, f.rule_id, f.flow_source, f.es_atipico,
               f.direction_conflict,
               coalesce(t.product_source='banking' AND t.product_type IN ({products}), false) AS es_caja,
               (f.flow_class IN ({ambiguous}) AND t.direction IS DISTINCT FROM 'zero') AS es_ambiguo
        FROM {transactions} t JOIN {labels} f USING (_src_row, transaction_id)
        WHERE t.is_booked AND NOT t.is_open_month
          AND t.month BETWEEN DATE '{FIRST_MONTH}' AND DATE '{LAST_MONTH}'
    ''')
    if con.execute(f'''SELECT count(*) FROM mart_tx t ANTI JOIN
        {parquet(paths.CLEAN_DIR / 'companies.parquet')} c USING (company_id)''').fetchone()[0]:
        raise ValueError('Transacciones sin empresa en el universo del panel')
    if con.execute('SELECT count(*) FROM mart_tx WHERE amount_eur IS NOT NULL AND NOT isfinite(amount_eur)').fetchone()[0]:
        raise ValueError('Importes EUR no finitos')


def deficit_state(lower, upper):
    return (f'CASE WHEN round({upper},2)<0 THEN true '
            f'WHEN round({lower},2)>=0 THEN false END')


def reasons_sql(reasons):
    terms = [f"CASE WHEN {condition} THEN ['{name}'] ELSE [] END" for name, condition in reasons.items()]
    return 'list_concat(' + ','.join(terms) + ')' if terms else '[]::VARCHAR[]'


def require_panel(con, name):
    source = parquet(paths.MARTS_DIR / f'{name}.parquet')
    n, unique = con.execute(f'SELECT count(*),count(DISTINCT (company_id,month)) FROM {source}').fetchone()
    missing = con.execute(f'''
        SELECT count(*) FROM mart_grid g FULL OUTER JOIN {source} p USING (company_id,month)
        WHERE g.company_id IS NULL OR p.company_id IS NULL
    ''').fetchone()[0]
    if n != unique or missing:
        raise ValueError(f'{name}: panel de entrada fuera del grano comun')
    return source


def publish_mart(con, name, rows_in, report, checks=None):
    count, unique, present = con.execute(
        f'SELECT count(*), count(DISTINCT (company_id,month)), '
        f'count(*) FILTER (WHERE company_id IS NOT NULL AND month IS NOT NULL) FROM {name}'
    ).fetchone()
    expected = con.execute('SELECT count(*) FROM mart_grid').fetchone()[0]
    if count != expected or count != unique or count != present:
        raise ValueError(f'{name}: grano invalido ({count}, esperado {expected})')
    for label, condition in (checks or {}).items():
        invalid = con.execute(f'SELECT count(*) FROM {name} WHERE {condition}').fetchone()[0]
        if invalid:
            raise ValueError(f'{name}: {label}: {invalid} filas')
    paths.MARTS_DIR.mkdir(parents=True, exist_ok=True)
    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output = paths.MARTS_DIR / f'{name}.parquet'
    target = str(output).replace("'", "''")
    con.execute(f"COPY (SELECT * FROM {name} ORDER BY company_id, month) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    report = {
        'mart': name, 'contract_version': VIEW_CONTRACT_VERSION,
        'grain': ['company_id', 'month'], 'rows': count, 'input_rows': rows_in,
        'columns': {row[0]: row[1] for row in con.execute(f'DESCRIBE {name}').fetchall()},
        **report,
    }
    (paths.REPORTS_DIR / f'{name}.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str, allow_nan=False) + '\n'
    )
    return CleanResult(table=name, rows_in=rows_in, rows_out=count, output_path=output, row_preserving=False)
