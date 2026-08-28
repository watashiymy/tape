# tests/test_journal_store.py — 交易日志的落盘（v0.3.0 设计 §2.1）
#
# 与行情缓存的根本差别：**这份文件不可再生**。缓存坏了删掉重拉即可，
# 交易记录删了就永远没了。所以这里的断言比 test_cache.py 更严：
#   1. 往返保真 —— 尤其前导零（本项目已两次踩过 "000333" → 333）；
#   2. 原子写 —— 写到一半被打断，磁盘上要么是旧的完整文件，要么是新的完整文件；
#   3. 坏文件必须**响亮**报错，而且提示的自愈办法是 git 找回，绝不能是"删掉重建"。
import multiprocessing as mp
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from quant.journal import schema, store

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 28, 15, 30, 12)


def _row(**over) -> dict:
    row = dict(date=date(2026, 8, 27), time="14:35", symbol="000333", name="美的集团",
               kind="buy", shares=100.0, price=71.5, amount=7150.0, fee=5.0, tax=0.0,
               source="ma_cross", stop_plan=68.0, reason="20日线金叉", note="")
    row.update(over)
    return row


def _hammer_store(path: str, n: int) -> None:
    """子进程入口（spawn 要求模块级函数）：反复整表落盘 + 读回。

    读回的结果只能是两个进程写的其中一份完整表，绝不能是半截文件——
    这正是"原子写"要保证的东西，单进程测不出来。
    """
    for i in range(n):
        assert store.append_trade(_row(reason=f"第{i}次"), path)
        loaded = store.load_trades(path)          # 坏文件会在这里响亮抛错
        assert list(loaded.columns) == list(schema.COLUMNS)
        assert len(loaded) >= 1
        assert loaded["symbol"].tolist() == ["000333"] * len(loaded)


# ================================================================ 空表与往返

def test_missing_file_returns_a_typed_empty_frame(tmp_path):
    """还没记过任何一笔时不许抛错（新用户的第一次打开），也不许返回 None——
    调用方就得到处写 `if df is None`，早晚漏一处。"""
    df = store.load_trades(tmp_path / "journal" / "trades.csv")

    assert df.empty
    assert list(df.columns) == list(schema.COLUMNS)
    assert df["shares"].dtype == "float64"
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_empty_frame_has_the_same_dtypes_as_a_loaded_one(tmp_path):
    """空表与有数据的表 dtype 必须一致。否则 pandas 3.0 里 concat 一张空表
    会把整列污染成 object（cache.merge 里已经踩过），而后续算术照样不报错。"""
    path = tmp_path / "trades.csv"
    empty = store.empty_trades()
    store.save_trades(pd.concat([empty, pd.DataFrame([_row(trade_id="x")])],
                                ignore_index=True), path)

    loaded = store.load_trades(path)

    assert dict(empty.dtypes.astype(str)) == dict(loaded.dtypes.astype(str))


def test_roundtrip_keeps_every_field(tmp_path):
    path = tmp_path / "trades.csv"
    store.append_trade(_row(), path, now=NOW)

    df = store.load_trades(path)

    assert len(df) == 1
    got = df.iloc[0]
    assert got["date"] == pd.Timestamp("2026-08-27")
    assert got["time"] == "14:35"
    assert got["symbol"] == "000333"
    assert got["name"] == "美的集团"
    assert got["kind"] == "buy"
    assert got["shares"] == pytest.approx(100.0)
    assert got["price"] == pytest.approx(71.5)
    assert got["amount"] == pytest.approx(7150.0)
    assert got["fee"] == pytest.approx(5.0)
    assert got["tax"] == pytest.approx(0.0)
    assert got["source"] == "ma_cross"
    assert got["stop_plan"] == pytest.approx(68.0)
    assert got["reason"] == "20日线金叉"


