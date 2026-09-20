from dataclasses import replace
import unittest
from unittest.mock import patch

from scripts.financial_v3_contract import POLICY_VERSION
from scripts.financial_v3_learning import (
    FeatureRow, TrainingRow, Fold, SCORE_FEATURES, TREND_FEATURES,
    feature_from_core, split_fold, fit_fixture_candidates, predict_fixture, select_candidate,
)
from scripts.financial_v3_events import FixtureScope
from tests.test_financial_v3_engine import fixture, evaluate


SCOPE = FixtureScope('invented-learning', tuple((f'invented-company-{i}', f'invented-group-{i}') for i in range(9)))


def features(i, month='2024-12-01', shift=0):
    values = {name: float(i + shift) / 10 for name in (*SCORE_FEATURES, *TREND_FEATURES)}
    return FeatureRow(f'invented-company-{i}', f'invented-group-{i}', month, values, 85.0,
                      month[:8] + '28T00:00:00Z', ('invented-feature-source',), 'neutral', POLICY_VERSION)


def training(conditional=False):
    labels = ['recovered', 'persistent', 'mixed'] if conditional else [0, 1]
    return [TrainingRow(features(i), labels[i % len(labels)], '2025-05-01T00:00:00Z') for i in range(6)]


def fold():
    return Fold('invented-fold', '2025-05-31T23:59:59Z', '2025-06-01', '2025-08-01',
                tuple(f'invented-group-{i}' for i in range(6)), tuple(f'invented-group-{i}' for i in range(6, 9)))


