"""回测入口：python scripts/run_backtest.py [--config config/settings.yaml] [--strategy 名称]
输出到 output/<策略>_<运行时间戳>/：metrics.json、equity.csv、trades.csv、report.html、
kline_<代码>.html × N。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.backtest.engine import Backtester
from quant.backtest.portfolio import Trade
from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from quant.strategy import build_strategies

OUTPUT = Path("output")

# 显式列名从 Trade 字段派生：加字段不会漏列，零成交时表头也不会消失。
TRADE_COLUMNS = [f.name for f in fields(Trade)]


def write_trades(trades: list[Trade], path: Path) -> None:
    """成交流水落盘。必须显式给 columns：零成交（暖机期吃满全部 K 线时就会发生）时
    pd.DataFrame([]) 一列都没有，写出的文件只有一个换行符，
    下游 pd.read_csv 直接 EmptyDataError——回测明明跑成功了，面板一开就崩。"""
    pd.DataFrame([vars(t) for t in trades], columns=TRADE_COLUMNS).to_csv(path, index=False)


def equal_weight_hold(bars: dict[str, pd.DataFrame]) -> pd.Series:
    """等权买入持有基准：各标的后复权收盘归一化后取均值（分红再投资口径，与策略同权）。"""
    norm = [df["adj_close"] / df["adj_close"].iloc[0] for df in bars.values()]
    return pd.concat(norm, axis=1).ffill().mean(axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--strategy", default=None, help="只跑指定策略，默认全部")
    ap.add_argument("--refresh", action="store_true", help="强制全量刷新行情缓存")
    args = ap.parse_args()

    settings = load_settings(args.config)
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

    strategies = build_strategies(settings.strategies)
    if args.strategy:
        strategies = [s for s in strategies if s.name == args.strategy]
        if not strategies:
            sys.exit(f"未知策略: {args.strategy}")

    benchmarks = {"沪深300(价格指数,不含分红)": bench_close, "等权买入持有": equal_weight_hold(bars)}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for strat in strategies:
        positions = {s: strat.generate_positions(df) for s, df in bars.items()}
        result = Backtester(bars, positions, settings).run()
        metrics = compute_metrics(result.equity, result.trades)

        run_dir = OUTPUT / f"{strat.name}_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result.equity.rename("equity").to_csv(run_dir / "equity.csv")
        write_trades(result.trades, run_dir / "trades.csv")
        pd.DataFrame(result.skipped, columns=["date", "symbol", "reason"]).to_csv(
            run_dir / "skipped.csv", index=False)
        equity_chart(result.equity, benchmarks).write_html(run_dir / "report.html")
        for sym, df in bars.items():
            sym_trades = [t for t in result.trades if t.symbol == sym]
            kline_chart(df, sym_trades, sym).write_html(run_dir / f"kline_{sym}.html")

        print(f"\n===== {strat.name} =====")
        for k, v in metrics.items():
            print(f"  {k:>18}: {v:.4f}" if isinstance(v, float) else f"  {k:>18}: {v}")
        print(f"  报告目录: {run_dir}")


if __name__ == "__main__":
    main()
