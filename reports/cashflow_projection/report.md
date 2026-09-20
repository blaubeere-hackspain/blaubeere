# Proyeccion de caja por empresa (union cobros + pagos, 30/60/90 dias)

Generado por `xray/cashflow_projection.py` (version `cashflow_projection_v1`). Reproducible con:

```sh
PYTHONPATH=. .venv/bin/python -B -m xray.cashflow_projection --cobros-dir reports/collection_schedule --pagos-dir reports/payment_schedule --panel reports/daily_flows/panel_diario.parquet --censo-panel reports/daily_flows/censo.json --output-dir reports/cashflow_projection
```

## 0. Que es esta pieza y de donde viene

Un primer modelo extrapolaba la serie de flujo diario y fracaso: el mejor baseline
resulto ser literalmente "predice cero" (`reports/cashflow_forecast/report.md`).
Esta pieza cambia la fuente de senal: lee el dinero YA COMPROMETIDO en la cartera de
facturas. Une el lado de COBROS (importes positivos) y el de PAGOS (importes
negativos) con una SUMA DIRECTA, sin re-firmar ningun lado, y los mide.

- **Convenio de signo**: Cobros publica importes POSITIVOS (entrada de caja) y pagos importes NEGATIVOS (salida de caja). La union es una SUMA DIRECTA sin re-firmar ningun lado: neto = entradas + salidas (salidas negativas).
- **Intervalo**: el horizonte acumula (corte, corte+h]: el dia del corte queda excluido y el dia corte+h incluido; mismo convenio que xray/cashflow_forecast.py.
- **Banda neutra**: banda = fraccion * (entradas_esperadas + |salidas_esperadas|) con fraccion 0.10.

## 1. Reproduccion de las cifras de referencia del corte vivo 2026-08-31

| lado | h=30 (M EUR) | h=60 (M EUR) | h=90 (M EUR) | empresas con calendario |
|---|---:|---:|---:|---:|
| cobros | 263.6 | 309.7 | 321.1 | 654 |
| pagos | -356.3 | -417.8 | -439.9 | 737 |

Referencias del encargo: cobros +263.6 / +309.7 / +321.1 M EUR (654 empresas);
pagos -356.3 / -417.8 / -439.9 M EUR (737 empresas). Coinciden.

## 2. Forecast del corte vivo 2026-08-31

| h | empresas | entradas (M EUR) | salidas (M EUR) | NETO (M EUR) | tendencia pos/neu/neg |
|---:|---:|---:|---:|---:|---|
| 30 | 1,115 | 263.6 | -356.3 | -92.7 | 303 / 450 / 362 |
| 60 | 1,115 | 309.7 | -417.8 | -108.0 | 302 / 445 / 368 |
| 90 | 1,115 | 321.1 | -439.9 | -118.8 | 304 / 439 / 372 |

- Universo del corte vivo: **1,115 empresas**.
- Cobertura de lados: ambos 692; solo cobros 9; solo pagos 70; ninguno 344.
- Estado del lado de cobros: {'con_datos': 701, 'cero_conocido_sin_facturas_vivas': 414, 'desconocido_empresa_fuera_censo_panel': 0}.
- Estado del lado de pagos: {'con_datos': 762, 'cero_conocido_sin_facturas_vivas': 353, 'desconocido_empresa_fuera_censo_panel': 0}.
- Estado del saldo del corte: {'con_saldo_corte': 883, 'sin_saldo_corte_empresa_no_activa_en_panel': 232}.
- Importe esperado de PAGOS descartado por caer mas alla de corte+90: -713.1 M EUR en total (-50.5 M EUR en el corte vivo).

### Cruces a saldo negativo dentro de 90 dias

- Empresas que cruzan a saldo negativo: **118**.
- Dia mediano del cruce: **1.0 dias** desde el corte (2026-09-01); p25 1.0 d, p75 13.0 d.
- Empresas ya negativas en el saldo del corte: 26.

