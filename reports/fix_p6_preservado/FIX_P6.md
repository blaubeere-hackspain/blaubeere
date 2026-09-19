# Fix P6 en healthscore_v3: preservado y revertido

Este directorio conserva el fix de la guarda sobre P6 (y sus cambios de
confianza y motivos) que se aplico sobre `xray/scoring_v3.py`,
`xray/scoring_io_v3.py` y sus tests, y que se DECIDIO REVERTIR por decision de
producto del usuario antes de que se consolidara. Los defectos que el fix
resolvia siguen ABIERTOS y son conocidos; se documentan aqui para poder
reaplicar el trabajo cuando se decida.

Estado de la reversion: verificada regenerando la salida completa del CLI
(`--output /tmp/score_v3_verif`, fuera del repo) y comparandola contra la
salida anterior preservada en `reports/score_v3_previo_defectuoso/`. El
parquet regenerado es identico fila a fila y columna a columna al preservado
salvo diferencias de ultimo bit (ULP, ~1e-14) en `health_score` /
`h_antes_de_mora` en 1.657 filas, atribuibles al orden de sumas de
punto flotante de la DuckDB `percentile_cont`; el corte 2026-08-31 no
presenta ninguna diferencia y el resumen publicado coincide.

## Que problema resolvia cada cambio (y su causa raiz)

### Defecto 1: H = 100 automatico con T6 = 0 (guarda sobre P6)

Causa raiz: con T6 = 0, `r_hist = C_hist/(C_hist + 0) = 1`, el termino `k`
iguala numerador y denominador (`k*R_hist = k`) y H sale 100 automatico, sin
ningun pago operativo demostrado. Empresas sin un solo pago identificado en
la ventana recibian nota perfecta.

El fix introdujo la guarda `p6 <= 0 -> health_score = None` con motivo
`pagos_operativos_no_demostrados`. La guarda va sobre P6 y NO sobre T6
porque D6 = 0 es legitimo (una empresa sin deuda esta bien) mientras que
P6 = 0 significa que no hemos identificado sus pagos.

### Defecto 2: la confianza colapsaba por D desconocido

Causa raiz: `d_eur` queda desconocido en los 6 meses de la ventana para 747
de 1.286 empresas (58%) porque `xray/marts/deuda.py` anula
`servicio_deuda_eur` ante cualquier salida ambigua. El motor metia D en el
mismo cubo de deduccion `entradas_desconocidas` que C y P, y con 6 meses de
D desconocido la confianza de media/alta caia a `ninguna` por diseno, aunque
C y P estuvieran perfectamente conocidos.

El fix excluyo D del cubo `entradas_desconocidas` (la deduccion de confianza
solo mira C y P; D queda como reason informativo
`d_eur_desconocido_en_N_meses` sin restar nivel).

### Defecto 3: `denominador_nulo` quedaba inalcanzable

Causa raiz: con la guarda sobre P6, cualquier fila con nota tiene
T6 > 0 y por tanto denominador positivo; el motivo `denominador_nulo`
(c6 = 0 y t6 = 0) quedaba logicamente inalcanzable. El fix lo sustituyo por
`sin_flujos_observados_en_la_ventana` para c6 = 0 y t6 = 0.

## Efecto medido (corte 2026-08-31, salvo indicacion)

Verificado contra `reports/score_v3_previo_defectuoso/assessments.parquet`
(antes) y `reports/fix_p6_preservado/assessments_con_fix.parquet` (despues):

| Metrica | Antes (sin fix) | Despues (con fix) |
|---|---|---|
| Empresas con nota | 959 | 516 |
| Empresas con nota >= 99,99 | 112 | 0 |
| Filas con p6 = 0 que conservan nota (todo el panel) | 5.894 | 0 |
| Empresas sin deuda (d6 = 0, p6 > 0) con nota | 7.365 | 7.365 (sin cambio) |
| health_score p10 / p50 / p90 | 3,30 / 68,55 / 100,00 | 2,24 / 50,02 / 82,07 |
| Confianza `ninguna` entre las que tienen nota | 807 de 959 (84%) | 371 de 516 (72%) |
| \|dH\| p50 / p75 / p90 del panel (resumen publicado) | 1,12 / 6,38 / 17,65 | 3,27 / 9,13 / 21,13 |

Nota sobre la ultima fila: las cifras publicadas de \|dH\| (1,12 / 6,38 /
17,65 y 3,27 / 9,13 / 21,13) proceden de los `summary.json` de cada
version, que calculan los percentiles sobre pares distintos: 15.003 pares
sin fix frente a 9.268 con fix. Recalcule sobre la poblacion "todos los
pares con nota en cada version" (15.003 y 9.340 pares) da 1,12 / 6,38 /
17,65 frente a 3,31 / 9,25 / 21,43; la diferencia es la definicion de
poblacion, no un error.

## Analisis de poblacion (lo importante)

Los deltas de las empresas que CONSERVAN nota son bit-identicos antes y
despues: 0 filas con p6 > 0 cambian de valor de `health_score` (comprobado
con igualdad estricta sobre los dos parquet). El fix no toca la formula de
las empresas con pagos identificados; solo retira la nota a las que no
tienen pagos demostrados.

Por eso el p75 de \|dH\| sube de 6,38 a 9,13 NO porque el algoritmo sea
menos estable, sino porque el 6,38 estaba diluido por miles de pares
artificiales con H = 100 constante y delta ~0 (empresas sin pagos
identificados que puntuaban 100 automatico). \|dH\| p75 sobre "todos los
pares con nota" no es invariante a cambios de cobertura: comparar 6,38 con
9,13 es comparar poblaciones distintas (15.003 pares frente a 9.268). Cual
quier metrica de estabilidad debe restringirse a la poblacion comun.

## Desglose de quien perdia la nota en el corte 2026-08-31

De las 959 empresas con nota antes del fix, 443 la pierden con el fix (todas
con p6 = 0):

- 112 con nota >= 99,99 (el caso extremo del bug: H = 100 automatico).
- 331 que puntuaban via historial rancio (k*R_hist con p6 = 0).
- 159 de ellas tenian ademas c6 = 0 y t6 = 0 (corte que cruza con los dos
  grupos anteriores).

## Por que se revirtio

Decision de producto del usuario: la version anterior cubre 959 empresas
frente a 516 y se prefiere construir desde ella. Los tres defectos descritos
arriba siguen abiertos y conocidos; el fix queda documentado y preservado
para reaplicarlo cuando se decida.

## Como reaplicarlo

Los cuatro ficheros preservados en este mismo directorio son copias del
estado CON fix:

- `xray_scoring_v3.py` -> `xray/scoring_v3.py`
- `xray_scoring_io_v3.py` -> `xray/scoring_io_v3.py`
- `tests_test_scoring_v3.py` -> `tests/test_scoring_v3.py`
- `tests_test_scoring_io_v3.py` -> `tests/test_scoring_io_v3.py`

Y la salida correspondiente: `assessments_con_fix.parquet` y
`summary_con_fix.json` (equivalentes a los que publico `reports/score_v3/`
cuando el fix estaba aplicado).
