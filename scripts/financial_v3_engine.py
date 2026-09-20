from dataclasses import dataclass

from scripts.financial_v3_contract import (
    POLICY_VERSION, currency_code, default_policy, require, utc_timestamp, validate_context, validate_policy,
)
from scripts.financial_v3_cash import CashTerms, EvidenceView, cash_month, month_shift
from scripts.financial_v3_obligations import ObligationCoverage, PrincipalObservation, obligation_month
from scripts.financial_v3_features import build_features
from scripts.financial_v3_score import score_features


@dataclass
class FinancialInputs:
    records: dict[str, list[dict]]
    cash_terms: list[CashTerms]
    obligation_coverage: list[ObligationCoverage]
    principal_observations: list[PrincipalObservation]

    def __post_init__(self):
        require(type(self.records) is dict, 'Expected typed record collections')
        for rows, kind in ((self.cash_terms, CashTerms), (self.obligation_coverage, ObligationCoverage),
                           (self.principal_observations, PrincipalObservation)):
            require(type(rows) is list and all(isinstance(row, kind) for row in rows), 'Invalid supplementary evidence')


def evaluate_company_month(inputs, context, *, observed_at, reporting_currency='EUR', policy=None):
    require(isinstance(inputs, FinancialInputs), 'Expected FinancialInputs')
    context = validate_context(context)
    policy = validate_policy(default_policy() if policy is None else policy)
    currency_code(reporting_currency)
    observed = utc_timestamp(observed_at)
    view = EvidenceView(inputs.records, context)
    months = [month_shift(context['month'], offset) for offset in range(-14, 1)]
    cash_history = [cash_month(view, m, inputs.cash_terms, reporting_currency) for m in months]
    obligation_history = [obligation_month(view, m, inputs.obligation_coverage, inputs.principal_observations, reporting_currency)
                          for m in months[-6:]]
    features = build_features(cash_history, obligation_history, context['month'])
    if observed < view.cutoff:
        features['reasons'] = sorted(set(features['reasons'] + ['open_month']))
    score = score_features(features, policy)
    return {'kind': 'financial_health_v3_core', 'core_version': 'financial-v3-core-v1', 'policy_version': POLICY_VERSION,
            'company_id': context['company_id'], 'month': context['month'], 'as_of': context['as_of'],
            'knowledge_cutoff_exclusive': context['knowledge_cutoff_exclusive'], 'view': context['view'],
            'calendar': 'gregorian_utc_closed_month_v1', 'reporting_currency': reporting_currency,
            'perimeter_id': features['perimeter_id'], 'cash': cash_history[-1], 'cash_history': cash_history[-6:],
            'obligations': obligation_history[-1], 'obligation_history': obligation_history,
            'features': features, 'score': score, 'prediction_eligible': context['view'] == 'as_of' and score['value'] is not None,
            'historical_prediction': False, 'automatic_alerts_enabled': False, 'evaluation_status': 'development_only',
            'limitations': ['Internal development arithmetic, not independent level validation or a forecast.',
                            'Pure caller-supplied financial records; no outcome access or model fitting.',
                            'Foreign-currency liability aggregates abstain until a comparable conversion contract is supplied.',
                            'Principal is atomic contractual face less allocated principal and dated amendments, not a repeated current stock.',
                            'ObligationCoverage certifies legal-face history, settlements and schedule completeness, not merely feed presence.']}
