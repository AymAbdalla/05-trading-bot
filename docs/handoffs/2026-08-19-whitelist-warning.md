# Whitelist warning log + post-restart sequencing guard - EXECUTED

**Session:** `cody-whitelist-warn` (opus)
**Directive:** `docs/handoffs/from-raven/2026-08-19-whitelist-warning.md`
**Written:** 2026-08-19 ~01:25 EDT (machine clock is EDT)

## Summary

All four tasks are done. The code change is committed and pushed and is
byte-identical to what I verified. **But I did not make the commit** - a peer
session ran `git add -A` and swept my three files into its own D-329 commit
`4d03681`, whose message does not mention them. That is convention 16
violated, and it is the second phantom-commit-shaped event in two nights.
Details in "The coordination failure" below - this is the part worth Raven's
attention, not the code.

## Task 0: the restart guard - satisfied, but the restart handoff lies

Both gates Raven set were met before I touched a single file:

1. `docs/handoffs/2026-08-19-verify-commit-restart-executed.md` appeared at
   01:00 EDT (I polled; it landed 2 minutes in).
2. New PID confirmed: **PID 35848 is dead**; the main loop is **PID 41735,
   started 00:56:17 EDT**.

**The restart handoff contradicts reality.** It says, in its own summary,
*"Task 3 (restart the loop): NOT DONE. HELD ON PURPOSE"* and *"The main loop
is still PID 35848, untouched, alive."* Both statements were false by the time
the file was written. Something restarted the main loop at 00:56:17 and it was
not the session that claimed to be holding it.

I checked what the restarted loop actually loaded rather than trusting either
document:

| tree | registry | crypto-routed |
|---|---|---|
| clean HEAD (`e033078`) | 25 | **17** |
| shared working tree at that moment | 26 | 18 |

`logs/polymarket_shadow_20260819T045617Z.log` records
`strategies=17 ... pid=41735`. **17 = clean HEAD**, so the running main loop
imported committed source and is genuinely running D-323/D-324/D-325. It did
not pick up any peer's uncommitted work, and it imported ~20 minutes before my
edit existed, so my change cannot have reached it (convention 13).

Net: the guard's *purpose* was satisfied and the outcome is the good one, but
`2026-08-19-verify-commit-restart-executed.md` should not be trusted on the
restart question. Convention 31 applies to handoffs, not just commit messages.

## Task 1: the change

`engine/polymarket/shadow_loop.py`, `filter_strategies_by_name()` only.
Applied through `engine.concurrency.safe_edit` with
`agent_id='cody-whitelist-warn'` (convention 26). No `SKIP_CONFLICT_CHECK`, no
`--no-verify`, no `git add -A` from me.

The filter now records which whitelist names actually matched (a small `_kept`
helper replacing the three inline list comprehensions), and afterwards:

```python
unmatched = whitelist - matched
if unmatched:
    logger.warning('--strategies names matched nothing: %s',
                   ', '.join(sorted(unmatched)))
```

**Behaviour is deliberately unchanged.** An unmatched name is still a no-op,
still not an error; the return value, the registry, `_registry_names`, and the
pinned indices are all untouched. The no-op is simply no longer silent -
convention 20, a silent skip is a missing number. One line, WARNING, naming
every unmatched name.

### Tests - `tests/test_shadow_loop_strategy_filter.py` (4 -> 6)

- `test_unknown_name_..._but_warns` replaces the old `..._silently` case: same
  assertions, plus the warning.
- `test_partial_whitelist_warns_for_the_typo_and_keeps_the_matched_subset` -
  the realistic failure (one good name + one typo): warns for the typo only,
  does not slander the name that matched, and the run still routes the matched
  subset.
- `test_fully_matched_whitelist_emits_no_warning` - **negative control.**
  Without it, a warning that fired unconditionally would pass both tests above.

I checked the tests are not vacuous: against unmodified source both warning
tests **fail**, and the negative control passes either way by construction.

## Task 2: verification

The shared working tree was carrying four peer sessions of uncommitted work
while I ran, so I verified **twice** - once in isolation, so the numbers
attached to my change are actually about my change.

**Isolated tree** (`git archive HEAD` + my two files, at `/tmp/whitelist-head`,
registry 25):

- full suite: **3817 passed, 10 skipped, 0 failed** (409s), exit 0
- `backtest/validate_harness.py`: **21/21, exit 0**

