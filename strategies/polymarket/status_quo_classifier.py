"""Status-quo classifier: the question SHAPE, never the probability.

Proposal 028 (`strategies/proposals/028-pm-status-quo-collector.md`). Read
`docs/handoffs/from-raven/2026-08-18-proposal-028.md` Task 1 first - this file
implements that task's rules exactly.

## The one job this file has

Given a market's question text (and, when available, its resolution date),
decide which of three SHAPES the question is:

    STATUS_QUO    - a dated statement that the world stays the same
                    ("X remains in power until <date>").
    CHANGE_EVENT  - a dated statement that something specific happens or
                    changes ("X resigns", "election result").
    UNKNOWN       - the honest default. Anything this file cannot place with
                    high confidence. UNKNOWN never trades - that gate lives in
                    `status_quo_collector.py`, not here, but this file's whole
                    reason to exist is to make that gate meaningful.

This is NOT a probability estimate. "Will Putin remain president until 2027"
is STATUS_QUO *shape* regardless of who actually wins. "Will Trump win the
2028 election" is CHANGE_EVENT shape even though "no change" (the incumbent
party keeps winning) is a live outcome - an election resolves to a change OR a
confirmation, and the question is framed around the CONTEST, not around
continuity, so it is never a status-quo contract. Confusing the two is exactly
the trap the reference wallet's one catastrophic loss came from (see the
proposal's `source` field): a question that LOOKED like a continuity bet and
was actually a regime-change bet with a continuity-shaped surface reading.

## Rule-based, deterministic, no model call (Task 1, rule 4)

Every branch below is a regex or a literal substring check. If a question
needs semantic judgement this file cannot make, it is UNKNOWN, not a prompt to
an LLM. A classifier whose institutional memory is "ask the model" cannot be
audited by grepping which rule fired, and this package's whole discipline
(convention 22: a claim in a docstring is not a wiring test) depends on being
able to point at the exact pattern that produced a label.

## Ordering is the whole design, read this before editing a pattern list

Checks run in a FIXED priority order and the first match wins:

  1. `NUMERIC_SHAPE_PATTERNS` - "what will be the price of X" is not a binary
     continuity question at all, regardless of any other word in it. Checked
     first so it can never be shadowed by a coincidental continuity keyword.
  2. `NEGATED_CHANGE_PHRASES` - "will NOT be removed" etc. These phrases
     contain a CHANGE-shaped word ("removed") but negate it into a continuity
     claim. Checked before the generic change patterns for exactly that
     reason: if this ran after them, "not be removed" would trip the bare
     `removed` pattern and mislabel a continuity question as CHANGE_EVENT.
  3. `CHANGE_EVENT_PATTERNS` - checked before ANY status-quo signal. This is
     the load-bearing ordering choice, and it exists to satisfy the handoff's
     explicit requirement: "No false STATUS_QUO on regime-change-shaped
     questions." A question like "Will the government remain in power after
     the coup, or will it be overthrown?" contains "remain in power" (a
     status-quo phrase) AND "overthrown" (a change trigger). If status-quo
     phrases were checked first, that question would be mislabelled
     STATUS_QUO on the strength of its own hedge clause. Change triggers run
     first so a regime-change-shaped question can never be rescued into
     STATUS_QUO by a continuity word appearing anywhere in its text.
  4. `STATUS_QUO_PHRASES` (multi-word) then `STATUS_QUO_KEYWORDS`
     (single-word, the exact list from the handoff: remain, stay, still,
     intact, continue, in power, in office, survives) - STATUS_QUO only if a
     DATE signal is also present (rule 5 below explains why).
  5. Anything left over is UNKNOWN, rule `no_clear_shape`. The honest default
     (Task 1, rule 3): nothing here forces an ambiguous question into
     STATUS_QUO.

## Why STATUS_QUO additionally requires a date signal

Task 1 rule 1 defines STATUS_QUO as "a DATED statement that the world stays
the same". A continuity keyword with no date attached ("Will the government
remain in power?", no horizon) is not the bounded, resolvable claim the
80-90c entry band prices - it is open-ended and this file will not guess a
horizon for it. `resolution_date` (the market's own end date, when the caller
has it - the strategy layer passes `ctx.market.end_date`) is authoritative
when given; otherwise this file falls back to finding a year or a month name
in the question text itself. A keyword hit with no date signal anywhere
degrades to UNKNOWN rather than STATUS_QUO, with its own rule name so the two
cases are never pooled (convention 20).
"""
import re
from dataclasses import dataclass
from typing import Optional

