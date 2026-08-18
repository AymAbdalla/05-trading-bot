# Decisions Log

## v11 - 2026-08-17 (multi-asset scope expansion, Polymarket added, harness rulings D-269 to D-284, shadow loop live)

### D-267. SPEC expanded to multi-asset; Polymarket added as an asset class (AYM RULING)

Aym, 2026-08-17: "we are no longer binance.us only or crypto only, we are
making a trading bot! we need to add polymarkets in it."

SPEC.md is expanded. The bot is a multi-asset trading engine. Crypto, equities,
ETFs, futures, options, and prediction markets (Polymarket) are all in scope for
backtest. Paper mode first, for every class, without exception. Recorded in
SPEC.md Section 1 (scope expansion note) and Section 2.1 (Polymarket).

Additive, not a replacement. Binance.US spot crypto is untouched and remains
the reference execution path. No existing crypto strategy, graveyard entry, or
verdict changes as a result of this decision.

Three things this does NOT relax:
- Long-only still binds SPOT execution. You cannot short spot without
  borrowing. Short exposure comes from futures and options, as Section 2
  already specified. Polymarket needs neither: buying the No side IS the short.
- Paper mode first, always. No asset class goes live without Aym's explicit
  approval, and every strategy passes the graveyard first.
- moondevonyt's published win rates and EV figures are HIS numbers from HIS
  logs on HIS setup. They are hypotheses, not evidence, until our graveyard
  says otherwise (convention 3). We take his strategy logic and take no
  dependency on his MoonDev API - liquidation and whale data come from public
  Binance/Bybit/OKX and Hyperliquid endpoints directly.

Polymarket specifics: binary outcome markets on Polygon, priced $0.00-$1.00,
settled in USDC, winners redeem at $1.00 and losers at $0.00. Data from three
public read-only zero-auth APIs (Gamma for discovery, CLOB for orderbooks and
price history, Data API for trades and open interest). Paper execution is a
simulated taker fill walked against the live CLOB book. Live execution would
need the CLOB SDK V2 with wallet-based EIP-712 signing and is explicitly out of
scope until paper mode proves a strategy through the graveyard AND Aym approves.

**The measurement consequence, which is the part that actually costs work.**
On Polymarket the price IS the market's probability estimate, so edge means a
calibration disagreement, not a price forecast. PnL is resolution-based: you
hold to settlement and collect $1.00 or $0.00. It is not path-based. Profit
factor, stop distance, R-multiple, and MAE/MFE - which the entire existing gate
stack is built on - do not transfer unmodified. Payoff is bounded both ways,
which caps loss per contract at the premium paid but also caps upside, so being
right 60% of the time at 55c is profitable and being right 60% of the time at
65c is not. Entry price and win rate are meaningless read apart.

Therefore: a Polymarket strategy is NOT durable until the harness extension
(SPEC 2.1 / brief task 3D) exists to score it on resolution PnL. Until then a
Polymarket strategy is code that has never been tested. Convention 11 -
NOT_TESTED, never tested-and-found-nothing. See D-268.

### D-268. Polymarket strategies ship as NOT_TESTED until the harness extension lands (CC)

The four ported strategies (streak_snapper, mid_price_continuation, box_builder,
corridor_collector) implement the base `Strategy` interface so the scanner can
call them, and they are structurally honest about the binary payoff: entry is
the per-share premium, stop is 0.00 (a losing share is worth zero - the true
floor, and it satisfies convention 8's stop-strictly-below-entry), target is
1.00 (resolution).

That makes them interface-compatible. It does NOT make them harness-runnable.
Feeding them to the existing vectorized harness would score a Polymarket
contract's payoff against BTC's price path, which is a different instrument -
the numbers would be fabricated, in the same way the pre-purge FUTURES rows
were. They must not be swept until 3D exists.

Recorded so a future session cannot mistake "the code is written" for "the
strategy was tested." Kill condition for all four: if the resolution-PnL
harness scores a strategy below 30bps net edge on our own data, it dies,
whatever moondevonyt's logs say.

### D-269. Bar starvation fix: lower min_idx for daily and weekly series (RAVEN RULING)

Cody's diagnosis (`docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md`)
measured that `min_idx = min(SCAN_WINDOW, 100) = 100`
(`vectorized_harness.py:1024`) against a last-20% test slice leaves daily
series a median of ONE scannable bar: 5,100 across 175 series, 5.01% of the
daily bars on disk. Weekly series get a median of 57.

Convention 11: NOT_TESTED means "could not run," never "ran and found
nothing." A strategy tested on 1 bar could not meaningfully run. The current
min_idx is a harness default that was never validated against daily/weekly
timeframes. Strategies labeled FAIL on daily/weekly were actually NOT_TESTED.

Ruling: lower `min_idx` to `max(strategy.min_bars, 25)` for daily and weekly
series. This is correcting a measurement error, not changing method or
loosening a filter. The scan window was too narrow for strategies to compute
their indicators.

Convention 17 caution: this will make numbers improve. The improvement comes
from strategies actually being tested, not from relaxing standards. Pre-change
graveyard numbers must be snapshotted before the re-run and compared
deliberately after.

### D-270. Confirmation stack fix for mean-reversion cohort (RAVEN RULING)

The sweep never sets `apply_confirmation_stack` or `require_regime_uptrend`,
so both default True (`vectorized_harness.py:610-611`). Every signal must
additionally satisfy `close > rising EMA50`. Cody measured that this removed
100% of V2_vwap_magnet_sessionatr, 99.5% of its control twin, 92% of
V5_capitulation_equity's candidate days, 87% of V3_intraday_momentum_crypto,
and 82% of V4_trend_reclaim.

On capitulation days specifically, `regime_uptrend` is true 7.77% of the time
against a 49.71% unconditional baseline: a 6.4x suppression of exactly the
signals mean-reversion strategies are designed to find.

Convention 11: a mean-reversion strategy filtered through `close > rising
EMA50` has not been tested and found wanting. It has not been tested.

Ruling: set `apply_confirmation_stack=False` for the mean-reversion cohort
(V2_vwap_magnet, V2_vwap_magnet_sessionatr, V5_capitulation_equity, and any
other strategy whose thesis is buying weakness or fading). Trend-following
strategies keep the stack. The machinery already exists and is used by
`constraint_sweep.py:64` and `dispersion_gate.py:353`.

Convention 17 caution applies identically. Pre-change numbers preserved.

### D-271. rsi_extreme threshold fix: 35 to 45 (RAVEN RULING)

`rsi_extreme` requires `rsi14 < 35` AND `close > ema50`. Over 42,010 bars:
4,783 satisfy the first, 21,982 the second, zero satisfy both. RSI(14)
conditional on `close > EMA50` has a hard floor at 36.26, so the threshold
sits below the support of the conditional distribution. Category (b):
unsatisfiable, not tight.

The thesis (oversold pullback in an uptrend) is sound. The threshold is wrong.

Ruling: change `rsi14 < 35` to `rsi14 >= 45` (1.28% firing rate). This
preserves the "oversold" intent while making the condition satisfiable. The
`>= 50` option (10.12% firing) is available if 1.28% proves too sparse after
the re-sweep.

### D-272. C2 lookback units fix and stale row deletion (RAVEN RULING)

Two problems in C2 (`strategies/builtin/strategy_lab.py`):

1. Units bug: anchor lookback is `24 * 4` BARS meaning 4 DAYS. True only for
   hourly bars. On 15m it reaches back 24 hours; on 5m, 8 hours. It can never
   reach Friday. Measured: 100% anchor failure on every sub-hourly series.

   Ruling: fix the lookback to be time-aware (convert 4 days to bars based on
   the series timeframe).

2. Stale rows: 9,042 of 9,735 C2 rows in the graveyard carry the reason
   string `"needs 840 bars, scan window is 260"`, which does not exist
   anywhere in the current codebase. The current gate emits `"needs 840 bars,
   series has {n}"`. These rows were written by a pre-fix harness; C2 has
   never run under current code.

   Ruling: delete the 9,042 stale rows before the re-sweep. Under current
   code C2 would be testable on 401 of 860 series.

Separately: the long-only constraint means C2 cannot reach 20 trades as a
long-only spot strategy (all 10 qualified weekend vacuums were moves UP).
This is a strategy design issue, not a harness bug. After the lookback fix
and re-run, if C2 still cannot fire meaningfully, retire it with a D-number
rather than repairing a second time (per proposal 004's kill condition).

### D-273. rsi_extreme threshold shipped and measured: 35 to 45 (CC, implements D-271 / R-007)

Raven ruled it in R-007 (`docs/handoffs/from-raven/2026-08-17-rulings-and-shadow-go.md`),
Aym authorised the round-2 rulings the same day. D-271 records the RULING.
This records the IMPLEMENTATION and the measured result, because a number
written into a decision before the run is an estimate until it is corrected
against the log (convention 15).

Where it lands: `strategies/builtin/expanded.py`, class `RsiExtreme`. The
threshold is now a named class constant `RSI_MAX_ENTRY = 45.0` instead of an
inline literal, because a hardcoded threshold is an assumption with an expiry
date (convention 17) and this one already expired once.

Measured by replaying the sweep's own pipeline outside the harness (`load_csv`
to last-20% test slice to `precompute_indicators` to `_make_window` to
`scan()`), so these are firing counts on real data, not estimates. The
scanned-bar counts reproduce the diagnosis table exactly (5,100 / 7,898 /
195,282 / 204,978 / 596,922), which is how we know it is the same universe
and not a friendlier one:

| timeframe | series | bars scanned | fires at 35 | fires at 45 | rate at 45 | conditional RSI floor |
|---|---|---|---|---|---|---|
| 1d  | 176 | 5,101   | 0 | 33    | 0.647% | 40.38 |
| 1wk | 164 | 7,898   | 0 | 57    | 0.722% | 41.00 |
| 1h  | 175 | 195,282 | 0 | 1,480 | 0.758% | 35.68 |
| 15m | 178 | 204,978 | 7 | 1,382 | 0.674% | 32.99 |
| 5m  | 178 | 596,922 | 7 | 4,179 | 0.700% | 31.71 |
| **all** | | **1,010,181** | **14** | **7,131** | **0.706%** | |

Correction to the diagnosis, on the record rather than quietly: "zero of
42,010 bars" is exact on daily, weekly and hourly, and NOT exact on 15m and
5m, where 7 bars each do satisfy both clauses. The conditional RSI floor is
below 35 on sub-hourly bars (32.99 and 31.71). So the honest claim is not
"logically impossible everywhere" but "impossible on 1d/1wk/1h and 14 firings
in 1,010,181 bars overall" - 0.0014%, which cannot produce a testable sample
under any sweep. The conclusion does not change; the wording does.

Convention 17 check, stated because this is exactly the shape of the
`COST_FLOOR = -0.30` false positive: this change LOOSENS a filter and the
number improved from ~zero. It is not a false positive here, and the reason
is a falsifiable measurement rather than a judgement. RSI(14) conditional on
`close > EMA50` has a measured floor of 40.38 on daily and 35.68 on hourly.
A threshold of 35 excludes 100% of the conditional support on those
timeframes. No sample size, no re-run and no cost model could ever have
produced a trade there. A strategy that cannot produce a trade was not
tested (convention 11), so rsi_extreme's 9,042 FAIL rows in the pre-resweep
graveyard were mislabelled and should have read NOT_TESTED.

What this does NOT establish: firing is not edge. 7,131 signals is a testable
sample, not a result, and the 5m column is 0.700% of bars - frequent enough
that costs will dominate. Whether rsi_extreme clears the gate is the
re-sweep's answer, not this decision's.

Kill condition: if the re-sweep shows rsi_extreme firing and failing on
economics, it dies on economics. 45 does not get re-tuned toward 50 to chase
a PASS. The `>= 50` option D-271 mentions is retired by this decision unless
the re-sweep produces under 200 trades in total, which the table above says
it will not.

Two caveats on the numbers, so nobody has to rediscover them. (1) The clause
counters read the harness's precomputed numpy Wilder RSI/EMA while `scan()`
recomputes with the pure-Python indicators; the two agree exactly on every
timeframe except 5m, where scan fires 4,179 against 4,177 clause-satisfying
bars. A 2-in-596,922 boundary disagreement, recorded rather than rounded
away. (2) These are RAW signal counts taken before the confirmation stack.
The stack is being changed concurrently under D-270, and a raw count is the
property this fix actually owns.

Pinned by `tests/test_r007_r008_fixes.py` (5 tests + 5 real-data
parametrisations), including an assertion that the threshold sits above the
measured conditional floor - the invariant whose violation was the bug.

### D-274. C2 lookback made timeframe-aware; min_bars is not, and that caps the fix (CC, implements D-272 part 1 / R-008)

Raven ruled it in R-008, Aym authorised. D-272 records the ruling. This
records the implementation, the measured before/after, and one thing the
ruling did not anticipate.

Where it lands: `strategies/builtin/strategy_lab.py`. Three hardcoded bar
counts became durations, and a shared `_bar_seconds()` helper infers the bar
size from the window's own timestamps (median spacing, not mean, because
equity series have weekend holes):

| horizon | was | now | 1h | 15m | 5m |
|---|---|---|---|---|---|
| anchor lookback | `24 * 4` bars | `ANCHOR_LOOKBACK_SECONDS` = 4 days | 96 | 384 | 1,152 |
| baseline weekly step | `168` bars | `WEEK_SECONDS` = 7 days | 168 | 672 | 2,016 |
| in-scan history guard | `24 * 7 * 5` bars | `HISTORY_SECONDS` = 5 weeks | 840 | 3,360 | 10,080 |

