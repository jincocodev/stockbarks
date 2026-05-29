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
ENV_PATH = Path.home() / ".hermes/secrets/sinotrade.env"

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

def lookup_name(code):
    """用 Shioaji 查股票中文簡稱。失敗回 code。"""
    try:
        env = {}
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        import shioaji as sj
        api = sj.Shioaji(simulation=False)
        api.login(api_key=env["SINOPAC_API_KEY"], secret_key=env["SINOPAC_SECRET_KEY"])
        try:
            c = api.Contracts.Stocks.get(code)
            if c is None:
                return None
            return c.name
        finally:
            api.logout()
    except Exception as e:
        print(f"lookup failed: {e}", file=sys.stderr)
        return code

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
    name = lookup_name(code)
    if name is None:
        notify(f"找不到代號 {code}")
        return
    s.setdefault("watchlist", []).append(code)
    s.setdefault("names", {})[code] = name
    if not s.get("current"):
        s["current"] = code
    save_state(s)
    notify(f"已新增 {code} {name}")

def cmd_remove(code):
    s = load_state()
    wl = s.get("watchlist", [])
    if code in wl:
        wl.remove(code)
        s["watchlist"] = wl
        s.get("names", {}).pop(code, None)
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
