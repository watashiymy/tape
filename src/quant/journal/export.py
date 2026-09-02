"""筛选与导出（设计 §5.2 / §5.5）。纯函数：进去一张表，出来一张表或一串字节。

**导出的语义只有一条：所见即所得。** 页面上筛完了再点导出，拿到的必须正是
屏幕上那些行——所以这里不做"导出全部"的分支，`to_csv_bytes` / `to_excel_bytes`
收的就是 `filter_trades` 的结果，页面不许绕过它另拼一张表。

三个容易做错、而且**错了看不出来**的地方，各自都有字节级/类型级的测试守着：

1. **CSV 必须带 BOM**（`utf-8-sig`）。没有它，Excel 按 GBK 解，理由与名称全是乱码。
   而在 Python 里 BOM 有没有都读得对，所以这条只能在字节层面守（不是"看着没乱码"）。
2. **代码列必须是文本**。`000333` 一旦成了数字单元格就是 `333`，而表格看上去完全正常
   ——本项目已两次踩过这个坑。CSV 那边靠 `dtype`，xlsx 那边靠"写进去的是 str"。
3. **日期必须是 ISO 文本**。写成 datetime 时，Excel 按机器的区域设置显示成
   `8/3/26` 或 `03.08.2026`；同一份导出在两台机器上长得不一样，对账就没法做。

导出格式与 `journal/trades.csv` **逐字节同构**（列序、编码、日期写法都走
`store.serialize`）。这不是巧合而是刻意：用户误删一段记录时，最快的自救是把导出的
那份放回去。格式一旦分叉，那条自救路径就是假的。
"""
from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import date, datetime

import pandas as pd

from quant.journal import schema, store

#: 下载按钮的 MIME。给错的话浏览器可能改扩展名或直接在标签页里打开一片乱码。
CSV_MIME = "text/csv"
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 工作表名。中文的一目了然，而且测试按它取回工作表（不靠 "Sheet1" 这种默认名）。
SHEET_NAME = "交易日志"


def filter_trades(trades: pd.DataFrame, *,
                  start: date | None = None, end: date | None = None,
                  symbols: Iterable[str] | None = None,
                  kinds: Iterable[str] | None = None,
                  sources: Iterable[str] | None = None,
                  reason: str = "") -> pd.DataFrame:
    """按设计 §5.2 的五个条件筛选，返回**新表**（入参一个字都不动）。

    **空 = 不限制这一项**，不是"筛掉全部"。这条语义反了的话，一打开页面就是一张
    空表，用户只会以为日志丢了——而文件其实一个字节都没变。所以每一项都先判空。

    日期区间两端都算在内（差一天就会漏掉首尾两笔，而汇总数字照样给得出来）；
    `start > end` 给空表而**不**把两头调换：那是替用户改需求。

    索引重排是必须的：页面的编辑区按位置回读这张表，留着 1/3/4 这种带洞的索引
    会让"第 0 行"指向别人。
    """
    out = trades.copy()
    day = out[schema.DATE_COLUMN]

    if start is not None:
        out = out[day >= pd.Timestamp(start)]
        day = out[schema.DATE_COLUMN]
    if end is not None:
        out = out[day <= pd.Timestamp(end)]

    for column, wanted in (("symbol", symbols), ("kind", kinds), ("source", sources)):
        picked = [str(v) for v in (wanted or ())]
        if picked:
            # astype(str) 而不是直接 isin：代码列必须按**文本**比，
            # 否则 333 会匹配上 000333（或者反过来一个都匹配不上）。
            out = out[out[column].astype(str).isin(picked)]

    keyword = str(reason or "").strip()
    if keyword:
        # 只搜 `reason`，不顺手搜 `note`：设计 §2.3 把两个字段分开了，
        # 混在一起搜会让用户按备注词筛出一笔理由完全无关的记录，以为筛选坏了。
        out = out[out["reason"].astype(str).str.contains(keyword, case=False,
                                                         regex=False, na=False)]
    return out.reset_index(drop=True)


def to_csv_bytes(trades: pd.DataFrame) -> bytes:
    """当前筛选结果 → CSV 字节，编码 `utf-8-sig`（带 BOM）。

    一笔都没筛到时给一份**只有表头**的文件，不是 0 字节：后者在 Excel 里是
    "文件已损坏"，而实情只是没有符合条件的记录。
    """
    text = store.serialize(trades).to_csv(index=False, lineterminator="\n")
    # 与 store.save_trades 同一个编码常量：那边落盘、这边导出，两处分叉的话
    # "导出的文件能放回去"就不成立了。
    return text.encode(store.ENCODING)


def to_excel_bytes(trades: pd.DataFrame) -> bytes:
    """当前筛选结果 → xlsx 字节（引擎 openpyxl）。

    喂给 openpyxl 的是 `store.serialize` 的产物，于是代码与日期都是 **str**，
    落进单元格就是文本：`000333` 不会变成 `333`，日期在任何区域设置下都是同一串。
    """
    _require_openpyxl()
    buffer = io.BytesIO()
    # 不用 with ExcelWriter(...)：pandas 自己会关掉 writer，而 buffer 要留着取值。
    store.serialize(trades).to_excel(buffer, index=False, engine="openpyxl",
                                     sheet_name=SHEET_NAME)
    return buffer.getvalue()


def export_name(day: date | datetime, suffix: str) -> str:
    """下载文件名：`trades_20260828.csv`。

    带日期是必须的：连点三次导出，浏览器会存成 `trades(1).csv` / `trades(2).csv`
    这种谁也认不出来的名字。前缀与 `journal/trades.csv` 同名不是巧合——
    这两份文件格式相同，看到名字就知道能放回去。
    """
    return f"trades_{schema.parse_date(day):%Y%m%d}.{suffix}"


def _require_openpyxl() -> None:
    """依赖缺失时说清装什么。

    pandas 原生那句 "Missing optional dependency 'openpyxl'" 不会提到本项目的装法，
    而这条路径恰好只在别人 clone 之后**第一次点导出**时出现——那时最需要一句能照做的话。
    """
    try:
        import openpyxl  # noqa: F401  只为确认装了
    except ImportError as e:
        raise RuntimeError(
            "导出 Excel 需要 openpyxl（它是可选依赖，不在主依赖里）："
            "请在项目根目录跑 `.venv/bin/pip install -e \".[excel]\"` 后重试；"
            "CSV 导出不依赖它，随时可用。"
        ) from e
