# Handoff — Cost model wired into both harnesses, graveyard restarted

**Date:** 2026-08-13, afternoon session (continuation of the 08-12/13 session)
**Decision record:** D-235 in docs/DECISIONS.md. Roadmap P0.2 now DONE.

## What was built

Executed the agreed next action from HANDOVER.md section 2, in order, with
Aym's confirmation before the destructive step.

**Modified:**
- `backtest/cost_model.py` — added `TradeCoster` (binds the four-regime model
  to one instrument; slip moves the fill, fees come off PnL) and `FlatCoster`
  (legacy flat model behind the same interface, version string encodes its
  rates). Added per-regime slippage constants (futures = 1 tick/side, not
  5bps — the flat model's worst single error).
- `backtest/instruments.py` — added `resolve_asset_class()` +
  `SECTOR_TO_CLASS`, now the single sector-to-class mapping for the whole
  project.
- `backtest/vectorized_harness.py` — coster wiring throughout run_strategy,
  twins, buy-hold. `use_cost_model: true` opts in; default stays bit-identical
  flat. Futures size whole contracts (ES->MES micro), margin is
  capital-at-risk, PnL in contract dollars, return_pct = return on capital at
  risk (unchanged for spot). Every report stamped
  cost_model_version/asset_class/instrument. Twin cache keys on full cost
  identity.
- `backtest/harness.py` — same wiring in the event harness (referee parity).
- `backtest/assertions.py` — new `cost_model_version_uniform` silent
  assertion: mixed stamps = dataset cannot be pooled.
- `backtest/run_incremental_graveyard.py` — forces use_cost_model, passes
  sector, stamps NOT_TESTED rows.
- `backtest/constraint_sweep.py` — same; DEFAULT_TICKERS now carry sector
  tags; output JSON stamped.
- `backtest/asset_class_analysis.py` — imports the shared resolver, prefers
  the stamped asset_class.
- `config.yaml` — `use_cost_model` documented (false by default), cost_profiles
  STATUS note updated to WIRED.
- `tests/test_backtest.py` — 6 twin-test call sites moved to the coster
  interface (flat rates preserved exactly).
- `tests/test_cost_model.py` — 4 new wiring regressions: modeled equity fees
  < flat/3 on identical trades; futures = 1 contract, CAR $1,800, PnL =
  move x 5; overrides force flat zero-cost; flat mode stamps `flat:`.

## Validation (all green before the restart)

- 401 tests pass (was 355; 42 in test_backtest re-run after signature fix)
- `validate_harness.py`: 21/21, exit 0
- Cross-harness referee: AGREE on AAPL and BTC_USD (flat path bit-identical)

## Destructive actions (Aym confirmed both via prompt)

- Killed the 46%-done graveyard sweep and the 50%-done constraint sweep
  (both were charging flat 30bps to everything).
- Archived: `research/graveyard/archive/v0_graveyard_flatcost_partial_2026-08-13.json`
  (+ constraint sweep JSON and both logs, same `_flatcost_` naming).
- Restarted both under the cost model: 477,015 combos, 49 strategies (v3
  included this time). Logs: `logs/graveyard_costmodel.log`,
  `logs/constraint_sweep_costmodel.log`.

## Skipped / deferred

- Pooling scripts (pooled_analysis.py etc.) do not themselves refuse mixed
  versions; enforcement lives in assertions.py which must be run on any
  graveyard before citing it. Could be hardened later.
- Options overlay not touched (still no IV smile; not swept).
- Equity half-spread is one number (1.5bps/leg) for all names; a per-
  instrument spread table is future work.
- GATE_VERSION not bumped: flat-mode results are bit-identical, and the
  cost-model era is separated by cost_model_version, which the new assertion
  enforces.

## Questionable / watch

- P0.3 pre-registered prediction stands: v0 library at corrected costs should
  land ~-$0.14/trade. If materially above, the gross-zero conclusion was
  cost-model-dependent — re-examine.
- Binance.US rates still UNVERIFIED (Aym checklist item 1). When verified,
  bump COST_MODEL_VERSION and the assertion will fence old results
  automatically.
- Futures margins in instruments.py are approximate and volatility-dependent;
  re-check before any live use.

## Next steps for Raven

- Review D-235 (this work) alongside the still-pending D-217 SOUL review.
- The restarted graveyard finishes in roughly the same wall time as before
  (~hours). Then: P0.3 control comparison, then the asset-class re-analysis
  cites the NEW file only — the archived flat-cost partial must never be
  pooled with it (the assertion will catch it, but don't try).

---

# Addendum — same day, second block: FRED unblocked, v4 ignitions, queued pass

## Built
- `FRED_API_KEY` in `.env`; macro calendar regenerated: **948 CPI release
  dates** (1949 to 2026-08-12) now in `backtest/data/aux/macro_calendar.json`.
- `MacroDrift(event='CPI')` = `V3_macro_drift_cpi` registered (fires 5x on
  SPY test slices - monthly event, narrow window, rare is correct).
  **Deliberately NOT added to MACRO_DATES** (IntradayMomentum's amplifier)
  mid-run - that would silently change an already-being-swept strategy.
  Fold in at the next full rebuild; comment in strategy_lab_v3.py says so.
- `strategies/builtin/strategy_lab_v4.py` - the three DEEP RENT ignitions as
  SHARE strategies per the assessment doc's recommendation:
  - `V4_gap_hold_proxy` (daily) - PEAD *price-only proxy*, no earnings data;
    proxy status is in the NAME so reports can't drop it. 7 signals across
    first-60-ticker slices.
  - `V4_52w_high_breakout` (weekly, faithful George & Hwang trigger).
    36 signals / 29 tickers.
  - `V4_trend_reclaim` (weekly translation of 100-day MA reclaim + 12-1
    momentum). 17 signals / 16 tickers.
  Weekly-bars choice is deliberate: a 52-week lookback fits inside the
  graveyard's 157-bar weekly test slices but NOT its ~250-bar daily slices.
- `tests/test_strategy_lab_v4.py` (17 tests) + roster/loader fixes in
  test_strategy_lab_v3.py. 386 passing in the fast suite.
- v4 registered in run_incremental_graveyard.py; **queued watcher** launches
  the incremental pass (only the 4 new strategies' combos) automatically when
  the current sweep exits → `logs/graveyard_v4_cpi_pass.log`.

## Skipped / deferred
- v3 #3 / #5 and v5 P1/P4 still blocked on the cross-sectional harness
  (SPEC 5.8) - next major build.
- v4 LEAPS wrapper untouched (Stage 1 shares-first, per assessment).
- Earnings calendar still missing (upgrades the PEAD proxy to real PEAD).

## Open with Aym
- His fee-structure upload did not arrive in the session uploads dir - only
  the HANDOVER came through. Needs re-upload/paste before touching
  cost-model rates (any change bumps COST_MODEL_VERSION).

---

# Addendum 2 — v5 doc landed, fee doc confirmed, P4 killed by precheck

- Aym uploaded `strategy-lab-v5.md` (now saved to references/ - the ROADMAP
  had only its summary) and the broker fee reference, which is byte-identical
  to the repo copy already powering cost model 2026-08-13. No rate changes.
- Aym's ruling: venue fee verification is a SHADOW-TEST gate, not a backtest
  blocker. ROADMAP P0.1 demoted (D-236).
- **P4 Fingerprint Router is DEAD (D-237).** Built its own pre-registered
  precheck (`backtest/vr_fingerprint.py`): cross-half Spearman of instrument
  VRs = -0.21 / +0.07 vs the 0.3 bar, 175 instruments. Fingerprint is
  weather. No routed backtest run, per the proposal's own no-trickery
  clause. 6 math-pin tests added.
- v5 remaining: P3 Dispersion Gate next (roadmap's own "P3 first"), P2 needs
  the maker-fill simulator, P1 needs the cross-sectional harness, P5 needs
  nothing new but funding-regime caveat applies.

---

# Addendum 3 — evening parallel build (four subagents), consolidation

Four subagents, disjoint file ownership, all lanes verified by the main
session (tests re-run, artifacts inspected): D-238..D-242 in DECISIONS.md.
Per-lane handoffs: 2026-08-13-cross-sectional-harness.md, -toll-collector.md,
-dispersion-gate.md, -forced-flow.md. Agent-runtime proposal:
docs/AGENT-RUNTIME-PROPOSAL.md (three decisions pending AYM).

Test count 355 -> 517 over the day. SPEC 5.8 status flipped to BUILT.
Experiment chain queued behind the graveyard: incremental pass -> dispersion
gate -> horizon ladder -> PLR.

SPEC items that structurally require Aym (cannot be built by code):
1. Supervised paper session + kill-switch drill (SPEC 10 / ROADMAP P4.1)
2. Notion + Telegram tokens (config.yaml blank; Echo's output channels)
3. Anything live-mode (gated by design)
4. Decisions: D-217 ratification path, Hermes/Raven carve-out, audit cadence
   (AGENT-RUNTIME-PROPOSAL.md), and the paid-data calls (options chains,
   survivorship-complete equities, earnings calendar)
