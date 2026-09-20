# Mapa de contexto de `healthscore_v3`

- **Estado:** documento de referencia; describe lo que el algoritmo absorbe a dia de hoy.
- **Version del modelo:** `healthscore_v3` (`xray/scoring_v3.py`, adaptador `xray/scoring_io_v3.py`).
- **Ejecucion de datos fijada:** `data/runs/20260919T022951Z-83143e8d` (resuelta por `xray.paths` desde `reports/current.json`). Todas las rutas relativas de este documento parten de esa ejecucion salvo las de `reports/`.
- **Metodo:** cada cifra numerica de este documento fue verificada contra el artefacto citado antes de escribirse. Las excepciones se marcan como *Nota de verificacion*.

Aviso de vigencia: `xray/scoring_v3.py` y `xray/scoring_io_v3.py` estan siendo corregidos en paralelo en el momento de escribir esto (seccion 8). Las secciones 8 y 9 documentan el estado previo a esa correccion, que es el estado medido y publicado.

## 1. Que es healthscore_v3 y su formula

healthscore_v3 es un indice determinista de **salud de flujo de caja**: mide si los cobros de la actividad cubren los pagos operativos y el servicio de deuda observados, con que holgura y con que estabilidad respecto de la propia historia de la empresa. No es un predictor, no se entrena, no compara empresas entre si y no pretende ser una probabilidad de impago.

Formula (contractual, definida en la cabecera de `xray/scoring_v3.py`):

```
Ventana: expansiva hasta 6 meses y luego movil de 6.
  para el corte de fin de mes m: meses cerrados max(primer_mes_observado, m-5) .. m

T6 = P6 + D6          (pagos operativos + servicio de deuda conocidos en la ventana)

R_hist = C_hist / (C_hist + T_hist)
  ratio acumulada de la PROPIA empresa desde su primer mes observado hasta m.
  Si C_hist + T_hist = 0, R_hist es indefinida: el termino k se omite entero
  (sin euros virtuales en numerador ni denominador). No se inventa 0,5.

colchon_aplicable = min(colchon_bruto, alpha * T6)     (0 si el colchon es None o negativo)

H       = 100 * (C6 + colchon_aplicable + k * R_hist) / (C6 + colchon_aplicable + T6 + k)
H_final = H * (1 - beta * mora_ratio)                  (penalizacion solo a la baja;
                                                        mora_ratio None = sin ajuste)
```

Tres decisiones de diseno que conviene entender antes de tocar nada:

- **La mezcla va en euros, no en notas.** Numerador y denominador suman importes EUR, no promedian puntuaciones de meses. Un mes de 30 EUR de cobros no puede pesar lo mismo que uno de 3 M EUR: promediar notas devolveria al indice el sesgo adverso que esta arquitectura corrige (las empresa-mes pequenas o poco observadas influirian igual que las grandes). El termino `k` son euros virtuales que emparejan a `R_hist` y sostienen la nota cuando el volumen observado de la ventana es pequeno.
- **La ventana es expansiva hasta 6 meses.** Las primeras notas no esperan calentamiento: desde el primer mes cerrado ya hay valor, con la ventana que exista (y el motivo `ventana_parcial` declarado).
- **`R_hist` es solo de la propia empresa.** El motor nunca mira otras empresas, no normaliza entre ellas y no compara con nada externo.

Guardas de no-nota (ningun numero se inventa cuando no hay nota): sin actividad de caja observada en todo el historial; `C6 = 0` y `T6 = 0` (nada de flujos observados en la ventana); y una guarda sobre pagos operativos demostrados en la ventana. El detalle y su estado de correccion estan en la seccion 8.

## 2. Fuentes primarias

Todas de **solo lectura**; el scorer y las capas derivadas no modifican ninguna. Rutas relativas a `data/runs/20260919T022951Z-83143e8d/`.