AVISO: la mediana del dia de cruce esta INFLADA por el pico artificial del dia
corte+1. Ambos lados colapsan a `>= corte+1` toda factura vencida cuyo pago esperado
ya paso (regla 3 de las tareas hermanas), de modo que el primer dia del calendario
concentra la mora vencida. El numero de empresas que cruzan (118) si es robusto; el
dia mediano (1) NO debe leerse como estacionalidad, solo como "casi de inmediato".

## 3. Evaluacion walk-forward contra la verdad observada

El arnes `xray/cashflow_forecast.py` se importa sin editar. Cortes por horizonte: {'30': 21, '60': 20, '90': 19}. La comparativa usa la MISMA poblacion comun (trios donde los tres modelos Y el target existen).

`diagnostico_B3`: 98.08% de las predicciones de B3 son exactamente 0 (38,969 de 39,733). B3 ES el baseline "predice cero".

### 3.1 Flujo neto acumulado: MAE y RMSE

| modelo | h | n | MAE EUR | RMSE EUR | mediana abs EUR | p75 abs EUR | sesgo EUR |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 30 | 14,491 | 889,923.63 | 34,449,169.05 | 48,956.10 | 213,593.86 | 176,764.93 |
| B2_media_movil_28d | 30 | 14,491 | 936,063.22 | 36,774,191.15 | 49,765.68 | 214,557.62 | 181,041.14 |
| B3_mediana_global | 30 | 14,491 | 392,406.80 | 8,631,983.10 | 28,119.29 | 125,241.29 | -72,867.73 |
| union | 30 | 14,491 | 499,150.80 | 8,688,958.32 | 52,126.20 | 216,705.95 | -97,978.35 |
| B1_persistencia | 60 | 13,579 | 2,221,153.42 | 70,937,596.13 | 68,531.66 | 288,299.82 | 567,183.51 |
| B2_media_movil_28d | 60 | 13,579 | 2,046,329.89 | 82,243,662.61 | 85,424.04 | 362,667.00 | 153,978.57 |
| B3_mediana_global | 60 | 13,579 | 903,838.26 | 36,538,005.79 | 38,640.72 | 168,051.64 | -440,901.01 |
| union | 60 | 13,579 | 1,020,978.98 | 36,565,960.83 | 69,728.10 | 274,786.25 | -463,933.27 |
| B1_persistencia | 90 | 11,663 | 2,181,161.47 | 66,970,953.98 | 79,042.77 | 337,534.73 | 1,152,342.17 |
| B2_media_movil_28d | 90 | 11,663 | 2,440,391.96 | 119,643,006.38 | 114,515.31 | 497,729.93 | 891,460.99 |
| B3_mediana_global | 90 | 11,663 | 582,926.43 | 9,700,487.94 | 44,217.24 | 197,581.24 | -73,757.95 |
| union | 90 | 11,663 | 713,768.25 | 9,768,203.47 | 80,900.00 | 311,119.71 | -87,709.97 |

### 3.2 Saldo proyectado: MAE y RMSE

| modelo | h | n | MAE EUR | RMSE EUR | mediana abs EUR | p75 abs EUR | sesgo EUR |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 30 | 12,990 | 949,125.94 | 36,369,200.29 | 53,545.71 | 223,823.31 | 198,316.51 |
| B2_media_movil_28d | 30 | 12,990 | 997,256.73 | 38,824,554.12 | 54,023.33 | 223,227.25 | 201,370.76 |
| B3_mediana_global | 30 | 12,990 | 411,527.51 | 9,085,442.93 | 30,989.57 | 129,262.43 | -76,228.85 |
| union | 30 | 12,990 | 516,787.02 | 9,134,345.04 | 55,910.97 | 224,809.89 | -103,017.20 |
| B1_persistencia | 60 | 12,211 | 2,397,695.22 | 74,787,747.41 | 74,464.96 | 294,056.28 | 654,801.90 |
| B2_media_movil_28d | 60 | 12,211 | 2,184,391.10 | 86,701,305.89 | 93,399.84 | 373,129.91 | 183,928.59 |
| B3_mediana_global | 60 | 12,211 | 955,205.44 | 38,504,799.51 | 42,507.51 | 173,276.21 | -467,606.41 |
| union | 60 | 12,211 | 1,068,946.75 | 38,527,451.24 | 74,629.02 | 287,499.87 | -496,942.02 |
| B1_persistencia | 90 | 10,571 | 2,333,705.54 | 70,324,900.79 | 84,818.08 | 341,417.75 | 1,306,074.57 |
| B2_media_movil_28d | 90 | 10,571 | 2,593,403.69 | 125,658,199.20 | 124,654.78 | 507,549.00 | 1,019,844.32 |
| B3_mediana_global | 90 | 10,571 | 586,036.56 | 10,052,391.56 | 48,195.20 | 200,471.61 | -51,493.95 |
| union | 90 | 10,571 | 714,820.87 | 10,106,676.12 | 85,836.97 | 320,413.78 | -73,261.32 |

