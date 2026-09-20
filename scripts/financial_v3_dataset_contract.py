import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.financial_v3_audit import ROOT, RUN_ID, sha256, current_hashes, check_baseline
from scripts.financial_v3_contract import canonical_bytes, require


DIRECTORY = ROOT / 'reports/modeling/financial-v3/dataset-v1'
VERSION = 'financial-v3-dataset-v1'
SOURCES = tuple(f'data/runs/{RUN_ID}/data/{area}/{name}.parquet' for area, name in (
    ('clean', 'companies'), ('clean', 'banking_products'), ('clean', 'balances'),
    ('clean', 'transactions'), ('clean', 'invoices'), ('clean', 'debt_products'),
    ('marts', 'panel_flujos'), ('marts', 'panel_deuda'), ('marts', 'panel_cobro')))
ASSIGNMENT = 'reports/modeling/protocol-v1/group_assignment.json'


def policy():
    return {
        'version': VERSION, 'kind': 'financial_health_v3', 'scope': 'internal_retrospective_existing_synthetic_dataset',
        'months': ['2024-09-01', '2026-08-01'], 'companies': 1286, 'months_per_company': 24,
        'outcomes_computed': False, 'training_authorized_here': False, 'independent_test': False,
        'strict_core_unchanged': True, 'automatic_alerts': False,
        'availability': {'known_on': None, 'retrieved_at': '2026-09-19T12:48:14Z',
            'nominal_dataset_snapshot_date': '2026-09-01',
            'debt_snapshot_date_basis': 'No measured date on debt_products; display only extraction context at pinned run retrieval, measured_at=null, never a historical stock.',
            'assumption_id': 'dataset-v1-booked-issued-month-end-retrospective',
            'assumed_features': 'Booked transactions and immutable issued invoice face/due fields assumed available at origin month-end; no historical ingestion evidence. This is simulation, never actual historical issuance.',
            'never_features': ['reversed_cash', 'balance_anchors', 'debt_snapshot', 'invoice_terminal_status',
                'invoice_payment_date', 'invoice_pending_amount', 'panel_cobro_stocks', 'cohort_outcomes', 'score'],
            'strict_historical_api': 'Reject dataset profiles; no verified known_on. Retrospective date selection is not as-of evidence.'},
        'flow_scope': 'Booked checking/saving/wallet panel_flujos. Known EUR subtotals, complete observed-currency totals and classification bounds are distinct. No bound covers missing company transactions. Financing/investment/transfer/adjustment/unknown stay separate.',
        'cash': {'basis': 'Product-native signed booked movement reversal, two scenarios: anchor opening day versus closing day. No available balance, no reconciliation claim, no stock FX invented.',
            'gaps': 'Missing active months counted; no-activity bridge sums mean observed ledger only, not a complete account. Month before first observed activity is unavailable.',
            'availability': 'Final snapshot retrieval only, all historical reversals forbidden as model inputs.'},
        'invoices': {'roles': 'negative=AP, positive=AR sign proxy, not verified economic role',
            'stocks': 'panel_cobro final-payment-state retrospective estimates, not historical legal balances. Unknown partial allocation stays unknown; cancellation is omitted by source without effective history.',
            'due_proxy': 'Absolute signed AP invoice face with valid issuance <= origin cutoff and due in origin month; include ALL final statuses, including cancel, to avoid future status selection. This is an issued-document commitment proxy, not proof enforceable or complete obligations. No banking payment is added.',
            'duplicates': 'Reject duplicate operation_id before aggregation; no guessed settlement allocations.',
            'mora': 'Weighted AP overdue exposure, not legal extra debt. panel 1-30 bin yields multiplier interval [1,1.25], then 1.5/2/3/4/4; missing bins produce null. Never equate missing exposure with zero.'},
        'growth': {'metric': 'Observed classified operating receipts, not sales',
            'windows': {'mom': 'm versus m-1', 'three_vs_three': 'm-2..m versus m-5..m-3', 'yoy': 'm versus m-12'},
            'formula': '(current_sum-prior_sum)/prior_sum; prior_sum>0',
            'complete': 'All requested calendar months and receipts known; publish EUR/classification coverage and active-perimeter changes. Known-subtotal growth is separately identified, never true-company growth.'},
        'scoring': {'version': VERSION + '-observed-scorecard-1', 'learned': False, 'validated': False,
            'full_headline': None, 'essential_missing': ['unrestricted_reconciled_cash', 'financial_debt_history', 'complete_obligations', 'verified_role_and_availability'],
            'components': 'Pure dimension_points reused for generation/growth/stability only. S=mean six observed receipts; F=receipts minus due AP face proxy. No cash-payment cost reduction.',
            'operating_weights': {'generation': 0.4, 'growth': 0.3, 'stability': 0.3},
            'mora_penalty': '40*E/(S+E); unknown E implies [0,40]. Missing dimensions imply [0,100], fixed weights, no renormalization. Publish interval only, never midpoint.',
            'interval_scope': 'Operating document/receipt scope only, NOT global health or a confidence interval. True company full interval remains [0,100] because unobserved perimeter is unbounded.',
            'nonpayment': 'With fixed receipts and issued due obligations, reduced bank payment cannot increase any component. Provisional mora can only lower the scoped score; missing mora gives uncertainty, not zero.'},
        'full_score_interval': [0, 100],
        'modeling': {'input_interface': 'features.jsonl: company_id/group_id/month/split/window/eligible/reasons/values. Real input eligibility needs 3 contiguous cash-active months, no missing EUR, positive observed receipt reference. Full score eligibility is separate and false.',
            'features': 'Current plus trailing 3m booked receipt/payment/classification-bound amounts; EUR/classification coverage; observed debt-service subtotal; issued AP/AR and due AP face proxy. No IDs as model predictors; no stock, final-state or score features.',
            'fit': {'groups': 'existing train only (150)', 'origins': ['2024-11-01', '2025-03-01'], 'label_maturity_max': '2025-06-30'},
            'validation': {'groups': 'existing validation only (50)', 'origins': ['2025-07-01', '2025-09-01'], 'label_maturity_max': '2025-12-31'},
            'purge': 'April-June 2025 origins excluded; every fit outcome ends before first validation origin. Three-month follow-up only. No group overlap. No retuning split after outcomes.',
            'exclusions': 'All 50 final_test groups excluded from features/labels/development/case selection. Product-only profiles allowed for all. No 2026 origins/outcomes; v2 March-May alerts and outcomes through August not revisited.',
            'recommended_targets': {'interface': 'Future worker separately constructs labels(company_id, origin, direction, value|null, onset|null, confirmation|null, matured_at, censor_reasons, source_hashes), never imported by this adapter.',
                'operating_receipt_contraction_3m': 'Origin baseline=mean observed receipts m-2..m >0; target true if at least 2 of m+1..m+3 have operating receipts <=0.8*baseline; false only with all three observable and fewer than two hits. Require classification-admissible EUR months and stable observed account perimeter across baseline/followup; otherwise unknown.',
                'operating_receipt_expansion_3m': 'Same rule with >=1.2*baseline; independent of score, not solvency or general improvement. Receipt-only direction does not reward withholding payment.',
                'limits': 'Synthetic measured-input proxy; classification, perimeter, seasonality and availability assumptions. Targets here are proposed/frozen, not computed. No six-question or scientific success claim. Due-obligation/deficit event may be future separately preregistered work, never silently substituted.'},
            'next_models': 'Train real-input prevalence, persistence, trend, then bounded regularized logistic baseline on these windows; fixture models forbidden on real companies. Fit preprocessing only on fit groups/dates. Internal retrospective result only.'},
        'money_wire': 'Finite JSON numbers in native units/EUR, source DOUBLE precision; display 2 decimals, do not treat as legal settlement cents. Ratios and points unrounded.',
        'artifact_layout': 'index.json plus profiles/<company_id>.json, features.jsonl, coverage.json, schema.json, manifest.json. Load one company; never a 99MiB monolith.',
        'source_files': list(SOURCES) + [ASSIGNMENT],
    }


