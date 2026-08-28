"""全市场扫描入口（v0.1.1 设计 §3.4）：收盘后一条命令，扫沪深主板非 ST，输出当日新 BUY。

用法：
    .venv/bin/python scripts/run_market_scan.py [--config config/settings.yaml]
                                                [--limit N] [--date YYYY-MM-DD]
                                                [--refresh-symbols]
baostock 约 17:30 后才有当日数据。耗时 = 固定开销 + 每票速率（2026-08-24 实测拆解）：
拉全市场清单（get_all_symbols，约 11500 行分页）固定约 2-4 分钟/次；逐票增量取数约
0.5-2 秒/只（网络往返主导，热/冷缓存同量级——缓存省的是不重拉 400 自然日窗口的历史，
省不掉每票一次联网往返）。全量约 3200 只单次预估 0.5-2 小时（50 只实测外推，未做全量
实跑）；中断重跑不会重拉已缓存的历史。

自 v0.2.3 起清单会落盘到 data/symbols.parquet，7 天内直接复用——那 2-4 分钟的固定
开销因此每周只付一次（`--refresh-symbols` 可强制重拉；它与回测脚本的 `--refresh`
是两回事，后者管的是行情缓存）。这份文件同时是面板「名称」列的离线来源。
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
from quant.data import symbols
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal.market_scan import classify_and_scan, sort_signals
from quant.strategy import build_strategies

SCAN_DIR = Path("output/scan")
CACHE_DIR = Path("data/cache")
SYMBOLS_PATH = symbols.SYMBOLS_PATH     # 全市场清单的落点（测试注入 tmp_path）
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


def load_universe(provider: BaostockProvider, expected: date, *,
                  refresh: bool = False) -> pd.DataFrame:
    """全市场清单：本地那份还新鲜就直接用，否则联网拉一次并落盘。

    联网拉一次固定 2-4 分钟（约 11500 行分页），而清单变动很慢（新股上市/退市/改名），
    每天重拉是纯浪费。复用时**必须把话说出来**：不然用户只知道这次快了，
    不知道为什么，也就无从判断名字/候选清单是不是旧的。

    清单文件损坏时 load_symbols 会响亮抛 RuntimeError，这里刻意不接：
    接住退化成"当作没有缓存去重拉"的话，每轮白付 2-4 分钟，而那个坏文件
    可以在磁盘上躺几个月没人发现。

    **落盘的是完整清单**，调用方的 `--limit` 截取必须发生在这之后：
    一次 `--limit 30` 若把文件写成 30 只，面板的名称列与候选清单会跟着只剩 30 只，
    而且没有任何报错。
    """
    if not refresh:
        cached = symbols.load_symbols(SYMBOLS_PATH)
        if cached is not None:
            listing, as_of = cached
            if symbols.is_fresh(as_of, date.today()):
                print(f"复用本地清单（as_of={as_of}，{len(listing)} 只，{SYMBOLS_PATH}）；"
                      f"省下拉取全市场清单的 2-4 分钟，要强制重拉加 --refresh-symbols")
                return listing
            print(f"本地清单已过期（as_of={as_of}），重新拉取…")
    listing = provider.get_all_symbols(expected)     # ValueError 交给调用方（非交易日等）
    symbols.save_symbols(listing, expected, SYMBOLS_PATH)
    print(f"已拉取全市场清单 {len(listing)} 只并保存到 {SYMBOLS_PATH}（as_of={expected}）")
    return listing


def main() -> None:
    # allow_abbrev=False 是**必须**的：argparse 默认认前缀缩写，而 `--refresh` 恰好是
    # `--refresh-symbols` 的唯一前缀。回测脚本有个 `--refresh`（刷行情缓存），
    # 手顺打到这里就会被静默解释成"重拉清单"——白等 2-4 分钟，还以为行情重拉了。
    ap = argparse.ArgumentParser(description="全市场每日 BUY 信号扫描（沪深主板非 ST）",
                                 allow_abbrev=False)
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 只（试跑用）")
    ap.add_argument("--date", default=None,
                    help="以指定交易日为基准扫描（YYYY-MM-DD；缺省用最近交易日）")
    ap.add_argument("--refresh-symbols", action="store_true",
                    help="强制重拉全市场清单（默认 7 天内复用本地 data/symbols.parquet）。"
                         "注意与回测脚本的 --refresh 不是一回事，后者刷的是行情缓存")
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
            universe = load_universe(provider, expected, refresh=args.refresh_symbols)
        except ValueError as e:
            # 当日 17:30 前清单未更新 / --date 给了非交易日：提示退出（与 run_daily_signal 一致）
            sys.exit(str(e))
        if args.limit:
            # 截取只影响本轮扫描；清单已在 load_universe 里整份落盘（见那里的说明）
            universe = universe.head(args.limit)
        total = len(universe)
        print(f"基准日 {expected}，扫描池 {total} 只，策略: {[s.name for s in strategies]}，"
              f"流动性门槛 20日均额 ≥ {scan_cfg.min_avg_amount:,.0f} 元")
        start = expected - timedelta(days=scan_cfg.history_days)
        service = DataService(provider, BarCache(CACHE_DIR))
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