| Fuente | Grano | Filas | Que aporta a v3 |
|---|---|---|---|
| `data/clean/transactions.parquet` | 1 fila por transaccion | 2.556.437 | Base de todo C y P: importes, fechas, direccion, conversion EUR, `is_booked`, `is_open_month` |
| `data/clean/companies.parquet` | 1 fila por empresa | 1.286 | Universo y `group_id` del perimetro |
| `data/clean/banking_products.parquet` | 1 fila por producto bancario | 5.987 | Catalogo que define los productos de caja (`checking`/`saving`/`wallet`) |
| `data/clean/invoices.parquet` | 1 fila por documento comercial | 897.894 | Cartera de AP/AR para la capa de mora (`xray/payment_delay.py`) |
| `data/clean/balances.parquet` | snapshot de saldo por producto y fecha | 7.996 (7.980 en el snapshot 2026-09-01, 1.273 de 1.286 empresas) | Solo diagnostico de cordura de la capa de caja; nunca entrada de la nota |
| `data/interim/tx_flow_class.parquet` | 1 fila por transaccion (alineada por `transaction_id`/`_src_row`) | 2.556.437 | Clase de flujo original (`operating_in`, `operating_out`, `transfer`, `unknown`, `non_economic`, `financing_*`, `investment_*`) que el adaptador usa para lo que la resolucion no toca |
| `data/marts/panel_flujos.parquet` | empresa-mes | 30.864 | `tiene_actividad_caja`, `primer_mes_caja`, `volumen_caja_conocido_eur` |
| `data/marts/panel_deuda.parquet` | empresa-mes | 30.864 | D: `servicio_deuda_eur` (principal + intereses) |
| `data/marts/panel_cobro.parquet` | empresa-mes | 30.864 (1.286 empresas) | Solo las cohortes de cobro `coh_pct_cobrado_30/60/90d` filtradas por `coh_Nd_available_at <= corte`; ninguna otra columna (ver seccion 5) |

Perimetro comun de las capas de caja: filas `is_booked`, `product_type` en `checking`/`saving`/`wallet`, meses cerrados (`NOT is_open_month`). Verificado midiendo: 2.233.167 filas de transaccion en ese perimetro, con un volumen absoluto de 297.537 M EUR (`abs(amount_eur)`).

Universo: 1.286 empresas, 24 meses cerrados de 2024-09 a 2026-08 (`reports/quality/panel_cobro.json`; los tres marts tienen 30.864 filas = 1.286 x 24).

## 3. Las tres capas derivadas de v3

Todas se generaron como informes derivados de solo lectura bajo `reports/` y estan probadas en `tests/`. Ninguna modifica `xray/flows.py` ni los marts publicados.

### 3.1 `xray/transfer_resolve.py` → `reports/transfer_resolution_v2/`

**Problema.** El bucket ambiguo `transfer` concentraba el 37% del volumen de caja observado: 110.631 M EUR de 297.537 M EUR del perimetro comun (verificado: `reports/transfer_resolution_v2/coverage.json`, clave `ambiguo_antes`, y medicion propia sobre `clean/transactions.parquet` + `interim/tx_flow_class.parquet`). Son 171.173 filas en 1.017 empresas que no pueden sumarse a C ni a P porque podrian ser movimientos internos entre cuentas propias. Esa masa ensanchaba las cotas de C y P de manera masiva.

**Tres reglas en orden (`R1 -> R2 estricta -> R2 laxa -> R3`; R3 solo toca lo que sigue ambiguo):**

| Regla | Criterio | Filas | EUR | Fuente |
|---|---|---|---|---|
| R1 | Retiradas de efectivo fuera del perimetro (`cash_withdrawal_sale_de_perimetro`) | 19.527 | 6.685 M | `coverage.json` → `recuperado.r1_cash_withdrawal` |
| R2 estricta | Par interno por importe EUR y fecha: max 3 dias, producto distinto (demostrado) | 10.496 | 26.240 M | `coverage.json` → `r2_estricta` |
| R2 laxa | Par interno max 7 dias, sin exigir producto distinto | 24.846 | 29.478 M | `coverage.json` → `r2_laxa` |
| R3 | Clasificacion por descripcion bancaria normalizada, anclada al inicio (`re.match`), primera coincidencia gana | 96.908 | 47.416 M | `coverage.json` → `r3_descripcion` |

