"""Hypothesis graph: a persistent world model of every strategy hypothesis tested.

WHAT THIS IS
------------
One table, `hypothesis_graph`, in `db/trading.db`. Every row is a claim about a
strategy - "this thing has edge" - together with what happened when it was
tested, and, when it failed, WHY it failed. The point is that a hypothesis that
died in the v0 sweep in August should not be re-proposed in September as if it
were new. `is_similar_to_failed()` is the query that stops that.

Three agents code against this module, so the public API is a CONTRACT. Do not
rename anything here without telling them.


TIMESTAMP UNIT: INTEGER MILLISECONDS SINCE THE UNIX EPOCH
---------------------------------------------------------
`ts` and `date_tested` are INTEGER MILLISECONDS, not seconds. This matches
`positions.opened_ts` (e.g. 1787022141000) and `liquidations.ts`, which are the
two tables this one is read beside. A ten-digit value in either column is a
SECONDS value that leaked in and is wrong by a factor of 1000. Use
`now_ms()` / `to_ms()` rather than writing `int(time.time())` at a call site.


WHICH GRAVEYARD FILE THIS READS, AND WHY
-----------------------------------------
`populate_from_graveyard()` streams `research/graveyard/v0_graveyard_full.json`
(389 MB, 535,425 entries, `generated` 2026-08-17 19:20:29).

That choice was made by elimination, not preference:

* `research/graveyard/summary.json` is AGGREGATE ONLY. It carries
  `verdict_counts`, `distinct_findings` and `not_tested_breakdown` for the whole
  sweep and has NO per-strategy breakdown of any kind. It cannot answer "did
  strategy X pass anywhere", which is the question this module exists to store.
  It IS read, as a cross-check on the totals, and its `graveyard` key is what
  names the raw file above.
* `research/judge_evidence_pack.json` has a per-strategy list (55 entries) with
  `n_rows_tested` / `n_rows_not_tested` / `asset_class` / `confidence`, but
  every `verdict` in it is `null` and it carries NO pass-row counts. It is read
  as an ENRICHMENT (asset class, confidence, cold-start flag), not as the
  verdict source.
* `research/graveyard/pooled.json` (`by_strategy`, 52 entries) has pooled trade
  counts, win rates and pnl-per-trade but again no pass/fail rows, and its
  README marks it PRE-PURGE (built 2026-08-13, against the graveyard that still
  had the 23,595 bad futures rows). It is read as an ENRICHMENT and is labelled
  stale in the evidence blob.
* `research/graveyard/asset_class.json` (138 cells) is strategy x asset class,
  also pre-purge, also no verdicts.
* `research/graveyard/v0_graveyard_R005_R006.json` is NOT read: the graveyard
  re-sweep is writing it right now, and reading a file mid-write would record a
  verdict against a partial run.

So the only file in the repo that actually carries a per-strategy verdict is the
raw sweep. It is streamed with `json.JSONDecoder.raw_decode` over a sliding
buffer (stdlib only, constant memory, ~3 s for the full 389 MB) rather than
`json.load`-ed, and only per-strategy AGGREGATES are kept in memory.


THE THREE VERDICT RULES (and convention 11)
--------------------------------------------
* FAIL on every tested row  -> TESTED_FAILED, with a failure_mode DERIVED from
  the measured columns (see `_derive_graveyard_failure_mode`).
* PASS on some rows but not all -> TESTED_CONDITIONAL, with the specific tickers
  named in `evidence['pass_tickers']`.
* NOT_TESTED -> UNTESTED, carrying the reason. NEVER TESTED_FAILED. Convention
  11: NOT_TESTED means "could not run", never "ran and found nothing". A
  strategy with both tested and not-tested rows gets TWO rows here: its verdict
  row, and a separate UNTESTED coverage row for the part that could not run.

Convention 2 is honoured in the evidence blob: `distinct_findings` (distinct
strategy x ticker x timeframe with a PASS) is recorded alongside
`raw_pass_rows`, and the notes tell the reader to cite the former.


CONVENTION 20 IN THE POPULATORS
--------------------------------
Every `populate_*` returns
`{'source','considered','inserted','updated','unchanged','skipped','skip_reasons',...}`
and asserts BOTH
`considered == inserted + updated + unchanged + skipped` and
`sum(skip_reasons.values()) == skipped`.
A silent `continue` in a filter loop is a missing number. Two different drop
causes never share one skip reason.


ZERO THIRD-PARTY IMPORTS
-------------------------
stdlib only. The similarity scorer is `difflib` plus a hand-rolled Jaccard, not
sklearn, not numpy, on purpose: this module has to import inside a shadow loop
that must not grow a dependency.

CLI
---
    env -u PYTHONPATH python3 -m agents.hypothesis_graph populate --source all
    env -u PYTHONPATH python3 -m agents.hypothesis_graph stats --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import string
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Absolute path to the live trading database.
DB_PATH: str = os.path.join(_REPO_ROOT, "db", "trading.db")

GRAVEYARD_DIR = os.path.join(_REPO_ROOT, "research", "graveyard")
GRAVEYARD_SUMMARY_PATH = os.path.join(GRAVEYARD_DIR, "summary.json")
GRAVEYARD_RAW_PATH = os.path.join(GRAVEYARD_DIR, "v0_graveyard_full.json")
POOLED_PATH = os.path.join(GRAVEYARD_DIR, "pooled.json")
JUDGE_PACK_PATH = os.path.join(_REPO_ROOT, "research", "judge_evidence_pack.json")
PROPOSALS_DIR = os.path.join(_REPO_ROOT, "strategies", "proposals")

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

STATUSES = (
    "TESTED_FAILED",
    "TESTED_CONFIRMED",
    "TESTED_CONDITIONAL",
    "UNTESTED",
)

REGIMES = ("bull", "bear", "sideways", "high_vol", "low_vol", "any")

FAILURE_MODES = (
    "spread_eats_edge",
    "model_miscalibrated",
    "never_fires",
    "stop_too_tight",
    "exit_too_early",
    "entry_signal_wrong",
    "regime_mismatch",
    "sample_too_small",
    "cost_exceeds_edge",
    "unclassified",
)

#: The only status that may carry a failure_mode.
FAILED_STATUS = "TESTED_FAILED"

#: What a TESTED_FAILED row gets when nobody named a mode. Never NULL.
DEFAULT_FAILURE_MODE = "unclassified"

_SOURCE_GRAVEYARD = "graveyard"
_SOURCE_SHADOW = "shadow"
_SOURCE_PROPOSAL = "proposal"

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hypothesis_graph (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    status TEXT NOT NULL,
    market_regime TEXT,
    asset_class TEXT,
    evidence_json TEXT,
    failure_mode TEXT,
    date_tested INTEGER,
    source TEXT,
    notes TEXT
)
"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_hypothesis_graph_strategy "
    "ON hypothesis_graph (strategy_name)",
    "CREATE INDEX IF NOT EXISTS idx_hypothesis_graph_status "
    "ON hypothesis_graph (status)",
    "CREATE INDEX IF NOT EXISTS idx_hypothesis_graph_source "
    "ON hypothesis_graph (source)",
)

_COLUMNS = (
    "id",
    "ts",
    "strategy_name",
    "hypothesis",
    "status",
    "market_regime",
    "asset_class",
    "evidence_json",
    "failure_mode",
    "date_tested",
    "source",
    "notes",
)

# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Hypothesis:
    """One row of `hypothesis_graph`, with `evidence_json` already parsed."""

    id: int
    ts: int
    strategy_name: str
    hypothesis: str
    status: str
    market_regime: Optional[str]
    asset_class: Optional[str]
    evidence: Dict[str, Any]
    failure_mode: Optional[str]
    date_tested: Optional[int]
    source: Optional[str]
    notes: Optional[str]


@dataclass(frozen=True)
class SimilarityMatch:
    """A stored hypothesis that resembles a candidate text.

    `score` is in 0..1 (see `_similarity`). `reason` is human-readable and names
    the matched strategy and what drove the score.
    """

    hypothesis: Hypothesis
    score: float
    reason: str


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


def now_ms() -> int:
    """Current wall time in INTEGER MILLISECONDS since epoch."""
    return int(time.time() * 1000)


def to_ms(value: Any) -> Optional[int]:
    """Coerce a datetime / epoch-seconds / epoch-ms value to epoch MILLISECONDS.

    A bare number below 1e11 is treated as SECONDS and multiplied by 1000; the
    boundary is year 5138 in seconds and 1973 in milliseconds, so no real
    trading timestamp is ambiguous.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = (
                    datetime.fromisoformat(text)
                    if fmt is None
                    else datetime.strptime(text, fmt)
                )
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        raise ValueError(f"cannot parse timestamp: {value!r}")
    number = int(value)
    return number * 1000 if abs(number) < 100_000_000_000 else number


# --------------------------------------------------------------------------
# JSON helpers (convention 19: json.loads is not strict; we write strictly)
# --------------------------------------------------------------------------


