"""信号池校验规则（v0.2.2 §3.3）：6 位数字 / 在扫描池内 / 去重保序 / 至少留 1 只。

每条规则背后都是一次真实的静默失败：八进制代码、ST 或次新股让回测口径失真、
空池子让两个入口报出误导性错误。规则本身是纯函数，面板只负责把结果画出来。
"""
import pandas as pd
import pytest

from quant.universe import (add_symbol, normalize_universe, pool_symbols,
                            remove_symbol, validate_symbol)

POOL = {"600519", "000333", "600036", "601318"}


# ---------------------------------------------------------------- 代码格式

@pytest.mark.parametrize("ok", ["600519", "000333", "000001", "688981"])
def test_validate_symbol_accepts_six_digits(ok):
    assert validate_symbol(ok) == ok


@pytest.mark.parametrize("bad", ["60051", "6005190", "60051a", "6005 9", "", "  600519",
                                 "600519 ", "sh.600519"])
def test_validate_symbol_rejects_bad_format(bad):
    with pytest.raises(ValueError, match="6 位数字"):
        validate_symbol(bad)


@pytest.mark.parametrize("bad", [600519, 219, None, 600519.0, ["600519"]])
def test_validate_symbol_rejects_non_str(bad):
    """整数一律拒绝，**不做 zfill 补零**：YAML 把 000333 读成八进制 219 时，
    补零会得到 '000219'——一个格式合法、看着像模像样、其实完全错误的代码。
    这种猜测正是要杜绝的静默失败。"""
    with pytest.raises(TypeError, match="字符串"):
        validate_symbol(bad)


# ---------------------------------------------------------------- 去重与顺序

def test_normalize_universe_dedupes_keeping_first_occurrence():
    assert normalize_universe(["600519", "000333", "600519", "600036"]) == (
        "600519", "000333", "600036")


def test_normalize_universe_preserves_order_not_sorted():
    """稳定顺序 = 用户看到的顺序，不是字典序：排序会让每次改动的 diff 跳来跳去。"""
    assert normalize_universe(["601318", "000333", "600036"]) == (
        "601318", "000333", "600036")


def test_normalize_universe_rejects_empty():
    with pytest.raises(ValueError, match="不能为空"):
        normalize_universe([])


def test_normalize_universe_rejects_all_duplicates_of_bad_symbol():
    with pytest.raises(ValueError, match="6 位数字"):
        normalize_universe(["600519", "abc"])


def test_normalize_universe_accepts_tuple_and_generator():
    assert normalize_universe(("600519",)) == ("600519",)
    assert normalize_universe(s for s in ["600519", "000333"]) == ("600519", "000333")


# ---------------------------------------------------------------- 加入

def test_add_symbol_appends_to_end():
    assert add_symbol(["600519", "000333"], "600036", POOL) == (
        "600519", "000333", "600036")


def test_add_symbol_is_idempotent():
    """面板上重复点"加入"（或从两个页面各加一次）不该报错，也不该加出两条。"""
    cur = ("600519", "000333")
    assert add_symbol(cur, "600519", POOL) == cur


def test_add_symbol_rejects_symbol_outside_scan_pool():
    """扫描池 = 主板、非 ST、上市满 400 天。引擎按主板 ±10% 建模，
    ST/次新股会让回测口径失真——这些票不能进池。"""
    with pytest.raises(ValueError, match="扫描池"):
        add_symbol(["600519"], "300750", POOL)


def test_add_symbol_rejects_bad_format_before_pool_check():
    with pytest.raises(ValueError, match="6 位数字"):
        add_symbol(["600519"], "60051", POOL)


def test_add_symbol_accepts_pool_as_list_or_series():
    """扫描池来自 get_all_symbols 的 DataFrame，调用方给什么容器都得认。"""
    assert add_symbol(["600519"], "000333", ["000333", "600519"])[-1] == "000333"
    df = pd.DataFrame({"symbol": ["000333", "600519"], "name": ["美的集团", "贵州茅台"]})
    assert add_symbol(["600519"], "000333", df["symbol"])[-1] == "000333"


def test_add_symbol_normalizes_existing_universe():
    """池子是从 YAML 读来的，可能早就带着重复项；加一只顺手把重复清掉。"""
    assert add_symbol(["600519", "600519"], "000333", POOL) == ("600519", "000333")


def test_add_symbol_does_not_mutate_input():
    cur = ["600519"]
    add_symbol(cur, "000333", POOL)
    assert cur == ["600519"]


def test_pool_symbols_from_dataframe():
    """get_all_symbols 返回 DataFrame[symbol, name]；转成集合供成员判定。"""
    df = pd.DataFrame({"symbol": ["600519", "000333"], "name": ["贵州茅台", "美的集团"]})
    assert pool_symbols(df) == {"600519", "000333"}


def test_pool_symbols_rejects_frame_without_symbol_column():
    with pytest.raises(ValueError, match="symbol"):
        pool_symbols(pd.DataFrame({"code": ["600519"]}))


# ---------------------------------------------------------------- 移除

def test_remove_symbol():
    assert remove_symbol(["600519", "000333", "600036"], "000333") == (
        "600519", "600036")


def test_remove_last_symbol_rejected():
    """空 universe 会让 run_daily_signal 打印误导性的"全部标的数据均未更新"退出、
    run_backtest 在 equal_weight_hold 深处抛 No objects to concatenate（v0.1.1 修过）。"""
    with pytest.raises(ValueError, match="至少"):
        remove_symbol(["600519"], "600519")


def test_remove_absent_symbol_raises():
    """移除一只不在池里的票多半意味着界面与文件已经不同步，必须响亮失败。"""
    with pytest.raises(ValueError, match="不在信号池"):
        remove_symbol(["600519", "000333"], "600036")


def test_remove_symbol_does_not_need_scan_pool():
    """离线（扫描池拉不到）时仍然要能移除：移除不依赖任何联网数据。"""
    assert remove_symbol(["600519", "000333"], "600519") == ("000333",)


def test_remove_symbol_does_not_mutate_input():
    cur = ["600519", "000333"]
    remove_symbol(cur, "600519")
    assert cur == ["600519", "000333"]
