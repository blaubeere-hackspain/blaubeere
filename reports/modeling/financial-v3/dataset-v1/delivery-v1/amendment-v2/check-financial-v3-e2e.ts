import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { chromium, expect, type Browser, type Page } from "@playwright/test";
import { amount, closedMonths, lineSegments, percent, receiptProbability, type FinancialAssessment } from "/home/mikel/.herdr/worktrees/predictive-models/feat-training-devin/apps/app/lib/financial-v3";

const root = "/home/mikel/.herdr/worktrees/predictive-models/feat-training-devin";
const dataset = join(root, "reports/modeling/financial-v3/dataset-v1");
const output = join(dataset, "delivery-v1", "amendment-v2");
const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const manifest = JSON.parse(await readFile(join(dataset, "manifest.json"), "utf8"));
assert.equal(sha(await readFile(join(dataset, "manifest.json"))), "e360ef33ba7cae39aa9c94d0f8e778f49c38a3985f77053a2f53bd4ae5ef8a44");
assert.equal(sha(await readFile(join(dataset, "model-v1/manifest.json"))), "71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428");
const indexBytes = await readFile(join(dataset, "index.json"));
assert.equal(sha(indexBytes), manifest.artifacts_sha256["index.json"]);
const index = JSON.parse(indexBytes.toString()).companies as { id: string; currency: string; path: string; sha256: string; bytes: number }[];
const mixedCurrency = index.find(c => c.currency !== "EUR")!;
const largest = index.reduce((a, b) => a.bytes > b.bytes ? a : b);
const ids = [...new Set(["COMP_0001", "COMP_0003", mixedCurrency.id, largest.id])];
const originals = new Map<string, any>();
for (const id of ids) {
  const entry = index.find(c => c.id === id)!;
  const bytes = await readFile(join(dataset, entry.path)); assert.equal(sha(bytes), entry.sha256);
  originals.set(id, JSON.parse(bytes.toString()));
}
const processes = new Set<ChildProcess>();
const secrets: string[] = [];
const steps: { name: string; status: string }[] = [];
const pageErrors: string[] = [];
let currentStep = "initialization";
let browser: Browser | undefined;
let apiCuts = 0, uiCuts = 0, regressionCuts = 0, maxPayload = 0;
let result: Record<string, unknown> = { command: "bun reports/modeling/financial-v3/dataset-v1/delivery-v1/amendment-v2/check-financial-v3-e2e.ts", source_queries: 0, outcome_queries: 0, inference: 0, ids, mixed_currency_company: mixedCurrency.id, largest_profile_company: largest.id, steps };
async function step(name: string, action: () => Promise<void>) { currentStep = name; await action(); steps.push({ name, status: "passed" }); }
async function port(): Promise<number> {
  return new Promise((done, fail) => {
    const server = createServer(); server.once("error", fail); server.listen(0, "127.0.0.1", () => {
      const value = (server.address() as { port: number }).port;
      server.close(error => error ? fail(error) : done(value));
    });
  });
}
function start(command: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) {
  const child = spawn(command, args, { cwd, env, detached: true, stdio: "ignore" });
  processes.add(child); child.on("error", () => {}); return child;
}
async function stop(child: ChildProcess) {
  if (!child.pid || !processes.has(child)) return;
  try { process.kill(-child.pid, "SIGTERM"); } catch {}
  await Promise.race([new Promise<void>(done => child.once("exit", () => done())), Bun.sleep(1500)]);
  try { process.kill(-child.pid, "SIGKILL"); } catch {}
  processes.delete(child);
}
async function ready(url: string, child: ChildProcess) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    assert.equal(child.exitCode, null, "Owned server exited before readiness");
    try { if ((await fetch(url, { signal: AbortSignal.timeout(2000) })).ok) return; } catch {}
    await Bun.sleep(250);
  }
  throw new Error("Bounded readiness timeout");
}
async function workspace(file: string, companyIds: string[], action: (page: Page, origin: string) => Promise<void>) {
  const temp = await mkdtemp(join(tmpdir(), "blaubeere-financial-v3-e2e-"));
  const apiPort = await port(); let appPort = await port(); while (appPort === apiPort) appPort = await port();
  const apiOrigin = `http://127.0.0.1:${apiPort}`, appOrigin = `http://127.0.0.1:${appPort}`;
  const password = randomBytes(32).toString("base64url"); secrets.push(password);
  const email = "v3-e2e@blaubeere.local";
  const api = start(join(root, "target/debug/blaubeere-api"), [], temp, {
    PATH: process.env.PATH, HOME: temp, RUST_LOG: "error", DATABASE_URL: `sqlite://${join(temp, "identity.sqlite")}`,
    ASSESSMENT_FILE: file, API_BIND: `127.0.0.1:${apiPort}`, API_ORIGIN: apiOrigin, APP_ORIGIN: appOrigin, MCP_RESOURCE: `${apiOrigin}/mcp`,
    BOOTSTRAP_EMAIL: email, BOOTSTRAP_PASSWORD: password, BOOTSTRAP_COMPANIES: companyIds.join(","),
  });
  const app = start(Bun.which("node")!, [join(root, "apps/app/node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", String(appPort)], join(root, "apps/app"), {
    PATH: process.env.PATH, HOME: process.env.HOME, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", API_INTERNAL_URL: apiOrigin,
  });
  const context = await browser!.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  const page = await context.newPage(); page.on("pageerror", error => pageErrors.push(error.name));
  try {
    await ready(`${apiOrigin}/health`, api); await ready(`${appOrigin}/login`, app);
    await step(`${companyIds[0]} unauthenticated API and browser login`, async () => {
      assert.equal((await page.request.get(`${appOrigin}/api/companies/${companyIds[0]}/assessment`)).status(), 401);
      await page.goto(`${appOrigin}/login`); await page.getByLabel("Work email").fill(email); await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click(); await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
      await expect(page.getByLabel("Company", { exact: true })).toHaveValue(companyIds[0]);
    });
    await action(page, appOrigin);
  } finally { await context.close(); await stop(app); await stop(api); await rm(temp, { recursive: true, force: true }); }
}
async function noCash(page: Page) {
  await expect(page.getByRole("heading", { name: "Cash over time" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Health, in context" })).toHaveCount(0);
  await expect(page.getByLabel("Outlook", { exact: true })).toHaveCount(0);
  for (const b of await page.getByRole("button", { name: "Explore a plan" }).all()) await expect(b).toBeDisabled();
}
const goal = { metric: "ending_cash", target_cents: 0, deadline: "2026-11-30", cash_floor_cents: 0, max_collection_days: 0, max_spend_reduction_pct: 0, max_funding_cents: 0, max_growth_pct: 0, business: null };
async function apiCut(page: Page, origin: string, id: string, cut: string) {
  const response = await page.request.get(`${origin}/api/companies/${id}/assessment?as_of=${cut}&view=retrospective`);
  assert.equal(response.status(), 200);
  const bytes = await response.body(); maxPayload = Math.max(maxPayload, bytes.length); assert.ok(bytes.length < 430_000);
  const data = JSON.parse(bytes.toString()) as FinancialAssessment;
  assert.equal(data.view, "retrospective"); assert.equal(data.selected_month, cut); assert.equal(data.current_point.as_of, cut);
  const original = originals.get(id);
  const selected = original.financial_v3.points.filter((p: any) => p.as_of <= cut);
  const expected = selected.map(({ cash, ...rest }: any) => rest);
  assert.deepEqual(data.company.financial_v3.points, expected); assert.deepEqual(data.current_point, expected.at(-1));
  assert.deepEqual(data.retrospective_reconstruction.points, selected.map((p: any) => ({ as_of: p.as_of, accounts: p.cash.accounts })));
  assert.equal(data.retrospective_reconstruction.view, "retrospective"); assert.equal(data.retrospective_reconstruction.historical_evidence, false);
  assert.equal(data.retrospective_reconstruction.retrieved_at, "2026-09-19T12:48:14Z");
  assert.equal("anchors" in data.company.financial_v3.meta, false); assert.equal("snapshot_context" in data.company.financial_v3.meta, false);
  for (const a of data.retrospective_reconstruction.anchors) assert.equal("balance" in a, false);
  assert.equal(data.company.health, null); assert.equal(data.forecast, null); assert.equal(data.cash_planning_available, false); assert.equal(data.automatic_alerts, false);
  assert.equal(data.experiment_report.view, "later_methodological_report"); assert.equal(data.experiment_report.historical_evidence, false);
  for (const p of data.model_overlay.points) {
    assert.ok(p.as_of <= cut); assert.equal(p.view, "retrospective"); assert.equal(p.actually_issued_historically, false);
    assert.equal("features" in p, false);
    if (p.month < "2026-01-01") for (const f of Object.values(p.forecast)) assert.equal(f.probabilities, null);
  }
  if (id === "COMP_0003") { assert.equal(data.model_overlay.reason, "final_test_product_only_no_model_inference"); assert.equal(data.model_overlay.current_point, null); }
  apiCuts++; return data;
}
try {
  currentStep = "launch sandboxed system Chromium";
  const executablePath = Bun.which("chromium") ?? Bun.which("chromium-browser"); assert.ok(executablePath);
  browser = await chromium.launch({ executablePath, headless: true, chromiumSandbox: true });
  result.browser = { version: browser.version(), executablePath, sandbox: true };
  await workspace(join(dataset, "index.json"), ids, async (page, origin) => {
    await step("negative scientific result, no headline/planner, sources and units", async () => {
      await expect(page.getByRole("heading", { name: "Internal retrospective utility FAILED", exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Full financial health unavailable", exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Six-question availability", exact: true })).toBeVisible();
      await expect(page.getByRole("img", { name: "Receipt expansion: separate probability axis from zero to one", exact: true })).toBeVisible();
      await expect(page.getByTestId("v3-reconstruction")).toContainText("Future-anchor reconstruction, never historical knowledge");
      await noCash(page);
    });
    await step("all 24 API cuts, four real profiles including holdout/mixed-currency/largest", async () => {
      for (const id of ids) for (const cut of closedMonths()) await apiCut(page, origin, id, cut);
    });
    for (const id of ids) await step(`${id} browser closed months, gaps, probabilities and currency`, async () => {
      await page.getByLabel("Company", { exact: true }).selectOption(id);
      await expect(page.getByTestId("financial-v3-cutoff")).toHaveText("2026-08-31");
      for (const cut of ["2024-09-30", "2025-12-31", "2026-01-31", "2026-08-31"]) {
        await page.getByLabel("Retrospective closed month", { exact: true }).selectOption(cut);
        await expect(page.getByTestId("financial-v3-cutoff")).toHaveText(cut);
        const data = await apiCut(page, origin, id, cut);
        assert.deepEqual(await page.locator("[data-financial-date]").evaluateAll(rows => rows.map(r => r.getAttribute("data-financial-date"))), data.company.financial_v3.points.map(p => p.as_of));
        await expect(page.getByLabel("Receipt expansion probability", { exact: true })).toHaveText(percent(receiptProbability(data.model_overlay.current_point, "receipt_expansion_3m")));
        const expected = lineSegments(data.company.financial_v3.points.map(p => p.flow.net_observed));
        await expect(page.getByRole("img", { name: "Observed monthly net cash flows in EUR, gaps not interpolated", exact: true }).locator("[data-segment]")).toHaveCount(expected.length);
        const flowRegion = page.getByRole("region", { name: "Monthly EUR flows", exact: true });
        await expect(flowRegion).toContainText(amount(data.current_point.flow.operating_receipts));
        const cashRegion = page.getByRole("region", { name: "Native cash reconstruction scenarios", exact: true });
        for (const a of data.retrospective_reconstruction.points.at(-1)!.accounts) if (a.closing_scenarios) await expect(cashRegion).toContainText(amount(a.closing_scenarios[0], a.currency));
        await noCash(page); uiCuts++;
      }
    });
    await step("keyboard selection and mobile layout", async () => {
      await page.getByLabel("Retrospective closed month", { exact: true }).focus(); await page.keyboard.press("Home"); await page.keyboard.press("Enter");
      await expect(page.getByTestId("financial-v3-cutoff")).toHaveText("2024-09-30"); uiCuts++;
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "Mobile overflow");
      await page.setViewportSize({ width: 1440, height: 1000 });
    });
    await step("403 authorization, strict-known-on and invalid dates, unconditional plan rejection", async () => {
      const list = await (await page.request.get(`${origin}/api/companies`)).json(); assert.deepEqual(list.map((c: any) => c.id).sort(), [...ids].sort());
      assert.equal((await page.request.get(`${origin}/api/companies/COMP_0002/assessment`)).status(), 403);
      assert.equal((await page.request.post(`${origin}/api/companies/COMP_0002/plans`, { headers: { Origin: origin }, data: goal })).status(), 403);
      for (const query of ["view=strict_known_on", "strict=true", "as_of=2025-02-27", "as_of=2026-09-30", "as_of=2024-08-31", "as_of=invalid"]) assert.equal((await page.request.get(`${origin}/api/companies/COMP_0001/assessment?${query}`)).status(), 400);
      for (const id of ids) assert.equal((await page.request.post(`${origin}/api/companies/${id}/plans`, { headers: { Origin: origin }, data: goal })).status(), 400);
    });
  });
  await workspace(join(root, "reports/modeling/trajectory-v2/assessment/companies.json"), ["COMP_0009", "COMP_0028", "COMP_0179"], async (page, origin) => {
    await step("trajectory unchanged: all development case API cuts and UI confirmations", async () => {
      await expect(page.getByRole("heading", { name: "Anticipation not validated", exact: true })).toBeVisible();
      for (const [id, confirmation] of [["COMP_0009", "2025-05-31"], ["COMP_0028", "2025-06-30"], ["COMP_0179", "2025-05-31"]]) {
        for (const cut of closedMonths()) {
          const response = await page.request.get(`${origin}/api/companies/${id}/assessment?as_of=${cut}`); assert.equal(response.status(), 200);
          const data = await response.json(); assert.equal(data.current_point.as_of, cut); assert.equal(data.company.trajectory.development_demo_case === null, cut < confirmation);
          if (cut < "2026-08-31") assert.equal(data.trajectory_metadata.backtest_summary, null);
          assert.ok(data.company.trajectory.points.every((p: any) => p.as_of <= cut)); regressionCuts++;
        }
        await page.getByLabel("Company", { exact: true }).selectOption(id); await expect(page.getByTestId("trajectory-cutoff")).toHaveText("2026-08-31");
        for (const cut of ["2024-09-30", confirmation, "2026-08-31"]) {
          await page.getByLabel("Closed month", { exact: true }).selectOption(cut); await expect(page.getByTestId("trajectory-cutoff")).toHaveText(cut);
          await expect(page.getByTestId("development-case")).toHaveCount(cut >= confirmation ? 1 : 0); await noCash(page);
        }
      }
    });
  });
  await workspace(join(root, "reports/modeling/assessment-v1/companies.json"), ["COMP_0001", "COMP_0002"], async (page, origin) => {
    await step("v1 eligible and abstained probability, old date behavior", async () => {
      await expect(page.getByRole("heading", { name: "Operating deficit proxy", exact: true })).toBeVisible();
      await expect(page.getByLabel("Estimated proxy probability")).not.toHaveText("Unavailable"); await noCash(page);
      assert.equal((await page.request.get(`${origin}/api/companies/COMP_0001/assessment?as_of=2025-05-31`)).status(), 400);
      await page.getByLabel("Company", { exact: true }).selectOption("COMP_0002"); await expect(page.getByLabel("Estimated proxy probability")).toHaveText("Unavailable");
    });
  });
  await workspace(join(root, "fixtures/companies.json"), ["DEMO_001"], async (page, origin) => {
    await step("cash demo forecast and planner preserved", async () => {
      await expect(page.getByRole("heading", { name: "Cash over time" })).toBeVisible();
      const data = await (await page.request.get(`${origin}/api/companies/DEMO_001/assessment`)).json(); assert.equal(data.forecast.funding_needed_cents, 210_000_000); assert.equal(data.cash_planning_available, true);
      assert.equal((await page.request.get(`${origin}/api/companies/DEMO_001/assessment?as_of=2025-05-31`)).status(), 400);
      await page.getByRole("button", { name: "Explore a plan" }).first().click(); await expect(page.getByRole("dialog")).toBeVisible();
      await page.getByRole("button", { name: "Close planning panel" }).click(); await expect(page.getByRole("dialog")).not.toBeVisible();
    });
  });
  assert.deepEqual(pageErrors, []); result.status = "passed";
} catch (error) {
  let message = error instanceof Error ? error.message : "Unknown failure";
  for (const secret of secrets) message = message.replaceAll(secret, "[REDACTED]");
  result = { ...result, status: "failed", failed_step: currentStep, error: message.slice(0, 5000) }; process.exitCode = 1;
} finally {
  await browser?.close(); for (const child of processes) await stop(child);
  result = { ...result, v3_api_cuts: apiCuts, v3_ui_cuts: uiCuts, trajectory_api_cuts: regressionCuts, max_response_bytes: maxPayload, page_errors: pageErrors, cleanup: "Only owned process groups and freshly created ephemeral identity directories removed" };
  const name = `browser-${new Date().toISOString().replaceAll(":", "-")}.json`;
  await writeFile(join(output, name), JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
  console.log(JSON.stringify({ report: join(output, name), ...result }));
}
