"""The reasoning half of Forge: Opus proposes, Python still holds the pen.

`agents/forge.py` is the deterministic half. It loads evidence, computes gaps,
enforces the proposal schema and refuses anything that would put a false or
unfalsifiable number into the record. What it CANNOT do is have an idea. Its
candidates come from `agents/forge_candidates.py`, a hand-written list that a
human rewrites every cycle. This module is the other half: it assembles the
evidence into one brief, asks Opus for candidates, and hands what comes back to
`forge.validate()` and `forge.write_proposal()`.

## Why the model does not write the file

Raven's instruction said "Opus writes the proposals to strategies/proposals/".
It does not, and the deviation is deliberate. Forge's entire contract is that
the deterministic half enforces the schema: a kill condition with a number and
a named harness (convention 6), an edge at or above the instrument's floor
(convention 5), a null edge on a repair rather than an invented one
(convention 11). That contract only holds while PYTHON does the writing. A
model with a Write tool can produce a file that never passed `validate()`, and
nothing downstream would know the difference, because a proposal document
carries no evidence of how it was made.

So the model returns a JSON array of CANDIDATES, and every one of them goes
through the same `validate()` gate as a hand-written candidate. Same output
directory, same file format, same refusal accounting, minus the failure mode.
The model's tool allowlist here is `('Read',)` for the same reason: it may go
look at the repo to sharpen an argument, and it may not write anything.

## Convention 11 is the reason there are three outcomes, not two

  status='ok'              the turn ran and returned candidates
  status='no_candidates'   the turn ran and returned an empty list. That is a
                           RESULT. The model declined to propose. It is not
                           NOT_TESTED and must never be recorded as one.
  status='unusable_reply'  the turn ran and said something we cannot parse.
                           Also a result, and a different one: the model spoke
                           and we failed to read it.
  status='NOT_TESTED'      the turn COULD NOT RUN. No `claude` binary, a
                           timeout, a non-zero exit. This is the only one that
                           is NOT_TESTED.

Four outcomes rather than one boolean, because folding them together is exactly
the mislabel convention 11 exists to prevent. `forge.py` falls back to the
deterministic candidate list in all of the last three, and records WHICH.

## Convention 20 in the brief and in the parser

Every truncation of the brief is recorded in `brief['truncations']` AND printed
into the brief itself, so the model is not left to infer "nothing there" from
an absence we created. Every candidate that the reply contained and that this
module dropped is counted under a named category in `DROP_CATEGORIES`, the full
schema is reported with zeros included, and the accounting identity
`entries_in_reply == kept + per-entry drops` is asserted rather than assumed.

## Convention 19 arrives via llm_client

`llm_client.extract_json` rejects NaN and Infinity before they reach a float
comparison. A reply carrying one is dropped under its OWN category
(`reply_contained_non_finite_number`), not folded in with ordinary parse
failure, because "the model wrote a number it did not have" and "the model
wrote prose where JSON was asked for" are different mistakes.
"""
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from agents import forge  # noqa: E402
from agents import hypothesis_graph as hg  # noqa: E402
from agents import llm_client  # noqa: E402
from agents import vault_digest  # noqa: E402
from agents import vault_reader  # noqa: E402

# The task name routes to Opus in `llm_client.MODEL_FOR_TASK`. Callers name a
# TASK, never a model string, so re-routing is one edit in llm_client.
TASK = 'forge_proposals'

DEFAULT_DB = os.path.join(ROOT, 'db', 'trading.db')

DEFAULT_N_PROPOSALS = 3

# A reasoning turn that has to read a brief this size is slow. This is not a
# performance target, it is the point past which we call the turn wedged.
DEFAULT_TIMEOUT_S = 900

# Read only. The model may go look at a strategy file to sharpen an argument.
# It may not write anything: Python writes the proposals. See the docstring.
DEFAULT_ALLOWED_TOOLS = ('Read',)

# ---------------------------------------------------------------------------
# Budgets
#
# Everything below ends up inside one prompt. Convention 17: each of these is
# an assumption with an expiry date, and the expiry is "the brief stopped
# fitting" or "a section started dominating for no reason". They are separate
# per section on purpose, so a vault that grows without bound cannot starve the
# hypothesis graph, and a hypothesis graph that grows to 10,000 rows cannot
# starve the shadow evidence.
# ---------------------------------------------------------------------------

VAULT_BUDGET_CHARS = vault_reader.DEFAULT_BUDGET_CHARS   # per vault section
VAULT_RENDER_CAP_CHARS = 40000                           # the rendered block
GRAVEYARD_CAP_CHARS = 8000
HYPOTHESIS_CAP_CHARS = 20000
SHADOW_CAP_CHARS = 8000

# Task 2 of the vault-digest work: the digest is read in full (it is capped
# at vault_digest.DIGEST_CAP_CHARS, ~10K, so this is bounded by construction).
# What is NOT bounded by construction is the delta of notes the digest has
# not absorbed yet, so that gets its own budget.
RECENT_NOTES_BUDGET_CHARS = 20000
DIGEST_STALE_DAYS = 30

