# tests/test_journal_pnl.py — FIFO 批次配对、持仓与已实现盈亏（v0.3.0 设计 §4）
#
# 这是整个交易日志功能的**正确性核心：算错比不算更糟**。
# 一个错的盈亏数字不会报错、不会崩、看着完全正常，用户会拿它做决策；
# 而"不算"至少还诚实。所以本文件的每一个盈亏数字都是**手算的**，
# 期望值连同算式写在注释里 —— 与回测引擎的测试同一套做法。
#
# 手算之所以必须：把期望值写成 `实现里同一个表达式` 等于把实现抄了两遍，
# 两边一起错的时候测试全绿。下面每处的算式都独立于实现，能用计算器复核。
from datetime import date

import pandas as pd
import pytest

from quant.config import Costs, StampTaxRule
from quant.journal import pnl, schema, store
from quant.report.metrics import trade_stats

# 与 test_journal_schema.py 同一套成本参数：费用一律走 backtest/costs.py，
# 日志与回测的口径一旦分叉，"我的实盘 vs 策略回测"这个并排比较就不成立了。
CFG = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


# ---------------------------------------------------------------- 造数据
def _rows(*rows) -> pd.DataFrame:
    """把若干 dict 补全默认值（name/amount/fee/tax）并编上可读的 trade_id。

    刻意走 `schema.apply_defaults`：费用因此与真实录入一模一样（自动按成本模型算），
    手算对照的才是用户真会看到的数字，而不是测试里现编的一套。
    """
    records = []
    for i, row in enumerate(rows, 1):
        record = schema.apply_defaults(row, costs=CFG)
        record["trade_id"] = record["trade_id"] or f"T{i}"
        records.append(record)
    return pd.DataFrame(records, columns=list(schema.COLUMNS))


def _buy(d: date, shares: float, price: float, **over) -> dict:
    row = dict(date=d, symbol="000333", kind="buy", shares=float(shares),
               price=float(price), source="discretionary")
    row.update(over)
    return row


def _sell(d: date, shares: float, price: float, **over) -> dict:
    return _buy(d, shares, price, **{"kind": "sell", **over})


def _adjust(d: date, shares: float, **over) -> dict:
    row = dict(date=d, symbol="000333", kind="adjust", shares=float(shares),
               source="discretionary")
    row.update(over)
    return row


def _dividend(d: date, amount: float, **over) -> dict:
    row = dict(date=d, symbol="000333", kind="dividend", amount=float(amount),
               source="discretionary")
    row.update(over)
    return row


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def by_symbol(report) -> dict:
    return {p.symbol: p for p in report.positions}


# ================================================================ 单买单卖（§4.1 的算式本身）

def test_single_buy_single_sell_hand_computed():
    """手算：
    买 1000 股 @ 10.00 → 名义 10000，佣金 max(10000×0.00025, 5) = 5.00，成本基 10005.00
    卖 1000 股 @ 12.00 → 名义 12000，佣金 max(3.00, 5) = 5.00，
                         印花税 12000×0.0005 = 6.00，净得 12000−5−6 = 11989.00
    已实现盈亏 = 11989.00 − 10005.00 = 1984.00
    持仓天数 = 2026-02-05 − 2026-01-05 = 31 天
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _sell(date(2026, 2, 5), 1000, 12.00),
    ))

    assert len(report.closings) == 1
    closing = report.closings[0]
    assert closing.net_proceeds == 11989.00
    assert closing.cost == 10005.00
    assert closing.pnl == 1984.00
    assert closing.unmatched_shares == 0.0

    assert len(report.matches) == 1
    match = report.matches[0]
    assert match.cost == 10005.00          # 买入费用进成本基（设计 §4.1）
    assert match.proceeds == 11989.00      # 卖出净得已扣佣金与印花税
    assert match.pnl == 1984.00
    assert match.holding_days == 31

    assert report.positions == ()           # 全部平掉，不留 0 股的空行
    assert report.summary["realized_pnl"] == 1984.00
    assert report.summary["n_trades"] == 1
    assert report.summary["win_rate"] == 1.0
    assert report.summary["avg_holding_days"] == pytest.approx(31.0)


def test_buy_fee_is_part_of_cost_basis_not_ignored():
    """漏掉买入佣金，这笔盈亏会变成 1989.00 —— 高估 5 元且永远不报错。
    单独钉一条：成本基必须是 股数×价 + 费用。"""
    report = pnl.compute_pnl(_rows(_buy(date(2026, 1, 5), 1000, 10.00)))
    position = by_symbol(report)["000333"]
    assert position.cost == 10005.00        # 不是 10000.00
    assert position.shares == 1000.0
    assert position.unit_cost == pytest.approx(10.005)


def test_user_entered_fee_wins_over_model():
    """券商实际扣费与模型算的几乎不会相等（各家佣金不同、还有过户费）。
    填了就得用填的：成本基 = 10000 + 12.34。"""
    report = pnl.compute_pnl(_rows(_buy(date(2026, 1, 5), 1000, 10.00, fee=12.34)))
    assert by_symbol(report)["000333"].cost == 10012.34


def test_sell_side_costs_reduce_proceeds():
    """卖出净得必须扣佣金与印花税。不扣的话这笔盈亏是 1995.00（高估 11 元）。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _sell(date(2026, 2, 5), 1000, 12.00),
    ))
    closing = report.closings[0]
    assert closing.fee == 5.00
    assert closing.tax == 6.00
    assert closing.net_proceeds == 12000.00 - 5.00 - 6.00


