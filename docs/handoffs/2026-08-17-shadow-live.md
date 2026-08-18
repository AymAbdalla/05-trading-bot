# Shadow live + R-005 to R-009 executed

**Date:** 2026-08-17 22:20
**By:** Cody (session B, PID 15666), three subagents
**Instruction file:** `docs/handoffs/from-raven/2026-08-17-rulings-and-shadow-go.md`
**Status:** all five rulings implemented. Two long jobs still running.

## Verified state

- `backtest/validate_harness.py` — **21/21, exit 0**, A5 cross-harness AGREE
- Full suite — **946 passed, 1 skipped, 0 failed** (419s)
- Dashboard suite under `.venv` — **113 passed** (CLAUDE.md's "56 tests" is stale)
- Combined: **1,059 passing**
- Nothing committed. Nothing staged. The tree is left for review.

## READ THIS FIRST: three Cody sessions ran against this repo at once

Raven spawned two instruction files 24 minutes apart, and a third session later.

| session | PID | state | scope |
|---|---|---|---|
| A | 15357 | **stopped, blocked, exited** | `resweep-with-harness-fixes.md`, D-269..D-272 |
| B (this one) | 15666 | ran to completion | `rulings-and-shadow-go.md`, R-005..R-011 |
| D | 18002 | **still alive at 22:20** | 2 Forge proposals + `spread_harvest_maker`, edits `shadow_loop.py` |

Session A detected the collision, reverted `vectorized_harness.py` to HEAD, saved
its work as a patch under `docs/handoffs/patches/`, wrote
`2026-08-17-resweep-BLOCKED-two-sessions.md`, and exited without running
anything. That was the right call. Its patch was reviewed, found correct, and
**applied as the base** for R-005/R-006 rather than duplicated. Credit to it for
catching defect 1 below.

Session A's most serious warning — two concurrent sweeps interleaving gate-2 and
gate-3 rows into one 389MB incremental file, silently, with no way to tell which
row ran under which config — **did not materialise.** The re-sweep writes to a
new file and `v0_graveyard_full.json` is untouched (still 19:20, byte-identical
to the archive).

**Consequence you must know about:** the full-suite run at 22:04 showed 8
failures in `test_polymarket_shadow_loop.py`. Those were a snapshot of session D
mid-write, not a real break. Re-run alone: 34/34. Re-run of the whole suite
after D's file settled: 946 passed, 0 failed. **A red suite in this repo right
now may mean "another session is mid-edit", not "broken".** Check `ps` before
believing a failure (convention 21).

## R-005 — bar starvation

`min_idx` is now timeframe-aware: daily and weekly start at
`max(strategy.min_bars, 25)`, intraday stays at 100. Constant named
`SLOW_TF_MIN_SCAN_START` so the threshold is one line to change.

Three defects were found that would have made the ruling inert or wrong:

1. **R-005 was inert in the sweep path.** `run_strategy` scanned from 25 but
   `scan_all_bars` still filled from 100, and the sweep replays *precomputed*
   signals — so on daily every newly-scannable bar came back `None`. The two
   must agree exactly or signals are cached at bars the replay never visits.
   Locked by `test_scan_all_bars_and_run_strategy_agree_on_the_start`.
2. **Third starvation site: `exit_signal_bars`, still at 100.** 2 of the 11 exit
   configs are signal exits, so a daily trade entered at bar 30 could not exit
   on a bearish pattern until bar 100 — entry at 25, exit at 100. Fixed.
3. **`GATE_VERSION` was still 2.** Both rulings change PASS/FAIL semantics, and
   `assert_gate_version_uniform` is the only thing stopping the two eras being
   pooled. Bumped to **3**. This is why the re-sweep is `--force`, a full
   rebuild, not a resume.

Scannable bars, whole universe, last-20% test slice:

| tf | series | slice bars | before | after | change |
|---|---|---|---|---|---|
| 5m | 178 | 614,722 | 596,922 | 596,922 | identical |
| 15m | 178 | 222,778 | 204,978 | 204,978 | identical |
| 1h | 175 | 212,782 | 195,282 | 195,282 | identical |
| **1d** | 176 | 22,701 | **5,101** | **18,301** | +13,200 (3.59x) |
| **1wk** | 148 | 22,698 | **7,898** | **18,998** | +11,100 (2.41x) |

Reproduces the diagnosis exactly (5,100 daily / 7,898 weekly), so this is the
same universe, not a different measurement.

**The control is the load-bearing number:** SPY 1h, all 47 non-cohort
strategies, **47 identical, 0 changed**. The intraday graveyard is bit-identical,
so any intraday movement in the re-swept numbers is a bug, not the ruling.

A regression was caused and fixed: `validate_harness` A5 went to FAIL because
the referee compares three engines that each have their own warmup and R-005
moved only the vectorized leg (BTC_USD 13 trades → 15, breaking its ±4 tolerance
against backtesting.py's 9). An explicit `scan_start_override` pins the referee
to the legacy start, asserted unused by the sweep, so it compares mechanics
rather than windows. A5 back to AGREE.

## R-006 — confirmation stack off for the mean-reversion cohort

Implemented as a **declared property**, not a hardcoded name list:
`_stack_applies` reads `strategy.mean_reversion` first, name list second. Five of
the eight declare it on their own class. V3 is per-*instance*, so the equity twin
keeps its gate. The three in `expanded.py` are bridged by name in
`strategies/cohorts.py` because a sibling subagent owned that file during the
run. Every graveyard row is now stamped `confirmation_stack_applied` and
`scan_start_idx`.

Trades on 12 daily tickers, `fixed_2r`, before = legacy warmup + stack on
everything:

| strategy | in cohort | before | after |
|---|---|---|---|
| stoch_rsi_oversold | yes | 21 | **95** |
| bollinger_reversion | yes | 7 | **21** |
| rsi_extreme | yes | 3 | **8** |
| V5_capitulation_equity | yes | 2 | 2 |
| hammer | no | 0 | 2 (bar supply only) |
| V4_gap_hold_proxy | no | 2 | 2 |

**Contested, implemented as written, objection recorded in
`cohorts.CONTESTED_MEMBERSHIP`:** R-006 puts `V3_intraday_momentum_crypto` in the
mean-reversion cohort. Its thesis is momentum — the first half hour's return
predicts the last half hour's. Session A excluded it on exactly those grounds.
It was not silently overridden, but **if V3 comes back looking better after the
re-sweep, convention 17 says suspect the cohort assignment before believing the
result.**

## R-007 — rsi_extreme 35 → 45 (D-273)

Now a named class constant `RSI_MAX_ENTRY = 45.0`.

**The diagnosis was corrected against measurement, not just applied.** "Zero of
42,010 bars satisfy both `rsi<35` and `close>ema50`" is exact on 1d/1wk/1h but
**wrong on 15m/5m**, where the conditional RSI floor drops to 32.99 and 31.71 and
7 bars each do satisfy both. Honest claim: **14 firings in 1,010,181 bars
(0.0014%)**, not impossible everywhere. Ruling unchanged, wording corrected.

After the fix: 7,131 firings, ~0.7% of bars, stable across all five timeframes.
These are RAW signals taken *before* the confirmation stack; post-stack counts
will differ.

Unchased, recorded rather than rounded away: on 5m, `scan()` fires 4,179 against
4,177 clause-satisfying bars — a 2-in-596,922 disagreement between the numpy
Wilder RSI in `precompute_indicators` and the pure-Python one inside `scan()`.

## R-008 — C2 timeframe-aware lookback (D-274)

Anchor lookback 96 bars → a duration: 96 (1h, unchanged by construction), 384
(15m), 1,152 (5m). Weekly step and history guard likewise.

**The fix produced zero new signals.** What it changed is *which gate rejects*:
pre-fix, 15m/5m wrongly cleared the 840-bar history guard and then failed the
anchor search silently on 100% of them. Post-fix they fail the history guard,
which is the true reason. Honest outcome, not a win.

**Live convention 11 violation, one line to close:** `min_bars` must become
timeframe-aware for the fix to be reachable. The harness reads the class constant
before it knows the timeframe and hands `scan()` 840 bars; on 15m C2 genuinely
needs 3,360. `min_bars_for()` was added but `min_bars = 840` left alone, because
the call sites are in `vectorized_harness.py`. 3,360 and 10,080 both exceed
`MAX_STRATEGY_WINDOW` (2,000), so sub-hourly C2 is **NOT_TESTED** — but until the
gate calls `min_bars_for()`, those rows write as **FAIL**. Fix before citing.

**C2 does fire (convention 3 satisfied), on 1h, on full history:** 876 anchor-
resolved trigger bars → 244 clear the move gate → 46 clear volume → 36 up
(discarded, long-only spot) / **10 down → 10 signals**. None land in the last-20%
test slice, which is why the sweep sees nothing.

**C2 cannot fire on daily and no lookback fix reaches it.** Daily bars stamp at
hours 0/4/5 UTC, max hour 5; C2's trigger needs Sunday hour ≥ 22. Zero trigger
bars over 40 daily series, pre- and post-fix. C2 is hourly-or-finer. Its 1d/1wk
rows are NOT_TESTED, not FAIL.

Disclosed, not fixed: `valid_for=48` is the same units bug (48 bars, intended 48
hours). Outside R-008's ruling, and C2 trades zero, so no baseline is corrupted.

## R-009 — 9,042 C2 stale rows archived (D-275)

Exactly **9,042** — the expected number. Extracted by a streaming reader from the
stable pre-fix backup, not the live file, because a re-sweep was in flight.

| bucket | rows |
|---|---|
| scanned | 535,425 |
| non-C2 | 525,690 |
| C2 total | 9,735 |
| **C2 stale, archived** | **9,042** |
| C2 current gate string | 154 |
| C2 other (264 unsizable, 275 short-slice) | 539 |

Both accounting identities asserted (convention 20). Written with
`allow_nan=False`; portability proved with `node -e 'JSON.parse(...)'`
(convention 19). `v0_graveyard_full.json` was **not** rewritten — removal from
the active file is satisfied by the re-sweep regenerating it, and the audit trail
is closed by the archived count.

## D-numbers: collided, resolved, still need a tidy

`docs/DECISIONS.md` already held **D-269..D-272** from session A's instruction
file, meaning *different things* than this session's brief asked for:

| D | DECISIONS.md (session A) | this brief asked for |
|---|---|---|
| D-269 | bar starvation | rsi_extreme |
| D-270 | confirmation stack | C2 lookback |
| D-271 | rsi_extreme | C2 stale rows |
| D-272 | C2 lookback + stale rows | — |

Three duplicate D-numbers were **not** created. **D-273, D-274, D-275** were
appended as implementation records that close out D-271 and D-272 with measured
numbers, and **D-275 explicitly supersedes D-272's "delete the stale rows" with
R-009's later "archive, do not delete"** — the two rulings were in direct
opposition. Code docstrings cite the same numbers, so DECISIONS.md and the code
agree.

**Still owed:** R-005/R-006 have no D-number of their own. Ready-to-merge text is
in the subagent report; it needs a number assigned by whoever owns the sequence.

## Polymarket shadow loop: LIVE

- **PID 17603**, alive, log `logs/polymarket_shadow_20260818T015010Z.log`
- `engine/polymarket/shadow_loop.py` + `ShadowStore`, `run_polymarket_shadow.sh`
- `tests/test_polymarket_shadow_loop.py` — 34 tests, fully offline, no network
- At 22:17: **298 cycles, 1,192 evaluations, 0 entries, 1,192 skips,
  equity $1,000.00, `identity_ok=True`**
- Market slug has rolled over repeatedly, so it is tracking real 5-minute
  windows, not a pinned market

Writes to `db/trading.db`: `signals`, `orders`, `fills`, `positions`,
`equity_snapshots` (first row exactly 1000.0), `audit_log`, `risk_events`. No
schema changes needed. WAL + `synchronous=NORMAL` + `busy_timeout`, one short
transaction per write; the dashboard reads it concurrently `mode=ro`.

`evaluations == entries + sum(skips)` **and** `== cycles * n_strategies` are
asserted every 60s, logged at ERROR and written to `audit_log` as
`accounting_violation` if broken. `no_market` / `api_error` / `no_liquidity`
never share a bucket — `_fetch_book_checked` was added because `fetch_orderbook`
collapses "outage" and "empty book" into one `None`.

### Only 1 of the 4 strategies can currently fire. This is a data gap, not a verdict.

| strategy | can fire | blocker |
|---|---|---|
| `PM_streak_snapper` | **yes** | — live gates working on real ATR/streak |
| `PM_mid_price_continuation` | no | needs window strike (Chainlink 60s TWAP), Gamma does not publish it |
| `PM_corridor_collector` | no | same missing strike ⇒ no `lead_bps` |
| `PM_box_builder` | no | maker strategy, returns QUOTE |

Spot was **not** substituted for the strike: spot is wrong precisely mid-move,
which is when these strategies trade. Simulating box_builder's resting bid as a
taker lift would manufacture the fills its edge depends on. **Per convention 11
these three are NOT_TESTED. A zero-entry session must not be read as four
strategies looking and declining.**

Zero entries so far is also expected on the merits: streak_snapper needs 4+
same-direction windows AND |cum move| > 3x ATR AND ask ≤ 0.52 AND within 20s of
window open. Hours between fires. The entry path is proven end-to-end in tests
(19 shares @ 0.50, orders+fills+positions+audit written, resolution settles to
exit_px=1.0, pnl_net=$9.50, r_multiple=1.0), **not on live data yet**.

A duplicate loop was caught: another session had launched the same script one
minute earlier. Two writers on one SQLite file would have produced two
independent $1,000 accounts interleaved into one equity curve. Per convention 21
the *newer* process (ours) was shut down gracefully, not the other session's.
~172 signals are duplicated per cycle across that 2-minute overlap; no positions
existed, so no PnL is affected.

## Re-sweep: RUNNING, NOT FINISHED

- **PID 18543**, log `logs/resweep_R005_R006.log`
- `run_incremental_graveyard.py --force --out research/graveyard/v0_graveyard_R005_R006.json`
- **Why this runner:** the only one carrying all 55 strategies and the sweep's
  confirmation config. `run_full_graveyard.py` has ~35; `run_vectorized_graveyard.py`
  writes a different file with only `expanded`.
- **Why `--out`:** the live 389MB artifact stays valid and complete until you
  promote it.
- Progress at 22:17: healthy, no errors, on SOL/USDT 1h. The comparable prior
  full sweep took **42,183s ≈ 11.7 hours**, so expect completion tomorrow morning.

**Promotion is a deliberate act, not automatic.** After it finishes, verify the
row count is 535,425, compare against the pre-fix baseline (535,425 rows /
48,675 distinct_findings / PASS 381 / FAIL 486,350 / NOT_TESTED 48,642 / C2 stale
9,042), and only then
`mv research/graveyard/v0_graveyard_R005_R006.json research/graveyard/v0_graveyard_full.json`.

**Convention 17 applies with full force.** Both rulings only removed a constraint
or loosened a filter. Numbers will improve. That is the exact shape of the
`COST_FLOOR = -0.30` false positive. The control (47/47 non-cohort strategies
identical on SPY 1h) is what makes an improvement believable; check it first.

## Needs a ruling

1. **`V3_intraday_momentum_crypto` in the mean-reversion cohort.** Contested
   above. Already baked into the running sweep.
2. **Wire `min_bars_for()`** so sub-hourly C2 writes NOT_TESTED instead of FAIL.
   Live convention 11 violation.
3. **Second starvation site, untouched:** `run_incremental_graveyard.py` skips
   any series whose test slice is `< 100` bars — the same hardcoded 100, and why
   25 weekly series are excluded. Lowering it adds series and changes the
   denominator. Left as one change at a time.
4. **Warmup contamination, and it is real:** `_ema` fills the pre-convergence
   region with `closes[0]`, so on daily bars 25–49 `ema50` is a seed, not an
   EMA-50, and `regime_uptrend` is False there by construction. **Any PASS
   landing on daily bars 25–49 needs a second look before it is believed.** Both
   rulings specify 25, so 25 was implemented.
5. **Tidy the cohort bridge:** move the three `expanded.py` strategies to
   `mean_reversion = True` and delete `COHORT_BRIDGE_EXPANDED_PY`. Deliberately
   *not* done mid-sweep — it is semantically a no-op (tests assert the resolved
   cohort is identical either way) and churning source under an 11.7-hour job
   buys nothing.
6. **Assign a D-number to R-005/R-006.**

## Loose ends, stated so they are not mistaken for results

- **`rising_three_methods` and `V4_trend_reclaim` were covered by no ruling** and
  will still be non-firing after the re-sweep. V4's 27/27 candidates die on
  `volume_min_ratio`, not the regime filter. Reporting them as "still non-firing
  after the fixes" without saying they were never in scope would misread as
  evidence about the strategies.
- **`V5_capitulation_equity` before-number is unconfirmed.** The 12-ticker probe
  gives it 2 trades under the pre-fix config; the archived graveyard records 0
  across all 9,735 rows. Probably sector-aware cost/sizing (`sector=None` was
  passed). Confirm against the archive before citing.
- **Firing is not edge.** 7,131 rsi_extreme signals and 10 C2 signals are
  testable samples, not results. Nothing here says either strategy passes.
- **Not verified on live data:** Gamma outage behaviour (tests only), and a real
  resolution settling a real position (no entry has fired).
- **Write volume:** ~78k `signals` rows/day. Fine for SQLite, but worth a
  retention decision before this runs for weeks.
- **Session D is still alive** and has added `temporal_arbitrage.py`,
  `cross_window_relative_value.py`, `spread_harvest_maker.py` and edited
  `shadow_loop.py`. The running loop imported the 4-strategy version at start
  (convention 13), so process and file have diverged. **Nobody has said who
  restarts the loop or when.**
- **`cp agents/forge/forge.agent.md .claude/agents/forge.md` is still not done.**
  Blocked by the permission layer in this session, same as the last one. Needs a
  manual run or a grant.

## Files

**New:** `engine/polymarket/shadow_loop.py`, `run_polymarket_shadow.sh`,
`tests/test_polymarket_shadow_loop.py`, `tests/test_r007_r008_fixes.py`,
`tests/test_harness_warmup_cohort.py`, `strategies/cohorts.py`,
`backtest/archive_c2_stale_rows.py`, `backtest/snapshot_graveyard.py`,
`research/graveyard/archive/c2_stale_rows.json`,
`research/graveyard/v0_graveyard_pre_R005.json` (APFS clone, ~0 disk)

**Modified:** `backtest/vectorized_harness.py`,
`backtest/run_incremental_graveyard.py`, `backtest/cross_harness_check.py`,
`strategies/builtin/expanded.py`, `strategies/builtin/strategy_lab.py`,
`strategies/builtin/strategy_lab_v2.py`, `_v3.py`, `_v5.py`,
`docs/DECISIONS.md`, `research/graveyard/harness_validation.json`

**Not ours, left alone (convention 21):** everything session D is writing under
`strategies/polymarket/`.
