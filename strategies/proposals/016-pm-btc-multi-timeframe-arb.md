---
name: "pm_btc_multi_timeframe_coherence"
thesis: "A probe, not an edge claim: measure whether the BTC 5m, 15m and hourly Up/Down markets are ever jointly priced outside the arithmetic bounds their nesting imposes, and record the two facts found while scoping it, that the hourly market settles on a different reference than the 5m and 15m markets and that the 5m/15m leg is already covered by proposal 005 and by PM_corridor_pair."
expected_edge_bps: null
kill_condition: "Retire this probe if any one of: fewer than 10 joint quote observations in 30 days where the three-market coherence bound is violated by more than 2c after fees, measured over the roughly 8,640 5m windows in that period by backtest/polymarket_harness.py; or hourly-market traded volume stays below 500 dollars per contract over 30 days, measured from the Gamma volumeNum field, since a bound violation on an untraded book is not a fill; or the 5m/15m leg is found to duplicate proposal 005 or PM_corridor_pair on inspection, which the body already argues it does."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "NO ENTRY RULE IS PROPOSED. This is kind=experiment and its deliverable is a measurement, not a position. The probe: on each 5m window boundary, record the best bid and ask for btc-updown-5m-{ts}, for the containing btc-updown-15m-{ts15}, and for the containing hourly bitcoin-up-or-down-{date}-{hour}-et market, along with the Binance.US 1m close and the 60s TWAP proxy strike for each window open. Compute the coherence bound described in the body and log every violation with its size in cents, the fee-adjusted size, and the quoted depth at the touch. Any strategy that trades this must be proposed separately after the measurement exists, because writing an entry rule before the bound has ever been observed to break is condition discovery by scanning (convention 4)."
data_requirements: "CLOB books for three market families simultaneously. BLOCKER: btc-updown-1h-{ts} DOES NOT EXIST. Verified 2026-08-18 against the Gamma API at three consecutive hourly boundaries, with and without the closed filter, plus a full enumeration of active markets which returned only 5m and 15m durations across 8 assets. The hourly BTC Up/Down market exists under a DIFFERENT slug family, bitcoin-up-or-down-<month>-<day>-<year>-<hour><ampm>-et, and requires a date-and-hour slug builder that this repository does not have. BLOCKER: that hourly market has cryptoMarketConfig of null and settles on the Binance BTC/USDT 1 hour candle open versus close, NOT on the Chainlink 60s TWAP that btc-5m-twap-60 and btc-15m-twap-60 both use. The three markets are not measuring the same quantity. BLOCKER: the hourly market is effectively untraded. Two open contracts inspected showed volumeNum of 26.76 and 29.00 dollars against quoted liquidity of 4,186 and 16,250, after roughly two days of listing. BLOCKER: no paired multi-timeframe history exists, which is the same blocker that has kept proposal 005 unbuilt since it was written."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard; every Polymarket strategy is NOT_TESTED per D-268 (convention 11). The 5m/15m leg of this proposal OVERLAPS proposal 005 pm_cross_window_relative_value, which is PROPOSED and UNBUILT, and it also overlaps the built PM_corridor_pair (corridor_pair_live.py, renamed under D-281 precisely so no row could be read as a measurement of 005). Per the README, no result from PM_corridor_pair is evidence for or against 005 in either direction, and the same applies to this. The body carries the required comparison table and the recommendation that the 5m/15m leg be FOLDED into 005 rather than duplicated here."
kind: experiment
status: PROPOSED
source: "forge, cross-market crypto sweep 2026-08-18"
forge_warnings: "1h market does not exist at the assumed slug; hourly settles on a different reference; 5m/15m leg duplicates 005; recommend folding"
---


## Read this section first: I am recommending against most of this proposal

I was asked to propose a BTC 5m plus 15m plus 1h multi-timeframe arbitrage. I
verified the markets before writing the argument, and the verification changed
the answer. This document exists to record what I found and to stop the idea
being re-proposed under a seventeenth number in three weeks. It is filed as
`kind: experiment` with a null edge, because convention 11 says an unknown edge
is recorded as unknown and never invented, and `agents/forge.py` refuses an
experiment that names a bps figure.

**My recommendation: fold the 5m/15m leg into proposal 005 and do not build the
1h leg at all.** Reasoning below.

## What I verified against the Gamma API

Read only, no orders, 2026-08-18 around 09:39Z.

**btc-updown-15m-{ts} EXISTS and is active.**

    slug   btc-updown-15m-1787045400
    active true, closed false
    cryptoMarketConfig  {'id': 'btc-15m-twap-60', 'asset': 'btc',
                         'duration': '15m', 'twapEnabled': True,
                         'twapLookbackSeconds': 60}
    bid 0.74  ask 0.75  liquidityNum 8,426.63  volumeNum 386.02

