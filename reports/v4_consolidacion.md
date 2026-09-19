# Consolidacion v4: deuda, mora (v2) y verificacion de cash_backfill

Fecha: 2026-09-19. Repo canonico: `/Users/josemariaiznardosalar/Documents/embat-hack`.
Interprete: `.venv/bin/python`. Workspace de referencia: `data/runs/20260919T022951Z-83143e8d`.

## 1. Copias realizadas (literal, sin mover origenes)

| Origen | Destino en canonico | Estado previo del destino |
|---|---|---|
| `orca/workspaces/embat-hack/v4-deuda/xray/debt_obligation.py` | `xray/debt_obligation.py` | No existia |
| `orca/workspaces/embat-hack/v4-deuda/tests/test_debt_obligation.py` | `tests/test_debt_obligation.py` | No existia |
| `orca/workspaces/embat-hack/v4-deuda/reports/debt_obligation/` | `reports/debt_obligation/` | No existia |
| `orca/workspaces/embat-hack/v4-mora/xray/payment_delay_v2.py` | `xray/payment_delay_v2.py` | No existia |
| `orca/workspaces/embat-hack/v4-mora/tests/test_payment_delay_v2.py` | `tests/test_payment_delay_v2.py` | No existia |
| `orca/workspaces/embat-hack/v4-mora/reports/payment_delay_v2/` | `reports/payment_delay_v2/` | No existia |

Los seis destinos no existian: no hubo ninguna sobrescritura ni conflicto.
Los worktrees de origen permanecen intactos (copias con `cp`, no `mv`).
`xray/cash_backfill.py`, `tests/test_cash_backfill.py` y `reports/cash_backfill/`
ya estaban en el canonico y no se han tocado.

## 2. Ejecucion end-to-end y reproducibilidad

Ambos modulos corrieron desde el canonico contra el workspace indicado. Como los
generadores abortan si la ruta de salida ya existe (proteccion anti-destruccion),
la regeneracion se hizo a un directorio temporal y se comparo el sha256 contra las
copias; las copias canonicas bajo `reports/` NO fueron sobreescritas.

### sha256 antes (copias traidas) vs despues (regenerado desde canonico)

| Fichero | Antes (copia) | Despues (regenerado) |
|---|---|---|
| `debt_obligation/debt_obligation_monthly.parquet` | `7dccf66383a3345f...db0894a344` | `7dccf66383a3345f...db0894a344` (identico) |
| `debt_obligation/coverage.json` | `db610e2fb9feb16e8...e4490e2e674cc` | `db610e2fb9feb16e8...e4490e2e674cc` (identico) |
| `payment_delay_v2/payment_delay_v2_monthly.parquet` | `630c8def49a69fcaa...0786763ccb` | `630c8def49a69fcaa...0786763ccb` (identico) |
| `payment_delay_v2/coverage.json` | `f3bd847d79fad31da...25f8335f25f6` | `2981c3a6ca04bd171...4f78f8748cc2` (CAMBIA) |

Unicos hashes completos:

```
debt  parquet    antes=despues  7dccf66383a3345fc934609bc107ef86dfb4da096b9b5504339366db0894a344
debt  coverage   antes=despues  db610e2fb9feb16e82d7c2218b234ce2ad228fbd9d300eca137e4490e2e674cc
mora  parquet    antes=despues  630c8def49a69fcaab1f24a1a50e31bf4165a1b946cc277e66e4300786763ccb
mora  coverage   antes          f3bd847d79fad31da1db092f8707762ba92d812739d81174a0a225f8335f25f6
mora  coverage   despues        2981c3a6ca04bd17129997d912231609032b934163b1293a5fea4f78f8748cc2
```

### Analisis de la diferencia en `payment_delay_v2/coverage.json`

Diff completo del JSON: una sola linea (la anotacion `workspace`):

