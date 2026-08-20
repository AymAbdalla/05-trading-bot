---
name: "pm_weather_information_lag_refused"
thesis: "External Signal 4 asks for a weather information-lag probe: daily temperature is a measured rather than forecast outcome, so the claimed edge is the speed at which the book reprices a public observation, and two independent X accounts plus cycle 3's report corroborate the mechanic. The brief asks the reasoner to verify the temperature-observation feed before writing it. Verified, and the answer closes the direction rather than opening it. The feed is HAVE, not missing: `engine/feeds/noaa_weather.py` is a 610-line METAR reader against aviationweather.gov that returns a station observation carrying `observed_ts` - when the STATION took the reading, not when we fetched it - and an `obs_age_sec` derived from it, with a documented refusal for a reading whose timestamp is absent and a separate refusal for one that is merely stale. Observation age is the exact quantity an information-lag strategy trades on, and it is already a first-class refusal reason rather than something a new proposal would have to add. The mechanic is not merely available, it is WIRED AND LIVE, and it is measuring the lag as ABSENT. Read read-only in db/trading.db at 2026-08-19T23:5xZ and 2026-08-20T00:0xZ: `PM_weather_arb` has emitted 29,122 signals of which 12,656 are on temperature markets across 39 distinct market slugs, and the single most common outcome after the model reads the airport and compares it to the book is `airport_agrees_with_market` - 2,614 occurrences across 9 markets, first at 2026-08-18T15:25:58Z and last at 2026-08-20T00:00:40Z, so it is current and not historical. That counter is not a plumbing failure and not a missing feed. It is the strategy successfully performing the exact comparison Signal 4 describes and finding that the book already agrees with the thermometer. Against 2,614 such findings the strategy has 4 lifetime entries in trading.db, all three-of-four on `highest-temperature-in-paris-on-august-19-2026-29corhigher` plus one on Milan, and all four are still OPEN with `pnl_net` NULL; environment B has 4 more of which exactly 1 has closed, for the +7.353 the brief cites. So the +7.35 on n=1 is one closed trade with seven open siblings, and there is no weather result in this system in either direction. What actually blocks the weather family is not information and not the feed. It is instrument resolution: `rung_narrower_than_model_resolution` fires 8,251 times, 65.2% of all temperature-market signals, live to 2026-08-20T00:00:42Z, and it is a deliberate refusal rather than a bug - `MIN_ATTAINABLE_P_YES = 0.5` refuses any rung whose width the model's own sigma cannot resolve, because a bounded rung of width w caps at 2*Phi(w/(2*sigma))-1 and below that cap the model's SIDE is decided before a temperature is read. The strategy's own module docstring records the same thing measured on a live board: with the gate in, 20 markets gave 0 entries, 17 `rung_narrower_than_model_resolution` and 2 `airport_agrees_with_market`, and calls that 'the honest reach of this model today'. Proposal 033 already exists to fix precisely this by trading a 3-rung bracket as wide as the model instead of a rung narrower than it, and 033 has NEVER FIRED: 7,608 signals, 0 acted, binding skip `outside_24_48h_lead_band` at 3,806 and live. Writing a new information-lag proposal on top of that would add a third weather strategy to a family whose first two are blocked on a width problem and a lead-time band, and would do it on the strength of an external claim our own instrument has looked for 2,614 times and not found."
expected_edge_bps: null
kill_condition: "This proposal creates no strategy and therefore records no edge (convention 11); it records a REFUSAL and the numbered condition that would reverse it. The direction stays refused, and no weather information-lag strategy is written, until `airport_agrees_with_market` falls below 65% of the temperature-market signals that REACH the airport comparison, over 2,000 or more such signals, measured by `agents/forge_shadow_eval.py --db db/trading.db` reading `signals.skip_reason` restricted to `strategy_id = 'PM_weather_arb'` and `pair LIKE '%temperature%'`. The denominator is stated explicitly because it is the whole test and an unstated one is how this proposal's own first draft went wrong: it is the temperature-market signal count MINUS the refusals that fire before the comparison is made (`rung_narrower_than_model_resolution`, `airport_obs_stale`, `airport_reading_unavailable`, `airport_obs_time_missing`, `daily_extreme_history_unavailable`, `observation_window_too_far_out`, `no_orderbook`, `no_asks`). Measured at 2026-08-20T00:0xZ that is 12,656 minus 9,233, or 3,423 signals reaching the comparison, of which 2,614 agree: 76.4%. REVERSE the refusal and write the probe if that rate breaks below 65%, because a book that stops agreeing with the thermometer is the lag the external signal claims. SEPARATELY, and this is the condition that matters more: the refusal is also reversed if proposal 033 begins firing and books 40 or more resolved brackets, because the width problem and not the information problem is what is measured to be binding, and a weather family that can actually take a position is a different question from one that cannot. If neither condition is met within 30 days, record the weather information-lag direction as NOT_TESTED and requeue it (convention 11) - NOT as failed. Nothing here says the lag does not exist in the venue's wider weather book; it says our instrument does not see it in the 39 markets it can read, and 39 markets over two days is not a refutation of anything."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This proposal writes NO strategy, changes NO entry rule, NO exit rule, NO sizing and NO gate, and creates no new table or feed. It is filed so that the Signal 4 direction is closed with a measurement and a reversal condition attached, rather than left open to be re-proposed every cycle the same external claim recirculates. Cycle 3 and cycle 4 both carried it; without this record cycle 5 and 6 will too.
  1. Do NOT add a third weather strategy. Two exist. `PM_weather_arb` is live, reads the airport, and has 4 open lifetime entries in trading.db plus 4 in survivors. `PM_weather_bracket_width_matched` (proposal 033) is live, has emitted 7,608 signals and acted on zero. A third would be blocked by the same two constraints as the first two and would add a strategy to the registry that cannot take a position.
  2. Do NOT lower `MIN_ATTAINABLE_P_YES` to make the weather book fire. It is 0.5 and it refuses rungs the model's sigma cannot resolve. Lowering it does not create resolution, it removes the check that reports its absence - the same shape as the standing correction against raising 034's edge threshold to make it fire, and the same reason: tightening or loosening a gate to change a firing rate selects for model error rather than measuring it.
  3. Do NOT read the 4 open `PM_weather_arb` positions, or the single closed +7.353 in environment B, as evidence. Seven of the eight are open and carry `pnl_net` NULL. A closed sample of one is not a sample, and the brief's own framing of it as `n=1` is correct and should be preserved rather than aggregated up as the family's record.
  4. Record, do not act on, the historical skip-reason defect this analysis surfaced and then closed. 9,423 signals are labelled `resolution_station_unknown` and every one of them is a CRYPTO market - `sol-updown-5m-...`, `btc-updown-5m-...` - not a temperature market. That is 100% of that counter, and the temperature universe carries zero rows of it. The counter reads as "a weather market whose rules text we could not parse" and meant "this is not a weather market at all". It is ALREADY FIXED: the `not_a_temperature_market` gate landed 2026-08-18 at ~15:25Z, checked before the station lookup for exactly this reason, and the counters show the changeover cleanly - `resolution_station_unknown` runs 10:31:12Z to 15:31:13Z and stops dead, `not_a_temperature_market` runs 15:25:56Z to 20:33:24Z. No live defect, no repair needed. It is recorded because it inverted the apparent binding constraint of this entire analysis for one query: a naive group-by over all `PM_weather_arb` signals puts `resolution_station_unknown` at the top with 9,423 and reads as a station-mapping crisis, and the true live constraint is `rung_narrower_than_model_resolution`. Convention 25 applies to your own aggregate.
  5. Any future weather work reads `STATION_ALIASES` before assuming coverage. It holds 16 entries, all US (KNYC, KLAX, KLGA, KMDW, KMIA, KORD, KDEN), and `CITY_STATION_FALLBACK` holds 4 (Hong Kong, Istanbul, Moscow, Tel Aviv). The markets actually being evaluated are Paris, Milan, Munich, London, Shenzhen and Shanghai, none of which is in either table, so station resolution for them is happening through the coordinate/discovery path rather than the alias tables. That is worth knowing before anyone reads the alias tables as the coverage map. It is an observation, not a defect, and this proposal does not act on it.
