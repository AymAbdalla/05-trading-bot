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
INSTALLER = os.path.join(REPO_ROOT, 'scripts', 'install_conflict_hook.sh')

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

    def env(self, **overrides):
        env = dict(os.environ)
        for key in CLEARED:
            env.pop(key, None)
        env['CONFLICT_CHECK_DB'] = str(self.db)
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run(self, _argv=(), **env_overrides):
        """Run the hook. Returns subprocess.CompletedProcess.

        `_argv` is what git forwards: nothing for pre-commit, the composed
        message file for commit-msg. The script picks its job from it.
        """
        return subprocess.run(['bash', HOOK] + list(_argv), cwd=str(self.repo),
                              capture_output=True, text=True,
                              env=self.env(**env_overrides))

    def run_msg(self, message, **env_overrides):
        """Run the hook in commit-msg mode over `message`."""
        path = os.path.join(str(self.repo), 'MSG_UNDER_TEST')
        with open(path, 'wb') as fh:
            fh.write(message.encode('utf-8'))
        return self.run(_argv=[path], **env_overrides)

    def install_hooks(self):
        """Run the REAL installer into this sandbox."""
        return subprocess.run(['bash', INSTALLER], cwd=str(self.repo),
                              capture_output=True, text=True, env=self.env())

    def commit(self, message, **env_overrides):
        """A real `git commit`, hooks and all. Not a direct hook call."""
        return subprocess.run(['git', 'commit', '-m', message],
                              cwd=str(self.repo), capture_output=True,
                              text=True, env=self.env(**env_overrides))


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'test@example.invalid')
    _git(repo, 'config', 'user.name', 'Test')
    # So the hook imports the real engine.concurrency rather than its stub,
    # and so the real installer can find the real logic script.
    os.symlink(os.path.join(REPO_ROOT, 'engine'), str(repo / 'engine'))
    os.symlink(os.path.join(REPO_ROOT, 'scripts'), str(repo / 'scripts'))
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


# ---------------------------------------------------------------------------
# Step 4: the D-335 Agent-Id commit trailer, in commit-msg mode.
#
# The four the ruling asked for are the first four. The rest pin the corners
# that decide whether this gate is real: git's trailer semantics (last
# paragraph only, `#` comments stripped), and the measurement that says the
# check cannot live in pre-commit at all.
# ---------------------------------------------------------------------------

TRAILED = 'subject line\n\nbody\n\nAgent-Id: cody-alpha\n'


