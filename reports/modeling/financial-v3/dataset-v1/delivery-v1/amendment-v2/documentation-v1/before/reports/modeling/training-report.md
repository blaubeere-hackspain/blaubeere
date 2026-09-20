# Informe consolidado de entrenamiento y evaluación — v1 y v2

Fecha de los experimentos documentados: **19 de septiembre de 2026**. Modelo supervisado: **`experiment-v1:hgb_leaf15`**, protocolo **`deficit-v1`**. Extensión de trayectoria y evaluación de alertas: **`trajectory-v2`**.

Este es el informe de referencia de las dos etapas. Describe ejecuciones y resultados contrastados con sus artefactos; esta consolidación documental no reentrena, no vuelve a evaluar reservas ni cambia métricas. Las secciones **1–12 conservan el entrenamiento y los resultados originales de v1**; las secciones **13–20 incorporan la metodología, resultados, entrega y limitaciones de v2**.

El checkpoint `c6174882ceff0f8697bb64cbec62cd2b83c2b204` conserva la implementación y resultados de v1, no los cambios posteriores de v2. Los manifests y recibos de cada etapa identifican código, datos y parámetros por sus hashes.

## Lectura rápida: resultados que no deben confundirse

| Etapa | Qué se construyó y evaluó | Resultado respaldado | Límite principal |
| --- | --- | --- | --- |
| **V1: clasificador entrenado** | Probabilidad del proxy de déficit en al menos dos de los próximos tres meses; selección de cuatro candidatos y test reservado por grupo/tiempo | AP **0,815603**; Brier **0,188429** frente a **0,251729** del baseline, mejora relativa **25,15 %** | 131 etiquetas observables de 299 casos; datos sintéticos, disponibilidad retrospectiva y probabilidad no calibrada |
| **V2: reglas y backtest, sin nuevo entrenamiento** | Trayectoria observada y reglas fijas de dirección/confirmación, evaluadas contra transiciones sostenidas de mejora y deterioro | Candidato: **2/37 mejoras** y **1/41 deterioros** anticipados; falsas alertas resolubles **96,000 %** y **98,039 %** | **Anticipación no validada**; solo revisión descriptiva, sin alertas predictivas automáticas |

V2 **no es un reentrenamiento ni una nueva versión de los pesos de HGB**. La AP de v1 y el recall de eventos de v2 responden a objetivos, unidades y poblaciones diferentes: no son una comparación antes/después del mismo modelo. Las probabilidades v1 se reutilizan en la vista temporal, pero no definen la dirección ni se evaluaron como predicciones de los eventos v2.

Ninguna etapa certifica un score oficial de salud financiera o resultados del leaderboard. El usuario comunica que el test oficial podría cambiar/cancelarse; no hay confirmación del organizador ni target, métrica o formato oficiales confirmados.

**Guía de lectura:**

- Secciones 1–12: entrenamiento, selección y test de v1.
- Secciones 13–15: reglas, eventos y protocolo temporal de v2.
- Secciones 16–17: resultados completos, cobertura e incertidumbre de v2.
- Secciones 18–20: casos, API/UI, verificación, incidencias y conclusión conjunta.

## 1. Resumen ejecutivo de v1

Se entrenó **un clasificador conjunto para todas las empresas elegibles**, no un modelo por empresa. Estima si habrá déficit operativo en al menos dos de los tres meses naturales siguientes, dentro del perímetro transaccional observado.

Se compararon un baseline de prevalencia, dos configuraciones de regresión logística y dos de gradient boosting con histogramas. La selección se hizo exclusivamente con dos validaciones temporales sobre grupos empresariales separados del entrenamiento. Después se ajustó el candidato elegido con el conjunto de entrenamiento final y se abrió una sola vez el test de grupos reservados y fechas posteriores.

| Resultado en el test final | Baseline constante | Modelo seleccionado |
| --- | ---: | ---: |
| Average precision (AP) | 0,511450 | **0,815603** |
| Brier, menor es mejor | 0,251729 | **0,188429** |
| Mejora relativa de Brier frente al baseline | — | **25,15 %** |
| ROC-AUC, diagnóstico secundario | — | 0,790112 |

El modelo superó los criterios predefinidos tanto en las dos validaciones como en el test final. Sin embargo, **las métricas finales corresponden a 131 etiquetas observables de 299 filas elegibles prospectivamente, distribuidas en 31 grupos**. Las otras 168 etiquetas se conservaron como desconocidas; no se convirtieron en negativos.

**Alcance:** datos sintéticos y reconstrucción retrospectiva. La salida es una estimación de probabilidad de un proxy operativo, no una probabilidad calibrada de quiebra, un score oficial de salud financiera, una previsión de saldo ni una recomendación automática de crédito.

Fuentes: [selección](experiment-v1/selection.json), [evaluación final](experiment-v1/final_report.json) y [registro de ejecución](experiment-v1/execution_summary.json).

## 2. Datos utilizados y controles previos

### 2.1. Snapshot y unidad de observación

- Dataset sintético de **1.286 empresas y 250 grupos empresariales**.
- Calendario analítico cerrado: septiembre de 2024 a agosto de 2026. El mes abierto de septiembre de 2026 no se trata como un mes completo.
- Unidad del modelo: **empresa × mes cerrado**, con importes analíticos en EUR cuando la conversión es verificable. No es una consolidación indiscriminada de monedas.
- Run de datos: `20260919T124814Z-e5302b58`.
- Predictores derivados únicamente de `panel_flujos.parquet`.
- Etiquetas procedentes de `targets_proxy.parquet`, separadas de los predictores.

El mart de flujos representa cuentas de caja del perímetro `checking`, `saving` y `wallet`, con movimientos contabilizados (`booked`) en meses cerrados. Se conserva la incertidumbre de clasificación mediante cotas operativas. Este perímetro **no equivale a todas las cuentas o toda la actividad de la empresa**.

El pipeline procesa los ocho CSV originales, pero el modelo no usa como features facturas, saldos actuales, snapshots de deuda ni el objetivo alternativo de cobro. Tampoco usa `company_id`, `group_id`, metadatos finales de la empresa o indicadores de actividad futura. Los identificadores sirven para relacionar filas y hacer particiones.

Fuentes: [diccionario](../../data_dictionary.md), [contrato de features](protocol-v1/feature_contract.json), [construcción de flujos](../../xray/marts/flujos.py) y [manifest del build](../build_manifest.json).

### 2.2. Puerta de preparación de datos, G-DATA

Antes del primer ajuste se materializaron los archivos Git LFS y se reconstruyó el pipeline. Los controles verificaron:

1. SHA-256 y tamaño de los ocho raw frente a sus OID de LFS, sin modificar los originales.
2. Conservación de filas; un recuento independiente con `csv.reader` se comparó con los Parquet de ingesta.
3. Claves, ownership, grupos, perímetro transaccional, FX y reconciliación de agregados.
4. Disponibilidad temporal declarada y ausencia de evidencia futura en la clasificación transferida.
5. Integridad del run, outputs, fuentes y logs de verificación.

Los **19 controles estructurales** terminaron sin violaciones. La verificación inmediatamente anterior al ajuste real pasó **123 tests**, el control de calidad y las ocho comparaciones CSV/Parquet. La verificación final del conjunto ampliado pasó 161 tests; son etapas distintas, no dos tamaños del conjunto de entrenamiento.

La disponibilidad se modela como cierre de mes, bajo un supuesto retrospectivo aprobado. No hay un historial completo de importaciones, cambios de estado o revisiones de categorías que permita certificar qué información estaba realmente disponible en cada fecha histórica.

