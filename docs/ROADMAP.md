# Roadmap

**Created:** 2026-08-13
**Owner:** Aym (decisions) / Raven (sequencing) / Claude Code (build)
**Rule:** no live trading until Aym explicitly approves. Go-live criteria are
prerequisites, not triggers (SPEC section 2, enforced in `engine/main.py`).

---

## Where the project actually is

**Built and trustworthy:** the measurement apparatus. Two backtest harnesses
that agree with each other and with an external engine, a 21-check validation
suite (oracle-through-harness, delayed-oracle lookahead detector, fee
application, cross-engine agreement), result-quality silent assertions, pooled
and asset-class analysis with leave-one-asset-out, a selection-bias validator,
strategy sandbox with AST allowlist and hash pinning, paper engine with
executor/reconciliation/kill switch, 261 tests.

**Measured and dead:** the v0 strategy library. 1,390,451 pooled trades,
implied GROSS edge **+$0.0011/trade**. Not "small edge lost to costs" - no
edge to begin with.

**The asymmetry:** the apparatus is the asset. Strategies are fungible.

---

## P0 - Blocking, do first

### P0.1 Verify the actual fee schedule (DEMOTED to shadow-test gate, Aym 2026-08-13)
Two independent reviewers flag that Binance.US may have moved to 0% maker /
0.02% taker (2026-04-22), against our modeled 0.10% taker. ccxt's public
market data still reports 0.10%, but that may be its static default; real
rates are account-specific and need an authenticated call.

**Why it is P0:** it does NOT rescue the dead library (verified: gross is
+$0.0011, so any positive cost loses), but it halves the bar for everything
downstream. Rule-3's 30bps hurdle becomes ~14bps taker, or near-zero maker
plus adverse selection.

**Action:** Aym checks the live account fee page or runs an authenticated
`fetch_trading_fees`. One number, unblocks the cost model.

### P0.2 Cost model as a first-class variable (SPEC 5.9) - DONE 2026-08-13
**BUILT:** `backtest/cost_model.py` implements all four regimes from
`references/broker-fee-reference-2026.md`, version-stamped `2026-08-13`, with
tests pinning the structural differences. Measured round trips on a $100
clip: crypto core 12bps, crypto other 14bps, **equity 4.2bps**, versus the
**30bps the old model charged everything**. Futures: $2.06 round trip on one
MES contract ($1,800 margin at risk).

**WIRED (D-235):** both harnesses accept `use_cost_model: true`; futures size
in whole contracts (ES->MES) with margin as capital-at-risk; every entry
stamped `cost_model_version`/`asset_class`/`instrument`; silent assertion
`cost_model_version_uniform` rejects cross-version pooling; twin caches key
on the full cost identity. 401 tests, validate_harness 21/21, referee agrees.
Flat mode (default) is bit-identical to the legacy model. The rates
themselves are still UNVERIFIED against the live account (P0.1).

**Two findings from re-costing the library at accurate rates:**
- Equity trades (905,124 of them, 65% of all data) were charged roughly **7x**
  the real cost. At accurate costs they move from -0.295 to -0.037 per trade -
  still negative, because gross is +0.005.
- **All 79,642 FUTURES rows are fictional.** Futures are per-contract on a
  minimum of one contract; MES is ~$34,000 notional. A $100 fixed-notional
  model cannot trade them at any price. They must be re-scoped to a real
  contract size or removed from the universe.

### P0.3 Re-run the v0 library at corrected costs as a CONTROL
Pre-registered prediction (Lab v5): everything lands at approximately
-$0.14/trade, confirming gross is zero at the new floor. If anything lands
materially above that, the gross-zero conclusion was cost-model-dependent and
must be re-examined. Cheap, and it validates the cost plumbing.

---

## P0.4 - Contract-instrument sizing (futures and options)

**BUILT 2026-08-13:** `backtest/instruments.py` + 9 tests. Contract specs with
multiplier, integer-only sizing, and MARGIN as capital-at-risk. Standard
contracts route to their reachable micro equivalent (ES -> MES, CL -> MCL).

**The finding it makes concrete:** at $100, ZERO futures contracts are
tradable - which is why all 79,642 futures rows are fictional. At $2,000:

