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

    def cash_tx(self, amount=100, category='collection', date='2025-01-15', company='a', product='cash', **changes):
        row = dict(company_id=company, product_id=f'{company}-{product}', date=date,
                   amount=amount, exchange_rate=1, status='booked', category=category,
                   description='OPAQUE REFERENCE')
        row.update(changes)
        return row

    def cash_tables(self, rows):
        from xray.flows import classify_flows

        companies = sorted({'empty', *(row['company_id'] for row in rows)})
        self.table('companies', [dict(company_id=key, group_id='g-'+key) for key in companies], 'clean')
        self.table('banking_products', [dict(product_id=f'{key}-{product}', company_id=key,
                                           currency='USD' if product=='usd' else 'EUR',
                                           type='card' if product=='card' else 'checking')
                                        for key in companies for product in ('cash', 'usd', 'card')])
        self.table('debt_products', [dict(product_id=f'{key}-debt', company_id=key, currency='EUR', type='loan')
                                     for key in companies])
        self.table('transactions', [dict(row, transaction_id=f'tx-{n}') for n, row in enumerate(rows)])
        _clean_transactions(self.con)
        classify_flows(self.con)

    def collection_panel(self, invoices):
        from xray.marts import cobro

        self.table('invoices', invoices)
        _clean_invoices(self.con)
        with patch.object(cobro, 'PANEL_PATH', paths.MARTS_DIR / 'panel_cobro.parquet'), \
             patch.object(cobro, 'REPORT_PATH', paths.REPORTS_DIR / 'panel_cobro.json'):
            cobro.build_panel_cobro(self.con)

    def test_cash_panel_reconciles_without_double_counting_ledgers(self):
        from xray.marts.flujos import build_panel_flujos

        self.cash_tables([
            self.cash_tx(100), self.cash_tx(-40, 'payment'),
            self.cash_tx(-10, 'debt_repayment'), self.cash_tx(20, 'transfer'),
            self.cash_tx(-7, None),
            self.cash_tx(-3, None, description='COMPENSATION FOR MISSING OR EXCESS'),
            self.cash_tx(-10, 'debt_repayment', product='debt'),
            self.cash_tx(-5, 'payment', product='card'),
        ])
        build_panel_flujos(self.con)
        file = paths.MARTS_DIR / 'panel_flujos.parquet'
        actual = self.con.execute(f"""
            SELECT n_tx_caja, n_tx_fuera_perimetro, neto_caja_eur,
                   fuera_perimetro_conocido_eur, operativo_min_eur, operativo_max_eur,
                   cobros_operativos_eur, pagos_operativos_eur
            FROM '{file}' WHERE company_id='a' AND month=DATE '2025-01-01'
        """).fetchone()
        self.assertEqual(actual, (6, 2, 60, -15, 50, 80, None, None))
        self.assertEqual(self.con.execute(f"SELECT count(*) FROM '{file}'").fetchone()[0], 48)

    def test_cash_panel_does_not_turn_missing_fx_or_activity_into_zero(self):
        from xray.marts.flujos import build_panel_flujos

        self.cash_tables([self.cash_tx(product='usd'), self.cash_tx(-10, 'payment')])
        build_panel_flujos(self.con)
        file = paths.MARTS_DIR / 'panel_flujos.parquet'
        actual = self.con.execute(f"""
            SELECT neto_caja_eur, neto_caja_conocido_eur, operativo_min_eur,
                   operativo_max_eur, cobertura_eur_pct, pagos_operativos_eur
            FROM '{file}' WHERE company_id='a' AND month=DATE '2025-01-01'
        """).fetchone()
        self.assertEqual(actual, (None, -10, None, None, 0.5, 10))
        self.assertEqual(self.con.execute(f"SELECT count(*) FROM '{file}' WHERE company_id='empty' AND neto_caja_conocido_eur IS NOT NULL").fetchone()[0], 0)

    def test_cash_panel_rejects_misaligned_classification(self):
        from xray.marts.flujos import build_panel_flujos

        self.cash_tables([self.cash_tx()])
        file = paths.INTERIM_DIR / 'tx_flow_class.parquet'
        self.con.execute(f"CREATE TEMP TABLE broken AS SELECT * FROM '{file}' WHERE false")
        self.con.execute(f"COPY broken TO '{file}' (FORMAT PARQUET)")
        with self.assertRaises(ValueError):
            build_panel_flujos(self.con)

    def test_debt_service_keeps_refunds_and_product_entries_separate(self):
        from xray.marts.flujos import build_panel_flujos
        from xray.marts.deuda import build_panel_deuda

        self.cash_tables([
            self.cash_tx(-30, 'debt_repayment'), self.cash_tx(-5, 'interest_charge'),
            self.cash_tx(2, 'interest_charge'), self.cash_tx(-30, 'debt_repayment', product='debt'),
        ])
        build_panel_flujos(self.con)
        build_panel_deuda(self.con)
        file = paths.MARTS_DIR / 'panel_deuda.parquet'
        actual = self.con.execute(f"""
            SELECT principal_conocido_eur, intereses_conocido_eur,
                   servicio_deuda_eur, reintegros_deuda_eur, servicio_deuda_neto_eur,
                   n_tx_producto_deuda, ledger_deuda_neto_conocido_eur
            FROM '{file}' WHERE company_id='a' AND month=DATE '2025-01-01'
        """).fetchone()
        self.assertEqual(actual, (30, 5, 35, 2, 33, 1, -30))

    def test_debt_service_unknown_is_not_no_debt(self):
        from xray.marts.flujos import build_panel_flujos
        from xray.marts.deuda import build_panel_deuda

        self.cash_tables([self.cash_tx(-10, 'debt_repayment'), self.cash_tx(-5, None)])
        build_panel_flujos(self.con)
        build_panel_deuda(self.con)
        file = paths.MARTS_DIR / 'panel_deuda.parquet'
        self.assertEqual(self.con.execute(f"SELECT servicio_deuda_conocido_eur, servicio_deuda_eur FROM '{file}' WHERE company_id='a' AND month=DATE '2025-01-01'").fetchone(), (10, None))
        self.assertEqual(self.con.execute(f"SELECT count(*) FROM '{file}' WHERE company_id='empty' AND servicio_deuda_eur IS NOT NULL").fetchone()[0], 0)

    def evidence_panels(self, rows, invoices=()):
        from xray.marts.flujos import build_panel_flujos
        from xray.marts.deuda import build_panel_deuda
        from xray.marts.evidencia import build_panel_evidencia

        self.cash_tables(rows)
        self.collection_panel(list(invoices))
        build_panel_flujos(self.con)
        build_panel_deuda(self.con)
        build_panel_evidencia(self.con)

    def test_evidence_keeps_available_erp_dimension_without_cash(self):
        rows = [self.cash_tx(company='b', status='pending')]
        invoices = [self.invoice(operation_id=f'b-{n}', company_id='b', issuance_date='2024-09-01',
                                 due_date='2024-09-30', payment_date='2024-09-20') for n in range(5)]
        self.evidence_panels(rows, invoices)
        file = paths.MARTS_DIR / 'panel_evidencia.parquet'
        actual = self.con.execute(f"SELECT estado_operativo,estado_servicio_deuda,estado_cobro,dimensiones_publicables FROM '{file}' WHERE company_id='b' AND month=DATE '2024-11-01'").fetchone()
        self.assertEqual(actual, ('insuficiente', 'insuficiente', 'publicable', ['cobro']))

    def test_future_records_do_not_change_historical_feature_panels(self):
        rows = [self.cash_tx(amount, category, month+'-15')
                for month in ('2024-09', '2024-10', '2024-11', '2024-12')
                for amount,category in [(100,'collection'),(-90,'payment')]]
        invoices = [self.invoice(operation_id=f'a-{n}', issuance_date='2024-09-01',
                                 due_date='2024-09-30', payment_date='2024-09-20') for n in range(5)]
        self.evidence_panels(rows, invoices)

        def previous():
            return {name: self.con.execute(f"SELECT * FROM '{paths.MARTS_DIR / (name+'.parquet')}' WHERE month<=DATE '2024-12-01' ORDER BY company_id,month").fetchall()
                    for name in ('panel_flujos', 'panel_deuda', 'panel_evidencia')}

        before = previous()
        rows.append(self.cash_tx(20000000, 'payment', '2026-07-01', company='empty'))
        invoices.append(self.invoice(operation_id='future', company_id='empty', issuance_date='2026-06-01',
                                     due_date='2026-07-01', payment_date='2026-06-20'))
        self.evidence_panels(rows, invoices)
        self.assertEqual(before, previous())

    def targets_panels(self, rows, invoices=()):
        from xray.marts import observabilidad
        from xray.marts.targets import build_targets_proxy

        self.evidence_panels(rows, invoices)
        with patch.object(observabilidad, 'OUT_PATH', paths.MARTS_DIR / 'observabilidad.parquet'), \
             patch.object(observabilidad, 'REPORT_PATH', paths.REPORTS_DIR / 'observabilidad.json'):
            observabilidad.build_observabilidad(self.con)
        build_targets_proxy(self.con)

    def test_deficit_targets_respect_uncertainty_outliers_and_missing_months(self):
        rows = []
        for company in 'abcdef':
            for month in ('2024-09','2024-10','2024-11','2024-12'):
                rows += [self.cash_tx(100, 'collection', month+'-15', company),
                         self.cash_tx(-90, 'payment', month+'-15', company)]
            for month in ('2025-01','2025-02','2025-03'):
                if company=='e' and month=='2025-03':
                    continue
                negative = month!='2025-03' and company not in 'cf'
                rows += [self.cash_tx(50 if negative else 100, 'collection', month+'-15', company),
                         self.cash_tx(-100 if negative else -50, 'payment', month+'-15', company)]
                if company=='c' and month!='2025-03':
                    rows.append(self.cash_tx(-20000000, 'payment', month+'-15', company))
            if company=='b':
                rows.append(self.cash_tx(200, 'transfer', '2025-01-15', company))
            if company=='d':
                rows.append(self.cash_tx(-200, 'payment', '2025-03-15', company, product='usd'))
        rows.append(self.cash_tx(100, 'collection', '2026-08-15'))
        self.targets_panels(rows)
        file = paths.MARTS_DIR / 'targets_proxy.parquet'
        actual = dict(self.con.execute(f"SELECT company_id,target_deficit_3m FROM '{file}' WHERE month=DATE '2024-12-01'").fetchall())
        self.assertEqual(actual, {'a': True, 'b': None, 'c': None, 'd': True, 'e': None, 'f': False, 'empty': None})
        reasons = dict(self.con.execute(f"SELECT company_id,motivo_deficit_no_observable FROM '{file}' WHERE month=DATE '2024-12-01'").fetchall())
        self.assertEqual(reasons['b'], 'incertidumbre_material')
        self.assertEqual(reasons['c'], 'sensible_atipicos')
        self.assertEqual(reasons['e'], 'horizonte_sin_actividad_caja')
        self.assertEqual(self.con.execute(f"SELECT target_deficit_3m,motivo_deficit_no_observable FROM '{file}' WHERE company_id='a' AND month=DATE '2026-08-01'").fetchone(), (None, 'sin_horizonte'))
        from xray.marts.targets import targets_available_at
        self.assertEqual(targets_available_at(self.con, '2025-03-30').fetchall(), [])
        labels = targets_available_at(self.con, '2025-03-31').fetchall()
        self.assertEqual({row[0]: row[4] for row in labels}, {'a': 1, 'd': 1, 'f': 0})

    def test_collection_target_uses_mature_baseline_and_future_cohorts(self):
        rows = [self.cash_tx(100, 'collection', month+'-15', company)
                for company in ('a','b')
                for month in ('2024-09','2024-10','2024-11','2024-12','2025-01','2025-02','2025-03','2025-04')]
        invoices = []
        for company in ('a','b'):
            for month in ('2024-09','2024-10','2024-11','2025-02','2025-03','2025-04'):
                paid = (company=='a') == (month<'2025-01')
                invoices.append(self.invoice(operation_id=company+month, company_id=company,
                    issuance_date=month+'-01', due_date=month+'-28',
                    payment_date=month+'-15' if paid else None,
                    status='paid' if paid else 'pending', pending_amount=0 if paid else 100))
        self.targets_panels(rows, invoices)
        file = paths.MARTS_DIR / 'targets_proxy.parquet'
        actual = self.con.execute(f"SELECT company_id,deterioro_cobro_60d_pp,baseline_cohort_end,available_at_cobro FROM '{file}' WHERE month=DATE '2025-01-01' AND company_id IN ('a','b') ORDER BY company_id").fetchall()
        self.assertEqual([row[1] for row in actual], [100, -100])
        self.assertEqual(str(actual[0][2]), '2024-11-01')
        self.assertEqual(str(actual[0][3]), '2025-06-29')
        from xray.marts.targets import targets_available_at
        before = targets_available_at(self.con, '2025-06-28').fetchall()
        self.assertEqual([row for row in before if row[3]=='deterioro_cobro_60d_pp'], [])
        after = targets_available_at(self.con, '2025-06-29').fetchall()
        self.assertEqual(len([row for row in after if row[3]=='deterioro_cobro_60d_pp']), 2)

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
