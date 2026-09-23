## Immediate next task

**Phase 15 ten-dollar canary — gate PASS, code not deployed:** the accelerated V3 statistical audit is PASS and the dedicated Johannesburg execution host probe is also PASS (`blocked=false`, `ZA/GP`). The current Master live gate is therefore PASS for the exact frozen V3. Durable host evidence is `docs/evidence/phase-15-v3-canary-execution-host-geoblock-20260923.json`.

The only authorized live contract is `phase15-v3-ten-dollar-canary-v1`: one live submission intent maximum, $10 fee-inclusive trade cost, $10 concurrent exposure, $10 daily-loss cap, one consecutive loss, frozen `min_edge=0.075`, and worker exit immediately after the first remote submission intent. The US host keeps V3 prediction/risk/ledger logic; only the official SDK signing/submission boundary is isolated to the unblocked Johannesburg host.

The repository must remain non-spending by itself. Do not commit, log, or automate wallet/private-key provisioning. Do not automate the final real-money activation. Those remain explicit operator boundaries outside GitHub automation. Continue V3 paper and V4 collection unchanged.

