# X-Ray Embat: contratos vigentes de vistas

Version 4, 19 de septiembre de 2026. Sustituye las cifras y reglas de las versiones anteriores. Los recuentos y distribuciones vigentes se consultan en los JSON de la ejecucion identificada por `reports/current.json`; no se mantienen copias manuales de cifras monetarias en este documento.

## 1. Producto y alcance

Los cuatro controles de la simulacion son cobros operativos, pagos operativos, servicio de deuda y caja inicial. Una intervencion modifica la proyeccion, no reescribe el pasado. Los parametros del futuro scorer deberan estar congelados y el scorer sera una funcion pura.

La identidad de caja conserva tambien nueva financiacion, aportaciones, inversion y otros flujos conciliados. Cada movimiento entra una sola vez. Un predictor de deficit operativo no equivale a una medida completa de salud financiera.

No hay etiquetas oficiales en el dataset. El deficit operativo sostenido y el deterioro del cobro son objetivos proxy que deberan declararse como tales, con disponibilidad y maduracion propias. La tabla de targets debe estar separada de la de predictores.

Implementado: ingesta, limpieza, clasificacion de flujos, registro de evidencia FX, panel de cobro, observabilidad temporal, panel_flujos, panel_deuda, panel_evidencia y targets_proxy. Pendiente: scorer, validacion predictiva por grupos/tiempo e integracion de resultados con la aplicacion. No se han entrenado modelos ni modificado la interfaz en esta fase.

## 2. Politica de moneda

Decision aprobada: **desconocido, no estimar**.

- Se conservan todos los campos originales, incluidos importe, moneda y tipo de cambio.
- Un nominal en EUR se conserva por identidad, independientemente de la moneda contable.
- Entre la misma moneda no se modifica el nominal; un tipo contradictorio queda marcado.
- Un cambio reportado entre monedas diferentes debe ser positivo, finito y distinto de 1. El tipo 1 en ese contexto es ambiguo: no demuestra conversion.
- En transacciones, la moneda del producto es la interpretacion de moneda del hecho usada por el pipeline. Si no se puede demostrar conversion a EUR, `amount_eur` es NULL y se marca `fx_not_convertible`.
- En facturas, el cambio reportado convierte `currency` a `accounting_currency`. Solo se obtiene EUR por identidad o por conversion reportada valida hacia EUR.
- `amount_eur_source` distingue `identity`, `reported` y `unknown`; `fx_ambiguous` expone la ambiguedad de tipo 1.
- No se sustituyen tipos desconocidos por medianas globales, mensuales ni cotizaciones inventadas.

`fx_rates` queda como registro de evidencia mensual: EUR por identidad y tipos extranjeros NULL. No alimenta la conversion del panel. La antigua estimacion de tipos y las cifras de rescate han quedado retiradas.

Las cifras de volumen en los informes usan importes efectivamente convertidos. Los nominales no convertidos solo se suman dentro de su propia moneda. Nunca se etiqueta como EUR una suma de distintas monedas.

## 3. Clasificacion de flujos

Una fila por transaccion, identificada por `_src_row` y `transaction_id`. Conserva clase, regla, fuente, evidencia temporal y posible contradiccion de direccion.

- La naturaleza economica procede de categoria o regla; la direccion procede del signo del importe. No se invierte el signo ni se usa el valor absoluto para transformar una salida en entrada.
- `direction_conflict` conserva la discrepancia entre la direccion sugerida por la categoria/regla y la real.
- La transferencia de categoria exige el mismo patron, empresa y direccion, con una unica categoria observada **en meses anteriores**. No aprende de otras empresas o del futuro.
- Un ingreso en un producto de deuda sin otra evidencia queda desconocido: no demuestra por si mismo una nueva disposicion de efectivo.
- `transfer` no significa necesariamente transferencia interna; no se elimina ni netea sin emparejamiento demostrado.
- El volumen sin clasificar se mide como fraccion del volumen absoluto convertible, no dividiendo netos. Se reportan aparte las filas sin conversion.
- `es_atipico` se calcula sobre EUR conocido; si EUR es desconocido la marca tambien puede ser desconocida. Las cifras antiguas de concentracion de volumen deben recalcularse tras cambiar la conversion.

