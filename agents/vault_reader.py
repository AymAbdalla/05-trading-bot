"""Read the Obsidian vault's Trading notes so an agent can learn from them.

The vault is the durable memory between Forge cycles. The critic writes lessons
into it after a shadow session; Forge reads them before proposing, so a
proposal that repeats a known-dead idea can be rejected by argument rather than
by rediscovering the loss.

This module only READS. `agents/vault_writer.py` is the only writer.

## The char budget is not a nicety

Everything here ends up inside a `claude -p` prompt. An unbounded vault read
grows without limit as the critic writes more lessons, and the prompt silently
becomes mostly stale notes. So every load takes a budget and reports what it
DROPPED (Convention 20: a silent truncation is a missing number). Newest notes
win, because a lesson written after a fix is more current than one written
before it.
"""
import os
import re
from typing import Any, Dict, List, Optional

VAULT_ROOT = os.path.expanduser('~/aym/vault/Trading')

LESSONS_DIR = os.path.join(VAULT_ROOT, 'Lessons')
BLOWUP_DIR = os.path.join(VAULT_ROOT, 'Blowup-Reports')
CARDS_DIR = os.path.join(VAULT_ROOT, 'Strategy-Cards')
CYCLES_DIR = os.path.join(VAULT_ROOT, 'Forge-Cycle-Summaries')

SECTIONS = (
    ('lessons', LESSONS_DIR),
    ('blowup_reports', BLOWUP_DIR),
    ('strategy_cards', CARDS_DIR),
    ('cycle_summaries', CYCLES_DIR),
)

# Per-section budget, characters.
#
# This started at 20,000, sized against Raven's hand-written notes, which ran
# about 1.5k each. The Opus-composed notes run 8k to 15k, so on the first real
# refresh the lessons section immediately blew the budget and silently (well,
# loudly: see `dropped`) lost the corridor lesson out of Forge's brief. A
# budget that quietly excludes half the evidence is worse than no budget,
# because the model then reasons confidently from a subset.
#
# Convention 17: this number is an assumption with an expiry date, and its
# expiry condition is written down. It expires when a section routinely
# reports `dropped`, which is exactly what happened to 20,000. Check
# `total_dropped` from `load_context()` before trusting a brief.
DEFAULT_BUDGET_CHARS = 60000

_STATUS = re.compile(r'^\*\*Status:\*\*\s*(.+)$', re.MULTILINE)
_FAILURE = re.compile(r'^\*\*Failure Mode:\*\*\s*(.+)$', re.MULTILINE)
_STRATEGY = re.compile(r'^\*\*Strategy:\*\*\s*(.+)$', re.MULTILINE)


class VaultNote(object):
    """One markdown note, with the few fields we parse out of it."""

    def __init__(self, path: str, text: str, mtime: float) -> None:
        self.path = path
        self.name = os.path.basename(path)
        self.text = text
        self.mtime = mtime
        self.title = self._first_heading(text)
        # The raw line, kept because it often carries a real qualifier
        # ("TESTED_FAILED (family verdict on 615 closed trades)").
        self.status = _first(_STATUS, text)
        self.failure_mode_raw = _first(_FAILURE, text)
        self.strategy = _first(_STRATEGY, text)
        # The bare token, for anything that GROUPS by failure mode.
        self.failure_mode = _token(self.failure_mode_raw)
        self.status_token = _token(self.status)

    @staticmethod
    def _first_heading(text: str) -> str:
        for line in text.splitlines():
            if line.startswith('# '):
                return line[2:].strip()
        return ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'title': self.title,
            'status': self.status,
            'status_token': self.status_token,
            'failure_mode': self.failure_mode,
            'failure_mode_raw': self.failure_mode_raw,
            'strategy': self.strategy,
            'chars': len(self.text),
        }


def _token(value: Optional[str]) -> Optional[str]:
    """The bare identifier at the front of a `**Failure Mode:**` line.

    A model writing a note will not write `spread_eats_edge` on its own. It
    writes ``` `spread_eats_edge` (confirmed by the inverse variant) ```,
    which is more useful to a human and useless as a dict key: two notes
    describing the SAME mode produce two different keys, and
    `known_failure_modes` then reports one occurrence each instead of two of
    one. That is a Convention 20 failure by accident, so the grouping key is
    normalised here and the prose is kept on `failure_mode_raw` rather than
    discarded.

    Returns None when nothing identifier-shaped is at the front, rather than
    inventing a token out of prose.
    """
    if not value:
        return None
    cleaned = value.strip().strip('`*_ ')
    match = re.match(r'[A-Za-z][A-Za-z0-9_]*', cleaned)
    return match.group(0) if match else None


