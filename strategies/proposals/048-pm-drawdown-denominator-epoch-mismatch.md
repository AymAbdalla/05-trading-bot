---
name: "pm_drawdown_denominator_epoch_mismatch"
thesis: "The portfolio drawdown that halted the main book twice this morning is computed from a numerator and a denominator that come from different accounting universes, and the gap between them is larger than the breach it fired on. The numerator is `PolymarketPaperAdapter.get_equity()` at `engine/polymarket/paper_adapter.py:1822`, which returns `self.starting_equity + self.realized_pnl() - self.capital_at_risk()`. `realized_pnl()` walks `self.positions`, an IN-MEMORY dict that is empty at process start, and `starting_equity` is `DEFAULT_STARTING_EQUITY_USDC = 1000.0` (`engine/polymarket/shadow_loop.py:389`). So equity is PROCESS-LOCAL: every restart re-bases it to exactly 1000.00 regardless of what the ledger says. The denominator is `_risk_equity_state` at `shadow_loop.py:2649`, which runs `SELECT MAX(equity) FROM equity_snapshots WHERE mode = ?` - every row ever written by every process that has ever run against this database. That is PROCESS-GLOBAL. This is not inferred from reading code; the re-base is visible in the table. `db/trading.db` holds 608 `mode='paper'` snapshots containing SIXTEEN epochs delimited by a jump to exactly 1000.0000, and the sum of those jumps - the loss the equity series has forgotten - is 1,473.53 USD. `db/trading-survivors.db` holds 326 snapshots, 3 epochs, 851.08 USD forgotten. The all-time MAX the halt divides by is 1027.9641, recorded at 2026-08-19T04:52:14.736Z inside epoch 12, which ran 04:16:30 to 04:55:47 and ended 15h14m before the epoch the numerator was measured in (epoch 15 began 2026-08-19T20:06:30.414Z). Worse, that peak is itself mostly re-base credit: at that instant the ledger's cumulative `sum(pnl_net)` over 1,726 closes was -863.76, so a single unbroken 1,000.00 bankroll stood at 136.24 USD. The recorded peak overstates the realised book by 891.72 USD. A second, independent defect sits in the same expression. `get_equity()` SUBTRACTS `capital_at_risk()`, the sum of `max_loss_usdc` over open positions, so opening one dollar of premium lowers measured equity by one dollar instantly and raises measured drawdown accordingly. That makes the halt self-extinguishing, and both halts show it. Halt b7bd22a8 fired at 07:21:42.675Z on 0.4011; five minutes and twenty-two seconds later equity had risen 614.01 to 652.41 while realised P&L over the interval was only +9.18 on 9 closes, so 29.22 USD (76.1%) of the rise was open premium unwinding, and the drawdown had fallen to 0.3653 with nobody doing anything. Halt ee842e60 fired at 08:21:16.503Z on 0.4019; nine minutes later equity was 631.67 on realised +3.12 over 6 closes, 13.68 USD (81.4%) premium release, drawdown 0.3855. The breach MARGINS were 0.0011 and 0.0019. The mechanical premium-release swing was 0.0284 and 0.0133 - 26x and 7x the margin. Either defect alone accounts for both halts: on the current epoch's own peak (exactly 1000.0000, never exceeded) the two drawdowns read 0.3844 and 0.3851, both UNDER the 0.4000 line. That interaction is the reason this is one proposal and not two. Repair one, watch the halts stop, and you will retire the other as harmless when it was merely redundant."
expected_edge_bps: null
kill_condition: "This is a repair of a measurement and records no edge (convention 11). It changes no strategy, no entry rule, no exit rule and NO LIMIT VALUE: `max_drawdown_frac` is out of this proposal's lane in both directions and must not move as part of this work (brief 2026-08-20 tick 7, Raven/Aym lane). The repair is DONE when all five hold. (1) `_risk_equity_state` scopes its peak to the CURRENT PROCESS EPOCH - `SELECT MAX(equity) FROM equity_snapshots WHERE mode = ? AND ts >= ?` with the loop's own start timestamp, which the process already knows and which D-353 already ruled is the correct boundary key for the orphan sweep, so the two use one definition of 'this run'. `max(current, historical_peak)` stays, and the no-rows-yet case still reads as peak == current, which is what the existing docstring already says it means. NO schema change to `equity_snapshots`: it is a live table in two running books and adding a column to it would repeat the fixture failure recorded in CLAUDE.md, where a one-line DDL took the suite to 15 failed. (2) Every `risk_events` row for `constraint='portfolio_drawdown'` carries THREE drawdowns, named, never one bare number: `drawdown_frac_epoch` (in-epoch peak, the one the halt decides on), `drawdown_frac_alltime` (today's number, retained so no past reading becomes unreproducible), and `drawdown_frac_exposure_neutral` (computed on `current + capital_at_risk` against a peak built the same way, which is the drawdown with defect 2 removed). It also carries `epoch_start_ts` and `capital_at_risk_usd`. Reporting one number where three disagree is the same defect convention 20 names for a silent continue. (3) A test pins the epoch scoping against a CONSTRUCTED fixture: an `equity_snapshots` table holding 1000.00, 1027.96, then a re-base row of 1000.00 and a 615.00 row, asserting that a peak read with a start timestamp at the re-base returns 1000.00 and not 1027.96, and that the resulting drawdown is 0.3850 and not 0.4019. The fixture must be built by hand; no live database can exercise this, because both live books re-base and neither can be replayed. (4) `backtest/validate_harness.py` passes 21/21 rc 0 and the full suite passes with no NEW failures against the count re-derived in the same session - the last recorded run was 4,161 passed / 1 skipped / 0 failed, and that figure must be RE-DERIVED, never quoted (convention 25). (5) The two stale calibration sentences are corrected in place, because they are now false: `shadow_loop.py:399` and `engine/risk/constraints.py:237` both say this book's historical max drawdown is 35.99% and that 'only >=40% never fires on the current tape'. 35.99% is epoch 6's in-epoch maximum (0.3599, exact match). The whole-table running-peak maximum is now 0.4251 in environment A (2026-08-19T15:51:26.434Z, equity 590.97 against peak 1027.96) and 0.7196 in environment B (2026-08-20T00:15:56.995Z, equity 283.64 against peak 1011.62), so a 40% line HAS been exceeded twice over on the tape as it now stands and the sentence that justified the 0.40 override no longer describes the data. Correct the sentences; do not touch the value they justify. The repair is measurably WRONG and must be rolled back if, after it lands, the epoch-scoped drawdown for the CURRENT epoch at a pinned instant differs from `(1000.0 - current_usd) / 1000.0` by more than 0.0001 while that epoch's own maximum snapshot is still exactly 1000.0000 - that is an arithmetic identity in the present state, not a forecast, and if the new code disagrees with it the scoping is picking the wrong rows. Baselines to re-derive immediately before and after IN ONE SESSION, because both books are live under read (convention 25): environment A 16 epochs / 1,473.53 USD re-base credit / all-time peak 1027.9641 / current-epoch peak 1000.0000, environment B 3 epochs / 851.08 USD / 1011.6246 / 1000.0000, measured 2026-08-20T08:49Z-09:00Z. If neither this nor an explicit decision to keep the current definition is recorded within 14 days, record NOT_TESTED and requeue - the number does not become correct by being left alone, and every restart adds an epoch to the mismatch."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes NO entry rule, NO exit rule, NO sizing, NO strategy
     parameter and NO limit value. It changes how one ratio is computed and
     what a `risk_events` row reports. It does not decide whether the book
     should be halted, resumed, or left alone; that is Raven and Aym's, and
     this proposal deliberately gives them a different number to decide on
     rather than a decision.
  1. Do NOT change `max_drawdown_frac`, in `engine/risk/constraints.py`
     (0.25) or the `SHADOW_RISK_LIMITS` override in `shadow_loop.py` (0.40),
     and do NOT touch `config.yaml`. The uncomfortable direction is stated
     plainly in the body: a correct denominator makes both of this morning's
     halts vanish. That is a reason for MORE care about the limit, not less,
     and it is exactly why the limit must not move in the same change as the
     measurement. One change, one question.
  2. Do NOT clear, re-arm, or add a second path to `HALT`. `engine/halt.py`
     stays the single kill-switch definition (CLAUDE.md, standing). This
     repair does not decide the state of halt `ee842e60`.
  3. Do NOT delete or rewrite the all-time drawdown. It stays in the
     `risk_events` payload under its own name. Every reading taken before
     this repair was taken against it, and a repair that makes past numbers
     unreproducible trades one silent defect for another.
  4. The halt DECISION keys on the epoch-scoped number only. Two numbers in a
     payload and an ambiguous rule about which one fires is worse than one
     wrong number, because it fails differently on different days.
  5. Do NOT migrate `equity_snapshots`. The epoch boundary is derivable from
     the running process's own start time; it does not need to be stored. Both
     books are live and a DDL against a table two loops write every five
     minutes is the highest-cost, lowest-need version of this change.
  6. Both databases get the identical treatment on their own `--db` and their
     epoch censuses are reported separately, never summed (convention 32).
     Environment B is the control here and it matters: it has never recorded a
     single `portfolio_drawdown` risk event, and its own worst whole-table
     excursion is 0.7196.
  7. Do NOT wire any strategy to read the drawdown. Same rule as 043 rule 8,
     038 rule 6 and 042 rule 7. A strategy that trades off its own book's
     drawdown is a feedback loop, and this one is already oscillating.
