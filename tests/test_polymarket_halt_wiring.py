"""The kill switch reaches the Polymarket path.

This file exists because the wiring was CLAIMED in three places and present in
none of them. `engine/halt.py`, `engine/executor.py` and `botctl.py` all
described a HALT that covered Polymarket; `paper_adapter.py` did not import
`engine.halt` at all, so a halted operator would have kept opening binaries.
Nothing failed, because nothing tested the claim.

So these tests assert the wiring itself, not the adapter's behaviour in
isolation: that the halt is consulted, that it is consulted FIRST, that an
unreadable HALT still blocks, and that the block is visible in the log rather
than looking like a window where no strategy signalled.

Every test writes HALT to a tmp_path via monkeypatch rather than the real
repository root. Touching the actual kill switch from a test suite is how you
end up with a HALT file left behind by a crashed run, which then silently
blocks a real session.
"""
import json
import os

import pytest

from engine import halt
from engine.polymarket import paper_adapter
from engine.polymarket.paper_adapter import PolymarketPaperAdapter
from engine.polymarket.types import Orderbook, PriceLevel


@pytest.fixture
def halt_file(tmp_path, monkeypatch):
    """Redirect the kill switch at a tmp path.

    `halt.HALT_FILE` is the single definition (there is deliberately no config
    override), so a test that wants a different path has to patch the module.
    """
    path = tmp_path / 'HALT'
    monkeypatch.setattr(halt, 'HALT_FILE', str(path))
    return path


@pytest.fixture
def adapter(tmp_path):
    return PolymarketPaperAdapter(
        client=object(),
        config={'polymarket': {'notional_cap_usdc': 100.0, 'min_shares': 1}},
        log_dir=str(tmp_path / 'log'))


@pytest.fixture
def book():
    """A book deep enough that nothing but the halt can stop the fill."""
    return Orderbook(token_id='t',
                     bids=[PriceLevel(0.40, 500)],
                     asks=[PriceLevel(0.50, 500)])


def buy(adapter, book, strategy='streak_snapper'):
    return adapter.simulate_taker_buy(
        strategy, 'btc-up-or-down-5m', 't', 'Up',
        limit_price=0.55, shares=10, window_ts=1755000000, book=book)


class TestHaltBlocksEntries:

    def test_the_control_case_fills(self, adapter, book, halt_file):
        """Without this, a blocked test proves nothing.

        Every other test here asserts a None return. A None return is also what
        you get from a misconfigured fixture, so the suite needs one case that
        shows this order fills when the switch is clear.
        """
        assert buy(adapter, book) is not None

    def test_a_halt_blocks_the_entry(self, adapter, book, halt_file):
        halt.write_halt('drill')
        assert buy(adapter, book) is None
        assert adapter.open_positions() == []

    def test_an_unreadable_halt_still_blocks(self, adapter, book, halt_file):
        """Convention 11: an unreadable state is not an empty one.

        `is_halted()` tests presence, not parseability, precisely so that a
        truncated or hand-edited HALT cannot read as "not halted". A kill
        switch that fails open is not a kill switch.
        """
        halt_file.write_text('{not json at all')
        assert halt.is_halted()
        assert buy(adapter, book) is None

    def test_resuming_lets_entries_through_again(self, adapter, book, halt_file):
        halt_id = halt.write_halt('drill')
        assert buy(adapter, book) is None
        halt.clear_halt()
        assert buy(adapter, book) is not None
        assert isinstance(halt_id, str) and halt_id


class TestHaltIsCheckedFirst:

    def test_the_halt_outranks_every_other_guard(self, adapter, book, halt_file):
        """A halted window logs `halted`, not whichever other guard also fired.

        Order matters for the log, not just the outcome. This request violates
        the notional cap AND the price band AND the share minimum; if the halt
        were checked last, the operator would read `over_notional_cap` and
        conclude the halt had nothing to do with it.
        """
        halt.write_halt('drill')
        adapter.simulate_taker_buy('s', 'mkt', 't', 'Up',
                                   limit_price=9.99, shares=0, book=book)
        assert list(adapter.decision_counts) == ['SKIP:halted']

    def test_a_halted_window_reads_no_orderbook(self, adapter, halt_file):
        """A halt short-circuits before any network call.

        Passing `book=None` forces the adapter to fetch. If it reached the
        fetch while halted it would raise here, because the stub client has no
        HTTP verb at all.
        """
        halt.write_halt('drill')

        def explode(*_args, **_kwargs):
            raise AssertionError('fetched an orderbook while halted')

        # Patching the module-level name is what the adapter actually calls.
        original = paper_adapter.fetch_orderbook
        paper_adapter.fetch_orderbook = explode
        try:
            assert buy(adapter, None) is None
        finally:
            paper_adapter.fetch_orderbook = original


class TestHaltIsVisible:

    def test_the_skip_is_counted_and_categorised(self, adapter, book, halt_file):
        """Convention 20: a skip that is not counted did not happen.

        A halted session must not be indistinguishable from a quiet one. If the
        adapter returned None without a row, the log would show a session where
        no strategy ever signalled, and the halt would be invisible in exactly
        the post-mortem that needed it.
        """
        halt.write_halt('drill')
        buy(adapter, book)
        assert adapter.decision_counts == {'SKIP:halted': 1}

    def test_the_skip_reaches_the_csv(self, adapter, book, halt_file):
        halt.write_halt('drill')
        buy(adapter, book)
        with open(adapter.log_path) as fh:
            rows = fh.read().splitlines()
        assert len(rows) == 2, 'header plus exactly one decision row'
        assert 'halted' in rows[1]

    def test_the_summary_reports_the_halt(self, adapter, book, halt_file):
        """An operator reading `summary()` sees WHY the entry count is zero."""
        halt.write_halt('drill')
        buy(adapter, book)
        summary = adapter.summary()
        assert summary['halted'] is True
        assert summary['entries'] == 0
        # Convention 19: the summary is written to disk downstream, so a
        # non-finite anywhere in it has to fail here rather than in a reader.
        json.dumps(summary, allow_nan=False)

    def test_the_summary_is_not_halted_when_clear(self, adapter, book, halt_file):
        buy(adapter, book)
        assert adapter.summary()['halted'] is False


class TestOneDefinition:

    def test_the_adapter_uses_the_shared_halt_module(self):
        """The point of `engine/halt.py` is that there is only one of it.

        A second copy of the path is the failure mode the module was written to
        prevent: the crypto side halts, the Polymarket side keeps trading, and
        nothing says the two disagreed. Patching `halt.HALT_FILE` above works
        ONLY if the adapter resolves the path through that module, so this
        asserts the import rather than trusting the fixture.
        """
        assert paper_adapter.is_halted is halt.is_halted

    def test_no_second_halt_path_is_hardcoded(self):
        """No module under engine/polymarket/ builds its own HALT path."""
        pm_dir = os.path.dirname(paper_adapter.__file__)
        offenders = []
        for name in sorted(os.listdir(pm_dir)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(pm_dir, name)) as fh:
                for lineno, line in enumerate(fh, 1):
                    if "'HALT'" in line or '"HALT"' in line:
                        offenders.append(f'{name}:{lineno}')
        assert not offenders, (
            f'HALT path literal outside engine/halt.py: {offenders}')