def _first(pattern: 're.Pattern', text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def read_dir(path: str, budget_chars: int = DEFAULT_BUDGET_CHARS
             ) -> Dict[str, Any]:
    """Read every `.md` in `path`, newest first, up to `budget_chars`.

    Returns `{'dir', 'exists', 'notes', 'dropped', 'chars'}`. A missing
    directory is `exists=False` with zero notes, not an error: the vault
    legitimately has no Blowup-Reports until the first blowup.
    """
    out: Dict[str, Any] = {
        'dir': path, 'exists': os.path.isdir(path),
        'notes': [], 'dropped': [], 'chars': 0,
    }
    if not out['exists']:
        return out

    entries = []
    for name in os.listdir(path):
        if not name.endswith('.md'):
            continue
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        entries.append((os.path.getmtime(full), full))
    entries.sort(reverse=True)

    used = 0
    for mtime, full in entries:
        try:
            with open(full, 'r', encoding='utf-8') as handle:
                text = handle.read()
        except OSError as exc:
            out['dropped'].append({'name': os.path.basename(full),
                                   'reason': 'unreadable: %s' % exc})
            continue
        if used + len(text) > budget_chars and out['notes']:
            out['dropped'].append({'name': os.path.basename(full),
                                   'reason': 'over budget_chars'})
            continue
        out['notes'].append(VaultNote(full, text, mtime))
        used += len(text)
    out['chars'] = used
    return out


def load_context(budget_chars: int = DEFAULT_BUDGET_CHARS) -> Dict[str, Any]:
    """Read all four Trading sections."""
    context: Dict[str, Any] = {'vault_root': VAULT_ROOT, 'sections': {}}
    for key, path in SECTIONS:
        context['sections'][key] = read_dir(path, budget_chars=budget_chars)
    context['total_notes'] = sum(
        len(s['notes']) for s in context['sections'].values())
    context['total_chars'] = sum(
        s['chars'] for s in context['sections'].values())
    context['total_dropped'] = sum(
        len(s['dropped']) for s in context['sections'].values())
    return context


def render_context(context: Optional[Dict[str, Any]] = None,
                   budget_chars: int = DEFAULT_BUDGET_CHARS) -> str:
    """Turn the vault into a prompt block.

    Says out loud when a section is empty and when notes were dropped, so the
    model is not left to infer "no lessons" from silence.
    """
    if context is None:
        context = load_context(budget_chars=budget_chars)

    lines: List[str] = ['# OBSIDIAN VAULT: what we already learned',
                        '', 'Source: %s' % context['vault_root'], '']
    for key, _path in SECTIONS:
        section = context['sections'][key]
        heading = key.replace('_', ' ').upper()
        if not section['exists']:
            lines.append('## %s\n\n(directory does not exist yet - '
                         'nothing has been written here)\n' % heading)
            continue
        if not section['notes']:
            lines.append('## %s\n\n(empty)\n' % heading)
            continue
        lines.append('## %s (%d notes)\n' % (heading, len(section['notes'])))
        for note in section['notes']:
            lines.append('### %s' % note.name)
            lines.append(note.text.strip())
            lines.append('')
        if section['dropped']:
            lines.append('_NOTE: %d note(s) in this section were NOT included '
                         '(%s). Do not read their absence as "nothing there"._'
                         % (len(section['dropped']),
                            ', '.join(sorted({d['reason']
                                              for d in section['dropped']}))))
            lines.append('')
    return '\n'.join(lines)


def known_failure_modes(context: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, List[str]]:
    """`{failure_mode: [strategy, ...]}` as recorded in vault notes.

    Forge uses this to check that a proposal actually addresses something the
    record says went wrong, rather than asserting that it does.
    """
    if context is None:
        context = load_context()
    modes: Dict[str, List[str]] = {}
    for key, _path in SECTIONS:
        for note in context['sections'][key]['notes']:
            if not note.failure_mode:
                continue
            who = note.strategy or note.title or note.name
            modes.setdefault(note.failure_mode, [])
            if who not in modes[note.failure_mode]:
                modes[note.failure_mode].append(who)
    return modes


def main() -> int:  # pragma: no cover - CLI convenience
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-chars', type=int,
                        default=DEFAULT_BUDGET_CHARS)
    parser.add_argument('--render', action='store_true',
                        help='print the prompt block instead of a summary')
    args = parser.parse_args()
    context = load_context(budget_chars=args.budget_chars)
    if args.render:
        print(render_context(context))
        return 0
    summary = {
        'total_notes': context['total_notes'],
        'total_chars': context['total_chars'],
        'total_dropped': context['total_dropped'],
        'sections': {k: {'exists': v['exists'],
                         'notes': [n.to_dict() for n in v['notes']],
                         'dropped': v['dropped']}
                     for k, v in context['sections'].items()},
        'known_failure_modes': known_failure_modes(context),
    }
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
