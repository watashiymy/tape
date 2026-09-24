"""两市成交额（spec §2 第 3 项、§3）。上证 + 深证成指 = 沪深两市全部（含极少量 B 股）。

盘中绝不给「较上一交易日 %」：半天比全天必然难看且误导。百分比只在
口径一致性检查**已做且通过**时给（`pct_ok is True`）；没做（None）或没过（False）都不给。
"""
from __future__ import annotations

from datetime import date, datetime, time

from quant.market.session import CLOSE, CLOSE_CUTOFF
from quant.market.sources import DailyBar

CONSISTENCY_TOLERANCE_WAN = 1.0     # [37] 是截断后的整数，走完的 bar 带两位小数，不能比相等


def previous_day(sh: list[DailyBar], sz: list[DailyBar], trade_date: date) -> DailyBar | None:
    """日期**严格早于** trade_date 的最后一根，两市相加。两边日期对不上就不给。"""
    prev_sh = [b for b in sh if b.day < trade_date]
    prev_sz = [b for b in sz if b.day < trade_date]
    if not prev_sh or not prev_sz or prev_sh[-1].day != prev_sz[-1].day:
        return None
    return DailyBar(prev_sh[-1].day, prev_sh[-1].amount_wan + prev_sz[-1].amount_wan)


def consistent(sh: list[DailyBar], sz: list[DailyBar], trade_date: date,
               quote_sh_wan: float, quote_sz_wan: float) -> bool | None:
    """K 线里 trade_date 那根与行情快照 [37] 相差 < 1 万元 → 口径一致。那根不存在 → None（没法检查）。"""
    bar_sh = next((b for b in sh if b.day == trade_date), None)
    bar_sz = next((b for b in sz if b.day == trade_date), None)
    if bar_sh is None or bar_sz is None:
        return None
    return (abs(bar_sh.amount_wan - quote_sh_wan) < CONSISTENCY_TOLERANCE_WAN
            and abs(bar_sz.amount_wan - quote_sz_wan) < CONSISTENCY_TOLERANCE_WAN)


def settled(now: datetime, trade_date: date, today: date) -> bool:
    """当天那根 K 线是否已定格（§3 缓存键的第二个分量，单调不回退）：
    数据属于过去的交易日、或今天已过收盘截止时刻。"""
    if trade_date < today:
        return True
    return now.time() >= CLOSE_CUTOFF


def _wan_yi(amount_wan: float) -> str:
    return f"{amount_wan / 1e8:.2f} 万亿"


def _day(d: date) -> str:
    return f"{d.month} 月 {d.day} 日"


def line(now: datetime, today: date, trade_date: date, current_wan: float, current_ts: time,
         prev: DailyBar | None, pct_ok: bool | None) -> str:
    intraday = trade_date == today and now.time() < CLOSE
    if intraday:
        head = f"两市成交额 {_wan_yi(current_wan)}（截至 {current_ts:%H:%M}）"
    else:
        head = f"两市成交额 {_day(trade_date)}全天 {_wan_yi(current_wan)}"
    if prev is None:
        return head
    text = f"{head} · {_day(prev.day)}全天 {_wan_yi(prev.amount_wan)}"
    if not intraday and pct_ok is True and prev.amount_wan > 0:
        pct = (current_wan / prev.amount_wan - 1) * 100
        text += f" · {pct:+.1f}%"
    return text
