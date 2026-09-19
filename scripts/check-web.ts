import assert from "node:assert/strict";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents } from "../apps/app/lib/format";

for (const value of [null, "https://evil.example", "//evil.example", "javascript:alert(1)", "/\\evil.example", "/login"]) assert.equal(returnPath(value), "/dashboard");
assert.equal(returnPath("/connect?state=example"), "/connect?state=example");
assert.equal(cents("100000.29", "Target"), 10000029);
for (const value of ["-1", "Infinity", "1e8", "1,000", "2.999", "", "9999999999999999999"]) assert.throws(() => cents(value, "Target"));
assert.equal(addDays("2026-08-31", 90), "2026-11-29");
const originalFetch = globalThis.fetch;
try {
  for (const status of [400, 422]) {
    globalThis.fetch = (async () => new Response("Invalid field", { status })) as typeof fetch;
    await assert.rejects(api("/companies/DEMO_001/plans"), error => error instanceof ApiError && error.status === status && error.message.includes("input format"));
  }
} finally { globalThis.fetch = originalFetch; }
console.log("Web checks passed: redirects, exact money input, dated horizons, and validation errors.");
