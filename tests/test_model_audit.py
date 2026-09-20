import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from xray import model_audit
from xray.pipeline import digest


class ModelAuditTest(unittest.TestCase):
    def test_lfs_pointer_separates_expected_oid_from_pointer_bytes(self):
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{"a" * 64}\nsize 12345\n'
        self.assertEqual(model_audit.parse_lfs_pointer(pointer.encode()), ('a' * 64, 12345))
        with self.assertRaises(ValueError):
            model_audit.parse_lfs_pointer(b'company_id,amount\nfictional,1\n')

    def test_manifest_hashes_detect_changes_and_reject_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / 'artifact.txt'
            file.write_text('original')
            expected = {'artifact.txt': digest(file)}
            self.assertTrue(model_audit.verify_files(root, expected)['artifact.txt']['matches'])
            file.write_text('modified')
            self.assertFalse(model_audit.verify_files(root, expected)['artifact.txt']['matches'])
            with self.assertRaises(ValueError):
                model_audit.verify_files(root, {'../outside.txt': 'a' * 64})

    def test_logical_comparison_ignores_byte_order_and_tiny_float_noise(self):
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as con:
            old, new = (Path(temporary) / name for name in ('old.parquet', 'new.parquet'))
            con.execute(f'''COPY (SELECT 'a' AS company_id,DATE '2025-01-01' AS month, 1.0::DOUBLE AS value
                UNION ALL SELECT 'b', DATE '2025-01-01', NULL) TO '{old}' (FORMAT PARQUET)''')
            con.execute(f'''COPY (SELECT 'b' AS company_id,DATE '2025-01-01' AS month,NULL::DOUBLE AS value
                UNION ALL SELECT 'a',DATE '2025-01-01',1.0+1e-10) TO '{new}' (FORMAT PARQUET)''')
            self.assertNotEqual(digest(old), digest(new))
            self.assertTrue(model_audit.compare_panel(con, old, new)['logical_matches'])
            con.execute(f'''COPY (SELECT 'a' AS company_id,DATE '2025-01-01' AS month,2.0::DOUBLE AS value
                UNION ALL SELECT 'b',DATE '2025-01-01',NULL) TO '{new}' (FORMAT PARQUET)''')
            self.assertFalse(model_audit.compare_panel(con, old, new)['logical_matches'])

    def monetary_comparison(self, before, after, gross=1e9, coverages=(0.5, 0.5), counts=(1, 1),
                            column='operativo_max_eur'):
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as con:
            files = [Path(temporary) / name for name in ('old.parquet', 'new.parquet')]
            for file, amount, coverage, count in zip(files, (before, after), coverages, counts):
                con.execute(f'''COPY (SELECT 'a' AS company_id,DATE '2025-01-01' AS month,
                    {amount}::DOUBLE AS {column},{coverage}::DOUBLE AS coverage,{count}::BIGINT AS n_tx)
                    TO '{file}' (FORMAT PARQUET)''')
            exposure = (f"SELECT 'a' AS company_id,DATE '2025-01-01' AS month,"
                        f'{gross}::DOUBLE AS _cash_gross_eur,0.0 AS _outside_gross_eur')
            return model_audit.compare_panel(con, *files, gross_sources=(exposure, exposure))

    def test_subcent_reduction_keeps_strict_failure_and_accepts_economic_identity(self):
        result = self.monetary_comparison('1.0', '1.0000001')
        self.assertFalse(result['logical_matches'])
        self.assertTrue(result['economic_matches'])
        stats = result['monetary_diagnostics']['operativo_max_eur']
        self.assertGreater(stats['max_abs_error'], 1e-8)
        self.assertLess(stats['max_error_over_gross'], 1e-12)
        self.assertEqual(stats['different_cents'], 0)
        self.assertEqual(stats['cent_sign_changes'], 0)

    def test_other_monetary_aggregates_accept_crossing_half_cent_with_strict_diagnostics(self):
        for before, after in (('1.0049999', '1.0050001'), ('-0.0049999', '-0.0050001')):
            with self.subTest(before=before, after=after):
                result = self.monetary_comparison(before, after, column='neto_caja_eur')
                self.assertFalse(result['logical_matches'])
                self.assertTrue(result['economic_matches'])
                stats = result['monetary_diagnostics']['neto_caja_eur']
                self.assertEqual(stats['different_cents'], 1)
                self.assertEqual(stats['cent_sign_changes'], int(before.startswith('-')))
                self.assertFalse(stats['cent_identity_required'])

    def test_monetary_bounds_reject_different_cents_and_signs_even_for_subcent_errors(self):
        bounds = ('operativo_min_eur', 'operativo_max_eur',
                  'operativo_min_sin_atipicos_eur', 'operativo_max_sin_atipicos_eur')
        for column in bounds:
            for before, after in (('1.0049999', '1.0050001'), ('-0.0049999', '-0.0050001')):
                with self.subTest(column=column, before=before, after=after):
                    result = self.monetary_comparison(before, after, column=column)
                    self.assertFalse(result['economic_matches'])
                    stats = result['monetary_diagnostics'][column]
                    self.assertEqual(stats['different_cents'], 1)
                    self.assertEqual(stats['cent_sign_changes'], int(before.startswith('-')))

    def test_other_monetary_aggregates_keep_cent_error_missingness_nonfinite_and_gross_guards(self):
        for before, after, gross in (
            ('0.0', '0.01', 1e15), ('0.0', '0.02', 1e15),
            ('NULL', '0.0', 1e9), ('0.0', 'NULL', 1e9),
            ("'NaN'", "'NaN'", 1e9), ("'Infinity'", "'Infinity'", 1e9),
            ('0.0', "'NaN'", 1e9), ("'-Infinity'", '0.0', 1e9),
            ('1.0', '1.0000001', 1), ('1.0', '1.0000001', 0),
        ):
            with self.subTest(before=before, after=after, gross=gross):
                result = self.monetary_comparison(before, after, gross, column='neto_caja_eur')
                self.assertFalse(result['economic_matches'])
        self.assertTrue(self.monetary_comparison('NULL', 'NULL', column='neto_caja_eur')['economic_matches'])
        self.assertTrue(self.monetary_comparison('0.0', '0.009', 1e15, column='neto_caja_eur')['economic_matches'])

    def test_economic_policy_keeps_nonmonetary_comparator_strict(self):
        for changes in ({'coverages': (0.5, 0.500001)}, {'counts': (1, 2)}):
            with self.subTest(changes=changes):
                result = self.monetary_comparison('1.0', '1.0', **changes)
                self.assertFalse(result['logical_matches'])
                self.assertFalse(result['economic_matches'])

    def test_economic_policy_rejects_bad_scale_missingness_and_nonfinite_values(self):
        for before, after, gross in (
            ('1.0', '1.0000001', 1), ('1.0', '1.0000001', 0),
            ('NULL', '0.0', 1e9), ("'NaN'", "'NaN'", 1e9),
            ('1000000000000000.0', '1000000000000000.125', 1e15),
        ):
            with self.subTest(before=before, after=after, gross=gross):
                self.assertFalse(self.monetary_comparison(before, after, gross)['economic_matches'])

    def source_comparison(self, before, after, blob=None, old_hash=None, new_hash=None, returncode=0):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            relative = 'xray/example.py'
            file = run / 'source' / relative
            file.parent.mkdir(parents=True)
            file.write_bytes(after)
            historical = {'git_revision': 'a' * 40, 'source': {
                relative: old_hash if old_hash is not None else hashlib.sha256(before).hexdigest()}}
            manifest = {'source': {relative: new_hash if new_hash is not None else digest(file)}}
            with patch.object(model_audit.subprocess, 'run') as command:
                command.return_value.stdout = before if blob is None else blob
                command.return_value.returncode = returncode
                result = model_audit.compare_economic_sources(run, historical, manifest)[relative]
                if result['same_manifest_bytes']:
                    command.assert_not_called()
                elif result['current_bytes_verified']:
                    command.assert_called_once_with(['git', 'show', f'{historical["git_revision"]}:{relative}'],
                                                    cwd=run, capture_output=True)
            return result

    def test_source_comparison_accepts_only_hash_verified_eol_equivalence(self):
        lf = b'value = 1\nother = 2\n'
        crlf = lf.replace(b'\n', b'\r\n')
        for before, after, blob in ((lf, crlf, lf), (crlf, lf, lf), (lf, crlf, crlf)):
            with self.subTest(before=before, after=after, blob=blob):
                result = self.source_comparison(before, after, blob)
                self.assertTrue(result['matches'])
                self.assertFalse(result['same_manifest_bytes'])
                self.assertTrue(result['current_bytes_verified'])
                self.assertTrue(result['historical_hash_verified'])
                self.assertTrue(result['normalized_bytes_match'])
                self.assertEqual(result['reason'], 'Verified LF/CRLF equivalence')
                self.assertEqual(result['git_blob_sha256'], hashlib.sha256(blob).hexdigest())

    def test_source_comparison_verifies_identical_hashes_against_actual_source(self):
        result = self.source_comparison(b'value = 1\n', b'value = 1\n')
        self.assertTrue(result['matches'])
        self.assertTrue(result['same_manifest_bytes'])
        self.assertFalse(self.source_comparison(b'value = 1\n', b'value = 1\n',
                                               old_hash='f' * 64, new_hash='f' * 64)['matches'])

    def test_source_comparison_rejects_real_changes_unknown_hashes_and_unavailable_blobs(self):
        lf = b'value = 1\n'
        cases = (
            (lf, b'value = 2\r\n', {}),
            (lf, lf.replace(b'\n', b'\r\n'), {'old_hash': 'f' * 64}),
            (lf, lf.replace(b'\n', b'\r\n'), {'new_hash': 'f' * 64}),
            (lf, lf.replace(b'\n', b'\r\n'), {'returncode': 128}),
            (b'value = 1\r', lf, {}),
        )
        for before, after, options in cases:
            with self.subTest(before=before, after=after, options=options):
                self.assertFalse(self.source_comparison(before, after, **options)['matches'])

    def test_source_comparison_rejects_missing_sources_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            source = {'xray/example.py': 'f' * 64}
            historical = {'git_revision': 'a' * 40, 'source': source}
            for before, after in ((source, {}), ({}, source), (source, source)):
                with self.subTest(before=before, after=after):
                    result = model_audit.compare_economic_sources(run, dict(historical, source=before), {'source': after})
                    self.assertFalse(result['xray/example.py']['matches'])
            with self.assertRaises(ValueError):
                model_audit.compare_economic_sources(run, historical, {'source': {'xray/../../outside.py': 'f' * 64}})

    def test_historical_comparison_exposes_source_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as con:
            run = Path(temporary)
            historical = {'run_id': 'previous', 'git_revision': 'a' * 40,
                          'inputs': {}, 'outputs': {}, 'parameters': {}, 'source': {}}
            model_audit.write_json(run / 'before/reports/build_manifest.json', historical)
            sources = {'xray/changed.py': {'matches': False, 'same_manifest_bytes': False},
                       'xray/eol.py': {'matches': True, 'same_manifest_bytes': False}}
            with patch.object(model_audit, 'compare_economic_sources', return_value=sources), \
                 patch.object(model_audit, 'gross_exposure', return_value='SELECT 1'):
                result = model_audit.historical_comparison(con, run, historical)
            self.assertFalse(result['economic_sources_match'])
            self.assertEqual(result['economic_sources'], sources)
            self.assertEqual(result['economic_sources_differing_bytes'], ['xray/changed.py', 'xray/eol.py'])
            self.assertEqual(result['economic_sources_mismatches'], ['xray/changed.py'])

    def test_target_comparison_does_not_consume_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary, duckdb.connect() as con:
            files = [Path(temporary) / name for name in ('old.parquet', 'new.parquet')]
            for file, outcome in zip(files, ('true', 'false')):
                con.execute(f'''COPY (SELECT 'a' AS company_id,'g' AS group_id,DATE '2025-01-01' AS month,
                    DATE '2025-01-31' AS origin_as_of,DATE '2025-04-30' AS available_at_deficit,
                    NULL::VARCHAR AS motivo_deficit_no_observable,1 AS target_version,
                    {outcome} AS target_deficit_3m) TO '{file}' (FORMAT PARQUET)''')
            result = model_audit.compare_panel(con, *files, model_audit.TARGET_METADATA)
            self.assertTrue(result['logical_matches'])
            self.assertNotIn('target_deficit_3m', result['columns_compared'])

    def test_verification_appends_unique_logs_preserves_history_and_rejects_stale_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'scripts/verify_data.py'
            script.parent.mkdir(parents=True)
            script.write_text('value = 1\n')
            model_audit.write_json(root / 'reports/current.json', {'workspace': 'data/runs/test'})
            record = root / 'reports/modeling/recheck.json'
            legacy = {'command': ['bash', 'scripts/run_checks.sh'], 'exit_code': 2, 'log': 'legacy.log'}
            model_audit.write_json(record, [legacy])
            old_digest = digest(record)
            with patch.object(model_audit.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                run.return_value.returncode = 2
                model_audit.record_check(root, 'run_checks')
                old = json.loads(record.read_text())
                run.return_value.returncode = 0
                model_audit.record_check(root, 'run_checks')
                attempts = json.loads(record.read_text())
                self.assertEqual(attempts[:2], old)
                self.assertEqual(attempts[0], legacy)
                self.assertTrue((record.parent / 'history' / f'recheck.{old_digest}.json').exists())
                self.assertNotEqual(attempts[1]['log'], attempts[2]['log'])
                self.assertEqual(attempts[2]['workspace_fingerprint_before'], attempts[2]['workspace_fingerprint'])
                status = model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], model_audit.current_source_hashes(root))
                self.assertTrue(status['passed'])
                self.assertEqual(status['attempt_index'], 2)
                script.write_text('changed source\n')
                self.assertFalse(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1],
                                                         model_audit.current_source_hashes(root))['passed'])

    def test_latest_current_failure_is_not_hidden_by_an_older_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / 'check.log'
            log.write_text('ok')
            attempt = {'command': model_audit.CHECK_COMMANDS[1][1], 'exit_code': 0,
                       'source_hashes': {}, 'source_hashes_after': {}, 'sources_unchanged': True,
                       'workspace_fingerprint': None, 'workspace_unchanged': True,
                       'log': 'check.log', 'log_sha256': digest(log)}
            self.assertTrue(model_audit.current_check(root, [attempt], attempt['command'], {})['passed'])
            attempts = [attempt, dict(attempt, exit_code=1)]
            self.assertFalse(model_audit.current_check(root, attempts, attempt['command'], {})['passed'])

    def test_verify_runs_only_two_fixed_portable_commands_then_audit_without_replaying_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_audit.write_json(root / 'reports/modeling/recheck.json', [
                {'command': ['untrusted', 'build'], 'exit_code': 0}])
            with patch.object(model_audit.subprocess, 'run') as run, \
                 patch.object(model_audit, 'audit', return_value=0) as audit, \
                 contextlib.redirect_stdout(io.StringIO()):
                run.side_effect = [type('Result', (), {'returncode': code})() for code in (2, 0)]
                self.assertEqual(model_audit.main(['--root', str(root), '--verify']), 2)
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    [sys.executable, '-B', '-m', 'scripts.verify_data', 'checks'],
                    [sys.executable, '-B', '-m', 'scripts.verify_data', 'ingest'],
                ])
                audit.assert_called_once_with(root, verify=True)

    def test_default_mode_does_not_execute_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(model_audit, 'record_check') as check, \
                 patch.object(model_audit, 'audit', return_value=1) as audit:
                self.assertEqual(model_audit.main(['--root', str(root)]), 1)
                check.assert_not_called()
                audit.assert_called_once_with(root, verify=False)

    def test_log_tampering_and_workspace_changes_invalidate_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(model_audit.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                run.return_value.returncode = 0
                model_audit.record_check(root, 'run_checks')
            attempts = json.loads((root / 'reports/modeling/recheck.json').read_text())
            self.assertTrue(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], {})['passed'])
            log = root / attempts[0]['log']
            original = log.read_bytes()
            log.write_text('tampered')
            self.assertFalse(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], {})['passed'])
            log.write_bytes(original)
            model_audit.write_json(root / 'reports/current.json', {'workspace': 'data/runs/other'})
            self.assertFalse(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], {})['passed'])

    def test_source_change_during_check_is_not_hidden_by_older_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'scripts/verify_data.py'
            source.parent.mkdir()
            source.write_text('before\n')
            sources = model_audit.current_source_hashes(root)
            with patch.object(model_audit.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                run.return_value.returncode = 0
                model_audit.record_check(root, 'run_checks')
                run.side_effect = lambda *args, **kwargs: (source.write_text('after\n'), type('Result', (), {'returncode': 0})())[1]
                model_audit.record_check(root, 'run_checks')
            source.write_text('before\n')
            attempts = json.loads((root / 'reports/modeling/recheck.json').read_text())
            self.assertFalse(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], sources)['passed'])

    def test_launch_error_is_recorded_as_failure_and_unknown_checks_cannot_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(model_audit.subprocess, 'run', side_effect=OSError('unavailable')) as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(model_audit.record_check(root, 'run_checks'), 0)
                with self.assertRaises(ValueError):
                    model_audit.record_check(root, 'build')
                self.assertEqual(run.call_count, 1)
            attempts = json.loads((root / 'reports/modeling/recheck.json').read_text())
            self.assertFalse(model_audit.current_check(root, attempts, model_audit.CHECK_COMMANDS[0][1], {})['passed'])

    def test_missing_inputs_produce_readable_blocked_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(model_audit, 'raw_identity', side_effect=FileNotFoundError('data/groups.csv')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(model_audit.audit(root), 1)
            report = json.loads((root / 'reports/modeling/data_gate.json').read_text())
            self.assertEqual(report['gate_status'], 'blocked')
            self.assertIn('FileNotFoundError', report['blockers'][0])
            self.assertIn('data/groups.csv', report['blockers'][0])

    def test_invalid_manifest_produces_readable_blocked_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / 'build.log'
            log.write_text('Publicada fixture;')
            model_audit.write_json(root / 'reports/modeling/recheck.json', [{
                'command': ['.venv/bin/python', '-B', '-m', 'xray.cli', 'build'],
                'exit_code': 0, 'log': 'build.log', 'log_sha256': digest(log)}])
            model_audit.write_json(root / 'reports/current.json', {'workspace': 'data/runs/fixture'})
            model_audit.write_json(root / 'data/runs/fixture/manifest.json', {})
            with patch.object(model_audit, 'raw_identity', return_value={}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(model_audit.audit(root), 1)
            report = json.loads((root / 'reports/modeling/data_gate.json').read_text())
            self.assertEqual(report['gate_status'], 'blocked')
            self.assertTrue(any('KeyError' in blocker for blocker in report['blockers']))
