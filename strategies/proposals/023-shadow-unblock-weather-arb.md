---
name: "shadow_unblock_weather_arb"
thesis: "PM_weather_arb has never evaluated its own entry condition in the shadow loop: it skipped 9423 of 15264 evaluations on 'resolution_station_unknown', which is a missing input (the resolution station) rather than a false condition. Supplying that input is what turns this strategy from NOT_TESTED into testable."
expected_edge_bps: null
kill_condition: "After the resolution station is supplied, if PM_weather_arb still enters on fewer than 1% of evaluations over 500 or more shadow cycles as measured by agents/forge_shadow_eval.py against db/trading.db, and scores no better than 0 net cents per share over 200 or more resolved positions in backtest/polymarket_harness.py, it is retired rather than repaired a second time."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Unchanged. This is a repair to the CONTEXT the strategy is handed, not to its logic: the resolution station must be present and correct before any entry rule of this strategy has been exercised even once."
data_requirements: "BLOCKER: the resolution station. Measured over 15264 live shadow evaluations, 97.7% of skips were data-blocked. Until that input exists this strategy is NOT_TESTED (convention 11) and must not be reported as having looked and declined."
related_graveyard_findings: "None. PREDICTION_MARKET has no graveyard rows at all, so this proposal rests on live shadow measurement rather than on a buried family. D-268: every Polymarket strategy is NOT_TESTED until backtest/polymarket_harness.py scores it."
kind: repair
status: PROPOSED
source: "agents/forge_shadow_eval.py over db/trading.db (measured, live shadow session)"
forge_warnings: "none"
---

## What was measured

Source: `db/trading.db` `signals`, read by `agents/forge_shadow_eval.py`. Session covers 307632 decision rows across 20 strategies.

`PM_weather_arb`:

| Bucket | Count |
|---|---|
| evaluations | 15264 |
| entries | 0 |
| skips classed DATA_BLOCKER | 14914 |
| skips classed GENUINE | 350 |

Dominant skip reason: `resolution_station_unknown` (9423 of 15264).

## Why this is NOT_TESTED and not a failure

A skip that names a missing input is not the strategy declining. It is the strategy never being asked. Convention 11 says NOT_TESTED means "could not run", never "ran and found nothing", and reporting this strategy as having produced zero entries without that label would put a verdict in the record that the evidence does not carry.

## The honest limit

This says nothing about whether the strategy has an edge. It says the question has not been asked yet. The edge estimate is null on purpose: inventing a bps figure here would be a fabricated number, and fabricated numbers get cited.
