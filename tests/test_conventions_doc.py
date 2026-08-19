"""Pins `docs/CONVENTIONS.md`, the tracked home of the numbered conventions.

Until 2026-08-18 the conventions lived only in `CLAUDE.md`, which is gitignored
and rewritten wholesale at the end of every session. Under several concurrent
Cody sessions that is a clobber surface, and it clobbered: convention 27 was
written twice in one day with two different meanings and the first was lost.
D-292 already ruled that silently overwriting an existing convention is the
worse failure. A rewritten untracked file cannot enforce a ruling. This file
can, so the conventions moved to a tracked doc and these tests hold it.

Three jobs:

  1. **Numbering is contiguous and unique.** A duplicate number is exactly the
     failure that lost a convention today, so it is red rather than tolerated.
     A gap means a convention was deleted instead of superseded.

  2. **The contested conventions keep their meaning.** 27, 28 and 29 all trace
     back to a collision. Each is pinned by the phrases that carry its point,
     so a future rewrite that reassigns a number fails here first.

  3. **`.claude/` really is gitignored.** `.claude/agents/forge.md` asserts this
     in its install comment. Convention 22: a claim in a comment is not a wiring
     test, so this is the wiring test. Asked for by Raven, 2026-08-18.

Deliberately NOT tested: that `CLAUDE.md` agrees with this file. `CLAUDE.md` is
untracked, absent from a clean checkout, and rewritten by every session, so
asserting over it would be permanently red for reasons that are not drift.
`docs/CONVENTIONS.md` says in its own text that it wins over the mirror.
"""
import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONVENTIONS_PATH = os.path.join(REPO_ROOT, 'docs', 'CONVENTIONS.md')

# A convention opens a line as "<n>. " at column zero. Continuation lines are
# indented, so this does not match them, and the numbering note's markdown table
# rows start with "|" so they do not match either.
_ENTRY_RE = re.compile(r'^(\d+)\. (.*)$', re.MULTILINE)


def _read_conventions_doc():
    with open(CONVENTIONS_PATH, 'r', encoding='utf-8') as handle:
        return handle.read()


def _parse_entries(text):
    """Return {number: full text of the convention, continuation lines joined}.

    The body of a convention runs from its own "<n>. " line up to the next
    "<n>. " line or the end of the list, so a phrase that lives on an indented
    continuation line is still findable by number.
    """
    matches = list(_ENTRY_RE.finditer(text))
    entries = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        assert number not in entries, (
            'convention %d appears twice in docs/CONVENTIONS.md. That is the '
            'exact clobber D-292 ruled against.' % number)
        entries[number] = text[match.start():end].strip()
    return entries


@pytest.fixture(scope='module')
def conventions():
    return _parse_entries(_read_conventions_doc())


class TestConventionsDocExists:

    def test_the_file_is_present(self):
        assert os.path.isfile(CONVENTIONS_PATH), (
            'docs/CONVENTIONS.md is the tracked home of the conventions. If it '
            'moved, move this test with it rather than deleting it.')

    def test_it_declares_itself_canonical(self):
        text = _read_conventions_doc()
        assert 'canonical' in text.lower()
        assert 'CLAUDE.md' in text, (
            'the doc has to say what happens when it and the CLAUDE.md mirror '
            'disagree, or the mirror silently competes with it again.')


class TestNumbering:

    def test_numbering_starts_at_one_and_is_contiguous(self, conventions):
        numbers = sorted(conventions)
        assert numbers, 'no conventions parsed out of docs/CONVENTIONS.md'
        expected = list(range(1, len(numbers) + 1))
        assert numbers == expected, (
            'convention numbering must be 1..N with no gaps and no duplicates. '
            'Got %r. A gap means a convention was deleted instead of '
            'superseded; a duplicate is the 2026-08-18 clobber.'
            % (numbers,))

    def test_no_convention_is_empty(self, conventions):
        for number, body in sorted(conventions.items()):
            assert len(body) > len('%d. ' % number) + 10, (
                'convention %d has no content' % number)


class TestContestedConventions:
    """27, 28 and 29 each survived a numbering collision. Pin their meanings."""

    def test_27_is_the_gate_direction_rule(self, conventions):
        body = conventions[27]
        assert 'DIRECTION of a gate' in body
        assert 'comparison operator' in body
        assert 'BEFORE editing' in body

    def test_28_is_half_a_resolution(self, conventions):
        body = conventions[28]
        assert 'Half a resolution is not a resolution' in body
        assert 'unfollowed' in body

    def test_29_is_the_getsource_rule(self, conventions):
        """Raven's wording, 2026-08-18. Every clause here carries weight."""
        body = conventions[29]
        assert 'inspect.getsource()' in body
        # the mechanism, not just the symptom
        assert 're-reads the file from disk at call time' in body
        assert 'convention 13' in body
        # the diagnostic, which is the actionable half
        assert '`stat`' in body
        assert 'mtime' in body
        assert 'collision, not a bug' in body
        # the fix, not just the diagnosis
        assert 'imported attributes, not source text' in body

    def test_29_is_not_numbered_27(self, conventions):
        """Raven asked for 27; 27 was already taken twice when it was applied.

        D-292: take the next free number, never overwrite. If a future session
        renumbers after a Raven ruling, this test and the numbering note in the
        doc both have to move, which is the point.
        """
        assert 'getsource' not in conventions[27]
        assert 'getsource' not in conventions[28]


