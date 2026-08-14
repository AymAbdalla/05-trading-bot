# US Broker Fee Reference — Crypto, Equities, Options, Futures
**Purpose:** parameterize the backtest harness to current, venue-accurate cost conditions
**Verified:** 2026-08-13 via public fee schedules and current comparisons. Fee schedules change without notice — the Verification Checklist at the bottom lists what to confirm against live accounts before hardcoding anything.

---

## The four cost regimes (read this before the tables)

The four asset classes are not four fee levels — they are four different cost STRUCTURES, and each demands a different harness model:

| Regime | Asset class | Cost scales with | Harness model |
|---|---|---|---|
| **Percentage** | Crypto | Notional (% per side) | % fee per leg, maker/taker flag |
| **Spread** | US equities | Spread width + tiny sell-side reg fees | half-spread per leg + reg on sells |
| **Fixed per-contract** | Options | Contract count, NOT notional | $/contract per leg + pass-throughs |
| **Stacked fixed** | Futures | Contract count (broker + exchange + reg) | $/contract/side, exchange-fee dominated |

Consequences: percentage costs are size-invariant (the graveyard's verified finding); fixed costs shrink as position size grows (its §5.6 caveat); spread costs depend on instrument liquidity, not broker choice; futures costs are nearly invisible as a % of notional but come with leverage.

---

## 1. CRYPTO — percentage regime

### Spot maker/taker, base tier (per side, % of notional)

| Venue | Maker | Taker | Notes |
|---|---|---|---|
| **Binance.US** (current v1 venue) | **0.00%** | **0.02%** | All pairs, all users, no tiers (since 2026-04-22). Core pairs BTC/USD, ETH/USD: 0%/0.01%. BNB fee payment: extra 5% off. Excludes One-Click Buy/Sell. |
| OKX (US) | 0.08% | 0.10% | Lowest of the non-Binance majors |
| Alpaca Crypto | 0.15% | 0.25% | Volume-tiered; relevant because the equity stack already lives here |
| Gemini ActiveTrader | 0.20% | 0.40% | Consumer Gemini interface is ~1.49%+ — never route there |
| Kraken Pro | 0.25% | 0.40% | Drops to 0.20/0.35 at $10–50k monthly volume; 0.10/0.20 at $250k. Kraken consumer interface: 1% + spread |
| Coinbase Advanced | 0.40–0.60% | 0.60–1.20% | Sources conflict on the 2026 base tier and Coinbase hides the schedule behind login — treat as the most expensive major and verify in-app if ever needed |
| Robinhood Crypto | $0 commission | $0 commission | Cost is embedded in the spread, not itemized — must be measured empirically, cannot be assumed small |

### Round-trip cost on the harness's $100 clip

| Execution path | Fee RT | Slippage RT (modeled) | Total | vs. old harness $0.30 |
|---|---|---|---|---|
| Binance.US taker/taker, core pair | $0.02 | $0.10 | **$0.12** | −60% |
| Binance.US taker/taker, other pairs | $0.04 | $0.10 | **$0.14** | −53% |
| Binance.US maker/maker | $0.00 | ~$0 (adverse selection instead) | **~$0** | −~100% on explicit cost |
| Kraken Pro taker/taker | $0.80 | $0.10 | $0.90 | +200% |
| Coinbase Advanced taker/taker | $1.20–2.40 | $0.10 | $1.30–2.50 | +330–730% |

The venue choice is worth more than any signal in the graveyard. Slippage (0.05%/side) is a modeling assumption, not a venue fee — it stays regardless of venue for taker legs and is replaced by adverse-selection measurement for maker legs.

---

## 2. US EQUITIES — spread regime

### Commissions (online US-listed stocks/ETFs)

| Broker | Commission | Notes |
|---|---|---|
| Alpaca (current v2 venue) | $0 | Retail self-directed API accounts; regulatory fees still apply; Elite Smart Router may differ |
| Robinhood, Webull, Schwab, Fidelity, E*TRADE | $0 | Industry standard |
| IBKR Lite | $0 | |
| IBKR Pro | Tiered per-share (fractions of a cent/share, minimums apply) | Only relevant if execution quality is being bought — verify schedule if ever considered |

### Regulatory fees — SELL side only, set by law, identical at every broker

| Fee | Current rate | Effective | Notes |
|---|---|---|---|
| SEC Section 31 | **$20.60 per $1,000,000** of sale proceeds | 2026-04-04 | **Was $0.00 from May 2025 through 2026-04-03** — a backtest spanning that window has a time-varying reg fee. Rounded up to the nearest penny. |
| FINRA TAF (equities) | **$0.000195/share**, min $0.01, cap $9.79/trade | 2026-01-01 | Was $0.000166/share and $8.30 cap before 2026. Sells only. |
| CAT fee | $0 | since 2025-12-01 | Regulators stopped charging CAT on equity/options orders |

Broker waivers exist (e.g., Robinhood absorbs SEC fee on sales ≤ $500 notional and TAF on ≤ 50 shares) — the harness should model the statutory fee and treat waivers as venue-specific bonuses.

### What a $100 equity clip actually costs
SEC fee on the sell: $100/1M × $20.60 = $0.002 → rounds to **$0.01**. TAF on a handful of shares: **$0.01** (the minimum). Spread on liquid large caps: ~1–5bps ($0.01–0.05 round trip); small caps and low-ADV names: 10–50bps+.

**All-in round trip on liquid large caps: ~$0.03–0.08 on $100 (≈3–8bps)** — the cheapest percentage-cost asset class available. The harness's crypto-derived $0.30 assumption, if ever applied to the equity lane, would overstate costs on liquid names by 4–10×. Spread modeling (per-instrument, from the fingerprinting work) is the entire game here; fees are a rounding error.

---

## 3. OPTIONS — fixed per-contract regime

### Broker commissions (US equity options, per contract, per side)

| Broker | Open | Close | Cap | Notes |
|---|---|---|---|---|
| Robinhood | $0 | $0 | — | Index options may carry fees |
| Webull | $0 | $0 | — | Index options: $0.30–0.50/contract |
| Firstrade / Public | $0 | $0 | — | |
| tastytrade | $1.00 | $0 | $10/leg | Cheapest mainstream for multi-lot sellers |
| Schwab / Fidelity / E*TRADE | $0.65 | $0.65 | — | Fidelity waives buy-to-close on premiums ≤ $0.65 |
| IBKR | $0.65 (premium ≥ $0.10); $0.50 ($0.05–0.10); $0.25 (< $0.05) | same | — | Volume tiers below; strongest for 100+ contracts/month |

### Regulatory & pass-through fees (all brokers, per contract)

| Fee | Rate | Side |
|---|---|---|
| FINRA TAF (options) | $0.00329/contract | sells, effective 2026-01-01 (was $0.00279) |
| SEC Section 31 | $20.60 per $1M of sale premium | sells, from 2026-04-04 |
| ORF + OCC clearing + exchange fees | typically a few cents/contract combined | both — appears on trade confirms; verify per broker, brokers bundle differently |

### The size-inversion table (why this regime is different)
Round trip at $0.65×2 = $1.30/contract (Schwab-class), or ~$0 (Robinhood/Webull) + pass-throughs:

| Premium per contract | $1.30 RT as % of premium | ~$0.10 RT (zero-commission + pass-throughs) |
|---|---|---|
| $100 (cheap short-dated) | 130 bps | ~10 bps |
| $500 | 26 bps | ~2 bps |
| $1,500 (DEEP RENT LEAPS) | 8.7 bps | ~0.7 bps |
| $8,000 (SPY LEAPS) | 1.6 bps | ~0.1 bps |

Exactly the graveyard §5.6 inversion: in fixed-cost regimes, bigger positions are CHEAPER in bps. But commissions are the small half of options costs — **the bid-ask spread dominates** (2–10%+ of premium on illiquid single-name LEAPS, pennies on SPY weeklies). The Lab v4 rule stands: spread-to-edge is the real gate; model fills at 25–50% of quoted width worse than mid.

---

## 4. FUTURES — stacked fixed regime (exchange-fee dominated)

Per contract, per side, three stacked layers: broker commission + exchange fee + regulatory (NFA ≈ $0.01–0.02).

### Broker commissions (per contract, per side)

| Broker | Micro contracts | Standard/E-mini | Notes |
|---|---|---|---|
| NinjaTrader (free plan) | $0.39 | $1.29 | Lifetime license ($1,499 one-time): micros $0.09; monthly plans in between; $25/mo inactivity fee |
| Plus500 Futures | $0.49 | $0.89 | + $0.02 reg/side; no platform/data fees |
| IBKR | $0.85 (tiered to $0.25 at volume) | $0.85 (tiered) | Micros count 1/10 toward volume tiers |
| tastytrade | ~$1.25 | ~$1.25 | Verify current schedule |
| Schwab | $2.25 | $2.25 | Verify current schedule |

### Exchange fees (CME, non-member, per side — the dominant layer)

| Contract | Exchange fee | Example all-in per side |
|---|---|---|
| ES (E-mini S&P) | $1.38 | IBKR: $0.85 + $1.38 + $0.01 = **$2.24** |
| MES (Micro E-mini S&P) | ~$0.60–0.65 | All-in round trips: NinjaTrader ~$2.07, Plus500 ~$2.27, IBKR ~$2.99 |

### The percentage-of-notional view (the sleeper finding)

| Contract | Approx. notional | All-in RT | RT as % of notional |
|---|---|---|---|
| MES | ~$34,000 | ~$2.10–3.00 | **0.6–0.9 bps** |
| ES | ~$340,000 | ~$4.50 | **~0.13 bps** |

Futures are the lowest-cost-per-exposure venue in the entire landscape by roughly an order of magnitude — sub-basis-point round trips. The Toll Law adores this terrain: at these costs, even small true edges are net-positive. The offsets: minimum position size is one contract (MES ≈ $34k notional exposure ≈ $2–3k margin), leverage changes the risk model entirely, and data/platform fees ($4–40/month per exchange for real-time data) are a fixed overhead that matters at small account sizes. This is a v3-lane fact, not a v1 action item — but when the futures lane opens, its cost physics are the friendliest any signal will ever fight on.

---

## 5. Harness parameterization summary

| Lane | Model | Parameters (current) |
|---|---|---|
| Crypto (Binance.US) | % per leg, maker/taker flag | taker 0.02% (0.01% core pairs), maker 0.00%; slippage 0.05%/side taker legs; maker legs: trade-through fill rule, adverse selection measured not assumed |
| Equities (Alpaca) | half-spread per leg + reg on sells | commission $0; SEC $20.60/$1M on sells (time-varying: $0 before 2026-04-04 within backtest windows); TAF $0.000195/sh min $0.01; per-instrument spread table from fingerprinting |
| Options | $/contract per leg + pass-throughs + spread fill model | $0–1.30 RT commission by broker choice; pass-throughs ~$0.05–0.10 RT; fills at mid ± 25–50% of quoted width |
| Futures | $/contract/side stacked | broker $0.39–2.25 + exchange ($1.38 ES / ~$0.62 MES) + reg $0.01–0.02; data fees as fixed monthly overhead |

Cost-model version stamps on every run remain non-negotiable — this document is the source for version "2026-08-13".

## 6. Verification checklist (before hardcoding)
1. Binance.US: confirm 0%/0.02% (and core-pair 0.01%) on the live account's fee page — announced 2026-04-22, "subject to change"
2. Alpaca: confirm $0 equity commission applies to the account type in use (retail API, not partner-routed) and current crypto tier
3. Coinbase Advanced base tier if ever needed — schedule requires login and public sources conflict (0.40/0.60 vs 0.60/1.20)
4. tastytrade and Schwab futures rates — included above from secondary sources, not primary schedules
5. Options pass-throughs (ORF/OCC/exchange) — read off an actual trade confirmation once live
6. SEC fee rate — resets with each fiscal-year appropriation (was $0 for eleven months, now $20.60/$1M); check the SEC Fee Rate Advisory page annually
7. FINRA TAF — adjusted 2026-01-01; historically revised every 1–3 years
