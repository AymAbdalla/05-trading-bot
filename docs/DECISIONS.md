# Decisions Log

## v10 - 2026-08-14 (D-226 duplicate_strategies superseded)

### D-266. D-226 duplicate_strategies finding superseded by post-purge analysis (Raven)

D-226 recorded that `duplicate_strategies` flagged breakout_20 and
momentum_continuation as 99.4% identical, concluding "one idea counted twice,
inflating breadth." That was accurate for the 218,295-entry graveyard it ran
against.

The post-purge 535,425-entry graveyard (55 strategies) tells a different story.
C2 pairs with all 54 other strategies at `identical_fraction` 1.0, because C2
produces zero trades in all 264 rows it is compared on. The next highest members
of the duplicate list (V2_vwap_magnet_sessionatr, V5_capitulation_equity,
V4_gap_hold_proxy, V4_trend_reclaim, rising_three_methods, rsi_extreme,
V3_intraday_momentum_crypto, V5_forced_flow_crypto) are the same eight
strategies that top the zero-trade list at 99%+.

Conclusion: `duplicate_strategies` and `trade_count_sanity` are one problem.
8 of 55 strategies do not fire. They contribute no PASS rows, so they are not
inflating the 155 distinct findings. The real cost is that 8 strategies were
never tested and are sitting in the graveyard looking like verdicts. Fixing the
firing should clear most of both assertions at once.

D-226 is left as historical record. This entry supersedes its
duplicate_strategies characterization only; D-226's other findings
(trade_count_sanity, quarantine_canary) still stand as written for the
218,295-entry graveyard.

## v9 - 2026-08-14 (Aym decisions: purge confirmed, fee model, Alpaca, paper run)

### D-261. Aym confirms the FUTURES purge (AYM RULING)
Aym confirmed: purge all contract rows. `run_post_sweep_repair.sh --confirm`
launched 2026-08-14. Drops 23,595 FUTURES/OPTIONS rows (including 51
PASS/PASS_BENCHMARK), rebuilds under current code (D-249 fix), emits judge pack.
EQUITY/ETF/CRYPTO untouched.

Actual: 23,595 rows dropped (51 PASS/PASS_BENCHMARK), per
`logs/post_sweep_repair.log`. The 12,936 written here before the run was an
estimate taken from an earlier, smaller graveyard; the live file was 535,425
entries and went to 511,830. Factual correction only, no version bump. Earlier
entries (D-259, D-254, D-249) still say 12,936 and are left as written - they
record what was believed at the time.

### D-262. Alpaca key rotation: Aym already rotated, will rotate again (AYM RULING)
Aym states he already rotated the Alpaca key since D-110 was logged. He will
rotate again. The .env file lives at `~/aym/projects/05-trading-bot/.env` and
is gitignored. Keys to store there:
- `ALPACA_API_KEY` (paper trading API key from Alpaca dashboard)
- `ALPACA_API_SECRET` (paper trading API secret)
- `ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2`
These are paper-mode market data keys only. No live trading keys exist.
D-110 closed.

### D-263. Binance.US live fee verification is RETIRED as a checklist item (AYM RULING)
The cost model now sources from `references/broker-fee-reference-2026.md`, the
verified fee reference Aym supplied. That file replaces the old "verify
Binance.US live fee page" checklist item. The cost model's `verified: False`
flag on crypto should be updated to reflect the broker-fee-reference as the
source of truth. D-236's framing still holds: backtesting measures edge outside
brokerage constraints; venue-specific verification matters for shadow testing,
not backtest cost modeling.

### D-264. Paper run and kill-switch drill deferred; focus on backtesting (AYM RULING)
Aym's directive: focus on backtesting all strategies and getting judge up to
speed. The supervised paper run and kill-switch drill are deferred until the
graveyard analysis is complete and judge is producing evidence packs regularly.
Not blocking. Not forgotten. Just not now.

### D-265. Broker fee reference is the single source of truth for costs (AYM RULING)
`references/broker-fee-reference-2026.md` is the canonical cost model input.
Any cost model code that references a different source or assumption should be
updated to read from this file. The four cost regimes (crypto percentage,
equity spread, options per-contract, futures stacked fixed) are the model.

## v8 - 2026-08-13 late evening (sweep triage, judge.py honesty fix)

### D-258. judge.py's sub-sections report their own failure too (CC)
Follow-up audit after D-255. `assertions.run_all` and
`summarize_graveyard.summarize` are called with the graveyard PATH, so they
re-read it independently of `load_graveyard` - two more chances to hit a
partial write during a live sweep. Both were wrapped in
`except: ... = None`, so a failed read silently removed a section from the
pack. `distinct_findings` comes from that summary, and convention 2 requires
citing it - which is how a pack ends up with no multiple-comparisons
correction and nothing marking its absence.

Fix: `_attempt()` retries and returns `(value, error)`, and the pack carries a
`degraded` list naming each section that could not be produced and why (None
when clean). Status is deliberately NOT downgraded - DURABLE is a statement
about harness validation, and overloading it would blur what it means. The
grep behind this found no other instances: judge.py held the only remaining
`JSONDecodeError` handlers outside the new purge tool.

### D-259. The purge tool is tested before it is ever pointed at real data (CC)
`tests/test_purge_stale_futures.py`, 15 tests. It deletes 12,936 real rows, so
the tests pin the destructive edges rather than the happy path: contract-only
scope (EQUITY/ETF/CRYPTO must survive byte-for-byte, not merely survive), dry
run writes nothing and creates no backup, the running-sweep guard refuses and
leaves the file untouched, `pgrep` failing fails CLOSED, the backup is the
pre-purge file, no `.tmp` survives, header counters are recomputed while
unrelated header fields survive, and an unreadable graveyard exits loudly
instead of looking like a successful no-op.

Verified the tool's keying assumption against the live file rather than
assuming it: all 294,294 rows carry `asset_class`, and the `_F` ticker suffix
maps 1:1 onto FUTURES (12,936 by both measures). So no ticker-based fallback
was added - it would have been speculative code in a deletion path.
Suite: 559 passed, 1 skipped.

### D-260. Post-sweep repair exists as a script but is NOT armed (CC)
`backtest/run_post_sweep_repair.sh`: waits out the sweep AND the queued chain,
dry-runs the purge into the log, applies it, rebuilds (futures under fixed
sizing plus the v4/v5 backfill in one pass), then emits a judge pack. Aborts
before the rebuild if the purge fails, leaving the graveyard unchanged or
restorable from the archive.

Unlike `run_queued_chain.sh` it does not launch itself and requires an
explicit `--confirm`, because step 2 is a deletion Aym owes a call on. It also
waits for the CHAIN, not just the sweep - purging underneath the chain would
delete rows its dispersion/horizon/PLR stages are mid-way through using. Uses
`env -u PYTHONPATH python3` throughout per D-257.

Note for whoever edits it: do NOT edit `run_queued_chain.sh` while PID 69639
is executing it. Bash reads a script by byte offset as it runs, so editing a
live script can make it execute garbage. That is why the repair sequence is a
new file rather than three lines appended to the chain.

### D-253. The running graveyard sweep is stale-code; let it finish anyway (CC)
`run_incremental_graveyard.py` (PID 63767) started 16:01 and Python snapshots
source at import, so the 6h+ run in flight is executing 16:01 code. It
therefore predates: v4 (16:19), v5 (16:45), the runner's own edit (16:47),
and - the one that matters - the D-249 sizing fix in `cost_model.py` (17:45).
Confirmed, not inferred: the log reports "49 new strategies" per series and
the graveyard header says `strategies_tested: 49`, which is exactly
28 expanded + 7 lab + 9 v2 + 5 v3, with v4's 3 and v5's 2 absent (54 today).

Decision: do NOT kill it. Only FUTURES/OPTIONS rows are affected by D-249
(`InstrumentSpec.is_contract` is true for those two alone), and those are
12,936 of 287,826 rows. The other ~96% - EQUITY, ETF, CRYPTO - never enter
the contract-sizing path and are good work. Killing the sweep would discard
them to fix a bucket that has to be re-run either way. The missing v4/v5
rows are benign: the queued chain's incremental pass backfills them under
current code.

### D-254. Purge all contract rows rather than date-splitting them (CC)
`backtest/purge_stale_futures.py`. The D-249 fix landed WITHOUT a
`COST_MODEL_VERSION` bump - the tag is '2026-08-13' on both sides of it, and
all 287,826 rows carry that single value. So the "never pool across
cost_model_version" convention cannot separate pre-fix from post-fix rows,
and nothing else in the row metadata can either.

