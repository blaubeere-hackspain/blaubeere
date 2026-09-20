# Modelo predictivo: servicio en Jio preparado para Rust

## Objetivo y alcance

Convertir implementación actual del **modelo predictivo** en un servicio de inferencia bajo demanda, desplegable en **VM Jio dedicada**, que acepte datos nuevos enviados por Rust. Poder mejorar después modelo y scoring sin rehacer integración.

Decisiones confirmadas:
- Rust conserva datos, historial, permisos y evaluaciones guardadas.
- Servicio Python recibe un snapshot financiero y calcula; **no mantiene otra base financiera**.
- Scoring v4 es un **motor analítico separado**. Se conserva sin cambios: código, resultados, consumidores y despliegue actuales.
- Este cierre entrega servicio y contrato probado desde Rust; no migra todavía endpoints, dashboard ni consumidor productivo.
- No incluye reentrenamiento, recalibración ni cambios de fórmula. Preparación técnica y calidad predictiva tienen criterios de aceptación separados.

## 1. Arquitectura

```text
Rust: autorización + snapshot financiero
                   │ HTTPS autenticado
                   ▼
VM Jio dedicada
  API Python → validación → preparación de datos
             → cálculo financiero + inferencia
             → explicación + cobertura + versiones
                   │
                   ▼
Rust: persistencia de evaluación y entrega al producto
```

Servicio Python con FastAPI/Uvicorn, proceso systemd no privilegiado y proxy HTTP protegido por HTTPS de Jio. Un bundle inmutable cargado al arrancar; sin descarga de modelos, entrenamiento ni lectura del dataset del repositorio durante peticiones.

Reutilizar núcleo y contratos de `scripts/financial_v3_engine.py`, `scripts/financial_v3_contract.py` y funciones de preparación/inferencia existentes. El nombre público será **modelo predictivo**; identificadores históricos permanecen únicamente donde sean necesarios para trazabilidad.

Los experimentos contienen rutas locales, ventanas fijas y verificaciones de todo el workspace. No se usarán sus runners como servidor. Añadir adaptación de serving separada, preservando fuentes y artefactos históricos sellados.

## 2. Contrato de entrada y salida

### API

- `POST /v1/assessments`: una empresa y un cierre mensual por petición.
- `GET /health/live`: proceso vivo, sin datos sensibles.
- `GET /health/ready`: bundle íntegro y evaluación de arranque superada.
- `GET /v1/model`: versiones, capacidades, esquema y límites; autenticado.
- OpenAPI y JSON Schema publicados como artefactos versionados. Sin Swagger público en despliegue.

### Petición

Sobre común: `schema_version`, `request_id`, `company_id`, `group_id`, `snapshot_id`, `as_of`, `view`, `reporting_currency` y colecciones de registros.

Reutilizar contratos existentes para cuentas, movimientos, coberturas, saldos fechados, FX, obligaciones, liquidaciones, modificaciones y evidencia de completitud. Incluir evidencia suplementaria que ya exige el núcleo financiero. Para features documentales, añadir colección explícita de facturas emitidas con identidad, AP/AR, emisión, vencimiento, importe, moneda y procedencia; no confundir emisión con fecha efectiva de una obligación.

Reglas:
- Importes como cadenas decimales con moneda; ratios y probabilidades como números finitos.
- Fechas de efecto, conocimiento y extracción separadas. Ausencias explícitas, nunca cero por defecto.
- Todos los registros deben pertenecer a empresa solicitada; referencias y asignaciones deben resolver sin ambigüedad.
- Duplicados idénticos son idempotentes; duplicados contradictorios se rechazan.
- Rust envía hechos y clasificación respaldada cuando exista, no features calculadas. Clasificación desconocida permanece desconocida.
- Historial suficiente para ventanas actuales y comparación anterior; conservar además obligaciones antiguas todavía abiertas, anclas y movimientos necesarios para conciliarlas y calendarios futuros conocidos. No imponer una ventana que borre deuda antigua.
- No aceptar rutas, SQL, URLs de descarga, pesos ni reglas de scoring suministrados por petición.

### Temporalidad

`view=as_of` por defecto: solo evidencia conocida al corte. `view=reconstructed_retrospective` debe solicitarse explícitamente; nunca se convierte automáticamente en evaluación histórica conocida entonces. No admitir simulación de disponibilidad en esta entrega.

Aceptar empresas nuevas y fechas de cierre nuevas mediante política de serving versionada, sin modificar protocolos históricos. Fechas anteriores al corte de ajuste/selección mantienen abstención predictiva. Conservar exclusiones identificadas del benchmark original. Nuevos datos no heredan una afirmación de validación fuera del dominio estudiado.

### Respuesta

Sobre versionado con:
- Identidad de empresa, snapshot, corte, vista y huella canónica de inputs.
- `schema_version`, `model_version`, `feature_version`, `policy_version`, `bundle_sha256` y `evaluated_at`.
- `financial`: score disponible, dimensiones, cantidades, cobertura y razones.
- `change`: diferencia frente al mes anterior calculada bajo la misma versión; explicación y comparabilidad.
- `predictions`: target exacto, horizonte, probabilidades, calibración declarada y razones de abstención.
- `explanations`, `warnings` y procedencia necesaria para auditar resultados.

