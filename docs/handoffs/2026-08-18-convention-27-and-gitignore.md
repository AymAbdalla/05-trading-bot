# Handoff: convention 27 and .claude/ gitignore

**From:** Cody, 2026-08-18
**Acting on:** `docs/handoffs/from-raven/2026-08-18-convention-27-and-gitignore.md`
**Status:** both actionable items done. One thing needs a Raven ruling: the
convention NUMBER.

## Headline: 27 was already taken. Twice. In one day.

Raven asked for the `getsource` rule to be convention 27. When I went to write
it, `CLAUDE.md` had a convention 27 already, and it was **not** the one that was
there when this session started:

| convention | where I found it | what happened |
|---|---|---|
| "Half a resolution is not a resolution" | `CLAUDE.md` as of 07:25 (the snapshot this session booted with) | gone from the live file. Clobbered by a later session. |
| "Verify the DIRECTION of a gate before changing its threshold" | `CLAUDE.md` on disk right now | live, still 27 |
| "`getsource` defeats the import snapshot" | Raven's ruling, this session | needed a number |

Three conventions, one number. D-292 already ruled on exactly this shape: take
the next free number, never overwrite. So none of the three got dropped:

- **27** = the gate-direction rule (kept, because it is what a live file asserts
  right now and I did not want a reader of `CLAUDE.md` contradicted)
- **28** = "Half a resolution is not a resolution" (recovered from the clobber)
- **29** = Raven's `getsource` rule, wording verbatim from the instruction file

**Nothing in the repo cites conventions 27 to 29 by number** (I grepped `docs/`,
`tests/`, `engine/`, `strategies/`; the only hits are the two handoff lines that
proposed the getsource one). So renumbering is still free. If you want getsource
at 27 and the other two pushed down, it is a one-line change in the doc plus the
pins in the test. **Your call, and I did not make it for you.**

## The conventions moved to a tracked file

Raven's instruction said "write it into `CONVENTIONS.md` (or wherever conventions
live)". There was no `CONVENTIONS.md`. They lived only in `CLAUDE.md`, which is
gitignored, absent from a clean checkout, and rewritten wholesale every session.

That is why 27 got clobbered, and it is also why the test Raven asked for could
not have been written against `CLAUDE.md`: a test that reads an untracked file
is red on any clean checkout.

So I created **`docs/CONVENTIONS.md`**, tracked, declared canonical, carrying
conventions 1 to 29. Conventions 1 to 26 are copied verbatim from the current
`CLAUDE.md`. I did not touch `CLAUDE.md` (Raven said not to, and nine sessions
are in it).

**This does create two lists.** The doc states that it wins over the `CLAUDE.md`
mirror and that the mirror is stale when they disagree. That is a convention 23
compromise I made deliberately rather than silently: the alternative was leaving
the canonical list in a file that gets rewritten by whoever finishes last. Flag
it if you disagree; the next epilogue that rewrites `CLAUDE.md` should shorten
its list to a pointer at `docs/CONVENTIONS.md`.

## Files changed

| file | what |
|---|---|
| `docs/CONVENTIONS.md` | NEW. Canonical conventions 1 to 29, plus a numbering note explaining the 27 collision. |
| `tests/test_conventions_doc.py` | NEW. 10 tests. |
| `.gitignore` | `.claude/` added, with a comment naming the ruling. Written via `engine.concurrency.safe_edit` (convention 26), with an idempotent `edit_fn` so a retry against another agent's content cannot double-append. |

## What the test actually pins

Not just "convention 29 exists". Three things:

1. **Numbering is 1..N, contiguous, no duplicates.** A duplicate number is the
   precise failure that lost a convention today, so it is red rather than
   tolerated. A gap means something was deleted instead of superseded.
2. **The three contested conventions keep their meanings.** 29 is pinned on
   every clause that carries weight in Raven's wording: the mechanism
   (`re-reads the file from disk at call time`), the link to convention 13, the
   diagnostic (`stat`, `mtime`, `collision, not a bug`), and the fix
   (`imported attributes, not source text`). A test that only checked the title
   would pass on a version that dropped the actionable half.
3. **`.claude/` is genuinely ignored**, asked of `git check-ignore` and not just
   read out of the file. `.claude/agents/forge.md` asserts this in a comment,
   and convention 22 says a comment is not a wiring test.

There were no pre-existing convention tests to match a pattern to. This is the
first one.

## Verification

```
tests/test_conventions_doc.py .......... 10 passed in 0.08s
git check-ignore -v .claude/  ->  .gitignore:51:.claude/
2452 tests collected repo-wide, clean (no import breakage)
```

`.claude/` has dropped out of `git status` untracked.

**I did not run the full suite.** Nothing I touched is production code or an
import target: one new doc, one new test file, one `.gitignore` line. Collection
is clean repo-wide. Say the word if you want the 313s run anyway. Note the
collected count is **2452**, not the 2,289 in `CLAUDE.md` - other sessions have
been adding tests all day.

## What I did NOT do, as instructed

- Did not attempt the forge `cp`. Escalated to Aym, per Raven.
- Did not touch `CLAUDE.md`, strategy logic, `STRIKE_PROXY_NOISE_FLOOR_BPS`, the
  poll interval, any running process, the WebSocket, or `agents/forge/forge.agent.md`.
- Did not add a D-number for any of this. D-301 was already recorded by Raven.
  If the 27/28/29 assignment needs to be durable it wants a D-number, and that
  is yours to write.

## For Raven

1. **Rule on the numbering.** 27/28/29 as assigned, or push the other two down
   and give getsource 27 as originally intended? Cheap either way, today.
2. **Ratify or reject `docs/CONVENTIONS.md` as canonical.** If yes, the next
   session that rewrites `CLAUDE.md` should replace its convention list with a
   pointer, and that instruction belongs in the epilogue rule.
3. "Half a resolution is not a resolution" was **lost** by a session rewrite and
   I recovered it from this session's boot snapshot. Worth knowing that the
   epilogue "REWRITE CLAUDE.md" rule is actively destroying content at this
   concurrency level. That is the fourth session in a row to say so.
