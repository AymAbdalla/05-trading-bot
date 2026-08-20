---
name: "pm_drawdown_breach_attribution"
thesis: "Proposal 048 shows the drawdown NUMBER is composed wrong. This proposal asks the separate question the brief actually put first - what the 40% is MADE of - and the answer is that it is made of uptime. The main book's current epoch has run 12.46 hours and given back 367.65 USD over 1,322 closes, a realised rate of -29.51 USD/hour. At that rate a 400 USD drawdown from the 1,000.00 re-base arrives after 13.6 hours. Environment B, a SEPARATE book with a different strategy set on a different database, has run 8.10 hours and given back 235.60 over 684 closes: -29.09 USD/hour, 13.8 hours to the same line. Two independent books agree on the arrival time to within 0.2 hours. That is what makes the constraint a clock rather than a control. The tape confirms it three ways. First, the constraint had never fired before today, and the reason is not that the book was ever safe: `equity_snapshots` holds 16 epochs in environment A with a MEDIAN duration of 1.29 hours and a mean of 3.14, and only three have ever exceeded 8 hours. The current epoch, at 12.84 hours, is the longest in the table and is the first one to reach the line. The constraint's expected first-fire time exceeds the median epoch by roughly tenfold, so before today it had simply never been given the uptime. Second, the breach is not an outlier on the book's own path. Full-hour realised P&L across the current epoch has mean -29.06 and standard deviation 40.68 over 12 hours; the expected 12-hour loss is 348.7 and a 400 USD breach sits 0.36 sigma beyond it, treating hours as independent - and they are not, because a market-side resolves once and carries roughly 22 shares (proposal 046), so the true sigma is larger and the breach is even MORE ordinary than 0.36 sigma makes it look. Third, and most directly, the halt did not select a bad hour. The worst hour of the epoch was -86.18 at 02:06Z and it did not fire anything; the halt fired five hours later during an hour that ranks second, because by then the CUMULATIVE total had crossed a level. A level test on an accumulating quantity with a stationary negative drift is a timer with extra steps. Underneath, the composition is the standing correction restated in dollars: of the 367.65 USD, the `fair_value_arb` family - parent, wide and patient - contributed -193.97, which is 52.8%, and `dip_arb` a further -6.50, so 54.5% of the drawdown that halted the book was produced by strategies the vault has already retired (TESTED_FAILED, spread_eats_edge) or already killed (D-356 R4), and which are still trading only because convention 13 makes a kill inert until a restart and the restart is being withheld for unrelated reasons. The halt is doing bluntly, by blocking every entry, what the withheld restart would have done selectively."
expected_edge_bps: null
kill_condition: "This is a governance measurement and records no edge (convention 11). It proposes no strategy, no limit change, no halt action and no restart, and it does NOT recommend resuming the book. It asks for one instrument. The instrument is BUILT when all four hold. (1) A read-only reporter - `backtest/drawdown_attribution.py`, taking `--db` and treating each database separately (convention 32) - reports, for the current process epoch: epoch start, uptime in hours, realised `sum(pnl_net)`, realised USD/hour, full-hour mean and standard deviation, the implied hours-to-limit at the epoch's own mean rate, and the distance in sigma between the observed cumulative loss and the epoch-mean path at the same uptime. It must print the number of independent MARKET-SIDES behind each figure alongside the close count, not the close count alone, because 046 established that shares within a market-side are one draw. (2) It also reports the composition: realised P&L by `strategy_id` and by `exit_reason` for the epoch, each as a signed USD total, a count, and a share of the epoch net - and it must print losers and winners as separate subtotals rather than only the net, because the net of this epoch is -367.65 while the gross channels are -2,085.05 against +1,717.39 and those are different facts. (3) Every future `portfolio_drawdown` breach writes the sigma figure and the hours-to-limit into its `risk_events` payload, so a breach is readable as 'the rate changed' or 'the clock ran out' at the moment it happens rather than only in hindsight. (4) `backtest/validate_harness.py` passes 21/21 rc 0 and the full suite passes with no NEW failures against a count RE-DERIVED in the same session (last recorded 4,161 passed / 1 skipped / 0 failed - re-derive, never quote, convention 25). The finding this instrument exists to test is GRADED, not assumed, and the bar is explicit: after FIVE recorded `portfolio_drawdown` breaches across both books, if EVERY breach reads under 1.0 sigma from its own epoch's mean path, the level limit is confirmed to carry no anomaly information on this book and the correct instrument is a RATE test, which is then a live proposal for Raven and Aym rather than a claim made here. If any breach reads at or beyond 2.0 sigma, this thesis is WRONG, the limit is discriminating, and this proposal should be marked TESTED_FAILED - a level limit that catches genuine excursions is exactly what it is named after. Between 1.0 and 2.0, NOT_TESTED and keep counting. There are TWO breaches on the record today, both in environment A, both at 0.36 sigma or less on the arithmetic above, and two is not five - so the honest current status is NOT_TESTED and this proposal grades nothing (convention 11). The measurement is measurably WRONG and must be rolled back if the reporter's realised USD/hour for a pinned epoch differs from `sum(pnl_net) / (max(closed_ts) - min(closed_ts))` computed directly over the same rows by more than 0.01 USD/hour; baselines to re-derive immediately before and after in ONE session because both books are live under read - environment A -29.51 USD/hour over 12.46 hours and 1,322 closes, environment B -29.09 over 8.10 hours and 684 closes, measured 2026-08-20T08:49Z-09:05Z. If nothing is built within 14 days, record NOT_TESTED and requeue; the clock keeps running either way and every restart resets it."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. No entry rule, no exit rule, no sizing, no gate, no strategy
     parameter, no limit value. One read-only reporter and one added field on
     an existing `risk_events` payload.
  1. This proposal does NOT recommend resuming the book, clearing halt
     `ee842e60`, re-arming anything, or restarting either loop. It does not
     recommend moving `max_drawdown_frac` in either direction. Those are
     Raven and Aym's, per the brief, and nothing measured here decides them.
  2. Do NOT read this as "the halt was wrong". The halt fired on a book that
     is genuinely losing 29.51 USD an hour, and stopping that book is a
     defensible thing to do. The claim is narrower and it is about
     INFORMATION: the breach did not tell anyone anything they could not have
     computed from the uptime, so a resume decision made on the strength of
     the breach is being made on no evidence.
  3. Do NOT wire any strategy to read the reporter, the sigma, or the
     drawdown. Same rule as 043 rule 8, 038 rule 6, 042 rule 7 and 048 rule 7.
  4. The composition table is an ACCOUNTING decomposition and must be labelled
     as one wherever it is printed. It is not a counterfactual. Removing the
     `fair_value_arb` family would change cash, the entry sequence, which
     signals reach the per-event cap and therefore what every other strategy
     did, so "-193.97 of the -367.65" does NOT license "the halt would not
     have fired without it". The reporter must not compute a
     book-without-strategy-X number.
  5. Report losers and winners as separate subtotals, never only the net. A
     net of -367.65 built from -2,085.05 against +1,717.39 is a different book
     from one that drifted quietly to -367.65, and the exit-reason table is
     where the difference is visible.
  6. Both databases separately, never pooled (convention 32), and never
     pooling maker with taker fills (convention 32).
  7. Count market-sides, not shares, wherever an error bar is printed (046).
     A figure with a close count and no cluster count is the defect 046 was
     filed for.
