# tests/test_journal_schema.py — 交易日志的字段定义与校验规则（v0.3.0 设计 §2 + §3）
#
# 这个文件真正在守的是设计 §3 那条**原则**：
#
#   「阻断只用于逻辑上不可能的事（未来日期）。其余一律警告 + 记下来 ——
#     这是日志，首要职责是如实记录发生了什么，而不是替用户否定现实。」
#
# 写反的代价是不对称的，两个方向都致命：
#   - 该警告的写成阻断 → 用户明明这么成交了，系统不让记（缓存里没这只票、
#     盘后大宗、漏记过买入导致的"卖超"），日志从此有缺口，后面的 FIFO 全错；
#   - 该阻断的写成警告 → 一行没有股数的买入被记下来，持仓与盈亏静默变成 NaN。
# 所以下面用 BLOCKING_CODES 把这条语义钉成**一处**可断言的事实，
# 再逐条正反用例把它焊死。
from datetime import date

import pandas as pd
import pytest

from quant.backtest.costs import commission, stamp_tax
from quant.config import Costs, StampTaxRule
from quant.journal import schema

CFG = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)

TODAY = date(2026, 8, 28)


def _buy(**over) -> dict:
    row = dict(date=date(2026, 8, 27), symbol="000333", kind="buy",
               shares=100.0, price=71.5, source="discretionary")
    row.update(over)
    return row


def _sell(**over) -> dict:
    return _buy(kind="sell", **over)


def codes(issues) -> set[str]:
    return {i.code for i in issues}


# ================================================================ 字段与行类型（§2.2 / §2.3）

def test_four_kinds_exist():
    """四种 kind 缺一不可：少了 adjust，送股后的持仓与券商对不上；
    少了 dividend，长期持有的盈亏被系统性低估（设计 §2.2）。"""
    assert set(schema.KINDS) == {"buy", "sell", "adjust", "dividend"}


def test_columns_match_design_field_table():
    """字段表（设计 §2.3）是存储格式的契约：CSV 的列顺序由它决定，
    改动会让已落盘的 journal/trades.csv 与代码对不上，所以钉死在这里。"""
    assert schema.COLUMNS == (
        "trade_id", "date", "time", "symbol", "name", "kind",
        "shares", "price", "amount", "fee", "tax",
        "source", "stop_plan", "reason", "note",
    )


def test_sources_are_derived_from_the_strategy_registry():
    """source 是设计里学习价值最高的字段：分组统计"跟信号做"与"自己拍脑袋"
    谁更赚钱。合法值 = 全部注册策略 + 两个固定项（v0.4.0 M1 起派生，
    不再手写死——手写的四元组在加第三个策略那天就欠一个座位）。"""
    from quant.strategy import REGISTRY

    assert schema.SOURCES == tuple(REGISTRY) + ("discretionary", "other")
    # v0.4.0 M3 的验收点：第三个策略**不改一行 schema 代码**就拿到了自己的座位。
    # 少了它，"照 tsmom 信号做的交易"会被 journal_ui.prefills 兜底成 other，
    # 「按来源对比谁更赚钱」于是静默丢掉这一整类交易，页面不报错。
    for key in ("ma_cross", "donchian", "tsmom"):
        assert key in schema.SOURCES, f"SOURCES 缺 {key}"


def test_sources_follow_a_new_strategy_registration():
    """往 REGISTRY 塞个假策略，SOURCES 必须自动跟上（设计 §1.3）。
    SOURCES 是 import 时求值的元组，所以按真实路径验证：注册后重新加载本模块
    ——新装的策略正是走这条路（先注册、后 import journal）。"""
    import importlib

    from quant.strategy import REGISTRY
    from quant.strategy.base import Strategy

    class Fake(Strategy):
        name = "fake_v99"
        label = "假策略"

        def generate_positions(self, df):
            raise NotImplementedError

    REGISTRY["fake_v99"] = Fake
    try:
        reloaded = importlib.reload(schema)
        assert "fake_v99" in reloaded.SOURCES
        assert reloaded.SOURCES[-2:] == ("discretionary", "other")
    finally:
        del REGISTRY["fake_v99"]
        importlib.reload(schema)   # 恢复原状，别让假策略漏进后面的测试


# ================================================================ 阻断 vs 警告（本文件的核心）

