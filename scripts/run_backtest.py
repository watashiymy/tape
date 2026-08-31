"""回测入口：python scripts/run_backtest.py [--config config/settings.yaml] [--strategy 名称]
输出到 output/<策略>_<运行时间戳>/：config_snapshot.json、equity.csv、trades.csv、
report.html、kline_<代码>.html × N、metrics.json（最后写，完成标记）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.backtest.engine import Backtester
from quant.backtest.portfolio import BacktestResult, Trade
from quant.config import OverlaysCfg, Settings, load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from quant.strategy import build_strategies
from quant.strategy.base import Strategy
from quant.strategy.pipeline import target_positions

OUTPUT = Path("output")

# 显式列名从 Trade 字段派生：加字段不会漏列，零成交时表头也不会消失。
TRADE_COLUMNS = [f.name for f in fields(Trade)]
# 被跳过的订单（涨跌停/资金不足等）。与成交明细不是一套列，面板的列配置按它对齐。
SKIPPED_COLUMNS = ["date", "symbol", "reason"]


def write_trades(trades: list[Trade], path: Path) -> None:
    """成交流水落盘。必须显式给 columns：零成交（暖机期吃满全部 K 线时就会发生）时
    pd.DataFrame([]) 一列都没有，写出的文件只有一个换行符，
    下游 pd.read_csv 直接 EmptyDataError——回测明明跑成功了，面板一开就崩。"""
    pd.DataFrame([vars(t) for t in trades], columns=TRADE_COLUMNS).to_csv(path, index=False)


def equal_weight_hold(bars: dict[str, pd.DataFrame]) -> pd.Series:
    """等权买入持有基准：各标的后复权收盘归一化后取均值（分红再投资口径，与策略同权）。

    fillna(1.0) 补的是**前导** NaN（晚上市/晚有数据的标的入场之前）：该份额按现金
    1.0 计，与引擎"固定额度、闲置为现金"口径一致。少了它 mean(skipna) 会直接忽略
    缺席标的——先涨的标的把基准顶高，晚来的一出现又拽回来，凭空一段虚高。
    ffill 补的是中段 NaN（停牌日），必须在 fillna 之前。"""
    norm = [df["adj_close"] / df["adj_close"].iloc[0] for df in bars.values()]
    return pd.concat(norm, axis=1).ffill().fillna(1.0).mean(axis=1)


def strategy_positions(strat: Strategy, bars: dict[str, pd.DataFrame],
                       overlays: OverlaysCfg) -> dict[str, pd.Series]:
    """整个回测入口的目标仓位出口：逐标的经 pipeline.target_positions（v0.4.0 M2）。

    此前这里直调 strat.generate_positions —— 三个入口各自直调时，叠加层（止损/趋势
    过滤）漏接任何一处都是静默分叉：扫描说买、回测按另一套规则算，谁也不报错。
    tests/test_strategy_pipeline.py 的源码级断言钉着 scripts/ 下不许再出现那种直调。
    """
    return {sym: target_positions(df, strat, overlays) for sym, df in bars.items()}


def config_snapshot(settings: Settings) -> dict:
    """本次回测的配置留档（universe/benchmark/start/capital/costs/strategies）。
    date 不能直接进 json.dumps，经 default=str 一次往返统一转成 ISO 字符串。"""
    return json.loads(json.dumps(asdict(settings), ensure_ascii=False, default=str))


def write_run_outputs(run_dir: Path, metrics: dict, result: BacktestResult,
                      bars: dict[str, pd.DataFrame], benchmarks: dict[str, pd.Series],
                      snapshot: dict) -> None:
    """落盘一次回测的全部产物。metrics.json 必须**最后**写（完成标记）：
    HTML 要花 1-2 秒写几十 MB，Ctrl-C 打断后若 metrics.json 已在，
    面板会把这个半截目录当成一次完整回测。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    result.equity.rename("equity").to_csv(run_dir / "equity.csv")
    write_trades(result.trades, run_dir / "trades.csv")
    pd.DataFrame(result.skipped, columns=SKIPPED_COLUMNS).to_csv(
        run_dir / "skipped.csv", index=False)
    equity_chart(result.equity, benchmarks).write_html(run_dir / "report.html")
    for sym, df in bars.items():
        sym_trades = [t for t in result.trades if t.symbol == sym]
        kline_chart(df, sym_trades, sym).write_html(run_dir / f"kline_{sym}.html")
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--strategy", default=None, help="只跑指定策略，默认全部")
    ap.add_argument("--refresh", action="store_true", help="强制全量刷新行情缓存")
    args = ap.parse_args()

    settings = load_settings(args.config)
    # 策略构造 + 过滤 + 空表守卫必须在联网取数**之前**：配置错误不该白等全量取数；
    # 空策略表若放行会静默空跑 exit 0（循环体一次不进），与"回测没产出"不可区分。
    strategies = build_strategies(settings.strategies)
    if args.strategy:
        strategies = [s for s in strategies if s.name == args.strategy]
        if not strategies:
            sys.exit(f"未知策略: {args.strategy}")
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")

    cache = BarCache("data/cache")
    bars: dict[str, pd.DataFrame] = {}
    with BaostockProvider() as provider:
        service = DataService(provider, cache)
        for sym in settings.universe:
            df, warns = service.get_bars(sym, settings.start, refresh=args.refresh)
            for w in warns:
                print(f"[warn] {sym}: {w}")
            bars[sym] = df
            print(f"[data] {sym}: {len(df)} 根K线 ({df.index.min().date()} ~ {df.index.max().date()})")
        bench_close = provider.get_index_daily(
            settings.benchmark, settings.start, pd.Timestamp.now().date())["close"]

    benchmarks = {"沪深300(价格指数,不含分红)": bench_close, "等权买入持有": equal_weight_hold(bars)}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = config_snapshot(settings)

    for strat in strategies:
        positions = strategy_positions(strat, bars, settings.overlays)
        result = Backtester(bars, positions, settings).run()
        metrics = compute_metrics(result.equity, result.trades)

        run_dir = OUTPUT / f"{strat.name}_{stamp}"
        write_run_outputs(run_dir, metrics, result, bars, benchmarks, snapshot)

        print(f"\n===== {strat.name} =====")
        for k, v in metrics.items():
            print(f"  {k:>18}: {v:.4f}" if isinstance(v, float) else f"  {k:>18}: {v}")
        print(f"  报告目录: {run_dir}")

    # 整轮完成标记（循环外，只打一次）。"报告目录:" 打在循环**内部**，配置里两个策略
    # 就有两行，第一行落盘时 donchian 还没开始算——面板据此判"完成"会早报一半，
    # 僵尸清理（process.DONE_MARKERS）更会把被 kill -9 的半截回测判成 success。
    print(f"\n全部完成: {len(strategies)} 个策略")


if __name__ == "__main__":
    main()
