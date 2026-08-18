"""Resolve the reason argument of every `decide('SKIP', ...)` in the package.

D-290, Option A. This exists because the guard it feeds -
`test_every_skip_reason_the_strategies_emit_is_classified` - only ever looked
at STRING LITERALS, and seven call sites in `strategies/polymarket/` pass a
VARIABLE. Sixteen reason strings were therefore invisible to it, and the suite
was green over them BY ACCIDENT rather than by coverage.

Two ways out were on the table. Option B was to BAN non-literal arguments,
forcing every module to re-spell shared reasons at the call site. That breaks
convention 20 - one cause, one name, across modules - and re-spelling
`liquidation_feed_empty` in a third module is exactly how `no_lead_or_atr`
happened. Option A, taken here, teaches the reader to follow the indirection.

WHAT IT RESOLVES, in order:

  1. a string literal
  2. an `IfExp` whose branches both resolve
  3. string concatenation with a constant part -> a PREFIX family
     (`'fair_value_' + est.reason`), checked against the classifier's prefix
     handling rather than expanded into members it cannot know
  4. a name bound at MODULE level to a string, or to a tuple/list of strings,
     including tuples built by `+` from other module constants and constants
     IMPORTED from a sibling module (`tuple(NO_DATA_REASONS)`)
  5. a name bound inside the enclosing function, including tuple-unpacking
     from a call (`_, status = f()`) - resolved by reading every `return` the
     producing function can take at that position
  6. an attribute of a value returned by a call (`liq.reason`) - resolved by
     collecting every value that flows into that field name inside the
     producing function, following one level of nested-helper parameter
     substitution (`fail(reason)` -> `LiquidationWindow(reason=reason)`)

WHAT IT DOES NOT DO: guess. An expression it cannot follow comes back as
`Unresolved` carrying file, line and the source text, and the caller FAILS on
it by name. That clause is the load-bearing half of D-290: without it Option A
is just a larger version of the same blind spot, quieter for being bigger.

The whole module is READ-ONLY static analysis over the source tree. It imports
nothing from the strategies and executes none of their code, so it works on a
module whose imports are broken and cannot be fooled by one whose constants are
computed at runtime - it reports those as unresolved instead.
"""
import ast
import os
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Call-site coverage is only asked of packages that actually emit skip rows.
STRATEGY_PACKAGE = ('strategies', 'polymarket')

#: Constructors that pass a value straight through to a field of the same name.
#: `tuple(NO_DATA_REASONS)` is a re-typing, not a transformation.
_PASSTHROUGH_CALLS = ('tuple', 'list', 'set', 'frozenset')


def is_success_sentinel(value: str) -> bool:
    """`'ok'` / `'ok_from_city_fallback_table'` - a producer SUCCEEDED.

    The resolver is an over-approximation on purpose: for `_, s = producer()`
    it collects every value `producer` can return, and the success value is
    one of them. The call site is guarded (`if implied is None:`,
    `if feed.status != 'ok':`) so the sentinel never actually reaches
    `decide('SKIP', ...)`, but the guards take four different shapes across
    the seven sites and reading them is a worse dependency than this rule.

    The rule is the package's existing naming convention, not a special case:
    `FeedRead.status` is documented as "'ok' or a FEED_SKIP_REASONS member",
    and `resolution_station_checked` returns `'ok'` / `'ok_from_...'`.

    Over-approximating the OTHER way would be the dangerous direction, so this
    is the one narrow filter and it is pinned:
    `test_no_classified_reason_looks_like_a_success_sentinel` fails if a real
    skip reason is ever named such that this rule would swallow it.
    """
    return value == 'ok' or value.startswith('ok_')


class Unresolved:
    """One `decide('SKIP', <expr>)` this module could not follow.

    Carries enough to fix it without re-running anything: the file, the line
    and the expression as written.
    """

    __slots__ = ('module', 'lineno', 'expr', 'why')

    def __init__(self, module: str, lineno: int, expr: str, why: str):
        self.module = module
        self.lineno = lineno
        self.expr = expr
        self.why = why

    def __repr__(self) -> str:
        return '%s:%d  decide(\'SKIP\', %s)  [%s]' % (
            self.module, self.lineno, self.expr, self.why)

    def __eq__(self, other) -> bool:
        return (isinstance(other, Unresolved)
                and (self.module, self.expr) == (other.module, other.expr))

    def __hash__(self) -> int:
        return hash((self.module, self.expr))


