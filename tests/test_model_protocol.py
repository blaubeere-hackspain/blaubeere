import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb

from scripts.model_features import build_features
from scripts.model_protocol import (
    assign_groups, build_split_keys, load_development_labels, prepare,
    protocol_contract, read_prepared, sha256_file, support_gate,
)
from tests.test_model_features import panel_rows


class ModelProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'data/runs/fixture'
        (self.run / 'data/marts').mkdir(parents=True)
        rows = [row for n in range(10) for row in panel_rows(f'c{n}', f'g{n}')]
        panel_json = self.root / 'panel.json'
        panel_json.write_text(json.dumps(rows, default=str))
        with duckdb.connect() as con:
            con.execute('CREATE TEMP TABLE fixture_panel AS SELECT * FROM read_json_auto(?)', [str(panel_json)])
            con.execute('COPY fixture_panel TO ? (FORMAT PARQUET)',
                        [str(self.run / 'data/marts/panel_flujos.parquet')])
        target = self.run / 'data/marts/targets_proxy.parquet'
        target.write_bytes(b'POISON: preparation must not parse outcomes')
        self.bind_manifest()
        self.out = self.root / 'reports/modeling/protocol-v1'

    def bind_manifest(self):
        manifest = {'run_id': 'fixture', 'parameters': {'target_version': 1,
                    'target_horizon_months': 3, 'min_deficit_months': 2,
                    'dataset_end': '2026-08-31'}, 'inputs': {},
                    'outputs': {f'data/marts/{name}.parquet': sha256_file(
                        self.run / f'data/marts/{name}.parquet')
                        for name in ('panel_flujos', 'targets_proxy')}}
        (self.run / 'manifest.json').write_text(json.dumps(manifest))
        (self.root / 'reports').mkdir(exist_ok=True)
        (self.root / 'reports/current.json').write_text(json.dumps({
            'workspace': 'data/runs/fixture',
            'manifest_sha256': sha256_file(self.run / 'manifest.json')}))

    def test_deterministic_hash_partition_and_disjoint_groups(self):
        groups = [f'g{i}' for i in range(100)]
        result = assign_groups(groups)
        self.assertEqual(result, assign_groups(reversed(groups)))
        self.assertEqual(result, assign_groups(groups + groups))
        ordered = sorted(groups, key=lambda g: (hashlib.sha256(('deficit-v1:' + g).encode()).hexdigest(), g))
        self.assertTrue(all(result[g] == 'train' for g in ordered[:60]))
        self.assertTrue(all(result[g] == 'validation' for g in ordered[60:80]))
        self.assertTrue(all(result[g] == 'final_test' for g in ordered[80:]))
        with self.assertRaises(ValueError):
            assign_groups(['g1', None])

    def test_train_purge_chronology_and_reserved_dates(self):
        groups = assign_groups([f'g{i}' for i in range(10)])
        rows = [r for i in range(10) for r in panel_rows(f'c{i}', f'g{i}')]
        keys = build_split_keys(build_features(rows), groups)
        self.assertEqual(set(keys), {'fold1_train', 'fold1_validation', 'fold2_train',
                                    'fold2_validation', 'final_train', 'final_test'})
        for prefix, cutoff, start in [('fold1', date(2025, 6, 30), date(2025, 7, 1)),
                                      ('fold2', date(2025, 9, 30), date(2025, 10, 1)),
                                      ('final', date(2026, 3, 31), date(2026, 4, 1))]:
            train = keys[f'{prefix}_train']
            evaluation = keys['final_test' if prefix == 'final' else f'{prefix}_validation']
            self.assertTrue(train and evaluation)
            self.assertTrue(all(k['label_available_at'] <= cutoff and k['month'] < start for k in train))
            self.assertFalse({k['group_id'] for k in train} & {k['group_id'] for k in evaluation})
        self.assertEqual({k['month'] for k in keys['final_test']}, {date(2026, 4, 1), date(2026, 5, 1)})
        self.assertEqual(max(k['label_available_at'] for k in keys['fold2_validation']), date(2026, 3, 31))
        self.assertFalse(any(k['month'] >= date(2026, 6, 1) for values in keys.values() for k in values))

    def test_prepare_writes_keys_and_contract_without_fetching_any_outcomes(self):
        with patch('scripts.model_protocol.load_development_labels', side_effect=AssertionError('outcomes')):
            result = prepare(self.root, self.out)
        self.assertEqual(result['label_values_fetched'], 0)
        self.assertEqual(result['status'], 'prepared_not_trained')
        self.assertTrue((self.out / 'protocol.json').is_file())
        self.assertTrue((self.out / 'feature_contract.json').is_file())
        self.assertTrue((self.out / 'splits/final_test.parquet').is_file())
        with duckdb.connect() as con:
            schema = con.execute('DESCRIBE SELECT * FROM read_parquet(?)',
                                 [str(self.out / 'features.parquet')]).fetchall()
        self.assertFalse(any('target' in r[0] for r in schema))
        self.assertEqual(read_prepared(self.out)['protocol']['target']['version'], 1)

    def test_no_overwrite_and_integrity_verification(self):
        prepare(self.root, self.out)
        before = sha256_file(self.out / 'protocol.json')
        with self.assertRaises(FileExistsError):
            prepare(self.root, self.out)
        self.assertEqual(before, sha256_file(self.out / 'protocol.json'))
        with (self.out / 'features.parquet').open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaises(ValueError):
            read_prepared(self.out)

    def test_input_hash_mismatch_stops_preparation(self):
        with (self.run / 'data/marts/panel_flujos.parquet').open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaises(ValueError):
            prepare(self.root, self.out)
        self.assertFalse(self.out.exists())

    def test_loader_preserves_censoring_and_rejects_holdout_before_query(self):
        with duckdb.connect() as con:
            con.execute('''CREATE TABLE labels AS SELECT
                'c'||i::VARCHAR AS company_id, 'g'||i::VARCHAR AS group_id,
                m::DATE AS month, last_day(m + INTERVAL 3 MONTH) AS available_at_deficit,
                CASE WHEN month=DATE '2025-07-01' THEN NULL ELSE true END AS target_deficit_3m,
                1 AS target_version
                FROM range(10) t(i), generate_series(DATE '2024-09-01', DATE '2026-08-01', INTERVAL 1 MONTH) d(m)''')
            con.execute('COPY labels TO ? (FORMAT PARQUET)',
                        [str(self.run / 'data/marts/targets_proxy.parquet')])
        self.bind_manifest()
        prepare(self.root, self.out)
        with duckdb.connect() as con:
            labels = load_development_labels(con, self.out, 'fold1_validation')
        self.assertTrue(labels)
        self.assertTrue(all(row['value'] is None for row in labels if row['month'] == date(2025, 7, 1)))
        self.assertTrue(all(row['value'] is True for row in labels if row['month'] != date(2025, 7, 1)))
        self.assertTrue(all(row['month'] <= date(2025, 9, 1) for row in labels))
        with self.assertRaises(PermissionError):
            load_development_labels(None, self.out, 'final_test')
        with self.assertRaises(ValueError):
            load_development_labels(None, self.out, 'arbitrary')

    def test_support_gate_blocks_missing_classes_and_insufficient_groups(self):
        labels = [{'value': bool(i % 2), 'group_id': f'g{i % 10}'} for i in range(60)]
        self.assertTrue(support_gate(labels)['passed'])
        self.assertFalse(support_gate([dict(r, value=True) for r in labels])['passed'])
        self.assertFalse(support_gate([dict(r, group_id='one') for r in labels])['passed'])
        self.assertFalse(support_gate([{'value': None, 'group_id': 'g'}])['passed'])
        contract = protocol_contract()
        self.assertEqual(contract['selection']['required_folds'], ['fold1', 'fold2'])
        self.assertEqual(contract['acceptance']['pr_auc'], 'average_precision > evaluation_prevalence')
        self.assertEqual(contract['acceptance']['brier_max_baseline_ratio'], 0.95)


if __name__ == '__main__':
    unittest.main()
