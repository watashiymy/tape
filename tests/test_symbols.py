# tests/test_symbols.py — 全市场清单的本地落盘（symbol/name + as_of）
#
# 为什么要有这个文件：`provider.get_all_symbols()` 是**每只票的名字**唯一的全量来源
# （约 3000 行，固定 2-4 分钟一次），此前拉完即丢。于是面板上名称列大半空缺
# （扫描 CSV 只记出了信号的标的），而每轮扫描还要重付一次那 2-4 分钟。
#
# 断言的重点是三件事：
#   1. 往返保真 —— 存进去什么、读回来还是什么（含 as_of，它决定要不要重拉）；
#   2. 文件坏了必须**响亮**报错 —— 静默返回 None 会让调用方以为"没缓存"，
#      于是每次都全量重拉：问题被掩盖，用户只知道"怎么又慢了"；
#   3. is_fresh 的边界 —— 它是"这次要不要联网"的唯一开关，错一天就是错 2-4 分钟。
from datetime import date

import pandas as pd
import pytest

from quant.data.symbols import SYMBOLS_PATH, is_fresh, load_symbols, save_symbols


def _listing(rows=(("000333", "美的集团"), ("600519", "贵州茅台"))) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=["symbol", "name"])


# ================================================================ 往返读写

def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "symbols.parquet"
    df = _listing()

    save_symbols(df, date(2026, 8, 26), path)
    loaded, as_of = load_symbols(path)

    assert list(loaded["symbol"]) == ["000333", "600519"]
    assert list(loaded["name"]) == ["美的集团", "贵州茅台"]
    assert as_of == date(2026, 8, 26)


def test_symbol_column_stays_a_string(tmp_path):
    """代码必须原样是 6 位字符串。一旦被当成整数存回来，"000333" 就成了 333，
    与扫描 CSV（dtype=str）、配置文件里的代码全部对不上——而对不上只表现为
    "名称又空了"，不会报任何错。"""
    path = tmp_path / "symbols.parquet"
    save_symbols(_listing([("000333", "美的集团")]), date(2026, 8, 26), path)

    loaded, _ = load_symbols(path)

    assert loaded["symbol"].tolist() == ["000333"]
    assert isinstance(loaded["symbol"].iloc[0], str)


def test_as_of_is_preserved_verbatim_not_replaced_by_today(tmp_path):
    """as_of 是"这份清单是哪天的"，不是"什么时候写的文件"。
    写成 today 的话，一份三个月前搬过来的清单会被判定为新鲜，永远不再刷新。"""
    path = tmp_path / "symbols.parquet"
    save_symbols(_listing(), date(2020, 1, 2), path)

    _, as_of = load_symbols(path)

    assert as_of == date(2020, 1, 2)
    assert as_of != date.today()


def test_save_creates_the_parent_directory(tmp_path):
    """默认落点是 data/symbols.parquet；全新 clone 里 data/ 可能还不存在
    （.gitignore 掉了 data/cache/，目录不进版本库）。"""
    path = tmp_path / "data" / "symbols.parquet"
    save_symbols(_listing(), date(2026, 8, 26), path)
    assert path.exists()


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    """与 BarCache 同一模式（tmp + os.replace）：拉了 2-4 分钟的清单写到一半被
    Ctrl-C，不许留下半截 parquet——那会让下次启动直接撞上"文件损坏"。"""
    path = tmp_path / "symbols.parquet"
    save_symbols(_listing(), date(2026, 8, 26), path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert path.exists()


def test_save_overwrites_the_previous_listing(tmp_path):
    path = tmp_path / "symbols.parquet"
    save_symbols(_listing(), date(2026, 8, 26), path)
    save_symbols(_listing([("601318", "中国平安")]), date(2026, 8, 27), path)

    loaded, as_of = load_symbols(path)

    assert list(loaded["symbol"]) == ["601318"]
    assert as_of == date(2026, 8, 27)


def test_save_rejects_a_frame_without_symbol_and_name(tmp_path):
    """少了 name 列照样能写成一个 parquet，读回来是一份"没有名字的名称表"——
    正是本次要修的那个静默失败的翻版。必须在写盘之前炸。"""
    path = tmp_path / "symbols.parquet"
    with pytest.raises(ValueError, match="name"):
        save_symbols(pd.DataFrame({"symbol": ["600519"]}), date(2026, 8, 26), path)
    assert not path.exists()


# ================================================================ 缺文件 / 坏文件

def test_load_missing_file_returns_none(tmp_path):
    """没有清单文件是**正常**状态（还没跑过扫描）：调用方据此降级到联网拉取。"""
    assert load_symbols(tmp_path / "symbols.parquet") is None


def test_load_corrupt_file_raises_loudly_with_the_path(tmp_path):
    """损坏 parquet 的原生报错只有 '<Buffer>'，不含文件名。
    更要命的是**不能**静默返回 None：那样调用方以为"没缓存"，于是每轮扫描都全量
    重拉 2-4 分钟，一份坏文件可以躺在那里几个月没人发现。"""
    path = tmp_path / "symbols.parquet"
    save_symbols(_listing(), date(2026, 8, 26), path)
    path.write_bytes(path.read_bytes()[:20])            # 截断

    with pytest.raises(RuntimeError) as ei:
        load_symbols(path)
    assert str(path) in str(ei.value), "报错必须带上路径，否则不知道该删哪个文件"
    assert "删除" in str(ei.value), "报错要告诉用户怎么自愈（删掉即可自动重拉）"


def test_load_a_parquet_without_the_as_of_metadata_raises_loudly(tmp_path):
    """别处生成的 parquet（或旧版本写的）没有 as_of。没有它就无从判断新鲜度，
    只能当损坏处理——绝不许猜一个日期出来（猜早了白拉，猜晚了永远不刷新）。"""
    path = tmp_path / "symbols.parquet"
    _listing().to_parquet(path)                          # 裸 parquet，无元信息

    with pytest.raises(RuntimeError) as ei:
        load_symbols(path)
    assert str(path) in str(ei.value)


# ================================================================ is_fresh（纯函数）

@pytest.mark.parametrize("age_days,expected", [
    (0, True),      # 今天拉的
    (1, True),
    (7, True),      # 边界内：清单变动很慢（新股上市/退市），7 天视为新鲜
    (8, False),     # 边界外：该重拉了
    (365, False),
])
def test_is_fresh_boundaries(age_days, expected):
    today = date(2026, 8, 27)
    as_of = date.fromordinal(today.toordinal() - age_days)
    assert is_fresh(as_of, today) is expected


def test_is_fresh_rejects_a_future_as_of():
    """as_of 在未来 = 机器时钟被改过 / 文件是别的机器搬来的。
    朴素写法 `(today - as_of).days <= 7` 会把它判为新鲜（负数当然 ≤ 7），
    于是那份来路不明的清单被无限期复用。必须判假去重拉。"""
    assert is_fresh(date(2026, 8, 28), date(2026, 8, 27)) is False


def test_is_fresh_max_age_is_configurable():
    today = date(2026, 8, 27)
    assert is_fresh(date(2026, 8, 26), today, max_age_days=0) is False
    assert is_fresh(today, today, max_age_days=0) is True


# ================================================================ 默认落点

def test_default_path_is_under_data():
    """默认落点与行情缓存同在 data/ 下（同一类"可以随手删掉、删了会自动重建"的产物）。"""
    assert SYMBOLS_PATH.parts[-2:] == ("data", "symbols.parquet")
