---
name: "pm_counterfactual_source_filter_gap"
thesis: "Proposal 043's kill condition states its admissible evidence in one unambiguous sentence: 'It grades ONLY positions whose market-side resolution is present in `market_resolutions` with `source` = `venue`, never sibling inference.' The instrument that implements it does not enforce that. `backtest/settlement_coverage.py:603` defines `counterfactual(conn, since_ms=None, sources=LIVE_SOURCES)`, and `LIVE_SOURCES` at `engine/polymarket/resolution_ledger.py:110` is `(SOURCE_VENUE, SOURCE_INFERRED_TERMINAL_PRICE)` - two sources, not one. This is not inferred from reading code: the tool prints its own filter, and both invocations this session emitted `ledger rows 780, sources ['venue', 'inferred_terminal_price']` and `ledger rows 372, sources ['venue', 'inferred_terminal_price']`. The graded arm is therefore wider than the proposal that defines it by exactly one source. The defect is LATENT and not live, and the honest statement of its current impact is zero: `SELECT source, COUNT(*) FROM market_resolutions GROUP BY source` returns `venue` and only `venue` in both databases - 786 rows in db/trading.db and 390 in db/trading-survivors.db at 2026-08-20T04:2xZ - and `SOURCE_INFERRED_TERMINAL_PRICE`'s own docstring says 'Defined by rule 3 and accepted by the writer; nothing produces it today. The venue field made a price reader unnecessary.' So no number anyone has read is affected, no verdict is contaminated, and nothing needs re-running. What makes it worth a proposal rather than a comment is what happens the day something does write that source, because the failure would be silent in three places at once. First, 043's kill condition would be violated by its own instrument with no error raised - the tool would grade rows the proposal excludes and would report a verdict in the proposal's name. Second, and worse, rule 6's mandatory self-check would quietly lose the independence it rests on. That self-check is the instrument's only error bar, and the code states its warrant explicitly at `settlement_coverage.py:717`: 'the paper adapter took the price from Gamma's outcomePrices, the ledger reads the CLOB's winner field. Different endpoint, different field, different failure modes - so where they overlap they are a real check and not a tautology.' Every clause of that warrant is a claim about `source = 'venue'` specifically. An `inferred_terminal_price` row is by its own definition 'taken from a terminal BOOK price at or after window close' - a price reading of a settled market, which is the same KIND of quantity as Gamma's `outcomePrices` and subject to the same failure modes. Admitting those rows would move the self-check from comparing a winner FIELD against a price READING to comparing one price reading against another, and the disagreement rate would fall for reasons that have nothing to do with the ledger being more correct. An error bar that shrinks when the instrument gets worse is the most dangerous single failure available to this design. Third, the two would compound: a narrower apparent error bar would make 043's 0.010 band look better justified at the exact moment it was least justified, which interacts directly with the sampling-error defect proposal 046 files this cycle. The root cause is traceable and is not carelessness. `LIVE_SOURCES` was authored for proposal 038's rule-2 COVERAGE metric, and its docstring says so: 'Sources that count toward the rule-2 coverage number in 038's kill condition. Backfill is deliberately absent.' It is exactly right for that job. 043 reused it as a default for a different metric whose own kill condition specifies something strictly narrower, and the reuse is invisible at the call site because the constant's name describes what it excludes rather than what it admits."
expected_edge_bps: null
kill_condition: "This is a repair and records no edge (convention 11). It changes no strategy, no threshold, no verdict and no number currently on the record - by construction, since the source it excludes has zero rows in both databases. The repair is DONE when all three of the following hold. (1) `counterfactual()` in `backtest/settlement_coverage.py` defaults to a VENUE-ONLY source set - a new named constant, not a literal at the call site and not a re-definition of `LIVE_SOURCES`, which must keep its current membership because proposal 038's coverage metric depends on it and is a separate kill condition that is correct as written. (2) The counterfactual REFUSES rather than filters: if any row in `market_resolutions` carries a source outside the graded set, the report prints that source and its row count in the header instead of silently omitting it, so a reader learns the ledger has non-venue content rather than seeing a number that quietly dropped it. Silent exclusion and silent inclusion are the same defect facing opposite directions (convention 20: a silent continue is a missing number). (3) A test pins it: assert that `counterfactual()` invoked against a fixture ledger containing one `venue` row and one `inferred_terminal_price` row for the same market-side grades ONLY the venue row, and that the header reports the excluded source. The test must construct the `inferred_terminal_price` row explicitly, because no live database contains one and a test that relies on live data would pass today for the wrong reason and forever. The repair is measurably WRONG and must be rolled back if, after it lands, `backtest/settlement_coverage.py --counterfactual` reports a matched count on either database that differs from the pre-repair count by anything other than ZERO - measured 143 and 126 matched `sell:salvage_floor` positions at 2026-08-20T04:2xZ, with the caveat that both databases are live under read and the baseline must be re-derived immediately before and after in one session rather than compared against these figures (convention 25). A non-zero delta would mean a non-venue row existed and was being graded, which would make this defect live rather than latent and would require every counterfactual reading since the ledger landed to be re-derived before any of them is quoted again. SEPARATELY and referred to Raven, not decided here: whether the correct resolution is to tighten the CODE to match 043's text, which is what this proposal implements and recommends, or to loosen 043's TEXT to match the code. The recommendation is to tighten the code, because 043's sentence is the ruling artefact and because the self-check's independence argument is written for the venue field specifically and does not survive the wider set. If neither is done within 14 days, record NOT_TESTED and requeue - the defect stays latent and harmless for exactly as long as nothing writes the second source, and its whole cost is that the day that changes is not a day anyone will be watching for."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  0. Scope. This changes NO entry rule, NO exit rule, NO sizing, NO gate and NO strategy parameter. It changes one default argument and adds one header line and one test. It does not grade the salvage counterfactual and does not carry any counterfactual reading forward as a finding.
  1. Do NOT change `LIVE_SOURCES` itself, and do not remove `SOURCE_INFERRED_TERMINAL_PRICE` from it. That constant serves proposal 038's rule-2 coverage number, which is a different kill condition on a different question, and which is correct as written: for COVERAGE, an inferred terminal price is a legitimately recovered resolution and counting it is right. The two metrics genuinely need different source sets, which is precisely why the graded one needs its own named constant rather than borrowing.
  2. Do NOT delete `SOURCE_INFERRED_TERMINAL_PRICE` from the vocabulary or from the writer's accepted set. It is defined by proposal 038 rule 3 and is a legitimate future path; the module docstring records that the venue's `tokens[].winner` field made a price reader unnecessary, which is a statement about today and not a decision to never build one. Deleting a defined-but-unused source would trade a latent grading defect for a latent write-rejection defect.
  3. The header change reports EXCLUDED sources by name and row count, never a bare total. `ledger rows 786, sources ['venue']` and `ledger rows 786 of 812, excluded: inferred_terminal_price 26` carry different information and the second is the one a reader needs. This is the same requirement 043 rule 5 imposes on match rates and for the same reason.
  4. Apply the graded source set to the rule 6 SELF-CHECK as well as to the graded arm, and not merely to the arm. The self-check is computed inside the same walk over the same `_ledger_map`, so it inherits whatever filter that map was built with; if the two ever diverge, the instrument would be checking itself against a population it does not grade. They must be the same set by construction, not by coincidence.
  5. Do NOT wire any strategy to read this output. Same rule as 043 rule 8, 038 rule 6 and 042 rule 7, for the same reason: resolution is knowable only after the window closes, so a strategy consuming it is look-ahead by construction. Consumers are `backtest/` and `agents/forge_shadow_eval.py` only.
  6. Both databases get the identical treatment on their own `--db` and their source censuses are reported separately, never summed (convention 32).
