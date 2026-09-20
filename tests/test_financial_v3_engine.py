import copy
from datetime import date, timedelta
from decimal import Decimal
import unittest

from scripts.financial_v3_contract import INPUT_VERSION, POLICY_VERSION, default_policy, month_end
from scripts.financial_v3_cash import CashTerms, month_shift
from scripts.financial_v3_obligations import ObligationCoverage, PrincipalObservation
from scripts.financial_v3_engine import FinancialInputs, evaluate_company_month


D = Decimal


def source(key, known='2025-01-01T00:00:00Z'):
    return {'source_id': 'invented-core-fixture', 'source_sha256': 'a' * 64, 'record_id': key,
            'effective_at': '2025-01-01T00:00:00Z', 'known_on': known,
            'retrieved_at': '2027-01-01T00:00:00Z', 'source_timezone': 'UTC',
            'availability_basis': 'verified' if known else 'unknown'}


def record(key, known='2025-01-01T00:00:00Z', **fields):
    return {'schema_version': INPUT_VERSION, 'company_id': 'invented-company',
            'source_ref': source(key, known), **fields}


def context(month='2025-06-01', view='as_of'):
    end = month_end(month)
    return {'schema_version': INPUT_VERSION, 'policy_version': POLICY_VERSION,
            'company_id': 'invented-company', 'month': month, 'as_of': str(end),
            'knowledge_cutoff_exclusive': str(end + timedelta(days=1)) + 'T00:00:00Z',
            'view': view, 'availability_assumption_id': None}


def fixture(paid='800', due='800', receipts=None, opening='2000', late_days=10):
    receipts = receipts or ['1000'] * 7
    rows = {key: [] for key in ('account', 'coverage_interval', 'cash_movement', 'balance_anchor',
                                'fx_observation', 'obligation', 'settlement', 'amendment', 'universe_attestation')}
    rows['account'].append(record('bank', account_id='bank', currency='EUR', product_type='bank',
                                  effective_from='2025-01-01', effective_to=None,
                                  unrestricted_policy='verified_unrestricted'))
    rows['balance_anchor'].append(record('opening', anchor_id='opening', account_id='bank', currency='EUR',
                                         amount_native=opening, unrestricted_native=opening,
                                         measured_at='2025-01-01T00:00:00Z', cutoff_semantics='opening',
                                         independently_observed=True))
    for kind in ('cash', 'operating_ap', 'financial_debt', 'ar'):
        rows['universe_attestation'].append(record(kind, attestation_id=kind, source_kind=kind,
            effective_from='2025-01-01', effective_to='2027-01-01', exhaustive=True,
            explicit_zero=kind in ('financial_debt', 'ar')))
    balance = D(opening)
    coverage = []
    for i, receipt in enumerate(receipts, 1):
        month = month_shift('2025-01-01', i - 1)
        end = month_end(month)
        close = str(end) + 'T23:59:59.999999Z'
        next_day = str(end + timedelta(days=1))
        amount = D(paid if i == 6 else due)
        due_date = date(2025, 6, 30) - timedelta(days=late_days) if i == 6 else date.fromisoformat(month).replace(day=20)
        oid, mid, aid = f'ap-{i}', f'pay-{i}', f'alloc-{i}'
        rows['coverage_interval'].append(record(f'cov-{i}', close, coverage_id=f'cov-{i}', account_id='bank',
            from_inclusive=month + 'T00:00:00Z', to_exclusive=next_day + 'T00:00:00Z',
            statement_complete=True, genuine_zero_activity=False))
        rows['cash_movement'].append(record(f'receipt-{i}', month + 'T12:00:00Z', movement_id=f'receipt-{i}',
            account_id='bank', currency='EUR', booked_at=month + 'T12:00:00Z', value_at=None,
            amount_native=receipt, booking_status='booked', economic_bucket='operating',
            classification_evidence='receipt-proof', transfer_pair_id=None, obligation_allocation_ids=[]))
        rows['obligation'].append(record(oid, month + 'T00:00:00Z', obligation_id=oid, version_id='v1',
            kind='operating_ap', currency='EUR', principal_or_face_amount=due, installment_id=None,
            financial_product_id=None, invoice_id=oid, due_date=str(due_date), effective_from='2025-01-01',
            effective_to=None, role_verified=True))
        if amount:
            at = month[:8] + '20T12:00:00Z'
            rows['cash_movement'].append(record(mid, at, movement_id=mid, account_id='bank', currency='EUR',
                booked_at=at, value_at=None, amount_native=str(-amount), booking_status='booked',
                economic_bucket='operating', classification_evidence='payment-proof', transfer_pair_id=None,
                obligation_allocation_ids=[aid]))
            rows['settlement'].append(record(aid, at, allocation_id=aid, obligation_id=oid, movement_id=mid,
                currency='EUR', paid_at=at, amount_native=str(amount), component='trade', allocation_policy='explicit'))
        balance += D(receipt) - amount
        rows['balance_anchor'].append(record(f'close-{i}', close, anchor_id=f'close-{i}', account_id='bank',
            currency='EUR', amount_native=str(balance), unrestricted_native=str(balance), measured_at=close,
            cutoff_semantics='closing', independently_observed=True))
        for kind in ('operating_ap', 'financial_debt', 'ar'):
            coverage.append(ObligationCoverage('invented-company', kind, '2025-01-01', next_day,
                '2025-01-01', next_day, '2027-01-01', 'constant-eur-perimeter-v1', source(f'ledger-{kind}-{i}', close)))
    terms = CashTerms('invented-company', 'bank', 'EUR', '0.01', 'booked_before_cutoff',
                      ('-999999999999',), source('cash-terms'))
    return FinancialInputs(rows, [terms], coverage, [])