On 1h the fix is a no-op by construction: the old literals were the correct
1h values, which is why the bug survived. Verified on real data, 18
crypto series across 5 timeframes and 2 data sources (Binance monthly
slices and the yfinance-style CSVs), sweep test slices:

| series | pre-fix anchor resolved | post-fix anchor resolved | pre-fix history guard | post-fix history guard |
|---|---|---|---|---|
| BTC/ETH/SOL 1h | 12/20 trigger bars each | 12/20 (unchanged) | 12 | 12 |
| BTC_USD/ETH_USD/SOL_USD 1h | 32/40 each | 32/40 (unchanged) | 32 | 32 |
| BTC/ETH/SOL 15m | **0/80 each** | 0/80 | 72 (wrongly passed) | 0 |
| BTC_USD/ETH_USD/SOL_USD 5m | **0/48 each** | 0/48 | 24 (wrongly passed) | 0 |

Read that table honestly: the fix produced ZERO new signals. What it changed
is which gate rejects sub-hourly bars. Pre-fix, a 15m series cleared the
840-bar history guard (840 bars is 5 weeks only on 1h) and then failed the
anchor search silently on 100% of trigger bars. Post-fix it fails the history
guard, which is the true reason and the honest one.

**The thing R-008 did not anticipate, stated explicitly because the task
asked and because it is the part that matters.** Fixing the lookback is not
sufficient for the fix to be REACHABLE on sub-hourly series. `min_bars` is a
class constant the harness reads before it knows the timeframe
(`vectorized_harness.py`), and it sizes the window it hands `scan()` as
`max(SCAN_WINDOW, min_bars)` = 840 bars. On 15m the strategy genuinely needs
3,360 bars and on 5m 10,080, so it can never satisfy its own history
requirement inside the window it is given. `min_bars` therefore DOES need to
become timeframe-aware. `WeekendVacuumReversion.min_bars_for(bar_seconds)`
now exposes the correct per-timeframe requirement; `min_bars` itself is left
at 840 because changing it is a harness-contract change and the harness is
another session's surface this cycle.

The consequence is a live convention 11 violation: 3,360 and 10,080 both
exceed `MAX_STRATEGY_WINDOW` (2,000), so the honest verdict for C2 on 15m and
5m is NOT_TESTED - the harness structurally cannot supply the history - but
until the gate calls `min_bars_for()` those rows will be written as FAIL.
One-line follow-up at the two `getattr(strategy, 'min_bars', ...)` call sites.
Until it lands, do not read a C2 sub-hourly FAIL as a verdict.

**C2 on daily and weekly is structurally dead, and no lookback fix reaches
it.** C2's trigger requires a Sunday bar at hour >= 22. Measured over 40
daily series (1,132 scanned bars): daily bars stamp at hours 0, 4 and 5 UTC
only, maximum hour 5. Equity daily series have no Sunday bar at all. Zero
trigger bars, therefore zero signals, on every daily and weekly series, in
both the pre-fix and post-fix code. C2 is an hourly-or-finer crypto strategy
and its daily/weekly graveyard rows are NOT_TESTED, not FAIL. Pinned by test.

**C2 does fire (convention 3 satisfied), on 1h, on full history.** Over the
full 1h series for BTC/ETH/SOL from both sources: 876 trigger bars with the
anchor resolved, 244 clear the 1.5x median-move gate, 46 also clear the
sub-40th-percentile volume gate. Of those 46 fully-qualified weekend vacuums,
36 were moves UP and are discarded by the long-only spot constraint, 10 were
moves DOWN, and `scan()` returns a signal on all 10. Zero of them fall inside
the sweep's last-20% test slice, which is why the sweep sees nothing.

That 36-to-10 split is the strategy's real problem and it is not a bug: you
cannot short spot (D-267). Proposal 004's kill condition applies. If the
re-sweep confirms C2 cannot reach 20 trades on 1h, it should be retired with
its own D-number rather than repaired a third time.

Not fixed, disclosed rather than smoothed over: `valid_for=48` in C2's Signal
is the same units bug (48 bars, intended 48 hours; 12 hours on 15m). It is an
order-lifetime parameter rather than a lookback, so it sits outside R-008's
ruling, and C2 currently produces zero trades so no baseline is corrupted by
leaving it. It should be swept up when C2 is retired or repaired.

Pinned by `tests/test_r007_r008_fixes.py` (8 tests), including one that fails
if 15m ever becomes reachable inside the default window, so the caveat above
cannot silently go stale.

### D-275. C2 stale rows ARCHIVED, not deleted (RAVEN RULING R-009, supersedes D-272 part 2)

D-272 ruled "delete the 9,042 stale rows before the re-sweep." R-009, issued
later the same day in `docs/handoffs/from-raven/2026-08-17-rulings-and-shadow-go.md`,
ruled ARCHIVE, do not delete. R-009 is the later ruling and it governs.
Archiving preserves the audit trail without polluting the active graveyard,
and it is the same instinct as D-255: an unreadable graveyard is not an empty
one, and evidence you deleted is evidence you cannot re-examine.

Where it lands: `research/graveyard/archive/c2_stale_rows.json` (3.9MB),
written by `backtest/archive_c2_stale_rows.py`, which reuses the streaming
object-by-object reader in `backtest/snapshot_graveyard.py` rather than
`json.load`ing the 389MB graveyard into RAM.

Measured, not assumed. Extracted from the stable pre-fix backup
`research/graveyard/archive/v0_graveyard_full.pre-D269-D272.json` rather than
the live file, because a concurrent re-sweep was rewriting the live one
(convention 21):

| bucket | rows |
|---|---|
| rows scanned | 535,425 |
| non-C2 | 525,690 |
| C2 total | 9,735 |
| C2 stale, archived | **9,042** |
| C2 under the current gate string | 154 |
| C2 other (264 unsizable_at_cap, 275 short-slice) | 539 |

9,042 exactly, matching the diagnosis. Convention 20: every bucket is counted
AND categorised, no two drop causes share a number, and both accounting
identities (`scanned == C2 + non-C2` and `C2 == stale + current + other`) are
asserted in the script, not checked by eye. Convention 19: written with
`json.dump(allow_nan=False)` and portability proved with
`node -e 'JSON.parse(...)'`, because `json.loads` would happily accept
`Infinity` back and no other parser will.

Why these rows are stale, restated because it is the whole justification:
they carry `not_tested_reason == "needs 840 bars, scan window is 260"`, and
no code in the tree emits that string. The current gate emits
`needs 840 bars, series has {n}`. They were written by a pre-fix harness that
refused to widen the scan window; `scan_all_bars` now widens to
`max(SCAN_WINDOW, min_bars)`. C2 has never run under current code.

Removal from the ACTIVE graveyard is deliberately not done here. The re-sweep
regenerates `v0_graveyard_full.json` from scratch, so the stale rows are gone
by regeneration, and rewriting a 389MB file another process is writing is how
you lose both copies. The audit trail is closed by the archived count above.

Pinned by `tests/test_r007_r008_fixes.py` (3 tests), including one that fails
if `vectorized_harness.py` ever regains the ability to emit the stale string -
which would mean these rows are not stale and archiving them was hiding
evidence rather than filing it.

### D-276. V3 removed from mean-reversion cohort (RAVEN RULING, corrects R-006)

R-006 included `V3_intraday_momentum_crypto` in the mean-reversion cohort by
mistake. V3's thesis is "the first half hour's return predicts the last half
hour's return" - that is momentum (trend continuation), not mean-reversion.
The confirmation stack (`close > rising EMA50`) is a trend filter appropriate
for momentum strategies. Applying it to V3 is correct; removing it was the
error.

Cody (session A, `docs/handoffs/2026-08-17-resweep-BLOCKED-two-sessions.md`)
identified this: "Under D-270's own criterion V3 does not belong in the
cohort."

Ruling: remove `V3_intraday_momentum_crypto` from `R006_COHORT` in
`strategies/cohorts.py` before the re-sweep. The assertion in
`_assert_consistent` will catch downstream drift.

### D-277. EMA convergence floor raised from 25 to 50 (RAVEN RULING)

D-269 set `SLOW_TF_MIN_SCAN_START = 25` for daily and weekly timeframes. Cody
(session A) flagged that `_ema` seeds the pre-convergence region with
`closes[0]`, so on bars before index ~49 the precomputed `ema50` is a seed,
not an EMA-50. With the floor at 25, roughly 24 of 76 scannable bars on a
101-bar daily series sit in the seeded region, and `regime_uptrend` is False
there by construction.

Ruling: raise `SLOW_TF_MIN_SCAN_START` from 25 to 50. The cost is minimal (for
most daily series the test slice starts well past bar 50) and the gain is
correctness: no scan enters the EMA seed region. Session B already documented
this as a caveat in the code; promoting it to a fix.

### D-278. rising_three_methods: ATR threshold 0.7 to 1.0 (RAVEN RULING)

`rising_three_methods` is one of two non-firing strategies not covered by any
prior ruling (D-269..D-275 and R-005..R-009 between them cover seven of the
nine). Its binding clause `small_reds` has 2 hits in 32,679 bars. The diagnosed
fix is loosening the ATR threshold from 0.7 to 1.0.

Ruling: apply the fix. This is a pure threshold loosening, so convention 17
applies: document before/after. If it still does not fire meaningfully after
the re-sweep, recommend retirement with a D-number.

### D-279. V4_trend_reclaim: exempt weekly bars from volume_min_ratio (RAVEN RULING)

`V4_trend_reclaim` is the second of two non-firing strategies not covered by
any prior ruling. 27 of 27 candidates die on `volume_min_ratio >= 1.2` alone,
not on the regime filter. V4 is trend-following, so it correctly keeps the
confirmation stack. The volume filter issue is a data-availability problem on
weekly bars (weekly volume is inherently lower and more variable).

Ruling: exempt weekly bars from `volume_min_ratio` for V4 only. Same shape as
D-269: a harness default never validated against weekly timeframes. Document
before/after.

### D-280. PM_temporal_arbitrage: proposal 002 shipped as runnable strategy (RAVEN RULING)

Implements Forge proposal 002: buy two sides of one 5m window at different
instants when each is cheap, pair redeems $1.00. Between legs the position is
NAKED. Leg-completion risk, not direction, is the whole trade.

Deviations from proposal 002, all tightenings, all in the module docstring:
leg 1 capped at 0.35 not 0.47 (moves break-even completion from ~89% to ~69%);
directional trigger added (proposal has none); 5-share blocks not 50; both caps
judged on the book-walked effective entry, not top-of-book.

NOT_TESTED until the harness extension lands (D-268). Completion rate is NOT
computable from ENTER decisions alone: `evaluate()` sees decisions, never fills,
and the halt check / risk gate / paper adapter downstream can all refuse. Must
come from a join of the `positions` table on `window_ts`. Every row carries
`completion_rate_measurable_from_this_log = False` and
`leg1_fill_confirmed = False` until a fill-confirmation callback is wired.

### D-281. PM_cross_window_relative_value: floored pair, NOT proposal 005 (RAVEN RULING)

The task brief described a floored-pair structure (15m leader + final-5m
opposite, pair floored at $1.00) and named it after proposal 005. Proposal 005
is NOT that. It is a one-leg relative-value bet with no floor that can lose its
whole premium. The floored pair is proposal 005's own "nearest neighbour"
(`corridor_collector`), with an explicit table of the differences and an
instruction never to pool them. Proposal 005's `data_requirements` call the
missing 30 days of paired history a BLOCKER.

Ruling on caveat 1: keep the class name (`CrossWindowRelativeValue`) since it
is already wired and the docstring is honest about the mismatch. BUT the
`strategy_name` attribute must be `PM_corridor_pair` (not anything referencing
proposal 005) so no graveyard row, dashboard line, or handoff can be read as a
measurement of proposal 005. Proposal 005 stays PROPOSED and unbuilt. No result
from this strategy is evidence for or against proposal 005. Every decision row
already carries `implements_proposal_005_hypothesis = False` and
`structure = 'floored_pair_not_relative_value'`.

Ruling on caveat 2: apply the 8c edge floor. The brief's $1.41 pair cap is fair
value (1.00 + 0.413 blended corridor rate). Paying fair value earns exactly
zero before fees. Worse, 0.413 is a blend and the binned table reads 0.326 at a
5-10bps lead, so at a 6bps lead the fair pair is 1.326 and a 1.41 cap is 8.4c
above fair: a reliably negative-expectancy entry. Zero-edge pairs at binned fair
value are negative-expectancy after fees by construction. corridor_collector's
8c edge requirement applies here too. `edge_vs_binned_fair >= 0.08` is a hard
gate. The `require_binned_fair` second gate stays. `pair_cap_binding` names
which gate stopped the trade.

### D-282. PM_spread_harvest_taker: taker adaptation, book_implied gate disabled (RAVEN RULING)

Taker adaptation of moondevonyt's `spread_harvest_maker`. This is a DIFFERENT
ORDER, not a tightening: his bot rests a post-only bid inside the spread; ours
pays the ask. Getting paid the spread is his entire thesis. File and class keep
his name; `strategy_name` is `PM_spread_harvest_taker` so no graveyard row can
read as a measurement of his maker bot.

Ruling on caveat 3: ship with `allow_book_implied_coin_flip=False` until a
Chainlink settlement strike feed exists. His primary gate
(`coa = |spot - strike| / ATR <= 0.40`) is unavailable because Gamma does not
publish the strike. `book_implied` (the 0.40-0.48 price band doing the near-tie
work) is a DIFFERENT GATE, not a looser one: it asks what the book thinks, so a
window that has quietly run away from the strike while quotes lag passes here
and would fail his. The two populations must never be pooled.
`allow_book_implied_coin_flip=False` refuses to trade without a real strike.
Results under `coin_flip_source='book_implied'` must be scored SEPARATELY from
any produced under `cushion_atr`.

