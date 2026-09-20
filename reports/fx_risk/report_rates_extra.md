# F1c · Segunda fuente EUR/divisa para ARS, COP, CLP y PEN

Serie mensual EUR/divisa del **FMI (IFS, Exchange Rates, `XDC_EUR`)** vendorizada en `data/fx_extra/`, con disciplina point-in-time. Es la segunda fuente externa (la primera es el BCE, `data/fx_ecb/`). No valora facturas ni toca la nota: eso es F3.

## Que se ha traido

- **Fuente**: IMF.STA:ER(4.0.1), indicador `XDC_EUR`.
- **Convencion**: unidades de divisa por 1 EUR (igual que el BCE).
- **Fichero crudo**: `data/fx_extra/imf_er_xdc_eur_monthly.csv` (con `sha256` verificado).
- **Historia de la fuente**: 1999-M01 a 2026-M08 (`XDC_EUR`).
- **Parquet publicado**: la historia COMPLETA de la fuente (divisa x mes x tipo), para que la disciplina point-in-time funcione tambien en los primeros cortes. La columna `en_ventana` marca la rejilla del repo (2024-M09 a 2026-M08).

## Cobertura real en la rejilla (por divisa y por fecha)

| Divisa | ISO3 | Meses con dato (`periodo_medio`) | Meses con dato (`fin_de_periodo`) | Rango `periodo_medio` | Meses ausentes |
| --- | --- | --- | --- | --- | --- |
| ARS | ARG | 24/24 | 24/24 | 1,063.2381 – 1,733.6604 | ninguno |
| CLP | CHL | 24/24 | 24/24 | 997.0166 – 1,126.4216 | ninguno |
| COP | COL | 23/24 | 23/24 | 3,735.8103 – 4,792.7077 | 2026-M08 |
| PEN | PER | 24/24 | 24/24 | 3.8526 – 4.1870 | ninguno |

### Cobertura point-in-time (lo que F3 puede usar sin fuga)

- available_at modelado como el ultimo instante del mes m+LAG. LAG=2 medido en los vintages ER_2026_JAN_VINTAGE (pub 2026-02-01, trae hasta 2025-M12) y ER_2026_APR_VINTAGE (pub 2026-04-27, trae 2026-M02 PA_RT / 2026-M03 EOP_RT), y LAG=4 en COP porque el vintage de abril de 2026 seguia anclado en 2025-M12 para Colombia. Nunca una fila se declara disponible antes de ese instante.

| Corte (cierre de mes) | ARS PA | CLP PA | COP PA | PEN PA |
| --- | --- | --- | --- | --- |
| 2024-M09 | 2024-M07 | 2024-M07 | 2024-M05 | 2024-M07 |
| 2024-M10 | 2024-M08 | 2024-M08 | 2024-M06 | 2024-M08 |
| 2024-M11 | 2024-M09 | 2024-M09 | 2024-M07 | 2024-M09 |
| 2024-M12 | 2024-M10 | 2024-M10 | 2024-M08 | 2024-M10 |
| 2025-M01 | 2024-M11 | 2024-M11 | 2024-M09 | 2024-M11 |
| 2025-M02 | 2024-M12 | 2024-M12 | 2024-M10 | 2024-M12 |
| 2025-M03 | 2025-M01 | 2025-M01 | 2024-M11 | 2025-M01 |
| 2025-M04 | 2025-M02 | 2025-M02 | 2024-M12 | 2025-M02 |
| 2025-M05 | 2025-M03 | 2025-M03 | 2025-M01 | 2025-M03 |
| 2025-M06 | 2025-M04 | 2025-M04 | 2025-M02 | 2025-M04 |
| 2025-M07 | 2025-M05 | 2025-M05 | 2025-M03 | 2025-M05 |
| 2025-M08 | 2025-M06 | 2025-M06 | 2025-M04 | 2025-M06 |
| 2025-M09 | 2025-M07 | 2025-M07 | 2025-M05 | 2025-M07 |
| 2025-M10 | 2025-M08 | 2025-M08 | 2025-M06 | 2025-M08 |
| 2025-M11 | 2025-M09 | 2025-M09 | 2025-M07 | 2025-M09 |
| 2025-M12 | 2025-M10 | 2025-M10 | 2025-M08 | 2025-M10 |
| 2026-M01 | 2025-M11 | 2025-M11 | 2025-M09 | 2025-M11 |
| 2026-M02 | 2025-M12 | 2025-M12 | 2025-M10 | 2025-M12 |
| 2026-M03 | 2026-M01 | 2026-M01 | 2025-M11 | 2026-M01 |
| 2026-M04 | 2026-M02 | 2026-M02 | 2025-M12 | 2026-M02 |
| 2026-M05 | 2026-M03 | 2026-M03 | 2026-M01 | 2026-M03 |
| 2026-M06 | 2026-M04 | 2026-M04 | 2026-M02 | 2026-M04 |
| 2026-M07 | 2026-M05 | 2026-M05 | 2026-M03 | 2026-M05 |
| 2026-M08 | 2026-M06 | 2026-M06 | 2026-M04 | 2026-M06 |

**Al ultimo corte util (2026-M08)**, el dato `periodo_medio` mas reciente utilizable es: ARS=2026-M06, CLP=2026-M06, COP=2026-M04, PEN=2026-M06.