data_requirements: |
  HAVE, verified read-only at 2026-08-20T04:2xZ: the source census that establishes the defect is latent rather than live. `SELECT source, COUNT(*) FROM market_resolutions GROUP BY source` returns exactly one row in each database - `venue` 786 in db/trading.db and `venue` 390 in db/trading-survivors.db. Both counts moved during the session (780 and 372 an hour earlier) because both books are live under read; the composition did not.
  HAVE: the tool's own declaration of its filter. `backtest/settlement_coverage.py --counterfactual` prints `sources ['venue', 'inferred_terminal_price']` in its header on both databases, so the discrepancy is observable from stdout without reading the source. This is what turned a suspicion into a measurement.
  HAVE: the constant definitions and their docstrings - `engine/polymarket/resolution_ledger.py:94` `SOURCE_VENUE`, `:99` `SOURCE_INFERRED_TERMINAL_PRICE` with its 'nothing produces it today' note, `:104` `SOURCE_SIBLING_INFERENCE_BACKFILL`, `:110` `LIVE_SOURCES`, `:116` `RESOLUTION_SOURCES`; and the call sites at `backtest/settlement_coverage.py:196` (coverage), `:263` (disagreements) and `:603` (counterfactual), all three defaulting to `LIVE_SOURCES`.
  HAVE: the self-check's stated independence warrant at `backtest/settlement_coverage.py:717`, which is the sentence that makes the venue-only requirement substantive rather than pedantic.
  MISSING, and it is why the kill condition asks for a constructed fixture rather than a live assertion: any `inferred_terminal_price` row anywhere. There is nothing to test against in either database and there never has been. A test that exercises the exclusion must build the row itself.
  NOT NEEDED: any database migration, any backfill, any re-derivation of past readings. The graded population is unchanged by this repair - that is the repair's own success criterion, stated as a zero-delta check in the kill condition.
  NOT NEEDED: `market_tape`, the calibration tape, `market_duration`, TWAP conditioning (see proposal 045), or the taker fee schedule.
