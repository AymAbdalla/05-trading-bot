# Handoff: Forge reasoner cycle 2

**Session:** `cody-forge-reasoner-c2`, 2026-08-19 ~07:00-07:20 EDT (11:00-11:35 UTC)
**Brief:** `docs/handoffs/from-raven/2026-08-19-forge-reasoner-cycle-2.md`
**HEAD:** `2bbfc26`, pushed, tree clean. Harness re-derived **21/21, exit 0** at 07:12 EDT.
**Touched:** docs/proposals ONLY. No code, no strategy, no live-loop change, nothing signalled or restarted.

## What was built

Three files, one commit (`2bbfc26`):

- **`strategies/proposals/038-pm-settlement-resolution-ledger.md`** (kind=repair)
- **`strategies/proposals/039-pm-time-stop-hold-through.md`** (kind=experiment)
- **`035-...-uncensored-arm.md`** amended, append-only, via `engine.concurrency.safe_edit`
- `external-signals-2026-08-19-cycle2.md` committed (it was untracked; last cycle's equivalent is tracked)

Both new proposals are `repair`/`experiment` with `expected_edge_bps: null`, so **neither touches the
disputed 200bps floor**. Frontmatter validated against `agents/forge.py` REQUIRED_FIELDS / KINDS /
VALID_ASSET_CLASSES — all three parse clean.

## The finding that drove all three

Everything came out of one query, run read-only on `db/trading.db` at ~11:30 UTC. **Re-derive before
quoting; these are claims about that read (convention 25).**

Settlement is not recorded anywhere. The only way to learn what a market resolved at is to infer it
from a *sibling* position on the same `(pair, outcome_side)` that happened to be held to `exit_px`
0.00 or 1.00. `outcome_side` lives in `signals.features_json`.

1. **Coverage is 37.6%.** 2,216 closed positions touch 864 distinct market-sides; resolution is
   recoverable for **325**. For **539 (62.4%)** the system holds positions whose outcome it cannot
   compute. A further 193 closed positions have no recoverable `outcome_side` at all.
2. **The method is sound.** Of 17 pairs where both sides were independently recovered, **17 of 17**
   show exactly one side at 1.00 — the arithmetic a binary must satisfy. That is a real validation.
3. **But the sample is biased toward losers.** Of 291 singly-recovered market-sides, only **28.5%**
   settled 1.00 against a ~50% unbiased benchmark. Mechanism: a winning side gets sold early by
   `profit_target` and leaves no settlement row; a losing side rots to 0.00 and records one.
4. **Exit price vs realised settlement** (recoverable rows only — this is the forecast-free test):

| exit_reason | n | avg exit px | settled 1.00 | diff |
|---|---|---|---|---|
| sell:price_stop | 231 | 0.2464 | 0.169 | -0.078 |
| sell:profit_target | 79 | 0.4376 | 0.203 | -0.235 |
| sell:salvage_floor | 22 | 0.0650 | 0.000 | -0.065 |
| sell:model_stop | 13 | 0.3337 | 0.308 | -0.026 |
| **sell:time_stop** | **16** | **0.4410** | **0.625** | **+0.184** |

Four exits sell *above* what the position turned out to be worth. One sells below. The bias in (3)
inflates all five, so **`time_stop` measuring badly survives a bias pushing the other way** — that is
the only reason 039 is filed.

## Things I want challenged

- **The `time_stop` result is NOT significant.** n=16, one-sided binomial p=0.1095. And it is one of
  five buckets I computed, so the multiple-comparison problem is real. 039 carries a banner saying so
  and its kill condition demands 120 *fresh* observations rather than re-grading these 16. If Raven
  thinks that is still too thin to file, that is a fair call.
- **The survivorship objection is the strongest argument against 039** and I could not fully kill it.
  A time stop fires on positions that survived 60s, and survivors settle 1.00 more often. I answer it
  by comparing against the market's *contemporaneous price* (0.4410) rather than the strategy base
  rate (18.6%) — the 18.6%-vs-71.4% framing is wrong and I say so in the body. Worth a second read.
- **I contradicted the brief on `salvage_floor`.** The brief calls it "a new, expensive exit" and the
  "censoring mechanism". Its *mechanism* claim is confirmed (21 of 21 recoverable salvage exits
  settled 0.00 — it is the loser subset exactly as 035 predicted), but economically salvage **saved
  $26.09** vs holding (actual -70.79, counterfactual -96.88). The -130.56 was already lost before the
  floor fired. So I did not write the salvage repair the brief suggested; I amended 035 instead.
- **2 market-sides carry both 0.00 and 1.00** in the raw inference map. Arithmetically impossible for
  one side of one binary. Dropped from my numbers, unexplained, flagged in 038 as its first test case.

## Deliberately not done

- **No set-building / complement no-arb proposal.** Standing correction 2 forbids a new complement
  no-arb gate before the ≥24h tape from ~03:28 2026-08-20. The brief's suggested direction 1 collides
  with its own constraint, so I left it.
- **No weather-longshot amendment to 033.** I had no measurement to ground it — env B weather books
  0 entries, which is correct per the standing corrections. Proposing it would have been reasoning
  from an X post alone.
- **`market_tape` untouched.** 026 and 037 are mid-measurement on it until ~03:28 2026-08-20. 038
  deliberately specifies a *new* table for exactly this reason.
- **Nothing scoped to 15m markets**, nothing depending on the keyed tape or calibration tape.
- Did not run the full pytest suite (docs-only change); harness only.

## Two things for the ONE restart / open items

1. **`AGENT_ID` read EMPTY this session — first non-SET reading in seven.** My parent chain is
   `3000 python -> 2901 zsh -> 32931 claude -> 1 hermes-agent`. The **Hermes gateway** spawned this
   session, not tmux, and *the gateway spawn path does not export `AGENT_ID`*. This is **open item 9,
   now observed rather than hypothetical.** I used the sanctioned `CONFLICT_CHECK_AGENT_ID` fallback;
   the hook accepted it and the trailer matches. CLAUDE.md's "sixth consecutive SET reading" line
   should be corrected — it is conditional on the spawn path.
2. **`fill_was_maker` is now a real column** (2,261 non-null in `db/trading.db`) but **only 8 rows
   read 1**, all opened 07:29-08:52 UTC. The 2,253 zeros run back to 2026-08-18 03:02, before the
   column existed — **backfill, not observation**. Convention 32 is mechanically checkable only on
   positions opened after 2026-08-19 07:28:34 UTC. 035's data_requirement saying the column does not
   exist is now stale; the amendment records that.

Also: `streak_snapper` post-restart, observed-taker-only is **n=7, +$15.00**. The +74.86/19 headline
in the brief pools backfilled-flag rows. Not enough to conclude anything either way yet.

## Hygiene

- 3 of 4 committed files show `untracked-by-coordination` (created with the Write tool, not
  checkout/checkin). Non-blocking per CLAUDE.md. The 035 amendment *did* go through `safe_edit` and
  verified clean, with an idempotent `edit_fn`.
- D-333 guard: no sibling alive. `ps` showed only my own parent (2901) and tmux server 37068 carrying
  its original argv — convention 25, that is not a live sibling.
- Live loops 71360/71394/71442 untouched.

## Next steps for Raven

1. Grade 038 and 039. 038 is the load-bearing one — 039, 035's grading, and any future forecast-free
   exit or structural measurement are all blocked on it, and it is independent of the 2026-08-20
   restart payload so it could land either side of it.
2. Rule on whether 039 should have been filed at p=0.11 at all.
3. Decide whether 038 belongs in the ~03:45 EDT 2026-08-20 restart's running order or after it. My
   read: **after**. It touches no existing table and the restart is already carrying five items.
