import copy
import unittest

from scripts.financial_v3_dataset_contract import policy, development_window, validate_company


class DatasetContractTests(unittest.TestCase):
    def test_scope(self):
        p = policy()
        self.assertEqual(p['version'], 'financial-v3-dataset-v1')
        self.assertFalse(p['outcomes_computed'])
        self.assertEqual(p['months'], ['2024-09-01', '2026-08-01'])
        self.assertEqual(p['full_score_interval'], [0, 100])

    def test_maturity_purge_and_group_exclusion(self):
        self.assertEqual(development_window('train', '2025-03-01'), 'fit')
        self.assertIsNone(development_window('train', '2025-04-01'))
        self.assertEqual(development_window('validation', '2025-07-01'), 'internal_validation')
        self.assertEqual(development_window('validation', '2025-09-01'), 'internal_validation')
        self.assertIsNone(development_window('validation', '2025-10-01'))
        self.assertIsNone(development_window('final_test', '2025-07-01'))
        self.assertIsNone(development_window('validation', '2026-03-01'))

    def test_wire_rejects_fake_headline(self):
        with self.assertRaises(ValueError):
            validate_company({'kind': 'financial_health_v3', 'health': {'score': 90}})

    def test_policy_returns_independent_value(self):
        p = policy()
        p['months'][0] = '1900-01-01'
        self.assertEqual(policy()['months'][0], '2024-09-01')


if __name__ == '__main__':
    unittest.main()
