"""Contrato de esquema de las 8 tablas del dataset.

Los tipos son los tipos DuckDB destino. La ingesta los aplica de forma
tolerante con TRY_CAST; cualquier regla de negocio vive en xray.clean.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str  # tipo DuckDB destino: "VARCHAR","BIGINT","DOUBLE","TIMESTAMP"
    role: str  # "key" | "fk" | "date" | "amount" | "rate" | "cat" | "text"


@dataclass(frozen=True)
class TableSpec:
    name: str
    filename: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnSpec, ...]


TABLES: dict[str, TableSpec] = {
    "groups": TableSpec(
        name="groups",
        filename="groups.csv",
        primary_key=("group_id",),
        columns=(
            ColumnSpec("group_id", "VARCHAR", "key"),
            ColumnSpec("erp", "VARCHAR", "cat"),
            ColumnSpec("n_companies_in_sample", "BIGINT", "amount"),
        ),
    ),
    "companies": TableSpec(
        name="companies",
        filename="companies.csv",
        primary_key=("company_id",),
        columns=(
            ColumnSpec("company_id", "VARCHAR", "key"),
            ColumnSpec("group_id", "VARCHAR", "fk"),
            ColumnSpec("country", "VARCHAR", "cat"),
            ColumnSpec("currency", "VARCHAR", "cat"),
            ColumnSpec("erp", "VARCHAR", "cat"),
            ColumnSpec("created_at", "TIMESTAMP", "date"),
        ),
    ),
    "banking_products": TableSpec(
        name="banking_products",
        filename="banking_products.csv",
        primary_key=("product_id",),
        columns=(
            ColumnSpec("product_id", "VARCHAR", "key"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("label", "VARCHAR", "text"),
            ColumnSpec("type", "VARCHAR", "cat"),
            ColumnSpec("bank_name", "VARCHAR", "cat"),
            ColumnSpec("service", "VARCHAR", "cat"),
            ColumnSpec("currency", "VARCHAR", "cat"),
            ColumnSpec("created_at", "TIMESTAMP", "date"),
        ),
    ),
    "debt_products": TableSpec(
        name="debt_products",
        filename="debt_products.csv",
        primary_key=("product_id",),
        columns=(
            ColumnSpec("product_id", "VARCHAR", "key"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("label", "VARCHAR", "text"),
            ColumnSpec("type", "VARCHAR", "cat"),
            ColumnSpec("bank_name", "VARCHAR", "cat"),
            ColumnSpec("service", "VARCHAR", "cat"),
            ColumnSpec("currency", "VARCHAR", "cat"),
            ColumnSpec("created_at", "TIMESTAMP", "date"),
            ColumnSpec("granted", "DOUBLE", "amount"),
            ColumnSpec("outstanding", "DOUBLE", "amount"),
            ColumnSpec("liquidity", "DOUBLE", "amount"),
        ),
    ),
    "debt_schedule_config": TableSpec(
        name="debt_schedule_config",
        filename="debt_schedule_config.csv",
        primary_key=("product_id",),
        columns=(
            ColumnSpec("product_id", "VARCHAR", "key"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("settlement_product_id", "VARCHAR", "fk"),
            ColumnSpec("currency", "VARCHAR", "cat"),
            ColumnSpec("amortization_type", "VARCHAR", "cat"),
            ColumnSpec("interest_calc_method", "VARCHAR", "cat"),
            ColumnSpec("amortising_frequency", "VARCHAR", "cat"),
            ColumnSpec("granted_balance", "DOUBLE", "amount"),
            ColumnSpec("outstanding_balance", "DOUBLE", "amount"),
            ColumnSpec("total_periods", "BIGINT", "amount"),
            ColumnSpec("next_payment_date", "TIMESTAMP", "date"),
            ColumnSpec("last_payment_date", "TIMESTAMP", "date"),
            ColumnSpec("annual_interest_rate_or_spread", "DOUBLE", "rate"),
            ColumnSpec("interest_type", "VARCHAR", "cat"),
        ),
    ),
    "transactions": TableSpec(
        name="transactions",
        filename="transactions.csv",
        primary_key=("transaction_id",),
        columns=(
            ColumnSpec("transaction_id", "VARCHAR", "key"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("product_id", "VARCHAR", "fk"),
            ColumnSpec("date", "TIMESTAMP", "date"),
            ColumnSpec("value_date", "TIMESTAMP", "date"),
            ColumnSpec("amount", "DOUBLE", "amount"),
            ColumnSpec("exchange_rate", "DOUBLE", "rate"),
            ColumnSpec("status", "VARCHAR", "cat"),
            ColumnSpec("accounting_status", "VARCHAR", "cat"),
            ColumnSpec("category", "VARCHAR", "cat"),
            ColumnSpec("description", "VARCHAR", "text"),
            ColumnSpec("counterparty_id", "VARCHAR", "fk"),
        ),
    ),
    "invoices": TableSpec(
        name="invoices",
        filename="invoices.csv",
        primary_key=("operation_id",),
        columns=(
            ColumnSpec("operation_id", "VARCHAR", "key"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("document_type", "VARCHAR", "cat"),
            ColumnSpec("issuance_date", "TIMESTAMP", "date"),
            ColumnSpec("due_date", "TIMESTAMP", "date"),
            ColumnSpec("payment_date", "TIMESTAMP", "date"),
            ColumnSpec("amount", "DOUBLE", "amount"),
            ColumnSpec("pending_amount", "DOUBLE", "amount"),
            ColumnSpec("currency", "VARCHAR", "cat"),
            ColumnSpec("accounting_currency", "VARCHAR", "cat"),
            ColumnSpec("exchange_rate", "DOUBLE", "rate"),
            ColumnSpec("status", "VARCHAR", "cat"),
            ColumnSpec("concept", "VARCHAR", "text"),
            ColumnSpec("counterparty_id", "VARCHAR", "fk"),
        ),
    ),
    "balances": TableSpec(
        name="balances",
        filename="balances.csv",
        primary_key=("product_id", "date"),
        columns=(
            ColumnSpec("product_id", "VARCHAR", "fk"),
            ColumnSpec("company_id", "VARCHAR", "fk"),
            ColumnSpec("date", "TIMESTAMP", "date"),
            ColumnSpec("balance", "DOUBLE", "amount"),
            ColumnSpec("available", "DOUBLE", "amount"),
            ColumnSpec("granted", "DOUBLE", "amount"),
            ColumnSpec("liquidity", "DOUBLE", "amount"),
            ColumnSpec("countable", "DOUBLE", "amount"),
        ),
    ),
}
