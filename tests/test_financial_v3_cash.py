import copy
from decimal import Decimal
import unittest

from tests.test_financial_v3_engine import evaluate, fixture, record


class CashTests(unittest.TestCase):
    def test_native_reversal_and_non_tautological_residual(self):
        data = fixture()
        data.records['balance_anchor'] = [data.records['balance_anchor'][0], data.records['balance_anchor'][6]]
        result = evaluate(data)
        self.assertEqual(result['cash']['ledger'], '3200')
        self.assertEqual(result['cash']['accounts'][0]['residual'], '0')
        self.assertEqual(result['cash_history'][0]['ledger'], '2200')
        data.records['balance_anchor'][1]['amount_native'] = '3210'
        data.records['balance_anchor'][1]['unrestricted_native'] = '3210'
        result = evaluate(data)
        self.assertIsNone(result['score']['value'])
        self.assertEqual(result['cash']['accounts'][0]['residual'], '10')
        self.assertIn('reconciliation_mismatch', result['cash']['reasons'])

    def test_single_anchor_provisional_not_eligible(self):
        data = fixture()
        data.records['balance_anchor'] = [data.records['balance_anchor'][6]]
        result = evaluate(data)
        self.assertEqual(result['cash']['ledger'], '3200')
        self.assertIsNone(result['score']['value'])
        self.assertIn('reconciliation_unverified', result['cash']['reasons'])

    def test_future_anchor_retrospective_not_historical(self):
        data = fixture()
        data.records['balance_anchor'] = [data.records['balance_anchor'][0], data.records['balance_anchor'][7]]
        strict = evaluate(data)
        self.assertIsNone(strict['score']['value'])
        retro = evaluate(data, view='reconstructed_retrospective')
        self.assertEqual(retro['cash']['ledger'], '3200')
        self.assertEqual(retro['cash']['known_on'], '2025-07-31T23:59:59.999999Z')
        self.assertFalse(retro['prediction_eligible'])
        self.assertFalse(retro['historical_prediction'])
        data.records['coverage_interval'].pop()
        self.assertIn('ledger_gap', evaluate(data, view='reconstructed_retrospective')['cash']['reasons'])

    def test_midnight_unknown_is_not_prior_close(self):
        data = fixture()
        data.records['balance_anchor'] = [data.records['balance_anchor'][0], data.records['balance_anchor'][6]]
        anchor = data.records['balance_anchor'][1]
        anchor.update(measured_at='2025-07-01T00:00:00Z', cutoff_semantics='unknown')
        self.assertIn('anchor_cutoff_unknown', evaluate(data)['cash']['reasons'])
        anchor.update(cutoff_semantics='instant')
        anchor['source_ref']['known_on'] = '2025-07-01T00:00:00Z'
        self.assertIsNone(evaluate(data)['cash']['ledger'])
        self.assertEqual(evaluate(data, view='reconstructed_retrospective')['cash']['ledger'], '3200')

    def test_all_signed_flows_and_unknown_classification(self):
        data = fixture()
        data.records['cash_movement'][0].update(economic_bucket='unknown', classification_evidence=None)
        result = evaluate(data)
        self.assertEqual(result['cash_history'][0]['ledger'], '2200')
        self.assertIsNone(result['features']['growth_3v3'])
        self.assertIsNone(result['score']['value'])
        self.assertEqual(result['cash_history'][0]['native_flows']['bank']['unknown']['inflow'], '1000')

    def test_negative_balances_are_not_sentinels(self):
        result = evaluate(fixture(opening='-5000'))
        self.assertEqual(result['cash']['ledger'], '-3800')
        self.assertIsNotNone(result['score']['value'])
        self.assertEqual(result['score']['dimensions']['liquidity']['points'], 0)

    def test_unrestricted_not_assumed(self):
        data = fixture()
        data.records['balance_anchor'][6]['unrestricted_native'] = None
        result = evaluate(data)
        self.assertEqual(result['cash']['ledger'], '3200')
        self.assertIsNone(result['cash']['unrestricted'])
        self.assertIn('unrestricted_cash_unverified', result['score']['reasons'])

    def test_stock_fx_not_transaction_fx(self):
        data = fixture()
        extra = copy.deepcopy(data.records['account'][0])
        extra.update(account_id='usd', currency='USD')
        data.records['account'].append(extra)
        from scripts.financial_v3_cash import CashTerms
        from tests.test_financial_v3_engine import source
        data.cash_terms.append(CashTerms('invented-company', 'usd', 'USD', '0.01', 'booked_before_cutoff', (), source('usd-terms')))
        for i, at, semantics in [(0, '2025-01-01T00:00:00Z', 'opening'), (1, '2025-06-30T23:59:59.999999Z', 'closing')]:
            data.records['balance_anchor'].append(record(f'usd-{i}', at, anchor_id=f'usd-{i}', account_id='usd', currency='USD',
                amount_native='120', unrestricted_native='120', measured_at=at, cutoff_semantics=semantics, independently_observed=True))
        data.records['coverage_interval'].append(record('usd-cov', '2025-06-30T23:59:59.999999Z', coverage_id='usd-cov',
            account_id='usd', from_inclusive='2025-01-01T00:00:00Z', to_exclusive='2025-07-01T00:00:00Z',
            statement_complete=True, genuine_zero_activity=True))
        fx = record('fx', '2025-06-30T23:59:59.999999Z', fx_id='fx', currency='USD', reporting_currency='EUR',
            measured_at='2025-06-30T23:59:59.999999Z', rate_local_per_reporting='1.2', valuation_kind='transaction')
        data.records['fx_observation'].append(fx)
        self.assertIsNone(evaluate(data)['cash']['ledger'])
        fx['valuation_kind'] = 'stock'
        result = evaluate(data)
        self.assertEqual(Decimal(result['cash']['ledger']), Decimal('3300'))
        self.assertEqual(result['cash']['accounts'][1]['ledger_native'], '120')

    def test_zero_activity_attestation_conflicts_with_movement(self):
        data = fixture()
        data.records['coverage_interval'][0]['genuine_zero_activity'] = True
        with self.assertRaises(ValueError):
            evaluate(data)


if __name__ == '__main__':
    unittest.main()
