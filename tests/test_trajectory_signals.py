import copy
import hashlib
import json
import math
from datetime import date
from pathlib import Path
import unittest

from scripts import trajectory_contract as tc
from scripts.model_features import last_day, shift_month
from scripts.trajectory_signals import alerts_from_trajectory, build_trajectory


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = tc.read_json(ROOT / tc.PROTOCOL)
FIRST = date(2024, 9, 1)
GROUPS = {'TOY_A': 'TOY_GROUP'}
FROZEN_HASHES = {}


def frozen_hashes():
    paths = list((ROOT / 'scripts').glob('model_*.py')) + list((ROOT / 'tests').glob('test_model_*.py'))
    return {str(path.relative_to(ROOT)): tc.digest(path) for path in paths}


def setUpModule():
    FROZEN_HASHES.update(frozen_hashes())


def tearDownModule():
    if FROZEN_HASHES != frozen_hashes():
        raise AssertionError('Frozen model source/test byte hashes changed')
    fixture = json.dumps(toy_series([i / 16 for i in range(24)]), sort_keys=True, allow_nan=False).encode()
    print('\nTOY ONLY provenance ' + json.dumps({
        'fixture_sha256': hashlib.sha256(fixture).hexdigest(),
        'signals_source_sha256': tc.digest(ROOT / 'scripts/trajectory_signals.py'),
        'signals_tests_sha256': tc.digest(Path(__file__)),
        'protocol_sha256': tc.digest(ROOT / tc.PROTOCOL),
        'frozen_model_files_unchanged': len(FROZEN_HASHES),
        'real_data_or_outcomes_read': 0,
    }, sort_keys=True))


def toy_row(index, lower=0.0, upper=None, trim=None, gross=1.0, **changes):
    month = shift_month(FIRST, index)
    upper = lower if upper is None else upper
    trim = (lower, upper) if trim is None else trim
    row = {
        'company_id': 'TOY_A', 'group_id': 'TOY_GROUP',
        'month': month.isoformat(), 'available_at': last_day(month).isoformat(),
        'tiene_actividad_caja': True, 'n_sin_eur': 0,
        'volumen_caja_conocido_eur': gross,
        'operativo_min_eur': lower * gross, 'operativo_max_eur': upper * gross,
        'operativo_min_sin_atipicos_eur': trim[0] * gross,
        'operativo_max_sin_atipicos_eur': trim[1] * gross,
        'cobros_operativos_conocido_eur': 0.5 * gross,
        'pagos_operativos_conocido_eur': 0.25 * gross,
        'ambiguo_entradas_conocido_eur': 0.0, 'ambiguo_salidas_conocido_eur': 0.0,
    }
    row.update(changes)
    return row


def toy_series(values):
    return [toy_row(index, value) for index, value in enumerate(values)]


def chronological_mean(values):
    total = 0.0
    for value in values:
        total += value
    return total / 3.0