# Per-row caps inside the hypothesis-graph block. A hypothesis text is one
# sentence by construction; the notes field is where the derivation lives and
# is the part that runs long.
HYPOTHESIS_TEXT_CHARS = 240
HYPOTHESIS_NOTES_CHARS = 320
DEFAULT_MAX_FAILED_HYPOTHESES = 60

TRUNCATION_MARKER = (
    '\n\n_[TRUNCATED: %d of %d characters of %s were dropped to fit the '
    'prompt budget. Do not read their absence as "there is nothing there". '
    'Convention 20.]_\n')


# ---------------------------------------------------------------------------
# Drop accounting
# ---------------------------------------------------------------------------

# Convention 20: two drop causes never share one counter, and a category that
# fired zero times is reported at zero rather than vanishing.
#
# The first three are WHOLE-REPLY failures. At most one of them can fire in a
# run, and when one does there are no per-entry counts to take.
REPLY_DROP_CATEGORIES = (
    'llm_turn_could_not_run',            # NOT_TESTED. The turn never happened.
    'reply_not_parseable_json',          # It ran and we cannot read it.
    'reply_contained_non_finite_number',  # Convention 19, its own bucket.
    'reply_not_a_list',                  # It ran, parsed, wrong shape.
)

# The rest are PER-ENTRY: the reply parsed as a list and an individual element
# was dropped before it ever reached `forge.validate()`.
ENTRY_DROP_CATEGORIES = (
    'entry_not_an_object',
    'missing_why_it_might_fail',
    'missing_addresses_past_failure',
)

DROP_CATEGORIES = REPLY_DROP_CATEGORIES + ENTRY_DROP_CATEGORIES

# Fields the reasoner demands that `forge.validate()` knows nothing about.
# `validate()` polices what would put a FALSE number into the record; these two
# police whether the model actually did the thinking, which is a different job
# and belongs here rather than in the shared validator.
REASONER_FIELDS = ('why_it_might_fail', 'addresses_past_failure')


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------

def _clip(text: str, cap: int, what: str,
          truncations: List[Dict[str, Any]]) -> str:
    """Cut `text` to `cap` chars, and SAY SO both ways.

    A silent truncation is a missing number (convention 20). So the drop is
    recorded in the machine-readable `truncations` list AND printed inline
    where the model will read it.
    """
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    truncations.append({
        'what': what,
        'kept_chars': cap,
        'dropped_chars': dropped,
        'total_chars': len(text),
    })
    return text[:cap] + (TRUNCATION_MARKER % (dropped, len(text), what))


def _short(value: Any, cap: int) -> str:
    """One field, flattened to a single line and capped."""
    text = ' '.join(str(value or '').split())
    if len(text) <= cap:
        return text
    return text[:cap] + ' [...]'


def load_failed_hypotheses(db_path: str = DEFAULT_DB,
                           limit: int = DEFAULT_MAX_FAILED_HYPOTHESES
                           ) -> Dict[str, Any]:
    """Every TESTED_FAILED row in `hypothesis_graph`, newest ids last.

    Reuses `agents/hypothesis_graph.get_failed_hypotheses` rather than writing
    a second SELECT. Convention 23: a second query is a second place to fix
    when the schema moves, and this one is a documented contract three agents
    already code against.

    Opened READ ONLY. The shadow loop may be writing this file right now, and
    a reasoning agent has no business holding a write handle on a live tape.

    An unreadable DB returns status='unreadable' with the error text, never a
    clean empty list. Convention 11: no rows read is not the same as no rows.
    """
    out: Dict[str, Any] = {
        'db_path': db_path,
        'status': 'ok',
        'error': None,
        'n_failed_total': 0,
        'n_rows_total': 0,
        'shown': [],
        'dropped_over_limit': 0,
        'limit': limit,
        'failure_mode_counts': {},
        'kill_recommendations': [],
    }
    try:
        conn = hg.connect(db_path, read_only=True)
    except sqlite3.Error as exc:
        out['status'] = 'unreadable'
        out['error'] = '%s: %s' % (type(exc).__name__, exc)
        return out

    try:
        failed = hg.get_failed_hypotheses(conn)
        out['n_rows_total'] = int(
            conn.execute('SELECT COUNT(*) FROM hypothesis_graph').fetchone()[0])
        modes = hg.failure_mode_counts(conn)
        out['failure_mode_counts'] = {
            '%s/%s' % (strategy, mode): n for (strategy, mode), n in
            sorted(modes.items())}
        out['kill_recommendations'] = hg.kill_recommendations(conn)
    except sqlite3.Error as exc:
        out['status'] = 'unreadable'
        out['error'] = '%s: %s' % (type(exc).__name__, exc)
        return out
    finally:
        conn.close()

    out['n_failed_total'] = len(failed)
    kept = failed[-limit:] if limit and len(failed) > limit else list(failed)
    out['dropped_over_limit'] = len(failed) - len(kept)
    out['shown'] = [{
        'id': h.id,
        'strategy_name': h.strategy_name,
        'asset_class': h.asset_class,
        'market_regime': h.market_regime,
        'failure_mode': h.failure_mode,
        'source': h.source,
        'hypothesis': _short(h.hypothesis, HYPOTHESIS_TEXT_CHARS),
        'notes': _short(h.notes, HYPOTHESIS_NOTES_CHARS),
    } for h in kept]
    return out


