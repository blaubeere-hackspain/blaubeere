# Financial data inventory

- Status: `blocked_by_data_access`
- Input root: `data`
- Mode: read-only; Git LFS objects were not fetched.
- Provisional unit: `company × closed month × currency`

## Raw logical tables

| Table | Path | Status | Materialized | Rows | SHA-256 | LFS OID |
| --- | --- | --- | ---: | ---: | --- | --- |
| `balances` | `data/balances.csv` | `unavailable` | false | unknown | `—` | `86664c229cf52554d75d7efb864fc07a94a1bebcd61a3ac85abc344f3ba2a30c` |
| `banking_products` | `data/banking_products.csv` | `unavailable` | false | unknown | `—` | `0fc5aa20fea32981a962cc08251720ece3f2afad8495910111d2fd8b16514d3c` |
| `companies` | `data/companies.csv` | `unavailable` | false | unknown | `—` | `5496ad00a1e9228f7e756a55c71c512fd06a73ad45a09ce3f103a620a5ad72ae` |
| `debt_products` | `data/debt_products.csv` | `unavailable` | false | unknown | `—` | `bf38430ccd15f6a9ca8769113c5f47d4aeee8fe55c425687731481cd002f05d8` |
| `debt_schedule_config` | `data/debt_schedule_config.csv` | `unavailable` | false | unknown | `—` | `b0d6cc3e6810e31d526eb7f4be3c2abe31496f74f5ddd58163876d60de168ea2` |
| `groups` | `data/groups.csv` | `unavailable` | false | unknown | `—` | `fa18d3f42cd24d7640bff22d7a9504293574c2338ce85d425e3134f18f169a8c` |
| `invoices` | `data/invoices.csv` | `unavailable` | false | unknown | `—` | `6686bd878244881bac78348a108852f3f30713ef244267f1fa3754b9be619c53` |
| `transactions` | `data/transactions.csv` | `unavailable` | false | unknown | `—` | `000a6820a7500c66aa70a17b3e813d0c57a8270b3c58f6f2f006708a20228f2b` |

## Derived/clean artifacts

| Path | Status | Materialized | SHA-256 |
| --- | --- | ---: | --- |
| `data/clean/balances.parquet` | `unavailable` | false | `—` |
| `data/clean/banking_products.parquet` | `unavailable` | false | `—` |
| `data/clean/companies.parquet` | `unavailable` | false | `—` |
| `data/clean/debt_products.parquet` | `unavailable` | false | `—` |
| `data/clean/debt_schedule_config.parquet` | `unavailable` | false | `—` |
| `data/clean/groups.parquet` | `unavailable` | false | `—` |
| `data/clean/invoices.parquet` | `unavailable` | false | `—` |
| `data/clean/transactions.parquet` | `unavailable` | false | `—` |

## Auxiliary sources

| Path | Status |
| --- | --- |
| `data_dictionary.md` | `missing` |
| `dataset/output` | `missing` |

## Contract and unresolved semantics

- Contract version: `1.0.0`.
- Extraction date, availability date, and historical known_at timestamps are unavailable.
- Invoice, payment, due-date, status, FX, and balance semantics require owner confirmation.
- License and permitted use of financial source data require owner confirmation.

## Reproduction

```text
python3 scripts/audit_financial_data.py inventory --input-root data --output-dir reports/readiness
```
