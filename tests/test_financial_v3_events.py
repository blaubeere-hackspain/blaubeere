import copy
from dataclasses import replace
from decimal import Decimal as D
import unittest

from scripts.financial_v3_cash import month_shift
from scripts.financial_v3_contract import POLICY_VERSION
from scripts.financial_v3_events import (
    FixtureScope, DebtState, EconomicMonth, adapt_core_outcome, scan_events,
    directional_label, conditional_label, confirmatory_entry,
)
from tests.test_financial_v3_engine import fixture, evaluate


SCOPE = FixtureScope('invented-v3t6', (('invented-company', 'invented-group'),))


def economic(i, F='2000', Q='0', paid='0', written='0', adjustment='0', Z='0', known=None):
    month = month_shift('2025-01-01', i)
    debt = DebtState('invented-debt', D(Q), D(paid), D(written), D(adjustment), 60)
    return EconomicMonth('invented-company', 'invented-group', month, D('10000'), D(F), D(Q), D(Z),
                         (debt,), 'invented-perimeter', known or month_shift(month, 1) + 'T00:00:00Z',
                         ('invented-source:' + month,), (), POLICY_VERSION)


def sequence(values):
    return [economic(i, F=f) for i, f in enumerate(values)]


class EventTests(unittest.TestCase):
    def test_confirmation_reference_and_independence(self):
        rows = sequence(['2000'] * 3 + ['-1000', '-1000', '-1000', '-1000'])
        scan = scan_events(rows, scope=SCOPE)
        event = next(e for e in scan.events if e.sign == 'deterioration')
        self.assertEqual((event.onset, event.confirmation), ('2025-04-01', '2025-05-01'))
        self.assertEqual((event.B, event.F0, event.Q0, event.A), (D(10000), D(2000), D(0), D(1000)))
        self.assertEqual(event.branches, ('generation',))
        self.assertEqual(event.observable_at, '2025-06-01T00:00:00Z')
        self.assertTrue(event.source_refs)
        changed = rows + [economic(7, F='999999')]
        self.assertEqual(scan_events(changed, scope=SCOPE).events[0], event)

    def test_same_branch_not_alternating(self):
        rows = sequence(['2000'] * 3 + ['-1000', '2000'])
        rows[4] = economic(4, Q='1500')
        self.assertFalse(scan_events(rows, scope=SCOPE).events)

    def test_cumulative_payment_not_repeated_and_not_writeoff(self):
        base = [economic(i, Q='3000') for i in range(3)]
        paid = base + [economic(i, Q='1000', paid='2000') for i in (3, 4)]
        self.assertEqual([e.sign for e in scan_events(paid, scope=SCOPE).events], ['improvement'])
        writeoff = base + [economic(i, Q='1000', written='2000', adjustment='2000') for i in (3, 4)]
        self.assertFalse(scan_events(writeoff, scope=SCOPE).events)

    def test_labels_unknown_maturity_availability_and_full_followup(self):
        rows = sequence(['2000'] * 8)
        scan = scan_events(rows, scope=SCOPE)
        early = directional_label(scan, 'invented-company', '2025-03-01', 'deterioration',
                                  as_at='2025-07-31T23:59:59Z', scope=SCOPE)
        self.assertIsNone(early['value'])
        mature = directional_label(scan, 'invented-company', '2025-03-01', 'deterioration',
                                   as_at='2025-08-01T00:00:00Z', scope=SCOPE)
        self.assertEqual(mature['value'], 0)
        self.assertEqual(mature['observable_at'], '2025-08-01T00:00:00Z')
        delayed = sequence(['2000'] * 3 + ['-1000', '-1000'])
        delayed[-1] = replace(delayed[-1], known_on='2025-09-02T00:00:00Z')
        scan = scan_events(delayed, scope=SCOPE)
        label = directional_label(scan, 'invented-company', '2025-03-01', 'deterioration',
                                  as_at='2025-08-01T00:00:00Z', scope=SCOPE)
        self.assertIsNone(label['value'])
        label = directional_label(scan, 'invented-company', '2025-03-01', 'deterioration',
                                  as_at='2025-09-02T00:00:00Z', scope=SCOPE)
        self.assertEqual(label['value'], 1)

    def test_gap_unknown_no_restart_and_two_nonqualifying_reset(self):
        rows = sequence(['2000'] * 3 + ['-1000'] * 3 + ['2000'] * 3 + ['-1000'] * 2)
        rows.pop(5)
        scan = scan_events(rows, scope=SCOPE)
        events = [e for e in scan.events if e.sign == 'deterioration']
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].onset, '2025-10-01')
        self.assertEqual(events[1].reference_months, ('2025-07-01', '2025-08-01', '2025-09-01'))
        uninterrupted = sequence(['2000'] * 3 + ['-1000'] * 9)
        uninterrupted.pop(5)
        self.assertEqual(len(scan_events(uninterrupted, scope=SCOPE).events), 1)

    def test_conditional_recovered_persistent_mixed_unknown(self):
        for tail, expected in [(['2000', '2000'], 'recovered'), (['-1000', '-1000'], 'persistent'),
                               (['2000', '-1000'], 'mixed')]:
            scan = scan_events(sequence(['2000'] * 3 + ['-1000', '-1000'] + tail), scope=SCOPE)
            event = scan.events[0]
            label = conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)
            self.assertEqual(label['value'], expected)
            self.assertEqual(label['forecast_month'], '2025-05-01')
            self.assertEqual(label['forecast_available_at'], '2025-06-01T00:00:00Z')
            self.assertFalse(label['advance_detection'])
        rows = sequence(['2000'] * 3 + ['-1000'] * 4)
        rows.pop(5)
        scan = scan_events(rows, scope=SCOPE)
        self.assertIsNone(conditional_label(scan, scan.events[0], as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'])

    def test_conditional_overdue_reduction_requires_payment(self):
        for payment, expected in [(True, 'recovered'), (False, 'mixed')]:
            rows = [economic(i) for i in range(3)] + [economic(i, Q='2000') for i in (3, 4)]
            rows += [economic(i, Q='0', paid='2000' if payment else '0',
                              adjustment='0' if payment else '2000') for i in (5, 6)]
            scan = scan_events(rows, scope=SCOPE)
            event = next(e for e in scan.events if e.sign == 'deterioration')
            self.assertEqual(conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'], expected)

    def test_unknown_and_scope_rejections(self):
        rows = sequence(['2000'] * 8)
        rows[4] = replace(rows[4], F=None, reasons=('missing_source',))
        scan = scan_events(rows, scope=SCOPE)
        self.assertIsNone(directional_label(scan, 'invented-company', '2025-03-01', 'improvement',
                                          as_at='2025-10-01T00:00:00Z', scope=SCOPE)['value'])
        for altered in [replace(rows[0], policy_version='wrong'), replace(rows[0], group_id='final_test'),
                        replace(rows[0], month='2026-04-01')]:
            with self.assertRaises(ValueError):
                scan_events([altered], scope=SCOPE)
        with self.assertRaises(ValueError):
            scan_events(rows, scope=None)
        with self.assertRaises(ValueError):
            scan_events([rows[1], rows[0]], scope=SCOPE)
        with self.assertRaises(ValueError):
            scan_events([rows[0], rows[0]], scope=SCOPE)

    def test_confirmatory_refuses_before_callback_even_self_approved(self):
        accessed = []
        with self.assertRaisesRegex(PermissionError, 'new_test_missing'):
            confirmatory_entry(lambda: accessed.append(True), approval={'approved': True, 'untouched': True})
        self.assertEqual(accessed, [])


class AdapterTests(unittest.TestCase):
    def test_composed_core_due_not_cash_and_score_independence(self):
        core = evaluate(fixture(paid='0'))
        prior = evaluate(fixture(paid='0'), month='2025-05-01')
        row = adapt_core_outcome(core, previous=prior, group_id='invented-group', scope=SCOPE)
        self.assertEqual((row.R, row.F, row.Q, row.Z), (D(1000), D(200), D(0), D(0)))
        changed = copy.deepcopy(core)
        changed['score']['value'] = None
        changed['score']['dimensions'] = {}
        self.assertEqual(adapt_core_outcome(changed, previous=prior, group_id='invented-group', scope=SCOPE), row)
        self.assertTrue(any('receipt-6' in ref for ref in row.source_refs))
        self.assertTrue(any('ap-6' in ref for ref in row.source_refs))
        self.assertEqual(row.known_on, '2025-06-30T23:59:59.999999Z')

    def test_core_missing_coverage_abstains_and_future_append_no_change(self):
        data = fixture()
        before = adapt_core_outcome(evaluate(data), previous=evaluate(data, month='2025-05-01'),
                                   group_id='invented-group', scope=SCOPE)
        data.records['cash_movement'][-1]['amount_native'] = '-99999'
        after = adapt_core_outcome(evaluate(data), previous=evaluate(data, month='2025-05-01'),
                                  group_id='invented-group', scope=SCOPE)
        self.assertEqual(before, after)
        data.obligation_coverage.clear()
        row = adapt_core_outcome(evaluate(data), previous=None, group_id='invented-group', scope=SCOPE)
        self.assertIsNone(row.F)
        self.assertIsNone(row.Q)
        self.assertTrue(row.reasons)

    def test_crossing_month_intramonth_changes_do_not_invent_Z(self):
        data = fixture(paid='300', late_days=40)
        core = evaluate(data)
        prior = evaluate(data, month='2025-05-01')
        row = adapt_core_outcome(core, previous=prior, group_id='invented-group', scope=SCOPE)
        self.assertEqual(row.Q, D(500))
        self.assertIsNone(row.Z)
        self.assertIn('newly_late_timing_unavailable', row.reasons)


class ExtendedEventTests(unittest.TestCase):
    def test_composed_core_history_to_event(self):
        data = fixture(due='8000', paid='8000', receipts=['10000'] * 3 + ['6000'] * 4, opening='100000')
        cores = [evaluate(data, month=month_shift('2025-01-01', i)) for i in range(7)]
        rows = [adapt_core_outcome(c, previous=cores[i - 1] if i else None, group_id='invented-group', scope=SCOPE)
                for i, c in enumerate(cores)]
        self.assertTrue(all(r.complete for r in rows), [r.reasons for r in rows])
        scan = scan_events(rows, scope=SCOPE)
        self.assertEqual(scan.events[0].onset, '2025-04-01')
        self.assertIsNone(cores[3]['score']['value'])
        self.assertEqual(scan.events[0].F0, D(2000))
        self.assertEqual(scan.events[0].observable_at, '2025-06-01T00:00:00Z')

    def test_payment_plus_cancellation_not_payment_recovery(self):
        rows = [economic(i, Q='5000') for i in range(3)]
        rows += [economic(i, Q='2000', paid='1000', adjustment='-2000') for i in (3, 4)]
        self.assertFalse(scan_events(rows, scope=SCOPE).events)

    def test_gap_in_candidate_cannot_restart_hidden_episode(self):
        rows = sequence(['2000'] * 3 + ['-1000'] * 8)
        rows.pop(4)
        scan = scan_events(rows, scope=SCOPE)
        self.assertFalse(scan.events)
        label = directional_label(scan, 'invented-company', '2025-07-01', 'deterioration',
                                  as_at='2026-01-01T00:00:00Z', scope=SCOPE)
        self.assertIsNone(label['value'])

    def test_opposing_events_can_coexist_and_boundaries(self):
        rows = [economic(i, F='500', Q='3000') for i in range(3)]
        rows += [economic(i, F='-500', Q='1000', paid='2000') for i in (3, 4)]
        self.assertEqual({e.sign for e in scan_events(rows, scope=SCOPE).events}, {'improvement', 'deterioration'})
        rows[-1] = replace(rows[-1], Z=D(1000))
        self.assertEqual({e.sign for e in scan_events(rows, scope=SCOPE).events}, {'deterioration'})
        large = [replace(r, R=D(40000)) for r in sequence(['2000'] * 3 + ['-1', '-1'])]
        self.assertEqual(scan_events(large, scope=SCOPE).events[0].A, D(2000))

    def test_unknown_old_availability_never_event_with_null_timestamp(self):
        rows = sequence(['2000'] * 4 + ['-1000'] * 3)
        rows[0] = replace(rows[0], known_on=None)
        scan = scan_events(rows, scope=SCOPE)
        for event in scan.events:
            self.assertIsNotNone(event.observable_at)


class CandidateGapCorrectionTests(unittest.TestCase):
    def test_unconfirmed_gap_stays_unknown_after_gap_leaves_label_window(self):
        for sign, changed in [('deterioration', '-1000'), ('improvement', '4000')]:
            for gap in ('absent', 'unknown_Z', 'unknown_F'):
                with self.subTest(sign=sign, gap=gap):
                    rows = sequence(['2000'] * 3 + [changed] * 9)
                    if gap == 'absent':
                        rows.pop(4)
                    elif gap == 'unknown_Z':
                        rows[4] = replace(rows[4], Z=None, reasons=('newly_late_timing_unavailable',))
                    else:
                        rows[4] = replace(rows[4], F=None, reasons=('missing_source',))
                    scan = scan_events(rows, scope=SCOPE)
                    label = directional_label(scan, 'invented-company', '2025-08-01', sign,
                                              as_at='2026-01-01T00:00:00Z', scope=SCOPE)
                    required = [scan.rows['invented-company', month_shift('2025-08-01', i)] for i in range(-2, 5)]
                    self.assertTrue(all(r.complete for r in required))
                    self.assertIsNone(label['value'])
                    self.assertIn('unknown_followup', label['reasons'])
                    self.assertFalse([e for e in scan.events if e.sign == sign])
                    self.assertEqual(scan.decisions['invented-company', sign, '2025-10-01'], 'unknown')

    def test_unknown_candidate_resets_only_after_two_fixed_reference_failures(self):
        rows = sequence(['2000'] * 3 + ['-1000'] * 6 + ['2000'] * 2 + ['-1000'] * 2)
        rows.pop(4)
        scan = scan_events(rows, scope=SCOPE)
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-09-01'], 'unknown')
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-10-01'], 'unknown')
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-11-01'], 'no_event')
        events = [e for e in scan.events if e.sign == 'deterioration']
        self.assertEqual([e.onset for e in events], ['2025-12-01'])
        self.assertEqual(events[0].reference_months, ('2025-09-01', '2025-10-01', '2025-11-01'))

    def test_gap_breaks_nonqualifying_reset_streak(self):
        rows = sequence(['2000'] * 3 + ['-1000', '-1000', '2000', '2000', '-1000', '2000', '2000', '-1000', '-1000'])
        rows.pop(4)
        rows = [replace(r, Z=None, reasons=('newly_late_timing_unavailable',)) if r.month == '2025-07-01' else r for r in rows]
        scan = scan_events(rows, scope=SCOPE)
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-08-01'], 'unknown')
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-09-01'], 'unknown')
        self.assertEqual(scan.decisions['invented-company', 'deterioration', '2025-10-01'], 'no_event')
        self.assertEqual([e.onset for e in scan.events if e.sign == 'deterioration'], ['2025-11-01'])


def prepeak_recovery_rows(action):
    rows = []
    for i in range(7):
        paid, remaining, adjustment, written, replacements = D(0), D(3000), D(0), D(0), ()
        days = (0, 0, 16, 46, 77, 107, 138)[i]
        if i >= 3:
            paid, remaining = D(1500), D(1500)
        if i >= 5:
            if action == 'payment':
                paid, remaining = D(2500), D(500)
            elif action in ('cancel', 'renegotiate'):
                remaining, adjustment = D(500), D(-1000)
                if action == 'renegotiate':
                    replacements = ('invented-replacement',)
            elif action == 'partial_payment_and_cancel':
                paid, remaining, adjustment = D(2000), D(500), D(-500)
            elif action == 'writeoff':
                written = D(1000)
        debt = DebtState('invented-prepeak-debt', remaining, paid, written, adjustment, days, replacements)
        q = remaining if days > 30 else D(0)
        row = replace(economic(i), Q=q, Z=D(1500) if i == 3 else D(0), debts=(debt,),
                      source_refs=(f'invented-legal-face-3000:{i}', f'invented-allocated-prepeak-payment-1500:{i}',
                                   f'invented-{action}-ledger:{i}'))
        rows.append(row)
    return rows


class PeakPaymentCorrectionTests(unittest.TestCase):
    def test_prepeak_payment_does_not_credit_postpeak_cancellation(self):
        for action in ('cancel', 'renegotiate', 'partial_payment_and_cancel'):
            with self.subTest(action=action):
                rows = prepeak_recovery_rows(action)
                for row in rows:
                    debt = row.debts[0]
                    self.assertEqual(debt.remaining, D(3000) + debt.nonpayment_adjustment - debt.paid)
                scan = scan_events(rows, scope=SCOPE)
                event = next(e for e in scan.events if e.sign == 'deterioration')
                self.assertEqual((event.onset, event.confirmation), ('2025-04-01', '2025-05-01'))
                result = conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)
                self.assertEqual(result['value'], 'mixed')
                self.assertTrue(any(f'invented-{action}-ledger' in r for r in result['source_refs']))

    def test_postpeak_allocated_payment_recovers_without_repeated_payment(self):
        rows = prepeak_recovery_rows('payment')
        self.assertEqual(rows[3].debts[0].paid, D(1500))
        self.assertEqual(rows[5].debts[0].paid, rows[6].debts[0].paid)
        scan = scan_events(rows, scope=SCOPE)
        event = next(e for e in scan.events if e.sign == 'deterioration')
        self.assertEqual(conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'], 'recovered')

    def test_writeoff_does_not_reduce_legal_exposure_or_become_payment(self):
        rows = prepeak_recovery_rows('writeoff')
        self.assertEqual(rows[5].Q, D(1500))
        self.assertEqual(rows[5].debts[0].paid, D(1500))
        self.assertEqual(rows[5].debts[0].written_off, D(1000))
        scan = scan_events(rows, scope=SCOPE)
        event = next(e for e in scan.events if e.sign == 'deterioration')
        self.assertEqual(conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'], 'persistent')

    def test_missing_recovery_amount_or_crossing_evidence_stays_unknown(self):
        for missing in ('Q', 'Z'):
            rows = prepeak_recovery_rows('payment')
            rows[5] = replace(rows[5], **{missing: None}, reasons=('missing_source',))
            scan = scan_events(rows, scope=SCOPE)
            event = next(e for e in scan.events if e.sign == 'deterioration')
            self.assertIsNone(conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'])

    def test_unreconciled_amendment_cannot_be_counted_as_repayment(self):
        rows = prepeak_recovery_rows('payment')
        for i in (5, 6):
            debt = replace(rows[i].debts[0], nonpayment_adjustment=D(-1000))
            rows[i] = replace(rows[i], debts=(debt,))
        scan = scan_events(rows, scope=SCOPE)
        event = next(e for e in scan.events if e.sign == 'deterioration')
        self.assertEqual(conditional_label(scan, event, as_at='2025-08-01T00:00:00Z', scope=SCOPE)['value'], 'mixed')

    def test_core_adapter_preserves_payment_writeoff_amendment_provenance(self):
        from tests.test_financial_v3_engine import record
        data = fixture(paid='300', late_days=40)
        for kind, amount in [('writeoff', '100'), ('cancel', '200')]:
            data.records['amendment'].append(record(f'invented-{kind}', '2025-06-25T00:00:00Z',
                amendment_id=f'invented-{kind}', obligation_id='ap-6', currency='EUR', kind=kind,
                amount_native=amount, effective_at='2025-06-25T00:00:00Z', replacement_obligation_id=None))
        row = adapt_core_outcome(evaluate(data), previous=evaluate(data, month='2025-05-01'),
                                 group_id='invented-group', scope=SCOPE)
        debt = next(d for d in row.debts if d.obligation_id == 'ap-6')
        self.assertEqual((debt.remaining, debt.paid, debt.written_off, debt.nonpayment_adjustment),
                         (D(300), D(300), D(100), D(-200)))
        self.assertEqual(row.Q, D(300))
        self.assertIsNone(row.Z)
        for key in ('alloc-6', 'pay-6', 'invented-cancel', 'invented-writeoff'):
            self.assertTrue(any(key in ref for ref in row.source_refs))


if __name__ == '__main__':
    unittest.main()