R3 es una tabla de **16 patrones auditables** con su medicion fila a fila (`coverage.json` → `patrones_r3`). La lectura semantica del castellano bancario que la sustenta: *traspaso* es entre cuentas propias y *transferencia* es a un tercero.

- 9 patrones de familia traspaso van a `internal_transfer` con **confianza MEDIA** (80.407 filas, verificado en `resolution.parquet`; son las clases del banco, no pares demostrados). *Nota de verificacion: el encargo indicaba 86.120 filas; la cifra que reproduce el artefacto es 80.407 (`groupby(rule='desc:traspaso', confidence='media')` en `reports/transfer_resolution_v2/resolution.parquet`).*
- 2 patrones de efectivo fuera del perimetro van a `operating_out` y 2 patrones de pasarela (Stripe payout) a `operating_in`, ambos con confianza media.
- 9.643 filas de transferencia a tercero (`TRANSFERENCIA`/`TRANSFER`/`TRANSF`) se asignan a `operating_in` u `operating_out` **por signo** con confianza **BAJA** (`resolution.parquet`: `desc:transferencia_tercero`, baja). El bloque `sensibilidad_transferencia_tercero` de `coverage.json` permite descartar esta regla sin recalcular.

**Resultado.** El ambiguo bajo de 116.304 filas / 48.228 M EUR (estado intermedio de la primera version de la capa, `reports/transfer_resolution/coverage.json`) a 19.396 filas / 812 M EUR (`coverage.json` → `ambiguo_despues`). El residual por empresa-mes paso de mediana 99,75 / p75 100 / p90 100 a mediana 0,0 / p75 0,0 / p90 63,56 (`residual_pct_empresa_mes` en ambos `coverage.json`), y las empresa-mes con ambiguo cero subieron de 2.148 a 7.884 de 10.486 (`empresa_mes_a_cero`).

**Lo que queda fuera a proposito, por no demostrable:**

- `SCF-AJUS.SALDO%` (ajustes de saldo que el propio pipeline de clases manda al bucket `transfer`, ver `xray/flows.py`): 9.137 filas siguen ambiguas tras la resolucion. *Nota de verificacion: el encargo indicaba 171 M EUR; mido 273,7 M EUR sumando `abs(amount_eur)` sobre esas 9.137 filas en el estado final (`resolution.parquet` + `transactions.parquet`). El numero de filas coincide; el importe no.*
- Las conversiones de divisa y las transferencias internacionales con `fx_ambiguous` quedan ambiguas.

**Honestidad de R2.** La tasa de falsos pares del emparejamiento por importe y fecha **no es medible**: `counterparty_id` solo esta informado en el 2,34% de las transferencias y no confirma pares (`coverage.json` → `honestidad`). La estricta es cota inferior y la laxa cota superior del volumen realmente interno: 10.496 filas / 26.240 M EUR frente a 35.342 filas / 55.718 M EUR (`cotas_volumen_interno`). Ninguna de las dos cotas es verdad verificada; el scorer debe tratar el volumen emparejado como un intervalo, no como flujo operativo confirmado.

### 3.2 `xray/cash_position.py` → `reports/cash_position/`

**Problema que resuelve.** v3 necesita un colchon: caja que la empresa pueda dedicar a cubrir pagos. No hay saldo bancario historico fiable (los balances son un unico snapshot posterior), asi que se **reconstruye la posicion de caja acumulando los movimientos hacia delante**, point-in-time y sin anclar en balances: `posicion_acumulada(mes) = flujo_neto(mes_origen) + ... + flujo_neto(mes)` sobre todo lo que mueve caja dentro del perimetro comun.

Lo que es, y lo que no es, en palabras del propio modulo (`xray/cash_position.py`, docstring): la posicion es **relativa al primer mes observado de cada empresa (mes_origen)** y **NO es un saldo bancario**. No esta permitido presentarla como el dinero que la empresa tiene en el banco. Sobre ella se derivan el `colchon` y `meses_de_cobertura`, que son adimensionales respecto del tamano de la empresa (todo se compara con la propia historia).

**Cobertura medida (`reports/cash_position/coverage.json`):**

