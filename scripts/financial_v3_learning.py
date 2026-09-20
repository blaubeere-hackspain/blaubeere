from dataclasses import dataclass, field
from decimal import Decimal
import math

from scripts.financial_v3_contract import POLICY_VERSION, default_policy, finite_number, month_end, monthly_cutoff, require, utc_timestamp
from scripts.financial_v3_cash import month_shift, timestamp
from scripts.financial_v3_features import DIMENSIONS, combined_metadata
from scripts.financial_v3_events import CLASSES, SIGNS, FixtureScope, require_scope, _core_check


POLICY = default_policy()['development']
SCORE_FEATURES = tuple('points_' + k for k in DIMENSIONS) + ('E_over_S',) + tuple('change_' + k for k in DIMENSIONS) + (
    'receipts_growth', 'months_since_last_known_primitive_state')
TREND_FEATURES = ('delta_recent3_prior3_F_over_S', 'delta_recent3_prior3_Q_over_S')
STATES = ('neutral', 'improvement', 'deterioration', 'recovered', 'persistent', 'mixed')
SEED = 1729
THREADS = 2


@dataclass(frozen=True)
class FeatureRow:
    company_id: str
    group_id: str
    month: str
    values: dict[str, float | None]
    headline: float | None
    known_on: str | None
    source_refs: tuple[str, ...]
    primitive_state: str
    policy_version: str = POLICY_VERSION
    reasons: tuple[str, ...] = ()
    feature_version: str = 'financial-v3-predictors-v1'


@dataclass(frozen=True)
class TrainingRow:
    feature: FeatureRow
    label: int | str | None
    label_known_on: str | None


@dataclass(frozen=True)
class Fold:
    fold_id: str
    train_cutoff: str
    validation_start: str
    validation_end: str
    train_groups: tuple[str, ...]
    validation_groups: tuple[str, ...]


@dataclass(frozen=True)
class FoldSplit:
    train: tuple[TrainingRow, ...]
    validation: tuple[TrainingRow, ...]
    exclusions: dict[str, int]


def _validate_feature(row, scope, allow_unavailable=False):
    require_scope(scope)
    require(type(row) is FeatureRow, 'Typed FeatureRow required')
    require_scope(scope, row.company_id, row.group_id, row.month)
    require(row.policy_version == POLICY_VERSION and row.feature_version == 'financial-v3-predictors-v1', 'Feature policy/version mismatch')
    require(set(row.values) == set(SCORE_FEATURES + TREND_FEATURES), 'Exact predictor allowlist required; no outcome features in X')
    require(row.primitive_state in STATES and bool(row.source_refs), 'Dated primitive state and feature provenance required')
    if row.known_on is not None:
        require(utc_timestamp(row.known_on) < monthly_cutoff(str(month_end(row.month))), 'featureknown exceeds exclusive origin cutoff')
    for value in row.values.values():
        if value is not None:
            finite_number(value)
    if row.headline is not None:
        require(0 <= finite_number(row.headline) <= 100, 'Headline outside score range')
    if not allow_unavailable:
        require(row.headline is not None and row.known_on is not None and not row.reasons and
                all(v is not None for v in row.values.values()), 'Missing core evidence: not predictive eligible; never impute to healthy')


