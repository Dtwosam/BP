import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboardSource = readFileSync(
  new URL("../app/dashboard-client.tsx", import.meta.url),
  "utf8",
);

test("phase12 dashboard exposes paper execution evidence without real controls", () => {
  for (const marker of [
    "BP · Phase 12",
    "Paper execution account",
    "Reconciliation",
    "Paper orders",
    "Paper fills",
    "Paper settlements",
    "Real execution unavailable",
  ]) {
    assert.match(dashboardSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  assert.doesNotMatch(dashboardSource, /Paper P&L is intentionally unavailable/);
  assert.doesNotMatch(dashboardSource, /Place order|Submit order|Execute trade/);
});
