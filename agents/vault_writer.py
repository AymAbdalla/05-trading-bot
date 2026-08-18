"""The only writer into the Obsidian vault's Trading notes, with model routing.

Raven's rule (2026-08-18): the vault stops holding hardcoded template prose.
A lesson, a blowup root cause, a strategy card, a cycle takeaway - those are
JUDGEMENTS, and a judgement written by a `.format()` call is a judgement nobody
made. Those route to Opus. A daily/weekly stats roll-up is not a judgement, it
is a table, so it routes to Sonnet.

Routing lives in `agents/llm_client.MODEL_FOR_TASK`; this module names tasks,
never models.

## The model does not hold the pen

Raven's instruction says "spawn Opus to write the report to the vault". We
spawn Opus to *compose* it and Python writes the file. Three reasons:

  1. We can validate before anything lands. A turn that came back empty, or
     that emitted a refusal instead of a report, must not overwrite a good
     note with nothing.
  2. The write is atomic (tmp + os.replace). A killed subprocess mid-Write
     leaves a half-note in the vault; this cannot.
  3. Every note gets a provenance header stating which model wrote it and
     when. A vault note that does not say it is model-written will be read as
     a human judgement six weeks from now.

Net effect is the same file in the same place, minus three failure modes.

## Nothing here may take down a caller

`shadow_runner` calls this from the blowup path. A blowup is exactly when you
least want a stack trace, so every public function returns a `VaultWrite`
describing what happened and raises only on programmer error (bad arguments).
When the model turn fails, the deterministic `fallback` text is written
instead and the note says, in the file, that the reasoning layer did not run.
Convention 11: that is NOT_TESTED, not "no root cause found".

## `skip_model`, and why it is NOT called `dry_run`

It was called `dry_run` for about two hours, and in that time a `--dry-run` of
`agents/critic.py` deposited a note built from synthetic test numbers into the
real `~/aym/vault/Trading/Forge-Cycle-Summaries/`. Nothing here was broken:
`dry_run` faithfully meant "skip the model, write the fallback". The name meant
"write nothing" to everyone who read it, which is the only meaning that
matters.

So the flag is `skip_model`, which says what it does. **It still writes a
file.** A caller that wants to write nothing must not call this module at all,
or must pass `out_dir` pointing somewhere disposable. `scripts/vault_refresh.py`
keeps `--dry-run` as a CLI alias for `--skip-model` with the trap spelled out
in its help text.

Every note-writing helper also takes `out_dir`, so a test can never reach the
real vault by accident.
"""
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from agents import llm_client, vault_digest
from agents.vault_reader import (  # noqa: F401  (re-exported for callers)
    BLOWUP_DIR, CARDS_DIR, CYCLES_DIR, LESSONS_DIR, VAULT_ROOT,
)

DAILY_DIR = os.path.join(VAULT_ROOT, 'Daily-Summaries')

# A real report is long. A one-line "I could not do that" is not. This is the
# floor below which we treat the turn as having produced nothing usable.
MIN_USEFUL_CHARS = 200

# Task 3 of the vault-digest work: a note over this cap gets its detailed
# evidence moved to an appendix, keeping thesis/verdict/pointer in the main
# note. Applies to new writes only; existing oversized notes are untouched.
NOTE_SIZE_CAP_CHARS = 10000

# The five "judgement" tasks - the ones write_* composes with Opus, not the
# mechanical `daily_summary` - are the ones eligible for a digest entry and
# for the size cap. Named here once so `compose_note` and `_prompt` agree.
DIGEST_ELIGIBLE_TASKS = frozenset({
    'blowup_root_cause', 'strategy_lesson', 'strategy_card',
    'forge_cycle_takeaway', 'critic_post_mortem',
})

# Phrases that mean the turn ran but declined. Written as a refusal check, not
# a quality check: we are not grading the prose, only catching a non-answer.
_NON_ANSWER = re.compile(
    r"^\s*(i('m| am)? (sorry|unable|not able)|i can(no|')t\b|"
    r"as an ai\b|dry_run:)", re.IGNORECASE)