- 1.278 de 1.286 empresas con serie; 8 sin actividad de caja observada.
- 21.464 empresa-mes emitidos; confianza **alta en 19.645** de ellos.
- `meses_de_cobertura` (colchon / salida media mensual): mediana 0,12, p75 0,74, p90 2,81. En el ultimo mes de cada empresa: mediana 0,25, p90 5,07.
- 10.525 de 21.464 empresa-mes con posicion acumulada negativa.

**Diagnostico contra balances (solo informe; no entra en ninguna cifra).** Contra el snapshot de `clean/balances.parquet` a 2026-09-01, la correlacion de Pearson entre posicion reconstruida y saldo es 0,0087 y el acuerdo de signo 632 de 1.263 empresas (`coverage.json` → `diagnostico_balances`). Es lo esperable: son magnitudes distintas por construccion (la reconstruida es relativa a un origen, el snapshot es un saldo absoluto; ademas la reconstrucion cubre financiacion e inversion, no solo caja bancaria). La consecuencia honesta: **la reconstruccion de caja no tiene ninguna validacion externa**. Se declara.

### 3.3 `xray/payment_delay.py` → `reports/payment_delay/`

**Problema que resuelve.** v3 penaliza la mora (`beta * mora_ratio`). Esta capa produce esa mora, y la produce point-in-time desde `clean/invoices.parquet`: en el corte del mes m, una factura cuenta si su vencimiento es anterior a m, sin mirar lo que paso despues.

Definiciones del modulo:

- Lado `outflow` = cartera de pago (AP); lado `inflow` = cartera de cobro (AR). El scorer usa el lado **PAGO** (`mora_ratio`, no `cobro_mora_ratio`).
- `payment_date` solo cuenta como fecha real de pago si `status_norm='paid'`; un pago posterior al corte no se usa para desmoralizar el corte pasado, y una factura pagada despues de su vencimiento cuenta como en mora en los cortes intermedios (test 1 de `tests/test_payment_delay.py`).
- `mora_ratio = vencida no liquidada / exigible`, en [0, 1]; exigible 0 o agujeros de FX dejan la ratio en NULL con intervalo declarado, nunca un valor inventado.
- `retraso_medio_dias_pagado`: media ponderada por importe de `payment_date - due_date` sobre lo ya pagado y observable en el corte.

**Cobertura medida (`reports/payment_delay/coverage.json`):**

- Grano empresa-mes, meses cerrados: 30.864 filas, 1.286 empresas, 24 meses.
- `mora_ratio` calculable en **12.841 de 30.864 empresa-mes (41,6%)** y **691 de 1.286 empresas**. El resto no tiene cartera observable en el corte: NULL con motivo, no cero.
- Distribucion de `mora_ratio`: mediana 0,17, p75 0,75, p90 1,0.
- Retraso medio de lo pagado: mediana 20,9 dias, p75 45,8, p90 91,8.
- **503 empresas no tienen cartera nunca**: nunca reciben penalizacion de mora.

## 4. Como se construyen C, P y D

El adaptador `xray/scoring_io_v3.py` parte de `marts/panel_flujos.parquet` y `marts/panel_deuda.parquet` y aplica la capa de resolucion sobre los movimientos de caja:

| Clase resultante | Tratamiento en el motor |
|---|---|
| `internal_transfer` | **Excluido de todo**: no es C, ni P, ni D |
| `operating_in` | Suma a C (importe absoluto conocido) |
| `operating_out` | Suma a P |
| `ambiguo` | No suma a ningun total; su importe se acumula aparte como `volumen_ambiguo_eur` de la empresa-mes |
| Ausente de `resolution.parquet` | Conserva su clase original de `interim/tx_flow_class.parquet`, exactamente como en el panel; las clases originales ambiguas (`transfer`/`unknown`/`non_economic`) no suman y dejan C o P desconocidos por direccion |

D (servicio de deuda) proviene de `panel_deuda.parquet` y la resolucion no lo altera.

