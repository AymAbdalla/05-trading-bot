---
name: "pm_offcrypto_tape_bootstrap_probe"
thesis: "This is an experiment to convert a permanent NOT_TESTED into a testable question. Five spaces poll and four of them are off-crypto: weather at 60s with 8 strategies, and event, sports and political at 60s each with 7. Verified by observation on 2026-08-18 at 17:25 PT, 3,610 off-crypto rows landed in signals since restart and exactly 0 were acted. The breakdown is the whole story. The fair_value family logs 494 rows each on fair_value_model_needs_crypto_spot, which is correct and permanent: the model is a crypto price model and no amount of work makes it price a temperature. smart_money_copy logs 494 on no_trade_in_this_market, which is a market-coverage question. But dip_arb logs 494 rows on insufficient_tape, and insufficient_tape is not a verdict. It means the strategy is still building history and has never once evaluated its own condition off-crypto. Under convention 11 that is NOT_TESTED, not a decline. The participant whose behaviour we would eventually be trading against is unknown, and pretending otherwise would be inventing a thesis, which is why this is an experiment with a null edge and not an edge hypothesis."
expected_edge_bps: null
kill_condition: "Run the tape bootstrap for 14 calendar days. If, after 14 days, fewer than 3 distinct off-crypto markets have accumulated enough tape for dip_arb to evaluate its actual entry condition even once, as counted by forge_shadow_eval on the per-space skip reasons, the off-crypto spaces are not tapeable at the current 60s cadence and the four non-crypto pollers should be switched off or re-cadenced rather than left burning cycles. Second kill: if tape accumulates but the resulting evaluations produce fewer than 20 acted signals across 100,000 off-crypto evaluations in shadow_loop, retire off-crypto routing for the dip_arb family on frequency."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: |
  Phase 1 is instrumentation and produces no trades at all. Phase 2 trades only if phase 1 clears its gate. Both phases are specified so this can be coded without asking a question.
  
  PHASE 1, tape bootstrap, no entries.
  1. For each off-crypto market observed by the event, sports, political and weather pollers, persist every observed mid, best bid and best ask to a per-market tape table keyed by market_id and timestamp. Today those observations are consumed and dropped, which is why insufficient_tape never clears.
  2. Persist the tape ACROSS loop restarts. This is the single change that matters. A restart today resets the in-memory tape to empty, so a 60s poller can never accumulate the sample a 5s poller reaches in minutes. 494 rows at 60s is roughly 8 hours of continuous uptime, and every restart returns it to zero.
  3. Emit a new per-market counter tape_rows_available on every evaluation, so insufficient_tape stops being a single undifferentiated bucket. Convention 20: two drop causes never share one counter, and right now not enough rows yet and this market is not tapeable are the same number.
  4. Flush space.counts to stdout alongside stats['counts'] at shadow_loop.py:3800, so off-crypto dispositions are visible without opening the DB. This is open item 6 in CLAUDE.md and this experiment cannot be monitored without it.
  
  Gate between phases: dip_arb must reach its required tape length on at least 3 distinct off-crypto markets and log at least 200 evaluations where the entry condition was actually computed rather than short-circuited.
  
  PHASE 2, and only after the gate.
  Entry: unchanged dip_arb logic, whatever it already computes on crypto, now running on a real off-crypto tape. Do not write new entry logic for this experiment. The entire point is to find out what the existing logic says when it is finally allowed to speak.
  Size: 5 shares. Fixed.
  Stop: 0.00. A losing binary share resolves to zero and there is no exit path between entry and resolution on the paper adapter for these markets. 0.00 is strictly below any entry price above zero, satisfying convention 8.
  Target: 1.00, resolution.
  Hard cap for the experiment: maximum 3 concurrent off-crypto positions and maximum $50 total notional at risk across the whole off-crypto experiment. This is a probe, not a deployment.
data_requirements: |
  1. Off-crypto market books from the event, sports, political and weather pollers. HAVE IT. All four spaces poll at 60s and 3,610 rows landed in signals since restart, verified by observation on 2026-08-18 at 17:25 PT.
  2. A persistent per-market price tape that survives loop restarts. DO NOT HAVE IT. This is the missing input and it is what makes this a repair-shaped experiment rather than an edge hypothesis. Its absence is the direct cause of all 494 dip_arb insufficient_tape rows.
  3. A per-market tape_rows_available counter. DO NOT HAVE IT. insufficient_tape is currently one bucket covering at least two distinct causes.
  4. space.counts flushed to stdout. DO NOT HAVE IT. shadow_loop.py:3800 prints stats['counts'], the crypto identity only. This is why a previous session grepped the log, found zero, and reported zero for a disposition that had 2,470 rows in the signals table.
  5. Resolution outcomes on off-crypto markets. PARTIALLY HAVE IT. Crypto up/down resolution is proven by the 1,365 closed positions. Whether event, sports and political markets resolve on the same code path with the same 1.00 or 0.00 settlement is not established anywhere in this brief and must be confirmed before phase 2 trades a single share. If it does not, phase 2 cannot run and the experiment ends at phase 1.
  6. signals table retention. AT RISK. Measured at roughly 460k rows per day against 377,904 rows now, with the 30 and 90 day retention policy ruled but not built as open item 2 in CLAUDE.md. A 14 day experiment that outlives its own evidence proves nothing, so retention should land before or alongside this.
  7. backtest/validate_harness.py exiting 0, per convention 1.
