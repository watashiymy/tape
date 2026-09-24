from datetime import datetime, time

from quant import market


def test_package_constants():
    assert market.BREADTH_READY == time(17, 30)          # 暂定值，Task 26 按实测改
    assert [c for c, _ in market.INDEXES] == [
        "sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852"]
    assert isinstance(market.now(), datetime)


import json
from pathlib import Path

import pytest

from quant.market import sources
from quant.market.sources import FetchError, ParseError

FIX = Path(__file__).resolve().parent / "fixtures" / "market"
CODES = [c for c, _ in market.INDEXES]


def _fix(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_tencent_quote_url_batches_all_codes_and_breadth():
    url = sources.tencent_quote_url(CODES)
    assert url.startswith("https://qt.gtimg.cn/q=")
    assert all(c in url for c in CODES) and "bkqtRank_A_sh,bkqtRank_A_sz" in url


def test_parse_tencent_quotes_from_recording():
    quotes = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)
    assert set(quotes) == set(CODES)
    q = quotes["sh000001"]
    assert q.name == "上证指数" and q.price > 0 and q.prev_close > 0
    assert abs(q.change - (q.price - q.prev_close)) <= 0.011
    assert q.ts.year == 2026 and q.amount_wan > 0 and q.source == "tencent"


def test_parse_tencent_breadth_from_recording():
    b = sources.parse_tencent_breadth(_fix("tencent_quotes.bin"))
    assert b.up + b.flat + b.down == b.total and 4000 <= b.total <= 7000
    assert b.scope == sources.SCOPE_TENCENT


def _mutate(raw: bytes, code: str, idx: int, value: str) -> bytes:
    """把某只指数第 idx 段改成 value（腾讯 GBK 文本），做变异测试用。"""
    text = raw.decode("gbk")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"v_{code}="):
            head, body = line.split('="', 1)
            fields = body.rstrip('";').split("~")
            fields[idx] = value
            lines[i] = head + '="' + "~".join(fields) + '";'
    return "\n".join(lines).encode("gbk")


def test_tencent_change_must_match_price_minus_prev_close():
    raw = _mutate(_fix("tencent_quotes.bin"), "sh000001", 31, "9.99")
    with pytest.raises(ParseError, match="涨跌额"):
        sources.parse_tencent_quotes(raw, CODES)


def test_tencent_pct_must_match_change_over_prev_close():
    raw = _mutate(_fix("tencent_quotes.bin"), "sz399001", 32, "7.77")
    with pytest.raises(ParseError, match="涨跌幅"):
        sources.parse_tencent_quotes(raw, CODES)


def test_tencent_missing_segment_is_an_error():
    """批量请求里写错的代码整段不出现——不是 v_pv_none_match。"""
    text = _fix("tencent_quotes.bin").decode("gbk")
    text = "\n".join(l for l in text.splitlines() if not l.startswith("v_sh000688="))
    with pytest.raises(ParseError, match="sh000688"):
        sources.parse_tencent_quotes(text.encode("gbk"), CODES)


def test_tencent_none_match_marker_is_an_error():
    raw = b'v_pv_none_match="1";\n'
    with pytest.raises(ParseError):
        sources.parse_tencent_quotes(raw, ["sh000001"])


def test_tencent_too_few_fields_is_an_error():
    raw = b'v_sh000001="1~x~000001~3.0";\n'
    with pytest.raises(ParseError, match="段"):
        sources.parse_tencent_quotes(raw, ["sh000001"])


def test_tencent_field_shift_by_one_is_caught():
    """字段下标整体错一位（例如把 [30] 当时间戳去解析 [31]）——靠算术关系拦住。"""
    text = _fix("tencent_quotes.bin").decode("gbk")
    shifted = "\n".join(
        (lambda h, b: h + '="' + "~".join(["0"] + b.rstrip('";').split("~")) + '";')(*l.split('="', 1))
        if l.startswith("v_s") else l for l in text.splitlines())
    with pytest.raises(ParseError):
        sources.parse_tencent_quotes(shifted.encode("gbk"), CODES)


def test_default_http_get_wraps_requests_errors(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("x")
    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(FetchError):
        sources.default_http_get("https://example.invalid/")