def _full_tree_vault(budget_chars: int, warning: str) -> Dict[str, Any]:
    """The pre-digest behaviour: read everything. Used by every fallback path."""
    context = vault_reader.load_context(budget_chars=budget_chars)
    return {
        'text': warning + '\n\n' + vault_reader.render_context(context),
        'total_notes': context['total_notes'],
        'total_chars': context['total_chars'],
        'total_dropped': context['total_dropped'],
        'known_failure_modes': vault_reader.known_failure_modes(context),
    }


def load_vault(budget_chars: int = VAULT_BUDGET_CHARS,
               recent_budget_chars: int = RECENT_NOTES_BUDGET_CHARS
               ) -> Dict[str, Any]:
    """The Obsidian vault, rendered for a prompt, plus its own accounting.

    Task 2 of the vault-digest work: the PRIMARY input is now
    `agents/vault_digest.py`'s rolling `_DIGEST.md` (capped, so bounded by
    construction), not the full note tree. Three explicit fallbacks, all of
    them degrading to the pre-digest full-tree read rather than silently
    returning less than before:

      * the digest file does not exist -> full tree, `digest_status='missing'`
      * the digest exists but its newest entry is over
        `DIGEST_STALE_DAYS` old -> full tree, `digest_status='stale'`
      * a section directory is unreadable -> `status='unreadable'`, same as
        the pre-digest behaviour

    When the digest is fresh, "recent notes" (Task 2.2) are the delta: any
    note on disk whose filename is not yet a digest entry heading, read in
    full and budgeted separately so a burst of `skip_model` fallback notes
    cannot starve the digest itself.
    """
    out: Dict[str, Any] = {
        'status': 'ok', 'error': None, 'text': '',
        'vault_root': vault_reader.VAULT_ROOT,
        'total_notes': 0, 'total_chars': 0, 'total_dropped': 0,
        'known_failure_modes': {},
        'digest_status': 'ok', 'digest_chars': 0, 'recent_notes': 0,
    }
    try:
        digest_text = vault_digest.read_digest()
    except OSError as exc:
        out['status'] = 'unreadable'
        out['error'] = '%s: %s' % (type(exc).__name__, exc)
        return out

    if not digest_text.strip():
        out.update(_full_tree_vault(
            budget_chars,
            '# OBSIDIAN VAULT (DIGEST MISSING)\n\n'
            'WARNING: %s does not exist yet. Falling back to the full vault '
            'tree exactly as before the digest existed. This is expected '
            'once, before the first note write after this feature landed.'
            % vault_digest.digest_path()))
        out['digest_status'] = 'missing'
        return out

    newest = vault_digest.newest_entry_date(digest_text)
    if vault_digest.is_stale(newest, max_age_days=DIGEST_STALE_DAYS):
        out.update(_full_tree_vault(
            budget_chars,
            '# OBSIDIAN VAULT (DIGEST STALE)\n\n'
            'WARNING: the digest at %s has not gained an entry since %s, '
            'more than %d days ago. Falling back to the full vault tree.'
            % (vault_digest.digest_path(), newest or '(never)',
               DIGEST_STALE_DAYS)))
        out['digest_status'] = 'stale'
        return out

    covered = vault_digest.digested_names(digest_text)
    recent = vault_reader.read_uncovered(covered, budget_chars=recent_budget_chars)

    parts = ['# OBSIDIAN VAULT DIGEST (rolling conclusions, newest entry %s)'
             % newest, '', digest_text.strip(), '']
    if recent['notes']:
        parts.append('# RECENT NOTES NOT YET IN THE DIGEST (%d)'
                     % len(recent['notes']))
        parts.append('')
        parts.append('These exist on disk but have no digest entry yet - '
                     'most often a `skip_model` fallback note, which carries '
                     'no judgement to digest (Convention 11).')
        parts.append('')
        for note in recent['notes']:
            parts.append('### %s' % note['name'])
            parts.append(note['text'].strip())
            parts.append('')
    if recent['dropped']:
        parts.append('_NOTE: %d recent note(s) NOT included (%s). Do not '
                     'read their absence as "there is nothing there"._'
                     % (len(recent['dropped']),
                        ', '.join(sorted({d['reason']
                                          for d in recent['dropped']}))))
        parts.append('')

    out['text'] = '\n'.join(parts)
    out['digest_status'] = 'ok'
    out['digest_chars'] = len(digest_text)
    out['recent_notes'] = len(recent['notes'])
    out['total_notes'] = len(recent['notes'])
    out['total_chars'] = len(digest_text) + recent['chars']
    out['total_dropped'] = len(recent['dropped'])
    return out


