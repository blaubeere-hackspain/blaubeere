# healthscore_v4 — capa D: motor puro (scoring_v4) y adaptador (scoring_io_v4)

Informe de la tarea. Corte de referencia: **2026-08-31** (fin del ultimo mes
cerrado). Workspace canonico: `data/runs/20260919T022951Z-83143e8d`.
v3 CONVIVE intacto: no se ha tocado `xray/scoring_v3.py`,
`xray/scoring_io_v3.py`, sus tests ni `reports/score_v3/`.

## Ficheros creados

| Fichero | Lineas | Contenido |
|---|---|---|
| `xray/scoring_v4.py` | 504 | motor puro: formula v4, guardas, confianza |
| `xray/scoring_io_v4.py` | 935 | adaptador: capas A/B/C + C/P/D, join verificado, CLI, comparativa v3, analisis de triple conteo |
| `tests/test_scoring_v4.py` | 382 | 24 tests del motor puro |
| `tests/test_scoring_io_v4.py` | 430 | 15 tests del adaptador (incluye el join real de 30.864 pares) |
| `reports/score_v4/assessments.parquet` | — | 30.864 filas (1286 empresas x 24 meses cerrados) |
| `reports/score_v4/summary.json` | — | informe machine-readable con por_corte, comparativa_v3 y riesgo_triple_conteo |

## Formula implementada (exactamente la especificada)

- Ventana expansiva hasta 6 meses y luego movil de 6 (igual que v3).
- `C6`, `P6`, `D6`: sumas de lo CONOCIDO en la ventana; nulo nunca es cero.
- `deficit_servicio_6` = SUMA del `deficit_servicio_eur` de la ventana (flujo mensual).
- `obligacion_vencida_m` = `obligacion_vencida_eur` DEL CORTE m, tomada UNA vez,
  NO sumada sobre la ventana (stock, no flujo). Test obligatorio del motor:
  6 cortes con la misma factura vencida producen el mismo `T6_efectivo` que 1 corte.
- `T6_efectivo = P6 + D6 + deficit_servicio_6 + obligacion_vencida_m`, con los
  None EXCLUIDOS de la suma.
- `colchon_v4 = max(0, saldo_reversa_eur en el corte m)`; NULL -> 0 con motivo
  `sin_caja_reconstruida` (nunca se inventa); negativo -> 0 con motivo
  `saldo_reversa_negativo`. Es el cambio clave respecto de v3, que usaba el
  `colchon` de cash_position (nivel menos minimo historico), invariante al
  ancla y por eso sin lectura de liquidez real.
- `colchon_aplicable = min(colchon_v4, alpha * T6_efectivo)`.
- `R_hist = C_hist / (C_hist + T_hist_efectivo)` sobre el denominador nuevo:
  flujos de todo el historial (P+D+deficit) mas el stock del corte una sola
  vez; indefinida -> se omite el termino k con motivo (no se inventa 0.5).
- `H = 100 * (C6 + colchon_aplicable + k*R_hist) / (C6 + colchon_aplicable + T6_efectivo + k)`.
- `H_final = H * (1 - beta*mora_indice) * multiplicador_deuda`; ambos factores
  SOLO a la baja, acotados; NULL -> sin ajuste con motivo registrado (nunca
  ajuste a cero). Verificado en el panel: 0 filas fuera de [0, 100].

Parametros usados: k = 4178.45 (medido en reports/calibration_k/report.md,
sobre la rejilla v3), alpha = 3.0, beta = 0.25. Todos PROVISIONALES pendientes
de recalibracion para v4; NO se ha calibrado nada en esta tarea.

## Guardas de no-nota (fix P6 reaplicado, en orden)

1. `sin_actividad_de_caja_observada`: sin actividad de caja en todo el historial.
2. `sin_flujos_observados_en_la_ventana`: c6 = 0 y t6_efectivo = 0 (sustituye al
   `denominador_nulo` de v3).
3. `pagos_operativos_no_demostrados`: p6 <= 0 Y (obligacion_vencida_m es NULL o 0).
   Si p6 = 0 pero hay obligacion vencida > 0, YA NO es un hueco de datos: es
   evidencia positiva de que la empresa debe y no paga; la formula funciona y
   la empresa recibe una nota BAJA, no un None. D6 = 0 no dispara ninguna guarda.

La deduccion de confianza `entradas_desconocidas` solo mira C y P; D desconocido
queda como reason informativo `d_eur_desconocido_en_N_meses` sin restar nivel
(defecto 2 del fix P6). El saldo_reversa ausente deduce nivel
(`sin_caja_reconstruida`).

## D5 — Comparativa v4 vs v3 (corte 2026-08-31 salvo indicacion)

Poblacion SIEMPRE declarada: los |dH| estan RESTRINGIDOS a la poblacion comun
de pares (company_id, month) con nota en ambas versiones; comparar |dH| sobre
poblaciones distintas es invalido (FIX_P6.md, y asi esta construido el
summary.json).

