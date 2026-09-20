# Backtest walk-forward de flujo de caja (30/60/90 dias)

Generado por `xray/cashflow_forecast.py` (version `cashflow_forecast_v1`). Reproducible con:

```sh
PYTHONPATH=. .venv/bin/python -B -m xray.cashflow_forecast --panel reports/daily_flows/panel_diario.parquet --censo reports/daily_flows/censo.json --output-dir reports/cashflow_forecast
```

- Panel: `reports/daily_flows/panel_diario.parquet` (sha256 `348f2550ee87a08ef7292554177d8c5b72eb3db8e3cdea8facde850f9d3479b6`).
- Censo diario: `reports/daily_flows/censo.json` (sha256 `77ffb29e38ba01e3a5ccaf516f7abc697203701b7e12a1cbe24e3ae1e5cfbebd`).

## 1. Poblacion por horizonte (censo de densidad 30/60/90)

Panel: 637471 filas empresa-dia, 1286 empresas, rango 2024-09-01 .. 2026-09-01. Ultimo dia cerrado: 2026-08-31.

Criterio: span >= W dias Y hueco_maximo <= W-1 Y mediana de dias con movimiento por ventana movil de W dias >= ceil(W/7).

| ventana W | umbral dias con mov. | empresas universo | evaluables v4 |
|---:|---:|---:|---:|
| 30 | 5 | 1017 | 732 |
| 60 | 9 | 1076 | 780 |
| 90 | 13 | 1087 | 790 |

**Ventana 60 declarada.** La ventana de 60 dias NO esta en el censo publicado: la constante `WINDOWS_DENSIDAD = (30, 90)` de `xray/daily_flows.py` fija las ventanas y este modulo no puede editar ese fichero. La ventana 60 se calcula aqui replicando el criterio primario (span >= 60, ninguna ventana movil de 60 dias vacia y mediana de dias con movimiento por ventana >= ceil(60/7) = 9) sobre los MISMOS helpers importados de daily_flows (`build_density`, `_window_counts`, `_gap_runs`, `_median`). La replica se valida porque las ventanas 30 y 90 reproducen exactamente los 732 y 790 evaluables v4 del censo existente.

Verificacion contra el censo ya consolidado en main:

| W | recomputado | censo publicado | coincide |
|---:|---:|---:|:---:|
| 30 | 732 | 732 | si |
| 90 | 790 | 790 | si |

Poblacion usada en el backtest (universo con densidad suficiente):

| horizonte h | universo densidad | evaluables v4 en la poblacion |
|---:|---:|---:|
| 30 | 1017 | 732 |
| 60 | 1076 | 780 |
| 90 | 1087 | 790 |

## 2. Cortes y censo de trios

- Criterio de corte: fin de mes cerrado >= primer dia del panel + 90 dias (historia previa) Y corte + h <= 2026-08-31 (target observable).
- Cortes por horizonte: h=30: 21, h=60: 20, h=90: 19.
- Convenio de intervalo: El target acumula/observa el intervalo ABIERTO por la izquierda y CERRADO por la derecha: (corte, corte+h]. El dia del corte queda EXCLUIDO del flujo acumulado y el dia corte+h queda INCLUIDO. El saldo proyectado es el saldo reconstruido de la fila corte+h. Es el mismo convenio para targets y para el flujo de persistencia de B1, de modo que B1 en h=30 repite literalmente el flujo observado en el intervalo inmediatamente anterior.

| h | trios evaluables | empresas | origenes | trios evaluables v4 |
|---:|---:|---:|---:|---:|
| 30 | 14796 | 1017 | 21 | 11207 |
| 60 | 13859 | 1073 | 20 | 10654 |
| 90 | 12002 | 1070 | 19 | 9334 |

### Cobertura de cada baseline (las metricas usan la interseccion comun)

| h | trios | B1 predice | B2 predice | B3 predice | comunes flujo | comunes saldo |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 14491 | 14491 | 14491 | 14491 | 14491 | 12990 |
| 60 | 13579 | 13579 | 13579 | 13579 | 13579 | 12211 |
| 90 | 11767 | 11767 | 11767 | 11663 | 11663 | 10571 |

## 3. Metricas por baseline: flujo neto acumulado