data_requirements: |
  HAVE, verified read-only at 2026-08-20T08:49Z-09:05Z with the wall clock,
  both books LIVE under read, every figure point-in-time:
  the epoch rate. Environment A current epoch (from 2026-08-19T20:06:30.414Z)
  1,322 closes, -367.65 USD, span 12.46h, -29.51 USD/h, -0.2781 per close.
  Environment B current epoch (from 2026-08-20T00:18:49.024Z) 684 closes,
  -235.60, span 8.10h, -29.09 USD/h.
  HAVE: epoch durations. Environment A 16 epochs - 8.68, 0.14, 0.34, 0.07,
  0.35, 3.94, 0.79, 2.19, 1.77, 0.41, 5.87, 0.81, 0.65, 2.53, 8.82, 12.84
  hours; median 1.29, mean 3.14. Environment B 3 epochs - 2.79, 16.79, 8.69.
  HAVE: the hourly distribution. Environment A, 12 full hours of the current
  epoch: mean -29.06, sd 40.68, min -86.18 (02:06Z), max +53.94 (05:06Z), and
  the hour before halt 1 was -63.54, second-worst. Environment B, 8 full
  hours: mean -27.42, sd 59.22, min -95.02, max +75.53.
  HAVE: the composition. Environment A current epoch by exit reason -
  `sell:salvage_floor` 207/-716.21, `sell:price_stop` 274/-713.23, `stop`
  323/-629.34, `sell:model_stop` 23/-21.19, `sell:time_stop` 6/-5.08 against
  `target` 165/+1,148.76, `sell:profit_target` 311/+498.96,
  `sell:mean_reverted` 7/+52.87, `sell:window_close` 3/+16.60,
  `sell:converged` 3/+0.20. By strategy - `PM_fair_value_arb` 565/-179.91,
  `PM_fair_value_settlement_exit` 380/-90.93, `PM_maker_rebate_quote_ladder`
  60/-41.95, `PM_streak_snapper` 12/-19.80, `PM_corridor_collector` 21/-16.15,
  `PM_fair_value_arb_wide` 31/-15.51, `PM_mid_price_continuation` 56/-10.05,
  `PM_dip_arb` 73/-6.50, `PM_small_liq_continuation` 1/-4.84,
  `PM_fair_value_arb_patient` 1/+1.45, `PM_corridor_pair` 38/+6.70,
  `PM_temporal_arbitrage` 84/+9.85.
  HAVE: the per-event cap census. 63 `per_event_notional` risk events in
  environment A across 13 distinct `window_ts` values, all `crypto_updown`.
  MISSING, and it is the reason clause 1 demands cluster counts: a market-side
  key on `positions`. The counts above are SHARE counts. 046 established the
  design effect is roughly 22 shares per resolved market-side, so 1,322 closes
  is on the order of 60 independent draws and 330 is on the order of 15. Every
  sigma in this document is first-order and stated as such.
  MISSING: any epoch that was ended deliberately rather than by a restart.
  All 16 boundaries are process restarts, so "epoch length" is really
  "interval between restarts" and is not a property of the book at all.
  NOT NEEDED: any database write, any migration, any backfill, any re-run of
  043/046/047, any strategy change, any restart.
