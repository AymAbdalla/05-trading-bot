# Claude Code Audit: Trading Bot v1

**Date:** 2026-08-12
**Auditor:** Claude Code (independent code audit, requested by Aym)
**Scope:** Full codebase logic audit: backtest harnesses, validation suite, indicators, strategies, engine/paper path, runners and reporting. Six parallel deep-read audits plus direct verification of top findings.
**Verdict:** The arithmetic core (fees, PnL, profit factor, RSI/ATR/EMA/MACD) is mostly sound. The layers around it are not. Backtest results are contaminated by lookahead and data bugs, the validation suite cannot detect the bugs it was built to catch, and the paper trading loop has four blockers that would make the live experiment measure nothing. Nothing here should be trusted or extended until the blocking items below are fixed and re-run.

---

## 0. The single most important finding

`research/graveyard/harness_validation.json` says `all_pass: false` (generated 17:37 on 2026-08-12). The full graveyard `v0_graveyard_full.json` was generated at 18:41 the same day, one hour AFTER the harness failed its own validation. The prior review (references/harness-validation-review.md, section 11 step 1) said explicitly: do not treat run output as durable until A1-A4 pass. That rule was not honored. All shipped graveyard numbers must be marked provisional.

Worse, the validation suite itself is broken, so even its failures understate the problem (Section 2).

---

## 1. Backtest results cannot be trusted (blocking)

**1.1 Regime filter lookahead, event harness.** `backtest/harness.py:265` slices the 1h regime series with the 15m bar index: `regime_candles[:i+1]`. At 15m bar i, only i/4 hours have passed, but i+1 hourly bars are provided (up to ~3x the elapsed period read from the future). Once `i >= len(regime_candles)` (true for most of any real run) the ENTIRE regime series including the final bar is used, so the gate degenerates to "was the dataset in an uptrend at its end". `build_graveyard.py:150` and `run_go_nogo.py:94` additionally pass full-range regime candles against the 20% test slice, so indices refer to different calendar periods. Verified directly. Confirmed independently by three separate audits.

**1.2 Unadjusted stock splits in the data.** Verified in the CSVs: NVDA 2024-06-07 close 1208.88 to 2024-06-10 open 120.37 (10:1 split rendered as a fake -90% crash); TSLA 2022-08-24 891.29 to 302.36 (3:1). Any long held across those dates books a fake catastrophic loss, downswing detection fires on phantom crashes, and buy-and-hold benchmarks are garbage for every split ticker. Root cause: `download_yfinance.py` uses auto-adjusted prices while `download_alpaca.py` downloads RAW prices to the same filenames (its docstring says it overwrites the yfinance versions). Also ~1,047 CSVs in backtest/data have no provenance from either script.

**1.3 The vectorized harness replaces every strategy's exit plan.** `vectorized_harness.py:533-541`: fills every signal at the signal candle close (ignoring `signal.entry`, so hammer buy-stops, S1 limit orders, VWAP midpoint limits are all filled at a price the strategy never asked for), and for fixed/trailing configs discards `signal.stop` and substitutes `entry - 0.25*ATR`. SPEC 5.1 defines 0.25 ATR as a buffer BELOW the signal low, not the whole stop distance. A 0.25 ATR stop is so tight that fees are ~40% of risk on daily bars, which the SPEC's own fee-to-edge gate would block (no fee-to-edge gate exists in the backtest path at all). Graveyard verdicts therefore describe a risk model no strategy defines and the engine will never trade. `Signal.valid_for` is read nowhere in the repo.

**1.4 The buy-and-hold comparison is mis-denominated.** Both harnesses (`harness.py:137-149`, `vectorized_harness.py:148-155`) compute strategy return as total PnL divided by the SUM of notional across all trades (a per-trade average), then compare it against the FULL-PERIOD buy-and-hold return. In up markets nothing can pass; in down markets losing strategies pass. Shipped example in v0_graveyard.json: BTC/USDT 15m hammer, return -0.21% (lost money), buy-hold -2.66%, `beats_buy_hold: true`. The go/no-go gate measures market direction, not strategy quality.

