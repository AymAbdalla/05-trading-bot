"""Tests for scripts/pre-commit-conflict-check -- the pre-commit hook.

Convention 22: a claim in a docstring is not a wiring test. The hook claims to
refuse things, so what is PINNED here is the refusing, by running the real
script as a subprocess and reading its exit code. Nothing is mocked and no
function is imported from it -- it is a bash script with an embedded python
heredoc, and the only honest way to test that is to execute it.

The load-bearing tests are the two that encode the failure this step exists
for (`git add -A` sweeping another agent's ledger-written files into a commit
whose message never mentions them, as in b1d44bb and 4d03681):

  * test_cross_owner_sweep_without_a_declaration_is_refused
  * test_declared_sweep_is_allowed_and_lists_every_swept_path

Every test builds a THROWAWAY git repo under tmp_path and a THROWAWAY
coordination database. Nothing here touches db/trading.db or the real
repository -- a live shadow loop is writing to the former and sibling sessions
to the latter (convention 21).

The throwaway repo gets a symlink to the real `engine` package so the hook
imports the REAL engine.concurrency rather than silently falling back to its
degraded stub. `test_the_hook_imports_the_real_concurrency_module` pins that,
because if it ever regressed the rest of this file would still pass while
testing a fallback nobody ships.
"""

import os
import sqlite3
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from engine.concurrency import ensure_schema, hash_bytes  # noqa: E402

HOOK = os.path.join(REPO_ROOT, 'scripts', 'pre-commit-conflict-check')

#: Cleared from the child environment in every run. The hook reads AGENT_ID and
#: TRADING_BOT_AGENT_ID as identity declarations, and whoever runs pytest may
#: well have one exported; inheriting it would make "undeclared" tests declare.
CLEARED = (
    'SKIP_CONFLICT_CHECK',
    'CONFLICT_CHECK_DB',
    'CONFLICT_CHECK_AGENT_ID',
    'CONFLICT_CHECK_ALLOW_SWEEP',
    'CONFLICT_CHECK_MAX_AGE',
    'AGENT_ID',
    'TRADING_BOT_AGENT_ID',
)

ALLOWED = 0
REFUSED = 1


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------

def _git(repo, *args):
    proc = subprocess.run(['git'] + list(args), cwd=str(repo),
                          capture_output=True, text=True)
    assert proc.returncode == 0, 'git %s failed: %s' % (args, proc.stderr)
    return proc.stdout


class Sandbox(object):
    """A throwaway repo plus a throwaway coordination database."""

    def __init__(self, repo, db):
        self.repo = repo
        self.db = db

    def write(self, rel, content):
        """Write a file into the sandbox repo. Returns its sha256."""
        path = os.path.join(str(self.repo), rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        data = content.encode('utf-8')
        with open(path, 'wb') as fh:
            fh.write(data)
        return hash_bytes(data)

    def stage(self, rel):
        _git(self.repo, 'add', '--', rel)

    def write_and_stage(self, rel, content):
        digest = self.write(rel, content)
        self.stage(rel)
        return digest

    def record(self, rel, agent_id, new_hash, action='checkin', ts=None):
        """Insert a coordinated-write row, the way engine.concurrency would."""
        conn = sqlite3.connect(str(self.db))
        try:
            with conn:
                conn.execute(
                    'INSERT INTO file_coordination '
                    '(ts, file_path, agent_id, action, old_hash, new_hash, '
                    'conflict_diff) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (int(ts if ts is not None else time.time()), rel,
                     agent_id, action, None, new_hash, None))
        finally:
            conn.close()

    def owned_by(self, rel, agent_id, content):
        """The common setup: a file written and recorded by `agent_id`."""
        digest = self.write_and_stage(rel, content)
        self.record(rel, agent_id, digest)
        return digest

    def run(self, **env_overrides):
        """Run the hook. Returns subprocess.CompletedProcess."""
        env = dict(os.environ)
        for key in CLEARED:
            env.pop(key, None)
        env['CONFLICT_CHECK_DB'] = str(self.db)
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return subprocess.run(['bash', HOOK], cwd=str(self.repo),
                              capture_output=True, text=True, env=env)


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'test@example.invalid')
    _git(repo, 'config', 'user.name', 'Test')
    # So the hook imports the real engine.concurrency rather than its stub.
    os.symlink(os.path.join(REPO_ROOT, 'engine'), str(repo / 'engine'))
    db = tmp_path / 'coordination.db'
    ensure_schema(str(db))
    return Sandbox(repo, db)


# ---------------------------------------------------------------------------
# The sandbox itself, and the pre-existing behaviour that must not regress
# ---------------------------------------------------------------------------