```
<   "workspace": "/Users/josemariaiznardosalar/Documents/embat-hack/data/runs/20260919T022951Z-83143e8d/data"
>   "workspace": "data/runs/20260919T022951Z-83143e8d/data"
```

- Causa: el modulo relativiza el workspace respecto de su raiz cuando la ruta es
  interna (`payment_delay_v2.py:1049-1051`). La copia original se genero desde el
  worktree apuntando al workspace del canonico por ruta absoluta (queda absoluta
  porque es externa al ROOT del worktree). Al regenerar desde el canonico, la ruta
  es interna y queda relativa.
- Es solo una cadena de anotacion de procedencia: **cero cifras de datos cambian**
  (filas, meses, empresas, distribuciones y correlaciones son identicos) y el
  parquet es byte a byte identico. No es un fallo de reproducibilidad de datos.

## 3. Bateria de tests (lista explicita, sin discover)

Comando exacto ejecutado:

```
.venv/bin/python -m unittest tests.test_contracts tests.test_marts tests.test_pipeline tests.test_regressions tests.test_cash_position tests.test_payment_delay tests.test_transfer_resolve tests.test_scoring_v3 tests.test_scoring_io_v3 tests.test_debt_obligation tests.test_payment_delay_v2 tests.test_cash_backfill -v
```

Resultado literal (cola):

```
Ran 194 tests in 63.236s
OK
```

194 tests, 0 fallos, 0 errores. Incluye `tests.test_debt_obligation` y
`tests.test_payment_delay_v2` recien traidos, junto a `tests.test_cash_backfill`
ya consolidado.

## 4. Diagnostico de coherencia de rejilla (las tres capas nuevas)

Las tres tablas se van a unir por `(company_id, month)`. Comparacion:

| Metrica | debt_obligation | payment_delay_v2 | cash_backfill |
|---|---|---|---|
| Filas totales | 30.864 | 30.864 | 32.150 |
| Filas de meses cerrados | 30.864 | 30.864 | 30.864 |
| Filas del mes ancla | 0 | 0 | 1.286 |
| Meses unicos | 24 (2024-09 .. 2026-08) | 24 (2024-09 .. 2026-08) | 25 (2024-09 .. 2026-09) |
| Empresas unicas | 1.286 | 1.286 | 1.286 |
| Duplicados (company_id, month) | 0 | 0 | 0 |

- Grano: exactamente una fila por `(company_id, month)` en las tres tablas.
- Mes ancla de cash_backfill: `2026-09-01`, 1.286 filas (una por empresa),
  coherente con 1.286 empresas. Los meses cerrados de cash son los 24 restantes
  (2024-09 .. 2026-08).
- **Conjunto de `(company_id, month)` de meses cerrados: IDENTICO en las tres
  capas** (30.864 pares; deuda == mora literalmente; cash cerrado == deuda == mora).
  Discrepancia: cero pares.
- Observacion de tipo (no bloqueante, ya conocida y reportada): cash_backfill
  guarda `month` como `object`/string (`'2024-09-01'`) mientras debt_obligation y
  payment_delay_v2 lo guardan como `datetime64[ms]`. Tras normalizar con
  `pd.to_datetime` los conjuntos coinciden al 100%. La capa D debera normalizar
  el tipo al hacer el join (o alinearse el dtype de publicacion en el futuro).

## 5. Conclusion

- Los seis elementos estan en el canonico (copia literal, origenes intactos).
- Ambos modulos corren end-to-end desde el canonico y regeneran sus salidas de
  forma reproducible (parquets byte a byte identicos; la unica diferencia de
  hash es la anotacion `workspace` de `payment_delay_v2/coverage.json`,
  explicada arriba, sin cambios de cifras).
- Bateria de 12 modulos de test: 194 tests, OK.
- Rejilla coherente entre las tres capas por `(company_id, month)` en meses
  cerrados; cash_backfill anade el mes ancla 2026-09 con 1.286 filas.
- No se ha tocado nada fuera de la lista blanca; sin commits ni staging.