La transferencia temporal presupone que la categoria reportada estaba disponible en la fecha del movimiento. No hay versiones de categorias para demostrarlo; esta aproximacion debe declararse en cualquier backtest.

## 4. Observabilidad

Grano empresa-mes sobre un calendario continuo definido en `xray/period.py`, no sobre los meses que casualmente aparecen en transacciones.

`objetivo_observable` exige actividad en el mes, ausencia de calentamiento y actividad en los tres meses futuros con horizonte completo. Se usa `lead` sobre el calendario. Los porcentajes de ausencia de conversion y clasificacion son porcentajes de **filas**, en escala 0-100.

Esta vista solo comprueba observabilidad temporal de actividad. No calcula el objetivo ni demuestra que el dinero este suficientemente clasificado o convertido. Esa segunda condicion correspondera a panel_evidencia y al constructor de targets.

No tener movimientos no demuestra inactividad economica: puede significar falta de cobertura. Se excluye de la evaluacion antes que inventar un cero. Esta seleccion tambien debe declararse al evaluar el modelo.

## 5. Panel de cobro

Grano empresa-mes cerrado. Perimetro: `invoice` no cancelada con emision valida. Los documentos restantes no se cuentan como si fueran automaticamente facturas comerciales; tampoco se afirma que este perimetro sea una cartera neta completa.

### Fechas y estado

- El campo original `payment_date` se conserva, pero no demuestra cobro fuera del estado `paid`.
- `settlement_date_ok` exige `paid`, ausencia de importe pendiente y pago no anterior a la emision.
- `maturity_date_ok` excluye vencimientos anteriores a la emision.
- Un pago con fecha corrupta o anterior a la emision no se considera pago a cero dias: queda desconocido. Un anticipo requiere evidencia y tratamiento especificos, no una correccion automatica de fecha.
- Una fecha de vencimiento invalida no elimina la factura del emitido. Impide dar por completo su aging.
- Un estado de liquidacion incierto invalida el total de stock correspondiente; el subtotal conocido se conserva separado.
- Los dias de cobro y retraso requieren suficientes fechas validas. DSO no puede ser negativo.

### Ausencia, cobertura y totales

- Antes del primer mes observado no se rellenan importes con ceros.
- Las empresas sin evidencia de ERP en un mes no se consideran observadas por existir una factura en el futuro.
- La continuidad de observacion tras la primera factura es inferida y queda marcada como tal; no se dispone de telemetria de conexion ERP.
- Los totales monetarios incompletos son NULL. `*_conocido_eur` conserva el subtotal disponible, sin presentarlo como total.
- La cobertura de emision y stock mide fraccion de documentos convertibles, escala 0-1. No divide euros entre nominales locales.
- Si el vencido es conocido y vale cero sobre cartera positiva conocida, su porcentaje es cero, no NULL.
- Las seis bandas de aging reconcilian con el vencido cuando este es observable. Un vencimiento desconocido no se etiqueta como no vencido.
- La normalizacion movil requiere tres meses de emision completos; no imputa ceros anteriores al inicio observado.

### Cohortes y disponibilidad

Los horizontes 30/60/90 usan exactamente el mismo universo monetario. Cuando son observables, los porcentajes deben estar entre 0 y 1 y ser no decrecientes con el horizonte.

`coh_30d_available_at`, `coh_60d_available_at` y `coh_90d_available_at` son fin del mes de emision mas el horizonte. Hay dos controles independientes:

1. Censura al cierre del dataset: una cohorte inmadura es NULL.
2. Consulta a fecha: `cohort_features_at(con, fecha_de_corte)` solo devuelve porcentajes maduros y no nulos, con horizonte y mes de origen explicitos.

Los importes incompletos o estados de pago inciertos tambien impiden publicar el porcentaje de cohorte. No se sustituyen por un cero ni se confunden con censura temporal.

