# Calibracion de (k, alpha, beta) para healthscore_v4 (medicion)

Formula recalculada desde el parquet: H_final = 100*(C6 + colchon + k*R_hist)/(C6 + colchon + T6_efectivo + k), con colchon = min(colchon_v4, alpha*T6_efectivo), y H_final = H * (1 - beta*mora_indice) * multiplicador_deuda, factor 1 cuando mora o multiplicador son None. Identica a xray/scoring_v4.py (verificada por test y por la comprobacion de coherencia de abajo). Este modulo solo mide: NO cambia ningun default del motor.

## DECLARACION ANTI-LABEL (obligatoria)

**Los parametros k, alpha y beta NO estan ajustados contra ninguna etiqueta, ni contra targets_proxy, ni contra ninguna variable de resultado.** Este analisis no ha leido marts/targets_proxy.parquet ni reports/label_review/. No hay etiquetas validadas en este dataset y calibrar contra un proxy seria circular. La calibracion es por SENSIBILIDAD y ESTABILIDAD medidas, con los criterios declarados en la cabecera de xray/calibrate_params_v4.py (C1-C4) antes de medir.

## Poblacion de calibracion (declarada) y su sesgo

- Fichero: `/Users/josemariaiznardosalar/Documents/embat-hack/reports/score_v4/assessments.parquet`.
- **Poblacion POST-EXCLUSION**: 15116 empresa-mes con nota, 957 empresas, 24 meses; las 33 empresas excluidas por nota 0 persistente (792 empresa-mes con excluida = true y health_score NULL en el parquet) quedan FUERA de todas las cifras de este informe.
- Sin nota (no excluidas): 14956 empresa-mes.
- Maxima discrepancia entre health_score del parquet y el recalculo con las k/alpha/beta de cada fila: 0.00e+00 (15116 filas comparadas).
- Volumen mediano (C6 + T6_efectivo) de la poblacion con nota: 383,863.26 EUR.
- Volumen mediano conocido de la poblacion sin nota (COTA INFERIOR): 241,615.94 EUR.
- **Sesgo medido (de observabilidad, no de tamano): la poblacion sin nota tiene un volumen CONOCIDO 0.63 veces el de la con nota (cota inferior: su volumen real puede ser mucho mayor, porque sus flujos son justamente los desconocidos). Es el mismo defecto de observabilidad asimetrica que excluyo a 33 empresas; el 17x de v3 se media sobre otro subconjunto y no es comparable. La poblacion de calibracion esta sesgada hacia empresas bien clasificadas.

## C1: estabilidad mes a mes DENTRO de v4 (la metrica que faltaba)

Definicion: |dH_final| entre meses naturales consecutivos de la MISMA empresa, sobre la POBLACION COMUN declarada (pares con nota evaluable con todos los valores de parametro comparados; n_pairs por fila). Excluidos los pares que tocan la ventana parcial expansiva (ventana_parcial = true en alguno de los dos meses) y los dos primeros meses con fila de cada empresa (convencion C2 de v3). NO es la distancia v3->v4, que ya esta medida y es otra cosa.

- Pares consecutivos con nota en la poblacion: 14062; excluidos por primeros meses: 1874; excluidos por ventana parcial: 2166; candidatos: 10022.
- Con los parametros ACTUALES heredados (k=4178.45, alpha=3.0, beta=0.25): |dH| p50 = 2.36, p75 = 6.99, p90 = 16.12 (10022 pares).
- Senal cruda sin suavizar ningun parametro (k=0, alpha=0, beta=0) sobre la poblacion comun: |dH| p50 = 0.94, p75 = 5.29, p90 = 14.40 (10022 pares).
- Senal cruda sobre TODOS los pares con nota (sin exclusion de ventana): |dH| p50 = 0.85, p75 = 5.50, p90 = 15.76 (14062 pares): la exclusion de los pares jovenes apenas cambia la senal cruda (p75 5.50 frente a 5.29): la volatilidad de v4 no vive solo en los meses jovenes.
- **Umbral declarado: p75(|dH_final|) < 10 puntos (10% de la escala 0-100 en el 75% de los pares: la lectura practica de "nada de montanas rusas").** Justificacion medida: la senal cruda tiene p75 = 5.29 en la poblacion comun (5.50 sobre todos los pares) y los parametros heredados suben la cola a 6.99: los canales nuevos (colchon, mora, obligacion) y el termino historico anaden volatilidad moderada, no la reproducen. La cota 10 deja margen para reaccionar a eventos reales (p90 = 16.12, dominado por eventos discretos de mora y obligacion) y descarta la montana rusa. El 8 de v3 NO se copia a ciegas: se midio sobre otro denominador (sin obligacion vencida, volumenes ~17x menores, reports/calibration_k/report.md).