**Nulo nunca es cero.** Una entrada cuya direccion o importe no se pueden demostrar no se imputa: se pasa al motor como `None`, se suma solo lo conocido, y la confianza declarada de la nota comunica el hueco. `xray/scoring_v3.py` excluye los None de todas las sumas y nunca interpreta ausencia como valor.

## 5. Que NO entra, y por que

Cada exclusion es deliberada y declarada:

- **`marts/targets_proxy.parquet` y `reports/label_review/`.** Son etiquetas *proxy* construidas para auditar el algoritmo, no verdad de negocio. No se usan para ajustar nada y la calibracion de parametros tiene **prohibido** leerlas (declarado en `xray/calibrate_params.py`).
- **Las columnas de stock de `panel_cobro.parquet`** (AR/AP vencido, aging, DSO...). `es_reconstruccion_retrospectiva` es TRUE en sus 30.864 filas (verificado): el panel se calculo hacia atras desde el final del historial, con los pagos posteriores ya descartados. Usarlas en una serie historica seria fuga de informacion. De ese mart solo se consumen las cohortes `coh_pct_cobrado_Nd` cuando `coh_Nd_available_at <= corte` (verificado en el contrato de `xray/payment_delay.py`).
- **`clean/balances.parquet`.** Solo como diagnostico de cordura (seccion 3.2), nunca como entrada de la nota.
- **ERP obligatorio: descartado.** 785 de 1.286 empresas aparecen con ERP alguna vez (`reports/quality/panel_cobro.json` → `empresas.con_erp`; el criterio del mart es que la empresa aparece en facturas). Exigir un ERP activo dejaria sin nota al resto de la cartera; fue el error de diseno de `rules_v1`.
- **Entrenamiento, etiquetas, normalizacion entre empresas y comparacion con otras empresas.** No hay modelo entrenado (`reports/label_review/round1/manifest.json` → `trained_health_model: false`), no hay normalizacion entre empresas y `R_hist` es estrictamente la historia propia.

## 6. Parametros y su estado de calibracion

| Parametro | Valor actual | Estado | Fuente |
|---|---|---|---|
| k | 4.178,45 EUR | **MEDIDO** (provisional, ver abajo) | `reports/calibration_k/report.md` |
| alpha | 3,0 | PROVISIONAL, sin calibrar | `reports/score_v3_previo_defectuoso/summary.json` → `advertencia` |
| beta | 0,25 | PROVISIONAL, sin calibrar | idem |

**k = 4.178,45** es la medida (`reports/calibration_k/report.md`): la k mas pequena del barrido que cumple `|dH| p75 < 8` (su p75 es 7,60; con k=0 es 8,10). Dos hallazgos de esa medicion:

- La ventana de 6 meses sola hace casi todo el trabajo de estabilidad: con k=0 el p75 de `|dH|` ya baja a 8,10, y el shrinkage solo aporta medio punto (8,10 → 7,60). *Nota de verificacion: la cifra de "linea de partida antes de v3" p75 = 29,5 que se me dio no la he podido reproducir contra ningun artefacto; lo mas cercano que puedo medir es la fila k=0 del barrido (8,10). Se declara como cifra aportada, no verificada.*
- Subir k gana estabilidad pero destruye reactividad: en el barrido la reactividad pasa de 5 meses (k=0) a 24 meses (k grande); a partir de k ~ 1,9e5 la reactividad ya es 12 meses o mas (`calibration_k/report.md`, tabla del barrido).

**Provisionalidad de k.** Esa k se midio **antes de R3** (capa de resolucion de transferencias), sobre el subconjunto de calibracion con C, P y D determinados: 3.390 de 21.036 empresa-mes con actividad, con un sesgo adverso de 17x (volumen mediano 21.742,82 EUR frente a una cota inferior de 370.681,88 EUR para el resto). Es provisional y debe revalidarse tras resolver la ambiguedad.

**alpha y beta.** Pendientes de calibracion. `xray/calibrate_params.py` existe y esta probado (`tests/test_calibrate_params.py`), pero su barrido real no se ha ejecutado todavia. Cuando se ejecute, la calibracion sera por **sensibilidad y estabilidad, nunca contra etiquetas**; y si alpha o beta no demuestran influencia medible, la recomendacion sera ponerlos a cero.

