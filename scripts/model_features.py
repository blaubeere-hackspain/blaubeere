import calendar
import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


SOURCE_COLUMNS = (
    'company_id', 'group_id', 'month', 'available_at', 'tiene_actividad_caja',
    'es_calentamiento', 'n_sin_eur', 'n_tx_caja', 'n_cuentas_activas',
    'volumen_caja_conocido_eur', 'neto_caja_eur',
    'operativo_min_eur', 'operativo_max_eur',
    'operativo_min_sin_atipicos_eur', 'operativo_max_sin_atipicos_eur',
    'cobros_operativos_conocido_eur', 'pagos_operativos_conocido_eur',
    'financiacion_entradas_conocido_eur', 'financiacion_salidas_conocido_eur',
    'ambiguo_entradas_conocido_eur', 'ambiguo_salidas_conocido_eur',
)
BASE_FEATURES = (
    'operating_min_ratio', 'operating_max_ratio', 'net_ratio',
    'operating_in_ratio', 'operating_out_ratio', 'financing_in_ratio',
    'financing_out_ratio', 'uncertainty_ratio', 'log_gross', 'log_tx',
    'log_accounts', 'operating_identified_ratio',
)
WINDOW_FEATURES = (
    'net_ratio_mean', 'net_ratio_std', 'net_ratio_slope',
    'operating_min_ratio_mean', 'operating_max_ratio_mean',
    'log_gross_mean', 'log_gross_std', 'deficit_frequency',
    'net_ratio_delta_prior', 'log_gross_delta_prior',
)
FEATURE_NAMES = BASE_FEATURES + tuple(f'{name}_{n}m' for n in (3, 6) for name in WINDOW_FEATURES)


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def shift_month(value, offset):
    value = as_date(value)
    year, month = divmod(value.year * 12 + value.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def last_day(value):
    value = as_date(value)
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def finite(value):
    return value is not None and math.isfinite(float(value))


def month_problem(row):
    if row is None:
        return 'missing_month'
    if row['tiene_actividad_caja'] is not True:
        return 'no_cash_activity'
    if row['n_sin_eur'] != 0:
        return 'unknown_fx'
    gross = row['volumen_caja_conocido_eur']
    if not finite(gross) or gross <= 0:
        return 'nonpositive_or_unknown_gross'
    return None


def robust_deficit(row):
    def state(lower, upper):
        if not finite(lower) or not finite(upper):
            return None
        cents = lambda value: Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if cents(upper) < 0:
            return True
        if cents(lower) >= 0:
            return False
        return None
    base = state(row['operativo_min_eur'], row['operativo_max_eur'])
    trimmed = state(row['operativo_min_sin_atipicos_eur'], row['operativo_max_sin_atipicos_eur'])
    return float(base) if base is not None and base == trimmed else None


def monthly_values(row):
    if month_problem(row):
        return {name: None for name in BASE_FEATURES}, None
    gross = float(row['volumen_caja_conocido_eur'])

    def ratio(*columns):
        values = [row[column] for column in columns]
        return sum(values) / gross if all(finite(value) for value in values) else None

    def log(column):
        value = row[column]
        return math.log1p(value) if finite(value) and value >= 0 else None

    values = dict(zip(BASE_FEATURES, (
        ratio('operativo_min_eur'), ratio('operativo_max_eur'), ratio('neto_caja_eur'),
        ratio('cobros_operativos_conocido_eur'), ratio('pagos_operativos_conocido_eur'),
        ratio('financiacion_entradas_conocido_eur'), ratio('financiacion_salidas_conocido_eur'),
        ratio('ambiguo_entradas_conocido_eur', 'ambiguo_salidas_conocido_eur'),
        log('volumen_caja_conocido_eur'), log('n_tx_caja'), log('n_cuentas_activas'), None,
    )))
    if values['operating_in_ratio'] is not None and values['operating_out_ratio'] is not None:
        values['operating_identified_ratio'] = values['operating_in_ratio'] - values['operating_out_ratio']
    return values, robust_deficit(row)


def slope(values):
    center = (len(values) - 1) / 2
    return sum((i - center) * value for i, value in enumerate(values)) / sum(
        (i - center) ** 2 for i in range(len(values)))


def build_features(rows, origins=None, cutoff=None):
    """Return keyed records with nested features/coverage; no target input is consulted."""
    panel = defaultdict(dict)
    groups = {}
    for raw in rows:
        row = {name: raw[name] for name in SOURCE_COLUMNS}
        row['month'], row['available_at'] = as_date(row['month']), as_date(row['available_at'])
        company, group, month = row['company_id'], row['group_id'], row['month']
        if not isinstance(company, str) or not company or not isinstance(group, str) or not group:
            raise ValueError('Nonempty string company/group keys required')
        if month.day != 1 or month in panel[company]:
            raise ValueError('Month-start grain must be unique per company')
        if company in groups and groups[company] != group:
            raise ValueError('Company group changed inside the source panel')
        groups[company] = group
        panel[company][month] = row
    requested = sorted(set(origins if origins is not None else (
        (company, month) for company, months in panel.items() for month in months)))
    records = []
    for company, raw_month in requested:
        month = as_date(raw_month)
        if month.day != 1:
            raise ValueError('Origins must be calendar month starts')
        known_as_of = last_day(month)
        if cutoff is not None and known_as_of > as_date(cutoff):
            continue
        history = {m: row for m, row in panel.get(company, {}).items()
                   if m <= month and last_day(m) <= known_as_of and row['available_at'] <= known_as_of}
        if month not in history:
            continue
        current = history[month]
        values, _ = monthly_values(current)
        missing = {name: month_problem(current) or 'unknown_source_value'
                   for name, value in values.items() if value is None}
        recent = [history.get(shift_month(month, i)) for i in range(-2, 1)]
        reasons = sorted({reason for row in recent if (reason := month_problem(row))})
        active = [m for m, row in history.items() if row['tiene_actividad_caja'] is True]
        if (current['es_calentamiento'] is not False or not active
                or min(active) > shift_month(month, -3)):
            reasons.append('less_than_three_prior_months')
        coverage = {'eligibility_reasons': reasons, 'n_sin_eur_current': current['n_sin_eur']}
        for n in (3, 6):
            window = [history.get(shift_month(month, i)) for i in range(1 - n, 1)]
            problem = next((month_problem(row) for row in window if month_problem(row)), None)
            coverage[f'valid_months_{n}m'] = sum(month_problem(row) is None for row in window)
            monthly = [monthly_values(row) for row in window]
            prior = [history.get(shift_month(month, i)) for i in range(-n, 0)]
            prior_problem = next((month_problem(row) for row in prior if month_problem(row)), None)
            for metric in WINDOW_FEATURES:
                reason, value = problem, None
                if metric.endswith('_delta_prior'):
                    source = metric.removesuffix('_delta_prior')
                    observations = [monthly_values(row)[0][source] for row in prior]
                    reason = month_problem(current) or prior_problem
                    if not reason and values[source] is not None and all(v is not None for v in observations):
                        value = values[source] - statistics.fmean(observations)
                else:
                    if metric == 'deficit_frequency':
                        observations = [entry[1] for entry in monthly]
                        operation = statistics.fmean
                    else:
                        source, operation_name = metric.rsplit('_', 1)
                        observations = [entry[0][source] for entry in monthly]
                        operation = {'mean': statistics.fmean, 'std': statistics.pstdev, 'slope': slope}[operation_name]
                    if not reason and all(v is not None for v in observations):
                        value = operation(observations)
                name = f'{metric}_{n}m'
                values[name] = value
                if value is None:
                    missing[name] = reason or ('uncertain_or_atypical_sensitive_deficit'
                                              if metric == 'deficit_frequency' else 'unknown_source_value')
        records.append({'company_id': company, 'group_id': groups[company], 'month': month,
                        'known_as_of': known_as_of, 'eligible': not reasons,
                        'coverage': coverage, 'missing_reason': missing, 'features': values})
    return records


def load_panel(con, source):
    """Project only the approved panel columns, never SELECT * or other marts."""
    result = con.execute('SELECT ' + ','.join(SOURCE_COLUMNS) +
                         ' FROM read_parquet(?) ORDER BY company_id,month', [str(source)])
    return [dict(zip(SOURCE_COLUMNS, row)) for row in result.fetchall()]


def feature_contract():
    bases = {
        'operating_min_ratio': 'operativo_min_eur / gross',
        'operating_max_ratio': 'operativo_max_eur / gross',
        'net_ratio': 'neto_caja_eur / gross',
        'operating_in_ratio': 'cobros_operativos_conocido_eur / gross',
        'operating_out_ratio': 'pagos_operativos_conocido_eur / gross',
        'financing_in_ratio': 'financiacion_entradas_conocido_eur / gross',
        'financing_out_ratio': 'financiacion_salidas_conocido_eur / gross',
        'uncertainty_ratio': '(ambiguo_entradas_conocido_eur + ambiguo_salidas_conocido_eur) / gross',
        'log_gross': 'log1p(volumen_caja_conocido_eur)',
        'log_tx': 'log1p(n_tx_caja)', 'log_accounts': 'log1p(n_cuentas_activas)',
        'operating_identified_ratio': 'operating_in_ratio - operating_out_ratio',
    }
    definitions = dict(bases)
    for n in (3, 6):
        for metric in WINDOW_FEATURES:
            definitions[f'{metric}_{n}m'] = (
                f'current minus mean of prior {n} calendar months (current excluded)'
                if metric.endswith('_delta_prior') else
                f'{metric}: last {n} contiguous calendar months including current; population std; OLS slope per month')
    return {
        'version': 1, 'source': 'data/marts/panel_flujos.parquet',
        'source_columns': list(SOURCE_COLUMNS), 'feature_order': list(FEATURE_NAMES),
        'definitions': definitions, 'feature_type': 'nullable DOUBLE',
        'gross': 'volumen_caja_conocido_eur; positive, complete known EUR only',
        'availability': 'known_as_of=last_day(origin); all contributing available_at<=known_as_of; partial months excluded',
        'retrospective_limitation': 'Approved synthetic final snapshot; no import/category revision history. Not certified historical known_on.',
        'eligibility': 'Current and prior 2 calendar months active, n_sin_eur=0, gross>0; es_calentamiento=false and first past observed activity at least 3 months before origin',
        'window_missingness': 'Any absent/inactive/unknown-FX/nonpositive-gross month invalidates that window; never compress rows or fill zero; 6m completeness does not control eligibility',
        'deficit_frequency': 'Mean robust past states only when every state known: round bounds to cents half-away-from-zero; upper<0 true, lower>=0 false, otherwise unknown; require base/trimmed agreement',
        'quality': 'coverage and missing_reason are metadata, not predictors or fixed health penalties',
        'excluded': ['identifiers', 'dates', 'targets', 'future_observability', 'stocks', 'invoices',
                     'debt', 'final_company_metadata', 'future_activity'],
        'preprocessing': 'No fitted transforms in T2; imputation/scaling/selection train-only per fold in T3; preserve feature order',
        'label_independence': 'Prospective eligibility never requires future activity, target availability or non-null label',
    }
