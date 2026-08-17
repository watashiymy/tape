# tests/test_run_backtest.py
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade

_SPEC = importlib.util.spec_from_file_location(
    "run_backtest", Path(__file__).resolve().parent.parent / "scripts" / "run_backtest.py")
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
