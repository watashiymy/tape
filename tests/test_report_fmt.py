# tests/test_report_fmt.py — v0.2.1 §4 数字格式化与方向配色的纯函数。
#
# 面板本身写不了单测（顶层就是 UI 代码），所以"要判断、要格式化"的一律下沉到
# quant.report.fmt，边界全在这里钉死：0 / 负数 / None / NaN / inf / 极大值 / 非数字。
# 这些值都是真会出现的：trades.csv 里未平仓那行 pnl 是空单元格（读出来 NaN），
# metrics.json 里 profit_factor 可能是 null。
import importlib.util
from dataclasses import fields
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report import fmt, palette

ROOT = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "run_market_scan", ROOT / "scripts" / "run_market_scan.py")
run_market_scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_market_scan)


def _col_format(col) -> str | None:
    """column_config 返回的是普通 dict（可断言），格式串在 type_config 里。"""
    return col["type_config"].get("format")


def _col_type(col) -> str:
    return col["type_config"]["type"]


# ---------------------------------------------------------------- fmt_amount

def test_fmt_amount_groups_thousands():
    """成交额动辄 2.9e8，没有千分位根本数不清是几个亿。"""
    assert fmt.fmt_amount(290000000) == "290,000,000"
    assert fmt.fmt_amount(1234.0) == "1,234"


def test_fmt_amount_zero_and_negative():
    """0 要老实显示 0（不是 —）；负数（亏损）保留负号。"""
    assert fmt.fmt_amount(0) == "0"
    assert fmt.fmt_amount(-12345.6) == "-12,346"


def test_fmt_amount_missing_values_show_dash():
    """CSV 空单元格读出来是 NaN，`f"{nan:,.0f}"` 会把 'nan' 甩到用户脸上；
    inf 同理渲染成 'inf'；非数字（object 列里的脏值）不能崩页。"""
    assert fmt.fmt_amount(None) == fmt.MISSING
    assert fmt.fmt_amount(float("nan")) == fmt.MISSING
    assert fmt.fmt_amount(float("inf")) == fmt.MISSING
    assert fmt.fmt_amount("暂无") == fmt.MISSING


def test_fmt_amount_huge_value_stays_readable():
    """极大值不许退化成科学计数法（'1e+15' 在金额列里等于没显示）。"""
    assert fmt.fmt_amount(1e15) == "1,000,000,000,000,000"


def test_fmt_amount_decimals_opt_in():
    assert fmt.fmt_amount(1234.5678, decimals=2) == "1,234.57"


# ---------------------------------------------------------------- fmt_pct

def test_fmt_pct_takes_ratio_and_signs_it():
    """入参是**比率**（metrics.json 的口径）：0.4016 → +40.16%。
    实测值走一遍：等权买入持有 401.6%。"""
    assert fmt.fmt_pct(4.016) == "+401.60%"
    assert fmt.fmt_pct(1.161) == "+116.10%"
    assert fmt.fmt_pct(0.489) == "+48.90%"


def test_fmt_pct_negative_keeps_minus():
    """最大回撤是负数（metrics 里 max_dd = min(...) ≤ 0）。"""
    assert fmt.fmt_pct(-0.3512) == "-35.12%"


def test_fmt_pct_zero_has_no_plus_sign():
    """0 不该显示成 '+0.00%'——加号是"涨"的意思，零不是涨。"""
    assert fmt.fmt_pct(0.0) == "0.00%"


def test_fmt_pct_tiny_negative_never_renders_minus_zero():
    """`f"{-1e-9:+.2%}"` 会渲染出 '-0.00%'：数值噪声被显示成下跌。
    符号必须按**四舍五入后**的值定，否则表里会出现一排 -0.00%。"""
    assert fmt.fmt_pct(-1e-9) == "0.00%"
    assert fmt.fmt_pct(1e-9) == "0.00%"


