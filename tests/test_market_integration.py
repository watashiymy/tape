# tests/test_market_integration.py
"""真的去请求腾讯 / 东财 / baostock（`pytest -m network`）。三条，与 test_packaging 的计数对账。"""
import pytest

from quant import market
from quant.market import sources

pytestmark = pytest.mark.network
CODES = [c for c, _ in market.INDEXES]


def test_tencent_quotes_still_parse():
    raw = sources.default_http_get(sources.tencent_quote_url(CODES))
    q = sources.parse_tencent_quotes(raw, CODES)
    assert set(q) == set(CODES) and sources.parse_tencent_breadth(raw).total > 4000


def test_eastmoney_fenbu_still_parses():
    dist = sources.parse_em_fenbu(sources.default_http_get(sources.em_fenbu_url()))
    assert dist.total > 4000 and sorted(dist.buckets) == list(range(-11, 12))


def test_baostock_snapshot_of_a_known_day():
    from datetime import date
    from quant.data.baostock_provider import BaostockProvider
    with BaostockProvider() as p:
        df = p.get_market_snapshot(date(2026, 9, 22))
    assert len(df) == 5222 and df["tradestatus"].sum() == 5209        # 实测值（spec §1）
