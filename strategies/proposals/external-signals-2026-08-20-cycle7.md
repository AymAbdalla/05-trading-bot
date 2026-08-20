# External Signals - 2026-08-20 Forge Cycle 7 (~13:15 UTC)

**Generated:** 2026-08-20 13:15 UTC (cron Forge eval loop, 4h tick)
**Source:** X API (xurl search, last 24h, 5 queries x 15 results) + web search (WebSearch bridge)
**Status:** RAW, awaiting Opus reasoning distillation
**Note:** Reddit (r/Polymarket, r/algotrading) crawler-blocked again this cycle
(www and old.reddit JSON both return block pages; the search bridge refuses
reddit.com domains). Same as cycles 2-6. X + web only.

---

## Signal 1: Maker-rebate RUGGED claim - the fee-surface story gains a mechanism (STRUCTURAL, HIGH for 042 context)

**Sources:**
- X @AgentBotega (3 likes, 1 rt, 12:58 UTC): "If you are Market Making on Polymarket you might be getting rugged. Polymarket is giving max tier Taker Rebates to handpicked whales behind the scenes. These whales make a big % of the volume and if they fill your orders you basically won't get any Makers rebate since they pay..." (thread, truncated)
- X @ProMint_X (47 likes, 10:38 UTC): "Polymarket Rebate Scam. Zero fees for whales. Full price for you. The Polymarket team was secretly zeroing out fees and paying rebates to the most aggressive whales and bots. Polymarket is literally reaching into the pockets of makers (the ones providing liquidity)."
- X @IssouChancla88 RT @AgentBotega (13:00 UTC): same claim.

**Claim:** The Aug 19-20 rebate story (cycle 6 Signal 1: taker rebates up to 50%, maker rebates up to 25%, special-user 80%) now has a sharper mechanism: the maker rebate is a share of the taker fee, so when a whale with max-tier taker rebate (effectively zero fee) sweeps a maker order, the maker's rebate is reduced or zeroed. The fee surface is not uniform per side - it is conditioned on WHO the counterparty is.

**Relevance to shadow (HIGH, context only):**
1. 042 (maker-fill markout probe) measures maker economics. If the counterparty tier conditions the maker rebate, the probe's markout carries a hidden conditioning variable (counterparty identity). This is a measurement caveat for 042's kill ceiling, NOT a ceiling re-size: press-reported numbers stay unverifiable from inside the system (unverified external claim rule; same posture as cycle 6). The reasoner should decide amendment (record the conditioning caveat) vs scenario note.
2. maker_rebate_quote_ladder is LIVE in trading.db (n=12, -18.65 this window per tick 7; lifetime maker fills 87/-39.35 in env A, 0 in env B). If the maker rebate is counterparty-conditioned, the ladder's assumption of uniform rebates is weaker than written. Measurement note, not a kill.
3. The 80% special-user asymmetry is not actionable for us (paper book, not a special account) but changes market-wide maker economics that our maker strategies sit inside.

## Signal 2: Quant MM bot claims on EXACTLY our universe (short crypto Up/Down) - hypothesis-generating for the maker family (MEDIUM)

**Sources:**
- X @codewithimanshu (27 likes, 15 rts, 09:43 UTC): "A trader built a QUANT bot using Claude Fable 5. Result: $81,323 profit on Polymarket. 25,511 predictions in 54 days. 46% win rate. $1,506 per day on average. The strategy is surprisingly simple: 1. Fast market-making model focused on short crypto Up/Down markets 2. No need [truncated]"
- X @taylorquinn_ai (36 likes, 26 rts, 03:03 UTC): same claim, "$500 and a $20 Claude subscription".
- X @AI_creator_Yara (57 likes, 44 rts, 08:59 UTC): same claim; adds "He focuses on arbitrage between BTC spot [truncated]".

**Claim:** A market-making bot on 5m-class crypto Up/Down markets (our universe family) collected $81k over 54 days at 46% WR and ~472 trades/day.

**Relevance to shadow (MEDIUM, hypothesis-generating only):**
1. The shape (sub-50% WR, very high trade count) is a spread/rebate collector, NOT a forecaster - consistent with the standing correction that forecasting has no proven edge and the maker economics family (024/042) is the unproven-but-promising lane. One more external datum for that family.
2. The horizon (5m-class, 472 trades/day) is outside our keyed universe until the pending restart keys 15m; 5m is not keyed at all. Latency edge is a refused family (tick 4: venue dynamic taker fee killed it). Do NOT propose a 5m MM strategy; file as corroboration for maker economics.
3. 46% WR with profitable outcomes also matches the censoring/undecidability thesis (041): a 46% WR book that nets positive must be harvesting structure (spread/rebate/resolution mechanics), not probability.

## Signal 3: "$40M realized arb from pricing structure" + the two dominant error patterns (STRUCTURAL corroboration, MEDIUM-HIGH)

**Sources:**
- WebSearch: StartupHub.ai (2026-08-16): "8 live prediction market arbitrage opportunities, up to 60.9% ROI (Trump-Somaliland recognition market)". StartupHub.ai (2026-07-27): "5 opportunities live on Polymarket, Kalshi and PredictIt".
- WebSearch research summary (April 2024 - April 2025 dataset, 7,000+ markets): "two primary arbitrage patterns: (1) the sum of yes/no share prices in the same market deviates from the theoretical $1; (2) probability divergences in logically related markets (e.g. 'Trump wins' vs 'Republicans win')."

