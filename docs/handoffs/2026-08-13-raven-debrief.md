# Raven debrief - 2026-08-13, post three-way discussion

**Written:** 2026-08-13 17:11 EDT
**For:** Raven, catching up after being away
**Covers:** state of the repo as of right now, built off ROADMAP.md, DECISIONS.md
(v3-v6), STRATEGY-COVERAGE-STATUS.md, and a live check of running processes/logs.

One honesty note up front: the three-way discussion files and the cost-model
handoff are all timestamped 16:52-17:02 today, and it's 17:11 now - the
parallel-agent build (D-238 through D-242) landed in the run-up to and during
that discussion, not in a separate multi-hour gap after it. If it felt like
hours, the four-agent parallel build plus the discussion itself was the
"hours." Since 17:02 the only new thing is the queued chain arming (17:08)
and the background sweeps continuing to grind. I'm not going to inflate that
into more than it is.

---

## 1. What's been built (D-235 through D-242, the parallel-agent evening build)

Four subagents built four disjoint-file lanes in parallel this evening, each
tested independently and against the full suite:

- **`backtest/cost_model.py` + `TradeCoster`/`FlatCoster` (D-235)** - the
  four-regime cost model (crypto core/other, equity, futures) wired into both
  harnesses behind `use_cost_model: true` (opt-in, off by default = bit-
  identical to the old flat model). Every report row now stamped with
  `cost_model_version`/`asset_class`/`instrument`. New silent assertion
  `cost_model_version_uniform` blocks pooling entries from different cost
  regimes. 401 tests at the time, `validate_harness.py` 21/21.

- **`backtest/instruments.py` (D-235/P0.4)** - contract specs for
  futures/options: multiplier, integer-only contract sizing, margin as
  capital-at-risk, standard contracts remapped to reachable micros (ES->MES,
  CL->MCL). 9 tests. This is the file that makes concrete why all 79,642
  futures rows in the v0 library were fictional (below).

- **`backtest/cross_sectional.py` + `run_horizon_ladder.py` (D-241, SPEC
  5.8)** - the cross-sectional harness (full explanation in section 4). 29
  tests, lookahead-oracle verified. Implements Lab v3 #3 (Same-Clock Echo),
  v3 #5 (Paid Liquidity Reversal), and v5 P1 (Horizon Ladder).

- **`backtest/toll_collector.py` (D-240, Lab v5 P2)** - maker-vs-taker
  execution simulator. 13 tests. Ran and resolved (see section 3).

- **`backtest/dispersion_gate.py` (D-238, Lab v5 P3)** - derived entry gate
  from the toll law, with per-asset-class thresholds instead of the doc's
  flat one. 20 tests. Smoke-tested, full run queued.

- **`strategies/builtin/strategy_lab_v5.py` (D-239, Lab v5 P5)** - two new
  registered strategies (crypto forced-flow, equity capitulation). 21 tests.
  Fires but far below the statistical power bar (~5-8 events vs the 400-800
  needed) - a shrug, not a verdict.

- **`backtest/vr_fingerprint.py` (D-237, Lab v5 P4)** - built specifically to
  test its own kill condition, which fired immediately (cross-half Spearman
  -0.21/+0.07 vs a 0.3 bar). **Killed same day it was built**, before any
  P&L was read. This is the cheapest possible outcome for a proposal - the
  precheck did its job.

- **`docs/AGENT-RUNTIME-PROPOSAL.md` (D-242)** - answers your standalone
  question about whether the bot's agents can run independent of
  Hermes/Raven. Short version: yes, nothing in any code path depends on you
  two; the enforcement is deterministic machinery (sandbox, registry inbox,
  gates, kill switch, Aym-only promotion). Needs your ruling on three things
  (listed in section 8).

Net effect on the test suite: 355 tests this morning -> 517 after the
parallel build (475 fast + 42 harness) -> **524 collected right now**
(section 6 has the live number).

---

## 2. Graveyard sweep status: RUNNING, not done

Two processes are live right now (checked via `ps`):

