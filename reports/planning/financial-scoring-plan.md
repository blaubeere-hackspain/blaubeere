# Plan de desarrollo: scoring financiero y predicción temporal

**Estado: PROPUESTA.** Diagnóstico inicial de los CSV realizado en solo lectura. No se han corregido datos, construido un panel, elegido un target definitivo ni entrenado modelos. Este documento no certifica preparación de datos ni promete precisión.

**Recomendación:** validar fuentes y semántica → producir datos derivados auditables → aprobar preparación para un alcance concreto → congelar target y validación → baseline → un modelo competidor → explicación y entrega. Si una fuente opcional no es fiable, reducir alcance; no inventar información para conservarla.

“100 % preparados” significa cumplir criterios de aceptación del uso elegido, no disponer de información perfecta. La precisión será un resultado medido sobre un objetivo explícito, no una propiedad garantizada de antemano.

## 1. Decisiones principales

| Tema | Propuesta |
|---|---|
| Fuente | `dataset/output/*.csv` y `dataset/output/data_dictionary.md`; no descargar LFS para empezar |
| Repositorio | `.gitignore` establece versionado solo de datos e informes; guardar este plan en `reports/planning/`. Acordar ubicación del código antes de implementar |
| Procesamiento | Pipeline batch reproducible, con originales inmutables y tablas derivadas |
| Unidad provisional | Empresa × mes cerrado × moneda, hasta justificar consolidación |
| Núcleo candidato | Flujos transaccionales, con clasificación y cobertura verificadas |
| Fuentes complementarias | Facturas, deuda y saldos solo para usos que superen sus controles |
| Salidas diferentes | Índice descriptivo actual, trayectoria y predicción de un desenlace futuro |
| Modelo | Un modelo conjunto sobre filas elegibles; no una red por empresa |
| Calidad | Cobertura y límites visibles; falta de fuente no equivale a mala salud |
| Fuera de alcance inicial | Decisiones de crédito/pagos, predicción de quiebra sin labels, streaming, microservicios, LLM calculando score |

No confundir millones de movimientos con millones de ejemplos independientes: existen 1.286 empresas relacionadas en 250 grupos y ventanas temporales solapadas. Datos sintéticos permiten evaluar el experimento, no demostrar precisión en empresas reales.

## 2. Evidencia inicial y límites

**Medido:** perfiles locales de CSV mediante Python en solo lectura, con comprobación de claves, fechas, distribuciones y cobertura. **Diccionario:** significado declarado por la fuente. **Informe previo:** evidencia auxiliar, no reproducción de esta auditoría. **Pendiente:** semántica o comprobación aún sin resolver.

