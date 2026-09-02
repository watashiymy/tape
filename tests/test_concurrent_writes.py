# 并发写入的丢更新（v0.5.0 设计 §4.2）。
#
# 这两份文件都走「读回整份 → 改 → 整份重写」，而 Streamlit 的多个 session 是
# **同一进程里的并发线程**：用户开两个标签页同时提交，两边各读到同一份旧内容、
# 各自整份重写，后写的把先写的悄悄冲掉。没有任何报错。
#
# 用真线程而不是打桩：这类 bug 的全部要害就在调度时序上，把时序换成假的等于没测。
# 每个用例都先用 `_unlocked_*` 复现一次原始症状——**证明这条测试真的能红**，
# 否则加锁之后它就是一条永远绿的空跑，谁也不知道保护还在不在。
from __future__ import annotations

import sys
import threading
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant import filelock            # noqa: E402
from quant.journal import store       # noqa: E402

WORKERS = 8


#: 真实配置的副本——落盘测试用真文件，别拿玩具 YAML 自欺欺人
#: （与 tests/test_config_edit.py 的 cfg 夹具同一个口径）。
REAL_CONFIG = ROOT / "config" / "settings.yaml"


def _row(i: int) -> dict:
    """一笔合法记账。字段名照 schema.COLUMNS，别自创（`action` 是扫描 CSV 的列名，
    日志里叫 `kind`——写错了 append_trade 会当场拒绝，这里就是那么发现的）。"""
    return {"date": date(2026, 9, 2), "time": "14:35", "symbol": "600519",
            "name": "贵州茅台", "kind": "buy", "shares": 100.0, "price": 1500.0,
            "amount": 150000.0, "fee": 37.5, "tax": 0.0, "source": "manual",
            "stop_plan": None, "reason": f"第 {i} 笔", "note": ""}


def _run_together(fn, n: int = WORKERS) -> list[BaseException]:
    """n 个线程同时进同一段代码。用 Barrier 把它们卡在同一起跑线上：
    不卡的话线程 1 往往在线程 2 起来之前就跑完了，竞态根本不发生，测试白绿。"""
    gate = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        gate.wait()
        try:
            fn(i)
        except BaseException as e:      # noqa: BLE001 - 线程里的异常要带回主线程断言
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ================================================================ 交易日志

def test_concurrent_appends_keep_every_trade(tmp_path):
    """八个标签页同时记账，八笔都得在。少一笔就是丢了一笔真实成交。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(0), path)            # 先有文件，从"有内容"开始改

    assert _run_together(lambda i: store.append_trade(_row(i + 1), path)) == []

    df = store.load_trades(path)
    assert len(df) == WORKERS + 1, f"丢了 {WORKERS + 1 - len(df)} 笔"
    assert df["trade_id"].is_unique, "主键撞车：重复主键的表现是「删一笔少两笔」"


def test_the_unlocked_version_really_does_lose_trades(tmp_path):
    """**证明上一条测得到东西**：绕开锁重跑同一件事，必然丢单。

    这条一旦变绿（不再丢），说明并发根本没发生（比如线程被串行化了），
    上面那条也就不再证明任何事——那时该修的是测试，不是庆祝。
    """
    path = tmp_path / "trades.csv"
    store.append_trade(_row(0), path)

    def unlocked_append(i: int) -> None:
        df = store.load_trades(path)             # 读
        row = {c: None for c in df.columns} | _row(i + 1) | {"trade_id": f"T{i:03d}"}
        merged = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        store.save_trades(merged, path)          # 写（中间没有互斥）

    _run_together(unlocked_append)
    assert len(store.load_trades(path)) < WORKERS + 1, \
        "没加锁却一笔没丢——并发没真正发生，上面那条测试是空跑"


def test_the_lock_file_is_disposable(tmp_path):
    """锁文件是纯运行期产物：内容永远为空，删掉也不影响下一次写。"""
    path = tmp_path / "trades.csv"
    store.append_trade(_row(1), path)
    lock = store.lock_path(path)
    assert lock.exists() and lock.read_bytes() == b""

    lock.unlink()
    store.append_trade(_row(2), path)            # 删了照样能写（会重建）
    assert len(store.load_trades(path)) == 2


def test_edits_inside_the_lock_see_the_freshest_table(tmp_path):
    """页面「保存修改」拿的是上一轮渲染时的旧快照。锁里必须重新读，
    否则另一个标签页在这期间记的那一笔会被整表重写抹掉。"""
    path = tmp_path / "trades.csv"
    first = store.append_trade(_row(1), path)
    stale = store.load_trades(path)              # 模拟"上一轮渲染读到的那份"
    second = store.append_trade(_row(2), path)   # 另一个标签页又记了一笔

    with store.locked(path):                     # app/journal_ui.save_edits 的形状
        fresh = store.load_trades(path)
        edits = pd.DataFrame([{"trade_id": first, "reason": "改过了"}])
        store.save_trades(store.apply_edits(fresh, edits), path)

    out = store.load_trades(path)
    assert set(out["trade_id"]) == {first, second}, "另一个标签页那笔被抹掉了"
    assert len(stale) == 1                       # 快照确实是旧的，前提成立


# ================================================================ 信号池

def test_concurrent_pool_adds_keep_every_symbol(tmp_path):
    """八个标签页同时点「＋ 加入」，八只都得进池子。
    少一只的后果不是少个名字——是**没有人管它的卖出信号**。"""
    from quant.config import load_local_universe, local_universe_path
    from quant.config_edit import write_local_universe

    # 真配置的副本：write_local_universe 写完会 load_settings 复核，玩具 YAML 过不了
    settings = tmp_path / "settings.yaml"
    settings.write_text(REAL_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")

    pool = ["600036", "601318", "600900", "000333", "600030", "600276", "601088", "600887"]
    lock_target = local_universe_path(settings)

    def add(i: int) -> None:
        with filelock.locked(lock_target):       # app/pool.add 的形状
            existing = load_local_universe(settings) or ("600519",)
            write_local_universe(settings, [*existing, pool[i]])

    assert _run_together(add, len(pool)) == []

    got = set(load_local_universe(settings) or ())
    assert got == {"600519", *pool}, f"丢了 {sorted({'600519', *pool} - got)}"


@pytest.mark.parametrize("fn", [filelock.lock_path, store.lock_path])
def test_lock_path_hangs_the_suffix_off_the_full_name(fn, tmp_path):
    """`trades.csv` → `trades.csv.lock`，不是 `trades.lock`：
    一眼看得出它是谁的锁（与 BACKUP_SUFFIX 同一个口径）。"""
    assert fn(tmp_path / "trades.csv").name == "trades.csv.lock"
