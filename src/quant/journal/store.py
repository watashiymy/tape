"""交易日志的落盘：`journal/trades.csv` 的读、写、追加与 trade_id 生成（设计 §2.1）。

**与行情缓存的根本差别：这份文件不可再生。** `data/cache/` 坏了删掉重拉即可，
交易记录删了就永远没了。所以这里在 BarCache 的两条既有约定之上再收紧一层：

- **原子写**（mkstemp + `os.replace`，同 BarCache.save）：写到一半被 Ctrl-C 或
  磁盘写满，磁盘上要么是旧的完整文件、要么是新的完整文件，绝不会是半截；
  临时名必须进程唯一——面板与脚本共用同一份日志是常态。
- **坏文件响亮报错**，但**自愈办法不是"删掉重建"**：那句话照抄过来会直接毁掉
  用户的全部交易记录。这里提示的是 `git checkout`——日志纳入版本控制
  （设计 §2.1）图的就是这个：git 历史等于免费的审计轨迹与撤销能力。
- **`dtype={"symbol": str}` 全程强制**：本项目已两次踩过 `000333` → `333`，
  表现只是"名称又空了""持仓对不上"，不会有任何报错。读、写、追加三条路径
  都必须走同一个 `_coerce`，漏一条就等于没做。
"""
from __future__ import annotations

import itertools
import os
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from quant.journal import schema

#: 默认落点，相对仓库根（同 symbols.SYMBOLS_PATH 的约定）。
#: 刻意不放 data/：那里混着 cache/ 与 symbols.parquet（都已 gitignore），
#: 而这份是**用户数据，要留住**。
TRADES_PATH = Path("journal") / "trades.csv"

#: 写盘编码固定带 BOM。设计 §2.1 选 CSV 的理由之一就是"可直接用 Excel 打开"，
#: 没有 BOM 的话 Excel 按 GBK 解，中文理由全是乱码。读盘用同一个编码名
#: （utf-8-sig 解码时 BOM 可有可无），于是用户用 vim/git 手改过、存回来没有 BOM
#: 的文件照样打得开。
ENCODING = "utf-8-sig"


def empty_trades() -> pd.DataFrame:
    """一张列齐、dtype 正确的空表。

    dtype 必须与读回来的表**完全一致**：pandas 3.0 的 concat 不再忽略空块的 dtype，
    拼一张 object 空表会把整列污染成 object，而后续算术照样不报错
    （BarCache.merge 里已经踩过同一个坑）。
    """
    return _coerce(pd.DataFrame({c: pd.Series([], dtype="object") for c in schema.COLUMNS}))


def load_trades(path: str | Path = TRADES_PATH) -> pd.DataFrame:
    """读回全部记录。文件不存在返回空表（不是 None）。

    返回 None 会让调用方到处写 `if df is None`，早晚漏一处；而"还没记过任何一笔"
    是每个新用户的第一次打开，属于正常状态，不该由每个调用点各自处理。
    """
    path = Path(path)
    if not path.exists():
        return empty_trades()
    try:
        raw = pd.read_csv(
            path,
            dtype={c: str for c in schema.STR_COLUMNS},   # 前导零的命门
            keep_default_na=False, na_values=[""],        # 备注写 "NA"/"nan" 是文本，不是缺失
            encoding=ENCODING,
        )
        missing = [c for c in schema.COLUMNS if c not in raw.columns]
        extra = [c for c in raw.columns if c not in schema.COLUMNS]
        if missing or extra:
            # 少一列若静默补 NaN，盈亏会少算全部费用且看着完全正常；
            # 多一列若静默忽略，下一次记账整表重写就把用户自己加的那列删了。
            raise ValueError(f"列不匹配：缺少 {missing}，多出 {extra}")
        return _coerce(raw)
    except Exception as e:
        raise RuntimeError(
            f"交易日志文件损坏: {path}（{type(e).__name__}: {e}）。"
            f"这份文件不可再生，**不要删**——先用 `git checkout -- {path}` 找回上一次提交的版本，"
            f"或用文本编辑器按表头（{','.join(schema.COLUMNS)}）修好后重试。"
        ) from e


