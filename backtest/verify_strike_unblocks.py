"""Prove the proxy strike actually unblocks the two strike-gated strategies.

This exists because "I fixed the wiring" is a claim and not a measurement
(convention 22: a claim in a docstring is not a wiring test). It runs the real
strategy objects against a real live context twice - once with the strike as
the shadow loop supplies it today (None), once with the measured proxy - and
prints what each strategy decided both times.

The bar being cleared is NOT "a strategy entered". Entering is a market
condition and cannot be summoned on demand. The bar is that the strategies stop
dying at the DATA gate and start reporting real decisions:

    before:  mid_price_continuation -> SKIP no_spot_or_strike     (never ran)
    after:   mid_price_continuation -> SKIP not_through_strike    (ran, declined)
                                    or ENTER                      (ran, acted)

Those are completely different facts. The first is NOT_TESTED, the second is a
result (convention 11).

It touches nothing the running shadow loop owns: no DB writes, no CSV rows, no
process signals. Read-only against the public APIs.

USAGE
    env -u PYTHONPATH python3 backtest/verify_strike_unblocks.py
    env -u PYTHONPATH python3 backtest/verify_strike_unblocks.py --polls 12 --interval 10
"""
import argparse
import logging
import os
import sys
import time
from collections import Counter
from typing import Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.client import PolymarketClient
from engine.polymarket.context import fetch_btc_spot_checked
from engine.polymarket.markets import current_window_ts, get_btc_updown_5m
from engine.polymarket.orderbook import fetch_orderbook
from engine.polymarket.strike import StrikeProxy, is_inside_noise_floor
from strategies.polymarket.base import MarketContext, Window

logger = logging.getLogger(__name__)

WINDOW_DURATION = 300

# The reasons that mean "this strategy never ran". Anything else means it ran.
DATA_GATE_REASONS = {'no_spot_or_strike', 'no_lead_or_atr',
                     'insufficient_window_history'}


def five_min_windows(session: requests.Session, lookback: int = 16):
    """Recent completed 5m windows with real USD magnitudes, oldest first."""
    resp = session.get('https://api.binance.us/api/v3/klines',
                       params={'symbol': 'BTCUSDT', 'interval': '5m',
                               'limit': lookback + 1},
                       timeout=15)
    if resp.status_code != 200:
        return []
    out = []
    for bar in resp.json()[:-1]:          # drop the in-progress bar
        try:
            ts = int(bar[0]) // 1000
            o, c = float(bar[1]), float(bar[4])
        except (TypeError, ValueError, IndexError):
            continue
        out.append(Window(ts=ts, open=o, close=c,
                          direction='UP' if c >= o else 'DOWN', source='price'))
    return out


def atr_bps(windows, spot: Optional[float]) -> Optional[float]:
    """ATR over the 5m windows, in BASIS POINTS.

    corridor_collector divides lead_bps by atr14, so the two must share units.
    A USD ATR divided into a bps lead is off by four orders of magnitude and
    would silently pass or fail every gate.
    """
    if not windows or not spot:
        return None
    ranges = [abs(w.close - w.open) for w in windows]
    if not ranges:
        return None
    return (sum(ranges) / len(ranges)) / spot * 10_000.0