def test_leading_zero_symbol_survives_the_roundtrip(tmp_path):
    """本项目最贵的一个坑：CSV 读回时 000333 被当成整数 333，
    于是名称查不到、持仓与信号池对不上——而全程没有任何报错。
    dtype={"symbol": str} 必须全程强制。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(symbol="000333"), path, now=NOW)

    df = store.load_trades(path)

    assert df["symbol"].tolist() == ["000333"]
    assert isinstance(df["symbol"].iloc[0], str)


def test_leading_zero_survives_a_second_append(tmp_path):
    """追加时要先读回再整表重写：读那一步只要漏了 dtype，
    第二笔就会把第一笔的 000333 改写成 333，且悄无声息。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(symbol="000333"), path, now=NOW)
    store.append_trade(_row(symbol="600519", name="贵州茅台"), path, now=NOW)

    assert store.load_trades(path)["symbol"].tolist() == ["000333", "600519"]


def test_chinese_text_roundtrips(tmp_path):
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="跌破止损，认输", note="手滑多下了一档"), path, now=NOW)

    df = store.load_trades(path)
    assert df["reason"].iloc[0] == "跌破止损，认输"
    assert df["note"].iloc[0] == "手滑多下了一档"


def test_file_has_a_utf8_bom_so_excel_opens_it_directly(tmp_path):
    """设计 §2.1 选 CSV 的理由之一就是"可直接用 Excel 打开"。
    没有 BOM 的话 Excel 按 GBK 解，中文理由全是乱码——用字节断言，不靠肉眼。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(), path, now=NOW)

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_plain_utf8_without_bom_still_loads(tmp_path):
    """用户可能用 vim/git 手改过这份文件（设计明说"可手工修"），存回来没有 BOM。
    读的时候必须两种都认，否则手改一次就打不开了。"""
    path = tmp_path / "trades.csv"
    header = ",".join(schema.COLUMNS)
    body = "t1,2026-08-27,,000333,美的集团,buy,100.0,71.5,7150.0,5.0,0.0,ma_cross,,手改,"
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")

    df = store.load_trades(path)

    assert df["symbol"].tolist() == ["000333"] and df["reason"].tolist() == ["手改"]


def test_note_reading_NA_is_text_not_a_missing_value(tmp_path):
    """pandas 默认把 NA/nan/null 这些字面量读成缺失值。备注里写了 "NA"
    （或某只票的名字含 nan）就会变成 NaN，用户以为自己没写过。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(note="NA", reason="nan"), path, now=NOW)

    df = store.load_trades(path)

    assert df["note"].iloc[0] == "NA" and df["reason"].iloc[0] == "nan"


def test_blank_optional_fields_come_back_as_empty_string_and_nan(tmp_path):
    """分红行没有股数/价格/止损。字符串列的空值统一是 ""，数值列统一是 NaN；
    两者混用会让页面到处判 `pd.isna(x) or x == ""`。"""
    path = tmp_path / "trades.csv"
    store.append_trade(dict(date=date(2026, 8, 27), symbol="000333", kind="dividend",
                            amount=328.0), path, now=NOW)

    got = store.load_trades(path).iloc[0]

    assert got["time"] == "" and got["note"] == ""
    assert pd.isna(got["shares"]) and pd.isna(got["stop_plan"])


# ================================================================ trade_id

def test_append_returns_a_trade_id_that_is_written_to_the_file(tmp_path):
    path = tmp_path / "trades.csv"
    tid = store.append_trade(_row(), path, now=NOW)

    assert tid == "20260828-153012-001"
    assert store.load_trades(path)["trade_id"].tolist() == [tid]


def test_existing_trade_ids_never_change_when_appending(tmp_path):
    """trade_id 是编辑/删除的定位依据（设计 §2.3：不靠行号）。
    重编号会让"删第 3 行"删掉别人。"""
    path = tmp_path / "trades.csv"
    first = store.append_trade(_row(), path, now=NOW)
    store.append_trade(_row(symbol="600519"), path, now=datetime(2026, 8, 29, 9, 31, 0))

    assert store.load_trades(path)["trade_id"].tolist()[0] == first