**1.5 Several strategies in the graveyard were never actually executed.** The graveyard reports these as tested-and-failed when they were structurally incapable of firing:
- `expanded.py:317` FairValueGap: `range(-3, -12)` is an empty range; dead code.
- `strategy_lab.py:266` C2 WeekendVacuumReversion needs 840 candles; the vectorized scan window is capped at 260.
- `expanded.py:559` DCA benchmarks: trigger is `len(closes) % interval == 0`, but window length is constant at 260 after ramp-up, so they go permanently silent.
- `patterns_all.py:253,495` both tasuki gap patterns are logically unsatisfiable (verified against 200k random candles: never fires). Comparisons are swapped.
- D2 FailedBreakoutHarvest short side: emits `direction='bearish'`, both harnesses only accept `'bullish'`; half the hypothesis silently dropped.
- Doji filter, three-white-soldiers trail, and all exit-signal strategies: never consumed by the vectorized harness (imported, unused), so no backtest ever honored SPEC 5.1 rows 5-7.

**1.6 Both harnesses disagree with each other and write the same file.** Different stops, different targets, different regime source (real 1h series vs EMA50 on the signal timeframe), different random twins, different warmup. `build_graveyard.py` (event) and `run_vectorized_graveyard.py` both write `research/graveyard/v0_graveyard.json` with different schemas; last writer wins silently.

**1.7 Latent lookahead channel.** `vectorized_harness.py:206-212, 332-343`: `support_levels_at(i)` serves each 20-bar bucket levels computed at the bucket END, so early bars see swing lows up to 19 bars in the future. No current strategy reads `window['support_levels']`, but the first one that does will backtest with future data. Fix: serve bucket k-1.

---

## 2. The validation suite validates nothing (blocking)

This is the most dangerous cluster because it manufactures false confidence.

- **The oracle, buy-hold, and coin-flip controls never call the harness.** `validate_harness.py:24-166` computes trades inline from raw closes with its own arithmetic. `VectorizedBacktestHarness` is never invoked. The message "Oracle control failed = signal-to-fill wiring is broken" is printed by a check that never touches the wiring. The harness could fill one bar early on every trade and A1 would still pass.
- **The control strategies in `strategies/builtin/controls.py` can never fire.** ORACLE_CONTROL's guard `i >= len(closes)-1` with `i = len(closes)-1` is always true (the in-code comment shows the author noticing this and stopping: "We need a different approach."). All three controls emit `direction='long'` but harnesses only accept `'bullish'`. BuyHoldControl's `stop=0.0` is falsy and fails the harness's `and signal.stop` check.
- **The lookahead shift test shifts nothing.** `validate_harness.py:195` builds `test_candles[1:] + [last]`: signals are NOT shifted relative to outcomes, so normal and shifted PF are identical by construction (harness_validation.json shows degradation 0.0 everywhere). Its failures also never set `all_pass = False`, and numpy booleans are serialized as strings ("False" is truthy in any Python consumer).
- **Oracle results that DID run show broken intraday wiring.** AAPL 1h oracle: PF 2.41, 45% win rate. A strategy that sees the future losing more than half its trades on 1h data is a five-alarm signal that something in the intraday path (resampling, alignment, or the inline control itself) is wrong. It was recorded and the run proceeded anyway.

