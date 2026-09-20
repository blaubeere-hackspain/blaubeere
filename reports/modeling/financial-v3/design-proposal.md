# Financial health v3 — development contract proposal 0.1

## 1. Status and boundaries

This is the practical V3T2 proposal following [audit.json](audit.json) and [audit-summary.json](audit-summary.json), not a learned model, frozen confirmatory protocol, business-validated policy, official rating or completed v3 goal. Numeric choices below are autonomous **development defaults**, chosen for interpretable economic behavior, not fitted to v1 outcomes or the consumed v2 reserve. A separate review must approve/freeze the policy before any new confirmatory outcomes. The user need not answer questions to proceed with generic implementation and invented fixtures.

**Predictive acceptance is blocked:** no truly untouched test has been identified. All 50 v1 final-test groups remain excluded, and the v2 temporal reserve is consumed. Do not manufacture a holdout by re-partitioning, changing labels or renaming the experiment. No new events, scores, financial reconstructions, training or evaluation were executed for this proposal. Automatic alerts remain disabled.

**Data feasibility is also blocked for a company headline:** the audited inputs cannot certify unrestricted reconciled cash, historical obligations/payment allocations, AP/AR roles, exhaustive debt or historical known-on dates. The expected truthful initial real-data delivery is a null headline plus scoped evidence and reasons, not six answered predictive questions. Implement the generic methods and prove behavior with invented fixtures; never relax gates just to populate the dashboard.

## 2. Compact architecture and artifact isolation

New modules should use the `financial_v3_` prefix and live alongside, never inside or modifying, frozen v1/v2 code:

| Proposed module | Pure core contract | Input/output boundary |
|---|---|---|
| `scripts/financial_v3_contract.py` | Validate versions, keys, units, availability, eligibility, policy and hashes | No model or outcome access; read-only `--check` |
| `scripts/financial_v3_cash.py` | Classify scoped flows; reverse native-currency ledger; verify independent balances | `Account`, `CashMovement`, `BalanceAnchor`, `CoverageInterval`, `FXObservation` -> `CashMonth` |
| `scripts/financial_v3_obligations.py` | Versioned obligations, allocations, principal roll-forward and aging | `ObligationVersion`, `SettlementAllocation`, `Amendment` -> `ObligationMonth` |
| `scripts/financial_v3_features.py` | Comparable receipts, due-adjusted generation, liquidity and coverage | Eligible company/month inputs -> versioned features; no future lookups |
| `scripts/financial_v3_score.py` | Fixed scorecard, mora penalty, exact level/delta explanation | Features + frozen policy -> `ScoreResult`; no probability conversion |
| `scripts/financial_v3_events.py` | Future economic events and censoring, independent of score/signals | Separately authorized outcome data -> events; never called during export/API |
| `scripts/financial_v3_evaluate.py` | Baselines, bounded candidate selection, chronological matching and reporting | Development only until untouched-data gate passes; separate exclusive test receipt |
| `scripts/financial_v3_export.py` | Versioned strict union, provenance and temporal filtering | Existing artifacts -> company/month payload; no training/evaluation |

Use only new artifacts under `reports/modeling/financial-v3/`, with immutable named subversions for future experiments. Do not write new marts into the pinned run or move a current pointer. Test files likewise use `test_financial_v3_*`. Tests should call pure functions on invented data, not regenerate old datasets. Keep audit checks hash-only: `.venv/bin/python -B scripts/financial_v3_audit.py --check`.

Later app integration extends the current discriminated union with `financial_health_v3`, retaining cash demo, v1 proxy and `operating_trajectory` unchanged. Use the existing authorized company endpoint/MCP tool, cached sealed export, exact source schema/hash validation and date filtering. No separate product, rebranding, new unauthorized lookup, runtime reconstruction or outcome access.

## 3. Input records, availability and reasons

Amounts are finite decimal values in a declared native currency; use Decimal in Python and decimal strings on JSON boundaries, with minor-unit quantization only for legal settlement amounts. Ratios/points are finite binary64, never NaN/Infinity. IDs are opaque strings, dates ISO, timestamps UTC plus original source timezone. Every fact has `source_id`, `source_sha256`, `record_id`, `effective_at`, `known_on: timestamp|null`, and `retrieved_at`; retrieval is not historical knowledge. Duplicates with identical canonical identity are idempotent; conflicting duplicates make the affected perimeter ineligible, not a last-write-wins overwrite.

Required records:

- `Account(account_id, company_id, product_type, currency, effective_from, effective_to|null, unrestricted_policy, source_ref)`; unknown account opening is not `created_at` onboarding.
- `CoverageInterval(account_id, from_inclusive, to_exclusive, statement_complete, genuine_zero_activity, available_at, source_ref)`; no rows alone never imply an interval is complete.
- `CashMovement(movement_id, account_id, booked_at, value_at, amount_native, booking_status, economic_bucket, classification_evidence, transfer_pair_id|null, obligation_allocation_ids[], known_on, source_ref)`. Buckets: operating, financing, investment, transfer, adjustment, unknown; inflow/outflow kept separately by sign. Unpaired transfers stay visible/ambiguous; no netting assumption.
- `BalanceAnchor(anchor_id, account_id, currency, amount_native, unrestricted_native|null, measured_at, cutoff_semantics: opening|closing|instant|unknown, known_on, independently_observed, source_ref)`. Invalid sentinel/nonfinite/mismatched owner or currency is rejected. A ledger balance is not automatically usable cash.
- `FXObservation(currency, reporting_currency, measured_at, rate_local_per_reporting, known_on, valuation_kind, source_ref)`; foreign identity rate of one without evidence is unknown. Month-end valuations and transaction conversions are different facts.
- `ObligationVersion(obligation_id, company_id, kind: operating_ap|financial_principal|financial_interest|other_payable|ar, currency, principal_or_face_amount, installment_id|null, financial_product_id|null, invoice_id|null, due_at|null, effective_from, effective_to|null, known_on, contract_source_ref, role_verified)`. AR is never offset against AP/debt. A principal balance is not itself a second invoice/installment obligation.
- `SettlementAllocation(allocation_id, obligation_id, movement_id|null, paid_at, known_on, amount_native, component: principal|interest|fee|trade, source_ref)`; a payment may allocate across obligations but summed allocations cannot exceed evidenced payment or remaining obligation. No counterparty/date coincidence alone establishes a match.
- `Amendment(amendment_id, obligation_id, kind: cancel|renegotiate|reverse|writeoff, effective_at, known_on, amount, replacement_obligation_id|null, evidence_ref)`; retain old lineage and delinquency history, never silently reset age. Writeoff is not payment or recovery.
- `UniverseAttestation(company_id, source_kind, effective_period, exhaustive, explicit_zero, known_on, source_ref)`; required for exhaustive cash, AP and financial debt. No product record does not mean no debt.

For month m the time interval is `[month_start, next_month_start)`. A closed-month origin is `month_end`, with `knowledge_cutoff` separately represented. Strict `as_of` includes only records with verified `known_on <= knowledge_cutoff` and effective applicability at m. No known_on means no strict as-of feature. An optional `as_of_simulation` may use an explicitly versioned assumed availability contract, **never** represent actual historical issuance; cannot admit a future anchor or final schedule vintage before its availability. Baseline dataset retrieval is not backdated to 2024.

`reconstructed_retrospective` can show history learned later, but publishes `reconstruction_available_at = max(anchor.known_on, all movements/adjustments/FX known_on)`; if any required date is unknown, this is null and `availability_verified=false`. It cannot feed earlier predictions. Display the date range reversed, anchor and reconciliation evidence. Invoice final-status stocks stay retrospective and provisional, not eligible historical obligations by default.

Required reason codes include `missing_source`, `universe_unverified`, `unknown_known_on`, `anchor_cutoff_unknown`, `anchor_invalid`, `unrestricted_cash_unverified`, `ledger_gap`, `reconciliation_unverified`, `reconciliation_mismatch`, `fx_missing`, `classification_ambiguous`, `role_unverified`, `due_date_unknown`, `settlement_history_missing`, `allocation_conflict`, `debt_vintage_missing`, `coverage_changed`, `insufficient_history`, `zero_reference`, `open_month`, `version_changed`, `immature_followup`, `new_test_missing`. Coverage/confidence is not an economic penalty.

## 4. Cash reconstruction and reconciliation

Work at account/native-currency grain first, using booked movements under an agreed booking-balance convention. A value-date balance requires a matching value-date ledger instead; do not mix the two. For every completed interval:

`B_start = B_end - inflows + outflows - signed_nontransaction_adjustments`.

The equivalent signed formula is `B_start = B_end - sum(amount_native) - adjustments_native`. Include all booked movements, including unknown economic categories, because they still move money; economic classification gates generation, not the balance identity. Recorded adjustments that already are movements must not be added twice. No hidden balancing plug. Transfer elimination requires demonstrated same-perimeter matched legs; per-account reversal retains each leg. Debt-product ledger entries do not get added again to banking movements.

Before reversal, require exact anchor cut semantics and the complete bridge from the requested month-end to that anchor, including the relevant September 1 movements when the anchor is on September 1. Do not assume a midnight timestamp is the August closing balance. Stop at any unknown coverage interval. Cash available is separate from ledger cash: require restrictions/release evidence, not `balance` as a substitute for missing `available`.

