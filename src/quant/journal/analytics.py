"""仪表盘的两组纯函数（v0.3.1 设计 §2 + §3）：累计已实现序列与持仓占比。

纯函数：不碰文件系统、不碰行情缓存、不 import streamlit。最新价从哪来
（journal_ui.latest_price 读本地缓存）是页面的事，这里只接一个 {symbol: price}。

两条与 `pnl` 一脉相承的口径纪律：

1. **算不出来就不出点/不编数**。配不上批次的卖出（pnl=None）、缺金额的分红，
   在 `pnl` 里是"跳过 + 标记"，这里必须同样跳过——否则曲线终点和指标卡的
   "总已实现"对不上，用户只会认定整页都不可信（一致性由测试钉住）。
2. **无市价按成本顶上并打 by_cost 标，而不是漏项**。占比图漏掉一只没市价的
   重仓，会让其余标的的占比全部虚高——诚实优于漏项（设计 §2.3）。
   注意这与指标卡刻意相反（那边缺价显示 — 并注明几只没算）：占比回答的是
   "仓位集中在哪"，缺一块就答错；市值回答的是"值多少钱"，编一块就答错。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date

import pandas as pd

from quant.journal import pnl, schema


def cumulative_realized(trades: pd.DataFrame) -> list[tuple[date, float]]:
    """按日累计的已实现盈亏（平仓 pnl + 分红净额），即阶梯曲线的数据点。

    - **同日多笔合并成一个点**：一天里卖两笔 + 收一笔分红，曲线上是一次跳变。
      也因此不依赖同日内的先后顺序（`pnl._ordered_rows` 的稳定排序只影响
      FIFO 配对，不影响按日汇总）。
    - 平仓盈亏取 `pnl.compute_pnl` 的 Closing（含费用、含 FIFO 分摊），
      **不在这里另算一遍**——两套算法迟早分叉，而分叉的那天没有任何报错。
    - 分红直接扫 trades 行：PnlReport 只有分红合计没有逐日金额。规则必须与
      `pnl._apply_dividend` 逐字对齐（净额 = amount − fee − tax；缺金额跳过），
      对齐与否由"曲线终点 == summary['total_realized']"的测试钉住。
    """
    by_day: dict[date, float] = {}

    for closing in pnl.compute_pnl(trades).closings:
        if closing.pnl is None:
            continue      # 一股都没配上：盈亏未知，画 0 上去就是编一笔平手
        by_day[closing.date] = by_day.get(closing.date, 0.0) + closing.pnl

    for day, amount in _dividends(trades):
        by_day[day] = by_day.get(day, 0.0) + amount

    points: list[tuple[date, float]] = []
    total = 0.0
    for day in sorted(by_day):
        total = round(total + by_day[day], 2)     # 逐点归整，别让浮点渣一路滚
        points.append((day, total))
    return points


def _dividends(trades: pd.DataFrame) -> Iterable[tuple[date, float]]:
    """逐笔分红的 (日期, 净到账)。跳过的行与 `pnl` 完全同一批：
    没代码/没日期/没金额的分红在那边是 UNUSABLE_ROW（页面已单独告警），
    这里再收进曲线就成了"指标卡说没有、曲线说有"。"""
    if trades.empty:
        return
    for row in trades.to_dict("records"):
        if str(row.get("kind") or "").strip() != "dividend":
            continue
        if not str(row.get("symbol") or "").strip():
            continue
        day = schema.parse_date(row.get(schema.DATE_COLUMN))
        amount = schema.to_number(row.get("amount"))
        if day is None or amount is None:
            continue
        fee = schema.to_number(row.get("fee")) or 0.0
        tax = schema.to_number(row.get("tax")) or 0.0
        yield day, round(amount - fee - tax, 2)


def position_weights(positions: Iterable[pnl.Position],
                     prices: Mapping[str, float]) -> list[dict]:
    """持仓占比行（按市值降序）：{symbol, name, value, weight, by_cost}。

    - 有市价用市价（价 × 股数），没有就按持仓成本顶上并打 `by_cost=True`；
      价格 0 / NaN / 负数一律当"没有"——0 是假数据（latest_price 明写不返回 0），
      按它算出 0 市值等于把一只在手的票从占比里抹掉。
    - 0 股的 Position 不进占比：那是 pnl 为了挂一致性告警造的空壳。
    - 权重和恒为 1（value / 合计），排序在这里做掉，图表拿到就是画的顺序。
    """
    rows: list[dict] = []
    for position in positions:
        if position.shares <= 0:
            continue
        price = prices.get(position.symbol)
        priced = price is not None and math.isfinite(price) and price > 0
        rows.append({
            "symbol": position.symbol,
            "name": position.name,
            "value": round(price * position.shares, 2) if priced
                     else round(position.cost, 2),
            "by_cost": not priced,
        })

    total = sum(r["value"] for r in rows)
    if total <= 0:
        return []      # 没有可占比的市值（没持仓，或全是 0 成本的怪数据）
    for row in rows:
        row["weight"] = row["value"] / total
    rows.sort(key=lambda r: (-r["value"], r["symbol"]))
    return [{k: r[k] for k in ("symbol", "name", "value", "weight", "by_cost")}
            for r in rows]
