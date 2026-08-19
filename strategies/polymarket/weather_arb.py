"""Weather Arb: trade the airport station, not the phone app.

THE CLAIM. Polymarket temperature markets resolve on an OFFICIAL weather
station named in the market's own rules text - typically an airport ASOS/AWOS
site. Retail participants anchor on whatever their phone shows, which is a
downtown or neighbourhood grid cell from a consumer forecast API. Airport and
downtown are two different measurements, and a social media post claims the gap
runs 3 to 8 degrees Fahrenheit.

## THAT NUMBER IS NOT EVIDENCE, AND NOTHING HERE TREATS IT AS ONE

The 3-8F figure is a claim from a social media post. It is a HYPOTHESIS. It is
not our data, we have never measured it, and no position of this strategy has
ever resolved. Every decision row this strategy emits - entries and skips alike
- carries `claimed_gap_is_unverified_vendor_number=True` and
`gap_never_measured_by_us=True`, exactly as `fair_value_arb` stamps its Reddit
provenance, so no later reader can pick "3 to 8 degrees" off a log and mistake
it for a measurement.

The gap is also NOT SIGNED. Urban heat island runs one way overnight (downtown
holds heat, the airport on its open field cools faster) and a sea breeze or lake
breeze runs the other way in the afternoon at a coastal airport. Anyone who
hardcodes "the airport is colder" has hardcoded half a diurnal cycle. Nothing
below assumes a direction: the airport reading is used as the
resolution-relevant observation, and the downtown reading is fetched only to
MEASURE the gap and its sign, never to gate a trade.

## WHAT A REAL POLYMARKET TEMPERATURE MARKET ACTUALLY LOOKS LIKE

Measured live on 2026-08-18 off `GET /events?tag_slug=weather&closed=false`:
2,045 markets under the tag, 1,771 of them city temperature markets, listed as
a LADDER per city per day. Eleven mutually exclusive rungs: two tails and nine
interior buckets.

    Will the highest temperature in Tokyo be 25C or below on August 18?
    Will the highest temperature in Tokyo be 26C on August 18?          <- bucket
    ...
    Will the highest temperature in Tokyo be 35C or higher on August 18?

    Will the highest temperature in New York City be 75F or below ...?
    Will the highest temperature in New York City be between 76-77F ...? <- bucket
    ...

Three facts in there each broke the original parser, and each is a way to lose
the whole position rather than a little of it:

  1. THE COMPARISON WORD COMES AFTER THE NUMBER. "85F or below", not
     "below 85F". The original `_THRESHOLD_RE` required it before, so it
     returned None on 100% of live questions and this strategy could not fire
     on a single real market.
  2. MOST OF THESE MARKETS ARE CELSIUS. 1,485 of 1,771 measured. `value_f` is
     Fahrenheit and is compared against a Fahrenheit METAR reading. Parsing
     "30C" into `value_f=30.0` prices 30F against an 86F market, a 56F error,
     and would look like a screaming edge on every single rung. The unit is
     parsed explicitly and a question with NO readable unit is refused rather
     than assumed - there is no default unit anywhere in this file.
  3. A RUNG IS AN INTERVAL, NOT A COMPARISON. "be 84F" means the reported daily
     high equals 84, and the rules text states the source reports whole
     degrees, so as a continuous interval that rung is [83.5, 84.5). Pricing it
     as "above 84" prices a tail at roughly twice the true probability.

Because the interval is built in the market's NATIVE unit and converted
afterwards, a Celsius bucket is 1.8F wide, not 1.0F. Doing the half-step in
Fahrenheit first would make every Celsius rung 44% too narrow. `parse_threshold`
does the arithmetic in the native unit and converts both edges, and
`test_a_celsius_bucket_is_widened_before_conversion_not_after` pins the
ordering.

There is a SECOND market family under the same tag that must never be priced
here: "Will global temperature increase by more than 1.29C in August 2026?".
It is a planetary anomaly index, not a city airport station, and it carries the
MASCULINE ORDINAL character U+00BA rather than the degree sign. It gets its own
exclusion and its own skip reason (`global_temperature_market_excluded`) so it
can never be pooled with a genuine parse failure.

## TWO MODELS, ONE PER RANDOM VARIABLE

Every live market on this board resolves on the DAILY EXTREME - the highest (or
lowest) reading of the station's LOCAL calendar day. That is not the same random
variable as the reading at a moment, and pricing one as the other is a
systematic bias rather than a rounding error: the daily max of a path is
stochastically larger than the path's endpoint, so a point-in-time model is
biased LOW on every "or higher" rung and HIGH on every "or below" rung of a
"highest temperature" ladder, and the other way round on a "lowest temperature"
ladder.

That was measured, not argued. On 2026-08-18, over 80 live markets with real
books and real METAR, the point-in-time model produced 7 entries with realised
"edge" between 0.45 and 0.999. Two of them, in the same minute:

    Madrid, station 33.0C. Market: "the highest today is 39C" at 0.70,
    because the afternoon peak has not happened. Model: 0.000024.
    Buenos Aires, station 7.0C. Market: "highest today is 8C or below" at
    0.001, because the afternoon will be warmer. Model: 0.87.

The market was right both times and the model was wrong in OPPOSITE directions,
which is the worst case: a pooled win rate would average the two biases into
something that looks unbiased.

SO THERE ARE NOW TWO MODELS AND THE ROW SAYS WHICH ONE RAN.

    market_metric = None          `probability_yes`. A single reading at the
                                  settlement stamp. UNCHANGED, so the existing
                                  tape stays comparable.
    market_metric = daily_high    `probability_yes_daily_extreme`, over
                 or daily_low     M = max(O, X)  (or min for a low), where

        O   is the extreme the station has ALREADY REPORTED inside the local
            observation day, read from the METAR history endpoint. It is not
            modelled. It has happened, and it is a HARD BOUND: if Madrid has
            already reported 33.0C then "the highest today is 30C or below" is
            false with probability exactly 1, whatever any forecast says.
        X   ~ Normal(mu, sigma), mu = open-meteo's forecast daily extreme for
            that local date AT THE STATION'S OWN COORDINATES plus the current
            station-minus-grid bias, sigma growing with the hours left in the
            local day.

`DailyExtremeEstimate`'s docstring states the three distributional assumptions
in full and names the measurement that would falsify each. The short version:
normality of an extreme is wrong in the tail (a max is asymptotically Gumbel and
right-skewed), the bias is assumed to persist from now to the diurnal peak when
it demonstrably drifts, and the sigma is now FITTED per station rather than
assumed - see "THE FITTED SIGMA" below.

`allow_daily_extreme_markets` selects between the two and lives in
`config.yaml: polymarket.weather`. When it is off, every daily-extreme market is
refused under `daily_extreme_not_priced_by_point_in_time_model` - a convention 11
cannot-run, not a claim that there is no edge there.

WHAT IS STILL NOT MEASURED. Not one weather position has ever resolved. The
harness now exists and the PREDICTOR's error has been measured (below), but the
model has never been scored against a market that settled, so every entry it
produces is still TAPE. It is honest tape - it prices the variable the market
resolves on, using the station's own observations as a bound and a sigma fitted
against that station's own thermometer - but "our forecast error is 2.74F" and
"this strategy wins" are two different claims and only the first one has a
number behind it. Every row carries
`daily_extreme_model_scored_on_resolved_markets=False` for exactly that reason.

## THE FITTED SIGMA, AND THE RESULT NOBODY EXPECTED

`backtest/measure_daily_extreme_calibration.py` exists as of 2026-08-18. It
reconstructs THIS predictor from open-meteo's archived model runs and scores it
against the station's own realised METAR daily extreme.

It was commissioned on the premise that the house sigma was a placeholder that
was far too wide. IT IS NOT. Measured over 537 station-days across 49 stations
at the 24-to-48-hour lead that covers the entire live board:

    mean -0.08F,  sd 2.73F,  RMSE 2.74F      against a house 2.96F at 31.5h

The estimate written before any run was 8% conservative. What IS several times
off is the number the instruction asked for and this harness refuses to fit: the
standard deviation of the daily extremes THEMSELVES, which is CLIMATE SPREAD and
runs to 8.6F at Amsterdam against a 2.7F forecast error at the same station.
Using it would have made the board LESS tradeable, not more.

So fitting sigma did not unblock the interior of a Celsius ladder and nothing
below pretends it did. A 1.8F bucket needs sigma under 1.334F to reach
`MIN_ATTAINABLE_P_YES` and under 1.191F to reach the 0.55 entry floor; exactly
one station of 49 (Ankara, 1.333F, on n=8) reaches the first and none reaches
the second. `rung_narrower_than_model_resolution` stays, on the same threshold.

What fitting bought instead: the per-station spread, which a single constant was
hiding. 1.33F at Ankara against 5.70F at San Francisco is a factor of four, and
the strategy now prices each station with its own number or refuses it under
`daily_extreme_sigma_unfitted_for_station`.

AND IT OPENED A HOLE, WHICH IS WHY GATE 6c EXISTS. Ankara's 1.333F puts a 1.8F
bucket's ceiling at 0.500335 - three ten-thousandths above the 0.5 gate. The
first live run with the fitted sigma on booked exactly one entry through that
crack: model 0.097, No side at 0.903, book 0.51, a 0.39 "edge" on a rung whose
side was decided by arithmetic. The floor that actually bites is the ENTRY
conviction, not 0.5, so a bounded rung whose Yes side cannot reach
`min_model_p_side` is refused under
`rung_cannot_reach_entry_conviction_on_yes`. `MIN_ATTAINABLE_P_YES` is
untouched: this ADDS a refusal, it cannot admit anything (convention 27).
Re-measured on the same 44 markets: 0 entries.

## THE DAILY-EXTREME MODEL CHANGED WHAT THIS STRATEGY IS CLAIMING. READ THIS.

The airport-versus-downtown thesis is a claim about a MEASUREMENT: retail reads
a downtown grid cell, the market resolves on an airport station, and the two
differ. The point-in-time model was a direct expression of that claim, because
its only input was the airport reading.

The daily-extreme model is not. Its centre is open-meteo's FORECAST daily
extreme, re-centred by the current station-minus-grid bias. So when it disagrees
with the book, the disagreement is mostly "our forecast provider expects a
different afternoon peak than the crowd does", and only secondarily "the crowd
is reading the wrong thermometer". Those are two different claims with two
different kill conditions, and the second one does not imply the first.

MEASURED, first live run, 2026-08-18 at 14:30Z, 25 highest-volume city ladder
rungs with real books, real METAR and real forecasts: 2 ENTERs, 22
`airport_agrees_with_market`, 1 `observation_window_too_far_out`. The two
entries were Madrid "highest is 36C" (book 0.64, model 0.19) and Shanghai
"highest is 31C" (book 0.56, model 0.20), both taking the No side at a realised
edge of 0.43 and 0.34.

## THOSE TWO ENTRIES WERE ARITHMETIC, NOT EDGE, AND THE GATE THAT KILLS THEM

Checking them is what found the next defect, and it is a bad one.

For a bounded rung of width `w` under a normal of standard deviation `sigma`,
the Yes probability is maximised when the mean sits at the rung's centre, and
that maximum is `2 * Phi(w / (2 * sigma)) - 1`. It depends on NOTHING but the
width and the sigma - not on the temperature, not on the forecast, not on the
station.

A Celsius bucket is 1.8F wide. At a 31.5-hour horizon the sigma is 2.96F. So

    ceiling = 2 * Phi(1.8 / 5.92) - 1 = 0.239

and the Madrid row returned **0.238**. The model was already at its ceiling. It
could not have said Yes about that rung whatever Madrid did. It then "disagreed"
with a book at 0.64 and booked a 0.43 edge, and it would do the same on nine of
the eleven rungs of every Celsius ladder, every cycle, forever - measuring the
width of a bucket against the width of our own sigma and calling the difference
edge.

That is the `strike_inside_proxy_noise_floor` shape exactly, and it gets the
same treatment: REFUSE where the instrument cannot resolve, rather than record a
measurement error as a decision (convention 11). `MIN_ATTAINABLE_P_YES = 0.5` is
not a tuning knob - below it the model's SIDE is fixed before a temperature is
read. Rungs under it are refused as `rung_narrower_than_model_resolution` and
every row carries `max_attainable_p_yes` so the refusal can be checked from
itself.

RE-MEASURED with the gate in, same board, 20 markets: **0 entries**, 17
`rung_narrower_than_model_resolution`, 2 `airport_agrees_with_market`, 1
`observation_window_too_far_out`. That is the honest reach of this model today.

What survives the gate: both TAILS of every ladder, which are unbounded on one
side and have no ceiling, and Fahrenheit RANGE buckets ([75.5, 77.5), 2.0F wide)
once the horizon is inside about an hour. A whole-degree Fahrenheit bucket is
1.0F wide and its ceiling is 0.31 even at the close, so this sigma can never
price one.

"THE WAY TO WIDEN THAT REACH IS TO FIT THE SIGMA" was the conclusion written
here before the harness ran. IT WAS WRONG, AND IT IS LEFT IN VIEW RATHER THAN
QUIETLY DELETED. The sigma was fitted, per station, against 537 station-days of
realised METAR, and it came back at 2.74F where the house number said 2.96F.
Measured on the real board of 2026-08-18 - 846 bounded rungs, 729 of them 1.8F
Celsius buckets and 117 of them 2.0F Fahrenheit ranges - the count that clears
0.55 at the fitted sigma is ZERO, and the count that clears
`MIN_ATTAINABLE_P_YES` is zero too. The reach did not widen. The 188 unbounded
tails were never gated by this and still are not.

Every row is stamped `pricing_model`, `station_minus_grid_bias_f`,
`horizon_beyond_same_day`, `max_attainable_p_yes` and
`daily_extreme_calibration_harness_exists=False`, so the populations are
separable and nothing downstream can read any of this as a measured win.

The airport-versus-downtown gap is still unmeasured and the recorder that would
measure it is still not built. `DowntownWeatherFeed` still runs on every row for
exactly that reason and still gates nothing.

## THE RESOLUTION SOURCE IS THE RULES TEXT, NEVER OUR ASSUMPTION

`WEATHER_MARKETS` below carries an ICAO station per city. Those are ASSUMPTIONS.
They are used to decide which station to FETCH and to cross-check, and they are
never used to decide what a market resolves on. `resolution_station_checked`
reads the market's own rules text and refuses the market when it cannot find a
station there:

    resolution_station_unknown     no rules text, or no station we recognise
    resolution_station_ambiguous   the rules text names two different stations

Both are SKIPs, and both are convention 11 cannot-runs rather than results.
Guessing a market's resolution source is how you lose the entire position while
being completely right about the weather. If the rules name a station that is
not in `STATION_ALIASES`, that reads as `resolution_station_unknown`, which is
the correct answer: we could not read it, so we do not trade it.

When the rules text and the table disagree, the RULES WIN and the row is
stamped `station_assumption_matches_rules=False`. That flag existing at all is
the point: it is the only way a wrong table entry becomes visible instead of
becoming a silent loss.

## OUR PROBABILITY MODEL, AND WHY IT IS THE WEAKEST PART OF THIS FILE

Given an airport reading `T` now and `h` hours until resolution, we price
P(final reading beats the threshold) as a normal CDF with

    sigma_F = SIGMA_FLOOR_F + SIGMA_PER_SQRT_HOUR_F * sqrt(h)

That is a diffusion assumption applied to a variable that is not a diffusion.
Temperature has a large, deterministic, strongly mean-reverting daily cycle: at
06:00 the next six hours are almost certainly UP and at 16:00 they are almost
certainly DOWN, and a symmetric random walk centred on the current reading says
neither. So this model is wrong in a KNOWN direction at KNOWN times of day, and
the constants are house numbers with an expiry date (convention 17). Replacing
them with a diurnal-climatology model is the obvious next step and is not built.
`MIN_EDGE = 0.08` is deliberately wide partly to absorb this.

## WHAT THIS STRATEGY IS NOT_TESTED ON (convention 11)

  - We have never resolved a weather position. Not one. Zero settled trades.
  - The airport-vs-downtown gap has never been measured against our own data.
    Nothing in this repo has ever recorded a paired (airport, downtown) reading.
  - The sigma model above has never been scored against realised intraday
    temperature paths.
  - The daily-extreme-versus-endpoint bias described above has never been
    quantified. It is stated, not measured.
  - `AirportWeatherFeed` reports the CURRENT reading. Nothing in this file
    tracks the running daily max, which is half the resolution variable on a
    "highest temperature" market and is already in the past by the afternoon.

What would measure the gap: a recorder that polls `AirportWeatherFeed` and
`DowntownWeatherFeed` for every city in `WEATHER_MARKETS` on a fixed cadence and
writes both readings plus their difference to a table, the same shape as
`engine/feeds/liquidation_recorder.py`. After a few weeks of tape, the honest
distribution of the gap by city and by hour of day is a query, not a claim. That
recorder does not exist. Until it does, "3 to 8 degrees" is a rumour this file
is careful never to launder.

## FETCHES INSIDE evaluate(), AND WHY THAT IS A COMPROMISE

`base.MarketContext` is a plain data bag precisely so a decision is reproducible
from a logged context. This strategy bends that: `MarketContext` carries no
weather fields, so `evaluate` calls its injected feeds. Both feeds are
constructor arguments (`airport_feed=`, `downtown_feed=`), both are fully
offline-injectable, and every reading that entered a decision is written into
`features` so the row is at least reconstructible after the fact. Rows are
stamped `readings_fetched_inside_evaluate=True`. A caller that wants strict
reproducibility should pass pre-fetched feed stubs.

## HOLDS TO RESOLUTION

`manages_exits = False`. There is no exit model here and no attempt at one: the
thesis is about where the thermometer sits at settlement, so selling early is a
different strategy. The stop is 0.00 - a losing binary share is worth exactly
zero, which satisfies convention 8.

KILL CONDITION: trailing-20 win rate below 55%, measured by
`backtest/polymarket_harness.py` on the `PM_weather_arb` population once 20
resolved trades exist. 55% is not arbitrary: entries are gated at an 8c edge, so
at a typical 0.40-0.60 entry premium the break-even win rate is the entry
premium itself, and 55% is roughly break-even-plus-nothing for a 50c fill. Below
it the 8c "edge" is not edge, it is model error. The harness must score this
population on RESOLVED trades only; there are no closed-before-resolution trades
here to pool with (contrast `fair_value_arb`, which has both). Convention 7 cuts
both ways here and 20 trades is a thin sample either way: a PASS on 20 is a
shrug too, and this line is a stop-loss on the hypothesis, not a promotion test.

SECOND KILL CONDITION, on the thesis rather than on the PnL: if the recorder
described above ever measures a median |airport - downtown| gap below 1.0F for a
city, that city is removed from `WEATHER_MARKETS`. A 3-8F claim that measures
under 1F is not a small edge, it is a wrong premise.

THIRD KILL CONDITION, on the model rather than on the thesis, and this is the
binding one today: the named harness is
`backtest/measure_daily_extreme_calibration.py`, and IT NOW EXISTS. It has
cleared the first half of what was asked of it - the predictor's error is
measured, per station, against the station's own thermometer - and it has NOT
cleared the second half, which is the number that matters: on at least 200
RESOLVED daily-extreme rungs, mean |model_p - realised_frequency| below 0.05 in
each of ten probability deciles. There are zero resolved rungs. Until there are,
an entry on a daily-extreme market is tape, and every row says so under
`daily_extreme_model_scored_on_resolved_markets`.

FOURTH KILL CONDITION, and it is the one this file's own measurement points at:
if the fitted sigma at the lead the board actually trades stays above 1.334F for
every station, this strategy cannot price an interior Celsius rung and its
entire reach is the 188 ladder tails. Measured 2026-08-18: 48 of 49 stations are
above it. That is not a reason to lower `MIN_ATTAINABLE_P_YES` - the gate is
arithmetic, not a preference - it is a reason to decide whether a tails-only
strategy is worth a discovery cycle every 60 seconds. That is Aym's call, and it
needs the number in front of it rather than an empty entry log.
"""
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

