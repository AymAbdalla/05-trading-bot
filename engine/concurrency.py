"""Optimistic concurrency control for a working tree shared by several agents.

Convention 21 says this directory is SHARED. On 2026-08-17 three Cody sessions
ran at once and one of them overwrote another's corrections in three files. This
module is the mechanism that turns that silent overwrite into a loud, catchable
`ConcurrentModificationError`.

    from engine.concurrency import checkout, checkin, ConcurrentModificationError

    ctx = checkout('strategies/polymarket/fair_value_arb.py')
    new_content = transform(ctx.content)        # your work happens here
    try:
        checkin(ctx, new_content)
    except ConcurrentModificationError as exc:
        print(exc.diff)                          # what THEY changed
        # re-read, reconcile, retry

It is OPTIMISTIC: nothing is locked while you work, so two agents may work on
the same file at once. The second one to finish LOSES and is told so, with a
diff, instead of winning silently. That is the whole trade - it costs a retry
and it buys never losing an edit.


WHAT THIS PROTECTS AGAINST, AND WHAT IT DOES NOT
------------------------------------------------
It protects against any writer that goes through THIS MODULE. It cannot protect
against a writer that does not - a plain `open(path, 'w')`, an editor, a `git
checkout`, or Claude Code's own Write/Edit tools. Those still clobber whatever
is there.

What it gives you against those writers is DETECTION, not prevention: if an
outside process changed the file after your checkout, your checkin refuses and
shows you the diff. Your edit is not lost; you are made to reconcile it. Stating
this precisely matters, because "we have concurrency control now" is exactly the
kind of claim that stops people checking (convention 22 - a claim in a docstring
is not a wiring test; the tests in tests/test_concurrency.py are).

The verify-then-write critical section inside `checkin` is additionally guarded
by an advisory `flock`, so two agents BOTH using this module cannot interleave
between the hash re-read and the rename. Without that lock the check would be a
textbook TOCTOU race with a window of a few milliseconds. The lock is held only
across the write, never across your work: holding it across the work would make
this pessimistic locking, and a crashed agent would then wedge the file for
everyone.

Lock files live in the system temp directory, keyed by a hash of the absolute
path, NOT next to the file being written. Lock files in the tree get committed by
accident and would need a .gitignore entry. The honest cost of putting them in
/tmp: an aggressive temp sweeper could unlink one mid-flight, and a later opener
would then create a fresh inode and take a DIFFERENT lock. That window is small
and the hash check behind it still holds, so the failure mode degrades to the
unlocked TOCTOU race rather than to a lost write.


THE FILE IS THE TRUTH, THE TABLE IS THE RECORD
-----------------------------------------------
Correctness comes from the SHA-256 comparison, not from `file_coordination`.
The table is an audit trail and the backing store for `who_is_editing()`.

So a database failure does NOT abort a write. It is logged at WARNING and
counted in `LOG_FAILURES`; the file still lands. The cost, stated rather than
buried: when logging fails, `who_is_editing()` goes blind and under-reports.
That is degraded, not wrong - and a module that refused to save your work
because an audit row would not insert would be worse.

For the same reason the write happens BEFORE its log row is inserted. A crash
between the two leaves a written file with no row (a missing record of a real
event) rather than a row claiming a write that never happened (a record of a
fictional event). Under-claiming beats over-claiming.


CONCURRENCY WITH THE OTHER WRITERS OF db/trading.db
----------------------------------------------------
The Polymarket shadow loop holds this database open continuously. Same rules as
`engine/feeds/liquidation_recorder.py` and `engine/feeds/hyperliquid_client.py`:

  * `PRAGMA busy_timeout` so a collision WAITS instead of raising.
  * journal_mode is READ and logged, never SET. Switching it on a database
    another process holds open is a global exclusive operation.
  * Short transactions. One row per call, connection opened and closed per call,
    because an agent session is not a long-lived writer and an idle open handle
    on a WAL database keeps the WAL from checkpointing.
  * This module writes to `file_coordination` and to no other table, and never
    issues DELETE or UPDATE. Append-only.
"""

from __future__ import annotations

import difflib
import errno
import hashlib
import logging
import os
import sqlite3
import subprocess
import tempfile
import time

