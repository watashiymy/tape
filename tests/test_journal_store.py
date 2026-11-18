# tests/test_journal_store.py — 交易日志的落盘（v0.3.0 设计 §2.1）
#
# 与行情缓存的根本差别：**这份文件不可再生**。缓存坏了删掉重拉即可，
# 交易记录删了就永远没了。所以这里的断言比 test_cache.py 更严：
#   1. 往返保真 —— 尤其前导零（本项目已两次踩过 "000333" → 333）；
#   2. 原子写 —— 写到一半被打断，磁盘上要么是旧的完整文件，要么是新的完整文件；
#   3. 坏文件必须**响亮**报错，而且提示的自愈办法是 git 找回，绝不能是"删掉重建"。
import errno
import multiprocessing as mp
import os
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


def test_corruption_message_points_at_the_backup_not_at_deleting_the_file(tmp_path):
    """与行情缓存的最大区别：这份文件**不可再生**。
    照抄 cache.py 那句"删除该文件后重跑即可自动重拉"会直接毁掉用户的全部交易记录。

    v0.3.2 起自愈办法也换了：日志移出版本控制之后 `git checkout` 是**假出路**
    （文件根本没被跟踪，那条命令只会报错），提示必须指向上一版备份 .bak。
    留着一句过时的自救指令比不给指令更坏——用户照做，然后以为数据真的没了。"""
    path = tmp_path / "trades.csv"
    path.write_text("symbol,price\n000333,71.5\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as ei:
        store.load_trades(path)

    msg = str(ei.value)
    assert str(store.backup_path(path)) in msg, msg
    assert "git checkout" not in msg, f"日志已不在版本控制里，这是假出路：{msg}"
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


# ================================================================ 上一版备份（v0.3.2 §3）
#
# 日志自 v0.3.2 起**移出版本控制**（它是用户数据，且是一份完整的真实交易记录，
# 仓库一推到远端就全公开了）。那等于拿掉了"git 历史 = 免费撤销"这层保护，
# 而这份文件不可再生、面板的 data_editor 又支持批量编辑——一次误操作可以抹掉多行。
# 所以落盘前先把上一版另存为 `trades.csv.bak`（单层，不做轮转）。

def test_the_first_save_creates_no_backup_and_does_not_complain(tmp_path):
    """第一次记账时没有"上一版"可备份。这不是异常，是每个新用户的第一笔——
    既不许报错，也不许留下一个空的 .bak（那会被当成"上一版是空的"）。"""
    path = tmp_path / "trades.csv"

    store.append_trade(_row(reason="第一笔"), path, now=NOW)

    assert path.exists()
    assert not store.backup_path(path).exists(), "无中生有造了一份备份"


def test_the_second_save_keeps_the_previous_version_byte_for_byte(tmp_path):
    """备份必须是**上一版**，不是刚写的这一版——存成新版等于没有备份。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    first = path.read_bytes()

    store.append_trade(_row(reason="第二笔"), path, now=NOW)

    bak = store.backup_path(path)
    assert bak.read_bytes() == first
    assert bak.read_bytes() != path.read_bytes()
    assert len(store.load_trades(path)) == 2
    assert len(store.load_trades(bak)) == 1, "备份必须还是一份能直接读回的日志"


def test_the_backup_sits_next_to_the_journal(tmp_path):
    """路径写死成 `<日志名>.bak`：用户要能在同一个目录里一眼看到它，
    页面上那行小字说的也是这个位置。"""
    path = tmp_path / "journal" / "trades.csv"
    assert store.backup_path(path) == tmp_path / "journal" / "trades.csv.bak"
    assert store.backup_path(str(path)) == tmp_path / "journal" / "trades.csv.bak"


def test_a_third_save_overwrites_the_backup_with_the_second_version(tmp_path):
    """单层备份（设计 §3 明确不做轮转）：.bak 永远是**紧邻的上一版**。

    "只留一层"连目录一起看（2026-11-18 补）：只比 .bak 的内容的话，悄悄多留一层
    （把被换下来的那份另存成别的名字）是看不出来的——第三次保存才有东西可轮转。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    store.append_trade(_row(reason="第二笔"), path, now=NOW)
    second = path.read_bytes()

    store.append_trade(_row(reason="第三笔"), path, now=NOW)

    assert store.backup_path(path).read_bytes() == second
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_a_failed_backup_is_not_swallowed(tmp_path):
    """备份失败必须**响亮**：悄悄跳过的话，用户以为有一层保护，其实没有
    ——而他只会在真的需要它的那一天才发现。那时已经晚了。

    这里用"备份路径被一个目录占着"制造失败（真实场景：用户手工建了个同名目录，
    或某次同步工具留下的残骸），不 monkeypatch 内部函数。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    before = path.read_bytes()
    store.backup_path(path).mkdir()

    with pytest.raises(OSError):
        store.append_trade(_row(reason="第二笔"), path, now=NOW)

    assert path.read_bytes() == before, "备份失败时不许把新版写进日志"
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_backup_leaves_no_tmp_files(tmp_path):
    """备份走与落盘同一套原子替换：中途断电不会留下半截 .bak。

    v0.5.0 起旁边还多一个 `trades.csv.lock`（并发临界区的挂载点，内容永远为空、
    随时可删）。这里逐个文件名钉死而不是只查 `*.tmp`：多出任何**没预料到**的
    落盘物都该在这条测试上红一次，由人确认它是什么，而不是被一个宽松的 glob 放过。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    store.append_trade(_row(reason="第二笔"), path, now=NOW)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]
    assert store.lock_path(path).read_bytes() == b"", "锁文件必须永远是空的"


