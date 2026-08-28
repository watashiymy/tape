"""FIFO 批次配对、当前持仓与已实现盈亏（设计 §4）。纯函数，不碰文件系统与行情。

**这是整个交易日志功能的正确性核心：算错比不算更糟。**
一个错的盈亏不会报错、不会崩、看着完全正常，用户会拿它做决策；"不算"至少还诚实。
所以本模块通篇的原则是：**算不出来就说算不出来**（`None` + 一致性标记），
绝不用 0 或"整笔净得当利润"这类看着正常的数字顶包。

三条口径上的关键决定，每条都能独立地把结果算错：

1. **成本基含买入费用**，卖出净得**扣**佣金与印花税（设计 §4.1 的算式）。
   少算一边，每一笔盈亏都会朝同一个方向偏，长期下来偏得很可观。
2. **配对（`Match`）才是统计的单位**，不是"一次卖出"。一次卖出吃掉两个批次
   就是两笔完整往返——回测里一次开仓到平仓正好也是一笔，
   这样 `report/metrics.py` 的胜率/盈亏比才真的可以和实盘并排比（设计 §4.2）。
3. **`adjust` 只动股数、不动成本总额**。送股摊薄单位成本是正确口径；
   把送股当成 0 元买入并进成本，此后每一笔卖出的盈亏全错且不会报错。

金额一律 `round(x, 2)`：批次成本在内部保持精确浮点，只在输出时四舍五入，
且逐笔分摊的末笔取"总数减去前面各笔"，保证明细加得回总数（见 `_split`）。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quant.journal import schema
from quant.report.metrics import trade_stats

# ---------------------------------------------------------------- 一致性问题码
#: 卖出股数超过持仓。与 schema 共用同一个码：录入时的那条警告和持仓页上
#: 这条标记说的是同一件事，两个码会让页面各挑一个显示。
OVERSELL = schema.OVERSELL
#: 没有任何批次时收到送股/缩股：按比例摊无从谈起（0 没有比例）。
ADJUST_WITHOUT_POSITION = "adjust_without_position"
#: 缩股超过持仓：负持仓在现实里不存在。
NEGATIVE_SHARES = "negative_shares"
#: 这一行没记下任何可用的事实（缺股数/价格、类型无法解释、日期认不出来）。
UNUSABLE_ROW = "unusable_row"


@dataclass(frozen=True)
class Inconsistency:
    """一条数据一致性标记。带 `trade_id` 是为了让页面能直接跳到出问题的那一行——
    只报"某只票卖超了"的话，用户得自己在几百行里找。"""
    code: str
    symbol: str
    trade_id: str
    message: str


@dataclass(frozen=True)
class Match:
    """一次卖出与**一个**买入批次配对成的完整往返交易。

    它是汇总指标的单位（见模块 docstring 第 2 条），也是"这笔平仓消耗了哪些
    买入批次"的可追溯凭证（设计 §4.2）：`buy_trade_id` 指回日志里的那一行。
    """
    symbol: str
    name: str
    buy_trade_id: str
    buy_date: date
    sell_trade_id: str
    sell_date: date
    shares: float
    cost: float             # 该批次分摊到的成本（含当初的买入费用）
    proceeds: float         # 卖出净得按股数分摊到该批次的部分
    pnl: float              # proceeds − cost
    holding_days: int       # 自然日，同回测口径
    buy_source: str         # 建仓来源：分组对比用它（"照信号做的" vs "自己拍的"）
    sell_source: str


@dataclass(frozen=True)
class Closing:
    """一次卖出的平仓明细。`matches` 是它消耗掉的买入批次。"""
    trade_id: str
    date: date
    symbol: str
    name: str
    shares: float           # 卖出股数（事实，未必全部配得上批次）
    price: float
    fee: float
    tax: float
    net_proceeds: float     # 净得 = 股数×价格 − 佣金 − 印花税（事实）
    cost: float             # 已配对批次的成本合计
    pnl: float | None       # 一股都没配上时是 None——成本未知，算不出来
    matched_shares: float
    unmatched_shares: float
    source: str             # 卖出行自己的来源（分组按建仓来源，见 by_source）
    matches: tuple[Match, ...] = ()
    issues: tuple[Inconsistency, ...] = ()


@dataclass(frozen=True)
class Position:
    """当前持仓。最新价与浮动盈亏要读行情缓存，不在纯函数里做（由页面来接）。"""
    symbol: str
    name: str
    shares: float
    cost: float
    unit_cost: float | None          # 0 股时是"没有"，不是 0
    issues: tuple[Inconsistency, ...] = ()


@dataclass(frozen=True)
class PnlReport:
    positions: tuple[Position, ...]
    closings: tuple[Closing, ...]
    matches: tuple[Match, ...]
    dividends: float
    summary: dict
    by_source: dict[str, dict]
    inconsistencies: tuple[Inconsistency, ...]


# ---------------------------------------------------------------- 内部状态
@dataclass
class _Lot:
    """一个还没被吃完的买入批次。`cost` 保持精确浮点，只在输出时 round。"""
    trade_id: str
    day: date
    shares: float
    cost: float
    source: str


@dataclass
class _Book:
    """一只票的账。"""
    symbol: str
    name: str = ""
    lots: list[_Lot] = field(default_factory=list)
    issues: list[Inconsistency] = field(default_factory=list)

    @property
    def shares(self) -> float:
        return sum(lot.shares for lot in self.lots)

    @property
    def cost(self) -> float:
        return sum(lot.cost for lot in self.lots)


def compute_pnl(trades: pd.DataFrame) -> PnlReport:
    """从整本日志算出持仓、逐笔平仓明细与汇总。输入不会被修改。

    坏行不抛异常：日志是手工维护的 CSV，用户会用 Excel 改它。
    一行看不懂就整本算不出来的话，用户唯一的出路是删数据。
    改成"跳过 + 标记"，其余记录照样给出可用的结果。
    """
    books: dict[str, _Book] = {}
    closings: list[Closing] = []
    dividends: list[tuple[str, float]] = []       # (source, 净到账金额)
    orphans: list[Inconsistency] = []             # 连标的都认不出来的行

    for row in _ordered_rows(trades):
        kind = str(row.get("kind") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        trade_id = str(row.get("trade_id") or "").strip()
        day = schema.parse_date(row.get(schema.DATE_COLUMN))

        if not symbol or kind not in schema.KINDS or day is None:
            issue = Inconsistency(
                UNUSABLE_ROW, symbol, trade_id,
                f"这行记录无法解释（类型 {kind!r}、代码 {symbol!r}、日期 "
                f"{row.get(schema.DATE_COLUMN)!r}），已跳过，不计入持仓与盈亏")
            # 连代码都认不出来时挂不到任何一只票上，收进 orphans——
            # 丢掉的话这一行就彻底消失了，而"少了一笔"没有任何迹象。
            (_book(books, symbol).issues if symbol else orphans).append(issue)
            continue

        book = _book(books, symbol)
        if name := str(row.get("name") or "").strip():
            book.name = name                       # 后录的名称覆盖旧的（改过名的票）

        if kind == "dividend":
            if (entry := _apply_dividend(book, row, trade_id)) is not None:
                dividends.append(entry)
        elif kind == "buy":
            _apply_buy(book, row, trade_id, day)
        elif kind == "sell":
            closing = _apply_sell(book, row, trade_id, day)
            if closing is not None:
                closings.append(closing)
        elif kind == "adjust":
            _apply_adjust(book, row, trade_id, day)

    matches = tuple(m for c in closings for m in c.matches)
    dividend_total = round(sum(amount for _, amount in dividends), 2)
    inconsistencies = tuple(orphans) + tuple(
        issue for book in _sorted(books) for issue in book.issues)

    return PnlReport(
        positions=tuple(_position(book) for book in _sorted(books)
                        if book.lots or book.issues),
        closings=tuple(closings),
        matches=matches,
        dividends=dividend_total,
        summary=_summary(matches, dividend_total),
        by_source=_by_source(matches, dividends),
        inconsistencies=inconsistencies,
    )


# ---------------------------------------------------------------- 逐类记录的处理

def _apply_buy(book: _Book, row, trade_id: str, day: date) -> None:
    shares = schema.to_number(row.get("shares"))
    price = schema.to_number(row.get("price"))
    if not (_usable(shares) and _usable(price)):
        book.issues.append(Inconsistency(
            UNUSABLE_ROW, book.symbol, trade_id,
            f"买入没有可用的股数/价格（{shares}/{price}），已跳过："
            f"当成 0 会凭空造出一笔 0 成本的持仓"))
        return
    # 成本基 = 股数×价格 + 买入费用（设计 §4.1）。tax 一并加上：买入通常是 0，
    # 但用户可能把过户费记在那里，认它比忽略它安全。
    book.lots.append(_Lot(
        trade_id=trade_id, day=day, shares=shares,
        cost=shares * price + _fee(row) + _tax(row), source=_source(row)))


def _apply_sell(book: _Book, row, trade_id: str, day: date) -> Closing | None:
    shares = schema.to_number(row.get("shares"))
    price = schema.to_number(row.get("price"))
    if not (_usable(shares) and _usable(price)):
        book.issues.append(Inconsistency(
            UNUSABLE_ROW, book.symbol, trade_id,
            f"卖出没有可用的股数/价格（{shares}/{price}），已跳过"))
        return None

    fee, tax = _fee(row), _tax(row)
    net = shares * price - fee - tax
    taken = _take_fifo(book, shares)               # [(批次, 消耗股数, 消耗成本)]
    matched = sum(qty for _, qty, _ in taken)
    issues: list[Inconsistency] = []
    # 用 _EPS 而不是 `matched < shares`：送股摊薄后的批次股数带浮点尾巴
    # （1300.0000000000002），严格小于会把一次正常的清仓误报成卖超。
    if shares - matched > _EPS:
        issue = Inconsistency(
            OVERSELL, book.symbol, trade_id,
            f"卖出 {shares:g} 股但只有 {matched:g} 股配得上买入批次，"
            f"未配对的 {shares - matched:g} 股成本未知，不计入已实现盈亏"
            f"——是不是漏记了一笔买入？")
        issues.append(issue)
        book.issues.append(issue)                  # 持仓页要持续看得到（设计 §3）

    # 净得按股数分摊，分母是**卖出股数**而非已配对股数：卖超时未配对的那部分
    # 连带它那份净得一起不计入盈亏。拿整笔净得去减已知成本的话，
    # 卖超会凭空多出一大块"利润"。
    #
    # 成本**不能**这么摊：每个批次带着自己当初的买入价与费用，按股数平摊会把
    # 贵批次的成本挪一部分给便宜批次——两笔配对的盈亏各错一块、合计却仍然对得上，
    # 是最难发现的一种错（本模块第一版就栽在这里，由 FIFO 用例当场抓到）。
    proceeds = _round_parts([net * qty / shares for _, qty, _ in taken])
    costs = _round_parts([cost for _, _, cost in taken])

    name = book.name
    matches = tuple(
        Match(symbol=book.symbol, name=name,
              buy_trade_id=lot.trade_id, buy_date=lot.day,
              sell_trade_id=trade_id, sell_date=day,
              shares=qty, cost=cost, proceeds=got, pnl=round(got - cost, 2),
              holding_days=(day - lot.day).days,
              buy_source=lot.source, sell_source=_source(row))
        for (lot, qty, _), got, cost in zip(taken, proceeds, costs))

    return Closing(
        trade_id=trade_id, date=day, symbol=book.symbol, name=name,
        shares=shares, price=price, fee=fee, tax=tax,
        net_proceeds=round(net, 2),
        cost=round(sum(m.cost for m in matches), 2),
        # 一股都没配上 → 盈亏**算不出来**。给 0 会假装是笔平手交易并污染胜率。
        pnl=round(sum(m.pnl for m in matches), 2) if matches else None,
        matched_shares=round(matched, 2),
        unmatched_shares=round(shares - matched, 2),
        source=_source(row), matches=matches, issues=tuple(issues))


def _apply_dividend(book: _Book, row, trade_id: str) -> tuple[str, float] | None:
    """现金分红：**累加进已实现收益，不动批次**（设计 §4.1）。

    A 股蓝筹年化股息 2–4%，长期持有下这部分收益不小，静默忽略是系统性低估。
    但金额缺失时不能按 0 记：那等于让一笔真实收益无声消失。
    """
    amount = schema.to_number(row.get("amount"))
    if amount is None:
        book.issues.append(Inconsistency(
            UNUSABLE_ROW, book.symbol, trade_id,
            "分红没有到账金额，已跳过：按 0 计会让这笔收益从总数里静默消失"))
        return None
    # 减去用户记在 fee/tax 上的扣款（红利税通常记这里）。
    return _source(row), round(amount - _fee(row) - _tax(row), 2)


def _apply_adjust(book: _Book, row, trade_id: str, day: date) -> None:
    """送股/转增/拆股：按比例调整现存批次的**股数**，**成本总额不变**。

    摊薄单位成本才是正确口径——送股没花钱，成本不该变；股数变了，
    每股摊到的成本自然就低了。必须摊到**每个**批次上，
    只加到最后一个批次的话，此后 FIFO 的每一次配对都错。
    """
    delta = schema.to_number(row.get("shares"))
    if delta is None or delta == 0:
        book.issues.append(Inconsistency(
            UNUSABLE_ROW, book.symbol, trade_id,
            f"股数调整没有可用的数量（{delta}），已跳过（送股填正数、缩股填负数）"))
        return

    before = book.shares
    if before <= 0:
        book.issues.append(Inconsistency(
            ADJUST_WITHOUT_POSITION, book.symbol, trade_id,
            f"{day} 的股数调整（{delta:+g} 股）没有对应的持仓批次，无法按比例摊，"
            f"已跳过——是不是漏记了买入？"))
        return
    after = before + delta
    if after <= 0:
        book.issues.append(Inconsistency(
            NEGATIVE_SHARES, book.symbol, trade_id,
            f"{day} 的股数调整（{delta:+g} 股）会让持仓从 {before:g} 变成 {after:g} 股，"
            f"负持仓在现实里不存在，已跳过"))
        return

    factor = after / before
    for lot in book.lots:
        lot.shares *= factor                       # cost 一个字都不动


# ---------------------------------------------------------------- FIFO 与分摊

def _take_fifo(book: _Book, shares: float) -> list[tuple[_Lot, float, float]]:
    """先进先出地消耗批次，返回 [(批次, 消耗股数, 消耗成本)]。

    成本按股数比例分摊，并从批次上**减去**（而不是按单位成本重算）：
    减法让"卖掉的成本 + 剩下的成本 == 原成本"永远成立，
    重算则会在送股摊薄出无限小数后一点点漂。
    """
    taken: list[tuple[_Lot, float, float]] = []
    remaining = shares
    for lot in book.lots:
        if remaining <= 0:
            break
        qty = min(lot.shares, remaining)
        if qty <= 0:
            continue
        cost = lot.cost * qty / lot.shares
        lot.shares -= qty
        lot.cost -= cost
        remaining -= qty
        taken.append((lot, qty, cost))
    book.lots = [lot for lot in book.lots if lot.shares > _EPS]
    return taken


def _round_parts(parts: list[float]) -> list[float]:
    """把精确的各份金额四舍五入到分，**末份取余额**，保证明细加得回总数。

    逐份独立四舍五入是不行的：9890.05 分成三等份时每份 3296.68333…，
    各自舍成 3296.68 后合计 9890.04，与总数差一分——用户对不上账，
    而对不上账的明细比没有明细更伤信任。
    """
    if not parts:
        return []
    out = [round(p, 2) for p in parts[:-1]]
    return out + [round(round(sum(parts), 2) - sum(out), 2)]


# ---------------------------------------------------------------- 汇总

def _summary(matches: Iterable[Match], dividends: float) -> dict:
    matches = list(matches)
    realized = round(sum(m.pnl for m in matches), 2)
    return {
        "realized_pnl": realized,
        "dividends": dividends,
        # 分红要计入已实现收益（设计 §2.2）：静默忽略是对长期持有的系统性低估。
        "total_realized": round(realized + dividends, 2),
        # 胜率/盈亏比/平均持仓天数**刻意与回测报告同一个函数**（设计 §4.2）。
        **trade_stats([m.pnl for m in matches], [m.holding_days for m in matches]),
    }


def _by_source(matches: Iterable[Match],
               dividends: Iterable[tuple[str, float]]) -> dict[str, dict]:
    """按来源分组：**照系统信号做的交易 vs 自己拍脑袋做的**，各自的胜率与盈亏比。

    这是整个功能里学习价值最高的一块——它直接回答"这套系统到底帮没帮上我"。
    分组键取**建仓**（买入批次）的来源，不是卖出行的：问的是"当初为什么进场"。
    按信号建仓、自己拍脑袋平掉的那些，仍该算在信号这一组，
    否则出场理由会把答案搅浑。
    """
    groups: dict[str, list[Match]] = {}
    for match in matches:
        groups.setdefault(match.buy_source, []).append(match)
    cash: dict[str, float] = {}
    for source, amount in dividends:
        cash[source] = round(cash.get(source, 0.0) + amount, 2)

    return {source: _summary(groups.get(source, []), cash.get(source, 0.0))
            for source in sorted(set(groups) | set(cash))}


# ---------------------------------------------------------------- 小工具

#: 浮点残渣阈值：送股摊薄后批次股数可能是 130.00000000000003 这种数，
#: 用 `> 0` 判"还剩没剩"会留下一个永远吃不完的幽灵批次。
_EPS = 1e-9


def _ordered_rows(trades: pd.DataFrame) -> list[dict]:
    """按日期**稳定**排序后逐行返回 dict。

    - 稳定：同一天的多笔按录入顺序处理。不稳定的话，FIFO 在同日批次间的先后
      会随实现细节漂移，而这会悄悄改掉已实现盈亏。
    - 不看 `time`：它可以为空（多数人事后补记只记得日期），
      混着空值排序等于给"有没有填时刻"赋予了先后含义。
    - 排序键用 `parse_date`：日期认不出来的行排到最后，交给调用方标成坏行
      （不能让它们决定别人的先后）。
    """
    if trades.empty:
        return []
    rows = trades.to_dict("records")
    order = sorted(range(len(rows)),
                   key=lambda i: (schema.parse_date(rows[i].get(schema.DATE_COLUMN))
                                  or date.max, i))
    return [rows[i] for i in order]


def _book(books: dict[str, _Book], symbol: str) -> _Book:
    return books.setdefault(symbol, _Book(symbol=symbol))


def _sorted(books: dict[str, _Book]) -> list[_Book]:
    return [books[s] for s in sorted(books)]


def _position(book: _Book) -> Position:
    shares = round(book.shares, 2)
    cost = round(book.cost, 2)
    return Position(
        symbol=book.symbol, name=book.name, shares=shares, cost=cost,
        # 单位成本是"元/股"的单价而非金额，留 4 位小数：送股摊薄后
        # （10005 / 1300 = 7.6961…）两位小数会把每股差 0.0038 元的信息抹掉，
        # 乘上十万股就是几百块。成本总额与股数才是权威值，这一栏是给人看的。
        unit_cost=round(book.cost / book.shares, 4) if book.shares > _EPS else None,
        issues=tuple(book.issues))


def _source(row) -> str:
    return str(row.get("source") or "").strip()


def _fee(row) -> float:
    return schema.to_number(row.get("fee")) or 0.0


def _tax(row) -> float:
    return schema.to_number(row.get("tax")) or 0.0


def _usable(value: float | None) -> bool:
    """能不能当成一个成交事实用。

    与 `schema` 的 BAD_NUMBER 对齐（股数/价格必须为正）：两层对"什么算事实"的
    判断一旦分叉，被录入层拒收的行会在这里变成 0 成本的白捡股——
    持仓看着多了一千股，成本只有 5 块钱，而且不会有任何报错。
    """
    return value is not None and value > 0