# ================================================================ 多批次与部分卖出

def test_partial_sell_consumes_oldest_lots_first():
    """手算（三个数字都独立可复核）：
    批次1 2026-01-05：1000 @ 10.00 → 佣金 5.00 → 成本 10005.00
    批次2 2026-01-20： 500 @ 12.00 → 名义 6000，佣金 max(1.5, 5) = 5.00 → 成本 6005.00
    卖出 2026-02-10：1200 @ 11.00 → 名义 13200，佣金 max(3.3, 5) = 5.00，
                     印花税 13200×0.0005 = 6.60 → 净得 13188.40

    FIFO：先吃光批次1 的 1000 股，再从批次2 取 200 股
      批次1 成本 10005.00；批次2 分摊成本 6005.00 × 200/500 = 2402.00
      净得按股数分摊：1000/1200 → 13188.40 × 5/6 = 10990.3333… → 10990.33
                      200/1200 → 余额 13188.40 − 10990.33 = 2198.07
      配对1 盈亏 = 10990.33 − 10005.00 =  985.33
      配对2 盈亏 =  2198.07 −  2402.00 = −203.93
      本次平仓合计 = 781.40（复核：13188.40 − 12407.00 = 781.40 ✓）
    剩余持仓：300 股，成本 6005.00 − 2402.00 = 3603.00，单位成本 12.01
    持仓天数：01-05→02-10 = 36 天；01-20→02-10 = 21 天
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _buy(date(2026, 1, 20), 500, 12.00),
        _sell(date(2026, 2, 10), 1200, 11.00),
    ))

    m1, m2 = report.matches
    assert (m1.buy_trade_id, m1.shares, m1.cost) == ("T1", 1000.0, 10005.00)
    assert (m2.buy_trade_id, m2.shares, m2.cost) == ("T2", 200.0, 2402.00)
    assert m1.proceeds == 10990.33
    assert m2.proceeds == 2198.07
    assert m1.pnl == 985.33
    assert m2.pnl == -203.93
    assert m1.holding_days == 36
    assert m2.holding_days == 21
    assert m1.sell_trade_id == m2.sell_trade_id == "T3"

    closing = report.closings[0]
    assert closing.pnl == 781.40
    assert closing.cost == 12407.00

    position = by_symbol(report)["000333"]
    assert position.shares == 300.0
    assert position.cost == 3603.00
    assert position.unit_cost == pytest.approx(12.01)


def test_matched_costs_and_proceeds_tie_out_to_the_closing():
    """费用分摊到部分卖出的**小数精度**：三笔等额买入被一次卖光，
    净得除以 3 除不尽，逐笔四舍五入后必须仍然加得回总数。

    手算：每批 300 @ 10.00 → 名义 3000，佣金 max(0.75, 5) = 5.00 → 成本 3005.00 ×3
          卖 900 @ 11.00 → 名义 9900，佣金 max(2.475, 5) = 5.00，
                           印花税 9900×0.0005 = 4.95 → 净得 9890.05
          9890.05 / 3 = 3296.68333… → 前两笔 3296.68，末笔取余额 3296.69
          （若三笔都取 3296.68，合计 9890.04，与净得差 1 分 —— 明细就对不上总数了）
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 300, 10.00),
        _buy(date(2026, 1, 6), 300, 10.00),
        _buy(date(2026, 1, 7), 300, 10.00),
        _sell(date(2026, 2, 10), 900, 11.00),
    ))
    closing = report.closings[0]
    assert closing.net_proceeds == 9890.05
    assert [m.proceeds for m in closing.matches] == [3296.68, 3296.68, 3296.69]
    assert sum(m.proceeds for m in closing.matches) == pytest.approx(closing.net_proceeds)
    assert sum(m.cost for m in closing.matches) == pytest.approx(closing.cost)
    assert sum(m.pnl for m in closing.matches) == pytest.approx(closing.pnl)