class Prefix:
    """A reason emitted as `'<literal>' + <something>`.

    Deliberately NOT expanded into members. The variable half is a runtime
    value this module cannot know, so the honest claim is about the FAMILY:
    the classifier must handle the prefix, and then every member is covered
    whatever it turns out to be.
    """

    __slots__ = ('value',)

    def __init__(self, value: str):
        self.value = value

    def __repr__(self) -> str:
        return 'Prefix(%r)' % self.value

    def __eq__(self, other) -> bool:
        return isinstance(other, Prefix) and self.value == other.value

    def __hash__(self) -> int:
        return hash(('prefix', self.value))


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:               # pragma: no cover - py<3.9 safety net
        return ast.dump(node)


class Module:
    """One parsed source file, plus the module-level facts worth caching."""

    def __init__(self, dotted: str, path: str, index: 'Index'):
        self.dotted = dotted
        self.path = path
        self.name = os.path.basename(path)
        self.index = index
        self.tree = ast.parse(open(path).read())
        self.functions: Dict[str, ast.FunctionDef] = {}
        self.classes: Dict[str, ast.ClassDef] = {}
        #: name -> dotted module it was imported FROM. Follows `from x import y`
        #: only; a plain `import x` is never used for a reason constant here.
        self.imported_from: Dict[str, str] = {}
        self._consts: Optional[Dict[str, FrozenSet[str]]] = None

        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.imported_from[alias.asname or alias.name] = \
                        node.module
        # Methods are reachable as `self.x(...)` producers too.
        for cls in self.classes.values():
            for node in cls.body:
                if isinstance(node, ast.FunctionDef):
                    self.functions.setdefault(node.name, node)

    # -- module-level constants --------------------------------------------
    @property
    def consts(self) -> Dict[str, FrozenSet[str]]:
        if self._consts is None:
            # Assigned BEFORE the walk: a self-referential constant would
            # otherwise recurse forever rather than simply failing to resolve.
            self._consts = {}
            for node in self.tree.body:
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    targets, value = [node.target], node.value
                else:
                    continue
                resolved = self._const_value(value)
                if resolved is None:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name):
                        self._consts[target.id] = resolved
        return self._consts

    def _const_value(self, node: ast.AST) -> Optional[FrozenSet[str]]:
        """A module-level expression as a set of strings, or None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return frozenset([node.value])
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            out: Set[str] = set()
            for element in node.elts:
                part = self._const_value(element)
                if part is None:
                    return None
                out |= part
            return frozenset(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._const_value(node.left)
            right = self._const_value(node.right)
            if left is None or right is None:
                return None
            return left | right
        if isinstance(node, ast.Call) \
                and getattr(node.func, 'id', '') in _PASSTHROUGH_CALLS \
                and len(node.args) == 1:
            return self._const_value(node.args[0])
        if isinstance(node, ast.Name):
            return self.lookup_const(node.id)
        return None

    def lookup_const(self, name: str) -> Optional[FrozenSet[str]]:
        """This module's constant, or the one it imported under that name."""
        if name in self.consts:
            return self.consts[name]
        source = self.imported_from.get(name)
        if source is None:
            return None
        other = self.index.load(source)
        if other is None or other is self:
            return None
        return other.lookup_const(name)

    def lookup_function(self, name: str) \
            -> Optional[Tuple['Module', ast.FunctionDef]]:
        if name in self.functions:
            return self, self.functions[name]
        source = self.imported_from.get(name)
        if source is None:
            return None
        other = self.index.load(source)
        if other is None or other is self:
            return None
        return other.lookup_function(name)


class Index:
    """Dotted-module -> Module, loaded from the source tree, cached."""

    def __init__(self, root: str = ROOT):
        self.root = root
        self._cache: Dict[str, Optional[Module]] = {}

    def load(self, dotted: str) -> Optional[Module]:
        if dotted in self._cache:
            return self._cache[dotted]
        path = os.path.join(self.root, *dotted.split('.')) + '.py'
        module: Optional[Module] = None
        if os.path.exists(path):
            try:
                module = Module(dotted, path, self)
            except (SyntaxError, OSError):      # pragma: no cover
                module = None
        self._cache[dotted] = module
        return module

    def load_path(self, path: str) -> Module:
        dotted = os.path.relpath(path, self.root)[:-3].replace(os.sep, '.')
        module = self.load(dotted)
        if module is None:                      # pragma: no cover
            raise AssertionError('could not parse %s' % path)
        return module


