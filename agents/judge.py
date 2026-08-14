"""Judge: emits read-only evidence packs from the graveyard.

Implements the Judge role (agents/judge/SOUL.md) as the ~80%-deterministic
program docs/AGENT-RUNTIME-PROPOSAL.md says it already is: Judge has no
opinions, only numbers with their n's. This module does not reimplement any
backtest logic - it wraps validate_harness.py, assertions.py,
pooled_analysis.py, asset_class_analysis.py, and summarize_graveyard.py into
one evidence-pack JSON.

Read-only: this module reads a graveyard JSON and writes only its own
evidence-pack JSON under research/. It never writes to strategy_registry,
registry.json, or opens any database in write mode, and it never calls the
wrapped modules with an output_path that would make THEM write graveyard
files either.
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys
import time
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import assertions, asset_class_analysis, pooled_analysis, summarize_graveyard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SOUL: "Under 30 trades you report cold-start... Under 50 shadow signals you
# report reviewable but not promotable... 50 is the bar for a promotion
# decision." Bars taken verbatim from SOUL.md, not invented here.
COLD_START_CEILING = 30
PROMOTABLE_FLOOR = 50

TESTED_VERDICTS = ('PASS', 'FAIL', 'PASS_BENCHMARK')


def _attempt(fn, path: str, attempts: int = 5, delay: float = 3.0):
    """Call fn(path), retrying a partial read. Returns (value, error_or_None).

    Same reasoning as load_graveyard: a sweep rewriting the graveyard makes
    truncated reads a normal event, not an exceptional one. The difference is
    that a failure here degrades one SECTION of the pack rather than the whole
    thing, so it is reported instead of raised.
    """
    last = None
    for i in range(max(attempts, 1)):
        try:
            return fn(path), None
        except json.JSONDecodeError as e:
            last = f'{type(e).__name__}: {e}'
            if i < attempts - 1:
                time.sleep(delay)
        except OSError as e:
            return None, f'{type(e).__name__}: {e}'
    return None, last


class GraveyardUnreadable(Exception):
    """The graveyard exists but could not be parsed.

    Deliberately NOT the same thing as an empty graveyard. See load_graveyard.
    """


def load_graveyard(path: str, attempts: int = 5, delay: float = 3.0) -> List[dict]:
    """A missing graveyard is an empty one. An UNPARSEABLE graveyard is not.

    The earlier version swallowed json.JSONDecodeError and returned [], which
    collapsed "I could not read the evidence" into "there is no evidence" -
    the exact confusion convention 11 bans for verdicts (NOT_TESTED means
    "could not run", never "ran and found nothing"). The failure mode was
    real, not theoretical: run_incremental_graveyard.py rewrites the whole
    12MB file after every ticker, so a read landing mid-`json.dump` sees
    truncated JSON. Judge then emitted a confident `status: DURABLE,
    entries: 0` pack against a 287k-entry graveyard.

    So: retry first (the partial-write window is short), and if it still will
    not parse, raise rather than lie. A genuinely empty or missing file
    still returns [] as before.
    """
    if not path or not os.path.exists(path):
        return []
    last = None
    for i in range(max(attempts, 1)):
        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)   # sweep is probably mid-save; let it finish
            continue
        except OSError:
            return []
        return data.get('entries', []) or []
    raise GraveyardUnreadable(f'{path} did not parse after {attempts} attempts: {last}')


def run_validation() -> bool:
    """Runs validate_harness.py's control suite. Imported lazily so that
    importing this module (or unit-testing build_evidence_pack with an
    injected validation_fn) never pulls in the harness/data-loading stack."""
    from backtest.validate_harness import main as _validate_harness_main
    return bool(_validate_harness_main())


def _confidence_label(n_trades: int) -> str:
    if n_trades < COLD_START_CEILING:
        return 'cold_start'
    if n_trades < PROMOTABLE_FLOOR:
        return 'reviewable_not_promotable'
    return 'evaluable'


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _win_rate_ci(win_rate: float, n: int, z: float = 1.96) -> Optional[List[float]]:
    """Wald normal-approximation 95% CI on win rate - NOT an exact binomial
    (Wilson/Clopper-Pearson) interval, and unreliable near n or p at the
    edges. Flagged as approximate rather than left uncomputed, per SOUL:
    'report confidence intervals on PF and win rate, or at minimum a
    binomial test against the 50 percent null.'"""
    if not n:
        return None
    se = math.sqrt(max(win_rate * (1 - win_rate), 0.0) / n)
    return [round(max(0.0, win_rate - z * se), 4), round(min(1.0, win_rate + z * se), 4)]


def _binomial_z_vs_null(win_rate: float, n: int, p0: float = 0.5) -> Optional[dict]:
    """Normal-approximation z-test of win_rate against a 50% null. Same
    approximation caveat as _win_rate_ci."""
    if not n:
        return None
    se0 = math.sqrt(p0 * (1 - p0) / n)
    if se0 == 0:
        return None
    z = (win_rate - p0) / se0
    p_value = 2 * (1 - _norm_cdf(abs(z)))
    return {'z': round(z, 3), 'p_value': round(p_value, 4), 'null': p0}


