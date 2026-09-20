# Blaubeere — contexto de datos, modelos y entrenamiento

**Alcance:** síntesis de la capa analítica para una base de conocimiento; no es una guía de la aplicación. **Estado contrastado:** 20 de septiembre de 2026. Los experimentos descritos se ejecutaron con el dataset existente; esta actualización documental no entrena ni reevalúa.

## 1. El encargo: un score como eje, no predecir quiebras

El brief pide leer el comportamiento financiero en ambas direcciones, empresa por empresa y mes a mes. El sistema debe responder:

1. **Quién está sano:** distinguir fragilidad, normalidad y solidez excepcional.
2. **Quién está mejorando:** reconocer recuperación aunque el nivel actual siga siendo mediocre.
3. **Quién empieza a torcerse:** advertir deterioro antes de que el nivel ya sea malo.
4. **Bache o caída:** separar un episodio transitorio de deterioro persistente.
5. **Por qué ha cambiado:** explicar el número, sus señales y su diferencia respecto al mes anterior.
6. **Cuándo se vio venir:** medir cuánto se adelantó la señal al comienzo del cambio, con misses y falsas alarmas.

Las cuatro capacidades del encargo son **leer el rastro → construir un score temporal → explicarlo → construir un producto apoyado en él**. El brief menciona generalizar a **60–80 empresas no vistas**: es una exigencia comunicada, no una prueba de que dispongamos de ese conjunto oficial o lo hayamos superado. Separar empresas relacionadas por grupo es necesario; evaluar un proxy en grupos separados no valida automáticamente un score financiero integral.

**No hemos cumplido todavía ese núcleo.** La ejecución pasó de un predictor de déficit a reglas de trayectoria y después a proxies de cambios en cobros. Son piezas reutilizables, pero ninguna sustituye el score central. La infraestructura y las verificaciones han avanzado más que la capacidad predictiva requerida.

## 2. Datos disponibles y decisiones financieras

El dataset del reto ya era sintético: **1.286 empresas, 250 grupos y 24 meses cerrados**, de septiembre de 2024 a agosto de 2026. Incluye empresas, productos bancarios y de deuda, calendarios, transacciones, facturas y saldos. No generamos otro dataset para obtener las métricas reales; los casos inventados se usan en tests de software.

El recorrido es **CSV → Parquet limpios → agregados empresa-mes → variables y etiquetas separadas**. Se conservan problemas de calidad, incertidumbre y faltantes; no se eliminan ni convierten silenciosamente en cero. La fuente sigue siendo el run `20260919T124814Z-e5302b58`.

El usuario ha fijado **trabajar con estos 24 meses**. No se exige adquirir otro dataset como condición para implementar o evaluar internamente. Esto no demuestra que todas las variables estén completas ni convierte el corpus ya explorado en un test independiente.

| Dimensión | Qué aprovechamos | Qué no se puede confundir |
| --- | --- | --- |
| Flujos mensuales | Movimientos contabilizados del perímetro observado, cobros/pagos, financiación y cotas de clasificación | Flujo no es saldo; subtotal conocido no es total de toda la empresa |
| Deuda | Servicio pagado, principal/intereses identificados, facturas pendientes y vencidas como estimaciones retrospectivas | Deuda pagada no es deuda pendiente total; outstanding actual no es una serie histórica |
| Crecimiento | Cambios en cobros operativos observados y documentos emitidos | Financiación, transferencias o retener pagos no son crecimiento del negocio |
| Mora | Exposición vencida ponderada y tramos de antigüedad; pruebas de que no pagar no mejora el score | El multiplicador es penalización de riesgo, no deuda legal adicional ni salida ficticia de caja |
| Caja inversa | Reversión de movimientos firmados desde el saldo final, por cuenta y moneda, con dos escenarios de corte | Es reconstrucción provisional; no caja disponible verificada ni información conocida entonces |

No basta contar 24 meses: hay snapshots de saldo/deuda, solo 87 calendarios de deuda y ausencia de algunas fechas de disponibilidad, pagos parciales y cambios de estado. Hay que aprovechar la evidencia y declarar supuestos sin inventar liquidaciones, deuda cero o conocimiento histórico.

## 3. Tres etapas distintas, no una comparación antes/después

| Etapa | Objetivo evaluado | Resultado y alcance |
| --- | --- | --- |
| **V1** | Déficit operativo en al menos dos de los tres meses siguientes | HGB mejora al baseline en su test definido; no es salud global |
| **V2** | Anticipación de transiciones operativas sostenidas mediante reglas | Resultado negativo: no demuestra anticipación útil |
| **V3 actual** | Contracción/expansión de cobros y recuperación de un bache de cobros | Gana persistencia, no las regresiones; no supera utilidad y el bache queda sin soporte suficiente |

Cambian objetivos, ventanas, poblaciones y métricas. No se puede comparar directamente AP de v1, recall de eventos v2 y AP de cobros v3 para afirmar que el mismo modelo mejoró o empeoró.

