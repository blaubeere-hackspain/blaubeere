import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

from scripts import financial_v3_dataset_model as m
from scripts import financial_v3_dataset_model_metrics as mm
from scripts.financial_v3_dataset import feature_row
from scripts.financial_v3_cash import month_shift
from tests.test_financial_v3_dataset import flow


def history(origin='2025-01-01', future=(70, 80, 110), current=100):
    rows = {}
    for i, value in zip(range(-3, 4), [100, 100, 100, current, *future]):
        rows[month_shift(origin, i)] = {**flow(value), '_perimeter': ['account-a']}
    return rows


def feature(company='c', group='g', month='2025-01-01', split='train'):
    return feature_row(company, group, split, month, history(month), {}, {})


class LabelTests(unittest.TestCase):
    def test_exact_directional_boundaries(self):
        labels = m.labels_for(feature(), history())
        self.assertEqual(labels['receipt_contraction_3m']['value'], 1)
        self.assertEqual(labels['receipt_expansion_3m']['value'], 0)
        other = m.labels_for(feature(), history(future=(120, 120, 0)))
        self.assertEqual(other['receipt_expansion_3m']['value'], 1)
        self.assertEqual(other['receipt_contraction_3m']['value'], 0)
        self.assertEqual(labels['receipt_contraction_3m']['matured_at'], '2025-04-30')

    def test_missing_followup_never_zero_even_two_hits(self):
        rows = history()
        del rows['2025-04-01']
        label = m.labels_for(feature(), rows)['receipt_contraction_3m']
        self.assertIsNone(label['value'])
        self.assertIn('incomplete_receipt_months', label['reasons'])

    def test_same_count_different_account_is_censored(self):
        rows = history()
        rows['2025-03-01']['_perimeter'] = ['account-b']
        self.assertIn('changed_or_missing_observed_accounts', m.labels_for(feature(), rows)['receipt_contraction_3m']['reasons'])

    def test_known_subtotal_is_not_complete_target(self):
        rows = history()
        rows['2025-03-01']['cobros_operativos_eur'] = None
        self.assertIsNone(m.labels_for(feature(), rows)['receipt_contraction_3m']['value'])

    def test_incomplete_eur_and_classification(self):
        for key, value in [('n_sin_eur', 1), ('n_ambiguos_entrada', 1), ('tiene_actividad_caja', False)]:
            rows = history()
            rows['2025-01-01'][key] = value
            self.assertIsNone(m.labels_for(feature(), rows)['receipt_contraction_3m']['value'])

    def test_nonpositive_reference_unknown(self):
        rows = history()
        for offset in (-2, -1, 0):
            rows[month_shift('2025-01-01', offset)] = {**flow(0), '_perimeter': ['account-a']}
        self.assertIn('nonpositive_receipt_reference', m.labels_for(feature(), rows)['receipt_contraction_3m']['reasons'])

    def test_conditional_cohort_uses_only_current_inputs(self):
        first = history(current=80, future=(90, 90, 10))
        second = history(current=80, future=(0, 0, 0))
        self.assertEqual(m.current_dip(first, '2025-01-01'), m.current_dip(second, '2025-01-01'))
        a = m.labels_for(feature(), first)['current_receipt_dip_3m']
        b = m.labels_for(feature(), second)['current_receipt_dip_3m']
        self.assertEqual(a['value'], 'recovered')
        self.assertEqual(b['value'], 'persistent')
        self.assertEqual(m.labels_for(feature(), history(current=80, future=(90, 70, 90)))['current_receipt_dip_3m']['value'], 'mixed')

    def test_conditional_prior_warmup_and_future_unknown(self):
        rows = history(current=80)
        del rows['2024-10-01']
        self.assertIsNone(m.labels_for(feature(), rows)['current_receipt_dip_3m']['value'])
        rows = history(current=80)
        del rows['2025-04-01']
        self.assertIsNone(m.labels_for(feature(), rows)['current_receipt_dip_3m']['value'])

    def test_non_dip_not_future_selected(self):
        self.assertIn('not_current_receipt_dip', m.labels_for(feature(), history(current=100, future=(0, 0, 0)))['current_receipt_dip_3m']['reasons'])

    def test_q4_predictions_include_censored_current_dips(self):
        origin = '2025-07-01'
        f = feature(month=origin, split='validation')
        h = history(origin, current=80, future=(90, 90, 90))
        model = {'candidate': 'constant', 'prevalence': {'recovered': .4, 'persistent': .3, 'mixed': .3}}
        label = {(f['company_id'], origin): m.labels_for(f, h)}
        p = m.preselection_predictions(model, [f], label, 'current_receipt_dip_3m', {'c': h})[0]
        del h['2025-10-01']
        label = {(f['company_id'], origin): m.labels_for(f, h)}
        q = m.preselection_predictions(model, [f], label, 'current_receipt_dip_3m', {'c': h})[0]
        self.assertEqual(p['probabilities'], q['probabilities'])
        self.assertIsNone(q['label'])
        self.assertTrue(q['current_dip']['eligible'])

    def test_label_function_rejects_2026(self):
        f = feature()
        f['month'] = '2026-01-01'
        with self.assertRaises(ValueError):
            m.labels_for(f, history('2026-01-01'))