from strategies.polymarket.base import (MARKET_TYPE_WEATHER, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# Endpoints. GET only, both of them, no auth, no key, no wallet.
# ---------------------------------------------------------------------------

AVIATION_WEATHER_URL = 'https://aviationweather.gov/api/data/metar'
OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

#: Both feeds sit on the critical path of a decision, so they get a SHORT
#: timeout. A 10s hang on a weather API is a decision that arrives after the
#: book has moved, which is worse than no decision at all.
FEED_TIMEOUT_SEC = 2.0
FEED_RETRIES = 2
FEED_BACKOFF_SEC = 0.25

USER_AGENT = '05-trading-bot/paper (read-only)'

# ---------------------------------------------------------------------------
# Thresholds. Every one of these is OURS and every one is an assumption with an
# expiry date (convention 17).
# ---------------------------------------------------------------------------

#: A METAR observation older than this is refused. One hour is a long time
#: during a frontal passage: a cold front can drop a station 15F in twenty
#: minutes, and a 59-minute-old reading taken during one is simply a different
#: temperature. This is the gate that stops us pricing yesterday's air.
MAX_OBS_AGE_SEC = 3600

#: How long a fetched METAR observation may be re-served from the module cache.
#: `evaluate()` fetches one observation per MARKET per cycle, and a city's ladder
#: is many markets standing on ONE station, so the request count scales with the
#: board and not with the number of stations. Measured 2026-08-18: a 1,299-market
#: sweep issued 1,299 requests and drew 44 4xx responses from aviationweather.gov.
#:
#: A station issues a METAR at most about every 30 minutes, so inside this window
#: a re-fetch returns the SAME observation. The request buys nothing and the 4xx
#: it may draw instead costs a decision.
#:
#: This cache CANNOT launder a stale reading past the freshness gate, and that is
#: the property that makes it safe rather than merely convenient. `MAX_OBS_AGE_SEC`
#: is checked against the observation's OWN `observed_ts`, never against the time
#: we fetched it, so a cached reading ages at exactly the same rate as a freshly
#: fetched one and is refused under `airport_obs_stale` at the same instant either
#: way. The TTL only decides how often we ASK; the age gate alone decides what we
#: are willing to price.
#:
#: EXPIRY: raise this only against a measured request count, never because a sweep
#: felt slow. Lower it to 0.0 to disable caching outright.
METAR_CACHE_TTL_SEC = 300.0

#: How long a fetched METAR HISTORY page may be re-served. Longer than the
#: single-observation TTL because it is a much bigger response for a much
#: slower-moving fact: the running daily extreme can only ever move when a new
#: observation lands, and a station issues one about every 30 minutes.
#: Measured 2026-08-18: `hours=18` on KLGA returned 21 rows and took several
#: seconds, against under a second for the single-observation call.
METAR_HISTORY_CACHE_TTL_SEC = 600.0

#: How far back to ask for observations. It has to cover the longest local day
#: elapsed so far at ANY station, which is up to 24 hours, but a station's own
#: local midnight is at most 24h back and the extreme is only needed over the
#: part of the day already elapsed. 26 hours covers a full local day plus the
#: worst UTC offset without asking for two days of tape.
METAR_HISTORY_HOURS = 26

#: The history endpoint returns a much bigger body than the single-observation
#: one and it is NOT on the critical path of a 5-second poll: the weather cycle
#: runs on its own 60-second cadence. Measured 2026-08-18, `hours=18` on KLGA
#: exceeded a 10-second read timeout once and returned 21 rows inside 25s on the
#: retry, so a 2-second budget would refuse this call almost always.
HISTORY_TIMEOUT_SEC = 12.0

#: Forecast responses carry three days of daily values plus 72 hourly ones.
#: Measured 2026-08-18 the call returned in well under a second, but it is a
#: bigger body than the current-temperature call `DowntownWeatherFeed` makes,
#: so it gets its own budget rather than sharing a 2-second one tuned for a
#: single number.
FORECAST_TIMEOUT_SEC = 6.0

#: A station forecast may be re-served for this long. open-meteo refreshes its
#: blended model output roughly hourly; 900s is finer than the data changes and
#: exists to stop a 60-second weather cycle issuing the same request 60 times an
#: hour per station.
FORECAST_CACHE_TTL_SEC = 900.0

#: Minimum gap between our own probability and the walked entry price. 8c is
#: wide on purpose: it has to absorb the sigma model's known diurnal bias (see
#: the module docstring) as well as the spread. EXPIRY: tighten only after the
#: harness scores a win rate at 8c, never because 8c produced too few trades.
#:
#: CONVENTION 5, IN THE RIGHT UNITS FOR THIS INSTRUMENT. The generic 30 bps
#: dead-on-arrival floor is a CRYPTO number. D-336: the smallest expressible
#: price move on the live tape is 0.001 (9,033 non-null best_ask observations
#: sit on that grid; only 14.7% land on 0.01), not one cent as previously
#: assumed. 0.001 on a 50-cent contract is 0.001 / 0.50 = 20 bps. So 20 bps is
#: the floor here, not 200, and an "edge" under it is a rounding artefact of
#: the tick grid.
#:
#: 8 cents on a 50-cent contract is 1,600 bps, eighty times that floor. That is
#: the gross modelled edge this gate demands BEFORE costs, and the taker fee on
#: Polymarket is currently zero (`config.yaml: polymarket.taker_fee_rate`), so
#: gross and net differ only by the book walk, which `effective_ask` already
#: charges us for.
POLYMARKET_TICK_ON_FIFTY_CENTS_BPS = 20.0
MIN_EDGE = 0.08

#: Uncertainty in the final reading, in degrees F, as a function of hours
#: remaining. HOUSE NUMBERS, never fitted, never backtested. See the module
#: docstring for why the functional form itself is suspect.
SIGMA_FLOOR_F = 0.75
SIGMA_PER_SQRT_HOUR_F = 1.5

#: Past this the sqrt term stops meaning anything and the diurnal cycle owns the
#: answer completely. Beyond it we refuse rather than extrapolate a model we
#: already know is wrong.
MAX_HOURS_TO_RESOLUTION = 36.0

# ---------------------------------------------------------------------------
# The DAILY EXTREME model's own constants. Separate block, separate names, and
# never reused by the point-in-time path (convention 23 cuts both ways: one
# definition per fact, and two facts never share one definition).
# ---------------------------------------------------------------------------

#: Standard deviation of the REMAINING-DAY extreme around the bias-corrected
#: forecast, in degrees F, as `floor + per_sqrt_hour * sqrt(hours to the local
#: day's close)`.
#:
#: CONVENTION 15: THESE ARE ESTIMATES WRITTEN BEFORE THE RUN, NOT MEASUREMENTS.
#: House numbers, never fitted, never backtested, exactly like the
#: point-in-time pair above.
#: They were chosen so that the implied same-day forecast error is roughly
#: 1.0F at zero lead and 2.7F at a 24-hour lead, which is the order of magnitude
#: published for blended-model next-day 2m maximum temperature. Nothing in this
#: repo has scored open-meteo's daily extreme against a station's realised
#: extreme even once. The moment `backtest/measure_daily_extreme_calibration.py`
#: exists these two numbers must be replaced by fitted ones and this comment
#: deleted.
#:
#: They are deliberately SMALLER than the point-in-time `SIGMA_PER_SQRT_HOUR_F`
#: of 1.5, and that is the whole point rather than an inconsistency: the
#: point-in-time model has to carry the entire deterministic diurnal swing in
#: its noise term because it has no diurnal term at all, whereas the forecast
#: path already contains the diurnal cycle in its mean.
DAILY_EXTREME_SIGMA_FLOOR_F = 1.0
DAILY_EXTREME_SIGMA_PER_SQRT_HOUR_F = 0.35

#: Refuse a market whose local observation day closes further out than this.
#:
#: 36 hours, and the number was MEASURED against the live board rather than
#: picked. On 2026-08-18 at 14:30Z the highest-volume city ladders were all for
#: the NEXT local day - Polymarket lists tomorrow's rungs while today's are
#: already past their settlement stamp (688 of 2,035 raw markets were dropped as
#: `end_date_past` in the same sweep). A European next-day ladder closes 31.5
#: hours out, so a 30-hour cap refused the entire tradable board and left this
#: strategy reporting `observation_window_too_far_out` on 12 of the 14 biggest
#: markets. 36 admits them; a US next-day ladder at 38.5 hours is still refused,
#: and the day AFTER tomorrow (about 60 hours) never comes close.
#:
#: It also equals `MAX_HOURS_TO_RESOLUTION`, so the two paths' horizons agree
#: rather than being two numbers a reader has to reconcile.
#:
#: WHAT IT COSTS, STATED. `daily_extreme_sigma_f(36)` is 3.1F, and the sigma
#: constants were chosen against published SAME-DAY forecast error. Past 24
#: hours this is an EXTRAPOLATION of an already-unfitted number, so every row
#: beyond a day carries `horizon_beyond_same_day=True` and the two populations
#: can be scored apart instead of pooled.
MAX_HOURS_TO_WINDOW_CLOSE = 36.0

#: Past this the sigma constants are being extrapolated rather than applied.
#: Not a gate, a STAMP: rows either side of it must be scorable separately.
SAME_DAY_HORIZON_HOURS = 24.0

#: The smallest ceiling on a rung's Yes probability that still lets the model
#: have an OPINION about it. See `max_attainable_p_yes` for the derivation and
#: for the live Madrid row that made this necessary.
#:
#: 0.5 is not a tuning knob and it is not arbitrary. Below it the model is
#: structurally incapable of preferring the Yes side of that rung, so
#: `model_side` is 'No' before a single temperature is read. It will then
#: "disagree" with any market pricing that rung above 0.5 and take the No side,
#: every cycle, on nine of the eleven rungs of every ladder. That is not an
#: edge, it is the width of the bucket measured against the width of our sigma.
#:
#: MEASURED, and this is the whole justification: a Celsius bucket is 1.8F wide
#: and the sigma at a 31.5-hour horizon is 2.96F, giving a ceiling of 0.239. The
#: Madrid 36C rung returned 0.238 - the ceiling, to three decimals - against a
#: book at 0.64, and booked a 0.43 "edge". A Fahrenheit whole-degree bucket is
#: worse still: its ceiling is 0.31 even half an hour before the day closes, so
#: those rungs are never priceable by this model at all.
#:
#: What survives: both TAILS of every ladder, which are unbounded on one side
#: and have no ceiling, and the wider Fahrenheit range buckets once the horizon
#: is short enough. That is the honest reach of a model with this sigma.
#:
#: THE SIGMA HAS SINCE BEEN FITTED AND THIS NUMBER STILL DOES NOT MOVE.
#: Convention 27: read the operator first. The gate is `attainable <
#: MIN_ATTAINABLE_P_YES -> refuse`, so it admits more rungs as SIGMA falls, and
#: lowering this threshold would admit rungs the model provably cannot have an
#: opinion about. The fit came back at 2.74F RMSE against a 1.334F requirement
#: for a Celsius bucket. Sigma did not fall, so nothing here changes.
MIN_ATTAINABLE_P_YES = 0.5

#: The model's probability for the SIDE IT BUYS, below which no entry is taken.
#:
#: Raven's instruction was "only trade when the model says P(Yes) > 0.55".
#: Applied to the literal Yes leg that would ban every No-side entry, and the No
#: side is where this strategy's disagreements live: `model_side` is chosen as
#: whichever side the model puts above 0.5, and on a ladder tail priced at 0.90
#: the model's opinion is almost always about the No leg. So the gate is applied
#: to `p_side`, the model probability of the leg actually being bought, which
#: reduces to exactly "P(Yes) > 0.55" whenever the leg is the Yes leg. The
#: deviation is deliberate and it is stated on every row as
#: `min_model_p_side`.
#:
#: WHAT IT IS FOR, given `MIN_EDGE` already exists. `MIN_EDGE` is a gate on
#: PRICE (model minus what we pay). This is a gate on CONVICTION. A model at
#: 0.52 against a book at 0.40 clears an 8c edge while being, on its own
#: numbers, barely distinguishable from a coin flip - and the sigma underneath
#: that 0.52 is fitted on 7 to 16 station-days per station (convention 7). At
#: 0.55 the model has to have taken a position before it is allowed to pay for
#: one.
#:
#: EXPIRY (convention 17): this is a number from an instruction, not from a
#: measurement. Nothing has scored entries at 0.55 against entries at 0.52
#: because no weather position has ever resolved.
MIN_MODEL_P_SIDE = 0.55

# ---------------------------------------------------------------------------
# THE FITTED SIGMA
# ---------------------------------------------------------------------------
#
# `backtest/measure_daily_extreme_calibration.py` EXISTS NOW. It writes the
# artifact named below, and `fitted_daily_extreme_sigma` reads it.
#
# WHAT IT MEASURED, AND WHY THE ANSWER IS NOT THE ONE ANYBODY EXPECTED. The
# instruction that commissioned the harness assumed the house constants were a
# placeholder that was far too wide. They are not. Measured 2026-08-18 over 537
# station-days across 49 stations, the residual of THIS predictor (open-meteo's
# archived previous-run daily extreme at the station's coordinates, plus the
# station-minus-grid bias) against the station's own realised METAR daily high
# at a 24-to-48-hour lead is
#
#     mean -0.08F,  sd 2.73F,  RMSE 2.74F
#
# and `daily_extreme_sigma_f(31.5)` - the house number - is 2.96F. The estimate
# written before any run was 8% conservative, not several times too wide.
#
# The number that IS several times off is the one the harness was asked for and
# refused to fit: the standard deviation of the daily extremes themselves, which
# is CLIMATE SPREAD. It runs to 8.6F at Amsterdam and 7.7F at Munich against a
# forecast error of 2.7F and 2.8F at the same two stations. Substituting it
# would have made every rung on the board unpriceable by a wider margin, not a
# narrower one. See the harness docstring for the full statement.
#
# CONSEQUENCE, STATED PLAINLY: fitting sigma does NOT unblock the interior of a
# Celsius ladder. A 1.8F bucket needs sigma under 1.191F to reach 0.55 and under
# 1.334F to reach `MIN_ATTAINABLE_P_YES`. At the live 24-48h horizon exactly one
# station of 49 (Ankara, LTAC, 1.333F on n=8) reaches the ceiling gate and NONE
# reaches 0.55. `rung_narrower_than_model_resolution` stays, and it stays on the
# same threshold: convention 27 says verify the DIRECTION first, and the
# direction here is that the gate admits more rungs as sigma FALLS. Sigma did
# not fall.
#
# Fitting it was still worth doing. It replaced a house number with a measured
# one, per station, and the per-station spread is enormous - 1.33F at Ankara
# against 5.70F at San Francisco. A single constant across 49 stations was
# hiding a factor of four.

#: Where the harness writes and this module reads. Repo-relative so a checkout
#: moved to another path still finds it, and OVERRIDABLE per instance so a test
#: never reads the live artifact by accident.
SIGMA_CALIBRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'research', 'weather_sigma_calibration.json')

#: Named on every row. Convention 6 wants a number AND a named harness, and the
#: value of naming it stops at the point where a reader cannot tell whether it
#: was ever built - so the row also carries whether it was READ.
CALIBRATION_HARNESS = 'backtest/measure_daily_extreme_calibration.py'

#: Lead buckets in the artifact are whole days, because open-meteo's archive
#: resolves model runs to whole days and nothing finer can be reconstructed
#: honestly. `hours // 24` picks the bucket a horizon falls in.
SIGMA_LEAD_BUCKET_HOURS = 24.0

#: Whether the DAILY EXTREME path prices with the fitted sigma or the house
#: constants.
#:
#: The MODULE default is False so that every existing caller and every existing
#: test constructing `WeatherArb()` keeps its exact previous behaviour, the same
#: contract `DEFAULT_ALLOW_DAILY_EXTREME_MARKETS` keeps. The live loop turns it
#: on from `config.yaml`.
#:
#: When it is ON there is NO fallback to the house constants. A station with no
#: fit is refused under `daily_extreme_sigma_unfitted_for_station`, which is a
#: convention 11 cannot-run: we have not measured this station's forecast error,
#: so we do not have a distribution to price it with. Falling back to a house
#: number while the row said `use_fitted_sigma: true` would be a measurement we
#: never took wearing the label of one we did.
DEFAULT_USE_FITTED_SIGMA = False

#: How far an hourly forecast timestamp may sit from the METAR observation and
#: still be used to compute the station-minus-grid bias. open-meteo's hourly
#: grid is exactly one hour, so half a step is the largest honest tolerance.
BIAS_HOUR_TOLERANCE_SEC = 1800

#: Whether a daily-extreme market may be priced when the station's own
#: observations for the elapsed part of the day could not be read.
#:
#: DEFAULT True MEANS "REFUSE", and it is the single most important line in this
#: block. The running observed extreme is not a nice-to-have input, it is a HARD
#: FLOOR on the resolution variable: if the station has already reported 33.0C
#: today then "the highest today is 39C" is a statement about the future alone,
#: and "the highest today is 30C or below" is already FALSE with probability
#: exactly 1. Pricing without that floor is precisely the failure the Madrid and
#: Buenos Aires rows recorded on 2026-08-18. Set False only to gather tape.
DEFAULT_REQUIRE_OBSERVED_EXTREME = True

#: Whether daily-extreme markets are priced at all. See gate 2c in `evaluate`.
#: The MODULE default stays False so that every existing caller and every test
#: constructing `WeatherArb()` keeps its exact previous behaviour; the live loop
#: turns it on from `config.yaml` via `set_weather_config`, which is the one
#: place the decision is recorded (convention 17: not hardcoded).
DEFAULT_ALLOW_DAILY_EXTREME_MARKETS = False

#: The live, config-overridable weather settings. Read through
#: `weather_config()`, written only by `set_weather_config()`.
_WEATHER_CONFIG: Dict[str, object] = {
    'allow_daily_extreme_markets': DEFAULT_ALLOW_DAILY_EXTREME_MARKETS,
    'require_observed_extreme': DEFAULT_REQUIRE_OBSERVED_EXTREME,
    'use_fitted_sigma': DEFAULT_USE_FITTED_SIGMA,
}

#: Keys `set_weather_config` accepts, and the type each is coerced to. An
#: unknown key RAISES rather than being ignored: a typo in config.yaml that
#: silently does nothing is a setting somebody believes is in force and is not.
_WEATHER_CONFIG_BOOL_KEYS = ('allow_daily_extreme_markets',
                             'require_observed_extreme',
                             'use_fitted_sigma')


def weather_config() -> Dict[str, object]:
    """A COPY of the live settings, so a caller cannot mutate them in place."""
    return dict(_WEATHER_CONFIG)


def set_weather_config(overrides: Optional[Dict[str, object]]
                       ) -> Dict[str, object]:
    """Apply `config.yaml: polymarket.weather` and return the resulting settings.

    Mirrors `strike.set_noise_floor_bps_by_asset`: one module-level setter,
    called once from the loop's `main()`, so the behaviour of a strategy the
    loop constructs itself can be changed without a code edit (convention 17).

    An unknown key or a non-boolean value RAISES `ValueError`. A config that
    quietly does nothing is worse than one that refuses to load: the operator
    believes the setting is in force, and every row afterwards is stamped with a
    flag that does not describe the run.

    `None` (no `weather:` block) leaves the module defaults untouched and is not
    an error - that is the shape of every config that predates this block.
    """
    if overrides is None:
        return weather_config()
    if not isinstance(overrides, dict):
        raise ValueError('polymarket.weather must be a mapping, got %s'
                         % type(overrides).__name__)

    unknown = sorted(set(overrides) - set(_WEATHER_CONFIG_BOOL_KEYS))
    if unknown:
        raise ValueError(
            'unknown polymarket.weather key(s): %s (known: %s)'
            % (', '.join(unknown), ', '.join(_WEATHER_CONFIG_BOOL_KEYS)))

    for key in _WEATHER_CONFIG_BOOL_KEYS:
        if key not in overrides:
            continue
        value = overrides[key]
        if not isinstance(value, bool):
            # Not coerced. `'false'` is a truthy string and would turn a refusal
            # into permission; a bool is the only value that means what it says.
            raise ValueError(
                'polymarket.weather.%s must be a boolean, got %r' % (key, value))
        _WEATHER_CONFIG[key] = value
    return weather_config()

#: Target size, per-trade notional cap and exchange minimum. The notional cap
#: matches `PolymarketPaperAdapter.notional_cap_usdc` and
#: `PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC`; restated here so a size
#: computed in this file cannot silently exceed a cap enforced somewhere else.
TARGET_SHARES = 20
MAX_NOTIONAL_USDC = 10.0
MIN_SHARES = 5

PRICE_TICK = 0.01

#: Celsius to Fahrenheit, defined in one place.
C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0

SECONDS_PER_HOUR = 3600.0

# ---------------------------------------------------------------------------
# The station table. READ THE WARNING.
# ---------------------------------------------------------------------------

#: city key -> station and geometry ASSUMPTIONS.
#:
#: `icao` here is what we FETCH and cross-check. It is NOT what a market
#: resolves on. Polymarket has used more than one station for the same city over
#: time (NYC contracts have referenced both the Central Park co-op station and
#: LaGuardia), and nothing stops them changing it in the next contract. EVERY
#: ONE of these assignments must be checked against the specific market's rules
#: text before any live use, which is what `resolution_station_checked` does and
#: why an unreadable rules text is a refusal rather than a fallback to this
#: table.
#:
#: `aliases` are the strings a rules text is likely to use. Their mapping onto
#: an ICAO lives in `STATION_ALIASES`, which is the authoritative direction.
#:
#: `downtown` lat/lon is the CITY CENTRE, which is the consumer-app anchor we
#: are claiming retail reads. Approximate to about a city block, which is far
#: finer than open-meteo's grid resolution anyway.
WEATHER_MARKETS: Dict[str, dict] = {
    'nyc': {
        'slug_patterns': ('nyc-temp', 'new-york-temp',
                          'highest-temperature-in-nyc'),
        'icao': 'KNYC',
        'aliases': ('KNYC', 'KLGA', 'Central Park', 'LaGuardia', 'La Guardia'),
        'downtown': (40.7128, -74.0060),
    },
    'la': {
        'slug_patterns': ('la-temp', 'los-angeles-temp',
                          'highest-temperature-in-la'),
        'icao': 'KLAX',
        'aliases': ('KLAX', 'Los Angeles International'),
        'downtown': (34.0522, -118.2437),
    },
    'chicago': {
        'slug_patterns': ('chicago-temp', 'highest-temperature-in-chicago'),
        'icao': 'KMDW',
        'aliases': ('KMDW', 'KORD', 'Midway', "O'Hare", 'OHare'),
        'downtown': (41.8781, -87.6298),
    },
    'miami': {
        'slug_patterns': ('miami-temp', 'highest-temperature-in-miami'),
        'icao': 'KMIA',
        'aliases': ('KMIA', 'Miami International'),
        'downtown': (25.7617, -80.1918),
    },
    'denver': {
        'slug_patterns': ('denver-temp', 'highest-temperature-in-denver'),
        'icao': 'KDEN',
        'aliases': ('KDEN', 'Denver International'),
        'downtown': (39.7392, -104.9903),
    },
}

#: Alias (lowercased) -> the ICAO it means. EXPLICIT, because inferring
#: "LaGuardia means KLGA" from list position is the same class of mistake as
#: inferring a resolution source from a slug. This is the only mapping
#: `resolution_station_checked` consults, and a station absent from it is
#: unreadable rather than assumed.
STATION_ALIASES: Dict[str, str] = {
    'knyc': 'KNYC',
    'klga': 'KLGA',
    'central park': 'KNYC',
    'laguardia': 'KLGA',
    'la guardia': 'KLGA',
    'klax': 'KLAX',
    'los angeles international': 'KLAX',
    'kmdw': 'KMDW',
    'kord': 'KORD',
    'midway': 'KMDW',
    "o'hare": 'KORD',
    'ohare': 'KORD',
    'kmia': 'KMIA',
    'miami international': 'KMIA',
    'kden': 'KDEN',
    'denver international': 'KDEN',
}

#: Cities whose markets carry NO station identifier anywhere in their rules
#: text, mapped to the station we believe they mean. MEASURED, then checked:
#: of the four cities with an empty `resolutionSource` (Hong Kong, Istanbul,
#: Moscow, Tel Aviv), THREE turn out to name their station inside a URL in the
#: `description` field (`weather.gov/wrh/timeseries?site=LTFM` and friends), so
#: `stations_in_urls` reads them straight off the rules text and this table is
#: never consulted for them. They are listed anyway because a silent
#: disagreement between what we measured and what the code reads is exactly the
#: failure this file exists to prevent.
#:
#: Note MOSCOW is UUWW (Vnukovo), NOT UUEE (Sheremetyevo). The rules text says
#: `site=UUWW`. Those are two airports 60km apart.
#:
#: HONG KONG IS THE ONE REAL FALLBACK AND IT IS A PROXY, NOT A MATCH. Its
#: markets resolve on the Hong Kong Observatory headquarters reading published
#: at weather.gov.hk, an URBAN station in Tsim Sha Tsui. VHHH is Chek Lap Kok,
#: an island airport 25km west. Trading VHHH as if it were HKO is precisely the
#: airport-versus-downtown gap this strategy claims to exploit, pointed at our
#: own head. So the table is OFF by default (`allow_station_fallback=False`)
#: and a row that used it is stamped `station_is_a_fallback_guess=True`.
CITY_STATION_FALLBACK: Dict[str, str] = {
    'hong kong': 'VHHH',
    'istanbul': 'LTFM',
    'moscow': 'UUWW',
    'tel aviv': 'LLBG',
}

#: Fields on a Gamma market's raw payload that can carry the rules text. Gamma
#: is not consistent about which one is populated, so all of them are read and
#: joined rather than one being trusted.
RULES_TEXT_FIELDS = ('description', 'rules', 'resolutionSource',
                     'resolution_source', 'resolutionCriteria')

#: Discovery goes through the TAG route, not `/public-search`. Measured on
#: 2026-08-18: `/public-search` returned 33 markets, every one of which mapped
#: to `by_city={'unmapped': 33}`; `tag_slug=weather` returned 2,045 markets
#: across 227 events, 1,771 of them city temperature ladders. A search endpoint
#: that silently returns 1.6% of the universe is worse than one that fails,
#: because the shortfall reads as "Polymarket listed nothing today".
WEATHER_TAG_SLUGS = ('weather', 'temperature')
GAMMA_EVENTS_PATH = '/events'

