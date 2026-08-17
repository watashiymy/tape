"""回测引擎（spec §8）：T-1 收盘目标仓位 → T 日开盘撮合；原始价成交，A 股约束内置。"""
from __future__ import annotations

import pandas as pd

from quant.backtest.costs import commission, stamp_tax
from quant.backtest.portfolio import BacktestResult, Slot, Trade
from quant.config import Settings

LIMIT_UP_RATIO = 1.095    # 主板涨停近似阈值（spec 决策3）
LIMIT_DOWN_RATIO = 0.905


class Backtester:
    def __init__(self, bars: dict[str, pd.DataFrame],
                 positions: dict[str, pd.Series], settings: Settings):
        for sym, pos in positions.items():
            assert pos.index.equals(bars[sym].index), f"{sym}: 信号与行情索引不一致"
        self.bars = bars
        self.settings = settings
        # T-1 收盘的目标仓位在 T 日执行（spec 决策2）
        self.desired = {s: p.shift(1).fillna(0).astype(int) for s, p in positions.items()}

    def run(self) -> BacktestResult:
        symbols = list(self.bars)
        budget = self.settings.capital / len(symbols)
        slots = {s: Slot(symbol=s, cash=budget, budget=budget) for s in symbols}
        calendar = sorted(set().union(*[set(df.index) for df in self.bars.values()]))
        equity: dict[pd.Timestamp, float] = {}
        trades: list[Trade] = []
        skipped: list[tuple] = []

        for t in calendar:
            for sym in symbols:
                slot, df = slots[sym], self.bars[sym]
                if t not in df.index:
                    continue  # 停牌/未上市：按 last_close 估值（spec 决策6）
                row = df.loc[t]
                self._apply_factor(slot, row)
                want = int(self.desired[sym].loc[t])
                if want == 1 and slot.shares == 0:
                    self._try_buy(slot, row, t, trades, skipped)
                elif want == 0 and slot.shares > 0:
                    self._try_sell(slot, row, t, trades, skipped)
                slot.last_close = float(row["close"])
                slot.last_factor = float(row["adj_factor"])
            equity[t] = sum(sl.cash + sl.shares * (sl.last_close or 0.0)
                            for sl in slots.values())
        return BacktestResult(pd.Series(equity).sort_index(), trades, skipped)

    def _apply_factor(self, slot: Slot, row: pd.Series) -> None:
        """除权除息日按后复权因子比率调整持仓股数（等效分红再投资，spec §8 步骤4）。"""
        f = float(row["adj_factor"])
        if slot.shares > 0 and slot.last_factor is not None and f != slot.last_factor:
            slot.shares *= f / slot.last_factor

    def _limit_up(self, row: pd.Series, prev_close: float | None) -> bool:
        if prev_close is None:
            return False
        one_word = row["high"] == row["low"] and row["close"] > prev_close
        return float(row["open"]) >= prev_close * LIMIT_UP_RATIO or bool(one_word)

    def _limit_down(self, row: pd.Series, prev_close: float | None) -> bool:
        if prev_close is None:
            return False
        one_word = row["high"] == row["low"] and row["close"] < prev_close
        return float(row["open"]) <= prev_close * LIMIT_DOWN_RATIO or bool(one_word)

    def _try_buy(self, slot: Slot, row: pd.Series, t: pd.Timestamp,
                 trades: list[Trade], skipped: list[tuple]) -> None:
        if self._limit_up(row, slot.last_close):
            skipped.append((t, slot.symbol, "涨停无法买入，顺延"))
            return
        cfg = self.settings.costs
        px = float(row["open"]) * (1 + cfg.slippage)
        # 建仓规模以「固定额度」与「可用现金」二者取小为准：赚到的钱留作闲置现金，
        # 不放大下一轮仓位（spec §8：每只资金上限=初始本金/N，不随权益浮动）
        spendable = min(slot.cash, slot.budget)
        qty = int(spendable // (px * 100)) * 100
        while qty >= 100 and qty * px + commission(qty * px, cfg) > spendable:
            qty -= 100
        if qty < 100:
            skipped.append((t, slot.symbol, "资金不足一手，放弃"))
            return
        fee = commission(qty * px, cfg)
        slot.cash -= qty * px + fee
        slot.shares = float(qty)
        slot.entry_date = t
        slot.cost_basis = qty * px + fee
        trades.append(Trade(slot.symbol, "buy", t, px, float(qty), fee))

    def _try_sell(self, slot: Slot, row: pd.Series, t: pd.Timestamp,
                  trades: list[Trade], skipped: list[tuple]) -> None:
        if slot.entry_date is not None and slot.entry_date >= t:
            skipped.append((t, slot.symbol, "T+1 当日不可卖"))
            return
        if self._limit_down(row, slot.last_close):
            skipped.append((t, slot.symbol, "跌停无法卖出，顺延"))
            return
        cfg = self.settings.costs
        px = float(row["open"]) * (1 - cfg.slippage)
        notional = slot.shares * px
        fee = commission(notional, cfg)
        tax = stamp_tax(notional, t.date(), cfg)
        proceeds = notional - fee - tax
        pnl = proceeds - slot.cost_basis
        holding = (t - slot.entry_date).days if slot.entry_date is not None else None
        slot.cash += proceeds
        trades.append(Trade(slot.symbol, "sell", t, px, slot.shares, fee, tax, pnl, holding))
        slot.shares = 0.0
        slot.entry_date = None
        slot.cost_basis = 0.0
