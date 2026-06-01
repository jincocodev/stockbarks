#!/usr/bin/env python3
"""StockWatch helper：處理 SwiftBar dropdown 按鈕事件。

usage:
  helper.py switch <code>
  helper.py add               (跳 AppleScript 輸入框)
  helper.py remove <code>
"""
import json
import os
import sys
import subprocess
from pathlib import Path

BASE = Path.home() / ".hermes/swiftbar"
STATE_PATH = BASE / "stockwatch_state.json"

def load_state():
    return json.loads(STATE_PATH.read_text())

def save_state(s):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE_PATH)

def notify(msg, title="StockWatch"):
    subprocess.run([
        "osascript", "-e",
        f'display notification "{msg}" with title "{title}"'
    ], check=False)

def ask_dialog(prompt):
    """彈 AppleScript 輸入框，回傳輸入字串或 None。"""
    script = f'''
    try
      set resp to display dialog "{prompt}" default answer "" buttons {{"取消", "新增"}} default button "新增" with title "StockWatch"
      if button returned of resp is "新增" then
        return text returned of resp
      end if
    on error
      return ""
    end try
    return ""
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    out = r.stdout.strip()
    return out or None

def cmd_switch(code):
    s = load_state()
    if code in s.get("watchlist", []):
        s["current"] = code
        save_state(s)

def cmd_add():
    code = ask_dialog("輸入股票代號 (例: 2330, 6584)：")
    if not code:
        return
    code = code.strip().upper()
    s = load_state()
    if code in s.get("watchlist", []):
        notify(f"{code} 已在清單中")
        return
    # 不在這裡登入 Shioaji——daemon 已用同一組 key 登入，再登一次會把它的
    # 行情 session 踢掉導致報價凍結。先用代號占位，daemon 會自動回填中文名
    # （查不到的代號 daemon 會略過、log 警告，清單上顯示「—」）。
    s.setdefault("watchlist", []).append(code)
    s.setdefault("names", {})[code] = code
    if not s.get("current"):
        s["current"] = code
    save_state(s)
    notify(f"已新增 {code}（名稱載入中…）")

def cmd_remove(code):
    s = load_state()
    wl = s.get("watchlist", [])
    if code in wl:
        wl.remove(code)
        s["watchlist"] = wl
        s.setdefault("names", {}).pop(code, None)
        if s.get("current") == code:
            s["current"] = wl[0] if wl else None
        save_state(s)
        notify(f"已移除 {code}")

def main():
    if len(sys.argv) < 2:
        print("usage: helper.py {switch|add|remove} [code]")
        sys.exit(1)
    op = sys.argv[1]
    if op == "switch" and len(sys.argv) >= 3:
        cmd_switch(sys.argv[2])
    elif op == "add":
        cmd_add()
    elif op == "remove" and len(sys.argv) >= 3:
        cmd_remove(sys.argv[2])
    else:
        print("unknown op")
        sys.exit(1)

if __name__ == "__main__":
    main()
