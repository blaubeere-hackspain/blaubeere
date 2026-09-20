# Goal v2 — mejorar el modelo con pi-autoresearch y score_v4

## Objetivo

Usar `healthscore_v4` como punto de partida para mejorar el modelo predictivo con los 24 meses disponibles. Pi-autoresearch debe proponer cambios, medirlos, conservar mejoras y aprender de los descartes. Se optimiza el modelo, no la herramienta pi-autoresearch.

Mantener separados **nivel financiero observado, dirección y pronóstico**. Buscar evidencia para responder quién está sano, quién mejora, quién empieza a deteriorarse, bache o caída persistente, por qué cambia y cuándo se pudo anticipar. Un proxy de cobros no valida por sí solo las seis respuestas.

## 1. Reutilizar score_v4 y preparar datos comparables

Leer [informe v4](../../reports/score_v4/INFORME.md), [resumen publicado](../../reports/score_v4/summary.json) y [calibración v4](../../reports/calibration_v4/report.md). Reutilizar [motor](../../xray/scoring_v4.py), [adaptador](../../xray/scoring_io_v4.py) y sus tests; no rehacer el algoritmo.

- Partir de `k=4178.45`, `alpha=3`, `beta=0.25`. La calibración existente los revalidó por sensibilidad y estabilidad, **no por capacidad predictiva**. `summary.json` todavía declara calibración pendiente: registrar esa diferencia de revisión sin regenerar resultados publicados.
- Aprovechar cobros/pagos, servicio y déficit de deuda, obligación vencida, mora, confianza, score y sus cambios temporales, sujetos a disponibilidad en cada corte. Obligación vencida es stock: entra una vez, no una por mes de ventana.
- Verificar identidad de empresas, grupos, meses y fuentes antes del join: score_v4 referencia el run `20260919T022951Z-83143e8d`; modelado anterior usa `20260919T124814Z-e5302b58`. Coincidir en claves no garantiza coincidir en datos.
- Separar features admisibles de reconstrucciones retrospectivas. Caja reconstruida desde un ancla futura y estados de facturas sin disponibilidad histórica demostrada quedan fuera del benchmark predictivo. Recalcular una variante admisible desde inputs; quitar una columna no elimina la fuga de un score que ya la incorpora.
- La [exclusión de ceros persistentes](../../xray/score_exclusions_v4.py) usa historial completo y agosto de 2026. Mantener la decisión publicada, pero no usar esa lista futura para seleccionar cohortes históricas. Preparar datos experimentales sin esa exclusión (`--no-excluir-cero-persistente`) en una salida nueva; fijar elegibilidad solo con información del origen.
- Medir candidatos → inputs utilizables → etiquetas observables por mes, grupo y clase. Investigar cobros sin clasificar, pagos simbólicos y confianza ausente antes de ajustar parámetros. Desconocido no equivale a cero ni a empresa insana.

**Salida:** dataset experimental trazable, catálogo de features admisibles y diagnóstico de cobertura. Si una señal solo admite lectura retrospectiva, conservarla como diagnóstico, no como evidencia de anticipación.

## 2. Congelar el benchmark antes de optimizar

Reutilizar como referencia el [experimento predictivo existente](../../scripts/financial_v3_dataset_model.py) y sus [métricas](../../scripts/financial_v3_dataset_model_metrics.py), sin modificar protocolos históricos.

1. Partir de los targets a tres meses de contracción/expansión de cobros: ≤80% / ≥120% de la media de los tres meses del origen, en al menos dos de los tres siguientes. Etiquetas independientes del score; seguimiento insuficiente queda desconocido. Documentar cualquier cambio de elegibilidad antes de generar baselines. Recuperación/persistencia se evalúa aparte cuando tenga soporte.
2. Fijar cohortes, fechas, separación por grupo y purga por maduración. Imputación, escalado, calibración probabilística y umbrales se ajustan solo dentro de train/desarrollo interno. Respetar holdout v1 y reserva v2; datos ya explorados siguen siendo desarrollo.
3. Métrica principal: **Brier medio por grupo, promediado por igual entre ambos targets; menor es mejor**. Publicar también Brier por target, precisión, recall, calibración, falsas alertas, anticipación y cobertura; nunca omitir el target que empeora.
4. Fijar mínimos numéricos de soporte por clase/grupo, cobertura y utilidad de alertas antes del primer ensayo. Tomar los gates existentes como referencia y justificar los elegidos; si no hay soporte, resolver datos o cerrar como insuficiente, no rebajar gates tras medir. Una solución sin alertas no demuestra anticipación.
5. Medir sobre las mismas filas: prevalencia, persistencia/tendencia, predictor actual y predictor sencillo con componentes v4 admisibles. Un score 0–100 no es una probabilidad: cualquier conversión se aprende en train. Publicar además cobertura sobre toda la población prevista.