def gather_evidence(db_path: str = DEFAULT_DB,
                    paper_log: Optional[str] = None,
                    *,
                    gaps: Optional[Dict[str, Any]] = None,
                    shadow_evaluation: Optional[Dict[str, Any]] = None,
                    include_shadow: bool = True,
                    vault_budget_chars: int = VAULT_BUDGET_CHARS,
                    max_failed_hypotheses: int = DEFAULT_MAX_FAILED_HYPOTHESES,
                    ) -> Dict[str, Any]:
    """Assemble everything Forge knows into one budgeted brief.

    Four sources, none of them reimplemented here:
      * the graveyard, via `forge.load_evidence()` + `forge.analyse_gaps()`
      * the live shadow loop, via `agents/forge_shadow_eval.evaluate()`, folded
        in with `forge.attach_shadow()`
      * the hypothesis graph, via `hypothesis_graph.get_failed_hypotheses()`
      * the Obsidian vault, via `vault_reader.render_context()`

    Pass `gaps=` when the caller has already computed them (forge.main has), so
    the graveyard is not loaded and analysed twice in one run, and
    `shadow_evaluation=` when it has already run the shadow evaluator, so the
    live DB is not read twice either.

    Every source that could not be read is recorded with its error rather than
    dropped, so the model is told "this was unreadable" instead of being left
    to infer "this was empty" (convention 11).
    """
    truncations: List[Dict[str, Any]] = []

    if gaps is None:
        gaps = forge.analyse_gaps(forge.load_evidence())

    shadow_summary: Dict[str, Any] = {
        'status': 'not_requested',
        'note': 'the caller did not ask for shadow evidence',
    }
    if include_shadow:
        from agents import forge_shadow_eval as shadow_eval
        evaluation = shadow_evaluation
        if evaluation is None:
            log_path = paper_log or shadow_eval.DEFAULT_PAPER_LOG
            evaluation = shadow_eval.evaluate(db_path, log_path)
        if 'shadow' not in gaps:
            gaps = forge.attach_shadow(gaps, evaluation)
        if evaluation.get('status') == 'ok':
            shadow_summary = {
                'status': 'ok',
                'gaps': gaps.get('shadow'),
                'deterministic_repairs': [
                    c['name'] for c in shadow_eval.shadow_candidates(evaluation)
                ],
            }
        else:
            # Convention 11 again. Unreadable, not empty.
            shadow_summary = {
                'status': 'unreadable',
                'error': evaluation.get('error'),
                'note': 'NOT_TESTED, not empty. Convention 11.',
            }

    hypothesis_graph = load_failed_hypotheses(db_path, limit=max_failed_hypotheses)
    vault = load_vault(budget_chars=vault_budget_chars)

    return {
        'gaps': gaps,
        'graveyard_errors': gaps.get('evidence_errors', {}),
        'shadow': shadow_summary,
        'hypothesis_graph': hypothesis_graph,
        'vault': vault,
        'vault_ctx_line': render_vault_ctx_line(vault, hypothesis_graph),
        'truncations': truncations,
        'budget': {
            'vault_per_section_chars': vault_budget_chars,
            'vault_render_cap_chars': VAULT_RENDER_CAP_CHARS,
            'graveyard_cap_chars': GRAVEYARD_CAP_CHARS,
            'hypothesis_cap_chars': HYPOTHESIS_CAP_CHARS,
            'shadow_cap_chars': SHADOW_CAP_CHARS,
            'max_failed_hypotheses': max_failed_hypotheses,
        },
    }


def render_vault_ctx_line(vault: Dict[str, Any],
                          hypothesis_graph: Dict[str, Any]) -> str:
    """Task 2.5: the one-line audit trail for what this cycle actually read.

    Printed with every Forge run so a human scanning logs can see, without
    opening the brief, whether the digest was used or the run fell all the
    way back to a full-tree read.
    """
    graph_n = hypothesis_graph.get('n_failed_total', 0)
    status = vault.get('digest_status', 'ok')
    if status == 'ok':
        return ('vault_ctx: digest=%.1fK recent=%d notes graph=%d'
                % (vault.get('digest_chars', 0) / 1000.0,
                   vault.get('recent_notes', 0), graph_n))
    return ('vault_ctx: digest=%s full_tree=%.1fK notes=%d graph=%d'
            % (status.upper(), vault.get('total_chars', 0) / 1000.0,
               vault.get('total_notes', 0), graph_n))


# ---------------------------------------------------------------------------
# Rendering the brief
# ---------------------------------------------------------------------------

def _json_block(obj: Any) -> str:
    """Pretty JSON that is safe to write. Convention 19 raises here, not later."""
    return json.dumps(obj, indent=2, sort_keys=True, allow_nan=False,
                      default=str)


def render_hypothesis_graph(block: Dict[str, Any]) -> str:
    """The TESTED_FAILED rows, as a table the model can cite by id."""
    lines = ['# HYPOTHESIS GRAPH: what has already been tested and failed', '']
    if block['status'] != 'ok':
        lines.append('STATUS: UNREADABLE (%s). This is NOT_TESTED, not "no '
                     'failures on record". Convention 11: do not treat the '
                     'absence of this section as permission to re-propose a '
                     'buried idea.' % block['error'])
        return '\n'.join(lines) + '\n'
    lines.append('Source: %s, table `hypothesis_graph`, '
                 'status = TESTED_FAILED.' % block['db_path'])
    lines.append('%d TESTED_FAILED row(s) of %d rows in the table. %d shown '
                 'below (oldest dropped first when over the limit of %d).'
                 % (block['n_failed_total'], block['n_rows_total'],
                    len(block['shown']), block['limit']))
    if block['dropped_over_limit']:
        lines.append('_%d failed row(s) were NOT included. Their absence is a '
                     'budget decision, not evidence that they do not exist._'
                     % block['dropped_over_limit'])
    lines.append('')
    lines.append('Cite these by `id` and by `strategy_name` when you claim a '
                 'proposal addresses a past failure. A vague "learns from past '
                 'failures" is not a citation.')
    lines.append('')
    for row in block['shown']:
        lines.append(
            '- **id %s** `%s` [%s / %s] failure_mode=`%s` source=%s'
            % (row['id'], row['strategy_name'], row['asset_class'],
               row['market_regime'], row['failure_mode'], row['source']))
        lines.append('  - hypothesis: %s' % row['hypothesis'])
        if row['notes']:
            lines.append('  - why it failed: %s' % row['notes'])
    lines.append('')
    if block['kill_recommendations']:
        lines.append('## Strategy x failure_mode seen 3 or more times')
        lines.append('')
        for rec in block['kill_recommendations']:
            lines.append('- `%s` died as `%s` %d time(s) (ids %s)'
                         % (rec['strategy_name'], rec['failure_mode'],
                            rec['count'],
                            ', '.join(str(i) for i in rec['hypothesis_ids'])))
        lines.append('')
    return '\n'.join(lines)


