"""Tests for `strategies.polymarket.market_mapper`. Pure functions, no network.

Every candidate market here is constructed in-process; `map_declared_play_
checked` never fetches anything, which is the whole point of the module's
"why this takes a market LIST, not a client" section. Three jobs:

  1. **A clean match survives every gate and nothing else does.**
  2. **Every distinct rejection cause gets its own counter** (convention 20) -
     `TestDropAccounting` builds one candidate per failure mode and asserts
     the specific reason, not just "it failed."
  3. **Ambiguity is refused, never resolved by a tiebreak** - two markets that
     both survive every gate must map to nobody.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import Market, Outcome              # noqa: E402
from strategies.polymarket.caller_feed import DeclaredPlay        # noqa: E402
from strategies.polymarket.market_mapper import (                  # noqa: E402
    NO_MAPPED_MARKET_REASON, map_declared_play, map_declared_play_checked,
    market_matches_ticker, within_expiry_window)

PLAY = DeclaredPlay(handle='zin1422', play_id='zin1422:p1', ticker='MRVL',
                    direction='short', expiry='2025-09-25', strike=200.0)


def _market(id_='m1', question='Will MRVL close above $80 by 9/25?',
           slug='will-mrvl-close-above-80-on-9-25', active=True,
           closed=False, end_date='2025-09-25T00:00:00Z', volume=50000.0):
    return Market(id=id_, question=question, slug=slug, condition_id=id_,
                 outcomes=(Outcome('Yes', 'tok-yes-' + id_),
                          Outcome('No', 'tok-no-' + id_)),
                 active=active, closed=closed, end_date=end_date,
                 volume=volume)


# ============ 1. market_matches_ticker: token-boundary discipline ============

class TestMarketMatchesTicker:
    def test_ticker_in_slug_matches(self):
        m = _market(slug='will-mrvl-close-above-80')
        assert market_matches_ticker(m, 'MRVL') is True

    def test_ticker_in_question_matches(self):
        m = _market(slug='some-slug', question='Will MRVL beat earnings?')
        assert market_matches_ticker(m, 'MRVL') is True

    def test_ticker_as_a_substring_of_an_unrelated_word_does_not_match(self):
        # 'F' (Ford) must not match inside 'FOMC' or 'FED'.
        m = _market(slug='fomc-rate-decision', question='Fed rate decision')
        assert market_matches_ticker(m, 'F') is False

    def test_ticker_as_a_prefix_of_a_longer_ticker_does_not_match(self):
        m = _market(slug='mrvlx-fund-thing', question='MRVLX fund')
        assert market_matches_ticker(m, 'MRVL') is False

    def test_case_insensitive(self):
        m = _market(slug='will-mrvl-close-above-80')
        assert market_matches_ticker(m, 'mrvl') is True

    def test_no_match_when_ticker_absent_from_both_fields(self):
        m = _market(slug='will-nbis-report-strong', question='NBIS earnings')
        assert market_matches_ticker(m, 'MRVL') is False


# ============ 2. within_expiry_window ============

class TestWithinExpiryWindow:
    def test_exact_match(self):
        assert within_expiry_window('2025-09-25T00:00:00Z', '2025-09-25') is True

    def test_within_tolerance(self):
        assert within_expiry_window('2025-09-26T00:00:00Z', '2025-09-25',
                                    tolerance_days=1) is True

    def test_outside_tolerance(self):
        assert within_expiry_window('2025-09-30T00:00:00Z', '2025-09-25',
                                    tolerance_days=1) is False

    def test_unreadable_market_date_is_false(self):
        assert within_expiry_window(None, '2025-09-25') is False
        assert within_expiry_window('not-a-date', '2025-09-25') is False

    def test_unreadable_declared_expiry_is_false(self):
        assert within_expiry_window('2025-09-25T00:00:00Z', None) is False


# ============ 3. map_declared_play_checked: the happy path ============

class TestMapDeclaredPlayHappyPath:
    def test_a_clean_market_is_mapped(self):
        m = _market()
        result = map_declared_play_checked(PLAY, [m])
        assert result['market'] is m
        assert result['reason'] is None
        assert result['drops'] == {}

    def test_map_declared_play_returns_the_same_answer_as_the_checked_variant(
            self):
        m = _market()
        market, reason = map_declared_play(PLAY, [m])
        assert market is m
        assert reason is None


# ============ 4. every distinct rejection cause ============

class TestDropAccounting:
    def test_wrong_ticker_is_dropped_and_never_reaches_the_other_gates(self):
        m = _market(slug='will-nbis-report-strong', question='NBIS earnings')
        result = map_declared_play_checked(PLAY, [m])
        assert result['market'] is None
        assert result['reason'] == NO_MAPPED_MARKET_REASON
        assert result['drops'] == {'ticker_mismatch': 1}
        assert result['ticker_matches'] == 0

    def test_closed_market_is_dropped(self):
        m = _market(closed=True)
        result = map_declared_play_checked(PLAY, [m])
        assert result['drops'].get('closed') == 1
        assert result['market'] is None

    def test_inactive_market_is_dropped(self):
        m = _market(active=False)
        result = map_declared_play_checked(PLAY, [m])
        assert result['drops'].get('inactive') == 1

    def test_unreadable_market_end_date_is_dropped(self):
        m = _market(end_date=None)
        result = map_declared_play_checked(PLAY, [m])
        assert result['drops'].get('market_end_date_unreadable') == 1

    def test_declared_expiry_unreadable_is_dropped(self):
        no_expiry_play = DeclaredPlay(handle='zin1422', play_id='p2',
                                      ticker='MRVL', direction='short',
                                      expiry=None)
        m = _market()
        result = map_declared_play_checked(no_expiry_play, [m])
        assert result['drops'].get('declared_expiry_unreadable') == 1

    def test_expiry_out_of_window_is_dropped(self):
        m = _market(end_date='2025-11-25T00:00:00Z')
        result = map_declared_play_checked(PLAY, [m])
        assert result['drops'].get('expiry_out_of_window') == 1

    def test_volume_unreadable_is_dropped(self):
        m = _market(volume=None)
        result = map_declared_play_checked(PLAY, [m])
        assert result['drops'].get('volume_unreadable') == 1

    def test_volume_below_floor_is_dropped(self):
        m = _market(volume=100.0)
        result = map_declared_play_checked(PLAY, [m], min_volume_usdc=10000.0)
        assert result['drops'].get('volume_below_floor') == 1

    def test_volume_exactly_at_the_floor_is_dropped_strictly(self):
        m = _market(volume=10000.0)
        result = map_declared_play_checked(PLAY, [m], min_volume_usdc=10000.0)
        assert result['drops'].get('volume_below_floor') == 1

    def test_a_mixed_candidate_list_reports_every_distinct_reason(self):
        good = _market(id_='good')
        wrong_ticker = _market(id_='wt', slug='nbis-thing', question='NBIS')
        closed = _market(id_='cl', closed=True)
        illiquid = _market(id_='il', volume=50.0)
        wrong_date = _market(id_='wd', end_date='2025-12-01T00:00:00Z')
        result = map_declared_play_checked(
            PLAY, [good, wrong_ticker, closed, illiquid, wrong_date])
        assert result['market'] is good
        assert result['drops'] == {
            'ticker_mismatch': 1, 'closed': 1, 'volume_below_floor': 1,
            'expiry_out_of_window': 1,
        }


# ============ 5. ambiguity is refused, never resolved by a tiebreak ============

class TestAmbiguity:
    def test_two_equally_valid_candidates_map_to_neither(self):
        a = _market(id_='a', volume=50000.0)
        b = _market(id_='b', volume=90000.0)  # would win a naive tiebreak
        result = map_declared_play_checked(PLAY, [a, b])
        assert result['market'] is None
        assert result['reason'] == NO_MAPPED_MARKET_REASON
        assert result['drops']['ambiguous_multiple_survivors'] == 1

    def test_ambiguity_is_distinguishable_from_a_zero_candidate_miss(self):
        a = _market(id_='a')
        b = _market(id_='b')
        ambiguous = map_declared_play_checked(PLAY, [a, b])
        no_match = map_declared_play_checked(
            PLAY, [_market(id_='c', slug='nbis', question='NBIS')])
        assert ambiguous['reason'] == no_match['reason'] == NO_MAPPED_MARKET_REASON
        assert 'ambiguous_multiple_survivors' in ambiguous['drops']
        assert 'ambiguous_multiple_survivors' not in no_match['drops']


# ============ 6. no candidates at all ============

class TestEmptyInput:
    def test_no_markets_at_all_yields_the_named_reason(self):
        result = map_declared_play_checked(PLAY, [])
        assert result['market'] is None
        assert result['reason'] == NO_MAPPED_MARKET_REASON
        assert result['drops'] == {}
        assert result['ticker_matches'] == 0

    def test_a_none_entry_in_the_list_is_skipped_without_raising(self):
        m = _market()
        result = map_declared_play_checked(PLAY, [None, m])
        assert result['market'] is m