Reconciliation residual against an **independently observed** second balance is `observed_end - (observed_start + signed_movements + signed_adjustments)`. Development tolerance in native units: `max(one currency minor unit, 1e-6 * max(abs(observed_start), abs(observed_end), 1))`. Publish residual and tolerance. Above tolerance => unusable; only one anchor => `reconciliation_unverified`, not a claim that a tautological reverse sum reconciles. Balance/FX source correctness still needs review even if the arithmetic passes.

For reporting-currency stocks, reconstruct each native balance first, then convert with the independently observed valuation FX at that date. Separately explain valuation change between dates. Existing transaction-specific EUR conversions and `fx_rates` (foreign rates null) cannot establish a stock FX curve. Until supplied, publish separate native amounts, with aggregate reporting-currency stock null if any required currency is unavailable. Pure EUR does not cure missing anchor semantics or unrestricted-cash evidence.

## 5. Obligations, partial payment and mora

For each obligation at t, remaining amount is face/principal plus effective evidenced adjustments minus **allocated** settlements known by the applicable knowledge cutoff; amounts cannot be negative. Interest payments do not reduce principal. Principal roll-forward is opening principal + drawdowns - principal allocations + documented principal adjustments. Reconcile to independent principal observations; do not repeat extraction outstanding across all months.

Allocation priority: explicit source allocation wins. If evidence certifies a payment belongs to a bounded contract family but does not identify the installment, development-only fallback is oldest due date then stable obligation ID **within the same company, currency and contract**; publish `allocation_policy=oldest_due_assumed`, making strict headline eligibility false until validated. No fallback across unrelated loans/suppliers. Unknown allocation => retained uncertainty, never assume full payment. Partial payment reduces exposure from its evidenced paid/known time; canceled or renegotiated obligations change only from documented effective/known times. No history deletion.

Let `u_i(t)` be unpaid legal amount and `d_i=max(0, calendar_days(t-due_i))`. `U_due = sum(u_i for due_i <= t)` includes grace-period obligations and due-today obligations. Unknown due dates make total due and headline compliance unknown; they are not current/not-overdue defaults.

Chosen development mora policy:

| Calendar days late | Multiplier k(d) |
|---|---:|
| not late (d=0) | exposure 0; amount still in U_due if due today |
| 1–7 | 1.00 |
| 8–30 | 1.25 |
| 31–60 | 1.50 |
| 61–90 | 2.00 |
| 91–180 | 3.00 |
| 181+ | 4.00, never disappears |

Grace is seven calendar days **for escalation**, not forgiveness/removal of actual overdue amount. `E = sum(u_i*k(d_i) for d_i>0)`. E is weighted risk exposure, not extra legal debt, expense or fictitious cash outflow. Publish actual overdue amount, buckets, earliest unresolved due date, principal/interest/trade subtotals, and E separately. Saturating age at 4 is a development policy limit, not evidence that old unpaid debt becomes benign.

## 6. Fixed scorecard with a no-payment invariant

### Eligible inputs and reference

An aggregate requires **all five dimensions plus complete obligations/mora**, a verified exhaustive comparable company perimeter, reconciled unrestricted cash, valid currency aggregation, verified operating classification and six contiguous closed months. Known partial dimensions may be displayed with scope; do not renormalize weights. Strict as-of additionally requires known-on eligibility. A company attested to have zero financial debt may satisfy the debt gate with zero; no debt feed may not.

At origin m, let `R_j` be verified comparable operating receipts, excluding financing, transfers, asset sales and unmatched classifications. Let `O_j` be verified operating obligations **falling due** in month j, counted once, including immediate-due cash expenses. It is a commitment measure, not accounting profit. All early/late cash settlements remain in the cash-flow view but are not a second O_j. Invoice recognition/accrual dates and debt interest stay separate. `F_j=R_j-O_j` is **commitment-adjusted operating generation**, not observed bank cash net. Unknown due-ledger coverage => F_j null, not fallback to cash payments.

Reference `S = mean(R_{m-5..m})`, in constant comparable reporting-currency units for the six-month window; require `S>0`. Zero denominator gives null rather than arbitrary epsilon or top score. S cannot depend on payment timing, cash outflows, gross bank volume or future values. Reference/FX/perimeter changes are separately explained. Define `r = mean(F_{m-2..m})/S`.

