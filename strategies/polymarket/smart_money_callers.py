"""Smart Money Callers: mirror a NAMED caller's declared stock play.

Proposal 027 (`strategies/proposals/027-pm-smart-money-callers.md`).
`smart_money_copy`'s pattern (follow a tracked source, mirror the SIDE, hold
to resolution) with a different and higher-signal source: a named human with a
published, checkable record (u/zin1422, r/wallstreetbets), not an anonymous
wallet. Read `strategies/polymarket/caller_feed.py` first - it is where a
Reddit post becomes a `DeclaredPlay` - and `strategies/polymarket/market_
mapper.py` second - it is where a `DeclaredPlay` becomes a candidate
`Market`. This file is the third and last step: given a `MarketContext`
wrapping ONE market, does any watched caller's declared play map to exactly
this market, and if so, mirror it.

## Why `supported_market_types` is narrowed to `(MARKET_TYPE_EVENT,)`

The task brief leaves this as a judgement call, to be made from what Task 2's
research actually found, not from the proposal's more general
"event/sports/political" phrasing. `market_mapper.py`'s module docstring
walks the evidence in `engine/polymarket/markets.py`: stock-price markets are
found either by full-text search (`search_markets`, Gamma's `/public-search`)
or by clearing the general volume floor (`search_event_markets`). Neither
`search_sports_markets` nor `search_political_markets` searches anything a
single-stock price market could plausibly be tagged under - their tag lists
are NFL/NBA/MLB/... and elections/Fed/Congress/... respectively, fixed lists
with no stock-ticker entry. So this strategy declares `(MARKET_TYPE_EVENT,)`
only. If a future caller ever declares a play on something Gamma tags as
sports or political (a sports-team stock spinoff, a policy-linked equity
event), that is a genuinely new case and widening this declaration is the
correct way to opt in - silently accepting a type this file never checked
its own matching logic against would be exactly the routing hazard
`PolymarketStrategy.assert_supports`'s docstring in `base.py` warns about.

## THE DIRECTION MAPPING, the single most dangerous bug in this file

A declared play's `direction` is 'long' or 'short' (calls/long vs puts/short
- see `caller_feed.DeclaredPlay`). A Polymarket stock-event market is framed
as a yes/no question ("Will MRVL close above $X by date Y?"), not as a
call/put. The mapping this file uses, and ONLY this mapping:

    direction == 'long'   ->  the market's 'Yes' outcome, or 'Up' if the
                              market uses that label instead of Yes/No
    direction == 'short'  ->  the market's 'No' outcome, or 'Down'

`outcome_side_for_direction` below is the SINGLE place this happens
(convention 23). It tries 'Yes'/'Up' (or 'No'/'Down') by NAME via
`Market.outcome()`, which is case-insensitive lookup - never by index, for
the exact reason `engine/polymarket/types.py`'s `Market` docstring warns
about: index 0 is not reliably the bullish side. If a market uses neither
label pair, this function returns None and `evaluate` refuses with
`outcome_side_unresolvable` - a market whose bullish/bearish framing cannot be
read is a market this file will not guess a side for.

**What this mapping assumes, stated because it is a real assumption
(convention 17):** that a Polymarket stock-event market's 'Yes'/'Up' side is
always the BULLISH resolution (the thing a long/calls play profits from). All
markets like this observed on Polymarket to date are framed that way, but
nothing here VERIFIES the question text agrees with the label before trading
- doing so would need reading the question text against the direction, which
is future work and is not built. This is why `market_mapper` only ever maps
by ticker/date/liquidity/status and never by direction: getting the SIDE
wrong is a strategy-layer risk, not a mapping-layer one, and keeping it in
one file (this one) is what makes it auditable in one place.

## Sizing: FIXED at 5 shares, always. No Kelly, no scaling. Read this before
## reading the entry gate below

The proposal's `entry_exit_rules` describes a Kelly-sized bankroll capped at
the strategy's position limit, with "the first 3 plays run at the minimum
size" implying larger plays could follow once a caller has 3+ VERIFIED plays.
The task instructions for this build override that with an explicit fixed
size: `CALLER_SHARES = 5`, hard cap, matching `MIN_SHARES` elsewhere in this
package. There is no code path in this file that ever sizes ABOVE
`CALLER_SHARES` - `MIN_SHARES` and `MAX_SHARES` are the SAME constant on
purpose, so "the first 3 plays run at minimum size" is trivially and always
true: every play, the first and the hundredth, runs at minimum size, because
minimum and maximum are identical here. Building genuine Kelly-based
up-sizing once a caller clears 3+ VERIFIED plays is real future work, gated on
the resolution oracle `caller_feed.py`'s module docstring says does not exist
yet - `caller_declared_plays_seen` and `caller_verified_plays` are stamped on
every ENTER row precisely so that future work has the numbers it needs
without re-deriving them, but nothing here acts on them beyond the entry gate
below.

## The entry gate: no trade until the caller record exists

Per Task 4's requirement and the proposal's demand that "a caller must have
at least 3 declared plays with verifiable outcomes before any capital is
allocated": this strategy refuses to enter on ANY caller it has never
recorded in `data/caller_record.json` (or the injected `caller_records`
mapping in a test). `caller_feed.CallerFeed.poll()` is what CREATES that
record, the first time it successfully parses a declared play for a handle -
so the gate is really "has Task 1's machinery ever seen this caller say
anything parseable," which is the bootstrap the proposal's "first 3 plays"
language describes. It is deliberately NOT gated on `verified_plays >= 3`:
`verified_plays` cannot become nonzero without a resolution oracle this task
does not build (convention 11 - NOT_TESTED must never read as "tested and
failed"), and gating entry on a number that can never move would make this
strategy permanently, silently inert while looking like it was evaluating a
real threshold.

## Exits

Holds to resolution, exactly like `smart_money_copy` and every non-fair
-value strategy in this package. `manages_exits = False`; the stop is the
structural `BINARY_STOP = 0.00` (convention 8 - a losing binary share is
worth exactly zero, which IS strictly below any positive entry premium).

KILL CONDITION (from the proposal, restated here for anyone reading only this
file): after the caller feed is wired, retire this strategy if it enters on
fewer than 1% of evaluations over 500+ shadow cycles
(`agents/forge_shadow_eval.py` against `db/trading.db`) AND scores no better
than 0 net cents per share over 100+ resolved positions
(`backtest/polymarket_harness.py`). A caller whose declared plays lose 10 or
more consecutive resolved positions at this fixed size is dropped from the
watchlist - that drop is an OPERATIONAL decision on the watchlist, not
something this file automates; nothing here removes a handle from
`CALLER_HANDLES` on its own.
"""
import logging
from typing import Dict, List, Optional, Sequence, Tuple