### Limites historicos no recuperables

El stock queda marcado como reconstruccion retrospectiva, no como feature point-in-time certificada. Faltan fechas de importacion, cancelacion y versiones de pagos parciales. Excluir una factura hoy cancelada no demuestra que estuviese cancelada en un corte pasado. Los estados finales pueden incorporar informacion que no estaba disponible entonces.

La API de features de cobro no exporta esos stocks retrospectivos. Las cohortes siguen suponiendo disponibilidad de la factura al emitirse; hay que declarar esa aproximacion porque no puede probarse con este dataset.

El saldo bancario del 1 de septiembre de 2026 solo sirve como snapshot o inicio de una simulacion desde esa fecha, no como liquidez conocida en meses anteriores.

## 6. Nuevas vistas empresa-mes

Las cuatro vistas conservan el calendario completo y la clave `(company_id, month)`, con `group_id` al lado. Sus esquemas, cobertura y motivos de ausencia se publican en `reports/quality/<vista>.json`. Los contratos son versionados y sus parametros quedan en el manifiesto de construccion.

### panel_flujos

Perimetro de caja: transacciones `booked` de productos bancarios `checking`, `saving` y `wallet`, en meses cerrados. Tarjetas, TPV, plataformas, productos de deuda y huerfanos no se suman como si fueran caja adicional: quedan en recuentos y netos conocidos fuera del perimetro. No se afirma que todos sean duplicados; se evita sumarlos sin conciliar su relacion con las cuentas de caja.

Los buckets conservan operacion, financiacion, inversion, transferencias, ajustes y desconocidos por signo. Todos los subtotales monetarios llevan el sufijo `conocido_eur`; reconcilian con el neto convertible observado. Los totales `neto_caja_eur`, `cobros_operativos_eur` y `pagos_operativos_eur` solo aparecen cuando la informacion relevante es completa. Mes sin movimientos observados no se rellena como mes economicamente vacio.

Las cotas operativas son `F - U_out` y `F + U_in`, donde F es el neto operativo identificado y U incluye unknown, transfer y non_economic. Requieren conversion conocida para todos esos apuntes. Son cotas condicionales: cada apunte observado dudoso puede ser operativo o no. No acotan cuentas ausentes ni los movimientos faltantes que pueda esconder un ajuste de conciliacion.

Se calcula la misma sensibilidad sin atipicos operativos conocidos. No se recortan los originales ni se supone que un movimiento grande sea falso. La financiacion entrante tampoco equivale automaticamente a prestamo nuevo: puede incluir reintegros.

### panel_deuda

Servicio observado en el mismo perimetro de caja. Principal e intereses se reconocen por reglas `cat:` o `transf:` de debt_repayment e interest_charge; los reintegros se conservan aparte. El neto puede ser negativo si los reintegros superan los pagos.

El total de servicio queda NULL si falta FX relevante o los pagos ambiguos pueden ocultar servicio de deuda. Los apuntes de productos de deuda aparecen solo como diagnostico separado, nunca sumados al servicio de caja. El desglose concilia con la financiacion de panel_flujos.

No contiene outstanding actual replicado hacia atras, caja historica reconstruida desde el saldo final ni cuotas proyectadas de calendarios sin vigencia demostrada. Un cero de servicio observado no demuestra ausencia de deuda en la empresa.

### panel_evidencia

Publica estados separados para operacion, servicio de deuda y cobro: `publicable`, `parcial` o `insuficiente`, cada uno con motivos. No suprime una dimension observable porque falte otra.

- Operacion y servicio: al menos tres meses transcurridos de historia y tres meses con actividad para publicar trayectoria; conversion/clasificacion relevante completa. Los atipicos que pueden cambiar el signo operativo degradan su evidencia.
- Cobro: solo la ultima cohorte a 60 dias ya madura al cierre del mes, con su fecha, numero de facturas y antiguedad. Menos de cinco facturas o mas de 90 dias desde disponibilidad se consideran evidencia parcial.
- Esos umbrales son reglas operativas de la version 1, no probabilidades de confianza calibradas.
- No incorpora `horizonte_completo` ni otros flags de futuro como predictores. Las filas historicas permanecen iguales al anadir registros futuros.

