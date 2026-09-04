"""信号池的读写编排（v0.2.2 §3.4）：面板上增删信号池。

**写的是 `config/universe.local.yaml`，不是 `config/settings.yaml`**（v0.3.2 §2.2）：
池子是用户状态（随交易变化、暴露关注标的），项目配置是项目决策。改一次自己的池子
不该让一个受版本控制的文件变脏（那会天天提示提交一份与代码无关的 diff）。
调用方传进来的仍然是 settings.yaml 的路径——本地文件的位置由它推导，
"内部写哪个文件"是实现细节。

**分工**（校验与写盘的真正逻辑都在 src/，这里只是编排 + Streamlit 接线）：
- `quant.universe`：代码格式、去重、"是否在扫描池内"、至少留 1 只；
- `quant.config_edit`：本地文件的整份重写 + 原子写 + 写后复核回滚；
- `quant.config`：合并（本地覆盖 > 种子）与"当前池子来自哪个文件"；
- 本模块：读当前池子 → 调上面几个 → 把结果变成一句人话，外加扫描池清单的缓存。

**本模块刻意不 import 任何 app 内部模块**（ui / theme / guide）：配置路径一律由
调用方传进来。这样它能在没有 Streamlit 运行时的情况下被直接加载单测——而"写坏
config/settings.yaml"是本项目最贵的故障，那部分逻辑必须能脱离 UI 测。

两个页面共用它：「信号池」页（增删）与「信号」页扫描表的每行 ＋（§3.4 B）。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from quant import filelock
from quant.config import load_settings, local_universe_path, universe_source
from quant.config_edit import write_local_universe
from quant.data.cache import BarCache
# 取函数而不是 `from quant.data import symbols`：本模块里 `symbols` 已经是好几个
# 函数的形参名（pool_table / on_remove / on_add 的那一轮行序），模块名会被就地遮住。
from quant.data.symbols import is_fresh, load_symbols
from quant.report import fmt
from quant.universe import add_symbol, remove_symbol, validate_symbol

# 表格里按钮列的列名（就是显示出来的表头）与单元格文案（ButtonColumn 拿单元格值当
# 按钮标签）。"已在池中"那一档没有真正的 disabled 单元格可用（ButtonColumn 整列
# 只读、不能按行禁用），所以用**文案**表达禁用态，回调里再挡一次（点了也不写文件）。
ACTION_COLUMN = "操作"
ADD_COLUMN = "信号池"
REMOVE_LABEL = "− 移除"
ADD_LABEL = "＋ 加入"
IN_POOL_LABEL = "✓ 已在池中"

# ButtonColumn 的 key：点击信息落在 st.session_state[key]（{"row", "label"}），
# 只在这一轮有效。两张表各一个 key，否则同一轮的两次点击会互相覆盖。
REMOVE_CLICK_KEY = "universe_remove_click"
ADD_CLICK_KEY = "scan_add_click"

# 「信号池」页添加区块的控件 key（测试按 key 取控件，别改字面量）
LOAD_BUTTON_KEY = "universe_load_pool"
RETRY_BUTTON_KEY = "universe_retry_pool"
ADD_BUTTON_KEY = "universe_add"
PICK_KEY = "universe_pick"
LOAD_FLAG = "universe_pool_loaded"     # 用户点过「加载清单」没有（见 pages_universe）

# 回调里写下、下一轮渲染时读出的提示（见 flash / show_flash）
FLASH_KEY = "universe_flash"

# 扫描池清单的缓存时长。清单一天变不了几只（新股/改名/ST），6 小时足够新；
# 而每次拉取是约 2–4 分钟的联网请求，不缓存就等于每次交互都罚一次这个时间。
POOL_TTL_S = 6 * 3600
# 转圈文案里**不写具体分钟数**：那个数字的唯一出处是 guide.FACTS["pool_fetch"]
# （按钮上方那行说明就在渲染它，且与 README 的实测拆解对账）。这里再抄一份
# 就成了两套说法，改一处必漏一处。
POOL_SPINNER = "正在准备全市场标的清单（本地已有近期清单则瞬时，否则联网拉取较慢）…"

# 读配置能出的错：文件不在（OSError）、YAML 语法坏（YAMLError）、
# 缺键/类型不对（KeyError/TypeError）、universe 为空或 capital 非法（ValueError）。
# 一律要接住——这些页面不能因为一份手工改坏的配置整页 traceback。
CONFIG_ERRORS = (OSError, ValueError, KeyError, TypeError, yaml.YAMLError)
# **写**配置还能多出一种：write_universe 的写后复核不过 → 它回滚原文件并抛
# RuntimeError（M2 的决定："宁可这次操作失败，也不能留下一个坏配置"）。
# 这是一条纯防御路径，正常情况走不到，但它自己崩掉就成了最难查的那类故障：
# 用户看到一屏 traceback，既不知道配置改没改，也不知道该不该重试——而实情是
# **一个字节都没动**。所以写入路径一律按这套接，接住之后如实转述"已回滚"。
# 读取路径**不**用它：那里出 RuntimeError 只能是程序 bug，不该被降级掩盖。
WRITE_ERRORS = (*CONFIG_ERRORS, RuntimeError)
# 拉清单能出的错：baostock 登录失败/超时（OSError 或库里各种自定义异常）、
# 非交易日或当日清单未更新（ValueError）。**只能**用 Exception 兜底：
# baostock 抛什么类型没有契约，漏一种就是整页崩在"离线"这个最常见的场景上。
FETCH_ERRORS = Exception


def current(config_path: str | Path) -> tuple[str, ...]:
    """当前信号池（本地覆盖优先，没有才是种子）。
    异常一律交给调用方（页面要按 CONFIG_ERRORS 降级）。"""
    return tuple(load_settings(config_path).universe)


def source(config_path: str | Path) -> Path | None:
    """当前池子来自哪个文件：本地覆盖文件，或 None（= settings.yaml 里的种子）。

    页面必须说得出来。两个文件都有 universe，看不出用的是哪个的话，用户会以为自己
    在跟踪 A 池子，而三个脚本每天在跑 B 池子——没有任何一处会报错。
    """
    return universe_source(config_path)


def local_path(config_path: str | Path) -> Path:
    """增删会写到哪个文件（不管它此刻在不在）。"""
    return local_universe_path(config_path)


def add(symbol: str, *, config_path: str | Path,
        allowed: Iterable[str]) -> str:
    """把 symbol 加进池子并落盘，返回一句给人看的结果。

    `allowed` 是扫描池代码集合（沪深主板、非 ST、上市满 400 天）。已在池中时
    **不写文件**：面板上重复点一下不该改文件、也不该报错。

    「读当前池子 → 加一只 → 整份重写」整段在锁里（v0.5.0）：两个标签页各自
    加一只不同的票时，两边都读到同一份旧池子，后写的那份里没有前一只——
    **少一只且不报错**，而少的那只从此没有人管它的卖出信号。
    """
    with filelock.locked(local_universe_path(config_path)):
        existing = current(config_path)
        if symbol in existing:
            return f"{symbol} 已在信号池中（共 {len(existing)} 只）"
        wanted = add_symbol(existing, symbol, allowed)  # 池外/格式不对在这里抛
        write_local_universe(config_path, wanted)       # 原子写 + 写后复核回滚
    return f"已加入 {symbol}，信号池现有 {len(wanted)} 只"


def remove(symbol: str, *, config_path: str | Path) -> str:
    """把 symbol 移出池子并落盘。清空到 0 只 / 不在池中都会抛 ValueError。

    与 `add` 同一个临界区（同一把锁、同一个理由）：并发的加与删若各拿一份旧快照
    整份重写，先写的那次改动会被悄悄抹掉。
    """
    with filelock.locked(local_universe_path(config_path)):
        wanted = remove_symbol(current(config_path), symbol)
        write_local_universe(config_path, wanted)
    return f"已移除 {symbol}，信号池现有 {len(wanted)} 只"


# ---------------------------------------------------------------- 扫描池清单（联网 + 缓存）

@st.cache_data(ttl=POOL_TTL_S, show_spinner=POOL_SPINNER)
def scan_pool(symbols_path: str) -> tuple[pd.DataFrame, str]:
    """全市场扫描池清单 DataFrame[symbol, name] 与它的基准日（ISO 字符串）。

    **先看本地那份**（`symbols_path`，由 run_market_scan.py 落盘）：7 天内的直接用，
    瞬时返回——跑过一次扫描之后，点「加载可选标的清单」不该再等 2-4 分钟。
    没有 / 已过期才联网，走下面那条老路（行为与 v0.2.2 完全一致）。
    文件损坏时 load_symbols 响亮抛 RuntimeError，这里**不接**：页面会按 FETCH_ERRORS
    降级并把路径与自愈办法如实转述。悄悄退回联网重拉的话，每次开面板都白等 2-4 分钟，
    而那个坏文件永远没人发现。

    路径由调用方传进来而不是在这里写死：本模块刻意不 import ui（见模块 docstring），
    而且测试必须能把它指到 tmp_path——写死的话，跑过一次真扫描之后离线测试就会去读
    线上文件，联网那条分支再也测不到。

    与 scripts/run_market_scan.py 同一个数据源（provider.get_all_symbols），
    所以面板上能加的票与扫描能扫到的票**是同一批**。

    基准日取最近交易日；当日清单约 17:30 后才有，盘中打开面板时它会抛 ValueError
    ——那就退一个交易日重试（清单一天变不了几只，用昨天的比"整天不能添加"好得多）。
    两天都拉不到就把原因抛出去，由页面降级并如实显示。

    provider 在**函数体内**取（`from quant.data import baostock_provider` 后取属性）：
    模块级 `from ... import BaostockProvider` 会把类绑死，测试就再也换不掉它，
    于是离线测试会真的去登录 baostock。
    """
    from quant.data import baostock_provider

    local = load_symbols(symbols_path)              # 损坏 → RuntimeError，故意不接
    if local is not None:
        listing, as_of = local
        if is_fresh(as_of, date.today()):
            return listing, as_of.isoformat()

    with baostock_provider.BaostockProvider() as provider:
        today = date.today()
        calendar = provider.get_trade_calendar(today - timedelta(days=21), today)
        if not calendar:
            raise RuntimeError("近三周无交易日？交易日历异常，无法确定清单基准日")
        reasons: list[str] = []
        for as_of in list(reversed(calendar))[:2]:      # 最近两个交易日，新的先试
            try:
                return provider.get_all_symbols(as_of), as_of.isoformat()
            except ValueError as e:                     # 非交易日 / 当日清单未更新
                reasons.append(f"{as_of}: {e}")
        raise RuntimeError("最近两个交易日的全市场清单都拉不到 —— " + "；".join(reasons))


def options(df: pd.DataFrame) -> list[str]:
    """候选项写成"代码 名称"（`600519 贵州茅台`）。

    用 selectbox + 这种标签而不是自由文本框：代码从候选里选，杜绝输错；
    而带上名称之后按名字也能搜——用户多半记得住"茅台"，记不住 600519。
    """
    return [f"{s} {n}" for s, n in zip(df["symbol"], df["name"])]


def symbol_of(option: str) -> str:
    """从"代码 名称"取回代码，并过一遍格式校验。

    校验不是多余的：这个值要写进配置文件，而配置文件驱动全部三个脚本。
    宁可在这里抛，也不要把"茅台"当代码写进去。
    """
    parts = str(option).split()
    if not parts:
        raise ValueError(f"选项为空，取不出股票代码: {option!r}")
    return validate_symbol(parts[0])


# ---------------------------------------------------------------- 表格数据

def latest_close(symbol: str, cache_dir: str | Path) -> float:
    """本地缓存里最后一根 K 线的收盘价；没有缓存/缓存坏了返回 NaN。

    **不许返回 0**：0 是一个看着像数据的假价格。缺值一路走到 pool_table 里被
    fmt.fmt_amount 变成 —（fmt.MISSING），表格上方那行灰字解释了它的含义。
    缓存损坏这里也只留空——K 线页会为同一个文件响亮报错，信号池页不必跟着崩。
    """
    try:
        bars = BarCache(cache_dir).load(symbol)
    except (RuntimeError, OSError, ValueError):
        return float("nan")
    if bars is None or bars.empty or "close" not in bars.columns:
        return float("nan")
    return float(bars.sort_index()["close"].iloc[-1])


def pool_table(symbols: Sequence[str], names: dict[str, str],
               cache_dir: str | Path) -> pd.DataFrame:
    """信号池表格（§3.4 A）：代码 / 名称 / 最新价 / 每行一个 − 按钮。

    名称来自 `ui.symbol_names()`（全市场清单 + 扫描 CSV，见那里的合并规则）。
    查不到就显示 `fmt.MISSING`（—）而不是空串：空白像 bug（"是不是没加载出来"），
    — 是明确的"暂无"，与右边「最新价」那一列同一个符号。不写"未知"——那看着像个名字。

    最新价是**格式化后的字符串**（`fmt.fmt_amount` → "1,600.00" / "—"），
    不是浮点数。理由是浏览器实测（v0.2.2 M3）：`st.column_config.NumberColumn`
    把缺值渲染成字面量 **"None"**——一张七行的表里六个 "None"，正是本项目明令
    禁止的那种输出（fmt.MISSING 就是为此存在的）。代价是这一列按字符串排序，
    七八行的池子里无所谓；换来的是缺值显示成 —，不会被当成"价格为 0/None"。
    """
    return pd.DataFrame({
        "代码": list(symbols),
        "名称": [names.get(s) or fmt.MISSING for s in symbols],
        "最新价": [fmt.fmt_amount(latest_close(s, cache_dir), decimals=2)
                for s in symbols],
        ACTION_COLUMN: [REMOVE_LABEL] * len(symbols),
    })


def scan_table(df: pd.DataFrame, in_pool: Iterable[str]) -> pd.DataFrame:
    """扫描结果表 + 末列的 ＋ 按钮（§3.4 B）。已在池中的那一行显示禁用态文案。

    加在**末列**：扫描表的列序（日期/代码/名称/策略/…）是用户已经熟悉的，
    在中间插一列会把每一列的位置都挪一格。
    """
    pool = set(in_pool)
    out = df.copy()
    out[ADD_COLUMN] = [IN_POOL_LABEL if str(s) in pool else ADD_LABEL
                       for s in df["symbol"]]
    return out


# ---------------------------------------------------------------- Streamlit 接线

def flash(level: str, text: str) -> None:
    """把结果留到**下一轮**渲染时再说（level: "ok" / "error"）。

    为什么不在回调里直接 st.toast/st.error：ButtonColumn 的 on_click 跑在脚本重跑
    **之前**，那一轮画的元素随即被重跑清掉；而"＋ 加入"按钮那条路径写完要 st.rerun()
    刷新表格，同样会把刚画的提示丢掉。存进 session_state 是唯一稳的做法。
    """
    st.session_state[FLASH_KEY] = (level, text)


def show_flash() -> None:
    """渲染并清掉上一轮留下的提示。成功走 st.toast（轻，不占版面），
    失败走 st.error（必须留在页面上：那意味着配置没被改动，用户得知道为什么）。"""
    message = st.session_state.get(FLASH_KEY)
    if message is None:
        return
    del st.session_state[FLASH_KEY]
    level, text = message
    if level == "error":
        st.error(text)
    else:
        st.toast(text)


def _clicked_symbol(key: str, symbols: Sequence[str]) -> str | None:
    """取被点那一行的代码。行号越界一律当没点过——表格与回调之间数据可能已经变了
    （另一个标签页刚改过配置），按错位的行号去改文件是最坏的结果。"""
    click = st.session_state.get(key)
    if click is None:
        return None
    row = int(click["row"])
    if not 0 <= row < len(symbols):
        return None
    return str(symbols[row])


def on_remove(symbols: Sequence[str], config_path: str | Path) -> None:
    """信号池表格里 − 的 on_click。`symbols` 是**渲染那一轮**的行序（由 args 传入）。"""
    symbol = _clicked_symbol(REMOVE_CLICK_KEY, symbols)
    if symbol is None:
        return
    try:
        flash("ok", remove(symbol, config_path=config_path))
    except WRITE_ERRORS as e:
        flash("error", f"移除 {symbol} 失败：{e}")


def on_add(symbols: Sequence[str], config_path: str | Path,
           allowed: Iterable[str]) -> None:
    """扫描表里 ＋ 的 on_click。

    `allowed` 传的是**这张扫描表自己的代码集合**：扫描 CSV 里的每一只都是
    get_all_symbols 筛过的（主板、非 ST、上市满 400 天），所以这条路径不必联网
    就能满足"必须在扫描池内"这条规则。
    """
    symbol = _clicked_symbol(ADD_CLICK_KEY, symbols)
    if symbol is None:
        return
    try:
        flash("ok", add(symbol, config_path=config_path, allowed=allowed))
    except WRITE_ERRORS as e:
        flash("error", f"加入 {symbol} 失败：{e}")
