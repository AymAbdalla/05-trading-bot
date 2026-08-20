# External Signals - 2026-08-20 Forge Cycle 6 (~08:45 UTC)

**Generated:** 2026-08-20 08:45 UTC (cron Forge eval loop, 4h tick)
**Source:** X API (xurl search, last 24h) + web search (Firecrawl/WebSearch bridge)
**Status:** RAW, awaiting Opus reasoning distillation
**Note:** Reddit (r/Polymarket, r/algotrading) crawler-blocked again this cycle
(same as cycles 2-5). X + web only.

---

## Signal 1: Fee-surface is MOVING - taker rebate program (up to 50% refund), maker rebates (up to 25% back), and an 80% "special user" rebate scandal breaking TODAY (STRUCTURAL, HIGH)

**Sources:**
- kucoin.com/news/flash/polymarket-to-launch-taker-fee-rebate-program-with-up-to-50-refund
- polymarkets.co.il/en/guide/polymarket-maker-rebates/ (maker rebates up to 25%, by category)
- X @ohiobug981 (12+ retweets, breaking ~07:30 UTC): "Bonereaper has been getting a 80% fee rebate on polymarket ever since the 7th, as well as other accounts..." followed by "heres what happened today after poly rebate scandal... Suhail came and said..." (Suhail = Polymarket founder; he publicly addressed it)
- X @predict_anon (8 likes, 2 rts): "Polymarket gave special users an 80% rebate (which means zero-fee), and they will rolling out VPC to bypass Cloudflare - which gives them a 10ms speed edge over normal users."

**Claim:** On top of the dynamic taker fee (the Aug 7 change 040 was built on), the venue now runs a taker fee REBATE program (up to 50% refund) and maker rebates (up to 25% of matched taker fees). Separately, a select-account 80% rebate (effectively zero fee) is being reported as a scandal today, with founder response. An alleged VPC rollout would give select users ~10ms of Cloudflare-bypass latency edge.

**Relevance to shadow (HIGH):**
1. 040 (dynamic taker fee regrade) was built on the fee surface as of the Aug 7 change and concluded "fees deepen losses, they do not cause them" with a flat-vs-peaked disagreement of 67 USD. A REBATE program changes the effective taker fee DOWN (up to 50% back) and maker economics UP (25% back). The 042 kill ceiling (-0.0315/share) is derived FROM the taker fee; if the real effective taker fee falls, that ceiling is conservative, not violated - but the maker markout break-even moves. Do NOT re-derive 040's headline from memory; the reasoner should decide whether the rebate program warrants an amendment or just a recorded scenario note (same posture as the TWAP unverified claims: press-reported, not verifiable from inside the system; 040's default stays 0.0).
2. The 80% special-user claim, if true, means the fee surface is NOT uniform across participants. Our paper adapter charges flat 0.0. That asymmetry is not actionable for us (we are not a special account) but it changes the market's maker economics.
3. The VPC/10ms claim is latency-arb material, which is a refused family (tick 4: venue killed latency arb with the dynamic fee; no latency edge). File only.

## Signal 2: CLOB/API outage 2026-08-19 ~08:50-10:49 UTC (~2h), cancel-only mode during recovery (RELIABILITY EVENT, MEDIUM)

**Source:** X @digest account, "POLYMARKET DIGEST - 20 AUGUST 2026": "Major Outage - Aug 19: CLOB/API went down ~08:50 UTC, restored ~10:49 UTC (approx 2h). Root cause: 3rd-party infrastructure provider. Cancel-only mode was briefly active during recovery."

**Relevance to shadow (MEDIUM):** The shadow loops are paper and read the same venue API. Any fills/orders/market_tape rows timestamped inside 08:50-10:49 UTC on Aug 19 may be partial, missing, or based on a degraded book. The 038 resolution ledger and the market_resolutions rows for windows overlapping that window deserve a data-quality look (were resolutions still published during the outage?). Do NOT let this drive a strategy proposal; it is a data-integrity note for the measurement family (038/043/046/047).

## Signal 3: External weather bot trader reports GREEN on prediction markets, questioning platform choice (CORROBORATION for weather family, MEDIUM)

**Source:** X @theparuchh (3 likes, ~07:53 UTC): "MY WEATHER BOT IS GREEN ON PREDICTION MARKETS - BUT I THINK I'VE BEEN TRADING IT ON THE WRONG PLATFORM... I've been running a weather bot - 21 cities, ensemble models, 24/7. It works."

