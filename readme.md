# Blaubeere

Internal finance planning: understand a dated cash gap, inspect the evidence, and compare a baseline with two bounded plans. Product scope lives in [docs/PRODUCT.md](docs/PRODUCT.md), [docs/DASHBOARD.md](docs/DASHBOARD.md), and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Run locally

Install Bun 1.3.12 and Rust 1.94 or newer:

```sh
bun install --frozen-lockfile
bun run setup
```

Import the published Parquet files using the command below in **Published Parquet assessments in the app**. Set the resulting `DATASET_DATABASE_URL` in `.env` and set `DATASET_DEMO=true` to make the challenge snapshot available to the public demo. Then run:

```sh
bun run dev
```

Open http://localhost:3100/login and choose **Access demo →**, or go directly to http://localhost:3100/demo. No sign-in is needed for the published dataset. Rust and its imported SQLite snapshot must be running; there is no bundled mock fallback.

Setup preserves existing configuration. The regular sign-in form accepts `finance@blaubeere.local` with the generated `BOOTSTRAP_PASSWORD`. Set `DATASET_TEAM_EMAIL=finance@blaubeere.local` to grant that account access to imported companies.

| Workspace | Address | Purpose |
| --- | --- | --- |
| `apps/landing` | http://localhost:3102 | Product landing page |
| `apps/app` | http://localhost:3100 | Company health dashboard, sign-in and assistant consent |
| `services/api` | http://localhost:8080 | Axum API, Parquet import, identity, OAuth and finance calculations |
| `services/mcp` | http://localhost:8081/mcp | Authenticated Streamable HTTP MCP |

Both Rust services share the identity database in `.local/blaubeere.db` and read the imported dataset snapshot. Run commands from the repository root. Each service also has a separate `dev:api`, `dev:mcp`, `dev:app`, or `dev:landing` script.

To develop the UI against production accounts and data, set `API_INTERNAL_URL` to the deployed app's HTTPS origin in your ignored `.env`, keep `APP_ORIGIN=http://localhost:3100`, and run `bun run dev:app`. Local Next.js forwards `/api` requests to the production Rust API. In development only, it checks the exact local Host and Origin before translating POST origins for the upstream API. Accounts and data are shared; browser session cookies remain separate for localhost and production. Registration and other authenticated changes affect production. This mode does not change the MCP service configuration or OAuth issuer.

## Try the workflow

1. Choose **Access demo →** and switch among imported companies using the selector at the top of the sidebar.
2. Inspect the health history chart. Change its month to update the rating, evidence summary, cash movements, payment arrears and observed debt service together.
3. Open **Explain this score** for the saved model inputs, formula, confidence and reasons at that cutoff. Missing scores remain unavailable.
4. Inspect cash categories, supplier payments, customer collections and the monthly source table. The challenge data is synthetic but these are its actual published outputs, not hand-authored dashboard values.
5. Sign in to connect an assistant to your authorised workspace. Forecasts and plans require additional verified inputs and are not fabricated from monthly historical records.

## Authentication and MCP

`DEMO_LOGIN=true` enables the optional `POST /api/auth/demo` endpoint for authenticated integration and MCP demos. It is separate from the read-only `/demo` page, which reads the published challenge dataset without creating a session. Each visitor receives a separate authenticated, 12-hour session and membership only in `DEMO_001`, which must be labelled `data_mode: "demo"`. Visitors cannot enter existing accounts or see each other’s assistant grants. Signing out ends the session; entering again creates a new visitor. Demo identities and grants remain in SQLite until explicitly removed.

The local example and MVP production deployment enable this flag. Set it to `false` in `.env` (local) or `deploy/runtime.sh` and the matching check in `deploy/jio.sh` (production, then redeploy) to disable the authenticated demo endpoint. The backend defaults to disabled when the flag is absent. Public dataset access is controlled separately by `DATASET_DEMO`.

Team accounts are provisioned through `BOOTSTRAP_EMAIL`, `BOOTSTRAP_PASSWORD` and comma-separated `BOOTSTRAP_COMPANIES` on API startup. Public registration creates an identity without company access; memberships must be granted separately. Provisioning an existing email preserves its password and memberships; changing bootstrap variables does not reset that account.