NOT_TESTED until the harness extension lands (D-268).

### D-283. corridor_collector: final-third check is a latent bug, fix before unblock (RAVEN RULING)

Cody found that `corridor_collector` never checks that the 5m window is the
FINAL THIRD of its 15m parent. The $1.00 floor exists ONLY because both markets
settle off the same close. Pair the 15m leader with the first or second third
and BOTH legs can lose, and nothing in the pricing would tell you.

Latent because corridor_collector cannot fire today (no strike, no `lead_bps`).
The new strategy (`PM_corridor_pair`, D-281) already enforces it:
`not_final_third_of_15m`, with a test.

Ruling: assign D-283. The fix is one line: add the final-third check to
`corridor_collector` before it is ever unblocked. Do not unblock
`corridor_collector` without this fix.

### D-284. rising_three_methods RETIRED (RAVEN RULING, executes D-278 kill condition)

D-278 loosened the ATR threshold from 0.7 to 1.0 and set the kill condition:
"if it still does not fire meaningfully after the re-sweep, recommend retirement
with a D-number."

After the fix: `small_reds` clause unblocked 8.6x (70 to 600 hits), but the
pattern still fires ZERO times. `within_range` is now binding (1.03% of bars);
it and `small_reds` co-occur on 5 of 13,901 bars, and none of those 5 satisfy
all remaining clauses simultaneously.

The kill condition is met. `rising_three_methods` is RETIRED. The strategy
class stays in the codebase (it is test data and a cautionary record), but it
is removed from the active strategy set. No graveyard row from any future
sweep will be produced for it. Its prior rows are NOT_TESTED (they could
never fire), not FAIL.

This is not a close call. A strategy that fires zero times after a threshold
loosening that unblocked its binding clause 8.6x has no edge to measure.
Retirement is the honest verdict.

### R-010. Intraday off-by-one in min_test_slice_bars: DEFER (RAVEN RULING)

Cody identified an off-by-one in `min_test_slice_bars`: the intraday gate
admits a 100-bar slice, which scans from bar 100, leaving zero scannable bars.
Same defect RIVN's row exposes on the weekly side.

Ruling: defer until the next fully-fixed sweep. Zero intraday series are
anywhere near the boundary today, so it costs nothing. Fixing it mid-re-sweep
would break the intraday control (convention 17). The fix will be applied
before the next sweep starts. Not a separate D-number; it is a follow-on
from D-269/R-005.

### R-011. Re-sweep scope: let 18543 finish, do NOT promote, start new sweep (RAVEN RULING)

PID 18543 is running on pre-D-276..D-279, pre-min_bars_for, pre-slice-gate
config. Its output is stale relative to the current codebase.

Ruling: let it finish (do not kill). Keep its output as a partial-fix
baseline for convention 17 comparison. Do NOT promote it over
`v0_graveyard_full.json`. Start a new fully-fixed sweep after 18543
finishes, with the intraday off-by-one fix (R-010) and rising_three_methods
retirement (D-284) applied.

### D-285. Multi-asset shadow loop: BTC, ETH, SOL (RAVEN RULING, approves Cody proposal)

The Polymarket shadow loop now polls BTC, ETH, and SOL 5m Up/Down markets. All
three carry an identical `*-5m-twap-60` Chainlink settlement, verified live
2026-08-18, which is what makes the BTC strategies applicable unchanged. Each
asset runs its OWN strategy instances, strike proxy, and candle source, because
strategy state is per-window and per-asset (a shared PriceTape would mix BTC
64,000 and SOL 76 into one series). The bankroll, adapter, and risk gate stay
shared because the money is shared. The accounting identity becomes
`cycles * strategies_per_asset * assets`.

Exit routing is keyed on `(asset, strategy_name)`, not `strategy_name` alone,
because every asset runs an instance called `PM_fair_value_arb` and a dict keyed
on name alone would collapse to whichever was written last. A position on an
unregistered slug is counted as `unroutable_position` and left alone.

The 5bp strike noise floor (`STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0`) is INHERITED
from the BTC measurement (199 windows) and stamped `noise_floor_measured_on:
'btc'` on every gated row. It is NOT re-measured for ETH/SOL; generalising
`measure_strike_proxy.py` is the honest next step. xrp and doge markets exist
and are deliberately not wired (exchange symbols unverified).

The shadow loop (PID 27030) was NOT restarted. It still runs 11-strategy BTC-only
code (convention 13). Aym restarts when ready. CLI default is now
`--assets btc,eth,sol`.

Source: `docs/handoffs/2026-08-18-eth-sol-shadow-and-noncrypto-proposals.md`,
`engine/polymarket/assets.py`, `engine/polymarket/shadow_loop.py`,
`tests/test_polymarket_multi_asset.py` (23 tests).

### D-286. Inverse FVA deviations approved: replaced entry cap, withheld model_stop (RAVEN RULING)

`FairValueArbInverse` (PM_fair_value_arb_inverse) inverts the parent's side
selection. Two deviations from the parent were required by the inversion and
are both provably correct:

1. **Entry cap replaced.** The parent picks the side with the largest
   `fair - ask`, so the rejected side is rich by that edge plus the overround.
   A model-derived cap on the flipped side is therefore always below the
   flipped ask (measured: fair(Down) 0.29, cap 0.25, actual ask 0.42, 17c
   unreachable). Inheriting it would produce a strategy that fires exactly
   never. Replaced with a book-derived cap. Consequence: the inverse has no
   price-based entry filter of its own, but the fair-value band [0.10, 0.90] IS
   inherited and is provably symmetric about 0.5 (`p in band` implies
   `1-p in band`).

2. **`model_stop` withheld from the exit chain.** It fires when
   `fair_value <= entry + margin`. An inverse position is by definition on the
   side the model prices below the entry, so it would fire on the first poll of
   every position ever opened, closing at the spread, and the hypothesis would
   never be tested. The observed value is still recorded as
   `model_fair_value_observed_not_acted_on` so "seen and refused" stays
   countable. Consequence: 4 live exit rules vs the parent's 6, so exit
   populations are not comparable across the two.

A new gate `inverse_entry_above_profit_target_ceiling` refuses entries above
0.98, where a 1c target stops existing. Fired twice in 2 cycles.

Key finding: **inverting a loser does not flip its sign.** On live data the
parent and the inverse BOTH stopped out on the same SOL window (-1.10 and
-0.55). The spread is paid in both directions and does not invert. Measured
overround on inverse entries was 1c to 5.75c against a profit target of 1c.
Naive inverse EV is +16bps, already below convention 5's 30bps DOA floor before
costs. Aym's premise ("79% loss becomes 79% win") does not hold: the arithmetic
is `0.79*0.01 - 0.21*0.03 = +0.0016/share` = 16 bps, and the parent's 21% wins
were +1c moves that flip to -1c, inside the 3c stop, so they do not stop out.
The inverse's loss population is not the parent's win population reflected. It
is still worth shadowing (free, testable, kill condition) but should not be
expected to print money.

Source: `docs/handoffs/2026-08-18-inverse-fva-and-liquidation-strategies-live.md`,
`strategies/polymarket/fair_value_arb_inverse.py` (616 lines),
`tests/test_fair_value_arb_inverse.py` (66 tests).

### D-287. Hyperliquid feed does NOT obey HALT (RAVEN RULING)

The Hyperliquid whale-position poller (`engine/feeds/hyperliquid_client.py`)
continues recording regardless of the trading HALT state. Halting trading is not
a reason to stop recording market data. The feed writes to `hyperliquid_positions`,
a separate table that does not affect the trading loop. This is the correct
default: you want to keep collecting data even when trading is paused.

Source: `docs/handoffs/2026-08-18-hyperliquid-whale-feed.md`.

### D-288. near_liq_trigger MAX_ENTRY_PRICE tightened 0.95 to 0.60 (RAVEN RULING)

At 0.95 the entry price cap IS the break-even for a binary outcome (a losing
share is worth $0.00), so it is not a useful filter. Tightened to 0.60, which is
the conservative direction (convention 17: tightening). The vendor's value is
preserved as `VENDOR_MAX_ENTRY_PRICE = 0.95` for reference.

The vendor's second lock (arm on whale position, then require a real
liquidation print within 120s) is NOT yet wired. `liquidation_feed.py` exists as
the shared read-only reader for the `liquidations` table. Wiring it as the
second lock is the highest-value next change. Currently stamped
`second_lock_wired = False` on every row.

Source: `docs/handoffs/2026-08-18-inverse-fva-and-liquidation-strategies-live.md`,
`strategies/polymarket/near_liq_trigger.py` (871 lines),
`tests/test_near_liq_trigger.py` (41 tests).


## v10 - 2026-08-14 (D-226 duplicate_strategies superseded)

### D-266. D-226 duplicate_strategies finding superseded by post-purge analysis (Raven)

D-226 recorded that `duplicate_strategies` flagged breakout_20 and
momentum_continuation as 99.4% identical, concluding "one idea counted twice,
inflating breadth." That was accurate for the 218,295-entry graveyard it ran
against.

The post-purge 535,425-entry graveyard (55 strategies) tells a different story.
C2 pairs with all 54 other strategies at `identical_fraction` 1.0, because C2
produces zero trades in all 264 rows it is compared on. The next highest members
of the duplicate list (V2_vwap_magnet_sessionatr, V5_capitulation_equity,
V4_gap_hold_proxy, V4_trend_reclaim, rising_three_methods, rsi_extreme,
V3_intraday_momentum_crypto, V5_forced_flow_crypto) are the same eight
strategies that top the zero-trade list at 99%+.

Conclusion: `duplicate_strategies` and `trade_count_sanity` are one problem.
8 of 55 strategies do not fire. They contribute no PASS rows, so they are not
inflating the 155 distinct findings. The real cost is that 8 strategies were
never tested and are sitting in the graveyard looking like verdicts. Fixing the
firing should clear most of both assertions at once.

D-226 is left as historical record. This entry supersedes its
duplicate_strategies characterization only; D-226's other findings
(trade_count_sanity, quarantine_canary) still stand as written for the
218,295-entry graveyard.

## v9 - 2026-08-14 (Aym decisions: purge confirmed, fee model, Alpaca, paper run)

### D-261. Aym confirms the FUTURES purge (AYM RULING)
Aym confirmed: purge all contract rows. `run_post_sweep_repair.sh --confirm`
launched 2026-08-14. Drops 23,595 FUTURES/OPTIONS rows (including 51
PASS/PASS_BENCHMARK), rebuilds under current code (D-249 fix), emits judge pack.
EQUITY/ETF/CRYPTO untouched.

Actual: 23,595 rows dropped (51 PASS/PASS_BENCHMARK), per
`logs/post_sweep_repair.log`. The 12,936 written here before the run was an
estimate taken from an earlier, smaller graveyard; the live file was 535,425
entries and went to 511,830. Factual correction only, no version bump. Earlier
entries (D-259, D-254, D-249) still say 12,936 and are left as written - they
record what was believed at the time.

### D-262. Alpaca key rotation: Aym already rotated, will rotate again (AYM RULING)
Aym states he already rotated the Alpaca key since D-110 was logged. He will
rotate again. The .env file lives at `~/aym/projects/05-trading-bot/.env` and
is gitignored. Keys to store there:
- `ALPACA_API_KEY` (paper trading API key from Alpaca dashboard)
- `ALPACA_API_SECRET` (paper trading API secret)
- `ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2`
These are paper-mode market data keys only. No live trading keys exist.
D-110 closed.

### D-263. Binance.US live fee verification is RETIRED as a checklist item (AYM RULING)
The cost model now sources from `references/broker-fee-reference-2026.md`, the
verified fee reference Aym supplied. That file replaces the old "verify
Binance.US live fee page" checklist item. The cost model's `verified: False`
flag on crypto should be updated to reflect the broker-fee-reference as the
source of truth. D-236's framing still holds: backtesting measures edge outside
brokerage constraints; venue-specific verification matters for shadow testing,
not backtest cost modeling.

### D-264. Paper run and kill-switch drill deferred; focus on backtesting (AYM RULING)
Aym's directive: focus on backtesting all strategies and getting judge up to
speed. The supervised paper run and kill-switch drill are deferred until the
graveyard analysis is complete and judge is producing evidence packs regularly.
Not blocking. Not forgotten. Just not now.

### D-265. Broker fee reference is the single source of truth for costs (AYM RULING)
`references/broker-fee-reference-2026.md` is the canonical cost model input.
Any cost model code that references a different source or assumption should be
updated to read from this file. The four cost regimes (crypto percentage,
equity spread, options per-contract, futures stacked fixed) are the model.

## v8 - 2026-08-13 late evening (sweep triage, judge.py honesty fix)

### D-258. judge.py's sub-sections report their own failure too (CC)
Follow-up audit after D-255. `assertions.run_all` and
`summarize_graveyard.summarize` are called with the graveyard PATH, so they
re-read it independently of `load_graveyard` - two more chances to hit a
partial write during a live sweep. Both were wrapped in
`except: ... = None`, so a failed read silently removed a section from the
pack. `distinct_findings` comes from that summary, and convention 2 requires
citing it - which is how a pack ends up with no multiple-comparisons
correction and nothing marking its absence.

Fix: `_attempt()` retries and returns `(value, error)`, and the pack carries a
`degraded` list naming each section that could not be produced and why (None
when clean). Status is deliberately NOT downgraded - DURABLE is a statement
about harness validation, and overloading it would blur what it means. The
grep behind this found no other instances: judge.py held the only remaining
`JSONDecodeError` handlers outside the new purge tool.

