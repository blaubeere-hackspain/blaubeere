# Trayectoria operativa v2 — evaluación y exportación

**anticipation_not_validated / descriptive_review_only**

Anticipación no validada: baja detección y alta proporción de falsas alertas. Solo revisión descriptiva; alertas predictivas automáticas desactivadas.

## Resultado real sellado

No se han vuelto a ejecutar desarrollo ni reserva, ni detectado eventos en el exportador. Se reutilizan los dos resultados sellados y sus intervalos, sin ajustar reglas ni seleccionar ejemplos por éxito. Menos alertas no demuestra superioridad. Los tres matches de reserva del candidato no validan anticipación utilizable.

| Fase | Signo | Método | Eventos | Alertas | Matches | Misses | Falsas | Censuradas | Recall | IC95 recall | FAS | IC95 FAS | Lead 1/2 meses | Mediana lead |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|
| development | improvement | candidate | 56 | 156 | 7 | 49 | 98 | 51 | 12.500% | [4.225%, 22.039%] | 93.333% | [87.231%, 97.872%] | 1/6 | 2.0 |
| development | improvement | baseline | 56 | 287 | 7 | 49 | 159 | 121 | 12.500% | [5.355%, 22.231%] | 95.783% | [92.262%, 98.204%] | 3/4 | 2.0 |
| development | deterioration | candidate | 61 | 158 | 3 | 58 | 106 | 49 | 4.918% | [0.000%, 9.805%] | 97.248% | [94.958%, 100.000%] | 3/0 | 1.0 |
| development | deterioration | baseline | 61 | 262 | 8 | 53 | 156 | 98 | 13.115% | [5.083%, 19.444%] | 95.122% | [91.772%, 98.319%] | 5/3 | 1.0 |
| reserved | improvement | candidate | 37 | 75 | 2 | 35 | 48 | 25 | 5.405% | [0.000%, 15.798%] | 96.000% | [89.357%, 100.000%] | 1/1 | 1.5 |
| reserved | improvement | baseline | 37 | 146 | 3 | 34 | 82 | 61 | 8.108% | [0.000%, 18.750%] | 96.471% | [91.664%, 100.000%] | 2/1 | 1.0 |
| reserved | deterioration | candidate | 41 | 73 | 1 | 40 | 50 | 22 | 2.439% | [0.000%, 8.571%] | 98.039% | [93.478%, 100.000%] | 0/1 | 2.0 |
| reserved | deterioration | baseline | 41 | 151 | 7 | 34 | 70 | 74 | 17.073% | [7.407%, 27.786%] | 90.909% | [84.415%, 96.668%] | 3/4 | 2.0 |

Recall = matches/eventos; FAS = falsas/(matches + falsas), no tasa de falsos positivos. Censuradas permanecen desconocidas: no se convierten en negativas. Lead y su mediana describen exclusivamente matches, no todas las filas ni todas las señales. IC95 percentil con 1.000 remuestreos de los 200 grupos (semilla 1729), preservando empresas y cronología y sin volver a ajustar; no es incertidumbre individual de p_proxy.

## Cobertura y límites metodológicos

Reserva temporal interna en empresas conocidas, no holdout independiente de nuevas empresas ni validación externa. 200 grupos / 1.078 empresas incluidos en evaluación; los 50 grupos final_test v1 / 208 empresas se excluyeron de eventos, matching y selección en todas las fechas. El exportador muestra sus flujos observados y probabilidades v1, pero no crea etiquetas de evento. Datos sintéticos reconstruidos; available_at no certifica known_on histórico. Clasificación, FX, actividad y ventanas incompletas limitan lo observado. No hay saldo real, planificación de caja, score de salud 0–100 ni explicación causal.

### development

Periodo sellado:
```json
{"alert_origin_first": "2025-03-01", "alert_origin_last": "2025-09-01", "outcomes_through": "2025-12-31"}
```
Cobertura agregada (sin eventos individuales de reserva):
```json
{
  "alerts_incomplete_followup": 320,
  "companies": 1078,
  "event_exclusions_no_eligible_origin": 28,
  "event_window_opportunities": 8624,
  "event_window_unknown_reasons": {
    "ambiguous_state": 3473,
    "base_trim_state_disagreement": 59,
    "invalid_base_bounds": 3836,
    "invalid_trim_bounds": 3836,
    "no_cash_activity": 3500,
    "nonpositive_or_unknown_gross": 3697,
    "unknown_fx": 448
  },
  "event_windows_events": 145,
  "event_windows_left_unknown": 0,
  "event_windows_observed": 1771,
  "event_windows_observed_nonevent": 1626,
  "event_windows_right_immature": 0,
  "event_windows_unknown": 6853,
  "excluded_groups": 50,
  "excluded_trajectory_rows": 0,
  "groups_with_companies": 200,
  "included_groups": 200,
  "input_eligible_origins": 3250,
  "input_ineligible_origins": 4296,
  "input_ineligible_reasons": {
    "invalid_base_bounds": 4295,
    "invalid_trim_bounds": 4295,
    "no_cash_activity": 4081,
    "nonpositive_or_unknown_gross": 4209,
    "unknown_fx": 317
  },
  "left_history_origins": 0,
  "origin_opportunities": 7546,
  "preperiod_crossing_episodes": 54,
  "preperiod_issued_alerts": {
    "baseline": 105,
    "candidate": 0
  }
}
```

