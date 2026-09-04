"""信号跟踪入口（v0.5.0 之前叫「每日信号」）：算出信号池在某个交易日的 BUY/SELL 变化。

    python scripts/run_daily_signal.py                      # 按数据最新的那一天算
    python scripts/run_daily_signal.py --date 2026-09-01    # 重算历史某天

**为什么改名、为什么不再要求"今天的数据到了才能跑"**：baostock 的当日日线约 17:30
才更新。老版本的判据是"最近一个交易日"，17:30 前跑必然撞上「全部标的数据均未更新到
今天」直接退出——而用户此刻想看的往往就是"截至目前最新的信号"，也就是上一交易日的。
现在缺省取**数据里最新的那一天**：今天的还没到就自动退到上一交易日，并在日志里说破；
想要哪天就 `--date` 指定，与全市场扫描同一套用法。它算的不一定是"今天"，
所以不再叫"每日"。

产物仍是 `output/signals/<基准日>.csv`：同一天重算会覆盖同名文件（同一件事重做）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal import baseday
from quant.signal.scan import scan
from quant.strategy import build_strategies

SIGNAL_DIR = Path("output/signals")
# 信号 CSV 的列（面板的列配置按它对齐；与全市场扫描的 CSV_COLUMNS 不是同一套）
CSV_COLUMNS = ["date", "symbol", "strategy", "action", "close"]


def require_strategies(strategy_cfg: dict[str, dict]) -> list:
    """构造策略，空表则退出。

    "无新信号"是多数日子的正常结果，与"一个策略都没跑"的输出**逐字相同**：
    同样打印无信号、同样写出只有表头的 CSV、同样退出码 0。而 config.py 的
    `raw.get("strategies") or {}` 让 settings.yaml 的 strategies 段缺失/为空/
    键名拼错时静默得到 {}——不挡住，信号系统会天天空跑且零告警，用户永远发现不了。
    在联网取数之前就退出，免得白抓十只标的的行情。
    """
    strategies = build_strategies(strategy_cfg)
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")
    return strategies


def latest_data_day(bars: dict[str, pd.DataFrame]) -> date:
    """池子里数据最新的那一天：各标的最后一根 K 线日期的最大值。

    这是缺省的基准日。用"最大值"而不是"最小值"：最小值会被一只长期停牌的票拖到
    几个月前，把所有人的信号都算成陈年旧账；最大值只会让停牌的那几只被判为
    "落后"并跳过（下面 split_by_day 就是这么处理的），与老版本对停牌的处理一致。
    """
    if not bars:
        raise ValueError("信号池里没有任何标的有行情数据")
    return max(df.index.max().date() for df in bars.values() if len(df))


def split_by_day(bars: dict[str, pd.DataFrame], base: date
                 ) -> tuple[dict[str, pd.DataFrame], dict[str, date]]:
    """按基准日切分：每只都截到 ≤ base，最后一根仍 < base 的算「落后」。

    **截断是硬要求**：`--date` 指历史某天时，缓存里有那之后的 K 线，不截的话
    scan() 会拿最后一根（也就是今天）算信号，产物文件名却写着历史那天——
    一份日期与内容对不上的信号清单，没有任何报错。
    返回 (可用的, 落后的: 各自最后一根的日期)。
    """
    usable: dict[str, pd.DataFrame] = {}
    stale: dict[str, date] = {}
    cutoff = pd.Timestamp(base)
    for sym, df in bars.items():
        cut = df[df.index <= cutoff]
        if len(cut) == 0 or cut.index.max().date() < base:
            stale[sym] = df.index.max().date() if len(df) else None
        else:
            usable[sym] = cut
    return usable, stale


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=None,
                    help="基准交易日 YYYY-MM-DD。留空 = 池子数据最新的那一天"
                         "（今天的还没更新就自动退到上一交易日，并在日志里说明）")
    args = ap.parse_args()

    settings = load_settings("config/settings.yaml")
    strategies = require_strategies(settings.strategies)
    requested = date.fromisoformat(args.date) if args.date else None

    bars: dict[str, pd.DataFrame] = {}
    with BaostockProvider() as provider:
        try:
            if requested is not None:
                # 闸门一，在取数之前：日期填错要在几百毫秒内知道
                baseday.require_trading_day(provider, requested)
            expected = baseday.latest_trading_day(provider)
        except ValueError as e:
            sys.exit(str(e))
        if requested is None and (note := baseday.weekday_note(expected)):
            print(note)
        service = DataService(provider, BarCache("data/cache"))
        for sym in settings.universe:
            df, warns = service.get_bars(sym, settings.start)
            for w in warns:
                print(f"[warn] {sym}: {w}")
            bars[sym] = df

    if requested is not None:
        base = requested
    else:
        try:
            base = latest_data_day(bars)
        except ValueError as e:
            sys.exit(str(e))
        if base < expected:
            print(f"[注意] 数据最新到 {base}，{expected} 的还没更新（baostock 约 17:30 后才有）。"
                  f"以下是 {base} 的信号；想要 {expected} 的，稍后不带参数重跑即可。")

    usable, stale = split_by_day(bars, base)
    if not usable:
        # 一只都没有 base 那天的数据。缺省模式下走不到这里（base 就是从数据里取的）；
        # 只有 --date 指了一个数据还没到（或全池停牌）的日子才会。
        # 前缀与老版本逐字相同：面板的进度解析（runner/progress.py 的 _STALE）认它。
        latest = sorted({d for d in stale.values() if d is not None})
        print(f"全部标的数据均未更新到 {base}，稍后再试（最新: {latest}）")
        sys.exit(1)
    if stale:
        # 部分落后 → 多半是个股停牌，跳过它们继续算其余标的
        print(f"以下标的没有 {base} 的数据（多为停牌），本次跳过：")
        for s, d in stale.items():
            print(f"  {s}: 最新 {d}")

    signals = scan(usable, strategies, overlays=settings.overlays)
    print(f"\n===== {base} 信号 =====（{len(usable)} 只 × {len(strategies)} 个策略）")
    if not signals:
        print(f"{base} 无新信号")
    else:
        print(pd.DataFrame(signals).to_string(index=False))
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = SIGNAL_DIR / f"{base}.csv"
    pd.DataFrame(signals, columns=CSV_COLUMNS).to_csv(out, index=False)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
