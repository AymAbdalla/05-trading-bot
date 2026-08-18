#!/usr/bin/env python3
"""One-time seed of `_DIGEST.md` from the notes that predate the digest hook.

`agents/vault_writer.py` only starts appending digest entries the next time it
writes a note. Six Trading notes already existed before that: two lessons, two
strategy cards, and two Forge-Cycle-Summaries (the day-1 cycle takeaway and the
critic's post-mortem). Without this script Forge's first post-digest read
would see a digest that is missing everything written before today, which is
worse than the full-tree read it replaces.

Each entry below was hand-written by reading the real note in full and
compressing it to the same three-line Verdict / Evidence / Relevance shape
`agents/vault_digest.append_entry` expects everywhere else, because these
notes predate the `## Digest Entry` section `vault_writer` now asks the model
for and so cannot be extracted mechanically. Re-running this script is a
no-op past the first time: `--check` refuses to double-seed a name already in
the digest.

Usage:
    env -u PYTHONPATH python3 scripts/vault_digest_backfill.py --check
    env -u PYTHONPATH python3 scripts/vault_digest_backfill.py --apply
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents import vault_digest  # noqa: E402

# (date, source filename, verdict, evidence, relevance)
BACKFILL = [
    ('2026-08-18', '2026-08-18-fair-value-arb-spread-problem.md',
     'TESTED_FAILED. Fair value arb (parent, hft, inverse) enters at ASK and '
     'exits at BID on ~8s holds; the inverse variant proves the model is not '
     'the problem, the round trip cost is.',
     '616 family trades, -$338.60 net, $0.00 fees; inverse wins 48.1% of 154 '
     'trades and still loses $32.50; sell:converged fired 3/616 (0.49%).',
     'Do not propose a market-order-both-legs Polymarket 5m strategy, do not '
     'invert a losing signal, do not lower the edge threshold to trade more. '
     'The only fix that addresses the mechanism is maker-fill simulation.'),
    ('2026-08-18', '2026-08-18-corridor-pair-works.md',
     'Superseded by D-314 and by the corridor_pair_live.md strategy card. The '
     '"buy both sides under $1.00" structural-arbitrage framing this note '
     'used is corrected: read the card, not this note, for the live framing.',
     '2 closed trades = 1 acted signal, 2 legs, 1 BTC outcome, +$3.95; '
     'corridor family net -$4.55 on 9 trades; 89.9% of evaluations blocked on '
     'two clock gates (not_final_third_of_15m, late_in_window).',
     'Do not cite this note\'s "100% win rate, risk-free" framing. Read '
     '[[corridor_pair_live]] for the D-314-corrected thesis.'),
    ('2026-08-18', 'corridor_pair_live.md',
     'PROVISIONAL (n=2, below the 30-trade bar). D-314 corrected the thesis: '
     'a 15m-leader plus final-5m-opposite pair on two clocks settling off one '
     'close, not two complementary outcome tokens; both legs winning is the '
     'DESIGNED payoff, not an anomaly.',
     '2 trades, +$3.95, avg entry 0.605; the one observed pair cost $1.21 '
     'combined (above the old sub-$1.00 framing) and paid out $2.00; siblings '
     'corridor_pair -$4.80/3 and corridor_collector -$3.70/4.',
     'Measure any corridor proposal against binned fair value '
     '1.00+P(corridor), never against $1.00. A one-legged fill has no floor '
     '(the $4.20 unhedged-loss risk). Kill at 30 trades if net is below $0.'),
    ('2026-08-18', 'fair_value_arb.md',
     'TESTED_FAILED, family verdict on 615 trades. 3 of 5 variants (parent, '
     'hft, inverse) triggered the kill condition; wide and patient are below '
     'the 30-trade bar and not evaluable.',
     'Family -$337.63, $0.00 fees; parent -$162.99/259 (32.8% WR); hft '
     '-$134.70/172 (22.7% WR, worst per-trade); inverse -$32.50/154 (48.1% '
     'WR, still negative, proving the cost is execution not the model).',
     'Do not invert the signal, lower the edge threshold, or raise '
     'max_trades_this_window for this family. The only fix that addresses '
     'the diagnosed mechanism is maker-fill simulation (not yet built).'),
    ('2026-08-18', '2026-08-18-cycle-001-day-1-lessons.md',
     'Day-1 shadow cycle summary, 778 closed trades, -$418.18 book P&L. '
     'Nothing "worked" at a supportable sample size; only temporal_arbitrage '
     'clears the 30-trade bar while positive, and it is economically flat.',
     'fair_value family is the loss, -$335.74/608 trades; an unexplained '
     'equity discontinuity (equity returns to exactly $1,000.00 at 11:26) '
     'means the day P&L does not reconcile; maker_fill_not_simulated blocks '
     'box_builder and grid_hedge, 2,475 skips combined.',
     'Reconcile the equity path before grading any proposal against it. Do '
     'not treat a never-fired strategy as a failed idea (Convention 11). '
     'Build maker-fill simulation before proposing more limit-order fixes.'),
    ('2026-08-18', '2026-08-18-critic-1970-01-01t0000-to-2026-08-18t1545.md',
     'Critic post-mortem over 983 closed trades disputes the deterministic '
     'classifier\'s headline: 413 losses labelled model_miscalibrated are '
     'execution-cost losses, not a bad model, per the inverse variant.',
     'KILL recommended for fair_value_arb (313, -$169.55), fair_value_arb_hft '
     '(219, -$167.03), fair_value_arb_inverse (186, -$48.53), dip_arb (138, '
     '-$49.73); stop_too_tight is real (67 trades, all fair-value family, all '
     'SOL) and contradicts the recorded stop_px=0.00 on every row.',
     'Do not propose a fair-value-family fix assuming stop_px=0.00 without '
     'first resolving the stop_px-versus-price_stop contradiction. Classifier '
     'itself needs repair: rename model_miscalibrated, gate '
     'entry_signal_wrong on payoff ratio, loosen spread_eats_edge.'),
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='write the entries; without this, only --check '
                             'or --apply is accepted, nothing is written')
    parser.add_argument('--check', action='store_true',
                        help='print what WOULD be written, write nothing')
    args = parser.parse_args(argv)

    if not args.apply and not args.check:
        parser.error('pass --check to preview or --apply to write')

    existing = vault_digest.digested_names(vault_digest.read_digest())
    to_write = [e for e in BACKFILL if e[1] not in existing]
    skipped = [e for e in BACKFILL if e[1] in existing]

    for date, name, verdict, evidence, relevance in to_write:
        print(('WOULD WRITE' if args.check else 'writing'), name)
        if args.apply:
            vault_digest.add_conclusion(name, verdict, evidence, relevance,
                                        date=date)
    for _date, name, _v, _e, _r in skipped:
        print('already in digest, skipping:', name)

    if args.apply:
        print('done: %d written, %d already present'
             % (len(to_write), len(skipped)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
