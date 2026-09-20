# Calibracion de k para healthscore_v3 (medicion, no scorer)

Formula medida: H(m,k) = 100 * (C6 + k*R_hist) / (C6 + T6 + k), con C6/T6 las
sumas de la ventana expansiva hasta 6 meses cerrados max(primer_mes, m-5)..m y
R_hist la ratio acumulada de la propia empresa hasta m. k es un volumen virtual
en euros y el unico parametro libre. H es evaluable solo en empresa-mes del
subconjunto de calibracion (C, P y D determinados y con actividad observada);
la ventana suma unicamente lo observado y un mes sin actividad no se imputa
como cero.

## Subconjunto de calibracion y su sesgo

- Empresa-mes con C, P y D determinados: 3390 de 21036 meses con actividad (16.1%).
- Volumen mensual mediano (C+T) del subconjunto: 21,742.82 EUR.
- Volumen mediano del resto de meses con actividad (cota inferior conocida): 370,681.88 EUR.
- **Sesgo adverso: el volumen mediano de las empresa-mes con nota es 17.0 veces menor** que el de las que quedan
  en intervalo. Como el volumen del resto se mide con los subtotales conocidos
  (cota inferior), esta cifra es una cota inferior del sesgo real.
- **Advertencia de provisionalidad**: este subconjunto esta sesgado hacia
  empresas pequenas y bien clasificadas; la k recomendada es PROVISIONAL y debe
  revalidarse cuando se resuelva la ambiguedad de clasificacion (revision humana
  del paquete label_review). No se ha imputado el punto medio de ningun intervalo.

## Rango del barrido de k (derivado de los datos)

- Volumen mensual (C+T) del subconjunto: p05 = 30 EUR, p50 = 2.174e+04 EUR, p95 = 1.511e+06 EUR.
- Barrido logaritmico de 22 puntos desde k = 0.3 EUR (p05/100, muy por debajo del p05) hasta
k = 1.511e+08 EUR (p95*100, muy por encima del p95), mas k = 0
  como referencia obligatoria (k = 0 es v3 sin shrinkage, la ratio de la ventana).

## Tabla del barrido

Empresas en banda 45-55: mediana/maximo sobre los cortes con >= 2 notas. La
columna de tendencia usa empresas con >= 12 meses evaluables; la linea base con
mes natural es 22,7%.

| k (EUR) | |dH| p50 | |dH| p75 | |dH| p90 | std p50 (C5) | IQR p50 (C5) | empresas 45-55 p50/max | tendencia % (C3) | reactividad (meses) |
|---|---|---|---|---|---|---|---|---|
| 0 (sin shrinkage) | 2.72 | 8.10 | 17.72 | 29.19 | 42.32 | 33/50 | 33.3% | 5.0 |
| 0.3 | 2.72 | 8.10 | 17.72 | 29.19 | 42.32 | 33/50 | 33.3% | 5.0 |
| 0.779 | 2.72 | 8.10 | 17.72 | 29.19 | 42.32 | 33/50 | 33.3% | 5.0 |
| 2.023 | 2.72 | 8.10 | 17.72 | 29.18 | 42.32 | 33/50 | 33.3% | 5.0 |
| 5.252 | 2.74 | 8.12 | 17.72 | 29.16 | 42.32 | 33/50 | 33.3% | 5.0 |
| 13.64 | 2.74 | 8.14 | 17.72 | 29.11 | 42.32 | 33/50 | 33.3% | 5.0 |
| 35.41 | 2.74 | 8.14 | 17.82 | 29.03 | 41.95 | 33/52 | 33.3% | 5.0 |
| 91.93 | 2.75 | 8.12 | 17.72 | 28.93 | 41.95 | 33/51 | 33.3% | 5.0 |
| 238.7 | 2.76 | 8.10 | 17.61 | 28.91 | 40.36 | 32/51 | 35.3% | 5.0 |
| 619.8 | 2.76 | 8.09 | 17.35 | 28.73 | 38.95 | 33/50 | 35.3% | 5.0 |
| 1609 | 2.76 | 8.01 | 17.20 | 28.41 | 37.88 | 33/49 | 35.3% | 5.0 |
| 4178 **cumple C2** | 2.74 | 7.60 | 16.88 | 28.15 | 37.44 | 32/50 | 37.3% | 5.0 |
| 1.085e+04 **cumple C2** | 2.59 | 7.30 | 16.05 | 27.94 | 36.60 | 34/50 | 38.2% | 5.0 |
| 2.817e+04 **cumple C2** | 2.47 | 6.76 | 15.01 | 27.66 | 37.05 | 33/52 | 42.2% | 6.0 |
| 7.314e+04 **cumple C2** | 2.23 | 6.16 | 13.70 | 27.19 | 36.61 | 34/52 | 44.1% | 6.0 |
| 1.899e+05 **cumple C2** | 2.12 | 5.48 | 12.74 | 27.00 | 35.76 | 34/52 | 49.0% | 12.0 |
| 4.931e+05 **cumple C2** | 1.80 | 4.82 | 12.06 | 27.02 | 35.01 | 34/49 | 55.9% | 18.0 |
| 1.28e+06 **cumple C2** | 1.52 | 4.33 | 11.13 | 27.07 | 35.29 | 32/45 | 54.9% | 22.0 |
| 3.325e+06 **cumple C2** | 1.33 | 3.89 | 10.14 | 27.10 | 36.42 | 32/44 | 56.9% | 23.0 |
| 8.632e+06 **cumple C2** | 1.22 | 3.62 | 9.73 | 27.09 | 37.05 | 31/45 | 55.9% | 24.0 |
| 2.241e+07 **cumple C2** | 1.17 | 3.43 | 9.38 | 27.14 | 37.13 | 32/43 | 56.9% | 24.0 |
| 5.82e+07 **cumple C2** | 1.12 | 3.37 | 9.35 | 27.18 | 37.54 | 32/43 | 56.9% | 24.0 |
| 1.511e+08 **cumple C2** | 1.09 | 3.35 | 9.35 | 27.19 | 37.37 | 32/42 | 57.8% | 24.0 |