## Eje k (alpha=3.0, beta=0.25 fijados)

Rango derivado de los datos de v4: volumen (C6 + T6_efectivo) de la poblacion p05 = 1374 EUR, p50 = 3.839e+05 EUR, p95 = 1.375e+07 EUR; barrido logaritmico de 22 puntos desde k = 13.74 EUR (p05/100) hasta k = 1.375e+09 EUR (p95*100), mas k = 0 obligatorio y el k actual. Al subir k: gana estabilidad, pierde dispersion (aplano hacia 100*R_hist) y pierde reactividad.

| k (EUR) | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | empresas 45-55 p50/max | cobertura p50 | reactividad (m) |
|---|---|---|---|---|---|---|---|---|
| 0 (sin shrinkage) **cumple p75<10** | 2.30 | 7.10 | 16.51 | 28.76 | 47.86 | 64/98 | 629 | 5.0 |
| 13.74 **cumple p75<10** | 2.32 | 7.12 | 16.54 | 28.74 | 47.82 | 65/98 | 629 | 5.0 |
| 33.04 **cumple p75<10** | 2.33 | 7.12 | 16.51 | 28.74 | 47.43 | 66/98 | 629 | 5.0 |
| 79.44 **cumple p75<10** | 2.35 | 7.15 | 16.51 | 28.73 | 46.94 | 66/99 | 629 | 5.0 |
| 191 **cumple p75<10** | 2.36 | 7.17 | 16.54 | 28.71 | 46.61 | 68/100 | 629 | 5.0 |
| 459.2 **cumple p75<10** | 2.36 | 7.16 | 16.45 | 28.74 | 46.29 | 69/101 | 629 | 5.0 |
| 1104 **cumple p75<10** | 2.37 | 7.12 | 16.25 | 28.78 | 46.46 | 68/102 | 629 | 5.0 |
| 2654 **cumple p75<10** | 2.38 | 7.01 | 16.16 | 29.02 | 46.33 | 70/102 | 629 | 5.0 |
| 4178 **cumple p75<10** | 2.36 | 6.99 | 16.12 | 29.01 | 46.84 | 70/104 | 629 | 5.0 |
| 6381 **cumple p75<10** | 2.35 | 6.95 | 15.79 | 29.15 | 46.73 | 70/104 | 629 | 5.0 |
| 1.534e+04 **cumple p75<10** | 2.29 | 6.84 | 15.40 | 29.34 | 46.81 | 72/105 | 629 | 5.0 |
| 3.688e+04 **cumple p75<10** | 2.22 | 6.64 | 14.87 | 29.55 | 47.39 | 72/108 | 629 | 5.0 |
| 8.867e+04 **cumple p75<10** | 2.08 | 6.18 | 13.91 | 29.81 | 46.93 | 74/109 | 629 | 5.0 |
| 2.132e+05 **cumple p75<10** | 1.87 | 5.60 | 12.84 | 30.06 | 48.50 | 80/115 | 629 | 5.0 |
| 5.125e+05 **cumple p75<10** | 1.63 | 4.91 | 11.60 | 30.37 | 49.70 | 70/124 | 629 | 6.0 |
| 1.232e+06 **cumple p75<10** | 1.37 | 4.29 | 10.06 | 30.73 | 50.11 | 72/114 | 629 | 6.0 |
| 2.962e+06 **cumple p75<10** | 1.12 | 3.61 | 8.92 | 31.09 | 51.09 | 70/112 | 629 | 11.0 |
| 7.122e+06 **cumple p75<10** | 0.89 | 3.11 | 7.97 | 31.40 | 52.45 | 74/109 | 629 | 17.0 |
| 1.712e+07 **cumple p75<10** | 0.73 | 2.78 | 7.40 | 31.65 | 53.15 | 76/107 | 629 | 21.0 |
| 4.117e+07 **cumple p75<10** | 0.62 | 2.55 | 7.02 | 31.83 | 53.78 | 76/104 | 629 | 23.0 |
| 9.897e+07 **cumple p75<10** | 0.56 | 2.44 | 6.89 | 31.94 | 54.35 | 77/104 | 629 | 24.0 |
| 2.379e+08 **cumple p75<10** | 0.51 | 2.37 | 6.82 | 32.01 | 54.54 | 76/107 | 629 | 24.0 |
| 5.72e+08 **cumple p75<10** | 0.49 | 2.36 | 6.81 | 32.04 | 54.65 | 76/107 | 629 | 24.0 |
| 1.375e+09 **cumple p75<10** | 0.48 | 2.35 | 6.79 | 32.06 | 54.65 | 76/109 | 629 | 24.0 |

