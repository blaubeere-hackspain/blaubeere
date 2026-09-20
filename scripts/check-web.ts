import assert from "node:assert/strict";
import "./check-welcome";
import "./check-scroll-reveals";
import "./check-dev-proxy";
import { createRequire } from "node:module";
import { LoginForm } from "../apps/app/components/login-form";
import { CompanyPicker } from "../apps/app/components/company-picker";
import { DailyCashPlot, MonthlyCashChart } from "../apps/app/components/monthly-cash-chart";
import { api, ApiError, returnPath } from "../apps/app/lib/api";
import { addDays, cents, money, monthlyTimeline } from "../apps/app/lib/format";
import { cashScale } from "../apps/app/components/cash-chart";
import companies from "../fixtures/companies.json";
import { HealthExplanation } from "../apps/app/components/health-explanation";
import { healthHref, healthTimeline, validHealthDate } from "../apps/app/lib/health";
import type { Company } from "../apps/app/lib/types";
import { outsideDialog } from "../apps/app/components/dialog";
import { ModelDashboard, modelNumber, modelReason, hasMonthlyData } from "../apps/app/components/model-dashboard";
import { agingAmounts, DefaultingCard, formatIndex } from "../apps/app/components/financial-cards";
import type { CashMonth, ModelAssessment, ModelRecord } from "../apps/app/lib/types";
import { HealthScorePanel } from "../apps/app/components/health-score-panel";
import preview from "../apps/landing/data/product-preview.json";

