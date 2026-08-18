"""The one place that spawns a Claude CLI subprocess for reasoning work.

Every agent that needs an LLM to *think* (Forge proposals, blowup root cause,
critic post-mortems, vault lessons) goes through here. Nothing else shells out
to `claude` directly, so there is exactly one definition of the timeout, the
tool allowlist, the model routing, and what "the model could not run" means.

## Why a subprocess and not an API call

No API key is stored in this repo and none should be. The `claude` CLI is
already authenticated on this machine, so `claude -p` is the cheapest correct
way to get a reasoning turn from inside a deterministic Python agent.

## Convention 11 is the whole point of the error taxonomy

`NOT_TESTED` means "could not run", never "ran and found nothing". So a failure
to get a model turn must be distinguishable from a model turn that returned
nothing useful:

  LLMUnavailable   the `claude` binary is not on PATH -> NOT_TESTED
  LLMTimeout       the turn exceeded `timeout_s`       -> NOT_TESTED
  LLMFailed        non-zero exit / empty stdout        -> NOT_TESTED
  (returns "")     ran, produced nothing parseable     -> a RESULT, and an
                   empty one, which a caller may legitimately record as
                   "the model declined to propose anything"

Callers that run inside the trading loop MUST treat all four as non-fatal.
A reasoning layer that crashes the shadow loop is worse than no reasoning
layer, so `shadow_runner` and friends wrap every call.

## Convention 19 is enforced on the way in AND on the way out

`json.loads` accepts `NaN`, `Infinity` and `-Infinity`, which are not JSON and
which an LLM will absolutely emit when asked for a number it does not have.
`strict_json_loads` rejects them. Writers use `allow_nan=False`.

## Model routing (Raven's instruction, 2026-08-18)

Reasoning -> Opus. Mechanical summaries of data that is already computed ->
Sonnet. The routing table lives in `MODEL_FOR_TASK` so a caller names a TASK
and does not hardcode a model string; changing the routing is then one edit
here rather than a grep across five agents.
"""
import json
import math
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Iterable, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAUDE_BIN = 'claude'

MODEL_OPUS = 'opus'
MODEL_SONNET = 'sonnet'

# A reasoning turn that reads a lot of evidence is slow. 10 minutes is not a
# performance target, it is the point past which we assume the turn is wedged.
DEFAULT_TIMEOUT_S = 600

# Convention 17: this allowlist is an assumption with an expiry date. It is
# deliberately NOT `*`. A reasoning agent has no business running Bash in a
# repo that holds a live-adjacent trading loop.
DEFAULT_ALLOWED_TOOLS = ('Read', 'Write')

# Named tasks -> model. Callers pass a task name, never a raw model string.
MODEL_FOR_TASK: Dict[str, str] = {
    # Reasoning. Opus.
    'forge_proposals': MODEL_OPUS,
    'strategy_lesson': MODEL_OPUS,
    'blowup_root_cause': MODEL_OPUS,
    'strategy_card': MODEL_OPUS,
    'forge_cycle_takeaway': MODEL_OPUS,
    'critic_post_mortem': MODEL_OPUS,
    # Mechanical. Sonnet.
    'daily_summary': MODEL_SONNET,
    'weekly_summary': MODEL_SONNET,
}

# Set to "1" to make every call return the canned reply instead of spawning a
# subprocess. Tests use this; so does anything that wants to exercise the
# plumbing without spending tokens.
DRY_RUN_ENV = 'AYM_LLM_DRY_RUN'


class LLMError(Exception):
    """Base: the model turn did not produce a usable result."""


class LLMUnavailable(LLMError):
    """The `claude` CLI is not installed / not on PATH."""


class LLMTimeout(LLMError):
    """The turn ran past `timeout_s` and was killed."""


class LLMFailed(LLMError):
    """The turn exited non-zero, or returned nothing at all."""


class LLMResult(object):
    """What one model turn produced, plus how it went.

    `ok` is False only when the turn could not run. A turn that ran and
    returned an empty string is `ok=True` with `text == ''`, because those are
    different facts and Convention 11 says we must not conflate them.
    """

    def __init__(self, text: str, model: str, task: str,
                 duration_s: float, ok: bool = True,
                 error: Optional[str] = None,
                 exit_code: Optional[int] = None) -> None:
        self.text = text
        self.model = model
        self.task = task
        self.duration_s = duration_s
        self.ok = ok
        self.error = error
        self.exit_code = exit_code

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ('LLMResult(task=%r, model=%r, ok=%r, chars=%d, %.1fs)'
                % (self.task, self.model, self.ok, len(self.text),
                   self.duration_s))

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task': self.task,
            'model': self.model,
            'ok': self.ok,
            'error': self.error,
            'exit_code': self.exit_code,
            'duration_s': round(self.duration_s, 2),
            'chars': len(self.text),
        }


def is_available() -> bool:
    """True when a model turn could actually be spawned."""
    if is_dry_run():
        return True
    return shutil.which(CLAUDE_BIN) is not None


def is_dry_run() -> bool:
    return os.environ.get(DRY_RUN_ENV, '') == '1'


def model_for_task(task: str) -> str:
    """Route a named task to a model.

    An unknown task routes to Opus rather than raising. Getting a more
    expensive model is a cost mistake; getting no reasoning at all because a
    caller used a task name that was not in the table is a correctness
    mistake, and we would rather make the cheap one.
    """
    return MODEL_FOR_TASK.get(task, MODEL_OPUS)


def run_task(task: str, prompt: str, **kwargs: Any) -> LLMResult:
    """Run `prompt` on whichever model `task` routes to."""
    kwargs.setdefault('model', model_for_task(task))
    kwargs.setdefault('task', task)
    return run_claude(prompt, **kwargs)


