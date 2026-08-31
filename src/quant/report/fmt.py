"""数字格式化、方向配色与表格列配置（v0.2.1 设计 §4）。

纯函数：不碰文件、不碰进程、不改状态。面板顶层是 UI 代码写不了单测，所以凡是
"要判断、要格式化"的都搬到这里——渲染出 'nan'、'-0.00%'、'1e+15' 这类东西，
用户是直接看到的假信息，必须有测试钉住（tests/test_report_fmt.py）。

`column_config` 的构造函数也放这里：`st.column_config.*` 返回的就是普通 dict，
可以在测试里断言，UI 层只负责把它塞给 `st.dataframe(column_config=...)`。
"""
from __future__ import annotations

import math
import re

import pandas as pd
import streamlit.column_config as column_config

from quant.report import palette
from quant.strategy import strategy_label

# A 股惯例红涨绿跌，与 K 线图一致。**全局铁律**：收益率、涨跌幅、盈亏一律用这两个色，
# 不许某处反过来（欧美习惯绿涨红跌，抄来的代码片段最容易在这里埋反向信号）。
#
# 这里只是**转发**：色值的唯一定义处是 quant.report.palette（图表与面板也从它取）。
# 保留 fmt.UP / fmt.DOWN / fmt.NEUTRAL 这三个名字是因为"方向色"在语义上属于本模块，
# 调用方一直按 fmt.xxx 读（tests/test_report_fmt.py 用 `is` 钉住转发关系）。
UP = palette.UP
DOWN = palette.DOWN
NEUTRAL = palette.NEUTRAL    # 平盘/无方向：暖灰，不上色（§2.3 第 4 条"色只用在有意义处"）

MISSING = "—"          # 缺值占位符。宁可显示占位符，不显示 'nan' / 'None' / 编出来的 0

# 放量倍数进度条的满格值。实测扫描结果里出现过 4.22 倍；再往上（打板、借壳复牌）
# 一律满格——超过 5 倍之后的差别对"是否放量"这个判断已经没有意义。
RATIO_BAR_MAX = 6.0

# 指标卡：键、顺序、中文标签。顺序就是 2×4 网格的铺排顺序，总收益率排第一格
# （最该先看到的数）。键必须与 quant.report.metrics.compute_metrics 的输出逐一对齐。
METRIC_LABELS = {
    "total_return": "总收益率", "cagr": "年化收益率", "max_drawdown": "最大回撤",
    "sharpe": "夏普比率(rf=0)", "n_trades": "交易次数", "win_rate": "胜率",
    "profit_factor": "盈亏比", "avg_holding_days": "平均持仓天数",
}
# 比率类指标：显示成百分号。其余（夏普、盈亏比、持仓天数）是无单位数字。
PERCENT_METRICS = ("total_return", "cagr", "max_drawdown", "win_rate")
# **只有这三项**有涨跌方向，才配得上红绿（§2.3 第 4 条"色只用在有意义处"）。
# 夏普为负不是"跌"，胜率 42% 更不是——给它们上色等于同屏多造几个假信号。
DIRECTIONAL_METRICS = ("total_return", "cagr", "max_drawdown")


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