## 7. Disciplina point-in-time

**Lo que la garantiza.** Cada capa derivada tiene un test que anade informacion posterior al corte y comprueba que el valor de ese corte no cambia:

- `tests/test_cash_position.py` → `test_movimientos_posteriores_no_cambian_cortes_anteriores`.
- `tests/test_payment_delay.py` → `test_1_point_in_time_vencida_en_m_pagada_en_m2` (y `test_7_cohorte_no_disponible_en_el_corte` para la fuga por cohortes).
- `tests/test_scoring_io_v3.py` → `test_point_in_time_future_movements_do_not_change_score` (la nota del corte es invariante a movimientos posteriores).
- El adaptador filtra todas las capas por `month <= as_of` y trata `available_at` como fin del propio mes (`xray/scoring_io_v3.py`, cabecera).

**Donde queda residuo, y se declara.** `clean/invoices.parquet` no tiene fecha de importacion ni de cancelacion en el sistema origen: puede demostrarse que una factura estaba VENCIDA antes del corte, pero no que estuviera REGISTRADA en el sistema en ese momento. La medida point-in-time es por vencimiento, no por disponibilidad de registro certificada (`reports/payment_delay/coverage.json` → `limitacion_residual`).

## 8. Defectos abiertos a dia de hoy (EN CORRECCION)

`xray/scoring_v3.py`, `xray/scoring_io_v3.py` y sus tests estan siendo modificados AHORA MISMO por otra tarea. Este apartado documenta el estado medido antes de la correccion y marca que se esta corrigiendo. Los artefactos de referencia del estado previo son `reports/score_v3_previo_defectuoso/` y `reports/score_charts/healthscore_v3_real_preliminar/`.

1. **112 empresas del corte 2026-08-31 con nota >= 99,99, todas con p6 = 0 y d6 = 0** (verificado sobre `healthscore_v3_real_preliminar/index.html`: 112 empresas con score >= 99,99 y t6 = 0 en el ultimo corte). Causa raiz: con T6 = 0, `r_hist = C_hist/(C_hist+0) = 1`, el termino k iguala numerador y denominador y H sale 100 automatico. **Arreglo en curso: guarda sobre P6** (no sobre T6; D6 = 0 es legitimo).
2. **807 de las 959 notas del ultimo corte salen con confianza 'ninguna'** (verificado en el mismo artefacto). Causa raiz: `d_eur` queda desconocido en los 6 meses de la ventana para 747 de 1.286 empresas (58%) porque `xray/marts/deuda.py` anula `servicio_deuda_eur` cuando hay cualquier salida ambigua en el mes (`n_ambiguos_salida > 0`); el motor metia D en el mismo cubo de deduccion de confianza que C y P y la confianza colapsaba. **Arreglo en curso: excluir D de esa deduccion.**
3. **327 empresas sin nota** en el ultimo corte (959 con nota + 327 = 1.286) por denominador nulo, con `c6 = 0` y `t6 = 0`; su motivo pasara a `sin_flujos_observados_en_la_ventana`.

## 9. Cifras de salida actuales (corte 2026-08-31, ANTES de la correccion)

Todas verificadas contra `reports/score_v3_previo_defectuoso/summary.json` (y el grafico `healthscore_v3_real_preliminar`, que renderiza el mismo parquet):

- 959 de 1.286 empresas con nota; 8 sin actividad; 319 con denominador nulo.
- `health_score`: p10 3,30 / p50 68,55 / p90 100,00.
- `|dH|` mes a mes sobre el panel (15.003 pares): p50 1,12 / p75 6,38 / p90 17,65. La linea de partida antes de v3 era p75 29,5 (cifra aportada; ver nota de verificacion en la seccion 6).
- 2.364 empresa-mes (7,7% de 30.864) con `volumen_ambiguo_pct > 0` (`diagnostico.volumen_ambiguo_pct.empresa_mes_con_ambiguo`).

## 10. Trazabilidad y reproduccion

Comandos exactos (interprete `.venv/bin/python`, `-B` para evitar bytecode). **Todos los generadores abortan si su ruta de salida existe, por diseno**: se pasa siempre una ruta nueva con `--output` para conservar los artefactos previos.

