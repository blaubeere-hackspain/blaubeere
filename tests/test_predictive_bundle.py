"""Portable selected-model parity, integrity, and read-only release checks."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_predictive_bundle import (
    HISTORICAL_MODULES,
    PACKAGE_DIRECTORY,
    ROOT,
    build,
    json_bytes,
    release_payloads,
)
from services.predictive.predictive_model.bundle import (
    FEATURES,
    MAX_FILE_BYTES,
    SOURCE_DIRECTORY,
    SOURCE_MANIFEST_SHA256,
    TARGETS,
    Bundle,
    sha256,
)


class PredictiveBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / 'release'
        self.result = build(self.output)
        self.data = self.output / 'bundle'
        self.bundle = Bundle.load(self.data, expected_sha256=self.result['bundle_sha256'])

    def rewrite(self, filename, mutate):
        """Resign fixtures so semantic checks, rather than stale hashes, reject them."""
        path = self.data / filename
        value = json.loads(path.read_bytes())
        mutate(value)
        path.write_bytes(json_bytes(value))
        manifest_path = self.data / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files'][filename] = sha256(path.read_bytes())
        manifest_path.write_bytes(json_bytes(manifest))
        return sha256(manifest_path.read_bytes())

    def test_known_states_and_original_parity(self):
        try:
            from scripts.financial_v3_dataset_model_metrics import predict
        except ModuleNotFoundError as error:
            if error.name not in {'numpy', 'sklearn', 'threadpoolctl'}:
                raise
            predict = None
        cases = [(79, 100, 'low'), (80, 100, 'low'), (80.001, 100, 'middle'),
                 (119.999, 100, 'middle'), (120, 100, 'high'), (121, 100, 'high'),
                 (None, 100, 'unknown'), (100, None, 'unknown'), (100, 0, 'unknown'),
                 (100, -1, 'unknown'), (None, None, 'unknown')]
        for target in TARGETS:
            model = self.bundle.models[target]
            for current, mean, state in cases:
                values = dict.fromkeys(FEATURES)
                values.update(receipts_known=current, receipts_known_mean3=mean)
                actual = self.bundle.predict(target, values)
                expected = None if model is None else model['states'].get(state, model['prevalence'])
                self.assertEqual(actual, expected)
                if predict is not None:
                    self.assertEqual(actual, predict(model, {'values': values}))
                if actual is not None:
                    actual['0'] = 999
                    again = self.bundle.predict(target, values)
                    assert again is not None
                    self.assertNotEqual(again['0'], 999)
        values = dict.fromkeys(FEATURES)
        values.update(receipts_known=80, receipts_known_mean3=100)
        self.assertEqual(self.bundle.predict(TARGETS[0], values),
                         {'0': 0.42191358024691356, '1': 0.5780864197530865})
        self.assertEqual(self.bundle.predict(TARGETS[1], values),
                         {'0': 0.685082304526749, '1': 0.31491769547325105})

    def test_archived_available_predictions_match(self):
        """Replay published features only; never evaluate labels or excluded groups."""
        original = ROOT / SOURCE_DIRECTORY
        index = json.loads((original / 'index.json').read_bytes())
        matched = 0
        for entry in index['companies']:
            payload = (original / entry['path']).read_bytes()
            self.assertEqual(sha256(payload), entry['sha256'])
            profile = json.loads(payload)
            for point in profile['points']:
                for target, forecast in point['forecast'].items():
                    expected = forecast['probabilities']
                    if expected is None:
                        continue
                    with self.subTest(company=entry['company_id'], as_of=point['as_of'], target=target):
                        self.assertNotIn(profile['company_id'], self.bundle.excluded_company_ids)
                        self.assertNotIn(profile['group_id'], self.bundle.excluded_group_ids)
                        actual = self.bundle.predict(target, point['features'])
                        self.assertEqual(actual, expected)
                        matched += 1
        self.assertGreater(matched, 0)

    def test_metadata_exclusions_and_exact_sources(self):
        original = ROOT / SOURCE_DIRECTORY
        self.assertEqual(sha256((original / 'manifest.json').read_bytes()), SOURCE_MANIFEST_SHA256)
        self.assertEqual((self.data / 'models.json').read_bytes(), (original / 'models.json').read_bytes())
        excluded = json.loads((original / 'index.json').read_bytes())['excluded_product_only']
        self.assertEqual(json.loads((self.data / 'exclusions.json').read_bytes())['excluded_product_only'], excluded)
        self.assertEqual(self.bundle.excluded_company_ids, {row['company_id'] for row in excluded})
        self.assertEqual(self.bundle.excluded_group_ids, {row['group_id'] for row in excluded})
        self.assertEqual(len(self.bundle.excluded_company_ids), 208)
        self.assertEqual(self.bundle.model_version, 'predictive-model-1')
        self.assertEqual(self.bundle.feature_version, 'predictive-features-1')
        self.assertEqual(self.bundle.feature_names, FEATURES)
        self.assertEqual(self.bundle.metadata['python_version'], '3.13.15')
        self.assertEqual(self.bundle.sha256, self.result['bundle_sha256'])
        release = json.loads((self.output / 'release-manifest.json').read_bytes())
        self.assertEqual(release['bundle_sha256'], self.bundle.sha256)
        self.assertEqual(release['python_version'], '3.13.15')
        self.assertTrue(all(not Path(name).is_absolute() for name in release['files'] | release['sources']))
        seal = json.loads((original / 'seal.json').read_bytes())
        self.assertEqual(self.bundle.metadata['provenance']['seal_sha256'],
                         sha256((original / 'seal.json').read_bytes()))
        for name in HISTORICAL_MODULES:
            self.assertEqual((self.output / name).read_bytes(), (ROOT / name).read_bytes())
            self.assertEqual(sha256((self.output / name).read_bytes()), seal['bindings_sha256'][name])
        self.assertEqual((self.output / 'scripts/__init__.py').read_bytes(), b'')
        for path in (self.output / 'predictive_model').rglob('*'):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (ROOT / PACKAGE_DIRECTORY / path.relative_to(self.output / 'predictive_model')).read_bytes())

    def test_reproducible_and_read_only_check(self):
        files, expected = release_payloads()
        manifest = json.loads(files['release-manifest.json'])
        paths = [ROOT / name for name in manifest['sources']]
        paths += [self.output / name for name in files]
        before = {str(path): (sha256(path.read_bytes()), path.stat().st_mtime_ns) for path in paths}
        second = self.directory / 'second'
        self.assertEqual(build(second)['bundle_sha256'], expected)
        for name, payload in files.items():
            self.assertEqual((second / name).read_bytes(), payload)
        self.assertTrue(build(self.output, check=True)['checked'])
        self.assertEqual(build(self.output)['bundle_sha256'], expected)
        self.assertEqual(before, {str(path): (sha256(path.read_bytes()), path.stat().st_mtime_ns) for path in paths})
        missing = self.directory / 'absent'
        with self.assertRaises(ValueError):
            build(missing, check=True)
        self.assertFalse(missing.exists())

    def test_invalid_expected_hash_and_corruption(self):
        for digest in ('', 'f' * 63, 'G' * 64, 'A' * 64, None, '0' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                Bundle.load(self.data, expected_sha256=digest)  # type: ignore[arg-type]
        for name in ('models.json', 'policy.json', 'features.json', 'exclusions.json', 'manifest.json'):
            path = self.data / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(name=name), self.assertRaises(ValueError):
                Bundle.load(self.data, expected_sha256=self.bundle.sha256)
            path.write_bytes(original)
        with self.assertRaises(TypeError):
            Bundle.load(self.data)  # type: ignore[call-arg]

    def test_incompatible_models_features_and_policy(self):
        pristine = {path.name: path.read_bytes() for path in self.data.iterdir()}
        mutations = [
            ('features.json', lambda value: value['names'].reverse()),
            ('features.json', lambda value: value.update(feature_version='other')),
            ('models.json', lambda value: value[TARGETS[0]].update(candidate='constant')),
            ('models.json', lambda value: value[TARGETS[0]].update(candidate='logistic_C1')),
            ('models.json', lambda value: value[TARGETS[0]].update(candidate='unregistered')),
            ('models.json', lambda value: value[TARGETS[0]].update(classes=[False, True])),
            ('models.json', lambda value: value[TARGETS[0]].update(classes=['0', '1'])),
            ('models.json', lambda value: value[TARGETS[0]].update(classes=[1, 0])),
            ('models.json', lambda value: value[TARGETS[0]].update(prevalence={'0': .2, '1': .2})),
            ('models.json', lambda value: value[TARGETS[0]].update(prevalence={'0': True, '1': 0})),
            ('models.json', lambda value: value[TARGETS[0]].update(prevalence={'0': -1, '1': 2})),
            ('models.json', lambda value: value[TARGETS[0]].update(prevalence={'0': '0.5', '1': .5})),
            ('models.json', lambda value: value[TARGETS[0]].update(prevalence={'0': .5})),
            ('models.json', lambda value: value[TARGETS[0]].update(states={'invalid': {'0': .5, '1': .5}})),
            ('models.json', lambda value: value[TARGETS[0]].update(states=[])),
            ('models.json', lambda value: value[TARGETS[0]].update(target=TARGETS[1])),
            ('models.json', lambda value: value[TARGETS[0]].update(feature_order=[])),
            ('models.json', lambda value: value.update(extra=None)),
            ('models.json', lambda value: value.update({TARGETS[2]: copy.deepcopy(value[TARGETS[0]])})),
            ('models.json', lambda value: value.update({TARGETS[0]: []})),
            ('policy.json', lambda value: value.update(unknown='incompatible')),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name, mutation=mutate):
                digest = self.rewrite(name, mutate)
                with self.assertRaises(ValueError):
                    Bundle.load(self.data, expected_sha256=digest)
                for filename, payload in pristine.items():
                    (self.data / filename).write_bytes(payload)
        for key in ('model_version', 'feature_version', 'schema_version', 'policy_version', 'python_version'):
            metadata = json.loads(pristine['manifest.json'])
            metadata[key] = 'incompatible'
            payload = json_bytes(metadata)
            (self.data / 'manifest.json').write_bytes(payload)
            with self.subTest(key=key), self.assertRaises(ValueError):
                Bundle.load(self.data, expected_sha256=sha256(payload))

    def test_missing_models_abstain_and_invalid_values_reject(self):
        digest = self.rewrite('models.json', lambda value: value.update(dict.fromkeys(TARGETS)))
        missing = Bundle.load(self.data, expected_sha256=digest)
        for target in TARGETS:
            self.assertIsNone(missing.predict(target, {}))
        for value in (True, '100', float('inf'), float('nan'), [], 10 ** 400):
            values = dict.fromkeys(FEATURES)
            values['receipts_known'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.bundle.predict(TARGETS[0], values)
        for values in ({}, {**dict.fromkeys(FEATURES), 'extra': 1}):
            with self.assertRaises(ValueError):
                self.bundle.predict(TARGETS[0], values)
        with self.assertRaises(ValueError):
            self.bundle.predict('unknown', dict.fromkeys(FEATURES))

    def test_exact_files_size_and_symlinks(self):
        for name in ('unexpected.json', 'extra-directory'):
            path = self.data / name
            path.write_bytes(b'{}') if '.' in name else path.mkdir()
            with self.assertRaises(ValueError):
                Bundle.load(self.data, expected_sha256=self.bundle.sha256)
            path.unlink() if path.is_file() else path.rmdir()
        model = self.data / 'models.json'
        original = model.read_bytes()
        model.write_bytes(b' ' * (MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            Bundle.load(self.data, expected_sha256=self.bundle.sha256)
        outside = self.directory / 'outside.json'
        outside.write_bytes(original)
        model.unlink()
        model.symlink_to(outside)
        with self.assertRaises(ValueError):
            Bundle.load(self.data, expected_sha256=self.bundle.sha256)
        link = self.directory / 'link'
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ValueError):
            Bundle.load(link / 'bundle', expected_sha256=self.bundle.sha256)
        with self.assertRaises(ValueError):
            build(link, check=True)

    def test_reject_duplicate_and_nonfinite_json(self):
        path = self.data / 'models.json'
        for payload in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            path.write_bytes(payload)
            manifest_path = self.data / 'manifest.json'
            manifest = json.loads(manifest_path.read_bytes())
            manifest['files']['models.json'] = sha256(payload)
            manifest_path.write_bytes(json_bytes(manifest))
            with self.assertRaises(ValueError):
                Bundle.load(self.data, expected_sha256=sha256(manifest_path.read_bytes()))

    def test_release_binds_all_code_and_refuses_existing_different(self):
        for name in ('predictive_model/bundle.py', HISTORICAL_MODULES[0], 'release-manifest.json'):
            path = self.output / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(name=name):
                for check in (False, True):
                    with self.assertRaises(ValueError):
                        build(self.output, check=check)
                self.assertEqual(path.read_bytes(), original + b'\n')
            path.write_bytes(original)
        (self.output / 'extra.py').write_bytes(b'')
        with self.assertRaises(ValueError):
            build(self.output, check=True)

    def test_source_hash_changes_and_pinned_original_reject(self):
        files, _ = release_payloads()
        root = self.directory / 'source'
        for relative in json.loads(files['release-manifest.json'])['sources']:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        release = self.directory / 'source-release'
        build(release, root=root)
        for name in (f'{SOURCE_DIRECTORY}/manifest.json', f'{SOURCE_DIRECTORY}/models.json',
                     f'{SOURCE_DIRECTORY}/index.json', f'{SOURCE_DIRECTORY}/seal.json',
                     *HISTORICAL_MODULES, f'{PACKAGE_DIRECTORY}/bundle.py', 'scripts/build_predictive_bundle.py'):
            path = root / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    build(release, check=True, root=root)
                if name.startswith(SOURCE_DIRECTORY) or name in HISTORICAL_MODULES:
                    fresh = self.directory / 'must-not-be-created'
                    with self.assertRaises(ValueError):
                        build(fresh, root=root)
                    self.assertFalse(fresh.exists())
            path.write_bytes(original)

    def test_isolated_portable_execution_and_cli_check(self):
        program = '''
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from predictive_model.bundle import Bundle, FEATURES
from scripts.financial_v3_engine import FinancialInputs
bundle = Bundle.load(sys.argv[1], expected_sha256=sys.argv[2])
values = dict.fromkeys(FEATURES)
values.update(receipts_known=120, receipts_known_mean3=100)
assert bundle.predict('receipt_contraction_3m', values)['1'] == 0.512890625
assert bundle.predict('current_receipt_dip_3m', values) is None
assert not {'numpy', 'sklearn'} & set(sys.modules)
print(bundle.sha256)
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-c', program,
                                 str(self.output), self.bundle.sha256], cwd=self.directory,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), self.bundle.sha256)
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/build_predictive_bundle.py'),
                                 '--output', str(self.output), '--check'], cwd=self.directory,
                                capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)['checked'])
        self.assertEqual(before, {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.output.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
