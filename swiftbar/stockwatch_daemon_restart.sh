#!/bin/bash
# 重啟 stockwatch daemon。
# daemon 由 launchd agent com.jincoco.stockwatch (KeepAlive) 管理，
# 所以這裡只用 launchctl 重啟它，絕不自己 nohup 另起一個——
# 否則會和 launchd 各起一個 daemon，兩個用同一組 API key 搶 Shioaji
# session，導致 snapshots 一直 SessionNotEstablished、報價凍結。
set -e
LABEL="com.jincoco.stockwatch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

# 保險：清掉任何沒被 launchd 管到的殘留 daemon（例如舊版腳本 nohup 起的）
pkill -f "stockwatch_daemon.py" 2>/dev/null || true
sleep 1

# 確保 agent 已載入（首次或 plist 更新後）
launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || true

# 原子重啟：kill 現有 instance，launchd 立刻重新拉起，全程只會有一個
launchctl kickstart -k "$DOMAIN/$LABEL"

osascript -e 'display notification "Daemon 已重啟" with title "StockWatch"'
