from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext

from scripts.financial_v3_contract import (
    POLICY_VERSION, canonical_hash, default_policy, identifier, month_date,
    month_end, monthly_cutoff, require, utc_timestamp,
)
from scripts.financial_v3_cash import month_shift, timestamp
from scripts.financial_v3_features import combined_metadata


D = Decimal
ZERO = D('0')
SIGNS = ('improvement', 'deterioration')
CLASSES = ('recovered', 'persistent', 'mixed')
TARGETS = default_policy()['targets']


@dataclass(frozen=True)
class FixtureScope:
    evidence_id: str
    company_groups: tuple[tuple[str, str], ...]
    policy_version: str = POLICY_VERSION
    data_kind: str = 'invented_fixture_only'

    def __post_init__(self):
        require(self.evidence_id.startswith('invented-'), 'Explicit invented scope evidence required')
        require(self.policy_version == POLICY_VERSION and self.data_kind == 'invented_fixture_only', 'Real-data authorization unavailable')
        require(bool(self.company_groups) and len(dict(self.company_groups)) == len(self.company_groups), 'Unique fixture company/group bindings required')
        for company, group in self.company_groups:
            require(company.startswith('invented-') and group.startswith('invented-'), 'Only invented identities; final_test forbidden')
            require('final_test' not in company and 'final_test' not in group, 'final_test excluded at all dates')


def require_scope(scope, company=None, group=None, month=None):
    require(type(scope) is FixtureScope, 'Explicit FixtureScope required; no disk or real-outcome authorization')
    scope.__post_init__()
    if company is not None:
        require(company in dict(scope.company_groups), 'Company outside invented scope')
        if group is not None:
            require(dict(scope.company_groups)[company] == group, 'Group outside invented scope; final_test forbidden')
    if month is not None:
        month_date(month)
        require(not ('2026-03-01' <= month <= '2026-08-01'), 'Consumed v2 reserve and outcomes forbidden in development')


def confirmatory_entry(outcome_supplier=None, *, approval=None):
    raise PermissionError('new_test_missing; business_review_missing; confirmatory_approval_missing; a separately sealed new-data access implementation is required')


@dataclass(frozen=True)
class DebtState:
    obligation_id: str
    remaining: Decimal
    paid: Decimal
    written_off: Decimal
    nonpayment_adjustment: Decimal
    late_days: int
    replacement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EconomicMonth:
    company_id: str
    group_id: str
    month: str
    R: Decimal | None
    F: Decimal | None
    Q: Decimal | None
    Z: Decimal | None
    debts: tuple[DebtState, ...]
    perimeter_id: str
    known_on: str | None
    source_refs: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION

    @property
    def complete(self):
        return not self.reasons and self.known_on is not None and all(x is not None for x in (self.R, self.F, self.Q, self.Z))

    @property
    def observable_at(self):
        return None if self.known_on is None else timestamp(max(utc_timestamp(self.known_on), monthly_cutoff(str(month_end(self.month)))))


def _validate_economic(row, scope):
    require(type(row) is EconomicMonth, 'Typed EconomicMonth required')
    require_scope(scope, row.company_id, row.group_id, row.month)
    require(row.policy_version == POLICY_VERSION, 'Policy version mismatch')
    identifier(row.perimeter_id)
    require(bool(row.source_refs), 'Economic source provenance required')
    for name in ('R', 'F', 'Q', 'Z'):
        value = getattr(row, name)
        require(value is None or type(value) is D and value.is_finite(), 'Finite decimal economic primitive required')
        if value is not None and name != 'F':
            require(value >= 0, 'Negative economic stock/receipt')
    if row.known_on is not None:
        utc_timestamp(row.known_on)
    require(len({d.obligation_id for d in row.debts}) == len(row.debts), 'duplicate_conflict: debt')
    for debt in row.debts:
        require(type(debt) is DebtState and type(debt.late_days) is int and debt.late_days >= 0, 'Invalid typed debt age')
        for name in ('remaining', 'paid', 'written_off', 'nonpayment_adjustment'):
            amount = getattr(debt, name)
            require(type(amount) is D and amount.is_finite(), 'Finite Decimal debt amount required')
            require(name == 'nonpayment_adjustment' or amount >= 0, 'Negative legal amount')
    if row.Q is not None:
        require(row.Q == sum((d.remaining for d in row.debts if d.late_days > 30), ZERO), 'Q must equal real unpaid over30d, not weighted exposure')


