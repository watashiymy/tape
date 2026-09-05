# tests/test_journal_restore.py — 「↶ 恢复上一版」的落盘层（2026-09-05）
#
# store.restore_backup 把正式文件与 .bak **互换**。这里钉四件事：换回去、再换回来、
# 没备份/坏备份时一个字节都不动、不留临时文件。这份文件不可再生，
# "换错了"与"换的时候把文件弄坏了"都是不可接受的。
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