Consequence: every one of the harness bugs in Section 1 sailed through a validation suite purpose-built (per the prior review's A1-A3) to catch exactly them.

---

## 3. Gates and reporting (major)

- **Infinite PF passes every gate in the event path.** `harness.py:98-101` returns inf when there are zero losses; `harness.py:583` and `build_graveyard.py:161` pass it. The vectorized path guards this; run_go_nogo/run_fast_gonogo/build_graveyard do not.
- **SPEC 5.3 acceptance bar exists nowhere.** Everything gates on trades >= 20 and PF >= 1.15. Nothing checks 150 trades, PF 1.3, maxDD <= 15%, single-trade concentration, or stress-probe survival (stress probes run but never affect verdicts).
- **Max drawdown formula is wrong and unused.** `harness.py:120-135` computes DD on cumulative trade PnL with a `peak > 0` guard: a pure losing streak reports 0% drawdown; a tiny dip after tiny profits reports ~96%. `tests/test_backtest.py:197-208` asserts the wrong semantics, locking the bug in.
- **Random twin is one seeded draw.** `random.seed(42)`, single sample, mismatched trade count; the event twin uses fixed 2%/4% exits. A gate criterion built on one noise sample (shipped data: ETH 1h twin PF 0.0, making beats_twin trivially true). Prior review said 100 matched twins with percentile output.
- **Execution-delay stress probe time-travels.** `harness.py:314-336`: with delay=1 the exit check runs against the entry bar's own pre-entry range; exits can precede entries.
- **7,020 combos, best-of-N presentation, no multiple-comparison correction.** Honest outcome this time (0 of 7,020 passed), but the printed "PROCEED to T8 if any pass" logic would promote the luckiest of 7,020 draws as if it were one pre-registered test. The prior review's n_eff/BY guidance is unimplemented.
- **Strategy inversion (SPEC 5.6) is a flag, not a feature.** 1,733 `inversion_flagged` entries, zero inverted runs. No sign-flip fee bug exists because the feature does not exist. Whoever builds it: gross vs net PF split first, per the prior review's F2.
- **Optimistic fills throughout.** Gap-through-stop fills at the stop price, not the open (with 1.2's fake split gaps this is enormous); stop exits are the one fill exempt from slippage.
- **Graveyard runs loosen the SPEC 5.2 stack** (RSI 60 to 70, volume 1.5x to 1.2x, location check absent in both harnesses). Deliberate, but graveyard verdicts are not SPEC-confirmation verdicts and are not labeled as such.
- `harness.py:380`: exit timestamps are wall-clock now(), not candle time; holding-period stats are meaningless.

---

## 4. Engine / paper path: four blockers before any paper run

- **B1. Scanner evaluates the forming candle, once, seconds after it opens, and never the closed candle.** `scanner.py:252-257` + `collector.py:41`: ccxt returns the in-progress candle; the scanner marks it scanned on first sight (~15s of volume) and never revisits. The volume filter compares ~zero partial volume to full-candle averages, so nearly every entry fails `volume_low`; anything that does fire is based on a candle that will repaint. Live behavior is structurally different from the closed-candle backtest.
- **B2. The 4-loss "24h pause" is permanent.** `risk.py:112-124, 201-206`: no time window, no expiry. Four losses in a row and the bot can never trade again (the streak can only break via a win that can never happen). Manual DB edit is the only recovery.
- **B3. No stop or target simulation exists in the paper path.** No code compares live price to stop_px/target_px; no executor loop exists. A paper position can drop 30% and sit open forever, and `get_equity()` (closed PnL only) will not even show the loss.
- **B4. Regime slope truthiness.** `scanner.py:119-122`: `slope_ok = ema_slope(...)` returns a float; `if slope_ok` treats NEGATIVE slopes as pass. The SPEC's "slope positive" filter is effectively just "price above EMA". One-character fix (`> 0`). Note `harness.py:217` does it correctly, so live silently diverges from backtest. `tests/test_scanner.py:164` widens its assertion around the bug instead of catching it.

Also major: signals logged `acted=1` before the risk gate runs (corrupts the skipped-signal learning dataset at the source, scanner.py:271,287); ops backstops read a table (`equity_snapshots`) that nothing ever writes; sell/exit orders consume the 1-trade/day entry budget (risk.py:87-95); doji block_entries is emitted but never enforced; risk gate accepts stop above entry (`abs()` masks it, risk.py:66-85); in-memory dedup re-logs signals after restart; `positions.signal_id` silently dropped on insert; hammer buy-stop unimplemented in the adapter (market orders only); schema omits `code_hash` (SPEC F8).

---

## 5. Indicators and patterns

Sound: RSI (Wilder, verified against textbook series), ATR, EMA core, MACD 12/26/9, Bollinger, engulfing body logic, morning star midpoint, TWS, doji, hammer/hanging-man disambiguation. No pattern indexes future bars.

Deviations:
- Piercing line tests open below prior CLOSE, not prior LOW (SPEC 5.1 #4); fires far more often than specified. Both pattern files; the comment even mislabels the variable.
- on_neck / in_neck registered as bullish entries; canonical doctrine says bearish continuation. They buy into confirmed downtrends.
- Bullish engulfing drops SPEC's "or within 1x ATR of support" alternative (requires downswing unconditionally).
- Stops wider than SPEC in engulfing / morning star / piercing (min of multiple lows instead of the specified single low), roughly doubling R in some cases.
- `ema_slope` warmup guard admits SMA-seed padding: garbage slopes with fewer than period+lookback candles.
- Volume SMA includes the signal candle itself (marginal signals fail that shouldn't); live and backtest at least agree.
- Flat-market RSI returns 100 (blocks entries); convention is 50.

---

## 6. Tests

66/66 passing is not evidence of correctness:
- `test_backtest.py` trade-execution tests run on a fixture that produces ZERO trades (verified empirically): loops never execute, `assert 0 <= 0 + 0.01`.
- `test_scanner.py`: `assert qsize() >= 0` (always true), `assert True`, and assertions widened to tolerate the regime bug.
- `test_risk.py`: conditional assertions that pass vacuously; no test for pause expiry, sell-counting, or inverted stops.
- Two tests assert wrong expected values (drawdown semantics, buy-hold comparison), locking bugs in.
- `test_paper_adapter.py` arithmetic assertions are genuine and correct (fills, fees, R).

---

## 7. Security

`backtest/download_alpaca.py:22-23` hardcodes an Alpaca API key and secret in source. Rotate the key at Alpaca and move to .env. (.env exists and is gitignored; repo is not a git repo yet, which is the only reason this hasn't leaked.)

---

## 8. What is genuinely sound

Fee math (0.10% per leg, both legs, correct notional), fixed-$100 sizing, fee-to-edge gate math (matches SPEC 6.4 exactly, including the worked examples), PF/expectancy/win-rate arithmetic, stop-first same-bar tie-break (conservative), chronological splits, DB layer (WAL, transactions, audit-before-order), confirmation stack ordering in the scanner, vectorized random twin design (same exit config as strategy), Black-Scholes d1/d2/parity/Greeks, and the shipped graveyard JSONs are internally consistent with their own (flawed) formulas: all 10,260 entries recompute cleanly, zero mismatches. The "0 of 7,020 passed" headline is arithmetically honest.

---

## 9. Recommended sequence (blocking order)

1. **Mark all current graveyard/backtest output provisional.** The validation file already says so; honor it. Do not let Quant/Forge read it as ground truth.
2. **Fix the validation suite first** so it can certify the fixes: controls must run THROUGH the harness (fix controls.py direction/guards/stop so they can fire), shift test must shift signals relative to outcomes, failures must set all_pass, booleans must be booleans.
3. **Fix harness blockers:** regime indexing (1.1), honor signal.entry/stop/valid_for or explicitly document fill-at-close as the contract and make both harnesses and SPEC agree (1.3), buy-hold comparison on same-notional dollars (1.4), gap fills at max(stop, open) worse-of, inf-PF guard in the event path.
4. **Fix the data:** pick ONE adjustment convention (adjusted everywhere is simplest for v1), re-download, add a split-gap detector to data_loader, document provenance for every CSV.
5. **Remove or fix dead strategies** (1.5) so the graveyard only contains strategies that actually ran.
6. **Re-run the graveyard** with the fixed harness on clean data. Expect different numbers.
7. **Fix the four engine blockers (B1-B4) before starting the paper experiment**, plus acted-flag logging and equity snapshots so the measurement layer works.
8. Rotate the Alpaca key.
9. Rewrite the vacuous tests as real regression tests for each bug above (each finding here is a test case).

Full agent-level detail (six audit reports with per-line findings) available from Claude Code on request.