Same 60 second Chainlink TWAP mechanic as the 5m market. `ts` is the window open
second floored to a 900 second boundary, which is what
`engine/polymarket/context.py` already does with `BTC_UPDOWN_15M_SLUG` at
line 37 and `BTC_UPDOWN_15M_DURATION`. That wiring is correct and already built.

**btc-updown-1h-{ts} DOES NOT EXIST.** Three separate checks:

1. Direct slug probes at four consecutive hourly boundaries, for `1h`, `1hr` and
   `60m`, with and without `closed=true`. Zero results in every case.
2. Enumeration of active non-closed markets ordered by `startDate` descending.
   Every `cryptoMarketConfig` returned had a `duration` of either `5m` or `15m`.
   No other duration exists. The assets carrying those durations are btc, eth,
   sol, bnb, doge, hype, xrp and zec, five 5m windows and one 15m window listed
   ahead per asset.
3. A search for "up or down 1h" surfaced the hourly family under an entirely
   different naming scheme.

**An hourly BTC Up/Down market DOES exist, under a different slug family and a
different settlement.** Confirmed open at the time of writing:

    slug   bitcoin-up-or-down-august-18-2026-11am-et
    slug   bitcoin-up-or-down-august-18-2026-12pm-et
    active true, closed false, acceptingOrders true
    cryptoMarketConfig  None
    outcomes ["Up", "Down"]
    11am contract: bid 0.49 ask 0.52 spread 0.03
                   liquidityNum 4,185.99   volumeNum 26.76
    12pm contract: bid 0.50 ask 0.51 spread 0.01
                   liquidityNum 16,249.51  volumeNum 29.00

Its resolution text, quoted from the Gamma description field:

> This market will resolve to "Up" if the close price is greater than or equal to
> the open price for the BTC/USDT 1 hour candle that begins on the time and date
> specified in the title. [...] The resolution source for this market is
> information from Binance, specifically the BTC/USDT pair.

Three things follow, and each is independently disqualifying.

**First, the settlement reference is different.** The 5m and 15m markets settle
on a Chainlink 60 second TWAP. The hourly settles on a Binance BTC/USDT 1 hour
candle, open versus close, on a single exchange, with no TWAP smoothing and with
ties resolving Up. Two markets that appear to ask "is BTC up over this interval"
are measuring the interval with different instruments. Any "coherence bound"
across them would be partly a bound and partly a basis between two settlement
references, and there is no way to tell which part a violation came from. That
alone breaks the arithmetic the proposal was supposed to rest on.

**Second, the market is untraded.** 26 to 29 dollars of volume after roughly two
days of listing (`startDate` 2026-08-16T15:00Z for a candle beginning
2026-08-18T15:00Z). Quoted liquidity in the thousands is not takeable depth, and
a coherence violation on a book nobody trades is a screenshot, not a fill.

**Third, the listing window is wrong for the trade.** The hourly contract is
listed roughly 48 hours before its candle even begins. For almost its entire
listed life there IS no window open, so there is no displacement, no strike, and
nothing for a nesting bound to bind against. The bound only becomes meaningful in
the final hour, which is the only hour anyone would trade, and it is the hour
with the least listing history.

## The 5m/15m nesting, and why the structural version is already built

The genuinely model-free relation in the 5m/15m nest is worth writing out,
because it is the only thing in this proposal that is not just proposal 005.

A 15m window contains exactly three 5m windows. If r1, r2, r3 are the log returns
of those three windows, then

    15m Up   is exactly   r1 + r2 + r3 > 0
    5m_i Up  is exactly   r_i > 0

During the THIRD 5m window, r1 and r2 are already known. The 15m contract's only
remaining uncertainty is r3, and

    15m Up   is exactly   r3 > -(r1 + r2)
    5m_3 Up  is exactly   r3 > 0

Two contracts, one random variable, two strikes. If r1 + r2 > 0, then
-(r1 + r2) < 0, so buying 15m Up at price a and 5m_3 Down at price b pays at
least 1.00 in every state and 2.00 on the overlap where -(r1+r2) < r3 <= 0.
Therefore a + b < 1.00 is a riskless arbitrage before fees.

**That is corridor_collector, exactly.** Proposal 005's own
`related_graveyard_findings` describes it as buying "the leading side of the 15m
AND the opposite side of the final 5m, so at least one leg always wins and the
pair is floored at 1.00". The derivation above IS that description, and
`PM_corridor_pair` is its live implementation.

Outside the third window, when all three returns are unknown, the nesting gives
only weak joint-distribution bounds and needs a model to sharpen into a signal.
Sharpening it with a model is proposal 005.

So the 5m/15m leg has no room left in it. The structural half is built and the
model half is proposed.

