"""T4: tests de contrato sobre los parquet ya generados.

Comprueban INVARIANTES sobre data/interim y data/clean reales (no mocks).
Si alguno falla, el pipeline esta roto: no se cambia el test para que pase.
"""

import unittest

import duckdb

from xray import paths
from xray.flags import Flag

EXPECTED_ROWS = {
    "groups": 250,
    "companies": 1286,
    "banking_products": 5987,
    "debt_products": 2239,
    "debt_schedule_config": 87,
    "balances": 7996,
    "transactions": 2556437,
    "invoices": 897894,
}

TABLES = list(EXPECTED_ROWS)

# Columnas de importe originales a comparar entre interim y clean, por tabla.
AMOUNT_COLUMNS = {
    "transactions": ("amount",),
    "invoices": ("amount", "pending_amount"),
    "debt_products": ("granted", "outstanding", "liquidity"),
    "balances": ("balance",),
}


def _parquet(stage: str, table: str) -> str:
    folder = paths.CLEAN_DIR if stage == 'clean' else paths.INTERIM_DIR
    return str(folder / f'{table}.parquet')


def setUpModule():
    global CON
    CON = duckdb.connect(database=":memory:")
    CON.execute("SET threads=4")


def tearDownModule():
    CON.close()


class ContractTest(unittest.TestCase):
    def _clean_columns(self, table: str) -> dict[str, str]:
        rows = CON.execute(
            f"describe select * from read_parquet('{_parquet('clean', table)}')"
        ).fetchall()
        return {name: dtype for name, dtype, *_ in rows}

    def _interim_columns(self, table: str) -> dict[str, str]:
        rows = CON.execute(
            f"describe select * from read_parquet('{_parquet('interim', table)}')"
        ).fetchall()
        return {name: dtype for name, dtype, *_ in rows}

    def _scalar(self, sql: str):
        return CON.execute(sql).fetchone()[0]

    # 1 -------------------------------------------------------------------
    def test_row_counts(self):
        for table, expected in EXPECTED_ROWS.items():
            with self.subTest(table=table):
                clean = self._scalar(
                    f"select count(*) from read_parquet('{_parquet('clean', table)}')"
                )
                interim = self._scalar(
                    f"select count(*) from read_parquet('{_parquet('interim', table)}')"
                )
                self.assertEqual(
                    clean, expected, f"clean/{table} tiene {clean}, esperado {expected}"
                )
                self.assertEqual(
                    interim,
                    clean,
                    f"interim/{table} ({interim}) != clean/{table} ({clean})",
                )

    # 2 -------------------------------------------------------------------
    def test_src_row_integro(self):
        for table in TABLES:
            with self.subTest(table=table):
                n = self._scalar(
                    f"select count(*) from read_parquet('{_parquet('clean', table)}')"
                )
                nulls, dupes, bad_min, bad_max, distinct = CON.execute(
                    f"""
                    select
                        count(*) filter (where _src_row is null),
                        count(*) - count(distinct _src_row),
                        min(_src_row),
                        max(_src_row),
                        count(distinct _src_row)
                    from read_parquet('{_parquet('clean', table)}')
                    """
                ).fetchone()
                self.assertEqual(nulls, 0, f"{table}: _src_row con nulos")
                self.assertEqual(dupes, 0, f"{table}: _src_row duplicado")
                self.assertEqual(distinct, n, f"{table}: cardinalidad de _src_row != N")
                self.assertEqual(bad_min, 1, f"{table}: min(_src_row) != 1")
                self.assertEqual(bad_max, n, f"{table}: max(_src_row) != N")

    # 3 -------------------------------------------------------------------
    def test_importes_no_modificados(self):
        for table, cols in AMOUNT_COLUMNS.items():
            for col in cols:
                with self.subTest(table=table, column=col):
                    diff = self._scalar(
                        f"""
                        select count(*) from
                            read_parquet('{_parquet('interim', table)}') i
                            join read_parquet('{_parquet('clean', table)}') c
                              using (_src_row)
                        where i.{col} is distinct from c.{col}
                        """
                    )
                    self.assertEqual(
                        diff, 0, f"{table}.{col}: {diff} importes cambiados en limpieza"
                    )

    # 4 -------------------------------------------------------------------
    def test_vocabulario_de_flags_cerrado(self):
        valid = {f.value for f in Flag}
        for table in TABLES:
            with self.subTest(table=table):
                found = {
                    v
                    for (v,) in CON.execute(
                        f"""
                        select distinct unnest(quality_flags)
                        from read_parquet('{_parquet('clean', table)}')
                        """
                    ).fetchall()
                }
                invalid = found - valid
                self.assertEqual(
                    invalid,
                    set(),
                    f"{table}: flags fuera de vocabulario: {sorted(invalid)}",
                )

    # 5 -------------------------------------------------------------------
    def test_fechas_dentro_de_ventana(self):
        lo, hi = "1990-01-01", "2031-01-01"
        checked = 0
        for table in TABLES:
            cols = self._clean_columns(table)
            for name, dtype in cols.items():
                if not name.endswith("_ok") or dtype != "TIMESTAMP":
                    continue
                with self.subTest(table=table, column=name):
                    outside = self._scalar(
                        f"""
                        select count(*) from read_parquet('{_parquet('clean', table)}')
                        where {name} < timestamp '{lo}' or {name} >= timestamp '{hi}'
                        """
                    )
                    self.assertEqual(
                        outside,
                        0,
                        f"{table}.{name}: {outside} valores fuera de [{lo}, {hi})",
                    )
                    checked += 1
        self.assertGreater(checked, 0, "no se encontro ninguna columna *_ok TIMESTAMP")

    # 6 -------------------------------------------------------------------
    def test_centinelas_balances(self):
        bad = self._scalar(
            f"""
            select count(*) from read_parquet('{_parquet('clean', 'balances')}')
            where (balance_ok is null) <> ('sentinel_hidden_balance' = any(quality_flags))
            """
        )
        self.assertEqual(
            bad,
            0,
            f"balances: {bad} filas donde balance_ok NULL no coincide con"
            " sentinel_hidden_balance",
        )

    # 7 -------------------------------------------------------------------
    def test_fx_coherente(self):
        base = _parquet("clean", "transactions")
        mism = self._scalar(
            f"""
            select count(*) from read_parquet('{base}')
            where (amount_eur is null) <> ('fx_not_convertible' = any(quality_flags))
            """
        )
        self.assertEqual(
            mism,
            0,
            f"transactions: {mism} filas donde amount_eur NULL no coincide con"
            " fx_not_convertible",
        )
        bad = self._scalar(
            f"""
            select count(*) from read_parquet('{base}')
            where product_currency <> 'EUR'
              and exchange_rate > 0
              and amount_eur is not null
              and abs(amount_eur - amount / exchange_rate) >= 0.01
            """
        )
        self.assertEqual(
            bad,
            0,
            f"transactions: {bad} filas no-EUR donde amount_eur != amount / exchange_rate",
        )

    # 8 -------------------------------------------------------------------
    def test_original_fields_and_business_keys(self):
        from xray.schema import TABLES as specs

        for name, spec in specs.items():
            with self.subTest(table=name):
                keys = ','.join(spec.primary_key)
                self.assertEqual(self._scalar(
                    f"SELECT count(*)-count(DISTINCT ({keys})) FROM '{_parquet('clean', name)}'"
                ), 0)
                differences = ' OR '.join(f'i."{col.name}" IS DISTINCT FROM c."{col.name}"' for col in spec.columns)
                self.assertEqual(self._scalar(
                    f"SELECT count(*) FROM '{_parquet('interim', name)}' i "
                    f"FULL OUTER JOIN '{_parquet('clean', name)}' c USING (_src_row) "
                    f"WHERE i._src_row IS NULL OR c._src_row IS NULL OR ({differences})"
                ), 0)

    def test_product_source_valido(self):
        valid = {"banking", "debt", "orphan"}
        for table in ("transactions", "balances"):
            with self.subTest(table=table):
                found = {
                    v
                    for (v,) in CON.execute(
                        f"""
                        select distinct product_source
                        from read_parquet('{_parquet('clean', table)}')
                        """
                    ).fetchall()
                }
                self.assertEqual(
                    found - valid,
                    set(),
                    f"{table}: product_source con valores fuera de {valid}: {found}",
                )


if __name__ == "__main__":
    unittest.main()
