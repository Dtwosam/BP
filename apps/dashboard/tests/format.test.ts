import assert from "node:assert/strict";
import test from "node:test";

import {
  formatAgeSeconds,
  formatDecimal,
  formatProbability,
  formatUtc,
} from "../lib/format.ts";

test("probabilities are formatted without changing the source value", () => {
  const source = "0.812345678901234567";

  assert.equal(formatProbability(source), "81.23%");
  assert.equal(source, "0.812345678901234567");
});

test("decimal formatter keeps explicit unavailable state", () => {
  assert.equal(formatDecimal(null, 4), "—");
  assert.equal(formatDecimal("0.005000000000000000", 4), "0.0050");
});

test("timestamps are explicit UTC", () => {
  assert.match(formatUtc("2026-08-28T12:00:00Z"), /UTC$/);
  assert.equal(formatUtc(null), "—");
});

test("age formatting never invents negative freshness", () => {
  assert.equal(formatAgeSeconds("2026-08-28T12:00:00Z", "2026-08-28T12:00:05Z"), "5s");
  assert.equal(formatAgeSeconds("2026-08-28T12:00:05Z", "2026-08-28T12:00:00Z"), "0s");
  assert.equal(formatAgeSeconds(null, "2026-08-28T12:00:00Z"), "—");
});
