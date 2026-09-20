from dataclasses import dataclass
import math
import random
import statistics

from scripts.financial_v3_contract import POLICY_VERSION, default_policy, finite_number, month_end, monthly_cutoff, require, utc_timestamp
from scripts.financial_v3_cash import month_shift
from scripts.financial_v3_events import (
    CLASSES, SIGNS, _metadata, _scan_scope, require_scope, directional_label, conditional_label,
)


POLICY = default_policy()


@dataclass(frozen=True)
class ForecastOrigin:
    company_id: str
    group_id: str
    month: str
    sign: str
    probability: float | None
    issued_at: str
    headline: float | None
    input_eligible: bool
    method: str
    policy_version: str = POLICY_VERSION


def _validate_origins(origins, scope):
    require_scope(scope)
    prior, keys = {}, set()
    for row in origins:
        require(type(row) is ForecastOrigin, 'Typed ForecastOrigin required')
        require_scope(scope, row.company_id, row.group_id, row.month)
        require(row.policy_version == POLICY_VERSION and row.sign in SIGNS and bool(row.method), 'Invalid forecast policy/sign/method')
        require(type(row.input_eligible) is bool, 'Eligibility must be explicit boolean')
        if row.probability is not None:
            require(0 <= finite_number(row.probability) <= 1, 'Invalid probability')
        if row.headline is not None:
            require(0 <= finite_number(row.headline) <= 100, 'Invalid headline')
        issued = utc_timestamp(row.issued_at)
        require(issued >= monthly_cutoff(str(month_end(row.month))), 'Cannot issue before origin closed')
        identity = (row.company_id, row.sign, row.method)
        require((identity, row.month) not in keys, 'Duplicate forecast origin')
        keys.add((identity, row.month))
        if identity in prior:
            previous = prior[identity]
            require(previous.month < row.month and utc_timestamp(previous.issued_at) < issued, 'Forecast chronology or actual issue order invalid')
        prior[identity] = row


def issue_signals(origins, *, scope):
    require_scope(scope)
    origins = tuple(origins)
    _validate_origins(origins, scope)
    states, result = {}, []
    threshold = POLICY['development']['alerts']['probability_threshold']
    for row in origins:
        key = (row.company_id, row.sign, row.method)
        state = states.setdefault(key, {'previous': None, 'watch': None, 'active': False, 'below': 0})
        if state['previous'] is not None and month_shift(state['previous'], 1) != row.month:
            state['watch'], state['below'] = None, 0
        eligible = row.input_eligible and row.headline is not None and row.probability is not None
        first, confirmed = None, None
        if not eligible:
            status = 'unavailable'
            state['watch'], state['below'] = None, 0
        elif row.probability < threshold:
            status = 'below_threshold'
            state['watch'] = None
            state['below'] += 1
            if state['below'] >= POLICY['development']['alerts']['reset_below_threshold_origins']:
                state['active'] = False
        else:
            state['below'] = 0
            if state['active']:
                status = 'suppressed'
            elif state['watch'] is None:
                status = 'watch'
                state['watch'] = row.issued_at
                first = row.issued_at
            else:
                status = 'confirmed'
                first, confirmed = state['watch'], row.issued_at
                state['active'] = True
                state['watch'] = None
        state['previous'] = row.month
        result.append({'company_id': row.company_id, 'group_id': row.group_id, 'month': row.month, 'sign': row.sign,
                       'method': row.method, 'status': status, 'probability': row.probability, 'headline': row.headline,
                       'input_eligible': eligible, 'first_signal_at': first, 'confirmed_alert_at': confirmed,
                       'issued_at': row.issued_at, 'policy_version': POLICY_VERSION,
                       'automatic_alerts_enabled': False, 'mock_only': True})
    return result


def _distance(origin, onset):
    return (int(onset[:4]) - int(origin[:4])) * 12 + int(onset[5:7]) - int(origin[5:7])


def _eligible_origin(row):
    return row.input_eligible and row.headline is not None


