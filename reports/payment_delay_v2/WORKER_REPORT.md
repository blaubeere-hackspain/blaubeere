# Capa C de healthscore_v4 — indice de morosidad robusto (`xray/payment_delay_v2.py`)

## Que se ha hecho

Modulo NUEVO `xray/payment_delay_v2.py` (v3 `xray/payment_delay.py` intacto) con
CLI `--workspace / --output-dir / --v3-dir`, mismo grano `(company_id, month)`,
mismos 24 meses cerrados y misma rejilla de 1.286 empresas. Cuatro mecanismos de
robustez, todos implementados:

1. **Severidad ponderada por dias** en buckets 1-30 / 31-60 / 61-90 / 91-180 /
   180+ (mismos cortes que la capa A, aun inexistente).
2. **EWMA** sobre los cortes previos de la propia empresa (nunca mira al futuro).
3. **Dos lados separados** (`mora_pago_robusta` AP / `mora_cobro_robusta` AR) mas
   `mora_indice` combinado con pesos declarados.
4. **Shrinkage** por euros virtuales sobre cartera fina, con motivo declarado si
   no hay historia.

## Ficheros y lineas de codigo

| Fichero | Lineas | Estado |
|---|---|---|
| `xray/payment_delay_v2.py` | 1077 | nuevo |
| `tests/test_payment_delay_v2.py` | 342 | nuevo |
| `reports/payment_delay_v2/payment_delay_v2_monthly.parquet` | 30.864 filas x 50 cols | salida |
| `reports/payment_delay_v2/coverage.json` | — | salida |

`xray/cli.py` NO se toca (no hace falta registrar subcomando: v3 tampoco lo esta).
`xray/payment_delay.py`, `xray/debt_obligation.py`, `data/**`, `docs/**` y el
resto de `reports/**` NO se tocan.

## Comparativa obligatoria contra v3

Fuente: `reports/payment_delay/coverage.json` y su parquet (12841 filas / 691
empresas; mediana 0,17 / p75 0,75 / p90 1,0).

### Cobertura (ganancia)

| Indice | empresa-mes | empresas | % sobre 30.864 |
|---|---|---|---|
| v3 `mora_ratio` | 12.841 | 691 | 41,6% |
| v2 `mora_pago_robusta` | 14.337 | 756 | 46,5% |
| v2 `mora_cobro_robusta` | 11.805 | 667 | 38,3% |
| v2 `mora_indice` | **14.880** | **759** | **48,2%** |

Ganancia de `mora_indice` frente a v3: **+2.039 filas empresa-mes (+15,9%)** y
**+68 empresas (+9,8%)**. La razon: v3 exigia cartera sin agujeros fx/eur y solo
media el lado pago; v2 calcula severidad sobre la exposicion conocida (los
agujeros se cuentan aparte y degradan `confidence`) y el indice cubre filas con
un solo lado observable.

### Distribucion y reduccion de saturacion

| Indice | n | mediana | p75 | p90 |
|---|---|---|---|---|
| v3 `mora_ratio` | 12.841 | 0,1698 | 0,7485 | **1,0000** |
| v2 `mora_pago_robusta` | 14.337 | 0,0887 | 0,2562 | 0,5602 |
| v2 `mora_cobro_robusta` | 11.805 | 0,0890 | 0,2999 | 0,6313 |
| v2 `mora_indice` | 14.880 | 0,1000 | **0,2758** | **0,5448** |

La cola alta deja de estar saturada: **p75 baja de 0,75 a 0,28** y **p90 baja de
1,00 a 0,54** (pago 0,56; cobro 0,63). El indice gradua en vez de ser binario.

### Correlacion `mora_indice` vs `mora_ratio` de v3

Sobre las **12.841** filas donde ambos son no nulos:

- Pearson: **0,6837**
- Spearman: **0,7905**

Coherente (miden el mismo fenomeno) pero con mejor graduacion.

## Constantes de diseno (PROVISIONALES) y justificacion

| Constante | Valor | Justificacion (1 linea) |
|---|---|---|
| `BUCKET_EDGES_DIAS` | 30 / 60 / 90 / 180 | Mismos cinco cortes que la capa A (`debt_obligation.py`) para que ambas capas sean legibles juntas. |
| `BUCKET_PESOS` | 0,10 / 0,25 / 0,45 / 0,70 / 1,00 | Curva concava de perdida esperada que crece con la antiguedad y satura en 1,00 a partir de 180 dias. |
| `LAMBDA_EWMA` | 0,35 | El mes corriente pesa ~1/3: amortigua picos aislados y exige 2-3 meses de mora sostenida. |
| `KAPPA_EUR` | 4.178,45 | Mismo mecanismo de euros virtuales que el score; valor de `k` recomendado por la calibracion medida (`reports/calibration_k/report.md`). |
| `PESO_MORA_PAGO` | 0,70 | La conducta de pago es propia y accionable: pesa mas en el indice. |
| `PESO_MORA_COBRO` | 0,30 | El retraso de cobro es riesgo externo y mas ruidoso: pesa menos. |

`confidence` (reproducido en `coverage.json`): `ninguna` = sin cartera exigible;
`baja` = agujeros >= 50% de la cartera; `media` = algun agujero o cartera fina
(< 3 facturas o < 2.000 EUR); `alta` = sin agujeros y cartera gruesa. La
`confidence` de fila es la peor de los lados observables.

## Salida literal de los dos comandos de test

### Comando 1

```
$ .venv/bin/python -m unittest tests.test_payment_delay_v2 -v
...
Ran 18 tests in 0.209s

OK
```

Cubre los criterios pedidos: no fuga point-in-time (factura pagada despues del
corte cuenta como vencida; vencimiento posterior no cuenta), persistencia (pico
aislado < pico sostenido), shrinkage (cartera fina no publica 1,0 y queda entre
su historia y 1,0), buckets, dos lados, agujeros, cancel, cohortes y CLI.

### Comando 2 (bateria preexistente)

```
$ XRAY_PROJECT_ROOT=/Users/josemariaiznardosalar/Documents/embat-hack \
  .venv/bin/python -m unittest tests.test_contracts tests.test_marts \
  tests.test_pipeline tests.test_regressions tests.test_cash_position \
  tests.test_payment_delay tests.test_transfer_resolve tests.test_scoring_v3 \
  tests.test_scoring_io_v3 -v
...
Ran 134 tests in 68.801s

OK
```

Nota de entorno: el worktree no contiene `data/interim/` (solo `data/clean/` y
`data/marts/`). Sin `XRAY_PROJECT_ROOT` apuntando al repo canonico, 28 tests
preexistentes fallan por `data/interim/*.parquet` ausente (no por este cambio).
Con el workspace canonico configurado, los 134 pasan. El modulo v2 se ejecuto
end-to-end contra
`/Users/josemariaiznardosalar/Documents/embat-hack/data/runs/20260919T022951Z-83143e8d`
(hashes de `clean/invoices.parquet` y `marts/panel_cobro.parquet` sin cambios).
