# Working conventions

**Status:** canonical. This file is git-tracked and is the source of truth for
convention numbering.

Until 2026-08-18 the numbered conventions lived only in `CLAUDE.md`, which is
gitignored and is rewritten wholesale at the end of every session. With several
concurrent Cody sessions that turned out to be a clobber surface: **convention
27 was written twice today with two different meanings**, and the first one was
silently lost when the second session rewrote the file. D-292 already ruled that
silently overwriting an existing convention is the worse failure. A rewritten
untracked file cannot enforce that; a tracked file with a test can.

`CLAUDE.md` may still carry a short mirror of this list for wake-up context. If
the two disagree, **this file wins** and the mirror is stale. Do not renumber
here to match a mirror.

Adding a convention: append the next free number, never reuse one. If you find
your number taken, take the next free one and say so in your handoff.

`tests/test_conventions_doc.py` pins the numbering (contiguous, no duplicates)
and the wording of the conventions that have been contested.

---

1. No result is durable unless `backtest/validate_harness.py` exits 0.
2. Cite `distinct_findings`, never raw pass counts.
3. Verify a strategy FIRES on real data before interpreting results.
4. Conditions predicted before testing, never discovered by scanning.
5. Estimate gross edge in bps first; under 30bps = dead on arrival.
6. Every proposal states a kill condition with a number and a named harness.
7. A FAIL on 200k trades is a verdict; on 1,700 a shrug. A PASS on 87 is also a shrug.
8. Every entry needs a stop strictly below entry. A losing binary share is 0.00.
9. Handoff to `docs/handoffs/` after every session. Not optional.
10. Decisions go in DECISIONS.md with a D-number, who decided, why, where.
11. NOT_TESTED means "could not run," never "ran and found nothing."
12. A cost RATE can legitimately be `inf` when an instrument is unaffordable.
13. Edits during a long run do not reach it; Python snapshots source at import.
14. Run `env -u PYTHONPATH python3` or `.venv/bin/python`. Hermes leaks its 3.11
    venv onto PYTHONPATH and numpy then fails like a broken install.
15. A number written into a decision BEFORE the run is an estimate. Correct it.
16. **Never `git add -A`.** Stage by explicit path.
17. **A hardcoded threshold is an assumption with an expiry date.** If a metric
    improves after a step that only LOOSENED a filter, suspect the baseline.
18. Before `pgrep -f <pattern>`, check the waiting shell's own command line.
19. **`json.loads` is NOT strict.** Write with `allow_nan=False`.
20. **A silent `continue` is a missing number.** Every skip counted AND
    categorised; two drop causes never share one counter.
21. **This working directory is SHARED.** Check `ps aux` and `git status` first.
22. **A claim in a docstring is not a wiring test.**
23. **A fix at one site is not a fix.**
24. **A cited D-number is not a decision.** Check it exists in DECISIONS.md.
25. **A PID in a doc is a claim, not a fact.** Confirm with `ps -p <pid>`.
26. **Hash-before-write.** Use `engine.concurrency.checkout()` / `checkin()` /
    `safe_edit(path, fn)` before writing a file another agent might be editing.
    `env -u PYTHONPATH python3 -m engine.concurrency who` lists open checkouts.
    It DETECTS but cannot PREVENT a writer that bypasses it.
27. **Verify the DIRECTION of a gate before changing its threshold.** Read the
    comparison operator. Then check what the change would admit, against real
    logged rows, BEFORE editing.
28. **Half a resolution is not a resolution.** When a static check can only
    partially follow something, report the whole site as unfollowed. Partial
    coverage that LOOKS complete is worse than none.
29. **`inspect.getsource()` defeats the import snapshot.** `inspect.getsource()`
    re-reads the file from disk at call time, defeating the import-time source
    snapshot that convention 13 relies on. A test that calls `getsource` on a
    module another session may edit will fail spuriously when the edit lands
    during the run. Before believing a `getsource` failure, `stat` the file it
    reads and compare the mtime to the test run window. If the mtime falls
    inside the window, the failure is a collision, not a bug. Tests that use
    `getsource` should assert over imported attributes, not source text,
    wherever the attribute carries the same information.

---

## Numbering note for 27, 28 and 29 (open, needs a Raven ruling)

Raven's instruction file dated 2026-08-18 asked for the `getsource` convention
to be **27**. By the time it was applied, 27 had already been taken twice by
other sessions on the same day:

| convention | first seen | fate |
|---|---|---|
| "Half a resolution is not a resolution" | `CLAUDE.md` as of 07:25 | clobbered, recovered here as **28** |
| "Verify the DIRECTION of a gate" | `CLAUDE.md` after 07:25, still live | kept at **27** |
| "`getsource` defeats the import snapshot" | Raven ruling, this session | assigned **29** |

Per D-292 (do not overwrite an existing convention, take the next free number),
none of the three was overwritten. 27 was left on the meaning a live file
currently asserts so that a reader of `CLAUDE.md` today is not contradicted;
the other two took the next free numbers in the order they were written.

Nothing anywhere in the repo cites conventions 27 to 29 by number, so
renumbering is still free if Raven prefers a different order. Say the word and
it is a one-line change here plus the pins in `tests/test_conventions_doc.py`.