def test_the_hook_imports_the_real_concurrency_module(sandbox):
    """If this fails every other test here is exercising the fallback stub."""
    sandbox.write_and_stage('a.txt', 'hello\n')
    result = sandbox.run()
    assert 'engine.concurrency did not import' not in result.stdout
    assert '[1/3] active checkouts' in result.stdout


def test_nothing_staged_is_allowed(sandbox):
    result = sandbox.run()
    assert result.returncode == ALLOWED
    assert 'OK (nothing to verify)' in result.stdout


def test_a_staged_hash_matching_the_ledger_is_verified(sandbox):
    sandbox.owned_by('a.txt', 'cody-alpha', 'hello\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'verified=1' in result.stdout


def test_a_staged_hash_differing_from_the_ledger_is_refused(sandbox):
    """The pre-existing step-2 refusal. Unchanged by the provenance step."""
    sandbox.write_and_stage('a.txt', 'edited outside the module\n')
    sandbox.record('a.txt', 'cody-alpha', hash_bytes(b'what the ledger saw\n'))
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED
    assert 'MISMATCH=1' in result.stdout
    assert 'changed after the last write' in result.stdout


def test_skip_conflict_check_still_bypasses_everything(sandbox):
    """Including the new step. A bypass has to bypass all of it or it lies."""
    sandbox.owned_by('a.txt', 'cody-alpha', 'hello\n')
    result = sandbox.run(SKIP_CONFLICT_CHECK='1')
    assert result.returncode == ALLOWED
    assert 'BYPASSED' in result.stdout
    assert 'This is a bypass, not a pass' in result.stdout


def test_an_unreadable_coordination_table_allows_and_says_so(sandbox):
    """COULD NOT VERIFY must never read as VERIFIED (convention 11)."""
    sandbox.write_and_stage('a.txt', 'hello\n')
    result = sandbox.run(
        CONFLICT_CHECK_DB=os.path.join(str(sandbox.repo), 'no-such.db'))
    assert result.returncode == ALLOWED
    assert 'NOTHING was verified' in result.stdout
    assert 'owner-unknown=1' in result.stdout


# ---------------------------------------------------------------------------
# Step 3: the cross-owner sweep. This is what the session was for.
# ---------------------------------------------------------------------------

def test_own_work_commit_is_allowed_and_raises_no_warning(sandbox):
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout
    assert 'FOREIGN-OWNED=0' in result.stdout
    assert 'SWEEP' not in result.stdout


def test_cross_owner_sweep_without_a_declaration_is_refused(sandbox):
    """The b1d44bb / 4d03681 failure, as a test.

    An undeclared session stages a file whose last coordinated write belongs to
    a different agent. Step 2 PASSES -- the hash matches, it always did -- and
    the commit is refused anyway.
    """
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run()
    assert result.returncode == REFUSED
    assert 'verified=1' in result.stdout      # step 2 was happy
    assert 'MISMATCH=0' in result.stdout      # and is not why we refused
    assert 'FOREIGN-OWNED=1' in result.stdout
    assert 'written through engine.concurrency by a DIFFERENT agent' \
        in result.stdout
    # The refusal has to name the path, the owner and the committing identity,
    # or the committer cannot act on it.
    assert 'theirs.txt' in result.stdout
    assert 'cody-execute-plan' in result.stdout
    assert 'NOT DECLARED' in result.stdout


def test_cross_owner_sweep_with_the_wrong_declaration_is_refused(sandbox):
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-beta')
    assert result.returncode == REFUSED
    assert 'FOREIGN-OWNED=1' in result.stdout
    assert 'cody-beta' in result.stdout
    assert 'cody-execute-plan' in result.stdout


def test_declared_sweep_is_allowed_and_lists_every_swept_path(sandbox):
    """The reconcile escape. Allowed, but never silent."""
    sandbox.owned_by('one.txt', 'cody-execute-plan', 'one\n')
    sandbox.owned_by('two.txt', 'raven-execute-plan-commit', 'two\n')
    result = sandbox.run(CONFLICT_CHECK_ALLOW_SWEEP='1')
    assert result.returncode == ALLOWED
    assert 'SWEEP DECLARED' in result.stdout
    assert 'FOREIGN-OWNED=2' in result.stdout
    for path, owner in (('one.txt', 'cody-execute-plan'),
                        ('two.txt', 'raven-execute-plan-commit')):
        assert path in result.stdout
        assert owner in result.stdout


@pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'yes', 'on'])
def test_the_sweep_escape_accepts_its_documented_spellings(sandbox, value):
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(CONFLICT_CHECK_ALLOW_SWEEP=value)
    assert result.returncode == ALLOWED
    assert 'SWEEP DECLARED' in result.stdout