from strategies.polymarket.base import (MARKET_TYPE_EVENT, Decision, Leg,
                                        MarketContext, PolymarketStrategy,
                                        effective_ask_for)
from strategies.polymarket.caller_feed import (CALLER_RECORD_PATH,
                                               CallerFeed, CallerRecord,
                                               DeclaredPlay,
                                               load_caller_records)
from strategies.polymarket.market_mapper import (DEFAULT_MIN_EVENT_VOLUME_USDC,
                                                 EXPIRY_TOLERANCE_DAYS,
                                                 map_declared_play_checked)
# Reused rather than re-derived (convention 23): the same break-even
# discipline `smart_money_copy` already applies to a taker entry on a binary.
from strategies.polymarket.smart_money_copy import MAX_ENTRY_PRICE

logger = logging.getLogger(__name__)

# Never False in this repo. Nothing here has live-trading authority: this
# module imports no wallet, no signer, no order path, and the feed it reads
# (`caller_feed.CallerFeed`) is read-only by the same structural argument
# `WalletTradeFeed` makes.
PAPER_MODE = True

#: Callers this strategy watches. One entry to start, per the proposal
#: ("Start with one caller"). Adding a second handle is a watchlist decision,
#: not a code change - the loop over `self.callers` in `evaluate` already
#: treats every handle identically.
CALLER_HANDLES: Tuple[str, ...] = ('zin1422',)

