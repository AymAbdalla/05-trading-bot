"""Polymarket prediction-market strategies (D-267, D-268).

Nineteen strategies. Count them in `build_strategies()`, not here: this opening
line said "Eleven" long after the list had grown past eleven, which is the
ordinary failure mode of a number written in prose next to a list that other
sessions append to. If the two disagree again, the list is right.

Five are ports from moondevonyt's public Polymarket repo, two implement Forge
proposals, one (`fair_value_arb`) implements Dan1ro0 concepts 1-2 and is the
only strategy FAMILY in this package that does NOT hold to resolution, and THREE
are parameter variants of that one - read the variants section below, because a
variant is not an independent result. moondevonyt's thresholds are preserved;
the MoonDev API dependency, the wallet client, his account config, and the live
order path are all removed. Data comes from Polymarket's public read-only APIs
and from public exchange feeds. The remainder are later additions: an inverse
fair-value variant, three liquidation-driven strategies, and the four newest
ones documented in their own section near the bottom of this docstring.

    from strategies.polymarket import build_strategies
    for s in build_strategies():
        decision = s.evaluate(ctx)   # always returns a Decision, never None

Every module here sets `PAPER_MODE = True` and every class carries
`paper_mode = True`. His originals ship `PAPER_MODE = False`.

## Status: every one of these is NOT_TESTED

None of the nineteen has been through our graveyard. moondevonyt's win rates are
his numbers from his logs on his setup - hypotheses, not evidence (convention
3). The two Forge-proposal strategies have no vendor numbers at all, only an
estimated edge written before any code existed (convention 15).
`fair_value_arb` carries a 99.3%/32,614-trade claim from a Reddit post about
somebody else's wallet, which is the weakest provenance in the package and is
stamped `claimed_win_rate_is_unverified_vendor_number=True` on every row it
emits - and so, by inheritance, do all three of its variants. The
resolution-PnL harness extension does not exist yet, and running these through
the existing price-path harness would fabricate numbers (D-268).

## The three fair-value variants are ONE hypothesis tested four ways

`PM_fair_value_arb_wide`, `PM_fair_value_arb_patient` and
`PM_fair_value_arb_hft` are thin subclasses of `FairValueArb`. They share its
fair value model, its entry path, its exit rule ORDER and its provenance. Only
constants differ, plus one piece of new logic in `patient` (a 15-second minimum
holding time, which DEFERS THE STOP - its module docstring states the worst-case
loss that creates, and that statement is load-bearing, not decoration).

Two consequences, both of which are ways to lie with these numbers:

  - **Four strategies agreeing is not four pieces of evidence.** They are one
    model evaluated on the same windows with different thresholds, so their
    results are heavily correlated by construction. This is the same failure the
    `fair_value.py` correlation rule exists to stop, one level up: counting one
    BTC move as five confirmations. A result that holds across all four is ONE
    result about the fair value model, not four.
  - **Their break-evens differ, so their win rates are not comparable.** Each
    class exposes `breakeven_win_rate`, computed from the INSTANCE rather than
    written down, because comparing a 62.5%-break-even variant's win rate
    against a 75%-break-even parent's is comparing two different questions:

        PM_fair_value_arb           1c target / 3c stop   break-even 75.0%
        PM_fair_value_arb_wide      3c target / 5c stop   break-even 62.5%
        PM_fair_value_arb_patient   2c target / 3c stop   break-even 60.0%
        PM_fair_value_arb_hft       1c target / 2c stop   break-even 66.7%

    A lower break-even is NOT a better strategy. It is a different payoff shape,
    and every one of these shapes was reached by moving a threshold rather than
    by measuring anything - convention 17's exact warning. `patient`'s 60% is
    additionally an UPPER BOUND, because its min-hold does not enforce the 3c
    stop for the first 15 seconds of a position's life.

The scorer must keep all four in SEPARATE populations. They share a code path,
not a population.

## One family exits early, and that splits the scoring

`PM_fair_value_arb` and its three variants sell before resolution. Their
positions close with `exit_kind='sell'` and a few cents of PnL; every other
strategy here closes with `exit_kind='resolution'` and a 1.00-or-0.00 payoff.
Those two populations must be scored SEPARATELY and never pooled - a pooled win
rate across them describes neither. `PolymarketPaperAdapter.summary()` reports
`by_exit_kind` for exactly this reason.

`PM_dip_arb` joins the SELL population. See its entry in the newest-four
section: it manages its own exits, so it belongs there and nowhere else.

## Where our port deliberately differs from his bots

  streak_snapper      SKIPs when the ask is above the 52c cap. He rests a 52c
                      limit anyway and cancels after 60s - a maker fill we
                      cannot simulate. Also gates on the book-walked effective
                      entry, not top-of-book.
  mid_price_cont.     Gates the 0.40-0.55 band on the effective entry for the
                      full intended size, not the best ask.
  box_builder         Returns QUOTE, never ENTER. See its module docstring.
  corridor_collector  Adds a depth check on both legs before committing to
                      either; he discovers thin books as an UNPAIRED leg.
  spread_harvest      TAKER, not maker. This is not a tightening, it is a
                      DIFFERENT ORDER, and the strategy key says so
                      (`PM_spread_harvest_taker`). Read its module docstring
                      before quoting any result from it against his bot.

The first four are tightenings, all logged in the module docstrings, and none
changes a threshold. The fifth is not a tightening and is named accordingly.

## The Forge-proposal strategy, and the one that only LOOKED like one

  temporal_arbitrage           Proposal 002. Buys the two sides of ONE 5m window
                               at different instants, each when it is cheap, for
                               a pair that redeems 1.00. Between the legs it is
                               NAKED, and leg-completion risk - not direction -
                               is the whole trade.
  corridor_pair_live           RENAMED from `cross_window_relative_value`
                               (D-281). The module and the class carry the
                               `_live` suffix; the `strategy_name` key is
                               `PM_corridor_pair`, which is what D-281 ruled.
                               The old name claimed a lineage this file
                               does not have. It implements the FLOORED PAIR
                               structure, which is proposal 005's own "nearest
                               neighbour" and explicitly NOT its relative-value
                               hypothesis. Proposal 005 stays PROPOSED and
                               UNBUILT: it is blocked on 30 days of paired
                               history we do not have, and nothing here invents
                               the missing distribution. The name now says what
                               the code is - corridor_collector's structure, run
                               live off a lead we can actually measure. Its
                               module docstring is the authority.

## The four newest strategies, and what is wrong with each

These four came out of a repo-reading session. All four thresholds are OURS and
UNMEASURED, written before any run (convention 15). None of the four has been
through `backtest/polymarket_harness.py`, because that harness does not score
resolution PnL yet (D-268). Every one of them is NOT_TESTED.

Of the four, only `PM_weather_arb` and `PM_dip_arb` can produce an entry at all
today. The other two are refusals, and each refuses for its own reason.

  PM_smart_money_copy   **CANNOT ENTER as shipped.** All seven tracked wallet
                        handles (bonereaper, 0x50f7, boneohio, coinfilippe,
                        0xaaaaa, doggystyie, Sharky6999) resolve to `None`,
                        because we have no real proxy wallet addresses for any
                        of them. Two of those handles - `0x50f7` and `0xaaaaa` -
                        are 4-hex PREFIXES, not addresses. They are held in a
                        SEPARATE map (`TRACKED_WALLET_PREFIXES`) precisely so a
                        prefix can never leak into a query string and be sent to
                        an endpoint as if it were an address. The strategy
                        refuses with `wallet_address_unresolved` BEFORE it makes
                        any network request at all.

                        The second blocker survives fixing the first. Even given
                        a real address, Polymarket's public `/trades` endpoint
                        returns FILLS, not OUTCOMES: no `won` field, no realized
                        PnL, no redemption flag. So the >60% win rate and >50
                        trades gate cannot be evaluated from it, and every wallet
                        is refused with `wallet_record_unmeasured`. That refusal
                        is deliberate. The source win rates are a blog post about
                        somebody else's wallet, and copying them into the gate
                        would fabricate the evidence the gate exists to check.
                        Both of these are NOT_TESTED (convention 11), not
                        tested-and-found-nothing.

  PM_grid_hedge         **CANNOT ENTER, by construction.** It is a MAKER
                        strategy. It returns QUOTE and never ENTER, exactly like
                        `PM_box_builder`, and the shadow loop counts it under
                        `maker_quote_not_simulable`. This is not a docstring
                        claim: a module-level `assert_not_enter` guard RAISES on
                        an ENTER decision, so the refusal is enforced in the
                        wiring and a future edit that tried to emit an entry
                        would blow up rather than quietly start trading
                        (convention 22).

                        Its kill condition is "grid PnL below -$5.00 over 50 grid
                        fills". That kill condition is currently UNMEASURABLE.
                        Maker fills are not modelled anywhere in this repo, so 50
                        grid fills can never accumulate and the condition can
                        never be evaluated, in either direction. It is not a
                        passing kill condition. It is one that cannot run.

  PM_weather_arb        **CANNOT ENTER on today's live board.** This model
                        prices a POINT-IN-TIME reading at the settlement
                        timestamp. Live weather markets resolve on the DAILY
                        EXTREME, and the daily max of a path is a different
                        random variable from the path's endpoint. Gated off
                        behind `allow_daily_extreme_markets=False`; every such
                        market is refused under
                        `daily_extreme_not_priced_by_point_in_time_model`, a
                        convention 11 cannot-run and NOT a claim that there is
                        no edge there.

                        The gate is CONDITIONAL, not blanket, and the
                        distinction matters for anyone reading the code: it
                        fires only when the market carries a detected
                        `market_metric`. A genuine point-in-time weather market
                        would still be enterable. Measured 2026-08-18 over 80
                        live markets with real books and real METAR, 100% of
                        the live universe was daily-extreme, so in practice
                        this books zero entries.

                        That measurement is also why the refusal exists at all:
                        the FIXED parser produced 7 ENTERs with realised "edge"
                        of 0.45 to 0.999, and the errors pointed in OPPOSITE
                        directions on the two ladders, so a pooled win rate
                        would have averaged two biases into something that
                        looked unbiased.

                        It is unproven in two further ways, plus a third
                        provenance problem.

                        First, its `WEATHER_MARKETS` station table (KNYC/KLGA,
                        KLAX, KMDW/KORD, KMIA, KDEN) is an ASSUMPTION. It was
                        assembled from general knowledge and has never been
                        checked against any live market's rules text. It is never
                        used to DECIDE resolution, and every row stamps
                        `station_assumption_matches_rules` so a disagreement with
                        the rules is visible in the data rather than silently
                        absorbed.

                        Second, its uncertainty model is
                        `sigma_F = 0.75 + 1.5*sqrt(hours)`. That is a diffusion
                        form, and it is being applied to a variable with a large
                        deterministic diurnal cycle. Temperature is not a random
                        walk; it goes up in the afternoon and down at night on a
                        schedule. So this model is wrong in a KNOWN direction at
                        KNOWN times of day. It was never fitted to anything and
                        never backtested.

                        Third, the claimed 3-8 degree airport-versus-downtown gap
                        is an unverified social media claim. It is stamped
                        `claimed_gap_is_unverified_vendor_number` on every row,
                        same treatment as the Reddit 99.3% number.

  PM_dip_arb            Can enter. Belongs in the SELL population and must NEVER
                        be pooled with the resolution population: it manages its
                        own exits (`manages_exits = True`), the same shape as the
                        `FairValueArb` family.

                        It is NOT the same hypothesis as `fair_value_arb`, and
                        the difference is the whole risk. `fair_value_arb` uses a
                        probability model of BTC's move, which is an independent
                        estimate of truth. `dip_arb` uses the outcome's OWN
                        historical mean price, which is a LAGGING estimate
                        derived from the same series it is trading. That means
                        "the price dipped below its average" and "the truth
                        changed and the average has not caught up yet" are
                        indistinguishable from price alone. Every real repricing
                        looks like a dip on the way through. That is its core
                        risk, and no threshold fixes it.

                        Its `breakeven_win_rate` is a computed instance property
                        (0.714 at defaults, which is the worst case), not a
                        written-down constant. It must be compared against its
                        OWN break-even, never against the fair-value family's,
                        for the same reason the four fair-value variants are not
                        comparable to each other.

                        Its tape can only ever be minutes long on this venue,
                        because 5m token ids are new every window and nothing
                        carries across. At a 5s poll, 20 observations takes about
                        100 seconds to accumulate and no new entry is allowed
                        inside the last 60 seconds, so its fireable band is
                        roughly seconds 100 to 240 of a 300 second window. A
                        "historical average" here means about two minutes of
                        history.

## What each one needs before it can be scored

  PM_streak_snapper           taker, single leg. Ready for the harness
                              extension as-is.
  PM_mid_price_continuation   taker, single leg. Needs a live BTC spot feed
                              and the window's strike (a Chainlink TWAP read;
                              Gamma does not publish one).
  PM_box_builder              MAKER. Returns QUOTE, never ENTER. Needs a maker
                              fill model before it can be scored at all - see
                              its module docstring for why simulating resting
                              fills as taker fills would overstate it.
  PM_corridor_collector       taker, two legs across two markets. Needs the 15m
                              market context alongside the 5m, a 15m-vs-5m-open
                              lead, and an ATR14 quoted in BASIS POINTS.
  PM_temporal_arbitrage       taker, one leg per decision, two decisions per
                              pair. Needs the scorer to charge UNPAIRED legs to
                              the strategy at their realised resolution PnL, and
                              to compute completion rate by joining positions on
                              window_ts - NOT by counting ENTER decisions, which
                              this strategy cannot confirm became fills.
  PM_corridor_pair            taker, two legs across two markets. Needs the 15m
                              context and BTC 5m bars covering the 15m open. It
                              only ever fires on the FINAL third of a 15m
                              window; the $1.00 floor does not exist otherwise.
                              Needs 8c of edge below binned fair (D-281).
  PM_spread_harvest_taker     taker, single leg. Fires on NOTHING today: D-282
                              ships it with `allow_book_implied_coin_flip=False`
                              and Gamma publishes no strike, so its only live
                              gate is unreachable. Results from a sensitivity
                              run under `coin_flip_source='book_implied'` must
                              be scored SEPARATELY from any produced under
                              `cushion_atr`; they are different gates.
  PM_fair_value_arb           taker in AND taker out. Needs a live BTC spot at
                              poll frequency (for the price tape that feeds the
                              speed and realized-vol inputs), both books for the
                              imbalance signal, and the 5m bar whose timestamp
                              equals this window's - all supplied by the shadow
                              loop. Needs the scorer to charge positions that
                              could NOT be sold at their realised resolution
                              PnL, exactly as temporal_arbitrage's unpaired legs
                              are charged, and to keep sold and redeemed trades
                              in separate populations.
  PM_fair_value_arb_wide      All three variants: exactly the parent's data
  PM_fair_value_arb_patient   requirements, plus a scorer that keeps each
  PM_fair_value_arb_hft       variant in its OWN population and compares each
                              realised win rate against its OWN
                              `breakeven_win_rate` rather than against the
                              others'. `patient` additionally needs the scorer
                              to surface `min_hold_suppressed_reason` on closes,
                              because the realised loss on deferred stops is the
                              only measurement that can falsify its min-hold.
  PM_smart_money_copy         Needs REAL wallet addresses for all seven handles,
                              and then needs an outcome-bearing trade source -
                              positions or redemptions, not `/trades` fills -
                              before its win-rate gate can be evaluated at all.
                              Without both, it emits refusals and nothing else.
  PM_weather_arb              Needs each market's rules text read and the
                              station table verified against it, and needs
                              `sigma_F` fitted to actual station history rather
                              than assumed. Until then a PASS from it is a
                              statement about our assumed sigma, not about
                              weather.
  PM_grid_hedge               MAKER. Needs the same maker fill model
                              `PM_box_builder` needs, and needs it before its
                              own kill condition is even expressible.
  PM_dip_arb                  taker in AND taker out. SELL population. Needs its
                              own break-even carried alongside its win rate on
                              every row, and needs the scorer to charge
                              unsellable positions at realised resolution PnL,
                              same treatment as the fair-value family.
"""
from strategies.polymarket.base import (BINARY_STOP, BINARY_TARGET, PAPER_MODE,
                                        Decision, Leg, MarketContext,
                                        PolymarketStrategy, Window,
                                        cumulative_move, effective_ask_for,
                                        opposite, source_counts, streak,
                                        window_atr)