#: Gamma caps a page at 100. `GAMMA_MAX_PAGES` is a runaway guard, not a limit
#: we expect to hit: 227 events is 3 pages. If discovery ever reports
#: `pagination_capped=True`, the universe grew and this number is stale
#: (convention 17), and the correct response is to raise it, never to trust the
#: truncated count.
GAMMA_PAGE_LIMIT = 100
GAMMA_MAX_PAGES = 40

#: A market has to look like a temperature market to survive discovery. Matched
#: against the question text, lowercased. NOT sufficient on its own - see
#: `looks_like_a_temperature_market`, because a real Polymarket question reads
#: "Will NYC exceed 85F on August 18?" and contains none of these words.
TEMPERATURE_KEYWORDS = ('temperature', 'degrees', 'fahrenheit', 'hottest',
                        'coldest')

#: Every SKIP reason `evaluate` can produce. Listed so a reader can see at a
#: glance that no two causes share a string (convention 20), and so a test can
#: assert every one of them is reachable rather than trusting the docstring
#: (convention 22).
SKIP_REASONS = (
    'no_market',
    'no_clock',
    'global_temperature_market_excluded',
    # THE WRONG PRODUCT ENTIRELY, and its own reason for a measured cause.
    # Until 2026-08-18 the shadow loop handed this strategy a BTC Up/Down 5m
    # market on every cycle and it came back `resolution_station_unknown` -
    # which is a statement about a weather market whose rules text we could not
    # read, not about a crypto market that has no weather in it at all.
    # Convention 20: two drop causes never share one counter, and those two are
    # about as different as two causes get.
    'not_a_temperature_market',
    'resolution_station_unknown',
    'resolution_station_ambiguous',
    'threshold_unparseable',
    'source_reporting_precision_unknown',
    'source_precision_finer_than_ladder_step',
    'daily_extreme_not_priced_by_point_in_time_model',
    # -- the DAILY EXTREME path's own refusals. Every one of these is a
    # convention 11 cannot-run: an input the model needs that we could not read.
    # They are separate strings because they need separate responses - a
    # forecast outage is an operational problem, an unparseable date is a parser
    # problem, and a station with no reported observations yet today is neither.
    'station_coordinates_unknown',
    'station_forecast_unavailable',
    'resolution_date_unparseable',
    'resolution_date_outside_forecast_window',
    'forecast_extreme_missing_for_date',
    'forecast_hour_missing_for_bias',
    'observation_window_closed',
    'observation_window_too_far_out',
    'daily_extreme_history_unavailable',
    # The rung is narrower than the model can resolve, so the model's SIDE is
    # decided by the bucket width rather than by the temperature. Same shape as
    # the strike-proxy noise floor and refused for the same reason: recording a
    # measurement error as a decision is a convention 11 inversion.
    'rung_narrower_than_model_resolution',
    # One notch further along the same axis, and a SEPARATE fact: the rung's
    # ceiling clears 0.5 but cannot reach the entry conviction floor, so the
    # model can never prefer Yes strongly enough to buy it and every entry from
    # it would be a No-side bet whose side was fixed by the bucket width. Found
    # by a live Ankara row that cleared the 0.5 gate by 0.000335 and booked a
    # 0.39 "edge" (see gate 6c).
    'rung_cannot_reach_entry_conviction_on_yes',
    # The station has no FITTED forecast-error sigma in
    # `research/weather_sigma_calibration.json`, and `use_fitted_sigma` is on.
    # Convention 11 to the letter: we have never measured how wrong our
    # predictor is at this station, so we have no distribution to price it
    # with. It is NOT the same fact as `station_forecast_unavailable` (the
    # forecast API did not answer) and NOT the same as
    # `rung_narrower_than_model_resolution` (we have a sigma and it is too
    # wide for the rung), and pooling any two of those three would hide a
    # missing calibration behind a live outage.
    'daily_extreme_sigma_unfitted_for_station',
    # The model and the market disagree, the price clears MIN_EDGE, and the
    # model still does not believe its own side hard enough to buy it. Its own
    # counter because it is a CONVICTION refusal and `edge_below_min` is a
    # PRICE refusal; one number for both would make it impossible to tell "we
    # were not paid enough" from "we were not sure enough".
    'model_confidence_below_entry_floor',
    'resolution_time_unknown',
    'market_past_resolution_time',
    'resolution_too_far_out',
    'airport_reading_unavailable',
    'airport_obs_time_missing',
    'airport_obs_stale',
    'no_orderbook',
    'no_asks',
    'market_implied_direction_unreadable',
    'airport_agrees_with_market',
    'unsizable_at_notional_cap',
    'unfillable_at_cap',
    'effective_ask_above_cap',
    'edge_below_min',
)

_NORMAL = NormalDist(0.0, 1.0)


def c_to_f(temp_c: float) -> float:
    """Celsius to Fahrenheit. One definition, used by both feeds."""
    return float(temp_c) * C_TO_F_SCALE + C_TO_F_OFFSET


def f_to_c(temp_f: float) -> float:
    """The inverse, so a row can report the METAR's own units."""
    return (float(temp_f) - C_TO_F_OFFSET) / C_TO_F_SCALE


def floor_to_tick(price: float, tick: float = PRICE_TICK) -> float:
    """Snap a limit DOWN onto the 1c grid.

    The epsilon is load-bearing for the same reason it is in
    `fair_value_arb.floor_to_tick`: `0.29 / 0.01` is 28.999999999999996 in
    binary floating point, so a bare floor moves a price already on the grid
    down a full tick.
    """
    if tick <= 0:
        return price
    steps = math.floor(price / tick + 1e-9)
    decimals = max(0, -math.floor(math.log10(tick)))
    return round(steps * tick, decimals)


def _safe_float(value) -> Optional[float]:
    """A finite float, or None. convention 19: NaN sails through `float()`."""
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _safe_coordinate(value, limit: float) -> Optional[float]:
    """A finite coordinate inside `[-limit, limit]`, or None.

    Range-checked rather than merely parsed. A latitude of 400 is not a station
    at an unusual place, it is a payload we should refuse to build a forecast
    request from, and open-meteo answers an out-of-range coordinate with an HTTP
    200 carrying an error body - which would arrive here as a less specific
    shape failure several layers later.
    """
    out = _safe_float(value)
    if out is None or not -abs(limit) <= out <= abs(limit):
        return None
    return out


