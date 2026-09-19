# Plan de desarrollo: scoring financiero y predicción temporal

**Estado: PROPUESTA.** Los 23 artefactos de datos versionados (ocho raw, ocho clean y siete marts) son punteros Git LFS no materializados. El pipeline ejecutable `xray` y sus siete marts existen; informes y `reports/build_manifest.json` registran una ejecución histórica `20260919T022951Z-83143e8d` en revisión `86439b6`, no una reproducción local del `HEAD` actual. No hay modelo predictivo entrenado ni target oficial aceptado; los targets proxy existentes son candidatos que requieren aprobación/evaluación.

**Recomendación:** validar fuentes y semántica → producir datos derivados auditables → aprobar preparación para un alcance concreto → congelar target y validación → baseline → un modelo competidor → explicación y entrega. Si una fuente opcional no es fiable, reducir alcance; no inventar información para conservarla.

“100 % preparados” significa cumplir criterios de aceptación del uso elegido, no disponer de información perfecta. La precisión será un resultado medido sobre un objetivo explícito, no una propiedad garantizada de antemano.

## 1. Decisiones principales

| Tema | Propuesta |
|---|---|
| Fuente | Ocho raw, ocho clean y siete marts son punteros Git LFS no materializados; `data_dictionary.md` sí existe. El manifest de readiness que enumera 16 es histórico y no prueba ausencia actual |
| Repositorio | Monorepo Bun/Rust con aplicaciones/servicios Next/Axum; `.gitignore` es un ignore habitual, no una política de ubicación. `reports/planning/` conserva este plan; ubicación de código queda como decisión de diseño |
| Procesamiento | Pipeline batch ejecutable (`xray`) con ingesta, limpieza, clasificación, evidencia FX y siete marts; su manifest histórico no prueba reproducción local en `HEAD` actual |
| Unidad provisional | Empresa × mes cerrado × moneda, hasta justificar consolidación |
| Moneda | Features internas por empresa × mes × moneda; consolidar solo moneda de reporte por empresa con contrato FX verificado |
| Núcleo candidato | Flujos transaccionales, con clasificación y cobertura verificadas |
| Fuentes complementarias | Facturas, deuda y saldos solo para usos que superen sus controles |
| Salidas diferentes | Índice descriptivo actual, trayectoria y predicción de un desenlace futuro |
| Validación interna principal | Generalización a grupos retenidos y fechas posteriores; forecasting de empresas conocidas es diagnóstico secundario |
| Modelo | Un modelo conjunto sobre filas elegibles; no una red por empresa |
| Calidad | Cobertura y límites visibles; falta de fuente no equivale a mala salud |
| Fuera de alcance inicial | Decisiones de crédito/pagos, predicción de quiebra sin labels, streaming, microservicios, LLM calculando score |

No confundir millones de movimientos con millones de ejemplos independientes: existen 1.286 empresas relacionadas en 250 grupos y ventanas temporales solapadas. Datos sintéticos permiten evaluar el experimento, no demostrar precisión en empresas reales.

## 2. Evidencia inicial y límites

**Informe existente/histórico:** afirmación extraída de `reports/quality/` y manifest de ejecución `20260919T022951Z-83143e8d` (`86439b6`); inputs y Parquet actuales no están materializados, así que no es reproducción del `HEAD` actual. **Borrador anterior no verificado:** número solo de versiones previas. **Pendiente:** semántica o comprobación aún sin resolver. `data_dictionary.md` describe dataset sintético, IDs y campos; no resuelve interpretación económica ni disponibilidad histórica.

