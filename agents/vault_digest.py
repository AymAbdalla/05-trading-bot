"""The rolling write-time digest of Trading vault conclusions.

Forge's Opus turn used to read the whole vault tree on every reasoned cycle.
The vault grows roughly 5x per the 2026-08-18 cost audit, so every turn's read
cost compounded with it. This module is the fix on the WRITE side: every time
`agents/vault_writer.py` composes a real (non-fallback) lesson, strategy card,
blowup report or cycle summary, it appends a short CONCLUSION here instead of
making Forge re-read the full prose next time. `agents/forge_reasoner.py` is
the read side; it reads this file first (see its `load_vault()`).

## Why this is a single file, not one per note

A rolling digest that grows without bound just becomes the thing it replaced.
So this file is capped at `DIGEST_CAP_CHARS` (~10K). Past the cap, the OLDEST
entries move to `_DIGEST-ARCHIVE.md` rather than being deleted (Convention 20:
a silent drop is a missing number; here it would be a missing conclusion).

## Why the digest file uses `engine.concurrency.safe_edit`

Every note write is a read-modify-write on this ONE shared file, and multiple
sessions can be composing notes around the same time (Convention 21: this
working directory is shared). The individual note files vault_writer.py
writes have unique paths, so they need no coordination. This file does.

## What this module does NOT do

It does not call a model. Extraction of the three digest fields
(`format_entry`'s Verdict / Evidence / Relevance) happens by asking the SAME
Opus turn that already composed the note to end with a small structured
section (see `extract_fields`), so no second model call is spent on
summarizing the summary.
"""
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from agents import vault_reader
from engine import concurrency as C

DIGEST_FILENAME = '_DIGEST.md'
ARCHIVE_FILENAME = '_DIGEST-ARCHIVE.md'

# Convention 17: this number is an assumption with an expiry date. It expires
# when Forge's brief routinely reports the digest at its cap with entries
# being archived every cycle; at that point either the cap needs to grow or
# the per-entry format needs to shrink.
DIGEST_CAP_CHARS = 10000

DEFAULT_AGENT_ID = 'cody-vault-digest'

_ARCHIVE_POINTER = '(older entries archived to Trading/_DIGEST-ARCHIVE.md)'

# One entry: `## <date> <source note filename>` then the three bold lines.
_ENTRY_HEADING_RE = re.compile(r'^## (\d{4}-\d{2}-\d{2}) (\S.*)$', re.MULTILINE)

# What `agents/vault_writer.py` asks the model to emit inline in the note, so
# a digest entry can be extracted from a turn that already ran.
_DIGEST_BLOCK_RE = re.compile(
    r'##\s*Digest Entry\s*\n+'
    r'\*\*Verdict:\*\*\s*(?P<verdict>.+?)\s*\n'
    r'\*\*Evidence:\*\*\s*(?P<evidence>.+?)\s*\n'
    r'\*\*Relevance to Forge:\*\*\s*(?P<relevance>.+?)\s*(?:\n|$)',
    re.IGNORECASE)


def digest_path() -> str:
    """Resolved against `vault_reader.VAULT_ROOT` at call time, not import
    time, so tests that monkeypatch `VAULT_ROOT` also redirect this."""
    return os.path.join(vault_reader.VAULT_ROOT, DIGEST_FILENAME)


