import duckdb

from xray.contracts import CleanResult
from xray.marts.common import CASH_PRODUCTS, cash_transactions, company_months, publish_mart, require_panel

PRINCIPAL_RULES = ('cat:debt_repayment', 'transf:debt_repayment')
INTEREST_RULES = ('cat:interest_charge', 'transf:interest_charge')


def build_panel_deuda(con: duckdb.DuckDBPyConnection) -> CleanResult:
    company_months(con)
    cash_transactions(con)
    flows = require_panel(con, 'panel_flujos')
    principal = ','.join(f"'{rule}'" for rule in PRINCIPAL_RULES)
    interest = ','.join(f"'{rule}'" for rule in INTEREST_RULES)
    service = principal + ',' + interest
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE pd_cash AS
        SELECT company_id, month, count(*) AS n_caja,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE flow_class='financing_out' AND rule_id IN ({principal})),0) AS principal_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE flow_class='financing_out' AND rule_id IN ({interest})),0) AS intereses_conocido_eur,
               coalesce(sum(abs(amount_eur)) FILTER (WHERE flow_class='financing_out' AND rule_id NOT IN ({service})),0) AS financiacion_no_desglosada_conocido_eur,
               coalesce(sum(amount_eur) FILTER (WHERE flow_class='financing_in' AND rule_id IN ({service})),0) AS reintegros_deuda_conocido_eur,
               count(*) FILTER (WHERE flow_class='financing_out' AND rule_id IN ({service})) AS n_pagos_servicio,
               count(*) FILTER (WHERE flow_class='financing_out' AND rule_id IN ({service}) AND amount_eur IS NULL) AS n_servicio_sin_eur,
               count(*) FILTER (WHERE flow_class='financing_out' AND rule_id NOT IN ({service})) AS n_financiacion_no_desglosada,
               count(*) FILTER (WHERE flow_class='financing_in' AND rule_id IN ({service}) AND amount_eur IS NULL) AS n_reintegros_sin_eur,
               count(*) FILTER (WHERE flow_class='financing_in' AND rule_id NOT IN ({service})) AS n_entradas_no_desglosadas
        FROM mart_tx WHERE es_caja GROUP BY 1,2
    ''')
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE panel_deuda AS
        WITH ledger AS (
            SELECT company_id, month, count(*) AS n_ledger,
                   coalesce(sum(amount_eur),0) AS ledger_deuda_neto_conocido_eur,
                   count(*) FILTER (WHERE amount_eur IS NULL) AS n_ledger_sin_eur
            FROM mart_tx WHERE product_source='debt' GROUP BY 1,2
        )
        SELECT f.company_id, f.group_id, f.month, f.available_at,
               f.tiene_actividad_caja, f.n_tx_producto_deuda,
               coalesce(a.n_caja,0)::BIGINT AS n_caja,
               a.principal_conocido_eur, a.intereses_conocido_eur,
               a.financiacion_no_desglosada_conocido_eur, a.reintegros_deuda_conocido_eur,
               a.principal_conocido_eur+a.intereses_conocido_eur AS servicio_deuda_conocido_eur,
               coalesce(a.n_pagos_servicio,0)::BIGINT AS n_pagos_servicio,
               coalesce(a.n_servicio_sin_eur,0)::BIGINT AS n_servicio_sin_eur,
               coalesce(a.n_financiacion_no_desglosada,0)::BIGINT AS n_financiacion_no_desglosada,
               f.n_ambiguos_salida AS n_posibles_pagos_no_identificados,
               CASE WHEN f.tiene_actividad_caja AND a.n_servicio_sin_eur=0
                          AND a.n_financiacion_no_desglosada=0 AND f.n_ambiguos_salida=0
                    THEN a.principal_conocido_eur+a.intereses_conocido_eur END AS servicio_deuda_eur,
               CASE WHEN f.tiene_actividad_caja AND a.n_reintegros_sin_eur=0
                          AND a.n_entradas_no_desglosadas=0 AND f.n_ambiguos_entrada=0
                    THEN a.reintegros_deuda_conocido_eur END AS reintegros_deuda_eur,
               servicio_deuda_eur-reintegros_deuda_eur AS servicio_deuda_neto_eur,
               (a.n_pagos_servicio-a.n_servicio_sin_eur)::DOUBLE/nullif(a.n_pagos_servicio,0) AS cobertura_servicio_eur_pct,
               l.ledger_deuda_neto_conocido_eur, coalesce(l.n_ledger_sin_eur,0)::BIGINT AS n_ledger_sin_eur,
               f.financiacion_salidas_conocido_eur AS financiacion_salidas_caja_conocido_eur
        FROM {flows} f
        LEFT JOIN pd_cash a USING (company_id,month)
        LEFT JOIN ledger l USING (company_id,month)
    ''')
    mismatch = con.execute(f'''
        SELECT count(*) FROM panel_deuda d JOIN {flows} f
        USING (company_id,month) WHERE d.n_caja<>f.n_tx_caja
    ''').fetchone()[0]
    if mismatch:
        raise ValueError('panel_flujos no corresponde a las transacciones actuales')
    stats = con.execute('''
        SELECT count(*) FILTER (WHERE servicio_deuda_eur IS NOT NULL),
               count(*) FILTER (WHERE n_pagos_servicio>0), sum(n_tx_producto_deuda)
        FROM panel_deuda
    ''').fetchone()
    return publish_mart(con, 'panel_deuda', con.execute('SELECT count(*) FROM mart_tx').fetchone()[0], {
        'perimetro': {'source': 'banking', 'product_types': list(CASH_PRODUCTS)},
        'filas_servicio_observable': stats[0], 'filas_con_pagos_identificados': stats[1],
        'apuntes_en_productos_deuda_no_sumados': stats[2],
        'signos': 'servicio bruto positivo; reintegros separados; neto puede ser negativo',
        'limites': 'Servicio identificado en cuentas de caja observadas, no deuda total de la empresa. No se usa outstanding actual ni se proyectan calendarios.',
    }, {
        'conciliacion_financiacion': 'abs(servicio_deuda_conocido_eur+financiacion_no_desglosada_conocido_eur-financiacion_salidas_caja_conocido_eur)>0.01',
        'sin_caja': 'NOT tiene_actividad_caja AND servicio_deuda_eur IS NOT NULL',
        'servicio_negativo': 'servicio_deuda_eur<0 OR principal_conocido_eur<0 OR intereses_conocido_eur<0',
        'cobertura': 'cobertura_servicio_eur_pct<0 OR cobertura_servicio_eur_pct>1',
        'neto': 'abs(servicio_deuda_neto_eur-(servicio_deuda_eur-reintegros_deuda_eur))>0.01',
    })
