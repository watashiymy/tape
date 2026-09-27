"""「更新收盘数据」入口（spec §5）：把 baostock 全市场快照按 310 个交易日的窗口补齐，
算出首页「趋势宽度」用的指标表。无参数。

    .venv/bin/python scripts/run_market_breadth.py

日志格式是与 runner 的接口（progress.parse_market_breadth 对着它写）：
    待补 N 天
    [i/N] YYYY-MM-DD 5220 行 用时 18.4s      # 存下
    [i/N] YYYY-MM-DD 跳过：<原因>            # 没存
    指标表已重算：K 天
    全部完成: …                              # 只有成功、或只缺「当天未发布」时才打
    补数失败：…                              # 有过去的日子失败时；退出码非 0

**本脚本任何路径都不许打出 process.DONE_MARKERS 里以「已保存」开头的那个标记**（连带冒号）
——runner 用子串匹配判成败，失败的一轮打了它会在面板重启后被记成成功。
本文件全文都不能出现那个子串，测试用源码扫描钉着，所以这里也只能拐着弯说。
"""
from __future__ import annotations

import sys
import time as _time
from collections.abc import Callable
from datetime import date, time, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant import market
from quant.data.baostock_provider import BaostockError, BaostockProvider, SnapshotInvalid
from quant.market import breadth
from quant.market.store import MarketStore, calendar_complete

WINDOW_TRADING_DAYS = market.WINDOW_TRADING_DAYS  # MA200 + 52 周 250 + 小图 60（spec §5.2）
CALENDAR_BACK_DAYS, CALENDAR_AHEAD_DAYS = 500, 60
RECOMPUTE_EVERY = 5
SESSION_RETRIES = 2
ROWS_FLOOR_RATIO = 0.98             # 行数不少于相邻快照较小那份的 98%，挡住入库中途的半截快照
MARKET_DIR = Path("data/market")


class SessionLost(RuntimeError):
    """会话类错误重登 SESSION_RETRIES 次仍失败。没有会话无法继续，且**不算数据源缺数**。"""


def _recompute(ms: MarketStore, window: list[date], cache: dict[date, pd.DataFrame],
               log: Callable[[str], None]) -> None:
    """全量重算指标表。`cache` 是本次运行里已读/已存的快照，免得每 5 天把 310 份 parquet 重读一遍。"""
    for d in ms.snapshot_dates():
        if d in window and d not in cache:
            cache[d] = ms.load_snapshot(d)
    snaps = {d: df for d, df in cache.items() if d in window}
    if snaps:
        ms.save_breadth(breadth.compute(snaps, window))
        for line in breadth.suspicious_suspended(snaps):
            log(f"注意：{line}")
    log(f"指标表已重算：{len(snaps)} 天")


