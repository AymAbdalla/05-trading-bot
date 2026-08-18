"""Tests for the reasoning layer: llm_client, vault_reader, vault_writer.

What these tests are actually defending, in priority order:

  1. **Nothing here can take down the shadow loop.** `shadow_runner` calls
     into the blowup path at the worst possible moment. Every failure mode of
     a model turn (binary missing, timeout, non-zero exit, empty stdout,
     refusal) must degrade to a written fallback note, not an exception.
  2. **A fallback is never mistaken for analysis.** Convention 11: NOT_TESTED
     means "could not run". A note written without a model turn must say so,
     in the file, because it will be read back as evidence by Forge.
  3. **Convention 19.** `json.loads` accepts NaN and Infinity. A model asked
     for an edge estimate it does not have will emit exactly those. They must
     raise here, not poison a comparison three modules downstream.
  4. **Convention 20.** A vault read that drops notes over budget must SAY it
     dropped them, so nobody reads the truncation as "there are no lessons".

No test in this file spawns a real model turn. That is enforced by
`AYM_LLM_DRY_RUN` and by monkeypatching `subprocess.run`, and one test asserts
the enforcement itself, because a suite that silently started costing money
per run would not announce itself.
"""
import json
import os
import subprocess
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import llm_client, vault_reader, vault_writer  # noqa: E402


# ---------------------------------------------------------------------------
# llm_client: routing
# ---------------------------------------------------------------------------

def test_reasoning_tasks_route_to_opus():
    for task in ('forge_proposals', 'strategy_lesson', 'blowup_root_cause',
                 'strategy_card', 'forge_cycle_takeaway',
                 'critic_post_mortem'):
        assert llm_client.model_for_task(task) == llm_client.MODEL_OPUS, task


def test_mechanical_tasks_route_to_sonnet():
    for task in ('daily_summary', 'weekly_summary'):
        assert llm_client.model_for_task(task) == llm_client.MODEL_SONNET


def test_an_unknown_task_routes_to_opus_not_to_an_exception():
    """Getting a pricier model is a cost mistake. Getting none is a bug."""
    assert llm_client.model_for_task('task-nobody-registered') == \
        llm_client.MODEL_OPUS


# ---------------------------------------------------------------------------
# llm_client: Convention 19
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('payload', [
    '{"edge_bps": NaN}',
    '{"edge_bps": Infinity}',
    '{"edge_bps": -Infinity}',
    '[1, 2, NaN]',
    '{"nested": {"deep": {"edge": NaN}}}',
])
def test_non_finite_json_is_rejected(payload):
    with pytest.raises(ValueError):
        llm_client.strict_json_loads(payload)


def test_stdlib_json_would_have_accepted_those():
    """The reason `strict_json_loads` exists, pinned as a fact not a claim."""
    assert json.loads('{"edge_bps": NaN}')  # does NOT raise in CPython


def test_the_word_nan_inside_a_string_is_still_fine():
    parsed = llm_client.strict_json_loads('{"note": "NaN means not a number"}')
    assert parsed['note'].startswith('NaN')


def test_dump_json_refuses_to_write_a_non_finite():
    with pytest.raises(ValueError):
        llm_client.dump_json({'edge_bps': float('inf')})


# ---------------------------------------------------------------------------
# llm_client: getting JSON out of a chatty model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Sure, here you go:\n\n```json\n{"a": 1}\n```\n\nHope that helps.',
    'Preamble text {"a": 1} trailing text',
])
def test_extract_json_survives_the_usual_wrappers(text):
    assert llm_client.extract_json(text) == {'a': 1}


def test_extract_json_raises_rather_than_returning_a_guess():
    with pytest.raises(ValueError):
        llm_client.extract_json('I could not produce any proposals today.')


def test_extract_json_rejects_a_fenced_block_containing_nan():
    with pytest.raises(ValueError):
        llm_client.extract_json('```json\n{"edge_bps": NaN}\n```')


# ---------------------------------------------------------------------------
# llm_client: every way a turn can fail to RUN
# ---------------------------------------------------------------------------

def _no_dry_run(monkeypatch):
    monkeypatch.delenv(llm_client.DRY_RUN_ENV, raising=False)
    monkeypatch.setattr(llm_client.shutil, 'which', lambda _b: '/fake/claude')


