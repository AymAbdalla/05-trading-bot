# 037 rename + 27/28/29 numbering note closed - EXECUTED

**Session:** `cody-037-rename` (PID 88351, bare `claude -p` path)
**When:** 2026-08-19 ~04:12-04:25 EDT
**Directive:** `docs/handoffs/from-raven/2026-08-19-037-rename-and-2773-note.md`
**Commit:** `5795042`, pushed to `origin/main` (`a672eab..5795042`)

## Headline

**The suite is GREEN again.** The one failing test at HEAD was the duplicate-037
filename, and the rename fixed it. Full suite re-derived:

```
3,962 passed / 1 skipped / 0 FAILED   (371.71s, exit 0)
backtest/validate_harness.py          21/21, exit 0
```

Measured 04:14-04:21 EDT with no sibling agent alive. Tree byte-identical before,
mid-run (t+90s) and after: `docs/CONVENTIONS.md` `1aaa0b7c39c2`,
`opportunity-report-2026-08-19.md` `a96aaa591372` at all three samples, HEAD
`a672eab` unchanged throughout.

Sanity on the number: the previous session's red run was 3,961 passed + 1 failed
= 3,962 collected. Now 3,962 passed. Nothing was added or dropped, one test
flipped red to green. That is the whole delta.

## Task 0: guard (D-333) - CLEARED

1. `ps aux | grep "claude -p"` showed PID 88351 (me) and the tmux wrapper 37068.
   The only `claude -p` child of 37068 is 88351. Its other child, 71442, is the
   env-B shadow loop daemon, not an agent session. No sibling.
2. `git status --porcelain` clean.
3. Two `git rev-parse HEAD` reads 10s apart, both `a672eab`.

Confirmed the target test was RED before touching anything:
`duplicate proposal numbers on disk: {'037': 2}`.

## Task 1: the rename

```
strategies/proposals/037-opportunity-report.md
  -> strategies/proposals/opportunity-report-2026-08-19.md
```

`git mv`, recorded as **R100** (pure rename, content byte-identical - the diff
confirms zero content change). `ls strategies/proposals/ | grep 037` now returns
exactly one file: `037-pm-complement-no-arbitrage-taker.md`, the real proposal
037, untouched. Matches the `external-signals-2026-08-19.md` precedent.

## Task 2: re-derivation

| check | result |
|---|---|
| pinned duplicate-number test alone | PASS |
| `tests/test_forge_reasoner.py` module | 47 passed |
| full suite (`--ignore=tests/test_dashboard_charts.py`) | **3,962 passed / 1 skipped / 0 failed**, 371.71s, exit 0 |
| `backtest/validate_harness.py` | **21/21 passed**, exit 0, cross-harness AGREE |

## Task 3: CONVENTIONS.md ruling recorded

Two changes to `docs/CONVENTIONS.md`, nothing else (diff reviewed line by line):

1. Heading `## Numbering note for 27, 28 and 29 (open, needs a Raven ruling)`
   -> `## Numbering note for 27, 28 and 29 (closed by Raven ruling, 2026-08-19)`
2. Appended, verbatim as supplied:
   `Raven ruling, 2026-08-19: numbering stands as assigned. D-292 was applied correctly (next free number, nothing overwritten), nothing cites 27-29 by number, and the pins already assert this state. Renumbering would churn the doc and tests for zero gain.`

No renumber. No pin touched. No convention text touched. No table touched.
Applied through `engine.concurrency.safe_edit` with an idempotent `edit_fn`.

## Task 4: commit hygiene

Staged by explicit pathspec, never `git add -A`. Commit carried all three paths
(`docs/CONVENTIONS.md` + the rename's delete/add pair). Both hooks ran naturally
and passed:

- pre-commit: `total=1 verified=1 MISMATCH=0 FOREIGN-OWNED=0`, own-work.
- commit-msg (D-335): `Agent-Id: cody-037-rename` matched the resolved identity.

No `--no-verify`, no `SKIP_CONFLICT_CHECK=1`, no `--author`, no bypass of any
kind.

## Correction for Raven: AGENT_ID was NOT exported on this spawn path

The directive said "the spawn wrapper exports AGENT_ID on this path" and CLAUDE.md
open item 11 recorded it as CLOSED, measured `cody-d337-ratify` at 03:52. **I
measured it empty.** `os.environ.get('AGENT_ID')` returned `None`, and so did
`CONFLICT_CHECK_AGENT_ID`. (`echo $AGENT_ID` is refused by the permission layer;
this is the python read.)

The fallback in CLAUDE.md worked - `CONFLICT_CHECK_AGENT_ID` passed through a
python `subprocess.run` env, no shell prefix, no bypass flag - so nothing was
blocked. But **open item 11 should be REOPENED, or at least downgraded from
CLOSED to intermittent.** Two sessions, ~20 minutes apart, on nominally the same
bare `claude -p` path, read different values. One of the two measurements is
wrong about what path it was on, or the export is not deterministic. Either way
"the wrapper exports it" is not a fact you can build a directive on yet.

Also worth noting, from the hook's own output (D-337(2)'s point, restated by the
tool itself): `declared via CONFLICT_CHECK_AGENT_ID; UNVERIFIED -- the hook
cannot check that a declaration is true`. The trailer warning stands.

## What I did NOT touch

Real proposal `037-pm-complement-no-arbitrage-taker.md` (including its stale
`forge_refusal:` field - that is 037's own session's job, open item 3), every
other file in `strategies/proposals/`, `agents/`, `engine/` (no source edits;
`engine.concurrency` used as a tool only), `strategies/polymarket/`, `scripts/`,
`tests/` (nothing edited, only run), `config.yaml`, the registry,
`run_polymarket_shadow.sh`, `docs/DECISIONS.md`, any other CONVENTIONS.md text,
any handoff but this one. No process was started, stopped or restarted - the
shadow loops (71360/71394/71442), 48637 and 37578 were left alone.

## For Raven

1. **Open item 9 (duplicate 037 / red suite) is CLOSED.** It was the only urgent
   item on the list. HEAD `5795042` is green.
2. **Open item 8 (27/28/29 numbering) is CLOSED** by your ruling, now in the doc
   itself and not only in a handoff.
3. **Open item 11 (AGENT_ID on the spawn path) should be REOPENED** - see the
   correction above. This is the one thing in the directive's stated world model
   that did not match measurement.
4. Untouched and still open: 037's stale `forge_refusal:` (item 3), env-B
   whitelist corrections at next natural restart (item 4), D-323 restatement
   (item 5), the forge brief (item 6), proposal 029's gate (item 7), and the
   03:28 UNDECLARED restart (item 2).
5. The 037 gate result itself is unchanged and still NOT_TESTED - this session
   moved a filename, not a finding. The complement-reflection caveat (26.6 min of
   keyed tape, top-of-book only, re-derive over >= 24h) is untouched and still
   yours.
