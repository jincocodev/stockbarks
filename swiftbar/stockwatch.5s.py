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

def png_sparkline(prices, prev_close, width=100, height=20, is_dark=False,
                  times=None, volumes=None):
    """畫真折線 PNG（三竹/籌碼K線分時圖風格，抗鋸齒版）。
    - 高倍 (SCALE) 繪製後 LANCZOS 縮回 -> 線條平滑無鋸齒
    - 價格折線分段染色（紅=高於昨收 / 綠=低於），漸層面積填充
    - 成交量：壓矮、半透明，鋪在價格圖「背景」（不另外吃高度）
    - 白色 VWAP 均價線、淡灰平盤虛線、現價最後一點帶光暈
    - x 軸固定 09:00-13:30
    """
    try:
        from PIL import Image, ImageDraw, ImageChops
    except ImportError:
        return None
    if not prices or len(prices) < 2:
        return None

    SCALE = 4          # 超取樣倍率，最後縮回 -> 抗鋸齒
    OUT_SCALE = 2      # 最終 PNG 2x（retina）
    VOL_CAP = 0.45     # 量柱最高佔繪圖區比例（壓矮當背景）
    VOL_ALPHA = 38     # 量柱透明度（淡）

    W, H = width * SCALE, height * SCALE
    PAD_X = 1 * SCALE
    PAD_Y = 2 * SCALE
    AREA_TOP = PAD_Y
    AREA_BOT = H - PAD_Y
    AREA_H = AREA_BOT - AREA_TOP

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    series = list(prices) + [prev_close]
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
        return AREA_TOP + int((hi - p) / rng * AREA_H)

    cur = prices[-1]
    RED_LINE = (255, 75, 75)
    GRN_LINE = (60, 205, 95)
    line_rgb = RED_LINE if cur >= prev_close else GRN_LINE
    baseline_y = y_of_price(prev_close)

    have_tv = bool(times and volumes and len(times) == len(prices)
                   and len(volumes) == len(prices))

    # 按分鐘聚合成交量 delta（第一筆設 0 避免累積起始值爆衝）
    minute_vol = {}
    minute_pv = {}
    if have_tv:
        deltas_v = [0] + [max(0, volumes[i] - volumes[i - 1])
                          for i in range(1, len(volumes))]
        for t, p, dv in zip(times, prices, deltas_v):
            m = t_to_min(t)
            if m < 0 or m > MARKET_TOTAL_MIN:
                continue
            mb = int(m)
            minute_vol[mb] = minute_vol.get(mb, 0) + dv
            minute_pv[mb] = minute_pv.get(mb, 0) + p * dv

    # === 1) 成交量背景柱（壓矮、半透明，畫在最底層）===
    if minute_vol:
        vals = sorted(minute_vol.values())
        idx = max(0, int(len(vals) * 0.95) - 1)
        cap_v = max(vals[idx], 1)
        bar_w = max(1, int((W - 2 * PAD_X) / MARKET_TOTAL_MIN))
        vlayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vlayer)
        for mb, dv in minute_vol.items():
            if dv <= 0:
                continue
            bar_h = int(min(1.0, dv / cap_v) * AREA_H * VOL_CAP)
            x = x_of_t(mb)
            vd.rectangle([x - bar_w // 2, AREA_BOT - bar_h,
                          x + (bar_w - bar_w // 2), AREA_BOT],
                         fill=(95, 150, 235, VOL_ALPHA))
        img.alpha_composite(vlayer)
        draw = ImageDraw.Draw(img)

    # 各點座標（時間軸對齊；無 times 則均分）
    pts = []
    if have_tv or (times and len(times) == len(prices)):
        for t, p in zip(times, prices):
            m = t_to_min(t)
            if m < 0:
                continue
            if m > MARKET_TOTAL_MIN:
                m = MARKET_TOTAL_MIN
            pts.append((x_of_t(m), y_of_price(p), p))
    else:
        n = len(prices)
        for i, p in enumerate(prices):
            x = PAD_X + int(i * (W - 2 * PAD_X) / max(n - 1, 1))
            pts.append((x, y_of_price(p), p))
    if len(pts) < 2:
        return None

    # 切成連續同色段（昨收為界），跨界插入交點
    def interp_cross(p1, p2):
        x1, _, v1 = p1
        x2, _, v2 = p2
        if v2 == v1:
            return None
        t = (prev_close - v1) / (v2 - v1)
        if t <= 0 or t >= 1:
            return None
        return (x1 + t * (x2 - x1), baseline_y, prev_close)

    def side_of(v):
        if v > prev_close:
            return 'up'
        if v < prev_close:
            return 'down'
        return None

    segments = []
    cur_seg = []
    cur_side = None
    for pt in pts:
        s = side_of(pt[2])
        if cur_side is None:
            cur_side = s if s else 'up'
            cur_seg = [pt]
            continue
        if s is None or s == cur_side:
            cur_seg.append(pt)
        else:
            cross = interp_cross(cur_seg[-1], pt)
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

    # === 2) 漸層面積填充（靠線端深、靠平盤線淡出）===
    A_MAX = 150
    for side, seg in segments:
        if len(seg) < 2:
            continue
        col = RED_LINE if side == 'up' else GRN_LINE
        ys = [y for _, y, _ in seg]
        top = min(min(ys), baseline_y)
        bot = max(max(ys), baseline_y)
        if bot <= top:
            continue
        far = max(1, abs((min(ys) if side == 'up' else max(ys)) - baseline_y))
        h = bot - top
        # 1px 寬的垂直漸層，再橫向拉寬（避免逐像素 Python 迴圈）
        col_grad = Image.new("RGBA", (1, h), (0, 0, 0, 0))
        cp = col_grad.load()
        for yy in range(h):
            a = int(max(0, min(A_MAX, A_MAX * abs(top + yy - baseline_y) / far)))
            cp[0, yy] = (col[0], col[1], col[2], a)
        grad = col_grad.resize((W, h))
        # 多邊形遮罩 × 漸層 alpha
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).polygon(
            [(seg[0][0], baseline_y)] + [(x, y) for x, y, _ in seg]
            + [(seg[-1][0], baseline_y)], fill=255)
        region = mask.crop((0, top, W, bot))
        grad.putalpha(ImageChops.multiply(grad.split()[3], region))
        img.alpha_composite(grad, (0, top))
    draw = ImageDraw.Draw(img)

    # 平盤線（淡灰虛線）
    dash = 3 * SCALE
    x = 0
    while x < W:
        draw.line([(x, baseline_y), (min(x + dash, W), baseline_y)],
                  fill=(165, 165, 165, 170), width=max(1, SCALE // 2))
        x += dash * 2

    # VWAP 均價線（白色細線，按分鐘累積）
    if minute_vol:
        cum_pv = cum_v = 0
        vwap_pts = []
        for mb in sorted(minute_vol.keys()):
            cum_pv += minute_pv[mb]
            cum_v += minute_vol[mb]
            if cum_v > 0:
                vwap_pts.append((x_of_t(mb), y_of_price(cum_pv / cum_v)))
        if len(vwap_pts) >= 2:
            draw.line(vwap_pts, fill=(255, 255, 255, 225),
                      width=max(1, SCALE // 2), joint="curve")

    # 價格折線（分段染色，粗線；抗鋸齒靠最後縮放）
    for side, seg in segments:
        if len(seg) < 2:
            continue
        color = (RED_LINE if side == 'up' else GRN_LINE) + (255,)
        draw.line([(x, y) for x, y, _ in seg], fill=color,
                  width=int(1.4 * SCALE), joint="curve")

    # === 3) 現價最後一點：光暈 + 實心圓點 ===
    lx, ly = pts[-1][0], pts[-1][1]
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for rr, aa in [(5 * SCALE, 28), (3.2 * SCALE, 52), (2 * SCALE, 85)]:
        gd.ellipse([lx - rr, ly - rr, lx + rr, ly + rr], fill=line_rgb + (aa,))
    img.alpha_composite(glow)
    draw = ImageDraw.Draw(img)
    r = int(1.3 * SCALE)
    draw.ellipse([lx - r, ly - r, lx + r, ly + r], fill=line_rgb + (255,))

    # 縮回顯示尺寸 -> 抗鋸齒
    img = img.resize((width * OUT_SCALE, height * OUT_SCALE), Image.LANCZOS)
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