from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    'CheckoutContext',
    'ConcurrentModificationError',
    'checkout',
    'checkin',
    'safe_write',
    'safe_edit',
    'who_is_editing',
    'ensure_schema',
    'hash_bytes',
    'hash_file',
    'repo_root',
    'DEFAULT_AGENT_ID',
]

# ---------------------------------------------------------------------------
# Policy constants. Module level, never inline literals, so the whole policy is
# visible in one place.
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = 'db/trading.db'

#: Overridable so Hermes, a cron job or a spawned session identifies itself.
#: Falls back to the login name, then to 'unknown' - never to a lie.
DEFAULT_AGENT_ID = (
    os.environ.get('AGENT_ID')
    or os.environ.get('TRADING_BOT_AGENT_ID')
    or 'cody'
)

SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CONNECT_TIMEOUT_SEC = 15.0

#: How far back `who_is_editing()` looks. A checkout older than this is assumed
#: to belong to a session that died without checking in. It is not "active"; a
#: dead agent is not editing anything.
DEFAULT_ACTIVE_WINDOW_SEC = 3600

#: `safe_edit` attempts. 3 is the spec. Each retry re-reads from disk, so a
#: retry applies your edit to THEIR content - which is the point.
DEFAULT_MAX_RETRIES = 3

#: How long to wait for the advisory lock before writing anyway. Writing anyway
#: is deliberate: the hash check still runs, so the worst case is the unlocked
#: TOCTOU window, whereas failing outright would let one wedged process block
#: every other agent's writes.
LOCK_TIMEOUT_SEC = 10.0
LOCK_POLL_SEC = 0.05
LOCK_DIR_NAME = 'aym-trading-bot-locks'

#: Unified-diff context lines.
DIFF_CONTEXT_LINES = 3
#: A diff stored in the coordination table is truncated to this many characters.
#: A 40MB conflict diff in an audit row helps nobody and bloats a database that
#: a live trading loop is writing to.
MAX_STORED_DIFF_CHARS = 20000

ACTION_CHECKOUT = 'checkout'
ACTION_CHECKIN = 'checkin'
ACTION_CONFLICT = 'conflict'
ACTION_WRITE = 'write'
ACTION_RELEASE = 'release'

#: A checkout is settled by any of these. A conflict settles it because the
#: contract after a conflict is "take a FRESH checkout and retry" - the old
#: context is dead, and `safe_edit` does exactly that.
TERMINAL_ACTIONS = (ACTION_CHECKIN, ACTION_CONFLICT, ACTION_RELEASE)