| Hallazgo | Evidencia / implicación |
|---|---|
| Filas: grupos 250; empresas 1.286; productos bancarios 5.987; deuda 2.239; schedules 87; saldos 7.996; transacciones 2.556.437; facturas 897.894 | Medido en los ocho CSV |
| Sin claves primarias vacías ni duplicadas en los perfiles ejecutados | Medido; saldos usa `(product_id, date)`. No descarta duplicación económica entre documentos/fuentes |
| Empresas con transacciones 1.286; facturas 785; deuda 378; schedules 40 | Medido; falta de registros no significa importe cero |
| Historia mensual cerrada: mínimo 1, mediana 18, máximo 24; ocho empresas con menos de seis meses | Medido sobre todos los estados transaccionales. Recalcular tras filtros y comprobar continuidad |
| Booking: 2024-09-01 a 2026-09-01; septiembre de 2026 contiene 9.242 transacciones | Medido; mes parcial no comparable con meses completos |
| Saldos fechados entre 2026-08-25 y 2026-09-01 | Medido; foto por producto, no serie histórica |
| Transacciones: `booked` 2.520.019; `pending` 6.579; estado ausente 29.839 | Medido; estados finales no prueban disponibilidad histórica |
| Categoría `-`: 635.530; vacíos: 330; total sin categoría: 635.860 | Medido; explica diferencia entre conteos del informe previo, no una contradicción |
| Contraparte ausente en 2.305.659 transacciones | Medido; concentración transaccional no puede ser una señal universal |
| Producto sin resolver: 1.314 transacciones y 29 saldos | Medido; excluir o resolver joins afectados con trazabilidad |
| `value_date` llega a 2099; vencimientos a 7025-07-31; pagos a 6913-11-20 | Medido; sintaxis válida no implica fecha financieramente válida |
| Facturas: vencimiento anterior a emisión 20.704; pago anterior a emisión 25.827 | Medido; anomalías para revisión, no correcciones automáticas; pueden existir anticipos |
| `payment_date` presente en 897.888 de 897.894 documentos | Medido; aparece también en estados pendientes/vencidos |
| 187.239 documentos `overdue` tienen pago fechado hasta el corte; 19.504 `paid` tienen pago posterior; 300 `paid` tienen pendiente distinto de cero | Medido; significado de pago realizado frente a planificado sigue pendiente |
| Tipos observados: invoice, paymentDocument, note, deposit, invoiceGroup, deliveryNote, refund, purchaseOrder, other, cheque | Medido; no sumar todo como facturas de venta ni cuentas por cobrar |
| 118.055 documentos tienen moneda contable distinta de EUR; 77 transacciones tienen tipo de cambio cero | Medido; conversión universal a EUR no demostrada |
| Dirección de FX y significado del signo de facturas/deuda | Pendiente. Diccionario no basta para demostrar EUR ni cliente/proveedor |
| Deuda: saldo al extraer, tipo actual/último; `created_at` es conexión | Diccionario; no constituye historial contractual ni prueba de producto activo |
| Saldo `-999999999` aparece dos veces | Medido; candidato a centinela, no motivo para eliminar todo saldo negativo |
| No se encontró label empresarial oficial en los ocho esquemas ni diccionario | Inspección acotada; confirmar si se entrega por otro canal |

Rutas auxiliares: `reports/quality/summary.md`, `summary.json`, `facts.json`, `dimensions.json`. `data/*.csv` y `data/clean/*.parquet` son punteros LFS locales. No hay aquí pipeline de limpieza reproducible ni linaje verificado hacia esos Parquet. Los conteos existentes no sustituyen hashes, reglas ni reconciliación.

**Limitación del diagnóstico:** los perfiles fueron ad hoc. Todavía no se produjo manifest de hashes ni informe automático durable; estos son entregables de la primera fase. Algunas definiciones de flags difieren entre perfiles e informes: comparar solo métricas con igual definición.

## 3. Fases, puertas de avance y alternativas

| Fase | Trabajo y entregable previsto | Criterio para avanzar | Si no se cumple |
|---|---|---|---|
| P0. Contrato e inventario | Manifest de fuentes; preguntas de unidad, target, horizonte y disponibilidad | Fuentes identificadas; usos provisionales y dudas registrados | Inspeccionar sin entrenar ni fijar semántica por intuición |
| P1. Auditoría reproducible | Perfil estructural, financiero y temporal; informe de calidad | Problemas clasificados, cuantificados y localizables | Ampliar comprobaciones del problema concreto |
| P2. Datos derivados | Limpieza determinista, cuarentena y ledger de correcciones | Reglas justificadas; ninguna modificación silenciosa | Excluir campo/fuente o pedir confirmación |
| P3. Panel as-of | `as_of_panel.parquet`, contrato de features y cobertura | Pruebas temporales, reconciliación y elegibilidad aprobadas | Reducir features, cohortes u horizonte |
| G-DATA. Aprobación | Informe de preparación del alcance elegido | Todos los controles críticos de esa ruta pasan | No ajustar modelos |
| P4. Target y split | `TARGET.md`, `future_outcomes.parquet`, índices de partición | Desenlace defendible; soporte maduro y evaluación congelada | Proxy aprobado, menor horizonte o índice descriptivo |
| P5. Baseline | Predicciones simples y métricas reproducibles | Referencia en unidades correctas y errores entendidos | Corregir protocolo antes de complejidad |
| P6. Modelo | Modelo candidato, comparación y explicaciones de casos | Mejora relevante y estable bajo protocolo congelado | Conservar baseline o abstenerse |
| P7. Entrega | Export, evidencia, versión y recorrido de demo | Contrato de salida validado; resultado reproducible | Export interno provisional, no envío oficial válido |
| P8. Extensiones | Ensemble, forecast auxiliar, explicaciones amplias y alertas | Necesidad concreta y beneficio comprobado | No construirlas |