def test_only_impossible_facts_block():
    """一处定义、全局对账：可阻断的码只有"这行根本没记下任何事实"那几种，
    再加设计明写的未来日期。价格越界/卖超/非整手/查不到名称/非交易日一律不在其中。"""
    assert schema.BLOCKING_CODES == frozenset({
        schema.MISSING, schema.BAD_KIND, schema.BAD_NUMBER,
        schema.BAD_DATE, schema.FUTURE_DATE,
    })
    for code in (schema.PRICE_OUT_OF_RANGE, schema.OVERSELL, schema.ODD_LOT,
                 schema.SYMBOL_UNKNOWN, schema.SYMBOL_FORMAT, schema.NON_TRADING_DAY):
        assert code not in schema.BLOCKING_CODES


def test_future_date_blocks():
    """唯一"逻辑上不可能"的事：明天的成交。这条是设计里指名要阻断的。"""
    issues = schema.validate_trade(_buy(date=date(2026, 8, 29)), today=TODAY)

    assert schema.FUTURE_DATE in codes(issues)
    assert schema.has_blocking(issues)


def test_today_is_not_a_future_date():
    """边界：今天成交今天记是最常见的用法，差一天就把主路径堵死。"""
    assert not schema.has_blocking(schema.validate_trade(_buy(date=TODAY), today=TODAY))


def test_price_far_outside_day_range_warns_but_never_blocks():
    """录入时打错一位（7.15 记成 71.5）——这是本功能最实用的一条校验，
    但只能警告：缓存可能没有这只票，也可能是盘后/大宗成交。"""
    ctx = schema.Context(price_range=(7.10, 7.30))

    issues = schema.validate_trade(_buy(symbol="601398", price=71.5), today=TODAY, context=ctx)

    assert schema.PRICE_OUT_OF_RANGE in codes(issues)
    assert not schema.has_blocking(issues)
    msg = next(i.message for i in issues if i.code == schema.PRICE_OUT_OF_RANGE)
    assert "7.10" in msg and "7.30" in msg          # 区间要说出来，否则用户无从判断


def test_price_inside_day_range_is_silent():
    ctx = schema.Context(price_range=(7.10, 7.30))
    assert codes(schema.validate_trade(_buy(price=7.15), today=TODAY, context=ctx)) == set()


def test_price_range_boundaries_are_inclusive():
    """恰好成交在最低价/最高价是每天都在发生的事，不许报。"""
    ctx = schema.Context(price_range=(7.10, 7.30))
    assert codes(schema.validate_trade(_buy(price=7.10), today=TODAY, context=ctx)) == set()
    assert codes(schema.validate_trade(_buy(price=7.30), today=TODAY, context=ctx)) == set()


def test_no_price_range_means_no_guess():
    """缓存里没有这一天（新股/停牌/没拉过）时不给区间，就什么都不说。
    编一个"区间未知所以可疑"的警告只会训练用户忽略所有警告。"""
    assert codes(schema.validate_trade(_buy(price=999.0), today=TODAY)) == set()


def test_oversell_warns_but_never_blocks():
    """卖超多半是漏记了买入，事后补一笔就好；阻断只会让用户没法记下真实发生的卖出。"""
    ctx = schema.Context(position_shares=100.0)

    issues = schema.validate_trade(_sell(shares=500.0), today=TODAY, context=ctx)

    assert schema.OVERSELL in codes(issues)
    assert not schema.has_blocking(issues)
    # 当前持仓要写进警告，否则用户不知道差多少、也就不知道该补哪一笔买入
    assert "100" in next(i.message for i in issues if i.code == schema.OVERSELL)


def test_sell_within_position_is_silent():
    ctx = schema.Context(position_shares=500.0)
    assert codes(schema.validate_trade(_sell(shares=500.0), today=TODAY, context=ctx)) == set()


def test_unknown_position_means_no_oversell_check():
    """还没算持仓（或标的从没买过记录）时不猜。"""
    assert codes(schema.validate_trade(_sell(shares=500.0), today=TODAY)) == set()


def test_odd_lot_buy_warns_but_never_blocks():
    """买入非整手：可转债、科创板、配售零股都合法，只能提醒。"""
    issues = schema.validate_trade(_buy(shares=150.0), today=TODAY)

    assert schema.ODD_LOT in codes(issues)
    assert not schema.has_blocking(issues)


def test_odd_lot_rule_does_not_apply_to_sell():
    """卖出零股不但合法，还是 A 股规则要求的（零股必须一次性卖出）。
    对卖出也报非整手，等于每次清仓零股都被警告一次——警告会因此贬值。"""
    assert codes(schema.validate_trade(_sell(shares=150.0), today=TODAY)) == set()


