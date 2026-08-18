"""Tests for the two vault_reader additions the digest work (Task 2) needs.

`list_notes` and `read_uncovered` are the delta-read primitives
`agents/forge_reasoner.py` uses so a fresh digest means reading almost
nothing, instead of the full note tree every cycle.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import vault_reader  # noqa: E402


@pytest.fixture
def sections(tmp_path, monkeypatch):
    lessons = tmp_path / 'Lessons'
    cards = tmp_path / 'Strategy-Cards'
    lessons.mkdir()
    cards.mkdir()
    (lessons / 'a.md').write_text('# A\n\nbody a\n')
    (lessons / 'b.md').write_text('# B\n\nbody b\n')
    (cards / 'c.md').write_text('# C\n\nbody c\n')
    monkeypatch.setattr(vault_reader, 'SECTIONS', (
        ('lessons', str(lessons)),
        ('strategy_cards', str(cards)),
    ))
    return {'lessons': lessons, 'cards': cards}


def test_list_notes_returns_names_and_mtimes_without_reading_content(sections):
    entries = vault_reader.list_notes(str(sections['lessons']))
    names = {name for name, _mtime in entries}
    assert names == {'a.md', 'b.md'}
    assert all(isinstance(mtime, float) for _name, mtime in entries)


def test_list_notes_ignores_non_markdown_and_missing_dirs(tmp_path, sections):
    (sections['lessons'] / 'notes.txt').write_text('not markdown')
    entries = vault_reader.list_notes(str(sections['lessons']))
    assert 'notes.txt' not in {n for n, _ in entries}
    assert vault_reader.list_notes(str(tmp_path / 'nowhere')) == []


def test_read_uncovered_skips_names_already_covered(sections):
    result = vault_reader.read_uncovered({'a.md'})
    names = {n['name'] for n in result['notes']}
    assert names == {'b.md', 'c.md'}
    assert 'a.md' not in names


def test_read_uncovered_with_everything_covered_reads_nothing(sections):
    result = vault_reader.read_uncovered({'a.md', 'b.md', 'c.md'})
    assert result['notes'] == []
    assert result['chars'] == 0


def test_read_uncovered_carries_the_section_and_full_text(sections):
    result = vault_reader.read_uncovered(set())
    by_name = {n['name']: n for n in result['notes']}
    assert by_name['a.md']['section'] == 'lessons'
    assert 'body a' in by_name['a.md']['text']


def test_read_uncovered_reports_drops_over_budget(sections):
    result = vault_reader.read_uncovered(set(), budget_chars=5)
    # At least one note fits (the budget check only rejects once something is
    # already kept), the rest are reported dropped, never silently missing.
    assert len(result['notes']) == 1
    assert len(result['dropped']) == 2
    assert all(d['reason'] == 'over budget_chars' for d in result['dropped'])