class ScopeTests(unittest.TestCase):
    def test_purge_and_holdout(self):
        rows = [feature()]
        assignment = {'g': 'train', 'v': 'validation', 'h': 'final_test'}
        companies = {'c': 'g'}
        m.validate_features(rows, assignment, companies)
        for month, split, group in [('2025-04-01', 'train', 'g'), ('2026-01-01', 'train', 'g'), ('2025-01-01', 'final_test', 'h')]:
            bad = copy.deepcopy(rows[0])
            bad.update(month=month, split=split, group_id=group)
            with self.assertRaises(ValueError):
                m.validate_features([bad], assignment, {'c': group})

    def test_company_group_match_and_duplicate(self):
        with self.assertRaises(ValueError):
            m.validate_features([feature()], {'g': 'train'}, {'c': 'other'})
        with self.assertRaises(ValueError):
            m.validate_features([feature(), feature()], {'g': 'train'}, {'c': 'g'})

    def test_approved_keys_no_2026_or_august_warmup(self):
        rows = [feature(month='2024-11-01'), feature('v', 'vg', '2025-09-01', 'validation')]
        keys = m.approved_keys(rows)
        self.assertEqual(min(k['month'] for k in keys), '2024-09-01')
        self.assertEqual(max(k['month'] for k in keys), '2025-12-01')
        rows[0]['eligible'] = False
        self.assertTrue(all(k['company_id'] == 'v' for k in m.approved_keys(rows)))

    def test_perimeter_sql_executes_and_scopes(self):
        con = duckdb.connect(':memory:')
        tx = "(SELECT * FROM (VALUES ('c','a',DATE '2025-01-02',TRUE,'banking','checking'), ('c','b',DATE '2025-01-03',TRUE,'banking','wallet'), ('c','z',DATE '2025-01-04',FALSE,'banking','checking'), ('c','q',DATE '2025-01-04',TRUE,'debt','loan'), ('holdout','h',DATE '2025-01-03',TRUE,'banking','checking'), ('c','future',DATE '2026-01-02',TRUE,'banking','checking')) t(company_id,product_id,date_ok,is_booked,product_source,product_type))"
        keys = [{'company_id': 'c', 'group_id': 'g', 'month': '2025-01-01'}]
        try:
            result = con.execute(m.perimeter_sql(tx, m.keys_sql(keys))).fetchall()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0][2], ['a', 'b'])
        finally:
            con.close()

    def test_feature_helper_matches_frozen_implementation_and_no_lookahead(self):
        h = history()
        expected = feature_row('c', 'g', 'train', '2025-01-01', h, {}, {})
        actual = m.monthly_features('c', 'g', 'train', '2025-01-01', h, {}, {})
        self.assertEqual(actual['values'], expected['values'])
        self.assertEqual(actual['eligible'], expected['eligible'])
        h['2025-04-01'] = flow(1e15)
        self.assertEqual(actual, m.monthly_features('c', 'g', 'train', '2025-01-01', h, {}, {}))
        with self.assertRaises(ValueError):
            m.monthly_features('c', 'g', 'final_test', '2026-01-01', h, {}, {})

    def test_receipt_exclusive_no_blind_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            m.claim_execution(out, {'frozen': True})
            with self.assertRaises(FileExistsError):
                m.claim_execution(out, {'frozen': True})
            self.assertTrue((out / 'execution-receipt.json').exists())

    def test_source_receipt_and_query_guards_have_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(m.duckdb, 'connect', side_effect=AssertionError('must not connect')):
                with self.assertRaises(ValueError):
                    m.ScopedInputs(out)
            self.assertEqual(list(out.iterdir()), [])
            m.claim_execution(out, {'fixture': True})
            inputs = m.ScopedInputs(out)
            try:
                keys = [{'company_id': 'c', 'group_id': 'g', 'month': '2026-01-01'}]
                with self.assertRaises(ValueError):
                    inputs.select('forbidden', 'SELECT 1', 'outcome', keys)
                with self.assertRaises(ValueError):
                    inputs.select('forbidden', 'CREATE TABLE forbidden(x INTEGER)', 'inference_input', keys)
                self.assertEqual(inputs.ledger, [])
                self.assertEqual(len(list(out.iterdir())), 1)
            finally:
                inputs.con.close()

    def test_check_binding_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a').write_text('a')
            bindings = {'a': m.sha256(root / 'a')}
            with patch.object(m, 'ScopedInputs', side_effect=AssertionError('no queries')), patch.object(mm, 'fit_candidate', side_effect=AssertionError('no fits')):
                m.check_hashes(bindings, root)
            self.assertEqual(list(root.iterdir()), [root / 'a'])
            (root / 'a').write_text('b')
            with self.assertRaises(ValueError):
                m.check_hashes(bindings, root)