El contrato provisional de uso se discute en P0: determina qué significa preparar datos correctamente. La generación final de labels y el ajuste estadístico esperan los controles aplicables. No fijar plazos de 3–4 horas para limpieza antes de conocer correcciones y dependencias del mentor; estimar cada fase después de P1.

## 4. P0–P1: validar antes de corregir

### Inventario y reproducibilidad

- Registrar ruta, bytes, hash, encoding, delimitador, esquema, filas, versión y fecha de extracción conocida. Separar fecha de descarga de fecha de disponibilidad del dato.
- Mantener raw inmutable. Comparar fuentes actuales con informes previos por identidad y definición, no solo por número de filas.
- Congelar snapshot de trabajo. No publicar datos ni subirlos a servicios externos; confirmar licencia y política del reto.
- Elegir Python y un motor tabular según entorno disponible; DuckDB es candidato, no requisito para empezar ni motivo para instalar un stack completo.

### Auditoría obligatoria

| Área | Comprobación |
|---|---|
| Estructura | Columnas, tipos, vacíos, centinelas, registros mal formados, claves y duplicados |
| Relaciones | Empresa/grupo/producto, propietario correcto, contraparte, settlement y claves huérfanas |
| Documentos | Tipos y signos, cancelaciones, grupos de facturas, anticipos, refunds y posible duplicación económica |
| Fechas | Parseo, plausibilidad, coherencia entre campos, futuros válidos, fechas finales mutables y cobertura temporal |
| Importes | Moneda origen/destino, sentido de FX, tipos cero/nulos, escala, signos y outliers por contexto |
| Cobertura | Fuente × empresa × grupo × moneda × mes; continuidad, cuentas añadidas y cambios de extracción |
| Disponibilidad | Qué campos se conocían entonces; actualizaciones/reconciliación tardías; ausencia de observation logs |
| Integridad económica | No sumar factura y pago como dos ingresos; no tratar transferencias propias como actividad operativa |

Informar número de filas y exposición monetaria afectada cuando sea calculable sin mezclar monedas. Medir pérdida de cobertura tras cada filtro. Auditoría estructural de todo el raw está permitida; no usar distribución de desenlaces del holdout para diseñar targets, filtros estadísticos o umbrales.

## 5. P2: correcciones permitidas y decisiones condicionadas

| Situación | Acción segura propuesta | Alternativa / bloqueo |
|---|---|---|
| Categoría `-` o vacía | Normalizar a ausente en derivado, conservar original y motivo | No asignar categoría por signo o conveniencia |
| Tipos/texto inconsistentes | Normalizar solo equivalencias demostradas | Valores desconocidos conservados y marcados |
| Fecha extrema, por ejemplo 7025 | Marcar campo y cuantificar impacto | No suponer que significa 2025; confirmar o excluir feature/registro afectado |
| Pago anterior a emisión | Revisar tipo y semántica de anticipo | No intercambiar fechas automáticamente |
| `payment_date` ambiguo | Bloquear aging histórico exacto y labels de pago | Admitir aproximación solo con supuesto explícito y aceptación humana |
| Facturas con tipos mezclados | Contrato de inclusión por tipo y relación entre documentos | No sumar pedidos, albaranes y facturas como hechos independientes |
| Signos de factura/deuda ambiguos | Preservar signo y pedir convención | No aplicar `abs()` indiscriminadamente ni inferir cliente/proveedor |
| FX no probado | Confirmar por fuente moneda destino y operación mediante casos/invariantes | Mantener moneda separada; excluir agregados que requieran conversión |
| Producto huérfano | Resolver con referencia autorizada o aislar joins afectados | No inventar entidad ni moneda |
| Duplicado técnico/económico | Deducir identidad y precedencia antes de deduplicar | Preservar si puede representar eventos diferentes |
| Saldo centinela/outlier | Confirmar centinela o marcar dato no utilizable | No borrar saldos negativos legítimos ni winsorizar con test |
| Estado pending/ausente | Política por uso; excluir del flujo realizado si justificado, registrar cobertura | Estado final `booked` no prueba que estuviera disponible en cada corte |
| Falta de facturas/deuda | Marcar fuente no observada | Nunca reemplazar por cero facturación/deuda |
| Mes sin movimientos | Comprobar cobertura y conexión de fuentes | Ausencia desconocida ≠ actividad cero |

