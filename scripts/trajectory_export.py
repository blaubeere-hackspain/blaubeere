import argparse
import copy
import hashlib
import json
import math
import os
import types
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypedDict, Union, get_args, get_origin, is_typeddict

from scripts import model_features as mf
from scripts import trajectory_backtest as tb
from scripts import trajectory_contract as tc
from scripts import trajectory_overlay as overlay
from scripts import trajectory_signals as ts


EXECUTION_SHA256 = '5a2b05bb2a83757ceaf18465c81f6db48c812a0036dcc2dd82c9566eca5f9a33'
OUTPUT = tc.ROOT / tc.DIRECTORY / 'assessment'
IMPLEMENTATION = ('scripts/trajectory_export.py', 'scripts/trajectory_overlay.py', 'tests/test_trajectory_export.py')
APPROVED_COLUMNS = tuple(sorted(ts.INPUT_COLUMNS | set(mf.SOURCE_COLUMNS)))
STATUS = 'anticipation_not_validated'
MODE = 'descriptive_review_only'
MODEL_VERSION = 'trajectory-v2+experiment-v1:hgb_leaf15'
AS_OF = '2026-08-31'
NOTICE = 'Anticipación no validada: baja detección y alta proporción de falsas alertas. Solo revisión descriptiva; alertas predictivas automáticas desactivadas.'


class Bounds(TypedDict):
    lower_eur: float | None
    upper_eur: float | None
    lower_ratio: float | None
    upper_ratio: float | None


class RatioBounds(TypedDict):
    lower_ratio: float | None
    upper_ratio: float | None


class Delta(TypedDict):
    lower: float | None
    upper: float | None


class Intervals(TypedDict):
    base: Delta
    trim: Delta


class MonthlyEvidence(TypedDict):
    input_eligible: bool
    reasons: list[str]
    gross_eur: float | None
    cash_activity: bool | None
    n_sin_eur: float | None
    base: Bounds
    trim: Bounds
    receipts_eur: float | None
    payments_eur: float | None
    unknown_in_eur: float | None
    unknown_out_eur: float | None
    explanation_reasons: list[str]


class WindowEvidence(TypedDict):
    months: list[str]
    base: RatioBounds
    trim: RatioBounds
    gross_eur: float | None
    receipts_ratio: float | None
    payments_ratio: float | None
    unknown_in_ratio: float | None
    unknown_out_ratio: float | None
    explanation_reasons: list[str]


class Components(TypedDict):
    receipts_delta_ratio: float | None
    payments_delta_ratio: float | None
    identified_net_delta_ratio: float | None
    receipt_contribution_ratio: float | None
    payment_contribution_ratio: float | None
    gross_delta_eur: float | None


class Evidence(TypedDict):
    monthly: MonthlyEvidence
    prior: WindowEvidence
    recent: WindowEvidence
    delta_intervals: Intervals
    components: Components
    explanation_reasons: list[str]
    source_refs: list[list[str]]


class FailureReason(TypedDict):
    month: str
    reasons: list[str]


class Coverage(TypedDict):
    valid_months_prior: int
    valid_months_recent: int
    valid_months_total: int
    failure_reasons: list[FailureReason]


class Signal(TypedDict):
    status: Literal['none', 'insufficient_evidence', 'watch', 'confirmed']
    sign: Literal['improvement', 'deterioration'] | None
    first_signal_at: str | None
    confirmed_alert_at: str | None
    episode_id: str | None


class ReviewAction(TypedDict):
    code: Literal['inspect_evidence', 'review_input_coverage']
    reason: str
    evidence_ref: Literal['evidence']
    executable: Literal[False]


class TrajectoryPoint(TypedDict):
    month: str
    as_of: str
    operating_state: Literal['deficit', 'non_deficit', 'insufficient_evidence']
    state_reasons: list[str]
    direction: Literal['improving', 'stable', 'deteriorating', 'insufficient_evidence']
    direction_reasons: list[str]
    monthly_input_eligible: bool
    direction_input_eligible: bool
    evidence: Evidence
    coverage: Coverage
    signal: Signal
    probability: overlay.ProbabilityPoint
    review_action: ReviewAction


class DemoCase(TypedDict):
    kind: Literal['improvement', 'deterioration', 'recovered_dip']
    company_id: str
    onset_month: str
    confirmed_at: str
    case_visibility_from: str
    pattern_months: list[str]
    proof: dict
    sealed_case: dict
    artifact_ref: str


