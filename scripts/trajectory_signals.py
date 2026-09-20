import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

from scripts.model_features import last_day, shift_month
from scripts.trajectory_contract import require, validate_rules


GROSS = 'volumen_caja_conocido_eur'
BOUNDS = {
    'base': ('operativo_min_eur', 'operativo_max_eur'),
    'trim': ('operativo_min_sin_atipicos_eur', 'operativo_max_sin_atipicos_eur'),
}
FLOWS = {
    'receipts': 'cobros_operativos_conocido_eur',
    'payments': 'pagos_operativos_conocido_eur',
    'unknown_in': 'ambiguo_entradas_conocido_eur',
    'unknown_out': 'ambiguo_salidas_conocido_eur',
}
INPUT_COLUMNS = frozenset({
    'company_id', 'group_id', 'month', 'available_at', 'tiene_actividad_caja', 'n_sin_eur', GROSS,
    *[column for bounds in BOUNDS.values() for column in bounds], *FLOWS.values(),
})
SIGNS = {'improving': 'improvement', 'deteriorating': 'deterioration'}
UNKNOWN = 'insufficient_evidence'


def _date(value):
    require(type(value) in (str, date), 'Expected ISO date or date')
    return date.fromisoformat(value) if isinstance(value, str) else value


def _identity(value):
    require(isinstance(value, str) and bool(value.strip()), 'Invalid company/group identity')
    return value


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _mean(values):
    if any(value is None for value in values):
        return None
    total = 0.0
    for value in values:
        total += value
    return _number(total / 3.0)


def _difference(recent, prior):
    return None if recent is None or prior is None else _number(recent - prior)


def _id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _index(rows, company_groups, first, last):
    require(company_groups is None or isinstance(company_groups, Mapping), 'company_groups must be a mapping')
    groups = {} if company_groups is None else dict(company_groups)
    for company, group in groups.items():
        _identity(company)
        _identity(group)
    indexed = {}
    for row in rows:
        require(isinstance(row, Mapping), 'Expected a monthly row mapping')
        require(set(row) <= INPUT_COLUMNS, 'Unexpected input keys; project only trajectory inputs')
        require({'company_id', 'month'} <= set(row), 'Missing company/month identity')
        company = _identity(row['company_id'])
        group = _identity(row.get('group_id', None if company_groups is None else groups.get(company)))
        if company_groups is not None:
            require(company in groups, 'Company outside supplied universe')
        require(company not in groups or groups[company] == group, 'Unstable company/group identity')
        groups[company] = group
        month = _date(row['month'])
        require(month.day == 1 and first <= month <= last, 'Invalid month grain or calendar range')
        key = company, month
        require(key not in indexed, 'Duplicate company/month')
        available = row.get('available_at')
        indexed[key] = dict(row, available_at=None if available is None else _date(available))
    return indexed, groups


def _monthly(source, month, as_of, first):
    item = {
        'month': month.isoformat(), 'input_eligible': False, 'reasons': [],
        'gross_eur': None, 'cash_activity': None, 'n_sin_eur': None,
        'source_ref': None, 'explanation_reasons': [],
        **{variant: dict.fromkeys(('lower_eur', 'upper_eur', 'lower_ratio', 'upper_ratio')) for variant in BOUNDS},
        **{f'{name}_{suffix}': None for name in FLOWS for suffix in ('eur', 'ratio')},
    }
    problem = None
    if month < first:
        problem = 'outside_calendar'
    elif source is None:
        problem = 'missing_month'
    elif source['available_at'] is None:
        problem = 'unknown_availability'
    elif source['available_at'] > as_of:
        problem = 'unavailable_as_of'
    if problem:
        item['reasons'] = item['explanation_reasons'] = [problem]
        return item
    item['source_ref'] = {'company_id': source['company_id'], 'month': month.isoformat(),
                          'available_at': source['available_at'].isoformat()}
    activity = source.get('tiene_actividad_caja')
    item['cash_activity'] = activity if type(activity) is bool else None
    item['n_sin_eur'] = _number(source.get('n_sin_eur'))
    gross = item['gross_eur'] = _number(source.get(GROSS))
    reasons = item['reasons']
    if activity is not True:
        reasons.append('no_cash_activity')
    if item['n_sin_eur'] != 0:
        reasons.append('unknown_fx')
    if gross is None or gross <= 0:
        reasons.append('nonpositive_or_unknown_gross')
    for variant, columns in BOUNDS.items():
        lower, upper = (_number(source.get(column)) for column in columns)
        item[variant].update(lower_eur=lower, upper_eur=upper)
        if lower is None or upper is None:
            reasons.append(f'invalid_{variant}_bounds')
        elif lower > upper:
            reasons.append(f'unordered_{variant}_bounds')
    item['input_eligible'] = not reasons
    if item['input_eligible']:
        for variant in BOUNDS:
            for bound in ('lower', 'upper'):
                item[variant][f'{bound}_ratio'] = _number(item[variant][f'{bound}_eur'] / gross)
    for name, column in FLOWS.items():
        value = _number(source.get(column))
        value = value if value is not None and value >= 0 else None
        item[f'{name}_eur'] = value
        if value is None:
            item['explanation_reasons'].append(f'unknown_{name}')
        elif item['input_eligible']:
            item[f'{name}_ratio'] = _number(value / gross)
            if item[f'{name}_ratio'] is None:
                item['explanation_reasons'].append('nonfinite_explanation_arithmetic')
    item['reasons'] = sorted(reasons)
    return item