| Hallazgo | Evidencia / implicación |
|---|---|
| Filas: grupos 250; empresas 1.286; productos bancarios 5.987; deuda 2.239; schedules 87; saldos 7.996; transacciones 2.556.437; facturas 897.894 | Informe existente: `reports/quality/summary.md`, «Recuento de filas por tabla»; reproducir con fuentes materializadas |
| Empresas con transacciones 1.286; facturas 785; deuda 378 | Informe existente: `reports/quality/summary.md`, «Cobertura cruzada de empresas»; falta de registros no significa importe cero |
| Historia mensual cerrada: mínimo 1, mediana 18, máximo 24; ocho empresas con menos de seis meses | Informe existente: `reports/quality/summary.md`, «Distribucion de meses de historia por empresa»; recalcular tras filtros y comprobar continuidad |
| Meses cerrados: 2024-09-01 a 2026-08-01; septiembre de 2026 es abierto y tiene 9.242 transacciones | Informe existente: `reports/quality/summary.md`, «Distribucion de meses…», y `reports/quality/facts.json`, `transactions.flag_counts.open_month`; mes parcial no comparable |
| Producto sin resolver: 1.314 transacciones y 29 saldos | Informe existente: `reports/quality/facts.json`, `transactions.flag_counts.fk_orphan` y `dimensions.json`, `balances.flag_counts.fk_orphan`; aislar joins afectados con trazabilidad |
| Estado ausente en 29.839 transacciones | Informe existente: `reports/quality/facts.json`, `transactions.flag_counts.status_missing`; estados no prueban disponibilidad histórica |
| Sin categoría: 635.530 (`category_missing`) y 635.860 (`category_norm` nulo) | Informes existentes: `facts.json`, `transactions.flag_counts.category_missing`, y `summary.md`, «Huecos de datos»; reproducir antes de adoptar una definición |
| Facturas: flag `fx_not_convertible` 108.445; notes: 118.055 `acct_no_eur` + 0 `currency_ausente` + 91 `rate_invalido_cross` = 118.146 | Informe histórico: `reports/quality/facts.json`; notes no descomponen flag: un nominal EUR puede convertirse por identidad aunque moneda contable no sea EUR |
| Transacciones: flag `fx_not_convertible` 160.207; notes: 1.314 huérfanos + 20 rate inválido no EUR | Informe histórico: `reports/quality/facts.json`; notas incompletas, no reconciliación exacta actual |
| Deuda: 1.351 saldos negativos, 145 positivos y 743 cero | Informe histórico: `reports/quality/dimensions.json`; conteos no resuelven significado económico del signo |
| Seis saldos con flag `sentinel_hidden_balance`: `-999999999` dos veces y cuatro positivos distintos una vez | Informe existente: `reports/quality/dimensions.json`, `balances.flag_counts.sentinel_hidden_balance` y `balances.notes`; positivos no están confirmados independientemente como centinelas frente a outliers |
| Claves, fechas extremas, estados de documentos, contraparte, tipos de factura y presencia de labels | Borrador anterior no verificado; conservar como preguntas de auditoría, no como resultados de inspección actual |
| Semántica de deuda (`saldo`, fecha, tipo), facturas y disponibilidad histórica | Pendiente pese al diccionario: confirmar con propietario/fuente, sin inferir cliente/proveedor ni historial contractual |

**FX implementado, evidencia histórica:** el limpiador conserva identidad para nominal EUR de factura aunque `accounting_currency` difiera, e identidad misma-moneda; tipo contradictorio queda marcado. Entre monedas distintas, tipo `1`, no finito, nulo o inválido deja EUR desconocido; no estima FX. Para transacciones aplica `amount_eur = amount / exchange_rate`; para facturas `amount_acct = amount / exchange_rate`. Q1/Q2/Q5 en `reports/quality/facts.json` son evidencia histórica del informe, no pruebas reproducidas aquí. La pregunta económica del signo de deuda sigue separada de facturas.

Rutas de evidencia: `reports/quality/{summary.md,facts.json,dimensions.json}`, `reports/build_manifest.json`, `xray/{cli.py,pipeline.py}` y `reports/vistas_y_hallazgos.md`. El manifest de readiness enumera históricamente 16 punteros; hoy hay 23 punteros LFS. El build manifest registra hashes, dependencias, código, outputs, parámetros y tests de aquella ejecución, pero no prueba reproducción local en `HEAD` actual. Inventario/script de readiness son inventario separado, no auditoría raw completa.

**Limitación del diagnóstico:** reutilizar primero pipeline, contratos y documentación existentes; recuperar solo metadata faltante o confirmación del autor para semántica/materialidad. Tras materializar snapshot autorizado, comparar identidad, conteos, reglas y outputs con el manifest histórico. `reports/readiness/{manifest.json,inventory.md,schema_contract.json}` y `scripts/audit_financial_data.py` registran disponibilidad/contrato provisional e inventario; no sustituyen auditoría raw ni linaje reproducido actual. Mantener métricas con definiciones distintas.

## 3. Fases, puertas de avance y alternativas

| Fase | Trabajo y entregable previsto | Criterio para avanzar | Si no se cumple |
|---|---|---|---|
| P0. Contrato e inventario | Revisar pipeline, contratos, diccionario, readiness histórico y contrato del reto; registrar unidad, target, horizonte y disponibilidad | Fuentes/uso provisional identificados; reglas, input y salida verificables | Pedir confirmación acotada sin fijar semántica por intuición |
| P1. Auditoría reproducible | Tras materializar, verificación focalizada de snapshot, conteos, reglas, outputs y manifest histórico | Problemas clasificados, cuantificados y localizables | Ampliar solo comprobación del problema concreto |
| P2. Datos derivados | Reutilizar limpieza existente tras verificación; cuarentena y ledger solo si hace falta | Reglas justificadas; ninguna modificación silenciosa | Excluir campo/fuente o demo honesta |
| P3. Panel as-of | Verificar/reutilizar `panel_flujos`, `panel_deuda`, `panel_cobro`, `panel_evidencia`, observabilidad y contrato de features | Pruebas temporales, reconciliación y elegibilidad aprobadas | Reducir features, cohortes u horizonte |
| G-DATA. Aprobación | Informe de preparación del alcance elegido | Todos los controles críticos de esa ruta pasan | No ajustar modelos |
| P4. Target y split | Aprobar/evaluar `targets_proxy` existente o fijar `TARGET.md`/índices | Desenlace defendible, maduro y evaluación congelada | Índice descriptivo o proxy explícitamente aprobado |
| P5. Baseline | Predicciones simples y métricas reproducibles | Referencia equivalente y errores entendidos | Corregir protocolo antes de complejidad |
| P6. Modelo | Modelo candidato, comparación y explicaciones de casos | Mejora relevante y estable bajo protocolo congelado | Conservar baseline o abstenerse |
| P7. Entrega | JSON validado para `ASSESSMENT_FILE`, evidencia y demo | Contrato interno validado; resultado reproducible | Demo interna etiquetada, no envío oficial válido |
| P8. Extensiones | Ensemble, forecast auxiliar, explicaciones amplias y alertas | Necesidad concreta y beneficio comprobado | No construirlas |