def run_claude(prompt: str,
               model: str = MODEL_OPUS,
               task: str = 'unnamed',
               allowed_tools: Sequence[str] = DEFAULT_ALLOWED_TOOLS,
               cwd: Optional[str] = None,
               timeout_s: int = DEFAULT_TIMEOUT_S,
               extra_args: Iterable[str] = (),
               raise_on_error: bool = False) -> LLMResult:
    """Spawn one `claude -p` turn and return what it said.

    Returns an `LLMResult` with `ok=False` on any failure to RUN. Set
    `raise_on_error=True` to get the exception instead; the default is the
    non-raising form because most callers here sit on a path where a dead
    model must degrade to "no lesson written", not to a stack trace.

    `cwd` defaults to the repo root so the child's Read/Write are rooted where
    the evidence actually is.
    """
    if cwd is None:
        cwd = ROOT
    started = time.time()

    if is_dry_run():
        return LLMResult(_dry_run_reply(task, prompt), model, task,
                         time.time() - started)

    if shutil.which(CLAUDE_BIN) is None:
        exc = LLMUnavailable(
            '%s is not on PATH; reasoning is NOT_TESTED, not failed'
            % CLAUDE_BIN)
        if raise_on_error:
            raise exc
        return LLMResult('', model, task, time.time() - started,
                         ok=False, error=str(exc))

    argv = [CLAUDE_BIN, '-p', prompt, '--model', model]
    if allowed_tools:
        argv += ['--allowedTools', ','.join(allowed_tools)]
    argv += list(extra_args)

    # Convention 14: Hermes leaks a 3.11 venv onto PYTHONPATH and numpy then
    # fails like a broken install. The child may run Python; strip it.
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)

    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        exc = LLMTimeout('%s turn for task %r exceeded %ds'
                         % (model, task, timeout_s))
        if raise_on_error:
            raise exc
        return LLMResult('', model, task, time.time() - started,
                         ok=False, error=str(exc))
    except OSError as exc_os:
        exc = LLMFailed('could not spawn %s: %s' % (CLAUDE_BIN, exc_os))
        if raise_on_error:
            raise exc
        return LLMResult('', model, task, time.time() - started,
                         ok=False, error=str(exc))

    duration = time.time() - started
    stdout = (proc.stdout or b'').decode('utf-8', 'replace').strip()
    stderr = (proc.stderr or b'').decode('utf-8', 'replace').strip()

    if proc.returncode != 0:
        exc = LLMFailed('%s exited %d for task %r: %s'
                        % (CLAUDE_BIN, proc.returncode, task, stderr[:500]))
        if raise_on_error:
            raise exc
        return LLMResult('', model, task, duration, ok=False,
                         error=str(exc), exit_code=proc.returncode)

    if not stdout:
        exc = LLMFailed('%s exited 0 but wrote nothing for task %r'
                        % (CLAUDE_BIN, task))
        if raise_on_error:
            raise exc
        return LLMResult('', model, task, duration, ok=False,
                         error=str(exc), exit_code=0)

    return LLMResult(stdout, model, task, duration, exit_code=0)


def _dry_run_reply(task: str, prompt: str) -> str:
    """A canned reply that is obviously canned.

    It must never look like a real model answer in a log, so it says so.
    """
    return ('DRY_RUN: no model was called for task %r (prompt was %d chars). '
            'Set %s=0 to spawn a real turn.' % (task, len(prompt), DRY_RUN_ENV))


# --------------------------------------------------------------------------
# JSON handling
# --------------------------------------------------------------------------

_NON_FINITE = re.compile(r'(?<![\w."])(-?Infinity|NaN)(?![\w"])')

_FENCE = re.compile(r'```(?:json)?\s*\n(.*?)\n?```', re.DOTALL)


def strict_json_loads(text: str) -> Any:
    """`json.loads` minus the three tokens that are not JSON.

    Convention 19. `json.loads('{"edge": NaN}')` succeeds in CPython and hands
    you a float that poisons every comparison downstream. Here it raises.
    """
    if _NON_FINITE.search(text):
        raise ValueError('non-finite JSON token (NaN/Infinity) in model '
                         'output; Convention 19 forbids it')
    parsed = json.loads(text, parse_constant=_reject_constant)
    _assert_finite(parsed)
    return parsed


def _reject_constant(name: str) -> Any:
    raise ValueError('non-finite JSON constant %r; Convention 19' % name)


def _assert_finite(node: Any, path: str = '$') -> None:
    if isinstance(node, float) and not math.isfinite(node):
        raise ValueError('non-finite number at %s' % path)
    if isinstance(node, dict):
        for key, value in node.items():
            _assert_finite(value, '%s.%s' % (path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_finite(value, '%s[%d]' % (path, i))


def extract_json(text: str) -> Any:
    """Pull the JSON payload out of a model turn.

    A model asked for JSON will wrap it in a fence, or prepend a sentence, or
    both. Tries, in order: a fenced block, the whole string, then the widest
    brace/bracket span. Raises `ValueError` if none of those parse, because a
    caller that cannot read the answer must not proceed as though it could.
    """
    candidates = []
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1))
    candidates.append(text)

    stripped = text.strip()
    for opener, closer in (('{', '}'), ('[', ']')):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            candidates.append(stripped[start:end + 1])

    errors = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return strict_json_loads(candidate)
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    raise ValueError('no parseable JSON in model output (%d candidates tried; '
                     'first error: %s)'
                     % (len(candidates), errors[0] if errors else 'none'))


def dump_json(obj: Any, **kwargs: Any) -> str:
    """`json.dumps` with Convention 19 wired on."""
    kwargs.setdefault('allow_nan', False)
    kwargs.setdefault('indent', 2)
    kwargs.setdefault('sort_keys', True)
    return json.dumps(obj, **kwargs)
