# Calendario de cobros (dinero que va a entrar) a 30/60/90 dias

Generado por `xray/collection_schedule.py` (version `collection_schedule_v1`). Reproducible con `PYTHONPATH=. python -B -m xray.collection_schedule`.

- flow_side='inflow', issuance_date_ok <= corte, NOT (status_norm='paid' AND payment_date_ok <= corte) y status_norm<>'cancel'. payment_date_ok solo es fecha REAL si status_norm='paid'; en otro estado (overdue) es fecha PREVISTA (payment_date_ok = due_date_ok) y se ignora (criterio status-aware).
- Probabilidad por tramo = P(cobro) estimada point-in-time. Para el tramo 'no_vencida' es la tasa de cura INCONDICIONAL P(pagada dentro de 180 dias desde el vencimiento) sobre cohortes ya vencidas y observables (due_date_ok <= corte - 180). Para los tramos vencidos es la tasa de cura RESIDUAL condicionada en seguir impagada al entrar en el tramo a = borde_inferior: P(pagada en (a, a+180] | impagada en a). Solo usa pagos con fecha <= corte. Un tramo con menos de 100 observaciones cae a la tasa global.
- El horizonte acumula el intervalo ABIERTO por la izquierda y CERRADO por la derecha (corte, corte+h]: el dia del corte queda excluido y el dia corte+h incluido. Mismo convenio que xray/cashflow_forecast.py.
- Fuente: `data/clean/invoices.parquet`, `flow_side='inflow'`. El lado de PAGOS es otra tarea; aqui NO se combinan.

## 0. Materia prima: reconciliacion del censo

El censo INICIAL del dispatch (124,278 vivas / 702 empresas / 1,477.7 M EUR al 2026-08-31) se midio por ERROR sin el filtro `status_norm<>'cancel'` de la regla 1. El coordinador lo confirmo y fijo como censo de referencia **120,003 vivas / 701 empresas / 1,436.6 M EUR**, que esta capa reproduce EXACTAMENTE aplicando la regla 1 literal. Por trazabilidad se publican ambas mediciones:

| variante | vivas | empresas | importe vivo |
|---|---:|---:|---:|
| **corte vivo + excluir cancel (regla 1; referencia)** | 120,003 | 701 | 1,436.6 M EUR |
| corte vivo + incluir cancel (censo inicial, erroneo) | 124,278 | 702 | 1,477.7 M EUR |

La discrepancia (4,275 facturas, 41.0 M EUR, 152 empresas) es exactamente el filtro `cancel`: una factura cancelada no es dinero que vaya a llegar. La constante auditable `EXCLUIR_CANCEL` controla la eleccion y el CLI expone `--incluir-cancel` para reproducir el censo inicial.

## 1. Corte vivo 2026-08-31 (el forecast que se entrega)

| metrica | valor |
|---|---:|
| facturas vivas | 120,003 |
| empresas con cartera viva | 701 |
| importe vivo | 1,436.6 M EUR |
| vivas no vencidas | 17,953 |
| vivas vencidas | 102,045 |
| empresas con calendario | 654 |
| facturas situadas en el calendario | 103,026 |
| no valorables | 15,485 |
| importe no valorable | 6.459 M EUR |
| importe esperado total (cualquier fecha) | 345.9 M EUR |
| de el, mas alla de corte+90 | 24.8 M EUR |

### Importe esperado por horizonte (corte vivo)

| h | importe esperado en (corte, corte+h] | facturas en ventana |
|---:|---:|---:|
| 30 | 263.6 M EUR | 97,495 |
| 60 | 309.7 M EUR | 101,442 |
| 90 | 321.1 M EUR | 103,026 |

## 2. Tabla de probabilidad de cobro por antiguedad (corte vivo)

| tramo | n facturas | importe vivo | prob. cobro | importe esperado | n estimacion | fallback |
|---|---:|---:|---:|---:|---:|:---:|
| no_vencida | 17,953 | 256.5 M EUR | 0.7134 | 183.0 M EUR | 198,129 | no |
| 0_30 | 13,488 | 139.8 M EUR | 0.4836 | 67.6 M EUR | 109,950 | no |
| 31_60 | 9,926 | 111.5 M EUR | 0.2442 | 27.2 M EUR | 68,654 | no |
| 61_90 | 8,532 | 87.2 M EUR | 0.1505 | 13.1 M EUR | 55,637 | no |
| 91_180 | 19,826 | 175.9 M EUR | 0.1076 | 18.9 M EUR | 48,989 | no |
| 180_mas | 50,273 | 659.3 M EUR | 0.0546 | 36.0 M EUR | 35,392 | no |