class BacktestReference(TypedDict):
    ref: Literal['manifest.json#/dictionary/backtest_summary']
    status: Literal['anticipation_not_validated']
    usage: Literal['descriptive_review_only']
    automatic_predictive_alerts_enabled: Literal[False]


class SourceProvenance(TypedDict):
    ref: Literal['manifest.json#/dictionary/source']
    company_id: str
    group_assignment: Literal['train', 'validation', 'final_test']
    event_evaluation_included: bool


class Trajectory(TypedDict):
    schema_version: Literal[2]
    protocol_ref: Literal['manifest.json#/dictionary/protocol']
    model_ref: Literal['manifest.json#/dictionary/model']
    source_provenance: SourceProvenance
    backtest_summary: BacktestReference
    points: list[TrajectoryPoint]
    development_demo_case: DemoCase | None
    review_actions_ref: Literal['manifest.json#/dictionary/review_actions']
    cash_planning: Literal['unavailable']


class CommonCompany(TypedDict):
    kind: Literal['operating_trajectory']
    id: str
    name: str
    group: str
    currency: Literal['EUR']
    assessment_date: Literal['2026-08-31']
    model_version: Literal['trajectory-v2+experiment-v1:hgb_leaf15']
    history_mode: Literal['reconstructed']
    data_mode: Literal['synthetic']
    opening_cash_cents: None
    buffer_cents: None
    health: None
    history: list
    flows: list
    drivers: list
    coverage: list
    predictive: None
    trajectory: Trajectory


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def validate_type(value, annotation, path='company'):
    origin, args = get_origin(annotation), get_args(annotation)
    if is_typeddict(annotation):
        fields = annotation.__annotations__
        tc.require(isinstance(value, dict) and set(value) == set(fields), f'Invalid fields: {path}')
        for key, child in fields.items():
            validate_type(value[key], child, f'{path}.{key}')
    elif origin in (types.UnionType, Union):
        for child in args:
            try:
                validate_type(value, child, path)
                return
            except ValueError:
                pass
        raise ValueError(f'Invalid nullable/union: {path}')
    elif origin is Literal:
        tc.require(any(type(value) is type(item) and value == item for item in args), f'Invalid enum: {path}')
    elif origin is list or annotation is list:
        tc.require(isinstance(value, list), f'Invalid array: {path}')
        if args:
            for item in value:
                validate_type(item, args[0], path)
        else:
            tc.require(not value, f'Unknown cash arrays must be empty: {path}')
    elif origin is dict or annotation is dict:
        tc.require(isinstance(value, dict), f'Invalid mapping: {path}')
        if args:
            for key, item in value.items():
                validate_type(key, args[0], path)
                validate_type(item, args[1], path)
    elif annotation is float:
        tc.require(type(value) in (int, float) and math.isfinite(value), f'Nonfinite number: {path}')
    elif annotation is None or annotation is type(None):
        tc.require(value is None, f'Expected null: {path}')
    else:
        tc.require(type(value) is annotation, f'Invalid scalar: {path}')


def handoff_schema(annotation):
    origin, args = get_origin(annotation), get_args(annotation)
    if is_typeddict(annotation):
        fields = annotation.__annotations__
        return {'type': 'object', 'required': list(fields), 'additionalProperties': False,
                'properties': {key: handoff_schema(child) for key, child in fields.items()}}
    if origin in (types.UnionType, Union):
        return {'anyOf': [handoff_schema(child) for child in args]}
    if origin is Literal:
        return {'enum': list(args)}
    if origin is list or annotation is list:
        return {'type': 'array', 'items': handoff_schema(args[0])} if args else {'type': 'array', 'maxItems': 0}
    if origin is dict or annotation is dict:
        return {'type': 'object', **({'additionalProperties': handoff_schema(args[1])} if args else {})}
    return {'type': {str: 'string', int: 'integer', float: 'number', bool: 'boolean',
                     type(None): 'null', None: 'null'}[annotation]}


def protected_snapshot(root):
    root = Path(root).resolve()
    execution = tc.read_json(tc.inside(root, f'{tc.DIRECTORY}/{tb.MANIFEST}'))
    names = set()
    for field in ('input_sha256', 'implementation_sha256', 'protected_sha256'):
        names.update(execution[field])
    model = tc.read_json(tc.inside(root, f'{tc.MODEL}/model_manifest.json'))
    names.update(model['source_sha256'])
    for directory in (tc.MODEL, tc.V1, 'reports/modeling/assessment-v1'):
        base = tc.inside(root, directory)
        names.update(str(path.relative_to(root)) for path in base.rglob('*') if path.is_file())
    names.update(str(path.relative_to(root)) for path in tc.inside(root, tc.DIRECTORY).iterdir() if path.is_file())
    return {name: tc.digest(tc.inside(root, name)) for name in sorted(names)}