class VaultWrite(object):
    """The outcome of one attempted vault note."""

    def __init__(self, path: str, written: bool, used_model: bool,
                 task: str, llm: Optional[llm_client.LLMResult] = None,
                 error: Optional[str] = None,
                 appendix_path: Optional[str] = None,
                 digest_updated: bool = False,
                 digest_skip_reason: Optional[str] = None) -> None:
        self.path = path
        self.written = written
        self.used_model = used_model
        self.task = task
        self.llm = llm
        self.error = error
        # Task 3: set when the note was over NOTE_SIZE_CAP_CHARS and its
        # detail was split into an appendix file.
        self.appendix_path = appendix_path
        # Task 1: whether this write appended a conclusion to _DIGEST.md.
        self.digest_updated = digest_updated
        self.digest_skip_reason = digest_skip_reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ('VaultWrite(path=%r, written=%r, used_model=%r, error=%r)'
                % (self.path, self.written, self.used_model, self.error))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            'path': self.path, 'written': self.written,
            'used_model': self.used_model, 'task': self.task,
            'error': self.error, 'appendix_path': self.appendix_path,
            'digest_updated': self.digest_updated,
            'digest_skip_reason': self.digest_skip_reason,
        }
        if self.llm is not None:
            out['llm'] = self.llm.to_dict()
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).strftime('%Y-%m-%dT%H:%M:%SZ')


