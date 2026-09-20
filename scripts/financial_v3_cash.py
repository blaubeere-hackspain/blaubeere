from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, localcontext

from scripts.financial_v3_contract import (
    canonical_bytes, currency_code, decimal_amount, deduplicate_records, identifier, input_schemas,
    iso_date, month_date, month_end, monthly_cutoff, reason_codes, require, utc_timestamp,
    validate_context, validate_source,
)


ZERO = Decimal(0)
BUCKETS = ('operating', 'financing', 'investment', 'transfer', 'adjustment', 'unknown')


def text(value):
    if value is None:
        return None
    require(value.is_finite(), 'Nonfinite computed money')
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def timestamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def month_shift(month, offset):
    value = month_date(month)
    year, index = divmod(value.year * 12 + value.month - 1 + offset, 12)
    return f'{year:04}-{index + 1:02}-01'


def source_key(source):
    return ':'.join(source[key] for key in ('source_id', 'record_id', 'source_sha256'))


def provenance(sources):
    unique = {canonical_bytes(validate_source(s)): s for s in sources}
    values = list(unique.values())
    known = [utc_timestamp(s['known_on']) for s in values if s['known_on'] is not None]
    return {'source_refs': sorted({source_key(s) for s in values}),
            'known_on': timestamp(max(known)) if values and len(known) == len(values) else None,
            'availability_verified': bool(values) and len(known) == len(values)
            and all(s['availability_basis'] == 'verified' for s in values)}


def availability_reasons(sources):
    result = []
    for s in sources:
        if s['known_on'] is None:
            result.append('unknown_known_on')
        if s['availability_basis'] != 'verified':
            result.append('unverified_availability')
    return list(reason_codes(*result))


@dataclass(frozen=True)
class CashTerms:
    company_id: str
    account_id: str
    currency: str
    minor_unit: str
    balance_convention: str
    invalid_stock_values: tuple[str, ...]
    source_ref: dict

    def __post_init__(self):
        identifier(self.company_id)
        identifier(self.account_id)
        currency_code(self.currency)
        require(decimal_amount(self.minor_unit) > 0, 'Invalid minor unit')
        require(self.balance_convention == 'booked_before_cutoff', 'Unsupported balance booking convention')
        for value in self.invalid_stock_values:
            decimal_amount(value)
        validate_source(self.source_ref)


class EvidenceView:
    def __init__(self, records, context):
        self.context = validate_context(context)
        require(context['view'] != 'as_of_simulation', 'Simulation needs a separately implemented availability contract')
        require(type(records) is dict and set(records) <= set(input_schemas()), 'Unknown record collections')
        self.cutoff = utc_timestamp(context['knowledge_cutoff_exclusive'])
        self.company = context['company_id']
        self.excluded = {}
        self.records = {}
        for kind in input_schemas():
            selected = []
            require(type(records.get(kind, [])) is list, 'Record collection must be a list')
            for row in records.get(kind, []):
                require(type(row) is dict, 'Expected record')
                if row.get('company_id') != self.company:
                    continue
                if self.visible(row['source_ref']):
                    selected.append(row)
                elif row['source_ref']['known_on'] is None or row['source_ref']['availability_basis'] != 'verified':
                    self.excluded.setdefault(kind, []).append(row)
            self.records[kind] = deduplicate_records(kind, selected)
        self._validate_accounts()

    def visible(self, source):
        source = validate_source(source)
        return self.context['view'] == 'reconstructed_retrospective' or (
            source['known_on'] is not None and utc_timestamp(source['known_on']) < self.cutoff
            and source['availability_basis'] == 'verified')

    def unavailable(self, *kinds):
        fields = {'cash_movement': 'booked_at', 'balance_anchor': 'measured_at', 'coverage_interval': 'from_inclusive',
                  'fx_observation': 'measured_at', 'settlement': 'paid_at', 'amendment': 'effective_at'}
        sources = []
        for kind in kinds:
            for row in self.excluded.get(kind, []):
                value = row.get(fields.get(kind, 'effective_from'))
                at = None if value is None else utc_timestamp(value) if 'T' in value else datetime.combine(iso_date(value), time.min, timezone.utc)
                if at is None or at < self.cutoff:
                    sources.append(row['source_ref'])
        return availability_reasons(sources)

    def rows(self, kind):
        return self.records[kind]

    def _validate_accounts(self):
        accounts = {}
        for row in self.rows('account'):
            prior = accounts.get(row['account_id'])
            require(prior is None or prior == row, 'coverage_changed: account versions require an explicit new perimeter')
            accounts[row['account_id']] = row
        for kind in ('cash_movement', 'balance_anchor', 'coverage_interval'):
            for row in self.rows(kind):
                account = accounts.get(row['account_id'])
                require(account is not None, 'Account ownership unresolved')
                if 'currency' in row:
                    require(row['currency'] == account['currency'], 'Account currency mismatch')
                at = row.get('booked_at', row.get('measured_at'))
                if at and account['effective_from']:
                    require(at[:10] >= account['effective_from'], 'Account fact predates effective opening')
                    require(account['effective_to'] is None or at[:10] < account['effective_to'], 'Account fact after closure')
        for cov in self.rows('coverage_interval'):
            if cov['genuine_zero_activity']:
                require(not any(m['account_id'] == cov['account_id'] and m['booking_status'] == 'booked'
                                and cov['from_inclusive'] <= m['booked_at'] < cov['to_exclusive']
                                for m in self.rows('cash_movement')), 'Zero activity conflicts with booked movement')

    def attestation(self, kind, start, end, sources, reasons, nonzero=False):
        found = [r for r in self.rows('universe_attestation') if r['source_kind'] == kind and r['exhaustive']
                 and r['effective_from'] <= start and (r['effective_to'] is None or r['effective_to'] >= end)]
        if not found:
            reasons.append('universe_unverified')
            reasons.extend(self.unavailable('universe_attestation'))
            return False
        sources.extend(r['source_ref'] for r in found)
        require(not nonzero or not any(r['explicit_zero'] for r in found), 'Explicit zero conflicts with observed universe')
        if not nonzero and not any(r['explicit_zero'] for r in found):
            reasons.append('missing_source')
            return False
        return True