### reserved

Periodo sellado:
```json
{"alert_origin_first": "2026-03-01", "alert_origin_last": "2026-05-01", "outcomes_through": "2026-08-31", "possible_event_onsets": ["2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"]}
```
Cobertura agregada (sin eventos individuales de reserva):
```json
{
  "alerts_incomplete_followup": 182,
  "companies": 1078,
  "event_exclusions_no_eligible_origin": 10,
  "event_window_opportunities": 4312,
  "event_window_unknown_reasons": {
    "ambiguous_state": 2502,
    "base_trim_state_disagreement": 30,
    "invalid_base_bounds": 856,
    "invalid_trim_bounds": 856,
    "no_cash_activity": 465,
    "nonpositive_or_unknown_gross": 689,
    "unknown_fx": 420
  },
  "event_windows_events": 88,
  "event_windows_left_unknown": 0,
  "event_windows_observed": 1074,
  "event_windows_observed_nonevent": 986,
  "event_windows_right_immature": 0,
  "event_windows_unknown": 3238,
  "excluded_groups": 50,
  "excluded_trajectory_rows": 0,
  "groups_with_companies": 200,
  "included_groups": 200,
  "input_eligible_origins": 2101,
  "input_ineligible_origins": 1133,
  "input_ineligible_reasons": {
    "invalid_base_bounds": 1133,
    "invalid_trim_bounds": 1133,
    "no_cash_activity": 939,
    "nonpositive_or_unknown_gross": 1046,
    "unknown_fx": 324
  },
  "left_history_origins": 0,
  "origin_opportunities": 3234,
  "preperiod_crossing_episodes": 109,
  "preperiod_issued_alerts": {
    "baseline": 1120,
    "candidate": 572
  }
}
```

## Tres casos de desarrollo, sin sustituciones

Se copia cada primer caso ordenado del JSON sellado completo, no se recalcula su selección. La confirmación es la fecha mínima de visibilidad de toda la anotación; nunca se anticipa retrospectivamente una recuperación. Las señales de vigilancia y su confirmación son hechos diferentes del evento. Estas fechas de desarrollo no tienen modelo v1 disponible: p_proxy=null, before_training_cutoff.

### deterioration: COMP_0009

Inicio observado: 2025-04-01; confirmado y visible desde: 2025-05-31; grupo: GROUP_0225.
Prueba de selección: {"eligible_origins": ["2025-03-01"], "qualifying_patterns": 61, "rank": 1, "selection_uses_alerts_or_probability": false, "sort": ["confirmed_at", "company_id", "onset_month"], "source_phase": "development"}
Matches sellados (si procede): {"baseline": null, "candidate": null}

| Mes | Estado | Dirección | Watch | Confirmación señal |
|---|---|---|---|---|
| 2025-02-01 | non_deficit | insufficient_evidence | — | — |
| 2025-03-01 | non_deficit | insufficient_evidence | — | — |
| 2025-04-01 | deficit | insufficient_evidence | — | — |
| 2025-05-01 | deficit | insufficient_evidence | — | — |

### improvement: COMP_0179

Inicio observado: 2025-04-01; confirmado y visible desde: 2025-05-31; grupo: GROUP_0017.
Prueba de selección: {"eligible_origins": ["2025-03-01"], "qualifying_patterns": 56, "rank": 1, "selection_uses_alerts_or_probability": false, "sort": ["confirmed_at", "company_id", "onset_month"], "source_phase": "development"}
Matches sellados (si procede): {"baseline": null, "candidate": null}

| Mes | Estado | Dirección | Watch | Confirmación señal |
|---|---|---|---|---|
| 2025-02-01 | deficit | deteriorating | 2025-02-28 | — |
| 2025-03-01 | deficit | deteriorating | 2025-02-28 | 2025-03-31 |
| 2025-04-01 | non_deficit | deteriorating | 2025-02-28 | 2025-03-31 |
| 2025-05-01 | non_deficit | improving | 2025-05-31 | — |