markets: "Not a market selection. A governance measurement over both shadow books' own ledgers and equity series. Both databases treated separately (convention 32)."
kind: governance
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

> **THIS PROPOSAL DOES NOT RECOMMEND RESUMING THE BOOK.** It recommends
> nothing about halt `ee842e60`, `max_drawdown_frac`, the restart, or either
> loop. Those are Raven and Aym's lane per the brief and nothing here decides
> them. What it does is answer the question the brief asked - what the 40% is
> made of - and the answer turns out to be uncomfortable in a different
> direction than expected.

> **PAIRED WITH 048, NOT DUPLICATING IT.** 048 says the drawdown RATIO is
> composed of incompatible parts. This says that even with a perfectly
> composed ratio, a fixed LEVEL on it carries no information about this book,
> because the book reaches any fixed level as a function of how long it has
> been left running.

## Two books, one arrival time

| | environment A | environment B |
|---|---|---|
| current epoch began | 2026-08-19T20:06:30.414Z | 2026-08-20T00:18:49.024Z |
| uptime at read | 12.46 h | 8.10 h |
| closes | 1,322 | 684 |
| realised `sum(pnl_net)` | -367.65 | -235.60 |
| **realised rate** | **-29.51 USD/h** | **-29.09 USD/h** |
| **hours to a 400 USD drawdown** | **13.6** | **13.8** |

Different database, different strategy set, different start time, and the two
books agree on when a 40% line arrives to within twelve minutes. That is not a
coincidence to be explained; it is what a stationary negative drift does. The
constraint is not detecting anything about either book. It is counting.

## The constraint had never fired because it had never been given the time

`equity_snapshots` in environment A holds 16 epochs. Their durations, in
hours:

```
8.68  0.14  0.34  0.07  0.35  3.94  0.79  2.19
1.77  0.41  5.87  0.81  0.65  2.53  8.82  12.84
```