def evaluate(inputs=None, month='2025-06-01', view='as_of', **kwargs):
    return evaluate_company_month(inputs or fixture(), context(month, view), observed_at='2026-01-01T00:00:00Z', **kwargs)


def loan(inputs, principal='600', interest='60', paid_principal='0', paid_interest='0'):
    inputs.records['universe_attestation'][2]['explicit_zero'] = False
    for kind, face, paid, component in [('financial_principal', principal, paid_principal, 'principal'),
                                        ('financial_interest', interest, paid_interest, 'interest')]:
        oid = kind
        inputs.records['obligation'].append(record(oid, obligation_id=oid, version_id='v1', kind=kind,
            currency='EUR', principal_or_face_amount=face, installment_id='installment-1',
            financial_product_id='loan-1', invoice_id=None, due_date='2025-06-20',
            effective_from='2025-01-01', effective_to=None, role_verified=True))
        if D(paid):
            mid, aid, at = f'pay-{oid}', f'alloc-{oid}', '2025-06-20T13:00:00Z'
            inputs.records['cash_movement'].append(record(mid, at, movement_id=mid, account_id='bank', currency='EUR',
                booked_at=at, value_at=None, amount_native=str(-D(paid)), booking_status='booked',
                economic_bucket='financing', classification_evidence='loan', transfer_pair_id=None,
                obligation_allocation_ids=[aid]))
            inputs.records['settlement'].append(record(aid, at, allocation_id=aid, obligation_id=oid,
                movement_id=mid, currency='EUR', paid_at=at, amount_native=paid, component=component,
                allocation_policy='explicit'))
    total = D(paid_principal) + D(paid_interest)
    for anchor in inputs.records['balance_anchor']:
        if anchor['measured_at'] >= '2025-06-20':
            anchor['amount_native'] = str(D(anchor['amount_native']) - total)
            anchor['unrestricted_native'] = anchor['amount_native']
    inputs.principal_observations.append(PrincipalObservation('invented-company', 'loan-1', 'EUR',
        '2025-06-30', str(D(principal) - D(paid_principal)), source('principal-stock', '2025-06-30T23:59:59.999999Z')))
    return inputs


