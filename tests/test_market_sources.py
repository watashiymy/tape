import json
from datetime import date as _date, datetime, time
from pathlib import Path

import pytest

from quant import market
from quant.market import sources
from quant.market.sources import FetchError, ParseError


def test_package_constants():
    assert market.BREADTH_READY == time(17, 30)          # 暂定值，Task 26 按实测改
    assert [c for c, _ in market.INDEXES] == [
        "sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852"]
    assert isinstance(market.now(), datetime)


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
    with pytest.raises(ParseError, match="涨跌额|涨跌幅|高低价|段"):
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
    with pytest.raises(ParseError, match="为负"):
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


def test_fetch_returns_ok_result():
    def fake(url, headers=None):
        assert "qt.gtimg.cn" in url
        return _fix("tencent_quotes.bin")
    r = sources.fetch(fake, sources.tencent_quote_url(CODES),
                      lambda raw: sources.parse_tencent_quotes(raw, CODES))
    assert r.ok and r.value["sh000001"].price > 0 and r.error is None


def test_fetch_turns_fetch_and_parse_errors_into_failed_results():
    def dead(url, headers=None):
        raise FetchError("timeout")
    r = sources.fetch(dead, "x", lambda raw: raw)
    assert not r.ok and "timeout" in r.error
    r2 = sources.fetch(lambda u, h=None: b"garbage", sources.em_fenbu_url(), sources.parse_em_fenbu)
    assert not r2.ok and r2.value is None


def test_fetch_logs_a_warning_on_unexpected_exception(caplog):
    def boom_parse(raw):
        raise TypeError("boom")
    with caplog.at_level("WARNING"):
        r = sources.fetch(lambda u, h=None: b"x", "http://x", boom_parse)
    assert not r.ok and "TypeError" in r.error
    assert any("boom" in rec.message or "TypeError" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------- I-1: em pool 缺键不当空池
def test_em_pool_missing_tc_is_rejected():
    with pytest.raises(ParseError, match="tc"):
        sources.parse_em_pool(b'{"rc":0,"data":{}}', "zt", _date(2026, 9, 24))


def test_em_pool_renamed_keys_are_rejected():
    with pytest.raises(ParseError, match="tc"):
        sources.parse_em_pool(b'{"rc":0,"data":{"total":52,"list":[]}}', "zt", _date(2026, 9, 24))


def test_em_pool_tc_zero_without_pool_key_is_still_empty():
    p = sources.parse_em_pool(b'{"rc":0,"data":{"tc":0}}', "zt", _date(2026, 9, 24))
    assert p.tc == 0 and p.rows == 0 and p.max_lbc is None


# ---------------------------------------------------------------- I-2: 新浪「名称对上」
def test_sina_name_matches_when_names_given():
    quotes = sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, None,
                                       names=dict(market.INDEXES))
    assert set(quotes) == set(CODES)


def test_sina_name_mismatch_is_rejected():
    text = _fix("sina_quotes.bin").decode("gb18030")
    assert text.count("上证指数") == 1
    text = text.replace('"上证指数,', '"深证成指,', 1)
    with pytest.raises(ParseError, match="名称"):
        sources.parse_sina_quotes(text.encode("gb18030"), CODES, None, names=dict(market.INDEXES))


def test_sina_name_check_is_skipped_when_names_is_none():
    quotes = sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, None)
    assert set(quotes) == set(CODES)


# ---------------------------------------------------------------- I-3: 解析器只抛 ParseError
def test_minute_row_with_colon_time_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["09:30 1.0"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError):
        sources.parse_tencent_minute(raw, "sh000001")


def test_minute_row_with_non_numeric_price_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 abc"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError):
        sources.parse_tencent_minute(raw, "sh000001")


def test_minute_row_with_invalid_hour_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["2530 1.0"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError):
        sources.parse_tencent_minute(raw, "sh000001")


def test_minute_rows_not_a_list_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":5},'
           b'"qt":{"market":[]}}}}')
    with pytest.raises(ParseError):
        sources.parse_tencent_minute(raw, "sh000001")


def test_json_top_level_non_dict_is_rejected():
    with pytest.raises(ParseError):
        sources._json(b"[]", "test")


def test_fenbu_that_is_a_dict_instead_of_a_list_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = {"oops": 1}
    with pytest.raises(ParseError):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_fenbu_element_that_is_a_list_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [[1, 2, 3]]
    with pytest.raises(ParseError):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_pool_tc_non_integer_is_rejected():
    doc = json.loads(_fix("em_ztpool.bin"))
    doc["data"]["tc"] = "abc"
    with pytest.raises(ParseError):
        sources.parse_em_pool(json.dumps(doc).encode(), "zt", _date(2026, 9, 24))


def test_pool_row_that_is_not_a_dict_is_rejected():
    doc = json.loads(_fix("em_ztpool.bin"))
    doc["data"]["pool"] = doc["data"]["pool"] + ["oops"]
    doc["data"]["tc"] = len(doc["data"]["pool"])
    with pytest.raises(ParseError):
        sources.parse_em_pool(json.dumps(doc).encode(), "zt", _date(2026, 9, 24))