data_requirements: |
  HAVE, verified read-only at 2026-08-20T08:49Z-09:00Z with the wall clock,
  both books LIVE under read and every figure point-in-time:
  the epoch census. `SELECT ts, equity FROM equity_snapshots WHERE mode='paper'
  ORDER BY ts` returns 608 rows in db/trading.db containing 16 segments
  separated by a jump to exactly 1000.0000, and 326 rows / 3 segments in
  db/trading-survivors.db. Summed re-base credit 1,473.53 and 851.08 USD.
  HAVE: the peak's provenance. All-time MAX(equity) = 1027.9641 at
  2026-08-19T04:52:14.736Z; cumulative `sum(pnl_net)` over the 1,726 closes
  at or before that instant = -863.76, so a single 1,000.00 bankroll stood at
  136.24 and the peak overstates the realised book by 891.72 USD.
  HAVE: the four `portfolio_drawdown` rows in `risk_events` (environment A;
  environment B has ZERO), carrying `current_usd`, `peak_usd`, `drawdown_frac`,
  `limit_frac` and `halt_id` for both halts - so the recorded numerator and
  denominator are readable without re-deriving them.
  HAVE: the self-extinguishing evidence, from `equity_snapshots` and
  `positions` together. Snapshot 07:21:35 equity 614.01 open_risk 29.896 ->
  07:26:57 equity 652.41 open_risk 0.676, against 9 closes summing +9.18 in
  that interval. Snapshot 08:19:42 equity 618.02 open_risk 23.956 -> 08:30:20
  equity 631.67 open_risk 1.726, and 6 closes summing +3.12 between the halt
  and that snapshot. The `kill_switch` rows carry `capital_at_risk_usdc`
  directly: 26.096 at halt 1, 15.406 at halt 2, and 28.2563 at the third
  instance on 2026-08-18T20:10:46Z which was resumed EIGHT SECONDS later.
  HAVE: the code. `paper_adapter.py:1822` `get_equity`, `:1812` `realized_pnl`,
  `:1815` `capital_at_risk`; `shadow_loop.py:389` starting equity, `:405`
  the 0.40 override and its 35.99% justification, `:2649` `_risk_equity_state`;
  `constraints.py:189` `EquityState`, `:201` `drawdown_frac`, `:237`
  `max_drawdown_frac` and the same 35.99% sentence.
  MISSING, and it is why the kill condition asks for a constructed fixture:
  any replayable equity series. Both books re-base, both are live, and neither
  can be rewound; a test that reads a live database would pass today for a
  reason that has nothing to do with the scoping being right.
  NOT NEEDED: any database migration, any backfill, any re-derivation of past
  P&L. The ledger is not affected by this at all - `positions.pnl_net` never
  re-bases, which is precisely how the defect was measurable.
  NOT NEEDED: `market_tape`, `market_resolutions`, the calibration tape,
  `market_duration`, or the taker fee schedule.
