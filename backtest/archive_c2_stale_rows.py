#!/usr/bin/env python3
"""R-009 / D-275: archive the stale C2 graveyard rows instead of deleting them.

The rows carry `not_tested_reason == "needs 840 bars, scan window is 260"`.
That string does not exist anywhere in the current codebase - the current gate
(`vectorized_harness.py`) emits `"needs 840 bars, series has {n}"` - so these
rows were written by a pre-fix harness and C2 has never run under current
code. Raven ruled ARCHIVE, not delete: an unreadable graveyard is not an empty
one (convention 11 / D-255), and the audit trail is the point.

Reads the 389MB graveyard object-by-object via the streaming reader in
`snapshot_graveyard.py` rather than `json.load`ing it into RAM.

This script does NOT rewrite the active graveyard. A concurrent re-sweep
regenerates it from scratch, and rewriting a file another process is writing
is how you lose both copies (convention 21).

Usage:
  env -u PYTHONPATH python3 backtest/archive_c2_stale_rows.py [source.json]
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.snapshot_graveyard import iter_entries  # noqa: E402

DEFAULT_SOURCE = ROOT / 'research' / 'graveyard' / 'v0_graveyard_full.json'
OUT = ROOT / 'research' / 'graveyard' / 'archive' / 'c2_stale_rows.json'

STALE_REASON = 'needs 840 bars, scan window is 260'
CURRENT_REASON_PREFIX = 'needs 840 bars, series has'


def _reason(entry):
    return entry.get('not_tested_reason') or entry.get('reason') or ''


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT

    stale = []
    scanned = 0
    c2_rows = 0
    c2_current_gate = 0
    c2_other = 0
    non_c2 = 0
    c2_other_reasons = Counter()

    for e in iter_entries(source):
        scanned += 1
        if e.get('strategy') != 'C2':
            non_c2 += 1
            continue
        c2_rows += 1
        r = _reason(e)
        if r == STALE_REASON:
            stale.append(e)
        elif r.startswith(CURRENT_REASON_PREFIX):
            c2_current_gate += 1
        else:
            c2_other += 1
            c2_other_reasons[r or '<no reason>'] += 1

    # Convention 20: every skip is counted AND categorised, no two drop causes
    # share a number, and the accounting identity is asserted rather than
    # assumed. A silent `continue` in a filter loop is a missing number.
    assert c2_rows == len(stale) + c2_current_gate + c2_other, (
        f'C2 partition broken: {c2_rows} != {len(stale)} + '
        f'{c2_current_gate} + {c2_other}')
    assert scanned == c2_rows + non_c2, (
        f'row partition broken: {scanned} != {c2_rows} + {non_c2}')

    payload = {
        'archived_by': 'backtest/archive_c2_stale_rows.py',
        'decision': 'D-275 (supersedes D-272 part 2), Raven ruling R-009',
        'source_file': str(source),
        'stale_reason_string': STALE_REASON,
        'why_stale': (
            'This reason string does not exist in the current codebase. The '
            'current gate emits "needs 840 bars, series has {n}". These rows '
            'were written by a pre-fix harness that refused to widen the '
            'scan window; scan_all_bars now widens to max(SCAN_WINDOW, '
            'min_bars). C2 has never run under current code.'),
        'accounting': {
            'rows_scanned': scanned,
            'non_c2_rows': non_c2,
            'c2_rows': c2_rows,
            'c2_stale_archived': len(stale),
            'c2_current_gate_reason': c2_current_gate,
            'c2_other': c2_other,
            'c2_other_reasons': dict(c2_other_reasons.most_common(10)),
        },
        'entries': stale,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        # allow_nan=False: json.loads is not strict and would happily accept
        # Infinity/NaN back, but no other JSON parser will (convention 19).
        json.dump(payload, f, indent=2, allow_nan=False)

    print(json.dumps(payload['accounting'], indent=2))
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
