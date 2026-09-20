# Procedencia de la serie vendorizada EUR/divisa del BCE

Este directorio contiene una **fuente externa vendorizada**: la serie completa de
tipos de referencia diarios del Banco Central Europeo (BCE). No es un artefacto
regenerable del pipeline (`data/` no se escribe en general); se vendoriza aqui de
forma deliberada para que el indice de inestabilidad FX sea reproducible para
siempre y no dependa de la red en tiempo de ejecucion.

## Fichero vendorizado

| Campo | Valor |
| --- | --- |
| Fichero | `data/fx_ecb/eurofxref-hist.csv` |
| Origen | BCE, Euro foreign exchange reference rates (historical) |
| URL del recurso descargado | `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip` |
| Formato descargado | ZIP; contiene un unico CSV (`eurofxref-hist.csv`) |
| Tamano del CSV | 1.922.007 bytes |
| sha256 del CSV vendorizado | `22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d` |
| sha256 del ZIP original | `44924a5317a2dee906f3e38e77a6061541f070074021347b600d8ab03bbd73b0` |
| Fecha y hora de descarga | 2026-09-20 01:57:24 CEST (2026-09-19 23:57:24 UTC) |
| `Last-Modified` del recurso (HTTP) | Fri, 18 Sep 2026 13:56:29 GMT |
| Filas de datos (sin cabecera) | 7.096 |
| Rango de fechas | 1999-01-04 a 2026-09-18 |
| Columnas | `Date` + 41 divisas + 1 columna final vacia de artefacto (`column42`) |

La ultima fila del CSV es 2026-09-18. La rejilla del repo termina en
`LAST_MONTH = 2026-08-01` (mes cerrado 2026-08), por lo que el CSV contiene mas
historia de la necesaria. El indice **recorta estrictamente a cierre de mes** y
no consume ninguna publicacion posterior al mes que mide (ver disciplina
point-in-time en `xray/fx_index.py`).

### Reproducir la descarga

```sh
curl -sS -o eurofxref-hist.zip \
  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip
unzip -p eurofxref-hist.zip eurofxref-hist.csv > eurofxref-hist.csv
shasum -a 256 eurofxref-hist.csv
# 22760c0c056226ba0d12d2acd67c3b60c18407a5185c48a06b3d16672fe6987d
```

Nota: el fichero es la publicacion oficial del BCE **en el momento de la
descarga**. Si se vuelve a descargar en una fecha posterior, el sha256 cambiara
porque el BCE anade filas nuevas cada dia laborable. La reproducibilidad del
indice depende del **CSV vendorizado**, no de la URL viva.

## Que contiene (formato)

CSV con cabecera `Date,USD,JPY,...`; una fila por dia laborable; celdas `N/A`
cuando la divisa no se publica ese dia. Los valores son unidades de divisa por
1 EUR (p. ej. `USD = 1.146` significa 1 EUR = 1,146 USD). Por construccion el
ancla es EUR: no hay columna EUR porque EUR/EUR es la identidad.

## Divisas publicadas (41 columnas), con su primera y ultima cotizacion

| Divisa | Primera | Ultima | Observaciones |
| --- | --- | --- | --- |
| SGD | 1999-01-04 | 2026-09-18 | 7096 |
| GBP | 1999-01-04 | 2026-09-18 | 7096 |
| NZD | 1999-01-04 | 2026-09-18 | 7096 |
| KRW | 1999-01-04 | 2026-09-18 | 7096 |
| SKK | 1999-01-04 | 2008-12-31 | 2560 |
| LTL | 1999-01-04 | 2014-12-31 | 4097 |
| NOK | 1999-01-04 | 2026-09-18 | 7096 |
| MTL | 1999-01-04 | 2007-12-31 | 2304 |
| ISK | 1999-01-04 | 2026-09-18 | 4755 |
| CHF | 1999-01-04 | 2026-09-18 | 7096 |
| CZK | 1999-01-04 | 2026-09-18 | 7096 |
| HUF | 1999-01-04 | 2026-09-18 | 7096 |
| LVL | 1999-01-04 | 2013-12-31 | 3842 |
| ROL | 1999-01-04 | 2005-06-30 | 1664 |
| CAD | 1999-01-04 | 2026-09-18 | 7096 |
| EEK | 1999-01-04 | 2010-12-31 | 3074 |
| PLN | 1999-01-04 | 2026-09-18 | 7096 |
| AUD | 1999-01-04 | 2026-09-18 | 7096 |
| ZAR | 1999-01-04 | 2026-09-18 | 7096 |
| USD | 1999-01-04 | 2026-09-18 | 7096 |
| SEK | 1999-01-04 | 2026-09-18 | 7096 |
| DKK | 1999-01-04 | 2026-09-18 | 7096 |
| HKD | 1999-01-04 | 2026-09-18 | 7096 |
| JPY | 1999-01-04 | 2026-09-18 | 7096 |
| CYP | 1999-01-04 | 2007-12-31 | 2304 |
| TRL | 1999-01-04 | 2004-12-31 | 1537 |
| SIT | 1999-01-04 | 2006-12-29 | 2049 |
| BGN | 2000-07-19 | 2025-12-31 | 6515 |
| TRY | 2005-01-03 | 2026-09-18 | 5559 |
| CNY | 2005-04-01 | 2026-09-18 | 5497 |
| IDR | 2005-04-01 | 2026-09-18 | 5497 |
| HRK | 2005-04-01 | 2022-12-30 | 4548 |
| RUB | 2005-04-01 | 2022-03-01 | 4333 |
| MYR | 2005-04-01 | 2026-09-18 | 5497 |
| PHP | 2005-04-01 | 2026-09-18 | 5497 |
| THB | 2005-04-01 | 2026-09-18 | 5497 |
| RON | 2005-07-01 | 2026-09-18 | 5432 |
| BRL | 2008-01-02 | 2026-09-18 | 4792 |
| MXN | 2008-01-02 | 2026-09-18 | 4792 |
| INR | 2009-01-02 | 2026-09-18 | 4536 |
| ILS | 2011-01-03 | 2026-09-18 | 4022 |

Once divisas de esta tabla (`CYP, EEK, LTL, LVL, MTL, ROL, SIT, SKK, HRK, RUB,
TRL`) no tienen **ninguna** cotizacion dentro de la ventana de analisis
(2024-09-01 a 2026-08-31): son divisas retiradas (euro) o suspendidas (RUB).
`BGN` deja de publicarse el 2025-12-31 (Bulgaria adopta el euro el 2026-01-01),
por lo que solo cubre parte de la ventana.

## Limitaciones declaradas

- El BCE **no publica** COP, ARS, CLP, PEN, NAD, MZN, AED, MAD ni varias otras
  divisas presentes en la cartera. El indice no las estima: las clasifica en el
  tramo mas inestable por defecto con motivo declarado
  (`sin_cobertura_bce_asumida_inestable`).
- No se usan medianas internas de `exchange_rate` ni ninguna otra fuente
  interna para estimar tipos (prohibicion vigente del spec, punto 8).
- Los tipos del BCE son descriptivos del mercado, no predicen el riesgo futuro.
