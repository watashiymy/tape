# tests/test_run_backtest.py
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import BacktestResult, Trade
from quant.config import load_settings

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


def test_equal_weight_hold_treats_pre_listing_share_as_cash():
    """一只标的晚开始（晚上市/晚有数据）：入场前该份额按现金 1.0 计，
    与引擎"固定额度、闲置为现金"口径一致。旧代码 mean(skipna) 直接忽略缺席标的：
    A 先涨 50% 时基准被顶到 1.5，B 一出现又拽回 1.25——凭空一段虚高再跳水。"""
    idx = pd.bdate_range("2024-01-01", periods=4)
    bars = {
        "A": pd.DataFrame({"adj_close": [100.0, 150.0, 150.0, 150.0]}, index=idx),
        "B": pd.DataFrame({"adj_close": [50.0, 50.0]}, index=idx[2:]),
    }
    got = run_backtest.equal_weight_hold(bars)
    # 手算：d1 (1+1)/2=1，d2 A=1.5 B=现金1.0 → 1.25，d3 (1.5+1)/2=1.25，d4 同
    assert got.tolist() == pytest.approx([1.0, 1.25, 1.25, 1.25])


def _fake_run_inputs():
    idx = pd.bdate_range("2024-01-02", periods=3)
    equity = pd.Series([100.0, 101.0, 102.0], index=idx)
    result = BacktestResult(equity=equity, trades=[_trade()], skipped=[])
    bars = {"600519": pd.DataFrame(
        {"open": [10.0] * 3, "high": [11.0] * 3, "low": [9.0] * 3, "close": [10.5] * 3},
        index=idx)}
    return result, bars, {"基准": equity.copy()}


def test_metrics_json_is_written_last_as_completion_marker(tmp_path, monkeypatch):
    """Ctrl-C 时序：旧代码先写 metrics.json 再花 1-2 秒写 55MB 的 report.html，
    打断后留下带合法 metrics.json 的半截目录，面板默认选中它直接崩页。
    metrics.json 必须最后写（完成标记）：模拟写 HTML 时被打断，目录里不得有它。"""
    result, bars, benchmarks = _fake_run_inputs()
    run_dir = tmp_path / "ma_cross_20260824_000000"

    def boom(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", boom, raising=True)
    with pytest.raises(KeyboardInterrupt):
        run_backtest.write_run_outputs(run_dir, {"total_return": 0.1}, result,
                                       bars, benchmarks, snapshot={})
    assert not (run_dir / "metrics.json").exists(), \
        "被打断的半截目录不该有 metrics.json——完成标记必须最后写"


def test_write_run_outputs_writes_full_set(tmp_path):
    """完整跑完时七件套齐全，metrics/config_snapshot 内容逐字可回读。"""
    result, bars, benchmarks = _fake_run_inputs()
    run_dir = tmp_path / "run"
    run_backtest.write_run_outputs(run_dir, {"total_return": 0.1}, result, bars,
                                   benchmarks, snapshot={"capital": 5000000.0})
    for f in ("config_snapshot.json", "equity.csv", "trades.csv", "skipped.csv",
              "report.html", "kline_600519.html", "metrics.json"):
        assert (run_dir / f).exists(), f"缺 {f}"
    assert json.loads((run_dir / "metrics.json").read_text(encoding="utf-8")) \
        == {"total_return": 0.1}
    assert json.loads((run_dir / "config_snapshot.json").read_text(encoding="utf-8")) \
        == {"capital": 5000000.0}


def test_config_snapshot_serializes_dates_and_costs(tmp_path):
    """run 目录要能留档本次配置。date 不可直接 json.dumps，必须已转成 ISO 字符串。"""
    cfg = tmp_path / "s.yaml"
    cfg.write_text(_CFG_BODY % "{ma_cross: {fast: 20, slow: 60}}", encoding="utf-8")
    snap = run_backtest.config_snapshot(load_settings(cfg))
    assert snap["universe"] == ["600519"]
    assert snap["benchmark"] == "000300"
    assert snap["start"] == "2026-01-05"          # date → ISO 字符串
    assert snap["capital"] == 5000000.0
    assert snap["costs"]["commission_rate"] == 0.00025
    assert snap["costs"]["commission_min"] == 5.0
    assert snap["costs"]["slippage"] == 0.001
    assert snap["costs"]["stamp_tax"][0] == \
        {"rate": 0.001, "until": "2023-08-27", "frm": None}
    assert snap["strategies"] == {"ma_cross": {"fast": 20, "slow": 60}}
    json.dumps(snap, ensure_ascii=False)           # 整体必须可直接序列化


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
