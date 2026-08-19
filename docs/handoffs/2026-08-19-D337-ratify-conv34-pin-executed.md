# D-337 ratification recorded + convention 34 pinned

**Session:** `cody-d337-ratify` (PID 81686), spawned 03:51 EDT, 2026-08-19.
**Directive:** `docs/handoffs/from-raven/2026-08-19-D337-ratify-conv34-pin.md`,
executed in full, in order.
**Start HEAD:** `c49fca2`.  **End HEAD:** `71c6500`, pushed.

## Commits (two, separate, both by pathspec, both pushed)

| commit | file | diff |
|---|---|---|
| `868282d` | `tests/test_conventions_doc.py` | +51, -0 |
| `71c6500` | `docs/DECISIONS.md` | +2, -0 |

`git show --stat` re-run on both after pushing: one file each, no sweep
(convention 31). No `git add -A`, no `--no-verify`, no `SKIP_CONFLICT_CHECK`,
no `--author`, no bypass flag of any kind.

## Task 1: convention 34 pinned

`docs/CONVENTIONS.md` was re-read first and the pinned phrases were taken from
the current text, not from the brief. New class `TestPathspecCommitConvention`,
three tests, following the `TestCommitMessageConvention` /
`TestUnsatisfiableHookConvention` pattern:

- `test_34_is_the_pathspec_commit_rule` - the mechanism
  (`Commit your own paths out of a shared index with a pathspec`,
  `git commit -- <paths>`), the ban (`Never \`git add -A\``), the shared-index
  reason (`another session's files may already be staged`,
  `leaves another session's staged entries untouched`), the near-miss repair
  that is itself a mutation of their index (`git restore --staged`), and the
  references to conventions 16 and 21.
- `test_34_names_the_sweeps_it_was_earned_by` - `b1d44bb`, `4d03681`,
  `26555f2`, and `D-337`.
- `test_34_ships_the_commands_not_just_the_rule` - `git add -- <your path>`,
  the `Agent-Id: cody-<topic>` trailer, `git status --porcelain`, `FIRST column`.

**The pins were verified to bite**, not just to pass. With those phrases
redacted from convention 34's body in memory, all three tests fail; against the
real doc, all three pass. A pin that cannot fail is not a pin.

`tests/test_conventions_doc.py` alone: **16 passed** (was 13).

## Task 2: Raven's ratification recorded

WAIT guard (D-333) was honoured, and it did NOT pass on the first check.

- 03:52 - sibling `cody-036-037` (PID 79317) alive. Guard FAILED. Waited.
- 03:58:43 - PID 79317 exited. Re-checked all three conditions:
  - `ps aux | grep "claude -p"` -> only PID 81686 (me) plus the known lingering
    tmux wrapper 37068 with no claude under it, ignored per the directive.
  - `git status --porcelain` -> empty.
  - `git rev-parse HEAD` twice, 8s apart -> `6a44d8f` both times.
- Guard CLEARED. HEAD had moved `c49fca2` -> `6a44d8f` while I waited; the
  sibling landed `b7e6417`, `55b3259`, `8f783a8`, `6a44d8f`.

`docs/DECISIONS.md` was **re-read after the guard cleared**, because the sibling
had appended its own e756af3 trailer-forgery note under D-337 in `55b3259`.
My note was appended AFTER that one. `git diff --numstat` before staging:
**2 insertions, 0 deletions**. No existing text edited, including both trailing
notes.

Content: the null-hash extension is ACCEPTED not struck; a row recording no
`new_hash` carries no content, cannot be evidence of authoring content, can only
hand ownership backwards and never forwards; stays pinned by
`test_rows_recording_no_hash_are_ownership_neutral_too`.

The note also records, explicitly labelled an observation and not a ruling, that
**(c)'s corner is measured gone**: `AGENT_ID` read `cody-d337-ratify` in this
session's environment at 03:52 on the bare `claude -p` path, so the spawn
template fix is real and confirmed from inside a governed session. Both commits
above were a plain `git commit -- <paths>` straight through both hooks - no
python subprocess carrying `CONFLICT_CHECK_AGENT_ID`, which is what
`cody-ledger-rule` had to do six hours earlier. That closes the D-337 handoff's
open item on the spawn template, and CLAUDE.md's open item 10.