Cada cambio registra `source_id`, campo, valor original, valor derivado, regla/version, motivo y disposición. Repetir proceso debe producir mismo resultado. Reconciliar filas leídas, retenidas, excluidas y en cuarentena sin doble conteo; importes antes/después por moneda y causa. Registrar exclusiones de campos por separado de exclusiones de filas.

No hace falta resolver todo el dataset para avanzar: un problema de facturas puede bloquear solo esa rama. La ruta transaccional requiere igualmente controles propios de estados, moneda, clasificación, cobertura y disponibilidad histórica. Si solo disponemos de extracción final, documentar supuesto retrospectivo y no presentarlo como reconstrucción probada de lo conocido en tiempo real.

## 6. P3: panel temporal y contrato de features

Una fila representa información disponible en corte `t`, no lo que sabemos hoy sobre aquella fecha. `company_id` y `group_id` sirven para identidad y particiones; no deben memorizarse como predictores. Metadatos de grupos, ERP y productos también pueden ser snapshots finales.

| Familia | Features iniciales candidatas | Condiciones |
|---|---|---|
| Flujos | Inflow, outflow y netflow por bloque; número de movimientos | Moneda y signo verificados; categorías financieras aprobadas |
| Dinámica | Ventanas 1/3/6 meses, tendencia, dispersión y persistencia | Historia utilizable y continua según política; no rellenar ausencia desconocida |
| Productos | Número de productos observados en ventana | No llamarlos activos contractualmente a partir de `created_at` |
| Calidad | Cobertura, historia disponible, porcentaje sin categoría/moneda/resolución | Calcular as-of; no convertirlo directamente en penalización de salud |
| Concentración | Peso de principales contrapartes observadas | Denominador, cobertura y lado económico definidos; no universal |
| Facturas | Retrasos y overdue reconstruidos | Pago efectivo, pagos parciales, dirección y tipos demostrados; actualmente bloqueado |
| Deuda | Obligaciones conocidas a fecha de corte | Vigencia contractual demostrada; datos actuales no retroceden en tiempo |
| Caja | Foto de saldo disponible en corte real | Histórico solo con reconstrucción completa por cuenta/moneda y reconciliada |

Bloques candidatos: operativo, financiación, transferencia, impuestos/comisiones y desconocido. Inversiones, cash withdrawals y otras categorías requieren política explícita; no forzarlas a operativo para cerrar suma. Mostrar flujos desconocidos y estudiar cuánto alteran señal.

Contrato por feature: `name`, fuentes/IDs, moneda, `event_date`, evidencia de `known_at`, `as_of_date`, lookback, transformación, denominador, elegibilidad, valor, `missing_reason` y cobertura. Guardar definición/version por feature y trazabilidad suficiente para reconstruir contribuciones; no necesariamente duplicar todas las filas origen por celda.

