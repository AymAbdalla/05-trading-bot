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
    This covers REVIEW files as much as any other doc: a review that names a
    PID must carry the timestamp it was verified at, and a stale citation in an
    older review never overrides a live `ps` check.
    A quotation of another document is a claim about a version (D-330):
    handoffs are mutable - quote with the timestamp you read, or re-read
    before relying on it.
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
30. **The shadow log is not the system of record for dispositions.** The
    `signals` table is. The stats line (`shadow_loop.flush_stats`) logs the crypto
    identity's counter only; space dispositions live in `space.counts` and the
    DB. Grepping stdout for a space skip reason returns 0 by construction.
    Query the DB before calling anything zero.

31. **A commit message is a claim, not a fact.** Verify the diff contains what
    the message asserts before you trust it or repeat it. Two consecutive
    commits on 2026-08-18 asserted work their diffs did not contain: `aafc768`
    ("D-319 research file untracking") touched only `DECISIONS.md` and four
    test files and untracked nothing, and `79ba55d` ("26 tests") shipped
    proposal 034's strategy with zero test files. Both claims were repeated
    downstream before anyone ran `git show --stat`. Run it.

32. **A fade or mirror claim is reported split by `fill_was_maker`, never
    pooled.** D-326's original ruling (+$281.74 mirrored, t=3.46, n=345)
    pooled maker and taker fills and was wrong: 80% of that evidence was
    maker fills, which cannot be mirrored (a maker fill only happens because
    the market already moved through our resting limit - the counterfactual
    "take the other side instead" does not exist for it). The taker-only
    number was t=1.19 on n=116, below the kill bar. `positions.fill_was_maker`
    (D-329) exists so this split is a query, not a re-derivation, the next
    time someone reports a fade or mirror result.

33. **A hook that cannot be satisfied by the agents it governs will be
    bypassed by them.** The cross-owner sweep check shipped 2026-08-19
    (`scripts/pre-commit-conflict-check`, `d66aff5`) refuses an undeclared
    session that stages another agent's ledger-written files, and the
    sanctioned way to declare is an environment variable. The permission layer
    refuses the env-prefix form (`VAR=value git commit ...`), so the hook's own
    author was the first agent it cornered: `cody-hook-harden` could not
    declare the sanctioned way and reached for `git commit --author` instead.
    That corner was escaped WITHOUT bypassing the hook - `--no-verify` and
    `SKIP_CONFLICT_CHECK=1` were not used, and that was verified - but a
    governance mechanism that leaves its subjects no sanctioned path gets
    circumvented, which is worse than the disease. D-331 fixed the cause:
    spawned sessions now export `AGENT_ID=cody-<topic>` for the whole session.
    The standing rule (D-334): before shipping a hook or gate, exercise its
    sanctioned path AS ONE OF THE AGENTS IT GOVERNS. If they cannot reach it,
    provide one they can, or expect `--no-verify`.

34. **Commit your own paths out of a shared index with a pathspec:
    `git commit -- <paths>`.** Never `git add -A` (convention 16). This working
    directory AND its git index are shared (convention 21), so by the time you
    commit, another session's files may already be staged and a bare
    `git commit` takes them. A pathspec commit commits exactly the paths you
    name and leaves another session's staged entries untouched -- which
    `git restore --staged` does not, because unstaging their work is itself a
    change to their index. Verified 2026-08-19 with both hooks installed, on a
    throwaway repo.

        git add -- <your path>          # explicit path, never -A
        git commit -m "subject" -m "body" -m "Agent-Id: cody-<topic>" \
            -- <your paths>

    Before staging, read the FIRST column of `git status --porcelain`: `M `
    means already staged by somebody. Three sweeps in three nights (`b1d44bb`,
    `4d03681`, `26555f2`) landed another session's files in a commit whose
    message named none of them; the third went through the hook built to stop
    the first two (D-337).

35. **A commit trailer block is the LAST paragraph of the message and nothing
    else, so `Co-Authored-By` and `Agent-Id` go in ONE final paragraph,
    `Agent-Id` last.** The D-335 hook does not hand-roll trailer parsing: step 4
    of `scripts/pre-commit-conflict-check` delegates to
    `git interpret-trailers --parse`, and git parses only the final paragraph of
    a message as trailers. That makes `-m` ORDER a correctness question, not a
    style one. A message ending

        Agent-Id: cody-<topic>

        Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

    has TWO paragraphs; git reads the second one only; the hook prints
    `trailers parsed: 1  (Agent-Id: 0)` and REFUSES. It is right to: by the
    definition git itself uses, that message carries no `Agent-Id`. The trailer
    is present in the text and absent from the object, which is the same class
    of failure as convention 31. Write one paragraph instead, and build the
    message in python rather than in bash, where the embedded newline is
    awkward:

        subject = "records: ..."
        body = "..."
        trailers = "Co-Authored-By: ...\nAgent-Id: cody-<topic>"
        subprocess.run(["git", "commit", "-m", subject, "-m", body,
                        "-m", trailers, "--"] + paths, env=env)

    This cost two failed commit attempts on 2026-08-19 before the SHAPE of the
    message, rather than its content, was suspected. Verify before committing,
    not after: `git interpret-trailers --parse <message-file>` prints exactly
    what the hook will see. Convention 34 carries the rest of the commit line.

---

## Numbering note for 27, 28 and 29 (closed by Raven ruling, 2026-08-19)

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

Raven ruling, 2026-08-19: numbering stands as assigned. D-292 was applied correctly (next free number, nothing overwritten), nothing cites 27-29 by number, and the pins already assert this state. Renumbering would churn the doc and tests for zero gain.