### D-259. The purge tool is tested before it is ever pointed at real data (CC)
`tests/test_purge_stale_futures.py`, 15 tests. It deletes 12,936 real rows, so
the tests pin the destructive edges rather than the happy path: contract-only
scope (EQUITY/ETF/CRYPTO must survive byte-for-byte, not merely survive), dry
run writes nothing and creates no backup, the running-sweep guard refuses and
leaves the file untouched, `pgrep` failing fails CLOSED, the backup is the
pre-purge file, no `.tmp` survives, header counters are recomputed while
unrelated header fields survive, and an unreadable graveyard exits loudly
instead of looking like a successful no-op.

Verified the tool's keying assumption against the live file rather than
assuming it: all 294,294 rows carry `asset_class`, and the `_F` ticker suffix
maps 1:1 onto FUTURES (12,936 by both measures). So no ticker-based fallback
was added - it would have been speculative code in a deletion path.
Suite: 559 passed, 1 skipped.

### D-260. Post-sweep repair exists as a script but is NOT armed (CC)
`backtest/run_post_sweep_repair.sh`: waits out the sweep AND the queued chain,
dry-runs the purge into the log, applies it, rebuilds (futures under fixed
sizing plus the v4/v5 backfill in one pass), then emits a judge pack. Aborts
before the rebuild if the purge fails, leaving the graveyard unchanged or
restorable from the archive.

Unlike `run_queued_chain.sh` it does not launch itself and requires an
explicit `--confirm`, because step 2 is a deletion Aym owes a call on. It also
waits for the CHAIN, not just the sweep - purging underneath the chain would
delete rows its dispersion/horizon/PLR stages are mid-way through using. Uses
`env -u PYTHONPATH python3` throughout per D-257.

Note for whoever edits it: do NOT edit `run_queued_chain.sh` while PID 69639
is executing it. Bash reads a script by byte offset as it runs, so editing a
live script can make it execute garbage. That is why the repair sequence is a
new file rather than three lines appended to the chain.

### D-253. The running graveyard sweep is stale-code; let it finish anyway (CC)
`run_incremental_graveyard.py` (PID 63767) started 16:01 and Python snapshots
source at import, so the 6h+ run in flight is executing 16:01 code. It
therefore predates: v4 (16:19), v5 (16:45), the runner's own edit (16:47),
and - the one that matters - the D-249 sizing fix in `cost_model.py` (17:45).
Confirmed, not inferred: the log reports "49 new strategies" per series and
the graveyard header says `strategies_tested: 49`, which is exactly
28 expanded + 7 lab + 9 v2 + 5 v3, with v4's 3 and v5's 2 absent (54 today).

Decision: do NOT kill it. Only FUTURES/OPTIONS rows are affected by D-249
(`InstrumentSpec.is_contract` is true for those two alone), and those are
12,936 of 287,826 rows. The other ~96% - EQUITY, ETF, CRYPTO - never enter
the contract-sizing path and are good work. Killing the sweep would discard
them to fix a bucket that has to be re-run either way. The missing v4/v5
rows are benign: the queued chain's incremental pass backfills them under
current code.

### D-254. Purge all contract rows rather than date-splitting them (CC)
`backtest/purge_stale_futures.py`. The D-249 fix landed WITHOUT a
`COST_MODEL_VERSION` bump - the tag is '2026-08-13' on both sides of it, and
all 287,826 rows carry that single value. So the "never pool across
cost_model_version" convention cannot separate pre-fix from post-fix rows,
and nothing else in the row metadata can either.

Rather than invent a distinction the data does not support, the tool drops
every FUTURES/OPTIONS row and lets the incremental runner rebuild them under
current code. Blunt, but correct whichever side of the fix a row came from.
EQUITY/ETF/CRYPTO are explicitly left alone. No version bump either: after
the purge every surviving contract row is post-fix by construction, and a
bump would falsely mark ~275k unaffected rows as stale.

Guard: the tool refuses to run while `run_incremental_graveyard.py` is alive,
because that runner holds the whole graveyard in memory and rewrites it after
every ticker - a purge landing mid-sweep is clobbered on the next save.
Writes are atomic (`os.replace`) and back up to `research/graveyard/archive/`
first. NOT YET RUN - it is armed for after the sweep and chain finish.

### D-255. judge.py must not report unreadable evidence as no evidence (CC)
Found by running it: `judge.py` emitted `status: DURABLE, entries: 0` against
a 287k-entry graveyard. `load_graveyard` caught `json.JSONDecodeError` and
returned `[]`, so a read landing mid-`json.dump` (the sweep rewrites 12MB
after every ticker) was laundered into a confident empty pack. That is
exactly the conflation convention 11 bans for verdicts - "could not run" is
never "ran and found nothing" - applied to the evidence layer instead.

Fix: `load_graveyard` retries (the partial-write window is short), then
raises `GraveyardUnreadable` rather than lying; `build_evidence_pack` catches
it and returns `status: UNREADABLE`, which no green harness can upgrade to
DURABLE. Missing/genuinely-empty files still return `[]` as before. The old
test asserted the buggy behaviour and was replaced by three: raises-on-
malformed, retries-then-succeeds, and never-durable-when-unreadable.
Suite green: 543 passed, 1 skipped, 544 collected.

### D-256. The constraint sweep does not show that selectivity creates edge (CC)
The script's own DIAGNOSTIC line ("Tightening the gate IMPROVES per-trade PnL
by +1.7174... selecting for something real") overstates its result on two
counts, both visible in `research/graveyard/constraint_sweep.json`:

1. It is not monotonic. AGGRESSIVE -$0.1793/trade, BASE -$0.4543,
   CONSERVATIVE +$1.5380. Tightening AGGRESSIVE->BASE makes it *worse*. A
   real selectivity effect reads AGGR < BASE < CONS; this is U-shaped, and
   the headline number is just the two endpoints subtracted.
2. It is two strategies. dca_7 (195 trades) and dca_14 (87 trades) are 5.6%
   of CONSERVATIVE's trades and 78.5% of its profit. Drop them and $/trade
   falls +1.5380 -> +0.3502. The rest of the tail is noise by convention 7:
   V2_expiry_pin is 15 trades at $59.51, rsi_extreme is 3 trades.

CONSERVATIVE fires 5,045 trades against AGGRESSIVE's 836,072 - 165.7x
selectivity for a bucket thin enough that a couple of DCA variants set its
sign. Recorded as NOT SUPPORTED, not as a negative result: the honest read is
that the sweep is underpowered at the conservative end, not that selectivity
is disproven. `research/2026-08-13-constraint-sensitivity-PRELIMINARY.md`
should stay PRELIMINARY.

### D-257. PYTHONPATH leaks from Hermes into spawned Cody sessions (CC, ops)
`python3` in this session failed to import numpy: `PYTHONPATH` was set to
Hermes's venv, putting `.../venv/lib/python3.11/site-packages` (numpy 2.4.6,
cpython-311 binaries) ahead of the correct 3.9 site-packages (numpy 2.0.2).
The machine is fine - it is in no shell rc file, and `env -u PYTHONPATH
python3` imports cleanly - and the 16:01 sweep and the queued chain are
unaffected because they were started from clean shells.

It matters because a Raven-spawned headless Cody hits the same contaminated
environment and its runs die on an ImportError that looks like a broken
numpy install. Any python3 invoked from an agent-spawned session should be
`env -u PYTHONPATH python3`. Not fixed in-repo: this is an environment
artifact, not a project bug, and hard-coding a workaround into the scripts
would hide it.

## v7 - 2026-08-13 evening (governance formalized, judge.py + sweep-loop batch)

### D-249. Futures/options contract sizing actually wired into the cost model (CC)
Real bug was one layer deeper than ROADMAP P0.4's "STILL TO DO" implied:
`TradeCoster` (D-235) already imported `instruments.spec_for` and computed
margin as capital-at-risk correctly, but `contracts` was a hardcoded
default of 1 that the constructor then floored to a minimum of 1
(`max(int(contracts), 1)`), so `instruments.InstrumentSpec.size_for`'s
affordability check was imported but structurally unreachable - futures
always traded exactly one contract regardless of whether the configured
notional cap could afford the margin. `tests/test_cost_model.py`'s own
`test_harness_futures_trade_one_contract_on_margin` pinned this as
"correct" at the harness's $100 default, i.e. the test encoded the bug.
Fixed at the shared `TradeCoster.__init__`/`CostModel.coster()` layer:
`contracts` now defaults to `None`, meaning "size it honestly via
`spec.size_for(notional_cap, price)`," floored at 0 (not 1) so
unaffordable really means unaffordable and feeds the harness's existing
`qty <= 0 -> skip` path. Fixing it at this shared layer means
`vectorized_harness.py`, `harness.py` (event engine), and
`cross_sectional.py` all inherit the fix without three separate patches.
Split the old test into two: unaffordable-at-$100 (produces no trades) and
affordable-at-$2,000 (ROADMAP's own example: one MES contract, $1,800
margin) - both pass. Also added `pooled_analysis.pool()`'s default
exclusion of FUTURES/OPTIONS from dollar-pooled cells (they were never
excluded there, only in `cross_sectional.py`), matching the "denominators
are different quantities" rule P0.4 states; `asset_class_analysis.py`
remains where those rows are actually analyzed, keyed by class. Full test
suite green (details in the matching handoff). Existing graveyard futures
rows were produced under the old bug and need a targeted re-run once the
live sweeps finish - flagged in ROADMAP P0.4, not done here since deleting
graveyard entries to force a re-test is a deliberate call, not a silent
side effect of a code fix.

### D-248. C2 WeekendVacuumReversion: fixed the wrong-question NOT_TESTED gate (CC)
`strategy_lab.py`'s C2 needs 840 bars; `vectorized_harness.SCAN_WINDOW` is a
flat 260 shared by all 35 strategies for O(1)-per-bar scan cost. The old
gate rejected ANY strategy with `min_bars > SCAN_WINDOW` outright as
NOT_TESTED, even on a series with thousands of bars available - it was
answering "does this strategy fit in the shared window" instead of "could
this run at all." Fix: `scan_all_bars` now hands each strategy its OWN
window, `max(SCAN_WINDOW, strategy.min_bars)`, so C2 gets 840 bars while
the other 34 strategies' cost is unchanged; `run_sweep`'s NOT_TESTED check
now asks whether THIS series has enough bars (`ind.n < min_bars`) rather
than comparing against the global window constant, with
`MAX_STRATEGY_WINDOW = 2000` kept as a defensive ceiling against a
hypothetical strategy asking for something unreasonable. A strategy that
clears both checks but still fires zero signals now correctly reports
FAIL/0-trades like any other strategy, not NOT_TESTED - NOT_TESTED means
"could not run," never "ran and found nothing" (D-109). Two existing tests
that pinned the old blanket-rejection behavior updated; one new test proves
the window actually widens (`max(windows_seen) > SCAN_WINDOW`) without
depending on synthetic data that happens to satisfy C2's firing gates - a
separate, larger fixture-engineering problem this pass does not attempt.
C2's powered result rides the graveyard's next natural incremental pass;
no separate run triggered here.

### D-247. agents/judge.py built: Judge-as-code, wraps existing modules (CC)
Per `docs/AGENT-RUNTIME-PROPOSAL.md`'s "Judge is ~80% deterministic
already" framing and `agents/judge/SOUL.md`'s requirements. New file, no
backtest logic reimplemented - composes `validate_harness.main()`,
`assertions.run_all()`, `pooled_analysis.per_strategy_summary()`,
`asset_class_analysis.analyze()`, and `summarize_graveyard.summarize()`
into one evidence-pack JSON. Per-strategy rows carry n_trades, twin
PERCENTILE (never a single-draw comparison, per D-219), an approximate
win-rate CI and z-test against the 50% null (labeled approximate - Wald
normal, not exact binomial), and the SOUL's cold_start
(n<30)/reviewable_not_promotable (30<=n<50)/evaluable (n>=50) confidence
labels. NOT_TESTED rows are never converted to a failure. Every row is
stamped PROVISIONAL whenever `validate_harness.py` is red - the flag
cannot be silently dropped downstream. `expected_best_by_chance` reuses
`summarize_graveyard.py`'s existing multiple-comparisons numbers rather
than reimplementing a correction; correcting on hypotheses GENERATED
(Forge's search log, the SOUL's harder requirement) is left an explicit
`not available` since no module in the codebase tracks that count yet -
noted as a TODO, not silently faked. Read-only: writes only its own pack
under a caller-given path, never touches graveyard files or the DB.
`tests/test_judge.py`, 17 tests, all green. Callable as
`python agents/judge.py --graveyard PATH --out PATH [--strategy NAME]`.

### D-246. Lab v4 DEEP RENT ignitions: already built, verified, and formally logged (CC)
`strategies/builtin/strategy_lab_v4.py` (317 lines) implements all three
IGNITIONS from `references/strategy-lab-v4.md` as SHARE strategies per
`docs/STRATEGY-LAB-V3-V4-ASSESSMENT.md`'s recommendation: `V4_gap_hold_proxy`
(I1, PEAD - price-only proxy, no earnings calendar exists so this detects
the gap-and-hold signature only, not SUE proper), `V4_52w_high_breakout`
(I2, George & Hwang anchoring effect, weekly bars so the 52-week lookback
fits inside the harness's test-slice length), `V4_trend_reclaim` (I3, 100-day
MA reclaim + non-negative 12-1 momentum, weekly bars). 17 tests
(`tests/test_strategy_lab_v4.py`), all green, and already wired into
`run_incremental_graveyard.py`'s `ALL_STRATEGIES`. This was built earlier
today (file predates the D-235-D-242 parallel build) but never got a
decision entry, and ROADMAP.md still described it as "being built" - both
gaps closed by this entry and the matching ROADMAP.md edit. No new code;
this is a documentation-debt fix. Powered results are riding the same
graveyard sweep already running (PID 63767) - no separate run needed.

