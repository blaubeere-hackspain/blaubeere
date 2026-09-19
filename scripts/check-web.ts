import assert from "node:assert/strict";
import "./check-welcome";
import "./check-scroll-reveals";
import { createRequire } from "node:module";
import { LoginForm } from "../apps/app/components/login-form";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents, money } from "../apps/app/lib/format";
import { cashScale } from "../apps/app/components/cash-chart";
import companies from "../fixtures/companies.json";
import { HealthExplanation } from "../apps/app/components/health-explanation";
import { healthHref, healthTimeline, validHealthDate } from "../apps/app/lib/health";
import type { Company } from "../apps/app/lib/types";
import { outsideDialog } from "../apps/app/components/dialog";
import { ModelDashboard, modelNumber, modelReason } from "../apps/app/components/model-dashboard";
import type { ModelAssessment, ModelRecord } from "../apps/app/lib/types";

const requireApp = createRequire(new URL("../apps/app/package.json", import.meta.url));
const { createElement } = requireApp("react");
const { renderToStaticMarkup } = requireApp("react-dom/server");
const demo = { company: companies[0] as Company };
const record: ModelRecord = {
  version: "healthscore_v4", company_id: "COMP_TEST", group_id: "GROUP_TEST", month: "2026-08-01", as_of: "2026-08-31",
  health_score: null, excluida: false, confidence: "ninguna", n_meses_ventana: 6, n_meses_con_actividad: 0,
  c6: null, p6: null, d6: null, t6_efectivo: null, r_hist: null, colchon_v4: null, colchon_aplicable: null,
  mora_indice: null, h_antes_de_ajustes: null, penalizacion_mora_puntos: null, volumen_ambiguo_eur: null, volumen_ambiguo_pct: null,
  deficit_servicio_6: null, obligacion_vencida_m: null, multiplicador_deuda: null, penalizacion_multiplicador_puntos: null,
  k: 4178.45, alpha: 3, beta: 0.25, reasons: ["sin_actividad_en_ventana"], cash: null, payment: null, debt: null,
};
const model: ModelAssessment = { kind: "model", company: { id: record.company_id, name: record.company_id, group: record.group_id, currency: "EUR", data_mode: "challenge" }, records: [record], provenance: { batch_id: "test", source_revision: "test", imported_at: "2026-09-19", files: [], model_summary: { advertencia: "Provisional", limitaciones: [] } } };
const modelOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: model }));
assert.ok(modelOverview.includes("No score") && modelOverview.includes("Monthly observations") && !modelOverview.includes("Usable cash today"));
assert.ok(modelOverview.includes('/dashboard/health/2026-08-31?company=COMP_TEST'));
assert.ok(!modelOverview.includes('id="model-company"'), "Company selection belongs only in the sidebar");
assert.ok(modelOverview.indexOf('id="health"') < modelOverview.indexOf('id="cash"'), "Health history must lead the dashboard");
const publicOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: model, demo: true }));
assert.ok(publicOverview.includes('/demo/health/2026-08-31?company=COMP_TEST') && !publicOverview.includes('/dashboard/health/'), "Public score links must stay in the company demo");
const publicDetail = renderToStaticMarkup(createElement(ModelDashboard, { data: model, demo: true, scoreDate: record.as_of }));
assert.ok(publicDetail.includes('/demo?company=COMP_TEST'), "Returning from an explanation must retain the demo company");
const chartRecords = Array.from({ length: 24 }, (_, index) => ({ ...record, as_of: new Date(Date.UTC(2024, index + 1, 0)).toISOString().slice(0, 10), health_score: index === 12 ? null : 50 + index }));
const modelHistory = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: chartRecords } }));
const modelSvg = modelHistory.match(/<svg[^>]*aria-labelledby="model-chart-title model-chart-description"[\s\S]*?<\/svg>/)![0];
assert.equal([...modelSvg.matchAll(/<text /g)].length, 8, "Keep five score ticks and three readable date labels in the desktop chart");
assert.equal([...modelSvg.matchAll(/<circle /g)].length, 23, "Missing ratings remain gaps, never plotted as zero");
assert.equal((modelSvg.match(/<path d="([^"]*)"/)![1].match(/M/g) ?? []).length, 2, "Do not join the line across a missing rating");
const modelDetail = renderToStaticMarkup(createElement(ModelDashboard, { data: model, scoreDate: record.as_of }));
assert.ok(modelDetail.includes("Not available") && modelDetail.includes("No cash activity was observed") && modelDetail.includes("not a bank balance"));
assert.ok(!modelDetail.includes("NaN") && !modelDetail.includes("€0"), "Missing source values must not become zero-valued cash");
assert.equal(modelNumber(null), "Not available");
assert.equal(modelNumber(0), "0");
assert.match(modelReason("p_eur_desconocido_en_2_meses"), /Operating payments.*2 month/);
const scoredV4 = { ...record, health_score: 54, c6: 300, p6: 100, d6: 20, deficit_servicio_6: 30, obligacion_vencida_m: 50, t6_efectivo: 200, colchon_v4: 60, colchon_aplicable: 60, mora_indice: 0.4, multiplicador_deuda: 0.75, h_antes_de_ajustes: 80, penalizacion_mora_puntos: 8, penalizacion_multiplicador_puntos: 18, reasons: [] };
const v4Detail = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [scoredV4] }, scoreDate: record.as_of }));
for (const text of ["healthscore_v4", "Effective obligations (T6)", "€200", "Debt-service shortfall", "€30", "Debt multiplier reduction", "18 points", "0.4 / 1", "0.75×", "× debt multiplier"]) assert.ok(v4Detail.includes(text), text);
assert.ok(!v4Detail.includes("NaN") && !v4Detail.includes("including 100") && !v4Detail.includes("published v3"));
const excludedV4 = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [{ ...record, excluida: true, confidence: "excluida", reasons: ["excluida_nota_cero_persistente"] }] } }));
assert.ok(excludedV4.includes("Excluded") && excludedV4.includes("uneven data coverage"), "Exclusion is a published data rule, not a zero score or evidence of poor financial health");

