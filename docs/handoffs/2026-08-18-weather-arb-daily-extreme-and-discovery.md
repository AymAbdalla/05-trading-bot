# Cody handoff: weather_arb fixed and wired (Task 6)

**Date:** 2026-08-18
**Session:** Task 6, fix and wire `weather_arb`
**Decision:** DECISIONS.md, "Weather markets get a daily-extreme model, a
discovery cycle, and their own counters" (landed as D-311; D-306 was taken by a
concurrent session mid-task, convention 24 again).

## The short version

`PM_weather_arb` was never blocked by its feeds. It was being handed a BTC
Up/Down 5m market 57 times a cycle and reporting `resolution_station_unknown`,
which is a completely different fact from the one it was recording. It now has
its own market universe, its own cycle, its own counters, and a model that
prices the variable these markets actually resolve on.

It reaches the pricing stage on live data, verified end to end through the loop
against the real Gamma, aviationweather.gov and open-meteo APIs. It books ZERO
entries, and that is the correct answer rather than a failure. See "the first
two entries were arithmetic" below.

## What was built

### 1. The model prices a DAILY EXTREME

Old: `probability_yes` priced a single reading at the settlement timestamp.
New, for any market whose `market_metric` is `daily_high` or `daily_low`:

    M = max(O, X)   for a high,    M = min(O, X)   for a low

`O` is the extreme the station has ALREADY reported inside the market's LOCAL
observation day, read from aviationweather.gov's `hours=` history endpoint. It
is not modelled. It is a hard bound: a day that has produced 33.0C cannot have a
maximum of 30C, probability exactly 0.0, no sigma involved.

`X ~ Normal(mu, sigma)` where `mu` is open-meteo's forecast daily extreme for
that local date AT THE STATION'S OWN COORDINATES, plus the current
station-minus-grid bias, and `sigma = 1.0 + 0.35 * sqrt(hours to local day
close)`.

The distributional assumption and its falsifier are written out in full on
`DailyExtremeEstimate`. Three things are assumed and each can be attacked
separately: normality of an extreme (wrong in the tail, a max is asymptotically
Gumbel), bias persistence to the diurnal peak (it drifts), and the two sigma
constants (convention 15 estimates, never fitted).

### 2. Config, not hardcoded

`config.yaml: polymarket.weather.allow_daily_extreme_markets: true` (Aym
approved) and `require_observed_extreme: true`. Applied through
`weather_arb.set_weather_config` from `shadow_loop.main()`, same shape as the
strike-proxy floor. Module default stays `False` so every existing caller and
test keeps its behaviour. An unknown key or a non-boolean value RAISES.

### 3. A weather cycle in the shadow loop

`run_weather_cycle` on a 60-second cadence. Discovery through
`find_weather_markets` (the existing tag-route sweep), then
`rank_weather_markets` picks the top 8 by volume among markets that actually
have a station, a threshold and a daily extreme.

**It does not touch the crypto identity.** `_count`, `_log_and_count`,
`evaluate_strategy` and `_attempt_entry` all take an optional counter, default
`None` = the crypto identity space. The weather cycle passes its own Counter and
has its own identity check. There is a test that runs a weather cycle and
asserts `loop.evaluations` and `loop.counts` did not move at all.

### 4. Feeds

- **METAR history** added to the existing inline `AirportWeatherFeed`, with its
  own cache, its own TTL and its own timeout. `lat`/`lon` now come off the METAR
  payload, which is what makes a station forecast possible without a second
  hand-maintained coordinate table for 51 cities.
- **`StationForecastFeed`**, new, open-meteo daily + hourly at the station's own
  coordinates with `timezone=auto`. Unit verification is IMPORTED from
  `engine.feeds.open_meteo.normalise_unit` rather than restated (convention 23).

## Things Raven should look at

### The first two entries were arithmetic. This is the most important finding.

The first live run booked 2 entries at realised edges of 0.43 and 0.34. I
checked them instead of reporting them, and they were not edge.

For a bounded rung of width `w` under a normal of standard deviation `sigma`,
the largest Yes probability the model can ever return is
`2 * Phi(w / (2 * sigma)) - 1`. That depends on nothing but the width and the
sigma. Not the temperature, not the forecast, not the station.

A Celsius bucket is 1.8F wide. At a 31.5-hour horizon the sigma is 2.96F:

    ceiling = 2 * Phi(1.8 / 5.92) - 1 = 0.239

