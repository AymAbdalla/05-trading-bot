"""Read live Polymarket shadow-trading results and turn them into Forge gaps.

The graveyard tells Forge what happened in a BACKTEST. This module tells it what
happened in the SHADOW LOOP: which strategies actually fired against a live
book, which never fired, and - the part that matters most - WHY they did not.

The central distinction, and the reason this module exists rather than a
`SELECT count(*) GROUP BY strategy`:

  DATA_BLOCKER  the strategy could never have fired because an input it needs
                was absent. `no_spot_or_strike` is not "the condition was not
                met", it is "the condition was never evaluated". Convention 11:
                that strategy is NOT_TESTED. It did not look and decline.
  GENUINE       the strategy had every input, evaluated its condition, and the
                condition was false. That IS a measurement, though a thin one.
  SIM_LIMIT     the strategy DECIDED to act and OUR SIDE refused - the paper
                adapter could not model the fill (a maker quote against a
                taker-only simulator), the risk gate blocked the entry, or the
                kill switch was engaged. Also NOT_TESTED, and for a reason that
                is ours, not the market's. The strategy passed its own test and
                never got to find out whether the market agreed, so counting
                these as GENUINE would read as "it looked and declined" - the
                exact inversion convention 11 exists to prevent.
  UNKNOWN       a reason string this module has never seen. Convention 20: it
                is counted and surfaced, never folded into one of the three
                above, because a silently reclassified skip is a missing number.

Conventions enforced here rather than trusted to a reader:
  11. an unreadable DB is NOT an empty DB. `evaluate()` returns
      status='unreadable' with the exception text; it never returns a clean
      zero that would read as "the strategies looked and declined"
  19. every number that leaves here is finite, so the caller can
      json.dump(allow_nan=False) it
  20. every decision row lands in exactly one bucket, and the accounting
      identity is ASSERTED: entries + skips + other == total rows, and
      sum(skip class counts) == skips

This module is READ ONLY. It opens the DB with mode=ro because the shadow loop
may be writing to it right now.
"""
import collections
import csv
import json
import math
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_DB = os.path.join(ROOT, 'db', 'trading.db')
DEFAULT_PAPER_LOG = os.path.join(
    ROOT, 'research', 'polymarket_paper', 'polymarket_paper_log.csv')

# A strategy with three evaluations that never fired has told us nothing. This
# is the floor below which "never fired" is not yet a gap, only a small sample.
# Convention 17: a hardcoded threshold is an assumption with an expiry date.
# The expiry here is "when a shadow session routinely runs thousands of cycles",
# at which point 30 is far too generous and should rise.
MIN_EVALUATIONS_FOR_GAP = 30

# Above this share of a strategy's skips being data-blocked, the strategy is
# NOT_TESTED rather than tested-and-silent. Set at a bare majority on purpose:
# if most of the time the inputs were not there, the minority of evaluated
# cycles is not a sample anyone should reason from.
DATA_BLOCKED_FRACTION = 0.5

DATA_BLOCKER = 'DATA_BLOCKER'
GENUINE = 'GENUINE'
SIM_LIMIT = 'SIM_LIMIT'
UNKNOWN = 'UNKNOWN'

