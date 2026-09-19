import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { LoginForm } from "../apps/app/components/login-form";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents } from "../apps/app/lib/format";
import { CashChart, cashScale } from "../apps/app/components/cash-chart";
import demo from "../apps/app/lib/demo.json";
import { outsideDialog } from "../apps/app/components/dialog";

const requireApp = createRequire(new URL("../apps/app/package.json", import.meta.url));
const { createElement } = requireApp("react");
const { renderToStaticMarkup } = requireApp("react-dom/server");
const login = renderToStaticMarkup(createElement(LoginForm));
assert.ok(login.includes('name="email"') && login.includes('type="password"'), "The normal sign-in form stays visible");
assert.match(login, /<a href="\/demo"[^>]*>Access demo/, "Demo must always be a direct link, independent of credentials and server flags");
assert.ok(login.indexOf('href="/demo"') > login.indexOf("</form>"), "Demo entry belongs below the sign-in form");
const chart = renderToStaticMarkup(createElement(CashChart, { company: demo.company, forecast: demo.forecasts[90] }));
assert.ok(chart.includes('<title id="cash-chart-title">Daily closing cash, history and 90-day outlook</title>'), "The chart title must survive server rendering for hydration and assistive technology");

for (const value of [null, "https://evil.example", "//evil.example", "javascript:alert(1)", "/\\evil.example", "/login"]) assert.equal(returnPath(value), "/dashboard");
assert.equal(returnPath("/connect?state=example"), "/connect?state=example");
assert.equal(cents("100000.29", "Target"), 10000029);
for (const value of ["-1", "Infinity", "1e8", "1,000", "2.999", "", "9999999999999999999"]) assert.throws(() => cents(value, "Target"));
assert.equal(addDays("2026-08-31", 90), "2026-11-29");
for (const [low, high] of [[-200_000_000, 310_000_000], [0, 0], [-1, 1], [-10_000_000, 0], [0, 1_000_000_000_000]]) {
  const scale = cashScale(low, high);
  assert.ok(scale.min <= low && scale.max >= high && scale.max > scale.min, "Chart must contain the full cash range, including a flat zero balance");
  assert.ok(scale.ticks.includes(0), "The cash chart must show the zero baseline");
  assert.ok(scale.ticks.length <= 7 && scale.ticks.every(Number.isSafeInteger), "Cash ticks must stay readable and use exact cents");
}
const bounds = { left: 20, right: 300, top: 40, bottom: 500 };
assert.equal(outsideDialog(bounds, 20, 40), false, "Dialog edges and padding are not its backdrop");
assert.equal(outsideDialog(bounds, 150, 200), false);
for (const [x, y] of [[19, 100], [301, 100], [100, 39], [100, 501]]) assert.equal(outsideDialog(bounds, x, y), true);
const originalFetch = globalThis.fetch;
try {
  for (const status of [400, 422]) {
    globalThis.fetch = (async () => new Response("Invalid field", { status })) as typeof fetch;
    await assert.rejects(api("/companies/DEMO_001/plans"), error => error instanceof ApiError && error.status === status && error.message.includes("input format"));
  }
} finally { globalThis.fetch = originalFetch; }
console.log("Web checks passed: offline demo entry and team sign-in, redirects, exact money input, dated horizons, chart scales, dialog boundaries and validation errors.");
