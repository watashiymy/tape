import multiprocessing as mp

import pandas as pd
import pytest

from quant.data.cache import BarCache
from tests.conftest import make_bars


def _row(d, px):
    return dict(date=d, open=px, high=px, low=px, close=px, volume=1000, amount=px * 1000)


def _hammer_cache(cache_dir: str, n: int) -> None:
    """子进程入口（spawn 要求模块级函数）：反复 save+load 同一标的。"""
    cache = BarCache(cache_dir)
    df = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    for _ in range(n):
        cache.save("600519", df)
        loaded = cache.load("600519")
        assert loaded is not None and loaded["close"].tolist() == [10.0, 11.0]
        cache.save_meta("600519", {"covered_start": "2016-01-01"})
        assert cache.load_meta("600519") == {"covered_start": "2016-01-01"}


def test_concurrent_save_load_two_processes(tmp_path):
    """两个进程共用 data/cache 是生产常态（回测跑着、面板/信号脚本也在跑）。
    临时文件名若固定为 <symbol>.parquet.tmp，两进程互抢：一方先 rename 走共享 tmp，
    另一方 FileNotFoundError；更糟的窗口是互相截断写入后把混写内容 rename 成正式文件。
    临时名必须进程唯一（mkstemp）。"""
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_hammer_cache, args=(str(tmp_path), 300)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(300)
    assert [p.exitcode for p in procs] == [0, 0]


def test_load_corrupt_parquet_raises_with_symbol_and_path(tmp_path):
    """损坏 parquet 的原生报错只有 '<Buffer>'，10 只循环里根本不知道该删哪个文件。
    必须包出带 symbol 和路径的错误，告诉用户删掉即可自动重拉。"""
    cache = BarCache(tmp_path)
    cache.save("600519", make_bars([_row("2024-01-02", 10.0)]))
    p = tmp_path / "600519.parquet"
    p.write_bytes(p.read_bytes()[:20])          # 截断，模拟写坏的缓存
    with pytest.raises(RuntimeError, match="600519") as ei:
        cache.load("600519")
    assert str(p) in str(ei.value)


def test_load_meta_corrupt_json_raises_with_symbol_and_path(tmp_path):
    cache = BarCache(tmp_path)
    cache.save_meta("600519", {"covered_start": "2016-01-01"})
    p = tmp_path / "600519.meta.json"
    p.write_bytes(p.read_bytes()[:5])           # 截断成非法 JSON
    with pytest.raises(RuntimeError, match="600519") as ei:
        cache.load_meta("600519")
    assert str(p) in str(ei.value)


def test_save_and_load_roundtrip(tmp_path):
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    cache.save("600519", df)
    loaded = cache.load("600519")
    assert loaded is not None
    assert list(loaded.index) == list(df.index)
    assert loaded["close"].tolist() == [10.0, 11.0]
    # 缓存层最该保证的是整个 schema 原样回来，不只是 close 这一列
    assert list(loaded.columns) == list(df.columns)
    assert loaded.dtypes.equals(df.dtypes)
    assert loaded.index.name == "date"


def test_load_missing_returns_none(tmp_path):
    assert BarCache(tmp_path).load("600519") is None


def test_meta_roundtrip_and_missing_returns_empty_dict(tmp_path):
    """meta 缺失必须返回 {} 而不是抛异常：本地已有的老缓存全都没有 meta.json，
    抛异常会让整个回测入口在第一只标的上就死掉。"""
    cache = BarCache(tmp_path)
    assert cache.load_meta("600519") == {}
    cache.save_meta("600519", {"covered_start": "2016-01-01"})
    assert cache.load_meta("600519") == {"covered_start": "2016-01-01"}
    assert cache.load_meta("600036") == {}          # 不能串标的


def test_save_meta_is_atomic_and_leaves_no_temp_file(tmp_path):
    cache = BarCache(tmp_path)
    cache.save_meta("600519", {"covered_start": "2016-01-01"})
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "600519.meta.json").exists()


