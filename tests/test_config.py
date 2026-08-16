from datetime import date

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
    assert s.universe == ["600519", "000333"]
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