def test_missing_binary_is_not_ok_and_does_not_raise(monkeypatch):
    monkeypatch.delenv(llm_client.DRY_RUN_ENV, raising=False)
    monkeypatch.setattr(llm_client.shutil, 'which', lambda _b: None)
    result = llm_client.run_claude('hi')
    assert result.ok is False
    assert result.text == ''
    assert 'PATH' in result.error


def test_missing_binary_raises_when_asked_to(monkeypatch):
    monkeypatch.delenv(llm_client.DRY_RUN_ENV, raising=False)
    monkeypatch.setattr(llm_client.shutil, 'which', lambda _b: None)
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.run_claude('hi', raise_on_error=True)


def test_timeout_is_not_ok_and_does_not_raise(monkeypatch):
    _no_dry_run(monkeypatch)

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd='claude', timeout=1)

    monkeypatch.setattr(llm_client.subprocess, 'run', _boom)
    result = llm_client.run_claude('hi', timeout_s=1)
    assert result.ok is False
    assert 'exceeded' in result.error


def test_non_zero_exit_is_not_ok(monkeypatch):
    _no_dry_run(monkeypatch)
    monkeypatch.setattr(
        llm_client.subprocess, 'run',
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 2, b'', b'model overloaded'))
    result = llm_client.run_claude('hi')
    assert result.ok is False
    assert result.exit_code == 2
    assert 'model overloaded' in result.error


def test_exit_zero_with_empty_stdout_is_not_ok(monkeypatch):
    """Exit 0 and nothing said is a failure to run, not an empty answer."""
    _no_dry_run(monkeypatch)
    monkeypatch.setattr(
        llm_client.subprocess, 'run',
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b'   \n', b''))
    result = llm_client.run_claude('hi')
    assert result.ok is False


def test_a_successful_turn_returns_the_text(monkeypatch):
    _no_dry_run(monkeypatch)
    monkeypatch.setattr(
        llm_client.subprocess, 'run',
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b'  hello  ', b''))
    result = llm_client.run_claude('hi')
    assert result.ok is True
    assert result.text == 'hello'


def test_pythonpath_is_stripped_from_the_child(monkeypatch):
    """Convention 14. Hermes leaks a 3.11 venv and numpy then looks broken."""
    _no_dry_run(monkeypatch)
    monkeypatch.setenv('PYTHONPATH', '/some/hermes/venv')
    seen = {}

    def _capture(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b'ok result', b'')

    monkeypatch.setattr(llm_client.subprocess, 'run', _capture)
    llm_client.run_claude('hi')
    assert 'PYTHONPATH' not in seen['env']


def test_the_model_flag_actually_reaches_the_command_line(monkeypatch):
    """Routing that never makes it into argv is routing that did not happen."""
    _no_dry_run(monkeypatch)
    seen = {}

    def _capture(argv, **kwargs):
        seen['argv'] = argv
        return subprocess.CompletedProcess(argv, 0, b'ok result', b'')

    monkeypatch.setattr(llm_client.subprocess, 'run', _capture)
    llm_client.run_task('daily_summary', 'hi')
    assert '--model' in seen['argv']
    assert seen['argv'][seen['argv'].index('--model') + 1] == \
        llm_client.MODEL_SONNET


def test_the_tool_allowlist_is_not_a_wildcard():
    """A reasoning agent has no business running Bash in this repo."""
    assert '*' not in llm_client.DEFAULT_ALLOWED_TOOLS
    assert 'Bash' not in llm_client.DEFAULT_ALLOWED_TOOLS


# ---------------------------------------------------------------------------
# vault_reader
# ---------------------------------------------------------------------------

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


def test_a_missing_directory_reads_as_empty_not_as_an_error(tmp_path):
    section = vault_reader.read_dir(str(tmp_path / 'nope'))
    assert section['exists'] is False
    assert section['notes'] == []


def test_notes_are_parsed_for_status_and_failure_mode(tmp_path):
    _write(str(tmp_path / 'a.md'),
           '# Fair Value Arb\n\n**Status:** TESTED_FAILED\n'
           '**Failure Mode:** spread_eats_edge\n'
           '**Strategy:** PM_fair_value_arb\n')
    note = vault_reader.read_dir(str(tmp_path))['notes'][0]
    assert note.title == 'Fair Value Arb'
    assert note.status == 'TESTED_FAILED'
    assert note.failure_mode == 'spread_eats_edge'
    assert note.strategy == 'PM_fair_value_arb'


