---
name: "pm_twap_settlement_regime_unidentifiable"
thesis: "External Signal 1 is the largest venue-structural change the shadow stack has been handed: since 2026-08-07 00:00 UTC, Polymarket crypto Up/Down markets (5m, 15m, 4h across BTC ETH SOL XRP DOGE BNB ZEC HYPE) settle their CLOSE on a 60-second Chainlink TWAP instead of a single print, the opening price stays a single instant, hourly markets are excluded, and Stanford-documented single-print sniping (821 wallets, ~8.2M USD) is eliminated venue-wide. The cycle brief asks for a TWAP amendment to the resolution-exit family on the stated premise that 'the settlement regime changed under every hold-to-resolution strategy on 2026-08-07 and the shadow stack's exit economics were calibrated pre-TWAP', and it correctly instructs the reasoner to VERIFY which markets in the 043 sample are TWAP-affected before claiming anything. Verified, and the verification closes the direction rather than opening it. The premise is false in the only sense that could be measured: NOTHING in this system was calibrated on pre-TWAP data, because this system has no pre-TWAP data. Measured read-only at 2026-08-20T04:24Z, the earliest position in db/trading.db opened 2026-08-18T03:02:21Z and the earliest in db/trading-survivors.db opened 2026-08-19T04:40:54Z; positions opened before the 2026-08-07 boundary number ZERO in both books, out of 3,803 and 2,053 respectively. The entire book postdates the regime change by eleven days. That kills the time-series identification outright, and it kills the brief's priority-3 maker split with the same number: of 75 maker fills in trading.db (env B has none), 0 predate TWAP, so a pre/post-Aug-7 maker markout comparison has an empty arm and is not a measurement that can be run at any future n. The cross-sectional identification fails too, and this is the half that had to be checked rather than assumed. TWAP covers crypto Up/Down 5m/15m/4h and excludes hourly and non-crypto. Our universe is 3,738 5m plus 61 15m plus 4 non-crypto in trading.db, and 2,044 5m plus 5 15m plus 4 non-crypto in survivors: hourly crypto is n=0 in both books, so the venue's own natural control group is one we do not trade, and the non-crypto arm is 8 weather positions across both databases of which exactly ONE has ever closed (+7.353, survivors, the same single trade proposal 044 already refused to read as evidence). So 99.9% of the book is TWAP-covered, the uncovered remainder is n=1 closed, and there is no contrast in either dimension. A regime change that applies uniformly to every observation in a dataset is absorbed into that dataset's baseline and cannot be separated from it: every per-share figure this stack has ever produced - the -0.0231 pooled calibration, the salvage-vs-hold delta in 043, temporal_arbitrage's +9.55 and -9.26, the exit asymmetry the brief re-tabulates every cycle - is already a post-TWAP measurement and always was. This is not a small caveat to a TWAP analysis; it is the finding that there is no TWAP analysis to be had here, and recording that with numbers is worth more than an amendment that would have to assume the contrast it lacks. What remains TRUE and worth carrying is narrower and purely mechanical: the salvage floor is a PRICE trigger (SALVAGE_FLOOR = 0.10, strategies/polymarket/fair_value_settlement_exit.py:271) with no time argument, and its module docstring justifies itself on the terminal claim that 'a losing binary share settles at 0.00', which is a statement about the settlement distribution and is now a statement about a TWAP-averaged one. Nothing in that file, or anywhere in engine/, references TWAP, averaging, or a settlement window - grepped this session. That is a genuine model-versus-venue gap. It is also NOT gradeable here, for the reason above, and it must not be laundered into a strategy change on the strength of a press-reported mechanic and zero contrastive data."
expected_edge_bps: null
kill_condition: "This proposal creates no strategy, changes no parameter, and records no edge (convention 11). It records a REFUSAL of the TWAP-identification direction and the numbered conditions that would reverse it. The direction stays refused, and no TWAP-conditioned strategy or exit change is written, until ONE of the following is measured by `agents/forge_shadow_eval.py` over `positions` in a single named database, never pooled across the two (convention 32): (1) A SINGLE-PRINT CONTROL ARM reaches 200 or more RESOLVED closes on TWAP-excluded markets - `pair NOT LIKE '%updown%'` (non-crypto) or `pair LIKE '%-1h-%'` (hourly crypto), counted separately and never summed with each other. Measured 2026-08-20T04:24Z that arm is 1 closed position across BOTH books and 0 hourly in either, so this condition is 199 closes away in a universe the loop does not currently trade. It is stated as a condition and explicitly NOT as a request: do NOT add hourly or non-crypto markets to the universe in order to satisfy it, because buying a control arm with real allocation in a book that is bleeding is a cost this proposal has not priced and is not authorised to incur. (2) The venue changes the settlement mechanic AGAIN - reverts the 60s window, re-times it, or extends TWAP to hourly - AND the changeover timestamp is recorded BEFORE the fact in `docs/DECISIONS.md`, AND 14 or more days of closes accumulate on each side of it in the same database under the same strategy set. The timestamp must be recorded in advance because a boundary discovered after the fact and fitted to a P&L series is a break the analyst chose, not one the venue imposed. (3) A pre-2026-08-07 position appears in either database by a route other than a backfill - which cannot happen for a shadow book that started on 2026-08-18, and is listed only so the condition set is exhaustive rather than convenient. If none of the three is met within 30 days, record the TWAP direction as NOT_TESTED and requeue (convention 11) - NOT as refuted. Nothing here says TWAP failed to change the tail distribution of hold-to-resolution positions; the venue's own description says it did, and the mechanism is sound. It says our instrument cannot see the change because it has no observation from the other side of it, and it never will have one."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This proposal writes NO strategy, changes NO entry rule, NO exit rule, NO sizing, NO gate, NO parameter, and creates no table, feed or script. It exists so that the TWAP direction is closed with measurements and reversal conditions attached rather than re-proposed every cycle the external signal recirculates - the failure mode proposal 044 was filed against for the weather signal, two cycles running.
  1. Do NOT amend proposals 038, 039 or 043 to add a TWAP conditioning term, a pre/post split, or a regime dummy. Every one of those instruments would be splitting a sample that is 100% on one side of the boundary. A split with an empty arm does not return a null result, it returns the unsplit number with a misleading label attached, and the label is what the next session would carry forward.
  2. Do NOT re-derive or re-report the brief's priority-3 maker pre/post-TWAP split. It is n=0 pre against 75 post in trading.db and 0 against 0 in survivors. Proposal 042 remains the correct maker instrument and its markout measurement is unaffected by this refusal - what is refused is specifically the pre/post-Aug-7 CUT of it, not the instrument.
  3. Do NOT change `SALVAGE_FLOOR`, the profit target, the price stop, or any exit threshold on the strength of the TWAP mechanic. The gap between the salvage floor's terminal reasoning and the venue's current settlement mechanic is real and is recorded in the thesis, but a parameter moved on an unmeasurable mechanism is a forecast wearing a structural costume, and D-342 R5 governs: a forecast-free strategy is one whose payoff is guaranteed by an IDENTITY. 'TWAP thins the tail, therefore lower the floor' is not an identity, it is a directional bet on a distribution nobody here has measured on both sides.
  4. Record, do not act on, the one asymmetric mechanical fact that survives: the TWAP averages only the CLOSE, while the OPEN is still a single instant. So the two ends of every crypto Up/Down window now have DIFFERENT price-formation mechanics, and any strategy whose thesis depends on open and close being symmetric draws on a symmetry that no longer holds. `PM_temporal_arbitrage` is the family this would touch first because it is the calibration instrument keyed to window timing. This is flagged for a future session that has a control arm; it is not actionable now and rule 0 applies.
  5. Any future TWAP work states its identification strategy in its thesis BEFORE its numbers, and names the arm that provides the contrast. This rule exists because this proposal's own first framing was a 043 amendment, and the amendment was two paragraphs of drafted mechanism before the zero-pre-TWAP count was run. The count took one query and closed the direction. Run the count first.
