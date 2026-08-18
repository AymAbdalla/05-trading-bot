---
name: "pm_btc_sol_beta_residual"
thesis: "Same shape as proposal 014 applied to SOL: the SOL 5m Up/Down book prices SOL's own displacement and appears not to price the contemporaneous BTC displacement, so a SOL contract sitting far below its beta-implied level during a large BTC move is priced off an incomplete information set."
expected_edge_bps: 330
kill_condition: "Any one of: measured catch-up fraction kappa below 0.10 over 200 or more scored entries, where kappa is the realised share of the beta-implied residual that closes between entry and window settlement; net resolution PnL per share below 0.5c (about 100bps of a 50c entry) over 200 or more scored entries; fewer than 200 qualifying entries in 120 days of paired BTC/SOL 5m history; or a measured SOL strike proxy error at or above 2.9 bps at the 50th percentile, which is the size of the entry signal itself and a tighter bar than the 5.0 bps used for BTC. All four scored by backtest/polymarket_harness.py, with the strike clause scored by a SOL-capable extension of backtest/measure_strike_proxy.py that does not exist yet."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "Observe the BTC and SOL 5m Up/Down markets for the SAME window ts (verified: btc-updown-5m-1787045700 and sol-updown-5m-1787045700 both existed at 2026-08-18 09:39Z, identical boundaries). Compute r_btc = log(spot_btc / strike_btc) and r_sol = log(spot_sol / strike_sol), both against the 60s TWAP proxy strike, not the raw spot. RESIDUAL = r_sol - beta * r_btc, where beta is a ROLLING measured regression coefficient over a trailing window, never a constant. ENTER long the lagging side of SOL when all of: abs(r_btc) >= 1.5 * sigma_btc_window; RESIDUAL is opposite in sign to r_btc and abs(RESIDUAL) >= 2.0 * sigma_residual (TIGHTER than 014's 1.5, because SOL's residual is 88% idiosyncratic); seconds_remaining in [90, 210]; SOL ask at or below 0.55; SOL ask depth at least 3x intended size, checked hard because the SOL 5m book carried 4,345 of quoted liquidity against BTC's 13,519 at the observed instant; BOTH strike proxies outside their measured noise floors. ONE LEG ONLY on the SOL market. NO BTC hedge leg. STOP: none, the premium is the floor (convention 8). TARGET: resolution at 1.00. TIME EXIT: none, the window resolves itself; no new entry under 90s remaining."
data_requirements: "Simultaneous CLOB books for btc-updown-5m-{ts} and sol-updown-5m-{ts} (four token ids per observation). Binance.US SOLUSDT /ticker/price and /klines (verified available). A SOL 60s TWAP proxy strike. BLOCKER: the SOL proxy error has NEVER been measured and backtest/measure_strike_proxy.py cannot measure it, its market lookup is hardcoded to get_btc_updown_5m_checked. BLOCKER: the SOL entry signal is 2.96 bps of implied displacement, which is SMALLER than the 5.0 bps BTC-measured STRIKE_PROXY_NOISE_FLOOR_BPS. If the SOL proxy is no better than the BTC proxy, the signal sits entirely inside the measurement error and the strategy has no usable input at all. BLOCKER: no paired multi-asset history exists. beta and sigma_residual ARE the entry rule and both are unmeasured over any horizon that matters. BLOCKER: kappa is unmeasured and the edge is linear in it."
related_graveyard_findings: "No PREDICTION_MARKET rows exist in the graveyard; all Polymarket strategies are NOT_TESTED per D-268, which is not evidence for or against anything (convention 11). This must NOT be pooled with proposal 014 (pm_btc_eth_beta_residual). Both legs sit on the SAME BTC displacement, so a day when BTC trends produces correlated entries in both, and pooling them would count one factor twice, which is the exact error engine/polymarket/fair_value.py declares its single btc_move cluster to prevent. If both are ever built they need a shared position cap keyed on the BTC signal, not two independent caps. Nearest built neighbours are PM_fair_value_arb and variants on the single BTC 5m market; same machinery, different asset, do not pool."
kind: edge_hypothesis
status: PROPOSED
source: "forge, cross-market crypto sweep 2026-08-18"
forge_warnings: "the brief's higher-beta premise is measurably false; signal smaller than the BTC-measured strike noise floor; kappa unmeasured"
---


## The brief's premise is wrong and I am correcting it up front