```
k=0           |################################################  <- objetivo p75<10 CUMPLE
k=13.74       |################################################  <- objetivo p75<10 CUMPLE
k=33.04       |################################################  <- objetivo p75<10 CUMPLE
k=79.44       |################################################  <- objetivo p75<10 CUMPLE
k=191         |################################################  <- objetivo p75<10 CUMPLE
k=459.2       |################################################  <- objetivo p75<10 CUMPLE
k=1104        |################################################  <- objetivo p75<10 CUMPLE
k=2654        |###############################################  <- objetivo p75<10 CUMPLE
k=4178        |###############################################  <- objetivo p75<10 CUMPLE
k=6381        |###############################################  <- objetivo p75<10 CUMPLE
k=1.534e+04   |##############################################  <- objetivo p75<10 CUMPLE
k=3.688e+04   |############################################  <- objetivo p75<10 CUMPLE
k=8.867e+04   |#########################################  <- objetivo p75<10 CUMPLE
k=2.132e+05   |######################################  <- objetivo p75<10 CUMPLE
k=5.125e+05   |#################################  <- objetivo p75<10 CUMPLE
k=1.232e+06   |#############################  <- objetivo p75<10 CUMPLE
k=2.962e+06   |########################  <- objetivo p75<10 CUMPLE
k=7.122e+06   |#####################  <- objetivo p75<10 CUMPLE
k=1.712e+07   |###################  <- objetivo p75<10 CUMPLE
k=4.117e+07   |#################  <- objetivo p75<10 CUMPLE
k=9.897e+07   |################  <- objetivo p75<10 CUMPLE
k=2.379e+08   |################  <- objetivo p75<10 CUMPLE
k=5.72e+08    |################  <- objetivo p75<10 CUMPLE
k=1.375e+09   |################  <- objetivo p75<10 CUMPLE
```

## Eje alpha (k=4178.45, beta=0.25 fijados)

El colchon v4 es el NIVEL de caja reconstruida (saldo_reversa), no el colchon invariante de v3: la escala cambio y el rango se re-deriva. Ratio colchon_v4/T6_efectivo (T6>0, colchon>0): p05 = 0.0064, p10 = 0.01997, p50 = 0.5801, p90 = 45.68, max = 4.525e+05. Saturacion = fraccion de empresa-mes con colchon_v4 > alpha*T6_efectivo. Influencia: empresa-mes que cambian su nota mas de 1 punto respecto a alpha=0.