def _state(item):
    if not item['input_eligible']:
        return UNKNOWN, list(item['reasons'])

    def cents(value):
        value = Decimal(str(value))
        with localcontext() as context:
            context.prec = max(28, value.adjusted() + 4)
            return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    states = []
    for variant in BOUNDS:
        bounds = item[variant]
        states.append('deficit' if cents(bounds['upper_eur']) < 0 else
                      'non_deficit' if cents(bounds['lower_eur']) >= 0 else UNKNOWN)
    if states[0] == states[1] != UNKNOWN:
        return states[0], []
    reasons = []
    if UNKNOWN in states:
        reasons.append('ambiguous_state')
    if states[0] != states[1]:
        reasons.append('base_trim_state_disagreement')
    return UNKNOWN, reasons


def _window(inputs):
    eligible = all(item['input_eligible'] for item in inputs)

    def mean(key, variant=None):
        return _mean([(item[variant] if variant else item)[key] for item in inputs]) if eligible else None

    result = {
        'months': [item['month'] for item in inputs], 'inputs': inputs,
        **{variant: {f'{bound}_ratio': mean(f'{bound}_ratio', variant) for bound in ('lower', 'upper')}
           for variant in BOUNDS},
        'gross_eur': mean('gross_eur'),
        **{f'{name}_ratio': mean(f'{name}_ratio') for name in FLOWS},
    }
    reasons = {reason for item in inputs for reason in item['explanation_reasons'] + item['reasons']}
    for name in ('receipts', 'payments'):
        if result[f'{name}_ratio'] is None:
            reasons.add(f'unknown_{name}')
    if eligible and result['gross_eur'] is None:
        reasons.add('nonfinite_explanation_arithmetic')
    result['explanation_reasons'] = sorted(reasons)
    return result


def _direction(prior, recent, threshold):
    inputs = prior['inputs'] + recent['inputs']
    eligible = all(item['input_eligible'] for item in inputs)
    deltas = {variant: {
        'lower': _difference(recent[variant]['lower_ratio'], prior[variant]['upper_ratio']),
        'upper': _difference(recent[variant]['upper_ratio'], prior[variant]['lower_ratio']),
    } for variant in BOUNDS}
    if not eligible:
        return UNKNOWN, sorted({reason for item in inputs for reason in item['reasons']}), deltas
    if any(value is None for interval in deltas.values() for value in interval.values()):
        return UNKNOWN, ['nonfinite_direction_arithmetic'], deltas
    if all(interval['lower'] >= threshold for interval in deltas.values()):
        return 'improving', [], deltas
    if all(interval['upper'] <= -threshold for interval in deltas.values()):
        return 'deteriorating', [], deltas
    if all(interval['lower'] > -threshold and interval['upper'] < threshold for interval in deltas.values()):
        return 'stable', [], deltas
    return UNKNOWN, ['ambiguous_direction'], deltas


def _signal(company, as_of, direction, previous, version):
    sign = SIGNS.get(direction)
    result = {'status': 'none' if direction == 'stable' else UNKNOWN,
              'sign': None, 'first_signal_at': None, 'confirmed_alert_at': None, 'episode_id': None}
    if sign is None:
        return result
    if previous and previous['sign'] == sign:
        return dict(previous, status='confirmed', confirmed_alert_at=previous['confirmed_alert_at'] or as_of)
    return dict(result, status='watch', sign=sign, first_signal_at=as_of,
                episode_id=_id(version, company, sign, as_of, 'episode'))