I was asked for the BTC/SOL version of 014 as "same shape, higher beta". On the
data I can reach, **SOL is the LOWER beta asset, not the higher one.**

Measured from Binance.US 5m klines, 1000 aligned bars, 83.2 hours span, read
only, 2026-08-18:

| relation | ETH | SOL |
|---|---|---|
| corr with BTC at 5m | 0.613 | **0.468** |
| R-squared | 0.376 | **0.219** |
| beta given BTC | 0.787 | **0.448** |
| own 5m sigma | 5.6 bps | **4.2 bps** |
| residual sigma | 4.5 bps (79% of total) | **3.7 bps (88% of total)** |
| corr with BTC at 1m | 0.353 | **0.254** |

SOL is less correlated with BTC, has a lower beta, and moves less per 5m window
than either BTC or ETH on this sample. Its mean absolute 5m move is 1.6 bps
against BTC's 2.2 and ETH's 2.6.

Convention 15: one 83 hour sample, one venue, measured on exchange bars and not
on the Chainlink TWAP the market settles against. This is a measurement with an
expiry date, not a constant. But it is a measurement, and the "higher beta"
premise was not.

## The central risk, and why SOL makes it more dangerous rather than less

The objection to any cross-asset crypto trade is that both markets are driven by
one factor, so a "hedged spread" is a levered bet on that factor in disguise.
This proposal, like 014, is ONE LEG. There is no BTC hedge, so there is no
costume. The bet is openly directional on SOL.

But SOL introduces a subtler version of the same trap. Its R-squared with BTC is
0.219, so **78% of SOL's 5m variance is not BTC.** That looks like independence,
and independence is exactly what would justify giving the BTC-derived signal its
own cluster in `engine/polymarket/fair_value.py` so it could MULTIPLY with SOL's
own diffusion signal instead of competing with it under the max rule.

That would be wrong, and it would be wrong in a way that is harder to spot than
the ETH case, because a weaker relationship looks more independent while
supplying a weaker signal. The two properties move together and only one of them
is flattering.

The correct handling is identical to 014's and costs nothing: put the BTC-derived
signal in the SAME cluster as SOL's own diffusion signal. `combine_multipliers`
takes the max within a cluster, so the BTC signal is used only when it is
stronger than SOL's own displacement view, which is exactly the entry condition.
Whenever SOL has already moved its beta share, SOL's own signal wins and the BTC
signal correctly contributes nothing. The module docstring's asymmetry applies
verbatim: adding a signal to `btc_move` costs nothing and can only replace a
weaker view of the same fact.

So, plainly: **nothing makes this spread independent of the BTC factor.** The
strategy is buildable inside the existing one-cluster discipline and only inside
it.

## BLOCKER: no measured lead time

Same scan as 014, same 1000 bar 1m sample. Cross correlation of BTC returns
against SOL returns at lag:

| lag (minutes) | corr(BTC_t, SOL_t+lag) |
|---|---|
| -3 | +0.043 |
| -2 | +0.043 |
| -1 | -0.010 |
| **0** | **+0.254** |
| +1 | +0.063 |
| +2 | +0.079 |
| +3 | +0.031 |

The lag +1 value is 0.063 against a contemporaneous 0.254. That is a collapse to
a quarter, and 0.063 on 1000 observations is barely outside sampling noise. For
SOL the lead-lag evidence is WEAKER than for ETH.

**This is a BLOCKER.** No lead has been measured at seconds resolution on the
settlement series, and at the resolution we can reach, the SOL lead is close to
indistinguishable from zero.

## Edge arithmetic

Same derivation as 014, run on SOL's measured inputs. At 150 seconds remaining:

    BTC displacement at entry     = 1.5 * 4.4 bps        = 6.60 bps
    beta-implied SOL displacement = 0.448 * 6.60 bps     = 2.96 bps
    observed SOL displacement (worst case for the market) = 0.00 bps
    unrealised residual                                  = 2.96 bps

    sigma_remaining(SOL) at tau=150s = 4.2 * sqrt(150/300) = 2.97 bps

With kappa = 0.10, the same deliberately harsh catch-up fraction used in 014:

    expected drift mu = 0.10 * 2.96 bps        = 0.296 bps
    z                 = 0.296 / 2.97           = 0.0997
    our P(SOL Up)     = Phi(0.0997)            = 0.540

Model uncertainty shrink (DEFAULT_MODEL_UNCERTAINTY = 0.15, log-odds space):

    log-odds = ln(0.540 / 0.460)               = 0.1590
    shrunk   = 0.1590 * 0.85                   = 0.1352
    P shrunk = e^0.1352 / (1 + e^0.1352)       = 0.534

