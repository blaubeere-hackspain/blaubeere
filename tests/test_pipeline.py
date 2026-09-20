import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xray import pipeline
from xray.cli import cmd_clean


class SnapshotTest(unittest.TestCase):
    def test_inventory_tests_run_from_snapshot_without_repository_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            pipeline.snapshot_source(Path(__file__).resolve().parents[1], source)
            result = subprocess.run(
                [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests',
                 '-p', 'test_audit_inventory.py', '-v'],
                cwd=source, env=dict(os.environ, PYTHONPATH=str(source)),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('Ran 4 tests', result.stdout)

    def test_build_snapshot_includes_test_dependencies_without_private_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            included = {
                'xray/__init__.py', 'xray/marts/flujos.py',
                'tests/test_audit_inventory.py', 'scripts/audit_financial_data.py',
                'scripts/run_checks.sh',
                'tests/fixtures/financial_audit/materialized/companies.csv',
                'tests/fixtures/financial_audit/materialized/balances.csv',
            }
            excluded = {
                'scripts/.env', 'scripts/private.pem', 'scripts/__pycache__/cached.py',
                'scripts/.private/credentials.py', 'tests/fixtures/.env',
                'tests/fixtures/__pycache__/cached.csv', 'tests/fixtures/private.key',
                'tests/fixtures/.private/credentials.csv', 'data/private.csv',
            }
            for relative in included | excluded:
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(relative)
            (root / 'scripts/linked.py').symlink_to(root / 'data/private.csv')
            (root / 'tests/fixtures/linked.csv').symlink_to(root / 'data/private.csv')
            (root / 'tests/fixtures/linked_dir').symlink_to(root / 'data', target_is_directory=True)
            with patch.object(pipeline.paths, 'ROOT', root), \
                 patch.object(pipeline, 'TABLES', {}), \
                 patch.object(pipeline.subprocess, 'run', side_effect=RuntimeError('stop before build')), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'stop before build'):
                    pipeline.run_build()
            source, = (root / 'data/runs').glob('*/source')
            copied = {file.relative_to(source).as_posix() for file in source.rglob('*') if file.is_file()}
            for relative in sorted(included):
                with self.subTest(dependency=relative):
                    self.assertIn(relative, copied)
                    self.assertEqual((source / relative).read_bytes(), (root / relative).read_bytes())
            self.assertEqual(copied, included | {'requirements.txt'})


class PublicationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / 'data/runs/test-run'
        self.outputs = ['data/clean/first.txt', 'data/marts/second.txt']
        for relative in self.outputs:
            for folder, value in [(self.root, 'previous'), (self.run, 'replacement')]:
                file = folder / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(value)
        self.manifest = {
            'schema_version': 2,
            'outputs': {relative: pipeline.digest(self.run / relative) for relative in self.outputs},
        }
        (self.run / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_publication_keeps_previous_files_and_commits_pointer(self):
        pipeline.publish_run(self.root, self.run, self.manifest)
        for relative in self.outputs:
            self.assertEqual((self.root / relative).read_text(), 'replacement')
            self.assertEqual((self.run / 'before' / relative).read_text(), 'previous')
        current = json.loads((self.root / 'reports/current.json').read_text())
        self.assertEqual(current['workspace'], 'data/runs/test-run')
        self.assertEqual(current['manifest_sha256'], pipeline.digest(self.run / 'manifest.json'))
        self.assertEqual(json.loads((self.root / 'reports/build_manifest.json').read_text()), self.manifest)

    def test_failed_pointer_publication_restores_portable_manifest(self):
        portable = self.root / 'reports/build_manifest.json'
        portable.parent.mkdir(parents=True, exist_ok=True)
        portable.write_text('previous manifest')
        original = pipeline.atomic_copy

        def copy(source, target):
            if Path(target) == self.root / 'reports/current.json':
                raise OSError('simulated pointer failure')
            return original(source, target)

        with patch.object(pipeline, 'atomic_copy', side_effect=copy):
            with self.assertRaises(OSError):
                pipeline.publish_run(self.root, self.run, self.manifest)
        self.assertEqual(portable.read_text(), 'previous manifest')
        for relative in self.outputs:
            self.assertEqual((self.root / relative).read_text(), 'previous')
        self.assertFalse((self.root / 'reports/current.json').exists())

    def test_failed_publication_restores_previous_files(self):
        original = pipeline.atomic_copy
        calls = 0

        def copy(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated failure')
            return original(source, target)

        with patch.object(pipeline, 'atomic_copy', side_effect=copy):
            with self.assertRaises(OSError):
                pipeline.publish_run(self.root, self.run, self.manifest)
        for relative in self.outputs:
            self.assertEqual((self.root / relative).read_text(), 'previous')
        self.assertFalse((self.root / 'reports/current.json').exists())
        self.assertFalse((self.root / 'reports/.publish.lock').exists())

    def test_modified_artifact_is_not_published(self):
        (self.run / self.outputs[0]).write_text('changed after validation')
        with self.assertRaises(ValueError):
            pipeline.publish_run(self.root, self.run, self.manifest)
        self.assertEqual((self.root / self.outputs[0]).read_text(), 'previous')

    def test_clean_passes_a_connection_and_closes_it(self):
        con = Mock()
        with patch('xray.db.connect', return_value=con), \
             patch('xray.clean.dimensions.clean_dimensions', return_value={}) as dimensions, \
             patch('xray.clean.facts.clean_facts', return_value={}) as facts, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_clean(None), 0)
        dimensions.assert_called_once_with(con)
        facts.assert_called_once_with(con)
        con.close.assert_called_once()
