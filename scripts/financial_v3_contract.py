import argparse
import calendar
import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = 'reports/modeling/financial-v3'
POLICY_VERSION = 'financial-v3-development-v1'
INPUT_VERSION = 'financial-v3-input-v1'
PROTOCOL = f'{DIRECTORY}/protocol-development-v1.json'
RECEIPT = f'{DIRECTORY}/protocol-development-v1-receipt.json'
BINDING_PATHS = (
    f'{DIRECTORY}/design-proposal.md', f'{DIRECTORY}/audit.json',
    f'{DIRECTORY}/audit-summary.json', f'{DIRECTORY}/protected-baseline.json',
    'scripts/financial_v3_audit.py', 'tests/test_financial_v3_audit.py',
    'scripts/financial_v3_contract.py', 'tests/test_financial_v3_contract.py',
)
REASONS = (
    'missing_source', 'universe_unverified', 'unknown_known_on', 'anchor_cutoff_unknown',
    'anchor_invalid', 'unrestricted_cash_unverified', 'ledger_gap', 'reconciliation_unverified',
    'reconciliation_mismatch', 'fx_missing', 'classification_ambiguous', 'role_unverified',
    'due_date_unknown', 'settlement_history_missing', 'allocation_conflict', 'debt_vintage_missing',
    'coverage_changed', 'insufficient_history', 'zero_reference', 'open_month', 'version_changed',
    'immature_followup', 'new_test_missing', 'unverified_availability', 'future_known_on',
    'duplicate_conflict', 'business_review_missing', 'confirmatory_approval_missing',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        require(math.isfinite(value), 'Nonfinite JSON number')
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        require(all(type(key) is str for key in value), 'JSON keys must be strings')
        for item in value.values():
            _json_value(item)
        return
    raise ValueError(f'Not a JSON type: {type(value).__name__}')


def canonical_bytes(value):
    _json_value(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def canonical_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, f'Duplicate JSON key: {key}')
            result[key] = item
        return result
    def invalid_constant(value):
        raise ValueError(f'Invalid JSON constant: {value}')
    result = json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)
    _json_value(result)
    return result


def exact_keys(value, keys):
    require(type(value) is dict and set(value) == set(keys), f'Expected exact fields: {sorted(keys)}')


def identifier(value):
    require(type(value) is str and 0 < len(value) <= 512 and value == value.strip()
            and all(ord(c) >= 32 for c in value), 'Expected nonblank identifier')
    return value


def currency_code(value):
    require(type(value) is str and re.fullmatch(r'[A-Z]{3}', value) is not None, 'Expected uppercase currency code')
    return value