def _mature_followup(scan, alert, observed):
    company, origin = alert['company_id'], alert['month']
    rows = [scan.rows.get((company, month_shift(origin, i))) for i in range(-2, 5)]
    if not all(r is not None and r.complete for r in rows) or len({r.perimeter_id for r in rows}) != 1:
        return False
    if any(scan.decisions.get((company, alert['sign'], month_shift(origin, i))) in (None, 'unknown') for i in (1, 2, 3)):
        return False
    meta = _metadata(rows)
    return meta['observable_at'] is not None and utc_timestamp(meta['observable_at']) <= observed


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def match_alerts(alerts, scan, origins, *, as_at, scope, cohort=None, origin_window=None):
    _scan_scope(scan, scope)
    origins, alerts = tuple(origins), tuple(alerts)
    _validate_origins(origins, scope)
    issued_signals = issue_signals(origins, scope=scope)
    registered = {(r['company_id'], r['sign'], r['month'], r['method']): r for r in issued_signals if r['status'] == 'confirmed'}
    for alert in alerts:
        key = (alert['company_id'], alert['sign'], alert['month'], alert['method'])
        require(key in registered and alert == registered[key], 'Alert does not replay prospective watch/confirmation/episode history')
    require(cohort in (None, 'still_strong_unvalidated'), 'Unknown cohort')
    if origin_window is not None:
        require(len(origin_window) == 2 and origin_window[0] <= origin_window[1], 'Invalid origin window')
        for month in origin_window:
            require_scope(scope, month=month)
    origins = tuple(r for r in origins if (cohort is None or _eligible_origin(r) and r.headline >= 80) and
                    (origin_window is None or origin_window[0] <= r.month <= origin_window[1]))
    selected_keys = {(r.company_id, r.sign, r.month, r.method) for r in origins}
    alerts = tuple(r for r in alerts if (r['company_id'], r['sign'], r['month'], r['method']) in selected_keys)
    observed = utc_timestamp(as_at)
    methods = {r.method for r in origins}.union(a['method'] for a in alerts)
    require(len(methods) <= 1, 'Match each method separately; event denominator independent of method firing')
    signs = {r.sign for r in origins}.union(a['sign'] for a in alerts)
    opportunities = [r for r in origins if _eligible_origin(r) and utc_timestamp(r.issued_at) <= observed]
    origin_index = {(r.company_id, r.sign, r.month, r.method): r for r in origins}
    event_pool, excluded = [], []
    for event in scan.events:
        if event.sign not in signs:
            continue
        if utc_timestamp(event.observable_at) > observed:
            excluded.append({'event_id': event.event_id, 'reason': 'event_not_yet_observable'})
            continue
        available = any(r.company_id == event.company_id and r.sign == event.sign and
                        _distance(r.month, event.onset) in (1, 2, 3) for r in opportunities)
        (event_pool if available else excluded).append(event if available else {'event_id': event.event_id, 'reason': 'no_input_eligible_lead_origin'})
    seen = set()
    for alert in alerts:
        require_scope(scope, alert['company_id'], alert['group_id'], alert['month'])
        key = (alert['company_id'], alert['sign'], alert['month'], alert['method'])
        require(key not in seen and key in origin_index, 'Duplicate or unregistered alert')
        seen.add(key)
        row = origin_index[key]
        require(alert['status'] == 'confirmed' and _eligible_origin(row) and row.probability is not None and row.probability >= 0.6,
                'Only confirmed eligible high alerts enter matching')
        require(alert['confirmed_alert_at'] == row.issued_at and alert['first_signal_at'] is not None and
                utc_timestamp(alert['first_signal_at']) < utc_timestamp(alert['confirmed_alert_at']) <= observed,
                'Invalid first/confirmation timestamps or future alert')
    matched, matches, false, censored, late = set(), [], [], [], []
    for alert in sorted(alerts, key=lambda a: (a['company_id'], a['sign'], utc_timestamp(a['confirmed_alert_at']))):
        candidates = [e for e in event_pool if e.event_id not in matched and e.company_id == alert['company_id'] and
                      e.sign == alert['sign'] and _distance(alert['month'], e.onset) in (1, 2, 3) and
                      utc_timestamp(alert['confirmed_alert_at']) < utc_timestamp(e.onset + 'T00:00:00Z')]
        if candidates:
            event = min(candidates, key=lambda e: e.onset)
            matched.add(event.event_id)
            matches.append({'event_id': event.event_id, 'alert_month': alert['month'], 'company_id': event.company_id,
                            'group_id': event.group_id, 'sign': event.sign, 'onset': event.onset,
                            'first_signal_at': alert['first_signal_at'], 'confirmed_alert_at': alert['confirmed_alert_at'],
                            'outcome_observable_at': event.observable_at, 'lead_months': _distance(alert['month'], event.onset)})
        else:
            (false if _mature_followup(scan, alert, observed) else censored).append(alert)
            if any(e.company_id == alert['company_id'] and e.sign == alert['sign'] and
                   0 <= _distance(e.onset, alert['month']) <= 3 for e in event_pool):
                late.append(alert)
    misses = [e.event_id for e in event_pool if e.event_id not in matched]
    leads = [m['lead_months'] for m in matches]
    group_rates = {}
    for group in sorted({r.group_id for r in opportunities}):
        es = [e for e in event_pool if e.group_id == group]
        ms = [m for m in matches if m['group_id'] == group]
        fs = [a for a in false if a['group_id'] == group]
        gs = [a for a in alerts if a['group_id'] == group]
        os = [r for r in opportunities if r.group_id == group]
        group_rates[group] = {'recall': _ratio(len(ms), len(es)), 'precision': _ratio(len(ms), len(ms) + len(fs)),
                              'burden': _ratio(len(gs), len(os))}
    macro = {k: statistics.mean(v[k] for v in group_rates.values() if v[k] is not None)
             if any(v[k] is not None for v in group_rates.values()) else None for k in ('recall', 'precision', 'burden')}
    labeled = [directional_label(scan, r.company_id, r.month, r.sign, as_at=as_at, scope=scope) for r in opportunities]
    probability_report = probability_metrics([v['value'] for v in labeled], [r.probability for r in opportunities], [r.group_id for r in opportunities])
    mature = sum(v['value'] is not None for v in labeled)
    support = POLICY['evaluation']['support_directional']
    support_sufficient = (len(group_rates) >= support['groups'] and len(event_pool) >= support['mature_events'] and
                          len({e.group_id for e in event_pool}) >= support['event_groups'] and
                          sum(v['value'] == 0 for v in labeled) >= support['mature_negatives'])
    return {'events': len(event_pool), 'matches': len(matches), 'misses': len(misses), 'alerts': len(alerts),
            'false': len(false), 'censored': len(censored), 'opportunities': len(opportunities),
            'intended_origins': len(origins), 'prediction_coverage': _ratio(sum(_eligible_origin(r) and r.probability is not None for r in origins), len(origins)),
            'recall': _ratio(len(matches), len(event_pool)), 'precision': _ratio(len(matches), len(matches) + len(false)),
            'false_alert_share': _ratio(len(false), len(matches) + len(false)), 'burden': _ratio(len(alerts), len(opportunities)),
            'lead_months': leads, 'median_positive_lead': statistics.median(leads) if leads else None,
            'matched_pairs': matches, 'missed_event_ids': misses, 'false_alerts': false, 'censored_alerts': censored,
            'excluded_events': excluded, 'nonpositive_late_alerts': len(late), 'per_group': group_rates, 'macro_group': macro,
            'probability_metrics': probability_report, 'mature_ascertainment': _ratio(mature, len(opportunities)),
            'mature_negative_origins': sum(v['value'] == 0 for v in labeled), 'event_groups': len({e.group_id for e in event_pool}),
            'support_sufficient': support_sufficient, 'predictive_success': False, 'mock_only': True}