markets: "Polymarket event, sports and political markets on the 60s polling cadence, plus weather. Explicitly NOT crypto up/down."
kind: experiment
status: PROPOSED
source: "forge"
forge_warnings: "no_graveyard_link_warning"
---

## The observation this rests on

Verified by observation on 2026-08-18 at 17:25 PT, not inferred from code. Since the loop restart, 3,610 off-crypto rows landed in the signals table and 0 were acted.

| strategy group | rows | reason |
|---|---|---|
| fair_value family, 5 variants | 494 each | fair_value_model_needs_crypto_spot |
| dip_arb | 494 | insufficient_tape |
| smart_money_copy | 494 | no_trade_in_this_market |
| weather_arb | 152 | across three reasons |

Those three reasons are not the same kind of thing and treating them as one number is the error this experiment exists to avoid.

fair_value_model_needs_crypto_spot is permanent and correct. The model prices crypto. A temperature market has no crypto spot and never will. Five of the seven off-crypto strategies are in this family and no work unblocks them.

no_trade_in_this_market is a coverage question about which wallets trade which markets, and it is already the subject of a deterministic repair.

insufficient_tape is the one that is neither. It means dip_arb has not yet seen enough price history to compute a mean. It has never evaluated its entry condition off-crypto even once. Under convention 11 that is NOT_TESTED, and NOT_TESTED means could not run, never ran and found nothing.

## Why the tape never fills

The crypto space polls at 5s. The four off-crypto spaces poll at 60s. That is a 12x difference in observations per unit time, so any fixed tape-length requirement takes 12x longer to satisfy off-crypto.

On top of that, the tape does not survive a restart. 494 rows at 60s is roughly 8 hours of continuous uptime. The loop has restarted several times in the recorded history, most recently at 16:58:22 on 2026-08-18. Every restart returns the off-crypto tape to zero, and 8 hours of uninterrupted uptime is not something this system currently delivers.

So insufficient_tape is not evidence that off-crypto markets lack price history. It is evidence that we throw the history away. That is a plumbing defect, and phase 1 fixes the plumbing before anyone argues about the edge.

## Why expected_edge_bps is null

Convention 11 and rule 3 in the brief both require it for kind experiment, and the requirement is correct here on the merits. dip_arb has never computed its entry condition on an off-crypto market. There is no basis for any number. Its crypto record is 21.2% win rate on 33 trades and 97 of 138 classified entry_signal_wrong, which is a reason for pessimism rather than a number to carry across to a different market type. Writing 250 bps here would be an invention, and inventing a number for something that has never run is worse than admitting there is none.

## What phase 1 costs and what it returns

Cost: one persistent tape table, one new per-market counter, one stdout flush at shadow_loop.py:3800, and 14 days of wall clock. No new entry logic, no trades, no risk.

Return, regardless of the outcome:
1. Whether off-crypto markets are tapeable at 60s at all. Currently unknown.
2. A split of insufficient_tape into not enough rows yet and this market is not tapeable, which are one bucket today. Convention 20: a silent continue is a missing number and two drop causes never share one counter.
3. Off-crypto dispositions visible in stdout, closing the exact hole that made a previous session report 0 for a disposition with 2,470 rows.

Item 3 has value even if this experiment is killed on day 14, and that is the argument for doing phase 1 first and separately.

## Scope, stated narrowly and on purpose

This experiment unblocks dip_arb, and possibly smart_money_copy if its wallet coverage repair also lands. It does not unblock the fair_value family, ever. It does not unblock weather_arb, whose blocker is a fitted sigma of 2.74F RMSE per station-day against the 1.334F a 1.8F bucket needs, and the vault instruction there is explicit: fit sigma, do not lower the floor.

One and a half strategies. That is the honest size of the prize and it should be weighed against the build cost before anyone starts.

## What would change my mind

1. If off-crypto markets turn out not to resolve on the same 1.00 or 0.00 settlement path as crypto up/down, phase 2 cannot run at all and this ends as a pure instrumentation change. Confirm the settlement path before building phase 2.
2. If the tape fills easily once persisted and dip_arb still logs a genuine skip on every off-crypto evaluation, that is a real answer and a good one. NOT_TESTED becomes TESTED and looked and declined, which is worth the 14 days on its own.
3. If the signals retention work, open item 2, does not land first, a 14 day experiment at roughly 460k rows per day may outlive its own evidence. Sequence retention ahead of this or the measurement window is not readable at the end.

Named harness: backtest/validate_harness.py must exit 0, per convention 1. Kill scoring is forge_shadow_eval on per-space skip reasons and shadow_loop evaluation counts.

