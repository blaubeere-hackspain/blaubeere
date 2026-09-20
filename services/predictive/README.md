# Modelo predictivo: paquete listo para integrar en serving

Preparación del modelo para [`docs/plans/xray-prediction-model-jio-plan.md`](../../docs/plans/xray-prediction-model-jio-plan.md), tareas T0–T2. **No incluye servidor HTTP, despliegue Jio ni consumidor Rust.** Scoring v4 y sus consumidores no se modifican.

## Construir y comprobar

Desde raíz del repositorio, con **Python 3.13.15**:

```sh
.venv/bin/python -B scripts/build_predictive_bundle.py --output .local/predictive-model
.venv/bin/python -B scripts/build_predictive_bundle.py --output .local/predictive-model --check
.venv/bin/python -B -m unittest tests.test_predictive_bundle tests.test_predictive_runtime -v
```

El constructor imprime `bundle_sha256` y `release_manifest_sha256`. Guardarlos en configuración del despliegue, fuera del payload de una petición. Repetir construcción/check es de solo lectura si salida coincide; una salida diferente se rechaza. Para una versión nueva, usar otro directorio. `--check` también detecta cambios en fuentes del repositorio desde la construcción.

Se comprueba cadena del manifiesto original → sello → seis fuentes científicas y estimadores JSON. No se ejecutan entrenamiento, evaluación de holdout ni runners históricos. Fuentes científicas se copian byte a byte; preparación de serving vive aparte.

La carpeta resultante es portable:

```text
predictive_model/       API Python, adaptador, schemas y CLI
scripts/               seis módulos científicos históricos, sin modificar
bundle/                JSON de estimadores, política, features, exclusiones y manifiesto
contracts/predictive/  petición, respuesta y ejemplo sintético
release-manifest.json  hashes de todos los archivos y procedencia
.python-version        3.13.15
README.md              este handoff
```

Copiar **carpeta completa**, no solo `bundle/`. Runtime usa únicamente biblioteca estándar: no requiere pip, NumPy, scikit-learn, DuckDB, dataset, etiquetas, pickles ni acceso de red. El entorno de desarrollo sí necesita `requirements-model.txt` para pruebas de paridad contra implementación histórica.

## Smoke test en otro directorio

Con carpeta construida como directorio actual y Python fijado:

```sh
python3.13 -B -m predictive_model \
  --bundle ./bundle \
  --bundle-sha256 '<bundle_sha256 del constructor>' \
  --input contracts/predictive/example.json
```

También acepta JSON por stdin. Devuelve JSON en stdout; errores sanitizados en stderr y exit code no cero. No escribe evaluaciones ni cambia pesos. El ejemplo es inventado para comprobar aritmética, no evidencia de eficacia financiera.

## Interfaz para propietario del servidor

```python
from predictive_model.bundle import Bundle
from predictive_model.contract import InputError, decode
from predictive_model.runtime import assess

# Una vez, al arrancar. El hash procede de configuración de release confiable.
bundle = Bundle.load("/release/bundle", expected_sha256=trusted_bundle_sha256)

# Dentro del adaptador autenticado; body son bytes, no un dict ya parseado.
try:
    result = assess(decode(body), bundle)
except InputError as error:
    # Traducir a {"error": error.code}, HTTP error.status. No loguear body.
    raise
```

`observed_at` es un argumento Python reservado a reloj confiable/tests, **no un campo que el cliente pueda enviar**. Producción debe omitirlo. Reutilizar instancia de bundle como solo lectura, no modificar sus diccionarios.

El propietario HTTP añade autenticación, HTTPS, liveness/readiness, carga del bundle y smoke de arranque, timeout, límite de concurrencia, logging sin datos financieros y traducción de errores. Un `ValueError`/`OSError` al cargar bundle impide readiness; un fallo inesperado de evaluación es `500`, nunca score cero. Verificar `release_manifest_sha256` y hashes del release antes de ejecutar código distribuido: el hash de bundle no autentica por sí solo los archivos Python.

## Contrato de snapshot

Ver `contracts/predictive/request.schema.json`, `response.schema.json` y `example.json`. Generadores de schemas:

```sh
python3.13 -B -m predictive_model --schema input
python3.13 -B -m predictive_model --schema output
```