La comparación con el build histórico conservó su diagnóstico numérico estricto. Para ruido de agregación flotante se aprobó mantener céntimos y signos redondeados idénticos en las cuatro cotas que determinan el target; los demás agregados monetarios admiten diferencias inferiores a 0,01 EUR, con controles de escala, nulidad y finitud. No se modificaron importes o etiquetas para mejorar resultados.

Fuentes: [auditoría de datos](data_gate.json), [verificador](../../xray/model_audit.py) y [ejecución previa al entrenamiento](experiment-v1/execution_summary.json).

## 3. Qué se predice: definición del target

El objetivo es **`target_deficit_3m`, versión 1**. Para un origen mensual `m`, el desenlace utiliza `m+1`, `m+2` y `m+3`, y madura el último día de `m+3`.

Cada mes futuro tiene una cota inferior y superior del neto operativo, que representan la incertidumbre sobre la clasificación de los movimientos ambiguos:

- Déficit definido si la cota superior, redondeada a céntimos, es negativa.
- Ausencia de déficit definida si la cota inferior, redondeada a céntimos, es no negativa.
- Estado mensual desconocido en los demás casos.

El target es positivo si hay al menos dos meses de déficit confirmado. Es negativo si ni siquiera contando los meses desconocidos como posibles déficits se alcanzarían dos. En los demás casos queda desconocido. Además, la conclusión debe coincidir al calcular las cotas **con y sin los atípicos identificados por el pipeline**.

Se exige actividad de caja en el origen y el horizonte futuro, salir del calentamiento y que el horizonte esté completo y maduro. La falta de observabilidad, incertidumbre material o sensibilidad a atípicos puede dejar la etiqueta en `NULL`.

**Etiqueta desconocida no significa clase 0.** Las filas sin desenlace utilizable no se emplean para ajustar ni puntuar el clasificador, aunque su cobertura se registra. La elegibilidad para emitir una predicción, en cambio, se determina solo con los inputs disponibles en el origen, sin exigir conocer el futuro.

Fuentes: [implementación del target](../../xray/marts/targets.py) y [protocolo congelado](protocol-v1/protocol.json).

## 4. Construcción de las 32 features

Se fijó una lista ordenada de **12 features del mes de origen y 20 de ventanas históricas**. La preparación no ajustó transformaciones estadísticas ni consultó valores del target: guardó contratos, grupos e índices y calculó hashes de los inputs.

### 4.1. Features del mes de origen

`gross` es `volumen_caja_conocido_eur`: volumen absoluto de movimientos de caja, positivo y con EUR completo para el mes utilizado. Los ratios no se dividen entre un neto próximo a cero.

| Feature | Definición |
| --- | --- |
| `operating_min_ratio` | Cota inferior operativa / `gross` |
| `operating_max_ratio` | Cota superior operativa / `gross` |
| `net_ratio` | Neto de caja / `gross` |
| `operating_in_ratio` | Cobros operativos identificados / `gross` |
| `operating_out_ratio` | Pagos operativos identificados / `gross` |
| `financing_in_ratio` | Entradas de financiación / `gross` |
| `financing_out_ratio` | Salidas de financiación / `gross` |
| `uncertainty_ratio` | Entradas y salidas ambiguas, en valor absoluto conocido / `gross` |
| `log_gross` | `log1p(gross)` |
| `log_tx` | `log1p(n_tx_caja)` |
| `log_accounts` | `log1p(n_cuentas_activas)`; cuentas observadas en el mes, no actividad contractual certificada |
| `operating_identified_ratio` | `operating_in_ratio - operating_out_ratio` |

Las variables de financiación describen movimientos observados, no stocks de deuda ni obligaciones contractuales futuras.

### 4.2. Features de ventanas

Cada familia siguiente se calcula con sufijos `_3m` y `_6m`, dando diez variables por ventana:

| Familia | Cálculo |
| --- | --- |
| `net_ratio_mean` | Media del ratio neto |
| `net_ratio_std` | Desviación estándar poblacional del ratio neto |
| `net_ratio_slope` | Pendiente por mes mediante mínimos cuadrados |
| `operating_min_ratio_mean` | Media de la cota inferior relativa |
| `operating_max_ratio_mean` | Media de la cota superior relativa |
| `log_gross_mean` | Media del logaritmo de volumen |
| `log_gross_std` | Desviación estándar poblacional del logaritmo de volumen |
| `deficit_frequency` | Media de estados históricos de déficit, solo si todos son conocidos y robustos a atípicos |
| `net_ratio_delta_prior` | Ratio neto actual menos la media de los `n` meses anteriores, excluyendo el actual |
| `log_gross_delta_prior` | Logaritmo de volumen actual menos la media de los `n` meses anteriores, excluyendo el actual |

Las ocho primeras familias incluyen el mes de origen en la ventana. Las dos diferencias utilizan el mes actual y una ventana de `n` meses estrictamente anteriores; por tanto, pueden necesitar un mes adicional de historia.

### 4.3. Elegibilidad y faltantes

Para que una fila sea elegible se requiere:

- Actividad de caja, ningún movimiento con EUR desconocido (`n_sin_eur=0`) y `gross` positivo en el mes actual y los dos anteriores.
- `es_calentamiento=false` y primera actividad histórica observada al menos tres meses antes del origen.
- Filas históricas disponibles a más tardar en el cierre del origen.

Las ventanas se construyen por **meses naturales consecutivos**, no comprimiendo huecos. Un mes ausente, sin actividad, con FX desconocido o volumen no utilizable deja la feature afectada como desconocida; nunca se rellena ese hecho con cero. No disponer de seis meses válidos puede dejar features de seis meses vacías sin impedir por sí solo la predicción.

`coverage` y `missing_reason` se conservan como metadatos de evidencia, no como IDs, penalizaciones fijas de salud o columnas adicionales de calidad en la matriz. Esto no impide que el tratamiento de valores ausentes del estimador aporte información de missingness.

En la preparación de los orígenes requeridos por las particiones hubo **17.664 filas candidatas y 7.132 elegibles**. No son todas las combinaciones del panel ni 7.132 ejemplos independientes; hay empresas repetidas y ventanas solapadas. Los conjuntos de entrenamiento de distintas etapas también se solapan deliberadamente.

Fuentes: [features](../../scripts/model_features.py), [contrato completo y orden](protocol-v1/feature_contract.json) y [cobertura estructural](protocol-v1/coverage.json).

## 5. Particiones: grupos separados y fechas posteriores

No se hizo un reparto aleatorio por filas. Primero se ordenaron los 250 grupos por:

```text
sha256(UTF-8("deficit-v1:" + group_id))
```

Se asignaron **150 grupos a entrenamiento, 50 a validación y 50 al test final**, antes de filtrar elegibilidad. Las empresas del mismo grupo permanecen en la misma partición.

A continuación se aplicaron ventanas temporales y una purga por maduración: una fila de entrenamiento solo puede entrar si su etiqueta ya habría madurado antes del inicio de la ventana de evaluación. No basta con que su mes de origen sea anterior.

| Etapa | Grupos disponibles para ajustar | Orígenes elegibles de entrenamiento | Etiquetas maduras hasta | Orígenes evaluados | Etiquetas de evaluación hasta |
| --- | --- | --- | --- | --- | --- |
| Fold 1 | Partición train | Diciembre 2024–marzo 2025 | 2025-06-30 | Julio–septiembre 2025, partición validation | 2025-12-31 |
| Fold 2 | Partición train | Diciembre 2024–junio 2025 | 2025-09-30 | Octubre–diciembre 2025, partición validation | 2026-03-31 |
| Ajuste y test finales | Particiones train + validation | Diciembre 2024–diciembre 2025 | 2026-03-31 | Abril–mayo 2026, partición final_test | 2026-08-31 |