data_requirements: |
  HAVE, verified read-only in both databases at 2026-08-20T04:24Z: `positions.opened_ts` (milliseconds), `positions.pair` carrying the market slug with its duration token (`-5m-`, `-15m-`) and its window-start epoch, `positions.closed_ts`, `qty`, `exit_px`, `exit_reason`, `pnl_net`, `fill_was_maker`, and `strategy_id`. Every count in the thesis came from these and nothing else. The TWAP boundary 2026-08-07T00:00:00Z is epoch_ms 1786060800000; the comparison is a single WHERE clause and returns 0 in both books.
  HAVE: the duration census that decides the cross-sectional half. trading.db 3,738 `-5m-` / 61 `-15m-` / 0 `-1h-` / 4 non-crypto; survivors 2,044 / 5 / 0 / 4. The `-1h-` count being exactly zero is the load-bearing number, because hourly is the one crypto duration the venue EXCLUDED from TWAP and is therefore the only natural control that shares the asset, the book structure and the strategy set.
  MISSING, and it is the whole finding rather than a gap to be filled: any observation from before 2026-08-07. This cannot be backfilled. `market_tape` starts inside the same window, `candles` stops 2026-08-11 and carries no Polymarket book, and proposal 038's `--backfill` recovers RESOLUTIONS for markets we traded, not markets we did not trade in a period when this system did not exist. There is no route to a pre-TWAP observation and no future n produces one.
  MISSING, non-blocking, and NOT requested: a single-print control arm with a usable sample. It is 8 weather positions across both books with 1 close. Reversal condition 1 names 200 resolved closes and rule 0 of the kill condition explicitly declines to buy them.
  NOT NEEDED: the 60-second TWAP window's exact composition, the Chainlink feed, per-second spot tape, or `market_tape` in any form. They would matter for a strategy conditioned on TWAP-window progress; no such strategy is proposed, and none can be graded here for the identification reason above.
  NOT NEEDED: the dynamic taker fee schedule or proposal 040's regrade. 040 is a separate venue-structural item on the same 15m universe and this proposal neither uses nor contradicts it. Both remain UNVERIFIED press-reported mechanics and neither is built on.
