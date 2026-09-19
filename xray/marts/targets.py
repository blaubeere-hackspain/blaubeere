from datetime import date

import duckdb

from xray import paths
from xray.contracts import CleanResult
from xray.marts.common import company_months, deficit_state, parquet, publish_mart, require_panel
from xray.period import DATASET_END

TARGET_VERSION = 1
TARGET_HORIZON_MONTHS = 3
COLLECTION_HORIZON_DAYS = 60
MIN_DEFICIT_MONTHS = 2
BASELINE_COHORT_MONTHS = 3


def build_targets_proxy(con: duckdb.DuckDBPyConnection) -> CleanResult:
    company_months(con)
    flows = require_panel(con, 'panel_flujos')
    collections = require_panel(con, 'panel_cobro')
    observations = require_panel(con, 'observabilidad')
    joins = ' '.join(
        f'LEFT JOIN {flows} f{i} ON f{i}.company_id=g.company_id AND f{i}.month=g.month+INTERVAL {i} MONTH'
        for i in range(1, TARGET_HORIZON_MONTHS+1)
    )
    states = [deficit_state(f'f{i}.operativo_min_eur', f'f{i}.operativo_max_eur') for i in range(1,TARGET_HORIZON_MONTHS+1)]
    trimmed = [deficit_state(f'f{i}.operativo_min_sin_atipicos_eur', f'f{i}.operativo_max_sin_atipicos_eur') for i in range(1,TARGET_HORIZON_MONTHS+1)]
    count_true = lambda terms: '+'.join(f'CASE WHEN ({term}) IS TRUE THEN 1 ELSE 0 END' for term in terms)
    count_possible = lambda terms: '+'.join(f'CASE WHEN ({term}) IS FALSE THEN 0 ELSE 1 END' for term in terms)
    activity = '+'.join(f'coalesce(f{i}.tiene_actividad_caja,false)::INTEGER' for i in range(1,TARGET_HORIZON_MONTHS+1))
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE tp_window AS
        SELECT g.company_id, g.group_id, g.month, g.available_at AS origin_as_of,
               last_day(g.month+INTERVAL {TARGET_HORIZON_MONTHS} MONTH) AS available_at_deficit,
               CAST(available_at_deficit+INTERVAL {COLLECTION_HORIZON_DAYS} DAY AS DATE) AS available_at_cobro,
               (SELECT max(month) FROM (SELECT DISTINCT month FROM mart_grid) dates
                WHERE last_day(dates.month)+INTERVAL {COLLECTION_HORIZON_DAYS} DAY<=g.available_at) AS baseline_cohort_end,
               f.tiene_actividad_caja, f.es_calentamiento,
               o.objetivo_observable AS cobertura_temporal_original,
               ({activity}) AS n_meses_futuros_con_caja,
               ({count_true(states)}) AS n_deficit_min,
               ({count_possible(states)}) AS n_deficit_max,
               ({count_true(trimmed)}) AS n_deficit_sin_atipicos_min,
               ({count_possible(trimmed)}) AS n_deficit_sin_atipicos_max
        FROM mart_grid g JOIN {flows} f USING (company_id,month)
        JOIN {observations} o USING (company_id,month)
        {joins}
    ''')
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE tp_cohorts AS
        WITH baseline AS (
            SELECT w.company_id, w.month, count(p.month) AS baseline_n_meses,
                   sum(p.coh_n_emitidas) AS baseline_n_facturas,
                   sum(p.coh_emitido_eur) AS baseline_emitido_eur,
                   sum(p.coh_emitido_eur*p.coh_pct_cobrado_60d)/nullif(sum(p.coh_emitido_eur),0) AS baseline_cobrado_60d_pct
            FROM tp_window w LEFT JOIN {collections} p
              ON p.company_id=w.company_id
             AND p.month BETWEEN w.baseline_cohort_end-INTERVAL {BASELINE_COHORT_MONTHS - 1} MONTH AND w.baseline_cohort_end
             AND p.coh_60d_available_at<=w.origin_as_of
             AND p.coh_pct_cobrado_60d IS NOT NULL AND p.coh_emitido_eur>0
            GROUP BY 1,2
        ), future AS (
            SELECT w.company_id, w.month, count(p.month) AS futuro_n_meses,
                   sum(p.coh_n_emitidas) AS futuro_n_facturas,
                   sum(p.coh_emitido_eur) AS futuro_emitido_eur,
                   sum(p.coh_emitido_eur*p.coh_pct_cobrado_60d)/nullif(sum(p.coh_emitido_eur),0) AS futuro_cobrado_60d_pct
            FROM tp_window w LEFT JOIN {collections} p
              ON p.company_id=w.company_id
             AND p.month BETWEEN w.month+INTERVAL 1 MONTH AND w.month+INTERVAL {TARGET_HORIZON_MONTHS} MONTH
             AND p.coh_60d_available_at<=DATE '{DATASET_END}'
             AND p.coh_pct_cobrado_60d IS NOT NULL AND p.coh_emitido_eur>0
            GROUP BY 1,2
        )
        SELECT w.*, b.* EXCLUDE (company_id,month), f.* EXCLUDE (company_id,month)
        FROM tp_window w JOIN baseline b USING (company_id,month)
        JOIN future f USING (company_id,month)
    ''')
    con.execute(f'''
        CREATE OR REPLACE TEMP TABLE targets_proxy AS
        WITH candidates AS (
            SELECT *, CASE WHEN n_deficit_min>={MIN_DEFICIT_MONTHS} THEN true
                           WHEN n_deficit_max<{MIN_DEFICIT_MONTHS} THEN false END AS base_candidate,
                      CASE WHEN n_deficit_sin_atipicos_min>={MIN_DEFICIT_MONTHS} THEN true
                           WHEN n_deficit_sin_atipicos_max<{MIN_DEFICIT_MONTHS} THEN false END AS trimmed_candidate,
                      CASE WHEN NOT tiene_actividad_caja THEN 'sin_caja_origen'
                           WHEN coalesce(es_calentamiento,true) THEN 'calentamiento'
                           WHEN available_at_deficit>DATE '{DATASET_END}' THEN 'sin_horizonte'
                           WHEN n_meses_futuros_con_caja<{TARGET_HORIZON_MONTHS} OR NOT coalesce(cobertura_temporal_original,false)
                           THEN 'horizonte_sin_actividad_caja' END AS motivo_temporal
            FROM tp_cohorts
        )
        SELECT * EXCLUDE (base_candidate,trimmed_candidate,motivo_temporal),
               {TARGET_VERSION}::INTEGER AS target_version,
               CAST(baseline_cohort_end-INTERVAL {BASELINE_COHORT_MONTHS - 1} MONTH AS DATE) AS baseline_cohort_start,
               CASE WHEN motivo_temporal IS NULL THEN base_candidate END AS deficit_base_3m,
               CASE WHEN motivo_temporal IS NULL THEN trimmed_candidate END AS deficit_sin_atipicos_3m,
               CASE WHEN motivo_temporal IS NOT NULL THEN motivo_temporal
                    WHEN base_candidate IS NULL THEN 'incertidumbre_material'
                    WHEN base_candidate IS DISTINCT FROM trimmed_candidate THEN 'sensible_atipicos'
                    END AS motivo_deficit_no_observable,
               CASE WHEN motivo_deficit_no_observable IS NULL THEN base_candidate END AS target_deficit_3m,
               CASE WHEN available_at_cobro>DATE '{DATASET_END}' THEN 'sin_horizonte_cohorte'
                    WHEN baseline_n_meses<{BASELINE_COHORT_MONTHS} THEN 'baseline_incompleto'
                    WHEN futuro_n_meses<{TARGET_HORIZON_MONTHS} THEN 'cohortes_futuras_incompletas'
                    END AS motivo_cobro_no_observable,
               CASE WHEN motivo_cobro_no_observable IS NULL
                    THEN 100.0*(baseline_cobrado_60d_pct-futuro_cobrado_60d_pct)
                    END AS deterioro_cobro_60d_pp
        FROM candidates
    ''')
    stats = con.execute('''
        SELECT count(*) FILTER (WHERE target_deficit_3m IS NOT NULL),
               count(*) FILTER (WHERE target_deficit_3m IS TRUE),
               count(*) FILTER (WHERE deterioro_cobro_60d_pp IS NOT NULL)
        FROM targets_proxy
    ''').fetchone()
    reasons = {
        name: dict(con.execute(f'''SELECT motivo_{name}_no_observable,count(*)
            FROM targets_proxy WHERE motivo_{name}_no_observable IS NOT NULL GROUP BY 1 ORDER BY 1''').fetchall())
        for name in ('deficit','cobro')
    }
    return publish_mart(con, 'targets_proxy', con.execute('SELECT count(*) FROM tp_window').fetchone()[0], {
        'target_version': TARGET_VERSION, 'horizonte_meses': TARGET_HORIZON_MONTHS,
        'deficit': 'Al menos 2 de los 3 meses futuros: neto operativo negativo redondeado a centimos, robusto a la asignacion de ambiguos y con/sin atipicos.',
        'cobro': '100*(conversion60d_base-conversion60d_futura), ponderada por EUR emitido; positivo indica menor conversion, no incumplimiento contractual.',
        'baseline_cobro': 'Tres cohortes consecutivas terminando en la ultima que habia madurado a 60 dias en el origen.',
        'futuro_cobro': 'Cohortes de emision m+1..m+3, con maduracion adicional de 60 dias; sin umbral binario inventado.',
        'filas_deficit_observable': stats[0], 'filas_deficit_positivo': stats[1],
        'filas_cobro_observable': stats[2], 'motivos_no_observable': reasons,
        'uso': 'Solo etiquetas proxy, nunca predictores. Consultar targets_available_at para respetar maduracion al entrenar.',
        'limites': 'No hay etiquetas oficiales ni versiones historicas de ERP; mantiene los supuestos declarados de las fuentes.',
    }, {
        'maduracion_deficit': f"target_deficit_3m IS NOT NULL AND available_at_deficit>DATE '{DATASET_END}'",
        'maduracion_cobro': f"deterioro_cobro_60d_pp IS NOT NULL AND available_at_cobro>DATE '{DATASET_END}'",
        'motivos_deficit': '(target_deficit_3m IS NULL)<>(motivo_deficit_no_observable IS NOT NULL)',
        'motivos_cobro': '(deterioro_cobro_60d_pp IS NULL)<>(motivo_cobro_no_observable IS NOT NULL)',
        'cotas_deficit': 'n_deficit_min>n_deficit_max OR n_deficit_max>3 OR n_deficit_min<0',
        'rango_cobro': 'abs(deterioro_cobro_60d_pp)>100.00000001',
    })


def targets_available_at(con, as_of):
    cutoff = date.fromisoformat(str(as_of))
    source = parquet(paths.MARTS_DIR / 'targets_proxy.parquet')
    fields = {'target_deficit_3m': 'available_at_deficit', 'deterioro_cobro_60d_pp': 'available_at_cobro'}
    selects = [f"SELECT company_id,group_id,month,'{name}' AS target_name,{name}::DOUBLE AS value,"
               f"{available} AS available_at FROM {source} WHERE {name} IS NOT NULL AND {available}<=?"
               for name, available in fields.items()]
    return con.sql(' UNION ALL '.join(selects) + ' ORDER BY company_id,month,target_name', params=[cutoff]*len(fields))