| baseline | h | n | MAE EUR | mediana error abs EUR | error_abs_p75 EUR | error_abs_p90 EUR | mediana error escalado | media error escalado | sesgo EUR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 30 | 14491 | 889,923.6295 | 48,956.1000 | 213,593.8600 | 852,565.9300 | 0.2595 | 6,978.2932 | 176,764.9285 |
| B2_media_movil_28d | 30 | 14491 | 936,063.2203 | 49,765.6800 | 214,557.6204 | 890,054.3793 | 0.2652 | 6,978.3006 | 181,041.1387 |
| B3_mediana_global | 30 | 14491 | 392,406.8034 | 28,119.2900 | 125,241.2950 | 490,426.0900 | 0.1417 | 6,978.1611 | -72,867.7326 |
| B1_persistencia | 60 | 13579 | 2,221,153.4190 | 68,531.6600 | 288,299.8200 | 1,144,695.9507 | 0.1709 | 12,880.7566 | 567,183.5134 |
| B2_media_movil_28d | 60 | 13579 | 2,046,329.8878 | 85,424.0371 | 362,667.0029 | 1,460,141.1294 | 0.2165 | 12,880.8281 | 153,978.5661 |
| B3_mediana_global | 60 | 13579 | 903,838.2596 | 38,640.7200 | 168,051.6400 | 684,832.5040 | 0.0954 | 12,880.6542 | -440,901.0069 |
| B1_persistencia | 90 | 11663 | 2,181,161.4729 | 79,042.7700 | 337,534.7306 | 1,307,239.3080 | 0.1272 | 21,625.0206 | 1,152,342.1698 |
| B2_media_movil_28d | 90 | 11663 | 2,440,391.9622 | 114,515.3079 | 497,729.9323 | 1,974,279.3463 | 0.1921 | 21,625.1267 | 891,460.9852 |
| B3_mediana_global | 90 | 11663 | 582,926.4321 | 44,217.2400 | 197,581.2400 | 745,227.7460 | 0.0693 | 21,626.0878 | -73,757.9510 |

## 4. Metricas por baseline: saldo proyectado

| baseline | h | n | MAE EUR | mediana error abs EUR | error_abs_p75 EUR | error_abs_p90 EUR | mediana error escalado | media error escalado | sesgo EUR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 30 | 12990 | 949,125.9374 | 53,545.7050 | 223,823.3100 | 878,889.8105 | n/a | n/a | 198,316.5062 |
| B2_media_movil_28d | 30 | 12990 | 997,256.7263 | 54,023.3282 | 223,227.2471 | 922,274.1970 | n/a | n/a | 201,370.7590 |
| B3_mediana_global | 30 | 12990 | 411,527.5101 | 30,989.5700 | 129,262.4300 | 507,866.4785 | n/a | n/a | -76,228.8545 |
| B1_persistencia | 60 | 12211 | 2,397,695.2240 | 74,464.9623 | 294,056.2770 | 1,181,876.7500 | n/a | n/a | 654,801.9009 |
| B2_media_movil_28d | 60 | 12211 | 2,184,391.0989 | 93,399.8429 | 373,129.9095 | 1,477,069.4871 | n/a | n/a | 183,928.5892 |
| B3_mediana_global | 60 | 12211 | 955,205.4413 | 42,507.5100 | 173,276.2100 | 701,236.2900 | n/a | n/a | -467,606.4132 |
| B1_persistencia | 90 | 10571 | 2,333,705.5394 | 84,818.0800 | 341,417.7450 | 1,340,374.2300 | n/a | n/a | 1,306,074.5658 |
| B2_media_movil_28d | 90 | 10571 | 2,593,403.6909 | 124,654.7757 | 507,549.0000 | 1,989,606.5964 | n/a | n/a | 1,019,844.3176 |
| B3_mediana_global | 90 | 10571 | 586,036.5578 | 48,195.2000 | 200,471.6050 | 772,016.9900 | n/a | n/a | -51,493.9514 |

## 5. Ranking y sensibilidad de la metrica

El ranking escalado usa la MEDIANA del error escalado (robusta); la media se publica en la tabla anterior pero esta dominada por un unico outlier (ver seccion 7).

