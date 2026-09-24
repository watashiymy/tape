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


def test_sina_headers_carry_the_referer():
    assert sources.SINA_HEADERS["Referer"].startswith("https://finance.sina.com.cn")


def test_parse_sina_quotes_from_recording():
    expected_day = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"].ts.date()
    quotes = sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, expected_day)
    assert set(quotes) == set(CODES)
    q = quotes["sh000001"]
    assert q.source == "sina" and q.price > 0
    assert abs(q.change - (q.price - q.prev_close)) < 1e-6      # 自己算的，恒等
    assert q.ts is not None and q.ts.date() == expected_day
    t = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"]
    assert abs(q.price - t.price) < 0.02                       # 两家同一时刻数值一致（实测）


def test_sina_wrong_date_is_rejected():
    from datetime import date
    with pytest.raises(ParseError, match="日期"):
        sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, date(2000, 1, 1))


def test_sina_empty_segment_is_rejected():
    raw = b'var hq_str_sh000001="";\n'
    with pytest.raises(ParseError, match="sh000001"):
        sources.parse_sina_quotes(raw, ["sh000001"], None)


def test_sina_bad_status_code_is_rejected():
    text = _fix("sina_quotes.bin").decode("gb18030")
    text = text.replace(",00,", ",01,")
    with pytest.raises(ParseError, match="状态"):
        sources.parse_sina_quotes(text.encode("gb18030"), CODES, None)


from datetime import date as _date


def test_parse_tencent_minute_from_recording():
    m = sources.parse_tencent_minute(_fix("tencent_minute_sh000001.bin"), "sh000001")
    assert m.code == "sh000001" and isinstance(m.trade_date, _date)
    assert 2 <= len(m.points) <= 242
    times = [t for t, _ in m.points]
    assert times == sorted(times), "分时点时间必须单调"
    assert all(p > 0 for _, p in m.points)
    assert m.sh_status in {"open", "close", None}


def test_minute_status_string_is_parsed_from_the_sh_segment():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 1.0 1 1","0931 1.1 2 2"]},"qt":{"market":["2026-09-23 10:00:00|HK_open_x|SH_open_\xe4\xba\xa4\xe6\x98\x93\xe4\xb8\xad|SZ_open_y"]}}}}')
    m = sources.parse_tencent_minute(raw, "sh000001")
    assert m.sh_status == "open" and m.trade_date == _date(2026, 9, 23)
    assert m.points[-1] == (time(9, 31), 1.1)


def test_minute_unknown_status_falls_back_to_none():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 1.0 1 1"]},"qt":{"market":["2026-09-23 10:00:00|SH_lunch_x"]}}}}')
    assert sources.parse_tencent_minute(raw, "sh000001").sh_status is None


def test_minute_non_monotonic_time_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0931 1.0 1 1","0930 1.1 2 2"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError, match="单调"):
        sources.parse_tencent_minute(raw, "sh000001")


def test_parse_tencent_kline_from_recording():
    bars = sources.parse_tencent_kline(_fix("tencent_kline_sh000001.bin"), "sh000001")
    assert len(bars) >= 2 and bars[-1].day > bars[-2].day
    assert all(b.amount_wan > 0 for b in bars)


def test_kline_param_error_has_code_zero_but_is_an_error():
    raw = b'{"code":0,"msg":"param error","data":[]}'
    with pytest.raises(ParseError, match="param error"):
        sources.parse_tencent_kline(raw, "sh000001")


def test_parse_em_fenbu_from_recording():
    dist = sources.parse_em_fenbu(_fix("em_fenbu.bin"))
    assert sorted(dist.buckets) == list(range(-11, 12))
    assert dist.total == sum(dist.buckets.values()) and 4000 <= dist.total <= 7000
    assert dist.up == sum(v for k, v in dist.buckets.items() if k > 0)
    assert dist.flat == dist.buckets[0] and dist.down + dist.up + dist.flat == dist.total
    assert dist.scope == sources.SCOPE_EM


def test_fenbu_missing_bucket_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [d for d in doc["data"]["fenbu"] if "7" not in d]
    with pytest.raises(ParseError, match="档"):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_fenbu_negative_count_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [({"3": -1} if "3" in d else d) for d in doc["data"]["fenbu"]]
    with pytest.raises(ParseError):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_em_pool_url_carries_ut_date_and_a_large_pagesize():
    url = sources.em_pool_url("zt", _date(2026, 9, 24))
    assert "getTopicZTPool" in url and "date=20260924" in url and "ut=" in url
    assert "pagesize=10000" in url


def test_parse_em_pools_from_recording():
    zt = sources.parse_em_pool(_fix("em_ztpool.bin"), "zt", _date(2026, 9, 24))
    zb = sources.parse_em_pool(_fix("em_zbpool.bin"), "zb", _date(2026, 9, 24))
    assert zt.tc == zt.rows and zt.max_lbc >= 1 and zb.tc == zb.rows
    assert zt.trade_date == _date(2026, 9, 24)


def test_pool_tc_must_equal_row_count():
    doc = json.loads(_fix("em_ztpool.bin"))
    doc["data"]["tc"] += 1
    with pytest.raises(ParseError, match="家数"):
        sources.parse_em_pool(json.dumps(doc).encode(), "zt", _date(2026, 9, 24))


def test_pool_rc_102_is_an_error():
    with pytest.raises(ParseError, match="102"):
        sources.parse_em_pool(b'{"rc":102,"data":null}', "zt", _date(2026, 9, 24))


def test_empty_pool_parses_but_max_lbc_is_none():
    p = sources.parse_em_pool(b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260924"}}',
                              "zt", _date(2026, 9, 24))
    assert p.tc == 0 and p.max_lbc is None
