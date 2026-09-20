import ast
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts import model_features as mf
from scripts import trajectory_contract as tc
from scripts import trajectory_export as te
from scripts import trajectory_overlay as ov
from scripts import trajectory_signals as ts


PROTOCOL = tc.read_json(tc.ROOT / tc.PROTOCOL)


def toy_panel():
    rows = []
    for offset in range(24):
        month = mf.shift_month('2024-09-01', offset)
        row = dict.fromkeys(mf.SOURCE_COLUMNS, 0.0)
        row.update(company_id='TOY', group_id='GROUP', month=month, available_at=mf.last_day(month),
                   tiene_actividad_caja=True, es_calentamiento=False, n_sin_eur=0,
                   n_tx_caja=10, n_cuentas_activas=1, volumen_caja_conocido_eur=100.0,
                   operativo_min_eur=10.0, operativo_max_eur=20.0,
                   operativo_min_sin_atipicos_eur=10.0, operativo_max_sin_atipicos_eur=20.0,
                   cobros_operativos_conocido_eur=40.0, pagos_operativos_conocido_eur=25.0)
        rows.append(row)
    return rows


class FixedModel:
    def predict_proba(self, values):
        return np.tile([0.75, 0.25], (len(values), 1))


def toy_company(panel=None, assignment='train'):
    panel = toy_panel() if panel is None else panel
    rows = ts.build_trajectory([{key: row[key] for key in ts.INPUT_COLUMNS} for row in panel], PROTOCOL)
    probabilities = {}
    for offset in range(5):
        month = mf.shift_month('2026-04-01', offset)
        record = ov.monthly_records(panel, {'TOY': 'GROUP'}, month, FixedModel(), 'fixed', True)[0]
        probabilities['TOY', month.isoformat()] = ov.probability_point(month, record)
    points = [te.pack_point(row, probabilities) for row in rows]
    return te.company_record('TOY', 'GROUP', points, {'GROUP': assignment}, {})


