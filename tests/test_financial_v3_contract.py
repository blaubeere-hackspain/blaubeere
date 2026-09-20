import copy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from scripts import financial_v3_contract as contract


def source():
    return {'source_id': 'fixture', 'source_sha256': 'a' * 64, 'record_id': 'r1',
            'effective_at': '2025-01-31T23:00:00Z', 'known_on': '2025-01-31T23:59:59.999999Z',
            'retrieved_at': '2025-02-01T00:00:00Z', 'source_timezone': 'Europe/Madrid',
            'availability_basis': 'verified'}


def anchor():
    return {'schema_version': contract.INPUT_VERSION, 'source_ref': source(),
            'company_id': 'c1', 'anchor_id': 'a1', 'account_id': 'b1', 'currency': 'EUR',
            'amount_native': '100.00', 'unrestricted_native': None,
            'measured_at': '2025-01-31T23:00:00Z', 'cutoff_semantics': 'instant',
            'independently_observed': True}


class PolicyTests(unittest.TestCase):
    def test_defaults_are_fixed_and_copies(self):
        policy = contract.default_policy()
        contract.validate_policy(policy)
        self.assertEqual(policy['scorecard']['weights'],
                         {'generation': 0.25, 'liquidity': 0.25, 'debt': 0.20, 'growth': 0.15, 'stability': 0.15})
        policy['scorecard']['weights']['generation'] = 0.99
        self.assertEqual(contract.default_policy()['scorecard']['weights']['generation'], 0.25)
        with self.assertRaises(ValueError):
            contract.validate_policy(policy)

    def test_defaults_do_not_claim_confirmation(self):
        policy = contract.default_policy()
        self.assertFalse(policy['status']['business_approved'])
        self.assertFalse(policy['status']['confirmatory_approved'])
        self.assertFalse(policy['status']['automatic_alerts_enabled'])
        self.assertFalse(policy['status']['independent_review_completed'])
        self.assertIn('new_test_missing', policy['status']['blockers'])
        self.assertEqual(policy['evaluation']['probability_gates']['ap_reference'], 'evaluation_prevalence_constant_baseline')
        self.assertEqual(policy['targets']['improvement_payment_evidence'], 'cumulative_since_reference_end_through_j')
        self.assertEqual(policy['targets']['conditional']['payment_reduction_evidence'], 'cumulative_since_reference_end_through_j')

    def test_policy_rejects_types_nonfinite_extra_missing_and_changed(self):
        for field, value in [('grace_days', True), ('grace_days', 7.0), ('penalty_cap', float('nan'))]:
            policy = contract.default_policy()
            policy['mora'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                contract.validate_policy(policy)
        policy = contract.default_policy()
        policy['extra'] = 1
        with self.assertRaises(ValueError):
            contract.validate_policy(policy)
        del policy['extra']
        del policy['targets']
        with self.assertRaises(ValueError):
            contract.validate_policy(policy)

    def test_canonical_and_strict_json(self):
        self.assertEqual(contract.canonical_hash({'b': 2, 'a': 1}), contract.canonical_hash({'a': 1, 'b': 2}))
        self.assertNotEqual(contract.canonical_hash({'a': 1}), contract.canonical_hash({'a': True}))
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                contract.parse_json(text)
        for value in ({1: 'x'}, {'a': Decimal('1')}, {'a': float('inf')}, ('not', 'json')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.canonical_bytes(value)


class PrimitiveTests(unittest.TestCase):
    def test_money_is_exact_and_not_native_float(self):
        money = contract.parse_money({'amount': '0.10', 'currency': 'EUR'})
        self.assertEqual(money.amount, Decimal('0.10'))
        self.assertEqual(money.currency, 'EUR')
        self.assertEqual(contract.decimal_amount(Decimal('0.2')), Decimal('0.2'))
        for value in (0.1, True, 1, 'NaN', 'Infinity', '1e2', '01', ' 1', '.1', '1.'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.decimal_amount(value)
        for value in ({'amount': '1', 'currency': 'eur'}, {'amount': '1', 'currency': 'EURO'},
                      {'amount': '1', 'currency': 'EUR', 'extra': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.parse_money(value)

    def test_number_and_bool_are_distinct(self):
        self.assertEqual(contract.finite_number(1), 1.0)
        for value in (True, False, float('inf'), float('nan'), Decimal('1'), '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.finite_number(value)

    def test_iso_dates_utc_and_month_boundaries(self):
        self.assertEqual(contract.iso_date('2024-02-29'), date(2024, 2, 29))
        self.assertEqual(contract.month_date('2025-01-01'), date(2025, 1, 1))
        self.assertEqual(contract.month_end('2024-02-01'), date(2024, 2, 29))
        self.assertEqual(contract.monthly_cutoff('2025-12-31'), datetime(2026, 1, 1, tzinfo=timezone.utc))
        for value in ('2025-02-29', '20250101', '2025-1-01', '2025-01-01T00:00:00Z', True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.iso_date(value)
        with self.assertRaises(ValueError):
            contract.month_date('2025-01-02')
        with self.assertRaises(ValueError):
            contract.monthly_cutoff('2025-01-30')
        for value in ('2025-01-01', '2025-01-01T00:00:00', '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00.1234567Z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.utc_timestamp(value)

    def test_source_availability_is_not_retrieval_or_effective_time(self):
        self.assertTrue(contract.source_availability(source(), '2025-01-31').eligible)
        value = source()
        value['known_on'] = None
        self.assertEqual(contract.source_availability(value, '2025-01-31').reasons, ('unknown_known_on',))
        value['known_on'] = '2025-02-01T00:00:00Z'
        self.assertIn('future_known_on', contract.source_availability(value, '2025-01-31').reasons)
        value['availability_basis'] = 'retrospective'
        value['known_on'] = '2025-01-01T00:00:00Z'
        self.assertIn('unverified_availability', contract.source_availability(value, '2025-01-31').reasons)
        value['availability_basis'] = 'assumed'
        self.assertFalse(contract.source_availability(value, '2025-01-31').eligible)
        value['availability_basis'] = 'verified'
        value['effective_at'] = '2025-06-01T00:00:00Z'
        self.assertTrue(contract.source_availability(value, '2025-01-31').eligible)

    def test_source_dates_and_keys_are_required(self):
        for key in ('source_sha256', 'effective_at', 'retrieved_at', 'source_timezone', 'known_on'):
            value = source()
            del value[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.validate_source(value)
        value = source()
        value['known_on'] = '2025-02-02T00:00:00Z'
        with self.assertRaises(ValueError):
            contract.validate_source(value)
        value = source()
        value['source_sha256'] = '../not-a-hash'
        with self.assertRaises(ValueError):
            contract.validate_source(value)

    def test_reconstruction_availability_propagates_unknown_and_future(self):
        old = source()
        newer = source()
        newer['known_on'] = '2025-02-01T00:00:00Z'
        self.assertEqual(contract.latest_known_on([old, newer]), contract.utc_timestamp(newer['known_on']))
        newer['known_on'] = None
        self.assertIsNone(contract.latest_known_on([old, newer]))
        self.assertIsNone(contract.latest_known_on([]))

    def test_reason_helpers_are_closed_and_deduplicated(self):
        self.assertEqual(contract.reason_codes('ledger_gap', 'ledger_gap', 'fx_missing'), ('fx_missing', 'ledger_gap'))
        with self.assertRaises(ValueError):
            contract.reason_codes('healthy_because_missing')


class RecordTests(unittest.TestCase):
    def test_input_schemas_are_fresh_and_closed(self):
        schemas = contract.input_schemas()
        self.assertEqual(set(schemas), {'account', 'coverage_interval', 'cash_movement', 'balance_anchor',
                                      'fx_observation', 'obligation', 'settlement', 'amendment', 'universe_attestation'})
        schemas['account']['company_id'] = 'anything'
        self.assertEqual(contract.input_schemas()['account']['company_id'], 'id')

    def test_remaining_record_fixtures_and_semantic_rejections(self):
        common = {'schema_version': contract.INPUT_VERSION, 'source_ref': source(), 'company_id': 'c1'}
        fixtures = {
            'account': {'account_id': 'b1', 'product_type': 'checking', 'currency': 'EUR',
                        'effective_from': None, 'effective_to': None, 'unrestricted_policy': 'unknown'},
            'cash_movement': {'movement_id': 't1', 'account_id': 'b1', 'currency': 'EUR',
                              'booked_at': '2025-01-31T23:00:00Z', 'value_at': None, 'amount_native': '-10.00',
                              'booking_status': 'booked', 'economic_bucket': 'operating',
                              'classification_evidence': 'e1', 'transfer_pair_id': None, 'obligation_allocation_ids': ['p1']},
            'fx_observation': {'fx_id': 'f1', 'currency': 'EUR', 'reporting_currency': 'EUR',
                               'measured_at': '2025-01-31T23:00:00Z', 'rate_local_per_reporting': '1', 'valuation_kind': 'stock'},
            'settlement': {'allocation_id': 'p1', 'obligation_id': 'o1', 'movement_id': 't1', 'currency': 'EUR',
                           'paid_at': '2025-01-31T23:00:00Z', 'amount_native': '10.00', 'component': 'trade', 'allocation_policy': 'explicit'},
            'amendment': {'amendment_id': 'am1', 'obligation_id': 'o1', 'currency': 'EUR', 'kind': 'renegotiate',
                          'effective_at': '2025-01-31T23:00:00Z', 'amount_native': '10', 'replacement_obligation_id': 'o2'},
            'universe_attestation': {'attestation_id': 'at1', 'source_kind': 'financial_debt',
                                     'effective_from': '2025-01-01', 'effective_to': None, 'exhaustive': True, 'explicit_zero': True},
        }
        for kind, fields in fixtures.items():
            with self.subTest(kind=kind):
                self.assertEqual(contract.validate_record(kind, {**common, **fields}), {**common, **fields})
        invalid = [('cash_movement', 'classification_evidence', None), ('cash_movement', 'obligation_allocation_ids', ['p1', 'p1']),
                   ('fx_observation', 'rate_local_per_reporting', '2'), ('settlement', 'amount_native', '-1'),
                   ('amendment', 'replacement_obligation_id', None), ('universe_attestation', 'exhaustive', False)]
        for kind, field, value in invalid:
            with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                contract.validate_record(kind, {**common, **fixtures[kind], field: value})

    def test_record_copy_unknown_missing_nullable_and_enum(self):
        record = anchor()
        result = contract.validate_record('balance_anchor', record)
        self.assertIsNone(result['unrestricted_native'])
        result['source_ref']['record_id'] = 'changed'
        self.assertEqual(record['source_ref']['record_id'], 'r1')
        for key, value in [('extra', 1), ('amount_native', True), ('cutoff_semantics', 'implied_close'),
                           ('independently_observed', 1), ('schema_version', 'v2')]:
            invalid = anchor()
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.validate_record('balance_anchor', invalid)
        del record['measured_at']
        with self.assertRaises(ValueError):
            contract.validate_record('balance_anchor', record)
        with self.assertRaises(ValueError):
            contract.validate_record('unexpected', anchor())

    def test_duplicates_idempotent_but_conflicts_rejected(self):
        first = anchor()
        self.assertEqual(len(contract.deduplicate_records('balance_anchor', [first, copy.deepcopy(first)])), 1)
        conflict = anchor()
        conflict['amount_native'] = '200.00'
        with self.assertRaisesRegex(ValueError, 'duplicate_conflict'):
            contract.deduplicate_records('balance_anchor', [first, conflict])

    def test_coverage_intervals_are_half_open_and_zero_requires_attestation(self):
        value = {'schema_version': contract.INPUT_VERSION, 'source_ref': source(), 'company_id': 'c1',
                 'account_id': 'b1', 'coverage_id': 'cv1', 'from_inclusive': '2025-01-01T00:00:00Z',
                 'to_exclusive': '2025-02-01T00:00:00Z', 'statement_complete': True, 'genuine_zero_activity': True}
        contract.validate_record('coverage_interval', value)
        value['statement_complete'] = False
        with self.assertRaises(ValueError):
            contract.validate_record('coverage_interval', value)
        value['genuine_zero_activity'] = False
        value['to_exclusive'] = value['from_inclusive']
        with self.assertRaises(ValueError):
            contract.validate_record('coverage_interval', value)

    def test_positive_legal_amount_and_unknown_due(self):
        value = {'schema_version': contract.INPUT_VERSION, 'source_ref': source(), 'company_id': 'c1',
                 'obligation_id': 'o1', 'version_id': 'v1', 'kind': 'operating_ap', 'currency': 'EUR',
                 'principal_or_face_amount': '10', 'installment_id': None, 'financial_product_id': None,
                 'invoice_id': 'i1', 'due_date': None, 'effective_from': '2025-01-01',
                 'effective_to': None, 'role_verified': False}
        result = contract.validate_record('obligation', value)
        self.assertIsNone(result['due_date'])
        value['principal_or_face_amount'] = '-1'
        with self.assertRaises(ValueError):
            contract.validate_record('obligation', value)

    def test_quantity_missingness_and_bool_are_not_scores(self):
        value = {'value': None, 'status': 'unavailable', 'reasons': ['missing_source'],
                 'source_refs': [], 'known_on': None, 'coverage_complete': False}
        self.assertIsNone(contract.validate_quantity(value)['value'])
        for change in ({'value': 0}, {'status': 'eligible'}, {'reasons': []}, {'coverage_complete': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                contract.validate_quantity({**value, **change})
        complete = {'value': 0, 'status': 'eligible', 'reasons': [], 'source_refs': ['fixture'],
                    'known_on': '2025-01-01T00:00:00Z', 'coverage_complete': True}
        self.assertEqual(contract.validate_quantity(complete)['value'], 0)
        with self.assertRaises(ValueError):
            contract.validate_quantity({**complete, 'value': False})

    def test_monthly_context_has_date_asof_exclusive_utc_cutoff(self):
        value = {'schema_version': contract.INPUT_VERSION, 'policy_version': contract.POLICY_VERSION,
                 'company_id': 'c1', 'month': '2025-01-01', 'as_of': '2025-01-31',
                 'knowledge_cutoff_exclusive': '2025-02-01T00:00:00Z', 'view': 'as_of',
                 'availability_assumption_id': None}
        contract.validate_context(value)
        with self.assertRaises(ValueError):
            contract.validate_context({**value, 'as_of': '2025-01-30'})
        with self.assertRaises(ValueError):
            contract.validate_context({**value, 'view': 'as_of_simulation'})
        with self.assertRaises(ValueError):
            contract.validate_context({**value, 'knowledge_cutoff_exclusive': '2025-02-02T00:00:00Z'})


class ReceiptTests(unittest.TestCase):
    def test_pure_receipt_check_binds_policy_sources_and_scope(self):
        payload = contract.protocol_bytes(contract.default_policy())
        bindings = {name: 'a' * 64 for name in contract.BINDING_PATHS}
        receipt = contract.make_receipt(payload, bindings, '2026-09-19T20:00:00Z')
        contract.validate_receipt(receipt, payload, bindings)
        changed = copy.deepcopy(receipt)
        changed['business_approved'] = True
        with self.assertRaises(ValueError):
            contract.validate_receipt(changed, payload, bindings)
        changed = dict(bindings)
        changed[next(iter(changed))] = 'b' * 64
        with self.assertRaises(ValueError):
            contract.validate_receipt(receipt, payload, changed)
        with self.assertRaises(ValueError):
            contract.validate_receipt(receipt, payload + b' ', bindings)
        with self.assertRaises(ValueError):
            contract.make_receipt(payload, {'../escape': 'a' * 64}, '2026-09-19T20:00:00Z')

    def test_exclusive_writer_never_overwrites(self):
        stream = MagicMock()
        stream.__enter__.return_value = stream
        with patch.object(Path, 'open', return_value=stream) as opened, patch.object(contract.os, 'fsync') as sync:
            contract.write_exclusive(Path('/unused/fixture'), b'{}')
            opened.assert_called_once_with('xb')
            stream.write.assert_called_once_with(b'{}')
            stream.flush.assert_called_once()
            sync.assert_called_once()
        with patch.object(Path, 'open', side_effect=FileExistsError), self.assertRaises(FileExistsError):
            contract.write_exclusive(Path('/unused/fixture'), b'{}')

    def test_existing_or_partial_seal_refuses_before_writing(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(contract, 'write_exclusive') as writer:
            with self.assertRaises(ValueError):
                contract.seal_development(Path('/unused'))
            writer.assert_not_called()

    def test_check_cli_does_not_seal(self):
        with patch.object(contract, 'check', return_value={'ok': True}) as check, \
             patch.object(contract, 'seal_development', side_effect=AssertionError('forbidden')):
            self.assertEqual(contract.main(['--check']), 0)
            check.assert_called_once()

    def test_check_missing_receipt_has_no_write_path(self):
        with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError), \
             patch.object(contract, 'write_exclusive', side_effect=AssertionError('forbidden')):
            with self.assertRaises(FileNotFoundError):
                contract.check(Path('/unused'))


if __name__ == '__main__':
    unittest.main()
