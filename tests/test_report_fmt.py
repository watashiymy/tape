# tests/test_report_fmt.py — v0.2.1 §4 数字格式化与方向配色的纯函数。
#
# 面板本身写不了单测（顶层就是 UI 代码），所以"要判断、要格式化"的一律下沉到
# quant.report.fmt，边界全在这里钉死：0 / 负数 / None / NaN / inf / 极大值 / 非数字。
# 这些值都是真会出现的：trades.csv 里未平仓那行 pnl 是空单元格（读出来 NaN），
# metrics.json 里 profit_factor 可能是 null。
import importlib.util
from dataclasses import fields
from pathlib import Path

import pytest

from quant.backtest.portfolio import Trade
from quant.report import fmt

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