def test_fifo_not_lifo():
    """两批不同价：先进先出必须吃**便宜的老批次**，盈亏才是 FIFO 口径。
    若实现写成 LIFO，本例的已实现盈亏会从 +994.00 变成 −6.00。

    手算：批次1 1000 @ 10.00 → 成本 10005.00；批次2 1000 @ 11.00 → 名义 11000，
          佣金 max(2.75, 5) = 5.00 → 成本 11005.00
          卖 1000 @ 11.00 → 名义 11000，佣金 5.00，印花税 5.50 → 净得 10989.50
          FIFO：10989.50 − 10005.00 = 984.50（LIFO 会是 −15.50）
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _buy(date(2026, 1, 6), 1000, 11.00),
        _sell(date(2026, 2, 10), 1000, 11.00),
    ))
    assert report.matches[0].buy_trade_id == "T1"
    assert report.summary["realized_pnl"] == 984.50
    assert by_symbol(report)["000333"].cost == 11005.00     # 留下的是贵的那批


def test_same_day_trades_follow_recorded_order():
    """同一天多笔：日期排序必须是**稳定**的，同日按录入顺序处理。
    否则 FIFO 在同日批次间的先后随实现细节漂移 —— 本例会从 984.50 变成 −15.50。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _buy(date(2026, 1, 5), 1000, 11.00),      # 同日、后录、更贵
        _sell(date(2026, 2, 10), 1000, 11.00),
    ))
    assert report.matches[0].buy_trade_id == "T1"
    assert report.summary["realized_pnl"] == 984.50


def test_rows_are_sorted_by_date_regardless_of_file_order():
    """事后补记很常见：2 月的卖出先录、1 月的买入后录。
    按文件顺序算的话这笔会变成"无批次可配"的卖超。"""
    report = pnl.compute_pnl(_rows(
        _sell(date(2026, 2, 5), 1000, 12.00),
        _buy(date(2026, 1, 5), 1000, 10.00),
    ))
    assert report.summary["realized_pnl"] == 1984.00
    assert report.inconsistencies == ()


def test_interleaved_buy_and_sell():
    """买卖交错：买 → 卖一半 → 再买 → 卖光。手算：
    批次1 1000 @ 10.00 → 成本 10005.00（单位成本 10.005）
    卖① 2026-02-10 500 @ 11.00 → 名义 5500，佣金 max(1.375,5)=5.00，
        印花税 2.75 → 净得 5492.25
        消耗批次1 的 500 股：成本 10005.00 × 500/1000 = 5002.50
        盈亏① = 5492.25 − 5002.50 = 489.75，持仓 36 天
    批次2 2026-03-01 200 @ 9.00 → 名义 1800，佣金 5.00 → 成本 1805.00
    卖② 2026-03-12 700 @ 12.00 → 名义 8400，佣金 max(2.1,5)=5.00，
        印花税 4.20 → 净得 8390.80
        FIFO：批次1 余 500 股（成本 10005.00 − 5002.50 = 5002.50），再取批次2 全部 200 股
        净得分摊：500/700 → 8390.80 × 5/7 = 5993.4285… → 5993.43
                  200/700 → 余额 8390.80 − 5993.43 = 2397.37
        盈亏②a = 5993.43 − 5002.50 = 990.93（持仓 2026-01-05→03-12 = 66 天）
        盈亏②b = 2397.37 − 1805.00 = 592.37（持仓 2026-03-01→03-12 = 11 天）
    合计已实现 = 489.75 + 990.93 + 592.37 = 2073.05
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _sell(date(2026, 2, 10), 500, 11.00),
        _buy(date(2026, 3, 1), 200, 9.00),
        _sell(date(2026, 3, 12), 700, 12.00),
    ))
    assert [m.pnl for m in report.matches] == [489.75, 990.93, 592.37]
    assert [m.holding_days for m in report.matches] == [36, 66, 11]
    assert report.summary["realized_pnl"] == 2073.05
    assert report.positions == ()


def test_multiple_symbols_are_independent():
    """两只票的批次不能互相消耗 —— 串了的话盈亏全错且不会报错。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="000333"),
        _buy(date(2026, 1, 6), 1000, 20.00, symbol="600519"),
        _sell(date(2026, 2, 5), 1000, 12.00, symbol="000333"),
    ))
    assert report.summary["realized_pnl"] == 1984.00
    positions = by_symbol(report)
    assert set(positions) == {"600519"}
    assert positions["600519"].shares == 1000.0