Browser sign-in uses Argon2 password hashes and opaque HttpOnly sessions. Company membership is checked in the backend for each assessment and plan request. Browser mutations require the configured app origin.

Add `http://localhost:8081/mcp` as a remote MCP server in a client supporting OAuth discovery, dynamic client registration and S256 PKCE. The client opens the app's consent screen, where the user can allow or decline access. **Connect assistant** lists active grants and allows immediate revocation.

Available tools are `list_companies`, `get_cash_outlook` and `compare_plans`. The `finance:read` scope includes non-mutating plan calculations. Access tokens are bound to the MCP resource; refresh tokens rotate, and replay revokes the grant. Tokens are stored as hashes. No tool changes records or executes payments.

The public `/demo` workspace now uses all imported challenge companies through the Rust API. Set `DATASET_DEMO=true` only for a dataset intended for public access, along with `DATASET_DATABASE_URL`. It defaults to false and is explicitly enabled in the MVP Jio deployment. `GET /api/demo/companies` and `GET /api/demo/companies/:id/assessment` read only the immutable challenge snapshot. They cannot access runtime company fixtures, accounts or assistant grants. Private company endpoints and MCP still require authentication and membership.

The old bundled Mediterránea dashboard and generated frontend snapshot have been removed. An unavailable import displays an error with retry; there is no mock-data fallback. The sidebar company selector is shared by the demo and private dashboard. Health history leads the overview, followed by monthly cash movements, payment/collection evidence, debt service and source records.

## Assessment inputs

The default snapshot is [fixtures/companies.json](fixtures/companies.json), a synthetic EUR example shared by the API and MCP. Set `ASSESSMENT_FILE` in `.env` to load an alternative JSON array with the same shape, then restart both services. Authorise its company IDs through account provisioning.

Amounts are signed integer cents in one reporting currency per company. This version assumes two decimal places. Each flow carries a stable ID, due date, knowledge date, settled amount, source and contractual/estimated timing. Inputs are bounded, duplicate flow IDs are rejected, settlements are deducted, internal transfers are excluded, and facts learned after the assessment date do not enter the forecast. Open overdue items are projected on the next day and disclosed as an assumption.

The upstream Data/Model workstreams own challenge CSV reconciliation, currency conversion, validated historical assessments and the official score. Rust imports their published Parquet outputs; the app does not compute a replacement challenge score. Health, driver and coverage evidence comes from the supplied snapshot. Missing business metrics require explicit finance-team inputs.

Planning compares two deterministic candidates, not every possible plan. Funding is conditional on availability, assumed on day one at 8% annual interest, with principal repayment after the horizon. Growth applies only to incremental revenue and associated costs. Plans are held in the current browser session and are not saved to the database.

## Sealed operating-deficit proxy assessments

The [training report (Spanish)](reports/modeling/training-report.md) documents the dataset, target, temporal/group splits, features, candidates, selection, observed results and reproduction limits.

The approved synthetic model can be displayed separately from cash planning. It estimates `target_deficit_3m`: operating deficit in at least two of the next three **calendar months**, within the observed perimeter. This is a retrospective, uncalibrated proxy, not a default probability, official health score, cash balance, or causal diagnosis. The current snapshot closes on 2026-08-31 and covers 2026-09-01 through 2026-11-30; it is not a rolling 90-day forecast.

```sh
python3 scripts/export_assessments.py
python3 scripts/export_assessments.py --check
python3 -B -m unittest tests.test_export_assessments -v
```

The standard-library exporter reads only the sealed `reports/modeling/experiment-v1/latest_predictions.json` and linked experiment artifacts. It pins the approved prediction SHA, verifies integrity sidecars, report/model/selection links and model artifact hashes, and never trains, evaluates, loads a pickle, or reads raw data/target tables. It writes `reports/modeling/assessment-v1/companies.json` and `manifest.json`. Repeating the export is byte-identical; existing different output is rejected, never overwritten. `--check` verifies both output files without writing.

