"""全市场清单的本地落盘：symbol/name + as_of（v0.2.3）。

**为什么要落盘**：`provider.get_all_symbols(as_of)` 是每只票**名字**唯一的全量来源
（约 3000 行、分页拉取、实测固定 2-4 分钟一次），此前扫描脚本与面板各拉一次、
拉完即丢。两个后果：
1. 面板上的「名称」列大半空缺 —— 另一个离线来源是扫描 CSV，而它只记录**出了信号**
   的标的（实测 5 份 CSV 累计只有 313 只，每轮却要扫 3010 只）；
2. 每轮扫描白付一次那 2-4 分钟。

清单变动很慢（新股上市、退市、改名），存一份在本地、7 天内直接复用即可。

**与 BarCache 的关系**：同一类产物（放在 data/ 下、可以随手删掉、删了自动重建），
所以刻意沿用它的两条既有约定 —— 原子写（tmp + os.replace）、损坏文件**响亮**报错
并带上路径。后者是本模块最要紧的一条：静默返回 None 会让调用方以为"没有缓存"，
于是每次都全量重拉，一份坏文件可以躺几个月没人发现，用户只知道"怎么又慢了"。
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# 默认落点。与行情缓存 data/cache/ 同级，相对仓库根；调用方可以注入别的路径
# （面板把它绑到自己的 ROOT，测试一律用 tmp_path）。
SYMBOLS_PATH = Path("data") / "symbols.parquet"

COLUMNS = ("symbol", "name")
_AS_OF_KEY = b"as_of"          # parquet schema metadata 的键（必须是 bytes）
MAX_AGE_DAYS = 7               # 清单变动很慢，7 天内视为新鲜


def save_symbols(df: pd.DataFrame, as_of: date, path: str | Path = SYMBOLS_PATH) -> None:
    """把清单落盘：symbol/name 两列 + as_of 作为 parquet 的 schema 元信息。

    as_of 存成**元信息**而不是一列：它是整份文件的属性（"这是哪个交易日的清单"），
    存成列的话空清单就没地方放它，而且每次读都要多一步"取第一行、假定各行相同"。

    列不全一律拒写：少了 name 照样能写成一个合法 parquet，读回来是一份"没有名字的
    名称表"——正是本模块要修的那个静默失败的翻版，必须在写盘之前炸。
    """
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"清单缺列 {missing}（需要 {list(COLUMNS)}），拒绝落盘")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df.loc[:, list(COLUMNS)], preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}),
                                           _AS_OF_KEY: as_of.isoformat().encode()})
    # 先写临时文件再原子替换（同 BarCache.save）：拉了 2-4 分钟的清单写到一半被
    # Ctrl-C，不许留下半截 parquet 让下次启动撞上"文件损坏"。
    # 临时名走 mkstemp 而非固定后缀：扫描脚本与面板可能同时在跑。
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)      # 写失败时不留垃圾（成功后已被 replace 走）


def load_symbols(path: str | Path = SYMBOLS_PATH) -> tuple[pd.DataFrame, date] | None:
    """读回 (DataFrame[symbol, name], as_of)。文件不存在返回 None。

    **文件损坏（含缺 as_of 元信息）一律响亮抛 RuntimeError**，不静默返回 None：
    None 的含义是"还没拉过，去联网拉一次"，用它掩盖损坏会让每轮扫描都白付 2-4 分钟，
    而且永远不会有人发现那个文件是坏的。报错带上路径 + 怎么自愈（删掉即可重建）。
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        table = pq.read_table(path)
        raw = (table.schema.metadata or {}).get(_AS_OF_KEY)
        if raw is None:
            raise ValueError(f"缺少 {_AS_OF_KEY.decode()} 元信息")
        as_of = date.fromisoformat(raw.decode())
        df = table.to_pandas()
        missing = [c for c in COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"缺列 {missing}")
    except Exception as e:
        raise RuntimeError(
            f"全市场清单文件损坏: {path}（{type(e).__name__}: {e}），"
            f"删除该文件后重跑即可自动重拉") from e
    return df, as_of


def is_fresh(as_of: date, today: date, max_age_days: int = MAX_AGE_DAYS) -> bool:
    """这份清单还能不能直接用。纯函数，不碰文件系统。

    上下界都要判：
    - 上界 max_age_days：清单变动很慢（新股上市/退市/改名），7 天内的照用；
    - 下界 0：as_of 在**未来**说明机器时钟被改过、或文件是从别处搬来的。
      朴素写法 `(today - as_of).days <= max_age_days` 会把它判为新鲜（负数当然 ≤ 7），
      于是那份来路不明的清单被无限期复用——宁可多拉一次。
    """
    age = (today - as_of).days
    return 0 <= age <= max_age_days
