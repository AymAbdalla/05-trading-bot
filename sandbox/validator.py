"""Strategy sandbox validator (T8) - SPEC F1/R1 + 5.6.

Gate for Quant-authored strategy modules before the engine will ever load
them. Three layers, in order:

1. AST allowlist (static, no execution): only whitelisted imports; no
   exec/eval/compile/open/__import__/getattr-family calls; no dunder
   attribute access; no `import *`. Fails closed.
2. Subprocess conformance (sandbox/_runner.py, 15s timeout): the module must
   define an instantiable Strategy subclass whose scan() returns well-formed
   Signals on synthetic data. Crashes/hangs kill the subprocess, not us.
3. Registry + hash pinning: sha256 of the file recorded in strategy_registry
   (code_hash). The loader must refuse a file whose hash differs from the
   registry. Family declared at registration; changing family later is
   rejected unless allow_family_migration=True (SPEC 5.6 semantic drift).

The allowlist is a POLICY surface: adding a module here widens what Quant
code can touch, and per DECISIONS.md D-203 each strategy-side addition needs
Raven's sign-off.
"""
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(PROJECT_ROOT, 'sandbox', '_runner.py')

# Modules a strategy may import. Anything else fails validation.
ALLOWED_IMPORTS = {
    'math', 'statistics', 'typing', 'dataclasses', 'datetime', 'zoneinfo',
    'random',                     # seeded internal randomness only
    'strategies', 'strategies.base',
    'indicators',                 # and any indicators.* submodule
    'ta',                         # ta-backed indicator stack (D-201; Raven to ratify)
    'pandas', 'numpy',            # required by the ta stack
}

FORBIDDEN_CALLS = {'exec', 'eval', 'compile', 'open', '__import__',
                   'getattr', 'setattr', 'delattr', 'globals', 'locals',
                   'vars', 'breakpoint', 'input', 'exit', 'quit'}

CONFORMANCE_TIMEOUT_S = 15


@dataclass
class ValidationResult:
    ok: bool
    sha256: str = ''
    errors: List[str] = field(default_factory=list)
    conformance: Optional[dict] = None


def _import_allowed(name: str) -> bool:
    root = name.split('.')[0]
    return name in ALLOWED_IMPORTS or root in ALLOWED_IMPORTS


def check_ast(source: str) -> List[str]:
    """Static allowlist check. Returns a list of violations (empty = clean)."""
    errors = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f'syntax error: {e}']

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _import_allowed(alias.name):
                    errors.append(f'line {node.lineno}: import {alias.name!r} not in allowlist')
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if node.level and node.level > 0:
                errors.append(f'line {node.lineno}: relative import forbidden')
            elif not _import_allowed(mod):
                errors.append(f'line {node.lineno}: from {mod!r} import ... not in allowlist')
            elif any(a.name == '*' for a in node.names):
                errors.append(f'line {node.lineno}: import * forbidden')
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                errors.append(f'line {node.lineno}: call to {fn.id}() forbidden')
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('__') and node.attr.endswith('__'):
                errors.append(f'line {node.lineno}: dunder attribute access '
                              f'({node.attr}) forbidden')
    return errors


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def run_conformance(path: str) -> dict:
    """Execute the candidate in an isolated subprocess with a hard timeout."""
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER, path],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
            timeout=CONFORMANCE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'conformance run exceeded {CONFORMANCE_TIMEOUT_S}s (hang?)'}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {'ok': False,
                'error': f'runner produced no verdict (exit {proc.returncode}): '
                         f'{(proc.stderr or proc.stdout)[-300:]}'}


def validate_strategy_file(path: str) -> ValidationResult:
    """Layers 1+2. Registry interaction is separate (register_strategy)."""
    if not os.path.exists(path):
        return ValidationResult(False, errors=[f'file not found: {path}'])
    with open(path) as f:
        source = f.read()

    ast_errors = check_ast(source)
    if ast_errors:
        # AST failure means the code is NEVER executed.
        return ValidationResult(False, sha256=file_sha256(path), errors=ast_errors)

    conf = run_conformance(path)
    if not conf.get('ok'):
        return ValidationResult(False, sha256=file_sha256(path),
                                errors=[f"conformance: {conf.get('error', 'unknown')}"],
                                conformance=conf)

    return ValidationResult(True, sha256=file_sha256(path), conformance=conf)


# ---------- registry (hash pinning + family drift) ----------

def ensure_registry_columns(conn):
    """Additive migration: family + code_hash columns (SPEC 5.6 / F8)."""
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(strategy_registry)')}
    if 'family' not in cols:
        conn.execute("ALTER TABLE strategy_registry ADD COLUMN family TEXT")
    if 'code_hash' not in cols:
        conn.execute("ALTER TABLE strategy_registry ADD COLUMN code_hash TEXT")
    conn.commit()


def register_strategy(conn, strategy_id: str, path: str, family: str,
                      changed_by: str = 'quant',
                      allow_family_migration: bool = False) -> ValidationResult:
    """Validate, then upsert into the registry as status='candidate'.

    Family drift (SPEC 5.6): if the strategy already has a family and the new
    declaration differs, reject unless allow_family_migration explicitly set
    (a family migration is a deliberate, reviewed event - never a silent edit).
    """
    result = validate_strategy_file(path)
    if not result.ok:
        return result

    ensure_registry_columns(conn)
    row = conn.execute('SELECT family FROM strategy_registry WHERE strategy_id = ?',
                       (strategy_id,)).fetchone()
    if row and row['family'] and row['family'] != family and not allow_family_migration:
        result.ok = False
        result.errors.append(
            f'family drift: registered {row["family"]!r} != declared {family!r} '
            f'(pass allow_family_migration=True for a deliberate migration)')
        return result

    now = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO strategy_registry (strategy_id, name, version, status, "
        "params_json, added_ts, status_changed_ts, changed_by, family, code_hash) "
        "VALUES (?, ?, 1, 'candidate', '{}', ?, ?, ?, ?, ?) "
        "ON CONFLICT(strategy_id) DO UPDATE SET "
        "version = version + 1, status = 'candidate', status_changed_ts = ?, "
        "changed_by = ?, family = ?, code_hash = ?",
        (strategy_id, strategy_id, now, now, changed_by, family, result.sha256,
         now, changed_by, family, result.sha256))
    conn.commit()
    return result


def verify_hash(conn, strategy_id: str, path: str) -> bool:
    """Loader-side check: file hash must match the registered hash exactly."""
    row = conn.execute('SELECT code_hash FROM strategy_registry WHERE strategy_id = ?',
                       (strategy_id,)).fetchone()
    if not row or not row['code_hash']:
        return False
    return file_sha256(path) == row['code_hash']
