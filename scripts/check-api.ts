import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";

const api = process.env.API_ORIGIN ?? "http://localhost:8080";
const app = process.env.APP_ORIGIN ?? "http://localhost:3100";
const mcp = process.env.MCP_RESOURCE ?? "http://localhost:8081/mcp";
const password = process.env.BOOTSTRAP_PASSWORD;
const demo = process.env.DEMO_LOGIN === "true";
assert.ok(demo || password, "Run bun run setup first, then start the services.");
const credentials = { email: process.env.BOOTSTRAP_EMAIL, password };
const login = await fetch(`${app}/api/auth/${demo ? "demo" : "login"}`, { method: "POST", headers: { "Content-Type": "application/json", Origin: app }, body: JSON.stringify(demo ? {} : credentials) });
assert.equal(login.status, 200, await login.clone().text());
const cookie = login.headers.get("set-cookie")!.split(";")[0];
assert.ok(login.headers.get("set-cookie")!.includes("HttpOnly"));
const browserHeaders = { Cookie: cookie, Origin: app, "Content-Type": "application/json" };
for (const path of ["/login", "/register"]) {
  const signedIn = await fetch(`${app}${path}`, { headers: { Cookie: cookie }, redirect: "manual" });
  assert.equal(signedIn.status, 307);
  assert.equal(new URL(signedIn.headers.get("location")!, app).pathname, "/dashboard");
  const expired = await fetch(`${app}${path}`, { headers: { Cookie: "blaubeere_session=expired" }, redirect: "manual" });
  assert.equal(expired.status, 200);
}
assert.equal((await fetch(`${api}/api/companies/NOT_AUTHORISED/assessment`, { headers: browserHeaders })).status, 403);
const assessment = await (await fetch(`${app}/api/companies/DEMO_001/assessment`, { headers: browserHeaders })).json();
assert.equal(assessment.forecast.funding_needed_cents, 210000000);
const metadata = await (await fetch(`${api}/.well-known/oauth-authorization-server`)).json();
assert.deepEqual(metadata.code_challenge_methods_supported, ["S256"]);
assert.equal((await fetch(mcp, { method: "POST" })).status, 401);
const client = await (await fetch(`${api}/oauth/register`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_name: "Local smoke check", redirect_uris: ["http://127.0.0.1:49999/callback"] }) })).json();
const verifier = randomBytes(32).toString("base64url");
const request = { client_id: client.client_id, redirect_uri: "http://127.0.0.1:49999/callback", response_type: "code", code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", resource: mcp, scope: "finance:read", state: "local-test" };
const consent = await fetch(`${app}/api/oauth/consent`, { method: "POST", headers: browserHeaders, body: JSON.stringify({ request, approve: true }) });
assert.equal(consent.status, 200, await consent.clone().text());
const redirect = new URL((await consent.json()).redirect);
assert.equal(redirect.searchParams.get("state"), "local-test");
const form = new URLSearchParams({ grant_type: "authorization_code", client_id: client.client_id, resource: mcp, code: redirect.searchParams.get("code")!, redirect_uri: request.redirect_uri, code_verifier: verifier });
const tokenResponse = await fetch(`${api}/oauth/token`, { method: "POST", body: form });
assert.equal(tokenResponse.status, 200, await tokenResponse.clone().text());
const tokens = await tokenResponse.json();
assert.equal((await fetch(`${api}/oauth/token`, { method: "POST", body: form })).status, 400, "Codes must be single use");
const headers = { Authorization: `Bearer ${tokens.access_token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25" };
let id = 0;
async function rpc(method: string, params: unknown) {
  const response = await fetch(mcp, { method: "POST", headers, body: JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params }) });
  assert.equal(response.status, 200, await response.clone().text());
  return response.json();
}
assert.ok((await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "local-smoke", version: "1.0" } })).result);
assert.ok((await rpc("tools/list", {})).result.tools.some((tool: {name: string}) => tool.name === "get_company_health"));
const tools = await rpc("tools/call", { name: "list_companies", arguments: {} });
const accessibleCompanies = JSON.parse(tools.result.content[0].text);
assert.ok(accessibleCompanies.some((company: {id: string}) => company.id === "DEMO_001"));
assert.deepEqual(tools.result.structuredContent.companies, accessibleCompanies);
const picker = await rpc("tools/call", { name: "render_company_picker", arguments: { company_ids: accessibleCompanies.map((company: {id: string}) => company.id) } });
assert.deepEqual(picker.result.structuredContent.companies, accessibleCompanies);
assert.ok((await rpc("tools/call", { name: "render_company_picker", arguments: { company_ids: ["NOT_AUTHORISED"] } })).error);
for (const company of accessibleCompanies.filter((company: {data_mode: string}) => company.data_mode === "challenge").slice(0, 3)) {
  const health = await rpc("tools/call", { name: "get_company_health", arguments: { company_id: company.id } });
  assert.equal(health.result.structuredContent.company.id, company.id);
  const month = health.result.structuredContent.as_of.slice(0, 7);
  const rest = await (await fetch(`${app}/api/companies/${company.id}/health?month=${month}`, { headers: browserHeaders })).json();
  assert.deepEqual(rest.health, health.result.structuredContent.health, "REST and MCP use the same health and alerts");
  assert.deepEqual(rest.metrics, health.result.structuredContent.metrics);
}
assert.ok((await rpc("tools/call", { name: "get_company_health", arguments: { company_id: "NOT_AUTHORISED" } })).error);
const resource = await rpc("resources/read", { uri: "ui://blau/company-picker-v5.html" });
assert.equal(resource.result.contents[0].mimeType, "text/html;profile=mcp-app");
assert.ok((await rpc("tools/call", { name: "get_cash_outlook", arguments: { company_id: "NOT_AUTHORISED" } })).error);
const goal = { metric: "min_cash", target_cents: 10000000, cash_floor_cents: 10000000, deadline: "2026-11-29", max_collection_days: 14, max_spend_reduction_pct: 10, max_funding_cents: 250000000, max_growth_pct: 0, business: null };
const plans = JSON.parse((await rpc("tools/call", { name: "compare_plans", arguments: { company_id: "DEMO_001", goal } })).result.content[0].text);
assert.equal(plans.plans[0].qualifies, false);
assert.equal(plans.plans[1].qualifies, true);
await fetch(`${api}/oauth/revoke`, { method: "POST", body: new URLSearchParams({ client_id: client.client_id, token: tokens.access_token }) });
assert.equal((await fetch(mcp, { method: "POST", headers, body: "{}" })).status, 401);
await fetch(`${app}/api/auth/logout`, { method: "POST", headers: browserHeaders, body: "{}" });
assert.equal((await fetch(`${app}/api/me`, { headers: browserHeaders })).status, 401);
console.log("Integration checks passed: login, company isolation, OAuth consent/PKCE, MCP tools, planning, revocation, logout.");