## Why this might fail

The strongest argument against: the off-crypto skip reasons may be correct and permanent rather than a plumbing gap, in which case this builds infrastructure for a set of markets that will never trade. The evidence is genuinely split on this and the split matters. The fair_value family's 494 rows on fair_value_model_needs_crypto_spot are definitively permanent, and the CLAUDE.md wake-up file says so directly: the model is a crypto price model, only the entry machinery is market-agnostic. That is 5 of the 7 strategies in the event, sports and political spaces, and no tape fixes any of them. weather_arb has its own hard blocker, rung_narrower_than_model_resolution, with a fitted 2.74F RMSE per station-day at the 24 to 48 hour lead against the 1.334F a 1.8F Celsius bucket needs, and the vault is explicit that the answer is to fit sigma and not to lower the floor. So the honest scope is narrow: this experiment is really about dip_arb, and possibly smart_money_copy, and about nothing else. One and a half strategies is a thin return on persistent tape storage plus a new counter plus a stdout flush.

Second, dip_arb is already a failure on crypto where it has plenty of tape. hypothesis_graph id 112 records win rate 21.2% on 33 closed trades, net -33.06, failure_mode stop_too_tight with 78.8% of closes stop-like, and id 138 records 97 of 138 closed trades classified entry_signal_wrong by agents/critic.py. Giving a strategy with a wrong entry signal more markets to be wrong in is a way to lose money in four new places. The counterargument is that entry_signal_wrong on crypto up/down 5m windows is a statement about crypto up/down 5m windows, and a sports market is a different generating process. But that counterargument is a hope, not evidence, and it is exactly the kind of hope this project's conventions exist to refuse.

Third, the CLAUDE.md wake-up file warns in plain terms not to read can as has: dip_arb CAN enter off-crypto but HAS NOT, and all 494 rows are insufficient_tape. This proposal is built on that distinction and it is worth stating that the distinction cuts both ways. Removing the blocker reveals the answer; it does not promise the answer is yes.

Fourth, 60s cadence may simply be too slow. If a sports or event market's tradeable dislocation lasts under a minute, a 60s poller sees the price before and the price after and never the opportunity. Persistent tape does not fix a sampling rate that is below the frequency of the phenomenon, and nothing in this brief measures that frequency.

## What past failure this addresses

Directly connected to hypothesis_graph id 112 (PM_dip_arb, failure_mode stop_too_tight, win rate 21.2% on 33 closed trades, net -33.06) and id 138 (PM_dip_arb, failure_mode entry_signal_wrong, 97 of 138 closed trades classified by agents/critic.py). Both verdicts were reached exclusively on crypto up/down windows. This experiment does not dispute either verdict and does not propose repairing dip_arb's crypto behaviour. It asks a different question: whether the same entry logic on a structurally different market type produces a different result, and it is honest that the answer may be no.

It also addresses a documented meta-failure rather than only a strategy failure. The CLAUDE.md wake-up file opens with it: a previous session grepped the shadow log for fair_value_model_needs_crypto_spot, found zero, and reported zero, when the true count in the signals table was 2,470. The stated root cause is that shadow_loop.py:3800 logs stats['counts'], the crypto identity only, and space.counts is never flushed to stdout, so grepping the log for a space skip reason returns 0 by construction. Phase 1 item 4 fixes that specific defect, which is also open item 6 in the ruled-but-not-executed list. Convention 22 says a constant that exists is not a cycle that runs; the corollary this session established is that a counter that is never flushed is not a count you can read.

On the vault notes: 2026-08-18-fair-value-arb-spread-problem.md is cited here for scope limitation rather than for a fix. Its family is 5 of the 7 strategies routed to event, sports and political, and its 616 trade, -$338.60, spread_eats_edge verdict plus the crypto-spot dependency together mean the fair_value family is out of scope for any off-crypto work permanently. Naming that up front is what stops this experiment from being sold as unblocking seven strategies when it is really about one and a half. 2026-08-18-corridor-pair-works.md supplies the stop rule used here: its prohibition 6 says do not propose a variant whose stop is anything other than 0.00 on a binary held to resolution, because no exit path exists between entry and resolution on the paper adapter.

This is deliberately not a duplicate of the three deterministic repairs already written, shadow_unblock_liq_cascade_chaser, shadow_unblock_smart_money_copy and shadow_unblock_weather_arb. Those unblock named single strategies on named missing inputs. This is a shared-infrastructure experiment on the tape layer under four polling spaces, and its phase 1 deliverable, persistent cross-restart tape plus a split counter plus a space.counts flush, is a loop-level change that no per-strategy repair produces.

## Forge warnings (non-blocking)

These used to be refusals. They no longer block a proposal, and they are recorded here so the information survives the downgrade.

- **no_graveyard_link_warning**: no related graveyard finding. Expected for PREDICTION_MARKET, EVENT and SPORTS: the graveyard has no rows in those classes.