## 3. Retraso de pago observado (mediana de payment - due)

- Mediana global (pagos <= corte): 0.0 dias.
- Dispersion entre empresas (medianas por empresa): p25=0.0, p50=0.0, p75=5.0.
- Empresas con retraso propio (>= 10 pagadas): 630.
- Empresas que caen a la mediana global por muestra corta: 71.

## 4. Motivos de no valorable (corte vivo)

| motivo | n | importe |
|---|---:|---:|
| sin_fecha_vencimiento | 5 | 6.459 M EUR |
| sin_importe_eur | 15,484 | 0.000 M EUR |

## 5. Cobertura por corte

| corte | vivas | empresas | importe vivo | no vencidas | muestra corta | esperado h=90 |
|---|---:|---:|---:|---:|---:|---:|
| 2024-11-30 | 9,610 | 339 | 89.8 M | 4,149 | 131 | 16.5 M |
| 2024-12-31 | 13,296 | 386 | 131.2 M | 4,846 | 143 | 30.3 M |
| 2025-01-31 | 17,000 | 427 | 152.8 M | 5,375 | 143 | 5.8 M |
| 2025-02-28 | 20,868 | 442 | 174.1 M | 5,812 | 137 | 39.1 M |
| 2025-03-31 | 24,070 | 451 | 233.4 M | 5,089 | 133 | 61.2 M |
| 2025-04-30 | 27,154 | 474 | 322.0 M | 5,971 | 131 | 93.3 M |
| 2025-05-31 | 31,222 | 494 | 416.5 M | 7,169 | 124 | 149.0 M |
| 2025-06-30 | 35,755 | 515 | 497.4 M | 8,086 | 123 | 171.0 M |
| 2025-07-31 | 40,613 | 535 | 543.2 M | 8,462 | 125 | 176.9 M |
| 2025-08-31 | 43,603 | 551 | 559.7 M | 7,020 | 115 | 142.6 M |
| 2025-09-30 | 46,673 | 558 | 615.0 M | 6,982 | 119 | 153.3 M |
| 2025-10-31 | 49,912 | 579 | 623.0 M | 7,983 | 115 | 129.9 M |
| 2025-11-30 | 52,851 | 590 | 673.4 M | 7,645 | 120 | 144.2 M |
| 2025-12-31 | 58,790 | 607 | 873.6 M | 10,110 | 137 | 239.6 M |
| 2026-01-31 | 62,222 | 637 | 867.2 M | 9,357 | 121 | 188.5 M |
| 2026-02-28 | 67,726 | 646 | 907.3 M | 11,881 | 113 | 182.9 M |
| 2026-03-31 | 73,345 | 666 | 976.4 M | 13,073 | 109 | 203.3 M |
| 2026-04-30 | 80,960 | 685 | 1,067.3 M | 14,905 | 98 | 256.0 M |
| 2026-05-31 | 91,277 | 687 | 1,129.5 M | 15,749 | 84 | 266.4 M |
| 2026-06-30 | 104,340 | 696 | 1,262.2 M | 19,846 | 77 | 312.8 M |
| 2026-07-31 | 113,539 | 700 | 1,374.1 M | 20,886 | 77 | 339.5 M |
| 2026-08-31 | 120,003 | 701 | 1,436.6 M | 17,953 | 71 | 321.1 M |

## 6. Tests

`tests/test_collection_schedule.py` pasa entero (12 tests) e incluye obligatoriamente (a) el test de NO-FUGA (eliminar facturas emitidas despues del corte y llevar muy lejos los pagos posteriores no cambia ninguna cifra del corte) y (b) el test status-aware (una factura `overdue` con `payment_date_ok = due_date_ok <= corte` sigue VIVA). Linea base de la suite completa medida en este worktree ANTES de tocar nada: 58 failed / 48 errors / 954 passed. DESPUES: 58 failed / 48 errors / 966 passed (+12, los nuevos). Cero fallos NUEVOS respecto a la linea base.

