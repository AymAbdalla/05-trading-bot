# README rewrite

**Date:** 2026-08-14
**Session:** Cody
**Trigger:** `docs/handoffs/from-raven/2026-08-14-readme-rewrite.md`
**Status:** README.md rewritten on disk. NOT committed, NOT pushed, per instruction.

## What was built

One file changed: `README.md`, full rewrite.

Sources read first: Raven's rewrite instruction, my own discussion note
(`docs/handoffs/from-cody/2026-08-14-readme-discussion.md`), Raven's convergence
note, DECISIONS.md (first 200 and last 120 lines plus the D-233/234/236/237
entries), the judge evidence pack, `logs/post_sweep_repair.log`, the v0 verdict
writeup, and the strategy registries.

Structure follows Raven's 10-point outline with the four refinements we agreed:

1. Title plus one-line description, neutral voice
2. **The result**, above the fold, first person, with the link to the v0 verdict
3. Status line: paper and backtest only, live trading not enabled
4. **How this was built**, first person, one paragraph, vendors not named at all,
   timeline folded into the same paragraph so the method and the four days
   explain each other
5. Architecture, neutral voice, with the cost model and third referee engine
   added (both were missing)
6. **Current evidence pack** table, new section
7. **What I learned**, six items, each anchored to a D-number
8. **Decisions made on this project**, AYM rulings first, CC below
9. Conventions, setup, roadmap, docs, changelog
10. No Author section

## Corrections made to the source instructions

Four places where the instruction did not match the data. All corrected in the
README, flagging them here so Raven and Aym can overrule.

1. **Strategy family table did not add up.** The old table listed v3 at 5 and
   totalled 55, but 28+7+9+5+3+2 is 54. Checked the registries: v3 is
   `STRATEGY_LAB_V3_STRATEGIES`, length 6. Table now sums correctly to 55.
   CLAUDE.md's "54" is also wrong and should be fixed on the next epilogue.

2. **"55 strategies across 1.39 million trades" mixes two runs.** The 1,390,451
   figure is the v0 verdict, which covers 35 strategies over 218,295 rows. The
   current graveyard is 55 strategies over 535,425 rows. Summed the per-row
   trade counts on the live file: 1,900,086. README uses "535,425 backtest runs
   and 1.9 million trade records", which is the accurate version of the same
   claim and a bigger number.

3. **D-261's row count in DECISIONS.md is stale.** The entry says 12,936 rows
   because it was written before the run. The log says the purge actually
   dropped 23,595 (535,425 to 511,830), 51 of them PASS/PASS_BENCHMARK. Used
   23,595, which matches Raven's instruction. Worth correcting D-261 in the log
   itself so the two agree.

4. **The judge pack is not clean and the README says so.** Status is DURABLE
   and `degraded` is null, but 4 of the 8 silent assertions fail on the current
   graveyard: quarantine_canary (MULN and SNDL rows present), trade_count_sanity
   (several strategies at 100% zero-trade rows), duplicate_strategies (C2
   identical to C5/D1/D2/S1/S2 on all 264 compared rows), and timeframe_coherence.
   Reporting DURABLE without that would be exactly the overclaim the repo is
   about, so it is one paragraph in the evidence pack section and one bullet in
   the roadmap. If Aym wants it out, it is one paragraph to delete, but I would
   keep it.

## Stale facts fixed

- DECISIONS.md cited as v9, D-101 through D-265, in both places it appears
- Post-sweep repair no longer "RUNNING". It completed 13:45
- All Binance.US references removed (D-263 retired the checklist item)
- "No live trading until Aym explicitly approves" replaced with "Live trading is
  not enabled", per the note about third person in a safety-critical line
- Author section deleted
- Changelog rewritten with the 08-14 repair as its own entry

## Verified, not assumed

- 535,425 entries, 55 strategies, 381 PASS, 52 PASS_BENCHMARK, 155 distinct
  findings, 509,080 tests completed, 5.13 expected max z: all read out of
  `research/judge_evidence_pack.json`
- 23,595 purged and the 51 PASS note: read out of `logs/post_sweep_repair.log`
- Family counts: imported the registry modules and counted
- No em dashes, en dashes, "Binance", "v8", "RUNNING" or "Author" left in the
  file (grepped, zero hits)

## Not done

- **No commit, no push.** Instruction was explicit and the public/private call
  is still Aym's.
- **No key audit.** Raven's convergence note says a proper key audit runs before
  the repo opens. My earlier pass was a scan, not an audit, and nothing in this
  session changed that. Do not treat the repo as cleared to go public.
- **No separate process repo.** Agreed to come after the README is right.
- **D-261's 12,936 not corrected in DECISIONS.md.** I did not edit the decision
  log this session. It is a one-line fix and Raven may prefer to do it as a
  v9 amendment rather than an in-place edit.
- **CLAUDE.md not rewritten.** The session epilogue rule asks for it, but
  CLAUDE.md currently describes the repair as running and the strategy count as
  54. Both are wrong now. Left it so this handoff can be reviewed against the
  state it describes. Flagging it as the next small job.

## Next steps for Raven

1. Review README.md against the outline. Everything in the 10-point structure is
   there; the disagreements are the four corrections above.
2. Rule on whether the "4 of 8 silent assertions fail" paragraph stays.
3. Aym decides public or private. Nothing gets pushed until then.
4. If public: key audit first, not a scan.
5. Fix D-261's row count and CLAUDE.md's stale state (repair running, 54
   strategies) whenever convenient.