- Una empresa y cierre mensual. `view` por defecto `as_of`; reconstrucción retrospectiva requiere valor explícito. Simulación no admitida.
- Importes nativos como cadenas decimales; moneda y procedencia siempre explícitas. El límite de precisión del núcleo es 38 dígitos y 12 decimales. Features del estimador son números binarios finitos sobre base analítica EUR.
- `records` reutiliza contratos financieros originales. Colecciones omitidas son evidencia ausente, nunca certificación de cero. Colecciones suplementarias se envían explícitamente, aunque estén vacías.
- `classifications` vincula movimiento a regla respaldada por `source_ref`. `cash_movement.classification_evidence` debe coincidir con `classifications.source_ref.record_id`. Una clasificación conocida después del corte se trata como desconocida en ese corte, sin ocultar movimiento bancario. `rule_id` conserva identidad de regla, no una feature calculada.
- Servicio pagado conocido conserva significado original: importe bruto de movimientos de salida de financiación con reglas `cat:debt_repayment`, `transf:debt_repayment`, `cat:interest_charge`, `transf:interest_charge`. No se sustituye por suma de liquidaciones ni se restan reintegros.
- `invoices` son documentos emitidos, no estados finales ni obligaciones duplicadas. `side=ap|ar`, importe nominal no negativo, `issued_on` y `due_on` independientes. `fx_at` identifica timestamp exacto de evidencia FX de transacción; `null` no inventa conversión. EUR mantiene identidad. Un `invoice_id` de obligación debe resolver en esta colección.
- Todo registro y evidencia suplementaria pertenece a empresa solicitada. Referencias de pagos/liquidaciones son bidireccionales. Duplicados idénticos se normalizan; contradictorios se rechazan incluso si su conocimiento es posterior al corte. Correcciones van en un snapshot nuevo, no como duplicados conflictivos.
- Account vintages contradictorios no tienen resolver en núcleo actual: se rechazan. Cambios de términos de obligaciones requieren modificaciones y linaje de sustitución explícitos, no sobrescritura silenciosa.
- Mantener historial para hasta 16 meses de cálculo/comparación, más obligaciones antiguas abiertas, calendarios futuros conocidos y puentes/anclas de conciliación. No recortar todo a 16 meses indiscriminadamente.
- Máximo 16 MiB y 50.000 registros, agregados antes de deduplicar. El servidor debe aplicar límite de bytes también durante recepción del cuerpo.

El schema valida forma; runtime además valida decimales, referencias, disponibilidad temporal y coherencia financiera. No aceptar rutas, URLs, SQL, versiones arbitrarias de política ni pesos dentro de snapshot.

## Resultados y evolución

`assess` calcula corte actual y mes anterior **con sus cortes de conocimiento respectivos**, explica cambio y produce las 18 features originales sin consultar datos locales. `financial` usa salida versionada del núcleo y `change` su explicación; campos internos científicos históricos mantienen nombres por trazabilidad. El adaptador deja `paid_by_kind`/`allocated_paid` afectados en `null` si obligaciones tienen otra moneda: evita un defecto de los agregados de pagos del núcleo congelado, que mezclan importes nativos. No cambia fórmulas ni fuentes históricas; conserva detalle etiquetado por moneda. `predictions` identifica objetivos reales, horizontes y abstenciones. No convertir probabilidades en puntos de salud.

Artefacto inicial conserva dos estimadores de persistencia seleccionados para contracción/expansión de cobros y predictor de bache no disponible. Las probabilidades no están calibradas. Score puede ser `null` por falta de evidencia aunque haya probabilidades disponibles; sus condiciones de elegibilidad son distintas. No se afirma mejora ni validación fuera del dominio estudiado.

Empresas nuevas y cierres nuevos funcionan sin editar servidor. Cortes anteriores/al corte de selección abstienen; 208 empresas y grupos excluidos del benchmark mantienen exclusión predictiva. La versión de serving amplía ventana operativa, **no modifica el experimento histórico ni su validación**.

Mismo snapshot normalizado, corte y bundle produce mismo resultado salvo `request_id` y `evaluated_at`. `input_sha256` no depende del orden de registros ni de duplicados idénticos; incluye identidad/versionado del snapshot y hechos futuros aunque estos no entren en evaluación `as_of`.

Para mejorar modelo: generar artefacto científico nuevo, actualizar compatibilidad y pruebas de paridad, construir otro release y cambiar código/política/pesos juntos. No hot reload ni aprendizaje por petición. Candidatos desconocidos se rechazan; no existe fallback silencioso a otro algoritmo.

## Comprobaciones realizadas por tests

Paridad financiera y delta contra núcleo original; 18 features contra helper histórico; probabilidades exactas en estados y límites; nulls; fechas nuevas; aislamiento por empresa; hechos/clasificación futuros; duplicados y referencias; JSON estricto; integridad, exclusiones y compatibilidad de bundle; fuentes protegidas; construcción reproducible y ejecución aislada con `python -I -S -B` fuera del repositorio, sin paquetes de terceros ni escrituras.

Jio, HTTP y Rust todavía deben comprobarse en T3–T7. El paquete del modelo no certifica rendimiento en VM ni servicio desplegado.
