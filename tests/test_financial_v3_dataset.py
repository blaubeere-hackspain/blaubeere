import copy
import unittest

from scripts.financial_v3_dataset import (
    MONTHS, aging_exposure, reverse_account, growth, invoice_documents,
    scoped_score, feature_row, historical_view, flow_point, invoice_point,
)


def flow(receipts=100.0, payments=80.0):
    return {'tiene_actividad_caja': True, 'n_tx_caja': 2, 'n_sin_eur': 0, 'n_ambiguos': 0,
            'cobertura_eur_pct': 1.0, 'cobertura_clasificacion_pct': 1.0, 'n_cuentas_activas': 1,
            'cobros_operativos_eur': receipts, 'cobros_operativos_conocido_eur': receipts,
            'pagos_operativos_conocido_eur': payments, 'pagos_operativos_eur': payments,
            'neto_caja_conocido_eur': receipts-payments, 'neto_caja_eur': receipts-payments,
            'volumen_caja_conocido_eur': receipts+payments,
            'operativo_min_eur': receipts-payments, 'operativo_max_eur': receipts-payments}


def anchor():
    return {'product_id': 'a', 'company_id': 'c', 'currency': 'EUR', 'anchor_currency': 'EUR',
            'anchor_company': 'c', 'balance': 1000.0, 'anchor_date': '2026-09-01',
            'before_anchor': 300.0, 'through_anchor': 320.0, 'bad_rows': 0}