Se eligieron **dos folds**, no cinco, por los 24 meses de historia, el horizonte de tres meses y la separación por grupos. Los dos folds reutilizan la partición de grupos de validación en fechas diferentes: no representan dos universos de grupos independientes.

Al decidir el ajuste final, el último resultado utilizado para seleccionar el modelo ya había madurado el 31 de marzo de 2026. El test final utiliza otros grupos y orígenes posteriores. Evalúa generalización a grupos no usados para entrenar; puede utilizar su historia de inputs para construir features, pero no sus etiquetas para ajustar el modelo.

Fuentes: [protocolo](protocol-v1/protocol.json), [asignación de grupos](protocol-v1/group_assignment.json) y [implementación de particiones y cargador de etiquetas](../../scripts/model_protocol.py).

## 6. Cuántos ejemplos se usaron realmente

La existencia de features elegibles no garantiza que el desenlace sea observable. Antes de calcular métricas se exigieron, en **cada muestra de entrenamiento y evaluación**, al menos 20 filas y cinco grupos por clase, además de diez grupos totales. Todos los conjuntos cumplieron ese mínimo; es una condición de viabilidad, no una demostración de potencia estadística.

| Conjunto | Filas elegibles por inputs | Etiquetas observables | Clase 0 | Clase 1 | Etiquetas desconocidas | Grupos con etiquetas observables |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fold 1, train | 1.209 | 654 | 342 | 312 | 555 | 49 |
| Fold 1, validación | 358 | 164 | 82 | 82 | 194 | 21 |
| Fold 2, train | 2.507 | 1.362 | 751 | 611 | 1.145 | 70 |
| Fold 2, validación | 386 | 175 | 76 | 99 | 211 | 23 |
| Entrenamiento final | 6.833 | **3.630** | 1.930 | 1.700 | 3.203 | **112** |
| Test final | 299 | **131** | 64 | 67 | 168 | **31** |

Estos grupos observados no deben confundirse con los 150/50/50 grupos asignados inicialmente. Por ejemplo, los 299 orígenes prospectivos de test pertenecen a 42 grupos, pero solo 31 aportan al menos una etiqueta utilizable. Un grupo puede contener ejemplos de las dos clases; los recuentos por clase de los JSON no se suman para obtener grupos únicos.

Fuentes: [soporte de desarrollo](experiment-v1/development_support.json), [soporte del ajuste final](experiment-v1/final_training_support.json), [cobertura](protocol-v1/coverage.json) y [test final](experiment-v1/final_report.json).

## 7. Baseline, candidatos y preprocesamiento

### 7.1. Baseline

En cada ajuste se calculó una probabilidad constante:

```text
p_base = media de las etiquetas observadas de entrenamiento
```

Se aplicó esa misma constante a todas las filas de la evaluación correspondiente. **No se estimó con la prevalencia de validación o test.** En el entrenamiento final fue `1700 / 3630 = 0,468320`.

La AP de un predictor constante coincide con la prevalencia de la muestra evaluada. Por eso el baseline de AP del test es 0,511450, aunque la probabilidad constante emitida por el baseline sea 0,468320.

No se ejecutó otro baseline de persistencia, un ensemble, una red neuronal ni una búsqueda AutoML. La comparación registrada es contra la prevalencia de entrenamiento.

### 7.2. Cuatro candidatos fijados antes de cargar las etiquetas de desarrollo

| Candidato | Familia | Diferencia explorada |
| --- | --- | --- |
| `logistic_c0.1` | Regresión logística | `C=0.1` |
| `logistic_c1` | Regresión logística | `C=1.0` |
| `hgb_leaf7` | `HistGradientBoostingClassifier` | `max_leaf_nodes=7` |
| `hgb_leaf15` | `HistGradientBoostingClassifier` | `max_leaf_nodes=15` |

La regresión logística usó un `Pipeline` con imputación por mediana, indicadores de ausencia (`add_indicator=true`, `keep_empty_features=true`), `StandardScaler` y clasificador con `max_iter=2000`, solver `lbfgs` y semilla 1729. **Cada pipeline se ajustó desde cero solo sobre el train del fold**; no se aprendieron medianas ni escalas de validación/test.

En cada ajuste se construyó una matriz `X` con las 32 columnas en el orden congelado y un vector binario `y`, usando las filas del split con inputs elegibles y etiqueta observada. El experimento real realizó **ocho ajustes de desarrollo** (cuatro candidatos por dos folds) y **un ajuste final**; los entrenamientos de los tests con fixtures no forman parte de ese recuento.

El boosting aprende una secuencia de árboles para reducir la pérdida de clasificación `log_loss`; la implementación con histogramas agrupa valores numéricos para buscar particiones. La salida utilizada fue `predict_proba(X)[:, 1]`, correspondiente a la clase de déficit. Recibió las 32 features numéricas y gestionó los valores ausentes de forma nativa. No utilizó la imputación o el escalado de la logística. Sus parámetros comunes fueron:

| Parámetro | Valor |
| --- | ---: |
| `loss` | `log_loss` |
| `learning_rate` | 0,05 |
| `max_iter` | 150 |
| `max_depth` | 3 |
| `l2_regularization` | 5,0 |
| `min_samples_leaf` | 20 |
| `max_bins` | 255 |
| `early_stopping` | `false` |
| `random_state` | 1729 |
| `class_weight` | Sin ponderación de clases |

`max_leaf_nodes` es una cota configurada, no el número de hojas que necesariamente tiene cada árbol; `max_depth=3` también restringe su tamaño. El early stopping aleatorio se deshabilitó para no introducir una validación interna distinta del protocolo temporal. No se aplicaron pesos por empresa/grupo ni remuestreo de clases durante el ajuste.

No hubo selección posterior de features, calibrador de probabilidades ni ajuste del umbral de decisión. El umbral diagnóstico se fijó en **0,5**. El ajuste y la predicción se limitaron a dos hilos.

Fuentes: [configuración congelada](experiment-v1/config.json), [estimadores y métricas](../../scripts/model_learning.py) y [parámetros del modelo persistido](experiment-v1/model_manifest.json).

## 8. Regla de aceptación y selección del ganador

Las condiciones, congeladas antes de comparar resultados, fueron:

```text
AP_modelo > prevalencia_evaluación
Brier_modelo <= 0,95 * Brier_baseline
Brier = media((probabilidad - etiqueta)^2)
Mejora_relativa_Brier = 1 - Brier_modelo / Brier_baseline
```

La métrica registrada como PR-AUC en el objetivo se operacionalizó como **`average_precision_score` (AP)**, no como integración trapezoidal de una curva precisión-recall. AP no es accuracy ni la precisión en el umbral 0,5.

Un candidato debía satisfacer **ambas condiciones en ambos folds**, con soporte suficiente. Entre candidatos válidos se escogió el menor promedio simple de Brier de los dos folds, sin ponderarlo por el número de filas. Un empate exacto favorecería logística y después el identificador del candidato. El test final no participó en esta decisión.

| Candidato | AP fold 1 | Brier fold 1 | AP fold 2 | Brier fold 2 | Brier medio |
| --- | ---: | ---: | ---: | ---: | ---: |
| Logística, C=0,1 | 0,691519 | 0,217001 | 0,780279 | 0,222666 | 0,219834 |
| Logística, C=1 | 0,676060 | 0,217362 | 0,775759 | 0,223698 | 0,220530 |
| HGB, límite de 7 hojas | 0,705701 | 0,201751 | 0,856234 | 0,186082 | 0,193917 |
| **HGB, límite de 15 hojas** | **0,698764** | **0,202851** | **0,864441** | **0,184657** | **0,193754** |