def test_two_appends_in_the_same_second_get_different_ids(tmp_path):
    """连着记两笔（从信号页一键记账很容易一秒内点两次）。ID 只到秒的话
    两行主键相同，之后删一笔会删掉两笔。"""
    path = tmp_path / "trades.csv"
    a = store.append_trade(_row(), path, now=NOW)
    b = store.append_trade(_row(symbol="600519"), path, now=NOW)

    assert a != b
    assert store.load_trades(path)["trade_id"].tolist() == [a, b]


def test_trade_id_is_based_on_entry_time_not_the_trade_date(tmp_path):
    """事后补记 2020 年的老交易时，ID 记的是"什么时候录的"。
    用成交日期当 ID 的话，改一次日期就等于换了主键，编辑功能直接失效。"""
    path = tmp_path / "trades.csv"
    tid = store.append_trade(_row(date=date(2020, 1, 2)), path, now=NOW)

    assert tid.startswith("20260828")


def test_append_refuses_a_caller_supplied_trade_id(tmp_path):
    """主键只能由 store 发。放行的话页面从表单里带一个空/重复的 ID 上来，
    唯一性就没了——而重复主键的表现是"删一笔少两笔"。"""
    with pytest.raises(ValueError, match="trade_id"):
        store.append_trade(_row(trade_id="我自己编的"), tmp_path / "trades.csv")


def test_append_rejects_unknown_fields(tmp_path):
    with pytest.raises(ValueError, match="symbol_code"):
        store.append_trade(_row(symbol_code="000333"), tmp_path / "trades.csv")


def test_append_creates_the_directory(tmp_path):
    """journal/ 目录还不存在时第一次记账不许炸。"""
    path = tmp_path / "journal" / "trades.csv"
    store.append_trade(_row(), path, now=NOW)

    assert path.exists()


# ================================================================ 原子写

