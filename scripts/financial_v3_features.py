from decimal import Decimal, localcontext
import math

from scripts.financial_v3_contract import canonical_hash, finite_number, reason_codes, require, utc_timestamp
from scripts.financial_v3_cash import ZERO, month_shift, text, timestamp


DIMENSIONS = ('generation', 'liquidity', 'debt', 'growth', 'stability')


def combined_metadata(parts):
    known = [p.get('known_on') for p in parts]
    return {'source_refs': sorted({s for p in parts for s in p.get('source_refs', [])}),
            'known_on': timestamp(max(utc_timestamp(t) for t in known)) if known and all(known) else None,
            'availability_verified': bool(parts) and all(p.get('availability_verified', False) for p in parts)}


def _mean(values):
    return sum(values, ZERO) / Decimal(len(values))


def _number(value):
    return None if value is None else finite_number(float(value))


def build_features(cash_history, obligation_history, month):
    with localcontext() as precision:
        precision.prec = 80
        return _build_features(cash_history, obligation_history, month)


def _build_features(cash_history, obligation_history, month):
    cash = {r['month']: r for r in cash_history}
    obligations = {r['month']: r for r in obligation_history}
    require(len(cash) == len(cash_history) and len(obligations) == len(obligation_history), 'duplicate_conflict: company month')
    months = [month_shift(month, i) for i in range(-5, 1)]
    current_cash, current_obligations = cash.get(month), obligations.get(month)
    require(current_cash is not None and current_obligations is not None, 'Missing current month objects')
    history_reasons, receipts, generations, signatures, parts = [], [], [], [], []
    receipt_series, generation_series = [], []
    for m in months:
        receipt_series.append(None)
        generation_series.append(None)
        c, o = cash.get(m), obligations.get(m)
        if c is None or o is None:
            history_reasons.append('insufficient_history')
            continue
        parts.extend((c, o))
        history_reasons.extend(c['flow_reasons'] + o['operating_reasons'])
        if c['operating_receipts'] is None or o['operating_due'] is None:
            history_reasons.append('insufficient_history')
            continue
        receipt, due = Decimal(c['operating_receipts']), Decimal(o['operating_due'])
        require(receipt >= 0, 'Negative operating receipt total')
        receipts.append(receipt)
        generations.append(receipt - due)
        receipt_series[-1], generation_series[-1] = text(receipt), text(receipt - due)
        signatures.append(canonical_hash({'accounts': c['perimeter_accounts'], 'obligations': o['comparability'], 'currency': c['currency']}))
    if len(set(signatures)) > 1:
        history_reasons.append('coverage_changed')
    complete = len(receipts) == 6 and not history_reasons
    reference = _mean(receipts) if complete else None
    recent_generation = _mean(generations[-3:]) if complete else None
    recent_receipts = _mean(receipts[-3:]) if complete else None
    prior_receipts = _mean(receipts[:3]) if complete else None
    generation_std = (_mean([(f - _mean(generations)) ** 2 for f in generations])).sqrt() if complete else None
    if reference is not None and reference <= 0:
        history_reasons.append('zero_reference')
    growth_reasons = list(history_reasons)
    growth = None
    if complete:
        if prior_receipts > 0:
            growth = (recent_receipts - prior_receipts) / prior_receipts
        else:
            growth_reasons.append('zero_reference')
    actual_cash = None if current_cash['unrestricted'] is None else Decimal(current_cash['unrestricted'])
    unpaid_due, principal, service, exposure = [None if current_obligations[key] is None else Decimal(current_obligations[key])
        for key in ('unpaid_due', 'financial_principal', 'next_service_mean', 'weighted_exposure')]
    adjusted = None if actual_cash is None or unpaid_due is None else actual_cash - unpaid_due
    dimension_reasons = {key: list(history_reasons) for key in DIMENSIONS}
    dimension_reasons['growth'] = growth_reasons
    dimension_reasons['liquidity'].extend(current_cash['reasons'] + current_obligations['reasons'])
    dimension_reasons['debt'].extend(current_obligations['reasons'])
    reasons = [r for rs in dimension_reasons.values() for r in rs] + current_cash['reasons'] + current_obligations['reasons']
    raw = {'F': _number(recent_generation), 'S': _number(reference), 'C': _number(adjusted), 'D': _number(principal),
           'J': _number(service), 'E': _number(exposure), 'recent_receipts': _number(recent_receipts),
           'prior_receipts': _number(prior_receipts), 'F_std': _number(generation_std)}
    mom = yoy = None
    optional_reasons = {'mom': [], 'yoy': []}
    mom_rows = [cash.get(month_shift(month, i)) for i in (-1, 0)]
    if all(r is not None and r['operating_receipts'] is not None and not r['flow_reasons'] for r in mom_rows):
        prior_mom, recent_mom = [Decimal(r['operating_receipts']) for r in mom_rows]
        if mom_rows[0]['perimeter_accounts'] != mom_rows[1]['perimeter_accounts'] or mom_rows[0]['currency'] != mom_rows[1]['currency']:
            optional_reasons['mom'].append('coverage_changed')
        elif prior_mom > 0:
            mom = (recent_mom - prior_mom) / prior_mom
        else:
            optional_reasons['mom'].append('zero_reference')
    else:
        optional_reasons['mom'].append('insufficient_history')
    yoy_rows = [cash.get(month_shift(month, i)) for i in range(-14, 1)]
    if all(r is not None and r['operating_receipts'] is not None and not r['flow_reasons'] for r in yoy_rows):
        if all(r['perimeter_accounts'] == current_cash['perimeter_accounts'] and r['currency'] == current_cash['currency'] for r in yoy_rows):
            year_prior = _mean([Decimal(r['operating_receipts']) for r in yoy_rows[:3]])
            year_recent = _mean([Decimal(r['operating_receipts']) for r in yoy_rows[-3:]])
            if year_prior > 0:
                yoy = (year_recent - year_prior) / year_prior
            else:
                optional_reasons['yoy'].append('zero_reference')
        else:
            optional_reasons['yoy'].append('coverage_changed')
    else:
        optional_reasons['yoy'].append('insufficient_history')
    require(all(value is None or math.isfinite(value) for value in raw.values()), 'Nonfinite feature')
    return {'month': month, 'reference_receipts': text(reference), 'generation_recent': text(recent_generation),
            'actual_unrestricted_cash': text(actual_cash), 'unpaid_due': text(unpaid_due), 'adjusted_cash': text(adjusted),
            'adjusted_cash_is_bank_balance': False, 'financial_principal': text(principal), 'next_service_mean': text(service),
            'weighted_exposure': text(exposure), 'growth_3v3': _number(growth), 'growth_mom': _number(mom), 'growth_yoy': _number(yoy),
            'growth_kind': 'nominal_comparable_operating_receipts_not_sales', 'optional_growth_reasons': optional_reasons,
            'growth_evidence': {'mom': {**combined_metadata([r for r in mom_rows if r is not None]), 'reasons': optional_reasons['mom']},
                                'yoy': {**combined_metadata([r for r in yoy_rows if r is not None]), 'reasons': optional_reasons['yoy']},
                                '3v3': {**combined_metadata([cash[m] for m in months if m in cash]), 'reasons': list(reason_codes(*growth_reasons))}},
            'receipt_series': receipt_series, 'generation_series': generation_series,
            'months': months, 'raw': raw, 'dimension_reasons': {k: list(reason_codes(*r)) for k, r in dimension_reasons.items()},
            'reasons': list(reason_codes(*reasons)),
            'perimeter_id': canonical_hash({'accounts': current_cash['perimeter_accounts'], 'obligations': current_obligations['comparability'],
                                             'currency': current_cash['currency']}), **combined_metadata(parts)}