`C_adj = unrestricted_cash_m - U_due_m` is a **scoring-only due-adjusted liquidity indicator**; it is not a new bank balance. `D` is verified gross financial principal outstanding (not AR-netted); `J` is mean contractually due principal+interest for the next three calendar months, from schedules demonstrably available at origin. `COV=max(mean(F_recent3),0)/J` for J>0. If J=0 with complete verified schedules, the service subscore is 100 by verified absence, not by missingness.

### Exact formulas (clip(x,a,b)=min(b,max(a,x)))

| Dimension | Definition (0–100, higher better) | Weight |
|---|---|---:|
| Generation G | `clip(50 + 100*r, 0, 100)`; zero commitment generation is midpoint, +0.5 S reaches 100 | 0.25 |
| Liquidity L | `100*clip((C_adj/S)/3, 0, 1)`; three months of receipt-scale due-adjusted liquidity reaches 100 | 0.25 |
| Debt Dscore | `0.5*[100*clip(1-D/(12*S),0,1)] + 0.5*[100*clip(COV/2,0,1)]`; verified J=0 uses 100 for the second bracket | 0.20 |
| Receipts growth H | `clip(50 + 100*g,0,100)`, `g=(mean(R_recent3)-mean(R_prior3))/mean(R_prior3)`; require prior mean>0 and comparable perimeter | 0.15 |
| Stability T | `100*clip(1 - std_population(F_{m-5..m}/S)/0.5,0,1)` | 0.15 |

Mora point penalty: `P=40*E/(S+E)` (E>=0, S>0). `base=50`; dimension contribution `c_k=w_k*(dimension_k-50)`. `unclipped=50+sum(c_k)-P`; `limit_adjustment=clip(unclipped,0,100)-unclipped`; **`score=unclipped+limit_adjustment`**. Nonfinite inputs fail. Do not round before scoring or comparing. Display one decimal but reconcile using stored full-precision values and a displayed rounding residual.

These weights emphasize generation/liquidity (half), retain debt/service (one fifth), and reserve lower weights for receipts growth/stability. They are **not estimated or approved economic utility weights**. Penalty saturation at 40 points and score clipping are explicit. Zero debt/non-deficit alone cannot imply exceptional health; generation, cash buffer, growth and stability must also support the score. Provisional numeric bands: [0,40) fragile; [40,60) constrained; [60,80) intermediate; [80,90) solid-candidate; [90,100] exceptional-candidate. Until independent level validation, suffix every band with `unvalidated_internal`; never declare a company healthy from these bands alone.

### No-payment proof and tests required before use

For paired worlds with identical receipts, incurred/due obligations, timing, FX, coverage and all other factors, withhold a due payment of A. Actual cash increases by A and U_due by A, so C_adj is unchanged. F, S, growth and stability are unchanged because none uses cash payment timing. Financial principal D cannot fall by withholding principal, and service schedules do not improve. E does not decrease, so P does not decrease. Every weighted term is equal or worse; monotone clipping preserves `score_unpaid <= score_paid`. During the seven-day grace, due-adjustment already prevents a liquidity reward. With greater age and unchanged legal exposure/reference, k and P never decrease. Increasing unpaid amount holding other factors fixed never improves score.

For a partial payment A from the same cash perimeter, cash and U_due both fall by A, leaving C_adj unchanged; legal exposure/penalty and (for principal) D weakly improve. Paying an obligation must not itself create fake operating growth. Retain both actual cash and due-adjusted indicators, clearly labelled. Debt stock in D and matured amount in liquidity/penalty are different risk views of the same liability; they are not additional ledger liabilities or extra expenses. Reconcile deduplication before scoring.

Invented paired fixtures must cover punctual payment, due-today, days 1/7/8/30/31/60/61/90/91/180/181/365, one-month and persistent delays, partial allocations, unknown due date, duplicate documents/payment rows, principal-versus-interest allocation, debt with no movements, cancellation and renegotiation lineage, zero debt with/without attestation, fixed versus changed denominator, currency mismatch, unknown inputs, score clipping and all horizons. A test that compares only the mora component is insufficient: assert the aggregate invariant and exact cash/obligation accounting together.

## 7. Growth, deltas and faithful explanation

Default observed growth is recent three calendar months versus preceding three, as H above. Show nominal comparable **operating receipts growth**, not sales or inflation-adjusted economic growth without external price data. Show verified invoiced-sales growth as a separate measure only after AP/AR role and credit-note semantics pass. Also expose month-on-month when both months are complete, and three-month year-on-year with 15 contiguous known months (recent3 versus the corresponding3 twelve months earlier). The 24-month synthetic source cannot establish longer seasonal patterns. Do not learn seasonal factors across future/reserved rows. Report seasonality limitation, source changes, FX effects and base-zero abstention. No `growth_pct` simulator feature.

