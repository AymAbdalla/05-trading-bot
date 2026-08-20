"""The equity carry-over knob on all three shadow launchers (D-383).

WHY THIS FILE EXISTS. On 2026-08-20 all three shadow books were restarted with
`./run_polymarket_shadow_<x>.sh --equity <number>`. None of the three launchers
has ever read `"$@"` - they are configured by environment variable - so every
one of those flags was silently discarded and all three books came up at the
$1,000 default instead of the equity they had been carrying. That injected
$106.48, $218.15 and $209.44 of capital that had never been traded for, and
nothing failed: three books came up green and the only evidence was a step in
`equity_snapshots`.

Two of the three could not have honoured the flag under any spelling, because
only main had `STARTING_EQUITY` plumbing at all. "Preserve equity, do not
re-fund" was therefore impossible through the safe path on env B and realm C -
the only way to pass `--equity` was to invoke the module directly, skipping all
five gates the launchers exist to enforce.

So this pins the two halves of the fix, for each launcher, BY EXECUTING IT:

  * an argument is REFUSED, loudly, naming the variable that does work;
  * `STARTING_EQUITY` actually reaches the module as `--equity`.

Convention 22: a claim in a comment is not a wiring test. The sandbox is the
one `test_launcher_banner.py` established - a throwaway repo-shaped directory
and a stub `python3` earlier on PATH - because reaching the launch line in the
real repo would start a second loop against a live database.
"""
import os
import shutil
import subprocess

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLEARED = ('AGENT_ID', 'TRADING_BOT_AGENT_ID', 'TRADING_LIVE_ACK', 'PYTHONPATH',
           'STARTING_EQUITY', 'POLL_SEC', 'DB', 'LOG_DIR', 'STRATEGIES',
           'UNPAUSE')

#: The three launchers and the extra files each needs in the sandbox.
LAUNCHERS = ('run_polymarket_shadow.sh',
             'run_polymarket_shadow_envb.sh',
             'run_polymarket_shadow_realmc.sh')

#: Like `test_launcher_banner`'s stub, but the launch branch RECORDS its argv
#: instead of discarding it - that recording is the whole subject here.
STUB_PYTHON3 = r"""#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.99.0-stub"
    exit 0
fi
if [ "${1:-}" = "-c" ]; then
    # The halt gate reads exit 0 as "halted"; exit 1 lets the script proceed.
    exit 1
fi
if [ "${1:-}" = "-u" ]; then
    printf '%s\n' "$*" > "${ARGV_SINK}"
    exit 0
fi
if [ "${1:-}" = "-" ]; then
    src="$(cat)"
    case "$src" in
        *config.yaml*) printf 'paper' ;;
        *) echo "stub: gate OK" ;;
    esac
    exit 0
fi
exit 0
"""


def _sandbox(tmp_path, launcher):
    shutil.copy2(os.path.join(REPO_ROOT, launcher), str(tmp_path / launcher))
    (tmp_path / 'config.yaml').write_text('mode: paper\n')
    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    stub = bindir / 'python3'
    stub.write_text(STUB_PYTHON3)
    stub.chmod(0o755)
    return tmp_path


def _run(tmp_path, launcher, args=(), starting_equity=None):
    """Run the copied launcher. Returns (returncode, output, argv_seen)."""
    sandbox = _sandbox(tmp_path, launcher)
    sink = sandbox / 'argv.txt'
    env = dict(os.environ)
    for name in CLEARED:
        env.pop(name, None)
    if starting_equity is not None:
        env['STARTING_EQUITY'] = str(starting_equity)
    env['ARGV_SINK'] = str(sink)
    env['PATH'] = str(sandbox / 'bin') + os.pathsep + env.get('PATH', '')
    proc = subprocess.Popen(
        ['bash', str(sandbox / launcher)] + list(args),
        cwd=str(sandbox), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = proc.communicate(timeout=180)
    argv = sink.read_text().strip() if sink.exists() else None
    return proc.returncode, out, argv


# ---------------------------------------------------------------------------
# Gate 0: the flag that was silently swallowed is now refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_the_exact_command_that_refunded_the_books_is_refused(tmp_path,
                                                              launcher):
    """`--equity <n>` on the command line. This is the incident, replayed."""
    rc, out, argv = _run(tmp_path, launcher, args=['--equity', '893.5235'])

    assert rc != 0, out
    assert 'REFUSING TO START' in out, out
    # It must not have reached the launch line.
    assert argv is None, argv


@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_the_refusal_names_the_variable_that_actually_works(tmp_path, launcher):
    """A refusal that does not say what to do instead just gets worked around."""
    _, out, _ = _run(tmp_path, launcher, args=['--equity', '900'])

    assert 'STARTING_EQUITY' in out, out


@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_any_stray_argument_is_refused_not_just_equity(tmp_path, launcher):
    rc, out, _ = _run(tmp_path, launcher, args=['--poll', '10'])

    assert rc != 0
    assert 'REFUSING TO START' in out, out


# ---------------------------------------------------------------------------
# The knob itself: STARTING_EQUITY reaches the module
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_starting_equity_reaches_the_module_as_the_equity_flag(tmp_path,
                                                               launcher):
    """The carry-over path. Before D-383 this was true of main only."""
    rc, out, argv = _run(tmp_path, launcher, starting_equity='866.0535')

    assert rc == 0, out
    assert argv is not None, out
    assert '--equity 866.0535' in argv, argv


@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_the_default_is_the_thousand_dollar_paper_book(tmp_path, launcher):
    """Unset means a fresh $1,000 book - the D-358 re-fund, stated explicitly."""
    rc, out, argv = _run(tmp_path, launcher)

    assert rc == 0, out
    assert '--equity 1000' in argv, argv


@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_the_started_equity_is_recorded_in_the_banner(tmp_path, launcher):
    """A restart's carry-over must be readable from the log afterwards.

    The equity a book started with is not recoverable from `ps` once the
    process is gone, and it is the number every cross-restart measurement
    depends on.
    """
    _, out, _ = _run(tmp_path, launcher, starting_equity='866.0535')

    assert '866.0535' in out, out


# ---------------------------------------------------------------------------
# Structural: all three launchers agree
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('launcher', LAUNCHERS)
def test_every_launcher_has_the_knob_and_the_gate(launcher):
    """These three files are deliberately parallel copies, not a shared helper
    (see main's Gate 4 comment). Parallel copies drift, so pin all three."""
    src = open(os.path.join(REPO_ROOT, launcher)).read()

    assert 'STARTING_EQUITY="${STARTING_EQUITY:-1000}"' in src
    assert '--equity "${STARTING_EQUITY}"' in src
    assert 'if [ "$#" -gt 0 ]; then' in src
