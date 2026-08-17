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


def require_strategies(strategy_cfg: dict[str, dict]) -> list:
    """构造策略，空表则退出。

    "今日无新信号"是多数日子的正常结果，与"一个策略都没跑"的输出**逐字相同**：
    同样打印无信号、同样写出只有表头的 CSV、同样退出码 0。而 config.py 的
    `raw.get("strategies") or {}` 让 settings.yaml 的 strategies 段缺失/为空/
    键名拼错时静默得到 {}——不挡住，信号系统会天天空跑且零告警，用户永远发现不了。
    在联网取数之前就退出，免得白抓十只标的的行情。
    """
    strategies = build_strategies(strategy_cfg)
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")
    return strategies


def main() -> None:
    settings = load_settings("config/settings.yaml")
    strategies = require_strategies(settings.strategies)
    bars = {}
    with BaostockProvider() as provider:
        cal = provider.get_trade_calendar(date.today() - timedelta(days=21), date.today())
        if not cal:
            sys.exit("近三周无交易日？交易日历异常，退出")
        expected = cal[-1]  # 最近一个交易日（含今天）
        if expected != date.today():
            # 周末/长假补跑上一交易日的信号是真实且合理的用法，故不像 spec §11 那样硬退出；
            # 但必须显式说破，否则用户会把上一交易日的旧信号当成今天的新信号。
            print(f"[注意] 今天 {date.today()} 非交易日，以下是最近交易日 {expected} 的信号"
                  f"（重算结果与当日一致，会覆盖同名 CSV）")
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

    signals = scan(bars, strategies)
    print(f"\n===== {expected} 信号 =====（扫描 {len(bars)} 只 × {len(strategies)} 个策略）")
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
