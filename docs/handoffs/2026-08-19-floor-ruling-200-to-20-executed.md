# Floor ruling executed: PREDICTION_MARKET edge floor 200 -> 20 bps (D-336)

**Session:** `cody-floor-ruling` (PID 60132), 2026-08-19 02:58 - 03:40 EDT
**Directive:** `docs/handoffs/from-raven/2026-08-19-floor-ruling-200-to-20-bps.md`
**Commit:** `e756af3` "D-336: prediction-market edge floor 200->20bps (live tape:
tick is 0.001, not 0.01)", trailer `Agent-Id: cody-floor-ruling`, PUSHED
(`main...origin/main` in sync).

## READ THIS FIRST: I did not make that commit, and I did not write the first
## draft of the change either

This is the single most important thing in this handoff and it is a governance
problem, not a code problem.

Timeline, all times EDT, all from `stat`/`ps`/`git log`, not from memory:

- 02:58 I start. Task 0 guard says WAIT: `cody-agent-trailer` (PID 58966) is
  alive. I wait, correctly, for ~25 minutes. During the wait I do read-only work
  only.
- 03:20:07 `agents/forge.py` is modified **by someone else**, already carrying a
  200 -> 20 change and already citing "D-336" by number. No `claude -p` sibling
  is alive at that moment except me. **There is NO `file_coordination` row for
  `agents/forge.py`** - the edit did not go through `engine.concurrency`.
- 03:21-03:24 `docs/DECISIONS.md`, `strategies/polymarket/weather_arb.py`,
  `agents/forge/forge.agent.md`, `agents/forge_candidates.py`,
  `tests/test_forge_reasoner.py`, `tests/test_forge_shadow_eval.py` all appear
  modified the same way. A full draft of my assigned task, un-ledgered and
  uncommitted.
- 03:23:37 PID 58966 exits. 03:25:10-03:25:46 I sample four times: all hashes and
  HEAD stable, only me alive. Guard clears. I edit.
- 03:26 I apply my corrections through `safe_edit(agent_id='cody-floor-ruling')`.
- 03:27:51 **commit `e756af3` is created by someone else**, containing MY working
  tree byte for byte, under MY `Agent-Id: cody-floor-ruling` trailer, with a
  commit message that is NOT the one my directive specified. I was inside a
  6-minute `pytest` run at the time and issued no git command.
- 03:28 PID 71304 spawns on `2026-08-19-D337-ledger-ownership.md`.

**What this means.** The Agent-Id trailer shipped under D-335 records an identity
that another actor can and did write on a commit the named agent did not make.
The trailer is a *label*, not *provenance*: nothing binds it to the process that
produced the tree. D-331's stated goal ("agent identity travels into git
history") is not achieved by it. Whoever rules on D-337 should treat this session
as the worked counter-example - it is the same failure D-332 documented for the
launcher banner, one layer up.

I chose not to fight it. Reverting or amending a pushed commit while three
sessions were live would have been worse than the disease, and the *content* is
correct. But nobody should read `e756af3`'s trailer as evidence that I committed
it, and convention 31 ("a commit message is a claim, not a fact") should be
extended to trailers.

## What I actually did

I found a complete draft in the tree and chose to **preserve and correct it**
rather than clobber it with a from-scratch rewrite. My edits, all through
`safe_edit(agent_id='cody-floor-ruling')` (ledger rows exist for all five):

1. **`docs/DECISIONS.md`** - replaced the draft D-336 with **Raven's verbatim
   ruling text**, as Task 1 required ("transcribe EXACTLY"). The draft was a
   different composition: it dropped Decision points (3) and (5), dropped the
   "tick is a venue property, EVENT/SPORTS flagged for re-confirmation" framing,
   dropped Raven's ~03:10 numbers, and **added** "Ratified by execution under
   Aym's overnight full-authority directive" - an authority claim not in the
   directive and not one I can verify. All of that is gone; the entry is now the
   directive's text, unaltered.
   Below the verbatim block I added one italic paragraph, clearly marked as mine
   and outside the ruling, recording (a) my independent re-derivation and (b) the
   four extra files touched. Reason: the ruling's `Where` line names only two
   files, but six were changed; a `Where` that omits changed files is a false
   record (convention 31). I did not edit the ruling's own `Where` line.
   Verified append-only: `git diff HEAD -- docs/DECISIONS.md` had **0 deletions**.

2. **`agents/forge.py`** - three things the draft missed, all required by Task 2:
   - Convention 17 expiry note updated from "expires if Polymarket changes its
     tick size" to "expires if the venue's **OBSERVED quoting grid** changes",
     with the note that the grid is a measurement off the tape, not a documented
     venue constant.
   - Added the venue-property / EVENT-SPORTS-inherit-and-re-confirm note to the
     constant block.
   - Restated the tick as `0.001` / "a TENTH of a cent" with the full derivation
     `0.001 / 0.50 = 20bps` (the draft said "0.1c tick" and gave no division).

