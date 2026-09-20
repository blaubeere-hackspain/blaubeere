from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext

from scripts.financial_v3_contract import (
    canonical_bytes, currency_code, decimal_amount, default_policy, identifier, iso_date,
    month_date, month_end, monthly_cutoff, reason_codes, require, utc_timestamp, validate_source,
)
from scripts.financial_v3_cash import ZERO, availability_reasons, month_shift, provenance, text


KINDS = ('operating_ap', 'financial_principal', 'financial_interest', 'other_payable', 'ar')
COMPONENTS = {'operating_ap': 'trade', 'financial_principal': 'principal', 'financial_interest': 'interest',
              'other_payable': 'fee', 'ar': 'trade'}


@dataclass(frozen=True)
class ObligationCoverage:
    company_id: str
    source_kind: str
    from_inclusive: str
    to_exclusive: str
    history_from: str
    settlements_to_exclusive: str
    schedule_to_exclusive: str
    comparable_id: str
    source_ref: dict

    def __post_init__(self):
        identifier(self.company_id)
        identifier(self.comparable_id)
        require(self.source_kind in ('operating_ap', 'financial_debt', 'ar'), 'Unknown obligation coverage kind')
        for value in (self.from_inclusive, self.to_exclusive, self.history_from,
                      self.settlements_to_exclusive, self.schedule_to_exclusive):
            iso_date(value)
        require(self.from_inclusive < self.to_exclusive, 'Empty obligation coverage')
        require(self.history_from <= self.from_inclusive, 'Settlement history starts after coverage')
        validate_source(self.source_ref)


@dataclass(frozen=True)
class PrincipalObservation:
    company_id: str
    financial_product_id: str
    currency: str
    as_of: str
    amount_native: str
    source_ref: dict

    def __post_init__(self):
        identifier(self.company_id)
        identifier(self.financial_product_id)
        currency_code(self.currency)
        monthly_cutoff(self.as_of)
        require(decimal_amount(self.amount_native) >= 0, 'Negative principal observation')
        validate_source(self.source_ref)


def mora_multiplier(days):
    require(type(days) is int and days >= 0, 'Late days must be nonnegative integer')
    if days == 0:
        return ZERO
    for bucket in default_policy()['mora']['buckets']:
        if days >= bucket['from_days'] and (bucket['to_days'] is None or days <= bucket['to_days']):
            return Decimal(str(bucket['multiplier']))
    raise ValueError('Uncovered mora age')


def _active_versions(view, as_of):
    grouped = {}
    for row in view.rows('obligation'):
        if row['effective_from'] <= as_of:
            grouped.setdefault(row['obligation_id'], []).append(row)
    selected = {}
    for oid, versions in grouped.items():
        signatures = {canonical_bytes({k: v for k, v in r.items() if k not in (
            'source_ref', 'version_id', 'effective_from', 'effective_to')}) for r in versions}
        require(len(signatures) == 1, 'debt_vintage_missing: changed terms require explicit amendment and replacement lineage')
        selected[oid] = max(versions, key=lambda r: (r['effective_from'], r['source_ref']['known_on'] or '', r['version_id']))
    invoices, installments, products = {}, {}, {}
    for oid, row in selected.items():
        if row['invoice_id']:
            key = (row['invoice_id'], row['kind'])
            require(key not in invoices, 'duplicate_conflict: duplicated invoice liability')
            invoices[key] = oid
        if row['kind'] in ('financial_principal', 'financial_interest'):
            require(row['financial_product_id'] is not None, 'Financial product ownership unresolved')
            key = (row['financial_product_id'], row['installment_id'], row['kind'])
            require(key not in installments, 'duplicate_conflict: duplicated installment component')
            installments[key] = oid
            if row['kind'] == 'financial_principal':
                products.setdefault(row['financial_product_id'], []).append(row)
    for rows in products.values():
        require(not (len(rows) > 1 and any(r['installment_id'] is None for r in rows)),
                'duplicate_conflict: principal stock and installments cannot both be legal obligations')
    return selected