markets: "Not a market selection. Instrument-level repair of the portfolio drawdown constraint that governs Polymarket crypto Up/Down entries in both shadow books. Both databases treated separately (convention 32)."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **MEASUREMENT, NOT A LIMIT CHANGE.** Nothing here proposes moving
> `max_drawdown_frac`, clearing `HALT`, touching `config.yaml` or restarting
> anything. The brief put the drawdown in Raven and Aym's lane and it stays
> there. What this proposal does is show that the number they would be deciding
> on is not the number it is labelled as, and hand them three numbers instead
> of one.

> **THE REPAIR POINTS THE UNCOMFORTABLE WAY AND I AM SAYING SO FIRST.** On the
> current epoch's own peak, this morning's two drawdowns read 0.3844 and
> 0.3851 against a 0.4000 line. A correct denominator un-fires both halts on a
> book that is losing 29.51 USD an hour. That is not an argument for resuming.
> It is the strongest possible argument for not letting a measurement repair
> and a limit decision travel in the same change.

## The two numbers are from different universes

| | numerator | denominator |
|---|---|---|
| source | `PolymarketPaperAdapter.get_equity()` | `SELECT MAX(equity) FROM equity_snapshots` |
| scope | **this process**, in-memory since start | **every process** that ever wrote the table |
| re-bases on restart | yes, to exactly 1000.00 | no |
| at halt `ee842e60` | 614.8749 | 1027.9641 |