def test_dropping_notes_over_budget_is_reported_not_silent(tmp_path):
    """Convention 20: a silent truncation is a missing number."""
    for i in range(5):
        _write(str(tmp_path / ('n%d.md' % i)), '# n%d\n' % i + 'x' * 500)
    section = vault_reader.read_dir(str(tmp_path), budget_chars=600)
    assert len(section['notes']) == 1
    assert len(section['dropped']) == 4
    assert all(d['reason'] == 'over budget_chars' for d in section['dropped'])


def test_render_says_out_loud_when_notes_were_dropped(tmp_path):
    for i in range(3):
        _write(str(tmp_path / ('n%d.md' % i)), '# n%d\n' % i + 'x' * 500)
    context = {'vault_root': str(tmp_path), 'sections': {}}
    section = vault_reader.read_dir(str(tmp_path), budget_chars=600)
    for key, _path in vault_reader.SECTIONS:
        context['sections'][key] = section
    rendered = vault_reader.render_context(context)
    assert 'were NOT included' in rendered
    assert 'Do not read their absence' in rendered


def test_render_distinguishes_missing_from_empty(tmp_path):
    missing = vault_reader.read_dir(str(tmp_path / 'nope'))
    empty = vault_reader.read_dir(str(tmp_path))
    context = {'vault_root': str(tmp_path),
               'sections': {'lessons': missing, 'blowup_reports': empty,
                            'strategy_cards': empty, 'cycle_summaries': empty}}
    rendered = vault_reader.render_context(context)
    assert 'does not exist yet' in rendered
    assert '(empty)' in rendered


def test_known_failure_modes_groups_by_mode(tmp_path):
    _write(str(tmp_path / 'a.md'),
           '# A\n**Failure Mode:** spread_eats_edge\n**Strategy:** S1\n')
    _write(str(tmp_path / 'b.md'),
           '# B\n**Failure Mode:** spread_eats_edge\n**Strategy:** S2\n')
    section = vault_reader.read_dir(str(tmp_path))
    context = {'sections': {k: section for k, _ in vault_reader.SECTIONS}}
    modes = vault_reader.known_failure_modes(context)
    assert set(modes['spread_eats_edge']) == {'S1', 'S2'}


# ---------------------------------------------------------------------------
# vault_writer
# ---------------------------------------------------------------------------

def test_a_good_turn_is_written_with_a_model_provenance_header(tmp_path,
                                                               monkeypatch):
    body = '# Real Report\n\n' + 'analysis. ' * 60
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            body, 'opus', task, 3.0))
    out = str(tmp_path / 'note.md')
    result = vault_writer.compose_note('strategy_lesson', 'p', out, 'FALLBACK')
    assert result.written and result.used_model
    text = open(out, encoding='utf-8').read()
    assert 'model: opus' in text
    assert 'NOT_TESTED' not in text
    assert 'Real Report' in text


def test_a_failed_turn_writes_the_fallback_and_says_so(tmp_path, monkeypatch):
    """Convention 11 in the file itself, where a future reader will see it."""
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            '', 'opus', task, 1.0, ok=False, error='claude exited 1'))
    out = str(tmp_path / 'note.md')
    result = vault_writer.compose_note('strategy_lesson', 'p', out,
                                       'FALLBACK BODY ' * 20)
    assert result.written is True
    assert result.used_model is False
    text = open(out, encoding='utf-8').read()
    assert 'model: NOT_TESTED' in text
    assert 'The reasoning layer did not run' in text
    assert 'claude exited 1' in text
    assert 'FALLBACK BODY' in text


def test_a_short_turn_is_treated_as_no_answer(tmp_path, monkeypatch):
    """An empty-ish reply must not overwrite a note with nothing."""
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult('ok', 'opus', task, 1.0))
    out = str(tmp_path / 'note.md')
    result = vault_writer.compose_note('strategy_lesson', 'p', out,
                                       'FALLBACK BODY ' * 20)
    assert result.used_model is False
    assert 'FALLBACK BODY' in open(out, encoding='utf-8').read()


def test_a_refusal_is_treated_as_no_answer(tmp_path, monkeypatch):
    refusal = "I'm sorry, I can't help with that. " * 20
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            refusal, 'opus', task, 1.0))
    out = str(tmp_path / 'note.md')
    result = vault_writer.compose_note('strategy_lesson', 'p', out,
                                       'FALLBACK BODY ' * 20)
    assert result.used_model is False
    assert 'FALLBACK BODY' in open(out, encoding='utf-8').read()


