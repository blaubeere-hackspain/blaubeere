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
const homepage = await (await read(landing)).text();
assert.ok(homepage.includes(app), "Landing must link to the deployed app");
const images = new Set([...homepage.matchAll(/<img[^>]+src="([^"]+)"/g)].map(match => match[1]));
assert.ok([...images].filter(path => path.includes("-oil.")).length >= 5, "Landing must include the hero, three editorial paintings and assistant painting");
const artworkUrls = [...images].map(path => new URL(path.replaceAll("&amp;", "&"), landing).href);
for (const match of login.matchAll(/<img[^>]+src="([^"]+)"/g)) artworkUrls.push(new URL(match[1].replaceAll("&amp;", "&"), app).href);
for (const file of ["open-path-oil.png", "conversation-oil.png", "garden-chairs-oil.png"]) artworkUrls.push(`${app}/_next/image?url=%2F${file}&w=640&q=75`);
for (const path of artworkUrls) {
  const artwork = await read(path);
  assert.ok(artwork.headers.get("content-type")?.startsWith("image/"), "Painting must be served as an image");
}
for (const [origin, html] of [[app, login], [landing, homepage]]) {
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
if (login.includes("Enter demo workspace")) {
  process.env.DEMO_LOGIN = "true";
  process.env.APP_ORIGIN = process.env.API_ORIGIN = app;
  process.env.MCP_RESOURCE = `${app}/mcp`;
  await import("./check-api");
}
console.log("Production checks passed: app, landing, all paintings, built assets, OAuth origins, MCP discovery and unauthorised access.");
