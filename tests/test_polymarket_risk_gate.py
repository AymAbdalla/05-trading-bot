"""Tests for the Polymarket risk gate (D-267).

No network, no DB except where the crypto ops backstops are explicitly under
test. The gate is a pure function of a portfolio state it is handed, so every
test here is a statement about arithmetic and control flow and nothing else.

What this file is actually guarding. On a binary the premium IS the risk: a
share pays exactly $1.00 or exactly $0.00, so `shares * premium` is
simultaneously the notional and the maximum loss. That makes every cap here a
USDC-of-premium cap, and it makes the failure mode specific: any path that
lets measured exposure come in LOWER than real exposure does not produce a
slightly loose limit, it produces a limit that does not bind. Most of the
tests below are that shape - unreadable positions, a side label in the wrong
case, a fee that is not counted.

Tests are grouped into two kinds, matching
`tests/test_polymarket_paper_adapter.py`:

  * Plain tests assert behaviour the gate gets RIGHT today. They are
    regression locks.

  * `xfail(strict=True)` tests assert the CORRECT behaviour for a defect that
    is still present, with the file:line and the consequence written into the
    reason. `strict=True` means a later fix reports XPASS, which pytest treats
    as a failure, so the fix cannot land without also deleting the marker. A
    green suite therefore never means "no known defects", it means "the known
    defects are exactly the ones listed here".
"""
import math
import os
import sys
import time

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket import risk_gate as rg
from engine.polymarket.risk_gate import (DEFAULT_CORRELATION_GROUPS,
                                         DEFAULT_MARKET_TYPE_PATTERNS,
                                         ExposureSnapshot, OpenExposure,
                                         PolymarketRiskGate, PolymarketVerdict,
                                         aggregate_exposure, classify_market_type,
                                         correlation_key, exposures_from_adapter,
                                         fractional_kelly, normalize_direction,
                                         realized_pnl_today,
                                         realized_pnl_today_by_asset,
                                         asset_bucket, UNKNOWN_ASSET,
                                         DEFAULT_DAILY_LOSS_LIMIT_USDC,
                                         DEFAULT_PORTFOLIO_DAILY_LOSS_LIMIT_USDC,
                                         PORTFOLIO_DAILY_LOSS_LIMIT_MULTIPLE,
                                         utc_midnight_seconds)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Real slug shapes. Classification is substring matching, so a test that uses
# a made-up slug is testing the fallback bucket by accident.
BTC5M = 'btc-updown-5m-2026-08-17-12-00'
BTC15M = 'btc-updown-15m-2026-08-17-12-00'
BTC1H = 'btc-updown-1h-2026-08-17-12-00'
ETH5M = 'eth-updown-5m-2026-08-17-12-00'
EVENT = 'will-the-fed-cut-rates-in-september'


# -- fixtures ---------------------------------------------------------------

def gate(**risk):
    """A gate with module defaults, overridden by keyword.

    Built through the config dict rather than by setting attributes so the
    `config['polymarket']['risk']` read path is exercised by every test.
    """
    return PolymarketRiskGate({'polymarket': {'risk': risk}})


def expo(slug=BTC5M, side='Up', cost=5.0, shares=10.0, market_type=None):
    return OpenExposure(market_slug=slug, outcome_side=side, cost_usdc=cost,
                        shares=shares, market_type=market_type)


class Unreadable:
    """An open position the gate cannot parse.

    Not a contrived case: this is what a partially-written state file, a
    schema change, or a None mark looks like from inside `aggregate_exposure`.
    """

    market_slug = BTC5M
    outcome_side = 'Up'
    cost_usdc = 'not-a-number'


class FakePosition:
    """Minimal stand-in for PaperPosition for the realized-PnL census."""

    def __init__(self, resolution=None, pnl_usdc=None, opened_ts=0,
                 position_id='p1', market_slug=None):
        self.resolution = resolution
        self.pnl_usdc = pnl_usdc
        self.opened_ts = opened_ts
        self.position_id = position_id
        # None on purpose by default: a position with no slug is exactly the
        # case `asset_bucket` has to route somewhere named rather than drop.
        self.market_slug = market_slug


@pytest.fixture
def g():
    return gate()


# -- per-trade premium cap ---------------------------------------------------

class TestPerTradePremiumCap:
    """`notional_cap_usdc` bounds the premium at risk on ONE trade."""

    def test_sizes_to_the_cap_exactly(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50)
        assert v.approved
        assert v.shares == 20                      # $10 / 0.50
        assert v.notional_usdc == pytest.approx(10.0)
        assert v.max_loss_usdc == pytest.approx(10.0)
        assert v.binding_constraint == 'notional_cap'

    def test_never_rounds_up_past_the_cap(self, g):
        """$10 at 30c is 33.33 shares. 34 would be $10.20 of risk."""
        v = g.check_order(BTC5M, 'Up', 0.30)
        assert v.shares == 33
        assert v.notional_usdc <= g.notional_cap_usdc

    def test_one_cent_over_the_cap_is_not_taken(self):
        """A budget of $10.00 at $2.001/share buys 4 shares, not 5.

        The interesting direction is the one that would breach: 5 shares would
        cost $10.005. `min_shares` is 5, so the honest answer is unsizable.
        """
        v = gate(notional_cap_usdc=10.0).check_order(BTC5M, 'Up', 2.001)
        assert not v.approved

    def test_a_cap_below_the_exchange_minimum_is_unsizable_not_a_loss(self):
        """Convention 11 / the D-249 shape: could not run, did not lose."""
        v = gate(notional_cap_usdc=2.0).check_order(BTC5M, 'Up', 0.90)
        assert not v.approved
        assert v.reason.startswith('unsizable_at_cap')
        assert v.shares == 0

    def test_max_loss_and_max_gain_are_exact_on_a_binary(self, g):
        v = g.check_order(BTC5M, 'Up', 0.40)
        assert v.shares == 25
        assert v.max_loss_usdc == pytest.approx(10.0)
        assert v.max_gain_usdc == pytest.approx(15.0)   # 25 * 1.00 - 10.00
        assert v.breakeven_win_rate == pytest.approx(0.40)


class TestPremiumValidity:
    """The tradeable band is the only sanity check a binary can have.

    Convention 8's inverted-stop check cannot be reused: on a binary the stop
    is a genuine 0.00 and can never invert, so it catches nothing here.
    """

    def test_zero_premium_is_refused(self, g):
        v = g.check_order(BTC5M, 'Up', 0.0)
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_negative_premium_is_refused(self, g):
        v = g.check_order(BTC5M, 'Up', -0.25)
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_premium_above_one_dollar_is_refused(self, g):
        """Paying over $1.00 for a $1.00-max payoff is a guaranteed loss."""
        v = g.check_order(BTC5M, 'Up', 1.40)
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_nan_premium_is_refused(self, g):
        v = g.check_order(BTC5M, 'Up', float('nan'))
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_nan_premium_does_not_leak_into_the_verdict(self, g):
        """A NaN echoed into the verdict makes `to_dict` unserialisable.

        Convention 19: `json.dump(allow_nan=False)` raises on it, and any
        non-Python parser rejects the file. The rejection is correct; the
        payload must still be portable.
        """
        v = g.check_order(BTC5M, 'Up', float('nan'))
        assert math.isfinite(v.premium)
        assert all(math.isfinite(x) for x in v.to_dict()['detail'].values())

    def test_non_numeric_premium_returns_a_verdict_not_an_exception(self, g):
        """A mis-parsed book hands the gate a string. That is a BLOCK.

        A risk gate that raises instead of returning is a risk gate the caller
        can skip past with a bare `except`. It must always answer.
        """
        v = g.check_order(BTC5M, 'Up', 'not-a-price')
        assert isinstance(v, PolymarketVerdict)
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_none_premium_returns_a_verdict(self, g):
        v = g.check_order(BTC5M, 'Up', None)
        assert not v.approved
        assert v.reason.startswith('invalid_premium')

    def test_band_edges_are_inclusive(self, g):
        """0.01 and 0.99 are IN the band; the block is outside it."""
        assert g.check_order(BTC5M, 'Up', 0.01).approved
        assert g.check_order(BTC5M, 'Up', 0.99).approved
        assert not g.check_order(BTC5M, 'Up', 0.009).approved
        assert not g.check_order(BTC5M, 'Up', 0.991).approved


# -- position counts ---------------------------------------------------------

