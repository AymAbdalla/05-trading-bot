"""Conditional-edge search, done the only way that proves anything.

THE REQUEST (Aym, 2026-08-13): test strategies across sectors, classes and
conditions; pool them generically; then keep the subsets where the pattern
wins, so we know a strategy works under those specific tickers/classes/
conditions.

THE HAZARD: "keep the subsets where it wins" applied to results you have
already seen is the textbook definition of selection bias. With 44 strategies
x 180 tickers x 11 exits, some subset ALWAYS looks profitable by chance. You
can manufacture a gorgeous equity curve from pure noise this way, and it will
fail the moment it meets new data. Reporting such a filtered result as edge
is the single most common way backtests lie.

THE DISCIPLINE THAT MAKES IT VALID: select on one set of instruments, verify
on a DIFFERENT set you did not look at.

  1. Split tickers randomly into SELECT and VERIFY halves (seeded).
  2. On SELECT only, find (strategy, condition) cells that beat the floor
     with adequate sample.
  3. Evaluate those EXACT cells on VERIFY.
  4. Compare the survival rate against the null: the survival rate of cells
     that were ELIGIBLE for selection but were NOT filtered on the floor.
     Survival meaningfully above that null is evidence; survival at or near
     it is data mining exposed.

Repeat over many random splits so the answer is not itself one lucky split.

WHY THE FLOOR IS DERIVED, NOT DECLARED (Raven ruling R-001, 2026-08-17)
-----------------------------------------------------------------------
This script used to hardcode `COST_FLOOR = -0.30` and assume the null was
"about 50%", on the reasoning that a floor near the middle of the cell
distribution makes survival a coin flip. That reasoning only holds while the
floor actually sits near the middle. After the D-261 futures purge the median
cell moved to about -0.06 - far above -0.30 - so nearly every cell cleared
the floor whether it had been selected or not, and the script reported 90-96%
survival as "ABOVE chance ... evidence of real conditional edge". That was an
artifact. A purge only REMOVES rows; it cannot manufacture edge. Convention
17: a hardcoded threshold is an assumption with an expiry date, and this one
had expired.

Two changes make the answer robust to wherever the distribution sits:

  1. The floor is DERIVED per condition slice, as the median per-trade PnL of
     that slice's cells. It moves with the data instead of going stale.
  2. The null is MEASURED, not assumed. For every split we compute the
     survival rate of the eligible-but-UNSELECTED-on cells under the same
     floor on the same verify half. The only thing separating that population
     from the selected one is the floor filter itself, so the difference -
     the LIFT - is exactly what selection bought. No coin-flip assumption is
     needed anywhere.

The verdict is read off the LIFT, never off the raw survival rate. A 96%
survival rate against a 95% null is a null result.

WHY LIFT ALONE IS NOT ENOUGH (found running R-001, 2026-08-17)
---------------------------------------------------------------
Deriving the floor from the median fixed the false positive but exposed a
second trap, and it is worth stating plainly because the fix for the first
problem walks straight into it.

With the floor AT the median, "survives" means "this cell is better than
typical", which is NOT the same question as "this cell makes money". About
half the cells clear a median floor by construction, so ANY stable structure
that persists across instruments will produce lift - and cost-per-trade and
turnover are exactly such structures. Selecting the cheapest-to-trade cells
on one half of the instruments reliably re-picks them on the other half. That
is persistence, not edge.

Measured here: lift came out at +10.8 to +17.2pp on four of five slices,
which clears the R-001 bar and would have printed "ABOVE the measured null".
But the selected cells LOST money on the verify half in every slice
(class: -$313,793 over 16.6M trades), and only 31-45% of them were profitable
at all. Selection was reliably picking the least-bad losers, and they stayed
losers. The null stands.

So the verdict needs BOTH gates:
  1. lift >= MIN_LIFT_FOR_EVIDENCE  - selection generalizes to unseen
     instruments at all, and
  2. the selected cells are actually PROFITABLE out-of-sample, measured
     TRADE-WEIGHTED - it is worth trading.

Gate 2 is trade-weighted on purpose. The unweighted mean per-trade PnL is the
trap that hides inside gate 1: it lets a 55-trade cell at +$4.50 outvote a
200,000-trade cell at -$0.02, and on this library it reported +0.49 for a
slice whose real, money-weighted answer was -0.013. Dollars are trade-
weighted; report both and believe the weighted one.

Conditions supported (all derivable from what the graveyard already stores):
  asset class, sector, timeframe, exit config, and combinations of them.

Usage: python3 backtest/conditional_edge.py [graveyard.json] [--splits N]
"""
import collections
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.asset_class_analysis import asset_class

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIN_TRADES_SELECT = 100  # sample needed before a cell may be SELECTED
MIN_TRADES_VERIFY = 50   # sample needed before a cell may be JUDGED on verify
N_SPLITS = 20