#: Fixed size, hard cap, both the floor and the ceiling. See the module
#: docstring's sizing section for why there is deliberately no distinction
#: between a "minimum" and a "maximum" here.
CALLER_SHARES = 5

#: Shares that must rest within `CALLER_DEPTH_BAND` of the best ask before an
#: entry is sized. Same discipline and same magnitude as `smart_money_copy`'s
#: `MIN_BOOK_DEPTH_SHARES`/`DEPTH_BAND` - a whale-sized fill against a 6-share
#: top level says nothing about what a 5-share order can actually get, and
#: neither does a caller-sized one.
CALLER_MIN_BOOK_DEPTH_SHARES = 50.0
CALLER_DEPTH_BAND = 0.03

#: Declared-play ids remembered so a play already entered is not re-entered
#: every cycle the shadow loop happens to re-evaluate the same market. Bounded
#: for the same reason `smart_money_copy.COPIED_IDS_KEPT` is: a long session
#: must not grow this without limit.
ENTERED_PLAYS_KEPT = 2000

#: The verified-play threshold the proposal names. Stamped on every ENTER row
#: as `caller_size_scaling_gate_min_verified_plays` so a reader can see WHAT
#: number would gate up-sizing once it exists, even though nothing here acts
#: on it yet (see the module docstring's sizing section).
CALLER_MIN_VERIFIED_PLAYS_FOR_SIZE_UP = 3


def outcome_side_for_direction(direction: str, market) -> Optional[str]:
    """'long'/'short' -> the market's own outcome NAME, or None.

    See the module docstring's "THE DIRECTION MAPPING" section before
    touching this function. Tries the bullish/bearish label pair the market
    actually uses (Yes/No, then Up/Down) and returns None rather than
    guessing when neither pair is present.
    """
    if direction == 'long':
        candidates = ('Yes', 'Up')
    elif direction == 'short':
        candidates = ('No', 'Down')
    else:
        return None
    for name in candidates:
        outcome = market.outcome(name) if market is not None else None
        if outcome is not None:
            return outcome.name
    return None