class TestMaxConcurrentPositions:

    def test_below_the_limit_is_allowed(self):
        opens = [expo(slug='mkt-{}'.format(i)) for i in range(4)]
        v = gate(max_concurrent_positions=5).check_order(EVENT, 'Yes', 0.50,
                                                         open_positions=opens)
        assert v.approved

    def test_at_the_limit_blocks(self):
        """5 open with a limit of 5 means the SIXTH is refused."""
        opens = [expo(slug='mkt-{}'.format(i)) for i in range(5)]
        v = gate(max_concurrent_positions=5).check_order(EVENT, 'Yes', 0.50,
                                                         open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_concurrent_positions')

    def test_over_the_limit_blocks(self):
        opens = [expo(slug='mkt-{}'.format(i)) for i in range(9)]
        v = gate(max_concurrent_positions=5).check_order(EVENT, 'Yes', 0.50,
                                                         open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_concurrent_positions')

    def test_unreadable_positions_cannot_buy_headroom(self):
        """The defect this whole file exists for, in one test.

        Nine positions the gate cannot parse are nine positions, not zero.
        Dropping them from the count means `max_concurrent_positions` stops
        binding exactly when the portfolio state is least trustworthy
        (convention 11: unreadable is not empty).
        """
        v = gate(max_concurrent_positions=5).check_order(
            EVENT, 'Yes', 0.50, open_positions=[Unreadable()] * 9)
        assert not v.approved

    def test_unreadable_positions_cannot_buy_exposure_headroom(self):
        """Same root cause on the USDC caps rather than the count."""
        v = gate(max_total_exposure_usdc=10.0).check_order(
            EVENT, 'Yes', 0.50, open_positions=[Unreadable()])
        assert not v.approved


class TestPerMarketCounts:

    def test_second_position_on_the_same_side_blocks(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50, open_positions=[expo(side='Up')])
        assert not v.approved
        assert v.reason.startswith('max_positions_per_market_side')

    def test_opposite_side_on_the_same_market_is_allowed(self, g):
        """A deliberate Up+Down hedge stays expressible (max 2 per market)."""
        v = g.check_order(BTC5M, 'Down', 0.50, open_positions=[expo(side='Up')])
        assert v.approved

    def test_third_position_on_the_same_market_blocks(self, g):
        opens = [expo(side='Up'), expo(side='Down')]
        v = g.check_order(BTC5M, 'Sideways', 0.50, open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_positions_per_market')

    def test_side_label_case_does_not_buy_a_second_position(self, g):
        """'up' and 'Up' are the same side of the same market.

        `normalize_direction` exists precisely so the gate never reads two
        spellings as two bets. If the per-side counter keys on the raw string,
        a strategy that lowercases its side label doubles its per-trade cap
        without any limit firing.
        """
        v = g.check_order(BTC5M, 'up', 0.50, open_positions=[expo(side='Up')])
        assert not v.approved
        assert v.reason.startswith('max_positions_per_market_side')

    def test_yes_is_the_same_side_as_up(self, g):
        """Both normalise to 'up'; nothing in one market quotes both labels."""
        v = g.check_order(BTC5M, 'Yes', 0.50, open_positions=[expo(side='Up')])
        assert not v.approved

    def test_slug_case_does_not_buy_a_second_position(self, g):
        """Same defect class, on the market key instead of the side key."""
        v = g.check_order(BTC5M, 'Up',
                          0.50, open_positions=[expo(slug=BTC5M.upper())])
        assert not v.approved


# -- daily loss circuit breaker ---------------------------------------------

class TestDailyLossBreaker:
    """Realized resolution loss since UTC midnight. Open positions are NOT in
    here - they are bounded separately by `max_total_exposure_usdc`."""

    def test_profit_does_not_trip_it(self, g):
        ok, reason = g.check_daily_loss_breaker(+50.0)
        assert ok and reason == 'ok'

    def test_below_the_limit_is_allowed(self, g):
        assert g.check_daily_loss_breaker(-29.99)[0]

    def test_exactly_at_the_limit_is_allowed(self, g):
        """`loss > limit` blocks, so AT the limit trading continues.

        Matches `RiskGate.check_ops_backstops`, which also uses a strict `>`
        on `daily_drop > daily_threshold`. Documented here because "the daily
        loss limit is $30" reads to a human as "$30 stops you". It does not;
        $30.01 does. That is a policy question for Aym, not a bug to flip.
        """
        assert g.check_daily_loss_breaker(-30.0)[0]

    def test_one_cent_over_the_limit_blocks(self, g):
        ok, reason = g.check_daily_loss_breaker(-30.01)
        assert not ok
        assert reason.startswith('daily_loss_breaker')

    def test_non_finite_pnl_blocks(self, g):
        """Unreadable PnL is not zero PnL (convention 11). Fail closed."""
        for bad in (float('nan'), float('inf'), float('-inf')):
            ok, reason = g.check_daily_loss_breaker(bad)
            assert not ok
            assert 'not finite' in reason

    def test_none_pnl_is_treated_as_flat(self, g):
        """A caller with no PnL series is not a caller in drawdown."""
        assert g.check_daily_loss_breaker(None)[0]

    def test_breaker_blocks_the_whole_order(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50, realized_pnl_today_usdc=-100.0)
        assert not v.approved
        assert v.reason.startswith('daily_loss_breaker')

    def test_breaker_runs_before_sizing(self, g):
        """A tripped breaker must not report a premium-band or sizing reason."""
        v = g.check_order(BTC5M, 'Up', 5.00, realized_pnl_today_usdc=-100.0)
        assert v.reason.startswith('daily_loss_breaker')


class TestDailyBoundary:
    """UTC midnight, in SECONDS. `opened_ts` is `int(time.time())`; the crypto
    `orders` table is in ms. Mixing them is a 1000x error that reads as
    "no trades today, ever"."""

    def test_utc_midnight_is_a_whole_number_of_days(self):
        assert utc_midnight_seconds(1755432000.0) % 86400 == 0

    def test_utc_midnight_is_at_or_before_now(self):
        now = time.time()
        assert 0 <= now - utc_midnight_seconds(now) < 86400

    def test_a_loss_from_yesterday_does_not_count_today(self):
        now = 1755432000.0                      # some time on 2026-08-17 UTC
        midnight = utc_midnight_seconds(now)
        pnl, counts = realized_pnl_today(
            [FakePosition('LOSS', -25.0, midnight - 1)], now=now)
        assert pnl == 0.0
        assert counts['skipped_before_today'] == 1

    def test_a_loss_at_exactly_midnight_counts_today(self):
        now = 1755432000.0
        midnight = utc_midnight_seconds(now)
        pnl, counts = realized_pnl_today(
            [FakePosition('LOSS', -25.0, midnight)], now=now)
        assert pnl == -25.0
        assert counts['counted'] == 1

    def test_the_breaker_resets_at_utc_midnight(self):
        """Same three losing positions, read one second either side of the
        boundary: tripped before, clean after."""
        g = gate(daily_loss_limit_usdc=30.0)
        before = 1755388799.0                   # 23:59:59 on day N
        after = 1755388800.0                    # 00:00:00 on day N+1
        losses = [FakePosition('LOSS', -20.0, int(before)),
                  FakePosition('LOSS', -20.0, int(before))]

        pnl_before, _ = realized_pnl_today(losses, now=before)
        pnl_after, _ = realized_pnl_today(losses, now=after)

        assert not g.check_daily_loss_breaker(pnl_before)[0]
        assert g.check_daily_loss_breaker(pnl_after)[0]

    def test_open_positions_are_not_realized_pnl(self):
        pnl, counts = realized_pnl_today([FakePosition(None, None, 0)],
                                         now=1755432000.0)
        assert pnl == 0.0
        assert counts['skipped_still_open'] == 1

    def test_resolved_but_unpriced_is_skipped_not_zeroed(self):
        """Convention 11 again: no pnl_usdc is not $0.00 of pnl."""
        now = 1755432000.0
        pnl, counts = realized_pnl_today(
            [FakePosition('LOSS', None, int(now))], now=now)
        assert counts['skipped_missing_pnl'] == 1
        assert counts['counted'] == 0

    def test_the_census_balances(self):
        """Convention 20: in - skipped == counted, for every category."""
        now = 1755432000.0
        midnight = utc_midnight_seconds(now)
        positions = [
            FakePosition('WIN', +3.0, midnight),
            FakePosition('LOSS', -5.0, midnight),
            FakePosition(None, None, midnight),
            FakePosition('LOSS', None, midnight),
            FakePosition('LOSS', -5.0, midnight - 100),
            FakePosition('LOSS', -5.0, None),
        ]
        pnl, counts = realized_pnl_today(positions, now=now)
        skipped = sum(v for k, v in counts.items() if k.startswith('skipped_'))
        assert counts['seen'] == 6
        assert counts['seen'] - skipped == counts['counted'] == 2
        assert pnl == pytest.approx(-2.0)


# -- the breaker is PER ASSET (D-285/D-288) ---------------------------------

SOL5M = 'sol-updown-5m-2026-08-18-12-00'


def _loss(usd, slug, ts):
    return FakePosition('LOSS', -abs(usd), ts, market_slug=slug)


class TestPerAssetDailyLossBreaker:
    """One $30 budget shared by 3 assets x 15 strategies is a coupling, not a
    control: the first combination to lose $30 anywhere halts entries on two
    assets that have not lost a cent. Each asset gets its own $30, and a second,
    higher limit across all of them catches the systemic case."""

    # -- the split ----------------------------------------------------------

    def test_pnl_splits_by_asset_and_sums_to_the_portfolio(self):
        now = 1755432000.0
        m = utc_midnight_seconds(now)
        by_asset, total, counts = realized_pnl_today_by_asset(
            [_loss(10.0, BTC5M, m), _loss(5.0, BTC15M, m),
             _loss(7.0, ETH5M, m), _loss(3.0, SOL5M, m)], now=now)
        # BTC 5m and BTC 15m are ONE asset. They are two market TYPES, which is
        # a different question the exposure caps ask.
        assert by_asset == {'btc': pytest.approx(-15.0),
                            'eth': pytest.approx(-7.0),
                            'sol': pytest.approx(-3.0)}
        assert total == pytest.approx(-25.0)
        assert sum(by_asset.values()) == pytest.approx(total)
        assert counts['counted'] == 4

    def test_an_unregistered_slug_lands_in_a_named_bucket(self):
        """An event market's loss must be measured by SOME breaker.

        Convention 20: a position that fell out of every bucket is a loss no
        limit ever sees. It does not get its own private budget either - every
        unrouted slug shares one.
        """
        now = 1755432000.0
        m = utc_midnight_seconds(now)
        by_asset, total, _ = realized_pnl_today_by_asset(
            [_loss(10.0, EVENT, m), _loss(10.0, 'trump-wins-2028', m)], now=now)
        assert by_asset == {UNKNOWN_ASSET: pytest.approx(-20.0)}
        assert total == pytest.approx(-20.0)

    def test_asset_bucket_never_guesses(self):
        assert asset_bucket(BTC5M) == 'btc'
        assert asset_bucket(ETH5M) == 'eth'
        assert asset_bucket(SOL5M) == 'sol'
        assert asset_bucket(EVENT) == UNKNOWN_ASSET
        assert asset_bucket(None) == UNKNOWN_ASSET
        assert asset_bucket('') == UNKNOWN_ASSET

    def test_the_split_agrees_with_the_unsplit_total(self):
        """Two functions, one day, one number. If they ever disagree the two
        tiers of the breaker are measuring different days."""
        now = 1755432000.0
        m = utc_midnight_seconds(now)
        positions = [
            _loss(10.0, BTC5M, m), FakePosition('WIN', +4.0, m, market_slug=ETH5M),
            FakePosition(None, None, m, market_slug=SOL5M),      # still open
            FakePosition('LOSS', None, m, market_slug=BTC5M),    # unpriced
            _loss(9.0, SOL5M, m - 100),                          # yesterday
        ]
        flat, flat_counts = realized_pnl_today(positions, now=now)
        by_asset, total, counts = realized_pnl_today_by_asset(positions, now=now)
        assert total == pytest.approx(flat)
        assert counts == flat_counts

    # -- the per-asset tier -------------------------------------------------

    def test_one_asset_losing_does_not_halt_the_others(self):
        """The whole point. SOL is down $40; BTC must still be tradeable."""
        g = gate()
        assert not g.check_daily_loss_breaker(-40.0, asset='sol')[0]
        assert g.check_daily_loss_breaker(0.0, asset='btc',
                                          portfolio_pnl_today_usdc=-40.0)[0]

    def test_the_reason_names_the_asset_that_did_the_damage(self):
        ok, reason = gate().check_daily_loss_breaker(-40.0, asset='sol')
        assert not ok
        assert 'asset=sol' in reason
        assert reason.startswith('daily_loss_breaker')

    def test_the_per_asset_limit_is_the_same_thirty_dollars(self):
        g = gate()
        assert g.check_daily_loss_breaker(-30.0, asset='btc')[0]
        assert not g.check_daily_loss_breaker(-30.01, asset='btc')[0]
        assert g.daily_loss_limit_usdc == DEFAULT_DAILY_LOSS_LIMIT_USDC

    def test_an_unsplit_book_still_gets_the_tight_limit(self):
        """A caller that has not been updated is OVER-protected, not under.

        One $30 budget for everything is the pre-D-285 behaviour and is
        strictly stricter than three $30 budgets, so the fallback errs safe.
        """
        ok, reason = gate().check_daily_loss_breaker(-40.0)
        assert not ok
        assert 'unsplit-book' in reason
        # And it must NOT claim the portfolio tier tripped - different limit,
        # different fact.
        assert 'portfolio limit' not in reason

    # -- the portfolio tier -------------------------------------------------

    def test_the_portfolio_limit_sits_strictly_above_the_sum_of_its_parts(self):
        """A portfolio cap set AT 3 x $30 could only ever trip at the same
        instant as the last per-asset cap, so it would not be a control."""
        g = gate()
        n_assets = 3
        assert (g.portfolio_daily_loss_limit_usdc
                > n_assets * g.daily_loss_limit_usdc)
        assert (DEFAULT_PORTFOLIO_DAILY_LOSS_LIMIT_USDC
                == pytest.approx(DEFAULT_DAILY_LOSS_LIMIT_USDC
                                 * PORTFOLIO_DAILY_LOSS_LIMIT_MULTIPLE))

    def test_a_systemic_drawdown_halts_everything(self):
        """$29 lost on each of five buckets: no per-asset limit is breached and
        the book is down $145... which is still under $150. At $151 it stops."""
        g = gate()
        assert g.check_daily_loss_breaker(-29.0, asset='btc',
                                          portfolio_pnl_today_usdc=-145.0)[0]
        ok, reason = g.check_daily_loss_breaker(
            -29.0, asset='btc', portfolio_pnl_today_usdc=-151.0)
        assert not ok
        assert 'portfolio limit' in reason
        assert 'systemic' in reason

    def test_the_portfolio_tier_does_not_run_when_it_was_not_supplied(self):
        """Convention 11: `None` is "not measured", never a measured $0.00.

        Silently reading it as break-even would pass a check nobody performed.
        """
        g = gate()
        assert g.check_daily_loss_breaker(-1.0, asset='btc')[0]
        assert g.check_daily_loss_breaker(
            -1.0, asset='btc', portfolio_pnl_today_usdc=None)[0]
        assert not g.check_daily_loss_breaker(
            -1.0, asset='btc', portfolio_pnl_today_usdc=-1000.0)[0]

    def test_the_per_asset_tier_is_reported_first(self):
        """When both trip, name the asset that did the damage, not the roll-up."""
        ok, reason = gate().check_daily_loss_breaker(
            -500.0, asset='sol', portfolio_pnl_today_usdc=-500.0)
        assert not ok
        assert 'asset=sol' in reason
        assert 'portfolio limit' not in reason

    @pytest.mark.parametrize('bad', ['nope', float('nan'), float('inf'), object()])
    def test_an_unreadable_portfolio_pnl_refuses(self, bad):
        """The second tier must fail closed the same way the first does."""
        ok, reason = gate().check_daily_loss_breaker(
            0.0, asset='btc', portfolio_pnl_today_usdc=bad)
        assert not ok
        assert 'portfolio' in reason
        assert reason.startswith('daily_loss_breaker')

    def test_both_limits_are_configurable(self):
        g = gate(daily_loss_limit_usdc=5.0,
                 portfolio_daily_loss_limit_usdc=11.0)
        assert not g.check_daily_loss_breaker(-5.01, asset='btc')[0]
        assert not g.check_daily_loss_breaker(
            -1.0, asset='btc', portfolio_pnl_today_usdc=-11.01)[0]

    # -- 0.0 means OFF, not "halt on the first cent" ------------------------

    @pytest.mark.parametrize('loss', [0.01, 50.0, 5_000.0, 1e9])
    def test_a_zero_limit_disables_the_tier_it_is_set_on(self, loss):
        """`config.yaml` sets both to 0.0 with the comment "DISABLED in shadow".

        Without the `> 0` guard, 0.0 is the TIGHTEST possible setting rather
        than the loosest: `loss > 0.0` is true of one cent, so the first losing
        resolution of the day would halt every entry while the config claimed
        the breaker was off. That failure is silent, points the wrong way, and
        looks exactly like a quiet market. It gets a test with a big number in
        it so nobody can reintroduce it quietly.
        """
        g = gate(daily_loss_limit_usdc=0.0, portfolio_daily_loss_limit_usdc=0.0)
        ok, reason = g.check_daily_loss_breaker(
            -loss, asset='btc', portfolio_pnl_today_usdc=-loss)
        assert ok, reason
        assert reason == 'ok'

    def test_the_two_tiers_disable_independently(self):
        """Turning one off must not turn the other off."""
        per_asset_off = gate(daily_loss_limit_usdc=0.0,
                             portfolio_daily_loss_limit_usdc=150.0)
        assert per_asset_off.check_daily_loss_breaker(-1_000.0, asset='btc')[0]
        assert not per_asset_off.check_daily_loss_breaker(
            -1_000.0, asset='btc', portfolio_pnl_today_usdc=-1_000.0)[0]

        portfolio_off = gate(daily_loss_limit_usdc=30.0,
                             portfolio_daily_loss_limit_usdc=0.0)
        assert not portfolio_off.check_daily_loss_breaker(-31.0, asset='btc')[0]
        assert portfolio_off.check_daily_loss_breaker(
            -1.0, asset='btc', portfolio_pnl_today_usdc=-1_000_000.0)[0]

    def test_disabling_does_not_disable_the_other_risk_caps(self):
        """A zero loss limit is not a zero risk gate. Everything else binds.

        The shadow config turns the breaker off to let strategies run to zero.
        That must not quietly take the exposure and position caps with it.

        The cap is passed EXPLICITLY rather than read from the module default.
        Since D-360 that default is the 100_000 sentinel (no count cap in
        shadow), so ranging over it would build 100,000 exposures to assert a
        skip-path mechanic that binds at any cap. What this test is about is
        that a disabled breaker does not disable the OTHER gates; the cap's
        value is asserted in `TestConfigWiring`, which is where it belongs.
        """
        g = gate(daily_loss_limit_usdc=0.0, portfolio_daily_loss_limit_usdc=0.0,
                 max_concurrent_positions=5)
        v = g.check_order(
            BTC5M, 'Up', 0.50,
            open_positions=[expo() for _ in range(5)],
            realized_pnl_today_usdc=-9_999.0)
        assert not v.approved
        assert v.reason.startswith('max_concurrent_positions')

    def test_the_module_defaults_are_not_zero(self):
        """The disable is a CONFIG choice for shadow, never the built-in.

        A future reader deleting the config keys must get a breaker back, not
        inherit shadow's "run to zero" posture into whatever runs next.
        """
        assert DEFAULT_DAILY_LOSS_LIMIT_USDC > 0
        assert DEFAULT_PORTFOLIO_DAILY_LOSS_LIMIT_USDC > 0
        d = PolymarketRiskGate()
        assert not d.check_daily_loss_breaker(-40.0, asset='sol')[0]

    # -- the wiring, end to end ---------------------------------------------

    def _adapter(self, *positions):
        class StubAdapter:
            def __init__(self, pos):
                self.positions = {str(i): p for i, p in enumerate(pos)}

            def open_positions(self):
                return []
        return StubAdapter(positions)

    def test_check_adapter_order_routes_the_loss_to_its_own_asset(self):
        """SOL lost $50 today. A SOL order is blocked; a BTC order is not.

        This is the wiring test for the whole change. Before it, both were
        blocked, and the shadow loop's BTC strategies sat out an entire day
        because a different asset had a bad hour.
        """
        now = int(time.time())
        adapter = self._adapter(_loss(50.0, SOL5M, now))
        g = gate()

        blocked = g.check_adapter_order(adapter, SOL5M, 'Up', 0.50)
        assert not blocked.approved
        assert blocked.reason.startswith('daily_loss_breaker')
        assert 'asset=sol' in blocked.reason

        allowed = g.check_adapter_order(adapter, BTC5M, 'Up', 0.50)
        assert allowed.approved, allowed.reason

    def test_check_adapter_order_still_trips_the_portfolio_tier(self):
        """BTC is only $29 down, the book is $174 down, and BTC stops anyway.

        Six $29 losses: one on each registered asset and three more on
        unregistered slugs. The order is on BTC, whose own bucket is under the
        $30 per-asset limit, so ONLY the portfolio tier can block this - which
        is the point of having it.
        """
        now = int(time.time())
        adapter = self._adapter(
            _loss(29.0, BTC5M, now), _loss(29.0, ETH5M, now),
            _loss(29.0, SOL5M, now), _loss(29.0, EVENT, now),
            _loss(29.0, 'some-other-market', now),
            _loss(29.0, 'yet-another-market', now))
        v = gate().check_adapter_order(adapter, BTC5M, 'Up', 0.50)
        assert not v.approved
        assert 'portfolio limit' in v.reason
        assert 'asset=btc' in v.reason      # says which slice was under its cap

    def test_the_fifteen_minute_leg_shares_its_five_minute_asset_budget(self):
        """`btc-updown-15m` and `btc-updown-5m` are ONE asset for the breaker.

        They are two market TYPES for the exposure caps. Two questions, two
        classifications, and conflating them would give a corridor pair two
        independent daily budgets on the same underlying.
        """
        now = int(time.time())
        adapter = self._adapter(_loss(40.0, BTC15M, now))
        v = gate().check_adapter_order(adapter, BTC5M, 'Up', 0.50)
        assert not v.approved
        assert 'asset=btc' in v.reason


# -- market type exposure ----------------------------------------------------

class TestMarketTypeClassification:

    @pytest.mark.parametrize('slug,expected', [
        (BTC5M, 'btc_5m'),
        (BTC15M, 'btc_15m'),
        (BTC1H, 'btc_1h'),
        (ETH5M, 'eth_5m'),
        ('bitcoin-up-or-down-5m-window', 'btc_5m'),
        (EVENT, 'event'),
        ('', 'event'),
    ])
    def test_slugs_classify(self, slug, expected):
        assert classify_market_type(slug) == expected

    def test_longer_patterns_win(self):
        """'btc-updown-15m' contains no shorter competing pattern today, but
        the sort is what guarantees a later, more specific pattern is not
        shadowed by a shorter one already in the table."""
        patterns = {'short': ('btc-updown',), 'long': ('btc-updown-15m',)}
        assert classify_market_type(BTC15M, patterns) == 'long'

    def test_an_unrecognised_slug_gets_a_named_bucket_not_none(self):
        """Named, so it is capped like everything else."""
        assert classify_market_type('some-brand-new-market') == 'event'


class TestMarketTypeExposureCap:

    def test_headroom_in_the_type_bounds_the_new_position(self):
        """$40 type cap, $34 already open, so only $6 of premium may be added.

        The cap is checked against exposure INCLUDING the proposed position,
        not against existing exposure alone.
        """
        opens = [expo(slug=BTC5M, cost=34.0)]
        v = gate(notional_cap_usdc=10.0,
                 max_exposure_per_market_type_usdc=40.0).check_order(
                     BTC5M, 'Down', 0.50, open_positions=opens)
        assert v.approved
        assert v.binding_constraint == 'max_exposure_per_market_type'
        assert v.max_loss_usdc <= 6.0
        assert opens[0].cost_usdc + v.max_loss_usdc <= 40.0

    def test_a_full_type_blocks(self):
        opens = [expo(slug=BTC5M, cost=40.0)]
        v = gate(max_exposure_per_market_type_usdc=40.0).check_order(
            BTC5M, 'Down', 0.50, open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_exposure_per_market_type')

    def test_a_different_type_is_unaffected(self):
        """btc_5m being full does not stop an eth_5m trade."""
        opens = [expo(slug=BTC5M, cost=40.0)]
        v = gate(max_exposure_per_market_type_usdc=40.0).check_order(
            ETH5M, 'Up', 0.50, open_positions=opens)
        assert v.approved

    def test_a_per_type_override_binds_tighter_than_the_flat_cap(self):
        g = gate(max_exposure_per_market_type_usdc=40.0,
                 market_type_exposure_overrides={'event': 6.0})
        assert g.market_type_cap('event') == 6.0
        assert g.market_type_cap('btc_5m') == 40.0

        v = g.check_order(EVENT, 'Yes', 0.50)
        assert v.approved
        assert v.binding_constraint == 'max_exposure_per_market_type'
        assert v.max_loss_usdc <= 6.0

    def test_an_override_never_loosens_the_per_trade_cap(self):
        """A $500 override on a type does not raise the $10 per-trade cap."""
        v = gate(notional_cap_usdc=10.0,
                 market_type_exposure_overrides={'btc_5m': 500.0}).check_order(
                     BTC5M, 'Up', 0.50)
        assert v.binding_constraint == 'notional_cap'
        assert v.max_loss_usdc <= 10.0

    def test_total_exposure_binds_across_types(self):
        opens = [expo(slug=BTC5M, cost=30.0), expo(slug=ETH5M, cost=30.0),
                 expo(slug=EVENT, cost=36.0)]
        v = gate(max_total_exposure_usdc=100.0).check_order(
            BTC1H, 'Up', 0.50, open_positions=opens)
        assert v.approved
        assert v.binding_constraint == 'max_total_exposure'
        assert v.max_loss_usdc <= 4.0


# -- correlation -------------------------------------------------------------

class TestCorrelationKeys:
    """Dan1ro0 section 6: BTC 5m Up and BTC 15m Up are the same bet."""

    def test_btc_5m_up_and_btc_15m_up_share_one_key(self):
        assert (correlation_key('btc_5m', 'Up')
                == correlation_key('btc_15m', 'Up')
                == 'btc:up')

    def test_opposite_directions_do_not_share_a_key(self):
        assert correlation_key('btc_5m', 'Up') != correlation_key('btc_5m', 'Down')

    def test_different_underlyings_do_not_share_a_key(self):
        assert correlation_key('btc_5m', 'Up') != correlation_key('eth_5m', 'Up')

    @pytest.mark.parametrize('label,expected', [
        ('Up', 'up'), ('YES', 'up'), ('over', 'up'), ('Higher', 'up'),
        ('Down', 'down'), ('no', 'down'), ('Under', 'down'), ('short', 'down'),
    ])
    def test_direction_aliases(self, label, expected):
        assert normalize_direction(label) == expected

    def test_an_unknown_direction_never_merges_into_up(self):
        """Guessing the direction of a label we cannot read is how a
        correlation limit stops meaning anything."""
        assert normalize_direction('Sideways') == 'other:sideways'
        assert normalize_direction('') == 'other:unknown'
        assert normalize_direction(None) == 'other:unknown'

    def test_an_ungrouped_type_still_gets_a_binding_key(self):
        """The docstring's claim, asserted: no declared group is not no cap."""
        key = correlation_key('event', 'Yes')
        assert key == 'ungrouped:event:up'
        assert key  # non-empty, so it indexes a real bucket


class TestCorrelatedExposureCap:

    def test_correlated_btc_up_positions_aggregate_into_one_bucket(self):
        opens = [expo(slug=BTC5M, side='Up', cost=30.0),
                 expo(slug=BTC15M, side='Up', cost=18.0)]
        snap = aggregate_exposure(opens)
        assert snap.by_correlation_key == {'btc:up': 48.0}
        assert set(snap.by_market_type) == {'btc_5m', 'btc_15m'}

    def test_the_second_correlated_bet_is_blocked_when_the_pair_breaches(self):
        """$50 correlated cap. BTC 5m Up at $30 plus BTC 15m Up at $18 leaves
        $2 of headroom, which does not buy the 5-share minimum at 50c."""
        opens = [expo(slug=BTC5M, side='Up', cost=30.0),
                 expo(slug=BTC15M, side='Up', cost=18.0)]
        v = gate(max_correlated_exposure_usdc=50.0).check_order(
            BTC1H, 'Up', 0.50, open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_correlated_exposure')

    def test_the_correlated_cap_binds_across_market_types(self):
        """Neither per-type cap is anywhere near full; the group cap is what
        stops the third leg. If it only ever fired inside one type it would be
        a duplicate of the per-type cap."""
        opens = [expo(slug=BTC5M, side='Up', cost=22.0),
                 expo(slug=BTC15M, side='Up', cost=22.0)]
        g = gate(max_exposure_per_market_type_usdc=40.0,
                 max_correlated_exposure_usdc=50.0)
        v = g.check_order(BTC1H, 'Up', 0.50, open_positions=opens)
        assert v.approved
        assert v.binding_constraint == 'max_correlated_exposure'
        assert v.max_loss_usdc <= 6.0

    def test_btc_up_and_btc_down_do_not_aggregate(self):
        """Opposite directions are not the same bet, so a full 'btc:up' bucket
        does not block a Down entry. It is also not NETTED: the Down entry is
        added gross to its own bucket."""
        opens = [expo(slug=BTC5M, side='Up', cost=50.0)]
        v = gate(max_correlated_exposure_usdc=50.0,
                 max_exposure_per_market_type_usdc=100.0).check_order(
                     BTC15M, 'Down', 0.50, open_positions=opens)
        assert v.approved
        assert v.correlation_key == 'btc:down'

    def test_gross_not_net_across_directions(self):
        opens = [expo(slug=BTC5M, side='Up', cost=30.0),
                 expo(slug=BTC5M, side='Down', cost=30.0)]
        snap = aggregate_exposure(opens)
        assert snap.total_usdc == 60.0          # not 0.0, not 30.0
        assert snap.by_correlation_key == {'btc:up': 30.0, 'btc:down': 30.0}

    def test_an_unrecognised_market_type_still_binds_under_ungrouped(self):
        """The case most likely to be a surprise. Two unrecognised slugs are
        still capped together under one 'ungrouped:event:up' key."""
        opens = [expo(slug='brand-new-thing-a', side='Yes', cost=25.0),
                 expo(slug='brand-new-thing-b', side='Yes', cost=25.0)]
        snap = aggregate_exposure(opens)
        assert snap.by_correlation_key == {'ungrouped:event:up': 50.0}

        v = gate(max_correlated_exposure_usdc=50.0,
                 max_exposure_per_market_type_usdc=1000.0,
                 max_total_exposure_usdc=1000.0).check_order(
                     'brand-new-thing-c', 'Yes', 0.50, open_positions=opens)
        assert not v.approved
        assert v.reason.startswith('max_correlated_exposure')


# -- exposure accounting -----------------------------------------------------

class TestExposureAccounting:
    """Convention 20: a silent `continue` in a filter loop is a missing
    number. Every skip is counted AND categorised, and the identity holds."""

    def test_the_identity_holds_on_a_clean_book(self):
        snap = aggregate_exposure([expo(), expo(slug=ETH5M)])
        c = snap.counts
        assert c['seen'] == c['counted'] == 2
        assert snap.total_usdc == 10.0

    def test_an_empty_book_is_a_zero_snapshot(self):
        snap = aggregate_exposure([])
        assert isinstance(snap, ExposureSnapshot)
        assert snap.total_usdc == 0.0 and snap.count == 0
        assert snap.counts['seen'] == 0

    def test_none_is_an_empty_book(self):
        assert aggregate_exposure(None).counts['seen'] == 0

    def test_an_unreadable_position_is_counted_as_unreadable(self):
        snap = aggregate_exposure([Unreadable()])
        assert snap.counts['skipped_unreadable'] == 1
        assert snap.counts['counted'] == 0

    def test_a_non_finite_cost_is_not_filed_as_non_positive(self):
        """Two different silent drops kept separate (convention 20).

        A NaN mark is an unreadable exposure, not an exposure of nothing. A
        cost of $0.00 is a real, benign observation. Merging them into one
        counter is exactly the reporting error convention 20 was written for,
        and it hides the only one of the two that should fail closed.
        """
        class NanCost:
            market_slug = BTC5M
            outcome_side = 'Up'
            cost_usdc = float('nan')

        counts = aggregate_exposure([NanCost()]).counts
        assert counts['skipped_non_positive_cost'] == 0
        assert counts['skipped_non_finite_cost'] == 1

    def test_the_identity_holds_with_every_kind_of_skip(self):
        class NanCost:
            market_slug = BTC5M
            outcome_side = 'Up'
            cost_usdc = float('nan')

        snap = aggregate_exposure([expo(), Unreadable(), NanCost(),
                                   expo(cost=0.0), expo(cost=-1.0)])
        c = snap.counts
        skipped = sum(v for k, v in c.items() if k.startswith('skipped_'))
        assert c['seen'] == 5
        assert c['seen'] - skipped == c['counted'] == 1

    def test_unclassified_slugs_are_counted(self):
        snap = aggregate_exposure([expo(slug=EVENT), expo(slug=BTC5M)])
        assert snap.counts['unclassified_slug'] == 1

    def test_an_explicit_market_type_overrides_the_slug(self):
        snap = aggregate_exposure([expo(slug='anything', market_type='btc_5m')])
        assert snap.by_market_type == {'btc_5m': 5.0}


# -- sizing math -------------------------------------------------------------

class TestFractionalKelly:

    def test_a_real_edge_sizes_positive(self):
        """p=0.60 at 0.50: full Kelly is 0.20, a fifth of that is 0.04."""
        assert fractional_kelly(0.60, 0.50) == pytest.approx(0.04)

    def test_no_edge_sizes_zero(self):
        assert fractional_kelly(0.50, 0.50) == 0.0

    def test_negative_edge_sizes_zero_never_negative(self):
        """A negative stake is a short. There is no short here."""
        assert fractional_kelly(0.40, 0.50) == 0.0

    @pytest.mark.parametrize('entry', [0.0, 1.0, -0.5, 1.5, float('nan'),
                                       float('inf')])
    def test_degenerate_entry_prices_size_zero_not_divide_by_zero(self, entry):
        assert fractional_kelly(0.60, entry) == 0.0

    @pytest.mark.parametrize('p', [-0.1, 1.1, float('nan'), float('inf')])
    def test_probabilities_outside_zero_one_size_zero(self, p):
        assert fractional_kelly(p, 0.50) == 0.0

    def test_certainty_sizes_the_whole_fraction(self):
        assert fractional_kelly(1.0, 0.50, fraction=0.20) == pytest.approx(0.20)

    def test_the_fraction_scales_linearly(self):
        assert (fractional_kelly(0.60, 0.50, 0.40)
                == pytest.approx(2 * fractional_kelly(0.60, 0.50, 0.20)))


class TestKellySizing:

    def test_kelly_without_a_fair_value_is_refused_not_downgraded(self):
        """Kelly with no probability estimate is not a smaller bet, it is an
        unmeasurable one. Falling back to flat would hide that."""
        v = gate(sizing_mode='kelly').check_order(BTC5M, 'Up', 0.50)
        assert not v.approved
        assert v.reason.startswith('kelly_requires_fair_value')

    def test_kelly_with_no_edge_is_refused(self):
        v = gate(sizing_mode='kelly').check_order(BTC5M, 'Up', 0.50,
                                                  fair_value=0.40)
        assert not v.approved
        assert v.reason.startswith('kelly_no_edge')

    def test_the_hard_caps_still_bind_over_kelly(self):
        """Kelly on a $2000 bankroll wants $80. The per-trade cap is $10."""
        v = gate(sizing_mode='kelly', bankroll_usdc=2000.0).check_order(
            BTC5M, 'Up', 0.50, fair_value=0.60)
        assert v.approved
        assert v.binding_constraint == 'notional_cap'
        assert v.max_loss_usdc <= 10.0

    def test_kelly_binds_when_it_is_the_smallest_budget(self):
        v = gate(sizing_mode='kelly', bankroll_usdc=100.0).check_order(
            BTC5M, 'Up', 0.50, fair_value=0.60)
        assert v.approved
        assert v.binding_constraint == 'kelly_stake'
        assert v.max_loss_usdc <= 4.0

    def test_a_non_finite_bankroll_is_refused_not_ignored(self):
        """A NaN budget compares False against everything, so it silently
        drops out of `min()` and the trade sizes at the NEXT cap up. The
        Kelly constraint stops existing without saying so."""
        v = gate(sizing_mode='kelly').check_order(
            BTC5M, 'Up', 0.50, fair_value=0.60, bankroll_usdc=float('nan'))
        assert not v.approved

    def test_an_invalid_sizing_mode_is_refused(self):
        v = gate(sizing_mode='martingale').check_order(BTC5M, 'Up', 0.50)
        assert not v.approved
        assert v.reason.startswith('invalid_sizing_mode')

    def test_the_per_call_sizing_mode_overrides_the_config(self):
        v = gate(sizing_mode='flat').check_order(BTC5M, 'Up', 0.50,
                                                 sizing_mode='kelly')
        assert not v.approved
        assert v.reason.startswith('kelly_requires_fair_value')


class TestRequestedShares:

    def test_a_smaller_request_is_honoured(self):
        v = gate().check_order(BTC5M, 'Up', 0.50, requested_shares=8)
        assert v.approved
        assert v.shares == 8
        assert v.binding_constraint == 'requested_shares'

    def test_a_larger_request_is_capped(self):
        v = gate().check_order(BTC5M, 'Up', 0.50, requested_shares=10_000)
        assert v.approved
        assert v.shares == 20
        assert v.binding_constraint == 'notional_cap'

    @pytest.mark.parametrize('bad', [0, -5, float('nan')])
    def test_a_degenerate_request_is_refused_not_ignored(self, bad):
        """Silently ignoring it sizes at the FULL cap, which is the opposite
        of what a caller asking for -5 or NaN shares meant. Convention 20:
        the skip has to be visible."""
        v = gate().check_order(BTC5M, 'Up', 0.50, requested_shares=bad)
        assert not v.approved
        assert v.reason.startswith('invalid_requested_shares')


class TestSharesForPremium:

    def test_whole_shares_only(self, g):
        assert g.shares_for_premium(0.30, 10.0) == 33

    def test_below_the_exchange_minimum_returns_zero(self, g):
        assert g.shares_for_premium(0.90, 2.0) == 0

    @pytest.mark.parametrize('premium', [0.0, -0.5, float('nan'), float('inf')])
    def test_a_degenerate_premium_returns_zero(self, g, premium):
        assert g.shares_for_premium(premium, 10.0) == 0

    @pytest.mark.parametrize('budget', [0.0, -10.0, float('nan'), float('inf')])
    def test_a_degenerate_budget_returns_zero(self, g, budget):
        assert g.shares_for_premium(0.50, budget) == 0


class TestFees:
    """Polymarket charges no CLOB taker fee today. That is an assumption with
    an expiry date, and the caps have to survive it changing."""

    def test_zero_fee_is_the_default(self, g):
        assert g.taker_fee_rate == 0.0
        assert g.check_order(BTC5M, 'Up', 0.50).fee_usdc == 0.0

    def test_the_fee_is_inside_the_per_trade_cap(self):
        """`max_loss_usdc` is notional PLUS fee, and the cap bounds the loss.

        Sizing on premium alone and then adding the fee on top puts real risk
        above every cap by exactly the fee. At 0% that is invisible, which is
        precisely why it has to be locked now rather than discovered the day
        Polymarket turns fees on.
        """
        g = gate(taker_fee_rate=0.02, notional_cap_usdc=10.0)
        v = g.check_order(BTC5M, 'Up', 0.50)
        assert v.approved
        assert v.fee_usdc > 0
        assert v.max_loss_usdc <= 10.0

    def test_the_fee_is_inside_the_total_exposure_cap(self):
        g = gate(taker_fee_rate=0.02, max_total_exposure_usdc=10.0,
                 notional_cap_usdc=100.0)
        v = g.check_order(BTC5M, 'Up', 0.50)
        assert v.approved
        assert v.max_loss_usdc <= 10.0


# -- mode refusal ------------------------------------------------------------

class TestPaperOnly:
    """Live execution needs EIP-712 signing and Aym's approval (D-267)."""

    def test_paper_is_allowed(self, g):
        assert g.check_order(BTC5M, 'Up', 0.50, mode='paper').approved

    @pytest.mark.parametrize('mode', ['live', 'LIVE', 'Paper', 'PAPER',
                                      'paper ', '', None, 'dry-run'])
    def test_anything_that_is_not_exactly_paper_is_refused(self, g, mode):
        """Fail closed on case and whitespace too. A gate that accepts 'PAPER'
        accepts whatever else a config typo produces."""
        v = g.check_order(BTC5M, 'Up', 0.50, mode=mode)
        assert not v.approved
        assert v.reason.startswith('live_mode_not_authorized')

    def test_the_mode_check_runs_before_everything_else(self, g):
        """Live must be refused for being live, not for a sizing detail."""
        v = g.check_order(BTC5M, 'Up', 0.50, mode='live',
                          realized_pnl_today_usdc=-9999.0)
        assert v.reason.startswith('live_mode_not_authorized')

    def test_the_adapter_helper_cannot_be_talked_into_live(self, g):
        class StubAdapter:
            positions = {}

            def open_positions(self):
                return []

        v = g.check_adapter_order(StubAdapter(), BTC5M, 'Up', 0.50, mode='live')
        assert not v.approved
        assert v.reason.startswith('live_mode_not_authorized')


# -- delegation to the crypto ops backstops ---------------------------------

class TestOpsBackstopDelegation:
    """The daily/weekly EQUITY backstops are asset agnostic tail-event
    catches. They are delegated to `engine.risk.RiskGate`, never
    reimplemented, so there is one definition of "equity fell off a cliff"."""

    def test_a_tripped_ops_backstop_blocks_the_order(self, g_with_ops, conn):
        gate_, _ops = g_with_ops
        v = gate_.check_order(BTC5M, 'Up', 0.50, conn=conn)
        assert not v.approved
        assert 'ops_stop' in v.reason

    def test_a_healthy_equity_series_does_not_block(self, g_with_ops_ok, conn):
        gate_, _ops = g_with_ops_ok
        assert gate_.check_order(BTC5M, 'Up', 0.50, conn=conn).approved

    def test_no_conn_means_the_backstops_are_not_consulted(self, g_with_ops):
        """Documented behaviour, asserted so it stays deliberate: with no DB
        handle the equity backstops are skipped and the Polymarket caps stand
        alone. The paper adapter writes no equity_snapshots rows, so on that
        path there is nothing for them to read anyway."""
        gate_, ops = g_with_ops
        assert gate_.check_order(BTC5M, 'Up', 0.50).approved
        assert ops.calls == []

    def test_the_backstop_reason_is_passed_through_verbatim(self, g_with_ops,
                                                            conn):
        gate_, _ops = g_with_ops
        v = gate_.check_order(BTC5M, 'Up', 0.50, conn=conn)
        assert v.reason
        assert v.shares == 0


class StubOpsGate:
    """Records whether it was consulted. A backstop that is silently never
    called is worse than no backstop, because the config says it is on."""

    def __init__(self, ok=True, reason='ok'):
        self.ok = ok
        self.reason = reason
        self.calls = []

    def check_ops_backstops(self, conn):
        self.calls.append(conn)
        return self.ok, self.reason


@pytest.fixture
def db(tmp_path, monkeypatch):
    from engine.db import init_schema
    monkeypatch.setenv('TRADING_DB_PATH', str(tmp_path / 'test_pm_risk.db'))
    init_schema()
    return str(tmp_path / 'test_pm_risk.db')


@pytest.fixture
def conn(db):
    from engine.db import get_connection
    c = get_connection()
    yield c
    c.close()


def _crypto_gate():
    from engine.risk import RiskGate
    return RiskGate({
        'exchange': {'name': 'binanceus',
                     'fees': {'maker': 0.001, 'taker': 0.001}},
        'risk': {'notional_cap_usd': 10, 'fee_to_edge_max': 0.15,
                 'max_trades_per_day': 1, 'consecutive_loss_pause': 4,
                 'max_concurrent_positions': 2, 'max_positions_per_pair': 1,
                 'daily_ops_stop_multiplier': 3,
                 'weekly_ops_stop_multiplier': 15},
    })


def _snapshot(conn, ts_ms, equity):
    conn.execute(
        "INSERT INTO equity_snapshots (ts, equity, cash, open_risk, mode) "
        "VALUES (?, ?, ?, 0.0, 'paper')", (ts_ms, equity, equity))
    conn.commit()


@pytest.fixture
def g_with_ops(conn):
    """Equity down $500 today against a $30 daily threshold (3 x $10)."""
    now_ms = int(time.time() * 1000)
    day_start = utc_midnight_seconds() * 1000
    _snapshot(conn, day_start + 1000, 2000.0)
    _snapshot(conn, now_ms, 1500.0)
    ops = _crypto_gate()
    return PolymarketRiskGate({'polymarket': {'risk': {}}}, ops_gate=ops), ops


@pytest.fixture
def g_with_ops_ok(conn):
    now_ms = int(time.time() * 1000)
    day_start = utc_midnight_seconds() * 1000
    _snapshot(conn, day_start + 1000, 2000.0)
    _snapshot(conn, now_ms, 1999.0)
    ops = _crypto_gate()
    return PolymarketRiskGate({'polymarket': {'risk': {}}}, ops_gate=ops), ops


class TestOpsBackstopContract:
    """The delegation itself, with a stub, so a failure here is about wiring
    rather than about the crypto gate's own thresholds."""

    def test_the_stub_is_consulted_when_a_conn_is_supplied(self):
        ops = StubOpsGate(ok=True)
        g_ = PolymarketRiskGate(None, ops_gate=ops)
        sentinel = object()
        assert g_.check_order(BTC5M, 'Up', 0.50, conn=sentinel).approved
        assert ops.calls == [sentinel]

    def test_a_refusing_stub_blocks_with_its_own_reason(self):
        ops = StubOpsGate(ok=False, reason='weekly_ops_stop: drop=$900.00')
        g_ = PolymarketRiskGate(None, ops_gate=ops)
        v = g_.check_order(BTC5M, 'Up', 0.50, conn=object())
        assert not v.approved
        assert v.reason == 'weekly_ops_stop: drop=$900.00'

    def test_the_backstops_run_before_the_polymarket_breaker(self):
        ops = StubOpsGate(ok=False, reason='daily_ops_stop: drop=$500.00')
        g_ = PolymarketRiskGate(None, ops_gate=ops)
        v = g_.check_order(BTC5M, 'Up', 0.50, conn=object(),
                           realized_pnl_today_usdc=-999.0)
        assert v.reason.startswith('daily_ops_stop')


# -- adapter integration -----------------------------------------------------

class TestAdapterIntegration:

    def test_exposures_are_read_off_open_positions_only(self):
        from engine.polymarket.paper_adapter import PaperPosition

        open_pos = PaperPosition(
            position_id='a', strategy='s', market_slug=BTC5M, token_id='t',
            outcome_side='Up', shares=20, avg_price=0.50, cost_usdc=10.0,
            fee_usdc=0.0, opened_ts=int(time.time()))
        resolved = PaperPosition(
            position_id='b', strategy='s', market_slug=BTC15M, token_id='t',
            outcome_side='Up', shares=20, avg_price=0.50, cost_usdc=10.0,
            fee_usdc=0.0, opened_ts=int(time.time()), resolution='LOSS',
            pnl_usdc=-10.0)

        class StubAdapter:
            positions = {'a': open_pos, 'b': resolved}

            def open_positions(self):
                return [open_pos]

        exposures = exposures_from_adapter(StubAdapter())
        assert len(exposures) == 1
        assert exposures[0].cost_usdc == pytest.approx(10.0)   # premium + fee

    def test_check_adapter_order_folds_in_todays_realized_loss(self):
        from engine.polymarket.paper_adapter import PaperPosition

        loser = PaperPosition(
            position_id='b', strategy='s', market_slug=BTC15M, token_id='t',
            outcome_side='Up', shares=20, avg_price=0.50, cost_usdc=10.0,
            fee_usdc=0.0, opened_ts=int(time.time()), resolution='LOSS',
            pnl_usdc=-100.0)

        class StubAdapter:
            positions = {'b': loser}

            def open_positions(self):
                return []

        v = gate().check_adapter_order(StubAdapter(), BTC5M, 'Up', 0.50)
        assert not v.approved
        assert v.reason.startswith('daily_loss_breaker')

    def test_the_adapter_is_never_written_to(self):
        from engine.polymarket.paper_adapter import PaperPosition

        pos = PaperPosition(
            position_id='a', strategy='s', market_slug=BTC5M, token_id='t',
            outcome_side='Up', shares=20, avg_price=0.50, cost_usdc=10.0,
            fee_usdc=0.0, opened_ts=int(time.time()))

        class StubAdapter:
            def __init__(self):
                self.positions = {'a': pos}

            def open_positions(self):
                return [pos]

        adapter = StubAdapter()
        before = dict(adapter.positions)
        gate().check_adapter_order(adapter, BTC15M, 'Up', 0.50)
        assert adapter.positions == before


# -- verdict shape -----------------------------------------------------------

class TestVerdictShape:

    def test_every_rejection_carries_a_reason(self, g):
        """Convention 6, asserted across every gate in the module."""
        rejections = [
            g.check_order(BTC5M, 'Up', 0.50, mode='live'),
            g.check_order(BTC5M, 'Up', 0.50, realized_pnl_today_usdc=-500.0),
            g.check_order(BTC5M, 'Up', 0.0),
            g.check_order(BTC5M, 'Up', 'junk'),
            # Explicit small cap, not the module default: since D-360 that
            # default is the 100_000 sentinel and this assertion is about the
            # VERDICT SHAPE of a count rejection, not about where the cap sits.
            gate(max_concurrent_positions=5).check_order(
                BTC5M, 'Up', 0.50,
                open_positions=[expo(slug='m{}'.format(i))
                                for i in range(6)]),
            g.check_order(BTC5M, 'Up', 0.50, open_positions=[expo(side='Up')]),
            gate(notional_cap_usdc=1.0).check_order(BTC5M, 'Up', 0.90),
            gate(sizing_mode='kelly').check_order(BTC5M, 'Up', 0.50),
            gate(sizing_mode='nonsense').check_order(BTC5M, 'Up', 0.50),
        ]
        for v in rejections:
            assert not v.approved
            assert v.reason and v.reason != 'approved'
            assert v.shares == 0

    def test_an_approval_says_approved(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50)
        assert v.approved and v.reason == 'approved'

    def test_to_dict_is_json_portable(self, g):
        """Convention 19: `json.dump(allow_nan=False)` must not raise, or the
        verdict cannot be logged in a file another parser can read."""
        import json
        for v in (g.check_order(BTC5M, 'Up', 0.50),
                  g.check_order(BTC5M, 'Up', float('nan')),
                  g.check_order(BTC5M, 'Up', 0.50, mode='live')):
            json.dumps(v.to_dict(), allow_nan=False)

    def test_the_verdict_carries_its_classification(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50)
        assert v.market_type == 'btc_5m'
        assert v.correlation_key == 'btc:up'
        assert v.sizing_mode == 'flat'

    def test_a_block_still_reports_the_classification(self, g):
        v = g.check_order(BTC5M, 'Up', 0.50, realized_pnl_today_usdc=-500.0)
        assert v.market_type == 'btc_5m'
        assert v.correlation_key == 'btc:up'


# -- config wiring -----------------------------------------------------------

class TestConfigWiring:
    """Convention 17. A threshold that only exists as a module constant cannot
    be reviewed, and nobody notices when the assumption behind it expires."""

    def test_defaults_apply_with_no_config_at_all(self):
        g_ = PolymarketRiskGate()
        assert g_.notional_cap_usdc == rg.DEFAULT_NOTIONAL_CAP_USDC
        assert g_.max_concurrent_positions == rg.DEFAULT_MAX_CONCURRENT_POSITIONS

    def test_config_overrides_reach_the_gate(self):
        g_ = PolymarketRiskGate({'polymarket': {'risk': {
            'notional_cap_usdc': 3.0, 'daily_loss_limit_usdc': 9.0}}})
        assert g_.notional_cap_usdc == 3.0
        assert g_.daily_loss_limit_usdc == 9.0

    def test_bankroll_falls_back_to_starting_equity(self):
        g_ = PolymarketRiskGate({'polymarket': {'starting_equity_usdc': 500.0}})
        assert g_.bankroll_usdc == 500.0

    def test_config_yaml_has_a_polymarket_risk_block(self):
        with open(os.path.join(REPO_ROOT, 'config.yaml')) as fh:
            cfg = yaml.safe_load(fh)
        assert 'risk' in cfg.get('polymarket', {}), (
            'config.yaml has no polymarket.risk block, so every Polymarket '
            'threshold is invisible to review (convention 17)')

    def test_config_yaml_matches_the_module_defaults(self):
        """The shipped config must not silently change behaviour.

        This is a drift lock in both directions: change a DEFAULT_* and forget
        config.yaml, or edit config.yaml thinking it is documentation, and
        this fails. Loosening a cap should be a decision with a D-number, not
        a diff nobody read.

        The two daily-loss scalars are excluded from the equality loop and
        checked separately below (D-316 ruling). config.yaml is the OVERRIDE,
        not a duplicate of the module default: Aym's explicit, repeated ruling
        is no portfolio-wide stop in shadow mode (blowup = log + restart +
        Forge adjusts), so config.yaml ships them at 0.0 - which
        `PolymarketRiskGate` already treats as "disabled" (`> 0` gates every
        use of both fields). The module default of 30.0 stays as the LIVE-mode
        fallback for a caller that builds a gate with no config at all; it is
        not a claim that shadow runs with a cap.
        """
        with open(os.path.join(REPO_ROOT, 'config.yaml')) as fh:
            cfg = yaml.safe_load(fh)
        from_yaml = PolymarketRiskGate(cfg)
        from_defaults = PolymarketRiskGate()

        scalars = ['notional_cap_usdc', 'max_total_exposure_usdc',
                   'max_concurrent_positions', 'max_positions_per_market_side',
                   'max_positions_per_market',
                   'max_exposure_per_market_type_usdc',
                   'max_correlated_exposure_usdc', 'min_premium', 'max_premium',
                   'min_shares', 'kelly_fraction', 'sizing_mode',
                   'bankroll_usdc', 'taker_fee_rate']
        for name in scalars:
            assert getattr(from_yaml, name) == getattr(from_defaults, name), (
                'config.yaml polymarket.risk.{} disagrees with the module '
                'default'.format(name))
        assert from_yaml.market_type_exposure_overrides == {}

        # config OVERRIDES the module default here by design (D-316): shadow
        # mode ships no daily stop, so both scalars are 0.0 - the value
        # `PolymarketRiskGate` reads as "no limit" - deliberately below the
        # module's 30.0 live-mode fallback rather than equal to it.
        assert from_yaml.daily_loss_limit_usdc == 0.0
        assert from_yaml.portfolio_daily_loss_limit_usdc == 0.0
        assert from_defaults.daily_loss_limit_usdc == DEFAULT_DAILY_LOSS_LIMIT_USDC

    def test_config_yaml_classification_tables_match_the_module(self):
        """YAML gives lists where the module gives tuples, so compare the
        behaviour rather than the container type."""
        with open(os.path.join(REPO_ROOT, 'config.yaml')) as fh:
            cfg = yaml.safe_load(fh)
        from_yaml = PolymarketRiskGate(cfg)

        def norm(d):
            return {k: tuple(v) for k, v in d.items()}

        assert norm(from_yaml.market_type_patterns) == norm(
            DEFAULT_MARKET_TYPE_PATTERNS)
        assert norm(from_yaml.correlation_groups) == norm(
            DEFAULT_CORRELATION_GROUPS)

        for slug, expected in [(BTC5M, 'btc_5m'), (ETH5M, 'eth_5m'),
                               (EVENT, 'event')]:
            assert from_yaml.market_type(slug) == expected
        assert from_yaml.correlation_key(BTC15M, 'Up') == 'btc:up'


# -- delegation (D-343 R1) ----------------------------------------------------

def test_pm_gate_no_longer_defines_its_own_notional_caps():
    """D-343 R1: the PM gate's per-trade and aggregate caps used to be two
    independent declarations of numbers `engine.risk.constraints` also
    declares - the exact duplication `engine/halt.py` warns against ("three
    copies of a kill switch is three chances for one of them to point
    somewhere else"). Both are now SOURCED from that module, not redeclared.
    """
    from engine.risk import constraints as risk_constraints

    assert rg.DEFAULT_NOTIONAL_CAP_USDC == pytest.approx(
        risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd)
    assert rg.DEFAULT_MAX_TOTAL_EXPOSURE_USDC == pytest.approx(
        risk_constraints.DEFAULT_LIMITS.aggregate_notional_usd)
    # The aggregate number in particular must have moved off the old $100 -
    # equality with the OLD constant alone would not prove delegation
    # happened, since $10 for the per-trade cap was already a coincidence.
    assert rg.DEFAULT_MAX_TOTAL_EXPOSURE_USDC != 100.0

    # Structural: the module must SOURCE the numbers from risk_constraints
    # rather than redeclare a literal that merely happens to match today.
    import inspect
    source = inspect.getsource(rg)
    assert ('risk_constraints.DEFAULT_LIMITS.per_trade_notional_usd'
           in source)
    assert ('risk_constraints.DEFAULT_LIMITS.aggregate_notional_usd'
           in source)


def test_config_yaml_max_total_exposure_matches_the_delegated_default():
    """The drift lock in `test_config_yaml_matches_the_module_defaults` only
    catches config.yaml drifting from the MODULE default - it says nothing
    about whether that default is itself still the decorative $100. This
    pins the number D-343 R1 actually ratified.
    """
    from engine.risk import constraints as risk_constraints
    with open(os.path.join(REPO_ROOT, 'config.yaml')) as fh:
        cfg = yaml.safe_load(fh)
    assert (cfg['polymarket']['risk']['max_total_exposure_usdc']
           == risk_constraints.DEFAULT_LIMITS.aggregate_notional_usd)