def feature_from_core(core, *, previous, group_id, primitive_state, state_month, state_known_on, scope, state_source_refs=()):
    require_scope(scope)
    _core_check(core, scope, group_id)
    require(primitive_state in STATES, 'Unknown primitive state')
    require_scope(scope, core['company_id'], group_id, state_month)
    require(state_month <= core['month'], 'Future primitive state')
    cutoff = monthly_cutoff(str(month_end(core['month'])))
    require(utc_timestamp(state_known_on) < cutoff, 'Future primitive state availability')
    values = dict.fromkeys(SCORE_FEATURES + TREND_FEATURES)
    reasons = list(core['score']['reasons'])
    require(all(type(s) is str and s for s in state_source_refs), 'Invalid primitive state source references')
    if not state_source_refs:
        reasons.append('primitive_state_source_missing')
    parts = [core['features']]
    if core['view'] != 'as_of' or not core['prediction_eligible'] or core['score']['value'] is None:
        reasons.append('headline_unavailable')
    for key in DIMENSIONS:
        values['points_' + key] = core['score']['dimensions'][key]['points']
    if previous is not None:
        _core_check(previous, scope, group_id)
        require(previous['company_id'] == core['company_id'], 'Company mismatch')
        parts.append(previous['features'])
        if month_shift(previous['month'], 1) != core['month'] or previous['perimeter_id'] != core['perimeter_id'] or previous['view'] != 'as_of':
            reasons.append('noncomparable_previous_origin')
        else:
            for key in DIMENSIONS:
                a, b = previous['score']['dimensions'][key]['points'], values['points_' + key]
                values['change_' + key] = None if a is None or b is None else b - a
    else:
        reasons.append('insufficient_history')
    s = core['features']['raw']['S']
    e = core['features']['raw']['E']
    if s is not None and s > 0 and e is not None:
        values['E_over_S'] = e / s
        f = core['features']['generation_series']
        if len(f) == 6 and all(v is not None for v in f):
            f = [float(Decimal(v)) for v in f]
            values[TREND_FEATURES[0]] = (math.fsum(f[3:]) - math.fsum(f[:3])) / (3 * s)
        history = core['obligation_history']
        if len(history) == 6 and all(o['coverage_complete'] and o['availability_verified'] and not o['reasons'] for o in history):
            q = [sum(float(Decimal(d['remaining'])) for d in o['items'] if d['kind'] != 'ar' and d['late_days'] is not None and d['late_days'] > 30) for o in history]
            values[TREND_FEATURES[1]] = (math.fsum(q[3:]) - math.fsum(q[:3])) / (3 * s)
    values['receipts_growth'] = core['features']['growth_3v3']
    values['months_since_last_known_primitive_state'] = (int(core['month'][:4]) - int(state_month[:4])) * 12 + int(core['month'][5:7]) - int(state_month[5:7])
    if any(v is None for v in values.values()):
        reasons.append('insufficient_predictor_history')
    metadata = combined_metadata(parts)
    known = None if metadata['known_on'] is None else timestamp(max(utc_timestamp(metadata['known_on']), utc_timestamp(state_known_on)))
    row = FeatureRow(core['company_id'], group_id, core['month'], values, core['score']['value'], known,
                     tuple(sorted(set(metadata['source_refs']).union(state_source_refs))), primitive_state, reasons=tuple(sorted(set(reasons))))
    _validate_feature(row, scope, allow_unavailable=True)
    return row


def _labels(target):
    require(target in (*SIGNS, 'conditional'), 'Unknown target')
    return CLASSES if target == 'conditional' else (0, 1)


def _validate_training(rows, target, scope, allow_unavailable=False):
    classes = _labels(target)
    seen, last = set(), {}
    for row in rows:
        require(type(row) is TrainingRow, 'Typed TrainingRow required')
        _validate_feature(row.feature, scope, allow_unavailable)
        f = row.feature
        key = (f.company_id, f.month)
        require(key not in seen, 'Duplicate training origin')
        seen.add(key)
        require(f.company_id not in last or last[f.company_id] < f.month, 'Training rows must be chronological per company')
        last[f.company_id] = f.month
        for offset in range(1, 5):
            require_scope(scope, f.company_id, f.group_id, month_shift(f.month, offset))
        require(row.label is None or type(row.label) is type(classes[0]) and row.label in classes, 'Invalid target class; unknown is None, not zero')
        if row.label is not None:
            require(row.label_known_on is not None, 'Known label requires actual availability timestamp')
            offset = 3 if target == 'conditional' else 5 if row.label == 0 else 3
            earliest = utc_timestamp(month_shift(f.month, offset) + 'T00:00:00Z')
            require(utc_timestamp(row.label_known_on) >= earliest, 'Label availability precedes required confirmation/maturity')
        elif row.label_known_on is not None:
            utc_timestamp(row.label_known_on)


