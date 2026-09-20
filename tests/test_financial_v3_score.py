import copy
import math
import unittest

from tests.test_financial_v3_engine import evaluate, fixture
from scripts.financial_v3_score import explain_delta, ratio_change


class ScoreTests(unittest.TestCase):
    def test_fixed_formulas_level_and_clipping_reconcile(self):
        result = evaluate()
        score = result['score']
        self.assertAlmostEqual(score['value'], 85)
        self.assertEqual(score['dimensions']['generation']['points'], 70)
        self.assertEqual(score['dimensions']['growth']['points'], 50)
        explanation = score['explanation']
        identity = explanation['base'] + math.fsum(explanation['contributions'].values()) - explanation['mora_penalty'] + explanation['limit_adjustment']
        self.assertAlmostEqual(identity, score['value'], places=12)
        self.assertAlmostEqual(explanation['residual'], 0, places=12)
        low = evaluate(fixture(opening='-10000', due='4000', paid='0', receipts=['1000','1000','1000','200','200','200','200']))
        self.assertEqual(low['score']['value'], 0)
        self.assertGreater(low['score']['explanation']['limit_adjustment'], 0)

    def test_delta_reconciles_and_ratio_fixed_order(self):
        data = fixture(receipts=['1000','1000','1000','1000','1100','1200','1500'])
        old, new = evaluate(data), evaluate(data, month='2025-07-01')
        delta = explain_delta(old, new)
        self.assertIsNotNone(delta['value'], delta['reasons'])
        total = math.fsum(delta['contributions'].values()) - delta['mora_penalty'] + delta['limit_adjustment']
        self.assertAlmostEqual(total, new['score']['value'] - old['score']['value'], places=10)
        self.assertAlmostEqual(delta['value'], total, places=10)
        self.assertTrue(delta['source_refs'])
        self.assertFalse(delta['causal_claim'])
        for dimension, steps in delta['dimension_steps'].items():
            self.assertAlmostEqual(math.fsum(steps.values()), delta['contributions'][dimension], places=10)
        decomposition = ratio_change(10, 2, 18, 3)
        self.assertEqual(decomposition['numerator'], 4)
        self.assertAlmostEqual(decomposition['denominator'], -3)
        self.assertAlmostEqual(decomposition['delta'], 1)

    def test_delta_gates_version_calendar_currency_and_perimeter(self):
        old, new = evaluate(), evaluate(month='2025-07-01')
        for key, value, reason in [('policy_version', 'other', 'version_changed'),
                                   ('month', '2025-08-01', 'insufficient_history'),
                                   ('perimeter_id', 'other', 'coverage_changed'),
                                   ('reporting_currency', 'USD', 'coverage_changed')]:
            changed = copy.deepcopy(new)
            changed[key] = value
            delta = explain_delta(old, changed)
            self.assertIsNone(delta['value'])
            self.assertIn(reason, delta['reasons'])

    def test_missing_core_no_renormalization(self):
        data = fixture()
        data.records['balance_anchor'][6]['unrestricted_native'] = None
        result = evaluate(data)
        self.assertIsNone(result['score']['value'])
        self.assertIsNone(result['score']['explanation']['contributions'])
        self.assertEqual(result['score']['dimensions']['generation']['weight'], 0.25)
        self.assertEqual(result['score']['dimensions']['generation']['points'], 70)


if __name__ == '__main__':
    unittest.main()
