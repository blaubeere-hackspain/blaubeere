# Panel diario point-in-time de flujo operativo neto y saldo

Generado por `xray/daily_flows.py`. Este modulo construye el sustrato diario; NO construye el detector de cambio de regimen.

## Decision de grano temporal: fecha de apunte (`date`)

> El proposito de este panel es ser el sustrato de un detector de cambio de regimen cuya ANTICIPACION se medira contra el giro de la nota mensual v4. Si la serie diaria usara fecha valor y la nota usa fecha de apunte, estariamos midiendo ambas con relojes distintos, y cualquier adelanto detectado podria ser un artefacto del desfase apunte-valor en lugar de una senal real. La coherencia de reloj con v4 pesa aqui mas que la correccion economica de la fecha valor.

Verificado sobre el workspace activo: `month == date_trunc(date)` en el 100% de las filas, `max(date) = 2026-09-01` y todo el mes abierto tiene `date = 2026-09-01`. Se excluye el mes abierto por `is_open_month`; nada mas se excluye del flujo.

## Perimetro y reconciliacion con la capa mensual

- Perimetro: `is_booked`, `NOT is_open_month`, meses 2024-09..2026-08, `product_source='banking'` y `product_type in ['checking', 'wallet', 'saving', 'tpv']` (identico a `xray/cash_backfill.py`).
- Filas empresa-dia: 637471 sobre 1286 empresas.
- Reconciliacion contra `reports/cash_backfill/cash_backfill_monthly.parquet`: discrepancia maxima del flujo neto **4.000e-06 EUR** sobre 32150 empresa-mes. Reconciliacion correcta: la discrepancia maxima es 4.000e-06 EUR (redondeo a 6 decimales de la capa mensual), por debajo del umbral 1e-03. Confirma que el perimetro, el signo y el grano temporal (fecha de apunte) del panel diario reproducen exactamente `cash_backfill_monthly`.
- Identidad del ancla: discrepancia maxima `saldo_reversa(T) - saldo_ancla` = 0.0 EUR sobre 1268 empresas con ancla.

## Censo de densidad

Dias con movimiento por empresa (universo / evaluables v4):

- Universo: n=1278 min=4.0 p10=47.0 p25=98.0 p50=190.0 p75=345.0 p90=490.3 max=730.0
- Evaluables v4: n=957 min=5.0 p10=43.0 p25=94.0 p50=194.0 p75=342.0 p90=469.0 max=730.0

Huecos (rachas de dias consecutivos sin movimiento):

- Rachas (universo): n=96597 min=1.0 p10=1.0 p25=1.0 p50=2.0 p75=3.0 p90=6.0 max=383.0
- Hueco maximo por empresa (universo): n=1278 min=0.0 p10=3.0 p25=4.0 p50=10.0 p75=21.0 p90=35.0 max=383.0
- Rachas >= 30 dias: 597; >= 90 dias: 53; >= 180 dias: 11.

### Criterio de suficiencia declarado

**Ventana de 30 dias.** span >= 30 dias Y ninguna ventana movil de 30 dias vacia (hueco_maximo <= 29) Y mediana de dias con movimiento por ventana >= 5 (actividad al menos semanal, ceil(30/7)).

- Empresas del universo que la soportan: **1017**.
- Empresas EVALUABLES v4 que la soportan: **732** de 957 (76.49%).

Sensibilidad del umbral (empresas del universo / evaluables v4):

- `novacio` (>= 1 dias con movimiento): 1068 / 773
- `mediana_ge_W/14` (>= 3 dias con movimiento): 1061 / 768
- `mediana_ge_W/10` (>= 3 dias con movimiento): 1061 / 768
- `mediana_ge_W/7` (>= 5 dias con movimiento): 1017 / 732
- `mediana_ge_W/5` (>= 6 dias con movimiento): 984 / 707

**Ventana de 90 dias.** span >= 90 dias Y ninguna ventana movil de 90 dias vacia (hueco_maximo <= 89) Y mediana de dias con movimiento por ventana >= 13 (actividad al menos semanal, ceil(90/7)).

- Empresas del universo que la soportan: **1087**.
- Empresas EVALUABLES v4 que la soportan: **790** de 957 (82.55%).

Sensibilidad del umbral (empresas del universo / evaluables v4):

- `novacio` (>= 1 dias con movimiento): 1235 / 918
- `mediana_ge_W/14` (>= 7 dias con movimiento): 1176 / 863
- `mediana_ge_W/10` (>= 9 dias con movimiento): 1148 / 841
- `mediana_ge_W/7` (>= 13 dias con movimiento): 1087 / 790
- `mediana_ge_W/5` (>= 18 dias con movimiento): 1029 / 743

### Interseccion con la poblacion evaluable de v4

`reports/score_v4/assessments.parquet` tiene 957 empresas evaluables (15116 empresa-mes). De ellas, 732 soportan la ventana de 30 dias y 790 la de 90 dias segun el criterio primario. Esa interseccion es la poblacion real del detector.

