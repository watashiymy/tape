# tests/test_journal_analytics.py — 仪表盘的两组纯函数（v0.3.1 设计 §2 + §3）
#
# 与 test_journal_pnl.py 同一条纪律：**每个期望值都是手算的**，算式写在注释里，
# 能用计算器独立复核。把期望值写成"实现里同一个表达式"等于把实现抄两遍，
# 两边一起错的时候测试全绿——而一个错的累计盈亏点不会报错，用户会拿它做决策。
#
# 两个函数的契约：
# - cumulative_realized(trades)：平仓 pnl + 分红按**日**合并累计，输出即阶梯数据点。
#   算不出来的（配不上批次的卖出、缺金额的分红）**不出点**，与 pnl 的口径一致；
#   最后一个点必须等于 summary["total_realized"]——曲线终点和指标卡对不上
#   是最伤信任的展示错误。
# - position_weights(positions, prices)：有市价用市价，无市价按成本顶上并打
#   by_cost 标（诚实优于漏项：漏了会让其余标的的占比虚高），权重和恒为 1。
from datetime import date

import pandas as pd
import pytest

from quant.config import Costs, StampTaxRule
from quant.journal import analytics, pnl, schema, store
from quant.journal.pnl import Position

# 与 test_journal_pnl.py 同一套成本参数：手算对照的费用要和真实录入一模一样。
CFG = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


def _rows(*rows) -> pd.DataFrame:
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


def _dividend(d: date, amount, **over) -> dict:
    row = dict(date=d, symbol="000333", kind="dividend", amount=amount,
               source="discretionary")
    row.update(over)
    return row


# ================================================================ 累计已实现（§2.2）

def test_cumulative_realized_hand_computed_with_interleaved_dividends():
    """多笔平仓与分红交错 + 同日多笔合并。手算：

    2026-01-05 买 1000 @ 10.00 → 名义 10,000，佣金 max(2.5, 5)=5.00，成本基 10,005.00
    2026-02-10 买  500 @ 12.00 → 名义  6,000，佣金 5.00，成本基 6,005.00
    2026-03-02 卖  600 @ 13.00 → 名义 7,800，佣金 5.00，印花税 3.90，净得 7,791.10
               FIFO 吃掉批次1 的 600 股：成本 10,005×600/1000 = 6,003.00
               → 平仓盈亏 7,791.10 − 6,003.00 = **1,788.10**
    2026-03-02 分红 200.00（与上一笔同日 → 合并成一个点）
               → 当日合计 1,788.10 + 200.00 = **1,988.10**
    2026-04-15 卖 900 @ 9.00 → 名义 8,100，佣金 5.00，印花税 4.05，净得 8,090.95
               吃掉批次1 剩余 400 股（成本 4,002.00）+ 批次2 全部 500 股（成本 6,005.00）
               → 平仓盈亏 8,090.95 − 10,007.00 = **−1,916.05**
               累计 1,988.10 − 1,916.05 = **72.05**
    2026-05-20 分红 80.00，红利税 8.00 记在佣金栏 → 净到账 72.00
               累计 72.05 + 72.00 = **144.05**
    """
    trades = _rows(
        _buy(date(2026, 1, 5), 1000, 10.0),
        _buy(date(2026, 2, 10), 500, 12.0),
        _sell(date(2026, 3, 2), 600, 13.0),
        _dividend(date(2026, 3, 2), 200.0),
        _sell(date(2026, 4, 15), 900, 9.0),
        _dividend(date(2026, 5, 20), 80.0, fee=8.0),
    )
    points = analytics.cumulative_realized(trades)
    assert points == [
        (date(2026, 3, 2), 1988.10),
        (date(2026, 4, 15), 72.05),
        (date(2026, 5, 20), 144.05),
    ]


def test_cumulative_last_point_equals_the_summary_total():
    """曲线终点必须等于指标卡的"总已实现"（同一本日志两个口径对不上，
    用户只会认定整页都不可信）。"""
    trades = _rows(
        _buy(date(2026, 1, 5), 1000, 10.0),
        _sell(date(2026, 3, 2), 600, 13.0),
        _dividend(date(2026, 5, 20), 80.0, fee=8.0),
    )
    points = analytics.cumulative_realized(trades)
    assert points[-1][1] == pytest.approx(
        pnl.compute_pnl(trades).summary["total_realized"])


def test_empty_journal_yields_no_points():
    assert analytics.cumulative_realized(store.empty_trades()) == []


def test_dividend_only_journal_still_builds_the_curve():
    """只有分红也是已实现收益（设计 §2.2：分红要计入，不许静默忽略）。
    手算：100.00 → 100.00；再来 50.00 → 150.00。"""
    trades = _rows(_dividend(date(2026, 1, 10), 100.0),
                   _dividend(date(2026, 2, 10), 50.0))
    assert analytics.cumulative_realized(trades) == [
        (date(2026, 1, 10), 100.0),
        (date(2026, 2, 10), 150.0),
    ]


