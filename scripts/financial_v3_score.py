import math

from scripts.financial_v3_contract import default_policy, finite_number, reason_codes, require, validate_policy
from scripts.financial_v3_cash import month_shift


INPUT_ORDER = {'generation': ('F', 'S'), 'liquidity': ('C', 'S'), 'debt': ('D', 'F', 'J', 'S'),
               'growth': ('recent_receipts', 'prior_receipts'), 'stability': ('F_std', 'S')}


def clip(value, lower=0.0, upper=100.0):
    return min(upper, max(lower, value))


def dimension_points(key, raw):
    for name in INPUT_ORDER[key]:
        finite_number(raw[name])
    if key == 'growth':
        require(raw['prior_receipts'] > 0, 'zero_reference')
        return clip(50 + 100 * (raw['recent_receipts'] - raw['prior_receipts']) / raw['prior_receipts'])
    require(raw['S'] > 0, 'zero_reference')
    if key == 'generation':
        return clip(50 + 100 * raw['F'] / raw['S'])
    if key == 'liquidity':
        return 100 * clip(raw['C'] / raw['S'] / 3, 0, 1)
    if key == 'debt':
        require(raw['D'] >= 0 and raw['J'] >= 0, 'Negative debt or service')
        stock = 100 * clip(1 - raw['D'] / (12 * raw['S']), 0, 1)
        service = 100 if raw['J'] == 0 else 100 * clip(max(raw['F'], 0) / raw['J'] / 2, 0, 1)
        return 0.5 * stock + 0.5 * service
    require(raw['F_std'] >= 0, 'Negative standard deviation')
    return 100 * clip(1 - raw['F_std'] / raw['S'] / 0.5, 0, 1)


def score_features(features, policy=None):
    policy = validate_policy(default_policy() if policy is None else policy)
    spec, raw = policy['scorecard'], features['raw']
    dimensions, contributions = {}, {}
    reasons = list(features['reasons'])
    for key, weight in spec['weights'].items():
        local_reasons = list(features['dimension_reasons'][key])
        if any(raw.get(name) is None for name in INPUT_ORDER[key]):
            local_reasons.append('missing_source')
        points = None if local_reasons else finite_number(dimension_points(key, raw))
        contribution = None if points is None else weight * (points - spec['base'])
        dimensions[key] = {'points': points, 'weight': weight, 'contribution': contribution,
                           'raw_inputs': {name: raw.get(name) for name in INPUT_ORDER[key]}, 'formula': spec['formulas'][key],
                           'reasons': list(reason_codes(*local_reasons)), 'source_refs': features['source_refs']}
        contributions[key] = contribution
        reasons.extend(local_reasons)
    reasons = list(reason_codes(*reasons))
    if reasons:
        return {'value': None, 'status': 'unavailable', 'reasons': reasons, 'band': None,
                'band_validation': 'unvalidated_internal', 'dimensions': dimensions,
                'known_on': features['known_on'], 'source_refs': features['source_refs'],
                'explanation': {'base': spec['base'], 'contributions': None, 'mora_penalty': None,
                                'limit_adjustment': None, 'residual': None, 'display_rounding_residual': None, 'causal_claim': False}}
    S, E = finite_number(raw['S']), finite_number(raw['E'])
    require(S > 0 and E >= 0, 'Invalid mora denominator/exposure')
    penalty = policy['mora']['penalty_cap'] * (E / (S + E))
    unclipped = math.fsum([spec['base'], *contributions.values(), -penalty])
    value = clip(unclipped)
    limit = value - unclipped
    residual = value - math.fsum([spec['base'], *contributions.values(), -penalty, limit])
    require(abs(residual) <= spec['explanation_residual_tolerance'], 'Level explanation residual')
    band = next(b['name'] for b in spec['bands'] if b['lower'] <= value and (value < b['upper'] or b['upper_inclusive']))
    return {'value': value, 'status': 'eligible', 'reasons': [], 'band': band, 'band_validation': 'unvalidated_internal',
            'dimensions': dimensions, 'known_on': features['known_on'], 'source_refs': features['source_refs'],
            'explanation': {'base': spec['base'], 'contributions': contributions, 'mora_penalty': penalty,
                            'unclipped': unclipped, 'limit_adjustment': limit, 'residual': residual,
                            'display_rounding_residual': round(value, spec['display_decimals']) - value, 'causal_claim': False}}