def verify_inputs(root=tc.ROOT):
    tc.require(Path(root).resolve() == tc.ROOT, 'Only the fixed local bound root is supported')
    tc.require(tc.inside(root, tc.RECEIPT).is_file(), 'Existing protocol receipt required')
    tc.require(tc.digest(tc.inside(root, tc.PROTOCOL)) == tb.PROTOCOL_SHA256, 'Protocol hash changed')
    tc.require(tc.digest(tc.inside(root, f'{tc.DIRECTORY}/{tb.MANIFEST}')) == EXECUTION_SHA256,
               'Execution manifest hash changed')
    proof = tc.check(root)
    verification = tb.verify(root)
    tc.require(verification['phases'] == {'development': 'complete', 'reserved': 'complete'} and
               verification['outcome_values_read'] == 0, 'Both consumed phases must already be complete')
    return tc.read_json(tc.inside(root, tc.PROTOCOL)), proof, verification


def load_panel(protocol, root=tc.ROOT):
    panel_path = tc.inside(root, f'data/runs/{protocol["source"]["run_id"]}/{tc.PANEL}')
    with tb.readonly_connection() as connection:
        result = connection.execute('SELECT ' + ','.join(f'"{name}"' for name in APPROVED_COLUMNS) +
                                    ' FROM read_parquet(?) ORDER BY company_id,month', [str(panel_path)])
        tc.require([item[0] for item in result.description] == list(APPROVED_COLUMNS), 'Unapproved input projection')
        rows = [dict(zip(APPROVED_COLUMNS, (float(v) if isinstance(v, Decimal) else v for v in values)))
                for values in result.fetchall()]
    return rows


def read_sealed_summaries(root=tc.ROOT):
    reports = {phase: tc.read_json(tc.inside(root, f'{tc.DIRECTORY}/{phase}-report.json')) for phase in tb.PHASES}
    cases = tc.read_json(tc.inside(root, f'{tc.DIRECTORY}/development-cases.json'))
    tc.require(set(cases) == {'improvement', 'deterioration', 'recovered_dip'}, 'Incomplete development cases')
    return reports, cases


def demo_cases(cases, assignment):
    result = {}
    for kind, case in cases.items():
        if case['status'] != 'available':
            tc.require(case['status'] == 'unavailable', 'Invalid case availability')
            continue
        proof = case['selection_proof']
        tc.require(assignment[case['group_id']] != 'final_test' and proof['rank'] == 1 and
                   proof['source_phase'] == 'development' and proof['selection_uses_alerts_or_probability'] is False
                   and case['confirmed_at'] <= '2025-12-31', 'Invalid sealed development selection')
        tc.require(case['company_id'] not in result, 'Multiple selected cases require a new explicit schema')
        result[case['company_id']] = {
            'kind': kind, 'company_id': case['company_id'], 'onset_month': case['onset_month'],
            'confirmed_at': case['confirmed_at'], 'case_visibility_from': case['confirmed_at'],
            'pattern_months': case['pattern_months'], 'proof': proof, 'sealed_case': case,
            'artifact_ref': f'{tc.DIRECTORY}/development-cases.json#/{kind}',
        }
    return result


def visible_development_case(case, as_of):
    cutoff = date.fromisoformat(as_of)
    return copy.deepcopy(case) if case is not None and date.fromisoformat(case['case_visibility_from']) <= cutoff else None


def pack_point(row, probabilities) -> TrajectoryPoint:
    evidence = row['evidence']
    month = row['month']
    if row['as_of'] < '2026-04-30':
        probability = overlay.probability_point(month)
    else:
        tc.require((row['company_id'], month) in probabilities, 'Missing permitted overlay month')
        probability = probabilities[row['company_id'], month]
    return {
        **{key: row[key] for key in ('month', 'as_of', 'operating_state', 'state_reasons', 'direction',
                                     'direction_reasons', 'monthly_input_eligible', 'direction_input_eligible', 'signal')},
        'evidence': {
            'monthly': {key: evidence['monthly'][key] for key in MonthlyEvidence.__annotations__},
            **{period: {key: evidence[period][key] for key in WindowEvidence.__annotations__}
               for period in ('prior', 'recent')},
            **{key: evidence[key] for key in ('delta_intervals', 'components', 'explanation_reasons')},
            'source_refs': [[ref['month'], ref['available_at']] for ref in evidence['source_refs']],
        },
        'coverage': {key: row['coverage'][key] for key in Coverage.__annotations__},
        'probability': probability,
        'review_action': {'code': 'review_input_coverage' if row['direction'] == ts.UNKNOWN else 'inspect_evidence',
                          'reason': ','.join(row['direction_reasons']) or row['direction'],
                          'evidence_ref': 'evidence', 'executable': False},
    }


