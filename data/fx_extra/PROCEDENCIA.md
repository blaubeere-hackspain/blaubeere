# Procedencia de la serie vendorizada EUR/divisa del FMI (tarea F1c)

Este directorio contiene una **fuente externa vendorizada**: los tipos de cambio
del *International Financial Statistics* (IFS) que publica el Fondo Monetario
Internacional (FMI) en su portal de datos (`data.imf.org`), para las cuatro
divisas que la cartera viva usa y que el BCE **no** publica:
**ARS (Argentina), COP (Colombia), CLP (Chile) y PEN (Peru)**.

No es un artefacto regenerable del pipeline (`data/` no se escribe en general);
se vendoriza aqui de forma deliberada, igual que `data/fx_ecb/`, para que la
valoracion en EUR de esas facturas sea reproducible sin depender de la red.

Motivo de la existencia de esta fuente: el BCE **no cotiza** ARS, COP, CLP ni
PEN, y el `exchange_rate` interno de las facturas en esas divisas es una
**identidad (= 1,00)** cuando estan contabilizadas en su propia moneda, no un
tipo. Sin una fuente externa esas cuatro divisas no se pueden valorar en EUR.

## Ficheros vendorizados

Todos los ficheros son la respuesta cruda de la API SDMX 2.1 del FMI
(`https://api.imf.org/external/sdmx/2.1/data/...`) con
`Accept: application/vnd.sdmx.data+csv;version=1.0.0`. Se guardan **tal cual**,
con las 41 columnas de metadatos que devuelve el FMI (entre ellas
`ACCESS_SHARING_LEVEL`, `SECURITY_CLASSIFICATION`, `PUBLISHER`, `STATUS`), y se
verifica su `sha256` al cargarlos (`xray/fx_rates_extra.py`).

| Fichero | Contenido | URL exacta del recurso | Descarga (UTC) | Filas con dato | Rango | `sha256` |
| --- | --- | --- | --- | --- | --- | --- |
| `imf_er_xdc_eur_monthly.csv` | `XDC_EUR`, `PA_RT` y `EOP_RT`, frecuencia `M`, paises `ARG+COL+CHL+PER` | `https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER,4.0.1/ARG+COL+CHL+PER.XDC_EUR..M` | 2026-09-20 00:33:12 (02:33:12 CEST) | 2.654 (de 2.672 filas) | 1999-M01 a 2026-M08 | `db424ef49706c4dd52de9dce3019134abb3489bca6478b9d108c2b38aa65b499` |
| `imf_er_xdc_usd_monthly.csv` | `XDC_USD`, `PA_RT`, frecuencia `M`, mismos paises | `https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER,4.0.1/ARG+COL+CHL+PER.XDC_USD.PA_RT.M` | 2026-09-20 00:33:14 (02:33:14 CEST) | 3.243 | 1957-M01 a 2026-M08 | `641c87d1b2574cdc62556269c13cf35340621347a56c09ec27f9a92340600a98` |
| `imf_er_vintage_2026_jan.csv` | Vintage `ER_2026_JAN_VINTAGE`, `XDC_EUR`, ambos tipos | `https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER_2026_JAN_VINTAGE,1.0.0/ARG+COL+CHL+PER.XDC_EUR..M` | 2026-09-20 00:35:01 (02:35:01 CEST) | 2.592 | 1999-M01 a 2025-M12 | `20c2cd7719bf92c85f35eb709a6becdd3a3070dde67b07c8a0bc1dfc4a3d18c5` |
| `imf_er_vintage_2026_apr.csv` | Vintage `ER_2026_APR_VINTAGE`, `XDC_EUR`, ambos tipos | `https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER_2026_APR_VINTAGE,1.0.0/ARG+COL+CHL+PER.XDC_EUR..M` | 2026-09-20 00:35:03 (02:35:03 CEST) | 2.607 | 1999-M01 a 2026-M02/M03 | `09e0fcdf13d91a1b5179a0343b520714023a6fe41429685c6e19f7d587a8ab43` |

Los dos ficheros `imf_er_vintage_*.csv` **no se usan para construir la serie**:
se vendorizan como **prueba auditable** de cuando cada mes paso a estar
disponible en el FMI. Solo documentan la disciplina point-in-time (ver abajo).

