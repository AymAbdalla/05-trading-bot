"""`db/schema.sql` and the feed modules' `SCHEMA_SQL` must not drift apart.

Two copies of the same DDL exist on purpose:

  - `db/schema.sql` is what a FRESH database is built from.
  - `SCHEMA_SQL` inside `engine/feeds/*.py` is what lets a feed bootstrap its
    own storage against a database that predates the table.

Both are `CREATE ... IF NOT EXISTS`, so whichever runs first wins and the
second is a silent no-op. That is exactly the shape that hides a divergence:
if the module gains a column and `schema.sql` does not, then a db built by the
feed and a db built from `schema.sql` have DIFFERENT tables, the feed writes
fine on its own machine, and the mismatch only surfaces as an
`OperationalError` on a fresh checkout - or, worse, as a column that silently
reads NULL.

So the agreement is ASSERTED here rather than asked for in a comment
(convention 22: a claim in a docstring is not a wiring test). This test is
cited by the comment block above the tables in `db/schema.sql`.

Compared via `PRAGMA table_info` and `sqlite_master` rather than by string
equality, because whitespace and comment differences between the two copies
are legitimate; a different column, type, NOT NULL flag or index is not.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.concurrency import SCHEMA_SQL as COORDINATION_SCHEMA_SQL  # noqa: E402
from engine.feeds.hyperliquid_client import \
    SCHEMA_SQL as HYPERLIQUID_SCHEMA_SQL  # noqa: E402
from engine.feeds.liquidation_recorder import \
    SCHEMA_SQL as LIQUIDATION_SCHEMA_SQL  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, 'db', 'schema.sql')

#: (table, the module SCHEMA_SQL that also declares it)
#:
#: `file_coordination` is not a feed - it is agent coordination - but it has the
#: identical two-copy hazard these tests exist for: engine/concurrency.py
#: declares the DDL so it can bootstrap against a database that predates the
#: table, and both copies are CREATE ... IF NOT EXISTS, so whichever runs first
#: wins and the second is a SILENT no-op. A drift would surface only on a fresh
#: checkout, or as a column that quietly reads NULL.
FEED_TABLES = (
    ('liquidations', LIQUIDATION_SCHEMA_SQL),
    ('hyperliquid_positions', HYPERLIQUID_SCHEMA_SQL),
    ('file_coordination', COORDINATION_SCHEMA_SQL),
)


def _columns(conn, table):
    """(name, type, notnull) per column, in declaration order."""
    return [(r[1], r[2].upper(), r[3])
            for r in conn.execute('PRAGMA table_info(%s)' % table)]


def _indexes(conn, table):
    return sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND name NOT LIKE 'sqlite_%'", (table,)))


@pytest.fixture(scope='module')
def from_schema_file():
    with open(SCHEMA_PATH, encoding='utf-8') as fh:
        ddl = fh.read()
    conn = sqlite3.connect(':memory:')
    conn.executescript(ddl)
    yield conn
    conn.close()


@pytest.fixture(scope='module')
def from_modules():
    conn = sqlite3.connect(':memory:')
    for _, ddl in FEED_TABLES:
        conn.executescript(ddl)
    yield conn
    conn.close()


@pytest.mark.parametrize('table', [t for t, _ in FEED_TABLES])
def test_the_table_exists_in_schema_sql_at_all(from_schema_file, table):
    # The failure this whole file exists for: the module has the table, the
    # canonical schema does not, and only a fresh db notices.
    got = list(from_schema_file.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)))
    assert got, '%s is missing from db/schema.sql' % table


@pytest.mark.parametrize('table', [t for t, _ in FEED_TABLES])
def test_the_columns_agree(from_schema_file, from_modules, table):
    a = _columns(from_schema_file, table)
    b = _columns(from_modules, table)
    assert a, '%s produced no columns from db/schema.sql' % table
    assert a == b, (
        '%s differs between db/schema.sql and the module SCHEMA_SQL\n'
        '  schema.sql: %r\n  module    : %r' % (table, a, b))


@pytest.mark.parametrize('table', [t for t, _ in FEED_TABLES])
def test_the_indexes_agree(from_schema_file, from_modules, table):
    a = _indexes(from_schema_file, table)
    b = _indexes(from_modules, table)
    assert a, '%s has no indexes in db/schema.sql' % table
    assert a == b, (
        '%s indexes differ\n  schema.sql: %r\n  module: %r' % (table, a, b))


def test_schema_sql_is_idempotent():
    """Re-applying it must be a no-op, or a restart against a live db throws.

    The shadow loop and the feeds open the same file. A missing IF NOT EXISTS
    would turn an ordinary restart into an `OperationalError: table already
    exists`, which reads like data loss and is not.
    """
    with open(SCHEMA_PATH, encoding='utf-8') as fh:
        ddl = fh.read()
    conn = sqlite3.connect(':memory:')
    conn.executescript(ddl)
    conn.executescript(ddl)  # the assertion IS that this does not raise
    conn.close()


def test_the_feed_tables_did_not_displace_the_engine_tables(from_schema_file):
    """The control. Appending to schema.sql must not drop what was there."""
    names = {r[0] for r in from_schema_file.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for core in ('candles', 'signals', 'orders', 'fills', 'positions',
                 'equity_snapshots', 'strategy_registry', 'risk_events',
                 'audit_log'):
        assert core in names, core