def save_trades(df: pd.DataFrame, path: str | Path = TRADES_PATH) -> None:
    """整表原子落盘。列不对一律拒写（沿用 save_symbols 的约定）。

    拒写而不是"尽力而为"：列不对的表写进去，就是把一个坏文件留给下一次启动，
    而那时用户已经不记得是哪一步弄坏的。
    """
    missing = [c for c in schema.COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in schema.COLUMNS]
    if missing or extra:
        raise ValueError(f"交易日志列不匹配，拒绝落盘：缺少 {missing}，多出 {extra}")

    out = _coerce(df)
    out[schema.DATE_COLUMN] = out[schema.DATE_COLUMN].dt.strftime("%Y-%m-%d")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        # lineterminator 钉成 \n：换行符随平台变的话，一次记账的 git diff 是整个文件。
        out.to_csv(tmp, index=False, encoding=ENCODING, lineterminator="\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)   # 写失败时不留垃圾（成功后已被 replace 走）


def append_trade(row: Mapping, path: str | Path = TRADES_PATH, *,
                 now: datetime | None = None) -> str:
    """追加一笔，返回新生成的 trade_id。

    走"读回 → 拼一行 → 整表原子重写"，不用 append 模式直接往文件尾巴上写：
    整表重写才能保证列顺序与编码始终如一，也才能让中断后的文件始终是完整的。
    量级只有一年几十到几百行，代价可以忽略。
    """
    unknown = [k for k in row if k not in schema.COLUMNS]
    if unknown:
        raise ValueError(f"未知字段 {unknown}，可用字段：{list(schema.COLUMNS)}")
    if str(row.get("trade_id") or "").strip():
        # 主键只能由 store 发。放行页面自带的 ID，唯一性就没了，
        # 而重复主键的表现是"删一笔少两笔"。
        raise ValueError("trade_id 由 store 生成，不要自带")

    df = load_trades(path)
    trade_id = next_trade_id(df["trade_id"], now or datetime.now())
    record = {c: row.get(c) for c in schema.COLUMNS} | {"trade_id": trade_id}
    merged = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    save_trades(merged, path)
    return trade_id


def next_trade_id(existing, now: datetime) -> str:
    """`YYYYMMDD-HHMMSS-序号`，序号在同一秒内递增。

    两个刻意的决定：

    - **用录入时刻，不用成交日期**：事后补记 2020 年的老交易时，ID 记的是
      "什么时候录的"。拿成交日期当 ID 的话，用户改一次日期就等于换了主键，
      而主键是编辑/删除的唯一定位依据（设计 §2.3 明写"不靠行号"）。
    - **同一秒也要能发第二个**：从信号页一键记账很容易一秒内点两次。
      序号取"当前文件里没被占用的最小值"，因此已有行的 ID 永远不会被重编——
      重编号会让"删第 3 行"删掉别人。
    """
    used = {str(t) for t in existing}
    prefix = now.strftime("%Y%m%d-%H%M%S")
    for seq in itertools.count(1):
        trade_id = f"{prefix}-{seq:03d}"
        if trade_id not in used:
            return trade_id
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- dtype 归一

def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """把任意来源（CSV / 表单 dict / 拼接结果）的表归一成同一套 dtype 与空值约定。

    读、写、追加三条路径共用这一处：只要有一条绕过去，前导零就会在那条路径上
    被悄悄吃掉——而那正是它两次逃过检查的方式。
    """
    out = df.loc[:, list(schema.COLUMNS)].copy()
    for col in schema.STR_COLUMNS:
        out[col] = out[col].fillna("").astype(str)
    for col in schema.NUM_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="raise").astype("float64")
    out[schema.DATE_COLUMN] = _to_datetime(out[schema.DATE_COLUMN])
    return out.reset_index(drop=True)


def _to_datetime(s: pd.Series) -> pd.Series:
    """date 列 → datetime64[ns]。无法解析一律**抛错**，不许静默变 NaT。

    静默变 NaT 的话，这笔交易会从所有按日期筛选的视图里消失，
    而汇总数字照样给得出来——又一个"看着正常的错数字"。

    先把每个值转成 ISO 字符串再按固定格式解析：混着 date/Timestamp/str 时，
    pandas 会逐元素猜格式，`2026/8/9` 这种手改值可能被猜成 8 月 9 日也可能是 9 月 8 日。
    """
    def one(v):
        if v is None or v is pd.NaT:
            return None
        if isinstance(v, str):
            return v.strip() or None
        if isinstance(v, datetime):        # 必须先于 date：datetime 是 date 的子类
            return v.strftime("%Y-%m-%d")
        if isinstance(v, date):
            return v.isoformat()
        if pd.isna(v):
            return None
        raise TypeError(f"{schema.DATE_COLUMN} 列不认识的值 {v!r}")

    return pd.to_datetime(s.map(one), format="%Y-%m-%d").astype("datetime64[ns]")
