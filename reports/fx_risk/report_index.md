# Indice de inestabilidad FX por divisa-mes (F1)

Fuente externa vendorizada: tipos de referencia diarios del BCE.

- URL: `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip`
- sha256: `22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d`
- Fichero: `data/fx_ecb/eurofxref-hist.csv`
- Ventana: 2024-09-01 a 2026-08-01 (24 meses)
- Filas de rejilla: 936 (39 divisas)

## Columnas del parquet (para F2/F3)

`fx_index.parquet` publica los dos ingredientes por separado. ATENCION: `tramo` pasa a ser el tramo COMBINADO; el tramo de solo-volatilidad de F1 se conserva en `tramo_volatilidad`.

| Columna | Significado |
| --- | --- |
| `vol_anualizada` | Volatilidad robusta anualizada (componente 1). |
| `tramo_volatilidad` | Tramo del componente 1 (el `tramo` de F1). |
| `deriva_ventana_meses` | Ventana movil de la deriva (12). |
| `deriva_valor` | Variacion firmada del valor frente al EUR (>0 aprecia). |
| `deriva_componente` | `max(0, -deriva_valor)`: solo erosion. |
| `tramo_deriva` | Tramo del componente 2. |
| `tramo` | Combinado: el mas severo de los dos. |

## Componente 1: volatilidad robusta anualizada

(IQR(rendimientos_log_diarios) / 1.349) * sqrt(252)

Escala robusta: un unico dia extremo (devaluacion puntual) no domina el mes; expresada en terminos anualizados para ser comparable entre divisas.

## Componente 2: deriva sostenida (asimetrica)

deriva_valor = tipo(cierre m-W) / tipo(cierre m) - 1; deriva_componente = max(0, -deriva_valor)

Ventana: 12 meses moviles.

El BCE cotiza unidades de divisa por EUR: si el tipo SUBE la divisa se DEBILITA. El cociente invierte el signo, de modo que deriva_valor < 0 = perdida para quien va a cobrar en esa divisa.

Solo cuenta la deriva negativa. Una revalorizacion da componente 0 (ni premia ni castiga).

12 meses maximiza la separacion medida (Cohen d ~1.0) entre el bloque estructuralmente debil (TRY, IDR, INR, JPY) y el apreciado (USD, GBP, BRL, MXN); a 18/24 meses la mediana del bloque apreciado se vuelve negativa (castigaria a BRL) y a 3/6 meses TRY solo es el mas castigado en 16/24 meses. El CSV llega a 1999-01-04, asi que no hay calentamiento.

## Combinacion de los dos componentes

Regla: **mas_severo_de_los_dos**. Una divisa es tan arriesgada como su peor dimension; un componente benigno no compensa al otro. Se publican los dos tramos por separado para que F3 mida cual mueve la nota.

## Tramos

- `vol_estable`: vol < 0.025
- `vol_volatil`: 0.025 <= vol < 0.1
- `vol_hipervolatil`: vol >= 0.1 o sin cobertura BCE
- `deriva_estable`: componente < 0.025
- `deriva_volatil`: 0.025 <= componente < 0.06
- `deriva_hipervolatil`: componente >= 0.06

Volatilidad: cortes sobre la distribucion medida (0.025 ~ p20, 0.10 ~ p95). Deriva: 2.5% de erosion es el borde de una banda gestionada y 6% acumulado en 12 meses separa en el ultimo mes la cola de depreciacion gestionada (IDR/INR/JPY/PHP, 6.5-7.8%) del bloque leve (RON 3.5%, THB 1.8%, resto 0).

Filas por tramo combinado: estable=141, volatil=406, hipervolatil=389
Filas por tramo de volatilidad: estable=155, volatil=547, hipervolatil=234
Filas por tramo de deriva: estable=451, volatil=114, hipervolatil=171

## Distribucion de la volatilidad robusta anualizada

- n: 736
- min: 0.0000
- p10: 0.0035
- p20: 0.0243
- p25: 0.0282
- p50: 0.0436
- p75: 0.0625
- p90: 0.0842
- p95: 0.0982
- p99: 0.1518
- max: 0.1915

## Distribucion del componente de deriva (erosion acumulada 12m)

- n: 736
- min: 0.0000
- p10: 0.0000
- p20: 0.0000
- p25: 0.0000
- p50: 0.0042
- p75: 0.0556
- p90: 0.1126
- p95: 0.1356
- p99: 0.2233
- max: 0.2810

## ANTES / DESPUES: efecto de anadir la deriva al tramo

Mes de censo: 2026-08-01.
Divisas que cambian de tramo al anadir la deriva: 2 (INR, JPY). Empresas de la cartera viva afectadas: 4.