def split_fold(rows, fold, *, target, as_at, scope):
    require_scope(scope)
    require(type(fold) is Fold, 'Typed Fold required')
    _validate_training(rows, target, scope, allow_unavailable=True)
    cutoff, observed = utc_timestamp(fold.train_cutoff), utc_timestamp(as_at)
    require_scope(scope, month=fold.validation_start)
    require_scope(scope, month=fold.validation_end)
    require(fold.validation_start <= fold.validation_end and cutoff < utc_timestamp(fold.validation_start + 'T00:00:00Z'), 'Fold chronology invalid')
    require(cutoff <= observed, 'Training cutoff exceeds observation date')
    allowed = {g for _, g in scope.company_groups}
    train_groups, valid_groups = set(fold.train_groups), set(fold.validation_groups)
    require(train_groups and valid_groups and not train_groups.intersection(valid_groups), 'Group isolation required')
    require(train_groups.union(valid_groups) <= allowed, 'Fold contains excluded/final_test group')
    require(len(train_groups) == len(fold.train_groups) and len(valid_groups) == len(fold.validation_groups), 'Duplicate fold groups')
    train, validation, excluded = [], [], {}
    for row in rows:
        f = row.feature
        reason = None
        if f.headline is None or f.known_on is None or f.reasons or any(v is None for v in f.values.values()):
            reason = 'input_ineligible'
        elif row.label is None:
            reason = 'unknown_label'
        elif f.group_id in train_groups:
            if monthly_cutoff(str(month_end(f.month))) > cutoff:
                reason = 'origin_after_training_cutoff'
            elif utc_timestamp(row.label_known_on) > cutoff:
                reason = 'label_not_mature_at_training_cutoff'
            else:
                train.append(row)
        elif f.group_id in valid_groups and fold.validation_start <= f.month <= fold.validation_end:
            if utc_timestamp(row.label_known_on) > observed:
                reason = 'label_not_mature_at_evaluation_cutoff'
            else:
                validation.append(row)
        else:
            reason = 'outside_fold'
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
    return FoldSplit(tuple(train), tuple(validation), excluded)


@dataclass
class FixtureModels:
    target: str
    train_cutoff: str
    scope: FixtureScope
    candidates: dict
    support: dict
    reasons: tuple[str, ...]
    seed: int = SEED
    threads: int = THREADS
    mock_only: bool = True
    fit_scope: str = 'invented_fixture_training_only'
    calibration: str = 'mock_development_only'
    train_groups: tuple[str, ...] = ()

    def company_export(self):
        raise PermissionError('Fixture-trained models cannot be exported as company models')


def _candidate_specs(target):
    _labels(target)
    return POLICY['conditional_candidates' if target == 'conditional' else 'directional_candidates']