### 3.3 Calibracion agregada (suma del forecast vs suma observada)

| modelo | h | n | predicho (M EUR) | observado (M EUR) | ratio pred/obs | sesgo (M EUR) |
|---|---:|---:|---:|---:|---:|---:|
| forecast_union | 30 | 14,491 | -363.9 | 1,055.9 | -0.345 | -1,419.8 |
| forecast_union | 60 | 13,579 | -312.8 | 5,987.0 | -0.052 | -6,299.7 |
| forecast_union | 90 | 11,663 | -162.5 | 860.5 | -0.189 | -1,023.0 |
| B1_persistencia | 30 | 14,491 | 3,617.4 | 1,055.9 | 3.426 | 2,561.5 |
| B1_persistencia | 60 | 13,579 | 13,688.8 | 5,987.0 | 2.286 | 7,701.8 |
| B1_persistencia | 90 | 11,663 | 14,300.3 | 860.5 | 16.619 | 13,439.8 |
| B3_mediana_global | 30 | 14,491 | 0.0 | 1,055.9 | 0.000 | -1,055.9 |
| B3_mediana_global | 60 | 13,579 | 0.0 | 5,987.0 | 0.000 | -5,987.0 |
| B3_mediana_global | 90 | 11,663 | 0.3 | 860.5 | 0.000 | -860.2 |


El agregado observado esta DOMINADO por unos pocos valores extremos no
factureros. Los 10 mayores |observado| de la poblacion comun y lo que predice la
union:

| empresa | corte | h | observado (M EUR) | union (M EUR) |
|---|---|---:|---:|---:|
| COMP_0604 | 2024-11-30 | 60 | 3,998.6 | 0.0 |
| COMP_0538 | 2024-11-30 | 60 | 999.9 | 0.0 |
| COMP_0538 | 2024-12-31 | 30 | 999.2 | 0.0 |
| COMP_0538 | 2024-12-31 | 60 | 998.9 | 0.0 |
| COMP_0538 | 2024-12-31 | 90 | 998.9 | 0.0 |
| COMP_0408 | 2026-06-30 | 60 | -208.2 | 0.0 |
| COMP_0408 | 2026-06-30 | 30 | -196.1 | 0.0 |
| COMP_0408 | 2026-05-31 | 90 | -142.1 | 0.0 |
| COMP_0408 | 2026-05-31 | 60 | -130.1 | 0.0 |
| COMP_0754 | 2026-03-31 | 90 | 90.7 | 0.0 |

### 3.4 Poblacion informada y correlacion

La union solo emite un flujo distinto de cero para empresas con facturas vivas.
En esa subpoblacion informada la comparativa se repite y se anade la correlacion:

| modelo | h | n | MAE EUR | RMSE EUR | mediana abs EUR | p75 abs EUR | sesgo EUR |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 30 | 8,545 | 483,206.88 | 2,435,406.95 | 50,114.39 | 191,055.95 | -28,188.56 |
| B2_media_movil_28d | 30 | 8,545 | 525,720.05 | 3,445,258.34 | 50,855.21 | 190,502.36 | -63,862.29 |
| B3_mediana_global | 30 | 8,545 | 290,935.87 | 1,655,324.94 | 29,457.53 | 113,522.12 | -24,032.36 |
| union | 30 | 8,545 | 471,957.19 | 2,100,873.70 | 72,789.20 | 265,793.66 | -66,616.08 |
| B1_persistencia | 60 | 8,111 | 692,197.82 | 3,359,859.20 | 68,220.53 | 258,048.97 | -54,808.40 |
| B2_media_movil_28d | 60 | 8,111 | 945,682.73 | 6,433,306.86 | 85,297.46 | 318,763.75 | -92,071.63 |
| B3_mediana_global | 60 | 8,111 | 419,488.01 | 2,410,939.08 | 38,871.83 | 152,898.34 | -32,458.20 |
| union | 60 | 8,111 | 615,598.70 | 3,038,742.09 | 90,850.62 | 322,263.27 | -71,017.58 |
| B1_persistencia | 90 | 7,066 | 763,622.14 | 3,823,715.52 | 78,028.35 | 301,971.08 | -48,825.03 |
| B2_media_movil_28d | 90 | 7,066 | 1,208,656.23 | 9,066,476.59 | 114,493.86 | 438,853.15 | -170,951.69 |
| B3_mediana_global | 90 | 7,066 | 450,174.57 | 2,622,465.35 | 44,818.51 | 179,341.33 | -8,985.78 |
| union | 90 | 7,066 | 666,141.78 | 3,008,857.91 | 105,423.66 | 357,012.27 | -31,999.87 |

| modelo | h | n | predicho (M EUR) | observado (M EUR) | ratio pred/obs | sesgo (M EUR) |
|---|---:|---:|---:|---:|---:|---:|
| forecast_union | 30 | 8,545 | -363.9 | 205.4 | -1.772 | -569.2 |
| forecast_union | 60 | 8,111 | -312.8 | 263.3 | -1.188 | -576.0 |
| forecast_union | 90 | 7,066 | -162.5 | 63.6 | -2.552 | -226.1 |
| B3_mediana_global | 30 | 8,545 | 0.0 | 205.4 | 0.000 | -205.4 |
| B3_mediana_global | 60 | 8,111 | 0.0 | 263.3 | 0.000 | -263.3 |
| B3_mediana_global | 90 | 7,066 | 0.2 | 63.6 | 0.002 | -63.5 |

| poblacion | h | n | pearson | spearman |
|---|---:|---:|---:|---:|
| poblacion_comun | 30 | 14,491 | -0.0082 | 0.0234 |
| poblacion_comun | 60 | 13,579 | -0.0096 | 0.0027 |
| poblacion_comun | 90 | 11,663 | -0.0026 | -0.0364 |
| poblacion_informada | 30 | 8,545 | -0.0570 | 0.0331 |
| poblacion_informada | 60 | 8,111 | -0.1923 | 0.0089 |
| poblacion_informada | 90 | 7,066 | -0.0125 | -0.0397 |

### 3.5 Acierto de signo del flujo neto

**h = 30** (n = 14,491)

- Tendencia con banda: acierto 0.3310 frente a trivial de la clase mayoritaria (positiva) 0.4657; supera trivial: NO.
- Signo estricto (sin banda): acierto 0.3522 frente a trivial 0.4657; supera trivial: NO.
- Matriz de confusion (tendencia con banda):

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 1,630 | 1,374 | 107 |
| negativa | 2,265 | 2,161 | 330 |
| neutra | 2,854 | 2,764 | 1,006 |

**h = 60** (n = 13,579)

- Tendencia con banda: acierto 0.3221 frente a trivial de la clase mayoritaria (positiva) 0.4817; supera trivial: NO.
- Signo estricto (sin banda): acierto 0.3410 frente a trivial 0.4817; supera trivial: NO.
- Matriz de confusion (tendencia con banda):

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 1,493 | 1,261 | 78 |
| negativa | 2,338 | 2,028 | 301 |
| neutra | 2,710 | 2,517 | 853 |

**h = 90** (n = 11,663)

- Tendencia con banda: acierto 0.3118 frente a trivial de la clase mayoritaria (positiva) 0.4808; supera trivial: NO.
- Signo estricto (sin banda): acierto 0.3295 frente a trivial 0.4808; supera trivial: NO.
- Matriz de confusion (tendencia con banda):

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 1,204 | 1,142 | 55 |
| negativa | 2,148 | 1,758 | 248 |
| neutra | 2,256 | 2,177 | 675 |

