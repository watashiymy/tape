# tests/test_run_backtest.py
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_backtest.py"
_SPEC = importlib.util.spec_from_file_location("run_backtest", _SCRIPT)
run_backtest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_backtest)


def _trade(symbol="600519", action="buy"):
    return Trade(symbol=symbol, action=action, date=pd.Timestamp("2024-01-05"),
                 price=10.0, shares=100, commission=5.0)


def test_trades_csv_keeps_header_when_no_trades(tmp_path):
    """零成交是真实结果（暖机期吃满全部 K 线时就会发生），不是异常。
    不带表头写出的 trades.csv 只有一个换行符，下游 pd.read_csv 直接 EmptyDataError——
    面板/报告一打开就崩，而回测本身其实跑成功了。"""
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([], path)
    df = pd.read_csv(path)          # 修复前：EmptyDataError: No columns to parse from file
    assert df.empty
    assert list(df.columns) == run_backtest.TRADE_COLUMNS


def test_trades_csv_columns_match_trade_fields(tmp_path):
    """空表表头必须与有成交时的列**逐字一致**，否则下游按列名取值会在空表上 KeyError。"""
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([_trade()], path)
    filled = pd.read_csv(path, dtype={"symbol": str})   # 不指定则 000333 会被读成 333
    assert list(filled.columns) == run_backtest.TRADE_COLUMNS
    assert filled["symbol"].tolist() == ["600519"]
    assert filled["commission"].tolist() == [5.0]

    empty_path = tmp_path / "empty.csv"
    run_backtest.write_trades([], empty_path)
    assert list(pd.read_csv(empty_path).columns) == list(filled.columns)


def test_no_trade_field_is_silently_dropped(tmp_path):
    """显式传 columns 的代价是漏列即静默丢数据（to_csv 根本不写那一列）。
    卖出行的 pnl / holding_days 是绩效复核的唯一依据，丢了不会报错只会算错。"""
    sold = Trade(symbol="600519", action="sell", date=pd.Timestamp("2024-02-05"),
                 price=12.0, shares=100, commission=5.0, stamp=0.6,
                 pnl=190.0, holding_days=31)
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([sold], path)
    got = pd.read_csv(path, dtype=str).iloc[0].to_dict()   # dtype=str：逐字比对写出的内容
    expected = {k: str(v) for k, v in vars(sold).items()} | {"date": "2024-02-05"}
    assert got == expected


_CFG_BODY = """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2026-01-05", capital: 5000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: %s
"""


def _run_script(tmp_path, strategies_yaml, extra_args=()):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_CFG_BODY % strategies_yaml, encoding="utf-8")
    # cwd=tmp_path：缓存目录 data/cache 是相对路径，联网前退出则 tmp_path 下不会有任何产物
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--config", str(cfg), *extra_args],
        capture_output=True, text=True, timeout=60, cwd=tmp_path)


def test_empty_strategies_aborts_before_network(tmp_path):
    """回归：策略构造原在联网取数**之后**——strategies: {} 时脚本登录 baostock、
    全量取完十只标的行情，然后静默空跑 exit 0（实测输出 login success! + 154 根K线）。
    "今日无输出"与"配置空了"不可区分。守卫必须在 BaostockProvider 之前：
    快速退出（timeout=60 兜底）、exit code != 0、报错提到策略、且全程不联网。"""
    proc = _run_script(tmp_path, "{}")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"空策略表必须以非零码退出，实际 {proc.returncode}\n{out}"
    assert "策略" in out
    assert "login" not in out, "输出出现 baostock 登录横幅，说明守卫在联网之后"


def test_unknown_strategy_filter_aborts_before_network(tmp_path):
    """--strategy 过滤后为空同样要在联网前退出：配置错误不该白等全量取数。"""
    proc = _run_script(tmp_path, "{ma_cross: {fast: 20, slow: 60}}",
                       extra_args=["--strategy", "ma_corss"])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "策略" in out
    assert "login" not in out


def test_equal_weight_hold_normalizes_each_symbol_to_one():
    """基准是"等权买入持有"：每只先按各自首日归一再取均值。
    直接对价格取均值会让高价股主导基准，贵州茅台一只就能决定曲线形状。"""
    idx = pd.bdate_range("2024-01-01", periods=3)
    bars = {
        "A": pd.DataFrame({"adj_close": [100.0, 110.0, 120.0]}, index=idx),
        "B": pd.DataFrame({"adj_close": [10.0, 10.0, 10.0]}, index=idx),
    }
    got = run_backtest.equal_weight_hold(bars)
    assert got.iloc[0] == pytest.approx(1.0)
    assert got.iloc[1] == pytest.approx((1.1 + 1.0) / 2)
    assert got.iloc[2] == pytest.approx((1.2 + 1.0) / 2)
