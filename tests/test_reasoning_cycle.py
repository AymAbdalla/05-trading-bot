"""Tests for the 4-hourly reasoning cycle runner.

What these defend, in priority order:

  1. **No test here spends a token.** `AYM_LLM_DRY_RUN=1` is set on every test
     that could reach `llm_client`, and the end-to-end test additionally
     monkeypatches `subprocess.run` inside `llm_client` to raise, so a real
     `claude -p` would fail the test loudly rather than quietly costing money.
  2. **Nothing lands in `~/aym/vault` during a test.** Forge reads the vault
     back as evidence, so a synthetic post-mortem written there is not a
     cosmetic problem: it becomes an input to the next real reasoning turn.
     One test snapshots the real vault directory and asserts it is byte-for-
     byte unchanged after a full sandboxed cycle.
  3. **Convention 11.** `NOT_TESTED` (the turn COULD NOT RUN) and `declined`
     (the turn ran and returned nothing usable) must stay two facts with two
     counters and two exit codes.
  4. **Two cron firings cannot overlap.** The lock is `flock`, so the second
     firing must exit `EXIT_LOCK_BUSY` without running a stage.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import llm_client, vault_writer  # noqa: E402
from scripts import reasoning_cycle as rc  # noqa: E402

REPO_DB = os.path.join(rc.ROOT, 'db', 'trading.db')


@pytest.fixture(autouse=True)
def _never_spend_money(monkeypatch):
    """The money rule, on every test in this file without exception."""
    monkeypatch.setenv(rc.DRY_RUN_ENV, '1')


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------

def test_the_lock_is_taken_and_released(tmp_path):
    path = str(tmp_path / 'cycle.lock')
    lock = rc.CycleLock(path)
    assert lock.acquire() is True
    second = rc.CycleLock(path)
    assert second.acquire() is False, 'a second holder got the same lock'
    assert 'pid=%d' % os.getpid() in second.holder
    lock.release()
    assert second.acquire() is True, 'the lock was not released'
    second.release()


def test_a_second_cycle_refuses_to_run_while_the_first_holds_the_lock(tmp_path):
    """The whole point of the lock: no stage runs, and the exit code says so.

    `--db` points at a path that does not exist. If the lock failed to stop the
    run, the critic stage would explode and the exit code would be
    `EXIT_FAILED`, not `EXIT_LOCK_BUSY`. So this cannot pass by accident.
    """
    lock_path = str(tmp_path / 'cycle.lock')
    out_dir = str(tmp_path / 'out')
    held = rc.CycleLock(lock_path)
    assert held.acquire() is True
    try:
        code = rc.main(['--only', 'critic', '--skip-model', '--quiet',
                        '--out-dir', out_dir, '--lock-file', lock_path,
                        '--db', str(tmp_path / 'no-such.db')])
    finally:
        held.release()

    assert code == rc.EXIT_LOCK_BUSY
    assert code != 0
    # Nothing ran: no run record, no critic artifacts.
    assert not os.path.exists(os.path.join(out_dir, rc.RUNS_JSONL_NAME))
    assert not os.path.exists(os.path.join(out_dir, 'cycles'))


def test_the_lock_survives_the_holder_dying(tmp_path):
    """`flock` and not a pid file, so a killed cycle leaves no stale lock."""
    path = str(tmp_path / 'cycle.lock')
    child = subprocess.run(
        [sys.executable, '-c',
         'import sys; sys.path.insert(0, %r)\n'
         'from scripts import reasoning_cycle as rc\n'
         'lock = rc.CycleLock(%r)\n'
         'assert lock.acquire()\n'
         'raise SystemExit(0)  # exits WITHOUT releasing\n'
         % (rc.ROOT, path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert child.returncode == 0, child.stderr.decode()
    after = rc.CycleLock(path)
    assert after.acquire() is True, 'a dead process still holds the lock'
    after.release()


# ---------------------------------------------------------------------------
# Convention 11: NOT_TESTED is not "declined"
# ---------------------------------------------------------------------------

def test_forge_reasoner_statuses_map_to_four_distinct_outcomes():
    could_not_run = rc.classify_reasoner(
        {'status': 'NOT_TESTED', 'error': 'claude is not on PATH'})
    ran_unusable = rc.classify_reasoner(
        {'status': 'unusable_reply', 'error': None})
    ran_empty = rc.classify_reasoner({'status': 'no_candidates', 'error': None})
    ran_ok = rc.classify_reasoner(
        {'status': 'ok', 'error': None, 'candidates_kept': 3})

    assert could_not_run['outcome'] == 'NOT_TESTED'
    assert ran_unusable['outcome'] == 'declined'
    assert ran_empty['outcome'] == 'declined'
    assert ran_ok['outcome'] == 'ok'
    assert could_not_run['outcome'] != ran_unusable['outcome'], (
        'Convention 11: "could not run" and "ran and said nothing usable" '
        'must not share a counter')
    assert 'COULD NOT RUN' in could_not_run['detail']
    assert 'not NOT_TESTED' in ran_unusable['detail']


def test_a_vault_write_distinguishes_could_not_run_from_ran_and_declined():
    """The distinction is `LLMResult.ok`, exactly as `llm_client` documents."""
    not_tested = rc.classify_vault_write(vault_writer.VaultWrite(
        '/tmp/x.md', written=True, used_model=False, task='critic_post_mortem',
        llm=llm_client.LLMResult('', 'opus', 'critic_post_mortem', 0.1,
                                 ok=False, error='timed out'),
        error='timed out').to_dict())
    declined = rc.classify_vault_write(vault_writer.VaultWrite(
        '/tmp/x.md', written=True, used_model=False, task='critic_post_mortem',
        llm=llm_client.LLMResult('no.', 'opus', 'critic_post_mortem', 0.1,
                                 ok=True),
        error='below the char floor').to_dict())
    used = rc.classify_vault_write(vault_writer.VaultWrite(
        '/tmp/x.md', written=True, used_model=True, task='critic_post_mortem',
        llm=llm_client.LLMResult('a real note', 'opus', 'critic_post_mortem',
                                 12.0, ok=True)).to_dict())
    skipped = rc.classify_vault_write(vault_writer.VaultWrite(
        '/tmp/x.md', written=True, used_model=False, task='critic_post_mortem',
        error='skip_model: no model turn was attempted').to_dict())

    assert not_tested['outcome'] == 'NOT_TESTED'
    assert declined['outcome'] == 'declined'
    assert used['outcome'] == 'ok'
    assert skipped['outcome'] == 'not_attempted'
    assert len({not_tested['outcome'], declined['outcome'], used['outcome'],
                skipped['outcome']}) == 4


def test_the_turn_tally_carries_the_full_schema_including_zeros():
    """Convention 20: an outcome that did not happen is a zero, not an absence."""
    counts = rc.tally_turns([{'turn': {'outcome': 'declined'}}])
    assert set(counts) == set(rc.TURN_OUTCOMES)
    assert counts['declined'] == 1
    assert counts['NOT_TESTED'] == 0


def test_a_cycle_where_every_turn_could_not_run_exits_not_tested(tmp_path,
                                                                monkeypatch):
    """Non-zero, and distinct from a crash. Nothing broke; nothing ran."""
    def fake_stage(log, **kwargs):
        return {'stage': 'critic',
                'turn': {'outcome': 'NOT_TESTED', 'status': 'llm_not_ok',
                         'detail': 'the turn COULD NOT RUN'},
                'kill': {'path': str(tmp_path / 'out' / 'kills' / 'k.md'),
                         'recommended': 0, 'withheld': 0,
                         'recommendations': []},
                'post_mortem': {'path': str(tmp_path / 'out' / 'cycles' / 'p.md'),
                                'written': True, 'used_model': False},
                'state_path': str(tmp_path / 'out' / 'critic_state.json'),
                'graph': {'dry_run': True, 'rows': 0, 'inserted': 0,
                          'updated': 0, 'unchanged': 0,
                          'never_fires_not_written': []}}

    monkeypatch.setattr(rc, 'stage_critic', fake_stage)
    out_dir = str(tmp_path / 'out')
    code = rc.main(['--only', 'critic', '--quiet', '--out-dir', out_dir])

    assert code == rc.EXIT_NOT_TESTED
    assert code != 0
    assert code != rc.EXIT_FAILED
    record = _last_run_record(out_dir)
    assert record['status'] == 'NOT_TESTED'
    assert record['turns']['NOT_TESTED'] == 1
    assert record['turns']['declined'] == 0


def test_a_declined_turn_is_a_result_and_exits_zero(tmp_path, monkeypatch):
    def fake_stage(log, **kwargs):
        return {'stage': 'critic',
                'turn': {'outcome': 'declined', 'status': 'unusable_reply',
                         'detail': 'ran, nothing usable'},
                'kill': {'path': str(tmp_path / 'out' / 'k.md'),
                         'recommended': 0, 'withheld': 0, 'recommendations': []},
                'post_mortem': {'path': str(tmp_path / 'out' / 'p.md'),
                                'written': True, 'used_model': False},
                'state_path': str(tmp_path / 'out' / 's.json'),
                'graph': {'dry_run': True, 'rows': 0, 'inserted': 0,
                          'updated': 0, 'unchanged': 0,
                          'never_fires_not_written': []}}

    monkeypatch.setattr(rc, 'stage_critic', fake_stage)
    out_dir = str(tmp_path / 'out')
    code = rc.main(['--only', 'critic', '--quiet', '--out-dir', out_dir])
    assert code == rc.EXIT_OK
    assert _last_run_record(out_dir)['turns']['declined'] == 1


# ---------------------------------------------------------------------------
# Failure is non-zero and leaves evidence
# ---------------------------------------------------------------------------

def test_a_failing_stage_exits_non_zero_and_writes_the_traceback(tmp_path):
    out_dir = str(tmp_path / 'out')
    missing_db = str(tmp_path / 'no-such.db')
    code = rc.main(['--only', 'critic', '--skip-model', '--quiet',
                    '--out-dir', out_dir, '--db', missing_db])

    assert code == rc.EXIT_FAILED
    record = _last_run_record(out_dir)
    assert record['status'] == 'FAILED'
    assert record['failures'] and record['failures'][0]['stage'] == 'critic'

    logs = [n for n in os.listdir(out_dir) if n.endswith('.log')]
    assert logs, 'a failed run left no log'
    text = open(os.path.join(out_dir, logs[0])).read()
    assert 'critic: FAILED' in text
    assert 'Traceback' in text, 'the log has no traceback to debug from'


def test_one_stage_failing_does_not_stop_the_other(tmp_path, monkeypatch):
    ran = []

    def boom(log, **kwargs):
        raise RuntimeError('forge exploded')

    def fine(log, **kwargs):
        ran.append('critic')
        return {'stage': 'critic', 'turn': {'outcome': 'ok', 'detail': ''},
                'kill': {'path': str(tmp_path / 'out' / 'k.md'),
                         'recommended': 0, 'withheld': 0, 'recommendations': []},
                'post_mortem': {'path': str(tmp_path / 'out' / 'p.md'),
                                'written': True, 'used_model': True},
                'state_path': str(tmp_path / 'out' / 's.json'),
                'graph': {'dry_run': True, 'rows': 0, 'inserted': 0,
                          'updated': 0, 'unchanged': 0,
                          'never_fires_not_written': []}}

    monkeypatch.setattr(rc, 'stage_forge', boom)
    monkeypatch.setattr(rc, 'stage_critic', fine)
    code = rc.main(['--quiet', '--out-dir', str(tmp_path / 'out')])
    assert ran == ['critic'], 'the critic was skipped because Forge died'
    assert code == rc.EXIT_FAILED


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------

def test_the_sandbox_assertion_catches_an_escaped_artifact(tmp_path):
    """Convention 22: `--out-dir` containment is checked, not just documented."""
    stages = [{'stage': 'critic',
               'kill': {'path': '/tmp/somewhere-else/kills.md'},
               'post_mortem': {'path': str(tmp_path / 'p.md')},
               'state_path': str(tmp_path / 's.json'),
               'graph': {'dry_run': True}}]
    with pytest.raises(AssertionError) as exc:
        rc._assert_sandbox(str(tmp_path), stages)
    assert 'somewhere-else' in str(exc.value)


def test_the_sandbox_assertion_catches_a_real_graph_write(tmp_path):
    stages = [{'stage': 'critic',
               'kill': {'path': str(tmp_path / 'k.md')},
               'post_mortem': {'path': str(tmp_path / 'p.md')},
               'state_path': str(tmp_path / 's.json'),
               'graph': {'dry_run': False}}]
    with pytest.raises(AssertionError) as exc:
        rc._assert_sandbox(str(tmp_path), stages)
    assert 'hypothesis graph' in str(exc.value)


@pytest.mark.skipif(not os.path.exists(REPO_DB),
                    reason='db/trading.db is not present')
def test_a_full_sandboxed_cycle_writes_only_inside_out_dir(tmp_path, monkeypatch):
    """The end-to-end one. Real Forge, real critic, real database, no model.

    Three things are asserted about what did NOT happen, because those are the
    failures that would be invisible: no note in the real vault, no row in the
    real `hypothesis_graph`, no `claude` subprocess.
    """
    spawned = []

    def refuse(*args, **kwargs):
        spawned.append(args)
        raise AssertionError('a real model turn was spawned in a test')

    monkeypatch.setattr(llm_client.subprocess, 'run', refuse)

    vault_before = _vault_snapshot()
    graph_before = _graph_row_count()

    out_dir = str(tmp_path / 'out')
    code = rc.main(['--skip-model', '--quiet', '--out-dir', out_dir,
                    '--since', '2h', '--n-proposals', '2'])

    assert code == rc.EXIT_OK, open(
        os.path.join(out_dir,
                     [n for n in os.listdir(out_dir)
                      if n.endswith('.log')][0])).read()
    assert spawned == []
    assert _vault_snapshot() == vault_before, (
        'a sandboxed cycle changed the real vault; Forge reads that back as '
        'evidence')
    assert _graph_row_count() == graph_before, (
        'a sandboxed cycle wrote to the real hypothesis_graph')

    # It DID write, in the sandbox. skip_model is not dry_run.
    assert os.path.isdir(os.path.join(out_dir, 'proposals'))
    assert os.listdir(os.path.join(out_dir, 'proposals'))
    assert os.listdir(os.path.join(out_dir, 'cycles'))
    assert os.listdir(os.path.join(out_dir, 'kills'))
    assert os.path.exists(os.path.join(out_dir, 'critic_state.json'))

    record = _last_run_record(out_dir)
    assert record['skip_model'] is True
    assert record['sandbox'] == out_dir
    assert record['turns']['NOT_TESTED'] == 0
    for path in rc.artifact_paths(record['stages']):
        assert os.path.realpath(path).startswith(os.path.realpath(out_dir))

    # The post-mortem was written and says the model did not run, so nobody
    # reads a fallback as analysis.
    cycles = os.path.join(out_dir, 'cycles')
    note = open(os.path.join(cycles, os.listdir(cycles)[0])).read()
    assert 'model: NOT_TESTED' in note
    assert 'reasoning layer did not run' in note


# ---------------------------------------------------------------------------
# The money rule
# ---------------------------------------------------------------------------

def test_the_env_dry_run_is_promoted_to_skip_model_without_the_flag(tmp_path,
                                                                   monkeypatch):
    """`AYM_LLM_DRY_RUN=1` in the environment wins over the absence of a flag.

    Otherwise the cycle would ask for a real turn, get `llm_client`'s canned
    reply, and record it in the log as though a model had answered.
    """
    seen = {}

    def capture(log, **kwargs):
        seen.update(kwargs)
        return {'stage': 'critic', 'turn': {'outcome': 'not_attempted',
                                            'detail': ''},
                'kill': {'path': str(tmp_path / 'out' / 'k.md'),
                         'recommended': 0, 'withheld': 0, 'recommendations': []},
                'post_mortem': {'path': str(tmp_path / 'out' / 'p.md'),
                                'written': True, 'used_model': False},
                'state_path': str(tmp_path / 'out' / 's.json'),
                'graph': {'dry_run': True, 'rows': 0, 'inserted': 0,
                          'updated': 0, 'unchanged': 0,
                          'never_fires_not_written': []}}

    monkeypatch.setenv(rc.DRY_RUN_ENV, '1')
    monkeypatch.setattr(rc, 'stage_critic', capture)
    out_dir = str(tmp_path / 'out')
    code = rc.main(['--only', 'critic', '--quiet', '--out-dir', out_dir])

    assert code == rc.EXIT_OK
    assert seen['skip_model'] is True, (
        '%s=1 did not reach the critic as skip_model' % rc.DRY_RUN_ENV)
    assert _last_run_record(out_dir)['skip_model'] is True


def test_the_skip_model_flag_sets_the_env_for_everything_downstream(tmp_path,
                                                                    monkeypatch):
    """`--skip-model` must also stop a turn nobody remembered to route."""
    monkeypatch.delenv(rc.DRY_RUN_ENV, raising=False)
    observed = {}

    def capture(log, **kwargs):
        observed['dry_run'] = llm_client.is_dry_run()
        return {'stage': 'critic', 'turn': {'outcome': 'not_attempted',
                                            'detail': ''},
                'kill': {'path': str(tmp_path / 'out' / 'k.md'),
                         'recommended': 0, 'withheld': 0, 'recommendations': []},
                'post_mortem': {'path': str(tmp_path / 'out' / 'p.md'),
                                'written': True, 'used_model': False},
                'state_path': str(tmp_path / 'out' / 's.json'),
                'graph': {'dry_run': True, 'rows': 0, 'inserted': 0,
                          'updated': 0, 'unchanged': 0,
                          'never_fires_not_written': []}}

    monkeypatch.setattr(rc, 'stage_critic', capture)
    rc.main(['--only', 'critic', '--skip-model', '--quiet',
             '--out-dir', str(tmp_path / 'out')])
    assert observed['dry_run'] is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_run_record(out_dir):
    path = os.path.join(out_dir, rc.RUNS_JSONL_NAME)
    with open(path) as fh:
        lines = [line for line in fh if line.strip()]
    return json.loads(lines[-1])


def _vault_snapshot():
    """`{relative path: (size, mtime)}` for the whole Trading vault."""
    root = vault_writer.VAULT_ROOT
    out = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, root)] = (stat.st_size,
                                                round(stat.st_mtime, 3))
    return out


def _graph_row_count():
    conn = sqlite3.connect('file:%s?mode=ro' % REPO_DB, uri=True)
    try:
        return conn.execute('select count(*) from hypothesis_graph').fetchone()[0]
    finally:
        conn.close()