class ExportUnitTests(unittest.TestCase):
    def test_typed_schema_and_unavailable_cash(self):
        company = toy_company()
        te.validate_company(company)
        schema = te.handoff_schema(te.CommonCompany)
        self.assertEqual(set(schema['required']), set(company))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(company['kind'], 'operating_trajectory')
        for key in ('opening_cash_cents', 'buffer_cents', 'health', 'predictive'):
            self.assertIsNone(company[key])
        for key in ('history', 'flows', 'drivers', 'coverage'):
            self.assertEqual(company[key], [])
        self.assertFalse(company['trajectory']['backtest_summary']['automatic_predictive_alerts_enabled'])

    def test_every_company_keeps_all_months_and_gaps(self):
        panel = toy_panel()
        panel.pop(18)
        company = toy_company(panel)
        te.validate_company(company)
        points = company['trajectory']['points']
        self.assertEqual(len(points), 24)
        self.assertEqual(points[18]['month'], '2026-03-01')
        self.assertIsNone(points[18]['evidence']['monthly']['gross_eur'])
        self.assertIn('missing_month', points[18]['state_reasons'])
        self.assertIsNone(points[19]['probability']['p_proxy'])
        self.assertIn('missing_month', points[19]['probability']['unavailable_reasons'])

    def test_cutoff_gating_and_calendar_horizon(self):
        company = toy_company()
        points = company['trajectory']['points']
        for point in points[:19]:
            self.assertIsNone(point['probability']['p_proxy'])
            self.assertIsNone(point['probability']['eligible'])
            self.assertEqual(point['probability']['unavailable_reasons'], ['before_training_cutoff'])
        self.assertEqual(points[19]['as_of'], '2026-04-30')
        self.assertEqual(points[19]['probability']['p_proxy'], 0.25)
        self.assertEqual(points[-1]['probability']['horizon_start'], '2026-09-01')
        self.assertEqual(points[-1]['probability']['horizon_end'], '2026-11-30')

    def test_late_and_unknown_availability_never_leaks(self):
        for unavailable in (None, date(2026, 5, 1)):
            with self.subTest(unavailable=unavailable):
                panel = toy_panel()
                panel[19]['available_at'] = unavailable
                company = toy_company(panel)
                te.validate_company(company)
                point = company['trajectory']['points'][19]
                reason = 'unknown_availability' if unavailable is None else 'unavailable_as_of'
                self.assertEqual(point['state_reasons'], [reason])
                self.assertIsNone(point['probability']['p_proxy'])
                self.assertEqual(point['probability']['unavailable_reasons'], [reason])
                self.assertIsNone(point['evidence']['monthly']['gross_eur'])

    def test_future_rows_do_not_change_probability_or_evidence(self):
        original = toy_panel()
        changed = copy.deepcopy(original)
        for row in changed[20:]:
            row['neto_caja_eur'] = 1e8
            row['operativo_min_eur'] = -1e8
            row['available_at'] = date(2026, 12, 31)
        left = ov.monthly_records(original, {'TOY': 'GROUP'}, date(2026, 4, 1), FixedModel(), 'fixed', True)
        right = ov.monthly_records(changed, {'TOY': 'GROUP'}, date(2026, 4, 1), FixedModel(), 'fixed', True)
        self.assertEqual(left, right)
        self.assertEqual(toy_company(original)['trajectory']['points'][19], toy_company(changed)['trajectory']['points'][19])

    def test_probability_is_not_direction_or_state(self):
        points = toy_company()['trajectory']['points']
        self.assertEqual(points[-1]['operating_state'], 'non_deficit')
        self.assertEqual(points[-1]['direction'], 'insufficient_evidence')
        self.assertEqual(points[-1]['probability']['p_proxy'], 0.25)
        self.assertFalse(points[-1]['review_action']['executable'])

    def test_nan_ids_dates_states_probability_ranges_are_rejected(self):
        original = toy_company()
        changes = [lambda c: c.update(id=''),
                   lambda c: c['trajectory']['points'].pop(),
                   lambda c: c['trajectory']['points'][0].update(operating_state='healthy'),
                   lambda c: c['trajectory']['points'][0].update(as_of='2024-10-31'),
                   lambda c: c['trajectory']['points'][-1]['probability'].update(p_proxy=float('nan')),
                   lambda c: c['trajectory']['points'][-1]['probability'].update(p_proxy=1.1),
                   lambda c: c['trajectory']['points'][0]['probability'].update(p_proxy=0.25),
                   lambda c: c['trajectory']['points'][-1]['signal'].update(confirmed_alert_at='2026-09-30'),
                   lambda c: c['trajectory']['points'][-1]['evidence'].update(source_refs=[['2026-08-01', '2026-09-01']])]
        for change in changes:
            with self.subTest(change=change):
                company = copy.deepcopy(original)
                change(company)
                with self.assertRaises(ValueError):
                    te.validate_company(company)

    def test_exact_and_fixed_absolute_tolerance_comparison(self):
        ov.assert_same({'p': 0.2, 'eligible': True, 'reason': None}, {'p': 0.2 + 5e-13, 'eligible': True, 'reason': None})
        for actual, expected in ((0.2, 0.2 + 2e-12), (0, None), (1, True), ({'a': 1}, {'b': 1}), (float('nan'), 0.1)):
            with self.subTest(actual=actual):
                with self.assertRaises(ValueError):
                    ov.assert_same(actual, expected)

    def test_full_sealed_cases_preserved_and_historical_visibility(self):
        reports, cases = te.read_sealed_summaries()
        assignment = tc.read_json(tc.ROOT / tc.GROUPS)
        selected = te.demo_cases(cases, assignment)
        self.assertEqual(len(selected), 3)
        for kind, case in cases.items():
            demo = selected[case['company_id']]
            self.assertEqual(demo['sealed_case'], case)
            self.assertEqual(demo['kind'], kind)
            before = date.fromisoformat(case['confirmed_at']) - timedelta(days=1)
            self.assertIsNone(te.visible_development_case(demo, before.isoformat()))
            self.assertEqual(te.visible_development_case(demo, case['confirmed_at']), demo)
            self.assertEqual(demo['proof']['rank'], 1)
            self.assertFalse(demo['proof']['selection_uses_alerts_or_probability'])
        self.assertEqual(cases['deterioration']['company_id'], 'COMP_0009')
        self.assertEqual(cases['deterioration']['matches_by_method'], {'baseline': None, 'candidate': None})
        self.assertIsNone(te.visible_development_case(None, '2026-08-31'))

    def test_excluded_group_can_have_probability_but_no_event_annotation(self):
        company = toy_company(assignment='final_test')
        te.validate_company(company)
        self.assertEqual(company['trajectory']['points'][-1]['probability']['p_proxy'], 0.25)
        self.assertIsNone(company['trajectory']['development_demo_case'])
        self.assertFalse(company['trajectory']['source_provenance']['event_evaluation_included'])
        _, cases = te.read_sealed_summaries()
        assignment = tc.read_json(tc.ROOT / tc.GROUPS)
        assignment[cases['deterioration']['group_id']] = 'final_test'
        with self.assertRaisesRegex(ValueError, 'Invalid sealed development selection'):
            te.demo_cases(cases, assignment)

    def test_sealed_reserved_censoring_not_negative_and_no_winner(self):
        reports, _ = te.read_sealed_summaries()
        candidate = {row['sign']: row for row in reports['reserved']['strata'] if row['method'] == 'candidate'}
        expected = {'improvement': (37, 75, 2, 35, 48, 25), 'deterioration': (41, 73, 1, 40, 50, 22)}
        for sign, counts in expected.items():
            row = candidate[sign]
            self.assertEqual(tuple(row[key] for key in ('events', 'alerts', 'matches', 'misses', 'false_alarms', 'censored_unknown')), counts)
            self.assertAlmostEqual(row['false_alert_share']['value'], row['false_alarms'] / (row['matches'] + row['false_alarms']))
        self.assertEqual(te.STATUS, 'anticipation_not_validated')
        self.assertEqual(te.MODE, 'descriptive_review_only')

    def test_hash_tamper_blocks_before_panel_or_inference(self):
        digest = tc.digest
        def tampered(path):
            return '0' * 64 if path == tc.ROOT / tc.PROTOCOL else digest(path)
        with patch.object(tc, 'digest', side_effect=tampered), patch.object(te, 'load_panel') as load:
            with self.assertRaisesRegex(ValueError, 'Protocol hash changed'):
                te.export()
            load.assert_not_called()
        with patch.object(tc, 'digest', return_value='0' * 64), patch.object(ov.mio, 'verify_local_model') as load_model:
            with self.assertRaisesRegex(ValueError, 'Sealed latest predictions changed'):
                ov.build_overlay([], {}, PROTOCOL)
            load_model.assert_not_called()

    def test_exclusive_idempotent_publish_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'assessment'
            files = {'companies.json': b'[]\n', 'manifest.json': b'{}\n'}
            with self.assertRaisesRegex(ValueError, 'Missing output'):
                te.publish(files, output, check=True)
            self.assertFalse(output.exists())
            te.publish(files, output)
            before = {name: (output / name).stat().st_mtime_ns for name in files}
            te.publish(files, output)
            te.publish(files, output, check=True)
            self.assertEqual(before, {name: (output / name).stat().st_mtime_ns for name in files})
            with self.assertRaisesRegex(ValueError, 'Immutable output differs'):
                te.publish({'companies.json': b'changed', 'new.json': b'no'}, output)
            self.assertEqual((output / 'companies.json').read_bytes(), b'[]\n')
            self.assertFalse((output / 'new.json').exists())

    def test_static_entry_points_never_fit_or_reexecute_backtest(self):
        forbidden = {'run_phase', 'freeze', 'evaluate_period', 'match_alerts', '_cases', 'bootstrap',
                     'infer_latest', 'verify_experiment', 'fit', 'fit_candidate', 'load_labels', 'metrics'}
        for name in ('scripts/trajectory_export.py', 'scripts/trajectory_overlay.py'):
            tree = ast.parse((tc.ROOT / name).read_text())
            calls = {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                     for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))}
            self.assertFalse(calls & forbidden)