markets: "Polymarket crypto Up/Down 5m and 15m windows - the population proposal 043 grades. Instrument-level repair; no market selection of its own. Both databases treated separately (convention 32)."
kind: repair
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

**RULED - 2026-08-20, D-356 R3 (`cody-tick6-rulings`). ACCEPTED and BUILT;
the arrow points at the CODE.** Raven confirmed the header claim live - the tool
printed `sources ['venue', 'inferred_terminal_price']` on both databases while
`market_resolutions` GROUP BY source returned only `venue` - and decided the
referred arrow question in the direction this proposal recommended: the CODE is
tightened, not 043's text. The reason is the one given here, that the
self-check's independence warrant is written for the winner FIELD specifically,
so a terminal book price fails it regardless of what 043's author meant by
"venue".

BUILT this session. `COUNTERFACTUAL_GRADED_SOURCES = (SOURCE_VENUE,)` is a new
named constant in `engine/polymarket/resolution_ledger.py`;
`LIVE_SOURCES` is UNCHANGED and still carries both sources, because 038's
coverage metric depends on it (rule 1); `SOURCE_INFERRED_TERMINAL_PRICE` is not
deleted from the vocabulary (rule 2); the report names excluded sources and
their row counts in its header instead of filtering silently (rule 3); the
self-check gets the same set by construction (rule 4).

**One clause of the kill condition could not be satisfied as worded, and the
reason is a schema fact this proposal did not know.** Clause 3 asks for a fixture
holding "one `venue` row and one `inferred_terminal_price` row for the same
market-side". `market_resolutions` carries `UNIQUE (market_slug, outcome_side)`,
verified this session by direct insert (`IntegrityError: UNIQUE constraint
failed`), and `write_resolutions` uses `INSERT OR IGNORE` - so a market-side
holds exactly ONE row whatever its source and the first writer wins. The
same-market-side contamination this proposal describes is therefore
IMPOSSIBLE, and the realisable failure is whole ADDITIONAL market-sides entering
the graded arm and the self-check. That is the mechanism that actually carried
the argument, and it is what the fixture builds: one venue market-side, one
inferred_terminal_price market-side, asserting the default grades ONE while
`sources=LIVE_SOURCES` grades TWO, so the test passes for the right reason
rather than because the second row is missing. A separate test pins the UNIQUE
constraint itself so this reads as a deliberate deviation and not a weakened
test.

The zero-delta rollback check PASSES on both books, but not as worded: a
pre-repair and post-repair count minutes apart measure LIVE DRIFT, not the
repair, and a first attempt showed exactly that - `narrow` matched 429 against
`wide` 428 in environment B, a direction a NARROWER filter cannot produce. Re-run
with both source sets read inside ONE pinned WAL snapshot, the matched counts are
IDENTICAL: 964 = 964 in environment A and 433 = 433 in environment B, with
self-check positions identical at 332 and 123. The census confirms why it had to
be zero - both ledgers are venue-only, 840 and 438 priced rows, zero excluded -
so the defect was latent exactly as filed and nothing needed re-running.

> **LATENT, NOT LIVE. Nothing measured is wrong.** `market_resolutions` holds
> `venue` rows and only `venue` rows in both databases - 786 and 390 at this
> snapshot - and the source this proposal excludes has never been written by
> anything. No counterfactual reading is contaminated and nothing needs
> re-running. The zero-delta check in the kill condition is how that claim gets
> verified rather than trusted.

> **The gap is one constant.** 043 says it grades `source = 'venue'`. The
> instrument defaults to `LIVE_SOURCES`, which is
> `('venue', 'inferred_terminal_price')`. The tool prints the wider set in its
> own header on every run.

## Why a latent defect is worth filing

Because of which mechanism it disarms, and because the cost of fixing it is
currently zero and will never be lower.

The counterfactual's rule 6 self-check is the only error bar 043 has. Its
warrant is written into the code beside it, and it is a claim about two
*different kinds* of measurement agreeing:

