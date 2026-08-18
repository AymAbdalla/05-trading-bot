"""Forge: turns graveyard evidence into structured strategy proposals.

Implements the Forge role (agents/forge/SOUL.md, agents/forge/forge.agent.md)
as the deterministic half of the loop. The LLM half writes the argument; this
module does the parts that must not drift: loading the evidence, computing the
gaps, enforcing the proposal schema, and refusing anything that violates a
convention.

Forge proposes. Forge does not build and does not grade. This module therefore
writes ONLY under `strategies/proposals/` plus its own run log. It never writes
to `strategies/builtin/`, `strategies/polymarket/`, `engine/`, `backtest/`, or
any graveyard file, and it never runs a sweep.

Conventions enforced here rather than trusted to a reader:
  5.  gross edge under 30bps is dead on arrival, so it is refused at write time
  6.  every proposal states a kill condition
  11. NOT_TESTED is never mined as evidence of failure, and a repair's unknown
      edge is recorded as null rather than invented as a number
  19. json.dump(allow_nan=False) so a non-finite raises here, not downstream
  20. every skip is counted AND categorised, and the accounting identity is
      asserted: len(candidates) - refused == written
"""
import argparse
import collections
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROPOSALS_DIR = os.path.join(ROOT, 'strategies', 'proposals')
RUN_LOG = os.path.join(PROPOSALS_DIR, 'forge_runs.jsonl')

SUMMARY_PATH = os.path.join(ROOT, 'research', 'graveyard', 'summary.json')
EVIDENCE_PATH = os.path.join(ROOT, 'research', 'judge_evidence_pack.json')
POOLED_PATH = os.path.join(ROOT, 'research', 'graveyard', 'pooled.json')

# Convention 5. Stated once, here, so that raising it later is a one-line
# change with a blast radius you can see rather than a constant sprinkled
# through five proposals. Convention 17: this is an assumption with an expiry
# date, and the expiry is "when the cost model says a 30bps gross edge no
# longer nets positive."
MIN_GROSS_EDGE_BPS = 30

# Below this fraction of rows producing at least one trade, a strategy is not
# "performing badly" - it is not running. Convention 11 territory.
NON_FIRING_TRADE_ROW_FRACTION = 0.01

REQUIRED_FIELDS = (
    'name',
    'thesis',
    'expected_edge_bps',
    'kill_condition',
    'asset_class',
    'entry_exit_rules',
    'data_requirements',
    'related_graveyard_findings',
)

# `expected_edge_bps` is the one required field allowed to be null, and only
# for a repair (see KINDS). Everything else must be non-empty.
NULLABLE_FIELDS = ('expected_edge_bps',)

# MULTI is legitimate ONLY for a repair spanning several classes, e.g. the nine
# non-firing strategies, which sit across CRYPTO, EQUITY and FUTURES. An edge
# hypothesis that cannot name one class has not been thought through.
VALID_ASSET_CLASSES = (
    'CRYPTO', 'EQUITY', 'ETF', 'FUTURES', 'OPTIONS', 'PREDICTION_MARKET',
    'MULTI',
)

