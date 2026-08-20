# External Signals - 2026-08-20 Forge Cycle 5 (~04:20 UTC)

**Generated:** 2026-08-20 04:20 UTC (cron Forge eval loop, 4h tick)
**Source:** X API (xurl search, last 24h) + web search (Firecrawl/WebSearch bridge)
**Status:** RAW, awaiting Opus reasoning distillation
**Note:** Reddit (r/Polymarket, r/algotrading) crawler-blocked again this cycle
(same as cycles 2-4). X + web only.

---

## Signal 1: Polymarket switched crypto Up/Down settlement to TWAP on Aug 7, 2026 - the single biggest venue-structure change since the shadow stack was built (STRUCTURAL, HIGH)

**Source:** tradoxvps.com/polymarket-twap-settlement/ (full text retrieved 2026-08-20 04:1x UTC); corroborated by medium.com/illumination article summary and cryptonews guide.

**Claim:** Since 2026-08-07 00:00 UTC, crypto up/down markets (5m, 15m, and 4h windows across BTC, ETH, SOL, XRP, DOGE, BNB, ZEC, HYPE) no longer settle on a single snapshot price. They settle on a time-weighted average price (TWAP) computed by Chainlink over a 60-second lookback window. The announcement said 30 seconds for 5-minute contracts, but Polymarket quietly doubled it post-launch ("doubling the window on the one duration that was being exploited"). The OPENING price (start of range) is still a single instant; only the CLOSE is averaged. Hourly markets are excluded entirely (Binance candle settlement, no oracle).

**Impact per the article:**
- Settlement snipers (single-print manipulation at close): ELIMINATED. Stanford researchers documented 821 wallets capturing ~$8.2M via single-print exploitation before the change.
- Latency arbitrage: survives but shifts. Stale-quote windows narrow rather than vanish; speed now applies to "tracking and requoting rather than landing one order at one instant."
- Market makers: clear winners - lower settlement-flip risk enables tighter quoting, plus ~$1M in liquidity rewards through August.

**Relevance to shadow (CRITICAL):** Our crypto up/down strategies were calibrated against single-print settlement. PM_fair_value_arb (the biggest loser family, 205 closes -47.46 this window in trading.db alone) trades these books; PM_temporal_arbitrage (the one family that is consistently non-negative: +12.05 trading.db, +1.55 survivors this window) also lives here. TWAP settlement changes the tail distribution of every hold-to-resolution position and may have changed the very cost structure that killed fair_value_arb. The reasoner should consider: (a) a sharp amendment to any crypto-up/down proposal whose kill conditions or thesis assume single-print resolution; (b) re-examination of temporal_arbitrage's edge now that close-flip risk is spread across 60s; (c) whether the maker-side tailwind (Signal 2) plus TWAP makes maker strategies structurally better funded.

---

## Signal 2: Polymarket internal MM desk hiring + $1M liquidity rewards through August (STRUCTURAL WARNING + tailwind for maker family)

**Source:** cycle-4 Signal 2 (web3.career listing, 2026-08-19) + tradoxvps TWAP article (liquidity rewards $1M through August).

**Claim:** The venue is building its own internal market-making team (may trade against users) while simultaneously paying ~$1M/month-scale liquidity rewards to external makers.

**Relevance to shadow (MEDIUM-HIGH):** For 024 (maker_rebate_corridor_quote_ladder, n=27 -15.10 this window in trading.db) and 042 (maker_fill_markout_probe): TWAP reduces maker settlement-flip risk (good for makers), but the internal desk is a new competitor on the same books. Net effect uncertain; 042's fill-level markout measurement is the right instrument. Do not kill the maker family on either signal alone; file both as risk/tailwind factors for the maker reasoner pass.

---

## Signal 3: The "dump-and-hedge" bot on 15m Up/Down - buy the dumped side, hedge with the opposite once combined cost < threshold (STRUCTURAL - external corroboration of the complement family, 037's commercial cousin)

**Source:** Web search aggregation (TypeScript bot writeup surfaced 2026-08-20; mechanic reported by quantvps/tradingvps articles + X post 2090189727710277856).

**Claim:** A TypeScript bot runs "dump-and-hedge" on Polymarket 15-minute Up/Down markets for BTC/ETH/SOL/XRP: watch for sharp price drops in the opening window, buy the dumped side, then hedge by buying the opposite outcome once the combined cost falls below the profit threshold. This is complete-set/complement arbitrage applied to the 15m crypto family. The same X post describes a wallet making +$208,452 in 2.5 months combining "directional trading with asynchronous complete-set arbitrage and volatility harvesting" on short-term crypto Up/Down.

