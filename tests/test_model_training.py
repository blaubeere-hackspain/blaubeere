import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.model_features import FEATURE_NAMES
from scripts.model_protocol import support_gate, write_json

ML_AVAILABLE = importlib.util.find_spec('sklearn') is not None
if ML_AVAILABLE:
    import numpy as np
    from scripts import model_experiment as experiment
    from scripts.model_learning import candidate_model, metrics, select_candidate, grouped_bootstrap
    from scripts.model_training_io import (
        assert_pair, check_hashes, matrix, observed_sample, reserve_evaluation,
        verify_contracts, verify_local_model,
    )


@unittest.skipUnless(ML_AVAILABLE, 'Optional model dependencies missing: install requirements-model.txt (sklearn)')
class ModelTrainingTests(unittest.TestCase):
    def test_transforms_fit_training_only_and_keep_empty(self):
        x = np.array([[0., np.nan], [2., np.nan], [4., np.nan], [6., np.nan]])
        model = candidate_model({'id': 'logistic_c0.1', 'family': 'logistic', 'C': 0.1}, 1729)
        model.fit(x, [0, 0, 1, 1])
        imputer = model.named_steps['imputer']
        self.assertEqual(imputer.statistics_.tolist(), [3., 0.])
        before = model.named_steps['scaler'].mean_.copy()
        p = model.predict_proba([[10000., 17.]])
        np.testing.assert_array_equal(before, model.named_steps['scaler'].mean_)
        self.assertEqual(imputer.transform(x).shape, (4, 3))
        self.assertTrue(np.isfinite(p).all())

    def test_hgb_has_no_random_early_stopping(self):
        model = candidate_model({'id': 'hgb_leaf7', 'family': 'hgb', 'max_leaf_nodes': 7}, 1729)
        self.assertFalse(model.early_stopping)
        self.assertEqual(model.max_iter, 150)
        self.assertEqual(model.max_depth, 3)

    def test_tied_average_precision_is_not_eligible(self):
        result = metrics([0, 0, 1, 1], [0.5] * 4, 0.5)
        self.assertEqual(result['average_precision'], 0.5)
        self.assertEqual(result['brier'], 0.25)
        self.assertFalse(result['passed'])

    def test_selection_requires_each_fold_and_ties_prefer_logistic(self):
        good = {'passed': True, 'brier': 0.1}
        bad = {'passed': False, 'brier': 0.001}
        candidates = [
            {'id': 'hgb_leaf7', 'family': 'hgb', 'folds': [good, good]},
            {'id': 'logistic_c1', 'family': 'logistic', 'folds': [good, good]},
            {'id': 'logistic_c0.1', 'family': 'logistic', 'folds': [good, bad]},
        ]
        self.assertEqual(select_candidate(candidates), 'logistic_c1')
        self.assertIsNone(select_candidate([candidates[-1]]))

    def test_unknown_labels_are_not_zero_and_support_blocks(self):
        labels = [{'company_id': str(i), 'group_id': str(i % 10), 'month': date(2025, 1, 1),
                   'value': None if i < 40 else i % 2} for i in range(60)]
        features = [{**row, 'features': dict.fromkeys(FEATURE_NAMES, 1.)} for row in labels]
        support = support_gate(labels)
        self.assertFalse(support['passed'])
        self.assertEqual(support['censored'], 40)
        rows, x, y = observed_sample(labels, features)
        self.assertEqual(len(rows), 20)
        self.assertEqual(int(y.sum()), 10)
        self.assertEqual(x.shape, (20, 32))

    def test_matrix_rejects_changed_feature_order_and_infinite_values(self):
        rows = [{'features': dict.fromkeys(FEATURE_NAMES, 1.)}]
        with self.assertRaises(ValueError):
            matrix(rows, list(reversed(FEATURE_NAMES)))
        rows[0]['features'][FEATURE_NAMES[0]] = float('inf')
        with self.assertRaises(ValueError):
            matrix(rows, list(FEATURE_NAMES))

    def test_contract_change_is_rejected(self):
        from scripts.model_features import feature_contract
        from scripts.model_protocol import protocol_contract
        frozen = json.loads(json.dumps({'feature_contract': feature_contract(), 'protocol': protocol_contract()}))
        verify_contracts(frozen)
        frozen['protocol']['splits']['fold1_train'] = {'before': '2099-01-01'}
        with self.assertRaises(ValueError):
            verify_contracts(frozen)

    def test_split_overlap_and_maturity_purge_rejected(self):
        train = [{'company_id': 'a', 'group_id': 'a', 'month': date(2025, 3, 1),
                  'label_available_at': date(2025, 6, 30)}]
        valid = [{'company_id': 'b', 'group_id': 'b', 'month': date(2025, 7, 1),
                  'label_available_at': date(2025, 10, 31)}]
        assert_pair(train, valid)
        with self.assertRaises(ValueError):
            assert_pair(train, train)
        train[0]['label_available_at'] = date(2025, 7, 31)
        with self.assertRaises(ValueError):
            assert_pair(train, valid)

    def test_changed_artifact_and_path_escape_rejected(self):
        from scripts.model_protocol import sha256_file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'artifact.json', {'a': 1})
            hashes = {'artifact.json': sha256_file(root / 'artifact.json')}
            check_hashes(root, hashes)
            with (root / 'artifact.json').open('a') as stream:
                stream.write(' ')
            with self.assertRaises(ValueError):
                check_hashes(root, hashes)
            with self.assertRaises(ValueError):
                check_hashes(root, {'../escape': 'bad'})

    def test_guard_rejects_before_fit_and_second_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises((ValueError, FileNotFoundError)):
                reserve_evaluation(root, {'model.joblib': 'absent'})
            self.assertFalse((root / 'evaluation_receipt.json').exists())
            write_json(root / 'model.json', {'fitted': True})
            from scripts.model_protocol import sha256_file
            hashes = {'model.json': sha256_file(root / 'model.json')}
            reserve_evaluation(root, hashes)
            with self.assertRaises(FileExistsError):
                reserve_evaluation(root, hashes)

    def test_final_evaluation_checks_guard_before_query(self):
        with patch.object(experiment, 'verify_experiment', side_effect=ValueError('not fitted')):
            with patch.object(experiment.duckdb, 'connect') as connect:
                with self.assertRaises(ValueError):
                    experiment.evaluate_final()
                connect.assert_not_called()

    def test_development_has_no_final_loader(self):
        import inspect
        source = inspect.getsource(experiment.develop)
        self.assertNotIn('evaluate_final', source)
        self.assertNotIn('final_test', source)
        self.assertNotIn('TARGET_PATH', source)

    def test_local_model_verification_before_deserialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('scripts.model_training_io.joblib.load') as load:
                with self.assertRaises(ValueError):
                    verify_local_model(Path(tmp), 'a' * 64)
                load.assert_not_called()

    def test_gate_rejects_stale_sources_before_any_label_query(self):
        from scripts import model_training_io as io
        stale = {'audit_exit_code': 0, 'blockers': [],
                 'gate_status': 'pass_for_approved_retrospective_scope', 'current_source_hashes': {}}
        with (patch.object(io, 'read_json', return_value=stale),
              patch.object(io, 'current_source_hashes', return_value={'script.py': 'changed'}),
              patch.object(io, 'bind_inputs') as bind):
            with self.assertRaisesRegex(ValueError, 'stale'):
                io.verify_gate()
            bind.assert_not_called()

    def test_aggregate_coverage_retains_censored_cohorts(self):
        from scripts.model_learning import aggregate_diagnostics
        observed = {'company_id': 'a', 'group_id': 'g', 'coverage': {'valid_months_6m': 6},
                    'features': dict.fromkeys(FEATURE_NAMES, 1.)}
        censored = {'company_id': 'b', 'group_id': 'h', 'coverage': {'valid_months_6m': 3},
                    'features': dict.fromkeys(FEATURE_NAMES)}
        report = aggregate_diagnostics([observed], [1], [0.7], 0.5, [observed, censored])
        cohorts = {item['value']: item for item in report['history_cohort']}
        self.assertEqual(cohorts['3to5_valid_months']['label_observation_fraction'], 0.)
        self.assertIsNone(cohorts['3to5_valid_months']['brier'])
        self.assertAlmostEqual(report['group_id_macro_brier'], 0.09)

    def test_inference_retains_ineligible_and_rejected_rows(self):
        from scripts.model_inference import prediction_records
        records = [{'company_id': str(i), 'group_id': 'g', 'known_as_of': date(2026, 8, 31),
                    'month': date(2026, 8, 1), 'eligible': i == 0,
                    'coverage': {'eligibility_reasons': [] if i == 0 else ['unknown_fx']},
                    'features': dict.fromkeys(FEATURE_NAMES, 1.), 'missing_reason': {}} for i in range(2)]
        model = Mock()
        model.predict_proba.return_value = np.array([[0.3, 0.7]])
        predictions = prediction_records(records, model, 'toy-v1', True)
        self.assertEqual(predictions[0]['p_proxy'], 0.7)
        self.assertIsNone(predictions[1]['p_proxy'])
        self.assertIn('unknown_fx', predictions[1]['unavailable_reasons'])
        self.assertEqual(predictions, prediction_records(records, model, 'toy-v1', True))
        rejected = prediction_records(records, model, 'toy-v1', False)
        self.assertTrue(all(row['p_proxy'] is None for row in rejected))
        self.assertFalse(rejected[0]['accepted'])

    def test_final_sql_is_scoped_and_consumed_before_query(self):
        from contextlib import ExitStack
        import duckdb
        from scripts import model_training_io as io
        from scripts.model_protocol import assign_groups, prepare, read_prepared
        from tests.test_model_protocol import ModelProtocolTest
        fixture = ModelProtocolTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        groups = [g for g, partition in assign_groups([f'g{i}' for i in range(10)]).items()
                  if partition == 'final_test']
        with duckdb.connect() as con:
            con.execute('''CREATE TABLE labels AS SELECT
                'c'||i::VARCHAR AS company_id, 'g'||i::VARCHAR AS group_id,
                m::DATE AS month, last_day(m + INTERVAL 3 MONTH) AS available_at_deficit,
                CASE WHEN month=DATE '2026-04-01' THEN NULL ELSE true END AS target_deficit_3m,
                CASE WHEN group_id IN (SELECT unnest(?)) AND month BETWEEN DATE '2026-04-01'
                AND DATE '2026-05-01' THEN 1 ELSE 999 END AS target_version
                FROM range(10) t(i), generate_series(DATE '2024-09-01', DATE '2026-08-01', INTERVAL 1 MONTH) d(m)''', [groups])
            con.execute('COPY labels TO ? (FORMAT PARQUET)', [str(fixture.run / 'data/marts/targets_proxy.parquet')])
        fixture.bind_manifest()
        prepare(fixture.root, fixture.out)
        folder = fixture.root / 'experiment'
        folder.mkdir()
        manifest = {'status': 'fitted_before_holdout', 'artifacts_sha256': {}, 'train_prevalence': 0.5,
                    'model_version': 'toy-v1'}
        write_json(folder / 'model_manifest.json', manifest)
        write_json(folder / 'model_manifest.json.integrity.json', {})
        model = Mock()
        model.predict_proba.side_effect = lambda x: np.tile([0.5, 0.5], (len(x), 1))
        original_connect = duckdb.connect

        def guarded_connect(*args, **kwargs):
            self.assertTrue((folder / 'evaluation_receipt.json').is_file())
            return original_connect(*args, **kwargs)

        with ExitStack() as stack:
            for module in (io, experiment):
                stack.enter_context(patch.object(module, 'EXPERIMENT_DIR', folder))
                stack.enter_context(patch.object(module, 'PROTOCOL_DIR', fixture.out))
            stack.enter_context(patch.object(experiment, 'verify_experiment', return_value=(io.configuration(), read_prepared(fixture.out))))
            stack.enter_context(patch.object(experiment, 'verify_sealed', return_value=manifest))
            stack.enter_context(patch.object(experiment, 'verify_local_model', return_value=(model, manifest)))
            stack.enter_context(patch.object(experiment.duckdb, 'connect', side_effect=guarded_connect))
            result = experiment.evaluate_final()
            self.assertEqual(result['status'], 'blocked_support')
            self.assertGreater(result['support']['censored'], 0)
            self.assertNotIn('metrics', result)
            with self.assertRaises(FileExistsError):
                experiment.evaluate_final()

    def test_verified_local_roundtrip_and_tamper_rejected(self):
        import joblib
        from scripts import model_training_io as io
        from scripts.model_protocol import sha256_file
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            model = candidate_model({'family': 'logistic', 'C': 0.1}, 1729)
            x = np.array([[0.] * 32, [1.] * 32, [2.] * 32, [3.] * 32])
            model.fit(x, [0, 0, 1, 1])
            with (folder / 'model.joblib').open('xb') as stream:
                joblib.dump(model, stream)
            write_json(folder / 'model_manifest.json', {
                'feature_order': list(FEATURE_NAMES), 'runtime': io.configuration()['runtime'],
                'artifacts_sha256': {'model.joblib': sha256_file(folder / 'model.joblib')},
                'source_sha256': {}})
            expected = sha256_file(folder / 'model_manifest.json')
            prepared = folder / 'prepared'
            prepared.mkdir()
            write_json(prepared / 'prepared.json', {'artifacts_sha256': {}})
            from scripts.model_features import feature_contract
            from scripts.model_protocol import protocol_contract
            write_json(prepared / 'feature_contract.json', feature_contract())
            write_json(prepared / 'protocol.json', protocol_contract())
            with (patch.object(io, 'EXPERIMENT_DIR', folder), patch.object(io, 'PROTOCOL_DIR', prepared),
                  patch.object(io, 'PREPARED_SHA256', sha256_file(prepared / 'prepared.json'))):
                loaded, _ = io.verify_local_model(folder, expected)
                np.testing.assert_array_equal(model.predict_proba(x), loaded.predict_proba(x))
                with (folder / 'model.joblib').open('ab') as stream:
                    stream.write(b'tampered')
                with patch.object(io.joblib, 'load') as loader:
                    with self.assertRaises(ValueError):
                        io.verify_local_model(folder, expected)
                    loader.assert_not_called()

    def test_deterministic_predictions_and_group_bootstrap(self):
        x = np.arange(80, dtype=float).reshape(40, 2)
        y = np.array([0, 1] * 20)
        config = {'id': 'logistic_c0.1', 'family': 'logistic', 'C': 0.1}
        a, b = candidate_model(config, 1729), candidate_model(config, 1729)
        a.fit(x, y)
        b.fit(x, y)
        p = a.predict_proba(x)[:, 1]
        np.testing.assert_array_equal(p, b.predict_proba(x)[:, 1])
        groups = [str(i // 4) for i in range(40)]
        ci = grouped_bootstrap(y, p, groups, 0.5, 1729, 20)
        self.assertEqual(ci, grouped_bootstrap(y, p, groups, 0.5, 1729, 20))
        self.assertEqual(ci['sampling_unit'], 'group_id_with_replacement_all_rows')


if __name__ == '__main__':
    unittest.main()