markets: "Polymarket crypto Up/Down. TWAP-covered per Signal 1: 5m, 15m and 4h. TWAP-excluded: hourly crypto and all non-crypto. Our traded universe measured 2026-08-20T04:24Z is 98.3% 5m and 1.6% 15m in db/trading.db and 99.6% / 0.2% in db/trading-survivors.db, both entirely inside the covered set, with 0 hourly and 4 non-crypto positions each. Each database is reported SEPARATELY and the two are never pooled (convention 32)."
kind: governance
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning, validator_kind_not_registered"
---

> **This proposal REFUSES the direction the cycle brief listed as priority 1**,
> and refuses priority 3 with the same measurement. The brief's premise is that
> the shadow stack's exit economics were calibrated pre-TWAP and that the
> regime changed underneath them. Measured: **0 of 3,803 positions in
> `db/trading.db` and 0 of 2,053 in `db/trading-survivors.db` opened before
> 2026-08-07.** The stack was born eleven days after the change. There is no
> pre-TWAP arm, there never will be one, and the venue's own natural control -
> hourly crypto, which TWAP excludes - is **n=0** in both books.

> **This is not a finding that TWAP did not matter.** The venue says it changed
> the settlement mechanic and the mechanism is sound. It is a finding that this
> instrument has no observation from the other side of the boundary, so the
> change is absorbed into our baseline rather than measurable against it.

## The two identification strategies, and how each one fails

There are exactly two ways to measure what a regime change did: compare across
the boundary in TIME, or compare covered against uncovered in the CROSS
SECTION. Both were checked before anything was drafted.

**Time.** The boundary is 2026-08-07T00:00:00Z. The earliest position in
`db/trading.db` opened 2026-08-18T03:02:21Z; in `db/trading-survivors.db`,
2026-08-19T04:40:54Z. Pre-boundary count: zero, zero. The gap is not marginal -
it is eleven and twelve days - so no sampling decision, no widened window and
no future accumulation reaches back across it.

**Cross section.** TWAP covers crypto Up/Down at 5m, 15m and 4h. It excludes
hourly crypto and everything non-crypto. Our books hold 0 hourly positions and
4 non-crypto positions each, of which 1 has ever closed. So the uncovered arm
is a single closed weather trade, which proposal 044 already recorded as not a
sample.

A change that applies to every row in a table is not a variable. It is part of
the table's definition, and it cannot be regressed against anything.

## What this costs, stated plainly