# Every skip reason emitted by strategies/polymarket/*.py, classified once,
# here, with the input that is missing named for the blockers. Sourced by
# grepping `decide('SKIP', '...')` across strategies/polymarket/ on 2026-08-17.
#
# The rule for deciding a row: does the reason name a MISSING INPUT or a FALSE
# CONDITION? `no_atr` is missing input. `lead_below_zone` is a false condition
# computed FROM an input that was present. When a reason could be read either
# way, it goes to DATA_BLOCKER, because over-reporting NOT_TESTED costs a
# re-test and under-reporting it puts a fabricated verdict in the record.
SKIP_CLASSIFICATION: Dict[str, Tuple[str, str]] = {
    # --- inputs that were absent -------------------------------------------
    'no_spot_or_strike': (DATA_BLOCKER, 'window strike (Chainlink 60s TWAP, '
                                        'not published by Gamma) and/or spot'),
    'no_lead_or_atr': (DATA_BLOCKER, 'lead_bps (needs the strike) and/or '
                                     'atr14'),
    'no_atr': (DATA_BLOCKER, 'atr14'),
    'no_spot': (DATA_BLOCKER, 'spot price'),
    # Emitted from BOTH sides: ~16 strategies raise it when the context carries
    # no market, and `shadow_loop.py` raises it when Gamma has no market for the
    # window at all. Same missing input, so one entry - but it was written twice
    # (here and again in the 2026-08-18 block below), and a duplicate dict key
    # is not an error in Python: the LATER one silently won. Merged 2026-08-18.
    'no_market': (DATA_BLOCKER, 'a market for this window (either absent from '
                                'the context or not served by Gamma)'),
    'no_orderbook': (DATA_BLOCKER, 'CLOB orderbook'),
    'no_asks': (DATA_BLOCKER, 'ask side of the book'),
    'no_bids_to_join': (DATA_BLOCKER, 'bid side of the book'),
    # `maker_rebate_corridor_quote_ladder` rests a BUY, so it needs the bid
    # side to have a price to join. Named separately from `no_bids_to_join`
    # rather than pooled with it: two strategies missing the same side of the
    # book are still two facts, and convention 20 forbids one counter for two
    # drop causes.
    'no_bids': (DATA_BLOCKER, 'bid side of the book'),
    'no_magnitude_data': (DATA_BLOCKER, 'move magnitude series'),
    'no_window_clock': (DATA_BLOCKER, 'window clock (seconds_remaining)'),
    'no_window_open': (DATA_BLOCKER, 'window open price'),
    'invalid_window_open': (DATA_BLOCKER, 'usable window open price'),
    'invalid_strike': (DATA_BLOCKER, 'usable strike'),
    'missing_market_leg': (DATA_BLOCKER, 'the 5m or the 15m market leg'),
    'insufficient_window_history': (DATA_BLOCKER, 'prior windows (warmup)'),
    'unreadable_window_direction': (DATA_BLOCKER, 'per-window direction'),
    'zero_atr_undefined_ratio': (DATA_BLOCKER, 'non-zero atr14'),
    'zero_atr_undefined_stretch': (DATA_BLOCKER, 'non-zero atr14'),
    'insufficient_book_depth': (DATA_BLOCKER, 'book depth'),
    'insufficient_ask_depth': (DATA_BLOCKER, 'ask depth'),
    'insufficient_depth_for_pair': (DATA_BLOCKER, 'book depth on both legs'),
    'degenerate_quote': (DATA_BLOCKER, 'a well-formed two-sided quote'),

    # --- the simulator, not the market -------------------------------------
    'maker_fill_not_simulated': (SIM_LIMIT, 'the paper adapter models taker '
                                            'fills only; this is a QUOTE'),
    'symmetric_disabled': (SIM_LIMIT, 'a config switch, not a market state'),

    # --- conditions that were evaluated and were false ----------------------
    'no_streak': (GENUINE, ''),
    'not_stretched': (GENUINE, ''),
    'not_through_strike': (GENUINE, ''),
    'not_a_coin_flip': (GENUINE, ''),
    'no_reversal_yet': (GENUINE, ''),
    'lead_below_zone': (GENUINE, ''),
    'lead_above_zone': (GENUINE, ''),
    'lead_inside_noise': (GENUINE, ''),
    'book_too_tight_to_arm': (GENUINE, ''),
    # Both evaluated a real condition against real inputs and it was false: a
    # clock gate that had a clock, and a price band that had a mid.
    'quote_outside_arm_band': (GENUINE, ''),
    'mid_outside_quote_band': (GENUINE, ''),
    'book_not_wide_enough': (GENUINE, ''),
    'ask_above_band': (GENUINE, ''),
    'ask_above_cap': (GENUINE, ''),
    'effective_ask_above_band': (GENUINE, ''),
    'effective_ask_below_band': (GENUINE, ''),
    'effective_ask_above_cap': (GENUINE, ''),
    'edge_below_threshold': (GENUINE, ''),
    'edge_threshold_exceeds_fair_value': (GENUINE, ''),
    'fair_value_outside_tradeable_band': (GENUINE, ''),
    'pair_cost_above_cap': (GENUINE, ''),
    'pair_cost_above_binned_fair': (GENUINE, ''),
    'pair_cost_above_edge_threshold': (GENUINE, ''),
    'pair_unfillable_at_caps': (GENUINE, ''),
    'no_profitable_completion': (GENUINE, ''),
    'completion_ask_above_cap': (GENUINE, ''),
    'unfillable_at_cap': (GENUINE, ''),
    'unfillable_at_band_high': (GENUINE, ''),
    'unsizable_at_notional_cap': (GENUINE, ''),
    'past_quote_window': (GENUINE, ''),
    'late_in_window': (GENUINE, ''),
    'too_late_in_window': (GENUINE, ''),
    'too_close_to_resolution': (GENUINE, ''),
    'out_of_time_band': (GENUINE, ''),
    'window_not_open': (GENUINE, ''),
    'already_entered_this_window': (GENUINE, ''),
    'max_trades_this_window': (GENUINE, ''),
    'pair_complete': (GENUINE, ''),
    'unpaired_leg_held_to_resolution': (GENUINE, ''),

    # === added 2026-08-18 ==================================================
    # The 2026-08-17 grep above went stale: concurrent sessions added the
    # fair-value INVERSE, three liquidation-fed strategies, smart-money copy,
    # dip arb and weather arb, and none of their reasons were classified. That
    # left 18.1% of all skips in UNKNOWN, dominated by one reason
    # (`strike_inside_proxy_noise_floor`, 6,468 rows) that is a DATA BLOCKER -
    # so the headline "these strategies ran and declined" was wrong about
    # nearly a fifth of the evidence. Re-derived by walking the AST of
    # strategies/polymarket/*.py for every SKIP literal, not by grep.

    # --- the loop's own skips, not any strategy's --------------------------
    # `engine/polymarket/shadow_loop.py` attributes cycle-level failures to
    # each strategy individually, so these reach this table.
    'strike_inside_proxy_noise_floor': (
        DATA_BLOCKER, 'a strike outside the measured proxy noise floor '
                      '(STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0). The signal was '
                      'inside our INSTRUMENT ERROR, so the window was never '
                      'a test of the strategy'),
    # (`no_market` lived here too until 2026-08-18; merged into the single
    # entry in the block above rather than shadowing it.)
    'no_liquidity': (DATA_BLOCKER, 'a two-sided, uncrossed book'),
    'api_error': (DATA_BLOCKER, 'the venue could not be reached at all - '
                                'never merged with no_market'),
    'cycle_exception': (DATA_BLOCKER, 'the cycle raised; the strategy never '
                                      'evaluated'),
    'enter_without_legs': (DATA_BLOCKER, 'a well-formed ENTER (no legs)'),
    'unknown_outcome_token': (DATA_BLOCKER, 'a token id resolvable to an '
                                            'outcome side'),

    # --- our side refused a decided action ---------------------------------
    # RETIRED 2026-08-18 (see RETIRED_SKIP_REASONS) and kept here because
    # ~220k historical CSV rows carry it and an unclassifiable historical row
    # would surface as UNKNOWN evidence rather than as filed evidence.
    'maker_quote_not_simulable': (SIM_LIMIT, 'RETIRED. The paper adapter '
                                             'modelled taker fills only and '
                                             'every QUOTE was dropped under '
                                             'this one string; the maker fill '
                                             'model is wired now'),
    'halted': (SIM_LIMIT, 'the kill switch was engaged; entry blocked'),

    # --- the MAKER path (wired 2026-08-18) ---------------------------------
    # `maker_quote_not_simulable` is retired below. It was the short-circuit
    # that counted every QUOTE under one string and threw the legs away, so
    # both maker strategies produced one number forever and that number
    # described the LOOP, not them. The loop now rests the legs and these are
    # the eight distinct causes that bucket was pooling.
    #
    # `maker_quote_rested` is NOT a data blocker and NOT a simulator limit: the
    # order is on the book and the fill is genuinely undecided until a later
    # snapshot. It is the maker equivalent of "ran and found nothing yet", so
    # it is GENUINE - a rest that never fills is a RESULT about the strategy's
    # pricing, which is exactly what box_builder and grid_hedge are being asked.
    'maker_quote_rested': (GENUINE, ''),
    'maker_halted': (SIM_LIMIT, 'the kill switch was engaged; the resting buy '
                                'was refused and any already on the book were '
                                'cancelled'),
    'maker_quote_without_legs': (DATA_BLOCKER, 'a well-formed QUOTE (no legs)'),
    'maker_quote_already_resting': (
        SIM_LIMIT, 'nothing - an order for this token is ALREADY on the book. '
                   'One quote per strategy per token; we do not chase'),
    'maker_rest_budget_exhausted': (
        SIM_LIMIT, 'a free maker slot. The resting-order budget exists so the '
                   'maker strategies cannot consume every concurrency slot and '
                   'starve the 17 taker strategies'),
    # `maker_rebate_corridor_quote_ladder`'s own per-window cap (proposal 024
    # rule 4: one resting order per market per window). SIM_LIMIT and not
    # GENUINE, on the same argument as `maker_quote_already_resting` directly
    # above: OUR budget refused, the market never got a look in. Calling it
    # GENUINE would read as "it evaluated the book and declined", which is the
    # precise confusion the SIM_LIMIT class exists to prevent.
    'already_quoted_this_window': (
        SIM_LIMIT, 'a free quote slot for this window - our own cap refused, '
                   'not the book'),

    # --- liquidation-fed strategies ----------------------------------------
    # `no_cascade` is flagged in its own source as "RAN and found nothing. The
    # only result-shaped skip in this block", and `balanced_liq_tape` is a
    # two-way squeeze actually observed - both are measurements.
    'no_cascade': (GENUINE, ''),
    'balanced_liq_tape': (GENUINE, ''),
    'liq_not_dominant_enough': (GENUINE, ''),
    'insufficient_liq_count': (GENUINE, ''),
    'liq_clusters_balanced': (GENUINE, ''),
    'no_liq_cluster_near_spot': (GENUINE, ''),
    'move_not_confirming_liq_direction': (GENUINE, ''),
    'mega_liq_belongs_to_cascade_chaser': (GENUINE, ''),
    'spread_too_wide': (GENUINE, ''),
    'too_early_in_window': (GENUINE, ''),
    'no_bids_for_spread': (DATA_BLOCKER, 'bid side of the book'),
    'no_window_open_bar': (DATA_BLOCKER, 'the window-open bar'),
    # An unmappable venue side would default to a bearish signal downstream,
    # which is the failure liq_cascade_chaser is written to prevent.
    'unmappable_liquidated_side': (DATA_BLOCKER, 'a venue order side that maps '
                                                 'to a liquidated side'),

    # --- smart money copy ---------------------------------------------------
    # The module draws this line itself: "Could not run. Never 'we looked and
    # the whales were quiet.'" A measured record that fails IS a result.
    'wallet_feed_unavailable': (DATA_BLOCKER, 'the tracked-wallet feed'),
    'wallet_address_unresolved': (DATA_BLOCKER, 'a resolvable wallet address'),
    'wallet_record_unmeasured': (DATA_BLOCKER, "the wallet's track record"),
    # 2026-08-18: split out of `wallet_record_unmeasured`. We SCORED some of
    # this wallet's fills against how their markets actually resolved, and got
    # fewer than the minimum sample. That is NOT_TESTED - a 75% on 8 trades is
    # a shrug (convention 7) - and it is a different fact from having scored
    # nothing at all, so it gets its own counter (convention 20).
    'wallet_record_insufficient_sample': (
        DATA_BLOCKER, 'enough RESOLVED trades to measure a win rate on'),
    'wallet_record_mixed_unmeasured_and_below': (
        DATA_BLOCKER, 'a track record for at least one wallet (some records '
                      'were unmeasured, some measured-and-below)'),
    # Any other mix of the three record-gate causes (unmeasured / insufficient
    # sample / below threshold). At least one leg is a data blocker, so the
    # whole row is classified as one rather than being credited as a result.
    'wallet_record_mixed_causes': (
        DATA_BLOCKER, 'a measurable, large-enough track record for at least '
                      'one wallet'),
    'no_trade_clock': (DATA_BLOCKER, 'a timestamp on the copied trade'),
    'wallet_record_below_threshold': (GENUINE, ''),
    'no_tracked_wallet_trades': (GENUINE, ''),
    'no_tracked_wallet_buy': (GENUINE, ''),
    'no_trade_in_this_market': (GENUINE, ''),
    'already_copied_this_trade': (GENUINE, ''),
    'copied_trade_stale': (GENUINE, ''),
    'ask_above_max_entry_price': (GENUINE, ''),

    # --- smart money callers (proposal 027) ---------------------------------
    # Same discipline as smart money copy directly above: a caller feed that
    # could not be read, or a caller this file has never tracked, is
    # NOT_TESTED. A play that failed to MAP to this market, or that has
    # already been entered, or that lost the book's price/depth gates, is a
    # real evaluated condition - GENUINE.
    'caller_feed_unavailable': (DATA_BLOCKER, "the watched caller's Reddit "
                                              'feed (redlib mirror unreachable '
                                              'or every watched caller failed '
                                              'this cycle)'),
    'caller_record_unknown': (
        DATA_BLOCKER, "a data/caller_record.json entry for this caller - "
                      'strategies/polymarket/caller_feed.py has never '
                      'recorded a parseable declared play from them yet'),
    'outcome_side_unresolvable': (
        DATA_BLOCKER, "an outcome-side label pair (Yes/No or Up/Down) on "
                      'this market - the direction mapping refuses to guess '
                      'a side when neither pair is present'),
    'no_declared_plays': (GENUINE, ''),
    'no_declared_play_for_market': (GENUINE, ''),
    'already_entered_this_play': (GENUINE, ''),
    'book_cannot_fill': (GENUINE, ''),

    # --- dip arb ------------------------------------------------------------
    'insufficient_tape': (DATA_BLOCKER, 'enough price tape to compute a mean'),
    # 2026-08-18: split out of `insufficient_tape` (proposal 031 phase 1,
    # convention 20) once every candidate already has a book and an ask -
    # the only remaining question is whether the tape has started at all.
    # Same DATA_BLOCKER class as the parent: both are CANNOT MEASURE.
    'insufficient_tape_building': (
        DATA_BLOCKER, 'enough price tape to compute a mean'),
    'insufficient_tape_not_yet_observed': (
        DATA_BLOCKER, 'enough price tape to compute a mean'),
    'no_outcomes': (DATA_BLOCKER, 'priced outcomes on the market'),
    'mean_outside_tradeable_band': (GENUINE, ''),
    'dip_below_threshold': (GENUINE, ''),
    'dip_threshold_exceeds_mean': (GENUINE, ''),

    # --- weather arb --------------------------------------------------------
    'no_clock': (DATA_BLOCKER, 'a window clock'),
    'resolution_station_unknown': (DATA_BLOCKER, 'the resolution station'),
    'resolution_station_ambiguous': (DATA_BLOCKER,
                                     'an unambiguous resolution station'),
    'threshold_unparseable': (DATA_BLOCKER,
                              'a parseable temperature threshold'),
    'resolution_time_unknown': (DATA_BLOCKER, 'the resolution time'),
    'airport_reading_unavailable': (DATA_BLOCKER, 'the airport observation'),
    'airport_obs_time_missing': (DATA_BLOCKER,
                                 'a timestamp on the airport observation'),
    # A stale reading is a DIFFERENT temperature, not a late one: a front can
    # move a station 15F in twenty minutes.
    'airport_obs_stale': (DATA_BLOCKER,
                          'an airport observation inside max_obs_age_sec'),
    'market_implied_direction_unreadable': (DATA_BLOCKER,
                                            'a readable market-implied side'),
    'airport_agrees_with_market': (GENUINE, ''),
    'market_past_resolution_time': (GENUINE, ''),
    'resolution_too_far_out': (GENUINE, ''),
    'edge_below_min': (GENUINE, ''),
    # weather_arb.py:1654, checked FIRST, before the station. A concurrent
    # session classed this GENUINE on the reading "the question WAS read and
    # the product was declined". D-291 RULES DATA_BLOCKER and that is what is
    # here now: the strategy has no station for a global-anomaly market, so it
    # cannot evaluate one. GENUINE would report "the strategy looked and found
    # no edge" about a product it has no instrument for, which reads as a
    # measurement and is not one - the convention 11 inversion.
    # It still exists as its own reason precisely so these never pool into
    # `resolution_station_unknown` above, which would read as a blocked
    # station on a product that has no station BY DESIGN. Two causes, two
    # names, both DATA_BLOCKER (convention 20).
    # D-291 also notes a fourth class, OUT_OF_UNIVERSE, would be conceptually
    # better here, and is premature for one reason. Revisit if the count grows.
    'global_temperature_market_excluded': (
        DATA_BLOCKER, 'a resolution station - this strategy prices CITY '
                      'stations and a global-anomaly market has none, so the '
                      'market type cannot be evaluated at all'),
    # weather_arb.py:1704 and :1707, both from `reporting_step_checked`, and
    # both DATA_BLOCKER for the same reason under two different causes: the
    # eleven-rung ladder's edges are only defined when the source rounds to
    # WHOLE degrees. Neither is an edge that was computed and found thin.
    'source_reporting_precision_unknown': (
        DATA_BLOCKER, 'a stated reporting precision in the rules text - '
                      'without it the rung edges are a guess'),
    # Precision IS known (0.1 native, the 66 Hong Kong markets), and that is
    # what makes it unusable: [26.5, 27.5) and [27.0, 28.0) are both "27C"
    # and Polymarket has not published which. The missing input is the rung
    # EDGE, not the reading.
    'source_precision_finer_than_ladder_step': (
        DATA_BLOCKER, 'published ladder rung edges for a sub-degree source'),
    # weather_arb.py:1751. The market asks about the DAY'S HIGH/LOW; this model
    # prices a POINT IN TIME. The module's own comment invokes convention 11
    # for it: not "there is no edge in these markets", but "this model cannot
    # price them and will not pretend to". Gated behind the named
    # `allow_daily_extreme_markets` flag so any override's rows stay separable.
    'daily_extreme_not_priced_by_point_in_time_model': (
        DATA_BLOCKER, 'a daily-extreme model - the point-in-time model cannot '
                      'price a high/low question'),

    # --- weather arb: THE WRONG PRODUCT -------------------------------------
    # Added 2026-08-18 with the weather cycle. Before that cycle existed the
    # shadow loop handed `PM_weather_arb` a BTC Up/Down 5m market on every poll
    # and it came back `resolution_station_unknown` - which classified as
    # "the resolution station is missing" for a market that has no weather in
    # it at all. Two entirely different facts under one counter; convention 20.
    #
    # DATA_BLOCKER for the same reason `global_temperature_market_excluded` is:
    # the strategy has no instrument for this product, so it did not look and
    # find no edge. GENUINE here would put a fabricated verdict in the record.
    'not_a_temperature_market': (
        DATA_BLOCKER, 'a temperature market - this evaluation was handed a '
                      'crypto Up/Down window, which this strategy has no '
                      'instrument for'),

    # --- weather arb: THE DAILY EXTREME MODEL'S OWN MISSING INPUTS ----------
    # All DATA_BLOCKER, and every one of them names a specific input that was
    # absent rather than a condition that was computed and came out false. The
    # daily-extreme model needs four things the point-in-time model did not:
    # the station's coordinates, a forecast at them, the market's observation
    # DAY, and the station's running extreme so far inside that day.
    'station_coordinates_unknown': (
        DATA_BLOCKER, 'the station lat/lon (normally on the METAR payload) - '
                      'without it no forecast can be requested'),
    'station_forecast_unavailable': (
        DATA_BLOCKER, 'the open-meteo daily/hourly forecast at the station'),
    'resolution_date_unparseable': (
        DATA_BLOCKER, 'the observation DATE - the question named none, and '
                      'endDate is a settlement stamp, not the window'),
    'resolution_date_outside_forecast_window': (
        DATA_BLOCKER, 'a forecast covering the market\'s local date'),
    'forecast_extreme_missing_for_date': (
        DATA_BLOCKER, 'the forecast daily high/low for that local date'),
    'forecast_hour_missing_for_bias': (
        DATA_BLOCKER, 'an hourly forecast point near the observation - '
                      'without it the station-minus-grid bias is unmeasurable '
                      'and the model would price the grid cell, which is the '
                      'consumer anchor this strategy claims retail is wrong '
                      'to use'),
    # The running observed extreme is a HARD FLOOR on the resolution value, not
    # an optional prior. Missing it is missing an input, and pricing without it
    # is the documented Madrid failure.
    'daily_extreme_history_unavailable': (
        DATA_BLOCKER, 'the station\'s observations for the elapsed part of the '
                      'local day, which bound the daily extreme from below'),
    # These two are about the OBSERVATION WINDOW, not the settlement stamp, and
    # they are GENUINE for the same reason `market_past_resolution_time` and
    # `resolution_too_far_out` are: every input was present and a condition on
    # them came out false. Note they cannot be merged with those two - Madrid's
    # endDate sits at 14:00 local on the very afternoon its market is about, so
    # the two clocks disagree by hours in both directions.
    'observation_window_closed': (GENUINE, ''),
    'observation_window_too_far_out': (GENUINE, ''),
    # THE SAME SHAPE AS `strike_inside_proxy_noise_floor`, and classified the
    # same way for the same reason. The rung is narrower than our sigma, so the
    # model's SIDE is decided by the bucket width before any temperature is
    # read: it can never prefer Yes, and would take No on nine of the eleven
    # rungs of every ladder, every cycle. Measured 2026-08-18, the Madrid 36C
    # rung returned 0.238 against a ceiling of 0.239 and booked a 0.43 "edge".
    #
    # DATA_BLOCKER, not GENUINE, and the distinction matters more here than
    # almost anywhere: GENUINE would report "the strategy looked and found no
    # edge" about a rung it has no resolution for. The missing input is a
    # FITTED sigma - the two constants are estimates and the calibration harness
    # does not exist. Convention 11: the strategy has NOT been tested on these
    # rungs, and an empty population here must never read as a silent strategy.
    # STILL A DATA BLOCKER AFTER THE HARNESS LANDED, AND THE REASON CHANGED.
    # `backtest/measure_daily_extreme_calibration.py` now exists and the sigma
    # IS fitted, per station, against 537 station-days of realised METAR daily
    # extremes. The fitted number came back at 2.74F RMSE at the 24-48h lead
    # against a house estimate of 2.96F - i.e. the estimate was right and the
    # rungs are still narrower than the model can resolve. A 1.8F Celsius bucket
    # needs sigma under 1.334F to reach a 0.5 ceiling and one station of 49
    # reaches it. So the missing input is no longer "a fitted sigma", it is a
    # SMALLER one, which is a different (and much harder) thing to go and get.
    'rung_narrower_than_model_resolution': (
        DATA_BLOCKER, 'a sigma narrow enough to resolve this rung - fitted '
                      '2026-08-18 at 2.74F RMSE per station-day at the 24-48h '
                      'lead, against the 1.334F a 1.8F Celsius bucket needs; a '
                      'narrower predictor, not a narrower threshold'),
    # The same axis, one notch further along, and its own counter because the
    # DISTANCE to tradeable is different: this rung's ceiling clears 0.5 but not
    # the 0.55 entry conviction floor. Pooling it into the line above would hide
    # how close the board is to the line - measured 2026-08-18, exactly one
    # station of 49 sits in this band.
    'rung_cannot_reach_entry_conviction_on_yes': (
        DATA_BLOCKER, 'a sigma narrow enough for the Yes side of this rung to '
                      'reach the 0.55 entry floor; the rung clears the 0.5 '
                      'resolution gate but its side is still decided by the '
                      'bucket width rather than by the temperature'),
    # NOT the same fact as the line above, and this is exactly the convention 20
    # split that would otherwise be lost: that one has a measured sigma and it
    # is too wide, this one has NO measured sigma at all. Convention 11 - the
    # strategy could not run at this station, it did not run and decline.
    'daily_extreme_sigma_unfitted_for_station': (
        DATA_BLOCKER, 'a fitted forecast-error sigma for this station in '
                      'research/weather_sigma_calibration.json (re-run '
                      'backtest/measure_daily_extreme_calibration.py; the '
                      'station universe is discovered from the live board and '
                      'a newly listed city will be absent until it is)'),
    # GENUINE: the model ran, priced the rung, and declined its own side. A
    # conviction refusal, deliberately not pooled with `edge_below_min`, which
    # is a PRICE refusal.
    'model_confidence_below_entry_floor': (GENUINE, ''),

    # --- corridor pair / collector -----------------------------------------
    'no_15m_window_open': (DATA_BLOCKER, 'the 15m window open price'),
    'invalid_15m_window_open': (DATA_BLOCKER, 'a usable 15m window open'),
    'not_final_third_of_15m': (GENUINE, ''),
    'ask_5m_above_cap': (GENUINE, ''),
    'ask_15m_above_cap': (GENUINE, ''),
    'edge_below_floor': (GENUINE, ''),

    # --- temporal arbitrage -------------------------------------------------
    'insufficient_leg1_depth': (DATA_BLOCKER, 'book depth on leg 1'),
    'insufficient_leg2_depth': (DATA_BLOCKER, 'book depth on leg 2'),
    'too_late_for_leg1': (GENUINE, ''),
    'leg1_ask_above_cap': (GENUINE, ''),
    'leg2_ask_above_cap': (GENUINE, ''),
    'leg1_effective_ask_above_cap': (GENUINE, ''),
    'leg2_effective_ask_above_cap': (GENUINE, ''),
    'leg1_unfillable_at_cap': (GENUINE, ''),
    'leg2_unfillable_at_cap': (GENUINE, ''),
    'leg2_deadline_passed_unpaired': (GENUINE, ''),
    'no_leg2_budget': (GENUINE, ''),

    # --- fair value arb INVERSE ---------------------------------------------
    # `inverse_side_*` names the side it would have taken, so these do not
    # collide with the parent's identically-shaped reasons and the two
    # strategies stay in separate populations.
    'inverse_side_no_orderbook': (DATA_BLOCKER, 'CLOB orderbook (inverse side)'),
    'inverse_side_no_ask': (DATA_BLOCKER, 'ask side of the book (inverse side)'),
    'inverse_side_unresolvable': (DATA_BLOCKER, 'a resolvable inverse side'),
    'inverse_side_insufficient_book_depth': (DATA_BLOCKER,
                                             'book depth (inverse side)'),
    'inverse_side_unpriceable_cap': (DATA_BLOCKER, 'a priceable entry cap'),
    'inverse_side_unpriceable_fill': (DATA_BLOCKER, 'a priceable fill'),
    'inverse_entry_above_profit_target_ceiling': (GENUINE, ''),
    'inverse_side_effective_ask_above_cap': (GENUINE, ''),
    'inverse_side_unfillable_at_cap': (GENUINE, ''),
    'inverse_side_unsizable_at_notional_cap': (GENUINE, ''),

    # --- grid hedge (a MAKER structure; landed 2026-08-18) ------------------
    # This module states in its own source that `implied_vol_below_realized` is
    # "the one reason in this file that is neither a cannot-run nor a refusal".
    # Taken at its word: everything else here is a blocker, and its entry path
    # ends in `maker_fill_not_simulated` (already SIM_LIMIT above), so a shadow
    # log full of grid_hedge skips is NOT_TESTED almost by construction.
    'both_books_unavailable': (DATA_BLOCKER, 'both CLOB orderbooks'),
    # Half a grid is a directional ladder: the self-hedge this structure needs
    # is the OTHER side filling on the reversal. Not the same fact as missing
    # both, and kept as its own reason for that reason.
    'one_book_unavailable': (DATA_BLOCKER, 'one of the two CLOB orderbooks'),
    # "A spread we cannot measure is not a narrow spread."
    'spread_undefined_no_bid': (DATA_BLOCKER, 'a bid on both sides'),
    'book_too_thin_for_grid': (DATA_BLOCKER,
                               'book depth (min_grid_depth_shares)'),
    # "The budget could not buy a grid, which is a cannot-run, not a market
    # view" - no rung survived the price floor and the minimum size.
    'grid_budget_exhausted': (DATA_BLOCKER,
                              'enough budget for at least one rung'),
    # Two vol legs, two owners, two fixes - never pooled into one number.
    'vol_inputs_unavailable': (DATA_BLOCKER, 'atr14 (the REALISED leg)'),
    'implied_vol_inputs_unavailable': (DATA_BLOCKER,
                                       'lead_bps (the IMPLIED leg)'),
    # The spread eats the rung interval before the grid does any work. A
    # measured book condition, so a result - but see the module note above.
    'spread_too_wide_for_grid': (GENUINE, ''),
    'implied_vol_below_realized': (GENUINE, ''),

    # === added 2026-08-18 (second pass) =====================================
    # REASONS THE AST TEST CANNOT SEE.
    #
    # `test_every_skip_reason_the_strategies_emit_is_classified` walks the AST
    # for `decide('SKIP', <string literal>)`. EIGHT call sites in the package
    # pass a VARIABLE instead, so the reasons behind them were invisible to the
    # test and the suite was green BY ACCIDENT over 16 unclassified strings.
    # Enumerated by hand from the function that produces each variable. See
    # docs/handoffs/2026-08-18-skip-classification-blind-spot.md, which asks
    # for a D-number on whether to close the hole in the test itself.
    #
    # NOTE ON EVIDENCE (convention 15): at the time of writing, db/trading.db
    # carries 41,530 skips and ZERO of them are any of the strings in this
    # block. This is PRE-EMPTIVE - it is not correcting a miscount that has
    # already happened. When these strategies do start logging against a live
    # feed, the rows land classified instead of silently in UNKNOWN.

    # --- grid_hedge.py:757, `decide('SKIP', implied_status)` ---------------
    # `implied_sigma_bps()` returns exactly three statuses: 'ok' (no skip) and
    # these two. Both are cannot-computes, and that module's own docstring
    # keeps them apart deliberately: "different facts and get different
    # strings". At the money Phi^-1(p) ~ 0 and EVERY sigma prices a coin flip,
    # so there is no implied vol to compare against realised. The comparison
    # was never made; it is not a comparison that came out false.
    'implied_vol_undefined_at_the_money': (
        DATA_BLOCKER, 'a lead far enough from the money for Phi^-1(p) to be '
                      'invertible (at the money every sigma prices a coin '
                      'flip, so the equation carries no information about '
                      'sigma at all)'),
    # A negative sigma is not a low volatility reading. It means the book and
    # the strike proxy disagree about which side is ahead, which is a fact
    # about our two INPUTS and must never be pooled with a measured vol.
    'implied_vol_sign_inconsistent': (
        DATA_BLOCKER, 'a book and a strike proxy that agree on which side is '
                      'ahead (they disagreed, so the algebra returned a '
                      'negative sigma - a data-quality fact, not a vol)'),

    # --- near_liq_trigger.py:976 and :981 ----------------------------------
    # These two ARE literals and the AST test did see them; they were simply
    # missing from this table, and were the only genuine full-suite failure
    # when this pass started. Both are RESULTS. They are deliberately two
    # reasons rather than one: the module's docstring says "the tape was
    # silent" and "the tape printed $900 and we wanted $5,000" demand
    # different responses. Both are reached only AFTER `window.ok` is True -
    # the liquidation recorder was alive, fresh, and had enough history - so
    # the feed WAS observed. The not-observed cases are the four
    # `liquidation_*` blockers immediately below and never share a counter
    # with these (convention 20).
    # TWO Raven instruction files landed three minutes apart asking for
    # OPPOSITE classifications on these two keys. Both are still on disk:
    #
    #   06:40  docs/handoffs/from-raven/
    #            2026-08-18-mechanical-fixes-from-review.md
    #          asked DATA_BLOCKER, on the rationale "the feed table has 0
    #          rows".
    #   06:43  docs/handoffs/from-raven/
    #            2026-08-18-classify-new-second-lock-reasons.md
    #          asked GENUINE, on the rationale that near_liq_trigger.py's own
    #          comments say "RAN" at both sites.
    #
    # GENUINE is what stands, and D-298 ruled it. Two sessions reached it
    # independently before the ruling, and the code-path argument below is
    # why: the 06:40 rationale describes a DIFFERENT key. With 0 rows
    # `window.ok` is False and the strategy emits `liquidation_feed_empty`,
    # so neither of these two is even reachable, and "cannot evaluate,
    # feed is empty" is already carried by that key. Classifying these as
    # DATA_BLOCKER too would put one cause under two names (convention 20).
    #
    # Do not read the 06:40 file as the live instruction because it is the
    # one you happened to open. Settled by a ruling, not an open question.
    # Measured consequence as of 2026-08-18: ZERO rows either way
    # (`select count(*) from liquidations` == 0).
    'no_recent_liquidation': (GENUINE, ''),
    # A floor WE chose. Only this one moves when we change our mind, which is
    # exactly why it is not pooled with the silent-tape case above.
    'liquidation_below_second_lock_min': (GENUINE, ''),

    # --- `decide('SKIP', <feed>.reason)`: the LIQUIDATION recorder ---------
    # liq_cascade_chaser.py:323, small_liq_continuation.py:298 and
    # near_liq_trigger.py:964 all forward `liquidation_feed.NO_DATA_REASONS`
    # verbatim rather than re-spelling them (convention 20: one cause, one
    # name, across modules). All four mean the recorder was missing, empty,
    # short or stale. near_liq_trigger's docstring labels all four NOT_TESTED.
    'liquidation_table_missing': (DATA_BLOCKER,
                                  'the `liquidations` table (db absent, or '
                                  'present with the recorder never having '
                                  'written to it)'),
    # Scoped to `symbol_like`: a table full of ETH rows is, for a BTC
    # strategy, exactly as unusable as an empty one.
    'liquidation_feed_empty': (DATA_BLOCKER,
                               'any liquidation row matching this symbol'),
    # A 30s span cannot answer a 120s question. Measuring the seconds it does
    # have would look like a quiet tape and actually be a short one.
    'liquidation_history_too_short': (DATA_BLOCKER,
                                      'enough liquidation history to cover '
                                      'the lookback window'),
    'liquidation_feed_stale': (DATA_BLOCKER,
                               'a live liquidation recorder (newest row '
                               'older than stale_after_sec)'),

    # --- near_liq_trigger.py:859, `decide('SKIP', feed.status)` ------------
    # The HYPERLIQUID whale poller. A dead whale poller and a dead liquidation
    # recorder are two different outages on two different processes and never
    # share a counter, which is why these six are separate from the four
    # above. All six are NOT_TESTED per that module's own docstring; its
    # seventh state, `no_liq_cluster_near_spot`, is the only result and is
    # already classified GENUINE further up.
    'hyperliquid_db_missing': (DATA_BLOCKER, 'db/trading.db itself'),
    'hyperliquid_db_unreadable': (DATA_BLOCKER,
                                  'a database sqlite will open'),
    'hyperliquid_table_missing': (DATA_BLOCKER,
                                  'the `hyperliquid_positions` table - the '
                                  'client has never run against this db'),
    'hyperliquid_feed_empty': (DATA_BLOCKER,
                               'any row in `hyperliquid_positions`'),
    # The age in seconds rides in `detail`, NOT in the reason string, so this
    # stays one exact key rather than becoming a prefix family.
    'hyperliquid_feed_stale': (DATA_BLOCKER,
                               'a live whale poller (newest row older than '
                               'FEED_MAX_AGE_SEC)'),
    'hyperliquid_single_snapshot_only': (
        DATA_BLOCKER, 'a second distinct snapshot ts - one poll is not proof '
                      'the poller is cycling'),

    # --- spread_harvest_maker.py:288, an IfExp over two literals -----------
    # `_underdog()` could not name an underdog. Which of the two strings comes
    # out is decided inline in the call, so neither was ever a visible literal.
    'no_cushion_data': (DATA_BLOCKER,
                        'spot, strike and atr14 for the cushion/ATR gate, '
                        'with the book-implied fallback switched off'),
    # SPLIT 2026-08-18. `no_underdog` pooled two causes; the strategy now emits
    # one of the two names below instead. Convention 20: two drop causes never
    # share one number.
    'no_book_midpoint': (DATA_BLOCKER,
                         'two midpoints - a one-sided book, or an absent bid '
                         'on either leg, leaves no midpoint to compare, so no '
                         'underdog could be named'),
    # The book WAS observed and both mids were present. The market is genuinely
    # tied at the midpoint, which is a condition that was evaluated and found
    # true, not an input that was missing.
    'book_implied_exact_tie': (GENUINE, ''),
    # RETIRED, kept for historical rows. The strategy no longer emits this;
    # rows logged before the split above still carry it and would otherwise
    # fall through to UNKNOWN. Its old pooled reading stands for those rows.
    'no_underdog': (DATA_BLOCKER,
                    'two midpoints that differ (a one-sided book has no '
                    'midpoint). HISTORICAL: rows before 2026-08-18 pool the '
                    'missing-midpoint and exact-tie causes under this one '
                    'name; see no_book_midpoint / book_implied_exact_tie'),

    # --- status quo collector (proposal 028) --------------------------------
    # The classifier (status_quo_classifier.py) is rule-based and deterministic
    # over real inputs (question text, resolution_date when present). Both
    # classifier outcomes below are the SAME shape as `not_a_temperature_market`
    # and `resolution_station_unknown`: the strategy has no instrument for a
    # market of this shape, so it never evaluated a real STATUS_QUO entry
    # condition on it. GENUINE here would report "looked and declined" about a
    # market this strategy was never built to trade.
    'classifier_change_event_shape': (
        DATA_BLOCKER, 'a STATUS_QUO-shaped question - the classifier read this '
                      'one as CHANGE_EVENT, a product this strategy has no '
                      'instrument for'),
    'classifier_unknown_shape': (
        DATA_BLOCKER, "a confidently classifiable shape - the classifier's "
                      'honest default when no rule matches with confidence; '
                      'same fact as resolution_station_unknown, one layer up'),
    # Same wrong-product logic: the strategy only prices binary continuity
    # contracts, and a non-binary market was never a candidate for its entry
    # condition.
    'not_binary': (DATA_BLOCKER, 'a binary contract'),
    'no_resolution_date': (DATA_BLOCKER, "the market's own end_date field "
                                         '(rule (c): a date found only in the '
                                         'question text does not satisfy '
                                         'entry)'),
    'no_market_slug': (DATA_BLOCKER, 'a market identity to track rung state '
                                     'against'),
    # The book and best_ask were both present and real; the price just did not
    # land in [min_no_price, max_no_price]. Same shape as mid_outside_quote_band.
    'price_outside_entry_band': (GENUINE, ''),
    # Both ladder reasons are computed from a real, present best_ask against
    # real, tracked rung state - same shape as already_entered_this_window and
    # pair_complete: a condition on live state, evaluated and found false.
    'ladder_rung_not_yet_reached': (GENUINE, ''),
    'ladder_fully_filled': (GENUINE, ''),
}

