# HANDOVER — Trading Bot, session of 2026-08-12/13

**Read this first, then `docs/ROADMAP.md`, then `docs/DECISIONS.md` (D-101 to D-242).**
Project root: `/Users/aympulse/aym/projects/05-trading-bot`

---

## 1. Current objective

Build a trustworthy measurement apparatus, then a graveyard of what works and
what does not, so the agents (Forge/Judge/Coach/Echo/Scout) have something
real to learn from. Agents come AFTER the foundation. **No live trading until
Aym explicitly says so** (SPEC section 2; also hard-enforced in
`engine/main.py`, which refuses any mode except paper).

## 2. STATE AFTER THE 2026-08-13 EVENING PARALLEL BUILD (D-235..D-242)

Afternoon: cost model + instrument specs wired into both harnesses (D-235),
graveyard + constraint sweep restarted at venue-accurate costs, FRED key in,
CPI macro-drift variant + Lab v4 share ignitions registered.

Evening (four parallel subagents, disjoint files, all verified):
1. **Cross-sectional harness BUILT** (SPEC 5.8, D-241) - v3 #3/#5 and v5 P1
   implemented on it; powered runs queued.
2. **P2 Toll Collector RUN** (D-240) - adverse selection ~1.3bps vs 12.6bps
   fee savings: passive execution survives as an execution-cost story;
   strategy P&L itself is a shrug (t=0.31); vol-band prediction inverted.
