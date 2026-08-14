"""botctl: human controls for the engine (SPEC 3.1, 6.3).

Usage:
  python3 botctl.py status            # positions, equity, halt state
  python3 botctl.py halt "reason"     # write HALT file; engine closes all and stops entering
  python3 botctl.py resume --ack ID   # remove HALT file (requires the halt id printed by halt)

The HALT file is the kill switch's persistence: the executor checks it every
step, so a halt survives engine restarts until a human resumes.
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HALT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'HALT')


def cmd_status():
    from engine.db import get_connection
    db_path = os.environ.get('TRADING_DB_PATH', 'db/trading.db')
    if not os.path.exists(db_path):
        print(f"no database at {db_path} - engine has never run")
        print(f"halted: {os.path.exists(HALT_FILE)}")
        return
    conn = get_connection(read_only=True)
    open_pos = conn.execute(
        "SELECT pair, strategy_id, entry_px, qty, stop_px, target_px, opened_ts "
        "FROM positions WHERE closed_ts IS NULL").fetchall()
    closed = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_net), 0) AS pnl "
        "FROM positions WHERE closed_ts IS NOT NULL").fetchone()
    equity_row = conn.execute(
        "SELECT ts, equity FROM equity_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()

    print(f"halted: {os.path.exists(HALT_FILE)}")
    if os.path.exists(HALT_FILE):
        try:
            with open(HALT_FILE) as f:
                print(f"  halt info: {f.read().strip()}")
        except OSError as e:
            print(f"  halt file unreadable: {e}")
    print(f"open positions: {len(open_pos)}")
    for p in open_pos:
        print(f"  {p['pair']} {p['strategy_id']} entry={p['entry_px']:.2f} "
              f"stop={p['stop_px']:.2f} target={p['target_px']:.2f}")
    print(f"closed trades: {closed['n']}, realized PnL: ${closed['pnl']:.2f}")
    if equity_row:
        age_min = (time.time() * 1000 - equity_row['ts']) / 60000
        print(f"last equity snapshot: ${equity_row['equity']:.2f} ({age_min:.0f}m ago)")
    else:
        print("no equity snapshots yet")


def cmd_halt(reason: str):
    if os.path.exists(HALT_FILE):
        # Never silently overwrite an active halt: that would invalidate the
        # ack id a human may already be holding (kill-switch tooling must be
        # boring and predictable).
        print("already halted; resume first, then halt again if needed:")
        cmd_status()
        sys.exit(1)
    halt_id = uuid.uuid4().hex[:8]
    with open(HALT_FILE, 'w') as f:
        json.dump({'halt_id': halt_id, 'ts': int(time.time() * 1000),
                   'reason': reason}, f)
    print(f"HALT written (id={halt_id}). Engine will close all positions and stop entering.")
    print(f"Resume with: python3 botctl.py resume --ack {halt_id}")


def cmd_resume(ack: str):
    if not os.path.exists(HALT_FILE):
        print("not halted")
        return
    try:
        with open(HALT_FILE) as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Hand-made or corrupt HALT file: any ack removes it, loudly. The
        # kill switch must never traceback during an emergency (N12).
        print("HALT file is not valid JSON (hand-created?); removing it on your ack.")
        info = {'halt_id': ack}
    if ack != info.get('halt_id'):
        print(f"ack mismatch: expected halt id {info.get('halt_id')!r}. Not resuming.")
        sys.exit(1)
    os.remove(HALT_FILE)
    print("HALT removed. Engine resumes entries on its next cycle.")


def main():
    args = sys.argv[1:]
    if not args or args[0] == 'status':
        cmd_status()
    elif args[0] == 'halt':
        cmd_halt(args[1] if len(args) > 1 else 'manual halt')
    elif args[0] == 'resume':
        if len(args) >= 3 and args[1] == '--ack':
            cmd_resume(args[2])
        else:
            print("usage: botctl.py resume --ack <halt_id>")
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
