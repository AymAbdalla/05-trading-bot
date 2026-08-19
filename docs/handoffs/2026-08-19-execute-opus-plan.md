# Execute Opus plan (D-329) — verified and committed

**Raven, 2026-08-19 01:20.** Written from verification of the tree (the cody-execute-plan session died before its epilogue; its work is committed at `4d03681` + `67b0501`, pushed). Everything verified by my own run.

## What shipped (D-329, ratified)

1. **Mirror-fade probe PAUSED (D-326 amended).** `strategies/polymarket/fair_value_mirror_fade.py` (22KB) + tests built, shipped PAUSED via the D-322 sentinel (`supported_market_types = ('smart_money',)`) with the evidence-cited comment. Kill condition recorded: fade thesis dead unless taker-only settled mirror PnL reaches t >= 2.0 on n >= 250 excluding ask <= 0.10 (today: t=1.19, n=116).
2. **Q3 measurements landed:**
   - `positions.fill_was_maker` (schema, INTEGER NOT NULL DEFAULT 0) — fill provenance never again pooled. Backfilled false.
   - `counter_ask` stamped on fair_value signals (complement token's best ask) — the prerequisite for the structural no-arbitrage family.
3. **Convention 32:** any fade/mirror claim is reported split by fill_was_maker, never pooled.
4. Env B whitelist correction noted for next natural restart (drop dip_arb + wide, add corridor_collector).

## The Opus correction (why D-326 changed)

The +$281.74 mirror signal was 80% maker fills — adverse selection by the simulator's fill rule (paper_adapter fills a resting BUY only after price fell below the limit, booking at the limit; the fill exists only in states that already moved against us). Not a market measurement. The executable taker portion: t=1.19, below the t>=2.0 bar. The fair_value model is still bad (slope 0.30, all strategies below break-even WR) — only the REMEDY changed.

**The fact that reorganizes the program:** temporal_arb's 83 uncensored positions show the market price calibrated to within 0.06 percentage points (paid 0.1813, realised 0.1807). The market is efficient on the only unbiased sample we own. Path forward = STRUCTURE (complement no-arb, strike monotonicity, resolution mechanics), not forecasting.

## Verification

- Full suite: **3,850 passed, 1 skipped, 0 failed** (my own run, 7 min).
- Harness: 21/21 (reported by verify-commit-restart session on the same tree).
- Main loop PID 41735 confirmed live on the D-323 source (17 strats per asset). Env B PID 38881 live on the survivor book (8 strats).
- Both loops: entries flowing (22 and 24 respectively as of 01:19).

## Standing

- Both books run the corrected configuration: bleeders paused, cap 10, fill provenance recorded, fade probe paused but present.
- Forge's next 4h tick reads the digest (now cheaper) + the corrected edge assessment.
- Next natural actions: env B whitelist correction at its restart; the structural strategy family (complement no-arb) needs counter_ask data to accumulate first.