For a read-only inference replay, use the pinned optional model environment (`requirements-model.txt`, Python 3.13.15), the sealed local model and its bound `data/runs` snapshot:

```sh
.venv/bin/python -B scripts/replay_model.py --check
.venv/bin/python -B -m unittest tests.test_replay_model -v
```

Replay first verifies the exporter's approved prediction SHA and linked seals, then the fixed local model's source/dependency hashes and the bound run manifest/panel hashes. It projects only approved `panel_flujos` columns, rebuilds August 2026 features and compares every company, metadata field, feature, eligibility flag and missing reason exactly. Unknowns remain null and missing current months remain abstentions. Probabilities allow only an absolute `1e-12` difference; the JSON result reports the maximum error and exact-equality status. It does not train, query targets, reevaluate the held-out test, consult the live G-DATA gate, modify predictions, or accept external pickle paths. `--check` writes nothing; final verification evidence is recorded separately in `reports/modeling/final-verification/`.

All 1,286 companies are preserved: 1,010 estimates and 276 explicit abstentions. Names are `Synthetic company <id>`. Each `kind: "proxy_only"` company has typed `predictive` evidence, eligibility and missing reasons, observed features, validation limits and provenance hashes. `opening_cash_cents`, `buffer_cents` and `health` are null; empty history/flows mean **unknown**, not no activity. EUR is an analytical source basis, not a claim about native reporting currency. The unchanged demo fixture defaults to `kind: "cash"` when kind is omitted.

Pass the absolute exported path as the process environment `ASSESSMENT_FILE` to both API and MCP, and authorise the desired IDs using existing provisioning. For example, `COMP_0001` has an estimate and `COMP_0002` abstains. Existing accounts retain their memberships; use a fresh local SQLite database and a newly provisioned account for isolated verification. No shared configuration or `.env` change is needed. The dashboard displays the proxy scope and missing evidence instead of cash charts, health points or planning controls. API/MCP assessments return `forecast: null`, `cash_planning_available: false` and a reason; plan comparisons reject proxy-only inputs even if callers supply cash-floor assumptions.

Validation is conditional on 131 observed labels out of 299 prospective rows across 31 groups. Average precision is 0.816 against prevalence 0.511; Brier is 0.188 against baseline 0.252. Relative Brier skill is 25.1% with a grouped fixed-fit 95% interval of 12.0–38.1%. This interval is not uncertainty on an individual probability and does not establish calibration or reliability for censored outcomes.

To verify the actual export in Rust and a real local browser (requires an installed Chromium; optionally set `CHROMIUM_PATH`):

```sh
cargo build --workspace
EXPORTED_ASSESSMENT_FILE="$PWD/reports/modeling/assessment-v1/companies.json" cargo test --workspace actual_export -- --ignored
bun run test:assessments:e2e
```

The browser script launches only local API/Next processes on free ports, uses fresh temporary SQLite databases and random unlogged passwords, signs in through the UI, checks eligible and unavailable states, keyboard controls, mobile layout, plan rejection, company isolation, and the original demo. It cleans up its own processes/databases and writes a secret-free `reports/modeling/assessment-v1/browser-summary.json`; it does not require manual browser interaction or take screenshots. Run `bun run test:web`, `bun run typecheck`, `bun run build`, and `bun run check:rust` for the remaining application regressions.

## Published Parquet assessments in the app

From the repository root, import the five published model outputs with the existing Rust API binary:

```sh
cargo run -p blaubeere-api -- import-parquet . .local/datasets "$(git rev-parse HEAD)"
```

The command prints the absolute SQLite snapshot path. Add `DATASET_DATABASE_URL=sqlite:///absolute/path/printed/above` and `DATASET_TEAM_EMAIL=finance@blaubeere.local` to `.env`, then restart the Rust services. The explicitly configured existing team account receives memberships in the imported companies. Demo visitors remain restricted to `DEMO_001`.