For two eligible same-policy adjacent months, `delta_score=sum(delta_c_k)-delta_P+delta_limit_adjustment` (base difference zero). Otherwise delta null with reason; never stitch policy versions or gaps. Each dimension stores raw values, units, denominator S, transformation, weight and contribution. Every two-input ratio change is exactly decomposed in declared order, not called causal: for `a/b`, numerator contribution `(a_new-a_old)/b_old`, denominator contribution `a_new*(1/b_new-1/b_old)`. Nested transforms use fixed-order telescoping recomputation (numerator, denominator, thresholds/limits) and disclose order-dependence; sums must equal the reported change. No approximate SHAP is needed for the scorecard. Predictor explanations live separately and never masquerade as score points.

Level and observed delta are not forecasts. Development descriptive direction is improving for delta>=+5 points, deteriorating for delta<=-5, otherwise stable, only on comparable eligible adjacent months. This rule is not a training target, proof of sustained improvement or predictive alert.

## 8. Proposed independent future targets (not computed)

Targets must consume a separately versioned **economic outcome ledger**, not score, delta, alerts, v1 proxy or their thresholds. Shared economic primitives are permissible; copying the score rule as a label is not. All settlement/receipt sources need dated availability and complete follow-up. Materiality below is chosen, not learned. Keep invoices/obligations and realized collections separate from forecasts.

For candidate event onset s, fix references using only the three months s-3..s-1: `B_s=mean(verified operating receipts)` (>0), `F0_s=mean(receipts - operating obligations due)` and `Q0_s=unpaid obligations already >30 calendar days late at s-1`. `A_s=max(EUR 1000, 0.05*B_s)` is fixed for the event; use observed reporting FX or abstain. Let F_j be actual future receipts minus obligations falling due, Q_j the real unpaid >30d legal amount (no mora multiplier), and Z_j the amount newly crossing 30d late in month j. Fully paid/writeoff/renegotiated statuses remain distinguishable; writeoff does not count as repayment recovery.

- **Deterioration onset s**, confirmed at end s+1: in both j=s,s+1, either (a) `Q_j-Q0_s >= A_s` with the increase due to unpaid obligations rather than a new-data/perimeter change, or (b) `F_j <= F0_s-A_s` AND `F_j < 0`. The same branch must hold in both months. This permits a currently strong company to deteriorate before its overall level is low.
- **Improvement onset s**, confirmed at end s+1: in both months, either (a) `Q0_s>=A_s`, `Q_j<=0.5*Q0_s`, the reduction is evidenced payments of at least A_s (not writeoff/renegotiation), `Z_j<A_s`, and `F_j>=F0_s-A_s`; or (b) `F_j>=F0_s+A_s`, `F_j>0`, and `Q_j<=Q0_s` with `Z_j<A_s`. This permits low-level recovery and growing generation in a non-distressed firm. Both future months and the baseline must be observable.
- At each sign scan chronologically; first qualifying onset starts an episode and fixes its B/F0/Q0/A references. End the episode after two consecutive fully observed months fail that sign's primitive condition against those fixed references. A gap makes episode state unknown, not a restart; resume only after two fully observed nonqualifying months. Earliest onset wins; no ranking by model accuracy. Separate signs may coexist through different economic channels; keep their targets/probabilities independent, not complementary.
- A generic training origin o asks whether an onset of each sign occurs in the **next three calendar months** o+1..o+3; full negative-label ascertainment requires outcomes through o+4 and the requisite history. Earliest qualifying positive can be observed earlier; record observability date separately. No label for a censored negative. Evaluation matches to deduplicated sign episodes, not repeated overlapping labels.
- **Bache versus persistent decline:** for a confirmed deterioration episode at s, by end s+3 label `recovered` if two consecutive months among s+1..s+3 have `F_j>=F0_s-A_s/2` AND `Q_j<=Q0_s+A_s/2`, with no material newly late exposure `Z_j>=A_s`. Label `persistent` if the episode's same deterioration branch remains true at all of s+1,s+2,s+3. The predicates are disjoint because recovery uses half-materiality. Fully observed cases satisfying neither are `mixed`, not silently bache/caída. Any missing required follow-up => unknown/censored. Emit a recovery/persistence/mixed forecast at confirmed decline s+1 using only data available then; never retrospectively label that forecast as known at s. Record elapsed onset-to-confirmation delay. This conditional forecast is distinct from advance deterioration detection.