def company_record(company, group, points, assignment, selected) -> CommonCompany:
    return {
        'kind': 'operating_trajectory', 'id': company, 'name': f'Synthetic company {company}', 'group': group,
        'currency': 'EUR', 'assessment_date': AS_OF, 'model_version': MODEL_VERSION,
        'history_mode': 'reconstructed', 'data_mode': 'synthetic', 'opening_cash_cents': None,
        'buffer_cents': None, 'health': None, 'history': [], 'flows': [], 'drivers': [], 'coverage': [], 'predictive': None,
        'trajectory': {
            'schema_version': 2, 'protocol_ref': 'manifest.json#/dictionary/protocol',
            'model_ref': 'manifest.json#/dictionary/model',
            'source_provenance': {'ref': 'manifest.json#/dictionary/source', 'company_id': company,
                                  'group_assignment': assignment[group],
                                  'event_evaluation_included': assignment[group] != 'final_test'},
            'backtest_summary': {'ref': 'manifest.json#/dictionary/backtest_summary', 'status': STATUS,
                                 'usage': MODE, 'automatic_predictive_alerts_enabled': False},
            'points': points, 'development_demo_case': selected.get(company),
            'review_actions_ref': 'manifest.json#/dictionary/review_actions', 'cash_planning': 'unavailable',
        },
    }


def validate_company(company):
    validate_type(company, CommonCompany)
    tc.require(company['id'].strip() and company['group'].strip(), 'Empty identity')
    trajectory = company['trajectory']
    tc.require(trajectory['source_provenance']['company_id'] == company['id'], 'Provenance identity mismatch')
    points = trajectory['points']
    calendar = [mf.shift_month('2024-09-01', offset).isoformat() for offset in range(24)]
    tc.require([p['month'] for p in points] == calendar, 'Every company must retain all 24 calendar months')
    for point in points:
        month, as_of = point['month'], point['as_of']
        tc.require(as_of == mf.last_day(month).isoformat(), 'Monthly as-of mismatch')
        evidence, probability = point['evidence'], point['probability']
        tc.require((probability['horizon_start'], probability['horizon_end']) == overlay.horizon(month), 'Horizon mismatch')
        p = probability['p_proxy']
        if as_of < '2026-04-30':
            tc.require(p is None and probability['eligible'] is None and
                       probability['unavailable_reasons'] == ['before_training_cutoff'], 'Probability before cutoff')
        else:
            tc.require(type(probability['eligible']) is bool, 'Missing probability eligibility')
            tc.require((p is not None) == probability['eligible'], 'Probability eligibility mismatch')
            tc.require(bool(probability['unavailable_reasons']) == (p is None), 'Abstention reason missing')
        tc.require(p is None or 0 <= p <= 1, 'Probability out of range')
        for period, offsets in (('prior', range(-5, -2)), ('recent', range(-2, 1))):
            tc.require(evidence[period]['months'] == [mf.shift_month(month, i).isoformat() for i in offsets],
                       'Noncontiguous evidence window')
        for source_month, available_at in evidence['source_refs']:
            tc.require(date.fromisoformat(source_month).day == 1 and
                       evidence['prior']['months'][0] <= source_month <= month and
                       date.fromisoformat(available_at) <= date.fromisoformat(as_of), 'Future evidence reference')
        coverage = point['coverage']
        tc.require(0 <= coverage['valid_months_prior'] <= 3 and 0 <= coverage['valid_months_recent'] <= 3 and
                   coverage['valid_months_total'] == coverage['valid_months_prior'] + coverage['valid_months_recent'] and
                   point['direction_input_eligible'] == (coverage['valid_months_total'] == 6), 'Coverage mismatch')
        for bound in ('base', 'trim'):
            values = evidence['monthly'][bound]
            if point['monthly_input_eligible']:
                tc.require(values['lower_eur'] <= values['upper_eur'], 'Invalid eligible monthly bounds')
        signal = point['signal']
        for key in ('first_signal_at', 'confirmed_alert_at'):
            if signal[key] is not None:
                tc.require(date.fromisoformat(signal[key]) <= date.fromisoformat(as_of), 'Future signal date')
        if signal['confirmed_alert_at'] is not None:
            tc.require(signal['first_signal_at'] < signal['confirmed_alert_at'], 'Backdated signal confirmation')
    case = trajectory['development_demo_case']
    if case is not None:
        tc.require(trajectory['source_provenance']['event_evaluation_included'] and case['company_id'] == company['id']
                   and case['case_visibility_from'] == case['confirmed_at'], 'Invalid case visibility or excluded-group label')
    encoded(company)


