import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from xray import paths
from xray.clean.facts import _clean_invoices, _clean_transactions
from xray.schema import TABLES


class PipelineRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mapping = {
            'ROOT': self.root,
            'WORKSPACE': self.root,
            'MARTS_DIR': self.root / 'data/marts',
            'RAW_DIR': self.root / 'data',
            'INTERIM_DIR': self.root / 'data/interim',
            'CLEAN_DIR': self.root / 'data/clean',
            'REPORTS_DIR': self.root / 'reports/quality',
        }
        for folder in self.mapping.values():
            folder.mkdir(parents=True, exist_ok=True)
        self.patcher = patch.multiple(paths, **self.mapping)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.con = duckdb.connect(':memory:')
        self.con.execute('SET threads=2')
        self.addCleanup(self.con.close)

    def table(self, name, rows, stage='interim'):
        spec = TABLES[name]
        cols = ', '.join(f'"{c.name}" {c.dtype}' for c in spec.columns)
        self.con.execute(f'CREATE OR REPLACE TEMP TABLE fixture ({cols}, _src_row BIGINT, _cast_failures VARCHAR[])')
        values = [tuple(r.get(col.name) for col in spec.columns) + (idx, [])
                  for idx, r in enumerate(rows, 1)]
        if values:
            placeholders = ','.join('?' for _ in values[0])
            self.con.executemany(f'INSERT INTO fixture VALUES ({placeholders})', values)
        target = self.root / 'data' / stage / f'{name}.parquet'
        target.parent.mkdir(parents=True, exist_ok=True)
        self.con.execute(f"COPY fixture TO '{target}' (FORMAT PARQUET)")

    def invoice(self, **changes):
        row = dict(operation_id='i', company_id='a', document_type='invoice',
                   issuance_date='2025-01-01', due_date='2025-01-31',
                   payment_date='2025-01-20', amount=100, pending_amount=0,
                   currency='EUR', accounting_currency='EUR', exchange_rate=1,
                   status='paid', counterparty_id='cp')
        row.update(changes)
        return row

    def test_invoice_fx_preserves_identity_and_unknown(self):
        self.table('invoices', [
            self.invoice(operation_id='eur', accounting_currency='USD', exchange_rate=0.86),
            self.invoice(operation_id='same', exchange_rate=1.17),
            self.invoice(operation_id='ambiguous', currency='USD', exchange_rate=1),
            self.invoice(operation_id='reported', currency='USD', exchange_rate=2),
            self.invoice(operation_id='foreign', currency='GBP', accounting_currency='GBP', exchange_rate=2),
        ])
        _clean_invoices(self.con)
        result = self.con.execute(f"SELECT operation_id, amount_eur, amount_acct FROM '{paths.CLEAN_DIR / 'invoices.parquet'}'").fetchall()
        actual = {key: (eur, acct) for key, eur, acct in result}
        self.assertEqual(actual['eur'][0], 100)
        self.assertEqual(actual['same'], (100, 100))
        self.assertEqual(actual['ambiguous'], (None, None))
        self.assertEqual(actual['reported'], (50, 50))
        self.assertEqual(actual['foreign'], (None, 100))

    def test_transaction_fx_marks_ambiguity(self):
        self.table('banking_products', [dict(product_id='usd', currency='USD', type='checking'),
                                        dict(product_id='eur', currency='EUR', type='checking')])
        self.table('debt_products', [])
        self.table('transactions', [dict(transaction_id=str(n), company_id='a', product_id=product,
                                         date='2025-01-01', amount=100, exchange_rate=rate, status='booked')
                                    for n, (product, rate) in enumerate([('usd', 1), ('usd', 2), ('eur', 1), ('missing', 1)])])
        _clean_transactions(self.con)
        rows = self.con.execute(f"SELECT amount_eur, amount_eur_source, quality_flags FROM '{paths.CLEAN_DIR / 'transactions.parquet'}' ORDER BY _src_row").fetchall()
        self.assertEqual([r[0] for r in rows], [None, 50, 100, None])
        self.assertEqual([r[1] for r in rows], ['unknown', 'reported', 'identity', 'unknown'])
        self.assertIn('fx_not_convertible', rows[0][2])

    def test_classification_uses_only_company_past_and_preserves_direction(self):
        from xray.flows import classify_flows

        self.table('banking_products', [dict(product_id='eur', currency='EUR', type='checking')])
        self.table('debt_products', [])
        rows = [dict(transaction_id=str(n), company_id=company, product_id='eur',
                     date=date, amount=amount, exchange_rate=1, status='booked',
                     category=category, description='OPAQUE REFERENCE')
                for n, (company, date, amount, category) in enumerate([
                    ('b', '2024-12-01', -100, 'transfer'),
                    ('a', '2025-01-01', -100, None),
                    ('a', '2025-02-01', -100, 'utility'),
                    ('a', '2025-03-01', -100, None),
                    ('a', '2025-03-02', 100, 'payment'),
                ])]
        self.table('transactions', rows)
        _clean_transactions(self.con)
        classify_flows(self.con)
        file = paths.INTERIM_DIR / 'tx_flow_class.parquet'
        before = self.con.execute(f"SELECT * FROM '{file}' ORDER BY _src_row").fetchall()
        self.assertEqual(before[1][2], 'unknown')
        self.assertEqual(before[3][2:4], ('operating_out', 'transferida'))
        self.assertEqual(before[4][2], 'operating_in')
        rows.append(dict(rows[0], transaction_id='future', company_id='a', date='2025-04-01'))
        self.table('transactions', rows)
        _clean_transactions(self.con)
        classify_flows(self.con)
        after = self.con.execute(f"SELECT * FROM '{file}' WHERE _src_row <= 5 ORDER BY _src_row").fetchall()
        self.assertEqual(before, after)

    def test_panel_currency_dates_and_availability(self):
        from xray.marts import cobro

        self.table('companies', [dict(company_id=key, group_id='g') for key in ('a', 'b', 'c', 'd')], 'clean')
        self.table('invoices', [
            self.invoice(operation_id='a1'),
            self.invoice(operation_id='a2', currency='GBP', exchange_rate=2, payment_date='2025-02-10'),
            self.invoice(operation_id='b', company_id='b', currency='GBP', accounting_currency='GBP', status='pending', pending_amount=100),
            self.invoice(operation_id='c', company_id='c', payment_date='2000-01-01'),
            self.invoice(operation_id='d', company_id='d', due_date='7025-01-01', status='pending', pending_amount=100),
        ])
        _clean_invoices(self.con)
        target = self.root / 'data/marts/panel_cobro.parquet'
        with patch.object(cobro, 'PANEL_PATH', target), patch.object(cobro, 'REPORT_PATH', paths.REPORTS_DIR / 'panel_cobro.json'):
            cobro.build_panel_cobro(self.con)
            rows = self.con.execute(f"SELECT company_id,ar_abierto_eur,ar_vencido_eur,coh_pct_cobrado_30d,coh_pct_cobrado_60d,coh_pct_cobrado_90d FROM '{target}' WHERE month=DATE '2025-01-01' ORDER BY company_id").fetchall()
            self.assertAlmostEqual(rows[0][3], 2/3)
            self.assertEqual(rows[0][4:], (1, 1))
            self.assertIsNone(rows[1][1])
            self.assertIsNone(rows[2][1])
            self.assertEqual(rows[3][1], 100)
            self.assertIsNone(rows[3][2])
            self.assertEqual(cobro.cohort_features_at(self.con, '2025-02-28').fetchall(), [])
            matured = cobro.cohort_features_at(self.con, '2025-03-02').fetchall()
            self.assertEqual(len(matured), 2)
            self.assertAlmostEqual(next(row[3] for row in matured if row[0] == 'a'), 2/3)
            self.assertEqual(self.con.execute(f"SELECT count(*) FROM '{target}' WHERE month<DATE '2025-01-01' AND ar_abierto_eur IS NOT NULL").fetchone()[0], 0)

    def test_observability_uses_calendar_future_not_past(self):
        from xray.flows import classify_flows
        from xray.marts import observabilidad

        self.table('companies', [dict(company_id=key, group_id='g') for key in ('a', 'b')], 'clean')
        self.table('banking_products', [dict(product_id='eur', currency='EUR', type='checking')])
        self.table('debt_products', [])
        months = {'a': ['2024-09', '2024-10', '2024-11', '2024-12', '2025-02', '2025-03'],
                  'b': ['2024-09', '2024-12', '2025-01', '2025-02', '2025-03']}
        self.table('transactions', [dict(transaction_id=key+month, company_id=key, product_id='eur',
                                        date=month+'-01', amount=100, exchange_rate=1, status='booked', category='collection')
                                   for key, dates in months.items() for month in dates])
        _clean_transactions(self.con)
        classify_flows(self.con)
        target = self.root / 'data/marts/observabilidad.parquet'
        with patch.object(observabilidad, 'OUT_PATH', target), patch.object(observabilidad, 'REPORT_PATH', paths.REPORTS_DIR / 'observabilidad.json'):
            observabilidad.build_observabilidad(self.con)
        self.assertEqual(self.con.execute(f"SELECT count(*) FROM '{target}'").fetchone()[0], 48)
        actual = self.con.execute(f"SELECT company_id,objetivo_observable FROM '{target}' WHERE month=DATE '2024-12-01' ORDER BY company_id").fetchall()
        self.assertEqual(actual, [('a', False), ('b', True)])

    def test_bad_dates_are_preserved_but_not_used_as_settlement(self):
        self.table('invoices', [
            self.invoice(operation_id='negative', payment_date='2000-01-01'),
            self.invoice(operation_id='planned', status='pending'),
            self.invoice(operation_id='partial', pending_amount=30),
            self.invoice(operation_id='valid'),
        ])
        _clean_invoices(self.con)
        rows = self.con.execute(f"SELECT operation_id, settlement_date_ok, days_to_payment, payment_date FROM '{paths.CLEAN_DIR / 'invoices.parquet'}' ORDER BY _src_row").fetchall()
        for row in rows[:3]:
            self.assertIsNone(row[1])
            self.assertIsNone(row[2])
            self.assertIsNotNone(row[3])
        self.assertEqual(rows[3][2], 19)