def anchor_cut(anchor):
    measured = utc_timestamp(anchor['measured_at'])
    semantics = anchor['cutoff_semantics']
    if semantics == 'unknown':
        return None
    if semantics == 'closing':
        return measured + timedelta(microseconds=1)
    return measured


def covered(view, account_id, start, end, sources):
    intervals = sorted((r for r in view.rows('coverage_interval') if r['account_id'] == account_id
                        and r['statement_complete'] and utc_timestamp(r['to_exclusive']) > start
                        and utc_timestamp(r['from_inclusive']) < end), key=lambda r: r['from_inclusive'])
    cursor = start
    for row in intervals:
        sources.append(row['source_ref'])
        lo, hi = utc_timestamp(row['from_inclusive']), utc_timestamp(row['to_exclusive'])
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def fx_rate(view, currency, reporting, measured_at, kind, sources):
    if currency == reporting:
        return Decimal(1)
    rows = [r for r in view.rows('fx_observation') if r['currency'] == currency and r['reporting_currency'] == reporting
            and utc_timestamp(r['measured_at']) == measured_at and r['valuation_kind'] == kind]
    if not rows:
        return None
    values = {decimal_amount(r['rate_local_per_reporting']) for r in rows}
    require(len(values) == 1, 'duplicate_conflict: incompatible FX observations')
    sources.extend(r['source_ref'] for r in rows)
    return next(iter(values))


