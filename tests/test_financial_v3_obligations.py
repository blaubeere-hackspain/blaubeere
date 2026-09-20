import copy
from decimal import Decimal
import unittest

from tests.test_financial_v3_engine import evaluate, fixture, loan, record


class ObligationTests(unittest.TestCase):
    def test_principal_interest_distinct_and_no_payment_invariant(self):
        paid = evaluate(loan(fixture(), paid_principal='600', paid_interest='60'))
        interest = evaluate(loan(fixture(), paid_interest='60'))
        unpaid = evaluate(loan(fixture()))
        self.assertEqual(paid['obligations']['financial_principal'], '0')
        self.assertEqual(interest['obligations']['financial_principal'], '600')
        self.assertEqual(unpaid['obligations']['unpaid_by_kind']['financial_interest'], '60')
        self.assertEqual(interest['features']['adjusted_cash'], unpaid['features']['adjusted_cash'])
        self.assertEqual(paid['features']['adjusted_cash'], unpaid['features']['adjusted_cash'])
        self.assertGreaterEqual(paid['score']['value'], interest['score']['value'])
        self.assertGreaterEqual(interest['score']['value'], unpaid['score']['value'])

    def test_allocations_cannot_exceed_payment_or_face(self):
        for mutation in ('amount', 'component', 'currency', 'owner', 'unlinked'):
            data = fixture()
            allocation = data.records['settlement'][0]
            if mutation == 'amount':
                allocation['amount_native'] = '801'
            elif mutation == 'component':
                allocation['component'] = 'principal'
            elif mutation == 'currency':
                allocation['currency'] = 'USD'
            elif mutation == 'owner':
                allocation['obligation_id'] = 'other-owner'
            else:
                allocation['movement_id'] = None
            if mutation == 'unlinked':
                self.assertIsNone(evaluate(data)['score']['value'])
            else:
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    evaluate(data)

    def test_no_ambiguous_fallback_or_duplicate_invoice(self):
        data = fixture()
        data.records['settlement'][0]['allocation_policy'] = 'oldest_due_assumed'
        self.assertIn('allocation_conflict', evaluate(data)['score']['reasons'])
        data = fixture()
        alias = copy.deepcopy(data.records['obligation'][0])
        alias['obligation_id'] = 'alias'
        data.records['obligation'].append(alias)
        with self.assertRaises(ValueError):
            evaluate(data)
        data = loan(fixture())
        alias = copy.deepcopy(data.records['obligation'][-2])
        alias.update(obligation_id='whole-loan', installment_id=None)
        data.records['obligation'].append(alias)
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_ar_is_not_net_against_debt(self):
        data = loan(fixture())
        data.records['universe_attestation'][3]['explicit_zero'] = False
        ar = copy.deepcopy(data.records['obligation'][0])
        ar.update(obligation_id='ar', invoice_id='ar', kind='ar', principal_or_face_amount='50000')
        data.records['obligation'].append(ar)
        result = evaluate(data)
        self.assertEqual(result['obligations']['receivables'], '50000')
        self.assertEqual(result['obligations']['financial_principal'], '600')
        self.assertEqual(result['obligations']['unpaid_due'], '660')

    def test_amendments_both_known_and_effective_writeoff_not_recovery(self):
        data = fixture(paid='0')
        amendment = record('cancel', '2025-07-01T00:00:00Z', amendment_id='cancel', obligation_id='ap-6', currency='EUR',
            kind='cancel', effective_at='2025-06-25T00:00:00Z', amount_native='200', replacement_obligation_id=None)
        data.records['amendment'].append(amendment)
        self.assertEqual(evaluate(data)['obligations']['unpaid_due'], '800')
        amendment['source_ref']['known_on'] = '2025-06-25T00:00:00Z'
        self.assertEqual(evaluate(data)['obligations']['unpaid_due'], '600')
        self.assertEqual(evaluate(data)['obligations']['operating_due'], '800')
        amendment['kind'] = 'writeoff'
        result = evaluate(data)
        self.assertEqual(result['obligations']['unpaid_due'], '800')
        self.assertEqual(result['obligations']['written_off'], '200')
        self.assertEqual(result['obligations']['allocated_paid'], '0')
        amendment.update(kind='cancel', effective_at='2025-07-01T00:00:00Z')
        self.assertEqual(evaluate(data)['obligations']['unpaid_due'], '800')

    def test_renegotiation_preserves_lineage(self):
        data = fixture(paid='0')
        replacement = copy.deepcopy(data.records['obligation'][5])
        replacement.update(obligation_id='replacement', invoice_id='replacement', effective_from='2025-06-25', due_date='2025-08-20')
        replacement['source_ref']['known_on'] = '2025-06-25T00:00:00Z'
        data.records['obligation'].append(replacement)
        data.records['amendment'].append(record('renegotiate', '2025-06-25T00:00:00Z', amendment_id='renegotiate',
            obligation_id='ap-6', currency='EUR', kind='renegotiate', effective_at='2025-06-25T00:00:00Z',
            amount_native='800', replacement_obligation_id='replacement'))
        result = evaluate(data)
        self.assertEqual(result['obligations']['unpaid_due'], '0')
        self.assertEqual(result['obligations']['unpaid_by_kind']['operating_ap'], '800')
        old = next(x for x in result['obligations']['items'] if x['obligation_id'] == 'ap-6')
        self.assertEqual(old['due_date'], '2025-06-20')
        self.assertEqual(old['replacement_ids'], ['replacement'])
        self.assertEqual(old['paid'], '0')

    def test_schedule_vintage_and_no_historical_snapshot_replication(self):
        data = loan(fixture())
        future = copy.deepcopy(data.records['obligation'][-2])
        future.update(obligation_id='next', installment_id='next', principal_or_face_amount='300', due_date='2025-08-20')
        future['source_ref']['known_on'] = '2025-07-01T00:00:00Z'
        data.records['obligation'].append(future)
        self.assertEqual(evaluate(data)['obligations']['next_service_mean'], '0')
        future['source_ref']['known_on'] = '2025-06-01T00:00:00Z'
        from dataclasses import replace
        data.principal_observations[0] = replace(data.principal_observations[0], amount_native='900')
        self.assertEqual(evaluate(data)['obligations']['next_service_mean'], '100')
        future.update(effective_from='2025-08-01')
        self.assertEqual(evaluate(data)['obligations']['financial_principal'], '600')
        self.assertIn('reconciliation_mismatch', evaluate(data)['score']['reasons'])

    def test_principal_observation_required_not_tautological(self):
        data = loan(fixture())
        data.principal_observations.clear()
        result = evaluate(data)
        self.assertEqual(result['obligations']['financial_principal'], '600')
        self.assertIn('reconciliation_unverified', result['score']['reasons'])

    def test_exact_decimal_amounts(self):
        result = evaluate(fixture(due='800.123456789012', paid='300.000000000001'))
        self.assertEqual(Decimal(result['obligations']['unpaid_due']), Decimal('500.123456789011'))
        self.assertEqual(Decimal(result['features']['adjusted_cash']), Decimal('3199.259259265928'))


class ObligationEvidenceTests(unittest.TestCase):
    def test_conflicting_coverage_cannot_be_resolved_by_input_order(self):
        from dataclasses import replace
        data = fixture()
        data.obligation_coverage.append(replace(data.obligation_coverage[15], comparable_id='conflicting-certificate'))
        with self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
            evaluate(data)

    def test_allocated_payment_proof_is_in_obligation_provenance(self):
        from scripts.financial_v3_cash import EvidenceView
        from scripts.financial_v3_obligations import obligation_month
        from tests.test_financial_v3_engine import context
        data = fixture()
        data.records['cash_movement'][11]['source_ref']['known_on'] = '2025-08-15T00:00:00Z'
        view = EvidenceView(data.records, context(view='reconstructed_retrospective'))
        result = obligation_month(view, '2025-06-01', data.obligation_coverage, data.principal_observations)
        self.assertEqual(result['known_on'], '2025-08-15T00:00:00Z')
        self.assertTrue(any(':pay-6:' in ref for ref in result['source_refs']))
        item = next(r for r in result['items'] if r['obligation_id'] == 'ap-6')
        self.assertTrue(any(':pay-6:' in ref for ref in item['source_refs']))


if __name__ == '__main__':
    unittest.main()