# ---------------------------------------------------------------- I-4: 钉住字段来源 / 禁用 qdate
def test_breadth_exact_counts_from_recording():
    b = sources.parse_tencent_breadth(_fix("tencent_quotes.bin"))
    assert (b.up, b.flat, b.down, b.total) == (1084, 137, 4001, 5222)


def test_tencent_amount_exact_value_from_recording():
    q = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)
    assert q["sh000001"].amount_wan == pytest.approx(78361300.0)


def test_sina_amount_matches_tencent_amount_within_tolerance():
    tencent = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"]
    sina = sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, None)["sh000001"]
    assert abs(sina.amount_wan - tencent.amount_wan) < 1


def test_kline_last_bar_amount_pins_the_tencent_quote_field_37():
    bars = sources.parse_tencent_kline(_fix("tencent_kline_sh000001.bin"), "sh000001")
    tencent = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"]
    assert bars[-1].amount_wan == pytest.approx(78361300.14)
    assert abs(bars[-1].amount_wan - tencent.amount_wan) < 1


def test_pool_trade_date_is_the_requested_date_not_qdate():
    """夹具的 qdate 是 20260924；传入 20260923 之后结果必须是 20260923——钉住「qdate 不可信，不用」。"""
    zt = sources.parse_em_pool(_fix("em_ztpool.bin"), "zt", _date(2026, 9, 23))
    assert zt.trade_date == _date(2026, 9, 23)


def test_minute_trade_date_comes_only_from_data_date_not_qt_market():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260926","data":'
           b'["0930 1.0"]},"qt":{"market":["2026-09-27 10:00:00|SH_open_x"]}}}}')
    m = sources.parse_tencent_minute(raw, "sh000001")
    assert m.trade_date == _date(2026, 9, 26)


def test_minute_from_recording_exact_trade_date_and_status():
    m = sources.parse_tencent_minute(_fix("tencent_minute_sh000001.bin"), "sh000001")
    assert m.trade_date == _date(2026, 9, 24) and m.sh_status == "close"


# ---------------------------------------------------------------- I-5: 校验规则的反例
def test_breadth_sum_mismatch_is_rejected():
    raw = _mutate(_fix("tencent_quotes.bin"), "bkqtRank_A_sh", 5, "999999")
    with pytest.raises(ParseError, match="≠"):
        sources.parse_tencent_breadth(raw)


def test_breadth_total_out_of_range_is_rejected():
    raw = _fix("tencent_quotes.bin")
    for key in ("bkqtRank_A_sh", "bkqtRank_A_sz"):
        raw = _mutate(raw, key, 2, "1")
        raw = _mutate(raw, key, 3, "0")
        raw = _mutate(raw, key, 4, "0")
        raw = _mutate(raw, key, 5, "1")
    with pytest.raises(ParseError, match="合理范围"):
        sources.parse_tencent_breadth(raw)


def test_fenbu_total_out_of_range_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [{k: max(0, v // 100) for k, v in d.items()}
                            for d in doc["data"]["fenbu"]]
    with pytest.raises(ParseError, match="合理范围"):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_pool_lbc_less_than_one_is_rejected():
    doc = json.loads(_fix("em_ztpool.bin"))
    doc["data"]["pool"][0]["lbc"] = 0
    with pytest.raises(ParseError, match="lbc"):
        sources.parse_em_pool(json.dumps(doc).encode(), "zt", _date(2026, 9, 24))


def test_kline_non_monotonic_dates_is_rejected():
    doc = json.loads(_fix("tencent_kline_sh000001.bin"))
    rows = doc["data"]["sh000001"]["day"]
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ParseError, match="单调"):
        sources.parse_tencent_kline(json.dumps(doc).encode(), "sh000001")


def test_minute_non_positive_price_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 0"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError, match="非正"):
        sources.parse_tencent_minute(raw, "sh000001")


# ---------------------------------------------------------------- Minor
def test_pool_unknown_kind_raises_value_error_not_parse_error():
    with pytest.raises(ValueError) as exc:
        sources.parse_em_pool(b"{}", "xx", _date(2026, 9, 24))
    assert not isinstance(exc.value, ParseError)


def test_fenbu_duplicate_bucket_key_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    fenbu = doc["data"]["fenbu"]
    dup_entry = next(d for d in fenbu if "3" in d)
    doc["data"]["fenbu"] = fenbu + [dict(dup_entry)]
    with pytest.raises(ParseError):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_result_forbids_both_value_and_error_being_none():
    with pytest.raises(ValueError):
        sources.Result(value=None, error=None)


def test_result_ok_true_only_when_value_present_and_no_error():
    assert sources.Result(value=1).ok
    assert not sources.Result(value=None, error="x").ok
    assert not sources.Result(value=1, error="x").ok