- **`backtest/run_incremental_graveyard.py`** - PID 63767, started
  **16:01:26**, currently 68+ minutes in. This is the freshly-restarted v0
  library re-cost at the new cost-model rates (P0.3's control run). Log
  (`logs/graveyard_costmodel.log`) shows it working alphabetically through
  tickers: **18 of 176 ticker files done** (AAPL through ARM, plus
  BTC/ETH/SOL crypto pairs interspersed) as of the last log line (BA, in
  progress). At the observed rate (~3.8 min/ticker average, some tickers
  much slower - BA's 1h timeframe alone took 57s, 5m took longer) this
  points to roughly **8-10 more hours** to finish all 176 tickers. That's a
  rough extrapolation from 18 data points, not a promise.

- **`backtest/constraint_sweep.py`** - PID 63797, started same time, running
  the AGGRESSIVE/BASE/CONSERVATIVE gate-sensitivity sweep from D-234 (14
  series x 44 strategies x 3 exits x 3 levels). Log shows **264/616
  strategy-series done** on the current level. This was compute-bound before
  (D-234 flagged it as "RESULTS PENDING") and is still running.

- **A queued chain is armed** (`backtest/run_queued_chain.sh`, PID 69639,
  started 17:08, logging to `logs/queued_chain.log`). It's a nohup'd
  sequence that survives this session ending:
  1. wait for `run_incremental_graveyard.py` to exit (polls every 5 min)
  2. incremental pass for v4/CPI/v5 strategy combos
  3. dispersion gate full run (Lab v5 P3)
  4. horizon ladder full run (Lab v5 P1, both cost models)
  5. PLR full run (Lab v3 #5)

  Right now it's sitting at step 0, waiting on the main sweep. Nothing in
  steps 2-5 has started. This chain is what will produce the powered results
  for P3 (Dispersion Gate) and P1 (Horizon Ladder) that the roadmap lists as
  "queued behind the graveyard."

**Bottom line: nothing is finished yet.** The re-costed v0 control run,
the constraint sweep, and all four queued cross-sectional/dispersion runs
are still ahead of us tonight, likely running well past when either of us
next checks in.

---

## 3. v3 / v4 / v5 strategy labs

**Lab v2** (18 unorthodox cross-asset day-trading hypotheses, D-230/231):
9 of them implemented and **already in the running sweep** above (Wick
Autopsy, Round-Number Defense Decay, Liquidation Echo, Second-Break Verdict,
Volume Desert Breakout, VWAP Magnet x2 variants, Expiry Pin Drift, 0DTE
Afternoon Amplifier). Grid is 44 strategies x 11 exit configs.

**Lab v3** (literature-anchored, `docs/handoffs/2026-08-13-strategy-lab-v3.md`):
- #2 intraday momentum, #4 macro drift - built, in the sweep.
- #1 vacuum refill - adapted to 15m, built.
- #3 Same-Clock Echo, #5 Paid Liquidity Reversal - were blocked on the
  cross-sectional harness; **now implemented** inside
  `backtest/cross_sectional.py` (D-241). Smoke-tested only; powered runs are
  step 5 of the queued chain (PLR) plus part of the cross-sectional module.

**Lab v4 DEEP RENT** (LEAPS, `docs/STRATEGY-LAB-V3-V4-ASSESSMENT.md`): the
three IGNITIONS (PEAD, 52-week-high breakout, trend reclaim) are being built
as SHARE strategies first, since they're falsifiable without an options
data source we don't have. The LEAPS wrapper itself is explicitly parked -
can't honestly validate it without an implied-vol surface.

**Lab v5** (the four knob-changing experiments, one signal-invariant idea
each) - this is the one with real motion tonight:

| # | Name | Status |
|---|---|---|
| P1 Horizon Ladder | BUILT (D-241); powered run is step 4 of the queued chain |
| P2 Toll Collector | **DONE, RUN (D-240)**: kill condition did not fire, adverse selection ~1.3bps vs 12.6bps saved, but strategy P&L itself is a shrug (t=0.31, n=516) |
| P3 Dispersion Gate | BUILT (D-238), per-class thresholds; powered run is step 3 of the queued chain |
| P4 Fingerprint Router | **DEAD (D-237)** - killed by its own precheck same day, zero compute wasted |
| P5 Forced-Flow Harvest | BUILT + registered (D-239); event count is 8-16x below the power bar - shrug territory until more event-years accumulate |

So: 1 dead, 1 resolved-but-a-shrug, 3 built-and-waiting-on-compute.

---

## 4. The cross-sectional harness (SPEC 5.8) and why it matters

`backtest/cross_sectional.py`, 29 tests. This is a structurally different
harness from the two we already had (event-based and vectorized), and it's
not redundant with them - it answers a question they structurally can't.

Both existing harnesses are **time-series** harnesses: for one instrument,
walk forward through time, decide whether to enter now based only on that
instrument's own past. The cross-sectional harness instead builds a **panel**
across many instruments at aligned time steps, ranks them against each
other at each bar (top/bottom-K), and only then applies the existing
fill/cost machinery to whichever ones get selected.

Two reasons that matters:

1. **It's a different edge geometry.** A time-series strategy answers "is
   this instrument about to go up." A cross-sectional strategy answers "is
   this instrument about to go up *relative to its peers right now*" - which
   neutralizes market-wide drift by construction. If BTC, ETH, and SOL all
   rally together, a time-series momentum strategy fires on all three and
   credits the signal; a cross-sectional ranker only fires on the one that's
   outperforming the other two, so it can't accidentally get paid for "crypto
   went up today."

2. **It's structurally more paranoid about lookahead than the older
   harnesses.** The ranker never gets access to the current bar's own close
   - `PanelView` denies it by construction, one full bar more conservative
   than the time-series harness. The load-bearing test is a lookahead
   oracle: a cheating ranker that WOULD earn +20%/trade if it could see the
   leak instead earns -8% and produces bit-identical trades to an honestly
   lagged ranker. That's the proof the wall actually holds, not just that it's
   documented to hold.

It unblocked three things that were stuck without it: Lab v3 #3 (Same-Clock
Echo), v3 #5 (Paid Liquidity Reversal), and v5 P1 (Horizon Ladder). All
three are now implemented; none have a powered result yet - that's what
the queued chain is grinding toward tonight.

One deliberate scoping decision inside it: futures are excluded from pooled
dollar cells, because contract-dollar P&L would silently outvote $100 spot
clips in any pooled statistic - the same denominator problem P0.4 is about.

---

## 5. The cost model change and what it revealed

`backtest/cost_model.py` replaced a single flat 30bps-everywhere cost
assumption with four venue/asset-specific regimes, version-stamped and
wired opt-in into both harnesses (D-235). Measured round trips on a $100
clip:

| Class | Round-trip cost |
|---|---|
| Crypto core (BTC/ETH) | 12bps |
| Crypto other | 14bps |
| Equity/ETF | **4.2bps** |
| Futures (1 MES contract) | $2.06 (1.3bps of ~$34k exposure, $1,800 margin at risk) |

versus the old flat model's 30bps charged to everything.

**Two findings fell out of re-costing at accurate rates:**

1. **Equities were overcharged by roughly 7x.** 905,124 equity trades
   (65% of the entire v0 dataset) move from -$0.295/trade to -$0.037/trade
   at accurate costs - still negative, because gross edge on that population
   is only +$0.005, but the gap between "clearly dead" and "close to the
   line" is real and was previously hidden by an overcharged cost model.

2. **All 79,642 futures rows in the v0 library are fictional.** The old
   model sized futures the same way as spot - fixed $100 notional. Futures
   are per-contract, minimum one contract, and MES alone is ~$34,000
   notional. A $100 account literally cannot hold one contract at any price.
   Every futures backtest row ever produced by the v0 sweep represents a
   trade that could not have happened. `backtest/instruments.py` (D-235/
   P0.4) now sizes futures correctly in whole contracts with margin as
   capital-at-risk, but it's not yet wired into the main sweep loop -
   futures/options results must not be pooled with spot until it is (this
   is explicitly flagged as STILL TO DO in ROADMAP P0.4).

The rates themselves are still formally UNVERIFIED against the live Binance
account - that's P0.1, which Aym demoted from a hard blocker to a
shadow-test gate (D-236): the reference file Aym re-supplied was byte-
identical to what's already wired, so backtesting can proceed on the
current numbers; live-account verification only matters once shadow testing
starts.

P0.3 (re-run the v0 library at corrected costs as a control) is the
graveyard sweep running right now in section 2. Pre-registered prediction:
everything lands near -$0.14/trade. That prediction was written down
**before** this run started, which is exactly the discipline standing rule
4 asks for.

---

## 6. Current test count

**524 tests collected** (`pytest --collect-only`, just run). That's up from
517 cited in this evening's parallel-build decision (D-235 through D-242),
which itself was up from 355 this morning. The gap between 517 and 524
is probably a handful of tests added in the last hour of that build I
haven't individually attributed to a specific decision entry - not worth
chasing further right now, the trend line is what matters.

`validate_harness.py` was last reported 21/21 (D-235). I have not re-run it
live in this check since two heavy sweeps are currently eating CPU; wouldn't
want to contend with them for a validation run right now, but it's cheap
to run once the graveyard sweep frees up.

---

## 7. What I'd work on next (my read, not a decision)

1. **Let the queued chain finish and read the results cold, not while
   compute is contending.** The main sweep (P0.3 control), constraint
   sweep, dispersion gate, horizon ladder, and PLR are all either running or
   queued behind the running one. Nothing productive happens by starting new
   work that competes with them for CPU on this machine tonight. The highest
   leverage action right now is literally waiting well, then reading the
   five outputs together since they were designed to be read as one story
   (the cost-corrected library, the dispersion gate that should show the
   conservative-gate signal getting sharper, and the cross-sectional results
   that are a genuinely different edge geometry).

2. **Wire `backtest/instruments.py` contract sizing into the actual sweep
   loop** so futures/options runs stop being silently excluded/fictional and
   start producing real, poolable-within-class numbers. This is flagged
   STILL TO DO in ROADMAP P0.4 and is pure plumbing at this point - the
   contract-spec logic already exists and is tested (9 tests), it just isn't
   called from the main sweep yet.

3. **Get your ruling on D-217's SOUL conflicts and D-242's agent-runtime
   proposal before any agent goes live**, since both are sitting drafted-
   but-unratified and the roadmap explicitly orders "graveyard foundation
   before agents exist." There's no rush tonight, but it's the next gate
   after tonight's compute lands, and it's cheap to decide in parallel with
   the sweeps running rather than after.

---

## 8. Blockers / decisions needed

**From Aym:**
- Nothing new blocking tonight. P0.1 (live fee verification) is demoted to
  a shadow-test gate per his own ruling (D-236), so it's not in the way of
  anything running right now.
- Alpaca key rotation is still owed (D-110, flagged as SECURITY, open since
  the v1 audit) - not urgent but not closed either.

**From you (Raven):**
- **D-217**: five agent SOUL.md files were drafted with 11 rules encoded
  that aren't literally in SPEC 5.7 - they come from the audit and the
  validation review. The two that most need a ruling: (a) whether Quant's
  existing SOUL (single-draw random-twin rule) gets updated to match the
  new percentile-based rule the other SOULs now use (D-219 already changed
  the code; the SOUL text is the open piece), and (b) how narrowly to read
  "Coach cannot report to human" (routing-through-Echo vs. something
  stricter). None of this is load-bearing in code yet - only Quant's SOUL is
  active - so there's no rush, but it's cheap to rule on now rather than
  after agents are closer to live.
- **D-242 (`docs/AGENT-RUNTIME-PROPOSAL.md`)**: needs your ruling on the
  D-217 ratification path, an explicit Hermes/Raven carve-out statement, and
  what audit cadence replaces per-decision review once agents run more
  independently.

Nothing is waiting on either of you to unblock tonight's compute - the
sweeps run themselves. These three items are queued for whenever you next
have bandwidth, not urgent tonight.
