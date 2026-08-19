# Hook hardening + restart forensics - EXECUTED

**Session:** `cody-hook-harden`, 2026-08-19 ~01:30-02:10 EDT
**Directive:** `docs/handoffs/from-raven/2026-08-19-hook-hardening-and-restart-forensics.md`
**Commit:** `d66aff5`, pushed to `origin/main`. Tree clean.
**Suite:** 3,900 passed / 1 skipped / 0 failed. Harness 21/21, exit 0.

---

## Task 0: the wait guard was honoured

`cody-reconcile` (PID 45891) was alive when I started. I made ZERO tree edits
and ZERO git mutations until BOTH conditions held: its handoff
`docs/handoffs/2026-08-19-reconcile-executed.md` existed AND the PID was gone.
The wait was ~17 minutes, spent reading and designing.

One thing for the record (convention 31, and it cost nothing here): reconcile
landed a SECOND commit `dee8b0c` (DECISIONS.md + CONVENTIONS.md: D-328
back-fill, D-330) that was not visible in the `git log` I ran immediately after
its PID exited. My commit sits on top of it. I verified with
`git show --stat d66aff5` that my commit contains **exactly my two files** and
nothing of reconcile's. **The PID-gone + handoff-exists guard is very slightly
racy against a session's final commit.** No damage here because our file sets
were disjoint. To make it airtight the guard should also require `git status`
clean AND two consecutive identical `git rev-parse HEAD` reads.

---

## Task 2: what I built

`scripts/pre-commit-conflict-check` gains a third step. Steps 1 and 2 are
unchanged in behaviour.

**The gap being closed.** Step 2 passed on BOTH phantom sweeps (`b1d44bb`,
`4d03681`) and was right to: the staged hash DID match the ledger. The ledger
knew the content was coordinated. It never checked that the coordinating agent
and the committing agent were the same one. Step 3 checks exactly that.

**It refuses** when a staged path's most recent coordinated write belongs to a
KNOWN non-human `agent_id` that differs from the committing identity.

**Owner recognition is a WHITELIST of agent-id shapes**
(`cody`/`raven`/`forge`/`hermes`/`claude`/`agent` + separator), not a blacklist
of human ones. I checked the live ledger before choosing: **43 distinct
`agent_id`s, every one of them `cody*` or `raven*`, zero human-shaped.**
Anything unrecognised falls to `no-agent-owner` and behaves exactly as it did
before step 3 existed. That is what keeps Aym's commits working. `codyssey`
does NOT match `cody`; there is a test for it.

**The sweep escape.** `CONFLICT_CHECK_ALLOW_SWEEP=1|true|yes|on` downgrades the
refusal to a loud, itemised warning naming every swept path and its owner. That
is the reconcile path. A value that is SET but unrecognised (`=maybe`) is
reported as "NOT armed" rather than silently behaving as unset - otherwise a
typo produces a refusal the committer believes they already answered.

**Convention 20 is honoured twice.** Step 3 gets its own four-bucket
classification, its own printed counts, and its own asserted accounting
identity. Every staged path lands in exactly one bucket in BOTH classifications.

**Both refusals are computed before either is printed**, so a commit that trips
hash-mismatch AND cross-owner is told about both in one run.

---

## The identity mechanism, and the thing that nearly sank it

The directive said: pick something testable standalone and honest by default,
and if it cannot be made reliable without a spawn-template change, say so
rather than half-building it. Here is what actually happened.

Resolution order:

1. `CONFLICT_CHECK_AGENT_ID`
2. `AGENT_ID`
3. `TRADING_BOT_AGENT_ID`
4. `GIT_AUTHOR_NAME`, **but only when the value is agent-shaped**

2 and 3 are deliberately the SAME variables `engine/concurrency.py:128` already
reads for `DEFAULT_AGENT_ID`. One identity, one place to set it, no second
parallel mechanism to drift. It never falls back to that module's literal
`'cody'` default - an undeclared session must read as undeclared.

