import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const origin = process.argv[2] ?? "http://127.0.0.1:3150";
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
await context.addInitScript(() => localStorage.setItem("blau:welcome:v1:demo", "done"));
const page = await context.newPage();
const errors: string[] = [];
page.on("pageerror", error => errors.push(error.message));
mkdirSync(".local", { recursive: true });
try {
  await page.goto(`${origin}/demo?company=COMP_0009`);
  const panel = page.locator("#health");
  const toggle = panel.getByRole("button", { name: "Forecast", exact: true });
  const history = panel.getByRole("img", { name: "Monthly health score history" });
  await toggle.waitFor();
  assert.equal(await toggle.getAttribute("aria-pressed"), "false");
  assert.ok(await history.isVisible(), "Original health history is the default");
  assert.equal(await page.locator(".health-risk-alert").count(), 0);
  assert.equal(await page.locator(".receipt-outlook").count(), 0);
  const cash = page.locator(".monthly-cash");
  const cashBefore = await cash.boundingBox();
  const cashText = await cash.innerText();
  const obligationsBefore = await page.locator(".obligation-cards").boundingBox();
  await toggle.focus();
  await page.keyboard.press("Enter");
  assert.equal(await toggle.getAttribute("aria-pressed"), "true");
  assert.ok(!await history.isVisible(), "Forecast replaces the health content");
  assert.deepEqual(await cash.boundingBox(), cashBefore, "Cash card stays in place");
  assert.equal(await cash.innerText(), cashText, "Cash card content stays unchanged");
  assert.deepEqual(await page.locator(".obligation-cards").boundingBox(), obligationsBefore, "Other cards stay in place");
  assert.equal(await page.locator(".receipt-outlook").count(), 1);
  await panel.getByText("57.8", { exact: false }).waitFor();
  assert.equal(await panel.getByText("No separate forecast", { exact: true }).count(), 3);
  assert.match(await panel.innerText(), /30 days[\s\S]*60 days[\s\S]*90 days/);
  assert.match(await panel.innerText(), /Accuracy is not yet validated/);
  const summary = panel.locator("summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  assert.equal(await panel.locator("details").getAttribute("open"), "");
  assert.match(await panel.innerText(), /automatic alerts are disabled/);
  await page.keyboard.press("Enter");
  await panel.screenshot({ path: ".local/receipt-outlook-desktop.png" });
  await toggle.click();
  assert.ok(await history.isVisible(), "Toggling off restores the original history");
  assert.equal(await panel.locator(".receipt-outlook").count(), 0);
  await toggle.click();

  await page.getByRole("button", { name: "Selected month: August 2026" }).click();
  await page.getByRole("button", { name: "Previous year", exact: true }).click();
  await page.getByRole("button", { name: "Dec 2025", exact: true }).click();
  await panel.getByText("Predictions begin in January 2026.", { exact: false }).first().waitFor();
  assert.equal(await panel.getByText("Not available", { exact: true }).count(), 2);
  assert.equal(await panel.locator(".receipt-probability").count(), 0);

  const index = JSON.parse(readFileSync("reports/modeling/financial-v3/dataset-v1/model-v1/index.json", "utf8"));
  await page.goto(`${origin}/demo?company=${index.excluded_product_only[0].company_id}`);
  await toggle.click();
  await panel.getByText("This company was excluded", { exact: false }).first().waitFor();
  assert.equal(await panel.locator(".receipt-probability").count(), 0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/demo?company=COMP_0009`);
  await toggle.click();
  await panel.getByText("57.8", { exact: false }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "No mobile horizontal overflow");
  await panel.screenshot({ path: ".local/receipt-outlook-mobile.png" });
  await panel.getByText("How to read this outlook", { exact: true }).click();
  await panel.getByText("automatic alerts are disabled", { exact: false }).scrollIntoViewIfNeeded();
  assert.ok(await panel.locator(".health-outlook-view").evaluate(element => element.scrollTop > 0), "All forecast details remain reachable inside the mobile card");
  assert.deepEqual(errors, [], "No browser runtime errors");
  console.log("Receipt outlook passed: card-only keyboard toggle, original history restored, alerts removed, unchanged surrounding layout, real model data, month changes, exclusions and mobile layout.");
} finally {
  await browser.close();
}
