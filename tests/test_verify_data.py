import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from scripts import verify_data
from xray import paths
from xray.schema import TABLES


class VerifyDataTest(unittest.TestCase):
    def ingest_fixture(self, root):
        raw, interim = root / 'raw', root / 'interim'
        raw.mkdir()
        interim.mkdir()
        with duckdb.connect() as con:
            for name, spec in TABLES.items():
                (raw / spec.filename).write_bytes(b'id,note\r\n1,"private\r\nmultiline"\r\n2,"other\nnote"\r\n')
                con.execute('COPY (SELECT * FROM range(2)) TO ? (FORMAT PARQUET)',
                            [str(interim / f'{name}.parquet')])
        return raw, interim

    def run_ingest(self, raw, interim):
        output = io.StringIO()
        with patch.object(paths, 'RAW_DIR', raw), patch.object(paths, 'INTERIM_DIR', interim), \
             contextlib.redirect_stdout(output):
            code = verify_data.main(['ingest'])
        self.assertNotIn('private', output.getvalue())
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_multiline_csv_records_match_all_eight_parquets(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw, interim = self.ingest_fixture(Path(temporary))
            code, records = self.run_ingest(raw, interim)
            self.assertEqual(code, 0)
            self.assertEqual(len(records), 8)
            self.assertEqual({row['table'] for row in records}, set(TABLES))
            self.assertTrue(all(row['csv_rows'] == row['parquet_rows'] == 2 for row in records))
            self.assertTrue(all(row['status'] == 'OK' for row in records))

    def test_mismatch_is_not_masked_by_later_successes(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw, interim = self.ingest_fixture(Path(temporary))
            (raw / 'groups.csv').write_text('id,note\n1,private\n')
            code, records = self.run_ingest(raw, interim)
            self.assertEqual(code, 1)
            self.assertEqual(records[0]['status'], 'MISMATCH')
            self.assertEqual(len(records), 8)
            self.assertEqual(records[-1]['status'], 'OK')

    def test_missing_raw_or_parquet_is_a_failure_not_a_skip(self):
        for missing in ('raw', 'interim'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                raw, interim = self.ingest_fixture(Path(temporary))
                (raw / 'groups.csv' if missing == 'raw' else interim / 'groups.parquet').unlink()
                code, records = self.run_ingest(raw, interim)
                self.assertEqual(code, 1)
                self.assertEqual(len(records), 8)
                self.assertEqual(records[0]['status'], 'ERROR')
                self.assertEqual(records[-1]['status'], 'OK')

    def test_empty_or_malformed_csv_cannot_pass(self):
        for content in ('', 'id,note\n1,"private unterminated\n'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                raw, interim = self.ingest_fixture(Path(temporary))
                (raw / 'groups.csv').write_text(content)
                code, records = self.run_ingest(raw, interim)
                self.assertEqual(code, 1)
                self.assertEqual(records[0]['status'], 'ERROR')

    def test_checks_run_exact_commands_and_propagate_quality_failure(self):
        with patch.object(verify_data.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            run.side_effect = [type('Result', (), {'returncode': code})() for code in (0, 4)]
            self.assertEqual(verify_data.main(['checks']), 4)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands, [
                [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                [sys.executable, '-B', '-m', 'xray.cli', 'quality'],
            ])
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([row['command'] for row in records if 'exit_code' in row], commands)
            self.assertEqual([row['exit_code'] for row in records if 'exit_code' in row], [0, 4])

    def test_quality_never_runs_after_unittest_failure(self):
        with patch.object(verify_data.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = 3
            self.assertEqual(verify_data.main(['checks']), 3)
            run.assert_called_once_with(
                [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                cwd=verify_data.ROOT)

    def test_launch_error_is_nonzero_and_does_not_run_quality(self):
        with patch.object(verify_data.subprocess, 'run', side_effect=OSError('unavailable')) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(verify_data.main(['checks']), 0)
            self.assertEqual(run.call_count, 1)
