"""Serving boundary; historical scientific contracts remain byte-identical."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

from scripts.financial_v3_cash import CashTerms, EvidenceView
from scripts.financial_v3_contract import (
    INPUT_VERSION,
    POLICY_VERSION,
    _field,
    canonical_bytes,
    currency_code,
    decimal_amount,
    deduplicate_records,
    exact_keys,
    identifier,
    input_schemas,
    monthly_cutoff,
    parse_json,
    require,
    utc_timestamp,
)
from scripts.financial_v3_obligations import (
    ObligationCoverage,
    PrincipalObservation,
    _allocations,
    _amendments,
)

SCHEMA_VERSION = 'predictive-assessment-1'
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 50_000
SUPPLEMENTS = {
    'cash_terms': {'company_id': 'id', 'account_id': 'id', 'currency': 'currency',
        'minor_unit': 'decimal', 'balance_convention': 'enum:booked_before_cutoff',
        'invalid_stock_values': 'decimals', 'source_ref': 'source'},
    'obligation_coverage': {'company_id': 'id', 'source_kind': 'enum:operating_ap|financial_debt|ar',
        'from_inclusive': 'date', 'to_exclusive': 'date', 'history_from': 'date',
        'settlements_to_exclusive': 'date', 'schedule_to_exclusive': 'date',
        'comparable_id': 'id', 'source_ref': 'source'},
    'principal_observations': {'company_id': 'id', 'financial_product_id': 'id',
        'currency': 'currency', 'as_of': 'date', 'amount_native': 'decimal', 'source_ref': 'source'},
    'invoices': {'company_id': 'id', 'invoice_id': 'id', 'side': 'enum:ap|ar',
        'issued_on': 'date', 'due_on': 'date?', 'amount_native': 'decimal',
        'currency': 'currency', 'fx_at': 'timestamp?', 'source_ref': 'source'},
    'classifications': {'company_id': 'id', 'movement_id': 'id', 'rule_id': 'id', 'source_ref': 'source'},
}
IDENTITIES = {
    'cash_terms': ('account_id',),
    'obligation_coverage': ('source_kind', 'from_inclusive', 'to_exclusive'),
    'principal_observations': ('financial_product_id', 'as_of'),
    'invoices': ('invoice_id',), 'classifications': ('movement_id',),
}
FIELDS = {'schema_version', 'request_id', 'company_id', 'group_id', 'snapshot_id',
          'as_of', 'view', 'reporting_currency', 'records', *SUPPLEMENTS}


class InputError(ValueError):
    """Stable, payload-free error suitable for an HTTP adapter."""
    def __init__(self, code, *, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def decode(value):
    if not isinstance(value, (str, bytes)):
        raise InputError('invalid_json', status=400)
    if len(value.encode('utf-8') if isinstance(value, str) else value) > MAX_BYTES:
        raise InputError('payload_too_large', status=413)
    try:
        return parse_json(value)
    except (ValueError, UnicodeError, RecursionError):
        raise InputError('invalid_json', status=400) from None


def _supplement(name, rows):
    require(type(rows) is list, 'Invalid collection')
    unique = {}
    for row in rows:
        exact_keys(row, SUPPLEMENTS[name])
        for key, spec in SUPPLEMENTS[name].items():
            if spec == 'decimals':
                require(type(row[key]) is list, 'Invalid decimal list')
                for value in row[key]:
                    _field('decimal', value)
            else:
                _field(spec, row[key])
        if name == 'cash_terms':
            CashTerms(**{**row, 'invalid_stock_values': tuple(row['invalid_stock_values'])})
        elif name == 'obligation_coverage':
            ObligationCoverage(**row)
        elif name == 'principal_observations':
            PrincipalObservation(**row)
        elif name == 'invoices':
            require(decimal_amount(row['amount_native']) >= 0, 'Negative invoice face')
        key = tuple(row[k] for k in IDENTITIES[name])
        require(key not in unique or canonical_bytes(unique[key]) == canonical_bytes(row), 'duplicate_conflict')
        unique[key] = row
    return sorted(unique.values(), key=canonical_bytes)


def _references(snapshot):
    rows = snapshot['records']
    accounts = {}
    for row in rows['account']:
        key = row['account_id']
        # The unchanged core has no account-vintage resolver; do not silently choose one.
        require(key not in accounts, 'Ambiguous account versions')
        accounts[key] = row
    obligations = defaultdict(list)
    for row in rows['obligation']:
        obligations[row['obligation_id']].append(row)
    for versions in obligations.values():
        require(len({canonical_bytes({k: v for k, v in r.items() if k not in
            ('source_ref', 'version_id', 'effective_from', 'effective_to')}) for r in versions}) == 1,
            'Changed obligation terms require amendment lineage')
    payments = {r['movement_id']: r for r in rows['cash_movement']}
    allocations = {r['allocation_id']: r for r in rows['settlement']}
    docs = {r['invoice_id']: r for r in snapshot['invoices']}
    classifications = {r['movement_id']: r for r in snapshot['classifications']}
    pairs = defaultdict(list)
    for kind in ('cash_movement', 'balance_anchor', 'coverage_interval'):
        for row in rows[kind]:
            account = accounts.get(row['account_id'])
            require(account is not None, 'Unresolved account')
            if account is not None and 'currency' in row:
                require(row['currency'] == account['currency'], 'Account currency mismatch')
    for row in payments.values():
        classification = classifications.get(row['movement_id'])
        if row['economic_bucket'] != 'unknown':
            require(classification is not None, 'Missing classification evidence')
        if classification is not None:
            require(row['classification_evidence'] == classification['source_ref']['record_id'],
                    'Classification evidence mismatch')
        for aid in row['obligation_allocation_ids']:
            require(aid in allocations and allocations[aid]['movement_id'] == row['movement_id'],
                    'Allocation must resolve bidirectionally')
        if row['transfer_pair_id'] is not None:
            require(row['economic_bucket'] == 'transfer', 'Non-transfer pair')
            pairs[row['transfer_pair_id']].append(row)
    require(set(classifications) <= set(payments), 'Orphan classification')
    for pair in pairs.values():
        require(len(pair) == 2 and pair[0]['account_id'] != pair[1]['account_id'], 'Unresolved transfer pair')
        a, b = [decimal_amount(r['amount_native']) for r in pair]
        require(a * b < 0, 'Transfer directions disagree')
        if pair[0]['currency'] == pair[1]['currency']:
            require(a + b == 0, 'Transfer amounts disagree')
    for row in allocations.values():
        require(row['obligation_id'] in obligations, 'Unresolved obligation')
        if row['movement_id'] is not None:
            require(row['movement_id'] in payments and
                    row['allocation_id'] in payments[row['movement_id']]['obligation_allocation_ids'],
                    'Unresolved payment')
    for row in rows['amendment']:
        require(row['obligation_id'] in obligations, 'Unresolved amended obligation')
        if row['replacement_obligation_id'] is not None:
            require(row['replacement_obligation_id'] in obligations, 'Unresolved replacement')
    for versions in obligations.values():
        for row in versions:
            if row['invoice_id'] is not None:
                doc = docs.get(row['invoice_id'])
                require(doc is not None, 'Unresolved invoice')
                if doc is not None:
                    require(row['currency'] == doc['currency'] and
                            ('ar' if row['kind'] == 'ar' else 'ap') == doc['side'], 'Invoice role mismatch')
    for row in snapshot['cash_terms']:
        account = accounts.get(row['account_id'])
        require(account is not None and account['currency'] == row['currency'], 'Orphan cash terms')
    products = {r['financial_product_id'] for r in rows['obligation'] if r['kind'] == 'financial_principal'}
    require(all(r['financial_product_id'] in products for r in snapshot['principal_observations']),
            'Orphan principal observation')
    # Validate asserted links even outside the requested historical window. This
    # view is only for integrity, never for features or financial evaluation.
    integrity_context = context(snapshot, snapshot['as_of'][:7] + '-01')
    integrity_context['view'] = 'reconstructed_retrospective'
    integrity_view = EvidenceView(rows, integrity_context)
    legal = {key: min(versions, key=lambda r: r['effective_from']) for key, versions in obligations.items()}
    last_instant = datetime.max.replace(tzinfo=timezone.utc)
    _allocations(integrity_view, legal, last_instant)
    _amendments(integrity_view, legal, last_instant)


def normalize(value, *, observed_at):
    """Validate all rows before temporal filtering; normalize idempotent duplicates."""
    try:
        require(type(value) is dict, 'Expected object')
        value = deepcopy(value)
        value.setdefault('view', 'as_of')
        exact_keys(value, FIELDS)
        require(value['schema_version'] == SCHEMA_VERSION, 'Unsupported schema')
        for key in ('request_id', 'company_id', 'group_id', 'snapshot_id'):
            identifier(value[key])
        require(value['view'] in ('as_of', 'reconstructed_retrospective'), 'Unsupported view')
        currency_code(value['reporting_currency'])
        cutoff = monthly_cutoff(value['as_of'])
        if cutoff > utc_timestamp(observed_at):
            raise InputError('open_month')
        require(type(value['records']) is dict and set(value['records']) <= set(input_schemas()),
                'Unknown record collections')
        collections = list(value['records'].values()) + [value[k] for k in SUPPLEMENTS]
        require(all(type(rows) is list for rows in collections), 'Invalid collection')
        if sum(map(len, collections)) > MAX_RECORDS or len(canonical_bytes(value)) > MAX_BYTES:
            raise InputError('payload_too_large', status=413)
        require(all(type(row) is dict and row.get('company_id') == value['company_id']
                    for rows in collections for row in rows), 'Company mismatch')
        value['records'] = {kind: sorted(deduplicate_records(kind, value['records'].get(kind, [])), key=canonical_bytes)
                            for kind in input_schemas()}
        for name in SUPPLEMENTS:
            value[name] = _supplement(name, value[name])
        _references(value)
        return value
    except InputError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise InputError('invalid_snapshot') from None


def context(snapshot, month):
    from scripts.financial_v3_contract import month_end
    end = str(month_end(month))
    return {'schema_version': INPUT_VERSION, 'policy_version': POLICY_VERSION,
            'company_id': snapshot['company_id'], 'month': month, 'as_of': end,
            'knowledge_cutoff_exclusive': monthly_cutoff(end).isoformat().replace('+00:00', 'Z'),
            'view': snapshot['view'], 'availability_assumption_id': None}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
