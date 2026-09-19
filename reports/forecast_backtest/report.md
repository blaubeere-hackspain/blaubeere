# Backtest walk-forward y baselines del health_score v4

Generado por `xray/forecast_backtest.py` (version `forecast_backtest_v1`). Reproducible con:

```sh
.venv/bin/python -B -m xray.forecast_backtest --input reports/score_v4/assessments.parquet --output-dir reports/forecast_backtest
```

- Fuente: `reports/score_v4/assessments.parquet` (sha256 `9177fbf443f7aadfe696422ccec90a7f17e89f7c1192af68d134265409218c79`, 1312412 bytes).

## 1. Poblacion evaluable congelada

- Regla: health_score no nulo y excluida = false.
- Empresas: **957**.
- Empresa-mes con nota: **15116**.
- Digest de `(company_id, month, health_score)`: `f057a87f36ded41ff3ab011752ed6a03f1be26e754b05c22cfddc4c1201a8940`.
- Las tareas posteriores deben evaluar sobre esta poblacion: si el digest no coincide, la poblacion cambio.

## 2. Censo walk-forward (sin recorte de calentamiento)

- Origenes (m con m+1, m+2 y m+3 dentro del panel): **21** (2024-09-01 .. 2026-05-01).
- Origenes utilizables (>= 1 par en los 3 horizontes): **21**.

| origen | pares h=1 | perdidos h=1 | pares h=2 | perdidos h=2 | pares h=3 | perdidos h=3 | ventana parcial |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-09-01 | 209 | 7 | 214 | 2 | 215 | 1 | 100.0% |
| 2024-10-01 | 274 | 0 | 274 | 0 | 273 | 1 | 100.0% |
| 2024-11-01 | 301 | 0 | 300 | 1 | 300 | 1 | 100.0% |
| 2024-12-01 | 339 | 1 | 339 | 1 | 335 | 5 | 100.0% |
| 2025-01-01 | 423 | 0 | 418 | 5 | 416 | 7 | 100.0% |
| 2025-02-01 | 479 | 6 | 478 | 7 | 477 | 8 | 36.3% |
| 2025-03-01 | 519 | 4 | 519 | 4 | 513 | 10 | 35.2% |
| 2025-04-01 | 540 | 2 | 534 | 8 | 530 | 12 | 35.6% |
| 2025-05-01 | 557 | 7 | 553 | 11 | 539 | 25 | 32.3% |
| 2025-06-01 | 589 | 5 | 572 | 22 | 571 | 23 | 19.0% |
| 2025-07-01 | 601 | 19 | 599 | 21 | 595 | 25 | 16.6% |
| 2025-08-01 | 606 | 9 | 603 | 12 | 603 | 12 | 11.5% |
| 2025-09-01 | 628 | 10 | 628 | 10 | 626 | 12 | 11.8% |
| 2025-10-01 | 663 | 4 | 658 | 9 | 652 | 15 | 12.9% |
| 2025-11-01 | 681 | 5 | 674 | 12 | 668 | 18 | 12.4% |
| 2025-12-01 | 715 | 13 | 708 | 20 | 708 | 20 | 13.5% |
| 2026-01-01 | 793 | 8 | 784 | 17 | 781 | 20 | 20.5% |
| 2026-02-01 | 831 | 10 | 828 | 13 | 820 | 21 | 21.9% |
| 2026-03-01 | 852 | 11 | 842 | 21 | 840 | 23 | 20.5% |
| 2026-04-01 | 861 | 14 | 857 | 18 | 849 | 26 | 19.8% |
| 2026-05-01 | 865 | 10 | 854 | 21 | 837 | 38 | 15.4% |

### Totales por horizonte

| h | pares evaluables | empresas distintas | perdidos (nota en m, no en m+h) |
|---:|---:|---:|---:|
| 1 | 12326 | 936 | 145 |
| 2 | 12236 | 936 | 235 |
| 3 | 12148 | 936 | 323 |

### Meses del talon (no son origen completo: falta m+3)

| mes | pares h=1 | pares h=2 | pares h=3 |
|---|---:|---:|---:|
| 2026-06-01 | 871 | 854 | n/a |
| 2026-07-01 | 865 | n/a | n/a |
| 2026-08-01 | n/a | n/a | n/a |

### Calentamiento (no se recorta el censo)

