#!/usr/bin/env python3
"""StockWatch 背景 daemon：每 5s 抓 Shioaji snapshot，寫 state 給 SwiftBar plugin。

state 結構：
~/.hermes/swiftbar/stockwatch_state.json   (watchlist + current 由 plugin/CLI 更新)
~/.hermes/swiftbar/stockwatch_data.json    (即時報價，daemon 寫，plugin 讀)
~/.hermes/swiftbar/stockwatch_ticks.json   (當日分時 list，每檔保留最近 N 點)

Refresh 動態：盤中 9:00-13:30 = 5s；盤前 30s；盤後 60s；夜間/週末 600s。
"""
import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

LOG = logging.getLogger("stockwatch")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(Path.home() / ".hermes/swiftbar/daemon.log"),
              logging.StreamHandler()],
)

BASE = Path.home() / ".hermes/swiftbar"
STATE_PATH = BASE / "stockwatch_state.json"
DATA_PATH = BASE / "stockwatch_data.json"
TICKS_PATH = BASE / "stockwatch_ticks.json"
ENV_PATH = Path.home() / ".hermes/secrets/sinotrade.env"

MAX_TICKS = 300  # 9:00-13:30 共 270 分鐘，留 300 點容納整天 1 分 K + 即時 tick

def load_env():
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def now_dt():
    return datetime.now()

def is_market_hours(d=None):
    d = d or now_dt()
    if d.weekday() >= 5:
        return False
    t = d.time()
    return dtime(9, 0) <= t <= dtime(13, 30)

def is_pre_market(d=None):
    d = d or now_dt()
    if d.weekday() >= 5:
        return False
    t = d.time()
    return dtime(8, 30) <= t < dtime(9, 0)

def is_post_close_window(d=None):
    # 13:30 - 14:30 之間，盤後資料還有變動（零股、定盤）
    d = d or now_dt()
    if d.weekday() >= 5:
        return False
    t = d.time()
    return dtime(13, 30) < t <= dtime(14, 30)

def current_interval():
    if is_market_hours():
        return 5
    if is_pre_market():
        return 30
    if is_post_close_window():
        return 60
    return 600  # 夜間

def load_state():
    return json.loads(STATE_PATH.read_text())

def load_data():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text())
        except Exception:
            pass
    return {}

def load_ticks():
    if TICKS_PATH.exists():
        try:
            return json.loads(TICKS_PATH.read_text())
        except Exception:
            pass
    return {}

