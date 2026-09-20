# ChatGPT company picker

## Implementation

Official guides: [Add UI to an MCP server](https://developers.openai.com/plugins/build/chatgpt-ui), [reference](https://developers.openai.com/plugins/reference), [troubleshooting](https://developers.openai.com/plugins/deploy/troubleshooting).

1. ChatGPT calls the OAuth-protected `list_companies` tool through the Next.js `/mcp` reverse proxy to Rust.
2. Rust returns the signed-in account's companies in `structuredContent`, without a UI template. ChatGPT then calls `render_company_picker` with the chosen company IDs. Rust rechecks all memberships and returns authoritative company summaries; only this render tool declares `_meta.ui.resourceUri` (`ui://blau/company-picker-v4.html`).
3. ChatGPT reads that resource. Rust returns self-contained HTML with MIME type `text/html;profile=mcp-app` and a restrictive CSP. No company data or credentials are embedded in the template.
4. The iframe performs `ui/initialize` / `ui/notifications/initialized`, receives `ui/notifications/tool-result`, and renders company buttons.
5. Selecting a company calls `get_company_health` through the host's `tools/call` bridge. Rust checks OAuth and that company's membership again, then uses the same summary logic as the dashboard.

The template lives in `services/mcp/src/company-picker.html`; descriptors, resources and transport authorization live in `services/mcp/src/main.rs`. The separate HTML artifact generated inside ChatGPT is a snapshot, not this live MCP widget.

## Investigation on 20 September 2026

Fixed and verified:

- **Stale Rust deployment artifacts.** Release directories shared `CARGO_TARGET_DIR`. Cargo reused dep-info pointing at retained older sources, so a deployment could report a new revision while running an older binary. Reproduced locally with two source directories: the second build printed the first version. Cleaning only the workspace packages before building corrected it. `deploy/runtime.sh` now does this, preserving the dependency cache.
- **Discovery blocked by account authentication.** ChatGPT's refresh action failed alongside a POST authorization rejection. Protocol discovery and allowlisted static templates can now load without a user token; all tool calls still require authentication. Resource URLs from existing conversations remain supported.
- **Widget contract drift.** The current descriptor advertises the standard resource URI and output schema; the resource uses the standard MIME type; initialization no longer skips the MCP Apps handshake merely because `window.openai` exists.

Verification: `cargo test -p blaubeere-mcp`, Clippy, web checks and production smoke checks passed. Production deployment [35486214438](https://github.com/blaubeere-hackspain/blaubeere/actions/runs/35486214438) rebuilt both Rust packages and passed authenticated OAuth/company-isolation checks. Public production resource reads return HTTP 200 with the v4 HTML and metadata.

Still unresolved: ChatGPT displays “No se ha podido abrir esta aplicación” in both Chat and Work mode, before mounting an iframe. Server diagnostics confirm ChatGPT fetched the current v4 template after the fixes; no component console error is available because there is no component iframe. This narrows the remaining problem to resource ingestion/render setup, but does **not** establish a particular ChatGPT defect or prove which field it rejects. Further changes should follow a concrete loader error, not another blind MIME/URI change.

Reproduction conversation: `https://chatgpt.com/c/6aaf5146-5e30-83ed-afdc-ca28a39d976f` (private to the signed-in user). Original prompt: “Blau: abre el selector interactivo nativo de mis empresas usando list_companies.” After refreshing actions, test the decoupled flow: “Blau: llama list_companies y después render_company_picker con los IDs devueltos.” App ID: `asdk_app_6aaf3ced61288191a0a4f66371e9cb95`. Server diagnostics [35486377319](https://github.com/blaubeere-hackspain/blaubeere/actions/runs/35486377319) show v4 resource reads around 03:22 UTC. No tokens or private company data are needed to reproduce static template loading.