def _core_check(core, scope, group):
    require(type(core) is dict and core.get('kind') == 'financial_health_v3_core' and
            core.get('core_version') == 'financial-v3-core-v1', 'Expected typed financial core v1 output')
    require(core['policy_version'] == POLICY_VERSION, 'Policy version mismatch')
    require_scope(scope, core['company_id'], group, core['month'])


def adapt_core_outcome(core, *, previous, group_id, scope):
    require_scope(scope)
    _core_check(core, scope, group_id)
    if previous is not None:
        _core_check(previous, scope, group_id)
        require(previous['company_id'] == core['company_id'] and month_shift(previous['month'], 1) == core['month'], 'Adjacent same-company previous core required')
    c, o = core['cash'], core['obligations']
    require(c['month'] == o['month'] == core['month'], 'Core month mismatch')
    reasons = list(c['flow_reasons']) + list(o['operating_reasons'])
    debts = tuple(DebtState(i['obligation_id'], D(i['remaining']), D(i['paid']), D(i['written_off']),
                            D(i['remaining']) + D(i['paid']) - D(i['face']), i['late_days'] or 0,
                            tuple(i['replacement_ids'])) for i in o['items'] if i['kind'] != 'ar')
    receipt = None if c['operating_receipts'] is None else D(c['operating_receipts'])
    generation = None if receipt is None or o['operating_due'] is None else receipt - D(o['operating_due'])
    comparable = canonical_hash({'accounts': c['perimeter_accounts'], 'obligations': o['comparability'], 'currency': c['currency']})
    valid = o['coverage_complete'] and o['availability_verified'] and not o['reasons']
    if not valid:
        reasons.extend(o['reasons'] or ['settlement_history_missing'])
    q = sum((d.remaining for d in debts if d.late_days > 30), ZERO) if valid else None
    z = ZERO if valid else None
    parts = [c, o]
    if c['currency'] != 'EUR' or o['currency'] != 'EUR':
        reasons.append('event_materiality_fx_unavailable')
        z = None
    prior_items = {}
    if previous is not None:
        p = previous['obligations']
        parts.append(p)
        prior_items = {i['obligation_id']: i for i in p['items'] if i['kind'] != 'ar'}
        if not p['coverage_complete'] or not p['availability_verified'] or previous['perimeter_id'] != comparable:
            reasons.append('coverage_changed')
            z = None
    for item in o['items']:
        if item['kind'] == 'ar' or item['due_date'] is None:
            continue
        crossed = month_date(item['due_date'][:8] + '01')
        from scripts.financial_v3_contract import iso_date
        crossed = iso_date(item['due_date']) + timedelta(days=31)
        if str(crossed)[:7] != core['month'][:7]:
            continue
        prior = prior_items.get(item['obligation_id'])
        unchanged = prior is not None and all(item[k] == prior[k] for k in ('paid', 'written_off', 'remaining', 'replacement_ids', 'face'))
        if not unchanged:
            z = None
            reasons.append('newly_late_timing_unavailable')
        elif z is not None:
            z += D(item['remaining'])
    meta = combined_metadata(parts)
    if not meta['availability_verified']:
        reasons.append('unverified_availability')
    row = EconomicMonth(core['company_id'], group_id, core['month'], receipt, generation, q, z, debts, comparable,
                        meta['known_on'], tuple(meta['source_refs']), tuple(sorted(set(reasons))))
    _validate_economic(row, scope)
    return row


@dataclass(frozen=True)
class EconomicEvent:
    company_id: str
    group_id: str
    sign: str
    onset: str
    confirmation: str
    observable_at: str
    B: Decimal
    F0: Decimal
    Q0: Decimal
    A: Decimal
    branches: tuple[str, ...]
    reference_months: tuple[str, ...]
    source_refs: tuple[str, ...]
    perimeter_id: str

    @property
    def event_id(self):
        return ':'.join((self.company_id, self.sign, self.onset))


@dataclass
class EventScan:
    events: tuple[EconomicEvent, ...]
    rows: dict[tuple[str, str], EconomicMonth]
    decisions: dict[tuple[str, str, str], str]
    scope: FixtureScope