| objetivo | h | ganador MAE EUR | ganador escalado (mediana) | coincide orden |
|---|---:|---|---|:---:|
| flujo_neto_acumulado | 30 | B3_mediana_global | B3_mediana_global | si |
| flujo_neto_acumulado | 60 | B3_mediana_global | B3_mediana_global | NO |
| flujo_neto_acumulado | 90 | B3_mediana_global | B3_mediana_global | si |
| saldo_proyectado | 30 | B3_mediana_global | n/a | n/a |
| saldo_proyectado | 60 | B3_mediana_global | n/a | n/a |
| saldo_proyectado | 90 | B3_mediana_global | n/a | n/a |

Orden completo por horizonte (flujo neto acumulado):

- h=30: MAE EUR ['B3_mediana_global', 'B1_persistencia', 'B2_media_movil_28d']; error escalado (mediana) ['B3_mediana_global', 'B1_persistencia', 'B2_media_movil_28d'].
- h=60: MAE EUR ['B3_mediana_global', 'B2_media_movil_28d', 'B1_persistencia']; error escalado (mediana) ['B3_mediana_global', 'B1_persistencia', 'B2_media_movil_28d'].
- h=90: MAE EUR ['B3_mediana_global', 'B1_persistencia', 'B2_media_movil_28d']; error escalado (mediana) ['B3_mediana_global', 'B1_persistencia', 'B2_media_movil_28d'].

Diagnostico de B3: 38969 de 39733 predicciones son exactamente 0 (98.08%). B3 (mediana cross-empresa del flujo acumulado) produce casi siempre 0 porque la mediana de la distribucion de flujos acumulados esta centrada en cero. En la practica B3 es el baseline "predice flujo neto cero".

## 6. Tendencia derivada del forecast

Banda neutra: referencia = mediana de las predicciones de las dos lineas base especificas de la empresa (B1 y B2); banda = (max - min) / 2 de esas dos predicciones. positiva si referencia > banda, negativa si referencia < -banda, neutra en otro caso. La banda es EX-ANTE (solo usa predicciones) y equivale a no declarar direccion cuando B1 y B2 no coinciden en el signo. B3 no entra en la tendencia por ser una referencia global identica para todas las empresas.

### h = 30 (n = 14491)

- Distribucion predicha: {'positiva': 5694, 'negativa': 6122, 'neutra': 2675}.
- Distribucion observada: {'positiva': 6749, 'negativa': 6299, 'neutra': 1443}.
- Acierto global: 0.4462 frente a la regla trivial de la clase mayoritaria (positiva): 0.4657. Supera la trivial: NO.
- Solo cuando declara direccion (n=11816): acierto 0.4334 frente a trivial direccional 0.5075. Supera la trivial: NO.
- Acierto por clase (recall): {'positiva': 0.3744258408653134, 'negativa': 0.4118113986347039, 'neutra': 0.932085932085932}.

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 2527 | 3127 | 40 |
| negativa | 3470 | 2594 | 58 |
| neutra | 752 | 578 | 1345 |

### h = 60 (n = 13579)

- Distribucion predicha: {'positiva': 4206, 'negativa': 4511, 'neutra': 4862}.
- Distribucion observada: {'positiva': 6541, 'negativa': 5806, 'neutra': 1232}.
- Acierto global: 0.3413 frente a la regla trivial de la clase mayoritaria (positiva): 0.4817. Supera la trivial: NO.
- Solo cuando declara direccion (n=8717): acierto 0.3973 frente a trivial direccional 0.5248. Supera la trivial: NO.
- Acierto por clase (recall): {'positiva': 0.2727411710747592, 'negativa': 0.2891836031691354, 'neutra': 0.9512987012987013}.

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 1784 | 2403 | 19 |
| negativa | 2791 | 1679 | 41 |
| neutra | 1966 | 1724 | 1172 |

### h = 90 (n = 11767)

- Distribucion predicha: {'positiva': 3441, 'negativa': 3650, 'neutra': 4676}.
- Distribucion observada: {'positiva': 5655, 'negativa': 5126, 'neutra': 986}.
- Acierto global: 0.3193 frente a la regla trivial de la clase mayoritaria (positiva): 0.4806. Supera la trivial: NO.
- Solo cuando declara direccion (n=7091): acierto 0.3950 frente a trivial direccional 0.5228. Supera la trivial: NO.
- Acierto por clase (recall): {'positiva': 0.2546419098143236, 'negativa': 0.2655091689426453, 'neutra': 0.9695740365111561}.