P0–P8 son mapa, no aprobaciones seriales. El contrato provisional de uso se discute en P0; labels y ajuste esperan controles aplicables. Reloj histórico: 19 sep. 2026 (+02); evento: 18–20 sep. Referencias: vault «HackSpain - Fuentes X Ray», F0, para alcance del reto; «HackSpain - Estudio X Ray», §§12.2–12.4, para checkpoints, stop rules y freeze. Esos checkpoints son horas productivas relativas, no agenda oficial.

**Timeboxes propuestos tras corte 19 Sep:** 30–60 min para confirmar inputs/contrato e inspeccionar pipeline existente; checkpoint: si materialización o semántica bloquea, elegir ruta elegible o fallback fixture etiquetado. Siguientes 60–90 min: producir/validar mínimo JSON app usando marts existentes solo si elegibles; no prometer completar datos. Confirmar deadline exacto del evento por separado; reducir alcance, no controles.

Stop rules: sin labels oficiales, no target oficial supervisado (solo baseline descriptivo/proxy); sin stock de caja reconstruido, no ratios históricos; sin mapping, descartar contraparte; si modelo no mejora, baseline; sin FX, no mezclar monedas; salida oficial desconocida, producir muestra y preguntar, no optimizar leaderboard.

## 4. P0–P1: validar antes de corregir

### Inventario y reproducibilidad

- Usar `reports/readiness/manifest.json` como inventario histórico de 16 rutas, no prueba de ausencia actual. Confirmar 23 punteros LFS actuales; tras materializar, registrar bytes, SHA-256, encoding, delimitador, esquema, filas, versión y fecha de extracción conocida; comparar SHA-256 calculado con OID LFS. Separar descarga de disponibilidad del dato.
- Mantener raw inmutable. Comparar fuentes materializadas con informes previos por identidad y definición, no solo por número de filas.
- Congelar snapshot de trabajo. No publicar datos ni subirlos a servicios externos; confirmar licencia y política del reto.
- Reutilizar Python/DuckDB del pipeline `xray` y sus dependencias documentadas; no elegir otro stack ni reconstruir limpieza sin un problema demostrado.

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
| Convención FX reportada, semántica pendiente | Reproducir casos/invariantes y confirmar por fuente moneda destino y operación | Mantener moneda separada; excluir agregados que requieran conversión |
| Producto huérfano | Resolver con referencia autorizada o aislar joins afectados | No inventar entidad ni moneda |
| Duplicado técnico/económico | Deducir identidad y precedencia antes de deduplicar | Preservar si puede representar eventos diferentes |
| Saldo centinela/outlier | Confirmar centinela o marcar dato no utilizable | No borrar saldos negativos legítimos ni winsorizar con test |
| Estado pending/ausente | Política por uso; excluir del flujo realizado si justificado, registrar cobertura | Estado final `booked` no prueba que estuviera disponible en cada corte |
| Falta de facturas/deuda | Marcar fuente no observada | Nunca reemplazar por cero facturación/deuda |
| Mes sin movimientos | Comprobar cobertura y conexión de fuentes | Ausencia desconocida ≠ actividad cero |

Cada cambio registra `source_id`, campo, valor original, valor derivado, regla/version, motivo y disposición. Repetir proceso debe producir mismo resultado. Reconciliar filas leídas, retenidas, excluidas y en cuarentena sin doble conteo; importes antes/después por moneda y causa. Registrar exclusiones de campos por separado de exclusiones de filas.

No hace falta resolver todo el dataset para avanzar: un problema de facturas puede bloquear solo esa rama. La ruta transaccional requiere igualmente controles propios de estados, moneda, clasificación, cobertura y disponibilidad histórica. Si solo disponemos de extracción final, documentar supuesto retrospectivo y no presentarlo como reconstrucción probada de lo conocido en tiempo real.

### 5.1. Piloto Jev: clasificación y apoyo a revisión para reporting (P2–P3)

**Estado:** incorporado al planning; no implementado ni evaluado. Piloto opcional, fuera del camino crítico de baseline/export. Jev propone clases de movimientos a partir de descripciones y contexto autorizado; no sustituye al predictor de P6, no calcula salud financiera ni resuelve por sí mismo semántica contable, FX, signos, ownership o disponibilidad histórica. No se ajusta un modelo antes de G-DATA: esta rama evalúa inferencia de un modelo preentrenado para preparar datos, con controles propios de fuentes y clasificación.

