"""Tests for engine/concurrency.py - optimistic concurrency control.

Convention 22 says a claim in a docstring is not a wiring test. The module
claims a lot, so these are the claims that are actually PINNED here:

  * A checkin after somebody else's write RAISES and LEAVES THEIR CONTENT ON
    DISK. Not "raises" alone - the whole point is that the loser's edit does
    not land, so `test_a_conflict_does_not_clobber_the_other_agents_write` is
    the load-bearing test in this file.
  * Absent and empty are different: `hash_file` returns None for a missing file
    and a real digest for an empty one (convention 11).
  * The write is atomic: a temp file plus rename, so no reader ever sees a
    half-written file, and the original's permission bits survive.
  * CRLF survives a round trip. Reading with universal newlines would rewrite
    the bytes and make every checkin on a CRLF file look like a foreign edit.
  * `safe_edit` reapplies the edit to the OTHER agent's content, and gives up
    after max_retries rather than forcing the write.
  * `who_is_editing` pairs checkouts to settling actions by COUNT, so a double
    checkout with one checkin still shows one open.
  * A database failure degrades `who_is_editing` but never blocks a write.

Every test uses `tmp_path` for both the file and the db. Nothing here touches
db/trading.db - a live shadow loop is writing to it.
"""

import os
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.concurrency import (  # noqa: E402
    ACTION_CHECKIN,
    ACTION_CHECKOUT,
    ACTION_CONFLICT,
    ACTION_WRITE,
    CheckoutContext,
    ConcurrentModificationError,
    checkin,
    checkout,
    ensure_schema,
    format_active,
    hash_bytes,
    hash_file,
    hash_text,
    release,
    rel_path,
    repo_root,
    safe_edit,
    safe_write,
    unified_diff,
    who_is_editing,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """A scratch coordination database. Never db/trading.db."""
    path = str(tmp_path / 'coord.db')
    ensure_schema(path)
    return path


@pytest.fixture
def target(tmp_path):
    """A scratch file with three lines of known content."""
    path = tmp_path / 'subject.py'
    path.write_text('alpha\nbravo\ncharlie\n')
    return str(path)


def rows(db_path, action=None):
    conn = sqlite3.connect(db_path)
    try:
        if action is None:
            cur = conn.execute(
                'SELECT action, file_path, agent_id, old_hash, new_hash, '
                'conflict_diff FROM file_coordination ORDER BY id')
        else:
            cur = conn.execute(
                'SELECT action, file_path, agent_id, old_hash, new_hash, '
                'conflict_diff FROM file_coordination WHERE action = ? '
                'ORDER BY id', (action,))
        return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hashing: absent is not empty
# ---------------------------------------------------------------------------

def test_hash_file_returns_none_for_a_missing_file(tmp_path):
    assert hash_file(str(tmp_path / 'nope.txt')) is None


def test_a_missing_file_and_an_empty_file_do_not_share_a_hash(tmp_path):
    """Convention 11: 'no file' and 'a file with nothing in it' are two facts.

    Collapsing them would make `checkin` accept a checkout of a deleted file as
    if nothing had happened.
    """
    empty = tmp_path / 'empty.txt'
    empty.write_text('')
    assert hash_file(str(empty)) == hash_bytes(b'')
    assert hash_file(str(tmp_path / 'absent.txt')) is None
    assert hash_file(str(empty)) is not None


def test_hash_text_and_hash_file_agree(target):
    """They must be comparable, because checkin compares exactly these two."""
    assert hash_text(open(target).read()) == hash_file(target)


def test_hash_is_sha256_hex(target):
    digest = hash_file(target)
    assert len(digest) == 64
    assert all(c in '0123456789abcdef' for c in digest)


# ---------------------------------------------------------------------------
# Path normalisation - two agents must agree on the key
# ---------------------------------------------------------------------------

def test_rel_path_normalises_equivalent_spellings_of_the_same_repo_file():
    """`x`, `./x` and `/abs/x` must all produce ONE key.

    If they did not, two agents editing the same file would file their
    checkouts under different keys and who_is_editing() would pair nothing with
    nothing while reporting no conflict.
    """
    root = repo_root()
    plain = rel_path('engine/concurrency.py')
    dotted = rel_path('./engine/concurrency.py')
    absolute = rel_path(os.path.join(root, 'engine', 'concurrency.py'))
    assert plain == dotted == absolute == 'engine/concurrency.py'


def test_rel_path_keeps_an_outside_path_absolute(tmp_path):
    """A path outside the repo must not grow a meaningless ../../.. prefix."""
    outside = str(tmp_path / 'elsewhere.txt')
    assert rel_path(outside).startswith('/')
    assert '..' not in rel_path(outside)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_checkout_captures_content_and_hash(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    assert ctx.content == 'alpha\nbravo\ncharlie\n'
    assert ctx.hash == hash_file(target)
    assert ctx.existed is True
    assert ctx.agent_id == 'cody'
    assert isinstance(ctx.timestamp, int)


def test_checkin_writes_when_nothing_changed(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    checkin(ctx, 'alpha\nBRAVO\ncharlie\n')
    assert open(target).read() == 'alpha\nBRAVO\ncharlie\n'


def test_checkout_and_checkin_are_both_logged(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    before = ctx.hash
    checkin(ctx, 'new\n')

    logged = rows(db)
    actions = [r[0] for r in logged]
    assert actions == [ACTION_CHECKOUT, ACTION_CHECKIN]

    checkin_row = logged[1]
    assert checkin_row[1] == rel_path(target)
    assert checkin_row[2] == 'cody'
    assert checkin_row[3] == before
    assert checkin_row[4] == hash_text('new\n')
    assert checkin_row[4] == hash_file(target)


def test_checkout_of_a_missing_file_raises_unless_allowed(tmp_path, db):
    missing = str(tmp_path / 'not-yet.py')
    with pytest.raises(FileNotFoundError):
        checkout(missing, db_path=db)

    ctx = checkout(missing, db_path=db, allow_missing=True)
    assert ctx.hash is None
    assert ctx.existed is False


# ---------------------------------------------------------------------------
# THE LOAD-BEARING TESTS: a conflict is detected and the loser does not write
# ---------------------------------------------------------------------------

def test_checkin_raises_when_another_writer_got_there_first(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    with open(target, 'w') as fh:                    # Raven, outside the module
        fh.write('alpha\nbravo\ncharlie\nfrom-raven\n')

    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'totally-different\n')

    exc = excinfo.value
    assert exc.expected_hash == ctx.hash
    assert exc.actual_hash == hash_file(target)
    assert exc.expected_hash != exc.actual_hash
    assert exc.file_path == rel_path(target)


def test_a_conflict_does_not_clobber_the_other_agents_write(target, db):
    """The whole reason this module exists.

    Detecting the conflict is worth nothing if the write happened anyway.
    """
    ctx = checkout(target, agent_id='cody', db_path=db)
    theirs = 'alpha\nbravo\ncharlie\nfrom-raven\n'
    with open(target, 'w') as fh:
        fh.write(theirs)

    with pytest.raises(ConcurrentModificationError):
        checkin(ctx, 'MINE-WOULD-HAVE-DESTROYED-THEIRS\n')

    assert open(target).read() == theirs


def test_the_conflict_diff_shows_what_the_other_agent_did(target, db):
    """The diff is checkout -> current, i.e. THEIR change, not ours.

    A diff of our own unwritten edit would tell the reader nothing about who
    they collided with.
    """
    ctx = checkout(target, agent_id='cody', db_path=db)
    with open(target, 'w') as fh:
        fh.write('alpha\nbravo\ncharlie\nfrom-raven\n')

    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'irrelevant\n')

    diff = excinfo.value.diff
    assert '+from-raven' in diff
    assert 'irrelevant' not in diff


def test_a_conflict_hands_back_the_current_content_for_reconciliation(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    theirs = 'alpha\nbravo\ncharlie\nfrom-raven\n'
    with open(target, 'w') as fh:
        fh.write(theirs)

    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'x\n')

    assert excinfo.value.current_content == theirs


def test_deletion_after_checkout_is_a_conflict_not_a_recreate(target, db):
    """A deleted file must not be silently recreated by a stale checkin.

    Somebody deleted it on purpose. Re-writing it from a stale snapshot would
    undo that decision without anybody noticing.
    """
    ctx = checkout(target, agent_id='cody', db_path=db)
    os.unlink(target)

    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'resurrected\n')

    assert excinfo.value.actual_hash is None
    assert 'DELETED' in excinfo.value.message
    assert not os.path.exists(target)


def test_two_agents_racing_to_create_the_same_new_file_collide(tmp_path, db):
    """An allow_missing checkout asserts the file must STILL not exist."""
    fresh = str(tmp_path / 'brand-new.py')
    ctx = checkout(fresh, agent_id='cody', db_path=db, allow_missing=True)

    with open(fresh, 'w') as fh:                     # Raven creates it first
        fh.write('raven-got-here-first\n')

    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'cody-content\n')

    assert excinfo.value.expected_hash is None
    assert 'CREATED' in excinfo.value.message
    assert open(fresh).read() == 'raven-got-here-first\n'


def test_a_conflict_is_logged_with_its_diff(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    with open(target, 'w') as fh:
        fh.write('changed-underneath\n')
    with pytest.raises(ConcurrentModificationError):
        checkin(ctx, 'mine\n')

    conflicts = rows(db, ACTION_CONFLICT)
    assert len(conflicts) == 1
    assert conflicts[0][2] == 'cody'
    assert '+changed-underneath' in conflicts[0][5]


def test_no_checkin_row_is_written_for_a_refused_write(target, db):
    """The audit trail must not record a write that did not happen."""
    ctx = checkout(target, agent_id='cody', db_path=db)
    with open(target, 'w') as fh:
        fh.write('x\n')
    with pytest.raises(ConcurrentModificationError):
        checkin(ctx, 'y\n')

    assert rows(db, ACTION_CHECKIN) == []


# ---------------------------------------------------------------------------
# Write mechanics: atomicity, permissions, encodings
# ---------------------------------------------------------------------------

def test_no_temp_files_are_left_behind(tmp_path, db):
    target = str(tmp_path / 'f.txt')
    with open(target, 'w') as fh:
        fh.write('one\n')
    ctx = checkout(target, db_path=db)
    checkin(ctx, 'two\n')

    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith('.tmp')]
    assert leftovers == []


def test_the_permission_bits_survive_a_checkin(tmp_path, db):
    """mkstemp creates 0600. A run_*.sh that silently loses +x after an edit is
    a genuinely confusing failure, so the original mode is carried over."""
    script = str(tmp_path / 'run_thing.sh')
    with open(script, 'w') as fh:
        fh.write('#!/bin/bash\necho hi\n')
    os.chmod(script, 0o755)

    ctx = checkout(script, db_path=db)
    checkin(ctx, '#!/bin/bash\necho bye\n')

    assert os.stat(script).st_mode & 0o777 == 0o755
    assert os.access(script, os.X_OK)


def test_crlf_survives_a_round_trip(tmp_path, db):
    """Universal-newline reading would turn CRLF into LF in memory, re-encode
    to different bytes, and make every checkin on such a file look like a
    foreign edit. The module reads binary to avoid exactly that."""
    target = str(tmp_path / 'windows.txt')
    with open(target, 'wb') as fh:
        fh.write(b'a\r\nb\r\n')

    ctx = checkout(target, db_path=db)
    assert ctx.content == 'a\r\nb\r\n'
    assert ctx.hash == hash_file(target)

    checkin(ctx, ctx.content)                 # a no-op edit must not conflict
    with open(target, 'rb') as fh:
        assert fh.read() == b'a\r\nb\r\n'


def test_a_no_op_checkin_is_not_a_conflict(target, db):
    ctx = checkout(target, db_path=db)
    checkin(ctx, ctx.content)
    assert open(target).read() == 'alpha\nbravo\ncharlie\n'


def test_unicode_content_round_trips(tmp_path, db):
    target = str(tmp_path / 'u.txt')
    with open(target, 'w', encoding='utf-8') as fh:
        fh.write('naive cafe éè - \U0001f600\n')
    ctx = checkout(target, db_path=db)
    checkin(ctx, ctx.content + 'more ü\n')
    with open(target, encoding='utf-8') as fh:
        assert fh.read().endswith('more ü\n')


def test_checkin_rejects_non_string_content(target, db):
    ctx = checkout(target, db_path=db)
    with pytest.raises(TypeError):
        checkin(ctx, b'bytes-are-not-text')


# ---------------------------------------------------------------------------
# safe_write
# ---------------------------------------------------------------------------

def test_safe_write_creates_a_new_file(tmp_path, db):
    target = str(tmp_path / 'created.py')
    safe_write(target, 'hello\n', agent_id='cody', db_path=db)
    assert open(target).read() == 'hello\n'
    assert [r[0] for r in rows(db)] == [ACTION_WRITE]


def test_safe_write_creates_missing_parent_directories(tmp_path, db):
    target = str(tmp_path / 'deep' / 'deeper' / 'f.txt')
    safe_write(target, 'x\n', db_path=db)
    assert open(target).read() == 'x\n'


def test_safe_write_must_be_new_refuses_an_existing_file(target, db):
    with pytest.raises(ConcurrentModificationError) as excinfo:
        safe_write(target, 'clobber\n', db_path=db, must_be_new=True)
    assert excinfo.value.expected_hash is None
    assert open(target).read() == 'alpha\nbravo\ncharlie\n'


def test_safe_write_must_be_new_allows_a_genuinely_new_path(tmp_path, db):
    target = str(tmp_path / 'genuinely-new.py')
    safe_write(target, 'fresh\n', db_path=db, must_be_new=True)
    assert open(target).read() == 'fresh\n'


def test_safe_write_logs_the_hash_it_replaced(target, db):
    before = hash_file(target)
    safe_write(target, 'replacement\n', db_path=db)
    write_rows = rows(db, ACTION_WRITE)
    assert write_rows[0][3] == before
    assert write_rows[0][4] == hash_text('replacement\n')


# ---------------------------------------------------------------------------
# safe_edit
# ---------------------------------------------------------------------------

def test_safe_edit_applies_the_function(target, db):
    safe_edit(target, lambda s: s.upper(), db_path=db)
    assert open(target).read() == 'ALPHA\nBRAVO\nCHARLIE\n'


def test_safe_edit_returns_what_it_wrote(target, db):
    out = safe_edit(target, lambda s: s + 'delta\n', db_path=db)
    assert out == open(target).read()


def test_safe_edit_reapplies_to_the_other_agents_content_after_a_conflict(
        target, db, monkeypatch):
    """The retry must reconcile, not re-clobber.

    A one-shot writer injected between the checkout and the checkin of the
    FIRST attempt forces exactly one conflict. The assertion that matters is
    that the final file contains BOTH changes - theirs and ours.
    """
    import engine.concurrency as C

    original_read = C._read
    calls = {'n': 0}

    def racing_read(path, encoding):
        # Call 1 is the checkout read; call 2 is the verify read inside the
        # first checkin. Landing Raven's write immediately before call 2 is
        # what makes attempt 1 lose, deterministically and without sleeps.
        calls['n'] += 1
        if calls['n'] == 2:
            with open(path, 'w') as fh:
                fh.write('alpha\nbravo\ncharlie\nfrom-raven\n')
        return original_read(path, encoding)

    monkeypatch.setattr(C, '_read', racing_read)
    safe_edit(target, lambda s: s + 'from-cody\n', db_path=db)

    final = open(target).read()
    assert 'from-raven' in final, 'the retry clobbered the other agent'
    assert 'from-cody' in final, 'our own edit was lost'


def test_safe_edit_gives_up_after_max_retries(target, db, monkeypatch):
    """A file being rewritten faster than we can read it is a human's problem.

    It must raise, not loop louder and not force the write.
    """
    import engine.concurrency as C

    original_read = C._read

    def always_racing(path, encoding):
        result = original_read(path, encoding)
        with open(path, 'a') as fh:            # somebody writes on every read
            fh.write('noise\n')
        return result

    monkeypatch.setattr(C, '_read', always_racing)
    with pytest.raises(ConcurrentModificationError) as excinfo:
        safe_edit(target, lambda s: s + 'mine\n', db_path=db, max_retries=3)
    monkeypatch.setattr(C, '_read', original_read)

    assert 'gave up after 3 attempts' in excinfo.value.message
    assert 'mine' not in open(target).read()


def test_safe_edit_rejects_an_edit_fn_that_does_not_return_a_string(target, db):
    with pytest.raises(TypeError):
        safe_edit(target, lambda s: None, db_path=db)


def test_safe_edit_rejects_a_nonsense_retry_count(target, db):
    with pytest.raises(ValueError):
        safe_edit(target, lambda s: s, db_path=db, max_retries=0)


def test_safe_edit_on_a_missing_file_raises_file_not_found(tmp_path, db):
    with pytest.raises(FileNotFoundError):
        safe_edit(str(tmp_path / 'ghost.py'), lambda s: s, db_path=db)


# ---------------------------------------------------------------------------
# who_is_editing
# ---------------------------------------------------------------------------

def test_who_is_editing_is_empty_when_nothing_is_open(db):
    assert who_is_editing(db_path=db) == []


def test_an_open_checkout_is_reported(target, db):
    checkout(target, agent_id='sessionA', db_path=db)
    active = who_is_editing(db_path=db)
    assert len(active) == 1
    assert active[0]['agent_id'] == 'sessionA'
    assert active[0]['file_path'] == rel_path(target)
    assert active[0]['age_seconds'] >= 0


def test_a_checked_in_checkout_is_not_reported(target, db):
    ctx = checkout(target, agent_id='sessionA', db_path=db)
    checkin(ctx, 'done\n')
    assert who_is_editing(db_path=db) == []


def test_a_released_checkout_is_not_reported(target, db):
    """Bailing out without writing must not leave a phantom active for an hour."""
    ctx = checkout(target, agent_id='sessionA', db_path=db)
    release(ctx)
    assert who_is_editing(db_path=db) == []


def test_a_conflict_settles_its_checkout(target, db):
    """After a conflict the contract is 'take a fresh checkout', so the dead
    context must not linger in the active list."""
    ctx = checkout(target, agent_id='sessionA', db_path=db)
    with open(target, 'w') as fh:
        fh.write('moved\n')
    with pytest.raises(ConcurrentModificationError):
        checkin(ctx, 'x\n')
    assert who_is_editing(db_path=db) == []


def test_two_checkouts_and_one_checkin_leaves_exactly_one_open(target, db):
    """Pairing is by COUNT, not 'the latest action wins'.

    'Latest wins' would report zero open here, which is a lie that hides a real
    outstanding edit.
    """
    checkout(target, agent_id='sessionA', db_path=db)
    second = checkout(target, agent_id='sessionA', db_path=db)
    checkin(second, 'written\n')

    active = who_is_editing(db_path=db)
    assert len(active) == 1
    assert active[0]['agent_id'] == 'sessionA'


def test_different_agents_on_the_same_file_are_tracked_separately(target, db):
    checkout(target, agent_id='cody', db_path=db)
    checkout(target, agent_id='raven', db_path=db)
    active = who_is_editing(db_path=db)
    assert sorted(r['agent_id'] for r in active) == ['cody', 'raven']


def test_a_checkout_older_than_the_window_is_treated_as_a_dead_session(
        target, db):
    checkout(target, agent_id='ghost', db_path=db)
    assert who_is_editing(db_path=db, max_age_sec=3600) != []
    # Same rows, a window that excludes them.
    assert who_is_editing(db_path=db, max_age_sec=0.0,
                          now=time.time() + 10_000) == []


def test_changed_since_checkout_flags_a_file_somebody_else_moved(target, db):
    checkout(target, agent_id='cody', db_path=db)
    active = who_is_editing(db_path=db)
    assert active[0]['changed_since_checkout'] is False

    with open(target, 'w') as fh:
        fh.write('somebody-else\n')
    active = who_is_editing(db_path=db)
    assert active[0]['changed_since_checkout'] is True


def test_who_is_editing_sorts_newest_first(tmp_path, db):
    a = tmp_path / 'a.py'
    b = tmp_path / 'b.py'
    a.write_text('a\n')
    b.write_text('b\n')
    checkout(str(a), agent_id='one', db_path=db)
    time.sleep(1.05)                 # ts is integer seconds
    checkout(str(b), agent_id='two', db_path=db)

    active = who_is_editing(db_path=db)
    assert [r['agent_id'] for r in active] == ['two', 'one']


def test_who_is_editing_on_an_unreadable_db_returns_empty_and_warns(
        tmp_path, caplog):
    """Convention 11: [] here means COULD NOT RUN.

    That is precisely why the pre-commit hook treats an empty list as advisory
    and never as proof that nobody is editing.
    """
    import logging
    bogus = tmp_path / 'not-a-database.db'
    bogus.write_bytes(b'this is not sqlite at all, not even close')
    with caplog.at_level(logging.WARNING):
        assert who_is_editing(db_path=str(bogus)) == []
    assert 'COULD NOT RUN' in caplog.text


def test_format_active_says_so_when_nothing_is_open():
    assert format_active([]) == 'no active checkouts'


# ---------------------------------------------------------------------------
# The audit log must never be able to veto a correct write
# ---------------------------------------------------------------------------

def test_a_dead_database_does_not_stop_the_file_from_being_written(
        target, tmp_path, caplog):
    """The file is the truth, the table is the record.

    A module that refused to save your work because an audit row would not
    insert would be worse than one with a gap in its log.
    """
    import logging
    bogus = str(tmp_path / 'corrupt.db')
    with open(bogus, 'wb') as fh:
        fh.write(b'definitely not sqlite')

    with caplog.at_level(logging.WARNING):
        ctx = checkout(target, agent_id='cody', db_path=bogus)
        checkin(ctx, 'written-anyway\n')

    assert open(target).read() == 'written-anyway\n'
    assert 'the write itself is unaffected' in caplog.text


# ---------------------------------------------------------------------------
# The advisory lock actually serialises two concurrent checkins
# ---------------------------------------------------------------------------

def test_concurrent_checkins_produce_exactly_one_winner(tmp_path, db):
    """Two threads, two checkouts of the SAME snapshot, both checking in.

    Without a lock around verify-then-write both could pass the hash check and
    the second would clobber the first. Exactly one must win and exactly one
    must raise, and the file must equal the winner's content - not a mixture.
    """
    target = str(tmp_path / 'contended.py')
    with open(target, 'w') as fh:
        fh.write('base\n')

    ctx_a = checkout(target, agent_id='A', db_path=db)
    ctx_b = checkout(target, agent_id='B', db_path=db)
    assert ctx_a.hash == ctx_b.hash

    barrier = threading.Barrier(2)
    outcomes = {}

    def attempt(name, ctx):
        barrier.wait()
        try:
            checkin(ctx, 'written-by-%s\n' % name)
            outcomes[name] = 'won'
        except ConcurrentModificationError:
            outcomes[name] = 'lost'

    threads = [threading.Thread(target=attempt, args=('A', ctx_a)),
               threading.Thread(target=attempt, args=('B', ctx_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes.values()) == ['lost', 'won']
    winner = [n for n, v in outcomes.items() if v == 'won'][0]
    assert open(target).read() == 'written-by-%s\n' % winner


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------

def test_unified_diff_labels_a_file_that_did_not_exist():
    out = unified_diff(None, 'new\n', 'x.py')
    assert 'did-not-exist' in out


def test_unified_diff_labels_a_deletion():
    out = unified_diff('old\n', None, 'x.py')
    assert 'deleted' in out


def test_unified_diff_of_identical_content_is_empty():
    assert unified_diff('same\n', 'same\n', 'x.py') == ''


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_ensure_schema_is_idempotent(tmp_path):
    path = str(tmp_path / 'twice.db')
    ensure_schema(path)
    ensure_schema(path, force=True)
    ensure_schema(path, force=True)
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert 'file_coordination' in names


def test_the_table_has_exactly_the_specified_columns(db):
    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute(
            'PRAGMA table_info(file_coordination)')]
    finally:
        conn.close()
    assert cols == ['id', 'ts', 'file_path', 'agent_id', 'action', 'old_hash',
                    'new_hash', 'conflict_diff']


def test_the_module_writes_to_no_table_but_file_coordination(target, db):
    """Append-only, one table. It shares db/trading.db with a live loop."""
    before = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    ctx = checkout(target, db_path=db)
    checkin(ctx, 'x\n')
    safe_write(target, 'y\n', db_path=db)
    after = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert after == before

    conn = sqlite3.connect(db)
    try:
        counts = conn.execute(
            'SELECT COUNT(*) FROM file_coordination').fetchone()[0]
    finally:
        conn.close()
    assert counts == 3       # checkout, checkin, write


def test_a_huge_conflict_diff_is_truncated_before_it_reaches_the_table(
        tmp_path, db):
    """A 40MB diff in an audit row helps nobody and bloats a database a live
    trading loop is writing to."""
    from engine.concurrency import MAX_STORED_DIFF_CHARS
    target = str(tmp_path / 'big.txt')
    with open(target, 'w') as fh:
        fh.write('line\n' * 20_000)

    ctx = checkout(target, db_path=db)
    with open(target, 'w') as fh:
        fh.write('different\n' * 20_000)
    with pytest.raises(ConcurrentModificationError) as excinfo:
        checkin(ctx, 'x\n')

    stored = rows(db, ACTION_CONFLICT)[0][5]
    assert len(stored) <= MAX_STORED_DIFF_CHARS + 100
    assert 'truncated' in stored
    # The in-memory diff on the exception is NOT truncated - the agent
    # reconciling needs all of it.
    assert len(excinfo.value.diff) > len(stored)


# ---------------------------------------------------------------------------
# Context object
# ---------------------------------------------------------------------------

def test_the_context_repr_is_readable(target, db):
    ctx = checkout(target, agent_id='cody', db_path=db)
    text = repr(ctx)
    assert 'CheckoutContext' in text
    assert 'cody' in text
    assert isinstance(ctx, CheckoutContext)