def test_a_note_without_a_fallback_is_a_programmer_error():
    with pytest.raises(ValueError):
        vault_writer.compose_note('strategy_lesson', 'p', '/tmp/x.md', '')


def test_the_write_is_atomic(tmp_path, monkeypatch):
    """A killed turn must not leave half a note where a whole one was."""
    out = str(tmp_path / 'note.md')
    vault_writer.atomic_write(out, 'first version\n')

    real_replace = os.replace

    def _explode(src, dst):
        raise OSError('disk full')

    monkeypatch.setattr(vault_writer.os, 'replace', _explode)
    with pytest.raises(OSError):
        vault_writer.atomic_write(out, 'second version\n')
    monkeypatch.setattr(vault_writer.os, 'replace', real_replace)

    assert open(out, encoding='utf-8').read() == 'first version\n'
    leftovers = [n for n in os.listdir(str(tmp_path)) if n.startswith('.vault-')]
    assert leftovers == [], 'temp file was not cleaned up: %r' % leftovers


def test_every_named_note_type_survives_a_dead_model(tmp_path, monkeypatch):
    """The whole public surface, against the worst case, in one place."""
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            '', 'opus', task, 0.1, ok=False, error='binary missing'))
    out = str(tmp_path)
    calls = [
        lambda: vault_writer.write_blowup_report(
            1, 1000.0, 0.0, 503, -1000.0, 7200,
            {'S': {'trades': 5, 'win_rate': 20, 'pnl': -50}}, out_dir=out),
        lambda: vault_writer.write_strategy_lesson('S', 'evidence', out_dir=out),
        lambda: vault_writer.write_strategy_card('S', 'evidence', out_dir=out),
        lambda: vault_writer.write_cycle_summary('c1', 'evidence', out_dir=out),
        lambda: vault_writer.write_post_mortem('w1', 'evidence', out_dir=out),
        lambda: vault_writer.write_daily_summary('2026-08-18', 'data',
                                                 out_dir=out),
    ]
    for call in calls:
        result = call()
        assert result.written is True, result.to_dict()
        assert result.used_model is False
        assert 'NOT_TESTED' in open(result.path, encoding='utf-8').read()


def test_the_prompt_carries_the_evidence_and_the_vault(monkeypatch, tmp_path):
    """A reasoning prompt that forgot the evidence is a fluent guess."""
    seen = {}

    def _capture(task, prompt, **kw):
        seen['prompt'] = prompt
        return llm_client.LLMResult('# Note\n' + 'x' * 400, 'opus', task, 1.0)

    monkeypatch.setattr(vault_writer.llm_client, 'run_task', _capture)
    vault_writer.write_blowup_report(
        7, 1000.0, 0.0, 503, -1000.0, 7200,
        {'PM_fair_value_arb': {'trades': 176, 'win_rate': 21, 'pnl': -133.31}},
        trade_detail='TRADE-DETAIL-MARKER',
        vault_context='VAULT-MARKER', out_dir=str(tmp_path))
    assert 'PM_fair_value_arb' in seen['prompt']
    assert '-133.31' in seen['prompt']
    assert 'TRADE-DETAIL-MARKER' in seen['prompt']
    assert 'VAULT-MARKER' in seen['prompt']


def test_the_prompt_forbids_inventing_numbers(monkeypatch, tmp_path):
    seen = {}

    def _capture(task, prompt, **kw):
        seen['prompt'] = prompt
        return llm_client.LLMResult('# Note\n' + 'x' * 400, 'opus', task, 1.0)

    monkeypatch.setattr(vault_writer.llm_client, 'run_task', _capture)
    vault_writer.write_strategy_lesson('S', 'evidence', out_dir=str(tmp_path))
    assert 'Do not invent numbers' in seen['prompt']


# ---------------------------------------------------------------------------
# The blowup path, end to end, on a throwaway database
# ---------------------------------------------------------------------------