## 4. V1: entrenamiento y evaluación del déficit

Se fijaron **32 variables** de flujos del mes y ventanas de tres/seis meses: ratios operativos y de financiación, neto, volumen, incertidumbre, medias, variabilidad, tendencias y cambios. V1 no usa como predictores facturas, saldos actuales ni stocks de deuda. Los IDs relacionan y separan filas, no predicen.

El target `target_deficit_3m` usa cotas operativas futuras, robustas a clasificación ambigua y a atípicos. Si no puede determinarse, queda desconocido, no negativo. Los inputs usan solo el pasado/origen bajo la disponibilidad retrospectiva declarada.

- Grupos asignados: **150 train, 50 validation, 50 final_test**; empresas del mismo grupo permanecen juntas.
- Dos validaciones temporales, con purga por maduración de las etiquetas.
- Cuatro candidatos: dos regresiones logísticas y dos configuraciones HGB; baseline de prevalencia de entrenamiento.
- Ganador: `hgb_leaf15`, seleccionado por Brier medio; ajuste final sobre **3.630 filas de 112 grupos** con desenlaces hasta marzo de 2026.
- Test final abierto una vez, en grupos separados y orígenes abril–mayo de 2026; no se retocó el modelo con ese test.

| Test interno v1 | Constante | Modelo |
| --- | ---: | ---: |
| AP | 0,511450 | **0,815603** |
| Brier, menor es mejor | 0,251729 | **0,188429** |

Mejora relativa de Brier: **25,15 %**. Solo **131 de 299 casos** tenían desenlace observable, en 31 grupos. Las probabilidades no están calibradas. La inferencia de agosto conservó 1.286 empresas: **1.010 estimaciones y 276 abstenciones**; cobertura no significa acierto futuro. AP no es accuracy y `100 × (1 − p)` no convierte el proxy en salud financiera.

## 5. V2: trayectoria descriptiva y alertas

V2 **no reentrena v1**. Compara tres meses con los tres anteriores, separa estado de dirección y confirma señales tras dos meses consecutivos. Los eventos se definen independientemente de las señales; primera advertencia, confirmación e inicio del evento tienen fechas distintas.

La reserva temporal, en empresas conocidas y excluyendo los grupos final_test v1, dio **2/37 mejoras** y **1/41 deterioros** anticipados. Las falsas alertas entre las resolubles fueron **96 % y 98,039 %**; no son tasas de falsos positivos sobre toda la población. La anticipación sigue no validada. No se ajustaron las reglas contra esa reserva.

## 6. V3: lo que sí se ejecutó sobre el dataset

Hay dos capas que no deben mezclarse:

- **Núcleo financiero estricto de desarrollo:** métodos de caja, obligaciones, mora creciente, crecimiento, scorecard y explicaciones, probados con fixtures. No demuestra por sí mismo salud financiera real.
- **Adaptación retrospectiva del dataset y experimento real de cobros:** perfiles observados, componentes parciales e intervalos; predictores entrenados con datos del dataset. El score global permanece `null` y el intervalo completo `[0,100]` es no informativo.

El adaptador conserva **30.864 puntos empresa-mes**. Produce flujos, servicio pagado, AP/AR y aging retrospectivos, crecimiento y caja inversa provisional. La mora del tramo de 1–30 días tiene un intervalo de multiplicadores porque no identifica exactamente los primeros siete días. El núcleo estricto distingue días exactos; no se deben atribuir esas fechas al agregado real.

### Variables, targets y partición

El experimento tiene **18 inputs**, no las 32 features de v1: valores actuales/medias de tres meses de cobros y pagos conocidos, cotas operativas y coberturas, documentos AP/AR emitidos, importe nominal de AP con vencimiento en el mes, problemas de fecha/FX y servicio pagado conocido. No incorpora como inputs saldos reconstruidos desde el futuro, deuda del snapshot final, estados finales de facturas ni scores.

Los targets binarios son cobros operativos clasificados **≤80 % o ≥120 %** de la media del origen y sus dos meses previos, en al menos dos de los tres meses siguientes. Se exigen meses completos y el mismo conjunto de cuentas observadas; lo demás queda desconocido. Son cambios en cobros, no quiebra ni mejora/deterioro financiero general.

Para bache se condiciona a una caída **ya observada** en el origen respecto a los tres meses anteriores. La recuperación requiere dos meses futuros consecutivos al menos al 90 % de esa referencia; persistencia, dos de tres al 80 % o menos; el resto es mixto. Esta clasificación tampoco es un diagnóstico estructural integral.