| Metrica | v3 sin fix (publicada) | v3 con fix (revertido, referencia) | **v4** |
|---|---|---|---|
| Empresas con nota (ultimo corte) | 959 | 516 | **904** |
| Empresas con nota >= 99,99 (ultimo corte) | 112 | 0 | **0** |
| Pares con nota >= 99,99 (panel completo) | 2.275 | — | **8** |
| health_score p10 / p50 / p90 (ultimo corte) | 3,30 / 68,55 / 100,00 | 2,24 / 50,02 / 82,07 | **2,74 / 36,89 / 79,19** |
| health_score p10 / p50 / p90 (panel) | 2,11 / 64,85 / 100,00 | — | **1,48 / 41,04 / 85,59** |
| Confianza (ultimo corte) | ninguna 1134 / media 58 / baja 43 / alta 51 | ninguna 371 de 516 con nota (72%) | **ninguna 1132 / media 89 / alta 65** |
| \|dH\| p50 / p75 / p90 | 1,12 / 6,38 / 17,65 (poblacion distinta) | 3,27 / 9,13 / 21,13 (poblacion distinta) | **8,21 / 29,24 / 55,10** |

Detalle de la poblacion comun: v3 tiene 15.962 pares con nota en el panel;
**13.274 pares** tienen nota en AMBAS versiones y sobre ellos se calculan los
|dH| (el resto son los que v3 nota y v4 retira, mas los que v4 nota y v3 no).
Los |dH| son grandes por diseno: v4 cambia el denominador (stock de obligacion
vencida), el colchon (nivel reconstruido) y reaplica la guarda de P6; no son
comparables con los deltas intra-version de v3 (que median estabilidad mes a
mes, otra poblacion otra vez).

### Nota >= 99,99 en v4

- Ultimo corte: **0** (esperado: 0; el bug de H = 100 automatico con T6 = 0
  esta bloqueado por la guarda de P6 reaplicada).
- Panel completo: **8 pares de 15.647 (0,05%)**, de 4 empresas (COMP_0743 x2,
  COMP_1034 x5, COMP_0377 x1). Investigado: NO es el bug de v3. Son cortes
  donde C6 es enorme (350 k a 10,7 M EUR) y P6 es minusculo pero CONOCIDO y
  POSITIVO (10-39 EUR), sin deuda ni obligacion vencida y con mora
  desconocida; H sale 99,99+ por aritmetica pura de la formula (colchon
  saturado en alpha*T6, R_hist ~ 1). La formula no los bloquea porque p6 > 0.
  Es un residual de calibracion (pagos simbolicos no activan la guarda),
  no un defecto del motor; se reporta para la ronda de calibracion.

### Empresas que reciben nota en v4 gracias a la nueva guarda

Definicion (ultimo corte): p6 = 0, obligacion_vencida_m > 0, nota no nula.

