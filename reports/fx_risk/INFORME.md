# F3 · Riesgo de divisa: valoracion por fecha de emision, indice FX y contrafactual medido

Capa F3 del `docs/SPEC_FX_RISK_INDEX.md`. Une los tipos del BCE (F1), el indice por divisa-mes (F1b) y la exposicion empresa-mes (F2), y **mide** que le haria a la nota aplicar un factor de riesgo de divisa. NO se toca la formula de `xray/scoring_v4.py`: el efecto se simula fuera del motor (camino B, capa + contrafactual medido).

## Resumen ejecutivo

- Facturas no-EUR vivas en el corte 2026-08-01: 14544.
- Facturas rescatadas valorandolas con el tipo BCE del **dia de emision**: **10867** (de 13010 sin EUR propio), por 408,667,544.75 EUR. La segunda fuente (FMI/IFS `XDC_EUR`, F1c ya enchufada) rescata ademas **2093** facturas por 9,060,885.79 EUR, y cierra la valoracion de ARS, COP, CLP y PEN.
- Perdida ya incurrida por movimiento de divisa (emision frente a corte): **94,554.50 EUR NETOS**, con 3,658,787.05 EUR de perdida bruta y 3,564,232.55 EUR de ganancia bruta, sobre 14487 facturas.
- Evaluables de v4 con exposicion no-EUR **medida** en el ultimo corte: 111 de 614 con cartera viva; con exposicion no-EUR **alguna vez**: 139. El rescate por tipo de emision (BCE + FMI) amplia la poblacion afectada, no la inventa.
- Poblacion evaluable v4: 957 empresas. Con exposicion no-EUR **alguna vez**: 139 (en toda la rejilla de empresas: 181); en el ultimo corte: 111. **Intactas: 818 de 957** (sin exposicion no-EUR en ningun corte).
- Spearman del castigo FX contra la penalizacion por mora: **0.109**; contra la penalizacion por multiplicador de deuda: **0.075**.

## 1. Valoracion por fecha de emision (tipos del BCE)

Instruccion del usuario: *"busca segun la fecha de factura cual era el tipo de cambio aquel dia"*. Cada factura viva en divisa cubierta por el BCE se valora en EUR con el tipo del dia de emision (`issuance_date_ok`). Si ese dia no cotiza (fin de semana, festivo) se usa el **ultimo dia habil ANTERIOR** con cotizacion. Nunca se interpola hacia delante ni se usa un tipo posterior a la fecha.

Prioridad de valoracion (declarada):

1. `amount_eur` nativo cuando `amount_eur_source='reported'` y la conversion no es ambigua (el tipo que declara la factura manda).
2. Tipo BCE del dia de emision (ultimo habil anterior si no cotiza).
3. Tipo FMI/IFS `XDC_EUR` (`fin_de_periodo`) **point-in-time**: la observacion con `available_at <= cierre del corte` mas cercana a la fecha objetivo. Solo entra cuando el BCE no cubre la divisa (ARS, COP, CLP, PEN); nunca una fila publicada despues del corte.
4. Sin valorar (nunca se imputa como cero).

| Metrica | Valor |
| --- | ---: |
| Facturas no-EUR vivas en el corte | 14544 |
| Sin EUR propio (a rescatar) | 13010 |
| **Rescatadas con tipo BCE de emision** | **10867** |
| **Rescatadas con tipo FMI de emision** | **2093** |
| EUR rescatados con BCE | 408,667,544.75 |
| EUR rescatados con FMI | 9,060,885.79 |
| Sin EUR propio y divisa opaca (no rescatables) | 50 |

### Desfase point-in-time de los tipos FMI aplicados

El IFS es mensual y publica con retardo; el desfase (meses entre la fecha objetivo y el mes del tipo usado) se mide y se publica, no se esconde. Es correcto usar un tipo de hace dos meses; lo prohibido es usar uno con `available_at` posterior al corte.

| Objetivo | Facturas | Desfase mediano | Desfase p90 | Desfase max |
| --- | ---: | ---: | ---: | ---: |
| emision | 2093 | 1.00 | 2.00 | 4 |
| corte | 2097 | 2.00 | 4.00 | 4 |

## 2. Valoracion a fecha de corte y perdida ya incurrida

La misma factura se valora con el tipo al **cierre del mes de corte**: el del BCE si lo hay y, si no, el FMI point-in-time. La diferencia `perdida_eur = eur_emision - eur_corte` es dinero ya perdido (o ganado) por movimiento de divisa, no riesgo teorico. Ambas fechas son <= corte, por lo que ambas son legitimas point-in-time.

Perdida neta en el corte 2026-08-01: **94,554.50 EUR**. Por divisa (mayor efecto absoluto; positivo = perdida, negativo = ganancia):