## Lectura honesta

- La fuente es **mensual**, no diaria. Valorar una factura con fecha `d` con el promedio (o el cierre) de su mes `m` es una aproximacion declarada, no el tipo de `d`.
- El **retardo de publicacion** es real (~2 meses, 4 en COP). No se finge disponibilidad en el propio mes: `available_at` lo modela con evidencia de vintages del propio FMI.
- Un mes sin dato queda a **nulo**; no se interpola ni se arrastra el mes anterior. Nulo nunca es cero.
- No se usan medianas internas de `exchange_rate` ni ninguna otra fuente interna para estimar tipos.

## Test ancla de plausibilidad

Contra la evidencia interna de `clean/invoices.parquet` con `accounting_currency_norm = 'EUR'` (el unico `exchange_rate` que NO es una identidad), los ordenes de magnitud cuadran. Medido: 23 filas en total (9 CLP, 11 COP, 3 PEN, **0 ARS**; el dispatch hablaba de 10 facturas, 5/2/3, que no se reproduce).

| Divisa | Evidencia interna (por EUR) | Banda de plausibilidad adoptada |
| --- | --- | --- |
| ARS | 0 filas (no hay ancla interna; el dispatch ya lo anticipaba) | 900 – 4000 |
| CLP | 9 filas, ≈ 1.024 – 1.110 | 900 – 1300 |
| COP | 11 filas, ≈ 4.244 – 4.678 (una fila degenerada de 0,50 EUR con tipo 50 se excluye) | 3500 – 5200 |
| PEN | 3 filas, ≈ 3,90 – 3,96 | 3.3 – 4.5 |

Los tests de `tests/test_fx_rates_extra.py` comprueban ademas que `XDC_EUR / XDC_USD` reproduce un EUR/USD de mercado (1,03-1,19 en la ventana) y que la serie no esta invertida.

## Fuentes evaluadas y por que

Se han evaluado seis familias de fuentes antes de elegir. Criterio: los cinco requisitos duros del dispatch (URL estable y auditable, licencia clara, cobertura 2024-09..2026-08, anclaje a EUR sin ambiguedad, y no inventar ni interpolar tipos).

| Fuente | Divisa(s) | Frecuencia | Veredicto | Motivo |
| --- | --- | --- | --- | --- |
| **FMI · IFS Exchange Rates (`XDC_EUR`)** | ARS, COP, CLP, PEN | mensual | **ELEGIDA** | Una sola URL y licencia `PUBLIC_OPEN`; serie **directamente EUR/divisa** (sin cadena); historia 1999-M01..2026-M08; vintages con fecha de publicacion real; ordenes de magnitud validados contra la evidencia interna. |
| BCRA (AR) `api.bcra.gob.ar` | ARS (+otras, via ARS) | diaria | descartada | Solo resuelve una fecha por peticion (`?fecha=`); `fechadesde`/`fechahasta` devuelven 400. Cubrir la ventana exigiria ~500 descargas y no daria las otras tres divisas. |
| BCCh (CL) `si3.bcentral.cl` | CLP | diaria | descartada | La API de la Base de Datos Estadisticos exige usuario y **token**: no es reproducible sin credenciales (falla el requisito de auditabilidad). |
| BanRep vía `datos.gov.co` (CO) | COP | diaria | descartada (buena) | TRM diaria abierta y con licencia CC-BY-SA; se descarta por simplicidad: obligaria a mezclar cuatro fuentes y cuatro licencias. |
| BCRP (PE) `estadisticas.bcrp.gob.pe` | PEN | diaria | descartada (buena) | API publica que funciona (USD/PEN); mismo argumento: una divisa y una licencia por fuente. |
| FRED (St. Louis Fed) | CLP (solo) | mensual | descartada | Solo existe `CCUSMA02CLM618N`; COL/PER/ARG dan 404 y las series diarias del H.10 para estas divisas estan retiradas. |
| Agregadores (exchangerate.host, mindicador.cl, ...) | varias | diaria | descartados | Sin licencia clara ni garantia de historia; incumplen el requisito 1. |

## Verificacion de las cifras del dispatch (universo de empresas)

El dispatch afirma que ARS/COP/CLP/PEN afectan a 20 empresas de la cartera viva de cobros, 4 solo-opacas y 16 con mezcla. **No se reproduce con el universo de F2** (`flow_side='inflow'`, `document_type_norm='invoice'`, sin `cancel`, emitida hasta el cierre y no pagada al cierre):

| Definicion | Empresas | Solo opacas | Con mezcla |
| --- | --- | --- | --- |
| F2 point-in-time, ultimo corte 2026-08 | **13** | 4 | 9 |
| F2 point-in-time, alguna vez en la ventana | **13** | 7 | 10 |
| Facturas inflow sin PIT (toda la historia) | **16** | 2 | 14 |
| Dispatch (referencia) | 20 | 4 | 16 |

El unico numero que cuadra exactamente es el de **4 empresas solo-opacas** en el ultimo corte. Las cifras del dispatch parecen una cota superior (un universo mas ancho que el de F2). No bloquea F1c: la fuente sirve igual; la diferencia se reporta para que F3 no dimensione el impacto con el 20.