- **388 empresas** (5579 pares en el panel; 379 en 2026-07, 360 en 2026-06).
- Notas que sacan: min 0,00 / p10 1,94 / p50 35,44 / p90 87,73 / max 99,92
  (ultimo corte; en el panel p50 33,68, max 99,96). Notas BAJAS en la cola
  baja, como exige el criterio literal ("si no pagas nada teniendo deuda
  tienes un problema").

### Sin nota y reparto de motivos (ultimo corte; panel entre parentesis)

| Motivo | Ultimo corte | Panel (pares) |
|---|---|---|
| falta_de_actividad | 8 | 8.873 |
| pagos_operativos_no_demostrados (p6=0 sin obligacion) | 108 | 2.199 |
| sin_flujos_observados_en_la_ventana | 266 | 4.145 |
| **Total sin nota** | **382** | **15.217** |
| Con nota | 904 | 15.647 |

## D3 — Riesgo de TRIPLE CONTEO (medido; no se cambio la formula)

La senal "facturas vencidas impagadas" alimenta tres canales a la vez:
(1) denominador (`obligacion_vencida_m`), (2) `multiplicador_deuda`
(capitaliza antiguedad, piso 0,5) y (3) `mora_indice` por el lado pago
(EWMA de severidad de mora de pago). Cifras en
`reports/score_v4/summary.json -> riesgo_triple_conteo`.

### Correlaciones entre canales (pares empresa-mes <= corte con ambos valores no nulos)

| Par | n | Pearson | Spearman |
|---|---|---|---|
| obligacion_vencida vs multiplicador_deuda | 28.470 | -0,29 | -0,83 |
| obligacion_vencida vs mora_pago_robusta | 14.201 | +0,20 | +0,56 |
| multiplicador_deuda vs mora_pago_robusta | 12.538 | -0,46 | -0,63 |

Lectura: obligacion y multiplicador son canales COMPLEMENTARIOS en importe
(spearman -0,83: el multiplicador se normaliza por la magnitud propia, con la
cota 0,5) pero ambos crecen con la severidad del impago; obligacion y mora_pago
correlacionan POSITIVAMENTE (+0,56 spearman): quien debe hace mas tiempo
tambien tarda mas en pagar. Los tres canales responden a la misma senal de
fondo, con transformaciones distintas.

### Descomposicion del castigo (Shapley sobre 3 canales binarios, ultimo corte)

Metodo: para cada empresa con nota, 8 combinaciones de canales activos; el
motor se reevalua con cada canal desactivado; castigo_i = -phi_i (Shapley), en
puntos de nota. Poblacion: 516 empresas evaluadas + 388 descartadas porque al
desactivar el canal denominador pierden la nota (nueva guarda) + 374 sin nota
en el corte + 8 sin fila en el corte = 1286.

Reparto del castigo entre las 300-310 empresas penalizadas (parte del castigo
total atribuida a cada canal):

| Canal | p50 | p90 | n con castigo > 0 |
|---|---|---|---|
| obligacion_en_denominador | 0,165 | 0,531 | 284 |
| mora_indice | 0,078 | 0,714 | 310 |
| multiplicador_deuda | 0,647 | 0,872 | 300 |

Top 2 penalizadas (castigo en puntos):

| Empresa | Nota final | Sin castigos | Denominador | Mora | Multiplicador |
|---|---|---|---|---|---|
| COMP_0518 | 12,43 | 99,57 | 54,44 | 1,80 | 30,89 |
| COMP_0688 | 9,76 | 94,33 | 51,75 | 5,00 | 27,81 |

### Recomendacion razonada (para que la decida el coordinador en la calibracion)

1. El canal multiplicador es el que mas castigo concentra (p50 0,647 del
   castigo total; p90 0,872). Su cota de diseno (multiplicador >= 0,5) limita
   el castigo a un 50% maximo POR SI SOLO, pero apilado con los otros dos el
   castigo total llega a ~87 puntos de nota (nota 12,43 desde 99,57).
2. El canal denominador es estructural: la lectura de la formula ("lo que pago
   mas lo que debia pagar y no pago") lo exige y no deberia atenuarse.
3. El canal mora aporta poco en mediana (0,078) pero se concentra en la cola
   (p90 0,714): en las peores empresas duplica el castigo sobre una senal que
   ya entr por denominador y multiplicador.
4. RECOMENDACION: mantener denominador y multiplicador tal como estan
   (el multiplicador ya esta acotado por diseno) y, en la calibracion, evaluar
   ATENUAR la mora_indice cuando hay obligacion vencida > 0 en el corte
   (p. ej. reducir beta efectivo, o aplicar la mora solo cuando no hay
   obligacion vencida que ya castigue). Tambien conviene recalibrar beta y la
   cota del multiplicador sobre la rejilla v4: sus defaults son de v3. NO se
   ha cambiado nada de la formula en esta tarea.

## Verificacion

- Bateria completa (lista explicita, sin discover):
  `.venv/bin/python -m unittest tests.test_contracts tests.test_marts tests.test_pipeline tests.test_regressions tests.test_cash_position tests.test_payment_delay tests.test_transfer_resolve tests.test_scoring_v3 tests.test_scoring_io_v3 tests.test_debt_obligation tests.test_payment_delay_v2 tests.test_cash_backfill tests.test_scoring_v4 tests.test_scoring_io_v4 -v`
  -> **233 tests, OK**.
- Especificos v4: `.venv/bin/python -m unittest tests.test_scoring_v4 tests.test_scoring_io_v4 -v` -> **39 tests, OK**.
- El join del adaptador verifica en cada corte que los tres conjuntos de
  (company_id, month) son identicos; el corte final tiene 30.864 pares
  (assert en `assess_at_v4` y test contra los parquet reales).
- Salida generada con: `.venv/bin/python -m xray.scoring_io_v4 --workspace
  data/runs/20260919T022951Z-83143e8d --output reports/score_v4`.
- Integridad: fingerprint de data/ invariante antes/despues del run;
  `assessments.parquet` con el esquema cerrado declarado (30 columnas).

## Bugs detectados en las capas A/B/C SIN arreglar (reportados, no tocados)

1. **DTYPE inconsistente de `month`** (conocido y avisado en el spec):
   `cash_backfill_monthly.parquet` guarda `month` con dtype distinto (STRING
   en la consolidacion original) que las capas A y C (datetime64[ms]). El
   adaptador lo normaliza con `CAST(month AS DATE)`; el join verificado no
   pierde filas. Arreglo de fondo (escribir dtype uniforme) fuera de alcance:
   pertenece a `xray/cash_backfill.py`.
2. Ningun otro defecto bloqueante detectado en A/B/C: los tres parquet tienen
   exactamente la misma rejilla de 30.864 pares de meses cerrados y las
   constantes declaradas en sus coverage.json se corresponden con lo medido
   (p. ej. multiplicador en [0,5; 1], obligacion_vencida no nula en 13.230
   empresa-mes).
