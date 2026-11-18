"""首页小图（spec §2）：纯函数生成 SVG，再编成 data URI 给 `<img>`。

为什么不是 plotly：每 30 秒要重画 6 幅分时图和 1 幅分布图，它们都不需要交互。
为什么不是内联 `<svg>`：Streamlit 1.61 的 st.html 用 DOMPurify 只开了 html 配置，
`<svg>` 连同子节点整段被删、且不报错（评审读前端源码证实）。`img` 在白名单里。
`img` 里的 SVG 拿不到页面 CSS 变量与网络字体，所以色值写死 palette 常量、文字不进 SVG。
"""
from __future__ import annotations

import base64
import math
from collections.abc import Sequence

from quant.report import palette

W, H = 120, 32          # 分时小图
HW, HH = 300, 60        # 分布柱状图
BW, BH = 300, 6         # 比例条
DONUT = 120             # 环形图的画布（正方形）
DONUT_R = 44            # 圆环中线的半径
DONUT_W = 18            # 环宽
DONUT_GAP = 2.0         # 相邻两块之间沿圆周留的缝：不描边，靠缝把两块分开


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


def donut(slices: Sequence[tuple[float, str]]) -> str:
    """环形图：`slices` 是 [(份额, 颜色)]，从十二点方向起顺时针依次排，份额不必先归一。

    每一块是同一个圆上的一段描边（stroke-dasharray 切出来的弧），块与块之间留一道缝而不是描边
    ——描边会吃掉小块的面积。份额不是正数的块不画；只剩一块时画成不带缝的整圈
    （只有一只持仓时不该在十二点方向留个豁口）。颜色由调用方给，必须取自 palette。
    """
    shares = [(max(float(share), 0.0), color) for share, color in slices]
    total = sum(share for share, _ in shares)
    visible = [(share, color) for share, color in shares if share > 0]
    if total <= 0 or not visible:
        return to_data_uri(_square(""))
    circumference = 2 * math.pi * DONUT_R
    center = DONUT / 2
    gap = DONUT_GAP if len(visible) > 1 else 0.0
    arcs, offset = [], 0.0
    for share, color in visible:
        length = share / total * circumference
        dash = max(length - gap, 0.5)             # 再小也留半个单位，别让一块彻底消失
        arcs.append(
            f'<circle cx="{center:g}" cy="{center:g}" r="{DONUT_R}" fill="none" stroke="{color}" '
            f'stroke-width="{DONUT_W}" stroke-dasharray="{dash:.2f} {circumference - dash:.2f}" '
            f'stroke-dashoffset="{0.0 - offset:.2f}" transform="rotate(-90 {center:g} {center:g})"/>')
        offset += length
    return to_data_uri(_square("".join(arcs)))


def _square(body: str) -> str:
    """环形图的画布。不带 preserveAspectRatio="none"（上面几幅小图要随卡片拉伸，圆不能被拉扁）。"""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{DONUT}" height="{DONUT}" '
            f'viewBox="0 0 {DONUT} {DONUT}">{body}</svg>')
