import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboardSource = readFileSync(
  new URL("../app/dashboard-client.tsx", import.meta.url),
  "utf8",
);
const snapshotSource = readFileSync(
  new URL("../lib/snapshot.ts", import.meta.url),
  "utf8",
);

test("phase14 dashboard exposes read-only live readiness diagnostics", () => {
  for (const marker of [
    "BP · Phase 14",
    "Live readiness",
    "Activation authorized",
    "Kill switch",
    "Geoblock",
    "Wallet configured",
    "Critical discrepancies",
    "Real execution unavailable",
  ]) {
    assert.match(dashboardSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  assert.match(snapshotSource, /live_readiness/);
  assert.match(snapshotSource, /critical_discrepancy_count/);
  assert.doesNotMatch(
    dashboardSource,
    /Place order|Submit order|Execute trade|Enable live|Cancel live|Authorize trading/,
  );
});
