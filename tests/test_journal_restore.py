# tests/test_journal_restore.py — 「↶ 恢复上一版」的落盘层（2026-09-05）
#
# store.restore_backup 把正式文件与 .bak **互换**。这里钉四件事：换回去、再换回来、
# 没备份/坏备份时一个字节都不动、不留临时文件。这份文件不可再生，
# "换错了"与"换的时候把文件弄坏了"都是不可接受的。
import errno
import multiprocessing as mp
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from quant.journal import store


def _row(trade_id: str, shares: int) -> dict:
    return {"trade_id": trade_id, "date": "2026-08-25", "time": "", "symbol": "600519",
            "name": "贵州茅台", "kind": "buy", "shares": shares, "price": 1500.0,
            "amount": shares * 1500.0, "fee": 5.0, "tax": 0.0, "source": "discretionary",
            "stop_plan": "", "reason": "", "note": ""}


def _save(path: Path, *rows: dict) -> bytes:
    store.save_trades(pd.DataFrame(list(rows), columns=store.schema.COLUMNS), path)
    return path.read_bytes()


def test_restore_swaps_the_journal_and_its_backup(tmp_path):
    path = tmp_path / "trades.csv"
    v1 = _save(path, _row("T1", 100))
    v2 = _save(path, _row("T1", 100), _row("T2", 200))      # .bak = v1
    assert store.backup_path(path).read_bytes() == v1

    n = store.restore_backup(path)

    assert n == 1
    assert path.read_bytes() == v1, "正式文件该变成上一版"
    assert store.backup_path(path).read_bytes() == v2, "刚才那版该成为备份（可以再换回）"


def test_restoring_twice_is_a_no_op(tmp_path):
    path = tmp_path / "trades.csv"
    _save(path, _row("T1", 100))
    v2 = _save(path, _row("T1", 100), _row("T2", 200))
    store.restore_backup(path)
    n = store.restore_backup(path)
    assert n == 2 and path.read_bytes() == v2


def test_restore_without_a_backup_raises_and_touches_nothing(tmp_path):
    """第一次保存之后才有备份——这不是坏文件，是 FileNotFoundError，措辞要说清。"""
    path = tmp_path / "trades.csv"
    v1 = _save(path, _row("T1", 100))
    assert not store.backup_path(path).exists()
    with pytest.raises(FileNotFoundError, match="第一次保存"):
        store.restore_backup(path)
    assert path.read_bytes() == v1


def test_a_corrupt_backup_is_rejected_before_any_file_moves(tmp_path):
    """坏备份换进正式文件 = 用一份读不懂的东西覆盖用户唯一的记录。必须先校验，
    校验不过一个字节都不动。"""
    path = tmp_path / "trades.csv"
    v1 = _save(path, _row("T1", 100))
    bak = store.backup_path(path)
    bak.write_text("symbol,kind\n600519,buy\n", encoding="utf-8")     # 列不对
    broken = bak.read_bytes()
    with pytest.raises(RuntimeError):
        store.restore_backup(path)
    assert path.read_bytes() == v1 and bak.read_bytes() == broken


def test_restore_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "journal" / "trades.csv"
    _save(path, _row("T1", 100))
    _save(path, _row("T1", 100), _row("T2", 200))
    store.restore_backup(path)
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_restore_uses_the_same_lock_as_saving(tmp_path, monkeypatch):
    """换文件与记账共用一把锁：另一个标签页正在追加时不许中途插进来换文件。"""
    path = tmp_path / "trades.csv"
    _save(path, _row("T1", 100))
    _save(path, _row("T1", 100), _row("T2", 200))
    seen = []
    real = store.locked

    def spy(p=store.TRADES_PATH):
        seen.append(Path(p))
        return real(p)
    monkeypatch.setattr(store, "locked", spy)
    store.restore_backup(path)
    assert seen == [path]