STATUS_QUO = 'STATUS_QUO'
CHANGE_EVENT = 'CHANGE_EVENT'
UNKNOWN = 'UNKNOWN'

#: Every label this file can produce. A classifier that returned a fourth
#: value would be a silent contract break for every caller that switches on
#: these three.
LABELS = (STATUS_QUO, CHANGE_EVENT, UNKNOWN)


@dataclass(frozen=True)
class Classification:
    """One classified question. `rule` names exactly which pattern fired, so
    a classification can be audited without re-reading this file's source."""

    label: str
    rule: str
    question: str

    @property
    def is_status_quo(self) -> bool:
        return self.label == STATUS_QUO


# ---------------------------------------------------------------------------
# 1. Numeric-shaped questions are never binary continuity, checked first.
# ---------------------------------------------------------------------------

NUMERIC_SHAPE_PATTERNS = (
    re.compile(r'\bwhat will (?:be|happen)\b', re.IGNORECASE),
    re.compile(r'\bhow (?:much|many)\b', re.IGNORECASE),
    re.compile(r'\b(?:price|value|level) of\b', re.IGNORECASE),
    re.compile(r'\babove\s+\$?\d', re.IGNORECASE),
    re.compile(r'\bbelow\s+\$?\d', re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# 2. Negation phrases that must be claimed BEFORE the generic change patterns
#    get a chance at the same word.
# ---------------------------------------------------------------------------

NEGATED_CHANGE_PHRASES = (
    re.compile(r'\bnot\s+be\s+removed\b', re.IGNORECASE),
    re.compile(r'\bnot\s+(?:be\s+)?removed\b', re.IGNORECASE),
    re.compile(r'\bwithout\s+being\s+removed\b', re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# 3. Change / event triggers. Checked before any status-quo signal - see the
#    module docstring's "Ordering is the whole design" section.
# ---------------------------------------------------------------------------

CHANGE_EVENT_PATTERNS = (
    ('win_election', re.compile(r'\bwin\b.{0,40}\belection\b', re.IGNORECASE)),
    ('hold_election', re.compile(r'\bhold\b.{0,20}\belection\b', re.IGNORECASE)),
    ('election_result', re.compile(r'\belection\s+result\b', re.IGNORECASE)),
    ('resign', re.compile(r'\bresign', re.IGNORECASE)),
    ('removed', re.compile(r'\bremov(?:e|ed|es|al)\b', re.IGNORECASE)),
    ('replaced', re.compile(r'\breplac(?:e|ed|es|ement)\b', re.IGNORECASE)),
    ('coup', re.compile(r'\bcoup\b', re.IGNORECASE)),
    ('impeach', re.compile(r'\bimpeach', re.IGNORECASE)),
    ('overthrow', re.compile(r'\boverthrow', re.IGNORECASE)),
    ('step_down', re.compile(r'\bsteps?\s+down\b', re.IGNORECASE)),
    ('ousted', re.compile(r'\boust(?:ed|er)?\b', re.IGNORECASE)),
    ('war_declared', re.compile(r'\bwar\b.{0,20}\bdeclar', re.IGNORECASE)),
)

# ---------------------------------------------------------------------------
# 4. Status-quo signals. Only reached once nothing above has matched.
# ---------------------------------------------------------------------------

#: Multi-word phrases, checked before the single-word keyword list so a
#: phrase's own wording is what gets attributed as the firing rule.
STATUS_QUO_PHRASES = (
    ('remain_intact', re.compile(r'\bremains?\s+intact\b', re.IGNORECASE)),
    ('remain_in_power',
     re.compile(r'\bremains?\s+in\s+power\b', re.IGNORECASE)),
    ('still_in_office',
     re.compile(r'\bstill\s+(?:be\s+)?in\s+office\b', re.IGNORECASE)),
    ('still_president',
     re.compile(r'\bstill\s+(?:be\s+)?president\b', re.IGNORECASE)),
)

#: The exact single-word list from the handoff (Task 1, rule 2): remain, stay,
#: still, intact, continue, in power, in office, survives. `\b`-bounded so
#: "remaining" and "remains" both hit "remain" without also matching unrelated
#: words that happen to contain the substring.
STATUS_QUO_KEYWORDS = (
    ('remain', re.compile(r'\bremains?\b', re.IGNORECASE)),
    ('stay', re.compile(r'\bstays?\b', re.IGNORECASE)),
    ('still', re.compile(r'\bstill\b', re.IGNORECASE)),
    ('intact', re.compile(r'\bintact\b', re.IGNORECASE)),
    ('continue', re.compile(r'\bcontinues?\b', re.IGNORECASE)),
    ('in_power', re.compile(r'\bin\s+power\b', re.IGNORECASE)),
    ('in_office', re.compile(r'\bin\s+office\b', re.IGNORECASE)),
    ('survives', re.compile(r'\bsurvives?\b', re.IGNORECASE)),
)

# ---------------------------------------------------------------------------
# 5. Date signal. Authoritative when the caller supplies `resolution_date`;
#    otherwise a year or a month name found in the question text itself.
# ---------------------------------------------------------------------------

DATE_TEXT_PATTERN = re.compile(
    r'\b(?:19|20)\d{2}\b'
    r'|\b(?:january|february|march|april|may|june|july|august|september'
    r'|october|november|december)\b',
    re.IGNORECASE)


def _has_date_signal(question: str, resolution_date: Optional[str]) -> bool:
    if resolution_date:
        return True
    return bool(DATE_TEXT_PATTERN.search(question))


def classify(question: str,
             resolution_date: Optional[str] = None) -> Classification:
    """Classify one market question. Never raises on ordinary input.

    `question` is the market's own question text. `resolution_date`, when the
    caller has it (`ctx.market.end_date` in the strategy layer), is used as
    the authoritative date signal instead of parsing the text for one - see
    the module docstring's date-signal section.
    """
    q = question or ''

    for pattern in NUMERIC_SHAPE_PATTERNS:
        if pattern.search(q):
            return Classification(UNKNOWN, 'numeric_shape', question)

    for pattern in NEGATED_CHANGE_PHRASES:
        if pattern.search(q):
            if _has_date_signal(q, resolution_date):
                return Classification(STATUS_QUO, 'negated_change_phrase',
                                      question)
            return Classification(UNKNOWN, 'negated_change_phrase_no_date',
                                  question)

    for rule, pattern in CHANGE_EVENT_PATTERNS:
        if pattern.search(q):
            return Classification(CHANGE_EVENT, rule, question)

    for rule, pattern in STATUS_QUO_PHRASES:
        if pattern.search(q):
            if _has_date_signal(q, resolution_date):
                return Classification(STATUS_QUO, 'phrase:{}'.format(rule),
                                      question)
            return Classification(UNKNOWN, 'phrase_no_date:{}'.format(rule),
                                  question)

    for rule, pattern in STATUS_QUO_KEYWORDS:
        if pattern.search(q):
            if _has_date_signal(q, resolution_date):
                return Classification(STATUS_QUO, 'keyword:{}'.format(rule),
                                      question)
            return Classification(UNKNOWN, 'keyword_no_date:{}'.format(rule),
                                  question)

    return Classification(UNKNOWN, 'no_clear_shape', question)