#: Reasons NO STRATEGY CAN EMIT ANY MORE, kept because rows logged before the
#: change still carry them. Without this list they would fall through to
#: UNKNOWN and a historical row would stop being classifiable at all.
#:
#: D-290's reverse check reads this. Every OTHER key in the table above must be
#: reachable from a `decide('SKIP', ...)` somewhere, so the table cannot
#: silently accumulate entries for code that no longer exists - and a name on
#: this list that a strategy STARTS emitting again is also red, because a live
#: reason filed under "historical" is a worse lie than an unclassified one.
#:
#: Same shape as `forge.RETIRED_REFUSAL_CATEGORIES`: retired is reported, never
#: deleted (convention 20).
RETIRED_SKIP_REASONS: Tuple[str, ...] = (
    # Split 2026-08-18 into `no_book_midpoint` + `book_implied_exact_tie`.
    'no_underdog',
    # Retired 2026-08-18 when the maker fill model was WIRED into
    # `engine/polymarket/shadow_loop.py`. It was the QUOTE short-circuit: one
    # bucket for eight distinct causes, and ~220k CSV rows of it. The rows are
    # real and stay classifiable; nothing emits it any more.
    'maker_quote_not_simulable',
)

#: Reasons the loop emits with a VARIABLE tail - the blocking gate's own
#: message, verbatim, numbers and market slug included ("risk_gate:
#: daily_loss_breaker: realized loss today =$30.08 > limit=$30.00"). They can
#: never be dict keys: every distinct number would be its own unclassified
#: reason, which is exactly how 20-odd of them ended up in UNKNOWN one row at a
#: time. Matched by PREFIX, and only for prefixes actually observed in the data.
SKIP_PREFIX_CLASSIFICATION: Tuple[Tuple[str, str, str], ...] = (
    ('risk_gate:', SIM_LIMIT, 'the risk gate blocked an entry the strategy '
                              'had already decided on'),
    ('adapter:', SIM_LIMIT, 'the paper adapter refused a decided entry'),
    # A SEPARATE prefix from `adapter:` on purpose. The taker path is refused
    # for taker reasons (`over_notional_cap`, `partial_below_min_shares`) and
    # the maker path for maker ones (`maker_would_cross_book`, `no_orderbook`
    # at rest time). Pooling them would hide which path is being refused, and
    # they have different fixes.
    ('maker_adapter:', SIM_LIMIT,
     'the paper adapter refused to REST a decided quote'),
)


