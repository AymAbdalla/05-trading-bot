"""Lab v5 P1 "Horizon Ladder" - a LAW test, signal-agnostic, on the
cross-sectional harness (backtest/cross_sectional.py, SPEC 5.8).

Source: references/strategy-lab-v5.md, proposal P1. The pre-registration
below is quoted VERBATIM from that document and was placed here BEFORE any
results existed (standing rule 4: conditions predicted before testing).

==== PRE-REGISTRATION (verbatim from strategy-lab-v5.md P1) ====

- **Thesis:** per the Toll Law, for any signal carrying true information,
  net edge per trade rises with holding period until signal decay dominates.
  The library's universal failure is predicted by horizon alone.
- **Design:** two deliberately generic, pre-committed signals - REV (5-day
  return in bottom cross-sectional decile -> long) and MOM (60-day return in
  top decile, above 100d MA -> long) - each run across the hold ladder
  {1, 3, 5, 10, 20 days}, all 180 tickers pooled, both cost models.
- **Pre-registered predictions:** (a) net-vs-hold slope is positive for both
  families at short end; (b) REV peaks at 3-10 day holds (Lehmann/Jegadeesh
  weekly-reversal horizon), MOM needs >=20 days; (c) nothing is viable at
  1-day under the old cost model.
- **Kill condition:** flat-or-declining net-vs-hold across BOTH families =>
  the Toll Law fails on this universe and short-horizon research is
  un-condemned.
- **Gross estimate:** REV at 5-day holds, documented weekly-reversal
  literature range ~= 30-80bps gross per trade pre-decay; MOM at 20 days
  similar order.
- **Frequency/power:** deciles x 180 tickers x ~100 non-overlapping
  formations => tens of thousands pooled per cell. Powered even for small
  edges.
- **Fires-check:** trivial (cross-sectional ranks always exist). Time-based
  holdout native: calendar-half split, exactly section 6.3.

==== END PRE-REGISTRATION ====

KILL CONDITION (restated as this module's own, per standing rule 6):
flat-or-declining net-per-trade vs hold length across BOTH the REV and MOM
families kills the Toll Law on this universe. A rising slope in one family
only is a partial survival and must be reported as such, not as a pass.

IMPLEMENTATION DEVIATIONS from the P1 text (decided before running):
- "all 180 tickers" -> all daily-bar tickers in backtest/data/*_1d.csv
  EXCEPT VIX (untradable index; used elsewhere as a context series) and the
  *_F futures (contract-dollar trades ~50x a $100 spot clip would dominate a
  pooled per-trade dollar figure; see daily_tradable_tickers()). ~165 names.
- "both cost models": --cost-model both runs the venue-accurate model
  (D-235) and the legacy flat model (0.10% taker + 0.05% slip). The two
  result sets carry different cost_model_version stamps and are written as
  separate cells; they must never be pooled (standing rule 8).
- Holds are counted in each instrument's OWN bars (trading days for
  equities, calendar days for crypto); the rebalance cadence is counted in
  grid steps (calendar days when crypto is present). A name still held at a
  rebalance is carried, never doubled.
- The harness denies the ranker the decision bar entirely (rank on data
  through t-1, enter at close of bar t) - one bar MORE conservative than
  the classic rank-on-close-trade-that-close convention, per SPEC 5.8's
  no-lookahead non-negotiable.

Usage:
  python3 backtest/run_horizon_ladder.py --smoke            # 10 tickers, quick
  python3 backtest/run_horizon_ladder.py --cost-model both  # the full P1 run
"""
import argparse
import json
import logging
import sys
import time as time_mod
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.cross_sectional import (
    CrossSectionalHarness, Panel, daily_tradable_tickers, make_mom_ranker,
    make_rev_ranker, sector_maps,
)

logger = logging.getLogger(__name__)

HOLD_LADDER = [1, 3, 5, 10, 20]

SMOKE_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META',
                 'GOOGL', 'JPM', 'XOM', 'UNH', 'TSLA']