class SmartMoneyCallers(PolymarketStrategy):
    """Mirror a watched caller's declared play on the ONE market this maps to.

    Kill condition: see the module docstring's final section. This strategy
    is NOT_TESTED (D-268) like every other Polymarket strategy in this
    package until the resolution-PnL harness exists.
    """

    strategy_name = 'PM_smart_money_callers'
    paper_mode = PAPER_MODE

    #: See the module docstring's ruling on why this is narrowed from the
    #: full `GENERAL_BINARY_MARKET_TYPES` to `event` alone.
    supported_market_types = (MARKET_TYPE_EVENT,)

    #: Holds to resolution. The shadow loop reads this to decide whether to
    #: poll an exit.
    manages_exits = False

    #: Stamped on every row so a caller-discovered sample can be told apart
    #: from a self-selected one later, matching `smart_money_copy.
    #: discovery_path`'s reasoning.
    discovery_path = 'followed_a_declared_caller_play'

    def __init__(self, feed: Optional[CallerFeed] = None,
                 callers: Sequence[str] = CALLER_HANDLES,
                 caller_records: Optional[Dict[str, CallerRecord]] = None,
                 record_path: str = CALLER_RECORD_PATH,
                 shares: int = CALLER_SHARES,
                 max_entry_price: float = MAX_ENTRY_PRICE,
                 min_book_depth_shares: float = CALLER_MIN_BOOK_DEPTH_SHARES,
                 depth_band: float = CALLER_DEPTH_BAND,
                 min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
                 tolerance_days: int = EXPIRY_TOLERANCE_DAYS):
        #: Injected the way `shadow_loop` injects `candle_source`, and the way
        #: `SmartMoneyCopy` injects `trade_feed`. A default is built only when
        #: nothing was supplied, so a test that passes a stub feed can never
        #: fall through to the network.
        self.feed = feed if feed is not None else CallerFeed()
        self.callers = tuple(callers)
        #: When provided (a test, typically), used AS GIVEN and never reloaded
        #: from disk - this is what lets a test exercise "no caller record
        #: exists yet" without touching the filesystem. `None` means "read
        #: `record_path` fresh on every `evaluate()` call," which is cheap (a
        #: small JSON file, no network) and correct: Task 1's feed can update
        #: that file between shadow-loop cycles and this strategy must see the
        #: update without a restart.
        self._injected_caller_records = caller_records
        self.record_path = record_path
        self.shares = int(shares)
        self.max_entry_price = float(max_entry_price)
        self.min_book_depth_shares = float(min_book_depth_shares)
        self.depth_band = float(depth_band)
        self.min_volume_usdc = float(min_volume_usdc)
        self.tolerance_days = int(tolerance_days)
        self._entered_play_ids: List[str] = []
        self._entered_play_id_set = set()

    # -- dedupe ---------------------------------------------------------

    def _already_entered(self, play_id: str) -> bool:
        return play_id in self._entered_play_id_set

    def _note_entered(self, play_id: str) -> None:
        if play_id in self._entered_play_id_set:
            return
        self._entered_play_ids.append(play_id)
        self._entered_play_id_set.add(play_id)
        while len(self._entered_play_ids) > ENTERED_PLAYS_KEPT:
            self._entered_play_id_set.discard(self._entered_play_ids.pop(0))

    # -- caller records ---------------------------------------------------

    def caller_records(self) -> Dict[str, CallerRecord]:
        if self._injected_caller_records is not None:
            return self._injected_caller_records
        return load_caller_records(self.record_path)

    # -- entry ------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        # RAISES on a type we did not declare (convention 22). See the module
        # docstring's ruling on why `supported_market_types` is `event` only.
        self.assert_supports(ctx)

        market = ctx.market
        slug = getattr(market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            feats.setdefault('discovery_path', self.discovery_path)
            feats.setdefault('market_type',
                             getattr(ctx, 'market_type', MARKET_TYPE_EVENT))
            feats.setdefault('exits_before_resolution', False)
            feats.setdefault('sizing_model', 'fixed_hard_cap_no_kelly')
            feats.setdefault('shares_hard_cap', self.shares)
            feats.setdefault('callers_watched', list(self.callers))
            feats.setdefault(
                'caller_size_scaling_gate_min_verified_plays',
                CALLER_MIN_VERIFIED_PLAYS_FOR_SIZE_UP)
            feats.setdefault('claimed_track_record_is_unverified_vendor_number',
                             True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if market is None:
            return decide('SKIP', 'no_market')

        # -- gather declared plays from every watched caller -----------
        all_plays: List[DeclaredPlay] = []
        feed_failures: List[str] = []
        parse_drops: Dict[str, int] = {}
        callers_fetched = 0
        for handle in self.callers:
            try:
                fetched, drops, status = self.feed.poll(handle)
            except Exception as exc:                      # noqa: BLE001
                logger.warning('caller feed raised for %s: %s', handle, exc)
                fetched, drops, status = None, {}, 'poll_raised'
            for key, n in (drops or {}).items():
                parse_drops[key] = parse_drops.get(key, 0) + n
            if fetched is None:
                feed_failures.append(handle)
                continue
            callers_fetched += 1
            all_plays.extend(fetched)

        feats = {
            'callers_queried': len(self.callers),
            'callers_fetched': callers_fetched,
            'callers_feed_failed': len(feed_failures),
            'feed_failed_handles': sorted(feed_failures),
            'parse_drops': dict(parse_drops),
            'declared_plays_seen_this_cycle': len(all_plays),
        }

        if callers_fetched == 0:
            # Could not run. Never "we looked and nobody posted" - convention
            # 11, same split `WalletTradeFeed.fetch_trades` makes between a
            # failed read and a genuinely empty one.
            return decide('SKIP', 'caller_feed_unavailable', **feats)

        if not all_plays:
            return decide('SKIP', 'no_declared_plays', **feats)

        # -- the caller-record gate: no entry before the caller is tracked --
        records = self.caller_records()
        feats['caller_records_known'] = sorted(records.keys())

        candidates: List[Tuple[DeclaredPlay, object]] = []
        map_drops: Dict[str, int] = {}
        untracked_caller_plays = 0
        for play in all_plays:
            record = records.get(play.handle)
            if record is None:
                untracked_caller_plays += 1
                continue
            result = map_declared_play_checked(
                play, [market], min_volume_usdc=self.min_volume_usdc,
                tolerance_days=self.tolerance_days)
            for key, n in (result['drops'] or {}).items():
                map_drops[key] = map_drops.get(key, 0) + n
            if result['market'] is None:
                continue
            candidates.append((play, record))

        feats['declared_plays_from_untracked_callers'] = untracked_caller_plays
        feats['map_drops'] = dict(map_drops)

        if not candidates and untracked_caller_plays and not map_drops:
            # EVERY play this cycle came from a caller with no record yet, and
            # nothing else was even attempted. Its own reason, never folded
            # into the generic "no play maps to this market" below - one is a
            # bootstrap gate, the other is a mapping miss (convention 20).
            return decide('SKIP', 'caller_record_unknown', **feats)

        if not candidates:
            return decide('SKIP', 'no_declared_play_for_market', **feats)

        # Newest declared play first. A play with no timestamp sorts last.
        candidates.sort(
            key=lambda pr: (pr[0].post_ts is not None, pr[0].post_ts or 0.0),
            reverse=True)
        fresh = [(p, r) for p, r in candidates
                if not self._already_entered(p.play_id)]
        feats['candidates_already_entered'] = len(candidates) - len(fresh)
        if not fresh:
            return decide('SKIP', 'already_entered_this_play', **feats)

        play, record = fresh[0]
        feats.update({
            'declared_handle': play.handle,
            'declared_ticker': play.ticker,
            'declared_direction': play.direction,
            'declared_expiry': play.expiry,
            'declared_strike': play.strike,
            'declared_play_id': play.play_id,
            'caller_declared_plays_seen': record.declared_plays_seen,
            'caller_verified_plays': record.verified_plays,
            'caller_record_measured': record.measured,
        })

        side = outcome_side_for_direction(play.direction, market)
        if side is None:
            return decide('SKIP', 'outcome_side_unresolvable', **feats)
        feats['outcome_side'] = side

        book = ctx.book(side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        feats['max_entry_price'] = self.max_entry_price
        if best_ask > self.max_entry_price:
            return decide('SKIP', 'ask_above_max_entry_price', **feats)

        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['min_book_depth_shares'] = self.min_book_depth_shares
        if depth < self.min_book_depth_shares:
            return decide('SKIP', 'insufficient_book_depth', **feats)

        effective = effective_ask_for(book, self.shares, self.max_entry_price)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            # The book cannot fill CALLER_SHARES under the price cap. Never a
            # partial fill counted as an entry (convention 12's discipline,
            # matching `smart_money_copy.unfillable_at_cap`).
            return decide('SKIP', 'book_cannot_fill', **feats)
        if effective > self.max_entry_price:
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        feats['shares'] = self.shares
        feats['notional_usdc'] = round(self.shares * effective, 4)
        feats['limit_price'] = self.max_entry_price
        feats['breakeven_win_rate'] = round(effective, 4)

        self._note_entered(play.play_id)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=self.max_entry_price,
                                order_type='taker',
                                shares=self.shares,
                                expected_price=effective)],
                      **feats)