Rather than invent a distinction the data does not support, the tool drops
every FUTURES/OPTIONS row and lets the incremental runner rebuild them under
current code. Blunt, but correct whichever side of the fix a row came from.
EQUITY/ETF/CRYPTO are explicitly left alone. No version bump either: after
the purge every surviving contract row is post-fix by construction, and a
bump would falsely mark ~275k unaffected rows as stale.

Guard: the tool refuses to run while `run_incremental_graveyard.py` is alive,
because that runner holds the whole graveyard in memory and rewrites it after
every ticker - a purge landing mid-sweep is clobbered on the next save.
Writes are atomic (`os.replace`) and back up to `research/graveyard/archive/`
first. NOT YET RUN - it is armed for after the sweep and chain finish.

### D-255. judge.py must not report unreadable evidence as no evidence (CC)
Found by running it: `judge.py` emitted `status: DURABLE, entries: 0` against
a 287k-entry graveyard. `load_graveyard` caught `json.JSONDecodeError` and
returned `[]`, so a read landing mid-`json.dump` (the sweep rewrites 12MB
after every ticker) was laundered into a confident empty pack. That is
exactly the conflation convention 11 bans for verdicts - "could not run" is
never "ran and found nothing" - applied to the evidence layer instead.

Fix: `load_graveyard` retries (the partial-write window is short), then
raises `GraveyardUnreadable` rather than lying; `build_evidence_pack` catches
it and returns `status: UNREADABLE`, which no green harness can upgrade to
DURABLE. Missing/genuinely-empty files still return `[]` as before. The old
test asserted the buggy behaviour and was replaced by three: raises-on-
malformed, retries-then-succeeds, and never-durable-when-unreadable.
Suite green: 543 passed, 1 skipped, 544 collected.

### D-256. The constraint sweep does not show that selectivity creates edge (CC)
The script's own DIAGNOSTIC line ("Tightening the gate IMPROVES per-trade PnL
by +1.7174... selecting for something real") overstates its result on two
counts, both visible in `research/graveyard/constraint_sweep.json`:

1. It is not monotonic. AGGRESSIVE -$0.1793/trade, BASE -$0.4543,
   CONSERVATIVE +$1.5380. Tightening AGGRESSIVE->BASE makes it *worse*. A
   real selectivity effect reads AGGR < BASE < CONS; this is U-shaped, and
   the headline number is just the two endpoints subtracted.
2. It is two strategies. dca_7 (195 trades) and dca_14 (87 trades) are 5.6%
   of CONSERVATIVE's trades and 78.5% of its profit. Drop them and $/trade
   falls +1.5380 -> +0.3502. The rest of the tail is noise by convention 7:
   V2_expiry_pin is 15 trades at $59.51, rsi_extreme is 3 trades.

CONSERVATIVE fires 5,045 trades against AGGRESSIVE's 836,072 - 165.7x
selectivity for a bucket thin enough that a couple of DCA variants set its
sign. Recorded as NOT SUPPORTED, not as a negative result: the honest read is
that the sweep is underpowered at the conservative end, not that selectivity
is disproven. `research/2026-08-13-constraint-sensitivity-PRELIMINARY.md`
should stay PRELIMINARY.

### D-257. PYTHONPATH leaks from Hermes into spawned Cody sessions (CC, ops)
`python3` in this session failed to import numpy: `PYTHONPATH` was set to
Hermes's venv, putting `.../venv/lib/python3.11/site-packages` (numpy 2.4.6,
cpython-311 binaries) ahead of the correct 3.9 site-packages (numpy 2.0.2).
The machine is fine - it is in no shell rc file, and `env -u PYTHONPATH
python3` imports cleanly - and the 16:01 sweep and the queued chain are
unaffected because they were started from clean shells.

It matters because a Raven-spawned headless Cody hits the same contaminated
environment and its runs die on an ImportError that looks like a broken
numpy install. Any python3 invoked from an agent-spawned session should be
`env -u PYTHONPATH python3`. Not fixed in-repo: this is an environment
artifact, not a project bug, and hard-coding a workaround into the scripts
would hide it.

## v7 - 2026-08-13 evening (governance formalized, judge.py + sweep-loop batch)

### D-249. Futures/options contract sizing actually wired into the cost model (CC)
Real bug was one layer deeper than ROADMAP P0.4's "STILL TO DO" implied:
`TradeCoster` (D-235) already imported `instruments.spec_for` and computed
margin as capital-at-risk correctly, but `contracts` was a hardcoded
default of 1 that the constructor then floored to a minimum of 1
(`max(int(contracts), 1)`), so `instruments.InstrumentSpec.size_for`'s
affordability check was imported but structurally unreachable - futures
always traded exactly one contract regardless of whether the configured
notional cap could afford the margin. `tests/test_cost_model.py`'s own
`test_harness_futures_trade_one_contract_on_margin` pinned this as
"correct" at the harness's $100 default, i.e. the test encoded the bug.
Fixed at the shared `TradeCoster.__init__`/`CostModel.coster()` layer:
`contracts` now defaults to `None`, meaning "size it honestly via
`spec.size_for(notional_cap, price)`," floored at 0 (not 1) so
unaffordable really means unaffordable and feeds the harness's existing
`qty <= 0 -> skip` path. Fixing it at this shared layer means
`vectorized_harness.py`, `harness.py` (event engine), and
`cross_sectional.py` all inherit the fix without three separate patches.
Split the old test into two: unaffordable-at-$100 (produces no trades) and
affordable-at-$2,000 (ROADMAP's own example: one MES contract, $1,800
margin) - both pass. Also added `pooled_analysis.pool()`'s default
exclusion of FUTURES/OPTIONS from dollar-pooled cells (they were never
excluded there, only in `cross_sectional.py`), matching the "denominators
are different quantities" rule P0.4 states; `asset_class_analysis.py`
remains where those rows are actually analyzed, keyed by class. Full test
suite green (details in the matching handoff). Existing graveyard futures
rows were produced under the old bug and need a targeted re-run once the
live sweeps finish - flagged in ROADMAP P0.4, not done here since deleting
graveyard entries to force a re-test is a deliberate call, not a silent
side effect of a code fix.

### D-248. C2 WeekendVacuumReversion: fixed the wrong-question NOT_TESTED gate (CC)
`strategy_lab.py`'s C2 needs 840 bars; `vectorized_harness.SCAN_WINDOW` is a
flat 260 shared by all 35 strategies for O(1)-per-bar scan cost. The old
gate rejected ANY strategy with `min_bars > SCAN_WINDOW` outright as
NOT_TESTED, even on a series with thousands of bars available - it was
answering "does this strategy fit in the shared window" instead of "could
this run at all." Fix: `scan_all_bars` now hands each strategy its OWN
window, `max(SCAN_WINDOW, strategy.min_bars)`, so C2 gets 840 bars while
the other 34 strategies' cost is unchanged; `run_sweep`'s NOT_TESTED check
now asks whether THIS series has enough bars (`ind.n < min_bars`) rather
than comparing against the global window constant, with
`MAX_STRATEGY_WINDOW = 2000` kept as a defensive ceiling against a
hypothetical strategy asking for something unreasonable. A strategy that
clears both checks but still fires zero signals now correctly reports
FAIL/0-trades like any other strategy, not NOT_TESTED - NOT_TESTED means
"could not run," never "ran and found nothing" (D-109). Two existing tests
that pinned the old blanket-rejection behavior updated; one new test proves
the window actually widens (`max(windows_seen) > SCAN_WINDOW`) without
depending on synthetic data that happens to satisfy C2's firing gates - a
separate, larger fixture-engineering problem this pass does not attempt.
C2's powered result rides the graveyard's next natural incremental pass;
no separate run triggered here.