def reconstruct_account(view, account, month, terms, reporting):
    start = datetime.combine(month_date(month), time.min, timezone.utc)
    end = monthly_cutoff(str(month_end(month)))
    sources = [account['source_ref']]
    reasons = list(view.unavailable('balance_anchor', 'coverage_interval'))
    account_id = account['account_id']
    term_rows = [t for t in terms if t.account_id == account_id and t.company_id == view.company and view.visible(t.source_ref)]
    require(not term_rows or all(t == term_rows[0] for t in term_rows), 'duplicate_conflict: multiple account terms')
    term = term_rows[0] if term_rows else None
    if term is None:
        reasons.append('anchor_cutoff_unknown')
    else:
        require(term.currency == account['currency'], 'Cash terms currency mismatch')
        sources.append(term.source_ref)
    anchors = []
    unknown_cutoff = False
    for row in view.rows('balance_anchor'):
        if row['account_id'] != account_id:
            continue
        amount = decimal_amount(row['amount_native'])
        unrestricted = None if row['unrestricted_native'] is None else decimal_amount(row['unrestricted_native'])
        if term:
            sentinels = {decimal_amount(x) for x in term.invalid_stock_values}
            require(amount not in sentinels and unrestricted not in sentinels, 'anchor_invalid: source stock sentinel')
        cut = anchor_cut(row)
        if cut is None:
            unknown_cutoff = True
        elif view.context['view'] == 'reconstructed_retrospective' or cut <= view.cutoff:
            anchors.append((cut, row))
    candidates = sorted((x for x in anchors if x[0] >= end), key=lambda x: (x[0], x[1]['anchor_id']))
    primary = candidates[0] if candidates else None
    result = {'account_id': account_id, 'currency': account['currency'], 'ledger_native': None, 'unrestricted_native': None,
              'ledger_reporting': None, 'unrestricted_reporting': None, 'anchor_ref': None, 'anchor_at': None,
              'anchor_cutoff_exclusive': None, 'anchor_known_on': None, 'reversed_from': timestamp(end), 'reversed_to': None,
              'reconciliation_anchor_ref': None, 'residual': None, 'tolerance': None, 'fx_stock_rate': None,
              'fx_valuation_change_on_reconstructed_native': None, 'booking_convention': 'booked_before_cutoff'}
    if primary is None:
        reasons.append('anchor_cutoff_unknown' if unknown_cutoff else 'missing_source')
    else:
        cut, anchor = primary
        sources.append(anchor['source_ref'])
        result.update(anchor_ref=anchor['anchor_id'], anchor_at=anchor['measured_at'], anchor_cutoff_exclusive=timestamp(cut),
                      anchor_known_on=anchor['source_ref']['known_on'], reversed_to=timestamp(cut))
        movements = [r for r in view.rows('cash_movement') if r['account_id'] == account_id]
        bridge = [r for r in movements if end <= utc_timestamp(r['booked_at']) < cut]
        bridge_complete = covered(view, account_id, min(start, end), cut, sources)
        if any(r['booking_status'] == 'unknown' for r in bridge):
            bridge_complete = False
        if not bridge_complete:
            reasons.append('ledger_gap')
        else:
            sources.extend(r['source_ref'] for r in bridge if r['booking_status'] == 'booked')
            balance = decimal_amount(anchor['amount_native']) - sum((decimal_amount(r['amount_native']) for r in bridge
                                                                     if r['booking_status'] == 'booked'), ZERO)
            result['ledger_native'] = text(balance)
            if account['unrestricted_policy'] == 'verified_unrestricted' and anchor['unrestricted_native'] is not None:
                require(decimal_amount(anchor['unrestricted_native']) == decimal_amount(anchor['amount_native']),
                        'Unrestricted account conflicts with anchor restriction')
                result['unrestricted_native'] = text(balance)
            elif cut == end and anchor['unrestricted_native'] is not None:
                result['unrestricted_native'] = anchor['unrestricted_native']
            else:
                reasons.append('unrestricted_cash_unverified')
            second = sorted((x for x in anchors if x[0] <= end and x[0] < cut and x[1]['independently_observed']
                             and x[1]['source_ref']['record_id'] != anchor['source_ref']['record_id']), key=lambda x: x[0])
            if not second or not anchor['independently_observed'] or term is None:
                reasons.append('reconciliation_unverified')
            else:
                lower, independent = second[-1]
                sources.append(independent['source_ref'])
                result['reconciliation_anchor_ref'] = independent['anchor_id']
                if not covered(view, account_id, lower, cut, sources):
                    reasons.append('ledger_gap')
                else:
                    between = [r for r in movements if lower <= utc_timestamp(r['booked_at']) < cut]
                    if any(r['booking_status'] == 'unknown' for r in between):
                        reasons.append('ledger_gap')
                    sources.extend(r['source_ref'] for r in between if r['booking_status'] == 'booked')
                    observed_start, observed_end = decimal_amount(independent['amount_native']), decimal_amount(anchor['amount_native'])
                    residual = observed_end - observed_start - sum((decimal_amount(r['amount_native']) for r in between
                                                                    if r['booking_status'] == 'booked'), ZERO)
                    tolerance = max(decimal_amount(term.minor_unit), Decimal('0.000001') * max(abs(observed_start), abs(observed_end), Decimal(1)))
                    result.update(residual=text(residual), tolerance=text(tolerance))
                    if abs(residual) > tolerance:
                        reasons.append('reconciliation_mismatch')
            rate = fx_rate(view, account['currency'], reporting, end - timedelta(microseconds=1), 'stock', sources)
            if rate is None:
                reasons.append('fx_missing')
            else:
                result.update(fx_stock_rate=text(rate), ledger_reporting=text(balance / rate))
                if result['unrestricted_native'] is not None:
                    result['unrestricted_reporting'] = text(Decimal(result['unrestricted_native']) / rate)
                extra_sources = []
                anchor_rate = fx_rate(view, account['currency'], reporting, cut - timedelta(microseconds=1), 'stock', extra_sources)
                if anchor_rate is not None:
                    result['fx_valuation_change_on_reconstructed_native'] = text(balance / rate - balance / anchor_rate)
                    sources.extend(extra_sources)
    reasons.extend(availability_reasons(sources))
    result.update(provenance(sources), reasons=list(reason_codes(*reasons)), usable=not reasons)
    return result, sources


