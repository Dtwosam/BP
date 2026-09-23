# Phase 15 Frozen V3 Live Canary

This runbook is for the **one-order** frozen-V3 canary only.

## Preconditions

- current clean checkout exactly matches `origin/main`;
- source truth version `0.14.180`;
- Master live gate is fully `pass`;
- user physical-network geography is unblocked;
- Johannesburg execution host direct geoblock is unblocked;
- no real order has yet been submitted under this canary;
- V3 and V4 remain unchanged.

## Risk

The first canary uses the existing **$5 strategy target**. The user's **$10** authorization is the hard per-market ceiling and also the maximum total exposure and daily loss ceiling. Exactly **one network submission attempt** is allowed, whether accepted, rejected, or ambiguous. Fresh selected-side displayed liquidity must be at least $5.

## Operator sequence

### 1. Bootstrap signer — no order

```bash
cd ~/BP
git fetch origin
git checkout main
git pull --ff-only origin main

PHASE15_ACCEPT_WALLET_SETUP=yes \
bash scripts/deploy/phase15_v3_canary_bootstrap_cloudshell.sh
```

Enter the private key only at the hidden Cloud Shell prompt. Do not paste it into chat.

Expected boundary: `TRADING_ORDER_SUBMITTED=false`, `KILL_SWITCH_ENGAGED=true`. Bootstrap health must also show zero official open orders and at least $5 collateral.

### 2. Prepare — no order

```bash
bash scripts/deploy/phase15_v3_canary_prepare_cloudshell.sh
```

The helper first verifies the official account has zero open orders and at least $5 collateral. It then waits for a **new** frozen-V3 trade signal, requires at least $5 of fresh displayed selected-side liquidity, applies the live-risk rules, and also requires at least **30 seconds remaining to market end before persisting an intent**. This 30-second prepare-time armability floor is an operational safety margin; the canonical live-risk minimum remains 15 seconds. Candidates inside the 30-second floor are skipped as `insufficient_arm_window` and create no live intent. Eligible candidates are persisted and written to:

```text
/tmp/bp-phase15-v3-canary-prepared.json
```

Expected boundary: `NO_REAL_ORDER_SUBMITTED=true`.

Review the printed side, target notional, limit price, shares, and market end time.

### 3. Arm — still no order

```bash
PHASE15_ACCEPT_REAL_MONEY=yes \
bash scripts/deploy/phase15_v3_canary_arm_cloudshell.sh
```

The arm is valid for at most 45 seconds and only for the exact prepared intent/request and exact executor SHA-256. It rechecks account cleanliness/collateral, removes the kill switch, but submits nothing.

If arm fails **before activation/submission** because the prepared market is no longer armable, do not run the submission command. The persisted intent must first be reconciled as closed-before-submission. That reconciliation is allowed only when the Johannesburg executor reports the kill switch engaged, activation invalid, submission not ready, no live order submitted, zero official open orders, and at least $5 collateral. It records a `closed_before_submission` event that does **not** consume the one permitted network submission attempt.

The production reconciliation is a separate explicit mutation boundary:

```bash
PHASE15_ACCEPT_UNSUBMITTED_RECONCILIATION=yes \
bash scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh
```

Only after that helper returns `PHASE15_V3_CANARY_RECONCILE_UNSUBMITTED=PASS` may prepare be run again.

### 4. Manual one-shot submission

Run only after the arm helper returns PASS:

```bash
cat /tmp/bp-phase15-v3-canary-prepared.json | \
gcloud compute ssh bp-v3-canary-exec \
  --project=project-4397f2c0-7098-4c1c-abb \
  --zone=africa-south1-a \
  --quiet \
  --command='sudo /opt/bp-canary/executor.sh' | \
tee /tmp/bp-phase15-v3-canary-result.json
```

The executor consumes the arm by re-engaging the kill switch **before** its SDK submission attempt. It then attempts cancellation after the two-second TTL.

Do not rerun the submission command if the result is missing, malformed, or ambiguous.

### 5. Record result

```bash
bash scripts/deploy/phase15_v3_canary_record_cloudshell.sh
```

Then stop. Do not arm or submit again, even if the first network attempt was rejected or returned an ambiguous result.

## After the canary

Official order/fill reconciliation is mandatory before any second order can be discussed. A successful first order does not authorize higher sizing.
