# D-366 executed: percentage-based max position size, three realms restarted

**Session:** `cody-D366` (pid 38391). **2026-08-20, 14:33 EDT / 18:33 UTC.**
**Brief:** `docs/handoffs/from-raven/2026-08-20-D366-position-pct.md`.
**Commit:** `31e5220` (implementation). HEAD before: `f5e70f8`.

## Status: DONE. All three realms are live on the D-366 code.

Suite **4,222 passed / 1 skipped / 0 failed** (401s). Harness **21/21, ALL
PASS**. Both re-derived this session, after the change.

## What was built

`engine/polymarket/paper_adapter.py`

- `DEFAULT_MAX_POSITION_PCT = 0.90`, overridable per book with the config key
  `max_position_pct`. **`config.yaml` untouched.**
- `max_position_cost()` = `get_equity() * max_position_pct`, floored at 0.
  "Available" is `get_equity()`: starting capital + realized PnL - premium
  already tied up in open positions.
- `shares_within_position_pct(shares, limit_price)` floors to whole shares that
  fit under that ceiling.
- **Both entry paths clip and fill: `simulate_taker_buy` and
  `simulate_maker_buy`.** No skip. The order shrinks and goes through.
- The **only** refusal added is physical, not policy: when 90% of what is left
  cannot buy the 5-share exchange minimum, `SKIP unsizable_at_position_pct`.
  That order could not have existed at any size. D-358 fund-if-zero is the exit
  from it. It is classified OPERATIONAL in `scripts/shadow_summary_lib.py`, not
  NO_TRADE, so a book that needs re-funding cannot read as "no signal".
- Clips are counted in a new `sizing_counts` dict, deliberately **not** in
  `decision_counts` - that one holds a one-count-per-CSV-row identity and a
  clipped order still writes its own ENTER row. Same precedent as
  `maker_counts`. Each clip also logs an INFO line naming both sizes.
- The ENTER row keeps the ORIGINAL request in `requested_shares` and the clipped
  size in `filled_shares`, so a clip is readable in the CSV, not inferred.

`engine/polymarket/shadow_loop.py` - `lift_shadow_capital_caps` docstring now
records that D-366 answered the capping half of the open ruling.

`tests/test_polymarket_paper_adapter.py` - 11 new tests, `TestPositionPercentCap`,
including the two numbers from Aym's ruling verbatim ($1,000 -> $900, $100 ->
$90), the size-down-not-skip lock, and the decay lock ($100 book: first entry
$90, second $9, third $0.90 - entries alone can never zero the book).

## Two judgement calls Raven should look at

**1. `notional_cap_usdc` is still $10 and is NOT lifted.** The brief said
"replace the D-365 sentinel with a percentage cap", and D-366 R4 says the
sentinel lift is replaced. Under `sizing_mode: flat` that number is the ORDER
SIZE, not a ceiling, and D-366 **R3 explicitly defers per-trade percentage
sizing to a future feature**. So orders are still sized at $10 by the gate, and
the 90% ceiling binds only when available capital falls to roughly the order
size - which is exactly the scenario Aym ruled on ("if the available capital is
less than the capital needed for the trade"). **Comparability with every trade
measured so far is intact.** If Aym wants trades to actually BE 90% of the book,
that is the R3 sizing feature and it needs its own ruling.

**2. The `over_notional_cap` check was kept, moved AFTER the clip.** The brief
said "instead of the notional check". I kept it because it is the guard against
a caller sizing above the declared cap ("an unbounded fabricated -PnL surface"),
and it also protects the backtest and real-money-shaped paths that share this
adapter. Since the percentage ceiling can only ever shrink an order, it refuses
exactly what it refused before - no behaviour change, verified by test. Say the
word and I will drop it.

## Restart and sweep

Pre-restart snapshots via the sqlite **backup API** (never `cp`):
`db/snapshots/{trading,trading-survivors,trading-realm-c}.db.pre-D366-20260820T182933Z`.

SIGTERM -> relaunch -> verify. **Env B and realm C died in ~6s; main took ~30s**
(it exits at cycle end, not on the signal).

| realm | pid | tmux | db | started |
|---|---|---|---|---|
| main (16 names) | **40841** | `shadow-main` | `db/trading.db` | 14:30:42 EDT |
| env B (4 fair_value) | **40884** | `shadow-survivors` | `db/trading-survivors.db` | 14:31:09 |
| realm C (6 un-paused) | **40927** | `shadow-realmc` | `db/trading-realm-c.db` | 14:31:36 |

All three banners read `commit: 31e5220`, `launched-by: cody-D366`. All three
are entering trades. No ERROR or Traceback in any log.

**Orphan sweep run AFTER the restart, against the NEW pids, as the restart
manufactured them:** main 13 rows / $33.07, env B 9 / $33.80, realm C 18 /
$63.79. **40 rows, $130.66.** `integrity_check ok` on all three,
`still open pre-bnd: 0` on all three.

## What is NOT done

- **R3 percentage per-trade SIZING (1-90% by conviction).** Deferred by the
  ruling itself. Not started.
- **The maker fill path is not re-checked at fill time.** A resting order is
  capped at REST time, when it ties up no premium; re-checking at fill would
  mean cancelling a maker order for lack of funds, which is the refusal D-366
  forbids. Residual: several orders resting at once are each capped against the
  same available capital. `box_builder`/`grid_hedge` rest one at a time and have
  booked no entries at all, so this is currently theoretical. Named, not hidden.
- **No D-366 clip has fired yet.** All three books were re-funded to $1,000 at
  restart and orders are $10, so the ceiling sits at $900 and does not bind.
  First evidence will be a `taker_capped_at_position_pct` count or an
  `unsizable_at_position_pct` skip once a book bleeds down. Nothing to read yet.

## Safety, unchanged and worth restating

Real-money `DEFAULT_LIMITS` (10/30/60, drawdown 0.25) is **unchanged** and still
asserted by a test. No auto-halt, no count cap, no daily loss breaker. D-366
adds the first thing that resembles a floor: entries alone can no longer zero a
book. **Nothing else stops a bleed except a human reading equity.** Realm C is
still deliberately running six strategies that were paused on measured bleed.

## Next steps for Raven

1. Rule on judgement call 1 (is $10 order size still what Aym wants, or is R3
   sizing the actual ask?) and 2 (keep or drop `over_notional_cap`).
2. In a few hours, check `sizing_counts` / the CSV for the first clip.
3. Aym owed, unchanged: rotate the Alpaca key (D-262), supervised paper run +
   kill-switch drill (D-264), ratify D-217 SOUL rules (D-244).