const requireApp = createRequire(new URL("../apps/app/package.json", import.meta.url));
const { createElement } = requireApp("react");
const { renderToStaticMarkup } = requireApp("react-dom/server");
const demo = { company: companies[0] as Company };
const record: ModelRecord = {
  version: "healthscore_v4", company_id: "COMP_TEST", group_id: "GROUP_TEST", month: "2026-08-01", as_of: "2026-08-31",
  health_score: null, excluida: false, confidence: "ninguna", n_meses_ventana: 6, n_meses_con_actividad: 1,
  c6: null, p6: null, d6: null, t6_efectivo: null, r_hist: null, colchon_v4: null, colchon_aplicable: null,
  mora_indice: null, h_antes_de_ajustes: null, penalizacion_mora_puntos: null, volumen_ambiguo_eur: null, volumen_ambiguo_pct: null,
  deficit_servicio_6: null, obligacion_vencida_m: null, multiplicador_deuda: null, penalizacion_multiplicador_puntos: null,
  k: 4178.45, alpha: 3, beta: 0.25, reasons: ["sin_actividad_en_ventana"], cash: null, payment: null, debt: null,
};
const model: ModelAssessment = { kind: "model", company: { id: record.company_id, name: record.company_id, group: record.group_id, currency: "EUR", data_mode: "challenge" }, records: [record], provenance: { batch_id: "test", source_revision: "test", imported_at: "2026-09-19", files: [], model_summary: { advertencia: "Provisional", limitaciones: [] } } };
for (const [list, label, disabled] of [[null, "Loading companies…", true], [[], "No companies available", true], [[model.company], "COMP_TEST", false]] as const) {
  const picker = renderToStaticMarkup(createElement(CompanyPicker, { id: "test-company", companies: list, selected: model.company.id, onSelect: () => {} }));
  assert.ok(picker.includes(label) && picker.includes('aria-haspopup="dialog"') && !picker.includes("<select"), "Use the styled company picker in every loading state");
  assert.equal(picker.includes('disabled=""'), disabled, "Only available companies can open the picker");
}
const modelOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: model }));
const company48 = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, company: { ...model.company, name: "COMP_0048" } } }));
const companyHeader = company48.match(/<header[\s\S]*?<\/header>/)![0];
assert.ok(companyHeader.includes("COMP 48") && companyHeader.includes("Financial health and the movements behind it.") && companyHeader.includes('id="model-date"') && companyHeader.includes('aria-haspopup="dialog"'), "Company title and calendar share one header");
assert.ok(!company48.includes("Imported company data") && !company48.includes("monthly assessments") && !companyHeader.includes("GROUP_TEST"), "The company heading stays free of dataset metadata");
assert.ok(modelOverview.includes("No score") && modelOverview.includes("Monthly observations") && !modelOverview.includes("Usable cash today"));
assert.ok(!modelOverview.includes('/dashboard/health/'), "The overview does not show an explanation button");
assert.ok(!modelOverview.includes('id="model-company"'), "Company selection belongs only in the sidebar");
assert.ok(modelOverview.indexOf('id="health"') < modelOverview.indexOf('id="cash"'), "Health history must lead the dashboard");
assert.ok(modelOverview.includes('class="stats-grid model-stats"') && modelOverview.indexOf('class="stats-grid model-stats"') < modelOverview.indexOf('id="health"'), "The four source metrics belong above the health chart");
const publicOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: model, demo: true }));
assert.ok(!publicOverview.includes('/demo/health/') && !publicOverview.includes('/dashboard/health/'), "The demo does not show an explanation button");
const publicDetail = renderToStaticMarkup(createElement(ModelDashboard, { data: model, demo: true, scoreDate: record.as_of }));
assert.ok(publicDetail.includes('/demo?company=COMP_TEST'), "Returning from an explanation must retain the demo company");
const chartRecords = Array.from({ length: 24 }, (_, index) => ({ ...record, as_of: new Date(Date.UTC(2024, index + 1, 0)).toISOString().slice(0, 10), health_score: index === 16 ? null : 50 + index }));
const modelHistory = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: chartRecords } }));
const modelSvg = modelHistory.match(/<svg[^>]*aria-labelledby="model-chart-title model-chart-description"[\s\S]*?<\/svg>/)![0];
assert.equal([...modelSvg.matchAll(/<text /g)].length, 8, "Keep five score ticks and three readable date labels in the desktop chart");
assert.equal([...modelSvg.matchAll(/<circle /g)].length, 11, "Only ratings from 2025 onward are plotted, and missing ratings remain gaps");
assert.ok(!modelSvg.includes("2024") && modelSvg.includes("31 Jan 2025"), "Health history starts in 2025");
assert.equal((modelSvg.match(/<path d="([^"]*)"/)![1].match(/M/g) ?? []).length, 2, "Do not join the line across a missing rating");
assert.ok(modelSvg.includes("Today"), "Extend the time axis through today without adding score points");
const modelDetail = renderToStaticMarkup(createElement(ModelDashboard, { data: model, scoreDate: record.as_of }));
assert.ok(modelDetail.includes("Not available") && modelDetail.includes("No cash activity was observed") && modelDetail.includes("not a bank balance"));
assert.ok(!modelDetail.includes("NaN") && !modelDetail.includes("€0"), "Missing source values must not become zero-valued cash");
assert.equal(modelNumber(null), "Not available");
assert.equal(modelNumber(0), "0");
assert.match(modelReason("p_eur_desconocido_en_2_meses"), /Operating payments.*2 month/);
const scoredV4 = { ...record, health_score: 54, c6: 300, p6: 100, d6: 20, deficit_servicio_6: 30, obligacion_vencida_m: 50, t6_efectivo: 200, colchon_v4: 60, colchon_aplicable: 60, mora_indice: 0.4, multiplicador_deuda: 0.75, h_antes_de_ajustes: 80, penalizacion_mora_puntos: 8, penalizacion_multiplicador_puntos: 18, reasons: [] };
const v4Detail = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [scoredV4] }, scoreDate: record.as_of }));
assert.equal((v4Detail.match(/class="health-score-gauge"[\s\S]*?<\/svg>/)![0].match(/stroke="var\(--accent\)"/g) ?? []).length, 19, "The gauge reflects the returned score on a 0–100 scale");
assert.ok(!modelOverview.match(/class="health-score-gauge"[\s\S]*?<\/svg>/)![0].includes('stroke="var(--accent)"'), "Missing evidence must not fill the score gauge");
for (const text of ["Effective obligations (T6)", "€200", "Debt-service shortfall", "€30", "Debt multiplier reduction", "18 points", "0.4 / 1", "0.75×", "× debt multiplier"]) assert.ok(v4Detail.includes(text), text);
assert.ok(!v4Detail.includes("NaN") && !v4Detail.includes("including 100") && !v4Detail.includes("published v3"));
const excludedV4 = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [{ ...record, excluida: true, confidence: "excluida", reasons: ["excluida_nota_cero_persistente"] }] }, scoreDate: record.as_of }));
assert.ok(excludedV4.includes("Excluded") && excludedV4.includes("uneven data coverage"), "Exclusion is a published data rule, not a zero score or evidence of poor financial health");