Observed SOL 5m quote at 2026-08-18 09:39Z was 0.49 bid / 0.50 ask.

    gross before maker adjustment = 0.534 - 0.50 = 3.4c

Halve it for makers who can already see BTC, same assumption as 014:

    gross edge = 1.7c
    as bps of the 50c premium = 1.7 / 50 * 10000 = 338 bps

**Rounded down to 330 bps.**

## Why 015 and 014 carry the SAME number, and why that is not a coincidence

They are equal because SOL's lower beta is almost exactly offset by SOL's lower
volatility. A smaller implied displacement measured against a smaller sigma
produces nearly the same z, and z is what the binary prices.

I could have manufactured a spread between the two headline numbers to make them
look independently derived. I did not, because the arithmetic is genuinely the
same and the inputs genuinely offset. **Do not read the two proposals as two
pieces of evidence.** They are one derivation with two parameter sets, they fire
off the same BTC displacement, and if both are built they need a shared exposure
cap keyed on the BTC signal.

SOL's real handicaps do not show up in the headline bps and are listed below
instead of being smuggled into the number as a fudge.

## SOL's specific handicaps

1. **The signal is smaller than the BTC-measured strike noise floor.** The entry
   signal is 2.96 bps of implied displacement. `STRIKE_PROXY_NOISE_FLOOR_BPS` is
   5.0, measured over 199 BTC windows by `backtest/measure_strike_proxy.py`. If
   the SOL proxy is no better than the BTC proxy, **the entire signal lives
   inside the measurement error.** This is a BLOCKER, not a caveat, and it is why
   the kill condition sets a 2.9 bps bar for SOL against 5.0 for BTC. That bar
   may simply not be achievable, in which case the correct outcome is that this
   strategy is never built.

2. **88% of the residual is idiosyncratic.** SOL's residual sigma is 3.7 bps
   against a total 5m sigma of 4.2. A large residual against BTC is very likely
   to be SOL doing something SOL-specific. This is why the entry threshold is
   2.0 residual sigma here against 1.5 in 014, and that asymmetry is itself an
   assumption with an expiry (convention 17), not a tuned constant.

3. **The book is a third the size.** Quoted liquidity at the observed instant:
   BTC 5m 13,519, ETH 5m 7,556, SOL 5m 4,345. Depth is checked hard in the entry
   rule for that reason, and a single quoted number from one instant is not a
   depth study.

## Expected trade frequency

Proxy measurement on the 1000 bar 5m sample: bars where abs(BTC return) exceeded
1 BTC sigma AND the SOL residual exceeded 2 residual sigma against it:
**8 of 1000, or 0.80%.**

    288 five-minute windows per day * 0.80% = 2.30 events per day
    200 events                              = about 87 days

Nearly three months of live shadow before a verdict stops being a shrug
(convention 7). Same caveat as 014: computed on full bar returns, not on the
intra-window state at 150 seconds remaining, so treat it as an order of
magnitude.

## What would make this wrong

1. **kappa is zero.** The lead-lag scan for SOL is weaker than for ETH and is
   close to indistinguishable from noise. If the residual does not close inside
   the window, the edge is exactly zero. Most likely failure.

2. **The residual is SOL news, not SOL lag.** At 88% idiosyncratic this is the
   default explanation, not the alternative one. Buying a SOL contract because
   SOL diverged from BTC is very often buying against somebody who knows why.

3. **The proxy error swallows the signal.** Covered above. This is the blocker
   that decides whether the strategy can exist at all.

4. **beta drifts.** 0.448 came from 83 hours. A frozen beta turns a regime shift
   into a permanent one-directional signal, which is the COST_FLOOR = -0.30 shape
   (convention 17). beta must be rolling, with a staleness check, and if a future
   version hardcodes it, suspect that first when results improve.

5. **Thin book, adverse fills.** 4,345 of quoted liquidity is not 4,345 of
   takeable depth at the touch, and a 1.7c edge does not survive a 1c slip.

## Entry price and win rate must be read together (D-267)

At a 0.50 entry, breakeven is a 50.0% win rate. The claimed fair value after
shrink is 0.534, so the claim is a **53.4% win rate at a 50c entry**. Either
figure alone is meaningless on a binary. Reject any report on this strategy that
gives a win rate without the average entry price alongside it.
