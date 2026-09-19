"""Vocabulario de flags de calidad.

ESTE VOCABULARIO ES CERRADO. Anadir un flag nuevo es un cambio de contrato:
T2 y T3 consumen estos flags por nombre, asi que cualquier alta/modificacion
de miembros debe coordinarse con las tareas de limpieza y calidad.
"""

from enum import StrEnum


class Flag(StrEnum):
    CAST_FAILED = "cast_failed"
    DATE_OUT_OF_RANGE = "date_out_of_range"
    DATE_ORDER_INVALID = "date_order_invalid"
    SENTINEL_VALUE = "sentinel_value"
    FX_RATE_INVALID = "fx_rate_invalid"
    FX_NOT_CONVERTIBLE = "fx_not_convertible"
    AMOUNT_EXTREME = "amount_extreme"
    AMOUNT_MISSING = "amount_missing"
    CATEGORY_MISSING = "category_missing"
    STATUS_MISSING = "status_missing"
    COUNTRY_NORMALIZED = "country_normalized"
    COUNTRY_UNKNOWN = "country_unknown"
    FK_ORPHAN = "fk_orphan"
    PAID_WITH_PENDING = "paid_with_pending"
    OPEN_MONTH = "open_month"
    SENTINEL_HIDDEN_BALANCE = "sentinel_hidden_balance"