| alpha | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | colchon saturado % | influencia (n > 1 pt) | influencia mediana |
|---|---|---|---|---|---|---|---|---|
| 0 | 1.07 | 5.26 | 13.80 | 32.35 | 53.45 | 91.1 | 0 | - |
| 0.00639966 | 1.07 | 5.23 | 13.75 | 32.23 | 53.01 | 86.6 | 0 | - |
| 0.0213527 | 1.13 | 5.19 | 13.69 | 31.99 | 52.05 | 81.6 | 3047 | 1.64 |
| 0.0712439 | 1.39 | 5.17 | 13.49 | 31.33 | 49.43 | 72.5 | 7327 | 2.76 |
| 0.237708 | 1.76 | 5.59 | 13.35 | 29.97 | 45.36 | 58.2 | 8974 | 5.10 |
| 0.793119 | 2.12 | 6.45 | 14.62 | 28.73 | 41.84 | 41.0 | 9748 | 7.50 |
| 2.64627 | 2.33 | 6.94 | 15.80 | 28.93 | 46.04 | 26.3 | 10100 | 9.19 |
| 3 | 2.36 | 6.99 | 16.12 | 29.01 | 46.84 | 25.0 | 10115 | 9.39 |
| 8.82937 | 2.41 | 7.16 | 16.30 | 29.76 | 50.16 | 16.7 | 10275 | 9.93 |
| 29.4595 | 2.40 | 7.21 | 16.38 | 30.19 | 50.54 | 10.7 | 10363 | 10.20 |
| 98.2925 | 2.39 | 7.17 | 16.30 | 30.46 | 51.34 | 6.8 | 10418 | 10.25 |
| 327.956 | 2.40 | 7.15 | 16.20 | 30.59 | 51.14 | 4.0 | 10439 | 10.26 |
| 1094.24 | 2.39 | 7.12 | 16.19 | 30.68 | 51.12 | 2.1 | 10445 | 10.27 |
| 3650.96 | 2.38 | 7.11 | 16.17 | 30.78 | 51.53 | 1.0 | 10446 | 10.27 |
| 12181.5 | 2.38 | 7.11 | 16.17 | 30.81 | 51.53 | 0.4 | 10446 | 10.27 |
| 40644.1 | 2.38 | 7.11 | 16.17 | 30.82 | 51.61 | 0.2 | 10446 | 10.27 |
| 135611 | 2.38 | 7.11 | 16.17 | 30.82 | 51.61 | 0.1 | 10446 | 10.27 |
| 452469 | 2.38 | 7.11 | 16.17 | 30.82 | 51.61 | 0.0 | 10446 | 10.27 |
| 452469 | 2.38 | 7.11 | 16.17 | 30.82 | 51.61 | 0.0 | 10446 | 10.27 |

## Eje beta (k=4178.45, alpha=3.0 fijados)

Rango [0, 1]: 0 = sin penalizacion por mora, 1 = mora total anula el canal. Castigo por mora: puntos de nota que se pierden sobre las filas con mora observable (p50 y p90). Influencia: empresa-mes que cambian su nota mas de 1 punto respecto a beta=0. Solape con el multiplicador de deuda: Spearman entre el castigo por mora y el castigo del multiplicador por empresa-mes (mora > 0 y multiplicador < 1), con beta=0.25: +0.430 (10384 filas comparables; +1 = los dos canales castigan a las mismas empresas: doble conteo sistematico).