def archive_path() -> str:
    return os.path.join(vault_reader.VAULT_ROOT, ARCHIVE_FILENAME)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> str:
    return _now().strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_digest(path: Optional[str] = None) -> str:
    """The digest file's text, or '' if it does not exist yet.

    '' means "no digest", which callers must treat as the explicit fallback
    case (Task 2.4), not as "digest exists and is empty".
    """
    try:
        with open(path or digest_path(), 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


def parse_entries(text: str) -> List[str]:
    """Split the digest body into whole `## ...` entries, in file order."""
    body = text.strip('\n')
    body = re.sub(r'^\(older entries archived.*\)\n*', '', body)
    if not body.strip():
        return []
    starts = [m.start() for m in re.finditer(r'^## ', body, re.MULTILINE)]
    if not starts:
        return []
    starts.append(len(body))
    return [body[starts[i]:starts[i + 1]].rstrip('\n') + '\n'
            for i in range(len(starts) - 1)]


def digested_names(text: str) -> Set[str]:
    """`{source note filename, ...}` already covered by a digest entry."""
    return {m.group(2).strip() for m in _ENTRY_HEADING_RE.finditer(text)}


def newest_entry_date(text: str) -> Optional[str]:
    """The most recent `YYYY-MM-DD` among all entries, or None if empty."""
    dates = [m.group(1) for m in _ENTRY_HEADING_RE.finditer(text)]
    return max(dates) if dates else None


def is_stale(newest_date: Optional[str], max_age_days: int = 30,
             as_of: Optional[datetime] = None) -> bool:
    """True when the digest's newest entry is older than `max_age_days`.

    A digest with no entries at all (`newest_date is None`) counts as stale:
    an empty digest that exists is not evidence the vault has nothing to say.
    """
    if newest_date is None:
        return True
    try:
        newest = datetime.strptime(newest_date, '%Y-%m-%d').replace(
            tzinfo=timezone.utc)
    except ValueError:
        return True
    now = as_of or _now()
    return (now - newest).days > max_age_days


def extract_fields(note_text: str) -> Optional[Dict[str, str]]:
    """Pull the `## Digest Entry` block a composed note is asked to carry.

    Returns None when the model did not include one (best-effort: a missing
    digest entry must never fail the note write that already succeeded).
    """
    match = _DIGEST_BLOCK_RE.search(note_text)
    if not match:
        return None
    return {
        'verdict': match.group('verdict').strip(),
        'evidence': match.group('evidence').strip(),
        'relevance': match.group('relevance').strip(),
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def format_entry(date: str, source_name: str, verdict: str, evidence: str,
                 relevance: str) -> str:
    return ('## %s %s\n'
            '**Verdict:** %s\n'
            '**Evidence:** %s\n'
            '**Relevance to Forge:** %s\n'
            % (date, source_name, verdict.strip(), evidence.strip(),
               relevance.strip()))


def _safe_edit_or_create(path: str, edit_fn, agent_id: str,
                         db_path: str = C.DEFAULT_DB_PATH) -> str:
    """`safe_edit`, but tolerant of the file not existing yet.

    `engine.concurrency.safe_edit` calls `checkout()` without
    `allow_missing=True`, so it raises `FileNotFoundError` on a brand new
    digest. Create it with `safe_write(must_be_new=True)` instead, and fall
    back to a normal `safe_edit` if another agent won the create race.
    """
    try:
        return C.safe_edit(path, edit_fn, agent_id=agent_id, db_path=db_path)
    except FileNotFoundError:
        content = edit_fn('')
        try:
            C.safe_write(path, content, agent_id=agent_id, db_path=db_path,
                         must_be_new=True)
            return content
        except C.ConcurrentModificationError:
            return C.safe_edit(path, edit_fn, agent_id=agent_id,
                               db_path=db_path)


def _append_and_cap(current: str, entry_text: str, cap_chars: int,
                    archive_path_: str, agent_id: str, db_path: str) -> str:
    entries = parse_entries(current)
    entries.append(entry_text)

    archived: List[str] = []
    while len(entries) > 1:
        body_len = sum(len(e) for e in entries)
        header_len = len(_ARCHIVE_POINTER) + 2 if archived else 0
        if body_len + header_len <= cap_chars:
            break
        archived.append(entries.pop(0))

    if archived:
        _archive(archived, archive_path_, agent_id, db_path)

    prefix = (_ARCHIVE_POINTER + '\n\n') if archived else ''
    return prefix + ''.join(entries)


def _archive(entries: List[str], archive_path_: str, agent_id: str,
            db_path: str) -> None:
    def _edit(current: str) -> str:
        return current + ('' if not current or current.endswith('\n')
                          else '\n') + ''.join(entries)
    _safe_edit_or_create(archive_path_, _edit, agent_id, db_path=db_path)


def append_entry(entry_text: str, *, cap_chars: int = DIGEST_CAP_CHARS,
                 digest_path_: Optional[str] = None,
                 archive_path_: Optional[str] = None,
                 agent_id: str = DEFAULT_AGENT_ID,
                 db_path: str = C.DEFAULT_DB_PATH) -> str:
    """Append one entry to the digest, then enforce the cap.

    Past `cap_chars`, the OLDEST entries move to the archive file rather than
    being deleted. Returns the digest's new content.

    `db_path` is the coordination ledger (default `db/trading.db`, same as
    every other `engine.concurrency` caller in this repo), NOT the digest
    file itself. Tests pass a throwaway one so they never touch the real
    ledger.
    """
    resolved_digest = digest_path_ or digest_path()
    resolved_archive = archive_path_ or archive_path()

    def _edit(current: str) -> str:
        return _append_and_cap(current, entry_text, cap_chars,
                               resolved_archive, agent_id, db_path)

    return _safe_edit_or_create(resolved_digest, _edit, agent_id,
                                db_path=db_path)


def add_conclusion(source_name: str, verdict: str, evidence: str,
                   relevance: str, *, date: Optional[str] = None,
                   cap_chars: int = DIGEST_CAP_CHARS,
                   digest_path_: Optional[str] = None,
                   archive_path_: Optional[str] = None,
                   agent_id: str = DEFAULT_AGENT_ID,
                   db_path: str = C.DEFAULT_DB_PATH) -> str:
    """Convenience: format then append one conclusion."""
    entry = format_entry(date or today(), source_name, verdict, evidence,
                         relevance)
    return append_entry(entry, cap_chars=cap_chars, digest_path_=digest_path_,
                        archive_path_=archive_path_, agent_id=agent_id,
                        db_path=db_path)
