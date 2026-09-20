"""Stateless evaluation entry point for an HTTP adapter or offline caller."""
from scripts.financial_v3_cash import CashTerms, month_shift
from scripts.financial_v3_contract import canonical_bytes, canonical_hash, month_end
from scripts.financial_v3_engine import FinancialInputs, evaluate_company_month
from scripts.financial_v3_obligations import ObligationCoverage, PrincipalObservation
from scripts.financial_v3_score import explain_delta

from .contract import SCHEMA_VERSION, InputError, context, normalize, now
from .features import effective_records, prepare


def _guard_foreign_paid_totals(financial):
    """The frozen core's paid aggregates mix native units; never relabel them."""
    adjusted = False
    for evidence in financial['obligation_history']:
        foreign_kinds = {row['kind'] for row in evidence['items'] if row['currency'] != evidence['currency']}
        for kind in foreign_kinds:
            evidence['paid_by_kind'][kind] = None
        if foreign_kinds - {'ar'}:
            evidence['allocated_paid'] = None
        adjusted |= bool(foreign_kinds)
    if adjusted:
        financial['limitations'].append('Serving leaves affected paid aggregates null for foreign-currency obligations; native item amounts retain their currency.')
    return financial


def assess(snapshot, bundle, *, observed_at=None):
    """Evaluate supplied facts only. Caller owns authentication, persistence and clock."""
    observed_at = now() if observed_at is None else observed_at
    snapshot = normalize(snapshot, observed_at=observed_at)
    month = snapshot['as_of'][:7] + '-01'
    current_context = context(snapshot, month)
    try:
        def financial(at):
            ctx = context(snapshot, at)
            records = effective_records(snapshot, ctx)
            inputs = FinancialInputs(records,
                [CashTerms(**{**r, 'invalid_stock_values': tuple(r['invalid_stock_values'])}) for r in snapshot['cash_terms']],
                [ObligationCoverage(**r) for r in snapshot['obligation_coverage']],
                [PrincipalObservation(**r) for r in snapshot['principal_observations']])
            return _guard_foreign_paid_totals(evaluate_company_month(inputs, ctx, observed_at=observed_at,
                reporting_currency=snapshot['reporting_currency'], policy=bundle.policy))

        current = financial(month)
        previous = financial(month_shift(month, -1))
        change = explain_delta(previous, current)
        prepared = prepare(snapshot, current_context, effective_records(snapshot, current_context))
        if tuple(prepared['feature_order']) != tuple(bundle.feature_names):
            raise RuntimeError('Runtime/bundle feature incompatibility')
        predictions = {}
        for target, model in bundle.models.items():
            reasons = list(prepared['reasons'])
            if snapshot['company_id'] in bundle.excluded_company_ids or snapshot['group_id'] in bundle.excluded_group_ids:
                reasons.append('original_benchmark_excluded')
            if model is None:
                reasons.append('insufficient_training_support_or_no_selected_model')
            elif snapshot['as_of'] <= model['fit_label_cutoff']:
                reasons.append('before_selection_cutoff_no_backcast')
            probabilities = None if reasons else bundle.predict(target, prepared['values'])
            predictions[target] = {
                'target': target, 'probabilities': probabilities, 'reasons': sorted(set(reasons)),
                'horizon_start': month_shift(month, 1), 'horizon_end': str(month_end(month_shift(month, 3))),
                'calibration': 'uncalibrated_internal_retrospective',
                'scope': 'classified_operating_receipts_not_solvency',
                'fit_label_cutoff': model['fit_label_cutoff'] if model else None,
            }
        value = {
            'schema_version': SCHEMA_VERSION, 'request_id': snapshot['request_id'],
            'company_id': snapshot['company_id'], 'group_id': snapshot['group_id'],
            'snapshot_id': snapshot['snapshot_id'], 'as_of': snapshot['as_of'], 'view': snapshot['view'],
            'input_sha256': canonical_hash({k: v for k, v in snapshot.items() if k != 'request_id'}),
            'model_version': bundle.model_version, 'feature_version': bundle.feature_version,
            'policy_version': current['policy_version'], 'bundle_sha256': bundle.sha256,
            'evaluated_at': observed_at, 'financial': current, 'change': change,
            'predictions': predictions, 'model_inputs': prepared,
            'explanations': {'score': current['score']['explanation'],
                'prediction': 'Selected empirical frequencies conditioned on current receipts/trailing mean; not causal effects or score points.'},
            'warnings': ['new_inputs_not_independently_validated', 'automatic_alerts_disabled',
                         'probabilities_are_not_financial_health_scores'],
        }
        if snapshot['view'] == 'reconstructed_retrospective':
            value['warnings'].append('retrospective_not_historically_known')
        canonical_bytes(value)  # A nonfinite/unsupported output must never reach JSON/HTTP.
        return value
    except InputError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError):
        raise InputError('inconsistent_financial_evidence') from None
