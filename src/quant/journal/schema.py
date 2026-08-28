"""交易日志的字段、行类型与校验规则（设计 §2 + §3）。纯函数，不碰文件系统。

**本模块最要紧的一条是"阻断 vs 警告"的语义**（设计 §3）：

    阻断只用于逻辑上不可能的事。其余一律「警告 + 记下来」——
    这是日志，首要职责是**如实记录发生了什么**，而不是替用户否定现实。

两个方向写反的代价都很大，而且不对称：

- **该警告的写成阻断**：用户明明这么成交了，系统不让记。缓存里没这只票、
  盘后大宗、漏记过一笔买入导致的"卖超"——每一种都会让日志出现缺口，
  而缺口会让此后所有 FIFO 配对与盈亏全错。
- **该阻断的写成警告**：一行没有股数的买入被记下来，持仓与盈亏静默变成 NaN。

所以可阻断的码集中在 `BLOCKING_CODES` 一处声明：一眼能看全，改动必须是显式的。
落在里面的只有两类：
1. 未来日期（设计指名要阻断的那件"逻辑上不可能的事"）；
2. 这行**根本没记下任何事实**——缺日期/代码/必填数字、类型无法解释、
   负股数负价格。它们不是"现实被否定"，而是一张没填完的表单；
   放行等于把 NaN 交给 FIFO，得到一个看着正常的错数字。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from quant.backtest.costs import commission, stamp_tax
from quant.config import Costs

# ---------------------------------------------------------------- 字段（设计 §2.3）
# 顺序即 journal/trades.csv 的列顺序：它是存储格式的契约，
# 随手改动会让已落盘的文件与代码对不上，也会把一次记账的 git diff 变成整个文件。
COLUMNS = (
    "trade_id",   # 稳定主键（录入时刻+序号），编辑/删除靠它定位，不靠行号
    "date",       # 成交日期
    "time",       # 成交时刻，可空（多数人事后补记只记得日期）
    "symbol",     # 6 位代码
    "name",       # 名称，从 data/symbols.parquet 自动带出
    "kind",       # buy / sell / adjust / dividend
    "shares",     # 股数（adjust 可为负；dividend 留空）
    "price",      # 成交价（dividend 留空）
    "amount",     # 现金流金额：买卖=股数×价格；dividend=到账金额
    "fee",        # 佣金，默认按成本模型自动算，可改成券商实际值
    "tax",        # 印花税（仅卖出），同上
    "source",     # 信号来源：策略名 / discretionary / other
    "stop_plan",  # 入场时**计划**的止损价
    "reason",     # 理由
    "note",       # 备注
)

# 字符串列的空值一律是 ""，数值列一律是 NaN/None。两者混用的话，
# 页面每处判空都要写 `pd.isna(x) or x == ""`，早晚漏一处。
STR_COLUMNS = ("trade_id", "time", "symbol", "name", "kind", "source", "reason", "note")
NUM_COLUMNS = ("shares", "price", "amount", "fee", "tax", "stop_plan")
DATE_COLUMN = "date"

KINDS = ("buy", "sell", "adjust", "dividend")

# adjust / dividend 不是可有可无的补丁（设计 §2.2）：
# 送股不是交易但股数确实变了，没有 adjust 就与券商对不上，而且**不会报错**；
# 静默忽略分红则是对长期持有收益的系统性低估。
REQUIRED_BY_KIND = {
    "buy": ("shares", "price"),
    "sell": ("shares", "price"),
    "adjust": ("shares",),      # 可正可负（送股 / 缩股），但不能是 0
    "dividend": ("amount",),    # 只有到账金额，要股数就是逼用户编数字
}

SOURCES = ("ma_cross", "donchian", "discretionary", "other")
LOT = 100                       # A 股买入的整手单位

# ---------------------------------------------------------------- 问题码
MISSING = "missing"                          # 必填缺失
BAD_KIND = "bad_kind"                        # 无法解释的记录类型
BAD_NUMBER = "bad_number"                    # 负价格/负股数/0 股调整等
BAD_DATE = "bad_date"                        # 日期无法解析
FUTURE_DATE = "future_date"                  # 晚于今天
SYMBOL_FORMAT = "symbol_format"              # 不是 6 位数字
SYMBOL_UNKNOWN = "symbol_unknown"            # 不在全市场清单里
PRICE_OUT_OF_RANGE = "price_out_of_range"    # 超出该日 [low, high]
NON_TRADING_DAY = "non_trading_day"          # 当天不是交易日
ODD_LOT = "odd_lot"                          # 买入非整手
OVERSELL = "oversell"                        # 卖出股数 > 当前持仓

#: 唯一一处"什么算阻断"的声明。见模块 docstring。
BLOCKING_CODES = frozenset({MISSING, BAD_KIND, BAD_NUMBER, BAD_DATE, FUTURE_DATE})


@dataclass(frozen=True)
class Issue:
    """一条校验结论。`blocking` 由码表推导，不单独存——
    两处各存一份迟早会不一致，而不一致的方向恰好是最危险的那个。"""
    code: str
    field: str
    message: str

    @property
    def blocking(self) -> bool:
        return self.code in BLOCKING_CODES


@dataclass(frozen=True)
class Context:
    """校验要用到的**外部事实**，全部可缺省。

    这是本系统相对通用记账工具的独有优势：它手里有行情，能替用户抓错。
    但每一项都可能拿不到（还没跑过扫描、缓存里没这只票、持仓还没算），
    **拿不到就什么都不说**——编一条"区间未知所以可疑"的警告，
    只会训练用户忽略所有警告。
    """
    names: Mapping[str, str] | None = None          # symbol → 名称（symbols.parquet）
    price_range: tuple[float, float] | None = None  # 该标的该日的 [low, high]
    trading_days: frozenset[date] | None = None     # 已知交易日
    position_shares: float | None = None            # 该标的当前持仓（卖超校验）


_NO_CONTEXT = Context()


# ---------------------------------------------------------------- 校验

def validate_trade(row: Mapping, *, today: date, context: Context | None = None) -> list[Issue]:
    """校验一行，返回**全部**问题（含警告），不抛异常。

    "全部"是刻意的：只报第一个的话，用户改完一个又冒一个，改到第三次就不看了；
    有阻断项时也不吞掉警告，否则用户修完日期重提才发现还有个卖超，两次往返都白付。

    调用方拿到列表后自己决定怎么用：`has_blocking()` 为真才拦下不写盘，
    其余一律照记 + 显眼地留在页面上（设计 §3："错误必须显眼、持续可见，
    不能记完就沉没"）。
    """
    ctx = context or _NO_CONTEXT
    issues: list[Issue] = []

    kind = str(row.get("kind") or "").strip()
    if not kind:
        issues.append(Issue(MISSING, "kind", "缺少记录类型（买入/卖出/股数调整/分红）"))
    elif kind not in KINDS:
        issues.append(Issue(BAD_KIND, "kind",
                            f"无法解释的记录类型 {kind!r}，只支持 {'/'.join(KINDS)}"))

    issues.extend(_check_date(row.get(DATE_COLUMN), today=today, ctx=ctx))
    issues.extend(_check_symbol(row.get("symbol"), ctx=ctx))
    if kind in KINDS:
        issues.extend(_check_numbers(row, kind=kind, ctx=ctx))
    return issues


def has_blocking(issues) -> bool:
    """有没有"必须拦下"的问题。页面只该用这一个判据，不要自己数 severity。"""
    return any(i.blocking for i in issues)


def blocking_issues(issues) -> list[Issue]:
    return [i for i in issues if i.blocking]


def warnings(issues) -> list[Issue]:
    return [i for i in issues if not i.blocking]


def _check_date(value, *, today: date, ctx: Context) -> list[Issue]:
    if is_blank(value):
        return [Issue(MISSING, DATE_COLUMN, "缺少成交日期：不知道什么时候发生的事记不下来")]
    d = parse_date(value)
    if d is None:
        return [Issue(BAD_DATE, DATE_COLUMN, f"日期 {value!r} 无法解析，请用 2026-08-27 这种写法")]
    if d > today:
        # 设计指名的唯一阻断项：明天的成交在逻辑上不可能发生。
        return [Issue(FUTURE_DATE, DATE_COLUMN, f"成交日期 {d} 晚于今天 {today}")]
    if ctx.trading_days is not None and d not in ctx.trading_days:
        # 不阻断：可能是场外/协议转让，也可能只是本地日历没覆盖到那么早。
        return [Issue(NON_TRADING_DAY, DATE_COLUMN, f"{d} 看起来不是交易日，请确认日期没记错")]
    return []


def _check_symbol(value, *, ctx: Context) -> list[Issue]:
    symbol = str(value or "").strip()
    if not symbol:
        return [Issue(MISSING, "symbol", "缺少标的代码：不知道是哪只票的记录没有意义")]
    if len(symbol) != 6 or not symbol.isdigit():
        # 不阻断：港股通/基金/未来的新代码规则都可能不是 6 位数字，而这是日志——先记下来。
        return [Issue(SYMBOL_FORMAT, "symbol", f"代码 {symbol!r} 不是 6 位数字，请确认没打错")]
    if ctx.names is not None and symbol not in ctx.names:
        # 不阻断：新股、退市股、清单滞后（is_fresh 最多允许 7 天）都会落到这里。
        return [Issue(SYMBOL_UNKNOWN, "symbol", f"{symbol} 不在全市场清单里（新股或已退市？）")]
    return []


def _check_numbers(row: Mapping, *, kind: str, ctx: Context) -> list[Issue]:
    issues: list[Issue] = []
    for field in REQUIRED_BY_KIND[kind]:
        value = row.get(field)
        if is_blank(value):
            issues.append(Issue(MISSING, field, f"{kind} 必须填写 {field}"))
            continue
        if to_number(value) is None:
            issues.append(Issue(BAD_NUMBER, field, f"{field} 的值 {value!r} 不是数字"))

    shares = to_number(row.get("shares"))
    price = to_number(row.get("price"))

    if kind in ("buy", "sell"):
        if shares is not None and shares <= 0:
            issues.append(Issue(BAD_NUMBER, "shares", f"{kind} 的股数必须为正，实际 {shares:g}"))
        if price is not None and price <= 0:
            issues.append(Issue(BAD_NUMBER, "price", f"成交价必须为正，实际 {price:g}"))
    elif kind == "adjust" and shares is not None and shares == 0:
        issues.append(Issue(BAD_NUMBER, "shares", "股数调整为 0 没有意义（送股填正数、缩股填负数）"))
    elif kind == "dividend":
        amount = to_number(row.get("amount"))
        if amount is not None and amount <= 0:
            issues.append(Issue(BAD_NUMBER, "amount", f"分红到账金额必须为正，实际 {amount:g}"))

    # 以下三条一律是警告。见模块 docstring 的"阻断 vs 警告"。
    if kind == "buy" and shares is not None and shares > 0 and shares % LOT:
        issues.append(Issue(ODD_LOT, "shares",
                            f"买入 {shares:g} 股不是 {LOT} 股的整数倍（可转债/科创板另有规则）"))
    if (kind == "sell" and shares is not None and ctx.position_shares is not None
            and shares > ctx.position_shares):
        issues.append(Issue(OVERSELL, "shares",
                            f"卖出 {shares:g} 股，超过当前持仓 {ctx.position_shares:g} 股，"
                            f"是不是漏记了一笔买入？"))
    if kind in ("buy", "sell") and price is not None and ctx.price_range is not None:
        low, high = ctx.price_range
        if not low <= price <= high:
            # 本功能最实用的一条：录入时打错一位（7.15 记成 71.5）在通用记账软件里无人可查。
            issues.append(Issue(PRICE_OUT_OF_RANGE, "price",
                                f"成交价 {price:g} 不在当日区间 [{low:.2f}, {high:.2f}] 内，"
                                f"是不是小数点错位了？"))
    return issues


# ---------------------------------------------------------------- 可推导字段的默认值

def apply_defaults(row: Mapping, *, costs: Costs,
                   names: Mapping[str, str] | None = None) -> dict:
    """补全能推导出来的字段（name / amount / fee / tax），返回**新的**完整行。

    两条铁律：

    1. **用户显式填了的一律不覆盖**。券商实际扣费与模型算的几乎不会完全相等
       （各家佣金不同、还有过户费），设计明写"可改成券商实际值"——填了就得留住。
       判空必须是"是不是没填"（None/空串/NaN），不能写成 `if not row["fee"]`：
       0 是合法的显式值（免佣活动、场内基金），会被那种写法默默改成 5 元。
    2. **推不出来就留空，绝不用 0 顶包**。缺股数的买入若被补成 amount=0，
       那是个看着正常的错数字——本项目一路在防的正是这种失败。

    费用一律调既有的 `backtest/costs.py`：日志与回测的口径一旦分叉，
    "我的实盘 vs 策略回测"这个并排比较（设计 §4.2）就不成立了。
    """
    unknown = [k for k in row if k not in COLUMNS]
    if unknown:
        # 字段名打错（symbol_code）时静默丢弃，落盘的就是一行没有代码的记录。
        raise ValueError(f"未知字段 {unknown}，可用字段：{list(COLUMNS)}")

    out: dict = {}
    for col in COLUMNS:
        value = row.get(col)
        if is_blank(value):
            out[col] = "" if col in STR_COLUMNS else None
        else:
            out[col] = value

    kind = str(out["kind"] or "").strip()
    symbol = str(out["symbol"] or "").strip()

    if not out["name"] and names and symbol in names:
        out["name"] = names[symbol]                # 查不到就留空：填"未知"看着像个名字

    shares, price = to_number(out["shares"]), to_number(out["price"])
    d = parse_date(out[DATE_COLUMN])

    if kind in ("buy", "sell") and shares is not None and price is not None:
        notional = abs(shares) * price
        _fill(out, "amount", round(notional, 2))
        _fill(out, "fee", round(commission(notional, costs), 2))
        if kind == "buy":
            _fill(out, "tax", 0.0)                 # 印花税仅卖出
        elif d is not None:
            # 必须按**成交日**取税率：补记 2023-08-25 的卖出要用当时的千一。
            # 用今天的税率算历史成交，是一个永远不会报错的错数字。
            _fill(out, "tax", round(stamp_tax(notional, d, costs), 2))
    elif kind == "adjust":
        _fill(out, "amount", 0.0)                  # 送股不产生现金流
        _fill(out, "fee", 0.0)
        _fill(out, "tax", 0.0)
    elif kind == "dividend":
        _fill(out, "fee", 0.0)                     # amount 是用户填的到账金额，不许推导覆盖
        _fill(out, "tax", 0.0)
    return out


def parse_date(value) -> date | None:
    """把 date / datetime / Timestamp / ISO 字符串统一成 date；认不出来返回 None。

    datetime 必须先于 date 判断——`isinstance(datetime_obj, date)` 恒为 True，
    漏判会让 datetime 一路流到比较处才炸（与 config._to_date 同一个坑）。
    """
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _fill(out: dict, key: str, value) -> None:
    if is_blank(out.get(key)):
        out[key] = value


def is_blank(value) -> bool:
    """没填。空串与 NaN 都算——前者来自表单，后者来自 CSV/DataFrame。

    公开而不是私有：`pnl` 也要照同一条规则判"这行有没有记下事实"。
    各写一份的话，某一天只有一边认得 NaN，缺股数的买入就会溜进 FIFO。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (date, datetime)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def to_number(value) -> float | None:
    """取数：没填返回 None，**认不出来也返回 None**（由调用方决定怎么报）。"""
    if is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
