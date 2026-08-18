# strategies/proposals/

Forge's output. Strategy hypotheses, not strategies.

Nothing in this directory is executable and nothing in it has been tested. A
file here is an argument that a strategy is worth building, addressed to a human
who decides whether to build it. Code lives in `strategies/builtin/` and
`strategies/polymarket/`; verdicts live in the graveyard.

## Why the separation exists

The author cannot be the referee. Forge generates hypotheses, Judge grades
results, and the gap between them is the only thing keeping the numbers from
being self-serving. If Forge could ship its own idea straight into a sweep it
would be grading its own homework, and every downstream figure would inherit
that.

So: Forge writes here. A human reads. Only then does anyone write code.

## Lifecycle

```
PROPOSED  ->  ACCEPTED  ->  BUILT  ->  (graveyard verdict)
     \
      ->  REJECTED
```

`status` in the frontmatter is the single source of truth for where a proposal
is. A REJECTED proposal stays in the directory. Deleting it loses the record
that the idea was considered, and the next Forge run proposes it again.

## The schema

Every file is `NNN-slug.md` with YAML frontmatter carrying nine mandatory
fields. `agents/forge.py` enforces them at write time, so a proposal that
violates one never reaches this directory - it appears in the run log as a
refusal with a category instead.

| Field | Meaning |
|---|---|
| `name` | snake_case identifier, unique against all 55 swept strategies |
| `thesis` | one sentence: what inefficiency, and why it persists |
| `expected_edge_bps` | gross, before costs. Null only for a repair |
| `kill_condition` | the specific measurement that ends this strategy |
| `asset_class` | CRYPTO, EQUITY, ETF, FUTURES, OPTIONS, PREDICTION_MARKET, MULTI |
| `entry_exit_rules` | entry trigger, stop, target, time exit. Concrete |
| `data_requirements` | every field and feed needed, including ones we lack |
| `related_graveyard_findings` | what is already buried in this family |
| `status` | PROPOSED, ACCEPTED, REJECTED, BUILT |

Plus `kind`, which is `edge_hypothesis` or `repair`.

## Two rules that are enforced in code, not by convention

**The 30bps floor.** An `edge_hypothesis` claiming under 30bps gross is refused
at write time (convention 5). Below that the cost model eats it and the only
question left is how long it takes to find out.

**A repair records a null edge, not a zero.** A `repair` fixes a strategy that
does not currently fire. Its edge is unknown until it fires, and convention 11
says unknown is not zero. `agents/forge.py` refuses a repair that names a
number, because an invented figure in a proposal becomes a cited figure two
documents later.

## Reading the run log

`forge_runs.jsonl` is append-only, one JSON object per run. It records every
candidate SCREENED, not just the ones written, and every refusal carries a
category (convention 20: a skip that is not counted did not happen). The
accounting identity `screened - refused == written` is asserted in code.

The full search is the result. Five proposals written out of fifty screened is
a report of fifty, and the run log is where that shows.

## Running Forge

```bash
env -u PYTHONPATH python3 agents/forge.py --gaps-only   # gap analysis, writes nothing
env -u PYTHONPATH python3 agents/forge.py               # generate proposals
```

`--gaps-only` prints what Forge sees in the graveyard: which strategies are not
firing, which asset classes have no coverage, which silent assertions are
failing, and the worst pooled performers. That is the input to the LLM half of
Forge (`agents/forge/forge.agent.md`), which writes the argument that
`agents/forge.py` then validates and files.

## A BUILT strategy is not the same as a strategy NAMED AFTER a proposal

`status` is per-proposal, and it is only `BUILT` when the code implements the
proposal's own hypothesis. Sharing markets, or even sharing a filename, is not
implementing it.

Current state, so nobody has to re-derive it:

| Proposal | Status | Note |
|---|---|---|
| 002 pm_temporal_arbitrage | BUILT | `strategies/polymarket/temporal_arbitrage.py`. Deviations are tightenings, all in the docstring (D-280). |
| 005 pm_cross_window_relative_value | **PROPOSED, UNBUILT** | See below. |

**Proposal 005 is PROPOSED and unbuilt, and the thing that looks like it is
not it.** `strategies/polymarket/corridor_pair_live.py` was originally called
`cross_window_relative_value.py`, which read as an implementation of 005. It is
not one. 005 is a ONE-LEG relative-value bet with no floor that can lose its
whole premium; `corridor_pair_live` is a TWO-LEG floored pair that cannot lose
both legs. Same two markets, opposite risk shape - 005's own
`related_graveyard_findings` says so and instructs that the two never be pooled.

005 stays unbuilt because its `data_requirements` name a BLOCKER: the score
needs 30 days of PAIRED 5m/15m history, and the mean and stdev in that score are
measured quantities, not tunable constants. Until they are measured the proposal
has no entry rule at all. Freezing a guessed mean and stdev into constants would
be the `COST_FLOOR = -0.30` mistake (convention 17), so nothing invents them.

The module, the class and the `strategy_name` were all renamed away from 005 -
to `corridor_pair_live` / `CorridorPairLive` / `PM_corridor_pair` (D-281) -
precisely so that no graveyard row, dashboard line or handoff can be read as a
measurement of proposal 005. D-281 rules only the `strategy_name` key; the
module and class keep the `_live` suffix. **No result from that strategy is
evidence for or against 005, in either direction.**

## What a reviewer should check

1. Does the kill condition name a number and a harness that can measure it?
2. Is the edge arithmetic shown, or asserted?
3. Does `related_graveyard_findings` engage the burial reason, or dodge it?
4. Are the data requirements we do NOT have flagged as blockers?
5. For a Polymarket proposal: are entry price and win rate stated together?
   Either alone is meaningless on a binary (D-267).
