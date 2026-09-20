# Blaubeere — contexto de datos, modelos y entrenamiento

**Alcance:** síntesis de la capa analítica para incorporar a una base de conocimiento. Explica datos, decisiones de modelado, entrenamiento, evaluación y límites; no describe la aplicación. **Corte de evidencia:** 19 de septiembre de 2026.

## 1. Qué buscamos predecir y por qué

No disponíamos de una etiqueta oficial de salud financiera ni de un contrato de evaluación confirmado. Por eso definimos un objetivo concreto, verificable con los datos: **déficit operativo en al menos dos de los tres meses siguientes** (`target_deficit_3m`). Es un *proxy*: aproxima un fenómeno financiero específico, no la salud completa de una empresa.

Separaremos siempre tres conceptos: **estado observado**, **cambio de trayectoria** y **probabilidad de un desenlace futuro**. Tener déficit no implica estar empeorando; dejar de tenerlo tampoco demuestra solvencia.

## 2. Qué datos usamos y cómo los preparamos

El dataset del reto ya era sintético: **1.286 empresas, 250 grupos y 24 meses**, con tablas de empresas, productos bancarios, deuda, calendarios, transacciones y facturas. No generamos otro dataset para entrenar ni para obtener las métricas. Sí usamos casos artificiales en tests de software.

El recorrido es **CSV originales → tablas limpias Parquet → agregados mensuales → variables predictoras y etiquetas separadas**. Se comprueban hashes, conservación de filas, relaciones entre tablas, fechas, conversiones de moneda y reconciliación. Los problemas se conservan como indicadores de calidad o incertidumbre, no se eliminan silenciosamente.

La unidad analítica es **empresa × mes cerrado**, entre septiembre de 2024 y agosto de 2026. V1 utiliza exclusivamente el panel de flujos de caja contabilizados del perímetro observado, con importes en EUR cuando la conversión es verificable. No incorpora facturas, saldos actuales ni stocks de deuda como predictores. Tener esas tablas no significa disponer de historiales completos y utilizables.

## 3. Cómo construimos las variables y el objetivo

Fijamos **32 variables**: entradas y salidas operativas y de financiación, neto, volumen, incertidumbre de clasificación y estadísticas de ventanas de tres y seis meses —medias, variabilidad, tendencias y diferencias—. Los identificadores sirven para relacionar y separar empresas, no para predecir.

Las variables solo usan información hasta el mes de origen. La etiqueta se obtiene de los tres meses posteriores, mediante cotas que conservan la incertidumbre de clasificación y exigen una conclusión consistente con y sin atípicos. **Si no puede determinarse el desenlace, queda desconocido; no se convierte en «sin déficit».**

Los huecos históricos se mantienen. Un importe desconocido o una conversión ausente no se rellena con cero. Si falta evidencia mínima para inferir, se devuelve una abstención con motivo.

## 4. Cómo entrenamos y evitamos fugas de información

Entrenamos un modelo conjunto, no uno por empresa. Antes del ajuste se fijaron variables, candidatos, criterios y particiones:

- **150 grupos para entrenamiento, 50 para validación y 50 para test.** Todas las empresas de un grupo permanecen juntas, evitando compartir información de empresas relacionadas entre particiones.
- **Dos validaciones temporales:** entrenamiento anterior y evaluación posterior; las etiquetas de entrenamiento debían haber madurado antes de evaluar.
- **Cuatro candidatos:** dos regresiones logísticas y dos modelos de gradient boosting con histogramas, frente a una probabilidad constante calculada en entrenamiento. Las transformaciones aprendidas se ajustaron solo con entrenamiento.

Ganó **`hgb_leaf15`** por el menor Brier medio de validación, tras superar los criterios mínimos en ambas ventanas. Se ajustó finalmente con entrenamiento y validación: **3.630 filas etiquetadas de 112 grupos**, con desenlaces disponibles hasta marzo de 2026. El test utilizó grupos no entrenados y orígenes de abril–mayo de 2026.

**El test final se abrió una sola vez.** No se retocó el modelo después de ver sus resultados.

## 5. Qué resultados obtuvimos y qué significan

| Test interno v1 | Referencia constante | Modelo |
| --- | ---: | ---: |
| Average precision, mayor es mejor | 0,511 | **0,816** |
| Brier, menor es mejor | 0,252 | **0,188** |

La mejora relativa de Brier fue **25,1 %**, con intervalo del 95 % por remuestreo de grupos de **12,0 %–38,1 %**. AP resume calidad de ordenación, no «81,6 % de aciertos».

Solo **131 de 299 casos empresa-mes** tenían desenlace observable; los otros 168 quedaron censurados. Es validación interna sobre datos sintéticos y una población parcialmente observable, no test oficial ni validación externa. La calibración de las probabilidades no está demostrada y la disponibilidad histórica al cierre mensual es un supuesto retrospectivo.

En la inferencia de agosto se conservaron las 1.286 empresas: **1.010 estimaciones y 276 abstenciones**. Esto mide cobertura, no nuevos aciertos. La salida es una probabilidad del proxy, nunca un score de salud calculado como `100 × (1 − probabilidad)`.

## 6. Qué añaden v2 y v3

**V2 no reentrena v1.** Añade reglas de trayectoria: compara tres meses con los tres anteriores y confirma señales tras dos meses consecutivos. Evalúa anticipación de mejora y deterioro por separado, en una reserva temporal de empresas conocidas, excluyendo los grupos del test v1.

El resultado fue negativo: anticipó **2/37 mejoras y 1/41 deterioros**, con **96 % y 98 % de falsas alertas entre las resolubles**. Sirve para describir trayectoria, pero no demuestra anticipación útil. No se ajustaron las reglas contra esa reserva. Estas métricas no son comparables directamente con AP/Brier de v1: evalúan otros desenlaces.

**V3 sigue en desarrollo:** incorpora métodos de caja reconstruida, deuda, crecimiento y mora, con pruebas sobre casos artificiales. No hay score financiero integral validado. Faltan evidencia completa de obligaciones/saldos/pagos y una reserva nueva no utilizada; reutilizar los tests anteriores no resolvería ese bloqueo.

## 7. Trazabilidad y fuentes

Datos, protocolos, modelo, predicciones y resultados se vinculan mediante versiones, hashes y recibos. La reproducción registrada de inferencia v1 coincidió exactamente con las predicciones guardadas, sin reentrenar ni reabrir el test. **Reproducibilidad del software no equivale a eficacia financiera.**

- [Diccionario de datos](../data_dictionary.md).
- [Informe de entrenamiento y evaluación v1/v2](../reports/modeling/training-report.md): metodología completa, parámetros, particiones, métricas y artefactos.
- [Auditoría v3](../reports/modeling/financial-v3/audit-summary.json) y [contrato de desarrollo](../reports/modeling/financial-v3/design-proposal.md): posibilidades y bloqueos de la siguiente etapa.