Level reference for Q1 must be external to the formula: propose a blinded panel of three qualified finance reviewers with complete independent statements, obligation records and cash restrictions, none seeing the v3 score. Majority ordinal label {fragile, constrained, ordinary, solid, exceptional}, unanimous exceptional; disagreement/unavailable => unknown. Rubric must be signed before confirmatory review, with explicit attention to payment compliance, funding resilience and sustainable generation, not the five weighted transforms. As an independently observed safety endpoint, adjudicate material payment failure (>30d late, >max(EUR1000, 5% trailing receipts), no disputed obligation) and unplanned rescue finance over next six months. Lack of failures alone is not exceptional health. Reviewers/business rubric and independent untouched outcomes are **not available now**; Q1 remains unvalidated.

## 9. Development candidates, success defaults and confirmation gates

No existing v2 reserved results are used to choose values here. Existing 200 groups are only retrospective development; **do not compute new outcomes in the consumed v2 reserved interval to tune the design**. Initial invented fixtures and historical development origin window March–September 2025 with follow-up through December 2025 may support later explicitly authorized exploration; six-month level-reference endpoints need separate follow-up and cannot be silently extended into reserve. All 50 final-test groups are excluded at all dates, including case selection. Old data cannot confirm any new design.

Before new data: preregister exact external/future acquisition, access log, identities, vintage rules, group/time split, frozen models/transformations and untouched origins. Prefer at least 50 new external groups for confirmation, distinct from all historical groups. If future observations on known non-final-test groups are used, label temporal transport, not unseen-group validation. No prospective dates are invented for data that do not exist. Require six history months, four future months for directional targets, four-month dip follow-up, and six for the level safety endpoint. Purge training origins whose outcome observability exceeds each fold's training cutoff; group isolation for unseen-company claims. Calibration and imputation are fitted on development only; missing core evidence still abstains, not imputes to healthy.

Development candidate budget after data eligibility review: four families per sign, fixed candidates, at most 3 grouped rolling development folds. (1) Training prevalence constant. (2) Persistence/last observed primitive state. (3) Simple recent-minus-prior-three-month trend on F/S and Q/S, logistic calibration on training only. (4) Interpretable logistic model of the five dimension values, mora E/S, observed changes, receipt growth and time-since-last-known-state, C in {0.1,1} (two fixed settings). Do not add an HGB search until the fixed baselines are useful and a separate amended development budget is approved. For bache/persistence/mixed: training class prevalence, last primitive-state persistence, and one multinomial logistic C=1. All candidate models are optional; if evidence/support is insufficient, forecast probabilities stay null. Bounded search does not confer validation.

Choose development winner by macro-group Brier (lowest; ties <=1e-6 prefer simpler then C=0.1), publish all candidate results. Probability decision threshold 0.60, two consecutive eligible origins for improvement/deterioration confirmation; one alert per sign episode, reset after two below-threshold eligible origins; gaps suppress issuance rather than invent continuity. Store first watch and actual confirmation separately; never credit watch as a confirmed alert. Conditional bache/persistence decisions use probability>=0.60 at s+1; otherwise abstain, with mixed probability still shown when valid. Probability calibration uses training folds only. No automatic operational alerts after mere development success.

Proposed **numeric utility gates**, all subject to business sign-off and freeze before new confirmatory outcomes:

| Question / sign | Minimum support and coverage | Required held-out utility |
|---|---|---|
| Q1 level | >=50 groups and >=300 independently reviewed company-months; >=30 references per ordinal class; strict headline eligibility >=70% of intended population company-months | Weighted ordinal kappa >=0.60 (95% group CI lower >=0.40); solid/exceptional reference precision >=0.80; exceptional precision >=0.90 with >=30 predictions in >=10 groups; six-month material-failure rate <=0.10 among solid/exceptional, reported with uncertainty; no claim from failures alone |
| Q2 improvement | >=50 groups, >=100 mature events across >=20 event groups, >=100 mature negatives; prediction eligibility >=70%, mature ascertainment >=80% | Event recall >=0.50, precision >=0.60 (false-alert share <=0.40), confirmed alert burden <=0.15 per eligible company-month; median positive lead >=1 calendar month; recall >=best fixed alert baseline+0.10 absolute |
| Q3 deterioration, overall | Same support/coverage separately for deterioration | Recall >=0.60, precision >=0.60, burden <=0.15, median positive lead >=1; recall >=best baseline+0.10 |
| Q3 still-strong cohort | Define at origin solely by eligible score>=80 (unvalidated-score cohort, not externally healthy); >=50 mature deterioration events across >=10 groups, >=70% prediction coverage | Recall >=0.50, precision >=0.60, median positive lead>=1, recall >=best baseline+0.10; also report independent Q1-reference cohort separately if origin-known labels exist |
| Q4 recovered/persistent | >=50 mature episodes per class across >=10 groups each; >=30 mixed cases; complete follow-up >=80%; decision coverage >=70% | Precision and recall >=0.60 for each recovered/persistent class, balanced accuracy >=best fixed baseline+0.10; report mixed/abstention separately |
| Q5 explanation | 100% eligible outputs and eligible consecutive pairs; every reference source/version available | Level and delta numerical residual <=1e-8 points (excluding explicitly represented display rounding); invariant/currency/temporal fixtures all pass; no predictive contribution labelled causal |
| Q6 lead / all directions | Same per-sign support as Q2/Q3, not only successful matches | Strictly positive lead credited only in [1,3]; median >=1; recall/precision/burden gates above; first_signal_at <= confirmed_at < onset for credited alerts, no backdating; misses/false/censored included |