VALID_ACTIONS = (ACTION_CHECKOUT, ACTION_CHECKIN, ACTION_CONFLICT,
                 ACTION_WRITE, ACTION_RELEASE)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS file_coordination (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_hash TEXT,
    new_hash TEXT,
    conflict_diff TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_coordination_ts
    ON file_coordination(ts);
CREATE INDEX IF NOT EXISTS idx_file_coordination_path_ts
    ON file_coordination(file_path, ts);
"""

#: Counted, not raised. `who_is_editing()` under-reports when these are nonzero,
#: so a caller that cares can check rather than guess.
LOG_FAILURES: Counter = Counter()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConcurrentModificationError(Exception):
    """The file changed between checkout and checkin.

    Carries everything needed to reconcile without a second read:
      `file_path`     repo-relative path, as logged
      `expected_hash` what the file hashed to at checkout (None: did not exist)
      `actual_hash`   what it hashes to now (None: it has been deleted)
      `diff`          unified diff, checkout content -> current content, i.e.
                      what THEY did. Not what you did.
      `current_content` the on-disk content now, so a caller can reconcile
                      without racing a re-read.
    """

    def __init__(self, file_path: str, expected_hash: Optional[str],
                 actual_hash: Optional[str], diff: str,
                 current_content: Optional[str] = None,
                 message: Optional[str] = None):
        self.file_path = file_path
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.diff = diff
        self.current_content = current_content
        self.message = message or self._default_message()
        super().__init__(self.message)

    def _default_message(self) -> str:
        if self.actual_hash is None:
            what = 'it has been DELETED since checkout'
        elif self.expected_hash is None:
            what = 'it has been CREATED since checkout'
        else:
            what = 'expected %s, found %s' % (
                _short(self.expected_hash), _short(self.actual_hash))
        return ('%s was modified by another writer: %s. Your edit was NOT '
                'written. Read the current version, reconcile, retry.'
                % (self.file_path, what))


def _short(digest: Optional[str]) -> str:
    return digest[:12] if digest else 'none'


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT: Optional[str] = None


def repo_root() -> str:
    """Absolute path of the repository root, resolved once and cached.

    Tries `git rev-parse` first so a worktree or a submodule resolves the way
    git itself sees it, then falls back to walking up for a `.git` entry, then
    to this file's parent's parent. The fallback chain exists because this
    module must import cleanly in a spawned agent whose cwd is anywhere.
    """
    global _REPO_ROOT
    if _REPO_ROOT is not None:
        return _REPO_ROOT

    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--show-toplevel'], cwd=here,
            stderr=subprocess.DEVNULL)
        root = out.decode('utf-8').strip()
        if root and os.path.isdir(root):
            _REPO_ROOT = os.path.realpath(root)
            return _REPO_ROOT
    except (OSError, subprocess.CalledProcessError):
        pass

    probe = here
    while True:
        if os.path.exists(os.path.join(probe, '.git')):
            _REPO_ROOT = os.path.realpath(probe)
            return _REPO_ROOT
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent

    _REPO_ROOT = os.path.realpath(os.path.dirname(here))
    return _REPO_ROOT


def _abs_path(file_path: str) -> str:
    """Absolute path for filesystem work. Relative inputs resolve against cwd.

    `os.path.abspath`, not `realpath`: resolving symlinks here would make us
    write THROUGH a symlink to its target while logging the target's path, and
    the operator asked about the name they typed.
    """
    return os.path.abspath(file_path)


def rel_path(file_path: str) -> str:
    """The form stored in `file_coordination`: repo-relative, POSIX separators.

    Two agents must agree on the key or `who_is_editing()` silently pairs
    nothing with nothing. `strategies/x.py`, `./strategies/x.py` and an absolute
    path all normalise to the same string. A path OUTSIDE the repo keeps its
    absolute form rather than growing a `../../..` prefix that means nothing to
    a reader.
    """
    absolute = _abs_path(file_path)
    root = repo_root()
    try:
        common = os.path.commonpath([os.path.realpath(absolute), root])
    except ValueError:
        return absolute.replace(os.sep, '/')
    if common != root:
        return absolute.replace(os.sep, '/')
    rel = os.path.relpath(os.path.realpath(absolute), root)
    return rel.replace(os.sep, '/')


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_bytes(payload: bytes) -> str:
    """SHA-256 of raw bytes, hex."""
    return hashlib.sha256(payload).hexdigest()


def hash_text(content: str, encoding: str = 'utf-8') -> str:
    """SHA-256 of text, hashed as its encoded bytes.

    Text is hashed via its bytes so that `hash_text(ctx.content)` and
    `hash_file(path)` agree. Hashing the str object's internal form instead
    would make the two incomparable, which is the one thing this module must
    never get wrong.
    """
    return hash_bytes(content.encode(encoding))


def hash_file(file_path: str) -> Optional[str]:
    """SHA-256 of a file's bytes, or None if it does not exist.

    None means "no file", which is NOT the same as the hash of an empty file
    (`e3b0c442...`). Convention 11: absent and empty are different facts and
    must not collapse into one value.
    """
    try:
        with open(_abs_path(file_path), 'rb') as fh:
            return hash_bytes(fh.read())
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise
    except OSError:
        raise


def _read(file_path: str, encoding: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (content, digest). Both None when the file does not exist.

    Reads BINARY and decodes explicitly. `open(path, 'r')` applies universal
    newline translation, which turns CRLF into LF in memory; the content would
    then re-encode to different bytes than the ones we hashed, and every checkin
    on a CRLF file would look like somebody else's edit.
    """
    try:
        with open(_abs_path(file_path), 'rb') as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None, None
    return raw.decode(encoding), hash_bytes(raw)


# ---------------------------------------------------------------------------
# The checkout context
# ---------------------------------------------------------------------------

class CheckoutContext(object):
    """A snapshot of one file at one instant, plus who took it and when.

    Deliberately not a NamedTuple: `hash` is None for a file that does not exist
    yet, and the invariants below are worth asserting in one place.
    """

    __slots__ = ('path', 'abs_path', 'hash', 'content', 'agent_id', 'timestamp',
                 'encoding', 'db_path', 'existed')

    def __init__(self, path: str, abs_path: str, digest: Optional[str],
                 content: Optional[str], agent_id: str, timestamp: int,
                 encoding: str, db_path: str):
        self.path = path                # repo-relative, the logged key
        self.abs_path = abs_path
        self.hash = digest              # None == did not exist at checkout
        self.content = content          # None == did not exist at checkout
        self.agent_id = agent_id
        self.timestamp = timestamp      # epoch seconds, int
        self.encoding = encoding
        self.db_path = db_path
        self.existed = digest is not None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.timestamp)

    def __repr__(self) -> str:
        return ('CheckoutContext(%s hash=%s agent=%s age=%.0fs existed=%s)'
                % (self.path, _short(self.hash), self.agent_id,
                   self.age_seconds, self.existed))