| Ticker | Micro | Tradable | Exposure | Capital at risk | Leverage |
|---|---|---|---|---|---|
| ES_F | MES | yes | $38,865 | $1,800 | 19.4x |
| NQ_F | MNQ | NO | - | - | - |
| CL_F | MCL | yes | $8,300 | $1,400 | 4.2x |
| GC_F | MGC | yes | $44,631 | $1,900 | 22.3x |
| RTY_F | M2K | yes (2) | $30,524 | $1,800 | 15.3x |

**WIRED 2026-08-13 (D-247):** the real bug was one level deeper than "not
wired" - `TradeCoster` (D-235) already imported `instruments.spec_for` and
correctly computed margin as capital-at-risk, but the actual contract
QUANTITY was a hardcoded `contracts=1` default that its own constructor
then floored to a minimum of 1, so `instruments.py`'s affordability check
(`size_for`) was imported but never actually called from the sizing path.
Every futures trade silently traded exactly one contract regardless of
whether the account could afford the margin. Fixed at the shared
`TradeCoster`/`CostModel.coster()` layer (not per-harness), so
`vectorized_harness.py`, `harness.py`, and `cross_sectional.py` all inherit
the honest behavior: contract quantity now comes from
`InstrumentSpec.size_for(notional_cap, price)`, which returns 0 (a real,
not-tradable answer) when the account cannot afford one contract, feeding
the harness's existing `qty <= 0 -> skip trade` path instead of a fictional
fill. At the sweep's default $100 cap, futures now correctly produce ZERO
trades; at $2,000 they size as shown in the table above. Pooling guard
added too: `pooled_analysis.pool()` now excludes FUTURES/OPTIONS from
dollar-pooled cells by default (`asset_class_analysis.py` remains where
they ARE analyzed, keyed by class) - matching the exclusion
`cross_sectional.py` already made for the same reason.

**Still open:** the graveyard's existing 79,642 futures rows were produced
under the old bug (always 1 contract) and need a targeted re-run once the
current sweeps (P0.3 control, constraint sweep) finish - not done in this
pass, since it would compete with those for CPU and isn't itself blocking
anything. `run_incremental_graveyard.py` only tests NEW combinations, so a
re-run needs those old entries removed from the graveyard JSON first,
which is a deliberate call for whoever runs it, not something to do
silently as a side effect of a code fix.

**The risk nobody sees in a bps table:** one MES on a $2,000 account is ~19x
leverage. A 5% adverse index move is a 100% account loss. Futures have the
best cost physics in the landscape AND the worst blowup profile for a small
account. Both facts are true and the second one is why this is not a v1 lane.

## P1 - The cross-sectional harness (SPEC 5.8) - BUILT 2026-08-13 (D-241)

DONE. `backtest/cross_sectional.py`, 29 tests, lookahead-oracle verified.
Unblocked Lab v3 #3 and #5 (both implemented) and Lab v5 P1 (implemented).
Powered runs queued behind the graveyard rebuild. A different edge geometry from everything tested so far, and it
neutralizes market drift by construction rather than by subtraction.

Deliverables: universe-aligned time stepping, per-bar ranking with no
same-bar lookahead, top/bottom-K selection, existing fill and cost semantics,
time-matched twins, survivorship stamping, pooled plus leave-one-out reporting.

---

## P2 - The four knob-changing experiments (Lab v5)

Each changes ONE structural assumption rather than proposing signal #36. Each
ships with a pre-registered prediction and a named kill condition.

