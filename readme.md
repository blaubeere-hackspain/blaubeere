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

## Published Parquet assessments in the app

From the repository root, import the four published model outputs with the existing Rust API binary:

```sh
cargo run -p blaubeere-api -- import-parquet . .local/datasets "$(git rev-parse HEAD)"
```

The command prints the absolute SQLite snapshot path. Add `DATASET_DATABASE_URL=sqlite:///absolute/path/printed/above` and `DATASET_TEAM_EMAIL=finance@blaubeere.local` to `.env`, then restart the Rust services. The explicitly configured existing team account receives memberships in the imported companies. Demo visitors remain restricted to `DEMO_001`.

Rust streams `reports/score_v3/assessments.parquet`, `reports/cash_position/cash_position_monthly.parquet`, `reports/payment_delay/payment_delay_monthly.parquet` and `reports/transfer_resolution_v2/resolution.parquet`. It validates and indexes a private, immutable SQLite snapshot, preserves nulls and reason codes, and records file hashes and source revision. An unchanged batch reuses its snapshot; failed imports cannot replace a healthy snapshot. API and MCP share the same Rust query and access-control logic. No Redis or database service is needed on the single VM.

`GET /api/companies/:id/assessment` returns `kind: "model"`, monthly `records` joined with cash/payment evidence, and provenance for imported companies. Model amounts are EUR values, unlike the integer-cent forecast contract. The UI links each monthly score to its own dated explanation; it does not interpolate daily scores. Relative cumulative cash movement is not a bank balance. Planning is unavailable for these companies until verified opening cash and future obligations are supplied. The independent `/demo` remains an illustrative, frontend-only experience.

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

## Verify

```sh
bun run check
# With bun run dev running against the default demo snapshot:
bun run test:integration
```

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