# ================================================================ adjust：送股摊薄（§4.1）

def test_adjust_dilutes_unit_cost_but_keeps_total_cost():
    """10 送 3：股数 1000 → 1300，**成本总额不变**（这是正确口径）。
    手算：成本 10005.00 不动，单位成本 10005/1300 = 7.696153… → 7.6962
    若实现把送股当成 0 元买入并入成本，单位成本会变成 7.6962 之外的数；
    若实现改了总成本，后面每一笔卖出的盈亏都跟着错。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _adjust(date(2026, 6, 10), 300),
    ))
    position = by_symbol(report)["000333"]
    assert position.shares == 1300.0
    assert position.cost == 10005.00
    assert position.unit_cost == pytest.approx(7.6962)
    assert report.inconsistencies == ()


def test_sell_after_adjust_uses_diluted_cost():
    """摊薄后卖光。手算：
    成本基仍是 10005.00，股数 1300
    卖 1300 @ 8.00 → 名义 10400，佣金 max(2.6, 5) = 5.00，
                     印花税 10400×0.0005 = 5.20 → 净得 10389.80
    盈亏 = 10389.80 − 10005.00 = 384.80（**是赚的**：送股后 8 元不等于亏）
    持仓天数 = 2026-01-05 → 2026-07-01 = 177 天（按建仓日算，送股不重置）
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _adjust(date(2026, 6, 10), 300),
        _sell(date(2026, 7, 1), 1300, 8.00),
    ))
    assert report.summary["realized_pnl"] == 384.80
    assert report.matches[0].holding_days == 177
    assert report.positions == ()


def test_partial_sell_after_adjust_prorates_cost_by_shares():
    """摊薄后卖一半：成本必须按**股数**比例分摊（10005 × 650/1300 = 5002.50），
    剩下 650 股仍带 5002.50 的成本。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _adjust(date(2026, 6, 10), 300),
        _sell(date(2026, 7, 1), 650, 8.00),
    ))
    assert report.matches[0].cost == 5002.50
    position = by_symbol(report)["000333"]
    assert position.shares == 650.0
    assert position.cost == 5002.50


def test_adjust_scales_every_existing_lot_proportionally():
    """两个批次时按比例摊到**每个**批次：总股数 1000+500=1500，送 300 → 因子 1.2
    批次1 → 1200 股（成本仍 10005.00），批次2 → 600 股（成本仍 6005.00）。
    只加到最后一个批次的话，此后 FIFO 的每一次配对都错。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _buy(date(2026, 1, 20), 500, 12.00),
        _adjust(date(2026, 6, 10), 300),
        _sell(date(2026, 7, 1), 1200, 20.00),      # 恰好吃光批次1
    ))
    assert report.matches[0].shares == 1200.0
    assert report.matches[0].cost == 10005.00
    assert len(report.matches) == 1                 # 没有溢出到批次2
    position = by_symbol(report)["000333"]
    assert position.shares == 600.0
    assert position.cost == 6005.00