La cabecera HTTP de las descargas no trae `Last-Modified` ni `ETag`: el recurso
es dinamico y se regenera cuando el FMI publica. Por eso la reproducibilidad
depende del **CSV vendorizado** (y de su `sha256`), no de la URL viva. El
servidor devolvio `content-type: application/vnd.sdmx.data+csv;version=1.0.0` y
`HTTP/2 200` en las cuatro descargas.

## Que contiene (formato y convencion)

Dimensiones del dataflow `IMF.STA:ER(4.0.1)` (dataset *Exchange Rates*, IFS):

- `COUNTRY`: `ARG`, `COL`, `CHL`, `PER` (codigos ISO3).
- `INDICATOR` = `XDC_EUR`: **unidades de moneda nacional por 1 EUR** (la misma
  convencion que el BCE: "unidades de divisa por EUR"). `XDC_USD` = unidades de
  moneda nacional por 1 USD.
- `TYPE_OF_TRANSFORMATION`: `PA_RT` = promedio del periodo (mes) y
  `EOP_RT` = fin de periodo (ultimo dia del mes).
- `FREQUENCY` = `M` (mensual). No hay frecuencia diaria para `XDC_EUR`.
- `TIME_PERIOD`: `AAAA-Mnn` (p. ej. `2026-M08`).
- `OBS_VALUE`: el tipo. **Vacio** cuando el FMI no tiene dato para ese mes
  (18 filas historicas, todas anteriores a 1986; ninguna dentro de la ventana
  del repo). Vacio nunca se convierte en cero.

### Cadena de conversion: NO hace falta

La serie es **directamente EUR/divisa** (`XDC_EUR`), asi que **no hay cadena
EUR/USD x USD/XXX**. Esto es mejor que encadenar: elimina la ambiguedad de
convencion. Aun asi se vendoriza `XDC_USD` para poder comprobar, sin depender
del CSV del BCE, que la serie esta bien orientada: `XDC_EUR / XDC_USD` debe
reproducir el EUR/USD de mercado. Medido en la ventana 2024-09..2026-08 para las
cuatro divisas: min 1,0354, max 1,1824, media 1,1320 (rango coherente con el
EUR/USD del BCE en esas fechas). Si la serie estuviera invertida, ese cociente
seria ~0,88 o ~1,0, no ~1,13.

## Cobertura real en la ventana del repo (2024-09 a 2026-08)

| Divisa | Pais | `PA_RT` (meses con dato) | `EOP_RT` (meses con dato) | Rango `PA_RT` (min-max, por EUR) | Rango `EOP_RT` (min-max, por EUR) |
| --- | --- | --- | --- | --- | --- |
| ARS | ARG | 24/24 | 24/24 | 1.063,24 - 1.733,66 | 1.066,76 - 1.744,04 |
| COP | COL | 23/24 | 23/24 | 3.735,81 - 4.792,71 | 3.597,58 - 4.802,73 |
| CLP | CHL | 24/24 | 24/24 | 997,02 - 1.126,42 | 990,30 - 1.128,59 |
| PEN | PER | 24/24 | 24/24 | 3,8526 - 4,1870 | 3,8312 - 4,1762 |

`COP` tiene 23 de 24 meses: **falta 2026-M08** en el FMI a la fecha de descarga
(el ultimo dato de Colombia es 2026-M07). Se declara ausente, no se interpola.

## Licencia y condiciones de uso

- El propio dato declara su nivel de acceso en la columna
  `ACCESS_SHARING_LEVEL = PUBLIC_OPEN` y `SECURITY_CLASSIFICATION = PUB` en
  **todas** las filas vendorizadas: publico y sin restriccion de acceso. El
  `PUBLISHER` es `IMF`, el departamento `STA` (Statistics) y el `TOPIC_DATASET`
  es `F31_ER,IFS`.
- Las condiciones de uso del FMI estan publicadas en
  `https://www.imf.org/en/About/copyright` (y enlazadas desde el portal
  `data.imf.org`). Su contenido no ha podido descargarse desde este entorno
  (el servidor devuelve `HTTP 403 Access Denied` a peticiones no navegador),
  por lo que **no se transcribe aqui**: se cita la URL canonica. En la practica
  el FMI publica este dataset como `PUBLIC_OPEN` y su reutilizacion esta
  permitida con **atribucion**; la atribucion sugerida es
  "Source: International Monetary Fund, International Financial Statistics
  (IFS), Exchange Rates (ER), consultado el 2026-09-20".
- No se ha vendido, sublicenciado ni redistribuido el dato fuera del repo.

## Disciplina point-in-time y `available_at` (decidido con evidencia)

