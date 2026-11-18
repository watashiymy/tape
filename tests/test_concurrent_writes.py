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


def _load_pool():
    """单独加载 app/pool.py（同 tests/test_dashboard_universe.py 的做法）。
    pool.py 刻意不 import 任何 app 内部模块，所以能这么直接加载。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("qd_pool_conc", ROOT / "app" / "pool.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_pool_conc"] = mod
    spec.loader.exec_module(mod)
    return mod


_pool = _load_pool()


def _seed_universe(settings_path) -> tuple[str, ...]:
    """settings.yaml 里那份种子池（本地覆盖文件还不存在时生效的那批）。"""
    import yaml
    return tuple(yaml.safe_load(Path(settings_path).read_text(encoding="utf-8"))["universe"])

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

    assert _run_together(unlocked_append) == [], \
        "线程里抛了异常——那不叫'丢单'，这条对照当场失去意义"
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


# ================================================================ 交易日志：删除（2026-11-18）
#
# 「记账」页的删除（每行一个按钮 → 确认框 → app/journal_ui.delete_trades）也是「读回整份 → 改 →
# 整份重写」。独立审查做变异时发现：把它身上的锁去掉、或者把读文件挪到锁外面，全量测试一条都不红
# ——上面那几条测的是 store 自己的函数和一段照抄的"形状"，没有哪条真的并发地调过这个函数。

def _load_journal_ui():
    """单独加载 app/journal_ui.py（它刻意不 import 任何 app 内部模块，同 _load_pool 的做法）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("qd_journal_ui_conc", ROOT / "app" / "journal_ui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_journal_ui_conc"] = mod
    spec.loader.exec_module(mod)
    return mod


_journal_ui = _load_journal_ui()


def _seed_trades(path: Path, n: int) -> list[str]:
    """落一份 n 条记录的日志，返回各条的记录号。"""
    rows = [_row(i) | {"trade_id": f"T{i:03d}"} for i in range(n)]
    store.save_trades(pd.DataFrame(rows, columns=list(store.schema.COLUMNS)), path)
    return [row["trade_id"] for row in rows]


def test_concurrent_deletes_all_take_effect(tmp_path, monkeypatch):
    """八个标签页同时各删一条，八条都得真的删掉。不互斥的话，各自读到同一份旧表、各自整表重写，
    后写的把先写的那次删除冲掉：删掉的记录又回到账上，而每个标签页都提示"已删除"。"""
    monkeypatch.setattr(_journal_ui, "flash", lambda level, text: None)   # 线程里没有会话可存提示
    path = tmp_path / "trades.csv"
    ids = _seed_trades(path, WORKERS + 1)
    done: list[bool] = [False] * WORKERS

    def delete(i: int) -> None:
        done[i] = _journal_ui.delete_trades([ids[i]], path=path, today=date(2026, 9, 3))

    assert _run_together(delete) == []
    assert done == [True] * WORKERS, "有的标签页没删成"
    left = list(store.load_trades(path)["trade_id"])
    assert left == [ids[-1]], f"删掉的记录又回来了 {len(left) - 1} 条: {left}"


def test_the_unlocked_delete_really_does_bring_records_back(tmp_path):
    """**证明上一条测得到东西**：绕开锁做同一件事，删掉的记录必然有回来的。
    这条一旦变绿，说明并发根本没发生，上面那条也就不再证明任何事。"""
    path = tmp_path / "trades.csv"
    ids = _seed_trades(path, WORKERS + 1)

    def unlocked_delete(i: int) -> None:
        df = store.load_trades(path)                                      # 读
        merged = store.apply_edits(df, pd.DataFrame(columns=["trade_id"]), delete_ids=[ids[i]])
        store.save_trades(merged, path)                                   # 写（中间没有互斥）

    assert _run_together(unlocked_delete) == [], \
        "线程里抛了异常——那不叫'删掉的又回来'，这条对照当场失去意义"
    assert len(store.load_trades(path)) > 1, \
        "没加锁却一条没回来——并发没真正发生，上面那条测试是空跑"


# ================================================================ 信号池

def test_concurrent_pool_adds_keep_every_symbol(tmp_path):
    """八个标签页同时点「＋ 加入」，八只都得进池子。
    少一只的后果不是少个名字——是**没有人管它的卖出信号**。

    这里调**真的 `pool.add`**，不复刻它的形状：复刻出来的闭包只能证明"我写的这段
    加锁逻辑是对的"，证明不了产品里那段加了锁——那正是这条测试要守的东西。
    """
    from quant.config import load_local_universe

    # 真配置的副本：write_local_universe 写完会 load_settings 复核，玩具 YAML 过不了
    settings = tmp_path / "settings.yaml"
    settings.write_text(REAL_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")

    # 必须挑**种子池之外**的代码：pool.add 对已在池中的标的直接短路不写文件，
    # 拿池内的票来测等于八个线程什么都没做，测试会假绿。
    seed = set(load_local_universe(settings) or ()) | set(_seed_universe(settings))
    picks = [s for s in ("600009", "600028", "600050", "600104", "600585",
                         "600690", "601006", "601111", "601857", "601988")
             if s not in seed][:8]
    assert len(picks) == 8, f"备选里可用的不足 8 只：{picks}"
    allowed = frozenset({*seed, *picks})         # 扫描池白名单：这批都算合法

    assert _run_together(lambda i: _pool.add(picks[i], config_path=settings,
                                             allowed=allowed), len(picks)) == []

    got = set(load_local_universe(settings) or ())
    assert set(picks) <= got, f"丢了 {sorted(set(picks) - got)}"
    assert seed <= got, "原有的池子成员被冲掉了"


@pytest.mark.parametrize("fn", [filelock.lock_path, store.lock_path])
def test_lock_path_hangs_the_suffix_off_the_full_name(fn, tmp_path):
    """`trades.csv` → `trades.csv.lock`，不是 `trades.lock`：
    一眼看得出它是谁的锁（与 BACKUP_SUFFIX 同一个口径）。"""
    assert fn(tmp_path / "trades.csv").name == "trades.csv.lock"
