# Claude Code Fix Session: Audit Remediation

**Date:** 2026-08-12
**Built by:** Claude Code (fixes for the findings in `2026-08-12-claude-code-audit.md`)
**Process:** Aym-approved sequence: Claude Code fixes -> Claude Code re-audits -> Raven reviews against SPEC.

## What was fixed (by area)

### Security / process
- Removed hardcoded Alpaca API key/secret from `download_alpaca.py`; now loads from `.env` and exits if missing. **AYM MUST STILL ROTATE THE KEY at Alpaca** (it lived in source).
- Killed the still-running graveyard build (its results were void).
- All graveyard JSONs flagged `PROVISIONAL: true` + `research/graveyard/README.md` forbids agent consumption until re-run.

### Validation suite (rebuilt first, so it certifies everything after)
- `controls.py` rewritten: OracleControl (sees next close via a control-only `future_closes` channel the harness only provides to `is_control` strategies), BuyHoldControl (single-use instance), CoinFlipControl (own seeded RNG). All emit `direction='bullish'` and non-falsy stops so they actually trade.
- `validate_harness.py` rewritten: every control now runs THROUGH `VectorizedBacktestHarness.run_strategy`. A1 oracle at zero cost must show extreme PF; A3 re-runs the oracle with `execution_delay=1` and requires collapse (a true lookahead detector: if delaying a future-seeing strategy doesn't hurt it, fills leak); A2 asserts fees strictly reduce PF per seed and that a hold-to-end trade reproduces the harness's own buy-and-hold PnL to the cent; A4 survivorship unchanged. Failures set `all_pass=False`; JSON uses native booleans, inf/nan become null.
- **Result: 21/21 checks pass against the fixed harness.**

### Both backtest harnesses
- Entry semantics now honor `signal.entry` + `valid_for` per SPEC 5.1: market at close, buy-stop resting orders (hammer), buy-limit resting orders; unfilled pending orders expire without a trade.
- Strategy stops honored; the vectorized harness no longer replaces stops with 0.25*ATR (fallback only for missing/invalid stops).
- Regime filter: event harness aligns 1h regime candles BY TIMESTAMP (`_regime_closed_counts`), only fully closed candles count. Walk-forward and build_graveyard pass full series safely now.
- Gap-aware exits: stop fills at min(stop, bar open); target at max(target, open). Stop/time/end exits pay slippage; resting-limit targets don't.
- `beats_buy_hold` is now a same-notional DOLLAR comparison incl. fees (`buy_hold_pnl_usd`), not avg-per-trade-% vs full-period-%.
- `gross_pf` (pre-fee PF) added to both result types and reports (F2 prerequisite for inversion work).
- Max drawdown computed on account equity (paper starting equity + cumulative PnL); losing streaks report real drawdowns.
- Infinite PF now FAILS gates everywhere (suspicious, never auto-pass); reports show PF null=infinite.
- Random twin: median PF over 10 seeded draws (own RNG), same slippage convention, timeout exits at the last scanned bar.
- Execution-delay probe fixed: exits can no longer trigger on pre-entry price action (entry_idx guard).
- `exit_ts` is the exit candle's timestamp, not wall clock.
- Support-level buckets serve bucket k-1 (no 19-bar lookahead channel).
- `build_graveyard.py` writes `v0_graveyard_event.json` (no longer collides with the vectorized `v0_graveyard.json`).
- Strategies whose `min_bars` exceeds the scan window get verdict `NOT_TESTED`, never tested-and-failed.

### Strategies
- FairValueGap: empty `range(-3,-12)` fixed (never fired before).
- Both tasuki gaps: swapped comparisons fixed; verified firing on textbook examples and rejecting near-misses.
- Piercing line: opens below prior LOW per SPEC (was prior close), both pattern files.
- on_neck/in_neck removed from the bullish entry registry (canonical bearish continuation patterns).
- DCA: cadence keyed on timestamps, not window length (was silent after bar 260).
- C2: declares `min_bars=840` (reported NOT_TESTED under the 260-bar window).
- D1 midday window + D2 day grouping now use US/Eastern (`_dt_et`), not UTC.
- D2 docstring documents that its short side is not simulated (longs-only harness).

### Data
- `data_loader.py`: parses yfinance-format CSVs (Date-string column). **706 of 973 files were silently unparseable before - the graveyard never actually tested them.** Now 936 clean files.
- Loader sorts, dedups, and warns on >40% close-to-open gaps (split detector). `check_data_integrity.py` scans the whole directory, writes `INTEGRITY_REPORT.md`, `--quarantine` moves bad files.
- Convention: ALL data adjusted. `download_alpaca.py` now requests `adjustment='all'` (raw Alpaca data was the source of the fake split crashes).
- Re-downloaded 21 corrupted daily/weekly files adjusted; rebuilt 5 leveraged-ETF weekly files from clean daily (Yahoo weekly bars are inconsistent around splits); quarantined SOXS (broken at source), NG_F (futures roll gaps, needs back-adjusted contracts), MULN (delisted from Yahoo; needs Sharadar/Norgate or Alpaca).
- Intraday sector-ETF/MULN/NVAX files remain quarantined until re-downloaded via the fixed Alpaca script (after key rotation).

### Engine (paper path)
- B1: scanner trims the forming candle (signal + regime series); only closed candles are scanned, matching backtest semantics.
- B2: 4-loss pause now expires after 24h (keyed on the last loss's closed_ts).
- B3: `PaperAdapter.check_exits()` simulates exchange-side stops/targets against the live bid (fills at bid, honest about gaps); `get_equity(collector)` marks open positions; `write_equity_snapshot()` feeds the ops backstops.
- B4: regime slope must be `> 0` (float-truthiness bug; negative slopes passed).
- Doji `block_entries` enforced in the scanner (skips entry scanning that candle).
- Scanner logs ALL signals `acted=0`; the executor must call `db.mark_signal_acted` after the risk gate approves (helpers added). Queue items now carry signal_id.
- Trades/day counts buy-side entries only, excluding rejected/errored; risk gate rejects stop >= entry; fee-to-edge uses signed edge.
- `positions.signal_id` persisted.

## Not done / deferred (needs Raven or Aym)
- **Executor (T9) still does not exist.** `check_exits`, `write_equity_snapshot`, `mark_signal_acted` have no caller yet; wiring them into the executor loop is T9 work.
- Intraday re-download via fixed Alpaca script (after key rotation).
- Graveyard re-run on the fixed harness + clean data (numbers will change; expect them to).
- SPEC 5.3 acceptance bar (150 trades / PF 1.3 / maxDD 15% / stress survival) is still not coded as a gate; current gates are the weaker T7 bar. Decide where it lives.
- Strategy inversion remains flag-only (build with gross/net split per review F2 when it happens).
- Multiple-comparison correction (n_eff, BY) still unimplemented.
- Aym's question about importing Python strategy libraries: recommendation is NO bulk import; use a TA library only as an indicator cross-check in tests, optionally one external backtester as a harness second opinion, libraries' docs as Forge research input later (Aym likes this part).
- **Indicator policy (Aym asked why we hand-roll; proposed for Raven's ratification):**
  (1) KEEP the existing hand-rolled RSI/ATR/EMA/MACD - audited correct, zero live-path dependencies; swapping now is regression risk for no gain.
  (2) ADD a library (pandas-ta or TA-Lib) as a TEST-ONLY dependency: known-answer tests requiring our indicators to agree with the library's on the same series.
  (3) All NEW indicators come from the library going forward - no more hand-rolling. Note: the strategy sandbox AST allowlist must then permit those library imports (design decision).
- **Library-vs-hand-rolled inventory (Aym requested a full sweep; swaps pending after test rewrite lands):**
  - REPLACE with libraries (hand-rolling already caused bugs here): session/timezone logic -> `exchange_calendars` (the UTC/ET bug inverted D1; holidays/half-days still unhandled); Black-Scholes -> `py_vollib` (degenerate-branch formula bug found in audit; swap before first real use); .env parsing -> `python-dotenv`; CSV parsing + resampling internals of data_loader -> `pandas` (hand parser silently dropped 706 files), keeping function signatures stable.
  - KEEP + cross-check in tests: indicators (audited correct; pandas-ta known-answer tests), candlestick patterns (TA-Lib CDL* detects only; our versions carry SPEC entry/stop/context - cross-check detection agreement).
  - KEEP deliberately: both harnesses (fills/fees/order semantics ARE the product; add vectorbt as a second-opinion test), risk gate, paper adapter, DB layer.
  - FUTURE components never hand-rolled: python-telegram-bot, notion-client, scipy/statsmodels (n_eff/BY corrections), APScheduler/launchd.
  - CONSTRAINT: any library a STRATEGY may import widens the sandbox AST allowlist (F1) - each strategy-side addition needs Raven's sign-off; engine-side libraries are unconstrained.
- **Aym rulings (2026-08-12 evening, override parts of the above):**
  (1) Indicators SWITCH to the `ta` library as the live math, via facade (signatures unchanged, internals delegate). Aym's rationale: fear of silently breaking hand-rolled math later. Hand-rolled versions become the test-side cross-check reference. Warmup semantics (NaN vs padding) re-verified after swap.
  (2) Multiple backtest harnesses: ours (event + vectorized) remain source of truth; `backtesting.py` added as an external referee in the validation suite - same strategies, same data, same fees, agreement within documented tolerance. Any future harness bug must now fool two unrelated engines.

## Verification status
- Validation suite: 21/21 PASS (oracle inf PF at zero cost, 100% win rates, delayed oracle collapses to ~1.0, buy-hold accounting to the cent, fees strictly reduce PF on all seeds).
- Test suite being rewritten (regression test per fixed bug) and a fresh re-audit of all changes runs after that; results appended to this doc's follow-up.