# How much survival must exceed the MEASURED null before the result is called
# evidence rather than noise. Set by Raven ruling R-001 (2026-08-17). This is
# a decision threshold on a measured quantity, not a stand-in for a property
# of the data: unlike the floor it replaced, it does not go stale when the
# cell distribution moves, because the null it is compared against is
# recomputed from that distribution on every run.
MIN_LIFT_FOR_EVIDENCE = 0.10   # 10 percentage points
MIN_JUDGED_FOR_POWER = 30      # below this the slice is underpowered, not null

# Gate 2's bar. Convention 5 is the project's standing dead-on-arrival line:
# under 30bps of edge, do not build it. Reused here rather than inventing a
# new number, and expressed in BPS on purpose - a bps bar is scale-free, so
# unlike the -0.30 dollar floor it replaced it cannot quietly stop meaning
# what it meant when the notional cap or the row population changes.
# A slice can clear zero and still be worthless: the `sector` slice came in at
# +0.0034/trade = 0.34bps, positive and ~100x too small to trade.
MIN_EDGE_BPS = 30.0

SLICES = ('class', 'sector', 'timeframe', 'class_x_timeframe', 'class_x_exit')


def notional_cap_usd(default: float = 100.0) -> float:
    """The denominator that turns $/trade into bps. Read from config.yaml so
    it tracks the cap the rows were actually built under."""
    try:
        import yaml
        with open(os.path.join(ROOT, 'config.yaml')) as f:
            return float(yaml.safe_load(f)['risk']['notional_cap_usd'])
    except Exception:                    # noqa: BLE001 - fall back, say so
        print(f'  (could not read config.yaml; assuming ${default:,.0f} cap)')
        return default


def underlying(ticker: str) -> str:
    return (ticker or '').split('/')[0].replace('_USD', '').replace('_F', '')


def cell_keys(e: dict):
    """The condition slices tested. Each is a hypothesis about WHERE a
    strategy might work. Keep them mechanistic and few - every extra slice
    is another comparison the correction has to pay for."""
    cls = asset_class(e)
    sector = e.get('sector') or 'Unknown'
    return {
        'class': (e['strategy'], cls),
        'sector': (e['strategy'], sector),
        'timeframe': (e['strategy'], e['timeframe']),
        'class_x_timeframe': (e['strategy'], cls, e['timeframe']),
        'class_x_exit': (e['strategy'], cls, e.get('exit_config')),
    }


def aggregate(entries, key_kind, ticker_filter=None):
    agg = collections.defaultdict(lambda: [0, 0.0])
    for e in entries:
        if e.get('verdict') not in ('PASS', 'FAIL', 'PASS_BENCHMARK'):
            continue
        if not e.get('trades'):
            continue
        # BENCHMARKS ARE NOT CANDIDATES. dca_7 / dca_14 have no signal - they
        # buy on a timer - so their PnL is market drift, and drift is exactly
        # what a conditional-edge search must not mistake for edge. Including
        # them let DCA-on-leveraged-ETFs (+$7.44/trade) carry the `sector`
        # slice: excluding them cut that slice's out-of-sample profit by two
        # thirds, from +0.0094 to +0.0034/trade. inversion.py and
        # dispersion_gate.py already treat these rows as controls; this makes
        # the three agree.
        if e.get('is_benchmark'):
            continue
        if ticker_filter is not None and underlying(e.get('ticker')) not in ticker_filter:
            continue
        k = cell_keys(e)[key_kind]
        agg[k][0] += e['trades']
        agg[k][1] += e.get('total_pnl_usd') or 0.0
    return {k: (n, p, p / n) for k, (n, p) in agg.items() if n}


