# Handoff: Forge, shadow readiness, and a kill switch that was not wired

**Date:** 2026-08-17
**By:** Cody
**Commit:** `b3dc9b0` (48 files, +12,051 / -47)
**Tests:** 887 passed, 1 skipped, 0 failed. `validate_harness.py` 21/21, exit 0.

---

## The thing worth reading first

**The shared kill switch did not cover Polymarket, and three places said it
did.**

`engine/halt.py` was written as the single definition, and its own docstring
explains that a third copy "was about to be added for the Polymarket path" so
every caller now imports it. `engine/executor.py` repeated the claim.
`botctl.py status` printed it to the operator at runtime:

> note: HALT covers BOTH the crypto executor and the Polymarket runner.

Nothing under `engine/polymarket/` imported `engine.halt`. `simulate_taker_buy`
is the only function that can open a Polymarket position and it had no halt
check. Nothing failed, because nothing tested the claim.

Fixed: the halt is now the FIRST guard in `simulate_taker_buy`, ahead of
position limits, price bands and sizing. Verified live (same order fills with
the switch clear, blocks with it engaged) and pinned by
`tests/test_polymarket_halt_wiring.py`, 12 tests, including one that fails if
any module under `engine/polymarket/` ever hardcodes its own HALT path again.

**One asymmetry you need to know about.** On the crypto path HALT also
FLATTENS. On Polymarket it can only block new entries, because a binary held to
resolution has no sell path in paper mode. A halt does not close Polymarket
exposure. `botctl status` now says that explicitly instead of letting an
operator infer that a halt closed the risk.

Mitigating context: no Polymarket runner exists, so nothing was actually
trading ungated. The exposure was that the next person to write a runner would
have trusted the claim.

---

## What the brief said vs what was there

Two items in the brief were already done by the previous session:

- **Task 1** (the failing `test_empty_book_reason_says_no_liquidity`) already
  passed. The empty-book path already logged `no_liquidity`, 60/60 green.
- What the brief did NOT mention: **22 tests were failing in
  `tests/test_polymarket_risk_gate.py`**, not 1 in the paper adapter. Those
  were the real blocker.

`.claude/agents/forge.md` could not be written: this session's permission config
denies writes under `.claude/`. The agent definition is authored at
**`agents/forge/forge.agent.md`** instead, which is the better home anyway since
`.claude/` is gitignored and the definition should be version controlled.
Install it with one command:

```bash
cp agents/forge/forge.agent.md .claude/agents/forge.md
```

That copy is the only outstanding step before Forge can be spawned as a
subagent. **Aym or Raven needs to run it.**

---

## The 22 risk-gate failures, by root cause

17 were implementation defects in `engine/polymarket/risk_gate.py`:

- A NaN premium propagated into the verdict (`float(premium or 0)`, and NaN is
  truthy), making `to_dict()` unserialisable under `allow_nan=False`. A string
  premium raised out of the gate instead of returning a verdict.
- **Unreadable positions bought headroom.** `aggregate_exposure` counted the
  skips but `check_order` never read the census, so nine unparseable positions
  measured as zero exposure and every USDC cap stopped binding. Convention 11.
- Case-sensitive market and side keys: `up` vs `Up`, `Yes` vs `Up`, `BTC-` vs
  `btc-` each held their own position on one market.
- Two different drops filed under one number (a NaN cost counted as
  non-positive). Split, and the identity assertion now sums every `skipped_*`
  key rather than a hardcoded pair. Convention 20.
- A NaN bankroll silently disabled the Kelly constraint: `max(nan, 0.0)` is NaN,
  which compares False against everything and drops out of `min()`.
- Degenerate `requested_shares` (0, -5, NaN) silently sized at the full cap.
- **The taker fee sat outside every cap.** Sizing used premium alone and added
  the fee afterwards, so real risk exceeded each cap by exactly the fee.

2 were in `engine/risk.py`, and this one is a live crypto-path defect, not a
test artifact:

> `get_day_open_equity` looked backward only (`ts <= midnight`). On any period
> whose first equity snapshot lands after the boundary - a fresh database, a
> restart after downtime, the first day of a deployment - it returned None, and
> `check_ops_backstops` guards on `is not None`, so **the daily ops stop was
> silently skipped entirely and nothing logged that it had not run.** It now
> falls forward to the earliest snapshot after the boundary. Convention 11: no
> carry-in row is not no drawdown, and the fallback errs toward tripping the
> backstop rather than disabling it.

3 were wrong constants in the test file:

- `1755432000` was labelled a UTC midnight. It is `2025-08-17T12:00:00Z`
  (`% 86400 == 43200`). Both constants sat inside the same UTC day, so no
  correct implementation could satisfy the test. Now real midnight boundaries.
- `4.0 <= pytest.approx(4.0)` raises `TypeError`: approx implements only
  `==`/`!=`. Replaced with a plain bound.
- `test_the_correlated_cap_binds_across_market_types` contradicted a sibling
  test on identical arithmetic. At $48 of exposure against a $50 cap the $2
  headroom is 4 shares, below `min_shares = 5`, so it was measuring the share
  minimum rather than the correlated cap. Setup moved to $44, leaving $6 of
  headroom. **Confirmed non-vacuous by mutation:** patching the correlation
  groups so each BTC timeframe sits in its own group makes the test fail, and
  the same mutant kills two sibling tests.

---

## Forge

Three pieces, all committed.

`agents/forge/forge.agent.md` is the operational agent definition (the narrative
half stays in `agents/forge/SOUL.md`). Hard boundary: Forge writes only to
`strategies/proposals/`, never to `strategies/builtin/`, `engine/` or
`backtest/`, and never runs a sweep.

`agents/forge.py` is the orchestrator. It loads the three evidence sources,
computes gaps, and enforces the schema **in code rather than by convention**:

| Refusal category | What it catches |
|---|---|
| `below_min_edge_bps` | under the 30bps floor (convention 5) |
| `duplicate_of_graveyard_entry` | re-proposing a swept strategy |
| `unmeasurable_kill_condition` | a kill condition naming no threshold (convention 6) |
| `non_finite_edge_estimate` | a NaN or inf that would poison the file (convention 19) |
| `repair_claims_an_edge` | a repair inventing a number for an unknown edge (convention 11) |
| `multi_class_edge_hypothesis` | MULTI used outside a cross-class repair |
| `missing_fields` / `unknown_asset_class` | schema |

Every refusal is counted BY CATEGORY into `strategies/proposals/forge_runs.jsonl`
and the accounting identity `screened - refused == written` is asserted, not
assumed (convention 20). All nine refusal paths were exercised directly and each
returns its own category; a valid control is accepted.

The gap analysis independently reproduces the exact nine non-firing strategies
from `judge_evidence_pack.json`, and reports PREDICTION_MARKET, OPTIONS and
FUTURES as absent asset classes (FUTURES correctly, post-D-261).

`agents/forge_candidates.py` holds the content, separated so the machinery can
be tested without it.

### The five proposals

| # | Name | Class | Est. gross | The honest caveat |
|---|---|---|---|---|
| 001 | `pm_dynamic_rotation` | PREDICTION_MARKET | 1200bps | needs historical CLOB DEPTH, which we do not have. A midpoint-only backtest would overstate it. |
| 002 | `pm_temporal_arbitrage` | PREDICTION_MARKET | 638bps | break-even leg-completion is ~89%. A 60% completion rate makes this a losing directional strategy wearing an arbitrage label. |
| 003 | `liq_cascade_spot_long` | CRYPTO | 70bps | moondevonyt's 10k trigger is dead on arrival on spot (10-30bps against a 22bps cost floor). Only survives at a 50M trigger, and may come back underpowered. |
| 004 | `nonfiring_nine_repair` | MULTI | null (repair) | see below |
| 005 | `pm_cross_window_relative_value` | PREDICTION_MARKET | 400bps | the 30-day paired gap distribution does not exist yet, so today this strategy has no entry rule at all. |

Each states an estimate with its arithmetic shown, a kill condition with a
number and a named harness, and the graveyard family it is adjacent to.
Proposal 003 explicitly engages why its nearest buried relative
(`V5_forced_flow_crypto`) failed, and adopts the mitigation as a requirement:
the scored window must be the INTERSECTION of the liquidation record and the
price history, and anything outside it is NOT_TESTED.

---

## The nine non-firing strategies: it is 2 bugs and 3 systemic conditions

Full report: `docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md`. It re-ran
the sweep's own pipeline with per-clause counters and **reproduces the
graveyard's exact trade counts for four of the nine**, so these are
measurements, not reads.

**Two genuine strategy defects.**

- `rsi_extreme` requires `rsi14 < 35` AND `close > ema50`. Over 42,010 bars:
  4,783 satisfy the first, 21,982 the second, and **zero satisfy both.** RSI(14)
  conditional on `close > EMA50` has a hard floor at 36.26, so the threshold
  sits below the support of the conditional distribution. Unsatisfiable, not
  tight. One-character fix.