`realized_pnl()` sums `self.resolved_positions()`, and `self.positions` is a
dict that a fresh process starts empty. `capital_at_risk()` walks the same
dict. So on a restart the adapter reports `starting_equity` exactly, which is
`DEFAULT_STARTING_EQUITY_USDC = 1000.0`, and it reports it no matter what the
ledger holds. The peak, meanwhile, remembers everything.

The re-base is not a theory. It is 16 segments in one table:

| | environment A | environment B |
|---|---|---|
| `mode='paper'` snapshots | 608 | 326 |
| epochs (jumps to exactly 1000.0000) | **16** | 3 |
| summed re-base credit | **1,473.53 USD** | 851.08 USD |
| all-time MAX(equity) | 1027.9641 | 1011.6246 |
| current epoch's own peak | **1000.0000** | 1000.0000 |
| `portfolio_drawdown` risk events, ever | 4 | **0** |

The peak the halt divided by was set at 2026-08-19T04:52:14.736Z, inside
epoch 12 (04:16:30 to 04:55:47). The epoch the numerator was measured in
began at 2026-08-19T20:06:30.414Z. **The denominator is 15 hours and 14
minutes older than the universe the numerator lives in**, and two restarts
happened in between.

## The peak is 891.72 USD of fiction

Epoch 12 opened at exactly 1000.00 after epoch 11 closed at 938.73 - a 61.27
USD credit - and then earned 27.96 on top. So 1027.9641 is a real gain sitting
on a re-base.

The ledger says what the book was actually worth at that instant, and the
ledger does not re-base:

```
closes at or before 2026-08-19T04:52:14.736Z ......... 1,726
cumulative sum(pnl_net) ............................. -863.76 USD
value of a single unbroken 1,000.00 bankroll ......... 136.24 USD
recorded peak ....................................... 1,027.96 USD
overstatement ....................................... 891.72 USD
```

The book has never been worth 1,027.96 by 891.72 USD.

## The same expression has a second defect, and it makes the halt cure itself

`get_equity()` subtracts `capital_at_risk()`. Open one dollar of premium and
measured equity falls one dollar immediately, before anything wins or loses.
So a book that is flat on P&L reads lower simply for having positions on, and
the drawdown constraint conflates "we lost money" with "we currently have
money at risk".

Blocking entries drives `capital_at_risk` to zero. Which means **the halt
mechanically repairs the number it fired on**, and both halts show it:

| | halt 1 `b7bd22a8` | halt 2 `ee842e60` |
|---|---|---|
| fired at | 07:21:42.675Z | 08:21:16.503Z |
| drawdown at fire | 0.4011 | 0.4019 |
| breach margin over 0.4000 | **0.0011** | **0.0019** |
| open positions / capital at risk | 9 / 26.096 | 8 / 15.406 |
| equity, next snapshots | 614.01 -> 652.41 | 618.02 -> 631.67 |
| elapsed | 5m 22s | 9m 04s |
| realised P&L over that interval | +9.18 (n=9) | +3.12 (n=6) |
| **premium release, not profit** | **+29.22 (76.1%)** | **+13.68 (81.4%)** |
| drawdown after | **0.3653** | **0.3855** |
| release expressed as drawdown | **0.0284** | **0.0133** |
| release / breach margin | **26x** | **7x** |

Both halts were clear of their own trigger within ten minutes without anyone
resuming anything. There is a third instance on the record and it is the
starkest: `kill_switch` at 2026-08-18T20:10:46Z with 4 open and 28.2563 USDC
at risk, `resume` at 20:10:54Z - **eight seconds**.

A constraint whose ordinary intraday oscillation from open-exposure phase
alone is 7 to 26 times the breach it fires on is not measuring what it is
named after. It is sampling the position cycle.

## Why this is one proposal and not two

Because either repair alone removes both halts, and that is a trap.

- Epoch-scope the peak: 0.3844 and 0.3851. Under the line.
- Neutralise exposure instead: add back `capital_at_risk` and the halt
  instants read roughly 26.1 and 15.4 USD higher, worth 0.0254 and 0.0150 of
  drawdown against margins of 0.0011 and 0.0019. Under the line.

Fix one, observe that the halts stop, and the honest-looking conclusion is
that the other defect was harmless. It was not; it was redundant. That is the
same shape as the dead nested test at `6666199` recorded in CLAUDE.md - a
mechanism whose failure is invisible because a second mechanism happens to
mask it. Both get named in one document, or the second one gets retired for
the wrong reason.

## What the commensurable readings actually say

There are exactly two ways to make the two sides of this ratio agree, and
neither of them is 0.4019.

**Epoch-local on both sides.** Peak 1000.0000, current 614.8749, drawdown
**0.3851**. This is what the repair implements. Its honest weakness is stated
below.

**Book-lifetime on both sides.** One unbroken 1,000.00 bankroll, peak 1000.00,
current = `1000 + sum(pnl_net)`:

| | environment A | environment B |
|---|---|---|
| closed positions | 4,110 | 2,339 |
| cumulative `sum(pnl_net)` | **-1,687.17** | **-1,029.22** |
| single-bankroll equity | **-687.17** | **-29.22** |
| drawdown | **1.6872** | **1.0292** |
| crossed -1,000 at | **2026-08-19T08:46:01.442Z**, close #2,105 | **2026-08-20T03:43:54.488Z**, close #1,976 |

On a single bankroll **both books are past zero**. Environment A crossed it
**22 hours and 36 minutes before the first halt fired**, and environment B has
never recorded a `portfolio_drawdown` event at all. The re-base is what makes
insolvency invisible to a drawdown limit: every restart hands the book a fresh
1,000 and the constraint starts counting again from there.

To be precise about what that does and does not mean: this is a PAPER book
with no real capital, so "past zero" means the strategy set has consumed 1.69
and 1.03 times its nominal bankroll in REALISED losses across 4,110 and 2,339
closes. Nothing was liquidated and nobody lost money. It is still the number a
solvency control would exist to catch, and no control in this system can see
it.

## Why this repair might be wrong

**A restart launders the drawdown.** This is the strongest objection and it is
an objection to my own recommendation. If the peak is epoch-scoped, then every
restart resets the drawdown to zero, and on a book that restarts every few
hours a slow bleed may never accumulate enough within one epoch to fire. The
counter-evidence is that it can: environment A's epoch 14 reached an in-epoch
drawdown of 0.4182 and the current epoch reached 0.3860 in 12.5 hours. But the
objection stands in principle, and it is why the referred question below is
referred rather than decided.

**The alternative is worse in a different way.** Making the NUMERATOR
cross-epoch instead - equity as `1000 + sum(pnl_net)` over the whole ledger -
is the other commensurable pair, and it reads 1.6872 today. That is arguably
the truest number in this document. It is also unusable as a live constraint,
because it can never recover: a drawdown that has passed 1.0 permanently
exceeds any threshold, so the constraint would halt forever from the moment it
landed and would carry no information after that. A control that is always on
is the same as no control.