def test_editing_the_whole_table_still_leaves_the_previous_version_behind(tmp_path):
    """面板「保存修改」那条路（整表重写）才是最危险的一条：勾错几行删除、
    或把一列改花，一次就没了。它必须同样留下上一版。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    before = path.read_bytes()

    store.save_trades(store.empty_trades(), path)      # 极端情况：整本被清空

    assert len(store.load_trades(path)) == 0
    assert store.backup_path(path).read_bytes() == before
    assert len(store.load_trades(store.backup_path(path))) == 1


# ================================================================ 写不成的时候，上一版也不许动（2026-11-18）
#
# 2026-11-18 之前的顺序是「先把 .bak 换成当前这版 → 再写新内容」。新内容没写成（磁盘满）时
# 日志确实没变，可 .bak 已经成了**当前这版**的拷贝：原来的上一版没了，「恢复上一版」换来换去
# 是两份一样的文件，而页面说的是"日志文件未被修改"。现在先把新内容完整写进临时文件，
# 写成了才动 .bak。下面的例子都是虚构的票和数。

def _two_saves(tmp_path) -> tuple[Path, bytes, bytes]:
    """连着记两笔：日志 = 第二版，上一版 = 第一版。交回（路径、日志的字节、上一版的字节）。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    store.append_trade(_row(symbol="600519", name="贵州茅台", reason="第二笔"), path, now=NOW)
    journal, previous = path.read_bytes(), store.backup_path(path).read_bytes()
    assert previous != journal, "前提：上一版与当前这版不一样，换没换才看得出来"
    return path, journal, previous


def _disk_full(monkeypatch) -> None:
    """让新内容写不进去。桩只打在"把表写成文件"那一步，备份与替换走的都是真的。"""
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(pd.DataFrame, "to_csv", boom)


