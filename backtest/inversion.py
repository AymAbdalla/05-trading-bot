"""Strategy inversion (SPEC 5.6) - actually TESTED, not just flagged.

Until now `inversion_flagged` was a to-do marker: thousands of flags, zero
inverted variants ever run. This module implements the V1 (long-only)
inversion type and, critically, the guardrails from the harness-validation
review's finding F2:

    "Inverting a strategy inverts its gross edge. It does not invert its
     costs. A PF-0.07 result is almost certainly fee drag, not an
     anti-signal. And the worst results in a large grid are the unluckiest,
     not the most reliably wrong."

So a flagged failure is NOT eligible for inversion just because net PF < 0.5.
Three conditions, ALL required (F2):

  1. GROSS PF (before fees) significantly below 1.0 - this is the whole test.
     A strategy whose gross PF is >= 1.0 lost to costs, not to being wrong.
  2. Sample adequacy - the same trade-count bar a positive result must clear.
  3. Out-of-sample confirmation - the fade is measured on a held-out slice,
     and carries the ORIGINAL's hypothesis count (inverting is the same
     hypothesis with a sign flip, not a new one).

V1 inversion implemented here: SIGNAL-AS-EXIT (fade). The failed entry signal
becomes an exit trigger on an always-long base: "when bullish_engulfing
fires, close any open long." If the pattern reliably precedes weakness,
exiting on it beats holding through it. Both legs pay full fees and slippage.

NOT implemented (documented gap): the contrarian-filter inversion ("block new
entries for N candles"). It only has meaning paired with a BASE entry
strategy, which makes it a combinatorial test across the strategy library
rather than a per-strategy variant. Needs its own design before it produces
interpretable numbers.
"""
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import (VectorizedBacktestHarness, Indicators,
                                         precompute_indicators, SCAN_WINDOW)

logger = logging.getLogger(__name__)

# F2 gating
MAX_GROSS_PF_FOR_INVERSION = 0.90   # gross edge must be clearly negative
MIN_TRADES_FOR_INVERSION = 30       # sample adequacy (same bar as a positive)
MIN_FADE_EDGE_USD = 0.0             # fade must BEAT buy-and-hold in dollars


@dataclass
class FadeResult:
    strategy: str
    ticker: str
    timeframe: str
    exits_taken: int
    fade_pnl_usd: float
    buy_hold_pnl_usd: float
    edge_usd: float
    beats_hold: bool
    oos: bool = True

    def to_dict(self) -> dict:
        return {
            'strategy': self.strategy, 'ticker': self.ticker,
            'timeframe': self.timeframe, 'inversion_type': 'signal_as_exit_fade',
            'exits_taken': self.exits_taken,
            'fade_pnl_usd': round(self.fade_pnl_usd, 4),
            'buy_hold_pnl_usd': round(self.buy_hold_pnl_usd, 4),
            'edge_usd': round(self.edge_usd, 4),
            'beats_hold': self.beats_hold,
            'out_of_sample': self.oos,
        }


def is_inversion_eligible(entry: dict) -> Optional[str]:
    """F2 gate. Returns None if eligible, else the reason it is not.

    `entry` is a graveyard report dict (needs gross_pf, trades, pf)."""
    if not entry.get('inversion_flagged'):
        return 'not flagged'
    if entry.get('is_benchmark'):
        # Fading a benchmark is meaningless: DCA has no signal to be wrong
        # about, so "exit whenever DCA would buy" tests nothing.
        return 'benchmark strategy (no signal to invert)'
    trades = entry.get('trades', 0)
    if trades < MIN_TRADES_FOR_INVERSION:
        return f'sample too small: {trades} < {MIN_TRADES_FOR_INVERSION}'
    gross = entry.get('gross_pf')
    if gross is None:
        # None encodes INFINITE gross PF (zero gross losers) - the opposite
        # of a reliably-wrong signal.
        return 'gross_pf infinite (not a negative gross edge)'
    if gross > MAX_GROSS_PF_FOR_INVERSION:
        return (f'gross_pf {gross:.2f} > {MAX_GROSS_PF_FOR_INVERSION}: '
                f'lost to COSTS, not to being wrong (F2)')
    return None


def run_fade_test(harness: VectorizedBacktestHarness, strategy,
                  ind: Indicators, ticker: str, timeframe: str,
                  signals: Optional[List] = None) -> FadeResult:
    """Always-long base; exit on every signal from `strategy`, re-enter on the
    next bar. Compared against plain buy-and-hold over the SAME window with
    the SAME notional and fees on every leg.

    Interpretation: edge_usd > 0 means stepping out of the market whenever
    this pattern fires beat holding through it - i.e. the pattern predicts
    weakness, which is exactly what a failed LONG entry signal being
    'reliably wrong' would imply.
    """
    fee, slip = harness.taker_fee, harness.slippage
    notional = harness.notional_cap
    n = ind.n
    min_idx = min(SCAN_WINDOW, 100)

    if signals is None:
        signals = harness.scan_all_bars(strategy, ind)

    # Buy-and-hold baseline over the identical window.
    bh_entry = float(ind.closes[min_idx]) * (1 + slip)
    bh_exit = float(ind.closes[-1]) * (1 - slip)
    bh_qty = notional / bh_entry
    bh_pnl = ((bh_exit - bh_entry) * bh_qty
              - (bh_entry + bh_exit) * bh_qty * fee)

    # Fade: hold, but step out whenever the signal fires; re-enter next bar.
    pnl = 0.0
    exits = 0
    i = min_idx
    in_position = True
    entry_px = bh_entry
    qty = notional / entry_px
    while i < n:
        sig = signals[i] if i < len(signals) else None
        if in_position and sig is not None:
            exit_px = float(ind.closes[i]) * (1 - slip)
            pnl += (exit_px - entry_px) * qty - (entry_px + exit_px) * qty * fee
            exits += 1
            in_position = False
        elif not in_position:
            entry_px = float(ind.closes[i]) * (1 + slip)
            qty = notional / entry_px
            in_position = True
        i += 1

    if in_position:
        exit_px = float(ind.closes[-1]) * (1 - slip)
        pnl += (exit_px - entry_px) * qty - (entry_px + exit_px) * qty * fee

    edge = pnl - bh_pnl
    return FadeResult(
        strategy=strategy.name, ticker=ticker, timeframe=timeframe,
        exits_taken=exits, fade_pnl_usd=pnl, buy_hold_pnl_usd=bh_pnl,
        edge_usd=edge, beats_hold=edge > MIN_FADE_EDGE_USD,
    )