# ---------------------------------------------------------------------------
# The coordination table
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    """Open the shared database under the rules in the module docstring."""
    parent = os.path.dirname(_abs_path(db_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=SQLITE_CONNECT_TIMEOUT_SEC)
    conn.execute('PRAGMA busy_timeout=%d;' % SQLITE_BUSY_TIMEOUT_MS)
    return conn


#: Databases whose schema this process has already ensured. Creating the table
#: is idempotent but not free, and `_log` runs on every checkout and checkin -
#: re-running three DDL statements per call would add write-lock contention to
#: a database a live trading loop is using.
_SCHEMA_READY: Set[str] = set()


def ensure_schema(db_path: str = DEFAULT_DB_PATH, force: bool = False) -> None:
    """Create `file_coordination` and its indices if absent.

    All `IF NOT EXISTS`, so it is safe to re-run and safe against a database
    another process holds open. journal_mode is READ and logged, never SET -
    see the module docstring.
    """
    key = _abs_path(db_path)
    if key in _SCHEMA_READY and not force:
        return
    conn = _connect(db_path)
    try:
        row = conn.execute('PRAGMA journal_mode;').fetchone()
        mode = (row[0] if row else '') or ''
        if mode.lower() != 'wal':
            logger.debug('file_coordination: journal_mode=%r (observed, not '
                         'set) on %s', mode, db_path)
        with conn:
            conn.executescript(SCHEMA_SQL)
        _SCHEMA_READY.add(key)
    finally:
        conn.close()


def _log(db_path: str, file_path: str, agent_id: str, action: str,
         old_hash: Optional[str] = None, new_hash: Optional[str] = None,
         conflict_diff: Optional[str] = None, ts: Optional[int] = None) -> bool:
    """Append one audit row. Returns True on success.

    NEVER raises. A failure here is counted in `LOG_FAILURES` and logged at
    WARNING, because the audit trail must not be able to veto a correct write
    (see the module docstring). The counter is what lets a caller notice that
    `who_is_editing()` has gone blind instead of trusting a short list.
    """
    assert action in VALID_ACTIONS, 'unknown action %r' % (action,)
    if conflict_diff is not None and len(conflict_diff) > MAX_STORED_DIFF_CHARS:
        conflict_diff = (conflict_diff[:MAX_STORED_DIFF_CHARS]
                         + '\n... [truncated at %d chars]'
                         % MAX_STORED_DIFF_CHARS)
    try:
        ensure_schema(db_path)
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as exc:
        LOG_FAILURES['connect'] += 1
        logger.warning('file_coordination: could not open %s (%s); the write '
                       'itself is unaffected but who_is_editing() will '
                       'under-report', db_path, exc)
        return False
    try:
        with conn:
            conn.execute(
                'INSERT INTO file_coordination '
                '(ts, file_path, agent_id, action, old_hash, new_hash, '
                ' conflict_diff) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (int(ts if ts is not None else time.time()), file_path,
                 agent_id, action, old_hash, new_hash, conflict_diff))
        return True
    except sqlite3.Error as exc:
        LOG_FAILURES[action] += 1
        logger.warning('file_coordination: could not log %s for %s (%s); the '
                       'write itself is unaffected', action, file_path, exc)
        return False
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Advisory locking around the verify-then-write critical section
# ---------------------------------------------------------------------------