def test_negative_adjust_shrinks_shares_and_raises_unit_cost():
    """缩股（合股）：1000 股 → 700 股，成本仍 10005.00，单位成本升到 14.2929。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _adjust(date(2026, 6, 10), -300),
    ))
    position = by_symbol(report)["000333"]
    assert position.shares == 700.0
    assert position.cost == 10005.00
    assert position.unit_cost == pytest.approx(round(10005 / 700, 4))


def test_adjust_beyond_position_is_flagged_not_applied():
    """缩股超过持仓：负持仓在现实里不存在。标记而非产出负数（更不能崩）。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _adjust(date(2026, 6, 10), -1200),
    ))
    position = by_symbol(report)["000333"]
    assert position.shares == 1000.0                     # 未应用，保持原样
    assert pnl.NEGATIVE_SHARES in codes(position.issues)
    assert pnl.NEGATIVE_SHARES in codes(report.inconsistencies)


def test_adjust_without_position_is_flagged():
    """没有任何批次时送股：按比例摊无从谈起（0 的比例算不出来）。
    标记出来，让用户知道是漏记了买入。"""
    report = pnl.compute_pnl(_rows(_adjust(date(2026, 6, 10), 300)))
    assert pnl.ADJUST_WITHOUT_POSITION in codes(report.inconsistencies)
    assert by_symbol(report)["000333"].shares == 0.0


# ================================================================ dividend：累加不动批次

def test_dividend_accumulates_and_leaves_lots_alone():
    """分红计入已实现收益，但**不动批次**：股数与成本都不变。
    手算：两笔 500.00 + 320.50 = 820.50"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _dividend(date(2026, 6, 15), 500.00),
        _dividend(date(2026, 12, 15), 320.50),
    ))
    assert report.dividends == 820.50
    assert report.summary["dividends"] == 820.50
    position = by_symbol(report)["000333"]
    assert position.shares == 1000.0
    assert position.cost == 10005.00            # 分红没有摊薄成本


def test_dividends_enter_total_realized_but_not_trade_stats():
    """总已实现收益要含分红（静默忽略是系统性低估），
    但胜率/盈亏比是**交易**指标，分红不是一笔交易，不能算进 n_trades。
    手算：1984.00（交易）+ 500.00（分红）= 2484.00"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _dividend(date(2026, 1, 20), 500.00),
        _sell(date(2026, 2, 5), 1000, 12.00),
    ))
    assert report.summary["realized_pnl"] == 1984.00
    assert report.summary["dividends"] == 500.00
    assert report.summary["total_realized"] == 2484.00
    assert report.summary["n_trades"] == 1


def test_dividend_only_log_has_no_trades():
    """只有分红、没有任何交易：不能崩，也不能编出 0% 的胜率。"""
    report = pnl.compute_pnl(_rows(_dividend(date(2026, 6, 15), 500.00)))
    assert report.dividends == 500.00
    assert report.summary["total_realized"] == 500.00
    assert report.summary["n_trades"] == 0
    assert report.summary["win_rate"] is None
    assert report.summary["profit_factor"] is None
    assert report.summary["avg_holding_days"] is None
    assert report.closings == ()
    assert report.positions == ()


def test_dividend_net_of_user_entered_withholding():
    """用户把红利税记进 tax 时要认：到账 = amount − fee − tax。
    手算：500.00 − 0.00 − 100.00 = 400.00"""
    report = pnl.compute_pnl(_rows(_dividend(date(2026, 6, 15), 500.00, tax=100.00)))
    assert report.dividends == 400.00


# ================================================================ 卖超：标记而非负持仓（§3 / §4.2）