**Oportunidad, no resultado esperado:** `reports/quality/flows.json` registra históricamente 498.080 movimientos `unknown` (19,84 % de filas; 5,12 % del volumen absoluto convertible a EUR), en meses cerrados y estado booked. No es una medición local reproducida ni una estimación de cuánto resolverá Jev. Mayor cobertura nominal no prueba mayor exactitud.

Flujo propuesto: **reglas verificadas → casos sin resolver → propuesta Jev → controles/abstención → revisión → promoción explícita**. Conservar originales y reglas actuales. Usar las clases de `xray/flows.py`, incluido `unknown`; signo/dirección y restricciones económicas se validan en código. Una salida tipada válida puede ser económicamente incorrecta. No deducir transferencia interna únicamente de una descripción genérica ni fabricar contrapartes.

#### Extensión prioritaria: clasificar casos para revisión humana

Reutilizar flags, conflictos y `motivos_*` de `xray/marts/evidencia.py` para construir una cola local de revisión enlazada a las fuentes. Las reglas resuelven el enrutamiento evidente y la prioridad por materialidad, sin mezclar monedas; Jev solo propone una categoría de revisión aprobada para descripciones o conceptos que sigan siendo ambiguos. No puede cambiar estados de evidencia, eliminar casos críticos de la cola ni aprobar correcciones o publicación.

Comparar contra la cola basada únicamente en reglas/materialidad: casos accionables por hora de revisión, errores de enrutamiento, casos críticos omitidos y abstención, con referencia humana independiente. Activar solo si existe un cuello de botella y mejora comprobable; si bastan las reglas, omitir Jev. Esta es la primera extensión propuesta, no un requisito adicional para baseline/export.

#### Experimento de movimientos y puerta de promoción

1. Confirmar autorización de tratamiento externo y materializar la muestra permitida; hasta entonces, solo ejemplos inventados sin datos del reto. Anonimizar no sustituye autorización. Minimizar texto/contexto enviado y no enviar identificadores, cuentas o importes exactos innecesarios.
2. Etiquetar manualmente una muestra estratificada por clase, empresa/grupo, idioma, patrón de descripción y materialidad, con casos desconocidos y controles ya resueltos. Separar desarrollo y evaluación por grupos/tiempo y evitar que descripciones repetidas equivalentes contaminen ambas partes. La etiqueta de referencia no puede ser simplemente la propia predicción o regla que se pretende evaluar.
3. Congelar taxonomía, instrucciones, contexto as-of, muestra, criterios de aceptación y umbrales por clase antes del conjunto de evaluación. Medir precisión/recall por clase, abstención, cobertura aceptada, errores ponderados por importe por moneda y confusiones transferencia/operativo/financiación. Comprobar calibración local; no interpretar `confidence` como probabilidad de acierto sin validación ni fijar un umbral universal.
4. Ejecutar en paralelo sin modificar los marts, targets ni reporting publicado. Comparar reglas frente a reglas + propuestas aceptables, mostrando impacto en neto operativo y límites de incertidumbre. Mantener `unknown` cuando falte evidencia, haya contradicciones o falle el servicio; baja confianza y entradas no puntuables no son clasificaciones aceptadas.
5. Promover solo con mejora material, errores de alto impacto aceptables y aprobación humana explícita. Reconciliación, integridad de filas/importes y controles as-of deben seguir pasando. Si cambia la clasificación utilizada por features o `targets_proxy`, versionar y regenerar ambos y repetir puertas/evaluaciones afectadas; no presentar labels derivados del propio clasificador como validación independiente de salud financiera.

Registrar por propuesta referencia local al origen, hash del input autorizado, versión de taxonomía/instrucciones, ruta/proveedor, modelo solicitado y versión efectiva si está expuesta, fecha, respuesta, probabilidades, confianza, decisión de aceptación/rechazo y motivo. Guardar resultados en almacenamiento local controlado para reproducir sin nuevas llamadas; no versionar textos sensibles ni respuestas que los contengan. Un alias de modelo no garantiza pesos inmutables: si no se puede fijar versión, declarar el límite y conservar el snapshot de resultados. Acotar concurrencia, reintentos, cuota y presupuesto; ningún fallback silencioso a otro modelo o proveedor.

#### Controles compartidos, aceptación independiente por uso

Clasificación de movimientos, revisión humana, normalización documental (§5.2) y control semántico (§11.1) requieren muestras, referencias humanas, taxonomías, métricas y criterios de aceptación propios. Aprobar un uso no aprueba los demás. Reutilizar autorización de datos aplicable, auditoría, presupuesto, separación desarrollo/evaluación y abstención; registrar también el uso evaluado. Si cambia cualquier derivado usado por features o targets, aplicar la regeneración y reevaluación del punto 5. Jev selecciona salidas tipadas predefinidas; no redacta informes libres ni sustituye aritmética, validación contable o aprobación G-DATA.

#### Acceso y configuración prevista

**Ruta acordada: `classifier.dev` como principal; Vercel AI Gateway → Jev como fallback explícito.** No llamar a ambas rutas para cada entrada. Las referencias previas de Gateway se consultaron el **19-09-2026**; tarifas, catálogo y límites de ambas rutas deben reconfirmarse antes de ejecutar.