El FMI **no publica el tipo del mes `m` dentro del mes `m`**: el IFS mensual se
actualiza con retardo. Por eso el modulo no puede fingir disponibilidad el
mismo mes. El retardo se ha **medido** con los dos vintages vendorizados:

| Vintage | Fecha de publicacion (UTC) | Ultimo mes `PA_RT` ARG/CHL/PER | Ultimo mes `PA_RT` COL | Ultimo mes `EOP_RT` ARG/CHL/PER |
| --- | --- | --- | --- | --- |
| `ER_2026_JAN_VINTAGE` | 2026-02-01T06:25:48Z | 2025-M12 | 2025-M12 | 2025-M12 |
| `ER_2026_APR_VINTAGE` | 2026-04-27T05:24:50Z | 2026-M02 | 2025-M12 | 2026-M03 |

Lectura:

- El valor de un mes `m` aparece en un vintage publicado **hacia el final del
  mes `m+2`** (dic-2025 ya estaba el 2026-02-01; feb-2026 el 2026-04-27).
- Colombia va mas lenta: el vintage del 2026-04-27 (que ya traia feb-2026 de
  los otros tres) seguia anclado en 2025-M12 para COP.
- Regla adoptada, **conservadora y declarada**, en
  `xray/fx_rates_extra.py`:
  `available_at` = **ultimo instante del mes `m+2`** y, en COP, del mes `m+4`
  (`PIT_LAG_MESES = 2`, `PIT_LAG_MESES_POR_DIVISA = {"COP": 4}`).
- Consecuencia honesta: para una decision al cierre del mes `M`, el dato mas
  reciente utilizable es el de `M-2` (y `M-4` en COP). El tipo del propio mes
  `M` **no** se puede usar en `M` sin fuga. Esto es una limitacion real de la
  fuente mensual, no un defecto del modulo.

El modulo expone `select_point_in_time(rows, as_of)` para que F3 no tenga que
reconstruir la regla, y `tests/test_fx_rates_extra.py` demuestra por perturbacion
y por truncado que ninguna fila futura altera la seleccion en un corte anterior.

## Reproducir la descarga

```sh
cd <raiz del repo>
H='Accept: application/vnd.sdmx.data+csv;version=1.0.0'
curl -sS -H "$H" -o data/fx_extra/imf_er_xdc_eur_monthly.csv \
  'https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER,4.0.1/ARG+COL+CHL+PER.XDC_EUR..M'
curl -sS -H "$H" -o data/fx_extra/imf_er_xdc_usd_monthly.csv \
  'https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER,4.0.1/ARG+COL+CHL+PER.XDC_USD.PA_RT.M'
curl -sS -H "$H" -o data/fx_extra/imf_er_vintage_2026_jan.csv \
  'https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER_2026_JAN_VINTAGE,1.0.0/ARG+COL+CHL+PER.XDC_EUR..M'
curl -sS -H "$H" -o data/fx_extra/imf_er_vintage_2026_apr.csv \
  'https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER_2026_APR_VINTAGE,1.0.0/ARG+COL+CHL+PER.XDC_EUR..M'
shasum -a 256 data/fx_extra/*.csv
```

Nota: como el FMI anade meses nuevos, volver a descargar en otra fecha cambia el
`sha256` del fichero principal. La serie publicada se regenera desde el CSV
vendorizado y `main` aborta si el `sha256` no coincide con el declarado aqui.

## Limitaciones declaradas

- **Frecuencia mensual**, no diaria. Valorar una factura con fecha `d` con el
  promedio (o el cierre) de su mes `m` es una aproximacion declarada, no el tipo
  de `d`. F3 debe decidir si usa `PA_RT` o `EOP_RT`; ambos se publican.
- **Retardo de publicacion** de ~2 meses (4 en COP): ver arriba.
- No se usan medianas internas de `exchange_rate` ni ninguna otra fuente interna
  para estimar tipos (prohibicion vigente del spec, punto 5). Tampoco se
  interpola ni se rellena: un mes sin dato queda a **nulo**, nunca a cero.
- El dataset del FMI puede mezclar convenciones oficiales y paralelas en
  episodios de controles de capital (relevante sobre todo en ARS). La
  verificacion de orden de magnitud contra la evidencia interna se hace en
  `reports/fx_risk/report_rates_extra.md` y en los tests.
- Los tipos son descriptivos del mercado, no predicen el riesgo futuro.