def test_a_failed_write_leaves_the_previous_version_alone_too(tmp_path, monkeypatch):
    """新内容没写成：日志与上一版都得和失败之前**逐字节相同**，目录里不留临时文件。

    上一版只留一版，是这份不可再生的文件唯一的撤销手段。一次没写成的保存把它换成
    "当前这版的拷贝"，等于什么都没存进去、却把退路拆了——而且没有任何一句话告诉用户。
    """
    path, journal, previous = _two_saves(tmp_path)
    _disk_full(monkeypatch)

    with pytest.raises(OSError, match="No space left"):
        store.append_trade(_row(symbol="600036", name="招商银行", reason="第三笔"), path, now=NOW)

    assert path.read_bytes() == journal
    assert store.backup_path(path).read_bytes() == previous, \
        "上一版被换成了当前这一版：唯一的撤销手段没了"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_a_failed_write_does_not_conjure_a_backup(tmp_path, monkeypatch):
    """只保存过一次、还没有上一版时写失败：不许凭空多出一份 .bak。

    那份 .bak 只会是当前这版的拷贝——「恢复上一版」的按钮因此亮了，点下去换的是两份
    一样的文件，页面还会说"已恢复上一版"。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    journal = path.read_bytes()
    _disk_full(monkeypatch)

    with pytest.raises(OSError, match="No space left"):
        store.append_trade(_row(symbol="600519", name="贵州茅台", reason="第二笔"), path, now=NOW)

    assert path.read_bytes() == journal
    assert not store.backup_path(path).exists(), "没写成的保存留下了一份「上一版」"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["trades.csv", "trades.csv.lock"]


def test_a_write_interrupted_by_ctrl_c_changes_nothing_either(tmp_path, monkeypatch):
    """写新内容时被 Ctrl-C 打断（面板与脚本共用这份日志，脚本是会被打断的）：同样两个文件
    不动、不留临时文件。

    上面几条抛的都是 OSError。收临时文件若只认那一类错，这里就会留下一个——Ctrl-C 不是
    OSError，连 Exception 都不是。
    """
    path, journal, previous = _two_saves(tmp_path)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(pd.DataFrame, "to_csv", interrupted)

    with pytest.raises(KeyboardInterrupt):
        store.append_trade(_row(symbol="600036", name="招商银行", reason="第三笔"), path, now=NOW)

    assert path.read_bytes() == journal
    assert store.backup_path(path).read_bytes() == previous
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


#: 下面两条用的"单个文件最多能写多大"（字节）：几条记录的表比它小，`_BIG` 那样的表比它大。
_FILE_SIZE_LIMIT = 4096
_BIG = (40, "占地方" * 100)       # 四十条、每条备注九百字节：存出来有几十 KB


def _table(n: int, note: str = "") -> pd.DataFrame:
    """n 条虚构记录的一张表（记录号 T000、T001……）。`note` 用来把文件撑大。"""
    return pd.DataFrame([_row(trade_id=f"T{i:03d}", note=note) for i in range(n)],
                        columns=list(schema.COLUMNS))


def _save_under_a_file_size_limit(path: str, n: int, note: str) -> None:
    """子进程入口（spawn 要求模块级函数）：先把"单个文件最多能写多大"压到 `_FILE_SIZE_LIMIT`，
    再去保存 `_table(n, note)`。

    不打桩——是操作系统自己在写到一半时拒绝（EFBIG），与磁盘写满是同一类失败：写了一截、
    写不下去了。放在子进程里做，这个限制就碰不到跑测试的进程自己。
    退出码 0 = 如期在写的时候被拒绝；其余都算前提没成立。
    """
    import resource
    import signal

    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)   # 超限时内核会先发这个信号，默认动作是杀进程
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FILE_SIZE_LIMIT, hard))
    try:
        store.save_trades(_table(n, note), path)
    except OSError as e:
        raise SystemExit(0 if e.errno == errno.EFBIG else 3) from e
    raise SystemExit(4)                              # 居然写成了：限制没生效


def _refused_by_the_system(path: Path, n: int, note: str = "") -> None:
    """在子进程里做一次注定被系统拒绝的保存，并确认它真的是那样失败的。"""
    proc = mp.get_context("spawn").Process(target=_save_under_a_file_size_limit,
                                           args=(str(path), n, note))
    proc.start()
    proc.join(120)
    if proc.is_alive():                              # 卡住了：别把它留在后台
        proc.kill()
        proc.join()
    assert proc.exitcode == 0, f"前提没成立（退出码 {proc.exitcode}）：没有在写的时候被系统拒绝"


def test_a_write_the_system_refuses_halfway_changes_nothing(tmp_path):
    """同一件事，不打桩再证一遍：让操作系统真的在写到一半时拒绝。

    上面两条是让 `to_csv` 一个字节没写就抛错；真实的"写满"是写了一截才失败，临时文件里
    留着半截内容。这里日志、上一版、目录里有什么，三样都得和失败之前一样。
    """
    pytest.importorskip("resource")                  # POSIX 才有这个限制
    path, journal, previous = _two_saves(tmp_path)
    assert len(journal) < _FILE_SIZE_LIMIT, "前提：现有的日志比限制小——另存上一版那一步写得进去"

    _refused_by_the_system(path, *_BIG)              # 新表比限制大：写新内容那一步就被拒绝

    assert path.read_bytes() == journal
    assert store.backup_path(path).read_bytes() == previous, \
        "上一版被换成了当前这一版：唯一的撤销手段没了"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_a_backup_the_system_refuses_halfway_leaves_the_old_one_whole(tmp_path):
    """反过来的那一种：新内容很小、写成了，轮到另存上一版时写到一半被拒绝（现有的日志大）。

    另存上一版做不成，就什么都不写——日志不换；而且**原来那份上一版得是完整的原样**，
    不许变成半截。直接往 .bak 里写（不走临时文件再替换）的话，这里留下的就是当前这版的
    前四千来个字节：既不是上一版，也不是一份读得回来的日志。新内容的临时文件也要收走。
    """
    pytest.importorskip("resource")
    path = tmp_path / "trades.csv"
    store.save_trades(_table(2), path)
    store.save_trades(_table(*_BIG), path)           # 上一版 = 两条的那一版；日志 = 大的那一版
    journal, previous = path.read_bytes(), store.backup_path(path).read_bytes()
    assert len(previous) < _FILE_SIZE_LIMIT < len(journal), \
        "前提：两条记录的表写得进去（下面要存的一条更是），现有的日志另存不进去"

    _refused_by_the_system(path, 1)                  # 新表只有一条：被拒绝的是另存上一版那一步

    assert path.read_bytes() == journal, "另存上一版没做成，日志却换了"
    assert store.backup_path(path).read_bytes() == previous, "另存到一半失败，把原来的上一版写坏了"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["trades.csv", "trades.csv.bak"]


def test_a_backup_that_cannot_be_moved_into_place_leaves_the_old_one_there(tmp_path, monkeypatch):
    """另存上一版的最后一下——把写好的临时文件改名成 .bak——失败：原来那份上一版得原样还在。

    在一块真的写满的小磁盘上量过：保存失败多半就落在这一下（写内容本身没报错，改名报
    "No space left on device"），所以它是最该守住的一种。上一条守的是"写到一半"；
    `test_a_failed_backup_is_not_swallowed` 拿一个目录占着 .bak，比不了内容。
    "先把旧的 .bak 删掉、再把新的改名过去"在平时看不出任何差别，在这里就把上一版删没了。
    """
    path, journal, previous = _two_saves(tmp_path)
    bak = store.backup_path(path)
    real_replace = os.replace

    def no_room_for_the_backup(src, dst, *args, **kwargs):
        if Path(dst) == bak:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", no_room_for_the_backup)

    with pytest.raises(OSError, match="No space left"):
        store.append_trade(_row(symbol="600036", name="招商银行", reason="第三笔"), path, now=NOW)

    assert path.read_bytes() == journal, "另存上一版没做成，日志却换了"
    assert bak.exists() and bak.read_bytes() == previous, "另存没做成，却把原来的上一版弄丢了"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_the_previous_version_is_in_place_before_the_journal_is_replaced(tmp_path, monkeypatch):
    """把"写新内容"挪到前面之后，这条顺序仍然不许反：日志被换掉的**那一刻**，被换掉的
    那一版必须已经躺在 .bak 里。

    只看写完之后的结果，分不出"先另存上一版、再换日志"与"先换日志、再补上一版"——后者在
    两步之间出事（断电、另存失败）时，被换掉的那一版磁盘上哪儿都没有。所以在换日志的那一下
    之前看一眼。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(reason="第一笔"), path, now=NOW)
    first = path.read_bytes()
    bak = store.backup_path(path)
    seen: list[tuple[bytes, bytes | None]] = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        if Path(dst) == path:
            seen.append((path.read_bytes(), bak.read_bytes() if bak.exists() else None))
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", spy)

    store.append_trade(_row(symbol="600519", name="贵州茅台", reason="第二笔"), path, now=NOW)

    assert seen == [(first, first)], "换日志的那一刻，被换掉的那一版还不在 .bak 里"
    assert path.read_bytes() != first, "前提：日志真的换成了新的一版"


