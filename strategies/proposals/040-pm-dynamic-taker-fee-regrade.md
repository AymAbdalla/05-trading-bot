---
name: "pm_dynamic_taker_fee_regrade"
thesis: "Polymarket introduced a dynamic taker fee on 15-minute crypto markets, reported at up to ~3.15% on a 50-cent contract and redistributed to makers, explicitly to neutralise latency arbitrage. Our shadow models the taker fee as a flat 0.0 (`engine/polymarket/paper_adapter.py:220`, `DEFAULT_TAKER_FEE_RATE = 0.0`, whose own comment says 'a strategy whose edge is 2c per share dies the day that changes'). The obvious response - set the knob to 0.0315 - is wrong three times over, and each error is measurable rather than arguable. FIRST, the SHAPE is wrong, not just the number. The fee is charged at `paper_adapter.py:833` as `walk.cost_usdc * self.taker_fee_rate` and at `:994` as `proceeds * self.taker_fee_rate`: a flat fraction of notional with no price argument. A fee that peaks at 50/50 and falls toward the tails cannot be expressed that way at all. Re-grading db/trading.db's 521 taker closes since the 07:30 EDT cutoff under a flat 3.15% costs 150.50 USD; under a peaked schedule normalised to the same 3.15% at p=0.50 it costs 83.41. The two shapes disagree by 67.09 USD on the same 521 trades, which is 19% of the window's entire loss. Shipping the right rate in the wrong shape produces a wrong number carrying a config key, which is worse than a zero everyone knows is a placeholder. SECOND, there is no duration argument, and our book is almost entirely the duration the fee does NOT name. Read 2026-08-19: of 2,788 closed positions in db/trading.db, 2,757 are 5m and 31 are 15m (1.11%); of 1,208 in db/trading-survivors.db, 1,205 are 5m and 3 are 15m (0.25%). A single global rate would tax 5m markets the venue does not tax, and the current global zero under-taxes the 15m ones. The fee rate must be a function of (market duration, price), and the adapter already holds `market_slug` at both charge sites, so the key is in hand. THIRD, and this is the part nobody has written down: the fee's incidence is 1x on some strategies and 2x on others, and which one you are is already recorded. Settlement redemption charges NO exit fee - `paper_adapter.py` computes redemption P&L as `redemption - cost_usdc - fee_usdc`, entry leg only - while a round-trip sale is charged again at `:994`. Lifetime settlement share by strategy, db/trading.db: PM_fair_value_arb 7 of 976 (1%), PM_fair_value_arb_hft 5 of 376 (1%), PM_fair_value_arb_inverse 0 of 266, PM_fair_value_arb_wide 0 of 79, against PM_temporal_arbitrage 148 of 148, PM_mid_price_continuation 70 of 70, PM_streak_snapper 44 of 44 and PM_maker_rebate_quote_ladder 43 of 43, all 100%. The fair-value family round-trips and would pay the new tax TWICE; the hold-to-resolution families pay it ONCE at entry and nothing at redemption. So the venue's change does not tax our book uniformly - it double-taxes the 1,703 closed positions and -849.41 USD that are already the largest loss centre in the system, and single-taxes everything that holds. That is a forecast-free re-ranking of the strategy population, derived from an accounting identity in our own adapter rather than from any view about prices. What the re-grade does NOT show is a rescue: under the peaked schedule with settlement legs correctly charged once, db/trading.db since cutoff moves from -354.54 to -437.95 and db/trading-survivors.db from -445.37 to -586.37, and NOT ONE strategy changes sign in either database. The fee makes losers lose more. It is not why we are losing, and this repair must not be read as blaming the venue for a model whose own diagnosis is 91% model and ~9% execution."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11). Its success condition is a measurement, not a P&L, and it has three parts, all three required. (1) SCHEDULE FIDELITY: the adapter's fee function must reproduce the venue's published schedule to within 1e-6 USD per share at nine named prices - 0.02, 0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90, 0.98 - asserted in a unit test that fails on a flat rate. A flat rate passes at exactly one of those nine prices, so a test that passes on a constant is not this test and the repair is FAILED if it ships one. (2) DURATION SELECTIVITY: over 200 or more closed positions booked after the repair lands, the count of 5m positions charged a non-zero taker fee must be exactly 0, and the count of 15m taker positions charged a non-zero fee must equal the count of 15m taker positions, both reported as numerator and denominator (convention 20). Anything other than 0/N and N/N means the duration key is not wired and the repair is FAILED rather than tuned. (3) INCIDENCE SEPARATION: the re-grade report must state entry-leg fee and exit-leg fee as two separate columns per strategy, never one total, because the whole result of this proposal is that the two differ by strategy; a single pooled fee column is the same error as pooling maker and taker fills (convention 32) and the repair is FAILED if the report emits one. Measurement path: `agents/forge_shadow_eval.py --db db/trading.db` and `--db db/trading-survivors.db`, reported separately and NEVER crossed, since environment B runs a different strategy whitelist. If 200 closed positions have not landed within 7 days of the repair going live, record NOT_TESTED and requeue; do not grade a partial sample. SEPARATELY AND REGARDLESS: the repair is FAILED if the fee constant is changed from 0.0 before the venue's actual schedule is read from a venue source. A number taken from a press summary and hardcoded is a fabrication with a config key on it, and the whole point of the existing 0.0 is that it is an honest placeholder rather than a wrong guess."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope, stated first because this proposal is easy to misread as a strategy. It adds NO strategy, NO entry, NO exit and NO position, and it changes no entry or exit decision anywhere. It changes what a fill COSTS in the paper adapter's books, and it adds a re-grade instrument that answers "which strategies survive a fee" without running anything forward. It is filed here rather than as an engineering ticket because it silently re-ranks every proposal in this directory that assumes a zero taker fee.
  1. Do NOT change `DEFAULT_TAKER_FEE_RATE` in this change. It stays 0.0. The default is the venue's schedule as we can currently verify it, and 5m is 99% of the book and is reported as still fee-free. What changes is that the rate becomes a FUNCTION rather than a constant.
  2. Replace the two flat multiplications with one function, `taker_fee_usdc(price, shares, market_slug)`, called at `paper_adapter.py:833` (entry) and `:994` (round-trip exit). It takes price because the schedule is price-dependent, and the slug because it is duration-dependent. Both arguments are already in scope at both call sites; this is a signature change, not a plumbing change.
  3. The schedule is CONFIG, not code. One table keyed by market duration, each entry carrying a shape name and its parameters, defaulting to `zero` for every duration including 15m until a venue-sourced schedule is written down. `config.yaml` is NOT touched by this proposal - the table lands in the adapter's own defaults with a config override, and whoever writes the real numbers into config does it as a separate, reviewable change. That separation is deliberate: the code change is mechanical and safe, the numbers are a claim about the venue.
  4. Duration is derived from the slug, not guessed. The slugs are literal - `-5m-` and `-15m-` appear in every one of the 3,996 closed positions across both databases, and a slug matching neither is UNKNOWN. An UNKNOWN duration must raise, not default to zero and not default to the 15m rate. A fee model that silently treats an unrecognised market as free is how a fee model becomes wrong without anyone noticing, and this is the same fail-closed rule convention 11 applies to unreadable state.
  5. The maker side gets the mirror treatment and its OWN function, `maker_fee_usdc(...)`, because under the new regime the maker number is expected to be NEGATIVE - a rebate is a fee with a sign. `DEFAULT_MAKER_FEE_RATE` stays 0.0 for the same reason as rule 1. Do not model the rebate as a negative taker fee and do not net the two into one number; they are paid by different parties on different events, and proposal 042 is the instrument that will need them separated.
  6. The re-grade is a SCRIPT over closed positions, not a backtest and not a re-run. For every closed position it recomputes the fee under a named scenario and reports the strategy's P&L before and after, with entry-leg and exit-leg fees in separate columns per kill-condition part 3. It charges the exit leg only when `exit_px` is NOT 0.00 or 1.00, because those two values are settlement and settlement redeems rather than sells. Getting this wrong overstates the fee on exactly the hold-to-resolution families the thesis is about, which is the trap this rule exists to name.
  7. Scenarios are NAMED and reported together, never one number: `zero` (today), `flat_315` (3.15% of notional both legs, the naive reading), and `peaked_315` (rate(p) = 0.0630 * min(p, 1-p), which equals 3.15% at p = 0.50 and decays toward the tails). `peaked_315` is a GUESS at the shape and must be labelled as one in the output. The point of reporting all three is that their disagreement - 67.09 USD across 521 positions between the two non-zero shapes - is itself the finding that justifies rule 2.
  8. The re-grade reports 5m and 15m separately and never pooled. With 31 and 3 fifteen-minute positions respectively, the 15m arm is a count, not an estimate, and any per-strategy 15m figure must be printed with its n beside it so nobody quotes it.
  9. Do NOT re-grade across the 2026-08-19 07:28:34 UTC `fill_was_maker` boundary as if it were one sample. Rows opened before it carry a backfilled 0 that is indistinguishable from an observed taker fill, so a fee re-grade over the full history is charging taker fees to fills whose type is unknown. Report the observed era separately and mark the pre-boundary era's fee figures as UPPER BOUNDS.
