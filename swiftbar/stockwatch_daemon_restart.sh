#!/bin/bash
# 啟動或重啟 stockwatch daemon
set -e
BASE="$HOME/.hermes/swiftbar"
PIDFILE="$BASE/daemon.pid"

# 先用 PID file 清舊的
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE")
  kill "$OLD" 2>/dev/null || true
fi

# 保險：用 pgrep 清掉所有同名 daemon process
pkill -f "stockwatch_daemon.py" 2>/dev/null || true
sleep 1

nohup /usr/bin/python3 "$BASE/stockwatch_daemon.py" >> "$BASE/daemon.log" 2>&1 &
echo $! > "$PIDFILE"

osascript -e 'display notification "Daemon 已啟動" with title "StockWatch"'