def build_trajectory(rows, protocol, cutoff=None, company_groups=None):
    """Build as-of rows, without I/O, events or probabilities, from strict monthly mappings.

    INPUT_COLUMNS is the permitted projection; financial fields may be absent/null.
    company_groups, when supplied, is the authoritative company -> group universe.
    Otherwise identities are inferred from rows. cutoff includes only closed months.
    Late inputs may contribute to later windows, never to earlier monthly snapshots.
    """
    validate_rules(protocol)
    require(protocol['monthly_state']['gross'] == GROSS and
            protocol['monthly_state']['base_bounds'] == list(BOUNDS['base']) and
            protocol['monthly_state']['trim_bounds'] == list(BOUNDS['trim']) and
            protocol['alerts']['sign_map'] == SIGNS, 'Unsupported signal rules')
    calendar = protocol['source']['calendar']
    first, last = _date(calendar['first_month']), _date(calendar['last_month'])
    cutoff = last_day(last) if cutoff is None else _date(cutoff)
    indexed, groups = _index(rows, company_groups, first, last)
    months = [shift_month(first, offset) for offset in range(calendar['months_per_company'])]
    version = protocol['protocol_version']
    result = []
    for company in sorted(groups):
        previous = None
        for month in months:
            as_of = last_day(month)
            if as_of > cutoff:
                break
            window_months = [shift_month(month, offset) for offset in range(-5, 1)]
            inputs = [_monthly(indexed.get((company, source_month)), source_month, as_of, first)
                      for source_month in window_months]
            prior, recent = _window(inputs[:3]), _window(inputs[3:])
            current = inputs[-1]
            state, state_reasons = _state(current)
            direction, direction_reasons, deltas = _direction(prior, recent, protocol['direction']['threshold'])
            receipts = _difference(recent['receipts_ratio'], prior['receipts_ratio'])
            payments = _difference(recent['payments_ratio'], prior['payments_ratio'])
            components = {
                'receipts_delta_ratio': receipts, 'payments_delta_ratio': payments,
                'identified_net_delta_ratio': _difference(receipts, payments),
                'receipt_contribution_ratio': receipts,
                'payment_contribution_ratio': None if payments is None else -payments,
                'gross_delta_eur': _difference(recent['gross_eur'], prior['gross_eur']),
            }
            explanation_reasons = set(prior['explanation_reasons'] + recent['explanation_reasons'])
            if any(value is None for value in components.values()):
                explanation_reasons.add('partial_explanation')
            signal = _signal(company, as_of.isoformat(), direction, previous, version)
            previous = signal
            valid_prior = sum(item['input_eligible'] for item in inputs[:3])
            valid_recent = sum(item['input_eligible'] for item in inputs[3:])
            result.append({
                'company_id': company, 'group_id': groups[company], 'month': month.isoformat(),
                'as_of': as_of.isoformat(), 'protocol_version': version,
                'operating_state': state, 'state_reasons': state_reasons,
                'monthly_input_eligible': current['input_eligible'],
                'direction': direction, 'direction_reasons': direction_reasons,
                'direction_input_eligible': valid_prior + valid_recent == 6,
                'evidence': {
                    'monthly': current, 'prior': prior, 'recent': recent, 'delta_intervals': deltas,
                    'components': components, 'unit': 'fraction of observed base gross; 0.05 = 5 percentage points',
                    'explanation_reasons': sorted(explanation_reasons),
                    'explanation_limits': protocol['direction']['explanation_limits'],
                    'source_refs': [item['source_ref'] for item in inputs if item['source_ref'] is not None],
                },
                'coverage': {
                    'valid_months_prior': valid_prior, 'valid_months_recent': valid_recent,
                    'valid_months_total': valid_prior + valid_recent,
                    'required_months': {'prior': 3, 'recent': 3, 'total': 6},
                    'rules': {
                        'monthly_input': protocol['monthly_state']['eligibility'],
                        'direction_input': protocol['direction']['input_eligibility'],
                        'aggregation': protocol['direction']['comparison_precision'],
                        'availability': protocol['source']['availability'],
                    },
                    'failure_reasons': [{'month': item['month'], 'reasons': list(item['reasons'])}
                                        for item in inputs if not item['input_eligible']],
                },
                'signal': signal,
            })
    return result


def alerts_from_trajectory(trajectory, method=None):
    """Extract issued alerts from built signal history; filter evaluation dates only afterwards.

    No episode is restarted here, even when given a slice of already-built rows.
    method is candidate, baseline, or None for both. No event labels are consulted.
    """
    require(method in (None, 'candidate', 'baseline'), 'Unknown alert method')
    alerts, seen = [], set()
    for row in trajectory:
        key = row['company_id'], row['month']
        require(key not in seen, 'Duplicate trajectory company/month')
        seen.add(key)
        signal = row['signal']
        for name, date_key in (('candidate', 'confirmed_alert_at'), ('baseline', 'first_signal_at')):
            if method not in (None, name) or signal[date_key] != row['as_of']:
                continue
            alerts.append({
                'alert_id': _id(row['protocol_version'], row['company_id'], signal['sign'], row['month'], name),
                'company_id': row['company_id'], 'group_id': row['group_id'],
                'protocol_version': row['protocol_version'], 'sign': signal['sign'],
                'origin_month': row['month'], 'issued_at': row['as_of'],
                'first_signal_at': signal['first_signal_at'],
                'confirmed_alert_at': signal['confirmed_alert_at'] if name == 'candidate' else None,
                'episode_id': signal['episode_id'], 'method': name, 'comparison_only': name == 'baseline',
            })
    return sorted(alerts, key=lambda row: (row['company_id'], row['sign'], row['origin_month'], row['method']))