data_requirements: |
  HAVE, verified read-only in db/trading.db and db/trading-survivors.db on 2026-08-19: `positions.entry_px`, `positions.exit_px`, `positions.qty`, `positions.pnl_net`, `positions.strategy_id`, `positions.pair`, `positions.closed_ts`, `positions.opened_ts` and `positions.fill_was_maker`. Those nine fields produced every number in the thesis and are the whole of what the re-grade script needs. No new column, no new table, no schema change.
  HAVE: `positions.fees` exists and reads 0.0000 on every closed position in both databases, which is the current model behaving exactly as documented rather than a bug.
  HAVE: the duration key is already in `positions.pair` as a literal `-5m-` or `-15m-` substring. Nothing needs the 15m keying change, `market_duration`, or any part of the ~03:45 EDT 2026-08-20 restart payload. This repair is deliberately independent of that restart and interacts with none of it.
  MISSING, and it is the single blocking unknown: THE ACTUAL SCHEDULE. What is known is a press-reported peak of approximately 3.15% at 50/50 on 15-minute crypto markets, redistributed to makers. What is NOT known is the functional form, whether the quoted percentage is of notional or of shares, whether it is charged on the buy leg, the sell leg or both, whether it applies to a sale that closes an existing position, and whether 5m markets are in scope at all. `peaked_315` in rule 7 is a fitted guess through one reported point and must never be cited as the venue's schedule. This is why rule 1 keeps the default at 0.0 and rule 3 keeps the numbers out of code.
  MISSING, non-blocking: whether the maker rebate is per-fill, pro-rata over a daily pool, or discretionary. Rule 5 creates the function; proposal 042 is where the number has to come from. A pro-rata daily pool cannot be modelled per-fill at all, and if that is the mechanism then the maker side of this repair stops at the signature and says so.
  NOT NEEDED: `market_resolutions` from proposal 038, the calibration tape, `market_tape.condition_id`. The re-grade reads settlement off `exit_px in (0.00, 1.00)`, which is already how settlement is recorded, and it needs no resolution it does not already have.