def _slug(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')
    return slug or 'untitled'


def atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` so a reader never sees a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=directory, delete=False,
        prefix='.vault-', suffix='.tmp')
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        # NamedTemporaryFile creates 0600. The rest of the vault is 0644 and
        # Obsidian and every other reader expects that, so a note that only
        # this process can read would be a silent difference.
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _provenance(task: str, result: Optional[llm_client.LLMResult],
                fell_back: bool) -> str:
    """The header every note carries, so nobody mistakes it for a human note."""
    lines = ['---',
             'generated_by: agents/vault_writer.py',
             'task: %s' % task,
             'generated_at: %s' % _stamp()]
    if result is not None and not fell_back:
        lines.append('model: %s' % result.model)
        lines.append('model_seconds: %.1f' % result.duration_s)
    else:
        lines.append('model: NOT_TESTED')
        reason = (result.error if result is not None and result.error
                  else 'no model turn was attempted')
        lines.append('model_not_run_reason: %s' % reason.replace('\n', ' ')[:300])
    lines.append('---')
    lines.append('')
    if fell_back:
        lines.append('> **The reasoning layer did not run for this note.** '
                     'What follows is the deterministic fallback: the numbers '
                     'are real, the analysis is absent. Convention 11 - this '
                     'is NOT_TESTED, not "no cause found".')
        lines.append('')
    return '\n'.join(lines)


def _usable(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_USEFUL_CHARS:
        return False
    if _NON_ANSWER.match(stripped):
        return False
    return True


_SECTION_HEADING_RE = re.compile(r'^## ', re.MULTILINE)

_KNOWN_OUT_DIRS = (BLOWUP_DIR, CARDS_DIR, CYCLES_DIR, LESSONS_DIR)


def _title_of(body: str) -> str:
    for line in body.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return 'Untitled'


def _appendix_dir(out_dir: str) -> str:
    """Where a split note's appendix lands.

    In production `out_dir` is one of the four `VAULT_ROOT/<Section>`
    constants, so this resolves to the single shared `VAULT_ROOT/Appendices`
    the task spec names. A test that points `out_dir` at a throwaway tmp path
    (as `tests/test_vault_refresh.py` already does, to keep a synthetic note
    out of the real vault) gets a sibling `Appendices` under that same tmp
    path instead, never the real vault.
    """
    parent = os.path.dirname(os.path.normpath(out_dir))
    known_parents = {os.path.dirname(d) for d in _KNOWN_OUT_DIRS}
    if parent in known_parents:
        return os.path.join(parent, 'Appendices')
    return os.path.join(out_dir, 'Appendices')


def split_oversized_note(body: str, cap_chars: int = NOTE_SIZE_CAP_CHARS
                         ) -> Tuple[str, Optional[str]]:
    """Split `body` at a `## ` boundary once it exceeds `cap_chars`.

    Returns `(head, detail)`. `detail` is None when no split happened: the
    body fit, or it had no `## ` heading to cut on cleanly (nothing here
    ever cuts mid-section). The opening title and key-value block, which
    precedes the first `## `, always stays in `head`.
    """
    if len(body) <= cap_chars:
        return body, None
    starts = [m.start() for m in _SECTION_HEADING_RE.finditer(body)]
    cut = next((s for s in starts if s > cap_chars), None)
    if cut is None:
        return body, None
    return body[:cut].rstrip() + '\n', body[cut:].rstrip() + '\n'


def _write_appendix_if_oversized(body: str, out_path: str
                                 ) -> Tuple[str, Optional[str]]:
    """Apply Task 3's cap. Returns `(possibly-shortened body, appendix_path)`."""
    head, detail = split_oversized_note(body)
    if detail is None:
        return body, None
    note_stem = os.path.splitext(os.path.basename(out_path))[0]
    appendix_dir = _appendix_dir(os.path.dirname(out_path))
    appendix_path = os.path.join(appendix_dir, '%s-appendix.md' % note_stem)
    title = _title_of(body)
    appendix_body = (
        '# Appendix: %s\n\n'
        'Full evidence for the note `%s.md`, split out because the parent '
        'exceeded the %d-char cap.\n\n%s'
        % (title, note_stem, NOTE_SIZE_CAP_CHARS, detail))
    atomic_write(appendix_path, appendix_body)
    pointer = ('\n\n> **Full evidence:** the detailed evidence for this note '
              'was split out at the %d-char cap. See '
              '`Trading/Appendices/%s-appendix.md`.\n'
              % (NOTE_SIZE_CAP_CHARS, note_stem))
    return head.rstrip() + pointer, appendix_path


def _update_digest(task: str, out_path: str, body: str,
                   appendix_path: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Task 1's write-time hook. Returns `(digest_updated, skip_reason)`.

    Best-effort by design: a digest entry that could not be extracted or
    written must never fail the note write that already succeeded.
    """
    if task not in DIGEST_ELIGIBLE_TASKS:
        return False, 'task %r is not a judgement note' % task
    fields = vault_digest.extract_fields(body)
    if fields is None:
        return False, 'no ## Digest Entry block in the composed text'
    relevance = fields['relevance']
    if appendix_path is not None:
        relevance = ('%s Full evidence: `Trading/Appendices/%s`.'
                     % (relevance, os.path.basename(appendix_path)))
    try:
        vault_digest.add_conclusion(
            source_name=os.path.basename(out_path),
            verdict=fields['verdict'], evidence=fields['evidence'],
            relevance=relevance)
    except OSError as exc:
        return False, 'digest write failed: %s' % exc
    return True, None


def compose_note(task: str, prompt: str, out_path: str,
                 fallback: str,
                 timeout_s: int = llm_client.DEFAULT_TIMEOUT_S,
                 skip_model: bool = False) -> VaultWrite:
    """Ask the routed model to compose a note; write it, or write `fallback`.

    This is the single primitive. Every helper below is a prompt plus a
    fallback plus a filename.
    """
    if not fallback or not fallback.strip():
        raise ValueError('fallback text is required: a note must be writable '
                         'even when the model cannot run')

    if skip_model:
        body = fallback
        header = _provenance(task, None, fell_back=True)
        atomic_write(out_path, header + body)
        return VaultWrite(out_path, True, False, task,
                          error='skip_model: no model turn was attempted',
                          digest_skip_reason='skip_model: digest not touched')

    result = llm_client.run_task(task, prompt, timeout_s=timeout_s,
                                 allowed_tools=('Read',))

    if result.ok and _usable(result.text):
        header = _provenance(task, result, fell_back=False)
        body = result.text.strip()
        # Task 1: extract the digest conclusion BEFORE any split, so a note
        # whose "## Digest Entry" block happens to land late is still found.
        digest_updated, digest_skip_reason = False, None
        digest_fields_body = body
        body, appendix_path = _write_appendix_if_oversized(body, out_path)
        atomic_write(out_path, header + body + '\n')
        digest_updated, digest_skip_reason = _update_digest(
            task, out_path, digest_fields_body, appendix_path)
        return VaultWrite(out_path, True, True, task, llm=result,
                          appendix_path=appendix_path,
                          digest_updated=digest_updated,
                          digest_skip_reason=digest_skip_reason)

    error = result.error or (
        'model turn returned %d chars, below the %d-char floor, or was a '
        'non-answer' % (len(result.text.strip()), MIN_USEFUL_CHARS))
    header = _provenance(task, result, fell_back=True)
    try:
        atomic_write(out_path, header + fallback.strip() + '\n')
    except OSError as exc:
        return VaultWrite(out_path, False, False, task, llm=result,
                          error='%s; and the fallback write failed: %s'
                                % (error, exc),
                          digest_skip_reason='fallback write: digest not '
                                             'touched')
    return VaultWrite(out_path, True, False, task, llm=result, error=error,
                      digest_skip_reason='fallback note: NOT_TESTED reasoning '
                                         'is not a conclusion')


# --------------------------------------------------------------------------
# Named note types
# --------------------------------------------------------------------------

def write_blowup_report(blowup_num: int, starting_eq: float, ending_eq: float,
                        trades: int, pnl: float, duration_s: int,
                        per_strategy: Dict[str, Any],
                        trade_detail: str = '',
                        vault_context: str = '',
                        out_dir: Optional[str] = None,
                        skip_model: bool = False,
                        timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                        ) -> VaultWrite:
    """Root-cause a wiped shadow account.

    `per_strategy` is the dict `shadow_runner` already computes.
    `trade_detail` is optional raw per-trade text (exit reasons, hold times).
    """
    out_dir = out_dir or BLOWUP_DIR
    path = os.path.join(
        out_dir, 'blowup-%03d-%s.md'
        % (blowup_num, _now().strftime('%Y-%m-%d_%H-%M')))

    table = _strategy_table(per_strategy)
    facts = (
        'Blowup number: %d\n'
        'Starting equity: $%.2f\n'
        'Ending equity: $%.2f\n'
        'Closed trades in the account history: %d\n'
        'Total P&L: $%.2f\n'
        'Duration: %ds (%.1f hours)\n'
        % (blowup_num, starting_eq, ending_eq, trades, pnl,
           duration_s, duration_s / 3600.0))

    prompt = _prompt(
        role='You are the post-mortem analyst for a paper-trading bot that '
             'just wiped a $1,000 shadow account on Polymarket binary '
             'markets.',
        task='Write the blowup report as a markdown document.',
        body=[('THE FACTS', facts),
              ('PER-STRATEGY BREAKDOWN', table),
              ('PER-TRADE DETAIL', trade_detail or '(not supplied)'),
              ('WHAT THE VAULT ALREADY SAYS', vault_context or '(empty)')],
        instructions=[
            'Identify what specifically killed THIS account. Name the '
            'strategies and cite their actual numbers from the breakdown '
            'above. Do not assert a cause the numbers do not support.',
            'Distinguish the strategy that lost the most DOLLARS from the '
            'one with the worst per-trade economics - they are usually not '
            'the same, and the second is the one that generalises.',
            'If the evidence is consistent with more than one cause, say so '
            'and say what data would separate them.',
            'Do not repeat a lesson the vault already contains unless this '
            'blowup adds a number to it. If it contradicts a vault note, say '
            'so explicitly.',
            'End with a section "## Lessons for Forge" of concrete, '
            'falsifiable statements. Each must name a strategy or a mechanism '
            'and a number. "Avoid spreads" is useless; "a 1c profit target '
            'against a median 2c spread cannot clear costs at any win rate '
            'below 100%" is useful.',
            'Start the document with a `# Blowup #%d` heading.' % blowup_num,
        ],
    )

    fallback = ('# Blowup #%d: Shadow Account Wiped\n\n%s\n\n'
                '## Per-strategy breakdown\n\n%s\n' % (blowup_num, facts, table))
    return compose_note('blowup_root_cause', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


def write_strategy_lesson(strategy: str, evidence: str,
                          vault_context: str = '',
                          status: str = 'TESTED_FAILED',
                          failure_mode: str = '',
                          out_dir: Optional[str] = None,
                          filename: Optional[str] = None,
                          skip_model: bool = False,
                          timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                          ) -> VaultWrite:
    """Write (or rewrite) a lesson note about one strategy."""
    out_dir = out_dir or LESSONS_DIR
    name = filename or '%s-%s.md' % (_now().strftime('%Y-%m-%d'),
                                     _slug(strategy))
    path = os.path.join(out_dir, name)

    prompt = _prompt(
        role='You are the analyst who maintains the lessons file for a '
             'paper-trading bot. These notes are read by another model '
             '(Forge) before it proposes new strategies, so their only job '
             'is to stop a known-dead idea being proposed again.',
        task='Write the lesson note for strategy `%s` as markdown.' % strategy,
        body=[('EVIDENCE (real trade data)', evidence),
              ('WHAT THE VAULT ALREADY SAYS', vault_context or '(empty)')],
        instructions=[
            'Open with `# ` then a title, then bold key-value lines: '
            '**Date:**, **Status:** %s, %s**Strategy:** %s'
            % (status,
               ('**Failure Mode:** %s, ' % failure_mode) if failure_mode
               else '', strategy),
            'State the mechanism, not the outcome. "It lost money" is the '
            'observation; the lesson is WHY the mechanism cannot pay.',
            'Quote specific trades from the evidence: entry, exit, hold time, '
            'exit reason. A lesson with no example is not checkable.',
            'Include a section "## What NOT to propose again" that a future '
            'proposal can be tested against. Be specific enough that a '
            'proposal either does or does not violate it.',
            'Include a section "## What would change this verdict" naming a '
            'number and a measurement. If the sample is small, say the '
            'sample size and say the verdict is provisional (Convention 7: a '
            'FAIL on 200k trades is a verdict, on 1,700 a shrug).',
            'Do not invent numbers. Every figure must trace to the evidence '
            'block. If you need a number that is not there, say it is not '
            'measured.',
        ],
    )
    fallback = ('# Lesson: %s\n\n**Date:** %s\n**Status:** %s\n%s'
                '**Strategy:** %s\n\n## Evidence\n\n```\n%s\n```\n'
                % (strategy, _now().strftime('%Y-%m-%d'), status,
                   ('**Failure Mode:** %s\n' % failure_mode)
                   if failure_mode else '', strategy, evidence))
    return compose_note('strategy_lesson', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


def write_strategy_card(strategy: str, evidence: str,
                        vault_context: str = '',
                        out_dir: Optional[str] = None,
                        filename: Optional[str] = None,
                        skip_model: bool = False,
                        timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                        ) -> VaultWrite:
    """Write (or rewrite) the standing card for one strategy."""
    out_dir = out_dir or CARDS_DIR
    path = os.path.join(out_dir, filename or '%s.md' % _slug(strategy))

    prompt = _prompt(
        role='You maintain the strategy cards for a paper-trading bot. A card '
             'is the standing description of one strategy: what it claims, '
             'what it actually did, and whether it is alive.',
        task='Write the strategy card for `%s` as markdown.' % strategy,
        body=[('EVIDENCE (real trade data)', evidence),
              ('WHAT THE VAULT ALREADY SAYS', vault_context or '(empty)')],
        instructions=[
            'Start with `# Strategy Card: <name>`, then **Status:**, '
            '**Last Updated:**, and **Failure Mode:** if it has one.',
            'Sections: Thesis, Parameters, Results (a markdown table of real '
            'numbers), Assessment, Kill condition, What would revive it.',
            'The Assessment is the point of the card. Say plainly whether the '
            'thesis survived contact with the data, and separate "the signal '
            'is wrong" from "the signal is right and the execution cost eats '
            'it" - they have completely different fixes.',
            'The kill condition must contain a NUMBER and name a harness '
            '(Convention 6).',
            'Every number must trace to the evidence block. Mark anything '
            'unmeasured as NOT_MEASURED rather than estimating it.',
        ],
    )
    fallback = ('# Strategy Card: %s\n\n**Last Updated:** %s\n\n'
                '## Evidence\n\n```\n%s\n```\n'
                % (strategy, _now().strftime('%Y-%m-%d'), evidence))
    return compose_note('strategy_card', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


def write_cycle_summary(cycle_label: str, evidence: str,
                        vault_context: str = '',
                        out_dir: Optional[str] = None,
                        filename: Optional[str] = None,
                        skip_model: bool = False,
                        timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                        ) -> VaultWrite:
    """Write the takeaway note for one Forge cycle / shadow session."""
    out_dir = out_dir or CYCLES_DIR
    path = os.path.join(out_dir, filename or '%s-%s.md'
                        % (_now().strftime('%Y-%m-%d'), _slug(cycle_label)))

    prompt = _prompt(
        role='You write the cycle takeaway for a paper-trading bot. Forge '
             'reads this before its next round of proposals.',
        task='Write the cycle summary for `%s` as markdown.' % cycle_label,
        body=[('EVIDENCE (real trade data)', evidence),
              ('WHAT THE VAULT ALREADY SAYS', vault_context or '(empty)')],
        instructions=[
            'Sections: What worked, What failed, What we still do not know, '
            'What to try next, What NOT to try next.',
            '"What we still do not know" is mandatory and must not be empty. '
            'A cycle that answered everything did not measure enough.',
            'Separate a strategy that never fired from one that fired and '
            'lost. Convention 11: never-fired is NOT_TESTED, it is not a '
            'failure, and it must not be mined as evidence against the idea.',
            'Flag any conclusion resting on fewer than 30 trades as '
            'provisional and say so with the count (Convention 7).',
            'Every number must trace to the evidence block.',
        ],
    )
    fallback = ('# Cycle Summary: %s\n\n**Date:** %s\n\n## Evidence\n\n'
                '```\n%s\n```\n'
                % (cycle_label, _now().strftime('%Y-%m-%d'), evidence))
    return compose_note('forge_cycle_takeaway', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


def write_post_mortem(label: str, evidence: str, vault_context: str = '',
                      out_dir: Optional[str] = None,
                      filename: Optional[str] = None,
                      skip_model: bool = False,
                      timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                      ) -> VaultWrite:
    """The critic's post-mortem over a review window."""
    out_dir = out_dir or CYCLES_DIR
    path = os.path.join(out_dir, filename or '%s-critic-%s.md'
                        % (_now().strftime('%Y-%m-%d'), _slug(label)))

    prompt = _prompt(
        role='You are the critic for a paper-trading bot. You have just been '
             'handed every losing trade from a review window, already '
             'classified into failure modes by a deterministic classifier.',
        task='Write the post-mortem for review window `%s`.' % label,
        body=[('EVIDENCE (classified losing trades)', evidence),
              ('WHAT THE VAULT ALREADY SAYS', vault_context or '(empty)')],
        instructions=[
            'Your job is to check the classifier, not to trust it. Say where '
            'you think a classification is wrong and why.',
            'Group by MECHANISM, not by strategy name. Five strategies dying '
            'of one mechanism is one finding, not five.',
            'Say which findings are strong enough to act on and which are '
            'small-sample noise, with the counts.',
            'End with "## Recommended actions", each naming a strategy, an '
            'action (kill / repair / leave alone / measure), and the number '
            'that justifies it.',
            'Every number must trace to the evidence block.',
        ],
    )
    fallback = ('# Critic Post-Mortem: %s\n\n**Date:** %s\n\n'
                '## Classified evidence\n\n```\n%s\n```\n'
                % (label, _now().strftime('%Y-%m-%d'), evidence))
    return compose_note('critic_post_mortem', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


def write_daily_summary(day: str, evidence: str,
                        out_dir: Optional[str] = None,
                        skip_model: bool = False,
                        timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                        ) -> VaultWrite:
    """Mechanical roll-up. Sonnet: there is no judgement to make here."""
    out_dir = out_dir or DAILY_DIR
    path = os.path.join(out_dir, '%s-daily.md' % day)
    prompt = _prompt(
        role='You format trading statistics. You do not interpret them.',
        task='Write the daily summary for %s as markdown.' % day,
        body=[('DATA', evidence)],
        instructions=[
            'Tables and totals only. No causal claims, no advice, no '
            '"this suggests". Interpretation is another agent\'s job.',
            'Copy every number exactly. Do not round differently, do not '
            'recompute, do not fill a gap.',
        ],
        require_digest_entry=False,
    )
    fallback = '# Daily Summary: %s\n\n```\n%s\n```\n' % (day, evidence)
    return compose_note('daily_summary', prompt, path, fallback,
                        timeout_s=timeout_s, skip_model=skip_model)


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------

def _prompt(role: str, task: str, body, instructions,
           require_digest_entry: bool = True) -> str:
    """Assemble a prompt with one shape, so every note is asked for the same way.

    `require_digest_entry` adds the one instruction every judgement note
    (not the mechanical `daily_summary`) needs: a small structured section
    `agents/vault_digest.py` can extract without a second model call. It goes
    right after the opening title/key-value block, ahead of every other
    heading, so a note later split by Task 3's size cap always keeps it in
    the head half.
    """
    parts = [role, '', task, '']
    for heading, text in body:
        parts.append('=' * 70)
        parts.append('# %s' % heading)
        parts.append('=' * 70)
        parts.append(str(text).strip() or '(none)')
        parts.append('')
    parts.append('=' * 70)
    parts.append('# HOW TO WRITE IT')
    parts.append('=' * 70)
    numbered = list(instructions)
    if require_digest_entry:
        numbered.append(
            'Immediately after the opening title and key-value lines, before '
            'any other `## ` heading, include a `## Digest Entry` section '
            'with exactly these three bold lines and nothing else in that '
            'section: `**Verdict:**` one sentence, what was learned or '
            'decided. `**Evidence:**` the 1 to 3 numbers that carry it. '
            '`**Relevance to Forge:**` one sentence on why a future proposal '
            'must know this. This section is read by a program, not just a '
            'human, so use exactly these three labels and keep each to one '
            'line.')
    for i, line in enumerate(numbered, 1):
        parts.append('%d. %s' % (i, line))
    parts.append('')
    parts.append('OUTPUT: the markdown document and nothing else. No preamble, '
                 'no "here is the report", no closing commentary. Do not use '
                 'em-dashes or double hyphens. Do not create any files - your '
                 'stdout IS the document and it is written to disk verbatim.')
    return '\n'.join(parts)


def _strategy_table(per_strategy: Dict[str, Any]) -> str:
    """Markdown table of the per-strategy dict shadow_runner already builds."""
    if not per_strategy:
        return '(no per-strategy data)'
    rows = ['| strategy | trades | win rate | P&L |', '|---|---|---|---|']
    ordered = sorted(per_strategy.items(),
                     key=lambda kv: kv[1].get('pnl', 0.0))
    for name, stats in ordered:
        rows.append('| %s | %s | %s%% | $%s |'
                    % (name, stats.get('trades', '?'),
                       stats.get('win_rate', '?'), stats.get('pnl', '?')))
    return '\n'.join(rows)


# --------------------------------------------------------------------------
# Blowup note as a standalone job
# --------------------------------------------------------------------------
#
# `shadow_runner` must restart the shadow loop within seconds of a blowup. An
# Opus turn takes minutes. So the runner does NOT wait: it writes the row to
# `shadow_blowups` (immediate, deterministic) and then spawns THIS module
# detached to compose the note from that row.
#
# Making it a row-id job rather than an in-process call buys three things:
# the runner cannot be blocked by it, the note survives the runner being
# killed, and any past blowup can be re-analysed later by id without
# re-running anything.

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'db', 'trading.db')

# How many individual losing trades to hand the model. Enough to show the
# shape of the damage, bounded so the prompt cannot grow without limit.
TRADE_DETAIL_LIMIT = 60


def _connect_ro(db_path: str):
    """Read-only connection. The shadow loop is usually mid-write."""
    import sqlite3
    return sqlite3.connect('file:%s?mode=ro' % db_path, uri=True, timeout=10)


def trade_detail(db_path: str = DB_PATH,
                 limit: int = TRADE_DETAIL_LIMIT) -> str:
    """The worst individual trades, as raw rows for the model to read.

    The per-strategy table says WHICH strategy bled. Only the individual
    trades say HOW: whether the exits were stops or time-outs, how long the
    holds were, and whether the entry and exit prices are consistent with the
    spread eating the edge rather than the direction being wrong.
    """
    try:
        conn = _connect_ro(db_path)
    except Exception as exc:  # sqlite3.Error and friends
        return '(could not open %s read-only: %s)' % (db_path, exc)
    try:
        rows = conn.execute(
            'SELECT strategy_id, pair, entry_px, exit_px, qty, stop_px,'
            '       pnl_net, exit_reason, opened_ts, closed_ts '
            'FROM positions WHERE closed_ts IS NOT NULL '
            'ORDER BY pnl_net ASC LIMIT ?', (limit,)).fetchall()
        reasons = conn.execute(
            'SELECT exit_reason, COUNT(*), '
            '       ROUND(COALESCE(SUM(pnl_net), 0), 2) '
            'FROM positions WHERE closed_ts IS NOT NULL '
            'GROUP BY exit_reason ORDER BY COUNT(*) DESC').fetchall()
    except Exception as exc:
        return '(query failed: %s)' % exc
    finally:
        conn.close()

    if not rows:
        return '(no closed positions in %s)' % db_path

    out = ['Worst %d closed trades by net P&L:' % len(rows), '',
           '| strategy | pair | entry | exit | qty | stop | pnl_net '
           '| exit_reason | hold_s |', '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        hold = ''
        if r[8] is not None and r[9] is not None:
            # Timestamps are epoch milliseconds in this schema.
            hold = '%.1f' % ((r[9] - r[8]) / 1000.0)
        out.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
                   % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], hold))
    out.append('')
    out.append('Exit reasons across ALL closed trades:')
    out.append('')
    out.append('| exit_reason | count | total pnl_net |')
    out.append('|---|---|---|')
    for reason, count, pnl in reasons:
        out.append('| %s | %s | %s |' % (reason, count, pnl))
    return '\n'.join(out)


