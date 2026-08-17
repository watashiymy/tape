"""每日信号入口（收盘后手动运行；baostock 数据约 17:30 后更新——spec §11）。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal.scan import scan
from quant.strategy import build_strategies

SIGNAL_DIR = Path("output/signals")


def main() -> None:
    settings = load_settings("config/settings.yaml")
    bars = {}
    with BaostockProvider() as provider:
        cal = provider.get_trade_calendar(date.today() - timedelta(days=21), date.today())
        if not cal:
            sys.exit("近三周无交易日？交易日历异常，退出")
        expected = cal[-1]  # 最近一个交易日（含今天）
        service = DataService(provider, BarCache("data/cache"))
        for sym in settings.universe:
            df, warns = service.get_bars(sym, settings.start)
            for w in warns:
                print(f"[warn] {sym}: {w}")
            bars[sym] = df

    stale = {s: df.index.max().date() for s, df in bars.items()
             if df.index.max().date() < expected}
    if len(stale) == len(bars):
        # 全部落后 → 数据源尚未更新（baostock 约 17:30 后才有当日数据）
        print(f"全部标的数据均未更新到 {expected}，稍后再试（最新: {sorted(set(stale.values()))}）")
        sys.exit(1)
    if stale:
        # 部分落后 → 多半是个股停牌，跳过它们继续扫描其余标的
        print(f"以下标的数据落后于 {expected}（多为停牌），本次跳过：")
        for s, d in stale.items():
            print(f"  {s}: 最新 {d}")
        bars = {s: df for s, df in bars.items() if s not in stale}

    signals = scan(bars, build_strategies(settings.strategies))
    print(f"\n===== {expected} 信号 =====")
    if not signals:
        print("今日无新信号")
    else:
        print(pd.DataFrame(signals).to_string(index=False))
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = SIGNAL_DIR / f"{expected}.csv"
    pd.DataFrame(signals, columns=["date", "symbol", "strategy", "action", "close"]).to_csv(
        out, index=False)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