## Recomendacion

**k recomendada = 4178.45 EUR.**

Cumple C2 (p75 de |dH| = 7.60 < 8) y es la k
mas pequena del barrido que lo hace: la de menor coste en dispersion (C5) y
en reactividad. Sus cifras: |dH| p50 = 2.74, p90 = 16.88; std p50 = 28.15; IQR p50 = 37.44; empresas en 45-55 32/50; tendencia = 37.3%; reactividad = 5 meses; sesgo adverso (independiente de k) = 17.0x.

## Compromiso: que se gana y que se pierde al subir k

| k | |dH| p75 (estabilidad C2) | std p50 (dispersion C5) | IQR p50 | empresas 45-55 p50 | reactividad (meses) |
|---|---|---|---|---|---|
| k = 0 | 8.10 | 29.19 | 42.32 | 33 | 5 |
| k = 1.511e+08 | 3.35 | 27.19 | 37.37 | 32 | 24 |

Grafico ASCII del compromiso (p75 de |dH| frente a k, escala log):

```
k=0           |################################################  <- objetivo p75<8 no cumple
k=0.3         |################################################  <- objetivo p75<8 no cumple
k=0.779       |################################################  <- objetivo p75<8 no cumple
k=2.023       |################################################  <- objetivo p75<8 no cumple
k=5.252       |################################################  <- objetivo p75<8 no cumple
k=13.64       |################################################  <- objetivo p75<8 no cumple
k=35.41       |################################################  <- objetivo p75<8 no cumple
k=91.93       |################################################  <- objetivo p75<8 no cumple
k=238.7       |################################################  <- objetivo p75<8 no cumple
k=619.8       |################################################  <- objetivo p75<8 no cumple
k=1609        |###############################################  <- objetivo p75<8 no cumple
k=4178        |#############################################  <- objetivo p75<8 CUMPLE
k=1.085e+04   |###########################################  <- objetivo p75<8 CUMPLE
k=2.817e+04   |########################################  <- objetivo p75<8 CUMPLE
k=7.314e+04   |####################################  <- objetivo p75<8 CUMPLE
k=1.899e+05   |################################  <- objetivo p75<8 CUMPLE
k=4.931e+05   |############################  <- objetivo p75<8 CUMPLE
k=1.28e+06    |##########################  <- objetivo p75<8 CUMPLE
k=3.325e+06   |#######################  <- objetivo p75<8 CUMPLE
k=8.632e+06   |#####################  <- objetivo p75<8 CUMPLE
k=2.241e+07   |####################  <- objetivo p75<8 CUMPLE
k=5.82e+07    |####################  <- objetivo p75<8 CUMPLE
k=1.511e+08   |####################  <- objetivo p75<8 CUMPLE
```

Al subir k: gana estabilidad (baja el p75 de |dH|), pierde dispersion (las notas
se aplanan hacia 100*R_hist, con menor std/IQR entre empresas) y pierde
reactividad (mas meses hasta reflejar un cambio sostenido de la ratio).

## Reactividad (metodo declarado)

Medida sobre series sinteticas de escalon (12 escenarios:
ratios iniciales 0,35/0,45/0,55 con saltos sostenidos de +/-0,15 y +/-0,25,
volumen mensual constante = 2.174e+04 EUR, el mediano del
subconjunto). Retardo mediano: 24.0 meses; peor caso 24 meses.

## Notas

- C2 excluye los dos primeros meses de cada empresa (ventana parcial expansiva).
- C4 no depende de k (la evaluabilidad de H no cambia con k); se reporta igual
  en cada fila para trazabilidad.
- Los criterios exactos estan declarados en la cabecera de xray/calibrate_k.py;
  este modulo solo mide, no implementa el scorer v3.
