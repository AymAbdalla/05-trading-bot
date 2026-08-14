"""Tests for the T8 strategy sandbox: AST allowlist, subprocess conformance,
hash pinning, family-drift enforcement."""
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from sandbox.validator import (check_ast, validate_strategy_file,
                               register_strategy, verify_hash,
                               ensure_registry_columns, file_sha256)
from engine.db import init_schema, get_connection


GOOD_STRATEGY = textwrap.dedent('''
    from typing import Dict, List, Optional
    from strategies.base import Strategy, Signal
    from indicators.atr import latest_atr

    class CandidateBreakout(Strategy):
        name = "candidate_breakout"
        is_entry = True

        def scan(self, candles):
            closes = candles["closes"]
            highs = candles["highs"]
            lows = candles["lows"]
            if len(closes) < 60:
                return None
            hi = max(highs[-21:-1])
            if closes[-1] > hi:
                atr_val = latest_atr(highs, lows, closes, 14)
                if atr_val <= 0:
                    return None
                entry = closes[-1]
                return Signal(pair="", pattern=self.name, direction="bullish",
                              confidence=0.5, features={}, entry=entry,
                              stop=entry - atr_val, target=entry + 2 * atr_val)
            return None
''')


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setenv('TRADING_DB_PATH', db_path)
    init_schema()
    return db_path


def _write(tmp_path, source, name='candidate.py'):
    p = tmp_path / name
    p.write_text(source)
    return str(p)


class TestAstAllowlist:
    def test_clean_strategy_passes(self):
        assert check_ast(GOOD_STRATEGY) == []

    @pytest.mark.parametrize('line,needle', [
        ('import os', 'not in allowlist'),
        ('import subprocess', 'not in allowlist'),
        ('from socket import socket', 'not in allowlist'),
        ('import requests', 'not in allowlist'),
        ('x = eval("1+1")', 'forbidden'),
        ('x = exec("pass")', 'forbidden'),
        ('f = open("/etc/passwd")', 'forbidden'),
        ('m = __import__("os")', 'forbidden'),
        ('y = getattr(object, "x")', 'forbidden'),
        ('z = (1).__class__', 'dunder'),
        ('from indicators.atr import *', 'import *'),
    ])
    def test_forbidden_constructs_rejected(self, line, needle):
        errors = check_ast(GOOD_STRATEGY + '\n' + line + '\n')
        assert errors, f'{line!r} should be rejected'
        assert any(needle in e for e in errors)


class TestConformance:
    def test_good_strategy_validates(self, tmp_path):
        result = validate_strategy_file(_write(tmp_path, GOOD_STRATEGY))
        assert result.ok, result.errors
        assert result.sha256
        names = [s['name'] for s in result.conformance['strategies']]
        assert 'candidate_breakout' in names

    def test_ast_failure_blocks_execution(self, tmp_path):
        # A module whose import-time side effect would create a file: the AST
        # gate must reject it WITHOUT running it.
        marker = tmp_path / 'pwned.txt'
        evil = f'import os\nos.system("touch {marker}")\n' + GOOD_STRATEGY
        result = validate_strategy_file(_write(tmp_path, evil))
        assert not result.ok
        assert not marker.exists(), 'AST-rejected code was executed!'

    def test_no_strategy_subclass_fails(self, tmp_path):
        result = validate_strategy_file(_write(tmp_path, 'x = 1\n'))
        assert not result.ok
        assert any('no Strategy subclass' in e for e in result.errors)

    def test_inverted_stop_fails_conformance(self, tmp_path):
        bad = GOOD_STRATEGY.replace('stop=entry - atr_val', 'stop=entry + atr_val')
        result = validate_strategy_file(_write(tmp_path, bad))
        assert not result.ok
        assert any('stop >= entry' in e for e in result.errors)

    def test_crashing_scan_fails(self, tmp_path):
        bad = GOOD_STRATEGY.replace('hi = max(highs[-21:-1])',
                                    'hi = 1 / 0')
        result = validate_strategy_file(_write(tmp_path, bad))
        assert not result.ok


class TestRegistry:
    def test_register_pins_hash_and_family(self, tmp_path, tmpdb):
        path = _write(tmp_path, GOOD_STRATEGY)
        conn = get_connection()
        result = register_strategy(conn, 'candidate_breakout', path, family='breakout')
        assert result.ok, result.errors

        row = conn.execute('SELECT status, family, code_hash FROM strategy_registry '
                           'WHERE strategy_id = ?', ('candidate_breakout',)).fetchone()
        assert row['status'] == 'candidate'
        assert row['family'] == 'breakout'
        assert row['code_hash'] == file_sha256(path)
        assert verify_hash(conn, 'candidate_breakout', path) is True
        conn.close()

    def test_hash_mismatch_detected_after_edit(self, tmp_path, tmpdb):
        path = _write(tmp_path, GOOD_STRATEGY)
        conn = get_connection()
        register_strategy(conn, 'candidate_breakout', path, family='breakout')
        # Someone edits the file after registration: loader must refuse it.
        Path(path).write_text(GOOD_STRATEGY + '\n# innocent-looking edit\n')
        assert verify_hash(conn, 'candidate_breakout', path) is False
        conn.close()

    def test_family_drift_rejected(self, tmp_path, tmpdb):
        path = _write(tmp_path, GOOD_STRATEGY)
        conn = get_connection()
        register_strategy(conn, 'candidate_breakout', path, family='breakout')
        result = register_strategy(conn, 'candidate_breakout', path, family='mean_reversion')
        assert not result.ok
        assert any('family drift' in e for e in result.errors)
        # Explicit migration is allowed (deliberate, reviewed event)
        result2 = register_strategy(conn, 'candidate_breakout', path,
                                    family='mean_reversion',
                                    allow_family_migration=True)
        assert result2.ok
        conn.close()

    def test_unregistered_strategy_fails_hash_check(self, tmp_path, tmpdb):
        path = _write(tmp_path, GOOD_STRATEGY)
        conn = get_connection()
        ensure_registry_columns(conn)
        assert verify_hash(conn, 'ghost_strategy', path) is False
        conn.close()
