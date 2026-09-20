from collections import Counter
from datetime import date

from scripts.model_features import last_day, shift_month
from scripts.trajectory_contract import require, validate_rules
from scripts.trajectory_metrics import METHODS, SIGNS, aggregate, empty_counts
from scripts.trajectory_signals import UNKNOWN, _id, alerts_from_trajectory


ROBUST = {'deficit', 'non_deficit'}
PATTERNS = {'deterioration': ['non_deficit', 'non_deficit', 'deficit', 'deficit'],
            'improvement': ['deficit', 'deficit', 'non_deficit', 'non_deficit'],
            'recovered_dip': ['non_deficit', 'non_deficit', 'deficit', 'non_deficit', 'non_deficit']}


def partition_groups(assignment):
    require(all(isinstance(group, str) and group and label in ('train', 'validation', 'final_test')
                for group, label in assignment.items()), 'Invalid group assignment')
    require(Counter(assignment.values()) == {'train': 150, 'validation': 50, 'final_test': 50}, 'Invalid group counts')
    return (sorted(g for g, label in assignment.items() if label != 'final_test'),
            sorted(g for g, label in assignment.items() if label == 'final_test'))


def months_between(first, last):
    first, last = date.fromisoformat(str(first)), date.fromisoformat(str(last))
    require(first.day == last.day == 1 and first <= last, 'Invalid month range')
    return [shift_month(first, i) for i in range((last.year - first.year) * 12 + last.month - first.month + 1)]


def state_window(index, company, months, cutoff, calendar_first):
    reasons = set()
    for month in months:
        row = index.get((company, month.isoformat()))
        if month < calendar_first:
            reasons.add('left_history_unavailable')
        elif last_day(month) > cutoff:
            reasons.add('right_immature')
        elif row is None:
            reasons.add('missing_month')
        elif row['operating_state'] not in ROBUST:
            reasons.update(row.get('state_reasons') or ['insufficient_state'])
    return {'complete': not reasons, 'reasons': sorted(reasons)}


def _index(rows):
    index, groups = {}, {}
    for row in rows:
        company, group, month = row['company_id'], row['group_id'], row['month']
        require(isinstance(company, str) and company and isinstance(group, str) and group, 'Invalid identity')
        require(date.fromisoformat(month).day == 1, 'Invalid month grain')
        require(row['as_of'] == last_day(month).isoformat(), 'Invalid as-of date')
        require(row['operating_state'] in ROBUST | {UNKNOWN}, 'Invalid operating state')
        require((company, month) not in index, 'Duplicate company/month')
        require(company not in groups or groups[company] == group, 'Unstable company/group')
        index[company, month] = row
        groups[company] = group
    return index, groups


def match_alerts(events, alerts, trajectory, cutoff, calendar_first):
    cutoff = date.fromisoformat(str(cutoff))
    calendar_first = date.fromisoformat(str(calendar_first))
    index, groups = _index(trajectory)
    event_keys, alert_keys, ids = set(), set(), set()
    eligible = []
    for event in events:
        key = event['company_id'], event['sign'], event['onset_month']
        require(key not in event_keys and event['event_id'] not in ids, 'Duplicate event identity')
        require(event['sign'] in SIGNS and event['group_id'] == groups.get(event['company_id']), 'Invalid event identity')
        event_keys.add(key)
        ids.add(event['event_id'])
        if event['eligible']:
            require(event['confirmed_at'] <= cutoff.isoformat(), 'Immature eligible event')
            eligible.append(event)
    for alert in alerts:
        key = alert['company_id'], alert['sign'], alert['origin_month'], alert['method']
        require(key not in alert_keys and alert['alert_id'] not in ids, 'Duplicate alert identity')
        require(alert['sign'] in SIGNS and alert['method'] in METHODS, 'Invalid alert stratum')
        require(alert['group_id'] == groups.get(alert['company_id']), 'Invalid alert group')
        require(alert['issued_at'] == last_day(alert['origin_month']).isoformat(), 'Invalid alert issue date')
        alert_keys.add(key)
        ids.add(alert['alert_id'])
    eligible.sort(key=lambda e: (e['company_id'], e['sign'], e['onset_month']))
    used, result = set(), []
    for alert in sorted(alerts, key=lambda a: (a['company_id'], a['sign'], a['origin_month'], a['method'])):
        origin = date.fromisoformat(alert['origin_month'])
        followup = state_window(index, alert['company_id'], [shift_month(origin, i) for i in range(-1, 4)], cutoff, calendar_first)
        match, lead = None, None
        for event in eligible:
            if event['company_id'] != alert['company_id'] or event['sign'] != alert['sign']:
                continue
            onset = date.fromisoformat(event['onset_month'])
            difference = (onset.year - origin.year) * 12 + onset.month - origin.month
            if difference in (1, 2) and (alert['method'], event['event_id']) not in used:
                match, lead = event['event_id'], difference
                used.add((alert['method'], match))
                break
        result.append({**alert, 'status': 'matched' if match else 'false_alarm' if followup['complete'] else 'censored_unknown',
                       'event_id': match, 'lead_months': lead, 'complete_followup': followup['complete'],
                       'followup_reasons': followup['reasons']})
    return result