def test_oversell_flags_and_never_goes_negative():
    """漏记过买入时会出现卖超。**必须产出一致性标记，而不是负持仓或崩溃**。

    手算：买 100 @ 10.00 → 名义 1000，佣金 max(0.25, 5) = 5.00 → 成本 1005.00
          卖 300 @ 12.00 → 名义 3600，佣金 max(0.9, 5) = 5.00，
                           印花税 3600×0.0005 = 1.80 → 净得 3593.20
          只有 100 股配得上，净得按股数分摊：3593.20 × 100/300 = 1197.7333… → 1197.73
          盈亏 = 1197.73 − 1005.00 = 192.73
    未配对的 200 股成本未知：把整笔净得都算成利润（3593.20 − 1005.00 = 2588.20）
    才是那种"看着正常的错数字"。
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 100, 10.00),
        _sell(date(2026, 2, 5), 300, 12.00),
    ))
    closing = report.closings[0]
    assert closing.matched_shares == 100.0
    assert closing.unmatched_shares == 200.0
    assert closing.pnl == 192.73
    assert pnl.OVERSELL in codes(closing.issues)

    position = by_symbol(report)["000333"]
    assert position.shares == 0.0                    # 不是 −200
    assert position.cost == 0.0
    assert pnl.OVERSELL in codes(position.issues)    # 在持仓页持续可见
    assert pnl.OVERSELL in codes(report.inconsistencies)
    assert report.summary["realized_pnl"] == 192.73


def test_sell_with_no_lots_yields_none_pnl_and_is_excluded_from_stats():
    """一股都没配上时盈亏**算不出来**（成本未知）。给 None 并大声标记，
    不能给 0 也不能把整笔净得当利润 —— 前者假装是笔平手交易会污染胜率，
    后者是凭空捏造收益。口径同 metrics.py：pnl is None 的不进统计。"""
    report = pnl.compute_pnl(_rows(_sell(date(2026, 2, 5), 300, 12.00)))
    closing = report.closings[0]
    assert closing.pnl is None
    assert closing.matched_shares == 0.0
    assert closing.matches == ()
    assert pnl.OVERSELL in codes(closing.issues)
    assert report.summary["n_trades"] == 0
    assert report.summary["win_rate"] is None
    assert report.summary["realized_pnl"] == 0.0


def test_oversell_does_not_poison_later_trades():
    """卖超之后重新买入：批次要从干净的 0 开始，不能背着"欠 200 股"的债。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 100, 10.00),
        _sell(date(2026, 2, 5), 300, 12.00),
        _buy(date(2026, 3, 5), 1000, 10.00),
    ))
    position = by_symbol(report)["000333"]
    assert position.shares == 1000.0
    assert position.cost == 10005.00


# ================================================================ 坏行：标记而不是崩

def test_row_missing_price_is_flagged_not_crashing():
    """手改过的 CSV 里可能有缺价格的买入。这一行**无法解释成事实**，
    跳过并标记；静默当成 0 元买入会凭空造出一大笔利润。"""
    report = pnl.compute_pnl(_rows(
        dict(date=date(2026, 1, 5), symbol="000333", kind="buy", shares=1000.0),
        _buy(date(2026, 1, 6), 1000, 10.00),
    ))
    assert pnl.UNUSABLE_ROW in codes(report.inconsistencies)
    position = by_symbol(report)["000333"]
    assert position.shares == 1000.0          # 只认得懂的那一行
    assert position.cost == 10005.00
    assert pnl.UNUSABLE_ROW in codes(position.issues)


def test_dividend_without_amount_is_flagged():
    """分红行没有金额 = 没记下任何事实。静默按 0 计的话，
    这笔收益从总数里消失了，而页面上没有任何迹象。"""
    report = pnl.compute_pnl(_rows(
        dict(date=date(2026, 6, 15), symbol="000333", kind="dividend"),
    ))
    assert pnl.UNUSABLE_ROW in codes(report.inconsistencies)
    assert report.dividends == 0.0


def test_zero_price_trade_is_flagged_like_schema_blocks_it():
    """0 元成交在录入时就是阻断项（schema 的 BAD_NUMBER）。
    手改 CSV 绕过校验后，pnl 必须用同一把尺子——两层对"什么算事实"的
    判断一旦不一致，被一层拒收的行会在另一层变成 0 成本的白捡股。"""
    report = pnl.compute_pnl(_rows(_buy(date(2026, 1, 5), 1000, 0.0)))
    assert pnl.UNUSABLE_ROW in codes(report.inconsistencies)
    assert report.positions[0].shares == 0.0


def test_unknown_kind_is_flagged_not_crashing():
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00) | {"kind": "split"},
    ))
    assert pnl.UNUSABLE_ROW in codes(report.inconsistencies)
    assert report.positions[0].shares == 0.0


# ================================================================ 汇总指标：与 report/metrics.py 同口径（§4.2）

def _two_closings(pnl_a: float, pnl_b: float) -> pd.DataFrame:
    """构造两笔独立的完整往返，卖价挑成盈亏恰好等于给定值。"""
    rows = []
    for i, target in enumerate((pnl_a, pnl_b)):
        symbol = f"00033{i}"
        # 买 1000 @ 10.00 → 成本 10005.00；卖 1000 @ p，净得 = 1000p − 5 − 0.5p
        # 令 净得 = 10005 + target → p = (10010 + target) / 999.5
        price = round((10010 + target) / 999.5, 6)
        rows.append(_buy(date(2026, 1, 5), 1000, 10.00, symbol=symbol))
        rows.append(_sell(date(2026, 2, 5), 1000, price, symbol=symbol))
    return _rows(*rows)