Median **1.29**. Mean **3.14**. Only three have ever passed 8 hours. The
current one, at **12.84 hours, is the longest in the table** - and it is the
first one ever to reach the line.

So the reassuring fact that a 40% halt "never fires on the current tape",
written into `shadow_loop.py:399` and `constraints.py:237`, was never a
statement about the book's risk. It was a statement about its uptime. The
expected first-fire time is roughly ten times the median epoch. Give the same
book the same strategies and leave it alone for half a day and it fires. It
did.

## The breach is not an excursion

Full-hour realised P&L across the current epoch, environment A, sorted:

```
-86.18  -63.54  -61.26  -56.57  -53.38  -47.83
-22.61  -22.34  -10.29   -5.57  +26.96  +53.94
```

mean **-29.06**, sd **40.68**, n=12.

Expected 12-hour loss at that mean: **348.7**. The line sits at 400. Treating
hours as independent, sd of the 12-hour sum is 140.9, so a 400 USD breach is
**0.36 sigma** beyond the mean path.

Hours are NOT independent - a market-side resolves once and carries roughly 22
shares (046), so the real sigma is larger. **That caveat cuts against the
limit, not for it:** a larger sigma puts the breach even closer to the middle
of the book's own distribution. 0.36 sigma is the most anomalous this breach
can possibly look.

And the timing is the plainest evidence of all:

- The **worst hour of the epoch was -86.18** at 02:06Z. Nothing fired.
- The halt fired at **07:21:42Z**, five hours later, in a stretch preceded by
  a -63.54 hour that ranks second.

A level test on an accumulating quantity fires when the accumulation reaches
the level, not when anything goes wrong. The worst hour of the epoch was
survived without comment and a mid-ranking one triggered the halt, because by
then the running total had arrived.

## What the drawdown is made of

Current epoch, environment A, n=1,322, net **-367.65** - and the net hides the
shape completely:

| exit reason | n | USD |
|---|---|---|
| `sell:salvage_floor` | 207 | **-716.21** |
| `sell:price_stop` | 274 | **-713.23** |
| `stop` | 323 | **-629.34** |
| `sell:model_stop` | 23 | -21.19 |
| `sell:time_stop` | 6 | -5.08 |
| **loss channels** | **833** | **-2,085.05** |
| `target` | 165 | +1,148.76 |
| `sell:profit_target` | 311 | +498.96 |
| `sell:mean_reverted` | 7 | +52.87 |
| `sell:window_close` | 3 | +16.60 |
| `sell:converged` | 3 | +0.20 |
| **win channels** | **489** | **+1,717.39** |

The book turns over 3.8 million USD-cents of gross P&L to lose 367.65. Three
exit reasons carry 2,085.05 of the loss and the standing instruments already
watch all three: 043 and its repairs 046/047 grade `sell:salvage_floor`, 039
watches `sell:time_stop`, and the `stop` / `sell:price_stop` pair is the
execution-cost mechanism the critic post-mortem identified across 413 losses.
Nothing new is proposed against them here and nothing should be - they are
instrumented and the instruments are short of their bars, not short of ideas.

By strategy, the epoch reads:

| strategy | n | USD | share of net |
|---|---|---|---|
| `PM_fair_value_arb` | 565 | -179.91 | 48.9% |
| `PM_fair_value_settlement_exit` | 380 | -90.93 | 24.7% |
| `PM_maker_rebate_quote_ladder` | 60 | -41.95 | 11.4% |
| `PM_streak_snapper` | 12 | -19.80 | 5.4% |
| `PM_corridor_collector` | 21 | -16.15 | 4.4% |
| `PM_fair_value_arb_wide` | 31 | -15.51 | 4.2% |
| `PM_mid_price_continuation` | 56 | -10.05 | 2.7% |
| `PM_dip_arb` | 73 | -6.50 | 1.8% |
| `PM_small_liq_continuation` | 1 | -4.84 | 1.3% |
| `PM_fair_value_arb_patient` | 1 | +1.45 | |
| `PM_corridor_pair` | 38 | +6.70 | |
| `PM_temporal_arbitrage` | 84 | +9.85 | |

**The `fair_value_arb` family - parent, wide, patient - is -193.97, or 52.8%
of the epoch's net loss. Add `dip_arb` at -6.50 and it is 54.5%.**