def derive_floor(entries, key_kind):
    """The floor for this slice: the MEDIAN per-trade PnL of its cells.

    Replaces the hardcoded -0.30 (R-001). The median is the natural centre of
    the distribution the selection step is drawing from, so it keeps the
    filter meaningful no matter where costs and the surviving row population
    put that distribution. Cells are weighted equally, not by trade count -
    the selection step also treats each cell as one decision - and only cells
    with enough sample to be judged at all are counted, so a handful of
    3-trade cells cannot drag the centre around.

    Returned alongside the population it was computed from, because a floor
    derived from 12 cells deserves less trust than one derived from 400.
    """
    cells = aggregate(entries, key_kind)
    ppts = [ppt for (n, _p, ppt) in cells.values() if n >= MIN_TRADES_VERIFY]
    if not ppts:
        return None, 0
    return statistics.median(ppts), len(ppts)


def run_split(entries, assets, key_kind, seed, floor):
    """One seeded select/verify split.

    Reports the selected population AND the eligible-but-unfiltered
    population on the same verify half under the same floor. The second is
    the empirical null: it answers "what survival rate does this floor
    produce when nothing was selected on it?"
    """
    rng = random.Random(seed)
    shuffled = sorted(assets)
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    select_set, verify_set = set(shuffled[:half]), set(shuffled[half:])

    sel = aggregate(entries, key_kind, select_set)
    ver = aggregate(entries, key_kind, verify_set)

    # ELIGIBLE: adequate sample on the select half. This is the population the
    # selection step draws from. The null is measured on ALL of it.
    # PICKED: the eligible cells that also beat the floor on the select half.
    # The floor filter is the ONLY difference between the two populations, so
    # any survival gap is attributable to selection and nothing else.
    eligible = [k for k, (n, _p, _ppt) in sel.items() if n >= MIN_TRADES_SELECT]
    picked = set(k for k in eligible if sel[k][2] > floor)

    survived = judged = 0
    null_survived = null_judged = 0
    verify_ppts = []
    sel_pnl = 0.0        # dollars the selected cells made on the verify half
    sel_trades = 0       # trades those dollars were spread over
    profitable = 0       # selected cells that actually made money on verify
    for k in eligible:
        if k not in ver:
            continue
        n, p, ppt = ver[k]
        if n < MIN_TRADES_VERIFY:
            continue
        clears = ppt > floor
        null_judged += 1
        null_survived += clears
        if k in picked:
            judged += 1
            verify_ppts.append(ppt)
            survived += clears
            sel_pnl += p
            sel_trades += n
            profitable += ppt > 0
    return {
        'selected': len(picked), 'judged_on_verify': judged,
        'survived': survived,
        'survival_rate': survived / judged if judged else None,
        'null_judged': null_judged, 'null_survived': null_survived,
        'null_survival_rate': null_survived / null_judged if null_judged else None,
        'mean_verify_ppt': statistics.mean(verify_ppts) if verify_ppts else None,
        'verify_pnl': sel_pnl, 'verify_trades': sel_trades,
        'cells_profitable': profitable,
    }