def test_summary_uses_the_same_trade_stats_as_backtest_report():
    """**刻意与回测报告用同一套口径**，否则"我的实际交易 vs 策略回测"
    的并排比较是在比两把不同的尺子。这里直接拿 metrics.trade_stats 对答案。"""
    report = pnl.compute_pnl(_two_closings(100.0, -50.0))
    expected = trade_stats([m.pnl for m in report.matches],
                           [m.holding_days for m in report.matches])
    for key in ("n_trades", "win_rate", "profit_factor", "avg_holding_days"):
        assert report.summary[key] == expected[key]
    assert report.summary["win_rate"] == pytest.approx(0.5)
    assert report.summary["profit_factor"] == pytest.approx(2.0)


def test_breakeven_counts_as_loss_like_metrics():
    """平手算亏（metrics.py 的既有口径：pnl <= 0 归入 losses）。"""
    report = pnl.compute_pnl(_two_closings(100.0, 0.0))
    assert report.summary["n_trades"] == 2
    assert report.summary["win_rate"] == pytest.approx(0.5)


def test_profit_factor_none_without_losses_and_zero_when_all_lose():
    """PF 的两种"没有值"必须分开：全亏是良定义的 0.0，没有亏损单才是 None。
    面板上两者都显示 — 的话，"每单都亏"这个强信号就被吞掉了。"""
    all_win = pnl.compute_pnl(_two_closings(100.0, 50.0))
    assert all_win.summary["profit_factor"] is None
    all_lose = pnl.compute_pnl(_two_closings(-30.0, -70.0))
    assert all_lose.summary["profit_factor"] == 0.0
    assert all_lose.summary["win_rate"] == 0.0


def test_avg_holding_days_is_per_pairing():
    """一次卖出配掉两个批次 = 两笔往返（回测里一次开平就是一笔，口径同此）。
    手算：持仓 36 天与 21 天 → 平均 28.5"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _buy(date(2026, 1, 20), 500, 12.00),
        _sell(date(2026, 2, 10), 1200, 11.00),
    ))
    assert report.summary["n_trades"] == 2
    assert report.summary["avg_holding_days"] == pytest.approx(28.5)


# ================================================================ 按 source 分组（§4.2）

def test_by_source_groups_by_entry_source():
    """这是整个功能里学习价值最高的一条：照信号做的 vs 自己拍脑袋的，哪类更赚。
    按**建仓**来源分组 —— 问的是"当初为什么进场"，不是"为什么出场"。

    手算：ma_cross 那只 = 1984.00（同单买单卖用例）
          discretionary 那只：买 1000 @ 10.00 → 成本 10005.00；
            卖 1000 @ 9.00 → 名义 9000，佣金 max(2.25,5)=5.00，印花税 4.50
            → 净得 8990.50 → 盈亏 −1014.50
    """
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="000333", source="ma_cross"),
        _sell(date(2026, 2, 5), 1000, 12.00, symbol="000333", source="ma_cross"),
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="600519", source="discretionary"),
        _sell(date(2026, 2, 5), 1000, 9.00, symbol="600519", source="discretionary"),
    ))
    assert set(report.by_source) == {"ma_cross", "discretionary"}
    assert report.by_source["ma_cross"]["realized_pnl"] == 1984.00
    assert report.by_source["ma_cross"]["win_rate"] == 1.0
    assert report.by_source["discretionary"]["realized_pnl"] == -1014.50
    assert report.by_source["discretionary"]["win_rate"] == 0.0
    assert report.by_source["discretionary"]["profit_factor"] == 0.0


def test_by_source_follows_the_buy_even_when_the_sell_says_otherwise():
    """按信号建的仓、自己拍脑袋平掉的，仍算在信号那一组：
    否则"信号帮没帮上我"这个问题会被出场理由污染。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00, source="donchian"),
        _sell(date(2026, 2, 5), 1000, 12.00, source="discretionary"),
    ))
    assert set(report.by_source) == {"donchian"}
    assert report.by_source["donchian"]["realized_pnl"] == 1984.00


