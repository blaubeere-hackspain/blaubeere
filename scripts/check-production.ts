import assert from "node:assert/strict";

const [app, landing] = process.argv.slice(2);
assert.ok(app?.startsWith("https://") && landing?.startsWith("https://"));
async function read(path: string, init?: RequestInit) {
  const response = await fetch(path, { ...init, signal: AbortSignal.timeout(20_000) });
  assert.equal(response.status, 200, `Unhealthy public endpoint: ${path}`);
  return response;
}
const login = await (await read(`${app}/login`)).text();
assert.ok(login.includes("blau"));
assert.match(login, /<a href="\/demo"[^>]*>Access demo/);
assert.ok(login.includes('href="/register?returnTo=%2Fdashboard"'), "Sign-in must link to registration");
const registration = await (await read(`${app}/register`)).text();
assert.ok(registration.includes("Create account") && registration.includes('autoComplete="new-password"'), "Registration must render a new-account form");
const demo = await (await read(`${app}/demo`)).text();
assert.ok(demo.includes("Published challenge dataset") && !demo.includes("Mediterránea Supply"), "Demo must use imported companies, never the removed hardcoded workspace");
assert.ok(!demo.includes('Why this number') && !demo.includes('Why this score'), "Cash metrics must not have explanation popups");
const companies = await (await read(`${app}/api/demo/companies`)).json();
assert.ok(companies.length > 1 && companies.every((company: { data_mode: string; id: string }) => company.data_mode === "challenge" && company.id !== "DEMO_001"));
for (const company of [companies[0], companies.at(-1)]) {
  const assessment = await (await read(`${app}/api/demo/companies/${company.id}/assessment`)).json();
  assert.equal(assessment.kind, "model");
  assert.equal(assessment.company.id, company.id);
  assert.equal(assessment.provenance.model_summary.model_version, "healthscore_v4");
  assert.equal(assessment.provenance.schema_version, 3);
  assert.ok(assessment.records.every((row: { version: string; health_score: number | null; excluida: boolean }) => row.version === "healthscore_v4" && (!row.excluida || row.health_score === null)), "Serve only v4 ratings and preserve exclusions");
  const sources = assessment.provenance.files.map((file: { path: string }) => file.path);
  for (const path of ["reports/score_v4/assessments.parquet", "reports/cash_backfill/cash_backfill_monthly.parquet", "reports/payment_delay_v2/payment_delay_v2_monthly.parquet", "reports/debt_obligation/debt_obligation_monthly.parquet"]) assert.ok(sources.includes(path), `Missing v4 source: ${path}`);
  assert.ok(assessment.records.length > 1 && assessment.provenance.files.length >= 3);
  const latest = assessment.records.at(-1);
  const health = await (await read(`${app}/demo/health/${latest.as_of}?company=${company.id}`)).text();
  assert.ok(health.includes(`Health assessment · ${latest.as_of}`), "Imported rating deep links must load without a session");
}
assert.equal((await fetch(`${app}/api/companies/${companies[0].id}/assessment`)).status, 401, "Private company access must still require authentication");
assert.equal((await fetch(`${app}/api/demo/companies/DEMO_001/assessment`)).status, 404, "Public dataset endpoints cannot read private fixture data");
const homepage = await (await read(landing)).text();
assert.ok(homepage.includes(app), "Landing must link to the deployed app");
const pricing = await (await read(`${landing}/pricing`)).text();
assert.ok(pricing.includes("$99") && pricing.includes("Enterprise") && pricing.includes("Contact sales"), "Pricing must display both plans and the sales CTA");
const images = new Set([...homepage.matchAll(/<img[^>]+src="([^"]+)"/g)].map(match => match[1]));
assert.ok([...images].filter(path => path.includes("-oil.")).length >= 4, "Landing must include the hero and three editorial paintings");
assert.equal([...homepage.matchAll(/aria-controls="assistant-artwork"/g)].length, 3, "Each assistant feature must select its painting");
const artworkUrls = [...images].map(path => new URL(path.replaceAll("&amp;", "&"), landing).href);
for (const match of login.matchAll(/<img[^>]+src="([^"]+)"/g)) artworkUrls.push(new URL(match[1].replaceAll("&amp;", "&"), app).href);
for (const file of ["open-path-oil.png", "conversation-oil.png", "garden-chairs-oil.png"]) artworkUrls.push(`${app}/_next/image?url=%2F${file}&w=640&q=75`);
for (const path of artworkUrls) {
  const artwork = await read(path);
  assert.ok(artwork.headers.get("content-type")?.startsWith("image/"), "Painting must be served as an image");
}
for (const [origin, html] of [[app, login], [app, demo], [landing, homepage]]) {
  const icons = [...html.matchAll(/<link[^>]+rel="(?:icon|apple-touch-icon)"[^>]+href="([^"]+)"/g)].map(match => match[1]);
  assert.ok(icons.length >= 2, "Pages must advertise browser and touch icons");
  for (const icon of icons) {
    const response = await read(new URL(icon.replaceAll("&amp;", "&"), origin).href);
    assert.ok(response.headers.get("content-type")?.startsWith("image/"), "Brand icon must be served as an image");
  }
  const assets = new Set([...html.matchAll(/(?:src|href)="([^" ]+\.(?:js|css)(?:\?[^" ]*)?)"/g)].map(match => match[1]));
  assert.ok(assets.size > 0, "Production page must include built assets");
  for (const asset of assets) await read(new URL(asset.replaceAll("&amp;", "&"), origin).href);
}
const meta = (html: string, key: string) => html.match(new RegExp(`<meta (?:name|property)="${key}" content="([^"]+)"`))?.[1] ?? "";
const pages = [
  { origin: landing, path: "/", html: homepage, private: false },
  { origin: landing, path: "/pricing", html: pricing, private: false },
  { origin: app, path: "/login", html: login, private: true },
  { origin: app, path: "/register", html: registration, private: true },
  { origin: app, path: "/demo", html: demo, private: true },
  { origin: app, path: "/dashboard", html: await (await read(`${app}/dashboard`)).text(), private: true },
  { origin: app, path: "/connect", html: await (await read(`${app}/connect`)).text(), private: true },
];
const titles = new Set<string>();
for (const page of pages) {
  const title = meta(page.html, "og:title");
  assert.ok(title.includes("blau") && !titles.has(title), "Every page must have a distinct branded title");
  titles.add(title);
  assert.equal(meta(page.html, "twitter:title"), title);
  assert.ok(meta(page.html, "description").length >= 40, "Every page needs a useful description");
  assert.equal(meta(page.html, "twitter:card"), "summary_large_image");
  assert.equal(meta(page.html, "og:image"), `${landing}/social-preview.png`);
  assert.equal(meta(page.html, "twitter:image"), `${landing}/social-preview.png`);
  assert.ok(meta(page.html, "og:image:alt").includes("blau"));
  const canonical = new URL(page.path, page.origin).href;
  assert.equal(new URL(meta(page.html, "og:url")).href, canonical);
  const canonicalTag = page.html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  assert.ok(canonicalTag, "Every page needs a canonical URL");
  assert.equal(new URL(canonicalTag).href, canonical);
  assert.equal(meta(page.html, "robots").includes("noindex"), page.private);
}
const socialPreview = await read(`${landing}/social-preview.png`);
assert.ok(socialPreview.headers.get("content-type")?.startsWith("image/png"));
const socialBytes = Buffer.from(await socialPreview.arrayBuffer());
assert.ok(socialBytes.length < 5_000_000, "Social preview must fit the large-card image limit");
assert.equal(socialBytes.readUInt32BE(16), 1734);
assert.equal(socialBytes.readUInt32BE(20), 907);
const oauth = await (await read(`${app}/.well-known/oauth-authorization-server`)).json();
assert.equal(oauth.issuer, app);
assert.equal(oauth.authorization_endpoint, `${app}/connect`);
const mcp = await (await read(`${app}/.well-known/oauth-protected-resource/mcp`)).json();
assert.equal(mcp.resource, `${app}/mcp`);
assert.deepEqual(mcp.authorization_servers, [app]);
assert.equal((await fetch(`${app}/api/me`)).status, 401);
const challenge = await fetch(`${app}/mcp`, { method: "POST" });
assert.equal(challenge.status, 401);
assert.ok(challenge.headers.get("www-authenticate")?.includes(`${app}/.well-known/oauth-protected-resource/mcp`));
if (process.env.DEMO_LOGIN === "true") {
  process.env.APP_ORIGIN = process.env.API_ORIGIN = app;
  process.env.MCP_RESOURCE = `${app}/mcp`;
  await import("./check-api");
}
console.log("Production checks passed: app, public demo, landing, artwork, favicons, page metadata, social preview, built assets, OAuth origins, MCP discovery and unauthorised access.");