Rust streams the published v4 snapshot: `reports/score_v4/assessments.parquet`, `reports/cash_backfill/cash_backfill_monthly.parquet`, `reports/payment_delay_v2/payment_delay_v2_monthly.parquet`, `reports/debt_obligation/debt_obligation_monthly.parquet` and `reports/transfer_resolution_v2/resolution.parquet`. It validates and indexes a private, immutable SQLite snapshot, preserves nulls and reason codes, and records file hashes and source revision. An unchanged batch reuses its snapshot; failed imports cannot replace a healthy snapshot. API and MCP share the same Rust query and access-control logic. No Redis or database service is needed on the single VM. Reimport after pulling v4 and update `DATASET_DATABASE_URL`: dataset schema 3 rejects older snapshots rather than serving mixed model versions. The identity database can stay in place.

For daily cash charts, also materialize the tracked LFS files `data/clean/transactions.parquet` and `data/clean/balances.parquet` before importing (both or neither). The same Rust importer groups booked checking/wallet/saving/TPV movements by day and original account currency, from January 2025 to the last closed month. It reconstructs native-currency daily closing balances backwards from the snapshot, preserves unknown amounts as gaps, and adds `daily_cash` to each monthly record. No currencies are added together or relabeled. Without these files, the existing monthly model still works and the daily chart shows an unavailable state. Deployment fetches those exact-revision LFS objects automatically.

`GET /api/companies/:id/assessment` returns `kind: "model"`, monthly `records` joined with cash/payment/debt evidence, and provenance for imported companies. Model amounts are EUR values, unlike the integer-cent forecast contract. The UI links each monthly score to its own dated explanation; it does not interpolate daily scores. The cash series is reconstructed backwards from the 1 September 2026 anchor; it is not a bank balance observed at each historical cutoff. v4 includes debt-service shortfalls, overdue obligations counted once, robust arrears and the debt multiplier. Published exclusions retain their company records with null scores. Planning is unavailable for these companies until verified opening cash and future obligations are supplied. The read-only `/demo` uses this same v4 snapshot.

## Challenge dataset pipeline

The offline Python/DuckDB pipeline produces the analytical outputs. The existing Rust API binary parses the published Parquet files into SQLite for the app and MCP; there is no Python runtime or additional data service in production. The published score remains provisional, not an official credit rating. The source dataset is described in [data_dictionary.md](data_dictionary.md); the current analytical contracts and limitations are in [reports/vistas_y_hallazgos.md](reports/vistas_y_hallazgos.md).

Use Python 3.13 and the pinned dependency in `requirements.txt`. On a fresh clone, download the dataset through Git LFS and build the regenerable intermediate tables before running the data checks:

```sh
git lfs pull
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -B -m xray.cli build
bash scripts/run_checks.sh
bash scripts/verify_ingest.sh
```

`build` reconstructs all layers from the eight CSVs in an isolated workspace and runs the tests before publication. `ingest`, `clean`, `flows`, and `marts` are aliases of this coherent rebuild; `quality` performs read-only checks. Unit regressions can also run without rebuilding the dataset:

```sh
.venv/bin/python -B -m unittest tests.test_regressions tests.test_pipeline -v
```

The data contracts are conservative:

- Preserve original fields. Keep native EUR by identity; use valid reported conversions, never estimated monthly/global rates. A rate of 1 between different currencies is ambiguous and leaves EUR unknown.
- Derive flow direction from the amount sign. Transfer categories only from earlier months of the same company and direction; retain discrepancy flags.
- Keep incomplete totals unknown, with known subtotals and document-count coverage separate. Invalid dates do not delete otherwise usable invoice amounts.
- Historical stocks are retrospective reconstructions, not certified point-in-time snapshots. Use `cohort_features_at(con, cutoff_date)` to read only matured collection cohorts.
- Keep the dataset calendar in `xray/period.py`.

The monthly outputs retain all companies and closed calendar months:

| Mart | Purpose |
| --- | --- |
| `panel_flujos` | Cash-account flows, reconciliation, known subtotals and operational uncertainty bounds |
| `panel_deuda` | Identified principal, interest and refunds, excluding duplicate product-ledger aggregation |
| `panel_evidencia` | Independent evidence states for operations, observed debt service and matured collections |
| `targets_proxy` | Robust future operating-deficit labels and continuous changes in 60-day collection conversion |