### D-247. agents/judge.py built: Judge-as-code, wraps existing modules (CC)
Per `docs/AGENT-RUNTIME-PROPOSAL.md`'s "Judge is ~80% deterministic
already" framing and `agents/judge/SOUL.md`'s requirements. New file, no
backtest logic reimplemented - composes `validate_harness.main()`,
`assertions.run_all()`, `pooled_analysis.per_strategy_summary()`,
`asset_class_analysis.analyze()`, and `summarize_graveyard.summarize()`
into one evidence-pack JSON. Per-strategy rows carry n_trades, twin
PERCENTILE (never a single-draw comparison, per D-219), an approximate
win-rate CI and z-test against the 50% null (labeled approximate - Wald
normal, not exact binomial), and the SOUL's cold_start
(n<30)/reviewable_not_promotable (30<=n<50)/evaluable (n>=50) confidence
labels. NOT_TESTED rows are never converted to a failure. Every row is
stamped PROVISIONAL whenever `validate_harness.py` is red - the flag
cannot be silently dropped downstream. `expected_best_by_chance` reuses
`summarize_graveyard.py`'s existing multiple-comparisons numbers rather
than reimplementing a correction; correcting on hypotheses GENERATED
(Forge's search log, the SOUL's harder requirement) is left an explicit
`not available` since no module in the codebase tracks that count yet -
noted as a TODO, not silently faked. Read-only: writes only its own pack
under a caller-given path, never touches graveyard files or the DB.
`tests/test_judge.py`, 17 tests, all green. Callable as
`python agents/judge.py --graveyard PATH --out PATH [--strategy NAME]`.

### D-246. Lab v4 DEEP RENT ignitions: already built, verified, and formally logged (CC)
`strategies/builtin/strategy_lab_v4.py` (317 lines) implements all three
IGNITIONS from `references/strategy-lab-v4.md` as SHARE strategies per
`docs/STRATEGY-LAB-V3-V4-ASSESSMENT.md`'s recommendation: `V4_gap_hold_proxy`
(I1, PEAD - price-only proxy, no earnings calendar exists so this detects
the gap-and-hold signature only, not SUE proper), `V4_52w_high_breakout`
(I2, George & Hwang anchoring effect, weekly bars so the 52-week lookback
fits inside the harness's test-slice length), `V4_trend_reclaim` (I3, 100-day
MA reclaim + non-negative 12-1 momentum, weekly bars). 17 tests
(`tests/test_strategy_lab_v4.py`), all green, and already wired into
`run_incremental_graveyard.py`'s `ALL_STRATEGIES`. This was built earlier
today (file predates the D-235-D-242 parallel build) but never got a
decision entry, and ROADMAP.md still described it as "being built" - both
gaps closed by this entry and the matching ROADMAP.md edit. No new code;
this is a documentation-debt fix. Powered results are riding the same
graveyard sweep already running (PID 63767) - no separate run needed.

### D-245. Option 2 (Agent SDK runner) stays the long-term migration target (Aym/Raven, formalized by CC)
Per `docs/agent-proposal-reconciliation.md` Q2: the joint debrief's "Hermes
cron triggers a scoped Claude Code subagent" is Option 1 refined, not a
replacement for Option 2. Written down explicitly so it doesn't read as
decided-by-default: **Hermes-cron-triggers-subagent is the near-term shape.
Option 2 (a standalone `agents/runtime.py` on the Agent SDK, launchd-
scheduled, no dependency on any interactive session) stays the long-term
target if/when volume or reliability demands it** - i.e. once the loop is
running daily/weekly and either needs more concurrency than one subagent
session gives, or needs to survive without Hermes as the trigger. Nobody
migrates preemptively; this just keeps the option live instead of letting
it evaporate by omission.

### D-244. D-217 ratified: SPEC-5.7 interim + Aym ratifies the 11 rules directly (Aym/Raven, formalized by CC)
Per `docs/agent-proposal-reconciliation.md` Q4, combining the runtime
proposal's options (a) and (b) rather than picking one: **agents run under
SPEC-5.7 rules only until ratification (interim safety - no agent is
blocked waiting on this), and Aym ratifies the 11 SOUL rules named in D-217
directly rather than waiting on a separate Raven ruling** (permanent
resolution - they are mostly extra honesty requirements, and the one
substantive conflict D-217 flagged, the twin-methodology mismatch between
Quant's old single-draw SOUL text and the new percentile rule, is already
resolved in code by D-219's percentile gate). Effect: `agents/quant/SOUL.md`
should be updated to match D-219's percentile language (the one piece of
D-217 that was a real conflict, not just an addition) the next time it's
touched; the other 10 rules across scout/forge/judge/coach/echo stand as
drafted. None of this is load-bearing yet since only Quant's SOUL is
active per SPEC 5.7's 5-live-strategies split trigger.

### D-243. Governance carve-out: trading-bot agents run standalone, Raven audits monthly (Aym/Raven, formalized by CC)
Per `docs/AGENT-RUNTIME-PROPOSAL.md` section "Governance" and
`docs/agent-proposal-reconciliation.md` Q1: the global stack rules make
Hermes/Raven the standing review layer for Claude Code sessions generally.
This project's agents (Quant now, the 5-way split later) do not depend on
either at runtime - the safety boundary is deterministic code (sandbox,
registry inbox, gate checks, validate_harness, kill switch, Aym-only
promotion), not AI review. Written down explicitly so the two rule systems
don't silently conflict: **trading-bot agents run standalone; Raven audits
monthly, not per-decision.** Audit cadence mechanism (also from the
proposal, not yet built): every agent action already lands in the existing
audit log; a weekly Echo digest and monthly SOUL drift-check cron are
queued behind Echo/judge.py existing to produce something to digest.

## v6 - 2026-08-13 evening (parallel agent build: SPEC 5.8 + Lab v5 P2/P3/P5)

Four subagents built four disjoint-file lanes in parallel; every lane passed
its own tests plus the full suite (475 fast + 42 harness = 517, up from 355
this morning), and each wrote its own handoff in docs/handoffs/.

### D-241. Cross-sectional harness BUILT (SPEC 5.8) (CC agent)
`backtest/cross_sectional.py` + 29 tests. Panel with union-grid alignment
(no forward fill), PanelView that structurally denies rankers the decision
bar (one bar MORE conservative than the time-series harness), TradeCoster
costs, time-matched twins that replay the strategy's own formation steps,
per-cell + pooled + leave-one-asset-out + calendar-half split, fires-check
before P&L. The load-bearing test is a lookahead oracle: a cheating ranker
that would earn +20%/trade on a leak earns -8% and produces bit-identical
trades to an honest lagged ranker. Implements v3 #3 Same-Clock Echo (30m
slots from 15m bars, DST-correct), v3 #5 Paid Liquidity Reversal (VIX
context gate; news exclusion stamped OMITTED_NO_EARNINGS_CALENDAR), and v5
P1 Horizon Ladder (`backtest/run_horizon_ladder.py`, pre-registration in
docstring before any result existed). Smoke runs prove machinery only;
powered runs queued behind the graveyard. Futures excluded from pooled
dollar cells (contract dollars would outvote spot clips).

### D-240. Lab v5 P2 Toll Collector: kill condition NOT fired; prediction refuted in direction (CC agent)
`backtest/toll_collector.py` + 13 tests + research/graveyard/toll_collector.json.
Conservative maker-fill simulator (fills ONLY on trade-through, low <
limit - 1 tick; every ambiguity resolved against the maker hypothesis).
12 months x 3 pairs x 1h: armed 31.8%, 1,556 episodes, 516 maker fills
(33.2%), taker-stop rate 39%.
- Maker beats identical taker-at-touch by 11.2bps vs fee+slip savings of
  12.6bps -> measured adverse selection ~1.3bps. Kill condition (adverse
  selection eats the discount) did NOT fire. Passive execution survives AS
  AN EXECUTION-COST STORY.
- Pre-registered prediction (edge concentrates in top vol band) REFUTED in
  direction: >=90th pctile fills net -7.8bps, 70-90 band +7.0bps. Inverted,
  both t < 1.
- Strategy P&L itself: +2.5bps/trade, t=0.31, n=516 - a shrug, and the
  margin sits inside the UNVERIFIED 0%-maker fee assumption.

### D-239. Lab v5 P5 Forced-Flow strategies built and registered; sample far below power bar (CC agent)
`strategies/builtin/strategy_lab_v5.py` (V5_forced_flow_crypto,
V5_capitulation_equity) + 21 tests. Registered (55 strategies total); the
armed incremental pass sweeps them automatically. Fires verified: ~5 crypto
events (Yahoo 1h slices) + 3 equity capitulations across 178 tickers -
FAR below P5's 400-800 power bar, so the graveyard's verdict on these
cohorts is a shrug until more event-years exist (standing rule 7). Caveats
stamped everywhere: funding table is one strange regime-year; no earnings
exclusion exists yet. The tasking's proposed crypto/equity discriminator
(funding-date matching) was checked and found wrong; replaced with a
24/7-tape gate + mirror gate so the two cohorts stay disjoint for the
mechanism-coherence kill condition. No P&L was read during parameter
fixing.

