from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


SEED = 1729


def candidate_model(candidate, seed=SEED):
    if candidate['family'] == 'logistic':
        return Pipeline([
            ('imputer', SimpleImputer(strategy='median', keep_empty_features=True, add_indicator=True)),
            ('scaler', StandardScaler()),
            ('classifier', LogisticRegression(C=candidate['C'], max_iter=2000, random_state=seed)),
        ])
    if candidate['family'] == 'hgb':
        return HistGradientBoostingClassifier(
            max_leaf_nodes=candidate['max_leaf_nodes'], max_depth=3, learning_rate=0.05,
            max_iter=150, l2_regularization=5., early_stopping=False, random_state=seed)
    raise ValueError('Unknown frozen candidate family')


def fit_candidate(candidate, x, y, seed=SEED):
    model = candidate_model(candidate, seed)
    with threadpool_limits(limits=2):
        model.fit(x, y)
    return model


def probabilities(model, x):
    with threadpool_limits(limits=2):
        values = model.predict_proba(x)[:, 1]
    if not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError('Invalid probability estimates')
    return values


def metrics(y, p, train_prevalence):
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    prevalence = float(y.mean())
    ap = float(average_precision_score(y, p))
    brier = float(brier_score_loss(y, p))
    baseline = float(np.mean((y - train_prevalence) ** 2))
    predicted = p >= 0.5
    tp = int(np.sum(predicted & (y == 1)))
    fp = int(np.sum(predicted & (y == 0)))
    fn = int(np.sum(~predicted & (y == 1)))
    return {
        'rows': len(y), 'prevalence': prevalence, 'average_precision': ap, 'brier': brier,
        'baseline': {'train_prevalence': float(train_prevalence), 'brier': baseline,
                     'average_precision': prevalence},
        'relative_brier_skill': 1. - brier / baseline if baseline > 0 else None,
        'roc_auc_diagnostic': float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
        'threshold': 0.5, 'precision': tp / (tp + fp) if tp + fp else 0.,
        'recall': tp / (tp + fn) if tp + fn else 0.,
        'true_alerts': tp, 'false_alerts': fp, 'missed_positives': fn,
        'true_negatives': int(np.sum(~predicted & (y == 0))),
        'passed': bool(ap > prevalence and brier <= 0.95 * baseline),
    }


def select_candidate(results):
    eligible = [row for row in results if len(row['folds']) == 2 and all(fold['passed'] for fold in row['folds'])]
    if not eligible:
        return None
    return min(eligible, key=lambda row: (
        sum(fold['brier'] for fold in row['folds']) / 2,
        row['family'] != 'logistic', row['id']))['id']


def reliability(y, p):
    y, p = np.asarray(y), np.asarray(p)
    bins = []
    for i in range(10):
        mask = (p >= i / 10) & ((p < (i + 1) / 10) if i < 9 else (p <= 1))
        bins.append({'lower': i / 10, 'upper': (i + 1) / 10, 'rows': int(mask.sum()),
                     'mean_probability': float(p[mask].mean()) if mask.any() else None,
                     'observed_fraction': float(y[mask].mean()) if mask.any() else None})
    return bins


def grouped_bootstrap(y, p, groups, baseline, seed=SEED, resamples=300):
    y, p, groups = np.asarray(y), np.asarray(p), np.asarray(groups)
    unique = sorted(set(groups))
    indices = {group: np.flatnonzero(groups == group) for group in unique}
    values = defaultdict(list)
    rng = np.random.default_rng(seed)
    skipped = 0
    for _ in range(resamples):
        sample = np.concatenate([indices[group] for group in rng.choice(unique, len(unique), replace=True)])
        if len(set(y[sample])) < 2:
            skipped += 1
            continue
        result = metrics(y[sample], p[sample], baseline)
        for name in ('average_precision', 'brier', 'relative_brier_skill', 'roc_auc_diagnostic'):
            values[name].append(result[name])
    return {'sampling_unit': 'group_id_with_replacement_all_rows', 'resamples': resamples,
            'seed': seed, 'single_class_resamples_skipped': skipped,
            'method': 'percentile_2.5_97.5_conditional_on_fixed_fit',
            'intervals_95': {name: np.quantile(scores, [0.025, 0.975]).tolist()
                             for name, scores in values.items()}}


def cohort_keys(row):
    cohort = '6plus_valid_months' if row['coverage']['valid_months_6m'] == 6 else '3to5_valid_months'
    missing6 = any(value is None for name, value in row['features'].items() if name.endswith('_6m'))
    return [('group_id', row['group_id']), ('company_id', row['company_id']),
            ('history_cohort', cohort), ('missing_6m_features', str(missing6).lower())]


def aggregate_diagnostics(rows, y, p, baseline, prospective_rows=None):
    buckets, totals = defaultdict(list), defaultdict(int)
    for row in prospective_rows if prospective_rows is not None else rows:
        for key in cohort_keys(row):
            totals[key] += 1
    for i, row in enumerate(rows):
        for key in cohort_keys(row):
            buckets[key].append(i)
    y, p = np.asarray(y), np.asarray(p)
    result = defaultdict(list)
    for (kind, value), total in sorted(totals.items()):
        indices = buckets[(kind, value)]
        actual, predicted = y[indices], p[indices]
        result[kind].append({'value': value, 'labeled_rows': len(indices), 'prospective_rows': total,
                            'label_observation_fraction': len(indices) / total,
                            'brier': float(np.mean((actual - predicted) ** 2)) if indices else None,
                            'baseline_brier': float(np.mean((actual - baseline) ** 2)) if indices else None,
                            'prevalence': float(actual.mean()) if indices else None})
    for kind in ('group_id', 'company_id'):
        scores = [item['brier'] for item in result[kind] if item['brier'] is not None]
        result[kind + '_macro_brier'] = float(np.mean(scores)) if scores else None
    result['cohort_definition'] = 'Count of valid months in the last six calendar months; not company age. Six-month feature missingness reported separately.'
    result['diagnostic_only'] = 'No subgroup acceptance claims or tuning; Brier conditional on observed labels.'
    return dict(result)
