# Calendario de PAGOS esperados (lado outflow, 30/60/90 dias)

Generado por `xray/payment_schedule.py` (`payment_schedule_v1`). Reproducible con:

```sh
PYTHONPATH=. .venv/bin/python -B -m xray.payment_schedule --output-dir reports/payment_schedule
```

Fuente: `data/clean/invoices.parquet`, `flow_side='outflow'` (SIN filtro de `document_type_norm`). Un worktree limpio no trae `data/interim/`; este modulo NO la necesita.

## 1. Convenio de signo (dependencia de la union)

amount_eur es NEGATIVO para flow_side='outflow'. importe_esperado_eur e importe_no_valorable_eur se publican con ese mismo signo (salida de caja). La tarea de union NO debe re-signar: el lado de cobros publica el signo opuesto (positivo).

## 2. Reproduccion del censo del coordinador al corte 2026-08-31

| metrica | coordinador | medido | coincide |
|---|---:|---:|:---:|
| facturas vivas | 122,086 | 122,086 | si |
| empresas | 762 | 762 | si |
| amount_eur (M EUR) | -1,427.9 | -1,427.9 | si |
| no vencidas | 25,437 | 25,437 | si |
| ya vencidas | 96,547 | 96,547 | si |

Ademas hay 102 facturas vivas con `due_date_ok` nulo (cuentan en el censo, NO se pueden fechar: `sin_fecha_vencimiento`).

## 3. Forecast del corte vivo 2026-08-31

- Empresas con calendario: **737**.

| horizonte | importe esperado (M EUR) | facturas valoradas |
|---:|---:|---:|
| 30 | -356.3 | 101,348 |
| 60 | -417.8 | 106,944 |
| 90 | -439.9 | 108,374 |

- Importe no valorable: **-194.8 M EUR** (importe conocido de facturas sin fecha de vencimiento). 12,457 facturas con importe desconocido (`sin_importe`: 12,450 con `amount_eur` nulo + 7 con `fx_ambiguous`, nunca contadas como cero) y 102 sin fecha de vencimiento.

Los importes con signo negativo son SALIDAS de caja (ver seccion 1). El calendario publica todas las fechas esperadas; los horizontes acumulan solo `(corte, corte+h]`.

## 4. Probabilidad de pago por tramo de antiguedad (corte 2026-08-31)

Curva de supervivencia P(pago <= madurez | seguia impaga a la edad del borde inferior), madurez = 365 dias, estimada SOLO con pagos <= corte. Facturas maduras: 153,822.

| tramo | p(pago) | n en riesgo | n pagadas | motivo |
|---|---:|---:|---:|---|
| no_vencida | 0.8043 | 153,822 | 123,719 | - |
| 1_30 | 0.6077 | 76,725 | 46,622 | - |
| 31_90 | 0.3352 | 45,283 | 15,180 | - |
| 91_180 | 0.1541 | 35,589 | 5,486 | - |
| 180_mas | 0.0662 | 32,237 | 2,134 | - |

### Lectura de la asimetria (lado de pagos)

En el lado de cobros la probabilidad por antiguedad modela 'puede que este dinero no llegue nunca'. En PAGOS el sentido economico es distinto: una factura de proveedor muy vencida sigue siendo una obligacion que probablemente habra que atender. Se aplica el MISMO estimador (fraccion historicamente pagada por tramo) y NO se supone que la tabla deba parecerse a la de cobros. La tabla sale marcadamente decreciente: los tramos viejos son una COTA INFERIOR de la obligacion economica, porque una factura puede quedar registrada como impagada sin generar nunca un pago observado (condonacion, compensacion, pago fuera de sistema). Es informacion, no un fallo a maquillar.

## 5. Retraso observado

- Mediana global del lado (pago - vencimiento, dias): **0.0**.
- Empresas con algun pago antes del corte: 730; pagos usados: 396,677.
- Empresas vivas que usan el retraso global (sin pagos o con < 10 pagos antes del corte): **93** de 762.
- Dispersion entre empresas (mediana por empresa, empresas con >= 10 pagos; n=681): p10=+0.0 d, p25=+0.0 d, p50=+0.0 d, p75=+2.0 d, p90=+10.0 d.

## 6. Metodo y disciplina point-in-time

- **Factura viva**: issuance_date_ok <= C AND NOT (status_norm='paid' AND payment_date_ok <= C) AND status_norm <> 'cancel'.
- **Importe**: `amount_eur` (nunca `pending_amount`). amount_eur nulo/no finito/fx_ambiguous -> motivo 'sin_importe' (importe desconocido, nunca cero); due_date_ok nulo -> motivo 'sin_fecha_vencimiento' (importe conocido pero no fechable). Ninguno se imputa como cero.
- **Fecha esperada**: `due_date_ok + mediana(retraso de la empresa)`, acotada a `>= corte + 1 dia` para toda factura viva. Con < 10 pagos previos se usa la mediana global (`retraso_global_por_muestra_corta`).
- **Probabilidad**: ver seccion 4; con muestra insuficiente se usa la tasa global del lado (`probabilidad_global_por_muestra_corta`).
- **No-fuga**: en el corte C solo se leen hechos <= C. Un test elimina y altera facturas y pagos posteriores a C y verifica que las cifras de C no cambian (`tests/test_payment_schedule.py`).
- **status-aware**: una factura `overdue` con `payment_date_ok = due_date_ok <= C` cuenta como VIVA (test dedicado).

## 7. Cobertura y cortes

- Origenes: `fin de mes cerrado >= primer dia con datos + 90 dias (historia previa) Y corte + h <= 2026-08-31 (target observable); MAS el corte vivo 2026-08-31 para todos los horizontes`.
- Cortes no vivos por horizonte (target observable): h=30: 21, h=60: 20, h=90: 19.
- Corte vivo anadido: 2026-08-31 (target no observable) para todos los horizontes.
- Filas de calendario: 22 cortes; ver `calendario.parquet` y `horizontes.parquet`.

## 8. Hallazgos y limitaciones

- El metodo comun acota la fecha esperada a `>= corte+1`: como la mediana de retraso del lado de pagos es baja, la mayor parte de la cartera ya vencida se concentra en los primeros dias del calendario y los horizontes 30/60/90 se diferencian sobre todo por la cartera aun no vencida. Se reporta como consecuencia directa del metodo prescrito.
- point-in-time por fechas de emision/vencimiento, no por registro certificado (la fuente no trae fecha de importacion ni de cancelacion).
- universo sin filtro de document_type_norm (unico que reproduce el censo del coordinador); difiere de debt_obligation.py.
- el calendario publica todas las fechas esperadas, no se trunca a 90 d.
- no se reutiliza el shrinkage de payment_delay_v2.py (ratio de severidad en EUR, no mediana de retraso en dias).