Both are already condemned. The family is TESTED_FAILED in the vault with the
mechanism named (`spread_eats_edge`, enters at ask and exits at bid on ~8s
holds, and the inverse variant losing money is what proved the model is not
the problem). `dip_arb` was killed by D-356 R4 this morning. Neither has
stopped trading, because convention 13 makes a strategy change inert until a
restart, and the restart is being withheld for reasons that have nothing to do
with either of them.

So the halt is doing, bluntly and to everything, what the withheld restart
would have done selectively to two things.

**That is accounting, not a counterfactual, and the distinction is load
bearing.** Removing the family would change cash, the order in which signals
were evaluated, which events hit the 30.00 per-event cap and therefore what
every other strategy did. "-193.97 of the -367.65" does not license "the halt
would not have fired". Rule 4 forbids the reporter from ever computing that
number, precisely because it would be quoted.

## The per-event cap question, answered and refused

The brief asked whether the book enters drawdown-driven states that the
per-event cap then compounds. Measured: 63 `per_event_notional` events across
13 distinct `window_ts` values, all `crypto_updown`, all in the current epoch.
Splitting the epoch's closes by whether they were opened in or adjacent to a
capped 5-minute bucket:

| | n closes | USD | per close |
|---|---|---|---|
| in/near a capped event | 330 | **+32.59** | +0.0987 |
| everything else | 992 | **-400.24** | -0.4035 |

The sign is the opposite of the hypothesis - the cap binds on the events where
this book does relatively WELL - and I am **refusing to grade it**. It is
confounded at least three ways: the cap binds where the book has the most
signals, which is where it has the most conviction; capped events are a
crypto Up/Down cluster rather than a random sample; and after 046's design
effect those 330 closes are on the order of 15 independent market-sides, which
is not a sample by any bar in this project. It is recorded so nobody
re-measures it and mistakes it for news. NOT_TESTED (convention 11).

## Why this might be wrong

**Two breaches is not five, and I have written a bar of five into the kill
condition for that reason.** Both of today's breaches are in one book, hours
apart, on one epoch. A stationary-drift story that explains two points is
barely a story. If the next three breaches land at 2 sigma, this proposal is
TESTED_FAILED and the level limit is doing exactly its job.

**The rate may not be stationary.** I have computed one mean and one sd from
12 hours of one epoch and compared a second book's 8 hours to it. The two
agree closely, which is the strongest evidence I have, but two agreeing
samples from two books running overlapping strategies in the same 5-minute
crypto markets during the same overnight session are not independent in the
way that agreement implies. If the rate is regime-dependent - and the -86.18
and +53.94 hours suggest something is - then "hours to the line" is an average
that no particular epoch experiences.

**A timer may be an acceptable control.** There is a coherent position that on
a book with no demonstrated edge, a limit that stops trading after roughly
half a day of uptime is a FINE thing to have, whatever it is named. I have
some sympathy for it. The objection is only that it is currently named a
drawdown limit and read as a risk signal, so a resume decision gets made as
though a breach carried information about the book's state. It does not. If
the timer is wanted, it should be a timer, and then the resume question has an
obvious answer instead of a hard one.

**The sigma arithmetic is first-order and I know it is wrong in a knowable
direction.** Hours are clustered by market-side, so the true sigma is larger
and every sigma in this document is an upper bound on how anomalous anything
looks. I have used it anyway because the conclusion survives the correction:
0.36 sigma only gets smaller.

## What past failure this addresses

The one CLAUDE.md names as the standing correction, in a new place: execution
is ~9% of the loss and the model is 91%, and no amount of exit engineering
touches it. A drawdown limit is exit engineering at the portfolio level. It
stops the book after the loss has happened, at a level it will always
eventually reach, and it produces a governance event that reads like new
information and is not.