def save_atomic(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    tmp.replace(path)

def main():
    LOG.info("daemon starting")
    env = load_env()

    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    contracts_cache = {}

    def do_login():
        api.login(api_key=env["SINOPAC_API_KEY"], secret_key=env["SINOPAC_SECRET_KEY"])
        LOG.info("shioaji logged in")

    def relogin(reason):
        # session 壞掉或跨日時重建連線。重登入會重新載入 contracts，
        # 順帶刷新昨收 / 漲跌停（跨日後這些值才不會是舊的）。
        LOG.warning("re-login (%s)", reason)
        try:
            api.logout()
        except Exception:
            pass
        contracts_cache.clear()
        do_login()

    do_login()

    def get_contract(code):
        if code in contracts_cache:
            return contracts_cache[code]
        c = api.Contracts.Stocks.get(code)
        if c is None:
            # 嘗試指數/期貨等先不支援
            return None
        contracts_cache[code] = c
        return c

    last_state_mtime = 0
    state = load_state()
    today_key = now_dt().strftime("%Y-%m-%d")
    ticks = load_ticks()
    # 隔日清掉舊 tick
    if ticks.get("_date") != today_key:
        ticks = {"_date": today_key}

    def backfill_today(code, contract):
        """用 1 分 K 回補今日 9:00 ~ 現在的分時資料。"""
        try:
            import pandas as pd
            today = now_dt().strftime("%Y-%m-%d")
            kb = api.kbars(contract, start=today, end=today)
            df = pd.DataFrame({**kb})
            if df.empty:
                return 0
            df.ts = pd.to_datetime(df.ts)
            arr = ticks.setdefault(code, [])
            existing_times = {t["t"] for t in arr}
            added = 0
            cum_v = 0
            for _, row in df.iterrows():
                t = row["ts"].strftime("%H:%M:%S")
                cum_v += int(row["Volume"])
                if t in existing_times:
                    continue
                arr.append({"t": t, "p": float(row["Close"]), "v": cum_v})
                added += 1
            arr.sort(key=lambda x: x["t"])
            if len(arr) > MAX_TICKS:
                del arr[:-MAX_TICKS]
            return added
        except Exception as e:
            LOG.error("backfill %s failed: %s", code, e)
            return 0

    # 啟動時回補當日已過去的分時
    initial_wl = state.get("watchlist", [])
    for code in initial_wl:
        c = get_contract(code)
        if c is not None:
            n = backfill_today(code, c)
            LOG.info("backfilled %s: %d bars", code, n)
    if initial_wl:
        save_atomic(TICKS_PATH, ticks)
    backfilled_codes = set(initial_wl)
    snap_fail = 0           # 連續 snapshot 失敗次數，達門檻就重登入
    warned_unknown = set()  # 查不到的代號只警告一次

    try:
        while True:
            # 重讀 state（user 可能改 watchlist）
            try:
                m = STATE_PATH.stat().st_mtime
                if m != last_state_mtime:
                    state = load_state()
                    last_state_mtime = m
                    LOG.info("state reloaded: watchlist=%s current=%s",
                             state.get("watchlist"), state.get("current"))
            except Exception as e:
                LOG.error("load state failed: %s", e)
                time.sleep(5)
                continue

            # 跨日清 tick
            tk = now_dt().strftime("%Y-%m-%d")
            if ticks.get("_date") != tk:
                ticks = {"_date": tk}
                today_key = tk
                relogin("new trading day")  # 刷新昨收 / 漲跌停
                backfilled_codes.clear()    # 新的一天重新回補分時

            watchlist = state.get("watchlist", [])
            if not watchlist:
                time.sleep(5)
                continue

            valid_contracts = []
            names_changed = []  # daemon 已登入，順手把缺的中文名補進 state（helper 不必再登入）
            for code in watchlist:
                c = get_contract(code)
                if c is None:
                    if code not in warned_unknown:
                        LOG.warning("contract not found, skipping: %s", code)
                        warned_unknown.add(code)
                    continue
                valid_contracts.append((code, c))
                cn = getattr(c, "name", None)
                if cn and state.get("names", {}).get(code, code) == code:
                    names_changed.append((code, cn))
                # 新加入 watchlist 的股票，回補今日分時
                if code not in backfilled_codes:
                    n = backfill_today(code, c)
                    LOG.info("backfilled (new) %s: %d bars", code, n)
                    backfilled_codes.add(code)

            if names_changed:
                # 重讀最新 state 再寫，降低和 helper 並寫時互蓋的機會
                try:
                    fresh = load_state()
                except Exception:
                    fresh = state
                nm = fresh.setdefault("names", {})
                for code, cn in names_changed:
                    nm[code] = cn
                save_atomic(STATE_PATH, fresh)
                state = fresh
                try:
                    last_state_mtime = STATE_PATH.stat().st_mtime  # 避免下一圈又 reload
                except Exception:
                    pass
                LOG.info("filled names: %s", names_changed)

            if not valid_contracts:
                time.sleep(5)
                continue

            try:
                snaps = api.snapshots([c for _, c in valid_contracts])
                snap_fail = 0
            except Exception as e:
                snap_fail += 1
                LOG.error("snapshots failed (%d): %s", snap_fail, e)
                # session 斷了不會自己好，連續失敗就重登入把它救回來
                if snap_fail >= 3:
                    relogin("snapshots repeatedly failing")
                    snap_fail = 0
                time.sleep(5)
                continue

            data = {"updated_at": now_dt().strftime("%H:%M:%S"), "stocks": {}}
            for (code, contract), snap in zip(valid_contracts, snaps):
                ref = contract.reference  # 昨收
                data["stocks"][code] = {
                    "name": state.get("names", {}).get(code, code),
                    "open": snap.open,
                    "high": snap.high,
                    "low": snap.low,
                    "close": snap.close,
                    "reference": ref,
                    "change_price": snap.change_price,
                    "change_rate": snap.change_rate,
                    "total_volume": snap.total_volume,
                    "buy_price": snap.buy_price,
                    "sell_price": snap.sell_price,
                    "limit_up": contract.limit_up,
                    "limit_down": contract.limit_down,
                    "ts": now_dt().strftime("%H:%M:%S"),
                }
                # 累積 tick：盤中有量才記。同分鐘覆蓋最後一筆，避免被 MAX_TICKS 截掉早盤回補資料
                if is_market_hours() and snap.total_volume > 0 and snap.close > 0:
                    arr = ticks.setdefault(code, [])
                    now_t = now_dt().strftime("%H:%M:%S")
                    cur_min = now_t[:5]  # HH:MM
                    new_tick = {"t": now_t, "p": float(snap.close), "v": int(snap.total_volume)}
                    if arr and arr[-1]["t"][:5] == cur_min:
                        arr[-1] = new_tick
                    else:
                        arr.append(new_tick)
                    if len(arr) > MAX_TICKS:
                        del arr[:-MAX_TICKS]

            save_atomic(DATA_PATH, data)
            save_atomic(TICKS_PATH, ticks)

            interval = current_interval()
            time.sleep(interval)
    finally:
        try:
            api.logout()
        except Exception:
            pass
        LOG.info("daemon stopped")

if __name__ == "__main__":
    main()
