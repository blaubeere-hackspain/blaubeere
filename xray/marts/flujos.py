import duckdb

from xray.contracts import CleanResult
from xray.marts.common import CASH_PRODUCTS, cash_transactions, company_months, publish_mart

BUCKETS = {
    'cobros_operativos': "flow_class='operating_in'",
    'pagos_operativos': "flow_class='operating_out'",
    'financiacion_entradas': "flow_class='financing_in'",
    'financiacion_salidas': "flow_class='financing_out'",
    'inversion_entradas': "flow_class='investment_in'",
    'inversion_salidas': "flow_class='investment_out'",
    'transferencias_entradas': "flow_class='transfer' AND direction='in'",
    'transferencias_salidas': "flow_class='transfer' AND direction='out'",
    'ajustes_entradas': "flow_class='non_economic' AND direction='in'",
    'ajustes_salidas': "flow_class='non_economic' AND direction='out'",
    'sin_clasificar_entradas': "flow_class='unknown' AND direction='in'",
    'sin_clasificar_salidas': "flow_class='unknown' AND direction='out'",
}


def build_panel_flujos(con: duckdb.DuckDBPyConnection) -> CleanResult:
    company_months(con)
    cash_transactions(con)
    counts = {
        'n_tx_caja': 'true',
        'n_sin_eur': 'amount_eur IS NULL',
        'n_ambiguos': 'es_ambiguo',
        'n_ambiguos_entrada': "es_ambiguo AND (direction='in' OR direction IS NULL)",
        'n_ambiguos_salida': "es_ambiguo AND (direction='out' OR direction IS NULL)",
        'n_operativo_sin_eur': "flow_class IN ('operating_in','operating_out') AND amount_eur IS NULL",
        'n_cobros_sin_eur': "flow_class='operating_in' AND amount_eur IS NULL",
        'n_pagos_sin_eur': "flow_class='operating_out' AND amount_eur IS NULL",
        'n_ambiguos_sin_eur': 'es_ambiguo AND amount_eur IS NULL',
        'n_atipicos': 'es_atipico IS TRUE',
        'n_atipicos_operativos': "es_atipico IS TRUE AND (flow_class IN ('operating_in','operating_out') OR es_ambiguo)",
        'n_atipicos_desconocidos': 'es_atipico IS NULL',
        'n_conflictos_direccion': 'direction_conflict',
    }
    sums = {f'{name}_conocido_eur': f'coalesce(sum(abs(amount_eur)) FILTER (WHERE {condition}),0)'
            for name, condition in BUCKETS.items()}
    sums.update({
        'neto_caja_conocido_eur': 'coalesce(sum(amount_eur),0)',
        'volumen_caja_conocido_eur': 'coalesce(sum(abs(amount_eur)),0)',
        'ambiguo_entradas_conocido_eur': "coalesce(sum(abs(amount_eur)) FILTER (WHERE es_ambiguo AND direction='in'),0)",
        'ambiguo_salidas_conocido_eur': "coalesce(sum(abs(amount_eur)) FILTER (WHERE es_ambiguo AND direction='out'),0)",
    })
    for name, condition in {
        'operativo_sin_atipicos_conocido_eur': "flow_class IN ('operating_in','operating_out')",
        'ambiguo_entradas_sin_atipicos_conocido_eur': "es_ambiguo AND direction='in'",
        'ambiguo_salidas_sin_atipicos_conocido_eur': "es_ambiguo AND direction='out'",
    }.items():
        value = 'amount_eur' if name.startswith('operativo_') else 'abs(amount_eur)'
        sums[name] = f'coalesce(sum({value}) FILTER (WHERE ({condition}) AND es_atipico IS NOT TRUE),0)'
    expressions = [f'count(*) FILTER (WHERE {condition}) AS {name}' for name, condition in counts.items()]
    expressions += [f'{expression} AS {name}' for name, expression in sums.items()]
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE pf_agg AS
        SELECT company_id, month, count(DISTINCT product_id) AS n_cuentas_activas,
               {', '.join(expressions)}
        FROM mart_tx WHERE es_caja GROUP BY company_id, month
    ''')
    projected_counts = ', '.join(f'coalesce(a.{name},0)::BIGINT AS {name}' for name in counts)
    projected_sums = ', '.join(f'a.{name}::DOUBLE AS {name}' for name in sums)
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE pf_grid AS
        WITH all_tx AS (
            SELECT company_id, month, count(*) AS n_tx_total,
                   count(*) FILTER (WHERE NOT es_caja) AS n_tx_fuera_perimetro,
                   count(*) FILTER (WHERE product_source='debt') AS n_tx_producto_deuda,
                   count(*) FILTER (WHERE product_source='orphan') AS n_tx_huerfanos,
                   coalesce(sum(amount_eur) FILTER (WHERE NOT es_caja),0) AS fuera_perimetro_conocido_eur
            FROM mart_tx GROUP BY 1,2
        ), history AS (
            SELECT company_id, min(month) AS first_month FROM pf_agg GROUP BY 1
        )
        SELECT g.*, coalesce(t.n_tx_total,0)::BIGINT AS n_tx_total,
               coalesce(t.n_tx_fuera_perimetro,0)::BIGINT AS n_tx_fuera_perimetro,
               coalesce(t.n_tx_producto_deuda,0)::BIGINT AS n_tx_producto_deuda,
               coalesce(t.n_tx_huerfanos,0)::BIGINT AS n_tx_huerfanos,
               t.fuera_perimetro_conocido_eur,
               coalesce(a.n_cuentas_activas,0)::BIGINT AS n_cuentas_activas,
               {projected_counts}, {projected_sums},
               coalesce(a.n_tx_caja,0)>0 AS tiene_actividad_caja,
               CASE WHEN g.month>=h.first_month THEN h.first_month END AS primer_mes_caja,
               CASE WHEN g.month>=h.first_month THEN date_diff('month',h.first_month,g.month) END AS meses_observados,
               a.cobros_operativos_conocido_eur-a.pagos_operativos_conocido_eur AS operativo_identificado_conocido_eur
        FROM mart_grid g LEFT JOIN pf_agg a USING (company_id,month)
        LEFT JOIN all_tx t USING (company_id,month)
        LEFT JOIN history h USING (company_id)
    ''')
    con.execute('''
        CREATE OR REPLACE TEMP TABLE panel_flujos AS
        SELECT *,
            sum(tiene_actividad_caja::INTEGER) OVER (
                PARTITION BY company_id ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )::BIGINT AS meses_con_actividad_hasta_m,
            CASE WHEN meses_observados IS NOT NULL THEN meses_observados<3 END AS es_calentamiento,
            CASE WHEN n_tx_caja>0 THEN (n_tx_caja-n_sin_eur)::DOUBLE/n_tx_caja END AS cobertura_eur_pct,
            CASE WHEN n_tx_caja>0 THEN (n_tx_caja-n_ambiguos)::DOUBLE/n_tx_caja END AS cobertura_clasificacion_pct,
            CASE WHEN volumen_caja_conocido_eur>0
                 THEN (ambiguo_entradas_conocido_eur+ambiguo_salidas_conocido_eur)/volumen_caja_conocido_eur
                 END AS volumen_ambiguo_pct,
            CASE WHEN tiene_actividad_caja AND n_sin_eur=0 THEN neto_caja_conocido_eur END AS neto_caja_eur,
            CASE WHEN tiene_actividad_caja AND n_cobros_sin_eur=0 AND n_ambiguos_entrada=0
                 THEN cobros_operativos_conocido_eur END AS cobros_operativos_eur,
            CASE WHEN tiene_actividad_caja AND n_pagos_sin_eur=0 AND n_ambiguos_salida=0
                 THEN pagos_operativos_conocido_eur END AS pagos_operativos_eur,
            cobros_operativos_eur-pagos_operativos_eur AS flujo_operativo_eur,
            CASE WHEN tiene_actividad_caja AND n_operativo_sin_eur+n_ambiguos_sin_eur=0
                 THEN operativo_identificado_conocido_eur-ambiguo_salidas_conocido_eur END AS operativo_min_eur,
            CASE WHEN tiene_actividad_caja AND n_operativo_sin_eur+n_ambiguos_sin_eur=0
                 THEN operativo_identificado_conocido_eur+ambiguo_entradas_conocido_eur END AS operativo_max_eur,
            CASE WHEN n_atipicos_operativos=0 THEN operativo_min_eur
                 WHEN tiene_actividad_caja AND n_operativo_sin_eur+n_ambiguos_sin_eur=0
                 THEN operativo_sin_atipicos_conocido_eur-ambiguo_salidas_sin_atipicos_conocido_eur END AS operativo_min_sin_atipicos_eur,
            CASE WHEN n_atipicos_operativos=0 THEN operativo_max_eur
                 WHEN tiene_actividad_caja AND n_operativo_sin_eur+n_ambiguos_sin_eur=0
                 THEN operativo_sin_atipicos_conocido_eur+ambiguo_entradas_sin_atipicos_conocido_eur END AS operativo_max_sin_atipicos_eur
        FROM pf_grid
    ''')
    incoming = '+'.join(f'{name}_conocido_eur' for name in BUCKETS if name=='cobros_operativos' or name.endswith('_entradas'))
    outgoing = '+'.join(f'{name}_conocido_eur' for name in BUCKETS if name=='pagos_operativos' or name.endswith('_salidas'))
    stats = con.execute('''
        SELECT count(*) FILTER (WHERE tiene_actividad_caja),
               count(*) FILTER (WHERE flujo_operativo_eur IS NOT NULL),
               sum(n_tx_caja), sum(n_tx_fuera_perimetro), sum(n_sin_eur)
        FROM panel_flujos
    ''').fetchone()
    return publish_mart(con, 'panel_flujos', con.execute('SELECT count(*) FROM mart_tx').fetchone()[0], {
        'perimetro': {'source': 'banking', 'product_types': list(CASH_PRODUCTS), 'status': 'booked'},
        'filas_con_actividad_caja': stats[0], 'filas_operativo_completo': stats[1],
        'transacciones_caja': stats[2], 'transacciones_fuera_perimetro': stats[3], 'filas_sin_eur': stats[4],
        'incertidumbre': 'unknown, transfer y non_economic conservan signo y componen cotas, sin neteo interno supuesto',
        'supuesto_cotas': 'Cada apunte observado ambiguo puede ser operativo o no. No acotan movimientos faltantes ni actividad fuera del perimetro.',
        'limites': 'Perimetro observado, no toda la empresa. Fecha de contabilizacion como disponibilidad; no hay versiones de categorias/importaciones.',
    }, {
        'conteos': 'n_tx_total<>n_tx_caja+n_tx_fuera_perimetro',
        'conciliacion': f'abs(neto_caja_conocido_eur-(({incoming})-({outgoing})))>0.01',
        'cotas': 'operativo_min_eur>operativo_max_eur OR operativo_min_sin_atipicos_eur>operativo_max_sin_atipicos_eur',
        'fx': 'n_sin_eur>0 AND neto_caja_eur IS NOT NULL',
        'sin_actividad': 'NOT tiene_actividad_caja AND neto_caja_conocido_eur IS NOT NULL',
    })