# ================================================================ 换到一半出了事（2026-11-19）
#
# 上面钉的是"换成了"与"根本没开始换"。恢复要动两个文件、分两下改名，这里钉中间出事的几种
# （2026-11-18 逐步实测过，那时的结果是：前两种各漏一个临时文件；最后一种停在半路——日志已经
# 换成了上一版，页面却说"记录未被修改"，点恢复之前的那一版只剩在一个临时文件里）。
# 例子都是虚构的票和数。

def _two_versions(tmp_path) -> tuple[Path, bytes, bytes]:
    """日志 = 第二版（两条），上一版 = 第一版（一条）。交回（路径、日志的字节、上一版的字节）。"""
    path = tmp_path / "trades.csv"
    v1 = _save(path, _row("T1", 100))
    v2 = _save(path, _row("T1", 100), _row("T2", 200))
    return path, v2, v1


def _names(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


#: 下面两条用的"单个文件最多能写多大"（字节）：两条记录的表比它小，六十条的比它大。
_FILE_SIZE_LIMIT = 4096
_MANY = 60


def _restore_under_a_file_size_limit(path: str) -> None:
    """子进程入口（spawn 要求模块级函数）：先把"单个文件最多能写多大"压到 `_FILE_SIZE_LIMIT`，
    再去恢复上一版。不打桩——是操作系统自己在写到一半时拒绝（EFBIG），与磁盘写满是同一类失败。
    放在子进程里做，这个限制就碰不到跑测试的进程自己。退出码 0 = 如期在写的时候被拒绝。
    """
    import resource
    import signal

    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)   # 超限时内核会先发这个信号，默认动作是杀进程
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FILE_SIZE_LIMIT, hard))
    try:
        store.restore_backup(path)
    except OSError as e:
        raise SystemExit(0 if e.errno == errno.EFBIG else 3) from e
    raise SystemExit(4)                              # 居然换成了：限制没生效


@pytest.mark.parametrize("big", ["当前这版", "上一版"])
def test_a_copy_the_system_refuses_halfway_leaves_nothing_behind(tmp_path, big):
    """恢复先把两版各抄进一个临时文件、再改名。抄到一半被系统拒绝（哪一份太大就是抄哪一份时）：
    两个文件都不动，而且**不留临时文件**——2026-11-19 之前，抄"当前这版"失败时那半截会留在目录里，
    每点一次多一个。"""
    pytest.importorskip("resource")                  # POSIX 才有这个限制
    path = tmp_path / "trades.csv"
    few = [_row("T1", 100), _row("T2", 200)]
    many = [_row(f"T{i:03d}", 100) for i in range(_MANY)]
    first, second = (few, many) if big == "当前这版" else (many, few)
    previous, journal = _save(path, *first), _save(path, *second)
    assert min(len(journal), len(previous)) < _FILE_SIZE_LIMIT < max(len(journal), len(previous)), \
        "前提：一份抄得进去、另一份抄不进去"

    proc = mp.get_context("spawn").Process(target=_restore_under_a_file_size_limit, args=(str(path),))
    proc.start()
    proc.join(120)
    if proc.is_alive():                              # 卡住了：别把它留在后台
        proc.kill()
        proc.join()

    assert proc.exitcode == 0, f"前提没成立（退出码 {proc.exitcode}）：没有在抄的时候被系统拒绝"
    assert path.read_bytes() == journal and store.backup_path(path).read_bytes() == previous
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


@pytest.mark.parametrize("failure", [OSError(errno.ENOSPC, "No space left on device"), KeyboardInterrupt()],
                         ids=["没有空间", "被打断"])
