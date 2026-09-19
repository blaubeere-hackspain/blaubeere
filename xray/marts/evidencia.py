import duckdb

from xray.contracts import CleanResult
from xray.marts.common import company_months, deficit_state, publish_mart, reasons_sql, require_panel

MIN_HISTORY_MONTHS = 3
MIN_COHORT_INVOICES = 5
MAX_COHORT_AGE_DAYS = 90


def build_panel_evidencia(con: duckdb.DuckDBPyConnection) -> CleanResult:
    company_months(con)
    flows = require_panel(con, 'panel_flujos')
    debt = require_panel(con, 'panel_deuda')
    collections = require_panel(con, 'panel_cobro')
    base_sign = deficit_state('f.operativo_min_eur', 'f.operativo_max_eur')
    trimmed_sign = deficit_state('f.operativo_min_sin_atipicos_eur', 'f.operativo_max_sin_atipicos_eur')
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE pe_base AS
        SELECT f.company_id, f.group_id, f.month, f.available_at,
               f.tiene_actividad_caja, f.n_tx_caja, f.n_tx_fuera_perimetro,
               f.n_sin_eur, f.n_operativo_sin_eur, f.n_ambiguos_sin_eur,
               f.n_ambiguos, f.n_atipicos, f.n_atipicos_operativos, f.n_atipicos_desconocidos, f.n_conflictos_direccion,
               f.meses_observados, f.meses_con_actividad_hasta_m,
               f.cobertura_eur_pct, f.cobertura_clasificacion_pct, f.volumen_ambiguo_pct,
               f.flujo_operativo_eur, d.servicio_deuda_eur,
               d.n_pagos_servicio, d.n_servicio_sin_eur, d.n_posibles_pagos_no_identificados,
               d.n_financiacion_no_desglosada, d.cobertura_servicio_eur_pct,
               coalesce(c.tiene_erp,false) AS tiene_erp,
               past.month AS cohorte_cobro_month,
               past.coh_60d_available_at AS cobro_available_at,
               past.coh_pct_cobrado_60d AS cobro_pct_60d,
               past.coh_n_emitidas AS cobro_n_facturas,
               date_diff('day',past.coh_60d_available_at,f.available_at) AS cobro_antiguedad_dias,
               {base_sign} AS deficit_operativo_definido,
               {trimmed_sign} AS deficit_sin_atipicos_definido,
               CASE WHEN f.n_atipicos_operativos=0 THEN false
                    WHEN deficit_operativo_definido IS NULL OR deficit_sin_atipicos_definido IS NULL THEN NULL
                    ELSE deficit_operativo_definido<>deficit_sin_atipicos_definido END AS sensible_atipicos
        FROM {flows} f JOIN {debt} d USING (company_id,month)
        JOIN {collections} c USING (company_id,month)
        LEFT JOIN LATERAL (
            SELECT p.month, p.coh_60d_available_at, p.coh_pct_cobrado_60d, p.coh_n_emitidas
            FROM {collections} p
            WHERE p.company_id=f.company_id AND p.coh_60d_available_at<=f.available_at
              AND p.coh_pct_cobrado_60d IS NOT NULL
            ORDER BY p.coh_60d_available_at DESC LIMIT 1
        ) past ON true
    ''')
    history = f'coalesce(meses_observados<{MIN_HISTORY_MONTHS},true) OR meses_con_actividad_hasta_m<{MIN_HISTORY_MONTHS}'
    operating_reasons = reasons_sql({
        'sin_caja_observada': 'NOT tiene_actividad_caja',
        'sin_importes_eur': 'n_tx_caja>0 AND n_tx_caja=n_sin_eur',
        'historia_corta': history,
        'fx_operativo_desconocido': 'n_operativo_sin_eur+n_ambiguos_sin_eur>0',
        'clasificacion_incompleta': 'n_ambiguos>0',
        'sensibilidad_atipicos': 'n_atipicos_operativos>0 AND sensible_atipicos IS DISTINCT FROM false',
    })
    debt_reasons = reasons_sql({
        'sin_caja_observada': 'NOT tiene_actividad_caja',
        'historia_corta': history,
        'fx_servicio_desconocido': 'n_servicio_sin_eur>0',
        'posibles_pagos_no_identificados': 'n_posibles_pagos_no_identificados>0',
        'financiacion_no_desglosada': 'n_financiacion_no_desglosada>0',
    })
    collection_reasons = reasons_sql({
        'sin_erp_observado': 'NOT tiene_erp',
        'sin_cohorte_madura': 'cobro_pct_60d IS NULL',
        'cohorte_pequena': f'cobro_n_facturas<{MIN_COHORT_INVOICES}',
        'cohorte_desactualizada': f'cobro_antiguedad_dias>{MAX_COHORT_AGE_DAYS}',
    })
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE panel_evidencia AS
        SELECT *, {operating_reasons} AS motivos_operativo,
               {debt_reasons} AS motivos_servicio_deuda,
               {collection_reasons} AS motivos_cobro,
               CASE WHEN NOT tiene_actividad_caja OR n_tx_caja=n_sin_eur THEN 'insuficiente'
                    WHEN len(motivos_operativo)>0 THEN 'parcial'
                    ELSE 'publicable' END AS estado_operativo,
               CASE WHEN NOT tiene_actividad_caja OR (n_pagos_servicio>0 AND n_pagos_servicio=n_servicio_sin_eur)
                    THEN 'insuficiente'
                    WHEN len(motivos_servicio_deuda)>0 THEN 'parcial'
                    ELSE 'publicable' END AS estado_servicio_deuda,
               CASE WHEN NOT tiene_erp OR cobro_pct_60d IS NULL THEN 'insuficiente'
                    WHEN len(motivos_cobro)>0 THEN 'parcial'
                    ELSE 'publicable' END AS estado_cobro,
               list_concat(
                   CASE WHEN estado_operativo='publicable' THEN ['operativo'] ELSE [] END,
                   CASE WHEN estado_servicio_deuda='publicable' THEN ['servicio_deuda'] ELSE [] END,
                   CASE WHEN estado_cobro='publicable' THEN ['cobro'] ELSE [] END
               ) AS dimensiones_publicables
        FROM pe_base
    ''')
    states = {
        dimension: dict(con.execute(
            f'SELECT estado_{dimension},count(*) FROM panel_evidencia GROUP BY 1 ORDER BY 1'
        ).fetchall()) for dimension in ('operativo', 'servicio_deuda', 'cobro')
    }
    return publish_mart(con, 'panel_evidencia', con.execute('SELECT count(*) FROM pe_base').fetchone()[0], {
        'estados_por_dimension': states,
        'reglas': {'min_historia_meses': MIN_HISTORY_MONTHS, 'min_facturas_cohorte': MIN_COHORT_INVOICES,
                   'max_antiguedad_cohorte_dias': MAX_COHORT_AGE_DAYS},
        'significado': 'Publicable significa metrica completa en el perimetro observado; no score certificado ni cobertura de toda la empresa.',
        'disponibilidad': 'Solo mes actual/pasado y cohortes maduras; no se incorporan flags de observabilidad futura a los predictores.',
        'limites': 'Umbrales operativos de evidencia, no confianza estadistica calibrada. No hay snapshots historicos de deuda/caja ni versiones ERP.',
    }, {
        'cohorte_futura': 'cobro_available_at>available_at',
        'operativo_publicable': "estado_operativo='publicable' AND flujo_operativo_eur IS NULL",
        'servicio_publicable': "estado_servicio_deuda='publicable' AND servicio_deuda_eur IS NULL",
        'cobro_publicable': "estado_cobro='publicable' AND cobro_pct_60d IS NULL",
    })
