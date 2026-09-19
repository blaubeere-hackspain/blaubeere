import copy
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import replay_model as replay
from scripts.export_assessments import SOURCE, verified_sources
from scripts.model_features import FEATURE_NAMES, SOURCE_COLUMNS
from tests.test_model_features import panel_rows

ML_AVAILABLE = importlib.util.find_spec('sklearn') is not None


def record(company='c1', eligible=True):
    return {'company_id': company, 'group_id': 'g1', 'as_of': replay.AS_OF,
            'origin_month': str(replay.ORIGIN), 'horizon_months': 3,
            'target': 'target_deficit_3m', 'p_proxy': 0.5 if eligible else None,
            'eligible': eligible, 'accepted': True, 'model_version': 'fixture',
            'coverage': {'valid_months_6m': 0, 'eligibility_reasons': [] if eligible else ['unknown_fx']},
            'unavailable_reasons': [] if eligible else ['unknown_fx'],
            'features': dict.fromkeys(FEATURE_NAMES),
            'missing_reason': dict.fromkeys(FEATURE_NAMES, 'unknown_source_value')}


class ReplayComparisonTests(unittest.TestCase):
    def setUp(self):
        self.records = [record(), record('c2', False)]

    def compare(self, changed):
        return replay.compare_records(self.records, changed, list(FEATURE_NAMES))

    def test_exact_and_company_order_independent(self):
        result = self.compare(list(reversed(self.records)))
        self.assertTrue(result['metadata_features_exact'])
        self.assertTrue(result['probabilities_exact'])
        self.assertEqual(result['max_probability_abs_error'], 0)
        self.assertEqual((result['companies'], result['estimates'], result['abstentions']), (2, 1, 1))

    def test_fixed_probability_tolerance_and_max_error(self):
        changed = copy.deepcopy(self.records)
        changed[0]['p_proxy'] += 5e-13
        result = self.compare(changed)
        self.assertFalse(result['probabilities_exact'])
        self.assertEqual(result['max_probability_abs_error'], abs(changed[0]['p_proxy'] - 0.5))
        changed[0]['p_proxy'] += 2e-12
        with self.assertRaisesRegex(ValueError, 'Probability mismatch'):
            self.compare(changed)

    def test_probability_unknown_bool_nonfinite_and_bounds_rejected(self):
        for value in (None, True, float('nan'), float('inf'), -0.1, 1.1):
            with self.subTest(value=value):
                changed = copy.deepcopy(self.records)
                changed[0]['p_proxy'] = value
                with self.assertRaises(ValueError):
                    self.compare(changed)
        changed = copy.deepcopy(self.records)
        changed[1]['p_proxy'] = 0.
        with self.assertRaises(ValueError):
            self.compare(changed)

    def test_every_metadata_field_is_exact(self):
        for key, value in {'group_id': 'other', 'as_of': '2026-07-31', 'origin_month': '2026-07-01',
                           'horizon_months': 4, 'target': 'other', 'model_version': 'other',
                           'coverage': {'valid_months_6m': None}, 'accepted': False,
                           'eligible': False, 'unavailable_reasons': ['unexpected'],
                           'extra_metadata': None}.items():
            with self.subTest(key=key):
                changed = copy.deepcopy(self.records)
                changed[0][key] = value
                with self.assertRaises(ValueError):
                    self.compare(changed)

    def test_unknown_features_cannot_be_zero_and_reasons_are_exact(self):
        for key in ('features', 'missing_reason'):
            changed = copy.deepcopy(self.records)
            changed[0][key][FEATURE_NAMES[0]] = 0. if key == 'features' else 'other'
            with self.assertRaises(ValueError):
                self.compare(changed)
        changed = copy.deepcopy(self.records)
        changed[0]['features'][FEATURE_NAMES[0]] = 0.
        del changed[0]['missing_reason'][FEATURE_NAMES[0]]
        with self.assertRaisesRegex(ValueError, 'Metadata/features'):
            self.compare(changed)

    def test_numeric_feature_difference_has_no_tolerance(self):
        self.records[0]['features'][FEATURE_NAMES[0]] = 1.
        del self.records[0]['missing_reason'][FEATURE_NAMES[0]]
        changed = copy.deepcopy(self.records)
        changed[0]['features'][FEATURE_NAMES[0]] += 1e-14
        with self.assertRaisesRegex(ValueError, 'Metadata/features'):
            self.compare(changed)

    def test_keys_duplicates_order_and_missing_reasons(self):
        for changed in (self.records[:1], self.records + [self.records[0]],
                        [record('other'), self.records[1]]):
            with self.assertRaises(ValueError):
                self.compare(changed)
        for field in ('features', 'missing_reason'):
            changed = copy.deepcopy(self.records)
            del changed[0][field][FEATURE_NAMES[0]]
            with self.assertRaises(ValueError):
                self.compare(changed)
        for order in (list(reversed(FEATURE_NAMES)), list(FEATURE_NAMES) + [FEATURE_NAMES[0]]):
            with self.assertRaisesRegex(ValueError, 'Feature order'):
                replay.compare_records(self.records, self.records, order)

    def test_metadata_bool_is_not_integer_or_unknown(self):
        for value in (False, None):
            changed = copy.deepcopy(self.records)
            changed[0]['coverage']['valid_months_6m'] = value
            with self.assertRaises(ValueError):
                self.compare(changed)

    def test_missing_current_month_retained_as_abstention(self):
        rows = panel_rows() + panel_rows(company='missing', months=23)
        features = replay.latest_features(rows)
        self.assertEqual([row['company_id'] for row in features], ['c1', 'missing'])
        self.assertTrue(features[0]['eligible'])
        missing = features[1]
        self.assertFalse(missing['eligible'])
        self.assertEqual(missing['coverage']['eligibility_reasons'], ['missing_current_month'])
        self.assertTrue(all(value is None for value in missing['features'].values()))
        self.assertEqual(set(missing['missing_reason'].values()), {'missing_current_month'})

    def test_panel_duplicate_and_group_drift_rejected(self):
        rows = panel_rows()
        with self.assertRaises(ValueError):
            replay.latest_features(rows + [rows[0]])
        rows[0]['group_id'] = 'other'
        with self.assertRaises(ValueError):
            replay.latest_features(rows)

    def test_bound_panel_hashes_and_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'data/runs/fixture'
            panel = run / replay.PANEL_PATH
            panel.parent.mkdir(parents=True)
            panel.write_bytes(b'fixture panel')
            run_manifest = {'run_id': 'fixture', 'outputs': {replay.PANEL_PATH: replay.digest(panel.read_bytes())}}
            manifest_file = run / 'manifest.json'
            manifest_file.write_text(json.dumps(run_manifest))
            manifest = {'input_binding': {'run_id': 'fixture', 'run_path': str(run),
                        'run_manifest_sha256': replay.digest(manifest_file.read_bytes()),
                        'input_sha256': run_manifest['outputs']}}
            self.assertEqual(replay.bound_panel(manifest, root), panel)
            for file in (panel, manifest_file):
                original = file.read_bytes()
                file.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'Bound hash mismatch'):
                    replay.bound_panel(manifest, root)
                file.write_bytes(original)
            panel.unlink()
            external = root / 'external'
            external.write_bytes(b'fixture panel')
            panel.symlink_to(external)
            with self.assertRaisesRegex(ValueError, 'escaped'):
                replay.bound_panel(manifest, root)
            manifest['input_binding']['run_path'] = str(root)
            with self.assertRaisesRegex(ValueError, 'outside'):
                replay.bound_panel(manifest, root)

    def test_tampered_sealed_predictions_rejected_before_panel_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'latest_predictions.json').write_bytes((SOURCE / 'latest_predictions.json').read_bytes() + b' ')
            with (patch.object(replay, 'verified_sources', side_effect=lambda: verified_sources(folder)),
                  patch.object(replay, 'bound_panel') as panel):
                with self.assertRaisesRegex(ValueError, 'Sealed hash mismatch'):
                    replay.replay()
                panel.assert_not_called()