It also addresses convention 11 in the direction people forget: NOT_TESTED is
a result. Two breaches at 0.36 sigma do not establish this thesis and the kill
condition says so explicitly and sets a bar of five. The instrument is worth
building before the third breach rather than after, because the numbers it
needs - `capital_at_risk` at the breach instant, the epoch's own hourly
distribution, the market-side counts - are cheapest to capture while the
breach is happening.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  work is proposal 048 filed this cycle (the ratio's composition), D-343
  (which created the constraint), D-356 R4 (`dip_arb`), the vault's
  `fair_value_arb` TESTED_FAILED verdict, and proposals 039/043/046/047 which
  already instrument the three exit channels this decomposition surfaces.

## RULING NOTE - D-380 R2 (Raven, recorded 2026-08-20; BUILT the same session)

**ACCEPTED and BUILT, exactly as written. No re-scope, no kill-condition
change.** It moves no limit, no halt state, no restart and no strategy; it adds
one read-only reporter and informational fields on future breach payloads.

Built as `backtest/drawdown_attribution.py` with `tests/test_drawdown_attribution.py`
(35 tests, constructed fixtures - all three books are live and cannot be
rewound). All four holds are met:

1. **Per-epoch statistics with cluster counts.** Epoch start, close span and
   snapshot span, realised `sum(pnl_net)`, realised USD/hour, full-hour mean and
   sd, hours-to-limit at the epoch's own mean rate, and the sigma distance
   between the limit and the epoch-mean path. Market-side counts print beside
   every close count; a test walks the rendered output and fails on any line
   carrying a close count without one (046).
2. **Composition** by `strategy_id` and `exit_reason`, losers and winners as
   separate subtotals, labelled an ACCOUNTING decomposition in the human output
   AND in the `--json` payload. **Rule 4 is enforced against the source, not by
   convention:** a test parses the module's AST and fails if any SQL literal
   filters a `strategy_id` OUT or any identifier offers a
   counterfactual/without/exclude helper.
3. **Payload enrichment landed in `engine/risk/events.py`**, which is the file
   that actually owns the `portfolio_drawdown` write - not `constraints.py`
   (pure and database-free by design) and not `shadow_loop.py`. Both the denial
   row and the halt row gain `sigma_observed`, `sigma_at_limit`,
   `hours_to_limit` and the epoch counts. The import is lazy and the whole thing
   is wrapped so it can only ever ADD fields: a breach whose annotation failed
   is still recorded, which a test pins.
4. **Harness 21/21 rc 0; suite 4,257 passed / 1 skipped / 0 failed**, 395s,
   re-derived in-session 2026-08-20 ~14:55 EDT (never quoted from a doc,
   convention 25).

**Reproduction check.** The reporter reproduces this proposal's pinned epoch
exactly when restricted to its original read window: 1,322 closes, -367.6491
USD, 12.4571 h span, -29.5133 USD/h, 12 full hours, mean -29.0558, sd 40.6836,
and the limit sitting 0.364 sigma beyond the mean path. The proposal's own
figures were 1,322 / -367.65 / 12.46 / -29.51 / -29.06 / 40.68 / 0.36.
`hours_to_limit` reads 13.77 rather than the proposal's 13.6 because the
instrument uses the epoch's own hourly MEAN, which is what hold 1 specifies;
the proposal divided by the close-span rate. Same clock, stated definition.

**STATUS STAYS NOT_TESTED, and the bar has NOT moved.** Five enriched breaches
are still required and there are **zero** - the ten `portfolio_drawdown` rows
already on `db/trading.db` predate the enrichment and are reported as
PRE-ENRICHMENT rather than counted as breaches that read low. An unmeasured
breach is not a quiet one (convention 11).

**Caveat recorded now rather than discovered later.** `SHADOW_RISK_LIMITS` sets
`max_drawdown_frac=1.0` (D-359 / A-17) and `drawdown_frac()` is bounded above by
1.0, so the `portfolio_drawdown` constraint **cannot fire on any shadow book
today**. Hold 3's enrichment is correct but DORMANT on all three books; it goes
live only under the real-money `DEFAULT_LIMITS` (0.25) or if a future ruling
lowers the shadow limit. **The 5-breach bar therefore cannot advance while the
shadow limit stands at 1.0**, and the 14-day requeue clause in the kill
condition will expire against a limit that is switched off. That is a fact
about the book, not a defect in the instrument, and it is Raven and Aym's to
decide - this note changes nothing.

The reporter prints the live shadow limit read FROM SOURCE on every run, so the
caveat can never go stale against this file.