def test_symbol_not_in_listing_warns_but_never_blocks():
    """新股/退市股不在清单里是常态，不阻断（设计 §3）。"""
    ctx = schema.Context(names={"600519": "贵州茅台"})

    issues = schema.validate_trade(_buy(symbol="000333"), today=TODAY, context=ctx)

    assert schema.SYMBOL_UNKNOWN in codes(issues)
    assert not schema.has_blocking(issues)


def test_symbol_in_listing_is_silent():
    ctx = schema.Context(names={"000333": "美的集团"})
    assert codes(schema.validate_trade(_buy(), today=TODAY, context=ctx)) == set()


def test_no_listing_means_no_symbol_warning():
    """还没跑过全市场扫描（没有 symbols.parquet）时，不能把每一笔都判成可疑。"""
    assert codes(schema.validate_trade(_buy(), today=TODAY)) == set()


def test_non_trading_day_warns_but_never_blocks():
    """2026-08-29 是周六。非交易日多半是把日期记错了，但也可能是场外/协议转让。"""
    ctx = schema.Context(trading_days=frozenset({date(2026, 8, 27), date(2026, 8, 28)}))

    issues = schema.validate_trade(_buy(date=date(2026, 8, 22)), today=TODAY, context=ctx)

    assert schema.NON_TRADING_DAY in codes(issues)
    assert not schema.has_blocking(issues)


def test_unknown_calendar_means_no_trading_day_check():
    """没给交易日历就不猜——本地缓存覆盖不到的老日期会全军覆没。"""
    assert codes(schema.validate_trade(_buy(date=date(2026, 8, 22)), today=TODAY)) == set()


def test_six_digit_symbol_format_warns_but_never_blocks():
    """非 6 位数字大概率是打错了，但港股通/基金/新代码规则都可能不是 6 位数字，
    而这是日志——先记下来。"""
    issues = schema.validate_trade(_buy(symbol="333"), today=TODAY)

    assert schema.SYMBOL_FORMAT in codes(issues)
    assert not schema.has_blocking(issues)


def test_leading_zero_symbol_is_valid():
    """000333 必须原样合法。本项目已两次踩过前导零被吃掉的坑。"""
    assert codes(schema.validate_trade(_buy(symbol="000333"), today=TODAY)) == set()


# ================================================================ 必填（§2.2 的必填列）

def test_buy_without_shares_blocks():
    """一行没有股数的买入不是"发生过的事实"，是一张没填完的表单：
    放它进去，FIFO 配对拿到 NaN，持仓与盈亏静默变成 NaN 而全程不报错——
    正是本项目一路在防的失败模式。"""
    issues = schema.validate_trade(_buy(shares=None), today=TODAY)

    assert schema.MISSING in codes(issues)
    assert schema.has_blocking(issues)
    assert any(i.field == "shares" for i in issues)


def test_buy_without_price_blocks():
    issues = schema.validate_trade(_buy(price=None), today=TODAY)
    assert schema.has_blocking(issues) and any(i.field == "price" for i in issues)


def test_sell_requires_shares_and_price():
    issues = schema.validate_trade(_sell(shares=None, price=None), today=TODAY)
    assert {i.field for i in issues if i.blocking} == {"shares", "price"}


def test_adjust_requires_shares_and_allows_negative():
    """送股是 +，缩股/拆股回调是 −，都合法；0 股的调整没有意义。
    adjust 不需要成交价（送股没有价格），要价格才是写错了规则。"""
    assert codes(schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="adjust", shares=-100.0), today=TODAY)) == set()
    assert codes(schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="adjust", shares=300.0), today=TODAY)) == set()

    zero = schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="adjust", shares=0.0), today=TODAY)
    assert schema.BAD_NUMBER in codes(zero) and schema.has_blocking(zero)

    missing = schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="adjust"), today=TODAY)
    assert schema.MISSING in codes(missing)


def test_dividend_requires_amount_only():
    """分红只有到账金额，没有股数与成交价——对它要求股数就等于逼用户编数字。"""
    assert codes(schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="dividend", amount=328.0), today=TODAY)) == set()

    issues = schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="dividend"), today=TODAY)
    assert schema.has_blocking(issues) and any(i.field == "amount" for i in issues)