The Madrid "highest is 36C" row returned **0.238**. The model was already at its
ceiling. It could not have said Yes about that rung whatever Madrid did. It then
"disagreed" with a book at 0.64, took No, and booked 0.43. It would do that on
nine of the eleven rungs of every ladder, every cycle, forever.

That is the `strike_inside_proxy_noise_floor` shape exactly, so it gets the same
treatment: refuse where the instrument cannot resolve.
`MIN_ATTAINABLE_P_YES = 0.5` is where "the model cannot prefer Yes" flips, so it
is a property of the arithmetic and not a threshold I picked. Rungs below it are
refused as `rung_narrower_than_model_resolution`, classified DATA_BLOCKER in
`forge_shadow_eval` because the missing input is a FITTED sigma.

**Re-measured with the gate in, same board, 20 markets: 0 entries, 17
`rung_narrower_than_model_resolution`, 2 `airport_agrees_with_market`, 1
`observation_window_too_far_out`.**

What survives: both ladder TAILS (unbounded on one side, no ceiling) and
Fahrenheit RANGE buckets inside about an hour of the close. A whole-degree
Fahrenheit bucket has a ceiling of 0.31 even at the close and can never be
priced by this sigma. The way to widen that reach is to FIT the sigma, not to
lower the floor.

### The claim changed. This is the other important one.

The airport-versus-downtown thesis is a claim about a MEASUREMENT: retail reads
a downtown grid cell, the market resolves on an airport station. The
daily-extreme model's centre is open-meteo's FORECAST, so when it disagrees with
the book, the disagreement is mostly "our forecast provider expects a different
afternoon peak than the crowd does" and only secondarily "the crowd is reading
the wrong thermometer".

Those are two different claims with two different kill conditions, and the
second one does not imply the first. It matters for what the kill condition
should be and for what a future harness is actually scoring. Every row is
stamped `pricing_model`, `station_minus_grid_bias_f`, `horizon_beyond_same_day`,
`max_attainable_p_yes` and `daily_extreme_calibration_harness_exists: false`, so
the populations stay separable.

### Two blockers found by running it, both fixed, both worth knowing

1. **`MAX_HOURS_TO_WINDOW_CLOSE` was 30 and refused the entire tradable board.**
   The high-volume city ladders are all for the NEXT local day (688 of 2,035 raw
   markets were already past their settlement stamp), and a European next-day
   ladder closes 31.5 hours out. Raised to 36, which matches
   `MAX_HOURS_TO_RESOLUTION` and still refuses a US next-day ladder at 38.5. The
   cost is stated: sigma at 36h is 3.1F and that is an extrapolation of a
   same-day number, so rows past 24 hours carry `horizon_beyond_same_day=True`.

2. **"The window has not opened yet" and "we could not read the window" were one
   counter.** Refusing both meant refusing every next-day market forever. They
   are now split: a window that has not opened has no floor to miss and is
   priced; an OPEN window we cannot read is refused under
   `daily_extreme_history_unavailable`. This is the convention 20 split that
   matters most in the file, because the safe response to the two is opposite.

### Deliberately NOT done

- **The D-305 METAR consolidation onto `engine/feeds/noaa_weather.py`.** That is
  a separate assigned task with its own skip-vocabulary churn. The inline cache
  in `weather_arb.py` IS the currently-wired METAR source; `noaa_weather.py` is
  still orphaned. I extended the wired one rather than adding a third path.
- **No process was killed or restarted.** Note that the `shadow_runner` wrapper
  AUTO-RESTARTS the loop, so whenever the running loop next dies it will respawn
  onto this source and the weather cycle will start against `db/trading.db`.
  That is intended, but somebody should know it will happen without anyone
  choosing a moment.

## Still open

- `backtest/measure_daily_extreme_calibration.py` does not exist. Until it does,
  every entry from this path is TAPE. Honest tape, not fabricated tape, but tape.
- The airport-versus-downtown gap is still unmeasured. `DowntownWeatherFeed`
  still runs on every row and still gates nothing, which is the only route by
  which "3 to 8 degrees" ever becomes a number.
- `hours_to_resolution` still reads `endDate` on the point-in-time path. Left
  unchanged on purpose so the existing tape stays comparable; the daily-extreme
  path uses the local day close instead, which is the correct horizon.
