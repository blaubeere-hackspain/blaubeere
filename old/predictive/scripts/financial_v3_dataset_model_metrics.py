import math
from collections import Counter, defaultdict

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from scripts.financial_v3_contract import require


FEATURES = tuple(k for name in ('receipts_known', 'payments_known', 'operating_low', 'operating_high',
    'classification_coverage', 'eur_coverage') for k in (name, name + '_mean3')) + (
    'ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows', 'service_paid_known')
TARGETS = ('receipt_contraction_3m', 'receipt_expansion_3m', 'current_receipt_dip_3m')
SEED = 1729


def classes(target):
    require(target in TARGETS, 'Unregistered target')
    return ('recovered', 'persistent', 'mixed') if target == TARGETS[2] else (0, 1)


def candidates(target):
    classes(target)
    return ['constant', 'logistic_C1'] if target == TARGETS[2] else ['constant', 'persistence', 'trend', 'logistic_C0.1', 'logistic_C1']


def support(rows, labels):
    per_class = {str(c): {'rows': sum(r['value'] == c for r in rows),
        'groups': len({r['feature']['group_id'] for r in rows if r['value'] == c}),
        'companies': len({r['feature']['company_id'] for r in rows if r['value'] == c})} for c in labels}
    groups = len({r['feature']['group_id'] for r in rows})
    return {'rows': len(rows), 'groups': groups, 'companies': len({r['feature']['company_id'] for r in rows}),
        'per_class': per_class, 'adequate': groups >= 10 and all(v['rows'] >= 20 and v['groups'] >= 5 for v in per_class.values()),
        'minimum_rows_per_class': 20, 'minimum_groups_per_class': 5, 'minimum_total_groups': 10}


def current_state(feature):
    x = feature['values']
    a, b = x['receipts_known'], x['receipts_known_mean3']
    if a is None or b is None or b <= 0:
        return 'unknown'
    return 'low' if a <= .8 * b else 'high' if a >= 1.2 * b else 'middle'


def matrix(features):
    return np.array([[np.nan if r['values'][n] is None else r['values'][n] for n in FEATURES] for r in features], dtype=float)


def transform(x, medians):
    missing = np.isnan(x)
    return np.concatenate([np.where(missing, medians, x), missing.astype(float)], axis=1)


def fit_candidate(name, rows, target):
    require(name in candidates(target), 'Candidate budget exceeded')
    labels = classes(target)
    if not support(rows, labels)['adequate']:
        return None
    counts = Counter(r['value'] for r in rows)
    prevalence = {str(c): counts[c] / len(rows) for c in labels}
    model = {'candidate': name, 'target': target, 'classes': list(labels), 'prevalence': prevalence,
        'training_support': support(rows, labels), 'feature_order': list(FEATURES), 'real_input_model': True,
        'calibration': 'uncalibrated_internal_retrospective', 'seed': SEED}
    if name == 'constant' or name == 'trend':
        return model
    if name == 'persistence':
        buckets = defaultdict(list)
        for r in rows:
            buckets[current_state(r['feature'])].append(r['value'])
        model['states'] = {k: {str(c): (sum(v == c for v in values) + 2 * prevalence[str(c)]) / (len(values) + 2)
            for c in labels} for k, values in buckets.items()}
        return model
    x = matrix([r['feature'] for r in rows])
    medians = [float(np.median(col[~np.isnan(col)])) if np.any(~np.isnan(col)) else 0.0 for col in x.T]
    expanded = transform(x, medians)
    scaler = StandardScaler().fit(expanded)
    C = .1 if name == 'logistic_C0.1' else 1.0
    lr = LogisticRegression(C=C, random_state=SEED, max_iter=2000, solver='lbfgs')
    with threadpool_limits(limits=2):
        lr.fit(scaler.transform(expanded), [r['value'] for r in rows])
    require(int(max(lr.n_iter_)) < 2000, 'Logistic did not converge; no repeat or retuning')
    model.update(C=C, classes=[v.item() if hasattr(v, 'item') else v for v in lr.classes_],
        medians=medians, mean=scaler.mean_.tolist(), scale=scaler.scale_.tolist(),
        coefficients=lr.coef_.tolist(), intercept=lr.intercept_.tolist(), n_iter=int(max(lr.n_iter_)),
        transformed_features=list(FEATURES) + [n + '__missing' for n in FEATURES],
        explanation='Exact standardized logit coefficients, not causal effects or score-point contributions; missing all-train columns use zero plus indicator.')
    portable = np.array([[predict(model, r['feature'])[str(c)] for c in model['classes']] for r in rows])
    with threadpool_limits(limits=2):
        expected = lr.predict_proba(scaler.transform(expanded))
    error = float(np.max(np.abs(expected - portable)))
    require(error < 1e-12, 'Portable coefficients do not match fitted sklearn probabilities')
    model['portable_max_error'] = error
    return model