def test_dividend_ignores_odd_lot_and_price_range_rules():
    """分红行没有股数/价格，按买入的规则去套它会凭空造出两条假警告。"""
    ctx = schema.Context(price_range=(7.10, 7.30))
    assert codes(schema.validate_trade(
        dict(date=TODAY, symbol="000333", kind="dividend", amount=328.0),
        today=TODAY, context=ctx)) == set()


def test_missing_date_or_symbol_blocks():
    """没有日期 = 不知道什么时候发生；没有代码 = 不知道是哪只票。都无从记录。"""
    assert schema.has_blocking(schema.validate_trade(_buy(date=None), today=TODAY))
    assert schema.has_blocking(schema.validate_trade(_buy(symbol=""), today=TODAY))


def test_unparsable_date_blocks_and_says_so():
    issues = schema.validate_trade(_buy(date="八月二十七"), today=TODAY)
    assert schema.BAD_DATE in codes(issues) and schema.has_blocking(issues)


def test_iso_string_date_is_accepted():
    """面板给的是 date 对象，CSV 与脚本给的是字符串，两边都得认。"""
    assert codes(schema.validate_trade(_buy(date="2026-08-27"), today=TODAY)) == set()


def test_unknown_kind_blocks():
    """kind 写错（比如 split）时这行无法解释：FIFO 会直接跳过它，
    持仓少了一截而没有任何提示。"""
    issues = schema.validate_trade(_buy(kind="split"), today=TODAY)
    assert schema.BAD_KIND in codes(issues) and schema.has_blocking(issues)


def test_negative_price_and_shares_block():
    """负价格、负股数的买入是逻辑上不可能的事，与未来日期同级。"""
    assert schema.BAD_NUMBER in codes(schema.validate_trade(_buy(price=-7.1), today=TODAY))
    assert schema.BAD_NUMBER in codes(schema.validate_trade(_buy(shares=-100.0), today=TODAY))


# ================================================================ 一行多个问题

def test_all_issues_are_reported_not_just_the_first():
    """一行可以同时是未来日期 + 卖超 + 非整手。只报第一个的话，
    用户改完一个又冒一个，改到第三次就不看了。"""
    ctx = schema.Context(position_shares=0.0, names={"600519": "贵州茅台"})
    issues = schema.validate_trade(
        _buy(date=date(2026, 9, 1), shares=150.0), today=TODAY, context=ctx)

    assert {schema.FUTURE_DATE, schema.ODD_LOT, schema.SYMBOL_UNKNOWN} <= codes(issues)


def test_warnings_survive_alongside_a_blocking_issue():
    """有阻断项时也不许把警告吞掉：用户修完日期重提，才发现还有个卖超——
    两次往返都是白付的。"""
    ctx = schema.Context(position_shares=0.0)
    issues = schema.validate_trade(_sell(date=date(2026, 9, 1), shares=500.0),
                                   today=TODAY, context=ctx)

    assert schema.FUTURE_DATE in codes(issues) and schema.OVERSELL in codes(issues)


def test_a_complete_ordinary_row_produces_nothing():
    """主路径必须绝对安静。任何"每笔都会响"的警告都会让整套告警失效。"""
    ctx = schema.Context(names={"000333": "美的集团"}, price_range=(70.0, 72.0),
                         position_shares=1000.0, trading_days=frozenset({date(2026, 8, 27)}))
    row = dict(date=date(2026, 8, 27), time="14:35", symbol="000333", name="美的集团",
               kind="sell", shares=100.0, price=71.5, amount=7150.0, fee=5.0, tax=3.58,
               source="ma_cross", stop_plan=68.0, reason="跌破 20 日均线", note="")

    assert schema.validate_trade(row, today=TODAY, context=ctx) == []


# ================================================================ 费用默认值（与回测同口径）

def test_buy_fee_uses_the_backtest_commission_model():
    """默认费用必须调既有的 backtest/costs.py：日志与回测的费用口径一旦分叉，
    "我的实盘 vs 策略回测"这个并排比较就不成立了（设计 §4.2）。
    手算：100 股 × 71.50 = 7150 元，万 2.5 = 1.7875 → 不足 5 元，按最低 5 元。"""
    d = schema.apply_defaults(_buy(), costs=CFG)

    assert d["amount"] == pytest.approx(7150.0)
    assert d["fee"] == pytest.approx(5.0)
    assert d["fee"] == pytest.approx(round(commission(7150.0, CFG), 2))
    assert d["tax"] == pytest.approx(0.0)          # 印花税仅卖出