| Capa / salida | Comando | Artefacto |
|---|---|---|
| Resolucion de transferencias | `.venv/bin/python -B -m xray.transfer_resolve --output reports/transfer_resolution_v2` | `reports/transfer_resolution_v2/{resolution.parquet,coverage.json}` |
| Posicion de caja | `.venv/bin/python -B -m xray.cash_position --output reports/cash_position` | `reports/cash_position/{cash_position_monthly.parquet,coverage.json}` |
| Mora de pago y cobro | `.venv/bin/python -B -m xray.payment_delay reports/payment_delay` (ruta como argumento posicional) | `reports/payment_delay/{payment_delay_monthly.parquet,coverage.json}` |
| Scoring completo (24 cortes) | `.venv/bin/python -B -m xray.scoring_io_v3 --output reports/score_v3` | `reports/score_v3/{assessments.parquet,summary.json}` |
| Grafica mensual | `.venv/bin/python -B -m xray.score_chart_v3 --output reports/score_charts/healthscore_v3/index.html` | HTML autonomo con selectores de empresas |
| Revision de etiquetas (ciega) | `.venv/bin/python -B -m xray.label_review --output reports/label_review/round1` | paquete ciego + `manifest.json`; `round1` es inmutable, no regenerar sobre el |
| Calibracion de k | `.venv/bin/python -B -m xray.calibrate_k --output reports/calibration_k` | `reports/calibration_k/{report.md,sweep.csv}` |
| Calibracion de alpha/beta | `.venv/bin/python -B -m xray.calibrate_params --input reports/score_v3/assessments.parquet --output reports/calibration_params` | barrido aun no ejecutado |

Notas de trazabilidad:

- Las capas de v3 son **derivadas de solo lectura**: no modifican `xray/flows.py` ni los marts publicados ni los ficheros de `data/`. Los tests de CLI lo comprueban comparando sha256 de las fuentes antes y despues (`test_transfer_resolve.py`, `test_cash_position.py`, `test_payment_delay.py`, `test_scoring_io_v3.py`).
- `reports/label_review/round1/manifest.json` congela la semilla (`health-review-20260919-v1`) y los `sha256` de las fuentes de datos y del codigo con los que se genero el paquete. El revisor humano solo debe abrir el `blind_packet` (`transactions.csv`, `company_months.csv`, `cases.json`, `index.html`), nunca los ficheros `not_blind`.
- El generador de graficas aborta si el HTML de salida existe y exige `--output` nuevo, igual que los generadores de informes de las tres capas.

## Notas de verificacion (resumen del ejecutor)

Cifras verificadas contra artefactos y que coinciden con el encargo: recuentos de las fuentes primarias (seccion 2); bucket ambiguo 171.173 filas / 110.631 M EUR sobre un perimetro de 297.537 M EUR (37,2%); R1, R2 estricta y laxa, y R3 fila a fila; ambiguo 116.304/48.228 M → 19.396/812 M con residuos y empresa-mes a cero; cotas de volumen interno y 2,34% de `counterparty_id`; cobertura, confianza, cobertura de meses, posiciones negativas y diagnostico de balances de la capa de caja; calculables, distribuciones y empresas sin cartera de la capa de mora; las cifras del corte previo a la correccion (seccion 9) y los tres defectos (112, 747/58%, 807, 327); k, criterios y sesgo del informe de calibracion; 785 empresas con ERP segun `reports/quality/panel_cobro.json`; `es_reconstruccion_retrospectiva` TRUE en las 30.864 filas de `panel_cobro`.

Tres discrepancias y una cifra no verificada, todas marcadas en el texto:

1. `internal_transfer` con confianza media en R3: mido **80.407** filas (el encargo decia 86.120).
2. `SCF-AJUS.SALDO` residual ambiguo: mido **273,7 M EUR** (el encargo decia 171 M; las 9.137 filas coinciden).
3. Linea de partida p75 = 29,5 antes de v3: **no la he podido reproducir** contra ningun artefacto; la declaro como cifra aportada.
