# debt_obligation — cierre de tarea (task_a6e3eb2b10e8)

Capa A de healthscore_v4: obligacion de deuda vencida y no atendida,
point-in-time, empresa-mes. Solo MIDE; no implementa el scorer v4.

## Ficheros

| fichero | estado | lineas |
|---|---|---|
| `xray/debt_obligation.py` | nuevo | 864 |
| `tests/test_debt_obligation.py` | nuevo (31 tests) | 372 |
| `reports/debt_obligation/debt_obligation_monthly.parquet` | salida | 30.864 filas x 23 cols |
| `reports/debt_obligation/coverage.json` | salida | — |

`xray/cli.py` NO se ha tocado: no hizo falta registrar subcomando.

## Criterio de impago (confirmado por el coordinador: opcion (a) status-aware)

`impagada al corte = NOT(status_norm='paid' AND payment_date_ok IS NOT NULL
AND payment_date_ok <= fin de m)`. En `clean/invoices.parquet` las facturas
`overdue` llevan `payment_date_ok = due_date_ok` (fecha PREVISTA, no real); la
lectura literal de `payment_date_ok` las daria por pagadas y reproduciria el
defecto. `status_norm='cancel'` queda excluido; la antiguedad se mide desde
`due_date_ok`.

Auditoria en `coverage.json -> criterio_impagada` (corte 2026-08-31):

- status-aware (vigente): **83.522 facturas / 726 empresas / 621,67 MEUR**
- literal (rechazado): 4.061 facturas / 303 empresas / 23,01 MEUR
- reparto por `status_norm`: overdue 77.223; payment_in_progress 724;
  paymentorder 191; pending 1.737; paid (pagadas despues del corte) 3.647

La cifra status-aware de referencia del coordinador era 83.515 (diff. de 7
facturas: su SQL comparaba `due_date_ok` TIMESTAMP contra DATE a medianoche y
excluia 56 facturas vencidas el propio 2026-08-31 con hora; aqui se compara
por dia natural `due_date_ok::DATE <= fin de m`, que es lo correcto).

## Cobertura medida

- Rejilla: 30.864 filas empresa-mes = 1.286 empresas x 24 meses cerrados.
- Empresas con obligacion vencida en algun corte: **743**
  (13.230 filas empresa-mes con `n_vencidas > 0`, 42,87% de la rejilla).
- `obligacion_vencida_eur`: 0 en 17.634 filas; NULL en 731 (todas sin importe
  medible); 13.230 con vencidas.
- Reparto de `confidence`: ninguna 15.871; alta 11.517; baja 2.941; media 535.
- `obligacion_no_atendida_eur`: n=30.133; mediana 0,0; p75 11.415,92;
  p90 258.466,89; max 43.145.234,81 EUR.
- `multiplicador_deuda` (no nulo): n=28.470; mediana 1,0; p75 1,0; p90 1,0;
  min 0,5; max 1,0. 10.836 filas con multiplicador < 1 (4.372 en la cota 0,5).
  2.394 filas con multiplicador NULL (1.882 sin magnitud de normalizacion y
  512 con vencidas todas sin importe medible).
- Deficit de servicio de deuda: **558 filas empresa-mes con
  `deficit_servicio_eur > 0`** (111 empresas); 1.057 filas calculables.
- Agujeros de importe (`n_sin_eur > 0`): 1.923 filas empresa-mes.

## Constantes de diseno (PROVISIONALES, sin calibrar)

- Pesos por bucket `1-30/31-60/61-90/91-180/180+` = `1,0 / 1,5 / 2,0 / 3,0 /
  5,0`: pesos crecientes porque un euro impagado mas antiguo es una senal de
  distress mas fuerte.
- `MULTIPLICADOR_MINIMO = 0.5`: cota de diseno provisional que evita que un
  impago lleve el multiplicador a 0 y deja hasta un 50% de castigo.
- Normalizacion `T6` (pagos operativos conocidos + servicio de deuda en la
  ventana movil de 6 meses hasta m, misma escala que el scorer v3), con
  fallback `6 x servicio_esperado`: se elige T6 por cobertura (servicio
  esperado solo existe para empresas con historial de deuda) y por expresar la
  presion como fraccion del propio volumen de pagos; `presion = severidad/T6`,
  `multiplicador = max(0,5; 1 - presion)`, exactamente 1,0 sin vencidas.
- `MIN_MESES_SERVICIO = 3`: minimo de meses previos con servicio > 0 para
  calcular `servicio_esperado`; con menos, NULL (no cero).

## Tests (salida literal)

Comando 1:

```
$ .venv/bin/python -m unittest tests.test_debt_obligation -v
...
Ran 31 tests in 0.002s

OK
```

Comando 2 (bateria preexistente, comando exacto del spec). En el worktree
`data/interim` no existe y `paths.WORKSPACE` queda en la raiz del worktree, por
lo que `test_cash_position.CliTest` falla por su `relative_to(paths.ROOT)` (no
por esta tarea); con la raiz canonica queda verde:

```
$ XRAY_PROJECT_ROOT=/Users/josemariaiznardosalar/Documents/embat-hack \
  .venv/bin/python -m unittest tests.test_contracts tests.test_marts \
  tests.test_pipeline tests.test_regressions tests.test_cash_position \
  tests.test_payment_delay tests.test_transfer_resolve tests.test_scoring_v3 \
  tests.test_scoring_io_v3 -v
...
Ran 134 tests in 60.761s

OK
```

Tambien pasa con el comando exacto dentro del repo canonico:

```
$ cd /Users/josemariaiznardosalar/Documents/embat-hack && \
  .venv/bin/python -m unittest tests.test_contracts tests.test_marts \
  tests.test_pipeline tests.test_regressions tests.test_cash_position \
  tests.test_payment_delay tests.test_transfer_resolve tests.test_scoring_v3 \
  tests.test_scoring_io_v3 -v
Ran 134 tests in 76.051s

OK
```

## Reproducir

```
.venv/bin/python -m xray.debt_obligation
# o en el worktree, apuntando al workspace canonico:
.venv/bin/python -m xray.debt_obligation \
  --workspace /Users/josemariaiznardosalar/Documents/embat-hack/data/runs/20260919T022951Z-83143e8d
```

Nota de entorno: en el worktree se creo un symlink `.venv` ->
`/Users/josemariaiznardosalar/Documents/embat-hack/.venv` para que
`.venv/bin/python` funcione; no es un artefacto de codigo (`.gitignore` ya
ignora `.venv/`).

## Fuera de alcance

No se ha tocado `xray/flows.py`, `xray/scoring*.py`, `xray/cash_position.py`,
`xray/payment_delay.py`, `xray/transfer_resolve.py`, `xray/calibrate_*.py`,
`xray/score_chart*.py`, ni ningun fichero bajo `data/`, ni `reports/` fuera de
`reports/debt_obligation/`, ni `docs/`, `readme.md`, `context_algo.md`. No se
usa `interim/debt_products.parquet`, `interim/debt_schedule_config.parquet`,
`clean/balances.parquet`, `marts/targets_proxy.parquet` ni
`reports/label_review/` en ninguna cifra publicada.