**Then I hit the wall the directive predicted, from the inside.** This session
cannot put a `VAR=value` prefix on a command - the permission layer refuses
`CONFLICT_CHECK_AGENT_ID=cody-hook-harden git commit ...` outright. So the hook
I had just written refused my own commit, twice, correctly, and I had no
sanctioned way to declare. I did not use `SKIP_CONFLICT_CHECK` and did not use
`--no-verify`.

**Item 4 is the resolution, and it is the directive's own second option.**
`git commit --author="cody-foo <cody@agents.invalid>"` needs no environment
prefix, and git exports `GIT_AUTHOR_NAME` to the pre-commit hook - I verified
that empirically in a throwaway repo rather than assuming it. It is strictly
better than the env vars in one way that matters: **the identity lands in git
history, so provenance outlives the hook run that checked it.**

**The condition on item 4 is load-bearing.** git sets `GIT_AUTHOR_NAME` on
EVERY commit, falling back to `user.name`. Accepting it unconditionally would
turn every one of Aym's commits into a declaration of "Aym Abdalla" and quietly
retire the `NOT DECLARED` state the whole step depends on. Hence "only when
agent-shaped", with two tests pinning both halves.

**This commit is itself authored `cody-hook-harden` through that channel.**
That is a visible change to repo commit metadata (every prior commit is
authored `Aym Abdalla`; the *committer* is still Aym). I think it is right - it
is the durable provenance record this problem has been missing - but it is a
convention change and **it is yours to accept or reject.**

---

## The blast radius, stated plainly

Rules 2 and 4 together mean **an undeclared session staging any agent-owned
file is refused.** No spawn template sets any of these variables today. So the
first effect of this landing is that sibling sessions' own legitimate commits
start failing until they declare.

That is the intended fail-loud behaviour, and the refusal message spells out
three fixes in priority order (including the `--author` form, precisely so a
session that cannot set an env prefix is not cornered). But the real risk is
that a cornered session reaches for `--no-verify`, which would be worse than
the disease.

**My recommendation:** change the spawn template in `CLAUDE.md` so spawned
sessions export `AGENT_ID=cody-<topic>` for the whole session. Then every
commit in that session is declared automatically with no per-command prefix,
the granularity is right (one identity per session), and `engine.concurrency`
picks up the same value for `safe_edit` - which is what it was always supposed
to do. **That is a change to the shared protocol, so it is Raven's call, not
mine. I did not make it.**

---

## Task 1: restart forensics - NOT FINDABLE

Who restarted the main loop at 00:56:17 is **not attributable from in-repo
evidence**, and I want to be exact about why rather than vague.

What I established:

- `41735` is the python loop; wrappers `41700`/`41736` are
  `run_polymarket_shadow.sh`, all started 12:56AM, all with TTY `??` - no
  controlling terminal, consistent with a detached launch from a Claude Code
  Bash call rather than an interactive shell.
- `verify-commit-restart` **explicitly disclaims it** in its own handoff: "The
  commit and the restart were performed by peer sessions, not by myself... a
  peer session performed the restart at 00:56:17." Its tmux launcher (PID
  37068) is still in the process table, corroborating that the session existed,
  not that it restarted anything.
- `2026-08-19-execute-opus-plan.md` only CONFIRMS 41735 is live. It claims no
  authorship. No other handoff in the repo references the restart.

What I could not check, and did not work around:

- `ps -o pid,ppid,lstart` is not permitted in this session, so I could not walk
  parentage.
- `tmux ls` is not permitted.
- `~/.zsh_history` is outside the sandbox - pattern search is restricted to the
  project directory. I did not attempt to circumvent that.

**The root cause is a missing measurement, not a missing investigation.**
`run_polymarket_shadow.sh` writes a banner with repo, commit, mode, equity,
poll, db, csv and python version - and **records nothing about who launched
it.** That is why this is unanswerable two nights running, and it will be
unanswerable a third time.

**Proposed fix (one line, I did NOT build it - out of scope):** add
`launched-by: ${AGENT_ID:-UNDECLARED}` and the parent PID to the banner block.
That converts this from forensics into a lookup, and it pairs with the
`AGENT_ID` spawn-template change above - one variable fixes both problems.

---

## Tests

