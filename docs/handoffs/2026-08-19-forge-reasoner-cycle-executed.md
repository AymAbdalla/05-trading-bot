# Forge reasoner cycle executed - 2026-08-19

**Agent:** `cody-forge-reasoner` (spawned, `AGENT_ID` was NOT set in the env -
see "Questionable" below). Directive:
`docs/handoffs/from-raven/2026-08-19-forge-reasoner-cycle.md`.
**HEAD at run:** `3efa59a`, clean apart from the untracked signals file.
**Everything numeric below was read at 2026-08-19 02:45-02:50 EDT from a
database a live loop was writing to. Timestamped readings, not stable facts
(convention 25).**

## What was built

Three proposals plus one run-log record. Nothing executable, no config touched,
no loop restarted, no decision written.

| File | kind | edge | one line |
|---|---|---|---|
| `strategies/proposals/035-pm-settlement-exit-uncensored-arm.md` | repair | null | The D-327 calibration instrument censors its own measurement |
| `strategies/proposals/036-pm-complement-pair-keying.md` | repair | null | Store `condition_id` on `market_tape`; unblocks the whole forecast-free family |
| `strategies/proposals/037-pm-complement-no-arbitrage-taker.md` | edge_hypothesis | 40 bps | Two-leg complement arb. BLOCKED on 036, and refused by the edge floor |
| `strategies/proposals/forge_runs.jsonl` | +1 line | | 9 screened, 6 refused, 3 written; identity asserted |

Written via `engine.concurrency.safe_edit(..., agent_id='cody-forge-reasoner')`
for the two contended files. Harness **21/21 exit 0**;
`tests/test_forge_reasoner.py` + `tests/test_hypothesis_graph.py` **186 passed**
(the only tests that read this directory; no Python changed, so the full 3,908
was not re-run).

## Four findings the directive's context did not contain

I checked the directive's premises against the DB before writing, and four of
them had moved or were wrong. These matter more than the proposals.

