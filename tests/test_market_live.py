import json
import time as time_module
from datetime import date, datetime, time
from pathlib import Path

import pytest

from quant import market
from quant.market import live, sources
from quant.market.sources import FetchError

FIX = Path(__file__).resolve().parent / "fixtures" / "market"
CODES = [c for c, _ in market.INDEXES]
NOW = datetime(2026, 9, 24, 19, 30)
TRADE_DATE = date(2026, 9, 24)


def fixture_http(broken: set[str] = frozenset()):
    """按 URL 片段找夹具；broken 里的片段直接报 FetchError（模拟某个来源挂了）。"""
    table = {
        "qt.gtimg.cn/q=": "tencent_quotes.bin", "hq.sinajs.cn": "sina_quotes.bin",
        "getTopicZDFenBu": "em_fenbu.bin", "getTopicZTPool": "em_ztpool.bin",
        "getTopicZBPool": "em_zbpool.bin", "newfqkline": None,
    }

    def get(url, headers=None):
        for frag in broken:
            if frag in url:
                raise FetchError(f"broken {frag}")
        if "minute/query?code=" in url:
            code = url.rsplit("=", 1)[1]
            return (FIX / f"tencent_minute_{code}.bin").read_bytes()
        if "newfqkline" in url:
            code = url.split("param=")[1].split(",")[0]
            return (FIX / f"tencent_kline_{code}.bin").read_bytes()
        for frag, name in table.items():
            if frag in url and name:
                return (FIX / name).read_bytes()
        raise AssertionError(f"没有这个 URL 的夹具: {url}")
    return get


def _drop_tencent_line(raw: bytes, key: str) -> bytes:
    """从腾讯行情原始字节里去掉某个 `v_<key>="..."` 段（分号分隔），其余段落原样保留。"""
    text = raw.decode("gbk", errors="replace")
    lines = [seg for seg in text.split(";") if f'v_{key}=' not in seg]
    return ";".join(lines).encode("gbk")


def _zt_pool_bytes(tc: int) -> bytes:
    """构造一份「家数 = 明细条数」自洽的东财涨停池响应，tc 可控——用来钉住 RECONCILE_TOLERANCE
    的边界（真实夹具的 tc 要拿去凑一个特定的偏差不好凑，不如直接造一份合法响应）。"""
    pool = [{"lbc": 1} for _ in range(tc)]
    return json.dumps({"rc": 0, "data": {"tc": tc, "pool": pool}}).encode("utf-8")


def test_full_round_uses_primary_sources():
    store = live.LiveStore()
    snap = live.fetch_round(fixture_http(), NOW, calendar_hint=None, store=store,
                            allow_cross_day=True)
    assert snap.quotes.source == "tencent" and not snap.quotes.fallback
    assert snap.trade_date == snap.minutes["sh000001"].trade_date
    assert snap.breadth.source == "eastmoney" and snap.breadth.scope == sources.SCOPE_EM
    assert snap.thermo.value.limit_up == snap.breadth.value.buckets[11]
    assert snap.thermo.value.zt_tc >= 0 and snap.thermo.trade_date == snap.trade_date
    assert snap.errors == []


def test_tencent_down_falls_back_to_sina_for_quotes_only():
    snap = live.fetch_round(fixture_http({"qt.gtimg.cn/q="}), NOW, None, live.LiveStore(), True)
    assert snap.quotes.source == "sina" and not snap.quotes.fallback
    assert snap.breadth.source == "eastmoney"          # 分布不受影响


def test_sina_fallback_is_refused_when_a_name_does_not_match():
    """spec §3 新浪校验里的「名称对上」要真的传进去：把新浪返回里上证指数的名字换掉，兜底必须失败。"""
    def get(url, headers=None):
        if "qt.gtimg.cn/q=" in url:
            raise FetchError("down")
        raw = fixture_http()(url, headers)
        if "hq.sinajs.cn" in url:
            raw = raw.decode("gb18030").replace("上证指数", "深证成指", 1).encode("gb18030")
        return raw
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.quotes is None and any("名称" in e for e in snap.errors)


def test_eastmoney_fenbu_down_falls_back_to_tencent_breadth_and_kills_thermo():
    store = live.LiveStore()
    snap = live.fetch_round(fixture_http({"getTopicZDFenBu"}), NOW, None, store, True)
    assert snap.breadth.source == "tencent" and snap.breadth.scope == sources.SCOPE_TENCENT
    assert snap.thermo is None or snap.thermo.fallback   # 没有上一轮 → None


