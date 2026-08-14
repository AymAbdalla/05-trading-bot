# Handoff: Lab v5 P3 "DISPERSION GATE" built (2026-08-13)

Built by Claude Code. For Raven review. Roadmap slot: P2, "P3 first."
Requirements: `references/strategy-lab-v5.md` SS2 (Toll Law) + SS3 P3.

## What was built

- `backtest/dispersion_gate.py` (new) - the derived entry gate experiment.
  - Gate: entry allowed only when ATR_hold >= c / kappa, kappa = 0.10
    (pre-registered in the v5 doc). ATR_hold = ATR14[signal bar] x
    sqrt(bars_in_hold) / close, c = venue-accurate round-trip cost for THIS
    instrument from `backtest/cost_model.py` (same coster the harness
    charges). Threshold is DERIVED from the fee schedule, never scanned.
  - Entries: `grid_2.0atr` and `stoch_rsi_oversold` (exact name matches in
    `strategies/builtin/expanded.py`) plus `dca_7` as the control (P3 says
    "dca"; registry has dca_7/dca_14, dca_7 chosen - higher frequency).
  - Exits: time-based only (`time_4c`/`time_8c`/`time_16c`) so
    bars_in_hold is exact - the simpler option the work order allowed;
    R-based exits excluded, no median-hold estimation needed.
  - Universe: all 1d + 1h series in `backtest/data/`, discovered exactly
    like `run_incremental_graveyard.py` (its SKIP_FILES, Binance 1h merged
    pairs, sector tags from `backtest/ticker_universe.json`).
  - Both arms (gated/ungated) run with the confirmation stack OFF - the
    gate is the only variable. `use_cost_model: true` throughout.
  - Vol-decile analysis (P3 pre-registration): every ungated spot trade
    bucketed by entry-time ATR14/close percentile within that series' own
    expanding history (>= 50 observations, pre-entry data only).
  - Time-based holdout (v5 work order 3): calendar-midpoint split per
    series, trades assigned by entry timestamp; H1/H2/pooled all reported,
    judgment on H2. Threshold is derived, so both halves are honest.
  - Leave-one-asset-out, same underlying-grouping as
    `asset_class_analysis.py`.
  - Fires-check (candidates, gated-out %, surviving trades per class)
    printed and stored in the JSON before any P&L field.
  - Output: `research/graveyard/dispersion_gate.json`, stamped with
    cost_model_version, gate_version, kappa, per-class c, both kill
    conditions, and the pre-registered predictions. Asserts a single
    cost_model_version across the run (standing rule 8).
- `tests/test_dispersion_gate.py` (new) - 20 tests, all synthetic/fast:
  per-class gate arithmetic with hand-computed numbers, >= boundary,
  sqrt-of-hold scaling, no-lookahead (future-mutation) tests for gate and
  deciles, decile bucketing hand-checks, holdout determinism, underlying
  grouping, pre-registered constants pinned.
- `research/graveyard/dispersion_gate_smoke.json` - smoke-run artifact
  (separate path; the pre-registered path is reserved for the full run).

## The per-class refinement (documented prominently, per work order)

The v5 doc derived the gate from a flat c = 14bps -> "per-hold ATR >= 1.4%".
Since D-235 the toll is per asset class, so the derived gate is SHARPER.
Thresholds actually used (c / kappa, from the smoke run's stamps):

| Class | c (bps of exposure) | ATR_hold threshold |
|---|---|---|
| CRYPTO (non-core pairs) | 14.0 | 1.400% |
| CRYPTO (core BTC/ETH pairs) | 12.0 | 1.200% |
| EQUITY | 4.21 | 0.421% |
| ETF | 4.21 | 0.421% |
| FUTURES (1 micro contract) | 1.42-1.50 | 0.142-0.150% |

Only non-core crypto matches the doc's 1.4%. Equity/ETF gates are ~3.3x
sharper. Each series gets its own instrument's c, byte-identical to what
the harness charges it (the coster is shared).

## Verification

- `python3 -m pytest tests/ -q` -> 467 passed, 1 skipped (includes the 20
  new tests; other agents' tests were in the tree and also pass).
- Smoke run (`--smoke`, 8 tickers x {1d,1h} = 16 series spanning
  EQUITY/ETF/CRYPTO/FUTURES) executed end-to-end: fires-check populated,
  gated-out % nonzero and class-ordered (crypto gates hardest, as the
  thresholds predict), holdout halves populated, deciles populated, LOO
  populated, JSON written. Per the work order, no results interpretation
  from the smoke run - 8 correlated tickers are far under the diversity
  the pre-registration assumes.
- Smoke fires-check flavor (time_8c): grid_2.0atr crypto 22.4% gated out,
  equity 0.0%, ETF 1.8%; stoch_rsi crypto 27.4% out; at time_4c crypto
  gates 52-61% out. The gate binds hardest exactly where c is highest -
  mechanics behave as derived.

## Fixed during the session (worth Raven's eyes)

- First smoke run pooled FUTURES contract-dollar PnL (1 MES ~ $38k
  exposure) with $100 spot clips; leave-one-out instantly flagged ES as a
  one-asset costume on every strategy - a denominator artifact, exactly
  the mismatch ROADMAP P0.4 forbids. Fixed: cross-class dollar pooling
  (pooled tables, deciles, LOO, power count) is now SPOT-only
  (CRYPTO/EQUITY/ETF); futures still run gated/ungated and appear in
  fires-check and per-class tables.

## Skipped / deferred

- The trend-state x vol-decile interaction P3 mentions ("sharpens the
  middle-decile trough") - single-variable test kept; documented skip.
- R-based exit configs (would need median realized hold from an ungated
  run; time exits chosen instead).
- 15m/5m/1wk series - the work order scopes 1d + 1h.
- No incremental save: the script is single-pass. If the full run is
  killed, rerun it (est. 1-3h; far cheaper than the graveyard sweep).

## Kill conditions on record (stated before any full-run result)

1. (P3 verbatim) Monotone-flat edge across vol deciles pooled =>
   dispersion conditioning is dead and SS4's 116 trades were the hammer's
   $1.48 in a costume.
2. (SS2 falsifiability) Gated per-trade net on the H2 holdout not better
   than ungated for the non-control entries => the derived gate selects
   nothing; Toll-Law gate at kappa = 0.10 dead on this universe.

Power bar: 4,000-8,700 pooled trades for +/-$0.09 edges. The report prints
VERDICT-CAPABLE or SHRUG; the smoke run already clears the count but not
the diversity - only the full run's label counts.

## Next step: queue the full run AFTER the graveyard finishes

Two CPU-heavy jobs were running (graveyard + constraint sweep), so the full
180-ticker sweep was NOT run. Exact command to queue:

```bash
cd /Users/aympulse/aym/projects/05-trading-bot
nohup nice -n 10 python3 backtest/dispersion_gate.py > logs/dispersion_gate.log 2>&1 &
```

Writes `research/graveyard/dispersion_gate.json`. Then judge against the
kill conditions above - on H2, deciles first.