def test_fmt_pct_missing_values_show_dash():
    """profit_factor / win_rate 在零平仓时是 None（metrics.py 明确留 None）。"""
    assert fmt.fmt_pct(None) == fmt.MISSING
    assert fmt.fmt_pct(float("nan")) == fmt.MISSING
    assert fmt.fmt_pct(float("inf")) == fmt.MISSING


def test_fmt_pct_huge_value_groups_thousands():
    """比率 120 = 12000%：百分号列也要千分位，否则数不清几位。"""
    assert fmt.fmt_pct(120.0) == "+12,000.00%"


def test_fmt_pct_percent_points_are_not_ratios():
    """陷阱固化：扫描 CSV 的 pct_chg 已经是**百分点**（-5.09 就是 -5.09%），
    喂给 fmt_pct 会得到 -509%。表格列一律走 column_config 的格式串，别走这里。"""
    assert fmt.fmt_pct(-5.09) == "-509.00%"


# ---------------------------------------------------------------- direction_color

def test_direction_colors_are_forwarded_from_the_shared_palette():
    """方向色只在 quant.report.palette 定义一次（面板、图表、表格三处都取它）。
    `is` 而不是 `==`：抄一份字面量照样 == 相等，但改一处就会漂移——
    图表刚因为"两边各写一套色值"白底了一版。"""
    assert fmt.UP is palette.UP
    assert fmt.DOWN is palette.DOWN
    assert fmt.NEUTRAL is palette.NEUTRAL


def test_direction_color_follows_a_share_convention():
    """A 股红涨绿跌是全局铁律（与 K 线图一致），反过来就是给人反向信号。"""
    assert fmt.UP == "#D94A4A"
    assert fmt.DOWN == "#3E9E7A"
    assert fmt.direction_color(0.12) == fmt.UP
    assert fmt.direction_color(-0.12) == fmt.DOWN


def test_direction_color_zero_is_neutral():
    """平盘不上色：色只用在有意义处（§2.3 第 4 条）。"""
    assert fmt.direction_color(0) == fmt.NEUTRAL
    assert fmt.direction_color(0.0) == fmt.NEUTRAL


def test_direction_color_missing_is_neutral():
    """None/NaN 不能崩、也不能瞎上色。"""
    assert fmt.direction_color(None) == fmt.NEUTRAL
    assert fmt.direction_color(float("nan")) == fmt.NEUTRAL
    assert fmt.direction_color("涨") == fmt.NEUTRAL


def test_direction_color_of_drawdown_is_green():
    """最大回撤恒为负 → 绿色。回撤是亏损，A 股口径下绿色正确，不许因为
    "回撤是坏事所以标红"而反过来——那会和总收益红涨的语义打架。"""
    assert fmt.direction_color(-0.3512) == fmt.DOWN


def test_direction_color_extreme_values():
    assert fmt.direction_color(1e15) == fmt.UP
    assert fmt.direction_color(-1e15) == fmt.DOWN


# ---------------------------------------------------------------- column_config

def test_scan_column_config_covers_scan_csv_columns():
    """列配置的键必须都是扫描 CSV 里真有的列：多写一个键 Streamlit 会静默忽略，
    于是"配好了"的千分位永远不出现。"""
    cfg = fmt.scan_column_config()
    assert set(cfg) <= set(run_market_scan.CSV_COLUMNS), \
        f"配置了不存在的列: {set(cfg) - set(run_market_scan.CSV_COLUMNS)}"
    for col in ("amount", "pct_chg", "amount_ratio_20d", "symbol"):
        assert col in cfg, f"扫描表的关键列 {col} 应有列配置"


def test_scan_column_config_amount_is_grouped_and_right_aligned():
    """金额右对齐 + 千分位（§2.3 第 3 条：眼睛能竖着扫）。"""
    amount = fmt.scan_column_config()["amount"]
    assert amount["alignment"] == "right"
    assert _col_format(amount) == "localized"