class EngineTests(unittest.TestCase):
    def test_paired_nonpayment_and_partial_payment(self):
        paid, partial, unpaid = [evaluate(fixture(paid=p)) for p in ('800', '300', '0')]
        for item in (paid, partial, unpaid):
            self.assertIsNotNone(item['score']['value'], item['score']['reasons'])
            self.assertEqual(item['features']['adjusted_cash'], '3200')
            self.assertEqual(item['features']['reference_receipts'], '1000')
            self.assertEqual(item['features']['generation_recent'], '200')
            self.assertEqual(item['features']['growth_3v3'], 0)
        self.assertEqual([D(x['cash']['unrestricted']) for x in (paid, partial, unpaid)], [D(3200), D(3700), D(4000)])
        self.assertEqual([D(x['obligations']['unpaid_due']) for x in (paid, partial, unpaid)], [D(0), D(500), D(800)])
        self.assertGreaterEqual(paid['score']['value'], partial['score']['value'])
        self.assertGreaterEqual(partial['score']['value'], unpaid['score']['value'])

    def test_age_and_exposure_aggregate_monotonic(self):
        prior = 101
        for days, multiplier in [(0, '0'), (1, '1'), (7, '1'), (8, '1.25'), (30, '1.25'),
                                 (31, '1.5'), (60, '1.5'), (61, '2'), (90, '2'), (91, '3'),
                                 (180, '3'), (181, '4'), (365, '4')]:
            data = loan(fixture(), principal='800', interest='0')
            data.records['obligation'][-2]['due_date'] = '2025-06-30'
            data.records['obligation'][-2]['effective_from'] = '2024-01-01'
            for cov in data.obligation_coverage:
                object.__setattr__(cov, 'history_from', '2024-01-01')
            result = evaluate(data)
            from scripts.financial_v3_obligations import mora_multiplier
            self.assertEqual(mora_multiplier(days), D(multiplier))
            self.assertEqual(result['obligations']['unpaid_due'], '800')
            self.assertEqual(result['obligations']['weighted_exposure'], '0')
            data.records['obligation'][-2]['due_date'] = str(date(2025, 6, 30) - timedelta(days=days))
            aged = evaluate(data)
            self.assertEqual(D(aged['obligations']['weighted_exposure']), D(800) * D(multiplier))
            self.assertLessEqual(aged['score']['value'], prior)
            prior = aged['score']['value']
        values = [evaluate(fixture(paid=p))['score']['value'] for p in ('800', '600', '200', '0')]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_future_facts_cannot_change_asof(self):
        data = fixture()
        expected = evaluate(data)
        future = copy.deepcopy(data.records['balance_anchor'][6])
        future['amount_native'] = '123456'
        future['source_ref']['known_on'] = '2025-07-01T00:00:00Z'
        data.records['balance_anchor'].append(future)
        future_obligation = copy.deepcopy(data.records['obligation'][0])
        future_obligation['principal_or_face_amount'] = '999999'
        future_obligation['source_ref']['known_on'] = '2025-07-01T00:00:00Z'
        data.records['obligation'].append(future_obligation)
        self.assertEqual(evaluate(data), expected)

    def test_nulls_gaps_and_missing_universe_never_zero(self):
        for transform, reason in [
            (lambda d: d.records['coverage_interval'].pop(2), 'ledger_gap'),
            (lambda d: d.records['universe_attestation'].pop(2), 'universe_unverified'),
            (lambda d: d.obligation_coverage.clear(), 'settlement_history_missing'),
            (lambda d: d.records['obligation'][1].update(due_date=None), 'due_date_unknown'),
            (lambda d: d.records['cash_movement'][0].update(economic_bucket='unknown', classification_evidence=None), 'classification_ambiguous'),
        ]:
            data = fixture()
            transform(data)
            result = evaluate(data)
            self.assertIsNone(result['score']['value'])
            self.assertIn(reason, result['score']['reasons'])

    def test_unknown_known_on_and_open_month(self):
        data = fixture()
        data.records['coverage_interval'][2]['source_ref'].update(known_on=None, availability_basis='unknown')
        result = evaluate(data)
        self.assertIsNone(result['score']['value'])
        self.assertIn('unknown_known_on', result['score']['reasons'])
        result = evaluate_company_month(fixture(), context(), observed_at='2025-06-29T00:00:00Z')
        self.assertIsNone(result['score']['value'])
        self.assertIn('open_month', result['score']['reasons'])

    def test_duplicate_identity_conflict_and_ownership(self):
        data = fixture()
        expected = evaluate(data)
        data.records['cash_movement'].append(copy.deepcopy(data.records['cash_movement'][0]))
        self.assertEqual(evaluate(data), expected)
        data.records['cash_movement'][-1]['amount_native'] = '1'
        with self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
            evaluate(data)
        for field, value in [('currency', 'USD'), ('account_id', 'other-company-bank')]:
            data = fixture()
            data.records['cash_movement'][0][field] = value
            with self.assertRaises(ValueError):
                evaluate(data)

    def test_known_zero_and_nonfinite(self):
        result = evaluate()
        self.assertEqual(result['obligations']['financial_principal'], '0')
        self.assertEqual(result['obligations']['next_service_mean'], '0')
        for invalid in ('NaN', 'Infinity', None, 2.5, '-999999999999'):
            data = fixture()
            data.records['balance_anchor'][6]['amount_native'] = invalid
            with self.assertRaises(ValueError):
                evaluate(data)
        self.assertIsNone(evaluate(fixture(receipts=['0'] * 7))['score']['value'])

    def test_fixed_policy_not_mutable_at_runtime(self):
        policy = default_policy()
        policy['scorecard']['weights']['growth'] = 0.9
        with self.assertRaises(ValueError):
            evaluate(policy=policy)

    def test_invented_levels_only_arithmetic_not_validation(self):
        healthy = evaluate(fixture(due='300', paid='300', opening='4000'))
        middle = evaluate(fixture(due='950', paid='950', opening='0'))
        weak = evaluate(fixture(due='1400', paid='0', opening='0', receipts=['1000','1000','1000','700','600','500','400']))
        self.assertGreater(healthy['score']['value'], middle['score']['value'])
        self.assertGreater(middle['score']['value'], weak['score']['value'])
        for item in (healthy, middle, weak):
            self.assertEqual(item['score']['band_validation'], 'unvalidated_internal')
            self.assertFalse(item['automatic_alerts_enabled'])
            self.assertFalse(item['historical_prediction'])