- Un vencimiento posterior a `t` puede ser válido si ya era conocido. `event_date <= t` para todos los campos no es test correcto de leakage.
- Saldo final, pendiente final, reconciliación final y último tipo de interés no se copian al pasado.
- Normalización relativa usa solo historia disponible hasta `t`; parámetros globales se ajustan en train. No normalizar con toda la vida posterior de empresa.
- Evitar CV y deltas relativos cuyo denominador sea netflow cercano a cero o negativo. Usar escalas de actividad positivas, límites de elegibilidad y valores no disponibles cuando corresponda.
- Controlar onboarding: aumento de cuentas conectadas o fuentes visibles no demuestra crecimiento del negocio.

### Pruebas mínimas antes de modelar

1. Alterar eventos posteriores a corte no cambia features anteriores, salvo información futura legítimamente conocida y modelada como tal.
2. Agregar un pago realizado después de `t` no modifica pago observado antes de `t`; verificar con pequeño caso controlado.
3. Mes parcial no entra en agregados comparables; ventanas respetan política de continuidad y denominadores.
4. No hay mezcla de monedas sin conversión validada; importes y joins cuadran con fuentes elegibles.
5. Fuentes ausentes producen desconocido, no cero; fixtures cubren positivos, negativos, cero, faltantes y anomalías.
6. Transformaciones estadísticas no se reajustan con validation/test; regeneración produce mismo contenido lógico.

Estas pruebas detectan errores del pipeline; no demuestran por sí solas disponibilidad histórica de un campo sin historial. Ese límite requiere evidencia o supuesto aceptado.

## 7. Puerta G-DATA: preparación aprobada para un alcance

- [ ] Fuentes congeladas, esquema y hashes registrados; pipeline reproducible.
- [ ] Reglas de claves/FKs y ownership aplicadas sin pérdidas silenciosas.
- [ ] Anomalías críticas corregidas con evidencia o excluidas con impacto cuantificado.
- [ ] Monedas, signos, estados y temporalidad resueltos para las features elegidas.
- [ ] Cobertura y universo elegible documentados por empresa, grupo, mes y fuente.
- [ ] Ledger y reconciliación de filas/importes pasan; proceso idempotente.
- [ ] Pruebas as-of y de dependencia posterior al corte pasan.
- [ ] Limitaciones y supuestos pendientes aceptados por responsable humano.

**No se ajustan modelos hasta superar esta puerta para la ruta elegida.** No exigir cobertura perfecta de fuentes opcionales ni inventar porcentajes universales de aceptación. Definir mínimos de historia, actividad y cobertura antes de evaluar modelos, con criterio financiero y datos de desarrollo. Insuficiencia del núcleo implica menor horizonte, menor universo, abstención o índice descriptivo.

## 8. P4: target defendible, maduración y validación

| Datos/contrato disponibles | Ruta | Qué no afirmar |
|---|---|---|
| Score numérico oficial | Regresión y métrica oficial | Que score implica probabilidad sin definición |
| Evento binario oficial | Clasificación; probabilidad calibrada si validable | Que accuracy global basta ante eventos escasos |
| Categorías ordenadas | Objetivo ordinal y coste de equivocaciones | Que todos los errores tienen igual gravedad |
| Ranking oficial | Aprendizaje/evaluación de ranking conforme contrato | Que ranking da probabilidad absoluta |
| Sin labels, desenlace operacional medible | Proxy futuro aprobado, con alcance y censura | Que equivale a salud integral o default |
| Sin desenlace defendible | Índice descriptivo trazable y trayectoria | Precisión predictiva o probabilidad de insolvencia |

### Candidato de proxy transaccional, no seleccionado

Para ventana `W` en moneda comparable: `B(W) = (sum(inflows) - sum(outflows)) / (sum(inflows) + sum(outflows))`. Posible target: `B(t+1…t+3) - B(t-2…t)`.

Denominador debe superar mínimo de actividad previamente justificado; de lo contrario target no disponible. No arreglarlo con epsilon arbitrario. Exigir cobertura estable y horizonte completamente observado. Esta razón limita explosiones por netflow cercano a cero, pero no elimina estacionalidad, cambios de fuentes ni confusión económica.

