// Run against a running app: bun scripts/check-chart-layout.ts http://localhost:3100 [landing URL]
import assert from "node:assert/strict";
import { chromium, expect, type Page } from "@playwright/test";

const app = process.argv[2] ?? "http://localhost:3100";
const landing = process.argv[3];
const browser = await chromium.launch({ headless: true });
async function fillsCards(page: Page) {
  await expect.poll(() => page.evaluate(() => {
    const health = document.querySelector<SVGSVGElement>(".health-history-chart svg")!;
    const cash = document.querySelector<SVGSVGElement>(".cash-flow-plot svg")!;
    return Math.max(
      Math.abs(health.viewBox.baseVal.width - health.getBoundingClientRect().width),
      Math.abs(cash.getBoundingClientRect().width - cash.parentElement!.getBoundingClientRect().width),
      Math.abs(cash.viewBox.baseVal.width - cash.getBoundingClientRect().width),
    );
  }), { message: "Both plots must fill their cards after animation and resizing" }).toBeLessThan(1);
}
try {
  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("blau:welcome:v1:demo", "done"));
    await page.goto(`${app}/demo?company=COMP_0318`);
    await page.locator(".cash-flow-plot svg").waitFor();
    // The entrance can finish after the initial width is correct; check its settled state.
    await page.waitForTimeout(1200);
    await fillsCards(page);
    await page.getByRole("button", { name: "Hide sidebar" }).click();
    await fillsCards(page);
    await page.getByRole("button", { name: "Show sidebar" }).click();
    for (const width of [1280, 390, 1920]) {
      await page.setViewportSize({ width, height: 1080 });
      await fillsCards(page);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    const plot = page.locator(".cash-flow-plot");
    const totals = page.locator(".cash-flow-totals");
    const totalValues = await totals.locator("dd").allTextContents();
    await plot.focus();
    await page.keyboard.press("Home");
    await expect(totals).toHaveAttribute("aria-label", "Cash flow for 1 Aug 2026");
    await page.keyboard.press("End");
    await expect(totals).toHaveAttribute("aria-label", "Cash flow for 31 Aug 2026");
    await page.keyboard.press("Escape");
    assert.deepEqual(await totals.locator("dd").allTextContents(), totalValues);
    const svg = plot.locator("svg");
    await svg.scrollIntoViewIfNeeded();
    const bounds = (await svg.boundingBox())!;
    await page.mouse.move(bounds.x + 68 + (bounds.width - 80) * 14.5 / 31, bounds.y + bounds.height / 2);
    await expect(totals).toHaveAttribute("aria-label", "Cash flow for 15 Aug 2026");
    await page.mouse.move(0, 0);
    await expect(totals).toHaveAttribute("aria-label", "Selected month cash totals");
    await page.getByRole("button", { name: "Selected month: August 2026" }).click();
    await page.getByRole("button", { name: "Jul 2026", exact: true }).click();
    await expect(page.locator(".monthly-cash .card-heading")).toContainText("July 2026");
    await page.waitForTimeout(1200);
    await fillsCards(page);
    if (landing) {
      await page.goto(landing);
      await page.locator(".cash-flow-plot svg").scrollIntoViewIfNeeded();
      await page.waitForTimeout(1200);
      await fillsCards(page);
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Chart layout and cash inspection passed with animations enabled and reduced motion.");
} finally { await browser.close(); }