| Ruta | Uso propuesto | Requisitos y límites |
|---|---|---|
| `https://classifier.dev/` | Principal para los pilotos autorizados, con `tier: "fast"` | API HTTP sin cuenta, clave ni SDK; 3.000 clasificaciones/minuto y 20.000/día por IP, hasta 1.000 inputs/request. No autoriza reclasificar todo el dataset ni elimina controles de privacidad |
| Vercel AI Gateway → Jev | Fallback previamente habilitado y autorizado | Modelo `typesafe-ai/jev`; tarifa publicada 0,042 USD/millón de tokens de entrada. Elegibilidad de Jev para créditos gratuitos y saldo de nuestra cuenta pendientes; exige credencial, presupuesto y política de datos aprobados |

**Skill del agente:** `bulk-classify`, instalada globalmente en `~/.agents/skills/bulk-classify/SKILL.md` desde [classifier.dev/skill.md](https://classifier.dev/skill.md). Es apoyo para operar la API, no una dependencia del pipeline ni permiso para enviar datos. Para esta ruta basta HTTP; CLI `classifier-dev` y MCP son opcionales, no requisitos de instalación.

**Principal — configuración prevista:**

1. Usar `POST https://classifier.dev` con JSON (`labels`, `inputs`, `tier: "fast"` e `instructions` cuando corresponda), `Content-Type: application/json` y un `User-Agent` identificable. Evitar textos en URLs; mantener llamadas fuera del dashboard y del build determinista `xray`. Probar conectividad con ejemplos inventados antes de cualquier muestra autorizada.
2. Definir 2–100 etiquetas semánticas, incluida salida explícita `unknown`/«ninguna de estas» cuando corresponda; una elección forzada con confianza alta no demuestra pertenencia. Validar orden, número y esquema de resultados. `unscored`, confianza nula o insuficiente y evidencia contradictoria implican abstención; no adoptar los umbrales de ejemplo de la skill como criterios financieros.
3. Acotar lotes a cuota restante y 1.000 entradas, cada una de hasta 32.000 caracteres. Limitar concurrencia, tiempos de espera y reintentos; respetar `Retry-After` y cabeceras de cuota. Registrar modelo por resultado, `modelsUsed`, ruta y decisión junto con la trazabilidad existente; conservar snapshot local para evitar nuevas llamadas al reproducir.

**Política de fallback:** tras timeout o errores transitorios `429`/`5xx` que persistan después de reintentos acotados, o respuesta de un modelo no aprobado, usar Gateway solo para entradas sin resultado elegible y si esa ruta ya cumple permisos, acceso y presupuesto. Registrar motivo, intentos y ruta efectiva; conservar respuestas aceptadas sin reclasificarlas. Baja confianza, `unknown`, `unscored` o errores de entrada no disparan cambios de proveedor para forzar una etiqueta. Si Gateway no está habilitado o falla, mantener abstención y revisión humana, nunca inventar resultado ni bloquear baseline/export.

**Fallback — configuración previa a su habilitación:** Vercel Gateway se puede consumir desde local/Jio; no requiere desplegar la app en Vercel ni alojar `classifier.dev`.

1. En el equipo Vercel, comprobar acceso al modelo, crédito elegible y presupuesto autorizado. Crear una clave dedicada de AI Gateway con límite de gasto; no comprar créditos ni activar recargas sin aprobación.
2. Inyectar `AI_GATEWAY_API_KEY` solo en el entorno del proceso servidor/batch mediante almacenamiento de secretos; nunca en Git, navegador, `NEXT_PUBLIC_*` o logs. No es necesaria una clave propia de TypeSafe para la ruta gestionada de Gateway.
3. Solo al habilitar el fallback, añadir un adaptador batch TypeScript/Bun aislado del dashboard y del build determinista `xray`. La documentación requiere AI SDK 7 y `experimental_evaluate` (soporte anunciado desde `ai` 7.0.105), con `model: 'typesafe-ai/jev'`, `state` y preguntas `choice` con criterios explícitos. La dependencia `ai` no existe actualmente: seleccionar y fijar una versión compatible revisada, preferentemente con al menos siete días de publicación; no instalar `latest` ni exigir este SDK a la ruta HTTP principal. No usar `generateText` ni endpoints compatibles con OpenAI para esta modalidad.
4. Revisar la política de datos de toda la ruta. Vercel documenta ZDR por solicitud mediante `providerOptions.gateway.zeroDataRetention: true` para planes Pro/Enterprise, sin recargo por solicitud; el plan puede tener coste. Si ZDR es requisito, bloquear ejecución cuando no esté disponible, nunca desactivarlo para continuar. ZDR no elimina la transmisión a terceros ni sustituye el permiso del reto.
5. Verificar credenciales, respuesta y consumo con ejemplos inventados; conservar artefactos separados de los marts oficiales. Gateway expone probabilidades en las respuestas y confianza separada en `providerMetadata.typesafe.confidence`; adaptar cada respuesta al contrato local sin asumir equivalencia de scores, confianza o versiones entre rutas. Evaluar aceptación y calibración por ruta con la misma taxonomía congelada; probar también caída de ambas y ausencia de credenciales.

En `classifier.dev`, incluso `fast` admite sustituciones de modelo y fallback interno: registrar y rechazar resultados de modelos no aprobados. No activar `smart` en este piloto: puede sustituir la etiqueta por otra de un LLM manteniendo confianza/scores originales de Jev. El servicio declara no guardar textos localmente y registrar una huella con clave del conjunto de etiquetas, además de metadatos; reenvía textos a proveedores. No introducir datos sensibles tampoco en etiquetas. Si el contrato exige fijar proveedor/modelo o ZDR que el servicio no garantiza, no enviar datos por esta ruta: usar únicamente una alternativa previamente autorizada que cumpla esas condiciones, o abstenerse. Rechazar una respuesta después no deshace la transmisión; la API pública no equivale a inferencia privada.

**Entregable del piloto:** muestra y referencia autorizadas, configuración/versiones, propuestas auditables, métricas y coste observado, comparación de agregados y decisión documentada de adoptar, limitar o descartar. Sin mejora o permisos, mantener clasificación actual y reporting con incertidumbre visible.

Fuentes: [classifier.dev](https://classifier.dev/), [presentación Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [Jev en Gateway](https://vercel.com/ai-gateway/models/jev), [anuncio y versión mínima SDK](https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway), [Evaluation](https://vercel.com/docs/ai-gateway/modalities/evaluation), [precios](https://vercel.com/docs/ai-gateway/pricing), [autenticación](https://vercel.com/docs/ai-gateway/authentication-and-byok), [ZDR](https://vercel.com/docs/ai-gateway/security-and-compliance/zdr).

### 5.2. Jev condicionado: normalización de tipos documentales (P2)

Activar solo si la auditoría encuentra variantes reales de `document_type` que la normalización existente de `xray/clean/facts.py` no resuelve. Jev puede proponer equivalencias usando el tipo original y `concept` como apoyo; un responsable valida la equivalencia y las variantes recurrentes pasan a un diccionario determinista. Conservar original, propuesta y decisión; los tipos desconocidos siguen desconocidos.

`xray/marts/cobro.py` admite `invoice`: el volumen excluido no demuestra errores de etiquetado ni cobertura recuperable. No reclasificar abonos, pedidos o albaranes como facturas por semejanza textual, inferir cliente/proveedor ni resolver signos con Jev. Evaluar precisión por tipo, falsas inclusiones e impacto monetario por moneda con referencia experta independiente; aplicar controles de §5.1 y repetir puertas afectadas antes de cambiar agregados o targets. Sin vocabulario observado, necesidad material y permiso, mantener esta rama pospuesta.

## 6. P3: panel temporal y contrato de features

Una fila representa información disponible en corte `t`, no lo que sabemos hoy sobre aquella fecha. `company_id` y `group_id` sirven para identidad y particiones; no deben memorizarse como predictores. Metadatos de grupos, ERP y productos también pueden ser snapshots finales.

| Familia | Features iniciales candidatas | Condiciones |
|---|---|---|
| Flujos | Inflow, outflow y netflow por bloque; número de movimientos | Moneda y signo verificados; categorías financieras aprobadas |
| Dinámica | Ventanas 1/3/6 meses, tendencia, dispersión y persistencia | Historia utilizable y continua según política; no rellenar ausencia desconocida |
| Productos | Número de productos observados en ventana | No llamarlos activos contractualmente a partir de `created_at` |
| Calidad | Cobertura, historia disponible, porcentaje sin categoría/moneda/resolución | Calcular as-of; no convertirlo directamente en penalización de salud |
| Concentración | Peso de principales contrapartes observadas | Denominador, cobertura y lado económico definidos; no universal |
| Facturas | Cohortes de conversión a 60 días existentes; stock/aging retrospectivo solo diagnóstico | Cohortes maduras y elegibles; pago, tipos y dirección demostrados. Historial contractual/final ERP sigue limitado |
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

### Candidatos implementados, aún no aprobados

`xray/marts/targets.py` ya construye `targets_proxy` versión 1, separado de predictores. **Déficit:** neto operativo negativo en al menos dos de tres meses futuros, con maduración, actividad, ambigüedad material y sensibilidad a outliers tratadas conservadoramente. **Cobro:** cambio en conversión a 60 días entre tres cohortes maduras base y cohortes `m+1..m+3`, ponderado por EUR emitido; `targets_available_at` filtra labels ya maduros. Son proxies versionados seleccionados por implementación, no outcomes oficiales aceptados ni evidencia de default; requieren aprobación y evaluación por grupos/tiempo. Límites: contrato/default y versiones históricas finales de ERP siguen sin probarse.

**Alternativa opcional no implementada, sin prioridad:** para `B(W) = (sum(inflows)-sum(outflows))/(sum(inflows)+sum(outflows))`, evaluar `Δ = B(t+1…t+3)-B(t-2…t)` solo si se aprueba. `Cov(X,Y-X)=Cov(X,Y)-Var(X)` puede sesgar asociación negativa; no prueba reversión universal. Persistencia de nivel `Ŷ=X` y delta cero `Δ̂=0` son mismo baseline. Comparar nivel/delta equivalentes con splits y métricas iguales.

`TARGET.md` fija unidad, fórmula, ventanas, elegibilidad, disponibilidad, maduración/censura, denominador, umbrales, casos límite y métricas. Umbrales en desarrollo/train; no mirar outcomes de holdout final. Final de historia sin desenlace maduro es censurado/desconocido, nunca negativo. Entrenar para copiar score contemporáneo no valida salud.

### Particiones y métricas

F0 del vault «HackSpain - Fuentes X Ray» menciona 60–80 empresas ocultas; split oficial, disjunción por grupo, target, métrica y formato siguen desconocidos. Objetivo interno: generalizar a empresas no vistas mediante validación conservadora grupos-disjuntos + forward-date, con target maduro, purge de ventanas invasoras y gates as-of/anti-leakage. No confundir grupos con empresas ni atribuir al reto esa disjunción. Forecasting de empresas conocidas es diagnóstico secundario, no intercambiable.

- En evaluación de grupos nuevos, `group_id` de train y validación son disjuntos; reportar por separado empresas conocidas a futuro.
- Para cada fila de train, label debe estar observable antes del corte de entrenamiento; purgar ventanas cuyo horizonte invade validación. `GroupKFold` por sí solo no lo hace.
- Guardar índices/versiones/cortes. Usar 4–5 folds solo con suficientes grupos, eventos y ventanas maduras; reducir folds/horizonte si no hay soporte.
- Imputación, escalado, selección, caps, calibración, umbrales y pesos de ensemble se aprenden solo en train/validación interna. Reservar test final sin tuning.
- Regresión: MAE/RMSE en unidades del target, Spearman como diagnóstico de orden. No comparar por MAE score 0–100 contra delta de otra escala.
- Eventos: PR-AUC, precisión, recall y falsas alertas, con soporte y prevalencia; calibración/Brier cuando se entreguen probabilidades. Sin clases suficientes, métrica no estimable.
- Reportar errores por empresa/grupo y también por fila, cobertura y cohortes de historia corta, moneda y faltantes. Intervalos por remuestreo de grupos cuando sea viable; no asumir filas temporales IID.

## 9. P5–P6: baseline, modelo y criterio de precisión

1. **Baselines equivalentes:** para nivel futuro, persistencia, media train-only o nivel autorregresivo; su delta convertido equivale a cero cambio para persistencia. Para evento, prevalencia. Índice de reglas se evalúa por coherencia/trazabilidad, no como predictor de otra magnitud sin puente validado.
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
| Jev: etiquetas temáticas de drivers (P8, pospuesto) | Necesidad demostrada de búsqueda o agrupación que categorías y motivos existentes no cubran | Solo navegación; taxonomía aprobada y evaluación propia bajo §5.1. No modificar puntos, score, suficiencia de evidencia ni atribuir causalidad; no convertir etiquetas en features sin validación aparte |

## 11. P7–P8: explicación, entrega y anticipación

P7 usa contrato interno de app, distinto del contrato oficial del reto. `readme.md`, `services/api/src/{main.rs,finance.rs}` y `fixtures/companies.json` muestran que `ASSESSMENT_FILE` carga al inicio un array JSON y reemplaza fixture; no hay recarga runtime. `Company` requiere `id`, `name`, `group`, `currency`, `assessment_date`, `model_version`, `history_mode`, `data_mode`, `opening_cash_cents`, `buffer_cents`, `health`, `history`, `drivers`, `coverage`, `flows`; cada flow requiere `id`, `label`, `amount_cents`, `settled_cents`, `date`, `known_on`, `kind`, `timing`, `source`. Validación: IDs empresa únicos/no vacíos; IDs flow únicos **por empresa**; moneda solo tres ASCII mayúsculas (no registro ISO real); importes enteros acotados; `settled_cents` entre 0 y `abs(amount_cents)`; `history_mode` `as_known|reconstructed`; `kind`/`timing` enumerados; fechas deserializables. No valida semántica de `source`, forma de `health` ni score oficial. Un importe es céntimo firmado; versión asume dos decimales y una moneda de reporte por empresa.

Preservar provenance, as-of y coverage. Conservar features internas por moneda; consolidar solo salida de empresa si contrato FX lo verifica, o reducir elegibilidad/demo etiquetada. No fabricar stock histórico, `known_on` ni score para poblar campos requeridos. Parquet es interno opcional; evidencia se incorpora conforme contrato soportado, no como archivo de entrada separado. Reconciliación CSV y score oficial siguen upstream; formato oficial es desconocido. Para aceptar P7, el JSON debe pasar `finance::load` y un recorrido de integración en la app que compruebe los campos consumidos por UI, fechas, cobertura y etiquetas de demo; parsear JSON no basta. Estas comprobaciones quedan pendientes: no se afirma ejecución verificada de app.

`direction` y `alert_state` tienen contrato aparte del score. Histéresis de dos meses es candidata, no regla fijada: medir sensibilidad/latencia en desarrollo. Una alerta confirmada en segundo mes se registra entonces, no se retrofecha al primero.

Bonus de anticipación: definir evento independiente, inicio frente a confirmación, primera alerta real, ventana de matching, un match por evento/signo, misses, falsas alarmas, ambas direcciones, censura y distribución de lead time. Un agregado de tres meses no identifica automáticamente fecha exacta de inicio; sin evidencia, no prometer meses de anticipación precisos.

### 11.1. Jev opcional: coherencia semántica de explicaciones (P7)

Primero validar fechas, importes, IDs y cobertura en código y generar textos simples desde hechos estructurados. Si persisten explicaciones libres en `drivers` o `coverage`, evaluar Jev para contrastar cada afirmación con evidencia enlazada y disponible en el corte: `respaldada`, `contradictoria` o `evidencia insuficiente`. Una referencia ausente no permite declarar respaldo; el texto evaluado es dato, no instrucción que pueda cambiar los criterios.

Evaluar con pares afirmación/evidencia etiquetados por humanos, incluidos casos correctos, contradicciones y fuentes insuficientes; evitar compartir plantillas equivalentes entre desarrollo y evaluación. Medir falsos respaldos, contradicciones detectadas, falsas alarmas y abstención frente a controles deterministas. Aplicar controles de §5.1; una salida tipada no certifica veracidad ni causalidad. Jev no debe redactar o reescribir explicaciones, alterar scores ni certificar salud financiera.

Guardar observaciones para revisión humana, fuera del JSON publicado; fallo o abstención significa no evaluado, nunca aprobado. Mantener validación determinista y revisión humana de afirmaciones materiales como alternativa sin Jev. Piloto separado, no dependencia de export ni reemplazo de `finance::load` o pruebas de integración.

## 12. Entregables previstos y aprobación

**Ya existen** pipeline `xray` (ingesta, limpieza, clasificación FX y siete marts), `data_dictionary.md`, `reports/vistas_y_hallazgos.md`, `reports/build_manifest.json`, `scripts/audit_financial_data.py` y `reports/readiness/{manifest.json,inventory.md,schema_contract.json}`. El script/readiness son inventario separado; manifest e informes son evidencia histórica, no reproducción actual. Los siguientes son propuestas o ampliaciones, no controles aprobados:

| Entregable futuro | Contenido |
|---|---|
| Actualización de inventario/manifest | Identidad, hashes tras materialización, esquemas, versiones y disponibilidad |
| Informe G-DATA | Calidad reproducible, cobertura, decisiones semánticas e impacto de exclusiones |
| Ledger o derivados adicionales | Solo si verificación del pipeline existente revela hueco; originales intactos |
| Pilotos Jev opcionales (§§5.1, 5.2 y 11.1) | Movimientos y apoyo a revisión; normalización documental condicionada; coherencia semántica opcional. Por uso: muestra autorizada, propuestas separadas, métricas, coste y decisión independiente de adoptar, limitar o descartar; `classifier.dev` principal y Gateway fallback sujeto a acceso, presupuesto y privacidad |
| Contrato de features as-of | Elegibilidad y metadatos sobre marts existentes |
| Aprobación de `targets_proxy` o `TARGET.md` | Objetivo, maduración, censura y límites explícitos |
| Índices de split y métricas | Cortes/grupos, baseline, cohortes, selección y test final |
| Modelo y JSON `ASSESSMENT_FILE` | Artefacto reproducible, predicción y campos app validados |
| Evidencia y guía de ejecución | Razones, fuentes, versiones, límites y regeneración de demo; no input app separado |

Preguntas prioritarias al mentor: entidad/unidad/target oficiales, métrica/formato/submission, labels y disponibilidad, historia/split del test; semántica/exclusiones de `payment_date`, facturas, tipos y FX; snapshots/historial; licencia. Cambios en semántica, exclusión, target, protocolo o moneda pueden invalidar limpieza, panel y splits: versionar decisiones y repetir puertas afectadas.

**Decisiones humanas pendientes:** semántica/exclusiones financieras materiales; target oficial o proxy; protocolo/split y formato de salida oficial; alcance material G-DATA y claims de demo; autorización de tratamiento externo por ruta, presupuesto, habilitación del fallback Gateway y promoción independiente de cada uso de Jev. La incorporación de estos usos opcionales y el orden `classifier.dev` → Gateway al planning están acordados; no aprueban esas decisiones ni autorizan envíos de datos del reto, gastos o cambios automáticos en los datos. Ningún piloto sustituye controles financieros ni se convierte en dependencia obligatoria de baseline/export.

**Siguiente unidad de trabajo propuesta:** 30–60 min P0: confirmar inputs/contrato e inspeccionar pipeline/marts existentes. Checkpoint: si materialización o semántica bloquea, elegir ruta elegible o fixture etiquetado. En siguientes 60–90 min, producir/validar JSON mínimo de app desde marts existentes solo si elegibles; baseline/export antes de modelo nuevo. No ejecutar modelado ahora ni prometer completar datos; deadline exacto se confirma aparte.