# An edge hypothesis claims a new inefficiency and must clear the 30bps floor.
# A repair fixes a strategy that does not currently run: its edge is genuinely
# UNKNOWN until it fires, and convention 11 says unknown is not zero. Forcing a
# repair to invent a bps number would put a fabricated figure into the record,
# so the schema requires it to be null instead.
KINDS = ('edge_hypothesis', 'repair')


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Read a JSON file. Returns (value, error_or_None).

    Convention 11 applies to the evidence layer too: an unreadable file is not
    an empty one. The caller gets an error string it must surface, never a
    silent `{}` that would make every gap look like a real gap.
    """
    try:
        with open(path) as fh:
            return json.load(fh), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f'{type(exc).__name__}: {exc}'


def load_evidence() -> Dict[str, Any]:
    """Load the three evidence sources Forge is allowed to reason from."""
    summary, summary_err = _load_json(SUMMARY_PATH)
    evidence, evidence_err = _load_json(EVIDENCE_PATH)
    pooled, pooled_err = _load_json(POOLED_PATH)
    return {
        'summary': summary,
        'evidence': evidence,
        'pooled': pooled,
        'errors': {k: v for k, v in (
            ('summary', summary_err),
            ('judge_evidence_pack', evidence_err),
            ('pooled', pooled_err),
        ) if v},
    }


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def find_non_firing(evidence: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strategies that produced (almost) no trades across every row tested.

    A strategy with zero trades did not fail. It did not run. Reported
    separately from performance so nobody reads it as a verdict.
    """
    if not evidence:
        return []
    out = []
    for rec in evidence.get('strategies', []):
        tested = rec.get('n_rows_tested') or 0
        trades = rec.get('n_trades') or 0
        if tested <= 0:
            continue
        # We have trade COUNT, not per-row firing, so the ratio below is a
        # proxy: fewer trades than 1% of the rows tested means the strategy
        # cannot have fired on more than 1% of them.
        if trades <= tested * NON_FIRING_TRADE_ROW_FRACTION:
            out.append({
                'strategy': rec.get('strategy'),
                'n_trades': trades,
                'n_rows_tested': tested,
                'n_rows_not_tested': rec.get('n_rows_not_tested'),
                'asset_class': rec.get('asset_class'),
            })
    return sorted(out, key=lambda r: (r['n_trades'], r['strategy'] or ''))