def development_window(split, month):
    if split == 'train' and '2024-11-01' <= month <= '2025-03-01':
        return 'fit'
    if split == 'validation' and '2025-07-01' <= month <= '2025-09-01':
        return 'internal_validation'
    return None


def validate_company(value):
    keys = {'id', 'name', 'group', 'currency', 'kind', 'assessment_date', 'model_version', 'history_mode',
            'data_mode', 'opening_cash_cents', 'buffer_cents', 'health', 'history', 'flows', 'coverage', 'drivers', 'predictive', 'financial_v3'}
    require(type(value) is dict and set(value) == keys, 'Dataset Company fields mismatch')
    require(value['kind'] == 'financial_health_v3' and value['model_version'] == VERSION, 'Dataset version mismatch')
    require(all(value[k] is None for k in ('opening_cash_cents', 'buffer_cents', 'health', 'predictive')), 'No fabricated cash/headline/predictor')
    require(all(value[k] == [] for k in ('history', 'flows', 'coverage', 'drivers')), 'Legacy arrays must be empty')
    require(value['history_mode'] == 'retrospective' and value['data_mode'] == 'synthetic', 'Retrospective synthetic scope required')
    require(set(value['financial_v3']) == {'points', 'meta'}, 'Financial payload fields mismatch')
    points = value['financial_v3']['points']
    require(len(points) == 24 and len({p['month'] for p in points}) == 24, '24 unique monthly slots required')
    for point in points:
        require(point['score']['headline'] is None and point['score']['full_company_interval'] == [0, 100], 'Strict headline remains unavailable')
        require(point['known_on'] is None, 'Do not fabricate known_on')
    canonical_bytes(value)
    return value