def test_scan_column_config_pct_chg_is_signed():
    """涨跌幅带符号，读的人不用去比对昨收。"""
    assert _col_format(fmt.scan_column_config()["pct_chg"]) == "%+.2f%%"


def test_scan_column_config_ratio_is_progress_column():
    """放量倍数是判断信号质量的关键列，用 ProgressColumn 可视化（设计 §2.4）。"""
    ratio = fmt.scan_column_config()["amount_ratio_20d"]
    assert _col_type(ratio) == "progress"
    assert ratio["type_config"]["min_value"] == 0
    assert ratio["type_config"]["max_value"] == fmt.RATIO_BAR_MAX


def test_scan_column_config_ratio_bar_max_covers_real_data():
    """实测扫描结果里出现过 4.22 倍。上限必须容得下真实数据，
    否则常态就是满格，等于没有可视化。"""
    assert fmt.RATIO_BAR_MAX >= 4.22


def test_trades_column_config_covers_trade_fields():
    """trades.csv 的列就是 Trade 的字段（run_backtest.TRADE_COLUMNS）。"""
    cfg = fmt.trades_column_config()
    names = {f.name for f in fields(Trade)}
    assert set(cfg) <= names, f"配置了不存在的列: {set(cfg) - names}"
    for col in ("symbol", "price", "pnl"):
        assert col in cfg


def test_trades_money_columns_are_right_aligned():
    cfg = fmt.trades_column_config()
    for col in ("price", "commission", "pnl"):
        assert cfg[col]["alignment"] == "right", f"{col} 是数字列，必须右对齐"


def test_column_configs_are_plain_dicts_with_labels():
    """返回结构必须是可断言的普通 dict（st.column_config 就是这么实现的），
    且每列都得有中文标签——裸着 amount_ratio_20d 给人看没有意义。"""
    for cfg in (fmt.scan_column_config(), fmt.trades_column_config()):
        for name, col in cfg.items():
            assert isinstance(col, dict), f"{name} 不是 dict，无法断言"
            assert col["label"], f"{name} 缺中文标签"


@pytest.mark.parametrize("builder", ["scan_column_config", "trades_column_config"])
def test_column_configs_are_fresh_objects(builder):
    """必须每次现造：返回同一个模块级 dict 时，调用方（或 Streamlit 自己）
    一改就串到下一次渲染。"""
    build = getattr(fmt, builder)
    assert build() is not build()


# ================================================================ v0.2.1 M2

_SIG_SPEC = importlib.util.spec_from_file_location(
    "run_daily_signal", ROOT / "scripts" / "run_daily_signal.py")
run_daily_signal = importlib.util.module_from_spec(_SIG_SPEC)
_SIG_SPEC.loader.exec_module(run_daily_signal)


# ---------------------------------------------------------------- 指标卡下沉

def test_metric_labels_cover_exactly_the_metrics_json_keys():
    """指标卡的标签表与 fmt_metric 一起下沉到 fmt（§4"UI 层只做组装"）。

    键必须与 compute_metrics 的输出**逐一对齐**：少一项那项指标在面板上人间蒸发，
    多一项则永远显示 —（metrics.get 拿不到），两种都是静默的。
    """
    from quant.report.metrics import compute_metrics

    equity = pd.Series([1.0, 1.1], index=pd.to_datetime(["2016-01-04", "2026-08-26"]))
    assert set(fmt.METRIC_LABELS) == set(compute_metrics(equity, []))


def test_metric_labels_are_chinese_and_ordered_return_first():
    """2×4 网格按字典顺序铺：第一格必须是总收益率（最该先看到的数）。"""
    assert list(fmt.METRIC_LABELS)[0] == "total_return"
    assert fmt.METRIC_LABELS["total_return"] == "总收益率"
    assert "rf=0" in fmt.METRIC_LABELS["sharpe"], "夏普的 rf=0 口径必须写在标签里"