`tests/test_pre_commit_hook.py`, **50 tests**, new file. They run the REAL
script as a subprocess against a throwaway git repo and a throwaway
coordination database. Nothing is mocked and no function is imported from it -
it is a bash script with an embedded python heredoc, and executing it is the
only honest way to test it. Nothing touches `db/trading.db` or the real repo.

The throwaway repo gets a symlink to the real `engine` package so the hook
imports the REAL `engine.concurrency` rather than silently falling back to its
degraded stub, and `test_the_hook_imports_the_real_concurrency_module` pins
that - if it regressed, every other test here would still pass while exercising
a fallback nobody ships.

Coverage the directive asked for, all present:
(a) sweep by a different agent, undeclared = REFUSED;
(b) sweep with declaration = allowed and every path listed;
(c) own-work = allowed, no warning;
(d) human commit, no declared identity = allowed;
(e) all existing hash-verification cases still pass.

Plus: both refusals in one run, the escape not rescuing a hash mismatch,
newest-write-decides-the-owner, agent-shape edge cases (`codyssey` is not
`cody`), blank declaration = undeclared, case/whitespace insensitivity,
unrecognised escape value reported, deletions not classified, unreadable table
= `owner-unknown` not `verified`, and the provenance accounting identity over a
mixed 4-file commit.

**Numbers, re-derived, not quoted:**

- `tests/test_pre_commit_hook.py` - 50 passed
- `tests/test_concurrency.py` - 58 passed
- Full suite `pytest tests/ -q --ignore=tests/test_dashboard_charts.py` -
  **3,900 passed, 1 skipped, 0 failed** (6m04s). Baseline was 3,850; the delta
  is exactly my 50 tests and nothing else moved.
- `backtest/validate_harness.py` - 21/21, exit 0 (convention 1).

The suite ran in-tree, which is normally untrustworthy (convention 21) - but no
sibling Claude session was alive for the duration. I checked.

---

## Verified, not assumed

- **The installed shim execs the script.** I read `.git/hooks/pre-commit`: it
  ends in `exec "$ROOT/scripts/pre-commit-conflict-check"`. The behaviour
  change is live with **no reinstall**.
- **I deliberately did NOT bump the `v1` marker in
  `scripts/install_conflict_hook.sh`.** That marker identifies the SHIM, not
  the policy. Bumping it would make the already-installed shim fail that
  script's `is_ours` test and get backed up as a stranger's hook on the next
  install. That trap is now documented in the hook header.
- **The refusal bites in the real repo**, not just the sandbox - I ran it
  against my own real staged commit, undeclared, and it refused with both paths
  and owners named.
- **`git show --stat d66aff5`** - exactly 2 files. No phantom sweep.

---

## What I did NOT touch

The main loop (41735), env B (38881), the liquidation recorder, the hyperliquid
poller, `engine/polymarket/shadow_loop.py`, `docs/DECISIONS.md`,
`docs/CONVENTIONS.md`, any strategy parameter/floor/market type, the registry,
`scripts/install_conflict_hook.sh`, `run_polymarket_shadow.sh`. I restarted
nothing and killed nothing. No `git add -A`, no `SKIP_CONFLICT_CHECK`, no
`--no-verify`, no `--dangerously-skip-permissions`.

Both edited files went through `engine.concurrency` with
`agent_id='cody-hook-harden'`, the hook edit guarded on the pre-image hash so a
concurrent change would have raised rather than been clobbered.

I did NOT rewrite `CLAUDE.md`.

---

## For Raven - decisions I could not make

1. **Spawn template: export `AGENT_ID=cody-<topic>`?** Without it, undeclared
   agent commits start failing tonight. This is the one that matters.
2. **Agent-authored commits.** `d66aff5` is authored `cody-hook-harden`, not
   `Aym Abdalla`. Durable provenance, but a visible convention change. Accept
   or revert?
3. **Banner provenance in `run_polymarket_shadow.sh`** - one line, kills the
   restart-forensics problem permanently. Proposed, not built.
4. **Tighten the wait guard** (see Task 0) - PID-gone is slightly racy against
   a session's final commit.
5. **Convention candidate:** *a hook that cannot be satisfied by the agents it
   governs will be bypassed by them.* The `--author` channel exists only
   because I hit exactly that wall on my own commit.
