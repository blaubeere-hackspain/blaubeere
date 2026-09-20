import unittest

from tests.test_financial_v3_engine import evaluate, fixture
from scripts.financial_v3_features import build_features


class FeatureTests(unittest.TestCase):
    def test_receipt_growth_not_gross_bank_or_withholding(self):
        receipts = ['1000', '1000', '1000', '1200', '1200', '1200', '1200']
        paid = evaluate(fixture(receipts=receipts))
        unpaid = evaluate(fixture(receipts=receipts, paid='0'))
        self.assertAlmostEqual(paid['features']['growth_3v3'], 0.2)
        self.assertEqual(paid['features']['growth_3v3'], unpaid['features']['growth_3v3'])
        self.assertEqual(paid['features']['growth_mom'], 0)
        self.assertIsNone(paid['features']['growth_yoy'])
        data = fixture(receipts=receipts)
        data.records['cash_movement'][0]['economic_bucket'] = 'financing'
        self.assertEqual(evaluate(data)['cash_history'][0]['operating_receipts'], '0')

    def test_six_month_requirement_and_zero_prior(self):
        result = evaluate(month='2025-05-01')
        self.assertIsNone(result['score']['value'])
        self.assertIn('insufficient_history', result['score']['reasons'])
        result = evaluate(fixture(receipts=['0', '0', '0', '1000', '1000', '1000', '1000']))
        self.assertIsNone(result['score']['value'])
        self.assertIn('zero_reference', result['score']['reasons'])

    def test_missing_interval_not_filled_and_perimeter_change(self):
        data = fixture()
        from dataclasses import replace
        data.obligation_coverage[6] = replace(data.obligation_coverage[6], comparable_id='changed')
        result = evaluate(data)
        self.assertIn('coverage_changed', result['score']['reasons'])
        self.assertIsNone(result['score']['value'])

    def test_yoy_needs_fifteen_contiguous_known_months(self):
        from tests.test_financial_v3_engine import context
        from scripts.financial_v3_engine import evaluate_company_month
        data = fixture(receipts=['1000'] * 12 + ['1400'] * 3)
        result = evaluate_company_month(data, context('2026-03-01'), observed_at='2026-04-01T00:00:00Z')
        self.assertIsNotNone(result['score']['value'], result['score']['reasons'])
        self.assertAlmostEqual(result['features']['growth_yoy'], 0.4)
        data.records['coverage_interval'].pop(0)
        result = evaluate_company_month(data, context('2026-03-01'), observed_at='2026-04-01T00:00:00Z')
        self.assertIsNone(result['features']['growth_yoy'])
        self.assertIsNotNone(result['score']['value'])


class GrowthBoundaryTests(unittest.TestCase):
    def test_mom_needs_two_months_not_six(self):
        result = evaluate(month='2025-02-01')
        self.assertEqual(result['features']['growth_mom'], 0)
        self.assertIsNone(result['score']['value'])

    def test_gap_keeps_calendar_positions_without_filling(self):
        data = fixture()
        data.records['coverage_interval'].pop(2)
        result = evaluate(data)
        self.assertEqual(len(result['features']['receipt_series']), 6)
        self.assertIsNone(result['features']['receipt_series'][2])
        self.assertIsNone(result['features']['generation_series'][2])
        self.assertEqual(result['features']['receipt_series'][3], '1000')

    def test_yoy_provenance_includes_full_comparison_window(self):
        from tests.test_financial_v3_engine import context
        from scripts.financial_v3_engine import evaluate_company_month
        data = fixture(receipts=['1000'] * 12 + ['1400'] * 3)
        result = evaluate_company_month(data, context('2026-03-01'), observed_at='2026-04-01T00:00:00Z')
        refs = result['features']['growth_evidence']['yoy']['source_refs']
        self.assertTrue(any(':receipt-1:' in ref for ref in refs))
        self.assertTrue(any(':receipt-15:' in ref for ref in refs))


if __name__ == '__main__':
    unittest.main()
