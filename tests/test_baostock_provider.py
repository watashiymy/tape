# tests/test_baostock_provider.py —— 不联网的单元测试（联网用例见 test_baostock_integration.py）
import pytest

from quant.data.baostock_provider import _fetch, to_bs_code


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
