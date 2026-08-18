---
name: "pm_btc_eth_beta_residual"
thesis: "The ETH 5m Up/Down book prices ETH's own displacement from its window open and appears not to price the contemporaneous BTC displacement, so when BTC has moved hard inside a window and ETH has not moved its beta-implied share, the ETH contract is priced off an incomplete information set and should drift toward the beta-implied level before the window settles."
expected_edge_bps: 330
kill_condition: "Any one of: measured catch-up fraction kappa below 0.10 over 200 or more scored entries, where kappa is the realised share of the beta-implied residual that closes between entry and window settlement; net resolution PnL per share below 0.5c (about 100bps of a 51c entry) over 200 or more scored entries; fewer than 200 qualifying entries in 90 days of paired BTC/ETH 5m history; or a measured ETH strike proxy error at or above 5.0 bps at the 50th percentile, which would put the proxy error above the entry signal itself. All four scored by backtest/polymarket_harness.py, with the strike clause scored by an ETH-capable extension of backtest/measure_strike_proxy.py that does not exist yet."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Observe the BTC and ETH 5m Up/Down markets for the SAME window ts (verified: btc-updown-5m-1787045700 and eth-updown-5m-1787045700 both existed at 2026-08-18 09:39Z, identical boundaries). Compute r_btc = log(spot_btc / strike_btc) and r_eth = log(spot_eth / strike_eth), both against the 60s TWAP proxy strike, not the raw spot. RESIDUAL = r_eth - beta * r_btc, where beta is a ROLLING measured regression coefficient over a trailing window, never a constant. ENTER long the lagging side of ETH when all of: abs(r_btc) >= 1.5 * sigma_btc_window; RESIDUAL is opposite in sign to r_btc and abs(RESIDUAL) >= 1.5 * sigma_residual; seconds_remaining in [90, 210]; ETH ask at or below 0.55; ETH ask depth at least 3x intended size; BOTH strike proxies outside their measured noise floors. ONE LEG ONLY on the ETH market. NO BTC hedge leg, refused deliberately, see the body. STOP: none, a losing binary settles at 0.00 and the premium is the floor (convention 8). TARGET: resolution at 1.00. TIME EXIT: none, the window resolves itself; no new entry under 90s remaining."
data_requirements: "Simultaneous CLOB books for btc-updown-5m-{ts} and eth-updown-5m-{ts} (four token ids per observation). Binance.US ETHUSDT /ticker/price and /klines (verified available, same endpoints as BTCUSDT). An ETH 60s TWAP proxy strike. BLOCKER: engine/polymarket/strike.py accepts a symbol argument but STRIKE_PROXY_NOISE_FLOOR_BPS = 5.0 is a BTC-only number measured over 199 windows; the ETH proxy error has NEVER been measured and backtest/measure_strike_proxy.py cannot measure it because its market lookup is hardcoded to get_btc_updown_5m_checked. BLOCKER: we have NO paired multi-asset history at all. beta and sigma_residual are the entry rule, and both are unmeasured over any horizon that matters. BLOCKER: kappa, the fraction of the residual that closes inside the remaining window, is unmeasured and the edge is linear in it."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard; every Polymarket strategy is NOT_TESTED per D-268, which is not evidence for or against anything (convention 11). Nearest neighbours: PM_fair_value_arb and its four variants trade a single BTC 5m market against engine/polymarket/fair_value.py, and this proposal is the same machinery pointed at a second asset with one extra input. It must NOT be pooled with them, because the fair value here depends on a cross-asset regression the single-asset strategies do not use, and a pooled PASS would credit the regression for edge the diffusion model produced on its own. corridor_collector and PM_corridor_pair are structural pair trades on nested BTC windows and share nothing with this except the venue."
kind: edge_hypothesis
status: PROPOSED
source: "forge, cross-market crypto sweep 2026-08-18"
forge_warnings: "no measured lead time; kappa unmeasured; ETH strike proxy error unmeasured"
---


## The central risk, stated first because it is the whole argument

BTC and ETH move together. A strategy that buys one because the other moved is
not an arbitrage, it is a directional bet on a shared factor, and the standard
way that bet disguises itself is by adding a hedge leg so it looks market
neutral. This proposal does not do that, and the refusal is deliberate. See
"Why there is no BTC hedge leg" below.

The honest claim is narrow. It is NOT that the ETH 5m contract is independent of
the BTC move. It is that BTC's displacement is INFORMATION about ETH's next 150
seconds, and that the ETH book has not fully absorbed it. That claim is
falsifiable, it is currently unmeasured, and the kill condition is written to
measure exactly it.

## What I measured, rather than assumed

The brief handed me "BTC/ETH/SOL are 0.8 to 0.9 correlated". On the data I can
actually reach, that is wrong. Measured from Binance.US klines on 2026-08-18,
read only, no trading:

**1000 aligned 1m bars, span about 16.7 hours:**

| pair | 1m log-return correlation |
|---|---|
| BTC, ETH | 0.353 |
| BTC, SOL | 0.254 |

**1000 aligned 5m bars, span 83.2 hours, open to close:**

| quantity | BTC | ETH | SOL |
|---|---|---|---|
| 5m sigma | 4.4 bps | 5.6 bps | 4.2 bps |
| mean absolute 5m move | 2.2 bps | 2.6 bps | 1.6 bps |

| relation | value |
|---|---|
| corr(BTC, ETH) at 5m | 0.613 |
| R-squared | 0.376 |
| beta(ETH given BTC) | 0.787 |
| ETH residual sigma | 4.5 bps, which is 79% of ETH's total 5m sigma |

So BTC explains 38% of ETH's 5m variance on this sample, not 80%. That number
cuts both ways and I am not going to pretend it only helps. It weakens the
"leveraged bet on one factor" objection, because most of ETH's 5m variance is
its own. It also weakens the strategy, because if only 38% of ETH's motion is
BTC-driven then "ETH has not caught up" is usually not a catch-up situation at
all, it is ETH doing something unrelated. Both readings are in the data and both
belong in the record.

Convention 15: this is one 83 hour sample from one venue, measured against
exchange bars and not against the Chainlink TWAP the market actually settles on.
It is a measurement, not a constant, and it has an expiry date the moment the
regime changes.

## BLOCKER: the lead time is not measured, and at the only resolution we have it is not detectable

A lead-lag claim needs a measured lead. I ran the scan. Cross correlation of
1m BTC returns against 1m ETH returns at various lags, same 1000 bar sample:

| lag (minutes) | corr(BTC_t, ETH_t+lag) |
|---|---|
| -3 | +0.055 |
| -2 | +0.106 |
| -1 | +0.088 |
| **0** | **+0.353** |
| +1 | +0.157 |
| +2 | +0.068 |
| +3 | -0.000 |

Contemporaneous dominates. The lag +1 value of 0.157 is the only candidate for a
lead, and at 1 minute resolution it is not separable from bar boundary bleed,
because a BTC move landing at second 55 of a bar shows up partly in the next
ETH bar for purely mechanical reasons.

**This is a BLOCKER.** We cannot measure a lead at 1 minute resolution, and the
strategy needs a lead measured at seconds resolution over the same TWAP series
the market settles on. Nothing in this repository currently produces that series
for ETH. Until it does, the entry rule is a hypothesis about a quantity nobody
has observed.

## Edge arithmetic

Shown in full, because convention 5 wants the arithmetic and not the conclusion.

At the entry threshold, at 150 seconds remaining:

    BTC displacement at entry     = 1.5 * 4.4 bps        = 6.60 bps
    beta-implied ETH displacement = 0.787 * 6.60 bps     = 5.19 bps
    observed ETH displacement (worst case for the market) = 0.00 bps
    unrealised residual                                  = 5.19 bps

    sigma_remaining(ETH) at tau=150s = 5.6 * sqrt(150/300) = 3.96 bps

Let kappa be the fraction of that residual that closes before settlement.
kappa is UNMEASURED. Taking kappa = 0.10, which is under a quarter of what the
lag +1 correlation would generously permit:

    expected drift mu = 0.10 * 5.19 bps        = 0.52 bps
    z                 = 0.52 / 3.96            = 0.131
    our P(ETH Up)     = Phi(0.131)             = 0.552
    market P(ETH Up), from ETH displacement alone = Phi(0) = 0.500

Apply the model uncertainty shrink that fair_value.py already applies
(DEFAULT_MODEL_UNCERTAINTY = 0.15, in log-odds space):

    log-odds  = ln(0.552 / 0.448)              = 0.2087
    shrunk    = 0.2087 * 0.85                  = 0.1774
    P shrunk  = e^0.1774 / (1 + e^0.1774)      = 0.544

We pay the ask, not the mid. Observed ETH 5m ask at 2026-08-18 09:39Z was 0.51.

    gross edge before any maker adjustment = 0.544 - 0.51 = 3.4c

Now the haircut that matters. The ETH book is quoted by bots that can see BTC.
Assume they already price HALF the catch-up:

    gross edge = 3.4c / 2                      = 1.7c
    as bps of the 51c premium = 1.7 / 51 * 10000 = 333 bps

**Rounded down to 330 bps.** That clears the PREDICTION_MARKET floor of 200bps
in agents/forge.py, which is one tick on a 50c contract.

Say the sobering version out loud: the entire claim is **1.7 cents**, and the
venue's minimum price increment is **1 cent**. This strategy claims to see less
than two ticks of mispricing. Every number in the derivation would have to be
close to right for that to survive contact with a real book.