from strategies.polymarket.box_builder import BoxBuilder, cap_bids
from strategies.polymarket.corridor_collector import (CorridorCollector,
                                                      p_corridor_lookup)
from strategies.polymarket.corridor_pair_live import \
    CorridorPairLive
from strategies.polymarket.dip_arb import DipArb
from strategies.polymarket.fair_value_arb import ExitDecision, FairValueArb
from strategies.polymarket.fair_value_arb_hft import FairValueArbHFT
from strategies.polymarket.fair_value_arb_inverse import FairValueArbInverse
from strategies.polymarket.fair_value_arb_patient import FairValueArbPatient
from strategies.polymarket.fair_value_arb_wide import FairValueArbWide
from strategies.polymarket.grid_hedge import GridHedge
from strategies.polymarket.liq_cascade_chaser import LiqCascadeChaser
from strategies.polymarket.mid_price_continuation import MidPriceContinuation
from strategies.polymarket.near_liq_trigger import NearLiqTrigger
from strategies.polymarket.small_liq_continuation import SmallLiqContinuation
from strategies.polymarket.smart_money_copy import SmartMoneyCopy
from strategies.polymarket.spread_harvest_maker import SpreadHarvestMaker
from strategies.polymarket.streak_snapper import StreakSnapper
from strategies.polymarket.temporal_arbitrage import TemporalArbitrage
from strategies.polymarket.weather_arb import WeatherArb