def render_brief(brief: Dict[str, Any]) -> str:
    """The whole evidence pack as one markdown block, budgeted per section.

    Idempotent: `brief['truncations']` is REPLACED, not appended to, so
    rendering the same brief twice does not report every cut twice.
    """
    truncations: List[Dict[str, Any]] = []

    graveyard = _json_block({
        'evidence_errors': brief['gaps'].get('evidence_errors', {}),
        'known_strategies': brief['gaps'].get('known_strategies', []),
        'non_firing': brief['gaps'].get('non_firing', []),
        'asset_classes': brief['gaps'].get('asset_classes', {}),
        'worst_pooled': brief['gaps'].get('worst_pooled', []),
        'failed_assertions': brief['gaps'].get('failed_assertions', []),
        'distinct_findings': brief['gaps'].get('distinct_findings'),
        'not_tested_breakdown': brief['gaps'].get('not_tested_breakdown'),
    })
    shadow = _json_block(brief['shadow'])
    hypotheses = render_hypothesis_graph(brief['hypothesis_graph'])

    vault_block = brief['vault']
    if vault_block['status'] != 'ok':
        vault_text = ('# OBSIDIAN VAULT\n\nSTATUS: UNREADABLE (%s). '
                      'NOT_TESTED, not "no lessons". Convention 11.\n'
                      % vault_block['error'])
    else:
        vault_text = vault_block['text']

    vault_ctx_line = render_vault_ctx_line(vault_block, brief['hypothesis_graph'])
    brief['vault_ctx_line'] = vault_ctx_line

    parts = [
        vault_ctx_line,
        '',
        '# GRAVEYARD AND GAP ANALYSIS (backtest)',
        '',
        'Computed by `agents/forge.py: analyse_gaps()`. `distinct_findings` is '
        'the number to cite, never a raw pass count (convention 2).',
        '',
        '```json',
        _clip(graveyard, GRAVEYARD_CAP_CHARS, 'the graveyard gap analysis',
              truncations),
        '```',
        '',
        '# LIVE SHADOW LOOP (paper, Polymarket)',
        '',
        'Computed by `agents/forge_shadow_eval.py` over `db/trading.db`. A '
        'DATA_BLOCKER skip means the strategy never evaluated its condition, '
        'so it is NOT_TESTED and did NOT look and decline (convention 11).',
        '',
        '```json',
        _clip(shadow, SHADOW_CAP_CHARS, 'the shadow evaluation', truncations),
        '```',
        '',
        _clip(hypotheses, HYPOTHESIS_CAP_CHARS, 'the hypothesis graph',
              truncations),
        '',
        _clip(vault_text, VAULT_RENDER_CAP_CHARS, 'the Obsidian vault',
              truncations),
    ]

    if truncations:
        parts.append('')
        parts.append('# WHAT THIS BRIEF DROPPED')
        parts.append('')
        parts.append('The brief is budgeted. These sections were cut. Nothing '
                     'below is evidence of absence.')
        parts.append('')
        for t in truncations:
            parts.append('- %s: kept %d of %d chars, dropped %d'
                         % (t['what'], t['kept_chars'], t['total_chars'],
                            t['dropped_chars']))
    brief['truncations'] = truncations
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

def _edge_floor_table() -> str:
    rows = []
    for cls in forge.VALID_ASSET_CLASSES:
        rows.append('  %-18s %d bps' % (cls, forge.min_edge_bps_for(cls)))
    return '\n'.join(rows)