- Criterio: origen anterior a que el panel tenga los 6 meses que exige la ventana del scorer; primer origen maduro = 2025-02-01.
- Origenes de calentamiento: 2024-09-01, 2024-10-01, 2024-11-01, 2024-12-01, 2025-01-01.
- Filas de poblacion en esos origenes: 1554; con `ventana_parcial = true`: 1554 (100.0%).
- NO se recorta el censo publicado; para seleccion de modelo se recomienda reportar tambien la subpoblacion_madura, que excluye esos origenes porque la nota de origen es parcial.
- Subpoblacion madura: origen >= 2025-02-01 (panel con >= 6 meses de historia); 16 origenes, digest `e8464f479d02a8c8bd7a6d2e0f0e4dd51efd98d07f2aa7c68866a146a06c763b`.

## 3. Baselines sobre el censo completo

Metricas por horizonte sobre la interseccion prediccion Y verdad (misma poblacion para los tres baselines).

| baseline | h | pares | empresas | MAE | RMSE | sesgo | error_abs_p50 | error_abs_p75 | error_abs_p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 1 | 12326 | 936 | 6.8039 | 13.2868 | 0.3420 | 2.5560 | 7.7253 | 18.2782 |
| B1_persistencia | 2 | 12236 | 936 | 10.1637 | 17.2922 | 0.9534 | 4.7994 | 13.1285 | 27.3827 |
| B1_persistencia | 3 | 12148 | 936 | 12.5965 | 20.0142 | 1.6081 | 6.7366 | 17.2484 | 33.4586 |
| B2_media_movil_3 | 1 | 12326 | 936 | 9.2648 | 15.2835 | 0.6823 | 4.7459 | 12.2624 | 24.2344 |
| B2_media_movil_3 | 2 | 12236 | 936 | 11.8982 | 18.5658 | 1.3136 | 6.7818 | 16.4760 | 30.7808 |
| B2_media_movil_3 | 3 | 12148 | 936 | 14.0275 | 21.0408 | 1.9732 | 8.5276 | 19.8062 | 35.5981 |
| B3_mediana_global | 1 | 12326 | 936 | 24.3991 | 28.8388 | -0.3376 | 23.2647 | 36.9137 | 45.9967 |
| B3_mediana_global | 2 | 12236 | 936 | 24.2244 | 28.6420 | 0.4411 | 23.0545 | 36.6087 | 45.4788 |
| B3_mediana_global | 3 | 12148 | 936 | 24.0977 | 28.4920 | 1.1813 | 22.8232 | 36.3796 | 45.1898 |

## 4. Metricas sobre la subpoblacion madura (sensibilidad)

| baseline | h | pares | empresas | MAE | RMSE | sesgo | error_abs_p50 | error_abs_p75 | error_abs_p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 1 | 10780 | 934 | 6.4797 | 12.7047 | 0.4554 | 2.4184 | 7.3757 | 17.2247 |
| B1_persistencia | 2 | 10691 | 932 | 9.8199 | 16.7959 | 0.9661 | 4.5804 | 12.5220 | 26.4115 |
| B1_persistencia | 3 | 10609 | 931 | 12.2732 | 19.6365 | 1.5084 | 6.4425 | 16.5362 | 32.5534 |
| B2_media_movil_3 | 1 | 10780 | 934 | 8.9455 | 14.7980 | 0.8771 | 4.5404 | 11.8033 | 23.5045 |
| B2_media_movil_3 | 2 | 10691 | 932 | 11.5440 | 18.1274 | 1.4053 | 6.3847 | 15.9184 | 30.0380 |
| B2_media_movil_3 | 3 | 10609 | 931 | 13.6675 | 20.6711 | 1.9570 | 8.0609 | 19.3840 | 35.0057 |
| B3_mediana_global | 1 | 10780 | 934 | 24.4689 | 28.8075 | -0.5524 | 23.5151 | 36.6813 | 45.3753 |
| B3_mediana_global | 2 | 10691 | 932 | 24.2810 | 28.6001 | 0.1317 | 23.2426 | 36.4540 | 44.9761 |
| B3_mediana_global | 3 | 10609 | 931 | 24.1230 | 28.4369 | 0.7633 | 23.0567 | 36.1303 | 44.6416 |

### Metricas sobre los origenes de calentamiento