def test_meta_does_not_collide_with_bars_file(tmp_path):
    """meta 与行情同目录同前缀。写 meta 若覆盖了 parquet，缓存直接全毁。"""
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0)])
    cache.save("600519", df)
    cache.save_meta("600519", {"covered_start": "2024-01-01"})
    assert cache.load("600519") is not None
    assert cache.load("600519")["close"].tolist() == [10.0]


def test_merge_dedup_keeps_last():
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    new = make_bars([_row("2024-01-03", 11.5), _row("2024-01-04", 12.0)])
    merged = BarCache.merge(old, new)
    assert len(merged) == 3
    assert merged.loc["2024-01-03", "close"] == 11.5  # 重叠日期以新数据为准
    assert merged.index.is_monotonic_increasing


def test_merge_from_empty_cache():
    """每个标的第一次取数都走这条分支（cached is None），必须钉住。"""
    new = make_bars([_row("2024-01-03", 11.0), _row("2024-01-02", 10.0)])
    merged = BarCache.merge(None, new)
    assert len(merged) == 2
    assert merged.index.is_monotonic_increasing


def test_merge_with_empty_new_preserves_dtypes():
    """pandas 3.0 的 concat 不再忽略空块的 dtype：拼一张空表会把所有列变成 object，
    而 object 下的算术照样不报错——只在当次进程里错，重跑又对，最难查的那类。"""
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    empty = pd.DataFrame(columns=old.columns, index=pd.DatetimeIndex([], name="date"))
    merged = BarCache.merge(old, empty)
    assert len(merged) == 2
    assert merged.dtypes.equals(old.dtypes)


def test_merge_mismatched_columns_raises():
    """加字段后本地旧缓存全是旧 schema。静默合并会让缺失列变 NaN，
    且 prepare_bars 一条告警都不发——指标全 NaN、净值一条直线、全程不报错。"""
    old = make_bars([_row("2024-01-02", 10.0)])
    new = make_bars([_row("2024-01-03", 11.0)]).drop(columns=["adj_factor"])
    with pytest.raises(ValueError, match="列不一致"):
        BarCache.merge(old, new)


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0)])
    cache.save("600519", df)
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "600519.parquet").exists()


def test_make_bars_fills_partially_specified_optional_columns():
    """make_bars 被 9 个后续测试文件依赖。只有部分行显式给了 trade_status 时，
    pandas 会把其余行填成 NaN——若不回填默认值，Task 3 的停牌过滤测试会把所有行滤光。"""
    df = make_bars([
        dict(date="2024-01-02", open=10, high=10, low=10, close=10, volume=1000, amount=1e4),
        dict(date="2024-01-03", open=10, high=10, low=10, close=10, volume=0, amount=0,
             trade_status=0),
    ])
    assert df["trade_status"].tolist() == [1, 0]   # 未给的那行必须是 1，不能是 NaN
    assert df["adj_factor"].tolist() == [1.0, 1.0]
    assert df["is_st"].tolist() == [0, 0]
    assert df.index.name == "date"
    # 必须断言 dtype：只比值察觉不到 float 污染（1.0 == 1 恒成立），
    # 而线上 provider 永远产出 int，fixture 造出 float 就是在测线上不存在的形态。
    assert df["trade_status"].dtype == "int64"
    assert df["is_st"].dtype == "int64"
    assert df["adj_factor"].dtype == "float64"


def test_make_bars_rejects_missing_required_column():
    """必填列漏写只会得到一列 NaN，而 NaN 参与比较恒为 False：
    Task 7 的成交额过滤会静默不出信号，测试还"通过"——测的却是错的东西。"""
    with pytest.raises(ValueError, match="必填列"):
        make_bars([
            dict(date="2024-01-02", open=10, high=10, low=10, close=10, volume=1000, amount=1e4),
            dict(date="2024-01-03", open=10, high=10, low=10, close=10, volume=1000),  # 漏 amount
        ])
