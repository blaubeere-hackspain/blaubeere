import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { chromium, expect, type Browser, type Page } from "@playwright/test";
import type { ProxyCompany } from "../apps/app/lib/types";

const root = resolve(import.meta.dir, "..");
const output = join(root, "reports/modeling/assessment-v1");
const assessmentFile = join(output, "companies.json");
const bytes = await readFile(assessmentFile);
const companies = JSON.parse(bytes.toString()) as ProxyCompany[];
const manifest = JSON.parse(await readFile(join(output, "manifest.json"), "utf8"));
const sha256 = (data: Uint8Array) => createHash("sha256").update(data).digest("hex");
assert.equal(sha256(bytes), manifest.companies_sha256);
assert.equal(companies.length, 1286);
const eligible = companies.find(c => c.predictive.accepted && c.predictive.eligible && c.predictive.probability_estimate !== null)!;
const abstained = companies.find(c => !c.predictive.eligible && c.predictive.probability_estimate === null)!;
const inaccessible = companies.find(c => c.id !== eligible.id && c.id !== abstained.id)!;
const secrets: string[] = [];
const steps: { name: string; status: string }[] = [];
const processes = new Set<ChildProcess>();
const pageErrors: string[] = [];
let currentStep = "initialization";
let browser: Browser | undefined;
let result: Record<string, unknown> = {
  command: "bun run test:assessments:e2e", assessment_file: "reports/modeling/assessment-v1/companies.json",
  companies_sha256: sha256(bytes), source_sha256: manifest.source_sha256,
  rows_loaded_by_api: companies.length, steps,
  environment: "Process-only configuration; fresh temporary SQLite per scenario; random unlogged bootstrap password; loopback-only browser requests; no screenshots or stored sessions",
};

async function step(name: string, action: () => Promise<void>) {
  currentStep = name;
  await action();
  steps.push({ name, status: "passed" });
}

async function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolvePort(port));
    });
  });
}

function start(command: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) {
  const process = spawn(command, args, { cwd, env, detached: true, stdio: "ignore" });
  processes.add(process);
  process.on("error", () => {});
  return process;
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
    assert.equal(child.exitCode, null, "Local server exited before readiness");
    try { if ((await fetch(url, { signal: AbortSignal.timeout(2000) })).ok) return; } catch {}
    await Bun.sleep(250);
  }
  throw new Error("Bounded local server readiness timed out");
}

