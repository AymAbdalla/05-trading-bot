"""Tests for agents/vault_digest.py, the rolling write-time digest.

Three things pinned here, matching the vault-digest handoff's own list:

  1. Cap enforcement: appending past DIGEST_CAP_CHARS drops the OLDEST
     entries into the archive file rather than deleting them.
  2. The digest and archive files are written through
     `engine.concurrency`, never a plain overwrite, and a brand new digest
     (no file on disk yet) does not raise.
  3. The read side: `digested_names` / `newest_entry_date` / `is_stale` /
     `extract_fields` are the primitives `agents/forge_reasoner.py` and
     `agents/vault_writer.py` build on, so they are pinned directly rather
     than only through their callers.

Every test uses `tmp_path` for the digest file, the archive file AND the
coordination ledger DB. Nothing here touches the real vault or db/trading.db.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import vault_digest as vd  # noqa: E402


@pytest.fixture
def paths(tmp_path):
    return {
        'digest': str(tmp_path / '_DIGEST.md'),
        'archive': str(tmp_path / '_DIGEST-ARCHIVE.md'),
        'db': str(tmp_path / 'coord.db'),
    }


def _entry(date, name, verdict='v', evidence='e', relevance='r'):
    return vd.format_entry(date, name, verdict, evidence, relevance)


# ---------------------------------------------------------------------------
# format_entry / parse_entries
# ---------------------------------------------------------------------------

def test_format_entry_has_the_three_required_bold_lines():
    text = vd.format_entry('2026-08-18', 'foo.md', 'It failed.',
                           '616 trades, -$338.60', 'Do not retry this.')
    assert text.startswith('## 2026-08-18 foo.md\n')
    assert '**Verdict:** It failed.' in text
    assert '**Evidence:** 616 trades, -$338.60' in text
    assert '**Relevance to Forge:** Do not retry this.' in text


def test_parse_entries_splits_on_heading_boundaries():
    text = _entry('2026-08-18', 'a.md') + _entry('2026-08-19', 'b.md')
    entries = vd.parse_entries(text)
    assert len(entries) == 2
    assert entries[0].startswith('## 2026-08-18 a.md')
    assert entries[1].startswith('## 2026-08-19 b.md')


def test_parse_entries_on_empty_text_is_empty():
    assert vd.parse_entries('') == []
    assert vd.parse_entries('   \n  ') == []


def test_parse_entries_strips_the_archive_pointer_line():
    text = '(older entries archived to Trading/_DIGEST-ARCHIVE.md)\n\n' + \
        _entry('2026-08-18', 'a.md')
    entries = vd.parse_entries(text)
    assert len(entries) == 1
    assert 'archived' not in entries[0]


# ---------------------------------------------------------------------------
# digested_names / newest_entry_date / is_stale
# ---------------------------------------------------------------------------

def test_digested_names_reads_the_filename_off_each_heading():
    text = _entry('2026-08-18', 'lesson-a.md') + _entry('2026-08-19', 'card-b.md')
    assert vd.digested_names(text) == {'lesson-a.md', 'card-b.md'}


def test_newest_entry_date_is_the_max_not_the_last_line():
    # Deliberately out of order: append order is not assumed to be sorted.
    text = _entry('2026-08-17', 'a.md') + _entry('2026-08-19', 'b.md') + \
        _entry('2026-08-18', 'c.md')
    assert vd.newest_entry_date(text) == '2026-08-19'


def test_newest_entry_date_on_empty_text_is_none():
    assert vd.newest_entry_date('') is None


def test_is_stale_true_past_the_age_limit():
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert vd.is_stale('2026-08-18', max_age_days=30, as_of=now) is True


def test_is_stale_false_within_the_age_limit():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert vd.is_stale('2026-08-18', max_age_days=30, as_of=now) is False


def test_is_stale_true_when_there_is_no_newest_date():
    """An empty digest that EXISTS is not evidence the vault has nothing."""
    assert vd.is_stale(None) is True


# ---------------------------------------------------------------------------
# extract_fields: the model's inline "## Digest Entry" block
# ---------------------------------------------------------------------------

def test_extract_fields_finds_a_well_formed_block():
    note = (
        '# Lesson: something\n\n**Status:** TESTED_FAILED\n\n'
        '## Digest Entry\n'
        '**Verdict:** The thesis did not survive.\n'
        '**Evidence:** 615 trades, -$337.63.\n'
        '**Relevance to Forge:** Do not propose this again.\n\n'
        '## The mechanism\n\nMore prose here.\n'
    )
    fields = vd.extract_fields(note)
    assert fields == {
        'verdict': 'The thesis did not survive.',
        'evidence': '615 trades, -$337.63.',
        'relevance': 'Do not propose this again.',
    }


def test_extract_fields_returns_none_when_the_block_is_missing():
    note = '# Lesson: something\n\n**Status:** TESTED_FAILED\n\nJust prose.\n'
    assert vd.extract_fields(note) is None


# ---------------------------------------------------------------------------
# append_entry: the write side, cap enforcement, archiving
# ---------------------------------------------------------------------------

def test_append_entry_creates_a_brand_new_digest(paths):
    """The file does not exist yet: this must not raise FileNotFoundError."""
    result = vd.append_entry(_entry('2026-08-18', 'a.md'),
                             digest_path_=paths['digest'],
                             archive_path_=paths['archive'],
                             db_path=paths['db'])
    assert 'a.md' in result
    assert os.path.exists(paths['digest'])


def test_append_entry_appends_to_an_existing_digest(paths):
    vd.append_entry(_entry('2026-08-18', 'a.md'), digest_path_=paths['digest'],
                    archive_path_=paths['archive'], db_path=paths['db'])
    result = vd.append_entry(_entry('2026-08-19', 'b.md'),
                             digest_path_=paths['digest'],
                             archive_path_=paths['archive'],
                             db_path=paths['db'])
    assert 'a.md' in result and 'b.md' in result


def test_append_entry_past_the_cap_archives_the_oldest_not_the_newest(paths):
    big_evidence = 'x' * 400
    for i in range(30):
        vd.append_entry(
            _entry('2026-08-%02d' % (i % 28 + 1), 'note-%02d.md' % i,
                   evidence=big_evidence),
            cap_chars=2000, digest_path_=paths['digest'],
            archive_path_=paths['archive'], db_path=paths['db'])

    final = vd.read_digest(paths['digest'])
    assert len(final) <= 2000 + len(vd._ARCHIVE_POINTER) + 2
    # The most recent entry must have survived the trim.
    assert 'note-29.md' in final
    # The oldest must have been evicted from the live digest...
    assert 'note-00.md' not in final
    # ...and landed in the archive rather than vanishing.
    archived = vd.read_digest(paths['archive'])
    assert 'note-00.md' in archived


def test_append_entry_under_the_cap_never_touches_the_archive(paths):
    vd.append_entry(_entry('2026-08-18', 'a.md'), cap_chars=100000,
                    digest_path_=paths['digest'],
                    archive_path_=paths['archive'], db_path=paths['db'])
    assert not os.path.exists(paths['archive'])


def test_the_archive_pointer_appears_only_after_something_was_dropped(paths):
    small = vd.append_entry(_entry('2026-08-18', 'a.md'), cap_chars=100000,
                            digest_path_=paths['digest'],
                            archive_path_=paths['archive'], db_path=paths['db'])
    assert 'archived to' not in small

    for i in range(20):
        result = vd.append_entry(
            _entry('2026-08-18', 'note-%02d.md' % i, evidence='x' * 300),
            cap_chars=1500, digest_path_=paths['digest'],
            archive_path_=paths['archive'], db_path=paths['db'])
    assert 'archived to Trading/_DIGEST-ARCHIVE.md' in result


def test_add_conclusion_is_format_entry_plus_append_entry(paths):
    vd.add_conclusion('lesson.md', 'It failed.', '100 trades, -$5.00',
                      'Do not retry.', date='2026-08-18',
                      digest_path_=paths['digest'],
                      archive_path_=paths['archive'], db_path=paths['db'])
    text = vd.read_digest(paths['digest'])
    assert '## 2026-08-18 lesson.md' in text
    assert '**Verdict:** It failed.' in text


def test_read_digest_on_a_missing_file_is_empty_string_not_an_error(tmp_path):
    assert vd.read_digest(str(tmp_path / 'nope.md')) == ''


def test_digest_path_follows_vault_reader_vault_root(monkeypatch, tmp_path):
    """`digest_path()` resolves at CALL time, so monkeypatching
    `vault_reader.VAULT_ROOT` (as the forge_reasoner test fixtures already
    do) redirects it, exactly like every other vault_reader constant."""
    from agents import vault_reader
    fake_root = str(tmp_path / 'vault' / 'Trading')
    monkeypatch.setattr(vault_reader, 'VAULT_ROOT', fake_root)
    assert vd.digest_path() == os.path.join(fake_root, '_DIGEST.md')