def build_prompt(brief: Dict[str, Any],
                 n_proposals: int = DEFAULT_N_PROPOSALS) -> str:
    """The Opus prompt: the brief, the exact schema, and the refusal rules.

    The schema section is generated FROM `forge.REQUIRED_FIELDS`,
    `forge.KINDS`, `forge.VALID_ASSET_CLASSES`, `forge.KNOWN_SCORERS` and
    `forge.min_edge_bps_for`, not typed out by hand. Convention 22: a claim in
    a prompt is not a wiring test, and a hand-copied schema drifts from the
    validator the first time somebody edits one and not the other. If a field
    is added to `validate()`, this prompt asks for it on the next run with no
    edit here.
    """
    required = '\n'.join(
        '  "%s": %s' % (f, _FIELD_HELP.get(f, 'required, non-empty'))
        for f in forge.REQUIRED_FIELDS)
    optional = '\n'.join(
        '  "%s": %s' % (f, _FIELD_HELP.get(f, 'optional'))
        for f in forge.OPTIONAL_FIELDS)
    reasoner = '\n'.join(
        '  "%s": %s' % (f, _FIELD_HELP[f]) for f in REASONER_FIELDS)

    return _PROMPT_TEMPLATE % {
        'n': n_proposals,
        'brief': render_brief(brief),
        'required': required,
        'optional': optional,
        'reasoner': reasoner,
        'kinds': ', '.join(forge.KINDS),
        'null_edge_kinds': ' and '.join(forge.NULL_EDGE_KINDS),
        'classes': ', '.join(forge.VALID_ASSET_CLASSES),
        'scorers': ', '.join(forge.KNOWN_SCORERS),
        'floors': _edge_floor_table(),
        'default_floor': forge.MIN_GROSS_EDGE_BPS,
    }


_FIELD_HELP = {
    'name': ('a short snake_case identifier, unique. This becomes the '
             'filename.'),
    'thesis': ('why this might work. Name the participant whose behaviour '
               'creates the inefficiency and why it persists.'),
    'expected_edge_bps': (
        'an HONEST gross edge estimate as a finite JSON number, or null when '
        'kind is a repair or an experiment. Never NaN, never Infinity, never '
        'a string. Show the arithmetic in the body.'),
    'kill_condition': (
        'MANDATORY and it must contain BOTH a NUMBER and the NAME OF A '
        'HARNESS. "if it underperforms" is refused. "if net pnl per resolved '
        'position is below 0 cents over 200 or more positions in '
        'backtest/polymarket_harness.py, retire it" is accepted.'),
    'asset_class': 'one of the vocabulary below.',
    'entry_exit_rules': (
        'concrete enough that somebody could code it without asking you a '
        'question. Every entry needs a stop strictly below entry '
        '(convention 8); a losing binary share is 0.00.'),
    'data_requirements': (
        'every input this needs, and for each one whether we currently have '
        'it. If we do not, say so plainly; that makes this a repair.'),
    'related_graveyard_findings': (
        'optional. Expected to be absent for PREDICTION_MARKET, EVENT and '
        'SPORTS: the graveyard holds no rows in those classes.'),
    'markets': 'optional. The specific markets or tickers this trades.',
    'why_it_might_fail': (
        'MANDATORY and non-empty. The strongest argument AGAINST your own '
        'proposal. A proposal with no stated failure mode has not been '
        'thought through.'),
    'addresses_past_failure': (
        'MANDATORY and non-empty. Cite SPECIFIC hypothesis_graph entries by '
        '`id` and by `strategy_name`, and SPECIFIC vault note filenames from '
        'the brief. "learns from past failures" is not a citation and will be '
        'dropped. If nothing in the record is relevant, say exactly that and '
        'say why the record does not cover this idea.'),
}


_PROMPT_TEMPLATE = """You are Forge, the strategy hypothesis generator for a
paper trading bot. Your job in this turn is to propose %(n)d new strategy
candidates from the evidence below.

Read all of it before you write anything. It contains what has already been
tested and failed, what is currently blocked and why, and the lessons written
into the Obsidian vault after previous cycles.

======================================================================
EVIDENCE
======================================================================

%(brief)s

======================================================================
WHAT TO RETURN
======================================================================

Reply with a SINGLE JSON ARRAY of %(n)d objects and NOTHING ELSE. No preamble,
no explanation before or after, no markdown outside the JSON. A fenced ```json
block is acceptable; prose around it is not.

You do NOT write any files. Python reads your JSON, validates every object
against the real schema, and writes the ones that pass to
strategies/proposals/. An object that fails validation is refused and counted,
so a field you skip is a proposal you lose.

Each object MUST carry these fields:

%(required)s

Each object MUST ALSO carry these two, which are what make this a reasoned
proposal rather than a template:

%(reasoner)s

These are optional and are written into the document when present:

%(optional)s

Plus:
  "kind": one of %(kinds)s. Default edge_hypothesis.
  "body": the full markdown argument. This is the document body. Show your
          arithmetic for the edge estimate, name the evidence you leaned on,
          and state what would change your mind.

======================================================================
THE RULES THAT WILL REFUSE YOU
======================================================================

1. KILL CONDITION (convention 6). It must contain a digit AND name one of
   these scorers, matched as a substring, case insensitive:
     %(scorers)s
   No number, or no named scorer, is a hard refusal.

2. EDGE FLOOR (convention 5). `expected_edge_bps` below the floor for its
   asset class is a hard refusal. The floor is instrument specific because bps
   is a ratio and the denominator differs:
%(floors)s
   Anything not listed defaults to %(default_floor)d bps. On a Polymarket
   binary the denominator is the PREMIUM in cents and the tick is 1 cent, so
   200 bps is ONE TICK on a 50 cent contract, the smallest edge that can
   physically exist there. Do not spend a proposal below its floor.

3. UNKNOWABLE EDGE (convention 11). kind %(null_edge_kinds)s MUST record
   `expected_edge_bps` as null. A strategy that has never evaluated its own
   condition has no knowable edge, and inventing a number for it is worse than
   admitting you do not have one. Any other kind must give a finite number.

4. ASSET CLASS vocabulary: %(classes)s. Something outside this list is allowed
   but warns, because no harness currently scores it.

5. NEVER write NaN or Infinity. They are not JSON and the parser rejects them.
   If you do not have a number, use null where null is allowed, or change the
   kind to an experiment.

======================================================================
HOW TO THINK ABOUT THIS
======================================================================

- Do not re-propose something the hypothesis graph already buried, unless you
  can name the id, name the failure_mode, and say what is DIFFERENT this time.
  A rename is not a difference.
- "It fires" is not edge. A signal count is a sample, not a result.
- NOT_TESTED means COULD NOT RUN. A strategy blocked on a missing input has
  not failed; it has never been asked. Those are repairs, not edge
  hypotheses, and the deterministic half of Forge already writes one repair per
  blocked strategy, so do not duplicate them.
- A gap in the record is a reason to propose, and it is also a reason to be
  honest that you are proposing into the dark.

======================================================================
STYLE
======================================================================

Write in plain, direct English. Do NOT use em-dashes and do NOT use double
hyphens anywhere in any field. Use a single hyphen surrounded by spaces, a
comma, or a new sentence instead. No corporate filler.

Return the JSON array now.
"""


