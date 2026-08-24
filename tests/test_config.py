from datetime import date
from pathlib import Path

import pytest

from quant.config import load_settings

# 必须从 __file__ 推导仓库根：相对路径 "config/settings.yaml" 依赖 cwd，
# 从任何非仓库根目录跑 pytest（IDE、CI 的绝对路径调用）该用例必挂。
REAL_CONFIG = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


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


_BODY = """
universe: %s
benchmark: "000300"
backtest: {start: "2016-01-01", capital: %s}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: {}
"""


def test_empty_universe_raises(tmp_path):
    """回归：空 universe 旧代码放行——run_daily_signal 打印误导性的
    "全部标的数据均未更新"（0==0 恒真）退出，run_backtest 在 equal_weight_hold
    深处抛 No objects to concatenate。必须在加载配置时就报错。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ("[]", "5000000"), encoding="utf-8")
    with pytest.raises(ValueError, match="universe 不能为空"):
        load_settings(cfg)


@pytest.mark.parametrize("capital", ["-5000000", "0"])
def test_nonpositive_capital_raises(tmp_path, capital):
    """回归：负本金旧代码"成功"跑完回测——产出全零 metrics + 上万行
    "资金不足"跳过记录，exit 0，看起来像策略从不交易。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', capital), encoding="utf-8")
    with pytest.raises(ValueError, match="capital"):
        load_settings(cfg)


def test_real_config_file():
    """两个 tmp_path 测试都自带 YAML，谁也管不到真正被脚本加载的那个文件。
    这里钉住 config/settings.yaml 本身，防止手改配置时打错字。"""
    s = load_settings(REAL_CONFIG)
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
    assert s.scan.history_days == 400
    assert s.scan.min_avg_amount == 50_000_000
    assert s.scan.top_n == 20


# ---------- scan 段（v0.1.1 §3.2）----------

def test_scan_defaults_when_section_missing(tmp_path):
    """旧配置没有 scan: 段必须照常加载并给出设计默认值——
    否则升级后所有既有配置文件（含用户手上的副本）集体 KeyError。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', "5000000"), encoding="utf-8")  # _BODY 无 scan 段
    s = load_settings(cfg)
    assert s.scan.history_days == 400
    assert s.scan.min_avg_amount == 50_000_000
    assert s.scan.top_n == 20


def test_scan_section_parsed(tmp_path):
    """显式 scan 段逐项覆盖默认；未给的键仍取默认（部分覆盖是配置文件的常态）。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_BODY % ('["600519"]', "5000000")
                   + "scan: {history_days: 500, min_avg_amount: 80000000}\n",
                   encoding="utf-8")
    s = load_settings(cfg)
    assert s.scan.history_days == 500
    assert s.scan.min_avg_amount == 80_000_000
    assert s.scan.top_n == 20  # 未显式给出 → 默认