class BoundIntegrationTests(unittest.TestCase):
    def test_real_fixed_model_per_month_august_parity_all_companies_no_targets(self):
        before = te.protected_snapshot(tc.ROOT)
        protocol, _, verification = te.verify_inputs()
        self.assertEqual(verification['phases'], {'development': 'complete', 'reserved': 'complete'})
        queries = []
        original_connection = te.tb.readonly_connection
        class ProjectedConnection:
            def __init__(self, connection):
                self.connection = connection
            def execute(self, sql, parameters):
                queries.append(sql)
                expected = 'SELECT ' + ','.join(f'"{name}"' for name in te.APPROVED_COLUMNS) + ' FROM read_parquet(?) ORDER BY company_id,month'
                if sql != expected:
                    raise AssertionError('Non-approved panel/target query')
                return self.connection.execute(sql, parameters)
        @contextmanager
        def guarded_connection():
            with original_connection() as connection:
                yield ProjectedConnection(connection)
        forbidden = ['scripts.model_learning.fit_candidate', 'scripts.model_protocol.load_development_labels',
                     'scripts.model_training_io.verify_experiment', 'scripts.model_inference.infer_latest',
                     'scripts.trajectory_backtest.run_phase', 'scripts.trajectory_events.evaluate_period',
                     'scripts.trajectory_events.match_alerts', 'scripts.trajectory_events._cases']
        with ExitStack() as stack:
            for target in forbidden:
                stack.enter_context(patch(target, side_effect=AssertionError(f'Forbidden call: {target}')))
            stack.enter_context(patch.object(te.tb, 'readonly_connection', side_effect=guarded_connection))
            inference = stack.enter_context(patch.object(ov.mi, 'prediction_records', wraps=ov.mi.prediction_records))
            loader = stack.enter_context(patch.object(ov.mio, 'verify_local_model', wraps=ov.mio.verify_local_model))
            panel = te.load_panel(protocol)
            groups = {row['company_id']: row['group_id'] for row in panel}
            probabilities, proof = ov.build_overlay(panel, groups, protocol)
            loader.assert_called_once_with(ov.mio.EXPERIMENT_DIR, protocol['v1_probability_overlay']['model_manifest_sha256'])
            self.assertEqual(inference.call_count, 5)
            self.assertEqual(len(probabilities), 1286 * 5)
            self.assertEqual([call.args[0][0]['month'].isoformat() for call in inference.call_args_list],
                             ['2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01'])
            for call in inference.call_args_list:
                features = call.args[0]
                self.assertEqual(len({r['company_id'] for r in features}), len(features))
            self.assertTrue(proof['august_matches_sealed_latest'])
            self.assertEqual(proof['monthly_counts']['2026-08-01']['estimates'], 1010)
            rows = ts.build_trajectory([{key: row[key] for key in ts.INPUT_COLUMNS} for row in panel], protocol)
            self.assertEqual(len(rows), 1286 * 24)
            assignment = tc.read_json(tc.ROOT / tc.GROUPS)
            _, cases = te.read_sealed_summaries()
            selected = te.demo_cases(cases, assignment)
            points = {company: [] for company in groups}
            for row in rows:
                points[row['company_id']].append(te.pack_point(row, probabilities))
            for company, group in groups.items():
                record = te.company_record(company, group, points[company], assignment, selected)
                te.validate_company(record)
                self.assertEqual(len(record['trajectory']['points']), 24)
                if assignment[group] == 'final_test':
                    self.assertIsNone(record['trajectory']['development_demo_case'])
        self.assertEqual(len(queries), 1)
        self.assertEqual(te.protected_snapshot(tc.ROOT), before)


if __name__ == '__main__':
    unittest.main()