def cash_month(view, month, terms, reporting_currency='EUR'):
    with localcontext() as precision:
        precision.prec = 80
        return _cash_month(view, month, terms, currency_code(reporting_currency))


def _cash_month(view, month, terms, reporting):
    start, end = month_date(month), month_date(month_shift(month, 1))
    lo, hi = datetime.combine(start, time.min, timezone.utc), datetime.combine(end, time.min, timezone.utc)
    sources, reasons, flow_reasons, accounts, native = [], [], [], [], {}
    active = sorted((a for a in view.rows('account') if (a['effective_from'] is None or a['effective_from'] < str(end))
                     and (a['effective_to'] is None or a['effective_to'] > str(start))), key=lambda a: a['account_id'])
    cash_universe = view.attestation('cash', str(start), str(end), sources, flow_reasons, bool(active))
    receipts = ZERO
    for account in active:
        sources.append(account['source_ref'])
        aid = account['account_id']
        if account['effective_from'] is None or account['effective_from'] > str(start) or (account['effective_to'] and account['effective_to'] < str(end)):
            flow_reasons.append('coverage_changed')
        if not covered(view, aid, lo, hi, sources):
            flow_reasons.append('ledger_gap')
        amounts = {bucket: {'inflow': ZERO, 'outflow': ZERO, 'net': ZERO} for bucket in BUCKETS}
        for movement in view.rows('cash_movement'):
            at = utc_timestamp(movement['booked_at'])
            if movement['account_id'] != aid or not lo <= at < hi:
                continue
            if movement['booking_status'] == 'unknown':
                flow_reasons.append('ledger_gap')
            if movement['booking_status'] != 'booked':
                continue
            sources.append(movement['source_ref'])
            amount, bucket = decimal_amount(movement['amount_native']), movement['economic_bucket']
            amounts[bucket]['net'] += amount
            amounts[bucket]['inflow' if amount >= 0 else 'outflow'] += abs(amount)
            if bucket == 'unknown':
                flow_reasons.append('classification_ambiguous')
            if bucket == 'operating' and amount > 0:
                rate = fx_rate(view, account['currency'], reporting, at, 'transaction', sources)
                if rate is None:
                    flow_reasons.append('fx_missing')
                else:
                    receipts += amount / rate
        native[aid] = {bucket: {k: text(v) for k, v in values.items()} for bucket, values in amounts.items()}
        result, stock_sources = reconstruct_account(view, account, month, terms, reporting)
        accounts.append(result)
        sources.extend(stock_sources)
        reasons.extend(result['reasons'])
    flow_reasons.extend(view.unavailable('account', 'coverage_interval', 'cash_movement'))
    flow_reasons.extend(availability_reasons(sources))
    reasons.extend(flow_reasons)
    ledger = sum((Decimal(a['ledger_reporting']) for a in accounts if a['ledger_reporting'] is not None), ZERO)
    unrestricted = sum((Decimal(a['unrestricted_reporting']) for a in accounts if a['unrestricted_reporting'] is not None), ZERO)
    if any(a['ledger_reporting'] is None for a in accounts) or not cash_universe:
        ledger = None
    if any(a['unrestricted_reporting'] is None for a in accounts) or not cash_universe:
        unrestricted = None
    return {'month': month, 'currency': reporting, 'ledger': text(ledger), 'unrestricted': text(unrestricted),
            'operating_receipts': text(receipts) if not flow_reasons else None,
            'known_receipts_subtotal': text(receipts), 'native_flows': native, 'accounts': accounts,
            'flow_reasons': list(reason_codes(*flow_reasons)), 'reasons': list(reason_codes(*reasons)),
            'perimeter_accounts': [[a['account_id'], a['currency'], a['unrestricted_policy']] for a in active],
            **provenance(sources)}
