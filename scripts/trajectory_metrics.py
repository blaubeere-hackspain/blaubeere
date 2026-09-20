import platform
import random
from collections import Counter

from scripts.trajectory_contract import require


SIGNS = ('improvement', 'deterioration')
METHODS = ('candidate', 'baseline')
COUNTS = ('events', 'alerts', 'matches', 'misses', 'false_alarms', 'censored_unknown')


def empty_counts():
    return {**dict.fromkeys(COUNTS, 0), 'lead_histogram': {'1': 0, '2': 0}}


def summarize(counts):
    require(all(type(counts[key]) is int and counts[key] >= 0 for key in COUNTS), 'Invalid counts')
    require(counts['events'] == counts['matches'] + counts['misses'], 'Event count identity')
    require(counts['alerts'] == counts['matches'] + counts['false_alarms'] + counts['censored_unknown'], 'Alert count identity')
    histogram = counts['lead_histogram']
    require(set(histogram) == {'1', '2'} and all(type(n) is int and n >= 0 for n in histogram.values())
            and sum(histogram.values()) == counts['matches'], 'Lead count identity')
    n = counts['matches']
    median = None if not n else sum(1 if rank < histogram['1'] else 2 for rank in ((n - 1) // 2, n // 2)) / 2
    resolved = counts['matches'] + counts['false_alarms']
    return {**counts,
            'recall': {'value': counts['matches'] / counts['events'] if counts['events'] else None,
                       'reason': None if counts['events'] else 'no_eligible_events'},
            'false_alert_share': {'value': counts['false_alarms'] / resolved if resolved else None,
                                  'reason': None if resolved else 'no_resolved_alerts'},
            'lead_median': {'value': median, 'reason': None if n else 'no_matches'}}


def aggregate(rows):
    result = empty_counts()
    for row in rows:
        summarize(row)
        for key in COUNTS:
            result[key] += row[key]
        for lead in ('1', '2'):
            result['lead_histogram'][lead] += row['lead_histogram'][lead]
    return summarize(result)


def group_draws(size):
    rng = random.Random(1729)
    return [[rng.randrange(size) for _ in range(size)] for _ in range(1000)]


def linear_quantile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (position - lower) * (values[upper] - values[lower])


def bootstrap(group_counts, included_groups):
    groups = sorted(included_groups)
    require(len(groups) == len(set(groups)) == 200, 'Bootstrap requires all 200 included groups')
    indexed = {}
    for row in group_counts:
        key = row['group_id'], row['sign'], row['method']
        require(key not in indexed, 'Duplicate group stratum')
        require(key[0] in groups and key[1] in SIGNS and key[2] in METHODS, 'Unexpected group stratum')
        summarize(row)
        indexed[key] = row
    for group in groups:
        for sign in SIGNS:
            require(len({indexed.get((group, sign, method), empty_counts())['events'] for method in METHODS}) == 1,
                    'Common event denominator mismatch')
    weights = [Counter(draw) for draw in group_draws(len(groups))]
    require(len(weights) == 1000 and all(sum(w.values()) == 200 for w in weights), 'Invalid bootstrap draws')
    strata = []
    for sign in SIGNS:
        for method in METHODS:
            rows = [indexed.get((group, sign, method), empty_counts()) for group in groups]
            events = sum(row['events'] for row in rows)
            event_groups = sum(row['events'] > 0 for row in rows)
            item = {'sign': sign, 'method': method}
            for metric in ('recall', 'false_alert_share'):
                numerators = [r['matches'] if metric == 'recall' else r['false_alarms'] for r in rows]
                denominators = [r['events'] if metric == 'recall' else r['matches'] + r['false_alarms'] for r in rows]
                denominator_groups = sum(value > 0 for value in denominators)
                values = []
                for weight in weights:
                    denominator = sum(copies * denominators[i] for i, copies in weight.items())
                    if denominator:
                        values.append(sum(copies * numerators[i] for i, copies in weight.items()) / denominator)
                reason = ('insufficient_event_support' if events < 5 or event_groups < 5 else
                          'insufficient_denominator_groups' if denominator_groups < 5 else
                          'fewer_than_950_finite_replicates' if len(values) < 950 else None)
                item[metric] = {'ci95': None if reason else [linear_quantile(values, p) for p in (0.025, 0.975)],
                                'reason': reason, 'events': events, 'event_groups': event_groups,
                                'denominator_groups': denominator_groups, 'finite_replicates': len(values),
                                'undefined_replicates': 1000 - len(values),
                                'conditioning': 'Defined replicates only; no redraws'}
            strata.append(item)
    return {'algorithm': 'stdlib random.Random MT19937 randrange; shared group multiplicities; linear percentile quantiles',
            'runtime': {'implementation': platform.python_implementation(), 'python': platform.python_version()},
            'replicates': 1000, 'seed': 1729, 'sampled_groups_per_replicate': 200,
            'copy_equivalence': 'Per-group matching counts preserve every company and chronology within each independent group copy',
            'strata': strata}
