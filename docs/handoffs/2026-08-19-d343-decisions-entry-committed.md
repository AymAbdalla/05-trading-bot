# D-343 DECISIONS.md entry: ALREADY COMMITTED. No action taken, no commit made.

**Session:** `cody-d343-records-check`, 2026-08-19 ~09:5x EDT.
**Brief:** `docs/handoffs/from-raven/2026-08-19-d343-decisions-entry-commit.md` (Raven, 09:45 EDT).
**Outcome:** the brief's premise is STALE. The entry was committed 77 minutes before
the brief was written. Nothing was committed this session. Per the brief's own rule
("If anything is not as described here, stop and say so in the handoff instead of
forcing the commit"), this session stopped and reports instead.

## The finding

`docs/DECISIONS.md` is NOT dangling. The D-343 entry is in HEAD, committed by the
`cody-risk-wire` session itself as **`b55ea73`**, subject
`records: D-343 risk module wiring (both D-342 blockers resolved)`, trailer
`Agent-Id: cody-risk-wire`, +21 lines, `docs/DECISIONS.md` the ONLY file in it.

Measured, not quoted:

- `git status --porcelain --untracked-files=all` -> **completely empty**. No `M docs/DECISIONS.md`, no untracked anything.
- Working-tree `docs/DECISIONS.md` vs `git show HEAD:docs/DECISIONS.md` -> **byte-identical** (sha256 `b694e736b9d50b19` both sides).
- `git show HEAD:docs/DECISIONS.md` contains the exact heading the brief asked to confirm: `### D-343. Risk module wiring: PM gate cap duplication delegated...`
- HEAD is **`da07067`**, not `161b12f` and not `5864461`.

## Why Raven saw a dangling entry (the 108-second window)

The brief inferred the dangling state from the dashboard-theme session's handoff.
That observation was real but transient. Commit timestamps:

```
5864461  08:26:20  risk: wire model-free entry constraints into the PM loop (D-343)   <- 7 code files
5b1093b  08:26:33  dashboard: Light/Dark theme toggle + mobile CSS, all three viewers
b55ea73  08:28:08  records: D-343 risk module wiring (both D-342 blockers resolved)   <- DECISIONS.md
9f7d9fe  08:28:22  handoff: risk module wired - D-343 executed...
da07067  08:29:38  design: discovery pipeline architecture (Raven brief, design-only)
```

`cody-risk-wire` split its work into a code commit and a records commit, **108 seconds
apart**. The dashboard session's `git status` snapshot landed inside that gap, so it
correctly saw `M docs/DECISIONS.md` and correctly noted that `5864461`'s message says
"This entry." while the commit carries no `DECISIONS.md`. Both observations are true;
the conclusion drawn from them (that the record would be lost) was overtaken 95 seconds
later. **Convention 31 in the other direction: a handoff is a claim about a moment, and
the moment expires.** Any status observed across a sibling's multi-commit sequence needs
re-deriving before it is acted on, which is what this session did.

## What was deliberately NOT done

- **No commit.** There is nothing to commit. An empty or `--allow-empty` commit carrying
  the brief's subject line would have put a false claim in history (convention 31): it
  would assert this session landed the entry when `cody-risk-wire` did.
- **No `--no-verify`, no `SKIP_CONFLICT_CHECK=1`, no `--author`.** Never reached; no
  hook ran because no commit was attempted.
- **No `checkout`/`checkin` no-op round trip on `docs/DECISIONS.md`.** The brief offered
  it as the fallback if the pre-commit hook refused. Not needed, and running it would
  have rewritten the ledger owner of a file this session did not change.
- **`CLAUDE.md` NOT rewritten this session** - see the live-sibling note below. This is a
  deliberate deviation from the standing session-epilogue rule.
- No engine code touched, no loop restarted or signalled, no suite run, no harness run,
  no backtest. Docs-only session, and in the end not even that.

## LIVE SIBLING - `CLAUDE.md` is checked out by another session RIGHT NOW

Filtering `ps` on `comm == claude` (convention 25, not on argv) at session time:

```
18915  claude -p read docs/handoffs/from-raven/2026-08-19-d343-decisions-entry-commit.md ...   <- this session
15106  claude ... -p read docs/handoffs/from-raven/2026-08-19-discovery-design.md ...          <- LIVE sibling
```

`.venv/bin/python -m engine.concurrency who`:

```
CLAUDE.md            cody-discovery-design    19s ago
docs/DECISIONS.md    cody-038-ledger        2270s ago  [CHANGED SINCE CHECKOUT]
```

`cody-discovery-design` (PID 15106, author of `da07067`) holds **`CLAUDE.md`, checked
out 19 seconds before this session read the ledger** - it is actively rewriting the
wake-up file as its own epilogue. This session therefore did not touch `CLAUDE.md`,
even though the epilogue rule says to rewrite it and even though the file is materially
stale (it still claims HEAD `161b12f`, still says the risk module has no caller, still
lists open items 2 and 3 as unresolved). **Not clobbering a live sibling mid-write
outranks the epilogue rule** (conventions 21 and 26; the D-333 guard fails on its very
first condition here - the sibling's PID is present). The sibling's own rewrite should
land those corrections; if it does not, the next session should apply them.

The `docs/DECISIONS.md` checkout by `cody-038-ledger` flagged `CHANGED SINCE CHECKOUT`
is the known-stale pattern already recorded in the D-342 and D-343 notes: 038 committed
and exited long ago, and the file has been committed twice since. Advisory only, and
untouched by this session.

## `AGENT_ID` data point (open item 12)

Probed with python at session start: **`os.environ.get('AGENT_ID')` -> `None`, EMPTY**
on this gateway spawn. The tally now has readings in BOTH directions from the same spawn
path (`cody-forge-reasoner-c2` empty, `cody-risk-module` empty, this session empty;
against `cody-kalman-discuss` set, `cody-risk-wire` set, `cody-dash-theme` set).
**Open item 12 is still NOT settled** and this is one more data point, not a resolution.
**Probe it, never assume.** The `CONFLICT_CHECK_AGENT_ID` fallback was prepared but not
used, since no commit was made.

## For Raven

1. **Open item: none created.** The brief's task is closed as already-done. No follow-up
   commit is needed on `docs/DECISIONS.md`.
2. Worth noting for future briefs: `cody-risk-wire` splitting code and records into two
   commits 108 seconds apart is good practice (convention 34, commit by pathspec), but it
   means a sibling reading `git status` mid-sequence will report a dangling record that
   is not dangling. The cheap fix is for review findings sourced from another session's
   handoff to be re-derived against live `git` state before a brief is written - or for
   the brief to say "verify first, and if already committed, stop", which this one
   effectively did in its rules section and which is why nothing was forced.
3. `CLAUDE.md` staleness is with `cody-discovery-design`, not with this session.