### D-238. Lab v5 P3 Dispersion Gate built; thresholds now PER-CLASS (CC agent)
`backtest/dispersion_gate.py` + 20 tests. Derived gate ATR_hold >= c/kappa
(kappa=0.10 pre-registered), with c taken from the SAME TradeCoster the
harness charges - the v5 doc assumed a flat 14bps; venue-accurate c makes
the equity gate ~3.3x sharper (0.421% vs 1.4%) and futures ~10x sharper.
Entries: grid_2.0atr, stoch_rsi_oversold, dca_7 control; time exits only;
expanding-window vol deciles (pre-entry data only); calendar-midpoint
holdout judged on H2; LOO. Smoke run (16 series) proves mechanics; the
gated-out fractions order by class exactly as the thresholds predict. One
defect caught in smoke: MES contract dollars pooled with $100 spot clips
(the P0.4 denominator sin) - cross-class dollar pooling is now SPOT-only.
Full 1-3h run queued behind the graveyard.

### D-242. Standalone agent-runtime proposal written (needs AYM)
docs/AGENT-RUNTIME-PROPOSAL.md. Answer to Aym's question: the bot's agents
CAN run independent of Hermes/Raven - nothing in any code path depends on
them; enforcement is the deterministic machinery (sandbox, registry inbox,
gates, validate_harness, kill switch, Aym-only promotion). Proposal: code
where possible (Judge/Echo are ~80% deterministic already), model where
necessary (Forge/Scout), Coach writes recommendations only. Three decisions
are Aym's: D-217 ratification path, an explicit Hermes/Raven carve-out
statement, and the audit cadence that replaces per-decision review.

## v5 - 2026-08-13 (cost model wired into both harnesses)

### D-237. Lab v5 P4 "Fingerprint Router" is DEAD - killed by its own precheck (CC)
`backtest/vr_fingerprint.py` implements P4's pre-registered kill condition (a)
from `references/strategy-lab-v5.md` (doc saved to references/ today - the
ROADMAP had summarized it but the full text was not in the repo):

> "trait instability - cross-half Spearman correlation of instrument VRs
> < 0.3 => the fingerprint is weather, not character, and routing dies
> before P&L is even read."

**Measured on 175 instruments** (daily bars, >= 200 bars per calendar half):
- VR(5,1) cross-half Spearman: **-0.213**
- VR(20,5) cross-half Spearman: **+0.072**

Both far below the 0.3 bar; VR(5,1) is mildly ANTI-correlated - an
instrument that looked mean-reverting in its first half was slightly more
likely to look trending in its second. The Lo-MacKinlay variance ratio, at
these sample lengths and this universe, is regime weather, not instrument
character. **No routed backtest was run, and none should be** - running one
anyway and reading its P&L would be exactly the winner-filtering the
proposal's no-trickery clause forbids.

This is the cheapest kind of result the lab produces: a proposal resolved
with ~zero compute because the kill condition was designed to fire before
the expensive part. Math pinned by tests/test_vr_fingerprint.py (6 tests).
Full per-instrument table: research/graveyard/vr_fingerprint.json.

Consequence for SPEC 5.8: the cross-sectional harness loses one of its four
named consumers (P4). It keeps three (v3 #3, v3 #5, v5 P1) and the context-
series capability, so its priority stands.

### D-236. Fee reference confirmed as-is; venue verification demoted to shadow-test gate (Aym)
Aym re-supplied the broker fee reference; it is byte-identical to
`references/broker-fee-reference-2026.md`, the source of cost model version
2026-08-13 already wired and running. No rate changes.

Aym's ruling: backtesting measures edge outside brokerage constraints;
venue-specific verification (Binance.US live fee page, checklist item 1)
matters when shadow testing starts, not before. ROADMAP P0.1 reframed
accordingly. `CostModel.describe()` keeps `verified: False` on crypto until
a live account confirms - the flag describes evidence, not urgency.

### D-235. Cost model + instrument specs wired; opt-in, version-stamped, pool-guarded (CC)
Implements the agreed next action from the 2026-08-12/13 handover (P0.2).

**What changed:**
- `TradeCoster` (backtest/cost_model.py): binds the four-regime CostModel to
  one instrument, exposing exactly what a harness loop needs - a slip rate
  that moves the FILL (and therefore the stop distance), and a dollar
  `leg_fee` that comes off PnL. Keeping those separate is the point: a model
  that subtracts spread at the end reports a risk plan nobody traded.
- `FlatCoster`: the legacy single-rate model behind the same interface, so
  the loop has ONE code path. Its version string encodes its rates.
- Both harnesses take `use_cost_model: true` in config. OFF BY DEFAULT and
  off means bit-identical to the old flat model - required by the
  cross-harness referee (external engines are configured with one commission
  rate) and by the zero-cost validation probes. `fee_override`/
  `slippage_override` always force flat semantics for the same reason.
- FUTURES size in WHOLE CONTRACTS (standard remapped to micros, ES->MES),
  margin is capital-at-risk, PnL is in contract dollars (multiplier), and
  `return_pct` is now return on capital at risk everywhere. For spot,
  capital at risk == notional, so spot numbers do not move.
- Every report row is stamped `cost_model_version` + `asset_class` +
  `instrument`. NOT_TESTED rows too.
- New silent assertion `cost_model_version_uniform`: entries with mixed
  stamps (including pre-stamp 'unstamped') cannot be read as one dataset.
  This is what makes the opt-in safe: a run that forgets the flag cannot be
  pooled with a run that used it.
- Twin caches key on the coster's full identity, so a twin computed under
  one cost regime can never answer for another.
- `resolve_asset_class` moved to backtest/instruments.py as the single
  sector->class mapping; asset_class_analysis.py now imports it.

**Guard rails that held:** 401 tests pass (4 new wiring regressions pin
equity-cheaper-than-flat, one-contract futures on margin, override-forces-
flat, flat-stamping). validate_harness.py 21/21, exit 0. Cross-harness
referee AGREES on both AAPL and BTC_USD.

**Measured round trips on a $100 clip (per coster.describe()):** crypto core
12bps, crypto other 14bps, equity/ETF 4.2bps, MES futures 1.3bps of exposure
($2.06 on one contract, $1,800 margin at risk, ~$34k exposure).

**Still true:** Binance.US rates remain UNVERIFIED (Aym checklist item 1);
equity half-spread 1.5bps/leg is a liquid-large-cap default, not per-
instrument; futures slip is one tick per side, an assumption.

## v4 - 2026-08-13 (inversion implemented + tested, agent SOULs, benchmark honesty)

### D-234. Constraint sensitivity sweep: aggressive vs base vs conservative (CC, Aym's ask)
`backtest/constraint_sweep.py`. Aym asked for an AGGRESSIVE (loose, fires
often) and CONSERVATIVE (harsh, rare) variant per strategy to see whether
performance changes.

**Framed as a DIAGNOSTIC, not an optimization.** "Which level scores best,
keep the winner" is parameter search on seen results and manufactures edge
exactly like subset-filtering (D-233). The falsifiable question is about
mechanism instead:
- If a strategy has real edge, tightening the gate should RAISE PnL per trade
  while lowering trade count. Fewer, better trades.
- If the gate carries no information, tightening only shrinks the sample.
  Per-trade PnL stays flat at the cost floor.

**The shape of the curve is the finding, independent of which level wins.**
A flat curve across a large change in selectivity proves the confirmation
stack is not selecting for anything.

Knob choice: the harness's own confirmation stack (regime filter, RSI ceiling,
volume ratio), NOT strategy-internal thresholds. This applies identical
tightening to all 44 strategies without editing 44 files and keeps the
comparison honest. Strategy-internal parameter tuning stays Forge's territory
(SPEC 5.5 edit ladder), deliberately untouched.

Levels: AGGRESSIVE (no stack at all), BASE (RSI 70 / volume 1.2 / regime),
CONSERVATIVE (RSI 45 / volume 2.0 / regime). 14 series spanning equities,
ETFs, crypto and futures across 5m/1h/1d, 3 exit configs.

RESULTS PENDING - the sweep is compute-bound (the ta-backed indicators are
~19x slower per call than the hand-rolled ones, the price paid for D-201's
correctness). Optimized to scan once per (series, strategy) and reuse across
all three levels, since signals do not depend on the confirmation stack.