class TestCommitMessageConvention:
    """31, added 2026-08-18 on a Raven ruling after two false commit messages."""

    def test_31_is_the_commit_message_rule(self, conventions):
        body = conventions[31]
        assert 'A commit message is a claim, not a fact' in body
        # the actionable half: what you actually run
        assert 'git show --stat' in body
        # the two incidents that earned it, so a rewrite cannot soften it
        assert 'aafc768' in body
        assert '79ba55d' in body


class TestUnsatisfiableHookConvention:
    """33, added 2026-08-19 on Raven's ruling D-334.

    Pinned because the incident is the whole convention. Strip the example and
    what is left is a platitude nobody would apply to their own hook; the
    example is what makes a future author check whether the agents they govern
    can actually reach the sanctioned path.
    """

    def test_33_is_the_unsatisfiable_hook_rule(self, conventions):
        body = conventions[33]
        assert 'cannot be satisfied by the agents it governs' in body
        assert 'bypassed by them' in body
        # the actionable half: what a future hook author is told to do
        assert 'AS ONE OF THE AGENTS IT GOVERNS' in body
        # the incident that earned it, so a rewrite cannot soften it into advice
        assert 'cody-hook-harden' in body
        assert '--author' in body
        assert '--no-verify' in body
        # and the fact that the escape was NOT a bypass, which is the whole
        # reason this is a convention rather than a disciplinary note
        assert 'WITHOUT bypassing the hook' in body

    def test_33_names_the_fix_that_removed_the_corner(self, conventions):
        """A convention that only describes a trap, and not the exit that was
        built, sends the next reader looking for a corner that no longer
        exists."""
        body = conventions[33]
        assert 'D-331' in body
        assert 'AGENT_ID=cody-<topic>' in body


class TestPathspecCommitConvention:
    """34, added 2026-08-19 after the third cross-session index sweep.

    The rule is a mechanism, not a sentiment. The working directory AND the git
    index are shared (convention 21), so the difference between `git commit` and
    `git commit -- <paths>` is whether another session's staged work lands in a
    commit whose message never names it. Strip the mechanism out and what is
    left is advice nobody can follow at the moment it matters.
    """

    def test_34_is_the_pathspec_commit_rule(self, conventions):
        body = conventions[34]
        # the mechanism, which is the whole convention
        assert ('Commit your own paths out of a shared index with a pathspec'
                in body)
        assert 'git commit -- <paths>' in body
        # the forbidden thing
        assert 'Never `git add -A`' in body
        # why it is not merely style: the index is shared, so a bare commit
        # takes whatever a sibling already staged
        assert "another session's files may already be staged" in body
        assert "leaves another session's staged entries untouched" in body
        # the plausible-looking repair that is itself a mutation of their index
        assert 'git restore --staged' in body
        # the conventions it rests on
        assert 'convention 16' in body
        assert 'convention 21' in body

    def test_34_names_the_sweeps_it_was_earned_by(self, conventions):
        """Three real commits took files their messages never named, and the
        third went through the hook built to stop the first two. Without them
        the convention reads as caution about something hypothetical."""
        body = conventions[34]
        for sha in ('b1d44bb', '4d03681', '26555f2'):
            assert sha in body, (
                'convention 34 no longer names the sweep %s that earned it'
                % sha)
        assert 'D-337' in body

    def test_34_ships_the_commands_not_just_the_rule(self, conventions):
        """Convention 33: a rule whose sanctioned path is not spelled out gets
        approximated by the agents it governs. 34 carries the literal commands,
        the D-335 trailer included, and the one-line diagnostic that tells you
        somebody else got to the index first."""
        body = conventions[34]
        assert 'git add -- <your path>' in body
        assert 'Agent-Id: cody-<topic>' in body
        assert 'git status --porcelain' in body
        assert 'FIRST column' in body


class TestClaudeDirIsGitignored:
    """Raven ruling, 2026-08-18: `.claude/` is an internal agent directory."""

    def test_gitignore_lists_the_directory(self):
        with open(os.path.join(REPO_ROOT, '.gitignore'), 'r',
                  encoding='utf-8') as handle:
            lines = [line.strip() for line in handle]
        assert '.claude/' in lines

    def test_git_actually_ignores_it(self):
        """The listing is a claim; `git check-ignore` is the fact.

        A pattern can be listed and still be overridden by a later negation, so
        ask git rather than reading the file twice.
        """
        result = subprocess.run(
            ['git', 'check-ignore', '-q', '.claude/'],
            cwd=REPO_ROOT, capture_output=True)
        if result.returncode == 128:
            pytest.skip('not a git checkout')
        assert result.returncode == 0, (
            'git does not ignore .claude/, but .claude/agents/forge.md tells '
            'the reader it does.')
