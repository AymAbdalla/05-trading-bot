"""Tests for `agents/critic.py`.

Every test builds its own throwaway SQLite database. Nothing here touches
`db/trading.db`: the Polymarket shadow loop writes that file continuously, and a
test that reads it would be non-deterministic even before it was dangerous.

The bulk of these tests are aimed at ONE property, because it is the property
that makes the critic worth having: the classifier must decline to answer when
the data cannot answer. So for each mode there is a test that it fires on a
constructed case AND a test that it goes to `unclassified`, with the right
reason, when the evidence it depends on is absent. A classifier that always
returns a plausible label would pass the first half of that pairing and fail the
second, which is exactly the failure this file is built to catch.
"""
import json
import os
import sqlite3
import uuid

import pytest

from agents import critic
from agents import hypothesis_graph as hg


# --------------------------------------------------------------------------
# Fixture plumbing
# --------------------------------------------------------------------------

_POSITIONS_SQL = """
CREATE TABLE positions (
    id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    signal_id TEXT,
    opened_ts INTEGER NOT NULL,
    closed_ts INTEGER,
    entry_px REAL NOT NULL,
    exit_px REAL,
    qty REAL NOT NULL,
    stop_px REAL NOT NULL,
    target_px REAL NOT NULL,
    pnl_gross REAL,
    pnl_net REAL,
    fees REAL DEFAULT 0,
    r_multiple REAL,
    exit_reason TEXT,
    mode TEXT NOT NULL DEFAULT 'paper'
)
"""

_SIGNALS_SQL = """
CREATE TABLE signals (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    pair TEXT NOT NULL,
    tf TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    pattern TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    features_json TEXT NOT NULL,
    acted INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    mode TEXT NOT NULL DEFAULT 'paper'
)
"""

#: A plausible epoch-MILLISECONDS base, matching the real tape's magnitude.
T0 = 1787020000000


class Tape(object):
    """A throwaway trading database that a test can add trades to."""

    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute(_POSITIONS_SQL)
        self.conn.execute(_SIGNALS_SQL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def signal(self, strategy, pair, ts, features, acted=1, skip_reason=None,
               signal_id=None):
        signal_id = signal_id or str(uuid.uuid4())
        self.conn.execute(
            'INSERT INTO signals (id, ts, pair, tf, strategy_id, pattern, '
            'direction, confidence, features_json, acted, skip_reason, mode) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (signal_id, int(ts), pair, '5m', strategy, strategy, 'long', 0.0,
             json.dumps(features, allow_nan=False), int(acted), skip_reason,
             'paper'))
        self.conn.commit()
        return signal_id

    def trade(self, strategy, pair='btc-updown-5m-1787020000', *,
              entry_px=0.30, exit_px=0.20, qty=20.0, opened_ts=T0,
              closed_ts=None, exit_reason='sell:price_stop', features=None,
              with_signal=True, position_id=None):
        """Add one closed position, with its entry signal by default.

        P&L is DERIVED from the prices rather than passed in, so a fixture
        cannot accidentally describe a trade whose price move and P&L disagree
        (which `is_long` would then, correctly, refuse to classify).
        """
        closed_ts = closed_ts if closed_ts is not None else opened_ts + 60_000
        pnl = round((exit_px - entry_px) * qty, 10)
        signal_id = None
        if with_signal:
            signal_id = self.signal(strategy, pair, opened_ts, features or {})
        position_id = position_id or str(uuid.uuid4())
        self.conn.execute(
            'INSERT INTO positions (id, pair, strategy_id, signal_id, '
            'opened_ts, closed_ts, entry_px, exit_px, qty, stop_px, target_px, '
            'pnl_gross, pnl_net, fees, r_multiple, exit_reason, mode) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (position_id, pair, strategy, signal_id, int(opened_ts),
             int(closed_ts), entry_px, exit_px, qty, 0.0, 1.0, pnl, pnl, 0.0,
             0.0, exit_reason, 'paper'))
        self.conn.commit()
        return position_id


@pytest.fixture
def tape(tmp_path):
    tape = Tape(str(tmp_path / 'throwaway.db'))
    yield tape
    tape.close()


def review(tape, since=0, until=T0 + 10_000_000):
    return critic.classify_window(since, until, db_path=tape.path)


def only(result):
    """The single classification in a one-loser review."""
    assert len(result['classifications']) == 1, (
        'fixture produced %d classifications, expected 1'
        % len(result['classifications']))
    return result['classifications'][0]


def bulk(tape, strategy, n, *, wins=0, entry_px=0.30, exit_px=0.20,
         features=None, pair_prefix='btc-updown-5m-',
         loss_exit_reason='sell:price_stop'):
    """`n` closed trades for one strategy, `wins` of them profitable.

    Used to get a strategy over MIN_STRATEGY_SAMPLE so the strategy-level
    classifiers are allowed to fire at all.
    """
    for i in range(n):
        winner = i < wins
        tape.trade(
            strategy,
            pair='%s%d' % (pair_prefix, 1787020000 + i),
            entry_px=entry_px,
            exit_px=(entry_px + 0.10) if winner else exit_px,
            opened_ts=T0 + i * 1000,
            exit_reason='sell:profit_target' if winner else loss_exit_reason,
            features=dict(features or {}))


# --------------------------------------------------------------------------
# Vocabulary and wiring
# --------------------------------------------------------------------------

def test_every_mode_the_critic_emits_can_be_stored_by_the_graph():
    """A mode the graph rejects would be silently lost on the way to disk."""
    for mode in critic.CRITIC_MODES:
        assert mode in hg.FAILURE_MODES, mode


def test_the_six_task_modes_plus_unclassified_are_all_present():
    for mode in ('spread_eats_edge', 'model_miscalibrated', 'stop_too_tight',
                 'never_fires', 'regime_mismatch', 'entry_signal_wrong',
                 critic.UNCLASSIFIED):
        assert mode in critic.CRITIC_MODES