def dictionary(protocol, reports, protected):
    return {
        'protocol': {'artifact': tc.PROTOCOL, 'sha256': tb.PROTOCOL_SHA256, 'version': 'trajectory-v2',
                     'monthly_state': protocol['monthly_state'], 'direction': protocol['direction'],
                     'alerts': protocol['alerts'], 'required_months': {'prior': 3, 'recent': 3, 'total': 6}},
        'source': {**protocol['source'], 'artifact': f'data/runs/{protocol["source"]["run_id"]}/{tc.PANEL}',
                   'projected_columns': list(APPROVED_COLUMNS),
                   'source_refs_encoding': '[month, available_at]; company_id from enclosing company; artifact and panel hash from this dictionary',
                   'currency_basis': 'EUR analítico verificado; no moneda nativa ni saldo de caja',
                   'cash_history_semantics': 'Desconocido; arrays vacíos no significan ausencia de actividad'},
        'model': {**protocol['v1_probability_overlay'], 'target': 'target_deficit_3m', 'horizon_months': 3,
                  'definition': 'Déficit operativo en al menos dos de los tres meses naturales siguientes; no transición ni default.',
                  'calibrated': False, 'retrospective': True, 'accepted_v1_proxy': True,
                  'latest_predictions_artifact': f'{tc.MODEL}/latest_predictions.json',
                  'latest_predictions_sha256': overlay.LATEST_SHA256,
                  'probability_vs_direction': 'Cambiar p_proxy no define mejora o deterioro. La dirección compara evidencia de flujos, no probabilidades.',
                  'exported_feature_policy': 'Se comparan todos los features, missing_reason y metadatos de agosto con latest sellado; no se duplican vectores de features en cada punto.'},
        'backtest_summary': {'status': STATUS, 'usage': MODE, 'notice': NOTICE,
                             'automatic_predictive_alerts_enabled': False,
                             'interval_scope': 'IC95 retrospectivo por grupos de regla fija; no IC individual de probabilidad; sin refit.',
                             'known_company_condition': protocol['evaluation']['independence'],
                             'lead_scope': protocol['evaluation']['lead_definition'],
                             'censoring': 'censored_unknown no es negativo ni cero; FAS=false/(matches+false), no FPR.',
                             'phases': reports,
                             'artifact_refs': {phase: {'artifact': f'{tc.DIRECTORY}/{phase}-report.json',
                                                      'sha256': protected[f'{tc.DIRECTORY}/{phase}-report.json']}
                                               for phase in tb.PHASES}},
        'review_actions': {'executable': False, 'persistence': False,
                           'inspect_evidence': ['Inspeccionar límites y clasificación', 'Revisar cobros y pagos conocidos',
                                                'Comparar periodos y denominadores'],
                           'review_input_coverage': ['Revisar meses ausentes y disponibilidad', 'Revisar FX y clasificación ambigua'],
                           'forbidden': ['simular pagos', 'ejecutar pagos', 'conceder crédito', 'asignación persistente']},
        'historical_visibility': {'points': 'API T5 debe filtrar points por as_of <= fecha solicitada.',
                                  'development_demo_case': 'Ocultar todo el objeto, incluido tipo, selección y recuperación, hasta case_visibility_from; usar visible_development_case.',
                                  'backtest_summary': 'Evaluación retrospectiva global, nunca presentarla como conocida en la fecha histórica.',
                                  'selection': 'Selector de demos explícitamente retrospectivo; no lista de eventos conocidos antes de confirmación.'},
    }


def percent(value):
    return 'no definido' if value is None else f'{100 * value:.3f}%'


def ci_text(metric):
    values = metric['ci95']
    return f'[{percent(values[0])}, {percent(values[1])}]' if values is not None else f'no definido ({metric["reason"]})'