| Divisa | Facturas | EUR emision | EUR corte | Perdida EUR | % |
| --- | ---: | ---: | ---: | ---: | ---: |
| USD | 6220 | 263,632,084.08 | 262,564,152.26 | 1,067,931.83 | 0.4% |
| BRL | 702 | 13,645,389.50 | 14,080,448.60 | -435,059.10 | -3.2% |
| MXN | 441 | 5,722,643.19 | 6,067,209.86 | -344,566.68 | -6.0% |
| AUD | 448 | 4,587,574.86 | 4,793,678.41 | -206,103.55 | -4.5% |
| ARS | 552 | 2,423,313.42 | 2,272,113.60 | 151,199.82 | 6.2% |
| GBP | 3212 | 153,031,750.30 | 153,138,549.03 | -106,798.73 | -0.1% |
| COP | 589 | 2,999,192.13 | 3,092,070.19 | -92,878.06 | -3.1% |
| INR | 5 | 396,693.21 | 329,845.84 | 66,847.37 | 16.9% |
| CAD | 681 | 5,142,034.86 | 5,115,594.55 | 26,440.30 | 0.5% |
| CLP | 523 | 3,531,608.21 | 3,552,958.85 | -21,350.64 | -0.6% |
| NOK | 7 | 1,747,820.94 | 1,755,861.26 | -8,040.32 | -0.5% |
| HUF | 3 | 5,968.85 | 67.35 | 5,901.50 | 98.9% |
| PEN | 433 | 219,603.50 | 225,006.80 | -5,403.30 | -2.5% |
| SGD | 127 | 1,606,822.80 | 1,610,847.84 | -4,025.04 | -0.2% |
| ISK | 4 | 49,025.57 | 50,065.80 | -1,040.23 | -2.1% |

## 3. Indice FX empresa-mes ponderado por dinero

Ponderacion por **dinero** (valor EUR de la cartera viva), no por numero de facturas. Escala declarada de severidad por tramo combinado de F1b: `estable=0.0`, `volatil=0.5`, `hipervolatil=1.0`. El indice vive en [0, 1]. Se publican por separado la contribucion por **volatilidad** (`indice_fx_vol`) y por **deriva** (`indice_fx_deriva`), y el combinado (`indice_fx`).

Columnas del parquet `reports/fx_risk/fx_risk_monthly.parquet`: `indice_fx`, `indice_fx_vol`, `indice_fx_deriva`, sus intervalos `[min, max]` para empresas con mezcla de divisas opacas, `pct_dinero_inestable`, `perdida_eur`, `importe_vivo_eur_corte`, `confidence` y `reasons`.

## 4. Divisas opacas residuales (sin cobertura BCE ni FMI)

Una divisa sin tipo BCE **ni FMI** no se puede pasar a EUR y **no se imputa**. Desde F6, ARS, COP, CLP y PEN ya NO estan aqui: las cubre la segunda fuente (FMI/IFS `XDC_EUR`, F1c) con regla point-in-time. El agujero residual es AED, MAD, MZN y NAD. Convencion (`nulo nunca es cero`, igual que `payment_delay_v2`):

- Empresa con **solo** divisas opacas: proporcion de dinero inestable = **100% por construccion**, exacta, sin necesidad de tipo. Valor puntual 1.0.
- Empresa con **mezcla** de opacas y valorables: no se puede cerrar la proporcion. `indice_fx` = NULL y se publica `[indice_fx_min, indice_fx_max]` = [caso opaco despreciable, caso opaco dominante].

Recuento en el corte 2026-08-01 sobre las 667 empresas con cartera viva: **0 con solo opacas** y **7 con mezcla** (AED, MAD, MZN y NAD).

La segunda fuente se engancha DENTRO de `xray/fx_risk.py` (`--extra-rates`, por defecto `reports/fx_risk/rates_extra.parquet`; `--sin-extra` reproduce el estado anterior). Con tipos para ARS/COP/CLP/PEN, las empresas de mezcla que solo tenian esas divisas cierran su indice puntual y dejan de caer en la rama de intervalo.

## 5. Contrafactual: que le haria a la nota

Simulacion fuera del motor: `H_fx = H_final * (1 - beta_fx * indice_fx)`. Poblacion: filas con `health_score` no nulo y `excluida=false` de `reports/score_v4/assessments.parquet` (957 empresas, 15116 empresa-mes).

| beta_fx | empresa-mes con cambio | empresa-mes materiales | empresas con cambio | empresas materiales | p50 \|dH\| | p90 \|dH\| |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.05 | 1207 | 254 | 135 | 41 | 0.113 | 1.657 |
| 0.1 | 1207 | 400 | 135 | 59 | 0.226 | 3.315 |
| 0.25 | 1207 | 554 | 135 | 76 | 0.565 | 8.287 |

Materialidad: umbral declarado de 1.0 puntos. 1 punto sobre la escala 0-100 es el 1% de la nota y esta por encima de la mediana de las penalizaciones existentes (medido en la poblacion evaluable: 0,34 puntos de mora; 0,78 de multiplicador de deuda). Se publica ademas cuantas empresas-mes cambian con cualquier |dH|>0.

### Ortogonalidad: correlacion de Spearman

Esta es la cifra que decide si el factor es senal nueva o un cuarto castigo sobre la misma empresa:

- Castigo FX (puntos) vs penalizacion por **mora**: **0.109**.
- Castigo FX (puntos) vs penalizacion por **multiplicador de deuda**: **0.075**.
- `indice_fx` vs `mora_indice`: 0.157.
- `indice_fx` vs `multiplicador_deuda`: -0.152.