## 4. Cobertura por corte

| corte | empresas | ambos | solo cobros | solo pagos | ninguno | con saldo corte |
|---|---:|---:|---:|---:|---:|---:|
| 2024-11-30 | 669 | 303 | 36 | 138 | 192 | 442 |
| 2024-12-31 | 717 | 348 | 38 | 128 | 203 | 477 |
| 2025-01-31 | 821 | 391 | 36 | 121 | 273 | 604 |
| 2025-02-28 | 842 | 414 | 28 | 124 | 276 | 645 |
| 2025-03-31 | 875 | 423 | 28 | 119 | 305 | 684 |
| 2025-04-30 | 901 | 452 | 22 | 122 | 305 | 699 |
| 2025-05-31 | 927 | 475 | 19 | 123 | 310 | 716 |
| 2025-06-30 | 946 | 497 | 18 | 126 | 305 | 730 |
| 2025-07-31 | 992 | 514 | 21 | 130 | 327 | 765 |
| 2025-08-31 | 1,027 | 531 | 20 | 125 | 351 | 789 |
| 2025-09-30 | 1,052 | 539 | 19 | 129 | 365 | 821 |
| 2025-10-31 | 1,077 | 560 | 19 | 123 | 375 | 853 |
| 2025-11-30 | 1,106 | 571 | 19 | 123 | 393 | 877 |
| 2025-12-31 | 1,146 | 587 | 20 | 122 | 417 | 917 |
| 2026-01-31 | 1,206 | 620 | 17 | 114 | 455 | 1,012 |
| 2026-02-28 | 1,253 | 629 | 17 | 114 | 493 | 1,072 |
| 2026-03-31 | 1,262 | 653 | 13 | 103 | 493 | 1,085 |
| 2026-04-30 | 1,256 | 675 | 10 | 84 | 487 | 1,080 |
| 2026-05-31 | 1,250 | 675 | 12 | 86 | 477 | 1,072 |
| 2026-06-30 | 1,241 | 687 | 9 | 79 | 466 | 1,060 |
| 2026-07-31 | 1,230 | 691 | 9 | 75 | 455 | 1,036 |
| 2026-08-31 | 1,115 | 692 | 9 | 70 | 344 | 883 |

## 5. Insumos copiados de los worktrees hermanos

Copiados **tal cual, sin editar**; el arnes se importa y no se reimplementa.

- **cobros**: `xray/collection_schedule.py`, `tests/test_collection_schedule.py`, `reports/collection_schedule/calendario.parquet`, `reports/collection_schedule/horizontes.parquet`, `reports/collection_schedule/censo.json`, `reports/collection_schedule/report.md`
- **pagos**: `xray/payment_schedule.py`, `tests/test_payment_schedule.py`, `reports/payment_schedule/calendario.parquet`, `reports/payment_schedule/horizontes.parquet`, `reports/payment_schedule/censo.json`, `reports/payment_schedule/report.md`
- **arnes_g1_autorizado_por_el_coordinador**: `xray/cashflow_forecast.py`, `tests/test_cashflow_forecast.py`, `reports/cashflow_forecast/predicciones.parquet`, `reports/cashflow_forecast/baselines.json`, `reports/cashflow_forecast/report.md`

Copiados tal cual, sin editar. El arnes xray/cashflow_forecast.py se importa (nunca se reimplementa ni se edita) y sus artefactos y tests viajan con el por indicacion del coordinador.

## 6. Limitaciones declaradas

