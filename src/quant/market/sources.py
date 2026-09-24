"""三家免费数据源的 URL、取数注入、解码与**按源分别写的**解析校验（spec §3）。

原则：一个数字只认一个来源；校验不过就当这次没取到（抛 ParseError），绝不显示可疑的数。
本模块不做任何缓存、不碰 streamlit；`http_get` 由调用方注入，默认 `default_http_get`
（requests，连接 2 秒 / 读取 3 秒超时）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

HttpGet = Callable[[str, dict[str, str] | None], bytes]

CONNECT_TIMEOUT_S = 2
READ_TIMEOUT_S = 3

SCOPE_TENCENT = "沪深 A 股，含 ST，停牌计入平盘"
SCOPE_EM = "沪深京 A 股，不含 ST 与停牌股"


class FetchError(RuntimeError):
    """网络层失败（超时、非 200、连接被断）。"""


class ParseError(ValueError):
    """返回体解析或校验失败——本轮这份数据作废。"""


def default_http_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    import requests
    try:
        r = requests.get(url, headers=headers or {}, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
    except requests.RequestException as e:
        raise FetchError(f"{url}: {e}") from e
    if r.status_code != 200:
        raise FetchError(f"{url}: HTTP {r.status_code}")
    return r.content


# ---------------------------------------------------------------- 腾讯行情
@dataclass(frozen=True)
class IndexQuote:
    code: str
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    change: float          # 涨跌额（腾讯给的；新浪由我们自己算）
    pct: float             # 涨跌幅 %
    amount_wan: float      # 成交额，万元
    ts: datetime | None    # 数据源自带的时刻（新浪：日期+时间）
    source: str            # "tencent" | "sina"


@dataclass(frozen=True)
class Breadth:
    up: int
    flat: int
    down: int
    total: int
    scope: str


_TENCENT_LINE = re.compile(r'v_(\w+)="([^"]*)"')
_T_MAX_INDEX = 37
_BREADTH_KEYS = ("bkqtRank_A_sh", "bkqtRank_A_sz")


def tencent_quote_url(codes: list[str]) -> str:
    return "https://qt.gtimg.cn/q=" + ",".join([*codes, *_BREADTH_KEYS])


def _tencent_segments(raw: bytes) -> dict[str, list[str]]:
    text = raw.decode("gbk", errors="replace")
    return {m.group(1): m.group(2).split("~") for m in _TENCENT_LINE.finditer(text)}


def _num(fields: list[str], i: int, what: str) -> float:
    try:
        return float(fields[i])
    except (IndexError, ValueError) as e:
        raise ParseError(f"{what}：第 {i} 段不是数字（{fields[i] if i < len(fields) else '缺'}）") from e


def parse_tencent_quotes(raw: bytes, codes: list[str]) -> dict[str, IndexQuote]:
    segs = _tencent_segments(raw)
    if "pv_none_match" in segs:
        raise ParseError("腾讯返回 v_pv_none_match：代码不被识别")
    out: dict[str, IndexQuote] = {}
    for code in codes:
        f = segs.get(code)
        if f is None:
            raise ParseError(f"腾讯返回里没有 {code} 这一段")
        if len(f) < _T_MAX_INDEX + 1:
            raise ParseError(f"{code} 只有 {len(f)} 段，少于所需的 {_T_MAX_INDEX + 1} 段")
        price, prev = _num(f, 3, code), _num(f, 4, code)
        opn, high, low = _num(f, 5, code), _num(f, 33, code), _num(f, 34, code)
        change, pct = _num(f, 31, code), _num(f, 32, code)
        if price <= 0 or prev <= 0:
            raise ParseError(f"{code} 价格非正：现价 {price} 昨收 {prev}")
        if abs(change - (price - prev)) > 0.011:
            raise ParseError(f"{code} 涨跌额 {change} 与 现价−昨收 {price - prev:.4f} 对不上")
        if abs(pct - 100 * change / prev) > 0.006:
            raise ParseError(f"{code} 涨跌幅 {pct} 与 涨跌额/昨收 对不上")
        if opn > 0 and (high < max(opn, price) - 1e-9 or low > min(opn, price) + 1e-9):
            raise ParseError(f"{code} 高低价与开盘/现价关系不成立")
        out[code] = IndexQuote(code=code, name=f[1], price=price, prev_close=prev, open=opn,
                               high=high, low=low, change=change, pct=pct,
                               amount_wan=_num(f, 37, code), ts=_tencent_ts(f[30]),
                               source="tencent")
    return out


def _tencent_ts(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def parse_tencent_breadth(raw: bytes) -> Breadth:
    segs = _tencent_segments(raw)
    counts = []
    for key in _BREADTH_KEYS:
        f = segs.get(key)
        if f is None or len(f) < 6:
            raise ParseError(f"腾讯返回里没有 {key}")
        up, flat, down, total = (int(_num(f, i, key)) for i in (2, 3, 4, 5))
        if up + flat + down != total:
            raise ParseError(f"{key} 涨+平+跌 ≠ 总（{up}+{flat}+{down} ≠ {total}）")
        counts.append((up, flat, down, total))
    up, flat, down, total = (sum(c[i] for c in counts) for i in range(4))
    if not 4000 <= total <= 7000:
        raise ParseError(f"沪深 A 股总数 {total} 不在合理范围")
    return Breadth(up=up, flat=flat, down=down, total=total, scope=SCOPE_TENCENT)


# ---------------------------------------------------------------- 新浪行情（兜底）
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}   # 不带就 5 秒后 403（实测）
_SINA_LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')
_S_MIN_FIELDS = 33


def sina_quote_url(codes: list[str]) -> str:
    return "https://hq.sinajs.cn/list=" + ",".join(codes)


def parse_sina_quotes(raw: bytes, codes: list[str],
                      expected_day: date | None) -> dict[str, IndexQuote]:
    """expected_day 是 §3「预期交易日」；None 表示三个来源都没有，允许用新浪自己的日期。"""
    text = raw.decode("gb18030", errors="replace")
    segs = {m.group(1): m.group(2) for m in _SINA_LINE.finditer(text)}
    out: dict[str, IndexQuote] = {}
    for code in codes:
        body = segs.get(code)
        if not body:
            raise ParseError(f"新浪返回里 {code} 是空的（代码不被识别或未返回）")
        f = body.split(",")
        if len(f) < _S_MIN_FIELDS:
            raise ParseError(f"{code} 只有 {len(f)} 段，少于 {_S_MIN_FIELDS}")
        if f[32] != "00":
            raise ParseError(f"{code} 状态码 {f[32]!r} 不是 00")
        try:
            day = datetime.strptime(f[30], "%Y-%m-%d").date()
            ts = datetime.combine(day, time.fromisoformat(f[31]))
        except ValueError as e:
            raise ParseError(f"{code} 日期/时间解析失败：{f[30]} {f[31]}") from e
        if expected_day is not None and day != expected_day:
            raise ParseError(f"{code} 日期 {day} ≠ 预期交易日 {expected_day}")
        opn, prev, price = _num(f, 1, code), _num(f, 2, code), _num(f, 3, code)
        high, low = _num(f, 4, code), _num(f, 5, code)
        if price <= 0 or prev <= 0:
            raise ParseError(f"{code} 价格非正")
        if opn > 0 and (high < max(opn, price) - 1e-9 or low > min(opn, price) + 1e-9):
            raise ParseError(f"{code} 高低价关系不成立")
        change = price - prev
        out[code] = IndexQuote(code=code, name=f[0], price=price, prev_close=prev, open=opn,
                               high=high, low=low, change=change, pct=100 * change / prev,
                               amount_wan=_num(f, 9, code) / 1e4, ts=ts, source="sina")
    return out


# ---------------------------------------------------------------- 腾讯分时与日 K
@dataclass(frozen=True)
class Minute:
    code: str
    trade_date: date                       # 数据源给的交易日——判「今天是不是交易日」的唯一依据
    points: tuple[tuple[time, float], ...] # (分钟结束时刻, 价格)
    sh_status: str | None                  # "open" / "close" / None（未知取值 → 回落到时钟判断）


@dataclass(frozen=True)
class DailyBar:
    day: date
    amount_wan: float


def tencent_minute_url(code: str) -> str:
    return f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"


def tencent_kline_url(code: str, count: int = 5) -> str:
    return ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
            f"?param={code},day,,,{count},qfq")


def _json(raw: bytes, what: str) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ParseError(f"{what}：不是合法 JSON") from e


def _tencent_data(raw: bytes, code: str, what: str) -> dict:
    doc = _json(raw, what)
    if doc.get("msg"):                      # 报错时 code 仍是 0，只能看 msg 与 data 的类型
        raise ParseError(f"{what}：{doc['msg']}")
    data = doc.get("data")
    if not isinstance(data, dict) or code not in data:
        raise ParseError(f"{what}：返回里没有 {code}")
    return data[code]


_STATUS_WORDS = {"open": "open", "close": "close"}


def parse_tencent_minute(raw: bytes, code: str) -> Minute:
    d = _tencent_data(raw, code, f"{code} 分时")
    try:
        day = datetime.strptime(d["data"]["date"], "%Y%m%d").date()
        rows = d["data"]["data"]
    except (KeyError, TypeError, ValueError) as e:
        raise ParseError(f"{code} 分时缺少 date/data") from e
    points = []
    for row in rows:
        parts = str(row).split()
        if len(parts) < 2:
            raise ParseError(f"{code} 分时行格式不对：{row!r}")
        t = time(int(parts[0][:2]), int(parts[0][2:4]))
        price = float(parts[1])
        if price <= 0:
            raise ParseError(f"{code} 分时价格非正：{row!r}")
        points.append((t, price))
    if any(b <= a for (a, _), (b, _) in zip(points, points[1:])):
        raise ParseError(f"{code} 分时时间不单调")
    status = None
    market = (d.get("qt") or {}).get("market") or []
    if market and isinstance(market[0], str):
        for seg in market[0].split("|"):
            if seg.startswith("SH_"):
                status = _STATUS_WORDS.get(seg.split("_")[1])   # 未知词 → None
    return Minute(code=code, trade_date=day, points=tuple(points), sh_status=status)


def parse_tencent_kline(raw: bytes, code: str) -> list[DailyBar]:
    d = _tencent_data(raw, code, f"{code} 日K")
    rows = d.get("day")
    if not isinstance(rows, list) or not rows:
        raise ParseError(f"{code} 日K 为空")
    bars = []
    for row in rows:
        try:
            bars.append(DailyBar(day=datetime.strptime(row[0], "%Y-%m-%d").date(),
                                 amount_wan=float(row[8])))
        except (IndexError, TypeError, ValueError) as e:
            raise ParseError(f"{code} 日K 行格式不对：{row!r}") from e
    if any(b.day <= a.day for a, b in zip(bars, bars[1:])):
        raise ParseError(f"{code} 日K 日期不单调")
    return bars