class DatasetTests(unittest.TestCase):
    def test_signed_cash_and_two_cutoffs(self):
        rows = {'2026-07-01': {'net': 150.0, 'n': 2}, '2026-08-01': {'net': 150.0, 'n': 2}}
        result = reverse_account(anchor(), rows, '2026-08-01')
        self.assertEqual(result['closing_scenarios'], [1000.0, 980.0])
        self.assertEqual(result['opening_scenarios'], [850.0, 830.0])
        self.assertEqual(result['closing_range'], [980.0, 1000.0])
        self.assertIsNone(result['available_balance'])
        self.assertFalse(result['verified'])

    def test_mismatched_cash_currency(self):
        a = anchor()
        a['anchor_currency'] = 'USD'
        self.assertEqual(reverse_account(a, {}, MONTHS[-1])['reason'], 'anchor_perimeter_mismatch')

    def test_cash_ownership(self):
        a = anchor()
        a['anchor_company'] = 'other'
        self.assertIsNone(reverse_account(a, {}, MONTHS[-1])['closing_range'])

    def test_cash_gaps_not_verified_or_zero_filled(self):
        rows = {'2026-06-01': {'net': 20.0, 'n': 1}, '2026-08-01': {'net': 50.0, 'n': 1}}
        result = reverse_account(anchor(), rows, '2026-07-01')
        self.assertGreater(result['gap_months'], 0)
        self.assertFalse(result['verified'])
        self.assertIsNone(reverse_account(anchor(), rows, '2024-09-01')['closing_range'])

    def test_mora_1_7_vs_8_30_interval(self):
        self.assertEqual(aging_exposure([100, 0, 0, 0, 0, 0]), [100.0, 125.0])
        self.assertEqual(aging_exposure([0, 100, 0, 0, 0, 0]), [150.0, 150.0])
        self.assertEqual(aging_exposure([0, 0, 0, 0, 0, 100]), [400.0, 400.0])
        self.assertIsNone(aging_exposure([None, 0, 0, 0, 0, 0]))

    def test_growth_exact_calendar_and_denominator(self):
        rows = {m: flow(100 if i < 3 else 150) for i, m in enumerate(MONTHS[:6])}
        g = growth(rows, MONTHS[5], 'three_vs_three', 'cobros_operativos_eur')
        self.assertEqual(g['value'], 0.5)
        self.assertEqual(g['denominator'], 300)
        self.assertEqual(g['prior'], MONTHS[:3])
        self.assertEqual(g['current'], MONTHS[3:6])
        del rows[MONTHS[1]]
        self.assertIsNone(growth(rows, MONTHS[5], 'three_vs_three', 'cobros_operativos_eur')['value'])

    def test_growth_zero_and_future_taint(self):
        rows = {MONTHS[0]: flow(0), MONTHS[1]: flow(10), MONTHS[2]: flow(999999)}
        self.assertEqual(growth(rows, MONTHS[1], 'mom', 'cobros_operativos_eur')['reason'], 'zero_reference')
        self.assertIsNone(growth(rows, MONTHS[1], 'yoy', 'cobros_operativos_eur')['value'])

    def test_partial_and_notpaid_dates_not_allocations(self):
        inv = {'operation_id': 'i', 'document_type_norm': 'invoice', 'amount': -100.0,
               'amount_eur': -100.0, 'issuance_date_ok': '2025-01-01', 'maturity_date_ok': '2025-01-10',
               'status_norm': 'notpaid', 'pending_amount': -20.0, 'payment_date_ok': '2025-01-02'}
        result = invoice_documents([inv], '2025-01-01')
        self.assertEqual(result['ap_due_face'], 100.0)
        inv['status_norm'] = 'paid'
        self.assertEqual(invoice_documents([inv], '2025-01-01'), result)
        inv['status_norm'] = 'cancel'
        self.assertEqual(invoice_documents([inv], '2025-01-01'), result)

    def test_duplicate_invoice_rejected(self):
        inv = {'operation_id': 'i'}
        with self.assertRaises(ValueError):
            invoice_documents([inv, inv], '2025-01-01')

    def test_future_invoice_not_in_due_proxy(self):
        inv = {'operation_id': 'i', 'document_type_norm': 'invoice', 'amount': -100.0,
               'amount_eur': -100.0, 'issuance_date_ok': '2025-02-01', 'maturity_date_ok': '2025-01-10'}
        self.assertIsNone(invoice_documents([inv], '2025-01-01')['ap_due_face'])

    def test_no_data_not_zero(self):
        self.assertIsNone(flow_point({})['net_observed'])
        self.assertIsNone(invoice_point({})['ap']['pending'])
        self.assertIsNone(invoice_documents([], '2025-01-01')['ap_due_face'])
        s = scoped_score({}, {}, MONTHS[5], None)
        self.assertEqual(s['operating_scope_interval'], [0, 100])
        self.assertIsNone(s['headline'])

    def test_nonpayment_cannot_improve_operating_scope(self):
        paid = {m: flow() for m in MONTHS[:6]}
        unpaid = {m: flow(payments=0) for m in MONTHS[:6]}
        due = {m: {'ap_due_face': 80.0} for m in MONTHS[:6]}
        a = scoped_score(paid, due, MONTHS[5], [0, 0])
        b = scoped_score(unpaid, due, MONTHS[5], [80, 100])
        self.assertEqual(a['components'], b['components'])
        self.assertLessEqual(b['operating_scope_interval'][1], a['operating_scope_interval'][1])
        self.assertEqual(a['components']['generation'], 70.0)
        self.assertEqual(a['full_company_interval'], [0, 100])

    def test_missing_mora_not_optimistic(self):
        rows = {m: flow() for m in MONTHS[:6]}
        due = {m: {'ap_due_face': 80.0} for m in MONTHS[:6]}
        a = scoped_score(rows, due, MONTHS[5], None)
        b = scoped_score(rows, due, MONTHS[5], [0, 0])
        self.assertLess(a['operating_scope_interval'][0], b['operating_scope_interval'][0])
        self.assertIsNone(a['operating_scope_score'])

    def test_feature_eligibility_separate_from_headline(self):
        rows = {m: flow() for m in MONTHS[:6]}
        f = feature_row('c', 'g', 'train', MONTHS[5], rows, {}, {})
        self.assertTrue(f['eligible'])
        self.assertFalse(f['strict_fullscore_eligible'])
        self.assertNotIn('score', f['values'])
        self.assertNotIn('cash', f['values'])
        self.assertIsNone(feature_row('c', 'g', 'final_test', MONTHS[5], rows, {}, {}))

    def test_feature_gap_and_fx(self):
        rows = {m: flow() for m in MONTHS[:6]}
        rows[MONTHS[4]]['n_sin_eur'] = 1
        self.assertFalse(feature_row('c', 'g', 'train', MONTHS[5], rows, {}, {})['eligible'])
        del rows[MONTHS[4]]
        self.assertFalse(feature_row('c', 'g', 'train', MONTHS[5], rows, {}, {})['eligible'])

    def test_same_count_different_accounts_flagged(self):
        rows = {MONTHS[0]: {**flow(), '_perimeter': ['a']}, MONTHS[1]: {**flow(), '_perimeter': ['b']}}
        self.assertTrue(growth(rows, MONTHS[1], 'mom', 'cobros_operativos_eur')['active_perimeter_changed'])

    def test_invoice_sql_matches_independent_fixture(self):
        import duckdb
        from scripts.financial_v3_dataset import invoice_sql
        con = duckdb.connect(':memory:')
        source = "(SELECT * FROM (VALUES ('a','c',-100.0,-100.0,DATE '2025-01-01',DATE '2025-01-20','invoice'), ('b','c',50.0,50.0,DATE '2025-01-02',DATE '2025-02-01','invoice'), ('f','c',-999.0,-999.0,DATE '2025-02-01',DATE '2025-01-10','invoice')) t(operation_id,company_id,amount,amount_eur,issuance_date_ok,maturity_date_ok,document_type_norm))"
        try:
            cur = con.execute(invoice_sql(source))
            rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
        finally:
            con.close()
        row = next(r for r in rows if str(r['month']) == '2025-01-01')
        self.assertEqual(row['ap_due_face'], 100)
        self.assertEqual(row['ar_issued_known'], 50)
        self.assertEqual(row['issued_rows'], 2)

    def test_no_data_company_wire_and_changes(self):
        from scripts.financial_v3_dataset import build_company, validate_wire
        profile = build_company({'company_id': 'c', 'group_id': 'g', 'currency_norm': 'EUR'}, {}, {}, {}, {}, {}, [], [], 'train', 'a'*64)
        validate_wire(profile)
        self.assertEqual(len(profile['financial_v3']['points']), 24)
        self.assertEqual(profile['financial_v3']['points'][1]['change']['reason'], 'missing_components_or_changed_reference_not_causal')

    def test_real_json_schema_has_nullable_money(self):
        from scripts.financial_v3_dataset import schema
        s = schema()
        self.assertIn('$schema', s)
        self.assertEqual(s['properties']['health']['type'], 'null')
        self.assertFalse(s['additionalProperties'])

    def test_strict_historical_api_fails_closed(self):
        with self.assertRaises(ValueError):
            historical_view({'financial_v3': {'points': []}}, '2025-01-31', strict=True)

    def test_retrospective_selection_strips_future_snapshot(self):
        profile = {'financial_v3': {'meta': {'snapshot_context': {'x': 1}}, 'points': [
            {'month': '2025-01-01', 'as_of': '2025-01-31'}, {'month': '2026-08-01', 'as_of': '2026-08-31'}]}}
        view = historical_view(profile, '2025-01-31')
        self.assertEqual(len(view['financial_v3']['points']), 1)
        self.assertNotIn('snapshot_context', view['financial_v3']['meta'])
        self.assertIn('snapshot_context', profile['financial_v3']['meta'])


if __name__ == '__main__':
    unittest.main()
