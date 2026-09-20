# Dashboard

One company dashboard and a dated health explanation page, built from published Parquet outputs. Scope: [Product](PRODUCT.md) · [Requirements](REQUIREMENTS.md).

## Dashboard

- **Context:** one company selector at the sidebar top; assessment date and currency in the body.
- **Health history:** the first chart shows the model-returned monthly ratings. Its native month control updates the selected score and evidence throughout the page.
- **Source summary:** monthly net movement, overdue supplier payments, overdue customer collections and observed rolling debt service.
- **Cash and payment details:** net/cumulative movement chart, signed cash categories, invoice amounts/delays and model debt-service window.
- **Evidence:** all saved monthly rows, per-date score explanations, source hashes, confidence and limitations.

The public demo exposes the explicitly published challenge snapshot without a session. The private dashboard shows only imported companies authorised for the signed-in identity. Neither view contains the removed hardcoded Mediterránea forecast or a mock fallback.

## Company health in MCP

The OAuth-protected `/mcp` endpoint exposes `get_company_health` with an exact `company_id` and optional `month` (`YYYY-MM`). It defaults to the latest month with company activity and returns the dated health state, issues, cash and payment metrics, original-currency totals, coverage and source provenance. `list_companies` lists only the signed-in account's memberships and renders a company picker in ChatGPT. Each selection is authorised again on the server.

The same Rust logic supplies `GET /api/companies/{id}/health?month=YYYY-MM` and each dashboard record's `health_status`. Alerts cover a score below 40, a fall of at least 5 points from the preceding month, negative reconstructed cash, net cash outflow, overdue payments/collections and an observed debt-service shortfall. These are provisional attention rules, not calibrated credit-risk boundaries. Missing, excluded, confidence-none and degenerate scores are not treated as evidence of poor health. All issues retain their cutoff, source, units and evidence.

Assign selected imported companies to an existing account through the administrative CLI, under the production runtime environment: `blaubeere-api grant-company-access EMAIL COMP_0006 COMP_0048 COMP_0176`. It validates every ID before writing, adds memberships idempotently and verifies the assigned data. The deployment workflow exposes this operation through optional manual `grant_email`, `grant_companies` and `access_only` inputs; no public administration endpoint is added. Accounts and grants persist in the existing identity SQLite database.

Replace one membership atomically with `blaubeere-api replace-company-access EMAIL OLD_ID NEW_ID`, or set the workflow's `replace_company` and a single `grant_companies` ID. The new company is validated before the old membership is removed; other memberships are preserved.

## Planning inputs

The existing Rust planning API still supports the validated daily JSON assessment contract below. Historical monthly Parquet outputs do not establish an opening bank balance or dated future obligations, so the current company dashboard does not offer forecasts or planning from those records. Plans remain conditional calculations and never rewrite source records or saved ratings.

## Daily model response contract

The authorised company assessment may include `health_assessments`, an array of dated model responses. Each entry contains `date` (unique ISO day), `model_version`, `history_mode` (`as_known` or `reconstructed`), `health`, `drivers`, `coverage`, and `flows`. See the runnable synthetic examples in `fixtures/companies.json` and the Rust `HealthAssessment` type.

- `health`: `score` (0–100), `previous_score` (0–100 or null), `period` (comparison interval), `label`, and `note` (the returned explanation). A daily comparison normally uses the preceding saved rating, but the model must identify its comparison interval.
- `drivers`: `label`, `detail`, `points` (signed contribution to the change from `previous_score`, or null when unquantified), and `source_ids`. The UI never invents contribution weights or calculates a replacement rating.
- `flows` and `coverage`: the evidence snapshot saved with this response, using the existing source shapes. Preserve the amounts, settlements, knowledge dates and coverage as they were at that cutoff. Future knowledge and invalid scores are rejected by the loader. Unavailable references and unexplained score differences remain visible.

Supply each new day's response through the existing assessment-file integration while retaining earlier entries. No daily model runner is introduced here; the UI displays supplied outputs. Dated snapshots take precedence over legacy score-only history for the same date. Legacy current assessments remain supported; older scores without a saved explanation explicitly show that it is unavailable. Days with no model output are not interpolated.

Private links use `/dashboard/health/YYYY-MM-DD?company=ID` and the existing authenticated company API. Login returns to the selected date. `/demo/health/YYYY-MM-DD?company=ID` reads the selected company’s published monthly assessment through the Rust demo API without a session.

## Published monthly challenge outputs

Imported companies use a `kind: "model"` response from the same authenticated Rust endpoint. It contains each published monthly score and its original inputs/reasons, joined to that month's cash and payment evidence, plus import provenance. The dashboard shows known collections, operating payments, debt service and confidence; every saved month-end rating opens its own explanation page. Missing scores remain empty. The single native company selector at the top of the sidebar supports the full imported company list. The dashboard body contains only the assessment-date selector.

The overview shares one month selection across the score, cash and invoice details, and rolling debt-service totals. Cash charts switch between net monthly and cumulative movements, preserve missing periods, and show exact EUR amounts when inspecting a month. Published cash categories, supplier payments, customer collections, invoice coverage gaps and a scrollable monthly record table sit alongside the score history. Table dates select a month; rating links open its saved explanation. Switching company returns to its latest month.

Debt service is the known observed D6 subtotal in the model window, not outstanding borrowing or a future repayment schedule. Invoice amounts due and overdue are totals at the selected cutoff, not monthly cash flows. Cash categories precede transfer-resolution adjustments and can differ from the model’s rolling totals. These fields come from the existing Rust response; the frontend does not parse Parquet or calculate replacement scores.

The current files contain monthly assessments, not daily outputs. Model inputs are EUR, not cents. Cash position is relative cumulative movement, not an opening balance. No forecast or plan is inferred from these historical artifacts. The provisional model's limitations, including the retained zero-operating-payment behavior, remain visible. Daily JSON assessment contracts remain available to authenticated backend integrations. The frontend uses imported monthly records only; the hardcoded Mediterránea demo and forecast have been removed.

The public demo uses the same model dashboard and Rust query as the private workspace, scoped to the explicitly published challenge database with `DATASET_DEMO=true`. Health score history is the first chart; its month control updates the selected score, evidence summary and all financial sections. Dated explanations stay within the public demo. Missing or unavailable data never falls back to the old mock workspace.