| Divisa | Tramo volatilidad (antes) | Tramo combinado (despues) | Deriva comp. |
| --- | --- | --- | --- |
| INR | volatil | hipervolatil | 0.0902 |
| JPY | volatil | hipervolatil | 0.0639 |

En toda la ventana, 22 divisas cambian de tramo en algun mes (AUD, BRL, CAD, CNY, CZK, GBP, HKD, HUF, IDR, INR, JPY, KRW, MXN, MYR, NZD, PHP, RON, SGD, THB, TRY, USD, ZAR); las empresas de la cartera viva que tienen alguna de esas divisas son 147.

Nota: la cifra de ventana es una COTA SUPERIOR (una divisa puede cambiar en un mes en que la empresa no tenga factura viva); el censo de empresas afectadas del mes de cierre es la medida principal.

### Sensibilidad al corte de deriva

| Corte deriva hiper | Divisas que cambian | Empresas afectadas |
| --- | --- | --- |
| 3% | IDR, INR, JPY, PHP, RON, TRY | 4 |
| 5% | IDR, INR, JPY, PHP, RON, TRY | 4 |
| 6% | IDR, INR, JPY, PHP, RON, TRY | 4 |
| 8% | RON, TRY | 0 |
| 10% | RON, TRY | 0 |

## Cobertura de la cartera viva

- Filtro: `document_type_norm = 'invoice' AND status_norm IN ('overdue', 'pending') AND flow_side = 'inflow'`
- Filas: 98340; empresas: 642; divisas: 30
- cubiertas_bce: 22 divisas (AUD, BRL, CAD, CHF, CNY, CZK, DKK, EUR, GBP, HKD, HUF, INR, ISK, JPY, MXN, NOK, NZD, PLN, SEK, SGD, USD, ZAR), 638 empresas
- sin_cobertura_bce: 8 divisas (AED, ARS, CLP, COP, MAD, MZN, NAD, PEN), 20 empresas
- Empresas en ambos grupos: 16

## Empresas de la cartera viva por tramo combinado (ultimo mes cerrado)

Mes: 2026-08-01

| Tramo | Empresas (solo volatilidad) | Empresas (combinado) |
| --- | --- | --- |
| estable | 599 | 599 |
| volatil | 128 | 127 |
| hipervolatil | 20 | 23 |

- estable: 599 empresas distintas en divisas (CHF, CZK, DKK, EUR, GBP, PLN, SEK, SGD)
- volatil: 127 empresas distintas en divisas (AUD, BRL, CAD, CNY, HKD, HUF, ISK, MXN, NOK, NZD, USD, ZAR)
- hipervolatil: 23 empresas distintas en divisas (AED, ARS, CLP, COP, INR, JPY, MAD, MZN, NAD, PEN)

Nota: una empresa con divisas en varios tramos aparece en cada uno; la suma no es el censo de empresas.

## Divisas hipervolatiles por defecto

Sin cobertura BCE en la ventana: AED, ARS, CLP, COP, MAD, MZN, NAD, PEN
Con cobertura BCE insuficiente en algun mes: BGN

## Resumen por divisa