Si clasificación es fiable, estudiar flujos operativos; si no, denominarlo explícitamente equilibrio de flujos observados. Financiación/transferencias pueden mejorar caja sin mejorar negocio. Comparar horizontes candidatos de uno y tres meses según soporte, no por cuál queda mejor en test. Proxy de facturas sigue bloqueado hasta resolver pagos/dirección; no hay soporte actual suficiente para default o incumplimiento de deuda.

`TARGET.md` debe fijar unidad, fórmula, ventanas, elegibilidad, disponibilidad del label, censura, denominador, umbrales, casos límite y métricas. Elegir umbrales en desarrollo/train; no mirar distribución de outcomes del holdout final. Final de historia sin desenlace maduro es censurado/desconocido, nunca negativo. Entrenar para copiar nuestro propio score contemporáneo no valida salud financiera.

### Particiones y métricas

- Alinear test con uso real: grupos nuevos, empresas conocidas hacia futuro o empresas nuevas al mismo corte son tareas distintas. Mientras se confirma, proponer evaluación conservadora temporal y por grupos; reportar escenarios separados.
- En evaluación de grupos nuevos, `group_id` de train y validación son disjuntos. En forecasting de empresas conocidas, distinguir explícitamente ese protocolo.
- Para cada fila de train, label debe estar observable antes del corte de entrenamiento; purgar ventanas cuyo horizonte invade validación. `GroupKFold` por sí solo no lo hace.
- Guardar índices/versiones/cortes. Usar 4–5 folds solo con suficientes grupos, eventos y ventanas maduras; reducir folds/horizonte si no hay soporte.
- Imputación, escalado, selección, caps, calibración, umbrales y pesos de ensemble se aprenden solo en train/validación interna. Reservar test final sin tuning.
- Regresión: MAE/RMSE en unidades del target, Spearman como diagnóstico de orden. No comparar por MAE score 0–100 contra delta de otra escala.
- Eventos: PR-AUC, precisión, recall y falsas alertas, con soporte y prevalencia; calibración/Brier cuando se entreguen probabilidades. Sin clases suficientes, métrica no estimable.
- Reportar errores por empresa/grupo y también por fila, cobertura y cohortes de historia corta, moneda y faltantes. Intervalos por remuestreo de grupos cuando sea viable; no asumir filas temporales IID.

## 9. P5–P6: baseline, modelo y criterio de precisión

1. **Baseline compatible:** persistencia o valor central aprendido en train para nivel futuro; cero cambio para delta; prevalencia para evento. Índice de reglas se evalúa por coherencia/trazabilidad, no como predictor de otra magnitud sin puente validado.
2. **Modelo pequeño:** regresión regularizada, logística u ordinal según target. Analizar errores y estabilidad antes de aumentar complejidad.
3. **Un competidor:** árboles con gradient boosting; LightGBM es candidato, no dependencia obligatoria. Seleccionar implementación según entorno y soporte, con búsqueda acotada.
4. **Selección:** mejora en métrica principal bajo particiones congeladas, estabilidad por cohortes y coste de explicación. Definir umbral práctico de mejora antes de comparar, no después de ver test.
5. **Decisión:** si no mejora consistentemente, conservar baseline. Si cobertura/desenlace no permiten validar, abstenerse de presentar predicción fiable. Evaluar test final una vez por versión congelada y documentar cambios posteriores como nuevo experimento.

Un índice 80/100 no significa 80 % de probabilidad. Explicaciones deben referirse al output realmente predicho. Mostrar incertidumbre validada cuando pueda estimarse y cobertura por separado; no inventar intervalos a partir de cantidad de datos.

## 10. Alternativas opcionales y cuándo activarlas

