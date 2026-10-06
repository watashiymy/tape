import base64
import re
from pathlib import Path

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


def test_trend_line_is_grey_and_skips_nan():
    svg = _svg(minicharts.trend_line([0.4, None, 0.5, 0.6]))
    assert palette.NEUTRAL in svg and palette.UP not in svg


def _attrs(tag: str, svg: str) -> list[dict[str, str]]:
    """svg 里每个 <tag …> 的属性字典（按出现顺序）。"""
    return [dict(re.findall(r'(\w[\w-]*)="([^"]*)"', m)) for m in re.findall(rf"<{tag}\b[^>]*>", svg)]


def _ys(points: str) -> list[float]:
    return [float(p.split(",")[1]) for p in points.split()]


def test_trend_line_uses_a_fixed_zero_to_hundred_scale():
    """占比小图不再按自身最小/最大拉满：三张卡片共用一把 0–100% 的尺子（2026-10-06 用户反馈）。"""
    H = minicharts.H
    full = _attrs("polyline", _svg(minicharts.trend_line([0.0, 1.0])))[0]["points"]
    assert _ys(full) == [H - 2, 2.0], full
    mid = _ys(_attrs("polyline", _svg(minicharts.trend_line([0.4, 0.6])))[0]["points"])
    assert 4 < min(mid) and max(mid) < H - 4, mid            # 旧实现会把 0.4–0.6 拉满画布
    clipped = _ys(_attrs("polyline", _svg(minicharts.trend_line([-0.5, 1.5])))[0]["points"])
    assert clipped == [H - 2, 2.0], clipped                   # 越界按 0/1 截断


def test_trend_line_has_a_fifty_percent_dashed_baseline_under_the_line():
    svg = _svg(minicharts.trend_line([0.4, 0.6]))
    lines = _attrs("line", svg)
    assert len(lines) == 1 and lines[0]["stroke-dasharray"] == "2 2", svg
    assert lines[0]["x1"] == "0" and lines[0]["x2"] == str(minicharts.W)
    assert float(lines[0]["y1"]) == float(lines[0]["y2"]) == minicharts.H / 2
    assert svg.index("<line ") < svg.index("<polyline"), "虚线在折线下面"


def test_trend_line_marks_the_last_valid_point_with_a_near_square_rect():
    """末端标记画在最后一个有效值上（孤立点也标）；用小矩形不用圆——画布横向随卡片拉伸，圆会变成椭圆。"""
    W, H = minicharts.W, minicharts.H
    for values, idx in (([0.4, None, 0.5, 0.6], 3), ([0.4, 0.5, None], 1), ([0.4, 0.5, None, 0.6], 3)):
        svg = _svg(minicharts.trend_line(values))
        rects = _attrs("rect", svg)
        assert len(rects) == 1 and "<circle" not in svg, svg
        r = rects[0]
        cx = float(r["x"]) + float(r["width"]) / 2
        cy = float(r["y"]) + float(r["height"]) / 2
        sx = (W - 2) / (len(values) - 1)
        assert abs(cx - (1 + idx * sx)) < 0.11, (values, cx)
        assert abs(cy - (H - 2 - values[idx] * (H - 4))) < 0.11, (values, cy)
        assert svg.rindex("<polyline") < svg.index("<rect"), "标记在最上层"


def test_trend_line_with_fewer_than_two_points_is_an_empty_frame():
    for values in ([], [0.5], [None, 0.5, None]):
        svg = _svg(minicharts.trend_line(values))
        assert "<polyline" not in svg and "<line " not in svg and "<rect" not in svg, values


def test_highs_lows_bars_point_opposite_ways():
    svg = _svg(minicharts.highs_lows([3, 5], [1, 0]))
    assert svg.count("<rect") == 3      # 2 根新高 + 1 根新低（0 不画）
    assert palette.UP in svg and palette.DOWN in svg


def test_module_has_no_raw_hex():
    assert not re.search(r"#[0-9A-Fa-f]{6}", SRC), "色值只能来自 palette"


def test_svg_has_no_script_or_foreign_object():
    for uri in (minicharts.sparkline([(0, 1.0), (1, 2.0)], 1.0), minicharts.histogram({k: 1 for k in range(-11, 12)})):
        s = _svg(uri)
        assert "<script" not in s and "foreignObject" not in s
