# Asset Class Coverage: What Has Actually Been Tested

**Date:** 2026-08-13
**Question (Aym):** what equity classes have we tested? options, futures,
crypto pairs, normal tickers? and how do fees affect each?

## Measured coverage (from the 218,295-entry run)

**180 distinct tickers, 28 sectors, 4 timeframes.**

| Class | Status | Detail |
|---|---|---|
| Equities (single names) | TESTED | 20+ sectors: mega-cap tech, semis, biotech, energy, financials, healthcare, industrials, utilities, materials, real estate, staples, discretionary, telecom, EV/auto, internet, hot/speculative |
| Sector + index ETFs | TESTED | XL* sector family, SPY/QQQ/IWM/DIA |
| Leveraged ETFs | TESTED | 12,285 entries (largest single bucket after Unknown) |
| Bond / commodity / volatility ETFs | TESTED | incl. UVXY, WEAT, GLD-type |
| Futures | TESTED | 8 contracts: CL, ES, GC, NG, NQ, RTY, YM, ZB |
| Crypto | TESTED | BTC/ETH/SOL on Binance pairs (15m, 1h) and Yahoo USD daily |
| **Options** | **NOT TESTED (now fixed)** | `synthetic_options.py` existed with ZERO callers since the project began |
| **Weekly timeframe** | **NOT TESTED (silent skip)** | see below |

Timeframes actually exercised: 5m, 15m, 1h, 1d. NOT 4h, NOT 1wk.

### The weekly gap: a silent skip, not a decision

176 `_1wk.csv` files exist and `1wk` is in the runner's timeframe list, but
weekly has NEVER produced a single graveyard entry. Cause: weekly files hold
~262 bars (5 years). The runner takes the last 20% as the test slice = 53
bars, and skips any series with fewer than 100 test bars. Every weekly file
fails that check silently.

Fix required: download 10-15 years of weekly history (or split weekly
differently), and make the runner emit NOT_TESTED with a reason instead of
`continue`. The SPEC explicitly lists weekly as in scope.

## Options: now tested, and the fee structure is the whole story

`backtest/options_overlay.py` replays any strategy's bullish signals as long
call purchases (OTM %, days-to-expiry, Black-Scholes on trailing realized vol
annualized BY TIMEFRAME, per-contract commissions, option spread).

### Your fee assumption, checked

You said options "only pay fees per trade not per contract." That is **not
how most US brokers price options**, and the difference matters enormously:

| Broker | Options commission |
|---|---|
| Schwab / Fidelity / E*TRADE | $0 base + **$0.65 per contract** |
| Tastytrade | **$1 per contract** to open, $0 to close, $10/leg cap |
| IBKR | **$0.15-$0.65 per contract**, typically $1 order minimum |
| Robinhood | $0 commission, but per-contract regulatory fees (~$0.03) |

So commissions scale with CONTRACTS. But your underlying instinct is the
important one, and it is right: **the per-contract fee is a FIXED DOLLAR
AMOUNT, not a percentage of premium.** That is the fixed-cost case from
yesterday's position-size note, and here it is real rather than hypothetical.

### Measured: fee drag swings 24x on strike selection alone

AAPL 1d, grid_1.0atr signals, $100 premium budget per trade, $0.65/contract:

| Strike | DTE | Trades | Premium/contract | Contracts | **Commission as % of premium** |
|---|---|---|---|---|---|
| 5% OTM | 30 | 2 | $81 | 1.0 | **1.61%** |
| 10% OTM | 30 | 18 | $58 | 2.7 | **4.29%** |
| 20% OTM | 30 | 23 | $9 | 28.4 | **39.36%** |

Cheap options are punished TWICE by fixed per-contract fees: the fee is a
larger share of each contract's premium, AND a fixed dollar budget buys many
more contracts, multiplying the number of fees paid. At 20% OTM the
commission ate 39% of every dollar of premium.

**Direct consequence for the SPEC's v2+ ideas:** the WSB-style "buy cheap OTM
calls" and Taleb-style "buy far OTM puts for crash protection" strategies are
the WORST case for this cost structure. They are exactly the trades where
fixed per-contract fees do maximum damage. Those ideas need a fee model
before they need a backtest.

### A hard constraint nobody had noticed

**The SPEC's $100 fixed notional cannot buy a single option contract on most
liquid names.** One 30-day 5% OTM call on a $200 stock costs $200-500 in
premium. At a $100 budget the overlay buys zero contracts and skips every
signal (pinned by a test). Options need their own sizing rule; the equity
notional cap does not transfer.

## Honesty warning on the option PnL numbers

The overlay produced some attractive-looking results (PF 2.4-2.8 on some
configs). **Do not treat these as evidence.** The model is optimistic by
construction:

- **No IV smile.** OTM options really trade above the realized-vol price, so
  entry premiums here are too cheap. This bias flatters every long-option
  result and gets worse the further OTM you go.
- Option bid/ask modelled as a flat 3%; real retail spreads are often wider.
- No early assignment, dividends, or pin risk.

The overlay is a FEE-STRUCTURE and directional-sanity model. Real chain data
(CBOE / ORATS / Polygon) remains the SPEC's V4 answer for anything tradeable.

## What to do next

1. Re-download weekly with 10-15y history, and make skips visible as
   NOT_TESTED rather than silent `continue`s.
2. Add the options overlay to the graveyard sweep for a subset of liquid
   optionable names, with commission as a swept parameter ($0, $0.65, $1.00)
   so the cost sensitivity is in the data, not in a note.
3. Before any options strategy is taken seriously, get one real chain
   snapshot and compare its implied vols against this model's assumptions.
   If the model is 30% cheap on premium, every long-option result here is
   fiction.