**Relevance to shadow (MEDIUM):** Corroborates that weather forecasting CAN be profitable on prediction markets, but 044 already measured the binding constraint on OUR weather family: not information, not the feed, but instrument resolution (rung_narrower_than_model_resolution fires 65.2% of the time; MIN_ATTAINABLE_P_YES=0.5 refusal). 033 (the fix for exactly that) has NEVER fired (7,608 signals, 0 acted). An external trader being green does not change our measurement, but it is the first independent "weather works" claim this cycle; worth one line in the reasoner context, not a proposal driver.

## Signal 4: "$40M realized arbitrage profit from systematic exploitation of pricing structure rather than lucky predictions" (STRUCTURAL family corroboration, MEDIUM)

**Sources:** laikalabs.ai/prediction-markets/polymarket-trading-strategies; newyorkcityservers.com/blog/prediction-market-arbitrage-guide; tradoxvps.com/how-to-win-on-polymarket-in-2026-the-strategic-edge-guide/; tradetheoutcome.com/polymarket-strategy-2026/

**Claim:** A research estimate puts ~$40M of realized arbitrage profit on prediction markets, derived from systematic exploitation of PRICING STRUCTURE (complete-set/basket/combinatorial) rather than forecast skill. Same-market basket arb is repeatedly called out as the profitable form; the standard advice "buy both sides under $1.00" is noted as needing >2.5-3% edge after the ~2% winning-position fee, and that profitable players are "sub-second bots capturing wider dislocations."

**Relevance to shadow (MEDIUM-HIGH):** This is external corroboration of the standing correction: STRUCTURE (complement no-arb 037, corridor_pair 005/036, resolution mechanics 038) is the unproven-but-promising family, and forecasting has no proven edge. 037 remains BLOCKED on 036 keying and the top-of-book reflection finding; corridor_pair is the live expression and is accumulating (+2.70 on n=2 this window; PROVISIONAL per vault). The reasoner may use this as context for a corridor_pair-family measurement, NOT to unblock 037 (that is Raven's call).

## Signal 5: Cross-venue arb alerts (Polymarket x Kalshi) are constant and fat (PERSISTENT, out of current universe)

**Sources:** X @predictxglobal (08:40 UTC): Polymarket 34c BUY vs Kalshi 61c SELL on "Will Dplus KIA win" - 24.83c edge; X @PB_Signal (08:36 UTC): Kalshi 0.75 vs Polymarket 0.12 on a Rotten Tomatoes score market - 13% spread; X @youpredictit: MagicMarkets 64c vs Polymarket 65c.

**Relevance to shadow:** Same disposition as cycles 2, 4, 5: persistent, fat, entirely outside the crypto Up/Down universe the discovery pass returns. No Kalshi/MagicMarkets feed in the shadow stack. Filed for universe expansion, not proposable now. Note the Kalshi-side ones are mostly SPORTS/ENTERTAINMENT markets, which also matches the no-sportsbook-feed refusal.

## Signal 6: Regulated US exchange sports parlays live (API-only beta, $7.4M volume in 2 weeks) + $1k->$145k LoL "Grok bot" + $313->$414k 15m up/down bot claims (NOISE / out of universe, LOW)

**Sources:** X @digest (00:01 UTC): Polymarket regulated US exchange launched sports parlay contracts Aug 5, API-only beta, up to 10 legs, $7.4M volume in two weeks without appearing in the consumer app. X @0x_exit: "$1k to $145k trading live League of Legends on Polymarket with what looks like a Grok Bot built strategy." Web: "bot reportedly turned $313 into $414k in a single month on 15m BTC/ETH/SOL up/down, 98% win rate, exploiting a window where Polymarket prices lag confirmed spot momentum."

**Relevance to shadow:** Sports parlays and LoL are out of the current universe (no sportsbook/esports feed; same refusal as tick 5). The $313->$414k 15m claim is the latency-arb family that tick 4 refused (venue introduced the dynamic taker fee specifically to kill it; we have no latency edge) and 15m is not keyed until the pending restart - so it is not evaluable regardless. The "98% WR" figure is the shape of a fabricated dashboard per tick 4's Signal 6 debunk. File only.

## Signal 7: Low-latency execution repeatedly cited as "the real edge" on prediction markets (PERSISTENT, refused family)

**Sources:** X @gabagool22 (08:42 UTC, reply): "for those with systematic, low-latency strategies, especially on platforms with active order books like polymarket, consistent profit comes from execution speed. that's the real edge." X @predict_anon: VPC/10ms claim (Signal 1). Web guides: "profitable players are sub-second bots."

**Relevance to shadow:** Same family as tick 4's refusal (pm_latency_arb_family: venue killed it with the dynamic fee, no latency edge, one cycle profit claim already debunked as fabricated). Persistent signal, same disposition: hypothesis-generating only. NOT a proposal.