def report_text(reports, cases, counts, overlay_proof):
    lines = ['# Trayectoria operativa v2 — evaluación y exportación', '',
             f'**{STATUS} / {MODE}**', '', NOTICE, '',
             '## Resultado real sellado', '',
             'No se han vuelto a ejecutar desarrollo ni reserva, ni detectado eventos en el exportador. '
             'Se reutilizan los dos resultados sellados y sus intervalos, sin ajustar reglas ni seleccionar ejemplos por éxito. '
             'Menos alertas no demuestra superioridad. Los tres matches de reserva del candidato no validan anticipación utilizable.', '',
             '| Fase | Signo | Método | Eventos | Alertas | Matches | Misses | Falsas | Censuradas | Recall | IC95 recall | FAS | IC95 FAS | Lead 1/2 meses | Mediana lead |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|']
    for phase, report in reports.items():
        intervals = {(s['sign'], s['method']): s for s in report['bootstrap']['strata']}
        for row in report['strata']:
            ci = intervals[row['sign'], row['method']]
            lead = row['lead_median']
            median = str(lead['value']) if lead['value'] is not None else f'no definido ({lead["reason"]})'
            values = [phase, row['sign'], row['method'], *[str(row[key]) for key in
                      ('events', 'alerts', 'matches', 'misses', 'false_alarms', 'censored_unknown')],
                      percent(row['recall']['value']), ci_text(ci['recall']), percent(row['false_alert_share']['value']),
                      ci_text(ci['false_alert_share']), f'{row["lead_histogram"]["1"]}/{row["lead_histogram"]["2"]}', median]
            lines.append('| ' + ' | '.join(values) + ' |')
    lines += ['', 'Recall = matches/eventos; FAS = falsas/(matches + falsas), no tasa de falsos positivos. '
              'Censuradas permanecen desconocidas: no se convierten en negativas. Lead y su mediana describen exclusivamente '
              'matches, no todas las filas ni todas las señales. IC95 percentil con 1.000 remuestreos de los 200 grupos '
              '(semilla 1729), preservando empresas y cronología y sin volver a ajustar; no es incertidumbre individual de p_proxy.', '',
              '## Cobertura y límites metodológicos', '',
              'Reserva temporal interna en empresas conocidas, no holdout independiente de nuevas empresas ni validación externa. '
              '200 grupos / 1.078 empresas incluidos en evaluación; los 50 grupos final_test v1 / 208 empresas se excluyeron '
              'de eventos, matching y selección en todas las fechas. El exportador muestra sus flujos observados y probabilidades '
              'v1, pero no crea etiquetas de evento. Datos sintéticos reconstruidos; available_at no certifica known_on histórico. '
              'Clasificación, FX, actividad y ventanas incompletas limitan lo observado. No hay saldo real, planificación de caja, '
              'score de salud 0–100 ni explicación causal.', '']
    for phase, report in reports.items():
        lines += [f'### {phase}', '', 'Periodo sellado:', '```json', json.dumps(report['period'], ensure_ascii=False, sort_keys=True),
                  '```', 'Cobertura agregada (sin eventos individuales de reserva):', '```json',
                  json.dumps(report['coverage'], ensure_ascii=False, sort_keys=True, indent=2), '```', '']
    lines += ['## Tres casos de desarrollo, sin sustituciones', '',
              'Se copia cada primer caso ordenado del JSON sellado completo, no se recalcula su selección. '
              'La confirmación es la fecha mínima de visibilidad de toda la anotación; nunca se anticipa retrospectivamente '
              'una recuperación. Las señales de vigilancia y su confirmación son hechos diferentes del evento. '
              'Estas fechas de desarrollo no tienen modelo v1 disponible: p_proxy=null, before_training_cutoff.', '']
    for kind, case in sorted(cases.items()):
        if case['status'] != 'available':
            lines += [f'- {kind}: no disponible.', '']
            continue
        lines += [f'### {kind}: {case["company_id"]}', '',
                  f'Inicio observado: {case["onset_month"]}; confirmado y visible desde: {case["confirmed_at"]}; grupo: {case["group_id"]}.',
                  f'Prueba de selección: {json.dumps(case["selection_proof"], ensure_ascii=False, sort_keys=True)}',
                  f'Matches sellados (si procede): {json.dumps(case.get("matches_by_method"), ensure_ascii=False, sort_keys=True)}', '',
                  '| Mes | Estado | Dirección | Watch | Confirmación señal |', '|---|---|---|---|---|']
        for point in case['timeline']:
            signal = point['signal']
            lines.append(f'| {point["month"]} | {point["operating_state"]} | {point["direction"]} | '
                         f'{signal["first_signal_at"] or "—"} | {signal["confirmed_alert_at"] or "—"} |')
        lines += ['']
    lines += ['COMP_0009 conserva su deterioro observado sin match candidato ni baseline: no se cambia por un caso más favorable.', '',
              '## Capa de probabilidad v1 separada', '',
              'Se reutiliza exclusivamente experiment-v1:hgb_leaf15, sin fit, calibración ni evaluación de targets. '
              'Estima déficit operativo en al menos dos de los tres meses naturales siguientes, no transición. '
              'El resultado AP v1 existente corresponde a otro target y no mide anticipación de estas transiciones; '
              'este resultado negativo no lo contradice. La dirección no se deduce de cambios de probabilidad. '
              'El entrenamiento/selección cierra el 31-03-2026; solo abril–agosto 2026 tienen overlay, con elegibilidad original '
              'y horizonte m+1..m+3. El modelo se entrenó físicamente en septiembre: esto es simulación retrospectiva, '
              'no emisión histórica en vivo. Agosto coincide con las 1.286 predicciones selladas en todos sus campos '
              '(tolerancia absoluta numérica fija 1e-12); 1.010 estimaciones y 276 abstenciones.', '',
              '```json', json.dumps(overlay_proof, sort_keys=True, indent=2), '```', '',
              '## Contrato de entrega y revisión', '',
              f'{counts["companies"]} empresas × 24 meses = {counts["points"]} puntos, septiembre 2024–agosto 2026. '
              'companies.json es un array CommonCompany de kind operating_trajectory; manifest.json incluye el JSON Schema '
              'y el diccionario compartido de protocolo, fuentes, modelo, revisión y backtest agregado. '
              'predictive, health, opening_cash_cents y buffer_cents son null; history/flows/drivers/coverage son arrays vacíos '
              'porque la caja es desconocida. evidence.source_refs son pares [month, available_at] con empresa del objeto contenedor. '
              'La evidencia numérica conserva límites, medias aritméticas no ponderadas de ratios, periodos, deltas y contribuciones '
              'de cobros/pagos conocidos: no SHAP ni causalidad. Acciones solo para inspeccionar evidencia, clasificación, FX, '
              'cobros/pagos y comparar periodos; sin ejecución, pagos, crédito ni persistencia.', '',
              'La integración Rust/UI sigue pendiente en T5. Debe conservar v1 proxy_only, filtrar puntos por fecha y ocultar '
              'todo development_demo_case antes de case_visibility_from (helper visible_development_case). '
              'El selector de demos y el backtest global son retrospectivos y deben rotularse como tales; no son conocimiento '
              'de una fecha pasada. No activar alertas automáticas ni simulación de caja.', '',
              '## Reproducción segura', '', '```sh',
              '.venv/bin/python -B -m scripts.trajectory_backtest verify',
              '.venv/bin/python -B -m scripts.trajectory_export',
              '.venv/bin/python -B -m scripts.trajectory_export --check', '```', '',
              'Exportación exclusiva e idempotente: diferencias bloquean, nunca sobrescriben; --check no escribe. '
              'Se verifican hashes protegidos antes y después; solo se reproducen señales descriptivas e inferencia, no outcomes. '
              'No ejecutar de nuevo develop ni evaluate-reserved.', '']
    return '\n'.join(lines).encode()