def test_everything_down_uses_last_good_with_its_own_stamp():
    store = live.LiveStore()
    good = live.fetch_round(fixture_http(), NOW, None, store, True)
    bad = live.fetch_round(fixture_http({"gtimg", "sinajs", "eastmoney"}),
                           NOW.replace(hour=20), None, store, allow_cross_day=True)
    assert bad.quotes.fallback and bad.quotes.value == good.quotes.value
    assert bad.quotes.ts == good.quotes.ts and bad.quotes.trade_date == good.trade_date
    assert bad.thermo.fallback and bad.thermo.value == good.thermo.value


def test_cross_day_last_good_is_refused_in_trading_hours():
    store = live.LiveStore()
    live.fetch_round(fixture_http(), NOW, None, store, True)
    tomorrow = datetime(2026, 9, 25, 10, 0)
    bad = live.fetch_round(fixture_http({"gtimg", "sinajs", "eastmoney"}), tomorrow, None,
                           store, allow_cross_day=False)
    assert bad.quotes is None and bad.breadth is None and bad.thermo is None


def test_pools_are_skipped_without_a_trade_date():
    calls = []

    def get(url, headers=None):
        calls.append(url)
        if "minute/query" in url:
            raise FetchError("no minutes")
        return fixture_http()(url, headers)
    snap = live.fetch_round(get, NOW, calendar_hint=None, store=live.LiveStore(),
                            allow_cross_day=True)
    assert not any("Pool" in u for u in calls), "不知道交易日就不请求池子"
    assert snap.trade_date is None and snap.thermo is None
    assert snap.breadth is None, "分布拿不到日期 → 本轮按失败处理，不许贴行情的日期"


def test_calendar_hint_supplies_the_trade_date_when_minutes_fail():
    def get(url, headers=None):
        if "minute/query" in url:
            raise FetchError("no minutes")
        return fixture_http()(url, headers)
    hint = date(2026, 9, 24)
    snap = live.fetch_round(get, NOW, calendar_hint=hint, store=live.LiveStore(), allow_cross_day=True)
    assert snap.trade_date == hint and snap.thermo is not None


def test_date_reconciliation_rejects_a_stale_distribution():
    """池子今天 tc=0（开盘前），分布还停在昨天（+11 档 51）→ 分布与温度计本轮作废。"""
    def get(url, headers=None):
        if "Pool" in url:
            return b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260925"}}'
        return fixture_http()(url, headers)
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.breadth is None and snap.thermo is None
    assert any("对不上" in e for e in snap.errors)


def test_store_reset_forgets_everything():
    store = live.LiveStore()
    live.fetch_round(fixture_http(), NOW, None, store, True)
    store.reset()
    assert store.get("quotes") is None and store.last_trade_date is None


# ---------------------------------------------------------------- I-1：行情与家数独立解析
def test_tencent_breadth_failure_does_not_discard_valid_quotes():
    """去掉腾讯返回里唯一的 bkqtRank_A_sh 段：家数解析必败，但行情段完好——两者本该独立解析，
    行情不能被家数拖累。分布本身没坏，所以整块 breadth 仍然是 eastmoney。"""
    def get(url, headers=None):
        raw = fixture_http()(url, headers)
        if "qt.gtimg.cn/q=" in url:
            raw = _drop_tencent_line(raw, "bkqtRank_A_sh")
        return raw
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.quotes.source == "tencent" and not snap.quotes.fallback
    assert snap.breadth.source == "eastmoney" and snap.breadth.scope == sources.SCOPE_EM
    assert any("腾讯家数" in e for e in snap.errors)


def test_tencent_breadth_and_distribution_both_broken_gives_no_breadth_but_quotes_still_ok():
    """连分布也坏了：这时候腾讯家数是唯一的兜底，但它自己也解析失败——breadth 只能是 None
    （没有上一轮可用值），行情依然靠独立解析活下来。"""
    def get(url, headers=None):
        if "getTopicZDFenBu" in url:
            raise FetchError("em down")
        raw = fixture_http()(url, headers)
        if "qt.gtimg.cn/q=" in url:
            raw = _drop_tencent_line(raw, "bkqtRank_A_sh")
        return raw
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.quotes.source == "tencent" and not snap.quotes.fallback
    assert snap.breadth is None
    assert any("腾讯家数" in e for e in snap.errors)


# ---------------------------------------------------------------- I-3：整轮统一 6 秒时限
def test_round_deadline_is_shared_across_both_phases(monkeypatch):
    """曾经第 2 段（池子）会拿到一份全新的 ROUND_DEADLINE_S，最坏情况整轮要等 2 倍时限。
    现在时限是整轮共享的：第 1 段几乎耗尽预算后，第 2 段只分到剩余的（至少 MIN_PHASE2_BUDGET_S）。
    用比 MIN_PHASE2_BUDGET_S 更慢的请求，逼第 2 段在缩水后的预算里超时——但整轮仍然远小于
    「两倍时限」，且明确报出「超过整轮时限」。"""
    monkeypatch.setattr(live, "ROUND_DEADLINE_S", 0.9)

    def slow_get(url, headers=None):
        time_module.sleep(0.7)
        return fixture_http()(url, headers)

    t0 = time_module.monotonic()
    snap = live.fetch_round(slow_get, NOW, calendar_hint=None, store=live.LiveStore(),
                            allow_cross_day=True)
    elapsed = time_module.monotonic() - t0
    assert elapsed < 1.5, f"整轮花了 {elapsed:.2f} 秒，超过安全上限"
    assert any("超过整轮时限" in e for e in snap.errors)