def _cases(patterns, index):
    result = {}
    for kind in (*SIGNS, 'recovered_dip'):
        qualifying = sorted((p for p in patterns if p['sign'] == kind and p['eligible']),
                            key=lambda p: (p['confirmed_at'], p['company_id'], p['onset_month']))
        if not qualifying:
            result[kind] = {'status': 'unavailable', 'reason': 'no_qualifying_development_pattern',
                            'selection_proof': {'qualifying_patterns': 0}}
            continue
        chosen = qualifying[0]
        timeline = []
        for month in chosen['pattern_months']:
            row = index[chosen['company_id'], month]
            monthly = row['evidence']['monthly']
            timeline.append({'month': month, 'as_of': row['as_of'], 'operating_state': row['operating_state'],
                             'base': monthly['base'], 'trim': monthly['trim'], 'direction': row['direction'],
                             'signal': row['signal']})
        result[kind] = {**chosen, 'status': 'available', 'timeline': timeline,
                        'history_ref': {'artifact': 'development-case-history.parquet', 'company_id': chosen['company_id']},
                        'selection_proof': {'sort': ['confirmed_at', 'company_id', 'onset_month'], 'rank': 1,
                                            'qualifying_patterns': len(qualifying), 'eligible_origins': chosen['eligible_origins'],
                                            'selection_uses_alerts_or_probability': False, 'source_phase': 'development'}}
    return result


