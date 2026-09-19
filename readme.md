# Blaubeere

Internal finance planning: understand a dated cash gap, inspect the evidence, and compare a baseline with two bounded plans. Product scope lives in [docs/PRODUCT.md](docs/PRODUCT.md), [docs/DASHBOARD.md](docs/DASHBOARD.md), and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Run locally

For the frontend demo, only Bun 1.3.12 is needed:

```sh
bun install --frozen-lockfile
bun run dev:app
```

Open http://localhost:3100/login and choose **Access demo →**, or go directly to http://localhost:3100/demo. No credentials, environment setup, API, database or deployment is required. The demo bundles synthetic data, four chart horizons and two prepared plans with fixed assumptions. Custom planning and assistant connections require a signed-in workspace.

To run the full authenticated app, install Rust 1.94 or newer as well:

```sh
bun install --frozen-lockfile
bun run setup
bun run dev
```

Setup creates an ignored `.env` and preserves existing configuration. The regular sign-in form accepts `finance@blaubeere.local` with the generated `BOOTSTRAP_PASSWORD`. The public **Access demo** link works regardless of `DEMO_LOGIN`.

| Workspace | Address | Purpose |
| --- | --- | --- |
| `apps/landing` | http://localhost:3102 | Product landing page |
| `apps/app` | http://localhost:3100 | Sign-in, cash dashboard, planning and assistant consent |
| `services/api` | http://localhost:8080 | Axum API, persistent identity, OAuth and shared finance calculations |
| `services/mcp` | http://localhost:8081/mcp | Authenticated Streamable HTTP MCP |

Both Rust services share the SQLite database in `.local/blaubeere.db`. Run commands from the repository root. Each service also has a separate `dev:api`, `dev:mcp`, `dev:app`, or `dev:landing` script.

## Try the workflow

1. Sign in with the locally provisioned account to try custom plans, or choose **Access demo →** to explore the prepared example. The demo company starts with €2m cash and a €4m payment on 28 September, before its later receivable. Its €100k cash floor makes the funding requirement €2.1m.
2. Inspect the chart, health drivers, coverage notes and source records. History is labelled reconstructed; the score and underlying records are illustrative.
3. Select **Explore a plan**. Compare the default goal with earlier collections, reduced discretionary spending and conditional funding. Constraints are checked on every projected day.
4. Set maximum new funding to zero and compare again to see the remaining gaps. Preview either plan on the cash chart; observed health and source records stay unchanged.
5. Choose monthly revenue to enter starting revenue, gross margin and collection delay explicitly. Retention and margin goals remain unavailable without their required source inputs.

## Authentication and MCP

`DEMO_LOGIN=true` enables the optional `POST /api/auth/demo` endpoint for authenticated integration and MCP demos. It is separate from the public `/demo` page, which never creates a session or calls the API. Each visitor receives a separate authenticated, 12-hour session and membership only in `DEMO_001`, which must be labelled `data_mode: "demo"`. Visitors cannot enter existing accounts or see each other’s assistant grants. Signing out ends the session; entering again creates a new visitor. Demo identities and grants remain in SQLite until explicitly removed.

The local example and MVP production deployment enable this flag. Set it to `false` in `.env` (local) or `deploy/runtime.sh` and the matching check in `deploy/jio.sh` (production, then redeploy) to disable the authenticated demo endpoint. The backend defaults to disabled when the flag is absent. The public frontend demo remains available.

Team accounts are provisioned through `BOOTSTRAP_EMAIL`, `BOOTSTRAP_PASSWORD` and comma-separated `BOOTSTRAP_COMPANIES` on API startup. There is no public registration for team accounts. Provisioning an existing email preserves its password and memberships; changing bootstrap variables does not reset that account.

Browser sign-in uses Argon2 password hashes and opaque HttpOnly sessions. Company membership is checked in the backend for each assessment and plan request. Browser mutations require the configured app origin.

Add `http://localhost:8081/mcp` as a remote MCP server in a client supporting OAuth discovery, dynamic client registration and S256 PKCE. The client opens the app's consent screen, where the user can allow or decline access. **Connect assistant** lists active grants and allows immediate revocation.

Available tools are `list_companies`, `get_cash_outlook` and `compare_plans`. The `finance:read` scope includes non-mutating plan calculations. Access tokens are bound to the MCP resource; refresh tokens rotate, and replay revokes the grant. Tokens are stored as hashes. No tool changes records or executes payments.

The public demo snapshot is generated from the same Rust finance model and bundled synthetic fixture. To refresh it after changing either:

```sh
cargo run -q -p blaubeere-api --example offline_demo > apps/app/lib/demo.json
cargo test -p blaubeere-api --example offline_demo
```

The exporter never reads runtime company files or credentials.

## Assessment inputs

The default snapshot is [fixtures/companies.json](fixtures/companies.json), a synthetic EUR example shared by the API and MCP. Set `ASSESSMENT_FILE` in `.env` to load an alternative JSON array with the same shape, then restart both services. Authorise its company IDs through account provisioning.

Amounts are signed integer cents in one reporting currency per company. This version assumes two decimal places. Each flow carries a stable ID, due date, knowledge date, settled amount, source and contractual/estimated timing. Inputs are bounded, duplicate flow IDs are rejected, settlements are deducted, internal transfers are excluded, and facts learned after the assessment date do not enter the forecast. Open overdue items are projected on the next day and disclosed as an assumption.

The upstream Data/Model workstreams own challenge CSV reconciliation, currency conversion, validated historical assessments and the official score. The app does not yet import those CSVs or compute a validated challenge score. Health, driver and coverage evidence comes from the supplied snapshot. Missing business metrics require explicit finance-team inputs.

Planning compares two deterministic candidates, not every possible plan. Funding is conditional on availability, assumed on day one at 8% annual interest, with principal repayment after the horizon. Growth applies only to incremental revenue and associated costs. Plans are held in the current browser session and are not saved to the database.

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