## Anomalias de fecha valor

- Regla de marcado: `fecha_valor_futura = (value_date_ok IS NULL OR value_date >= 2026-09-01)` (no se excluye ninguna fila del flujo).
- Filas marcadas en el perimetro: 457 (449 con fecha valor futura + 8 centinelas) en 78 empresas.
- Volumen marcado: 8324543.326289 EUR; neto marcado: -1351672.784213 EUR.
- Sobre TODA la tabla de transacciones (no solo el perimetro de caja): 9113 filas con `value_date >= 2026-09-01` (547 en mes cerrado + 8566 en el mes abierto) y 8 centinelas. El encargo citaba 9.105 filas con value_date >= 2026-09-01, de las que 1.093 en mes cerrado y 8.586 en el abierto; esas cifras no cuadran entre si (1.093 + 8.586 = 9.679) ni con la tabla actual. Se reportan los numeros medidos. La decision de grano (date) y la regla de marcado no dependen de este conteo.

Contaminacion material (peso sobre el neto >= 0.25): **9 empresas**, de las cuales 7 son evaluables v4: COMP_0576 (0.8688), COMP_0352 (0.832931), COMP_0413 (0.58769), COMP_0562 (0.524727), COMP_0271 (0.49351), COMP_0954 (0.324453), COMP_1161 (0.316567), COMP_0005 (0.300004), COMP_1267 (0.25069).
Contaminacion dominante (>= 0.5): 4 empresas (3 evaluables v4).
Definicion del peso: |neto_marcado| / (|neto_marcado| + |neto_no_marcado|), acotado en [0, 1]; mide cuanto del movimiento NETO absoluto de la empresa aportan las filas marcadas. El neto es lo que consumira el detector. La distribucion de peso_neto es muy concentrada cerca de cero (p75 ~ 0,04) con una cola fina: el decil superior supera 0,30. Un umbral de 0,25 separa esa cola del grueso y evita marcar como contaminada a una empresa por una sola fila irrelevante.

## COMP_0413

- Filas marcadas: 22 (8 centinelas + 14 con fecha valor futuro).
- Volumen marcado: 1743680.82 EUR = 10.15% de su volumen bruto (17184623.23 EUR).
- Neto marcado: 256846.5 EUR; peso sobre su neto total (437044.13 EUR) = 58.77%.
- Las filas centinela (value_date = 2099-12-31) son pares wash exactos (neto 0) y solo inflan el VOLUMEN bruto; las filas con value_date futuro son direccionales y se comen la mayor parte del NETO. El neto es lo que consumira el detector, luego esta empresa esta materialmente contaminada en su neto aunque su volumen marcado sea solo ~10% del bruto.

## Lectura honesta

La interseccion con la poblacion evaluable de v4 es la cifra que decide el alcance: 732 de 957 empresas evaluables (76.49%) soportan la ventana de 30 dias y 790 (82.55%) la de 90. La densidad NO es homogenea: la mediana de dias con movimiento por empresa evaluable es 194.0 y el decil superior de hueco maximo supera los 48.0 dias, de modo que hay una cola de empresas esporadicas en las que una ventana de 30 dias puede contener un unico movimiento. El criterio primario (ninguna ventana vacia y actividad al menos semanal) y su tabla de sensibilidad delimitan esa cola: relajar el umbral a una sola transaccion por ventana sube la interseccion de 732 a 773 empresas, pero ahi el 'cambio de regimen' seria el artefacto de una sola transaccion.

En resumen: para la mayoria de las evaluables hay senal diaria suficiente; para una minoria esporadica (huecos de decenas de dias, 597 rachas de 30+ dias y 53 de 90+ en el universo) el detector deberia excluirse o tratarse con ventanas mas largas. Las 7 empresas evaluables con anomalias que explican >= 0.25 de su neto son una nota al pie (de las cuales 3 superan el 50%), no un riesgo sistemico; la decision sobre ellas queda en manos del coordinador.

## Fuentes (sha256)

- `data/clean/balances.parquet`: `86591d78768df467ebc053ebaa4b6ab9a42540bcc58d82e39b264ba1c760ebd3`
- `data/clean/companies.parquet`: `d3eec399514a09b112e5bc02afb9772b08e6ab04c00c55f128bfe4b2ce85a487`
- `data/clean/transactions.parquet`: `5a88e7f43978849bb514b80c2111b547ea44037a53484b10c4bcd7c36b9668b7`
- `data/interim/tx_flow_class.parquet`: `e847a46f8415a900e266496069d42c69a3e99b6905ac61b840984d924b907aed`
- `reports/cash_backfill/cash_backfill_monthly.parquet`: `70db83e48a89e505fe946d98394a3c4b2aba40ad57fd5a387c5e2fef8061c4fb`
- `reports/score_v4/assessments.parquet`: `9177fbf443f7aadfe696422ccec90a7f17e89f7c1192af68d134265409218c79`