def _lock_path(abs_path: str) -> str:
    directory = os.path.join(tempfile.gettempdir(), LOCK_DIR_NAME)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = tempfile.gettempdir()
    key = hashlib.sha256(abs_path.encode('utf-8')).hexdigest()[:16]
    return os.path.join(directory, '%s.lock' % key)


class _FileLock(object):
    """Advisory `flock` held ONLY across verify-then-write.

    A timeout does not raise. It logs and proceeds unlocked, because the hash
    check still runs behind it: the degraded case is the millisecond TOCTOU
    window, whereas raising would let one stuck holder block every other agent.
    `acquired` records which happened, so a test can assert it rather than
    infer it.

    On a platform without `fcntl` this is a no-op and says so once. Everything
    else in the module still works; only the last-millisecond guarantee goes.
    """

    def __init__(self, abs_path: str, timeout: float = LOCK_TIMEOUT_SEC):
        self.path = _lock_path(abs_path)
        self.timeout = timeout
        self.acquired = False
        self._fh = None

    def __enter__(self) -> '_FileLock':
        try:
            import fcntl
        except ImportError:            # pragma: no cover - POSIX only here
            logger.debug('fcntl unavailable; checkin proceeds without the '
                         'advisory lock (hash check still applies)')
            return self
        deadline = time.time() + self.timeout
        try:
            self._fh = open(self.path, 'a+')
        except OSError as exc:
            logger.warning('could not open lock file %s (%s); proceeding '
                           'unlocked', self.path, exc)
            return self
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    logger.warning('flock failed on %s (%s); proceeding '
                                   'unlocked', self.path, exc)
                    return self
                if time.time() >= deadline:
                    logger.warning(
                        'lock on %s still held after %.0fs; proceeding '
                        'WITHOUT it. The hash check still runs, so the risk '
                        'is the narrow TOCTOU window, not a blind overwrite.',
                        self.path, self.timeout)
                    return self
                time.sleep(LOCK_POLL_SEC)

    def __exit__(self, *exc_info) -> None:
        if self._fh is None:
            return
        try:
            if self.acquired:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(abs_path: str, content: str, encoding: str) -> None:
    """Write via a temp file in the SAME directory, then `os.replace`.

    Same directory because `os.replace` is only atomic within one filesystem;
    a temp file in /tmp would fall back to a copy and reintroduce the partial
    write this exists to prevent.

    `fsync` before the rename so a power loss cannot leave a renamed-but-empty
    file. The permission bits of the ORIGINAL are carried over, because
    `mkstemp` creates 0600 and a silently un-executable `run_*.sh` after an
    edit is a genuinely confusing failure.
    """
    directory = os.path.dirname(abs_path) or '.'
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)

    mode = None
    try:
        mode = os.stat(abs_path).st_mode & 0o7777
    except OSError:
        pass

    fd, tmp = tempfile.mkstemp(dir=directory,
                               prefix='.' + os.path.basename(abs_path) + '.',
                               suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content.encode(encoding))
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, abs_path)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # Durability of the RENAME itself needs the directory synced too. Best
    # effort: some filesystems refuse O_RDONLY fsync on a directory, and that
    # is not a reason to fail a write that already landed.
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def unified_diff(before: Optional[str], after: Optional[str],
                 path: str, before_label: str = 'checkout',
                 after_label: str = 'current') -> str:
    """Unified diff `before` -> `after`. None is rendered as an absent file."""
    before_lines = (before or '').splitlines(keepends=True)
    after_lines = (after or '').splitlines(keepends=True)
    if before is None:
        before_label = 'did-not-exist'
    if after is None:
        after_label = 'deleted'
    return ''.join(difflib.unified_diff(
        before_lines, after_lines,
        fromfile='%s@%s' % (path, before_label),
        tofile='%s@%s' % (path, after_label),
        n=DIFF_CONTEXT_LINES))


# ---------------------------------------------------------------------------
# The public API
# ---------------------------------------------------------------------------