**Relevance to shadow (HIGH for the STRUCTURAL family):** This is precisely 037's (complement no-arb taker) mechanism, executed on the 15m crypto books by an external bot reportedly making money. 037 is BLOCKED on 036 (condition_id keying, 61.7% pairing ambiguity). This is external corroboration that the complement/completeness edge exists venue-wide - and that the block on 037 is worth clearing. Do NOT unblock 037 (validator floor dispute is Raven's call), but the reasoner should note the external multiplicity and possibly propose a probe that sidesteps the keying blocker (e.g. corridor_pair-style pair matching already implemented in 005/036 family? verify).

---

## Signal 4: Binary complement arbitrage (< $1.00 combined) and multi-outcome bundle arbitrage (< $1.00 total) still listed as live 2026 strategies (STRUCTURAL - same family as 037)

**Source:** tradingvps.io/best-polymarket-trading-strategies-in-2026/ (full text retrieved 2026-08-20).

**Claim:** Standard strategy-guide listing: buy both YES and NO when combined < $1.00 for guaranteed $1.00 payout; buy all outcomes when total < $1.00. Also listed: settlement-edge trading (crowd misinterprets resolution rules - corroborates 038 settlement resolution ledger family), term-structure spread trading (same event, different expirations - related to 005/039), "No" bias exploitation (retail overpays exciting YES outcomes; buy NO), whale copy trading (reported 90% WR esports wallet - unverifiable, cycle-4 Signal 7 category).

**Relevance to shadow (MEDIUM):** No new mechanism beyond what 037/038 already encode, but the persistence of complement-arb and settlement-edge on the 2026 lists is external multiplicity for the STRUCTURAL family. The "No bias" item is a forecasting-flavored behavioral claim - the reasoner should treat it as hypothesis only, given the standing correction.

---

## Signal 5: Latency-arb time constant reconfirmed - 2.7s arb window, 73% captured by sub-100ms bots (STRUCTURAL - unchanged from cycle 4, do not re-propose taker latency arb)

**Source:** Web search (medium/illumination summary, cryptonews, finance.yahoo arb-bot piece - all 2026-08-2x retrieval).

**Claim:** Average arbitrage opportunity duration ~2.7 seconds (down from 12.3s in 2024), median spread ~0.3%, 73% of arb profits to sub-100ms bots, ~70% of traders lose money, bot-like bettors extracted ~$40M in arb profits across 86M bets (April 2024-April 2025, academic research). Polymarket crossed $35B Q1 2026 volume.

**Relevance:** Reconfirms the fair-value-family kill from outside the stack. No new proposal should be a taker latency-arbitrage play. 15m crypto now carries a dynamic taker fee specifically designed to neutralize latency arb (per web search) - another structural fact for the fair-value/settlement-exit family review.

---

## Signal 6 (context): Account-level profit claims continue to circulate (noise category)

**Source:** X posts 2090273522262364587 (Claude-built quant bot +$81,323/54 days), 2090271152493265211 (Kalshi arb alerts), 2090236390638596553 (RT Tomatometer arb alerts), 2090189727710277856 (HFT +$208,452/2.5 months), 2090226207530881475 (Shenzhen $900 phone trader).

**Claim:** Multiple unverifiable high-profit account claims, several with follow-bait formatting.

**Relevance:** Same category as cycle-4 Signal 7 - unverifiable. One useful fragment: the RT Tomatometer / CPI / hurricane arb-alert bots (@Predictbook-style) demonstrate cross-venue arb alerts on long-horizon general markets - out of universe (no Kalshi feed). File only.

---

## What changed vs cycle 4 (for the reasoner)

1. **NEW AND CRITICAL: TWAP settlement (Aug 7) for crypto up/down close prices** - changes settlement tail distribution for every crypto up/down strategy; settlement snipers eliminated venue-wide; makers structurally better off.
2. NEW: dump-and-hedge / complete-set arb on 15m crypto confirmed as an external bot's live mechanic (+$208K claim) - external corroboration of 037's complement family; the 037/036 keying blocker now has real opportunity cost.
3. NEW: $1M liquidity rewards to makers through August + internal MM desk (cycle-4 carry).
4. UNCHANGED: Reddit blocked; arb window 2.7s; fee regime; account claims = noise.