**1. Proposal 034 is not "not yet wired". It is live, and it is not testing its
own hypothesis.** `PM_fair_value_settlement_exit` has 6 entries and 5 closes.
Three closed on `sell:salvage_floor` at **6.2s, 48.7s and 80.1s**. Only two
reached a terminal binary price. That is 60% censoring on the exact sample
D-327 named, and it is not random: a position reaches the salvage floor when its
bid has collapsed to 0.10, i.e. when it is losing. Deleting losers raises the
measured settlement frequency, and D-327's kill condition is a *lower* bound
test ("dead if realised settlement frequency over the first 60 entries is below
0.30"). **The floor pushes the instrument away from its own kill condition.**
One entry landed at 0.11 against a 0.10 stop - one cent of room - and closed 6.2
seconds later. The module's documented degenerate-case guard
(`fair_value_settlement_exit.py:177-189`) covers entries *at or below* the
floor, not entries marginally above it, and that band is populated. Proposal 035
is the repair.

**2. Both D-329 measurements are dark in every running environment.**
`positions.fill_was_maker` is in `db/schema.sql:95` and migrated at
`engine/polymarket/shadow_loop.py:720`, and is **not a column of `positions` in
either `db/trading.db` or `db/trading-survivors.db`**. Zero `signals` rows
contain `counter_ask` (`fair_value_arb.py:670`). Cause is convention 13 exactly:
both loops run `e033078`, and `git show e033078:engine/polymarket/shadow_loop.py`
greps **0** hits for `fill_was_maker`. CLAUDE.md says "two measurements shipped
under D-329" - true of the tree, false of the running system. **Convention 32
cannot be applied to any position booked since 00:56 EDT.** Not a code defect,
needs no code change; the next natural restart fixes it. I did not restart
anything. This is the highest-value item in this handoff and I deliberately did
not spend a proposal slot on it, because its remedy is a restart I am directed
not to perform.

**3. The `market_tape` pairing failure has a concrete cause, and the expensive
half is already solved.** The 61.7% complement ambiguity is not a weak
heuristic, it is the absence of a key: `market_tape` (`dip_arb.py:280`) is
`market_id, ts, mid, best_bid, best_ask, source` and `market_id` is a bare
76-78 digit ERC-1155 token id. No heuristic can fix it - two unrelated markets
near 0.50 are indistinguishable from a complement pair by price, and crypto
Up/Down lives near 0.50. But `engine/polymarket/markets.py:110/130/139` already
parses `clobTokenIds` and stamps `condition_id` on every discovery pass, and the
tape **already samples synchronously** (185 distinct timestamps, up to 49 token
quotes sharing one identical float `ts`). The mapping is built in memory every
pass and thrown away at write. Proposal 036 is one field addition, and it is the
blocker under 026, 030, 037 and the Bregman idea.

**4. The 200 bps PREDICTION_MARKET edge floor rests on a tick size the tape
contradicts. This one needs Raven.** `agents/forge.py:109` sets
`MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS['PREDICTION_MARKET'] = 200`, and line 107
gives the derivation in full: `1c tick / 50c premium = 200bps`. Same reasoning
at `weather_arb.py:480-490`. The tape says otherwise: **8,722 of 8,722 non-null
`best_ask` values lie on a 0.001 grid; only 1,278 (14.7%) lie on a 0.01 grid**,
and inside the 0.10-0.90 band the constant was written about, 598 of 1,658 are
sub-cent. If the tick is a tenth of a cent, the same derivation gives a **20 bps
floor, not 200**.

Proposal 037 claims 40 bps and would therefore be **refused at write time** by
`agents/forge.py` under `below_min_edge_bps`. I filed it at `PROPOSED` anyway,
with a `forge_refusal:` field stating the refusal in full at the top of its own
frontmatter, because the README is explicit that the author cannot be the
referee and because the refusal turns on a constant the proposal disputes with
data rather than on the proposal's merits. **I did not raise 037's gate to 200
to make it pass** - that is the same error 037's own rule 5 refuses to make
about fees, and a 20-tick complement violation is a different and far rarer
event than a 4-tick one. Raven's call, and it is a decision about a constant:
either re-derive the floor from the observed grid, or write the non-tick reason
for 200 at `agents/forge.py:107` where the tick reason currently sits. Proposals
032 (250) and 033 (380) clear either floor, so nothing already filed is at risk.

## One standing correction moved under me

CLAUDE.md's headline says `PM_temporal_arbitrage` shows the market calibrated to
within **0.06 percentage points** (83 positions, paid 0.1813, realised 0.1807).
At 02:45 EDT that strategy has **91 closed positions, 455 shares: paid 0.1832/sh,
realised 0.1648/sh, edge -0.0184 - 1.84 percentage points against us, roughly
thirty times the quoted figure.**

Eight more positions moved the headline by that much. Zero censoring still holds
(all 91 closed at `exit_px` 0.00 or 1.00). The lesson is not that the market is
suddenly beatable in our favour - the sign is still against us, and this does
nothing for the fair_value model. The lesson is that **at n=91 the estimate is
not stable enough to bound anything tightly in either direction**, and the
sentence "the market is calibrated to within 0.06 points" should not be carried
into another document without re-deriving it. CLAUDE.md and DECISIONS.md are
Raven's lane; I have not edited either beyond this project's own wake-up file.

**A trap for whoever measures this next:** settlement is recorded as
`exit_reason` **`'stop'` when the token settles to 0.00 and `'target'` when it
settles to 1.00**. It is not a distinct exit reason. Any censoring-rate or
settlement-frequency query must key on `exit_px IN (0.00, 1.00)`, never on the
`exit_reason` string, or it will classify every settled loss as a stop-out and
report a censoring rate near 1.00. `PM_temporal_arbitrage`'s 76 `stop` / 15
`target` split is the same shape.

## What was skipped or deferred

Six candidates screened and refused, each with a category, all recorded in
`forge_runs.jsonl` (convention 20 - an uncounted skip did not happen):

- **D-329 measurement activation** - remedy is a restart, out of session scope.
- **Overreaction fade, 90-120 min** (external signal 1) - the window is longer
  than the entire life of a 5-15 minute crypto Up/Down market. It cannot be
  expressed on the instrument we trade. Separately it is a forecasting strategy
  in the family the taker-only mirror split already paused at t=1.19 / n=116.
- **Rules edge / resolution text** (signal 2) - crypto Up/Down resolution text is
  templated and identical across windows, so the dispersion it feeds on is zero
  here. Real, but it needs event or political markets we do not discover.
- **Bregman projection** (signal 4) - folded into 036/037 rather than proposed.
  036 rule 1 stores `outcome_index` rather than a side flag *specifically* so
  the n-outcome version needs no further schema work; 037 rule 1 explicitly
  refuses to extend to it. Right order: key the tape, count two-outcome
  opportunities for free, spend solver effort only if that count is non-trivial.
- **Mention-market no bias** (signal 5) - no transcript corpus, wrong universe.
- **Maker rebate** (signal 6) - already proposal 024.

## Questionable / incomplete

- **`AGENT_ID` was empty in my environment** (`env | grep AGENT_ID` returned
  nothing), despite D-331 and despite CLAUDE.md saying spawned sessions are
  launched with it. I passed `agent_id='cody-forge-reasoner'` explicitly to
  `safe_edit` and committed nothing, so nothing is mis-attributed. But the
  spawn path that produced this session does not set it, which means D-331's
  guarantee is not actually holding for whatever spawned me. Worth checking
  before the `Agent-Id:` trailer question is settled.
- **035's headline number rests on n = 5 closed positions.** The structural
  argument (a stop that fires when losing removes losers from a
  settlement-frequency estimate) is true at any n, but the 60% is an
  illustration and is labelled as one inside the proposal.
- **037 is unbuildable as filed** - blocked on 036, blocked on the fee question
  (`config.yaml:136` says taker fee 0.0 and flags itself "an assumption, not a
  law"; external signal 3 claims 2% on winners, which at 200 bps of a 1.00
  payout would eat the 40 bps gate five times over), and refused by the edge
  floor. Three blockers, all named in its frontmatter. Its Phase 0 costs nothing
  and runs on stored data - that is the only part anyone should do first.
- I did **not** run the full 3,908 suite. No Python changed. Harness is 21/21.
- I did not verify that the discovery pass and the tape writer share a call
  frame, which is 036's one implementation unknown. Flagged inside 036.

## Next steps for Raven

1. **Rule on the 200 bps floor** (finding 4). It gates 037 and every future
   sub-200bps prediction-market hypothesis.
2. **The next natural restart activates both D-329 measurements** (finding 2).
   Until it happens, no fill-provenance claim can be made about anything booked
   since 00:56 EDT, and convention 32 is unenforceable rather than satisfied.
3. Decide whether 036 lands before 037 is considered at all. My recommendation:
   yes, and 037's Phase 0 opportunity count is the cheapest real test in the
   queue - no capital, no adapter, stored data only.
4. Re-derive the `PM_temporal_arbitrage` calibration line in CLAUDE.md, or drop
   the "0.06 percentage points" number from it.
5. The `AGENT_ID` spawn gap above.
