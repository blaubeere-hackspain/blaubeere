from datetime import date, timedelta

FIRST_MONTH = date(2024, 9, 1)
OPEN_MONTH = date(2026, 9, 1)
DATASET_END = OPEN_MONTH - timedelta(days=1)
LAST_MONTH = DATASET_END.replace(day=1)
MONTHS_SQL = (
    f"generate_series(DATE '{FIRST_MONTH}', DATE '{LAST_MONTH}', INTERVAL 1 MONTH)"
)