| beta | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | castigo mora p50 | castigo mora p90 | influencia (n > 1 pt) | influencia mediana |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2.42 | 7.16 | 16.48 | 29.32 | 47.25 | 0.00 | 0.00 | 0 | - |
| 0.05 | 2.40 | 7.13 | 16.39 | 29.25 | 47.23 | 0.12 | 0.78 | 734 | 1.35 |
| 0.1 | 2.40 | 7.08 | 16.28 | 29.18 | 47.11 | 0.24 | 1.55 | 2001 | 1.67 |
| 0.15 | 2.38 | 7.05 | 16.21 | 29.11 | 46.98 | 0.36 | 2.33 | 3023 | 1.92 |
| 0.2 | 2.36 | 7.02 | 16.19 | 29.05 | 46.81 | 0.48 | 3.10 | 3748 | 2.11 |
| 0.25 | 2.36 | 6.99 | 16.12 | 29.01 | 46.84 | 0.60 | 3.88 | 4378 | 2.34 |
| 0.3 | 2.35 | 6.97 | 16.01 | 28.98 | 46.80 | 0.72 | 4.66 | 4877 | 2.55 |
| 0.35 | 2.33 | 6.94 | 15.86 | 28.96 | 46.72 | 0.84 | 5.43 | 5267 | 2.72 |
| 0.4 | 2.32 | 6.91 | 15.78 | 28.95 | 46.77 | 0.96 | 6.21 | 5599 | 2.91 |
| 0.5 | 2.32 | 6.86 | 15.58 | 28.93 | 46.97 | 1.20 | 7.76 | 6136 | 3.28 |
| 0.6 | 2.30 | 6.80 | 15.50 | 28.94 | 47.07 | 1.43 | 9.31 | 6559 | 3.61 |
| 0.75 | 2.29 | 6.71 | 15.20 | 29.00 | 47.49 | 1.79 | 11.64 | 7045 | 4.08 |
| 0.9 | 2.28 | 6.62 | 14.83 | 29.06 | 48.10 | 2.15 | 13.97 | 7393 | 4.60 |
| 1 | 2.26 | 6.58 | 14.72 | 29.11 | 48.67 | 2.39 | 15.52 | 7577 | 4.92 |

## Interaccion: como cambia el optimo de k segun alpha

Para cada alpha de referencia (derivado de la curva de saturacion: p10/p50/p90 del ratio colchon/T6, mas 0 y alpha_max) se re-barra el eje k y se elige la menor k que cumple p75<10 y no aplana.

| alpha de referencia | k* que cumple p75<10 | dH p75 con esa k | std p50 | reactividad (m) |
|---|---|---|---|---|
| 0 | 0 | 5.14 | 32.63 | 5.0 |
| 0.0199674 | 0 | 5.11 | 32.20 | 5.0 |
| 0.58013 | 459.192 | 6.27 | 28.25 | 5.0 |
| 45.6777 | 0 | 7.25 | 30.98 | 5.0 |
| 452469 | 0 | 7.28 | 31.11 | 5.0 |

## Interaccion: efecto de beta segun cuanta obligacion vencida hay

Castigo por mora (puntos, p50 y p90) repartido por presencia de obligacion vencida > 0 en el corte (con k=4178.45, alpha=3.0). Con obligacion > 0 la senal ya entra por denominador y multiplicador: el castigo por mora ahi es doble conteo.

| beta | obligacion > 0: castigo p50 | obligacion > 0: p90 | obligacion 0/NULL: p50 | obligacion 0/NULL: p90 |
|---|---|---|---|---|
| 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 0.05 | 0.14 | 0.80 | 0.00 | 0.22 |
| 0.1 | 0.28 | 1.60 | 0.00 | 0.44 |
| 0.15 | 0.41 | 2.40 | 0.00 | 0.66 |
| 0.2 | 0.55 | 3.20 | 0.00 | 0.89 |
| 0.25 | 0.69 | 4.00 | 0.00 | 1.11 |
| 0.3 | 0.83 | 4.80 | 0.00 | 1.33 |
| 0.35 | 0.97 | 5.60 | 0.00 | 1.55 |
| 0.4 | 1.11 | 6.40 | 0.00 | 1.77 |
| 0.5 | 1.38 | 8.00 | 0.00 | 2.22 |
| 0.6 | 1.66 | 9.60 | 0.00 | 2.66 |
| 0.75 | 2.07 | 12.00 | 0.00 | 3.32 |
| 0.9 | 2.49 | 14.40 | 0.00 | 3.99 |
| 1 | 2.76 | 16.00 | 0.00 | 4.43 |