def test_an_unrecognised_sweep_value_refuses_and_says_it_is_not_armed(sandbox):
    """A typo'd escape must not look like "I never set it" (convention 20)."""
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(CONFLICT_CHECK_ALLOW_SWEEP='maybe')
    assert result.returncode == REFUSED
    assert 'is NOT armed' in result.stdout
    assert "set to 'maybe'" in result.stdout


# ---------------------------------------------------------------------------
# Humans, and everything the hook must NOT start refusing
# ---------------------------------------------------------------------------

def test_a_human_commit_over_paths_with_no_ledger_row_is_unchanged(sandbox):
    """Aym commits a file nothing has ever coordinated. Nothing changes."""
    sandbox.write_and_stage('notes.md', 'hand written\n')
    result = sandbox.run()
    assert result.returncode == ALLOWED
    assert 'untracked-by-coordination=1' in result.stdout
    assert 'no-agent-owner=1' in result.stdout
    assert 'FOREIGN-OWNED=0' in result.stdout


@pytest.mark.parametrize('owner', ['aym', 'ayman', 'human', 'manual-edit'])
def test_a_human_shaped_ledger_owner_is_not_a_cross_owner_sweep(sandbox,
                                                                owner):
    """Only a KNOWN non-human owner can trigger the refusal."""
    sandbox.owned_by('a.txt', owner, 'hello\n')
    result = sandbox.run()
    assert result.returncode == ALLOWED
    assert 'no-agent-owner=1' in result.stdout


@pytest.mark.parametrize('owner', [
    'cody', 'cody-execute-plan', 'raven-execute-plan-commit',
    'CODY-Shouty', 'forge_eval', 'hermes.digest', 'claude:one', 'agent/x',
])
def test_known_agent_id_shapes_are_recognised_as_agents(sandbox, owner):
    sandbox.owned_by('a.txt', owner, 'hello\n')
    result = sandbox.run()
    assert result.returncode == REFUSED
    assert 'FOREIGN-OWNED=1' in result.stdout


@pytest.mark.parametrize('owner', ['codyssey', 'ravenous', 'forgery'])
def test_an_id_that_merely_starts_with_an_agent_word_is_not_an_agent(sandbox,
                                                                     owner):
    """`cody` must match `cody-foo`, not `codyssey`. Separator or nothing."""
    sandbox.owned_by('a.txt', owner, 'hello\n')
    result = sandbox.run()
    assert result.returncode == ALLOWED
    assert 'no-agent-owner=1' in result.stdout


@pytest.mark.parametrize('var', ['CONFLICT_CHECK_AGENT_ID', 'AGENT_ID',
                                 'TRADING_BOT_AGENT_ID'])
def test_every_documented_identity_variable_is_read(sandbox, var):
    """AGENT_ID is what engine.concurrency already reads. One identity."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(**{var: 'cody-alpha'})
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout
    assert var in result.stdout


def test_identity_matching_ignores_case_and_surrounding_space(sandbox):
    sandbox.owned_by('mine.txt', 'cody-Alpha', 'my work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='  cody-alpha  ')
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout


def test_a_blank_declaration_counts_as_undeclared(sandbox):
    """An exported-but-empty AGENT_ID must not read as a claim of ownership."""
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='   ')
    assert result.returncode == REFUSED
    assert 'NOT DECLARED' in result.stdout


def test_the_declaration_is_reported_as_unverified(sandbox):
    """It raises the cost of the mistake. It is not a security boundary."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert 'UNVERIFIED' in result.stdout


# ---------------------------------------------------------------------------
# Interactions and accounting
# ---------------------------------------------------------------------------