3. **P3 Dispersion Gate BUILT** (D-238) - derived per-class thresholds
   (equity 3.3x sharper than v5 doc's flat assumption); full run queued.
4. **P5 Forced-Flow BUILT + registered** (D-239) - ~8 raw events, far below
   power bar; shrug until more event-years.
5. **P4 Fingerprint Router DEAD** (D-237) - failed its own pre-registered
   VR-stability precheck (Spearman -0.21/+0.07 vs 0.3 bar); no routed
   backtest was ever run.
6. **Agent-runtime proposal written** (D-242, docs/AGENT-RUNTIME-PROPOSAL.md)
   - agents CAN run independent of Hermes/Raven; three decisions are Aym's.

**517 tests total (475 fast + 42 harness). validate_harness.py 21/21.**

**Next actions when the compute frees up (all queued, automatic):**
incremental graveyard pass (v4 + CPI + v5 combos) -> dispersion gate full
run -> horizon ladder full run -> PLR full run. Then: P0.3 control
comparison against the -$0.14 pre-registration, re-run pooled/asset-class
analyses against the NEW graveyard only, and read the queued experiment
results against their pre-registrations.

## 3. Background jobs RUNNING RIGHT NOW

| Job | State | Log |
|---|---|---|
| Graveyard sweep (55-strategy era begins at the queued pass; current run is 49) | running, saves per ticker | `logs/graveyard_costmodel.log` |
| Constraint sweep | running | `logs/constraint_sweep_costmodel.log` |
| QUEUED: incremental pass (v4+CPI+v5) | armed, fires when sweep exits | `logs/graveyard_v4_cpi_pass.log` |
| QUEUED: dispersion gate -> horizon ladder -> PLR | armed, fires after incremental pass | `logs/dispersion_gate.log` etc. |

## 4. State of the work

### Built and trustworthy (the project's actual asset)
- Two backtest harnesses that agree with each other and with an external
  engine (`backtest/cross_harness_check.py`)
- 21-check validation suite (`backtest/validate_harness.py`) — oracle runs
  THROUGH the harness, delayed-oracle lookahead detector, fee application,
  cross-engine agreement. **Exit 0 required before trusting any result.**
- Result-quality silent assertions (`backtest/assertions.py`)
- Pooled + asset-class analysis with automatic leave-one-asset-out
- Selection-bias validator (`backtest/conditional_edge.py`)
- Strategy sandbox: AST allowlist, subprocess conformance, hash pinning
- Paper engine: executor, reconciliation, kill switch, resting orders,
  realized-slippage instrumentation
- **355 tests passing** (`python3 -m pytest tests/ -q`)

### Measured and dead
The v0 library: **1,390,451 pooled trades, implied GROSS edge +0.0011/trade
(+0.11 bps).** Not "small edge lost to costs" — no edge. Confirmed per asset
class (crypto -0.008, equity +0.005, ETF -0.011, futures +0.016 per trade
before costs).

### Strategy inventory (49 registered)
28 v0 expanded + 7 lab v1 + 9 lab v2 + 5 lab v3. All fire on real data
(verified). v3 has **0 graveyard rows so far** — registered mid-run.

## 5. UNVERIFIED ASSUMPTIONS (do not treat as fact)

1. **Binance.US fee rates.** `cost_model.py` uses 0% maker / 0.02% taker per
   `references/broker-fee-reference-2026.md`. ccxt's public data still reports
   0.10%. Marked `verified: False` in `CostModel.describe()`.
   **NEEDS AYM: check the live account fee page.**
2. **Cost model IS wired (D-235)** but its RATES are still unverified
   (item 1), and equity half-spread is a single liquid-large-cap default.
3. **Old futures rows were fictional; new ones trade 1 MES on $1,800
   margin.** The archived flat-cost file still has the fictional rows.
4. **Options never swept.** The overlay exists but has NO IV smile, so
   long-option results are optimistic by construction. Real chains cost
   $99-600/mo.
5. **Slippage 0.05%/side is an assumption**, never measured live. Paper
   adapter now records realized-vs-assumed bps on every fill — the first
   supervised paper session answers it.
6. **Funding data is one year and an unusual regime** (sustained negative BTC
   perp funding in 2026). Any funding-conditioned result is a claim about one
   strange regime.
7. **Survivorship bias present.** Only MULN/SNDL are delisted names, kept as
   canaries. Universe is otherwise survivors-only.
8. **Constraint sweep preliminary result** (+0.094/trade at the CONSERVATIVE
   gate) is **noise**: 116 trades, 39% of profit from strategies firing 1-2
   times, 3 correlated mega-cap tickers. Needs ~4,000-8,700 trades to judge.
   Do NOT report it as a signal.

## 6. Files created/changed this session (by area)

**Cost & instruments (new, tested, NOT wired):**
`backtest/cost_model.py`, `backtest/instruments.py`,
`tests/test_cost_model.py`, `tests/test_instruments.py`, `config.yaml`
(`cost_profiles` block, documented-not-wired)

**Analysis tooling (new):** `backtest/pooled_analysis.py`,
`backtest/asset_class_analysis.py`, `backtest/conditional_edge.py`,
`backtest/constraint_sweep.py`, `backtest/assertions.py`,
`backtest/summarize_graveyard.py`, `backtest/inversion.py`,
`backtest/run_inversions.py`, `backtest/premium_filter_study.py`,
`backtest/balance_sweep.py`, `backtest/options_overlay.py`,
`backtest/cross_harness_check.py`

**Harness fixes:** `backtest/harness.py`, `backtest/vectorized_harness.py`
(regime timestamp alignment, entry order semantics, gap fills, dollar
buy-hold, percentile twin gate, time-matched twins, signal-exit configs,
gate versioning, id()-reuse cache bug)

**Data:** `backtest/data_loader.py` (pandas rewrite; 706 files were silently
unparseable), `backtest/download_missing.py`,
`backtest/download_strategy_data.py`, `backtest/check_data_integrity.py`

**Engine:** `engine/executor.py` (new), `engine/main.py` (new), `botctl.py`
(new), `engine/scanner.py`, `engine/risk.py`, `engine/adapters/paper.py`,
`engine/db.py`

**Sandbox:** `sandbox/validator.py`, `sandbox/_runner.py`

**Strategies:** `strategies/builtin/strategy_lab_v2.py` (9),
`strategies/builtin/strategy_lab_v3.py` (5), fixes to `expanded.py`,
`strategy_lab.py`, `indicators/patterns*.py`

**Indicators:** all now `ta`-library facades; originals moved to
`tests/reference_indicators.py` as the cross-check referee

**Agents:** `agents/{scout,forge,judge,coach,echo}/SOUL.md` + `agents/README.md`
(drafted, NOT active; 11 rules exceed SPEC 5.7 and need Raven's ruling —
especially the twin-methodology conflict with Quant's existing SOUL, now
resolved in code as a percentile test)

**Docs:** `SPEC.md` (+5.8 cross-sectional harness, +5.9 cost model),
`docs/ROADMAP.md`, `docs/DECISIONS.md` (D-101..D-234),
`docs/STRATEGY-GRAVEYARD-PACKAGE.md`, `docs/DATA-SOURCES.md`,
`docs/STRATEGY-COVERAGE-STATUS.md`, `docs/STRATEGY-LAB-V3-V4-ASSESSMENT.md`,
`research/2026-08-13-*.md` (5 findings docs), `references/strategy-lab-v{2,3,4}.md`,
`references/broker-fee-reference-2026.md`

## 7. Key findings a fresh session must not re-derive

1. **Gross edge across the library is +0.11 bps.** Capital, venue, and sizing
   move the toll; none of them move the 0.11.
2. **Position size is irrelevant to edge on percentage-fee instruments.**
   Verified: PF/win-rate/return% identical at $100 and $100,000. It matters
   ONLY for contract instruments (access) and fixed-cost regimes.
3. **Rare patterns cannot be judged per ticker.** 13 of 35 never reached 20
   trades in 212,058 runs. Pool by strategy, and by asset class.
4. **Filtering to winners buys exactly nothing.** Select on half the
   underlyings, verify on the other half: survival ~53-58% (coin flip), mean
   -0.302/trade on unseen instruments against a -0.30 floor.
5. **Inversion (SPEC 5.6) is refuted.** 48 gated candidates, zero beat
   buy-and-hold, edge = -$0.31/exit against a $0.30 cost. It is friction.
6. **Aggregates hide concentration.** Three separate instances caught. Always
   run leave-one-asset-out; it is automated now.
7. **Cost is the structural variable of the whole search space**, not a
   footnote. Every strategy fails by approximately the cost.

## 8. Owed by Aym

1. **Verify Binance.US fee schedule** on the live account (unblocks the cost
   model; one number).
2. **FRED API key** (free, 30s, `FRED_API_KEY=` in `.env`) → unlocks CPI dates
   for the macro strategies.
3. **First supervised paper run** + kill-switch drill:
   `python3 -m engine.main`, then `python3 botctl.py halt "drill"`.
4. **Raven review** of the CC DECISION entries, especially D-217 (agent SOUL
   rules that exceed SPEC 5.7).
5. Alpaca keys ARE in `.env` and verified working. The OLD key that was
   hardcoded in source should still be rotated at Alpaca if not already.

## 9. Standing rules (earned, do not relax)

1. No result is durable unless `validate_harness.py` exits 0.
2. Cite `distinct_findings` from `summary.json`, never raw pass counts.
3. Verify a strategy FIRES on real data before interpreting any result.
   (Four dead-on-arrival strategies were caught this way.)
4. Conditions must be predicted before testing, never discovered by scanning.
5. Estimate gross edge in bps before writing code.
6. Every proposal states a kill condition.
7. A FAIL on a 200k-trade strategy is a verdict; on a 1,700-trade strategy it
   is a shrug. Say which.
8. Never pool results across cost-model or gate versions.