class TrajectorySignalsTests(unittest.TestCase):
    def build(self, rows, cutoff=None, groups=GROUPS):
        return build_trajectory(rows, PROTOCOL, cutoff=cutoff, company_groups=groups)

    def current(self, rows, index=5):
        return self.build(rows, last_day(shift_month(FIRST, index)))[-1]

    def assert_finite_json(self, value):
        json.dumps(value, allow_nan=False)

    def test_canonical_contract_and_fixture_only_provenance(self):
        self.assertEqual(tc.digest(ROOT / tc.PROTOCOL), 'd37ee0cad9101b4e6ae1dbc39f69d471ff7457b2cf3c5dac8ac5bd786c70159d')
        self.assertTrue(FROZEN_HASHES)
        self.assertEqual(PROTOCOL['source']['calendar']['months_per_company'], 24)

    def test_all_calendar_rows_and_metadata_only_companies_retained(self):
        groups = {**GROUPS, 'TOY_B': 'TOY_OTHER'}
        result = self.build([toy_row(5)], groups=groups)
        self.assertEqual(len(result), 48)
        for company in groups:
            rows = [row for row in result if row['company_id'] == company]
            self.assertEqual(len(rows), 24)
            self.assertEqual(rows[0]['month'], '2024-09-01')
            self.assertEqual(rows[-1]['as_of'], '2026-08-31')
        self.assertEqual(sum(row['monthly_input_eligible'] for row in result), 1)
        self.assertEqual(sum(row['direction_input_eligible'] for row in result), 0)
        self.assert_finite_json(result)

    def test_company_universe_can_come_from_rows(self):
        result = self.build([toy_row(0)], groups=None)
        self.assertEqual(len(result), 24)
        self.assertEqual(self.build([], groups=None), [])
        missing_group = toy_row(0)
        del missing_group['group_id']
        self.assertEqual(self.build([missing_group])[0]['group_id'], 'TOY_GROUP')
        with self.assertRaisesRegex(ValueError, 'group'):
            self.build([missing_group], groups=None)

    def test_missing_group_without_metadata_is_rejected_in_either_order(self):
        known, missing = toy_row(0), toy_row(1)
        del missing['group_id']
        for rows in ([known, missing], [missing, known]):
            with self.subTest(order=[row['month'] for row in rows]), self.assertRaisesRegex(ValueError, 'group'):
                self.build(rows, groups=None)

    def test_closed_month_cutoffs(self):
        rows = toy_series([0.0] * 24)
        self.assertEqual(self.build(rows, '2024-09-29'), [])
        self.assertEqual(len(self.build(rows, '2025-02-27')), 5)
        self.assertEqual(len(self.build(rows, '2025-02-28')), 6)
        self.assertEqual(len(self.build(rows, '2030-01-01')), 24)

    def test_future_append_mutate_delete_does_not_change_past(self):
        rows = toy_series([index / 16 for index in range(24)])
        prefix = self.build(rows[:9], '2025-05-31')
        self.assertEqual(prefix, self.build(rows)[:9])
        changed = copy.deepcopy(rows)
        for row in changed[9:]:
            row['operativo_min_eur'] = -999999.0
            row['operativo_max_eur'] = math.nan
            row['cobros_operativos_conocido_eur'] = math.inf
        self.assertEqual(prefix, self.build(changed)[:9])
        self.assertEqual(prefix, self.build(rows[:9])[:9])

    def test_inputs_and_protocol_are_not_mutated_and_order_is_irrelevant(self):
        rows = toy_series([index / 16 for index in range(24)])
        original, protocol = copy.deepcopy(rows), copy.deepcopy(PROTOCOL)
        result = self.build(iter(reversed(rows)))
        self.assertEqual(result, self.build(rows))
        self.assertEqual(rows, original)
        self.assertEqual(PROTOCOL, protocol)

    def test_late_availability_excluded_then_eligible_only_as_of_later_origin(self):
        rows = toy_series([0.0, 0.0, 0.0, 0.25, 0.25, 0.25, 0.5])
        rows[2]['available_at'] = '2025-03-01'
        early = self.current(rows)
        self.assertFalse(early['direction_input_eligible'])
        self.assertIn('unavailable_as_of', early['direction_reasons'])
        hidden = early['evidence']['prior']['inputs'][2]
        self.assertIsNone(hidden['gross_eur'])
        self.assertIsNone(hidden['source_ref'])
        self.assertIsNone(hidden['base']['lower_eur'])
        later = self.current(rows, 6)
        self.assertTrue(later['direction_input_eligible'])
        self.assertEqual(self.build(rows)[2]['state_reasons'], ['unavailable_as_of'])
        for window in ('prior', 'recent'):
            for item in later['evidence'][window]['inputs']:
                if item['source_ref']:
                    self.assertLessEqual(item['source_ref']['available_at'], later['as_of'])

    def test_missing_availability_never_assumed(self):
        row = toy_row(0)
        del row['available_at']
        current = self.current([row], 0)
        self.assertFalse(current['monthly_input_eligible'])
        self.assertIn('unknown_availability', current['state_reasons'])
        self.assertIsNone(current['evidence']['monthly']['gross_eur'])

    def test_missing_month_is_inserted_not_compressed(self):
        rows = [toy_row(index, index / 16) for index in (0, 1, 2, 4, 5, 6)]
        result = self.build(rows)
        self.assertEqual(result[3]['state_reasons'], ['missing_month'])
        self.assertFalse(result[6]['direction_input_eligible'])
        self.assertEqual(result[6]['coverage']['valid_months_total'], 5)
        self.assertEqual(result[6]['evidence']['prior']['months'], ['2024-10-01', '2024-11-01', '2024-12-01'])
        self.assertIn('missing_month', result[6]['direction_reasons'])

    def test_short_history_is_unknown_not_stable(self):
        row = self.current(toy_series([0.0] * 5), 4)
        self.assertEqual(row['direction'], 'insufficient_evidence')
        self.assertIn('outside_calendar', row['direction_reasons'])
        self.assertEqual(row['coverage']['valid_months_total'], 5)

    def test_duplicate_month_identity_and_group_conflicts_rejected(self):
        for rows, groups in [([toy_row(0), toy_row(0)], GROUPS),
                             ([toy_row(0), toy_row(1, group_id='OTHER')], None),
                             ([toy_row(0, company_id='UNLISTED')], GROUPS),
                             ([toy_row(0, company_id=1)], None),
                             ([toy_row(0, group_id='')], None)]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.build(rows, groups=groups)

    def test_month_grain_and_unexpected_keys_rejected(self):
        for changes in ({'month': '2024-09-02'}, {'month': '2024-08-01'},
                        {'target_deficit_3m': 1.0}, {'future_observable': True},
                        {'available_at': 'not-a-date'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.build([toy_row(0, **changes)])

    def test_invalid_inputs_never_filled_with_zero(self):
        changes = [({'n_sin_eur': 1}, 'unknown_fx'),
                   ({'n_sin_eur': None}, 'unknown_fx'),
                   ({'n_sin_eur': False}, 'unknown_fx'),
                   ({'tiene_actividad_caja': False}, 'no_cash_activity'),
                   ({'volumen_caja_conocido_eur': 0.0}, 'nonpositive_or_unknown_gross'),
                   ({'volumen_caja_conocido_eur': -1.0}, 'nonpositive_or_unknown_gross'),
                   ({'volumen_caja_conocido_eur': math.inf}, 'nonpositive_or_unknown_gross'),
                   ({'operativo_min_eur': math.nan}, 'invalid_base_bounds'),
                   ({'operativo_max_sin_atipicos_eur': None}, 'invalid_trim_bounds'),
                   ({'operativo_min_eur': 1.0, 'operativo_max_eur': -1.0}, 'unordered_base_bounds'),
                   ({'operativo_min_sin_atipicos_eur': 1.0}, 'unordered_trim_bounds')]
        for change, reason in changes:
            with self.subTest(change=change):
                rows = toy_series([0.0] * 6)
                rows[-1].update(change)
                row = self.current(rows)
                self.assertFalse(row['monthly_input_eligible'])
                self.assertFalse(row['direction_input_eligible'])
                self.assertEqual(row['operating_state'], 'insufficient_evidence')
                self.assertIn(reason, row['state_reasons'])
                self.assertIsNone(row['evidence']['delta_intervals']['base']['lower'])
                self.assert_finite_json(row)

    def test_cent_rounding_half_away_for_state_only(self):
        for value, state in [(-0.005, 'deficit'), (-0.0049, 'non_deficit'), (0.005, 'non_deficit')]:
            with self.subTest(value=value):
                row = self.current([toy_row(0, value)], 0)
                self.assertEqual(row['operating_state'], state)
                self.assertEqual(row['evidence']['monthly']['base']['lower_ratio'], value)

    def test_ambiguous_state_and_trim_disagreement(self):
        for source, reason in [(toy_row(0, -0.2, 0.2), 'ambiguous_state'),
                               (toy_row(0, -0.2, trim=(0.2, 0.2)), 'base_trim_state_disagreement')]:
            row = self.current([source], 0)
            self.assertTrue(row['monthly_input_eligible'])
            self.assertEqual(row['operating_state'], 'insufficient_evidence')
            self.assertIn(reason, row['state_reasons'])

    def test_state_and_direction_are_separate_in_both_signs(self):
        improving = self.current(toy_series([-0.75] * 3 + [-0.25] * 3))
        deteriorating = self.current(toy_series([0.75] * 3 + [0.25] * 3))
        self.assertEqual((improving['operating_state'], improving['direction']), ('deficit', 'improving'))
        self.assertEqual((deteriorating['operating_state'], deteriorating['direction']), ('non_deficit', 'deteriorating'))

    def test_direction_does_not_require_robust_monthly_states(self):
        rows = [toy_row(index, -0.01, 0.01) if index < 3 else toy_row(index, 0.25, 0.3) for index in range(6)]
        result = self.build(rows)
        self.assertEqual(result[0]['operating_state'], 'insufficient_evidence')
        self.assertEqual(result[5]['direction'], 'improving')
        self.assertTrue(result[5]['direction_input_eligible'])

    def test_threshold_equality_and_nextafter_follow_actual_binary_arithmetic(self):
        for sign in (1, -1):
            for prior_value in (math.nextafter(0.075, -math.inf), 0.075, math.nextafter(0.075, math.inf)):
                values = [sign * prior_value] * 3 + [sign * 0.125] * 3
                actual = chronological_mean(values[3:]) - chronological_mean(values[:3])
                if prior_value == 0.075:
                    self.assertEqual(actual, sign * 0.05)
                expected = 'improving' if actual >= 0.05 else 'deteriorating' if actual <= -0.05 else 'stable'
                with self.subTest(sign=sign, prior=prior_value, actual=actual):
                    row = self.current(toy_series(values))
                    self.assertEqual(row['direction'], expected)
                    self.assertEqual(row['evidence']['delta_intervals']['base']['lower'], actual)
        for value in (math.nextafter(0.05, -math.inf), 0.05, math.nextafter(0.05, math.inf)):
            actual = chronological_mean([value] * 3)
            row = self.current(toy_series([0.0] * 3 + [value] * 3))
            self.assertEqual(row['direction'], 'improving' if actual >= 0.05 else 'stable')
            self.assertEqual(row['evidence']['delta_intervals']['base']['lower'], actual)

    def test_chronological_additions_not_compensated_sum(self):
        values = [1e16, 1.0, -1e16, 0.0, 0.0, 0.0]
        row = self.current(toy_series(values))
        self.assertEqual(row['evidence']['prior']['base']['lower_ratio'], 0.0)
        self.assertEqual(row['direction'], 'stable')

    def test_interval_overlap_and_base_trim_disagreement_are_not_stable(self):
        wide = [toy_row(index, -0.1, 0.1) for index in range(6)]
        disagree = [toy_row(index, 0.0 if index < 3 else 0.25, trim=(0.0, 0.0)) for index in range(6)]
        for rows in (wide, disagree):
            row = self.current(rows)
            self.assertTrue(row['direction_input_eligible'])
            self.assertEqual(row['direction'], 'insufficient_evidence')
            self.assertIn('ambiguous_direction', row['direction_reasons'])

    def test_stable_requires_strict_interior_for_entire_interval(self):
        rows = [toy_row(index, 0.075) if index < 3 else toy_row(index, 0.075, 0.125) for index in range(6)]
        row = self.current(rows)
        self.assertEqual(row['evidence']['delta_intervals']['base']['upper'], 0.05)
        self.assertEqual(row['direction'], 'insufficient_evidence')
        self.assertEqual(self.current(toy_series([0.125] * 6))['direction'], 'stable')

    def test_common_gross_unweighted_monthly_ratios_and_component_identity(self):
        grosses = [8.0, 16.0, 32.0, 64.0, 128.0, 256.0]
        ratios = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625]
        rows = [toy_row(index, value, trim=(value + 0.0625, value + 0.0625), gross=gross,
                        cobros_operativos_conocido_eur=(0.25 + index / 16) * gross,
                        pagos_operativos_conocido_eur=(0.5 - index / 32) * gross)
                for index, (value, gross) in enumerate(zip(ratios, grosses))]
        row = self.current(rows)
        evidence = row['evidence']
        self.assertEqual(evidence['prior']['base']['lower_ratio'], chronological_mean(ratios[:3]))
        self.assertNotEqual(evidence['prior']['base']['lower_ratio'], sum(r['operativo_min_eur'] for r in rows[:3]) / sum(grosses[:3]))
        self.assertEqual(evidence['recent']['trim']['lower_ratio'], chronological_mean([r + 0.0625 for r in ratios[3:]]))
        for source, entry in zip(rows, evidence['prior']['inputs'] + evidence['recent']['inputs']):
            self.assertEqual(entry['trim']['lower_ratio'], source['operativo_min_sin_atipicos_eur'] / source['volumen_caja_conocido_eur'])
        components = evidence['components']
        self.assertEqual(components['receipts_delta_ratio'], evidence['recent']['receipts_ratio'] - evidence['prior']['receipts_ratio'])
        self.assertEqual(components['payments_delta_ratio'], evidence['recent']['payments_ratio'] - evidence['prior']['payments_ratio'])
        self.assertEqual(components['identified_net_delta_ratio'], components['receipts_delta_ratio'] - components['payments_delta_ratio'])
        self.assertEqual(components['payment_contribution_ratio'], -components['payments_delta_ratio'])
        self.assertEqual(components['gross_delta_eur'], chronological_mean(grosses[3:]) - chronological_mean(grosses[:3]))
        self.assertEqual(len(evidence['source_refs']), 6)

    def test_missing_subtotal_keeps_bounds_direction_but_partial_explanation(self):
        rows = toy_series([0.0] * 3 + [0.25] * 3)
        del rows[0]['cobros_operativos_conocido_eur']
        rows[1]['pagos_operativos_conocido_eur'] = math.nan
        row = self.current(rows)
        self.assertEqual(row['direction'], 'improving')
        self.assertIsNone(row['evidence']['prior']['receipts_ratio'])
        self.assertIsNone(row['evidence']['prior']['payments_ratio'])
        self.assertIsNone(row['evidence']['components']['identified_net_delta_ratio'])
        self.assertIn('unknown_receipts', row['evidence']['explanation_reasons'])
        self.assertIn('unknown_payments', row['evidence']['explanation_reasons'])
        self.assert_finite_json(row)

    def test_finite_inputs_with_overflow_still_produce_finite_json(self):
        for rows in ([toy_row(index, 1e308) for index in range(6)],
                     [toy_row(index, operativo_min_eur=1e308, operativo_max_eur=1e308,
                              operativo_min_sin_atipicos_eur=1e308, operativo_max_sin_atipicos_eur=1e308,
                              volumen_caja_conocido_eur=1e-308) for index in range(6)]):
            row = self.current(rows)
            self.assertEqual(row['operating_state'], 'non_deficit')
            self.assertEqual(row['direction'], 'insufficient_evidence')
            self.assertIn('nonfinite_direction_arithmetic', row['direction_reasons'])
            self.assert_finite_json(row)

    def test_watch_confirm_one_alert_per_run_with_actual_dates(self):
        result = self.build(toy_series([index / 8 for index in range(24)]))
        first, second, last = result[5]['signal'], result[6]['signal'], result[-1]['signal']
        self.assertEqual(first['status'], 'watch')
        self.assertEqual(first['first_signal_at'], '2025-02-28')
        self.assertIsNone(first['confirmed_alert_at'])
        self.assertEqual(second['status'], 'confirmed')
        self.assertEqual(second['confirmed_alert_at'], '2025-03-31')
        self.assertEqual(last, second)
        alerts = alerts_from_trajectory(result)
        self.assertEqual(len(alerts), 2)
        candidate = alerts_from_trajectory(result, method='candidate')
        baseline = alerts_from_trajectory(result, method='baseline')
        self.assertEqual(len(candidate), 1)
        self.assertEqual(len(baseline), 1)
        self.assertEqual(candidate[0]['issued_at'], second['confirmed_alert_at'])
        self.assertEqual(baseline[0]['issued_at'], first['first_signal_at'])
        self.assertFalse(candidate[0]['comparison_only'])
        self.assertTrue(baseline[0]['comparison_only'])
        self.assertNotEqual(candidate[0]['alert_id'], baseline[0]['alert_id'])
        self.assertEqual(alerts, alerts_from_trajectory(self.build(toy_series([index / 8 for index in range(24)]))))

    def test_opposite_sign_starts_new_watch_immediately(self):
        result = self.build(toy_series([0.0] * 3 + [0.25] * 3 + [-1.0, -1.0]))
        self.assertEqual(result[5]['direction'], 'improving')
        self.assertEqual(result[6]['direction'], 'deteriorating')
        self.assertEqual(result[6]['signal']['status'], 'watch')
        self.assertEqual(result[6]['signal']['first_signal_at'], '2025-03-31')
        self.assertEqual(result[7]['signal']['confirmed_alert_at'], '2025-04-30')
        self.assertNotEqual(result[5]['signal']['episode_id'], result[6]['signal']['episode_id'])
        self.assertEqual(len(alerts_from_trajectory(result, 'baseline')), 2)
        self.assertEqual(len(alerts_from_trajectory(result, 'candidate')), 1)

    def test_stable_break_resets_run(self):
        result = self.build(toy_series([0.0] * 3 + [0.25] * 6 + [0.5, 0.5]))
        self.assertEqual(result[8]['direction'], 'stable')
        self.assertEqual(result[8]['signal']['status'], 'none')
        self.assertIsNone(result[8]['signal']['episode_id'])
        self.assertEqual(result[9]['signal']['status'], 'watch')
        self.assertEqual(result[10]['signal']['status'], 'confirmed')
        self.assertNotEqual(result[7]['signal']['episode_id'], result[9]['signal']['episode_id'])
        self.assertEqual(len(alerts_from_trajectory(result, 'candidate')), 2)

    def test_missing_and_ambiguous_breaks_reset_old_run(self):
        for missing in (True, False):
            rows = toy_series([index / 8 for index in range(24)])
            if missing:
                rows.pop(8)
            else:
                rows[8].update(operativo_min_eur=-100.0, operativo_max_eur=100.0)
            result = self.build(rows)
            self.assertEqual(result[8]['signal']['status'], 'insufficient_evidence')
            self.assertIsNone(result[8]['signal']['first_signal_at'])
            self.assertEqual(result[14]['signal']['status'], 'watch')
            self.assertEqual(result[15]['signal']['status'], 'confirmed')
            self.assertNotEqual(result[6]['signal']['episode_id'], result[15]['signal']['episode_id'])
            self.assertEqual(len(alerts_from_trajectory(result, 'candidate')), 2)

    def test_history_carries_across_nominal_evaluation_boundaries(self):
        result = self.build(toy_series([index / 8 for index in range(24)]))
        split = PROTOCOL['evaluation']['reserved']['alert_origin_first']
        reserved_rows = [row for row in result if row['month'] >= split]
        self.assertTrue(all(row['signal']['status'] == 'confirmed' for row in reserved_rows))
        self.assertEqual(alerts_from_trajectory(reserved_rows), [])
        self.assertEqual([alert for alert in alerts_from_trajectory(result) if alert['origin_month'] >= split], [])

    def test_company_episode_isolation_and_collision_safe_identifiers(self):
        rows = toy_series([index / 8 for index in range(8)])
        other = [dict(row, company_id='TOY_A|improvement', group_id='TOY_OTHER') for row in rows]
        result = self.build(rows + other, groups=None)
        alerts = alerts_from_trajectory(result)
        self.assertEqual(len(alerts), 4)
        self.assertEqual(len({row['alert_id'] for row in alerts}), 4)
        with self.assertRaises(ValueError):
            alerts_from_trajectory(result, 'unknown')
        with self.assertRaises(ValueError):
            alerts_from_trajectory(result + result[:1])

    def test_modified_protocol_rules_are_rejected_without_io(self):
        protocol = copy.deepcopy(PROTOCOL)
        protocol['direction']['threshold'] = 0.01
        with self.assertRaises(ValueError):
            build_trajectory([], protocol, company_groups=GROUPS)


if __name__ == '__main__':
    unittest.main()
