#!/usr/bin/env python3
"""Shadow loop wrapper that tracks account blowups and auto-restarts.

When equity hits $0 (or below), logs the blowup with full stats, resets equity
to $1,000, and restarts the loop. All previous trade history is preserved in
db/trading.db. The blowup count is tracked in a separate table.

Usage:
    python3 scripts/shadow_runner.py
"""
import sqlite3
import time
import subprocess
import sys
import os
import signal
import json
from datetime import datetime, timezone

REPO = os.path.expanduser('~/aym/projects/05-trading-bot')
DB = os.path.join(REPO, 'db/trading.db')
VAULT = os.path.expanduser('~/aym/vault/Trading')
STARTING_EQUITY = 1000.0
RESTART_DELAY = 5  # seconds between blowup and restart

def ensure_blowup_table():
    conn = sqlite3.connect(DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS shadow_blowups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            blowup_number INTEGER NOT NULL,
            starting_equity REAL NOT NULL,
            ending_equity REAL NOT NULL,
            total_trades INTEGER NOT NULL,
            total_pnl REAL NOT NULL,
            duration_seconds INTEGER NOT NULL,
            per_strategy_json TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_blowup_count():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM shadow_blowups')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_current_equity():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('SELECT equity FROM equity_snapshots ORDER BY ts DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    return row[0] if row else STARTING_EQUITY

def get_trade_stats():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('SELECT COUNT(*), COALESCE(SUM(pnl_net), 0) FROM positions WHERE closed_ts IS NOT NULL')
    total, pnl = c.fetchone()
    
    # Per-strategy breakdown
    c.execute('''SELECT strategy_id, COUNT(*), COALESCE(SUM(pnl_net), 0),
                        SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END)
                 FROM positions WHERE closed_ts IS NOT NULL
                 GROUP BY strategy_id ORDER BY SUM(pnl_net) DESC''')
    import json
    per_strategy = {}
    for row in c.fetchall():
        wr = row[3]/row[1]*100 if row[1] > 0 else 0
        per_strategy[row[0]] = {
            'trades': row[1], 'pnl': round(row[2], 2), 'wins': row[3], 'win_rate': round(wr, 1)
        }
    conn.close()
    return total, pnl, json.dumps(per_strategy)

def spawn_blowup_report(blowup_id):
    """Kick off the Opus root-cause note for a blowup row, DETACHED.

    The old version of this function contained the root cause as a hardcoded
    f-string: it asserted "the primary cause was the fair_value_arb family"
    and "the spread is the enemy" no matter what the account had actually
    done. That is a judgement nobody made, and it would have kept asserting
    itself after the strategies it names were killed.

    So the split is now: the RAW DATA write stays here, in Python, immediate
    and deterministic (log_blowup wrote the row before we were called). The
    REASONING is composed by Opus in `agents/vault_writer.py`, from that row.

    It is spawned detached rather than called inline because the runner must
    restart the shadow loop within seconds and a reasoning turn takes minutes.
    Detached also means the note survives this runner being killed, and any
    past blowup can be re-analysed later with:

        env -u PYTHONPATH python3 -m agents.vault_writer blowup --id N

    Returns the child PID, or None when it could not be spawned. Never
    raises: a blowup is exactly when you least want a stack trace.
    """
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)  # Convention 14
    log_path = os.path.join(REPO, 'logs', 'blowup_report_%03d.log' % blowup_id)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_handle = open(log_path, 'ab')
    except OSError as exc:
        print('WARNING: could not open %s: %s' % (log_path, exc))
        log_handle = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [sys.executable, '-m', 'agents.vault_writer', 'blowup',
             '--id', str(blowup_id)],
            cwd=REPO, env=env, stdin=subprocess.DEVNULL,
            stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as exc:
        print('WARNING: could not spawn the blowup report job: %s' % exc)
        return None
    print('Blowup report job spawned (PID %d), log: %s'
          % (proc.pid, log_path))
    return proc.pid


def log_blowup(blowup_num, starting_eq, ending_eq, trades, pnl, duration, per_strategy):
    conn = sqlite3.connect(DB)
    cur = conn.execute('''
        INSERT INTO shadow_blowups (ts, blowup_number, starting_equity, ending_equity,
                                     total_trades, total_pnl, duration_seconds, per_strategy_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (int(time.time()*1000), blowup_num, starting_eq, ending_eq, trades, pnl, duration, per_strategy))
    blowup_id = cur.lastrowid
    conn.commit()
    conn.close()
    
    # The raw row is now committed. Hand the REASONING to Opus, detached, so
    # the shadow loop restarts on schedule instead of waiting on a model turn.
    try:
        spawn_blowup_report(blowup_id)
    except Exception as e:
        print(f"WARNING: could not spawn the blowup report job: {e}")

def reset_equity():
    """Write a new equity snapshot at $1000 to reset the starting point."""
    conn = sqlite3.connect(DB)
    ts = int(time.time() * 1000)
    conn.execute('''
        INSERT OR REPLACE INTO equity_snapshots (ts, equity, cash, open_risk, mode)
        VALUES (?, ?, ?, 0, 'paper')
    ''', (ts, STARTING_EQUITY, STARTING_EQUITY))
    conn.commit()
    conn.close()

def run_shadow_loop():
    """Start the shadow loop as a subprocess. Returns the Popen object."""
    proc = subprocess.Popen(
        ['./run_polymarket_shadow.sh'],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid
    )
    return proc

def main():
    ensure_blowup_table()
    blowup_count = get_blowup_count()
    print(f"=== SHADOW RUNNER ===")
    print(f"Previous blowups: {blowup_count}")
    print(f"Starting equity: ${STARTING_EQUITY}")
    print(f"Daily loss limit: DISABLED (shadow mode)")
    print(f"Auto-restart on blowup: YES")
    print()
    
    proc = run_shadow_loop()
    start_time = time.time()
    current_starting = STARTING_EQUITY
    
    print(f"Shadow loop started (PID {proc.pid})")
    
    while True:
        time.sleep(10)  # check every 10 seconds
        
        # Check if process died
        if proc.poll() is not None:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Shadow loop process died (exit {proc.returncode}). Restarting in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
            proc = run_shadow_loop()
            start_time = time.time()
            print(f"Shadow loop restarted (PID {proc.pid})")
            continue
        
        # Check equity
        equity = get_current_equity()
        
        if equity <= 0:
            duration = int(time.time() - start_time)
            trades, pnl, per_strategy = get_trade_stats()
            blowup_count += 1
            
            print(f"\n{'='*60}")
            print(f"BLOWUP #{blowup_count}")
            print(f"  Starting equity: ${current_starting:.2f}")
            print(f"  Ending equity: ${equity:.2f}")
            print(f"  Total trades: {trades}")
            print(f"  Total PnL: ${pnl:.2f}")
            print(f"  Duration: {duration}s ({duration/3600:.1f}h)")
            print(f"  Previous blowups: {blowup_count - 1}")
            print(f"{'='*60}\n")
            
            log_blowup(blowup_count, current_starting, equity, trades, pnl, duration, per_strategy)
            
            # Kill the shadow loop
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait()
            
            # Reset and restart
            reset_equity()
            current_starting = STARTING_EQUITY
            print(f"Equity reset to ${STARTING_EQUITY}. Restarting in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
            proc = run_shadow_loop()
            start_time = time.time()
            print(f"Shadow loop restarted (PID {proc.pid}). Blowup count: {blowup_count}")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nShadow runner stopped by user.")
        sys.exit(0)
