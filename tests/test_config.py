from datetime import date

import pytest

from quant.config import load_settings


def test_load_settings(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519", "000333"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 5000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies:
  ma_cross: {fast: 20, slow: 60}
""",
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.universe == ("600519", "000333")
    assert s.start == date(2016, 1, 1)
    assert s.capital == 5_000_000
    assert s.costs.commission_min == 5.0
    assert s.strategies["ma_cross"]["fast"] == 20


def test_stamp_rate_segments(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: {}
""",
        encoding="utf-8",
    )
    c = load_settings(cfg).costs
    assert c.stamp_rate(date(2023, 8, 27)) == 0.001
    assert c.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert c.stamp_rate(date(2016, 1, 4)) == 0.001
    assert c.stamp_rate(date(2026, 8, 14)) == 0.0005


def test_stamp_rate_three_segments_and_order_independent(tmp_path):
    """回归：印花税若再次调整，最自然的改法就是往列表里追加一段。
    "满足任一边界即返回"的写法会让带 frm 的第二段吞掉其后所有日期，
    静默返回旧税率——错误税率污染每一次回测且永不报错。"""
    body = """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
%s
  slippage: 0.001
strategies: {}
"""
    seg_a = '    - {until: "2023-08-27", rate: 0.001}'
    seg_b = '    - {from: "2023-08-28", until: "2026-12-31", rate: 0.0005}'
    seg_c = '    - {from: "2027-01-01", rate: 0.00025}'

    for name, segs in [("正序", [seg_a, seg_b, seg_c]), ("乱序", [seg_c, seg_a, seg_b])]:
        cfg = tmp_path / f"s_{name}.yaml"
        cfg.write_text(body % "\n".join(segs), encoding="utf-8")
        c = load_settings(cfg).costs
        assert c.stamp_rate(date(2016, 1, 4)) == 0.001, name
        assert c.stamp_rate(date(2024, 5, 6)) == 0.0005, name
        assert c.stamp_rate(date(2027, 6, 1)) == 0.00025, name


def test_stamp_rate_gap_raises_loudly(tmp_path):
    cfg = tmp_path / "gap.yaml"
    cfg.write_text("""
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2020-12-31", rate: 0.001}
    - {from: "2022-01-01", rate: 0.0005}
  slippage: 0.001
strategies: {}
""", encoding="utf-8")
    c = load_settings(cfg).costs
    with pytest.raises(ValueError, match="没有覆盖"):
        c.stamp_rate(date(2021, 6, 1))  # 落在缺口里必须报错，不能悄悄返回某个税率


def test_real_config_file():
    """两个 tmp_path 测试都自带 YAML，谁也管不到真正被脚本加载的那个文件。
    这里钉住 config/settings.yaml 本身，防止手改配置时打错字。"""
    s = load_settings("config/settings.yaml")
    assert len(s.universe) == 10
    # 全部 6 位数字：YAML 1.1 会把不加引号的 000333 当八进制解析成 219，
    # str() 之后变成 "219" —— 一个静默错误的股票代码。
    assert all(len(c) == 6 and c.isdigit() for c in s.universe)
    assert s.capital == 5_000_000
    assert s.costs.commission_rate == 0.00025
    assert s.costs.commission_min == 5.0
    assert s.costs.slippage == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 27)) == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert set(s.strategies) == {"ma_cross", "donchian"}
