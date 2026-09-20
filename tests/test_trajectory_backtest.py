import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import trajectory_backtest as tb
from scripts import trajectory_contract as tc
from tests import test_trajectory_contract as contract_fixtures
from tests.test_trajectory_events import FIRST
from scripts.model_features import last_day, shift_month


PROTECTED_HASHES = {}


def setUpModule():
    PROTECTED_HASHES.update({name: tc.digest(tc.ROOT / name) for name in tb.PROTECTED_FILES})


def tearDownModule():
    if PROTECTED_HASHES != {name: tc.digest(tc.ROOT / name) for name in tb.PROTECTED_FILES}:
        raise AssertionError('Protected real v1 model/receipt/prediction byte hashes changed')
    print('\nTOY ONLY runner provenance ' + json.dumps({'protected_v1_artifacts_unchanged': len(PROTECTED_HASHES),
          'real_financial_values_queried': 0, 'protocol_sha256': tc.digest(tc.ROOT / tc.PROTOCOL)}, sort_keys=True))


class TrajectoryBacktestTests(unittest.TestCase):
    def setUp(self):
        fixture = contract_fixtures.TrajectoryContractTests('test_fixture_hashes_groups_no_outcomes_and_receipt_preserved')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.root = fixture, fixture.root
        for name in tb.IMPLEMENTATION_FILES:
            source = tc.ROOT / name
            fixture.put(name, source.read_bytes())
        for name in tb.PROTECTED_FILES:
            if not (self.root / name).exists():
                fixture.put(name, b'opaque protected fixture')
        tc.check(self.root)
        self.addCleanup(patch.stopall)
        patch.object(tb, 'PROTOCOL_SHA256', tc.digest(self.root / tc.PROTOCOL)).start()
        self.protected = {name: tc.digest(self.root / name) for name in tb.PROTECTED_FILES}

    def tearDown(self):
        self.assertEqual(self.protected, {name: tc.digest(self.root / name) for name in tb.PROTECTED_FILES})

    def freeze(self):
        return tb.freeze(self.root)

    def test_freeze_metadata_only_exclusive_and_bound_outputs(self):
        with patch.object(tb, 'readonly_connection', side_effect=AssertionError('no DB at freeze')):
            result = self.freeze()
        self.assertEqual(result['outcome_values_read'], 0)
        manifest = tc.read_json(self.root / tc.DIRECTORY / 'execution-manifest.json')
        self.assertEqual(len(manifest['included_groups']), 200)
        self.assertEqual(len(manifest['excluded_groups']), 50)
        self.assertIn('scripts/trajectory_events.py', manifest['implementation_sha256'])
        self.assertIn('tests/test_trajectory_backtest.py', manifest['implementation_sha256'])
        self.assertEqual(manifest['protected_sha256'], self.protected)
        before = (self.root / tc.DIRECTORY / 'execution-manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertEqual(before, (self.root / tc.DIRECTORY / 'execution-manifest.json').read_bytes())

    def test_tampered_code_metadata_or_sidecar_blocks_before_connection(self):
        self.freeze()
        for name in ('scripts/trajectory_events.py', tc.GROUPS,
                     f'{tc.DIRECTORY}/execution-manifest.json.sha256'):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(name=name), patch.object(tb, 'readonly_connection', side_effect=AssertionError('no DB')):
                with self.assertRaises(ValueError):
                    tb.run_phase(self.root, 'development')
            self.assertFalse((self.root / tc.DIRECTORY / 'development-access.json').exists())
            path.write_bytes(original)

    def test_missing_protocol_receipt_never_recreated_by_runner(self):
        (self.root / tc.RECEIPT).unlink()
        with self.assertRaisesRegex(ValueError, 'receipt'):
            self.freeze()
        self.assertFalse((self.root / tc.RECEIPT).exists())

    def test_receipt_fsync_precedes_first_reserved_connection_failure_consumes(self):
        self.freeze()
        self.complete_development_fixture()
        operations = []
        original_fsync = tb.os.fsync

        def syncing(fd):
            operations.append('fsync')
            return original_fsync(fd)

        @contextlib.contextmanager
        def failed_connection():
            receipt = self.root / tc.DIRECTORY / 'reserved-access.json'
            self.assertTrue(receipt.exists())
            self.assertIn('fsync', operations)
            self.assertEqual(tc.read_json(receipt)['phase'], 'reserved')
            operations.append('connect')
            raise RuntimeError('fixture failed first DB connection')
            yield

        with patch.object(tb.os, 'fsync', syncing), patch.object(tb, 'readonly_connection', failed_connection):
            with self.assertRaisesRegex(RuntimeError, 'fixture failed'):
                tb.run_phase(self.root, 'reserved')
        self.assertGreater(operations.index('connect'), operations.index('fsync'))
        with patch.object(tb, 'readonly_connection', side_effect=AssertionError('must not replay')):
            with self.assertRaises(FileExistsError):
                tb.run_phase(self.root, 'reserved')
            verification = tb.verify(self.root)
        self.assertEqual(verification['phases']['reserved'], 'consumed_incomplete_inspect_do_not_replay')

    def complete_development_fixture(self):
        with patch.object(tb, 'load_panel', return_value=([], {'A': 'GROUP_0001'})):
            tb.run_phase(self.root, 'development')

    def test_develop_artifacts_exclusive_verify_only_and_no_probability(self):
        self.freeze()
        self.complete_development_fixture()
        with patch.object(tb, 'readonly_connection', side_effect=AssertionError('verification must not query')):
            self.assertEqual(tb.verify(self.root)['phases']['development'], 'complete')
            with self.assertRaises(FileExistsError):
                tb.run_phase(self.root, 'development')
        directory = self.root / tc.DIRECTORY
        report = tc.read_json(directory / 'development-report.json')
        self.assertEqual(report['coverage']['origin_opportunities'], 7)
        self.assertEqual(report['coverage']['input_eligible_origins'], 0)
        self.assertEqual(report['bootstrap']['replicates'], 1000)
        cases = tc.read_json(directory / 'development-cases.json')
        self.assertTrue(all(case['status'] == 'unavailable' for case in cases.values()))
        self.assertTrue((directory / 'development-case-history.parquet').exists())
        artifact = directory / 'development-events.json'
        artifact.write_bytes(artifact.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            tb.verify(self.root)

    def test_reserve_requires_completed_development_and_no_include_override(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError, 'development'):
            tb.run_phase(self.root, 'reserved')
        with self.assertRaises(ValueError):
            tb.run_phase(self.root, 'arbitrary')
        parser = tb.argument_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['develop', '--include-groups', 'GROUP_0201'])

    def test_freeze_after_any_phase_record_is_forbidden(self):
        tb.write_json(tb.output_path(self.root, 'reserved-access.json'), {'consumed': True})
        with self.assertRaisesRegex(ValueError, 'prior phase'):
            self.freeze()
        self.assertFalse(tb.output_path(self.root, tb.MANIFEST).exists())

    def test_loader_rejects_in_memory_cutoff_override_before_connection(self):
        self.freeze()
        manifest = tb.verify_execution(self.root)
        altered = copy.deepcopy(self.fixture.contract)
        altered['evaluation']['development']['outcomes_through'] = '2026-08-31'
        tb.write_json(tb.output_path(self.root, 'development-access.json'), tb.access_record(manifest, altered, 'development'))
        with patch.object(tb, 'readonly_connection', side_effect=AssertionError('must reject before DB')):
            with self.assertRaisesRegex(ValueError, 'Pinned protocol'):
                tb.load_panel(self.root, altered, 'development')

    def test_output_exclusive_parquet_roundtrip_and_compression(self):
        import duckdb
        target = tb.output_path(self.root, 'fixture-evidence.parquet')
        row = {'company_id': 'A', 'group_id': 'GROUP_0001', 'month': '2025-03-01', 'evidence': {'lower': -1.5}}
        tb.write_parquet(target, [row])
        original = target.read_bytes()
        with self.assertRaises(FileExistsError):
            tb.write_parquet(target, [])
        self.assertEqual(original, target.read_bytes())
        with tb.readonly_connection() as connection:
            result = connection.execute('SELECT company_id, group_id, month, row_json FROM read_parquet(?)', [str(target)]).fetchone()
            self.assertEqual(json.loads(result[-1]), row)
            compression = connection.execute('SELECT DISTINCT compression FROM parquet_metadata(?)', [str(target)]).fetchall()
            self.assertEqual(compression, [('ZSTD',)])
            with self.assertRaises(duckdb.InvalidInputException):
                connection.execute('CREATE TABLE forbidden (i INTEGER)')
        self.assertEqual(tb.verified_hash(target), tc.digest(target))

    def test_sql_projection_and_filters_precede_fetch_from_pinned_run(self):
        self.freeze()
        calls = []
        expected_columns = sorted(tb.INPUT_COLUMNS)

        class Connection:
            description = []

            def execute(con, sql, parameters):
                calls.append(('execute', sql, parameters))
                self.assertIn('group_id IN (', sql)
                self.assertEqual(parameters[0], str(self.root / self.fixture.run_name / tc.PANEL))
                self.assertTrue(all(f'GROUP_{i:04d}' in parameters for i in range(1, 201)))
                self.assertFalse(any(f'GROUP_{i:04d}' in parameters for i in range(201, 251)))
                self.assertNotIn('SELECT *', sql)
                self.assertNotIn('target', sql.lower())
                if 'month = ?' in sql:
                    con.description = [('company_id',), ('group_id',)]
                    con.result = [('A', 'GROUP_0001')]
                    self.assertEqual(parameters[-1], '2024-09-01')
                elif sql.startswith('SELECT company_id, group_id, month'):
                    self.assertIn('month <= ?', sql)
                    self.assertEqual(parameters[-1], '2025-12-31')
                    con.description = [('company_id',), ('group_id',), ('month',)]
                    con.result = []
                else:
                    self.assertIn('month <= ?', sql)
                    self.assertEqual(parameters[-1], '2025-12-31')
                    projection = sql.split(' FROM ')[0].removeprefix('SELECT ')
                    self.assertEqual(projection, ', '.join(f'"{c}"' for c in expected_columns))
                    con.description = [(c,) for c in expected_columns]
                    con.result = []
                return con

            def fetchall(con):
                calls.append(('fetch',))
                return con.result

        @contextlib.contextmanager
        def connection():
            self.assertTrue((self.root / tc.DIRECTORY / 'development-access.json').exists())
            yield Connection()

        with patch.object(tb, 'readonly_connection', connection), patch.object(tb, 'write_parquet'):
            with self.assertRaises(FileNotFoundError):
                tb.run_phase(self.root, 'development')
        self.assertEqual([c[0] for c in calls], ['execute', 'fetch', 'execute', 'fetch', 'execute', 'fetch'])

    def test_duplicate_phase_identity_rejected_before_financial_projection(self):
        self.freeze()
        executions = []

        class Connection:
            def execute(con, sql, parameters):
                executions.append(sql)
                if 'month = ?' in sql:
                    con.description = [('company_id',), ('group_id',)]
                    con.result = [('A', 'GROUP_0001')]
                else:
                    self.assertTrue(sql.startswith('SELECT company_id, group_id, month'))
                    con.description = [('company_id',), ('group_id',), ('month',)]
                    con.result = [('A', 'GROUP_0001', FIRST)] * 2
                return con

            def fetchall(con):
                return con.result

        @contextlib.contextmanager
        def connection():
            yield Connection()

        with patch.object(tb, 'readonly_connection', connection), self.assertRaisesRegex(ValueError, 'phase identity'):
            tb.run_phase(self.root, 'development')
        self.assertEqual(len(executions), 2)

    def test_fixture_parquet_loader_closed_cutoff_and_excluded_group(self):
        import duckdb
        panel = self.root / self.fixture.run_name / tc.PANEL
        panel.unlink()
        with duckdb.connect() as con:
            columns = ', '.join(f'"{c}" ' + ('VARCHAR' if c in ('company_id', 'group_id') else 'DATE' if c in ('month', 'available_at')
                                           else 'BOOLEAN' if c == 'tiene_actividad_caja' else 'DOUBLE') for c in sorted(tb.INPUT_COLUMNS))
            con.execute(f'CREATE TABLE fixture ({columns})')
            for group, company in [('GROUP_0001', 'A'), ('GROUP_0201', 'EXCLUDED')]:
                for i in range(24):
                    month = shift_month(FIRST, i)
                    row = dict.fromkeys(tb.INPUT_COLUMNS, 1.0)
                    row.update(company_id=company, group_id=group, month=month, available_at=None if i == 4 else last_day(month),
                               tiene_actividad_caja=True, n_sin_eur=0)
                    con.execute(f'INSERT INTO fixture VALUES ({", ".join("?" for _ in row)})', [row[c] for c in sorted(row)])
            con.execute('COPY fixture TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(panel)])
        protocol = self.fixture.contract
        proof = {'input_sha256': {f'{self.fixture.run_name}/{tc.PANEL}': tc.digest(panel)},
                 'manifest_sha256': 'fixture', 'protocol_sha256': 'fixture', 'implementation_sha256': {}}
        tb.write_json(tb.output_path(self.root, 'development-access.json'), tb.access_record(proof, protocol, 'development'))
        with patch.object(tb, 'verify_execution', return_value=proof):
            rows, universe = tb.load_panel(self.root, protocol, 'development')
        self.assertEqual(universe, {'A': 'GROUP_0001'})
        self.assertEqual(len(rows), 16)
        self.assertEqual(max(row['month'].isoformat() for row in rows), '2025-12-01')
        self.assertTrue(all(set(row) == tb.INPUT_COLUMNS for row in rows))


if __name__ == '__main__':
    unittest.main()
