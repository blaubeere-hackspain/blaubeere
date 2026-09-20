import copy
import unittest
from datetime import date

from scripts import trajectory_contract as tc
from scripts.model_features import last_day, shift_month
from scripts.trajectory_signals import build_trajectory
from scripts.trajectory_events import evaluate_period, match_alerts, state_window


PROTOCOL = tc.read_json(tc.ROOT / tc.PROTOCOL)
ASSIGNMENT = {f'G{i:03}': 'train' if i < 150 else 'validation' if i < 200 else 'final_test' for i in range(250)}
FIRST = date(2024, 9, 1)


def history(states=None, eligible=True, company='A', group='G000', length=16):
    states = states or {}
    return [{'company_id': company, 'group_id': group, 'month': shift_month(FIRST, i).isoformat(),
             'as_of': last_day(shift_month(FIRST, i)).isoformat(), 'protocol_version': 'trajectory-v2',
             'operating_state': states.get(i, 'non_deficit'), 'state_reasons': [],
             'direction_input_eligible': eligible and i >= 5, 'direction_reasons': [] if i >= 5 else ['outside_calendar'],
             'signal': {'sign': None, 'status': 'none', 'first_signal_at': None, 'confirmed_alert_at': None, 'episode_id': None},
             'direction': 'stable', 'evidence': {'monthly': {'base': {}, 'trim': {}}}}
            for i in range(length)]


def issue(rows, i, sign='deterioration', first=None, candidate=True):
    row = rows[i]
    row['signal'] = {'sign': sign, 'status': 'confirmed' if candidate else 'watch',
                     'first_signal_at': rows[i if first is None else first]['as_of'],
                     'confirmed_alert_at': row['as_of'] if candidate else None,
                     'episode_id': f'{row["company_id"]}-{i}'}


def stratum(result, sign='deterioration', method='candidate'):
    return next(row for row in result['strata'] if row['sign'] == sign and row['method'] == method)