const cashRecord: ModelRecord = { ...record, d6: 60.25, p6: 1234.56, t6_efectivo: 1294.81,
  cash: { saldo_reversa_eur: -321.09, flujo_neto: 678.12, meses_de_cobertura_reversa: null, saldo_ancla_eur: 900, confidence: "baja", flujo_operating_in: 1000, flujo_operating_out: -321.88, flujo_financing_in: 0, flujo_financing_out: 0, flujo_investment_in: 0, flujo_investment_out: 0, flujo_transfer: null, flujo_non_economic: 0, flujo_unknown: null, flags: ["agujeros_en_tramo"] },
  payment: { pago_exposicion_eur: 500.44, pago_vencido_eur: 123.45, mora_pago_robusta: null, cobro_exposicion_eur: 2000, cobro_vencido_eur: 876.54, mora_cobro_robusta: 0.43827, mora_indice: 0.43827, confidence: "baja", confidence_pago: "baja", confidence_cobro: "baja", pago_n_huecos_eur: 1, cobro_n_huecos_eur: 1, n_vencimiento_desconocido: 3 },
};
const cashRecords = [{ ...cashRecord, as_of: "2026-06-30" }, { ...record, as_of: "2026-07-31" }, cashRecord];
const dataOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: cashRecords } }));
for (const text of ["Cash movements over time", "Reconstructed cash", "Payments and collections", "Debt and overdue obligations", "Monthly source records", "€678.12", "-€321.88", "€123.45", "€876.54", "€60.25", "2 invoice(s) with unknown EUR amounts"]) assert.ok(dataOverview.includes(text), `Display the published amount or explanation: ${text}`);
assert.match(dataOverview, /id="model-date"/, "The overview must let users select any published month");
assert.ok(dataOverview.includes("not the outstanding debt balance") && dataOverview.includes("not a bank balance"));
const cashSvg = dataOverview.match(/<svg[^>]*aria-labelledby="monthly-chart-title monthly-chart-description"[\s\S]*?<\/svg>/)![0];
assert.equal((cashSvg.match(/<path d="([^"]*)"/)![1].match(/M/g) ?? []).length, 2, "Missing cash months split the plotted series");
assert.ok(!cashSvg.includes("NaN") && !cashSvg.includes("undefined"));
assert.ok(modelOverview.includes("No cash movements were published"), "An empty cash history needs a clear state without suppressing other datasets");
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
console.log("Web checks passed: published company demo and team sign-in, redirects, exact money input, dated horizons, chart scales, dated health explanations, dialog boundaries and validation errors.");