def checkout(file_path: str, agent_id: str = DEFAULT_AGENT_ID,
             db_path: str = DEFAULT_DB_PATH, encoding: str = 'utf-8',
             allow_missing: bool = False) -> CheckoutContext:
    """Snapshot a file's content and hash before you start working on it.

    Nothing is locked. Other agents keep working; `checkin` is where the
    collision surfaces.

    A missing file raises `FileNotFoundError` unless `allow_missing=True`, in
    which case the context has `hash is None` and `existed is False` - which
    `checkin` then enforces as "this file must still not exist", so two agents
    racing to CREATE the same file also collide instead of one silently winning.
    """
    absolute = _abs_path(file_path)
    key = rel_path(file_path)
    content, digest = _read(absolute, encoding)
    if digest is None and not allow_missing:
        raise FileNotFoundError(
            errno.ENOENT,
            'cannot checkout a file that does not exist (pass '
            'allow_missing=True to reserve a new path)', absolute)

    ctx = CheckoutContext(
        path=key, abs_path=absolute, digest=digest, content=content,
        agent_id=agent_id, timestamp=int(time.time()), encoding=encoding,
        db_path=db_path)
    _log(db_path, key, agent_id, ACTION_CHECKOUT, old_hash=digest,
         ts=ctx.timestamp)
    logger.debug('checkout %s', ctx)
    return ctx


def checkin(context: CheckoutContext, new_content: str) -> None:
    """Verify nothing changed since checkout, then write atomically.

    Raises `ConcurrentModificationError` if the file's current hash differs
    from the one recorded at checkout - including the two asymmetric cases:
    the file was DELETED (actual None), or it was CREATED after an
    `allow_missing` checkout (expected None).

    The re-read, the comparison and the rename all happen inside one advisory
    lock, so two agents using this module cannot interleave between them.
    """
    if not isinstance(new_content, str):
        raise TypeError('new_content must be str, got %s'
                        % type(new_content).__name__)

    with _FileLock(context.abs_path):
        current_content, current_hash = _read(context.abs_path,
                                              context.encoding)

        if current_hash != context.hash:
            diff = unified_diff(context.content, current_content, context.path)
            _log(context.db_path, context.path, context.agent_id,
                 ACTION_CONFLICT, old_hash=context.hash,
                 new_hash=current_hash, conflict_diff=diff)
            logger.warning('CONFLICT on %s: expected %s, found %s',
                           context.path, _short(context.hash),
                           _short(current_hash))
            raise ConcurrentModificationError(
                file_path=context.path, expected_hash=context.hash,
                actual_hash=current_hash, diff=diff,
                current_content=current_content)

        _atomic_write(context.abs_path, new_content, context.encoding)
        new_hash = hash_text(new_content, context.encoding)

    # Outside the lock on purpose: the file has landed, the lock's only job is
    # done, and a slow database must not extend the window other agents wait on.
    _log(context.db_path, context.path, context.agent_id, ACTION_CHECKIN,
         old_hash=context.hash, new_hash=new_hash)
    logger.debug('checkin %s %s -> %s', context.path, _short(context.hash),
                 _short(new_hash))


def release(context: CheckoutContext) -> None:
    """Abandon a checkout without writing.

    Without this, deciding not to edit leaves a checkout that `who_is_editing()`
    reports as active for an hour. Call it on the path where you bail out.
    """
    _log(context.db_path, context.path, context.agent_id, ACTION_RELEASE,
         old_hash=context.hash)