The cash perimeter is `checking`, `saving`, and `wallet`; other products remain in separate diagnostics. These are observed-perimeter proxies, not complete company accounts. No current debt/cash snapshot is copied into historical rows and no unverified debt schedule is projected. Read future outcomes only through `targets_available_at(con, cutoff_date)` from `xray.marts.targets`, which enforces label maturity. Targets are not predictor columns.

Source, tests, final clean/mart Parquet files, reports, and `reports/build_manifest.json` are versioned. The portable manifest records input/output hashes, source hashes, dependency versions, parameters, and verification; final Parquet files use Git LFS.

Intermediate tables, virtual environments, caches, agent settings, local execution history, and backups stay out of Git. A local build retains its executed source and complete manifest in `data/runs/<run_id>/`, with the replaced artifacts in `before/`. The ignored `reports/current.json` is the atomic local publication pointer; `xray.paths` pins readers to that complete run. The files at the usual `data/clean`, `data/marts`, and `reports/quality` paths are compatibility copies published individually, not a joint read transaction. A fresh clone has no local pointer and uses those versioned paths until its first build.

## Monthly cash-flow healthscore (v4)

`healthscore_v4` measures each closed month's ability to cover what the company paid plus what it owed and did not pay, on a continuous scale. Unlike the previous `healthscore_flow_v2`, an unpaid due obligation enters the denominator as a stock of the cutoff (taken once per cutoff, never summed over the window), and the buffer is the reconstructed cash LEVEL from a real anchor balance instead of an anchor-invariant historical range:

```text
T6_efectivo = P6 + D6 + deficit_servicio_6 + obligacion_vencida_m   (stock of the cutoff, taken once)
colchon_v4  = max(0, saldo_reversa_eur at the cutoff)               (cash level rebuilt backwards from a real snapshot)
colchon_aplicable = min(colchon_v4, alpha * T6_efectivo)
H       = 100 * (C6 + colchon_aplicable + k * R_hist) / (C6 + colchon_aplicable + T6_efectivo + k)
H_final = H * (1 - beta * mora_indice) * multiplicador_deuda        (both factors only downwards, bounded)
```

Unknowns are never zeros: a NULL amount is excluded from sums and the confidence level declares it. `mora_indice` and `multiplicador_deuda` only ever lower the note.

The score has four layers, each with its own module and monthly Parquet output: layer A (`xray/debt_obligation.py`) measures overdue and unattended debt point-in-time plus the debt multiplier; layer B (`xray/cash_backfill.py`) rebuilds the cash level backwards from the 2026-09-01 balance snapshot; layer C (`xray/payment_delay_v2.py`) grades arrears severity with ageing buckets, EWMA persistence and shrinkage; layer D (`xray/scoring_v4.py` with adapter `xray/scoring_io_v4.py`) applies the formula above over the six-month window (expansive up to six months, then rolling).

No-note guards (no number is ever invented): no observed cash activity in the whole history; no observed flows in the window; and payments not demonstrated (`P6 <= 0`) unless there is an overdue obligation in the cutoff — if `P6 = 0` with overdue debt outstanding, that is positive evidence of owing without paying and the company receives a LOW score, not a missing one.

Companies with a persistent zero score are EXCLUDED from the v4 perimeter by explicit product decision (33 companies). The exclusion never deletes rows: their rows keep `health_score = NULL`, `excluida = true` and the reason `excluida_nota_cero_persistente`. It is reversible at generation time with `--no-excluir-cero-persistente`. These companies have real activity (median 1,567 transactions and 471 money-in rows per company, 3,789,677,867.27 EUR in total): their zero comes from asymmetric observability (collections that fail to classify as operating income) combined with visible invoice debt, not necessarily from bad financial health.