### D-245. Option 2 (Agent SDK runner) stays the long-term migration target (Aym/Raven, formalized by CC)
Per `docs/agent-proposal-reconciliation.md` Q2: the joint debrief's "Hermes
cron triggers a scoped Claude Code subagent" is Option 1 refined, not a
replacement for Option 2. Written down explicitly so it doesn't read as
decided-by-default: **Hermes-cron-triggers-subagent is the near-term shape.
Option 2 (a standalone `agents/runtime.py` on the Agent SDK, launchd-
scheduled, no dependency on any interactive session) stays the long-term
target if/when volume or reliability demands it** - i.e. once the loop is
running daily/weekly and either needs more concurrency than one subagent
session gives, or needs to survive without Hermes as the trigger. Nobody
migrates preemptively; this just keeps the option live instead of letting
it evaporate by omission.

### D-244. D-217 ratified: SPEC-5.7 interim + Aym ratifies the 11 rules directly (Aym/Raven, formalized by CC)
Per `docs/agent-proposal-reconciliation.md` Q4, combining the runtime
proposal's options (a) and (b) rather than picking one: **agents run under
SPEC-5.7 rules only until ratification (interim safety - no agent is
blocked waiting on this), and Aym ratifies the 11 SOUL rules named in D-217
directly rather than waiting on a separate Raven ruling** (permanent
resolution - they are mostly extra honesty requirements, and the one
substantive conflict D-217 flagged, the twin-methodology mismatch between
Quant's old single-draw SOUL text and the new percentile rule, is already
resolved in code by D-219's percentile gate). Effect: `agents/quant/SOUL.md`
should be updated to match D-219's percentile language (the one piece of
D-217 that was a real conflict, not just an addition) the next time it's
touched; the other 10 rules across scout/forge/judge/coach/echo stand as
drafted. None of this is load-bearing yet since only Quant's SOUL is
active per SPEC 5.7's 5-live-strategies split trigger.

### D-243. Governance carve-out: trading-bot agents run standalone, Raven audits monthly (Aym/Raven, formalized by CC)
Per `docs/AGENT-RUNTIME-PROPOSAL.md` section "Governance" and
`docs/agent-proposal-reconciliation.md` Q1: the global stack rules make
Hermes/Raven the standing review layer for Claude Code sessions generally.
This project's agents (Quant now, the 5-way split later) do not depend on
either at runtime - the safety boundary is deterministic code (sandbox,
registry inbox, gate checks, validate_harness, kill switch, Aym-only
promotion), not AI review. Written down explicitly so the two rule systems
don't silently conflict: **trading-bot agents run standalone; Raven audits
monthly, not per-decision.** Audit cadence mechanism (also from the
proposal, not yet built): every agent action already lands in the existing
audit log; a weekly Echo digest and monthly SOUL drift-check cron are
queued behind Echo/judge.py existing to produce something to digest.

## v6 - 2026-08-13 evening (parallel agent build: SPEC 5.8 + Lab v5 P2/P3/P5)

Four subagents built four disjoint-file lanes in parallel; every lane passed
its own tests plus the full suite (475 fast + 42 harness = 517, up from 355
this morning), and each wrote its own handoff in docs/handoffs/.

### D-241. Cross-sectional harness BUILT (SPEC 5.8) (CC agent)
`backtest/cross_sectional.py` + 29 tests. Panel with union-grid alignment
(no forward fill), PanelView that structurally denies rankers the decision
bar (one bar MORE conservative than the time-series harness), TradeCoster
costs, time-matched twins that replay the strategy's own formation steps,
per-cell + pooled + leave-one-asset-out + calendar-half split, fires-check
before P&L. The load-bearing test is a lookahead oracle: a cheating ranker
that would earn +20%/trade on a leak earns -8% and produces bit-identical
trades to an honest lagged ranker. Implements v3 #3 Same-Clock Echo (30m
slots from 15m bars, DST-correct), v3 #5 Paid Liquidity Reversal (VIX
context gate; news exclusion stamped OMITTED_NO_EARNINGS_CALENDAR), and v5
P1 Horizon Ladder (`backtest/run_horizon_ladder.py`, pre-registration in
docstring before any result existed). Smoke runs prove machinery only;
powered runs queued behind the graveyard. Futures excluded from pooled
dollar cells (contract dollars would outvote spot clips).

### D-240. Lab v5 P2 Toll Collector: kill condition NOT fired; prediction refuted in direction (CC agent)
`backtest/toll_collector.py` + 13 tests + research/graveyard/toll_collector.json.
Conservative maker-fill simulator (fills ONLY on trade-through, low <
limit - 1 tick; every ambiguity resolved against the maker hypothesis).
12 months x 3 pairs x 1h: armed 31.8%, 1,556 episodes, 516 maker fills
(33.2%), taker-stop rate 39%.
- Maker beats identical taker-at-touch by 11.2bps vs fee+slip savings of
  12.6bps -> measured adverse selection ~1.3bps. Kill condition (adverse
  selection eats the discount) did NOT fire. Passive execution survives AS
  AN EXECUTION-COST STORY.
- Pre-registered prediction (edge concentrates in top vol band) REFUTED in
  direction: >=90th pctile fills net -7.8bps, 70-90 band +7.0bps. Inverted,
  both t < 1.
- Strategy P&L itself: +2.5bps/trade, t=0.31, n=516 - a shrug, and the
  margin sits inside the UNVERIFIED 0%-maker fee assumption.

### D-239. Lab v5 P5 Forced-Flow strategies built and registered; sample far below power bar (CC agent)
`strategies/builtin/strategy_lab_v5.py` (V5_forced_flow_crypto,
V5_capitulation_equity) + 21 tests. Registered (55 strategies total); the
armed incremental pass sweeps them automatically. Fires verified: ~5 crypto
events (Yahoo 1h slices) + 3 equity capitulations across 178 tickers -
FAR below P5's 400-800 power bar, so the graveyard's verdict on these
cohorts is a shrug until more event-years exist (standing rule 7). Caveats
stamped everywhere: funding table is one strange regime-year; no earnings
exclusion exists yet. The tasking's proposed crypto/equity discriminator
(funding-date matching) was checked and found wrong; replaced with a
24/7-tape gate + mirror gate so the two cohorts stay disjoint for the
mechanism-coherence kill condition. No P&L was read during parameter
fixing.

### D-238. Lab v5 P3 Dispersion Gate built; thresholds now PER-CLASS (CC agent)
`backtest/dispersion_gate.py` + 20 tests. Derived gate ATR_hold >= c/kappa
(kappa=0.10 pre-registered), with c taken from the SAME TradeCoster the
harness charges - the v5 doc assumed a flat 14bps; venue-accurate c makes
the equity gate ~3.3x sharper (0.421% vs 1.4%) and futures ~10x sharper.
Entries: grid_2.0atr, stoch_rsi_oversold, dca_7 control; time exits only;
expanding-window vol deciles (pre-entry data only); calendar-midpoint
holdout judged on H2; LOO. Smoke run (16 series) proves mechanics; the
gated-out fractions order by class exactly as the thresholds predict. One
defect caught in smoke: MES contract dollars pooled with $100 spot clips
(the P0.4 denominator sin) - cross-class dollar pooling is now SPOT-only.
Full 1-3h run queued behind the graveyard.

### D-242. Standalone agent-runtime proposal written (needs AYM)
docs/AGENT-RUNTIME-PROPOSAL.md. Answer to Aym's question: the bot's agents
CAN run independent of Hermes/Raven - nothing in any code path depends on
them; enforcement is the deterministic machinery (sandbox, registry inbox,
gates, validate_harness, kill switch, Aym-only promotion). Proposal: code
where possible (Judge/Echo are ~80% deterministic already), model where
necessary (Forge/Scout), Coach writes recommendations only. Three decisions
are Aym's: D-217 ratification path, an explicit Hermes/Raven carve-out
statement, and the audit cadence that replaces per-decision review.

## v5 - 2026-08-13 (cost model wired into both harnesses)

### D-237. Lab v5 P4 "Fingerprint Router" is DEAD - killed by its own precheck (CC)
`backtest/vr_fingerprint.py` implements P4's pre-registered kill condition (a)
from `references/strategy-lab-v5.md` (doc saved to references/ today - the
ROADMAP had summarized it but the full text was not in the repo):

> "trait instability - cross-half Spearman correlation of instrument VRs
> < 0.3 => the fingerprint is weather, not character, and routing dies
> before P&L is even read."

**Measured on 175 instruments** (daily bars, >= 200 bars per calendar half):
- VR(5,1) cross-half Spearman: **-0.213**
- VR(20,5) cross-half Spearman: **+0.072**

Both far below the 0.3 bar; VR(5,1) is mildly ANTI-correlated - an
instrument that looked mean-reverting in its first half was slightly more
likely to look trending in its second. The Lo-MacKinlay variance ratio, at
these sample lengths and this universe, is regime weather, not instrument
character. **No routed backtest was run, and none should be** - running one
anyway and reading its P&L would be exactly the winner-filtering the
proposal's no-trickery clause forbids.

This is the cheapest kind of result the lab produces: a proposal resolved
with ~zero compute because the kill condition was designed to fire before
the expensive part. Math pinned by tests/test_vr_fingerprint.py (6 tests).
Full per-instrument table: research/graveyard/vr_fingerprint.json.

Consequence for SPEC 5.8: the cross-sectional harness loses one of its four
named consumers (P4). It keeps three (v3 #3, v3 #5, v5 P1) and the context-
series capability, so its priority stands.

### D-236. Fee reference confirmed as-is; venue verification demoted to shadow-test gate (Aym)
Aym re-supplied the broker fee reference; it is byte-identical to
`references/broker-fee-reference-2026.md`, the source of cost model version
2026-08-13 already wired and running. No rate changes.

Aym's ruling: backtesting measures edge outside brokerage constraints;
venue-specific verification (Binance.US live fee page, checklist item 1)
matters when shadow testing starts, not before. ROADMAP P0.1 reframed
accordingly. `CostModel.describe()` keeps `verified: False` on crypto until
a live account confirms - the flag describes evidence, not urgency.

### D-235. Cost model + instrument specs wired; opt-in, version-stamped, pool-guarded (CC)
Implements the agreed next action from the 2026-08-12/13 handover (P0.2).

**What changed:**
- `TradeCoster` (backtest/cost_model.py): binds the four-regime CostModel to
  one instrument, exposing exactly what a harness loop needs - a slip rate
  that moves the FILL (and therefore the stop distance), and a dollar
  `leg_fee` that comes off PnL. Keeping those separate is the point: a model
  that subtracts spread at the end reports a risk plan nobody traded.
- `FlatCoster`: the legacy single-rate model behind the same interface, so
  the loop has ONE code path. Its version string encodes its rates.
- Both harnesses take `use_cost_model: true` in config. OFF BY DEFAULT and
  off means bit-identical to the old flat model - required by the
  cross-harness referee (external engines are configured with one commission
  rate) and by the zero-cost validation probes. `fee_override`/
  `slippage_override` always force flat semantics for the same reason.
- FUTURES size in WHOLE CONTRACTS (standard remapped to micros, ES->MES),
  margin is capital-at-risk, PnL is in contract dollars (multiplier), and
  `return_pct` is now return on capital at risk everywhere. For spot,
  capital at risk == notional, so spot numbers do not move.
- Every report row is stamped `cost_model_version` + `asset_class` +
  `instrument`. NOT_TESTED rows too.
- New silent assertion `cost_model_version_uniform`: entries with mixed
  stamps (including pre-stamp 'unstamped') cannot be read as one dataset.
  This is what makes the opt-in safe: a run that forgets the flag cannot be
  pooled with a run that used it.
- Twin caches key on the coster's full identity, so a twin computed under
  one cost regime can never answer for another.
- `resolve_asset_class` moved to backtest/instruments.py as the single
  sector->class mapping; asset_class_analysis.py now imports it.

**Guard rails that held:** 401 tests pass (4 new wiring regressions pin
equity-cheaper-than-flat, one-contract futures on margin, override-forces-
flat, flat-stamping). validate_harness.py 21/21, exit 0. Cross-harness
referee AGREES on both AAPL and BTC_USD.

**Measured round trips on a $100 clip (per coster.describe()):** crypto core
12bps, crypto other 14bps, equity/ETF 4.2bps, MES futures 1.3bps of exposure
($2.06 on one contract, $1,800 margin at risk, ~$34k exposure).

**Still true:** Binance.US rates remain UNVERIFIED (Aym checklist item 1);
equity half-spread 1.5bps/leg is a liquid-large-cap default, not per-
instrument; futures slip is one tick per side, an assumption.

## v4 - 2026-08-13 (inversion implemented + tested, agent SOULs, benchmark honesty)

### D-234. Constraint sensitivity sweep: aggressive vs base vs conservative (CC, Aym's ask)
`backtest/constraint_sweep.py`. Aym asked for an AGGRESSIVE (loose, fires
often) and CONSERVATIVE (harsh, rare) variant per strategy to see whether
performance changes.

**Framed as a DIAGNOSTIC, not an optimization.** "Which level scores best,
keep the winner" is parameter search on seen results and manufactures edge
exactly like subset-filtering (D-233). The falsifiable question is about
mechanism instead:
- If a strategy has real edge, tightening the gate should RAISE PnL per trade
  while lowering trade count. Fewer, better trades.
- If the gate carries no information, tightening only shrinks the sample.
  Per-trade PnL stays flat at the cost floor.

**The shape of the curve is the finding, independent of which level wins.**
A flat curve across a large change in selectivity proves the confirmation
stack is not selecting for anything.

Knob choice: the harness's own confirmation stack (regime filter, RSI ceiling,
volume ratio), NOT strategy-internal thresholds. This applies identical
tightening to all 44 strategies without editing 44 files and keeps the
comparison honest. Strategy-internal parameter tuning stays Forge's territory
(SPEC 5.5 edit ladder), deliberately untouched.