def evaluate_period(trajectory, protocol, assignment, phase):
    validate_rules(protocol)
    require(phase in ('development', 'reserved'), 'Unknown evaluation phase')
    included, excluded = partition_groups(assignment)
    period = protocol['evaluation'][phase]
    first = date.fromisoformat(period['alert_origin_first'])
    last = date.fromisoformat(period['alert_origin_last'])
    cutoff = date.fromisoformat(period['outcomes_through'])
    calendar_first = date.fromisoformat(protocol['source']['calendar']['first_month'])
    rows, excluded_rows = [], 0
    for row in trajectory:
        require(row['group_id'] in assignment, 'Unknown group')
        if row['group_id'] in excluded:
            excluded_rows += 1
            continue
        require(calendar_first <= date.fromisoformat(row['month']) and row['as_of'] <= cutoff.isoformat(), 'Trajectory outside phase cutoff')
        rows.append(row)
    index, companies = _index(rows)
    origins = months_between(first, last)
    onsets = months_between(shift_month(first, 1), shift_month(last, 2))
    coverage = {'companies': len(companies), 'included_groups': len(included), 'excluded_groups': len(excluded),
                'groups_with_companies': len(set(companies.values())), 'excluded_trajectory_rows': excluded_rows,
                'origin_opportunities': len(companies) * len(origins), 'input_eligible_origins': 0,
                'input_ineligible_origins': 0, 'preperiod_crossing_episodes': 0}
    origin_reasons, origin_left, window_reasons = Counter(), 0, Counter()
    windows, patterns = [], []
    for company, group in sorted(companies.items()):
        for origin in origins:
            row = index.get((company, origin.isoformat()))
            if row and row['direction_input_eligible']:
                coverage['input_eligible_origins'] += 1
            else:
                coverage['input_ineligible_origins'] += 1
                origin_reasons.update(set(row['direction_reasons'] or ['input_ineligible']) if row else ['missing_month'])
            if shift_month(origin, -5) < calendar_first:
                origin_left += 1
        boundary = index.get((company, first.isoformat()))
        if boundary and boundary['signal']['first_signal_at'] and boundary['signal']['first_signal_at'] < first.isoformat():
            coverage['preperiod_crossing_episodes'] += 1
        for onset in onsets:
            eligible_origins = [origin.isoformat() for origin in (shift_month(onset, -2), shift_month(onset, -1))
                                if first <= origin <= last and index.get((company, origin.isoformat()), {}).get('direction_input_eligible', False)]
            pattern_months = [shift_month(onset, i) for i in range(-2, 2)]
            observed = state_window(index, company, pattern_months, cutoff, calendar_first)
            sign = None
            if observed['complete']:
                states = [index[company, m.isoformat()]['operating_state'] for m in pattern_months]
                sign = next((s for s in SIGNS if states == PATTERNS[s]), None)
            window_reasons.update(observed['reasons'])
            windows.append({'company_id': company, 'group_id': group, 'onset_month': onset.isoformat(),
                            'status': 'unknown' if not observed['complete'] else 'event' if sign else 'observed_nonevent',
                            'sign': sign, 'reasons': observed['reasons'], 'eligible_origins': eligible_origins})
            kinds = [sign] if sign else []
            if phase == 'development':
                dip_months = [shift_month(onset, i) for i in range(-2, 3)]
                if state_window(index, company, dip_months, cutoff, calendar_first)['complete']:
                    if [index[company, m.isoformat()]['operating_state'] for m in dip_months] == PATTERNS['recovered_dip']:
                        kinds.append('recovered_dip')
            for kind in kinds:
                pattern_end = 2 if kind == 'recovered_dip' else 1
                patterns.append({'event_id': _id(protocol['protocol_version'], company, kind, onset.isoformat()),
                                 'company_id': company, 'group_id': group, 'sign': kind, 'onset_month': onset.isoformat(),
                                 'confirmed_at': last_day(shift_month(onset, pattern_end)).isoformat(),
                                 'pattern_months': [shift_month(onset, i).isoformat() for i in range(-2, pattern_end + 1)],
                                 'eligible': bool(eligible_origins), 'eligible_origins': eligible_origins,
                                 'exclusion_reason': None if eligible_origins else 'no_eligible_origin'})
    events = [p for p in patterns if p['sign'] in SIGNS]
    all_alerts = alerts_from_trajectory(rows)
    alerts = match_alerts(events, [a for a in all_alerts if first.isoformat() <= a['origin_month'] <= last.isoformat()],
                          rows, cutoff, calendar_first)
    group_counts = {(group, sign, method): {'group_id': group, 'sign': sign, 'method': method, **empty_counts()}
                    for group in included for sign in SIGNS for method in METHODS}
    for event in events:
        event['matches_by_method'] = {}
        for method in METHODS:
            match = next((a for a in alerts if a['method'] == method and a['event_id'] == event['event_id']), None)
            event['matches_by_method'][method] = None if match is None else match['alert_id']
            if event['eligible']:
                count = group_counts[event['group_id'], event['sign'], method]
                count['events'] += 1
                count['misses'] += int(match is None)
    for alert in alerts:
        count = group_counts[alert['group_id'], alert['sign'], alert['method']]
        count['alerts'] += 1
        count[{'matched': 'matches', 'false_alarm': 'false_alarms', 'censored_unknown': 'censored_unknown'}[alert['status']]] += 1
        if alert['status'] == 'matched':
            count['lead_histogram'][str(alert['lead_months'])] += 1
    strata = [{'sign': sign, 'method': method,
               **aggregate([count for (group, s, m), count in group_counts.items() if s == sign and m == method]),
               'coverage': {'alerts_incomplete_followup': sum(a['sign'] == sign and a['method'] == method and not a['complete_followup'] for a in alerts),
                            'matched_incomplete_followup': sum(a['sign'] == sign and a['method'] == method and a['status'] == 'matched' and not a['complete_followup'] for a in alerts),
                            'events_without_eligible_origin': sum(e['sign'] == sign and not e['eligible'] for e in events),
                            'preperiod_issued_alerts': sum(a['sign'] == sign and a['method'] == method and a['origin_month'] < first.isoformat() for a in all_alerts)}}
              for sign in SIGNS for method in METHODS]
    statuses = Counter(w['status'] for w in windows)
    coverage.update(input_ineligible_reasons=dict(sorted(origin_reasons.items())), left_history_origins=origin_left,
                    event_window_opportunities=len(windows), event_windows_observed=sum(w['status'] != 'unknown' for w in windows),
                    event_windows_observed_nonevent=statuses['observed_nonevent'], event_windows_unknown=statuses['unknown'],
                    event_windows_events=statuses['event'], event_window_unknown_reasons=dict(sorted(window_reasons.items())),
                    event_windows_left_unknown=window_reasons['left_history_unavailable'],
                    event_windows_right_immature=window_reasons['right_immature'],
                    event_exclusions_no_eligible_origin=sum(not e['eligible'] for e in events),
                    alerts_incomplete_followup=sum(not a['complete_followup'] for a in alerts),
                    preperiod_issued_alerts={method: sum(a['method'] == method and a['origin_month'] < first.isoformat() for a in all_alerts)
                                             for method in METHODS})
    return {'phase': phase, 'period': period, 'coverage': coverage, 'strata': strata,
            'group_counts': list(group_counts.values()), 'events': events, 'alerts': alerts, 'event_windows': windows,
            'cases': _cases(patterns, index) if phase == 'development' else None,
            'interpretation': 'Known-company internal retrospective new endpoint; not independent unseen groups, not v1 target evaluation',
            'success_policy': protocol['reporting']['success_policy']}