class EvidenceBoundaryTests(unittest.TestCase):
    def test_absent_debt_and_ar_are_null_not_zero(self):
        data = fixture()
        data.records['universe_attestation'] = [r for r in data.records['universe_attestation'] if r['source_kind'] not in ('financial_debt', 'ar')]
        result = evaluate(data)
        self.assertIsNone(result['obligations']['financial_principal'])
        self.assertIsNone(result['obligations']['next_service_mean'])
        self.assertIsNone(result['obligations']['receivables'])
        self.assertEqual(result['obligations']['known_subtotals']['financial_principal'], '0')
        self.assertIsNone(result['features']['adjusted_cash'])
        self.assertIsNone(result['score']['value'])

    def test_unknown_due_never_reclassified_as_not_due(self):
        data = fixture(paid='0')
        data.records['obligation'][5]['due_date'] = None
        result = evaluate(data)
        self.assertIsNone(result['obligations']['unpaid_due'])
        self.assertIsNone(result['obligations']['not_due'])
        self.assertIsNone(result['features']['adjusted_cash'])
        self.assertEqual(result['obligations']['known_subtotals']['unpaid_due'], '0')

    def test_supplement_duplicates_idempotent_and_conflicts_reject(self):
        data = fixture()
        expected = evaluate(data)
        data.cash_terms.append(copy.deepcopy(data.cash_terms[0]))
        self.assertEqual(evaluate(data), expected)
        from dataclasses import replace
        data.cash_terms[-1] = replace(data.cash_terms[-1], minor_unit='100')
        with self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
            evaluate(data)

    def test_json_boundary_and_knowledge_timestamp_precision(self):
        from scripts.financial_v3_contract import canonical_bytes
        from scripts.financial_v3_features import combined_metadata
        result = evaluate()
        self.assertTrue(canonical_bytes(result))
        parts = [dict(known_on=t, source_refs=['r'], availability_verified=True) for t in
                 ('2025-06-30T23:59:59Z', '2025-06-30T23:59:59.999999Z')]
        self.assertEqual(combined_metadata(parts)['known_on'], '2025-06-30T23:59:59.999999Z')

    def test_account_zero_attestation_is_observed_zero(self):
        data = fixture(receipts=['0'] * 7, due='0', paid='0', opening='0')
        for kind in ('account', 'cash_movement', 'balance_anchor', 'coverage_interval'):
            data.records[kind] = []
        data.records['universe_attestation'][0]['explicit_zero'] = True
        result = evaluate(data)
        self.assertEqual(result['cash']['ledger'], '0')
        self.assertEqual(result['cash']['unrestricted'], '0')
        self.assertIsNone(result['score']['value'])
        self.assertIn('zero_reference', result['score']['reasons'])


