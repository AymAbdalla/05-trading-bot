"""Wiring tests for agents/hypothesis_graph.py.

Convention 22: a claim in a docstring is not a wiring test. Everything the
module's docstring promises - the verdict ladder, the NOT_TESTED mapping, the
convention-20 accounting identity, the win-rate boundaries, the failure_mode
rules - is asserted here against a real SQLite file.

Everything that WRITES uses a `tmp_path` database. The live `db/trading.db` is
opened by exactly one test, READ-ONLY, through `populate_from_shadow`'s own
read-only connection, and that test writes its rows into a temp DB.

The parsers are tested against SYNTHETIC fixture files built in `tmp_path`, so
a re-sweep that changes the real graveyard cannot turn this file red. One
clearly-marked test (`TestAgainstRealRepoFiles`) additionally runs each
populator against the REAL repo files, and skips gracefully when a source is
absent.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from agents import hypothesis_graph as hg

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==========================================================================
# helpers
# ==========================================================================


@pytest.fixture()
def conn(tmp_path):
    """A fresh, empty hypothesis_graph database. Never the live one."""
    c = hg.connect(str(tmp_path / "hg.db"))
    hg.ensure_table(c)
    yield c
    c.close()


def _add(c, **kwargs):
    """add_hypothesis with the boring required fields defaulted."""
    kwargs.setdefault("strategy_name", "S")
    kwargs.setdefault("hypothesis", "h")
    kwargs.setdefault("status", "UNTESTED")
    kwargs.setdefault("source", "test")
    return hg.add_hypothesis(c, **kwargs)


def assert_identity(result):
    """The convention-20 accounting identity, asserted by the CALLER too.

    The populators assert this internally. Re-asserting it here is deliberate:
    an `assert` inside library code disappears under `python -O`, and this is
    the number that catches a silent `continue`.
    """
    assert result["considered"] == (
        result["inserted"]
        + result["updated"]
        + result["unchanged"]
        + result["skipped"]
    ), result
    assert sum(result["skip_reasons"].values()) == result["skipped"], result
    for key in ("source", "considered", "inserted", "updated", "unchanged",
                "skipped", "skip_reasons"):
        assert key in result, "missing required key {!r}: {}".format(key, result)


def write_graveyard(tmp_path, entries, *, generated="2026-08-17 19:20:29"):
    """Build a synthetic (summary.json, raw graveyard) pair. Returns summary path."""
    raw = tmp_path / "v0_synthetic.json"
    raw.write_text(
        json.dumps({"generated": generated, "entries": entries}, indent=1)
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"graveyard": raw.name, "generated": generated,
                    "entries_total": len(entries)})
    )
    return str(summary)


def gy_entry(strategy, verdict, **over):
    """One raw graveyard row with the real column set."""
    row = {
        "strategy": strategy,
        "ticker": over.pop("ticker", "AAA"),
        "timeframe": over.pop("timeframe", "15m"),
        "exit_config": "fixed_1r",
        "trades": 40,
        "pf": 0.8,
        "gross_pf": 0.9,
        "win_rate": 0.4,
        "verdict": verdict,
        "asset_class": "EQUITY",
        "beats_twin": False,
    }
    row.update(over)
    return row


def make_positions_db(tmp_path, rows, name="positions.db"):
    """Build a synthetic `positions` table matching the real schema."""
    path = str(tmp_path / name)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE positions (id TEXT PRIMARY KEY, pair TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, signal_id TEXT, opened_ts INTEGER NOT NULL, "
        "closed_ts INTEGER, entry_px REAL NOT NULL, exit_px REAL, "
        "qty REAL NOT NULL, stop_px REAL NOT NULL, target_px REAL NOT NULL, "
        "pnl_gross REAL, pnl_net REAL, fees REAL DEFAULT 0, r_multiple REAL, "
        "exit_reason TEXT, mode TEXT NOT NULL DEFAULT 'paper')"
    )
    db.executemany(
        "INSERT INTO positions (id, pair, strategy_id, opened_ts, closed_ts, "
        "entry_px, exit_px, qty, stop_px, target_px, pnl_gross, pnl_net, fees, "
        "exit_reason, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    db.close()
    return path


def closed_position(pid, strategy, pnl, *, exit_reason="sell:price_stop",
                    opened=1787022141000, closed=1787022441000, fees=0.0):
    return (
        str(pid), "btc-updown-5m-1787022000", strategy, opened, closed,
        0.5, 0.4, 10.0, 0.0, 1.0, pnl + fees, pnl, fees, exit_reason, "paper",
    )


def write_proposal(tmp_path, filename, **fields):
    body = ["---"]
    for key, value in fields.items():
        body.append('{}: "{}"'.format(key, value))
    body.append("---")
    body.append("")
    body.append("## body")
    (tmp_path / filename).write_text("\n".join(body))


# ==========================================================================
# schema
# ==========================================================================


class TestSchema:
    def test_ensure_table_creates_table_and_indices(self, tmp_path):
        c = hg.connect(str(tmp_path / "a.db"))
        hg.ensure_table(c)
        names = {
            r[0]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_hypothesis_graph_strategy" in names
        assert "idx_hypothesis_graph_status" in names
        assert "idx_hypothesis_graph_source" in names
        cols = [r[1] for r in c.execute("PRAGMA table_info(hypothesis_graph)")]
        assert cols == list(hg._COLUMNS)
        c.close()

    def test_ensure_table_is_idempotent_and_preserves_rows(self, tmp_path):
        c = hg.connect(str(tmp_path / "b.db"))
        hg.ensure_table(c)
        _add(c, strategy_name="keepme")
        for _ in range(3):
            hg.ensure_table(c)
        assert len(hg.all_hypotheses(c)) == 1
        c.close()

    def test_db_path_is_absolute_and_points_at_the_repo_db(self):
        assert os.path.isabs(hg.DB_PATH)
        assert hg.DB_PATH == os.path.join(REPO_ROOT, "db", "trading.db")

    def test_read_only_connection_refuses_writes(self, tmp_path):
        path = str(tmp_path / "ro.db")
        w = hg.connect(path)
        hg.ensure_table(w)
        w.close()
        r = hg.connect(path, read_only=True)
        with pytest.raises(sqlite3.OperationalError):
            r.execute(
                "INSERT INTO hypothesis_graph (ts, strategy_name, hypothesis, "
                "status) VALUES (1,'a','b','UNTESTED')"
            )
        r.close()

    def test_busy_timeout_is_set(self, tmp_path):
        c = hg.connect(str(tmp_path / "t.db"))
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        c.close()

    def test_timestamps_are_milliseconds(self, conn):
        rowid = _add(conn)
        ts = conn.execute(
            "SELECT ts FROM hypothesis_graph WHERE id=?", (rowid,)
        ).fetchone()[0]
        # 13 digits = ms. A 10-digit value would be seconds and wrong by 1000x.
        assert 1_600_000_000_000 < ts < 100_000_000_000_000


# ==========================================================================
# add / upsert
# ==========================================================================


class TestAddAndUpsert:
    def test_add_returns_rowid_and_round_trips(self, conn):
        rowid = hg.add_hypothesis(
            conn,
            strategy_name="alpha",
            hypothesis="edge exists",
            status="TESTED_CONDITIONAL",
            source="graveyard",
            market_regime="high_vol",
            asset_class="CRYPTO",
            evidence={"n": 5, "tickers": ["BTC"]},
            date_tested=1787022141000,
            notes="note",
        )
        h = hg.all_hypotheses(conn)[0]
        assert h.id == rowid
        assert h.strategy_name == "alpha"
        assert h.evidence == {"n": 5, "tickers": ["BTC"]}
        assert h.market_regime == "high_vol"
        assert h.date_tested == 1787022141000
        assert h.failure_mode is None

    def test_add_always_inserts_even_for_an_identical_row(self, conn):
        _add(conn)
        _add(conn)
        assert len(hg.all_hypotheses(conn)) == 2

    def test_upsert_inserts_then_reports_unchanged_then_updated(self, conn):
        kw = dict(
            strategy_name="alpha",
            hypothesis="edge exists",
            status="UNTESTED",
            source="proposal",
            asset_class="CRYPTO",
        )
        rid, action = hg.upsert_hypothesis(conn, **kw)
        assert action == "inserted"

        rid2, action = hg.upsert_hypothesis(conn, **kw)
        assert (rid2, action) == (rid, "unchanged")

        rid3, action = hg.upsert_hypothesis(conn, **dict(kw, status="TESTED_FAILED"))
        assert (rid3, action) == (rid, "updated")
        assert len(hg.all_hypotheses(conn)) == 1
        assert hg.all_hypotheses(conn)[0].status == "TESTED_FAILED"

    def test_unchanged_does_not_move_ts(self, conn):
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED",
                  source="s", ts=111_000_000_000_000)
        rid, _ = hg.upsert_hypothesis(conn, **kw)
        hg.upsert_hypothesis(conn, **dict(kw, ts=222_000_000_000_000))
        ts = conn.execute(
            "SELECT ts FROM hypothesis_graph WHERE id=?", (rid,)
        ).fetchone()[0]
        assert ts == 111_000_000_000_000

    def test_updated_moves_ts(self, conn):
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED",
                  source="s", ts=111_000_000_000_000)
        rid, _ = hg.upsert_hypothesis(conn, **kw)
        hg.upsert_hypothesis(
            conn, **dict(kw, status="TESTED_CONFIRMED", ts=222_000_000_000_000)
        )
        ts = conn.execute(
            "SELECT ts FROM hypothesis_graph WHERE id=?", (rid,)
        ).fetchone()[0]
        assert ts == 222_000_000_000_000

    @pytest.mark.parametrize(
        "field,value",
        [
            ("strategy_name", "other"),
            ("hypothesis", "other text"),
            ("asset_class", "EQUITY"),
            ("market_regime", "bear"),
            ("source", "shadow"),
        ],
    )
    def test_every_identity_column_creates_a_separate_row(self, conn, field, value):
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED",
                  source="s", asset_class="CRYPTO", market_regime="any")
        hg.upsert_hypothesis(conn, **kw)
        _, action = hg.upsert_hypothesis(conn, **dict(kw, **{field: value}))
        assert action == "inserted"
        assert len(hg.all_hypotheses(conn)) == 2

    def test_null_asset_class_matches_null_asset_class(self, conn):
        """SQL `=` would never match NULL; the identity lookup must use `IS`."""
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED",
                  source="s", asset_class=None)
        hg.upsert_hypothesis(conn, **kw)
        _, action = hg.upsert_hypothesis(conn, **kw)
        assert action == "unchanged"

    def test_evidence_none_and_empty_dict_are_the_same_row(self, conn):
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED", source="s")
        hg.upsert_hypothesis(conn, **kw, evidence=None)
        _, action = hg.upsert_hypothesis(conn, **kw, evidence={})
        assert action == "unchanged"

    def test_evidence_key_order_does_not_fake_an_update(self, conn):
        kw = dict(strategy_name="a", hypothesis="h", status="UNTESTED", source="s")
        hg.upsert_hypothesis(conn, **kw, evidence={"a": 1, "b": 2})
        _, action = hg.upsert_hypothesis(conn, **kw, evidence={"b": 2, "a": 1})
        assert action == "unchanged"


# ==========================================================================
# validation
# ==========================================================================


class TestValidation:
    def test_bad_status_raises_and_names_the_value(self, conn):
        with pytest.raises(ValueError) as exc:
            _add(conn, status="PROBABLY_FINE")
        assert "PROBABLY_FINE" in str(exc.value)

    def test_bad_regime_raises_and_names_the_value(self, conn):
        with pytest.raises(ValueError) as exc:
            _add(conn, market_regime="crabbing")
        assert "crabbing" in str(exc.value)

    def test_bad_failure_mode_raises_and_names_the_value(self, conn):
        with pytest.raises(ValueError) as exc:
            _add(conn, status="TESTED_FAILED", failure_mode="vibes")
        assert "vibes" in str(exc.value)

    def test_failed_with_no_mode_defaults_to_unclassified_never_null(self, conn):
        _add(conn, status="TESTED_FAILED")
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.failure_mode == "unclassified"
        raw = conn.execute("SELECT failure_mode FROM hypothesis_graph").fetchone()[0]
        assert raw is not None

    @pytest.mark.parametrize(
        "status", ["TESTED_CONFIRMED", "TESTED_CONDITIONAL", "UNTESTED"]
    )
    def test_non_failed_status_may_not_carry_a_failure_mode(self, conn, status):
        with pytest.raises(ValueError) as exc:
            _add(conn, status=status, failure_mode="sample_too_small")
        assert "sample_too_small" in str(exc.value)
        assert status in str(exc.value)

    def test_upsert_applies_the_same_validation(self, conn):
        with pytest.raises(ValueError):
            hg.upsert_hypothesis(
                conn, strategy_name="a", hypothesis="h",
                status="UNTESTED", source="s", failure_mode="never_fires",
            )

    @pytest.mark.parametrize("bad", ["", "   ", None, 7])
    def test_empty_required_text_is_rejected(self, conn, bad):
        with pytest.raises(ValueError):
            _add(conn, strategy_name=bad)
        with pytest.raises(ValueError):
            _add(conn, hypothesis=bad)
        with pytest.raises(ValueError):
            _add(conn, source=bad)

    def test_record_failure_mode_requires_a_valid_mode(self, conn):
        with pytest.raises(ValueError):
            hg.record_failure_mode(conn, strategy_name="a", failure_mode=None)
        with pytest.raises(ValueError):
            hg.record_failure_mode(conn, strategy_name="a", failure_mode="nope")

    def test_all_hypotheses_rejects_a_bad_status_filter(self, conn):
        with pytest.raises(ValueError):
            hg.all_hypotheses(conn, status="NOPE")

    def test_the_vocabularies_are_the_contract(self):
        assert hg.STATUSES == (
            "TESTED_FAILED", "TESTED_CONFIRMED", "TESTED_CONDITIONAL", "UNTESTED",
        )
        assert hg.REGIMES == (
            "bull", "bear", "sideways", "high_vol", "low_vol", "any",
        )
        assert hg.FAILURE_MODES == (
            "spread_eats_edge", "model_miscalibrated", "never_fires",
            "stop_too_tight", "exit_too_early", "entry_signal_wrong",
            "regime_mismatch", "sample_too_small", "cost_exceeds_edge",
            "unclassified",
        )


# ==========================================================================
# getters
# ==========================================================================


class TestGetters:
    @pytest.fixture()
    def seeded(self, conn):
        _add(conn, strategy_name="a", hypothesis="1", status="TESTED_FAILED",
             asset_class="CRYPTO", market_regime="bull", failure_mode="never_fires")
        _add(conn, strategy_name="b", hypothesis="2", status="TESTED_FAILED",
             asset_class="EQUITY", market_regime="any")
        _add(conn, strategy_name="a", hypothesis="3", status="TESTED_CONFIRMED",
             asset_class="CRYPTO", market_regime="bear")
        _add(conn, strategy_name="c", hypothesis="4", status="TESTED_CONDITIONAL",
             asset_class="EQUITY", market_regime="any")
        _add(conn, strategy_name="d", hypothesis="5", status="UNTESTED",
             asset_class=None, market_regime="any")
        return conn

    def test_each_getter_returns_only_its_status(self, seeded):
        assert {h.status for h in hg.get_failed_hypotheses(seeded)} == {
            "TESTED_FAILED"
        }
        assert {h.status for h in hg.get_confirmed_hypotheses(seeded)} == {
            "TESTED_CONFIRMED"
        }
        assert {h.status for h in hg.get_conditional_hypotheses(seeded)} == {
            "TESTED_CONDITIONAL"
        }
        assert {h.status for h in hg.get_untested(seeded)} == {"UNTESTED"}

    def test_strategy_name_filter(self, seeded):
        rows = hg.get_failed_hypotheses(seeded, strategy_name="a")
        assert [h.hypothesis for h in rows] == ["1"]

    def test_asset_class_filter(self, seeded):
        rows = hg.get_failed_hypotheses(seeded, asset_class="EQUITY")
        assert [h.hypothesis for h in rows] == ["2"]

    def test_market_regime_filter(self, seeded):
        rows = hg.get_failed_hypotheses(seeded, market_regime="bull")
        assert [h.hypothesis for h in rows] == ["1"]

    def test_filters_compose(self, seeded):
        assert hg.get_failed_hypotheses(
            seeded, strategy_name="a", asset_class="EQUITY"
        ) == []

    def test_all_hypotheses_unfiltered_returns_everything_ordered_by_id(self, seeded):
        rows = hg.all_hypotheses(seeded)
        assert [h.hypothesis for h in rows] == ["1", "2", "3", "4", "5"]
        assert [h.id for h in rows] == sorted(h.id for h in rows)

    def test_all_hypotheses_status_filter(self, seeded):
        rows = hg.all_hypotheses(seeded, status="TESTED_FAILED")
        assert len(rows) == 2

    def test_getters_return_hypothesis_dataclass_with_parsed_evidence(self, conn):
        _add(conn, status="TESTED_FAILED", evidence={"k": [1, 2]})
        h = hg.get_failed_hypotheses(conn)[0]
        assert isinstance(h, hg.Hypothesis)
        assert h.evidence["k"] == [1, 2]

    def test_hypothesis_is_frozen(self, conn):
        _add(conn)
        h = hg.all_hypotheses(conn)[0]
        with pytest.raises(Exception):
            h.status = "TESTED_FAILED"  # type: ignore[misc]


# ==========================================================================
# similarity
# ==========================================================================


class TestSimilarity:
    SIMILAR_A = "buy the dip on BTC when RSI is below 35 and price is above the 50 EMA"
    SIMILAR_B = "buying dips in BTC when the RSI drops under 35 while price sits above EMA 50"
    UNRELATED = "sell weather contracts when the airport METAR temperature ladder is mispriced"

    def test_a_clearly_similar_pair_scores_above_the_default_threshold(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED", failure_mode="entry_signal_wrong")
        match = hg.is_similar_to_failed(conn, self.SIMILAR_B)
        assert match is not None
        assert match.score >= 0.55
        assert isinstance(match, hg.SimilarityMatch)

    def test_a_clearly_unrelated_pair_scores_below_the_default_threshold(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        assert hg.is_similar_to_failed(conn, self.UNRELATED) is None

    def test_unrelated_still_scores_something_at_threshold_zero(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        matches = hg.similar_failures(conn, self.UNRELATED, threshold=0.0)
        assert len(matches) == 1
        assert 0.0 <= matches[0].score < 0.55

    def test_same_strategy_name_boosts_the_score(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        plain = hg.similar_failures(conn, self.SIMILAR_B, threshold=0.0)[0]
        boosted = hg.similar_failures(
            conn, self.SIMILAR_B, strategy_name="dip", threshold=0.0
        )[0]
        assert boosted.score > plain.score
        assert "same-strategy boost" in boosted.reason
        assert "same-strategy boost" not in plain.reason

    def test_the_boost_is_bounded_by_one(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        match = hg.similar_failures(
            conn, self.SIMILAR_A, strategy_name="dip", threshold=0.0
        )[0]
        assert match.score <= 1.0

    def test_identical_text_scores_one(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        match = hg.similar_failures(conn, self.SIMILAR_A, threshold=0.0)[0]
        assert match.score == pytest.approx(1.0)

    def test_reason_names_the_matched_strategy_and_the_components(self, conn):
        _add(conn, strategy_name="dip_arb", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        match = hg.similar_failures(conn, self.SIMILAR_B, threshold=0.0)[0]
        assert "dip_arb" in match.reason
        assert "sequence" in match.reason
        assert "token overlap" in match.reason

    def test_only_failed_rows_are_candidates(self, conn):
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="TESTED_CONFIRMED")
        _add(conn, strategy_name="dip", hypothesis=self.SIMILAR_A,
             status="UNTESTED", asset_class="X")
        assert hg.similar_failures(conn, self.SIMILAR_A, threshold=0.0) == []

    def test_strategy_name_does_not_restrict_the_candidate_set(self, conn):
        """Documented asymmetry: strategy_name boosts, it does not filter.

        The whole value of the query is learning that SOMEONE ELSE'S strategy
        already died on your idea.
        """
        _add(conn, strategy_name="someone_else", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        matches = hg.similar_failures(
            conn, self.SIMILAR_B, strategy_name="my_new_thing", threshold=0.0
        )
        assert len(matches) == 1
        assert matches[0].hypothesis.strategy_name == "someone_else"

    def test_asset_class_does_restrict_the_candidate_set(self, conn):
        _add(conn, strategy_name="a", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED", asset_class="CRYPTO")
        assert hg.similar_failures(
            conn, self.SIMILAR_A, asset_class="EQUITY", threshold=0.0
        ) == []
        assert len(
            hg.similar_failures(
                conn, self.SIMILAR_A, asset_class="CRYPTO", threshold=0.0
            )
        ) == 1

    def test_limit_and_descending_order(self, conn):
        _add(conn, strategy_name="a", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        _add(conn, strategy_name="b", hypothesis=self.SIMILAR_B,
             status="TESTED_FAILED")
        _add(conn, strategy_name="c", hypothesis=self.UNRELATED,
             status="TESTED_FAILED")
        matches = hg.similar_failures(conn, self.SIMILAR_A, limit=2, threshold=0.0)
        assert len(matches) == 2
        assert matches[0].score >= matches[1].score
        assert matches[0].hypothesis.strategy_name == "a"

    def test_threshold_filters(self, conn):
        _add(conn, strategy_name="a", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        assert hg.similar_failures(conn, self.UNRELATED, threshold=0.99) == []

    def test_empty_graph_returns_none(self, conn):
        assert hg.is_similar_to_failed(conn, "anything at all") is None

    def test_normalise_strips_punctuation_and_case(self):
        assert hg.normalise_text("Buy  the DIP, on BTC!") == "buy the dip on btc"

    def test_scores_are_bounded_and_finite(self, conn):
        _add(conn, strategy_name="a", hypothesis=self.SIMILAR_A,
             status="TESTED_FAILED")
        for text in (self.SIMILAR_A, self.SIMILAR_B, self.UNRELATED, "x"):
            score = hg.similar_failures(conn, text, threshold=0.0)[0].score
            assert 0.0 <= score <= 1.0
            assert math.isfinite(score)


# ==========================================================================
# failure_mode_counts / kill_recommendations
# ==========================================================================


class TestFailureModeAnalytics:
    def _seed(self, conn, n, mode="never_fires", strategy="a", asset="CRYPTO"):
        for i in range(n):
            _add(conn, strategy_name=strategy, hypothesis="h{}".format(i),
                 status="TESTED_FAILED", failure_mode=mode, asset_class=asset)

    def test_counts_group_by_strategy_and_mode(self, conn):
        self._seed(conn, 2, "never_fires", "a")
        self._seed(conn, 1, "stop_too_tight", "a")
        self._seed(conn, 3, "never_fires", "b")
        assert hg.failure_mode_counts(conn) == {
            ("a", "never_fires"): 2,
            ("a", "stop_too_tight"): 1,
            ("b", "never_fires"): 3,
        }

    def test_counts_filter_by_strategy(self, conn):
        self._seed(conn, 2, "never_fires", "a")
        self._seed(conn, 3, "never_fires", "b")
        assert hg.failure_mode_counts(conn, strategy_name="b") == {
            ("b", "never_fires"): 3
        }

    def test_counts_ignore_non_failed_rows(self, conn):
        _add(conn, strategy_name="a", status="UNTESTED")
        _add(conn, strategy_name="a", hypothesis="x", status="TESTED_CONFIRMED")
        assert hg.failure_mode_counts(conn) == {}

    def test_kill_below_threshold_is_excluded(self, conn):
        self._seed(conn, 2)
        assert hg.kill_recommendations(conn, threshold=3) == []

    def test_kill_at_threshold_is_included(self, conn):
        self._seed(conn, 3)
        recs = hg.kill_recommendations(conn, threshold=3)
        assert len(recs) == 1
        assert recs[0]["count"] == 3

    def test_kill_above_threshold_is_included(self, conn):
        self._seed(conn, 5)
        assert hg.kill_recommendations(conn, threshold=3)[0]["count"] == 5

    def test_kill_dict_shape_is_the_contract(self, conn):
        self._seed(conn, 3, asset="CRYPTO")
        rec = hg.kill_recommendations(conn, threshold=3)[0]
        assert set(rec) == {
            "strategy_name", "failure_mode", "count", "asset_classes",
            "hypothesis_ids",
        }
        assert rec["strategy_name"] == "a"
        assert rec["failure_mode"] == "never_fires"
        assert rec["asset_classes"] == ["CRYPTO"]
        assert len(rec["hypothesis_ids"]) == 3

    def test_kill_collects_distinct_asset_classes_including_null(self, conn):
        self._seed(conn, 2, asset="CRYPTO")
        self._seed(conn, 1, asset="EQUITY")
        self._seed(conn, 1, asset=None)
        rec = hg.kill_recommendations(conn, threshold=3)[0]
        assert rec["asset_classes"] == ["CRYPTO", "EQUITY", None]

    def test_kill_sorted_by_count_descending(self, conn):
        self._seed(conn, 3, "never_fires", "a")
        self._seed(conn, 5, "stop_too_tight", "b")
        recs = hg.kill_recommendations(conn, threshold=3)
        assert [r["count"] for r in recs] == [5, 3]

    def test_record_failure_mode_writes_a_failed_row(self, conn):
        rowid = hg.record_failure_mode(
            conn, strategy_name="a", failure_mode="stop_too_tight",
            asset_class="CRYPTO", evidence={"n": 3}, notes="n",
        )
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.id == rowid
        assert h.status == "TESTED_FAILED"
        assert h.failure_mode == "stop_too_tight"
        assert h.source == "shadow"
        assert h.evidence == {"n": 3}

    def test_record_failure_mode_is_idempotent(self, conn):
        a = hg.record_failure_mode(conn, strategy_name="a",
                                   failure_mode="stop_too_tight")
        b = hg.record_failure_mode(conn, strategy_name="a",
                                   failure_mode="stop_too_tight")
        assert a == b
        assert len(hg.get_failed_hypotheses(conn)) == 1


# ==========================================================================
# populate_from_graveyard (synthetic fixtures)
# ==========================================================================


class TestPopulateFromGraveyard:
    def test_streamer_survives_chunk_boundaries(self, tmp_path):
        entries = [gy_entry("s{}".format(i), "FAIL") for i in range(50)]
        summary = write_graveyard(tmp_path, entries)
        raw = os.path.join(os.path.dirname(summary), "v0_synthetic.json")
        for chunk in (16, 64, 257, 1 << 20):
            got = list(hg._stream_json_array(raw, "entries", chunk_size=chunk))
            assert len(got) == 50, "chunk_size={}".format(chunk)
            assert got[0]["strategy"] == "s0"
            assert got[-1]["strategy"] == "s49"

    def test_all_fail_becomes_tested_failed_with_a_mode(self, conn, tmp_path):
        entries = [
            gy_entry("loser", "FAIL", ticker="AAA", trades=200),
            gy_entry("loser", "FAIL", ticker="BBB", trades=200),
        ]
        result = hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert_identity(result)
        assert result["verdicts"] == {"TESTED_FAILED": 1}
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.strategy_name == "loser"
        assert h.failure_mode in hg.FAILURE_MODES
        assert h.failure_mode is not None
        assert h.evidence["rows_fail"] == 2

    def test_pass_on_some_becomes_conditional_and_names_the_tickers(
        self, conn, tmp_path
    ):
        entries = [
            gy_entry("mixed", "PASS", ticker="TSLA", timeframe="15m", trades=200),
            gy_entry("mixed", "PASS", ticker="TSLA", timeframe="5m", trades=200),
            gy_entry("mixed", "FAIL", ticker="SLB", trades=200),
        ]
        result = hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert_identity(result)
        h = hg.get_conditional_hypotheses(conn)[0]
        assert h.status == "TESTED_CONDITIONAL"
        assert h.failure_mode is None
        assert h.evidence["pass_tickers"] == ["TSLA"]
        assert "TSLA" in h.notes
        # convention 2: two PASS rows collapse to two findings here (two
        # timeframes) but the count must be present and named.
        assert h.evidence["distinct_findings_strategy_x_ticker_x_timeframe"] == 2
        assert h.evidence["raw_pass_rows"] == 2

    def test_distinct_findings_collapse_exit_configs(self, conn, tmp_path):
        """One ticker x timeframe across 3 exit configs is ONE finding."""
        entries = [
            gy_entry("mixed", "PASS", ticker="TSLA", timeframe="15m",
                     exit_config=cfg, trades=200)
            for cfg in ("fixed_1r", "fixed_2r", "fixed_3r")
        ] + [gy_entry("mixed", "FAIL", ticker="SLB", trades=200)]
        hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        h = hg.get_conditional_hypotheses(conn)[0]
        assert h.evidence["raw_pass_rows"] == 3
        assert h.evidence["distinct_findings_strategy_x_ticker_x_timeframe"] == 1

    def test_pass_everywhere_becomes_confirmed(self, conn, tmp_path):
        entries = [gy_entry("winner", "PASS", trades=200)]
        result = hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert_identity(result)
        assert len(hg.get_confirmed_hypotheses(conn)) == 1

    def test_not_tested_becomes_untested_and_NEVER_tested_failed(
        self, conn, tmp_path
    ):
        """Convention 11, asserted explicitly.

        A strategy whose every row is NOT_TESTED must land in UNTESTED. If it
        ever appears in TESTED_FAILED, the graph has recorded 'could not run'
        as 'ran and found nothing', which is the exact mislabel convention 11
        exists to prevent.
        """
        entries = [
            gy_entry("cold", "NOT_TESTED", trades=0, pf=None, gross_pf=None,
                     not_tested_reason="needs 840 bars, scan window is 260"),
            gy_entry("cold", "NOT_TESTED", ticker="BBB", trades=0, pf=None,
                     gross_pf=None, not_tested_reason="unsizable at cap"),
        ]
        result = hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert_identity(result)

        assert hg.get_failed_hypotheses(conn) == []
        assert hg.get_conditional_hypotheses(conn) == []
        assert hg.get_confirmed_hypotheses(conn) == []
        untested = hg.get_untested(conn)
        assert len(untested) == 1
        assert untested[0].strategy_name == "cold"
        assert untested[0].failure_mode is None
        assert "COULD NOT RUN" in untested[0].notes
        assert untested[0].evidence["not_tested_reasons"] == {
            "insufficient_bars": 1,
            "unsizable_at_cap": 1,
        }

    def test_partly_not_tested_emits_a_separate_untested_coverage_row(
        self, conn, tmp_path
    ):
        entries = [
            gy_entry("half", "FAIL", trades=200),
            gy_entry("half", "NOT_TESTED", ticker="BBB", trades=0, pf=None,
                     not_tested_reason="needs 840 bars, scan window is 260"),
        ]
        result = hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert_identity(result)
        assert result["considered"] == 2
        assert len(hg.get_failed_hypotheses(conn)) == 1
        untested = hg.get_untested(conn)
        assert len(untested) == 1
        assert untested[0].strategy_name == "half"
        assert untested[0].evidence["rows_not_tested"] == 1

    def test_never_fires_is_derived_from_zero_trades(self, conn, tmp_path):
        entries = [gy_entry("ghost", "FAIL", trades=0, pf=None, gross_pf=None)]
        hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert hg.get_failed_hypotheses(conn)[0].failure_mode == "never_fires"

    def test_sample_too_small_is_derived_from_a_thin_pool(self, conn, tmp_path):
        entries = [gy_entry("thin", "FAIL", trades=3)]
        hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert hg.get_failed_hypotheses(conn)[0].failure_mode == "sample_too_small"

    def test_cost_exceeds_edge_is_derived_from_gross_beating_net(
        self, conn, tmp_path
    ):
        entries = [
            gy_entry("costly", "FAIL", trades=200, pf=0.8, gross_pf=1.4),
            gy_entry("costly", "FAIL", ticker="BBB", trades=200, pf=0.7,
                     gross_pf=1.3),
        ]
        hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert hg.get_failed_hypotheses(conn)[0].failure_mode == "cost_exceeds_edge"

    def test_entry_signal_wrong_when_there_was_no_gross_edge_either(
        self, conn, tmp_path
    ):
        entries = [gy_entry("weak", "FAIL", trades=200, pf=0.5, gross_pf=0.6)]
        hg.populate_from_graveyard(conn, write_graveyard(tmp_path, entries))
        assert hg.get_failed_hypotheses(conn)[0].failure_mode == "entry_signal_wrong"

    def test_rerun_reports_unchanged_not_duplicates(self, conn, tmp_path):
        summary = write_graveyard(tmp_path, [gy_entry("loser", "FAIL", trades=200)])
        first = hg.populate_from_graveyard(conn, summary)
        second = hg.populate_from_graveyard(conn, summary)
        assert_identity(first)
        assert_identity(second)
        assert first["inserted"] == 1 and first["unchanged"] == 0
        assert second["inserted"] == 0 and second["unchanged"] == 1
        assert len(hg.all_hypotheses(conn)) == 1

    def test_missing_raw_file_reports_an_error_and_writes_nothing(
        self, conn, tmp_path
    ):
        summary = tmp_path / "summary.json"
        summary.write_text(json.dumps({"graveyard": "does_not_exist.json"}))
        result = hg.populate_from_graveyard(conn, str(summary))
        assert_identity(result)
        assert result["considered"] == 0
        assert "error" in result
        assert "does_not_exist.json" in result["error"]
        assert hg.all_hypotheses(conn) == []

    def test_summary_json_names_the_raw_file_that_gets_read(self, conn, tmp_path):
        summary = write_graveyard(tmp_path, [gy_entry("x", "FAIL", trades=200)])
        result = hg.populate_from_graveyard(conn, summary)
        assert result["raw_path"].endswith("v0_synthetic.json")
        assert result["entries_streamed"] == 1

    def test_max_entries_caps_the_stream_and_says_so(self, conn, tmp_path):
        entries = [gy_entry("s{}".format(i), "FAIL", trades=200) for i in range(10)]
        result = hg.populate_from_graveyard(
            conn, write_graveyard(tmp_path, entries), max_entries=4
        )
        assert_identity(result)
        assert result["entries_streamed"] == 4
        assert result["strategies_seen"] == 4

    def test_not_tested_reason_classifier_keeps_causes_apart(self):
        assert hg._classify_not_tested_reason("needs 840 bars") == "insufficient_bars"
        assert hg._classify_not_tested_reason("unsizable at cap") == "unsizable_at_cap"
        assert hg._classify_not_tested_reason(None) == "reason_not_recorded"
        assert hg._classify_not_tested_reason("moon phase wrong").startswith("other:")


# ==========================================================================
# populate_from_shadow (synthetic fixtures)
# ==========================================================================


class TestPopulateFromShadow:
    def _run(self, conn, tmp_path, rows, **kw):
        path = make_positions_db(tmp_path, rows)
        result = hg.populate_from_shadow(conn, path, **kw)
        assert_identity(result)
        return result

    def _rows(self, strategy, n, wins, **kw):
        return [
            closed_position(
                "{}-{}".format(strategy, i), strategy,
                1.0 if i < wins else -1.0, **kw
            )
            for i in range(n)
        ]

    def test_below_40_percent_fails(self, conn, tmp_path):
        self._run(conn, tmp_path, self._rows("a", 20, 7))  # 35%
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.status == "TESTED_FAILED"
        assert h.failure_mode in hg.FAILURE_MODES
        assert h.evidence["n_closed_trades"] == 20

    def test_exactly_40_percent_is_conditional_not_failed(self, conn, tmp_path):
        self._run(conn, tmp_path, self._rows("a", 20, 8))  # exactly 0.40
        assert hg.get_failed_hypotheses(conn) == []
        h = hg.get_conditional_hypotheses(conn)[0]
        assert h.evidence["win_rate"] == 0.4
        assert h.failure_mode is None

    def test_exactly_60_percent_is_conditional_not_confirmed(self, conn, tmp_path):
        self._run(conn, tmp_path, self._rows("a", 20, 12))  # exactly 0.60
        assert hg.get_confirmed_hypotheses(conn) == []
        assert len(hg.get_conditional_hypotheses(conn)) == 1

    def test_above_60_percent_confirms(self, conn, tmp_path):
        self._run(conn, tmp_path, self._rows("a", 20, 13))  # 65%
        h = hg.get_confirmed_hypotheses(conn)[0]
        assert h.failure_mode is None
        assert "n=20" in h.notes

    def test_min_trades_minus_one_is_untested_not_a_verdict(self, conn, tmp_path):
        result = self._run(conn, tmp_path, self._rows("a", 19, 0), min_trades=20)
        assert result["verdicts"] == {"UNTESTED": 1}
        h = hg.get_untested(conn)[0]
        assert h.status == "UNTESTED"
        assert h.failure_mode is None, "a non-failed status must not carry a mode"
        assert h.evidence["n_closed_trades"] == 19
        assert "19" in h.notes and "below" in h.notes

    def test_exactly_min_trades_gets_a_verdict(self, conn, tmp_path):
        result = self._run(conn, tmp_path, self._rows("a", 20, 0), min_trades=20)
        assert result["verdicts"] == {"TESTED_FAILED": 1}

    def test_the_small_sample_path_never_produces_sample_too_small(
        self, conn, tmp_path
    ):
        """The explicitly-wrong design, pinned so it cannot come back."""
        self._run(conn, tmp_path, self._rows("a", 3, 3), min_trades=20)
        rows = hg.all_hypotheses(conn)
        assert [r.status for r in rows] == ["UNTESTED"]
        assert all(r.failure_mode is None for r in rows)
        assert hg.failure_mode_counts(conn) == {}

    def test_n_is_in_the_evidence_and_the_notes_on_every_row(self, conn, tmp_path):
        """Convention 7: no win rate may be quoted without its sample size."""
        rows = self._rows("small", 5, 5) + self._rows("big", 25, 20)
        self._run(conn, tmp_path, rows, min_trades=20)
        by_name = {h.strategy_name: h for h in hg.all_hypotheses(conn)}
        assert by_name["small"].evidence["n_closed_trades"] == 5
        assert by_name["big"].evidence["n_closed_trades"] == 25
        for h in by_name.values():
            assert "n={}".format(h.evidence["n_closed_trades"]) in h.notes
            assert h.evidence["sample_caveat"]

    def test_open_positions_are_excluded_from_the_count(self, conn, tmp_path):
        rows = self._rows("a", 20, 0)
        rows.append(
            ("open-1", "btc-updown-5m-1", "a", 1787022141000, None,
             0.5, None, 10.0, 0.0, 1.0, None, None, 0.0, None, "paper")
        )
        self._run(conn, tmp_path, rows, min_trades=20)
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.evidence["n_closed_trades"] == 20
        assert h.evidence["open_positions_excluded"] == 1

    def test_a_strategy_with_only_open_positions_is_skipped_and_counted(
        self, conn, tmp_path
    ):
        rows = [
            ("open-1", "btc-updown-5m-1", "ghost", 1787022141000, None,
             0.5, None, 10.0, 0.0, 1.0, None, None, 0.0, None, "paper")
        ]
        result = self._run(conn, tmp_path, rows)
        assert result["considered"] == 1
        assert result["skipped"] == 1
        assert result["skip_reasons"] == {"strategy_has_no_closed_positions": 1}
        assert hg.all_hypotheses(conn) == []

    def test_stop_too_tight_is_derived_from_the_exit_reasons(self, conn, tmp_path):
        self._run(conn, tmp_path,
                  self._rows("a", 20, 2, exit_reason="sell:price_stop"))
        assert hg.get_failed_hypotheses(conn)[0].failure_mode == "stop_too_tight"

    def test_cost_exceeds_edge_is_derived_when_fees_flip_the_sign(
        self, conn, tmp_path
    ):
        # 5 x +0.10 and 15 x -0.05 nets -0.25; 20 x 0.50 in fees puts gross at
        # +9.75. Costs, not the signal, are what turned this negative.
        rows = [
            closed_position("a-{}".format(i), "a", 0.10 if i < 5 else -0.05,
                            fees=0.5)
            for i in range(20)
        ]
        self._run(conn, tmp_path, rows)
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.evidence["pnl_gross_total"] > 0 >= h.evidence["pnl_net_total"]
        assert h.failure_mode == "cost_exceeds_edge"

    def test_mean_hold_time_is_computed_in_ms_and_minutes(self, conn, tmp_path):
        rows = self._rows("a", 20, 0, opened=1787022141000, closed=1787022441000)
        self._run(conn, tmp_path, rows)
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.evidence["mean_hold_ms"] == 300000.0
        assert h.evidence["mean_hold_minutes"] == 5.0

    def test_date_tested_is_the_last_close_in_ms(self, conn, tmp_path):
        self._run(conn, tmp_path, self._rows("a", 20, 0))
        assert hg.get_failed_hypotheses(conn)[0].date_tested == 1787022441000

    def test_rerun_is_unchanged(self, conn, tmp_path):
        path = make_positions_db(tmp_path, self._rows("a", 20, 0))
        first = hg.populate_from_shadow(conn, path)
        second = hg.populate_from_shadow(conn, path)
        assert_identity(second)
        assert first["inserted"] == 1
        assert second["unchanged"] == 1
        assert len(hg.all_hypotheses(conn)) == 1

    def test_missing_database_reports_an_error(self, conn, tmp_path):
        result = hg.populate_from_shadow(conn, str(tmp_path / "nope.db"))
        assert_identity(result)
        assert result["considered"] == 0
        assert "error" in result

    def test_database_without_a_positions_table_reports_an_error(
        self, conn, tmp_path
    ):
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).close()
        result = hg.populate_from_shadow(conn, path)
        assert_identity(result)
        assert "positions" in result["error"]

    def test_the_source_database_is_never_written(self, conn, tmp_path):
        path = make_positions_db(tmp_path, self._rows("a", 20, 0))
        hg.populate_from_shadow(conn, path)
        src = sqlite3.connect(path)
        names = {
            r[0] for r in src.execute("SELECT name FROM sqlite_master")
        }
        src.close()
        assert "hypothesis_graph" not in names


# ==========================================================================
# populate_from_proposals (synthetic fixtures)
# ==========================================================================


class TestPopulateFromProposals:
    @pytest.fixture()
    def props(self, tmp_path):
        """A proposals directory of its own, NOT bare tmp_path.

        The populator considers every file in the directory it is handed, which
        is correct. The `conn` fixture puts its database in `tmp_path`, so
        sharing that directory would count a stray .db as a proposal skip and
        make these counts lie.
        """
        d = tmp_path / "proposals"
        d.mkdir()
        return d

    def test_a_proposal_becomes_an_untested_row(self, conn, props):
        write_proposal(
            props, "001-x.md",
            name="pm_thing", thesis="the book is slow to reprice",
            expected_edge_bps="1200", kill_condition="under 1.5c per rotation",
            asset_class="PREDICTION_MARKET", kind="edge_hypothesis",
            status="PROPOSED", source="forge",
        )
        result = hg.populate_from_proposals(conn, str(props))
        assert_identity(result)
        assert result["considered"] == 1 and result["inserted"] == 1
        h = hg.get_untested(conn)[0]
        assert h.strategy_name == "pm_thing"
        assert h.hypothesis == "the book is slow to reprice"
        assert h.asset_class == "PREDICTION_MARKET"
        assert h.source == "proposal"
        assert h.failure_mode is None
        assert h.evidence["kill_condition"] == "under 1.5c per rotation"
        assert h.evidence["file"] == "001-x.md"

    def test_non_markdown_files_are_skipped_under_their_own_reason(
        self, conn, props
    ):
        write_proposal(props, "001-x.md", name="a", thesis="t")
        (props / "forge_runs.jsonl").write_text('{"a":1}\n')
        (props / ".gitkeep").write_text("")
        result = hg.populate_from_proposals(conn, str(props))
        assert_identity(result)
        assert result["considered"] == 3
        assert result["inserted"] == 1
        assert result["skip_reasons"] == {"not_a_markdown_file": 2}

    def test_readme_without_front_matter_gets_its_own_skip_reason(
        self, conn, props
    ):
        write_proposal(props, "001-x.md", name="a", thesis="t")
        (props / "README.md").write_text("# How to write a proposal\n")
        result = hg.populate_from_proposals(conn, str(props))
        assert_identity(result)
        assert result["skip_reasons"] == {"no_front_matter": 1}

    def test_two_drop_causes_never_share_one_skip_reason(self, conn, props):
        """Convention 20, asserted rather than described."""
        (props / "README.md").write_text("# no front matter\n")
        (props / "forge_runs.jsonl").write_text("{}\n")
        write_proposal(props, "002-noname.md", thesis="t")
        write_proposal(props, "003-nothesis.md", name="n")
        result = hg.populate_from_proposals(conn, str(props))
        assert_identity(result)
        assert result["skip_reasons"] == {
            "no_front_matter": 1,
            "not_a_markdown_file": 1,
            "front_matter_missing_name": 1,
            "front_matter_missing_thesis": 1,
        }
        assert result["skipped"] == 4
        assert result["inserted"] == 0

    def test_rerun_is_unchanged(self, conn, props):
        write_proposal(props, "001-x.md", name="a", thesis="t")
        hg.populate_from_proposals(conn, str(props))
        second = hg.populate_from_proposals(conn, str(props))
        assert_identity(second)
        assert second["unchanged"] == 1
        assert len(hg.all_hypotheses(conn)) == 1

    def test_missing_directory_reports_an_error(self, conn, tmp_path):
        result = hg.populate_from_proposals(conn, str(tmp_path / "nope"))
        assert_identity(result)
        assert result["considered"] == 0
        assert "error" in result

    def test_front_matter_parser_handles_the_real_shapes(self):
        text = (
            '---\n'
            'name: "pm_thing"\n'
            'expected_edge_bps: 1200\n'
            'nothing: null\n'
            'flag: true\n'
            'bare: some words\n'
            'wrapped: "first part\n'
            '  second part"\n'
            '---\n\n## body\n'
        )
        front = hg.parse_front_matter(text)
        assert front["name"] == "pm_thing"
        assert front["expected_edge_bps"] == 1200
        assert front["nothing"] is None
        assert front["flag"] is True
        assert front["bare"] == "some words"
        assert "second part" in front["wrapped"]

    def test_front_matter_parser_returns_none_without_a_header(self):
        assert hg.parse_front_matter("# just a readme\n") is None
        assert hg.parse_front_matter("---\nname: x\nnever closed") is None


# ==========================================================================
# populate_all
# ==========================================================================


class TestPopulateAll:
    def test_returns_all_four_keys_and_totals_balance(self, conn, monkeypatch,
                                                      tmp_path):
        gy = write_graveyard(tmp_path, [gy_entry("loser", "FAIL", trades=200)])
        pos = make_positions_db(
            tmp_path, [closed_position(i, "a", -1.0) for i in range(20)]
        )
        props = tmp_path / "props"
        props.mkdir()
        write_proposal(props, "001-x.md", name="p", thesis="t")
        (props / "README.md").write_text("# readme\n")

        monkeypatch.setattr(hg, "GRAVEYARD_SUMMARY_PATH", gy)
        monkeypatch.setattr(hg, "DB_PATH", pos)
        monkeypatch.setattr(hg, "PROPOSALS_DIR", str(props))

        result = hg.populate_all(conn)
        assert set(result) == {"graveyard", "shadow", "proposals", "totals"}
        for key in ("graveyard", "shadow", "proposals", "totals"):
            assert_identity(result[key])
        assert result["totals"]["considered"] == (
            result["graveyard"]["considered"]
            + result["shadow"]["considered"]
            + result["proposals"]["considered"]
        )

    def test_totals_prefix_skip_reasons_with_their_source(self, conn, monkeypatch,
                                                          tmp_path):
        props = tmp_path / "props"
        props.mkdir()
        (props / "README.md").write_text("# readme\n")
        monkeypatch.setattr(hg, "GRAVEYARD_SUMMARY_PATH",
                            str(tmp_path / "absent.json"))
        monkeypatch.setattr(hg, "DB_PATH", str(tmp_path / "absent.db"))
        monkeypatch.setattr(hg, "PROPOSALS_DIR", str(props))
        result = hg.populate_all(conn)
        assert result["totals"]["skip_reasons"] == {"proposal:no_front_matter": 1}


# ==========================================================================
# stats + JSON strictness
# ==========================================================================


class TestStatsAndJson:
    def test_stats_groups_by_every_dimension(self, conn):
        _add(conn, strategy_name="a", status="TESTED_FAILED",
             failure_mode="never_fires", asset_class="CRYPTO", source="graveyard")
        _add(conn, strategy_name="b", status="UNTESTED", source="proposal")
        data = hg.stats(conn)
        assert data["rows"] == 2
        assert data["distinct_strategies"] == 2
        assert data["by_status"] == {"TESTED_FAILED": 1, "UNTESTED": 1}
        assert data["by_source"] == {"graveyard": 1, "proposal": 1}
        assert data["by_failure_mode"]["never_fires"] == 1
        assert data["by_failure_mode"]["(null)"] == 1

    def test_dumps_refuses_bare_nan_and_infinity(self):
        """Convention 19: json.loads accepts Infinity/NaN, so we never emit them."""
        out = hg.dumps({"a": float("inf"), "b": float("nan"), "c": float("-inf")})
        assert "Infinity" not in out and "NaN" not in out
        assert json.loads(out) == {"a": "inf", "b": "nan", "c": "-inf"}

    def test_dumps_is_sorted_and_deterministic(self):
        assert hg.dumps({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'

    def test_evidence_with_a_non_finite_float_still_round_trips(self, conn):
        _add(conn, status="TESTED_FAILED", evidence={"toll_rate": float("inf")})
        h = hg.get_failed_hypotheses(conn)[0]
        assert h.evidence["toll_rate"] == "inf"

    def test_to_ms_treats_seconds_and_ms_correctly(self):
        assert hg.to_ms(1787022141) == 1787022141000
        assert hg.to_ms(1787022141000) == 1787022141000
        assert hg.to_ms(None) is None
        assert hg.now_ms() > 1_600_000_000_000

    def test_to_ms_parses_the_graveyard_generated_string_as_utc_ms(self):
        expected = int(
            datetime(2026, 8, 17, 19, 20, 29, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert hg.to_ms("2026-08-17 19:20:29") == expected
        assert hg.to_ms("2026-08-17T19:20:29Z") == expected
        assert len(str(expected)) == 13, "must be milliseconds, not seconds"

    def test_to_ms_rejects_an_unparseable_string(self):
        with pytest.raises(ValueError):
            hg.to_ms("last thursday")


# ==========================================================================
# CLI
# ==========================================================================


def run_cli(*args, db=None):
    cmd = [sys.executable, "-m", "agents.hypothesis_graph"]
    if db:
        cmd += ["--db", db]
    cmd += list(args)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=300
    )


class TestCli:
    @pytest.fixture()
    def cli_db(self, tmp_path):
        path = str(tmp_path / "cli.db")
        c = hg.connect(path)
        hg.ensure_table(c)
        hg.add_hypothesis(
            c, strategy_name="a", hypothesis="edge on btc",
            status="TESTED_FAILED", source="graveyard",
            failure_mode="never_fires", asset_class="CRYPTO",
            evidence={"rate": float("inf")},
        )
        hg.add_hypothesis(
            c, strategy_name="b", hypothesis="another idea",
            status="UNTESTED", source="proposal",
        )
        hg.add_hypothesis(
            c, strategy_name="c", hypothesis="a good one",
            status="TESTED_CONFIRMED", source="shadow",
        )
        c.close()
        return path

    @pytest.mark.parametrize(
        "args",
        [
            ("stats", "--json"),
            ("list", "--json"),
            ("failed", "--json"),
            ("confirmed", "--json"),
            ("untested", "--json"),
            ("kills", "--threshold", "1", "--json"),
        ],
    )
    def test_json_output_parses_and_is_nan_free(self, cli_db, args):
        proc = run_cli(*args, db=cli_db)
        assert proc.returncode == 0, proc.stderr
        assert "NaN" not in proc.stdout
        assert "Infinity" not in proc.stdout
        parsed = json.loads(proc.stdout)
        assert parsed is not None
        # json.loads is NOT strict; re-encoding strictly proves it is clean.
        json.dumps(parsed, allow_nan=False)

    def test_list_json_carries_the_rows(self, cli_db):
        proc = run_cli("list", "--json", db=cli_db)
        rows = json.loads(proc.stdout)
        assert len(rows) == 3
        assert {r["strategy_name"] for r in rows} == {"a", "b", "c"}

    def test_failed_json_only_failed(self, cli_db):
        rows = json.loads(run_cli("failed", "--json", db=cli_db).stdout)
        assert [r["status"] for r in rows] == ["TESTED_FAILED"]

    def test_status_and_strategy_filters(self, cli_db):
        rows = json.loads(
            run_cli("list", "--status", "UNTESTED", "--json", db=cli_db).stdout
        )
        assert [r["strategy_name"] for r in rows] == ["b"]
        rows = json.loads(run_cli("list", "--strategy", "c", "--json",
                                  db=cli_db).stdout)
        assert [r["strategy_name"] for r in rows] == ["c"]

    def test_limit(self, cli_db):
        rows = json.loads(run_cli("list", "--limit", "1", "--json", db=cli_db).stdout)
        assert len(rows) == 1

    def test_stats_json_shape(self, cli_db):
        data = json.loads(run_cli("stats", "--json", db=cli_db).stdout)
        assert data["rows"] == 3
        assert data["by_status"]["TESTED_FAILED"] == 1

    def test_human_output_is_not_json_but_still_succeeds(self, cli_db):
        proc = run_cli("stats", db=cli_db)
        assert proc.returncode == 0, proc.stderr
        assert "rows:" in proc.stdout

    def test_populate_proposals_json_against_a_temp_db(self, tmp_path):
        proc = run_cli("populate", "--source", "proposals", "--json",
                       db=str(tmp_path / "p.db"))
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert_identity(result)
        assert result["source"] == "proposal"

    def test_an_unknown_subcommand_fails_loudly(self, cli_db):
        assert run_cli("frobnicate", db=cli_db).returncode != 0


# ==========================================================================
# REAL repo files. Clearly marked, skips gracefully.
# ==========================================================================


class TestAgainstRealRepoFiles:
    """Runs each populator against the LIVE repo files into a TEMP database.

    These are the only tests that touch real data. They write nothing to
    `db/trading.db`: the shadow populator opens it READ-ONLY and the rows land
    in `tmp_path`. Each skips rather than fails when its source is absent, so a
    fresh clone without the (gitignored) 389 MB graveyard stays green.
    """

    def test_graveyard_against_the_real_sweep(self, conn):
        if not os.path.exists(hg.GRAVEYARD_SUMMARY_PATH):
            pytest.skip("no research/graveyard/summary.json in this checkout")
        if not os.path.exists(hg.GRAVEYARD_RAW_PATH):
            pytest.skip("raw graveyard is gitignored and absent here")
        result = hg.populate_from_graveyard(conn)
        assert_identity(result)
        assert result["considered"] > 0
        assert result["strategies_seen"] > 0
        # Convention 11 on real data: nothing that could not run became a FAIL.
        for h in hg.get_untested(conn):
            assert h.failure_mode is None
        # Convention 2: every conditional row carries its distinct-finding count.
        for h in hg.get_conditional_hypotheses(conn):
            assert "distinct_findings_strategy_x_ticker_x_timeframe" in h.evidence
            assert h.evidence["pass_tickers"]

    def test_graveyard_distinct_findings_match_summary_json(self, conn):
        """Independent re-derivation: our collapse must equal summary.json's.

        `summary.json` reports `distinct_findings.strategy_x_ticker_x_timeframe`
        for the whole sweep. Summing our per-strategy collapse must reproduce
        it exactly, and the same for `raw_pass_rows`. If a re-sweep moves the
        graveyard, both sides move together and this stays green; if only one
        moves, our aggregation is wrong.
        """
        if not os.path.exists(hg.GRAVEYARD_RAW_PATH):
            pytest.skip("raw graveyard is gitignored and absent here")
        with open(hg.GRAVEYARD_SUMMARY_PATH, encoding="utf-8") as fh:
            summary = json.load(fh)
        expected_findings = summary["distinct_findings"][
            "strategy_x_ticker_x_timeframe"
        ]
        expected_pass_rows = summary["raw_pass_rows"]

        hg.populate_from_graveyard(conn)
        rows = [
            h
            for h in hg.all_hypotheses(conn)
            if h.status in ("TESTED_CONDITIONAL", "TESTED_CONFIRMED")
        ]
        got_findings = sum(
            h.evidence["distinct_findings_strategy_x_ticker_x_timeframe"]
            for h in rows
        )
        got_pass_rows = sum(h.evidence["raw_pass_rows"] for h in rows)
        assert got_findings == expected_findings
        assert got_pass_rows == expected_pass_rows

    def test_shadow_against_the_live_database_read_only(self, conn):
        if not os.path.exists(hg.DB_PATH):
            pytest.skip("no db/trading.db in this checkout")
        result = hg.populate_from_shadow(conn)
        assert_identity(result)
        assert result["positions_db"] == hg.DB_PATH
        # Every row must carry its sample size; convention 7 both ways.
        for h in hg.all_hypotheses(conn):
            assert h.evidence["n_closed_trades"] >= 1
            if h.status == "UNTESTED":
                assert h.failure_mode is None
            if h.status == "TESTED_FAILED":
                assert h.failure_mode in hg.FAILURE_MODES

    def test_proposals_against_the_real_directory(self, conn):
        if not os.path.isdir(hg.PROPOSALS_DIR):
            pytest.skip("no strategies/proposals/ in this checkout")
        result = hg.populate_from_proposals(conn)
        assert_identity(result)
        assert result["inserted"] >= 1
        # README.md and forge_runs.jsonl must be skipped under DISTINCT reasons.
        assert set(result["skip_reasons"]) <= {
            "not_a_markdown_file",
            "no_front_matter",
            "front_matter_missing_name",
            "front_matter_missing_thesis",
            "unreadable_file",
        }
        for h in hg.get_untested(conn):
            assert h.source == "proposal"
            assert h.failure_mode is None

    def test_populate_all_against_the_real_repo(self, conn):
        if not os.path.exists(hg.GRAVEYARD_RAW_PATH):
            pytest.skip("raw graveyard is gitignored and absent here")
        result = hg.populate_all(conn)
        for key in ("graveyard", "shadow", "proposals", "totals"):
            assert_identity(result[key])
        assert result["totals"]["considered"] > 0
        # The whole graph must be internally consistent afterwards.
        for h in hg.all_hypotheses(conn):
            assert h.status in hg.STATUSES
            assert h.market_regime in hg.REGIMES
            if h.status == "TESTED_FAILED":
                assert h.failure_mode in hg.FAILURE_MODES
            else:
                assert h.failure_mode is None
            json.dumps(h.evidence, allow_nan=False)