The edge is LINEAR in kappa. At kappa = 0 the edge is exactly zero, and kappa = 0
is entirely consistent with the lead-lag scan above.

## Why there is no BTC hedge leg

The obvious version of this strategy buys ETH Up and buys BTC Down as a hedge,
and calls the result market neutral. That version is refused here.

At 5m the two are 0.613 correlated. A hedge leg would strip out most of the
shared variance and most of the premium along with it, and what remains is a
levered bet on a 4.5 bps residual funded by two 1c spreads. Worse, it would let
the strategy be described as an arbitrage when it is not one, which is the exact
costume the brief warns about. One leg, stated as directional, is the honest
shape. The downside is bounded by the premium regardless (convention 8), so the
hedge buys nothing structural either.

## How this interacts with the single btc_move cluster in fair_value.py

This is the part that could quietly break the fair value model, so it gets its
own section.

`combine_multipliers` takes the MAX within a declared cluster and the PRODUCT
across clusters. Today there is exactly one cluster, `btc_move`, and the module
docstring is explicit that this is a claim ("we currently have one piece of
information about this window, observed several ways"), not a placeholder.

The tempting move is to add the BTC-derived signal to an ETH fair value as a
SECOND cluster, so it multiplies with ETH's own diffusion signal. **That would be
the mistake the module was written to prevent.** The measured R-squared is 0.376,
so the two signals share 38% of their variance. Declaring them independent
clusters would be independence by declaration, which is precisely what the
docstring refuses.

The correct handling costs nothing and is already supported: put the BTC-derived
signal in the SAME cluster as ETH's own diffusion signal. The max rule then
selects it only when it is the stronger of the two, which is exactly the entry
condition, because the entry requires ETH displacement near zero and BTC
displacement large. When ETH has already moved its beta share, ETH's own
diffusion signal is stronger and wins, and the BTC signal correctly contributes
nothing. The rule does the refusing for us.

So the answer to "what makes this spread independent of the btc_move factor" is:
**nothing does, and the design must not claim otherwise.** The strategy is
buildable inside the existing one-cluster discipline. It is not buildable if
someone adds a cluster to make the numbers bigger.

## Expected trade frequency, and whether 200 trades is reachable

Proxy measurement on the same 1000 bar 5m sample: bars where abs(BTC return)
exceeded 1 BTC sigma AND the ETH residual exceeded 2 residual sigma in the
opposite direction: **13 of 1000, or 1.30%**.

    288 five-minute windows per day * 1.30% = 3.74 events per day
    200 events                              = about 54 days

That is a real answer to convention 7. Two months of live shadow to reach a
sample where a verdict is a verdict rather than a shrug.

Caveat, and it is not small: that 1.30% was computed on FULL BAR returns, not on
the intra-window state at 150 seconds remaining. The intra-window rate will be
different and I have not measured it. Treat 1.30% as an order of magnitude.

## What would make this wrong

1. **kappa is zero.** The residual does not close inside the window, it just
   persists into the next one. The lead-lag scan is consistent with this. This is
   the single most likely failure and the kill condition puts it first.

2. **The residual is not a lag, it is news.** ETH has its own order flow, its own
   liquidations, its own unlock schedule. 79% of its 5m sigma is idiosyncratic on
   this sample. A large residual against BTC is more likely to be ETH-specific
   information than ETH being slow, and buying against ETH-specific information
   is buying into an informed seller.

3. **The strike proxy error swallows the signal.** The entry signal is 5.19 bps
   of implied displacement. The BTC-measured proxy noise floor is 5.0 bps. If the
   ETH proxy is no better than the BTC one, the signal and the measurement error
   are the same size, and every entry is a coin flip dressed as a regression.
   This is why the fourth kill clause exists.

4. **beta is not stable.** 0.787 came from 83 hours. If beta drifts to 0.5 the
   residual computed with 0.787 reads a systematic bias as a signal and the
   strategy fires constantly in one direction. This is the COST_FLOOR = -0.30
   shape (convention 17): a number that was measured once, frozen, and then
   quietly stopped describing the data. beta must be a rolling measurement with
   its own staleness check, and if a future version hardcodes a beta, that is the
   first thing to suspect when the results improve.

5. **The market already prices it.** My 50% haircut for "makers can see BTC" is a
   guess. If the true figure is 90%, the edge is 0.34c, a third of a tick, and
   not expressible on this venue.

## Entry price and win rate must be read together (D-267)

At a 0.51 entry, breakeven is a 51.0% win rate. The claimed fair value after
shrink is 0.544, so the claim is a **54.4% win rate at a 51c entry**. Neither
number means anything alone: 54% right at 60c loses money, and 51% right at 45c
makes it. Any report on this strategy that quotes a win rate without the average
entry price should be rejected on sight.