@pytest.mark.parametrize("key, value, text", [
    ("n_trades", 243, "243"),              # int 不许变成 '243.00'
    ("total_return", 1.1642, "116.42%"),
    ("win_rate", 0.41975, "41.98%"),
    ("max_drawdown", -0.3512, "-35.12%"),
    ("sharpe", 0.9621, "0.96"),
    ("profit_factor", None, fmt.MISSING),  # 零平仓时 metrics.py 明确留 None
    ("avg_holding_days", None, fmt.MISSING),
    ("sharpe", float("nan"), fmt.MISSING),
    ("total_return", float("inf"), fmt.MISSING),
])
def test_fmt_metric(key, value, text):
    assert fmt.fmt_metric(key, value) == text


def test_fmt_metric_of_unknown_key_does_not_crash():
    """metrics.json 是文件，将来加了新键（或手工改过）不能崩页。"""
    assert fmt.fmt_metric("brand_new_metric", 1.5) == "1.50"


# ---------------------------------------------------------------- metric_color

def test_metric_color_reds_the_gain_and_greens_the_drawdown():
    """§2.4：总收益/回撤按正负上红绿（A 股口径，与 K 线图一致）。
    实测值走一遍：双均线 +116.1%、回撤 -35.12%。"""
    assert fmt.metric_color("total_return", 1.161) == fmt.UP
    assert fmt.metric_color("cagr", 0.0789) == fmt.UP
    assert fmt.metric_color("max_drawdown", -0.3512) == fmt.DOWN
    assert fmt.metric_color("total_return", -0.2) == fmt.DOWN


@pytest.mark.parametrize("key, value", [
    ("sharpe", -1.5),          # 负夏普不是"跌"，别跟收益率抢红绿
    ("win_rate", 0.4198),      # 胜率没有方向
    ("profit_factor", 0.8),
    ("n_trades", 243),
    ("avg_holding_days", 12.5),
])
def test_metric_color_is_none_for_non_directional_metrics(key, value):
    """§2.3 第 4 条：色只用在有意义处。给胜率上红绿等于制造四个假信号。"""
    assert fmt.metric_color(key, value) is None


@pytest.mark.parametrize("value", [0, 0.0, None, float("nan"), float("inf"), "涨"])
def test_metric_color_is_none_when_flat_or_missing(value):
    """平盘与缺值一律不上色（返回 None = 用默认文字色，不是灰掉）。"""
    assert fmt.metric_color("total_return", value) is None


# ---------------------------------------------------------------- 每日信号列配置

def test_signal_column_config_covers_signal_csv_columns():
    """每日信号 CSV 的列与扫描 CSV **不同**（没有 name/amount/放量倍数）：
    拿 scan_column_config 顶替，多出来的键会被 Streamlit 静默忽略，
    而真正需要中文标签的 action / close 反而没配上。"""
    cfg = fmt.signal_column_config()
    assert set(cfg) <= set(run_daily_signal.CSV_COLUMNS), \
        f"配置了不存在的列: {set(cfg) - set(run_daily_signal.CSV_COLUMNS)}"
    for col in ("symbol", "action", "close", "date", "strategy"):
        assert col in cfg, f"信号表的 {col} 列应有列配置"


def test_signal_column_config_close_is_right_aligned_number():
    close = fmt.signal_column_config()["close"]
    assert close["alignment"] == "right"
    assert _col_format(close) == "%.2f"


def test_signal_column_config_has_chinese_labels():
    for name, col in fmt.signal_column_config().items():
        assert col["label"], f"{name} 缺中文标签"


def test_signal_column_config_is_a_fresh_object():
    assert fmt.signal_column_config() is not fmt.signal_column_config()


# ---------------------------------------------------------------- direction_styler

def _css_of(styler) -> dict:
    """Styler 的单元格样式：{(行, 列): [("color", "#xxxxxx")]}。
    _compute() 才会真正求值（否则 ctx 一直是空的，断言全绿但什么都没测）。"""
    styler._compute()
    return {k: dict(v) for k, v in styler.ctx.items() if v}


