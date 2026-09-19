import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { LoginForm } from "../apps/app/components/login-form";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents, money } from "../apps/app/lib/format";
import { CashChart, cashScale } from "../apps/app/components/cash-chart";
import demo from "../apps/app/lib/demo.json";
import { cashEvidence, MetricBreakdown, MetricWhy } from "../apps/app/components/metric-breakdown";
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

// The displayed cash explanation must reconcile to the authoritative Rust forecast.
for (const forecast of Object.values(demo.forecasts)) {
  for (const point of forecast.points) assert.equal(cashEvidence(demo.company, point.date, forecast.points.at(-1)!.date).closing, point.cash_cents);
}
assert.equal(money(1, "EUR", false, true), "€0.01", "Explanations must not round a cent discrepancy to zero");
assert.equal(money(100_029, "EUR", false, true), "€1,000.29");
const flow = demo.company.flows[0];
const evidenceCompany = { ...demo.company, flows: [
  { ...flow, id: "part-paid", amount_cents: -10_000, settled_cents: 2_000, date: "2026-08-01" },
  { ...flow, id: "late", amount_cents: 5_000, date: "2026-09-03" },
  { ...flow, id: "settled", amount_cents: 1_000, settled_cents: 1_000 },
  { ...flow, id: "future-knowledge", known_on: "2026-09-02" },
  { ...flow, id: "transfer", kind: "internal_transfer" },
] };
const evidence = cashEvidence(evidenceCompany, "2026-09-01", "2026-09-30");
assert.equal(evidence.outgoing, 8_000);
assert.equal(evidence.incoming, 0);
assert.equal(evidence.included[0].projectedDate, "2026-09-01");
assert.deepEqual(evidence.included.map(flow => flow.id), ["part-paid"]);
assert.deepEqual(evidence.laterReceipts.map(flow => flow.id), ["late"]);
assert.equal(cashEvidence(evidenceCompany, "2026-08-31", "2026-09-30").closing, demo.company.opening_cash_cents);
for (const metric of ["cash", "funding", "shortfall", "minimum", "health"]) {
  const trigger = renderToStaticMarkup(createElement(MetricWhy, { metric, onClick() {} }));
  assert.ok(trigger.includes('aria-haspopup="dialog"') && trigger.includes("Why this"));
  const popup = renderToStaticMarkup(createElement(MetricBreakdown, { company: demo.company, forecast: demo.forecasts[90], metric, open: false, onClose() {} }));
  assert.ok(popup.includes('aria-labelledby="metric-title"') && popup.includes("Evidence coverage"));
}
const unexplained = renderToStaticMarkup(createElement(MetricBreakdown, { company: { ...demo.company, drivers: [] }, forecast: demo.forecasts[90], metric: "health", open: false, onClose() {} }));
assert.ok(unexplained.includes("-12 points of change are not attributed") && unexplained.includes("No score drivers were supplied"));
const safeForecast = demo.comparison.plans[1].forecast;
assert.equal(safeForecast.first_shortfall, null);
const noBreach = renderToStaticMarkup(createElement(MetricBreakdown, { company: demo.company, forecast: safeForecast, metric: "shortfall", open: false, onClose() {} }));
assert.ok(noBreach.includes("None forecast") && !noBreach.includes("Cash at first breach"));

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
console.log("Web checks passed: offline demo entry and team sign-in, redirects, exact money input, dated horizons, chart scales, metric evidence, dialog boundaries and validation errors.");
