# Phase 14 Permanent Research Daemon Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permanently install the research-only live predictor and prospective-outcome daemons on the production host with exact-head verification, rollback, and no live-money capability.

**Architecture:** Reuse the existing `/opt/bp` checkout and permanent virtualenv. A Cloud Shell wrapper verifies the feature-branch SHA and launches a candidate installer from a detached worktree; the installer snapshots current checkout/unit state, switches `/opt/bp` to the exact candidate, installs both hardened units, verifies the existing five core services plus dashboard safety, and rolls back all owned changes on any failure.

**Tech Stack:** Bash, systemd, Git, Python 3.12, pytest static deployment-contract tests, GitHub Actions, Google Cloud Shell/GCE.

**Spec:** `docs/superpowers/specs/2026-08-31-phase14-permanent-research-daemon-install.md`

## Global Constraints

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- no wallet, signing, real-order submission, funding, promotion, or live-enable path
- no database migration or package installation in this slice
- preserve recorder, PostgreSQL, dashboard API/web, and paper worker availability
- installation success is operational continuity only and cannot alter any Master live-gate result
- Phase 15 remains blocked

## Production correction note

The first production install attempt on candidate `196519555bed8f68d37654bd171dac23f681fd52` failed before mutation with `deployed_checkout_not_clean`. Read-only host inspection showed the established Phase 11/12 dashboard build residue rather than arbitrary application-source edits: modified `apps/dashboard/next-env.d.ts` and `apps/dashboard/tsconfig.json`, plus untracked `.node/`, `apps/dashboard/.next/`, `apps/dashboard/node_modules/`, and `apps/dashboard/tsconfig.tsbuildinfo`.

The approved spec supersedes the original generic clean-check language below for production execution. The corrected implementation must allow only those known dashboard-generated paths, fail closed on every other status entry, reject any candidate that tracks/collides with the untracked runtime paths, preserve the untracked runtime roots, and restore the pre-install bytes of the two tolerated tracked generated files on rollback. This correction does not relax source-integrity checking beyond that explicit allowlist.

---

### Task 1: Lock the permanent-install deployment contract with RED tests

**Files:**
- Create: `tests/deploy/test_phase14_prospective_runtime_install.py`
- Modify: `deploy/bp-live-predictor.service`
- Create later: `scripts/deploy/phase14_prospective_runtime_install.sh`
- Create later: `scripts/deploy/phase14_prospective_runtime_cloudshell.sh`
- Modify later: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: existing `deploy/bp-live-predictor.service`, `deploy/bp-prospective-outcomes.service`, and CI shell validation.
- Produces: static contracts that the implementation must satisfy before production rollout.

- [ ] **Step 1: Write the failing tests**

Create tests asserting:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_predictor_unit_pins_research_zero_money_boundary() -> None:
    content = (ROOT / "deploy/bp-live-predictor.service").read_text()
    for line in (
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
    ):
        assert line in content


def test_permanent_installer_is_exact_head_rollback_capable_and_two_daemon_only() -> None:
    content = (ROOT / "scripts/deploy/phase14_prospective_runtime_install.sh").read_text()
    # Assert root/exact SHA/deployed-checkout integrity, safety settings, five core services,
    # candidate checkout, rollback trap, both unit backups and state restoration,
    # both daemon enable/start, dashboard safety probe, evidence, and PASS marker.


def test_cloudshell_wrapper_verifies_remote_branch_sha_and_runs_candidate_installer() -> None:
    content = (ROOT / "scripts/deploy/phase14_prospective_runtime_cloudshell.sh").read_text()
    # Assert required exact head, branch fetch equality, detached candidate worktree,
    # sudo installer invocation, cleanup, and no secret or live-money arguments.


def test_ci_syntax_checks_permanent_runtime_scripts() -> None:
    content = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "bash -n scripts/deploy/phase14_prospective_runtime_install.sh" in content
    assert "bash -n scripts/deploy/phase14_prospective_runtime_cloudshell.sh" in content
```

- [ ] **Step 2: Run CI and verify RED**

Expected: Ruff and all existing tests pass; the new deployment-contract tests fail only because the predictor unit lacks explicit safety overrides and the two new scripts/CI validation do not yet exist.

- [ ] **Step 3: Commit the RED checkpoint**

Commit message: `test: require fail-closed prospective runtime install`

---

### Task 2: Implement the hardened two-daemon installer and wrapper

**Files:**
- Modify: `deploy/bp-live-predictor.service`
- Create: `scripts/deploy/phase14_prospective_runtime_install.sh`
- Create: `scripts/deploy/phase14_prospective_runtime_cloudshell.sh`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/deploy/test_phase14_prospective_runtime_install.py`

**Interfaces:**
- `phase14_prospective_runtime_install.sh EXPECTED_HEAD` executes as root from a verified candidate worktree and mutates `/opt/bp` plus only the two prospective systemd units.
- `phase14_prospective_runtime_cloudshell.sh` consumes `PHASE14_PROSPECTIVE_RUNTIME_HEAD` and optional project/zone/VM/branch overrides, verifies the remote branch SHA, and invokes the candidate installer.