def run(provider_factory: Callable[[], BaostockProvider], ms: MarketStore, *, today: date,
        now_t: time, log: Callable[[str], None]) -> int:
    """返回退出码。provider_factory 每次调用建一个新会话（重新登录就是再调一次）。"""
    ms.clean_tmp()
    provider = provider_factory()
    provider.__enter__()
    cache: dict[date, pd.DataFrame] = {}
    try:
        start = today - timedelta(days=CALENDAR_BACK_DAYS)
        end = today + timedelta(days=CALENDAR_AHEAD_DAYS)
        trading = frozenset(provider.get_trade_calendar(start, end))
        # 日历日按本地 range 生成：baostock 的 query_trade_dates 返回的就是**每个自然日一行**
        # （is_trading_day 标 1/0），provider.get_trade_calendar 只留了交易日。所以「逐日连续」
        # 那条完整性检查在这里恒过，真正起作用的是「末日 ≥ 今天」与「≥ 310 个交易日」。
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        last_done = _last_finished_trading_day(trading, today, now_t)
        why = calendar_complete(days, trading, today, last_done, WINDOW_TRADING_DAYS)
        if why:
            log(f"补数失败：交易日历不完整（{why}），本次不补数也不删文件")
            return 2
        ms.save_calendar(days, trading)
        window = sorted(d for d in trading if d <= last_done)[-WINDOW_TRADING_DAYS:]
        permanent = ms.permanent_gaps()
        have = set(ms.snapshot_dates())
        todo = [d for d in reversed(window) if d not in have and d not in permanent]
        log(f"待补 {len(todo)} 天")
        n, saved_since, failed_past, today_missing, session_lost = len(todo), 0, [], None, False
        for i, day in enumerate(todo, 1):
            t0 = _time.monotonic()
            try:
                outcome = _fetch_one(provider_factory, provider, day, today)
            except SessionLost as e:
                log(f"[{i}/{n}] {day} 跳过：{e}")
                session_lost = True
                break                             # 没有会话无法继续；不记 gaps
            if isinstance(outcome, str):
                log(f"[{i}/{n}] {day} 跳过：{outcome}")
                if day == today:
                    today_missing = day
                else:
                    ms.record_failure(day, failed_on=today)
                    failed_past.append(day)
                continue
            df, provider = outcome
            # 98% 规则比的是相邻**已存**快照；全新目录的第一份没有相邻件，规则天然不生效（spec §5.2 原意）
            floor = ms.neighbour_rows(day)
            if floor is not None and len(df) < ROWS_FLOOR_RATIO * floor:
                reason = f"只有 {len(df)} 行，少于相邻快照的 98%（{floor} 行），可能入库中途"
                log(f"[{i}/{n}] {day} 跳过：{reason}")
                if day == today:
                    today_missing = day
                else:
                    ms.record_failure(day, failed_on=today)
                    failed_past.append(day)
                continue
            ms.save_snapshot(day, df)
            cache[day] = df
            log(f"[{i}/{n}] {day} {len(df)} 行 用时 {_time.monotonic() - t0:.1f}s")
            saved_since += 1
            if saved_since % RECOMPUTE_EVERY == 0:
                _recompute(ms, window, cache, log)
        _recompute(ms, window, cache, log)
        ms.apply_retention(window_first=window[0])
        if session_lost:
            log("补数失败：数据源会话失效，重新登录后仍然失败；已存的日子保留，稍后再试")
            return 3
        if failed_past:
            dates = "、".join(d.isoformat() for d in failed_past)
            log(f"补数失败：{dates} 拿不到数据（已记录，再失败一次将不再重试）")
            return 1
        done = max((d for d in window if d in set(ms.snapshot_dates())), default=None)
        tail = (f"；{today_missing} 的收盘数据还没发布，稍后再试" if today_missing else "")
        log(f"全部完成: 已补到 {done}{tail}")
        return 0
    finally:
        provider.__exit__(None, None, None)


def _last_finished_trading_day(trading: frozenset[date], today: date, now_t: time) -> date:
    include_today = today in trading and now_t >= market.BREADTH_READY
    candidates = [d for d in trading if d < today or (include_today and d == today)]
    return max(candidates)


def _fetch_one(factory, provider, day: date, today: date):
    """取一天。返回 (df, provider)（provider 可能已换成重新登录的新会话），或失败原因字符串。
    0 行 = 真空结果；SnapshotInvalid 与非会话错误各重试 1 次；会话错误重新登录最多 2 次，
    仍失败抛 SessionLost（调用方停止补数、不记 gaps）。"""
    attempts, relogins = 0, 0
    while True:
        try:
            df = provider.get_market_snapshot(day)
        except BaostockError as e:
            if e.is_session_error:
                if relogins >= SESSION_RETRIES:
                    raise SessionLost(f"会话失效，重新登录 {SESSION_RETRIES} 次仍失败（{e}）") from e
                relogins += 1
                provider.__exit__(None, None, None)
                provider = factory()
                provider.__enter__()
                continue
            if attempts < 1:
                attempts += 1
                continue
            return f"{e}"
        except SnapshotInvalid as e:
            if attempts < 1:
                attempts += 1
                continue
            return f"快照没通过校验（{e}）"
        if df.empty:
            if attempts < 1:
                attempts += 1
                continue
            return "当天的收盘数据还没发布" if day == today else "重试后仍为 0 行"
        return df, provider


def main() -> int:
    now = market.now()
    return run(BaostockProvider, MarketStore(MARKET_DIR), today=now.date(), now_t=now.time(),
               log=print)


if __name__ == "__main__":
    sys.exit(main())