| Alternativa | Condición de activación | Control / motivo para posponer |
|---|---|---|
| Ensemble promedio o `1/MAE` | Modelos útiles con errores fuera de muestra complementarios y mejora validada | MAE individual no mide diversidad; pesos se deciden fuera del test final; manejar error cero sin fórmula ciega |
| AutoETS / forecast auxiliar | Historial suficiente y mejora frente a pronóstico ingenuo | Poca historia limita estacionalidad; generar features rolling-origin dentro de folds, nunca ajustar serie completa |
| SHAP para casos | Modelo elegido y necesidad de explicar predicciones | Empezar con mejora/deterioro/cobertura baja; mostrar valores, fechas y warnings, no solo top-5 |
| SHAP para todas las filas | Contrato de producto lo necesita y coste aceptable | Atribución no demuestra causalidad; ampliar tras estabilizar modelo |
| Detector no supervisado | Necesidad de señalar observaciones raras | Anomalía no equivale a mala salud ni reemplaza validación de outcomes |
| Modelo secuencial complejo | Evidencia de que modelos simples fallan y más historia útil lo justifica | No prioridad con mediana de 18 meses y grupos correlacionados |
| Reconstrucción de saldos | Historia completa por cuenta/moneda y conciliación independiente | Si faltan movimientos, no fabricar stock histórico |

## 11. P7–P8: explicación, entrega y anticipación

Exportar identificador, corte, versión de datos/modelo/target, predicción, unidad, cobertura, warnings y evidencia. Acordar formato con mentor: `predictions.parquet` interno es provisional, no una entrega oficial válida por sí misma. Mantener train/predict reproducibles y salida desacoplada de UI; demo puede usar resultados precalculados.

`direction` y `alert_state` tienen contrato aparte del score. Histéresis de dos meses es candidata, no regla fijada: medir sensibilidad/latencia en desarrollo. Una alerta confirmada en segundo mes se registra entonces, no se retrofecha al primero.

Bonus de anticipación: definir evento independiente, inicio frente a confirmación, primera alerta real, ventana de matching, un match por evento/signo, misses, falsas alarmas, ambas direcciones, censura y distribución de lead time. Un agregado de tres meses no identifica automáticamente fecha exacta de inicio; sin evidencia, no prometer meses de anticipación precisos.

## 12. Entregables previstos y aprobación

**Solo este plan existe como nuevo entregable.** Los siguientes son propuestas, no archivos generados ni controles aprobados:

| Entregable futuro | Contenido |
|---|---|
| Manifest de fuentes | Identidad, hashes, esquemas, versiones y disponibilidad |
| `reports/readiness/` | Calidad reproducible, cobertura, decisiones semánticas, impacto de exclusiones y aprobación G-DATA |
| Datos derivados y ledger | Parquet canónico, cuarentena y trazabilidad; originales intactos |
| `as_of_panel.parquet` | Features temporales elegibles y metadatos |
| `TARGET.md` y `future_outcomes.parquet` | Objetivo congelado, labels, maduración y censura |
| Índices de split y métricas | Cortes/grupos, baseline, cohortes, selección y test final |
| Modelo y `predictions.parquet` | Artefacto reproducible y predicción en contrato acordado |
| `evidence.json` y guía de ejecución | Razones, fuentes, versiones, límites y regeneración de demo |

Preguntas prioritarias al mentor: unidad y objetivo evaluados; métrica/formato; labels y cuándo se conocen; historia del test; significado de `payment_date` y estados; dirección de facturas/tipos/FX; snapshots frente a historial; licencia y uso externo. Cambios en unidad, semántica, disponibilidad o moneda pueden invalidar limpieza, panel, target y splits: versionar decisiones y repetir puertas afectadas.

**Aprobaciones humanas:** inicio de implementación; reglas financieras ambiguas/exclusiones relevantes; alcance G-DATA; target proxy si no hay oficial; protocolo de evaluación; modelo y claims de entrega. No convertir una respuesta del mentor en permiso automático para cambiar todo.

**Siguiente unidad de trabajo:** acordar repositorio/ruta del código conforme política actual —o aprobar explícitamente cambiarla— y después implementar exclusivamente auditor reproducible y reporte de preparación propuesto, con casos pequeños que fallen ante errores y ejecución sobre CSV reales. Revisar hallazgos antes de aprobar correcciones materiales. No arrancar modelos, ensemble ni forecast mientras preparación de la ruta elegida siga bloqueada.