# ---------------------------------------------------------------------------
# Scope: what a name is bound to inside one function
# ---------------------------------------------------------------------------

class _Scope:
    """Every binding of every name inside one function body.

    A list per name, not a single value: `reason = 'a'` in one branch and
    `reason = 'b'` in another are BOTH reachable, and taking the last one is
    how you lose half a reason family.
    """

    def __init__(self, func: ast.FunctionDef):
        self.func = func
        self.bindings: Dict[str, List[Tuple[str, ast.AST, int]]] = {}
        self.params: List[str] = [a.arg for a in func.args.args] \
            + [a.arg for a in func.args.kwonlyargs]
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._bind(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._bind(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                # A loop variable is a runtime value; recorded so the name is
                # KNOWN-unresolvable rather than mistaken for a module const.
                self._bind_opaque(node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars:
                self._bind_opaque(node.optional_vars)

    def _bind(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.bindings.setdefault(target.id, []).append(
                ('direct', value, -1))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for i, element in enumerate(target.elts):
                if isinstance(element, ast.Name):
                    self.bindings.setdefault(element.id, []).append(
                        ('unpack', value, i))

    def _bind_opaque(self, target: ast.AST) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                self.bindings.setdefault(node.id, []).append(
                    ('opaque', node, -1))


def _returns(func: ast.FunctionDef) -> List[ast.AST]:
    """Every `return <value>` in `func`, EXCLUDING its nested functions.

    A nested helper's returns belong to the helper, not to the outer function.
    Pooling them is how `fail()`'s return would be mistaken for
    `read_liquidation_window()`'s.
    """
    out: List[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return) and child.value is not None:
                out.append(child.value)
            walk(child)

    walk(func)
    return out


def _nested_functions(func: ast.FunctionDef) -> Dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(func)
            if isinstance(n, ast.FunctionDef) and n is not func}


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

class Resolver:

    #: Guards against a cycle in mutually recursive helpers. Depth, not a
    #: visited-set, because the same function CAN legitimately be entered twice
    #: down two different paths.
    MAX_DEPTH = 6

    def __init__(self, index: Optional[Index] = None):
        self.index = index or Index()

    def resolve(self, node: ast.AST, module: Module,
                scope: Optional[_Scope], depth: int = 0):
        """-> set of strings, a Prefix, or None (meaning: could not follow)."""
        if depth > self.MAX_DEPTH:
            return None

        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return {node.value}
            return None

        if isinstance(node, ast.IfExp):
            left = self.resolve(node.body, module, scope, depth + 1)
            right = self.resolve(node.orelse, module, scope, depth + 1)
            if isinstance(left, set) and isinstance(right, set):
                return left | right
            return None

        if isinstance(node, ast.BoolOp):
            # `est.reason or 'unusable'` - every operand is reachable.
            parts = [self.resolve(v, module, scope, depth + 1)
                     for v in node.values]
            if all(isinstance(p, set) for p in parts):
                out: Set[str] = set()
                for part in parts:
                    out |= part
                return out
            return None

        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # A prefix family. The literal half is the claim; the other half
            # is a runtime value and is not guessed at.
            if isinstance(node.left, ast.Constant) \
                    and isinstance(node.left.value, str):
                return Prefix(node.left.value)
            return None

        if isinstance(node, ast.Call):
            if getattr(node.func, 'id', '') in _PASSTHROUGH_CALLS \
                    and len(node.args) == 1:
                return self.resolve(node.args[0], module, scope, depth + 1)
            return None

        if isinstance(node, ast.Name):
            return self._resolve_name(node, module, scope, depth)

        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(node, module, scope, depth)

        return None

    # -- names --------------------------------------------------------------
    def _resolve_name(self, node: ast.Name, module: Module,
                      scope: Optional[_Scope], depth: int):
        if scope is not None and node.id in scope.bindings:
            out: Set[str] = set()
            for kind, value, position in scope.bindings[node.id]:
                if kind == 'opaque':
                    return None
                if kind == 'direct':
                    part = self.resolve(value, module, scope, depth + 1)
                else:
                    part = self._resolve_unpack(value, position, module,
                                                scope, depth)
                if not isinstance(part, set):
                    return None
                out |= part
            return out
        constant = module.lookup_const(node.id)
        return set(constant) if constant is not None else None

    def _resolve_unpack(self, value: ast.AST, position: int, module: Module,
                        scope: Optional[_Scope], depth: int):
        """`_, status = producer()` -> element `position` of every return."""
        if isinstance(value, (ast.Tuple, ast.List)):
            if position < len(value.elts):
                return self.resolve(value.elts[position], module, scope,
                                    depth + 1)
            return None
        if not isinstance(value, ast.Call):
            return None
        found = self._producer(value, module)
        if found is None:
            return None
        producer_module, producer = found
        producer_scope = _Scope(producer)
        out: Set[str] = set()
        for returned in _returns(producer):
            if not isinstance(returned, (ast.Tuple, ast.List)):
                return None
            if position >= len(returned.elts):
                return None
            part = self.resolve(returned.elts[position], producer_module,
                                producer_scope, depth + 1)
            if not isinstance(part, set):
                return None
            out |= part
        return out or None

    # -- attributes ---------------------------------------------------------
    def _resolve_attribute(self, node: ast.Attribute, module: Module,
                           scope: Optional[_Scope], depth: int):
        """`liq.reason` -> everything that reaches the `reason` field.

        The producing call is found first, so this is not "any field called
        `reason` anywhere" - it is the field of the object THIS call returns.
        """
        if not isinstance(node.value, ast.Name) or scope is None:
            return None
        bindings = scope.bindings.get(node.value.id)
        if not bindings:
            return None
        out: Set[str] = set()
        for kind, value, _position in bindings:
            if kind != 'direct' or not isinstance(value, ast.Call):
                return None
            found = self._producer(value, module)
            if found is None:
                return None
            producer_module, producer = found
            part = self._field_values(node.attr, producer, producer_module,
                                      depth)
            if part is None:
                return None
            out |= part
        return out or None

    def _field_values(self, field: str, producer: ast.FunctionDef,
                      producer_module: Module, depth: int) \
            -> Optional[Set[str]]:
        """Every string that reaches `<field>=` inside `producer`.

        `None` values are DROPPED, not treated as unresolvable: a dataclass
        field set to None is the "no reason, it succeeded" case, and it is
        never a skip reason.
        """
        scope = _Scope(producer)
        nested = _nested_functions(producer)
        out: Set[str] = set()
        for call in [n for n in ast.walk(producer) if isinstance(n, ast.Call)]:
            for keyword in call.keywords:
                if keyword.arg != field:
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value is None:
                    continue
                part = self.resolve(value, producer_module, scope, depth + 1)
                if isinstance(part, set):
                    out |= part
                    continue
                # `LiquidationWindow(reason=reason)` inside `def fail(reason)`.
                # Substitute every argument the helper is actually called with.
                part = self._substitute_parameter(value, nested, producer,
                                                  producer_module, scope,
                                                  depth)
                if part is None:
                    return None
                out |= part
        return out

    def _substitute_parameter(self, value: ast.AST,
                              nested: Dict[str, ast.FunctionDef],
                              producer: ast.FunctionDef,
                              producer_module: Module, scope: _Scope,
                              depth: int) -> Optional[Set[str]]:
        """A nested helper's parameter -> the args its call sites pass it."""
        if not isinstance(value, ast.Name):
            return None
        out: Set[str] = set()
        matched = False
        for helper_name, helper in nested.items():
            params = [a.arg for a in helper.args.args]
            if value.id not in params:
                continue
            position = params.index(value.id)
            matched = True
            for call in [n for n in ast.walk(producer)
                         if isinstance(n, ast.Call)
                         and getattr(n.func, 'id', '') == helper_name]:
                argument: Optional[ast.AST] = None
                if position < len(call.args):
                    argument = call.args[position]
                else:
                    for keyword in call.keywords:
                        if keyword.arg == value.id:
                            argument = keyword.value
                if argument is None:
                    return None
                part = self.resolve(argument, producer_module, scope,
                                    depth + 1)
                if not isinstance(part, set):
                    return None
                out |= part
        return out if matched else None

    # -- producers ----------------------------------------------------------
    def _producer(self, call: ast.Call, module: Module) \
            -> Optional[Tuple[Module, ast.FunctionDef]]:
        func = call.func
        if isinstance(func, ast.Name):
            return module.lookup_function(func.id)
        if isinstance(func, ast.Attribute):
            # `self.read_feed(...)` / `mod.read_feed(...)`. The method name is
            # unique enough inside one package; a miss returns None and the
            # site is reported unresolved rather than guessed.
            return module.lookup_function(func.attr)
        return None


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def string_literals_in(*packages) -> FrozenSet[str]:
    """Every string constant appearing anywhere in the given packages.

    Used ONLY by the reverse check, and the asymmetry is deliberate. Going
    forward (code -> table) a false positive puts an unclassified reason in
    the record, so that direction gets the careful resolver. Going backward
    (table -> code) the question is merely "does this string appear anywhere
    at all", and a crude literal sweep is the RIGHT tool: it over-reports
    reachability, which at worst leaves a dead entry in place, rather than
    under-reporting it and deleting a classification that is still in use.

    It also catches the emitters the resolver deliberately does not model -
    `shadow_loop.py` attributes its own cycle-level failures to each strategy,
    so `api_error` and `cycle_exception` reach the table without ever passing
    through a `decide('SKIP', ...)`.
    """
    import glob

    out: Set[str] = set()
    for package in packages:
        for path in glob.glob(os.path.join(ROOT, *package, '*.py')):
            for node in ast.walk(ast.parse(open(path).read())):
                if isinstance(node, ast.Constant) \
                        and isinstance(node.value, str):
                    out.add(node.value)
    return frozenset(out)


def _enclosing_functions(tree: ast.AST) -> Dict[int, ast.FunctionDef]:
    """id(Call node) -> the FunctionDef it sits in (innermost wins)."""
    out: Dict[int, ast.FunctionDef] = {}

    def walk(node: ast.AST, current: Optional[ast.FunctionDef]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, ast.FunctionDef) else current
            if isinstance(child, ast.Call) and inner is not None:
                out[id(child)] = inner
            walk(child, inner)

    walk(tree, None)
    return out


def skip_reason_sites(paths=None, index: Optional[Index] = None):
    """Every `decide('SKIP', ...)` in the strategy package, resolved.

    Returns `(reasons, prefixes, unresolved)`:
      reasons     {reason string: sorted [module basenames that can emit it]}
      prefixes    {prefix string: sorted [module basenames]}
      unresolved  [Unresolved] - the loud half of D-290
    """
    import glob

    index = index or Index()
    resolver = Resolver(index)
    if paths is None:
        paths = sorted(glob.glob(
            os.path.join(ROOT, *STRATEGY_PACKAGE, '*.py')))

    reasons: Dict[str, Set[str]] = {}
    prefixes: Dict[str, Set[str]] = {}
    unresolved: List[Unresolved] = []

    for path in paths:
        module = index.load_path(path)
        enclosing = _enclosing_functions(module.tree)
        scopes: Dict[int, _Scope] = {}
        for node in ast.walk(module.tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, 'id', '') == 'decide'):
                continue
            args = node.args
            if not (len(args) >= 2 and isinstance(args[0], ast.Constant)
                    and args[0].value == 'SKIP'):
                continue
            func = enclosing.get(id(node))
            scope = None
            if func is not None:
                if id(func) not in scopes:
                    scopes[id(func)] = _Scope(func)
                scope = scopes[id(func)]
            result = resolver.resolve(args[1], module, scope)
            if isinstance(result, Prefix):
                prefixes.setdefault(result.value, set()).add(module.name)
            elif isinstance(result, set) and result:
                for reason in result:
                    if is_success_sentinel(reason):
                        continue
                    reasons.setdefault(reason, set()).add(module.name)
            else:
                unresolved.append(Unresolved(
                    module.name, getattr(args[1], 'lineno', node.lineno),
                    _unparse(args[1]),
                    'no resolution rule follows this expression'))

    return ({r: sorted(owners) for r, owners in reasons.items()},
            {p: sorted(owners) for p, owners in prefixes.items()},
            unresolved)