def fmt_metric(key: str, value) -> str:
    """指标卡数值。int 必须原样 str()：一律 f"{v:.2f}" 会把交易次数渲染成 '243.00'。
    比率类走百分号；None / NaN / inf（零平仓、手改过的 metrics.json）显示 —。

    不带正号：方向由 metric_color() 的红绿表达，再加个 '+' 是重复。
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    v = _finite(value)
    if v is None:
        return MISSING
    if key in PERCENT_METRICS:
        return f"{v:.2%}"
    return f"{v:.2f}"


def signed_color(x) -> str | None:
    """红绿的唯一闸门：有方向给红/绿，平盘与缺值给 None。

    None 而不是 NEUTRAL：不上色 ≠ 上一层灰。把正常数字灰掉会让它比标题还暗，
    等于把主角降级（§2.3 第 1 条"数字最大最亮"）。
    """
    color = direction_color(x)
    return None if color == NEUTRAL else color


def metric_color(key: str, value) -> str | None:
    """指标卡的正负着色判定。只有 DIRECTIONAL_METRICS 才有方向，
    其余（夏普、胜率、盈亏比、持仓天数）一律不上色。"""
    return signed_color(value) if key in DIRECTIONAL_METRICS else None


def direction_color(x) -> str:
    """方向色：涨红、跌绿、平盘与缺值中性灰。

    最大回撤恒为负 → 绿色。不许因为"回撤是坏事所以标红"而反过来：
    那会和"总收益红涨"的语义当面打架，同一屏里两种红就没人看得懂了。
    """
    v = _finite(x)
    if v is None or v == 0:
        return NEUTRAL
    return UP if v > 0 else DOWN


# 回测产物目录名的时间戳后缀（run_backtest.py 的 {策略键}_{YYYYMMDD}_{HHMMSS}）。
# 这是**唯一定义处**：app/ui.py 的排序键也用它，两处各写一份迟早对不上。
RUN_STAMP = re.compile(r"_(\d{8}_\d{6})$")


def run_label(name: str) -> str:
    """回测目录名 → 给人看的「策略显示名 时间戳」（v0.4.0 M1）。

    目录名照旧存键（它是数据，不是界面）；这里只换显示。两级兜底都不许抛错：
    未知策略键（已下架策略的旧产物）原样显示键——strategy_label 的原样返回
    正好是这个语义；没有时间戳后缀的目录名（手工改过名）整个原样返回，
    不猜哪截是策略。
    """
    m = RUN_STAMP.search(name)
    if m is None:
        return name
    return f"{strategy_label(name[:m.start()])} {m.group(1)}"


def map_strategy_labels(df: pd.DataFrame) -> pd.DataFrame:
    """把表的 strategy 列换成中文显示名，返回**新的** DataFrame（v0.4.0 M1）。

    只在显示层用（扫描/信号表喂给 st.dataframe 之前那一下）；磁盘上的 CSV
    与预填（journal_ui.prefills 读的原 df）照旧是键，所以**绝不改原 df**。
    没有 strategy 列的表（成交明细、被跳过表）原样返回；未知键原样显示；
    非字符串（读坏的 CSV 里的 NaN）不碰——str(NaN) 会变成一个像键的 "nan"。
    """
    if "strategy" not in df.columns:
        return df
    out = df.copy()
    out["strategy"] = [strategy_label(v) if isinstance(v, str) else v
                       for v in out["strategy"]]
    return out


def scan_column_config() -> dict:
    """全市场扫描 / 每日信号表的列配置（列名取自 run_market_scan.CSV_COLUMNS）。

    每次现造：返回模块级 dict 时，调用方一改就串到下一次渲染。
    键必须是 CSV 里真有的列——多写一个键 Streamlit 会静默忽略，
    于是"配好了"的千分位永远不出现（tests 里拿 CSV_COLUMNS 对过）。
    """
    return {
        # 每一列都要有中文标签：漏一列只是表头多个裸 'date'，但那正是"配好了"的错觉
        "date": column_config.TextColumn("日期", width="small"),
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


def signal_column_config() -> dict:
    """固定池每日信号表的列配置（列名取自 run_daily_signal.CSV_COLUMNS）。

    与扫描表**不是**同一套列：这里没有 name / amount / 放量倍数，却多一个 action
    （buy/sell）。拿 scan_column_config 顶替的话，多出来的键被 Streamlit 静默忽略，
    真正需要中文标签的 action / close 反而没配上。
    """
    return {
        "date": column_config.TextColumn("日期", width="small"),
        "symbol": column_config.TextColumn("代码", width="small"),
        "strategy": column_config.TextColumn("策略", width="small"),
        "action": column_config.TextColumn("方向", width="small",
                                           help="buy=进场信号，sell=出场信号。"),
        "close": column_config.NumberColumn("收盘价", format="%.2f", alignment="right"),
    }


def skipped_column_config() -> dict:
    """被跳过的订单表（列名即 run_backtest.SKIPPED_COLUMNS）。

    与成交明细**不是**一套列：只有 date/symbol/reason。拿 trades_column_config
    顶替时页面照样能跑（多出来的键被静默忽略），但真正要解释的 reason 反而没标签。
    """
    return {
        "date": column_config.TextColumn("日期", width="small"),
        "symbol": column_config.TextColumn("代码", width="small"),
        "reason": column_config.TextColumn(
            "原因", width="medium",
            help="涨跌停无法成交、资金不足、停牌等；引擎的约束都记在这里。"),
    }


def direction_styler(df: pd.DataFrame, columns) -> "pd.io.formats.style.Styler":
    """给方向列（涨跌幅、盈亏）的文字上红绿，返回 Styler 交给 st.dataframe。

    为什么不用 column_config：`st.column_config.NumberColumn` 没有 color 参数
    （实测签名确认），条件着色在列配置里做不到。Streamlit 明确支持 pandas Styler 的
    颜色，且"column_config 的文本/数字格式优先于 Styler"——所以千分位、百分号仍由
    列配置说话，Styler 只管颜色，两者不打架。

    `columns` 里不存在的列必须跳过：同一个渲染函数要同时喂扫描表（有 pct_chg）
    和每日信号表（没有），Styler 的 subset 指向不存在的列会 KeyError 崩页。
    """
    present = [c for c in columns if c in df.columns]
    styler = df.style
    if present:
        styler = styler.map(_direction_css, subset=present)
    return styler


def _direction_css(x) -> str:
    """单元格的方向色 CSS。平盘/缺值返回空串——不上色，而不是上一层灰。"""
    color = direction_color(x)
    return "" if color == NEUTRAL else f"color: {color};"


def symbol_trade_summary(trades: pd.DataFrame) -> dict:
    """某标的的成交小结（K 线图上方那一行）：总笔数、买/卖笔数、已平仓盈亏合计。

    `pnl` 只在平仓那一笔上有值，仍持仓的标的整列是 NaN。此时 `sum()` 给 0.0——
    那等于宣布"这只不赚不亏"，是编出来的数字，必须返回 None 让面板显示 —。
    老回测目录的 trades.csv 连 pnl 列都没有（v0.1 早期产物），也不能 KeyError。
    """
    n_trades = int(len(trades))
    actions = (trades["action"].astype(str).str.lower()
               if "action" in trades.columns else pd.Series(dtype=object))
    values = [v for v in (_finite(x) for x in trades.get("pnl", ()))
              if v is not None]
    return {
        "n_trades": n_trades,
        "n_buy": int((actions == "buy").sum()),
        "n_sell": int((actions == "sell").sum()),
        "pnl": sum(values) if values else None,
    }


def symbol_label(symbol: str, name=None) -> str:
    """标的选择器的显示文案："600519 贵州茅台"。

    名称的唯一离线来源是扫描 CSV，多数标的没有（扫描只记录出信号的那些），
    所以缺名是常态：老实只显示代码，绝不渲染 "600519 None" / "600519 nan"。
    """
    text = "" if name is None else str(name).strip()
    if not text or text.lower() == "nan":
        return str(symbol)
    return f"{symbol} {text}"


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
        # width 不是可有可无的美化：默认列宽装不下 "-58,419.224"，表格网格从
        # **左边**裁字，一笔亏 5.8 万会渲染成 "8,419.224"——亏损看起来像盈利。
        # 实跑面板时抓到的，本表只有这一列会出现"负号 + 千分位 + 五位整数"。
        "pnl": column_config.NumberColumn(
            "盈亏", format="localized", alignment="right", width="medium",
            help="平仓那一笔才有盈亏；买入行与仍持仓的标的是空值。"),
        "holding_days": column_config.NumberColumn("持仓天数", format="%d",
                                                   alignment="right"),
    }