For probability outputs on each applicable binary target: AP >=training prevalence+0.10, AP >=best fixed probabilistic baseline+0.05, Brier <=0.95*best baseline Brier, and fixed-bin (10 equal-width probability bins) ECE<=0.10; require >=100 positives and >=100 negatives in >=20 groups. For multiclass bache forecasts, macro one-vs-rest AP and per-class Brier/calibration reported; each recovered/persistent class must pass its analogous binary gates. No probabilities means these predictive gates cannot pass by omission.

Bootstrap 2000 group resamples with seed 1729, shared draws for paired method comparisons, 95% percentile intervals; >=1900 finite replicates required per interval, otherwise support failure, never redraw. Require the 95% lower bound of paired recall improvement over best fixed baseline >0 (in addition to the 0.10 point gate), and paired Brier improvement lower bound >0 when probabilities are claimed. Report row-weighted and macro-group/company rates, temporal thirds, sign/cohort support and all failures. At least two of three prespecified temporal thirds must meet the point recall/precision gates; a third with inadequate support is insufficient, not a success. These are intentionally demanding chosen requirements, not measured achievements.

Chronological alert matching: per company/sign/method, increasing actual issue time, match earliest unmatched eligible onset 1–3 months later, one-to-one. The event denominator is defined independently of candidate firing: events with at least one input-eligible origin in their lead window. Report excluded events without opportunities. Unmatched alerts are false only after full mature follow-up; else censored. Publish `events=matches+misses`, `alerts=matches+false+censored`, opportunities, eligible counts/reasons, episode counts, load, lead distribution, missed events and non-positive late alerts. For Q4 show confusion/abstention at the conditional forecast date, not advance-detection lead.

Freeze untouched-data receipt, protocol, sources, feature extraction, transformations, candidate selection, thresholds, models, hashes and code **before** one confirmatory opening. A consumed/interrupted receipt prohibits replay. Integrity/inference reproduction is separate from label re-evaluation. Negative or insufficient results remain published and keep v3 pending. Activation needs a separate explicit approval even after gates pass.

## 10. Typed API/app result contract

The following is a proposal for the new union branch; a worker should turn it into matching Python/Rust/TypeScript validators and invented contract fixtures before any integration. It is not an implemented endpoint.