- `C2` computes its anchor lookback as `24 * 4` BARS while meaning four DAYS.
  100% anchor failure on every sub-hourly series. Separately, **all 9,042 of its
  stale graveyard rows carry a reason string that no longer exists in the
  codebase**, so C2 has never run under current code.

**Three systemic conditions produce the other seven.**

1. **Bar starvation.** `min_idx = 100` against a last-20% test slice leaves
   daily series a median of ONE scannable bar: 5,100 across 175 series, 5.01% of
   the daily bars on disk. This reaches well past these nine. It means the daily
   evidence behind "509,080 tests" is overstated by roughly 20x.
2. **The confirmation stack is a trend filter applied to every strategy.** The
   sweep never sets `apply_confirmation_stack` or `require_regime_uptrend`, so
   both default True and every signal must satisfy `close > rising EMA50`. It
   removed 100% of `V2_vwap_magnet_sessionatr`, 99.5% of its control twin, 92%
   of `V5_capitulation_equity`'s candidate days, 87% of
   `V3_intraday_momentum_crypto`, 82% of `V4_trend_reclaim`. **A mean-reversion
   strategy filtered through "price is above a rising EMA50" has not been tested
   and found wanting. It has not been tested.**
3. **Unvalidated grid and coverage assumptions.** 1h equity bars stamp on the
   hour, so V2's `[930, 945)` trigger box is permanently empty. 1h crypto stamps
   23:00, so V3's 23:30 trigger is unreachable. The funding table overlaps the
   Binance price slices by 18 days. None raise; all produce silent zeros that
   read as verdicts.

### This needs a Raven ruling BEFORE execution

Conditions 1 and 2 change the graveyard's headline numbers. Both are arguably
corrections of a measurement error rather than a change in method, but neither
is Cody's call and neither should happen quietly between two sweeps.

The specific risk is the one convention 17 exists for: re-running with a looser
filter will make numbers look better, and that is exactly the shape of the
`COST_FLOOR = -0.30` false positive. Rule first, then run, then compare against
the pre-change numbers deliberately.

**Scope caveat, to be read narrowly:** the full sweep was not re-run, so the
"would become N findings" figures are raw-signal counts, not PASS counts. A
strategy that starts firing may still fail on economics. The claim is only that
seven of these nine were never given the chance to lose.

---

## Shadow readiness

- `run_shadow.sh` at the repo root, executable. Three refusal gates before
  launch: config `mode` must be exactly `paper`, `TRADING_LIVE_ACK` must be
  unset, HALT must be clear. Gate 3 imports `engine.halt` rather than testing a
  path, so the launcher cannot disagree with the engine about where HALT lives.
  All gates verified by execution, including that a config with no `mode:` key
  is refused rather than defaulting to paper.
- `docs/SHADOW-PREFLIGHT.md`, 9 sections. Every command in it was executed
  except two, which are tagged UNVERIFIED in the document (the healthy-start log
  lines, and `kill <pid>` behaviour).

Two things the preflight records because an operator would otherwise be misled:
`config.yaml logging.file: logs/engine.log` **is not wired** (no FileHandler
anywhere in the repo; the tee in `run_shadow.sh` is the only thing producing a
log file), and **nothing in `engine/` loads `.env`**, so editing it does not
change the engine's environment.

---

## Not mine, left alone

`dashboard/`, `tests/test_dashboard_charts.py`, `tests/test_dashboard_db_reader.py`
and a zero-byte `trading_bot.db` appeared in the working tree during this
session and were being written as recently as 21:05. They are not in this
session's scope and were NOT staged (convention 21). `trading_bot.db` is
untracked and unignored; someone should add it to `.gitignore`, which I did not
touch to avoid racing whoever is writing `dashboard/`.

---

## Next steps for Raven

1. **Rule on the two systemic harness conditions** (bar starvation, confirmation
   stack). Blocking: nothing should be re-swept until this is decided.
2. **Install the Forge agent definition:**
   `cp agents/forge/forge.agent.md .claude/agents/forge.md`
3. **Review the 5 proposals.** Reviewer checklist is in
   `strategies/proposals/README.md`. Proposal 003 is the weakest on edge and
   says so.
4. Decide whether `rsi_extreme` and `C2` get a D-number each (one-line fixes,
   but both change a strategy definition).
5. Decide whether C2's 9,042 stale rows get deleted before anyone cites them.
6. `trading_bot.db` and the `dashboard/` work need an owner.

Still owed from before, unchanged: Alpaca key rotation (D-262), the first
supervised paper run and kill-switch drill with Aym present (D-264), ratifying
D-217's 11 SOUL rules (D-244).