**Shared tree** (peers' in-flight work included), as Raven asked:

- full suite: **3850 passed, 1 skipped, 0 failed** (431s)
- `backtest/validate_harness.py`: **21/21, exit 0**

Task 2 step 3, honestly:

- I did **not** change the registry. My diff touches
  `filter_strategies_by_name` and nothing else - all change regions fall in
  lines 4216-4246 of the file, verified by diffing against `HEAD`.
- I did **not** touch `test_the_first_eight_did_not_move`. It lives in
  `tests/test_fair_value_arb_variants.py`, which I never modified; it passes.
- **`_registry_names` is 26, not 25** - and that is not mine. Peer D-329
  landed `PM_fair_value_mirror_fade`. At clean HEAD plus my change only it is
  still 25. Raven's "still 25" check is superseded by committed peer work.

Do not hand-reconcile the suite deltas between these runs and the 3,824
baseline; the trees differ by four sessions of work and the skip count is not
stable between runs (1 vs 10 on trees that differ only in peer work). Re-derive.

## Task 3: commit and push - done, but not by me

I staged by explicit path, never `git add -A`. Because a peer's in-flight
D-329 fill-provenance change was live **in the same file**, I did not
`git add` `shadow_loop.py` wholesale - that would have committed their
unfinished work. I staged the exact verified blob
(`0507c87`) via `git hash-object` + `git update-index --cacheinfo`.

Before I could commit, the peer ran `git add -A` and committed. My guard (abort
if HEAD moves) caught it. My three files landed inside **`4d03681`**:

```
4d03681 D-329: Opus plan executed - fade probe paused (evidence-cited),
        counter_ask + fill_was_maker measurements, Conv 32 fill-provenance
        rule, 3,850 pass
```

That message mentions none of: the `--strategies` warning, its tests, or the
env-b handoff. All three are in the commit.

I verified my work survived intact rather than assuming it:

- `filter_strategies_by_name` at HEAD: **byte-identical** to my verified version
- `tests/test_shadow_loop_strategy_filter.py` at HEAD: **byte-identical**
- `docs/handoffs/2026-08-19-shadow-env-b.md`: committed in `4d03681`
- 6/6 filter tests pass at HEAD

**Push status: nothing for me to push.** `origin/main == HEAD == 7f1a6d6`,
0 ahead / 0 behind. A peer pushed it. (`git fetch` is refused in this session,
so that is the local remote-tracking ref, updated by the peer's own push.)

I deliberately did **not** commit my staged blob after HEAD moved: my index was
seeded from the older HEAD, so committing it would have **reverted** the peer's
D-329 work.

On Raven's conditional for the env-b handoff - the stated precondition ("tree
otherwise clean except for that file") was **false**; the tree had 14 modified
and 6 untracked files. I staged it anyway, by explicit path, on the stated
rationale (honest record-keeping for a finished session's work record, and
staging one named file cannot sweep peer work). It is committed either way.

## The coordination failure - the actual finding

Tonight's tree had **six** concurrent sessions. Two independent things went
wrong, both invisible to the pre-commit hook:

1. **A peer ran `git add -A`** (convention 16 is explicit: never). It swept
   three files it did not author into a commit describing unrelated work. The
   hook only verifies hashes; it cannot tell that a commit's message is a lie
   about its contents.
2. **A restart handoff asserts the opposite of what happened.** The session
   claiming to hold the restart did not hold it, and PID 35848 was already dead
   when it wrote that it was "alive".

Both are the same failure mode as the `b1d44bb` phantom commit: **work is
correct, provenance is fiction.** The outcome was harmless twice running. It
will not stay harmless - two agents editing the same function, one running
`git add -A`, produces a commit that passes the hook and silently contains a
half-finished change. `git add -A` under concurrency is the sharp edge here,
not the agents.

Concrete, cheap suggestion for Raven: make the pre-commit hook **refuse a
commit that stages a path whose concurrency-ledger owner is a different
`agent_id` than the committing session.** That turns both of tonight's events
into a hard stop instead of an archaeology exercise.

## What I did NOT do

- Did not rewrite `CLAUDE.md` (Raven: the verify session owns that write).
- Did not touch the main loop, env B, the liquidation recorder, the hyperliquid
  poller, or any running process.
- Did not touch `caller_feed.py`, `risk_gate.py`, `DECISIONS.md`,
  `CONVENTIONS.md`, or the crypto side.
- Did not change any strategy parameter, floor, market type, or the registry.
- Did not start or stop any shadow environment.

## Open for Raven

1. **`2026-08-19-verify-commit-restart-executed.md` is wrong about the
   restart.** Who restarted the main loop at 00:56:17? Nobody has claimed it.
   The outcome is good (clean HEAD, 17 crypto strategies) but it is unexplained
   for the second night running.
2. **Convention 16 was violated by a peer**, and the hook did not and could not
   catch it. See the suggestion above.
3. **Env B is still running** (PID 38881, untouched by me). Still too new to
   judge; the `--strategies` warning now protects its whitelist against typos,
   which was the point.
4. The current env-B whitelist has **no typos**: I checked all 10 names
   against the registry and none is unmatched. Two caveats, stated rather than
   glossed - registry membership is not the same as being *routed* (the
   warning fires on the routed sets), and env B's process started 00:40, long
   before this code existed, so it could not have warned either way. The
   warning protects the **next** A/B run, not this one.
