"""In-memory equivalent of the estimator's observed-perimeter monthly inputs."""
import math
import statistics
from copy import deepcopy
from decimal import localcontext

from scripts.financial_v3_cash import EvidenceView, fx_rate, month_shift
from scripts.financial_v3_contract import (
    decimal_amount,
    month_end,
    source_availability,
    utc_timestamp,
)

CASH_PRODUCTS = ('checking', 'saving', 'wallet')
AMBIGUOUS = ('unknown', 'transfer', 'adjustment')
SERVICE_RULES = ('cat:debt_repayment', 'transf:debt_repayment', 'cat:interest_charge', 'transf:interest_charge')
BASE_FEATURES = ('receipts_known', 'payments_known', 'operating_low', 'operating_high',
                 'classification_coverage', 'eur_coverage')
FEATURE_NAMES = tuple(k for name in BASE_FEATURES for k in (name, name + '_mean3')) + (
    'ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows',
    'service_paid_known')


def visible(source, context):
    return context['view'] == 'reconstructed_retrospective' or source_availability(source, context['as_of']).eligible


def effective_records(snapshot, context):
    records = deepcopy(snapshot['records'])
    classifications = {r['movement_id']: r for r in snapshot['classifications']}
    for row in records['cash_movement']:
        evidence = classifications.get(row['movement_id'])
        if evidence is not None and not visible(evidence['source_ref'], context):
            # Cash fact stays visible; a classification learned later does not travel backwards.
            row['economic_bucket'], row['classification_evidence'] = 'unknown', None
    return records


def _number(value):
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError('Invalid model number') from None
    if not math.isfinite(result):
        raise ValueError('Nonfinite model number')
    return result


def _eur(view, row, at):
    value = decimal_amount(row['amount_native'])
    if row['currency'] == 'EUR':
        return _number(value)
    if at is None:
        return None
    rate = fx_rate(view, row['currency'], 'EUR', utc_timestamp(at), 'transaction', [])
    if rate is None:
        return None
    with localcontext() as precision:
        precision.prec = 80
        return _number(value / rate)


def _flow(view, month, classifications):
    accounts = {r['account_id']: r for r in view.rows('account')}
    rows = [r for r in view.rows('cash_movement') if r['booking_status'] == 'booked'
            and r['booked_at'][:7] == month[:7] and accounts[r['account_id']]['product_type'] in CASH_PRODUCTS]
    values = [(r, _eur(view, r, r['booked_at'])) for r in rows]
    unknown_eur = sum(value is None for _, value in values)
    def ambiguous(row):
        return row['economic_bucket'] in AMBIGUOUS and decimal_amount(row['amount_native']) != 0
    def total(bucket, incoming):
        return math.fsum(abs(value) for row, value in values if value is not None
                         and row['economic_bucket'] == bucket and (value > 0 if incoming else value < 0))
    receipts, payments = total('operating', True), total('operating', False)
    uncertain_in = math.fsum(value for row, value in values if value is not None and value > 0 and ambiguous(row))
    uncertain_out = math.fsum(-value for row, value in values if value is not None and value < 0 and ambiguous(row))
    bounded = bool(rows) and not any(value is None and (r['economic_bucket'] == 'operating' or ambiguous(r)) for r, value in values)
    service = math.fsum(abs(value) for row, value in values if value is not None and value < 0
                        and row['economic_bucket'] == 'financing'
                        and classifications.get(row['movement_id'], {}).get('rule_id') in SERVICE_RULES)
    return {'receipts_known': receipts if rows else None, 'payments_known': payments if rows else None,
            'operating_low': receipts - payments - uncertain_out if bounded else None,
            'operating_high': receipts - payments + uncertain_in if bounded else None,
            'classification_coverage': (len(rows) - sum(ambiguous(r) for r in rows)) / len(rows) if rows else None,
            'eur_coverage': (len(rows) - unknown_eur) / len(rows) if rows else None,
            'service_paid_known': service if rows else None, 'active': bool(rows), 'missing_eur': unknown_eur}


def _documents(view, invoices, month):
    selected = [r for r in invoices if r['issued_on'] <= str(month_end(month)) and decimal_amount(r['amount_native']) != 0]
    keys = ('ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows')
    if not selected:
        return dict.fromkeys(keys)
    valued = [(r, _eur(view, r, r['fx_at'])) for r in selected]
    due = [(r, amount) for r, amount in valued if r['side'] == 'ap'
           and r['due_on'] is not None and month <= r['due_on'] <= str(month_end(month))]
    unknown = sum(r['side'] == 'ap' and (r['due_on'] is None or r['due_on'] < r['issued_on']) for r in selected)
    missing = sum(value is None for _, value in due)
    result = {'ap_due_face': math.fsum(value for _, value in due if value is not None) if not unknown and not missing else None,
              'unknown_due_rows': unknown, 'due_missing_eur_rows': missing}
    for side in ('ap', 'ar'):
        result[side + '_issued_known'] = math.fsum(value for r, value in valued if value is not None
            and r['side'] == side and month <= r['issued_on'] <= str(month_end(month)))
    return result


def prepare(snapshot, context, records):
    view = EvidenceView(records, context)
    classifications = {r['movement_id']: r for r in snapshot['classifications'] if visible(r['source_ref'], context)}
    invoices = [r for r in snapshot['invoices'] if visible(r['source_ref'], context)]
    month = context['month']
    history = [_flow(view, month_shift(month, i), classifications) for i in (-2, -1, 0)]
    values = {}
    for name in BASE_FEATURES:
        nums = [r[name] for r in history]
        values[name] = nums[-1]
        values[name + '_mean3'] = statistics.mean(nums) if all(v is not None for v in nums) else None
    values.update(_documents(view, invoices, month))
    values['service_paid_known'] = history[-1]['service_paid_known']
    reasons = []
    if not all(r['active'] for r in history):
        reasons.append('three_contiguous_cash_months_missing')
    if any(r['missing_eur'] for r in history):
        reasons.append('eur_coverage_incomplete')
    receipts = [r['receipts_known'] for r in history]
    if any(v is None for v in receipts) or math.fsum(v or 0 for v in receipts) <= 0:
        reasons.append('positive_observed_receipt_reference_missing')
    start = month_shift(month, -2)
    accounts = {r['account_id']: r for r in records['account']}
    if any(r['booking_status'] == 'booked'
           and accounts[r['account_id']]['product_type'] in CASH_PRODUCTS
           and start <= r['booked_at'][:10] <= context['as_of']
           for r in view.excluded.get('cash_movement', [])):
        reasons.append('unverified_input_availability')
    return {'values': {name: values[name] for name in FEATURE_NAMES}, 'eligible': not reasons, 'reasons': sorted(reasons),
            'missing_features': [name for name in FEATURE_NAMES if values[name] is None],
            'currency_basis': 'EUR', 'scope': 'observed_cash_perimeter',
            'feature_order': list(FEATURE_NAMES)}