def safe_write(file_path: str, content: str,
               agent_id: str = DEFAULT_AGENT_ID,
               db_path: str = DEFAULT_DB_PATH, encoding: str = 'utf-8',
               must_be_new: bool = False) -> None:
    """checkout + checkin in one call, for content you already have.

    Use it when you did not need to read the old file to produce the new one.

    `must_be_new=True` is the NEW-FILE mode from the spec: it refuses if the
    path already exists, and the existence check happens INSIDE the lock, so two
    agents creating the same file at once produce one success and one
    `ConcurrentModificationError` rather than one silent overwrite.

    With `must_be_new=False` on an existing file, the read and the write are
    both inside the lock, so this is atomic against other users of this module.
    It is NOT protection against an edit somebody made BEFORE you called it -
    there is no window in which you were holding a stale view, because you never
    held one. If your new content depends on the old content, use `safe_edit` or
    an explicit checkout/checkin pair instead; that is what detects a stale view.
    """
    absolute = _abs_path(file_path)
    key = rel_path(file_path)

    with _FileLock(absolute):
        current_content, current_hash = _read(absolute, encoding)

        if must_be_new and current_hash is not None:
            diff = unified_diff(None, current_content, key)
            _log(db_path, key, agent_id, ACTION_CONFLICT, old_hash=None,
                 new_hash=current_hash, conflict_diff=diff)
            raise ConcurrentModificationError(
                file_path=key, expected_hash=None, actual_hash=current_hash,
                diff=diff, current_content=current_content,
                message=('%s already exists (hash %s) but safe_write was '
                         'called with must_be_new=True. Another agent created '
                         'it first. Read it, reconcile, retry.'
                         % (key, _short(current_hash))))

        _atomic_write(absolute, content, encoding)
        new_hash = hash_text(content, encoding)

    _log(db_path, key, agent_id, ACTION_WRITE, old_hash=current_hash,
         new_hash=new_hash)
    logger.debug('safe_write %s %s -> %s', key, _short(current_hash),
                 _short(new_hash))


def safe_edit(file_path: str, edit_fn: Callable[[str], str],
              agent_id: str = DEFAULT_AGENT_ID,
              db_path: str = DEFAULT_DB_PATH, encoding: str = 'utf-8',
              max_retries: int = DEFAULT_MAX_RETRIES) -> str:
    """checkout, apply `edit_fn(old) -> new`, checkin. Retry on conflict.

    Returns the content actually written.

    On conflict it takes a FRESH checkout and calls `edit_fn` again on the
    OTHER agent's content, which is what makes the retry a reconciliation
    rather than a re-clobber. That puts a real requirement on `edit_fn`: it
    must be a function of the content it is handed, and it must tolerate being
    run against content that already carries somebody else's change. A
    `content + '\\n# appended'` is fine. A `content.replace(a, b)` that has
    already been applied is fine (it becomes a no-op). Anything that assumes it
    is looking at the original is not, and will silently apply twice.

    After `max_retries` exhausted attempts the last
    `ConcurrentModificationError` is raised. It is not swallowed and the file is
    not forced: a file that is being rewritten faster than we can read it is a
    situation for a human, not for a louder retry loop.
    """
    if max_retries < 1:
        raise ValueError('max_retries must be >= 1, got %d' % max_retries)

    last_exc: Optional[ConcurrentModificationError] = None
    for attempt in range(1, max_retries + 1):
        ctx = checkout(file_path, agent_id=agent_id, db_path=db_path,
                       encoding=encoding)
        new_content = edit_fn(ctx.content or '')
        if not isinstance(new_content, str):
            release(ctx)
            raise TypeError('edit_fn must return str, got %s'
                            % type(new_content).__name__)
        try:
            checkin(ctx, new_content)
            if attempt > 1:
                logger.info('safe_edit %s succeeded on attempt %d/%d',
                            ctx.path, attempt, max_retries)
            return new_content
        except ConcurrentModificationError as exc:
            last_exc = exc
            logger.warning('safe_edit %s conflict on attempt %d/%d; re-reading '
                           'and reapplying', ctx.path, attempt, max_retries)

    assert last_exc is not None
    last_exc.message = (
        '%s (gave up after %d attempts)' % (last_exc.message, max_retries))
    raise last_exc