def _throwaway_db(path):
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE shadow_blowups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
            blowup_number INTEGER, starting_equity REAL, ending_equity REAL,
            total_trades INTEGER, total_pnl REAL, duration_seconds INTEGER,
            per_strategy_json TEXT);
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, pair TEXT, strategy_id TEXT,
            signal_id INTEGER, opened_ts INTEGER, closed_ts INTEGER,
            entry_px REAL, exit_px REAL, qty REAL, stop_px REAL,
            target_px REAL, pnl_gross REAL, pnl_net REAL, fees REAL,
            r_multiple REAL, exit_reason TEXT, mode TEXT);
    ''')
    conn.execute(
        'INSERT INTO shadow_blowups (ts, blowup_number, starting_equity,'
        ' ending_equity, total_trades, total_pnl, duration_seconds,'
        ' per_strategy_json) VALUES (1, 1, 1000.0, 0.0, 503, -1000.0, 7200, ?)',
        (json.dumps({'PM_fair_value_arb':
                     {'trades': 176, 'pnl': -133.31, 'win_rate': 21.0}}),))
    conn.execute(
        "INSERT INTO positions VALUES (1, 'BTC-5m', 'PM_fair_value_arb', 1,"
        " 1000, 7200, 0.06, 0.03, 10, 0.03, 0.07, -0.3, -0.31, 0.01, -1.0,"
        " 'stop', 'paper')")
    conn.commit()
    conn.close()


def test_blowup_note_from_db_reads_the_row_and_the_trades(tmp_path,
                                                          monkeypatch):
    db = str(tmp_path / 'trading.db')
    _throwaway_db(db)
    seen = {}

    def _capture(task, prompt, **kw):
        seen['prompt'] = prompt
        return llm_client.LLMResult('# Blowup #1\n' + 'x' * 400, 'opus',
                                    task, 1.0)

    monkeypatch.setattr(vault_writer.llm_client, 'run_task', _capture)
    result = vault_writer.blowup_note_from_db(
        db_path=db, out_dir=str(tmp_path / 'out'))
    assert result.written and result.used_model
    # The per-strategy roll-up AND the individual trade both reached the model.
    assert 'PM_fair_value_arb' in seen['prompt']
    assert '-133.31' in seen['prompt']
    assert 'BTC-5m' in seen['prompt']
    assert 'stop' in seen['prompt']


def test_blowup_note_from_db_raises_on_a_missing_row(tmp_path):
    db = str(tmp_path / 'trading.db')
    _throwaway_db(db)
    with pytest.raises(ValueError):
        vault_writer.blowup_note_from_db(blowup_id=999, db_path=db,
                                         out_dir=str(tmp_path / 'out'))


def test_trade_detail_degrades_rather_than_raising_on_a_bad_db(tmp_path):
    """This runs on the blowup path. It must never be the thing that throws."""
    text = vault_writer.trade_detail(db_path=str(tmp_path / 'nothing.db'))
    assert 'could not open' in text


def test_a_test_run_cannot_write_into_the_real_vault(tmp_path, monkeypatch):
    """A synthetic note in the vault would be read back as real evidence."""
    db = str(tmp_path / 'trading.db')
    _throwaway_db(db)
    monkeypatch.setattr(
        vault_writer.llm_client, 'run_task',
        lambda task, prompt, **kw: llm_client.LLMResult(
            '# X\n' + 'y' * 400, 'opus', task, 1.0))
    out = str(tmp_path / 'out')
    result = vault_writer.blowup_note_from_db(db_path=db, out_dir=out)
    assert result.path.startswith(out)
    assert vault_writer.VAULT_ROOT not in result.path


# ---------------------------------------------------------------------------
# The suite must not start spending money without saying so
# ---------------------------------------------------------------------------

def test_no_test_in_this_file_spawns_a_real_model_turn(monkeypatch):
    """The guard itself, asserted.

    If `run_claude` ever stops honouring the dry-run switch, this fails rather
    than the suite quietly starting to cost tokens per run.
    """
    monkeypatch.setenv(llm_client.DRY_RUN_ENV, '1')

    def _forbidden(*_a, **_k):
        raise AssertionError('a real subprocess was spawned')

    monkeypatch.setattr(llm_client.subprocess, 'run', _forbidden)
    result = llm_client.run_task('forge_proposals', 'hello')
    assert result.ok is True
    assert 'DRY_RUN' in result.text


def test_a_written_note_is_readable_by_everything_else(tmp_path):
    """NamedTemporaryFile gives 0600; the rest of the vault is 0644.

    A note only this process can read is a silent difference that would show
    up as an unexplained gap in the next vault read.
    """
    out = str(tmp_path / 'note.md')
    vault_writer.atomic_write(out, 'body\n')
    assert oct(os.stat(out).st_mode & 0o777) == '0o644'


# ---------------------------------------------------------------------------
# Defects found by round-tripping REAL Opus output, not synthetic fixtures
# ---------------------------------------------------------------------------

def test_a_failure_mode_with_prose_still_groups_on_the_bare_token(tmp_path):
    """Two notes naming ONE mode must not become two modes.

    Real output writes ``**Failure Mode:** `spread_eats_edge` (confirmed by
    the inverse variant)``. Keyed raw, that is a different mode from a note
    that wrote the bare token, and the count silently splits.
    """
    _write(str(tmp_path / 'a.md'),
           '# A\n**Failure Mode:** spread_eats_edge\n**Strategy:** S1\n')
    _write(str(tmp_path / 'b.md'),
           '# B\n**Failure Mode:** `spread_eats_edge` (confirmed by the '
           'inverse variant, see Assessment)\n**Strategy:** S2\n')
    section = vault_reader.read_dir(str(tmp_path))
    context = {'sections': {k: section for k, _ in vault_reader.SECTIONS}}
    modes = vault_reader.known_failure_modes(context)
    assert list(modes) == ['spread_eats_edge']
    assert set(modes['spread_eats_edge']) == {'S1', 'S2'}


def test_the_prose_qualifier_is_kept_not_discarded(tmp_path):
    _write(str(tmp_path / 'a.md'),
           '# A\n**Status:** TESTED_FAILED (family verdict on 615 trades)\n'
           '**Failure Mode:** `spread_eats_edge` (see Assessment)\n')
    note = vault_reader.read_dir(str(tmp_path))['notes'][0]
    assert note.failure_mode == 'spread_eats_edge'
    assert 'see Assessment' in note.failure_mode_raw
    assert note.status_token == 'TESTED_FAILED'
    assert '615 trades' in note.status


def test_a_prose_only_failure_mode_yields_no_token(tmp_path):
    """Better no key than a key invented out of a sentence."""
    _write(str(tmp_path / 'a.md'), '# A\n**Failure Mode:** (none observed)\n')
    note = vault_reader.read_dir(str(tmp_path))['notes'][0]
    assert note.failure_mode is None


def test_the_budget_fits_the_notes_the_writer_actually_produces(tmp_path):
    """20,000 was sized against 1.5k hand-written notes and silently dropped
    half the evidence once Opus started writing 8k to 15k ones."""
    for i in range(2):
        _write(str(tmp_path / ('n%d.md' % i)), '# n%d\n' % i + 'x' * 15000)
    section = vault_reader.read_dir(str(tmp_path))
    assert section['dropped'] == [], 'the default budget drops real notes'
    assert len(section['notes']) == 2


def test_skip_model_still_writes_and_the_name_says_so(tmp_path):
    """The flag formerly called `dry_run`.

    It skips the model. It does NOT skip the write. That gap put a note built
    from synthetic test numbers into the real vault once, so the behaviour is
    pinned here and the name now matches it.
    """
    out = str(tmp_path / 'note.md')
    result = vault_writer.compose_note('strategy_lesson', 'p', out,
                                       'FALLBACK BODY ' * 20, skip_model=True)
    assert result.written is True
    assert result.used_model is False
    assert os.path.exists(out)
    assert 'skip_model' in result.error


def test_no_public_writer_still_takes_a_dry_run_argument():
    """A leftover `dry_run=` alias would let the old trap back in silently."""
    import inspect
    for name in ('compose_note', 'write_blowup_report', 'write_strategy_lesson',
                 'write_strategy_card', 'write_cycle_summary',
                 'write_post_mortem', 'write_daily_summary',
                 'blowup_note_from_db'):
        params = inspect.signature(getattr(vault_writer, name)).parameters
        assert 'dry_run' not in params, name
        assert 'skip_model' in params, name


def test_every_note_writer_accepts_an_out_dir():
    """So a test can never reach the real vault by accident."""
    import inspect
    for name in ('write_blowup_report', 'write_strategy_lesson',
                 'write_strategy_card', 'write_cycle_summary',
                 'write_post_mortem', 'write_daily_summary',
                 'blowup_note_from_db'):
        params = inspect.signature(getattr(vault_writer, name)).parameters
        assert 'out_dir' in params, name