Levels: AGGRESSIVE (no stack at all), BASE (RSI 70 / volume 1.2 / regime),
CONSERVATIVE (RSI 45 / volume 2.0 / regime). 14 series spanning equities,
ETFs, crypto and futures across 5m/1h/1d, 3 exit configs.

RESULTS PENDING - the sweep is compute-bound (the ta-backed indicators are
~19x slower per call than the hand-rolled ones, the price paid for D-201's
correctness). Optimized to scan once per (series, strategy) and reuse across
all three levels, since signals do not depend on the confirmation stack.

### D-232. Asset-class pooling + leave-one-asset-out guard (CC, Aym's ask)
`backtest/asset_class_analysis.py`. Rare patterns are generic, not
ticker-specific, so the unit is the pattern. Pooling by asset class made
**105 of 128 strategy-class cells judgeable** where per-ticker almost none
were. Verdict unchanged: every judgeable cell negative, clustered at the
-$0.30 cost floor, and the cross-class SPREAD is tiny (0.06-0.36) - the
strategies are not class-specific, they are uniformly at zero. That is
evidence against the simple form of the fingerprinting thesis.

The study's best cell (`bullish_harami` CRYPTO, -0.036/trade over 162 trades)
turned out to be **one asset in a costume**: -0.417 without SOL, worse than
the floor, with a single 9-trade cell producing +2.534/trade. Added an
automatic **leave-one-asset-out** check flagging any cell that moves >0.15
when its top underlying is removed, grouping by UNDERLYING so BTC/USDT and
BTC_USD are one asset. Three cells flagged.

### D-233. Conditional edge tested properly; filtering to winners yields NOTHING (CC)
Aym asked to slice by sector/class/condition and keep the subsets where
patterns win. The naive version of that is the textbook selection-bias
failure, so `backtest/conditional_edge.py` does it the only valid way:
SELECT winning cells on half the underlyings, JUDGE those exact cells on the
other half, 20 random splits.

Result across all five condition slices (class, sector, timeframe,
class x timeframe, class x exit): **survival 53.5%-58.7%, i.e. a coin flip.**
The decisive number: cells that WON on the selection half average
**-$0.302/trade on unseen instruments, against a -$0.30 cost floor.**
Selecting winners bought exactly zero.

Caveat recorded: `sector` shows 58.2%, marginally above chance, but it has
the most slices (most chances at noise) AND a ticker-split does not break
sector correlation, so it is not a clean holdout for a sector hypothesis.
A time-based holdout would be needed, and is only worth running once
something shows positive GROSS edge.

The tooling is permanent: the moment any strategy shows gross edge above
costs, one command tests whether it is real or a subset artifact.
Writeup: `research/2026-08-13-conditional-edge-finding.md`.

### D-231. Strategy Lab v2: 9 strategies built, wired, and running (CC)
`strategies/builtin/strategy_lab_v2.py` + 120 tests. Implemented the 8 that
run on data we have: Wick Autopsy, Round-Number Defense Decay, Liquidation
Echo, Second-Break Verdict, Volume Desert Breakout, VWAP Magnet Close, Expiry
Pin Drift, 0DTE Afternoon Amplifier. All verified ALIVE on real data (signal
counts per file recorded in the handoff), which caught two dead-on-arrival
bugs before they reached the graveyard:
- Second-Break fired 0 times on AAPL: the spec's "OR height 0.5-2.0 ATR"
  means SESSION-scale ATR but the harness passes BAR ATR (opening ranges run
  2.4-6.4x the 5m ATR). Added sqrt-of-time rescaling. 0 -> 225 signals.
- Liquidation Echo's swing-low detector matched flat tape (non-strict
  minimum), so cascades trivially "held above" a pool that never existed.

**VWAP ATR-scale judgement call resolved by testing both, not by choosing.**
Bar scale fires ~35% of ticker-days (matching the spec's own estimate) with
targets near the fee floor; session scale fires ~1.5% with targets that clear
costs. Registered as `V2_vwap_magnet` and `V2_vwap_magnet_sessionatr` so the
graveyard settles it empirically. 9 strategies total.

Grid is now **44 strategies x 11 exit configs x 885 test sets = 330,410
backtests**, running with time-matched twins (D-230) so the six clock-anchored
v2 strategies cannot win on the clock.

Known: Wick Autopsy (~1 signal per 4,000 bars) and Liquidation Echo will fall
under the 20-trade per-ticker gate and need POOLED analysis (D-227) to be
judgeable at all.

### D-230. Strategy Lab v2 filed; its harness warning was correct and is fixed (CC)
`references/strategy-lab-v2.md` (18 unorthodox cross-asset day-trading
hypotheses in genome format, with theses and kill conditions).

The doc raises what it calls silent assertion #15, and it is RIGHT: several
strategies are clock-anchored (opening range, lunch desert, 15:30 VWAP
magnet, expiry afternoon), and the random twin drew entries uniformly across
the session. Comparing a 15:30-only strategy against an all-day twin credits
the CLOCK, not the signal - a false-positive generator for exactly the
strategies this doc adds.

**Fixed:** `_time_bucket_key` detects clock anchoring from a strategy's
actual signal times (returns None when signals spread across the session, or
on daily/weekly bars where one bar-time is meaningless), and the twin then
draws ONLY from matching minutes of day. Guard: needs >=20 matched bars
before restricting, else falls back to unrestricted. Twin cache key now
includes the buckets. 4 tests, including one on a series engineered so one
minute of day behaves differently - if time-matching were decorative that
test would fail.

Implementation of the strategies themselves is in progress; only the subset
runnable on existing data (pure OHLCV + the funding CSVs already present) is
being built. Blocked on missing data: premarket bars (2.2), economic
calendar (3.2), session/settlement tags (3.1, 3.3), surprise scanner (2.7).

### D-228. Plan confirmed against SPEC (Aym asked; verified, not assumed)
Aym asked whether the plan is: no live trading until he says so, build the
graveyard foundation by testing all strategies + exit configs, THEN build the
agents, THEN test agent enhancements. Verified against SPEC:
- **No live trading**: SPEC section 2 verbatim. Also enforced in CODE -
  `engine/main.py` refuses any mode except paper regardless of config.
- **Graveyard before agents**: SPEC's own "In scope (backtest expansion -
  now)" says "build the graveyard foundation before agents exist", and the
  T-order puts Quant at T13/T14 after the T7 backtest moment of truth.
- **Version confusion resolved**: V1/V2/V3/V4 scope LIVE EXECUTION only.
  Backtesting futures/options/equities now does NOT violate "futures is V3".
  Raven did not err; they are two different axes.
Flagged for the agent build: Forge must NOT inherit "35 strategies failed."
The accurate framing is "generic pattern trading shows zero gross edge;
instrument-strategy pairing (v2 fingerprinting) is untested and is the
promising direction." The difference decides whether Forge wastes months.

### D-229. Exit-signal strategies finally wired into the sweep (CC)
Two new exit configs: `signal_exit` (stop + bearish-pattern exit) and
`signal_exit_2r` (stop + pattern exit + 2R target), putting the 14
EXIT_STRATEGIES_EXPANDED into the graveyard for the first time. Exits fill at
the NEXT bar's open (a pattern is only known once its candle closes, SPEC 5.1
#6). Bearish-exit bars are computed once per series and cached, since they
depend only on price. Grid is now 11 exit configs.
Verified AAPL 1h: 1,261 exit bars, 588 signal exits across 1,107 trades,
stops still honored. Result: -$0.299/trade, no better than mechanical exits -
another independent confirmation of D-227.
Also: the runner's silent `continue` for short series now emits NOT_TESTED
rows with a reason (D-223 closed), and weekly is in the grid (885 test sets,
up from ~693).

### D-226. Silent assertions (result-quality set) built and immediately paid off (CC)
`backtest/assertions.py`: quarantine canary, mirror-pair contradiction,
win-rate ceiling, trade-count sanity, duplicate-strategy detector, timeframe
coherence, gate-version uniformity. These are the RETROACTIVE set (read
stored rows) that the validation review separated from the harness-validity
set already in validate_harness.py. Per review section 9 they are NOT
disclosed to Forge/Quant.

First run over 218,295 entries failed three:
- **trade_count_sanity**: 17 strategies produce zero trades on >60% of
  tickers (rising_three_methods and rsi_extreme: 99%).
- **duplicate_strategies**: breakout_20 and momentum_continuation have 99.4%
  identical trade counts - one idea counted twice, inflating breadth.
- **quarantine_canary**: 63 SNDL rows above PF 1.3, all on 2-3 trades
  (small-sample noise; the assertion needs a min-trade floor to be useful).

### D-227. THE v0 VERDICT: 33 of 35 strategies lose exactly the trading cost (CC)
The assertions exposed that per-ticker verdicts were structurally impossible
for rare patterns: **13 of 35 strategies never once reached the 20-trade floor
in 212,058 runs**; morning_star and piercing_line never exceeded 6 and 8
trades in ANY run. All were recorded FAIL, which reads as "does not work"
when the truth was "cannot be measured."

Fix: `backtest/pooled_analysis.py` aggregates across tickers (a pattern's
edge belongs to the pattern, not to AAPL). 33 of 35 became judgeable.

Pooled result: **every strategy lands at -$0.25 to -$0.35 PnL per trade
against a $0.30 round-trip cost.** Gross edge is indistinguishable from zero
across the entire v0 library - they are not predicting badly, they are not
predicting. Independently matches the inversion study's -$0.31 per exit.

Consequences: the SPEC 5.3 150-trade bar should apply to POOLED counts;
"insufficient data" must be a verdict distinct from FAIL at strategy level;
and Forge must never be told "35 strategies failed" (the honest statement is
33 measured at zero edge, 2 unmeasurable). Writeup:
`research/2026-08-13-v0-verdict.md`.

### D-225. "Skip expensive contracts" is a risk AMPLIFIER, not risk management (CC)
Aym proposed extracting the small account's forced selectivity as a
deliberate rule (it declined unaffordable trades, which felt like risk
management). Tested properly on **4,633 option trades, 25 tickers**, by
bucketing one unfiltered population by entry premium rather than comparing
divergent filtered runs.

Result inverts the hypothesis. Cheap contracts (bucket 1) have the highest
MEAN (+23%) but the worst MEDIAN (-16%), worst win rate (46%), and nearly
DOUBLE the variance (163% vs 89%) of the priciest bucket, whose median is
-0.7% with a 49.7% win rate. Cheap options are lottery tickets; the mean is
carried by rare winners. Risk-adjusted returns are roughly flat across
buckets - what changes is distribution SHAPE.

So affordability constraints push a small account into the highest-variance,
worst-median part of the distribution: the opposite of protecting the
balance. The earlier "3 of 4 skipped trades were losers" was n=4; at n=4,633
the effect reverses.

**Corrected extractable rule:** for survival, prefer HIGHER premium (closer
to the money), not lower. This compounds with D-222: cheap contracts are also
worst on commission drag (39% vs 1.6% of premium). Cheap = worse fees + worse
median + double variance. The SPEC's v2+ WSB/tail-risk ideas live in bucket 1
and must be judged on tail behavior and bankroll survival, never mean return.
Tool: `backtest/premium_filter_study.py`. Writeup:
`research/2026-08-13-premium-selectivity-finding.md`.

### D-224. Balance sweep: spot invariant, options size-sensitive for 3 reasons (CC)
Aym asked to disregard the $100 cap and sweep account balances.
`backtest/balance_sweep.py`, $500 to $100,000:

- **Spot: exactly invariant.** PF/win-rate/return% identical at every balance
  (dollars scaled 200x, quality metrics did not move). Position size is a
  RISK decision only; it has zero effect on whether an edge exists.
- **Options: size matters, but not via the fee rate.** commission/premium =
  (2 x fee x contracts)/(premium x 100 x contracts) - contract count CANCELS,
  so the ratio is set by PREMIUM PER CONTRACT (strike/expiry), not balance
  (0.58% -> 0.51% across a 200x range). The three real effects are: order
  minimums binding only at the bottom ($500 pays 0.72% vs 0.58% with a $1
  minimum), **idle capital from indivisible contracts (26.4% unspent at $500
  vs 0.4% at $25k)**, and affordability gating which trades happen at all.

**TRAP PINNED BY TEST:** the $500 account posted the BEST profit factor
(1.93 vs 1.45) and it is meaningless - it declined 4 unaffordable trades, 3
of which were losers. Worse, the populations are not even nested: declining
one trade frees the scanner to enter a later trade the funded account was
still holding through, so different balances follow genuinely divergent trade
sequences. **Never compare PF across account sizes without confirming trade
counts match.**

Practical: options need their own sizing floor, roughly "enough for 5-10
contracts of typical premium" so rounding waste stays under ~5%.

