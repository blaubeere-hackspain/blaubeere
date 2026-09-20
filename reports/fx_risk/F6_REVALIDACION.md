# F6 · Enganche de los tipos del FMI (F1c) en la capa FX y regeneración de la cadena

## Artefacto regenerado (lo primero)

**HTML absoluto:**
`/Users/josemariaiznardosalar/orca/workspaces/embat-hack/f6-fmi/reports/score_charts/healthscore_v4/index.html`

Cadena regenerada (en este orden):

1. `reports/fx_risk/fx_risk_monthly.parquet` (+ `contrafactual.json`, `INFORME.md`) — `xray.fx_risk --force`
2. `reports/score_v4/{assessments.parquet,summary.json,exclusiones.json}` — `xray.scoring_io_v4 --con-fx`
3. `reports/score_charts/healthscore_v4/index.html` — `xray.score_chart_v4`

## Qué se ha hecho

- Copiadas de F1c las tres piezas verificadas por `sha256`
  (`data/fx_extra/imf_er_xdc_eur_monthly.csv` = `db424ef4…`, `xray/fx_rates_extra.py`,
  `reports/fx_risk/rates_extra.parquet`).
- Enganchada la serie FMI **dentro de `xray/fx_risk.py`** (clase `ExtraRateSeries` y
  `load_extra_rate_series`), tipo `fin_de_periodo`, con la regla point-in-time declarada:
  para una fecha objetivo dentro del corte `m`, la observación con `available_at <= cierre de m`
  más cercana a esa fecha. Nunca se lee una fila publicada después del corte.
- Prioridad de valoración: (a) `amount_eur` nativo `reported`; (b) tipo BCE del día de emisión;
  (c) tipo FMI según la regla; (d) sin valorar. `beta_fx` sigue en **0,05** y la fórmula no se tocó.
- El desfase (meses) se publica como columna en el parquet (`lag_fmi_emision_*`,
  `lag_fmi_corte_*`) y en el censo.
- Corregido el cero engañoso de la gráfica: las divisas sin tipo muestran **"sin dato"**,
  visualmente distinto de un `0,00 €` medido (verificado en el navegador contra el SVG/la tabla).

## Cifras medidas (corte 2026-08, población evaluable v4 = 957 empresas)

### 1. Empresas evaluables en rama de INTERVALO

| | Antes | Después |
| --- | ---: | ---: |
| Empresas evaluables en intervalo | **13** | **5** |
| Pasan a valor PUNTUAL | — | **8** |
| Siguen en intervalo | — | **5** |

- Pasan a puntual: `COMP_0313, COMP_0323, COMP_0469, COMP_0629, COMP_0641, COMP_0666, COMP_0959, COMP_1244`.
- Siguen en intervalo (aún tienen AED/MAD/MZN/NAD): `COMP_0365, COMP_0611, COMP_0658, COMP_1046, COMP_1143`.

### 2. Facturas y dinero que entran en la medición

| | Antes | Después | Δ |
| --- | ---: | ---: | ---: |
| Facturas valoradas al corte | 102.423 | 104.520 | **+2.097** |
| Importe vivo valorado al corte | 1.697,10 M € | 1.706,24 M € | **+9.142.149,44 €** |
| Facturas rescatadas con tipo FMI de emisión | 0 | **2.093** | +2.093 |
| EUR rescatados con FMI (emisión) | 0 | **9.060.885,79 €** | +9.060.885,79 € |

El dinero nuevo en la medición (9.142.149,44 €) es exactamente la suma del importe vivo
al corte de ARS + COP + CLP + PEN.

### 3. Pérdida ya incurrida en las cuatro divisas (antes invisible)

| Divisa | Facturas | EUR emisión | EUR corte | Pérdida EUR | % |
| --- | ---: | ---: | ---: | ---: | ---: |
| ARS | 552 | 2.423.313,42 | 2.272.113,60 | **+151.199,82** | +6,24 % |
| COP | 589 | 2.999.192,13 | 3.092.070,19 | **−92.878,06** | −3,10 % |
| CLP | 523 | 3.531.608,21 | 3.552.958,85 | **−21.350,64** | −0,60 % |
| PEN | 433 | 219.603,50 | 225.006,80 | **−5.403,30** | −2,46 % |
| **Neto** | 2.097 | | | **+31.567,82** | |

- Pérdida **bruta** de las cuatro: **+151.199,82 €** (toda en ARS).
- Ganancia **bruta** de las cuatro: **−119.632,00 €** (COP, CLP, PEN).
- Efecto sobre la pérdida neta del corte: **62.986,69 € → 94.554,50 € (+31.567,81 €)**.
- El signo positivo sigue siendo pérdida (`perdida_eur = eur_emision − eur_corte`).

### 4. Empresas que cambian de nota respecto del estado actual