class LearningTests(unittest.TestCase):
    def test_core_feature_adapter_and_future_not_outcomes(self):
        scope = FixtureScope('invented-core-learning', (('invented-company', 'invented-group'),))
        data = fixture()
        row = feature_from_core(evaluate(data), previous=evaluate(data, month='2025-05-01'),
                                group_id='invented-group', primitive_state='neutral', state_month='2025-05-01',
                                state_known_on='2025-05-30T00:00:00Z', scope=scope)
        self.assertEqual(set(row.values), set(SCORE_FEATURES + TREND_FEATURES))
        self.assertTrue(row.source_refs)
        self.assertNotIn('label', row.values)
        data.records['balance_anchor'].clear()
        missing = feature_from_core(evaluate(data), previous=None, group_id='invented-group',
                                    primitive_state='neutral', state_month='2025-05-01',
                                    state_known_on='2025-05-30T00:00:00Z', scope=scope)
        self.assertIsNone(missing.headline)
        self.assertTrue(missing.reasons)

    def test_time_group_maturity_and_unknown_purge(self):
        rows = training()
        rows[0] = replace(rows[0], label=None, label_known_on=None)
        rows[1] = replace(rows[1], label_known_on='2025-06-01T00:00:00Z')
        test = TrainingRow(features(6, '2025-06-01'), 1, '2025-10-01T00:00:00Z')
        split = split_fold(rows + [test], fold(), target='deterioration', as_at='2025-10-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(len(split.train), 4)
        self.assertEqual(len(split.validation), 1)
        self.assertEqual(split.exclusions['unknown_label'], 1)
        self.assertEqual(split.exclusions['label_not_mature_at_training_cutoff'], 1)
        self.assertTrue(all(r.feature.group_id != test.feature.group_id for r in split.train))

    def test_invalid_features_and_folds_fail_before_fit(self):
        invalid = [replace(features(0), headline=None),
                   replace(features(0), known_on='2025-04-01T00:00:00Z'),
                   replace(features(0), values={**features(0).values, 'future_Q': 3}),
                   replace(features(0), policy_version='other'),
                   replace(features(0), month='2026-04-01'),
                   replace(features(0), group_id='final_test')]
        for bad in invalid:
            with self.assertRaises(ValueError):
                fit_fixture_candidates([TrainingRow(bad, 0, '2025-05-01T00:00:00Z')],
                                       target='improvement', train_cutoff=fold().train_cutoff, scope=SCOPE)
        with self.assertRaises(ValueError):
            split_fold(training(), replace(fold(), validation_groups=fold().train_groups),
                       target='deterioration', as_at='2025-12-01T00:00:00Z', scope=SCOPE)
        with self.assertRaises(ValueError):
            split_fold(training(), replace(fold(), train_cutoff='2025-07-01T00:00:00Z'),
                       target='deterioration', as_at='2025-12-01T00:00:00Z', scope=SCOPE)

    def test_exact_budget_reproducibility_train_only_scaling(self):
        models = fit_fixture_candidates(training(), target='deterioration', train_cutoff=fold().train_cutoff, scope=SCOPE)
        self.assertEqual(tuple(models.candidates), ('prevalence', 'persistence', 'trend', 'scorecard_logistic_c0.1', 'scorecard_logistic_c1'))
        self.assertTrue(models.mock_only)
        self.assertEqual(models.threads, 2)
        self.assertEqual(models.seed, 1729)
        model = models.candidates['scorecard_logistic_c1']
        means = model.named_steps['scale'].mean_.copy()
        prediction = predict_fixture(models, features(6, '2025-06-01', shift=900), scope=SCOPE)
        self.assertEqual(prediction['calibration'], 'mock_development_only')
        self.assertTrue(all(0 <= p <= 1 for p in prediction['probabilities'].values()))
        self.assertEqual(list(means), list(model.named_steps['scale'].mean_))
        again = fit_fixture_candidates(training(), target='deterioration', train_cutoff=fold().train_cutoff, scope=SCOPE)
        self.assertEqual(predict_fixture(again, features(6, '2025-06-01', shift=900), scope=SCOPE), prediction)
        with self.assertRaises(PermissionError):
            models.company_export()

    def test_no_automatic_fit_with_insufficient_support_and_absent_class(self):
        with patch('sklearn.linear_model.LogisticRegression.fit', side_effect=AssertionError('must not fit')):
            tiny = fit_fixture_candidates(training()[:2], target='deterioration', train_cutoff=fold().train_cutoff, scope=SCOPE)
            self.assertTrue(all(v is None for v in tiny.candidates.values()))
            missing = fit_fixture_candidates([r for r in training(True) if r.label != 'mixed'], target='conditional', train_cutoff=fold().train_cutoff, scope=SCOPE)
            self.assertTrue(all(v is None for v in missing.candidates.values()))
        models = fit_fixture_candidates(training(True), target='conditional', train_cutoff=fold().train_cutoff, scope=SCOPE)
        self.assertEqual(tuple(models.candidates), ('class_prevalence', 'primitive_persistence', 'conditional_logistic_c1'))
        result = predict_fixture(models, features(6, '2025-06-01'), scope=SCOPE)
        for probabilities in result['probabilities'].values():
            self.assertEqual(set(probabilities), {'recovered', 'persistent', 'mixed'})
            self.assertAlmostEqual(sum(probabilities.values()), 1)

    def test_prediction_missing_headline_is_null_never_imputed(self):
        models = fit_fixture_candidates(training(), target='improvement', train_cutoff=fold().train_cutoff, scope=SCOPE)
        result = predict_fixture(models, replace(features(6, '2025-06-01'), headline=None), scope=SCOPE)
        self.assertTrue(all(value is None for value in result['probabilities'].values()))
        with self.assertRaises(ValueError):
            predict_fixture(models, features(0), scope=SCOPE)

    def test_macro_group_selection_ties_and_budget(self):
        self.assertEqual(select_candidate({'prevalence': [0.2], 'scorecard_logistic_c1': [0.1999999]}, target='improvement'), 'prevalence')
        self.assertEqual(select_candidate({'scorecard_logistic_c0.1': [0.2], 'scorecard_logistic_c1': [0.2]}, target='improvement'), 'scorecard_logistic_c0.1')
        with self.assertRaises(ValueError):
            select_candidate({'hgb': [0.1]}, target='improvement')
        with self.assertRaises(ValueError):
            select_candidate({'prevalence': [0.1] * 4}, target='improvement')


class ExtendedLearningTests(unittest.TestCase):
    def test_development_fold_executes_bounded_candidates(self):
        from scripts.financial_v3_learning import develop_fixture_folds
        rows = training() + [TrainingRow(features(i, '2025-06-01'), i % 2, '2025-11-01T00:00:00Z') for i in range(6, 9)]
        result = develop_fixture_folds(rows, [fold()], target='improvement', as_at='2025-12-01T00:00:00Z', scope=SCOPE)
        self.assertIsNotNone(result['selected_candidate'])
        self.assertEqual(len(result['folds'][0]['candidates']), 5)
        self.assertFalse(result['company_model_export_allowed'])
        with self.assertRaises(ValueError):
            develop_fixture_folds(rows, [fold()] * 4, target='improvement', as_at='2025-12-01T00:00:00Z', scope=SCOPE)

    def test_precomputed_negative_cannot_claim_premature_label(self):
        rows = [TrainingRow(features(0, '2025-03-01'), 0, '2025-05-01T00:00:00Z')]
        with self.assertRaisesRegex(ValueError, 'maturity'):
            split_fold(rows, fold(), target='improvement', as_at='2025-12-01T00:00:00Z', scope=SCOPE)

    def test_state_provenance_explicit_and_future_state_forbidden(self):
        from tests.test_financial_v3_events import SCOPE as core_scope
        data = fixture(receipts=['1000'] * 8)
        args = dict(previous=evaluate(data), group_id='invented-group', primitive_state='neutral',
                    state_month='2025-06-01', state_known_on='2025-06-30T00:00:00Z', scope=core_scope,
                    state_source_refs=('invented-primitive-source',))
        row = feature_from_core(evaluate(data, month='2025-07-01'), **args)
        self.assertIn('invented-primitive-source', row.source_refs)
        self.assertFalse(row.reasons)
        with self.assertRaises(ValueError):
            feature_from_core(evaluate(data, month='2025-07-01'), **{**args, 'state_known_on': '2025-08-01T00:00:00Z'})

    def test_conditional_forecast_is_dated_at_confirmation_not_onset(self):
        from scripts.financial_v3_learning import conditional_fixture_forecast
        from scripts.financial_v3_events import scan_events
        from tests.test_financial_v3_events import economic
        models = fit_fixture_candidates(training(True), target='conditional', train_cutoff=fold().train_cutoff, scope=SCOPE)
        rows = [replace(economic(i, F='2000' if i < 4 else '-1000'), company_id='invented-company-6', group_id='invented-group-6') for i in range(8)]
        event = scan_events(rows, scope=SCOPE).events[0]
        result = conditional_fixture_forecast(models, features(6, '2025-06-01'), event,
                                             issued_at='2025-07-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(result['forecast_month'], '2025-06-01')
        self.assertFalse(result['advance_detection'])
        with self.assertRaises(ValueError):
            conditional_fixture_forecast(models, features(6, '2025-06-01'), event,
                                         issued_at='2025-06-15T00:00:00Z', scope=SCOPE)


if __name__ == '__main__':
    unittest.main()