def _parse_iso_seconds(value) -> Optional[int]:
    """ISO 8601 -> unix seconds, or None. Naive strings are read as UTC.

    Reading a naive timestamp as UTC is an assumption, and it is the right one
    for both feeds here (open-meteo returns UTC unless asked otherwise), but it
    is exactly the kind of assumption that silently shifts an age check by hours
    if a feed ever changes. Ages computed from it are reported on the row, never
    hidden. An int or float epoch passes through unchanged, because Gamma
    sometimes sends one where it usually sends a string.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if not math.isfinite(float(value)) else int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Readings and feeds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """One temperature observation, always in Fahrenheit.

    `observed_ts` is None when the source did not tell us when it was taken.
    That is NOT the same as "just now" and is never treated as such: an
    observation we cannot age is refused, because a stale reading and a fresh
    one are the same number with completely different meanings.
    """

    source: str              # 'airport_metar' | 'downtown_open_meteo'
    station: str             # ICAO, or 'lat,lon' for the downtown grid cell
    temp_f: float
    observed_ts: Optional[int] = None
    raw: Optional[str] = None
    #: WHERE the station is, straight off the METAR payload's own `lat`/`lon`.
    #: Measured 2026-08-18: every aviationweather.gov row carries `lat`, `lon`,
    #: `elev` and `name`. This matters because the daily-extreme model needs a
    #: forecast AT THE STATION, and the alternative was a second hand-maintained
    #: coordinate table for 51 cities - a table that would be an assumption
    #: about where a station is, when the station tells us itself.
    #: None when the payload omitted them, which is a refusal upstream and never
    #: a licence to fall back on a city centre.
    lat: Optional[float] = None
    lon: Optional[float] = None

    def age_sec(self, now: float) -> Optional[float]:
        if self.observed_ts is None:
            return None
        return float(now) - float(self.observed_ts)

    def to_dict(self) -> dict:
        return {'source': self.source, 'station': self.station,
                'temp_f': round(self.temp_f, 2),
                'observed_ts': self.observed_ts,
                'lat': self.lat, 'lon': self.lon}


class _HttpFeed(object):
    """Shared GET, bounded retry and never-raise behaviour for both feeds.

    `session` and `sleep_fn` are injectable so tests run fully offline and
    without real sleeps. Nothing here raises: a failed read returns
    `(None, reason)` and the caller skips. Returning a temperature we could not
    verify would be worse than returning nothing, and returning `None` with no
    reason would pool an outage with a genuinely absent field (convention 20).
    """

    url = ''
    source_name = ''

    def __init__(self, session=None, timeout: float = FEED_TIMEOUT_SEC,
                 retries: int = FEED_RETRIES, sleep_fn=None):
        if session is None:
            # Imported lazily. A strategy module should stay importable without
            # a network stack, the same reason `base.effective_ask_for` defers
            # its engine import.
            import requests
            session = requests.Session()
            session.headers.update({'User-Agent': USER_AGENT})
        self.session = session
        self.timeout = float(timeout)
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self._sleep = sleep_fn or time.sleep
        #: Counted by cause. A run that failed on the network and a run that got
        #: clean 404s need different responses, and one combined number cannot
        #: tell them apart.
        self.stats: Dict[str, int] = {}

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def _get_json(self, params: dict, timeout: Optional[float] = None
                  ) -> Tuple[Optional[object], str]:
        """GET and decode. `(payload, 'ok')` or `(None, reason)`.

        Retries network errors, 429 and 5xx. Does NOT retry other 4xx: a 400 on
        a bad ICAO is a real answer from the server, and retrying it is extra
        load for the same answer.

        `timeout` overrides the instance budget for ONE call. It exists because
        the same host answers a one-row query in well under a second and a
        26-hour history query in seconds; one budget for both would either hang
        the decision path or refuse the history call every time. A per-call
        override keeps the two facts apart instead of raising the shared number.
        """
        budget = self.timeout if timeout is None else float(timeout)
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self._bump('requests')
            try:
                resp = self.session.get(self.url, params=params,
                                        timeout=budget)
            except Exception:                            # noqa: BLE001
                # Deliberately broad. A feed exception must never escape into
                # `evaluate`, whatever the injected session decided to raise.
                if is_last:
                    self._bump('fail_network')
                    return None, 'feed_network_failure'
                self._bump('retries')
                self._sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            code = getattr(resp, 'status_code', None)
            if code == 200:
                try:
                    return resp.json(), 'ok'
                except Exception:                        # noqa: BLE001
                    self._bump('fail_bad_json')
                    return None, 'feed_bad_json'

            if code == 429 or (isinstance(code, int) and code >= 500):
                if is_last:
                    self._bump('fail_http_transient')
                    return None, 'feed_http_transient'
                self._bump('retries')
                self._sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            self._bump('fail_http_4xx')
            return None, 'feed_http_error'

        return None, 'feed_network_failure'              # pragma: no cover


# ---------------------------------------------------------------------------
# METAR observation cache
# ---------------------------------------------------------------------------
#
# MODULE level, not per instance, and that is the whole point. `WeatherArb`
# builds its feed lazily and a sweep may build several `AirportWeatherFeed`
# objects; a per-instance cache would then miss on exactly the repeat that a
# multi-rung ladder generates. Keyed by ICAO alone because the METAR endpoint's
# only other parameter is the response format.
#
# Values are `(expires_at, Reading)`. `Reading` is a frozen dataclass, so a
# cached entry cannot be mutated by whoever we hand it to.
#
# ONLY successful reads are stored. A 4xx, a network failure, an empty station
# response and a non-finite temperature are all left uncached deliberately: a
# cached failure would turn one bad minute into five minutes of guaranteed
# refusals, and the retry that would have fixed it never happens.
_METAR_CACHE: Dict[str, Tuple[float, Reading]] = {}
_METAR_CACHE_LOCK = threading.Lock()

#: The HISTORY cache, and it is a SECOND dict rather than a second kind of value
#: in the first one. Two facts, two caches, two TTLs: a single observation and a
#: 26-hour page of them are different responses at different costs with
#: different refresh rates, and keying them into one dict under one TTL would
#: mean whichever was written last decided the freshness of both.
#: Values are `(expires_at, tuple_of_Readings)`, newest first, exactly as
#: aviationweather.gov returns them.
_METAR_HISTORY_CACHE: Dict[str, Tuple[float, Tuple[Reading, ...]]] = {}
_METAR_HISTORY_CACHE_LOCK = threading.Lock()


def clear_metar_cache() -> None:
    """Drop every cached observation, single AND history.

    Tests need this because the caches outlive an instance by design, so one
    test's fetch would otherwise satisfy the next test's assertion about how
    many requests were issued. Both are cleared by this one call on purpose: a
    test that cleared only half would leave the other half contaminating its
    neighbours in exactly the silent way `tests/conftest.py` exists to prevent.
    """
    with _METAR_CACHE_LOCK:
        _METAR_CACHE.clear()
    with _METAR_HISTORY_CACHE_LOCK:
        _METAR_HISTORY_CACHE.clear()


def metar_cache_size() -> int:
    """Number of cached single observations, expired included. Diagnostic."""
    with _METAR_CACHE_LOCK:
        return len(_METAR_CACHE)


def metar_history_cache_size() -> int:
    """Number of cached history pages, expired included. Diagnostic."""
    with _METAR_HISTORY_CACHE_LOCK:
        return len(_METAR_HISTORY_CACHE)


@dataclass(frozen=True)
class DailyObserved:
    """The station's OWN running extreme over the elapsed part of a local day.

    This is not a model output and not a forecast. It is the largest (or
    smallest) temperature the resolution station has actually reported inside
    the market's observation window so far, and on a "highest temperature today"
    market it is a HARD FLOOR on the resolution value: the day's high cannot
    come in below a reading the day has already produced.

    `observations` is the count that produced it, and it is on every row for the
    reason convention 7 exists: an extreme over 2 observations and an extreme
    over 21 are the same number carrying completely different information about
    how much of the day has been seen.

    `window_start_ts` / `window_end_ts` are the LOCAL calendar day in unix
    seconds, so a reader can check by eye that the window is 86,400 wide and
    that the observations fall inside it.
    """

    station: str
    metric: str                    # 'daily_high' | 'daily_low'
    extreme_f: float
    observations: int
    first_ts: int
    last_ts: int
    window_start_ts: int
    window_end_ts: int

    def to_dict(self) -> dict:
        return {'observed_station': self.station,
                'observed_metric': self.metric,
                'observed_extreme_f': round(self.extreme_f, 2),
                'observed_extreme_c': round(f_to_c(self.extreme_f), 2),
                'observed_count': self.observations,
                'observed_first_ts': self.first_ts,
                'observed_last_ts': self.last_ts,
                'observation_window_start_ts': self.window_start_ts,
                'observation_window_end_ts': self.window_end_ts}


class AirportWeatherFeed(_HttpFeed):
    """METAR observations from aviationweather.gov. THE resolution-relevant one.

    METAR reports temperature in whole or half degrees CELSIUS, so a converted
    Fahrenheit value carries about 0.9F of quantisation. On a market whose
    threshold is a whole number of degrees F that quantisation is material near
    the line, which is why every row also carries `airport_temp_c`.

    Reads are cached per station for `cache_ttl_sec` (see `METAR_CACHE_TTL_SEC`
    for why that is safe). `cache_hits` and `cache_misses` are counted
    SEPARATELY from `requests` in `stats`, because "we did not send a request"
    and "we sent one and it failed" are different facts and one number for both
    would hide a feed outage behind a healthy-looking hit rate (convention 20).
    """

    url = AVIATION_WEATHER_URL
    source_name = 'airport_metar'

    def __init__(self, session=None, timeout: float = FEED_TIMEOUT_SEC,
                 retries: int = FEED_RETRIES, sleep_fn=None,
                 cache_ttl_sec: float = METAR_CACHE_TTL_SEC, clock=None,
                 history_cache_ttl_sec: float = METAR_HISTORY_CACHE_TTL_SEC,
                 history_hours: int = METAR_HISTORY_HOURS,
                 history_timeout: float = HISTORY_TIMEOUT_SEC):
        super().__init__(session=session, timeout=timeout, retries=retries,
                         sleep_fn=sleep_fn)
        # Negative would be a cache that expires before it is written, which no
        # caller means. 0.0 is meaningful and kept: it disables the cache.
        self.cache_ttl_sec = max(0.0, float(cache_ttl_sec))
        self.history_cache_ttl_sec = max(0.0, float(history_cache_ttl_sec))
        self.history_hours = max(1, int(history_hours))
        self.history_timeout = max(0.0, float(history_timeout))
        # Injectable so a test can advance the clock without sleeping, the same
        # reason `sleep_fn` is injectable.
        self._clock = clock or time.time

    def _cache_get(self, key: str) -> Optional[Reading]:
        if self.cache_ttl_sec <= 0:
            return None
        now = float(self._clock())
        with _METAR_CACHE_LOCK:
            entry = _METAR_CACHE.get(key)
            if entry is None:
                return None
            expires_at, reading = entry
            if now >= expires_at:
                # Deleted rather than left in place: an expired entry that stays
                # is a slow leak in a process that runs for days.
                del _METAR_CACHE[key]
                return None
            return reading

    def _cache_put(self, key: str, reading: Reading) -> None:
        if self.cache_ttl_sec <= 0:
            return
        with _METAR_CACHE_LOCK:
            _METAR_CACHE[key] = (float(self._clock()) + self.cache_ttl_sec,
                                 reading)

    @staticmethod
    def _rows_from(payload) -> Tuple[Optional[list], str]:
        """The row list, or `(None, reason)`. One shape check, two callers."""
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get('data') or []
        else:
            return None, 'airport_unexpected_shape'
        if not rows:
            # The station reported nothing, or the ICAO is wrong. Either way we
            # have no observation, and no observation is not a temperature.
            return None, 'airport_no_observation'
        return rows, 'ok'

    def _reading_from_row(self, row, icao: str) -> Tuple[Optional[Reading], str]:
        """One aviationweather.gov row -> a Reading, with a refusal reason.

        Split out of `observation` so the history path parses rows through the
        SAME code (convention 23). A second parser would be a second place for
        the Celsius-to-Fahrenheit conversion and the NaN guard to disagree, and
        the disagreement would be invisible: both would return a temperature.
        """
        if not isinstance(row, dict):
            return None, 'airport_unexpected_shape'

        try:
            temp_c = float(row.get('temp'))
        except (TypeError, ValueError):
            return None, 'airport_no_temperature_field'
        if not math.isfinite(temp_c):
            # convention 19: a NaN sails straight through float() and poisons
            # every average it touches downstream.
            return None, 'airport_non_finite_temperature'

        obs_ts = row.get('obsTime')
        if obs_ts is None:
            obs_ts = row.get('reportTime')
        try:
            observed = int(float(obs_ts))
        except (TypeError, ValueError):
            observed = _parse_iso_seconds(obs_ts)

        return Reading(source=self.source_name,
                       station=str(row.get('icaoId') or icao).upper(),
                       temp_f=c_to_f(temp_c),
                       observed_ts=observed,
                       raw=row.get('rawOb'),
                       lat=_safe_coordinate(row.get('lat'), 90.0),
                       lon=_safe_coordinate(row.get('lon'), 180.0)), 'ok'

    def observation(self, icao: str) -> Tuple[Optional[Reading], str]:
        key = str(icao).upper()

        cached = self._cache_get(key)
        if cached is not None:
            self._bump('cache_hits')
            return cached, 'ok'
        self._bump('cache_misses')

        payload, status = self._get_json({'ids': key, 'format': 'json'})
        if payload is None:
            return None, status

        rows, status = self._rows_from(payload)
        if rows is None:
            return None, status

        reading, status = self._reading_from_row(rows[0], icao)
        if reading is None:
            return None, status
        # Cached under the REQUESTED icao, not the one the response echoed back:
        # the next caller will ask by the same key this one did.
        self._cache_put(key, reading)
        return reading, 'ok'

    # -- history ------------------------------------------------------------

    def _history_cache_get(self, key: str) -> Optional[Tuple[Reading, ...]]:
        if self.history_cache_ttl_sec <= 0:
            return None
        now = float(self._clock())
        with _METAR_HISTORY_CACHE_LOCK:
            entry = _METAR_HISTORY_CACHE.get(key)
            if entry is None:
                return None
            expires_at, readings = entry
            if now >= expires_at:
                del _METAR_HISTORY_CACHE[key]
                return None
            return readings

    def _history_cache_put(self, key: str,
                           readings: Tuple[Reading, ...]) -> None:
        if self.history_cache_ttl_sec <= 0:
            return
        with _METAR_HISTORY_CACHE_LOCK:
            _METAR_HISTORY_CACHE[key] = (
                float(self._clock()) + self.history_cache_ttl_sec, readings)

    def history(self, icao: str, hours: Optional[int] = None
                ) -> Tuple[Optional[Tuple[Reading, ...]], str]:
        """Every readable observation for a station over the last `hours`.

        Returns `(readings, 'ok')` newest-first, or `(None, reason)`.

        MEASURED 2026-08-18 against the live endpoint: `ids=KLGA&format=json`
        returns 1 row and `ids=KLGA&format=json&hours=18` returns 21, spanning
        2026-08-17T20:24Z to 2026-08-18T14:00Z, newest first, with `temp`
        values in Celsius exactly as the single-row call. So `hours` is a
        recognised parameter and not one of the silently-ignored kind.

        Rows that will not parse are COUNTED by cause and dropped, never
        silently skipped (convention 20). A page whose rows ALL fail to parse
        returns `airport_no_observation` rather than an empty tuple: an empty
        tuple would read as "the station reported nothing", which is a fact
        about the station rather than about our parser.
        """
        key = '{}|{}'.format(str(icao).upper(),
                             int(self.history_hours if hours is None
                                 else hours))
        cached = self._history_cache_get(key)
        if cached is not None:
            self._bump('history_cache_hits')
            return cached, 'ok'
        self._bump('history_cache_misses')

        payload, status = self._get_json(
            {'ids': str(icao).upper(), 'format': 'json',
             'hours': int(self.history_hours if hours is None else hours)},
            timeout=self.history_timeout)
        if payload is None:
            return None, status

        rows, status = self._rows_from(payload)
        if rows is None:
            return None, status

        readings: List[Reading] = []
        for row in rows:
            reading, row_status = self._reading_from_row(row, icao)
            if reading is None:
                self._bump('history_row_' + row_status)
                continue
            if reading.observed_ts is None:
                # An observation we cannot place in time cannot be placed in a
                # calendar day either, so it can never contribute to a daily
                # extreme. Its own counter, never pooled with a parse failure.
                self._bump('history_row_no_obs_time')
                continue
            readings.append(reading)

        if not readings:
            return None, 'airport_no_observation'

        out = tuple(readings)
        self._history_cache_put(key, out)
        return out, 'ok'

    def daily_extreme_checked(self, icao: str, metric: str,
                              window_start_ts: int, window_end_ts: int
                              ) -> Tuple[Optional[DailyObserved], str]:
        """The station's running extreme inside `[window_start_ts, window_end_ts)`.

        Returns `(DailyObserved, 'ok')` or `(None, reason)`.

        THE WINDOW IS HALF-OPEN and that is not fussiness: the two ends are two
        different local midnights, and an observation at exactly the closing
        midnight belongs to the NEXT day's market. Counting it in both would
        make one reading a floor under two different contracts.

        `airport_history_no_observation_in_window` is its own reason and is NOT
        pooled with a failed read. It is the normal state in the first minutes
        of a local day, when nothing has been reported yet - a market whose
        observation window has barely opened has no floor, which is a fact about
        the clock rather than a fault (convention 11).
        """
        if metric not in ('daily_high', 'daily_low'):
            return None, 'airport_history_metric_unknown'

        readings, status = self.history(icao)
        if readings is None:
            return None, status

        start, end = int(window_start_ts), int(window_end_ts)
        inside = [r for r in readings
                  if r.observed_ts is not None and start <= r.observed_ts < end]
        if not inside:
            return None, 'airport_history_no_observation_in_window'

        pick = max if metric == 'daily_high' else min
        extreme = pick(r.temp_f for r in inside)
        stamps = [int(r.observed_ts) for r in inside]
        return DailyObserved(
            station=str(icao).upper(),
            metric=metric,
            extreme_f=float(extreme),
            observations=len(inside),
            first_ts=min(stamps),
            last_ts=max(stamps),
            window_start_ts=start,
            window_end_ts=end), 'ok'


class DowntownWeatherFeed(_HttpFeed):
    """Current temperature for a downtown lat/lon, from open-meteo.

    This is the CONSUMER-APP ANCHOR, and it is diagnostic only. Nothing in the
    entry gate reads it. It exists so that every row carries the gap this
    strategy's whole thesis rests on, which is the only route by which the 3-8F
    claim ever becomes a measurement instead of staying a rumour.

    open-meteo returns a MODEL grid cell, not a station observation. That is
    exactly what a phone app shows, which is the point, but it must never be
    confused with a reading that anything resolves on.
    """

    url = OPEN_METEO_URL
    source_name = 'downtown_open_meteo'

    def observation(self, lat: float,
                    lon: float) -> Tuple[Optional[Reading], str]:
        payload, status = self._get_json({
            'latitude': lat, 'longitude': lon,
            'current': 'temperature_2m',
            'temperature_unit': 'fahrenheit',
        })
        if payload is None:
            return None, status
        if not isinstance(payload, dict):
            return None, 'downtown_unexpected_shape'
        current = payload.get('current')
        if not isinstance(current, dict):
            return None, 'downtown_no_current_block'
        try:
            temp_f = float(current.get('temperature_2m'))
        except (TypeError, ValueError):
            return None, 'downtown_no_temperature_field'
        if not math.isfinite(temp_f):
            return None, 'downtown_non_finite_temperature'
        return Reading(source=self.source_name,
                       station='{:.4f},{:.4f}'.format(float(lat), float(lon)),
                       temp_f=temp_f,
                       observed_ts=_parse_iso_seconds(current.get('time')),
                       raw=None), 'ok'


# ---------------------------------------------------------------------------
# Station forecast: the diurnal cycle, from open-meteo, AT THE STATION
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StationForecast:
    """open-meteo's daily extremes and hourly path for ONE station.

    Requested at the station's OWN coordinates - the ones the METAR payload
    reported - and with `timezone=auto`, so `daily_dates` are the station's
    LOCAL calendar dates. That is the only calendar a "highest temperature on
    August 18" market can possibly mean, and asking in UTC would silently shift
    the observation window by up to half a day for a station on the far side of
    the world.

    Measured live 2026-08-18 for KLGA's coordinates (40.7794, -73.8803):

        timezone 'America/New_York', utc_offset_seconds -14400
        daily.time            ['2026-08-17', '2026-08-18', '2026-08-19']
        daily.temperature_2m_max [80.2, 82.8, 89.1]  (degF, unit verified)
        daily.temperature_2m_min [68.6, 71.2, 71.6]
        hourly 72 points, '2026-08-18T15:00' -> 81.6

    `hourly_ts` is stored as UNIX SECONDS, converted from the naive local
    strings using this response's own `utc_offset_seconds`. Storing the naive
    strings and comparing them to a UTC observation timestamp later is the same
    class of error the offset exists to prevent.
    """

    req_lat: float
    req_lon: float
    grid_lat: Optional[float]
    grid_lon: Optional[float]
    utc_offset_sec: int
    timezone_name: Optional[str]
    daily_dates: Tuple[str, ...]
    daily_max_f: Tuple[Optional[float], ...]
    daily_min_f: Tuple[Optional[float], ...]
    hourly_ts: Tuple[int, ...]
    hourly_f: Tuple[float, ...]
    fetched_ts: float
    unit_received: str = 'F'
    converted: bool = False

    def extreme_for(self, local_date: str, metric: str) -> Optional[float]:
        """The forecast daily high or low for one LOCAL date, or None.

        None means the date is not in the response, which is a refusal upstream
        and never a reason to reach for a neighbouring day's number.
        """
        try:
            index = self.daily_dates.index(local_date)
        except ValueError:
            return None
        series = (self.daily_max_f if metric == 'daily_high'
                  else self.daily_min_f)
        if index >= len(series):
            return None
        return series[index]

    def local_day_bounds(self, local_date: str) -> Optional[Tuple[int, int]]:
        """`(start_ts, end_ts)` in unix seconds for a LOCAL calendar date.

        Half open: `[local midnight, next local midnight)`. Built from this
        response's own offset rather than from a timezone database, so the
        window can never disagree with the daily extremes it is paired with.

        A DST transition inside the day makes the true window 23 or 25 hours,
        and this returns a flat 24. That error is at most one hour at one edge
        of the day, it is at the ends where the extreme almost never sits, and
        fixing it needs a full tz database that this repo does not carry. It is
        stated rather than hidden, and every row carries the bounds so a
        transition day is identifiable after the fact.
        """
        try:
            day = datetime.strptime(local_date, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None
        start = int(day.replace(tzinfo=timezone.utc).timestamp()
                    ) - int(self.utc_offset_sec)
        return start, start + 86400

    def hourly_at(self, ts, tolerance_sec: int = BIAS_HOUR_TOLERANCE_SEC
                  ) -> Optional[float]:
        """The hourly forecast NEAREST `ts`, or None if none is close enough.

        Nearest rather than interpolated, and bounded by a tolerance rather than
        unbounded, because this value's only job is to measure the
        station-minus-grid bias at the moment of the observation. Reaching four
        hours away for the nearest available point would measure the bias
        against a different part of the diurnal cycle and call it a station
        offset.
        """
        target = _safe_float(ts)
        if target is None or not self.hourly_ts:
            return None
        best_i, best_gap = None, None
        for i, stamp in enumerate(self.hourly_ts):
            gap = abs(float(stamp) - target)
            if best_gap is None or gap < best_gap:
                best_i, best_gap = i, gap
        if best_i is None or best_gap > float(tolerance_sec):
            return None
        return self.hourly_f[best_i]

    def to_dict(self) -> dict:
        return {'forecast_req_lat': self.req_lat,
                'forecast_req_lon': self.req_lon,
                'forecast_grid_lat': self.grid_lat,
                'forecast_grid_lon': self.grid_lon,
                'forecast_timezone': self.timezone_name,
                'forecast_utc_offset_sec': self.utc_offset_sec,
                'forecast_days': list(self.daily_dates),
                'forecast_unit_received': self.unit_received,
                'forecast_unit_converted': self.converted,
                'forecast_hourly_points': len(self.hourly_ts)}


class StationForecastFeed(_HttpFeed):
    """open-meteo's daily extremes and hourly path at a STATION's coordinates.

    THIS IS NOT `DowntownWeatherFeed` AND THE TWO MUST NEVER BE MERGED. That one
    reads a CITY CENTRE grid cell and is diagnostic only, existing so the
    airport-versus-downtown gap can be measured. This one reads the AIRPORT's
    own grid cell and it IS an input to the price. Pointing either at the
    other's coordinates would delete the gap measurement and put the consumer
    anchor into the model at the same time.

    THE UNIT IS VERIFIED, NOT ASSUMED, and the verification is imported from
    `engine.feeds.open_meteo.normalise_unit` rather than restated here
    (convention 23). open-meteo's documented default is CELSIUS and it returns
    it under the identical field name, so a dropped `temperature_unit`
    parameter is a silent 50-degree error that reads as a huge edge. That module
    is where the marker table lives and where it is tested.

    Reads are cached per rounded coordinate for `cache_ttl_sec`. The cache is
    PER INSTANCE, unlike the METAR one: a station forecast is fetched once per
    station per weather cycle, not once per rung, so the module-level sharing
    that the METAR cache needs buys nothing here and a module-level dict would
    be one more thing `tests/conftest.py` has to remember to clear.
    """

    url = OPEN_METEO_URL
    source_name = 'station_open_meteo_forecast'

    #: Requested days. Three forward plus one back covers "today" from any local
    #: hour and "tomorrow" for a market listed the evening before, with the past
    #: day there so a market whose local date has just rolled over is still
    #: found rather than reading as outside the window.
    FORECAST_DAYS = 3
    PAST_DAYS = 1

    def __init__(self, session=None, timeout: float = FORECAST_TIMEOUT_SEC,
                 retries: int = FEED_RETRIES, sleep_fn=None,
                 cache_ttl_sec: float = FORECAST_CACHE_TTL_SEC, clock=None):
        super().__init__(session=session, timeout=timeout, retries=retries,
                         sleep_fn=sleep_fn)
        self.cache_ttl_sec = max(0.0, float(cache_ttl_sec))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        #: 'lat,lon' -> (expires_at, StationForecast)
        self._cache: Dict[str, Tuple[float, StationForecast]] = {}

    @staticmethod
    def cache_key(lat: float, lon: float) -> str:
        """Four decimals, about 11 metres - far finer than the model grid."""
        return '{:.4f},{:.4f}'.format(float(lat), float(lon))

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cached(self, key: str) -> Optional[StationForecast]:
        if self.cache_ttl_sec <= 0:
            return None
        now = float(self._clock())
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, forecast = entry
            if now >= expires_at:
                del self._cache[key]
                return None
            return forecast

    def forecast_checked(self, lat, lon
                         ) -> Tuple[Optional[StationForecast], str]:
        """`(StationForecast, 'ok')` or `(None, reason)`. Never raises.

        Every refusal has its own string so a decision row can carry the CAUSE
        (convention 20). In particular a transport failure, a 200 carrying an
        error body, a missing daily block and an unreadable unit are four
        different problems needing four different responses, and pooling them
        into "no forecast" would hide whichever is smaller.
        """
        from engine.feeds.open_meteo import normalise_unit

        lat_f = _safe_coordinate(lat, 90.0)
        lon_f = _safe_coordinate(lon, 180.0)
        if lat_f is None or lon_f is None:
            # Caught here rather than sent: open-meteo answers an out-of-range
            # coordinate with a 200 and an error body, which would arrive as the
            # much less specific `forecast_no_daily_block`.
            return None, 'forecast_bad_coordinates'

        key = self.cache_key(lat_f, lon_f)
        cached = self._cached(key)
        if cached is not None:
            self._bump('forecast_cache_hits')
            return cached, 'ok'
        self._bump('forecast_cache_misses')

        payload, status = self._get_json({
            'latitude': lat_f,
            'longitude': lon_f,
            'daily': 'temperature_2m_max,temperature_2m_min',
            'hourly': 'temperature_2m',
            # Explicit. The documented DEFAULT IS CELSIUS and it comes back
            # under the identical field name, so dropping this line is a silent
            # 50-degree error. The response's own unit is verified below anyway.
            'temperature_unit': 'fahrenheit',
            # LOCAL calendar days. Without this open-meteo answers in GMT and
            # `daily.time` stops meaning the station's own calendar day, which
            # is the only calendar the market's question refers to.
            'timezone': 'auto',
            'forecast_days': self.FORECAST_DAYS,
            'past_days': self.PAST_DAYS,
        })
        if payload is None:
            return None, status
        if not isinstance(payload, dict):
            return None, 'forecast_unexpected_shape'

        daily = payload.get('daily')
        if not isinstance(daily, dict):
            # This is the shape a bad request arrives in AFTER a 200: an
            # open-meteo error body is a dict with `error` and `reason`.
            return None, 'forecast_no_daily_block'
        hourly = payload.get('hourly')
        if not isinstance(hourly, dict):
            return None, 'forecast_no_hourly_block'

        units = payload.get('daily_units')
        marker = units.get('temperature_2m_max') if isinstance(units, dict) \
            else None
        received = normalise_unit(marker)
        if received is None:
            return None, 'forecast_unexpected_unit'
        converted = received != 'F'

        offset = _safe_float(payload.get('utc_offset_seconds'))
        if offset is None:
            # With `timezone=auto` the offset is the ONLY thing that turns a
            # naive local string into an instant. Reading the strings as UTC
            # without it would move every window boundary by whole hours.
            return None, 'forecast_no_utc_offset'

        dates = daily.get('time')
        if not isinstance(dates, list) or not dates:
            return None, 'forecast_no_daily_block'

        def _series(block, field, count):
            values = block.get(field)
            if not isinstance(values, list):
                return tuple([None] * count)
            out = []
            for i in range(count):
                raw = values[i] if i < len(values) else None
                value = _safe_float(raw)
                out.append(None if value is None
                           else (value if not converted else c_to_f(value)))
            return tuple(out)

        n_days = len(dates)
        daily_max = _series(daily, 'temperature_2m_max', n_days)
        daily_min = _series(daily, 'temperature_2m_min', n_days)

        stamps = hourly.get('time')
        temps = hourly.get('temperature_2m')
        if not isinstance(stamps, list) or not isinstance(temps, list):
            return None, 'forecast_no_hourly_block'
        hourly_ts: List[int] = []
        hourly_f: List[float] = []
        for i, stamp in enumerate(stamps):
            naive = _parse_iso_seconds(stamp)
            value = _safe_float(temps[i]) if i < len(temps) else None
            if naive is None or value is None:
                # A single unreadable hour is not a failed forecast; it is one
                # missing point, and the bias lookup refuses on its own if the
                # missing one is the hour it needed.
                self._bump('forecast_hour_unreadable')
                continue
            # `_parse_iso_seconds` read the naive string AS UTC. It is local, so
            # subtract the offset to recover the true instant.
            hourly_ts.append(naive - int(offset))
            hourly_f.append(value if not converted else c_to_f(value))

        forecast = StationForecast(
            req_lat=lat_f, req_lon=lon_f,
            grid_lat=_safe_float(payload.get('latitude')),
            grid_lon=_safe_float(payload.get('longitude')),
            utc_offset_sec=int(offset),
            timezone_name=(payload.get('timezone')
                           if isinstance(payload.get('timezone'), str)
                           else None),
            daily_dates=tuple(str(d) for d in dates),
            daily_max_f=daily_max,
            daily_min_f=daily_min,
            hourly_ts=tuple(hourly_ts),
            hourly_f=tuple(hourly_f),
            fetched_ts=float(self._clock()),
            unit_received=received,
            converted=converted)

        if self.cache_ttl_sec > 0:
            with self._lock:
                self._cache[key] = (float(self._clock()) + self.cache_ttl_sec,
                                    forecast)
        self._bump('forecast_ok')
        return forecast, 'ok'


# ---------------------------------------------------------------------------
# Rules text, thresholds, discovery
# ---------------------------------------------------------------------------

def rules_text(market) -> str:
    """Join every field on a Gamma market that could carry the rules.

    Returns '' when the market carries no rules text at all, which is a refusal
    upstream and never a licence to fall back on the station table.
    """
    raw = getattr(market, 'raw', None)
    if not isinstance(raw, dict):
        return ''
    parts: List[str] = []
    for key in RULES_TEXT_FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return '\n'.join(parts)


def stations_named_in(text: str) -> List[str]:
    """Every distinct ICAO the text names IN PROSE, via `STATION_ALIASES`.

    Four-letter codes are matched on a word boundary so `KDEN` cannot fire
    inside an unrelated token. Multi-word station names are matched as plain
    substrings, where a boundary regex buys nothing.

    This route is alias-gated on purpose: a bare four-letter word in prose is
    not self-evidently a station, so a code we do not already know reads as
    unreadable rather than as a station. `stations_in_urls` is the opposite
    case and is not alias-gated; see there for why.
    """
    lowered = str(text or '').lower()
    found = set()
    for alias, icao in STATION_ALIASES.items():
        if len(alias) == 4 and alias.isalpha():
            if re.search(r'\b' + re.escape(alias) + r'\b', lowered):
                found.add(icao)
        elif alias in lowered:
            found.add(icao)
    return sorted(found)


#: An ICAO sitting in a RESOLUTION URL. This is the form every real Polymarket
#: temperature market uses and the form the alias table could never cover:
#:
#:     https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA
#:     https://www.weather.gov/wrh/timeseries?site=LTFM
#:
#: Measured over 1,727 live markets this yields 50 distinct codes and ZERO
#: false positives, and it is why coverage went from 132 markets to 1,661.
#:
#: Unlike `stations_named_in` this is NOT alias-gated, and that is a deliberate
#: difference rather than an oversight. A four-letter uppercase token in the
#: final path segment of the market's own resolution link, or in its `site=`
#: parameter, IS the station identifier - the URL is the market telling us
#: where to look, not prose we are interpreting. Requiring UPPERCASE and a
#: URL-shaped prefix is what keeps `/history/` and `/daily/` out.
_ICAO_IN_URL_RE = re.compile(
    r'(?:/|site=|station=|stid=|sid=)(?P<icao>[A-Z]{4})(?![A-Za-z0-9])')


def stations_in_urls(text: str) -> List[str]:
    """Every distinct ICAO the text names inside a resolution URL. Sorted."""
    return sorted(set(_ICAO_IN_URL_RE.findall(str(text or ''))))


def all_stations_named_in(text: str) -> List[str]:
    """Union of the prose route and the URL route. Sorted."""
    return sorted(set(stations_named_in(text)) | set(stations_in_urls(text)))


def resolution_station_checked(market,
                               allow_fallback: bool = False
                               ) -> Tuple[Optional[str], str]:
    """Which station does this market SAY it resolves on?

    Returns `(icao, 'ok')`, `(icao, 'ok_from_city_fallback_table')`,
    `(None, 'resolution_station_unknown')` or
    `(None, 'resolution_station_ambiguous')`.

    THE RULES TEXT WINS. Both the URL route and the prose route read the
    market's own text, and the fallback table is consulted ONLY when the text
    named nothing at all. When the text names something, the table cannot
    override it and cannot even be reached.

    A station named in prose but absent from `STATION_ALIASES` reads as
    `resolution_station_unknown`. That is the honest answer: we could not read
    it, so we do not trade it. It is NOT "there is no station".

    Two DIFFERENT stations in one rules text is `resolution_station_ambiguous`
    and its own cause. Picking the first is a coin flip on the whole position.
    Note the two routes agreeing is not ambiguity: the NYC rules text says both
    "LaGuardia Airport Station" and `.../new-york-city/KLGA`, which is one
    station named twice, and the union is taken before the count.
    """
    text = rules_text(market)
    if text.strip():
        found = all_stations_named_in(text)
        if len(found) > 1:
            return None, 'resolution_station_ambiguous'
        if found:
            return found[0], 'ok'
    if allow_fallback:
        city = city_name_from_question(market)
        icao = CITY_STATION_FALLBACK.get(city or '')
        if icao is not None:
            return icao, 'ok_from_city_fallback_table'
    return None, 'resolution_station_unknown'


def city_for_market(market) -> Optional[str]:
    """Which `WEATHER_MARKETS` key this market's slug looks like, or None.

    Used ONLY to pick a downtown lat/lon, to shortlist markets in discovery, and
    to cross-check the station table against the rules text. Never used to
    decide resolution.
    """
    slug = str(getattr(market, 'slug', '') or '').lower()
    for key, city in WEATHER_MARKETS.items():
        for pattern in city['slug_patterns']:
            if pattern in slug:
                return key
    question = str(getattr(market, 'question', '') or '').lower()
    for key, city in WEATHER_MARKETS.items():
        for alias in city['aliases']:
            if len(alias) == 4 and alias.isalpha():
                continue
            if alias.lower() in question:
                return key
    return None


#: "Will the highest temperature in Seoul (Incheon) be 22C on August 18?" ->
#: "seoul". The parenthetical is the airport, not the city, and keeping it
#: would split one city across two report buckets.
_CITY_IN_QUESTION_RE = re.compile(
    r'temperature\s+in\s+(?P<city>.+?)\s+be\b', re.IGNORECASE)


def city_name_from_question(market) -> Optional[str]:
    """The city a ladder question names, lowercased, or None.

    Separate from `city_for_market` and NOT a replacement for it. This one is
    for REPORTING (the `by_city` breakdown) and for the fallback station table,
    both of which need all 51 cities Polymarket lists. `city_for_market` keys
    into `WEATHER_MARKETS`, which carries downtown coordinates for five cities
    only, and is the one used to pick a downtown grid cell. Collapsing them
    would mean either inventing coordinates or losing 46 cities from the
    report, so they stay two functions with two jobs.
    """
    question = str(getattr(market, 'question', '') or '')
    match = _CITY_IN_QUESTION_RE.search(question)
    if match is None:
        return None
    city = re.sub(r'\s*\([^)]*\)', '', match.group('city')).strip().lower()
    return city or None


#: What the resolution source's own rules text says about REPORTING PRECISION.
#: Live text, all three variants:
#:     "measures temperatures to whole degrees Celsius (eg, 9C)"      1,419
#:     "measures temperatures to whole degrees Fahrenheit (eg, 21F)"    286
#:     "measures temperatures in Celsius to one decimal place"           66
_REPORTING_PRECISION_RE = re.compile(
    r'measures\s+temperatures?\s*'
    r'(?:in\s+(?:celsius|fahrenheit|centigrade)\s*)?'
    r'to\s+(?P<prec>whole\s+degrees?|one\s+decimal\s+place)',
    re.IGNORECASE)


def reporting_step_checked(market) -> Tuple[Optional[float], str]:
    """How finely does the resolution source report, in NATIVE degrees?

    Returns `(1.0, 'ok')`, `(0.1, 'source_precision_finer_than_ladder_step')`
    or `(None, 'source_reporting_precision_unknown')`.

    THIS GATE EXISTS BECAUSE THE LADDER EDGES DEPEND ON IT. A rung labelled 84
    means "the reported value is 84". When the source reports whole degrees,
    reported == round(true), so the rung is exactly [83.5, 84.5) and the whole
    eleven-rung ladder tiles the line with no gap and no overlap. When the
    source reports to one decimal place - which the 66 Hong Kong markets do -
    that identity fails and Polymarket has not published where the rung edges
    sit. [26.5, 27.5) and [27.0, 28.0) are both readings of "27C", they differ
    by half a degree, and half a degree against a 0.75F sigma floor is most of
    the claimed edge. So those markets are REFUSED under their own reason
    rather than priced on a guessed edge.

    An unstated precision is also a refusal, and a separate one. Measured, all
    1,771 live city markets state theirs, so this costs nothing today and is
    the gate that catches Polymarket quietly changing the wording.
    """
    text = rules_text(market)
    if not text.strip():
        return None, 'source_reporting_precision_unknown'
    match = _REPORTING_PRECISION_RE.search(text)
    if match is None:
        return None, 'source_reporting_precision_unknown'
    if 'whole' in match.group('prec').lower():
        return 1.0, 'ok'
    return 0.1, 'source_precision_finer_than_ladder_step'


#: Comparison words, split by which way Yes has to go. `at most` and `at least`
#: are whole phrases because "at" alone is meaningless.
_BELOW_WORDS = frozenset(('below', 'under', 'less than', 'lower than',
                          'at most'))
_ABOVE_WORDS = frozenset(('exceed', 'exceeds', 'above', 'over', 'at least',
                          'greater than', 'higher than'))

#: The POSTFIX comparators, which is the form every live market uses:
#: "84F or below". Kept separate from the prefix words because the two forms
#: mean different things - see `parse_threshold_checked`.
_POSTFIX_BELOW_WORDS = frozenset(('below', 'lower', 'less', 'under', 'colder'))
_POSTFIX_ABOVE_WORDS = frozenset(('higher', 'above', 'more', 'greater', 'over',
                                  'warmer'))

#: U+00B0 DEGREE SIGN and U+00BA MASCULINE ORDINAL. Polymarket ships both, and
#: the second one is not a degree sign at all - it is the character the global
#: temperature family uses. A unit regex that knows only U+00B0 reads
#: "1.29ºC" as a bare number.
_DEGREE_CHARS = '°º'

#: The unit suffix, OPTIONAL in the pattern so that "no unit" arrives as a
#: distinct outcome rather than as "no match". Two drop causes, two numbers
#: (convention 20): `no_threshold_pattern` and `unit_missing_or_ambiguous` are
#: completely different problems and pooling them would hide whichever is
#: smaller.
_UNIT_SUFFIX = (r'\s*(?:degrees?\s*)?(?:[' + _DEGREE_CHARS + r']\s*)?'
                r'(?P<unit>fahrenheit|celsius|centigrade|f|c)?\b')

_NUM = r'-?\d+(?:\.\d+)?'

#: "be between 76-77F". The Fahrenheit ladders are TWO degrees wide per rung,
#: which the Celsius ladders are not. Separator covers the hyphen, U+2013 EN
#: DASH, U+2014 EM DASH (escaped, never written literally - house rule), "to"
#: and "and".
_RANGE_RE = re.compile(
    r'between\s+(?P<lo>' + _NUM + r')\s*(?:-|\u2013|\u2014|to|and)\s*'
    r'(?P<hi>' + _NUM + r')' + _UNIT_SUFFIX, re.IGNORECASE)

#: "84F or below", "33C or higher". THE form that broke the original parser.
_POSTFIX_RE = re.compile(
    r'(?P<value>' + _NUM + r')' + _UNIT_SUFFIX +
    r'\s*or\s+(?P<post>below|lower|less|under|colder|higher|above|more|'
    r'greater|over|warmer)\b', re.IGNORECASE)

#: "be 84F" with nothing after it: the exact-degree rung.
_BUCKET_RE = re.compile(
    r'\bbe\s+(?P<value>' + _NUM + r')' + _UNIT_SUFFIX + r'(?!\s*or\s)',
    re.IGNORECASE)

#: The legacy PREFIX form, "exceed 85F". No live Polymarket market uses it, but
#: it is a perfectly ordinary way to write a threshold and dropping support
#: would be a regression for no gain.
_THRESHOLD_RE = re.compile(
    r'\b(?P<word>exceeds|exceed|above|over|at least|greater than|higher than|'
    r'below|under|less than|lower than|at most)\s*'
    r'(?P<value>' + _NUM + r')' + _UNIT_SUFFIX,
    re.IGNORECASE)

#: Every unit marker in the question, used to catch a question that names BOTH.
#: A question carrying "C" in one place and "F" in another is not a question we
#: get to pick a side of.
_ANY_UNIT_RE = re.compile(
    r'' + _NUM + r'\s*(?:degrees?\s*)?(?:[' + _DEGREE_CHARS + r']\s*)?'
    r'(fahrenheit|celsius|centigrade|f|c)\b', re.IGNORECASE)

#: A number with an explicit TEMPERATURE unit marker. Used only by discovery:
#: "Will BTC exceed 50000?" parses as a threshold under a unit-free regex, and
#: shortlisting it as a weather market would be a false positive that costs a
#: station lookup and a confusing skip row.
_TEMP_UNIT_MARKER_RE = re.compile(
    r'' + _NUM + r'\s*(?:[' + _DEGREE_CHARS + r']\s*)?'
    r'(?:f\b|c\b|degrees\b|fahrenheit\b|celsius\b)', re.IGNORECASE)

#: The OTHER market family under the weather tag. A planetary anomaly index,
#: not a city airport station, and not something any METAR reading prices.
_GLOBAL_TEMPERATURE_RE = re.compile(
    r'global\s+temperature|global\s+average\s+temperature|'
    r'temperature\s+anomaly', re.IGNORECASE)

#: Every outcome `parse_threshold_checked` can report. One string per cause.
THRESHOLD_PARSE_REASONS = (
    'ok',
    'question_empty',
    'global_temperature_market',
    'no_threshold_pattern',
    'unit_missing_or_ambiguous',
    'non_integer_ladder_rung',
    'range_bounds_inverted',
)

#: `kind` values that describe a LADDER rung, meaning the edges were derived
#: from a half-step and therefore depend on the source reporting whole degrees.
LADDER_KINDS = ('at_or_below', 'at_or_above', 'exact_bucket', 'range_bucket')


def is_global_temperature_market(question) -> bool:
    """True for the planetary-anomaly family, which is NOT a city market."""
    if not isinstance(question, str):
        return False
    return _GLOBAL_TEMPERATURE_RE.search(question) is not None


def _normalise_unit(token) -> Optional[str]:
    """'c' / 'Celsius' / 'centigrade' -> 'C'. 'f' / 'Fahrenheit' -> 'F'."""
    if not token:
        return None
    head = str(token).strip().lower()[0]
    if head == 'c':
        return 'C'
    if head == 'f':
        return 'F'
    return None                              # pragma: no cover - regex guards


def _units_in(question: str) -> set:
    """Every distinct unit the question names. More than one is ambiguous."""
    return {_normalise_unit(tok) for tok in _ANY_UNIT_RE.findall(question)} - {
        None}


def to_fahrenheit(value: Optional[float], unit: str) -> Optional[float]:
    """Convert a NATIVE-unit temperature to Fahrenheit. None passes through.

    None means "this edge is unbounded", and an unbounded edge stays unbounded
    in any unit. Sending it through `c_to_f` would turn it into 32.0.
    """
    if value is None:
        return None
    return float(value) if unit == 'F' else c_to_f(value)


@dataclass(frozen=True)
class Threshold:
    """A parsed temperature threshold, as a HALF-OPEN INTERVAL `[lo_f, hi_f)`.

    Yes resolves true when the resolution reading lands inside the interval.
    `None` on either edge means unbounded on that side, so the two ladder tails
    and the interior buckets are the same object with different edges.

    The public shape the rest of the repo already reads is preserved:

        value_f     the single number a human would call "the threshold", in
                    FAHRENHEIT. For a tail that is the decision boundary. For a
                    bucket it is the rung CENTRE, which is not a boundary, so
                    `kind` is on every row and must be read alongside it.
        above       True / False for a tail, None for a bucket, where "neither
                    above nor below" is the honest answer.
        matched_text the substring that produced all of this.

    `native_lo` / `native_hi` are the SAME interval in the market's own unit,
    kept so a log row can be checked by eye against the question text without
    anyone having to run the conversion backwards. That is not decoration: a
    Celsius bucket whose F edges look 1.8 apart instead of 1.0 is either
    correct or a bug, and the native pair is what tells the two apart.
    """

    value_f: Optional[float]
    above: Optional[bool]
    matched_text: str
    kind: str = ''
    unit: str = 'F'
    lo_f: Optional[float] = None
    hi_f: Optional[float] = None
    native_lo: Optional[float] = None
    native_hi: Optional[float] = None
    ladder_step_native: Optional[float] = None

    def __post_init__(self):
        # Back-compatible construction: `Threshold(85.0, True, 'exceed 85F')`
        # still means "Yes needs above 85F" and fills its own interval in.
        if not self.kind:
            if self.above is True:
                object.__setattr__(self, 'kind', 'at_or_above')
            elif self.above is False:
                object.__setattr__(self, 'kind', 'at_or_below')
            else:
                object.__setattr__(self, 'kind', 'exact_bucket')
        if self.lo_f is None and self.hi_f is None and self.value_f is not None:
            if self.kind == 'at_or_above':
                object.__setattr__(self, 'lo_f', float(self.value_f))
            elif self.kind == 'at_or_below':
                object.__setattr__(self, 'hi_f', float(self.value_f))

    @property
    def is_ladder_rung(self) -> bool:
        """True when the edges came from a half-step off an integer rung."""
        return self.ladder_step_native is not None

    def to_dict(self) -> dict:
        def r(value):
            return None if value is None else round(float(value), 4)

        return {'threshold_f': self.value_f,
                'yes_needs_above': self.above,
                'threshold_matched_text': self.matched_text,
                'threshold_kind': self.kind,
                'threshold_unit': self.unit,
                'threshold_lo_f': r(self.lo_f),
                'threshold_hi_f': r(self.hi_f),
                'threshold_native_lo': r(self.native_lo),
                'threshold_native_hi': r(self.native_hi),
                'threshold_is_ladder_rung': self.is_ladder_rung,
                'ladder_step_native': self.ladder_step_native}


def _ladder(matched_text: str, unit: str, native_lo: Optional[float],
            native_hi: Optional[float], kind: str, step: float,
            value_native: float, above: Optional[bool]) -> Threshold:
    """Build a ladder rung. THE CONVERSION HAPPENS HERE AND ONLY HERE.

    `native_lo` and `native_hi` arrive already widened by the half-step IN THE
    MARKET'S OWN UNIT, and are converted afterwards. Widening in Fahrenheit
    instead would make every Celsius rung 1.0F wide where it should be 1.8F, a
    44% understatement of the bucket that shows up as a fabricated edge on
    every interior rung of every Celsius ladder. There is a test that pins the
    ordering rather than the result, because the result is right by accident
    under either ordering when the unit is already Fahrenheit.
    """
    return Threshold(value_f=to_fahrenheit(value_native, unit),
                     above=above,
                     matched_text=matched_text,
                     kind=kind,
                     unit=unit,
                     lo_f=to_fahrenheit(native_lo, unit),
                     hi_f=to_fahrenheit(native_hi, unit),
                     native_lo=native_lo,
                     native_hi=native_hi,
                     ladder_step_native=step)


def parse_threshold_checked(question) -> Tuple[Optional[Threshold], str]:
    """Pull the threshold out of a market question, WITH a refusal reason.

    `(Threshold, 'ok')` or `(None, reason)`, reason from
    `THRESHOLD_PARSE_REASONS`. There is deliberately NO default anywhere in
    here: a strategy that falls back to a guessed threshold, or a guessed UNIT,
    when it cannot read the real one is trading a number it invented, and on a
    binary that is the whole position.

    Four forms, tried in this order because the later ones are prefixes of the
    earlier ones:

        1. "be between 76-77F"   range bucket   [75.5, 77.5)
        2. "84F or below"        tail           (-inf, 84.5)
           "88F or higher"       tail           [87.5, +inf)
        3. "exceed 85F"          legacy prefix  [85.0, +inf)
        4. "be 84F"              exact bucket   [83.5, 84.5)

    THE HALF-STEP IS ONLY ON THE LADDER FORMS, and that asymmetry is the point,
    not an inconsistency. A ladder rung is a statement about the REPORTED
    value, which the rules text says is a whole number of degrees, so
    "reported <= 84" is "true temperature < 84.5". A prefix comparison
    ("exceed 85F") is a statement about the temperature itself and has no
    rounding step in it. Both are correct for their own form, and the row
    carries `threshold_is_ladder_rung` so a reader never has to guess which
    they are looking at.

    "Highest temperature in NYC on August 18?" carries no threshold at all and
    correctly returns `no_threshold_pattern`. That is a scalar market, not a
    binary this strategy can price.
    """
    if not isinstance(question, str) or not question.strip():
        return None, 'question_empty'
    if is_global_temperature_market(question):
        return None, 'global_temperature_market'

    units = _units_in(question)
    if len(units) > 1:
        return None, 'unit_missing_or_ambiguous'

    match = _RANGE_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        lo, hi = float(match.group('lo')), float(match.group('hi'))
        if not (float(lo).is_integer() and float(hi).is_integer()):
            return None, 'non_integer_ladder_rung'
        if hi < lo:
            return None, 'range_bounds_inverted'
        return _ladder(match.group(0).strip(), unit, lo - 0.5, hi + 0.5,
                       'range_bucket', float(hi - lo + 1.0),
                       (lo + hi) / 2.0, None), 'ok'

    match = _POSTFIX_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not float(value).is_integer():
            return None, 'non_integer_ladder_rung'
        word = match.group('post').lower()
        if word in _POSTFIX_BELOW_WORDS:
            return _ladder(match.group(0).strip(), unit, None, value + 0.5,
                           'at_or_below', 1.0, value + 0.5, False), 'ok'
        return _ladder(match.group(0).strip(), unit, value - 0.5, None,
                       'at_or_above', 1.0, value - 0.5, True), 'ok'

    match = _THRESHOLD_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not math.isfinite(value):         # pragma: no cover - regex guards
            return None, 'no_threshold_pattern'
        above = match.group('word').lower() in _ABOVE_WORDS
        value_f = to_fahrenheit(value, unit)
        return Threshold(value_f=value_f, above=above,
                         matched_text=match.group(0).strip(),
                         kind='at_or_above' if above else 'at_or_below',
                         unit=unit,
                         lo_f=value_f if above else None,
                         hi_f=None if above else value_f,
                         native_lo=value if above else None,
                         native_hi=None if above else value,
                         ladder_step_native=None), 'ok'

    match = _BUCKET_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not float(value).is_integer():
            return None, 'non_integer_ladder_rung'
        return _ladder(match.group(0).strip(), unit, value - 0.5, value + 0.5,
                       'exact_bucket', 1.0, value, None), 'ok'

    return None, 'no_threshold_pattern'


def parse_threshold(question) -> Optional[Threshold]:
    """`parse_threshold_checked` without the reason. None means unparseable."""
    threshold, _reason = parse_threshold_checked(question)
    return threshold


def market_metric(question) -> Optional[str]:
    """'daily_high' / 'daily_low' / None.

    Recorded on every row, never gated on. It exists because the two ladders
    are biased in OPPOSITE directions by the point-in-time model (see the
    module docstring), and a pooled win rate across both would average the two
    biases into a number that looks unbiased and is not.
    """
    text = str(question or '').lower()
    if 'highest temperature' in text:
        return 'daily_high'
    if 'lowest temperature' in text:
        return 'daily_low'
    return None


# ---------------------------------------------------------------------------
# The DAILY EXTREME model
# ---------------------------------------------------------------------------

_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july',
           'august', 'september', 'october', 'november', 'december')

#: "... on August 18?" and "... on Aug 18, 2026?". The DAY the observation
#: window covers, which is the one thing `endDate` demonstrably does not tell us
#: (see `WeatherArb.hours_to_resolution`: Madrid's settles at 14:00 local, in
#: the middle of the very day it is about).
_RESOLUTION_DATE_RE = re.compile(
    r'\bon\s+(?P<month>' + '|'.join(m[:3] for m in _MONTHS) +
    r')[a-z]*\.?\s+(?P<day>\d{1,2})\b', re.IGNORECASE)


def resolution_month_day(question) -> Optional[Tuple[int, int]]:
    """`(month, day)` from a market question, or None. Year deliberately absent.

    A question says "on August 18" and never says which year. Guessing one is
    guessing across a year boundary at exactly the moment the guess matters, so
    the year is resolved LATER against the forecast's own list of local dates -
    which contains only real dates, in the station's own calendar, around now.
    """
    match = _RESOLUTION_DATE_RE.search(str(question or ''))
    if match is None:
        return None
    head = match.group('month').lower()[:3]
    month = next((i + 1 for i, name in enumerate(_MONTHS)
                  if name.startswith(head)), None)
    day = int(match.group('day'))
    if month is None or not 1 <= day <= 31:
        return None
    return month, day


def resolution_local_date_checked(question, forecast: StationForecast
                                  ) -> Tuple[Optional[str], str]:
    """The market's observation day as a LOCAL 'YYYY-MM-DD', or a refusal.

    Returns `(date, 'ok')`, `(None, 'resolution_date_unparseable')` or
    `(None, 'resolution_date_outside_forecast_window')`.

    THE YEAR COMES FROM THE FORECAST, NOT FROM `time.time()`. The forecast's
    `daily_dates` are four consecutive real dates in the station's own calendar,
    so matching month and day against them resolves the year exactly and cannot
    be wrong across a New Year boundary. If no listed day matches, the market is
    about a date we have no forecast for, and that is a REFUSAL rather than an
    invitation to extrapolate: convention 11, a cannot-run and not a result.

    Two matching dates would mean a forecast spanning more than a year, which
    cannot happen with `past_days=1, forecast_days=3`. The first match is taken
    and the impossibility is not defended against, because a guard for it would
    be untestable and would read as though it were possible.
    """
    parsed = resolution_month_day(question)
    if parsed is None:
        return None, 'resolution_date_unparseable'
    month, day = parsed
    wanted = '{:02d}-{:02d}'.format(month, day)
    for date in forecast.daily_dates:
        if date[5:] == wanted:
            return date, 'ok'
    return None, 'resolution_date_outside_forecast_window'


@dataclass(frozen=True)
class DailyExtremeEstimate:
    """The predictive distribution of a DAY'S EXTREME at one station.

    ## THE RANDOM VARIABLE, STATED EXACTLY

        M = max(O, X)   for a "highest temperature" market
        M = min(O, X)   for a "lowest temperature" market

    `O` is the extreme the station has ALREADY REPORTED inside the local
    observation day. It is not a random variable at all: it has happened, it is
    observed, and it is a hard bound on `M`. `X` is the extreme over the REST of
    the day and it is the only thing being modelled.

    ## THE DISTRIBUTIONAL ASSUMPTION, AND WHAT WOULD FALSIFY IT

        X ~ Normal(mu, sigma),
        mu    = open-meteo's forecast daily extreme for this local date at this
                station's own coordinates, PLUS the current station-minus-grid
                bias, and
        sigma = DAILY_EXTREME_SIGMA_FLOOR_F
                + DAILY_EXTREME_SIGMA_PER_SQRT_HOUR_F * sqrt(hours until the
                  local day closes).

    Three things are being assumed and each can be attacked separately:

      1. NORMALITY OF A REMAINING-DAY EXTREME. A maximum is a max-stable
         quantity and its asymptotic law is Gumbel, not Gaussian: the true
         distribution is right-skewed, so a normal understates the upper tail
         of a daily HIGH and overstates the lower tail of a daily LOW. It is
         used anyway because the remaining-day max here is dominated by ONE
         deterministic afternoon peak rather than by a large number of
         independent draws, which is the regime where the extreme-value limit
         has not bitten yet and forecast error, which is roughly symmetric,
         dominates.
      2. THE FORECAST IS AN UNBIASED CENTRE ONCE THE STATION BIAS IS REMOVED.
         open-meteo serves a blended model on a grid cell; the station sits
         inside that cell and reads systematically warmer or cooler than it.
         `bias_f` removes the CURRENT offset and assumes it persists to the
         peak. It will not persist exactly: an airport on an open field and a
         model grid cell diverge most at the diurnal extremes, which is where
         it matters.
      3. THE SIGMA CONSTANTS. Convention 15: written before any run, never
         fitted. See `DAILY_EXTREME_SIGMA_FLOOR_F`.

    FALSIFIED BY: the probability integral transform. Take the realised daily
    extreme at the station, evaluate this predictive CDF at it, and collect the
    values over resolved markets. Under a correct model they are uniform on
    [0, 1]. The model is falsified if, on at least 200 station-days,

      - more than 10% of realised extremes fall outside the central 90%
        interval (a sigma that is too small), or
      - the mean PIT value differs from 0.5 by more than 0.05 (a biased mu), or
      - the PIT histogram is visibly right-skewed for daily highs, which is
        assumption 1 failing and calls for a Gumbel tail rather than a wider
        normal.

    The named harness is `backtest/measure_daily_extreme_calibration.py` and it
    now exists. It measures the PREDICTIVE ERROR (assumption 3) and reports the
    residual MEAN alongside the spread, which is a direct read on assumption 2:
    a station whose residual mean is +2.5F at a 24-hour lead is a station where
    the bias did not persist to the peak. It does NOT test assumption 1 - the
    PIT histogram needs resolved markets and there are none - so normality is
    still assumed rather than checked.

    ## WHAT THIS FIXES, IN THE TWO ROWS THAT MOTIVATED IT

    Madrid 2026-08-18, station 33.0C at 11:00 UTC, market "the highest today is
    39C" at 0.70. The OLD model priced the reading at the settlement stamp and
    said 0.000024. This one prices the day's forecast peak with the observed
    33.0C as a floor, which is a completely different question and no longer the
    one the market was obviously right about.

    Buenos Aires the same minute, station 7.0C, "highest today is 8C or below"
    at 0.001. The OLD model said 0.87 because 7.0C is below 8.5C. This one takes
    the day's forecast maximum, which is the warmer afternoon the market was
    pricing, and the observed 7.0C floor does not help the "or below" side at
    all. Note the two errors pointed in OPPOSITE directions, which is why a
    pooled win rate across the two ladders would have averaged them into
    something that looked unbiased.
    """

    metric: str                        # 'daily_high' | 'daily_low'
    mu_f: float
    sigma_f: float
    forecast_extreme_f: float
    bias_f: float
    observed_extreme_f: Optional[float]
    observations_used: int
    hours_to_window_close: float
    local_date: str
    window_start_ts: int
    window_end_ts: int
    #: WHERE THE SIGMA CAME FROM. Defaulted so every existing construction site
    #: and every existing test keeps its exact meaning: an estimate written
    #: before any run, which is what those rows were.
    #:
    #: `'house_constants_unfitted'`            convention 15 estimate
    #: `'fitted_lead_bucket'`                  measured residuals at this
    #:                                         station, in the lead bucket this
    #:                                         horizon falls in
    #: `'fitted_curve_outside_measured_bucket'` the fitted curve, extrapolated
    #:
    #: The three are never pooled downstream, which is the whole reason this is
    #: a string on the row rather than a boolean.
    sigma_source: str = 'house_constants_unfitted'
    #: Station-days behind a fitted sigma. None for the house constants, which
    #: have no sample behind them at all. Convention 7 lives or dies on this
    #: number being visible: a sigma fitted on 7 station-days and one fitted on
    #: 537 are the same float carrying completely different information.
    sigma_n: Optional[int] = None
    #: True when the horizon sat outside every lead bucket the harness could
    #: measure - in practice, under 24 hours, where open-meteo's archive has no
    #: model run to verify against.
    sigma_horizon_is_extrapolated: bool = False

    def to_dict(self) -> dict:
        return {
            'daily_extreme_model': 'forecast_anchored_normal_floored_by_observed',
            'daily_extreme_metric': self.metric,
            'daily_extreme_mu_f': round(self.mu_f, 3),
            'daily_extreme_sigma_f': round(self.sigma_f, 3),
            'daily_extreme_forecast_f': round(self.forecast_extreme_f, 3),
            'station_minus_grid_bias_f': round(self.bias_f, 3),
            'observed_extreme_f': (None if self.observed_extreme_f is None
                                   else round(self.observed_extreme_f, 2)),
            'observed_extreme_count': self.observations_used,
            'hours_to_window_close': round(self.hours_to_window_close, 3),
            'observation_local_date': self.local_date,
            'observation_window_start_ts': self.window_start_ts,
            'observation_window_end_ts': self.window_end_ts,
            'model_prices_daily_extreme': True,
            # No longer a constant. It says whether THIS ROW's sigma was
            # measured, which is the fact a downstream reader needs; a flag that
            # was always True after the harness landed would have told them
            # nothing about the row in front of them.
            'sigma_constants_are_estimates_never_fitted': (
                self.sigma_source == 'house_constants_unfitted'),
            'daily_extreme_sigma_source': self.sigma_source,
            'daily_extreme_sigma_n': self.sigma_n,
            'sigma_horizon_is_extrapolated': self.sigma_horizon_is_extrapolated,
            # The harness EXISTS as of 2026-08-18. What it has not done is score
            # this model against a RESOLVED market, because no weather position
            # has ever resolved - so the second flag stays False and the two
            # facts stay separate (convention 11).
            'daily_extreme_calibration_harness_exists': True,
            'daily_extreme_calibration_harness': CALIBRATION_HARNESS,
            'daily_extreme_model_scored_on_resolved_markets': False,
        }


def daily_extreme_cdf(x: float, estimate: DailyExtremeEstimate) -> float:
    """P(the day's extreme is strictly below `x`).

    The observed part is EXACT, not modelled, and that is the whole structural
    fix. For a daily high, `M = max(O, X)`, so

        P(M < x) = 1{O < x} * P(X < x)

    and once the station has already reported something at or above `x` the
    probability is exactly zero - no sigma, no forecast, no argument. For a
    daily low, `M = min(O, X)`, so

        P(M < x) = 1 - (1 - 1{O < x})(1 - P(X < x))
                 = 1 if O < x else P(X < x)

    A `sigma <= 0` is impossible through the constructor (the floor is > 0) but
    is handled as the degenerate point mass rather than dividing by it.
    """
    value = float(x)
    sigma = float(estimate.sigma_f)
    observed = estimate.observed_extreme_f

    if sigma > 0:
        p_x = _NORMAL.cdf((value - float(estimate.mu_f)) / sigma)
    else:                                    # pragma: no cover - floor is > 0
        p_x = 1.0 if float(estimate.mu_f) < value else 0.0

    if estimate.metric == 'daily_low':
        if observed is not None and float(observed) < value:
            return 1.0
        return p_x
    # daily_high
    if observed is not None and float(observed) >= value:
        return 0.0
    return p_x


def probability_yes_daily_extreme(threshold: 'Threshold',
                                  estimate: DailyExtremeEstimate) -> float:
    """P(the day's extreme lands inside the rung), as `CDF(hi) - CDF(lo)`.

    Same interval arithmetic as the point-in-time `probability_yes`, over a
    different random variable. An unbounded edge collapses the corresponding
    term to 1 or 0, so a tail rung comes out one-sided without a second branch.

    Clamped at both ends: the observed-extreme indicator can make `CDF(hi)` and
    `CDF(lo)` land on the same value, and floating point can then produce a hair
    below zero, which would walk straight into a negative edge and an entry.
    """
    lo, hi = threshold.lo_f, threshold.hi_f
    p_hi = 1.0 if hi is None else daily_extreme_cdf(hi, estimate)
    p_lo = 0.0 if lo is None else daily_extreme_cdf(lo, estimate)
    return max(0.0, min(1.0, p_hi - p_lo))


def max_attainable_p_yes(threshold: 'Threshold', sigma_f: float
                         ) -> Optional[float]:
    """The LARGEST Yes probability this model could ever return for a rung.

    For a bounded interval of width `w` under a normal of standard deviation
    `sigma`, the probability is maximised when the mean sits at the interval's
    centre, and it equals

        2 * Phi(w / (2 * sigma)) - 1

    which depends on NOTHING but the width and the sigma. Not on the
    temperature, not on the forecast, not on the observation.

    Returns None for an unbounded rung, where the answer is 1.0 and the concept
    does not bite: a tail can always be made certain by a far enough mean.

    ## THIS IS THE NUMBER THAT EXPOSED A FABRICATED EDGE

    Measured live 2026-08-18, Madrid, "Will the highest temperature in Madrid be
    36C on August 19?". A Celsius bucket is 1.8F wide. At 31.5 hours to the
    local day's close the sigma is 2.96F, so

        p_max = 2 * Phi(1.8 / 5.92) - 1 = 0.239

    The book quoted that rung at 0.64. The model returned 0.238 - which is
    p_max to three decimals, i.e. the model was ALREADY at its ceiling and the
    forecast was sitting almost exactly on the rung. It then "disagreed" with
    the market, took the No side, and booked a 0.43 realised edge.

    That edge is arithmetic, not information. Below 0.5 the model CANNOT say Yes
    on that rung no matter what the weather does, so it takes the No side of
    every interior rung of every Celsius ladder, every cycle, forever. Nine of
    the eleven rungs on a ladder are interior. A win rate over those rows would
    be measuring the width of a bucket against the width of our sigma.

    It is the same shape as the strike-proxy noise floor and it gets the same
    treatment: refuse where the instrument cannot resolve, rather than record a
    measurement error as a decision (convention 11).
    """
    lo, hi = threshold.lo_f, threshold.hi_f
    if lo is None or hi is None:
        return None
    sigma = float(sigma_f)
    if sigma <= 0:                           # pragma: no cover - floor is > 0
        return 1.0
    return 2.0 * _NORMAL.cdf((float(hi) - float(lo)) / (2.0 * sigma)) - 1.0


def daily_extreme_sigma_f(hours_to_window_close: float,
                          floor_f: float = DAILY_EXTREME_SIGMA_FLOOR_F,
                          per_sqrt_hour_f: float =
                          DAILY_EXTREME_SIGMA_PER_SQRT_HOUR_F) -> float:
    """Uncertainty in the REMAINING-day extreme, in degrees F.

    Monotone non-decreasing in the hours remaining, and floored strictly above
    zero: a zero sigma would say a forecast is exact, and would make every rung
    price 0.00 or 1.00 with a full-size position behind it.
    """
    return float(floor_f) + float(per_sqrt_hour_f) * math.sqrt(
        max(0.0, float(hours_to_window_close)))


# ---------------------------------------------------------------------------
# Reading the fitted sigma
# ---------------------------------------------------------------------------
#
# Cached on (path, mtime, size), NOT on path alone. A cache keyed on the path
# would pin whatever the first caller read for the life of the process, and this
# strategy runs inside a loop that stays up for days while the harness is
# re-run underneath it. Keying on the file's own identity means a refreshed
# artifact lands on the next call and a re-read costs one `stat`.
_SIGMA_CALIBRATION_CACHE: Dict[str, Tuple[Tuple[float, int], Optional[dict]]] = {}
_SIGMA_CALIBRATION_LOCK = threading.Lock()


def clear_sigma_calibration_cache() -> None:
    """Drop every cached artifact. Tests need it; the cache outlives instances."""
    with _SIGMA_CALIBRATION_LOCK:
        _SIGMA_CALIBRATION_CACHE.clear()


def load_sigma_calibration(path: Optional[str] = None) -> Optional[dict]:
    """Read the fitted-sigma artifact, or None.

    None means "there is no calibration here", which the caller turns into
    `daily_extreme_sigma_unfitted_for_station` - a convention 11 cannot-run.
    NEVER raises and never returns a partial dict: a truncated or malformed
    artifact reads as absent, because half a calibration priced as a whole one
    is worse than none (convention 28).

    A payload whose `schema_version` we do not recognise is ALSO absent. The
    alternative is reading a future artifact's fields by name and getting
    whatever happens to be there.
    """
    target = SIGMA_CALIBRATION_PATH if path is None else str(path)
    try:
        stat = os.stat(target)
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        with _SIGMA_CALIBRATION_LOCK:
            _SIGMA_CALIBRATION_CACHE.pop(target, None)
        return None
    with _SIGMA_CALIBRATION_LOCK:
        hit = _SIGMA_CALIBRATION_CACHE.get(target)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    payload: Optional[dict] = None
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            raw = json.load(handle)
        if isinstance(raw, dict) and isinstance(raw.get('stations'), dict):
            version = raw.get('schema_version')
            if isinstance(version, int) and version >= 1:
                payload = raw
    except (OSError, ValueError):
        payload = None
    with _SIGMA_CALIBRATION_LOCK:
        _SIGMA_CALIBRATION_CACHE[target] = (stamp, payload)
    return payload


def fitted_daily_extreme_sigma(calibration: Optional[dict], station: str,
                               metric: str, hours_to_window_close: float
                               ) -> Tuple[Optional[float], Dict[str, object]]:
    """The MEASURED forecast-error sigma for one station, or `(None, why)`.

    Returns `(sigma_f, features)`. `features` always carries `sigma_fit_status`,
    and every failure has its own string there: a missing artifact, a station
    absent from it, a metric that could not be fitted and a curve that came back
    non-positive are four different facts with four different fixes.

    ## THE MEASURED BUCKET WINS OVER THE FITTED CURVE, AND THAT IS DELIBERATE

    The artifact carries two things per station: per-lead residual statistics,
    and a `floor + b*sqrt(h)` curve fitted through them. Where the horizon falls
    INSIDE a lead bucket that has enough samples, this returns the bucket's own
    measured spread rather than the curve's value at that hour. The curve is a
    compromise across 36 to 108 hours and reads about 13% high at 31.5 hours
    against the lead-1 measurement it was partly fitted on; the bucket is the
    measurement.

    The curve is used only OUTSIDE the measured buckets, and a row that used it
    is stamped `sigma_horizon_is_extrapolated=True`. Below 24 hours it is a true
    extrapolation and cannot be anything else: open-meteo's archive resolves
    model runs to whole days, so there is no such thing as "the run from six
    hours ago" to verify against. That gap is real and it is the one place this
    strategy could still be pricing with a number nobody measured, which is why
    it is a flag on the row rather than a footnote.
    """
    feats: Dict[str, object] = {
        'daily_extreme_calibration_harness': CALIBRATION_HARNESS,
        'sigma_calibration_present': isinstance(calibration, dict),
    }
    if not isinstance(calibration, dict):
        feats['sigma_fit_status'] = 'calibration_artifact_missing'
        return None, feats
    feats['sigma_calibration_generated_utc'] = calibration.get('generated_utc')
    stations = calibration.get('stations')
    entry = stations.get(str(station)) if isinstance(stations, dict) else None
    if not isinstance(entry, dict):
        feats['sigma_fit_status'] = 'station_not_in_calibration'
        return None, feats
    cell = entry.get('metrics')
    cell = cell.get(metric) if isinstance(cell, dict) else None
    if not isinstance(cell, dict) or not cell.get('fit_ok'):
        feats['sigma_fit_status'] = 'metric_not_fitted_for_this_station'
        return None, feats

    basis = str(cell.get('sigma_basis') or 'rmse')
    min_samples = int(_safe_float(cell.get('min_samples')) or 0)
    hours = max(0.0, float(hours_to_window_close))
    lead = int(hours // SIGMA_LEAD_BUCKET_HOURS)
    feats.update({'daily_extreme_sigma_basis': basis,
                  'daily_extreme_sigma_lead_days': lead,
                  'daily_extreme_sigma_min_samples': min_samples})

    buckets = cell.get('station_verified_by_lead')
    bucket = buckets.get(str(lead)) if isinstance(buckets, dict) else None
    if isinstance(bucket, dict):
        count = int(_safe_float(bucket.get('n')) or 0)
        value = _safe_float(bucket.get(basis + '_f'))
        feats['daily_extreme_sigma_n'] = count
        if count >= min_samples and value is not None and value > 0:
            feats.update({
                'sigma_fit_status': 'ok',
                'daily_extreme_sigma_source': 'fitted_lead_bucket',
                'daily_extreme_sigma_residual_mean_f': bucket.get('mean_f'),
                'sigma_horizon_is_extrapolated': False,
            })
            return value, feats

    floor = _safe_float(cell.get('sigma_floor_f'))
    slope = _safe_float(cell.get('sigma_per_sqrt_hour_f'))
    if floor is None or slope is None:
        feats['sigma_fit_status'] = 'fitted_curve_unreadable'
        return None, feats
    value = daily_extreme_sigma_f(hours, floor, slope)
    if not (value > 0):
        # A non-positive sigma prices every rung 0.00 or 1.00 with a full-size
        # position behind it. Refused, never floored into something usable.
        feats['sigma_fit_status'] = 'fitted_sigma_not_positive'
        return None, feats
    feats.update({
        'sigma_fit_status': 'ok',
        'daily_extreme_sigma_source': 'fitted_curve_outside_measured_bucket',
        'daily_extreme_sigma_floor_f': floor,
        'daily_extreme_sigma_per_sqrt_hour_f': slope,
        'sigma_horizon_is_extrapolated': True,
    })
    return value, feats


def looks_like_a_temperature_market(market) -> bool:
    """Discovery shortlist. Deliberately WIDER than `parse_threshold`.

    A real Polymarket question reads "Will NYC exceed 85F on August 18?" and
    contains none of `TEMPERATURE_KEYWORDS`, so a keyword-only filter drops the
    exact markets this strategy exists for. Three independent signals, any of
    which shortlists:

      1. a `TEMPERATURE_KEYWORDS` word in the question
      2. the slug or question maps onto a `WEATHER_MARKETS` city
      3. the question carries a number with an explicit temperature unit

    The ONE hard exclusion is the global temperature family, which carries the
    word "temperature" and would sail through signal 1. It is not a city
    airport market at any stage, so excluding it here is not a shortcut past a
    later gate.

    This is a SHORTLIST, not a resolution decision. Everything shortlisted still
    has to survive `resolution_station_checked` and `parse_threshold` before a
    single share is priced, so a false positive here costs a skip row, never a
    trade.
    """
    raw_question = str(getattr(market, 'question', '') or '')
    if is_global_temperature_market(raw_question):
        return False
    question = raw_question.lower()
    if any(k in question for k in TEMPERATURE_KEYWORDS):
        return True
    if city_for_market(market) is not None:
        return True
    return _TEMP_UNIT_MARKER_RE.search(question) is not None


#: Every reason `find_weather_markets` can drop a raw Gamma market. One string
#: per cause and the accounting identity is asserted, so a market that vanishes
#: has a name attached rather than being a silent `continue` (convention 20).
DISCOVERY_DROP_REASONS = (
    'event_not_a_dict',
    'market_not_a_dict',
    'market_unbuildable',
    'closed',
    'inactive',
    'not_accepting_orders',
    'end_date_missing',
    'end_date_past',
    'not_a_temperature_market',
    'duplicate_across_tags',
    'over_limit',
)


def _gamma_events_page(client, tag_slug: str, offset: int,
                       limit: int) -> Tuple[Optional[list], str]:
    """One page of `/events` for a tag. `(events, 'ok')` or `(None, reason)`.

    Goes through `client.gamma` and nothing else. A raw socket or a bare
    `requests.get` here would bypass the client's timeout, retry and header
    handling, and would be the second place in the repo that knows the Gamma
    host.

    Gamma returns a bare list on this route and a `{'events': [...]}` wrapper
    on others. Both are accepted because the client is not the only thing that
    ever changes shape, and a shape we do not recognise is `unexpected_shape`
    rather than an empty page - an empty page would end pagination early and
    silently truncate the universe.
    """
    payload = client.gamma(GAMMA_EVENTS_PATH, {
        'tag_slug': tag_slug,
        'closed': 'false',
        'limit': limit,
        'offset': offset,
    })
    if payload is None:
        return None, 'read_failed'
    if isinstance(payload, list):
        return payload, 'ok'
    if isinstance(payload, dict):
        events = payload.get('events')
        if events is None:
            events = payload.get('data')
        if isinstance(events, list):
            return events, 'ok'
    return None, 'unexpected_shape'


def find_weather_markets(client, limit: Optional[int] = None,
                         now: Optional[float] = None) -> Dict[str, object]:
    """Find live temperature markets on Gamma, via the TAG route.

    Returns `{'ok', 'markets', 'by_city', 'raw_count', 'searched', 'reason'}`
    plus `drops`, `pages`, `truncated` and `pagination_capped` for accounting.

    Three outcomes that must never be pooled (convention 11):

        ok=False, reason='read_failed'        Gamma was unreachable. NOT a
                                              result, and NOT an empty market
                                              list.
        ok=True,  reason='no_weather_market'  Gamma answered and nothing
                                              matched. A cannot-run, still not
                                              a result.
        ok=True,  reason=None                 markets found.

    A run that found nothing because the API was down and a run that found
    nothing because Polymarket listed no temperature markets that day are the
    same empty list and completely different facts.

    WHY THE TAG ROUTE AND NOT `/public-search`. Measured 2026-08-18, the search
    route returned 33 markets and the tag route returned 2,045. The search route
    did not fail; it answered, and its answer was 1.6% of the universe. That is
    the worst possible failure mode because it looks exactly like a quiet day.

    STALE MARKETS ARE DROPPED HERE, not left for `evaluate`. A market whose
    `endDate` has passed cannot be entered, and letting it through would spend
    a METAR fetch and emit a `market_past_resolution_time` row for every rung of
    every yesterday. A market with NO `endDate` is dropped too, under its own
    reason: we cannot tell a fresh one from a stale one, and that is not the
    same fact as knowing it is stale.
    """
    now = time.time() if now is None else float(now)
    seen: Dict[str, object] = {}
    drops: Dict[str, int] = {}
    unbuildable: Dict[str, int] = {}
    raw_count = 0
    pages = 0
    any_ok = False
    read_failures: Dict[str, int] = {}
    pagination_capped = False

    def drop(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1

    for tag_slug in WEATHER_TAG_SLUGS:
        offset = 0
        for _page in range(GAMMA_MAX_PAGES):
            events, status = _gamma_events_page(client, tag_slug, offset,
                                                GAMMA_PAGE_LIMIT)
            if events is None:
                read_failures[status] = read_failures.get(status, 0) + 1
                break
            any_ok = True
            pages += 1
            for event in events:
                if not isinstance(event, dict):
                    drop('event_not_a_dict')
                    raw_count += 1
                    continue
                for raw in event.get('markets') or ():
                    raw_count += 1
                    if not isinstance(raw, dict):
                        drop('market_not_a_dict')
                        continue
                    keep, reason, payload = _discovery_keep(raw, now)
                    if not keep:
                        drop(reason)
                        if reason == 'market_unbuildable':
                            # `payload` is the sub-reason from
                            # `market_from_gamma_checked`. Counted separately
                            # so "44 unbuildable" never hides four different
                            # malformed-payload bugs behind one number.
                            key = str(payload)
                            unbuildable[key] = unbuildable.get(key, 0) + 1
                        continue
                    market = payload
                    key = market.slug or market.id
                    if key in seen:
                        drop('duplicate_across_tags')
                        continue
                    if limit and len(seen) >= limit:
                        drop('over_limit')
                        continue
                    seen[key] = market
            if len(events) < GAMMA_PAGE_LIMIT:
                break
            offset += GAMMA_PAGE_LIMIT
        else:
            pagination_capped = True

    if not any_ok:
        return {'ok': False, 'markets': [], 'by_city': {}, 'raw_count': 0,
                'searched': list(WEATHER_TAG_SLUGS), 'drops': {},
                'unbuildable': {}, 'pages': 0, 'truncated': False,
                'pagination_capped': False, 'read_failures': read_failures,
                'reason': 'read_failed'}

    markets = list(seen.values())
    by_city: Dict[str, list] = {}
    for market in markets:
        key = (city_name_from_question(market) or city_for_market(market)
               or 'unmapped')
        by_city.setdefault(key, []).append(market)

    # Convention 20: every raw market is either kept or counted under exactly
    # one named cause. An assert here is cheap and it is the only thing that
    # would catch a `continue` added later without a counter next to it.
    accounted = len(markets) + sum(drops.values())
    assert accounted == raw_count, (accounted, raw_count, drops)

    return {'ok': True, 'markets': markets, 'by_city': by_city,
            'raw_count': raw_count, 'searched': list(WEATHER_TAG_SLUGS),
            'drops': drops, 'unbuildable': unbuildable, 'pages': pages,
            'truncated': bool(drops.get('over_limit')),
            'pagination_capped': pagination_capped,
            'read_failures': read_failures,
            'reason': None if markets else 'no_weather_market'}


#: Every reason `rank_weather_markets` can decline to POLL a discovered market.
#: Declining to poll is not the same as skipping it: a market here never reaches
#: `evaluate` and never produces a decision row, so it needs its own vocabulary
#: rather than borrowing SKIP_REASONS strings that would then appear in the log
#: without a decision behind them (convention 20).
#: Every string is `poll_`-prefixed so that no ranking decline can ever be read
#: as a decision skip. `threshold_unparseable` exists in BOTH vocabularies and
#: means the same thing about a market, but one of them happened before a book
#: was ever fetched and the other happened inside `evaluate`. Sharing the string
#: would make a grep across the logs return two different populations.
POLL_DECLINE_REASONS = (
    'poll_station_unreadable',
    'poll_threshold_unparseable',
    'poll_not_a_daily_extreme_market',
    'poll_below_volume_floor',
    'poll_outside_poll_budget',
)


def rank_weather_markets(markets, limit: int,
                         min_volume_usdc: float = 0.0
                         ) -> Dict[str, object]:
    """Pick which discovered markets are worth spending an orderbook read on.

    Returns `{'selected', 'declined', 'volume_ordered', 'considered'}` where
    `declined` is a count PER CAUSE and the identity
    `len(selected) + sum(declined.values()) == considered` holds.

    ## WHY THIS EXISTS RATHER THAN "TAKE THE TOP N BY VOLUME"

    Measured live 2026-08-18: `find_weather_markets` returned 1,090 markets and
    the six highest-volume were "Will 2026 be the hottest year on record?" and
    its five siblings, at $393k to $820k. Those are ANNUAL RANKING markets. They
    carry the word "hottest", so the discovery shortlist admits them, and they
    have no station and no threshold, so every one of them would have cost two
    orderbook reads and produced a skip row. A plain volume sort would have
    spent an entire poll budget on markets that structurally cannot trade, at
    100x the volume of the biggest genuine city ladder ($9,330, Paris).

    So the filter runs FIRST and the volume sort runs on what survives. Every
    filter here is a pure string operation on data already in hand - no network
    - so applying it to 1,090 markets costs nothing and saves 2,000 book reads.

    ## THE ORDER IS EXACT, NOT GAMMA'S

    Sorted locally on `market.volume`, descending, with `None` treated as the
    bottom rather than as zero. This deliberately does NOT ask Gamma to sort:
    `order=volume` sorts that column as TEXT and returns the SMALLEST markets
    while still returning HTTP 200 (see `markets.VOLUME_ORDER_FIELD`), and the
    `/events` tag route this discovery uses takes no order parameter at all.
    A local sort on a list we already hold cannot be wrong in that way.
    """
    considered = 0
    declined: Dict[str, int] = {}
    keep: List[Tuple[float, object]] = []

    def decline(reason: str) -> None:
        declined[reason] = declined.get(reason, 0) + 1

    for market in markets or ():
        considered += 1
        # Cheapest and most selective first, so the counters attribute a market
        # to the FIRST thing wrong with it rather than to whichever gate ran.
        if market_metric(getattr(market, 'question', None)) is None:
            # The annual-ranking family and any point-in-time market land here.
            decline('poll_not_a_daily_extreme_market')
            continue
        station, _status = resolution_station_checked(market)
        if station is None:
            decline('poll_station_unreadable')
            continue
        if parse_threshold(getattr(market, 'question', None)) is None:
            decline('poll_threshold_unparseable')
            continue
        volume = _safe_float(getattr(market, 'volume', None))
        if volume is None or volume < float(min_volume_usdc):
            # `None` is UNREADABLE, not zero, and it lands here alongside a
            # genuinely small market because the poll budget cares only about
            # "not worth a read". The two stay distinguishable on the market
            # object itself, which the caller still holds.
            decline('poll_below_volume_floor')
            continue
        keep.append((volume, market))

    keep.sort(key=lambda pair: -pair[0])
    cap = max(0, int(limit))
    selected = [market for _volume, market in keep[:cap]]
    over = len(keep) - len(selected)
    if over > 0:
        declined['poll_outside_poll_budget'] = declined.get(
            'poll_outside_poll_budget', 0) + over

    total = len(selected) + sum(declined.values())
    if total != considered:                  # pragma: no cover - guard
        raise AssertionError(
            'weather poll ranking does not balance: {} selected + {} declined '
            '!= {} considered'.format(len(selected), sum(declined.values()),
                                      considered))

    return {'selected': selected, 'declined': declined,
            'considered': considered,
            'volume_ordered': [_safe_float(getattr(m, 'volume', None))
                               for m in selected]}


def _discovery_keep(raw: dict, now: float) -> Tuple[bool, str, object]:
    """One raw Gamma market -> `(keep, reason, market_or_unbuildable_reason)`.

    Split out of `find_weather_markets` so the filter order is readable in one
    screen. Order matters for ATTRIBUTION, not for the outcome: a closed market
    that is also stale is counted once, as closed.
    """
    from engine.polymarket.markets import market_from_gamma_checked

    if raw.get('closed'):
        return False, 'closed', None
    if not raw.get('active', True):
        return False, 'inactive', None
    if raw.get('acceptingOrders') is False:
        return False, 'not_accepting_orders', None

    # `endDate` is a SETTLEMENT ADMIN timestamp, NOT the close of the
    # observation window. See `WeatherArb.hours_to_resolution` for the
    # measurement (Madrid resolves at 12:00Z = 14:00 local, mid-afternoon) and
    # for why it is documented rather than fixed.
    #
    # Used here only as a LIVENESS filter, which is the one job it is actually
    # fit for: a market whose settlement stamp has passed is over, whatever the
    # observation window was. Do not read the value as a horizon.
    end_ts = _parse_iso_seconds(raw.get('endDate'))
    if end_ts is None:
        return False, 'end_date_missing', None
    if end_ts <= now:
        return False, 'end_date_past', None

    market, build_reason = market_from_gamma_checked(raw)
    if market is None:
        return False, 'market_unbuildable', build_reason
    if not looks_like_a_temperature_market(market):
        return False, 'not_a_temperature_market', None
    return True, 'ok', market


# ---------------------------------------------------------------------------
# The strategy
# ---------------------------------------------------------------------------

class WeatherArb(PolymarketStrategy):
    """Price a temperature binary off the AIRPORT station the rules name.

    Enters only when all of these hold:

      1. the question is not the global temperature family
      2. the market's own rules text names one station we can read and fetch
      3. the question carries a parseable threshold WITH AN EXPLICIT UNIT
      4. a ladder rung's source reports whole degrees, so its edges are exact
      5. the market is not a daily extreme, which this model cannot price
      6. the METAR observation is under an hour old
      7. our probability and the market's implied direction DISAGREE
      8. the model's probability for the side it would BUY exceeds
         `min_model_p_side` (0.55) - a conviction test, not a price test
      9. the walked entry price still leaves at least MIN_EDGE

    And, when `use_fitted_sigma` is on, the station has a MEASURED
    forecast-error sigma in `research/weather_sigma_calibration.json`. A station
    with no fit is refused rather than priced with a house number
    (`daily_extreme_sigma_unfitted_for_station`, convention 11).

    ON LIVE DATA, GATE 5 REFUSES EVERYTHING. Every Polymarket temperature
    market measured on 2026-08-18 resolves on a daily high or a daily low, so
    with `allow_daily_extreme_markets=False` this strategy books nothing. The
    parser fix that got the other seven gates working is real and measurable
    (1,727 of 1,739 questions parse, 1,661 resolve to a station, 1,203 reach
    the orderbook); gate 5 is the honest verdict on what to do with them.

    Holds to resolution. The stop is 0.00, which on a binary is the exact floor
    and satisfies convention 8.
    """

    strategy_name = 'PM_weather_arb'
    paper_mode = PAPER_MODE

    #: Holds to resolution. There is no exit model here; see the docstring.
    manages_exits = False

    #: CAPABILITY FLAG, read by the shadow loop, in the same shape as
    #: `manages_exits` and `needs_strike`. It says "hand me a temperature
    #: market, not a crypto window". The loop dispatches its weather cycle on
    #: this rather than on the strategy's NAME: a name check is a list somebody
    #: has to remember to update, and a second weather strategy added later
    #: would silently never be polled.
    needs_weather_market = True

    #: WEATHER ONLY. Nothing in this file can price anything else: the whole
    #: model is a temperature distribution around a station reading, and the
    #: only market fields it reads are the question text, the rules text and the
    #: books.
    #:
    #: READ THIS BEFORE CONCLUDING THE CRYPTO NUMBERS CHANGED. Until D-312 this
    #: class ALSO ran inside the crypto cycle, on all three of SHADOW_ASSETS,
    #: every poll, where it returned `not_a_temperature_market` every single
    #: time. That was DELIBERATE and it was LOAD-BEARING, not an oversight:
    #: `shadow_loop.py` (the comment at its lines 1140-1145) kept this strategy
    #: in the crypto list so the per-cycle identity stayed at 19 x 3 = 57
    #: evaluations, and `not_a_temperature_market` is the named counter that
    #: made those 3 refusals per cycle readable rather than a silent hole
    #: (convention 20).
    #:
    #: Declaring WEATHER only moves that job to the ROUTER. The loop no longer
    #: hands this class a crypto context at all, so the crypto denominator drops
    #: from 19 x 3 to 18 x 3 = 54 and the `not_a_temperature_market` rows stop
    #: appearing on the crypto path. That is a change in the DENOMINATOR, not in
    #: any strategy's behaviour, and any comparison of a per-cycle count across
    #: that boundary is comparing two different denominators. The reason string
    #: itself stays in `SKIP_REASONS`: it is still the honest answer for a
    #: crypto market reached by any other path, and deleting it would lose the
    #: distinction it was created to draw.
    supported_market_types = (MARKET_TYPE_WEATHER,)

    def __init__(self, airport_feed=None, downtown_feed=None,
                 forecast_feed=None,
                 min_edge: float = MIN_EDGE,
                 max_obs_age_sec: float = MAX_OBS_AGE_SEC,
                 sigma_floor_f: float = SIGMA_FLOOR_F,
                 sigma_per_sqrt_hour_f: float = SIGMA_PER_SQRT_HOUR_F,
                 max_hours_to_resolution: float = MAX_HOURS_TO_RESOLUTION,
                 target_shares: int = TARGET_SHARES,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 fetch_downtown: bool = True,
                 allow_station_fallback: bool = False,
                 allow_daily_extreme_markets: Optional[bool] = None,
                 require_observed_extreme: Optional[bool] = None,
                 daily_extreme_sigma_floor_f: float =
                 DAILY_EXTREME_SIGMA_FLOOR_F,
                 daily_extreme_sigma_per_sqrt_hour_f: float =
                 DAILY_EXTREME_SIGMA_PER_SQRT_HOUR_F,
                 max_hours_to_window_close: float = MAX_HOURS_TO_WINDOW_CLOSE,
                 use_fitted_sigma: Optional[bool] = None,
                 sigma_calibration: Optional[dict] = None,
                 min_model_p_side: float = MIN_MODEL_P_SIDE):
        #: All three feeds are built LAZILY. Constructing an AirportWeatherFeed
        #: imports `requests`; a test that injects fakes, or a caller that only
        #: wants `parse_threshold`, should not need a network stack.
        self._airport_feed = airport_feed
        self._downtown_feed = downtown_feed
        self._forecast_feed = forecast_feed
        self.min_edge = min_edge
        self.max_obs_age_sec = max_obs_age_sec
        self.sigma_floor_f = sigma_floor_f
        self.sigma_per_sqrt_hour_f = sigma_per_sqrt_hour_f
        self.max_hours_to_resolution = max_hours_to_resolution
        self.target_shares = target_shares
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        #: The downtown reading is diagnostic only and costs a second HTTP round
        #: trip. Turning it off loses the gap measurement, which is the only
        #: thing that would ever turn the 3-8F claim into a number, so the
        #: default is on and every row records which way it was set.
        self.fetch_downtown = bool(fetch_downtown)
        #: OFF by default, and that default is the whole point. Turning it on
        #: lets `CITY_STATION_FALLBACK` supply a station for a market whose
        #: rules text names none, which is the one thing this file refuses to
        #: do everywhere else. Measured, it buys exactly one city (Hong Kong,
        #: 66 markets, 3.7%) and it buys it with an airport 25km from the
        #: station the market actually resolves on. A row that used it is
        #: stamped `station_is_a_fallback_guess=True` so the population can be
        #: scored separately, which is the only condition under which turning
        #: it on is defensible.
        self.allow_station_fallback = bool(allow_station_fallback)
        #: `None` means "take whatever `config.yaml` set", which is how the live
        #: loop turns this on without a code edit (convention 17). An explicit
        #: True or False from a caller always wins, which is what every existing
        #: test relies on.
        #:
        #: WHAT IT NOW MEANS HAS CHANGED. It used to mean "price a daily-extreme
        #: market with the point-in-time model", and the rows that produced were
        #: model error with a price attached. It now means "price it with the
        #: DAILY EXTREME model": a forecast-anchored normal floored by the
        #: station's own running extreme. See `DailyExtremeEstimate` for the
        #: distributional assumption and for what would falsify it.
        self.allow_daily_extreme_markets = bool(
            weather_config()['allow_daily_extreme_markets']
            if allow_daily_extreme_markets is None
            else allow_daily_extreme_markets)
        #: True refuses a daily-extreme market whose station history could not
        #: be read. The running observed extreme is a HARD FLOOR on the
        #: resolution value, not a nice-to-have, so pricing without it is
        #: exactly the failure the Madrid and Buenos Aires rows recorded.
        self.require_observed_extreme = bool(
            weather_config()['require_observed_extreme']
            if require_observed_extreme is None
            else require_observed_extreme)
        self.daily_extreme_sigma_floor_f = float(daily_extreme_sigma_floor_f)
        self.daily_extreme_sigma_per_sqrt_hour_f = float(
            daily_extreme_sigma_per_sqrt_hour_f)
        self.max_hours_to_window_close = float(max_hours_to_window_close)
        #: `None` means "take whatever `config.yaml` set", exactly as
        #: `allow_daily_extreme_markets` does. An explicit bool always wins.
        self.use_fitted_sigma = bool(
            weather_config()['use_fitted_sigma']
            if use_fitted_sigma is None else use_fitted_sigma)
        #: `None` = load `SIGMA_CALIBRATION_PATH` lazily on first use. Pass a
        #: dict to inject one, and pass `{}` to inject the ABSENCE of one - a
        #: test that wants the unfitted path must be able to say so without
        #: depending on whether the live artifact happens to be on disk.
        self._sigma_calibration = sigma_calibration
        self.min_model_p_side = float(min_model_p_side)

    @property
    def sigma_calibration(self) -> Optional[dict]:
        """The fitted-sigma artifact, loaded once, refreshed when it changes.

        Lazy for the same reason the three feeds are lazy: constructing a
        strategy must not touch the disk, and a caller that only wants
        `parse_threshold` should not need the artifact to exist.
        """
        if self._sigma_calibration is None:
            self._sigma_calibration = load_sigma_calibration()
        return self._sigma_calibration

    # -- feeds --------------------------------------------------------------

    @property
    def airport_feed(self) -> AirportWeatherFeed:
        if self._airport_feed is None:
            self._airport_feed = AirportWeatherFeed()
        return self._airport_feed

    @property
    def downtown_feed(self) -> DowntownWeatherFeed:
        if self._downtown_feed is None:
            self._downtown_feed = DowntownWeatherFeed()
        return self._downtown_feed

    @property
    def forecast_feed(self) -> StationForecastFeed:
        if self._forecast_feed is None:
            self._forecast_feed = StationForecastFeed()
        return self._forecast_feed

    # -- the daily extreme --------------------------------------------------

    def daily_extreme_estimate(self, market, station: str, reading: Reading,
                               metric: str, now: float
                               ) -> Tuple[Optional[DailyExtremeEstimate],
                                          str, dict]:
        """Assemble the predictive distribution of the day's extreme.

        Returns `(estimate, status, features)`. `status` is `'ok'` or one of the
        daily-extreme SKIP_REASONS, and `features` carries everything read on
        the way there so a REFUSAL row is as reconstructible as an entry row.

        Every failure has its own string. A forecast outage, an unparseable
        date, a date outside the forecast window and a station with nothing
        reported yet today are four different facts, and one shared "cannot
        price it" counter would hide whichever is smallest (convention 20).

        FETCHES. Two network reads live in here, both on injected feeds, both
        with their own timeouts, both fail-closed. That is the same compromise
        the module docstring already records for the METAR read, for the same
        reason: `MarketContext` carries no weather fields.
        """
        feats: Dict[str, object] = {}

        # 1. WHERE IS THE STATION. Straight off the METAR payload rather than
        # from a coordinate table: the station tells us itself, and a table
        # would be one more assumption to keep in sync with 51 cities.
        if reading.lat is None or reading.lon is None:
            return None, 'station_coordinates_unknown', feats
        feats['station_lat'] = reading.lat
        feats['station_lon'] = reading.lon

        # 2. THE DIURNAL CYCLE, at that station, in that station's calendar.
        forecast, forecast_status = self.forecast_feed.forecast_checked(
            reading.lat, reading.lon)
        feats['forecast_feed_status'] = forecast_status
        if forecast is None:
            return None, 'station_forecast_unavailable', feats
        feats.update(forecast.to_dict())

        # 3. WHICH DAY. From the question, with the YEAR resolved against the
        # forecast's own local dates. `endDate` is not consulted here at all -
        # it is a settlement admin stamp that Madrid puts in the middle of the
        # very afternoon the market is about.
        local_date, date_status = resolution_local_date_checked(
            getattr(market, 'question', None), forecast)
        feats['resolution_date_status'] = date_status
        if local_date is None:
            return None, date_status, feats
        feats['observation_local_date'] = local_date

        bounds = forecast.local_day_bounds(local_date)
        if bounds is None:                   # pragma: no cover - date is ISO
            return None, 'resolution_date_unparseable', feats
        window_start, window_end = bounds
        hours_to_close = (float(window_end) - float(now)) / SECONDS_PER_HOUR
        feats.update({'observation_window_start_ts': window_start,
                      'observation_window_end_ts': window_end,
                      'hours_to_window_close': round(hours_to_close, 3),
                      # Past a day the sigma constants are extrapolated rather
                      # than applied. Stamped, never gated, so the two
                      # populations can be scored apart instead of pooled.
                      'horizon_beyond_same_day': (
                          hours_to_close > SAME_DAY_HORIZON_HOURS),
                      'same_day_horizon_hours': SAME_DAY_HORIZON_HOURS})
        if hours_to_close <= 0:
            # The day is over. Not the same as `market_past_resolution_time`,
            # which is about the settlement stamp: this one is about the
            # OBSERVATION window, and the two are hours apart by construction.
            return None, 'observation_window_closed', feats
        if hours_to_close > self.max_hours_to_window_close:
            feats['max_hours_to_window_close'] = self.max_hours_to_window_close
            return None, 'observation_window_too_far_out', feats

        # 4. THE FORECAST EXTREME for that day and that metric.
        forecast_extreme = forecast.extreme_for(local_date, metric)
        if forecast_extreme is None:
            return None, 'forecast_extreme_missing_for_date', feats
        feats['forecast_extreme_f'] = round(float(forecast_extreme), 3)

        # 5. THE STATION BIAS. The whole thesis of this strategy is that the
        # station and the grid cell are different thermometers, so the forecast
        # is re-centred on the station rather than used raw. Without this the
        # model would be pricing open-meteo's grid cell, which is the CONSUMER
        # anchor - the exact number the strategy claims retail is wrong to use.
        grid_now = forecast.hourly_at(reading.observed_ts)
        if grid_now is None:
            return None, 'forecast_hour_missing_for_bias', feats
        bias = float(reading.temp_f) - float(grid_now)
        feats.update({'forecast_grid_temp_at_obs_f': round(float(grid_now), 3),
                      'station_minus_grid_bias_f': round(bias, 3)})

        # 6. THE HARD FLOOR: what the station has already reported today.
        # Asked for by the station the RULES named, which is the same key the
        # single-observation read used. Asking by the ICAO the response echoed
        # back would be a second cache key for one station.
        #
        # TWO DIFFERENT ABSENCES, TWO DIFFERENT ANSWERS. This is the place
        # convention 20 earns its keep, because the safe response to the two is
        # OPPOSITE:
        #
        #   The window has not OPENED yet. Most of the tradable board is
        #   next-day ladders - measured 2026-08-18, every one of the fourteen
        #   highest-volume city markets was for the next local day - and no part
        #   of that day has happened anywhere. There IS no running extreme. The
        #   floor genuinely does not exist, and refusing here refuses the entire
        #   next-day board forever, which is exactly what the first live run of
        #   this model did before the split existed.
        #
        #   The window is OPEN and we could not read it. Now the floor exists
        #   and we are blind to it. Pricing anyway is the documented Madrid
        #   failure: a station already sitting at 33.0C while the model prices
        #   the day's high off a forecast alone.
        #
        # One shared "no observed extreme" counter cannot tell those apart.
        window_open = float(now) >= float(window_start)
        feats['observation_window_open'] = window_open
        observed = None
        observed_status = 'observation_window_not_open_yet'
        if window_open:
            observed, observed_status = self.airport_feed.daily_extreme_checked(
                station, metric, window_start, window_end)
        feats['observed_extreme_status'] = observed_status
        if observed is None:
            feats['require_observed_extreme'] = self.require_observed_extreme
            if window_open and self.require_observed_extreme:
                # Convention 11: a cannot-run. We are not saying the day has no
                # extreme; we are saying we could not read the one bound on it
                # that is not a model output.
                return None, 'daily_extreme_history_unavailable', feats
        else:
            feats.update(observed.to_dict())

        # 7. THE SIGMA. Measured, or a house number, and the row says which.
        #
        # Convention 11 is the whole design of this branch. With
        # `use_fitted_sigma` on there is NO silent fallback: a station we have
        # never measured is refused, because "we do not know how wrong we are
        # here" is a cannot-run and not a probability. With it off the house
        # constants are used exactly as before and the row says so, so the two
        # populations can never be pooled by a later reader.
        feats['use_fitted_sigma'] = self.use_fitted_sigma
        sigma_source = 'house_constants_unfitted'
        sigma_n: Optional[int] = None
        sigma_extrapolated = False
        if self.use_fitted_sigma:
            sigma, sigma_feats = fitted_daily_extreme_sigma(
                self.sigma_calibration, station, metric, hours_to_close)
            feats.update(sigma_feats)
            if sigma is None:
                return None, 'daily_extreme_sigma_unfitted_for_station', feats
            sigma_source = str(sigma_feats.get('daily_extreme_sigma_source'))
            raw_n = sigma_feats.get('daily_extreme_sigma_n')
            sigma_n = None if raw_n is None else int(raw_n)
            sigma_extrapolated = bool(
                sigma_feats.get('sigma_horizon_is_extrapolated'))
        else:
            sigma = daily_extreme_sigma_f(
                hours_to_close, self.daily_extreme_sigma_floor_f,
                self.daily_extreme_sigma_per_sqrt_hour_f)
            feats['sigma_fit_status'] = 'fitted_sigma_not_requested'
        estimate = DailyExtremeEstimate(
            metric=metric,
            mu_f=float(forecast_extreme) + bias,
            sigma_f=sigma,
            forecast_extreme_f=float(forecast_extreme),
            bias_f=bias,
            observed_extreme_f=(None if observed is None
                                else observed.extreme_f),
            observations_used=0 if observed is None else observed.observations,
            hours_to_window_close=hours_to_close,
            local_date=local_date,
            window_start_ts=window_start,
            window_end_ts=window_end,
            sigma_source=sigma_source,
            sigma_n=sigma_n,
            sigma_horizon_is_extrapolated=sigma_extrapolated)
        feats.update(estimate.to_dict())
        return estimate, 'ok', feats

    # -- model --------------------------------------------------------------

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the context rather than the wall clock, so a decision is
        reproducible from a logged context and a test does not have to mock
        `time`. For a weather market the caller sets `window_ts` to the poll
        second; there is no 5-minute window here to floor to.
        """
        if not ctx.window_ts:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window or 0.0)

    def sigma_f(self, hours_remaining: float) -> float:
        """Standard deviation of the final reading, in degrees F.

        See the module docstring: a diffusion form on a variable with a
        deterministic daily cycle, wrong in a known direction at known times of
        day.
        """
        return self.sigma_floor_f + self.sigma_per_sqrt_hour_f * math.sqrt(
            max(0.0, float(hours_remaining)))

    def probability_yes(self, temp_f: float, threshold: Threshold,
                        hours_remaining: float) -> float:
        """P(the market's Yes side resolves true), from the airport reading.

        `CDF(hi) - CDF(lo)` with either edge allowed to be unbounded, which
        collapses to the old one-sided form on a tail: an `at_or_above` rung has
        `hi_f=None`, so the upper term is 1 and the result is `1 - CDF(lo)`,
        bit-for-bit what the previous two-branch version returned. The bucket
        case is the one that needs both terms, and it is the case that does not
        exist in any older log.

        Reads NOTHING but the interval. A caller that hands it a bucket gets a
        bucket price without having to know it did.
        """
        temp = float(temp_f)
        sigma = self.sigma_f(hours_remaining)
        lo, hi = threshold.lo_f, threshold.hi_f
        if sigma <= 0:                       # pragma: no cover - floor is > 0
            inside = ((lo is None or temp >= lo) and (hi is None or temp < hi))
            return 1.0 if inside else 0.0
        p_hi = 1.0 if hi is None else _NORMAL.cdf((hi - temp) / sigma)
        p_lo = 0.0 if lo is None else _NORMAL.cdf((lo - temp) / sigma)
        # Clamped because floating point can return a hair below zero on a
        # bucket far out in a tail, and a negative probability walks straight
        # into a negative edge and an entry.
        return max(0.0, min(1.0, p_hi - p_lo))

    @staticmethod
    def hours_to_resolution(market, now: float) -> Optional[float]:
        """Hours from `now` until `market.end_date`, or None if unreadable.

        ## KNOWN LIMITATION: `endDate` is a SETTLEMENT timestamp, not the close
        ## of the observation window. Documented, deliberately not fixed.

        Measured 2026-08-18 on live Gamma markets: Madrid's `endDate` is 12:00Z,
        which is 14:00 local. That is mid-afternoon. It is not the end of the
        station's calendar day, and on a "highest temperature today" market the
        afternoon peak has typically not even happened yet at that hour.

        So this returns hours-until-an-admin-timestamp, and the number the
        physics wants is hours-until-the-observation-window-closes, which for a
        daily-extreme market is local midnight at the station. The two differ by
        hours, in a direction that varies by city and by the market's own
        timezone, so there is no constant offset that repairs it.

        Two consumers are affected, and BOTH are inside the gate that is
        currently closed:

          - `max_hours_to_resolution` admits and rejects against the wrong
            horizon.
          - `sigma_f(hours)` is fed the wrong horizon, and it is the term under
            the square root, so an understated horizon understates the
            uncertainty and OVERSTATES every edge computed from it.

        This is moot today and only today: `allow_daily_extreme_markets`
        defaults to False, so every market that would exercise this is refused
        upstream under `daily_extreme_not_priced_by_point_in_time_model` and no
        entry is ever priced off this number. It is NOT moot for whoever builds
        the daily-extreme model. Fixing it needs the station's local timezone
        and the market's own resolution language parsed into a window close, not
        an offset applied to `endDate`.

        Left as-is ON PURPOSE. Changing the horizon now would silently move
        `resolution_too_far_out` and every `sigma_f` in the logged tape while the
        strategy is gated off, which would make the pre-model rows and the
        post-model rows non-comparable for no gain (convention 17).
        """
        end_ts = _parse_iso_seconds(getattr(market, 'end_date', None))
        if end_ts is None:
            return None
        return (float(end_ts) - float(now)) / SECONDS_PER_HOUR

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one temperature market. ALWAYS returns a Decision."""
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            # Stated on EVERY row, skips included. Nothing downstream may pick
            # the vendor claim up off a log as if it were a measurement.
            feats.setdefault('claimed_gap_is_unverified_vendor_number', True)
            feats.setdefault('gap_never_measured_by_us', True)
            feats.setdefault('claimed_gap_range_f', (3.0, 8.0))
            feats.setdefault('gap_sign_is_not_constant', True)
            feats.setdefault('readings_fetched_inside_evaluate', True)
            feats.setdefault('resolution_source_read_from_rules_text', True)
            feats.setdefault('holds_to_resolution', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        now = self.clock(ctx)
        if now is None:
            # Every gate below is timed: observation age and hours to resolution
            # both need a clock. Guessing one is guessing the freshness of the
            # only reading that matters.
            return decide('SKIP', 'no_clock')

        question = getattr(ctx.market, 'question', None)

        # 0. WRONG MARKET FAMILY. Checked FIRST, before the station, because a
        # global temperature market has no station either and would otherwise
        # be counted as `resolution_station_unknown` - pooling "we could not
        # read the station" with "this is not a city market at all", which are
        # two entirely different facts about two entirely different products.
        if is_global_temperature_market(question):
            return decide('SKIP', 'global_temperature_market_excluded',
                          question=question)

        # 0b. THE WRONG PRODUCT ENTIRELY. Checked before the station for the
        # same reason step 0 is: a BTC Up/Down 5m market has no station either,
        # and until this gate existed the shadow loop's 57-evaluation cycle
        # reported `resolution_station_unknown` for it three times a cycle -
        # a reason that reads as "a weather market whose rules we could not
        # parse" and was in fact "this is not a weather market". Convention 20.
        #
        # This shortlist is DELIBERATELY WIDE (see
        # `looks_like_a_temperature_market`); everything it admits still has to
        # survive the station gate and the threshold gate, so a false positive
        # here costs a skip row and never a trade.
        if not looks_like_a_temperature_market(ctx.market):
            return decide('SKIP', 'not_a_temperature_market', question=question)

        # 1. THE RULES TEXT DECIDES. Not the table, and not the slug.
        station, station_status = resolution_station_checked(
            ctx.market, allow_fallback=self.allow_station_fallback)
        city_key = city_for_market(ctx.market)
        assumed = (WEATHER_MARKETS.get(city_key) or {}).get('icao')
        from_fallback = station_status == 'ok_from_city_fallback_table'
        feats: Dict[str, object] = {
            'city_key': city_key,
            'city_name': city_name_from_question(ctx.market),
            'market_metric': market_metric(question),
            # Provisional, and overwritten at gate 6 by whichever model actually
            # ran. It is set here so that a row skipping BEFORE gate 6 still
            # says which model it would have used - a skip with no model stamp
            # cannot be grouped against the entries it belongs with.
            'model_prices_point_in_time_not_daily_extreme': not (
                market_metric(question) is not None
                and self.allow_daily_extreme_markets),
            'assumed_station_from_table': assumed,
            'rules_station': None if from_fallback else station,
            'station_source': ('city_fallback_table' if from_fallback
                               else 'rules_text'),
            'station_is_a_fallback_guess': from_fallback,
            'allow_station_fallback': self.allow_station_fallback,
            'rules_text_present': bool(rules_text(ctx.market).strip()),
            'station_assumption_matches_rules': (
                None if (station is None or assumed is None)
                else station == str(assumed).upper()),
        }
        if station is None:
            # Two different causes, two different strings. Never pooled.
            return decide('SKIP', station_status, **feats)

        # 2. THE THRESHOLD. No default threshold and no default UNIT, ever.
        feats['question'] = question
        threshold, parse_status = parse_threshold_checked(question or '')
        feats['threshold_parse_status'] = parse_status
        if threshold is None:
            # `threshold_parse_status` keeps the five parse failures apart in a
            # log while the action-level reason says the one thing the gate is
            # about. The global-family case never reaches here; it was refused
            # at step 0 under its own reason.
            return decide('SKIP', 'threshold_unparseable', **feats)
        feats.update(threshold.to_dict())

        # 2b. THE LADDER EDGES ARE ONLY CORRECT IF THE SOURCE ROUNDS TO WHOLE
        # DEGREES. See `reporting_step_checked`. Applies to ladder rungs only:
        # a legacy prefix comparison carries no rounding step to disagree with.
        if threshold.is_ladder_rung:
            step, step_status = reporting_step_checked(ctx.market)
            feats['source_reporting_step_native'] = step
            feats['source_reporting_status'] = step_status
            if step is None:
                return decide('SKIP', 'source_reporting_precision_unknown',
                              **feats)
            if step < 1.0:
                return decide('SKIP', 'source_precision_finer_than_ladder_step',
                              **feats)

        # 2c. THE MODEL PRICES THE WRONG VARIABLE ON A DAILY-EXTREME MARKET.
        # This gate was added after the parser fix, because the parser fix is
        # what made the problem visible. MEASURED live on 2026-08-18 over 80
        # markets with real books and real METAR: 7 ENTERs, realised "edge"
        # 0.45 to 0.999. An 86-cent edge on a market quoting 0.1c is not edge,
        # it is the model being wrong, and it is wrong for a reason we can name
        # rather than a reason we have to go looking for.
        #
        #   Madrid, 11:00 UTC, station reading 33.0C. Market prices "the
        #   HIGHEST temperature today is 39C" at 0.70, because the afternoon
        #   peak has not happened yet. This model prices the reading at the
        #   settlement timestamp and says 0.000024. The market is right.
        #
        #   Buenos Aires, same minute, station reading 7.0C. Market prices
        #   "highest today is 8C or below" at 0.001, because the afternoon will
        #   be warmer. This model says 0.87. The market is right again, and
        #   note the error points the OTHER WAY, so a pooled win rate over both
        #   ladders would average the two biases into something that looks
        #   unbiased.
        #
        # Convention 17: a number that improves dramatically right after a step
        # that only removed a constraint is a baseline problem, not a result.
        # This is that shape exactly, so the refusal is the default and the
        # override is a named flag whose rows are separable.
        metric = feats.get('market_metric')
        feats['allow_daily_extreme_markets'] = self.allow_daily_extreme_markets
        feats['prices_daily_extreme'] = bool(
            metric is not None and self.allow_daily_extreme_markets)
        if metric is not None and not self.allow_daily_extreme_markets:
            # Convention 11: a cannot-run, not a result. We are not saying
            # there is no edge in these markets. We are saying the POINT-IN-TIME
            # model cannot price them and will not pretend to. The daily-extreme
            # model below can; this flag is what selects between them, and it is
            # config-driven so the choice is recorded in one place.
            return decide('SKIP', 'daily_extreme_not_priced_by_point_in_time_model',
                          **feats)

        # 3. TIME TO RESOLUTION, from the SETTLEMENT stamp.
        #
        # Read `hours_to_resolution` before changing anything here: `endDate` is
        # a settlement admin timestamp, not the close of the observation window,
        # and Madrid's sits at 14:00 LOCAL on the very afternoon its market is
        # about. So on the daily-extreme path the horizon gate is applied
        # against the LOCAL DAY's close instead (`observation_window_too_far_out`
        # in `daily_extreme_estimate`), and only the two LIVENESS checks are kept
        # here - a market with no settlement stamp, and one whose stamp has
        # already passed, are over whatever the observation window was.
        #
        # The point-in-time path keeps the horizon gate on `endDate` exactly as
        # it was. Moving it would silently repoint `resolution_too_far_out` and
        # every logged `sigma_f` in the existing tape while the model itself
        # did not change, which would make the pre-change and post-change rows
        # non-comparable for no gain (convention 17).
        hours = self.hours_to_resolution(ctx.market, now)
        feats['end_date'] = getattr(ctx.market, 'end_date', None)
        feats['hours_to_resolution'] = None if hours is None else round(hours, 3)
        if hours is None:
            return decide('SKIP', 'resolution_time_unknown', **feats)
        if hours <= 0:
            return decide('SKIP', 'market_past_resolution_time', **feats)
        if metric is None and hours > self.max_hours_to_resolution:
            return decide('SKIP', 'resolution_too_far_out',
                          max_hours_to_resolution=self.max_hours_to_resolution,
                          **feats)

        # 4. THE AIRPORT READING - the one the market actually settles on.
        reading, read_status = self.airport_feed.observation(station)
        feats['airport_feed_status'] = read_status
        if reading is None:
            # An outage, a bad ICAO and an empty response all arrive here. Each
            # keeps its own status string in `airport_feed_status`; the
            # action-level reason says only "we have no reading", which is the
            # one fact the gate is about.
            return decide('SKIP', 'airport_reading_unavailable', **feats)

        age = reading.age_sec(now)
        feats.update({
            'airport_station': reading.station,
            'airport_temp_f': round(reading.temp_f, 2),
            'airport_temp_c': round(f_to_c(reading.temp_f), 2),
            'airport_obs_ts': reading.observed_ts,
            'airport_obs_age_sec': None if age is None else round(age, 1),
            'max_obs_age_sec': self.max_obs_age_sec,
        })
        if age is None:
            # An observation we cannot age is not a fresh observation.
            return decide('SKIP', 'airport_obs_time_missing', **feats)
        if age > self.max_obs_age_sec:
            # A front can move a station 15F in twenty minutes. An hour-old
            # reading taken during one is a different temperature, not a stale
            # copy of the same one.
            return decide('SKIP', 'airport_obs_stale', **feats)

        # 5. THE DOWNTOWN READING. Diagnostic. Never a gate.
        feats['fetch_downtown'] = self.fetch_downtown
        gap_f = None
        if self.fetch_downtown and city_key is not None:
            lat, lon = WEATHER_MARKETS[city_key]['downtown']
            downtown, downtown_status = self.downtown_feed.observation(lat, lon)
            feats['downtown_feed_status'] = downtown_status
            if downtown is not None:
                gap_f = reading.temp_f - downtown.temp_f
                feats['downtown_temp_f'] = round(downtown.temp_f, 2)
        else:
            feats['downtown_feed_status'] = (
                'not_fetched' if not self.fetch_downtown else 'no_city_mapping')
        feats['airport_minus_downtown_f'] = (None if gap_f is None
                                             else round(gap_f, 2))
        # A missing downtown reading blocks NOTHING. It costs us the
        # measurement, not the trade, and the row says which of the two happened
        # rather than leaving a silent None.
        feats['gap_measured_this_row'] = gap_f is not None
        feats['gap_within_claimed_range'] = (None if gap_f is None
                                             else 3.0 <= abs(gap_f) <= 8.0)

        # 6. OUR PROBABILITY. TWO MODELS, ONE PER RANDOM VARIABLE, and the row
        # says which one priced it. They are never blended and never pooled: a
        # win rate over both would be a win rate over two different questions.
        if metric is None:
            # POINT IN TIME. The market asks about a single reading at the
            # settlement stamp, which is what this prices. Unchanged, so the
            # existing tape stays comparable.
            p_yes = self.probability_yes(reading.temp_f, threshold, hours)
            feats.update({
                'pricing_model': 'point_in_time_normal',
                'sigma_f': round(self.sigma_f(hours), 3),
                'sigma_model': 'sqrt_hours_diffusion_no_diurnal_term',
                'sigma_model_is_wrong_at_known_times_of_day': True,
                'model_prices_point_in_time_not_daily_extreme': True,
            })
        else:
            # DAILY EXTREME. `max(observed_so_far, Normal(forecast + bias,
            # sigma))`. See `DailyExtremeEstimate` for the assumption and for
            # what would falsify it. The observed part is not modelled at all -
            # it has already happened and it is a hard bound.
            estimate, estimate_status, estimate_feats = \
                self.daily_extreme_estimate(ctx.market, station, reading,
                                            metric, now)
            feats.update(estimate_feats)
            if estimate is None:
                # Each of these is its own named cannot-run; `estimate_status`
                # IS the reason string, never a shared "could not price it".
                return decide('SKIP', estimate_status, **feats)
            # 6b. CAN THIS MODEL HAVE AN OPINION ABOUT THIS RUNG AT ALL?
            # Checked BEFORE the probability is used, and gated here rather
            # than left for the edge test to absorb, for exactly the reason the
            # strike-proxy floor is gated before a strategy evaluates: letting
            # a model decline on its own terms inside its own blind spot
            # records a measurement error as a strategy decision, and the two
            # are indistinguishable once they share a reason string.
            #
            # See `max_attainable_p_yes`. Below the floor the model's SIDE is
            # fixed by the bucket's width against our sigma, before any
            # temperature is read.
            attainable = max_attainable_p_yes(threshold, estimate.sigma_f)
            feats.update({
                'max_attainable_p_yes': (None if attainable is None
                                         else round(attainable, 6)),
                'min_attainable_p_yes': MIN_ATTAINABLE_P_YES,
                'rung_is_bounded_on_both_sides': attainable is not None,
            })
            if attainable is not None and attainable < MIN_ATTAINABLE_P_YES:
                # Convention 11: a cannot-run. We are NOT saying this rung is
                # fairly priced. We are saying our sigma is wider than the rung
                # and any answer we give is about the sigma.
                return decide('SKIP', 'rung_narrower_than_model_resolution',
                              **feats)
            # 6c. AND THE SAME ARGUMENT AT THE ENTRY FLOOR RATHER THAN AT 0.5.
            #
            # THIS GATE EXISTS BECAUSE FITTING THE SIGMA OPENED A HOLE, AND THE
            # HOLE BOOKED A REAL ROW. Measured live 2026-08-18 with the fitted
            # sigma on: Ankara's station sigma came back at 1.333F, which puts a
            # 1.8F Celsius bucket's ceiling at 0.500335 - three ten-thousandths
            # ABOVE `MIN_ATTAINABLE_P_YES`, so gate 6b passed it. The row then
            # priced "the highest in Ankara is 31C" at a model 0.097, took the
            # No side at 0.903 against a book at 0.51 and booked a 0.39 "edge".
            # That is precisely the Madrid pathology gate 6b was built for,
            # arriving through a threshold it cleared by a rounding error.
            #
            # The floor that actually bites is the ENTRY conviction, not 0.5: a
            # rung whose Yes side can never reach `min_model_p_side` can only
            # ever be entered on its No side, and that choice was made by the
            # bucket's width against our sigma before a temperature was read.
            # Raven's instruction read literally - "only trade when the model
            # says P(Yes) > 0.55" - refuses exactly these rungs, and this is
            # where the literal reading belongs.
            #
            # ITS OWN REASON, not a widened 6b (convention 20). "Our sigma
            # cannot resolve this rung at all" and "our sigma can resolve it but
            # never confidently enough to prefer Yes" are different distances
            # from tradeable, and one counter for both would hide how close the
            # board is to the line. `MIN_ATTAINABLE_P_YES` is untouched;
            # convention 27, this ADDS a refusal and cannot admit anything.
            feats['min_attainable_p_yes_for_entry'] = self.min_model_p_side
            if attainable is not None and attainable <= self.min_model_p_side:
                return decide('SKIP',
                              'rung_cannot_reach_entry_conviction_on_yes',
                              **feats)

            p_yes = probability_yes_daily_extreme(threshold, estimate)
            feats.update({
                'pricing_model': 'daily_extreme_forecast_anchored_normal',
                'sigma_f': round(estimate.sigma_f, 3),
                'sigma_model': 'daily_extreme_sqrt_hours_to_local_day_close',
                'sigma_model_is_wrong_at_known_times_of_day': False,
                'model_prices_point_in_time_not_daily_extreme': False,
            })
        feats.update({
            'model_p_yes': round(p_yes, 6),
            'model_p_no': round(1.0 - p_yes, 6),
            'confidence': round(max(p_yes, 1.0 - p_yes), 6),
            'confidence_is_model_output_not_measured_win_rate': True,
        })

        # 7. THE BOOKS.
        book_yes = ctx.book('Yes')
        book_no = ctx.book('No')
        if book_yes is None and book_no is None:
            return decide('SKIP', 'no_orderbook', **feats)
        ask_yes = None if book_yes is None else book_yes.best_ask
        ask_no = None if book_no is None else book_no.best_ask
        feats.update({'ask_yes': ask_yes, 'ask_no': ask_no})
        if ask_yes is None and ask_no is None:
            # An empty book and a bids-only book are the same fact for a BUY:
            # nothing to lift at any price.
            return decide('SKIP', 'no_asks', **feats)

        # The market's implied P(Yes). Read off the Yes ask where there is one,
        # otherwise inferred from the No ask. The inference is stated on the row,
        # not silent: an inferred probability carries the No side's spread as
        # well as its own.
        if ask_yes is not None:
            implied_yes = float(ask_yes)
            implied_source = 'yes_ask'
        else:
            implied_yes = 1.0 - float(ask_no)
            implied_source = 'inferred_from_no_ask'
        feats['market_implied_p_yes'] = round(implied_yes, 6)
        feats['market_implied_source'] = implied_source

        market_side = 'Yes' if implied_yes > 0.5 else 'No'
        model_side = 'Yes' if p_yes > 0.5 else 'No'
        feats.update({'market_implied_side': market_side,
                      'model_side': model_side})
        if abs(implied_yes - 0.5) < 1e-12 or abs(p_yes - 0.5) < 1e-12:
            # A market or a model sitting exactly on 0.5 has no direction to
            # disagree with. Its own cause, never pooled with a real agreement.
            return decide('SKIP', 'market_implied_direction_unreadable', **feats)
        if market_side == model_side:
            # The crowd already agrees with the airport. Whatever edge this
            # strategy claims comes from them anchoring on the WRONG
            # thermometer, so when they are anchored on the right one there is
            # nothing here. This is the strategy WORKING, and it is expected to
            # be the overwhelming majority of rows.
            return decide('SKIP', 'airport_agrees_with_market', **feats)

        side = model_side
        p_side = p_yes if side == 'Yes' else 1.0 - p_yes
        book = book_yes if side == 'Yes' else book_no
        feats.update({'outcome_side': side, 'model_p_side': round(p_side, 6)})
        if book is None or book.best_ask is None:
            # We disagree with the market on the one side we cannot buy.
            return decide('SKIP', 'no_asks', **feats)

        # 7b. CONVICTION, which is not the same test as PRICE.
        #
        # `MIN_EDGE` below asks "are we paid enough for this". This asks "do we
        # believe it enough to pay anything". A model at 0.502 against a book at
        # 0.40 clears an 8c edge while being, on its own arithmetic, a coin
        # flip - and the sigma underneath it is fitted on 7 to 16 station-days.
        # `>` not `>=`, so a model sitting exactly on the floor is refused:
        # 0.55 is the smallest conviction that is allowed to trade, not the
        # largest that is not.
        feats['min_model_p_side'] = self.min_model_p_side
        if not p_side > self.min_model_p_side:
            return decide('SKIP', 'model_confidence_below_entry_floor', **feats)

        # 8. PRICE. The cap IS the edge: quote the worst price at which the
        # trade still clears MIN_EDGE, and gate on the BOOK-WALKED average under
        # it, so a fill several cents inside the cap is reported at what it
        # actually cost (the house rule in base.Leg.premium).
        cap = floor_to_tick(p_side - self.min_edge)
        feats.update({'min_edge': self.min_edge, 'entry_cap': cap,
                      'best_ask_on_side': book.best_ask})
        if cap < PRICE_TICK:
            # The threshold eats the whole price. There is no limit at or above
            # one tick that still carries the edge.
            return decide('SKIP', 'edge_below_min',
                          edge_reason='min_edge_exceeds_model_probability',
                          **feats)

        # Size DOWN to the notional cap rather than letting the adapter reject
        # the order. "20 shares does not fit in $10 at this price" and "the risk
        # gate refused you" are two different facts.
        affordable = int(math.floor(self.max_notional_usdc / cap + 1e-9))
        shares = min(self.target_shares, affordable)
        feats.update({'target_shares': self.target_shares,
                      'affordable_shares_at_cap': affordable,
                      'shares': shares,
                      'shares_capped_by_notional': shares < self.target_shares})
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        effective = effective_ask_for(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > cap:
            # INVARIANT GUARD. walk_book cannot return this under the same
            # limit, but the cap is the whole edge and a silent regression here
            # would be invisible. Same guard fair_value_arb keeps.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        edge = p_side - effective
        edge_bps = (round(edge / effective * 10_000, 1) if effective > 0
                    else None)
        feats.update({
            'realized_edge': round(edge, 4),
            'realized_edge_bps': edge_bps,
            # CONVENTION 5, IN THE RIGHT UNITS. The 30 bps dead-on-arrival floor
            # is a crypto number; on a binary the smallest expressible move is
            # 0.001 (a tenth of a cent, observed on the live tape), which is
            # 20 bps on a 50-cent contract (D-336). Carried on the row
            # rather than gated on, because `min_edge` already gates far above
            # it (8c on 50c is 1,600 bps) and a second gate on the same fact
            # would make the binding one ambiguous.
            'binary_tick_floor_bps': POLYMARKET_TICK_ON_FIFTY_CENTS_BPS,
            'edge_clears_binary_tick_floor': (
                None if edge_bps is None
                else edge_bps >= POLYMARKET_TICK_ON_FIFTY_CENTS_BPS),
            'breakeven_win_rate_if_held': round(effective, 4),
            'notional_usdc': round(shares * effective, 4),
        })
        if edge < self.min_edge:
            # INVARIANT GUARD, and unreachable through the normal path: `cap` is
            # floored onto the tick grid at or below `p_side - min_edge`, so any
            # fill at or under the cap already clears MIN_EDGE. It stays because
            # the invariant is the whole entry rule, and a future edit to `cap`
            # that broke it would otherwise book entries with no edge and no
            # error. `edge_reason` keeps the two flavours apart in a log.
            return decide('SKIP', 'edge_below_min',
                          edge_reason='walked_price_ate_the_edge', **feats)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
