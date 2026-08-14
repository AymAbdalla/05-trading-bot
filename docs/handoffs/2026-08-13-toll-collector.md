# Handoff — Lab v5 P2 "TOLL COLLECTOR" (2026-08-13)

## What was built
- `backtest/toll_collector.py` — self-contained maker-only passive-reversion
  experiment engine. Shares NO simulation code with the harnesses (they are
  mid-graveyard-run); copies the Binance CSV loader from
  `run_incremental_graveyard.py` by design. Uses `CostModel` for all fees
  (crypto maker 0%, taker 0.01% core / 0.02%), stamps `cost_model_version`.
  - Conservative maker-fill simulator per SPEC 5.9: resting limit BUY fills
    ONLY if bar low < limit − 1 tick (touch never fills); mirror rule for the
    limit SELL exit. No gap price improvement on maker legs. Same-bar stop on
    the fill bar (deterministic on a flush-through). Every ambiguity resolved
    AGAINST the maker hypothesis.
  - Design per P2: limit buys at k·ATR14 below last close, k ∈ {1.5,2.0,2.5};
    armed only when 24h realized vol > 70th pctile of trailing 720 bars,
    percentile from STRICTLY-past data. Exit maker at fill + 1.0·ATR; taker
    stop at fill − 1.5·ATR; 72-bar taker time backstop. Order rests while
    armed, re-priced each bar close, cancelled when arming lapses. $100 fixed
    notional (SPEC 6.1), one position per (pair, k).
  - Comparison arm: identical limit levels executed taker-at-touch (the
    kill-condition instrument).
  - Fires-check (armed %, orders, fill rate, taker-stop rate) computed,
    printed and serialized BEFORE any P&L (v5 work order 4).
- `tests/test_toll_collector.py` — 13 tests, all passing: trade-through rule
  (touch / limit−tick / through, both sides, plus engine-level maker-vs-taker
  asymmetry), arming-percentile lookahead fixture (including the current bar
  would flip the decision; asserted it doesn't), zero maker fees/slippage,
  taker stop pays core-pair fee + slippage, gap-through-stop fills at open,
  fires-check fields present and ordered before P&L, small-sample shrug note.
- `research/graveyard/toll_collector.json` — full results, cost model
  `2026-08-13`, per-trade maker records included.

## Results (BTCUSDT/ETHUSDT/SOLUSDT 1h, 2024-10 → 2025-09)
- Fires-check: armed 31.8% of eligible bars; 1,556 order episodes; 516 maker
  fills (33.2% fill rate, falling from ~48% at k=1.5 to ~16% at k=2.5);
  taker-stop rate 39.0% (127 stops + 74 same-bar stops vs 315 maker targets).
- Kill condition: NOT fired, but narrowly. Maker net +2.5 bps/trade vs taker
  net −8.7 bps/trade → maker wins by 11.2 bps against fee+slip savings of
  12.6 bps. Implied adverse-selection cost of demanding trade-through:
  ~1.3 bps — real but far smaller than the fee discount.
- Pre-registered prediction: REFUTED in direction. Edge is NOT concentrated
  in the top vol band: fills arming ≥90th pctile net −7.8 bps (n=155,
  t=−0.42); the 70–90 band nets +7.0 bps (n=361, t=0.82). Inverted.
- Power honesty (rule 7): maker mean +2.5 bps carries t=0.31 on std 186 bps —
  indistinguishable from zero. 516 fills clears the 400 floor but nothing
  here is a strategy verdict; the solid measurement is the EXECUTION
  comparison (maker vs taker on the same levels), not the P&L.

## Skipped / deferred
- Data is ~12 months per pair, not the ~2 years hoped for; single regime.
- m (exit ATR multiple) fixed at 1.0, stop at 1.5·ATR — pre-committed, never
  swept. Sweeping them now would be exactly the scan-for-conditions sin.
- Taker-arm per-trade records not serialized (summary stats only) to keep the
  JSON lean; maker trades are all there.
- No integration with the shared harnesses or graveyard schema — intentional.

## Questionable / for Raven
- Fee rates still UNVERIFIED against the live Binance.US account (HANDOVER
  §5.1). The whole maker-vs-taker margin (11.2 vs 12.6 bps) sits inside the
  error bar of that unverified 0% maker assumption.
- "Orders placed" = contiguous resting spells (episodes), with per-bar
  re-pricing inside a spell. A different lifetime convention would change
  fill-rate denominators, not fills.
- Taker arm gets touch fills and gap improvement the maker arm is denied;
  asymmetries deliberately favor taker. A maker win is therefore conservative,
  but the taker arm also fills a slightly different trade set (517 vs 516) —
  that set difference IS the adverse selection being measured.

## Next steps
- If pursued: re-run once fee rates are account-verified; consider 15m data
  (already on disk) for finer fill resolution on the same design, and a
  second year of 1h data for a second regime.
- The inverted vol-band result (top-decile arming loses) is a new pre-
  registrable hypothesis for a v6 proposal, not a knob to tune here.