**Salida:** protocolo, splits, etiquetas, gates y baseline reproducibles y congelados. Ninguno cambia dentro del bucle. Si cambian, cerrar la búsqueda y versionar otro protocolo con nuevos baselines.

## 3. Preparar pi-autoresearch

Al autorizar la ejecución, usar un entorno de experimentación aislado y limpio con el código y datos necesarios: `keep` crea commits y los descartes revierten cambios. No ejecutar esas operaciones sobre el worktree con trabajo pendiente.

Crear la configuración nativa, sin construir otro orquestador:

| Archivo | Contenido |
| --- | --- |
| `.auto/prompt.md` | Este plan, comando exacto, métrica, archivos editables, restricciones y aprendizajes |
| `.auto/measure.sh` | Evaluador fijo; emite `METRIC brier_macro_group=...` y métricas secundarias |
| `.auto/checks.sh` | Tests financieros, ausencia de fuga, integridad del protocolo y gates de cobertura/utilidad; fallo bloquea `keep` |
| `.auto/config.json` | `maxIterations: 20`; fijar también límite total de fits, hilos y timeout antes del arranque |
| `.auto/log.jsonl` | Registro nativo de todos los ensayos, incluidos descartes y fallos |

La allowlist de escritura incluye solo implementación/configuración del candidato y memoria del experimento. Datos, evaluador, labels, splits, gates y tests de aceptación quedan protegidos. Reutilizar dependencias instaladas. Guardar outputs en rutas nuevas bajo `reports/modeling/financial-v4/`, nunca sobre `reports/score_v4/` ni resultados anteriores. Preservar evidencias de cada ensayo fuera del alcance de las reversiones y enlazarlas desde el log.

**Salida:** baseline ejecutado con el mismo runner del bucle, checks comprobados y presupuesto registrado. Las 20 evaluaciones incluyen baselines, repeticiones y fallos; no reiniciar segmentos para ampliar presupuesto.

## 4. Iterar con una hipótesis por ensayo

Orden de búsqueda:

1. **Aportación de v4:** comparar predictor sin/con componentes admisibles, score admisible, deltas y tendencias. Separar nivel, cambio y calidad de observación.
2. **Memoria temporal:** comparar ventanas cortas/largas y agregaciones de tendencias dentro del catálogo fijado.
3. **Parámetros del candidato:** probar `k`, `alpha` y `beta` sin tocar defaults publicados; vigilar estabilidad, sensibilidad y reactividad, no optimizar solo una curva más suave.
4. **Redundancia de mora:** ablations del triple castigo —obligación, multiplicador y mora— y variante de beta atenuado cuando ya hay obligación vencida. Son candidatos experimentales, no cambios automáticos de la fórmula publicada.
5. **Estimador:** regresión regularizada y después boosting ya disponible; calibración probabilística dentro de train. Preferir el candidato más sencillo si no hay ganancia relevante.

Ciclo: **hipótesis → cambio mínimo → `run_experiment` → checks → `log_experiment` → siguiente hipótesis**.

- `keep` solo si mejora frente al mejor candidato/baseline comparable y cumple todos los gates; igualdad o empeoramiento → `discard`; errores → `crash` o `checks_failed`.
- Registrar configuración, semilla, revisión de código/datos, métricas, cobertura, predicciones y motivo de decisión. Usar `asi` para guardar qué se aprendió y qué probar después; actualizar `.auto/prompt.md` para reanudar sin repetir ideas descartadas.
- Si la ganancia es pequeña, confirmarla con las semillas/ventanas fijadas, dentro del presupuesto. La confianza mostrada por pi-autoresearch no sustituye incertidumbre por grupo ni validación temporal.
- Si una avería persiste tras una corrección focalizada y su recheck, detener y registrar el bloqueo.

## 5. Cerrar con evidencia

Al agotar presupuesto, reproducir el mejor candidato y compararlo con todos los baselines bajo el protocolo congelado. Usar un control fuera del bucle solo si estaba reservado y es admisible; declarar cualquier exposición previa, sin presentarlo como test independiente nuevo.

Entregar configuración/modelo reproducible, predicciones por empresa/mes, historial completo y resumen breve: mejora medida, cobertura, fallos y cuáles de las seis preguntas quedan respaldadas. Sin mejora válida, conservar el baseline y documentar el resultado negativo.

**Fuera de este plan:** frontend, backend, HTML, despliegues, reparación de cachés y documentación de producto. Esta revisión define el trabajo; no inicia experimentos ni modifica modelos publicados.