def build_strategies():
    """Fresh instances of all nineteen. Order is stable for reproducible logs.

    New strategies are APPENDED, never inserted. The shadow loop's accounting
    identity is `evaluations == cycles * len(strategies)`, so a reordering
    would not break it - but every historical log line is keyed by position in
    somebody's head, and appending keeps a diff of the counters readable. The
    three fair-value variants are appended after `FairValueArb` for that reason,
    which also happens to keep the family contiguous.

    Fresh instances matter more than it looks: `TemporalArbitrage`,
    `SpreadHarvestMaker` and the whole `FairValueArb` family carry per-window
    state, so two callers sharing one instance would share a block ledger. The
    `FairValueArb` family additionally carries a BTC price TAPE, and two loops
    feeding one tape would interleave their observations into a series neither
    of them saw. Note that the four fair-value instances each keep their OWN
    tape - four independent copies of the same observations, which is wasteful
    and deliberate: a shared tape would couple their state and a bug in one
    would silently move the other three.

    `DipArb` carries a per-token price tape for the same reason and needs the
    same isolation. `GridHedge` carries per-window rung state. `SmartMoneyCopy`
    and `WeatherArb` cache feed reads per instance. Sharing any of them across
    two loops would merge two observation streams into one neither loop saw.
    """
    return [
        StreakSnapper(),
        MidPriceContinuation(),
        BoxBuilder(),
        CorridorCollector(),
        TemporalArbitrage(),
        CorridorPairLive(),
        SpreadHarvestMaker(),
        FairValueArb(),
        FairValueArbWide(),
        FairValueArbPatient(),
        FairValueArbHFT(),
        FairValueArbInverse(),
        LiqCascadeChaser(),
        SmallLiqContinuation(),
        NearLiqTrigger(),
        SmartMoneyCopy(),
        WeatherArb(),
        GridHedge(),
        DipArb(),
    ]


__all__ = [
    'PolymarketStrategy', 'MarketContext', 'Decision', 'Leg', 'Window',
    'BINARY_STOP', 'BINARY_TARGET', 'PAPER_MODE',
    'streak', 'window_atr', 'cumulative_move', 'opposite', 'source_counts',
    'effective_ask_for', 'cap_bids', 'p_corridor_lookup',
    'StreakSnapper', 'MidPriceContinuation', 'BoxBuilder', 'CorridorCollector',
    'TemporalArbitrage', 'CorridorPairLive', 'SpreadHarvestMaker',
    'FairValueArb', 'FairValueArbWide', 'FairValueArbPatient',
    'FairValueArbHFT', 'FairValueArbInverse', 'ExitDecision',
    'LiqCascadeChaser', 'SmallLiqContinuation', 'NearLiqTrigger',
    'SmartMoneyCopy', 'WeatherArb', 'GridHedge', 'DipArb',
    'build_strategies',
]
