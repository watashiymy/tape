import base64
import math
import re
from pathlib import Path

import pytest

from quant.report import minicharts, palette

SRC = (Path(__file__).resolve().parent.parent / "src/quant/report/minicharts.py").read_text("utf-8")


def _svg(uri: str) -> str:
    assert uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")


def test_sparkline_uses_up_color_above_prev_close_and_a_dashed_baseline():
    svg = _svg(minicharts.sparkline([(0, 10.0), (1, 10.5), (2, 10.2)], prev_close=10.0))
    assert svg.startswith("<svg") and palette.UP in svg and "stroke-dasharray" in svg
    svg2 = _svg(minicharts.sparkline([(0, 10.0), (1, 9.5)], prev_close=10.0))
    assert palette.DOWN in svg2 and palette.UP not in svg2


def test_sparkline_with_one_point_or_none_renders_an_empty_frame():
    assert "<polyline" not in _svg(minicharts.sparkline([], prev_close=1.0))
    assert "<polyline" not in _svg(minicharts.sparkline([(0, 1.0)], prev_close=1.0))


def test_histogram_colors_negative_buckets_green_positive_red_zero_neutral():
    buckets = {k: (k + 12) * 3 for k in range(-11, 12)}
    svg = _svg(minicharts.histogram(buckets))
    assert svg.count("<rect") == 23
    assert palette.UP in svg and palette.DOWN in svg and palette.NEUTRAL in svg


def test_ratio_bar_widths_are_proportional():
    svg = _svg(minicharts.ratio_bar(up=1, flat=1, down=2))
    widths = [float(w) for w in re.findall(r'width="([\d.]+)"', svg)[1:]]   # 第一个是画布
    assert widths[0] == widths[1] and abs(widths[2] - 2 * widths[0]) < 1e-6


def test_module_has_no_raw_hex():
    assert not re.search(r"#[0-9A-Fa-f]{6}", SRC), "色值只能来自 palette"


def test_svg_has_no_script_or_foreign_object():
    for uri in (minicharts.sparkline([(0, 1.0), (1, 2.0)], 1.0), minicharts.histogram({k: 1 for k in range(-11, 12)})):
        s = _svg(uri)
        assert "<script" not in s and "foreignObject" not in s


# ---------------------------------------------------------------- 环形图（2026-11-18，持仓占比）

def _arcs(svg: str) -> list[dict]:
    out = []
    for tag in re.findall(r"<circle [^>]*/>", svg):
        dash, rest = re.search(r'stroke-dasharray="([\d.]+) ([\d.]+)"', tag).groups()
        out.append({"color": re.search(r'stroke="([^"]+)"', tag).group(1), "dash": float(dash), "rest": float(rest),
                    "offset": float(re.search(r'stroke-dashoffset="(-?[\d.]+)"', tag).group(1))})
    return out


_C = 2 * math.pi * minicharts.DONUT_R


def test_donut_draws_one_arc_per_slice_in_the_order_given():
    """从十二点方向起顺时针：每一块是同一个圆上的一段弧，起点接着上一块的终点。"""
    a, b, c = palette.PRIMARY_RAMP[:3]
    svg = _svg(minicharts.donut([(50, a), (30, b), (20, c)]))
    arcs = _arcs(svg)
    assert [x["color"] for x in arcs] == [a, b, c]
    assert [x["offset"] for x in arcs] == pytest.approx([0, -0.5 * _C, -0.8 * _C], abs=0.02)
    assert svg.count('transform="rotate(-90 60 60)"') == 3, "不转这九十度，第一块会从三点钟方向开始"
    assert 'preserveAspectRatio' not in svg, "圆不能跟着卡片被拉扁"


def test_donut_arc_lengths_are_the_shares_minus_a_gap():
    """弧长按份额分；块与块之间留一道缝（不描边——描边吃小块的面积）。份额不必先归一。"""
    arcs = _arcs(_svg(minicharts.donut([(2, palette.PRIMARY), (1, palette.NEUTRAL), (1, palette.TEXT)])))
    assert [x["dash"] for x in arcs] == pytest.approx(
        [_C / 2 - minicharts.DONUT_GAP, _C / 4 - minicharts.DONUT_GAP, _C / 4 - minicharts.DONUT_GAP], abs=0.02)
    for x in arcs:
        assert x["dash"] + x["rest"] == pytest.approx(_C, abs=0.02)


def test_a_single_slice_is_a_full_ring_without_a_notch():
    """只有一只持仓：整圈，不在十二点方向留个豁口。"""
    (arc,) = _arcs(_svg(minicharts.donut([(7.5, palette.PRIMARY)])))
    assert arc["dash"] == pytest.approx(_C, abs=0.02) and arc["offset"] == 0


def test_donut_skips_empty_slices_and_keeps_tiny_ones_visible():
    arcs = _arcs(_svg(minicharts.donut([(99.9, palette.PRIMARY), (0, palette.NEUTRAL), (-3, palette.UP),
                                        (0.1, palette.TEXT)])))
    assert [x["color"] for x in arcs] == [palette.PRIMARY, palette.TEXT], "零和负的份额不画"
    assert arcs[1]["dash"] >= 0.5, "再小的一块也留一点，不让它彻底消失"


def test_donut_of_nothing_is_an_empty_canvas():
    for slices in ([], [(0, palette.PRIMARY)], [(-1, palette.PRIMARY)]):
        svg = _svg(minicharts.donut(slices))
        assert svg.startswith("<svg") and "<circle" not in svg


def test_donut_svg_is_inert():
    s = _svg(minicharts.donut([(1, palette.PRIMARY), (1, palette.NEUTRAL)]))
    assert "<script" not in s and "foreignObject" not in s and "<text" not in s
