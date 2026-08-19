"""Tests for the launch-provenance banner in run_polymarket_shadow.sh (D-332).

Convention 22: a claim in a comment is not a wiring test. The script CLAIMS its
banner records who launched the loop, so what is pinned here is the printing --
by executing the real script and reading its real stdout.

The script cannot be pointed at the real repo. Reaching the banner means
reaching the line after it, which starts a SECOND shadow loop against
db/trading.db while the live one is writing to it (convention 21). So every
test builds a THROWAWAY repo-shaped directory under tmp_path holding a copy of
the script, a paper-mode config.yaml, and a stub `python3` earlier on PATH that
answers the four things the gates ask it and does nothing whatsoever when asked
to run engine.polymarket.shadow_loop. Nothing here touches the real db, the
real logs, the real config or the real engine.

The two load-bearing tests are the ones the forensics gap in D-332 needs:

  * test_a_declared_agent_id_is_carried_into_the_banner
  * test_the_banner_pids_are_the_real_launcher_and_its_parent

The second is why both pids are printed rather than one, and it is not
cosmetic: `$$` and `${PPID}` are expanded inside a `{ ... } | tee` pipeline,
which bash runs in a SUBSHELL. Both happen to survive that (`$$` is the shell's
pid, not the subshell's; `PPID` is fixed at shell startup), but "happens to" is
not something anyone should have to re-derive at 2am during an incident. The
test compares the printed numbers against the pids pytest itself observes, so
if a future edit moves them somewhere a subshell does rewrite them, it fails.
"""

import os
import shutil
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'run_polymarket_shadow.sh')

#: Cleared from every child environment. Whoever runs pytest may well have
#: AGENT_ID exported -- this session was spawned with one (D-331) -- and
#: inheriting it would make the UNDECLARED test declare. TRADING_LIVE_ACK is
#: cleared because gate 2 refuses to start at all when it is set.
CLEARED = ('AGENT_ID', 'TRADING_BOT_AGENT_ID', 'TRADING_LIVE_ACK', 'PYTHONPATH')

#: Stand-in for python3 across the launcher's five invocations of it. Answers
#: by the shape of argv, imports nothing, and refuses nothing: the gates are
#: not what is under test here, the banner is. tests/test_polymarket_*.py
#: already pin the real gates against the real engine.
STUB_PYTHON3 = r"""#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
    echo "Python 3.99.0-stub"
    exit 0
fi
if [ "${1:-}" = "-c" ]; then
    # Gate 4, the halt check. It reads exit 0 as "halted", so exit 1 is the
    # answer that lets the script proceed to the banner.
    exit 1
fi
if [ "${1:-}" = "-u" ]; then
    # The launch itself. Exit immediately, enter nothing, touch no database.
    exit 0
fi
if [ "${1:-}" = "-" ]; then
    src="$(cat)"
    case "$src" in
        *config.yaml*) printf 'paper' ;;             # gate 1: the mode probe
        *) echo "stub: paper-mode assertions OK" ;;  # gate 3: the assertions
    esac
    exit 0
fi
exit 0
"""


def _sandbox(tmp_path):
    shutil.copy2(SCRIPT, str(tmp_path / 'run_polymarket_shadow.sh'))
    (tmp_path / 'config.yaml').write_text('mode: paper\n')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    stub = bindir / 'python3'
    stub.write_text(STUB_PYTHON3)
    stub.chmod(0o755)
    return tmp_path


def _run(tmp_path, agent_id=None):
    """Run the copied launcher to completion. Returns (proc, output, sandbox)."""
    sandbox = _sandbox(tmp_path)
    env = dict(os.environ)
    for name in CLEARED:
        env.pop(name, None)
    if agent_id is not None:
        env['AGENT_ID'] = agent_id
    env['PATH'] = str(sandbox / 'bin') + os.pathsep + env.get('PATH', '')
    proc = subprocess.Popen(
        ['bash', str(sandbox / 'run_polymarket_shadow.sh')],
        cwd=str(sandbox), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = proc.communicate(timeout=120)
    return proc, out, sandbox


# ---------------------------------------------------------------------------
# the banner, executed
# ---------------------------------------------------------------------------

def test_a_declared_agent_id_is_carried_into_the_banner(tmp_path):
    proc, out, _ = _run(tmp_path, agent_id='cody-some-topic')
    assert proc.returncode == 0, out
    assert 'launched-by: cody-some-topic' in out, out


def test_an_undeclared_launcher_says_so_rather_than_defaulting(tmp_path):
    """A human shell exports nothing. The banner must say so, not guess.

    UNDECLARED is a measurement of ignorance, and convention 20 wants those
    visible rather than papered over with a plausible-looking default.
    """
    proc, out, _ = _run(tmp_path)
    assert proc.returncode == 0, out
    assert 'launched-by: UNDECLARED' in out, out


def test_the_banner_pids_are_the_real_launcher_and_its_parent(tmp_path):
    """$$ is the bash pid pytest spawned; ${PPID} is pytest's own."""
    proc, out, _ = _run(tmp_path, agent_id='cody-pid-check')
    assert proc.returncode == 0, out
    assert 'launcher-pid: %d' % proc.pid in out, out
    assert 'parent-pid: %d' % os.getpid() in out, out


def test_the_provenance_reaches_the_log_file_and_not_only_the_terminal(tmp_path):
    """The banner is teed. A record only on a terminal that is long gone is no
    record at all -- the forensics question is always asked days later."""
    proc, out, sandbox = _run(tmp_path, agent_id='cody-tee-check')
    assert proc.returncode == 0, out
    logs = sorted((sandbox / 'logs').glob('polymarket_shadow_*.log'))
    assert len(logs) == 1, [p.name for p in logs]
    written = logs[0].read_text()
    assert 'launched-by: cody-tee-check' in written, written
    assert 'launcher-pid: %d' % proc.pid in written, written


# ---------------------------------------------------------------------------
# the banner, as source
# ---------------------------------------------------------------------------

def test_the_provenance_lines_sit_inside_the_banner_block():
    """Pins placement, which the subprocess tests cannot distinguish: a line
    printed after the closing `===` would still show up in stdout."""
    with open(SCRIPT) as handle:
        source = handle.read()
    opening = source.index('=== polymarket shadow session')
    closing = source.index('echo "==="', opening)
    block = source[opening:closing]
    assert 'echo "launched-by: ${AGENT_ID:-UNDECLARED}"' in block, block
    assert 'echo "launcher-pid: $$' in block, block
    assert 'parent-pid: ${PPID}"' in block, block


def test_the_launch_block_still_starts_the_real_module():
    """The one thing this session must not have broken. The banner edit sits
    directly above the launch; a bad splice there is silent until a restart."""
    with open(SCRIPT) as handle:
        source = handle.read()
    assert '-u -m engine.polymarket.shadow_loop' in source
    assert 'CHILD=$!' in source
    assert 'kill -0 "${CHILD}"' in source