def test_when_the_second_temp_file_cannot_be_made_the_first_is_cleaned_up(tmp_path, monkeypatch, failure):
    """第一个临时文件（当前这版）已经写好，第二个建不出来：两个文件不动，第一个也得收走。
    2026-11-19 之前它会留在目录里——里面是一整份当前的记录。
    抄文件这一段，不管是系统报错还是被 Ctrl-C 打断都收：这时两个文件都还没碰过，临时文件纯属多余。"""
    path, journal, previous = _two_versions(tmp_path)
    real_mkstemp, made = tempfile.mkstemp, []

    def no_second_one(*args, **kwargs):
        made.append(1)
        if len(made) == 2:
            raise failure
        return real_mkstemp(*args, **kwargs)
    monkeypatch.setattr(store.tempfile, "mkstemp", no_second_one)

    with pytest.raises(type(failure)):
        store.restore_backup(path)

    assert len(made) == 2, "前提：失败的是第二个"
    assert path.read_bytes() == journal and store.backup_path(path).read_bytes() == previous
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_when_the_journal_cannot_be_replaced_nothing_changes(tmp_path, monkeypatch):
    """两份都抄好了，第一下改名——把日志换成上一版——没成（日志文件被锁定；磁盘真的写满时，
    恢复失败多半就落在这一下）：两个文件不动，两个临时文件都收走。"""
    path, journal, previous = _two_versions(tmp_path)
    real_replace = os.replace

    def the_journal_is_locked(src, dst, *args, **kwargs):
        if Path(dst) == path:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", the_journal_is_locked)

    with pytest.raises(PermissionError):
        store.restore_backup(path)

    assert path.read_bytes() == journal and store.backup_path(path).read_bytes() == previous
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_an_interrupt_right_after_the_journal_is_swapped_does_not_throw_that_version_away(
        tmp_path, monkeypatch):
    """第一下改名**做成之后**才落下的打断（脚本里的 Ctrl-C 正好落在那一下返回的时候）。

    这时日志已经是上一版，点恢复之前的那一版只剩在临时文件里——"出了错就把临时文件收走"在这里
    等于把那一版删掉，哪儿都没有了。所以改名这一下只认系统报的错（它报错就是没换成）；别的异常
    不去猜到底换没换，一个文件都不删。独立审查用定时信号实测过：改之前随机打断五千来次，
    二百多次是这个结果（页面上走不到——回调不在主线程——但直接调用的脚本走得到）。
    """
    path, journal, previous = _two_versions(tmp_path)
    real_replace = os.replace

    def interrupted_just_after(src, dst, *args, **kwargs):
        real_replace(src, dst, *args, **kwargs)
        if Path(dst) == path:
            raise KeyboardInterrupt
    monkeypatch.setattr(store.os, "replace", interrupted_just_after)

    with pytest.raises(KeyboardInterrupt):
        store.restore_backup(path)

    assert path.read_bytes() == previous, "前提：日志确实已经换成了上一版"
    kept = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert len(kept) == 1 and kept[0].read_bytes() == journal, "点恢复之前的那一版被当成垃圾收走了"


