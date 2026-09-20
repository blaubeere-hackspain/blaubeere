import copy
import unittest

from scripts.trajectory_metrics import bootstrap, summarize


GROUPS = [f'G{i:03}' for i in range(200)]


def counts(events=0, matches=0, false=0, censored=0):
    return {'events': events, 'alerts': matches + false + censored, 'matches': matches,
            'misses': events - matches, 'false_alarms': false, 'censored_unknown': censored,
            'lead_histogram': {'1': matches, '2': 0}}


def grouped(n=5):
    return [{'group_id': group, 'sign': sign, 'method': method, **counts(1, 1)}
            for group in GROUPS[:n] for sign in ('improvement', 'deterioration') for method in ('candidate', 'baseline')]


class TrajectoryMetricsTests(unittest.TestCase):
    def test_null_rates_and_lead_have_explicit_reasons(self):
        result = summarize(counts())
        self.assertIsNone(result['recall']['value'])
        self.assertEqual(result['recall']['reason'], 'no_eligible_events')
        self.assertEqual(result['false_alert_share']['reason'], 'no_resolved_alerts')
        self.assertEqual(result['lead_median']['reason'], 'no_matches')
        result = summarize(counts(2, 0, 3, 1))
        self.assertEqual(result['recall']['value'], 0)
        self.assertEqual(result['false_alert_share']['value'], 1)
        self.assertIsNone(result['lead_median']['value'])

    def test_count_identities_and_lead_histogram_checked(self):
        for key in ('events', 'alerts', 'matches', 'misses'):
            broken = counts(3, 2, 1)
            broken[key] += 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize(broken)
        result = counts(4, 4)
        result['lead_histogram'] = {'1': 2, '2': 2}
        self.assertEqual(summarize(result)['lead_median']['value'], 1.5)

    def test_shared_draws_deterministic_all_groups_support_and_linear_quantiles(self):
        a, b = bootstrap(grouped(), GROUPS), bootstrap(grouped(), GROUPS)
        self.assertEqual(a, b)
        self.assertEqual(a['replicates'], 1000)
        self.assertEqual(a['seed'], 1729)
        self.assertEqual(a['sampled_groups_per_replicate'], 200)
        strata = a['strata']
        candidate = next(row for row in strata if row['sign'] == 'improvement' and row['method'] == 'candidate')
        baseline = next(row for row in strata if row['sign'] == 'improvement' and row['method'] == 'baseline')
        self.assertEqual(candidate['recall'], baseline['recall'])
        self.assertEqual(candidate['recall']['ci95'], [1.0, 1.0])
        self.assertGreaterEqual(candidate['recall']['finite_replicates'], 950)
        self.assertEqual(candidate['recall']['event_groups'], 5)
        self.assertEqual(candidate['recall']['denominator_groups'], 5)

    def test_low_support_and_no_denominator_not_invented(self):
        result = bootstrap(grouped(4), GROUPS)['strata'][0]
        self.assertIsNone(result['recall']['ci95'])
        self.assertEqual(result['recall']['reason'], 'insufficient_event_support')
        data = grouped()
        for row in data:
            row.update(counts(1, 0))
        result = bootstrap(data, GROUPS)['strata'][0]
        self.assertEqual(result['false_alert_share']['finite_replicates'], 0)
        self.assertEqual(result['false_alert_share']['undefined_replicates'], 1000)
        self.assertEqual(result['false_alert_share']['reason'], 'insufficient_denominator_groups')

    def test_undefined_replicates_not_redrawn_even_with_support(self):
        data = grouped()
        draws = [[199] * 200 for _ in range(51)] + [[0, 1, 2, 3, 4] * 40 for _ in range(949)]
        from unittest.mock import patch
        with patch('scripts.trajectory_metrics.group_draws', return_value=draws):
            result = bootstrap(data, GROUPS)['strata'][0]['recall']
        self.assertEqual(result['finite_replicates'], 949)
        self.assertIsNone(result['ci95'])
        self.assertEqual(result['reason'], 'fewer_than_950_finite_replicates')

    def test_bootstrap_rejects_duplicate_groups_and_inconsistent_common_events(self):
        with self.assertRaises(ValueError):
            bootstrap(grouped(), GROUPS[:-1])
        data = copy.deepcopy(grouped())
        data[0].update(counts(2, 1))
        with self.assertRaisesRegex(ValueError, 'Common event'):
            bootstrap(data, GROUPS)


if __name__ == '__main__':
    unittest.main()