@unittest.skipUnless(ML_AVAILABLE, 'Optional model dependencies missing: install requirements-model.txt (sklearn)')
class ReplayModelTests(unittest.TestCase):
    def test_external_pickle_rejected_before_deserialization(self):
        from scripts.model_training_io import verify_local_model
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.model_training_io.joblib.load') as load:
            with self.assertRaisesRegex(ValueError, 'no external pickle'):
                verify_local_model(Path(tmp), 'a' * 64)
            load.assert_not_called()

    def test_real_replay_only_projects_panel_never_fits_or_reads_labels(self):
        import duckdb
        from scripts import model_training_io as io
        original_connect = duckdb.connect
        queries = []

        class PanelOnly:
            def __enter__(self):
                self.con = original_connect(config={'threads': 2})
                return self

            def execute(self, query, parameters):
                self_outer.assertEqual(query, 'SELECT ' + ','.join(SOURCE_COLUMNS) +
                                       ' FROM read_parquet(?) ORDER BY company_id,month')
                self_outer.assertEqual(Path(parameters[0]).name, 'panel_flujos.parquet')
                queries.append(query)
                return self.con.execute(query, parameters)

            def __exit__(self, *args):
                self.con.close()

        self_outer = self
        forbidden = ('scripts.model_training_io.verify_experiment', 'scripts.model_training_io.verify_gate',
                     'scripts.model_inference.infer_latest', 'scripts.model_experiment.develop',
                     'scripts.model_experiment.fit_final', 'scripts.model_experiment.evaluate_final',
                     'scripts.model_protocol.load_development_labels', 'scripts.model_experiment.load_development_labels',
                     'xray.marts.targets.targets_available_at',
                     'sklearn.ensemble.HistGradientBoostingClassifier.fit')
        with ExitStack() as stack:
            for name in forbidden:
                stack.enter_context(patch(name, side_effect=AssertionError('Forbidden replay operation')))
            stack.enter_context(patch.object(duckdb, 'connect', side_effect=lambda **kwargs: PanelOnly()))
            loader = stack.enter_context(patch.object(io, 'verify_local_model', wraps=io.verify_local_model))
            result = replay.replay()
            loader.assert_called_once_with(io.EXPERIMENT_DIR, result['model_manifest_sha256'])
        self.assertEqual(len(queries), 1)
        self.assertEqual((result['companies'], result['estimates'], result['abstentions']), (1286, 1010, 276))
        self.assertTrue(result['metadata_features_exact'])
        self.assertLessEqual(result['max_probability_abs_error'], 1e-12)
        self.assertFalse(result['training_performed'])
        self.assertFalse(result['evaluation_performed'])


if __name__ == '__main__':
    unittest.main()
