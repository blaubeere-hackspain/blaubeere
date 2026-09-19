#!/usr/bin/env bash
# Verifica que la ingesta no perdio ninguna fila:
# registros de datos del CSV == filas del parquet interim.
#
# NOTA: no se usa `wc -l - 1` porque transactions.csv e invoices.csv tienen
# campos entrecomillados con saltos de linea embebidos (comprobado: 671.589
# lineas con comillas en transactions.csv). wc -l cuenta lineas fisicas, no
# registros CSV; el contador independiente es el modulo csv de Python,
# que NO es el mismo lector que usa la ingesta (DuckDB).
set -euo pipefail
cd "$(dirname "$0")/.."

TABLES="groups companies banking_products debt_products debt_schedule_config transactions invoices balances"

fail=0
printf "%-24s %12s %12s %s\n" "tabla" "csv_rows" "parquet_rows" "estado"
for t in $TABLES; do
  csv_rows=$(.venv/bin/python -B -c "
import csv
from xray import paths
with open(paths.RAW_DIR / '${t}.csv', newline='') as f:
    print(sum(1 for _ in csv.reader(f)) - 1)
")
  parquet_rows=$(.venv/bin/python -B -c "
import duckdb
from xray import paths
con = duckdb.connect()
print(con.execute(\"SELECT count(*) FROM read_parquet(?)\", [str(paths.INTERIM_DIR / '${t}.parquet')]).fetchone()[0])
")
  if [ "$csv_rows" -eq "$parquet_rows" ]; then
    printf "%-24s %12s %12s OK\n" "$t" "$csv_rows" "$parquet_rows"
  else
    printf "%-24s %12s %12s MISMATCH\n" "$t" "$csv_rows" "$parquet_rows"
    fail=1
  fi
done

exit $fail