def test_a_matching_trailer_is_allowed(sandbox):
    """(a) The sanctioned path. If this fails, nothing can ever be committed."""
    result = sandbox.run_msg(TRAILED, CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'matches the resolved identity' in result.stdout


def test_a_missing_trailer_is_refused(sandbox):
    """(b) The whole point of D-335: identity resolved, nothing recorded."""
    result = sandbox.run_msg('subject line\n\nbody, no trailer\n',
                             CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED
    assert 'carries no Agent-Id trailer' in result.stdout


def test_a_mismatched_trailer_is_refused(sandbox):
    """(c) A trailer naming somebody else is worse than none: it misattributes."""
    result = sandbox.run_msg('subject\n\nAgent-Id: cody-somebody-else\n',
                             CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED
    assert 'cody-somebody-else' in result.stdout
    assert 'cody-alpha' in result.stdout


def test_no_identity_needs_no_trailer(sandbox):
    """(d) The human path. D-335(2) gates on a RESOLVED identity only."""
    result = sandbox.run_msg('just a human commit\n')
    assert result.returncode == ALLOWED
    assert 'no identity declared' in result.stdout


def test_the_refusal_prints_the_exact_line_to_add(sandbox):
    """Convention 33: a gate has to name its own sanctioned path."""
    result = sandbox.run_msg('subject\n', CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED
    assert 'Agent-Id: cody-alpha' in result.stdout


@pytest.mark.parametrize('var', ['CONFLICT_CHECK_AGENT_ID', 'AGENT_ID',
                                 'TRADING_BOT_AGENT_ID'])
def test_every_identity_variable_gates_the_trailer(sandbox, var):
    """Same resolution order as step 3, or the two steps disagree on WHO."""
    assert sandbox.run_msg(TRAILED, **{var: 'cody-alpha'}).returncode == ALLOWED
    assert sandbox.run_msg('subject\n',
                           **{var: 'cody-alpha'}).returncode == REFUSED


def test_an_agent_shaped_git_author_also_gates_the_trailer(sandbox):
    result = sandbox.run_msg('subject\n', GIT_AUTHOR_NAME='cody-alpha')
    assert result.returncode == REFUSED


def test_a_human_git_author_does_not_gate_the_trailer(sandbox):
    """git sets GIT_AUTHOR_NAME on every commit. Aym's commits stay unchanged."""
    result = sandbox.run_msg('subject\n', GIT_AUTHOR_NAME='Aym Abdalla')
    assert result.returncode == ALLOWED


def test_the_trailer_key_is_case_insensitive(sandbox):
    """git parses trailer keys case-insensitively, so this hook must too."""
    result = sandbox.run_msg('subject\n\nagent-id: cody-alpha\n',
                             CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED


def test_the_identity_comparison_ignores_case_and_space(sandbox):
    result = sandbox.run_msg('subject\n\nAgent-Id:   CODY-Alpha  \n',
                             CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED


def test_a_trailer_that_is_not_the_last_paragraph_is_refused(sandbox):
    """Trailer semantics are git's, not "the string appears somewhere".

    A message with prose after the trailer block has NO trailers as far as git
    is concerned, so `git log --grep` would not find it either. Accepting it
    here would record provenance that the tooling cannot read back.
    """
    result = sandbox.run_msg(
        'subject\n\nAgent-Id: cody-alpha\n\nafterthought prose\n',
        CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED


def test_git_comment_lines_do_not_hide_the_trailer(sandbox):
    """A real `git commit` message file is full of `#` lines at this point.

    If these were counted as a trailing paragraph, EVERY interactive commit
    would be refused and the gate would be bypassed within a day.
    """
    result = sandbox.run_msg(
        'subject\n\nAgent-Id: cody-alpha\n'
        '# Please enter the commit message for your changes.\n'
        '# On branch main\n',
        CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED


def test_a_trailer_beside_other_trailers_is_found(sandbox):
    result = sandbox.run_msg(
        'subject\n\nCo-Authored-By: Someone <s@x.invalid>\n'
        'Agent-Id: cody-alpha\n',
        CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED


def test_two_conflicting_trailers_are_refused(sandbox):
    """Ambiguous provenance is not provenance."""
    result = sandbox.run_msg(
        'subject\n\nAgent-Id: cody-alpha\nAgent-Id: cody-beta\n',
        CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == REFUSED


def test_a_missing_message_file_could_not_run_and_allows(sandbox):
    """Convention 11: COULD NOT RUN is not a refusal and not a pass."""
    result = sandbox.run(_argv=[os.path.join(str(sandbox.repo), 'nope.txt')],
                         CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'COULD NOT RUN' in result.stdout
    assert 'NOTHING was verified' in result.stdout


def test_skip_conflict_check_bypasses_the_trailer_check_too(sandbox):
    """A bypass has to bypass all of it or it lies about what it skipped."""
    result = sandbox.run_msg('subject\n', CONFLICT_CHECK_AGENT_ID='cody-alpha',
                             SKIP_CONFLICT_CHECK='1')
    assert result.returncode == ALLOWED
    assert 'BYPASSED' in result.stdout


def test_pre_commit_mode_never_requires_a_trailer(sandbox):
    """The two modes must not both claim the trailer, or one of them is wrong."""
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.run(CONFLICT_CHECK_AGENT_ID='cody-alpha')
    assert result.returncode == ALLOWED
    assert 'own-work=1' in result.stdout
    assert 'verified by the commit-msg hook' in result.stdout


# ---------------------------------------------------------------------------
# Why the check is NOT in pre-commit. This is the measurement, as a test.
# ---------------------------------------------------------------------------

def test_commit_editmsg_at_pre_commit_time_is_the_previous_message(sandbox):
    """D-335 says to read .git/COMMIT_EDITMSG from pre-commit. It is STALE.

    git composes the new message only after pre-commit returns, so at that
    moment the file still holds the message of the commit BEFORE this one.
    Gating on it would refuse the first agent commit for a predecessor written
    before the rule existed, and pass every commit that follows a correct one
    no matter what it said. That is why step 4 runs as commit-msg instead.

    If this test ever fails, git changed its ordering and the design decision
    behind step 4 should be revisited.
    """
    probe = os.path.join(str(sandbox.repo), '.git', 'hooks', 'pre-commit')
    with open(probe, 'w') as fh:
        fh.write('#!/bin/bash\n'
                 'M="$(git rev-parse --git-dir)/COMMIT_EDITMSG"\n'
                 'if [ -f "$M" ]; then echo "SAW:[$(head -1 "$M")]";'
                 ' else echo "SAW:<NO FILE>"; fi\n')
    os.chmod(probe, 0o755)

    seen = []
    for msg in ('FIRST', 'SECOND', 'THIRD'):
        sandbox.write_and_stage('a.txt', msg + '\n')
        result = sandbox.commit(msg)
        assert result.returncode == 0, result.stderr
        seen.append((result.stdout + result.stderr))

    assert 'SAW:<NO FILE>' in seen[0]      # nothing to read at all
    assert 'SAW:[FIRST]' in seen[1]        # one behind
    assert 'SAW:[SECOND]' in seen[2]       # still one behind


# ---------------------------------------------------------------------------
# Convention 33: exercise the sanctioned path as one of the agents it governs.
# These drive a REAL `git commit` through the REAL installed shims.
# ---------------------------------------------------------------------------

def test_the_installer_installs_both_shims(sandbox):
    result = sandbox.install_hooks()
    assert result.returncode == 0, result.stderr
    for name in ('pre-commit', 'commit-msg'):
        path = os.path.join(str(sandbox.repo), '.git', 'hooks', name)
        assert os.access(path, os.X_OK), '%s not installed executable' % name


def test_a_real_agent_commit_with_the_trailer_succeeds(sandbox):
    """The end-to-end sanctioned path: identity declared, trailer present."""
    assert sandbox.install_hooks().returncode == 0
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.commit('records: a thing\n\nAgent-Id: cody-alpha',
                            AGENT_ID='cody-alpha')
    assert result.returncode == 0, result.stdout + result.stderr
    log = _git(sandbox.repo, 'log', '-1', '--pretty=%B')
    assert 'Agent-Id: cody-alpha' in log


def test_a_real_agent_commit_without_the_trailer_is_refused(sandbox):
    """The failure D-335 exists to stop, through the real hooks."""
    assert sandbox.install_hooks().returncode == 0
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    result = sandbox.commit('records: a thing', AGENT_ID='cody-alpha')
    assert result.returncode != 0
    assert 'carries no Agent-Id trailer' in (result.stdout + result.stderr)
    # and nothing was committed
    ok = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', 'HEAD'],
                        cwd=str(sandbox.repo), capture_output=True)
    assert ok.returncode != 0, 'a refused commit still created a commit'


def test_a_real_human_commit_needs_no_trailer(sandbox):
    """Aym's own commits must behave exactly as they did before D-335."""
    assert sandbox.install_hooks().returncode == 0
    sandbox.write_and_stage('a.txt', 'human work\n')
    result = sandbox.commit('a plain human commit')
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_trailer_lands_where_git_log_grep_can_find_it(sandbox):
    """D-335(1) promises greppable provenance. Pin the promise, not the intent."""
    assert sandbox.install_hooks().returncode == 0
    sandbox.owned_by('mine.txt', 'cody-alpha', 'my work\n')
    assert sandbox.commit('records: a thing\n\nAgent-Id: cody-alpha',
                          AGENT_ID='cody-alpha').returncode == 0
    found = _git(sandbox.repo, 'log', '--grep=^Agent-Id: cody-alpha',
                 '--pretty=%h')
    assert found.strip(), 'git log --grep cannot find the trailer'
