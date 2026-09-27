"""一轮盘中取数（spec §3「一轮怎么发」）：两段并发、按块取舍、最近成功值。

块（Block）是页面唯一消费的东西：值 + 来源 + 范围标签 + 数据源时刻 + 交易日 + 是否兜底。
三个块：quotes（指数 + 腾讯家数原始件）、breadth（分布 或 腾讯家数）、thermo（温度计）。
采用顺序：主源本轮 > 兜底源本轮 > 最近成功值 > None（页面显示「暂不可用」）。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from quant import market
from quant.market import sources
from quant.market.sources import HttpGet, Result

ROUND_DEADLINE_S = 6.0       # 整轮（两段合计）最迟收口——不是每段各 6 秒
MIN_PHASE2_BUDGET_S = 0.5    # 第 1 段几乎耗尽预算时，也留给池子这么多，别让它一点机会都没有
RECONCILE_TOLERANCE = 5      # 涨停池 tc 与分布 +11 档允许差几只（两个请求不是同一瞬间）
HTTP_GET: HttpGet = sources.default_http_get     # 测试用替身覆盖这一个名字


@dataclass(frozen=True)
class Block:
    value: Any
    source: str          # "tencent" | "sina" | "eastmoney"
    scope: str           # 范围标签（家数/分布用；行情为空串）
    ts: datetime | None  # 数据源自带的时刻
    trade_date: date | None
    fallback: bool = False


@dataclass(frozen=True)
class Thermo:
    limit_up: int        # 分布 +11 档
    limit_down: int      # 分布 −11 档
    zt_tc: int           # 涨停池家数（只用于炸板率与对账）
    zb_tc: int           # 炸板池家数
    max_lbc: int | None  # 最高连板


@dataclass
class LiveSnapshot:
    fetched_at: datetime
    trade_date: date | None
    source_ok: bool                       # 分时是否至少一只成功
    sh_status: str | None
    minutes: dict[str, sources.Minute] = field(default_factory=dict)
    quotes: Block | None = None
    breadth: Block | None = None
    thermo: Block | None = None
    errors: list[str] = field(default_factory=list)


class LiveStore:
    """进程内的最近成功值。**必须可 reset**：测试之间不许残留（spec §8）。"""

    def __init__(self) -> None:
        self._blocks: dict[str, Block] = {}
        self.last_trade_date: date | None = None

    def put(self, key: str, block: Block) -> None:
        self._blocks[key] = block

    def get(self, key: str) -> Block | None:
        return self._blocks.get(key)

    def reset(self) -> None:
        self._blocks.clear()
        self.last_trade_date = None


STORE = LiveStore()


def _run_all(jobs: dict[str, Any], timeout: float) -> dict[str, Result]:
    """并发跑一组无参 callable，最迟 `timeout` 秒收口；超时的当失败。
    不用 `with`：那会在退出时 shutdown(wait=True) 等完超时的任务，deadline 就成了摆设。"""
    out: dict[str, Result] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(jobs)))
    try:
        futs = {pool.submit(fn): key for key, fn in jobs.items()}
        done, not_done = wait(futs, timeout=timeout)
        for f in done:
            out[futs[f]] = f.result()
        for f in not_done:
            out[futs[f]] = Result(value=None, error="超过整轮时限")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _parse(raw: bytes, parse: Any) -> Result:
    """对已经到手的原始字节复用 `fetch()` 的异常收编逻辑（不再发请求，http_get 直接把 raw 吐回去）。"""
    return sources.fetch(lambda _url, _headers=None: raw, "", parse)


def _pick(current: Block | None, key: str, store: LiveStore, snap: LiveSnapshot,
          allow_cross_day: bool) -> Block | None:
    if current is not None:
        store.put(key, current)
        return current
    last = store.get(key)
    if last is None:
        return None
    # 交易时段里不显示跨交易日的旧值（§3）。比较对象是**今天**，不是 snap.trade_date：
    # 分时全失败时 snap.trade_date 可能就是从 store 抄来的昨天，拿它比永远相等。
    if not allow_cross_day and last.trade_date != snap.fetched_at.date():
        return None
    return Block(last.value, last.source, last.scope, last.ts, last.trade_date, fallback=True)


def without_cross_day(snap: LiveSnapshot, today: date) -> LiveSnapshot:
    """本轮是按「允许跨日」取的，重判后却落在不许跨日的状态（集合竞价/待开盘/交易中）时用：
    返回一份拷贝，把**跨交易日的兜底块**（`fallback` 且交易日 ≠ today）换成 None，其余原样。
    这是同间隔改判（未知 → 交易中都是 30 秒行，不整页重跑）那一跳的纯数据补丁，不重新取数。"""
    def keep(block: Block | None) -> Block | None:
        if block is not None and block.fallback and block.trade_date != today:
            return None
        return block
    return replace(snap, quotes=keep(snap.quotes), breadth=keep(snap.breadth), thermo=keep(snap.thermo))


def fetch_round(http_get: HttpGet, now: datetime, calendar_hint: date | None,
                store: LiveStore, allow_cross_day: bool) -> LiveSnapshot:
    t0 = time.monotonic()          # 整轮（两段合计）的收口时刻从这里算，不是每段各给一份 ROUND_DEADLINE_S
    codes = [c for c, _ in market.INDEXES]
    snap = LiveSnapshot(fetched_at=now, trade_date=None, source_ok=False, sh_status=None)

    # —— 第 1 段：并发
    # 腾讯只取一次原始字节；行情与家数各自独立解析（见下），任一份校验失败都不连累另一份。
    jobs = {
        "tencent": lambda: sources.fetch(http_get, sources.tencent_quote_url(codes), lambda raw: raw),
        "fenbu": lambda: sources.fetch(http_get, sources.em_fenbu_url(), sources.parse_em_fenbu),
    }
    for code in codes:
        jobs[f"min:{code}"] = (lambda c=code: sources.fetch(
            http_get, sources.tencent_minute_url(c), lambda raw: sources.parse_tencent_minute(raw, c)))
    # 新浪与主源同一轮并发发出（多 1 个请求），主源失败才采用；日期校验放到拿到预期交易日之后
    jobs["sina_raw"] = lambda: sources.fetch(http_get, sources.sina_quote_url(codes),
                                             lambda raw: raw, headers=sources.SINA_HEADERS)
    res = _run_all(jobs, ROUND_DEADLINE_S)

    # —— 交易日期：6 次分时里成功的那些必须一致
    dates = set()
    for code in codes:
        r = res[f"min:{code}"]
        if r.ok:
            snap.minutes[code] = r.value
            dates.add(r.value.trade_date)
        else:
            snap.errors.append(f"分时 {code}: {r.error}")
    if len(dates) > 1:
        snap.errors.append(f"分时交易日期不一致: {sorted(dates)}")
        snap.minutes.clear()
        dates.clear()
    snap.source_ok = bool(dates)
    if dates:
        snap.trade_date = dates.pop()
        store.last_trade_date = snap.trade_date
        sh = snap.minutes.get("sh000001")
        snap.sh_status = sh.sh_status if sh else None
    else:
        snap.trade_date = calendar_hint or store.last_trade_date

    # —— 行情块：腾讯的行情与家数各自独立解析（同一份原始字节，两次校验），
    # 一份校验不过不连累另一份——这就是为什么两者不能拼在同一个 lambda 里一起解析。
    quotes_block = None
    tencent_breadth = None
    if res["tencent"].ok:
        raw = res["tencent"].value
        quotes_res = _parse(raw, lambda r: sources.parse_tencent_quotes(r, codes))
        breadth_res = _parse(raw, sources.parse_tencent_breadth)
        if quotes_res.ok:
            quotes = quotes_res.value
            quotes_block = Block(quotes, "tencent", "", quotes["sh000001"].ts, snap.trade_date)
        else:
            snap.errors.append(f"腾讯行情: {quotes_res.error}")
        if breadth_res.ok:
            tencent_breadth = breadth_res.value
        else:
            snap.errors.append(f"腾讯家数: {breadth_res.error}")
    else:
        snap.errors.append(f"腾讯行情: {res['tencent'].error}")

    if quotes_block is None:
        if res["sina_raw"].ok:
            try:
                quotes = sources.parse_sina_quotes(res["sina_raw"].value, codes, snap.trade_date,
                                                   names=dict(market.INDEXES))   # spec §3「名称对上」
                quotes_block = Block(quotes, "sina", "", quotes["sh000001"].ts, snap.trade_date)
            except sources.ParseError as e:
                snap.errors.append(f"新浪行情: {e}")
        else:
            snap.errors.append(f"新浪行情: {res['sina_raw'].error}")
    snap.quotes = _pick(quotes_block, "quotes", store, snap, allow_cross_day)

    # —— 第 2 段：池子（只在知道交易日时）。预算是整轮剩下的，不是重新给一份 ROUND_DEADLINE_S——
    # 否则最坏情况第 1 段吃满 6 秒、第 2 段又吃满 6 秒，整轮变成 12 秒（I-3）。
    zt = zb = None
    if snap.trade_date is not None:
        phase2_budget = max(MIN_PHASE2_BUDGET_S, ROUND_DEADLINE_S - (time.monotonic() - t0))
        pools = _run_all({
            "zt": lambda: sources.fetch(http_get, sources.em_pool_url("zt", snap.trade_date),
                                        lambda raw: sources.parse_em_pool(raw, "zt", snap.trade_date)),
            "zb": lambda: sources.fetch(http_get, sources.em_pool_url("zb", snap.trade_date),
                                        lambda raw: sources.parse_em_pool(raw, "zb", snap.trade_date)),
        }, phase2_budget)
        zt, zb = pools["zt"].value, pools["zb"].value
        for k in ("zt", "zb"):
            if not pools[k].ok:
                snap.errors.append(f"东财池子 {k}: {pools[k].error}")

    # —— 分布块 + 温度计块（同轮、同源、日期对账）
    breadth_block = thermo_block = None
    dist = res["fenbu"].value if res["fenbu"].ok else None
    if dist is None:
        snap.errors.append(f"东财分布: {res['fenbu'].error}")
    if dist is not None and zt is not None and zb is not None:
        bucket11 = dist.buckets[11]
        if (zt.tc == 0 and bucket11 > 0) or abs(zt.tc - bucket11) > RECONCILE_TOLERANCE:
            snap.errors.append(f"分布与池子的日期对不上：涨停池 {zt.tc} vs 分布 +11 档 {bucket11}")
        else:
            stamp = quotes_block.ts if quotes_block else now
            breadth_block = Block(dist, "eastmoney", dist.scope, stamp, snap.trade_date)
            thermo_block = Block(Thermo(limit_up=bucket11, limit_down=dist.buckets[-11],
                                        zt_tc=zt.tc, zb_tc=zb.tc, max_lbc=zt.max_lbc),
                                 "eastmoney", dist.scope, stamp, snap.trade_date)
    if breadth_block is None and dist is None and tencent_breadth is not None:
        # 兜底：只有家数，分布图由页面显示「暂不可用」，范围标签跟着换
        breadth_block = Block(tencent_breadth, "tencent", tencent_breadth.scope,
                              quotes_block.ts if quotes_block else now, snap.trade_date)
    snap.breadth = _pick(breadth_block, "breadth", store, snap, allow_cross_day)
    snap.thermo = _pick(thermo_block, "thermo", store, snap, allow_cross_day)
    return snap
