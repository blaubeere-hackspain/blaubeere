from dataclasses import replace
import unittest

from scripts.financial_v3_cash import month_shift
from scripts.financial_v3_evaluate import (
    ForecastOrigin, issue_signals, match_alerts, probability_metrics, classification_metrics,
    grouped_uncertainty, q1_reference_status, evaluate_development,
)
from scripts.financial_v3_events import scan_events
from tests.test_financial_v3_events import SCOPE, economic, sequence


def origin(i, probability=0.7, headline=85.0, eligible=True, sign='deterioration'):
    month = month_shift('2025-01-01', i)
    return ForecastOrigin('invented-company', 'invented-group', month, sign, probability,
                          month_shift(month, 1) + 'T00:00:00Z', headline, eligible, 'invented-method')


class SignalTests(unittest.TestCase):
    def test_watch_confirm_not_backdated_and_no_reissue(self):
        timeline = issue_signals([origin(i) for i in range(6)], scope=SCOPE)
        self.assertEqual([s['status'] for s in timeline], ['watch', 'confirmed', 'suppressed', 'suppressed', 'suppressed', 'suppressed'])
        alert = timeline[1]
        self.assertEqual(alert['first_signal_at'], '2025-02-01T00:00:00Z')
        self.assertEqual(alert['confirmed_alert_at'], '2025-03-01T00:00:00Z')
        self.assertEqual(alert['month'], '2025-02-01')

    def test_gaps_break_streak_but_do_not_reset_episode(self):
        signals = issue_signals([origin(0), origin(2), origin(3), origin(5), origin(6)], scope=SCOPE)
        self.assertEqual([s['status'] for s in signals], ['watch', 'watch', 'confirmed', 'suppressed', 'suppressed'])
        signals = issue_signals([origin(0), origin(1), origin(2, 0.2), origin(3, 0.2), origin(4), origin(5)], scope=SCOPE)
        self.assertEqual(sum(s['status'] == 'confirmed' for s in signals), 2)
        signals = issue_signals([origin(0), origin(1, headline=None), origin(2), origin(3)], scope=SCOPE)
        self.assertEqual([s['status'] for s in signals], ['watch', 'unavailable', 'watch', 'confirmed'])

    def test_chronology_and_probability_validation(self):
        for rows in [[origin(1), origin(0)], [origin(0), origin(0)], [origin(0, 1.1)],
                     [replace(origin(0), issued_at='2025-01-15T00:00:00Z')]]:
            with self.assertRaises(ValueError):
                issue_signals(rows, scope=SCOPE)