# ---------------------------------------------------------------- I-4：钉住真实夹具的实值
def test_full_round_pins_exact_fixture_values():
    """不只是断言「关系」（谁等于谁），把从真实夹具算出来的具体数字钉死，防止今后悄悄改坏了
    还能凑巧维持住相对关系。数值来自本文件旁的 tests/fixtures/market/*.bin，用
    sources.parse_* 独立算过一遍核对（RECORDED.md 记录了录制来源）。"""
    store = live.LiveStore()
    snap = live.fetch_round(fixture_http(), NOW, None, store, True)
    assert snap.thermo.value == live.Thermo(limit_up=52, limit_down=13, zt_tc=52, zb_tc=10, max_lbc=5)
    assert snap.breadth.value.buckets[11] == 52
    assert snap.quotes.value["sh000001"].price == pytest.approx(3888.37)
    assert snap.source_ok is True
    assert snap.sh_status == "close"
    # 家数/温度计块的 ts 跟着行情走，不是取数时刻 now（§3：数据源自带的时刻）
    assert snap.breadth.ts == snap.quotes.ts
    assert snap.thermo.ts == snap.quotes.ts
    assert snap.quotes.ts != NOW


def test_reconciliation_tolerance_boundary_at_5_and_6():
    """涨停池 tc 与分布 +11 档相差 > RECONCILE_TOLERANCE(5) 就判「对不上」；恰好 5 则接受。
    真实分布的 +11 档是 52（见 test_full_round_pins_exact_fixture_values），造两份自洽的
    涨停池响应（tc == 明细条数），一份差 6、一份差 5。"""
    def make_get(zt_tc):
        def get(url, headers=None):
            if "getTopicZTPool" in url:
                return _zt_pool_bytes(zt_tc)
            return fixture_http()(url, headers)
        return get

    rejected = live.fetch_round(make_get(58), NOW, None, live.LiveStore(), True)   # 52 + 6
    assert rejected.breadth is None and rejected.thermo is None
    assert any("对不上" in e for e in rejected.errors)

    accepted = live.fetch_round(make_get(57), NOW, None, live.LiveStore(), True)   # 52 + 5
    assert accepted.breadth is not None and accepted.thermo is not None
    assert accepted.thermo.value.zt_tc == 57


def test_calendar_hint_beats_stores_last_trade_date_for_pool_requests():
    """预期交易日的顺序是「本轮分时 > 日历推算（这里体现为调用方传入的 calendar_hint）> 上一轮
    成功的日期」——store.last_trade_date 排在 calendar_hint 之后，分时全失败时不能反过来用。"""
    store = live.LiveStore()
    live.fetch_round(fixture_http(), NOW, None, store, True)
    assert store.last_trade_date == TRADE_DATE

    hint = date(2026, 9, 20)
    calls = []

    def get(url, headers=None):
        calls.append(url)
        if "minute/query" in url:
            raise FetchError("no minutes")
        return fixture_http()(url, headers)

    snap = live.fetch_round(get, NOW, calendar_hint=hint, store=store, allow_cross_day=True)
    assert snap.trade_date == hint
    pool_urls = [u for u in calls if "Pool" in u]
    assert pool_urls and all(f"date={hint:%Y%m%d}" in u for u in pool_urls)
    assert not any(f"date={TRADE_DATE:%Y%m%d}" in u for u in pool_urls)


def test_inconsistent_minute_dates_clears_minutes_and_marks_source_not_ok():
    """6 次分时里有一次日期跟其余的对不上：不能选多数派将就，整批分时都得当失败处理。"""
    def get(url, headers=None):
        raw = fixture_http()(url, headers)
        if "minute/query?code=sh000001" in url:
            doc = json.loads(raw)
            doc["data"]["sh000001"]["data"]["date"] = "20260923"
            raw = json.dumps(doc).encode("utf-8")
        return raw
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.minutes == {}
    assert snap.source_ok is False
    assert any("不一致" in e for e in snap.errors)


def test_store_last_trade_date_written_only_after_a_successful_round():
    store = live.LiveStore()
    assert store.last_trade_date is None
    live.fetch_round(fixture_http(), NOW, None, store, True)
    assert store.last_trade_date == TRADE_DATE