def _allocations(view, obligations, cutoff):
    payments = {r['movement_id']: r for r in view.rows('cash_movement')}
    allocated, by_obligation, reasons = {}, {}, []
    for row in view.rows('settlement'):
        if utc_timestamp(row['paid_at']) >= cutoff:
            continue
        obligation = obligations.get(row['obligation_id'])
        require(obligation is not None, 'Allocation obligation ownership unresolved')
        require(row['currency'] == obligation['currency'], 'Allocation currency mismatch')
        require(row['component'] == COMPONENTS[obligation['kind']], 'Allocation component mismatch')
        require(row['paid_at'][:10] >= obligation['effective_from'], 'Allocation predates obligation')
        if row['allocation_policy'] != 'explicit' or row['movement_id'] is None:
            reasons.append('allocation_conflict')
            continue
        payment = payments.get(row['movement_id'])
        require(payment is not None, 'Allocation payment ownership unresolved')
        require(payment['currency'] == row['currency'], 'Payment allocation currency mismatch')
        require(payment['booking_status'] == 'booked', 'Allocation requires actual booked payment')
        require(utc_timestamp(payment['booked_at']) == utc_timestamp(row['paid_at']), 'Payment booking/allocation time mismatch')
        require(row['allocation_id'] in payment['obligation_allocation_ids'], 'Payment allocation link missing')
        amount = decimal_amount(payment['amount_native'])
        require(amount > 0 if obligation['kind'] == 'ar' else amount < 0, 'Payment sign mismatches legal role')
        allocated[row['movement_id']] = allocated.get(row['movement_id'], ZERO) + decimal_amount(row['amount_native'])
        require(allocated[row['movement_id']] <= abs(amount), 'allocation_conflict: allocated beyond payment')
        by_obligation.setdefault(row['obligation_id'], []).append(row)
    known_ids = {r['allocation_id'] for r in view.rows('settlement') if utc_timestamp(r['paid_at']) < cutoff}
    for row in payments.values():
        if utc_timestamp(row['booked_at']) < cutoff and any(a not in known_ids for a in row['obligation_allocation_ids']):
            reasons.append('allocation_conflict')
    return by_obligation, reasons


def _amendments(view, obligations, cutoff):
    by_obligation, replacements = {}, set()
    for row in view.rows('amendment'):
        if utc_timestamp(row['effective_at']) >= cutoff:
            continue
        original = obligations.get(row['obligation_id'])
        require(original is not None, 'Amendment obligation ownership unresolved')
        require(original['currency'] == row['currency'], 'Amendment currency mismatch')
        require(decimal_amount(row['amount_native']) >= 0, 'Amendment amount must be nonnegative; kind defines direction')
        require(row['effective_at'][:10] >= original['effective_from'], 'Amendment predates obligation')
        if row['kind'] == 'renegotiate':
            replacement = obligations.get(row['replacement_obligation_id'])
            require(replacement is not None and replacement['obligation_id'] != original['obligation_id'], 'Replacement lineage unresolved')
            require(replacement['obligation_id'] not in replacements, 'Replacement counted twice')
            require(replacement['currency'] == original['currency'] and replacement['kind'] == original['kind'], 'Replacement component mismatch')
            require(replacement['effective_from'] == row['effective_at'][:10], 'Replacement must take effect with amendment')
            require(decimal_amount(replacement['principal_or_face_amount']) == decimal_amount(row['amount_native']), 'Replacement face differs from transferred legal balance')
            replacements.add(replacement['obligation_id'])
        by_obligation.setdefault(row['obligation_id'], []).append(row)
    for origin in by_obligation:
        seen, todo = set(), [origin]
        while todo:
            oid = todo.pop()
            require(oid not in seen, 'Cyclic amendment lineage')
            seen.add(oid)
            todo.extend(r['replacement_obligation_id'] for r in by_obligation.get(oid, []) if r['kind'] == 'renegotiate')
    return by_obligation


def obligation_month(view, month, coverage, principal_observations, reporting_currency='EUR'):
    with localcontext() as precision:
        precision.prec = 80
        return _obligation_month(view, month, coverage, principal_observations, currency_code(reporting_currency))