Referencias del fold 1: prevalencia de evaluación 0,500000 y Brier baseline 0,250526. Referencias del fold 2: 0,565714 y 0,259396, respectivamente.

Los cuatro candidatos pasaron las condiciones. Ganó **`hgb_leaf15`** por la regla de Brier medio; no por tener la mejor AP en todos los folds. Su ventaja media sobre `hgb_leaf7` fue pequeña: no se presenta como una diferencia de superioridad estadística demostrada.

Fuentes: [resultados completos de desarrollo](experiment-v1/development.json) y [decisión persistida](experiment-v1/selection.json).

## 9. Ajuste final y test reservado

Con la selección guardada, se ajustó un modelo nuevo del candidato elegido sobre las **3.630 etiquetas observables del entrenamiento final**, correspondientes a 112 grupos y con maduración máxima a 2026-03-31. El modelo se serializó en `model.joblib` y se registraron sus parámetros, entorno, fuentes y hashes **antes de acceder a las etiquetas del test**.

El evaluador creó mediante escritura exclusiva y `fsync` un [recibo de evaluación](experiment-v1/evaluation_receipt.json) antes de consultar el holdout. Las etiquetas se limitaron en SQL a las claves, grupos, fechas y maduración de `final_test` antes de recuperarlas. La existencia del recibo impide una repetición automática, incluso tras una interrupción.

Se evaluó **una vez**. No hubo correcciones del modelo, cambios de features, relajación de umbrales ni ajuste de hiperparámetros tras ver el test. Tampoco se reentrenó después con las etiquetas del holdout.

### 9.1. Métricas finales

| Métrica | Valor |
| --- | ---: |
| Filas observables / elegibles por inputs | 131 / 299 |
| Cobertura de etiquetas | 43,81 % |
| Grupos observados | 31 |
| Positivos / negativos | 67 / 64 |
| AP | **0,815603** |
| AP baseline / prevalencia del test | 0,511450 |
| Brier | **0,188429** |
| Brier baseline de prevalencia de train | 0,251729 |
| Mejora relativa de Brier | **25,15 %** |
| ROC-AUC, solo diagnóstico | 0,790112 |
| Precisión a umbral 0,5 | 0,741379 |
| Recall a umbral 0,5 | 0,641791 |

A ese umbral hubo **43 verdaderos positivos, 15 falsos positivos, 24 falsos negativos y 49 verdaderos negativos**. Es un diagnóstico del evento binario en la muestra etiquetada, no una política operativa de alertas aprobada.

### 9.2. Incertidumbre y calibración

Se hicieron **300 réplicas bootstrap por grupo**, con semilla 1729. En cada réplica se muestrearon grupos con reemplazo y se conservaron todas sus filas etiquetadas. No se remuestrearon filas como si fueran independientes. No se descartó ninguna réplica por tener una sola clase.

| Métrica | Intervalo bootstrap del 95 % |
| --- | --- |
| AP | [0,720553; 0,898483] |
| Brier | [0,154371; 0,222095] |
| Mejora relativa de Brier | [12,03 %; 38,07 %] |

Son percentiles 2,5 y 97,5 **condicionados al modelo ya ajustado**: no incorporan reentrenamiento ni toda la incertidumbre de selección del modelo. No son intervalos de confianza de la probabilidad de cada empresa.

El informe también guarda diez intervalos de fiabilidad, Brier agregado por empresa/grupo y diagnósticos de historia/faltantes. Se usaron para describir resultados, no para retocar el modelo. Las cohortes de historia cuentan meses válidos dentro de los seis últimos meses, no la edad de la empresa. **No se aplicó calibración posterior ni se demostró calibración individual.**

Fuentes: [evaluación final](experiment-v1/final_report.json), [evaluador](../../scripts/model_experiment.py) y [bootstrap/diagnósticos](../../scripts/model_learning.py).

## 10. Inferencia posterior y entrega

El mismo modelo final, sin incorporar etiquetas posteriores a su corte de entrenamiento, se aplicó a las features disponibles al **31 de agosto de 2026**. Esas predicciones cubren **septiembre, octubre y noviembre de 2026**: tres meses naturales, no exactamente 90 días.

Se conservaron las 1.286 empresas:

- **1.010** con inputs elegibles y estimación `p_proxy`.
- **276** sin estimación, con `p_proxy=null` y motivos explícitos.

La inferencia de agosto no consultó etiquetas futuras. Su cobertura del 1.010/1.286 no debe confundirse con la cobertura de etiquetas del test ni interpretarse como rendimiento validado sobre ese horizonte futuro.

La API y la app reciben assessments `proxy_only`: caja, buffer y salud quedan en `null`; no se fabrican flujos fechados ni historia de caja. Las features mostradas son evidencia de entrada, no atribuciones causales o puntos de salud. El modelo se entrega para inspección, no para ejecutar pagos o aprobar crédito.

**Jev no intervino en el entrenamiento, las features, las etiquetas o las probabilidades.** Sus cuatro pilotos quedaron preparados offline con ejemplos inventados y mocks; no se enviaron datos a servicios externos ni se promovieron clasificaciones.

Fuentes: [predicciones de agosto](experiment-v1/latest_predictions.json), [export para la app](assessment-v1/manifest.json) y [estado de preparación Jev](jev-pilot-v1/summary.json).

## 11. Entorno, ejecución y reproducción

### 11.1. Entorno registrado

| Componente | Versión |
| --- | --- |
| Python | 3.13.15 |
| DuckDB | 1.5.5 |
| scikit-learn | 1.7.2 |
| NumPy | 2.5.3 |
| SciPy | 1.18.1 |
| joblib | 1.6.0 |
| cloudpickle | 3.1.2 |
| threadpoolctl | 3.6.0 |

