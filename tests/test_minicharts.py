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