3. **`strategies/polymarket/weather_arb.py`** - the draft changed the constant
   and its docstring but **left a contradicting comment inside the same file** at
   line ~4250 still asserting "the smallest expressible move is a cent, which is
   200 bps". Fixed. The constant is `20.0`; the "eighty times that floor" arithmetic
   the draft already corrected is right (1,600 / 20).

4. **Arithmetic slip, three places** - the draft claimed the old binary floor was
   "eight times above" the 30bps spot floor. 200 / 30 = 6.67. Changed to `~6.7x`
   in `agents/forge.py`, `agents/forge/forge.agent.md`, and the comment in
   `tests/test_forge_shadow_eval.py`. In a change whose entire subject is an
   unchecked numeric premise, shipping a wrong multiplier was not acceptable.

## Deviation from the directive's file list, declared

Task 3 said stage `docs/DECISIONS.md`, `agents/forge.py`,
`strategies/polymarket/weather_arb.py` **and ONLY those**. Six files went in.
The other three - `agents/forge/forge.agent.md`, `agents/forge_candidates.py`,
`tests/test_forge_reasoner.py`, `tests/test_forge_shadow_eval.py` - restate the
200 figure in prose or **assert it in a test**. Specifically:

- `tests/test_forge_shadow_eval.py:548` asserted `min_edge_bps_for(
  'PREDICTION_MARKET') == 200` and used a 100bps candidate to prove refusal;
  at a 20bps floor, 100bps clears and the test fails two ways.
- `tests/test_forge_reasoner.py` refusal test used a 30bps candidate, which now
  clears; and its prompt test asserted `PREDICTION_MARKET\s+200 bps`.

Committing the three named files alone would have left a red tree, which
convention 1 does not permit. I flag it rather than let the stat line pass
unexplained. (The test edits themselves were in the tree when I arrived; I only
corrected the comment arithmetic.)

## Numbers

- **Full suite:** `3,949 passed, 1 skipped, 0 failed` in 372s
  (`pytest tests/ -q --ignore=tests/test_dashboard_charts.py`), run in-tree at
  03:27-03:33 with one sibling (71304) spawning at 03:28. It did not touch my
  files; but per convention 21 this run is slightly less trustworthy than an
  isolated one. Targeted run beforehand at 03:26 with **no** sibling alive:
  `272 passed` across `test_forge_reasoner`, `test_forge_shadow_eval`,
  `test_weather_shadow_wiring`, `test_hypothesis_graph`.
- **Harness:** `backtest/validate_harness.py` 21/21, **exit 0**, 03:33.
- **Live floor after the change:** `PREDICTION_MARKET 20, EVENT 20, SPORTS 20,
  CRYPTO 30`.

## The tape, re-derived independently (convention 25)

I did not take the ruling's premise on trust. At ~03:05 EDT, read-only against
`db/trading.db`:

```
n_nonnull = 9,246   on 0.001 grid = 9,246 (100%)   on 0.01 grid = 1,342 (14.5%)
in band 0.10-0.90 = 1,752          of which sub-cent = 640 (36.5%)
```

Raven's ~03:10 read was 9,033 / 14.6% / 36%. The tape grows between reads; the
three quantities agree. **The premise holds: the tick is 0.001, and the 200 bps
floor was derived from a venue property the venue does not have.**

## Proposal 037

Not re-filed, not touched. 40 bps is **four ticks** on the observed grid and
clears the re-derived 20 bps floor by 2x, so `below_min_edge_bps` no longer
applies to it. Its `forge_refusal:` field is now stale and someone should clear
it - that is 037's own queued session's job, not mine. 032 (250) and 033 (380)
clear either floor and are unaffected.

## What I did NOT touch

- Any proposal file, `strategies/proposals/forge_runs.jsonl`, the registry,
  `config.yaml`, `run_polymarket_shadow.sh`.
- `scripts/pre-commit-conflict-check`, `tests/test_pre_commit_hook.py` (owned by
  the trailer session, and modified again by PID 71304 as I write this).
- `engine/polymarket/shadow_loop.py`, any strategy code beyond `weather_arb.py`'s
  tick constant and its two comments.
- Any live daemon. **Nothing was restarted.** Main shadow loop 41735, env B
  38881, liquidation recorder, hyperliquid poller: all untouched.
- No backtest was run. Shadow only.

## Carried forward, unchanged

- **The D-329 measurements are still dark in every live process** and are a
  RESTART item: ready for the next natural restart, not this session's job.
- **The running loops do not have the new floor.** Convention 13: 41735 imported
  `agents/forge.py` at 00:56 on `e033078`. The floor change reaches nothing that
  is currently running. It affects the *next* forge cycle and the next restart.
  Nothing about live behaviour changed tonight.

## For Raven

1. **Rule on the trailer-forgery problem above before D-337 lands.** A trailer
   another process can write is not provenance. This session is the worked
   example.
2. The commit message on `e756af3` is not the one the directive specified. I did
   not write it and did not amend it. If the exact wording mattered, it is lost.
3. 037's stale `forge_refusal:` field needs clearing by its own session.
4. `agents/forge_candidates.py` has an ugly line wrap from the draft edit
   ("D-336), a\nkill condition"). Cosmetic, left alone deliberately.