def decimal_amount(value):
    require(type(value) in (str, Decimal), 'Money requires a decimal string or Decimal, never native float/int/bool')
    if type(value) is Decimal:
        require(value.is_finite(), 'Nonfinite decimal')
        text = format(value, 'f')
    else:
        text = value
    require(re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?', text) is not None, 'Invalid fixed-point decimal')
    require(len(text.replace('-', '').replace('.', '')) <= 38 and
            ('.' not in text or len(text.split('.')[1]) <= 12), 'Decimal exceeds 38 digits or 12 fractional places')
    return Decimal(text)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        require(type(self.amount) is Decimal, 'Money.amount must be Decimal')
        decimal_amount(self.amount)
        currency_code(self.currency)


def parse_money(value):
    exact_keys(value, ('amount', 'currency'))
    require(type(value['amount']) is str, 'Wire money amount must be a decimal string')
    return Money(decimal_amount(value['amount']), currency_code(value['currency']))


def finite_number(value):
    require(type(value) in (int, float), 'Expected number, not bool or implicit conversion')
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError('Number outside finite binary64 range') from error
    require(math.isfinite(number), 'Expected finite number')
    return number


def iso_date(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is not None, 'Expected ISO date')
    return date.fromisoformat(value)


def utc_timestamp(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z', value) is not None,
            'Expected explicit UTC timestamp with Z, seconds and at most six fractional digits')
    return datetime.fromisoformat(value[:-1] + '+00:00')


def month_date(value):
    result = iso_date(value)
    require(result.day == 1, 'Month must be represented by first-of-month date')
    return result


def month_end(value):
    result = month_date(value)
    return result.replace(day=calendar.monthrange(result.year, result.month)[1])


def monthly_cutoff(as_of):
    result = iso_date(as_of)
    require(result.day == calendar.monthrange(result.year, result.month)[1], 'As-of must be a closed month-end date')
    require(result < date.max, 'Month-end cutoff overflows supported calendar')
    return datetime.combine(result + timedelta(days=1), time.min, tzinfo=timezone.utc)


def reason_codes(*codes):
    require(all(type(code) is str and code in REASONS for code in codes), 'Unknown reason code')
    return tuple(sorted(set(codes)))


def validate_source(value):
    exact_keys(value, ('source_id', 'source_sha256', 'record_id', 'effective_at', 'known_on',
                       'retrieved_at', 'source_timezone', 'availability_basis'))
    for key in ('source_id', 'record_id', 'source_timezone'):
        identifier(value[key])
    require(type(value['source_sha256']) is str and re.fullmatch(r'[a-f0-9]{64}', value['source_sha256']) is not None, 'Invalid source SHA256')
    utc_timestamp(value['effective_at'])
    retrieved = utc_timestamp(value['retrieved_at'])
    require(value['availability_basis'] in ('verified', 'retrospective', 'assumed', 'unknown'), 'Invalid availability basis')
    if value['known_on'] is not None:
        require(utc_timestamp(value['known_on']) <= retrieved, 'known_on cannot exceed retrieval')
        require(value['availability_basis'] != 'unknown', 'Unknown availability must have null known_on')
    return copy.deepcopy(value)


@dataclass(frozen=True)
class Availability:
    eligible: bool
    known_on: datetime | None
    reasons: tuple[str, ...]


def source_availability(value, as_of):
    source = validate_source(value)
    cutoff = monthly_cutoff(as_of)
    known = None if source['known_on'] is None else utc_timestamp(source['known_on'])
    reasons = []
    if known is None:
        reasons.append('unknown_known_on')
    elif known >= cutoff:
        reasons.append('future_known_on')
    if source['availability_basis'] != 'verified':
        reasons.append('unverified_availability')
    return Availability(not reasons, known, reason_codes(*reasons))


def latest_known_on(sources):
    require(type(sources) is list, 'Sources must be a list')
    values = [validate_source(value)['known_on'] for value in sources]
    if not values or None in values:
        return None
    return max(utc_timestamp(value) for value in values)


def input_schemas():
    common = {'schema_version': 'input_version', 'company_id': 'id', 'source_ref': 'source'}
    fields = {
        'account': {'account_id': 'id', 'product_type': 'id', 'currency': 'currency',
                    'effective_from': 'date?', 'effective_to': 'date?', 'unrestricted_policy': 'enum:verified_unrestricted|restricted|unknown'},
        'coverage_interval': {'coverage_id': 'id', 'account_id': 'id', 'from_inclusive': 'timestamp',
                              'to_exclusive': 'timestamp', 'statement_complete': 'bool', 'genuine_zero_activity': 'bool'},
        'cash_movement': {'movement_id': 'id', 'account_id': 'id', 'currency': 'currency', 'booked_at': 'timestamp',
                          'value_at': 'timestamp?', 'amount_native': 'decimal', 'booking_status': 'enum:booked|pending|unknown',
                          'economic_bucket': 'enum:operating|financing|investment|transfer|adjustment|unknown',
                          'classification_evidence': 'id?', 'transfer_pair_id': 'id?', 'obligation_allocation_ids': 'ids'},
        'balance_anchor': {'anchor_id': 'id', 'account_id': 'id', 'currency': 'currency', 'amount_native': 'decimal',
                           'unrestricted_native': 'decimal?', 'measured_at': 'timestamp',
                           'cutoff_semantics': 'enum:opening|closing|instant|unknown', 'independently_observed': 'bool'},
        'fx_observation': {'fx_id': 'id', 'currency': 'currency', 'reporting_currency': 'currency', 'measured_at': 'timestamp',
                           'rate_local_per_reporting': 'decimal', 'valuation_kind': 'enum:transaction|stock|unknown'},
        'obligation': {'obligation_id': 'id', 'version_id': 'id',
                       'kind': 'enum:operating_ap|financial_principal|financial_interest|other_payable|ar',
                       'currency': 'currency', 'principal_or_face_amount': 'decimal', 'installment_id': 'id?',
                       'financial_product_id': 'id?', 'invoice_id': 'id?', 'due_date': 'date?',
                       'effective_from': 'date', 'effective_to': 'date?', 'role_verified': 'bool'},
        'settlement': {'allocation_id': 'id', 'obligation_id': 'id', 'movement_id': 'id?', 'currency': 'currency',
                       'paid_at': 'timestamp', 'amount_native': 'decimal', 'component': 'enum:principal|interest|fee|trade',
                       'allocation_policy': 'enum:explicit|oldest_due_assumed|unknown'},
        'amendment': {'amendment_id': 'id', 'obligation_id': 'id', 'currency': 'currency',
                      'kind': 'enum:cancel|renegotiate|reverse|writeoff', 'effective_at': 'timestamp',
                      'amount_native': 'decimal', 'replacement_obligation_id': 'id?'},
        'universe_attestation': {'attestation_id': 'id', 'source_kind': 'enum:cash|operating_ap|financial_debt|ar',
                                 'effective_from': 'date', 'effective_to': 'date?', 'exhaustive': 'bool', 'explicit_zero': 'bool'},
    }
    return {name: {**common, **schema} for name, schema in fields.items()}


def _field(spec, value):
    if spec.endswith('?'):
        if value is None:
            return
        spec = spec[:-1]
    if spec.startswith('enum:'):
        require(type(value) is str and value in spec[5:].split('|'), f'Invalid enum: {spec}')
    elif spec == 'input_version':
        require(value == INPUT_VERSION, 'Unknown input schema version')
    elif spec == 'bool':
        require(type(value) is bool, 'Expected boolean')
    elif spec == 'ids':
        require(type(value) is list, 'Expected ID list')
        for item in value:
            identifier(item)
        require(len(value) == len(set(value)), 'Duplicate ID in list')
    elif spec == 'decimal':
        require(type(value) is str, 'Record decimal must be a string')
        decimal_amount(value)
    else:
        validators = {'id': identifier, 'currency': currency_code, 'date': iso_date,
                      'timestamp': utc_timestamp, 'source': validate_source}
        require(spec in validators, f'Unknown field specification: {spec}')
        validators[spec](value)


def validate_record(kind, value):
    schemas = input_schemas()
    require(type(kind) is str and kind in schemas, 'Unknown record kind')
    exact_keys(value, schemas[kind])
    for key, spec in schemas[kind].items():
        _field(spec, value[key])
    if 'effective_from' in value and value['effective_to'] is not None:
        require(value['effective_from'] is not None and iso_date(value['effective_from']) < iso_date(value['effective_to']), 'Invalid half-open effective interval')
    if kind == 'coverage_interval':
        require(utc_timestamp(value['from_inclusive']) < utc_timestamp(value['to_exclusive']), 'Invalid coverage interval')
        require(not value['genuine_zero_activity'] or value['statement_complete'], 'Zero activity requires complete statement evidence')
    if kind == 'universe_attestation':
        require(not value['explicit_zero'] or value['exhaustive'], 'Zero attestation requires exhaustive universe')
    if kind == 'fx_observation':
        require(decimal_amount(value['rate_local_per_reporting']) > 0, 'FX rate must be positive')
        if value['currency'] == value['reporting_currency']:
            require(decimal_amount(value['rate_local_per_reporting']) == 1, 'Same-currency FX must equal one')
    if kind in ('settlement', 'obligation'):
        key = 'amount_native' if kind == 'settlement' else 'principal_or_face_amount'
        require(decimal_amount(value[key]) >= 0, 'Legal settlement/face amount cannot be negative')
    if kind == 'cash_movement' and value['economic_bucket'] != 'unknown':
        require(value['classification_evidence'] is not None, 'Classified movement requires evidence reference')
    if kind == 'amendment' and value['kind'] == 'renegotiate':
        require(value['replacement_obligation_id'] is not None, 'Renegotiation requires replacement lineage')
    return copy.deepcopy(value)


def deduplicate_records(kind, records):
    require(type(records) is list, 'Records must be a list')
    identities = {
        'account': ('account_id', 'effective_from'), 'coverage_interval': ('coverage_id',),
        'cash_movement': ('movement_id',), 'balance_anchor': ('anchor_id',), 'fx_observation': ('fx_id',),
        'obligation': ('obligation_id', 'version_id'), 'settlement': ('allocation_id',),
        'amendment': ('amendment_id',), 'universe_attestation': ('attestation_id',),
    }
    require(type(kind) is str and kind in identities, 'Unknown record kind')
    found = {}
    for record in records:
        validated = validate_record(kind, record)
        key = tuple(validated[field] for field in ('company_id', *identities[kind]))
        if key in found:
            require(canonical_bytes(found[key]) == canonical_bytes(validated), 'duplicate_conflict')
        else:
            found[key] = validated
    return list(found.values())


def validate_quantity(value, value_type='number'):
    exact_keys(value, ('value', 'status', 'reasons', 'source_refs', 'known_on', 'coverage_complete'))
    require(value_type in ('number', 'money'), 'Unknown quantity value type')
    require(value['status'] in ('eligible', 'partial', 'unavailable'), 'Unknown evidence status')
    require(type(value['coverage_complete']) is bool, 'Coverage complete must be boolean')
    require(type(value['reasons']) is list, 'Reasons must be a list')
    reason_codes(*value['reasons'])
    require(len(value['reasons']) == len(set(value['reasons'])), 'Duplicate reason')
    _field('ids', value['source_refs'])
    _field('timestamp?', value['known_on'])
    if value['value'] is None:
        require(value['status'] == 'unavailable' and bool(value['reasons']), 'Null is unavailable with explicit reasons')
    else:
        (finite_number if value_type == 'number' else parse_money)(value['value'])
        require(value['status'] != 'unavailable', 'Unavailable quantity cannot contain a value')
        require(bool(value['source_refs']), 'Observed value requires source references')
        if value['status'] == 'eligible':
            require(value['coverage_complete'] and not value['reasons'] and value['known_on'] is not None, 'Eligible quantity requires complete dated evidence')
        else:
            require(bool(value['reasons']), 'Partial quantity requires reasons')
    return copy.deepcopy(value)


def validate_context(value):
    exact_keys(value, ('schema_version', 'policy_version', 'company_id', 'month', 'as_of',
                       'knowledge_cutoff_exclusive', 'view', 'availability_assumption_id'))
    _field('input_version', value['schema_version'])
    require(value['policy_version'] == POLICY_VERSION, 'Unknown policy version')
    identifier(value['company_id'])
    require(month_end(value['month']) == iso_date(value['as_of']), 'Month and as-of disagree')
    require(utc_timestamp(value['knowledge_cutoff_exclusive']) == monthly_cutoff(value['as_of']), 'Cutoff must be next UTC midnight, exclusive')
    require(value['view'] in ('as_of', 'as_of_simulation', 'reconstructed_retrospective'), 'Unknown view')
    if value['view'] == 'as_of_simulation':
        identifier(value['availability_assumption_id'])
    else:
        require(value['availability_assumption_id'] is None, 'Assumption ID only permitted for explicit simulation')
    return copy.deepcopy(value)


def _policy_spec():
    return {
        'policy_version': POLICY_VERSION, 'input_schema_version': INPUT_VERSION, 'result_kind': 'financial_health_v3',
        'status': {'scope': 'frozen_development_only', 'business_approved': False, 'confirmatory_approved': False,
                   'automatic_alerts_enabled': False, 'independent_review_completed': False,
                   'review': 'Parent inline fallback; separate read-only review unavailable due quota; trace held by parent.',
                   'blockers': ['new_test_missing', 'business_review_missing', 'confirmatory_approval_missing',
                                'universe_unverified', 'unrestricted_cash_unverified', 'reconciliation_unverified',
                                'settlement_history_missing', 'debt_vintage_missing', 'role_unverified', 'unknown_known_on'],
                   'future_utility_gates': 'Frozen development decision proposals, not business-approved confirmation criteria'},
        'source_scope': {'run_id': '20260919T124814Z-e5302b58', 'v1_final_test_groups_excluded': 50,
                         'development_groups': 200, 'v2_reserve_consumed': True, 'untouched_test_identified': False,
                         'financial_queries_authorized_by_this_contract': False, 'outcome_access_authorized': False,
                         'new_confirmation_data': 'Untouched external groups or future dates with documented access/vintage ledger; never relabel old data'},
        'input_conventions': {
            'schemas': input_schemas(), 'unknown_fields': 'reject', 'required_nullable_fields': 'must be present; null is not zero',
            'money': 'Fixed-point decimal strings, at most 38 digits and 12 fractional places; never native float or implicit int/bool',
            'minor_units': 'Source-verified currency minor unit supplied separately; do not assume all currencies use cents; no automatic quantization',
            'numbers': 'Finite binary64 ratios/points; bool is not a number; unknowns remain null',
            'monthly_grain': 'month YYYY-MM-01; as_of YYYY-MM-DD must equal calendar month-end; closed period [month_start,next_month_start)',
            'cutoff': 'knowledge_cutoff_exclusive is next-month-start 00:00:00Z; historical knowledge requires known_on strictly before this instant',
            'timestamps': 'UTC YYYY-MM-DDTHH:MM:SS[.ffffff]Z, at most six fractional digits; dates are never implicitly timestamps',
            'source_timezone': 'Required original timezone label; UTC times must already be resolved upstream, never infer timezone or DST',
            'source_ref_fields': ['source_id', 'source_sha256', 'record_id', 'effective_at', 'known_on', 'retrieved_at', 'source_timezone', 'availability_basis'],
            'availability_basis': ['verified', 'retrospective', 'assumed', 'unknown'],
            'known_on': 'Required nullable UTC field; never alias effective_at, due_date, extraction, retrieved_at or simulated availability to historical knowledge',
            'source_availability_helper': 'Checks verified knowledge only; effective applicability, full provenance resolution, financial eligibility and future known schedules are caller responsibilities',
            'reconstruction_available_at': 'max(all contributing known_on); null if any unknown, with availability basis kept separate',
            'duplicates': 'Same company plus typed identity: byte-equivalent canonical records idempotent; any conflict rejects affected perimeter; no last-write-wins',
            'schema_clarifications': 'Known-on/availability live only in source_ref. Date-grain obligation due_date replaces ambiguous due_at. Explicit version_id and allocation_policy added. Precise measured_at and cutoff_semantics required for anchors.',
            'validation_scope': 'Input syntax/semantics and metadata only; not full output/API schema, cross-record reconciliation, ownership proof or economic truth',
        },
        'reason_codes': list(REASONS),
        'cash': {'reverse_formula': 'B_start=B_end-sum(signed_native_movements)-signed_nontransaction_adjustments',
                 'tolerance_formula': 'max(currency_minor_unit, relative_tolerance*max(abs(observed_start),abs(observed_end),scale_floor))',
                 'relative_tolerance': 0.000001, 'scale_floor': 1, 'independent_balance_observations_required': 2,
                 'requirements': ['same_account_native_currency', 'exact_anchor_cutoff', 'complete_bridge_including_open_period_if_required',
                                  'no_gap_filling', 'no_hidden_adjustments', 'unrestricted_evidence_separate', 'dated_stock_fx_for_aggregation'],
                 'movement_and_stock_fx_are_interchangeable': False},
        'obligations': {'remaining_formula': 'face_or_principal+evidenced_adjustments-allocated_settlements',
                        'principal_formula': 'opening_principal+drawdowns-principal_allocations+principal_adjustments',
                        'interest_reduces_principal': False, 'snapshot_repetition_allowed': False,
                        'ar_offset_allowed': False, 'explicit_zero_requires_exhaustive_attestation': True,
                        'allocation': 'Explicit first; only evidenced bounded contract family may use oldest_due_then_id; assumed allocation blocks strict headline',
                        'amendments': 'Dated effective and known lineage; cancellation/renegotiation/writeoff never silently reset age or count as payment recovery'},
        'mora': {'grace_days': 7, 'grace_semantics': 'escalation_only_not_forgiveness',
                 'buckets': [{'from_days': 1, 'to_days': 7, 'multiplier': 1.0},
                             {'from_days': 8, 'to_days': 30, 'multiplier': 1.25},
                             {'from_days': 31, 'to_days': 60, 'multiplier': 1.5},
                             {'from_days': 61, 'to_days': 90, 'multiplier': 2.0},
                             {'from_days': 91, 'to_days': 180, 'multiplier': 3.0},
                             {'from_days': 181, 'to_days': None, 'multiplier': 4.0}],
                 'due_today_weighted_exposure': 0, 'due_today_in_unpaid_due': True,
                 'exposure_formula': 'E=sum(unpaid_i*k(days_late_i) for days_late_i>0)',
                 'penalty_cap': 40, 'penalty_formula': 'P=40*E/(S+E)',
                 'exposure_is_legal_debt_or_cash_expense': False, 'old_obligations_disappear': False},
        'scorecard': {
            'weights': {'generation': 0.25, 'liquidity': 0.25, 'debt': 0.20, 'growth': 0.15, 'stability': 0.15},
            'reference_months': 6, 'recent_months': 3, 'prior_months': 3, 'debt_service_future_months': 3,
            'reference_formula': 'S=mean(verified_comparable_operating_receipts[m-5..m]); require S>0',
            'generation_input': 'F_j=operating_receipts_j-operating_obligations_due_j; not cash payments or accounting profit',
            'liquidity_input': 'C_adj=unrestricted_cash-U_due; U_due includes grace and due-today amounts',
            'service_input': 'J=mean(principal+interest due next3 under origin-known schedules); COV=max(mean(F_recent3),0)/J',
            'zero_service': 'Only complete verified J=0 gives service subscore 100',
            'formulas': {'generation': 'clip(50+100*mean(F_recent3)/S,0,100)',
                         'liquidity': '100*clip((C_adj/S)/3,0,1)',
                         'debt': '0.5*100*clip(1-D/(12*S),0,1)+0.5*100*clip(COV/2,0,1)',
                         'growth': 'clip(50+100*(mean(R_recent3)-mean(R_prior3))/mean(R_prior3),0,100)',
                         'stability': '100*clip(1-std_population(F_last6/S)/0.5,0,1)'},
            'parameters': {'midpoint': 50, 'ratio_points': 100, 'liquidity_reference_months': 3,
                           'debt_receipt_reference_months': 12, 'service_coverage_ceiling': 2,
                           'debt_stock_weight': 0.5, 'debt_service_weight': 0.5, 'stability_std_ceiling': 0.5},
            'base': 50, 'range': [0, 100], 'contribution_formula': 'c_k=w_k*(dimension_k-50)',
            'total_formula': 'unclipped=50+sum(c_k)-P; limit_adjustment=clip(unclipped,0,100)-unclipped; score=unclipped+limit_adjustment',
            'delta_formula': 'sum(delta_c_k)-delta_P+delta_limit_adjustment; same version/perimeter and adjacent eligible months only',
            'headline_gate': ['five_dimensions', 'complete_obligations_mora', 'exhaustive_comparable_perimeter', 'reconciled_unrestricted_cash',
                              'valid_currency_aggregation', 'verified_operating_classification', 'six_contiguous_closed_months', 'strict_known_on_for_as_of'],
            'missing_core': 'headline_null_no_weight_renormalization', 'zero_denominator': 'null_with_zero_reference',
            'no_payment_invariant': 'Withhold A: cash+=A and U_due+=A; C_adj unchanged; F,S,growth,stability unchanged; D cannot improve; E/P nondecreasing; aggregate cannot improve',
            'bands': [{'name': 'fragile', 'lower': 0, 'upper': 40, 'upper_inclusive': False},
                      {'name': 'constrained', 'lower': 40, 'upper': 60, 'upper_inclusive': False},
                      {'name': 'intermediate', 'lower': 60, 'upper': 80, 'upper_inclusive': False},
                      {'name': 'solid_candidate', 'lower': 80, 'upper': 90, 'upper_inclusive': False},
                      {'name': 'exceptional_candidate', 'lower': 90, 'upper': 100, 'upper_inclusive': True}],
            'band_validation': 'unvalidated_internal', 'display_decimals': 1, 'explanation_residual_tolerance': 0.00000001,
            'observed_direction_delta_points': 5, 'yoy_required_contiguous_months': 15,
            'ratio_explanation': '(a_new-a_old)/b_old + a_new*(1/b_new-1/b_old); fixed order, noncausal; explicit display rounding residual',
        },
        'targets': {
            'derived_from_score_delta_alerts_or_v1_proxy': False, 'reference_month_offsets': [-3, -2, -1],
            'reference_formula': 'B=mean(R_reference)>0; F0=mean(F_reference); Q0=real_unpaid_over30d_at_s_minus_1',
            'materiality_eur': '1000', 'materiality_receipts_fraction': 0.05, 'materiality_formula': 'A=max(EUR1000,0.05*B)',
            'late_days_strictly_greater_than': 30, 'confirmation_month_offsets': [0, 1],
            'deterioration_branches': ['Q_j-Q0>=A from unpaid obligations, not coverage change', 'F_j<=F0-A and F_j<0'],
            'improvement_branches': ['Q0>=A and Q_j<=0.5*Q0 and cumulative_reference_obligation_payments_through_j>=A and Z_j<A and F_j>=F0-A',
                                     'F_j>=F0+A and F_j>0 and Q_j<=Q0 and Z_j<A'],
            'same_branch_required_in_both_confirmation_months': True,
            'improvement_payment_evidence': 'cumulative_since_reference_end_through_j',
            'payment_evidence_scope': 'Allocated repayments of obligations in Q0, measured from s-1 close through j; no repeated-payment requirement, no writeoff/renegotiation recovery credit',
            'signs_may_coexist': True, 'sign_probabilities_complementary': False,
            'episode_exit_nonqualifying_months': 2, 'gaps': 'unknown_not_episode_restart; require two observed nonqualifying months before new episode',
            'origin_onset_horizon_months': [1, 2, 3], 'negative_followup_through_offset': 4,
            'positive_observable_at': 'max(required economic source known_on and confirmation close); never backdate',
            'conditional': {'forecast_at_onset_offset': 1, 'label_at_onset_offset': 3,
                            'recovery_consecutive_months': 2, 'candidate_month_offsets': [1, 2, 3],
                            'recovery_rule': 'F_j>=F0-A/2 and Q_j<=Q0+A/2 and Z_j<A in two consecutive candidate months, with payment evidence for any overdue reduction used to satisfy recovery',
                            'payment_reduction_evidence': 'cumulative_since_reference_end_through_j',
                            'payment_reduction_scope': 'Any overdue reduction credited to recovery must be evidenced allocated repayments of the affected obligations, cumulative from s-1 close through j; not fresh repeated repayment each month and never writeoff/renegotiation credit',
                            'persistence_rule': 'same deterioration branch true at s+1,s+2,s+3',
                            'otherwise_fully_observed': 'mixed', 'missing_followup': 'unknown_censored'},
            'level_reference': {'reviewers': 3, 'blinded_to_score': True, 'classes': ['fragile', 'constrained', 'ordinary', 'solid', 'exceptional'],
                                'decision': 'strict majority for nonexceptional; unanimous exceptional; no majority or nonunanimous exceptional candidate is unknown',
                                'business_rubric_approved': False, 'safety_followup_months': 6,
                                'failure_rule': 'undisputed unpaid >30d and >max(EUR1000,0.05*trailing_receipts), or independently adjudicated unplanned rescue finance',
                                'no_failure_implies_exceptional': False},
        },
        'development': {
            'permitted_now': 'Contract and invented fixtures only; no financial data queries, training, events, outcomes, real scores or application changes',
            'historical_origins_if_later_authorized': ['2025-03-01', '2025-09-01'], 'historical_outcome_cutoff_if_later_authorized': '2025-12-31',
            'consumed_v2_reserved_origins_excluded': ['2026-03-01', '2026-05-01'], 'v2_reserved_outcomes_through': '2026-08-31',
            'old_reserve_tuning_allowed': False, 'maximum_grouped_rolling_folds': 3,
            'directional_candidates': [
                {'id': 'prevalence', 'family': 'constant', 'fit': 'training_prevalence'},
                {'id': 'persistence', 'family': 'last_primitive_state', 'probability': 'training_only_estimate_or_null'},
                {'id': 'trend', 'family': 'logistic', 'C': 1.0, 'features': ['delta_recent3_prior3_F_over_S', 'delta_recent3_prior3_Q_over_S']},
                {'id': 'scorecard_logistic_c0.1', 'family': 'logistic', 'C': 0.1},
                {'id': 'scorecard_logistic_c1', 'family': 'logistic', 'C': 1.0}],
            'scorecard_predictor_features': ['five_dimension_points', 'E_over_S', 'observed_dimension_changes', 'receipts_growth', 'months_since_last_known_primitive_state'],
            'conditional_candidates': [{'id': 'class_prevalence', 'family': 'constant'}, {'id': 'primitive_persistence', 'family': 'last_primitive_state'},
                                       {'id': 'conditional_logistic_c1', 'family': 'multinomial_logistic', 'C': 1.0}],
            'additional_model_search_allowed': False, 'selection_metric': 'macro_group_brier_minimum', 'tie_tolerance': 0.000001,
            'tie_order': ['simpler_family', 'smaller_C', 'candidate_id'], 'fit_preprocessing_and_calibration': 'training_only_with_maturation_purge',
            'alerts': {'probability_threshold': 0.60, 'confirmation_consecutive_origins': 2, 'reset_below_threshold_origins': 2,
                       'conditional_decision_threshold': 0.60, 'one_per_sign_episode': True,
                       'gaps': 'suppress_issuance_and_break_confirmation_streak; no invented consecutive origins', 'first_watch_is_confirmed_alert': False},
        },
        'evaluation': {
            'status': 'proposed_future_gates_not_business_approved', 'new_external_groups_preferred_minimum': 50,
            'groups_final_test_v1_ever_eligible': False, 'new_dates_or_cohorts_fixed': False,
            'support_directional': {'groups': 50, 'mature_events': 100, 'event_groups': 20, 'mature_negatives': 100,
                                    'prediction_coverage_min': 0.70, 'mature_ascertainment_min': 0.80},
            'improvement': {'recall_min': 0.50, 'precision_min': 0.60, 'false_alert_share_max': 0.40, 'burden_max': 0.15,
                            'median_positive_lead_min': 1, 'recall_vs_best_baseline_margin': 0.10},
            'deterioration': {'recall_min': 0.60, 'precision_min': 0.60, 'false_alert_share_max': 0.40, 'burden_max': 0.15,
                              'median_positive_lead_min': 1, 'recall_vs_best_baseline_margin': 0.10},
            'still_strong': {'origin_score_min': 80, 'interpretation': 'eligible_unvalidated_score_cohort_not_claimed_healthy',
                             'mature_events': 50, 'event_groups': 10, 'prediction_coverage_min': 0.70,
                             'recall_min': 0.50, 'precision_min': 0.60, 'median_positive_lead_min': 1, 'recall_vs_best_baseline_margin': 0.10},
            'level': {'groups': 50, 'reviewed_company_months': 300, 'references_per_class': 30, 'headline_coverage_min': 0.70,
                      'weighted_kappa_min': 0.60, 'weighted_kappa_ci_lower_min': 0.40, 'solid_exceptional_precision_min': 0.80,
                      'exceptional_precision_min': 0.90, 'exceptional_predictions': 30, 'exceptional_prediction_groups': 10,
                      'six_month_material_failure_rate_max': 0.10},
            'conditional': {'mature_episodes_per_recovered_persistent_class': 50, 'groups_per_class': 10, 'mixed_cases': 30,
                            'complete_followup_min': 0.80, 'decision_coverage_min': 0.70, 'precision_min_per_class': 0.60,
                            'recall_min_per_class': 0.60, 'balanced_accuracy_vs_best_baseline_margin': 0.10},
            'probability_gates': {'ap_reference': 'evaluation_prevalence_constant_baseline', 'ap_vs_constant_margin': 0.10,
                                  'ap_vs_best_fixed_baseline_margin': 0.05, 'brier_vs_best_baseline_max_factor': 0.95,
                                  'ece_max': 0.10, 'ece_equal_width_bins': 10, 'positive_support': 100, 'negative_support': 100, 'groups': 20,
                                  'conditional': 'One-vs-rest per recovered/persistent class; also macro AP and per-class Brier/ECE',
                                  'constant_reference': 'Evaluate a constant prediction vector on exactly the same eligible labeled evaluation rows; AP equals their prevalence, not training prevalence'},
            'matching': {'lead_months': [1, 2, 3], 'one_to_one': True, 'order': 'company_sign_actual_issue_time_then_earliest_unmatched_onset',
                         'event_denominator': 'independent_of_firing; require at least one input-eligible lead origin',
                         'unmatched': 'false_only_with_complete_mature_followup_else_censored',
                         'identities': ['events=matches+misses', 'alerts=matches+false+censored'],
                         'conditional_forecast_is_advance_detection': False},
            'explanations': {'coverage_min': 1.0, 'residual_tolerance_points': 0.00000001, 'causal_claims_allowed': False},
            'bootstrap': {'replicates': 2000, 'seed': 1729, 'unit': 'group', 'confidence': 0.95,
                          'quantiles': [0.025, 0.975], 'quantile_method': 'linear', 'finite_replicates_min': 1900,
                          'shared_draws': True, 'redraw_undefined': False, 'paired_recall_improvement_ci_lower_strict_min': 0,
                          'paired_brier_improvement_ci_lower_strict_min': 0},
            'temporal_stability': {'prespecified_thirds': 3, 'thirds_passing_recall_precision_min': 2,
                                   'insufficient_support': 'failure_of_support_not_a_passing_third'},
            'freeze_gate': 'New protocol/data-access receipt, code/features/transformations/models and final business-approved utility gates before any new confirmation outcomes; consumed/interrupted receipt never replayed',
            'activation_requires_separate_approval': True,
        },
        'clarifications': [
            'Original design-proposal.md remains unchanged; this development protocol records inline-reviewed interpretations.',
            'Payment-reduction evidence is cumulative since the fixed event reference; the same repayment need not be made again in each confirmation month.',
            'AP constant reference uses evaluation prevalence on the same eligible evaluation rows, not training prevalence; best fixed baseline AP margin remains 0.05.',
            'Same branch must establish both directional confirmation months; conditional recovery still requires two consecutive half-materiality recovery months.',
            'Monthly as_of is a month-end DATE; cutoff is exclusive next UTC midnight. Precise anchor measured_at and opening/closing/instant/unknown semantics are retained, never inferred.',
            'Nullable known_on is required in source_ref, never filled from retrieved_at/effective_at or retrospectively inferred payment dates.',
            'Trend logistic C=1 and deterministic tie order clarify the bounded development candidate list; no fit is authorized by contract creation.',
            'No-majority level labels are unknown; unanimous exceptional remains required. The independent financial rubric is not approved.',
        ],
    }


_DEFAULT_JSON = canonical_bytes(_policy_spec())


def default_policy():
    return parse_json(_DEFAULT_JSON)


def validate_policy(value):
    require(canonical_bytes(value) == _DEFAULT_JSON, 'Policy differs from frozen development v1; create a new version, never silently amend')
    return copy.deepcopy(value)


def protocol_bytes(value):
    validate_policy(value)
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def make_receipt(payload, bindings, created_at):
    require(type(payload) is bytes, 'Protocol payload must be bytes')
    policy = parse_json(payload)
    require(protocol_bytes(policy) == payload, 'Protocol bytes are not the canonical development artifact representation')
    exact_keys(bindings, BINDING_PATHS)
    require(all(type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None for value in bindings.values()), 'Invalid binding hashes')
    utc_timestamp(created_at)
    return {'receipt_version': 1, 'policy_version': POLICY_VERSION, 'protocol_path': PROTOCOL,
            'protocol_sha256': hashlib.sha256(payload).hexdigest(), 'policy_canonical_sha256': canonical_hash(policy),
            'input_schemas_sha256': canonical_hash(input_schemas()), 'bindings_sha256': copy.deepcopy(bindings),
            'created_at': created_at, 'scope': 'development_only', 'business_approved': False,
            'confirmatory_approved': False, 'automatic_alerts_enabled': False,
            'financial_data_queries': 0, 'outcome_values_read': 0,
            'review': 'parent_inline_fallback_independent_review_unavailable_quota',
            'interruption_policy': 'Exclusive artifacts; partial seal blocks replay/overwrite; inspect and version explicitly'}


def validate_receipt(value, payload, bindings):
    require(type(value) is dict and 'created_at' in value, 'Invalid receipt')
    expected = make_receipt(payload, bindings, value['created_at'])
    require(canonical_bytes(value) == canonical_bytes(expected), 'Development receipt or bound bytes changed')
    return copy.deepcopy(value)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bound_hashes(root):
    root = root.resolve()
    result = {}
    for relative in BINDING_PATHS:
        path = root / relative
        require(path.resolve().is_relative_to(root) and not path.is_symlink(), 'Binding path escapes root or is a symlink')
        result[relative] = file_hash(path)
    return result


def write_exclusive(path, payload):
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def seal_development(root=ROOT):
    root = Path(root)
    protocol, receipt = root / PROTOCOL, root / RECEIPT
    require(not protocol.exists() and not receipt.exists(), 'Existing or partial development seal; use --check, never overwrite or retry blindly')
    require(protocol.parent.is_dir(), 'Output directory must already exist')
    bindings = bound_hashes(root)
    payload = protocol_bytes(default_policy())
    proof = make_receipt(payload, bindings, datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    write_exclusive(protocol, payload)
    write_exclusive(receipt, (json.dumps(proof, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    return check(root)


def check(root=ROOT):
    root = Path(root)
    receipt = parse_json((root / RECEIPT).read_bytes())
    payload = (root / PROTOCOL).read_bytes()
    proof = validate_receipt(receipt, payload, bound_hashes(root))
    return {'ok': True, 'policy_version': POLICY_VERSION, 'protocol_sha256': proof['protocol_sha256'],
            'policy_canonical_sha256': proof['policy_canonical_sha256'],
            'receipt_sha256': file_hash(root / RECEIPT), 'bound_metadata_and_sources': len(BINDING_PATHS),
            'mode': 'read_only_metadata_and_hashes', 'financial_data_queries': 0, 'outcome_values_read': 0,
            'business_approved': False, 'confirmatory_approved': False, 'automatic_alerts_enabled': False}


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--seal-development', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = check() if args.check else seal_development()
    except (ValueError, OSError, TypeError) as error:
        parser.exit(1, f'Financial v3 contract failed: {error}\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