def find_asset_class_gaps(evidence: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Which asset classes carry coverage, and which carry none.

    PREDICTION_MARKET will show as absent until the Polymarket harness has
    actually swept. That absence is the gap, and it is NOT_TESTED (D-268), not
    a negative result.
    """
    classes = [c for c in VALID_ASSET_CLASSES if c != 'MULTI']
    if not evidence:
        return {'covered': {}, 'absent': classes}
    covered: Dict[str, Dict[str, Any]] = {}
    for row in evidence.get('asset_class_breakdown', []):
        cls = row.get('class')
        if not cls:
            continue
        agg = covered.setdefault(cls, {'strategies': 0, 'trades': 0})
        agg['strategies'] += 1
        agg['trades'] += row.get('trades') or 0
    absent = [c for c in classes if c not in covered]
    return {'covered': covered, 'absent': absent}


def find_pooled_losers(pooled: Optional[Dict[str, Any]],
                       limit: int = 10) -> List[Dict[str, Any]]:
    """Judgeable strategies with the worst pooled pnl per trade.

    Only `judgeable` rows: pooling a strategy under the minimum trade count
    produces a number with no power behind it, and convention 7 says a verdict
    on a thin sample is a shrug in both directions.
    """
    if not pooled:
        return []
    rows = [r for r in pooled.get('by_strategy', []) if r.get('judgeable')]
    rows.sort(key=lambda r: r.get('pnl_per_trade') if r.get('pnl_per_trade')
              is not None else 0.0)
    return rows[:limit]


def analyse_gaps(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """The full gap picture Forge proposes against."""
    evidence = bundle.get('evidence')
    summary = bundle.get('summary')
    pooled = bundle.get('pooled')

    known: List[str] = []
    if evidence:
        known = [r.get('strategy') for r in evidence.get('strategies', [])
                 if r.get('strategy')]

    gaps: Dict[str, Any] = {
        'evidence_errors': bundle.get('errors', {}),
        'known_strategies': sorted(known),
        'non_firing': find_non_firing(evidence),
        'asset_classes': find_asset_class_gaps(evidence),
        'worst_pooled': find_pooled_losers(pooled),
        'failed_assertions': (
            (evidence or {}).get('silent_assertions', {}).get('failed', [])),
    }

    if summary:
        gaps['distinct_findings'] = summary.get('distinct_findings')
        gaps['not_tested_breakdown'] = summary.get('not_tested_breakdown')
        gaps['tested_rows_with_trades'] = summary.get('tested_rows_with_trades')
    return gaps


# ---------------------------------------------------------------------------
# Proposal validation and rendering
# ---------------------------------------------------------------------------

class ProposalRefused(Exception):
    """A candidate that must not be written, carrying its category."""

    def __init__(self, category: str, detail: str):
        super().__init__(f'{category}: {detail}')
        self.category = category
        self.detail = detail


def validate(candidate: Dict[str, Any], known_strategies: List[str]) -> None:
    """Refuse anything that breaks the schema or a convention.

    Raises ProposalRefused with a CATEGORY, never a bare False and never a
    silent skip. Convention 20: the caller counts these by category.
    """
    kind = candidate.get('kind', 'edge_hypothesis')
    if kind not in KINDS:
        raise ProposalRefused('unknown_kind', str(kind))

    missing = [f for f in REQUIRED_FIELDS
               if f not in candidate
               or (not candidate[f] and f not in NULLABLE_FIELDS)]
    if missing:
        raise ProposalRefused('missing_fields', ','.join(missing))

    name = candidate['name']
    if name in known_strategies:
        raise ProposalRefused(
            'duplicate_of_graveyard_entry',
            f'{name} is already a swept strategy')

    cls = candidate['asset_class']
    if cls not in VALID_ASSET_CLASSES:
        raise ProposalRefused('unknown_asset_class', str(cls))
    if cls == 'MULTI' and kind != 'repair':
        raise ProposalRefused(
            'multi_class_edge_hypothesis',
            'MULTI is only valid for a repair spanning classes')

    bps = candidate['expected_edge_bps']
    if kind == 'repair':
        # The edge of a strategy that has never fired is not knowable. Recording
        # it as null keeps a fabricated number out of the file (convention 11).
        if bps is not None:
            raise ProposalRefused(
                'repair_claims_an_edge',
                f'repair must record expected_edge_bps as null, got {bps!r}')
    else:
        if not isinstance(bps, (int, float)) or isinstance(bps, bool):
            raise ProposalRefused('non_numeric_edge_estimate', repr(bps))
        if not math.isfinite(bps):
            # Convention 19: a non-finite would serialise into a file that
            # json.loads accepts and every other parser rejects.
            raise ProposalRefused('non_finite_edge_estimate', repr(bps))
        if bps < MIN_GROSS_EDGE_BPS:
            raise ProposalRefused(
                'below_min_edge_bps',
                f'{bps}bps < {MIN_GROSS_EDGE_BPS}bps floor')

    # Convention 6. A kill condition that names no measurement is a sentence,
    # not a kill condition, so require a digit somewhere in it.
    kill = str(candidate['kill_condition'])
    if not any(ch.isdigit() for ch in kill):
        raise ProposalRefused(
            'unmeasurable_kill_condition',
            'kill condition states no threshold')


def _yaml_scalar(value: Any) -> str:
    """Render one frontmatter value. Block scalars for anything multi-line."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if '\n' in text:
        body = '\n'.join('  ' + line for line in text.rstrip().split('\n'))
        return '|\n' + body
    # Quote defensively: a colon or a leading % in a bare scalar is a parse
    # error in strict YAML even though many loaders tolerate it.
    return json.dumps(text)


def render(candidate: Dict[str, Any]) -> str:
    """Proposal document: YAML frontmatter contract, markdown argument."""
    lines = ['---']
    for field in REQUIRED_FIELDS:
        lines.append(f'{field}: {_yaml_scalar(candidate[field])}')
    lines.append(f"kind: {candidate.get('kind', 'edge_hypothesis')}")
    lines.append(f"status: {candidate.get('status', 'PROPOSED')}")
    lines.append(f"source: {_yaml_scalar(candidate.get('source', 'forge'))}")
    lines.append('---')
    lines.append('')
    lines.append(candidate.get('body', '').rstrip())
    lines.append('')
    return '\n'.join(lines)


def write_proposal(candidate: Dict[str, Any], index: int) -> str:
    """Write one proposal and return its path."""
    slug = candidate['name'].replace('_', '-')
    path = os.path.join(PROPOSALS_DIR, f'{index:03d}-{slug}.md')
    with open(path, 'w') as fh:
        fh.write(render(candidate))
    return path


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def generate(candidates: List[Dict[str, Any]],
             gaps: Dict[str, Any],
             start_index: int = 1) -> Dict[str, Any]:
    """Validate and write every candidate. Returns the run record.

    Convention 20: refusals are counted BY CATEGORY, and the accounting
    identity is asserted rather than assumed. A proposal that vanished between
    the candidate list and the output directory is a missing number.
    """
    known = gaps.get('known_strategies', [])
    written: List[Dict[str, Any]] = []
    refused: List[Dict[str, str]] = []

    index = start_index
    for candidate in candidates:
        try:
            validate(candidate, known)
        except ProposalRefused as exc:
            refused.append({
                'name': str(candidate.get('name')),
                'category': exc.category,
                'detail': exc.detail,
            })
            continue
        path = write_proposal(candidate, index)
        written.append({
            'name': candidate['name'],
            'path': os.path.relpath(path, ROOT),
            'kind': candidate.get('kind', 'edge_hypothesis'),
            'asset_class': candidate['asset_class'],
            'expected_edge_bps': candidate['expected_edge_bps'],
        })
        index += 1

    by_category = collections.Counter(r['category'] for r in refused)

    assert len(candidates) - len(refused) == len(written), (
        f'accounting identity broken: {len(candidates)} candidates - '
        f'{len(refused)} refused != {len(written)} written')

    return {
        'candidates_screened': len(candidates),
        'written': written,
        'refused': refused,
        'refused_by_category': dict(by_category),
        'evidence_errors': gaps.get('evidence_errors', {}),
        'gaps_used': {
            'non_firing_count': len(gaps.get('non_firing', [])),
            'asset_classes_absent': gaps.get('asset_classes', {}).get('absent'),
            'failed_assertions': gaps.get('failed_assertions'),
            'distinct_findings': gaps.get('distinct_findings'),
        },
    }


def log_run(record: Dict[str, Any]) -> None:
    """Append the run record. Convention 19: allow_nan=False raises here."""
    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    with open(RUN_LOG, 'a') as fh:
        fh.write(json.dumps(record, allow_nan=False, sort_keys=True) + '\n')


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gaps-only', action='store_true',
                        help='print the gap analysis and write nothing')
    parser.add_argument('--start-index', type=int, default=1,
                        help='first proposal number (default 1)')
    args = parser.parse_args(argv)

    bundle = load_evidence()
    gaps = analyse_gaps(bundle)

    if gaps['evidence_errors']:
        # Not fatal: Forge can still write a proposal that does not lean on the
        # unreadable source. It IS reported, because a gap computed from a file
        # that failed to load is not a gap (convention 11).
        for source, err in gaps['evidence_errors'].items():
            print(f'WARN unreadable evidence source {source}: {err}',
                  file=sys.stderr)

    if args.gaps_only:
        print(json.dumps(gaps, indent=2, allow_nan=False, sort_keys=True))
        return 0

    from agents.forge_candidates import CANDIDATES

    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    record = generate(CANDIDATES, gaps, start_index=args.start_index)
    log_run(record)

    print(f"screened {record['candidates_screened']}, "
          f"wrote {len(record['written'])}, "
          f"refused {len(record['refused'])}")
    for row in record['written']:
        bps = ('unknown' if row['expected_edge_bps'] is None
               else f"~{row['expected_edge_bps']}bps")
        print(f"  WROTE   {row['path']} ({row['asset_class']}, {bps})")
    for row in record['refused']:
        print(f"  REFUSED {row['name']}: {row['category']} ({row['detail']})")
    return 0


if __name__ == '__main__':
    sys.path.insert(0, ROOT)
    raise SystemExit(main())
