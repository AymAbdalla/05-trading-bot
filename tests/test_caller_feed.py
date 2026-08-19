"""Tests for `strategies.polymarket.caller_feed`. Offline only.

Every network call in this file goes through an injected `transport` stub, and
every write through the ledger points `db_path` at `tmp_path`, never at the
shared `db/trading.db` - matching `tests/test_concurrency.py`'s own
convention. A test here touching the real coordination database or the real
network is a test bug.

Five jobs:

  1. **Parsing must accept the documented format and refuse everything else,
     by NAME.** `TestExtractDeclaredPlay` walks one missing field at a time.
  2. **A listing payload must turn into plays AND a drop census that never
     silently drops anything.**
  3. **`CallerRecord` must dedupe declared plays across polls**, not double
     count them, and must never invent a verified outcome.
  4. **The TTL gate must be a cache, never a re-fetch**, and must distinguish
     "rate limited" from "fetched, found nothing" from "unreachable"
     (convention 11).
  5. **Every write must land on disk through `engine.concurrency`** so the
     pre-commit hook's hash check would pass against it.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import concurrency as C                        # noqa: E402
from strategies.polymarket.caller_feed import (              # noqa: E402
    CallerFeed, CallerRecord, DeclaredPlay, PARSE_DROP_REASONS,
    POLL_STATUS_CACHED, POLL_STATUS_FRESH,
    POLL_STATUS_UNREACHABLE_NO_CACHE, POLL_STATUS_UNREACHABLE_STALE_CACHE,
    caller_record_from_dict, extract_declared_play, load_caller_records,
    merge_declared_plays, parse_caller_posts, record_declared_plays)

HANDLE = 'zin1422'
POST_TS = 1755000000.0  # 2025-08-12T13:20:00Z


# ============ fixtures ============

def _listing(children):
    return {'kind': 'Listing', 'data': {'children': children}}


def _post(post_id, title='', selftext='', created_utc=POST_TS, kind='t3',
         body=None):
    data = {'id': post_id, 'title': title, 'selftext': selftext,
           'created_utc': created_utc}
    if body is not None:
        data['body'] = body
    return {'kind': kind, 'data': data}


class StubTransport:
    """The only transport these tests ever use. Touches nothing."""

    def __init__(self, payload=None, raise_exc=None):
        self.payload = payload
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.raise_exc is not None:
            raise self.raise_exc
        return json.dumps(self.payload)


def _feed(tmp_path, transport=None, clock=None, poll_interval_sec=3600.0):
    return CallerFeed(
        data_dir=str(tmp_path / 'caller_feed'),
        record_path=str(tmp_path / 'caller_record.json'),
        db_path=str(tmp_path / 'coord.db'),
        agent_id='cody-027-test',
        transport=transport, clock=clock,
        poll_interval_sec=poll_interval_sec)


# ============ 1. extract_declared_play ============

class TestExtractDeclaredPlay:
    def test_a_complete_labelled_play_parses(self):
        play, reason = extract_declared_play(
            HANDLE, 'p1', '$MRVL puts 200 strike 9/25', POST_TS)
        assert reason is None
        assert play.ticker == 'MRVL'
        assert play.direction == 'short'
        assert play.expiry == '2025-09-25'
        assert play.strike == 200.0
        assert play.play_id == 'zin1422:p1'

    def test_calls_and_long_both_normalise_to_long(self):
        for word in ('calls', 'call', 'long'):
            play, reason = extract_declared_play(
                HANDLE, 'p', '$NBIS {} 10/3'.format(word), POST_TS)
            assert reason is None, word
            assert play.direction == 'long', word

    def test_puts_and_short_both_normalise_to_short(self):
        for word in ('puts', 'put', 'short'):
            play, reason = extract_declared_play(
                HANDLE, 'p', '$NBIS {} 10/3'.format(word), POST_TS)
            assert reason is None, word
            assert play.direction == 'short', word

    def test_missing_dollar_sign_is_refused_not_guessed(self):
        # The task's own worked example format requires the '$' delimiter.
        # A bare ticker word is indistinguishable from English and must not
        # parse.
        play, reason = extract_declared_play(
            HANDLE, 'p2', 'MRVL puts 9/25 (no dollar sign)', POST_TS)
        assert play is None
        assert reason == 'no_ticker_found'

    def test_no_direction_word_is_refused(self):
        play, reason = extract_declared_play(
            HANDLE, 'p3', '$MRVL 9/25 thinking about this', POST_TS)
        assert play is None
        assert reason == 'no_direction_found'

    def test_no_date_pattern_is_refused(self):
        play, reason = extract_declared_play(
            HANDLE, 'p4', '$MRVL puts incoming soon', POST_TS)
        assert play is None
        assert reason == 'no_expiry_found'

    def test_an_impossible_calendar_date_is_refused(self):
        play, reason = extract_declared_play(
            HANDLE, 'p5', '$MRVL puts 13/45', POST_TS)
        assert play is None
        assert reason == 'unparseable_expiry_date'

    def test_a_bare_month_day_with_no_post_timestamp_is_refused(self):
        # Cannot roll a bare month/day forward into a year without knowing
        # "now" at post time.
        play, reason = extract_declared_play(
            HANDLE, 'p6', '$MRVL puts 9/25', post_ts=None)
        assert play is None
        assert reason == 'no_post_timestamp'

    def test_a_date_before_the_post_date_rolls_to_next_year(self):
        # POST_TS is 2025-08-12. A declared expiry of 1/15 (January) is
        # BEFORE that in calendar terms, so it must mean January of the
        # FOLLOWING year - nobody declares an already-expired play.
        play, reason = extract_declared_play(
            HANDLE, 'p7', '$MRVL puts 1/15', POST_TS)
        assert reason is None
        assert play.expiry == '2026-01-15'

    def test_a_date_after_the_post_date_stays_the_same_year(self):
        play, reason = extract_declared_play(
            HANDLE, 'p8', '$MRVL puts 9/25', POST_TS)
        assert reason is None
        assert play.expiry == '2025-09-25'

    def test_an_explicit_two_digit_year_is_read_as_2000s(self):
        play, reason = extract_declared_play(
            HANDLE, 'p9', '$MRVL puts 9/25/26', POST_TS)
        assert reason is None
        assert play.expiry == '2026-09-25'

    def test_no_strike_is_still_a_complete_play(self):
        # Strike is optional per the proposal; direction + ticker + expiry is
        # a complete, tradeable play.
        play, reason = extract_declared_play(
            HANDLE, 'p10', '$MRVL puts 9/25', POST_TS)
        assert reason is None
        assert play.strike is None

    def test_every_drop_reason_is_a_named_member_of_the_registry(self):
        cases = [
            'MRVL puts 9/25',
            '$MRVL 9/25',
            '$MRVL puts incoming',
            '$MRVL puts 13/45',
        ]
        for i, text in enumerate(cases):
            _play, reason = extract_declared_play(HANDLE, str(i), text, POST_TS)
            assert reason in PARSE_DROP_REASONS


# ============ 2. parse_caller_posts ============

class TestParseCallerPosts:
    def test_a_mixed_listing_splits_into_plays_and_a_named_drop_census(self):
        payload = _listing([
            _post('p1', title='$MRVL puts 200 strike 9/25'),
            _post('p2', title='no ticker here at all'),
            _post('p3', title='$NBIS calls 10/3'),
            {'kind': 't3'},  # no 'data' field at all
            'not-a-dict',
        ])
        plays, drops = parse_caller_posts(payload, HANDLE)
        assert {p.ticker for p in plays} == {'MRVL', 'NBIS'}
        assert drops['no_ticker_found'] == 1
        assert drops['no_data_field'] == 1
        assert drops['child_not_a_dict'] == 1
        # Accounting identity: every child landed in exactly one bucket.
        assert len(plays) + sum(drops.values()) == len(payload['data']['children'])

    def test_a_single_listing_dict_and_a_list_of_listings_both_parse(self):
        one = _listing([_post('p1', title='$MRVL puts 9/25')])
        plays_dict, _drops = parse_caller_posts(one, HANDLE)
        plays_list, _drops2 = parse_caller_posts([one], HANDLE)
        assert len(plays_dict) == 1
        assert len(plays_list) == 1
        assert plays_dict[0].ticker == plays_list[0].ticker

    def test_a_comment_body_parses_the_same_way_as_a_post_selftext(self):
        payload = _listing([
            _post('c1', title='', kind='t1', body='$NBIS calls 10/3'),
        ])
        plays, drops = parse_caller_posts(payload, HANDLE)
        assert len(plays) == 1
        assert plays[0].source_kind == 't1'

    def test_completely_unusable_payload_yields_no_plays_and_no_crash(self):
        plays, drops = parse_caller_posts({'unexpected': 'shape'}, HANDLE)
        assert plays == []
        assert drops == {}


# ============ 3. CallerRecord and merge_declared_plays ============

class TestCallerRecord:
    def test_declared_plays_seen_is_derived_from_play_ids(self):
        rec = CallerRecord(handle=HANDLE, play_ids=('a', 'b', 'c'))
        assert rec.declared_plays_seen == 3

    def test_a_fresh_record_is_unmeasured_with_zero_verified_plays(self):
        rec = CallerRecord(handle=HANDLE)
        assert rec.measured is False
        assert rec.verified_plays == 0

    def test_merge_deduplicates_the_same_play_seen_on_two_polls(self):
        play = DeclaredPlay(handle=HANDLE, play_id='zin1422:p1', ticker='MRVL',
                            direction='short')
        first = merge_declared_plays(None, HANDLE, [play], now=100.0)
        assert first.declared_plays_seen == 1
        # A second poll refetches the WHOLE listing, so the same play comes
        # back. Merging again must NOT double the count.
        second = merge_declared_plays(first, HANDLE, [play], now=200.0)
        assert second.declared_plays_seen == 1
        assert second.first_seen_ts == 100.0
        assert second.last_seen_ts == 200.0

    def test_merge_grows_the_count_only_for_a_genuinely_new_play_id(self):
        p1 = DeclaredPlay(handle=HANDLE, play_id='zin1422:p1', ticker='MRVL',
                          direction='short')
        p2 = DeclaredPlay(handle=HANDLE, play_id='zin1422:p2', ticker='NBIS',
                          direction='long')
        rec = merge_declared_plays(None, HANDLE, [p1], now=100.0)
        rec = merge_declared_plays(rec, HANDLE, [p1, p2], now=200.0)
        assert rec.declared_plays_seen == 2

    def test_merge_never_touches_verification_fields(self):
        existing = CallerRecord(handle=HANDLE, play_ids=('a',),
                                verified_plays=2, measured=True)
        merged = merge_declared_plays(existing, HANDLE, [], now=1.0)
        assert merged.verified_plays == 2
        assert merged.measured is True

    def test_round_trip_through_to_dict_and_from_dict(self):
        rec = CallerRecord(handle=HANDLE, play_ids=('b', 'a'), verified_plays=0,
                           measured=False, first_seen_ts=1.0, last_seen_ts=2.0)
        rebuilt = caller_record_from_dict(HANDLE, rec.to_dict())
        assert rebuilt.play_ids == ('a', 'b')  # sorted on the way in
        assert rebuilt.declared_plays_seen == 2
        assert rebuilt.measured is False

    def test_a_corrupt_field_in_the_dict_degrades_rather_than_raises(self):
        rebuilt = caller_record_from_dict(HANDLE, {'play_ids': 'not-a-list',
                                                    'verified_plays': 'nan'})
        assert rebuilt.play_ids == ()
        assert rebuilt.verified_plays == 0


# ============ 4. CallerFeed.poll: TTL, failure, persistence ============

class TestCallerFeedPoll:
    def test_a_successful_fetch_returns_fetched_fresh(self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        feed = _feed(tmp_path, transport=StubTransport(payload=payload))
        plays, drops, status = feed.poll(HANDLE)
        assert status == POLL_STATUS_FRESH
        assert len(plays) == 1
        assert drops == {}

    def test_within_the_ttl_window_a_second_poll_never_hits_the_transport(
            self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        transport = StubTransport(payload=payload)
        clock = {'t': 1000.0}
        feed = _feed(tmp_path, transport=transport,
                    clock=lambda: clock['t'], poll_interval_sec=3600.0)
        feed.poll(HANDLE)
        clock['t'] += 10.0  # well inside the hour window
        plays, _drops, status = feed.poll(HANDLE)
        assert status == POLL_STATUS_CACHED
        assert len(transport.calls) == 1  # never called twice
        assert len(plays) == 1  # the cached result, not empty

    def test_after_the_ttl_window_a_poll_refetches(self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        transport = StubTransport(payload=payload)
        clock = {'t': 1000.0}
        feed = _feed(tmp_path, transport=transport,
                    clock=lambda: clock['t'], poll_interval_sec=3600.0)
        feed.poll(HANDLE)
        clock['t'] += 3601.0
        _plays, _drops, status = feed.poll(HANDLE)
        assert status == POLL_STATUS_FRESH
        assert len(transport.calls) == 2

    def test_an_unreachable_mirror_with_no_prior_cache_returns_none_not_empty(
            self, tmp_path):
        feed = _feed(tmp_path, transport=StubTransport(
            raise_exc=IOError('connection refused')))
        plays, drops, status = feed.poll(HANDLE)
        # None, never [] - convention 11: unreachable and "fetched, quiet"
        # must never be the same value.
        assert plays is None
        assert status == POLL_STATUS_UNREACHABLE_NO_CACHE
        assert drops == {}

    def test_an_unreachable_mirror_after_a_prior_success_returns_stale_cache(
            self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        transport = StubTransport(payload=payload)
        clock = {'t': 1000.0}
        feed = _feed(tmp_path, transport=transport, clock=lambda: clock['t'],
                    poll_interval_sec=1.0)
        feed.poll(HANDLE)
        clock['t'] += 2.0
        transport.raise_exc = IOError('now it is down')
        plays, _drops, status = feed.poll(HANDLE)
        assert status == POLL_STATUS_UNREACHABLE_STALE_CACHE
        assert len(plays) == 1  # the STALE result, not None and not []

    def test_a_successful_fetch_persists_raw_posts_as_jsonl(self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25',
                                  created_utc=POST_TS)])
        feed = _feed(tmp_path, transport=StubTransport(payload=payload))
        feed.poll(HANDLE)
        jsonl_path = tmp_path / 'caller_feed' / '{}.jsonl'.format(HANDLE)
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row['created_utc'] == POST_TS  # the POST's own timestamp
        assert row['handle'] == HANDLE

    def test_two_polls_append_rather_than_overwrite_the_jsonl(self, tmp_path):
        payload1 = _listing([_post('p1', title='$MRVL puts 9/25')])
        payload2 = _listing([_post('p2', title='$NBIS calls 10/3')])
        transport = StubTransport(payload=payload1)
        clock = {'t': 1000.0}
        feed = _feed(tmp_path, transport=transport, clock=lambda: clock['t'],
                    poll_interval_sec=1.0)
        feed.poll(HANDLE)
        clock['t'] += 2.0
        transport.payload = payload2
        feed.poll(HANDLE)
        jsonl_path = tmp_path / 'caller_feed' / '{}.jsonl'.format(HANDLE)
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_a_successful_fetch_with_plays_writes_the_caller_record(
            self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        feed = _feed(tmp_path, transport=StubTransport(payload=payload))
        feed.poll(HANDLE)
        records = load_caller_records(str(tmp_path / 'caller_record.json'))
        assert HANDLE in records
        assert records[HANDLE].declared_plays_seen == 1
        assert records[HANDLE].measured is False

    def test_a_fetch_with_zero_parseable_plays_does_not_write_a_record(
            self, tmp_path):
        payload = _listing([_post('p1', title='nothing parseable here')])
        feed = _feed(tmp_path, transport=StubTransport(payload=payload))
        feed.poll(HANDLE)
        record_path = tmp_path / 'caller_record.json'
        assert not record_path.exists()

    def test_the_write_lands_through_the_ledger_and_the_hash_matches(
            self, tmp_path):
        payload = _listing([_post('p1', title='$MRVL puts 9/25')])
        db_path = str(tmp_path / 'coord.db')
        feed = _feed(tmp_path, transport=StubTransport(payload=payload))
        feed.poll(HANDLE)
        record_path = str(tmp_path / 'caller_record.json')
        on_disk_hash = C.hash_file(record_path)
        assert on_disk_hash is not None
        # The pre-commit hook's check IS this comparison: the file's current
        # hash must equal the hash `_log` recorded on the last checkin/write.
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT new_hash FROM file_coordination WHERE file_path LIKE "
            "'%caller_record.json' AND action='write' ORDER BY id DESC "
            "LIMIT 1").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == on_disk_hash


# ============ 5. record_declared_plays (direct) ============

class TestRecordDeclaredPlays:
    def test_creates_the_file_on_first_write(self, tmp_path):
        play = DeclaredPlay(handle=HANDLE, play_id='zin1422:p1', ticker='MRVL',
                            direction='short')
        path = str(tmp_path / 'caller_record.json')
        db = str(tmp_path / 'coord.db')
        rec = record_declared_plays(HANDLE, [play], path=path,
                                    agent_id='cody-027-test', now=1.0,
                                    db_path=db)
        assert rec.declared_plays_seen == 1
        assert os.path.exists(path)

    def test_a_second_call_merges_rather_than_replaces(self, tmp_path):
        p1 = DeclaredPlay(handle=HANDLE, play_id='zin1422:p1', ticker='MRVL',
                          direction='short')
        p2 = DeclaredPlay(handle=HANDLE, play_id='zin1422:p2', ticker='NBIS',
                          direction='long')
        path = str(tmp_path / 'caller_record.json')
        db = str(tmp_path / 'coord.db')
        record_declared_plays(HANDLE, [p1], path=path, now=1.0, db_path=db)
        rec = record_declared_plays(HANDLE, [p1, p2], path=path, now=2.0,
                                    db_path=db)
        assert rec.declared_plays_seen == 2

    def test_an_empty_play_list_is_a_no_op_on_disk(self, tmp_path):
        path = str(tmp_path / 'caller_record.json')
        db = str(tmp_path / 'coord.db')
        rec = record_declared_plays(HANDLE, [], path=path, now=1.0, db_path=db)
        assert rec.declared_plays_seen == 0
        assert not os.path.exists(path)