async function workspace(file: string, ids: string[], test: (page: Page, appOrigin: string) => Promise<void>) {
  const directory = await mkdtemp(join(tmpdir(), "blaubeere-assessment-e2e-"));
  const apiPort = await freePort();
  let appPort = await freePort();
  while (appPort === apiPort) appPort = await freePort();
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  const appOrigin = `http://127.0.0.1:${appPort}`;
  const password = randomBytes(32).toString("base64url");
  secrets.push(password);
  const email = "assessment-e2e@blaubeere.local";
  const api = start(join(root, "target/debug/blaubeere-api"), [], directory, {
    PATH: process.env.PATH, HOME: directory, RUST_LOG: "error",
    DATABASE_URL: `sqlite://${join(directory, "identity.sqlite")}`,
    ASSESSMENT_FILE: file, API_BIND: `127.0.0.1:${apiPort}`,
    API_ORIGIN: apiOrigin, APP_ORIGIN: appOrigin, MCP_RESOURCE: `${apiOrigin}/mcp`,
    BOOTSTRAP_EMAIL: email, BOOTSTRAP_PASSWORD: password, BOOTSTRAP_COMPANIES: ids.join(","),
  });
  const app = start(Bun.which("node")!, [join(root, "apps/app/node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", String(appPort)], join(root, "apps/app"), {
    PATH: process.env.PATH, HOME: process.env.HOME, NODE_ENV: "development", NEXT_TELEMETRY_DISABLED: "1", API_INTERNAL_URL: apiOrigin,
  });
  const context = await browser!.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route("**/*", route => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", error => pageErrors.push(error.name));
  try {
    await ready(`${apiOrigin}/health`, api);
    await ready(`${appOrigin}/login`, app);
    await step(`${ids[0]} unauthenticated assessment rejected`, async () => {
      assert.equal((await context.request.get(`${appOrigin}/api/companies/${ids[0]}/assessment`)).status(), 401);
    });
    await step(`${ids[0]} login through browser UI`, async () => {
      await page.goto(`${appOrigin}/login`);
      await page.getByLabel("Work email").fill(email);
      await page.getByLabel("Password", { exact: true }).fill(password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
      await expect(page.getByLabel("Company", { exact: true })).toHaveValue(ids[0]);
    });
    await test(page, appOrigin);
  } finally {
    await context.close();
    await stop(app);
    await stop(api);
    await rm(directory, { recursive: true, force: true });
  }
}

async function noCashUi(page: Page) {
  await expect(page.getByRole("heading", { name: "Cash over time" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Health, in context" })).toHaveCount(0);
  await expect(page.getByText("Usable cash today", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Outlook", { exact: true })).toHaveCount(0);
  await expect(page.locator(".cash-chart")).toHaveCount(0);
  for (const button of await page.getByRole("button", { name: "Explore a plan" }).all()) await expect(button).toBeDisabled();
  await expect(page.getByText(/Cash planning is unavailable/)).toBeVisible();
}

try {
  currentStep = "launch system Chromium";
  const executablePath = process.env.CHROMIUM_PATH ?? Bun.which("chromium") ?? Bun.which("chromium-browser");
  assert.ok(executablePath, "System Chromium is required; set CHROMIUM_PATH");
  browser = await chromium.launch({ executablePath, headless: true, chromiumSandbox: true });
  result.browser = { engine: "Chromium", version: browser.version(), executable: executablePath, headless: true, sandbox: true };
  await workspace(assessmentFile, [eligible.id, abstained.id], async (page, origin) => {
    await step("eligible probability, definition, fixed calendar horizon and validation limits", async () => {
      await expect(page.getByRole("heading", { name: "Operating deficit proxy", exact: true })).toBeVisible();
      await expect(page.getByLabel("Estimated proxy probability")).toHaveText(`${(100 * eligible.predictive.probability_estimate!).toFixed(1)}%`);
      await expect(page.getByText("Fixed horizon: 3 calendar months", { exact: true })).toBeVisible();
      await expect(page.getByText(/Conditional on 131\/299 observed labels across 31 groups/)).toBeVisible();
      await expect(page.getByText("Not calibrated", { exact: true })).toBeVisible();
      await expect(page.getByText(/Synthetic · reconstructed · retrospective/)).toBeVisible();
      await expect(page.getByText(eligible.predictive.definition, { exact: true })).toBeVisible();
      await noCashUi(page);
    });
    await step("keyboard input evidence disclosure", async () => {
      const summary = page.locator("summary").filter({ hasText: "Observed input features" });
      await summary.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("region", { name: "Observed proxy input features" })).toBeVisible();
      await page.keyboard.press("Enter");
    });
    await step("mobile proxy remains usable without fake cash charts", async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(page.getByLabel("Estimated proxy probability")).toBeVisible();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
      await page.setViewportSize({ width: 1440, height: 1000 });
    });
    await step("actual exported API data matches and compare rejects both proxy rows", async () => {
      for (const company of [eligible, abstained]) {
        const response = await page.request.get(`${origin}/api/companies/${company.id}/assessment?days=180&buffer_cents=0`);
        assert.equal(response.status(), 200);
        const body = await response.json();
        assert.equal(body.forecast, null);
        assert.equal(body.cash_planning_available, false);
        assert.equal(body.company.opening_cash_cents, null);
        assert.equal(body.company.health, null);
        assert.deepEqual(body.company.predictive.observed_features, company.predictive.observed_features);
        assert.equal(body.company.predictive.probability_estimate, company.predictive.probability_estimate);
        const rejected = await page.request.post(`${origin}/api/companies/${company.id}/plans`, {
          headers: { Origin: origin },
          data: { metric: "ending_cash", target_cents: 0, deadline: "2026-11-30", cash_floor_cents: 0, max_collection_days: 0, max_spend_reduction_pct: 0, max_funding_cents: 0, max_growth_pct: 0, business: null },
        });
        assert.equal(rejected.status(), 400);
        assert.match((await rejected.json()).error, /Cash planning is unavailable/);
      }
    });
    await step("company isolation preserved", async () => {
      const list = await (await page.request.get(`${origin}/api/companies`)).json();
      assert.deepEqual(list.map((c: { id: string }) => c.id), [eligible.id, abstained.id]);
      assert.equal((await page.request.get(`${origin}/api/companies/${inaccessible.id}/assessment`)).status(), 403);
    });
    await step("keyboard company switch shows abstention and reason with no enabled planner", async () => {
      await page.getByLabel("Company", { exact: true }).focus();
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(page.getByLabel("Company", { exact: true })).toHaveValue(abstained.id);
      await expect(page.getByLabel("Estimated proxy probability")).toHaveText("Unavailable");
      for (const reason of abstained.predictive.unavailable_reasons) await expect(page.getByRole("listitem").filter({ hasText: reason.replaceAll("_", " ") }).first()).toBeVisible();
      await noCashUi(page);
    });
    result.proxy = { eligible_id: eligible.id, probability: eligible.predictive.probability_estimate, displayed_probability: `${(100 * eligible.predictive.probability_estimate!).toFixed(1)}%`, abstained_id: abstained.id, unavailable_reasons: abstained.predictive.unavailable_reasons, api_compare_status: 400 };
  });
  await workspace(join(root, "fixtures/companies.json"), ["DEMO_001"], async (page, origin) => {
    await step("original demo cash forecast, health, horizon and planner regression", async () => {
      await expect(page.getByRole("heading", { name: "Cash over time" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Health, in context" })).toBeVisible();
      await expect(page.getByLabel("Outlook", { exact: true })).toBeEnabled();
      const body = await (await page.request.get(`${origin}/api/companies/DEMO_001/assessment`)).json();
      assert.equal(body.cash_planning_available, true);
      assert.equal(body.forecast.funding_needed_cents, 210_000_000);
      await page.getByRole("button", { name: "Explore a plan" }).first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.getByRole("button", { name: "Close planning panel" }).click();
      await expect(page.getByRole("dialog")).not.toBeVisible();
    });
  });
  assert.deepEqual(pageErrors, []);
  result = { ...result, status: "passed", page_errors: pageErrors, cleanup: "Only owned process groups and temporary databases stopped/removed" };
} catch (error) {
  let message = error instanceof Error ? error.message : "Unknown failure";
  for (const secret of secrets) message = message.replaceAll(secret, "[REDACTED]");
  result = { ...result, status: "failed", failed_step: currentStep, error: message.slice(0, 4000) };
  process.exitCode = 1;
} finally {
  await browser?.close();
  for (const child of processes) await stop(child);
  const summary = JSON.stringify(result, null, 2) + "\n";
  await writeFile(join(output, "browser-summary.json"), summary);
  console.log(summary);
}