def test_inversions(graveyard_path: str, data_loader_fn, strategy_lookup: Dict,
                    output_path: str, config: Optional[dict] = None,
                    max_candidates: int = 200) -> dict:
    """Read a graveyard, F2-gate its flagged failures, and actually run the
    fade test on the eligible ones. Writes a JSON report.

    data_loader_fn(ticker, timeframe) -> candles list (caller supplies, so
    this module stays independent of file layout).
    """
    with open(graveyard_path) as f:
        graveyard = json.load(f)
    entries = graveyard.get('entries', [])

    harness = VectorizedBacktestHarness(config or {})
    eligible, rejected = [], {}
    seen = set()
    for e in entries:
        key = (e.get('ticker'), e.get('timeframe'), e.get('strategy'))
        if key in seen:
            continue  # one fade test per strategy/ticker/tf, not per exit config
        reason = is_inversion_eligible(e)
        if reason is None:
            seen.add(key)
            eligible.append(e)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1

    logger.info(f'inversion candidates: {len(eligible)} eligible, '
                f'{sum(rejected.values())} rejected')
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1])[:5]:
        logger.info(f'  rejected ({count}): {reason}')

    dropped_by_cap = max(0, len(eligible) - max_candidates)
    if dropped_by_cap:
        logger.info(f'CAP: {len(eligible)} eligible candidates, '
                    f'max_candidates={max_candidates}, testing first '
                    f'{max_candidates} ({dropped_by_cap} dropped)')

    # Not every candidate inside the cap reaches the fade test either. Those
    # skips were silent too, which is how 200 considered became 196 tested
    # with nothing in the output saying why.
    skipped = {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    results = []
    considered = eligible[:max_candidates]
    for e in considered:
        strategy = strategy_lookup.get(e['strategy'])
        if strategy is None:
            skip('strategy_not_in_lookup')
            continue
        candles = data_loader_fn(e['ticker'], e['timeframe'])
        if not candles or len(candles) < 300:
            skip('insufficient_candles_lt_300')
            continue
        # Out-of-sample: the fade is measured on the LAST 20% - the same
        # holdout the original verdict used, never the fitting window.
        test_candles = candles[int(len(candles) * 0.8):]
        if len(test_candles) < 150:
            skip('holdout_lt_150_bars')
            continue
        ind = precompute_indicators(test_candles)
        try:
            res = run_fade_test(harness, strategy, ind, e['ticker'], e['timeframe'])
        except Exception as exc:
            logger.error(f"fade test failed for {e['strategy']} {e['ticker']}: {exc}")
            skip('fade_test_raised')
            continue
        results.append(res.to_dict())

    winners = [r for r in results if r['beats_hold']]
    report = {
        'generated_from': os.path.basename(graveyard_path),
        'method': 'signal_as_exit_fade (SPEC 5.6 V1), F2-gated',
        'f2_gate': {
            'max_gross_pf': MAX_GROSS_PF_FOR_INVERSION,
            'min_trades': MIN_TRADES_FOR_INVERSION,
            'note': 'gross PF (pre-fee) must be clearly < 1.0: a net-PF failure '
                    'that is gross-PF-positive lost to costs, not to being wrong',
        },
        'candidates_eligible': len(eligible),
        'candidates_rejected_by_reason': rejected,
        'cap_info': {
            'max_candidates': max_candidates,
            'eligible': len(eligible),
            'considered': len(considered),
            'dropped_by_cap': dropped_by_cap,
            'capped': dropped_by_cap > 0,
            'skipped_within_cap_by_reason': skipped,
            'note': ('`tested` counts only candidates that reached the fade '
                     'test. eligible - dropped_by_cap - '
                     'sum(skipped_within_cap_by_reason) = tested. An untested '
                     'candidate is NOT a candidate that was tested and found '
                     'nothing (standing rule 11); the cap is arbitrary '
                     'ordering, not a verdict.'),
        },
        'tested': len(results),
        'beat_buy_hold': len(winners),
        'multiple_comparison_note':
            'These carry the ORIGINAL hypotheses count. An inverted variant is '
            'the same hypothesis with a sign flip, not a new one. Do not treat '
            f'{len(winners)} winners out of {len(results)} as {len(winners)} discoveries.',
        'results': sorted(results, key=lambda r: -r['edge_usd']),
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    cap_msg = (f' (CAPPED: {dropped_by_cap} of {len(eligible)} eligible never '
               f'tested)' if dropped_by_cap else '')
    logger.info(f'wrote {output_path}: {len(results)} tested, '
                f'{len(winners)} beat buy-and-hold{cap_msg}')
    return report