# ---------------------------------------------------------------------------
# Parsing the reply
# ---------------------------------------------------------------------------

class ReasonerResult(object):
    """What one reasoning turn produced, and how it went.

    `candidates` are ready to hand to `forge.validate()`. They have NOT been
    validated here: that is deliberate, so there is exactly one validator and
    exactly one refusal ledger (convention 23).
    """

    def __init__(self, status: str, candidates: List[Dict[str, Any]],
                 llm: Optional[llm_client.LLMResult] = None,
                 dropped: Optional[List[Dict[str, str]]] = None,
                 entries_in_reply: int = 0,
                 prompt_chars: int = 0,
                 error: Optional[str] = None) -> None:
        self.status = status
        self.candidates = candidates
        self.llm = llm
        self.dropped = dropped or []
        self.entries_in_reply = entries_in_reply
        self.prompt_chars = prompt_chars
        self.error = error

    @property
    def ok(self) -> bool:
        """True only when the turn ran AND produced at least one candidate."""
        return self.status == 'ok'

    @property
    def not_tested(self) -> bool:
        """True when the turn COULD NOT RUN. Convention 11's one case."""
        return self.status == 'NOT_TESTED'

    def dropped_by_category(self) -> Dict[str, int]:
        """Full schema, zeros included. Convention 20."""
        counts = {c: 0 for c in DROP_CATEGORIES}
        for row in self.dropped:
            counts[row['category']] = counts.get(row['category'], 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status,
            'error': self.error,
            'prompt_chars': self.prompt_chars,
            'entries_in_reply': self.entries_in_reply,
            'candidates_kept': len(self.candidates),
            'candidate_names': [c.get('name') for c in self.candidates],
            'dropped': self.dropped,
            'dropped_by_category': self.dropped_by_category(),
            'llm': self.llm.to_dict() if self.llm is not None else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ('ReasonerResult(status=%r, candidates=%d, dropped=%d)'
                % (self.status, len(self.candidates), len(self.dropped)))


def _fold_reasoner_fields(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Append the reasoning fields to the body so they survive rendering.

    `forge.render()` writes REQUIRED_FIELDS, OPTIONAL_FIELDS and the body.
    `why_it_might_fail` and `addresses_past_failure` are in neither list, so
    without this they would be validated and then thrown away, which is the
    worst of both. Folding them into the body keeps `forge.render()` untouched
    and puts the model's own case against itself in the document a human
    actually reads.
    """
    out = dict(candidate)
    body = str(out.get('body') or '').rstrip()
    extra = [
        '',
        '## Why this might fail',
        '',
        str(out.get('why_it_might_fail', '')).strip(),
        '',
        '## What past failure this addresses',
        '',
        str(out.get('addresses_past_failure', '')).strip(),
        '',
    ]
    out['body'] = (body + '\n' + '\n'.join(extra)).strip() + '\n'
    return out


def parse_candidates(text: str) -> Tuple[str, List[Dict[str, Any]],
                                         List[Dict[str, str]], int]:
    """Turn a model reply into candidates. Returns (status, kept, dropped, n).

    `n` is how many entries the reply actually contained, so the caller can
    assert `n == len(kept) + per-entry drops` rather than trusting it.
    """
    dropped: List[Dict[str, str]] = []
    try:
        payload = llm_client.extract_json(text)
    except ValueError as exc:
        # Convention 20: these are two different mistakes and they get two
        # different counters. `llm_client.strict_json_loads` is the single
        # definition of "non-finite" (convention 23), so the discrimination is
        # taken from its error text rather than by copying its regex here.
        # `test_a_reply_with_nan_lands_in_its_own_category` pins this wiring.
        category = ('reply_contained_non_finite_number'
                    if 'non-finite' in str(exc)
                    else 'reply_not_parseable_json')
        dropped.append({'name': '(whole reply)', 'category': category,
                        'detail': str(exc)[:300]})
        return 'unusable_reply', [], dropped, 0

    if not isinstance(payload, list):
        dropped.append({
            'name': '(whole reply)',
            'category': 'reply_not_a_list',
            'detail': 'expected a JSON array, got %s' % type(payload).__name__,
        })
        return 'unusable_reply', [], dropped, 0

    kept: List[Dict[str, Any]] = []
    for i, entry in enumerate(payload):
        if not isinstance(entry, dict):
            dropped.append({
                'name': '(entry %d)' % i,
                'category': 'entry_not_an_object',
                'detail': 'got %s' % type(entry).__name__,
            })
            continue
        name = str(entry.get('name') or '(entry %d)' % i)
        missing = [f for f in REASONER_FIELDS
                   if not str(entry.get(f) or '').strip()]
        if missing:
            # One category per field, never a shared "missing_fields" counter.
            dropped.append({
                'name': name,
                'category': 'missing_%s' % missing[0],
                'detail': 'empty or absent: %s' % ', '.join(missing),
            })
            continue
        kept.append(_fold_reasoner_fields(entry))

    status = 'ok' if kept else 'no_candidates'
    return status, kept, dropped, len(payload)


def reason(brief: Optional[Dict[str, Any]] = None,
           *,
           n_proposals: int = DEFAULT_N_PROPOSALS,
           db_path: str = DEFAULT_DB,
           paper_log: Optional[str] = None,
           timeout_s: int = DEFAULT_TIMEOUT_S,
           allowed_tools: Sequence[str] = DEFAULT_ALLOWED_TOOLS,
           ) -> ReasonerResult:
    """Run one Opus turn and return the candidates it proposed.

    Builds the brief when one is not supplied. Never raises on a dead model:
    an `LLMResult` with `ok is False` becomes status='NOT_TESTED' and an empty
    candidate list, because a reasoning layer that crashes the caller is worse
    than no reasoning layer.
    """
    if brief is None:
        brief = gather_evidence(db_path=db_path, paper_log=paper_log)
    prompt = build_prompt(brief, n_proposals=n_proposals)

    result = llm_client.run_task(TASK, prompt, timeout_s=timeout_s,
                                 allowed_tools=tuple(allowed_tools))

    if not result.ok:
        # Convention 11. The turn COULD NOT RUN. This is the only NOT_TESTED.
        return ReasonerResult(
            'NOT_TESTED', [], llm=result,
            dropped=[{'name': '(no turn)',
                      'category': 'llm_turn_could_not_run',
                      'detail': str(result.error)}],
            prompt_chars=len(prompt), error=result.error)

    status, kept, dropped, n_entries = parse_candidates(result.text)

    # Convention 20: the identity is asserted, not assumed. Every entry the
    # reply contained was either kept or dropped under exactly one category.
    per_entry = [d for d in dropped if d['category'] in ENTRY_DROP_CATEGORIES]
    assert n_entries == len(kept) + len(per_entry), (
        'reasoner accounting identity broken: %d entries != %d kept + %d '
        'dropped' % (n_entries, len(kept), len(per_entry)))
    unknown = sorted({d['category'] for d in dropped} - set(DROP_CATEGORIES))
    assert not unknown, 'drop category outside the schema: %s' % unknown

    return ReasonerResult(status if status in ('ok', 'no_candidates')
                          else 'unusable_reply',
                          kept, llm=result, dropped=dropped,
                          entries_in_reply=n_entries,
                          prompt_chars=len(prompt))


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - CLI
    """Print the brief or the prompt. Writes nothing, spawns nothing by default."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--paper-log', default=None)
    parser.add_argument('--n', type=int, default=DEFAULT_N_PROPOSALS)
    parser.add_argument('--show', choices=('brief', 'prompt', 'summary'),
                        default='summary')
    parser.add_argument('--run', action='store_true',
                        help='actually spawn the Opus turn and print the '
                             'candidate names it returned')
    args = parser.parse_args(argv)

    brief = gather_evidence(db_path=args.db, paper_log=args.paper_log)
    # Task 2.5: printed on every run, in every --show mode, so a human
    # scanning logs sees what THIS cycle actually read without opening the
    # brief. Always stderr so it never lands inside a --show=summary reader's
    # json.loads(stdout).
    print(brief['vault_ctx_line'], file=sys.stderr)
    if args.show == 'brief':
        print(render_brief(brief))
    elif args.show == 'prompt':
        print(build_prompt(brief, n_proposals=args.n))
    else:
        print(_json_block({
            'truncations': brief['truncations'],
            'hypothesis_graph': {
                k: v for k, v in brief['hypothesis_graph'].items()
                if k != 'shown'},
            'vault': {k: v for k, v in brief['vault'].items() if k != 'text'},
            'vault_ctx_line': brief['vault_ctx_line'],
            'shadow_status': brief['shadow'].get('status'),
            'prompt_chars': len(build_prompt(brief, n_proposals=args.n)),
        }))
    if args.run:
        outcome = reason(brief, n_proposals=args.n)
        print(_json_block(outcome.to_dict()))
    return 0


if __name__ == '__main__':  # pragma: no cover
    import sys
    sys.path.insert(0, ROOT)
    raise SystemExit(main())