def test_by_source_includes_dividends_of_that_source():
    """分红也带 source：归到它自己那一组，且不计入 n_trades。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00, source="ma_cross"),
        _dividend(date(2026, 6, 15), 500.00, source="ma_cross"),
    ))
    group = report.by_source["ma_cross"]
    assert group["dividends"] == 500.00
    assert group["total_realized"] == 500.00
    assert group["n_trades"] == 0


# ================================================================ 持仓输出与边界

def test_position_carries_name_and_symbols_are_sorted():
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="600519", name="贵州茅台"),
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="000333", name="美的集团"),
    ))
    assert [p.symbol for p in report.positions] == ["000333", "600519"]
    assert [p.name for p in report.positions] == ["美的集团", "贵州茅台"]


def test_flat_position_without_issues_is_dropped():
    """平掉就不该再占一行 0 股。但**带一致性标记的要留着**
    （见 test_oversell_flags_and_never_goes_negative）—— 错误必须持续可见。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _sell(date(2026, 2, 5), 1000, 12.00),
    ))
    assert report.positions == ()


def test_unit_cost_is_none_when_no_shares():
    """0 股的单位成本不是 0，是"没有"。给 0 会在持仓页显示成"成本 0 元的白捡股"。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 100, 10.00),
        _sell(date(2026, 2, 5), 300, 12.00),      # 卖超 → 行保留
    ))
    assert by_symbol(report)["000333"].unit_cost is None


def test_empty_log():
    """空日志是每个新用户的第一次打开，属正常状态：不能崩，也不能编数字。"""
    report = pnl.compute_pnl(store.empty_trades())
    assert report.positions == ()
    assert report.closings == ()
    assert report.matches == ()
    assert report.dividends == 0.0
    assert report.by_source == {}
    assert report.inconsistencies == ()
    assert report.summary["realized_pnl"] == 0.0
    assert report.summary["total_realized"] == 0.0
    assert report.summary["n_trades"] == 0
    assert report.summary["win_rate"] is None
    assert report.summary["profit_factor"] is None
    assert report.summary["avg_holding_days"] is None


def test_amounts_are_rounded_to_cents():
    """金额一律 round(x, 2)：浮点尾巴（1197.7333…）不该漏到页面与导出上。"""
    report = pnl.compute_pnl(_rows(
        _buy(date(2026, 1, 5), 100, 10.00),
        _sell(date(2026, 2, 5), 300, 12.00),
    ))
    values = [report.summary["realized_pnl"], report.summary["total_realized"]]
    for closing in report.closings:
        values += [closing.net_proceeds, closing.cost, closing.pnl]
    for match in report.matches:
        values += [match.cost, match.proceeds, match.pnl]
    for position in report.positions:
        values += [position.shares, position.cost]
    assert all(v == round(v, 2) for v in values if v is not None)


def test_input_dataframe_is_not_mutated():
    """纯函数：调用方的表不能被改（面板会把同一张表交给多个视图）。"""
    df = _rows(
        _buy(date(2026, 1, 5), 1000, 10.00),
        _sell(date(2026, 2, 5), 1000, 12.00),
    )
    before = df.copy(deep=True)
    pnl.compute_pnl(df)
    pd.testing.assert_frame_equal(df, before)


def test_works_on_a_dataframe_round_tripped_through_the_store(tmp_path):
    """真实路径：落盘再读回来（date 变 datetime64、空值变 NaN/""、
    symbol 前导零靠 dtype=str 保住）。只测手工构造的 DataFrame 的话，
    这些 dtype 差异会在用户第一次打开时才暴露。"""
    path = tmp_path / "trades.csv"
    df = _rows(
        _buy(date(2026, 1, 5), 1000, 10.00, symbol="000333", name="美的集团"),
        _dividend(date(2026, 6, 15), 500.00, symbol="000333"),
        _sell(date(2026, 7, 1), 1000, 12.00, symbol="000333"),
    )
    store.save_trades(df, path)
    report = pnl.compute_pnl(store.load_trades(path))

    assert report.matches[0].symbol == "000333"        # 前导零没被吃掉
    assert report.matches[0].name == "美的集团"
    assert report.summary["realized_pnl"] == 1984.00
    assert report.summary["dividends"] == 500.00