def _reference(rows):
    if len(rows) != 3 or not all(r is not None and r.complete for r in rows) or len({r.perimeter_id for r in rows}) != 1:
        return None
    with localcontext() as ctx:
        ctx.prec = 80
        b = sum((r.R for r in rows), ZERO) / D(3)
        if b <= 0:
            return None
        return (b, sum((r.F for r in rows), ZERO) / D(3), rows[-1].Q,
                max(D(TARGETS['materiality_eur']), D(str(TARGETS['materiality_receipts_fraction'])) * b), rows[-1])


def _payments(reference, row):
    current = {d.obligation_id: d for d in row.debts}
    return sum((min(d.remaining, max(ZERO, current[d.obligation_id].paid - d.paid))
                for d in reference.debts if d.late_days > 30 and d.obligation_id in current), ZERO)


def _branches(sign, ref, row):
    if row is None or not row.complete or row.perimeter_id != ref[-1].perimeter_id:
        return None
    _, f0, q0, a, last = ref
    if sign == 'deterioration':
        tests = {'arrears': row.Q - q0 >= a, 'generation': row.F <= f0 - a and row.F < 0}
    else:
        tests = {'arrears': q0 >= a and row.Q <= q0 / 2 and _payments(last, row) >= a and
                 _payment_backed_reduction([last], row, last) and row.Z < a and row.F >= f0 - a,
                 'generation': row.F >= f0 + a and row.F > 0 and row.Q <= q0 and row.Z < a}
    return {key for key, value in tests.items() if value}


def _metadata(rows):
    present = [r for r in rows if r is not None]
    known = [r.observable_at for r in present]
    return {'source_refs': sorted({s for r in present for s in r.source_refs}),
            'observable_at': timestamp(max(utc_timestamp(k) for k in known)) if known and all(known) else None,
            'history_months': [r.month for r in present]}


def scan_events(records, *, scope):
    require_scope(scope)
    indexed, companies, last = {}, {}, {}
    for row in records:
        _validate_economic(row, scope)
        require(row.company_id not in last or last[row.company_id] < row.month, 'Chronological unique economic months required')
        last[row.company_id] = row.month
        indexed[row.company_id, row.month] = row
        companies.setdefault(row.company_id, []).append(row)
    events, decisions = [], {}
    for company, series in sorted(companies.items()):
        for sign in SIGNS:
            active = None
            unknown_episode = False
            failures = 0
            month = month_shift(series[0].month, 3)
            while month <= series[-1].month:
                row = indexed.get((company, month))
                key = (company, sign, month)
                if active is not None:
                    primitive = _branches(sign, active, row)
                    if primitive is None:
                        unknown_episode, failures = True, 0
                        decisions[key] = 'unknown'
                    else:
                        failures = failures + 1 if not primitive else 0
                        decisions[key] = 'unknown' if unknown_episode else 'no_event'
                        if failures == 2:
                            active, failures, unknown_episode = None, 0, False
                            decisions[key] = 'no_event'
                else:
                    reference_months = tuple(month_shift(month, i) for i in (-3, -2, -1))
                    ref = _reference([indexed.get((company, m)) for m in reference_months])
                    next_row = indexed.get((company, month_shift(month, 1)))
                    first = _branches(sign, ref, row) if ref is not None else None
                    second = _branches(sign, ref, next_row) if ref is not None else None
                    decisions[key] = 'unknown' if first is None or (first and second is None) else 'no_event'
                    if ref is not None and (first is None or (first and second is None)):
                        active, unknown_episode, failures = ref, True, 0
                    common = first.intersection(second) if first is not None and second is not None else set()
                    if common:
                        prefix = [r for r in series if r.month <= next_row.month and r.known_on is not None]
                        meta = _metadata(prefix)
                        event = EconomicEvent(company, row.group_id, sign, month, next_row.month, meta['observable_at'],
                                              *ref[:4], tuple(sorted(common)), reference_months, tuple(meta['source_refs']), row.perimeter_id)
                        events.append(event)
                        decisions[key] = 'event'
                        active, failures = ref, 0
                month = month_shift(month, 1)
    return EventScan(tuple(sorted(events, key=lambda e: (e.company_id, e.onset, e.sign))), indexed, decisions, scope)


def _scan_scope(scan, scope):
    require_scope(scope)
    require(type(scan) is EventScan and scan.scope == scope, 'Scan scope mismatch')


