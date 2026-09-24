from datetime import date, datetime, time
from pathlib import Path

import pytest

from quant import market
from quant.market import live, sources
from quant.market.sources import FetchError

FIX = Path(__file__).resolve().parent / "fixtures" / "market"
CODES = [c for c, _ in market.INDEXES]
NOW = datetime(2026, 9, 24, 19, 30)


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
