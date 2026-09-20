import math
from datetime import date
from typing import TypedDict

from scripts import model_features as mf
from scripts import model_inference as mi
from scripts import model_training_io as mio
from scripts import trajectory_contract as tc


LATEST_SHA256 = 'ec6b3d3f813238412420a0627dabf56e1ee7ef38111f92aadb2415d98a770cec'
TOLERANCE = 1e-12


class ProbabilityPoint(TypedDict):
    p_proxy: float | None
    eligible: bool | None
    horizon_start: str
    horizon_end: str
    unavailable_reasons: list[str]
    coverage: dict
    missing_feature_reasons: dict[str, str]


def assert_same(actual, expected, path='latest'):
    if isinstance(expected, dict):
        tc.require(isinstance(actual, dict) and actual.keys() == expected.keys(), f'Keys differ: {path}')
        for key in expected:
            assert_same(actual[key], expected[key], f'{path}.{key}')
    elif isinstance(expected, list):
        tc.require(isinstance(actual, list) and len(actual) == len(expected), f'Length differs: {path}')
        for i, value in enumerate(expected):
            assert_same(actual[i], value, f'{path}[{i}]')
    elif type(expected) in (float, int):
        tc.require(type(actual) in (float, int) and math.isfinite(actual) and math.isfinite(expected)
                   and abs(actual - expected) <= TOLERANCE, f'Numeric mismatch: {path}')
    else:
        tc.require(type(actual) is type(expected) and actual == expected, f'Value differs: {path}')


def horizon(month):
    return (mf.shift_month(month, 1).isoformat(), mf.last_day(mf.shift_month(month, 3)).isoformat())


def probability_point(month, record=None) -> ProbabilityPoint:
    start, end = horizon(month)
    return {
        'p_proxy': None if record is None else record['p_proxy'],
        'eligible': None if record is None else record['eligible'],
        'horizon_start': start, 'horizon_end': end,
        'unavailable_reasons': ['before_training_cutoff'] if record is None else record['unavailable_reasons'],
        'coverage': {} if record is None else record['coverage'],
        'missing_feature_reasons': {} if record is None else record['missing_reason'],
    }


def fallback(company, group, month, reason):
    return {'company_id': company, 'group_id': group, 'month': month,
            'known_as_of': mf.last_day(month), 'eligible': False,
            'coverage': {'eligibility_reasons': [reason], 'valid_months_3m': 0, 'valid_months_6m': 0},
            'features': dict.fromkeys(mf.FEATURE_NAMES),
            'missing_reason': dict.fromkeys(mf.FEATURE_NAMES, reason)}


def monthly_records(panel, groups, month, model, model_version, accepted):
    as_of = mf.last_day(month)
    available = [row for row in panel if row['available_at'] is not None]
    features = mf.build_features(available, [(company, month) for company in sorted(groups)], cutoff=as_of)
    covered = {row['company_id'] for row in features}
    current = {row['company_id']: row for row in panel if mf.as_date(row['month']) == month}
    for company in sorted(groups.keys() - covered):
        source = current.get(company)
        reason = ('missing_current_month' if source is None else
                  'unknown_availability' if source['available_at'] is None else 'unavailable_as_of')
        features.append(fallback(company, groups[company], month, reason))
    features.sort(key=lambda row: row['company_id'])
    return mi.prediction_records(features, model, model_version, accepted)


def build_overlay(panel, groups, protocol):
    overlay = protocol['v1_probability_overlay']
    latest_path = mio.EXPERIMENT_DIR / 'latest_predictions.json'
    tc.require(tc.digest(latest_path) == LATEST_SHA256, 'Sealed latest predictions changed')
    tc.require(tc.read_json(mio.EXPERIMENT_DIR / 'latest_predictions.json.integrity.json') ==
               {'sha256': LATEST_SHA256}, 'Latest integrity mismatch')
    latest = tc.read_json(latest_path)
    model, manifest = mio.verify_local_model(mio.EXPERIMENT_DIR, overlay['model_manifest_sha256'])
    tc.require(manifest['model_version'] == overlay['model_version'] and latest['accepted'] is True
               and latest['calibrated'] is False and latest['future_labels_consulted'] is False,
               'Invalid fixed-model overlay scope')
    tc.require(latest['input_sha256'] == protocol['source']['panel_sha256'] and
               latest['model_manifest_sha256'] == overlay['model_manifest_sha256'] and
               latest['feature_order'] == list(mf.FEATURE_NAMES), 'Overlay input/model binding mismatch')
    first = date.fromisoformat(overlay['first_permitted_as_of']).replace(day=1)
    last = date.fromisoformat(overlay['last_permitted_as_of']).replace(day=1)
    tc.require(first == date(2026, 4, 1) and last == date(2026, 8, 1), 'Overlay calendar changed')
    result, counts = {}, {}
    for offset in range(5):
        month = mf.shift_month(first, offset)
        records = monthly_records(panel, groups, month, model, manifest['model_version'], latest['accepted'])
        tc.require(len(records) == len(groups) and {r['company_id'] for r in records} == set(groups),
                   'Incomplete monthly prediction universe')
        if month == last:
            assert_same(records, latest['predictions'])
            tc.require(len(records) == latest['companies'] == 1286 and
                       sum(r['eligible'] for r in records) == latest['eligible_companies'] == 1010 and
                       sum(r['p_proxy'] is not None for r in records) == latest['companies_with_estimate'],
                       'Latest coverage mismatch')
        counts[month.isoformat()] = {'companies': len(records), 'eligible': sum(r['eligible'] for r in records),
                                   'estimates': sum(r['p_proxy'] is not None for r in records)}
        for record in records:
            result[record['company_id'], month.isoformat()] = probability_point(month, record)
    return result, {'monthly_counts': counts, 'august_matches_sealed_latest': True,
                    'absolute_numeric_tolerance': TOLERANCE, 'latest_predictions_sha256': LATEST_SHA256,
                    'fitting_performed': False, 'v1_target_queries': 0, 'event_detection_calls': 0}
