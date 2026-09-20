import hashlib
import json
import math
from pathlib import Path
import unittest

from scripts.financial_v3_dataset_contract import DIRECTORY, ROOT


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


@unittest.skipUnless((DIRECTORY / 'manifest.json').exists(), 'Requires sealed input-profile export, never outcomes')
class IndependentExportValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((DIRECTORY / 'manifest.json').read_text())
        cls.index = json.loads((DIRECTORY / 'index.json').read_text())['companies']
        cls.receipt = json.loads((DIRECTORY / 'receipt.json').read_text())

    def test_negative_booked_movement_reverses_with_its_sign(self):
        from scripts.financial_v3_dataset import reverse_account
        anchor = {'product_id': 'p', 'company_id': 'c', 'currency': 'EUR', 'anchor_currency': 'EUR',
                  'anchor_company': 'c', 'balance': 1000.0, 'anchor_date': '2026-09-01',
                  'before_anchor': 300.0, 'through_anchor': 320.0, 'bad_rows': 0}
        rows = {'2026-07-01': {'net': 400.0, 'n': 1}, '2026-08-01': {'net': -100.0, 'n': 1}}
        result = reverse_account(anchor, rows, '2026-08-01')
        self.assertEqual(result['closing_scenarios'], [1000.0, 980.0])
        self.assertEqual(result['opening_scenarios'], [1100.0, 1080.0])

    def test_all_input_and_protected_hashes_after(self):
        for key in ('inputs_sha256', 'protected_before_sha256'):
            for name, expected in self.receipt[key].items():
                self.assertEqual(digest(ROOT / name), expected, name)
        self.assertEqual(len(self.receipt['protected_before_sha256']), 574)

    def test_bounded_index_and_profile_files(self):
        self.assertEqual(len(self.index), 1286)
        self.assertLess((DIRECTORY / 'index.json').stat().st_size, 1024*1024)
        self.assertLess(max(item['bytes'] for item in self.index), 512*1024)
        self.assertEqual(sum(item['bytes'] for item in self.index), self.manifest['profile_bytes'])

    def test_independent_financial_identities_and_unknowns(self):
        count = 0
        for item in self.index:
            company = json.loads((DIRECTORY / item['path']).read_text())
            self.assertIsNone(company['health'])
            for p in company['financial_v3']['points']:
                count += 1
                self.assertIsNone(p['known_on'])
                f = p['flow']
                if f['net_known'] is not None:
                    components = list(f['composition'].values())
                    self.assertAlmostEqual(math.fsum(a-b for a, b in components), f['net_known'], delta=0.02)
                    self.assertAlmostEqual(f['known_in_out'][0]-f['known_in_out'][1], f['net_known'], delta=0.02)
                if not f['observed']:
                    self.assertIsNone(f['net_observed'])
                E = p['invoices']['mora_exposure_range']
                if E is not None:
                    bins = p['invoices']['ap']['aging']
                    lower = sum(a*w for a, w in zip(bins, [1, 1.5, 2, 3, 4, 4]))
                    self.assertAlmostEqual(E[0], lower, delta=0.02)
                    self.assertAlmostEqual(E[1]-E[0], bins[0]*0.25, delta=0.02)
                for a in p['cash']['accounts']:
                    self.assertFalse(a['verified'])
                    self.assertIsNone(a['available_balance'])
                    if a['closing_range'] is not None:
                        self.assertEqual(a['closing_range'], sorted(a['closing_scenarios']))
                score = p['score']
                self.assertIsNone(score['headline'])
                self.assertEqual(score['full_company_interval'], [0, 100])
                self.assertLessEqual(0, score['operating_scope_interval'][0])
                self.assertLessEqual(score['operating_scope_interval'][0], score['operating_scope_interval'][1])
                self.assertLessEqual(score['operating_scope_interval'][1], 100)
        self.assertEqual(count, 30864)

    def test_feature_groups_time_and_no_terminal_inputs(self):
        rows = [json.loads(line) for line in (DIRECTORY / 'features.jsonl').read_text().splitlines()]
        allowed = {'receipts_known', 'receipts_known_mean3', 'payments_known', 'payments_known_mean3',
            'operating_low', 'operating_low_mean3', 'operating_high', 'operating_high_mean3',
            'classification_coverage', 'classification_coverage_mean3', 'eur_coverage', 'eur_coverage_mean3',
            'ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows', 'due_missing_eur_rows', 'service_paid_known'}
        groups = {'fit': set(), 'internal_validation': set()}
        for row in rows:
            self.assertEqual(set(row['values']), allowed)
            self.assertFalse(row['strict_fullscore_eligible'])
            self.assertLess(row['month'], '2026-01-01')
            self.assertNotEqual(row['split'], 'final_test')
            groups[row['window']].add(row['group_id'])
            if row['window'] == 'fit':
                self.assertLessEqual(row['month'], '2025-03-01')
            else:
                self.assertGreaterEqual(row['month'], '2025-07-01')
                self.assertLessEqual(row['month'], '2025-09-01')
        self.assertFalse(groups['fit'] & groups['internal_validation'])
        self.assertEqual(len(rows), 4930)
        self.assertEqual(sum(r['eligible'] for r in rows), 1881)

    def test_schema_full_export_read_only(self):
        from scripts.financial_v3_dataset import validate_wire, schema
        spec = schema()
        for item in self.index:
            validate_wire(json.loads((DIRECTORY / item['path']).read_text()), spec)

    def test_report_source_backed_sample_and_integrity(self):
        sample = json.loads((DIRECTORY / 'sample.json').read_text())
        p = sample['point']
        examples = []
        for item in self.index:
            profile = json.loads((DIRECTORY / item['path']).read_text())
            if profile['financial_v3']['meta']['product_only_group']:
                continue
            for point in profile['financial_v3']['points']:
                if point['month'] <= '2025-09-01' and all(v is not None for v in point['score']['components'].values()):
                    examples.append({'company_id': item['id'], 'month': point['month'], 'score': point['score'],
                                     'due_face_proxy': point['documents']['ap_due_face']})
                    break
            if len(examples) == 1:
                break
        report = {'protected_before_count': len(self.receipt['protected_before_sha256']), 'protected_after_match': True,
            'input_hash_count': len(self.receipt['inputs_sha256']), 'artifact_count_without_verification': sum(p.is_file() and p.name != 'verification.json' for p in DIRECTORY.rglob('*')),
            'max_profile_bytes': self.manifest['max_profile_bytes'], 'index_bytes': (DIRECTORY / 'index.json').stat().st_size,
            'profile_bytes': self.manifest['profile_bytes'], 'feature_bytes': (DIRECTORY / 'features.jsonl').stat().st_size,
            'input_queries_export': len(json.loads((DIRECTORY / 'queries.json').read_text())['queries']),
            'numeric_example': {'company_id': sample['company_id'], 'month': p['month'], 'flow': p['flow'],
                                'score': p['score'], 'documents': p['documents']},
            'component_example': examples, 'example_selection': 'First lexicographic non-holdout company with all observed components in development dates; source coverage only, no labels or outcomes',
            'new_code_sha256': {str(path.relative_to(ROOT)): digest(path) for pattern in ('scripts/financial_v3_dataset*.py', 'tests/test_financial_v3_dataset*.py') for path in ROOT.glob(pattern)},
            'artifact_sha256': {name: digest(DIRECTORY / name) for name in ('policy.json', 'receipt.json', 'manifest.json', 'index.json', 'features.jsonl', 'coverage.json', 'schema.json', 'sample.json', 'queries.json')}}
        print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