def classify_skip_reason(reason: Optional[str]) -> Tuple[str, str]:
    """Return (class, missing_input) for one skip reason.

    An unrecognised reason returns UNKNOWN, never a guess. A guess here would
    silently move a NOT_TESTED strategy into the "ran and found nothing" pile,
    which is the exact error convention 11 exists to prevent.
    """
    if reason is None or reason == '':
        return UNKNOWN, 'skip with no reason recorded'
    hit = SKIP_CLASSIFICATION.get(reason)
    if hit is not None:
        return hit
    # `fair_value_*` is a family emitted with a suffix. Match the prefix
    # rather than guessing, and only for prefixes we have actually seen.
    if reason.startswith('fair_value_'):
        return GENUINE, ''
    # Gates that embed their own numbers in the reason string. Checked AFTER
    # the exact table so a specific entry always wins over a prefix.
    for prefix, cls, missing in SKIP_PREFIX_CLASSIFICATION:
        if reason.startswith(prefix):
            return cls, missing
    return UNKNOWN, f'reason {reason!r} is not in SKIP_CLASSIFICATION'


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class ShadowUnreadable(Exception):
    """The evidence could not be read. Not the same as there being none."""


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open the DB read-only. The shadow loop may be writing to it right now."""
    if not os.path.exists(path):
        raise ShadowUnreadable(f'no such database: {path}')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Force a real read so a corrupt or locked file fails HERE, with a
        # message, rather than three functions later as an empty result set.
        conn.execute('select count(*) from sqlite_master').fetchone()
        return conn
    except sqlite3.Error as exc:
        raise ShadowUnreadable(f'{type(exc).__name__}: {exc}') from exc


def _table_names(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")]


def _require_tables(conn: sqlite3.Connection, needed: Tuple[str, ...]) -> None:
    present = set(_table_names(conn))
    missing = [t for t in needed if t not in present]
    if missing:
        raise ShadowUnreadable(
            'schema is missing table(s): ' + ','.join(missing))


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float or None. Convention 19: nothing non-finite
    leaves this module, because json.dump(allow_nan=False) downstream would
    raise on it and the caller would lose the whole record over one NaN."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def read_decisions(conn: sqlite3.Connection,
                   mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every signal/decision row. `acted=1` is an entry, `acted=0` is a skip."""
    _require_tables(conn, ('signals',))
    sql = ('select ts, pair, tf, strategy_id, pattern, direction, confidence, '
           'acted, skip_reason, mode from signals')
    params: Tuple[Any, ...] = ()
    if mode:
        sql += ' where mode = ?'
        params = (mode,)
    return [dict(r) for r in conn.execute(sql, params)]