def probability_metrics(labels, probabilities, groups):
    require(len(labels) == len(probabilities) == len(groups), 'Probability metric arrays differ in length')
    rows, unknown, missing = [], 0, 0
    for y, p, g in zip(labels, probabilities, groups):
        require(y is None or type(y) is int and y in (0, 1), 'Binary labels must be 0/1/None')
        if p is not None:
            require(0 <= finite_number(p) <= 1, 'Invalid probability')
        if y is None:
            unknown += 1
        elif p is None:
            missing += 1
        else:
            rows.append((y, p, g))
    positives = sum(y for y, _, _ in rows)
    negatives = len(rows) - positives
    ap = None
    if positives and negatives:
        tp, fp, prior_recall, ap = 0, 0, 0, 0.0
        for threshold in sorted({p for _, p, _ in rows}, reverse=True):
            tied = [y for y, p, _ in rows if p == threshold]
            tp += sum(tied)
            fp += len(tied) - sum(tied)
            recall = tp / positives
            ap += (recall - prior_recall) * tp / (tp + fp)
            prior_recall = recall
    bins = []
    for i in range(10):
        selected = [(y, p) for y, p, _ in rows if min(int(p * 10), 9) == i]
        bins.append({'lower': i / 10, 'upper': (i + 1) / 10, 'count': len(selected),
                     'mean_probability': statistics.mean(p for _, p in selected) if selected else None,
                     'observed_rate': statistics.mean(y for y, _ in selected) if selected else None})
    ece = math.fsum(b['count'] * abs(b['mean_probability'] - b['observed_rate']) for b in bins if b['count']) / len(rows) if rows else None
    group_scores = {}
    for y, p, g in rows:
        group_scores.setdefault(g, []).append((p - y) ** 2)
    support = POLICY['evaluation']['probability_gates']
    return {'rows': len(rows), 'positive_support': positives, 'negative_support': negatives, 'groups': len(group_scores),
            'unknown_labels': unknown, 'missing_predictions': missing, 'ap': ap,
            'constant_ap_reference': _ratio(positives, len(rows)) if positives and negatives else None,
            'brier': statistics.mean((p - y) ** 2 for y, p, _ in rows) if rows else None,
            'macro_group_brier': statistics.mean(statistics.mean(v) for v in group_scores.values()) if group_scores else None,
            'ece': ece, 'calibration': bins, 'support_sufficient': positives >= support['positive_support'] and
            negatives >= support['negative_support'] and len(group_scores) >= support['groups'], 'predictive_success': False}