data_requirements: |
  HAVE, verified by reading `engine/feeds/noaa_weather.py` at this snapshot: a temperature-OBSERVATION feed. METAR from aviationweather.gov, per-ICAO, with `observed_ts` as the station's own reading time, `obs_age_sec` computed from it, a cache with its own lifetime distinct from the observation's age, and named refusals `metar_no_observation`, `metar_http_transient`, `metar_http_error`, `metar_bad_station_argument`. This is the feed the brief asked to verify and it is present. It is an observation feed and not a forecast feed, which is the distinction Signal 4 turns on.
  HAVE: the daily-extreme path. `daily_extreme_history_unavailable` is a live named refusal (275 rows on temperature markets), which means the running daily extreme is fetched from METAR history rather than assumed, and `backtest/measure_daily_extreme_calibration.py` exists and writes the fitted sigma artefact that `fitted_daily_extreme_sigma` reads.
  HAVE: `signals.skip_reason` at the granularity this refusal needs, with 14 distinct live reasons on the temperature universe. Every number in the thesis came from `signals` alone.
  HAVE: resolution-station checking as an explicit, counted step (`resolution_station_checked`, with `resolution_station_unknown` and `resolution_station_ambiguous` as separate reasons rather than one pooled counter).
  MISSING, and it is why the reversal condition is written on a skip rate rather than on P&L: any closed weather position in db/trading.db. All 4 are open. Environment B has exactly 1 closed at +7.353. There is no weather P&L series in this system to write a kill against, in either database, and a kill condition denominated in weather P&L would be unfalsifiable today. This is stated rather than worked around.
  MISSING, non-blocking: a fitted sigma for every station traded. `daily_extreme_sigma_unfitted_for_station` is a live refusal reason, so the model already declines stations it has not been fitted for. Fitting more stations would widen the universe; it would not address the rung-width constraint, which is a property of the ratio between the sigma and the rung and not of which station it was fitted on.
  NOT NEEDED: NOAA, open-meteo or any additional weather source. The feed question the brief posed is answered HAVE and the constraint is elsewhere. Adding a second observation source would not move `rung_narrower_than_model_resolution`.
  NOT NEEDED: the 038 ledger, the 15m keying change, `market_duration`, the calibration tape. Nothing in this record depends on crypto-window instrumentation.
