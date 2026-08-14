"""Backtest report generator.

Produces human-readable reports from backtest results.
Includes go/no-go verdicts, stress probe summaries, and walk-forward averages.
"""
import json
import logging
from typing import Dict, List
from pathlib import Path

from backtest.harness import BacktestResult

logger = logging.getLogger(__name__)


def _fmt_pf(value) -> str:
    """PF fields are None when infinite (zero losing trades)."""
    if value is None:
        return 'inf*'
    return f'{value:.2f}'


def generate_report(results: Dict[str, BacktestResult],
                    pair: str, period_label: str = '') -> str:
    """Generate a text report from backtest results."""
    lines = []
    lines.append(f"# Backtest Report: {pair}")
    if period_label:
        lines.append(f"Period: {period_label}")
    lines.append("")
    lines.append("| Strategy | Trades | Win% | PF | Expectancy | Return% | BH% | Twin PF | Beats BH | Beats Twin |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for name, result in results.items():
        r = result.to_report()
        lines.append(
            f"| {name} | {r['trade_count']} | {r['win_rate']*100:.1f}% | "
            f"{_fmt_pf(r['profit_factor'])} | {r['expectancy']:.4f} | "
            f"{r['strategy_return_pct']:.2f}% | {r['buy_hold_return_pct']:.2f}% | "
            f"{_fmt_pf(r['random_twin_pf'])} | "
            f"{'YES' if r['beats_buy_hold'] else 'NO'} | "
            f"{'YES' if r['beats_random_twin'] else 'NO'} |"
        )

    lines.append("")
    lines.append("## Go/No-Go Verdicts")

    return '\n'.join(lines)


def generate_full_report(
    test_results: Dict[str, BacktestResult],
    walk_forward_results: Dict[str, List[BacktestResult]],
    stress_results: Dict[str, Dict[str, BacktestResult]],
    pair: str,
    go_no_go: Dict[str, dict],
    output_path: str = None,
) -> str:
    """Generate a complete backtest report with all sections."""
    lines = []
    lines.append(f"# T7 Backtest Report: {pair}")
    lines.append(f"Generated: {__import__('datetime').datetime.now().isoformat()}")
    lines.append("")

    # Go/No-Go summary
    lines.append("## Go/No-Go Summary")
    passed = sum(1 for v in go_no_go.values() if v['pass'])
    failed = sum(1 for v in go_no_go.values() if not v['pass'])
    lines.append(f"Passed: {passed} / {len(go_no_go)}")
    lines.append(f"Failed: {failed} / {len(go_no_go)}")
    lines.append("")

    for name, verdict in go_no_go.items():
        status = 'PASS' if verdict['pass'] else 'FAIL'
        lines.append(f"- **{name}**: {status} - {'; '.join(verdict['reasons'])}")
    lines.append("")

    # Test set results
    lines.append("## Test Set Results (Holdout)")
    lines.append(generate_report(test_results, pair, 'test holdout (20%)'))
    lines.append("")

    # Walk-forward
    if walk_forward_results:
        lines.append("## Walk-Forward Validation")
        lines.append("| Strategy | Windows | Avg PF | Avg Return% | Avg Trades |")
        lines.append("|---|---|---|---|---|")

        for name, wf_results in walk_forward_results.items():
            if wf_results:
                avg_pf = sum(r.profit_factor for r in wf_results if r.profit_factor != float('inf')) / len(wf_results)
                avg_ret = sum(r.strategy_return_pct for r in wf_results) / len(wf_results)
                avg_trades = sum(r.trade_count for r in wf_results) / len(wf_results)
                lines.append(f"| {name} | {len(wf_results)} | {avg_pf:.2f} | {avg_ret:.2f}% | {avg_trades:.1f} |")

        lines.append("")

    # Stress probes
    if stress_results:
        lines.append("## Stress Probe Results")
        for strategy_name, probes in stress_results.items():
            lines.append(f"### {strategy_name}")
            lines.append("| Probe | PF | Return% | Trades |")
            lines.append("|---|---|---|---|")

            for probe_name, result in probes.items():
                r = result.to_report()
                lines.append(f"| {probe_name} | {_fmt_pf(r['profit_factor'])} | {r['strategy_return_pct']:.2f}% | {r['trade_count']} |")

            lines.append("")

    report = '\n'.join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(report)
        logger.info(f"Report saved to {output_path}")

    return report