markets: "Polymarket crypto Up/Down, 5m and 15m, both databases, all strategies. The re-grade is over closed positions already booked; the adapter change applies to every future fill in every market the loop touches."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## Why this might fail

The most likely failure is that the schedule never gets sourced, and the
repair lands as a function with `zero` in every slot - correct, honest, and
doing nothing. That is a real outcome and it is still worth the change,
because the current code cannot express the fee even once someone knows it,
and the gap between "we do not know the number" and "we could not represent
the number" is the whole of this proposal. But nobody should record a landed
signature as a landed fee model.

The second failure is that `peaked_315` is simply the wrong curve and the
re-grade's headline numbers are fiction with four decimal places. One reported
point does not determine a function. I chose `min(p, 1-p)` because it is the
simplest form that peaks at 0.50 and vanishes at the tails, which is what
"highest when odds are closest to 50%" describes, but the venue could as
easily be charging on `p*(1-p)`, on a stepped band table, or on a spread-linked
quantity. The 83.41-versus-150.50 disagreement between two plausible shapes is
the honest measure of that uncertainty, and it is why rule 7 forces all three
scenarios into the same report rather than letting one become the number.

Third, and this is the failure that would matter most: the re-grade is
computed on positions that were TAKEN under a zero-fee assumption. Every one
of the 521 trades in the window was entered by a gate that did not price a
fee. A real fee does not just subtract from those trades, it removes most of
them - a fair-value entry with 2c of claimed edge does not fire at all against
a 3c round-trip tax. So the re-grade's output is an upper bound on the damage
and a lower bound on the behavioural change, and it says nothing whatsoever
about what the population of trades would have been. It answers "what did this
book cost under a fee", never "what would this strategy do under a fee". The
second question needs the forward run, and this proposal deliberately does not
claim to answer it.

Fourth, a caveat on the 15m arm that should stop anyone quoting it. 31 and 3
positions is not a sample. Every 15m figure this repair produces is a count
with a decimal point on it, and the reason the exposure is that small is that
the discovery pass is 5m-dominated - which means our measured exposure to the
fee reflects our universe selection, not the venue's economics. If the
discovery pass ever balances toward 15m, the exposure changes by an order of
magnitude and none of these numbers carry over.

## What past failure this addresses

It addresses a failure mode rather than a strategy: an assumption with an
expiry date that expired without anything noticing. The comment at
`paper_adapter.py:216-220` predicted this exact event, named the exact
consequence ("a strategy whose edge is 2c per share dies the day that
changes"), and was written specifically so that the zero would be findable
when the day came. The day came, and the mechanism that found it was an
external-signals sweep rather than anything in the system. That is the gap
worth recording: convention 17's expiring assumptions are documented in
comments and nothing rechecks them.

The engaged prior failure by P&L is the fair-value family, and this repair
sharpens the existing verdict rather than replacing it. The vault records that
family as TESTED_FAILED with `spread_eats_edge` as the mechanism and round-trip
cost as the cause. The measurement here is that the family round-trips on 99%
of its closes while every hold-to-resolution strategy settles on 100% of
theirs, so the venue's new tax lands on the family's already-diagnosed weak
point at double weight. Same mechanism, larger coefficient.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp and has no rows in
  this class. The engaged prior failure is a modelling assumption rather than a
  buried strategy, so there is no honest link to supply.