def test_unclassified_is_a_real_bucket_not_a_remainder(tape):
    """It appears in `by_mode` with a count even when it is zero."""
    tape.trade('S', features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    result = review(tape)
    assert critic.UNCLASSIFIED in result['summary']['by_mode']


# --------------------------------------------------------------------------
# spread_eats_edge
# --------------------------------------------------------------------------

def test_spread_eats_edge_fires_when_the_exit_beat_the_entry_mid(tape):
    """Bought the 0.30 ask against a 0.10 bid, sold at the 0.20 mid: the mid
    never moved, so the half-spread is the entire loss."""
    tape.trade('S', entry_px=0.30, exit_px=0.20,
               features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    detail = only(review(tape))
    assert detail.mode == 'spread_eats_edge'
    assert detail.level == 'trade'
    assert '0.1000' in detail.why or '0.2000' in detail.why


def test_spread_eats_edge_does_not_fire_when_the_mid_moved_against_us(tape):
    """Same book, but the exit is below the entry mid: the market moved, and
    the spread is no longer a complete explanation."""
    tape.trade('S', entry_px=0.30, exit_px=0.15,
               features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    detail = only(review(tape))
    assert detail.mode != 'spread_eats_edge'


def test_spread_eats_edge_cannot_run_without_a_recorded_spread(tape):
    """A best_ask with no recorded spread means the test is not eligible, not
    that it failed."""
    tape.trade('S', entry_px=0.30, exit_px=0.20, features={'best_ask': 0.30})
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED


def test_spread_eats_edge_ignores_a_best_bid_from_the_complement_token(tape):
    """The bug this pins is real and was in this file's first version.

    `PM_fair_value_arb_inverse` records the COMPLEMENT token's bid: best_ask
    0.67, best_bid 0.33, spread 0.01, and the two quotes sum to 1.00 because
    they are opposite sides of a binary, not two sides of one book. Averaging
    them gives 0.50, a price of nothing, and every inverse exit above 0.50 then
    looked like the spread had eaten a real edge. The mid must come from the
    ask and the recorded spread.
    """
    tape.trade('S', entry_px=0.67, exit_px=0.55,
               features={'best_ask': 0.67, 'best_bid': 0.33, 'spread': 0.01})
    detail = only(review(tape))
    assert detail.mode != 'spread_eats_edge'


def test_spread_eats_edge_uses_the_recorded_spread_for_the_mid(tape):
    """Same complement-style book, but the exit really is above the true mid of
    0.665, so the test fires and says which quote it trusted."""
    tape.trade('S', entry_px=0.67, exit_px=0.666,
               features={'best_ask': 0.67, 'best_bid': 0.33, 'spread': 0.01})
    detail = only(review(tape))
    assert detail.mode == 'spread_eats_edge'
    assert 'does not belong to this book' in detail.why


def test_spread_eats_edge_says_when_the_bid_corroborates(tape):
    tape.trade('S', entry_px=0.30, exit_px=0.26,
               features={'best_ask': 0.30, 'best_bid': 0.20, 'spread': 0.10})
    detail = only(review(tape))
    assert detail.mode == 'spread_eats_edge'
    assert 'corroborates' in detail.why


def test_spread_eats_edge_refuses_a_non_positive_spread(tape):
    tape.trade('S', entry_px=0.30, exit_px=0.29,
               features={'best_ask': 0.30, 'best_bid': 0.30, 'spread': 0.0})
    detail = only(review(tape))
    assert detail.mode != 'spread_eats_edge'


# --------------------------------------------------------------------------
# stop_too_tight
# --------------------------------------------------------------------------

def test_stop_too_tight_fires_when_a_later_quote_gave_the_loss_back(tape):
    """The stop realised -0.10, then the same market and side quoted a bid back
    above the entry. That is a real post-exit observation of the very token."""
    pair = 'btc-updown-5m-1787020000'
    tape.trade('S', pair=pair, entry_px=0.30, exit_px=0.20,
               exit_reason='sell:price_stop', opened_ts=T0,
               closed_ts=T0 + 60_000,
               features={'outcome_side': 'Up'})
    tape.signal('Other', pair, T0 + 120_000,
                {'outcome_side': 'Up', 'best_bid': 0.35}, acted=0,
                skip_reason='max_trades_this_window')
    detail = only(review(tape))
    assert detail.mode == 'stop_too_tight'
    assert '0.3500' in detail.why


def test_stop_too_tight_is_unclassified_when_no_post_exit_quote_exists(tape):
    """The decisive test: every loser in the real tape exited on a stop, so a
    classifier that inferred `stop_too_tight` from the exit reason alone would
    label all of them. Without an observation this must refuse."""
    tape.trade('S', entry_px=0.30, exit_px=0.20,
               exit_reason='sell:price_stop',
               features={'outcome_side': 'Up'})
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED
    assert detail.unclassified_reason == 'no_post_exit_price_observation'
    assert 'NOT_TESTED' in detail.why


def test_stop_too_tight_does_not_fire_when_the_later_quote_stayed_below(tape):
    """An observation that says the market did NOT come back is a real answer,
    and it is a different fact from having no observation."""
    pair = 'btc-updown-5m-1787020000'
    tape.trade('S', pair=pair, entry_px=0.30, exit_px=0.20,
               exit_reason='sell:price_stop', closed_ts=T0 + 60_000,
               features={'outcome_side': 'Up'})
    tape.signal('Other', pair, T0 + 120_000,
                {'outcome_side': 'Up', 'best_bid': 0.12}, acted=0)
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED
    assert detail.unclassified_reason == 'strategy_sample_below_min'


def test_stop_too_tight_ignores_quotes_for_the_other_outcome_side(tape):
    """`btc-updown` has two tokens. A bid on Down says nothing about Up."""
    pair = 'btc-updown-5m-1787020000'
    tape.trade('S', pair=pair, entry_px=0.30, exit_px=0.20,
               exit_reason='sell:price_stop', closed_ts=T0 + 60_000,
               features={'outcome_side': 'Up'})
    tape.signal('Other', pair, T0 + 120_000,
                {'outcome_side': 'Down', 'best_bid': 0.95}, acted=0)
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED


def test_stop_too_tight_ignores_quotes_recorded_before_the_exit(tape):
    """A quote from before the stop is not evidence about what happened after
    it."""
    pair = 'btc-updown-5m-1787020000'
    tape.signal('Other', pair, T0 + 10_000,
                {'outcome_side': 'Up', 'best_bid': 0.95}, acted=0)
    tape.trade('S', pair=pair, entry_px=0.30, exit_px=0.20,
               exit_reason='sell:price_stop', opened_ts=T0,
               closed_ts=T0 + 60_000, features={'outcome_side': 'Up'})
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED
    assert detail.unclassified_reason == 'no_post_exit_price_observation'


def test_stop_too_tight_does_not_consider_a_non_stop_exit(tape):
    """A trade that closed on a target did not have its stop hit, so the
    question does not arise."""
    pair = 'btc-updown-5m-1787020000'
    tape.trade('S', pair=pair, entry_px=0.30, exit_px=0.20,
               exit_reason='sell:mean_reverted', closed_ts=T0 + 60_000,
               features={'outcome_side': 'Up'})
    tape.signal('Other', pair, T0 + 120_000,
                {'outcome_side': 'Up', 'best_bid': 0.95}, acted=0)
    detail = only(review(tape))
    assert detail.mode != 'stop_too_tight'


# --------------------------------------------------------------------------
# model_miscalibrated
# --------------------------------------------------------------------------

def test_model_miscalibrated_fires_on_an_adverse_mean_move_with_a_model_price(tape):
    bulk(tape, 'FV', critic.MIN_STRATEGY_SAMPLE, wins=5,
         features={'side_fair_value': 0.62})
    result = review(tape)
    modes = result['summary']['by_mode']
    assert modes['model_miscalibrated'] > 0
    detail = next(d for d in result['classifications']
                  if d.mode == 'model_miscalibrated')
    assert detail.level == 'strategy'
    assert 'exits before resolution' in detail.why


def test_model_miscalibrated_needs_an_explicit_model_price(tape):
    """Same trades, same adverse move, no published model price: the mode is
    not testable and must not be asserted."""
    bulk(tape, 'NoModel', critic.MIN_STRATEGY_SAMPLE, wins=5, features={})
    result = review(tape)
    assert result['summary']['by_mode']['model_miscalibrated'] == 0


def test_model_miscalibrated_needs_the_minimum_sample(tape):
    """One trade short of the floor is a shrug, not a verdict (Convention 7).

    The losses close on a non-stop exit so that the stop test does not apply
    and the sample floor is the first thing in the ladder that blocks.
    """
    bulk(tape, 'FV', critic.MIN_STRATEGY_SAMPLE - 1,
         features={'side_fair_value': 0.62},
         loss_exit_reason='sell:mean_reverted')
    result = review(tape)
    assert result['summary']['by_mode']['model_miscalibrated'] == 0
    assert (result['summary']['unclassified_reasons']['strategy_sample_below_min']
            == critic.MIN_STRATEGY_SAMPLE - 1)


def test_a_missing_outcome_side_is_its_own_bucket(tape):
    """It is not the same fact as "no later quote exists", so it does not share
    that counter (Convention 20)."""
    tape.trade('S', entry_px=0.30, exit_px=0.15,
               exit_reason='sell:price_stop', features={'side_fair_value': 0.6})
    detail = only(review(tape))
    assert detail.unclassified_reason == 'entry_side_not_recorded'


def test_model_miscalibrated_is_measured_over_winners_too(tape):
    """Scoring the mean move over losers only would make the test a tautology:
    a loser moved against the entry by definition. Here the winners are large
    enough to pull the mean favourable, and the mode must not fire."""
    n = critic.MIN_STRATEGY_SAMPLE
    for i in range(n):
        winner = i % 2 == 0
        tape.trade('FV', pair='p%d' % i, entry_px=0.30,
                   exit_px=0.90 if winner else 0.28,
                   opened_ts=T0 + i * 1000,
                   exit_reason='sell:profit_target' if winner
                               else 'sell:price_stop',
                   features={'side_fair_value': 0.62})
    result = review(tape)
    assert result['strategy_stats']['FV']['mean_favourable_move'] > 0
    assert result['summary']['by_mode']['model_miscalibrated'] == 0


# --------------------------------------------------------------------------
# entry_signal_wrong
# --------------------------------------------------------------------------

def test_entry_signal_wrong_fires_at_or_below_the_coin_flip(tape):
    bulk(tape, 'Dir', critic.MIN_STRATEGY_SAMPLE, wins=5, features={})
    result = review(tape)
    assert result['summary']['by_mode']['entry_signal_wrong'] > 0
    detail = next(d for d in result['classifications']
                  if d.mode == 'entry_signal_wrong')
    assert 'not a profitability verdict' in detail.why


def test_entry_signal_wrong_does_not_fire_above_the_coin_flip(tape):
    """A strategy winning 60% still loses money here, and that is a different
    diagnosis. This test pins that the classifier says so."""
    n = 40
    bulk(tape, 'Dir', n, wins=24, features={})
    result = review(tape)
    assert result['strategy_stats']['Dir']['win_rate'] == pytest.approx(0.6)
    assert result['summary']['by_mode']['entry_signal_wrong'] == 0


def test_model_miscalibrated_outranks_entry_signal_wrong_and_records_both(tape):
    """Both strategy-level tests can match. The ladder picks one and keeps the
    other in `also_matched` rather than dropping it."""
    bulk(tape, 'FV', critic.MIN_STRATEGY_SAMPLE, wins=5,
         features={'side_fair_value': 0.62})
    detail = next(d for d in review(tape)['classifications']
                  if d.mode == 'model_miscalibrated')
    assert 'entry_signal_wrong' in detail.also_matched


def test_a_per_trade_finding_outranks_a_strategy_finding(tape):
    """The spread test decides from data on the trade itself, so it wins the
    ladder, and the strategy-level match survives in `also_matched`."""
    bulk(tape, 'FV', critic.MIN_STRATEGY_SAMPLE, wins=5,
         features={'side_fair_value': 0.62, 'best_bid': 0.10,
                   'best_ask': 0.30, 'spread': 0.20})
    result = review(tape)
    detail = next(d for d in result['classifications']
                  if d.mode == 'spread_eats_edge')
    assert 'model_miscalibrated' in detail.also_matched


# --------------------------------------------------------------------------
# regime_mismatch: the mode we cannot decide
# --------------------------------------------------------------------------

def test_regime_mismatch_never_fires_and_says_why():
    assert 'regime_mismatch' in critic.NOT_DECIDABLE
    why = critic.NOT_DECIDABLE['regime_mismatch']
    assert 'NOT_TESTED' in why
    assert 'no regime label' in why


def test_regime_mismatch_returns_none_for_every_shape_of_trade():
    """Fed a row carrying every field it could conceivably want, it still
    declines, because the label it needs does not exist anywhere."""
    row = {'id': 'x', 'strategy_id': 'S', 'pair': 'p', 'entry_px': 0.3,
           'exit_px': 0.2, 'pnl_net': -2.0, 'market_regime': 'bull',
           'regime': 'high_vol'}
    context = {'strategy_stats': {}, 'entry_signals': {}, 'post_exit_bids': {}}
    assert critic.check_regime_mismatch(row, context) is None


def test_the_review_carries_the_not_decidable_notice(tape):
    """A reader must not infer "regime is fine" from an absent count."""
    tape.trade('S')
    result = review(tape)
    assert 'regime_mismatch' in result['summary']['not_decidable']
    assert 'regime_mismatch' in critic.build_evidence(result)


# --------------------------------------------------------------------------
# never_fires
# --------------------------------------------------------------------------

def test_never_fires_finds_a_strategy_that_only_ever_skipped(tape):
    for i in range(5):
        tape.signal('PM_weather_arb', 'btc-updown-5m-1', T0 + i * 1000, {},
                    acted=0, skip_reason='resolution_station_unknown')
    result = review(tape)
    assert len(result['never_fires']) == 1
    item = result['never_fires'][0]
    assert item['strategy'] == 'PM_weather_arb'
    assert item['status'] == 'NOT_TESTED'
    assert item['top_skip_reasons'][0] == ('resolution_station_unknown', 5)


def test_a_strategy_that_traded_is_never_never_fires(tape):
    """A closed trade proves the strategy fired."""
    tape.trade('S')
    assert review(tape)['never_fires'] == []


def test_never_fires_is_not_a_kill_candidate(tape):
    """Convention 11: NOT_TESTED must not be mined as evidence against the
    idea, so it cannot clear a kill bar however many times it appears."""
    for i in range(50):
        tape.signal('PM_weather_arb', 'btc-updown-5m-1', T0 + i * 1000, {},
                    acted=0, skip_reason='resolution_station_unknown')
    result = review(tape)
    assert result['never_fires']
    assert critic.kill_recommendations(result) == []


def test_never_fires_is_not_written_to_the_hypothesis_graph(tape):
    """A real trade is present too, so the table definitely gets created and
    the absence of a never_fires row is a real absence, not a missing table."""
    for i in range(50):
        tape.signal('PM_weather_arb', 'btc-updown-5m-1', T0 + i * 1000, {},
                    acted=0, skip_reason='resolution_station_unknown')
    tape.trade('S', entry_px=0.30, exit_px=0.20,
               features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    result = review(tape)
    out = critic.update_hypothesis_graph(result, db_path=tape.path)
    assert out['never_fires_not_written'] == ['PM_weather_arb']
    assert out['inserted'] >= 1
    conn = sqlite3.connect(tape.path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM hypothesis_graph WHERE failure_mode="
            "'never_fires'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM hypothesis_graph WHERE strategy_name="
            "'PM_weather_arb'").fetchone()[0] == 0
    finally:
        conn.close()


def test_skip_reason_counts_sum_back_to_the_skipped_total(tape):
    """Convention 20 inside the never_fires evidence itself."""
    for i in range(4):
        tape.signal('X', 'p', T0 + i * 1000, {}, acted=0, skip_reason='a')
    for i in range(3):
        tape.signal('X', 'p', T0 + 100 + i * 1000, {}, acted=0,
                    skip_reason='b')
    tape.signal('X', 'p', T0 + 9000, {}, acted=0, skip_reason=None)
    item = review(tape)['never_fires'][0]
    assert item['skipped'] == 8
    assert sum(n for _, n in item['top_skip_reasons']) == 8


# --------------------------------------------------------------------------
# Convention 20: the accounting identities
# --------------------------------------------------------------------------

def test_every_closed_trade_is_counted_exactly_once(tape):
    bulk(tape, 'A', 10, wins=4, features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    bulk(tape, 'B', 35, wins=5, features={'side_fair_value': 0.62})
    summary = review(tape)['summary']
    assert summary['closed'] == 45
    assert summary['winners'] + summary['losers'] + summary['flat'] == 45


def test_the_mode_counts_sum_back_to_the_losers(tape):
    bulk(tape, 'A', 10, wins=4, features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    bulk(tape, 'B', 35, wins=5, features={'side_fair_value': 0.62})
    summary = review(tape)['summary']
    assert sum(summary['by_mode'].values()) == summary['losers']


def test_the_unclassified_reasons_sum_back_to_the_unclassified_count(tape):
    bulk(tape, 'A', 10, wins=4, features={})
    bulk(tape, 'B', 35, wins=5, features={'side_fair_value': 0.62})
    summary = review(tape)['summary']
    assert (sum(summary['unclassified_reasons'].values())
            == summary['by_mode'][critic.UNCLASSIFIED])


def test_two_drop_causes_never_share_one_counter(tape):
    """A missing book and a missing post-exit quote are different problems with
    different fixes, so they must land in different buckets."""
    tape.trade('Small', entry_px=0.30, exit_px=0.15,
               features={'outcome_side': 'Up'}, opened_ts=T0)
    tape.trade('Small', entry_px=0.30, exit_px=0.15,
               exit_reason='sell:mean_reverted', opened_ts=T0 + 5000,
               features={})
    reasons = review(tape)['summary']['unclassified_reasons']
    assert reasons['no_post_exit_price_observation'] == 1
    assert reasons['strategy_sample_below_min'] == 1


def test_a_broken_identity_raises_rather_than_printing_a_wrong_number():
    bad = {'closed': 10, 'winners': 3, 'losers': 5, 'flat': 0,
           'by_mode': {'unclassified': 5}, 'unclassified_reasons': {}}
    with pytest.raises(AssertionError):
        critic._assert_accounting(bad)


def test_a_mode_count_that_does_not_sum_raises():
    bad = {'closed': 8, 'winners': 3, 'losers': 5, 'flat': 0,
           'by_mode': {'unclassified': 4},
           'unclassified_reasons': {'strategy_sample_below_min': 4}}
    with pytest.raises(AssertionError):
        critic._assert_accounting(bad)


def test_an_unclassified_reason_outside_the_vocabulary_is_refused():
    row = {'id': 'x', 'strategy_id': 'S'}
    with pytest.raises(AssertionError):
        critic.Classification(row, critic.UNCLASSIFIED, 0.0, 'why', 'none',
                              unclassified_reason='something_new')


# --------------------------------------------------------------------------
# Direction derivation
# --------------------------------------------------------------------------

def test_direction_is_derived_from_the_data_not_assumed():
    assert critic.is_long({'entry_px': 0.3, 'exit_px': 0.2,
                           'pnl_gross': -2.0}) is True
    assert critic.is_long({'entry_px': 0.3, 'exit_px': 0.4,
                           'pnl_gross': -2.0}) is False


def test_a_trade_whose_price_and_pnl_disagree_is_not_price_tested(tape):
    """Sign disagreement means one of the two records is wrong, and no
    price-direction test may run on it."""
    signal_id = tape.signal('S', 'p', T0, {'outcome_side': 'Up',
                                           'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    tape.conn.execute(
        'INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, '
        'closed_ts, entry_px, exit_px, qty, stop_px, target_px, pnl_gross, '
        'pnl_net, fees, r_multiple, exit_reason, mode) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('weird', 'p', 'S', signal_id, T0, T0 + 1000, 0.30, 0.30, 20.0, 0.0,
         1.0, -2.0, -2.0, 0.0, 0.0, 'sell:price_stop', 'paper'))
    tape.conn.commit()
    detail = only(review(tape))
    assert detail.mode == critic.UNCLASSIFIED
    assert detail.unclassified_reason == 'position_direction_not_derivable'


def test_a_position_with_no_entry_signal_row_is_counted_as_such(tape):
    tape.trade('S', with_signal=False)
    detail = only(review(tape))
    assert detail.unclassified_reason == 'entry_signal_row_missing'


# --------------------------------------------------------------------------
# Windowing
# --------------------------------------------------------------------------

def test_the_window_selects_on_closed_ts_not_opened_ts(tape):
    """The critic reviews outcomes, so a trade that opened before the window
    but closed inside it belongs to this window."""
    tape.trade('S', opened_ts=T0 - 500_000, closed_ts=T0 + 1000)
    assert review(tape, since=T0, until=T0 + 10_000)['summary']['closed'] == 1


def test_the_window_upper_bound_is_exclusive(tape):
    tape.trade('S', opened_ts=T0, closed_ts=T0 + 5000)
    assert review(tape, since=T0, until=T0 + 5000)['summary']['closed'] == 0
    assert review(tape, since=T0, until=T0 + 5001)['summary']['closed'] == 1


def test_an_open_position_is_not_reviewed(tape):
    tape.conn.execute(
        'INSERT INTO positions (id, pair, strategy_id, signal_id, opened_ts, '
        'closed_ts, entry_px, exit_px, qty, stop_px, target_px, pnl_gross, '
        'pnl_net, fees, r_multiple, exit_reason, mode) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('open1', 'p', 'S', None, T0, None, 0.30, None, 20.0, 0.0, 1.0,
         None, None, 0.0, None, None, 'paper'))
    tape.conn.commit()
    assert review(tape)['summary']['closed'] == 0


def test_a_backwards_window_is_refused(tape):
    with pytest.raises(ValueError):
        critic.classify_window(T0 + 1000, T0, db_path=tape.path)


# --------------------------------------------------------------------------
# Read-only guarantee
# --------------------------------------------------------------------------

def test_classify_window_does_not_modify_the_trading_tables(tape):
    bulk(tape, 'A', 12, wins=3, features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    before = _digest(tape.path)
    review(tape)
    assert _digest(tape.path) == before


def _digest(path):
    conn = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    try:
        return (
            conn.execute('SELECT COUNT(*), TOTAL(pnl_net) FROM positions'
                         ).fetchone(),
            conn.execute('SELECT COUNT(*), TOTAL(acted) FROM signals'
                         ).fetchone(),
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Hypothesis graph writes
# --------------------------------------------------------------------------

def test_update_hypothesis_graph_writes_one_row_per_strategy_and_mode(tape):
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    result = review(tape)
    out = critic.update_hypothesis_graph(result, db_path=tape.path)
    assert out['inserted'] >= 1
    conn = hg.connect(tape.path)
    try:
        rows = hg.all_hypotheses(conn, strategy_name='FV')
    finally:
        conn.close()
    assert rows
    assert all(r.source == critic.SOURCE_CRITIC for r in rows)
    assert all(r.status == hg.FAILED_STATUS for r in rows)


def test_running_the_critic_twice_does_not_duplicate_rows(tape):
    """Idempotency. The upsert identity plus a deterministic hypothesis text
    means a second pass over the same window reports `unchanged`."""
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    result = review(tape)
    first = critic.update_hypothesis_graph(result, db_path=tape.path)
    second = critic.update_hypothesis_graph(result, db_path=tape.path)
    assert first['inserted'] == len(first['rows'])
    assert second['inserted'] == 0
    assert second['unchanged'] == len(second['rows'])
    conn = sqlite3.connect(tape.path)
    try:
        total = conn.execute('SELECT COUNT(*) FROM hypothesis_graph').fetchone()[0]
    finally:
        conn.close()
    assert total == len(first['rows'])


def test_the_evidence_written_to_the_graph_has_no_wall_clock_in_it(tape):
    """If it did, a re-run would report a phantom 'updated' every time."""
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    result = review(tape)
    rows = critic._graph_rows(result)
    for row in rows:
        assert row['evidence']['window_since_ms'] == result['summary']['since_ts']
        assert row['evidence']['window_until_ms'] == result['summary']['until_ts']
        assert 'generated_at' not in row['evidence']


def test_graph_evidence_survives_a_strict_json_dump(tape):
    """Convention 19: `allow_nan=False` must not raise on anything we build."""
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    for row in critic._graph_rows(review(tape)):
        json.dumps(row['evidence'], allow_nan=False)


def test_the_graph_dry_run_writes_nothing_but_reports_everything(tape):
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    result = review(tape)
    out = critic.update_hypothesis_graph(result, db_path=tape.path,
                                         dry_run=True)
    assert out['dry_run'] is True
    assert out['rows']
    assert out['inserted'] == 0
    conn = sqlite3.connect(tape.path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        conn.close()
    assert 'hypothesis_graph' not in tables


def test_unclassified_losses_are_still_recorded_in_the_graph(tape):
    """"We could not name why this lost" is a verdict worth keeping. Hiding it
    would make the graph read as though every loss had an explanation."""
    tape.trade('Small', entry_px=0.30, exit_px=0.15, features={})
    rows = critic._graph_rows(review(tape))
    assert any(r['failure_mode'] == critic.UNCLASSIFIED for r in rows)


# --------------------------------------------------------------------------
# Kill recommendations
# --------------------------------------------------------------------------

def test_three_occurrences_of_one_mode_earns_a_recommendation(tape):
    for i in range(3):
        tape.trade('S', pair='p%d' % i, entry_px=0.30, exit_px=0.20,
                   opened_ts=T0 + i * 1000,
                   features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    recs = critic.kill_recommendations(review(tape))
    assert len(recs) == 1
    assert recs[0]['failure_mode'] == 'spread_eats_edge'
    assert recs[0]['occurrences'] == 3


def test_two_occurrences_does_not(tape):
    for i in range(2):
        tape.trade('S', pair='p%d' % i, entry_px=0.30, exit_px=0.20,
                   opened_ts=T0 + i * 1000,
                   features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    assert critic.kill_recommendations(review(tape)) == []


def test_a_small_sample_recommendation_is_marked_provisional(tape):
    for i in range(3):
        tape.trade('S', pair='p%d' % i, entry_px=0.30, exit_px=0.20,
                   opened_ts=T0 + i * 1000,
                   features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    rec = critic.kill_recommendations(review(tape))[0]
    assert rec['provisional'] is True
    assert rec['closed_trades_in_window'] == 3
    assert 'Convention 7' in rec['verdict_strength']


def test_a_large_sample_recommendation_is_not_provisional(tape):
    bulk(tape, 'S', 40, wins=0, features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    rec = critic.kill_recommendations(review(tape))[0]
    assert rec['provisional'] is False
    assert rec['closed_trades_in_window'] == 40


def test_a_net_positive_strategy_is_withheld_not_recommended(tape):
    """A bad direction call and an unprofitable strategy are different claims.

    This mirrors PM_temporal_arbitrage on the real tape: a 19.5% win rate over
    41 closed trades, which is a correct `entry_signal_wrong`, on a strategy
    that is net POSITIVE because the payoffs are asymmetric. The finding stands;
    the kill does not.
    """
    n = 40
    for i in range(n):
        winner = i < 8
        tape.trade('Pair', pair='p%d' % i, entry_px=0.30,
                   exit_px=0.90 if winner else 0.28,
                   opened_ts=T0 + i * 1000,
                   exit_reason='sell:profit_target' if winner
                               else 'sell:mean_reverted',
                   features={})
    result = review(tape)
    assert result['strategy_stats']['Pair']['pnl_net'] > 0
    assert result['strategy_stats']['Pair']['win_rate'] < 0.5
    recs = critic.kill_recommendations(result)
    assert len(recs) == 1
    assert recs[0]['failure_mode'] == 'entry_signal_wrong'
    assert recs[0]['recommended'] is False
    assert 'net' in recs[0]['withheld_reason']

    text = critic.render_kill_file(result, recs)
    assert 'Withheld' in text
    assert 'Pair' in text


def test_a_losing_strategy_is_actually_recommended(tape):
    bulk(tape, 'S', 40, wins=0,
         features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    rec = critic.kill_recommendations(review(tape))[0]
    assert rec['recommended'] is True
    assert rec['withheld_reason'] is None
    assert rec['pnl_net'] < 0


def test_unclassified_never_earns_a_kill_recommendation(tape):
    """Otherwise the bar could be cleared by ignorance."""
    for i in range(9):
        tape.trade('S', pair='p%d' % i, entry_px=0.30, exit_px=0.15,
                   opened_ts=T0 + i * 1000, features={})
    result = review(tape)
    assert result['summary']['by_mode'][critic.UNCLASSIFIED] == 9
    assert critic.kill_recommendations(result) == []


def test_every_recommendation_states_its_sample_size(tape):
    bulk(tape, 'S', 40, wins=0, features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    text = critic.render_kill_file(review(tape),
                                   critic.kill_recommendations(review(tape)))
    assert 'Strategy closed trades in window' in text
    assert 'Convention 7' in text


def test_the_kill_file_is_named_for_the_window_end(tape, tmp_path):
    tape.trade('S')
    out = critic.write_kill_recommendations(
        review(tape, until=1787062400000), out_dir=str(tmp_path))
    assert os.path.basename(out['path']).endswith(
        '-critic-kill-recommendations.md')
    assert os.path.basename(out['path']).startswith('2026-08-')
    assert out['written'] is True
    assert os.path.exists(out['path'])


def test_the_kill_file_dry_run_writes_no_file(tape, tmp_path):
    out_dir = tmp_path / 'kills'
    tape.trade('S')
    out = critic.write_kill_recommendations(review(tape), out_dir=str(out_dir),
                                            dry_run=True)
    assert out['written'] is False
    assert not out_dir.exists()
    assert out['text']


def test_the_kill_file_names_what_could_not_be_decided(tape):
    tape.trade('S', features={'outcome_side': 'Up'})
    result = review(tape)
    text = critic.render_kill_file(result, critic.kill_recommendations(result))
    assert 'regime_mismatch' in text
    assert 'no_post_exit_price_observation' in text


# --------------------------------------------------------------------------
# The evidence block and the post-mortem
# --------------------------------------------------------------------------

def test_the_evidence_block_carries_the_accounting(tape):
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    text = critic.build_evidence(review(tape))
    assert 'ACCOUNTING' in text
    assert 'FAILURE MODE COUNTS' in text
    assert 'UNCLASSIFIED BREAKDOWN' in text
    assert 'WHAT THIS CLASSIFIER CANNOT DECIDE' in text


def test_the_evidence_block_carries_real_worked_examples(tape):
    tape.trade('S', pair='btc-updown-5m-42', entry_px=0.30, exit_px=0.20,
               features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    text = critic.build_evidence(review(tape))
    assert 'btc-updown-5m-42' in text
    assert 'entry 0.3000 exit 0.2000' in text


def test_the_post_mortem_goes_through_the_vault_writer(tape, tmp_path,
                                                       monkeypatch):
    """`AYM_LLM_DRY_RUN=1` so no model turn is spawned. The canned reply is a
    non-answer, so the writer must fall back and SAY it fell back."""
    monkeypatch.setenv('AYM_LLM_DRY_RUN', '1')
    tape.trade('S', features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    write = critic.write_post_mortem(review(tape), label='unit-test',
                                     out_dir=str(tmp_path), vault_context='')
    assert write.written is True
    assert write.used_model is False
    with open(write.path, 'r', encoding='utf-8') as handle:
        text = handle.read()
    assert 'model: NOT_TESTED' in text
    assert 'reasoning layer did not run' in text


def test_skip_model_still_writes_the_deterministic_note(tape, tmp_path):
    """`skip_model` is vault_writer's dry run: no model turn, note still
    written."""
    tape.trade('S', features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    write = critic.write_post_mortem(review(tape), label='unit-test',
                                     out_dir=str(tmp_path), skip_model=True,
                                     vault_context='')
    assert write.written is True
    assert write.used_model is False
    assert os.path.exists(write.path)


def test_the_post_mortem_dry_run_writes_absolutely_nothing(tape, tmp_path):
    """The critic's `--dry-run` means no file appears. This is not the same as
    vault_writer's `dry_run`, which skips the model and writes anyway; that
    difference once deposited a unit test's synthetic numbers into the real
    vault, which is what this test exists to stop."""
    out_dir = tmp_path / 'notes'
    tape.trade('S', features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    write = critic.write_post_mortem(review(tape), label='unit-test',
                                     out_dir=str(out_dir), dry_run=True,
                                     vault_context='')
    assert write.written is False
    assert not out_dir.exists()
    assert not os.path.exists(write.path)
    assert 'nothing written' in (write.error or '')


def test_the_dry_run_path_matches_where_the_writer_actually_writes(
        tape, tmp_path):
    """Convention 22: the dry run predicts a path, so prove the prediction by
    writing for real and comparing, rather than asserting it in a docstring."""
    tape.trade('S', features={'best_bid': 0.10, 'best_ask': 0.30, 'spread': 0.20})
    result = review(tape)
    predicted = critic.write_post_mortem(
        result, label='unit-test', out_dir=str(tmp_path), dry_run=True,
        vault_context='').path
    actual = critic.write_post_mortem(
        result, label='unit-test', out_dir=str(tmp_path), skip_model=True,
        vault_context='').path
    assert predicted == actual
    assert os.path.exists(actual)


def test_a_dry_run_never_touches_the_real_vault_directory(tape, monkeypatch):
    """No out_dir given, so a leak would land in ~/aym/vault/Trading. Nothing
    may be written and the vault must not even be read."""
    def explode(*_args, **_kwargs):
        raise AssertionError('the vault must not be touched during a dry run')

    monkeypatch.setattr(critic.vault_writer, 'atomic_write', explode)
    monkeypatch.setattr(critic.vault_reader, 'render_context', explode)
    tape.trade('S')
    write = critic.write_post_mortem(review(tape), dry_run=True)
    assert write.written is False
    assert write.path.startswith(critic.vault_writer.CYCLES_DIR)


def test_the_post_mortem_does_not_read_the_real_vault_when_context_is_given(
        tape, tmp_path, monkeypatch):
    """Guards against a test, or a caller, silently pulling ~/aym/vault in."""
    def explode(*_args, **_kwargs):
        raise AssertionError('render_context must not be called here')

    monkeypatch.setattr(critic.vault_reader, 'render_context', explode)
    tape.trade('S')
    critic.write_post_mortem(review(tape), label='x', out_dir=str(tmp_path),
                             dry_run=True, vault_context='(none)')


# --------------------------------------------------------------------------
# --since parsing and the state file
# --------------------------------------------------------------------------

def test_parse_since_reads_durations():
    now = 1_000_000_000_000
    assert critic.parse_since('4h', state={}, now=now)[0] == now - 4 * 3600_000
    assert critic.parse_since('90m', state={}, now=now)[0] == now - 90 * 60_000
    assert critic.parse_since('7d', state={}, now=now)[0] == now - 7 * 86400_000


def test_parse_since_reads_an_iso_date():
    ts, how = critic.parse_since('2026-08-17', state={})
    assert ts == 1786924800000  # 2026-08-17T00:00:00Z
    assert 'explicit' in how


def test_parse_since_reads_an_epoch_ms_value():
    assert critic.parse_since('1787022141000', state={})[0] == 1787022141000


def test_parse_since_last_with_no_bookmark_scans_from_zero():
    ts, how = critic.parse_since('last', state={})
    assert ts == 0
    assert 'no previous review' in how


def test_parse_since_last_reads_the_bookmark():
    ts, how = critic.parse_since('last', state={'last_review_until_ms': 12345})
    assert ts == 12345
    assert 'last review' in how


def test_the_state_file_round_trips(tmp_path):
    path = str(tmp_path / 'critic_state.json')
    critic.save_state(1787062400000, path)
    state = critic.load_state(path)
    assert state['last_review_until_ms'] == 1787062400000
    assert critic.parse_since('last', state=state)[0] == 1787062400000


def test_a_corrupt_state_file_is_reported_not_fatal(tmp_path):
    path = str(tmp_path / 'critic_state.json')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('{not json')
    state = critic.load_state(path)
    assert '_unreadable' in state
    assert critic.parse_since('last', state=state)[0] == 0


def test_a_missing_state_file_is_an_empty_state(tmp_path):
    assert critic.load_state(str(tmp_path / 'nope.json')) == {}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_the_cli_dry_run_writes_nothing_anywhere(tape, tmp_path, monkeypatch,
                                                 capsys):
    """Nothing to the database, nothing to the handoffs directory, nothing to
    the vault, and no bookmark move. `atomic_write` is booby-trapped so any
    write at all fails the test rather than quietly landing somewhere real."""
    monkeypatch.setenv('AYM_LLM_DRY_RUN', '1')
    monkeypatch.setattr(critic, 'KILL_DIR', str(tmp_path / 'kills'))

    def explode(*_args, **_kwargs):
        raise AssertionError('a dry run must not write anything')

    monkeypatch.setattr(critic.vault_writer, 'atomic_write', explode)
    monkeypatch.setattr(critic.vault_reader, 'render_context', explode)
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    state_path = str(tmp_path / 'state.json')

    rc = critic.main(['--since', '0', '--until', str(T0 + 10_000_000),
                      '--db', tape.path, '--dry-run', '--state', state_path])
    assert rc == 0

    out = capsys.readouterr().out
    assert 'CRITIC REVIEW' in out
    assert 'DRY RUN' in out
    assert not os.path.exists(state_path)
    assert not os.path.isdir(str(tmp_path / 'kills'))
    conn = sqlite3.connect(tape.path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        conn.close()
    assert 'hypothesis_graph' not in tables


def test_the_cli_reports_every_mode_count(tape, capsys):
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    critic.main(['--since', '0', '--until', str(T0 + 10_000_000),
                 '--db', tape.path])
    out = capsys.readouterr().out
    for mode in critic.CRITIC_MODES:
        if mode == 'never_fires':
            continue
        assert mode in out
    assert 'NOT DECIDABLE' in out


def test_the_cli_json_output_is_strict_json(tape, capsys):
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})
    critic.main(['--since', '0', '--until', str(T0 + 10_000_000),
                 '--db', tape.path, '--json'])
    out = capsys.readouterr().out
    payload = json.loads(out[out.index('{'):])
    assert payload['summary']['closed'] == 35
    assert sum(payload['summary']['by_mode'].values()) == \
        payload['summary']['losers']


def test_the_cli_moves_the_bookmark_only_on_a_real_run(tape, tmp_path,
                                                       monkeypatch):
    monkeypatch.setenv('AYM_LLM_DRY_RUN', '1')
    monkeypatch.setattr(critic.vault_reader, 'render_context', lambda: '')
    state_path = str(tmp_path / 'state.json')
    bulk(tape, 'FV', 35, wins=5, features={'side_fair_value': 0.62})

    critic.main(['--since', '0', '--until', str(T0 + 5000), '--db', tape.path,
                 '--state', state_path])
    assert not os.path.exists(state_path)

    critic.main(['--since', '0', '--until', str(T0 + 5000), '--db', tape.path,
                 '--state', state_path, '--update-graph'])
    assert critic.load_state(state_path)['last_review_until_ms'] == T0 + 5000
