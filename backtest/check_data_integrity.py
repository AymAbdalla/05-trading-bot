"""Scan every CSV in backtest/data for corruption (unadjusted splits, bad rows).

Writes backtest/data/INTEGRITY_REPORT.md and, with --quarantine, moves files
showing probable split gaps into backtest/data/quarantine/ so no backtest can
consume them until they are re-downloaded with a consistent adjustment
convention (adjusted prices everywhere: yfinance auto_adjust=True, Alpaca
adjustment='all').
"""
import os
import shutil
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_loader import load_csv, check_integrity

logging.basicConfig(level=logging.WARNING)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
QUARANTINE_DIR = os.path.join(DATA_DIR, 'quarantine')
REPORT_PATH = os.path.join(DATA_DIR, 'INTEGRITY_REPORT.md')


def main(quarantine: bool = False):
    flagged = {}
    clean = 0
    unreadable = []

    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith('.csv'))
    for fname in files:
        candles = load_csv(os.path.join(DATA_DIR, fname))
        if len(candles) < 10:
            unreadable.append(fname)
            continue
        gaps = [p for p in check_integrity(candles, fname) if 'gap' in p]
        if gaps:
            flagged[fname] = gaps
        else:
            clean += 1

    lines = [
        '# Data Integrity Report',
        '',
        f'Files scanned: {len(files)} | clean: {clean} | '
        f'flagged (probable unadjusted splits / bad rows): {len(flagged)} | '
        f'unreadable/too short: {len(unreadable)}',
        '',
        'Flagged files must NOT be used in backtests until re-downloaded with',
        "adjusted prices (yfinance auto_adjust=True or Alpaca adjustment='all').",
        '',
    ]
    for fname, gaps in sorted(flagged.items()):
        lines.append(f'## {fname}')
        for g in gaps[:10]:
            lines.append(f'- {g}')
        if len(gaps) > 10:
            lines.append(f'- ... and {len(gaps) - 10} more')
        lines.append('')
    if unreadable:
        lines.append('## Unreadable / too short (not candle data or corrupt)')
        for fname in unreadable:
            lines.append(f'- {fname}')

    with open(REPORT_PATH, 'w') as f:
        f.write('\n'.join(lines))
    print(f'Report: {REPORT_PATH}')
    print(f'Scanned {len(files)}: {clean} clean, {len(flagged)} flagged, {len(unreadable)} unreadable')

    if quarantine and flagged:
        os.makedirs(QUARANTINE_DIR, exist_ok=True)
        for fname in flagged:
            shutil.move(os.path.join(DATA_DIR, fname), os.path.join(QUARANTINE_DIR, fname))
        print(f'Moved {len(flagged)} flagged files to {QUARANTINE_DIR}')


if __name__ == '__main__':
    main(quarantine='--quarantine' in sys.argv)