const cashRecord: ModelRecord = { ...record, d6: 60.25, p6: 1234.56, t6_efectivo: 1294.81,
  cash: { saldo_reversa_eur: -321.09, flujo_neto: 678.12, volumen_conocido: 1321.88, meses_de_cobertura_reversa: null, saldo_ancla_eur: 900, confidence: "baja", flujo_operating_in: 1000, flujo_operating_out: -321.88, flujo_financing_in: 0, flujo_financing_out: 0, flujo_investment_in: 0, flujo_investment_out: 0, flujo_transfer: null, flujo_non_economic: 0, flujo_unknown: null, flags: ["agujeros_en_tramo"] },
  payment: { pago_vencido_1_30_eur: 23.45, pago_vencido_31_60_eur: 100, pago_vencido_61_90_eur: 0, pago_vencido_91_180_eur: 0, pago_vencido_180_mas_eur: 0, pago_n_facturas: 5,
    cobro_vencido_1_30_eur: 76.54, cobro_vencido_31_60_eur: 800, cobro_vencido_61_90_eur: 0, cobro_vencido_91_180_eur: 0, cobro_vencido_180_mas_eur: 0, cobro_n_facturas: 8,
    pago_exposicion_eur: 500.44, pago_vencido_eur: 123.45, mora_pago_robusta: null, cobro_exposicion_eur: 2000, cobro_vencido_eur: 876.54, mora_cobro_robusta: 0.43827, mora_indice: 0.43827, confidence: "baja", confidence_pago: "baja", confidence_cobro: "baja", pago_n_huecos_eur: 1, cobro_n_huecos_eur: 1, n_vencimiento_desconocido: 3 },
};
const daily: CashMonth = { currency: "EUR", anchor_date: "2026-09-01", income: 1000, expense: 321.88, closing_balance: -321.09, days: [
  { date: "2026-08-01", income: 1000, expense: 321.88, balance: -321.09, unknown_movements: 0 },
  { date: "2026-08-02", income: null, expense: null, balance: null, unknown_movements: 1 },
  { date: "2026-08-03", income: 0, expense: 0, balance: -321.09, unknown_movements: 0 },
] };
const inactive = { ...record, n_meses_con_actividad: 0 };
assert.equal(hasMonthlyData(inactive), false, "An empty model-grid row is not an available company month");
assert.equal(hasMonthlyData({ ...inactive, cash: cashRecord.cash }), true, "Cash activity remains available even without a score");
assert.equal(hasMonthlyData({ ...inactive, payment: cashRecord.payment }), true, "Invoice evidence makes a month available");
assert.equal(hasMonthlyData({ ...inactive, health_score: 0 }), true, "A real zero score is still a recorded assessment");
const inactiveOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [inactive] } }));
assert.ok(inactiveOverview.includes("No recorded activity yet") && inactiveOverview.includes('id="model-date"') && inactiveOverview.includes('disabled=""'));
const latestEmpty = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: [{ ...cashRecord, as_of: "2026-07-31" }, inactive] } }));
assert.ok(latestEmpty.includes('Selected month: July 2026'), "Default to the latest month with company evidence");
const cashRecords = [{ ...cashRecord, as_of: "2026-06-30" }, { ...record, as_of: "2026-07-31" }, { ...cashRecord, daily_cash: [daily] }];
const dataOverview = renderToStaticMarkup(createElement(ModelDashboard, { data: { ...model, records: cashRecords } }));
for (const text of ["Cash flow", "Income", "Expenses", "Reconstructed cash", "Overdue collections", "Overdue payments", "Defaulting", "€678.12", "-€321.09", "€123.45", "€876.54", "1 invoice(s) with unknown EUR amounts"]) assert.ok(dataOverview.includes(text), `Display the published amount or explanation: ${text}`);
for (const removed of ["Signed amounts in the published cash categories", "Debt and overdue obligations", "Monthly source records", 'id="history"']) assert.ok(!dataOverview.includes(removed), `Remove the dashboard section: ${removed}`);
const previewHealth = renderToStaticMarkup(createElement(HealthScorePanel, { records: preview.history, row: preview.record }));
assert.ok(!previewHealth.includes("Explain this score") && !previewHealth.includes("Why no score?"), "The landing preview does not show an explanation button");
assert.ok(previewHealth.includes("30.2") && previewHealth.includes("-14.9 points since 31 Jul") && previewHealth.includes('stroke-dasharray="4 4"'), "The landing preview uses the saved score history, including the selected month marker");
const flaggedHealth = renderToStaticMarkup(createElement(HealthScorePanel, { records: preview.history, row: { ...preview.record, health_status: { state: "attention_needed", score: 30.2, confidence: "alta", low_score_threshold: 40, policy_note: "Provisional attention rules", issues: [{ code: "low_health_score", title: "Cash-flow health needs attention", detail: "Review cash and overdue payments.", value: 30.2, unit: "points" }] } } }));
assert.ok(flaggedHealth.includes('role="status"') && flaggedHealth.includes("Cash-flow health needs attention") && flaggedHealth.includes("Review issue") && flaggedHealth.includes("31 Aug 2026"), "Render dated backend alerts with expandable evidence");
const previewCash = preview.record.daily_cash[0];
assert.deepEqual([preview.company_id, preview.record.as_of, previewCash.days.length, previewCash.income, previewCash.expense, previewCash.closing_balance], ["COMP_0006", "2026-08-31", 31, 35572.28, 31152.94, 15564.58]);
assert.equal(previewCash.days.reduce((sum, day) => sum + Math.round(day.income * 100) - Math.round(day.expense * 100), 0), 441934, "The published preview daily cash reconciles to its month totals");
assert.ok(dataOverview.includes('id="model-date"') && dataOverview.includes("August 2026"));
assert.ok(!dataOverview.includes("healthscore_v4") && !dataOverview.includes("Shared scale"));
const metricCaptions = [...dataOverview.match(/<section class="stats-grid model-stats"[\s\S]*?<\/section>/)![0].matchAll(/<p>(.*?)<\/p>/g)];
assert.equal(metricCaptions.length, 4);
assert.ok(metricCaptions.every(caption => caption[1].includes("2026")), "All metric periods include the selected year");
const cashSvg = dataOverview.match(/<svg[^>]*aria-labelledby="daily-chart-title daily-chart-description"[\s\S]*?<\/svg>/)![0];
assert.equal((cashSvg.match(/<path[^>]* d="([^"]*)"/)![1].match(/M/g) ?? []).length, 2, "Missing daily balances split the line");
for (const series of ["income", "expense"]) assert.equal([...cashSvg.matchAll(new RegExp(`<rect[^>]*data-series="${series}"`, "g"))].length, 2, "Daily bars preserve gaps instead of fabricating movements");
assert.ok(!cashSvg.includes("NaN") && !cashSvg.includes("Jun") && !cashSvg.includes("Jul") && !cashSvg.includes("Today"), "Cash chart contains only the selected month");
const sameScale: CashMonth = { ...daily, days: [{ ...daily.days[0], income: 1000, expense: 1000, balance: 1000 }] };
for (const height of [290, 220]) {
  const sharedScaleChart = renderToStaticMarkup(createElement(DailyCashPlot, { series: sameScale, height }));
  const balanceY = sharedScaleChart.match(/<circle[^>]* cy="([^"]+)"/)![1];
  for (const series of ["income", "expense"]) assert.equal(sharedScaleChart.match(new RegExp(`<rect[^>]*data-series="${series}"[^>]* y="([^"]+)"`))![1], balanceY, "Full and compact charts share one scale for all three series");
  assert.ok(sharedScaleChart.includes(`viewBox="0 0 1000 ${height}"`) && sharedScaleChart.includes(`height:${height}px`), "Compact SVG coordinates must match their rendered height for accurate pointer inspection");
}
const zeroChart = renderToStaticMarkup(createElement(DailyCashPlot, { series: { ...sameScale, days: [{ ...sameScale.days[0], balance: 0 }] } }));
assert.ok(zeroChart.includes("Reconstructed cash · €0"));
const nativeChart = renderToStaticMarkup(createElement(MonthlyCashChart, { row: { ...cashRecord, daily_cash: [{ ...daily, currency: "GBP" }] } }));
assert.ok(nativeChart.includes("Original GBP accounts") && nativeChart.includes("£1,000") && !nativeChart.includes("€"), "Currency labels must follow the original-currency amounts");
const aged = agingAmounts(cashRecord.payment, "pago");
assert.ok(aged.complete && Math.abs(aged.buckets.reduce((sum, bucket) => sum + bucket.fraction!, 0) - 1) < 1e-9);
assert.ok(agingAmounts(null, "pago").buckets.every(bucket => bucket.fraction === null));
assert.ok(!agingAmounts({ ...cashRecord.payment!, pago_vencido_1_30_eur: null }, "pago").complete);
assert.ok(!agingAmounts({ ...cashRecord.payment!, pago_vencido_eur: 999 }, "pago").complete, "Inconsistent aging must not produce fabricated shares");
const defaultingRow = { ...cashRecord, mora_indice: .34, payment: { ...cashRecord.payment!, mora_pago_robusta: .4, mora_cobro_robusta: .2 } };
const defaultingMarkup = renderToStaticMarkup(createElement(DefaultingCard, { row: defaultingRow }));
assert.ok(defaultingMarkup.includes("0.34 / 1") && defaultingMarkup.includes("width:40%") && defaultingMarkup.includes("width:20%") && defaultingMarkup.includes("not a probability of default"), "Defaulting shows the saved index and both sides on a fixed 0–1 scale");
for (const value of [null, undefined, NaN, Infinity, -1, 1.01]) assert.equal(formatIndex(value), "Not available");
assert.equal(formatIndex(0), "0 / 1", "A known zero is distinct from missing arrears evidence");
const partialDefaulting = renderToStaticMarkup(createElement(DefaultingCard, { row: { ...defaultingRow, mora_indice: 0, payment: { ...defaultingRow.payment, mora_pago_robusta: null, mora_cobro_robusta: 0 } } }));
assert.ok(partialDefaulting.includes("Only customer-collection evidence") && partialDefaulting.includes("Not available") && (partialDefaulting.match(/class="defaulting-track"/g) ?? []).length === 1, "An unknown side stays missing while a known zero keeps its bar");
const missingDefaulting = renderToStaticMarkup(createElement(DefaultingCard, { row: record }));
assert.ok(missingDefaulting.includes("Insufficient payment evidence") && !missingDefaulting.includes('class="defaulting-track"') && !missingDefaulting.includes("0 / 1"));
assert.ok(!missingDefaulting.includes("Currency risk") && !missingDefaulting.includes("fx-risk-title"), "A row without the FX layer renders no currency-risk half");
const fxRow = { ...cashRecord, indice_fx: 0.32, indice_fx_aplicado: 0.32, penalizacion_fx_puntos: 1.21, beta_fx: 0.05 };
const fxMarkup = renderToStaticMarkup(createElement(DefaultingCard, { row: fxRow }));
assert.ok(fxMarkup.includes("Currency risk") && fxMarkup.includes("0.32 / 1") && fxMarkup.includes("width:32%") && fxMarkup.includes("1.21 points") && fxMarkup.includes("0.05"), "Currency risk shows the observed index, its amber bar and the score reduction");
assert.ok(!fxMarkup.includes("NaN") && !fxMarkup.includes("undefined"));
const fxIntervalRow = { ...cashRecord, indice_fx: null, indice_fx_min: 0.5, indice_es_intervalo: true, indice_fx_aplicado: 0.5 };
const fxIntervalMarkup = renderToStaticMarkup(createElement(DefaultingCard, { row: fxIntervalRow }));
assert.ok(fxIntervalMarkup.includes("0.5 / 1") && fxIntervalMarkup.includes("minimum compatible penalty"), "An opaque-currency row shows the applied minimum penalty without a range");
assert.ok(!fxIntervalMarkup.includes("0.5 – 1") && !/interval/i.test(fxIntervalMarkup) && !fxIntervalMarkup.includes("NaN") && !fxIntervalMarkup.includes("undefined"), "No min-max range or interval wording is ever rendered");
const fxUnknownRow = { ...cashRecord, indice_fx: null, indice_fx_aplicado: null };
const fxUnknownMarkup = renderToStaticMarkup(createElement(DefaultingCard, { row: fxUnknownRow }));
assert.ok(fxUnknownMarkup.includes("could not be valued this month") && !fxUnknownMarkup.includes('class="fx-track"') && !fxUnknownMarkup.includes("0 / 1"), "An unknown FX index is missing evidence, never zero and never punished");
assert.ok(!fxUnknownMarkup.includes("NaN") && !fxUnknownMarkup.includes("undefined"));
const fxZeroRow = { ...cashRecord, indice_fx: 0 };
const fxZeroMarkup = renderToStaticMarkup(createElement(DefaultingCard, { row: fxZeroRow }));
assert.ok(fxZeroMarkup.includes("0 / 1") && !fxZeroMarkup.includes("could not be valued this month"), "A known zero FX index is shown as 0 / 1, not as missing evidence");
assert.ok(!fxZeroMarkup.includes("NaN") && !fxZeroMarkup.includes("undefined"));
assert.ok(!modelReason("castigo_fx_indice_0.5000").includes("castigo_fx") && modelReason("castigo_fx_indice_0.5000").includes("0.5 / 1"), "Dynamic FX reason codes are redacted in readable text");
assert.ok(!modelReason("indice_fx_desconocido").includes("indice_fx"), "FX reason codes never leak their raw identifiers");
assert.ok(!dataOverview.match(/<section class="stats-grid model-stats"[\s\S]*?<\/section>/)![0].includes("Observed debt service") && dataOverview.includes('id="defaulting-title"') && !dataOverview.includes('id="debt-service-title"'), "The dashboard replaces the debt service metric and card with defaulting");
assert.deepEqual(monthlyTimeline(["2026-08-31"], "2026-09-19"), ["2026-08-31", "2026-09-19"]);
assert.deepEqual(monthlyTimeline(["2024-01-31"], "2024-04-10"), ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-10"]);
assert.deepEqual(monthlyTimeline(["2026-08-31"], "2026-09-30"), ["2026-08-31", "2026-09-30"]);
assert.deepEqual(monthlyTimeline(["2026-08-31"], "2026-08-31"), ["2026-08-31"]);
assert.deepEqual(monthlyTimeline(["2026-08-31"], "2026-07-01"), ["2026-08-31"]);
assert.deepEqual(monthlyTimeline([], "2026-09-19"), []);
assert.ok(modelOverview.includes("Daily cash movements have not been imported"), "An empty cash history needs a clear state without suppressing other datasets");
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
assert.equal(money(-0, "EUR", false, true), "€0", "Rounded balances must not display a negative zero");
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