## 7. Decisiones de metodo tomadas por el agente (documentadas)

El spec delega explicitamente los tramos de antiguedad y su estimacion ('Define tu los tramos y justificalos'). Estas son las decisiones y la alternativa descartada:

1. **Probabilidad = tasa de cura RESIDUAL, no 'pagada hasta el corte' a secas.** La fraccion de facturas de un tramo ya pagadas al corte es CRECIENTE con la antiguedad (censura a la derecha: las jovenes aun no han tenido tiempo de pagar), lo que daria el incentivo perverso de tratar la mora vieja como MAS cobrable. Se estima P(pagada en (a, a+180] | impagada en a), que decrece con la antiguedad. Alternativa descartada: usar directamente `status_norm='paid'` terminal como desenlace, que es FUGA (usa pagos posteriores al corte).
2. **Tramos 0-30 / 31-60 / 61-90 / 91-180 / >180 mas 'no_vencida'.** Bordes 30/60/90/180 alineados con `payment_delay_v2` (legibilidad conjunta) y con los horizontes del producto; 180 es donde la curva de cura medida se aplana. La ventana de cura se fija en 180 dias.
3. **Probabilidad evaluada en el borde INFERIOR del tramo** (condicionada a seguir impagada al entrar). Es una cota superior dentro del tramo; usar el borde superior seria mas conservador pero penalizaria a facturas que acaban de entrar en el tramo. Se declara la direccion del sesgo.
4. **Retraso con aritmetica directa de fechas** (`payment - due`) y no con `days_to_payment - days_to_due`: esas columnas traen nulos (4,253 y 8,242 de 238,970 pagadas antes del corte) que descartarian observaciones en silencio.
5. **Gating point-in-time por marca de tiempo real** (`issuance_date_ok <= corte`, `payment_date_ok <= corte`, comparacion timestamp contra medianoche), que reproduce el censo de referencia; la programacion (fecha esperada, tramos) usa el DIA del calendario.
6. **'paid' con fecha de pago nula no cuenta como viva**: no puede demostrarse que el cobro siga pendiente y la opcion conservadora es no inflar la caja. Es la semantica de tres valores de SQL.
7. **El calendario se limita a (corte, corte+90]** porque mas alla no hay horizonte de producto; el importe esperado total y el que queda fuera se publican en el censo.

## 8. Lectura honesta y hallazgos incomodos

- **El censo inicial del dispatch incluia `cancel` por error.** El coordinador lo confirmo y la correccion queda trazada aqui: el censo de referencia es 120,003 / 701 / 1,436.6 M EUR (regla 1 literal).
- **El mejor predictor de la fecha de pago es 'paga el mismo dia del vencimiento'**: la mediana global del retraso es 0 dias. La dispersion entre empresas es estrecha (p25=0, p50=0, p75=5).
- **La mora vieja NO es dinero probable.** El tramo >180 dias concentra 659.3 M EUR (46% del importe vivo) pero solo se espera cobrar 5.46% de el. Ignorar esta probabilidad sobrestimaria la caja futura en decenas de millones.
- **El horizonte a 30 dias concentra la mora.** Toda factura vencida cuyo pago esperado ya paso se colapsa a corte+1 (regla 3), de modo que el calendario del dia siguiente al corte es un pico artificial. El agregado a 30 dias es util; el dia concreto no debe leerse como estacionalidad.
- **La probabilidad es una cota superior dentro del tramo**: se estima condicionando en la entrada al tramo (borde inferior); una factura mas profunda dentro del tramo cobrara menos. Ademas la tasa de un tramo joven esta censurada a la derecha, por lo que es una cota INFERIOR de la cura eventual.
- **`amount_eur` es cota superior del saldo pendiente** (nominal emitido). No existe `pending_amount` PIT-safe.
- **`status_norm='cancel'` es terminal y la fuente no tiene fecha de cancelacion**: excluirlas asume que ya estaban canceladas en el corte. Es el precio de no tener fecha de cancelacion.
- Los cortes tempranos (2024-11-30 .. 2025-02-28) tienen poca historia sazonada y su tabla de probabilidad es inestable; el conjunto de datos de origen es corto (arranca 2024-09). El corte vivo 2026-08-31 si tiene cohortes suficientes.