## D. Variantes del triple conteo (medicion, sin elegir default)

La misma senal de impago entra por denominador, multiplicador y mora (reparto Shapley publicado del castigo: p50 0,163 denominador / 0,0785 mora / 0,6487 multiplicador, reports/score_v4/summary.json). Variantes medidas: (a) beta constante como ahora; (b) beta atenuado x0.5 cuando obligacion_vencida_m > 0; (c) mora aplicada SOLO cuando obligacion_vencida_m = 0 o NULL.

| Terna | Variante | dH p50 | dH p75 | dH p90 | std p50 | IQR p50 | empresas cambian de tramo (ultimo corte) | cambios de tramo (panel) |
|---|---|---|---|---|---|---|---|---|
| actual (4178.45, 3.0, 0.25) | a | 2.36 | 6.99 | 16.12 | 29.01 | 46.84 | 0 | 0 |
| actual (4178.45, 3.0, 0.25) | b | 2.39 | 7.08 | 16.24 | 29.13 | 47.04 | 5 | 182 |
| actual (4178.45, 3.0, 0.25) | c | 2.42 | 7.17 | 16.49 | 29.29 | 47.01 | 18 | 390 |

**Recomendacion razonada para el coordinador: variante (b) beta atenuado (x0.5) cuando obligacion_vencida_m > 0.** El default NO se cambia aqui. Lectura de los numeros (terna actual): |dH| p75 de (a) = 6.99, (b) = 7.08, (c) = 7.17; castigo por mora p50 de (a) = 0.60, (b) = 0.30 (el doble conteo que se quita), (c) = 0.00; empresas que cambian de tramo en el ultimo corte frente a (a): (b) 5, (c) 18. El reparto Shapley publicado (p50 0,163 denominador / 0,0785 mora / 0,6487 multiplicador; la mora p90 0,714) muestra que la mora duplica el castigo en la cola sobre una senal que ya entro por dos canales en el 69% de los empresa-mes con nota. Atenuar (b) o acotar (c) el canal mora SOLO donde ya hay obligacion vencida reduce ese doble conteo sin tocar el castigo donde no hay solape. La decision es del coordinador.

## Compromiso de cada parametro: que se gana y que se pierde al subirlo

| Parametro | Se gana | Se pierde |
|---|---|---|
| k (de 0 a 1.375e+09) | estabilidad: p75 de |dH| baja de 7.10 a 2.35 (todas las k hasta ~1e5 cumplen el umbral; la ganancia relevante empieza en k >= 1e5) | dispersion: std p50 sube de 28.76 a 32.06 (en v4 subir k NO aplana, a diferencia de v3); el coste real es la reactividad: 5 a 24 meses |
| alpha (de 0 a 4.525e+05) | el colchon (liquidez) pesa mas: saturacion del colchon baja de 86.6% a 0.0% de empresa-mes; la influencia mediana del colchon crece de 2.76 a 9.39 puntos | la nota sube para las empresas con caja (efecto techo) y gana volatilidad (p75 sube); por encima de alpha ~9 el parametro es casi inerte (saturacion <= 17%): el orden de magnitud del ratio colchon/T6 (p50 0.58, p75 3.8) situa el 3.0 dentro de escala
| beta (de 0 a 1) | castigo creciente por mora: castigo p50 de 0.00 a 2.39 puntos (p90 de 0.00 a 15.52) | estabilidad: NO empeora con beta (p75 baja levemente, 7.16 -> 6.58: la mora persistente amortigua los saltos); el coste es el solape con el multiplicador cuando hay obligacion vencida (Spearman +0.43), que se trata en la decision D, no bajando beta globalmente |

## Recomendacion