def fit_fixture_candidates(rows, *, target, train_cutoff, scope):
    require_scope(scope)
    rows = tuple(rows)
    _validate_training(rows, target, scope)
    cutoff = utc_timestamp(train_cutoff)
    require(all(r.label is not None and r.label_known_on is not None and utc_timestamp(r.label_known_on) <= cutoff and
                monthly_cutoff(str(month_end(r.feature.month))) <= cutoff for r in rows), 'Unknown/immature training rows must be purged, not fitted')
    classes = _labels(target)
    counts = {c: sum(r.label == c for r in rows) for c in classes}
    groups = {c: len({r.feature.group_id for r in rows if r.label == c}) for c in classes}
    support = {'per_class': counts, 'groups_per_class': groups, 'fixture_min_rows_per_class': 2,
               'fixture_min_groups_per_class': 2, 'confirmatory_support': False}
    models = FixtureModels(target, train_cutoff, scope, {s['id']: None for s in _candidate_specs(target)}, support, (),
                           train_groups=tuple(sorted({r.feature.group_id for r in rows})))
    if any(counts[c] < 2 or groups[c] < 2 for c in classes):
        models.reasons = ('insufficient_fixture_support_no_fit',)
        return models
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits
    require(sklearn.__version__ == '1.7.2', 'Unreviewed sklearn version; fixture learner is bounded to installed 1.7.2')
    prevalence = {c: counts[c] / len(rows) for c in classes}
    persistence = {}
    for state in sorted({r.feature.primitive_state for r in rows}):
        selected = [r for r in rows if r.feature.primitive_state == state]
        persistence[state] = {c: sum(r.label == c for r in selected) / len(selected) for c in classes}
    for spec in _candidate_specs(target):
        if spec['family'] == 'constant':
            models.candidates[spec['id']] = {'kind': 'constant', 'probabilities': prevalence}
        elif spec['family'] == 'last_primitive_state':
            models.candidates[spec['id']] = {'kind': 'persistence', 'probabilities': persistence}
        else:
            names = TREND_FEATURES if spec['id'] == 'trend' else SCORE_FEATURES
            x = [[r.feature.values[n] for n in names] for r in rows]
            y = [r.label for r in rows]
            model = Pipeline([('scale', StandardScaler()), ('logistic', LogisticRegression(
                C=spec['C'], solver='lbfgs', random_state=SEED, max_iter=1000, n_jobs=THREADS))])
            with threadpool_limits(limits=THREADS):
                model.fit(x, y)
            require(max(model.named_steps['logistic'].n_iter_) < 1000, 'Fixture logistic did not converge; no retry/search')
            models.candidates[spec['id']] = model
    return models


def predict_fixture(models, row, *, scope):
    require_scope(scope)
    require(type(models) is FixtureModels and models.scope == scope and models.mock_only, 'Fixture models/scope required')
    _validate_feature(row, scope, allow_unavailable=True)
    require(monthly_cutoff(str(month_end(row.month))) > utc_timestamp(models.train_cutoff), 'Model unavailable at historical origin')
    eligible = row.headline is not None and row.known_on is not None and not row.reasons and all(v is not None for v in row.values.values())
    predictions = {}
    for name, model in models.candidates.items():
        probabilities = None
        if eligible and model is not None:
            if type(model) is dict:
                probabilities = model['probabilities'] if model['kind'] == 'constant' else model['probabilities'].get(row.primitive_state)
            else:
                from threadpoolctl import threadpool_limits
                names = TREND_FEATURES if name == 'trend' else SCORE_FEATURES
                with threadpool_limits(limits=THREADS):
                    p = model.predict_proba([[row.values[n] for n in names]])[0]
                probabilities = dict(zip(model.classes_, map(float, p)))
        predictions[name] = (None if probabilities is None else {c: float(probabilities[c]) for c in CLASSES}
                             if models.target == 'conditional' else float(probabilities[1]))
    return {'probabilities': predictions, 'calibration': 'mock_development_only', 'mock_only': True,
            'company_model': False, 'automatic_alerts_enabled': False,
            'reasons': list(models.reasons) + ([] if eligible else ['missing_core_evidence']),
            'model_available_at': models.train_cutoff, 'feature_source_refs': list(row.source_refs)}