### D-222. Options tested for the first time; fee structure is the finding (CC)
`backtest/options_overlay.py` + 8 tests. `synthetic_options.py` had existed
with ZERO callers - options were in-scope in the SPEC and had never been run.
The overlay replays any strategy's bullish signals as long calls with
per-CONTRACT commissions, timeframe-correct vol annualization, and an option
spread.

Aym's premise ("options pay per trade, not per contract") is wrong for most
US brokers (Schwab/Fidelity $0.65/contract, tastytrade $1, IBKR $0.15-0.65 +
order minimum). But the underlying instinct is right and important: the fee
is a FIXED DOLLAR AMOUNT per contract, not a percentage of premium. Measured
on AAPL: commission runs **1.61% of premium at 5% OTM and 39.36% at 20% OTM**
- cheap options are punished twice (bigger fee share per contract, and a
fixed budget buys more contracts). The SPEC's v2+ WSB and tail-risk ideas
(buy cheap far-OTM) are the worst case for this structure.

Hard constraint discovered: **the $100 SPEC notional cannot buy one option
contract** on most liquid names (premiums run $200-500/contract). Options
need their own sizing rule. Pinned by test.

DO NOT trust the overlay's PnL: no IV smile means long-option entries are
priced too cheaply, flattering every result. It is a fee-structure model,
not a pricing engine. Writeup:
`research/2026-08-13-asset-class-coverage.md`.

