# Dashboard

One dashboard, a dated health explanation page, and one planning panel. Scope: [Product](PRODUCT.md) · [Requirements](REQUIREMENTS.md).

## Dashboard

- **Context:** authorised company, assessment date, currency.
- **Cash outlook:** usable cash, funding needed above the chosen buffer, first shortfall date, minimum projected cash.
- **One chart:** cash history + baseline forecast + cash buffer; cover the goal horizon with 30/60/90-day checkpoints.
- **Daily health:** each returned rating links to its own dated explanation page. Show that model response’s score, narrative, drivers, evidence coverage and source snapshot; label reconstructed assessments. Cash metric cards have no explanation popup.
- **Primary action:** “Explore a plan”.

## Planning panel

- Choose one supported metric, target, and deadline; show required inputs for unsupported metrics.
- Enter assumptions, adjustable drivers and their limits, and constraints such as a cash floor.
- Compare baseline + two suggested plans: driver changes, target result, cash impact, trade-offs, and constraints passed or failed.
- Check constraints throughout the horizon. Later receipts cannot erase earlier shortfalls.
- If neither plan qualifies, explain the remaining gaps; do not claim every possible plan is impossible.

Keep assumptions visible and sources inspectable. Plans are conditional projections; source records, baseline, and observed health score stay unchanged. Planning never rewrites saved daily ratings.

## Daily model response contract

The authorised company assessment may include `health_assessments`, an array of dated model responses. Each entry contains `date` (unique ISO day), `model_version`, `history_mode` (`as_known` or `reconstructed`), `health`, `drivers`, `coverage`, and `flows`. See the runnable synthetic examples in `fixtures/companies.json` and the Rust `HealthAssessment` type.

- `health`: `score` (0–100), `previous_score` (0–100 or null), `period` (comparison interval), `label`, and `note` (the returned explanation). A daily comparison normally uses the preceding saved rating, but the model must identify its comparison interval.
- `drivers`: `label`, `detail`, `points` (signed contribution to the change from `previous_score`, or null when unquantified), and `source_ids`. The UI never invents contribution weights or calculates a replacement rating.
- `flows` and `coverage`: the evidence snapshot saved with this response, using the existing source shapes. Preserve the amounts, settlements, knowledge dates and coverage as they were at that cutoff. Future knowledge and invalid scores are rejected by the loader. Unavailable references and unexplained score differences remain visible.

Supply each new day's response through the existing assessment-file integration while retaining earlier entries. No daily model runner is introduced here; the UI displays supplied outputs. Dated snapshots take precedence over legacy score-only history for the same date. Legacy current assessments remain supported; older scores without a saved explanation explicitly show that it is unavailable. Days with no model output are not interpolated.

Private links use `/dashboard/health/YYYY-MM-DD?company=ID` and the existing authenticated company API. Login returns to the selected date. `/demo/health/YYYY-MM-DD` uses only bundled synthetic snapshots and needs no deployed backend.