### D-232. Asset-class pooling + leave-one-asset-out guard (CC, Aym's ask)
`backtest/asset_class_analysis.py`. Rare patterns are generic, not
ticker-specific, so the unit is the pattern. Pooling by asset class made
**105 of 128 strategy-class cells judgeable** where per-ticker almost none
were. Verdict unchanged: every judgeable cell negative, clustered at the
-$0.30 cost floor, and the cross-class SPREAD is tiny (0.06-0.36) - the
strategies are not class-specific, they are uniformly at zero. That is
evidence against the simple form of the fingerprinting thesis.

The study's best cell (`bullish_harami` CRYPTO, -0.036/trade over 162 trades)
turned out to be **one asset in a costume**: -0.417 without SOL, worse than
the floor, with a single 9-trade cell producing +2.534/trade. Added an
automatic **leave-one-asset-out** check flagging any cell that moves >0.15
when its top underlying is removed, grouping by UNDERLYING so BTC/USDT and
BTC_USD are one asset. Three cells flagged.

### D-233. Conditional edge tested properly; filtering to winners yields NOTHING (CC)
Aym asked to slice by sector/class/condition and keep the subsets where
patterns win. The naive version of that is the textbook selection-bias
failure, so `backtest/conditional_edge.py` does it the only valid way:
SELECT winning cells on half the underlyings, JUDGE those exact cells on the
other half, 20 random splits.

Result across all five condition slices (class, sector, timeframe,
class x timeframe, class x exit): **survival 53.5%-58.7%, i.e. a coin flip.**
The decisive number: cells that WON on the selection half average
**-$0.302/trade on unseen instruments, against a -$0.30 cost floor.**
Selecting winners bought exactly zero.

Caveat recorded: `sector` shows 58.2%, marginally above chance, but it has
the most slices (most chances at noise) AND a ticker-split does not break
sector correlation, so it is not a clean holdout for a sector hypothesis.
A time-based holdout would be needed, and is only worth running once
something shows positive GROSS edge.

The tooling is permanent: the moment any strategy shows gross edge above
costs, one command tests whether it is real or a subset artifact.
Writeup: `research/2026-08-13-conditional-edge-finding.md`.

### D-231. Strategy Lab v2: 9 strategies built, wired, and running (CC)
`strategies/builtin/strategy_lab_v2.py` + 120 tests. Implemented the 8 that
run on data we have: Wick Autopsy, Round-Number Defense Decay, Liquidation
Echo, Second-Break Verdict, Volume Desert Breakout, VWAP Magnet Close, Expiry
Pin Drift, 0DTE Afternoon Amplifier. All verified ALIVE on real data (signal
counts per file recorded in the handoff), which caught two dead-on-arrival
bugs before they reached the graveyard:
- Second-Break fired 0 times on AAPL: the spec's "OR height 0.5-2.0 ATR"
  means SESSION-scale ATR but the harness passes BAR ATR (opening ranges run
  2.4-6.4x the 5m ATR). Added sqrt-of-time rescaling. 0 -> 225 signals.
- Liquidation Echo's swing-low detector matched flat tape (non-strict
  minimum), so cascades trivially "held above" a pool that never existed.

**VWAP ATR-scale judgement call resolved by testing both, not by choosing.**
Bar scale fires ~35% of ticker-days (matching the spec's own estimate) with
targets near the fee floor; session scale fires ~1.5% with targets that clear
costs. Registered as `V2_vwap_magnet` and `V2_vwap_magnet_sessionatr` so the
graveyard settles it empirically. 9 strategies total.

Grid is now **44 strategies x 11 exit configs x 885 test sets = 330,410
backtests**, running with time-matched twins (D-230) so the six clock-anchored
v2 strategies cannot win on the clock.

Known: Wick Autopsy (~1 signal per 4,000 bars) and Liquidation Echo will fall
under the 20-trade per-ticker gate and need POOLED analysis (D-227) to be
judgeable at all.

### D-230. Strategy Lab v2 filed; its harness warning was correct and is fixed (CC)
`references/strategy-lab-v2.md` (18 unorthodox cross-asset day-trading
hypotheses in genome format, with theses and kill conditions).

The doc raises what it calls silent assertion #15, and it is RIGHT: several
strategies are clock-anchored (opening range, lunch desert, 15:30 VWAP
magnet, expiry afternoon), and the random twin drew entries uniformly across
the session. Comparing a 15:30-only strategy against an all-day twin credits
the CLOCK, not the signal - a false-positive generator for exactly the
strategies this doc adds.

**Fixed:** `_time_bucket_key` detects clock anchoring from a strategy's
actual signal times (returns None when signals spread across the session, or
on daily/weekly bars where one bar-time is meaningless), and the twin then
draws ONLY from matching minutes of day. Guard: needs >=20 matched bars
before restricting, else falls back to unrestricted. Twin cache key now
includes the buckets. 4 tests, including one on a series engineered so one
minute of day behaves differently - if time-matching were decorative that
test would fail.

Implementation of the strategies themselves is in progress; only the subset
runnable on existing data (pure OHLCV + the funding CSVs already present) is
being built. Blocked on missing data: premarket bars (2.2), economic
calendar (3.2), session/settlement tags (3.1, 3.3), surprise scanner (2.7).

### D-228. Plan confirmed against SPEC (Aym asked; verified, not assumed)
Aym asked whether the plan is: no live trading until he says so, build the
graveyard foundation by testing all strategies + exit configs, THEN build the
agents, THEN test agent enhancements. Verified against SPEC:
- **No live trading**: SPEC section 2 verbatim. Also enforced in CODE -
  `engine/main.py` refuses any mode except paper regardless of config.
- **Graveyard before agents**: SPEC's own "In scope (backtest expansion -
  now)" says "build the graveyard foundation before agents exist", and the
  T-order puts Quant at T13/T14 after the T7 backtest moment of truth.
- **Version confusion resolved**: V1/V2/V3/V4 scope LIVE EXECUTION only.
  Backtesting futures/options/equities now does NOT violate "futures is V3".
  Raven did not err; they are two different axes.
Flagged for the agent build: Forge must NOT inherit "35 strategies failed."
The accurate framing is "generic pattern trading shows zero gross edge;
instrument-strategy pairing (v2 fingerprinting) is untested and is the
promising direction." The difference decides whether Forge wastes months.

### D-229. Exit-signal strategies finally wired into the sweep (CC)
Two new exit configs: `signal_exit` (stop + bearish-pattern exit) and
`signal_exit_2r` (stop + pattern exit + 2R target), putting the 14
EXIT_STRATEGIES_EXPANDED into the graveyard for the first time. Exits fill at
the NEXT bar's open (a pattern is only known once its candle closes, SPEC 5.1
#6). Bearish-exit bars are computed once per series and cached, since they
depend only on price. Grid is now 11 exit configs.
Verified AAPL 1h: 1,261 exit bars, 588 signal exits across 1,107 trades,
stops still honored. Result: -$0.299/trade, no better than mechanical exits -
another independent confirmation of D-227.
Also: the runner's silent `continue` for short series now emits NOT_TESTED
rows with a reason (D-223 closed), and weekly is in the grid (885 test sets,
up from ~693).

### D-226. Silent assertions (result-quality set) built and immediately paid off (CC)
`backtest/assertions.py`: quarantine canary, mirror-pair contradiction,
win-rate ceiling, trade-count sanity, duplicate-strategy detector, timeframe
coherence, gate-version uniformity. These are the RETROACTIVE set (read
stored rows) that the validation review separated from the harness-validity
set already in validate_harness.py. Per review section 9 they are NOT
disclosed to Forge/Quant.

First run over 218,295 entries failed three:
- **trade_count_sanity**: 17 strategies produce zero trades on >60% of
  tickers (rising_three_methods and rsi_extreme: 99%).
- **duplicate_strategies**: breakout_20 and momentum_continuation have 99.4%
  identical trade counts - one idea counted twice, inflating breadth.
- **quarantine_canary**: 63 SNDL rows above PF 1.3, all on 2-3 trades
  (small-sample noise; the assertion needs a min-trade floor to be useful).

### D-227. THE v0 VERDICT: 33 of 35 strategies lose exactly the trading cost (CC)
The assertions exposed that per-ticker verdicts were structurally impossible
for rare patterns: **13 of 35 strategies never once reached the 20-trade floor
in 212,058 runs**; morning_star and piercing_line never exceeded 6 and 8
trades in ANY run. All were recorded FAIL, which reads as "does not work"
when the truth was "cannot be measured."