## Numbers, re-derived. THE SUITE IS RED, and not by me.

Measured 03:59-04:06 EDT, in-tree, with **no sibling `claude -p` alive** - the
first quiet-tree measurement of the night. `git status --porcelain` was
`M docs/DECISIONS.md` and nothing else before and after, HEAD `6a44d8f` both
times, and the DECISIONS.md sha256 was byte-identical across the whole run
(`ac893d1da2f3`). The daemons were live, as always.

- `backtest/validate_harness.py`: **21/21, exit 0** (exit code captured, not
  inferred from the log). Convention 1 is GREEN.
- Full suite: **3,961 passed / 1 skipped / 1 FAILED**, 370.42s.

**The failure:** `tests/test_forge_reasoner.py::test_the_live_proposals_dir_has_no_duplicate_numbers`
-> `duplicate proposal numbers on disk: {'037': 2}`.

`strategies/proposals/` now holds BOTH `037-opportunity-report.md` and
`037-pm-complement-no-arbitrage-taker.md`. The second is the real proposal 037
(added in `1a36c7e`). The first was added by sibling `cody-036-037` in `8f783a8`
at ~03:56 and takes a number that was already used. That test exists precisely
to catch this - it is described in its own docstring as "the regression itself,
pinned against the real directory" - so it is doing its job, and the regression
is real, not a flaky test.

**I did not fix it.** `strategies/proposals/037-*` and the forge tests are both
on this directive's explicit do-not-touch list, and the file belongs to the
036/037 lane. The red predates my `71c6500`, and a two-line append to
`docs/DECISIONS.md` cannot reach the proposals directory. I committed into it
anyway rather than leave a ratification record sitting uncommitted in a shared
working tree, and said so in the commit message. Flagging rather than fixing is
the call I made; if that was wrong, the fix is one `git mv`.

**Suggested fix, for the 036/037 lane or Raven:** the file is an opportunity
report, not a proposal, so it should not be carrying a proposal number at all.
Rename to something outside the `NNN-` namespace, e.g.
`opportunity-report-2026-08-19.md`, matching the existing
`external-signals-2026-08-19.md`. Do NOT renumber the real 037 - it is cited by
number in DECISIONS.md, in CLAUDE.md and in `8f783a8`'s own message.

## What I did NOT touch

Proposal 036/037 files and every other file in `strategies/proposals/`, the
forge tests, `agents/forge.py`, `agents/forge_candidates.py`,
`agents/forge/forge.agent.md`, `strategies/polymarket/weather_arb.py`,
`engine/polymarket/shadow_loop.py`, `scripts/pre-commit-conflict-check`,
`scripts/install_conflict_hook.sh`, `tests/test_pre_commit_hook.py`,
`config.yaml`, the registry, `run_polymarket_shadow.sh`.

**No daemon was touched, started, stopped or restarted.** Main shadow loop, env
B, liquidation recorder and hyperliquid poller all ran untouched throughout,
including through the 370s suite. Nothing here is a restart candidate: both
changes are a test file and a doc, and convention 13 means neither reaches a
running process regardless.

Shadow only. No live trading. No backtesting run beyond the validity harness.

## For Raven

1. **The red suite is the one thing needing action**, and it is not mine to
   take. See the rename above.
2. D-337's null-hash extension is now ratified IN THE RECORD, not just in a
   handoff. CLAUDE.md open item 9 can be struck.
3. CLAUDE.md open item 10 (spawn template does not export `AGENT_ID`) is
   measured CLOSED on the bare `claude -p` path. Item 11 (convention 34 has no
   pin) is CLOSED by `868282d`.
4. Still open and untouched by this session: the trailer-forgery question
   (item 1 - D-337(2) accepts it as a cost, `cody-036-037` recorded the worked
   counter-example, but nothing has replaced the trailer with real provenance),
   and who restarted the loops at 03:28 (item 2).
5. The 27/28/29 renumbering note in `docs/CONVENTIONS.md` is still yours to
   rule on. `test_29_is_not_numbered_27` will need moving if you renumber, which
   is the point of it.