| # | Knob | Test | Needs |
|---|---|---|---|
| P1 Horizon Ladder | holding period | BUILT (D-241), powered run queued | backtest/run_horizon_ladder.py |
| P2 Toll Collector | execution side | RUN (D-240): kill condition not fired, adverse selection ~1.3bps vs 12.6bps saved; strategy P&L a shrug (t=0.31); vol prediction inverted | backtest/toll_collector.py |
| P3 Dispersion Gate | entry condition, DERIVED | BUILT (D-238) with PER-CLASS c (equity gate 3.3x sharper than the doc's flat 14bps); full run queued | backtest/dispersion_gate.py |
| ~~P4 Fingerprint Router~~ | ~~instrument character~~ | **DEAD 2026-08-13 (D-237)**: VR fingerprint failed its own stability precheck (Spearman -0.21 / +0.07 vs 0.3 bar) | backtest/vr_fingerprint.py |
| P5 Forced-Flow Harvest | event definition | BUILT + registered (D-239); ~8 raw events, FAR below the 400-800 power bar - a shrug until more event-years | strategies/builtin/strategy_lab_v5.py |

**P3 first.** It reuses everything already built, it is the cheapest powered
test in the set, and it formalizes the one non-negative signal seen so far
(the conservative gate) with a threshold DERIVED from the toll law rather than
scanned from results.

---

## P3 - Strategy labs already written

- **Lab v3** (literature-anchored): #2 intraday momentum and #4 macro drift
  are testable now and being built. #1 vacuum refill adapted to 15m. #3 and #5
  wait on the cross-sectional harness.
- **Lab v4 DEEP RENT** (LEAPS): the three IGNITIONS are BUILT as SHARE
  strategies (`V4_gap_hold_proxy`, `V4_52w_high_breakout`, `V4_trend_reclaim`
  in `strategies/builtin/strategy_lab_v4.py`, 16 tests, wired into the
  sweep) - see D-246. I1 is honestly labeled a price-only PEAD proxy (no
  earnings calendar exists, same gap as Lab v5's capitulation-equity leg).
  Powered results ride the same graveyard sweep as everything else. The
  LEAPS wrapper itself remains parked - cannot be honestly validated
  without an implied-volatility surface we do not have.
- **Lab v2**: 9 strategies built and in the running sweep.

---

## P4 - Engine work before any paper session matters

1. First supervised paper run + kill-switch drill (NEEDS AYM).
2. Slippage instrumentation is live: realized-vs-assumed bps recorded on every
   fill. First session answers whether 0.05% is right.
3. Registry loader for shadow-mode strategies (uses `sandbox.verify_hash`).
4. Telegram alerts (SPEC 6.2/6.3), then launchd.
5. Full live-mode reconciliation before live is even discussed.

---

## P5 - Data gaps, ranked by what they unblock

| Gap | Unblocks | Cost |
|---|---|---|
| FRED API key | CPI dates for macro strategies | free, 30 seconds (AYM) |
| Earnings calendar | Lab v3 #5 news exclusion, Lab v4 earnings guard | free-ish |
| 1m crypto bars | faithful Vacuum Refill | free, Binance archives |
| 4h timeframe | untested horizon between 1h and 1d | free, resample |
| Real options chains | any honest Lab v4 test | $99-600/mo |
| Survivorship-complete equities | removes a known bias | $50-500/mo |

---

## P6 - Agent runtime (separate track, does not compete with P0-P5)

Search-and-measurement (above) and agent-runtime are different tracks that
don't block each other. See `docs/AGENT-RUNTIME-PROPOSAL.md` (the design)
and `docs/agent-proposal-reconciliation.md` (what was agreed vs. the
proposal); governance decisions are D-243/D-244/D-245 in DECISIONS.md.

Priority order: (1) `agents/judge.py` (Judge-as-code, wraps the existing
validate_harness/assertions/pooled-analysis machinery - useful immediately,
no LLM needed), (2) Forge pointed at the surviving lab proposals once
judge.py can evaluate what it writes, (3) Scout/Coach/Echo activate when
there is something to scout for, coach, and report on. The agent runtime
earns nothing until the search track (above) finds a survivor to manage -
both proceed in parallel, but this is not the bottleneck.

---

## Standing rules (earned, not assumed)

1. No result is durable unless `validate_harness.py` exits 0.
2. Cite `distinct_findings`, never raw pass counts.
3. Verify a strategy FIRES on real data before interpreting any result.
4. Conditions must be predicted before testing, never discovered by scanning.
5. Estimate gross edge in bps before writing code; under the cost hurdle means
   dead on arrival.
6. Every proposal states a kill condition.
7. A FAIL on a 200k-trade strategy is a verdict; a FAIL on a 1,700-trade
   strategy is a shrug. Report which one it is.