def test_commission_above_the_floor_is_rounded_to_cents():
    """手算：1000 股 × 71.50 = 71500 元，× 0.00025 = 17.875 → 17.88（记的是真钱，分为止）。"""
    d = schema.apply_defaults(_buy(shares=1000.0), costs=CFG)

    assert d["amount"] == pytest.approx(71500.0)
    assert d["fee"] == pytest.approx(17.88)


def test_sell_adds_stamp_tax_at_the_rate_of_that_day():
    """手算：71500 × 0.0005 = 35.75（2023-08-28 起的税率）。"""
    d = schema.apply_defaults(_sell(shares=1000.0, date=date(2026, 8, 27)), costs=CFG)

    assert d["fee"] == pytest.approx(17.88)
    assert d["tax"] == pytest.approx(35.75)
    assert d["tax"] == pytest.approx(round(stamp_tax(71500.0, date(2026, 8, 27), CFG), 2))


def test_stamp_tax_follows_the_trade_date_not_today():
    """补记 2023-08-25 的卖出时用的必须是当时的千一：71500 × 0.001 = 71.50。
    用今天的税率算历史成交，是一个永远不会报错的错数字。"""
    d = schema.apply_defaults(_sell(shares=1000.0, date=date(2023, 8, 25)), costs=CFG)
    assert d["tax"] == pytest.approx(71.50)


def test_adjust_and_dividend_have_no_fees():
    """送股与分红到账不产生佣金/印花税，也不产生买卖现金流。"""
    adj = schema.apply_defaults(
        dict(date=TODAY, symbol="000333", kind="adjust", shares=300.0), costs=CFG)
    assert (adj["fee"], adj["tax"], adj["amount"]) == (0.0, 0.0, 0.0)

    div = schema.apply_defaults(
        dict(date=TODAY, symbol="000333", kind="dividend", amount=328.0), costs=CFG)
    assert (div["fee"], div["tax"]) == (0.0, 0.0)
    assert div["amount"] == pytest.approx(328.0)   # 到账金额是用户填的，不许被推导覆盖


def test_user_supplied_values_are_never_overwritten():
    """券商实际扣费与模型算出来的几乎不会完全相等（各家佣金不同、有过户费）。
    设计明写"可改成券商实际值"——那么填了就必须留住。"""
    d = schema.apply_defaults(_sell(shares=1000.0, fee=23.0, tax=35.0, amount=71499.0),
                              costs=CFG)

    assert (d["fee"], d["tax"], d["amount"]) == (23.0, 35.0, 71499.0)


def test_explicit_zero_fee_is_kept_not_treated_as_missing():
    """0 是合法的显式值（免佣活动、场内基金）。用 `if not row["fee"]` 判空的写法
    会把它当成没填，然后默默替用户改成 5 元。"""
    assert schema.apply_defaults(_buy(fee=0.0), costs=CFG)["fee"] == 0.0


def test_name_is_filled_from_the_listing():
    """名称从 data/symbols.parquet 自动带出（离线可用，v0.2.3 的产物）。"""
    d = schema.apply_defaults(_buy(), costs=CFG, names={"000333": "美的集团"})
    assert d["name"] == "美的集团"


def test_unknown_symbol_leaves_the_name_empty_rather_than_inventing_one():
    """查不到就留空（校验那边会给警告）。填"未知"看着像个名字，最坏。"""
    d = schema.apply_defaults(_buy(), costs=CFG, names={"600519": "贵州茅台"})
    assert d["name"] == ""


def test_user_supplied_name_wins_over_the_listing():
    """清单最多可能滞后 7 天（is_fresh 的窗口），改过名的以用户写的为准。"""
    d = schema.apply_defaults(_buy(name="美的集团(改名后)"), costs=CFG,
                              names={"000333": "美的集团"})
    assert d["name"] == "美的集团(改名后)"


def test_apply_defaults_returns_every_column_and_does_not_mutate_input():
    row = _buy()
    d = schema.apply_defaults(row, costs=CFG)

    assert set(d) == set(schema.COLUMNS)
    assert "fee" not in row                        # 原 dict 不许被就地改


def test_apply_defaults_rejects_unknown_fields():
    """字段名打错（symbol_code）静默丢弃的话，落盘的是一行没有代码的记录。"""
    with pytest.raises(ValueError, match="symbol_code"):
        schema.apply_defaults(_buy(symbol_code="000333"), costs=CFG)


