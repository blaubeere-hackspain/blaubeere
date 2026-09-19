import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { LoginForm } from "../apps/app/components/login-form";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents, money } from "../apps/app/lib/format";
import { CashChart, cashScale } from "../apps/app/components/cash-chart";
import demo from "../apps/app/lib/demo.json";
import { HealthExplanation } from "../apps/app/components/health-explanation";
import { healthHref, healthTimeline, validHealthDate } from "../apps/app/lib/health";
import type { Company } from "../apps/app/lib/types";
import { outsideDialog } from "../apps/app/components/dialog";

const requireApp = createRequire(new URL("../apps/app/package.json", import.meta.url));
const { createElement } = requireApp("react");
const { renderToStaticMarkup } = requireApp("react-dom/server");
const login = renderToStaticMarkup(createElement(LoginForm));
assert.ok(login.includes('name="email"') && login.includes('type="password"'), "The normal sign-in form stays visible");
assert.match(login, /<form\b[^>]*method="post"/, "A submit before hydration must never put credentials in the URL");
assert.match(login, /<a href="\/demo"[^>]*>Access demo/, "Demo must always be a direct link, independent of credentials and server flags");
assert.ok(login.indexOf('href="/demo"') > login.indexOf("</form>"), "Demo entry belongs below the sign-in form");
assert.ok(login.includes('href="/register?returnTo=%2Fdashboard"'), "Sign-in must offer account registration");
const registration = renderToStaticMarkup(createElement(LoginForm, { register: true, returnTo: "/connect?state=keep-me" }));
assert.ok(registration.includes('autoComplete="new-password"') && registration.includes('minLength="12"') && registration.includes("Create account"));
assert.ok(registration.includes('href="/login?returnTo=%2Fconnect%3Fstate%3Dkeep-me"'), "Switching auth forms must preserve the assistant authorization flow");
const unsafeRegistration = renderToStaticMarkup(createElement(LoginForm, { register: true, returnTo: "https://evil.example" }));
assert.ok(!unsafeRegistration.includes("evil.example") && unsafeRegistration.includes('href="/login?returnTo=%2Fdashboard"'));
const chart = renderToStaticMarkup(createElement(CashChart, { company: demo.company, forecast: demo.forecasts[90] }));
assert.ok(chart.includes('<title id="cash-chart-title">Daily closing cash, history and 90-day outlook</title>'), "The chart title must survive server rendering for hydration and assistive technology");

// A historical rating must resolve to its own saved model response, never today's drivers.
const timeline = healthTimeline(demo.company);
assert.equal(timeline.length, 6);
assert.equal(timeline.filter(point => point.date === demo.company.assessment_date).length, 1);
assert.deepEqual(timeline.map(point => point.score), [68, 65, 61, 59, 58, 56]);
for (const point of timeline) {
  const page = renderToStaticMarkup(createElement(HealthExplanation, { company: demo.company, scoreDate: point.date, demo: true }));
  assert.ok(page.includes(point.assessment!.health.note), "Each page must show the saved explanation for its own date");
  assert.ok(page.includes('aria-label="Assessment navigation"') && page.includes('id="score-date"'));
  assert.ok(!page.includes('<dialog'), "A score opens a page, not a popup");
}
const legacy: Company = { ...demo.company, health_assessments: undefined };
const historical = renderToStaticMarkup(createElement(HealthExplanation, { company: legacy, scoreDate: "2026-07-02" }));
assert.ok(historical.includes("Explanation not available") && !historical.includes(demo.company.drivers[0].detail), "Missing historical explanations cannot borrow current evidence");
assert.ok(healthTimeline(legacy).at(-1)?.assessment, "Existing current assessments remain supported");
const noDate = renderToStaticMarkup(createElement(HealthExplanation, { company: demo.company, scoreDate: "2026-07-03" }));
assert.ok(noDate.includes("No score recorded for this date"));
const missing = structuredClone(demo.company) as Company;
const latest = missing.health_assessments!.at(-1)!;
latest.drivers = [{ label: "Unweighted reason", detail: "No numerical attribution supplied", points: null, source_ids: ["absent", "future"] }];
latest.flows = [{ ...demo.company.flows[0], id: "future", known_on: "2026-09-01", source: "DO NOT SHOW FUTURE EVIDENCE" }];
const gaps = renderToStaticMarkup(createElement(HealthExplanation, { company: missing, scoreDate: latest.date }));
assert.ok(gaps.includes("Change without numerical attribution") && gaps.includes("No numerical effect was returned"));
assert.ok(gaps.includes("Source absent was not included") && !gaps.includes("DO NOT SHOW FUTURE EVIDENCE"));
assert.equal(healthHref("company/id", "2026-08-31"), "/dashboard/health/2026-08-31?company=company%2Fid");
for (const value of ["2026-02-30", "2026-08-31T12:00:00Z", "invalid", "2026-13-01"]) assert.equal(validHealthDate(value), false);
assert.equal(validHealthDate("2026-08-31"), true);
assert.equal(returnPath("/dashboard/health/2026-08-31?company=DEMO_001"), "/dashboard/health/2026-08-31?company=DEMO_001");
assert.equal(money(1, "EUR", false, true), "€0.01");
assert.equal(money(100_029, "EUR", false, true), "€1,000.29");

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
console.log("Web checks passed: offline demo entry and team sign-in, redirects, exact money input, dated horizons, chart scales, dated health explanations, dialog boundaries and validation errors.");
