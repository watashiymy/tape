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
MARK_W, MARK_H = 1.4, 3.2   # 趋势小图末端标记：画布横向随卡片拉伸 1.3–2.4 倍，这个比例拉伸后接近正方形（圆会变椭圆）


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


def _ok(v: float | None) -> bool:
    """有效值：不是 None 也不是 NaN（NaN != NaN）。"""
    return v is not None and v == v


def trend_line(values: Sequence[float | None]) -> str:
    """均线占比的近 60 日走势，**固定 0–100% 的尺子**（0 → 底、1 → 顶），不按序列自身的最小/最大缩放——
    否则站上 200 日线那张 13%–31% 的小幅波动会被拉得和 20 日线 19%–91% 一样剧烈（2026-10-06 用户反馈）。
    画布正中一条 50% 虚线（与分时小图的昨收虚线同一套语言）；单条灰线（零类别配色）；None 处断开；
    越界按 0/1 截断；NaN 与 None 一样当缺值；
    末端一个小矩形标出当前位置（画在最后一个有效值上，哪怕它是孤立点）。不足两个有效点只出空画布。
    绘制顺序：虚线 → 折线段 → 标记。"""
    pts = [(i, v) for i, v in enumerate(values) if _ok(v)]
    if len(pts) < 2:
        return to_data_uri(_frame(W, H, ""))
    pad = 2
    sx = (W - 2) / max(1, len(values) - 1)

    def px(i): return 1 + i * sx
    def py(v): return H - pad - min(1.0, max(0.0, v)) * (H - 2 * pad)
    mid = H / 2
    segs, cur = [], []
    for i, v in enumerate(values):
        if not _ok(v):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append(f"{px(i):.1f},{py(v):.1f}")
    if len(cur) > 1:
        segs.append(cur)
    li, lv = pts[-1]
    body = (f'<line x1="0" y1="{mid:.1f}" x2="{W}" y2="{mid:.1f}" stroke="{palette.NEUTRAL}" '
            f'stroke-width="0.8" stroke-dasharray="2 2"/>'
            + "".join(f'<polyline points="{" ".join(s)}" fill="none" stroke="{palette.NEUTRAL}" '
                      f'stroke-width="1.2"/>' for s in segs)
            + f'<rect x="{px(li) - MARK_W / 2:.1f}" y="{py(lv) - MARK_H / 2:.1f}" width="{MARK_W}" '
              f'height="{MARK_H}" fill="{palette.NEUTRAL}"/>')
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
