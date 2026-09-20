import assert from "node:assert/strict";
import { test } from "node:test";
import { closedMonth, observedRatio, precision, segments } from "./trajectory";
import type { TrajectoryPoint } from "./trajectory-types";

test("trajectory segments preserve precision and never bridge nulls or missing months", () => {
  const exact = 0.43328044815789507;
  const rows = segments([{ date: "2026-01-31", value: exact }, { date: "2026-02-28", value: null }, { date: "2026-03-31", value: 0 }, { date: "2026-05-31", value: -0.1 }, { date: "2026-06-30", value: 0.2 }]);
  assert.deepEqual(rows.map(row => row.length), [1, 1, 2]);
  assert.equal(rows[0][0].value, exact);
  assert.equal(rows[1][0].index, 2);
  assert.equal(precision(exact), String(exact));
  assert.equal(precision(null), "Unavailable");
  assert.deepEqual(segments([{ date: "2025-01-31", value: null }]), []);
});
test("closed month selection permits only server-advertised dates", () => {
  const available = ["2024-09-30", "2026-08-31"];
  assert.equal(closedMonth("2024-09-30", available), true);
  for (const value of ["2024-09-29", "2026-09-30", "invalid", "2024-9-30"]) assert.equal(closedMonth(value, available), false);
});
test("observed ratios are dimensionless and unknown is not zero", () => {
  const point = { evidence: { monthly: { input_eligible: true, gross_eur: 100, receipts_eur: 20, payments_eur: 70 } } } as TrajectoryPoint;
  assert.equal(observedRatio(point), -0.5);
  point.evidence.monthly.receipts_eur = null;
  assert.equal(observedRatio(point), null);
  point.evidence.monthly.receipts_eur = 0;
  point.evidence.monthly.gross_eur = 0;
  assert.equal(observedRatio(point), null);
  point.evidence.monthly.gross_eur = 100;
  point.evidence.monthly.input_eligible = false;
  assert.equal(observedRatio(point), null);
});