def test_a_failed_write_leaves_the_old_file_intact(tmp_path):
    """写盘中途出错（磁盘满、Ctrl-C）时，原文件必须原封不动。
    直接写目标文件的话，用户记第 101 笔时崩一次，前 100 笔就没了。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    before = path.read_bytes()

    def boom(*a, **kw):
        raise OSError("disk full")

    original = pd.DataFrame.to_csv
    pd.DataFrame.to_csv = boom
    try:
        with pytest.raises(OSError):
            store.append_trade(_row(reason="第二笔"), path, now=NOW)
    finally:
        pd.DataFrame.to_csv = original

    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []       # 也不许留下垃圾临时文件


def test_concurrent_writers_never_see_a_half_written_file(tmp_path):
    """面板与脚本共用一份 journal/trades.csv 是常态。临时文件名若固定，
    两进程互抢：一方 FileNotFoundError，更糟的是互相截断后把混写内容 rename 成正式文件。
    临时名必须进程唯一（mkstemp），沿用 BarCache 的做法。"""
    path = str(tmp_path / "trades.csv")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_hammer_store, args=(path, 60)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(300)

    assert [p.exitcode for p in procs] == [0, 0]
    assert list(tmp_path.glob("*.tmp")) == []


# ================================================================ 坏文件必须响亮

def test_missing_columns_raise_loudly_with_the_path(tmp_path):
    """少一列（用户在 Excel 里删了 fee 那列另存）时若静默补 NaN，
    盈亏会少算全部费用，且看着完全正常。"""
    path = tmp_path / "trades.csv"
    path.write_text("symbol,price\n000333,71.5\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as ei:
        store.load_trades(path)

    assert str(path) in str(ei.value)
    assert "fee" in str(ei.value)                   # 缺哪几列要说清楚


def test_unknown_extra_columns_raise_loudly(tmp_path):
    """用户在 Excel 里加了一列自用备注。静默忽略的话，下一次记账整表重写时
    那一列就被我们悄悄删掉了——毁的是用户自己的数据。"""
    path = tmp_path / "trades.csv"
    path.write_text(",".join(schema.COLUMNS) + ",我的批注\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="我的批注"):
        store.load_trades(path)


def test_undecodable_file_raises_loudly(tmp_path):
    """Excel 在中文 Windows 上另存 CSV 默认是 GBK。报"文件坏了 + 路径 + 怎么办"，
    而不是让 pandas 抛一个不带文件名的 UnicodeDecodeError。"""
    path = tmp_path / "trades.csv"
    path.write_bytes((",".join(schema.COLUMNS) + "\n").encode("utf-8")
                     + "t1,2026-08-27,,000333,美的集团,buy,100,71.5,7150,5,0,ma_cross,,理由,\n"
                     .encode("gbk"))

    with pytest.raises(RuntimeError) as ei:
        store.load_trades(path)
    assert str(path) in str(ei.value)


def test_unparsable_date_column_raises_loudly(tmp_path):
    """手改时把日期写成 2026/8/27 或 27-08-2026。静默变 NaT 的话，
    这笔交易会从所有按日期筛选的视图里消失，而汇总数字照样给得出来。"""
    path = tmp_path / "trades.csv"
    header = ",".join(schema.COLUMNS)
    path.write_text(f"{header}\nt1,八月二十七,,000333,美的,buy,100,71.5,7150,5,0,ma_cross,,x,\n",
                    encoding="utf-8")

    with pytest.raises(RuntimeError, match="date"):
        store.load_trades(path)


def test_corruption_message_points_at_git_not_at_deleting_the_file(tmp_path):
    """与行情缓存的最大区别：这份文件**不可再生**。
    照抄 cache.py 那句"删除该文件后重跑即可自动重拉"会直接毁掉用户的全部交易记录。
    正确的自愈办法是 git 找回（设计 §2.1 把日志纳入版本控制，图的就是这个）。"""
    path = tmp_path / "trades.csv"
    path.write_text("symbol,price\n000333,71.5\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as ei:
        store.load_trades(path)

    msg = str(ei.value)
    assert "git checkout" in msg
    assert "删除该文件" not in msg


# ================================================================ 写入前的列校验

def test_save_rejects_a_frame_with_wrong_columns(tmp_path):
    """落盘前就要炸：列不对的表写进去，就是把坏文件留给下一次启动。
    （沿用 save_symbols 的"列不全一律拒写"约定。）"""
    with pytest.raises(ValueError):
        store.save_trades(pd.DataFrame([{"symbol": "000333"}]), tmp_path / "trades.csv")


def test_save_writes_columns_in_the_declared_order(tmp_path):
    """列顺序稳定 = git diff 干净。顺序随手变的话，一次记账的 diff 是整个文件。"""
    path = tmp_path / "trades.csv"
    shuffled = store.empty_trades().loc[:, list(reversed(schema.COLUMNS))]

    store.save_trades(shuffled, path)

    assert path.read_text(encoding="utf-8-sig").splitlines()[0] == ",".join(schema.COLUMNS)


# ================================================================ 仓库里的那份（设计 §2.1：纳入 git）

def test_repo_journal_file_exists_and_loads():
    """journal/trades.csv 必须随仓库存在（哪怕只有表头）：
    它同时也是"这个目录要进 git"的载体——空目录 git 根本不跟踪。"""
    path = ROOT / "journal" / "trades.csv"
    assert path.exists(), "缺少 journal/trades.csv（带表头的初始空文件）"

    df = store.load_trades(path)
    assert list(df.columns) == list(schema.COLUMNS)


def test_journal_is_not_gitignored():
    """交易记录不可再生，git 历史就是免费的审计轨迹与撤销能力（设计 §2.1）。
    被 .gitignore 掉的话，用户误删一行永远找不回来——而且不会有任何提示。"""
    proc = subprocess.run(["git", "check-ignore", "journal/trades.csv"],
                          cwd=ROOT, capture_output=True, text=True)

    assert proc.returncode != 0, "journal/trades.csv 被 .gitignore 掉了：用户数据必须进版本控制"