No convertir probabilidades en puntos de salud. No presentar proxies de cobros como predicción de salud integral. Mantener salidas no disponibles del artefacto actual —incluido predictor sin entrenamiento suficiente— como `null` con motivo. Una mejora posterior puede completar estas salidas sin cambiar estructura del contrato.

Separar `request_id` y `evaluated_at` del resultado determinista: mismo snapshot, corte y bundle produce mismo contenido financiero y predictivo.

## 3. Preparación de datos, inferencia y bundle

- Adaptador en memoria: registros normalizados → evidencia mensual → núcleo financiero y features del estimador. Sin ejecutar pipeline completo ni escribir Parquet por petición.
- Mantener significado y orden de las 18 features del artefacto actual: valores actuales/medias de tres meses de cobros, pagos, cotas y cobertura; documentos AP/AR; problemas de vencimiento/FX; servicio pagado.
- Preservar perímetro, unidades, denominadores de cobertura y tratamiento de desconocidos. Una feature imposible de reconstruir no se sustituye por otra parecida.
- Pruebas de paridad independientes para cálculo financiero, agregación de inputs e inferencia. Los resultados retrospectivos no se usarán como oráculo de conocimiento histórico estricto.
- Bundle mínimo: estimadores JSON seleccionados, política, contrato de features, compatibilidad de esquemas y manifiesto con hashes y procedencia. Sin pickles externos, etiquetas, dataset completo ni historial de experimentación.
- Validar bundle al arrancar: hashes, tipos, targets, orden de features y compatibilidad. Fallar antes de estar ready si no es válido.
- Runtime fijado y dependencias bloqueadas; conservar primera versión científica sin modificar parámetros. Las dependencias necesarias para importar código reutilizado se incluyen explícitamente, no mediante acceso al entorno de entrenamiento.

## 4. Operación y seguridad

- HTTPS entre Rust y Jio; token Bearer de servicio, secreto externo al repositorio. Admitir clave vigente y anterior durante rotación.
- Rust verifica permisos del usuario antes de enviar datos. El servicio autentica backend y rechaza mezclas de empresas; la clave de servicio no sustituye autorización de usuario.
- Sin persistencia de payloads ni logs financieros. Logs: identificador de petición, versión, duración, estado y códigos de error; nunca token, cuentas, facturas ni importes.
- Defaults iniciales: una empresa/corte, máximo 16 MiB y 50.000 registros por petición; un cálculo simultáneo por instancia, sin cola persistente. Exceso de carga devuelve `503` con `Retry-After`, no trabajo ilimitado.
- Errores estables: `400` JSON inválido, `401` autenticación, `413` límites, `422` contrato/referencias/fecha inválidos, `503` no disponible o ocupado, `500` error interno sin detalles sensibles.
- Evidencia insuficiente es respuesta `200` con resultados parciales/abstenciones, no avería del servicio.
- Contrato Rust: timeout total inicial de 20 segundos y como máximo un reintento ante fallo transitorio; nunca ante errores de contrato. Peticiones no escriben datos, por lo que reintentarlas no duplica evaluaciones dentro del servicio.

## 5. Tareas paralelizables

| ID | Tarea / propietario | Depende de | Entregable y criterio de cierre |
|---|---|---|---|
| **T0** | **Fijar contrato y baseline** — responsable de integración | — | `contracts/predictive/`: esquemas completos, errores, fixtures y contratos internos entre preparación, cálculo e inferencia. Identificar bundle actual y capturar resultados de referencia sin reentrenar. Registrar hashes; separar archivos de serving de activos históricos protegidos. |
| **T1** | **Adaptar datos y cálculo financiero** — datos/modelo | T0 | Adaptador puro en `services/predictive/runtime/`: registros → evidencia y features; score, comparación y explicación. Reutiliza reglas existentes. Pruebas de temporalidad, monedas, deuda antigua, datos faltantes y paridad. |
| **T2** | **Empaquetar estimadores e inferencia** — modelo | T0 | Creador/verificador de bundle y ejecutor de inferencia, separados de entrenamiento. JSON actual reproducido, capacidades y abstenciones conservadas. Puede trabajar con features de fixtures sin esperar T1. |
| **T3** | **Construir API segura** — servicio | T0 | Endpoints, validación, autenticación, límites, readiness y errores. Trabaja contra evaluador simulado hasta integración; no implementa fórmulas. |
| **T4** | **Preparar despliegue Jio aislado** — infraestructura | T0 | Scripts y workflow nuevos, pruebas con herramientas simuladas, systemd, secretos, staging de release y rollback. No modifica despliegue de app ni scoring v4. Usa servicio mínimo de prueba hasta T6. |
| **T5** | **Probar contrato desde Rust** — integración | T0 | Cliente de referencia y test ejecutable aislado: serialización, decimales, nulls, variantes, errores, autenticación y timeouts. No conecta endpoints productivos ni modifica dashboard. Usa fixtures/servidor simulado hasta T6. |
| **T6** | **Integrar recorrido real** — responsable de integración | T1, T2, T3 | Sustituir evaluador simulado; ejecutar datos normalizados → features → score/predicción → HTTP. Paridad y pruebas negativas pasan con bundle real. |
| **T7** | **Verificar entrega en Jio y handoff** — infraestructura + integración | T4, T5, T6 | Despliegue en VM dedicada, cliente Rust real contra HTTPS, pruebas de carga acotada, reinicio, rotación y rollback. Entregar endpoint, versiones, guía y evidencias. |

