"""Tests for agents/vault_writer.py's two additions from the vault-digest work.

  1. The digest hook: a real (model-composed, non-fallback) note updates
     `_DIGEST.md`; a `skip_model` note and a fallback note do NOT.
  2. The size-cap / appendix split: a note over `NOTE_SIZE_CAP_CHARS` gets its
     detail moved to `Trading/Appendices/<note>-appendix.md`, keeping the
     head (title, key-value block, everything up to the section that first
     crosses the cap) in the main note with a pointer.

No test here spawns a real model turn: `llm_client.run_task` is monkeypatched
throughout, following the pattern already established in
`tests/test_vault_refresh.py`. Every note write targets `tmp_path`, never the
real vault, and every digest write is monkeypatched or pointed at `tmp_path`
too, so nothing here can touch `~/aym/vault` or the real coordination ledger.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import llm_client, vault_digest, vault_writer as vw  # noqa: E402


def _reply(text: str, ok: bool = True, model: str = 'opus'):
    def run_task(task, prompt, **kwargs):
        return llm_client.LLMResult(text, model, task, 1.0, ok=ok)
    return run_task


DIGEST_BLOCK = (
    '## Digest Entry\n'
    '**Verdict:** The thesis did not survive.\n'
    '**Evidence:** 615 trades, -$337.63.\n'
    '**Relevance to Forge:** Do not propose this family again.\n\n'
)


def _note_with_digest_block(extra_chars: int = 0) -> str:
    padding = ('Extra evidence line.\n' * (extra_chars // 22)) if extra_chars else ''
    return (
        '# Strategy Card: fair value arb\n\n'
        '**Status:** TESTED_FAILED\n\n'
        + DIGEST_BLOCK +
        '## Thesis\n\nCompute a fair value and trade the gap.\n\n'
        '## Results\n\n' + padding + '\n'
        '## Assessment\n\nIt lost money.\n'
    )


@pytest.fixture(autouse=True)
def _isolate_digest(monkeypatch, tmp_path):
    """Point the digest hook at a throwaway file/ledger for every test here."""
    digest_path = str(tmp_path / '_DIGEST.md')
    archive_path = str(tmp_path / '_DIGEST-ARCHIVE.md')
    db_path = str(tmp_path / 'coord.db')

    real_add_conclusion = vault_digest.add_conclusion

    def _add_conclusion(*args, **kwargs):
        kwargs.setdefault('digest_path_', digest_path)
        kwargs.setdefault('archive_path_', archive_path)
        kwargs.setdefault('db_path', db_path)
        return real_add_conclusion(*args, **kwargs)

    monkeypatch.setattr(vault_digest, 'add_conclusion', _add_conclusion)
    return {'digest': digest_path, 'archive': archive_path, 'db': db_path}


# ---------------------------------------------------------------------------
# The digest hook
# ---------------------------------------------------------------------------

def test_a_real_note_write_updates_the_digest(monkeypatch, tmp_path, _isolate_digest):
    monkeypatch.setattr(llm_client, 'run_task', _reply(_note_with_digest_block()))
    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.written and result.used_model
    assert result.digest_updated is True
    assert result.digest_skip_reason is None

    digest_text = vault_digest.read_digest(_isolate_digest['digest'])
    assert 'The thesis did not survive.' in digest_text
    assert os.path.basename(result.path) in digest_text


def test_skip_model_does_not_touch_the_digest(monkeypatch, tmp_path, _isolate_digest):
    def _forbidden(*a, **k):
        raise AssertionError('skip_model must not spawn a model turn')
    monkeypatch.setattr(llm_client, 'run_task', _forbidden)

    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'),
                                    skip_model=True)
    assert result.written
    assert result.digest_updated is False
    assert not os.path.exists(_isolate_digest['digest'])


def test_a_fallback_note_does_not_touch_the_digest(monkeypatch, tmp_path,
                                                    _isolate_digest):
    """A model turn that fails falls back to the deterministic fallback text,
    which carries no judgement (Convention 11) and so is not a conclusion."""
    monkeypatch.setattr(llm_client, 'run_task', _reply('', ok=False))
    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.written and not result.used_model
    assert result.digest_updated is False
    assert not os.path.exists(_isolate_digest['digest'])


def test_a_note_missing_the_digest_block_skips_the_digest_without_failing(
        monkeypatch, tmp_path, _isolate_digest):
    """The note write must succeed even if the model forgot the block."""
    text = ('# Strategy Card: something\n\n**Status:** TESTED_FAILED\n\n'
           '## Thesis\n\nNo digest entry section here.\n' + 'x' * 200)
    monkeypatch.setattr(llm_client, 'run_task', _reply(text))
    result = vw.write_strategy_card('PM_something', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.written and result.used_model
    assert result.digest_updated is False
    assert 'no ## Digest Entry block' in result.digest_skip_reason


def test_daily_summary_is_not_digest_eligible(monkeypatch, tmp_path,
                                               _isolate_digest):
    """The mechanical roll-up is not a judgement (module docstring)."""
    text = '# Daily Summary: 2026-08-18\n\n' + DIGEST_BLOCK + 'x' * 200
    monkeypatch.setattr(llm_client, 'run_task', _reply(text))
    result = vw.write_daily_summary('2026-08-18', 'evidence',
                                    out_dir=str(tmp_path / 'out'))
    assert result.written
    assert result.digest_updated is False
    assert 'not a judgement note' in result.digest_skip_reason


def test_the_prompt_asks_for_a_digest_entry_for_judgement_notes():
    prompt = vw._prompt('role', 'task', [], ['instruction one'])
    assert '## Digest Entry' in prompt
    assert '**Verdict:**' in prompt


def test_the_prompt_does_not_ask_for_a_digest_entry_when_disabled():
    prompt = vw._prompt('role', 'task', [], ['instruction one'],
                        require_digest_entry=False)
    assert 'Digest Entry' not in prompt


# ---------------------------------------------------------------------------
# Size cap / appendix split
# ---------------------------------------------------------------------------

def test_an_oversized_note_is_split_and_the_main_note_gets_a_pointer(
        monkeypatch, tmp_path, _isolate_digest):
    big = _note_with_digest_block(extra_chars=vw.NOTE_SIZE_CAP_CHARS + 2000)
    assert len(big) > vw.NOTE_SIZE_CAP_CHARS
    monkeypatch.setattr(llm_client, 'run_task', _reply(big))

    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.written
    assert result.appendix_path is not None
    assert os.path.exists(result.appendix_path)

    main_text = open(result.path).read()
    assert 'Full evidence' in main_text
    assert os.path.basename(result.appendix_path) in main_text
    # The appendix carries the detail that got cut, the main note does not.
    appendix_text = open(result.appendix_path).read()
    assert 'Appendix:' in appendix_text


def test_a_note_under_the_cap_is_never_split(monkeypatch, tmp_path,
                                             _isolate_digest):
    small = _note_with_digest_block()
    assert len(small) < vw.NOTE_SIZE_CAP_CHARS
    monkeypatch.setattr(llm_client, 'run_task', _reply(small))
    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.appendix_path is None


def test_split_oversized_note_keeps_the_opening_block_in_head():
    body = ('# Title\n\n**Status:** X\n\n' + ('## A\n\n' + 'y' * 100 + '\n') * 200)
    head, detail = vw.split_oversized_note(body, cap_chars=500)
    assert detail is not None
    assert head.startswith('# Title')
    assert len(head) <= 600  # near the cap, plus the boundary section


def test_split_oversized_note_with_no_heading_to_cut_on_is_left_whole():
    body = 'x' * 20000  # no "## " anywhere
    head, detail = vw.split_oversized_note(body, cap_chars=10000)
    assert detail is None
    assert head == body


def test_split_oversized_note_under_the_cap_is_a_no_op():
    body = '# Title\n\n## A\n\nshort\n'
    head, detail = vw.split_oversized_note(body, cap_chars=10000)
    assert detail is None
    assert head == body


def test_appendix_dir_uses_the_shared_vault_appendices_folder_in_production():
    assert vw._appendix_dir(vw.CARDS_DIR) == os.path.join(
        vw.VAULT_ROOT, 'Appendices')
    assert vw._appendix_dir(vw.LESSONS_DIR) == os.path.join(
        vw.VAULT_ROOT, 'Appendices')


def test_appendix_dir_falls_back_to_a_sibling_for_an_arbitrary_test_dir(tmp_path):
    out_dir = str(tmp_path / 'somewhere')
    assert vw._appendix_dir(out_dir) == os.path.join(out_dir, 'Appendices')


def test_a_split_note_still_gets_a_digest_entry_naming_the_appendix(
        monkeypatch, tmp_path, _isolate_digest):
    big = _note_with_digest_block(extra_chars=vw.NOTE_SIZE_CAP_CHARS + 2000)
    monkeypatch.setattr(llm_client, 'run_task', _reply(big))
    result = vw.write_strategy_card('PM_fair_value_arb', 'evidence text',
                                    out_dir=str(tmp_path / 'out'))
    assert result.digest_updated is True
    digest_text = vault_digest.read_digest(_isolate_digest['digest'])
    assert os.path.basename(result.appendix_path) in digest_text