k, alpha and beta were calibrated for v4 by measured sensitivity and stability on the v4 grid (`xray/calibrate_params_v4.py`, report in `reports/calibration_v4/report.md`; never fitted against `targets_proxy` or `label_review`): the three inherited values were REVALIDATED unchanged (k was originally measured on the v3 grid). Month-over-month stability is measured and meets the declared threshold: |dH_final| between consecutive months of the same company has p75 = 6.99 < 10 points (p50 2.36 / p90 16.12 over 10,022 pairs). The triple counting of overdue unpaid invoices (denominator, debt multiplier and arrears all punish the same signal; Spearman +0.43 between the arrears and multiplier punishments) is a known and accepted limitation (product decision: constant beta, formula unchanged).

v3 (`xray/scoring_v3.py` and its adapter) coexists untouched and remains comparable: both versions score the same 30,864 company-month grid over 24 closed months, and the published comparison restricts |delta H| to the population scored by both versions (13,159 pairs; p50 8.40 / p75 29.29 / p90 55.26).

Run each module with `python -m`; v4 is NOT registered in `xray/cli.py`:

```sh
.venv/bin/python -B -m xray.debt_obligation --output-dir reports/debt_obligation
.venv/bin/python -B -m xray.cash_backfill --output reports/cash_backfill
.venv/bin/python -B -m xray.payment_delay_v2 --output-dir reports/payment_delay_v2
.venv/bin/python -B -m xray.scoring_io_v4 --workspace data/runs/<run_id> --output reports/score_v4
.venv/bin/python -B -m xray.score_exclusions_v4
.venv/bin/python -B -m xray.score_chart_v4 --output reports/score_charts/healthscore_v4/index.html
.venv/bin/python -B -m unittest tests.test_scoring_v4 tests.test_scoring_io_v4 -v
```

Generators abort if the output path already exists, so previous reports are never overwritten; pass a new path for later exports. The chart writes a self-contained HTML comparison to `reports/score_charts/healthscore_v4/index.html` (default `--output-dir reports/score_v4`). The score is a cash-flow indicator, not a certified credit rating or an official challenge score; parameters and per-month outputs live in `reports/score_v4/summary.json`.

## Label review packet and proxy benchmark

There are no official labels in the dataset, and no health model has been trained. The archived `xray/label_review.py` (now under `.legacy/scorer_v2/xray/`) produces a blind human-review packet for the proxy labels plus a baseline benchmark of those proxies, so that future predictive work has a fixed reference point. The proxy labels (future sustained operating deficit, 60-day collection-conversion deterioration) are not business truth: they depend on the current flow classification and are only observable for a subset of rows.

`reports/label_review/round1/` is an already-generated packet; treat it as immutable. The generator module itself is archived, not deleted: restore it before generating a new packet (see `.legacy/README.md`):

```sh
mv .legacy/scorer_v2/xray/label_review.py xray/
mv .legacy/scorer_v2/tests/test_label_review.py tests/
```

With the module restored, generate a new packet with:

```sh
.venv/bin/python -B -m xray.label_review --output <new-path> --transactions 400 --cases 40
```

The generator aborts if the `--output` path already exists, so previous review packets are never overwritten; passing a new path is mandatory. Defaults are 400 transactions and 40 company-month cases. A returned review CSV (filled-in `transactions.csv` or `company_months.csv` columns) is checked without generating a packet:

```sh
.venv/bin/python -B -m xray.label_review --validate-review <reviewed-csv>
```

The packet distinguishes what a human reviewer may open from what must stay hidden:

- Blind packet (`transactions.csv`, `company_months.csv`, `cases.json`, `index.html`): the reviewer's entry point is `index.html`; keep drafts in the tab only and download the CSV when finished. `rubric.json` is a proposal awaiting human validation.
- Not blind (`transaction_selection.json`, `case_selection.json`, `baseline_metrics.json`): selection metadata and benchmark metrics; do not open while reviewing.

`manifest.json` records the fixed seed (`health-review-20260919-v1`), the dataset end (2026-08-31), the row counts (400 transactions, 40 cases, 4200 transfer-candidate pairs), the frozen-code and source SHA256 hashes, and the review status `pending_human_review` with zero gold labels. The rubric is explicitly unvalidated (`propuesta_para_revision_humana_no_validada`).

