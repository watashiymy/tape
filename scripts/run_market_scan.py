"""全市场扫描入口（v0.1.1 设计 §3.4）：收盘后一条命令，扫沪深主板非 ST，输出当日新 BUY。

用法：
    .venv/bin/python scripts/run_market_scan.py [--config config/settings.yaml]
                                                [--limit N] [--date YYYY-MM-DD]
baostock 约 17:30 后才有当日数据。耗时 = 固定开销 + 每票速率（2026-08-24 实测拆解）：
拉全市场清单（get_all_symbols，约 11500 行分页）固定约 2-4 分钟/次；逐票增量取数约
0.5-2 秒/只（网络往返主导，热/冷缓存同量级——缓存省的是不重拉 400 自然日窗口的历史，
省不掉每票一次联网往返）。全量约 3200 只单次预估 0.5-2 小时（50 只实测外推，未做全量
实跑）；中断重跑不会重拉已缓存的历史。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal.market_scan import classify_and_scan, sort_signals
from quant.strategy import build_strategies

SCAN_DIR = Path("output/scan")
CSV_COLUMNS = ["date", "symbol", "name", "strategy", "close", "pct_chg",
               "amount", "amount_ratio_20d"]
SKIP_KEYS = ("stale", "insufficient_history", "is_st", "low_liquidity", "no_signal")
PROGRESS_EVERY = 100


def require_strategies(strategy_cfg: dict[str, dict]) -> list:
    """构造策略，空表则退出（与 run_daily_signal 同一守卫，必须在联网**之前**）。

    strategies 段缺失/为空时 config 静默给 {}——不挡住，扫描会把 3200 只全抓一遍
    然后"成功"输出一张空表，且与"今日无新信号"的正常日输出逐字相同，永远发现不了。
    """
    strategies = build_strategies(strategy_cfg)
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")
    return strategies


def fetch_with_retry(service, symbol: str, start, end):
    """取数失败重试一次；仍失败把异常抛回调用方计数（单票失败不得中断全场扫描）。"""
    try:
        return service.get_bars(symbol, start, end)
    except Exception:
        return service.get_bars(symbol, start, end)


def resolve_expected(provider: BaostockProvider, date_arg: str | None) -> date:
    """基准交易日：--date 指定则用之；缺省取最近交易日（含今天）。"""
    if date_arg:
        return date.fromisoformat(date_arg)
    cal = provider.get_trade_calendar(date.today() - timedelta(days=21), date.today())
    if not cal:
        sys.exit("近三周无交易日？交易日历异常，退出")
    expected = cal[-1]
    if expected != date.today():
        print(f"[注意] 今天 {date.today()} 非交易日，扫描基准为最近交易日 {expected}")
    return expected


def main() -> None:
    ap = argparse.ArgumentParser(description="全市场每日 BUY 信号扫描（沪深主板非 ST）")
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 只（试跑用）")
    ap.add_argument("--date", default=None,
                    help="以指定交易日为基准扫描（YYYY-MM-DD；缺省用最近交易日）")
    args = ap.parse_args()

    settings = load_settings(args.config)
    strategies = require_strategies(settings.strategies)   # 守卫在联网之前
    scan_cfg = settings.scan
    t0 = time.monotonic()

    signals: list[dict] = []
    failures: list[tuple[str, str]] = []
    counts = dict.fromkeys(SKIP_KEYS, 0)
    signal_symbols = 0
    with BaostockProvider() as provider:
        expected = resolve_expected(provider, args.date)
        try:
            universe = provider.get_all_symbols(expected)
        except ValueError as e:
            # 当日 17:30 前清单未更新 / --date 给了非交易日：提示退出（与 run_daily_signal 一致）
            sys.exit(str(e))
        if args.limit:
            universe = universe.head(args.limit)
        total = len(universe)
        print(f"基准日 {expected}，扫描池 {total} 只，策略: {[s.name for s in strategies]}，"
              f"流动性门槛 20日均额 ≥ {scan_cfg.min_avg_amount:,.0f} 元")
        start = expected - timedelta(days=scan_cfg.history_days)
        service = DataService(provider, BarCache("data/cache"))
        for i, (sym, name) in enumerate(zip(universe["symbol"], universe["name"]), 1):
            try:
                df, _warns = fetch_with_retry(service, sym, start, expected)
                df.attrs["symbol"], df.attrs["name"] = sym, name
                sigs, skip = classify_and_scan(df, strategies, expected,
                                               scan_cfg.min_avg_amount)
            except Exception as e:
                failures.append((sym, f"{type(e).__name__}: {e}"))
            else:
                if skip is not None:
                    counts[skip] += 1
                elif sigs:
                    signal_symbols += 1
                    signals.extend(sigs)
                else:
                    counts["no_signal"] += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                print(f"[{i}/{total}] 信号 {len(signals)} 条，失败 {len(failures)} 只，"
                      f"耗时 {time.monotonic() - t0:.0f}s", flush=True)

    signals = sort_signals(signals)
    print(f"\n===== {expected} 全市场新 BUY 信号 =====")
    if not signals:
        print("今日无新信号")
    else:
        with pd.option_context("display.float_format", "{:,.2f}".format):
            print(pd.DataFrame(signals[: scan_cfg.top_n], columns=CSV_COLUMNS)
                  .to_string(index=False))
        if len(signals) > scan_cfg.top_n:
            print(f"（终端仅展示成交额前 {scan_cfg.top_n} 条，共 {len(signals)} 条，全量见 CSV）")

    print(f"\n汇总：扫描 {total} 只 → 信号 {len(signals)} 条（{signal_symbols} 只标的）；"
          f"跳过 stale={counts['stale']}"
          f" insufficient_history={counts['insufficient_history']}"
          f" is_st={counts['is_st']}"
          f" low_liquidity={counts['low_liquidity']}"
          f" no_signal={counts['no_signal']}；"
          f"失败 {len(failures)} 只；总耗时 {time.monotonic() - t0:.0f}s")
    if failures:
        print("失败清单（重试一次后仍失败）：")
        for sym, err in failures:
            print(f"  {sym}: {err}")

    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    out = SCAN_DIR / f"{expected}.csv"
    pd.DataFrame(signals, columns=CSV_COLUMNS).to_csv(out, index=False)  # 空结果也留表头
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