def test_a_mixed_commit_refuses_on_the_foreign_path_only(sandbox):
    """Own work + somebody else's. The refusal names only what is not yours."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED
    assert 'own-work=1' in result.stdout
    assert 'FOREIGN-OWNED=1' in result.stdout
    foreign_block = result.stdout.split('FOREIGN-OWNED (1):')[1]
    assert 'theirs.txt' in foreign_block.split('own-work')[0]


def test_both_refusals_are_reported_in_one_run(sandbox):
    """Fixing one and rediscovering the other on the next attempt is waste."""
    sandbox.write_and_stage('changed.txt', 'edited outside the module\n')
    sandbox.record('changed.txt', 'cody-alpha', hash_bytes(b'ledger saw\n'))
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run()
    assert result.returncode == REFUSED
    assert 'changed after the last write' in result.stdout
    assert 'by a DIFFERENT agent' in result.stdout


def test_a_declared_sweep_does_not_rescue_a_hash_mismatch(sandbox):
    """The escape covers provenance only. It is not a general override."""
    sandbox.write_and_stage('changed.txt', 'edited outside the module\n')
    sandbox.record('changed.txt', 'cody-alpha', hash_bytes(b'ledger saw\n'))
    result = sandbox.run(CONFLICT_CHECK_ALLOW_SWEEP='1')
    assert result.returncode == REFUSED
    assert 'MISMATCH=1' in result.stdout


def test_the_newest_coordinated_write_decides_the_owner(sandbox):
    """Two agents touched it; the LAST one owns it."""
    digest = sandbox.write_and_stage('a.txt', 'final content\n')
    sandbox.record('a.txt', 'cody-first', hash_bytes(b'older\n'), ts=1000)
    sandbox.record('a.txt', 'cody-second', digest, ts=2000)
    assert sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-second').returncode \
        == ALLOWED
    assert sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-first').returncode \
        == REFUSED


def test_the_provenance_accounting_identity_holds_over_a_mixed_commit(sandbox):
    """Convention 20: every staged path lands in exactly one owner bucket."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'mine\n')
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'theirs\n')
    sandbox.owned_by('human.txt', 'aym', 'human\n')
    sandbox.write_and_stage('orphan.txt', 'no ledger row\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha',
                         CONFLICT_CHECK_ALLOW_SWEEP='1')
    assert result.returncode == ALLOWED
    assert 'INTERNAL ERROR' not in result.stdout
    assert 'total=4  own-work=1  FOREIGN-OWNED=1  no-agent-owner=2  ' \
           'owner-unknown=0' in result.stdout


def test_a_deleted_path_is_not_classified_at_all(sandbox):
    """Deletions have no content to hash and no owner question to answer."""
    sandbox.owned_by('gone.txt', 'cody-alpha', 'bye\n')
    _git(sandbox.repo, 'commit', '-q', '--no-verify', '-m', 'seed')
    os.remove(os.path.join(str(sandbox.repo), 'gone.txt'))
    sandbox.stage('gone.txt')
    result = sandbox.run()
    assert result.returncode == ALLOWED
    assert 'OK (nothing to verify)' in result.stdout


# ---------------------------------------------------------------------------
# GIT_AUTHOR_NAME: the prefix-free declaration channel
# ---------------------------------------------------------------------------

def test_an_agent_shaped_git_author_declares_the_identity(sandbox):
    """`git commit --author=...` needs no env prefix, which spawned sessions
    frequently cannot set. git exports it to the hook, so it works."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(GIT_AUTHOR_NAME='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout
    assert 'GIT_AUTHOR_NAME' in result.stdout


def test_a_human_git_author_is_not_a_declaration(sandbox):
    """Load-bearing. git sets GIT_AUTHOR_NAME on EVERY commit, falling back to
    user.name, so accepting it unconditionally would make every one of Aym's
    commits declare `Aym Abdalla` and retire the NOT DECLARED state."""
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(GIT_AUTHOR_NAME='Aym Abdalla')
    assert result.returncode == REFUSED
    assert 'NOT DECLARED' in result.stdout
    assert 'Aym Abdalla' not in result.stdout


def test_a_human_git_author_still_commits_their_own_paths(sandbox):
    """The other half: rejecting it as a DECLARATION must not start refusing
    ordinary human commits."""
    sandbox.write_and_stage('notes.md', 'hand written\n')
    sandbox.owned_by('human.txt', 'aym', 'also mine\n')
    result = sandbox.run(GIT_AUTHOR_NAME='Aym Abdalla')
    assert result.returncode == ALLOWED
    assert 'no-agent-owner=2' in result.stdout


def test_an_explicit_declaration_beats_the_git_author(sandbox):
    """Resolution order: the explicit variables win."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha',
                         GIT_AUTHOR_NAME='cody-somebody-else')
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout
    assert 'CONFLICT_CHECK_AGENT_ID' in result.stdout


def test_a_git_author_naming_a_different_agent_is_still_refused(sandbox):
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run(GIT_AUTHOR_NAME='cody-alpha')
    assert result.returncode == REFUSED
    assert 'FOREIGN-OWNED=1' in result.stdout
    assert 'cody-alpha' in result.stdout


def test_the_refusal_offers_the_author_form_as_an_escape_hatch(sandbox):
    """A session that cannot set an env prefix must be told the form it can
    actually use, or it will reach for --no-verify."""
    sandbox.owned_by('theirs.txt', 'cody-execute-plan', 'their work\n')
    result = sandbox.run()
    assert result.returncode == REFUSED
    assert '--author=' in result.stdout
