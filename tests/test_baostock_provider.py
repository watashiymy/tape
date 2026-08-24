# tests/test_baostock_provider.py —— 不联网的单元测试（联网用例见 test_baostock_integration.py）
from datetime import date

import pandas as pd
import pytest

from quant.data.baostock_provider import (_factors_to_series, _fetch,
                                          _filter_scan_universe, to_bs_code)


class FakeResultSet:
    """模拟 baostock 的 ResultData。

    关键行为（照抄 baostock/data/resultset.py 的 next()）：翻页请求失败时**不抛异常**，
    只是把服务端返回的错误码写进 self.error_code 然后 return False——与"数据读完了"
    在调用方看来完全一样。
    """
    fields = ["date", "close"]

    def __init__(self, rows, fail_after=None):
        self._rows = rows
        self._i = 0
        self._fail_after = fail_after
        self.error_code = "0"
        self.error_msg = ""

    def next(self):
        if self._fail_after is not None and self._i == self._fail_after:
            self.error_code = "10002"
            self.error_msg = "网络接收错误"
            return False
        return self._i < len(self._rows)

    def get_row_data(self):
        row = self._rows[self._i]
        self._i += 1
        return row


def test_to_bs_code():
    assert to_bs_code("600519") == "sh.600519"
    assert to_bs_code("000333") == "sz.000333"
    assert to_bs_code("300750") == "sz.300750"


def test_fetch_reads_all_rows():
    df = _fetch(FakeResultSet([["2024-01-02", "10"], ["2024-01-03", "11"]]))
    assert list(df["close"]) == ["10", "11"]


def test_fetch_raises_when_first_page_failed():
    rs = FakeResultSet([])
    rs.error_code, rs.error_msg = "10001", "登录失效"
    with pytest.raises(RuntimeError, match="10001"):
        _fetch(rs)


def test_fetch_raises_when_pagination_fails_midway():
    """翻到第二页时服务端报错：修复前循环"正常"结束，半截数据被当成完整历史返回，
    回测于是在残缺行情上跑完且无任何异常。"""
    rows = [["2024-01-02", "10"], ["2024-01-03", "11"], ["2024-01-04", "12"]]
    with pytest.raises(RuntimeError, match="10002"):
        _fetch(FakeResultSet(rows, fail_after=2))


def test_factors_to_series_empty_table_supports_ffill_reindex():
    """无除权记录的标的（新股）因子表为空。兜底若返回默认 RangeIndex 的空 Series，
    get_daily_bars 里 reindex(DatetimeIndex, method='ffill') 直接
    TypeError: Cannot compare dtypes int64 and datetime64[us]——
    兜底本想让 fillna(1.0) 全填 1，实际那条路径永远走不到。"""
    s = _factors_to_series(pd.DataFrame(columns=["dividOperateDate", "backAdjustFactor"]))
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    out = s.reindex(idx, method="ffill")        # 修复前在这里就 TypeError
    assert out.isna().all()
    assert out.fillna(1.0).tolist() == [1.0, 1.0]


def test_factors_to_series_converts_types_and_sorts():
    """非空路径钉住：字符串日期/因子 → DatetimeIndex + float，且按日期升序（ffill 的前提）。"""
    fac = pd.DataFrame({"dividOperateDate": ["2024-06-14", "2023-06-30"],
                        "backAdjustFactor": ["1.30", "1.20"]})
    s = _factors_to_series(fac)
    assert list(s.index) == [pd.Timestamp("2023-06-30"), pd.Timestamp("2024-06-14")]
    assert s.tolist() == [1.20, 1.30]
    assert s.dtype == "float64"


# ---------- _filter_scan_universe（v0.1.1 扫描池过滤，纯函数离线测）----------
# 字段名照抄真实接口（2026-08-24 探针实测）：
#   query_all_stock  → code / tradeStatus / code_name
#   query_stock_basic → code / code_name / ipoDate / outDate / type / status

AS_OF = date(2026, 8, 21)
OLD_IPO = "2001-08-27"  # 距 as_of 远超 400 自然日


def _all_df(*rows):
    """rows: (code, name[, tradeStatus])"""
    return pd.DataFrame(
        [{"code": c, "tradeStatus": (r[2] if len(r) > 2 else "1"), "code_name": n}
         for r in rows for c, n in [(r[0], r[1])]],
        columns=["code", "tradeStatus", "code_name"])


def _basic_df(*rows):
    """rows: (code, name[, ipoDate, type, status])"""
    return pd.DataFrame(
        [{"code": r[0], "code_name": r[1],
          "ipoDate": (r[2] if len(r) > 2 else OLD_IPO), "outDate": "",
          "type": (r[3] if len(r) > 3 else "1"),
          "status": (r[4] if len(r) > 4 else "1")}
         for r in rows],
        columns=["code", "code_name", "ipoDate", "outDate", "type", "status"])