Las dependencias quedaron fijadas en [requirements-model.txt](../../requirements-model.txt), generado desde [requirements-model.in](../../requirements-model.in) con exclusión de publicaciones posteriores a 2026-09-12. Semilla 1729; límites `OMP_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, además de `threadpool_limits(2)` y dos hilos DuckDB en el entrenamiento.

### 11.2. Secuencia histórica ejecutada

El pipeline de datos se reconstruyó y verificó antes de preparar el protocolo. Después se implementó y probó el código de entrenamiento y se refrescó G-DATA antes de ajustar modelos. La secuencia relevante fue:

```text
.venv/bin/python -B -m scripts.model_protocol prepare
.venv/bin/python -B -m xray.model_audit --verify
.venv/bin/python -B -m scripts.model_experiment freeze
.venv/bin/python -B -m scripts.model_experiment develop
.venv/bin/python -B -m scripts.model_experiment fit-final
.venv/bin/python -B -m scripts.model_experiment evaluate-final
.venv/bin/python -B -m scripts.model_experiment infer-latest
```

**Este bloque es un registro histórico, no una receta para repetir sobre `experiment-v1`.** La preparación y los artefactos son exclusivos e inmutables. No se deben borrar recibos, sobrescribir directorios ni volver a abrir el test para buscar mejores métricas. Un nuevo entrenamiento exige versionar explícitamente protocolo, configuración y artefactos; los comandos actuales no son un runner genérico para múltiples experimentos.

Detalle de intentos y orden: [execution_summary.json](experiment-v1/execution_summary.json). Durante el modelado real se registraron cero correcciones y una evaluación del holdout; los tests previos con fixtures sí incluyeron el ciclo rojo/verde de desarrollo.

### 11.3. Verificaciones seguras del resultado existente

Desde la raíz del worktree original y con el entorno y snapshot ligado disponibles:

```sh
.venv/bin/python -B scripts/replay_model.py --check
.venv/bin/python -B scripts/export_assessments.py --check
```

El replay verifica los hashes del artefacto local, fuentes y dependencias; reconstruye features de agosto y compara probabilidades, metadatos, elegibilidad y motivos con el JSON guardado. **No entrena, no evalúa el test y no consulta valores del target.** La ejecución registrada reprodujo las 1.286 filas con features/metadatos exactos y **error máximo de probabilidad 0,0**.

El exportador verifica el JSON y sus enlaces de integridad sin cargar el pickle. Un hash verifica identidad, no hace seguro cualquier pickle externo: el cargador del modelo solo admite el artefacto local fijado.

**Límite de portabilidad:** `data/runs/` es almacenamiento local ignorado por Git y los manifiestos del experimento fijan una ruta absoluta, hashes de fuentes y versiones exactas. El checkpoint contiene código, modelo y resultados, pero un clon nuevo no recrea automáticamente el snapshot local ni permite ejecutar el replay sin restaurar ese contexto. Rehacer el build o modificar hashes para que pasen los checks no equivale a reproducir el experimento original.

Fuentes: [replay observado](final-verification/replay.json), [código de replay](../../scripts/replay_model.py) y [guía de uso de la app](../../readme.md).

### 11.4. Identidad de los artefactos principales

| Artefacto | SHA-256 |
| --- | --- |
| `experiment-v1/model.joblib` | `2ac7cffd080d6c0a22422c90a3b806ae2f053d585eb4b7330078ca9c588bcbf5` |
| `experiment-v1/model_manifest.json` | `9b30c24feeb75fb0c2f17bca9a2eb178a508dd3e24759ac53c713424dd531dc8` |
| `experiment-v1/evaluation_receipt.json` | `96916854a53c8933f36a12b40d057044e670b1beb4484d7553bce2f07584ae95` |
| `experiment-v1/final_report.json` | `2b248e2c798e869ca41d540a601662ee9dcbad83d3b0dd30d813e245d786c9a2` |
| `experiment-v1/latest_predictions.json` | `ec6b3d3f813238412420a0627dabf56e1ee7ef38111f92aadb2415d98a770cec` |

El [manifest del modelo](experiment-v1/model_manifest.json) enlaza además los inputs, el protocolo preparado y el código. La [verificación final](final-verification/summary.json) conserva comandos, resultados y hashes, incluyendo la integridad de los raw y del recibo de evaluación.

## 12. Interpretación y límites de v1

La conclusión respaldada es: **este clasificador mejora al baseline constante para el proxy y la población etiquetada definidos por el protocolo, con separación temporal y de grupos**. No permite afirmar precisión universal ni que una empresa tenga una probabilidad concreta de insolvencia.

Las limitaciones principales son:

1. **Datos sintéticos:** no existe validación externa sobre empresas reales ni sobre el conjunto oficial del reto.
2. **Disponibilidad retrospectiva:** estados y categorías proceden de un snapshot final; los tests temporales no sustituyen registros históricos de observación.
3. **Selección de etiquetas observables:** el 56,19 % de los casos prospectivos del test quedó sin etiqueta. Exigir actividad futura y robustez del target puede sesgar la población evaluada.
4. **Muestra final pequeña y correlacionada:** 131 filas y 31 grupos; dos orígenes mensuales y ventanas futuras solapadas. El bootstrap por grupos evita tratarlas como IID, pero no elimina todas las fuentes de incertidumbre.
5. **Referencia limitada:** la mejora se midió contra prevalencia de entrenamiento, no contra todos los baselines financieros posibles. La diferencia entre los dos candidatos HGB fue pequeña.
6. **Sin calibración, explicación causal ni incertidumbre individual:** ni Brier ni los intervalos agregados justifican esas afirmaciones.
7. **Cobertura conservadora:** las abstenciones son parte del producto; reducirlas exige estudiar datos y validar de nuevo, no asignar cero o una probabilidad artificial.

Una futura versión del predictor supervisado podría evaluar calibración y baselines adicionales en nuevos datos de desarrollo, ampliar cobertura con semántica verificada y realizar validación externa. Son propuestas, **no resultados obtenidos**. El test ya utilizado no debe convertirse en conjunto de tuning; cualquier extensión debe tener una nueva evaluación independiente.

## 13. V2: objetivo, datos y ausencia de reentrenamiento

V2 amplió el sistema para responder a preguntas que una probabilidad de déficit aislada no resuelve: **cómo evoluciona una empresa, qué cambió en sus flujos, cuándo aparece una señal y si esa señal anticipa una transición posterior**.

Se conservaron el modelo v1, su configuración, features, recibo del test y resultados. Se implementaron por separado:

1. Una serie mensual de estado operativo y dirección con evidencia numérica.
2. Un candidato de alertas con persistencia de dos meses y un baseline sin esa persistencia.
3. Eventos futuros de mejora/deterioro y un backtest temporal preregistrado.
4. Una vista histórica que reutiliza el predictor v1 únicamente después de su corte válido.

**No se entrenó un modelo supervisado v2, no se ajustaron pesos nuevos ni se calibró HGB.** Los umbrales y reglas de v2 son decisiones de diseño fijadas antes de consultar sus outcomes, no parámetros aprendidos de la reserva. Los pequeños datasets inventados pertenecen a tests de software; las métricas de eventos utilizan el mismo dataset sintético proporcionado por el reto y el mismo `panel_flujos` verificado que v1.

El contrato `trajectory-v2` se escribió antes de medir resultados. Se sellaron código, dependencias e inputs, y se registró un recibo exclusivo antes de cada acceso real de desarrollo y reserva. Hubo una ejecución real de cada fase, sin tuning posterior ni repetición de la reserva. Los tests sobre fixtures y los replays de exportación no son nuevas evaluaciones de esa reserva.

Fuentes: [protocolo v2](trajectory-v2/protocol.json), [recibo de protocolo](trajectory-v2/protocol-receipt.json), [manifest de ejecución](trajectory-v2/execution-manifest.json) y [recibo reservado](trajectory-v2/reserved-access.json).

## 14. V2: estado, dirección y emisión de señales

### 14.1. Estado operativo mensual

Cada empresa conserva todos los meses cerrados entre septiembre de 2024 y agosto de 2026, incluidos huecos y meses no elegibles. El estado toma uno de estos valores:

- `deficit`: cota superior operativa negativa, redondeada a céntimos, tanto en base como sin atípicos.
- `non_deficit`: cota inferior no negativa, redondeada a céntimos, en ambas variantes.
- `insufficient_evidence`: inputs no utilizables, incertidumbre o desacuerdo entre variantes.

La elegibilidad exige mes cerrado presente, actividad de caja, EUR completo, gross finito y positivo y cotas finitas ordenadas. La información ausente no se transforma en cero. **El estado indica nivel de flujo operativo; no saldo de caja ni dirección de cambio.**

### 14.2. Dirección: tres meses frente a los tres anteriores

La dirección utiliza seis meses naturales consecutivos: los tres recientes, incluido el actual, y los tres anteriores. Se calculan medias aritméticas no ponderadas de los ratios mensuales de las cotas operativas sobre el gross observado. No es un ratio de sumas ni se inventa un gross recortado para la variante sin atípicos.

Para cada variante base y sin atípicos:

```text
D_inferior = media(cota_inferior/gross, recientes) - media(cota_superior/gross, anteriores)
D_superior = media(cota_superior/gross, recientes) - media(cota_inferior/gross, anteriores)
```

| Dirección | Regla en ambas variantes |
| --- | --- |
| `improving` | `D_inferior >= 0,05` |
| `deteriorating` | `D_superior <= -0,05` |
| `stable` | Intervalo completo estrictamente dentro de `(-0,05; 0,05)` |
| `insufficient_evidence` | Cualquier otro caso: inputs incompletos, ambigüedad o falta de acuerdo |

El umbral es **cinco puntos porcentuales del gross**, fijado por diseño, no optimizado. Las comparaciones utilizan aritmética binaria de doble precisión sin redondeo visual ni tolerancias añadidas para forzar una clasificación. Las cotas monetarias del estado sí se redondean a céntimos; son operaciones diferentes.

Puede haber una empresa en déficit pero mejorando, o una empresa sin déficit pero deteriorándose. Los seis meses deben tener inputs utilizables, pero no es obligatorio que el estado mensual de cada uno sea clasificable: la incertidumbre de las cotas se propaga a la comparación. Son cotas de asignación operativa, **no intervalos estadísticos de confianza**.

La explicación muestra ventanas, gross, cobros y pagos conocidos, cotas y descomposición:

```text
cambio del neto identificado relativo = cambio de cobros/gross - cambio de pagos/gross
```

Es una identidad aritmética de indicadores observados, **no una atribución causal, SHAP ni una explicación de por qué HGB asigna una probabilidad concreta**.

### 14.3. Señal provisional, candidato y baseline

- **Primer mes direccional:** se registra `watch`, una señal provisional.
- **Candidato:** confirma una alerta en el segundo mes consecutivo del mismo signo, con fecha de ese segundo cierre.
- **Baseline:** emite una alerta al primer mes de cada episodio direccional, sin exigir persistencia.
- Cada método emite una sola alerta por racha. Estabilidad, huecos, evidencia insuficiente o cambio de signo rompen la racha anterior.
- Una racha que cruza un límite de evaluación no se reinicia para fabricar una nueva alerta dentro del periodo.

Se conservan `first_signal_at` y `confirmed_alert_at` por separado. La confirmación no se retrofecha al primer `watch`. Son **señales simuladas retrospectivamente**, no un registro de alertas que se emitieran realmente en esas fechas históricas.

Fuentes: [reglas y umbrales](trajectory-v2/protocol.json), [cálculo de señales](../../scripts/trajectory_signals.py) y [pruebas temporales](../../tests/test_trajectory_signals.py).

## 15. V2: eventos y protocolo de evaluación

### 15.1. Eventos independientes de la señal

Las etiquetas se construyen con el estado operativo robusto, no con los deltas de dirección, las alertas ni `p_proxy`:

| Tipo | Patrón de meses consecutivos |
| --- | --- |
| Deterioro sostenido | Dos meses `non_deficit`, seguidos de dos meses `deficit` |
| Mejora sostenida | Dos meses `deficit`, seguidos de dos meses `non_deficit` |
| Bache recuperado, solo demo | Dos meses `non_deficit`, un mes `deficit` y dos meses `non_deficit` |

En las transiciones sostenidas, el inicio es el primer mes del estado nuevo y la confirmación llega al cierre del segundo. Un hueco o estado insuficiente convierte el patrón en desconocido, no en ausencia de evento. El bache es una clasificación retrospectiva separada: la recuperación se confirma en el último de sus cinco meses y no se utiliza como target sostenido del backtest.

### 15.2. Población y cortes temporales

Se excluyeron **todos los 50 grupos del test v1**, en todas las fechas, antes de detectar eventos, evaluar alertas o elegir casos. La evaluación usa **200 grupos y 1.078 empresas** de las particiones train/validation de v1.

| Fase v2 | Orígenes de alertas | Outcomes disponibles hasta | Inicios posibles de eventos |
| --- | --- | --- | --- |
| Desarrollo | Marzo–septiembre 2025 | 2025-12-31 | Abril–noviembre 2025 |
| Reserva temporal | Marzo–mayo 2026 | 2026-08-31 | Abril–julio 2026 |

La ventana de anticipación es **uno o dos meses naturales antes del inicio del evento**, no antes de su confirmación. Por ejemplo, una alerta emitida al cierre de mayo puede anticipar un inicio en junio o julio; el evento iniciado en julio se confirma al cierre de agosto.

Esta reserva es **temporal y sobre empresas conocidas**, dentro del mismo corpus sintético y con preparación compartida. No se presenta como un nuevo test independiente de grupos desconocidos, una validación externa ni una repetición del test oficial. No se compararon las probabilidades v1 contra estos eventos, porque su target es distinto.

### 15.3. Matching, misses y censura

El denominador de eventos es común a candidato y baseline. Un evento confirmado entra si tiene al menos un origen con inputs de dirección elegibles dentro de su ventana de anticipación y del periodo evaluado; no se exige que el método hubiera emitido una señal correcta.

Dentro de cada empresa y signo, las alertas se procesan por fecha. Cada una se asocia al primer evento futuro elegible no emparejado de esa empresa y signo, a uno o dos meses. Cada alerta y evento se usan como máximo una vez por método.

- Evento elegible sin alerta asociada: **miss**.
- Alerta sin match con seguimiento completo: **falsa alarma**.
- Alerta sin match y seguimiento insuficiente: **censurada/desconocida**, no falsa por defecto.
- Para declarar falsa una alerta de origen `o`, deben ser robustos y observables todos los estados desde `o-1` hasta `o+3`, cubriendo los dos posibles inicios y sus confirmaciones.
- Un evento completamente observado puede dar un match aunque otro tramo del horizonte sea desconocido; la cobertura se informa aparte.
- Las alertas adicionales no desaparecen porque otra alerta ya haya consumido el evento.

```text
Eventos = matches + misses
Alertas = matches + falsas alarmas + censuradas
Recall = matches / eventos elegibles
FAS = falsas alarmas / (matches + falsas alarmas)
```

**FAS es la proporción de falsas entre alertas resolubles, no la tasa de falsos positivos (FPR)**. Las métricas con denominador cero y el lead sin matches quedan `null` con motivo explícito.

Fuentes: [protocolo](trajectory-v2/protocol.json), [eventos y matching](../../scripts/trajectory_events.py) y [métricas](../../scripts/trajectory_metrics.py).

## 16. V2: resultados observados

### 16.1. Desarrollo y reserva, sin ocultar el baseline

Todas las tasas siguientes son porcentajes. Los conteos son eventos o alertas, no empresas distintas.

| Fase | Signo | Método | Eventos | Alertas | Matches | Misses | Falsas | Censuradas | Recall | FAS |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Desarrollo | Mejora | Candidato, dos meses | 56 | 156 | 7 | 49 | 98 | 51 | 12,500 % | 93,333 % |
| Desarrollo | Mejora | Baseline, un mes | 56 | 287 | 7 | 49 | 159 | 121 | 12,500 % | 95,783 % |
| Desarrollo | Deterioro | Candidato, dos meses | 61 | 158 | 3 | 58 | 106 | 49 | 4,918 % | 97,248 % |
| Desarrollo | Deterioro | Baseline, un mes | 61 | 262 | 8 | 53 | 156 | 98 | 13,115 % | 95,122 % |
| **Reserva** | **Mejora** | **Candidato, dos meses** | **37** | **75** | **2** | **35** | **48** | **25** | **5,405 %** | **96,000 %** |
| Reserva | Mejora | Baseline, un mes | 37 | 146 | 3 | 34 | 82 | 61 | 8,108 % | 96,471 % |
| **Reserva** | **Deterioro** | **Candidato, dos meses** | **41** | **73** | **1** | **40** | **50** | **22** | **2,439 %** | **98,039 %** |
| Reserva | Deterioro | Baseline, un mes | 41 | 151 | 7 | 34 | 70 | 74 | 17,073 % | 90,909 % |

En la reserva, la FAS del candidato de mejora es `48 / (2 + 48) = 96 %`; las 25 censuradas no entran en ese denominador. Para deterioro es `50 / (1 + 50) = 98,039 %`, con 22 censuradas fuera del denominador. No significa que se haya comprobado el resultado de todas las alertas.

### 16.2. Lead observado e intervalos de la reserva

| Signo y método | Matches a 1 / 2 meses | Mediana lead, solo matches | IC95 recall | IC95 FAS |
| --- | --- | ---: | --- | --- |
| Mejora, candidato | 1 / 1 | 1,5 meses | [0,000 %; 15,798 %] | [89,357 %; 100,000 %] |
| Mejora, baseline | 2 / 1 | 1,0 meses | [0,000 %; 18,750 %] | [91,664 %; 100,000 %] |
| Deterioro, candidato | 0 / 1 | 2,0 meses | [0,000 %; 8,571 %] | [93,478 %; 100,000 %] |
| Deterioro, baseline | 3 / 4 | 2,0 meses | [7,407 %; 27,786 %] | [84,415 %; 96,668 %] |

**Solo hay tres matches de reserva del candidato.** Sus medianas de lead no justifican afirmar «el sistema anticipa los problemas dos meses»: describen exclusivamente esos pocos aciertos.

### 16.3. Conclusión de eficacia

**`anticipation_not_validated` / `descriptive_review_only`.** El candidato detecta pocos eventos y produce una proporción muy alta de falsas alertas entre las resolubles. En deterioro, además, tiene menor recall y mayor FAS que el baseline. En mejora, una FAS ligeramente menor no compensa por sí sola el menor recall ni demuestra superioridad.

La confirmación de dos meses reduce el número de alertas emitidas, pero **menos alertas no equivale a mayor utilidad predictiva**. No se modificaron umbrales, eventos, ventanas ni casos para corregir estas cifras tras ver la reserva. Las alertas predictivas automáticas permanecen desactivadas; el producto muestra señales para revisión descriptiva.

Este resultado negativo no contradice la mejora Brier de v1: **déficit futuro y transición de estado son desenlaces diferentes**. Tampoco las pruebas de software aprobadas convierten este backtest en un resultado predictivo satisfactorio.

Fuentes: [desarrollo](trajectory-v2/development-report.json), [reserva](trajectory-v2/reserved-report.json) e [informe sellado de evaluación/exportación](trajectory-v2/assessment/report.md).

## 17. V2: cobertura e incertidumbre

### 17.1. Universo evaluable

| Medida | Desarrollo | Reserva |
| --- | ---: | ---: |
| Empresas / grupos incluidos | 1.078 / 200 | 1.078 / 200 |
| Oportunidades empresa-origen | 7.546 | 3.234 |
| Orígenes con inputs elegibles para dirección | 3.250 | 2.101 |
| Orígenes con inputs no elegibles | 4.296 | 1.133 |
| Ventanas posibles de evento | 8.624 | 4.312 |
| Ventanas observadas | 1.771 | 1.074 |
| Ventanas desconocidas | 6.853 | 3.238 |
| Eventos confirmados antes de exigir origen elegible | 145 | 88 |
| Eventos excluidos por no tener origen elegible | 28 | 10 |
| Eventos elegibles usados en recall | 117 | 78 |

Las ventanas de evento, los orígenes de alerta y los eventos confirmados son unidades diferentes; no deben sumarse como si fueran filas equivalentes. La cobertura limitada por actividad, FX, cotas y ambigüedad condiciona las métricas. Los motivos pueden solaparse: sumar sus conteos no da necesariamente el total de excluidos.

### 17.2. Bootstrap por grupos

Se fijaron **1.000 réplicas**, semilla **1729**, muestreando con reemplazo los 200 grupos incluidos. Candidato y baseline comparten sorteos; se preservan las empresas y la cronología dentro de cada copia del grupo mediante sus conteos de matching. No se reentrena ni se modifica el evento en las réplicas.

El protocolo exige al menos cinco eventos en cinco grupos y cinco grupos que contribuyan al denominador de cada métrica/método. Los intervalos son percentiles 2,5 y 97,5 con interpolación lineal. Se requieren al menos 950 réplicas finitas de 1.000; no se repiten sorteos para reemplazar resultados indefinidos. Con soporte insuficiente, el intervalo queda nulo con motivo y solo se presentan estadísticas descriptivas.

Estos intervalos describen **rendimiento agregado de reglas fijas en los grupos observados**. No son intervalos de probabilidad individual ni resuelven el sesgo de cobertura, la disponibilidad retrospectiva o la falta de validación externa.

Fuentes: [contrato de soporte y bootstrap](trajectory-v2/protocol.json), [implementación](../../scripts/trajectory_metrics.py) y reportes de las dos fases citados en la sección anterior.

## 18. V2: casos, probabilidades y entrega API/UI

### 18.1. Tres ejemplos de desarrollo, no elegidos por acierto

Se eligió el primer patrón elegible por fecha de confirmación, empresa y mes de inicio, sin ordenar por magnitud, probabilidad o éxito de alertas. No se tomaron casos de la reserva ni de los 50 grupos de test v1.

| Caso | Empresa | Inicio observado | Confirmado y visible desde | Qué debe verse sin ocultar resultados |
| --- | --- | --- | --- | --- |
| Deterioro sostenido | `COMP_0009` | 2025-04-01 | 2025-05-31 | Estado pasa a déficit; dirección insuficiente en el patrón; ningún match del candidato ni baseline |
| Mejora sostenida | `COMP_0179` | 2025-04-01 | 2025-05-31 | Estado pasa a no-déficit; la dirección todavía es deteriorating en abril y improving en mayo; ningún match |
| Bache recuperado | `COMP_0028` | 2025-04-01 | 2025-06-30 | Un mes en déficit entre meses sin déficit; recuperación conocida solo al cierre de junio; no es target sostenido |

Las discrepancias son parte de la evidencia: **un evento observado de mejora puede coexistir con una dirección calculada todavía desfavorable**. No se sustituyeron los casos para hacer parecer eficaz la alerta. El caso completo se oculta en API/UI antes de su confirmación; la navegación a ejemplos se rotula como selección retrospectiva de desarrollo, no conocimiento del pasado.

### 18.2. Reutilización del predictor v1

La trayectoria descriptiva cubre 24 meses, pero la curva de probabilidad v1 solo puede tener valores elegibles desde abril de 2026, después de las etiquetas de entrenamiento/selección disponibles hasta el 31 de marzo. Antes de ese corte, `p_proxy=null` con motivo `before_training_cutoff`. Después, inputs no elegibles también producen abstención.

| Cierre de inferencia | Empresas preservadas | Estimaciones disponibles |
| --- | ---: | ---: |
| Abril 2026 | 1.286 | 968 |
| Mayo 2026 | 1.286 | 1.026 |
| Junio 2026 | 1.286 | 1.032 |
| Julio 2026 | 1.286 | 1.023 |
| Agosto 2026 | 1.286 | 1.010 |

Agosto coincide con las predicciones selladas de v1; no se ajustó otro modelo ni se consultaron sus targets para producir la curva. El modelo se entrenó físicamente en septiembre: los puntos de abril–agosto son una **simulación retrospectiva as-of**, no prueba de emisión histórica en vivo. Los ejemplos de desarrollo de 2025 no reciben probabilidades inventadas.

### 18.3. Entrega e interpretación

El nuevo modo `operating_trajectory` contiene **1.286 empresas × 24 meses = 30.864 puntos**. Las otras 208 empresas, pertenecientes a los 50 grupos excluidos, pueden visualizar sus indicadores y probabilidades, pero no tienen etiquetas de eventos ni casos seleccionados por el backtest v2.

Se implementaron:

- Carga y verificación del export y su manifest en servidor, con esquema tipado y referencias de diccionario locales; no se envía el dataset completo al navegador.
- Consulta `as_of` por empresa autorizada y mes cerrado; se filtran puntos, fecha de assessment y fila actual, rechazando cortes inválidos en lugar de sustituirlos por el último.
- Ocultación de todo el caso hasta su confirmación y de las métricas globales del backtest en vistas históricas. El informe global posterior se identifica como metodología retrospectiva.
- Gráficos que no unen huecos y separan ratios operativos de la probabilidad; evidencia de periodos, denominadores, cobros/pagos, cotas y fechas.
- Revisión manual no ejecutable; caja, buffer y salud siguen desconocidos y los planes se rechazan en este modo. Los modos cash de demo y proxy v1 se conservan.
- Aviso explícito **“Anticipation not validated”**, sin alertas predictivas automáticas.

El export ocupa 103.942.971 bytes y permanece en servidor; en el E2E registrado, la respuesta máxima por empresa/corte fue de 115.763 bytes. No se incorporó Jev a las reglas, los labels o la inferencia.

Fuentes: [casos sellados](trajectory-v2/development-cases.json), [manifest del export y esquema](trajectory-v2/assessment/manifest.json), [evidencia E2E](trajectory-v2/app-verification/browser-2026-09-19T18-59-16.113Z.json) y [guía de uso](../../readme.md).

## 19. V2: verificación, revisión y límites de las incidencias

La verificación final de la implementación registró:

| Comprobación | Resultado registrado |
| --- | --- |
| Suite Python completa | 254 tests, cero skips, aprobados |
| Calidad y CSV frente a Parquet | Sin discrepancias; ocho tablas coinciden |
| `bun run check` | Typecheck y build de ambas apps, checks web, tres tests web de trayectoria, fmt/clippy y once tests Rust ordinarios aprobados |
| Exports reales en Rust | Cuatro tests que se ejecutan explícitamente: dos v2 y dos v1 |
| Navegador Chromium con sandbox | 13 pasos, 78 respuestas históricas API, 26 cortes UI y cero errores de página |
| Reproducción del export v2 | Bytes idénticos y 112 archivos protegidos sin cambios |
| Replay de inferencia v1 | Probabilidades, features y metadatos exactos; error máximo de probabilidad 0,0 |

No se repitieron las fases reales de evaluación durante estos checks. Los tests de fixtures sí ejercitan ajuste/evaluación sobre datos de prueba, separados de los artefactos y resultados reales.

**Revisión independiente y fallo del servicio:** el evaluador de v2 recibió revisión independiente antes de abrir la reserva. Los dos intentos posteriores de revisión delegada final de la entrega fallaron por el límite del servicio de subagentes. La revisión final se hizo inline por el agente padre y **no es independiente**, como registra `final_delivery_independent: false`. No se debe presentar ese fallback como una segunda aprobación externa ni atribuirle una validación que no realizó.

Los mensajes de conexión/cuota pertenecen al servicio de agentes; el backtest y las pruebas de aplicación sí produjeron resultados guardados. La causa exacta de los avisos genéricos de desconexión no quedó determinada. **El mal rendimiento de las alertas es el resultado del backtest completado**, no una métrica generada por el fallo de revisión.

Los informes sellados conservan el estado de la fase en que se emitieron. En particular, el informe de exportación v2 menciona integración pendiente porque se generó antes de la etapa de API/UI. Se preserva para no romper sus hashes; esta sección y los registros finales documentan la integración posterior ya verificada.

La presente consolidación modifica documentación, no código ni resultados. Los conteos anteriores son los registrados al verificar la implementación v2; no se afirma que se haya vuelto a ejecutar la suite o los backtests para redactar este Markdown.

Fuente: [resumen de verificación final, comandos, hashes y limitaciones](trajectory-v2/final-verification/summary.json).

## 20. Conclusión conjunta y reproducción segura

### 20.1. Qué queda demostrado y qué no

| Afirmación | Conclusión |
| --- | --- |
| El clasificador v1 mejora a su baseline en su proxy y test definidos | Respaldada dentro de su población observable y límites sintéticos/retrospectivos |
| La misma AP demuestra anticipación de mejora/deterioro | No; es otro desenlace y otra evaluación |
| Las reglas v2 son útiles como alertas predictivas | **No validado:** baja detección y alta FAS; no se promueven a uso predictivo automático |
| La trayectoria se puede inspeccionar por fecha con evidencia y abstenciones | Implementado y probado en API/UI |
| Pasar tests de software demuestra eficacia financiera | No; comprueba implementación e integración, no calibración ni utilidad externa |
| Tenemos evaluación oficial o validación real externa | No disponible |

Una siguiente investigación de anticipación tendría que definir otra hipótesis y una evaluación adecuada. No se debe ajustar contra esta reserva ya inspeccionada ni ocultar sus malos resultados. Tampoco hay una causa demostrada del bajo rendimiento más allá de lo medido: una explicación causal o una mejora prometida requerirían evidencia nueva.

### 20.2. Verificar sin reabrir experimentos

Desde el worktree y entorno ligados al snapshot original, los comandos de comprobación son:

```sh
.venv/bin/python -B -m scripts.trajectory_backtest verify
.venv/bin/python -B -m scripts.trajectory_export --check
.venv/bin/python -B scripts/replay_model.py --check
```

`verify` comprueba integridad y finalización de fases existentes, no recalcula métricas. El `--check` de exportación reconstruye indicadores descriptivos e inferencia para comparar bytes, sin detectar eventos ni ejecutar otra vez el backtest. No borrar recibos ni repetir `develop` / `evaluate-reserved`; se mantiene el límite de portabilidad y versiones exactas explicado en la sección 11.

| Artefacto v2 | SHA-256 |
| --- | --- |
| `trajectory-v2/protocol.json` | `d37ee0cad9101b4e6ae1dbc39f69d471ff7457b2cf3c5dac8ac5bd786c70159d` |
| `trajectory-v2/execution-manifest.json` | `5a2b05bb2a83757ceaf18465c81f6db48c812a0036dcc2dd82c9566eca5f9a33` |
| `trajectory-v2/reserved-access.json` | `2decf275e47ef57fbfe4de17753455b121115185a5c3a66ed938c481abfc04b2` |
| `trajectory-v2/reserved-report.json` | `9498d0798cc48caf04fd3020fe7a86cc9c1c7ed12d5f4ebfb674df1d61e683ac` |
| `trajectory-v2/assessment/companies.json` | `2c9cbfd1cb5f8976cbff5184f7eabcc796fe86763de0e0cc512c2448a579a5c9` |

Este documento consolida los resultados para lectura; los [JSON del protocolo](trajectory-v2/protocol.json), [desarrollo](trajectory-v2/development-report.json), [reserva](trajectory-v2/reserved-report.json) y [verificación final](trajectory-v2/final-verification/summary.json) conservan el detalle trazable. El vault, los artefactos sellados y los resultados originales de v1 no se modifican con esta actualización.