| predicha \ observada | positiva | negativa | neutra |
|---|---:|---:|---:|
| positiva | 1440 | 1993 | 8 |
| negativa | 2267 | 1361 | 22 |
| neutra | 1948 | 1772 | 956 |

## 7. Lectura honesta

- flujo_neto_acumulado h=30: gana en MAE EUR **B3_mediana_global** (392,406.8034 EUR) y en error escalado (mediana) **B3_mediana_global**. El orden coincide entre ambas metricas.
- flujo_neto_acumulado h=60: gana en MAE EUR **B3_mediana_global** (903,838.2596 EUR) y en error escalado (mediana) **B3_mediana_global**. El orden CAMBIA entre metrica absoluta y escalada: un baseline que gana en EUR puede perder al normalizar por tamano de empresa.
- flujo_neto_acumulado h=90: gana en MAE EUR **B3_mediana_global** (582,926.4321 EUR) y en error escalado (mediana) **B3_mediana_global**. El orden coincide entre ambas metricas.
- saldo_proyectado h=30: gana en MAE EUR **B3_mediana_global** (411,527.5101 EUR). El error escalado no aplica a este objetivo (la escala es de flujo neto).
- saldo_proyectado h=60: gana en MAE EUR **B3_mediana_global** (955,205.4413 EUR). El error escalado no aplica a este objetivo (la escala es de flujo neto).
- saldo_proyectado h=90: gana en MAE EUR **B3_mediana_global** (586,036.5578 EUR). El error escalado no aplica a este objetivo (la escala es de flujo neto).
- **Hallazgo central**: B3 (mediana global) gana MAE y error escalado en los tres horizontes. Su prediccion es casi siempre ~0 (la mediana cross-empresa del flujo acumulado es ~0), de modo que el mejor baseline es, en la practica, "predice flujo neto cero". Ninguna linea base bate ese punto con margen: el flujo neto diario es casi impredecible en magnitud y signo con aritmetica de persistencia.
- **Hallazgo incomodo**: el error escalado medio esta dominado por un solo trio (COMP_0469 h=90): un movimiento de 81,834,063.6364 EUR tras una ventana previa sin flujo NETO. Por eso el ranking escalado usa la mediana.
- tendencia h=30: acierto global 0.4462 frente a trivial 0.4657; acierto direccional 0.4334 frente a trivial direccional 0.5075. NO supera la regla trivial: la tendencia derivada del forecast no aporta valor en este horizonte.
- tendencia h=60: acierto global 0.3413 frente a trivial 0.4817; acierto direccional 0.3973 frente a trivial direccional 0.5248. NO supera la regla trivial: la tendencia derivada del forecast no aporta valor en este horizonte.
- tendencia h=90: acierto global 0.3193 frente a trivial 0.4806; acierto direccional 0.3950 frente a trivial direccional 0.5228. NO supera la regla trivial: la tendencia derivada del forecast no aporta valor en este horizonte.

## 8. Limitaciones declaradas

- PRECISION: `saldo_reversa_eur` es una reconstruccion hacia atras desde el ancla 2026-09-01 y su `saldo_reversa_available_at` es el ancla, posterior a todos los cortes. El modulo NO recalcula el saldo: lee el valor ya almacenado en la fila del corte. El panel diario documenta esa reconstruccion como recuperacion de un nivel de caja que la empresa conocia en su momento; se mantiene aqui por coherencia con la tarea P0 y se declara como limitacion. El test de no-fuga verifica que ninguna prediccion cambia si se eliminan o alteran las filas posteriores al corte.
- La poblacion es el universo con densidad suficiente para cada horizonte (no solo las evaluables v4). El censo publica ambos conteos; las metricas se miden sobre el universo para no encoger la muestra.
- B1 y B2 predicen en todos los trios; B3 no predice en los primeros cortes porque exige un origen anterior ya cerrado. El ranking se calcula sobre la interseccion comun por horizonte (misma poblacion para los tres baselines), y se publica el n de cada baseline.
- La densidad se mide sobre el span completo observado de la empresa, no localmente alrededor del corte; un trio exige ademas h dias de rejilla previa y target observable, de modo que la historia del corte si esta garantizada.
- El saldo proyectado hereda los huecos FX del panel: si saldo_reversa_eur es nulo en el corte o en corte+h, el objetivo/prediccion de saldo no existe y se descarta de las metricas de saldo (nunca se imputa).
