# F2 · Exposicion en divisa de la cartera viva de COBROS (empresa-mes)

Capa F2 del `docs/SPEC_FX_RISK_INDEX.md`. Construye el LADO EMPRESA: que parte de lo que a cada empresa le deben esta en cada divisa, mes a mes, point-in-time. NO calcula inestabilidad ni toca tipos de cambio (F1); NO une con el indice ni toca la nota (F3).

## Universo y criterio

- flow_side='inflow', document_type_norm='invoice', status_norm<>'cancel', issuance_date_ok <= fin de m (cartera viva de cobros: incluye lo aun no vencido) y NOT (status_norm='paid' AND payment_date_ok <= fin de m). payment_date_ok solo es fecha REAL si status_norm='paid'; en otro estado es fecha PREVISTA y se ignora (criterio status-aware).
- Importe vivo: amount (nominal emitido) y NO pending_amount: pending_amount es el saldo TERMINAL del snapshot (=0 si la factura ya esta pagada) y usarlo en cortes pasados pondria a cero la exposicion de todo lo cobrado despues del corte (fuga). Medido: las 7.016 facturas terminal-paid vivas en el ultimo corte tienen pending_amount=0. amount es cota superior si hubo cobro parcial.
- Valoracion EUR: `amount_eur` de la propia factura (identidad si EUR, conversion reportada si no). Sin tipos estimados.
- Confidence: ninguna=sin cartera viva de cobros en el corte; baja=facturas no valorables >=50% de la cartera (en numero); media=algun hueco de valoracion, o cartera valorable fina (<3 facturas o <2000 EUR), o divisa desconocida; alta=sin huecos y cartera gruesa

## Reparto en el ultimo corte cerrado (2026-08-01)

- Empresas con cartera viva de cobros: 667
- Facturas vivas: 104577
- **EUR**: 90033 facturas · 1,245,924,584.51 EUR
- **No-EUR valorable**: 1534 facturas · 42,696,674.96 EUR (17 divisas: AED, BRL, CAD, CHF, CLP, COP, CZK, DKK, GBP, HUF, JPY, MXN, NOK, PEN, PLN, SGD, USD)
- **No valorable** (sin EUR conocido): 13010 facturas · 24 divisas: AED, ARS, AUD, BRL, CAD, CHF, CLP, COP, DKK, GBP, HKD, INR, ISK, JPY, MAD, MXN, MZN, NAD, NZD, PEN, SEK, SGD, USD, ZAR
- Divisa desconocida: 0 facturas

- No-EUR total: 14544 facturas, de las que solo el 10.5% son valorables en EUR (1534 de 14544)
- Emitidas y aun no vencidas en el corte (incluidas por el filtro de emision): 16460 facturas

El importe no valorable NO se suma a la exposicion en EUR ni se imputa como cero: se publica con su propio conteo y sus propias divisas.

## Cruce con la poblacion evaluable de v4 (957)

- Evaluables con cartera viva en el corte (2026-08-01): 614
- Evaluables con **exposicion no-EUR**: 76
- Evaluables con **exposicion no valorable**: 54
- Alguna vez (algun corte): no-EUR 91, no valorable 61

## Lectura honesta de viabilidad

- La ponderacion por dinero es viable para la parte EUR y no-EUR valorable, pero queda coja para la parte no valorable: no se puede expresar en EUR sin inventar tipos (prohibido). Por eso la no valorable se publica como magnitud aparte y la `confidence` la degrada.
- El importe vivo usa `amount` (nominal emitido) porque `pending_amount` es terminal y pondria a cero lo cobrado despues del corte.

- Limitacion residual: La fuente no tiene fecha de importacion: no puede demostrarse que una factura estuviera REGISTRADA en el sistema en el corte m, solo que su emision era anterior a m. Medida point-in-time por emision, no por disponibilidad de registro certificada.