**Claim:** Same-market complete-set (yes+no != $1) and logically-related-market divergence are the two dominant, measurable pricing-structure errors. This is the same family as cycle 6 Signal 4 (~$40M realized) with the mechanism now stated more precisely.

**Relevance to shadow (MEDIUM-HIGH):** Direct external corroboration for the standing structural family: complement no-arb (037, BLOCKED on 036 keying + top-of-book reflection) and corridor_pair (005/036, the live expression). Pattern (1) (same-market yes+no sum) is exactly 037's mechanism; pattern (2) (related-market divergence) is corridor_pair's cross-window cousin. The reasoner may use this as context for a corridor_pair-family measurement or a new cross-market expression of the completeness family - NOT to unblock 037 (Raven's call). Note: cross-venue (Kalshi) legs remain out of universe - the research spans multiple venues, our shadow is Polymarket-only.

## Signal 4: PolyMM open-source sports market-making bot (de-vig + quoting + hedging) - structure reference (LOW-MEDIUM)

**Source:** X @PredMarketWiki (7 likes, 1 rt, 11:32 UTC): "New project added to the prediction markets wiki: PolyMM by @kachoio - an open-source Python bot for Polymarket sports market making. It combines bookmaker de-vigging, automated quoting, and hedging, making it a useful reference for sports-market quant."

**Relevance to shadow (LOW-MEDIUM):** Sports are out of universe (no sportsbook feed). But the structure - de-vig the bookmaker price, quote inside it, hedge residuals - is a cleaner expression of the maker/quoting family than anything we run (maker_rebate_quote_ladder is a plain ladder). Reference only; do NOT propose porting sports de-vigging (universe refusal). Worth one line for the 024/042 context: the profitable quoted-side models in the wild are de-vig-based, not fee-rebate-based.

## Signal 5: Whales dominate Polymarket disputes ($5B adjudicated) - resolution-mechanics stress test (MEDIUM, context for 038 family)

**Sources:**
- Bloomberg (2026-05-26, surfaced via WebSearch): "Crypto Whales Dominate Polymarket Disputes Worth $5 Billion".
- WebSearch: ~2,000 financial contracts disputed/adjudicated over the past year; nine anonymous wallets effectively gain control over contested outcomes.

**Relevance to shadow (MEDIUM):** Dispute outcomes are the tail of resolution mechanics. Our 038 resolution-ledger family measures settlement against the public resolution; disputes are the cases where the public resolution is contested. No row action proposed - context that the resolution surface we measure is itself subject to a governance process we do not model. Keep 038's scope as-is; note in reasoner context only.

## Signal 6: Cross-venue Kalshi/Polymarket arb alerts constant and fat (PERSISTENT, out of universe)

**Sources:**
- X @PB_Signal (multiple, 13:15/08:56/08:36/06:18 UTC): music/entertainment and general markets: "YES Kalshi @ 0.68 / NO Polymarket @ 0.2, spread 12%"; "YES Kalshi @ 0.75 / NO Polymarket @ 0.12, spread 13-14%"; "Big Brother winner, spread 7.9%".
- X @ArbBets20 (04:15 UTC RT): "Buy Yes on Kalshi - 52c. Buy No on Polymarket - 36c."

**Relevance to shadow:** Same disposition as cycles 2-6: persistent, fat, entirely outside the crypto Up/Down universe the discovery pass returns. No Kalshi feed in the shadow stack. Filed for universe expansion, not proposable now.

## Signal 7: Noise / refused families (LOW, file only)

- X @kinexbtdev / @danielwon80 (12:39 UTC): "Polymarket 5-Minute Market Momentum Bots: Inside the High-Frequency Strategy Crushing Crypto Prediction Markets in 2026" - latency/momentum family refused at tick 4; 5m not keyed; file only.
- X @marvin_x1 (13:02 UTC): "TRADER FACESHOT MADE $74,105 ON POLYMARKET - almost all of it in ONE DAY" + @BimbaCrypto (11:37 UTC): "$216 to $52K in 2 weeks, 62 trades, 100% win rate" + @BimbaCrypto (02:14 UTC): "2,783 trades, $2.36M soccer fade machine" - trader-profile claims; the 100% WR shapes match tick 4's fabricated-dashboard debunk methodology. File only.
- X @jetwaniavinash (13:06 UTC): "I found a Polymarket strategy with a 100% win rate and t-stat 3.38. Real backtest. Fees included. Then I ran one more test - and found out it was fake. The autopsy." + "Drop a Polymarket strategy idea - I'll run the honest test, win or die. Dying on paper is free." - methodological corroboration for the no-backtesting rule (a claims-to-be-real backtest debunked on one more test). LOW; note only. The account is a useful external check source for future cycles.
- X @billy__trader (11:15 UTC): "the edge isn't a better prediction. It's being physically closer to the exchange's computer" - latency family, refused. File only.
- X @monolith_fund (10:39 UTC): fee gap table (Hyperliquid HIP-4 ~$0.07 round trip vs AMM ~$0.80 vs predictdotfun ~$4.00) - different venue family, out of scope. File only.
- X @0x_exit (07:45 UTC RT): "$1k to $145k trading League of Legends" - esports, out of universe. File only.
- X @recogard (19:20 UTC Aug 19): "8 free GitHub repos for Polymarket analysis" - tooling list, not a strategy. Note only.