def ratio_change(old_numerator, old_denominator, new_numerator, new_denominator):
    a0, b0, a1, b1 = [finite_number(x) for x in (old_numerator, old_denominator, new_numerator, new_denominator)]
    require(b0 > 0 and b1 > 0, 'zero_reference')
    numerator, denominator = (a1 - a0) / b0, a1 * (1 / b1 - 1 / b0)
    delta = a1 / b1 - a0 / b0
    return {'numerator': numerator, 'denominator': denominator, 'delta': delta,
            'residual': delta - math.fsum([numerator, denominator]), 'order': ['numerator', 'denominator'], 'causal_claim': False}


def explain_delta(old, new):
    reasons = []
    if old['policy_version'] != new['policy_version']:
        reasons.append('version_changed')
    if month_shift(old['month'], 1) != new['month']:
        reasons.append('insufficient_history')
    for key in ('company_id', 'perimeter_id', 'reporting_currency', 'calendar', 'view'):
        if old[key] != new[key]:
            reasons.append('coverage_changed')
    if old['score']['value'] is None or new['score']['value'] is None:
        reasons.extend(old['score']['reasons'] + new['score']['reasons'] + ['missing_source'])
    sources = sorted(set(old['score']['source_refs'] + new['score']['source_refs']))
    if reasons:
        return {'value': None, 'reasons': list(reason_codes(*reasons)), 'source_refs': sources,
                'contributions': None, 'mora_penalty': None, 'limit_adjustment': None, 'residual': None,
                'dimension_steps': None, 'direction': 'insufficient_evidence', 'causal_claim': False}
    before, after = old['score']['explanation'], new['score']['explanation']
    contributions = {k: after['contributions'][k] - v for k, v in before['contributions'].items()}
    penalty = after['mora_penalty'] - before['mora_penalty']
    limit = after['limit_adjustment'] - before['limit_adjustment']
    delta = new['score']['value'] - old['score']['value']
    residual = delta - math.fsum([*contributions.values(), -penalty, limit])
    require(abs(residual) <= 1e-8, 'Delta explanation residual')
    steps, ratios = {}, {}
    a, b = old['features']['raw'], new['features']['raw']
    for key, order in INPUT_ORDER.items():
        state = dict(a)
        weight = new['score']['dimensions'][key]['weight']
        last = dimension_points(key, state)
        steps[key] = {}
        for name in order:
            state[name] = b[name]
            current = dimension_points(key, state)
            steps[key][name] = (current - last) * weight
            last = current
        require(abs(math.fsum(steps[key].values()) - contributions[key]) <= 1e-8, 'Dimension telescoping residual')
    for name, numerator, denominator in [('generation', 'F', 'S'), ('liquidity', 'C', 'S'),
                                          ('debt_stock', 'D', 'S'), ('growth', 'recent_receipts', 'prior_receipts'),
                                          ('stability', 'F_std', 'S'), ('service', 'F', 'J')]:
        if a[denominator] > 0 and b[denominator] > 0:
            ratios[name] = ratio_change(a[numerator], a[denominator], b[numerator], b[denominator])
    ratios['mora'] = ratio_change(a['E'], a['S'] + a['E'], b['E'], b['S'] + b['E'])
    return {'value': delta, 'reasons': [], 'source_refs': sources, 'contributions': contributions,
            'mora_penalty': penalty, 'limit_adjustment': limit, 'residual': residual, 'dimension_steps': steps,
            'ratio_changes': ratios, 'order_dependent': True,
            'reference_changes': {'S_old': a['S'], 'S_new': b['S'], 'changed': a['S'] != b['S']},
            'direction': 'improving' if delta >= 5 else 'deteriorating' if delta <= -5 else 'stable', 'causal_claim': False}