> the paper adapter took the price from Gamma's `outcomePrices`, the ledger
> reads the CLOB's `winner` field. Different endpoint, different field,
> different failure modes - so where they overlap they are a real check and not
> a tautology.

Every clause there is true of `source = 'venue'` and none of it is true of
`source = 'inferred_terminal_price'`, which is defined as "taken from a
terminal BOOK price at or after window close". A terminal book price and
Gamma's settled `outcomePrices` are two price readings of the same resolved
market. They would agree more often than a price reading and a winner field
do - not because the ledger became more correct, but because the comparison
stopped being independent.

So the failure mode is: someone wires the terminal-price reader that 038 rule 3
already authorises, the ledger gains a second source, the graded arm silently
widens, **and the self-check's disagreement rate falls**. A shrinking error bar
reads as an instrument getting better. It would be an instrument losing the
ability to detect its own errors, and the 0.0500 self-check threshold in 043's
kill condition would be passed more comfortably than ever.

That interacts directly with proposal 046, filed this cycle: 046 shows the
0.010 band is already narrower than one sigma of sampling error. A self-check
that also understates measurement error would leave both of 043's error budgets
optimistic simultaneously.

## The root cause is a name

`LIVE_SOURCES` is not a careless constant. It was authored precisely, for
proposal 038's coverage metric, and its docstring explains its exclusion:

> Sources that count toward the rule-2 coverage number in 038's kill condition.
> Backfill is deliberately absent: coverage is measured on markets FETCHED
> AFTER the ledger lands, and counting recovered history toward it would let
> the repair pass on data that predates it.

It is exactly right for coverage. For coverage, an inferred terminal price *is*
a recovered resolution and should count. The name describes what it excludes -
backfill - and says nothing about what it admits, so at the `counterfactual()`
call site it reads as "the sources that are live", which is true and is not the
question 043 asks. 043 asks for the sources that are *observations of the
venue's own resolution*, which is a strictly smaller set.

Two metrics, two correct-but-different source sets, one shared default. The fix
is a second named constant, not a change to the first.

## Why this repair might be wrong

The strongest objection is that I may have the direction backwards. It is
entirely possible that 043's sentence was loose rather than load-bearing - that
its author wrote "venue" meaning "not sibling inference", which is what
`LIVE_SOURCES` already delivers, and that no narrowing was ever intended. The
sentence does continue "...never sibling inference", which is at least
consistent with that reading and is the only exclusion it names explicitly.

If that is right, the correct repair is to fix the TEXT, not the code, and this
proposal has the arrow pointing the wrong way. I have recommended tightening the
code anyway, for one reason that I think survives the objection: the
self-check's independence argument is written for the winner field specifically,
and that argument does not hold for a terminal price reader regardless of what
043's author intended by the word. So even under the loose reading, venue-only
is what the instrument's own error bar requires. But the ruling is Raven's and
the kill condition says so.

Second, I have asserted that a terminal book price is not independent of
Gamma's `outcomePrices` without measuring it, and I cannot measure it, because
no row of that source exists. It is an argument from what the two quantities
are, not from their observed correlation. If the terminal-price reader were
built against a genuinely different endpoint with genuinely different failure
modes, the independence might survive. That is a real possibility and it is why
rule 2 refuses to delete the source rather than merely excluding it.

Third, this is a small finding and I am aware it reads as larger than it is
because it is written at length. Its live impact is zero rows. I have filed it
at this size because the failure it prevents is silent in three places at once
and because the window in which it costs nothing to fix is open now and closes
the moment the reader lands - not because it is urgent today.

## What past failure this addresses

It addresses the shape convention 20 names - a silent `continue` is a missing
number - applied to a filter rather than a loop. A source filter that admits
more than its proposal specifies produces no error, no warning and no visible
difference until the extra source is non-empty, at which point it produces a
number that is wrong in a direction nobody is watching.

It also addresses, in miniature, the failure CLAUDE.md records as the dead
nested test at `6666199`: a mechanism that stopped doing its job while every
green signal stayed green. The dead test passed by not running. This filter
would pass by filtering a set that happens to be empty. Both are cases where
the absence of a symptom is not evidence the mechanism works, and both are only
catchable by checking the mechanism directly rather than its output - which is
what the constructed fixture in the kill condition's clause 3 is for.

## Forge warnings (non-blocking)

- **no_graveyard_link_warning**: no related graveyard finding. Expected for
  PREDICTION_MARKET; the graveyard is crypto spot and perp. The engaged prior
  work is proposals 038 and 043 and the `resolution_ledger` module's own source
  vocabulary, cited by line number rather than by graveyard id.
