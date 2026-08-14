# Graveyard: what was tested and what died

**Status:** DURABLE
**Graveyard build:** 2026-08-14 13:45 (post futures purge, D-261)
**Cost model version:** 2026-08-13

This directory holds the output of the v0 sweep: every strategy, ticker,
timeframe and exit config that was run, and the verdict each one got. Most of
it is a record of failure. That is the point. The graveyard exists so a result
cannot be quietly re-tested until it looks good.

## Headline numbers

| Thing | Value |
|---|---|
| Entries | 535,425 |
| Strategies | 55 |
| Tests completed | 509,080 |
| FAIL | 508,647 |
| NOT_TESTED | 26,345 |
| PASS (raw rows) | 381 |
| PASS_BENCHMARK (not discoveries) | 52 |
| **Distinct findings** | **155** |
| Expected best-by-chance | ~5.13 sigma |

## Read this before you cite anything

**Cite `distinct_findings`, never raw PASS counts.** The 381 PASS rows collapse
to 155 distinct (strategy x ticker x timeframe) findings, 143 by strategy
family, 137 by strategy x ticker, across 62 tickers. One strategy that works on
one ticker across 11 exit configs is one observation reported eleven times.
`summary.json` does the collapsing for you.

**155 findings out of 509,080 tests is the base rate, not a discovery.** At this
grid size chance alone is expected to produce a best result around 5.1 sigma.
A single impressive row proves nothing. Any judge reading this must correct for
multiple comparisons on hypotheses GENERATED, using an effective test count
(correlated tickers and timeframes are not independent), not the raw row count.

**NOT_TESTED means "could not run."** It never means "ran and found nothing."

## What DURABLE does and does not mean

DURABLE is a statement about harness validation only. It means
`validate_harness.py` exits 0 and the four validation families (oracle, buyhold,
coinflip, assertions) all pass, so the numbers were produced by a harness that
is behaving. It does **not** mean the graveyard is clean.

**4 of the 8 silent assertions currently FAIL against this graveyard:**

| Assertion | What it is flagging |
|---|---|
| `quarantine_canary` | Quarantined tickers (MULN, SNDL) still have rows, mostly 1 to 4 trade rows with `inf` PF |
| `trade_count_sanity` | 8 strategies produce zero trades in 99%+ of their rows. C2, V2_vwap_magnet_sessionatr and V5_capitulation_equity are at 100% zero |
| `duplicate_strategies` | 132 strategy pairs are near-identical |
| `timeframe_coherence` | 5m and 15m profit factors disagree in direction on the same strategy, ticker and exit config |

These are known (D-226), not new, and not fixed. Do not describe this pack as
clean.

**`duplicate_strategies` and `trade_count_sanity` are the same problem.** The
duplicate signal is driven by strategies that never fire. C2 pairs with all 54
other strategies at `identical_fraction` 1.0 because C2 produces zero trades in
all 264 rows it is compared on, so every comparison is empty against empty. The
next-highest members of the duplicate list (V2_vwap_magnet_sessionatr,
V5_capitulation_equity, V4_gap_hold_proxy, V4_trend_reclaim,
rising_three_methods, rsi_extreme, V3_intraday_momentum_crypto,
V5_forced_flow_crypto) are the same strategies that top the zero-trade list.
This is convention 3 (verify a strategy FIRES before interpreting its results)
failing loudly, not evidence that six strategy IDs are secretly one strategy.
Fixing the non-firing strategies should clear most of both assertions.

## Files

Post-purge (built against the current 535,425-entry graveyard):

| File | What it is |
|---|---|
| `summary.json` | Distinct-finding collapse and multiple-comparisons context. **Start here.** |
| `harness_validation.json` | `all_pass: true`, generated 2026-08-14 13:45 |
| `v0_graveyard_full.json` | The raw sweep, 385MB. Gitignored, local only, not on GitHub |
| `../judge_evidence_pack.json` | What `agents/judge.py` reads. Carries the current silent-assertion results |

Pre-purge (built 2026-08-13 or earlier, against the graveyard that still
contained the 23,595 bad futures contract rows). Treat these as indicative, not
current, until they are rebuilt:

| File | Built |
|---|---|
| `assertions.json` | 2026-08-13 16:06, only 539 entries. Superseded by the `silent_assertions` block in the judge pack |
| `asset_class.json` | 2026-08-13 10:58 |
| `conditional_edge.json` | 2026-08-13 11:10 |
| `constraint_sweep.json` | 2026-08-13 19:02. Its own DIAGNOSTIC overclaims. See D-256 |
| `dispersion_gate.json` | 2026-08-14 08:57 |
| `dispersion_gate_smoke.json` | 2026-08-13 16:59 |
| `inversions.json` | 2026-08-12 23:35 |
| `pooled.json` | 2026-08-13 09:50 |
| `toll_collector.json` | 2026-08-13 16:45 |
| `vr_fingerprint.json` | 2026-08-13 16:28 |

`archive/` holds pre-purge and pre-fix snapshots. The large ones are gitignored.

## The verdict this supports

33 of 35 v0 strategies showed zero gross edge. Full writeup in
`research/2026-08-13-v0-verdict.md`. Nothing here has cleared the bar for live
capital, and nothing is trading.