- El calendario de producto se trunca a (corte, corte+90]. El calendario de cobros ya viene truncado a 90 dias; el de pagos publica fechas esperadas mas alla de 90 dias, que se descartan por quedar fuera del horizonte de producto. Se publica cuanto importe de pagos cae fuera de la ventana.
- El universo por corte son las empresas con horizontes en algun lado MAS las empresas con fila en el panel en ese corte. Una empresa del panel sin facturas vivas de un lado recibe 0 en ese lado (cero conocido); no se imputa el saldo.
- El forecast es de flujo inducido por facturas: los movimientos de caja sin factura (nominas, impuestos, transferencias, financiacion) no se modelan y no entran en el flujo esperado. Para empresas sin facturas vivas el forecast neto es 0 y el saldo proyectado queda igual al saldo del corte.
- SCOPE: la verdad observada del arnes es el flujo neto TOTAL del panel de transacciones, cuyo alcance NO coincide con el del dinero facturero. Por eso la comparativa de calibracion agregada es desfavorable por construccion del target; la pieza debe leerse como calendario de caja comprometida en facturas, no como predictor de la caja total.
- saldo_reversa_eur se lee de la fila del corte del panel; si la empresa no esta activa en el panel ese dia, el saldo proyectado es nulo (nunca cero).
- La union no mezcla el corte vivo con los cortes historicos al estimar nada: no hay parametros que estimar, solo se lee la fila del corte C de cada lado. El corte vivo se publica como forecast de producto (sin target observable) y los cortes historicos se usan solo para medir.

## 7. Lectura honesta

- h=30: mejor MAE **B3_mediana_global**, mejor RMSE **B3_mediana_global**. Calibracion agregada ratio pred/obs: union -0.345 vs B3 0.000. Correlacion union-target (pearson/spearman): -0.008 / 0.023.
- h=60: mejor MAE **B3_mediana_global**, mejor RMSE **B3_mediana_global**. Calibracion agregada ratio pred/obs: union -0.052 vs B3 0.000. Correlacion union-target (pearson/spearman): -0.010 / 0.003.
- h=90: mejor MAE **B3_mediana_global**, mejor RMSE **B3_mediana_global**. Calibracion agregada ratio pred/obs: union -0.189 vs B3 0.000. Correlacion union-target (pearson/spearman): -0.003 / -0.036.
- RESULTADO PRINCIPAL, SIN MAQUILLAR: el forecast union PIERDE frente a "predice cero" (B3) en MAE Y en RMSE en los tres horizontes, y NO gana la calibracion agregada: su ratio predicho/observado es erratico y de signo opuesto al observado en varios cortes, y su sesgo agregado es mayor en valor absoluto que el de B3 (que predice 0). La correlacion con el target observado es ~0 y el acierto de signo (~0.31-0.35) queda por debajo de la regla trivial de la clase mayoritaria. Esto ocurre tambien en la subpoblacion "informada" (empresas con facturas vivas).
- POR QUE: el target observado del arnes es el flujo neto TOTAL del panel de transacciones, no el flujo facturero. Esta dominado por (i) movimientos no factureros (transferencias, financiacion, no economicos) de magnitud bruta enorme y (ii) unos pocos valores extremos de una sola empresa-corte (p.ej. +999.2 M EUR o +3,998.6 M EUR en 30/60 dias) para empresas que la union predice ~0 porque no tienen facturas vivas. La union mide dinero COMPROMETIDO en facturas; el target mide toda la caja. Son objetos distintos y la comparacion es desfavorable por construccion del target, no por un error de signo del pipeline.
- VALOR REAL DE LA PIEZA: no es un predictor del flujo total de caja. Es un CALENDARIO DE CAJA COMPROMETIDA en facturas, point-in-time, con signo correcto, desglose entradas/salidas, dia de movimiento y cruce a saldo negativo. Sirve para saber CUANDO y CUANTO dinero facturero se mueve y quien se queda sin colchon, no para acertar la caja total. El MAE, por ser L1, lo gana la mediana (cero) casi por construccion matematica; ninguna metrica pedida favorece aqui a la esperanza.
- HALLAZGO INCOMODO ADICIONAL: los horizontes publicados por las tareas hermanas excluyen parte del dia frontera corte+h (cobros por retraso fraccionario; pagos por marcas de tiempo sub-diarias) mientras su calendario si lo incluye. La union reproduce las referencias con los horizontes (convenio timestamp) y construye el calendario a grano de dia; la discrepancia queda medida y publicada.