def test_direction_styler_colors_up_red_and_down_green():
    """column_config 没有条件着色能力（NumberColumn 无 color 参数，实测确认），
    红绿只能走 pandas Styler；st.dataframe 支持它，且 column_config 的
    格式串优先级更高，所以千分位/百分号不会被 Styler 顶掉。"""
    df = pd.DataFrame({"symbol": ["000020", "600519"], "pct_chg": [-5.09, 3.2]})
    css = _css_of(fmt.direction_styler(df, ["pct_chg"]))
    assert css == {(0, 1): {"color": fmt.DOWN}, (1, 1): {"color": fmt.UP}}


def test_direction_styler_leaves_flat_and_missing_cells_uncolored():
    """0 与 NaN（未平仓那行的 pnl、停牌日的空涨跌幅）不许上色，
    否则表里全是彩的，红绿就没信息量了。"""
    df = pd.DataFrame({"pct_chg": [0.0, float("nan"), float("inf")]})
    assert _css_of(fmt.direction_styler(df, ["pct_chg"])) == {}


def test_direction_styler_colors_by_the_same_rule_as_the_metric_cards():
    """着色一律走 direction_color——表格与指标卡不能各有一套判定（那才会出现
    "表格绿、指标卡红"的对撞）。副作用是数值噪声（-1e-9，显示成 -0.00%）也会被判绿；
    真实 pct_chg 是百分点（-5.09 这种量级），这个量级的噪声不存在，不值得为它加阈值。"""
    df = pd.DataFrame({"pct_chg": [-1e-9]})
    assert _css_of(fmt.direction_styler(df, ["pct_chg"])) == {(0, 0): {"color": fmt.DOWN}}


def test_direction_styler_ignores_columns_the_csv_does_not_have():
    """每日信号 CSV 没有 pct_chg：Styler 的 subset 指向不存在的列会
    KeyError 直接崩页（同一个渲染函数要同时喂扫描表和信号表）。"""
    df = pd.DataFrame({"symbol": ["000020"], "close": [11.74]})
    assert _css_of(fmt.direction_styler(df, ["pct_chg", "amount_ratio_20d"])) == {}


def test_direction_styler_survives_an_empty_frame():
    """当日无新信号是常态：只有表头的 CSV 也得能上色不崩。"""
    df = pd.DataFrame({"symbol": [], "pct_chg": []})
    assert _css_of(fmt.direction_styler(df, ["pct_chg"])) == {}


def test_direction_styler_does_not_touch_other_columns():
    """只给方向列上色：把整表染红绿等于宣布收盘价也有涨跌。"""
    df = pd.DataFrame({"close": [11.74], "pct_chg": [-5.09]})
    css = _css_of(fmt.direction_styler(df, ["pct_chg"]))
    assert list(css) == [(0, 1)], css


def test_direction_styler_keeps_the_underlying_frame_intact():
    """Styler 不得改动数据本身（前导零、dtype 都得原样交给 st.dataframe）。"""
    df = pd.DataFrame({"symbol": ["000020"], "pct_chg": [-5.09]})
    styler = fmt.direction_styler(df, ["pct_chg"])
    assert styler.data is df
    assert df["symbol"].tolist() == ["000020"]


# ---------------------------------------------------------------- 个股小结（K线页）