```typescript
type EvidenceStatus = 'eligible' | 'partial' | 'unavailable';
type Money = { amount: string; currency: string };
type Quantity<T> = {
  value: T | null;
  status: EvidenceStatus;
  reasons: string[];
  source_refs: string[];
  known_on: string | null;
  coverage_complete: boolean;
};
type Dimension = {
  key: 'generation' | 'liquidity' | 'debt' | 'growth' | 'stability';
  points: Quantity<number>;
  weight: number;
  contribution_points: number | null;
  raw_inputs: Record<string, Quantity<Money | number>>;
};
type FinancialHealthV3Point = {
  month: string;
  as_of: string;
  knowledge_cutoff: string;
  view: 'reconstructed_retrospective' | 'as_of' | 'as_of_simulation';
  retrospective_available_at: string | null;
  availability_verified: boolean;
  score: Quantity<number>;
  band: 'fragile' | 'constrained' | 'intermediate' | 'solid_candidate' |
        'exceptional_candidate' | null;
  band_validation: 'unvalidated_internal' | 'independently_validated';
  dimensions: Dimension[];
  observed_delta: Quantity<number>;
  observed_direction: 'improving' | 'stable' | 'deteriorating' | 'insufficient_evidence';
  explanation: {
    base_points: number;
    contribution_points: Record<string, number | null>;
    mora_penalty_points: number | null;
    limit_adjustment_points: number | null;
    residual_points: number | null;
    delta_contribution_points: Record<string, number | null>;
    delta_residual_points: number | null;
    display_rounding_residual_points: number | null;
    reference_changes: string[];
    causal_claim: false;
  };
  cash: {
    observed_flows: Record<string, Quantity<Money>>;
    reconstructed_ledger: Quantity<Money>;
    unrestricted: Quantity<Money>;
    due_adjusted_indicator: Quantity<Money>;
    accounts: Array<{
      account_ref: string;
      currency: string;
      anchor_ref: string | null;
      anchor_at: string | null;
      anchor_known_on: string | null;
      reversed_from: string | null;
      reversed_to: string | null;
      residual: Money | null;
      tolerance: Money | null;
      usable: boolean;
      reasons: string[];
    }>;
  };
  obligations: {
    financial_principal: Quantity<Money>;
    non_due: Quantity<Money>;
    operating_ap: Quantity<Money>;
    receivables: Quantity<Money>;
    principal_due: Quantity<Money>;
    interest_due: Quantity<Money>;
    allocated_paid: Quantity<Money>;
    overdue_actual: Quantity<Money>;
    overdue_weighted_exposure: Quantity<Money>;
    earliest_unresolved_due_at: string | null;
    aging: Array<{ from_days: number; to_days: number | null; actual: Quantity<Money>; multiplier: number }>;
  };
  forecasts: Array<{
    target: 'improvement_onset_3m' | 'deterioration_onset_3m' |
            'recovered_by_s_plus_3' | 'persistent_through_s_plus_3' | 'mixed_at_s_plus_3';
    probability: Quantity<number>;
    issued_at: string | null;
    horizon_start: string | null;
    horizon_end: string | null;
    model_version: string | null;
    calibration: 'unvalidated' | 'development_only' | 'confirmed';
    explanation_ref: string | null;
  }>;
  timeline: Array<{
    sign: 'improvement' | 'deterioration';
    first_signal_at: string | null;
    confirmed_alert_at: string | null;
    event_onset_at: string | null;
    event_confirmation_at: string | null;
    outcome_observable_at: string | null;
    label: 'recovered' | 'persistent' | 'mixed' | 'unknown' | null;
    retrospectively_confirmed: boolean;
  }>;
  coverage: Record<string, { observed: number; required: number | null; reasons: string[] }>;
};
type FinancialHealthV3 = {
  kind: 'financial_health_v3';
  schema_version: 'financial-health-v3.0';
  policy_version: string;
  source_run_id: string;
  export_sha256: string;
  company_id: string;
  selected_as_of: string;
  current_point: FinancialHealthV3Point;
  history: FinancialHealthV3Point[];
  evaluation_status: 'development_only' | 'blocked_new_data' | 'confirmed' | 'failed';
  automatic_alerts_enabled: false;
  blockers: string[];
};
```

Validation must reject extra/unexpected enum values and nonfinite numeric values; require exactly five unique weighted dimensions. `score.value!=null` requires all core eligibility, finite contributions, correct range and identity. If score null, aggregate contribution/residual totals are null, although individual eligible dimensions may retain their own points; no fabricated partial aggregate. `observed_delta` requires adjacent comparable months and same policy/perimeter. Decimal-string amounts and reporting currencies must agree for any aggregation. Reason strings should become the closed enum above (plus versioned extensions), not arbitrary silent fallback.

Historical as-of responses include only points/fields actually available by `knowledge_cutoff`; hide whole later event/case objects, not just their labels. Explicit retrospective mode may show later reconstructions/outcome confirmations but must preserve their true availability and cannot call them historical predictions. Protect account/obligation source refs with the same company authorization; export should not expose raw descriptions, counterparties or cross-company data. Separate source coverage from learned uncertainty; no invented individual confidence intervals. UI shows null headline as **insufficient evidence**, not 0/100, green/no-risk or `100*(1-p_proxy)`.

## 11. Immediate next worker action

1. Review this proposal, especially the strict cash/debt/role gates, no-payment construction, independent economic targets and utility defaults; record adoption/amendment as a new version before implementation that could access outcomes. No goal-state edits by workers.
2. Implement pure typed records and cash/obligation functions with failing invented fixtures first. Prove native-currency reversal and no-payment invariants. Do not project the 87 extraction schedules or backfill historical outstanding; make absent required evidence an explicit refusal.
3. Keep model probabilities/headline scores null on unaudited/incomplete real inputs. If implementing a retrospective evidence export later, use the pinned run and the 200-group development exclusion contract, preserve gaps and publish component coverage. Do not claim complete balances or full-company debt merely because arithmetic can run.
4. Arrange the new evidence and untouched future/external data listed in audit-summary.json; independent business/reference review and a separately frozen confirmatory protocol remain blockers. Continue generic development autonomously without changing the scientific truth or marking the whole v3 objective complete.
