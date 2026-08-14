# Handoff: Cross-Sectional Harness (SPEC 5.8) — 2026-08-13

Built by Claude Code. SPEC 5.8 was "the single largest missing capability";
this session builds it, plus Lab v5 P1 (Horizon Ladder) and Lab v3 #3/#5 on
top of it. No other files were touched (two other agents working in
parallel; graveyard + constraint sweep still running, so NO full-universe
runs were launched — smoke only).

## What was built

**`backtest/cross_sectional.py`** (new, ~1,000 lines)
- `Panel`: N tradable instruments + named CONTEXT instruments (readable,
  never tradable — VIX, sector ETFs) aligned on the UNION of bar keys.
  Forward-fill nothing; a missing bar stays missing. `date_align=True`
  floors keys to the UTC date so equity daily bars (04:00 UTC stamps) and
  crypto daily bars (00:00 UTC) share one daily cross-section; true bar
  timestamps are preserved for fee/fill bookkeeping.
- `PanelView`: what a ranker sees at a step — bars STRICTLY BEFORE the step
  key. The decision/trade bar is structurally absent, not merely off-limits.
- `CrossSectionalHarness.run()`: steps the grid, ranks, selects
  (top/bottom decile/quintile/K with min_scored / min_names / max_names
  clamps), opens the slice with vectorized-harness fill semantics (entry at
  close-or-open of decision bar + slippage, gap-aware stop fills at
  min(stop, open), market exits pay slippage, both legs pay fees via
  `CostModel().coster(...)` + `resolve_asset_class`), carries held names
  (never doubled, never phantom-filled), and reports pooled + per-cell +
  leave-one-asset-out + calendar-half time split + time-matched twins.
- Twins: replay the strategy's OWN formation steps with identical breadth,
  randomizing only name choice — time-matched by construction (the
  cross-sectional analog of `_time_bucket_key`/`_run_random_twin`).
- Stamps on every result: `cost_model_version` (+ uniformity flag; 'MIXED'
  poison value if it ever mixes), `survivorship: 'survivors-only universe'`,
  `cs_gate_version`, asset classes, fires-check emitted BEFORE P&L.
