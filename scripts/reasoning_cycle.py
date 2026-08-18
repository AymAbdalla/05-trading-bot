#!/usr/bin/env python3
"""One 4-hourly reasoning cycle: Forge-with-Opus, then the critic.

This is the SCHEDULING layer and nothing else. Every piece of behaviour it
invokes already existed before this file:

  agents/forge.py --reasoner    Opus proposes, `forge.validate()` refuses,
                                Python writes strategies/proposals/*.md
  agents/critic.py              classifies losers, updates hypothesis_graph,
                                writes the kill file, asks Opus for the
                                post-mortem via agents/vault_writer.py

So this module adds four things that a cron entry needs and neither agent has:

1. **A lock.** The working directory is shared and both halves write to the
   vault, to `strategies/proposals/`, and to `hypothesis_graph` in the live
   trading database. Two overlapping cron firings would interleave those
   writes. `flock` on a lock file, non-blocking: the second firing exits
   `EXIT_LOCK_BUSY` immediately rather than queueing behind the first.

2. **A timestamped log.** `logs/reasoning_cycle_<UTC>.log` plus one appended
   JSON record per run in `logs/reasoning_cycle_runs.jsonl`. A run that fails
   at 04:00 has to leave evidence that is still there at 09:00.

3. **Convention 11, counted.** A model turn has FOUR outcomes and they are four
   different facts, so they get four counters and never share one
   (convention 20):

       not_attempted   we chose not to spawn a turn (--skip-model, or
                       AYM_LLM_DRY_RUN=1 in the environment)
       NOT_TESTED      the turn COULD NOT RUN: no `claude` binary, a timeout,
                       a non-zero exit. `LLMResult.ok is False`.
       declined        the turn RAN and produced nothing we could use. For
                       Forge that is `unusable_reply` / `no_candidates`; for a
                       vault note it is `llm.ok is True` with `used_model`
                       False. This is a RESULT, and an empty one.
       ok              the turn ran and we used what it said.

   A cycle in which every turn was NOT_TESTED exits `EXIT_NOT_TESTED` (3),
   which is non-zero but distinct from a crash: nothing broke, the reasoning
   layer simply did not run, and that must not read as "the model had no
   ideas".

4. **`--out-dir`, a sandbox.** With `--out-dir DIR`, EVERY artifact goes under
   DIR and nothing at all is written outside it: no vault note, no
   `hypothesis_graph` row, no proposal in the repo, no critic bookmark. That is
   what makes a smoke test safe. It is enforced, not promised: `_assert_sandbox`
   checks every recorded path at the end of the run. `vault_writer` under its
   old flag name once deposited a synthetic note into the real vault, and Forge
   reads the vault back as evidence, so a polluted vault is not a cosmetic
   problem.

`--skip-model` is `skip_model`, NOT `dry_run`. It skips the MODEL and still
WRITES the files, because the thing being smoke-tested is the plumbing.

Usage:

    # smoke, writes nothing outside the sandbox, spends no tokens
    env -u PYTHONPATH .venv/bin/python scripts/reasoning_cycle.py \\
        --skip-model --out-dir /tmp/rc

    # what cron runs
    env -u PYTHONPATH .venv/bin/python scripts/reasoning_cycle.py
"""
import argparse
import errno
import fcntl
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TextIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOG_DIR = os.path.join(ROOT, 'logs')
DEFAULT_DB = os.path.join(ROOT, 'db', 'trading.db')
DEFAULT_LOCK = os.path.join(LOG_DIR, 'reasoning_cycle.lock')
RUNS_JSONL_NAME = 'reasoning_cycle_runs.jsonl'

EXIT_OK = 0
EXIT_FAILED = 1
#: The cycle completed but every model turn COULD NOT RUN. Non-zero so cron
#: notices, distinct from 1 so a reader knows nothing crashed.
EXIT_NOT_TESTED = 3
#: BSD sysexits EX_TEMPFAIL. Another cycle holds the lock; try again later.
EXIT_LOCK_BUSY = 75

#: Convention 20: the closed vocabulary of what happened to a model turn. Every
#: outcome is a separate counter and they sum to the number of turns considered.
TURN_OUTCOMES = ('ok', 'declined', 'NOT_TESTED', 'not_attempted')

