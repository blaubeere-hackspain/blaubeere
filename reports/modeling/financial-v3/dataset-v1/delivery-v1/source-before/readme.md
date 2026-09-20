> **Goal v3 pending — source audit and development design, not a new validated score.** [Audit evidence](reports/modeling/financial-v3/audit-summary.json) and [proposed financial/API contract](reports/modeling/financial-v3/design-proposal.md) are isolated from v1/v2. Incomplete cash, debt or settlement evidence requires a null headline; reconstructed retrospective balances cannot become earlier as-of signals. No untouched confirmatory test is identified: all 50 v1 final-test groups stay excluded, the v2 reserve is consumed, and new future/external data is required. No model, labels, evaluation or API/app implementation was run for this phase; old artifacts/results and the delivery documented below remain intact. Read-only preservation check: `.venv/bin/python -B scripts/financial_v3_audit.py --check`.

# Blaubeere

Internal finance planning: inspect a company's evidence and operating trajectory; keep the existing cash demo for dated gaps and comparison of a baseline with two bounded plans. **V1 proxy delivery is historical and sealed. V2 now has a separate trajectory API/MCP/dashboard mode using the sealed export; anticipation is not validated and automatic predictive alerts remain disabled.** Product scope lives in [docs/PRODUCT.md](docs/PRODUCT.md), [docs/DASHBOARD.md](docs/DASHBOARD.md), and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Operating trajectory v2 — descriptive review only