### D-223. Weekly timeframe has never been tested (silent skip, CC found)
176 `_1wk.csv` files exist, `1wk` is in the runner's timeframe list, and
weekly has produced ZERO graveyard entries since the project began. Cause:
weekly files hold ~262 bars, the 20% test slice is 53 bars, and the runner
skips any series under 100 test bars with a bare `continue`. Fix needs
10-15y weekly history AND the runner emitting NOT_TESTED with a reason
instead of skipping silently (D-109's principle, applied to data length).
Queued, not yet done.

### D-219. Random twin is a PERCENTILE against 100 matched twins (CC RULING)
Aym delegated the Judge-vs-Quant SOUL conflict. Ruling: the percentile method
wins, and the CODE was upgraded to produce it rather than just documenting an
aspiration. `VResult.twin_pfs` now holds the full distribution;
`twin_percentile` reports where the strategy sits in it; `beats_random_twin()`
requires the 90th percentile. The old median-plus-0.15 test survives as
`beats_random_twin_legacy` for comparison only.

Why the old test was weak: one draw is a coin flip, and a fixed PF gap is
scale-dependent (0.15 means something different at PF 0.4 than at PF 4.0).
"96th percentile of 100 matched twins" is a statement; "beat one draw by 0.16"
is not.

Affordable because the twin distribution depends only on (price series, exit
config, costs) and NEVER on the strategy - so it is computed once per
(ticker, exit_config) and shared across all 35 strategies instead of being
recomputed 35 times. 100 twins now cost less than 10 did before.

Quant's SOUL updated to match Judge's (both now cite percentile + twin count).
Effect on the one interesting result so far: grid_1.0atr on ADBE 1h sits at
the **96th percentile of 100 twins** - it survives the stricter test.

### D-220. Gate version stamped on every graveyard entry (CC)
`GATE_VERSION = 2`. The incremental resume key has no code fingerprint, so
entries from different PASS/FAIL semantics could be silently pooled (an
audit finding that had no fix). Every entry now carries the stamp. The
partial 15k-entry run under the old gate was archived, not resumed, for
exactly this reason: mixing a median-gate epoch with a percentile-gate epoch
would produce a graveyard whose verdicts mean two different things.

### D-221. Position size does not change edge on a percentage-fee venue (CC)
Aym asked whether larger positions could outgrow fixed round-trip costs.
Measured: at $100 / $1k / $10k / $100k notional the SAME strategy returns
PF 1.6113, 56.2% win rate, 0.370% return - identical to four decimals, with
PnL and fees both scaling linearly. Binance.US fees are percentages, so size
multiplies both sides of the ledger. Under a hypothetical FIXED $1-per-order
commission the same trades flip from -$22.88 at $100 to +$9,091 at $100k, so
the intuition is exactly right for fixed-cost venues (per-trade commissions,
on-chain gas) and exactly wrong here. At genuinely large size costs get WORSE
(market impact, SPEC F7). Writeup:
`research/2026-08-13-position-size-and-costs.md`.

### D-218. Graveyard reports DISTINCT FINDINGS, not pass rows (CC)
`backtest/summarize_graveyard.py`. The 15k-entry partial run produced 12 PASS
rows, which reads like 12 discoveries. It is **2**: eleven of them were grid
strategies on the SAME ticker and timeframe (ADBE 1h) across 11 exit configs,
and the two grid parameterizations are one idea. The summary now reports raw
rows alongside distinct findings collapsed four ways (strategy x ticker x tf,
strategy FAMILY x ticker x tf, strategy x ticker, tickers-with-any-pass), the
pass concentration by ticker, and the expected-best-by-chance z-score for the
grid size (~4.4 sigma at 15k tests - the validation review's section 5 point
that a spectacular row is the base rate, not evidence). Benchmark passes are
counted separately and excluded from discoveries. 3 tests pin it, including
the exact ADBE shape.

**Reading rule for Aym/Raven/agents: never cite a pass count from the
graveyard JSON. Cite `distinct_findings` from summary.json.**

### D-215. SPEC 5.6 inversion implemented, F2-gated, and EMPIRICALLY REFUTED (CC)
`backtest/inversion.py` + `run_inversions.py`: signal-as-exit fade, measured
out-of-sample against buy-and-hold with full costs on both legs, gated by the
review's F2 conditions (gross PF <= 0.90, >= 30 trades, OOS). First real run:
**48 eligible tested, 0 beat buy-and-hold, edge = -$0.31 per exit against a
$0.30 round-trip cost.** The "anti-signal" is trading friction, to the cent.
Full writeup: `research/2026-08-13-inversion-finding.md`. Machinery kept (it
settles the question per run, cheaply); expectation retired. Contrarian-filter
inversion remains unimplemented by design (needs a base-strategy pairing to
mean anything). 10 tests incl. both directions (a signal that truly precedes
drops DOES show positive fade edge, proving the test can detect real edge).

### D-216. Benchmark strategies cannot be "discoveries" (CC)
The first fresh graveyard run produced exactly one PASS: `dca_14` on ETH/USDT
beating buy-and-hold by $0.31 over 28 trades. DCA has no signal - in a rising
market it wins on timing luck. Benchmarks now carry `is_benchmark` and report
`PASS_BENCHMARK`, never `PASS`, and are excluded from inversion (fading a
signal-less strategy tests nothing). Pinned by test.

### D-217. Five agent SOUL.md files drafted (CC, NEEDS RAVEN RULING)
`agents/{scout,forge,judge,coach,echo}/SOUL.md` + `agents/README.md`, matching
Quant's approved structure and voice. The drafting agent flagged 11 rules it
encoded that are NOT literally in SPEC 5.7 - they come from the audit and the
validation review. The ones that most need Aym/Raven's ruling:
- Judge RETURNS UNEVALUATED any Forge submission lacking hypotheses_generated /
  screened counts and the variant log (the p-hacking hole, review section 6).
- Judge corrects for multiple comparisons on hypotheses GENERATED, not
  submitted; must report expected-best-by-chance alongside observed best.
- Random twin as a percentile against a distribution of matched twins, NOT a
  single draw + 0.15 threshold. **This conflicts with Quant's existing SOUL,
  which still cites the single-draw rule.** Needs a deliberate ruling on
  whether Quant's file is updated to match.
- Coach never addresses Aym directly; routes through Echo. (SPEC says Coach
  cannot "report to human" - read as routing, could be read narrower.)
- Coach makes NO lifecycle recommendation at all on provisional numbers
  (stricter than D-102 states).
- Forge is told the physics but never the tripwires ("you do not know what the
  validation assertions check and you do not try to find out") - constrains
  what may be put in Forge's context at runtime.
- Scout's speculative/supported/never-"confirmed" grading scale is invented.
Full list in the agents' handoff notes; none are load-bearing in code yet
(only Quant's SOUL is active; the rest are designed-for-later per SPEC 5.7's
5-live-strategies split trigger).

## v3 - 2026-08-12/13 (night session: performance fix + T8 + reconciliation)

### D-209. Sweep scans each strategy once, replays across exit configs (CC DECISION)
The ta facade swap made per-call indicators ~19x slower (measured), which
would have turned the graveyard re-run into weeks. Since signals don't depend
on the exit config, `run_sweep` now calls `scan_all_bars()` once per strategy
and replays cached signals across all 9 exit configs (~9x, plus skipped
window construction). Verified: 0 mismatches across 24 strategy/config combos
vs direct scanning, pinned by `TestScanCacheEquivalence`. Valid only for
stateless strategies (all swept builtins are). Benchmark after fix: ~30s per
daily ticker. The first (slow) re-run was killed and restarted fresh.

### D-210. T8 sandbox built (CC DECISION, per queue D-208)
`sandbox/validator.py` + `sandbox/_runner.py`: AST allowlist (imports,
exec/eval/open/getattr-family, dunder access, star imports - fails closed and
NEVER executes rejected code), subprocess conformance with 15s timeout
(Strategy interface, well-formed Signals, stop<entry), sha256 hash pinning in
strategy_registry (additive migration adds family/code_hash columns), family
drift rejected unless allow_family_migration=True. 21 tests incl. proof that
AST-rejected code with import-time side effects never runs. ALLOWLIST POLICY:
ta/pandas/numpy included for Quant strategies - Raven to ratify (D-203).

### D-212. Engine re-audit findings triaged and fixed (CC, per re-audit report)
The independent engine re-audit (relaunched after limit reset) verified
B1-B4 + majors fixed and found real defects in the NEW executor, all fixed
same night: N1 halt now drains the queue with 'halted' labels, retries
position closes every step, and entries expire after valid_for x interval
('signal_expired'); N2 reconcile no longer closes trailed-stop winners
(unprotectable = missing/nonpositive stop only) and counts only real closes;
N3 stale-data check filters by signal timeframe; N4 every non-executed signal
path now labels its row (not_selected / block_enforced_in_scanner /
fill_failed / close_failed / trail_not_applied); N6 daily/weekly ops stops
now AUTO-HALT (HALT file + risk_events row), human resume only; N12 botctl
hardened (no overwrite of active halt, no traceback on corrupt HALT file);
N11 shutdown order scanner->executor->collector.

**N5 decision (documented deliberate gap):** the paper adapter fills
market-only; resting buy-stop/limit simulation (matching the backtest's
D-103 order semantics) is NOT implemented yet. Until it is, hammer-style
buy-stop entries fill at current ask instead of resting at the trigger
level - a known backtest/paper divergence, queued as the next engine build
item. Accepted findings not yet fixed (queued): N7 cash column semantics,
N8 scan-candle finality (re-scan once a later candle exists), N10 holiday
test fixture, per-thread ccxt instances, remaining SPEC gaps (bullish
engulfing support-alternative, SPEC-vs-code stop widths, volume SMA
self-inclusion, flat-market RSI=100 convention, restart signal dedup,
code_hash on orders, API-error-storm pause).

### D-214. Resting-order simulation in the paper path (CC, closes N5/D-212 gap)
`PaperAdapter.place_pending_buy` + `check_pending_orders` + executor routing:
entries near the ask fill market-now; above -> resting buy-stop (fills as a
market buy at current ask when touched - honest about gaps past the
trigger); below -> resting buy-limit (fills AT the limit, maker fee, no
slippage). Orders expire after valid_for x interval (from the signal row);
position stop/target reconstructed from the signal's features at fill; halt
cancels all resting orders. Placed = acted (an order exists); expiry is
recorded on the order row. Paper execution now matches the backtest's D-103
order semantics. 8 new tests (16 executor tests total).

### D-213. Backtest re-audit findings triaged and fixed (CC, per re-audit report)
The independent backtest-layer re-audit verified every blocking Section 1-2
fix as real, and surfaced new defects, fixed same night:
- **NEW-1 (HIGH): Binance switched kline timestamps to MICROSECONDS at
  2025-01-01.** Mixed-unit merged series corrupted interval inference and
  re-opened regime lookahead on event-harness crypto paths. All load points
  (both Binance mergers + data_loader) now normalize ts >= 1e14 to ms.
- NEW-2: mergers no longer drop the first candle of each headerless monthly
  file. NEW-3: random-twin median is now the TRUE median including inf seeds
  (finite-subset median understated the baseline). NEW-6: vectorized harness
  now SKIPS invalid-stop signals like the event harness (no more silent
  0.25xATR substitute). NEW-7: run_fast_gonogo no longer crashes on
  infinite-PF rows.
- The in-flight graveyard run carried garbage crypto timestamps from NEW-1:
  killed, partial output archived (v0_graveyard_full_pre-us-fix.json, 1,260
  entries), restarted clean on fixed loaders.
- Accepted-not-yet-fixed (queued for Raven's sequencing): NEW-4 delay probe
  morphs order types (anti-conservative stress probe), NEW-5 beats_twin inf
  semantics divergence (report field only), NEW-8 same-bar entry+stop
  optimism, NEW-9 same-bar re-entry divergence, NEW-10 referee small-sample
  corners, NEW-11 loader mixed-format edge, NEW-12 synthetic_options Greeks
  guards, NEW-14 build_graveyard's private yfinance loader, plus the six
  documented-divergence items in the report's section 3.

### D-211. Reconciliation on boot, paper-mode semantics (CC DECISION)
`Executor.reconcile_on_boot()` (called by main.py before threads start):
closes unprotectable positions (missing/inverted stops), marks pre-boot
queued signals 'engine_restart' so the skipped-signal dataset stays complete,
audit-logs the summary. Live-mode reconciliation (exchange comparison,
SPEC 7.3 full) remains TODO before any live launch.


Running record of engineering and process decisions. Newest section first.
Each entry: what was decided, who decided, why, and where it lives in code.
Raven: treat entries marked AYM RULING as settled; entries marked CC DECISION
are Claude Code's judgment calls, open to your review.

---

## v2 - 2026-08-12 (evening session: overhaul + libraries + continuation)

### D-201. Indicators are ta-library-backed via facades (AYM RULING)
Production indicator math comes from the maintained `ta` library. Facades in
`indicators/` keep every original signature and padding convention so no call
site changed; the audited hand-rolled math moved to
`tests/reference_indicators.py` and the cross-check tests
(`tests/test_indicator_crosscheck.py`) now require the two unrelated
implementations to agree forever. Rationale (Aym): fear of silently breaking
hand-rolled math in future edits. Side benefit: the old O(n^2) MACD
signal-line reconstruction is gone. Known delta: ta's EMA seeds differ from
SMA-seeding; convergence verified within 0.5% before any consumer's warmup
guard.

### D-202. Three backtest engines must agree (AYM RULING)
`backtest/cross_harness_check.py` runs an SMA-cross referee strategy through
the event harness, the vectorized harness, and backtesting.py (external),
wired into `validate_harness.py` as assertion A5. Agreement semantics are
documented in the file: exact-ish between our two engines, win-rate-band
against the external one. Calibration note (CC DECISION): external trade-count
tolerance is max(4, 25%) because small-sample tp/sl fill differences shift
position lifetimes and therefore later signal availability; the systematic-
error teeth are the 15-point win-rate band (the audited lookahead bug produced
90%+ win rates). First run: our engines agreed EXACTLY on AAPL and BTC; the
external agreed within tolerance.

### D-203. No bulk import of strategy libraries; docs as research input (AYM+CC)
Strategy libraries are not imported wholesale (multiple-comparisons inflation,
sandbox surface, near-zero new information). Forge may read library
DOCUMENTATION as research input (Aym endorsed). Library-vs-hand-rolled
inventory lives in `docs/handoffs/2026-08-12-claude-code-fixes.md`.

### D-204. Library swaps executed (CC DECISION per inventory + Aym push)
python-dotenv (env loading), vollib (Black-Scholes core; fixed the
zero-volatility branch bug in the process), pandas (CSV parsing internals of
`data_loader.py`, signatures stable), exchange_calendars (NYSE session gating
for D1/D2 via `_in_xnys_session`, replacing weekday checks that traded
holidays).

### D-205. Referee/validation gaps found by the test-suite audit, closed (CC)
- Pipeline-level regime alignment test added (the one mutation that survived:
  helper was tested, wiring wasn't). `tests/test_backtest.py::TestRegimePipelineAlignment`.
- `backtest/test_known_answers.py` converted to real pytest assertions (was
  return-only: pytest reported 6 passed unconditionally).
- Pattern regression tests added for tasuki/FVG/DCA/piercing/on_neck/C2
  (`tests/test_pattern_regressions.py`).
- Buy-hold validation tolerance now scales with return magnitude (fixed
  0.75pp threshold falsely failed correct runs on +714% series).
- Random-twin fee assertion moved to flat data where it is meaningful.

### D-206. Re-audit coverage compromise (CC DECISION, forced by spend limit)
The three fresh-eyes re-audit agents: test-suite auditor COMPLETED (mutation
checks; found the gaps in D-205); the backtest-layer and engine-layer
re-audit agents were TERMINATED mid-run by the monthly spend limit. Mechanical
verification stands in: 120+ tests green, validation suite 21/21 including
oracle/delay/accounting/fee/cross-harness assertions. Raven's review should
weight the backtest and engine layers accordingly; an agent re-audit can be
rerun when the limit resets.

### D-207. Graveyard reset and re-run (CC DECISION)
Old v0 graveyard JSONs are archived to `research/graveyard/archive/` rather
than resumed from: the incremental runner's resume key has no data fingerprint,
so resuming would merge void-era results with fixed-era results
indistinguishably. Fresh run started on the fixed harness + clean data.

### D-208. T9 executor built next (CC DECISION)
SPEC 15 orders T8 (sandbox) before T9 (execution), but the sandbox only
matters once Quant authors strategies (T13+), while the executor is the
missing piece that makes the paper experiment runnable and gives
`check_exits` / `write_equity_snapshot` / `mark_signal_acted` their caller.
Built as `engine/executor.py` + `engine/main.py`. Sandbox remains next in
queue.

---

## v1 - 2026-08-12 (audit + fix session)

### D-101. Builder/verifier separation (AYM RULING)
Claude Code fixes (it wrote the audit), Claude Code re-audits, Raven reviews
against SPEC. Mirrors SPEC 5.7's Forge/Judge separation.

### D-102. Nothing is durable until validate_harness.py exits 0 (CC, Aym-approved)
The validation suite was rebuilt so every control runs THROUGH the harness
(the old controls computed their own inline results and validated nothing).
Oracle sees the future via a control-only channel real strategies can never
receive; its delayed twin must collapse; buy-hold accounting must reproduce
to the cent; fees must strictly reduce PF per seed. `all_pass: false` means
every downstream result is provisional - enforced socially and by the
PROVISIONAL flags in the graveyard JSONs.

### D-103. Entry semantics contract (CC, per SPEC 5.1)
Signals fill per their order type: market at close (+slippage), buy-stop
resting `valid_for` candles (fills at max(level, open) + slippage), buy-limit
resting (fills at min(level, open), no slippage), unfilled orders expire
without a trade. Both harnesses implement it identically.

### D-104. Costs are pessimistic-realistic (CC)
Gap-through stops fill at min(stop, open); stops/time/end exits pay slippage,
resting-limit targets don't; infinite PF fails every gate (zero losses over a
test window means bug or tiny sample, never edge).

### D-105. Buy-and-hold comparison in dollars (CC, per prior review)
Same fixed notional, same window, fees on both sides. The old avg-per-trade-%
vs full-period-% comparison produced a shipped false positive.

### D-106. All price data split+dividend adjusted (CC)
One convention everywhere. Loader flags >40% close-to-open gaps;
check_data_integrity.py scans and quarantines; Alpaca script now requests
adjustment='all'. Weekly bars for split-heavy leveraged ETFs are rebuilt from
clean daily data (Yahoo's weekly bars are inconsistent around splits).
NG_F quarantined (futures roll gaps are not fixable by adjustment), SOXS
quarantined (broken at source), MULN quarantined (delisted from Yahoo - which
is why it is the survivorship canary).

### D-107. Scanner writes acted=0, always (CC, per SPEC 9.3)
Only the execution layer flips acted=1 after the risk gate approves
(`db.mark_signal_acted`). Keeps the skipped-signal learning dataset honest.

### D-108. 4-loss pause expires after 24h (CC, per SPEC 6.2)
Keyed on the last loss's closed_ts. Without expiry the pause was a permanent
deadlock (blocked entries can never produce the streak-breaking win).

### D-109. NOT_TESTED is a verdict (CC)
A strategy whose min_bars exceeds the scan window is reported NOT_TESTED,
never tested-and-failed. Graveyard truthfulness over graveyard completeness.

### D-110. Alpaca key rotation owed by Aym (SECURITY)
Key was hardcoded in source (now removed, .env-only). Rotate at Alpaca.

## v12 - 2026-08-18 (Raven technical rulings: skip classification, concurrency, strike proxy, per-asset breaker, second lock)

### D-289. strike_inside_proxy_noise_floor stays DATA_BLOCKER (RAVEN RULING, resolves Cody handoff dispute)

Cody's handoff `2026-08-18-skip-classification-gap-closed.md` disputed Raven's
prior instruction to classify this as GENUINE. Cody kept DATA_BLOCKER. Raven
confirms: DATA_BLOCKER is correct. The strike proxy is a measured instrument
with known error. STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0 is the floor below which
the signal is inside our own measurement error. The strategy was not offered
an edge and declined; it was refused an input it could trust. The module's
own tie-break rule agrees: "when a reason could be read either way, it goes to
DATA_BLOCKER, because over-reporting NOT_TESTED costs a re-test and
under-reporting it puts a fabricated verdict in the record." Moving it to
GENUINE would flip PM_corridor_collector and PM_mid_price_continuation from
NOT_TESTED to RAN_NO_ENTRY, which is the convention 11 inversion. No code
change needed; the existing classification is correct.

### D-290. AST skip-reason test: Option A, resolve indirection with loud failure (RAVEN RULING)

The AST-walking test `test_every_skip_reason_the_strategies_emit_is_classified`
cannot see skip reasons passed as variables (8 call sites, 16 invisible
reasons). Option A: teach the test to resolve simple indirection (module-level
constant tuples like NO_DATA_REASONS, local variables assigned from string
constants, IfExp over two literals) with a loud failure on unresolvable sites.
Option B: ban non-literal arguments, forcing strategies to re-spell shared
reasons (violates convention 20, "one cause, one name, across modules").

Option A is correct. It preserves the code property (convention 20) while
converting an invisible gap into a named red test. The load-bearing part is
the unresolvable-site failure clause: without it, Option A is just a bigger
version of the same blind spot. The loud failure MUST be implemented. Also
add the reverse check: every key in SKIP_CLASSIFICATION must be reachable
from some strategy, so the table does not silently accumulate dead entries.

### D-291. global_temperature_market_excluded = DATA_BLOCKER (RAVEN RULING)

A concurrent session classified this as GENUINE ("the question WAS read and
the product was declined"). Raven rules DATA_BLOCKER. The strategy has no
station for global-anomaly markets, so it cannot evaluate them. GENUINE would
count as "the strategy looked and found no edge" on a product it has no
station for, which reads as a measurement and is not one. The OUT_OF_UNIVERSE
fourth class is conceptually better but premature for one reason; revisit if
the count grows. Change the existing GENUINE entry to DATA_BLOCKER in
SKIP_CLASSIFICATION.

### D-292. Convention 26 stays as Convention 26 (RAVEN RULING)

Cody added hash-before-write as Convention 26 because 22 was already taken
("A claim in a docstring is not a wiring test"). Keep 26. Renumbering is a
one-line change if Aym wants it, but silently overwriting an existing
convention was the worse failure.

### D-293. Per-asset split lives inside check_adapter_order (RAVEN RULING)

Cody placed the asset routing inside `check_adapter_order` (one site that
decides which bucket a slug belongs to) rather than deriving it a second
time in `shadow_loop.py`. This is correct per convention 23 (one cause, one
name, one site). Deriving the asset a second time in the loop is the
convention 23 failure mode in reverse: two places that agree today and drift
silently. Keep as built.

### D-294. portfolio_daily_loss_limit_usdc = $150 approved (RAVEN RULING)

5x the per-asset limit ($30), strictly above the sum of 3 assets ($90). A
portfolio cap set at the sum of its parts can only ever trip at the same
instant as the last per-asset cap and would never bind on its own. The
inequality test (fails if SHADOW_ASSETS grows past 5) is correct. Keep $150.

### D-295. Second-lock $5,000 floor kept as vendor spec (RAVEN RULING)

D-288 wires the vendor's second lock, which the module docstring documents
as requiring a >= $5,000 liquidation print. Keep the vendor number. It is a
separate named constant (SECOND_LOCK_MIN_USD) with its own skip reason, so
setting it to 0.0 reverts to "any print at all" with a one-line change. The
two-result split (no_recent_liquidation vs liquidation_below_second_lock_min)
is correct: "tape was silent" and "tape printed under our floor" demand
different responses.

### D-296. near_liq_trigger kill-condition clock deferred until liquidation tape has data (RAVEN RULING)

The 30-day / 10-entry kill condition clause must not start counting while the
liquidation tape is empty. The liquidations table has 0 rows (Binance
geoblocked, Bybit quiet). Starting the clock now would kill the strategy for
lack of a sample caused by a dead feed, recording it as a verdict about the
idea. The kill clock starts only after the liquidations table has >= 1 row.
Implementation: add a guard in the kill-condition check that defers if the
liquidation feed has produced zero rows to date.

### D-297. Strike gate rows get per-asset measured-error field (RAVEN RULING)

The `noise_floor_measured_on: 'btc'` stamp is now under-descriptive. We have
ETH and SOL measurements (research/strike_proxy_by_asset.json). Add a
per-asset field `strike_proxy_error_at_floor_pct` to the gated row's
features_json, populated from the measured data. Keep the existing
`noise_floor_measured_on` field (it is still BTC-derived; the floor value
itself is unchanged). The new field records what we KNOW about each asset's
error rate at that floor. n=84 for SOL is under the convention 7 threshold
of 100; stamp it as a strong hint, not a verdict.

### D-298. no_recent_liquidation and liquidation_below_second_lock_min stay GENUINE (RAVEN RULING, resolves Cody handoff dispute)

Cody kept both GENUINE against Raven's instruction file, with a code-path
argument. Verified: both reasons are emitted at near_liq_trigger.py:976 and :981,
AFTER `if not window.ok: return decide('SKIP', window.reason, ...)` at line 964.
When the feed table has 0 rows, `window.ok` is False and the strategy returns
`liquidation_feed_empty` (already DATA_BLOCKER at line 420). Neither of these
two keys is reachable in the state Raven's instruction described. Classifying
them DATA_BLOCKER would report "could not run" for a lock that ran and returned
false, which is the convention 11 inversion pointing the other way. Cody is
correct. Keep GENUINE. Two independent readings agreed.
