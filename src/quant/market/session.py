"""首页的时段判定（spec §4）。纯函数：不看时钟、不碰网络、不碰 streamlit。

三件事：今天是不是交易日（三态判定链）、现在处于哪个状态、数据算不算过期。
**只返回枚举**，文案映射在 app/guide.py 的 HOME_STATE_TEXT——本模块属于 src/，不 import app/。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum

# 时段边界（北京时间；全项目假定本机时钟即北京时间）
AUCTION_START = time(9, 14)
AUCTION_END = time(9, 25)
OPEN = time(9, 30)
LUNCH_START = time(11, 30)
LUNCH_END = time(13, 0)
CLOSE = time(15, 0)
#: 收盘截止时刻：东财分布收盘后至少 20 分钟还在变（实测 15:20 才稳）。暂定 15:30，Task 26 按实测改。
CLOSE_CUTOFF = time(15, 30)
#: 判休市截止时刻：源日期过了这个点还是旧的才算非交易日。暂定 9:45，Task 26 按实测改。
NON_TRADING_CUTOFF = time(9, 45)
# 连续竞价时段（过期判据只在这两段里判）
CONTINUOUS = ((time(9, 31), LUNCH_START), (time(13, 1), time(14, 57)))
STALE_MINUTE_S = 180      # 分时最后一点落后 3 分钟
STALE_QUOTE_S = 120       # 行情时间戳落后 2 分钟

LIVE_INTERVAL_S, TICK_INTERVAL_S = 30, 60
SHORT_TTL_S, LONG_TTL_S = 25, 600


class TradeDay(Enum):
    TRADING = "trading"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


class State(Enum):
    CLOSED_DAY = "closed_day"   # 休市（非交易日）
    PRE_OPEN = "pre_open"       # 盘前
    AUCTION = "auction"         # 集合竞价
    WAIT_OPEN = "wait_open"     # 待开盘
    TRADING = "trading"         # 交易中
    LUNCH = "lunch"             # 午间休市
    CLOSED = "closed"           # 已收盘
    UNKNOWN = "unknown"         # 状态未知


@dataclass(frozen=True)
class Calendar:
    """本地交易日历的只读视图（store.py 从 calendar.parquet 装出来）。
    只在 [first, last] 内当作权威；范围外一律「未知」，绝不当作休市。"""
    first: date
    last: date
    trading: frozenset[date]

    def covers(self, day: date) -> bool:
        return self.first <= day <= self.last

    def is_trading(self, day: date) -> bool:
        return day in self.trading

    def previous_trading_day(self, day: date) -> date | None:
        d = day - timedelta(days=1)
        while d >= self.first:
            if d in self.trading:
                return d
            d -= timedelta(days=1)
        return None


@dataclass(frozen=True)
class Decision:
    state: State
    interval_s: int      # fragment 的 run_every
    cache_ttl: int       # 走哪个缓存入口（25 秒 / 600 秒）


def trade_day(today: date, calendar: Calendar | None, source_ok: bool,
              source_date: date | None, now_t: time,
              cutoff: time = NON_TRADING_CUTOFF) -> TradeDay:
    """§4.1 判定链，命中即停。请求失败永远不计入。"""
    if today.weekday() >= 5:
        return TradeDay.NON_TRADING
    if calendar is not None and calendar.covers(today):
        return TradeDay.TRADING if calendar.is_trading(today) else TradeDay.NON_TRADING
    if source_ok and source_date is not None:
        if source_date == today:
            return TradeDay.TRADING
        if source_date < today and now_t >= cutoff:
            return TradeDay.NON_TRADING
    return TradeDay.UNKNOWN


def _clock_row(now_t: time) -> tuple[State, int, int]:
    """交易日的时钟行 →（状态, 间隔, 缓存 TTL）。§4.2 表格的直译。"""
    if now_t < AUCTION_START:
        return State.PRE_OPEN, TICK_INTERVAL_S, LONG_TTL_S
    if now_t < AUCTION_END:
        return State.AUCTION, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < OPEN:
        return State.WAIT_OPEN, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < LUNCH_START:
        return State.TRADING, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < LUNCH_END:
        return State.LUNCH, TICK_INTERVAL_S, LONG_TTL_S
    if now_t < CLOSE:
        return State.TRADING, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < CLOSE_CUTOFF:
        return State.CLOSED, TICK_INTERVAL_S, SHORT_TTL_S     # 收盘后统计仍在修正：每 60 秒取一次
    return State.CLOSED, TICK_INTERVAL_S, LONG_TTL_S


def decide(now: datetime, td: TradeDay) -> Decision:
    """非交易日优先于所有时钟行；未知按交易日行的节奏取数、但状态是「未知」。"""
    if td is TradeDay.NON_TRADING:
        return Decision(State.CLOSED_DAY, TICK_INTERVAL_S, LONG_TTL_S)
    state, interval, ttl = _clock_row(now.time())
    if td is TradeDay.UNKNOWN:
        state = State.UNKNOWN
    return Decision(state, interval, ttl)


def in_continuous_auction(now_t: time) -> bool:
    return any(a <= now_t < b for a, b in CONTINUOUS)


def is_stale(now: datetime, state: State, last_point: datetime | None,
             quote_ts: datetime | None) -> bool:
    """§4.3 的过期判据。「数据延迟」是**只叠加在「交易中」上**的标记（§4.2）：
    其余状态一律不判——休市日/盘前的分时停在最近交易日 15:00，只看时钟会一直挂着「延迟」；
    收盘后不判（§4.3：收盘后腾讯、新浪的时间戳跟着请求时刻走，已说明不了新旧）。
    第二道闸是时钟：只在连续竞价段（9:31–11:30、13:01–14:57）里判。

    分时最后一点优先，拿不到才看行情时间戳。方向：数据落后于现在。
    `last_point` 是分时最后一点的**完整日期时刻**（调用方用 `datetime.combine(minute.trade_date,
    points[-1][0])` 拼出来）——只传 `time` 会在跨交易日时把昨天的点当成今天的，永远判不出过期。"""
    if state is not State.TRADING or not in_continuous_auction(now.time()):
        return False
    if last_point is not None:
        # 分时点按分钟**结束**时刻标注，所以那一分钟的数据其实新到 (last_point − 1 分钟)
        point = last_point - timedelta(minutes=1)
        return (now - point).total_seconds() > STALE_MINUTE_S
    if quote_ts is not None:
        return (now - quote_ts).total_seconds() > STALE_QUOTE_S
    return False


# ---------------------------------------------------------------- 按状态的页面规则
# 页面只排版（§7）：「交易时段」这组状态各处都要用，判断留在这里，页面不自己列枚举。
_LIVE_STATES = frozenset({State.AUCTION, State.WAIT_OPEN, State.TRADING})


def is_live(state: State) -> bool:
    """集合竞价、待开盘、交易中：行情在动。状态条的 pill 用琥珀（running）。"""
    return state in _LIVE_STATES


def allow_cross_day(state: State) -> bool:
    """§3：交易时段里不显示跨交易日的最近成功值（直接「暂不可用」）；其余状态允许。"""
    return state not in _LIVE_STATES


def stamp_kind(state: State) -> str:
    """状态条上状态文字后面拼哪种戳（§4.2 表格的「状态」列）：
    - ``"date"``：已收盘、休市、盘前、状态未知——显示数据自己的日期（腾讯与新浪的时间戳在
      收盘后会跟着请求时刻走，拿它当时刻是误导）；
    - ``"minute"``：午间休市——显示分时最后一点（11:30）；
    - ``"quote"``：集合竞价、待开盘、交易中——显示行情时间戳（时:分:秒）。"""
    if state is State.LUNCH:
        return "minute"
    if state in _LIVE_STATES:
        return "quote"
    return "date"


def sealed_at_close(state: State, block_day: date | None, today: date) -> bool:
    """涨跌分布 ±11 档的说法（§2 第 4 项）：「收盘封住」还是「封住涨停/跌停」（盘中）。
    已收盘、休市 → 收盘封住；此外块属于更早的交易日（盘前/状态未知时显示的上一交易日的块）
    也是收盘封住——按**数据自己的日期**判，不只看时钟。`today` 由调用方传（本模块不看时钟）。"""
    if state in (State.CLOSED, State.CLOSED_DAY):
        return True
    return block_day is not None and block_day != today


def expected_trade_day(source_date: date | None, calendar: Calendar | None, now_t: time,
                       last_known: date | None, *, today: date) -> date | None:
    """§3「预期交易日」：本轮源日期 → 日历推算 → 上一轮成功的源日期 → None。
    `today` 由调用方传（页面传 market.now().date()）——本模块不看时钟。"""
    if source_date is not None:
        return source_date
    if calendar is not None and calendar.covers(today):
        if calendar.is_trading(today) and now_t >= AUCTION_START:
            return today
        prev = calendar.previous_trading_day(today)
        if prev is not None:
            return prev
        # 日历覆盖今天，但往前找不到任何交易日（例如日历首日就是今天）——
        # 别把 None 当结果吐出去，退到上一轮成功的日期。
    return last_known