def read_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    _require_tables(conn, ('positions',))
    return [dict(r) for r in conn.execute('select * from positions')]


def read_equity(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    _require_tables(conn, ('equity_snapshots',))
    return [dict(r) for r in conn.execute(
        'select ts, equity, cash, open_risk, mode from equity_snapshots '
        'order by ts')]


def read_paper_log(path: str) -> Dict[str, Any]:
    """The CSV the shadow loop writes alongside the DB.

    It carries columns the `signals` table does not: `resolution`, `won`,
    `pnl_usdc`, `position_id`. Those are the only place a RESOLVED binary shows
    up, so the CSV is not redundant with the DB even though the decision rows
    overlap. A missing CSV is reported, not swallowed.
    """
    if not os.path.exists(path):
        return {'status': 'absent', 'path': path, 'error': 'no such file'}
    try:
        with open(path, newline='') as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        return {'status': 'unreadable', 'path': path,
                'error': f'{type(exc).__name__}: {exc}'}

    actions = collections.Counter(r.get('action') or '' for r in rows)
    resolved = [r for r in rows if (r.get('resolution') or '').strip()]
    won = collections.Counter(
        (r.get('won') or '').strip() for r in resolved)
    pnl_values = [_finite(r.get('pnl_usdc')) for r in rows]
    pnl_values = [v for v in pnl_values if v is not None]
    return {
        'status': 'ok',
        'path': os.path.relpath(path, ROOT),
        'n_rows': len(rows),
        'actions': dict(actions),
        'n_resolved': len(resolved),
        'won_counts': dict(won),
        'n_rows_with_pnl': len(pnl_values),
        'pnl_usdc_total': round(sum(pnl_values), 6) if pnl_values else 0.0,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarise_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Closed and open positions, per strategy. Wins, losses, PnL."""
    closed = [p for p in positions if p.get('closed_ts') is not None]
    open_ = [p for p in positions if p.get('closed_ts') is None]

    by_strategy: Dict[str, Dict[str, Any]] = {}
    wins = losses = flats = unknown_pnl = 0
    total_net = 0.0
    for pos in closed:
        sid = str(pos.get('strategy_id'))
        agg = by_strategy.setdefault(sid, {
            'n_closed': 0, 'wins': 0, 'losses': 0, 'flats': 0,
            'pnl_net_total': 0.0, 'n_pnl_missing': 0,
        })
        agg['n_closed'] += 1
        pnl = _finite(pos.get('pnl_net'))
        if pnl is None:
            pnl = _finite(pos.get('pnl_gross'))
        if pnl is None:
            # A closed position with no PnL is not a flat trade. It is an
            # unreadable one (convention 11), so it gets its own counter.
            agg['n_pnl_missing'] += 1
            unknown_pnl += 1
            continue
        agg['pnl_net_total'] = round(agg['pnl_net_total'] + pnl, 8)
        total_net = round(total_net + pnl, 8)
        if pnl > 0:
            agg['wins'] += 1
            wins += 1
        elif pnl < 0:
            agg['losses'] += 1
            losses += 1
        else:
            agg['flats'] += 1
            flats += 1

    assert wins + losses + flats + unknown_pnl == len(closed), (
        'position accounting identity broken: '
        f'{wins}+{losses}+{flats}+{unknown_pnl} != {len(closed)} closed')

    return {
        'n_positions': len(positions),
        'n_closed': len(closed),
        'n_open': len(open_),
        'wins': wins,
        'losses': losses,
        'flats': flats,
        'n_closed_with_unreadable_pnl': unknown_pnl,
        'pnl_net_total': total_net,
        'by_strategy': by_strategy,
    }


def summarise_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-strategy firing behaviour and skip-reason breakdown.

    Convention 20 is the whole design of this function. Every row lands in
    exactly one of entries/skips/malformed, every skip lands in exactly one
    class, and both identities are asserted rather than assumed.
    """
    per: Dict[str, Dict[str, Any]] = {}
    entries = skips = malformed = 0
    unknown_reasons: Dict[str, int] = collections.Counter()

    for row in decisions:
        sid = str(row.get('strategy_id'))
        agg = per.setdefault(sid, {
            'n_evaluations': 0,
            'n_entries': 0,
            'n_skips': 0,
            'n_malformed': 0,
            'skip_reasons': collections.Counter(),
            'skip_classes': collections.Counter(),
            'first_ts': None,
            'last_ts': None,
            'markets': set(),
        })
        agg['n_evaluations'] += 1
        ts = row.get('ts')
        if isinstance(ts, (int, float)) and math.isfinite(float(ts)):
            agg['first_ts'] = ts if agg['first_ts'] is None \
                else min(agg['first_ts'], ts)
            agg['last_ts'] = ts if agg['last_ts'] is None \
                else max(agg['last_ts'], ts)
        if row.get('pair'):
            agg['markets'].add(str(row['pair']))

        acted = row.get('acted')
        if acted == 1:
            agg['n_entries'] += 1
            entries += 1
        elif acted == 0:
            agg['n_skips'] += 1
            skips += 1
            reason = row.get('skip_reason')
            key = reason if reason else '<null_skip_reason>'
            agg['skip_reasons'][key] += 1
            cls, _ = classify_skip_reason(reason)
            agg['skip_classes'][cls] += 1
            if cls == UNKNOWN:
                unknown_reasons[key] += 1
        else:
            # Neither acted nor skipped. Convention 20: this is a bucket, not
            # a `continue`.
            agg['n_malformed'] += 1
            malformed += 1

    assert entries + skips + malformed == len(decisions), (
        'decision accounting identity broken: '
        f'{entries}+{skips}+{malformed} != {len(decisions)} rows')

    out: Dict[str, Dict[str, Any]] = {}
    for sid, agg in per.items():
        assert sum(agg['skip_classes'].values()) == agg['n_skips'], (
            f'skip-class identity broken for {sid}: '
            f"{sum(agg['skip_classes'].values())} != {agg['n_skips']}")
        blocked = (agg['skip_classes'].get(DATA_BLOCKER, 0)
                   + agg['skip_classes'].get(SIM_LIMIT, 0))
        blocked_fraction = (blocked / agg['n_skips']) if agg['n_skips'] else 0.0
        dominant = agg['skip_reasons'].most_common(1)
        out[sid] = {
            'n_evaluations': agg['n_evaluations'],
            'n_entries': agg['n_entries'],
            'n_skips': agg['n_skips'],
            'n_malformed': agg['n_malformed'],
            'entry_rate': round(agg['n_entries'] / agg['n_evaluations'], 6)
                          if agg['n_evaluations'] else 0.0,
            'skip_reasons': dict(agg['skip_reasons']),
            'skip_classes': dict(agg['skip_classes']),
            'blocked_fraction': round(blocked_fraction, 6),
            'dominant_skip_reason': dominant[0][0] if dominant else None,
            'dominant_skip_count': dominant[0][1] if dominant else 0,
            'first_ts': agg['first_ts'],
            'last_ts': agg['last_ts'],
            'n_markets': len(agg['markets']),
        }

    return {
        'n_rows': len(decisions),
        'n_entries': entries,
        'n_skips': skips,
        'n_malformed': malformed,
        'n_strategies': len(out),
        'by_strategy': out,
        'unknown_skip_reasons': dict(unknown_reasons),
    }


def summarise_equity(snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overall paper performance from the equity curve."""
    if not snapshots:
        return {'n_snapshots': 0, 'status': 'no_snapshots'}
    eq = [(s['ts'], _finite(s.get('equity'))) for s in snapshots]
    eq = [(ts, v) for ts, v in eq if v is not None]
    if not eq:
        return {'n_snapshots': len(snapshots),
                'status': 'no_finite_equity_values'}
    first_ts, first = eq[0]
    last_ts, last = eq[-1]
    peak = eq[0][1]
    max_dd = 0.0
    for _, v in eq:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return {
        'status': 'ok',
        'n_snapshots': len(eq),
        'first_ts': first_ts,
        'last_ts': last_ts,
        'equity_first': first,
        'equity_last': last,
        'equity_peak': peak,
        'return_pct': round(((last - first) / first) * 100.0, 6)
                      if first else None,
        'max_drawdown_pct': round(max_dd * 100.0, 6),
        'open_risk_last': _finite(snapshots[-1].get('open_risk')),
    }


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------

def derive_gaps(decision_summary: Dict[str, Any],
                position_summary: Dict[str, Any]) -> Dict[str, Any]:
    """The part Forge proposes against.

    Splits the never-fired strategies into NOT_TESTED (an input was missing, so
    the strategy never got to decide) and RAN_NO_ENTRY (every input present,
    condition false). Only the second is a measurement, and even then a thin
    one; convention 7 cuts both ways.
    """
    not_tested: List[Dict[str, Any]] = []
    ran_no_entry: List[Dict[str, Any]] = []
    fired: List[Dict[str, Any]] = []
    underpowered: List[Dict[str, Any]] = []

    for sid, rec in sorted(decision_summary.get('by_strategy', {}).items()):
        row = {
            'strategy': sid,
            'n_evaluations': rec['n_evaluations'],
            'n_entries': rec['n_entries'],
            'blocked_fraction': rec['blocked_fraction'],
            'dominant_skip_reason': rec['dominant_skip_reason'],
            'dominant_skip_count': rec['dominant_skip_count'],
            'skip_classes': rec['skip_classes'],
        }
        if rec['n_entries'] > 0:
            fired.append(row)
            continue
        if rec['n_evaluations'] < MIN_EVALUATIONS_FOR_GAP:
            # Too few looks to call anything. Not a gap, not a verdict.
            row['verdict'] = 'UNDERPOWERED'
            row['note'] = (f"{rec['n_evaluations']} evaluations is under the "
                           f'{MIN_EVALUATIONS_FOR_GAP} floor')
            underpowered.append(row)
            continue
        if rec['blocked_fraction'] >= DATA_BLOCKED_FRACTION:
            reason = rec['dominant_skip_reason']
            _, missing = classify_skip_reason(reason)
            row['verdict'] = 'NOT_TESTED'
            row['missing_input'] = missing
            row['note'] = (
                f"{rec['blocked_fraction']:.1%} of skips were data-blocked. "
                'This strategy did not look and decline; it never got to '
                'look. Convention 11.')
            not_tested.append(row)
        else:
            row['verdict'] = 'RAN_NO_ENTRY'
            row['note'] = (
                'inputs were present and the entry condition was false on '
                f"{rec['n_skips'] if 'n_skips' in rec else rec['n_evaluations']}"
                ' evaluations. A measurement, and a thin one.')
            ran_no_entry.append(row)

    # Dominant skip reasons across the whole session, with their class.
    reason_totals: collections.Counter = collections.Counter()
    for rec in decision_summary.get('by_strategy', {}).values():
        for reason, n in rec['skip_reasons'].items():
            reason_totals[reason] += n
    dominant = []
    for reason, n in reason_totals.most_common():
        cls, missing = classify_skip_reason(
            None if reason == '<null_skip_reason>' else reason)
        dominant.append({
            'reason': reason,
            'count': n,
            'class': cls,
            'missing_input': missing,
            'share_of_skips': round(n / decision_summary['n_skips'], 6)
                              if decision_summary.get('n_skips') else 0.0,
        })

    total = decision_summary.get('n_strategies', 0)
    bucketed = (len(not_tested) + len(ran_no_entry) + len(fired)
                + len(underpowered))
    assert bucketed == total, (
        f'gap accounting identity broken: {bucketed} bucketed != '
        f'{total} strategies')

    return {
        'strategies_not_tested': not_tested,
        'strategies_ran_no_entry': ran_no_entry,
        'strategies_fired': fired,
        'strategies_underpowered': underpowered,
        'dominant_skip_reasons': dominant,
        'unknown_skip_reasons': decision_summary.get(
            'unknown_skip_reasons', {}),
        'n_closed_positions': position_summary.get('n_closed', 0),
        'zero_entry_session': decision_summary.get('n_entries', 0) == 0,
        'min_evaluations_for_gap': MIN_EVALUATIONS_FOR_GAP,
        'data_blocked_fraction_threshold': DATA_BLOCKED_FRACTION,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate(db_path: str = DEFAULT_DB,
             paper_log_path: str = DEFAULT_PAPER_LOG,
             mode: Optional[str] = None) -> Dict[str, Any]:
    """Full shadow evaluation.

    Returns a dict with `status` of 'ok' or 'unreadable'. NEVER an empty 'ok':
    an unreadable DB is not an empty one (convention 11), and a caller that
    read a silent `{}` as "no strategy fired" would be recording a verdict the
    evidence does not support.
    """
    try:
        conn = _connect_ro(db_path)
    except ShadowUnreadable as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': str(exc),
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }

    try:
        decisions = read_decisions(conn, mode=mode)
        positions = read_positions(conn)
        equity = read_equity(conn)
        tables = sorted(_table_names(conn))
    except (ShadowUnreadable, sqlite3.Error) as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': f'{type(exc).__name__}: {exc}',
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }
    finally:
        conn.close()

    decision_summary = summarise_decisions(decisions)
    position_summary = summarise_positions(positions)
    equity_summary = summarise_equity(equity)
    paper_log = read_paper_log(paper_log_path)

    return {
        'status': 'ok',
        'db_path': os.path.relpath(db_path, ROOT)
                   if db_path.startswith(ROOT) else db_path,
        'mode_filter': mode,
        'tables': tables,
        'decisions': decision_summary,
        'positions': position_summary,
        'equity': equity_summary,
        'paper_log': paper_log,
        'gaps': derive_gaps(decision_summary, position_summary),
    }


# ---------------------------------------------------------------------------
# Turning the evaluation into Forge candidates
# ---------------------------------------------------------------------------

def shadow_candidates(evaluation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build proposal candidates from what the shadow loop actually showed.

    One `repair` per NOT_TESTED strategy: the input it needs is missing, which
    is a fixable engineering gap and not an edge question. `expected_edge_bps`
    is null for all of them, because the edge of a strategy that has never
    evaluated its own condition is not knowable (convention 11).

    Nothing is generated for a RAN_NO_ENTRY strategy: "the condition was false
    297 times" is a measurement Forge should report, not act on, and acting on
    it would be discovering a condition by scanning (convention 4).
    """
    if evaluation.get('status') != 'ok':
        return []
    gaps = evaluation.get('gaps', {})
    out: List[Dict[str, Any]] = []

    for row in gaps.get('strategies_not_tested', []):
        sid = row['strategy']
        slug = sid.lower().lstrip('_')
        if slug.startswith('pm_'):
            slug = slug[3:]
        reason = row.get('dominant_skip_reason')
        missing = row.get('missing_input') or 'an unnamed input'
        n_eval = row['n_evaluations']
        out.append({
            'name': f'shadow_unblock_{slug}',
            'kind': 'repair',
            'asset_class': 'PREDICTION_MARKET',
            'source': ('agents/forge_shadow_eval.py over db/trading.db '
                       '(measured, live shadow session)'),
            'thesis': (
                f'{sid} has never evaluated its own entry condition in the '
                f'shadow loop: it skipped {row["dominant_skip_count"]} of '
                f'{n_eval} evaluations on {reason!r}, which is a missing '
                f'input ({missing}) rather than a false condition. Supplying '
                'that input is what turns this strategy from NOT_TESTED into '
                'testable.'),
            'expected_edge_bps': None,
            'kill_condition': (
                f'After {missing} is supplied, if {sid} still enters on fewer '
                f'than 1% of evaluations over 500 or more shadow cycles as '
                'measured by agents/forge_shadow_eval.py against db/trading.db, '
                'and scores no better than 0 net cents per share over 200 or '
                'more resolved positions in backtest/polymarket_harness.py, it '
                'is retired rather than repaired a second time.'),
            'entry_exit_rules': (
                'Unchanged. This is a repair to the CONTEXT the strategy is '
                f'handed, not to its logic: {missing} must be present and '
                'correct before any entry rule of this strategy has been '
                'exercised even once.'),
            'data_requirements': (
                f'BLOCKER: {missing}. Measured over {n_eval} live shadow '
                f'evaluations, {row["blocked_fraction"]:.1%} of skips were '
                'data-blocked. Until that input exists this strategy is '
                'NOT_TESTED (convention 11) and must not be reported as having '
                'looked and declined.'),
            'related_graveyard_findings': (
                'None. PREDICTION_MARKET has no graveyard rows at all, so this '
                'proposal rests on live shadow measurement rather than on a '
                'buried family. D-268: every Polymarket strategy is NOT_TESTED '
                'until backtest/polymarket_harness.py scores it.'),
            'body': _render_repair_body(row, evaluation),
        })
    return out


def _render_repair_body(row: Dict[str, Any],
                        evaluation: Dict[str, Any]) -> str:
    sid = row['strategy']
    classes = row.get('skip_classes', {})
    lines = [
        '## What was measured',
        '',
        f'Source: `db/trading.db` `signals`, read by '
        f'`agents/forge_shadow_eval.py`. Session covers '
        f"{evaluation['decisions']['n_rows']} decision rows across "
        f"{evaluation['decisions']['n_strategies']} strategies.",
        '',
        f'`{sid}`:',
        '',
        '| Bucket | Count |',
        '|---|---|',
        f"| evaluations | {row['n_evaluations']} |",
        f"| entries | {row['n_entries']} |",
    ]
    for cls in (DATA_BLOCKER, SIM_LIMIT, GENUINE, UNKNOWN):
        if classes.get(cls):
            lines.append(f'| skips classed {cls} | {classes[cls]} |')
    lines += [
        '',
        f"Dominant skip reason: `{row['dominant_skip_reason']}` "
        f"({row['dominant_skip_count']} of {row['n_evaluations']}).",
        '',
        '## Why this is NOT_TESTED and not a failure',
        '',
        'A skip that names a missing input is not the strategy declining. It '
        'is the strategy never being asked. Convention 11 says NOT_TESTED '
        'means "could not run", never "ran and found nothing", and reporting '
        'this strategy as having produced zero entries without that label '
        'would put a verdict in the record that the evidence does not carry.',
        '',
        '## The honest limit',
        '',
        'This says nothing about whether the strategy has an edge. It says '
        'the question has not been asked yet. The edge estimate is null on '
        'purpose: inventing a bps figure here would be a fabricated number, '
        'and fabricated numbers get cited.',
    ]
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--paper-log', default=DEFAULT_PAPER_LOG)
    parser.add_argument('--mode', default=None,
                        help="filter signals by mode, e.g. 'paper'")
    args = parser.parse_args(argv)
    result = evaluate(args.db, args.paper_log, mode=args.mode)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