def test_the_gap_that_is_left_a_failed_final_rename_costs_the_previous_version(tmp_path, monkeypatch):
    """**没补上的那一小段，如实钉在这里**：新内容写好了、上一版也另存好了，最后把日志换成
    新内容的那一下改名失败——日志文件被锁定时就是这样（在 macOS 上把文件真的锁定之后
    实测过，报的就是下面这个错）；磁盘真的写满时，极少数也会落在这一下（独立审查扫出过）。

    该守住的都守住了：日志没变、不留临时文件、照样响亮报错。但 .bak 这时已经是**当前这版**
    的拷贝，原来的上一版没了——"换日志之前上一版必须已经到位"这条顺序的代价：两个文件的
    改名合不成一步。这不是想要的行为（见 save_trades 的说明）；哪天给它补上了回退，
    把最后一条断言改成 `== previous`。
    """
    path, journal, previous = _two_saves(tmp_path)
    real_replace = os.replace

    def refuse_the_journal(src, dst, *args, **kwargs):
        if Path(dst) == path:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", refuse_the_journal)

    with pytest.raises(PermissionError):
        store.append_trade(_row(symbol="600036", name="招商银行", reason="第三笔"), path, now=NOW)

    assert path.read_bytes() == journal
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]
    assert store.backup_path(path).read_bytes() == journal, \
        "已知的缺口变了：若是补上了回退，这里该是 == previous，把这条断言改过去"


