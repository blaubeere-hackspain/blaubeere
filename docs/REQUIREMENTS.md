# Requirements and coordination

Scope: [PRODUCT.md](PRODUCT.md). Owners are implementation workstreams, not customer teams. **P0 = MVP; P1 = bonus.**

## P0 checklist

| ID | Owner | Done when |
|---|---|---|
| R1 | Data | Company/group joins preserve currencies, amounts, dates, settlements, and source history. No duplicate cash flows; internal transfers are excluded from operating revenue. |
| R2 | Data | Dated inputs cover usable cash, receivables, payables, debt, other commitments, and accessible committed funding. Missing/stale inputs are explicit; later information cannot enter earlier assessments. |
| R3 | Model | Dated cash projections cover the selected goal horizon, including 30/60/90-day checkpoints, minimum cash, first shortfall, and funding needed above a stated buffer. Contractual timing and behavioural estimates are distinguished. |
| R4 | Model | The challenge-required score recognises health, improvement, and deterioration with numerical drivers; isolated shocks and persistent changes are distinguished. Missing evidence is not automatically poor health. |
| R5 | Model + App | Finance teams enter business assumptions; historical data supplies defaults where justified. Each metric has a definition, unit, period, and source. Unsupported metrics require inputs; they are not invented from bank data. |
| R6 | Model + App | Users select one financial or business target, deadline, adjustable drivers, bounds, and constraints. The planner compares suggested combinations by target attainment, cash impact, and trade-offs; it reports unmet gaps if no tested scenario qualifies. |
| R7 | App | Cash pressure leads the view, with health as context; users can inspect causes, set goals, and compare baseline versus plans. Source records remain unchanged. Backend authorisation restricts company access. |
| R8 | Model | Confirm official submission format/unit. Validate on held-out business groups and forward dates without leakage; report improvement and deterioration separately. |
| R9 | App + Model | Runnable submission and five-minute demo show a cash gap, explanation, historical change, goal, and suggested plan with visible assumptions. |

**P1:** automatic material-change alerts, including recoveries; measured warning lead time alongside misses and false alerts.

## Handoffs and build order

1. **Together:** confirm challenge target, available history, and supported goal metrics. Keep related subsidiaries together during validation.
2. **Data → Model:** company/group, cutoff, dated cash flows, balances, currency treatment, source coverage. Confirm payroll/taxes, restrictions, partial settlements, and credit conditions; missing obligations are not zero.
3. **Model → App:** assessment date/version, history mode (`as_known` / `reconstructed`), cash path, funding gap/date, score/trajectory, drivers, assumptions, coverage. Goals add metric/target/deadline/constraints; scenarios add driver changes, projected outcomes, and unmet constraints.
4. **Sequence:** historical assessment → baseline forecast → goals and bounded scenario suggestions. App starts against one shared, labelled fixture while Data/Model build. Prefer a few supported drivers and candidate plans over a general optimiser.

## Acceptance checks

- €2m cash, €4m due, no other flows/funding, zero buffer → €2m funding need. A later receipt cannot erase an earlier shortfall.
- Later information cannot change an `as_known` assessment using the same model version. Final-only balances do not establish historical knowledge.
- A plan reaching its final target but breaching the cash floor earlier fails that constraint. No qualifying candidate produces a remaining-gap explanation, not a claim that every possible plan is impossible.
- Growth/churn relationships are explicit and not double-counted. Suggested changes are conditional assumptions; the tool does not invent causal effects such as spending cuts improving retention.
- Goals and scenarios preserve the baseline and current score. Cross-company unauthorised access fails.