def test_an_unmatchable_sell_puts_no_point_on_the_curve():
    """没有任何买入批次的卖出：盈亏**算不出来**（pnl 给 None）。
    不出点，而不是画一个 0 上去——0 是编出来的平手。"""
    trades = _rows(_sell(date(2026, 3, 2), 600, 13.0))
    assert analytics.cumulative_realized(trades) == []


def test_a_dividend_without_amount_is_skipped_like_pnl_does():
    """缺金额的分红在 pnl 里是跳过 + 标记；曲线必须同口径跳过，
    否则终点和指标卡对不上。amount=None 走 _rows 会被 apply_defaults 留空。"""
    trades = _rows(_dividend(date(2026, 1, 10), None),
                   _dividend(date(2026, 2, 10), 50.0))
    assert analytics.cumulative_realized(trades) == [(date(2026, 2, 10), 50.0)]


def test_partial_oversell_contributes_only_the_matched_part():
    """卖超（漏记买入）时 pnl 只算配得上的部分。手算：

    买 100 @ 10.00 → 成本基 100×10 + 5 = 1,005.00
    卖 500 @ 12.00 → 名义 6,000，佣金 5.00，印花税 3.00，净得 5,992.00
    配对 100 股的净得份额 = 5,992 × 100/500 = 1,198.40
    平仓盈亏（只算配上的 100 股）= 1,198.40 − 1,005.00 = **193.40**
    """
    trades = _rows(_buy(date(2026, 1, 5), 100, 10.0),
                   _sell(date(2026, 3, 2), 500, 12.0))
    assert analytics.cumulative_realized(trades) == [(date(2026, 3, 2), 193.40)]


# ================================================================ 持仓占比（§2.3）

def _pos(symbol: str, shares: float, cost: float, name: str = "") -> Position:
    return Position(symbol=symbol, name=name, shares=shares, cost=cost,
                    unit_cost=round(cost / shares, 4) if shares else None)


def test_position_weights_hand_computed_with_cost_fallback():
    """手算：A 有市价 12.00 × 1000 股 = 12,000.00；B 无市价按成本 6,005.00 顶上。
    合计 18,005.00 → A 占 12,000/18,005 = 0.66648…，B 占 0.33351…。
    B 必须打 by_cost 标——诚实优于漏项，漏了 B 会让 A 的占比虚高成 100%。"""
    rows = analytics.position_weights(
        [_pos("000333", 1000.0, 10005.0, "美的集团"),
         _pos("600519", 500.0, 6005.0, "贵州茅台")],
        {"000333": 12.0, "600519": float("nan")})
    assert [r["symbol"] for r in rows] == ["000333", "600519"]  # 按市值降序
    assert rows[0] == {"symbol": "000333", "name": "美的集团", "value": 12000.0,
                       "weight": pytest.approx(12000.0 / 18005.0), "by_cost": False}
    assert rows[1]["value"] == pytest.approx(6005.0)
    assert rows[1]["by_cost"] is True
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0)


def test_weights_sum_to_one_even_when_no_symbol_has_a_price():
    """全部无市价：全按成本、全打标，权重和仍是 1（占比图照样画得出来）。"""
    rows = analytics.position_weights(
        [_pos("000333", 1000.0, 10005.0), _pos("600519", 500.0, 6005.0)], {})
    assert all(r["by_cost"] for r in rows)
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0)
    # 降序：10,005 > 6,005
    assert [r["symbol"] for r in rows] == ["000333", "600519"]


def test_no_positions_no_rows():
    assert analytics.position_weights([], {"000333": 12.0}) == []


def test_a_zero_share_position_is_excluded():
    """pnl 会为"只有一致性问题、没有批次"的票造一个 0 股 Position（为了把告警
    挂到持仓表上）。占比图里它不该出现——0 股没有市值可占。"""
    rows = analytics.position_weights(
        [_pos("000333", 1000.0, 10005.0),
         Position(symbol="600519", name="", shares=0.0, cost=0.0, unit_cost=None)],
        {"000333": 12.0})
    assert [r["symbol"] for r in rows] == ["000333"]
    assert rows[0]["weight"] == pytest.approx(1.0)


def test_a_nonpositive_price_counts_as_missing_not_as_zero_value():
    """价格 0 是假数据（journal_ui.latest_price 明写"不许返回 0"）。
    真混进来也不能把市值算成 0——按成本顶上并打标，同"无市价"一条路。"""
    rows = analytics.position_weights([_pos("000333", 1000.0, 10005.0)],
                                      {"000333": 0.0})
    assert rows[0]["by_cost"] is True
    assert rows[0]["value"] == pytest.approx(10005.0)