Publicable significa metrica utilizable dentro del perimetro y supuestos declarados, no un score completo ni cobertura garantizada de toda la empresa.

### targets_proxy

Tabla separada de los predictores, sin etiquetas oficiales ni umbrales aprendidos sobre todo el dataset.

**Deficit:** neto operativo negativo, redondeado a centimos, en al menos dos de los tres meses siguientes. Exige actividad de caja en origen y horizonte, maduracion y ausencia de calentamiento. Se cuentan deficits seguros y posibles bajo las cotas. Dos meses seguramente negativos bastan aunque el tercero sea ambiguo; cuando la incertidumbre puede cambiar la etiqueta, el resultado es NULL. Tambien queda NULL si la etiqueta no es robusta con/sin atipicos. No mide severidad relativa ni salud financiera completa.

**Cobro:** `100 * (conversion_60d_base - conversion_60d_futura)`, ponderado por importe EUR emitido. Positivo significa menor conversion a caja; negativo, mejora. La base son tres cohortes consecutivas que terminan en la ultima madura al origen; el futuro son las cohortes m+1..m+3. Se requieren las tres cohortes completas de cada tramo. No se inventa un umbral binario de deterioro y no se confunde conversion a caja con incumplimiento del plazo pactado.

`available_at_deficit` es el cierre de m+3; `available_at_cobro` anade 60 dias a ese cierre. La consulta `targets_available_at(con, fecha_de_corte)` devuelve solo labels no nulos cuya maduracion ya ocurrio. Estas fechas deben respetarse al separar entrenamiento y validacion, manteniendo ademas grupos completos fuera del ajuste.

## 7. Publicacion y reproducibilidad

`python -B -m xray.cli build` construye en una ejecucion nueva, con copia del codigo, hashes de los CSV, parametros, versiones y dependencia DuckDB fijada. Ejecuta pruebas de contratos reales y regresiones sinteticas antes de publicar.

`reports/current.json` identifica atomicamente una ejecucion validada. Los consumidores usan `xray.paths` para fijarla. Las copias de compatibilidad en las rutas historicas se publican por archivo; no son una transaccion de lectura conjunta. Los artefactos anteriores se conservan en `before/` y una publicacion fallida revierte sus copias de compatibilidad.

El repo versiona ahora el codigo, las pruebas, los datos finales y los informes. `reports/build_manifest.json` conserva la procedencia portable de los artefactos publicados: hashes de entradas, salidas y codigo, parametros, dependencias y verificacion. Las ejecuciones locales, sus backups y `reports/current.json` no se versionan. Un clon nuevo usa las rutas canonicas hasta su primera reconstruccion; esta genera los intermedios y crea su propio puntero local. Cada ejecucion local conserva ademas su copia `source/` y sus hashes.

## 8. Verificacion y decisiones pendientes

Las pruebas comprueban conservacion de originales, conversion por identidad, FX ambiguo, direccion de flujos, independencia de evidencia futura, calendario con huecos, coherencia de cohortes, cobertura dimensional, ausencia de falsos ceros, aging, disponibilidad y recuperacion ante fallo de publicacion.

Quedan decisiones de dominio que el dataset no permite inventar:

- Unidad, metrica y formato oficial de entrega del reto.
- Tratamiento de abonos y documentos diferentes de invoice.
- Emparejamiento de transferencias internas y doble apunte banco/deuda.
- Vigencia de los calendarios de deuda y proyeccion de cuotas.
- Validacion economica de categorias bancarias y de asignaciones LLM antes de incorporarlas.
- Interpretacion de saldos extremos: el umbral de centinela es conservador, no demuestra que un saldo grande sea falso. El valor original sigue conservado.

No se aplican predicciones LLM, no se inventa historial ausente y no se han construido ni entrenado nuevos modelos como parte de estas correcciones.