# ================================================================ 仓库里的那份（v0.3.2 §2.1：移出版本控制）

def test_the_repo_journal_still_loads_if_this_machine_has_one():
    """v0.3.2 起 `journal/trades.csv` 不再随仓库分发（它是用户数据，已 gitignore），
    所以不能再断言"文件必须存在"——新克隆就是没有，而且开箱即用（load 空表）。
    但本机若有（多数情况：这就是用户的真实日志），它必须仍然读得动：
    读不动意味着某次改动毁了一份不可再生的文件。"""
    path = ROOT / "journal" / "trades.csv"
    if not path.exists():
        pytest.skip("本机还没记过任何一笔（新克隆的正常状态）")

    df = store.load_trades(path)
    assert list(df.columns) == list(schema.COLUMNS)


def test_a_fresh_clone_without_any_journal_file_works_out_of_the_box(tmp_path):
    """把上一条的另一半钉住：没有 journal/trades.csv 时不需要任何占位文件——
    读是空表，写会自己建目录。这是"日志可以直接 gitignore"的前提。"""
    path = tmp_path / "journal" / "trades.csv"

    assert len(store.load_trades(path)) == 0
    store.append_trade(_row(), path, now=NOW)
    assert len(store.load_trades(path)) == 1


def test_the_journal_and_its_backup_are_gitignored():
    """交易记录是用户数据：仓库一旦推到任何远端，一份完整的真实交易记录就公开了。
    这条断言防的是将来有人"顺手"把 .gitignore 里那两行删掉。"""
    for rel in ("journal/trades.csv", "journal/trades.csv.bak"):
        proc = subprocess.run(["git", "check-ignore", rel],
                              cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, f"{rel} 没有被 .gitignore 掉：用户数据不该进版本控制"