def classification_metrics(labels, probabilities, groups):
    require(len(labels) == len(probabilities) == len(groups), 'Classification arrays differ in length')
    matrix = {c: dict.fromkeys((*CLASSES, 'abstain'), 0) for c in CLASSES}
    unknown = 0
    class_groups = {c: set() for c in CLASSES}
    for y, p, group in zip(labels, probabilities, groups):
        require(y is None or y in CLASSES, 'Unknown conditional class')
        if p is not None:
            require(set(p) == set(CLASSES) and all(0 <= finite_number(v) <= 1 for v in p.values()) and abs(math.fsum(p.values()) - 1) <= 1e-8,
                    'Exact three-class normalized probabilities required')
        if y is None:
            unknown += 1
            continue
        prediction = 'abstain'
        if p is not None:
            best = max(CLASSES, key=lambda c: p[c])
            if p[best] >= POLICY['development']['alerts']['conditional_decision_threshold']:
                prediction = best
        matrix[y][prediction] += 1
        class_groups[y].add(group)
    per_class = {}
    probability_reports = {}
    for c in CLASSES:
        n = sum(matrix[c].values())
        predicted = sum(matrix[y][c] for y in CLASSES)
        per_class[c] = {'support': n, 'groups': len(class_groups[c]), 'recall': _ratio(matrix[c][c], n), 'precision': _ratio(matrix[c][c], predicted)}
        probability_reports[c] = probability_metrics([None if y is None else int(y == c) for y in labels],
                                                     [None if p is None else p[c] for p in probabilities], groups)
    total = sum(sum(v.values()) for v in matrix.values())
    decided = total - sum(matrix[c]['abstain'] for c in CLASSES)
    sufficient = all(per_class[c]['support'] >= (30 if c == 'mixed' else 50) and per_class[c]['groups'] >= 10 for c in CLASSES)
    return {'matrix': matrix, 'per_class': per_class, 'unknown_labels': unknown,
            'balanced_accuracy': statistics.mean(per_class[c]['recall'] for c in CLASSES) if all(per_class[c]['recall'] is not None for c in CLASSES) else None,
            'decision_coverage': _ratio(decided, total), 'complete_followup': _ratio(total, len(labels)),
            'probability_metrics': probability_reports,
            'macro_ap': statistics.mean(r['ap'] for r in probability_reports.values()) if all(r['ap'] is not None for r in probability_reports.values()) else None,
            'support_sufficient': sufficient, 'predictive_success': False}


def _quantile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (position - low) * (values[high] - values[low])


def grouped_uncertainty(group_values, *, scope, test_only_replicates=None, higher_is_better=False):
    require_scope(scope)
    require(set(group_values) <= {g for _, g in scope.company_groups}, 'Bootstrap groups outside fixture scope')
    require(type(higher_is_better) is bool, 'Explicit paired direction required')
    spec = POLICY['evaluation']['bootstrap']
    n = spec['replicates'] if test_only_replicates is None else test_only_replicates
    require(type(n) is int and 2 <= n <= spec['replicates'], 'Invalid test-only bootstrap count')
    for pair in group_values.values():
        require(len(pair) == 2, 'Paired method/baseline group statistics required')
        for value in pair:
            if value is not None:
                finite_number(value)
    randomizer = random.Random(spec['seed'])
    keys, method, baseline, improvement = sorted(group_values), [], [], []
    for _ in range(n):
        draw = [group_values[randomizer.choice(keys)] for _ in keys]
        if not draw or any(a is None or b is None for a, b in draw):
            continue
        a, b = statistics.mean(v[0] for v in draw), statistics.mean(v[1] for v in draw)
        method.append(a)
        baseline.append(b)
        improvement.append(a - b if higher_is_better else b - a)
    interval = lambda v: [_quantile(v, q) for q in spec['quantiles']] if v else None
    return {'method_interval': interval(method), 'baseline_interval': interval(baseline),
            'paired_improvement_interval': interval(improvement), 'finite_replicates': len(improvement),
            'replicates': n, 'seed': spec['seed'], 'shared_draws': True, 'redraw_undefined': False,
            'test_only': test_only_replicates is not None, 'estimand': 'macro_group_mean_paired_difference',
            'support_sufficient': test_only_replicates is None and len(improvement) >= spec['finite_replicates_min'] and len(keys) >= 20,
            'predictive_success': False}