- [ ] **Step 1: Harden the predictor unit**

Add exactly:

```ini
Environment=MODE=research
Environment=LIVE_TRADING_ENABLED=false
Environment=MAX_TRADE_SIZE_USD=0
Environment=MAX_DAILY_LOSS_USD=0
```

Keep the existing frozen Phase 9 source calibration run IDs and unprivileged systemd hardening.

- [ ] **Step 2: Implement the installer**

The installer must:

```text
require root + 40-hex expected SHA
require /opt/bp deployed-checkout integrity and /etc/bp/bp.env present
allow only the approved dashboard-generated tracked/untracked residue; reject everything else
reject candidate collisions with preserved untracked dashboard runtime paths
read and require research/live=false/zero/zero
require recorder/postgres/dashboard-api/dashboard-web/paper active
require candidate installer checkout HEAD == expected SHA
capture old /opt/bp HEAD and symbolic ref
capture existing predictor/outcome unit files plus active/enabled state
backup tolerated modified Next-generated tracked files for rollback
validate both candidate units and forbidden money/live strings
arm EXIT rollback before checkout/unit mutation
checkout /opt/bp detached at expected SHA while preserving untracked dashboard runtime roots
verify live_prediction and prospective_outcomes imports from /opt/bp/.venv
copy both units, daemon-reload, enable --now both
wait until both stay active and verify effective process safety environment
require all five existing services still active
probe dashboard snapshot for RESEARCH, live=false, execution_available=false, paper_execution_available=true
require /opt/bp HEAD == expected SHA and revalidate deployed-checkout integrity
write sanitized /var/lib/bp/evidence/phase14-prospective-runtime-install-<UTC>.txt
print PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS
```

Rollback must stop the two prospective daemons, restore/remove their previous unit files and root-controlled safety file, restore enabled/active states, restore the previous `/opt/bp` ref/HEAD, restore the pre-install bytes of tolerated modified Next-generated tracked files, preserve untracked dashboard runtime roots, and reload systemd.

- [ ] **Step 3: Implement the Cloud Shell wrapper**

The wrapper must:

```text
require PHASE14_PROSPECTIVE_RUNTIME_HEAD as 40 lowercase hex
set project/zone/VM defaults used by prior Phase 14 helpers
verify gcloud auth
validate deployed-checkout integrity using the same narrow dashboard-generated allowlist
remote fetch exact feature branch into origin/<branch>
require fetched SHA == expected SHA
create detached candidate worktree
run candidate installer as root
remove worktree on exit
verify /opt/bp HEAD == expected SHA
verify both prospective daemons active+enabled and five core services active
re-read research/live=false/zero/zero
emit PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS
```

It must not accept or pass wallet/private-key/order/funding/live-enable arguments.

- [ ] **Step 4: Add CI bash syntax validation**

Add:

```bash
bash -n scripts/deploy/phase14_prospective_runtime_install.sh
bash -n scripts/deploy/phase14_prospective_runtime_cloudshell.sh
```

- [ ] **Step 5: Run the targeted tests and full CI**

Expected: all deployment-contract tests and the complete Python/dashboard/deployment/health lanes pass.

- [ ] **Step 6: Commit GREEN**

Commit message: `feat: add permanent prospective research runtime install`

---

### Task 3: Exact-head verification and production host acceptance

**Files:**
- No implementation change unless a failing test or host defect requires a new RED/GREEN cycle.
- Add sanitized host evidence only after PASS.

**Interfaces:**
- Consumes final CI-green exact SHA.
- Produces host evidence that both research daemons are permanently active/enabled with the money boundary unchanged.

- [ ] **Step 1: Verify exact-head CI and operational smokes**

Require CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak to pass on the exact candidate SHA.

- [ ] **Step 2: Run Cloud Shell permanent install**

```bash
cd ~/BP
git fetch origin phase14-prospective-outcome-sync
git checkout phase14-prospective-outcome-sync
git pull --ff-only
export PHASE14_PROSPECTIVE_RUNTIME_HEAD=<FINAL_40_CHAR_SHA>
bash scripts/deploy/phase14_prospective_runtime_cloudshell.sh
```

- [ ] **Step 3: Validate host evidence**

Require the PASS marker, exact deployed HEAD, both prospective services active+enabled, all five core services active, research/live=false/zero/zero, and sanitized evidence path.

- [ ] **Step 4: Record host evidence and governance**

After PASS only, update `docs/MASTER-SOURCE-OF-TRUTH.md`, `docs/DECISION-LOG.md`, `docs/CHANGELOG.md`, and `PROJECT_STATE.json` to mark permanent install PASS without changing the live-gate matrix. Add sanitized host evidence JSON.

- [ ] **Step 5: Run final exact-head CI after governance/evidence cleanup**

Require all final gates green before considering PR #14 merge-ready.