class ModelTests(unittest.TestCase):
    def rows(self):
        rows = []
        for i in range(60):
            f = feature(str(i), 'g' + str(i % 10))
            f['values']['receipts_known'] = 80 + i
            rows.append({'feature': f, 'value': i % 2})
        return rows

    def test_support_minima_and_budget(self):
        self.assertTrue(mm.support(self.rows(), (0, 1))['adequate'])
        self.assertFalse(mm.support(self.rows()[:30], (0, 1))['adequate'])
        self.assertEqual(mm.candidates('receipt_contraction_3m'), ['constant', 'persistence', 'trend', 'logistic_C0.1', 'logistic_C1'])
        self.assertEqual(mm.candidates('current_receipt_dip_3m'), ['constant', 'logistic_C1'])
        self.assertEqual(m.protocol()['logistic'], {'C': [0.1, 1.0], 'seed': 1729, 'max_iter': 2000, 'solver': 'lbfgs', 'threads': 2})
        with self.assertRaises(ValueError):
            mm.fit_candidate('logistic_C10', self.rows(), 'receipt_contraction_3m')

    def test_train_only_preprocessing_and_portable_exact_model(self):
        model = mm.fit_candidate('logistic_C0.1', self.rows(), 'receipt_contraction_3m')
        saved = copy.deepcopy(model)
        row = feature()
        row['values']['receipts_known'] = 1e9
        p = mm.predict(model, row)
        self.assertEqual(model, saved)
        self.assertTrue(all(0 <= v <= 1 for v in p.values()))
        self.assertAlmostEqual(sum(p.values()), 1)
        self.assertEqual(model['C'], .1)
        self.assertLess(model['n_iter'], 2000)
        self.assertEqual(len(model['medians']), len(mm.FEATURES))
        self.assertEqual(len(model['scale']), 2 * len(mm.FEATURES))
        self.assertLess(model['portable_max_error'], 1e-12)

    def test_insufficient_support_abstains(self):
        self.assertIsNone(mm.fit_candidate('constant', self.rows()[:10], 'receipt_contraction_3m'))

    def test_group_metric_equal_weight_and_tie(self):
        rows = self.rows()
        probs = [mm.predict(mm.fit_candidate('constant', rows, 'receipt_contraction_3m'), r['feature']) for r in rows]
        metrics = mm.evaluate(rows, probs, (0, 1))
        self.assertAlmostEqual(metrics['mean_group_brier'], .25)
        self.assertIn('calibration', metrics['per_class']['1'])
        self.assertEqual(mm.choose({'constant': metrics, 'logistic_C1': metrics}, 'receipt_contraction_3m'), 'constant')

    def test_preselection_overlay_abstains(self):
        self.assertEqual(m.overlay_time_reason('2025-12-01'), 'before_selection_cutoff_no_backcast')
        self.assertIsNone(m.overlay_time_reason('2026-01-01'))
        self.assertIsNone(m.overlay_time_reason('2026-08-01'))
        self.assertEqual(m.overlay_time_reason('2026-09-01'), 'outside_frozen_product_window')


if __name__ == '__main__':
    unittest.main()