def _one_or_list(values):
    vals = sorted({v for v in values if v is not None})
    if not vals:
        return None
    return vals[0] if len(vals) == 1 else vals


def _per_strategy_rows(entries: List[dict]) -> List[dict]:
    """Per-strategy evidence rows. Pooled trade/win-rate aggregates come
    straight from pooled_analysis.per_strategy_summary (not recomputed here);
    twin_percentile / cost_model_version / asset_class / NOT_TESTED handling
    are read off the raw rows because per_strategy_summary does not carry
    them."""
    pooled_rows = {r['strategy']: r for r in pooled_analysis.per_strategy_summary(entries)}

    by_strategy = collections.defaultdict(list)
    for e in entries:
        by_strategy[e.get('strategy')].append(e)

    rows = []
    for strategy_name, group in sorted(by_strategy.items(), key=lambda kv: (kv[0] is None, kv[0])):
        tested = [e for e in group if e.get('verdict') in TESTED_VERDICTS]
        not_tested = [e for e in group if e.get('verdict') == 'NOT_TESTED']

        if not tested:
            # Never converted to a failure: NOT_TESTED is a verdict (SOUL).
            reasons = sorted({e.get('not_tested_reason') for e in not_tested
                              if e.get('not_tested_reason')})
            rows.append({
                'strategy': strategy_name,
                'verdict': 'NOT_TESTED',
                'not_tested_reason': reasons[0] if len(reasons) == 1 else (reasons or None),
                'n_trades': 0,
                'n_rows_tested': 0,
                'n_rows_not_tested': len(not_tested),
                'confidence': None,
                'observed_best_pf': None,
                'infinite_pf_row_count': 0,
                'pooled_win_rate': None,
                'win_rate_ci_95_approx': None,
                'win_rate_vs_50pct_null': None,
                'twin_percentile_median': None,
                'twin_percentile_n': 0,
                'cost_model_version': _one_or_list(e.get('cost_model_version') for e in group),
                'asset_class': _one_or_list(e.get('asset_class') for e in group),
            })
            continue

        pooled = pooled_rows.get(strategy_name, {})
        n_trades = pooled.get('pooled_trades', sum(e.get('trades') or 0 for e in tested))

        finite_pfs = [e['pf'] for e in tested if e.get('pf') is not None]
        # pf is None in the harness's own encoding of infinite PF (zero
        # losses), never "not computed" - SOUL: never accept infinite PF as
        # a pass, so it is counted and surfaced, not silently dropped.
        infinite_pf_rows = sum(1 for e in tested
                               if e.get('pf') is None and (e.get('trades') or 0) > 0)
        twin_percentiles = [e['twin_percentile'] for e in tested
                            if e.get('twin_percentile') is not None]

        win_rate = pooled.get('pooled_win_rate')
        ci = _win_rate_ci(win_rate, n_trades) if win_rate is not None else None
        z_test = _binomial_z_vs_null(win_rate, n_trades) if win_rate is not None else None

        rows.append({
            'strategy': strategy_name,
            'verdict': None,
            'n_trades': n_trades,
            'n_rows_tested': len(tested),
            'n_rows_not_tested': len(not_tested),
            'confidence': _confidence_label(n_trades),
            'observed_best_pf': max(finite_pfs) if finite_pfs else None,
            'infinite_pf_row_count': infinite_pf_rows,
            'pooled_win_rate': win_rate,
            'win_rate_ci_95_approx': ci,
            'win_rate_vs_50pct_null': z_test,
            'twin_percentile_median': (statistics.median(twin_percentiles)
                                       if twin_percentiles else None),
            'twin_percentile_n': len(twin_percentiles),
            'cost_model_version': _one_or_list(e.get('cost_model_version') for e in tested),
            'asset_class': _one_or_list(e.get('asset_class') for e in tested),
            'pooled_trades': pooled.get('pooled_trades'),
            'tickers': pooled.get('tickers'),
            'judgeable_pooled': pooled.get('judgeable'),
        })
    return rows


def _expected_best_by_chance(strategy_rows: List[dict],
                             graveyard_summary: Optional[dict]) -> dict:
    """Grid size next to observed-best, per strategy, per SOUL: 'you report
    expected-best-by-chance next to observed-best.' The sqrt(2 ln n) base
    rate over TESTS COMPLETED is reused from summarize_graveyard.py, which
    already computes it - not reimplemented here.

    TODO: no module in this codebase corrects on hypotheses GENERATED
    (Forge's search log) rather than submitted/tested. That correction is
    out of scope for this wrapper (SOUL: correct on generated, never on
    submitted, but the generated count is not tracked anywhere yet).
    """
    tested_strategies = sorted(r['strategy'] for r in strategy_rows if r.get('n_rows_tested'))
    observed_best = {r['strategy']: r.get('observed_best_pf')
                     for r in strategy_rows if r.get('observed_best_pf') is not None}
    mc = (graveyard_summary or {}).get('multiple_comparisons', {})
    return {
        'n_strategies_tested': len(tested_strategies),
        'tests_completed': mc.get('tests_completed'),
        'expected_max_z_under_null': mc.get('expected_max_z_under_null'),
        'note': mc.get('note'),
        'observed_best_pf_by_strategy': observed_best,
        'generated_hypothesis_correction': 'not available: hypotheses-generated count is not tracked upstream',
    }


