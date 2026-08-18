import io
import sys

PATH = 'tests/test_fair_value_arb.py'
EDITS = []


def edit(old, new):
    EDITS.append((old, new))


edit(
    """    def test_the_reported_premium_is_the_walked_average_not_the_cap(self):
        # base.Leg.premium's house rule: reporting the cap as the entry is how
        # a binary backtest books a 47c fill as a 55c one.
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.58, 8.0), (0.62, 200.0))))

        leg = d.primary_leg
        assert d.action == 'ENTER', d.reason
        assert 0.58 < leg.expected_price < 0.62
        assert leg.expected_price != leg.limit_price""",
    """    def test_the_reported_premium_is_the_walked_average_not_the_cap(self):
        # base.Leg.premium's house rule: reporting the cap as the entry is how
        # a binary backtest books a 47c fill as a 55c one. Here the top level
        # is 8 shares and the order needs more, so the walked average must land
        # strictly between the two levels.
        s = FairValueArb()
        d = s.evaluate(_ctx(up_asks=((0.58, 8.0), (0.60, 200.0))))

        leg = d.primary_leg
        assert d.action == 'ENTER', d.reason
        assert leg.shares > 8
        assert 0.58 < leg.expected_price < 0.60
        assert leg.expected_price != leg.limit_price""")

edit(
    """    def test_it_takes_whichever_side_is_mispriced(self):
        # BTC below the open, so Down is the model's favourite and its ask is
        # the cheap one.
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=99_940.0, up_asks=((0.42, 200.0),),
                            down_asks=((0.60, 200.0),)))

        assert d.action == 'SKIP'   # Down's ask is RICH here, Up's is not cheap
        s2 = FairValueArb()
        d2 = s2.evaluate(_ctx(spot=99_940.0, up_asks=((0.60, 200.0),),
                              down_asks=((0.55, 200.0),)))
        assert d2.action == 'ENTER', d2.reason
        assert d2.features['outcome_side'] == 'Down'""",
    """    def test_it_takes_whichever_side_is_mispriced(self):
        # BTC $60 BELOW the open, so the model's favourite is Down and the
        # mispricing to look for is on the Down ask. Neither side cheap enough
        # -> skip; Down cheap -> Down entry. The Up side is never chosen here,
        # which is the point: the strategy follows the gap, not the direction.
        both_fair = FairValueArb().evaluate(
            _ctx(spot=99_940.0, up_asks=((0.42, 200.0),),
                 down_asks=((0.70, 200.0),)))
        assert both_fair.action == 'SKIP'
        assert both_fair.reason == 'edge_below_threshold'

        down_cheap = FairValueArb().evaluate(
            _ctx(spot=99_940.0, up_asks=((0.60, 200.0),),
                 down_asks=((0.55, 200.0),)))
        assert down_cheap.action == 'ENTER', down_cheap.reason
        assert down_cheap.features['outcome_side'] == 'Down'""")

edit(
    """    def test_twenty_shares_survives_when_the_cap_allows_it(self):
        # At a 0.50 cap, 20 shares is exactly $10 - the brief's stated sizing.
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=100_030.0, up_asks=((0.40, 300.0),)))
        assert d.action == 'ENTER', d.reason
        assert d.features['shares'] == fva.TARGET_SHARES""",
    """    def test_twenty_shares_survives_when_the_cap_allows_it(self):
        # BTC barely off the open, so fair value is near 0.52 and the entry cap
        # lands under 0.50 - where 20 shares fits inside the $10 per-trade cap,
        # which is the brief's stated sizing.
        s = FairValueArb()
        d = s.evaluate(_ctx(spot=100_005.0, up_asks=((0.40, 300.0),)))

        assert d.action == 'ENTER', d.reason
        assert d.features['entry_cap'] <= 0.50
        assert d.features['shares'] == fva.TARGET_SHARES
        assert d.features['shares_capped_by_notional'] is False""")

edit(
    """    def test_a_book_that_cannot_fill_the_size_under_the_cap_is_refused(self):
        # Depth passes the band gate on paper but every share above 4 is priced
        # over the cap, so the walk cannot complete.
        s = FairValueArb()
        fair = s.evaluate(_ctx()).features['side_fair_value']
        cap = floor_to_tick(fair - fva.EDGE_THRESHOLD)
        d = FairValueArb().evaluate(
            _ctx(up_asks=((round(cap - 0.05, 2), 4.0),
                          (round(cap + 0.01, 2), 500.0))))

        assert d.reason == 'unfillable_at_cap'""",
    """    def test_the_depth_gate_normally_makes_the_walk_gate_unreachable(self):
        # An invariant worth pinning rather than discovering later: the depth
        # band (3c) is NARROWER than the edge threshold (4c), so anything
        # counted as depth is priced strictly under the entry cap, and the
        # required size (<= 20) is always under the depth floor (50). With the
        # default constants `unfillable_at_cap` is therefore a defensive guard,
        # not a live branch. If someone widens DEPTH_BAND past EDGE_THRESHOLD
        # this stops holding and the walk gate becomes load-bearing.
        assert fva.DEPTH_BAND < fva.EDGE_THRESHOLD
        assert fva.TARGET_SHARES < fva.MIN_BOOK_DEPTH_SHARES

    def test_a_book_that_cannot_fill_the_size_under_the_cap_is_refused(self):
        # Reachable only with the depth floor relaxed (see the test above), so
        # the guard itself is still exercised: 4 shares sit under the cap and
        # everything else is priced over it, so the walk cannot complete and a
        # PARTIAL is not an entry.
        fair = FairValueArb().evaluate(_ctx()).features['side_fair_value']
        cap = floor_to_tick(fair - fva.EDGE_THRESHOLD)
        d = FairValueArb(min_book_depth_shares=1.0).evaluate(
            _ctx(up_asks=((round(cap - 0.05, 2), 4.0),
                          (round(cap + 0.05, 2), 500.0))))

        assert d.reason == 'unfillable_at_cap'
        assert d.features['shares'] > 4""")


def main() -> int:
    with io.open(PATH, encoding='utf-8') as f:
        text = f.read()
    for i, (old, new) in enumerate(EDITS, 1):
        n = text.count(old)
        if n != 1:
            sys.stderr.write('PATCH {} matched {} times\n'.format(i, n))
            return 1
        text = text.replace(old, new, 1)
    with io.open(PATH, 'w', encoding='utf-8') as f:
        f.write(text)
    print('patched', PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