def conditional_fixture_forecast(models, row, event, *, issued_at, scope):
    require_scope(scope)
    require(models.target == 'conditional' and event.sign == 'deterioration', 'Conditional deterioration model/event required')
    require_scope(scope, event.company_id, event.group_id, event.onset)
    require(row.company_id == event.company_id and row.month == event.confirmation, 'Conditional forecast must use confirmation-origin inputs')
    issued = utc_timestamp(issued_at)
    require(issued >= utc_timestamp(event.observable_at) and issued >= monthly_cutoff(str(month_end(row.month))), 'Cannot forecast before actual confirmation availability')
    require(issued < utc_timestamp(month_shift(event.onset, 4) + 'T00:00:00Z'), 'Conditional horizon has ended; no retrospective forecast')
    result = predict_fixture(models, row, scope=scope)
    decisions = {}
    for candidate, probabilities in result['probabilities'].items():
        best = max(CLASSES, key=lambda c: probabilities[c]) if probabilities is not None else None
        decisions[candidate] = best if best is not None and probabilities[best] >= POLICY['alerts']['conditional_decision_threshold'] else None
    return {**result, 'event_id': event.event_id, 'forecast_month': event.confirmation, 'issued_at': issued_at,
            'event_observable_at': event.observable_at, 'decisions': decisions, 'advance_detection': False,
            'onset_to_confirmation_months': 1, 'event_source_refs': list(event.source_refs)}


def select_candidate(fold_macro_brier, *, target):
    specs = _candidate_specs(target)
    order = {s['id']: i for i, s in enumerate(specs)}
    require(set(fold_macro_brier) <= set(order), 'Candidate budget exceeded')
    scores = {}
    for name, values in fold_macro_brier.items():
        require(0 < len(values) <= POLICY['maximum_grouped_rolling_folds'], 'Fold budget exceeded or empty')
        if all(v is not None for v in values):
            require(all(finite_number(v) >= 0 for v in values), 'Invalid macro-group Brier')
            scores[name] = math.fsum(values) / len(values)
    if not scores:
        return None
    require(len({len(fold_macro_brier[n]) for n in scores}) == 1, 'Candidates evaluated on unequal fold counts')
    best = min(scores.values())
    return min((n for n, v in scores.items() if v <= best + POLICY['tie_tolerance']), key=lambda n: order[n])


def develop_fixture_folds(rows, folds, *, target, as_at, scope):
    require_scope(scope)
    require(1 <= len(folds) <= POLICY['maximum_grouped_rolling_folds'], 'Maximum three rolling folds')
    require(len({f.fold_id for f in folds}) == len(folds), 'Duplicate fold ID')
    for before, after in zip(folds, folds[1:]):
        require(utc_timestamp(before.train_cutoff) < utc_timestamp(after.train_cutoff) and before.validation_end < after.validation_start, 'Rolling folds must advance without overlapping validation')
    scores = {s['id']: [] for s in _candidate_specs(target)}
    reports = []
    for fold in folds:
        split = split_fold(rows, fold, target=target, as_at=as_at, scope=scope)
        models = fit_fixture_candidates(split.train, target=target, train_cutoff=fold.train_cutoff, scope=scope)
        predictions = [predict_fixture(models, r.feature, scope=scope)['probabilities'] for r in split.validation]
        report = {'fold_id': fold.fold_id, 'exclusions': split.exclusions, 'support': models.support, 'candidates': {}}
        for candidate in scores:
            groups, missing = {}, 0
            for row, predicted in zip(split.validation, predictions):
                p = predicted[candidate]
                if p is None:
                    missing += 1
                    continue
                error = math.fsum((p[c] - int(row.label == c)) ** 2 for c in CLASSES) / 3 if target == 'conditional' else (p - row.label) ** 2
                groups.setdefault(row.feature.group_id, []).append(error)
            score = math.fsum(math.fsum(v) / len(v) for v in groups.values()) / len(groups) if groups and not missing else None
            scores[candidate].append(score)
            report['candidates'][candidate] = {'macro_group_brier': score, 'missing_predictions': missing, 'groups': len(groups)}
        reports.append(report)
    return {'folds': reports, 'selected_candidate': select_candidate(scores, target=target), 'all_candidate_scores': scores,
            'mock_only': True, 'predictive_success': False, 'company_model_export_allowed': False}