def test_apply_defaults_leaves_broken_rows_alone_instead_of_guessing():
    """缺股数的行不许被"补"成 amount=0：0 元买入是个看着正常的错数字，
    而校验那边已经把这行判成阻断了。"""
    d = schema.apply_defaults(_buy(shares=None), costs=CFG)
    assert d["amount"] is None and d["fee"] is None and d["tax"] is None


# ---------------------------------------------------------------- 表格里清空的日期（2026-11-16 审查抓到）

def test_a_cleared_date_cell_is_blank_not_a_date():
    """可编辑表格里把日期那一格清空，交回来的是 `pd.NaT`。它是 datetime 的一个**实例**
    （`isinstance(pd.NaT, datetime)` 为真），按"是日期就不算空"的老写法会被当成一个真日期，
    一路走到"晚不晚于今天"的比较才炸成 TypeError——页面上是一屏 traceback，而不是设计好的
    那句「缺少成交日期」。空就是空，不管它是哪种空。"""
    assert schema.is_blank(pd.NaT) is True
    assert schema.parse_date(pd.NaT) is None


def test_a_row_whose_date_was_cleared_is_refused_in_words_not_with_a_crash():
    issues = schema.validate_trade(_buy(date=pd.NaT), today=TODAY)
    assert schema.MISSING in codes(issues), codes(issues)
    assert schema.has_blocking(issues)
    assert any("成交日期" in i.message for i in schema.blocking_issues(issues))


# ---------------------------------------------------------------- 校验信息是给填表的人看的（2026-11-16）

@pytest.mark.parametrize("row", [
    dict(kind="buy", shares=None, price=None),
    dict(kind="sell", shares=None, price=None),
    dict(kind="adjust", shares=None),
    dict(kind="dividend", amount=None),
    dict(kind="buy", shares="abc", price="xyz"),
    dict(kind="buy", shares=-100.0, price=71.5),
    dict(kind="sell", shares=-100.0, price=71.5),
])
def test_validation_messages_name_things_the_way_the_page_does(row):
    """空着表单点「记这一笔」，页面上第一句报错原先是「buy 必须填写 shares；buy 必须填写 price」
    ——内部的键原样甩给了填表的人（用户点名过的「中文里不夹英文值」）。
    信息里说的是页面上的字：买入、股数、成交价、到账金额。"""
    base = dict(date=date(2026, 8, 27), symbol="000333", source="discretionary")
    text = "；".join(i.message for i in schema.validate_trade({**base, **row}, today=TODAY))
    assert text, "前提：这一行有问题可报"
    for key in ("buy", "sell", "adjust", "dividend", "shares", "price", "amount"):
        assert key not in text, f"校验信息里冒出了内部键 {key!r}: {text}"


def test_missing_numbers_are_named_in_chinese():
    text = "；".join(i.message for i in schema.validate_trade(
        _buy(shares=None, price=None), today=TODAY))
    assert "买入必须填写股数" in text and "买入必须填写成交价" in text, text
    text = "；".join(i.message for i in schema.validate_trade(
        dict(date=date(2026, 8, 27), symbol="000333", kind="dividend", amount=None), today=TODAY))
    assert "现金分红必须填写到账金额" in text, text


def test_the_kind_names_cover_every_record_type():
    assert set(schema.KIND_NAMES) == set(schema.KINDS)


# ---------------------------------------------------------------- 类型空着 / 类型认不出来（2026-11-18）
#
# 历史表里「类型」是个下拉格子，选中它按删除键就清空了。清空的格子交回来的不是 None，是 NaN，
# 而 NaN 算真值——`str(NaN or "")` 是 "nan" 三个字母。于是点「保存修改」被拦下时（拦下是对的），
# 页面上那句话成了「无法解释的记录类型 'nan'，只支持 buy/sell/adjust/dividend」：
# 填表的人没填过 nan 这个词；后面四个是存进文件的键，页面上别处都叫买入 / 卖出 / 股数调整 / 现金分红。

def _about_kind(row: dict) -> list:
    return [i for i in schema.validate_trade(row, today=TODAY) if i.field == "kind"]


@pytest.mark.parametrize("blank", [float("nan"), pd.NA, pd.NaT, None, "", "   "],
                         ids=["NaN", "NA", "NaT", "None", "空串", "只有空白"])
