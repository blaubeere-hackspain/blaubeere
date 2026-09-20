import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import trajectory_contract as tc


class TrajectoryContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'repo'
        self.root.mkdir()
        self.contract = tc.read_json(tc.ROOT / tc.PROTOCOL)
        self.run_name = f'data/runs/{tc.RUN_ID}'
        self.put(f'{self.run_name}/{tc.PANEL}', b'opaque fixture bytes; not a Parquet file')
        panel_sha = tc.digest(self.root / self.run_name / tc.PANEL)
        self.put_json(f'{self.run_name}/manifest.json', {'run_id': tc.RUN_ID, 'outputs': {tc.PANEL: panel_sha}})
        manifest_sha = tc.digest(self.root / self.run_name / 'manifest.json')
        binding = {'run_id': tc.RUN_ID, 'run_path': str(self.root / self.run_name),
                   'run_manifest_sha256': manifest_sha, 'input_sha256': {tc.PANEL: panel_sha}}
        self.put_json(f'{tc.V1}/protocol.json', {'binding': binding, 'selection': {'last_validation_label_matures': '2026-03-31'},
                                               'splits': {'final_train': {'labels_as_of': '2026-03-31'}}})
        self.put_json(f'{tc.V1}/feature_contract.json', {'binding': binding})
        groups = {f'GROUP_{i:04d}': 'train' if i <= 150 else 'validation' if i <= 200 else 'final_test' for i in range(1, 251)}
        self.put_json(tc.GROUPS, groups)
        self.put(f'{tc.MODEL}/model.joblib', b'opaque fixture bytes; not a pickle')
        model_sha = tc.digest(self.root / tc.MODEL / 'model.joblib')
        self.put_json(f'{tc.MODEL}/model_manifest.json', {'model_version': 'experiment-v1:hgb_leaf15', 'input_binding': binding,
                                                       'artifacts_sha256': {'model.joblib': model_sha}})
        self.contract['source'].update(panel_sha256=panel_sha, run_manifest_sha256=manifest_sha)
        for name in self.contract['metadata_inputs_sha256']:
            self.contract['metadata_inputs_sha256'][name] = tc.digest(self.root / name)
        self.contract['v1_probability_overlay'].update(model_sha256=model_sha,
            model_manifest_sha256=tc.digest(self.root / tc.MODEL / 'model_manifest.json'))
        self.save_contract()

    def put(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def put_json(self, relative, value):
        self.put(relative, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())

    def save_contract(self):
        self.put_json(tc.PROTOCOL, self.contract)

    def test_registered_bytes_match_markdown(self):
        plan = (tc.ROOT / 'reports/planning/financial-scoring-plan.md').read_text(encoding='utf-8')
        block = plan.split('```json\n', 1)[1].split('\n```', 1)[0] + '\n'
        self.assertEqual((tc.ROOT / tc.PROTOCOL).read_bytes(), block.encode())
        tc.validate_rules(tc.read_json(tc.ROOT / tc.PROTOCOL))

    def test_fixture_hashes_groups_no_outcomes_and_receipt_preserved(self):
        result = tc.check(self.root)
        receipt = self.root / tc.RECEIPT
        original, modified_at = receipt.read_bytes(), receipt.stat().st_mtime_ns
        self.assertEqual(result['included_groups'], 200)
        self.assertEqual(result['excluded_groups'], 50)
        self.assertEqual(result['outcome_values_read'], 0)
        self.assertEqual(result['group_counts'], {'train': 150, 'validation': 50, 'final_test': 50})
        self.assertEqual(result['protocol_sha256'], tc.digest(self.root / tc.PROTOCOL))
        self.assertEqual(result, tc.check(self.root))
        self.assertEqual(original, receipt.read_bytes())
        self.assertEqual(modified_at, receipt.stat().st_mtime_ns)

    def test_invalid_rules_exclusions_and_cutoffs_before_freeze(self):
        original = copy.deepcopy(self.contract)
        changes = [('direction', 'threshold', 0.01), ('direction', 'prior_month_offsets', [-4, -3, -2]),
                   ('events', 'improvement', 'defined by probability'), ('evaluation', 'lead_months', [True, 2]),
                   ('evaluation', 'included_assignments', ['train', 'validation', 'final_test']),
                   ('evaluation', 'excluded_groups_by_v1_design', 0),
                   ('v1_probability_overlay', 'first_permitted_as_of', '2026-03-31'),
                   ('v1_probability_overlay', 'training_and_selection_labels_through', '2026-04-30')]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                self.contract = copy.deepcopy(original)
                self.contract[section][key] = value
                self.save_contract()
                with self.assertRaisesRegex(ValueError, 'Invalid rule'):
                    tc.check(self.root)
                self.assertFalse((self.root / tc.RECEIPT).exists())

    def test_reserved_window_change_rejected(self):
        self.contract['evaluation']['reserved']['outcomes_through'] = '2026-07-31'
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'evaluation.reserved'):
            tc.check(self.root)

    def test_path_escape_and_symlink_rejected(self):
        for name in ('../outside', '/tmp/outside'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Path escape'):
                tc.inside(self.root, name)
        outside = self.root.parent / 'outside'
        outside.write_bytes(b'fixture')
        (self.root / 'escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Path escape'):
            tc.inside(self.root, 'escape')
        self.contract['metadata_inputs_sha256']['../outside'] = '0' * 64
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'Path escape'):
            tc.check(self.root)

    def test_assignment_counts_checked_even_with_matching_hash(self):
        groups = tc.read_json(self.root / tc.GROUPS)
        groups['GROUP_0250'] = 'train'
        self.put_json(tc.GROUPS, groups)
        self.contract['metadata_inputs_sha256'][tc.GROUPS] = tc.digest(self.root / tc.GROUPS)
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'group exclusions/counts'):
            tc.check(self.root)

    def test_v1_cutoff_binding_checked_even_with_matching_hash(self):
        name = f'{tc.V1}/protocol.json'
        value = tc.read_json(self.root / name)
        value['splits']['final_train']['labels_as_of'] = '2026-04-30'
        self.put_json(name, value)
        self.contract['metadata_inputs_sha256'][name] = tc.digest(self.root / name)
        self.save_contract()
        with self.assertRaisesRegex(ValueError, 'V1 cutoff mismatch'):
            tc.check(self.root)

    def test_each_input_hash_is_checked_without_parsing_binary_inputs(self):
        names = list(self.contract['metadata_inputs_sha256']) + [f'{self.run_name}/{tc.PANEL}', f'{self.run_name}/manifest.json',
                                                               f'{tc.MODEL}/model.joblib', f'{tc.MODEL}/model_manifest.json']
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'Input hash mismatch'):
                    tc.check(self.root)
                self.assertFalse((self.root / tc.RECEIPT).exists())
                path.write_bytes(original)

    def test_frozen_contract_bytes_cannot_change(self):
        tc.check(self.root)
        path = self.root / tc.PROTOCOL
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'Frozen protocol hash mismatch'):
            tc.check(self.root)

    def test_modified_receipt_is_not_overwritten(self):
        tc.check(self.root)
        receipt = tc.read_json(self.root / tc.RECEIPT)
        receipt['included_groups'] = 250
        self.put_json(tc.RECEIPT, receipt)
        original = (self.root / tc.RECEIPT).read_bytes()
        with self.assertRaisesRegex(ValueError, 'Protocol receipt mismatch'):
            tc.check(self.root)
        self.assertEqual(original, (self.root / tc.RECEIPT).read_bytes())

    def test_receipt_creation_is_exclusive_without_retry(self):
        original_open = Path.open
        calls = []

        def racing_open(path, mode='r', *args, **kwargs):
            if path == self.root / tc.RECEIPT and mode == 'x':
                calls.append(mode)
                raise FileExistsError('fixture competing exclusive writer')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', racing_open), self.assertRaises(FileExistsError):
            tc.check(self.root)
        self.assertEqual(calls, ['x'])


if __name__ == '__main__':
    unittest.main()
