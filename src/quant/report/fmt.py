"""数字格式化、方向配色与表格列配置（v0.2.1 设计 §4）。

纯函数：不碰文件、不碰进程、不改状态。面板顶层是 UI 代码写不了单测，所以凡是
"要判断、要格式化"的都搬到这里——渲染出 'nan'、'-0.00%'、'1e+15' 这类东西，
用户是直接看到的假信息，必须有测试钉住（tests/test_report_fmt.py）。

`column_config` 的构造函数也放这里：`st.column_config.*` 返回的就是普通 dict，
可以在测试里断言，UI 层只负责把它塞给 `st.dataframe(column_config=...)`。
"""
from __future__ import annotations

import math

import streamlit.column_config as column_config

# A 股惯例红涨绿跌，与 K 线图一致。**全局铁律**：收益率、涨跌幅、盈亏一律用这两个色，
# 不许某处反过来（欧美习惯绿涨红跌，抄来的代码片段最容易在这里埋反向信号）。
UP = "#D94A4A"
DOWN = "#3E9E7A"
NEUTRAL = "#8C8778"    # 平盘/无方向：暖灰，不上色（§2.3 第 4 条"色只用在有意义处"）

MISSING = "—"          # 缺值占位符。宁可显示占位符，不显示 'nan' / 'None' / 编出来的 0

# 放量倍数进度条的满格值。实测扫描结果里出现过 4.22 倍；再往上（打板、借壳复牌）
# 一律满格——超过 5 倍之后的差别对"是否放量"这个判断已经没有意义。
RATIO_BAR_MAX = 6.0


def _finite(x) -> float | None:
    """折成有限浮点数；None / NaN / inf / 非数字一律 None（调用方显示 MISSING）。

    这三种都真会出现：trades.csv 未平仓那行 pnl 是空单元格（读出来 NaN），
    metrics.json 的 profit_factor 零平仓时是 null，object 列里还可能混进字符串。
    直接格式化会渲染出 'nan' / 'inf' —— 那是甩到用户脸上的乱码，不是数据。
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fmt_amount(x, *, decimals: int = 0) -> str:
    """金额/成交额千分位。成交额动辄 2.9e8，没有分组根本数不清是几个亿；
    `f"{v:g}"` 之类会退化成科学计数法，在金额列里等于没显示。"""
    v = _finite(x)
    if v is None:
        return MISSING
    return f"{v:,.{decimals}f}"


def fmt_pct(x, *, decimals: int = 2) -> str:
    """**比率** → 带符号百分号（0.4016 → "+40.16%"）。

    入参口径是 metrics.json 那种比率。**注意**扫描 CSV 的 `pct_chg` 已经是百分点
    （-5.09 就是 -5.09%），喂进来会得到 "-509.00%"——表格列一律走
    `scan_column_config()` 里的格式串，不要绕道这里。

    符号按**四舍五入后**的值定：直接 `f"{v:+.2%}"` 会把 -1e-9 这种数值噪声
    渲染成 "-0.00%"（一列排下来全是负号，看着像全线下跌）。零也不带加号——
    加号是"涨"的意思，平盘不是涨。
    """
    v = _finite(x)
    if v is None:
        return MISSING
    pct = round(v * 100, decimals)
    if pct == 0:
        return f"{0.0:,.{decimals}f}%"      # 显式喂 0.0：round 会留下 -0.0
    return f"{pct:+,.{decimals}f}%"


def direction_color(x) -> str:
    """方向色：涨红、跌绿、平盘与缺值中性灰。

    最大回撤恒为负 → 绿色。不许因为"回撤是坏事所以标红"而反过来：
    那会和"总收益红涨"的语义当面打架，同一屏里两种红就没人看得懂了。
    """
    v = _finite(x)
    if v is None or v == 0:
        return NEUTRAL
    return UP if v > 0 else DOWN


def scan_column_config() -> dict:
    """全市场扫描 / 每日信号表的列配置（列名取自 run_market_scan.CSV_COLUMNS）。

    每次现造：返回模块级 dict 时，调用方一改就串到下一次渲染。
    键必须是 CSV 里真有的列——多写一个键 Streamlit 会静默忽略，
    于是"配好了"的千分位永远不出现（tests 里拿 CSV_COLUMNS 对过）。
    """
    return {
        # 代码按字符串读（000333 不能变 333），列宽给小档，别把表撑开
        "symbol": column_config.TextColumn("代码", width="small"),
        "name": column_config.TextColumn("名称", width="small"),
        "strategy": column_config.TextColumn("策略", width="small"),
        "close": column_config.NumberColumn("收盘价", format="%.2f", alignment="right"),
        "pct_chg": column_config.NumberColumn(
            "涨跌幅%", format="%+.2f%%", alignment="right",
            help="当日涨跌幅（已是百分点）。机械信号里它的参考价值低于放量倍数。"),
        "amount": column_config.NumberColumn(
            "成交额", format="localized", alignment="right",
            help="当日成交额（元）。扫描已过滤低流动性标的。"),
        "amount_ratio_20d": column_config.ProgressColumn(
            "放量倍数", format="%.2fx", min_value=0, max_value=RATIO_BAR_MAX,
            help="当日成交额 / 前 20 日均额。判断信号质量最该看的一列："
                 "没有量的突破多半是假突破。"),
    }


def trades_column_config() -> dict:
    """交易明细表的列配置（列名即 Trade 的字段 = run_backtest.TRADE_COLUMNS）。

    date 故意用 TextColumn：CSV 里是 "2016-04-05" 字符串，喂给 DateColumn 会类型
    不匹配；ISO 日期的字典序就是时间序，排序照样对。
    """
    return {
        "symbol": column_config.TextColumn("代码", width="small"),
        "action": column_config.TextColumn("方向", width="small"),
        "date": column_config.TextColumn("日期", width="small"),
        "price": column_config.NumberColumn("成交价", format="%.3f", alignment="right"),
        "shares": column_config.NumberColumn("股数", format="localized",
                                             alignment="right"),
        "commission": column_config.NumberColumn("佣金", format="%.2f",
                                                 alignment="right"),
        "stamp": column_config.NumberColumn("印花税", format="%.2f", alignment="right",
                                            help="A 股只在卖出时收，买入行恒为 0。"),
        "pnl": column_config.NumberColumn(
            "盈亏", format="localized", alignment="right",
            help="平仓那一笔才有盈亏；买入行与仍持仓的标的是空值。"),
        "holding_days": column_config.NumberColumn("持仓天数", format="%d",
                                                   alignment="right"),
    }