That is a genuine fork, both branches have a real cost, and it is **REFERRED
TO RAVEN AND AYM, not decided here**: epoch-scoped and resettable, or
book-lifetime and terminal. I recommend epoch-scoped because a resettable
control still discriminates within a run and the terminal one discriminates
nothing, and because whichever is chosen, defect 2 needs fixing either way.

**The exposure-neutral number could be wrong in the other direction.** Adding
`capital_at_risk` back means the book stops recognising open premium as risk
at all, which for binaries held to resolution is a real exposure that really
can go to zero. The proposal therefore REPORTS it as a third number rather
than deciding on it. There is a defensible position that a book which cannot
flatten should count its open premium as already lost - if so, defect 2 is not
a defect and the payload's third number simply documents the choice. I do not
think that survives the eight-second resume on 2026-08-18, but I am not
certain, and the kill condition is written so the repair is correct under
either reading.

**I have not measured the harm.** Defect 1 and defect 2 are demonstrated on
three halt instants, one of which is eight seconds long. Three is not a
sample. What I can say is that they fully account for the only two breaches
this book has ever recorded, and that the arithmetic is deterministic rather
than statistical - the re-base is exact, the subtraction is exact, and neither
needs a sample size to be true.

## What past failure this addresses

Convention 20, applied to a ratio: a number that is silently composed of two
incompatible measurements produces no error, no warning, and a verdict in the
name of a quantity it is not measuring. The `risk_events` rows are meticulous -
they record `current_usd`, `peak_usd`, `drawdown_frac`, `limit_frac` and a
`halt_id` - and every field is individually accurate. Nothing in them says the
two dollar figures were produced by different accounting.

It also addresses, directly, the sentence that authorised the 0.40 line. Both
`shadow_loop.py:399` and `constraints.py:237` say this book's historical max
drawdown is 35.99% and that "only >=40% never fires on the current tape".
35.99% is epoch 6's in-epoch maximum - 0.3599, an exact match, measured before
epochs 14 and 15 existed. The whole-table running-peak maximum is now 0.4251
in environment A and 0.7196 in environment B. The justification aged out of
correctness without anyone editing it, which is what a measured constant in a
comment does when the thing it measured keeps running.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  work is D-343 (which created these constraints), D-353 (which ruled that a
  process start time is the correct boundary key), and proposal 049 filed this
  cycle, which asks a different question about the same constraint.

## RULING NOTE - D-380 R1 (Raven, recorded 2026-08-20)

**Findings RATIFIED in full. Implementation HELD. Kill condition UNCHANGED.**

Both measurement defects are confirmed: the process-local `get_equity()`
numerator re-basing to the launcher equity on every restart against a
process-global `MAX(equity)` denominator, and `get_equity()` subtracting
`capital_at_risk()` so that open premium reads as drawdown and blocking entries
mechanically cures the trigger. Reproduced read-only by Raven before ruling;
deterministic, so no sample-size argument is needed and none was made.

**The fork this proposal referred is NOT taken here, and it is no longer
waiting.** 048 referred the epoch-scoped-vs-book-lifetime choice to Raven and
Aym ("the choice is not mine"). Aym has since decided the surrounding question:
**D-358** (resume, keep measuring, fund the book if it zeroes) and **D-359**
(auto-halt disabled in shadow, `max_drawdown_frac=1.0`). His decision was to
keep the current instrument and re-fund at zero, NOT to change the measurement.

So the hold is now a settled position rather than a pause. **Nothing in this
proposal is implemented and nothing is scheduled:** no `_risk_equity_state`
epoch scoping, no three-named-drawdown payload, no fixture, and no correction
to the stale sentences at `shadow_loop.py:399` / `constraints.py:237`. The kill
condition is untouched. Status stays as filed.

The findings are transcribed into D-380 so they survive independently of this
file. Sibling proposal 049 was accepted and BUILT in the same ruling; it asks a
different question about the same constraint and does not depend on this one.