Fix: `backtest/pooled_analysis.py` aggregates across tickers (a pattern's
edge belongs to the pattern, not to AAPL). 33 of 35 became judgeable.

Pooled result: **every strategy lands at -$0.25 to -$0.35 PnL per trade
against a $0.30 round-trip cost.** Gross edge is indistinguishable from zero
across the entire v0 library - they are not predicting badly, they are not
predicting. Independently matches the inversion study's -$0.31 per exit.

Consequences: the SPEC 5.3 150-trade bar should apply to POOLED counts;
"insufficient data" must be a verdict distinct from FAIL at strategy level;
and Forge must never be told "35 strategies failed" (the honest statement is
33 measured at zero edge, 2 unmeasurable). Writeup:
`research/2026-08-13-v0-verdict.md`.

### D-225. "Skip expensive contracts" is a risk AMPLIFIER, not risk management (CC)
Aym proposed extracting the small account's forced selectivity as a
deliberate rule (it declined unaffordable trades, which felt like risk
management). Tested properly on **4,633 option trades, 25 tickers**, by
bucketing one unfiltered population by entry premium rather than comparing
divergent filtered runs.

Result inverts the hypothesis. Cheap contracts (bucket 1) have the highest
MEAN (+23%) but the worst MEDIAN (-16%), worst win rate (46%), and nearly
DOUBLE the variance (163% vs 89%) of the priciest bucket, whose median is
-0.7% with a 49.7% win rate. Cheap options are lottery tickets; the mean is
carried by rare winners. Risk-adjusted returns are roughly flat across
buckets - what changes is distribution SHAPE.

So affordability constraints push a small account into the highest-variance,
worst-median part of the distribution: the opposite of protecting the
balance. The earlier "3 of 4 skipped trades were losers" was n=4; at n=4,633
the effect reverses.

**Corrected extractable rule:** for survival, prefer HIGHER premium (closer
to the money), not lower. This compounds with D-222: cheap contracts are also
worst on commission drag (39% vs 1.6% of premium). Cheap = worse fees + worse
median + double variance. The SPEC's v2+ WSB/tail-risk ideas live in bucket 1
and must be judged on tail behavior and bankroll survival, never mean return.
Tool: `backtest/premium_filter_study.py`. Writeup:
`research/2026-08-13-premium-selectivity-finding.md`.

### D-224. Balance sweep: spot invariant, options size-sensitive for 3 reasons (CC)
Aym asked to disregard the $100 cap and sweep account balances.
`backtest/balance_sweep.py`, $500 to $100,000:

- **Spot: exactly invariant.** PF/win-rate/return% identical at every balance
  (dollars scaled 200x, quality metrics did not move). Position size is a
  RISK decision only; it has zero effect on whether an edge exists.
- **Options: size matters, but not via the fee rate.** commission/premium =
  (2 x fee x contracts)/(premium x 100 x contracts) - contract count CANCELS,
  so the ratio is set by PREMIUM PER CONTRACT (strike/expiry), not balance
  (0.58% -> 0.51% across a 200x range). The three real effects are: order
  minimums binding only at the bottom ($500 pays 0.72% vs 0.58% with a $1
  minimum), **idle capital from indivisible contracts (26.4% unspent at $500
  vs 0.4% at $25k)**, and affordability gating which trades happen at all.

**TRAP PINNED BY TEST:** the $500 account posted the BEST profit factor
(1.93 vs 1.45) and it is meaningless - it declined 4 unaffordable trades, 3
of which were losers. Worse, the populations are not even nested: declining
one trade frees the scanner to enter a later trade the funded account was
still holding through, so different balances follow genuinely divergent trade
sequences. **Never compare PF across account sizes without confirming trade
counts match.**

Practical: options need their own sizing floor, roughly "enough for 5-10
contracts of typical premium" so rounding waste stays under ~5%.

### D-222. Options tested for the first time; fee structure is the finding (CC)
`backtest/options_overlay.py` + 8 tests. `synthetic_options.py` had existed
with ZERO callers - options were in-scope in the SPEC and had never been run.
The overlay replays any strategy's bullish signals as long calls with
per-CONTRACT commissions, timeframe-correct vol annualization, and an option
spread.

Aym's premise ("options pay per trade, not per contract") is wrong for most
US brokers (Schwab/Fidelity $0.65/contract, tastytrade $1, IBKR $0.15-0.65 +
order minimum). But the underlying instinct is right and important: the fee
is a FIXED DOLLAR AMOUNT per contract, not a percentage of premium. Measured
on AAPL: commission runs **1.61% of premium at 5% OTM and 39.36% at 20% OTM**
- cheap options are punished twice (bigger fee share per contract, and a
fixed budget buys more contracts). The SPEC's v2+ WSB and tail-risk ideas
(buy cheap far-OTM) are the worst case for this structure.

Hard constraint discovered: **the $100 SPEC notional cannot buy one option
contract** on most liquid names (premiums run $200-500/contract). Options
need their own sizing rule. Pinned by test.

DO NOT trust the overlay's PnL: no IV smile means long-option entries are
priced too cheaply, flattering every result. It is a fee-structure model,
not a pricing engine. Writeup:
`research/2026-08-13-asset-class-coverage.md`.

