---
name: "pm_event_fair_value_arb"
thesis: "Long-dated event markets price tail outcomes above any defensible base rate because the marginal buyer of a longshot is a narrative trader with no cost of being wrong until resolution, and the only trade that corrects it locks capital for months, so nobody with a base rate is willing to pay the carry to enforce convergence."
expected_edge_bps: 400
kill_condition: "Any one of: net edge below 200bps (one tick on a mid-priced contract) over 200 or more RESOLVED event-market entries, scored by backtest/polymarket_harness.py on resolution PnL; OR measured median half spread on the target book above 1.5c per share, since 1.5c on a 92c No share is 163bps and two thirds of the estimated edge; OR fewer than 200 resolved markets in the archive where an ex-ante timestamped base rate existed BEFORE the entry timestamp. The first two are measurable by backtest/polymarket_harness.py once the archive exists. The archive does not exist and is build step 1."
asset_class: "PREDICTION_MARKET"
entry_exit_rules: "UNIVERSE: non-crypto event markets discovered by Gamma tag (politics, Fed, macro), resolution date 14 to 180 days out, 24h volume above a floor set from the archive rather than guessed. SIGNAL: an external base rate B, timestamped strictly before the observation, for the Yes outcome. GAP = market_yes_price - B. ENTER the No side when GAP >= 0.04 AND the market Yes price is at or below 0.15, that is, only in the longshot band where the bias is claimed to live. SIZE: whole shares affordable at the notional cap. STOP: none possible and none needed. A losing share settles at exactly 0.00, which is the true floor and satisfies convention 8. TARGET: resolution at 1.00. TIME EXIT: none. The position is held to settlement, because the existing harness scores resolution PnL and nothing else. NO ENTRY when the resolution date is more than 180 days out, because lockup cost scales linearly with holding period and eats the estimate."
data_requirements: "HAVE: Gamma market discovery with a tag filter (engine/polymarket/markets.py list_markets and list_all_markets, which accepts closed=True and pages properly). CLOB price history keyed on conditionId with intervals up to 1y (engine/polymarket/prices.py). Orderbook depth (engine/polymarket/orderbook.py). Resolution outcome by slug (prices.resolution_price_checked). Resolution PnL scoring (backtest/polymarket_harness.py). BLOCKER 1: NO EXTERNAL BASE RATE FEED EXISTS. Nothing in engine/polymarket/ or engine/feeds/ reads polls, forecast aggregators, fed funds futures, or any outside probability estimate. engine/polymarket/fair_value.py is a BTC lognormal strike model and produces nothing usable for an election or an FOMC decision. Without B there is no GAP and the strategy has no entry rule at all. BLOCKER 2: NO RESOLVED EVENT MARKET ARCHIVE EXISTS. resolved_markets_from_client is defined in backtest/polymarket_harness.py and has zero callers anywhere in the repo, and no script archives closed Gamma markets. We hold zero rows of event-market history. BLOCKER 3: THE BASE RATE MUST BE POINT IN TIME AND NEVER REVISED. A poll aggregate that is restated after the fact is lookahead wearing a data feed, and it would manufacture the exact edge this proposal claims to find. BLOCKER 4: THE COST CONSTANTS ARE CALIBRATED ON THE WRONG INSTRUMENT. cost_model.py sets PM_HALF_SPREAD_PER_SHARE = 0.005 and PM_DEPTH_SLIP_PER_SHARE = 0.005, calibrated against BTC Up/Down 5m books, which are among the tightest on the venue. Event-market books are not measured."
related_graveyard_findings: "There are NO PREDICTION_MARKET rows in the graveyard. Its 535,425 rows and 155 distinct findings cover CRYPTO, EQUITY and ETF only, so nothing here inherits a verdict in either direction. Nearest neighbour by NAME is the PM_fair_value_arb family already in strategies/polymarket/ (plus hft, patient, wide and inverse variants). Those are a different instrument and must never be pooled with this: they compute a Bayesian fair value for a 5 minute BTC Up/Down window from BTC state and a strike, they resolve in minutes, and their fair value has no base rate in it at all. This one holds for months and its fair value is an external forecast. Same two words in the name, unrelated bets. Nearest neighbour by MECHANISM in the graveyard is macro_drift, which is a slow directional overlay and is not in the judgeable pooled set, so it supplies no usable prior either."
kind: edge_hypothesis
status: PROPOSED
source: "forge, event-market gap (asset class has zero graveyard coverage, D-268)"
forge_warnings: "none"
---


## Edge arithmetic

Worked on the longshot band, which is where the claimed bias lives and where
the sample requirement is smallest.

Market prices a tail outcome at 8c. An ex-ante base rate says 4c. Buy the No
side at 92c.

    EV per share = 0.96 * 1.00 - 0.92 = 0.04, that is 4c
    gross edge   = 4c / 92c = 435 bps of premium paid

I record 400 rather than 435. The base rate B is itself an estimate, and
rounding an estimate upward is how a proposal number becomes a cited number two
documents later. Convention 15: 400 is an estimate written before any run.

D-267 requires the entry price and the win rate together, because either alone
says nothing on a binary:

    entry 92c on the No side
    breakeven win rate  = 92.0%
    hypothesised win rate = 96.0%
    margin = 4.0 percentage points

Read the other way, 96% right is worthless if you paid 97c for it, and 92% is
plenty if you paid 85c. The pair is the claim.