class TrajectoryEventsTests(unittest.TestCase):
    def evaluate(self, rows):
        return evaluate_period(rows, PROTOCOL, ASSIGNMENT, 'development')

    def test_exact_two_old_two_new_both_signs_and_finite_confirmation(self):
        result = self.evaluate(history({8: 'deficit', 9: 'deficit'}))
        events = result['events']
        self.assertEqual([(e['sign'], e['onset_month'], e['confirmed_at']) for e in events],
                         [('deterioration', '2025-05-01', '2025-06-30'), ('improvement', '2025-07-01', '2025-08-31')])
        self.assertTrue(all(e['eligible'] for e in events))
        self.assertEqual(stratum(result)['misses'], 1)
        self.assertEqual(stratum(result, 'improvement')['misses'], 1)

    def test_observed_nonevent_is_not_unknown_and_single_dip_is_demo_only(self):
        result = self.evaluate(history({8: 'deficit'}))
        self.assertEqual(result['events'], [])
        self.assertEqual(result['coverage']['event_windows_unknown'], 0)
        self.assertEqual(result['coverage']['event_windows_observed_nonevent'], 8)
        dip = result['cases']['recovered_dip']
        self.assertEqual(dip['status'], 'available')
        self.assertEqual(dip['onset_month'], '2025-05-01')
        self.assertEqual(dip['confirmed_at'], '2025-07-31')
        self.assertEqual(result['cases']['improvement']['status'], 'unavailable')

    def test_gap_never_compressed_or_counted_as_negative(self):
        rows = history({8: 'deficit', 9: 'deficit'})
        rows.pop(7)
        result = self.evaluate(rows)
        self.assertFalse(any(e['sign'] == 'deterioration' for e in result['events']))
        self.assertGreater(result['coverage']['event_windows_unknown'], 0)
        self.assertEqual(stratum(result)['misses'], 0)

    def test_common_denominator_independent_of_direction_or_issued_alert(self):
        rows = history({8: 'deficit', 9: 'deficit'})
        for row in rows:
            row['direction'] = 'insufficient_evidence'
        result = self.evaluate(rows)
        for method in ('candidate', 'baseline'):
            self.assertEqual(stratum(result, method=method)['events'], 1)
        for row in rows:
            row['direction_input_eligible'] = False
        result = self.evaluate(rows)
        self.assertEqual(stratum(result)['events'], 0)
        self.assertEqual(result['coverage']['event_exclusions_no_eligible_origin'], 2)
        self.assertTrue(all(case['status'] == 'unavailable' for case in result['cases'].values()))

    def test_matching_extra_wrong_sign_chronology_and_miss_identities(self):
        rows = history({8: 'deficit', 9: 'deficit'})
        issue(rows, 6)
        issue(rows, 7)
        issue(rows, 10, 'deterioration')
        result = self.evaluate(list(reversed(rows)))
        count = stratum(result)
        self.assertEqual([count[k] for k in ('events', 'alerts', 'matches', 'misses', 'false_alarms', 'censored_unknown')],
                         [1, 3, 1, 0, 2, 0])
        alerts = [a for a in result['alerts'] if a['method'] == 'candidate']
        self.assertEqual(alerts[0]['origin_month'], '2025-03-01')
        self.assertEqual(alerts[0]['lead_months'], 2)
        self.assertEqual(stratum(result, 'improvement')['misses'], 1)

    def test_full_followup_needed_only_for_unmatched_alert(self):
        rows = history({8: 'deficit', 9: 'deficit', 10: 'insufficient_evidence'})
        issue(rows, 7)
        issue(rows, 8)
        result = self.evaluate(rows)
        count = stratum(result)
        self.assertEqual((count['matches'], count['censored_unknown'], count['false_alarms']), (1, 1, 0))
        self.assertEqual(result['coverage']['alerts_incomplete_followup'], 4)
        matched = next(a for a in result['alerts'] if a['status'] == 'matched')
        self.assertFalse(matched['complete_followup'])

    def test_confirmation_cutoff_and_right_censoring(self):
        rows = history({14: 'deficit', 15: 'deficit'})
        issue(rows, 12)
        rows.pop()
        result = self.evaluate(rows)
        self.assertEqual(result['events'], [])
        self.assertEqual(stratum(result)['censored_unknown'], 1)
        self.assertGreater(result['coverage']['event_windows_unknown'], 0)

    def test_excluded_groups_removed_before_validation_and_case_selection(self):
        rows = history({8: 'deficit', 9: 'deficit'})
        excluded = history({7: 'deficit', 8: 'deficit'}, company='EXCLUDED', group='G200')
        result = self.evaluate(rows + excluded + excluded)
        self.assertEqual(result['coverage']['excluded_trajectory_rows'], 32)
        self.assertEqual(result['coverage']['companies'], 1)
        self.assertFalse(any(e['company_id'] == 'EXCLUDED' for e in result['events']))
        self.assertEqual(result['cases']['deterioration']['company_id'], 'A')
        with self.assertRaisesRegex(ValueError, 'Unknown group'):
            self.evaluate(history(group='not_assigned'))

    def test_duplicate_trajectory_and_alerts_rejected(self):
        rows = history()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.evaluate(rows + rows[:1])
        issue(rows, 6)
        result = self.evaluate(rows)
        alert = result['alerts'][0]
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            match_alerts([], [alert, dict(alert, alert_id='other')], rows, '2025-12-31', FIRST)

    def test_case_selection_earliest_confirmed_company_tie_not_match(self):
        a = history({8: 'deficit', 9: 'deficit'}, company='A')
        b = history({8: 'deficit', 9: 'deficit'}, company='B', group='G001')
        issue(b, 6)
        result = self.evaluate(b + a)
        case = result['cases']['deterioration']
        self.assertEqual(case['company_id'], 'A')
        self.assertEqual(case['selection_proof']['qualifying_patterns'], 2)
        self.assertEqual(case['selection_proof']['rank'], 1)
        self.assertEqual(len(case['timeline']), 4)
        self.assertIn('base', case['timeline'][0])

    def test_future_rows_fail_and_reserved_has_no_case_fallback(self):
        rows = history()
        future = copy.deepcopy(rows[-1])
        future.update(month='2026-01-01', as_of='2026-01-31')
        with self.assertRaisesRegex(ValueError, 'cutoff'):
            self.evaluate(rows + [future])
        result = evaluate_period(rows, PROTOCOL, ASSIGNMENT, 'reserved')
        self.assertIsNone(result['cases'])

    def test_reserved_last_onset_confirmation_and_common_three_origins(self):
        for sign in ('deterioration', 'improvement'):
            old, new = ('non_deficit', 'deficit') if sign == 'deterioration' else ('deficit', 'non_deficit')
            rows = history({i: old if i < 22 else new for i in range(24)}, length=24)
            issue(rows, 20, sign)
            result = evaluate_period(rows, PROTOCOL, ASSIGNMENT, 'reserved')
            self.assertIsNone(result['cases'])
            self.assertEqual(result['coverage']['origin_opportunities'], 3)
            self.assertEqual(result['coverage']['event_window_opportunities'], 4)
            event = result['events'][0]
            self.assertEqual((event['onset_month'], event['confirmed_at']), ('2026-07-01', '2026-08-31'))
            self.assertEqual(event['eligible_origins'], ['2026-05-01'])
            for method in ('candidate', 'baseline'):
                metric = stratum(result, sign, method)
                self.assertEqual((metric['events'], metric['matches'], metric['lead_histogram']['2']), (1, 1, 1))
                self.assertEqual(metric['coverage']['alerts_incomplete_followup'], 0)
            self.assertEqual(result['coverage']['event_windows_left_unknown'], 0)
            self.assertEqual(result['coverage']['event_windows_right_immature'], 0)

    def test_window_maturity_last_day_and_left_boundary(self):
        rows = history()
        index = {(r['company_id'], r['month']): r for r in rows}
        months = [date(2025, 5, 1), date(2025, 6, 1)]
        self.assertEqual(state_window(index, 'A', months, date(2025, 6, 29), FIRST)['reasons'], ['right_immature'])
        self.assertTrue(state_window(index, 'A', months, date(2025, 6, 30), FIRST)['complete'])
        self.assertEqual(state_window(index, 'A', [shift_month(FIRST, -1)], date(2025, 6, 30), FIRST)['reasons'],
                         ['left_history_unavailable'])

    def test_last_development_onset_sustained_but_dip_must_confirm_in_december(self):
        result = self.evaluate(history({14: 'deficit', 15: 'deficit'}))
        event = result['events'][0]
        self.assertEqual((event['onset_month'], event['confirmed_at']), ('2025-11-01', '2025-12-31'))
        self.assertEqual(event['eligible_origins'], ['2025-09-01'])
        self.assertEqual(self.evaluate(history({14: 'deficit'}))['cases']['recovered_dip']['status'], 'unavailable')
        outside = self.evaluate(history({6: 'deficit', 7: 'deficit'}))
        self.assertFalse(any(e['sign'] == 'deterioration' for e in outside['events']))

    def test_earliest_future_event_order_company_isolation_and_duplicates(self):
        rows = history({8: 'deficit', 9: 'deficit'})
        issue(rows, 6)
        result = self.evaluate(rows)
        event = next(e for e in result['events'] if e['sign'] == 'deterioration')
        earlier = dict(event, onset_month='2025-04-01', confirmed_at='2025-05-31', event_id='earlier')
        alert = next(a for a in result['alerts'] if a['method'] == 'candidate')
        matched = match_alerts([event, earlier], [alert], rows, '2025-12-31', FIRST)
        self.assertEqual(matched[0]['event_id'], 'earlier')
        self.assertEqual(matched[0]['lead_months'], 1)
        for duplicate in ([event, event], [event, dict(event, event_id='different')]):
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                match_alerts(duplicate, [alert], rows, '2025-12-31', FIRST)
        other = history(company='B', group='G001')
        issue(other, 6)
        isolated = self.evaluate(history({8: 'deficit', 9: 'deficit'}) + other)
        self.assertEqual(stratum(isolated)['matches'], 0)
        self.assertEqual(stratum(isolated)['false_alarms'], 1)

    def test_no_zero_negative_or_three_month_lead(self):
        for index in (5, 8, 9):
            rows = history({8: 'deficit', 9: 'deficit'})
            issue(rows, index)
            result = self.evaluate(rows)
            self.assertEqual(stratum(result)['matches'], 0)
            self.assertEqual(stratum(result)['misses'], 1)

    def test_full_history_signals_carry_into_period_before_extraction(self):
        inputs = []
        for i in range(16):
            month = shift_month(FIRST, i)
            inputs.append({'company_id': 'A', 'group_id': 'G000', 'month': month.isoformat(),
                           'available_at': last_day(month).isoformat(), 'tiene_actividad_caja': True,
                           'n_sin_eur': 0, 'volumen_caja_conocido_eur': 1.0,
                           **{k: i / 8 for k in ('operativo_min_eur', 'operativo_max_eur',
                                                'operativo_min_sin_atipicos_eur', 'operativo_max_sin_atipicos_eur')}})
        result = self.evaluate(build_trajectory(inputs, PROTOCOL, '2025-12-31'))
        self.assertEqual(stratum(result, 'improvement', 'candidate')['alerts'], 1)
        self.assertEqual(stratum(result, 'improvement', 'baseline')['alerts'], 0)
        self.assertEqual(result['coverage']['preperiod_crossing_episodes'], 1)
        self.assertEqual(result['alerts'][0]['first_signal_at'], '2025-02-28')


if __name__ == '__main__':
    unittest.main()