DRY_RUN_ENV = 'AYM_LLM_DRY_RUN'


def utc_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class Tee(object):
    """Write every line to the log file AND to stdout, flushing as we go.

    Unbuffered on purpose. A cycle that dies half way through has to leave the
    lines it had already emitted on disk; a buffered log of a crashed run is
    the case where the log is most needed and least likely to exist.
    """

    def __init__(self, path: str, echo: bool = True) -> None:
        self.path = path
        self.echo = echo
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh: TextIO = open(path, 'a')

    def __call__(self, message: str = '') -> None:
        line = '%s %s' % (datetime.now(timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'), message) if message else ''
        self._fh.write(line + '\n')
        self._fh.flush()
        if self.echo:
            sys.stdout.write(line + '\n')
            sys.stdout.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------

class CycleLock(object):
    """`flock`-based mutual exclusion for one reasoning cycle.

    `flock` and not a pid file: the kernel drops the lock when the process
    dies, so a cycle killed by a reboot or an OOM does not leave a stale lock
    that blocks every subsequent firing. The pid and start time are written
    INTO the file purely so a human can see who holds it; they are advisory
    text, not the lock.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: Optional[int] = None
        self.holder: str = ''

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                os.close(fd)
                raise
            try:
                self.holder = os.read(fd, 4096).decode('utf-8', 'replace').strip()
            except OSError:
                self.holder = '(unreadable)'
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, ('pid=%d started=%s\n'
                      % (os.getpid(),
                         datetime.now(timezone.utc).isoformat())).encode())
        os.fsync(fd)
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> 'CycleLock':
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Classifying what happened to a model turn (convention 11)
# ---------------------------------------------------------------------------

def classify_reasoner(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Forge's reasoner record -> one of `TURN_OUTCOMES`.

    `forge_reasoner` already draws the line in the right place; this only maps
    its vocabulary onto the cycle's counters without collapsing any of it.
    """
    if record is None:
        return {'outcome': 'not_attempted', 'status': None,
                'detail': 'the reasoner was not asked for'}
    status = record.get('status')
    error = record.get('error')
    if status == 'NOT_TESTED':
        return {'outcome': 'NOT_TESTED', 'status': status,
                'detail': 'the Opus turn COULD NOT RUN: %s' % (error or '?')}
    if status == 'ok':
        return {'outcome': 'ok', 'status': status,
                'detail': '%d candidate(s) returned'
                          % record.get('candidates_kept', 0)}
    # 'no_candidates' and 'unusable_reply' both mean the turn RAN.
    return {'outcome': 'declined', 'status': status,
            'detail': 'the turn RAN and produced nothing usable (%s); this is '
                      'a result, not NOT_TESTED' % status}


def classify_vault_write(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`VaultWrite.to_dict()` -> one of `TURN_OUTCOMES`.

    `llm is None` means no turn was ever spawned (`skip_model`). `llm.ok is
    False` means the turn could not run. `llm.ok is True` with `used_model`
    False means it ran and said something too short or too empty to use. Three
    different facts, three different outcomes.
    """
    if record is None:
        return {'outcome': 'not_attempted', 'status': None,
                'detail': 'no post-mortem was attempted'}
    llm = record.get('llm')
    if llm is None:
        return {'outcome': 'not_attempted', 'status': 'skip_model',
                'detail': record.get('error') or 'no model turn was attempted'}
    if not llm.get('ok'):
        return {'outcome': 'NOT_TESTED', 'status': 'llm_not_ok',
                'detail': 'the turn COULD NOT RUN: %s'
                          % (record.get('error') or llm.get('error') or '?')}
    if record.get('used_model'):
        return {'outcome': 'ok', 'status': 'used_model',
                'detail': '%s wrote the note in %.1fs'
                          % (llm.get('model'), llm.get('duration_s', 0.0))}
    return {'outcome': 'declined', 'status': 'unusable_reply',
            'detail': 'the turn RAN and returned nothing usable, so the '
                      'deterministic fallback was written: %s'
                      % (record.get('error') or '?')}


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_forge(log: Tee, *, db_path: str, n_proposals: int,
                proposals_dir: Optional[str]) -> Dict[str, Any]:
    """Run `forge.py --reasoner` in process and read back its run record.

    In process, and through `forge.main()` rather than through a
    reimplementation of it, because there must be exactly one definition of
    what a Forge run is (convention 23). The run record it appends to
    `forge.RUN_LOG` is the structured result; parsing its stdout would be a
    second, worse definition.

    `proposals_dir` monkeypatches `forge.PROPOSALS_DIR` and `forge.RUN_LOG`.
    Both are read at call time inside the functions that use them, which is the
    documented way Forge's own tests point it at a temporary directory.
    """
    from agents import forge

    saved = (forge.PROPOSALS_DIR, forge.RUN_LOG)
    if proposals_dir:
        forge.PROPOSALS_DIR = proposals_dir
        forge.RUN_LOG = os.path.join(proposals_dir, 'forge_runs.jsonl')
    run_log_path = forge.RUN_LOG
    before = _jsonl_len(run_log_path)

    argv = ['--reasoner',
            '--shadow-results', db_path,
            '--n-proposals', str(n_proposals)]
    log('forge: argv=%s' % ' '.join(argv))
    log('forge: proposals -> %s' % forge.PROPOSALS_DIR)
    try:
        rc = forge.main(argv)
    finally:
        forge.PROPOSALS_DIR, forge.RUN_LOG = saved

    record = _last_jsonl(run_log_path) if _jsonl_len(run_log_path) > before else None
    if record is None:
        raise RuntimeError('forge.main() returned %r but appended no record to '
                           '%s; the run is unverifiable' % (rc, run_log_path))

    turn = classify_reasoner(record.get('reasoner'))
    written = record.get('written', [])
    refused = record.get('refused', [])
    warned = record.get('warned', [])

    log('forge: reasoner outcome=%s (%s)' % (turn['outcome'], turn['detail']))
    if record.get('reasoner', {}).get('fell_back_to_deterministic'):
        log('forge: FELL BACK to the hand written candidate list, reason=%s'
            % record['reasoner'].get('fallback_reason'))
    log('forge: screened %d, wrote %d, refused %d, warned %d'
        % (record.get('candidates_screened', 0), len(written), len(refused),
           len(warned)))
    for row in written:
        log('forge:   WROTE   %s' % row.get('path'))
    for row in refused:
        log('forge:   REFUSED %s: %s' % (row.get('name'), row.get('category')))

    return {
        'stage': 'forge',
        'exit_code': rc,
        'run_log': run_log_path,
        'proposals_dir': forge.PROPOSALS_DIR if not proposals_dir else proposals_dir,
        'turn': turn,
        'candidates_screened': record.get('candidates_screened', 0),
        'written': len(written),
        'refused': len(refused),
        'warned': len(warned),
        'written_paths': [r.get('path') for r in written],
        'reasoner': record.get('reasoner'),
    }


def stage_critic(log: Tee, *, db_path: str, since: str, until: Optional[str],
                 threshold: int, skip_model: bool, sandbox: Optional[str],
                 state_path: str) -> Dict[str, Any]:
    """Classify the window, update the graph, write the kill file and the note.

    Called through the critic's own API rather than its CLI for one reason:
    `critic.main()` has no `--out-dir`, so there would be no way to run a smoke
    test that cannot reach the real vault. Every call below is the same
    function `main()` calls, in the same order, with the same arguments.
    """
    from agents import critic

    state = critic.load_state(state_path)
    since_ts, since_how = critic.parse_since(since, state=state)
    until_ts = (critic.parse_since(until, state=state)[0] if until
                else critic.now_ms())
    log('critic: window resolved from --since %r: %s' % (since, since_how))

    result = critic.classify_window(since_ts, until_ts, db_path=db_path)
    summary = result['summary']
    log('critic: %s .. %s' % (summary['since_iso'], summary['until_iso']))
    log('critic: closed=%d winners=%d losers=%d flat=%d'
        % (summary['closed'], summary['winners'], summary['losers'],
           summary['flat']))
    for mode, count in sorted(summary['by_mode'].items(),
                              key=lambda kv: (-kv[1], kv[0])):
        log('critic:   mode %-22s %d' % (mode, count))

    # --- hypothesis graph -------------------------------------------------
    graph = critic.update_hypothesis_graph(result, db_path=db_path,
                                           dry_run=bool(sandbox))
    log('critic: hypothesis_graph %s: %d row(s), inserted=%d updated=%d '
        'unchanged=%d'
        % ('SANDBOX, nothing written' if sandbox else 'written',
           len(graph['rows']), graph['inserted'], graph['updated'],
           graph['unchanged']))
    if graph['never_fires_not_written']:
        log('critic:   never_fires NOT written for %d strategy(ies) (%s): %s'
            % (len(graph['never_fires_not_written']),
               graph['never_fires_not_written_because'],
               ', '.join(graph['never_fires_not_written'])))

    # --- kill recommendations ---------------------------------------------
    kill_dir = (os.path.join(sandbox, 'kills') if sandbox else critic.KILL_DIR)
    kill = critic.write_kill_recommendations(result, out_dir=kill_dir,
                                             threshold=threshold)
    recommended = [r for r in kill['recommendations'] if r['recommended']]
    withheld = [r for r in kill['recommendations'] if not r['recommended']]
    log('critic: kill file -> %s (%d recommended, %d withheld)'
        % (kill['path'], len(recommended), len(withheld)))
    for rec in recommended:
        log('critic:   KILL     %-30s %-22s x%d over %d closed, pnl %+.2f %s'
            % (rec['strategy'], rec['failure_mode'], rec['occurrences'],
               rec['closed_trades_in_window'], rec['pnl_net'],
               'PROVISIONAL' if rec['provisional'] else 'SUPPORTED'))
    for rec in withheld:
        log('critic:   WITHHELD %-30s %-22s x%d net %+.2f'
            % (rec['strategy'], rec['failure_mode'], rec['occurrences'],
               rec['pnl_net']))

    # --- post-mortem ------------------------------------------------------
    cycles_dir = (os.path.join(sandbox, 'cycles') if sandbox else None)
    write = critic.write_post_mortem(result, out_dir=cycles_dir,
                                     skip_model=skip_model)
    write_d = write.to_dict()
    turn = classify_vault_write(write_d)
    log('critic: post-mortem -> %s' % write.path)
    log('critic: post-mortem turn outcome=%s (%s)'
        % (turn['outcome'], turn['detail']))

    # --- bookmark ---------------------------------------------------------
    critic.save_state(until_ts, state_path,
                      extra={'last_closed_trades': summary['closed'],
                             'last_losers': summary['losers']})
    log('critic: bookmark moved to %s in %s' % (summary['until_iso'],
                                                state_path))

    return {
        'stage': 'critic',
        'window': {'since_iso': summary['since_iso'],
                   'until_iso': summary['until_iso'],
                   'since_how': since_how},
        'closed': summary['closed'],
        'winners': summary['winners'],
        'losers': summary['losers'],
        'by_mode': dict(summary['by_mode']),
        'graph': {'dry_run': graph['dry_run'],
                  'rows': len(graph['rows']),
                  'inserted': graph['inserted'],
                  'updated': graph['updated'],
                  'unchanged': graph['unchanged'],
                  'never_fires_not_written': graph['never_fires_not_written']},
        'kill': {'path': kill['path'],
                 'recommended': len(recommended),
                 'withheld': len(withheld),
                 'recommendations': [
                     {'strategy': r['strategy'],
                      'failure_mode': r['failure_mode'],
                      'occurrences': r['occurrences'],
                      'pnl_net': r['pnl_net'],
                      'provisional': r['provisional']}
                     for r in recommended]},
        'post_mortem': {'path': write.path, 'written': write.written,
                        'used_model': write.used_model},
        'turn': turn,
        'state_path': state_path,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonl_len(path: str) -> int:
    try:
        with open(path) as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _last_jsonl(path: str) -> Optional[Dict[str, Any]]:
    last = None
    try:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    last = line
    except OSError:
        return None
    if last is None:
        return None
    try:
        return json.loads(last)
    except ValueError:
        return None


def artifact_paths(stages: List[Dict[str, Any]]) -> List[str]:
    """Every path a stage says it wrote to. Used by the sandbox assertion."""
    paths: List[str] = []
    for stage in stages:
        if stage.get('stage') == 'forge':
            paths.append(stage.get('run_log', ''))
            paths.extend(p for p in stage.get('written_paths', []) if p)
        elif stage.get('stage') == 'critic':
            paths.append(stage['kill']['path'])
            paths.append(stage['post_mortem']['path'])
            paths.append(stage['state_path'])
    return [p for p in paths if p]


def _assert_sandbox(sandbox: str, stages: List[Dict[str, Any]]) -> None:
    """Refuse to report success if anything landed outside the sandbox.

    Convention 22: `--out-dir` promising containment is a claim in a docstring.
    This is the check.
    """
    root = os.path.realpath(sandbox)
    escaped = [p for p in artifact_paths(stages)
               if os.path.commonpath([root, os.path.realpath(p)]) != root]
    if escaped:
        raise AssertionError(
            'sandbox violated: --out-dir was %s but these artifacts landed '
            'outside it: %s' % (sandbox, ', '.join(escaped)))
    for stage in stages:
        if stage.get('stage') == 'critic' and not stage['graph']['dry_run']:
            raise AssertionError('sandbox violated: the hypothesis graph was '
                                 'written for real under --out-dir')


def tally_turns(stages: List[Dict[str, Any]]) -> Dict[str, int]:
    """Convention 20: the full schema with the zeros in it."""
    counts = {outcome: 0 for outcome in TURN_OUTCOMES}
    for stage in stages:
        turn = stage.get('turn')
        if turn:
            counts[turn['outcome']] = counts.get(turn['outcome'], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='scripts/reasoning_cycle.py',
        description='One reasoning cycle: Forge (Opus) then the critic.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default=DEFAULT_DB,
                        help='trading database (default db/trading.db)')
    parser.add_argument('--since', default='last',
                        help="critic window start: 'last', 4h, an ISO date, "
                             'or an epoch (default: last)')
    parser.add_argument('--until', default=None,
                        help='critic window end (default: now)')
    parser.add_argument('--n-proposals', type=int, default=3,
                        help='candidates to ask Opus for (default 3)')
    parser.add_argument('--threshold', type=int, default=None,
                        help='same-mode occurrences before a kill is '
                             'recommended (default: the critic\'s own)')
    parser.add_argument('--skip-model', action='store_true',
                        help='skip every MODEL turn; still write every file. '
                             'This is skip_model, not dry_run.')
    parser.add_argument('--out-dir', default=None,
                        help='SANDBOX. Every artifact goes under this '
                             'directory and nothing is written outside it: no '
                             'vault note, no hypothesis_graph row, no proposal '
                             'in the repo, no critic bookmark.')
    parser.add_argument('--only', choices=('forge', 'critic', 'both'),
                        default='both',
                        help='run one half of the cycle (default both)')
    parser.add_argument('--lock-file', default=None,
                        help='lock path (default logs/reasoning_cycle.lock, '
                             'or <out-dir>/reasoning_cycle.lock in a sandbox)')
    parser.add_argument('--log-dir', default=None,
                        help='where the timestamped log goes (default logs/, '
                             'or <out-dir> in a sandbox)')
    parser.add_argument('--quiet', action='store_true',
                        help='log to the file only, not to stdout')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from agents import critic

    args = build_parser().parse_args(argv)
    sandbox = os.path.abspath(args.out_dir) if args.out_dir else None
    if sandbox:
        os.makedirs(sandbox, exist_ok=True)

    log_dir = args.log_dir or (sandbox or LOG_DIR)
    lock_path = args.lock_file or (
        os.path.join(sandbox, 'reasoning_cycle.lock') if sandbox
        else DEFAULT_LOCK)
    started = datetime.now(timezone.utc)
    log_path = os.path.join(log_dir, 'reasoning_cycle_%s.log'
                            % utc_stamp(started))

    # An AYM_LLM_DRY_RUN already in the environment is NOT overridden: it is
    # the money rule and it wins. It is promoted to --skip-model so the log
    # says which turns did not happen instead of recording a canned reply as a
    # real one.
    env_dry_run = os.environ.get(DRY_RUN_ENV, '') == '1'
    skip_model = args.skip_model or env_dry_run
    if args.skip_model:
        os.environ[DRY_RUN_ENV] = '1'

    log = Tee(log_path, echo=not args.quiet)
    lock = CycleLock(lock_path)
    stages: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []

    log('=== reasoning cycle %s ===' % utc_stamp(started))
    log('root=%s pid=%d' % (ROOT, os.getpid()))
    log('db=%s only=%s n_proposals=%d' % (args.db, args.only, args.n_proposals))
    log('skip_model=%s (flag=%s, %s=%s) sandbox=%s'
        % (skip_model, args.skip_model, DRY_RUN_ENV,
           os.environ.get(DRY_RUN_ENV, ''), sandbox or 'NONE, this is a real run'))
    log('lock=%s log=%s' % (lock_path, log_path))

    if not lock.acquire():
        log('LOCK BUSY: another reasoning cycle holds %s (%s). Exiting %d '
            'without running anything.'
            % (lock_path, lock.holder or 'holder unknown', EXIT_LOCK_BUSY))
        log.close()
        return EXIT_LOCK_BUSY
    log('lock acquired')

    try:
        if args.only in ('forge', 'both'):
            log('--- forge ---')
            try:
                stages.append(stage_forge(
                    log, db_path=args.db, n_proposals=args.n_proposals,
                    proposals_dir=(os.path.join(sandbox, 'proposals')
                                   if sandbox else None)))
            except Exception as exc:  # noqa: BLE001 - a stage must not kill the cycle
                failures.append({'stage': 'forge', 'error': repr(exc)})
                log('forge: FAILED %r' % exc)
                for line in traceback.format_exc().splitlines():
                    log('forge:   %s' % line)

        if args.only in ('critic', 'both'):
            log('--- critic ---')
            try:
                stages.append(stage_critic(
                    log, db_path=args.db, since=args.since, until=args.until,
                    threshold=(args.threshold if args.threshold is not None
                               else critic.KILL_THRESHOLD),
                    skip_model=skip_model, sandbox=sandbox,
                    state_path=(os.path.join(sandbox, 'critic_state.json')
                                if sandbox else critic.STATE_PATH)))
            except Exception as exc:  # noqa: BLE001
                failures.append({'stage': 'critic', 'error': repr(exc)})
                log('critic: FAILED %r' % exc)
                for line in traceback.format_exc().splitlines():
                    log('critic:   %s' % line)

        if sandbox and not failures:
            _assert_sandbox(sandbox, stages)
            log('sandbox assertion passed: every artifact is under %s' % sandbox)
    finally:
        lock.release()
        log('lock released')

    turns = tally_turns(stages)
    log('--- model turns (convention 11) ---')
    for outcome in TURN_OUTCOMES:
        log('  %-14s %d' % (outcome, turns[outcome]))
    log('  NOT_TESTED means the turn COULD NOT RUN. `declined` means it ran '
        'and returned nothing usable. They are not the same fact.')

    if failures:
        status, code = 'FAILED', EXIT_FAILED
    elif turns['NOT_TESTED'] and not turns['ok'] and not turns['declined']:
        status, code = 'NOT_TESTED', EXIT_NOT_TESTED
    else:
        status, code = 'ok', EXIT_OK

    finished = datetime.now(timezone.utc)
    record = {
        'started_utc': started.isoformat(),
        'finished_utc': finished.isoformat(),
        'duration_s': round((finished - started).total_seconds(), 1),
        'status': status,
        'exit_code': code,
        'skip_model': skip_model,
        'sandbox': sandbox,
        'db': args.db,
        'only': args.only,
        'log': log_path,
        'turns': turns,
        'failures': failures,
        'stages': stages,
    }
    runs_jsonl = os.path.join(sandbox or LOG_DIR, RUNS_JSONL_NAME)
    try:
        os.makedirs(os.path.dirname(runs_jsonl), exist_ok=True)
        with open(runs_jsonl, 'a') as fh:
            # Convention 19: allow_nan=False, so a NaN raises here instead of
            # producing a line no strict reader can parse.
            fh.write(json.dumps(record, allow_nan=False, sort_keys=True,
                                default=str) + '\n')
        log('run record appended to %s' % runs_jsonl)
    except (OSError, ValueError) as exc:
        log('WARN could not append the run record to %s: %r' % (runs_jsonl, exc))

    log('=== cycle %s, exit %d, %.1fs ===' % (status, code, record['duration_s']))
    log.close()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