def test_a_blank_kind_is_a_missing_kind_whichever_blank_it_is(blank):
    """空就是空，不管它是哪种空：表格里清空的格子（NaN）与表单里没选（None / 空串）是同一件事，
    走的都是「缺少」那一支，不是「类型认不出来」——更不许崩（`pd.NA or ""` 会当场抛 TypeError）。"""
    found = _about_kind(_buy(kind=blank))
    assert [i.code for i in found] == [schema.MISSING], [(i.code, i.message) for i in found]
    assert found[0].blocking


def test_a_cleared_kind_cell_is_refused_in_the_pages_own_words():
    """清空的格子，说的是「缺少记录类型」和页面上那四个名字——没有 nan，没有存进文件的键。"""
    said = "；".join(i.message for i in _about_kind(_buy(kind=float("nan"))))
    assert "类型" in said, said
    assert "nan" not in said.lower() and "None" not in said, said
    for key in schema.KINDS:
        assert key not in said, f"校验信息里冒出了存进文件的键 {key!r}: {said}"
    for name in schema.KIND_NAMES.values():
        assert name in said, f"没告诉他可以选「{name}」: {said}"


def test_an_unknown_kind_is_quoted_and_the_choices_are_listed_by_their_page_names():
    """类型是个认不出来的值（页面上填不出来，手改文件才会有）：点出那个值——他得知道是哪个词不对，
    再用页面上的名字列出可选的几种。照样是阻断，问题码不变。"""
    found = _about_kind(_buy(kind="split"))
    assert [i.code for i in found] == [schema.BAD_KIND] and found[0].blocking
    said = found[0].message
    assert "split" in said, said
    for key in schema.KINDS:
        assert key not in said, f"校验信息里冒出了存进文件的键 {key!r}: {said}"
    for name in schema.KIND_NAMES.values():
        assert name in said, f"没列出「{name}」: {said}"


def test_both_kind_messages_list_the_same_names_in_the_same_order():
    """「缺少类型」与「类型认不出来」里列的那几个名字取自同一处（schema.KIND_NAMES），顺序同文件里的四种：
    手写两份的话，一句叫「分红」一句叫「现金分红」。"""
    listed = "/".join(schema.KIND_NAMES[k] for k in schema.KINDS)
    assert listed in _about_kind(_buy(kind=None))[0].message
    assert listed in _about_kind(_buy(kind="split"))[0].message


# ---------------------------------------------------------------- 代码空着（2026-11-19）
#
# 历史表里选中「代码」那一格按删除键，交回来的是 NaN（点进去把字删光交回的是空串，那种一直拦得住）。
# 原先只有空串 / None 算缺代码；NaN 被当成 "nan" 三个字母，判成「代码 'nan' 不是 6 位数字」——那是一条**警告**。
# 保存只看阻断项，于是代码是空的一行存得进去，之后算持仓与盈亏时只能跳过它。

def _about_symbol(row: dict) -> list:
    return [i for i in schema.validate_trade(row, today=TODAY) if i.field == "symbol"]


@pytest.mark.parametrize("blank", [float("nan"), pd.NA, pd.NaT, None, "", "   "],
                         ids=["NaN", "NA", "NaT", "None", "空串", "只有空白"])
def test_a_blank_symbol_is_a_missing_symbol_whichever_blank_it_is(blank):
    found = _about_symbol(_buy(symbol=blank))
    assert [i.code for i in found] == [schema.MISSING], [(i.code, i.message) for i in found]
    assert found[0].blocking, "缺代码的行不许存进去"
    assert "代码" in found[0].message and "nan" not in found[0].message.lower(), found[0].message


@pytest.mark.parametrize("symbol", ["333", "00700", "ABC123", "0003330", "nan"])
def test_a_symbol_that_was_typed_still_only_warns(symbol):
    """反过来不能矫枉过正：填了东西、只是不像 6 位数字（港股通、基金、打错一位），照旧只提醒、照记。
    连真的敲了 nan 三个字母也一样——那是他填的，不是空格子。"""
    found = _about_symbol(_buy(symbol=symbol))
    assert [i.code for i in found] == [schema.SYMBOL_FORMAT] and not found[0].blocking, found


def test_spaces_around_a_good_symbol_are_not_a_problem():
    """代码两头带了空白（粘贴进来的）：照去掉空白之后的 6 位数字认，不提醒。"""
    assert _about_symbol(_buy(symbol=" 000333 ")) == []