def who_is_editing(db_path: str = DEFAULT_DB_PATH,
                   max_age_sec: float = DEFAULT_ACTIVE_WINDOW_SEC,
                   now: Optional[float] = None) -> List[Dict]:
    """Checkouts inside the window that have no matching settling action.

    Returns dicts of `file_path`, `agent_id`, `timestamp`, `age_seconds`,
    `old_hash`, `current_hash`, `changed_since_checkout`, newest first.

    Pairing rule, stated because a plain "latest action wins" would be wrong:
    for each (file_path, agent_id) the open count is
    `n_checkout - n_settled` over the window, and the most recent `n_open`
    checkouts are reported. An agent that checks a file out twice and checks in
    once still has one open. `n_settled` may exceed `n_checkout` when a checkout
    happened before the window, so the count is floored at zero rather than
    going negative.

    `max_age_sec` is a real assumption with an expiry date (convention 17): a
    checkout older than the window is treated as a dead session, not as a
    long-running edit. Raise it if agents legitimately hold work longer.

    A database that cannot be read returns [] and logs at WARNING. That is
    "could not run", not "nobody is editing" (convention 11) - which is exactly
    why the pre-commit hook treats an empty list as advisory and never as proof.
    """
    now_ts = time.time() if now is None else now
    since = int(now_ts - max_age_sec)

    try:
        ensure_schema(db_path)
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as exc:
        LOG_FAILURES['read'] += 1
        logger.warning('who_is_editing: cannot open %s (%s); returning [] - '
                       'this means COULD NOT RUN, not "nobody is editing"',
                       db_path, exc)
        return []
    try:
        rows = conn.execute(
            'SELECT ts, file_path, agent_id, action, old_hash '
            'FROM file_coordination WHERE ts >= ? ORDER BY ts ASC, id ASC',
            (since,)).fetchall()
    except sqlite3.Error as exc:
        LOG_FAILURES['read'] += 1
        logger.warning('who_is_editing: query failed on %s (%s); returning [] '
                       '- COULD NOT RUN, not "nobody is editing"',
                       db_path, exc)
        return []
    finally:
        conn.close()

    checkouts: Dict[tuple, List[tuple]] = {}
    settled: Counter = Counter()
    for ts, file_path, agent_id, action, old_hash in rows:
        key = (file_path, agent_id)
        if action == ACTION_CHECKOUT:
            checkouts.setdefault(key, []).append((ts, old_hash))
        elif action in TERMINAL_ACTIONS:
            settled[key] += 1

    active: List[Dict] = []
    hash_cache: Dict[str, Optional[str]] = {}
    for key, taken in checkouts.items():
        file_path, agent_id = key
        n_open = max(0, len(taken) - settled[key])
        if not n_open:
            continue
        if file_path not in hash_cache:
            try:
                hash_cache[file_path] = hash_file(
                    os.path.join(repo_root(), file_path)
                    if not os.path.isabs(file_path) else file_path)
            except OSError:
                hash_cache[file_path] = None
        current = hash_cache[file_path]
        for ts, old_hash in taken[-n_open:]:
            active.append({
                'file_path': file_path,
                'agent_id': agent_id,
                'timestamp': int(ts),
                'age_seconds': max(0.0, now_ts - ts),
                'old_hash': old_hash,
                'current_hash': current,
                'changed_since_checkout': current != old_hash,
            })

    active.sort(key=lambda r: r['timestamp'], reverse=True)
    return active


def format_active(active: Sequence[Dict]) -> str:
    """One line per active checkout, for a hook or a status command."""
    if not active:
        return 'no active checkouts'
    out = []
    for row in active:
        out.append('  %-52s %-10s %5.0fs ago%s'
                   % (row['file_path'], row['agent_id'], row['age_seconds'],
                      '  [CHANGED SINCE CHECKOUT]'
                      if row['changed_since_checkout'] else ''))
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# CLI: `env -u PYTHONPATH python3 -m engine.concurrency who`
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description='Optimistic concurrency control for the shared tree.')
    p.add_argument('command', choices=['who', 'hash', 'init'],
                   help="'who' lists active checkouts, 'hash' prints a file's "
                        "SHA-256, 'init' creates the coordination table")
    p.add_argument('path', nargs='?', help='file, for the hash command')
    p.add_argument('--db', default=DEFAULT_DB_PATH)
    p.add_argument('--max-age', type=float, default=DEFAULT_ACTIVE_WINDOW_SEC)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')

    if args.command == 'init':
        ensure_schema(args.db)
        print('file_coordination ready in %s' % args.db)
        return 0

    if args.command == 'hash':
        if not args.path:
            p.error('hash needs a path')
        digest = hash_file(args.path)
        if digest is None:
            print('%s: DOES NOT EXIST' % args.path)
            return 1
        print('%s  %s' % (digest, rel_path(args.path)))
        return 0

    active = who_is_editing(db_path=args.db, max_age_sec=args.max_age)
    print('active checkouts in the last %.0fs: %d' % (args.max_age, len(active)))
    print(format_active(active))
    if LOG_FAILURES:
        print('WARNING: coordination log had failures %s - this list may '
              'UNDER-report' % dict(LOG_FAILURES))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