### recovered_dip: COMP_0028

Inicio observado: 2025-04-01; confirmado y visible desde: 2025-06-30; grupo: GROUP_0218.
Prueba de selección: {"eligible_origins": ["2025-03-01"], "qualifying_patterns": 42, "rank": 1, "selection_uses_alerts_or_probability": false, "sort": ["confirmed_at", "company_id", "onset_month"], "source_phase": "development"}
Matches sellados (si procede): null

| Mes | Estado | Dirección | Watch | Confirmación señal |
|---|---|---|---|---|
| 2025-02-01 | non_deficit | improving | 2025-02-28 | — |
| 2025-03-01 | non_deficit | improving | 2025-02-28 | 2025-03-31 |
| 2025-04-01 | deficit | improving | 2025-02-28 | 2025-03-31 |
| 2025-05-01 | non_deficit | improving | 2025-02-28 | 2025-03-31 |
| 2025-06-01 | non_deficit | insufficient_evidence | — | — |

COMP_0009 conserva su deterioro observado sin match candidato ni baseline: no se cambia por un caso más favorable.

## Capa de probabilidad v1 separada

Se reutiliza exclusivamente experiment-v1:hgb_leaf15, sin fit, calibración ni evaluación de targets. Estima déficit operativo en al menos dos de los tres meses naturales siguientes, no transición. El resultado AP v1 existente corresponde a otro target y no mide anticipación de estas transiciones; este resultado negativo no lo contradice. La dirección no se deduce de cambios de probabilidad. El entrenamiento/selección cierra el 31-03-2026; solo abril–agosto 2026 tienen overlay, con elegibilidad original y horizonte m+1..m+3. El modelo se entrenó físicamente en septiembre: esto es simulación retrospectiva, no emisión histórica en vivo. Agosto coincide con las 1.286 predicciones selladas en todos sus campos (tolerancia absoluta numérica fija 1e-12); 1.010 estimaciones y 276 abstenciones.

```json
{
  "absolute_numeric_tolerance": 1e-12,
  "august_matches_sealed_latest": true,
  "event_detection_calls": 0,
  "fitting_performed": false,
  "latest_predictions_sha256": "ec6b3d3f813238412420a0627dabf56e1ee7ef38111f92aadb2415d98a770cec",
  "monthly_counts": {
    "2026-04-01": {
      "companies": 1286,
      "eligible": 968,
      "estimates": 968
    },
    "2026-05-01": {
      "companies": 1286,
      "eligible": 1026,
      "estimates": 1026
    },
    "2026-06-01": {
      "companies": 1286,
      "eligible": 1032,
      "estimates": 1032
    },
    "2026-07-01": {
      "companies": 1286,
      "eligible": 1023,
      "estimates": 1023
    },
    "2026-08-01": {
      "companies": 1286,
      "eligible": 1010,
      "estimates": 1010
    }
  },
  "v1_target_queries": 0
}
```

## Contrato de entrega y revisión

1286 empresas × 24 meses = 30864 puntos, septiembre 2024–agosto 2026. companies.json es un array CommonCompany de kind operating_trajectory; manifest.json incluye el JSON Schema y el diccionario compartido de protocolo, fuentes, modelo, revisión y backtest agregado. predictive, health, opening_cash_cents y buffer_cents son null; history/flows/drivers/coverage son arrays vacíos porque la caja es desconocida. evidence.source_refs son pares [month, available_at] con empresa del objeto contenedor. La evidencia numérica conserva límites, medias aritméticas no ponderadas de ratios, periodos, deltas y contribuciones de cobros/pagos conocidos: no SHAP ni causalidad. Acciones solo para inspeccionar evidencia, clasificación, FX, cobros/pagos y comparar periodos; sin ejecución, pagos, crédito ni persistencia.

La integración Rust/UI sigue pendiente en T5. Debe conservar v1 proxy_only, filtrar puntos por fecha y ocultar todo development_demo_case antes de case_visibility_from (helper visible_development_case). El selector de demos y el backtest global son retrospectivos y deben rotularse como tales; no son conocimiento de una fecha pasada. No activar alertas automáticas ni simulación de caja.

## Reproducción segura

```sh
.venv/bin/python -B -m scripts.trajectory_backtest verify
.venv/bin/python -B -m scripts.trajectory_export
.venv/bin/python -B -m scripts.trajectory_export --check
```

Exportación exclusiva e idempotente: diferencias bloquean, nunca sobrescriben; --check no escribe. Se verifican hashes protegidos antes y después; solo se reproducen señales descriptivas e inferencia, no outcomes. No ejecutar de nuevo develop ni evaluate-reserved.