# ---------------------------------------------------------------- 校验信息里的数与引号（2026-11-19）
#
# 原先信息里的数用的是「最多六位有效数字」的写法：到一百万就成了科学计数法（买入 1.00005e+06 股），
# 一百二十三万四千五百六十七股的持仓写成 1.23457e+06（还舍掉了一位），1688.125 的成交价写成 1688.12。
# 值外面的引号是程序的写法（代码 '333'）。这些话是给填表的人看的。

def _message(code: str, row: dict, **facts) -> str:
    issues = schema.validate_trade(row, today=TODAY, context=schema.Context(**facts))
    return next(i.message for i in issues if i.code == code)


def _dividend(amount: float) -> dict:
    return dict(date=date(2026, 8, 27), symbol="000333", kind="dividend", amount=amount)


def test_big_share_counts_and_amounts_are_written_out_in_full():
    """写全，带千分位——页面上别处的股数与金额都这么写。"""
    assert "买入 1,000,050 股不是 100 股的整数倍" in _message(schema.ODD_LOT, _buy(shares=1000050.0))
    over = _message(schema.OVERSELL, _sell(shares=1500000.0), position_shares=1234567.0)
    assert "卖出 1,500,000 股" in over and "当前持仓 1,234,567 股" in over, over
    assert "实际 -1,500,000" in _message(schema.BAD_NUMBER, _buy(shares=-1500000.0))
    assert "实际 -1,500,000" in _message(schema.BAD_NUMBER, _dividend(-1500000.0))


def test_everyday_numbers_read_exactly_as_before():
    assert "买入 150 股不是 100 股的整数倍" in _message(schema.ODD_LOT, _buy(shares=150.0))
    over = _message(schema.OVERSELL, _sell(shares=500.0), position_shares=100.0)
    assert "卖出 500 股，超过当前持仓 100 股" in over, over
    assert _message(schema.BAD_NUMBER, _buy(shares=-100.0)).endswith("实际 -100")
    assert _message(schema.BAD_NUMBER, _buy(price=-7.1)).endswith("实际 -7.1")
    assert _message(schema.BAD_NUMBER, _dividend(-5.0)).endswith("实际 -5")
    assert _message(schema.BAD_NUMBER, _buy(shares=0.0)).endswith("实际 0")
    # 写出来是 0 的数不带负号：表里敲 -0，或者一个小到写不出来的负数
    assert _message(schema.BAD_NUMBER, _buy(shares=-0.0)).endswith("实际 0")
    assert _message(schema.BAD_NUMBER, _buy(shares=-1e-9)).endswith("实际 0")
    assert "成交价 715 不在当日区间 [70.10, 72.30] 内" in _message(
        schema.PRICE_OUT_OF_RANGE, _buy(price=715.0), price_range=(70.1, 72.3))


def test_a_price_is_written_as_typed_not_rounded_to_six_digits():
    """成交价照输入的原样写，不带千分位——与紧跟着的当日区间是同一种写法（区间一直是 1600.00 这样）。"""
    said = _message(schema.PRICE_OUT_OF_RANGE, _buy(price=1688.125), price_range=(1600.0, 1650.0))
    assert "成交价 1688.125 不在当日区间 [1600.00, 1650.00] 内" in said, said
    assert _message(schema.BAD_NUMBER, _buy(price=-1688.125)).endswith("实际 -1688.125")
    assert "成交价 0.9855 不在" in _message(
        schema.PRICE_OUT_OF_RANGE, _buy(price=0.9855), price_range=(1.1, 1.2))


@pytest.mark.parametrize("row, facts", [
    (_buy(shares=1000050.0), {}),
    (_buy(shares=-12345678.0, price=-1234567.5), {}),
    (_sell(shares=98765432.0), {"position_shares": 12345678.0}),
    (_buy(shares=100.0, price=1234567.125), {"price_range": (1.0, 2.0)}),
    (_dividend(-98765432.1), {}),
    (dict(date=date(2026, 8, 27), symbol="000333", kind="adjust", shares=0.0), {}),
], ids=["非整手", "负的股数与价格", "卖超", "价格越界", "负的分红", "调整为 0"])
def test_no_validation_message_is_in_scientific_notation(row, facts):
    said = "；".join(i.message for i in schema.validate_trade(
        row, today=TODAY, context=schema.Context(**facts)))
    assert said, "前提：这一行有话可说"
    assert "e+" not in said and "e-" not in said, said


def test_an_odd_symbol_is_quoted_the_way_the_page_quotes_things():
    said = _message(schema.SYMBOL_FORMAT, _buy(symbol="333"))
    assert "代码「333」不是 6 位数字" in said and "'" not in said, said