def run_ladder(tickers, holds, signals, use_cost_model, twin_seeds,
               min_scored) -> list:
    """One (cost model) pass over signals x holds. The panel and rankers are
    built once; each cell is an independent harness run so every cell gets
    its own fires-check, twins, per-cell rows, and leave-one-out."""
    sector_of, _ = sector_maps()
    panel = Panel.from_csv_dir(tickers, '1d', date_align=True)
    print(f'panel: {len(panel.tickers)} tradable names, '
          f'{panel.n_steps} grid steps')

    harness = CrossSectionalHarness({'use_cost_model': use_cost_model})
    rankers = {
        # REV: bottom decile of 5-day return -> long (buy the losers).
        'REV': (make_rev_ranker(days=5),
                {'direction': 'bottom', 'mode': 'decile',
                 'min_scored': min_scored},
                6),
        # MOM: top decile of 60-day return, gated above the 100d MA -> long.
        'MOM': (make_mom_ranker(days=60, ma_period=100),
                {'direction': 'top', 'mode': 'decile',
                 'min_scored': min_scored},
                101),
    }

    cells = []
    for sig in signals:
        ranker, selection, min_history = rankers[sig]
        for hold in holds:
            t0 = time_mod.time()
            report = harness.run(
                panel, ranker, f'horizon_ladder_{sig}',
                selection=selection,
                # Pure hold-N: no stop. P1 tests the HORIZON, and a stop
                # would resample the horizon it claims to test.
                exit_cfg={'type': 'time', 'bars': hold},
                entry_mode='close',
                rebalance_every=hold,   # non-overlapping formations
                min_history=min_history,
                sector_of=sector_of,
                twin_seeds=twin_seeds,
                params={'signal': sig, 'hold_days': hold,
                        'proposal': 'strategy-lab-v5 P1'},
            )
            # Fires-check FIRST (v5 work order 4), then the P&L line.
            fc = report['fires_check']
            print(f"[{sig} hold={hold:>2d}] fires: formations={fc['formations']} "
                  f"opened={fc['names_opened']} "
                  f"ranker_errors={fc['ranker_errors']} "
                  f"({time_mod.time() - t0:.1f}s)")
            print(f"[{sig} hold={hold:>2d}] pnl/trade={report['pnl_per_trade']} "
                  f"trades={report['trades']} pf={report['pf']} "
                  f"twin_pctile={report['twin_percentile']} "
                  f"loo_worst={report['leave_one_out']['pnl_per_trade_worst_drop']} "
                  f"cost={report['cost_model_version']}")
            cells.append(report)
    return cells


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description='Lab v5 P1 Horizon Ladder '
                                 '(cross-sectional REV/MOM x hold ladder)')
    ap.add_argument('--smoke', action='store_true',
                    help='10-ticker proof-of-execution run (reduced twins). '
                    'NOT a result - power is nowhere near the P1 bar.')
    ap.add_argument('--tickers', default=None,
                    help='comma-separated ticker override')
    ap.add_argument('--holds', default=None,
                    help='comma-separated hold ladder override')
    ap.add_argument('--signals', default='REV,MOM')
    ap.add_argument('--cost-model', choices=['venue', 'flat', 'both'],
                    default='venue')
    ap.add_argument('--twin-seeds', type=int, default=None)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    if args.tickers:
        tickers = args.tickers.split(',')
    elif args.smoke:
        tickers = SMOKE_TICKERS
    else:
        tickers = daily_tradable_tickers()
    holds = ([int(h) for h in args.holds.split(',')] if args.holds
             else HOLD_LADDER)
    signals = args.signals.split(',')
    twin_seeds = args.twin_seeds
    if twin_seeds is None:
        twin_seeds = 20 if args.smoke else 100
    # A "decile" needs a real cross-section behind it; on a 10-name smoke
    # panel we accept 8 so the machinery is exercised at all.
    min_scored = 8 if len(tickers) <= 15 else 30

    cost_modes = {'venue': [True], 'flat': [False],
                  'both': [True, False]}[args.cost_model]
    cells = []
    for use_cm in cost_modes:
        label = 'venue-accurate' if use_cm else 'legacy flat'
        print(f'=== cost model: {label} ===')
        cells.extend(run_ladder(tickers, holds, signals, use_cm,
                                twin_seeds, min_scored))

    out = args.out
    if out is None:
        mode = 'smoke' if args.smoke else 'full'
        out = str(ROOT / 'research' / 'cross_sectional'
                  / f'horizon_ladder_{mode}.json')
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump({
            'generated': time_mod.strftime('%Y-%m-%d %H:%M:%S'),
            'proposal': 'strategy-lab-v5 P1 Horizon Ladder',
            'smoke': bool(args.smoke),
            'universe_size': len(tickers),
            'cells': cells,
        }, f, indent=1)
    print(f'saved: {out} ({len(cells)} cells)')

    # The pre-registered read: net-per-trade vs hold, per family per cost
    # model. Printed, never auto-judged - the kill condition is a human
    # decision against the module docstring.
    print('\nnet pnl/trade by hold (read against the pre-registration above):')
    by_key = {}
    for c in cells:
        key = (c['params']['signal'], c['cost_model_version'])
        by_key.setdefault(key, []).append(
            (c['params']['hold_days'], c['pnl_per_trade'], c['trades']))
    for (sig, cmv), rows in sorted(by_key.items()):
        rows.sort()
        line = '  '.join(f'h{h}={p} (n={n})' for h, p, n in rows)
        print(f'  {sig} [{cmv}]: {line}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