def main():
    cap = notional_cap_usd()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = args[0] if args else os.path.join(ROOT, 'research', 'graveyard',
                                             'v0_graveyard_full.json')
    n_splits = N_SPLITS
    if '--splits' in sys.argv:
        n_splits = int(sys.argv[sys.argv.index('--splits') + 1])
    if not os.path.exists(path):
        print(f'not found: {path}')
        return 1
    with open(path) as f:
        entries = json.load(f).get('entries', [])

    assets = {underlying(e.get('ticker')) for e in entries if e.get('ticker')}
    assets.discard('')
    print(f'{len(entries):,} rows | {len(assets)} distinct underlyings | '
          f'{n_splits} random select/verify splits\n')
    print('METHOD: pick winning cells on HALF the underlyings, then judge those')
    print('exact cells on the OTHER half. The floor is DERIVED per slice (the')
    print('median cell), and the null is MEASURED as the survival rate of the')
    print('eligible cells that were NOT filtered on that floor. The verdict is')
    print('the LIFT (observed - null), never the raw survival rate: a 96%')
    print(f'survival against a 95% null is nothing. Evidence needs BOTH lift')
    print(f'>= {MIN_LIFT_FOR_EVIDENCE:.0%} AND >= {MIN_EDGE_BPS:.0f}bps of '
          f'trade-weighted profit on the unseen half -')
    print('a slice can generalize perfectly and still only re-pick reliable')
    print('losers. Benchmarks (dca_*) are excluded: they have no signal.\n')

    print(f"{'condition slice':<20s}{'floor':>8s}{'selected':>9s}{'judged':>8s}"
          f"{'surv':>7s}{'null':>7s}{'lift':>8s}{'wtd $/trd':>13s}"
          f"{'bps':>9s}{'+ve':>8s}")
    print('-' * 93)

    summary = {}
    for key_kind in SLICES:
        floor, floor_cells = derive_floor(entries, key_kind)
        if floor is None:
            summary[key_kind] = {'derived_floor': None, 'floor_from_cells': 0,
                                 'note': 'no cells with adequate sample'}
            print(f'{key_kind:<20s}{"n/a":>8s}')
            continue

        runs = [run_split(entries, assets, key_kind, seed, floor)
                for seed in range(n_splits)]
        judged = sum(r['judged_on_verify'] for r in runs)
        surv = sum(r['survived'] for r in runs)
        sel = sum(r['selected'] for r in runs)
        null_judged = sum(r['null_judged'] for r in runs)
        null_surv = sum(r['null_survived'] for r in runs)
        rates = [r['survival_rate'] for r in runs if r['survival_rate'] is not None]
        ppts = [r['mean_verify_ppt'] for r in runs if r['mean_verify_ppt'] is not None]
        sel_pnl = sum(r['verify_pnl'] for r in runs)
        sel_trades = sum(r['verify_trades'] for r in runs)
        profitable = sum(r['cells_profitable'] for r in runs)
        # THE MONEY NUMBER: total dollars over total trades. Not the mean of
        # per-cell rates, which weights a 55-trade cell like a 200k-trade one.
        weighted_ppt = (sel_pnl / sel_trades) if sel_trades else None
        weighted_bps = (weighted_ppt / cap * 10_000) if weighted_ppt is not None else None

        rate = surv / judged if judged else None
        null_rate = null_surv / null_judged if null_judged else None
        lift = (rate - null_rate) if (rate is not None and null_rate is not None) else None

        summary[key_kind] = {
            'derived_floor': round(floor, 6),
            'floor_from_cells': floor_cells,
            'selected_total': sel, 'judged_total': judged, 'survived_total': surv,
            'survival_rate': rate,
            'survival_rate_stdev': statistics.pstdev(rates) if len(rates) > 1 else None,
            'null_judged_total': null_judged,
            'null_survived_total': null_surv,
            'null_survival_rate': null_rate,
            'lift_over_null': lift,
            'mean_verify_pnl_per_trade': statistics.mean(ppts) if ppts else None,
            'weighted_verify_pnl_per_trade': weighted_ppt,
            'weighted_verify_edge_bps': weighted_bps,
            'verify_pnl_usd': round(sel_pnl, 2),
            'verify_trades': sel_trades,
            'cells_profitable_on_verify': profitable,
            'frac_cells_profitable': (profitable / judged) if judged else None,
        }
        rate_s = f'{rate:.1%}' if rate is not None else 'n/a'
        null_s = f'{null_rate:.1%}' if null_rate is not None else 'n/a'
        lift_s = f'{lift:+.1%}' if lift is not None else 'n/a'
        wppt_s = f'{weighted_ppt:+.4f}' if weighted_ppt is not None else 'n/a'
        bps_s = f'{weighted_bps:+.2f}' if weighted_bps is not None else 'n/a'
        prof_s = f'{profitable / judged:.0%}' if judged else 'n/a'
        print(f'{key_kind:<20s}{floor:>8.3f}{sel:>9}{judged:>8}'
              f'{rate_s:>7}{null_s:>7}{lift_s:>8}{wppt_s:>13}{bps_s:>9}{prof_s:>8}')

    print('\nINTERPRETATION  (evidence needs BOTH: lift over the measured null,')
    print('                 AND selected cells that actually make money)')
    any_real = False
    underpowered = []
    for kind, s in summary.items():
        lift = s.get('lift_over_null')
        if lift is None:
            continue
        if s['judged_total'] < MIN_JUDGED_FOR_POWER:
            underpowered.append(kind)
            print(f'  {kind}: only {s["judged_total"]} cells judged - '
                  f'UNDERPOWERED, no verdict either way')
            continue
        wppt = s['weighted_verify_pnl_per_trade']
        wbps = s['weighted_verify_edge_bps']
        head = (f'  {kind}: survival {s["survival_rate"]:.0%} vs null '
                f'{s["null_survival_rate"]:.0%}, lift {lift * 100:+.1f}pp')
        generalizes = lift >= MIN_LIFT_FOR_EVIDENCE
        pays = wbps is not None and wbps >= MIN_EDGE_BPS
        if generalizes and pays:
            any_real = True
            print(f'{head}, and the selected cells cleared the {MIN_EDGE_BPS:.0f}bps '
                  f'bar on unseen underlyings ({wbps:+.2f}bps, {wppt:+.4f}/trade '
                  f'over {s["verify_trades"]:,} trades) - EVIDENCE. Worth a '
                  f'proper out-of-sample test on held-out TIME as well.')
        elif generalizes:
            # The trap this script exists to catch, in its subtler form: the
            # selection persists across instruments but persists at a loss.
            verb = 'lost money on' if (wppt or 0) < 0 else 'made too little on'
            print(f'{head} - selection GENERALIZES but does not PAY. The '
                  f'selected cells {verb} unseen underlyings '
                  f'({wbps:+.2f}bps = {wppt:+.4f}/trade over '
                  f'{s["verify_trades"]:,} trades; only '
                  f'{s["frac_cells_profitable"]:.0%} of them were profitable at '
                  f'all), against a {MIN_EDGE_BPS:.0f}bps bar. Persistent '
                  f'cost/turnover structure, not edge: with the floor at the '
                  f'median, "survives" means "better than typical", and typical '
                  f'does not pay.')
        else:
            print(f'{head} - selection bought essentially nothing. Winners '
                  f'selected here do NOT generalize to unseen instruments.')

    if not any_real:
        print('\n  NULL RESULT: no evidence of conditional edge.')
        print('  No condition slice both cleared the '
              f'{MIN_LIFT_FOR_EVIDENCE:.0%} lift bar over its own measured')
        print(f'  null AND returned {MIN_EDGE_BPS:.0f}bps on the underlyings it had '
              f'not seen.')
        print('  Filtering to winning tickers/classes on this library manufactures')
        print('  results that do not transfer. This is exactly the failure the')
        print('  method exists to detect, and it is the expected outcome when')
        print('  gross edge is zero.')
        if underpowered:
            print(f'  ({len(underpowered)} slice(s) underpowered and excluded: '
                  f'{", ".join(underpowered)})')

    tot_pnl = sum(s.get('verify_pnl_usd') or 0.0 for s in summary.values())
    tot_trades = sum(s.get('verify_trades') or 0 for s in summary.values())
    if tot_trades:
        print(f'\n  Across every slice, the SELECTED cells judged on unseen '
              f'underlyings:\n     ${tot_pnl:,.0f} over {tot_trades:,} trades '
              f'= {tot_pnl / tot_trades:+.4f}/trade')
        unw = [s['mean_verify_pnl_per_trade'] for s in summary.values()
               if s.get('mean_verify_pnl_per_trade') is not None]
        if unw:
            print(f'     (unweighted per-cell mean is {statistics.mean(unw):+.3f} - '
                  f'ignore it, it is dominated by tiny cells)')

    verdict = ('EVIDENCE_OF_CONDITIONAL_EDGE' if any_real
               else 'NULL_no_evidence_of_conditional_edge')
    out = os.path.join(ROOT, 'research', 'graveyard', 'conditional_edge.json')
    with open(out, 'w') as f:
        # allow_nan=False so a non-finite fails loudly here rather than
        # shipping a file that JSON.parse rejects (convention 19).
        json.dump({'verdict': verdict,
                   'floor_method': 'median cell pnl_per_trade, derived per slice (R-001)',
                   'null_method': 'measured: eligible cells not filtered on the floor',
                   'evidence_requires': ('BOTH lift >= min_lift_for_evidence AND '
                                         'weighted_verify_edge_bps >= min_edge_bps'),
                   'min_lift_for_evidence': MIN_LIFT_FOR_EVIDENCE,
                   'min_edge_bps': MIN_EDGE_BPS,
                   'notional_cap_usd': cap,
                   'benchmarks_excluded': True,
                   'min_judged_for_power': MIN_JUDGED_FOR_POWER,
                   'splits': n_splits,
                   'min_trades_select': MIN_TRADES_SELECT,
                   'min_trades_verify': MIN_TRADES_VERIFY,
                   'summary': summary}, f, indent=1, allow_nan=False)
    print(f'\nsaved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