### D-223. Weekly timeframe has never been tested (silent skip, CC found)
176 `_1wk.csv` files exist, `1wk` is in the runner's timeframe list, and
weekly has produced ZERO graveyard entries since the project began. Cause:
weekly files hold ~262 bars, the 20% test slice is 53 bars, and the runner
skips any series under 100 test bars with a bare `continue`. Fix needs
10-15y weekly history AND the runner emitting NOT_TESTED with a reason
instead of skipping silently (D-109's principle, applied to data length).
Queued, not yet done.

### D-219. Random twin is a PERCENTILE against 100 matched twins (CC RULING)
Aym delegated the Judge-vs-Quant SOUL conflict. Ruling: the percentile method
wins, and the CODE was upgraded to produce it rather than just documenting an
aspiration. `VResult.twin_pfs` now holds the full distribution;
`twin_percentile` reports where the strategy sits in it; `beats_random_twin()`
requires the 90th percentile. The old median-plus-0.15 test survives as
`beats_random_twin_legacy` for comparison only.

Why the old test was weak: one draw is a coin flip, and a fixed PF gap is
scale-dependent (0.15 means something different at PF 0.4 than at PF 4.0).
"96th percentile of 100 matched twins" is a statement; "beat one draw by 0.16"
is not.

Affordable because the twin distribution depends only on (price series, exit
config, costs) and NEVER on the strategy - so it is computed once per
(ticker, exit_config) and shared across all 35 strategies instead of being
recomputed 35 times. 100 twins now cost less than 10 did before.

Quant's SOUL updated to match Judge's (both now cite percentile + twin count).
Effect on the one interesting result so far: grid_1.0atr on ADBE 1h sits at
the **96th percentile of 100 twins** - it survives the stricter test.

### D-220. Gate version stamped on every graveyard entry (CC)
`GATE_VERSION = 2`. The incremental resume key has no code fingerprint, so
entries from different PASS/FAIL semantics could be silently pooled (an
audit finding that had no fix). Every entry now carries the stamp. The
partial 15k-entry run under the old gate was archived, not resumed, for
exactly this reason: mixing a median-gate epoch with a percentile-gate epoch
would produce a graveyard whose verdicts mean two different things.

### D-221. Position size does not change edge on a percentage-fee venue (CC)
Aym asked whether larger positions could outgrow fixed round-trip costs.
Measured: at $100 / $1k / $10k / $100k notional the SAME strategy returns
PF 1.6113, 56.2% win rate, 0.370% return - identical to four decimals, with
PnL and fees both scaling linearly. Binance.US fees are percentages, so size
multiplies both sides of the ledger. Under a hypothetical FIXED $1-per-order
commission the same trades flip from -$22.88 at $100 to +$9,091 at $100k, so
the intuition is exactly right for fixed-cost venues (per-trade commissions,
on-chain gas) and exactly wrong here. At genuinely large size costs get WORSE
(market impact, SPEC F7). Writeup:
`research/2026-08-13-position-size-and-costs.md`.

### D-218. Graveyard reports DISTINCT FINDINGS, not pass rows (CC)
`backtest/summarize_graveyard.py`. The 15k-entry partial run produced 12 PASS
rows, which reads like 12 discoveries. It is **2**: eleven of them were grid
strategies on the SAME ticker and timeframe (ADBE 1h) across 11 exit configs,
and the two grid parameterizations are one idea. The summary now reports raw
rows alongside distinct findings collapsed four ways (strategy x ticker x tf,
strategy FAMILY x ticker x tf, strategy x ticker, tickers-with-any-pass), the
pass concentration by ticker, and the expected-best-by-chance z-score for the
grid size (~4.4 sigma at 15k tests - the validation review's section 5 point
that a spectacular row is the base rate, not evidence). Benchmark passes are
counted separately and excluded from discoveries. 3 tests pin it, including
the exact ADBE shape.

**Reading rule for Aym/Raven/agents: never cite a pass count from the
graveyard JSON. Cite `distinct_findings` from summary.json.**

### D-215. SPEC 5.6 inversion implemented, F2-gated, and EMPIRICALLY REFUTED (CC)
`backtest/inversion.py` + `run_inversions.py`: signal-as-exit fade, measured
out-of-sample against buy-and-hold with full costs on both legs, gated by the
review's F2 conditions (gross PF <= 0.90, >= 30 trades, OOS). First real run:
**48 eligible tested, 0 beat buy-and-hold, edge = -$0.31 per exit against a
$0.30 round-trip cost.** The "anti-signal" is trading friction, to the cent.
Full writeup: `research/2026-08-13-inversion-finding.md`. Machinery kept (it
settles the question per run, cheaply); expectation retired. Contrarian-filter
inversion remains unimplemented by design (needs a base-strategy pairing to
mean anything). 10 tests incl. both directions (a signal that truly precedes
drops DOES show positive fade edge, proving the test can detect real edge).

### D-216. Benchmark strategies cannot be "discoveries" (CC)
The first fresh graveyard run produced exactly one PASS: `dca_14` on ETH/USDT
beating buy-and-hold by $0.31 over 28 trades. DCA has no signal - in a rising
market it wins on timing luck. Benchmarks now carry `is_benchmark` and report
`PASS_BENCHMARK`, never `PASS`, and are excluded from inversion (fading a
signal-less strategy tests nothing). Pinned by test.

### D-217. Five agent SOUL.md files drafted (CC, NEEDS RAVEN RULING)
`agents/{scout,forge,judge,coach,echo}/SOUL.md` + `agents/README.md`, matching
Quant's approved structure and voice. The drafting agent flagged 11 rules it
encoded that are NOT literally in SPEC 5.7 - they come from the audit and the
validation review. The ones that most need Aym/Raven's ruling:
- Judge RETURNS UNEVALUATED any Forge submission lacking hypotheses_generated /
  screened counts and the variant log (the p-hacking hole, review section 6).
- Judge corrects for multiple comparisons on hypotheses GENERATED, not
  submitted; must report expected-best-by-chance alongside observed best.
- Random twin as a percentile against a distribution of matched twins, NOT a
  single draw + 0.15 threshold. **This conflicts with Quant's existing SOUL,
  which still cites the single-draw rule.** Needs a deliberate ruling on
  whether Quant's file is updated to match.
- Coach never addresses Aym directly; routes through Echo. (SPEC says Coach
  cannot "report to human" - read as routing, could be read narrower.)
- Coach makes NO lifecycle recommendation at all on provisional numbers
  (stricter than D-102 states).
- Forge is told the physics but never the tripwires ("you do not know what the
  validation assertions check and you do not try to find out") - constrains
  what may be put in Forge's context at runtime.
- Scout's speculative/supported/never-"confirmed" grading scale is invented.
Full list in the agents' handoff notes; none are load-bearing in code yet
(only Quant's SOUL is active; the rest are designed-for-later per SPEC 5.7's
5-live-strategies split trigger).

## v3 - 2026-08-12/13 (night session: performance fix + T8 + reconciliation)

### D-209. Sweep scans each strategy once, replays across exit configs (CC DECISION)
The ta facade swap made per-call indicators ~19x slower (measured), which
would have turned the graveyard re-run into weeks. Since signals don't depend
on the exit config, `run_sweep` now calls `scan_all_bars()` once per strategy
and replays cached signals across all 9 exit configs (~9x, plus skipped
window construction). Verified: 0 mismatches across 24 strategy/config combos
vs direct scanning, pinned by `TestScanCacheEquivalence`. Valid only for
stateless strategies (all swept builtins are). Benchmark after fix: ~30s per
daily ticker. The first (slow) re-run was killed and restarted fresh.

### D-210. T8 sandbox built (CC DECISION, per queue D-208)
`sandbox/validator.py` + `sandbox/_runner.py`: AST allowlist (imports,
exec/eval/open/getattr-family, dunder access, star imports - fails closed and
NEVER executes rejected code), subprocess conformance with 15s timeout
(Strategy interface, well-formed Signals, stop<entry), sha256 hash pinning in
strategy_registry (additive migration adds family/code_hash columns), family
drift rejected unless allow_family_migration=True. 21 tests incl. proof that
AST-rejected code with import-time side effects never runs. ALLOWLIST POLICY:
ta/pandas/numpy included for Quant strategies - Raven to ratify (D-203).

### D-212. Engine re-audit findings triaged and fixed (CC, per re-audit report)
The independent engine re-audit (relaunched after limit reset) verified
B1-B4 + majors fixed and found real defects in the NEW executor, all fixed
same night: N1 halt now drains the queue with 'halted' labels, retries
position closes every step, and entries expire after valid_for x interval
('signal_expired'); N2 reconcile no longer closes trailed-stop winners
(unprotectable = missing/nonpositive stop only) and counts only real closes;
N3 stale-data check filters by signal timeframe; N4 every non-executed signal
path now labels its row (not_selected / block_enforced_in_scanner /
fill_failed / close_failed / trail_not_applied); N6 daily/weekly ops stops
now AUTO-HALT (HALT file + risk_events row), human resume only; N12 botctl
hardened (no overwrite of active halt, no traceback on corrupt HALT file);
N11 shutdown order scanner->executor->collector.

**N5 decision (documented deliberate gap):** the paper adapter fills
market-only; resting buy-stop/limit simulation (matching the backtest's
D-103 order semantics) is NOT implemented yet. Until it is, hammer-style
buy-stop entries fill at current ask instead of resting at the trigger
level - a known backtest/paper divergence, queued as the next engine build
item. Accepted findings not yet fixed (queued): N7 cash column semantics,
N8 scan-candle finality (re-scan once a later candle exists), N10 holiday
test fixture, per-thread ccxt instances, remaining SPEC gaps (bullish
engulfing support-alternative, SPEC-vs-code stop widths, volume SMA
self-inclusion, flat-market RSI=100 convention, restart signal dedup,
code_hash on orders, API-error-storm pause).

### D-214. Resting-order simulation in the paper path (CC, closes N5/D-212 gap)
`PaperAdapter.place_pending_buy` + `check_pending_orders` + executor routing:
entries near the ask fill market-now; above -> resting buy-stop (fills as a
market buy at current ask when touched - honest about gaps past the
trigger); below -> resting buy-limit (fills AT the limit, maker fee, no
slippage). Orders expire after valid_for x interval (from the signal row);
position stop/target reconstructed from the signal's features at fill; halt
cancels all resting orders. Placed = acted (an order exists); expiry is
recorded on the order row. Paper execution now matches the backtest's D-103
order semantics. 8 new tests (16 executor tests total).

### D-213. Backtest re-audit findings triaged and fixed (CC, per re-audit report)
The independent backtest-layer re-audit verified every blocking Section 1-2
fix as real, and surfaced new defects, fixed same night:
- **NEW-1 (HIGH): Binance switched kline timestamps to MICROSECONDS at
  2025-01-01.** Mixed-unit merged series corrupted interval inference and
  re-opened regime lookahead on event-harness crypto paths. All load points
  (both Binance mergers + data_loader) now normalize ts >= 1e14 to ms.
- NEW-2: mergers no longer drop the first candle of each headerless monthly
  file. NEW-3: random-twin median is now the TRUE median including inf seeds
  (finite-subset median understated the baseline). NEW-6: vectorized harness
  now SKIPS invalid-stop signals like the event harness (no more silent
  0.25xATR substitute). NEW-7: run_fast_gonogo no longer crashes on
  infinite-PF rows.
- The in-flight graveyard run carried garbage crypto timestamps from NEW-1:
  killed, partial output archived (v0_graveyard_full_pre-us-fix.json, 1,260
  entries), restarted clean on fixed loaders.
- Accepted-not-yet-fixed (queued for Raven's sequencing): NEW-4 delay probe
  morphs order types (anti-conservative stress probe), NEW-5 beats_twin inf
  semantics divergence (report field only), NEW-8 same-bar entry+stop
  optimism, NEW-9 same-bar re-entry divergence, NEW-10 referee small-sample
  corners, NEW-11 loader mixed-format edge, NEW-12 synthetic_options Greeks
  guards, NEW-14 build_graveyard's private yfinance loader, plus the six
  documented-divergence items in the report's section 3.

### D-211. Reconciliation on boot, paper-mode semantics (CC DECISION)
`Executor.reconcile_on_boot()` (called by main.py before threads start):
closes unprotectable positions (missing/inverted stops), marks pre-boot
queued signals 'engine_restart' so the skipped-signal dataset stays complete,
audit-logs the summary. Live-mode reconciliation (exchange comparison,
SPEC 7.3 full) remains TODO before any live launch.


Running record of engineering and process decisions. Newest section first.
Each entry: what was decided, who decided, why, and where it lives in code.
Raven: treat entries marked AYM RULING as settled; entries marked CC DECISION
are Claude Code's judgment calls, open to your review.

---

## v2 - 2026-08-12 (evening session: overhaul + libraries + continuation)

### D-201. Indicators are ta-library-backed via facades (AYM RULING)
Production indicator math comes from the maintained `ta` library. Facades in
`indicators/` keep every original signature and padding convention so no call
site changed; the audited hand-rolled math moved to
`tests/reference_indicators.py` and the cross-check tests
(`tests/test_indicator_crosscheck.py`) now require the two unrelated
implementations to agree forever. Rationale (Aym): fear of silently breaking
hand-rolled math in future edits. Side benefit: the old O(n^2) MACD
signal-line reconstruction is gone. Known delta: ta's EMA seeds differ from
SMA-seeding; convergence verified within 0.5% before any consumer's warmup
guard.

### D-202. Three backtest engines must agree (AYM RULING)
`backtest/cross_harness_check.py` runs an SMA-cross referee strategy through
the event harness, the vectorized harness, and backtesting.py (external),
wired into `validate_harness.py` as assertion A5. Agreement semantics are
documented in the file: exact-ish between our two engines, win-rate-band
against the external one. Calibration note (CC DECISION): external trade-count
tolerance is max(4, 25%) because small-sample tp/sl fill differences shift
position lifetimes and therefore later signal availability; the systematic-
error teeth are the 15-point win-rate band (the audited lookahead bug produced
90%+ win rates). First run: our engines agreed EXACTLY on AAPL and BTC; the
external agreed within tolerance.

### D-203. No bulk import of strategy libraries; docs as research input (AYM+CC)
Strategy libraries are not imported wholesale (multiple-comparisons inflation,
sandbox surface, near-zero new information). Forge may read library
DOCUMENTATION as research input (Aym endorsed). Library-vs-hand-rolled
inventory lives in `docs/handoffs/2026-08-12-claude-code-fixes.md`.

### D-204. Library swaps executed (CC DECISION per inventory + Aym push)
python-dotenv (env loading), vollib (Black-Scholes core; fixed the
zero-volatility branch bug in the process), pandas (CSV parsing internals of
`data_loader.py`, signatures stable), exchange_calendars (NYSE session gating
for D1/D2 via `_in_xnys_session`, replacing weekday checks that traded
holidays).

### D-205. Referee/validation gaps found by the test-suite audit, closed (CC)
- Pipeline-level regime alignment test added (the one mutation that survived:
  helper was tested, wiring wasn't). `tests/test_backtest.py::TestRegimePipelineAlignment`.
- `backtest/test_known_answers.py` converted to real pytest assertions (was
  return-only: pytest reported 6 passed unconditionally).
- Pattern regression tests added for tasuki/FVG/DCA/piercing/on_neck/C2
  (`tests/test_pattern_regressions.py`).
- Buy-hold validation tolerance now scales with return magnitude (fixed
  0.75pp threshold falsely failed correct runs on +714% series).
- Random-twin fee assertion moved to flat data where it is meaningful.

### D-206. Re-audit coverage compromise (CC DECISION, forced by spend limit)
The three fresh-eyes re-audit agents: test-suite auditor COMPLETED (mutation
checks; found the gaps in D-205); the backtest-layer and engine-layer
re-audit agents were TERMINATED mid-run by the monthly spend limit. Mechanical
verification stands in: 120+ tests green, validation suite 21/21 including
oracle/delay/accounting/fee/cross-harness assertions. Raven's review should
weight the backtest and engine layers accordingly; an agent re-audit can be
rerun when the limit resets.

### D-207. Graveyard reset and re-run (CC DECISION)
Old v0 graveyard JSONs are archived to `research/graveyard/archive/` rather
than resumed from: the incremental runner's resume key has no data fingerprint,
so resuming would merge void-era results with fixed-era results
indistinguishably. Fresh run started on the fixed harness + clean data.

### D-208. T9 executor built next (CC DECISION)
SPEC 15 orders T8 (sandbox) before T9 (execution), but the sandbox only
matters once Quant authors strategies (T13+), while the executor is the
missing piece that makes the paper experiment runnable and gives
`check_exits` / `write_equity_snapshot` / `mark_signal_acted` their caller.
Built as `engine/executor.py` + `engine/main.py`. Sandbox remains next in
queue.

---

## v1 - 2026-08-12 (audit + fix session)

### D-101. Builder/verifier separation (AYM RULING)
Claude Code fixes (it wrote the audit), Claude Code re-audits, Raven reviews
against SPEC. Mirrors SPEC 5.7's Forge/Judge separation.

### D-102. Nothing is durable until validate_harness.py exits 0 (CC, Aym-approved)
The validation suite was rebuilt so every control runs THROUGH the harness
(the old controls computed their own inline results and validated nothing).
Oracle sees the future via a control-only channel real strategies can never
receive; its delayed twin must collapse; buy-hold accounting must reproduce
to the cent; fees must strictly reduce PF per seed. `all_pass: false` means
every downstream result is provisional - enforced socially and by the
PROVISIONAL flags in the graveyard JSONs.

### D-103. Entry semantics contract (CC, per SPEC 5.1)
Signals fill per their order type: market at close (+slippage), buy-stop
resting `valid_for` candles (fills at max(level, open) + slippage), buy-limit
resting (fills at min(level, open), no slippage), unfilled orders expire
without a trade. Both harnesses implement it identically.

### D-104. Costs are pessimistic-realistic (CC)
Gap-through stops fill at min(stop, open); stops/time/end exits pay slippage,
resting-limit targets don't; infinite PF fails every gate (zero losses over a
test window means bug or tiny sample, never edge).

### D-105. Buy-and-hold comparison in dollars (CC, per prior review)
Same fixed notional, same window, fees on both sides. The old avg-per-trade-%
vs full-period-% comparison produced a shipped false positive.

### D-106. All price data split+dividend adjusted (CC)
One convention everywhere. Loader flags >40% close-to-open gaps;
check_data_integrity.py scans and quarantines; Alpaca script now requests
adjustment='all'. Weekly bars for split-heavy leveraged ETFs are rebuilt from
clean daily data (Yahoo's weekly bars are inconsistent around splits).
NG_F quarantined (futures roll gaps are not fixable by adjustment), SOXS
quarantined (broken at source), MULN quarantined (delisted from Yahoo - which
is why it is the survivorship canary).

### D-107. Scanner writes acted=0, always (CC, per SPEC 9.3)
Only the execution layer flips acted=1 after the risk gate approves
(`db.mark_signal_acted`). Keeps the skipped-signal learning dataset honest.

### D-108. 4-loss pause expires after 24h (CC, per SPEC 6.2)
Keyed on the last loss's closed_ts. Without expiry the pause was a permanent
deadlock (blocked entries can never produce the streak-breaking win).

### D-109. NOT_TESTED is a verdict (CC)
A strategy whose min_bars exceeds the scan window is reported NOT_TESTED,
never tested-and-failed. Graveyard truthfulness over graveyard completeness.

### D-110. Alpaca key rotation owed by Aym (SECURITY)
Key was hardcoded in source (now removed, .env-only). Rotate at Alpaca.