def directional_label(scan, company, origin, sign, *, as_at, scope):
    _scan_scope(scan, scope)
    require_scope(scope, company, month=origin)
    require(sign in SIGNS, 'Unknown direction')
    cutoff = utc_timestamp(as_at)
    future = [month_shift(origin, i) for i in (1, 2, 3)]
    positives = [e for e in scan.events if e.company_id == company and e.sign == sign and e.onset in future]
    if positives:
        first = min(positives, key=lambda e: e.onset)
        visible = utc_timestamp(first.observable_at) <= cutoff
        return {'value': 1 if visible else None, 'observable_at': first.observable_at,
                'reasons': [] if visible else ['immature_followup'], 'event_id': first.event_id if visible else None,
                'source_refs': list(first.source_refs), 'history_months': list(first.reference_months)}
    required = [scan.rows.get((company, month_shift(origin, i))) for i in range(-2, 5)]
    history = [r for (c, m), r in scan.rows.items() if c == company and m <= month_shift(origin, 4)]
    meta = _metadata(history)
    complete = all(r is not None and r.complete for r in required)
    complete = complete and len({r.perimeter_id for r in required}) == 1
    complete = complete and all(scan.decisions.get((company, sign, m)) == 'no_event' for m in future)
    mature = complete and meta['observable_at'] is not None and utc_timestamp(meta['observable_at']) <= cutoff
    return {'value': 0 if mature else None, **meta, 'event_id': None,
            'reasons': [] if mature else ['immature_followup' if complete else 'unknown_followup']}


def _payment_backed_reduction(history, current, reference):
    now = {d.obligation_id: d for d in current.debts}
    peaks = {}
    for row in [reference, *history]:
        for debt in row.debts:
            if debt.late_days > 30 and (debt.obligation_id not in peaks or debt.remaining > peaks[debt.obligation_id].remaining):
                peaks[debt.obligation_id] = debt
    for oid, peak in peaks.items():
        if oid not in now:
            return False
        after = now[oid]
        with localcontext() as ctx:
            ctx.prec = 80
            reduction = max(ZERO, peak.remaining - after.remaining)
            if reduction:
                paid_since_peak = after.paid - peak.paid
                adjustment_since_peak = after.nonpayment_adjustment - peak.nonpayment_adjustment
                if paid_since_peak < reduction or peak.remaining + adjustment_since_peak - paid_since_peak != after.remaining:
                    return False
    return True


def conditional_label(scan, event, *, as_at, scope):
    _scan_scope(scan, scope)
    require(event in scan.events and event.sign == 'deterioration', 'Confirmed deterioration episode required')
    cutoff = utc_timestamp(as_at)
    reference = scan.rows[event.company_id, event.reference_months[-1]]
    ref = (event.B, event.F0, event.Q0, event.A, reference)
    rows = [scan.rows.get((event.company_id, month_shift(event.onset, i))) for i in (1, 2, 3)]
    history = [scan.rows.get((event.company_id, month_shift(event.onset, i))) for i in range(-1, 4)]
    meta = _metadata([scan.rows[event.company_id, m] for m in event.reference_months] + history)
    valid = all(r is not None and r.complete and r.perimeter_id == event.perimeter_id for r in history)
    value = None
    if valid and utc_timestamp(meta['observable_at']) <= cutoff:
        recoveries = []
        for row in rows:
            past = [r for r in history if r.month <= row.month]
            recoveries.append(row.F >= event.F0 - event.A / 2 and row.Q <= event.Q0 + event.A / 2 and row.Z < event.A
                              and _payment_backed_reduction(past, row, reference))
        recovered = any(a and b for a, b in zip(recoveries, recoveries[1:]))
        persistent = bool(set(event.branches).intersection(*[_branches('deterioration', ref, r) for r in rows]))
        require(not (recovered and persistent), 'Conditional predicates must be disjoint')
        value = 'recovered' if recovered else 'persistent' if persistent else 'mixed'
    return {'value': value, **meta, 'reasons': [] if value is not None else ['immature_followup' if valid else 'unknown_followup'],
            'forecast_month': event.confirmation, 'forecast_available_at': event.observable_at,
            'onset_to_confirmation_months': 1, 'advance_detection': False, 'event_id': event.event_id}