def protected_hashes():
    values = current_hashes(ROOT)
    for area in ('scripts', 'tests', 'reports/modeling/financial-v3'):
        for path in sorted((ROOT / area).rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.is_relative_to(DIRECTORY):
                continue
            if 'financial_v3_dataset' in path.name:
                continue
            if area.startswith('reports') or 'financial_v3' in path.name:
                values[path.relative_to(ROOT).as_posix()] = sha256(path)
    return dict(sorted(values.items()))


def write_new(path, value):
    with path.open('xb') as handle:
        handle.write(canonical_bytes(value) + b'\n')


def freeze():
    require(not (DIRECTORY / 'policy.json').exists() and not (DIRECTORY / 'receipt.json').exists(), 'Policy already frozen')
    require(check_baseline(ROOT)['ok'], 'Protected audit mismatch')
    DIRECTORY.mkdir(exist_ok=True)
    inputs = {p: sha256(ROOT / p) for p in policy()['source_files']}
    inputs[f'data/runs/{RUN_ID}/manifest.json'] = sha256(ROOT / f'data/runs/{RUN_ID}/manifest.json')
    write_new(DIRECTORY / 'policy.json', policy())
    receipt = {'version': VERSION, 'frozen_at': datetime.now(timezone.utc).isoformat(),
               'before_any_dataset_value_queries': True, 'outcomes_or_training': False,
               'policy_sha256': sha256(DIRECTORY / 'policy.json'), 'inputs_sha256': inputs,
               'protected_before_sha256': protected_hashes(),
               'contract_sha256': sha256(Path(__file__))}
    write_new(DIRECTORY / 'receipt.json', receipt)
    return {'ok': True, 'protected_files': len(receipt['protected_before_sha256']), 'inputs': len(inputs)}


def check():
    receipt = json.loads((DIRECTORY / 'receipt.json').read_text())
    require(json.loads((DIRECTORY / 'policy.json').read_text()) == policy(), 'Policy changed')
    require(sha256(DIRECTORY / 'policy.json') == receipt['policy_sha256'], 'Policy hash mismatch')
    require(sha256(Path(__file__)) == receipt['contract_sha256'], 'Frozen dataset contract changed')
    require(all(sha256(ROOT / p) == h for p, h in receipt['inputs_sha256'].items()), 'Input mismatch')
    require(protected_hashes() == receipt['protected_before_sha256'], 'Protected file change')
    return {'ok': True, 'inputs': len(receipt['inputs_sha256']), 'protected_files': len(receipt['protected_before_sha256']), 'queries': 0, 'writes': 0}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--freeze', action='store_true')
    group.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(freeze() if args.freeze else check(), sort_keys=True))


if __name__ == '__main__':
    main()
