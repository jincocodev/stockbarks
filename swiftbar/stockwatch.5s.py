#!/usr/bin/env python3
# <bitbar.title>StockWatch</bitbar.title>
# <bitbar.version>v0.2</bitbar.version>
# <bitbar.author>Jincoco</bitbar.author>
# <bitbar.desc>Realtime TW stock ticker with Braille sparkline</bitbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
"""SwiftBar plugin: 讀 stockwatch daemon 寫的 state，畫 menu bar + dropdown。"""
import base64
import io
import json
import sys
from pathlib import Path

BASE = Path.home() / ".hermes/swiftbar"
STATE_PATH = BASE / "stockwatch_state.json"
DATA_PATH = BASE / "stockwatch_data.json"
TICKS_PATH = BASE / "stockwatch_ticks.json"
HELPER = BASE / "stockwatch_helper.py"
PY = "/usr/bin/python3"

UP = "#E03131"
DOWN = "#2F9E44"
FLAT = "#868E96"

# ---------------- PNG sparkline ----------------

def png_sparkline(prices, prev_close, width=120, height=22, is_dark=False,
                  times=None, volumes=None):
    """畫真折線 PNG（三竹/籌碼K線分時圖風格）。
    - 上 70%: 紅/綠折線 + 半透明面積填充 (vs 昨收) + 白色均價線 + 橘虛線=昨收
    - 下 30%: 藍色成交量柱（每分鐘 delta）
    - x 軸固定 09:00-13:30
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not prices or len(prices) < 2:
        return None

    SCALE = 2
    W, H = width * SCALE, height * SCALE
    PAD_X = 1 * SCALE
    PAD_Y = 1 * SCALE

    VOL_RATIO = 0.28
    GAP = 1 * SCALE
    PRICE_AREA_H = int((H - 2 * PAD_Y - GAP) * (1 - VOL_RATIO))
    VOL_AREA_H = (H - 2 * PAD_Y - GAP) - PRICE_AREA_H
    PRICE_TOP = PAD_Y
    PRICE_BOT = PAD_Y + PRICE_AREA_H
    VOL_BOT = H - PAD_Y

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    series = list(prices) + [prev_close]  # 包含 ref 確保虛線在畫面內
    data_lo, data_hi = min(series), max(series)
    span = max(data_hi - data_lo, prev_close * 0.005)
    pad = span * 0.10
    lo = data_lo - pad
    hi = data_hi + pad
    rng = hi - lo

    MARKET_OPEN_MIN = 9 * 60
    MARKET_TOTAL_MIN = 270

    def t_to_min(t):
        hh, mm, ss = t.split(":")
        return int(hh) * 60 + int(mm) + int(ss) / 60 - MARKET_OPEN_MIN

    def x_of_t(minute):
        return PAD_X + int(minute / MARKET_TOTAL_MIN * (W - 2 * PAD_X))

    def y_of_price(p):
        return PRICE_TOP + int((hi - p) / rng * PRICE_AREA_H)

    cur = prices[-1]
    # 分段配色：高於昨收紅、低於昨收綠
    RED_LINE = (255, 80, 80, 255)
    RED_FILL = (255, 60, 60, 60)
    GRN_LINE = (80, 220, 100, 255)
    GRN_FILL = (60, 200, 90, 60)
    # 最後一點顏色（用於圓點）
    line_color = RED_LINE if cur >= prev_close else GRN_LINE

    baseline_y = y_of_price(prev_close)

    # 計算各點座標（用時間軸對齊）
    pts = []
    if times and len(times) == len(prices):
        for t, p in zip(times, prices):
            mins = t_to_min(t)
            if mins < 0:
                continue
            if mins > MARKET_TOTAL_MIN:
                mins = MARKET_TOTAL_MIN
            pts.append((x_of_t(mins), y_of_price(p), p))
    else:
        n = len(prices)
        for i, p in enumerate(prices):
            x = PAD_X + int(i * (W - 2 * PAD_X) / max(n - 1, 1))
            pts.append((x, y_of_price(p), p))

    if len(pts) < 2:
        return None

    # === 分段填充 + 分段折線（昨收為分界） ===
    # 在每對相鄰點之間如果跨過 baseline，插入交點
    def interp_cross(p1, p2, baseline_price):
        x1, y1, v1 = p1
        x2, y2, v2 = p2
        # 線性插值 x：v1 + t*(v2-v1) = baseline -> t
        if v2 == v1:
            return None
        t = (baseline_price - v1) / (v2 - v1)
        if t <= 0 or t >= 1:
            return None
        return (x1 + t * (x2 - x1), baseline_y, baseline_price)

    # 切成連續同色段
    segments = []  # [(side, [pts...])]  side: 'up' / 'down'
    def side_of(v):
        if v > prev_close: return 'up'
        if v < prev_close: return 'down'
        return None  # 剛好踩在線上，視為延續

    cur_seg = []
    cur_side = None
    for i, pt in enumerate(pts):
        s = side_of(pt[2])
        if cur_side is None:
            cur_side = s if s else 'up'
            cur_seg = [pt]
            continue
        if s is None or s == cur_side:
            cur_seg.append(pt)
        else:
            # 跨界：插入交點，封一段、開新段
            cross = interp_cross(cur_seg[-1], pt, prev_close)
            if cross:
                cur_seg.append(cross)
                segments.append((cur_side, cur_seg))
                cur_seg = [cross, pt]
            else:
                segments.append((cur_side, cur_seg))
                cur_seg = [pt]
            cur_side = s
    if cur_seg:
        segments.append((cur_side, cur_seg))

    # 畫填充
    for side, seg in segments:
        if len(seg) < 2:
            continue
        fill = RED_FILL if side == 'up' else GRN_FILL
        poly = [(seg[0][0], baseline_y)]
        poly += [(x, y) for x, y, _ in seg]
        poly += [(seg[-1][0], baseline_y)]
        draw.polygon(poly, fill=fill)

    # 平盤線（淡灰虛線）
    baseline_color = (170, 170, 170, 180)
    dash_len = 3 * SCALE
    gap_len = 3 * SCALE
    x = 0
    while x < W:
        draw.line([(x, baseline_y), (min(x + dash_len, W), baseline_y)],
                  fill=baseline_color, width=1)
        x += dash_len + gap_len

    # === 按分鐘聚合成交量（delta），第一筆設 0 避免累積起始值爆衝 ===
    # bucket[minute_int] = (sum_dv, avg_price_weighted)
    minute_vol = {}   # minute -> dv
    minute_pv = {}    # minute -> sum(p*dv)
    if volumes and times and len(volumes) == len(prices):
        deltas_v = [0] + [max(0, volumes[i] - volumes[i - 1])
                          for i in range(1, len(volumes))]
        for t, p, dv in zip(times, prices, deltas_v):
            mins = t_to_min(t)
            if mins < 0 or mins > MARKET_TOTAL_MIN:
                continue
            mb = int(mins)
            minute_vol[mb] = minute_vol.get(mb, 0) + dv
            minute_pv[mb] = minute_pv.get(mb, 0) + p * dv

    # 均價線 VWAP（白色細線，按分鐘累積）
    if minute_vol:
        cum_pv = 0
        cum_v = 0
        vwap_pts = []
        for mb in sorted(minute_vol.keys()):
            cum_pv += minute_pv[mb]
            cum_v += minute_vol[mb]
            if cum_v > 0:
                vwap = cum_pv / cum_v
                vwap_pts.append((x_of_t(mb), y_of_price(vwap)))
        if len(vwap_pts) >= 2:
            draw.line(vwap_pts, fill=(255, 255, 255, 220), width=1, joint="curve")

    # 價格折線（分段染色）
    for side, seg in segments:
        if len(seg) < 2:
            continue
        color = RED_LINE if side == 'up' else GRN_LINE
        draw.line([(x, y) for x, y, _ in seg], fill=color, width=1 * SCALE, joint="curve")

    # 最後一點圓點
    lx, ly = pts[-1][0], pts[-1][1]
    r = 1 * SCALE + 1
    draw.ellipse([lx - r, ly - r, lx + r, ly + r], fill=line_color)

    # 成交量柱（按分鐘聚合，藍色細柱）
    if minute_vol:
        # 用 P95 當基準避開開盤集合競價超大量，超過上限的柱頂滿就好
        vals = sorted(minute_vol.values())
        if vals:
            idx = max(0, int(len(vals) * 0.95) - 1)
            cap_v = max(vals[idx], 1)
        else:
            cap_v = 1
        bar_color = (90, 170, 255, 230)
        bar_w = max(1, int((W - 2 * PAD_X) / MARKET_TOTAL_MIN))
        for mb, dv in minute_vol.items():
            if dv <= 0:
                continue
            ratio = min(1.0, dv / cap_v)
            bar_h = max(1, int(ratio * VOL_AREA_H))
            x = x_of_t(mb)
            draw.rectangle(
                [x - bar_w // 2, VOL_BOT - bar_h,
                 x + (bar_w - bar_w // 2), VOL_BOT],
                fill=bar_color,
            )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")

# ---------------- Braille fallback (沒 PIL 時用) ----------------

BIT = {
    (0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
    (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80,
}

def braille_with_baseline(prices, prev_close, width=24):
    if not prices:
        return ""
    series = list(prices) + [prev_close]
    lo, hi = min(series), max(series)
    if hi == lo:
        hi = lo + 0.01

    def to_row(p):
        r = 3 - round((p - lo) / (hi - lo) * 3)
        return max(0, min(3, r))

    base_row = to_row(prev_close)
    n = len(prices)
    cells = []
    for i in range(width):
        bits = 0
        baseline_active = (i % 2 == 0)
        for col in (0, 1):
            idx = int((i * 2 + col) * n / (width * 2))
            idx = min(n - 1, idx)
            r = to_row(prices[idx])
            bits |= BIT[(col, r)]
            if baseline_active and r != base_row:
                bits |= BIT[(col, base_row)]
        cells.append(chr(0x2800 + bits))
    return "".join(cells)

def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default

def fmt_rate(r):
    if r is None:
        return "—"
    s = "+" if r >= 0 else ""
    return f"{s}{r:.2f}%"

def arrow(cp):
    return "▲" if cp > 0 else ("▼" if cp < 0 else "—")

def color(cp):
    return UP if cp > 0 else (DOWN if cp < 0 else FLAT)

def emit(text, **attrs):
    line = text
    if attrs:
        line += " | " + " ".join(f'{k}={v}' for k, v in attrs.items())
    print(line)

def emit_sub(prefix, text, **attrs):
    """SwiftBar 子選單用 '--' 前綴。prefix 是 '--' 或 '----'..."""
    emit(f"{prefix} {text}", **attrs)

def main():
    state = load(STATE_PATH, {"watchlist": [], "current": None, "names": {}})
    data = load(DATA_PATH, {"stocks": {}})
    ticks = load(TICKS_PATH, {})

    watchlist = state.get("watchlist", [])
    current = state.get("current")
    names = state.get("names", {})
    stocks = data.get("stocks", {})

    # ---------- menu bar ----------
    if not current or current not in stocks:
        emit("StockWatch …", font="Menlo", size=12)
    else:
        s = stocks[current]
        ref = s.get("reference") or s.get("close")
        cp = s.get("change_price", 0) or 0
        cr = s.get("change_rate", 0) or 0
        close = s.get("close", 0)
        arr = ticks.get(current, [])
        # 嘗試 PNG 折線；失敗就退回 Braille
        png_b64 = None
        if arr and len(arr) >= 2:
            png_b64 = png_sparkline(
                [t["p"] for t in arr], ref,
                width=100, height=20,
                times=[t["t"] for t in arr],
                volumes=[t["v"] for t in arr],
            )
        line = f"{current} {close:g} {arrow(cp)}{fmt_rate(cr)}"
        attrs = {"color": color(cp), "font": "Menlo", "size": 12}
        if png_b64:
            attrs["image"] = png_b64
        else:
            # fallback: Braille
            if arr:
                line += " " + braille_with_baseline([t["p"] for t in arr], ref, width=24)
        emit(line, **attrs)

    print("---")

    # ---------- 當前股票詳細 ----------
    if current and current in stocks:
        s = stocks[current]
        cp = s.get("change_price", 0) or 0
        cr = s.get("change_rate", 0) or 0
        emit(f"{current}  {s.get('name', current)}", size=14)
        emit(f"  {s['close']:g}    {cp:+g}    {fmt_rate(cr)}",
             color=color(cp), font="Menlo", size=13)
        print("---")
        rows = [
            ("開盤", f"{s['open']:g}"),
            ("最高", f"{s['high']:g}"),
            ("最低", f"{s['low']:g}"),
            ("昨收", f"{s.get('reference', 0):g}"),
            ("成交量", f"{s.get('total_volume', 0):,}"),
            ("買 / 賣", f"{s.get('buy_price', 0):g}  /  {s.get('sell_price', 0):g}"),
            ("漲停 / 跌停", f"{s.get('limit_up', 0):g}  /  {s.get('limit_down', 0):g}"),
        ]
        for k, v in rows:
            emit(f"  {k:<10}{v}", font="Menlo", size=12)
        emit(f"  更新      {s.get('ts', '—')}", font="Menlo", size=11, color=FLAT)
        print("---")

    # ---------- 追蹤清單 ----------
    emit("追蹤清單", size=11, color=FLAT)
    for code in watchlist:
        s = stocks.get(code, {})
        marker = "●" if code == current else "○"
        name = names.get(code, code)
        if s:
            cp = s.get("change_price", 0) or 0
            cr = s.get("change_rate", 0) or 0
            row = f"  {marker}  {code}  {name:<8}  {s.get('close', 0):>7g}   {fmt_rate(cr)}"
            c = color(cp)
        else:
            row = f"  {marker}  {code}  {name:<8}  —"
            c = FLAT
        emit(row, font="Menlo", size=12, color=c,
             bash=PY, param1=str(HELPER), param2="switch", param3=code,
             terminal="false", refresh="true")

    print("---")

    # ---------- 管理 ----------
    emit("管理", size=11, color=FLAT)
    emit("  新增股票…",
         bash=PY, param1=str(HELPER), param2="add",
         terminal="false", refresh="true")
    emit("  移除股票")
    for code in watchlist:
        name = names.get(code, code)
        emit_sub("--", f"{code}  {name}",
                 bash=PY, param1=str(HELPER), param2="remove", param3=code,
                 terminal="false", refresh="true")

    print("---")
    emit(f"資料更新：{data.get('updated_at', '—')}", size=11, color=FLAT)
    emit("立即刷新", refresh="true")
    emit("啟動 / 重啟 Daemon",
         bash="/bin/bash", param1=str(BASE / "stockwatch_daemon_restart.sh"),
         terminal="false")
    emit("查看 Daemon Log",
         bash="/bin/bash", param1="-c",
         param2=f"open -a Console {BASE}/daemon.log",
         terminal="false")

if __name__ == "__main__":
    main()
