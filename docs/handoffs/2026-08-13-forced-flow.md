# Handoff: Lab v5 P5 "FORCED-FLOW HARVEST" — 2026-08-13

Built by Claude Code from `references/strategy-lab-v5.md`, proposal P5 only.

## What was built

- `strategies/builtin/strategy_lab_v5.py` (new) — two strategies, deliberately
  SEPARATE graveyard cohorts because P5's third kill condition (mechanism
  coherence: the funding-stress leg and the volume-climax leg disagreeing on
  sign) is only measurable if the legs never pool:
  - `V5_forced_flow_crypto` — prior-UTC-date funding stress (pooled BTC/ETH/SOL
    daily mean at or below the 25th percentile of its own trailing history,
    min 30 trailing dates, lookahead-free) + a 3-5 bar liquidation cascade
    (down candles, monotone lower closes, net range expansion first-to-last)
    on volume z > 3, cascade low holding above the 45-bar prior swing low.
    Long at cascade close, stop strictly below the event low, 2R target.
    Crypto gating is STRUCTURAL: bar spacing 1h..1d + 24/7 tape (weekend bars
    present) + funding date match.
  - `V5_capitulation_equity` — daily bars, weekday-only tape, volume z > 4,
    close in the bottom decile of the day's range, gap-down open (>= 0.5%).
    Long at close, stop strictly below the day low, 2R target.
  - Funding table loaded ONCE at import (v3 macro-calendar pattern), degrades
    to empty on any failure -> crypto strategy emits nothing rather than
    crashing a sweep. Never-raise wrapper identical to v2/v3/v4.
- `tests/test_strategy_lab_v5.py` (new) — 21 tests: firing fixtures, one
  rejection test per condition (each violated condition alone kills the
  signal), cohort-disjointness both directions (equity tape cannot enter the
  crypto cohort and vice versa), lookahead-safety of the funding date,
  degrade-to-empty, garbage-never-raises, roster, min_bars <= SCAN_WINDOW.
  All pass; `tests/test_strategy_lab_v4.py` re-run alongside, all pass
  (38 total).
- `backtest/run_incremental_graveyard.py` — ONE registration edit (import +
  ALL_STRATEGIES + strategies-count log line), mirroring how v4 was added.
  Registry now 55 strategies, names verified unique. NO graveyard run was
  launched; the queued incremental pass picks the new combos up.

## Fires-check (standing rule 3, BEFORE registration)

Raw `scan_all_bars` counts (confirmation stack off) over the graveyard's
last-20% test slices:

| Data | Slice | Signals |
|---|---|---|
| BTC_USD_1h | 3,497 bars, 2026-03-20 -> 2026-08-12 | 2 |
| ETH_USD_1h | 3,496 bars, same window | 1 |
| SOL_USD_1h | 3,497 bars, same window | 2 |
| BTC/ETH/SOL_USD_1d | 147 bars each | 0 |
| Binance merged 1h (3 pairs) | 1,752 bars, 2025-07-20 -> 2025-09-30 | 0 |
| Equity `*_1d` (178 tickers) | ~100-150 evaluable bars each | 3 (AAPL, NVDA, SOFI) |

Both strategies fire on real data -> both registered.

Why the zeros are structural, not bugs: the 1d crypto test slices leave only
47 evaluable windows each (harness scans from bar 100 of a 147-bar slice);
the Binance merged 1h test slices END 2025-09-30, and the funding stress
table only starts being usable 2025-09-12 (30-day trailing burn-in on a
table that begins 2025-08-13) — near-zero overlap. Most 500-bar equity 1d
files have ~0 evaluable bars for the same scan-from-bar-100 reason; the
equity signal supply comes from the ~25 long (1,253-bar) files.

## Skipped / deferred

- P5's earnings-window exclusion for the equity leg: NO earnings calendar
  exists in this project. See "Questionable" — this is the loudest caveat.
- P1-P4 of the v5 doc: out of scope for this session (P2 needs the maker-fill
  simulator, P1/P3/P4 need the cross-sectional/holdout tooling).
- Per-symbol funding tables: scan() has no ticker identity, so the funding
  stress is a pooled BTC/ETH/SOL daily mean. Documented in the module.

## Questionable / caveats (read before interpreting ANY v5 result)

1. **FUNDING-REGIME CAVEAT (HANDOVER unverified assumption 6):** the funding
   data is ONE YEAR (2025-08-13 to 2026-08-13) of an UNUSUAL regime —
   sustained negative BTC perp funding in 2026. Any funding-conditioned
   result from `V5_forced_flow_crypto` is a claim about one strange regime,
   not about crypto funding generally. Do not generalize a PASS or FAIL.
2. **Earnings contamination is UNCONTROLLED** in `V5_capitulation_equity`:
   P5 requires "no earnings within the window"; no earnings calendar exists,
   so some capitulation days are earnings collapses (informed selling). All
   equity-leg results are PROVISIONAL until a calendar exists. The omission
   is stamped into every signal's feature dict.
3. **The tasking's gate rationale was wrong and was corrected:** it assumed
   equity series would never match funding dates. Checked: equity 1d files
   run through 2026-08 and their test slices sit ENTIRELY inside the funding
   year. The crypto gate therefore rests on the 24/7-tape check (weekend
   bars), which equities can never pass; the funding-date match alone would
   not have excluded them.
4. **Severely underpowered at these frequencies:** ~5 crypto + ~3 equity raw
   signals in the test slices is nowhere near P5's own 400-800 trade bar
   (P5 forecast 2-6k events across 180 instruments; the reality of this
   dataset's evaluable windows is orders of magnitude thinner). Whatever the
   graveyard says about these two cohorts, per standing rule 7 it is a
   shrug, not a verdict, until the event pool grows (longer slices, more
   instruments, or intraday crypto data inside the funding year).
5. **Definition choices tuned on FIRE-FREQUENCY only, never P&L** (standing
   rule 4): "expanding" = net range expansion first-to-last (strict per-bar
   monotone expansion killed ~60% of otherwise-qualifying cascades on 1h
   data); stress percentile 0.25; gap minimum 0.5%; baselines 45/60 bars.
   All fixed before any backtest P&L was read; the diagnostics scripts read
   candle counts only.
6. Exit mismatch: P5 holds 2-5 days; nearest harness exit config is
   `time_8c` (documented in the module). Verdicts from trailing/fixed-R
   configs measure a different trade.

## Next steps (for Raven)

1. When the queued incremental graveyard pass completes, read the two v5
   cohorts SEPARATELY (mechanism-coherence kill condition) at `time_8c`
   first, with the leave-one-underlying-out flag.
2. Earnings calendar acquisition unblocks the equity leg's exclusion AND
   v4's I1 proxy upgrade — same data purchase serves both.
3. If the crypto leg shows anything, the immediate question is regime
   dependence: one year, one regime. More funding history (or a second
   stressed regime) is the only cure.
4. Consider extending crypto 1h coverage inside the funding year (Binance
   merged data ends 2025-09-30) — that is where this cohort's power lives.