### Costs, which is where most of the estimate goes

Modelled by backtest/cost_model.py: taker fee 0.0, half spread 0.5c per share,
depth slip 0.5c per share when the fill is walked, gas not charged in backtest
(PM_CHARGE_GAS is False by design).

    modelled half spread only:  0.5c / 92c =  54 bps
    modelled walked fill:       1.0c / 92c = 109 bps

Those constants were fitted to BTC Up/Down 5m books. A politics market 90 days
from resolution routinely quotes 2c to 5c wide, which is a 1c to 2.5c half
spread:

    realistic half spread 1.0c / 92c = 109 bps
    realistic half spread 2.5c / 92c = 272 bps

### Lockup, which the crypto side never has to think about

Capital in a Polymarket share earns nothing. A 90 day hold at a 4% risk free
rate gives up:

    0.04 * (90 / 365) = 0.986% of the premium = 99 bps

That is not a rounding term. It is a quarter of the estimate.

### The whole sum

    gross                                    400 bps
    less cost at the modelled 1.0c walked   -109 bps
    less 90 day lockup                       -99 bps
    net, optimistic                          192 bps

    gross                                    400 bps
    less cost at a realistic 2.5c half sprd -272 bps
    less 90 day lockup                       -99 bps
    net, pessimistic                          29 bps

So the honest reading is: this clears the 200bps PREDICTION_MARKET gross floor
comfortably and clears it NET only if event-market spreads turn out close to
the BTC-calibrated constant. Nobody has measured that. It is BLOCKER 4 and it
is the single number most likely to kill the idea, which is why it sits in the
kill condition as a threshold in cents rather than as a hope.

### Sample size, which for once is the friendly part

At a 92c entry the outcome is nearly deterministic, so per-trade variance is
small compared to a 50c bet.

    win return  = 8/92 = +8.70%,  lose return = -100%
    mean        = 0.96 * 0.0870 + 0.04 * (-1.00) = +4.35%
    sd          = sqrt(0.96 * 0.04) * (0.0870 + 1.0) = 0.196 * 1.087 = 21.3%
    n for t = 2 = (2 * 0.213 / 0.0435)^2 = 96 trades

96 trades. The harness floor MIN_RESOLVED_TRADES is 200, so the convention 7
shrug floor binds before the power calculation does. That is the opposite of
the usual situation on this venue and it is the main reason to work the
longshot band rather than the 50c band.

It does not make 200 resolved event markets easy to get. It just means that if
we ever get them, the answer will be readable.

## Why the edge would persist

The lockup is the mechanism, not an inconvenience. Correcting a 4c mispricing
on a 90 day contract costs 90 days of dead capital plus the spread. An
arbitrageur with a better base rate still has to want that trade more than a
T-bill, and most do not. So the mispricing is not protected by secrecy or by
speed. It is protected by carry.

That story is also the reason to distrust the trade. Read the next section.

## What would make this wrong

**The lockup that protects the edge is also the thing that eats it.** The same
sentence that explains why nobody corrects the mispricing explains why we
should not want to either. If the correct discount for 90 days of locked
capital in an illiquid venue is more than 4% annualised, and it plausibly is
once you price the venue and counterparty risk rather than just the T-bill, the
99bps lockup term is understated and the pessimistic net goes negative.

**The longshot band may be correctly priced and I may be reading a fee.** A 4c
gap at an 8c price is a 50% relative disagreement. Interpreting that as
irrationality assumes our base rate is better than the market's. On events
where the market has information the poll aggregate does not, which is most
interesting events, the base rate is the stale number and we are the sucker.
The test that separates these is whether the gap predicts the outcome
CONDITIONAL on the base rate's own historical calibration, not on average.

**The published longshot-bias literature is a hypothesis, not our evidence.**
There is decades of betting-market work on favourite-longshot bias, and there
are published claims about Polymarket calibration specifically. Every one of
those is somebody else's number from somebody else's sample. Convention 3 and
D-267 are explicit: they are hypotheses until our own harness says otherwise.
Nothing in this document is entitled to lean on them.

**Selection at the point of archiving.** If build step 1 archives closed
markets by Gamma volume ordering, it archives the markets that got attention,
which are disproportionately the ones that resolved dramatically. That biases
the resolved set before the strategy sees it. The archiver has to define its
universe by a rule applied at OBSERVATION time, not by an attribute known only
after resolution.

**A base rate that is quietly a price.** Several forecast aggregators ingest
prediction market prices. If B is derived from anything that reads Polymarket,
GAP is measuring our own reflection and will look small, stable and tradeable.
The feed has to be audited for that before it gates anything.

## Build order, so the blockers do not get skipped

1. A resolved event-market archiver. Gamma supports closed=True and
   list_all_markets already pages it. This is missing work, not missing
   capability.
2. A base rate feed with point-in-time snapshots. This is the real blocker and
   it is not a small one.
3. Measure the event-market half spread and depth on the archive, and either
   confirm PM_HALF_SPREAD_PER_SHARE or replace it. Convention 17: 0.005 is an
   assumption with an expiry date, and it was written for a different book.
4. Only then score with backtest/polymarket_harness.py.

Until step 4 runs, this strategy is NOT_TESTED in the convention 11 sense:
could not run. It is not "ran and found nothing", and no interim number from
steps 1 to 3 is a verdict on it.