markets: "Polymarket daily temperature markets - 39 distinct slugs observed in db/trading.db across Paris, Milan, Munich, London, Shenzhen and Shanghai. Reported for db/trading.db; environment B's weather figures are quoted separately and never pooled with them (convention 32)."
kind: governance
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning, validator_kind_not_registered"
---

> **This proposal REFUSES a direction the cycle brief listed as priority 2.**
> It does not propose a strategy. The brief asked whether the shadow stack has
> a temperature-observation feed before a weather information-lag probe is
> written. It does, the mechanic is already wired, and it is live-measuring the
> lag as absent 2,614 times. Filing the probe anyway would build on an external
> claim against our own contrary instrument.

> **This is not a finding that the lag does not exist.** It is a finding that
> our instrument does not see it in the 39 markets it can read, over two days.
> The reversal conditions are numbered, and the one that matters most is
> proposal 033 firing at all.

## The number that decides it, and the denominator it needs

Of the temperature-market signals that get far enough to compare the airport
against the book, `airport_agrees_with_market` is 2,614 of 3,423, or 76.4%.
The reversal threshold is 65%.

The denominator matters more than the rate and I got it wrong in this
proposal's own first draft, which is why the kill condition now enumerates it
term by term. Dividing the agreements by ALL temperature signals gives 20.6%
and reads as though the comparison almost never agrees; dividing by the signals
that actually reached the comparison gives 76.4% and reads as though it almost
always does. The second is the right denominator, because a signal refused at
`rung_narrower_than_model_resolution` never looked at the book at all and
cannot be evidence either way about whether the book was lagging. Two
defensible-looking queries over the same table differ by 56 points here, and
only one of them is answering Signal 4's question.

The threshold is set 11 points below the current reading rather than adjacent
to it, because the reading is two days old and its session-to-session variance
is unmeasured; a threshold inside that unmeasured variance would fire on noise.
It is set at 65% rather than lower because the direction deserves a test it can
actually pass - the external corroboration is now three-fold across two cycles
and the mechanic is sound in principle. Daily temperature genuinely is measured
rather than forecast, and a book that reprices a thermometer slowly genuinely
would be exploitable without any forecasting.

What the measurement says is narrower and worth stating precisely: in the nine
markets where our model got as far as comparing its reading to the book, the
book was already there. That is a statement about nine markets on two days, and
the honest reading of it is that we have not found the lag rather than that it
is absent. The 60% threshold turns "we have not found it" into a standing test
that runs itself every cycle instead of a judgement that has to be re-argued.