def predict(model, feature):
    if model is None:
        return None
    name = model['candidate']
    if name == 'constant':
        return dict(model['prevalence'])
    if name == 'persistence':
        return dict(model['states'].get(current_state(feature), model['prevalence']))
    if name == 'trend':
        x = feature['values']
        last, mean = x['receipts_known'], x['receipts_known_mean3']
        if last is None or mean is None or mean <= 0:
            return dict(model['prevalence'])
        prior2 = (3 * mean - last) / 2
        projected = max(0, last + 2 * (last - prior2))
        hit = projected <= .8 * mean if model['target'] == TARGETS[0] else projected >= 1.2 * mean
        p = .8 if hit else .2
        return {'0': 1-p, '1': p}
    x = transform(matrix([feature]), model['medians'])[0]
    z = (x - np.array(model['mean'])) / np.array(model['scale'])
    logits = np.array(model['coefficients']) @ z + model['intercept']
    if len(model['classes']) == 2:
        v = float(logits[0])
        p = 1 / (1 + math.exp(-v)) if v >= 0 else math.exp(v) / (1 + math.exp(v))
        probabilities = [1-p, p]
    else:
        exp = np.exp(logits - max(logits))
        probabilities = exp / sum(exp)
    return {str(c): float(p) for c, p in zip(model['classes'], probabilities)}


def binary_metrics(y, p):
    y, p = np.asarray(y), np.asarray(p)
    alerts = p >= .6
    tp = int(np.sum(alerts & (y == 1)))
    fp = int(np.sum(alerts & (y == 0)))
    positives = int(sum(y))
    bins = []
    for i in range(5):
        mask = (p >= i / 5) & ((p < (i+1) / 5) if i < 4 else (p <= 1))
        bins.append({'low': i / 5, 'high': (i+1) / 5, 'rows': int(sum(mask)),
            'mean_probability': float(np.mean(p[mask])) if any(mask) else None,
            'observed_rate': float(np.mean(y[mask])) if any(mask) else None})
    ece = sum(b['rows'] * abs(b['mean_probability'] - b['observed_rate']) for b in bins if b['rows']) / len(y)
    return {'rows': len(y), 'positives': positives, 'prevalence': float(np.mean(y)),
        'average_precision': float(average_precision_score(y, p)) if positives else None,
        'brier': float(np.mean((y-p)**2)), 'calibration': bins, 'ece5': ece,
        'mean_probability': float(np.mean(p)), 'threshold': .6, 'alerts': int(sum(alerts)),
        'precision': tp / (tp+fp) if tp+fp else None, 'recall': tp / positives if positives else None,
        'false_alerts': fp, 'misses': positives-tp, 'alert_rate': float(np.mean(alerts))}