| Divisa | Vol mediana | Deriva mediana | Deriva comp. mediana | Deriva comp. max | Meses deriva | Tramo vol modal | Tramo deriva modal | Tramo combinado modal | Meses BCE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AED | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| ARS | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| AUD | 0.0545 | -0.0158 | 0.0158 | 0.1041 | 24/24 | volatil | estable | volatil | 24/24 |
| BGN | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 16/24 | estable | estable | estable | 16/24 |
| BRL | 0.0868 | -0.0279 | 0.0279 | 0.1655 | 24/24 | volatil | estable | volatil | 24/24 |
| CAD | 0.0380 | -0.0544 | 0.0544 | 0.0872 | 24/24 | volatil | hipervolatil | volatil | 24/24 |
| CHF | 0.0312 | 0.0212 | 0.0000 | 0.0161 | 24/24 | volatil | estable | volatil | 24/24 |
| CLP | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| CNY | 0.0393 | -0.0130 | 0.0130 | 0.0902 | 24/24 | volatil | estable | volatil | 24/24 |
| COP | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| CZK | 0.0207 | 0.0166 | 0.0000 | 0.0382 | 24/24 | estable | estable | estable | 24/24 |
| DKK | 0.0013 | -0.0008 | 0.0008 | 0.0019 | 24/24 | estable | estable | estable | 24/24 |
| EUR | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 24/24 | estable | estable | estable | 24/24 |
| GBP | 0.0343 | -0.0090 | 0.0090 | 0.0573 | 24/24 | volatil | estable | volatil | 24/24 |
| HKD | 0.0508 | -0.0458 | 0.0458 | 0.1298 | 24/24 | volatil | estable | volatil | 24/24 |
| HUF | 0.0461 | 0.0032 | 0.0053 | 0.0753 | 24/24 | volatil | estable | volatil | 24/24 |
| IDR | 0.0501 | -0.0732 | 0.0732 | 0.1534 | 24/24 | volatil | hipervolatil | hipervolatil | 24/24 |
| ILS | 0.0857 | 0.0544 | 0.0000 | 0.0257 | 24/24 | volatil | estable | volatil | 24/24 |
| INR | 0.0578 | -0.0902 | 0.0902 | 0.1788 | 24/24 | volatil | hipervolatil | hipervolatil | 24/24 |
| ISK | 0.0329 | 0.0155 | 0.0000 | 0.0385 | 24/24 | volatil | estable | volatil | 24/24 |
| JPY | 0.0558 | -0.0639 | 0.0639 | 0.1476 | 24/24 | volatil | hipervolatil | hipervolatil | 24/24 |
| KRW | 0.0631 | -0.0783 | 0.0783 | 0.1316 | 24/24 | volatil | hipervolatil | hipervolatil | 24/24 |
| MAD | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| MXN | 0.0518 | 0.0067 | 0.0008 | 0.1879 | 24/24 | volatil | estable | volatil | 24/24 |
| MYR | 0.0418 | 0.0436 | 0.0000 | 0.0657 | 24/24 | volatil | estable | volatil | 24/24 |
| MZN | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| NAD | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| NOK | 0.0605 | 0.0033 | 0.0000 | 0.0470 | 24/24 | volatil | estable | volatil | 24/24 |
| NZD | 0.0508 | -0.0533 | 0.0533 | 0.1304 | 24/24 | volatil | volatil | volatil | 24/24 |
| PEN | - | - | - | - | 0/24 | hipervolatil | - | hipervolatil | 0/24 |
| PHP | 0.0533 | -0.0540 | 0.0540 | 0.1357 | 24/24 | volatil | hipervolatil | volatil | 24/24 |
| PLN | 0.0276 | 0.0076 | 0.0000 | 0.0245 | 24/24 | volatil | estable | volatil | 24/24 |
| RON | 0.0058 | -0.0203 | 0.0203 | 0.0394 | 24/24 | estable | estable | estable | 24/24 |
| SEK | 0.0410 | 0.0198 | 0.0000 | 0.0317 | 24/24 | volatil | estable | volatil | 24/24 |
| SGD | 0.0265 | -0.0105 | 0.0105 | 0.0675 | 24/24 | volatil | estable | volatil | 24/24 |
| THB | 0.0483 | 0.0025 | 0.0010 | 0.0678 | 24/24 | volatil | estable | volatil | 24/24 |
| TRY | 0.0574 | -0.2022 | 0.2022 | 0.2810 | 24/24 | volatil | hipervolatil | hipervolatil | 24/24 |
| USD | 0.0546 | -0.0447 | 0.0447 | 0.1280 | 24/24 | volatil | volatil | volatil | 24/24 |
| ZAR | 0.0773 | 0.0261 | 0.0000 | 0.0645 | 24/24 | volatil | estable | volatil | 24/24 |

## Lectura honesta de la limitacion

La distribucion del componente de deriva es un CONTINUO, no dos bloques separados: p50=0.0042, p75=0.0556, p90=0.1126, max=0.2810. Solo TRY es un valor atipico claro.

Meses por divisa en que la deriva sola ya da hipervolatil (de 24): TRY=24, INR=18, IDR=16, KRW=16, JPY=13, PHP=11, BRL=10, MXN=10, CAD=9, NZD=9, AUD=8, CNY=7, HKD=6, USD=5, HUF=3, SGD=3, MYR=1, THB=1, ZAR=1

Consecuencia: el corte del 6% es una decision de politica, no un hueco natural de la distribucion. En el mes de cierre arregla la inversion descrita (TRY la mas castigada, BRL sin erosion y sin subir de tramo), pero dentro de la ventana BRL, USD, HKD, NZD, CAD, KRW y PHP tuvieron episodios de depreciacion sostenida de 12 meses en 2024-2025 y la deriva los marca como hipervolatiles en esos meses. Eso es point-in-time correcto (en esos cortes SI perdian valor), pero significa que el indice sigue castigando a BRL en parte de la ventana. BRL y USD comparten el tramo volatil antes y despues: el indice por tramos no los separa aunque la volatilidad cruda de BRL sea mayor (0.087 frente a 0.055).
