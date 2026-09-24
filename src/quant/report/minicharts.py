"""首页小图（spec §2）：纯函数生成 SVG，再编成 data URI 给 `<img>`。

为什么不是 plotly：每 30 秒要重画 6 幅分时图和 1 幅分布图，它们都不需要交互。
为什么不是内联 `<svg>`：Streamlit 1.61 的 st.html 用 DOMPurify 只开了 html 配置，
`<svg>` 连同子节点整段被删、且不报错（评审读前端源码证实）。`img` 在白名单里。
`img` 里的 SVG 拿不到页面 CSS 变量与网络字体，所以色值写死 palette 常量、文字不进 SVG。
"""
from __future__ import annotations

import base64
from collections.abc import Sequence

from quant.report import palette

W, H = 120, 32          # 分时/趋势小图
HW, HH = 300, 60        # 分布柱状图
BW, BH = 300, 6         # 比例条


def to_data_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _frame(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" preserveAspectRatio="none">{body}</svg>')


def _scale(values: Sequence[float], h: int, pad: int = 2) -> tuple[float, float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    return lo, (h - 2 * pad) / (hi - lo)


def sparkline(points: Sequence[tuple[float, float]], prev_close: float) -> str:
    """当日分时：x 是相对位置（任意单调数），虚线是昨收，颜色按最后一点相对昨收。"""
    if len(points) < 2:
        return to_data_uri(_frame(W, H, ""))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points] + [prev_close]
    lo, k = _scale(ys, H)
    x0, x1 = min(xs), max(xs)
    sx = (W - 2) / (x1 - x0) if x1 > x0 else 0

    def px(x): return 1 + (x - x0) * sx
    def py(y): return H - 2 - (y - lo) * k
    color = palette.UP if points[-1][1] >= prev_close else palette.DOWN
    base_y = py(prev_close)
    line = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in points)
    body = (f'<line x1="0" y1="{base_y:.1f}" x2="{W}" y2="{base_y:.1f}" stroke="{palette.NEUTRAL}" '
            f'stroke-width="0.8" stroke-dasharray="2 2"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.3"/>')
    return to_data_uri(_frame(W, H, body))


def histogram(buckets: dict[int, int]) -> str:
    """23 档涨跌分布：负档绿、0 档灰、正档红（红涨绿跌是全局铁律）。"""
    keys = sorted(buckets)
    top = max(buckets.values()) or 1
    bw = HW / len(keys)
    rects = []
    for i, k in enumerate(keys):
        h = max(1.0, buckets[k] / top * (HH - 4))
        color = palette.DOWN if k < 0 else palette.UP if k > 0 else palette.NEUTRAL
        rects.append(f'<rect x="{i * bw + 1:.1f}" y="{HH - 2 - h:.1f}" width="{bw - 2:.1f}" '
                     f'height="{h:.1f}" fill="{color}"/>')
    return to_data_uri(_frame(HW, HH, "".join(rects)))


def ratio_bar(up: int, flat: int, down: int) -> str:
    total = max(1, up + flat + down)
    x = 0.0
    parts = []
    for n, color in ((up, palette.UP), (flat, palette.NEUTRAL), (down, palette.DOWN)):
        w = BW * n / total
        parts.append(f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="{BH}" fill="{color}"/>')
        x += w
    return to_data_uri(_frame(BW, BH, "".join(parts)))


def trend_line(values: Sequence[float | None]) -> str:
    """单条灰线（零类别配色）：均线占比的近 60 日走势。None 处断开。"""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return to_data_uri(_frame(W, H, ""))
    lo, k = _scale([v for _, v in pts], H)
    sx = (W - 2) / max(1, len(values) - 1)
    segs, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append(f"{1 + i * sx:.1f},{H - 2 - (v - lo) * k:.1f}")
    if len(cur) > 1:
        segs.append(cur)
    body = "".join(f'<polyline points="{" ".join(s)}" fill="none" stroke="{palette.NEUTRAL}" '
                   f'stroke-width="1.2"/>' for s in segs)
    return to_data_uri(_frame(W, H, body))


def highs_lows(highs: Sequence[int], lows: Sequence[int]) -> str:
    """新高向上红柱、新低向下绿柱，共用零轴。这里的红绿本来就是涨跌语义，不算类别配色。"""
    n = max(len(highs), len(lows), 1)
    top = max([*highs, *lows, 1])
    bw = (W - 2) / n
    mid = H / 2
    rects = []
    for i in range(n):
        h_up = (highs[i] if i < len(highs) else 0) / top * (mid - 1)
        h_dn = (lows[i] if i < len(lows) else 0) / top * (mid - 1)
        x = 1 + i * bw
        if h_up > 0:
            rects.append(f'<rect x="{x:.1f}" y="{mid - h_up:.1f}" width="{max(0.5, bw - 1):.1f}" '
                         f'height="{h_up:.1f}" fill="{palette.UP}"/>')
        if h_dn > 0:
            rects.append(f'<rect x="{x:.1f}" y="{mid:.1f}" width="{max(0.5, bw - 1):.1f}" '
                         f'height="{h_dn:.1f}" fill="{palette.DOWN}"/>')
    return to_data_uri(_frame(W, H, "".join(rects)))