`validation_protocol.json` and `baseline_metrics.json` define the proxy benchmark, not a trained model. The objective is `target_deficit_3m`; groups are partitioned by `SHA256(seed:group_id)` (train < 70, validation < 85, test rest) with separate label cutoffs per split (train 2025-12-31, validation 2026-05-31, test 2026-08-31) and disjoint validation/test origins. Two baselines are reported: train-prevalence and persistence of the current robust deficit (abstaining when ambiguous). `proxy_inventory.json` lists the available proxy labels per name and group. These figures are a baseline for future experiments; do not use them to tune the scorer.

## Verify

```sh
bun run check
# With bun run dev running against the default demo snapshot:
bun run test:integration
```

`bun run check` includes deployment **validation only**, never a deployment: it runs `bash -n` separately on both shell files after normalizing CRLF to LF in memory, rejects malformed LF/CRLF fixtures, and exercises existing account/auth/origin checks against mocked tools and temporary script copies. Repository deployment scripts and policies are not rewritten.

Checks cover TypeScript, production builds, Rust formatting/lints, authentication, company isolation, PKCE and token rotation, MCP transport, dated shortfalls and plan constraints. The integration check signs in through the app proxy, exercises OAuth and all three tools, then revokes its grant and signs out.

For deployment, configure canonical HTTPS `APP_ORIGIN` and `API_ORIGIN` values without trailing slashes, an HTTPS `MCP_RESOURCE` ending in `/mcp`, the internal API URL and public app/landing URLs before building. Terminate TLS at the proxy and persist the database directory. SQLite supports a single deployment; multiple replicas require a shared database design.

## Jio production

Every push to `main` runs `.github/workflows/deploy-jio.yml`. A manual workflow run can redeploy a selected commit. The workflow uses a dedicated Medium Jio VM (2 vCPU, 4 GiB), builds both Next.js apps and Rust services, runs the checks, then activates the new release and verifies its public HTTPS endpoints. Builds and the Rust Parquet import happen before service restarts. Failed local health checks restore the previous release; database migrations are forward-only and must remain compatible with that release.

Repository configuration:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `JIO_API_KEY` | Jio API key for the `blaubeere` account |
| Secret | `JIO_SSH_KEY` | Private SSH key created for this VM |
| Variable | `JIO_VM_ID` | Dedicated persistent VM ID |
| Variable | `JIO_ENDPOINT` | Jio endpoint; omit to use the CLI default |

Jio publishes port 8080 for the app, API, OAuth and MCP, and port 3102 for the landing. Only the landing URL appears in the Actions completion summary. Nginx forwards app traffic to Next.js, whose rewrites proxy API/OAuth requests to Rust and MCP requests to the existing MCP listener; systemd runs the four application services as an unprivileged `blaubeere` user.

Deployment verifies that the key belongs to `blaubeere` before touching a VM. For local commands, `JIO_API_KEY` overrides `jio login`; unset a stale environment key to use the saved login. GitHub Actions uses the repository secret.

State lives under `/var/lib/blaubeere`: `data/blaubeere.db` persists identity across releases, `data/datasets/finance-HASH.sqlite` stores immutable model snapshots, `bootstrap.env` contains the initial finance account credentials, and `deployed-revision` records the healthy commit. Retrieve credentials through an authorised Jio SSH session with `sudo cat /var/lib/blaubeere/bootstrap.env`; never commit them. Provisioning and assessment overrides follow the rules above; service settings are in `runtime.env`. Retained releases permit manual rollback and should be pruned as disk usage grows. A destroyed VM needs explicit reprovisioning and a database restore; the workflow will not silently replace it.

For a manual deployment from a configured local Jio session:

```sh
JIO_VM_ID=<vm-id> GIT_REF=$(git rev-parse HEAD) bash deploy/jio.sh
```

The implementation and documentation are original. The SaaS template informed the workspace layout and native Jio deployment approach; X-Ray informed the visual direction.