def blowup_note_from_db(blowup_id: Optional[int] = None,
                        db_path: str = DB_PATH,
                        skip_model: bool = False,
                        out_dir: Optional[str] = None,
                        timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
                        ) -> VaultWrite:
    """Compose the vault note for one row of `shadow_blowups`.

    `blowup_id=None` means the most recent row. `out_dir` exists so a test can
    point this at a temporary directory: a synthetic blowup row must never be
    able to drop a synthetic note into the real vault, because the vault is
    read back as evidence by Forge and by the critic.
    """
    import json as _json

    conn = _connect_ro(db_path)
    try:
        if blowup_id is None:
            row = conn.execute(
                'SELECT blowup_number, starting_equity, ending_equity,'
                '       total_trades, total_pnl, duration_seconds,'
                '       per_strategy_json '
                'FROM shadow_blowups ORDER BY id DESC LIMIT 1').fetchone()
        else:
            row = conn.execute(
                'SELECT blowup_number, starting_equity, ending_equity,'
                '       total_trades, total_pnl, duration_seconds,'
                '       per_strategy_json '
                'FROM shadow_blowups WHERE id = ?', (blowup_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError('no shadow_blowups row for id=%r in %s'
                         % (blowup_id, db_path))

    try:
        per_strategy = _json.loads(row[6] or '{}')
    except ValueError:
        per_strategy = {}

    # The vault context is best-effort: a missing vault must not stop the
    # report being written.
    try:
        from agents import vault_reader
        vault_context = vault_reader.render_context()
    except Exception as exc:  # pragma: no cover - defensive
        vault_context = '(vault unreadable: %s)' % exc

    return write_blowup_report(
        blowup_num=int(row[0]), starting_eq=float(row[1]),
        ending_eq=float(row[2]), trades=int(row[3]), pnl=float(row[4]),
        duration_s=int(row[5]), per_strategy=per_strategy,
        trade_detail=trade_detail(db_path=db_path),
        vault_context=vault_context, out_dir=out_dir, skip_model=skip_model,
        timeout_s=timeout_s)


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - CLI
    import argparse
    parser = argparse.ArgumentParser(
        description='Compose a vault note from data already in the database.')
    sub = parser.add_subparsers(dest='what', required=True)

    blowup = sub.add_parser('blowup', help='root-cause a shadow_blowups row')
    blowup.add_argument('--id', type=int, default=None,
                        help='shadow_blowups.id (default: most recent)')
    blowup.add_argument('--db', default=DB_PATH)
    blowup.add_argument('--dry-run', action='store_true',
                        help='write the deterministic fallback, no model turn')
    blowup.add_argument('--out-dir', default=None,
                        help='override the vault directory (for testing)')
    blowup.add_argument('--timeout', type=int,
                        default=llm_client.DEFAULT_TIMEOUT_S)

    args = parser.parse_args(argv)
    if args.what == 'blowup':
        result = blowup_note_from_db(blowup_id=args.id, db_path=args.db,
                                     skip_model=args.skip_model,
                                     out_dir=args.out_dir,
                                     timeout_s=args.timeout)
        print(llm_client.dump_json(result.to_dict()))
        return 0 if result.written else 1
    return 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