def _json_safe(obj: Any) -> Any:
    """Make `obj` safe for `json.dumps(..., allow_nan=False)`.

    Non-finite floats become the STRINGS 'inf' / '-inf' / 'nan'. Convention 12
    says a rate can legitimately be `inf`, so the information is preserved
    rather than nulled; convention 19 says it must not be written as the
    bare token `Infinity`, which `json.loads` would happily accept back.
    """
    if isinstance(obj, float):
        if math.isnan(obj):
            return "nan"
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in (sorted(obj) if isinstance(obj, set) else obj)]
    return obj


def dumps(obj: Any) -> str:
    """The one JSON writer this module uses. Strict, sorted, deterministic."""
    return json.dumps(_json_safe(obj), allow_nan=False, sort_keys=True)


def _encode_evidence(evidence: Optional[Dict[str, Any]]) -> str:
    """Canonical evidence_json. `None` and `{}` both normalise to `'{}'`.

    Normalising means an upsert that passes `None` after one that passed `{}`
    reports 'unchanged' rather than a phantom 'updated'.
    """
    return dumps(evidence or {})


def _decode_evidence(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {"_unparseable_evidence_json": str(raw)[:200]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


# --------------------------------------------------------------------------
# Connection / schema
# --------------------------------------------------------------------------


def connect(db_path: str = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Open `db_path`.

    `busy_timeout` is 5000 ms because the Polymarket shadow loop writes this
    same file continuously; a write here waits for it rather than raising
    'database is locked'. `read_only=True` opens a `file:...?mode=ro` URI, which
    the SQLite layer itself enforces - a stray INSERT raises instead of
    corrupting a live tape.
    """
    if read_only:
        uri = "file:{}?mode=ro".format(_uri_quote(os.path.abspath(db_path)))
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _uri_quote(path: str) -> str:
    """Percent-encode the characters SQLite's URI parser treats specially."""
    out = []
    for ch in path:
        if ch.isalnum() or ch in "/._-~":
            out.append(ch)
        else:
            out.append("%{:02X}".format(ord(ch)))
    return "".join(out)


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the table and its three indices. Idempotent, safe to call always.

    Only ever CREATE ... IF NOT EXISTS. Nothing in this module drops, alters or
    vacuums anything.
    """
    conn.execute(_CREATE_TABLE_SQL)
    for stmt in _CREATE_INDEX_SQL:
        conn.execute(stmt)
    conn.commit()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _validate_status(status: Any) -> str:
    if status not in STATUSES:
        raise ValueError(
            "invalid status {!r}: must be one of {}".format(status, list(STATUSES))
        )
    return status


def _validate_regime(market_regime: Any) -> Optional[str]:
    if market_regime is None:
        return None
    if market_regime not in REGIMES:
        raise ValueError(
            "invalid market_regime {!r}: must be one of {}".format(
                market_regime, list(REGIMES)
            )
        )
    return market_regime


def _validate_failure_mode(failure_mode: Any, status: str) -> Optional[str]:
    """Resolve the failure_mode for `status`, or raise ValueError.

    Two rules, both enforced here so no caller can bypass them:
      * TESTED_FAILED with no mode defaults to 'unclassified'. Never NULL - a
        NULL failure mode on a failed row is an unanswered question that looks
        like an answered one.
      * Any other status must NOT carry a mode. A 'CONDITIONAL because the
        sample was small' row would be a failure verdict wearing a
        non-failure label, which is exactly the mislabel convention 11 exists
        to prevent.
    """
    if failure_mode is not None and failure_mode not in FAILURE_MODES:
        raise ValueError(
            "invalid failure_mode {!r}: must be one of {}".format(
                failure_mode, list(FAILURE_MODES)
            )
        )
    if status == FAILED_STATUS:
        return failure_mode or DEFAULT_FAILURE_MODE
    if failure_mode is not None:
        raise ValueError(
            "failure_mode {!r} is not allowed on status {!r}: only {} may carry "
            "a failure_mode".format(failure_mode, status, FAILED_STATUS)
        )
    return None


def _validate_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "invalid {}: expected a non-empty string, got {!r}".format(
                field_name, value
            )
        )
    return value.strip()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def add_hypothesis(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    hypothesis: str,
    status: str,
    source: str,
    market_regime: str = "any",
    asset_class: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    failure_mode: Optional[str] = None,
    date_tested: Optional[int] = None,
    notes: Optional[str] = None,
    ts: Optional[int] = None,
) -> int:
    """Insert a new row unconditionally. Returns the new rowid.

    Use `upsert_hypothesis` when re-running a populator; this one always
    inserts and will happily create a duplicate.
    """
    ensure_table(conn)
    strategy_name = _validate_text(strategy_name, "strategy_name")
    hypothesis = _validate_text(hypothesis, "hypothesis")
    source = _validate_text(source, "source")
    status = _validate_status(status)
    market_regime = _validate_regime(market_regime)
    failure_mode = _validate_failure_mode(failure_mode, status)

    cur = conn.execute(
        "INSERT INTO hypothesis_graph "
        "(ts, strategy_name, hypothesis, status, market_regime, asset_class, "
        " evidence_json, failure_mode, date_tested, source, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(ts) if ts is not None else now_ms(),
            strategy_name,
            hypothesis,
            status,
            market_regime,
            asset_class,
            _encode_evidence(evidence),
            failure_mode,
            int(date_tested) if date_tested is not None else None,
            source,
            notes,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


#: The columns that define "the same hypothesis" for `upsert_hypothesis`.
UPSERT_IDENTITY = (
    "strategy_name",
    "hypothesis",
    "asset_class",
    "market_regime",
    "source",
)

#: The columns an upsert is allowed to change on an existing identity.
_MUTABLE = ("status", "evidence_json", "failure_mode", "date_tested", "notes")


def upsert_hypothesis(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    hypothesis: str,
    status: str,
    source: str,
    market_regime: str = "any",
    asset_class: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    failure_mode: Optional[str] = None,
    date_tested: Optional[int] = None,
    notes: Optional[str] = None,
    ts: Optional[int] = None,
) -> Tuple[int, str]:
    """Insert or update by identity. Returns `(rowid, action)`.

    `action` is one of 'inserted' | 'updated' | 'unchanged'.

    Identity is `(strategy_name, hypothesis, asset_class, market_regime,
    source)`, compared with SQL `IS` so a NULL asset_class matches a NULL
    asset_class rather than matching nothing.

    'unchanged' is a real, distinct outcome and it does NOT touch `ts`. A
    populator that re-runs against an unmoved graveyard must be able to say
    "nothing moved" and have that be visible in the counts, which is why
    `populate_*` reports inserted / updated / unchanged separately instead of
    one 'written' number.
    """
    ensure_table(conn)
    strategy_name = _validate_text(strategy_name, "strategy_name")
    hypothesis = _validate_text(hypothesis, "hypothesis")
    source = _validate_text(source, "source")
    status = _validate_status(status)
    market_regime = _validate_regime(market_regime)
    failure_mode = _validate_failure_mode(failure_mode, status)
    evidence_json = _encode_evidence(evidence)
    date_tested = int(date_tested) if date_tested is not None else None

    row = conn.execute(
        "SELECT * FROM hypothesis_graph WHERE strategy_name IS ? AND hypothesis IS ? "
        "AND asset_class IS ? AND market_regime IS ? AND source IS ? "
        "ORDER BY id LIMIT 1",
        (strategy_name, hypothesis, asset_class, market_regime, source),
    ).fetchone()

    if row is None:
        rowid = add_hypothesis(
            conn,
            strategy_name=strategy_name,
            hypothesis=hypothesis,
            status=status,
            source=source,
            market_regime=market_regime,
            asset_class=asset_class,
            evidence=evidence,
            failure_mode=failure_mode,
            date_tested=date_tested,
            notes=notes,
            ts=ts,
        )
        return rowid, "inserted"

    incoming = {
        "status": status,
        "evidence_json": evidence_json,
        "failure_mode": failure_mode,
        "date_tested": date_tested,
        "notes": notes,
    }
    if all(row[col] == incoming[col] for col in _MUTABLE):
        return int(row["id"]), "unchanged"

    conn.execute(
        "UPDATE hypothesis_graph SET ts=?, status=?, evidence_json=?, "
        "failure_mode=?, date_tested=?, notes=? WHERE id=?",
        (
            int(ts) if ts is not None else now_ms(),
            status,
            evidence_json,
            failure_mode,
            date_tested,
            notes,
            int(row["id"]),
        ),
    )
    conn.commit()
    return int(row["id"]), "updated"


def record_failure_mode(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    failure_mode: str,
    asset_class: Optional[str] = None,
    market_regime: str = "any",
    evidence: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
    hypothesis: Optional[str] = None,
    source: str = _SOURCE_SHADOW,
    date_tested: Optional[int] = None,
) -> int:
    """Record that `strategy_name` failed with `failure_mode`. Returns the rowid.

    Always writes status TESTED_FAILED, so `failure_mode` is mandatory and is
    validated against FAILURE_MODES. When `hypothesis` is omitted a stable
    default text is synthesised so the upsert identity is deterministic across
    re-runs.
    """
    if failure_mode is None:
        raise ValueError("failure_mode is required by record_failure_mode")
    text = hypothesis or "{} has exploitable edge (failed: {})".format(
        strategy_name, failure_mode
    )
    rowid, _action = upsert_hypothesis(
        conn,
        strategy_name=strategy_name,
        hypothesis=text,
        status=FAILED_STATUS,
        source=source,
        market_regime=market_regime,
        asset_class=asset_class,
        evidence=evidence,
        failure_mode=failure_mode,
        date_tested=date_tested,
        notes=notes,
    )
    return rowid


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def _row_to_hypothesis(row: sqlite3.Row) -> Hypothesis:
    return Hypothesis(
        id=int(row["id"]),
        ts=int(row["ts"]),
        strategy_name=row["strategy_name"],
        hypothesis=row["hypothesis"],
        status=row["status"],
        market_regime=row["market_regime"],
        asset_class=row["asset_class"],
        evidence=_decode_evidence(row["evidence_json"]),
        failure_mode=row["failure_mode"],
        date_tested=int(row["date_tested"]) if row["date_tested"] is not None else None,
        source=row["source"],
        notes=row["notes"],
    )


def _query(
    conn: sqlite3.Connection,
    *,
    status: Optional[str] = None,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
    source: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Hypothesis]:
    ensure_table(conn)
    clauses: List[str] = []
    params: List[Any] = []
    for col, value in (
        ("status", status),
        ("strategy_name", strategy_name),
        ("asset_class", asset_class),
        ("market_regime", market_regime),
        ("source", source),
    ):
        if value is not None:
            clauses.append("{} = ?".format(col))
            params.append(value)
    sql = "SELECT * FROM hypothesis_graph"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [_row_to_hypothesis(r) for r in conn.execute(sql, params)]


def all_hypotheses(
    conn: sqlite3.Connection,
    *,
    status: Optional[str] = None,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> List[Hypothesis]:
    """Every row, oldest id first, optionally filtered."""
    if status is not None:
        _validate_status(status)
    return _query(
        conn,
        status=status,
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
    )


def get_failed_hypotheses(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> List[Hypothesis]:
    """Every TESTED_FAILED row. This is the "do not re-propose" set."""
    return _query(
        conn,
        status="TESTED_FAILED",
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
    )


def get_confirmed_hypotheses(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> List[Hypothesis]:
    """Every TESTED_CONFIRMED row."""
    return _query(
        conn,
        status="TESTED_CONFIRMED",
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
    )


def get_conditional_hypotheses(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> List[Hypothesis]:
    """Every TESTED_CONDITIONAL row (passed somewhere, not everywhere)."""
    return _query(
        conn,
        status="TESTED_CONDITIONAL",
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
    )


def get_untested(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
) -> List[Hypothesis]:
    """Every UNTESTED row: proposals, and coverage that COULD NOT RUN.

    Convention 11 lives here. Nothing in this list is a negative result.
    """
    return _query(
        conn,
        status="UNTESTED",
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
    )


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------

#: Weight on difflib's character-level sequence ratio.
SEQUENCE_WEIGHT = 0.6
#: Weight on token-set Jaccard overlap.
JACCARD_WEIGHT = 0.4
#: Bounded pull toward 1.0 when the candidate names the same strategy.
SAME_STRATEGY_BOOST = 0.15

_PUNCT_TABLE = {ord(ch): " " for ch in string.punctuation}


def normalise_text(text: str) -> str:
    """Lowercase, replace punctuation with spaces, collapse whitespace.

    Deliberately keeps stop words. A stop-word list is a tuning knob nobody
    would re-measure, and convention 17 says a hardcoded threshold is an
    assumption with an expiry date; two hypotheses that differ only in stop
    words are in fact near-identical, which is what we want to catch.
    """
    return " ".join(str(text).lower().translate(_PUNCT_TABLE).split())


def _jaccard(a_tokens: set, b_tokens: set) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _similarity(
    candidate_text: str,
    stored: Hypothesis,
    *,
    strategy_name: Optional[str] = None,
) -> Tuple[float, str]:
    """Score `candidate_text` against a stored hypothesis. Returns (score, reason).

    THE FORMULA, stated once so nobody has to guess:

        a = normalise(candidate_text)
        b = normalise(stored.hypothesis)
        seq     = difflib.SequenceMatcher(None, a, b).ratio()      # 0..1
        jac     = |tokens(a) & tokens(b)| / |tokens(a) | tokens(b)| # 0..1
        blended = 0.6 * seq + 0.4 * jac
        score   = blended + 0.15 * (1 - blended)   if same strategy_name
                = blended                          otherwise

    The boost is multiplicative on the REMAINING headroom, not additive on the
    score, so it can never push past 1.0 and it moves a weak match far less
    than a strong one. Two components rather than one because they fail in
    opposite directions: `seq` is fooled by shared boilerplate phrasing and
    `jac` is fooled by reordering, and a text has to beat both to score high.
    """
    a = normalise_text(candidate_text)
    b = normalise_text(stored.hypothesis)
    seq = SequenceMatcher(None, a, b).ratio()
    jac = _jaccard(set(a.split()), set(b.split()))
    blended = SEQUENCE_WEIGHT * seq + JACCARD_WEIGHT * jac

    same_strategy = bool(strategy_name) and strategy_name == stored.strategy_name
    score = blended
    if same_strategy:
        score = blended + SAME_STRATEGY_BOOST * (1.0 - blended)
    score = max(0.0, min(1.0, score))

    shared = sorted(set(a.split()) & set(b.split()))
    driver = "wording" if seq >= jac else "shared terms"
    reason = (
        "{:.2f} similar to {} hypothesis #{} on strategy '{}' "
        "(sequence {:.2f}, token overlap {:.2f}, blended {:.2f}{}); "
        "driven mostly by {}; {} shared term(s){}".format(
            score,
            stored.status,
            stored.id,
            stored.strategy_name,
            seq,
            jac,
            blended,
            ", +{:.2f} same-strategy boost".format(score - blended)
            if same_strategy
            else "",
            driver,
            len(shared),
            ": " + ", ".join(shared[:8]) if shared else "",
        )
    )
    return score, reason


def similar_failures(
    conn: sqlite3.Connection,
    hypothesis_text: str,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
    limit: int = 5,
    threshold: float = 0.0,
) -> List[SimilarityMatch]:
    """Rank TESTED_FAILED rows by resemblance to `hypothesis_text`.

    FILTER SEMANTICS - read this, it is not symmetric and that is deliberate:

      * `strategy_name` does NOT restrict the candidate set. It only supplies
        the same-strategy boost. Restricting on it would defeat the purpose:
        the value of this query is finding that SOMEONE ELSE'S strategy already
        died on your idea.
      * `asset_class` and `market_regime`, when given, DO restrict the
        candidate set. "Has this failed in CRYPTO" is a different question from
        "has this failed", and both are legitimate.

    Results are sorted by score descending, then by id ascending so ties are
    stable, filtered to `score >= threshold`, and truncated to `limit`.
    """
    failures = get_failed_hypotheses(
        conn, asset_class=asset_class, market_regime=market_regime
    )
    scored: List[SimilarityMatch] = []
    for stored in failures:
        score, reason = _similarity(
            hypothesis_text, stored, strategy_name=strategy_name
        )
        if score >= threshold:
            scored.append(SimilarityMatch(hypothesis=stored, score=score, reason=reason))
    scored.sort(key=lambda m: (-m.score, m.hypothesis.id))
    return scored[: int(limit)] if limit is not None else scored


def is_similar_to_failed(
    conn: sqlite3.Connection,
    hypothesis_text: str,
    *,
    strategy_name: Optional[str] = None,
    asset_class: Optional[str] = None,
    market_regime: Optional[str] = None,
    threshold: float = 0.55,
) -> Optional[SimilarityMatch]:
    """The single best failed match at or above `threshold`, else None.

    This is the gate an idea generator calls before spending a sweep on
    something the graveyard already answered. Same filter semantics as
    `similar_failures`.
    """
    matches = similar_failures(
        conn,
        hypothesis_text,
        strategy_name=strategy_name,
        asset_class=asset_class,
        market_regime=market_regime,
        limit=1,
        threshold=threshold,
    )
    return matches[0] if matches else None


# --------------------------------------------------------------------------
# Failure-mode analytics
# --------------------------------------------------------------------------


def failure_mode_counts(
    conn: sqlite3.Connection, *, strategy_name: Optional[str] = None
) -> Dict[Tuple[str, str], int]:
    """`{(strategy_name, failure_mode): count}` over TESTED_FAILED rows only.

    A failed row always has a mode (see `_validate_failure_mode`), so nothing
    silently drops out of this count.
    """
    ensure_table(conn)
    sql = (
        "SELECT strategy_name, failure_mode, COUNT(*) AS n FROM hypothesis_graph "
        "WHERE status = ? AND failure_mode IS NOT NULL"
    )
    params: List[Any] = [FAILED_STATUS]
    if strategy_name is not None:
        sql += " AND strategy_name = ?"
        params.append(strategy_name)
    sql += " GROUP BY strategy_name, failure_mode ORDER BY strategy_name, failure_mode"
    return {
        (row["strategy_name"], row["failure_mode"]): int(row["n"])
        for row in conn.execute(sql, params)
    }


def kill_recommendations(
    conn: sqlite3.Connection, *, threshold: int = 3
) -> List[Dict[str, Any]]:
    """Strategy x failure_mode pairs seen at least `threshold` times.

    A recommendation, not a verdict: convention 7 says a FAIL on 200k trades is
    a verdict and a FAIL on 1,700 is a shrug, and this function counts ROWS in
    the hypothesis graph, not trades. It says "this strategy keeps dying the
    same way", which is a reason to look, not a reason to retire.

    Sorted by count descending, then strategy name, then failure mode.
    `asset_classes` lists the distinct classes involved; a NULL asset_class
    appears as a trailing `None`.
    """
    ensure_table(conn)
    rows = conn.execute(
        "SELECT strategy_name, failure_mode, asset_class, id FROM hypothesis_graph "
        "WHERE status = ? AND failure_mode IS NOT NULL ORDER BY id",
        (FAILED_STATUS,),
    ).fetchall()

    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (row["strategy_name"], row["failure_mode"])
        bucket = grouped.setdefault(
            key,
            {
                "strategy_name": row["strategy_name"],
                "failure_mode": row["failure_mode"],
                "count": 0,
                "_classes": set(),
                "_has_null_class": False,
                "hypothesis_ids": [],
            },
        )
        bucket["count"] += 1
        if row["asset_class"] is None:
            bucket["_has_null_class"] = True
        else:
            bucket["_classes"].add(row["asset_class"])
        bucket["hypothesis_ids"].append(int(row["id"]))

    out: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        if bucket["count"] < int(threshold):
            continue
        classes: List[Any] = sorted(bucket.pop("_classes"))
        if bucket.pop("_has_null_class"):
            classes.append(None)
        bucket["asset_classes"] = classes
        out.append(bucket)
    out.sort(key=lambda d: (-d["count"], d["strategy_name"], d["failure_mode"]))
    return out


# --------------------------------------------------------------------------
# Populator plumbing: the convention-20 counter
# --------------------------------------------------------------------------


class _Tally:
    """Counts every candidate exactly once and asserts it at the end.

    Convention 20: a silent `continue` in a filter loop is a missing number.
    Nothing leaves a populator without passing through `record()` or `skip()`.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.considered = 0
        self.inserted = 0
        self.updated = 0
        self.unchanged = 0
        self.skipped = 0
        self.skip_reasons: Counter = Counter()
        self.extra: Dict[str, Any] = {}

    def consider(self, n: int = 1) -> None:
        self.considered += n

    def record(self, action: str) -> None:
        if action == "inserted":
            self.inserted += 1
        elif action == "updated":
            self.updated += 1
        elif action == "unchanged":
            self.unchanged += 1
        else:  # pragma: no cover - upsert_hypothesis returns only those three
            raise ValueError("unknown upsert action {!r}".format(action))

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] += 1

    def finish(self) -> Dict[str, Any]:
        assert self.considered == (
            self.inserted + self.updated + self.unchanged + self.skipped
        ), (
            "{} accounting identity broken: considered={} != "
            "inserted={} + updated={} + unchanged={} + skipped={}".format(
                self.source,
                self.considered,
                self.inserted,
                self.updated,
                self.unchanged,
                self.skipped,
            )
        )
        assert sum(self.skip_reasons.values()) == self.skipped, (
            "{} skip_reasons sum {} != skipped {}".format(
                self.source, sum(self.skip_reasons.values()), self.skipped
            )
        )
        out: Dict[str, Any] = {
            "source": self.source,
            "considered": self.considered,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "skip_reasons": dict(self.skip_reasons),
        }
        out.update(self.extra)
        return out


# --------------------------------------------------------------------------
# Source 1: the graveyard
# --------------------------------------------------------------------------


def _stream_json_array(
    path: str, key: str = "entries", *, chunk_size: int = 1 << 20
) -> Iterator[Dict[str, Any]]:
    """Yield objects from the top-level array at `path`'s `key`, in constant memory.

    `json.load` on a 389 MB file peaks near 4 GB of Python objects. This walks
    the file with `json.JSONDecoder.raw_decode` over a sliding window instead:
    stdlib only, no ijson, and it never holds more than one chunk plus one
    entry. Roughly 3 s for the full graveyard.
    """
    decoder = json.JSONDecoder()
    with open(path, "r", encoding="utf-8") as fh:
        buf = fh.read(chunk_size)
        marker = '"{}"'.format(key)
        pos = buf.find(marker)
        while pos < 0:
            more = fh.read(chunk_size)
            if not more:
                raise ValueError("no {!r} array found in {}".format(key, path))
            buf += more
            pos = buf.find(marker)
        idx = buf.index("[", pos) + 1

        while True:
            # Advance past separators, refilling if the window runs dry.
            while True:
                while idx < len(buf) and buf[idx] in " \t\r\n,":
                    idx += 1
                if idx < len(buf):
                    break
                more = fh.read(chunk_size)
                if not more:
                    return
                buf, idx = buf[idx:] + more, 0
            if buf[idx] == "]":
                return
            # Decode one entry, refilling until it is whole.
            while True:
                try:
                    obj, end = decoder.raw_decode(buf, idx)
                    break
                except ValueError:
                    more = fh.read(chunk_size)
                    if not more:
                        raise
                    buf, idx = buf[idx:] + more, 0
            yield obj
            idx = end
            if idx > chunk_size:  # keep the window bounded
                buf, idx = buf[idx:], 0


def _classify_not_tested_reason(reason: Optional[str]) -> str:
    """Bucket a raw `not_tested_reason` string into a stable family.

    The families match `summary.json`'s own `not_tested_breakdown` keys so the
    two can be cross-checked. Anything unrecognised keeps a truncated raw form
    rather than being folded into a catch-all - convention 20 again, two drop
    causes must not share one number.
    """
    if not reason:
        return "reason_not_recorded"
    low = str(reason).lower()
    if "bar" in low:
        return "insufficient_bars"
    if "unsizable" in low or "cap" in low or "size" in low:
        return "unsizable_at_cap"
    return "other:" + low[:40]


@dataclass
class _StrategyAgg:
    """Per-strategy aggregate over the raw graveyard. Bounded memory."""

    rows: int = 0
    fail: int = 0
    passed: int = 0
    pass_benchmark: int = 0
    not_tested: int = 0
    trades_total: int = 0
    rows_with_trades: int = 0
    rows_with_pf: int = 0
    pf_gt1: int = 0
    gross_pf_gt1: int = 0
    beats_twin: int = 0
    pass_findings: set = field(default_factory=set)
    pass_tickers: set = field(default_factory=set)
    tickers: set = field(default_factory=set)
    timeframes: set = field(default_factory=set)
    asset_classes: Counter = field(default_factory=Counter)
    not_tested_reasons: Counter = field(default_factory=Counter)


#: The graveyard's own pooling floor (`pooled.json: min_pooled_trades`). Below
#: this a FAIL is a shrug, not a verdict (convention 7).
GRAVEYARD_MIN_POOLED_TRADES = 150


def _derive_graveyard_failure_mode(agg: _StrategyAgg) -> Tuple[str, str]:
    """Derive a failure_mode from measured columns. Returns (mode, why).

    The ladder, in order, every rung measured rather than assumed:

      1. `trades_total == 0`               -> never_fires
      2. zero-trade rows >= 99% of rows    -> never_fires  (matches the
         graveyard README's `trade_count_sanity` assertion, which flags 8
         strategies at 99%+ zero)
      3. `trades_total < 150`              -> sample_too_small (the graveyard's
         own `min_pooled_trades`)
      4. gross PF > 1 on most priced rows but net PF > 1 on a minority
                                           -> cost_exceeds_edge
      5. gross PF > 1 on a minority        -> entry_signal_wrong (there was no
         edge BEFORE costs, so costs are not the story)
      6. anything else                     -> unclassified, never a guess
    """
    if agg.trades_total == 0:
        return "never_fires", "0 trades across {} rows".format(agg.rows)
    zero_frac = 1.0 - (agg.rows_with_trades / agg.rows if agg.rows else 0.0)
    if zero_frac >= 0.99:
        return (
            "never_fires",
            "{:.1%} of {} rows produced zero trades".format(zero_frac, agg.rows),
        )
    if agg.trades_total < GRAVEYARD_MIN_POOLED_TRADES:
        return (
            "sample_too_small",
            "{} pooled trades is below the graveyard floor of {}".format(
                agg.trades_total, GRAVEYARD_MIN_POOLED_TRADES
            ),
        )
    if agg.rows_with_pf:
        gross_frac = agg.gross_pf_gt1 / agg.rows_with_pf
        net_frac = agg.pf_gt1 / agg.rows_with_pf
        if gross_frac >= 0.5 and net_frac < 0.5:
            return (
                "cost_exceeds_edge",
                "gross PF > 1 on {:.1%} of priced rows but net PF > 1 on only "
                "{:.1%}".format(gross_frac, net_frac),
            )
        if gross_frac < 0.5:
            return (
                "entry_signal_wrong",
                "gross PF > 1 on only {:.1%} of priced rows, so there was no "
                "edge before costs".format(gross_frac),
            )
    return "unclassified", "no measured column discriminated a mode"


def _load_json_optional(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def populate_from_graveyard(
    conn: sqlite3.Connection,
    summary_path: Optional[str] = None,
    *,
    raw_path: Optional[str] = None,
    max_entries: Optional[int] = None,
) -> Dict[str, Any]:
    """Populate from the v0 sweep graveyard.

    `summary_path` names `research/graveyard/summary.json`; its `graveyard` key
    is what selects the raw file streamed for per-strategy verdicts (see the
    module docstring for why summary.json alone cannot do this job).
    `raw_path` overrides that, and `max_entries` caps the stream - both are
    OPTIONAL keyword-only extras for tests; the contract call
    `populate_from_graveyard(conn)` behaves exactly as specified.

    Emits up to two rows per strategy:
      * the verdict row (TESTED_FAILED / TESTED_CONDITIONAL / TESTED_CONFIRMED,
        or UNTESTED when NOTHING could be tested), and
      * a separate UNTESTED coverage row when some rows could not run.
    """
    tally = _Tally(_SOURCE_GRAVEYARD)
    summary_path = summary_path or GRAVEYARD_SUMMARY_PATH
    summary = _load_json_optional(summary_path)

    if raw_path is None:
        named = (summary or {}).get("graveyard") if isinstance(summary, dict) else None
        base = os.path.dirname(os.path.abspath(summary_path))
        raw_path = os.path.join(base, named) if named else GRAVEYARD_RAW_PATH

    tally.extra["summary_path"] = summary_path
    tally.extra["raw_path"] = raw_path
    tally.extra["summary_found"] = summary is not None

    if not os.path.exists(raw_path):
        tally.extra["error"] = (
            "raw graveyard {!r} not found; no per-strategy verdicts are "
            "available from summary.json alone (it is aggregate only), so "
            "NOTHING was written rather than a guess".format(raw_path)
        )
        return tally.finish()

    tally.extra["raw_bytes"] = os.path.getsize(raw_path)
    try:
        aggs, entries_seen = _aggregate_graveyard(raw_path, max_entries)
    except (OSError, ValueError, MemoryError) as exc:
        tally.extra["error"] = "could not stream {}: {}".format(raw_path, exc)
        return tally.finish()

    tally.extra["entries_streamed"] = entries_seen
    tally.extra["strategies_seen"] = len(aggs)
    if isinstance(summary, dict):
        tally.extra["summary_entries_total"] = summary.get("entries_total")
        tally.extra["summary_distinct_findings"] = summary.get("distinct_findings")

    # Enrichment sources. Neither carries a verdict; both are labelled.
    pooled_raw = _load_json_optional(POOLED_PATH) or {}
    pooled = {
        row["strategy"]: row
        for row in pooled_raw.get("by_strategy", [])
        if isinstance(row, dict) and row.get("strategy")
    }
    judge_raw = _load_json_optional(JUDGE_PACK_PATH) or {}
    judge = {
        row["strategy"]: row
        for row in judge_raw.get("strategies", [])
        if isinstance(row, dict) and row.get("strategy")
    }
    tally.extra["enrichment"] = {
        "pooled_json_strategies": len(pooled),
        "judge_pack_strategies": len(judge),
        "note": "pooled.json and judge_evidence_pack.json carry NO verdicts "
        "(every judge verdict is null) and pooled.json is pre-purge; they are "
        "enrichment only",
    }

    generated = None
    if isinstance(summary, dict):
        generated = summary.get("generated")
    date_tested = None
    try:
        date_tested = to_ms(generated) if generated else None
    except ValueError:
        date_tested = None
    if date_tested is None:
        date_tested = int(os.path.getmtime(raw_path) * 1000)

    verdict_counts: Counter = Counter()

    for name in sorted(aggs):
        agg = aggs[name]
        asset_class = (
            agg.asset_classes.most_common(1)[0][0] if agg.asset_classes else None
        )
        tested = agg.fail + agg.passed + agg.pass_benchmark
        distinct_findings = len(agg.pass_findings)

        base_evidence: Dict[str, Any] = {
            "rows": agg.rows,
            "rows_fail": agg.fail,
            "raw_pass_rows": agg.passed,
            "pass_benchmark_rows": agg.pass_benchmark,
            "rows_not_tested": agg.not_tested,
            "distinct_findings_strategy_x_ticker_x_timeframe": distinct_findings,
            "trades_total": agg.trades_total,
            "rows_with_trades": agg.rows_with_trades,
            "tickers_swept": len(agg.tickers),
            "timeframes_swept": sorted(agg.timeframes),
            "asset_class_rows": dict(agg.asset_classes),
            "graveyard_file": os.path.basename(raw_path),
            "cite": "cite distinct_findings, never raw_pass_rows (convention 2)",
        }
        if name in pooled:
            base_evidence["pooled_json_prepurge"] = pooled[name]
        if name in judge:
            base_evidence["judge_pack"] = {
                k: judge[name].get(k)
                for k in ("confidence", "status", "asset_class", "n_trades")
            }

        # --- record 1: the verdict on what COULD be tested -----------------
        tally.consider()
        if tested == 0:
            # Nothing ran at all. Convention 11: this is UNTESTED, full stop.
            if agg.not_tested == 0:
                tally.skip("strategy_had_no_rows_of_any_verdict")
            else:
                evidence = dict(base_evidence)
                evidence["not_tested_reasons"] = dict(agg.not_tested_reasons)
                _, action = upsert_hypothesis(
                    conn,
                    strategy_name=name,
                    hypothesis=_graveyard_hypothesis_text(name, asset_class),
                    status="UNTESTED",
                    source=_SOURCE_GRAVEYARD,
                    market_regime="any",
                    asset_class=asset_class,
                    evidence=evidence,
                    date_tested=date_tested,
                    notes=(
                        "NOT_TESTED on all {} sweep rows. Convention 11: this "
                        "means COULD NOT RUN, never 'ran and found nothing'. "
                        "Dominant reason: {}.".format(
                            agg.rows, _top_reason(agg.not_tested_reasons)
                        )
                    ),
                )
                tally.record(action)
                verdict_counts["UNTESTED"] += 1
        elif agg.passed > 0 and agg.fail == 0:
            evidence = dict(base_evidence)
            evidence["pass_tickers"] = sorted(agg.pass_tickers)
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_graveyard_hypothesis_text(name, asset_class),
                status="TESTED_CONFIRMED",
                source=_SOURCE_GRAVEYARD,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=date_tested,
                notes=(
                    "PASS on every tested row ({} rows, {} distinct findings). "
                    "Cite distinct_findings, not raw pass rows.".format(
                        tested, distinct_findings
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_CONFIRMED"] += 1
        elif agg.passed > 0:
            evidence = dict(base_evidence)
            evidence["pass_tickers"] = sorted(agg.pass_tickers)
            evidence["pass_findings"] = sorted(
                "{} {}".format(t, tf) for t, tf in agg.pass_findings
            )
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_graveyard_hypothesis_text(name, asset_class),
                status="TESTED_CONDITIONAL",
                source=_SOURCE_GRAVEYARD,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=date_tested,
                notes=(
                    "PASS on {} of {} tested rows, collapsing to {} distinct "
                    "finding(s) on ticker(s): {}. Failed on the other {} rows. "
                    "Cite distinct_findings (convention 2).".format(
                        agg.passed,
                        tested,
                        distinct_findings,
                        ", ".join(sorted(agg.pass_tickers)) or "none",
                        agg.fail,
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_CONDITIONAL"] += 1
        else:
            mode, why = _derive_graveyard_failure_mode(agg)
            evidence = dict(base_evidence)
            evidence["failure_mode_derivation"] = why
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_graveyard_hypothesis_text(name, asset_class),
                status="TESTED_FAILED",
                source=_SOURCE_GRAVEYARD,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                failure_mode=mode,
                date_tested=date_tested,
                notes=(
                    "FAIL on all {} tested rows across {} ticker(s). "
                    "failure_mode={} because {}.".format(
                        tested, len(agg.tickers), mode, why
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_FAILED"] += 1

        # --- record 2: the coverage that COULD NOT RUN ---------------------
        if agg.not_tested > 0 and tested > 0:
            tally.consider()
            evidence = dict(base_evidence)
            evidence["not_tested_reasons"] = dict(agg.not_tested_reasons)
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_graveyard_untested_text(name, asset_class),
                status="UNTESTED",
                source=_SOURCE_GRAVEYARD,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=date_tested,
                notes=(
                    "{} of {} sweep rows COULD NOT RUN (convention 11: not a "
                    "negative result). Dominant reason: {}.".format(
                        agg.not_tested, agg.rows, _top_reason(agg.not_tested_reasons)
                    )
                ),
            )
            tally.record(action)
            verdict_counts["UNTESTED_COVERAGE"] += 1

    tally.extra["verdicts"] = dict(verdict_counts)
    return tally.finish()


def _aggregate_graveyard(
    path: str, max_entries: Optional[int] = None
) -> Tuple[Dict[str, _StrategyAgg], int]:
    """Stream the raw graveyard into per-strategy aggregates.

    Returns `(aggregates, entries_streamed)`. `max_entries` is a test-only cap;
    a capped run is a PARTIAL read and the caller records it as such
    (`entries_streamed` in the return dict), never as a full sweep.
    """
    aggs: Dict[str, _StrategyAgg] = {}
    seen = 0
    for entry in _stream_json_array(path, "entries"):
        seen += 1
        _fold_entry(aggs, entry)
        if max_entries is not None and seen >= max_entries:
            break
    return aggs, seen


def _fold_entry(aggs: Dict[str, _StrategyAgg], entry: Dict[str, Any]) -> None:
    """Fold one raw graveyard entry into the per-strategy aggregate."""
    name = entry.get("strategy")
    if not name:
        return
    agg = aggs.get(name)
    if agg is None:
        agg = aggs[name] = _StrategyAgg()
    agg.rows += 1
    verdict = entry.get("verdict")
    ticker = entry.get("ticker")
    timeframe = entry.get("timeframe")
    if ticker:
        agg.tickers.add(ticker)
    if timeframe:
        agg.timeframes.add(timeframe)
    if entry.get("asset_class"):
        agg.asset_classes[entry["asset_class"]] += 1
    if verdict == "FAIL":
        agg.fail += 1
    elif verdict == "PASS":
        agg.passed += 1
        agg.pass_findings.add((ticker, timeframe))
        if ticker:
            agg.pass_tickers.add(ticker)
    elif verdict == "PASS_BENCHMARK":
        agg.pass_benchmark += 1
    elif verdict == "NOT_TESTED":
        agg.not_tested += 1
        agg.not_tested_reasons[
            _classify_not_tested_reason(entry.get("not_tested_reason"))
        ] += 1
    trades = entry.get("trades") or 0
    agg.trades_total += int(trades)
    if trades:
        agg.rows_with_trades += 1
    pf = entry.get("pf")
    if pf is not None:
        agg.rows_with_pf += 1
        if pf > 1.0:
            agg.pf_gt1 += 1
    gross = entry.get("gross_pf")
    if gross is not None and gross > 1.0:
        agg.gross_pf_gt1 += 1
    if entry.get("beats_twin"):
        agg.beats_twin += 1


def _top_reason(counter: Counter) -> str:
    if not counter:
        return "none recorded"
    reason, n = counter.most_common(1)[0]
    return "{} ({} rows)".format(reason, n)


def _graveyard_hypothesis_text(name: str, asset_class: Optional[str]) -> str:
    return "{} has a net-positive expectancy edge on {} instruments".format(
        name, asset_class or "swept"
    )


def _graveyard_untested_text(name: str, asset_class: Optional[str]) -> str:
    return "{} on {} instruments has sweep coverage that could not be run".format(
        name, asset_class or "swept"
    )


# --------------------------------------------------------------------------
# Source 2: the live shadow tape
# --------------------------------------------------------------------------

#: Win-rate bands. `<40%` fails, `>60%` confirms, the closed interval
#: `[40%, 60%]` is conditional. Both boundaries land in CONDITIONAL on purpose:
#: a strategy sitting exactly on a threshold has not distinguished itself.
SHADOW_FAIL_WIN_RATE = 0.40
SHADOW_CONFIRM_WIN_RATE = 0.60


def _derive_shadow_failure_mode(stats: Dict[str, Any]) -> Tuple[str, str]:
    """Derive a failure_mode from the closed-position record. (mode, why).

    Ladder, in order:
      1. gross positive but net negative      -> cost_exceeds_edge
      2. >= 50% of exits are stop-like        -> stop_too_tight
      3. >= 50% of exits are target-like and net is still negative
                                              -> exit_too_early
      4. otherwise                            -> entry_signal_wrong
    """
    if stats["pnl_gross"] > 0 and stats["pnl_net"] <= 0:
        return (
            "cost_exceeds_edge",
            "gross {:+.2f} but net {:+.2f} after {:.2f} in fees".format(
                stats["pnl_gross"], stats["pnl_net"], stats["fees"]
            ),
        )
    n = stats["trades"]
    stop_frac = stats["stop_exits"] / n if n else 0.0
    target_frac = stats["target_exits"] / n if n else 0.0
    if stop_frac >= 0.5:
        return (
            "stop_too_tight",
            "{:.1%} of {} closes were stop-like exits".format(stop_frac, n),
        )
    if target_frac >= 0.5:
        return (
            "exit_too_early",
            "{:.1%} of {} closes hit a target yet net pnl is {:+.2f}".format(
                target_frac, n, stats["pnl_net"]
            ),
        )
    return (
        "entry_signal_wrong",
        "win rate {:.1%} over {} closes with no dominant exit pattern".format(
            stats["win_rate"], n
        ),
    )


_STOP_EXIT_TOKENS = ("stop",)
_TARGET_EXIT_TOKENS = ("target", "converged", "mean_reverted", "profit")


def populate_from_shadow(
    conn: sqlite3.Connection,
    db_path: Optional[str] = None,
    *,
    min_trades: int = 20,
) -> Dict[str, Any]:
    """Populate from CLOSED positions in `db_path` (default: the live DB).

    The source database is opened READ-ONLY. A position is CLOSED when
    `closed_ts IS NOT NULL`; there is no `status` column on `positions`.

    Verdict bands (see SHADOW_FAIL_WIN_RATE / SHADOW_CONFIRM_WIN_RATE), applied
    ONLY at or above `min_trades` closed trades:
        win rate < 40%          -> TESTED_FAILED (with a derived failure_mode)
        40% <= win rate <= 60%  -> TESTED_CONDITIONAL
        win rate > 60%          -> TESTED_CONFIRMED

    BELOW `min_trades` the row is UNTESTED, not CONDITIONAL. Two reasons, and
    both matter:
      * a non-failed status must not carry a failure_mode, so
        'CONDITIONAL + sample_too_small' is not expressible and would have to
        be faked;
      * convention 7 cuts both ways - a PASS on 87 trades is a shrug, so a
        verdict on 9 is noise wearing a verdict's clothes.
    `n` is written into BOTH the evidence and the notes on every single row,
    verdict or not, so no reader can quote a win rate without its sample size.

    A strategy with positions but ZERO closed ones is skipped and counted; it
    has not produced a single scoreable outcome.
    """
    tally = _Tally(_SOURCE_SHADOW)
    db_path = db_path or DB_PATH
    tally.extra["positions_db"] = db_path

    if not os.path.exists(db_path):
        tally.extra["error"] = "positions database {!r} not found".format(db_path)
        return tally.finish()

    src = connect(db_path, read_only=True)
    try:
        has_positions = src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()[0]
        if not has_positions:
            tally.extra["error"] = "no `positions` table in {!r}".format(db_path)
            return tally.finish()
        rows = src.execute(
            "SELECT strategy_id, pair, opened_ts, closed_ts, pnl_gross, pnl_net, "
            "fees, exit_reason FROM positions"
        ).fetchall()
    finally:
        src.close()

    grouped: Dict[str, Dict[str, Any]] = {}
    open_only: Counter = Counter()
    for row in rows:
        name = row["strategy_id"]
        if not name:
            continue
        bucket = grouped.setdefault(
            name,
            {
                "trades": 0,
                "wins": 0,
                "pnl_net": 0.0,
                "pnl_gross": 0.0,
                "fees": 0.0,
                "hold_ms_total": 0,
                "hold_n": 0,
                "stop_exits": 0,
                "target_exits": 0,
                "pairs": set(),
                "last_closed_ts": None,
                "open_positions": 0,
            },
        )
        if row["closed_ts"] is None:
            bucket["open_positions"] += 1
            open_only[name] += 1
            continue
        bucket["trades"] += 1
        net = row["pnl_net"] or 0.0
        bucket["pnl_net"] += net
        bucket["pnl_gross"] += row["pnl_gross"] or 0.0
        bucket["fees"] += row["fees"] or 0.0
        if net > 0:
            bucket["wins"] += 1
        if row["opened_ts"] is not None:
            bucket["hold_ms_total"] += int(row["closed_ts"]) - int(row["opened_ts"])
            bucket["hold_n"] += 1
        reason = (row["exit_reason"] or "").lower()
        if any(tok in reason for tok in _STOP_EXIT_TOKENS):
            bucket["stop_exits"] += 1
        elif any(tok in reason for tok in _TARGET_EXIT_TOKENS):
            bucket["target_exits"] += 1
        if row["pair"]:
            bucket["pairs"].add(row["pair"])
        prev = bucket["last_closed_ts"]
        ts_val = int(row["closed_ts"])
        bucket["last_closed_ts"] = ts_val if prev is None else max(prev, ts_val)

    tally.extra["strategies_with_positions"] = len(grouped)
    tally.extra["min_trades"] = int(min_trades)
    verdict_counts: Counter = Counter()

    for name in sorted(grouped):
        tally.consider()
        b = grouped[name]
        n = b["trades"]
        if n == 0:
            tally.skip("strategy_has_no_closed_positions")
            continue
        win_rate = b["wins"] / n
        mean_hold_ms = (b["hold_ms_total"] / b["hold_n"]) if b["hold_n"] else None
        stats = {
            "trades": n,
            "wins": b["wins"],
            "win_rate": win_rate,
            "pnl_net": b["pnl_net"],
            "pnl_gross": b["pnl_gross"],
            "fees": b["fees"],
            "stop_exits": b["stop_exits"],
            "target_exits": b["target_exits"],
        }
        asset_class = _shadow_asset_class(b["pairs"])
        evidence: Dict[str, Any] = {
            "n_closed_trades": n,
            "wins": b["wins"],
            "win_rate": round(win_rate, 4),
            "pnl_net_total": round(b["pnl_net"], 4),
            "pnl_net_mean": round(b["pnl_net"] / n, 6),
            "pnl_gross_total": round(b["pnl_gross"], 4),
            "fees_total": round(b["fees"], 4),
            "mean_hold_ms": round(mean_hold_ms, 1) if mean_hold_ms is not None else None,
            "mean_hold_minutes": round(mean_hold_ms / 60000.0, 3)
            if mean_hold_ms is not None
            else None,
            "open_positions_excluded": b["open_positions"],
            "distinct_markets": len(b["pairs"]),
            "stop_like_exits": b["stop_exits"],
            "target_like_exits": b["target_exits"],
            "min_trades_for_a_verdict": int(min_trades),
            "sample_caveat": "convention 7: a verdict on {} trades is worth "
            "exactly what {} trades buy".format(n, n),
        }
        n_clause = "n={} closed trades".format(n)

        if n < int(min_trades):
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_shadow_hypothesis_text(name),
                status="UNTESTED",
                source=_SOURCE_SHADOW,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=b["last_closed_ts"],
                notes=(
                    "UNTESTED: {} is below the {}-trade floor, so the {:.1%} "
                    "win rate is a sample, not a verdict. Recorded as UNTESTED "
                    "rather than CONDITIONAL because a non-failed status must "
                    "not carry a failure_mode, and 'sample_too_small' is a "
                    "failure mode.".format(n_clause, int(min_trades), win_rate)
                ),
            )
            tally.record(action)
            verdict_counts["UNTESTED"] += 1
            continue

        if win_rate < SHADOW_FAIL_WIN_RATE:
            mode, why = _derive_shadow_failure_mode(stats)
            evidence["failure_mode_derivation"] = why
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_shadow_hypothesis_text(name),
                status="TESTED_FAILED",
                source=_SOURCE_SHADOW,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                failure_mode=mode,
                date_tested=b["last_closed_ts"],
                notes=(
                    "FAILED: win rate {:.1%} < {:.0%} on {}, net {:+.2f}. "
                    "failure_mode={} because {}.".format(
                        win_rate,
                        SHADOW_FAIL_WIN_RATE,
                        n_clause,
                        b["pnl_net"],
                        mode,
                        why,
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_FAILED"] += 1
        elif win_rate > SHADOW_CONFIRM_WIN_RATE:
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_shadow_hypothesis_text(name),
                status="TESTED_CONFIRMED",
                source=_SOURCE_SHADOW,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=b["last_closed_ts"],
                notes=(
                    "CONFIRMED: win rate {:.1%} > {:.0%} on {}, net {:+.2f}. "
                    "Convention 7 still applies - {} is a small sample.".format(
                        win_rate, SHADOW_CONFIRM_WIN_RATE, n_clause, b["pnl_net"], n
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_CONFIRMED"] += 1
        else:
            _, action = upsert_hypothesis(
                conn,
                strategy_name=name,
                hypothesis=_shadow_hypothesis_text(name),
                status="TESTED_CONDITIONAL",
                source=_SOURCE_SHADOW,
                market_regime="any",
                asset_class=asset_class,
                evidence=evidence,
                date_tested=b["last_closed_ts"],
                notes=(
                    "CONDITIONAL: win rate {:.1%} sits inside [{:.0%}, {:.0%}] "
                    "on {}, net {:+.2f}. Neither confirmed nor killed.".format(
                        win_rate,
                        SHADOW_FAIL_WIN_RATE,
                        SHADOW_CONFIRM_WIN_RATE,
                        n_clause,
                        b["pnl_net"],
                    )
                ),
            )
            tally.record(action)
            verdict_counts["TESTED_CONDITIONAL"] += 1

    tally.extra["verdicts"] = dict(verdict_counts)
    tally.extra["open_only_positions"] = dict(open_only)
    return tally.finish()


def _shadow_hypothesis_text(name: str) -> str:
    return "{} produces net-positive closed trades in the shadow loop".format(name)


def _shadow_asset_class(pairs: Iterable[str]) -> Optional[str]:
    """Classify a strategy's markets. Polymarket slugs look like `btc-updown-5m-...`."""
    pairs = list(pairs)
    if not pairs:
        return None
    if all("updown" in p or p.startswith(("btc-", "eth-", "sol-")) for p in pairs):
        return "PREDICTION_MARKET"
    return "CRYPTO"


# --------------------------------------------------------------------------
# Source 3: forge proposals
# --------------------------------------------------------------------------

_FRONT_MATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def parse_front_matter(text: str) -> Optional[Dict[str, Any]]:
    """Parse the `---`-delimited YAML-ish header of a proposal.

    Deliberately NOT a YAML parser (no PyYAML - this module takes zero
    third-party imports). All 16 proposals in `strategies/proposals/` use the
    same flat shape: one `key: value` per line, values single- or double-quoted
    or bare, plus the bare tokens `null` / `true` / `false` and plain integers.
    A continuation line (one that does not start a new key) is appended to the
    previous value, so a wrapped string does not silently truncate.

    Returns None when there is no front matter at all.
    """
    if not text.startswith("---"):
        return None
    rest = text[3:]
    end = rest.find("\n---")
    if end < 0:
        return None
    out: Dict[str, Any] = {}
    last_key: Optional[str] = None
    for line in rest[:end].split("\n"):
        match = _FRONT_MATTER_KEY.match(line)
        if match:
            last_key = match.group(1)
            out[last_key] = _coerce_scalar(match.group(2))
        elif line.strip() and last_key is not None:
            out[last_key] = "{} {}".format(out[last_key], line.strip()).strip()
    return out


def _coerce_scalar(raw: str) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    low = raw.lower()
    if low in ("null", "~", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def populate_from_proposals(
    conn: sqlite3.Connection, proposals_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Populate UNTESTED rows from `strategies/proposals/`.

    Every proposal is an UNTESTED hypothesis by definition - it has been
    written, not run - so none of these ever carries a failure_mode.

    Every file in the directory is CONSIDERED, including the ones that are not
    proposals, and each is skipped under its OWN reason rather than one shared
    'skipped' bucket:
      * `not_a_markdown_file`   - forge_runs.jsonl, .gitkeep
      * `no_front_matter`       - README.md
      * `front_matter_missing_name`
      * `front_matter_missing_thesis`
    """
    tally = _Tally(_SOURCE_PROPOSAL)
    proposals_dir = proposals_dir or PROPOSALS_DIR
    tally.extra["proposals_dir"] = proposals_dir

    if not os.path.isdir(proposals_dir):
        tally.extra["error"] = "proposals directory {!r} not found".format(
            proposals_dir
        )
        return tally.finish()

    names = sorted(
        n for n in os.listdir(proposals_dir)
        if os.path.isfile(os.path.join(proposals_dir, n))
    )
    for name in names:
        tally.consider()
        path = os.path.join(proposals_dir, name)
        if not name.lower().endswith(".md"):
            tally.skip("not_a_markdown_file")
            continue
        try:
            text = open(path, "r", encoding="utf-8").read()
        except OSError:
            tally.skip("unreadable_file")
            continue
        front = parse_front_matter(text)
        if front is None:
            tally.skip("no_front_matter")
            continue
        strategy_name = front.get("name")
        if not strategy_name:
            tally.skip("front_matter_missing_name")
            continue
        thesis = front.get("thesis") or front.get("hypothesis")
        if not thesis:
            tally.skip("front_matter_missing_thesis")
            continue

        evidence = {
            "file": name,
            "expected_edge_bps": front.get("expected_edge_bps"),
            "kill_condition": front.get("kill_condition"),
            "entry_exit_rules": front.get("entry_exit_rules"),
            "data_requirements": front.get("data_requirements"),
            "related_graveyard_findings": front.get("related_graveyard_findings"),
            "kind": front.get("kind"),
            "proposal_status": front.get("status"),
            "proposal_source": front.get("source"),
            "forge_warnings": front.get("forge_warnings"),
        }
        kill = front.get("kill_condition")
        _, action = upsert_hypothesis(
            conn,
            strategy_name=str(strategy_name),
            hypothesis=str(thesis),
            status="UNTESTED",
            source=_SOURCE_PROPOSAL,
            market_regime="any",
            asset_class=front.get("asset_class"),
            evidence=evidence,
            date_tested=None,
            notes=(
                "Proposal {} ({}). UNTESTED: written, never run. "
                "Kill condition: {}".format(
                    name,
                    front.get("kind") or "unspecified kind",
                    (str(kill)[:400] + "...")
                    if kill and len(str(kill)) > 400
                    else (kill or "NONE STATED - convention 6 violation"),
                )
            ),
        )
        tally.record(action)

    return tally.finish()


# --------------------------------------------------------------------------
# populate_all
# --------------------------------------------------------------------------


def populate_all(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Run all three populators. Returns per-source results plus `totals`.

    `totals.skip_reasons` keys are PREFIXED with their source
    (`proposal:no_front_matter`) so two sources that happen to name a drop
    cause the same way never collapse into one number.
    """
    results = {
        "graveyard": populate_from_graveyard(conn),
        "shadow": populate_from_shadow(conn),
        "proposals": populate_from_proposals(conn),
    }
    totals = {
        "source": "all",
        "considered": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "skip_reasons": {},
    }
    for res in results.values():
        for key in ("considered", "inserted", "updated", "unchanged", "skipped"):
            totals[key] += res[key]
        for reason, count in res["skip_reasons"].items():
            key = "{}:{}".format(res["source"], reason)
            totals["skip_reasons"][key] = totals["skip_reasons"].get(key, 0) + count
    assert totals["considered"] == (
        totals["inserted"] + totals["updated"] + totals["unchanged"] + totals["skipped"]
    ), "populate_all totals do not balance: {}".format(totals)
    assert sum(totals["skip_reasons"].values()) == totals["skipped"], (
        "populate_all skip_reasons sum {} != skipped {}".format(
            sum(totals["skip_reasons"].values()), totals["skipped"]
        )
    )
    results["totals"] = totals
    return results


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Row counts by status, source, failure mode and asset class."""
    ensure_table(conn)

    def group(col: str) -> Dict[str, int]:
        return {
            (row[0] if row[0] is not None else "(null)"): int(row[1])
            for row in conn.execute(
                "SELECT {c}, COUNT(*) FROM hypothesis_graph GROUP BY {c} "
                "ORDER BY COUNT(*) DESC, {c}".format(c=col)
            )
        }

    total = int(conn.execute("SELECT COUNT(*) FROM hypothesis_graph").fetchone()[0])
    distinct = int(
        conn.execute(
            "SELECT COUNT(DISTINCT strategy_name) FROM hypothesis_graph"
        ).fetchone()[0]
    )
    return {
        "db_path": DB_PATH,
        "rows": total,
        "distinct_strategies": distinct,
        "by_status": group("status"),
        "by_source": group("source"),
        "by_failure_mode": group("failure_mode"),
        "by_asset_class": group("asset_class"),
        "by_market_regime": group("market_regime"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _hypothesis_to_dict(h: Hypothesis) -> Dict[str, Any]:
    return {
        "id": h.id,
        "ts": h.ts,
        "strategy_name": h.strategy_name,
        "hypothesis": h.hypothesis,
        "status": h.status,
        "market_regime": h.market_regime,
        "asset_class": h.asset_class,
        "evidence": h.evidence,
        "failure_mode": h.failure_mode,
        "date_tested": h.date_tested,
        "source": h.source,
        "notes": h.notes,
    }


def _print_rows(rows: List[Hypothesis]) -> None:
    if not rows:
        print("(no rows)")
        return
    for h in rows:
        print(
            "#{id:<5} {status:<19} {mode:<19} {name:<34} {ac}".format(
                id=h.id,
                status=h.status,
                mode=h.failure_mode or "-",
                name=h.strategy_name[:34],
                ac=h.asset_class or "-",
            )
        )
        print("      {}".format(h.hypothesis[:150]))
    print("\n{} row(s)".format(len(rows)))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agents.hypothesis_graph",
        description="Persistent world model of every strategy hypothesis tested.",
    )
    parser.add_argument("--db", default=DB_PATH, help="database path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pop = sub.add_parser("populate", help="populate from the evidence sources")
    p_pop.add_argument(
        "--source",
        choices=("graveyard", "shadow", "proposals", "all"),
        default="all",
    )
    p_pop.add_argument("--json", action="store_true")

    p_list = sub.add_parser("list", help="list rows")
    p_list.add_argument("--status", choices=list(STATUSES))
    p_list.add_argument("--strategy")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true")

    for name, helptext in (
        ("failed", "list TESTED_FAILED rows"),
        ("confirmed", "list TESTED_CONFIRMED rows"),
        ("untested", "list UNTESTED rows"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--strategy")
        sp.add_argument("--limit", type=int, default=50)
        sp.add_argument("--json", action="store_true")

    p_kill = sub.add_parser("kills", help="repeat failure modes worth a look")
    p_kill.add_argument("--threshold", type=int, default=3)
    p_kill.add_argument("--json", action="store_true")

    p_stats = sub.add_parser("stats", help="row counts")
    p_stats.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    conn = connect(args.db)
    try:
        ensure_table(conn)
        if args.cmd == "populate":
            if args.source == "all":
                result: Dict[str, Any] = populate_all(conn)
            elif args.source == "graveyard":
                result = populate_from_graveyard(conn)
            elif args.source == "shadow":
                result = populate_from_shadow(conn)
            else:
                result = populate_from_proposals(conn)
            if args.json:
                print(dumps(result))
            else:
                _print_populate(result)
            return 0

        if args.cmd in ("list", "failed", "confirmed", "untested"):
            status = {
                "failed": "TESTED_FAILED",
                "confirmed": "TESTED_CONFIRMED",
                "untested": "UNTESTED",
            }.get(args.cmd, getattr(args, "status", None))
            rows = _query(
                conn,
                status=status,
                strategy_name=getattr(args, "strategy", None),
                limit=args.limit,
            )
            if args.json:
                print(dumps([_hypothesis_to_dict(h) for h in rows]))
            else:
                _print_rows(rows)
            return 0

        if args.cmd == "kills":
            recs = kill_recommendations(conn, threshold=args.threshold)
            if args.json:
                print(dumps(recs))
            else:
                if not recs:
                    print("(no strategy/failure-mode pair at or above threshold "
                          "{})".format(args.threshold))
                for rec in recs:
                    print(
                        "{:<34} {:<19} x{}  classes={}  ids={}".format(
                            rec["strategy_name"],
                            rec["failure_mode"],
                            rec["count"],
                            rec["asset_classes"],
                            rec["hypothesis_ids"][:10],
                        )
                    )
            return 0

        if args.cmd == "stats":
            data = stats(conn)
            if args.json:
                print(dumps(data))
            else:
                print("rows: {}".format(data["rows"]))
                print("distinct strategies: {}".format(data["distinct_strategies"]))
                for label in (
                    "by_status",
                    "by_source",
                    "by_failure_mode",
                    "by_asset_class",
                ):
                    print("\n{}:".format(label))
                    for key, count in data[label].items():
                        print("  {:<24} {}".format(key, count))
            return 0
    finally:
        conn.close()
    return 0  # pragma: no cover


def _print_populate(result: Dict[str, Any]) -> None:
    blocks = (
        [(k, v) for k, v in result.items() if isinstance(v, dict) and "considered" in v]
        or [("result", result)]
    )
    for label, res in blocks:
        print(
            "{:<12} considered={} inserted={} updated={} unchanged={} skipped={}".format(
                label,
                res["considered"],
                res["inserted"],
                res["updated"],
                res["unchanged"],
                res["skipped"],
            )
        )
        if res["skip_reasons"]:
            for reason, count in sorted(res["skip_reasons"].items()):
                print("             skip {:<40} {}".format(reason, count))
        if res.get("verdicts"):
            print("             verdicts {}".format(res["verdicts"]))
        if res.get("error"):
            print("             ERROR {}".format(res["error"]))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