```text
T0 ─┬─ T1 ─┐
    ├─ T2 ─┼─ T6 ─┐
    ├─ T3 ─┘      │
    ├─ T4 ────────┼─ T7
    └─ T5 ────────┘
```

**Cinco frentes paralelos después de T0.** Cada uno tiene archivos propios; contratos compartidos solo los cambia responsable de integración. T1 produce evidencia/features; T2 consume ese contrato; T3 desconoce algoritmos; T4 desconoce contenido financiero; T5 prueba interfaz pública.

Las mejoras futuras del modelo discurren aparte: nuevos bundles, mismas interfaces compatibles. No se promueven automáticamente al terminar entrenamiento.

## 6. Despliegue y actualizaciones

- Reutilizar patrón Jio existente: cuenta verificada, VM explícita, endpoint HTTPS, releases inmutables y systemd.
- Variables independientes `MODEL_JIO_VM_ID`, credenciales SSH correspondientes y secreto del servicio. Rechazar VM coincidente con despliegue de aplicación.
- Directorio y usuario exclusivos del modelo predictivo. Primera VM con 2 vCPU/4 GiB como default; medir antes de aumentar recursos.
- Workflow manual de despliegue, no asociado inicialmente a cada push de `main`. No crear ni destruir VMs automáticamente.
- Construir y verificar release antes de activar; cargar bundle, ejecutar caso conocido y comprobar readiness y endpoint autenticado.
- Activación conjunta de código, dependencias y bundle. Si falla, restaurar release completo anterior y verificarlo; nunca rollback parcial de pesos.
- Promover nuevos modelos mediante nuevo bundle y verificación de compatibilidad, sin hot reload durante peticiones.

## 7. Pruebas y aceptación

1. **Paridad:** probabilidades actuales con error absoluto máximo `1e-12`; identidad de clases, features, motivos y elegibilidad. Cálculo financiero coincide con funciones actuales para inputs equivalentes; importes preservan precisión decimal.
2. **Inputs nuevos:** empresa no presente en export original, nuevo cierre mensual y snapshot corregido producen evaluación sin editar archivos del servidor. Snapshot anterior sigue siendo reproducible.
3. **Temporalidad:** hechos aprendidos después del corte no alteran `as_of`; mes abierto rechazado; reconstrucción etiquetada; ausencia de historia produce abstención. Cambios de perímetro invalidan comparaciones cuando corresponda.
4. **Integridad:** monedas no se suman sin conversión válida; no duplicar liquidaciones; conservar deuda antigua; no inventar vencimientos, saldos ni evidencia de completitud.
5. **Seguridad:** token ausente/incorrecto, JSON con claves duplicadas o no finitos, payload excesivo, referencias cruzadas y registros de otra empresa rechazados. Sin secretos ni payloads en logs.
6. **Runtime:** bundle corrupto/incompatible impide readiness; cero entrenamiento y cero acceso a etiquetas/raw dataset durante inferencia; respuestas de error estables.
7. **Rust/Jio:** cliente de referencia consume respuesta real por HTTPS; timeout y servicio caído no se convierten en score cero. Reinicio y rollback recuperan versión conocida.
8. **Rendimiento:** objetivo inicial p95 ≤10 s con una petición activa y fixture representativo de 5.000 registros; medir memoria y rechazo de exceso de concurrencia en VM objetivo. Si falla, optimizar antes de declarar cierre, no ocultarlo con más timeout.
9. **No regresión:** scoring v4 y recorrido existente permanecen sin cambios. Verificadores históricos se mantienen históricos; tests nuevos no reescriben sellos para aparentar compatibilidad.

## Defaults y límites explícitos

- “Listo para Rust” significa contrato y cliente de referencia verificados; integración productiva del backend sigue fuera del cierre acordado.
- Sin base duplicada, colas, streaming, caché distribuida, registro general de modelos ni reentrenamiento online.
- Cambiar datos recalcula; cambiar modelo requiere release. Versionado de API independiente del versionado científico.
- Credenciales y VM dedicada deben estar provisionadas antes de T7; no hacen falta para ejecutar T0–T6.
- No se declara mejora de scoring ni nueva validación científica por desplegar el servicio. Se conserva capacidad y limitaciones del artefacto inicial, preparado para evolucionar.