Pares usados: 8978 (mora), 8978 (multiplicador).

### Desglose volatilidad vs deriva

| beta_fx | efecto total puntos | efecto volatilidad | efecto deriva | % volatilidad |
| ---: | ---: | ---: | ---: | ---: |
| 0.05 | 634.81 | 559.81 | 468.69 | 54.4% |
| 0.1 | 1,269.62 | 1,119.63 | 937.39 | 54.4% |
| 0.25 | 3,174.06 | 2,799.06 | 2,343.47 | 54.4% |

Las cifras de la tabla son |dH| en puntos sobre las empresa-mes que **cambian** (excluye las de indice 0, que son mayoria): p50, p90 y el maximo. El efecto total es la suma de puntos de nota perdidos en toda la poblacion.

### Empresas mas castigadas (max |dH| en el escenario mayor)

| Empresa | max |dH| puntos |
| --- | ---: |
| COMP_0658 | 24.348 |
| COMP_0359 | 22.843 |
| COMP_0666 | 22.307 |
| COMP_0384 | 21.697 |
| COMP_0287 | 20.717 |
| COMP_1244 | 19.701 |
| COMP_0525 | 17.031 |
| COMP_0029 | 16.936 |
| COMP_0341 | 16.706 |
| COMP_0614 | 15.477 |

## 6. Recomendacion honesta

Con `beta_fx=0.25`, el factor mueve a 135 empresas (76 de forma material) de las 957; 818 quedan intactas. Solo 139 empresas evaluables tienen exposicion no-EUR en algun corte (111 en el ultimo). En terminos de ortogonalidad, las correlaciones son BAJAS (<0.20): el castigo FX no repite el de mora ni el de deuda, es senal nueva.

Lectura de producto:

- La ortogonalidad es el mejor argumento a favor del factor: las correlaciones medidas son muy bajas, de modo que NO es un cuarto castigo sobre la misma empresa que ya castigan mora y deuda.
- En contra: el factor solo alcanza a una minoria de la poblacion (139 de 957) y la perdida ya incurrida NETA es practicamente nula (94,554.50 EUR) porque las ganancias por divisa compensan las perdidas. La volatilidad pesa algo mas que la deriva (54.4% del efecto bruto), y la deriva pasada no predice la futura.
- Recomendacion: SI merece explorarse como capa, pero como factor ESPECIFICO de inestabilidad con beta PEQUENO (0.05-0.10) y acotado, no como penalizacion universal. El indice combinado (mas severo de volatilidad y deriva) es defendible; la deriva por si sola, no. El agujero de ARS/COP/CLP/PEN ya esta cerrado (F1c enchufado en F6): esas divisas, las mas inestables de la cartera, ya no caen en la rama de intervalo y su perdida ya incurrida es visible. El agujero residual es AED/MAD/MZN/NAD, fuera de la cobertura BCE y FMI.

## 7. Limitaciones declaradas

- El agujero residual de las divisas opacas (AED, MAD, MZN, NAD): el BCE no las publica y el FMI/IFS tampoco, asi que no se imputa un tipo. Una empresa con solo opacas publica 100% inestable por construccion; una con mezcla publica un intervalo con el valor puntual a NULL. ARS, COP, CLP y PEN ya NO son opacas: las cubre la segunda fuente (FMI/IFS XDC_EUR) con regla point-in-time. La deriva PASADA no predice la deriva FUTURA: el componente de deriva mide erosion ya ocurrida en 12 meses, no una expectativa. El corte del 6% de deriva es una decision de politica, no un hueco natural de la distribucion (lo dice el informe de F1b): la distribucion de deriva es un continuo y el corte se eligio para separar la cola de depreciacion gestionada del bloque leve. La fuente no tiene fecha de importacion: la cartera viva se reconstruye por emision y pago, no por disponibilidad de registro certificada. El FMI/IFS publica con ~2 meses de retardo (4 en COP), asi que valorar a una fecha reciente usa un tipo mas antiguo; el desfase se publica en meses, no se esconde.
- La valoracion usa `amount` (nominal emitido) y no `pending_amount` (saldo terminal del snapshot): usarlo pondria a cero lo ya cobrado en cortes pasados (fuga). Es cota superior si hubo cobro parcial.
- En facturas con conversion propia (`reported`), la perdida mezcla el tipo declarado por la factura con el tipo BCE del corte; se declara.
- Calidad de fuente: 3 facturas HUF declaran una conversion propia con un `exchange_rate` ~100x menor que el del BCE, lo que infla su perdida declarada (5.9k EUR de los 94,554.50 EUR netos del corte). Se respeta la prioridad declarada (manda la factura) pero se senala el dato anomalo.

## Artefactos

- `reports/fx_risk/fx_risk_monthly.parquet` — empresa-mes con valoraciones, perdida, indice, intervalos y desfase FMI en meses.
- `reports/fx_risk/contrafactual.json` — todos los escenarios, distribuciones, Spearman y desglose.
- `reports/fx_risk/INFORME.md` — este informe.