def q1_reference_status():
    return {'weighted_kappa': None, 'reference_labels': None, 'validated': False,
            'blockers': ['independent_level_reference_unavailable', 'business_rubric_not_approved', 'new_test_missing']}


@dataclass(frozen=True)
class ConditionalForecast:
    event_id: str
    issued_at: str
    probabilities: dict[str, float] | None


def evaluate_development(origins, scan, *, as_at, scope, prespecified_thirds=None, conditional_forecasts=()):
    _scan_scope(scan, scope)
    origins = tuple(origins)
    observed = utc_timestamp(as_at)
    timeline = [r for r in issue_signals(origins, scope=scope) if utc_timestamp(r['issued_at']) <= observed]
    origins = tuple(r for r in origins if utc_timestamp(r.issued_at) <= observed)
    directions = {}
    by_sign = {}
    for sign in SIGNS:
        selected = [r for r in origins if r.sign == sign]
        alerts = [r for r in timeline if r['sign'] == sign and r['status'] == 'confirmed']
        by_sign[sign] = (selected, alerts)
        directions[sign] = match_alerts(alerts, scan, selected, as_at=as_at, scope=scope)
    strong, strong_alerts = by_sign['deterioration']
    thirds = []
    if prespecified_thirds is not None:
        require(len(prespecified_thirds) == 3, 'Exactly three prespecified temporal thirds required')
        for i, bounds in enumerate(prespecified_thirds):
            require(len(bounds) == 2 and bounds[0] <= bounds[1], 'Invalid temporal third')
            require(i == 0 or prespecified_thirds[i - 1][1] < bounds[0], 'Temporal thirds overlap or reverse chronology')
            reports = {sign: match_alerts(alerts, scan, selected, as_at=as_at, scope=scope, origin_window=bounds)
                       for sign, (selected, alerts) in by_sign.items()}
            passing = {sign: r['support_sufficient'] and r['recall'] is not None and r['precision'] is not None and
                       r['recall'] >= POLICY['evaluation'][sign]['recall_min'] and r['precision'] >= POLICY['evaluation'][sign]['precision_min']
                       for sign, r in reports.items()}
            thirds.append({'origin_bounds': list(bounds), 'directions': reports, 'point_gate_pass': passing,
                           'utility_pass': all(passing.values()), 'predictive_success': False})
    episodes = {e.event_id: e for e in scan.events if e.sign == 'deterioration' and utc_timestamp(e.observable_at) <= observed}
    forecasts = {}
    for forecast in conditional_forecasts:
        require(type(forecast) is ConditionalForecast and forecast.event_id in episodes and forecast.event_id not in forecasts,
                'Unique forecast for observable confirmed deterioration required')
        event = episodes[forecast.event_id]
        issued = utc_timestamp(forecast.issued_at)
        require(utc_timestamp(event.observable_at) <= issued <= observed and issued < utc_timestamp(month_shift(event.onset, 4) + 'T00:00:00Z'),
                'Conditional forecast outside confirmation-to-outcome window')
        forecasts[forecast.event_id] = forecast
    labels = [conditional_label(scan, e, as_at=as_at, scope=scope) for e in episodes.values()]
    probabilities = [forecasts[e.event_id].probabilities if e.event_id in forecasts else None for e in episodes.values()]
    q4 = classification_metrics([r['value'] for r in labels], probabilities, [e.group_id for e in episodes.values()])
    q4.update({'advance_detection': False, 'episodes': len(episodes), 'missing_forecasts': len(episodes) - len(forecasts),
               'labels': labels, 'forecast_timestamps': {key: v.issued_at for key, v in forecasts.items()}})
    return {'directions': directions, 'timeline': timeline, 'q1': q1_reference_status(), 'q4': q4, 'temporal_thirds': thirds,
            'temporal_stability_status': 'fixture_only' if prespecified_thirds is not None else 'prespecified_thirds_unavailable',
            'still_strong_unvalidated': match_alerts(strong_alerts, scan, strong, as_at=as_at, scope=scope, cohort='still_strong_unvalidated'),
            'predictive_success': False, 'automatic_alerts_enabled': False, 'evaluation_status': 'mock_development_only',
            'blockers': ['new_test_missing', 'business_review_missing', 'confirmatory_approval_missing'],
            'pending': ['independent_level_review', 'real_certified_features', 'untouched_confirmation', 'operational_activation']}
