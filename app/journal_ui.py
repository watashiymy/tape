"""交易日志两个子页（记账 / 持仓与盈亏）共享的编排层（v0.3.0 §5，v0.3.1 拆页）：
控件 key、预填、表格、校验与写盘的接线。

**分工**（真正的逻辑都在 src/，这里只是编排 + Streamlit 接线）：
- `quant.journal.schema`：字段、默认值（费用按成本模型自动算）、校验的阻断/警告语义；
- `quant.journal.store`：原子落盘、trade_id、按主键改删；
- `quant.journal.pnl`：FIFO 配对、持仓、盈亏、按来源分组；
- `quant.journal.export`：筛选与 CSV/Excel 导出；
- 本模块：把上面几个接到控件上，并把结果变成一句人话。

**本模块刻意不 import 任何 app 内部模块**（ui / theme / guide / pool）：路径一律由
调用方传进来。理由与 app/pool.py 完全相同——"改错或删错用户的交易记录"是本功能
最贵的故障（那份文件不可再生），那部分逻辑必须能脱离 Streamlit 运行时被直接单测
（tests/test_dashboard_journal.py 就是这么加载它的）。

代价是与 pool.latest_close 有几行相似的"读缓存最后一根收盘价"。刻意不去复用：
本模块还要那份 K 线算**当日 [low, high]**（录入时的价格区间校验），
所以缓存读取这条路在这里本来就得有一份；反过来去 import pool 只会把
"信号池的读写编排"变成日志页的依赖，两个功能从此绑在一起。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from quant.config import Costs
from quant.data.cache import BarCache
from quant.journal import export, pnl, schema, store
from quant.report import fmt
from quant.strategy import REGISTRY, strategy_label

# ---------------------------------------------------------------- 中文标签
# 四种记录类型都要有标签：漏一个，那一档在筛选框里就是个裸 'adjust'。
# 长句解释（各自的含义与为什么必须有）在 app/guide.py 与 README 里，这里只放短标签。
KIND_LABELS = {"buy": "买入", "sell": "卖出", "adjust": "股数调整", "dividend": "现金分红"}
# 策略侧的来源标签派生自注册表（v0.4.0 M1）：新策略自动获得日志侧显示名，
# 不再有"加了策略忘了这里"的静默缺口；两个非策略项手写（它们不在注册表里）。
SOURCE_LABELS = {**{key: f"{strategy_label(key)}信号" for key in REGISTRY},
                 "discretionary": "自主决策", "other": "其他"}

#: 主路径与"其他记录"的分工（设计 §2.2）：买卖是日常，另两种收进折叠区，
#: 但**必须有入口**——没有它们，送股与分红会被漏记，而那会让持仓与券商对不上、
#: 此后每一笔卖出的 FIFO 配对全错，且不会报错。
TRADE_KINDS = ("buy", "sell")
OTHER_KINDS = ("adjust", "dividend")

#: 日期输入框的下限。**不能用 Streamlit 的默认值**（value 往前十年）：那会让
#: "补记 2014 年那笔"直接选不出日期，而且没有任何提示——一个静默的功能缺口。
#: 取上交所开市日，比任何 A 股交易都早，等于"没有下限"但仍然挡住手滑打成 1900 年。
EARLIEST_DAY = date(1990, 12, 19)

#: 默认来源是**自主决策**，不是任何一个策略名。默认成 ma_cross 会把用户自己拍脑袋
#: 做的交易记成"照系统信号做的"，而 source 字段存在的全部意义就是把这两类分开比较
#: （设计 §2.3）——默认值填错，那个比较就永远是错的，还看不出来。
DEFAULT_SOURCE = "discretionary"

# ---------------------------------------------------------------- 控件 key
# 测试按 key 取控件，别改字面量。
KIND_KEY = "journal_kind"
DATE_KEY = "journal_date"
TIME_KEY = "journal_time"
SYMBOL_KEY = "journal_symbol"
SHARES_KEY = "journal_shares"
PRICE_KEY = "journal_price"
FEE_KEY = "journal_fee"
TAX_KEY = "journal_tax"
SOURCE_KEY = "journal_source"
STOP_KEY = "journal_stop_plan"
REASON_KEY = "journal_reason"
NOTE_KEY = "journal_note"
SUBMIT_KEY = "journal_submit"

OTHER_KIND_KEY = "journal_other_kind"
OTHER_DATE_KEY = "journal_other_date"
OTHER_SYMBOL_KEY = "journal_other_symbol"
OTHER_SHARES_KEY = "journal_other_shares"
OTHER_AMOUNT_KEY = "journal_other_amount"
OTHER_SOURCE_KEY = "journal_other_source"
OTHER_REASON_KEY = "journal_other_reason"
OTHER_SUBMIT_KEY = "journal_other_submit"

FILTER_START_KEY = "journal_filter_start"
FILTER_END_KEY = "journal_filter_end"
FILTER_SYMBOL_KEY = "journal_filter_symbols"
FILTER_KIND_KEY = "journal_filter_kinds"
FILTER_SOURCE_KEY = "journal_filter_sources"
FILTER_REASON_KEY = "journal_filter_reason"

EDITOR_KEY = "journal_editor"
SAVE_KEY = "journal_save"
CSV_KEY = "journal_export_csv"
XLSX_KEY = "journal_export_xlsx"

#: 费用自动算的"依据签名"。见 sync_auto_costs。
COST_SIG_KEY = "journal_cost_signature"
#: 一键记账带过来的名称，按 {代码: 名称} 存——代码一改就自动失效，不会串味。
NAME_HINT_KEY = "journal_name_hint"
#: 回调里写下、下一轮渲染时读出的提示（见 flash / show_flash）。
FLASH_KEY = "journal_flash"
#: 一键记账的落点：预填内容 + "该跳到日志页了"这个标志。
PREFILL_KEY = "journal_prefill"
JUMP_KEY = "journal_jump"

# 「信号」与扫描表里那一列（设计 §5.1 的一键记账）。
RECORD_COLUMN = "记账"
RECORD_LABEL = "＋ 记一笔"
SIGNAL_CLICK_KEY = "journal_signal_record_click"
SCAN_CLICK_KEY = "journal_scan_record_click"
RECORD_HELP = ("按这一行预填一笔交易（代码/名称/日期/方向/来源）并跳到「记账」页。"
               "**不预填成交价**：信号那天的收盘价不是你的成交价。")

# 编辑区那一列勾选框。删除刻意是两步（勾选 + 保存）：这份文件不可再生，
# 一键删除误点一下就没了，而那次改动可能还没提交进 git。
DELETE_COLUMN = "删除"

# 持仓表的列名（测试按它取值，别改字面量）。
SYMBOL_COLUMN = "代码"
NAME_COLUMN = "名称"
SHARES_COLUMN = "股数"
COST_COLUMN = "持仓成本"
UNIT_COST_COLUMN = "单位成本"
PRICE_COLUMN = "最新价"
FLOAT_PNL_COLUMN = "浮动盈亏"
ISSUE_COLUMN = "数据一致性"

#: 日志表的中文表头。缺一列只是表头多个裸 'stop_plan'，但那正是"配好了"的错觉。
LOG_LABELS = {
    "trade_id": "记录号", "date": "日期", "time": "时刻", "symbol": "代码",
    "name": "名称", "kind": "类型", "shares": "股数", "price": "成交价",
    "amount": "金额", "fee": "佣金", "tax": "印花税", "source": "来源",
    "stop_plan": "计划止损", "reason": "理由", "note": "备注",
}

#: 一致性标记的短标签（长句在 pnl 的 Inconsistency.message 里，会单独显眼地列出来）。
ISSUE_LABELS = {
    pnl.OVERSELL: "卖超", pnl.ADJUST_WITHOUT_POSITION: "调整无持仓",
    pnl.NEGATIVE_SHARES: "股数为负", pnl.UNUSABLE_ROW: "有行读不懂",
}

#: 读缓存能出的错：文件不在（None）、损坏（RuntimeError）、不是 parquet（OSError/
#: ValueError）。一律降级成"没有区间/没有最新价"——录入表单不能因为一个坏缓存打不开。
CACHE_ERRORS = (RuntimeError, OSError, ValueError)


# ================================================================ 提示（跨轮次）

def flash(level: str, text: str) -> None:
    """把结论留到**下一轮**渲染时再说（level: "ok" / "warn" / "error"）。

    与 pool.flash 同一个理由：写盘之后要 st.rerun() 让表格跟着变，而那会把这一轮
    刚画的提示丢掉。刻意做成**列表**：一笔记录可以同时"记下了"+"价格越界"+"卖超"，
    只留最后一条的话，用户会漏看正好是最该看的那条。
    """
    st.session_state.setdefault(FLASH_KEY, []).append((level, text))


def show_flash() -> None:
    """渲染并清掉上一轮留下的提示。

    成功走 toast（轻，不占版面）；**警告与错误一律留在页面上**——设计 §3 明写
    "错误必须显眼、持续可见，不能记完就沉没"，toast 几秒就没了。
    """
    messages = st.session_state.pop(FLASH_KEY, [])
    for level, text in messages:
        if level == "error":
            st.error(text)
        elif level == "warn":
            st.warning(text)
        else:
            st.toast(text)


# ================================================================ 一键记账（§5.1）

def prefills(signals: pd.DataFrame, names: Mapping[str, str] | None = None
             ) -> tuple[dict, ...]:
    """把信号/扫描表的每一行折成一份"预填"：代码 / 名称 / 日期 / 方向 / 来源。

    **不含成交价**：信号那天的收盘价不是用户的成交价，预填上去就是一个看着正常的
    错数字——而本项目一路在防的正是这种失败。

    两处容错都是真会发生的：
    - 扫描 CSV 没有 `action` 列（全市场扫描只报当日新触发的 BUY）→ 一律 buy；
    - 策略名不在 `schema.SOURCES` 里（旧产物里已下架策略的键；"新策略没跟上"
      自 v0.4.0 起不存在了——SOURCES 派生自注册表）→ 退到 `other`，
      而不是让来源选择框收到一个不在选项里的值当场崩掉。
    """
    lookup = dict(names or {})
    out: list[dict] = []
    for row in signals.to_dict("records"):
        symbol = str(row.get("symbol") or "").strip()
        strategy = str(row.get("strategy") or "").strip()
        action = str(row.get("action") or "buy").strip().lower()
        out.append({
            "symbol": symbol,
            # 查不到名称就留空：填"未知"看着像个名字（同 apply_defaults 的约定）。
            "name": str(row.get("name") or lookup.get(symbol) or "").strip(),
            "date": schema.parse_date(row.get("date")),
            "kind": "sell" if action == "sell" else "buy",
            "source": strategy if strategy in schema.SOURCES else "other",
        })
    return tuple(out)


def record_table(df: pd.DataFrame) -> pd.DataFrame:
    """在表的**末列**加上「记一笔」。加在末列的理由同 pool.scan_table：
    列序是用户已经熟悉的，在中间插一列会把每一列的位置都挪一格。"""
    out = df.copy()
    out[RECORD_COLUMN] = [RECORD_LABEL] * len(df)
    return out


def record_column(rows: Sequence[Mapping], click_key: str):
    """「记一笔」那一列的列配置。`rows` 是**渲染那一轮**的预填行序（由 args 传入）：
    回调跑在下一轮脚本之前，那时页面上的 DataFrame 已经不存在了，只能靠它认行。"""
    return st.column_config.ButtonColumn(
        RECORD_COLUMN, on_click=on_record, args=(tuple(rows), click_key),
        key=click_key, help=RECORD_HELP)


def on_record(rows: Sequence[Mapping], click_key: str) -> None:
    """「记一笔」的 on_click：写下预填 + 请求跳页。这一轮不画任何东西
    （回调跑在重跑之前，画了也会被清掉）。"""
    click = st.session_state.get(click_key)
    if click is None:
        return
    row = int(click["row"])
    if not 0 <= row < len(rows):
        # 行号越界一律当没点过：表格与回调之间数据可能已经变了（另一个标签页刚跑完扫描），
        # 按错位的行号预填一笔别人的交易是最坏的结果。
        return
    st.session_state[PREFILL_KEY] = dict(rows[row])
    st.session_state[JUMP_KEY] = True


def jump_if_requested(page) -> None:
    """有人点过「记一笔」就切到「记账」页（v0.3.1 拆页后录入住在那里）。

    必须由 dashboard.py 在 `st.navigation(...)` **之后**调用：st.switch_page 只认
    已注册的页，而注册发生在 st.navigation 里。放在回调里也不行——那时脚本还没重跑。
    """
    if st.session_state.pop(JUMP_KEY, False):
        st.switch_page(page)


def apply_prefill() -> dict | None:
    """把预填写进各控件的 session_state，返回它（没有预填时返回 None）。

    **必须在控件创建之前调用**：Streamlit 只允许在 widget 实例化**前**改它的
    session_state，晚一步就是异常。
    """
    prefill = st.session_state.pop(PREFILL_KEY, None)
    if not prefill:
        return None
    st.session_state[SYMBOL_KEY] = str(prefill.get("symbol") or "")
    st.session_state[KIND_KEY] = prefill.get("kind") or "buy"
    st.session_state[SOURCE_KEY] = prefill.get("source") or DEFAULT_SOURCE
    if (day := schema.parse_date(prefill.get("date"))) is not None:
        st.session_state[DATE_KEY] = day
    if name := str(prefill.get("name") or "").strip():
        st.session_state[NAME_HINT_KEY] = {st.session_state[SYMBOL_KEY]: name}
    # 类型/日期都换了，费用必须重算——留着上一笔的数字是最难发现的一种错。
    st.session_state.pop(COST_SIG_KEY, None)
    return prefill


def hinted_name(symbol: str, names: Mapping[str, str] | None = None) -> str:
    """名称：先查本地清单，查不到就用一键记账那一行带过来的那个。

    按**代码**配对，所以用户一改代码提示就自动失效——不会把 A 的名字挂到 B 上。
    两边都没有就返回空串：绝不编一个"未知"，那看着像个名字。
    """
    hint = st.session_state.get(NAME_HINT_KEY) or {}
    return str((names or {}).get(symbol) or hint.get(symbol) or "").strip()


# ================================================================ 本地行情（离线可用）

def bars(symbol: str, cache_dir: str | Path) -> pd.DataFrame | None:
    """本地缓存里这只票的日线；没有/坏了一律返回 None。

    坏文件在这里**不响亮报错**：K 线页会为同一个文件明确报错，而这里只是为了
    带出一个价格区间——为此把整个录入表单打没，代价完全不对等。
    """
    try:
        df = BarCache(cache_dir).load(symbol)
    except CACHE_ERRORS:
        return None
    if df is None or df.empty:
        return None
    return df.sort_index()


def price_range(symbol: str, day: date | None,
                cache_dir: str | Path) -> tuple[float, float] | None:
    """该标的该日的 [low, high]；缓存里没有那一天就返回 None。

    **拿不到就什么都不说**（同 schema.Context 的约定）：编一条"区间未知所以可疑"
    的警告，只会训练用户忽略所有警告。
    """
    if not symbol or day is None:
        return None
    df = bars(symbol, cache_dir)
    if df is None or not {"low", "high"} <= set(df.columns):
        return None
    picked = df[df.index.normalize() == pd.Timestamp(day)]
    if picked.empty:
        return None
    return float(picked["low"].iloc[-1]), float(picked["high"].iloc[-1])


def latest_price(symbol: str, cache_dir: str | Path) -> float:
    """缓存里最后一根 K 线的收盘价；没有就是 NaN。

    **不许返回 0**：0 是一个看着像数据的假价格，会让浮动盈亏变成"亏掉全部本金"。
    缺值一路走到 positions_table 里被 fmt 变成 —（设计 §4.3 明写的那条局限）。
    """
    df = bars(symbol, cache_dir)
    if df is None or "close" not in df.columns:
        return float("nan")
    return float(df["close"].iloc[-1])


# ================================================================ 录入（§5.1）

def sync_auto_costs(*, kind: str, day: date | None, shares, price,
                    costs: Costs) -> None:
    """按成本模型把佣金与印花税**预填**进两个输入框，依据一变就重算。

    预填而不是留空：留空时用户不知道该填什么，随手填 0 会让盈亏系统性偏高
    （设计 §5.1 明写"费用自动按成本模型算好可改"）。

    "依据"是 (类型, 日期, 股数, 价格) 这个签名：
    - 签名没变 → **一个字都不动**，用户改成券商实际值之后不会被覆盖；
    - 签名变了 → 重算。改了价格却留着上一次的费用，是最难发现的一种错——
      表格里那一行看着完全正常。

    印花税按**成交日**取税率（apply_defaults 已经这么做），补记 2023 年的卖出
    才会用当时的千分之一。
    """
    signature = (kind, str(day), shares, price)
    if st.session_state.get(COST_SIG_KEY) == signature:
        return
    st.session_state[COST_SIG_KEY] = signature
    auto = schema.apply_defaults(
        {"kind": kind, "date": day, "shares": shares, "price": price}, costs=costs)
    st.session_state[FEE_KEY] = float(auto["fee"] or 0.0)
    st.session_state[TAX_KEY] = float(auto["tax"] or 0.0)


def submit(row: Mapping, *, path: str | Path, costs: Costs,
           names: Mapping[str, str] | None = None,
           price_range_: tuple[float, float] | None = None,
           position_shares: float | None = None,
           today: date) -> bool:
    """校验 → 落盘 → 把结论留给下一轮。返回"写进去了没有"。

    两件事在这里定死（设计 §3）：

    - **只有阻断项拦得住落盘**。价格越界、非整手、卖超、代码不在清单里一律照记，
      因为日志的首要职责是如实记录发生了什么，而不是替用户否定现实；
    - **警告必须说出来**，而且走 st.warning 留在页面上而不是 toast——
      记完就沉没的警告等于没有警告。
    """
    record = schema.apply_defaults(row, costs=costs, names=names)
    issues = schema.validate_trade(record, today=today, context=schema.Context(
        names=names, price_range=price_range_, position_shares=position_shares))
    if schema.has_blocking(issues):
        flash("error", "这一笔**没有**记下来（下面这些必须先改）：" +
              "；".join(i.message for i in schema.blocking_issues(issues)))
        return False
    try:
        trade_id = store.append_trade(record, path)
    except (OSError, ValueError, RuntimeError) as e:
        # 写盘失败必须如实说，不能只 toast 一句"已记录"：用户会以为记下了。
        flash("error", f"落盘失败（{type(e).__name__}: {e}），这一笔没有记下来。")
        return False
    flash("ok", f"已记下 {KIND_LABELS.get(record['kind'], record['kind'])} "
                f"{record['symbol']}（记录号 {trade_id}）")
    for issue in schema.warnings(issues):
        flash("warn", f"{trade_id} 已记下，但请复核：{issue.message}")
    clear_entry_form()
    return True


#: 记完一笔要清掉的录入区控件（v0.5.0）。**刻意不清 `kind` 与 `source`**：
#: 一晚上连着记的往往是同方向、同来源的几笔，清了反而每次都要重选。
_ENTRY_KEYS_TO_CLEAR = (SYMBOL_KEY, SHARES_KEY, PRICE_KEY, FEE_KEY, TAX_KEY,
                        STOP_KEY, REASON_KEY, NOTE_KEY, TIME_KEY, COST_SIG_KEY)


def clear_entry_form() -> None:
    """落盘成功后清掉录入区。

    不清的代价有两个，后一个更贵：

    1. 按钮照样能点，且表单一格没变——不确定记上没有的人再点一下，就静默多一条
       一模一样的记录（`trade_id` 不同，所以没有任何重复主键的报错拦得住）。
    2. **同一晚记第二笔时，「股数」框里还留着上一笔的数**。改了代码与成交价之后
       `sync_auto_costs` 的签名变了，佣金会照着**留下来的那个股数**重算一遍，
       于是那一行看着完全自洽——正是本模块注释里点名的"最难发现的一种错"，
       而它会静默污染 FIFO 配对与之后所有盈亏。

    日期不清（回到"今天"由控件的 value 决定，而补记旧交易时用户刚选好的那天
    清掉反而添乱）；kind / source 也不清，见 `_ENTRY_KEYS_TO_CLEAR`。
    """
    for key in _ENTRY_KEYS_TO_CLEAR:
        st.session_state.pop(key, None)


# ================================================================ 编辑与删除（§5.2）

def editor_frame(filtered: pd.DataFrame) -> pd.DataFrame:
    """编辑区的表：筛选结果 + 末列一个「删除」勾选框。"""
    out = filtered.copy()
    out[DELETE_COLUMN] = False
    return out


def editor_columns() -> dict:
    """编辑区的列配置。

    `trade_id` **禁止编辑**：它是改删的唯一定位依据（设计 §2.3），改得动就等于
    换了一笔交易的身份。类型与来源走下拉框而不是自由文本：手打一个 "sel"
    会让那一行从此被 pnl 当成"读不懂"跳过。
    """
    columns: dict = {
        "trade_id": st.column_config.TextColumn(LOG_LABELS["trade_id"], disabled=True,
                                                help="store 生成的主键，改删都靠它定位。"),
        "date": st.column_config.DateColumn(LOG_LABELS["date"], format="YYYY-MM-DD"),
        "kind": st.column_config.SelectboxColumn(LOG_LABELS["kind"],
                                                 options=list(schema.KINDS)),
        "source": st.column_config.SelectboxColumn(LOG_LABELS["source"],
                                                   options=list(schema.SOURCES)),
        DELETE_COLUMN: st.column_config.CheckboxColumn(
            DELETE_COLUMN, help="勾上并按「保存修改」才真的删除（两步是刻意的："
                                "这份文件不可再生）。"),
    }
    for name in ("time", "symbol", "name", "reason", "note"):
        columns[name] = st.column_config.TextColumn(LOG_LABELS[name])
    for name in ("shares", "price", "amount", "fee", "tax", "stop_plan"):
        # format 一律 %.2f 之外还给 price/stop_plan 三位：低价股的分位有意义。
        digits = "%.3f" if name in ("price", "stop_plan") else "%.2f"
        columns[name] = st.column_config.NumberColumn(LOG_LABELS[name], format=digits)
    return columns


def split_editor_result(edited: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """把编辑区交回来的表拆成（要写回的行, 要删掉的 trade_id）。

    勾选列可能是 None（编辑器里那一格从没被碰过）——`fillna(False)` 不是可有可无的：
    把 None 当成"删"会删掉一整批没勾的行。
    """
    if edited.empty or DELETE_COLUMN not in edited.columns:
        return edited.copy(), []
    flags = edited[DELETE_COLUMN].fillna(False).astype(bool)
    deleted = [str(t) for t in edited.loc[flags, "trade_id"]]
    return edited.loc[~flags].drop(columns=[DELETE_COLUMN]), deleted


def blocking_rows(rows: pd.DataFrame, *, today: date) -> list[str]:
    """编辑后仍有阻断项的行，折成给人看的一行行字。

    只查阻断项（缺日期/股数、类型无法解释、未来日期）：那些行落盘之后 pnl 只能
    跳过它们，于是盈亏静默少算一笔。警告级别的不管——那是日志该如实记录的现实。
    """
    out: list[str] = []
    for row in rows.to_dict("records"):
        issues = schema.blocking_issues(schema.validate_trade(row, today=today))
        if issues:
            out.append(f"{row.get('trade_id')}（{schema.parse_date(row.get('date'))} "
                       f"{row.get('symbol')}）：" + "；".join(i.message for i in issues))
    return out


def save_edits(edited: pd.DataFrame, *, path: str | Path, today: date) -> bool:
    """把编辑区的改动与删除写回日志。返回"写进去了没有"。

    先校验再落盘：编辑器里把股数清空是一秒钟的事，而落盘之后 FIFO 会静默少算一笔。
    拒写时**不清编辑区的状态**——用户那几格改动还留在屏幕上，才改得回来。
    """
    keep, deleted = split_editor_result(edited)
    if broken := blocking_rows(keep, today=today):
        flash("error", "**没有保存**（下面这些行改坏了，落盘会让盈亏静默算错）：\n\n"
              + "\n\n".join(f"- {line}" for line in broken))
        return False
    try:
        with store.locked(path):
            # **锁里重新读一次**，不拿 `trades` 那份快照直接改（v0.5.0）：
            # 它是上一轮渲染时读的，从那以后另一个标签页可能又记了一笔。
            # 拿旧快照整表重写 = 把那一笔冲掉且不报错。重读是安全的——
            # apply_edits 一律按 trade_id 定位、认不出的主键直接拒绝，
            # 落在新表上与落在旧表上语义完全一致（这正是"绝不按行号"的红利）。
            merged = store.apply_edits(store.load_trades(path), keep, delete_ids=deleted)
            store.save_trades(merged, path)
    except (OSError, ValueError, RuntimeError) as e:
        flash("error", f"保存失败（{type(e).__name__}: {e}），日志文件未被修改。")
        return False
    # 成功了才清编辑区：data_editor 的改动按**行号**存在 session_state 里，
    # 删掉一行之后行号全变了，留着旧改动会在下一轮落到别人身上。
    st.session_state.pop(EDITOR_KEY, None)
    flash("ok", f"已保存：改动 {len(keep)} 行"
                + (f"、删除 {len(deleted)} 行" if deleted else ""))
    return True


# ================================================================ 筛选与导出（§5.2 / §5.5）

def filter_options(trades: pd.DataFrame, column: str) -> list[str]:
    """筛选框的候选：**日志里真出现过的值**，排好序。

    不给全集（全市场几千个代码）：那样的多选框没法用。类型与来源列则用固定全集
    更合适，所以那两处调用方自己传 schema 的常量。
    """
    return sorted({str(v) for v in trades[column] if str(v).strip()})


def current_filter() -> dict:
    """从各控件的 session_state 取出当前筛选条件，直接喂给 export.filter_trades。

    读 session_state 而不是接收控件返回值：导出按钮排在表格之后，两处必须用**同一份**
    条件，否则会出现"表里 3 行、导出 12 行"这种最伤信任的不一致。
    """
    return {
        "start": st.session_state.get(FILTER_START_KEY),
        "end": st.session_state.get(FILTER_END_KEY),
        "symbols": st.session_state.get(FILTER_SYMBOL_KEY) or (),
        "kinds": st.session_state.get(FILTER_KIND_KEY) or (),
        "sources": st.session_state.get(FILTER_SOURCE_KEY) or (),
        "reason": st.session_state.get(FILTER_REASON_KEY) or "",
    }


def export_payloads(filtered: pd.DataFrame, today: date) -> list[dict]:
    """两个下载按钮的全部参数。**数据源只有一个**：当前筛选结果。

    Excel 那份可能因为缺 openpyxl 而拿不到——此时**只**摘掉 Excel 按钮并如实说明，
    CSV 照常可用（它不依赖任何额外依赖）。
    """
    out = [{"label": "↓ 导出 CSV（Excel 可直接打开）", "key": CSV_KEY,
            "data": export.to_csv_bytes(filtered), "mime": export.CSV_MIME,
            "file_name": export.export_name(today, "csv"), "error": ""}]
    try:
        payload = export.to_excel_bytes(filtered)
    except RuntimeError as e:
        return out + [{"label": "", "key": XLSX_KEY, "data": b"", "mime": "",
                       "file_name": "", "error": str(e)}]
    return out + [{"label": "↓ 导出 Excel", "key": XLSX_KEY, "data": payload,
                   "mime": export.EXCEL_MIME,
                   "file_name": export.export_name(today, "xlsx"), "error": ""}]


# ================================================================ 持仓与盈亏（§4.2）

def positions_table(positions: Iterable, cache_dir: str | Path) -> pd.DataFrame:
    """当前持仓表：代码/名称/股数/持仓成本/单位成本/最新价/浮动盈亏/一致性标记。

    最新价与浮动盈亏是**格式化后的字符串**（缺值 = —），不是浮点数。理由与
    pool.pool_table 完全一样：`NumberColumn` 把缺值渲染成字面量 "None"（浏览器实测），
    而"没有本地缓存"是完全正常的状态（设计 §4.3 明写的那条局限）。
    代价是这两列按字符串排序——持仓通常不到十行，无所谓。
    """
    rows = []
    for position in positions:
        price = latest_price(position.symbol, cache_dir)
        floating = price * position.shares - position.cost
        codes = list(dict.fromkeys(i.code for i in position.issues))
        rows.append({
            SYMBOL_COLUMN: position.symbol,
            NAME_COLUMN: position.name or fmt.MISSING,
            SHARES_COLUMN: position.shares,
            COST_COLUMN: fmt.fmt_amount(position.cost, decimals=2),
            UNIT_COST_COLUMN: fmt.fmt_amount(position.unit_cost, decimals=4),
            PRICE_COLUMN: fmt.fmt_amount(price, decimals=2),
            FLOAT_PNL_COLUMN: fmt.fmt_amount(floating, decimals=2),
            ISSUE_COLUMN: "、".join(ISSUE_LABELS.get(c, c) for c in codes)
                          or fmt.MISSING,
        })
    return pd.DataFrame(rows, columns=[SYMBOL_COLUMN, NAME_COLUMN, SHARES_COLUMN,
                                       COST_COLUMN, UNIT_COST_COLUMN, PRICE_COLUMN,
                                       FLOAT_PNL_COLUMN, ISSUE_COLUMN])


def positions_columns() -> dict:
    return {
        SYMBOL_COLUMN: st.column_config.TextColumn(SYMBOL_COLUMN, width="small"),
        NAME_COLUMN: st.column_config.TextColumn(NAME_COLUMN, width="small"),
        SHARES_COLUMN: st.column_config.NumberColumn(SHARES_COLUMN, format="localized",
                                                     alignment="right"),
        COST_COLUMN: st.column_config.TextColumn(COST_COLUMN, alignment="right"),
        UNIT_COST_COLUMN: st.column_config.TextColumn(
            UNIT_COST_COLUMN, alignment="right",
            help="持仓成本 ÷ 股数，留四位小数：送股摊薄之后两位小数会把每股几厘的差抹掉。"),
        PRICE_COLUMN: st.column_config.TextColumn(
            PRICE_COLUMN, alignment="right",
            help="本地缓存 data/cache/ 里最后一根日线的收盘价。没取过数的标的显示 —。"),
        FLOAT_PNL_COLUMN: st.column_config.TextColumn(
            FLOAT_PNL_COLUMN, alignment="right",
            help="（最新价 × 股数）− 持仓成本。最新价拿不到时显示 —，不按 0 算。"),
        ISSUE_COLUMN: st.column_config.TextColumn(
            ISSUE_COLUMN, help="卖超/调整无持仓等；详情见上面的告警。"),
    }


#: 汇总指标的标签与取值键。前三个是钱，后四个**刻意与回测报告同一套口径**
#: （设计 §4.2：这样才能把"你的实际交易"和"策略回测"并排比较）。
SUMMARY_MONEY = (("total_realized", "已实现总收益"), ("realized_pnl", "平仓盈亏"),
                 ("dividends", "分红到账"))
SUMMARY_STATS = (("n_trades", "平仓笔数"), ("win_rate", "胜率"),
                 ("profit_factor", "盈亏比"), ("avg_holding_days", "平均持仓天数"))


def summary_metrics(summary: Mapping) -> list[tuple[str, str, str | None]]:
    """汇总指标卡：[(标签, 文本, 颜色)]。

    **一笔都没平仓时钱那三项显示 —，不是 0.00**。0.00 会被读成"我不赚不亏"，
    而实情是还没有任何已实现结果——这正是 fmt.MISSING 存在的理由。
    统计那四项的格式化直接走 fmt.fmt_metric（与回测指标卡同一个函数，
    None 自动变 —），不另写一套。
    """
    closed = int(summary.get("n_trades") or 0)
    dividends = float(summary.get("dividends") or 0.0)
    # "有没有已实现的东西可报"逐项判断：一笔都没平仓时平仓盈亏是"没有"，
    # 没记过分红时分红是"没有"，两样都没有时合计也是"没有"。
    available = {
        "realized_pnl": closed > 0,
        "dividends": dividends != 0.0,
        "total_realized": closed > 0 or dividends != 0.0,
    }
    out: list[tuple[str, str, str | None]] = []
    for key, label in SUMMARY_MONEY:
        value = summary.get(key)
        if not available[key]:
            out.append((label, fmt.MISSING, None))
            continue
        out.append((label, fmt.fmt_amount(value, decimals=2), fmt.signed_color(value)))
    for key, label in SUMMARY_STATS:
        value = summary.get(key)
        text = str(closed) if key == "n_trades" else fmt.fmt_metric(key, value)
        out.append((label, text, None))
    return out


def dashboard_metrics(summary: Mapping, positions: Iterable, prices: Mapping[str, float]
                      ) -> tuple[list[tuple[str, str, str | None]], int]:
    """仪表盘 2×3 指标块（v0.3.1 §2.1）：[(标签, 文本, 颜色)] + 无市价的持仓数。

    与 summary_metrics 同一条"没有就说没有"的纪律，外加市价这一维：

    - **无市价的持仓不按 0 计入市值与浮动盈亏**——0 元市值等于宣布那只票
      一文不值。可算的部分照给（调用方要把"N 只无市价未计入"注在旁边），
      一只都算不了时显示 —。
    - **总盈亏 = 总已实现 + 浮动**。浮动整个算不出来（有持仓但全无市价）时
      总盈亏也是 —：拿"已实现"顶给"总盈亏"等于宣称浮动为 0。
      没有任何持仓时浮动**确为** 0（什么都没拿着），总盈亏就是总已实现。
    - 胜率/盈亏比直接走 fmt.fmt_metric（与回测指标卡同一个函数，None 自动 —）。
    """
    market = floating = 0.0
    priced = unpriced = 0
    for position in positions:
        if position.shares <= 0:
            continue                    # pnl 为挂告警造的 0 股空壳，没有市值可言
        price = prices.get(position.symbol)
        if price is not None and math.isfinite(price) and price > 0:
            priced += 1
            market += price * position.shares
            floating += price * position.shares - position.cost
        else:
            unpriced += 1

    closed = int(summary.get("n_trades") or 0)
    dividends = float(summary.get("dividends") or 0.0)
    realized_known = closed > 0 or dividends != 0.0
    realized = float(summary.get("total_realized") or 0.0)
    # 浮动"确为 0"（没有任何持仓）也算已知；有持仓但全无市价才是"算不出来"。
    floating_known = priced > 0 or unpriced == 0
    total_known = floating_known and (priced > 0 or realized_known)
    total = realized + floating

    def money(value: float, known: bool, *, directional: bool = True
              ) -> tuple[str, str | None]:
        if not known:
            return fmt.MISSING, None
        return (fmt.fmt_amount(value, decimals=2),
                fmt.signed_color(value) if directional else None)

    out: list[tuple[str, str, str | None]] = []
    for label, (text, color) in (
            ("总已实现（含分红）", money(realized, realized_known)),
            ("当前持仓市值", money(market, priced > 0, directional=False)),
            ("浮动盈亏", money(floating, priced > 0)),
            ("总盈亏（已实现+浮动）", money(total, total_known))):
        out.append((label, text, color))
    for key, label in (("win_rate", "胜率"), ("profit_factor", "盈亏比")):
        out.append((label, fmt.fmt_metric(key, summary.get(key)), None))
    return out, unpriced


BY_SOURCE_COLUMN = "来源"


def by_source_table(by_source: Mapping[str, Mapping]) -> pd.DataFrame:
    """按来源分组的对比表：**照系统信号做的交易 vs 自己拍脑袋做的**。

    这是整个功能里学习价值最高的一块——它直接回答"这套系统到底帮没帮上我"。
    分组键是**建仓**来源（pnl.by_source 已经这么分），问的是"当初为什么进场"。
    """
    rows = []
    for source, summary in by_source.items():
        row = {BY_SOURCE_COLUMN: SOURCE_LABELS.get(source, source or fmt.MISSING)}
        for label, text, _color in summary_metrics(summary):
            row[label] = text
        rows.append(row)
    columns = [BY_SOURCE_COLUMN] + [label for _k, label in SUMMARY_MONEY + SUMMARY_STATS]
    return pd.DataFrame(rows, columns=columns)


def by_source_columns() -> dict:
    """分组表整列都是**已格式化的字符串**（缺值 = —，同 summary_metrics）：
    这张表的每一格都可能是"算不出来"（某一组还没有平仓交易），
    而 NumberColumn 把缺值渲染成字面量 "None"。"""
    labels = [label for _key, label in SUMMARY_MONEY + SUMMARY_STATS]
    return {BY_SOURCE_COLUMN: st.column_config.TextColumn(BY_SOURCE_COLUMN,
                                                          width="small"),
            **{label: st.column_config.TextColumn(label, alignment="right")
               for label in labels}}


MATCH_COLUMNS = ("卖出日", "代码", "名称", "股数", "成本", "净得", "盈亏",
                 "持仓天数", "建仓来源", "买入日", "买入记录号", "卖出记录号")


def matches_table(matches: Iterable) -> pd.DataFrame:
    """逐笔平仓明细：一次卖出吃掉两个批次就是**两行**（设计 §4.2 的可追溯配对）。

    `买入记录号` 指回日志里的那一行，"这笔平仓消耗了哪些买入批次"因此查得到。
    盈亏留成数字（不是字符串）：它永远算得出来（配不上批次的部分不进这张表），
    所以可以让 Styler 上红绿。
    """
    rows = [{
        "卖出日": f"{m.sell_date}", "代码": m.symbol, "名称": m.name or fmt.MISSING,
        "股数": m.shares, "成本": m.cost, "净得": m.proceeds, "盈亏": m.pnl,
        "持仓天数": m.holding_days,
        "建仓来源": SOURCE_LABELS.get(m.buy_source, m.buy_source or fmt.MISSING),
        "买入日": f"{m.buy_date}", "买入记录号": m.buy_trade_id,
        "卖出记录号": m.sell_trade_id,
    } for m in matches]
    return pd.DataFrame(rows, columns=list(MATCH_COLUMNS))


def matches_columns() -> dict:
    money = ("成本", "净得", "盈亏")
    columns = {name: st.column_config.TextColumn(name, width="small")
               for name in ("卖出日", "代码", "名称", "建仓来源", "买入日",
                            "买入记录号", "卖出记录号")}
    columns["股数"] = st.column_config.NumberColumn("股数", format="localized",
                                                   alignment="right")
    for name in money:
        columns[name] = st.column_config.NumberColumn(name, format="localized",
                                                      alignment="right", width="medium")
    columns["持仓天数"] = st.column_config.NumberColumn("持仓天数", format="%d",
                                                    alignment="right")
    return columns