- Fit: grupos train, orígenes **noviembre 2024–marzo 2025**, etiquetas maduras hasta junio.
- Validación interna: grupos validation, orígenes **julio–septiembre 2025**, etiquetas hasta diciembre.
- Abril–junio se purga; no se consultan outcomes de 2026 ni se reutiliza la reserva v2. Los 50 grupos final_test quedan excluidos también de inferencia v3: sus 208 empresas son solo perfiles de evidencia.
- **4.930 filas candidatas, 1.881 elegibles**; 1.536 elegibles de fit y 345 de validación.

### Entrenamiento y resultados

Se compararon constante, persistencia, tendencia y logística `C=0.1/1`. Persistencia estima frecuencias del desenlace según el estado actual de cobros, aprendidas en entrenamiento. Las logísticas imputan/escalan solo con train. Hubo **cuatro ajustes logísticos reales**; ganó persistencia por Brier medio por grupo en ambos targets.

| Target v3 | AP constante | AP persistencia | Brier constante | Brier persistencia |
| --- | ---: | ---: | ---: | ---: |
| Contracción de cobros | 0,321429 | 0,443001 | 0,228742 | 0,211127 |
| Expansión de cobros | 0,357143 | 0,410000 | 0,243182 | 0,224609 |

Mejora frente a constante, **no frente a la mejor regla simple**. Las regresiones no ganaron por el criterio fijado. Solo **212 filas de entrenamiento y 28 de validación** tenían etiquetas binarias utilizables; validación cubre 8 grupos. Al umbral 0,6 el seleccionado no emitió alertas: recall 0. La precisión queda indefinida, no cero. Los criterios de utilidad fallaron y no se midió una anticipación económica válida.

El target de bache tuvo 11 recuperaciones, 38 persistencias y 12 mixtos en train: dos clases no alcanzaron el mínimo de 20 filas. **No se ajustó ni emitió su predictor.** Selección y evaluación usaron la misma validación interna, por lo que tampoco sería aceptación independiente aunque sus métricas fueran mejores.

El reajuste de las dos persistencias seleccionadas utiliza 240 filas maduras hasta diciembre de 2025. El overlay solo empieza en enero de 2026: **6.289 puntos con probabilidad por target**, en 1.078 empresas potenciales; el resto conserva razones de abstención. No se presenta como predicción emitida históricamente.

## 7. Qué falta frente al brief

| Pregunta | Estado respaldado |
| --- | --- |
| Quién está sano | Sin score global utilizable ni validación de solidez excepcional |
| Quién está mejorando | Evidencia observada parcial; expansión de cobros no basta |
| Quién empieza a torcerse | Proxy limitado, sin advertencia financiera útil demostrada |
| Bache o caída | Métodos y casos de prueba; predictor real sin soporte suficiente |
| Por qué ha cambiado | Explicación aritmética parcial, no del score global pendiente |
| Cuándo se vio venir | Anticipación útil no validada |

La siguiente prioridad es **score mensual → explicación → persistencia/bache → anticipación → generalización a empresas no vistas → artefacto sencillo**. La entrega debe sintetizar los resultados y su evidencia, no añadir una arquitectura de aplicación. Primero hay que diagnosticar por qué las reglas actuales dejan tan pocas etiquetas utilizables y qué parte responde a datos ausentes o a decisiones demasiado conservadoras. Después, definir una iteración versionada antes de evaluarla. No cambiar criterios a posteriori, reabrir reservas ni prometer mejora sin medición.

## 8. Trazabilidad y fuentes

La entrega objetivo es un artefacto sencillo; existe un generador de informe HTML local que consume los perfiles y métricas sellados, sin entrenar ni evaluar. Las verificaciones técnicas anteriores y su enmienda de caché son evidencia histórica, no pruebas de eficacia financiera ni una nueva verificación de aplicación en esta revisión. La revisión final registrada fue inline, **no independiente**. Esta actualización conserva las versiones previas y verifica documentación y artefactos analíticos, sin modificar datos, código de modelos, parámetros o resultados.

- [Diccionario de datos](../data_dictionary.md).
- [Informe consolidado v1/v2/v3](../reports/modeling/training-report.md).
- [Auditoría v3](../reports/modeling/financial-v3/audit-summary.json) y [propuesta inicial de desarrollo](../reports/modeling/financial-v3/design-proposal.md), esta última histórica, no equivalente al modelo finalmente seleccionado.
- [Política del adaptador real](../reports/modeling/financial-v3/dataset-v1/policy.json), [protocolo del experimento](../reports/modeling/financial-v3/dataset-v1/model-v1/protocol.json) y [métricas por candidato](../reports/modeling/financial-v3/dataset-v1/model-v1/metrics-summary.json).
- [Verificación técnica enmendada](../reports/modeling/financial-v3/dataset-v1/delivery-v1/amendment-v2/verification.json) y [revisión del padre, no independiente](../reports/modeling/financial-v3/dataset-v1/delivery-v1/amendment-v2/check-parent-review-20260920-v1.json).
- [Estado de producto y comprador](PRODUCT.md), separado de la evidencia de entrenamiento.