**RECOMENDACION: MANTENER k = 4178.45 EUR, alpha = 3 y beta = 0.25.** Los tres valores heredados de v3 quedan REVALIDADOS sobre la rejilla v4: cumplen p75(|dH|) < 10, no aplanan (criterio de dispersion declarado), la reactividad de k sigue en 5 meses y alpha/beta tienen influencia demostrable. Ninguna sustitucion del barrido gana estabilidad medida relevante sin coste mayor.
- k = 4178.45 (heredada de v3): REVALIDADA sobre la rejilla v4: p75=6.99 < 10, std p50 29.01 >= 0.85 x referencia (28.05) y reactividad 5 <= 6 meses. La menor k del barrido que tambien cumple es 0: no se recomienda porque desactiva el termino historico sin ganancia de estabilidad medido relevante, y las k grandes que ganan estabilidad la pagan con 12-24 meses de reactividad.
- alpha = 3 (heredado, nunca calibrado): REVALIDADO: influencia demostrable (10115 empresa-mes cambian > 1 punto), p75=6.99 < 10, dispersion OK y saturacion del colchon 25.0% <= 50%. El menor alpha del barrido que tambien cumple es 0.793: sustituir el actual sin ganancia medida no se justifica.
- beta = 0.25 (heredado, cota de diseno): REVALIDADO (p75=6.99 < 10 y dispersion OK en todo el eje; el canal mora castiga p50 0.60 / p90 3.88 puntos). El menor beta con influencia demostrable es 0.05, con un castigo mediano de 0.12 puntos: canal casi decorativo; bajar beta globalmente no es el tratamiento del doble conteo (ver decision D).
- verificacion conjunta (4178.45, 3, 0.25): p75=6.99 < 10 y dispersion OK

Valores MINIMOS del barrido que tambien cumplirian los criterios (referencia, no recomendados): k = 0, alpha = 0.793, beta = 0.05. Son el punto de partida si el coordinador quisiera estrechar cualquier canal; ninguno gana estabilidad medida relevante frente al valor mantenido.

Cifras de la terna conjunta: |dH_final| p50 = 2.36, p75 = 6.99, p90 = 16.12; std mediana = 29.01; IQR mediano = 46.84; empresas 45-55 p50/max = 70/104; cobertura p50 = 629 empresas por corte.

### Comparacion con los parametros actuales (heredados de v3)

Con (k=4178.45, alpha=3.0, beta=0.25): |dH| p50 = 2.36, p75 = 6.99, p90 = 16.12; std mediana = 29.01; reactividad = 5.0 meses. Cumple el objetivo p75<10: SI.

### Advertencia de provisionalidad

Los valores recomendados son PROVISIONALES: la poblacion de calibracion esta sesgada por OBSERVABILIDAD, no por tamano: la poblacion con nota es la mayoritaria y su volumen conocido es 1.6 veces MAYOR que el volumen conocido de la sin nota (cota inferior: el volumen real de la sin nota puede ser mucho mayor porque sus flujos son justamente los desconocidos, el mismo defecto de observabilidad asimetrica que excluyo a 33 empresas y que v3 midio como sesgo 17x sobre su subconjunto). Los parametros deben revalidarse cuando se resuelva la clasificacion de cobros (seccion 6 de context_algo_v4.md).

## Notas

- Exclusion de estabilidad: pares donde ALGUN mes tiene ventana_parcial = true o indice < 2 dentro de la serie de la empresa, mas los pares sin nota evaluable en alguna configuracion comparada (poblacion comun declarada; n_pairs por fila).
- Criterio de no aplanamiento: std mediana >= 0.85 * referencia (0,0,0) y empresas 45-55 mediana <= 1.2 * referencia (idem v3).
- Influencia demostrable: alguna empresa-mes cambia su nota mas de 1 punto respecto al valor 0 del eje.
- El barrido de ejes es por ejes; la interaccion k x alpha y el reparto de beta por obligacion vencida se reportan aparte, y la terna recomendada se verifica de forma conjunta (fila `joint`).
- Reactividad: series sinteticas de escalon de xray/calibrate_k.py (validas para v4 con colchon 0, obligacion 0 y mora None: la formula v4 se reduce exactamente a la v3).
- Este modulo solo mide; los defaults de xray/scoring_v4.py y xray/scoring_io_v4.py NO se tocan.
