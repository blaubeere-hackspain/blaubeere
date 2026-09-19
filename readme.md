# Blaubeere

Internal finance planning: understand a dated cash gap, inspect the evidence, and compare a baseline with two bounded plans. Product scope lives in [docs/PRODUCT.md](docs/PRODUCT.md), [docs/DASHBOARD.md](docs/DASHBOARD.md), and [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

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

The upstream Data/Model workstreams own challenge CSV reconciliation, currency conversion, validated historical assessments and the official score. The app does not yet import those CSVs or compute a validated challenge score. Health, driver and coverage evidence comes from the supplied snapshot. Missing business metrics require explicit finance-team inputs.

Planning compares two deterministic candidates, not every possible plan. Funding is conditional on availability, assumed on day one at 8% annual interest, with principal repayment after the horizon. Growth applies only to incremental revenue and associated costs. Plans are held in the current browser session and are not saved to the database.

## Verify

```sh
bun run check
# With bun run dev running against the default demo snapshot:
bun run test:integration
```

Checks cover TypeScript, production builds, Rust formatting/lints, authentication, company isolation, PKCE and token rotation, MCP transport, dated shortfalls and plan constraints. The integration check signs in through the app proxy, exercises OAuth and all three tools, then revokes its grant and signs out.

For deployment, configure canonical HTTPS `APP_ORIGIN` and `API_ORIGIN` values without trailing slashes, an HTTPS `MCP_RESOURCE` ending in `/mcp`, the internal API URL and public app/landing URLs before building. Terminate TLS at the proxy and persist the database directory. SQLite supports a single deployment; multiple replicas require a shared database design.

The implementation and documentation are original. The SaaS template informed the `apps/` and `services/` layout only; X-Ray informed the visual direction.