def publish(files, output, check=False):
    output = Path(output)
    for name, content in files.items():
        tc.require(Path(name).name == name and not (output / name).is_symlink(), 'Unsafe output filename')
        path = output / name
        if path.exists():
            tc.require(path.read_bytes() == content, f'Immutable output differs: {name}')
        else:
            tc.require(not check, f'Missing output: {name}')
    if check:
        return
    output.mkdir(exist_ok=True)
    for name, content in files.items():
        path = output / name
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    tb._fsync_directory(output)


def export(check=False):
    protocol, proof, verification = verify_inputs()
    before = protected_snapshot(tc.ROOT)
    reports, cases = read_sealed_summaries()
    assignment = tc.read_json(tc.inside(tc.ROOT, tc.GROUPS))
    selected = demo_cases(cases, assignment)
    panel = load_panel(protocol)
    signal_panel = [{key: row[key] for key in ts.INPUT_COLUMNS} for row in panel]
    rows = ts.build_trajectory(signal_panel, protocol)
    groups = {row['company_id']: row['group_id'] for row in rows}
    tc.require(len(groups) == 1286 and len(rows) == 1286 * 24 and all(g in assignment for g in groups.values()),
               'Full company calendar mismatch')
    probabilities, overlay_proof = overlay.build_overlay(panel, groups, protocol)
    points = {company: [] for company in groups}
    states, directions = Counter(), Counter()
    for row in rows:
        points[row['company_id']].append(pack_point(row, probabilities))
        states[row['operating_state']] += 1
        directions[row['direction']] += 1
    del rows, panel, signal_panel
    companies = [company_record(company, group, points[company], assignment, selected)
                 for company, group in sorted(groups.items())]
    for company in companies:
        validate_company(company)
    tc.require({c['id'] for c in companies if c['trajectory']['development_demo_case']} == set(selected), 'Lost case binding')
    excluded = [c for c in companies if assignment[c['group']] == 'final_test']
    tc.require(len(excluded) == 208 and len({c['group'] for c in excluded}) == 50, 'Excluded display universe mismatch')
    payload = encoded(companies)
    tc.require(len(payload) < 100 * 1024 * 1024, 'Export exceeds 100 MiB compact-file limit')
    counts = {'companies': len(companies), 'points': len(companies) * 24, 'months_per_company': 24,
              'evaluation_included_companies': len(companies) - len(excluded), 'evaluation_excluded_companies': len(excluded),
              'evaluation_included_groups': 200, 'evaluation_excluded_groups': 50,
              'operating_states': dict(sorted(states.items())), 'directions': dict(sorted(directions.items()))}
    report = report_text(reports, cases, counts, overlay_proof)
    tc.require(protected_snapshot(tc.ROOT) == before, 'Protected hashes changed during export')
    tc.require(tb.verify() == verification, 'Execution verification changed during export')
    manifest = {
        'schema_version': 2, 'kind': 'operating_trajectory', 'as_of': AS_OF, 'model_version': MODEL_VERSION,
        'status': STATUS, 'usage': MODE, 'automatic_predictive_alerts_enabled': False,
        'notice': NOTICE, 'counts': counts, 'companies_sha256': digest_bytes(payload), 'companies_bytes': len(payload),
        'report_sha256': digest_bytes(report), 'protocol_sha256': proof['protocol_sha256'],
        'execution_manifest_sha256': EXECUTION_SHA256,
        'implementation_sha256': {name: tc.digest(tc.ROOT / name) for name in IMPLEMENTATION},
        'protected_sha256_before': before, 'protected_sha256_after': before, 'protected_hashes_unchanged': True,
        'verification': verification, 'overlay_verification': overlay_proof,
        'dictionary': dictionary(protocol, reports, before),
        'company_schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema', **handoff_schema(CommonCompany)},
        'reproduction': '.venv/bin/python -B -m scripts.trajectory_export --check',
    }
    tc.require(not OUTPUT.is_symlink() and OUTPUT.resolve() == tc.ROOT / tc.DIRECTORY / 'assessment', 'Unsafe output directory')
    files = {'companies.json': payload, 'report.md': report, 'manifest.json': encoded(manifest)}
    publish(files, OUTPUT, check)
    tc.require(protected_snapshot(tc.ROOT) == before, 'Protected hashes changed while publishing')
    return {'status': 'checked' if check else 'exported', 'assessment_status': STATUS, 'usage': MODE,
            'counts': counts, 'outputs': {name: {'sha256': digest_bytes(content), 'bytes': len(content)} for name, content in files.items()},
            'cases': {kind: {key: case.get(key) for key in ('company_id', 'onset_month', 'confirmed_at', 'matches_by_method')}
                      for kind, case in cases.items()}, 'overlay': overlay_proof,
            'schema_example': {'kind': companies[0]['kind'], 'trajectory_keys': list(companies[0]['trajectory']),
                               'point_keys': list(points[companies[0]['id']][0]), 'source_refs': '[month,available_at]'},
            'protected_files_verified': len(before), 'protected_hashes_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description='Exclusive v2 descriptive export; no fitting, event detection or backtest phase execution')
    parser.add_argument('--check', action='store_true', help='Replay export/inference only and compare exact bytes; never write')
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.check), sort_keys=True, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Trajectory export blocked: {error}\n')


if __name__ == '__main__':
    main()
