import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { proxy } from "../apps/app/proxy";

const { NextRequest } = createRequire(new URL("../apps/app/package.json", import.meta.url))("next/server");
const saved = { NODE_ENV: process.env.NODE_ENV, APP_ORIGIN: process.env.APP_ORIGIN, API_INTERNAL_URL: process.env.API_INTERNAL_URL };
const request = (origin = "http://localhost:3100", host = "localhost:3100", extra = {}, path = "/api/auth/register") => new NextRequest(`http://localhost:3100${path}`, { method: "POST", headers: { host, origin, ...extra } });
try {
  Object.assign(process.env, { NODE_ENV: "development", APP_ORIGIN: "http://localhost:3100", API_INTERNAL_URL: "https://production.example" });
  for (const path of ["/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/oauth/consent", "/api/connections/test/revoke"]) {
    const response = proxy(request(undefined, undefined, { cookie: "blaubeere_session=test" }, path));
    assert.equal(response.headers.get("x-middleware-request-origin"), "https://production.example");
    assert.equal(response.headers.get("x-middleware-request-cookie"), "blaubeere_session=test");
    assert.equal(response.headers.get("origin"), null, "Request overrides must not leak into response headers");
  }
  for (const origin of ["https://evil.example", "http://localhost:3101", "null", ""]) assert.equal(proxy(request(origin)).status, 403);
  assert.equal(proxy(request(undefined, "evil.example", { "x-forwarded-host": "localhost:3100" })).status, 403);
  assert.equal(proxy(request(undefined, undefined, { "sec-fetch-site": "cross-site" })).status, 403);
  assert.equal(proxy(new NextRequest("http://localhost:3100/api/auth/register", { method: "POST", headers: { host: "localhost:3100" } })).status, 403);
  assert.equal(proxy(new NextRequest("http://localhost:3100/api/demo/companies")).headers.get("x-middleware-request-origin"), null);
  process.env.APP_ORIGIN = "https://public.example";
  assert.equal(proxy(request("https://public.example", "public.example")).status, 403);
  process.env.API_INTERNAL_URL = "http://127.0.0.1:8080";
  assert.equal(proxy(request()).headers.get("x-middleware-request-origin"), null, "Local Rust mode stays unchanged");
  Object.assign(process.env, { NODE_ENV: "production", API_INTERNAL_URL: "https://production.example" });
  assert.equal(proxy(request()).headers.get("x-middleware-request-origin"), null, "The deployed app never translates origins");
} finally {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
}
console.log("Shared-backend proxy checks passed: local origin validation, session forwarding and production isolation.");