[Goal v2 and the exact protocol design](reports/planning/financial-scoring-plan.md#preregistro-v2) fix rules and input identities before any new outcome inspection. The [versioned immutable JSON](reports/modeling/trajectory-v2/protocol.json) is materialised with an [exclusive hash receipt](reports/modeling/trajectory-v2/protocol-receipt.json). Contract fixtures and `python3 -B scripts/trajectory_contract.py --check` passed: byte hashes and assignment metadata only, 200 included groups and all 50 v1 final-test groups excluded. On its first successful check the verifier exclusively creates the receipt; subsequent checks verify it without rewriting. This phase does not run the data pipeline, train, infer, compute events/metrics or reopen the v1 test. The existing export and sealed development/reserved results now feed the app; this integration does not execute any model, inference, event-label or backtest phase.

The addition stays inside the current dashboard, with the cash demo and planning panel preserved. No rebrand or four new pages. It shows every company × closed month from September 2024 through August 2026, retaining gaps, a robust current deficit/non-deficit state and a separate `improving` / `stable` / `deteriorating` / `insufficient_evidence` direction. Known receipts/payments ratios, base/trim uncertainty and arithmetic changes explain the evidence, not causal effects or model points. A deficit is not automatically deterioration, and missing evidence is not bad health.

The frozen v1 probability is a separate `target_deficit_3m` overlay, only for eligible April–August 2026 month-ends after the 31 March training/selection label cutoff; earlier dates stay unavailable. This is retrospective simulation with a model physically trained in September, not proof of live historical issuance. No 0–100 official score, fabricated cash stock, new fits or new evaluation of the v1 target.

V2 distinguishes provisional watch from a simulated alert confirmed at the second consecutive same-sign month-end, without backdating. The sealed report compares this candidate against a one-month baseline: reserved improvement 2/37 matches, 75 alerts, 48 false and 25 censored; deterioration 1/41, 73 alerts, 50 false and 22 censored. False-alert shares are 96% and 98.039%, not FPR; baseline matches are 3/37 and 7/41. **Anticipation not validated.** Only three candidate matches support the lead summary. Group intervals are aggregate, not individual probability CIs; censored rows remain unknown. V1 AP is a different target and is not a v2 event metric. The [v2 evaluation and delivery report (Spanish)](reports/modeling/trajectory-v2/assessment/report.md) records both methods, censoring, group intervals and the three unaltered development cases.

Exclude all 50 v1 final-test groups from v2 development, event/alert evaluation and case selection. Development uses origins March–September 2025 and outcomes through December; the temporal reserve uses origins March–May 2026 and outcomes through August in the 200 train/validation groups. This is known-company evaluation on a shared synthetic corpus, not a new independent group holdout. Demo improvement, deterioration and recovered-dip cases come only from development; report unavailable types rather than borrowing reserve cases. All availability remains retrospective, not certified `known_on`.

The user has communicated that the official test may change or be cancelled; this is **not organiser confirmation**. Official target, metric and submission format remain unconfirmed. Existing commands below document the existing system, not authorisation to run model/data workflows during this application-verification phase.

### Load and verify the existing trajectory export

Set process-only `ASSESSMENT_FILE` to the absolute `reports/modeling/trajectory-v2/assessment/companies.json` path in both Rust services. Keep its sibling `manifest.json`: startup pins both SHA256 identities, validates the exact nested company schema and temporal/count invariants, and shares a cached local dictionary. No HTTP/path reference resolver exists. The 103,942,971-byte export stays server-side; responses contain one authorised company and the selected history, never all companies' trajectories.

`GET /api/companies/{id}/assessment?as_of=2025-05-31` and MCP `get_cash_outlook({company_id, as_of})` accept only exported closed months. Omitted/null MCP dates mean the explicitly labelled latest snapshot; malformed, non-month-end and out-of-range dates fail rather than falling back. Cash and v1 proxy accept only their original snapshot date. Trajectory responses add `selected_as_of`, `current_point`, `available_as_of` and safely resolved `trajectory_metadata`; historical company dates and points are filtered, full cases are hidden until confirmation, and global backtest metrics are omitted at historical cuts. The latest report is labelled as later retrospective methodology. Forecast remains null and all plan comparisons reject trajectory input.

The authorised-only **Example retrospective development** selector retains `COMP_0009` deterioration and `COMP_0179` improvement (visible from 2025-05-31), and `COMP_0028` recovered dip (2025-06-30). Their onset is April 2025, their histories start September 2024, and no development-date probability is fabricated. Cases are not replaced to favour successful alerts.

```sh
cargo build --workspace
TRAJECTORY_ASSESSMENT_FILE="$PWD/reports/modeling/trajectory-v2/assessment/companies.json" cargo test --workspace actual_trajectory -- --ignored
bun run test:trajectory:e2e
bun run check
```

The trajectory E2E uses existing Playwright and sandboxed system Chromium, bounded local ports/readiness, fresh temporary identities and unlogged random passwords. It verifies all three cases, all their historical API cuts, eligible April–August overlay, gaps, authorisation and old cash/v1 modes. Only new timestamped evidence is written under `reports/modeling/trajectory-v2/app-verification/`; it does not rewrite the v1 browser summary or any sealed result. No model/backtest replay is part of these application commands. Overall goal acceptance remains a separate coordinating decision.

## Run locally

Requires Bun 1.3.12 and Rust 1.94 or newer.

```sh
bun install --frozen-lockfile
bun run setup
bun run dev
```

Setup creates an ignored `.env` with a random local password and prints the sign-in details. Existing configuration is preserved. Sign in as `finance@blaubeere.local` with the generated `BOOTSTRAP_PASSWORD`.

| Workspace | Address | Purpose |
| --- | --- | --- |
| `apps/landing` | http://localhost:3102 | Product landing page |
| `apps/app` | http://localhost:3100 | Sign-in, cash dashboard, planning and assistant consent |
| `services/api` | http://localhost:8080 | Axum API, persistent identity, OAuth and shared finance calculations |
| `services/mcp` | http://localhost:8081/mcp | Authenticated Streamable HTTP MCP |

Both Rust services share the SQLite database in `.local/blaubeere.db`. Run commands from the repository root. Each service also has a separate `dev:api`, `dev:mcp`, `dev:app`, or `dev:landing` script.

## Try the workflow

1. Open the app and sign in. The demo company starts with €2m cash and a €4m payment on 28 September, before its later receivable. Its €100k cash floor makes the funding requirement €2.1m.
2. Inspect the chart, health drivers, coverage notes and source records. History is labelled reconstructed; the score and underlying records are illustrative.
3. Select **Explore a plan**. Compare the default goal with earlier collections, reduced discretionary spending and conditional funding. Constraints are checked on every projected day.
4. Set maximum new funding to zero and compare again to see the remaining gaps. Preview either plan on the cash chart; observed health and source records stay unchanged.
5. Choose monthly revenue to enter starting revenue, gross margin and collection delay explicitly. Retention and margin goals remain unavailable without their required source inputs.

## Authentication and MCP

Accounts are provisioned through `BOOTSTRAP_EMAIL`, `BOOTSTRAP_PASSWORD` and comma-separated `BOOTSTRAP_COMPANIES` on API startup. There is no public registration. Provisioning an existing email preserves its password and memberships; changing bootstrap variables does not reset that account.

Browser sign-in uses Argon2 password hashes and opaque HttpOnly sessions. Company membership is checked in the backend for each assessment and plan request. Browser mutations require the configured app origin.

Add `http://localhost:8081/mcp` as a remote MCP server in a client supporting OAuth discovery, dynamic client registration and S256 PKCE. The client opens the app's consent screen, where the user can allow or decline access. **Connect assistant** lists active grants and allows immediate revocation.

Available tools are `list_companies`, `get_cash_outlook` and `compare_plans`. The `finance:read` scope includes non-mutating plan calculations. Access tokens are bound to the MCP resource; refresh tokens rotate, and replay revokes the grant. Tokens are stored as hashes. No tool changes records or executes payments.

## Assessment inputs

The default snapshot is [fixtures/companies.json](fixtures/companies.json), a synthetic EUR example shared by the API and MCP. Set `ASSESSMENT_FILE` in `.env` to load an alternative JSON array with the same shape, then restart both services. Authorise its company IDs through account provisioning.

Amounts are signed integer cents in one reporting currency per company. This version assumes two decimal places. Each flow carries a stable ID, due date, knowledge date, settled amount, source and contractual/estimated timing. Inputs are bounded, duplicate flow IDs are rejected, settlements are deducted, internal transfers are excluded, and facts learned after the assessment date do not enter the forecast. Open overdue items are projected on the next day and disclosed as an assumption.

The upstream Data/Model workstreams own challenge CSV reconciliation, currency conversion and the scope of historical assessments. No official target, metric, format or validated challenge score is confirmed. The app does not import those CSVs directly; the sealed proxy export is a distinct mode. Any health field in the cash fixture is illustrative; driver and coverage evidence comes from the supplied snapshot. Missing business metrics require explicit finance-team inputs.

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

## Challenge dataset pipeline

The Python/DuckDB pipeline is separate from the Rust planning services: publishing its Parquet tables does not replace the app's illustrative assessment snapshot or provide an official challenge score. The source dataset is described in [data_dictionary.md](data_dictionary.md); the current analytical contracts and limitations are in [reports/vistas_y_hallazgos.md](reports/vistas_y_hallazgos.md).

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

Every push to `main` runs `.github/workflows/deploy-jio.yml`. A manual workflow run can redeploy a selected commit. The workflow uses a dedicated Medium Jio VM (2 vCPU, 4 GiB), builds both Next.js apps and Rust services, runs the checks, then activates the new release and verifies its public HTTPS endpoints. Builds happen before service restarts. Failed local health checks restore the previous release; database migrations are forward-only and must remain compatible with that release.

Repository configuration:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `JIO_API_KEY` | Jio API key for the `blaubeere` account |
| Secret | `JIO_SSH_KEY` | Private SSH key created for this VM |
| Variable | `JIO_VM_ID` | Dedicated persistent VM ID |
| Variable | `JIO_ENDPOINT` | Jio endpoint; omit to use the CLI default |

Jio publishes port 8080 for the app, API, OAuth and MCP, and port 3102 for the landing. Deployment URLs appear in the Actions run summary. Nginx forwards to private listeners; systemd runs the four application services as an unprivileged `blaubeere` user.

Deployment verifies that the key belongs to `blaubeere` before touching a VM. For local commands, `JIO_API_KEY` overrides `jio login`; unset a stale environment key to use the saved login. GitHub Actions uses the repository secret.

State lives under `/var/lib/blaubeere`: `data/blaubeere.db` persists across releases, `bootstrap.env` contains the initial finance account credentials, and `deployed-revision` records the healthy commit. Retrieve credentials through an authorised Jio SSH session with `sudo cat /var/lib/blaubeere/bootstrap.env`; never commit them. Provisioning and assessment overrides follow the rules above; service settings are in `runtime.env`. Retained releases permit manual rollback and should be pruned as disk usage grows. A destroyed VM needs explicit reprovisioning and a database restore; the workflow will not silently replace it.

For a manual deployment from a configured local Jio session:

```sh
JIO_VM_ID=<vm-id> GIT_REF=$(git rev-parse HEAD) bash deploy/jio.sh
```

The implementation and documentation are original. The SaaS template informed the workspace layout and native Jio deployment approach; X-Ray informed the visual direction.