def _empty_pack(graveyard_path: str, strategy: Optional[str],
                status: str, harness_validated: bool) -> dict:
    return {
        'status': status,
        'harness_validated': harness_validated,
        'graveyard': os.path.basename(graveyard_path) if graveyard_path else None,
        'strategy_filter': strategy,
        'entries_total': 0,
        'silent_assertions': None,
        'degraded': None,
        'distinct_findings': None,
        'graveyard_summary': None,
        'strategies': [],
        'asset_class_breakdown': [],
        'expected_best_by_chance': None,
        'note': 'not available: no graveyard entries for this path/filter',
    }


def build_evidence_pack(graveyard_path: str, strategy: Optional[str] = None,
                        validation_fn: Optional[Callable[[], bool]] = None) -> dict:
    """The core Judge output. validation_fn defaults to run_validation() (the
    real harness suite) but is injectable so callers/tests are not forced to
    run the full control suite to exercise this function."""
    validation_fn = validation_fn or run_validation
    harness_validated = bool(validation_fn())
    status = 'DURABLE' if harness_validated else 'PROVISIONAL'

    try:
        all_entries = load_graveyard(graveyard_path)
    except GraveyardUnreadable as e:
        # A pack we could not read the evidence for is never DURABLE, however
        # green the harness is. Say so instead of reporting an empty pack.
        pack = _empty_pack(graveyard_path, strategy, 'UNREADABLE', harness_validated)
        pack['note'] = (f'not available: graveyard could not be parsed ({e}). '
                        f'If a sweep is running, re-run once it exits.')
        return pack

    entries = ([e for e in all_entries if e.get('strategy') == strategy]
              if strategy else all_entries)

    if not entries:
        return _empty_pack(graveyard_path, strategy, status, harness_validated)

    # These two re-read the graveyard from disk independently of
    # load_graveyard, so each can hit its own partial write during a live
    # sweep. Retry as load_graveyard does, and if a section still cannot be
    # produced, say WHICH and WHY in `degraded` rather than returning a pack
    # that silently lacks it. `distinct_findings` comes from the summary and
    # convention 2 requires citing it - a silent None there is how a pack
    # ends up with no multiple-comparisons correction and no sign of it.
    silent_assertions = None
    graveyard_summary = None
    degraded = []
    if os.path.exists(graveyard_path):
        # No output_path: Judge never makes the wrapped modules write
        # graveyard files on its behalf, it only writes its own pack.
        silent_assertions, err = _attempt(assertions.run_all, graveyard_path)
        if err:
            degraded.append(f'silent_assertions unavailable: {err}')
        graveyard_summary, err = _attempt(summarize_graveyard.summarize, graveyard_path)
        if err:
            degraded.append(f'graveyard_summary (and distinct_findings) unavailable: {err}')

    strategy_rows = _per_strategy_rows(entries)
    asset_breakdown = asset_class_analysis.analyze(entries, strategy_filter=strategy)
    expected_best_by_chance = _expected_best_by_chance(strategy_rows, graveyard_summary)

    if status == 'PROVISIONAL':
        for row in strategy_rows:
            row['status'] = 'PROVISIONAL'
    else:
        for row in strategy_rows:
            row['status'] = 'DURABLE'

    return {
        'status': status,
        'harness_validated': harness_validated,
        'graveyard': os.path.basename(graveyard_path),
        'strategy_filter': strategy,
        'entries_total': len(entries),
        'silent_assertions': silent_assertions,
        'degraded': degraded or None,
        'distinct_findings': (graveyard_summary or {}).get('distinct_findings'),
        'graveyard_summary': graveyard_summary,
        'strategies': strategy_rows,
        'asset_class_breakdown': asset_breakdown,
        'expected_best_by_chance': expected_best_by_chance,
    }


def emit_evidence_pack(graveyard_path: str, output_path: str,
                       strategy: Optional[str] = None,
                       validation_fn: Optional[Callable[[], bool]] = None) -> dict:
    pack = build_evidence_pack(graveyard_path, strategy=strategy, validation_fn=validation_fn)
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(pack, f, indent=2)
    return pack


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Judge: read-only evidence-pack emitter over an existing graveyard.')
    parser.add_argument('--graveyard', required=True,
                        help='path to a graveyard JSON, e.g. research/graveyard/v0_graveyard_full.json')
    parser.add_argument('--out', required=True,
                        help='output path for the evidence-pack JSON, e.g. research/judge_evidence_pack.json')
    parser.add_argument('--strategy', default=None, help='filter to one strategy name')
    args = parser.parse_args()

    pack = emit_evidence_pack(args.graveyard, args.out, strategy=args.strategy)
    print(f"status: {pack['status']}  entries: {pack.get('entries_total', 0)}  "
         f"strategies: {len(pack.get('strategies', []))}")
    print(f"saved: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