| baseline | h | pares | empresas | MAE | RMSE | sesgo | error_abs_p50 | error_abs_p75 | error_abs_p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1_persistencia | 1 | 1546 | 424 | 9.0640 | 16.7937 | -0.4488 | 3.5677 | 10.7618 | 25.2131 |
| B1_persistencia | 2 | 1545 | 424 | 12.5424 | 20.3983 | 0.8654 | 6.9047 | 16.6076 | 33.1980 |
| B1_persistencia | 3 | 1539 | 424 | 14.8250 | 22.4459 | 2.2955 | 9.1281 | 20.7978 | 38.0370 |
| B2_media_movil_3 | 1 | 1546 | 424 | 11.4911 | 18.3146 | -0.6759 | 6.7128 | 15.5379 | 29.0911 |
| B2_media_movil_3 | 2 | 1545 | 424 | 14.3489 | 21.3542 | 0.6789 | 9.5558 | 19.7796 | 35.4436 |
| B2_media_movil_3 | 3 | 1539 | 424 | 16.5089 | 23.4312 | 2.0847 | 11.9143 | 22.5040 | 39.5731 |
| B3_mediana_global | 1 | 1546 | 424 | 23.9119 | 29.0565 | 1.1606 | 21.7089 | 38.9107 | 48.1575 |
| B3_mediana_global | 2 | 1545 | 424 | 23.8327 | 28.9301 | 2.5821 | 21.3506 | 38.3689 | 47.7979 |
| B3_mediana_global | 3 | 1539 | 424 | 23.9232 | 28.8685 | 4.0630 | 21.5626 | 38.3537 | 47.0439 |

## 5. Autocorrelacion de la nota

| h | pares | Pearson | Spearman | delta medio | delta abs medio | delta abs p50 | delta abs p90 | pendiente OLS |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 12326 | 0.8955 | 0.8958 | -0.3420 | 6.8039 | 2.5560 | 18.2782 | 0.8902 |
| 2 | 12236 | 0.8218 | 0.8230 | -0.9534 | 10.1637 | 4.7994 | 27.3827 | 0.8112 |
| 3 | 12148 | 0.7601 | 0.7619 | -1.6081 | 12.5965 | 6.7366 | 33.4586 | 0.7445 |

## 6. Diagnostico: B2 en meses sin nota (poblacion distinta)

B2 emitiendo prediccion tambien en meses sin nota pero con historia, evaluado con exigir_nota_en_origen=False; NO comparable con metricas_censo porque la poblacion de pares es mayor.

| h | pares | empresas | MAE | RMSE |
|---:|---:|---:|---:|---:|
| 1 | 12411 | 936 | 9.3107 | 15.3895 |
| 2 | 12360 | 937 | 11.9765 | 18.7152 |
| 3 | 12291 | 937 | 14.1292 | 21.2313 |

## 7. Lectura

- h=1: B1 persistencia MAE 6.8039 frente a B2 9.2648 y B3 24.3991; autocorrelacion Pearson 0.8955. B1 reduce el MAE de B2 un 26.6% y el de B3 un 72.1%.
- h=2: B1 persistencia MAE 10.1637 frente a B2 11.8982 y B3 24.2244; autocorrelacion Pearson 0.8218. B1 reduce el MAE de B2 un 14.6% y el de B3 un 58.0%.
- h=3: B1 persistencia MAE 12.5965 frente a B2 14.0275 y B3 24.0977; autocorrelacion Pearson 0.7601. B1 reduce el MAE de B2 un 10.2% y el de B3 un 47.7%.
- B1 (persistencia) gana a la media movil en los tres horizontes: la nota es inercial y promediar introduce retardo. El rival a batir es B1, no B2.
- El margen real de un modelo es la diferencia B1 - modelo, no B1 - B3. B3 es una referencia tonta deliberada.

## 8. Notas metodologicas

- health_score no nulo y excluida = false: nunca se imputa una nota ausente (ni cero, ni la anterior, ni la mediana). Un par sin nota en m o en m+h no es evaluable.
- Los tres baselines se evaluan sobre los mismos pares del censo (origenes m con m+1, m+2 y m+3 dentro del panel); el evaluador verifica que n_pares coincide con el censo por horizonte.
- B2 usa las ultimas 3 notas disponibles hasta m inclusive; los huecos no se rellenan. La rama sin historia no se ejerce en el censo porque el origen siempre tiene nota; se prueba con media_ultimas y se mide aparte la variante diagnostica.
- El recorte de calentamiento no se impone: el censo publicado es completo (6 meses de ventana del scorer motivan la subpoblacion madura, declarada con su cifra).