def test_an_interrupt_right_after_the_backup_is_swapped_is_not_mistaken_for_a_failure(
        tmp_path, monkeypatch):
    """第二下改名**做成之后**才落下的打断：这时已经全换完了——日志是上一版、刚才那版成了新的上一版。
    不许把它当成"上一版没换成"去往回换：装着刚才那版的临时文件已经改名走了，往回换只会换出一个
    "停在半路、那一版在某某文件里"的假报告，指向一个不存在的文件。"""
    path, journal, previous = _two_versions(tmp_path)
    bak = store.backup_path(path)
    real_replace = os.replace

    def interrupted_just_after(src, dst, *args, **kwargs):
        real_replace(src, dst, *args, **kwargs)
        if Path(dst) == bak:
            raise KeyboardInterrupt
    monkeypatch.setattr(store.os, "replace", interrupted_just_after)

    with pytest.raises(KeyboardInterrupt):
        store.restore_backup(path)

    assert path.read_bytes() == previous and bak.read_bytes() == journal, "其实已经换完了"
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_restore_brings_the_journal_back_when_only_the_backup_is_left(tmp_path):
    """日志文件不在了、只剩一份上一版：恢复把它放回来。没有"刚才那版"可换成备份，上一版原样留着。"""
    path, _, previous = _two_versions(tmp_path)
    path.unlink()

    assert store.restore_backup(path) == 1

    assert path.read_bytes() == previous and store.backup_path(path).read_bytes() == previous
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_when_the_backup_cannot_be_replaced_the_journal_is_put_back(tmp_path, monkeypatch):
    """最后一步——把"刚才那版"换成新的上一版——没成（备份文件被锁定；磁盘写满时也碰得到）：
    日志得**换回刚才那版**，等于什么都没发生，目录里不留东西。

    2026-11-19 之前这里停在半路：日志已经是上一版、.bak 也还是上一版，点恢复之前的那一版
    （往往是最新的）只剩在一个名字随机的临时文件里，而页面说的是"记录未被修改"。
    """
    path, journal, previous = _two_versions(tmp_path)
    bak = store.backup_path(path)
    real_replace = os.replace

    def the_backup_is_locked(src, dst, *args, **kwargs):
        if Path(dst) == bak:
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", the_backup_is_locked)

    with pytest.raises(PermissionError):
        store.restore_backup(path)

    assert path.read_bytes() == journal, "恢复没做成，日志却停在了上一版"
    assert bak.read_bytes() == previous
    assert _names(path) == ["trades.csv", "trades.csv.bak", "trades.csv.lock"]


def test_when_even_putting_it_back_fails_the_error_says_where_that_version_is(tmp_path, monkeypatch):
    """最坏的一种：日志换成了上一版，换备份没成，把日志换回去**也**没成（盘满时改名一下都做不了）。

    这时没有办法让它"等于没发生"。能做的只有两件，都得做到：点恢复之前的那一版**原样留着、
    不许当垃圾收走**（它是那一版唯一的下落）；抛出的错里**写明它在哪个文件里**——不许再让
    调用方以为"什么都没动"。
    """
    path, journal, previous = _two_versions(tmp_path)
    bak = store.backup_path(path)
    real_replace, renames = os.replace, []

    def only_the_first_rename_works(src, dst, *args, **kwargs):
        renames.append(Path(dst).name)
        if len(renames) > 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(store.os, "replace", only_the_first_rename_works)

    with pytest.raises(store.RestoreHalfDone) as caught:
        store.restore_backup(path)

    assert renames == ["trades.csv", "trades.csv.bak", "trades.csv"], \
        "前提：换日志成了 → 换备份没成 → 把日志换回去也没成"
    kept = caught.value.kept
    assert kept.parent == path.parent and kept.read_bytes() == journal, "点恢复之前的那一版得原样留着"
    assert path.read_bytes() == previous and bak.read_bytes() == previous
    assert _names(path) == sorted(["trades.csv", "trades.csv.bak", "trades.csv.lock", kept.name])
    assert str(kept) in str(caught.value), "错里要写明那一版在哪个文件里"
    assert isinstance(caught.value.__cause__, OSError) and "No space left" in str(caught.value.__cause__)


def test_an_unreadable_backup_is_reported_as_the_backup(tmp_path):
    """上一版读不出来时，报的是"上一版读不出来"并指名那个文件。

    2026-11-19 之前用的是"日志坏了"那句现成的话：说的是"交易日志文件损坏"，还让人去找
    `trades.csv.bak.bak`——那个文件从来不存在。
    """
    path = tmp_path / "trades.csv"
    _save(path, _row("T1", 100))
    bak = store.backup_path(path)
    bak.write_text("symbol,kind\n600519,buy\n", encoding="utf-8")     # 列不对

    with pytest.raises(RuntimeError) as caught:
        store.restore_backup(path)

    said = str(caught.value)
    assert "上一版" in said and str(bak) in said, said
    assert ".bak.bak" not in said, f"让人去找一个不存在的文件: {said}"
    assert "不要删" not in said and "交易日志文件损坏" not in said, f"坏的是备份，不是日志: {said}"
    assert "列不匹配" in said, f"读不出来的原因要带上: {said}"