## Why this refusal might be wrong

The strongest objection is universe. Signal 4's accounts name NYC, LA, Atlanta,
Denver, Chicago, Seoul and London, and `STATION_ALIASES` covers exactly the US
subset of that list - KNYC, KLAX, KDEN, KORD, KMDW, KMIA, KLGA. The markets our
loop actually evaluated were Paris, Milan, Munich, London, Shenzhen and
Shanghai. So we may be measuring agreement in the wrong cities. If the US
daily-temperature books are the thin, low-attention ones the traders describe
and the European and Chinese books are not, then 2,614 agreements in the latter
say nothing about the former. I cannot resolve this from the data: I can see
which slugs were evaluated, not why the loop's discovery surfaced those and not
the US ones. That is the single largest hole in this refusal and a future
session should look at the discovery path before treating the direction as
settled.

Second, `airport_agrees_with_market` is a point-in-time comparison and Signal 4
describes a lag measured in minutes. A gate that fires when the book and the
thermometer agree AT THE MOMENT WE LOOKED does not distinguish "there was never
a gap" from "there was a gap and it closed before our 60-second poll came
round". Our cadence is 60s and the claimed lag is of that order. So the counter
may be recording our own sampling rate rather than the market's efficiency,
and the clean test - which this proposal does not build - would be to record
the observation age and the book together on every comparison and look at
whether disagreement is a function of `obs_age_sec`. That is a real experiment
and it is cheaper than a strategy; I am not filing it because the family that
would consume it cannot take a position until the width constraint moves, and
filing measurement for a blocked family is how a proposal queue silts up.

Third, I am reading the rung-width constraint as the binding one on the
strength of a counter, and a counter tells you what fired first, not what would
have fired next. `rung_narrower_than_model_resolution` runs before the airport
comparison in the gate order, so its 8,251 could be masking any number of
downstream refusals - including, in principle, disagreements we never got to
evaluate. The 4,297 denominator in the reversal condition is the population that
reached the comparison, which is the right denominator for the question, but it
is 34% of the temperature universe and the other 66% is dark to this argument.

Fourth, and cutting the other way: I have refused a direction on two days of
data. 39 markets, 12,656 signals, one loop. Convention 11's discipline is that
NOT_TESTED is a result and not a failure, and I have tried to write this as
NOT_TESTED-with-a-reversal-condition rather than as a kill. If a reader takes
this as "weather is dead" they have taken more than it says.

## What past failure this addresses

It addresses the failure mode the standing corrections name as "weather books 0
entries and that is CORRECT" - and it updates it, because that correction is now
stale in a small but real way. `PM_weather_arb` has 4 entries in trading.db and
4 in environment B; the zero-entry era ended on 2026-08-19 at 02:08:04Z with a
Paris position. The correction's SUBSTANCE stands, in that the weather book is
still refusing almost everything and refusing it for a stated arithmetic reason.
Its literal claim no longer holds and a future session quoting it as a live fact
would be quoting a number that has moved (convention 25).

It also addresses the pattern proposal 041 was written against: recording a
named external claim as if it were a measurement. Cycle 3 and cycle 4 both
carried the weather trader signal, cycle 4 added a second corroborating account
and our own +7.35, and the natural next step is a proposal that treats
three social-media corroborations plus one open-position-adjacent trade as a
basis. The measured position is that our instrument performed the relevant
comparison 2,614 times and found nothing, and that the family's actual blocker
is a sigma-to-rung-width ratio that proposal 033 already named a year of
sessions ago and that 033 has never once fired to test.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  work is proposals 023 and 033 and the `weather_arb` module's own measured
  board runs, cited by counter rather than by graveyard id.
- **validator_kind_not_registered**: `kind: governance` is not in
  `agents/forge.py:208` `KINDS`, so `forge.py:491` would refuse this file as
  `unknown_kind` and `:209` `NULL_EDGE_KINDS` would separately refuse its null
  `expected_edge_bps`. Latent and not live - a hand-written `.md` never passes
  through `forge.py`. Same posture as proposal 041, which is the open item
  Raven has not yet ruled on: this file is deliberately NOT relabelled `repair`
  to make it pass, because it repairs nothing.