def evaluate(rows, predictions, labels):
    require(len(rows) == len(predictions), 'Prediction alignment mismatch')
    paired = [(r, p) for r, p in zip(rows, predictions) if p is not None]
    result = {'support': support(rows, labels), 'predicted_rows': len(paired), 'missing_predictions': len(rows)-len(paired),
        'per_class': {}, 'mean_group_brier': None, 'mean_company_brier': None, 'brier': None,
        'groups': {}, 'companies': {}, 'origin_months': {}, 'average_precision': None}
    if not paired:
        return result
    for c in labels:
        result['per_class'][str(c)] = binary_metrics([int(r['value'] == c) for r, _ in paired], [p[str(c)] for _, p in paired])
    for field, out in [('group_id', 'groups'), ('company_id', 'companies'), ('month', 'origin_months')]:
        grouped = defaultdict(list)
        for r, p in paired:
            grouped[r['feature'][field]].append((r, p))
        for group, selected in grouped.items():
            per_class = {str(c): binary_metrics([int(r['value'] == c) for r, _ in selected], [p[str(c)] for _, p in selected]) for c in labels}
            result[out][group] = {'rows': len(selected), 'brier': sum(v['brier'] for v in per_class.values()) / len(labels),
                'per_class': {c: {k: v[k] for k in ('positives', 'average_precision', 'brier', 'precision', 'recall', 'alerts', 'misses', 'false_alerts')} for c, v in per_class.items()}}
    result['brier'] = sum(v['brier'] for v in result['per_class'].values()) / len(labels)
    result['mean_group_brier'] = sum(v['brier'] for v in result['groups'].values()) / len(result['groups'])
    result['mean_company_brier'] = sum(v['brier'] for v in result['companies'].values()) / len(result['companies'])
    relevant = [result['per_class']['1']] if labels == (0, 1) else list(result['per_class'].values())
    for name in ('average_precision', 'precision', 'recall', 'alert_rate', 'ece5'):
        nums = [r[name] for r in relevant]
        result[name] = sum(nums) / len(nums) if all(v is not None for v in nums) else None
    return result


def choose(metrics, target):
    order = candidates(target)
    scores = {k: v['mean_group_brier'] for k, v in metrics.items() if v['mean_group_brier'] is not None and not v['missing_predictions']}
    if not scores:
        return None
    best = min(scores.values())
    return next(k for k in order if k in scores and scores[k] <= best + 1e-12)


def utility(metrics, selected, labeled_fraction, criteria):
    if selected is None:
        return {'pass': False, 'checks': {'selected_model': False}, 'reason': 'insufficient_support_or_no_validation_predictions'}
    chosen, constant = metrics[selected], metrics['constant']
    simple = [v['mean_group_brier'] for k, v in metrics.items() if k in ('constant', 'persistence', 'trend') and v['mean_group_brier'] is not None]
    def above(key, threshold):
        return chosen.get(key) is not None and chosen[key] >= threshold
    checks = {'validation_support': chosen['support']['adequate'],
        'labeled_fraction_of_eligible': labeled_fraction >= criteria['minimum_labeled_fraction'],
        'precision': above('precision', criteria['precision']), 'recall': above('recall', criteria['recall']),
        'ap_gain_vs_prevalence': (chosen['average_precision'] is not None and constant['average_precision'] is not None
            and chosen['average_precision'] >= constant['average_precision'] + criteria['ap_gain']),
        'brier_gain_vs_prevalence': constant['mean_group_brier'] is not None and constant['mean_group_brier']-chosen['mean_group_brier'] >= criteria['brier_gain_constant'],
        'brier_gain_vs_best_simple': bool(simple) and min(simple)-chosen['mean_group_brier'] >= criteria['brier_gain_simple'],
        'calibration_ece5': chosen.get('ece5') is not None and chosen['ece5'] <= criteria['maximum_ece5'],
        'alert_rate': chosen.get('alert_rate') is not None and chosen['alert_rate'] <= criteria['maximum_alert_rate']}
    return {'pass': all(checks.values()), 'checks': checks, 'selection_bias': 'Same internal validation used for selection and utility; not independent acceptance even if all criteria pass.'}
