import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import financial_v3_dataset_delivery_amendment as m


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v3-amendment-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / m.CACHE_PATH
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text('{"version":"5.9.3","fileNames":[],"fileInfos":[]}')

    def put(self, path, value):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
        return hashlib.sha256(value).hexdigest()

    def bindings(self):
        result = {m.CACHE_PATH: m.CACHE_EXPECTED}
        for path in ('data/input.bin', 'models/seal.json', 'scripts/science.py', 'tests/frozen_test.py', 'profiles/company.json'):
            result[path] = self.put(path, path.encode())
        original = b'historical app bytes'
        result['services/api/src/finance.rs'] = hashlib.sha256(original).hexdigest()
        self.put('services/api/src/finance.rs', b'authorized delivered app')
        before_path = m.DELIVERY_REL + '/source-before/services/api/src/finance.rs'
        self.put(before_path, original)
        return result, {'services/api/src/finance.rs': {'sha256': result['services/api/src/finance.rs'], 'historic_seal_bound': True}}

    def test_exact_decision_and_single_exception(self):
        value = m.validate_decision(m.canonical(m.DECISION))
        self.assertEqual(len(value['exceptions']), 1)
        self.assertEqual(value['exceptions'][0]['path'], m.CACHE_PATH)
        self.assertFalse(value['exceptions'][0]['historical_bytes_preserved'])
        self.assertEqual(m.sha_bytes(m.canonical(value)), m.DECISION_PIN)

    def test_unknown_fields_and_duplicate_keys_rejected(self):
        bad = copy.deepcopy(m.DECISION)
        bad['extra'] = True
        with self.assertRaises(ValueError):
            m.validate_decision(m.canonical(bad))
        duplicate = m.canonical(m.DECISION).decode().replace('{', '{"version":"tampered",', 1).encode()
        with self.assertRaises(ValueError):
            m.validate_decision(duplicate)

    def test_every_exception_field_is_pinned(self):
        for key, value in [('path', 'data/input.bin'), ('kind', 'scientific'), ('historical_expected_sha256', '0' * 64), ('historical_bytes_preserved', True), ('historical_bytes_preserved', 0)]:
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(m.DECISION)
                bad['exceptions'][0][key] = value
                with self.assertRaises(ValueError):
                    m.validate_decision(m.canonical(bad))

    def test_exception_expansion_wildcard_and_traversal_rejected(self):
        for path in ('apps/app/*', '../apps/app/tsconfig.tsbuildinfo', '/tmp/cache', 'https://example/cache', 'apps\\app\\cache'):
            bad = copy.deepcopy(m.DECISION)
            bad['exceptions'][0]['path'] = path
            with self.assertRaises(ValueError):
                m.validate_decision(m.canonical(bad))
        bad = copy.deepcopy(m.DECISION)
        bad['exceptions'].append(copy.deepcopy(bad['exceptions'][0]))
        with self.assertRaises(ValueError):
            m.validate_decision(m.canonical(bad))

    def test_decision_whitespace_or_hash_tamper_rejected(self):
        for content in (m.canonical(m.DECISION) + b' ', b'{}', m.canonical(m.DECISION).replace(b'generated', b'GENerated')):
            with self.assertRaises(ValueError):
                m.validate_decision(content)

    def test_regenerated_cache_allowed_but_recorded_not_preserved(self):
        binding, before = self.bindings()
        initial = m.check_historical(self.root, binding, before)
        self.cache.write_text('{"version":"5.9.3","fileNames":["new"],"fileInfos":[]}')
        next_check = m.check_historical(self.root, binding, before)
        self.assertEqual(initial['historical_exact_bindings'], len(binding) - 1)
        self.assertEqual(next_check['exception_count'], 1)
        self.assertNotEqual(initial['derived_cache']['sha256'], next_check['derived_cache']['sha256'])
        self.assertFalse(next_check['historical_whole_worktree_integrity'])
        self.assertFalse(next_check['derived_cache']['historical_bytes_preserved'])
        self.assertEqual(next_check['derived_cache']['version'], '5.9.3')
        self.assertIn('observed_at', next_check['derived_cache'])

    def test_every_other_historical_asset_remains_exact(self):
        binding, before = self.bindings()
        for path in binding:
            if path in (m.CACHE_PATH, 'services/api/src/finance.rs'):
                continue
            with self.subTest(path=path):
                file = self.root / path
                original = file.read_bytes()
                file.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    m.check_historical(self.root, binding, before)
                file.write_bytes(original)

    def test_captured_app_before_cannot_be_replaced_with_current(self):
        binding, before = self.bindings()
        self.put(m.DELIVERY_REL + '/source-before/services/api/src/finance.rs', b'authorized delivered app')
        with self.assertRaises(ValueError):
            m.check_historical(self.root, binding, before)

    def test_cache_historical_expected_binding_cannot_change(self):
        binding, before = self.bindings()
        binding[m.CACHE_PATH] = m.sha_file(self.cache)
        with self.assertRaises(ValueError):
            m.check_historical(self.root, binding, before)

    def test_unapproved_before_snapshot_redirection_rejected(self):
        binding, before = self.bindings()
        before['data/input.bin'] = {'sha256': binding['data/input.bin'], 'historic_seal_bound': True}
        with self.assertRaises(ValueError):
            m.check_historical(self.root, binding, before)

    def test_cache_symlink_and_directory_rejected(self):
        self.cache.rename(self.cache.with_name('saved'))
        self.cache.symlink_to(self.cache.with_name('saved'))
        with self.assertRaises(ValueError):
            m.cache_record(self.root)
        self.cache.unlink()
        self.cache.mkdir()
        with self.assertRaises(ValueError):
            m.cache_record(self.root)

    def test_cache_parent_symlink_rejected(self):
        parent = self.root / 'apps/app'
        parent.rename(self.root / 'saved-app')
        parent.symlink_to(self.root / 'saved-app', target_is_directory=True)
        with self.assertRaises(ValueError):
            m.cache_record(self.root)

    def test_missing_or_malformed_cache_rejected(self):
        for value in (b'null', b'{}', b'{"version":NaN}', b'{"version":5}', b'invalid'):
            self.cache.write_bytes(value)
            with self.assertRaises(ValueError):
                m.cache_record(self.root)
        self.cache.unlink()
        with self.assertRaises(ValueError):
            m.cache_record(self.root)

    def test_binding_path_traversal_and_external_reference_rejected(self):
        for path in ('../secret', '/tmp/secret', 'https://example/a', 'a/../b', 'a\\b', './a'):
            with self.assertRaises(ValueError):
                m.safe_file(self.root, path)

    def test_other_asset_symlink_rejected_even_if_bytes_equal(self):
        expected = self.put('science.bin', b'original')
        (self.root / 'alias').symlink_to(self.root / 'science.bin')
        with self.assertRaises(ValueError):
            m.check_map(self.root, {'alias': expected})

    def test_old_receipt_and_current_source_bindings_are_immutable(self):
        receipt = self.put('old-receipt.json', b'{"sealed":true}')
        source = self.put('app.tsx', b'current app')
        m.check_map(self.root, {'old-receipt.json': receipt, 'app.tsx': source})
        for path in ('old-receipt.json', 'app.tsx'):
            original = (self.root / path).read_bytes()
            (self.root / path).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                m.check_map(self.root, {'old-receipt.json': receipt, 'app.tsx': source})
            (self.root / path).write_bytes(original)

    def test_artifact_inventory_additions_and_removals_fail(self):
        self.put('bundle/a.json', b'original')
        m.check_inventory(self.root, 'bundle', {'a.json'})
        self.put('bundle/extra.json', b'new')
        with self.assertRaises(ValueError):
            m.check_inventory(self.root, 'bundle', {'a.json'})
        with self.assertRaises(ValueError):
            m.check_inventory(self.root, 'bundle', {'a.json', 'extra.json', 'missing.json'})

    def test_inventory_symlink_and_unapproved_exclusion_rejected(self):
        self.put('bundle/a.json', b'original')
        (self.root / 'bundle/link').symlink_to(self.root / 'bundle/a.json')
        with self.assertRaises(ValueError):
            m.check_inventory(self.root, 'bundle', {'a.json', 'link'})
        with self.assertRaises(ValueError):
            m.check_inventory(self.root, 'bundle', {'a.json'}, excluded=('a.json',))

    def test_source_receipt_exact_fixed_list_and_pin(self):
        receipt = {'version': m.VERSION, 'decision_sha256': m.DECISION_PIN,
            'source_sha256': {p: 'a' * 64 for p in m.SOURCE_PATHS}, 'preservation_sha256': 'b' * 64,
            'support_sha256': {p: 'c' * 64 for p in m.SUPPORT_PATHS}, 'historical_whole_worktree_integrity': False}
        m.validate_source_receipt(receipt)
        for key in ('source_sha256', 'support_sha256'):
            bad = copy.deepcopy(receipt)
            bad[key]['extra'] = 'f' * 64
            with self.assertRaises(ValueError):
                m.validate_source_receipt(bad)
        bad = copy.deepcopy(receipt)
        bad['historical_whole_worktree_integrity'] = True
        with self.assertRaises(ValueError):
            m.validate_source_receipt(bad)
        bad = copy.deepcopy(receipt)
        del bad['source_sha256'][m.SOURCE_PATHS[0]]
        with self.assertRaises(ValueError):
            m.validate_source_receipt(bad)

    def test_source_receipt_hash_tamper_and_extra_fields_rejected(self):
        good = {'version': m.VERSION, 'decision_sha256': m.DECISION_PIN,
            'source_sha256': {p: 'a' * 64 for p in m.SOURCE_PATHS}, 'preservation_sha256': 'b' * 64,
            'support_sha256': {p: 'c' * 64 for p in m.SUPPORT_PATHS}, 'historical_whole_worktree_integrity': False}
        for key, value in [('decision_sha256', '0' * 64), ('preservation_sha256', 'bad'), ('extra', True)]:
            bad = copy.deepcopy(good)
            bad[key] = value
            with self.assertRaises(ValueError):
                m.validate_source_receipt(bad)


if __name__ == '__main__':
    unittest.main()