def build(client: PolymarketClient, proxy: StrikeProxy, window_ts: int,
          use_proxy: bool) -> MarketContext:
    """A live context, with the proxy strike wired in or left out."""
    market = get_btc_updown_5m(client, window_ts)
    books = {}
    if market is not None:
        for outcome in market.outcomes:
            book = fetch_orderbook(client, outcome.token_id)
            if book is not None:
                books[outcome.token_id] = book

    spot = fetch_btc_spot_checked(client)['spot']
    windows = five_min_windows(client.session)

    strike = proxy.strike_for(window_ts)['strike'] if use_proxy else None
    lead = None
    if spot is not None and strike:
        lead = (spot - strike) / strike * 10_000.0

    return MarketContext(
        window_ts=window_ts, windows=windows, market=market, books=books,
        spot=spot, strike=strike,
        seconds_into_window=time.time() - window_ts,
        market_15m=None, books_15m={}, lead_bps=lead,
        atr14=atr_bps(windows, spot))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--polls', type=int, default=6)
    p.add_argument('--interval', type=float, default=10.0)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')

    from strategies.polymarket.corridor_collector import CorridorCollector
    from strategies.polymarket.mid_price_continuation import MidPriceContinuation

    strategies = [MidPriceContinuation(), CorridorCollector()]
    client = PolymarketClient()
    proxy = StrikeProxy(session=client.session)

    before: Counter = Counter()
    after: Counter = Counter()
    ran_after = 0
    total = 0

    print('=' * 72)
    print('DOES THE PROXY STRIKE UNBLOCK THE STRIKE-GATED STRATEGIES?')
    print('=' * 72)

    for poll in range(args.polls):
        window_ts = current_window_ts()
        ctx_off = build(client, proxy, window_ts, use_proxy=False)
        ctx_on = build(client, proxy, window_ts, use_proxy=True)

        inside = is_inside_noise_floor(ctx_on.lead_bps)
        print(f"\npoll {poll + 1}/{args.polls}  window={window_ts} "
              f"t+{ctx_on.seconds_into_window:.0f}s")
        print(f"  spot={ctx_on.spot}  strike(proxy)={ctx_on.strike}  "
              f"lead={None if ctx_on.lead_bps is None else round(ctx_on.lead_bps, 2)} bps  "
              f"atr={None if ctx_on.atr14 is None else round(ctx_on.atr14, 2)} bps")
        print(f"  inside measured noise floor: {inside}")

        for strat in strategies:
            name = getattr(strat, 'strategy_name', type(strat).__name__)
            total += 1

            d_off = strat.evaluate(ctx_off)
            r_off = d_off.reason or d_off.action
            before[r_off] += 1

            if inside:
                # The proxy cannot resolve a lead this small. Refuse with a
                # DISTINCT reason so it never pools with a real market
                # condition (convention 20).
                r_on = 'strike_inside_proxy_noise_floor'
                after[r_on] += 1
            else:
                d_on = strat.evaluate(ctx_on)
                r_on = d_on.reason or d_on.action
                after[r_on] += 1
                if r_on not in DATA_GATE_REASONS:
                    ran_after += 1

            moved = ' <-- now runs' if (r_off in DATA_GATE_REASONS
                                        and r_on not in DATA_GATE_REASONS
                                        and r_on != 'strike_inside_proxy_noise_floor') else ''
            print(f"    {name:<28} before={r_off:<26} after={r_on}{moved}")

        if poll < args.polls - 1:
            time.sleep(args.interval)

    print('\n' + '=' * 72)
    print('BEFORE (strike=None, what the loop does today)')
    for reason, n in before.most_common():
        gate = '  [DATA GATE: never ran]' if reason in DATA_GATE_REASONS else ''
        print(f"  {n:>4}  {reason}{gate}")
    print('\nAFTER (proxy strike + measured noise floor)')
    for reason, n in after.most_common():
        gate = '  [DATA GATE: never ran]' if reason in DATA_GATE_REASONS else ''
        print(f"  {n:>4}  {reason}{gate}")

    blocked_before = sum(n for r, n in before.items() if r in DATA_GATE_REASONS)
    blocked_after = sum(n for r, n in after.items() if r in DATA_GATE_REASONS)
    print('\n' + '-' * 72)
    print(f"evaluations                        : {total}")
    print(f"blocked at the data gate BEFORE    : {blocked_before}/{total}")
    print(f"blocked at the data gate AFTER     : {blocked_after}/{total}")
    print(f"reached real strategy logic AFTER  : {ran_after}/{total}")
    print('-' * 72)

    if blocked_after < blocked_before:
        print('RESULT: the strike gate is cleared. The strategies now reach their')
        print('        own logic instead of dying on missing data.')
        return 0
    print('RESULT: still blocked. The proxy did not supply a usable strike.')
    print('        This is NOT_TESTED, not a verdict on the strategies.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