def _obligation_month(view, month, coverage, observations, reporting):
    end_date, start = str(month_end(month)), month
    next_start, horizon_end = month_shift(month, 1), month_shift(month, 4)
    cutoff = monthly_cutoff(end_date)
    obligations = _active_versions(view, end_date)
    allocations, allocation_reasons = _allocations(view, obligations, cutoff)
    payments = {r['movement_id']: r for r in view.rows('cash_movement')}
    amendments = _amendments(view, obligations, cutoff)
    sources, reasons = [], list(allocation_reasons)
    operating_reasons, ar_reasons, comparable = [], [], []
    scope_issues = {}
    for kind in ('operating_ap', 'financial_debt', 'ar'):
        scoped = [r for r in obligations.values() if (r['kind'] in ('financial_principal', 'financial_interest') if kind == 'financial_debt'
                  else r['kind'] in ('operating_ap', 'other_payable') if kind == 'operating_ap' else r['kind'] == 'ar')]
        scope_reasons = []
        view.attestation(kind, start, next_start, sources, scope_reasons, bool(scoped))
        candidates = [c for c in coverage if c.company_id == view.company and c.source_kind == kind
                      and view.visible(c.source_ref) and c.from_inclusive <= start and c.to_exclusive >= next_start]
        certificates = {}
        for candidate in candidates:
            identity = (candidate.from_inclusive, candidate.to_exclusive)
            require(identity not in certificates or certificates[identity] == candidate, 'duplicate_conflict: obligation coverage certificates')
            certificates[identity] = candidate
        if not candidates:
            scope_reasons.append('settlement_history_missing')
        else:
            chosen = min(candidates, key=lambda c: (c.to_exclusive, c.from_inclusive, c.source_ref['record_id']))
            sources.append(chosen.source_ref)
            comparable.append([kind, chosen.comparable_id])
            if chosen.settlements_to_exclusive < next_start or any(r['effective_from'] < chosen.history_from for r in scoped):
                scope_reasons.append('settlement_history_missing')
            if kind == 'financial_debt' and chosen.schedule_to_exclusive < horizon_end:
                scope_reasons.append('debt_vintage_missing')
        scope_issues[kind] = scope_reasons
        if kind == 'operating_ap':
            operating_reasons.extend(scope_reasons)
        if kind == 'ar':
            ar_reasons.extend(scope_reasons)
        else:
            reasons.extend(scope_reasons)
    unpaid, due_amounts, paid_by_kind = ({k: ZERO for k in KINDS} for _ in range(3))
    total_due = overdue = weighted = non_due = paid_month = writeoff = next_service = ZERO
    items, product_stocks, due_dates = [], {}, []
    aging = {str(b['from_days']): {'from_days': b['from_days'], 'to_days': b['to_days'], 'multiplier': b['multiplier'], 'actual': ZERO, 'weighted': ZERO}
             for b in default_policy()['mora']['buckets']}
    for oid, row in sorted(obligations.items()):
        sources.append(row['source_ref'])
        local_reasons = []
        if not row['role_verified']:
            local_reasons.append('role_unverified')
        if row['due_date'] is None:
            local_reasons.append('due_date_unknown')
        if row['effective_to'] is not None and row['effective_to'] <= end_date:
            local_reasons.append('debt_vintage_missing')
        if row['currency'] != reporting:
            local_reasons.append('fx_missing')
        face = decimal_amount(row['principal_or_face_amount'])
        remaining, paid, written, adjusted_due = face, ZERO, ZERO, face
        events = [(utc_timestamp(r['paid_at']), 1, r) for r in allocations.get(oid, [])]
        payment_sources = [payments[r['movement_id']]['source_ref'] for r in allocations.get(oid, [])]
        sources.extend(payment_sources)
        events.extend((utc_timestamp(r['effective_at']), 0, r) for r in amendments.get(oid, []))
        replacements = []
        for at, payment, event in sorted(events, key=lambda x: (x[0], x[1], x[2]['source_ref']['record_id'])):
            sources.append(event['source_ref'])
            amount = decimal_amount(event['amount_native'])
            if payment:
                remaining -= amount
                paid += amount
                if at.date().isoformat() >= start:
                    paid_by_kind[row['kind']] += amount
                    if row['kind'] != 'ar':
                        paid_month += amount
            elif event['kind'] == 'writeoff':
                require(amount <= remaining, 'Writeoff exceeds unresolved balance')
                written += amount
            else:
                adjustment = amount if event['kind'] == 'reverse' else -amount
                remaining += adjustment
                if row['due_date'] is not None and at.date().isoformat() <= row['due_date']:
                    adjusted_due += adjustment
                if event['kind'] == 'renegotiate':
                    replacements.append(event['replacement_obligation_id'])
            require(remaining >= 0 and adjusted_due >= 0, 'allocation_conflict: settlement/amendment exceeds legal face')
        kind, due = row['kind'], row['due_date']
        late_days = max(0, (iso_date(end_date) - iso_date(due)).days) if due is not None else None
        is_due = due is not None and due <= end_date
        exposure = remaining * mora_multiplier(late_days) if late_days is not None else None
        if row['currency'] == reporting:
            unpaid[kind] += remaining
            if due is not None and start <= due < next_start:
                due_amounts[kind] += adjusted_due
            if kind != 'ar':
                writeoff += written
                if is_due:
                    total_due += remaining
                    if remaining:
                        due_dates.append(due)
                elif due is not None:
                    non_due += remaining
                if late_days and remaining:
                    overdue += remaining
                    weighted += exposure
                    for bucket in aging.values():
                        if late_days >= bucket['from_days'] and (bucket['to_days'] is None or late_days <= bucket['to_days']):
                            bucket['actual'] += remaining
                            bucket['weighted'] += exposure
                if kind in ('financial_principal', 'financial_interest') and due is not None and next_start <= due < horizon_end:
                    next_service += remaining
            if kind == 'financial_principal':
                product_stocks[row['financial_product_id']] = product_stocks.get(row['financial_product_id'], ZERO) + remaining
        scope_kind = 'financial_debt' if kind in ('financial_principal', 'financial_interest') else 'ar' if kind == 'ar' else 'operating_ap'
        scope_issues[scope_kind].extend(local_reasons)
        if kind == 'operating_ap':
            operating_reasons.extend(local_reasons)
        if kind == 'ar':
            ar_reasons.extend(local_reasons)
        else:
            reasons.extend(local_reasons)
        items.append({'obligation_id': oid, 'version_id': row['version_id'], 'kind': kind, 'currency': row['currency'],
                      'financial_product_id': row['financial_product_id'], 'face': text(face), 'remaining': text(remaining),
                      'due_date': due, 'late_days': late_days, 'weighted_exposure': text(exposure) if kind != 'ar' else None,
                      'paid': text(paid), 'written_off': text(written), 'replacement_ids': replacements,
                      **provenance([row['source_ref'], *payment_sources, *[e[2]['source_ref'] for e in events]]),
                      'reasons': list(reason_codes(*local_reasons))})
    principal_checks = []
    for product, amount in sorted(product_stocks.items()):
        matches = [o for o in observations if o.company_id == view.company and o.financial_product_id == product
                   and o.as_of == end_date and view.visible(o.source_ref)]
        if not matches:
            reasons.append('reconciliation_unverified')
            principal_checks.append({'financial_product_id': product, 'residual': None})
        else:
            require(all(o.currency == reporting for o in matches), 'Principal observation currency mismatch')
            values = {decimal_amount(o.amount_native) for o in matches}
            require(len(values) == 1, 'duplicate_conflict: principal observations')
            residual = next(iter(values)) - amount
            sources.extend(o.source_ref for o in matches)
            principal_checks.append({'financial_product_id': product, 'residual': text(residual)})
            if residual != 0:
                reasons.append('reconciliation_mismatch')
    availability = availability_reasons(sources)
    unknown = view.unavailable('obligation', 'settlement', 'amendment', 'universe_attestation')
    reasons.extend(availability + unknown)
    operating_reasons.extend(availability + unknown)
    incomplete_codes = {'missing_source', 'universe_unverified', 'unknown_known_on', 'unverified_availability',
                        'settlement_history_missing', 'debt_vintage_missing', 'due_date_unknown',
                        'role_unverified', 'allocation_conflict', 'fx_missing'}
    complete = not incomplete_codes.intersection(reasons)
    scope_complete = {k: not incomplete_codes.intersection(v + availability + unknown + allocation_reasons)
                      for k, v in scope_issues.items()}
    subtotals = {**{k: text(v) for k, v in unpaid.items()}, 'unpaid_due': text(total_due), 'overdue_actual': text(overdue),
                 'weighted_exposure': text(weighted), 'not_due': text(non_due), 'next_service_mean': text(next_service / Decimal(3))}
    return {'month': month, 'currency': reporting,
            'financial_principal': subtotals['financial_principal'] if scope_complete['financial_debt'] else None,
            'operating_ap': subtotals['operating_ap'] if scope_complete['operating_ap'] else None,
            'receivables': subtotals['ar'] if scope_complete['ar'] else None,
            'known_subtotals': subtotals, 'coverage_complete': complete, 'scope_complete': scope_complete,
            'unpaid_by_kind': {k: text(v) for k, v in unpaid.items()},
            'due_by_kind': {k: text(v) for k, v in due_amounts.items()},
            'paid_by_kind': {k: text(v) for k, v in paid_by_kind.items()},
            'operating_due': text(due_amounts['operating_ap']) if not operating_reasons else None,
            'unpaid_due': text(total_due) if complete else None, 'overdue_actual': text(overdue) if complete else None,
            'weighted_exposure': text(weighted) if complete else None, 'not_due': text(non_due) if complete else None,
            'next_service_mean': text(next_service / Decimal(3)) if scope_complete['financial_debt'] else None,
            'allocated_paid': text(paid_month), 'written_off': text(writeoff),
            'earliest_unresolved_due_date': min(due_dates) if due_dates else None,
            'aging': [{**b, 'actual': text(b['actual']), 'weighted': text(b['weighted'])} for b in aging.values()],
            'items': items, 'principal_reconciliation': principal_checks, 'comparability': comparable,
            'operating_reasons': list(reason_codes(*operating_reasons)), 'ar_reasons': list(reason_codes(*ar_reasons)),
            'reasons': list(reason_codes(*reasons)), 'totals_scope': 'known_reporting_currency_subtotals', **provenance(sources)}
