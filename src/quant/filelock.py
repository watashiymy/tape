"""跨进程/跨线程的写入互斥（v0.5.0 设计 §4.2）。

本项目有两份**读-改-写**的用户数据文件，两者都不可再生或不便重建：

- `journal/trades.csv`（真实成交记录）——`store.append_trade` 与页面的「保存修改」
- `config/universe.local.yaml`（关注池）——`config_edit.write_local_universe` 与
  「信号」页扫描表里的「＋ 加入」

Streamlit 的多个 session 是**同一进程里的并发线程**：用户开两个标签页同时提交，
两边各自读到同一份旧内容、各自整份重写，后写的把先写的悄悄冲掉。没有任何报错，
而 `journal` 那层 `.bak` 撤销这时也已经被覆盖成同样缺一笔的版本。

放在中立模块而不是 `journal/store.py` 里，是为了让 `quant.config_edit` 用得上
它却不必反过来依赖 `quant.journal`——那个方向的依赖没有任何道理。

## 为什么这次可以用锁文件

v0.2.0 设计曾明确拒绝过"锁文件"，理由是**陈旧锁没人回收**：进程被 kill 之后
磁盘上留着一个谁也不敢删的锁，下次启动直接死锁。

`fcntl.flock` 不是那种锁。它由**内核**维护并在 fd 关闭、进程退出（含 `kill -9`）
或机器重启时自动释放，磁盘上那个 `.lock` 文件只是个挂载点，内容永远为空、
随时可以删。当年拒绝的理由在这里不成立。

## 为什么锁旁挂文件而不是锁数据文件本身

两处落盘都是 `mkstemp` + `os.replace` 的原子写——正式文件的 inode 每次都被换掉。
锁在旧 fd 上的那把锁根本不看守新文件，等于没锁。旁挂的 `.lock` 从不被替换，
才是稳定的锁对象。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:                       # POSIX 独有；Windows 没有，见 `locked` 的退化说明
    import fcntl
except ImportError:        # pragma: no cover - 开发与实测环境都是 macOS
    fcntl = None           # type: ignore[assignment]

#: 旁挂锁文件的后缀。它是**纯运行期产物**，与不可再生的数据文件正相反——
#: 所以它进 .gitignore，删掉也不会丢任何东西。
LOCK_SUFFIX = ".lock"


def lock_path(path: str | Path) -> Path:
    """某份数据文件对应的锁文件位置（`trades.csv` → `trades.csv.lock`）。

    后缀拼在完整文件名后面而不是换掉扩展名，与 `BACKUP_SUFFIX` 同一个口径：
    一眼看得出它是谁的锁。
    """
    path = Path(path)
    return path.with_name(path.name + LOCK_SUFFIX)


@contextmanager
def locked(path: str | Path) -> Iterator[None]:
    """把「读 → 改 → 写」**整段**包成互斥临界区。

    只锁落盘那一步是**不管用**的：丢掉的是读到的那份旧快照，等两边都读完再互斥
    地写，照样是后写的赢。所以临界区必须从读开始。

    非 POSIX 平台（没有 `fcntl`）上退化成不加锁。这里如实退化而不是在 import 期
    崩掉：崩掉会让整个面板打不开，而本项目的开发与实测环境是 macOS、既有的单用户
    单进程用法本来也不会并发——代价远大于失去这一层保护。
    """
    if fcntl is None:                                   # pragma: no cover
        yield
        return
    lock = lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # 'a' 而不是 'w'：'w' 会截断文件，而截断发生在拿到锁之前——正好在别人持锁时。
    with open(lock, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