class EvaluationTests(unittest.TestCase):
    def test_matching_denominator_misses_and_false_censored_identities(self):
        scan = scan_events(sequence(['2000'] * 3 + ['-1000'] * 4 + ['2000'] * 2 + ['-1000'] * 2 + ['2000']), scope=SCOPE)
        origins = [origin(i) for i in range(12)]
        alerts = [s for s in issue_signals(origins, scope=SCOPE) if s['status'] == 'confirmed']
        report = match_alerts(alerts, scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['events'], 2)
        self.assertEqual(report['matches'], 1)
        self.assertEqual(report['misses'], 1)
        self.assertEqual(report['alerts'], report['matches'] + report['false'] + report['censored'])
        self.assertEqual(report['lead_months'], [2])
        self.assertEqual(report['burden'], 1 / 12)
        none = match_alerts([], scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(none['events'], report['events'])
        self.assertEqual(none['misses'], 2)
        late_origins = [origin(i, probability=None if i < 8 else 0.7) for i in range(12)]
        late_alerts = [s for s in issue_signals(late_origins, scope=SCOPE) if s['status'] == 'confirmed']
        self.assertEqual([s['month'] for s in late_alerts], ['2025-10-01'])
        late_report = match_alerts(late_alerts, scan, late_origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(late_report['events'], report['events'])
        self.assertEqual(late_report['opportunities'], report['opportunities'])
        self.assertEqual(late_report['censored'], 1)
        self.assertEqual(late_report['matches'], 0)
        self.assertEqual(late_report['nonpositive_late_alerts'], 1)

    def test_complete_followup_false_gap_is_censored_and_one_to_one(self):
        rows = sequence(['2000'] * 12)
        origins = [origin(i, probability=0.7 if i in (2, 3) else None) for i in range(12)]
        alerts = [s for s in issue_signals(origins, scope=SCOPE) if s['status'] == 'confirmed']
        self.assertEqual([a['month'] for a in alerts], ['2025-04-01'])
        alert = alerts[0]
        scan = scan_events(rows, scope=SCOPE)
        report = match_alerts([alert], scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['false'], 1)
        self.assertEqual(report['opportunities'], 12)
        rows.pop(5)
        report = match_alerts([alert], scan_events(rows, scope=SCOPE), origins,
                              as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['censored'], 1)
        with self.assertRaises(ValueError):
            match_alerts([alert, alert], scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)

    def test_actual_late_issuance_not_credited_by_old_origin(self):
        scan = scan_events(sequence(['2000'] * 3 + ['-1000'] * 4), scope=SCOPE)
        rows = [origin(0), replace(origin(1), issued_at='2025-05-01T00:00:00Z')]
        alerts = [s for s in issue_signals(rows, scope=SCOPE) if s['status'] == 'confirmed']
        report = match_alerts(alerts, scan, rows, as_at='2025-09-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['matches'], 0)

    def test_probability_metrics_constant_reference_calibration_support(self):
        report = probability_metrics([0, 1, 1, None], [0.2, 0.2, 0.2, None], ['g1', 'g1', 'g2', 'g2'])
        self.assertAlmostEqual(report['ap'], 2 / 3)
        self.assertAlmostEqual(report['constant_ap_reference'], 2 / 3)
        self.assertAlmostEqual(report['brier'], (0.04 + 0.64 + 0.64) / 3)
        self.assertEqual(report['unknown_labels'], 1)
        self.assertEqual(len(report['calibration']), 10)
        self.assertFalse(report['support_sufficient'])
        self.assertFalse(report['predictive_success'])
        self.assertIsNone(probability_metrics([0, 0], [0.1, 0.2], ['g1', 'g2'])['ap'])

    def test_conditional_true_matrix_unknown_and_missing_class(self):
        p = [{'recovered': 0.7, 'persistent': 0.2, 'mixed': 0.1},
             {'recovered': 0.2, 'persistent': 0.7, 'mixed': 0.1},
             {'recovered': 0.4, 'persistent': 0.4, 'mixed': 0.2}, None]
        report = classification_metrics(['persistent', 'persistent', 'mixed', None], p, ['a', 'b', 'a', 'c'])
        self.assertEqual(report['matrix']['persistent']['recovered'], 1)
        self.assertEqual(report['matrix']['persistent']['persistent'], 1)
        self.assertEqual(report['matrix']['mixed']['abstain'], 1)
        self.assertEqual(report['unknown_labels'], 1)
        self.assertEqual(report['per_class']['recovered']['support'], 0)
        self.assertIsNone(report['per_class']['recovered']['recall'])
        self.assertIsNone(report['balanced_accuracy'])
        self.assertFalse(report['support_sufficient'])

    def test_grouped_uncertainty_shared_fixed_draws(self):
        values = {'invented-group': (0.2, 0.4)}
        a = grouped_uncertainty(values, scope=SCOPE, test_only_replicates=30)
        b = grouped_uncertainty(values, scope=SCOPE, test_only_replicates=30)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a['paired_improvement_interval'][0], 0.2)
        self.assertFalse(a['support_sufficient'])
        self.assertTrue(a['test_only'])
        full = grouped_uncertainty(values, scope=SCOPE)
        self.assertEqual(full['replicates'], 2000)
        self.assertEqual(full['seed'], 1729)
        self.assertFalse(full['support_sufficient'])

    def test_q1_blocker_and_report_no_success_claim(self):
        self.assertIsNone(q1_reference_status()['weighted_kappa'])
        self.assertIn('independent_level_reference_unavailable', q1_reference_status()['blockers'])
        scan = scan_events(sequence(['2000'] * 3 + ['-1000'] * 5), scope=SCOPE)
        result = evaluate_development([origin(i) for i in range(8)], scan, as_at='2025-09-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(set(result['directions']), {'improvement', 'deterioration'})
        self.assertFalse(result['predictive_success'])
        self.assertFalse(result['automatic_alerts_enabled'])
        self.assertIn('still_strong_unvalidated', result)


class ExtendedEvaluationTests(unittest.TestCase):
    def test_forged_skipped_watch_or_repeated_episode_rejected(self):
        scan = scan_events(sequence(['2000'] * 12), scope=SCOPE)
        origins = [origin(i) for i in range(12)]
        alert = issue_signals(origins, scope=SCOPE)[1]
        forged = {**alert, 'month': '2025-04-01', 'confirmed_alert_at': origins[3].issued_at,
                  'issued_at': origins[3].issued_at, 'first_signal_at': origins[2].issued_at}
        with self.assertRaises(ValueError):
            match_alerts([alert, forged], scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        with self.assertRaises(ValueError):
            match_alerts([alert], scan, origins[1:], as_at='2026-01-01T00:00:00Z', scope=SCOPE)

    def test_one_to_one_two_valid_episodes_same_event(self):
        scan = scan_events([economic(-1)] + sequence(['2000'] * 7 + ['-1000'] * 5), scope=SCOPE)
        origins = [origin(i, 0.2 if i in (2, 3) else 0.7) for i in range(12)]
        alerts = [r for r in issue_signals(origins, scope=SCOPE) if r['status'] == 'confirmed']
        report = match_alerts(alerts, scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['matches'], 1)
        self.assertEqual(report['false'], 1)
        self.assertEqual(report['events'], 1)

    def test_missing_december_history_censors_earlier_alert(self):
        scan = scan_events(sequence(['2000'] * 7 + ['-1000'] * 5), scope=SCOPE)
        origins = [origin(i, 0.2 if i in (2, 3) else 0.7) for i in range(12)]
        alerts = [r for r in issue_signals(origins, scope=SCOPE) if r['status'] == 'confirmed']
        report = match_alerts(alerts, scan, origins, as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(report['events'], 1)
        self.assertEqual(report['matches'], 1)
        self.assertEqual(report['false'], 0)
        self.assertEqual(report['censored'], 1)
        self.assertEqual(report['censored_alerts'][0]['month'], '2025-02-01')
        self.assertEqual(report['alerts'], report['matches'] + report['false'] + report['censored'])

    def test_probabilities_and_prespecified_thirds_in_report(self):
        scan = scan_events(sequence(['2000'] * 3 + ['-1000'] * 9), scope=SCOPE)
        thirds = (('2025-01-01', '2025-04-01'), ('2025-05-01', '2025-08-01'), ('2025-09-01', '2025-12-01'))
        result = evaluate_development([origin(i) for i in range(12)], scan, as_at='2026-01-01T00:00:00Z', scope=SCOPE,
                                      prespecified_thirds=thirds)
        report = result['directions']['deterioration']
        self.assertIn('probability_metrics', report)
        self.assertEqual(len(result['temporal_thirds']), 3)
        self.assertFalse(any(t['utility_pass'] for t in result['temporal_thirds']))
        self.assertEqual(report['events'], report['matches'] + report['misses'])
        self.assertIn('mature_ascertainment', report)

    def test_conditional_forecast_matrix_attached_only_when_dated(self):
        from scripts.financial_v3_evaluate import ConditionalForecast
        scan = scan_events(sequence(['2000'] * 3 + ['-1000'] * 2 + ['2000'] * 4), scope=SCOPE)
        event = scan.events[0]
        forecast = ConditionalForecast(event.event_id, '2025-06-01T00:00:00Z',
                                       {'recovered': 0.7, 'persistent': 0.2, 'mixed': 0.1})
        result = evaluate_development([origin(i) for i in range(9)], scan, as_at='2025-10-01T00:00:00Z', scope=SCOPE,
                                      conditional_forecasts=(forecast,))
        self.assertEqual(result['q4']['matrix']['recovered']['recovered'], 1)
        self.assertFalse(result['q4']['advance_detection'])
        with self.assertRaises(ValueError):
            evaluate_development([origin(i) for i in range(9)], scan, as_at='2025-10-01T00:00:00Z', scope=SCOPE,
                                 conditional_forecasts=(replace(forecast, issued_at='2025-05-15T00:00:00Z'),))


if __name__ == '__main__':
    unittest.main()
