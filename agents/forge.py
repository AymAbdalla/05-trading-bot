"""Forge: turns evidence into structured strategy proposals.

Implements the Forge role (agents/forge/SOUL.md, agents/forge/forge.agent.md)
as the deterministic half of the loop. The LLM half writes the argument; this
module does the parts that must not drift: loading the evidence, computing the
gaps, enforcing what little of the proposal schema still binds, and refusing
anything that would put a false or unfalsifiable number into the record.

Evidence comes from three places now:
  - the graveyard (backtest): summary.json, judge_evidence_pack.json, pooled
  - the shadow loop (live paper): db/trading.db, via agents/forge_shadow_eval.py
  - the Obsidian vault (durable memory between cycles): lessons, blowup
    reports, strategy cards and cycle summaries, via agents/vault_reader.py

## Where the CANDIDATES come from (two paths, one validator)

By default the candidate list is `agents/forge_candidates.py`, hand written and
rewritten each cycle. With `--reasoner` it is instead an Opus turn, run by
`agents/forge_reasoner.py`, which reads the same evidence plus the hypothesis
graph and the vault and returns JSON candidates.

The model does NOT write the proposal files. It returns candidates and they go
through the SAME `validate()` and `write_proposal()` path as a hand written
one. That is the whole point: this module's contract is that the deterministic
half enforces the schema and refuses anything unfalsifiable, and that contract
only holds while Python holds the pen. See `agents/forge_reasoner.py` for the
longer argument.

When the reasoner CANNOT RUN, the run log records NOT_TESTED (convention 11)
and the deterministic candidate list is used instead. It never crashes and it
never writes zero proposals without saying why.

Forge proposes. Forge does not build and does not grade. This module therefore
writes ONLY under `strategies/proposals/` plus its own run log. It never writes
to `strategies/builtin/`, `strategies/polymarket/`, `engine/`, `backtest/`, or
any graveyard file, and it never runs a sweep.

## The creative mandate (Aym, 2026-08-17)

Verbatim: "forge can be as creative as they want to be on these strats let's
have fun with it and don't be so controlling on what he is allowed to make."

So the refusals that policed TASTE are gone. What is left refuses only things
that would put a false or unfalsifiable number into the record:

  KEPT as refusals    missing core fields; a kill condition with no number or
                      no named harness; an edge estimate that is not a finite
                      number when one is required; an edge below the
                      INSTRUMENT'S floor; a repair or experiment claiming an
                      edge it cannot know.
  DOWNGRADED to       duplicating a graveyard name; MULTI asset class on a
  WARNINGS            non-repair; an asset class outside the known list; no
                      related graveyard finding.

A warning is printed, counted by category in the run log, and written onto the
proposal document itself, so nothing that used to be a refusal becomes
invisible. Convention 20 applies to the retirement too: the retired categories
stay in the counter schema at zero rather than silently disappearing.

Conventions enforced here rather than trusted to a reader:
  5.  gross edge below the instrument's floor is dead on arrival, refused at
      write time (see MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS)
  6.  every proposal states a kill condition with a NUMBER and a NAMED HARNESS.
      This is the one hard content constraint that survives.
  11. NOT_TESTED is never mined as evidence of failure, and a repair's unknown
      edge is recorded as null rather than invented as a number
  19. json.dump(allow_nan=False) so a non-finite raises here, not downstream
  20. every skip is counted AND categorised, and the accounting identities are
      asserted: screened - refused == written, and the per-category counts sum
      back to the totals
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

# Convention 5, made instrument-aware.
#
# bps is a RATIO and the denominator is not the same instrument to instrument,
# so one number cannot serve both. On crypto/equity spot the denominator is
# notional and the round-trip cost floor is roughly 22bps, so a 30bps floor is
# "clears costs with a little room". On a Polymarket binary the denominator is
# the PREMIUM, quoted in cents. So on a binary the floor is set to ONE TICK of
# a mid-priced contract, which is the smallest edge that can physically exist
# there.
#
# D-336: the tick used to derive the binary floor was assumed to be 1c. It is
# not. The live tape (9,033 non-null best_ask observations) sits on a 0.001
# grid: only 14.7% land on the 0.01 grid, the rest need the finer grid to be
# represented at all. The real tick is 0.1c, not 1c, so the floor derived from
# it is 20bps, not 200, and is now BELOW the 30bps spot floor rather than
# ~6.7x above it - a binary edge can be smaller than a spot edge and
# still be real, because the binary's denominator (premium) is smaller.
#
# Convention 17: both numbers are assumptions with expiry dates. The spot floor
# expires when the cost model says 30bps no longer nets positive; the binary
# floor expires if the venue's OBSERVED quoting grid changes (D-336: the grid
# is a measurement off the tape, not a documented venue constant, so it gets
# re-checked rather than trusted).
MIN_GROSS_EDGE_BPS = 30

MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS: Dict[str, int] = {
    # Observed tick 0.001 (a TENTH of a cent, measured off the live tape) on a
    # 50c premium: 0.001 / 0.50 = 20bps (D-336). One tick is the floor of what
    # is expressible, so anything under it is not a small edge, it is not an
    # edge.
    #
    # The tick is a VENUE property, not an asset-class property. EVENT and
    # SPORTS have no tape of their own yet, so they inherit the Polymarket
    # venue tick here and are FLAGGED FOR RE-CONFIRMATION the moment either of
    # them has a tape to measure.
    'PREDICTION_MARKET': 20,
    'EVENT': 20,
    'SPORTS': 20,
}


def min_edge_bps_for(asset_class: Optional[str]) -> int:
    """The gross-edge floor for one instrument class."""
    return MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS.get(
        str(asset_class), MIN_GROSS_EDGE_BPS)


# Below this fraction of rows producing at least one trade, a strategy is not
# "performing badly" - it is not running. Convention 11 territory.
NON_FIRING_TRADE_ROW_FRACTION = 0.01

# Convention 6, and the ONE hard content constraint left. A kill condition must
# name the thing that would score it, otherwise it is a sentence about the
# future rather than a measurement anyone can take. Matched case-insensitively
# as a substring so "the vectorized harness" and "backtest/polymarket_harness.py"
# both pass. Add to this list when a new scorer lands; a scorer that is not
# here will read as unnamed, which is a loud failure rather than a quiet one.
KNOWN_SCORERS = (
    'harness',                    # covers *_harness.py and "the X harness"
    'run_incremental_graveyard',
    'run_full_graveyard',
    'run_vectorized_graveyard',
    'run_go_nogo',
    'run_fast_gonogo',
    'run_horizon_ladder',
    'run_inversions',
    'constraint_sweep',
    'conditional_edge',
    'dispersion_gate',
    'pooled_analysis',
    'cross_sectional',
    'judge.py',
    'forge_shadow_eval',
    'shadow_loop',
)

REQUIRED_FIELDS = (
    'name',
    'thesis',
    'expected_edge_bps',
    'kill_condition',
    'asset_class',
    'entry_exit_rules',
    'data_requirements',
)

# Was required, now optional. A Polymarket binary, an event market or a sports
# market has NO graveyard analogue - the graveyard is crypto spot and perp - so
# demanding one forced either a fabricated link or a refusal. Absence is now a
# warning, and the field is still rendered when supplied.
OPTIONAL_FIELDS = (
    'related_graveyard_findings',
    'markets',
)

# `expected_edge_bps` is the one required field allowed to be null, and only
# for the kinds in NULL_EDGE_KINDS. Everything else must be non-empty.
NULLABLE_FIELDS = ('expected_edge_bps',)

# Expanded well past the graveyard's four classes, because "propose only in
# classes we have already swept" is exactly the constraint the creative mandate
# removes. An unlisted class is now a WARNING, not a refusal, so this list is a
# vocabulary rather than a fence.
VALID_ASSET_CLASSES = (
    'CRYPTO', 'EQUITY', 'ETF', 'FUTURES', 'OPTIONS', 'PREDICTION_MARKET',
    'EVENT', 'SPORTS', 'FX', 'COMMODITY', 'RATES', 'MULTI',
)

# edge_hypothesis  claims an inefficiency and must put a number on it.
# combination      two or more concepts wired together; same evidentiary bar as
#                  an edge_hypothesis, because a combination that cannot state
#                  a combined edge has not been thought through as one strategy.
# repair           fixes a strategy that does not currently run.
# experiment       a deliberate probe run to FIND OUT whether an edge exists.
#
# repair and experiment must record expected_edge_bps as null. Not to restrict
# them: the opposite. It lets them exist without inventing a bps figure, which
# is what the old schema forced. Convention 11: unknown is not zero, and a
# fabricated number gets cited.
KINDS = ('edge_hypothesis', 'combination', 'repair', 'experiment')
NULL_EDGE_KINDS = ('repair', 'experiment')

# Convention 20. These still fire and still block.
REFUSAL_CATEGORIES = (
    'unknown_kind',
    'missing_fields',
    'unmeasurable_kill_condition',
    'kill_condition_names_no_harness',
    'non_numeric_edge_estimate',
    'non_finite_edge_estimate',
    'below_min_edge_bps',
    'unknowable_edge_claimed',
)

# Convention 20 again: a retired category does not vanish from the schema. It
# stays here, is reported at zero refusals, and its information now arrives as
# the warning named in the value.
RETIRED_REFUSAL_CATEGORIES: Dict[str, str] = {
    # The graveyard is crypto spot/perp. A Polymarket binary that shares a name
    # with a buried strategy is a different instrument with a different payoff,
    # so the duplicate check was a false positive there. Kept as information.
    'duplicate_of_graveyard_entry': 'duplicate_name_warning',
    # Multi-concept combinations are now the point, not a schema violation.
    'multi_class_edge_hypothesis': 'multi_class_warning',
    # A class we have never swept is a gap, not an error.
    'unknown_asset_class': 'unlisted_asset_class_warning',
    # See OPTIONAL_FIELDS.
    'missing_related_graveyard_findings': 'no_graveyard_link_warning',
}

WARNING_CATEGORIES = tuple(sorted(set(RETIRED_REFUSAL_CATEGORIES.values())))


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
    """Load the three graveyard sources Forge is allowed to reason from."""
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


def load_vault_summary(budget_chars: Optional[int] = None) -> Dict[str, Any]:
    """The Obsidian vault, summarised for the gap picture.

    A SUMMARY, not the notes themselves: this ends up in `--gaps-only` output
    and in the run log, and pasting 60k chars of lessons into both would make
    neither readable. The full rendered text goes to the reasoning prompt via
    `agents/forge_reasoner.py`, which budgets it explicitly.

    Convention 11 at the evidence layer: a vault that could not be read is
    status='unreadable' with the error, never a clean zero that would read as
    "no lessons have been written".
    """
    try:
        from agents import vault_reader
    except ImportError as exc:  # pragma: no cover - the module ships with this
        return {'status': 'unavailable', 'error': f'{type(exc).__name__}: {exc}'}
    try:
        kwargs = {} if budget_chars is None else {'budget_chars': budget_chars}
        context = vault_reader.load_context(**kwargs)
        return {
            'status': 'ok',
            'vault_root': context['vault_root'],
            'total_notes': context['total_notes'],
            'total_chars': context['total_chars'],
            'total_dropped': context['total_dropped'],
            'notes_by_section': {
                key: [note.name for note in section['notes']]
                for key, section in context['sections'].items()},
            'known_failure_modes': vault_reader.known_failure_modes(context),
        }
    except OSError as exc:
        return {'status': 'unreadable',
                'error': f'{type(exc).__name__}: {exc}',
                'note': 'NOT_TESTED, not empty. Convention 11.'}


def analyse_gaps(bundle: Dict[str, Any],
                 include_vault: bool = True) -> Dict[str, Any]:
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

    if include_vault:
        # The durable memory between cycles. A proposal that repeats a lesson
        # already written into the vault should be refused by argument, and
        # that is only possible if Forge has read the vault first.
        gaps['vault'] = load_vault_summary()
    return gaps


def attach_shadow(gaps: Dict[str, Any],
                  shadow: Dict[str, Any]) -> Dict[str, Any]:
    """Fold a shadow evaluation into the gap picture.

    An unreadable shadow DB is recorded under `shadow_error`, never dropped.
    Convention 11: no shadow evidence is not the same as no shadow entries.
    """
    gaps = dict(gaps)
    if shadow.get('status') != 'ok':
        gaps['shadow_error'] = {
            'db_path': shadow.get('db_path'),
            'error': shadow.get('error'),
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }
        gaps['shadow'] = None
        return gaps
    sg = shadow.get('gaps', {})
    gaps['shadow'] = {
        'db_path': shadow.get('db_path'),
        'n_decision_rows': shadow['decisions']['n_rows'],
        'n_entries': shadow['decisions']['n_entries'],
        'n_strategies': shadow['decisions']['n_strategies'],
        'zero_entry_session': sg.get('zero_entry_session'),
        'strategies_not_tested': [r['strategy']
                                  for r in sg.get('strategies_not_tested', [])],
        'strategies_ran_no_entry': [
            r['strategy'] for r in sg.get('strategies_ran_no_entry', [])],
        'strategies_fired': [r['strategy']
                             for r in sg.get('strategies_fired', [])],
        'strategies_underpowered': [
            r['strategy'] for r in sg.get('strategies_underpowered', [])],
        'dominant_skip_reasons': sg.get('dominant_skip_reasons', []),
        'unknown_skip_reasons': sg.get('unknown_skip_reasons', {}),
        'closed_positions': shadow['positions']['n_closed'],
        'equity': shadow.get('equity', {}),
        'paper_log': shadow.get('paper_log', {}),
    }
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


def _kill_condition_names_a_harness(kill: str) -> bool:
    low = kill.lower()
    return any(tok in low for tok in KNOWN_SCORERS)


def validate(candidate: Dict[str, Any],
             known_strategies: List[str]) -> List[Dict[str, str]]:
    """Refuse what is false; warn about what is merely unusual.

    Raises ProposalRefused with a CATEGORY, never a bare False and never a
    silent skip. Returns the list of non-blocking warnings, which the caller
    counts by category and writes onto the proposal (convention 20: a
    downgraded refusal must not become invisible).
    """
    warnings: List[Dict[str, str]] = []

    def warn(category: str, detail: str) -> None:
        warnings.append({'category': category, 'detail': detail})

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
        # RETIRED refusal. The graveyard is crypto spot and perp; a binary or
        # an event market with the same name is a different instrument with a
        # different payoff, so this was a false positive there. The information
        # is still worth carrying, so it is annotated rather than deleted.
        warn('duplicate_name_warning',
             f'{name} shares a name with a swept graveyard strategy. Engage '
             'the burial reason in the body, or rename if the instrument is '
             'genuinely different.')

    cls = candidate['asset_class']
    if cls not in VALID_ASSET_CLASSES:
        # RETIRED refusal.
        warn('unlisted_asset_class_warning',
             f'{cls!r} is outside the known vocabulary '
             f'({", ".join(VALID_ASSET_CLASSES)}). Allowed; it just means no '
             'harness currently scores this class.')
    if cls == 'MULTI' and kind not in ('repair', 'combination', 'experiment'):
        # RETIRED refusal.
        warn('multi_class_warning',
             'MULTI on an edge_hypothesis. Fine, but an edge that cannot name '
             'one instrument is harder to score; consider kind=combination.')

    if not candidate.get('related_graveyard_findings'):
        # RETIRED refusal (the field left REQUIRED_FIELDS).
        warn('no_graveyard_link_warning',
             'no related graveyard finding. Expected for PREDICTION_MARKET, '
             'EVENT and SPORTS: the graveyard has no rows in those classes.')

    bps = candidate['expected_edge_bps']
    if kind in NULL_EDGE_KINDS:
        # The edge of a strategy that has never fired, or of a probe run to
        # find out, is not knowable. Recording it as null keeps a fabricated
        # number out of the file (convention 11).
        if bps is not None:
            raise ProposalRefused(
                'unknowable_edge_claimed',
                f'kind={kind} must record expected_edge_bps as null, '
                f'got {bps!r}')
    else:
        if not isinstance(bps, (int, float)) or isinstance(bps, bool):
            raise ProposalRefused('non_numeric_edge_estimate', repr(bps))
        if not math.isfinite(bps):
            # Convention 19: a non-finite would serialise into a file that
            # json.loads accepts and every other parser rejects.
            raise ProposalRefused('non_finite_edge_estimate', repr(bps))
        floor = min_edge_bps_for(cls)
        if bps < floor:
            raise ProposalRefused(
                'below_min_edge_bps',
                f'{bps}bps < {floor}bps floor for {cls}')

    # Convention 6, and the one hard content constraint. A kill condition needs
    # a NUMBER (so it is a threshold, not a mood) and a NAMED HARNESS (so
    # somebody can actually take the measurement).
    kill = str(candidate['kill_condition'])
    if not any(ch.isdigit() for ch in kill):
        raise ProposalRefused(
            'unmeasurable_kill_condition',
            'kill condition states no threshold')
    if not _kill_condition_names_a_harness(kill):
        raise ProposalRefused(
            'kill_condition_names_no_harness',
            'kill condition names no scorer; expected one of: '
            + ', '.join(KNOWN_SCORERS))

    return warnings


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


def render(candidate: Dict[str, Any],
           warnings: Optional[List[Dict[str, str]]] = None) -> str:
    """Proposal document: YAML frontmatter contract, markdown argument.

    Warnings are written into BOTH the frontmatter and the body. A warning that
    only exists in the run log is a warning nobody reading the proposal sees.
    """
    warnings = warnings or []
    lines = ['---']
    for field in REQUIRED_FIELDS:
        lines.append(f'{field}: {_yaml_scalar(candidate[field])}')
    for field in OPTIONAL_FIELDS:
        if candidate.get(field):
            lines.append(f'{field}: {_yaml_scalar(candidate[field])}')
    lines.append(f"kind: {candidate.get('kind', 'edge_hypothesis')}")
    lines.append(f"status: {candidate.get('status', 'PROPOSED')}")
    lines.append(f"source: {_yaml_scalar(candidate.get('source', 'forge'))}")
    lines.append('forge_warnings: '
                 + _yaml_scalar(', '.join(w['category'] for w in warnings)
                                or 'none'))
    lines.append('---')
    lines.append('')
    lines.append(candidate.get('body', '').rstrip())
    if warnings:
        lines.append('')
        lines.append('## Forge warnings (non-blocking)')
        lines.append('')
        lines.append('These used to be refusals. They no longer block a '
                     'proposal, and they are recorded here so the information '
                     'survives the downgrade.')
        lines.append('')
        for w in warnings:
            lines.append(f"- **{w['category']}**: {w['detail']}")
    lines.append('')
    return '\n'.join(lines)


def write_proposal(candidate: Dict[str, Any], index: int,
                   warnings: Optional[List[Dict[str, str]]] = None) -> str:
    """Write one proposal and return its path."""
    slug = proposal_slug(candidate['name'])
    path = os.path.join(PROPOSALS_DIR, f'{index:03d}-{slug}.md')
    with open(path, 'w') as fh:
        fh.write(render(candidate, warnings))
    return path


# ---------------------------------------------------------------------------
# Where candidates come from
# ---------------------------------------------------------------------------

# Convention 20: the reasoner's outcomes are a closed vocabulary and every one
# of them is a DIFFERENT fact about what happened, so they get different names
# and the fallback reason is recorded rather than flattened to a boolean.
REASONER_FALLBACK_REASONS = (
    'NOT_TESTED',       # the turn could not run at all (convention 11)
    'unusable_reply',   # it ran and we could not read what it said
    'no_candidates',    # it ran, we read it, and it proposed nothing
)


def collect_candidates(gaps: Dict[str, Any],
                       shadow_candidates: Optional[List[Dict[str, Any]]] = None,
                       *,
                       use_reasoner: bool = False,
                       n_proposals: int = 3,
                       db_path: Optional[str] = None,
                       paper_log: Optional[str] = None,
                       shadow_evaluation: Optional[Dict[str, Any]] = None,
                       ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """The candidate list, and the reasoner record when one was asked for.

    Returns `(candidates, reasoner_record_or_None)`.

    `use_reasoner=False` is the default and it is EXACTLY the old behaviour:
    the hand written list from `agents/forge_candidates.py` plus whatever the
    shadow evaluator produced, and a `None` record so nothing new appears in
    the run log.

    `use_reasoner=True` replaces the hand written list with an Opus turn. It
    does NOT replace the shadow repairs: those come from a measurement, not
    from an idea, and the flag says nothing about them. When the turn cannot
    run, or returns nothing usable, the hand written list is used instead and
    the record says which of `REASONER_FALLBACK_REASONS` happened.
    """
    from agents.forge_candidates import CANDIDATES
    shadow_candidates = list(shadow_candidates or [])

    if not use_reasoner:
        return list(CANDIDATES) + shadow_candidates, None

    # Imported lazily: forge_reasoner imports THIS module at module level, so
    # a top level import here would be a cycle.
    from agents import forge_reasoner

    brief = forge_reasoner.gather_evidence(
        db_path=db_path or forge_reasoner.DEFAULT_DB,
        paper_log=paper_log,
        gaps=gaps,
        shadow_evaluation=shadow_evaluation,
        include_shadow=True,
    )
    outcome = forge_reasoner.reason(brief, n_proposals=n_proposals,
                                    db_path=db_path
                                    or forge_reasoner.DEFAULT_DB,
                                    paper_log=paper_log)

    record: Dict[str, Any] = dict(outcome.to_dict())
    record['requested'] = True
    record['n_proposals_requested'] = n_proposals
    record['task'] = forge_reasoner.TASK

    if outcome.candidates:
        record['fell_back_to_deterministic'] = False
        record['fallback_reason'] = None
        return list(outcome.candidates) + shadow_candidates, record

    # Nothing usable came back. Fall back rather than write zero proposals
    # with no explanation, and say WHY in the record (convention 20).
    assert outcome.status in REASONER_FALLBACK_REASONS, (
        f'unknown reasoner status {outcome.status!r}')
    record['fell_back_to_deterministic'] = True
    record['fallback_reason'] = outcome.status
    record['not_tested'] = outcome.status == 'NOT_TESTED'
    return list(CANDIDATES) + shadow_candidates, record


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def proposal_slug(name: str) -> str:
    """The filename slug for a proposal name.

    Single definition, because `write_proposal` and the number allocator must
    agree about what "the same proposal" means. Two definitions of a slug is
    two opinions about identity (convention 23).
    """
    return str(name).replace('_', '-')


def existing_numbers_by_slug(proposals_dir: Optional[str] = None
                             ) -> Dict[str, int]:
    """`{slug: number}` for every proposal already on disk.

    A proposal's number is a property of the proposal, not of where it landed
    in one run's candidate list. Re-running Forge must rewrite `007-foo.md`,
    never add `024-foo.md` beside it.

    A slug carrying two numbers (which the 001-vs-024 episode created) resolves
    to the LOWEST, so a repair collapses the duplicates onto the original
    rather than onto the accident.
    """
    if proposals_dir is None:
        proposals_dir = PROPOSALS_DIR
    out: Dict[str, int] = {}
    try:
        names = os.listdir(proposals_dir)
    except OSError:
        return out
    for name in sorted(names):
        if not name.endswith('.md'):
            continue
        head, _, tail = name[:-3].partition('-')
        if len(head) == 3 and head.isdigit() and tail:
            number = int(head)
            if tail not in out or number < out[tail]:
                out[tail] = number
    return out


def next_free_index(proposals_dir: Optional[str] = None) -> int:
    """One past the highest proposal number already on disk.

    `proposals_dir` defaults to `PROPOSALS_DIR` resolved AT CALL TIME, not as
    a default argument. A default argument would bind the module global once
    at import and then ignore anyone who monkeypatches it, which is exactly
    how the tests point this at a temporary directory. `write_proposal` reads
    the global inside its body for the same reason; these two must agree about
    which directory they are talking about or a test passes while the numbers
    come from the real repo.

    `start_index` used to default to 1, which meant that every run that forgot
    to pass `--start-index` restarted the numbering. Nothing was overwritten,
    because the filename carries the slug as well as the number, so the actual
    damage was quieter than a clobber and worse to live with: on 2026-08-18 a
    run produced a second 001 through 007 beside the existing ones, and
    "proposal 005" stopped identifying a document. `corridor_pair_live.py`
    cites proposal 005 by number in its docstring.

    So the default is now derived rather than assumed. `--start-index` is kept
    as an explicit override, because a caller who is deliberately renumbering
    needs it, but the safe thing is what happens when nobody thinks about it.

    An empty or unreadable directory gives 1, which is the correct first
    number and not a fallback to the old bug.
    """
    if proposals_dir is None:
        proposals_dir = PROPOSALS_DIR
    highest = 0
    try:
        names = os.listdir(proposals_dir)
    except OSError:
        return 1
    for name in names:
        if not name.endswith('.md'):
            continue
        head = name.split('-', 1)[0]
        if len(head) == 3 and head.isdigit():
            highest = max(highest, int(head))
    return highest + 1


def generate(candidates: List[Dict[str, Any]],
             gaps: Dict[str, Any],
             start_index: Optional[int] = None) -> Dict[str, Any]:
    """Validate and write every candidate. Returns the run record.

    Convention 20: refusals are counted BY CATEGORY, warnings are counted BY
    CATEGORY, the RETIRED categories stay in the schema at zero, and every
    accounting identity is asserted rather than assumed. A proposal that
    vanished between the candidate list and the output directory is a missing
    number, and so is a refusal category that quietly stopped existing.
    """
    known = gaps.get('known_strategies', [])
    written: List[Dict[str, Any]] = []
    refused: List[Dict[str, str]] = []
    warned: List[Dict[str, str]] = []

    # A proposal's number belongs to its IDENTITY, not to its position in this
    # run's candidate list.
    #
    # This has now been wrong in two different directions in one day. It began
    # as `start_index=1`, so the reasoner's first real run produced a second
    # 001 through 007 beside the existing ones and "proposal 005" stopped
    # naming a document. Changing the default to next-free fixed that and broke
    # the other half: the DETERMINISTIC path re-emits the same hand written
    # list every run, and it used to rewrite 001-005 in place. Appending
    # instead produced 024-028 carrying the identical five slugs three minutes
    # later.
    #
    # Both failures are the same mistake, which is numbering by position. Keyed
    # on the slug, a re-run of the same proposal overwrites itself and a
    # genuinely new proposal takes the next free number. Neither duplicate can
    # happen. `--start-index` still forces sequential numbering for a
    # deliberate renumbering.
    existing = existing_numbers_by_slug()
    index = next_free_index() if start_index is None else start_index
    for candidate in candidates:
        try:
            warnings = validate(candidate, known)
        except ProposalRefused as exc:
            refused.append({
                'name': str(candidate.get('name')),
                'category': exc.category,
                'detail': exc.detail,
            })
            continue
        for w in warnings:
            warned.append({'name': candidate['name'], **w})
        slug = proposal_slug(candidate['name'])
        if start_index is None and slug in existing:
            number = existing[slug]
        else:
            number = index
            index += 1
            existing[slug] = number
        path = write_proposal(candidate, number, warnings)
        written.append({
            'name': candidate['name'],
            'path': os.path.relpath(path, ROOT),
            'kind': candidate.get('kind', 'edge_hypothesis'),
            'asset_class': candidate['asset_class'],
            'expected_edge_bps': candidate['expected_edge_bps'],
            'warnings': [w['category'] for w in warnings],
            'number': number,
        })

    seen_refusals = collections.Counter(r['category'] for r in refused)
    seen_warnings = collections.Counter(w['category'] for w in warned)

    # Full schema, zeros included. A category that fired zero times is a
    # measurement; a category that disappeared from the schema is a hole.
    refused_by_category = {c: seen_refusals.get(c, 0)
                           for c in REFUSAL_CATEGORIES}
    warned_by_category = {c: seen_warnings.get(c, 0)
                          for c in WARNING_CATEGORIES}

    unknown_refusals = sorted(set(seen_refusals) - set(REFUSAL_CATEGORIES))
    unknown_warnings = sorted(set(seen_warnings) - set(WARNING_CATEGORIES))
    assert not unknown_refusals, (
        f'refusal category outside the schema: {unknown_refusals}')
    assert not unknown_warnings, (
        f'warning category outside the schema: {unknown_warnings}')

    assert len(candidates) - len(refused) == len(written), (
        f'accounting identity broken: {len(candidates)} candidates - '
        f'{len(refused)} refused != {len(written)} written')
    assert sum(refused_by_category.values()) == len(refused), (
        'refusal category counts do not sum to the refusal total')
    assert sum(warned_by_category.values()) == len(warned), (
        'warning category counts do not sum to the warning total')

    return {
        'candidates_screened': len(candidates),
        'written': written,
        'refused': refused,
        'refused_by_category': refused_by_category,
        'warned': warned,
        'warned_by_category': warned_by_category,
        'retired_refusal_categories': dict(RETIRED_REFUSAL_CATEGORIES),
        'min_gross_edge_bps': {
            'default': MIN_GROSS_EDGE_BPS,
            'by_asset_class': dict(MIN_GROSS_EDGE_BPS_BY_ASSET_CLASS),
        },
        'evidence_errors': gaps.get('evidence_errors', {}),
        'gaps_used': {
            'non_firing_count': len(gaps.get('non_firing', [])),
            'asset_classes_absent': gaps.get('asset_classes', {}).get('absent'),
            'failed_assertions': gaps.get('failed_assertions'),
            'distinct_findings': gaps.get('distinct_findings'),
            'shadow': gaps.get('shadow'),
            'shadow_error': gaps.get('shadow_error'),
            'vault': gaps.get('vault'),
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
    parser.add_argument('--start-index', type=int, default=None,
                        help='first proposal number. Default is one past the '
                             'highest number already in strategies/proposals/,'
                             ' so a run cannot silently produce a second 001')
    parser.add_argument('--shadow-results', metavar='DB_PATH', default=None,
                        help='read live shadow-trading results from this '
                             'SQLite DB (e.g. db/trading.db) and propose '
                             'against them as well as against the graveyard')
    parser.add_argument('--paper-log', metavar='CSV_PATH', default=None,
                        help='Polymarket paper log CSV; defaults to '
                             'research/polymarket_paper/'
                             'polymarket_paper_log.csv')
    parser.add_argument('--reasoner', '--opus', action='store_true',
                        dest='reasoner',
                        help='ask Opus for the candidates instead of using the '
                             'hand written list in agents/forge_candidates.py. '
                             'The model returns JSON; PYTHON still validates '
                             'and writes every proposal. If the turn cannot '
                             'run, the run log records NOT_TESTED and the hand '
                             'written list is used instead.')
    parser.add_argument('--n-proposals', type=int, default=3,
                        help='how many candidates to ask the reasoner for '
                             '(default 3). Ignored without --reasoner.')
    args = parser.parse_args(argv)

    bundle = load_evidence()
    gaps = analyse_gaps(bundle)

    shadow = None
    shadow_candidates: List[Dict[str, Any]] = []
    if args.shadow_results:
        from agents import forge_shadow_eval as shadow_eval
        paper_log = args.paper_log or shadow_eval.DEFAULT_PAPER_LOG
        shadow = shadow_eval.evaluate(args.shadow_results, paper_log)
        gaps = attach_shadow(gaps, shadow)
        if shadow.get('status') != 'ok':
            # Convention 11: this is NOT_TESTED, not "no entries". Loud.
            print(f"WARN unreadable shadow results "
                  f"{args.shadow_results}: {shadow.get('error')}",
                  file=sys.stderr)
        else:
            shadow_candidates = shadow_eval.shadow_candidates(shadow)
            if args.gaps_only:
                gaps['shadow_full'] = shadow

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

    candidates, reasoner_record = collect_candidates(
        gaps, shadow_candidates,
        use_reasoner=args.reasoner,
        n_proposals=args.n_proposals,
        db_path=args.shadow_results,
        paper_log=args.paper_log,
        shadow_evaluation=shadow,
    )

    if reasoner_record and reasoner_record['fell_back_to_deterministic']:
        # Loud, and on stderr, because "Forge wrote the template proposals
        # again" looks identical to a normal run in the output below.
        why = reasoner_record['fallback_reason']
        note = ('the Opus turn COULD NOT RUN, so this is NOT_TESTED and not '
                '"the model had no ideas" (convention 11)'
                if why == 'NOT_TESTED' else
                'the Opus turn ran but produced nothing usable')
        print(f'WARN reasoner fell back to the deterministic candidate list: '
              f'{why} ({note}); error={reasoner_record.get("error")}',
              file=sys.stderr)

    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    record = generate(candidates, gaps, start_index=args.start_index)
    record['shadow_results_path'] = args.shadow_results
    record['shadow_candidates_added'] = len(shadow_candidates)
    if reasoner_record is not None:
        record['reasoner'] = reasoner_record
    log_run(record)

    print(f"screened {record['candidates_screened']}, "
          f"wrote {len(record['written'])}, "
          f"refused {len(record['refused'])}, "
          f"warned {len(record['warned'])}")
    for row in record['written']:
        bps = ('unknown' if row['expected_edge_bps'] is None
               else f"~{row['expected_edge_bps']}bps")
        print(f"  WROTE   {row['path']} ({row['asset_class']}, {bps})")
    for row in record['refused']:
        print(f"  REFUSED {row['name']}: {row['category']} ({row['detail']})")
    for row in record['warned']:
        print(f"  WARN    {row['name']}: {row['category']}")
    return 0


if __name__ == '__main__':
    sys.path.insert(0, ROOT)
    raise SystemExit(main())