| Ámbito | Empresas que cambian | Empresa-mes | Materiales (≥1 pt) | max \|dH\| | Σ(dH) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Corte 2026-08 | **8** | 8 | 1 | −1,464 pt | −4,670 pt |
| Panel completo | **8** | 74 | 2 (10 e-m) | −1,594 pt | −36,673 pt |

- Todas las 8 son las que salen de la rama de intervalo; **ninguna** cambia al alza.
- Mayor cambio: `COMP_1244` 77,293 → 75,829 (−1,464 pt).
- La materialidad global de la gráfica pasa de 36 a **38** empresas; intactas siguen **822**.

### 5. Desfase de los tipos FMI aplicados (meses)

| Objetivo | Aplicaciones | Mediano | p90 | Máx | Distribución |
| --- | ---: | ---: | ---: | ---: | --- |
| Emisión (corte) | 2.093 | 1 | 2 | 4 | 0:1044, 1:621, 2:227, 3:71, 4:130 |
| Corte (corte) | 2.097 | 2 | 4 | 4 | 2:1508, 4:589 |
| Emisión (panel) | 37.017 | 1 | 3 | 4 | 0:12455, 1:14174, 2:6322, 3:1757, 4:2309 |
| Corte (panel) | 37.065 | 2 | 4 | 4 | 2:23994, 4:13071 |

El desfase del corte (2 meses; 4 en COP) es el retardo de publicación del IFS, no un defecto:
usar un tipo de hace dos meses es correcto, lo prohibido es usar uno con `available_at > corte`.

## Verificación de signo (COMP_0029 / COMP_0521)

Contra el parquet y contra lo que pinta la gráfica:

| Empresa | `perdida_eur` (parquet, corte) | Lado en la gráfica | Comprobación |
| --- | ---: | --- | --- |
| **COMP_0029** | **+703.570,52** | **Pérdida** (barra roja izquierda, fila 1 del ranking, tarjeta "Mayor pérdida ya incurrida") | correcto |
| **COMP_0521** | **−779.927,77** | **Ganancia** (barra azul derecha, tarjeta "Mayor ganancia (ojo al signo)") | correcto |

La nota de la propia gráfica lo declara: *"El mayor movimiento absoluto del corte 2026-08-01 es
COMP_0521 con 779.927,77 EUR de GANANCIA, no de pérdida"*. **La convención está bien; no había
que invertirla.**

## Revalidación obligatoria

### Estabilidad C1 (|dH_final| mes a mes, misma población 10.022 pares)

| | n pares | p50 | p75 | p90 |
| --- | ---: | ---: | ---: | ---: |
| Antes | 10.022 | 2,352 | **6,979** | 16,083 |
| Después | 10.022 | 2,352 | **6,979** | 16,083 |

**`p75 = 6,979 < 10`: CUMPLE**, idéntico antes y después.

### Invariancia (empresas sin exposición no-EUR)

- 1.092 empresas que **nunca** tienen exposición no-EUR: 26.208 pares, **0** con diferencia
  (máx `2,8e-14`, el no-determinismo preexistente de DuckDB, dentro de la tolerancia declarada `1e-9`).
- Población evaluable: **957 empresas antes y después** (sin cambio).

## Criterios de hecho

1. Test ancla FMI (`tests/test_fx_risk.py`): factura ARS emitida 2026-04-20 valorada con los tipos
   reales del parquet (emisión 2026-04, corte 2026-06) y la fila de 2026-07 (publicada 2026-09-30)
   **no** se usa en el corte de 2026-08. ✅
2. Divisas sin tipo (AED, MAD, MZN, NAD) muestran "sin dato", no `0,00 €`; verificado en el
   HTML renderizado en Safari (columna "Cobertura de tipo" = `sin tipo (ni BCE ni FMI)` y celda de
   pérdida en cursiva "sin dato"). ✅
3. Estabilidad p75 = 6,979 < 10. ✅
4. Sin fallos/errores nuevos: `tests/test_fx_risk.py` 25/25 y `tests/test_score_chart_v4.py` 42/42. ✅

## Ficheros tocados

- `xray/fx_risk.py`, `xray/score_chart_v4.py`
- `xray/fx_rates_extra.py`, `data/fx_extra/*`, `reports/fx_risk/rates_extra.parquet` (copias F1c)
- `tests/test_fx_risk.py`
- `reports/fx_risk/{fx_risk_monthly.parquet,contrafactual.json,INFORME.md}`
- `reports/score_v4/{assessments.parquet,summary.json,exclusiones.json}`
- `reports/score_charts/healthscore_v4/index.html`

## Nota de reproducibilidad

El pipeline tiene no-determinismo preexistente (DuckDB, threads=4) de ~1e-14 en dobles: dos
ejecuciones de `xray.fx_risk` producen `health_score` idéntico salvo ese ruido. La cadena
commiteada (parquet → assessments → HTML) está generada en secuencia sobre el mismo parquet.