class ExtendedInvariantTests(unittest.TestCase):
    def test_future_effective_unknown_facts_do_not_change_origin(self):
        data = fixture()
        expected = evaluate(data)
        future = copy.deepcopy(data.records['cash_movement'][-1])
        future['source_ref'].update(known_on=None, availability_basis='unknown')
        data.records['cash_movement'].append(future)
        self.assertEqual(evaluate(data), expected)

    def test_payment_cannot_be_overallocated_across_obligations(self):
        data = fixture()
        allocation = copy.deepcopy(data.records['settlement'][0])
        allocation.update(allocation_id='another-allocation', obligation_id='ap-2', amount_native='1')
        data.records['settlement'].append(allocation)
        data.records['cash_movement'][1]['obligation_allocation_ids'].append('another-allocation')
        with self.assertRaisesRegex(ValueError, 'beyond payment'):
            evaluate(data)

    def test_insufficient_origin_schedule_blocks_known_zero_service(self):
        from dataclasses import replace
        data = fixture()
        data.obligation_coverage = [replace(c, schedule_to_exclusive='2025-08-01') if c.source_kind == 'financial_debt' else c
                                    for c in data.obligation_coverage]
        result = evaluate(data)
        self.assertIsNone(result['obligations']['next_service_mean'])
        self.assertIn('debt_vintage_missing', result['score']['reasons'])

    def test_foreign_obligations_abstain_without_silent_netting(self):
        data = fixture(paid='0')
        data.records['obligation'][5]['currency'] = 'USD'
        result = evaluate(data)
        self.assertIsNone(result['obligations']['unpaid_due'])
        self.assertIsNone(result['score']['value'])
        self.assertIn('fx_missing', result['score']['reasons'])
        item = next(r for r in result['obligations']['items'] if r['obligation_id'] == 'ap-6')
        self.assertEqual((item['remaining'], item['currency']), ('800', 'USD'))

    def test_improving_and_still_high_deteriorating_are_observed_only(self):
        from scripts.financial_v3_score import explain_delta
        rising = fixture(due='950', paid='950', opening='0', receipts=['1000'] * 6 + ['1800'])
        falling = fixture(due='300', paid='300', opening='4000', receipts=['1000'] * 6 + ['100'])
        improvement = explain_delta(evaluate(rising), evaluate(rising, month='2025-07-01'))
        deterioration = explain_delta(evaluate(falling), evaluate(falling, month='2025-07-01'))
        self.assertEqual(improvement['direction'], 'improving')
        self.assertEqual(deterioration['direction'], 'deteriorating')
        self.assertGreater(evaluate(falling)['score']['value'], 90)
        self.assertGreater(evaluate(falling, month='2025-07-01')['score']['value'], 60)

    def test_open_interval_anchor_includes_all_bridge_movements(self):
        data = fixture()
        later = copy.deepcopy(data.records['balance_anchor'][7])
        later.update(anchor_id='mid-july-1', amount_native='4200', unrestricted_native='4200',
                     measured_at='2025-07-01T13:00:00Z', cutoff_semantics='instant')
        later['source_ref']['known_on'] = '2025-07-01T13:00:00Z'
        data.records['balance_anchor'] = [data.records['balance_anchor'][0], later]
        result = evaluate(data, view='reconstructed_retrospective')
        self.assertEqual(result['cash']['ledger'], '3200')
        self.assertEqual(result['cash']['accounts'][0]['residual'], '0')
        data.records['coverage_interval'].pop()
        self.assertIn('ledger_gap', evaluate(data, view='reconstructed_retrospective')['cash']['reasons'])


if __name__ == '__main__':
    unittest.main()