- Rankers: `make_rev_ranker`, `make_mom_ranker` (P1),
  `make_same_clock_echo_ranker` (v3 #3), 
  `make_paid_liquidity_reversal_ranker` (v3 #5), plus
  `aggregate_15m_to_30m` (no *_30m data exists; built from *_15m — #3 is
  feasible, ~2 years of 15m across 178 names).
- CLI: `python3 backtest/cross_sectional.py --strategy {echo,plr} [--smoke]`
  writes to `research/cross_sectional/`.

**`backtest/run_horizon_ladder.py`** (new)
- Lab v5 P1: REV (5d return, bottom decile → long) and MOM (60d return, top
  decile, above 100d MA → long) × holds {1,3,5,10,20}, non-overlapping
  formations (rebalance_every = hold), pure hold-N exits (no stop — a stop
  would resample the horizon the proposal tests).
- The P1 pre-registration (predictions + kill condition) is quoted VERBATIM
  in the module docstring, placed before any results existed.

**`tests/test_cross_sectional.py`** (new): 29 tests, all passing in <1s.

## Design decisions (and why)

1. **One bar more conservative than the time-series harness on lookahead.**
   `scan()` in the existing harnesses sees the signal bar it fills on. Here
   the ranker CANNOT see the decision bar at all (rank on data ≤ t−1, fill
   at bar t close/open). Costs one bar of signal freshness; buys a
   structural guarantee instead of a convention, which is what the first
   graveyard's death was worth. The oracle test enforces it: a cheating
   ranker in entry-at-open/exit-at-close mode would earn +20%/trade if the
   bar leaked; it loses ~-8%/trade, and its trades are bit-identical to an
   honest one-bar-lagged ranker.
2. **use_cost_model defaults TRUE** (vectorized defaults false). This
   harness has no flat-era referee it must stay bit-identical with. Flat
   mode still available and distinctly stamped.
3. **Rebalance cadence is in grid steps; holds are in each instrument's own
   bars.** With crypto in a daily universe the grid includes weekends, so
   `rebalance_every=5` is ~5 calendar days while an equity 5-bar hold is 5
   trading days. A formation only "consumes" the schedule slot if it clears
   min_scored, so weekend crypto-only steps don't eat the schedule of a
   170-name universe.
4. **Selected-but-missing names are skipped and counted**
   (`fires_check.names_skipped_missing_bar`), re-evaluated fresh at the next
   rebalance. No forward-fill, no deferred phantom fills.
5. **Ranker exceptions degrade to no-signal** and are counted
   (`ranker_errors`) — rank code never raises, per standing rules.

## Deviations from SPEC / source docs (all deliberate, all visible)

- **P1 "all 180 tickers"** → all `backtest/data/*_1d.csv` EXCEPT `VIX`
  (untradable index; context series elsewhere) and the 8 `*_F` futures
  (TradeCoster sizes them in whole contracts on margin; one MES trade's
  contract dollars would outvote ~50 $100 spot clips in a pooled
  pnl-per-trade, the exact pooling instruments.py condemns). Effective
  universe: 165 names.
- **v3 #5 news exclusion SKIPPED — prominent.** No earnings calendar exists
  in this environment. The source doc calls this filter "the single most
  important nuance"; without it, this implementation buys informed selling
  alongside liquidity selling, so the doc itself predicts a DEPRESSED edge.
  A failure does not refute Nagel's conditional claim. Stamped in run params
  as `news_exclusion: OMITTED_NO_EARNINGS_CALENDAR`.
- **v3 #5 residual-mean-revert exit (>−0.25σ) not implemented** — exits are
  5-day time or −2 ATR stop only; stamped
  `residual_reversion_exit: OMITTED_TIME_AND_STOP_ONLY`.
- **v3 #3 is FEASIBLE from 15m data** (aggregated to 30m; ~2 years). DST is
  handled via zoneinfo — 9:30 NY resolves to 13:30 UTC in summer and 14:30
  UTC in winter, verified on AAPL (332 + 169 slot bars).
- **No buy-and-hold benchmark in the report** — cross-sectional long-slice
  vs flat-rest removes drift by construction (SPEC 5.8's own rationale); the
  drift-free comparator is the time-matched twin distribution.

## Test results

`python3 -m pytest tests/test_cross_sectional.py -q` → **29 passed** (~0.2s).
Coverage: grid=union / nothing forward-filled / missing bars never traded;
date-align semantics; structural lookahead denial + cheating-ranker oracle +
cheat==lag equivalence; decile/quintile/k selection incl. thin-cross-section
gates; venue + flat cost stamping; survivorship + gate stamps; time-exit
duration; gap-aware stop fills; no entry on unholdable final bar; held names
never doubled; leave-one-out shape + concentration detection; per-cell +
pooled + time-split shape; raising ranker degrades; context readable but
untradable; twins time-matched (twin entries ⊆ strategy formation steps);
15m→30m aggregation (gaps stay gaps).

Full suite NOT re-run this session (two CPU-heavy background jobs); no
existing file was modified, so prior 401-test state is untouched. Rule 1
still applies: nothing here is durable until `validate_harness.py` exits 0
alongside these tests.

## Smoke runs (10 mega-caps, reduced twins — machinery proof, NOT results)

- **Horizon ladder** (`--smoke`, venue costs): executes end-to-end, 10
  cells, fires-check printed before P&L. Net/trade rises with hold in both
  families (REV h1 $0.10 → h20 $1.39; MOM h1 $0.15 → h20 $3.81) but n is
  tiny (25–810/cell vs the 4,000+ bar), the panel is 10 correlated mega-caps
  in a bull window, and twin percentiles are unremarkable (0.05–0.9). This
  is beta + noise until the full run; do not cite it.
  Saved: `research/cross_sectional/horizon_ladder_smoke.json`.
- **PLR** (`--smoke`): 46 VIX-gated formations, 84 trades, all in the second
  calendar half (VIX regime + 252d warmup) — the gate demonstrably works.
  loo flags AAPL concentration on this tiny panel, as it should.
- **Echo** (`--smoke`): 982 slot formations (~2/day), 982 trades, net
  −$0.065/trade, twin percentile 0.05 — 30-minute holds paying the toll,
  exactly the fee-reality risk the genome names. Full core-list run needed
  before any verdict.

## Queued full-run commands (DO NOT run until graveyard + constraint sweep finish)

```bash
# P1 Horizon Ladder, full universe (~165 names), both cost models:
python3 backtest/run_horizon_ladder.py --cost-model both \
    --out research/cross_sectional/horizon_ladder_full.json

# Lab v3 #5 Paid Liquidity Reversal, full stock universe (~107 names):
python3 backtest/cross_sectional.py --strategy plr \
    --out research/cross_sectional/plr_full.json

# Lab v3 #3 Same-Clock Echo on a wider core list (pick liquid names), e.g.:
python3 backtest/cross_sectional.py --strategy echo \
    --tickers AAPL,MSFT,NVDA,GOOGL,META,AMZN,TSLA,AMD,AVGO,JPM,BAC,GS,XOM,CVX,UNH,LLY,V,MA,COST,WMT,NFLX,CRM,ORCL,INTC,MU,QCOM,DIS,BA,CAT,GE,SPY,QQQ,SMH,XLK,XLF,XLE \
    --out research/cross_sectional/echo_full.json
```
Estimated cost: the H=1 ladder cells are the heavy ones (~1,200 formations
× ~16 names × 100 twins); expect minutes per cell, not hours. Echo on ~35
names is light. Read every result against its docstring's kill condition
and the P1 pre-registration; judge on the pooled count per standing rule 7.

## Open questions for Raven

1. Futures in the cross-section: worth adding a return-on-capital pooling
   mode so `*_F` names can join without dollar-scale distortion?
2. Echo full universe: genome says "core list" — which list is canonical?
   (I used a hand-picked liquid set in the queued command above.)
3. PLR without news exclusion: acceptable to run and interpret with the
   documented depression, or should an earnings-calendar source be acquired
   first (FMP/AlphaVantage free tiers exist)?
4. Should `validate_harness.py` grow cross-sectional checks (delayed-oracle
   analog through THIS harness)? The oracle currently lives only in
   tests/test_cross_sectional.py.