def _trades_df(rows: list[dict], columns: list[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns or ["symbol", "action", "pnl"])


def test_symbol_trade_summary_counts_and_sums():
    """K 线图上方那行小结：成交笔数 + 买卖各几笔 + 已平仓盈亏合计。"""
    df = _trades_df([
        {"symbol": "600519", "action": "buy", "pnl": None},
        {"symbol": "600519", "action": "sell", "pnl": 12345.6},
        {"symbol": "600519", "action": "buy", "pnl": None},
        {"symbol": "600519", "action": "sell", "pnl": -2345.6},
    ])
    assert fmt.symbol_trade_summary(df) == {
        "n_trades": 4, "n_buy": 2, "n_sell": 2, "pnl": pytest.approx(10000.0)}


def test_symbol_trade_summary_of_open_position_has_no_pnl():
    """只买未卖（仍持仓）：pnl 全是 NaN。`sum()` 会给 0.0——那等于宣布"这只不赚不亏"，
    是编出来的数字；必须给 None 让面板显示 —。"""
    df = _trades_df([{"symbol": "600519", "action": "buy", "pnl": None}])
    out = fmt.symbol_trade_summary(df)
    assert out["n_trades"] == 1 and out["n_buy"] == 1 and out["n_sell"] == 0
    assert out["pnl"] is None


def test_symbol_trade_summary_of_empty_frame():
    """该标的一笔没成交（策略从没进场）：全零 + 盈亏 None，不能崩。"""
    assert fmt.symbol_trade_summary(_trades_df([])) == {
        "n_trades": 0, "n_buy": 0, "n_sell": 0, "pnl": None}


def test_symbol_trade_summary_without_pnl_column():
    """老回测目录的 trades.csv 没有 pnl / holding_days 列（v0.1 早期产物）：
    KeyError 会把整个 K 线页崩掉。"""
    df = _trades_df([{"symbol": "600519", "action": "buy"}],
                    columns=["symbol", "action"])
    assert fmt.symbol_trade_summary(df)["pnl"] is None


def test_symbol_trade_summary_ignores_inf_pnl():
    """脏值（手工改过的 CSV）不能把小结变成 'inf'。"""
    df = _trades_df([{"symbol": "600519", "action": "sell", "pnl": float("inf")},
                     {"symbol": "600519", "action": "sell", "pnl": 100.0}])
    assert fmt.symbol_trade_summary(df)["pnl"] == pytest.approx(100.0)


# ---------------------------------------------------------------- 标的选择器标签

@pytest.mark.parametrize("name, label", [
    ("贵州茅台", "600519 贵州茅台"),
    (None, "600519"),          # 名称未知时不许显示 "600519 None"
    ("", "600519"),
    (float("nan"), "600519"),   # 从 CSV 读出来的空单元格
    ("   ", "600519"),
])
def test_symbol_label(name, label):
    """§2.4：标的选择器要显示"代码 名称"。名称来源是扫描 CSV，未必有——
    没有就老实只显示代码，绝不渲染 'None' / 'nan'。"""
    assert fmt.symbol_label("600519", name) == label


# ---------------------------------------------------------------- signed_color

def test_signed_color_is_the_shared_red_green_gate():
    """指标卡、个股小结共用同一道判定：有方向给红绿，平盘/缺值给 None（不上色）。
    None 而不是灰色——把正常数字灰掉会让它比标题还暗。"""
    assert fmt.signed_color(195.0) == fmt.UP
    assert fmt.signed_color(-195.0) == fmt.DOWN
    assert fmt.signed_color(0) is None
    assert fmt.signed_color(None) is None
    assert fmt.signed_color(float("nan")) is None
    assert fmt.signed_color("盈") is None


def test_metric_color_reuses_signed_color():
    """两处判定必须是同一个函数，否则"表格绿、指标卡红"的对撞就会回来。"""
    assert fmt.metric_color("total_return", 1.161) == fmt.signed_color(1.161)


# -------------------------------------------------- 实跑面板时发现的两处显示缺陷（M2）

def test_scan_column_config_labels_every_column():
    """扫描表**每一列**都要有中文标签。漏一列的表现很轻微（表头出现一个裸
    'date'），但那正是"配好了"的错觉：实跑 2026-08-25 那份扫描时就漏在这里。
    改成相等而不是子集：将来 CSV 加了列，这条会提醒去配它。"""
    assert set(fmt.scan_column_config()) == set(run_market_scan.CSV_COLUMNS)


def test_signal_column_config_labels_every_column():
    assert set(fmt.signal_column_config()) == set(run_daily_signal.CSV_COLUMNS)


def test_trades_pnl_column_is_wide_enough_for_a_negative_amount():
    """实跑发现的显示缺陷：默认列宽装不下 "-58,419.224"，表格网格从**左边**裁字，
    于是一笔亏 5.8 万渲染成 "8,419.224" —— 亏损看起来像盈利，是最坏的那种假信息。
    盈亏是本表唯一会出现"负号 + 千分位 + 五位整数"的列，给它一档宽度。"""
    assert fmt.trades_column_config()["pnl"]["width"] == "medium"


# -------------------------------------------------- 被跳过的订单表（列与成交表不同）

_BT_SPEC = importlib.util.spec_from_file_location(
    "run_backtest", ROOT / "scripts" / "run_backtest.py")
run_backtest = importlib.util.module_from_spec(_BT_SPEC)
_BT_SPEC.loader.exec_module(run_backtest)


def test_skipped_column_config_matches_the_skipped_csv():
    """skipped.csv 只有 date/symbol/reason，与成交明细**不是**一套列。
    拿 trades_column_config 顶替时页面照样能跑：多出来的 7 个键被 Streamlit
    静默忽略，而真正要解释的 reason（涨停/资金不足）连中文标签都没有。"""
    cfg = fmt.skipped_column_config()
    assert set(cfg) == set(run_backtest.SKIPPED_COLUMNS)
    for name, col in cfg.items():
        assert col["label"], f"{name} 缺中文标签"


def test_skipped_reason_column_is_wide_enough_to_read():
    """原因是这张表唯一有信息量的列（"涨停无法买入"/"资金不足"），别缩成一条。"""
    assert fmt.skipped_column_config()["reason"]["width"] == "medium"


# -------------------------------------------------- 显示名与内部键分离（v0.4.0 M1）
# 数据文件（扫描/信号 CSV、回测目录名）照旧存键；下面两个函数只在**显示层**换成
# 中文显示名。未知键一律原样通行——旧产物里的键永远能显示，绝不抛错。


def test_run_label_replaces_the_strategy_key_with_its_label():
    """回测目录名 {策略键}_{YYYYMMDD}_{HHMMSS} → 「策略显示名 时间戳」。"""
    assert fmt.run_label("ma_cross_20260817_121152") == "双均线交叉 20260817_121152"
    assert fmt.run_label("donchian_20260824_151600") == "唐奇安通道突破 20260824_151600"


def test_run_label_keeps_unknown_names_readable_and_never_raises():
    """未知策略键（已下架策略的旧产物）原样显示键；没有时间戳后缀的目录名
    （手工改过名的产物）整个原样返回——这两种目录都真会留在 output/ 里。"""
    assert fmt.run_label("turtle_20260817_121152") == "turtle 20260817_121152"
    assert fmt.run_label("我改过名的目录") == "我改过名的目录"


def test_map_strategy_labels_touches_only_the_strategy_column():
    df = pd.DataFrame({"symbol": ["000333", "000020"],
                       "strategy": ["ma_cross", "donchian"],
                       "close": [71.5, 11.74]})
    out = fmt.map_strategy_labels(df)
    assert out["strategy"].tolist() == ["双均线交叉", "唐奇安通道突破"]
    assert out["symbol"].tolist() == ["000333", "000020"], "别的列一个字不许动"
    assert out["close"].tolist() == [71.5, 11.74]
    assert df["strategy"].tolist() == ["ma_cross", "donchian"], \
        "不许改原 df：预填（prefills）读的还是键"


def test_map_strategy_labels_passes_unknown_keys_and_absent_column_through():
    """未知键原样显示（旧产物兼容）；没有 strategy 列的表（成交明细、被跳过表）
    原样返回，不 KeyError。"""
    assert (fmt.map_strategy_labels(pd.DataFrame({"strategy": ["turtle"]}))
            ["strategy"].tolist() == ["turtle"])
    df = pd.DataFrame({"symbol": ["000333"]})
    assert fmt.map_strategy_labels(df).equals(df)
