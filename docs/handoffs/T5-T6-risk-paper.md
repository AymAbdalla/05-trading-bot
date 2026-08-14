# T5+T6 Handoff: Risk Gate + Paper Adapter

**Date:** 2026-08-12
**Built by:** Raven (directly on Aym's machine)
**Tasks:** T5 (risk gate) + T6 (paper adapter)

## What was built

### T5: Risk Gate (`engine/risk.py`)
- Fixed notional cap: $100/trade, does NOT scale with balance
- Fee-to-edge gate: rejects trades where fees > 15% of edge (entry-stop distance)
- Max trades/day: 1 (config-driven, start at 1)
- Max concurrent positions: 2
- Max positions per pair: 1
- Consecutive loss pause: 4 losses = 24h pause
- Ops backstops: daily (3x notional) and weekly (15x notional) equity drop limits (tail-event, not primary trading risk)
- Returns RiskVerdict dataclass with approved/reason/qty/notional/fee_cost/edge
- Pure functions, no side effects. Execution layer calls check_order() before every order.

### T6: Paper Adapter (`engine/adapters/paper.py`)
- Market buy fills at ask + adverse slippage (F8/R8 fix: NOT mid)
- Market sell fills at bid - adverse slippage (F8/R8 fix: NOT mid)
- Taker fee 0.10% applied on both buy and sell
- Open position: market buy + position row with stop/target
- Close position: market sell + PnL computation (gross, net, fees, R-multiple)
- Equity tracking: starting_equity + sum of closed PnL
- Audit log written before order (same pattern as live)
- Order and fill rows written to DB with correct FK relationship

## Bug fixed during build

- `fills.order_id` is a FK to `orders.id` (the UUID PK), not `orders.cl_ord_id`. Fixed both buy and sell methods to use a separate `order_pk` for the fill reference.
- `close_position` query that retrieves buy fee was joining on `cl_ord_id` instead of `id`. Fixed to join through `positions` table for correct pair/time matching.

## Test results

43/43 passed across T4+T5+T6:
- T4 (scanner): 13 tests
- T5 (risk gate): 16 tests
- T6 (paper adapter): 14 tests

## Next steps

T7: Backtest harness. This is the moment of truth.
