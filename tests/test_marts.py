import unittest

import duckdb

from xray import paths


class MartContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = duckdb.connect(":memory:")
        cls.con.execute("SET threads=4")
        for name, path in {
            "panel": paths.MARTS_DIR / "panel_cobro.parquet",
            "obs": paths.MARTS_DIR / "observabilidad.parquet",
            "inv": paths.CLEAN_DIR / "invoices.parquet",
            "tx": paths.CLEAN_DIR / "transactions.parquet",
            "flows": paths.INTERIM_DIR / "tx_flow_class.parquet",
        }.items():
            cls.con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def assert_no_rows(self, sql):
        self.assertEqual(self.con.execute(sql).fetchone()[0], 0)

    def test_grain_and_flow_keys(self):
        for table in ('panel', 'obs'):
            self.assert_no_rows(f"SELECT count(*)-count(DISTINCT (company_id,month)) FROM {table}")
            self.assertEqual(self.con.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1286*24)
        self.assert_no_rows('SELECT count(*)-count(DISTINCT (_src_row,transaction_id)) FROM flows')
        self.assert_no_rows('''
            SELECT count(*) FROM tx FULL OUTER JOIN flows USING (_src_row,transaction_id)
            WHERE tx.transaction_id IS NULL OR flows.transaction_id IS NULL
        ''')

    def test_transferred_categories_have_past_evidence(self):
        self.assert_no_rows('''
            SELECT count(*) FROM tx JOIN flows USING (_src_row,transaction_id)
            WHERE flow_source='transferida' AND (evidence_month IS NULL OR evidence_month>=month)
        ''')

    def test_fx_registry_has_no_estimates(self):
        file = paths.MARTS_DIR / 'fx_rates.parquet'
        self.assert_no_rows(f"SELECT count(*) FROM '{file}' WHERE moneda<>'EUR' AND rate_por_eur IS NOT NULL")

    def test_observability_looks_forward(self):
        self.assert_no_rows("""
            WITH w AS (
                SELECT *, lead(tiene_actividad, 1, false) OVER z AS a1,
                          lead(tiene_actividad, 2, false) OVER z AS a2,
                          lead(tiene_actividad, 3, false) OVER z AS a3
                FROM obs WINDOW z AS (PARTITION BY company_id ORDER BY month)
            )
            SELECT count(*) FROM w
            WHERE objetivo_observable IS DISTINCT FROM
                (tiene_actividad AND horizonte_completo AND a1 AND a2 AND a3
                 AND NOT coalesce(es_calentamiento, false))
        """)

    def test_flow_direction(self):
        self.assert_no_rows("""
            SELECT count(*) FROM tx JOIN flows USING (_src_row, transaction_id)
            WHERE (flow_class LIKE '%_in' AND amount <= 0)
               OR (flow_class LIKE '%_out' AND amount >= 0)
        """)

    def test_native_eur_identity(self):
        self.assert_no_rows("""
            SELECT count(*) FROM inv WHERE upper(trim(currency)) = 'EUR'
            AND amount_eur IS DISTINCT FROM amount
        """)

    def test_ambiguous_fx_is_unknown(self):
        self.assert_no_rows("""
            SELECT count(*) FROM tx
            WHERE product_currency <> 'EUR' AND exchange_rate = 1
              AND amount_eur IS NOT NULL
        """)

    def test_cohorts_are_monotone(self):
        self.assert_no_rows("""
            SELECT count(*) FROM panel
            WHERE coh_pct_cobrado_30d > coh_pct_cobrado_60d + 1e-10
               OR coh_pct_cobrado_60d > coh_pct_cobrado_90d + 1e-10
        """)

    def test_percentages_have_valid_units(self):
        columns = [r[0] for r in self.con.execute("DESCRIBE panel").fetchall()]
        for col in columns:
            if '_pct' in col or col.endswith('_pct'):
                with self.subTest(column=col):
                    self.assert_no_rows(
                        f"SELECT count(*) FROM panel WHERE {col} < 0 OR {col} > 1 + 1e-10"
                    )

    def test_no_negative_dso(self):
        self.assert_no_rows("SELECT count(*) FROM panel WHERE ar_dso_dias < 0")

    def test_no_pre_observation_balances(self):
        self.assert_no_rows("""
            SELECT count(*) FROM panel WHERE meses_observados < 0
              AND (ar_abierto_eur IS NOT NULL OR ap_abierto_eur IS NOT NULL)
        """)

    def test_unknown_currency_is_not_zero_balance(self):
        self.assert_no_rows("""
            SELECT count(*) FROM panel
            WHERE (ar_n_abiertas > 0 AND ar_abierto_eur = 0)
               OR (ap_n_abiertas > 0 AND ap_abierto_eur = 0)
        """)

    def test_zero_overdue_has_zero_ratio(self):
        self.assert_no_rows("""
            SELECT count(*) FROM panel
            WHERE (ar_abierto_eur > 0 AND ar_vencido_eur = 0
                   AND ar_pct_vencido IS DISTINCT FROM 0)
               OR (ap_abierto_eur > 0 AND ap_vencido_eur = 0
                   AND ap_pct_vencido IS DISTINCT FROM 0)
        """)

    def test_cohort_availability_and_censoring(self):
        for horizon in (30, 60, 90):
            with self.subTest(horizon=horizon):
                self.assert_no_rows(f"""
                    SELECT count(*) FROM panel
                    WHERE coh_{horizon}d_available_at IS DISTINCT FROM
                          CAST(last_day(month) + INTERVAL {horizon} DAY AS DATE)
                       OR (coh_{horizon}d_available_at > DATE '2026-08-31'
                           AND coh_pct_cobrado_{horizon}d IS NOT NULL)
                """)

    def test_aging_reconciles(self):
        for side in ('ar', 'ap'):
            parts = '+'.join(f'{side}_aging_{band}_eur' for band in (
                '0_30', '31_60', '61_90', '91_180', '181_360', '360_mas'
            ))
            self.assert_no_rows(f"""
                SELECT count(*) FROM panel
                WHERE abs({side}_vencido_eur - ({parts})) > 0.01
            """)