## Why this is not corridor_collector and not proposal 005

The comparison table proposal 005 owes its reader, extended to three columns
because there are three things here now.

| | PM_corridor_pair (built) | 005 pm_cross_window_relative_value (PROPOSED, unbuilt) | this (016) |
|---|---|---|---|
| Legs | 2, opposite sides | 1 | 0, no position is proposed |
| Worst case | pair floored at 1.00, a leg always wins | lose the full premium | none, it does not trade |
| Bet | structure | mispricing | no bet, a measurement |
| Needs a fair-value model | no | yes | no, a coherence bound is model free |
| Markets | 5m and 15m | 5m and 15m | 5m, 15m and hourly |
| What is genuinely new | nothing new here | nothing new here | only the hourly leg, which is blocked |
| Kind | strategy | edge_hypothesis | experiment, null edge |

Read the row that matters: the only column entry where 016 is not a restatement
of something that already exists is the hourly leg, and the hourly leg is blocked
on a settlement mismatch, on volume, and on a slug builder we do not have.

## Recommendation

1. **Fold the 5m/15m portion into proposal 005.** Do not build it here. 005 is
   already the right home for a model-based 5m/15m relative value bet, it already
   carries the correct blocker (30 days of paired history, and a mean and stdev
   that are measured quantities rather than constants), and splitting the same
   idea across two proposal numbers would guarantee that a future reader pools
   two documents into one apparent result.

2. **Do not build the 1h leg.** Revisit only if all three change: the hourly
   family adopts a TWAP settlement matching the 5m and 15m markets, its volume
   clears the 500 dollars per contract bar in the kill condition, and the listing
   window tightens so a contract is not quoted 48 hours before its candle opens.

3. **Keep the measurement.** If anybody wants the number, run the probe in
   `entry_exit_rules` and log violations. It is cheap, read only, and it settles
   the question with data instead of another proposal. But it is a measurement
   task, not a strategy, and that is why this file records a null edge.

4. **Do not delete this file if it is rejected.** README, lifecycle section: a
   REJECTED proposal stays, because deleting it loses the record and the next
   Forge run proposes it again. The verified fact that `btc-updown-1h-{ts}` does
   not exist is the single most reusable thing in this document.

## A gap this exercise did surface, filed here so it is not lost

The Gamma enumeration turned up 5m and 15m Up/Down markets for **eight** assets,
not one: btc, eth, sol, bnb, doge, hype, xrp and zec, all with
`twapLookbackSeconds` of 60. `engine/polymarket/context.py` currently resolves
btc only. Whether that is a gap worth closing depends entirely on whether
proposals 014 and 015 survive their own blockers, and it is not a proposal in
itself. Recording it so the enumeration does not have to be redone.

## What would make this wrong

1. **The hourly family changes.** Polymarket relisted these markets before. If a
   `btc-updown-1h-{ts}` with a `btc-1h-twap-60` config appears, the settlement
   mismatch evaporates and the first blocker with it. Re-verify before believing
   this document; it is a snapshot of 2026-08-18, not a permanent fact.

2. **The volume figure is measured wrong.** I read `volumeNum` from Gamma on two
   contracts at one instant. If `volumeNum` on this family excludes some venue or
   is reported on a different basis than on the 5m markets, 26 dollars could be
   an artefact. That is why the kill condition specifies a 30 day measurement
   rather than resting on the two readings I took.

3. **My nesting derivation is wrong about tie handling.** I asserted
   `15m Up` is exactly `r1 + r2 + r3 > 0`. If the 5m and 15m TWAP markets resolve
   ties Up, as the hourly market's text explicitly does, the boundary case
   `r3 = -(r1 + r2)` sits on the wrong side of one of my inequalities and the
   overlap interval is half open rather than open. That does not change the
   conclusion, since the pair still pays at least 1.00, but the exact bound
   should be re-derived from each market's own resolution text before anyone
   trades a violation of it.

4. **The observed 15m price at 0.745 against the 5m at 0.505 looks like a huge
   gap and is not one.** At the moment I sampled, `btc-updown-15m-1787045400` was
   about ten minutes into its window with BTC up from its open, while
   `btc-updown-5m-1787045700` had opened five minutes later at a fresh reference
   and sat at a coin flip. The two contracts measure displacement from DIFFERENT
   open prices. Anybody scanning for cross-window "divergence" without
   normalising for the different reference opens will find enormous fake signals
   constantly. This is the single most likely way a multi-timeframe strategy
   fools itself, and it is worth stating even in a proposal I am recommending
   against.

## Entry price and win rate (D-267)

Not applicable and deliberately so. This proposal takes no position, so it has no
entry price and no win rate, and quoting either would be inventing a number for a
strategy that does not trade. If the probe ever becomes a strategy, that proposal
must state both together or neither.