def test_scan_universe_keeps_mainboard_and_strips_prefix():
    """主板前缀 sh.60*/sz.00*（含原中小板 002/003）保留；返回 6 位 symbol + name。"""
    out = _filter_scan_universe(
        _all_df(("sh.600519", "贵州茅台"), ("sh.601318", "中国平安"),
                ("sh.603259", "药明康德"), ("sz.000333", "美的集团"),
                ("sz.002594", "比亚迪")),
        _basic_df(("sh.600519", "贵州茅台"), ("sh.601318", "中国平安"),
                  ("sh.603259", "药明康德"), ("sz.000333", "美的集团"),
                  ("sz.002594", "比亚迪")),
        AS_OF)
    assert list(out.columns) == ["symbol", "name"]
    assert sorted(out["symbol"]) == ["000333", "002594", "600519", "601318", "603259"]
    assert set(out["name"]) == {"贵州茅台", "中国平安", "药明康德", "美的集团", "比亚迪"}
    assert (out["symbol"].str.len() == 6).all()


def test_scan_universe_drops_non_mainboard_prefixes():
    """科创板 688 / 创业板 300 / 北交所 bj. 一律剔除。"""
    out = _filter_scan_universe(
        _all_df(("sh.688111", "金山办公"), ("sz.300750", "宁德时代"),
                ("bj.832566", "梓橦宫"), ("sh.600519", "贵州茅台")),
        _basic_df(("sh.688111", "金山办公"), ("sz.300750", "宁德时代"),
                  ("bj.832566", "梓橦宫"), ("sh.600519", "贵州茅台")),
        AS_OF)
    assert list(out["symbol"]) == ["600519"]


def test_scan_universe_drops_indexes():
    """query_all_stock 混有指数（sh.000001 上证综指等）。sh.00 前缀天然出局；
    即便前缀撞上主板（构造 sz.00 的 type=2），type!=1 也必须兜住。"""
    out = _filter_scan_universe(
        _all_df(("sh.000001", "上证综合指数"), ("sz.399001", "深证成指"),
                ("sz.000998", "假想指数"), ("sz.000333", "美的集团")),
        _basic_df(("sh.000001", "上证综合指数", "1991-07-15", "2"),
                  ("sz.399001", "深证成指", "1994-01-03", "2"),
                  ("sz.000998", "假想指数", OLD_IPO, "2"),
                  ("sz.000333", "美的集团")),
        AS_OF)
    assert list(out["symbol"]) == ["000333"]


def test_scan_universe_drops_st_names():
    """名称含 'ST' 剔除（ST/*ST/S*ST 全命中子串）。"""
    out = _filter_scan_universe(
        _all_df(("sh.600876", "ST凯盛"), ("sz.000004", "*ST国华"),
                ("sh.600606", "S*ST绿庭"), ("sh.600519", "贵州茅台")),
        _basic_df(("sh.600876", "ST凯盛"), ("sz.000004", "*ST国华"),
                  ("sh.600606", "S*ST绿庭"), ("sh.600519", "贵州茅台")),
        AS_OF)
    assert list(out["symbol"]) == ["600519"]


def test_scan_universe_drops_young_ipo_with_400d_boundary():
    """上市距 as_of 不足 400 自然日剔除；恰好 400 天保留（"不足"是严格小于）。
    as_of=2026-08-21：2025-07-17 恰 400 天留，2025-07-18 是 399 天剔。"""
    out = _filter_scan_universe(
        _all_df(("sh.605599", "恰好四百"), ("sz.001999", "新股次新"),
                ("sh.600519", "贵州茅台")),
        _basic_df(("sh.605599", "恰好四百", "2025-07-17"),
                  ("sz.001999", "新股次新", "2025-07-18"),
                  ("sh.600519", "贵州茅台")),
        AS_OF)
    assert sorted(out["symbol"]) == ["600519", "605599"]


def test_scan_universe_drops_delisted_and_missing_basic():
    """status!=1（退市）剔除；只出现在 all_df 而 basic 缺失的 code 走交集剔除，
    缺 ipoDate 的行不能因 NaT 比较静默放行。"""
    out = _filter_scan_universe(
        _all_df(("sh.600001", "邯郸钢铁"), ("sh.600002", "齐鲁石化"),
                ("sh.600519", "贵州茅台")),
        _basic_df(("sh.600001", "邯郸钢铁", OLD_IPO, "1", "0"),
                  ("sh.600519", "贵州茅台")),
        AS_OF)
    assert list(out["symbol"]) == ["600519"]


def test_scan_universe_output_sorted_and_reindexed():
    """输出按 symbol 升序、RangeIndex——扫描主循环/CSV 依赖稳定顺序。"""
    out = _filter_scan_universe(
        _all_df(("sh.600519", "贵州茅台"), ("sz.000333", "美的集团"),
                ("sh.600036", "招商银行")),
        _basic_df(("sh.600519", "贵州茅台"), ("sz.000333", "美的集团"),
                  ("sh.600036", "招商银行")),
        AS_OF)
    assert list(out["symbol"]) == ["000333", "600036", "600519"]
    assert list(out.index) == [0, 1, 2]
