> **Goal v3 pending — audit/design only.** The active objective is explainable monthly financial health with bidirectional anticipation; it is not yet implemented or validated. [Source audit](../reports/modeling/financial-v3/audit-summary.json) and [development proposal](../reports/modeling/financial-v3/design-proposal.md) require null headline scores when core evidence is incomplete, separate retrospective reconstruction from as-of signals, and never reward retained cash from overdue payments. No untouched confirmatory test is identified: all 50 v1 final-test groups remain excluded and the v2 reserve is consumed; new future/external evidence is required. Automatic alerts stay disabled. The v1/v2 delivery and contracts below are preserved, not replaced or reopened.

# Product

**Positioning:** Blaubeere helps a company's finance team understand its observed operating trajectory, explain improvement and deterioration, and inspect warning evidence. The existing cash-planning demo remains a separate, explicitly illustrative mode.

**Buyer/users:** CFO, treasury, and finance teams; access remains restricted to their authorised companies. No ActionDesk rebrand, action-management product or four-page redesign.

## Current evidence and approved v2 scope

V1 is complete for a synthetic operating-deficit proxy, not comprehensive financial health. Its [historical training report](../reports/modeling/training-report.md) and sealed artifacts remain unchanged. V2 now has a separate trajectory dashboard/API/MCP mode consuming the existing sealed export and measured backtest under the [exact protocol](../reports/planning/financial-scoring-plan.md#preregistro-v2). **Anticipation not validated:** reserved candidate detection is 2/37 improvement events and 1/41 deterioration events, with false-alert shares 96% and 98.039%. It supports descriptive review only; automatic predictive alerts remain disabled. Integration does not rerun training, event detection or backtest phases. Application evidence is isolated in [app-verification](../reports/modeling/trajectory-v2/app-verification/); overall goal acceptance is not asserted here.

The supplied context from the 18 September X-Ray study prioritises both directions, trajectory, numerical explanation, the buyer and a navigable demo. Anticipation was a bonus there and is now part of the approved internal v2 goal. The user reports that the official test **may be cancelled or changed**; this is communicated information, not organiser confirmation. Official target, metric and submission format remain unconfirmed. Do not claim an official 0–100 score.

## Core v2 workflow

1. **Inspect the monthly trajectory:** select company and closed-month as-of date across September 2024–August 2026. Retain every company/month, including insufficient evidence and gaps. Source is the pinned `panel_flujos` run, not reconstructed cash balances.
2. **Separate level from change:** show robust `deficit` / `non_deficit` / `insufficient_evidence` as the current operating state. Separately show `improving`, `stable`, `deteriorating` or `insufficient_evidence`, comparing three recent months with the preceding three. A deficit can improve; a non-deficit can deteriorate. Missing evidence is not bad health.
3. **Explain numerically:** show recent/prior known operating receipts and payments relative to observed gross cash volume, base/trim uncertainty bounds and arithmetic contributions (`delta receipts ratio - delta payments ratio`). Explain denominator and coverage changes. These are input arithmetic, not causal effects, SHAP or points assigned by the model.
4. **Inspect warnings:** distinguish the first provisional directional signal from an alert confirmed only at the second consecutive same-sign month-end. One alert per run; gaps, stable or insufficient evidence break the run. The one-month baseline is a comparison, never a backdated confirmed alert.
5. **Inspect the separate probability overlay:** the unchanged v1 model estimates `target_deficit_3m`, not improvement/deterioration events. Only eligible April–August 2026 month-ends, strictly after the 31 March training/selection label cutoff, may have values. Earlier dates or ineligible months remain null with reasons. This is retrospective as-of simulation, not proof that the September-trained model issued predictions live in April.

The observed perimeter is booked checking/saving/wallet flows in bounded EUR with verified conversions. Month-end availability is a retrospective assumption, not certified `known_on`. Observed trajectory, proxy probability and cash planning are distinct outputs; no stock of cash, default risk, calibrated confidence or causal diagnosis is inferred from flows alone.

## What success means

A finance user can navigate a company/month, distinguish state from direction, explain the numerical evidence and see what is missing. The demo covers improvement, deterioration and a recovered one-month dip using **development cases only**; their actual signals and missed alerts remain visible, rather than substituting successful cases. Future missing case types must be reported, not fabricated or borrowed from a reserve.

Measure both signs separately against independently defined sustained state-transition events. Candidate and one-month baseline must report events, alerts, matches, misses, false alarms, censored outcomes, event recall, false-alert share and calendar-month lead time under the [preregistered rules](../reports/planning/financial-scoring-plan.md#preregistro-v2). No required winning score: recall zero, null lead time or insufficient support are legitimate results. The dashboard presents the existing sealed measurements as a later retrospective report, not as information known at historical cuts.

The backtest uses a temporal reserve among the 200 v1 train/validation groups; **all 50 final-test groups are excluded** from v2 development, event/alert evaluation and case selection. This is known-company evaluation, not a new independent group holdout or external validation. Shared synthetic data and upstream preparation limit independence. Their observed trajectories can be displayed in the product without outcome labels or outcome-based case selection; reserved outcomes must not be inspected early through the UI.

## Preserve the cash-planning demo

The existing example starts from €2m cash, a €4m payment and explicit assumptions. Its dated gap and bounded plans remain useful **demo evidence**, not financial stocks derived from challenge flows. Keep its current dashboard/planning panel and label illustrative health fields as illustrative, not official or validated v2 health.

Where the snapshot supports them, finance users can still choose a metric, target and deadline, enter assumptions, and compare plans and cash constraints. Missing revenue, retention, margins, obligations or funding are not invented. Planning never rewrites observed trajectory or guarantees a result; proxy-only companies must not acquire cash controls through fabricated balances.

[Embat's treasury forecasting and risk monitoring](https://www.embat.io/treasury-management/cashflow) is product context, not proof of this implementation's capabilities. No new external services, public reports/sharing, payment execution or credit decisions are part of v2.

Implementation contract: [REQUIREMENTS.md](REQUIREMENTS.md). Navigation: [DASHBOARD.md](DASHBOARD.md).