It costs the cycle its headline. The brief called Signal 1 "the single most
important item" and "EXTERNAL and structural", and it is both of those things
for the venue. What it is not is *measurable by us*, and the distinction
between a change that matters and a change we can see is the distinction
convention 11 was written to protect. Recording "we cannot see this, here is
the count, here is what would let us" is a result. Writing a TWAP amendment
that splits a one-sided sample would have produced a document with the word
TWAP in it and no more information than we started with, and the next session
would have inherited the label as though it were evidence.

## The one real gap it leaves open

The salvage floor's justification is a claim about terminal distributions. Its
module docstring at `strategies/polymarket/fair_value_settlement_exit.py`
argues that "a losing binary share settles at 0.00, so selling at 0.10 recovers
10c the position would otherwise lose", and `SALVAGE_FLOOR = 0.10` is a flat
price trigger with no time argument at all. Under single-print settlement a
share at 0.06 with two minutes left retains real flip optionality. Under a 60s
TWAP close, optionality decays differently - and specifically it decays as the
averaging window fills, which is a mechanic the trigger does not know exists.
Grepped this session: no file in `engine/` or `strategies/` mentions TWAP,
averaging, or a settlement window.

That is a real model-versus-venue gap and I am recording it rather than
resolving it, because resolving it requires knowing how the tail actually
changed, which requires the contrast this proposal has just shown we do not
have. Proposal 046, filed this cycle, addresses the adjacent and *answerable*
question - whether the instrument that grades the salvage floor can support a
verdict at all - which is prior to it.

## Why this refusal might be wrong

The strongest objection is that I have taken "identifiable" to mean
"identifiable by contrast", and contrast is not the only route. A sufficiently
specified structural model of TWAP settlement could predict a point value for
the settle rate of a share salvaged at price s with t seconds left, and that
prediction could be tested against post-TWAP data alone, with no pre-TWAP arm
at all. That is a legitimate identification strategy and this proposal does not
have it. What it would require is a model of the underlying spot process, which
is a forecaster, which D-342 R5 puts outside the structural family. So the
route exists and it leads somewhere we have already decided not to go; if a
future session disagrees with that boundary, this refusal is where it should
argue, not at the counts.

Second, I have treated the 4h duration as untraded on the strength of the slug
census, and 4h markets would carry a `-4h-` token that my duration bucket would
have caught. It found none. But the census keys on the slug string, and a
market whose slug does not follow the `-<dur>-` convention would fall into the
non-crypto bucket and be miscounted. The non-crypto bucket is 4 positions in
each book and I read all 8 of them individually - they are all weather - so the
error, if it exists, is bounded at zero here. It would not stay bounded if the
universe widened.

Third, and cutting hardest: a control arm is 199 closes away, and reversal
condition 1 declines to buy it. That is a judgement about cost, not a
measurement, and a reader who thinks a TWAP-versus-single-print contrast is
worth 200 hourly-market closes of allocation should overrule it. I have written
the condition so that it can be overruled explicitly rather than drifted into.

## What past failure this addresses

It addresses the failure proposal 044 was filed against and names it as a
recurring shape: an external signal arrives with high confidence and real
corroboration, the brief promotes it to priority 1, and the natural next step
is a proposal that treats the signal's importance to the VENUE as evidence
about OUR book. 044 refused the weather information-lag probe because our own
instrument had performed the relevant comparison 2,614 times and found nothing.
This refuses the TWAP amendment because our own instrument cannot perform the
comparison at all. Different reason, same discipline: the external claim is not
the measurement, and the check is one query.

It also addresses the standing correction that proposal 040 wrote for the
dynamic taker fee - another press-reported venue-structural change on the same
15m crypto universe, whose default the stack deliberately left at 0.0 "until a
venue-sourced schedule exists". TWAP gets the same posture for the same reason,
and the two now form a pair: two real venue changes, both unverified from
inside the system, neither built on.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  work is proposals 038, 039, 040, 042, 043 and 044, cited by measurement.
- **validator_kind_not_registered**: `kind: governance` was registered in
  `agents/forge.py` at `c73d23c` (2026-08-19 15:57 EDT), added to both `KINDS`
  and `NULL_EDGE_KINDS`, so this warning is retained as a LABEL for continuity
  with 041 and 044 and is believed NOT to be live. Not re-verified by reading
  `agents/forge.py` this session - CLAUDE.md records the commit and open items
  0 and 18 as closed on it, and this proposal did not re-derive that. Treat the
  warning as stale-pending-confirmation rather than as a claim (convention 25).
