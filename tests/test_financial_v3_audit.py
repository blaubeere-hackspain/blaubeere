import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from scripts import financial_v3_audit as audit


class FinancialV3AuditTests(unittest.TestCase):
    def test_pinned_paths_only(self):
        root = Path('/repo')
        self.assertEqual(audit.pinned_input(root, 'data/clean/balances.parquet'),
                         root / 'data/runs' / audit.RUN_ID / 'data/clean/balances.parquet')
        for relative in ('../balances.parquet', 'data/marts/targets_proxy.parquet',
                         'data/current.json', '/tmp/a.parquet'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                audit.pinned_input(root, relative)

    def test_exclusion_contract(self):
        assignment = {f'g{i}': 'final_test' if i < 50 else 'train' for i in range(250)}
        result = audit.assignment_summary(assignment)
        self.assertEqual(result['excluded_groups'], 50)
        self.assertEqual(result['development_groups'], 200)
        self.assertEqual(result['new_confirmatory_groups_identified'], 0)
        assignment['g0'] = 'train'
        with self.assertRaises(ValueError):
            audit.assignment_summary(assignment)

    def test_only_bounded_select(self):
        con = duckdb.connect(':memory:')
        try:
            self.assertEqual(audit.select_rows(con, 'SELECT 2 AS n'), [{'n': 2}])
            for sql in ('CREATE TABLE x(i INTEGER)', 'SELECT 1; SELECT 2', 'COPY x TO x'):
                with self.subTest(sql=sql), self.assertRaises(ValueError):
                    audit.select_rows(con, sql)
            with self.assertRaises(ValueError):
                audit.select_rows(con, 'SELECT * FROM range(4)', max_rows=3)
        finally:
            con.close()

    def test_ledger_gaps_are_not_zero_months(self):
        con = duckdb.connect(':memory:')
        try:
            source = "(VALUES ('a', DATE '2024-09-01'), ('a', DATE '2024-11-01'), ('b', DATE '2024-10-01')) AS t(product_id, month)"
            result = audit.select_rows(con, audit.ledger_coverage_sql(source))
            self.assertEqual(result, [{'active_months': 1, 'internal_gap_months': 0, 'products': 1},
                                      {'active_months': 2, 'internal_gap_months': 1, 'products': 1}])
        finally:
            con.close()

    def test_hash_diff_missing_and_modified(self):
        differences = audit.compare_hashes({'a': '1', 'b': '2'}, {'a': '3', 'c': '4'})
        self.assertEqual(differences, [{'path': 'a', 'reason': 'changed'},
                                       {'path': 'b', 'reason': 'missing'},
                                       {'path': 'c', 'reason': 'added'}])

    def test_check_does_not_run_audit(self):
        with patch.object(audit, 'check_baseline', return_value={'ok': True}) as check, \
             patch.object(audit, 'capture', side_effect=AssertionError('capture forbidden')):
            self.assertEqual(audit.main(['--check']), 0)
            check.assert_called_once()


if __name__ == '__main__':
    unittest.main()
