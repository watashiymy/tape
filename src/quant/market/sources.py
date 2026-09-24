"""三家免费数据源的 URL、取数注入、解码与**按源分别写的**解析校验（spec §3）。

原则：一个数字只认一个来源；校验不过就当这次没取到（抛 ParseError），绝不显示可疑的数。
本模块不做任何缓存、不碰 streamlit；`http_get` 由调用方注入，默认 `default_http_get`
（requests，连接 2 秒 / 读取 3 秒超时）。
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Generic, TypeVar

HttpGet = Callable[[str, dict[str, str] | None], bytes]
T = TypeVar("T")

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


def _int(fields: list[str], i: int, what: str) -> int:
    try:
        return int(fields[i])
    except (IndexError, ValueError) as e:
        raise ParseError(f"{what}：第 {i} 段不是整数（{fields[i] if i < len(fields) else '缺'}）") from e


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
        up, flat, down, total = (_int(f, i, key) for i in (2, 3, 4, 5))
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
                      expected_day: date | None, *,
                      names: dict[str, str] | None = None) -> dict[str, IndexQuote]:
    """expected_day 是 §3「预期交易日」；None 表示三个来源都没有，允许用新浪自己的日期。
    names：若给出（代码 → 显示名），校验新浪返回的名称与之一致（spec §3「名称对上」）；
    None 表示不做这项校验（调用方没有可比对的名称）。"""
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
        if names is not None:
            expected_name = names.get(code)
            actual_name = f[0].strip()
            if expected_name is not None and actual_name != expected_name:
                raise ParseError(f"{code} 名称 {actual_name!r} 与预期 {expected_name!r} 不符")
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
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ParseError(f"{what}：不是合法 JSON") from e
    if not isinstance(doc, dict):
        raise ParseError(f"{what}：JSON 顶层不是对象（{type(doc).__name__}）")
    return doc


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
    if not isinstance(rows, list):
        raise ParseError(f"{code} 分时 data 不是列表（{type(rows).__name__}）")
    points = []
    for row in rows:
        try:
            parts = str(row).split()
            if len(parts) < 2:
                raise ValueError("段数不够")
            t = time(int(parts[0][:2]), int(parts[0][2:4]))
            price = float(parts[1])
        except (ValueError, IndexError, TypeError) as e:
            raise ParseError(f"{code} 分时行格式不对：{row!r}") from e
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


# ---------------------------------------------------------------- 东方财富 push2ex
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"      # 网页内置的固定值；东财改版会失效
EM_POOL_PAGESIZE = 10000                         # 给足：极端行情涨停上千只时 tc 仍须等于明细数
_POOL_KINDS = {"zt": ("getTopicZTPool", "fbt:asc"), "zb": ("getTopicZBPool", "fbt:asc")}


@dataclass(frozen=True)
class Distribution:
    buckets: dict[int, int]     # -11..11；0=平盘；±11=封住涨停/跌停；±10=超过 9% 未封板
    total: int
    up: int
    flat: int
    down: int
    scope: str


@dataclass(frozen=True)
class Pool:
    kind: str                   # "zt" | "zb"
    trade_date: date            # 我们请求时传的 date（qdate 不可信，不用）
    tc: int
    rows: int
    max_lbc: int | None         # 涨停池的最高连板；空池为 None


def em_fenbu_url() -> str:
    return f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={EM_UT}&dpt=wz.ztzt"


def em_pool_url(kind: str, day: date) -> str:
    name, sort = _POOL_KINDS[kind]
    return (f"https://push2ex.eastmoney.com/{name}?ut={EM_UT}&dpt=wz.ztzt&Pageindex=0"
            f"&pagesize={EM_POOL_PAGESIZE}&sort={sort}&date={day:%Y%m%d}")


def _em_data(raw: bytes, what: str) -> dict:
    doc = _json(raw, what)
    if doc.get("rc") != 0 or not isinstance(doc.get("data"), dict):
        raise ParseError(f"{what}：rc={doc.get('rc')}，data={type(doc.get('data')).__name__}")
    return doc["data"]


def parse_em_fenbu(raw: bytes) -> Distribution:
    data = _em_data(raw, "东财涨跌分布")
    fenbu = data.get("fenbu")
    if fenbu is None:
        fenbu = []
    if not isinstance(fenbu, list):
        raise ParseError(f"东财涨跌分布 fenbu 不是列表（{type(fenbu).__name__}）")
    buckets: dict[int, int] = {}
    for item in fenbu:
        if not isinstance(item, dict):
            raise ParseError(f"东财涨跌分布档位条目不是对象：{item!r}")
        for k, v in item.items():
            try:
                key, val = int(k), int(v)
            except (TypeError, ValueError) as e:
                raise ParseError(f"东财涨跌分布档位不是整数：{k}:{v}") from e
            if val < 0:
                raise ParseError(f"东财涨跌分布第 {key} 档为负：{val}")
            if key in buckets:
                raise ParseError(f"东财涨跌分布档位 {key} 重复出现")
            buckets[key] = val
    if sorted(buckets) != list(range(-11, 12)):
        raise ParseError(f"东财涨跌分布档位不全：{sorted(buckets)}")
    total = sum(buckets.values())
    if not 4000 <= total <= 7000:
        raise ParseError(f"东财涨跌分布合计 {total} 不在合理范围")
    up = sum(v for k, v in buckets.items() if k > 0)
    down = sum(v for k, v in buckets.items() if k < 0)
    return Distribution(buckets=buckets, total=total, up=up, flat=buckets[0], down=down,
                        scope=SCOPE_EM)


def parse_em_pool(raw: bytes, kind: str, requested: date) -> Pool:
    if kind not in _POOL_KINDS:
        raise ValueError(f"未知的池子种类：{kind!r}")
    what = f"东财{'涨停' if kind == 'zt' else '炸板'}池"
    data = _em_data(raw, what)
    if "tc" not in data:
        raise ParseError(f"{what}：返回里没有 tc 字段")
    try:
        tc = int(data["tc"])
    except (TypeError, ValueError) as e:
        raise ParseError(f"{what}：tc 不是整数（{data['tc']!r}）") from e
    pool = data.get("pool")
    if pool is None:
        if tc != 0:
            raise ParseError(f"{what}：tc={tc} 但没有 pool 明细")
        pool = []
    if not isinstance(pool, list):
        raise ParseError(f"{what}：pool 不是列表（{type(pool).__name__}）")
    for row in pool:
        if not isinstance(row, dict):
            raise ParseError(f"{what}：pool 里有一条不是对象：{row!r}")
    if tc != len(pool):
        raise ParseError(f"东财池子家数 tc={tc} ≠ 明细 {len(pool)} 条（pagesize 不够或返回被截断）")
    lbcs = []
    if kind == "zt":
        for row in pool:
            try:
                lbcs.append(int(row.get("lbc") or 0))
            except (TypeError, ValueError) as e:
                raise ParseError(f"{what}：lbc 不是整数（{row.get('lbc')!r}）") from e
        if any(x < 1 for x in lbcs):
            raise ParseError("涨停池 lbc 不是正整数")
    return Pool(kind=kind, trade_date=requested, tc=tc, rows=len(pool),
                max_lbc=(max(lbcs) if lbcs else None))


@dataclass(frozen=True)
class Result(Generic[T]):
    value: T | None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.value is None and self.error is None:
            raise ValueError("Result 不能 value 和 error 同时为空——不知道算成功还是失败")

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None


def fetch(http_get: HttpGet, url: str, parse: Callable[[bytes], T],
          headers: dict[str, str] | None = None) -> Result[T]:
    """取 + 解析，永不抛：网络与校验失败都折成 Result.error，由 live.py 决定兜底。"""
    try:
        return Result(value=parse(http_get(url, headers)))
    except (FetchError, ParseError) as e:
        return Result(value=None, error=str(e))
    except Exception as e:                       # 解析器里没预料到的形态也不许把页面打没
        logging.getLogger(__name__).warning(
            "%s 解析出现未预期异常（不是 FetchError/ParseError）：%s: %s", url, type(e).__name__, e)
        return Result(value=None, error=f"{type(e).__name__}: {e}")
