# Informe de calidad consolidado (T4)

Fuente: `data/clean/*.parquet` + `reports/quality/dimensions.json` + `reports/quality/facts.json`.

## Recuento de filas por tabla

| tabla | interim | clean | esperado | estado |
|---|---:|---:|---:|---|
| groups | 250 | 250 | 250 | OK |
| companies | 1286 | 1286 | 1286 | OK |
| banking_products | 5987 | 5987 | 5987 | OK |
| debt_products | 2239 | 2239 | 2239 | OK |
| debt_schedule_config | 87 | 87 | 87 | OK |
| balances | 7996 | 7996 | 7996 | OK |
| transactions | 2556437 | 2556437 | 2556437 | OK |
| invoices | 897894 | 897894 | 897894 | OK |

## Flags de calidad por tabla (ordenados por frecuencia)

### balances (7996 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `fk_orphan` | 29 | 0.36% |
| `sentinel_hidden_balance` | 6 | 0.08% |
### banking_products (5987 filas)

Sin flags.
### companies (1286 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `country_normalized` | 36 | 2.80% |
### debt_products (2239 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `amount_missing` | 169 | 7.55% |
### debt_schedule_config (87 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `fk_orphan` | 10 | 11.49% |
### groups (250 filas)

Sin flags.
### invoices (897894 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `fx_not_convertible` | 108445 | 12.08% |
| `date_order_invalid` | 34310 | 3.82% |
| `open_month` | 5233 | 0.58% |
| `fx_rate_invalid` | 3841 | 0.43% |
| `paid_with_pending` | 300 | 0.03% |
| `date_out_of_range` | 223 | 0.02% |
| `amount_extreme` | 10 | 0.00% |
### transactions (2556437 filas)

| flag | filas | % de la tabla |
|---|---:|---:|
| `category_missing` | 635530 | 24.86% |
| `fx_not_convertible` | 160207 | 6.27% |
| `status_missing` | 29839 | 1.17% |
| `open_month` | 9242 | 0.36% |
| `fk_orphan` | 1314 | 0.05% |
| `fx_rate_invalid` | 77 | 0.00% |
| `amount_extreme` | 24 | 0.00% |
| `date_out_of_range` | 8 | 0.00% |

## Cobertura cruzada de empresas

Empresas totales en `companies`: **1286**.

Empresas distintas que aparecen en cada fuente de actividad:

| fuente | empresas | % sobre el total |
|---|---:|---:|
| Transacciones | 1286 | 100.0 % |
| Cuentas bancarias (banking_products) | 1283 | 99.77 % |
| Saldos (balances) | 1273 | 98.99 % |
| Facturas (invoices) | 785 | 61.04 % |
| Deuda (debt_products) | 378 | 29.39 % |

Una empresa ausente en facturas o deuda **no significa cero facturacion o cero deuda**: significa que esa fuente no tiene registros para ella.

## Distribucion de meses de historia por empresa

Meses **cerrados** con transacciones por empresa (se excluye el mes abierto):

| estadistico | meses cerrados |
|---|---:|
| minimo | 1 |
| p25 | 10.0 |
| mediana | 18.0 |
| p75 | 24.0 |
| maximo | 24 |

- Empresas con historia: **1286**.
- Empresas con **menos de 6 meses cerrados**: **8**.

Rango de meses cerrados del dataset: **2024-09-01** a **2026-08-01**. Mes abierto: **2026-09-01**.

## Actividad por mes cerrado

Empresas con actividad y numero de transacciones por mes cerrado. Los bordes del periodo (primer y ultimo mes) pueden aparecer deprimidos si el extracto empezó o terminó a mitad de mes:

| mes | empresas con actividad | transacciones |
|---|---:|---:|
| 2024-09-01 | 439 | 41975 |
| 2024-10-01 | 470 | 49521 |
| 2024-11-01 | 476 | 44730 |
| 2024-12-01 | 520 | 48178 |
| 2025-01-01 | 648 | 61961 |
| 2025-02-01 | 684 | 61678 |
| 2025-03-01 | 717 | 71859 |
| 2025-04-01 | 742 | 77155 |
| 2025-05-01 | 762 | 79439 |
| 2025-06-01 | 777 | 83975 |
| 2025-07-01 | 829 | 97619 |
| 2025-08-01 | 842 | 85031 |
| 2025-09-01 | 890 | 101442 |
| 2025-10-01 | 933 | 110678 |
| 2025-11-01 | 960 | 99263 |
| 2025-12-01 | 1019 | 114764 |
| 2026-01-01 | 1153 | 130699 |
| 2026-02-01 | 1217 | 147951 |
| 2026-03-01 | 1223 | 182783 |
| 2026-04-01 | 1229 | 176180 |
| 2026-05-01 | 1216 | 166696 |
| 2026-06-01 | 1205 | 177661 |
| 2026-07-01 | 1199 | 185290 |
| 2026-08-01 | 1163 | 150667 |

## Huecos de datos en transacciones

- Transacciones **sin categoria** (`category_norm` NULL): **635860** filas (**24.87 %** del total).
- Transacciones **sin `amount_eur`** (no convertibles a EUR): **160207** filas (**6.27 %** del total).

## Avisos para la capa de features

Hechos que cualquier consumidor de estos datos DEBE conocer antes de usarlos:

- **2026-09-01 es un mes abierto y no comparable.** Solo contiene parte de la actividad (datos parciales); no debe mezclarse con los meses cerrados en agregaciones, ratios ni ventanas temporales.
- **Los saldos son un unico snapshot, no una serie historica.** Toda la tabla `balances` corresponde a una sola fecha de corte (2026-09-01); no hay evolucion mensual de saldos y por tanto no se pueden calcular tendencias de saldo.
- **Cobertura parcial de facturas y deuda.** Solo el **61.04 %** de las 1286 empresas tiene facturas y el **29.39 %** tiene deuda registrada. La ausencia de registros en esas tablas NO significa cero facturacion o cero deuda: significa que la fuente no aporta datos para esa empresa.
- **Los importes extremos estan marcados pero NO recortados.** Las filas con `amount_extreme` (y los centinelas de saldo) conservan su valor original; cualquier agregacion sensible a outliers debe tratarlos explicitamente.
- **FX conservador.** El nominal EUR se conserva por identidad. Una conversion reportada hacia EUR requiere tipo positivo, finito y distinto de 1 entre monedas diferentes. El resto queda desconocido, sin tipos estimados. Los agregados incompletos no deben presentarse como totales ni como ceros.
- **Un 24.87 % de las transacciones no tiene categoria** (`category_norm` NULL). Todo feature basado en categorías debe contemplar explícitamente el valor faltante.
- Hay filas con claves huerfanas (`fk_orphan`): empresas, productos o contrapartes que no resuelven contra las tablas maestras. Estan marcadas, no eliminadas.
