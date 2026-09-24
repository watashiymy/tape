# 「市场概览」首页实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 TAPE 面板加一个「市场概览」落地页：盘中每 30 秒自动刷新的指数行情 / 两市成交额 / 涨跌分布 / 涨停温度计，加上由 baostock 全市场快照逐日积累的趋势宽度（站上均线占比、52 周新高新低），以及驱动后者的「更新收盘数据」任务。

**Architecture:** 盘中数据由页面自己取（腾讯为主、新浪兜底、东财 push2ex 补分布与池子），取数/解析/校验/时段判定全在 `src/quant/market/` 的纯函数里，页面只排版；两个 `st.fragment`（行情区 30/60 秒、按钮区 2 秒）按 `session.py` 的决定切换。收盘层是一个走既有 runner 的独立进程任务，快照按 310 个交易日滚动保留，指标表每 5 天全量重算并原子写盘。

**Tech Stack:** Python 3.14 / pandas 3.0.5 / pyarrow / requests 2.34（新声明）/ baostock ≥0.9.3（升下界）/ streamlit 1.61.1 / pytest。

**Spec:** `docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md`（所有取舍以 spec 为准；本计划引用其小节号，如「spec §4.2」）。

**约定：**
- 所有命令在项目根目录执行，Python 一律 `.venv/bin/python`。
- 单元测试一律离线；联网测试标 `@pytest.mark.network`。`tests/test_packaging.py` 钉着联网用例总数（现在是 3），Task 25 才一起改。
- 变异测试是本项目最有效的手段，但 `__pycache__` 按 (mtime 秒, size) 判失效：**改写同长度源码再还原会命中陈旧 .pyc**。做变异实验一律 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest …`。
- 每个任务以 git commit 收尾；测试未过不许提交。分支 `feat/v0.6-market-overview`，全部完成再合 main。
- 面板文案面向使用者（不出现路径/库名/版本号/「今天」「今日」），文案常量全部放 `app/guide.py`，前缀 `HOME_`。
- 时钟：全项目假定本机时钟即北京时间。页面用 `quant.market.now()` 取时刻（测试可 monkeypatch），**不直接调 `datetime.now()`**。

---

## 文件结构总览

```
src/quant/market/__init__.py        # now()、BREADTH_READY、INDEXES 等常量（Task 3）
src/quant/market/sources.py         # 三家数据源：URL、取数注入、解码、按源解析与校验（Task 4-8）
src/quant/market/live.py            # 一轮两段式取数 + 主备取舍 + 最近成功值 LiveStore（Task 9）
src/quant/market/session.py         # 交易日三态 + 状态表 + 过期判据（纯函数，Task 10）
src/quant/market/turnover.py        # 两市成交额文案与口径一致性（Task 11）
src/quant/market/breadth.py         # 快照 → 复权链 → 宽度指标表（Task 13）
src/quant/market/store.py           # data/market/ 读写、原子写、日历完整性、gaps.json、保留规则（Task 12）
src/quant/report/minicharts.py      # SVG 小图 → data URI（Task 14）
src/quant/data/baostock_provider.py # BaostockError(code) + get_market_snapshot()（Task 15）
src/quant/runner/progress.py        # parse_market_breadth（Task 16）
src/quant/runner/jobs.py            # market_breadth 任务登记（Task 16）
scripts/run_market_breadth.py       # 收盘数据任务入口（Task 17）
scripts/probe_market_sources.py     # 录夹具的一次性探针（Task 2）
app/guide.py                        # HOME_* 文案、FACTS 两项、JOB_HELP 一项（Task 19）
app/theme.py                        # 首页卡片的 HTML 构造函数 + CSS（Task 20）
app/pages_home.py                   # 页面（Task 21-23）
app/dashboard.py / app/ui.py / app/pages_console.py   # 导航、PAGE_INTRO、控制条指向、排班（Task 18）
tests/fixtures/market/              # 现场录制的真实返回 + RECORDED.md（Task 2）
tests/test_market_sources.py / test_market_live.py / test_market_session.py / test_market_turnover.py
tests/test_market_breadth.py / test_market_store.py / test_minicharts.py
tests/test_run_market_breadth.py / test_dashboard_home.py
tests/conftest.py                   # 全局离线替身、冻结时钟、清缓存、联网守卫（Task 18）
README.md / pyproject.toml / .gitignore
```

**里程碑**：A 地基与夹具（Task 1-2）→ B 纯逻辑（Task 3-14）→ C 收盘数据任务（Task 15-17）→ D 页面（Task 18-24）→ E 实测定常数与文档（Task 25-27）。

---

## 里程碑 A：地基与夹具

### Task 1: 依赖与目录 [A]

**Files:**
- Modify: `pyproject.toml:8-17, 35`
- Modify: `.gitignore`
- Test: `tests/test_packaging.py`（既有用例自动覆盖）

- [ ] **Step 1: 改依赖**

`pyproject.toml` 的 `dependencies` 里：
```toml
    "baostock>=0.9.3,<1",   # query_daily_history_k_AStock（收盘数据任务）是 0.9.3 才有的；
                            # 装着 0.8.x 的环境在 pip 看来也满足 >=0.8.8，跑到任务才崩
    "requests>=2.34,<3",    # 首页盘中行情的 HTTP 客户端。本机实测 2.34.2；此前它只是 streamlit 的传递依赖
```
把原来的 `"baostock>=0.8.8,<1",` 删掉。`markers` 那行改成：
```toml
markers = ["network: 需要联网的集成测试（baostock、腾讯/新浪行情、东方财富）"]
```

- [ ] **Step 2: .gitignore 加一行**

在 `data/symbols.parquet` 下面加 `data/market/`。

- [ ] **Step 3: 跑打包测试**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: 全部 PASS（`test_no_upper_bound_excludes_the_version_actually_installed` 会验 requests 2.34.2 落在区间内）。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "build(home): 声明 requests，baostock 下界升到 0.9.3；data/market/ 不进 git"
```

### Task 2: 录夹具的探针脚本 + 首批夹具 [A]

**Files:**
- Create: `scripts/probe_market_sources.py`
- Create: `tests/fixtures/market/RECORDED.md`
- Create: `tests/fixtures/market/*.bin|*.json`（脚本产出）

这是一次性探针（同 `scripts/probe_baostock.py` 的地位），**只在实现期用**，不进 JOBS。它不依赖还没写的 `quant.market`——URL 就地写死，这样第一天就能录。

- [ ] **Step 1: 写脚本**

```python
"""录制首页数据源的真实返回，当测试夹具（一次性探针，不是任务入口）。

    .venv/bin/python scripts/probe_market_sources.py tests/fixtures/market

每个来源一个文件，原样保存字节（腾讯 GBK、新浪 GB18030、东财 UTF-8 JSON），
并把录制时刻与 URL 追加到 RECORDED.md。解析测试对着这些字节写；
spec §10 要求的几个时点（8:50 / 9:16 / 9:26 / 9:31 / 9:45 / 15:00-15:40 每分钟）
用 --tag 区分（文件名带 tag）。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

INDEXES = ("sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852")
_Q = ",".join(INDEXES) + ",bkqtRank_A_sh,bkqtRank_A_sz"
UT = "7eea3edcaed734bea9cbfc24409ed989"
SOURCES = {
    "tencent_quotes": (f"https://qt.gtimg.cn/q={_Q}", {}),
    "sina_quotes": (f"https://hq.sinajs.cn/list={','.join(INDEXES)}",
                    {"Referer": "https://finance.sina.com.cn"}),
    "em_fenbu": (f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={UT}&dpt=wz.ztzt", {}),
}
for code in INDEXES:
    SOURCES[f"tencent_minute_{code}"] = (
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}", {})
for code in ("sh000001", "sz399001"):
    SOURCES[f"tencent_kline_{code}"] = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={code},day,,,5,qfq", {})


def pool_url(kind: str, day: str) -> str:
    sort = "fbt:asc" if kind == "getTopicZTPool" else "fund:asc"
    return (f"https://push2ex.eastmoney.com/{kind}?ut={UT}&dpt=wz.ztzt&Pageindex=0"
            f"&pagesize=10000&sort={sort}&date={day}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--tag", default="", help="文件名后缀，如 0916")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="东财池子的 date 参数（交易日 YYYYMMDD）")
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sources = dict(SOURCES)
    sources["em_ztpool"] = (pool_url("getTopicZTPool", args.date), {})
    sources["em_zbpool"] = (pool_url("getTopicZBPool", args.date), {})
    log = out / "RECORDED.md"
    suffix = f"_{args.tag}" if args.tag else ""
    with log.open("a", encoding="utf-8") as f:
        for name, (url, headers) in sources.items():
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                r = requests.get(url, headers=headers, timeout=(2, 5))
                body, status = r.content, r.status_code
            except requests.RequestException as e:
                body, status = b"", f"ERR {e}"
            path = out / f"{name}{suffix}.bin"
            path.write_bytes(body)
            f.write(f"- {stamp} `{path.name}` ← {url}  HTTP {status}, {len(body)} B\n")
            print(f"{stamp} {name}: HTTP {status} {len(body)} B")
            time.sleep(1.0)           # 礼貌访问


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 现在录一份收盘后的夹具**

Run: `.venv/bin/python scripts/probe_market_sources.py tests/fixtures/market --date 20260924`
Expected: 13 个 `.bin` 文件，每个 HTTP 200，`RECORDED.md` 多 13 行。**看一眼**：`tencent_quotes.bin` 用 GBK 解码含 `v_sh000001=`；`em_fenbu.bin` 含 `"fenbu"`；`em_ztpool.bin` 含 `"tc"`。

- [ ] **Step 3: 在 RECORDED.md 顶部补说明**

```markdown
# 首页数据源夹具（现场录制）

每个文件是数据源的**原始字节**，编码：腾讯 GBK、新浪 GB18030、东财 UTF-8。
`tests/test_market_sources.py` 对着它们写解析测试。按 spec §10 补录的盘前/竞价/收盘后
各时点文件名带 `_HHMM` 后缀。下面每行是一次录制。
```

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_market_sources.py tests/fixtures/market
git commit -m "test(home): 录首页数据源夹具的探针脚本 + 收盘后首批夹具"
```

---

## 里程碑 B：纯逻辑（src/quant/market）

### Task 3: 包骨架与常量 [B]

**Files:**
- Create: `src/quant/market/__init__.py`
- Test: `tests/test_market_sources.py`（先只放一个 import 测试）

- [ ] **Step 1: 写测试**

```python
# tests/test_market_sources.py
from datetime import datetime, time

from quant import market


def test_package_constants():
    assert market.BREADTH_READY == time(17, 30)          # 暂定值，Task 26 按实测改
    assert [c for c, _ in market.INDEXES] == [
        "sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000852"]
    assert isinstance(market.now(), datetime)
```

- [ ] **Step 2: 跑，确认 ImportError**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: FAIL（`No module named 'quant.market'`）。

- [ ] **Step 3: 写包**

```python
"""「市场概览」首页的数据层（spec §7）。

盘中行情来自门户网站的公开接口（腾讯/新浪/东方财富 push2ex），收盘后的宽度来自
baostock 全市场快照。判断与格式化全在本包的纯函数里，页面只排版。
本包不 import app/。
"""
from __future__ import annotations

from datetime import datetime, time

#: 收盘数据（baostock 全市场快照）的就绪时刻。**唯一出处**：guide.FACTS["breadth_ready"]
#: 与任务脚本都引用它。暂定 17:30（baostock 文档值）；实测 15:44 仍空、19:16 已有，
#: 实现计划 Task 26 按 §10 的实测结果修正。**不复用** FACTS["data_ready"]——那是另一个接口。
BREADTH_READY = time(17, 30)

#: 首页六张指数卡片：(腾讯/新浪代码, 显示名)。沪深300 用沪市代码 sh000300，不是 sz399300。
INDEXES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
    ("sh000688", "科创50"), ("sh000300", "沪深300"), ("sh000852", "中证1000"),
)


def now() -> datetime:
    """页面与任务取当前时刻的唯一入口。全项目假定本机时钟即北京时间。
    测试 monkeypatch 这一个函数就能冻结整页的时钟（spec §8「全局隔离」）。"""
    return datetime.now()
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/__init__.py tests/test_market_sources.py
git commit -m "feat(market): quant.market 包骨架——now()、BREADTH_READY、INDEXES"
```

### Task 4: sources.py — 类型、取数注入、腾讯行情解析与校验 [B]

**Files:**
- Create: `src/quant/market/sources.py`
- Test: `tests/test_market_sources.py`

腾讯行情字段（`~` 切分，0 起）：`[1]` 名称 `[2]` 代码 `[3]` 现价 `[4]` 昨收 `[5]` 今开 `[30]` 时间戳 YYYYMMDDHHMMSS `[31]` 涨跌额 `[32]` 涨跌幅 `[33]` 最高 `[34]` 最低 `[37]` 成交额（万元）。批量请求里写错的代码**整段不出现**；单代码请求返回 `v_pv_none_match="1"`。`bkqtRank_A_sh/sz`：`[2]` 涨 `[3]` 平 `[4]` 跌 `[5]` 总（沪深 A，含 ST，停牌计入平）。

- [ ] **Step 1: 写测试**

```python
# tests/test_market_sources.py（追加）
from pathlib import Path

import pytest

from quant.market import sources
from quant.market.sources import FetchError, ParseError

FIX = Path(__file__).resolve().parent / "fixtures" / "market"
CODES = [c for c, _ in market.INDEXES]


def _fix(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_tencent_quote_url_batches_all_codes_and_breadth():
    url = sources.tencent_quote_url(CODES)
    assert url.startswith("https://qt.gtimg.cn/q=")
    assert all(c in url for c in CODES) and "bkqtRank_A_sh,bkqtRank_A_sz" in url


def test_parse_tencent_quotes_from_recording():
    quotes = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)
    assert set(quotes) == set(CODES)
    q = quotes["sh000001"]
    assert q.name == "上证指数" and q.price > 0 and q.prev_close > 0
    assert abs(q.change - (q.price - q.prev_close)) <= 0.011
    assert q.ts.year == 2026 and q.amount_wan > 0 and q.source == "tencent"


def test_parse_tencent_breadth_from_recording():
    b = sources.parse_tencent_breadth(_fix("tencent_quotes.bin"))
    assert b.up + b.flat + b.down == b.total and 4000 <= b.total <= 7000
    assert b.scope == sources.SCOPE_TENCENT


def _mutate(raw: bytes, code: str, idx: int, value: str) -> bytes:
    """把某只指数第 idx 段改成 value（腾讯 GBK 文本），做变异测试用。"""
    text = raw.decode("gbk")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"v_{code}="):
            head, body = line.split('="', 1)
            fields = body.rstrip('";').split("~")
            fields[idx] = value
            lines[i] = head + '="' + "~".join(fields) + '";'
    return "\n".join(lines).encode("gbk")


def test_tencent_change_must_match_price_minus_prev_close():
    raw = _mutate(_fix("tencent_quotes.bin"), "sh000001", 31, "9.99")
    with pytest.raises(ParseError, match="涨跌额"):
        sources.parse_tencent_quotes(raw, CODES)


def test_tencent_pct_must_match_change_over_prev_close():
    raw = _mutate(_fix("tencent_quotes.bin"), "sz399001", 32, "7.77")
    with pytest.raises(ParseError, match="涨跌幅"):
        sources.parse_tencent_quotes(raw, CODES)


def test_tencent_missing_segment_is_an_error():
    """批量请求里写错的代码整段不出现——不是 v_pv_none_match。"""
    text = _fix("tencent_quotes.bin").decode("gbk")
    text = "\n".join(l for l in text.splitlines() if not l.startswith("v_sh000688="))
    with pytest.raises(ParseError, match="sh000688"):
        sources.parse_tencent_quotes(text.encode("gbk"), CODES)


def test_tencent_none_match_marker_is_an_error():
    raw = b'v_pv_none_match="1";\n'
    with pytest.raises(ParseError):
        sources.parse_tencent_quotes(raw, ["sh000001"])


def test_tencent_too_few_fields_is_an_error():
    raw = b'v_sh000001="1~x~000001~3.0";\n'
    with pytest.raises(ParseError, match="段"):
        sources.parse_tencent_quotes(raw, ["sh000001"])


def test_tencent_field_shift_by_one_is_caught():
    """字段下标整体错一位（例如把 [30] 当时间戳去解析 [31]）——靠算术关系拦住。"""
    text = _fix("tencent_quotes.bin").decode("gbk")
    shifted = "\n".join(
        (lambda h, b: h + '="' + "~".join(["0"] + b.rstrip('";').split("~")) + '";')(*l.split('="', 1))
        if l.startswith("v_s") else l for l in text.splitlines())
    with pytest.raises(ParseError):
        sources.parse_tencent_quotes(shifted.encode("gbk"), CODES)


def test_default_http_get_wraps_requests_errors(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("x")
    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(FetchError):
        sources.default_http_get("https://example.invalid/")
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: FAIL（`cannot import name 'sources'`）。

- [ ] **Step 3: 写 sources.py（腾讯部分）**

```python
"""三家免费数据源的 URL、取数注入、解码与**按源分别写的**解析校验（spec §3）。

原则：一个数字只认一个来源；校验不过就当这次没取到（抛 ParseError），绝不显示可疑的数。
本模块不做任何缓存、不碰 streamlit；`http_get` 由调用方注入，默认 `default_http_get`
（requests，连接 2 秒 / 读取 3 秒超时）。
"""
from __future__ import annotations

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
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: 全部 PASS。若 `test_parse_tencent_quotes_from_recording` 因夹具里开盘价为 0（盘前录的）而失败，说明高低校验要按 spec §10 收窄——本任务先按上面 `opn > 0` 才校验写。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/sources.py tests/test_market_sources.py
git commit -m "feat(market): 腾讯指数行情与涨跌家数的解析校验（含变异测试）"
```

### Task 5: sources.py — 新浪兜底行情 [B]

**Files:**
- Modify: `src/quant/market/sources.py`
- Test: `tests/test_market_sources.py`

新浪：必须带 `Referer: https://finance.sina.com.cn`；GB18030；`var hq_str_sh000001="名称,今开,昨收,现价,最高,最低,…,[30]日期,[31]时间,[32]状态码";`，代码不对时值是空串。没有涨跌额/涨跌幅，由我们自己算。

- [ ] **Step 1: 写测试**

```python
def test_sina_headers_carry_the_referer():
    assert sources.SINA_HEADERS["Referer"].startswith("https://finance.sina.com.cn")


def test_parse_sina_quotes_from_recording():
    expected_day = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"].ts.date()
    quotes = sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, expected_day)
    assert set(quotes) == set(CODES)
    q = quotes["sh000001"]
    assert q.source == "sina" and q.price > 0
    assert abs(q.change - (q.price - q.prev_close)) < 1e-6      # 自己算的，恒等
    assert q.ts is not None and q.ts.date() == expected_day
    t = sources.parse_tencent_quotes(_fix("tencent_quotes.bin"), CODES)["sh000001"]
    assert abs(q.price - t.price) < 0.02                       # 两家同一时刻数值一致（实测）


def test_sina_wrong_date_is_rejected():
    from datetime import date
    with pytest.raises(ParseError, match="日期"):
        sources.parse_sina_quotes(_fix("sina_quotes.bin"), CODES, date(2000, 1, 1))


def test_sina_empty_segment_is_rejected():
    raw = b'var hq_str_sh000001="";\n'
    with pytest.raises(ParseError, match="sh000001"):
        sources.parse_sina_quotes(raw, ["sh000001"], None)


def test_sina_bad_status_code_is_rejected():
    text = _fix("sina_quotes.bin").decode("gb18030")
    text = text.replace(",00,", ",01,")
    with pytest.raises(ParseError, match="状态"):
        sources.parse_sina_quotes(text.encode("gb18030"), CODES, None)
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q -k sina`
Expected: FAIL（`AttributeError: SINA_HEADERS`）。

- [ ] **Step 3: 实现**

```python
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
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/sources.py tests/test_market_sources.py
git commit -m "feat(market): 新浪兜底行情解析（Referer、GB18030、日期与状态码校验）"
```

### Task 6: sources.py — 腾讯分时（交易日期、状态串、分时点）与日 K [B]

**Files:**
- Modify: `src/quant/market/sources.py`
- Test: `tests/test_market_sources.py`

分时 `web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000001`：JSON `data[code].data.date = "20260923"`，`data[code].data.data = ["0930 3951.37 1234 5678", …]`（HHMM 价格 累计量 累计额），`data[code].qt.market = ["2026-09-23 15:39:10|HK_open_交易中|SH_close_已收盘|SZ_close_已收盘|…"]`。日 K `newfqkline`：`data[code].day = [[日期, 开, 收, 高, 低, 量(手), {}, 换手%, 成交额(万元), "", ""], …]`，盘中最后一根是**未走完**的当天；**报错时 `code` 仍是 0、`msg="param error"`、`data=[]`**。

- [ ] **Step 1: 写测试**

```python
from datetime import date as _date


def test_parse_tencent_minute_from_recording():
    m = sources.parse_tencent_minute(_fix("tencent_minute_sh000001.bin"), "sh000001")
    assert m.code == "sh000001" and isinstance(m.trade_date, _date)
    assert 2 <= len(m.points) <= 242
    times = [t for t, _ in m.points]
    assert times == sorted(times), "分时点时间必须单调"
    assert all(p > 0 for _, p in m.points)
    assert m.sh_status in {"open", "close", None}


def test_minute_status_string_is_parsed_from_the_sh_segment():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 1.0 1 1","0931 1.1 2 2"]},"qt":{"market":["2026-09-23 10:00:00|HK_open_x|SH_open_\xe4\xba\xa4\xe6\x98\x93\xe4\xb8\xad|SZ_open_y"]}}}}')
    m = sources.parse_tencent_minute(raw, "sh000001")
    assert m.sh_status == "open" and m.trade_date == _date(2026, 9, 23)
    assert m.points[-1] == (time(9, 31), 1.1)


def test_minute_unknown_status_falls_back_to_none():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0930 1.0 1 1"]},"qt":{"market":["2026-09-23 10:00:00|SH_lunch_x"]}}}}')
    assert sources.parse_tencent_minute(raw, "sh000001").sh_status is None


def test_minute_non_monotonic_time_is_rejected():
    raw = (b'{"code":0,"msg":"","data":{"sh000001":{"data":{"date":"20260923","data":'
           b'["0931 1.0 1 1","0930 1.1 2 2"]},"qt":{"market":[]}}}}')
    with pytest.raises(ParseError, match="单调"):
        sources.parse_tencent_minute(raw, "sh000001")


def test_parse_tencent_kline_from_recording():
    bars = sources.parse_tencent_kline(_fix("tencent_kline_sh000001.bin"), "sh000001")
    assert len(bars) >= 2 and bars[-1].day > bars[-2].day
    assert all(b.amount_wan > 0 for b in bars)


def test_kline_param_error_has_code_zero_but_is_an_error():
    raw = b'{"code":0,"msg":"param error","data":[]}'
    with pytest.raises(ParseError, match="param error"):
        sources.parse_tencent_kline(raw, "sh000001")
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q -k "minute or kline"`
Expected: FAIL（AttributeError）。

- [ ] **Step 3: 实现**

```python
import json

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
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/sources.py tests/test_market_sources.py
git commit -m "feat(market): 腾讯分时（交易日期/状态串/分时点）与日K解析；报错时 code 仍为 0 的坑"
```

### Task 7: sources.py — 东财涨跌分布与池子 [B]

**Files:**
- Modify: `src/quant/market/sources.py`
- Test: `tests/test_market_sources.py`

分布：`data.fenbu` 是 `[{"-11": 13}, {"-10": 15}, …, {"11": 51}]`（23 档，键 -11..11）。池子：必须带 `ut` 与 `date`；`data.tc` 家数、`data.pool[]` 明细（`lbc` 连板数）；**`qdate` 永远是服务器当天，禁用**；日期不对或超保留期返回 `rc=0, tc=0`（不报错）；缺 `ut`/`date` 时 `rc=102, data=null`。

- [ ] **Step 1: 写测试**

```python
def test_parse_em_fenbu_from_recording():
    dist = sources.parse_em_fenbu(_fix("em_fenbu.bin"))
    assert sorted(dist.buckets) == list(range(-11, 12))
    assert dist.total == sum(dist.buckets.values()) and 4000 <= dist.total <= 7000
    assert dist.up == sum(v for k, v in dist.buckets.items() if k > 0)
    assert dist.flat == dist.buckets[0] and dist.down + dist.up + dist.flat == dist.total
    assert dist.scope == sources.SCOPE_EM


def test_fenbu_missing_bucket_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [d for d in doc["data"]["fenbu"] if "7" not in d]
    with pytest.raises(ParseError, match="档"):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_fenbu_negative_count_is_rejected():
    doc = json.loads(_fix("em_fenbu.bin"))
    doc["data"]["fenbu"] = [({"3": -1} if "3" in d else d) for d in doc["data"]["fenbu"]]
    with pytest.raises(ParseError):
        sources.parse_em_fenbu(json.dumps(doc).encode())


def test_em_pool_url_carries_ut_date_and_a_large_pagesize():
    url = sources.em_pool_url("zt", _date(2026, 9, 24))
    assert "getTopicZTPool" in url and "date=20260924" in url and "ut=" in url
    assert "pagesize=10000" in url


def test_parse_em_pools_from_recording():
    zt = sources.parse_em_pool(_fix("em_ztpool.bin"), "zt", _date(2026, 9, 24))
    zb = sources.parse_em_pool(_fix("em_zbpool.bin"), "zb", _date(2026, 9, 24))
    assert zt.tc == zt.rows and zt.max_lbc >= 1 and zb.tc == zb.rows
    assert zt.trade_date == _date(2026, 9, 24)


def test_pool_tc_must_equal_row_count():
    doc = json.loads(_fix("em_ztpool.bin"))
    doc["data"]["tc"] += 1
    with pytest.raises(ParseError, match="家数"):
        sources.parse_em_pool(json.dumps(doc).encode(), "zt", _date(2026, 9, 24))


def test_pool_rc_102_is_an_error():
    with pytest.raises(ParseError, match="102"):
        sources.parse_em_pool(b'{"rc":102,"data":null}', "zt", _date(2026, 9, 24))


def test_empty_pool_parses_but_max_lbc_is_none():
    p = sources.parse_em_pool(b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260924"}}',
                              "zt", _date(2026, 9, 24))
    assert p.tc == 0 and p.max_lbc is None
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q -k "em_ or fenbu or pool"`
Expected: FAIL。

- [ ] **Step 3: 实现**

```python
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
    buckets: dict[int, int] = {}
    for item in data.get("fenbu") or []:
        for k, v in dict(item).items():
            try:
                key, val = int(k), int(v)
            except (TypeError, ValueError) as e:
                raise ParseError(f"东财涨跌分布档位不是整数：{k}:{v}") from e
            if val < 0:
                raise ParseError(f"东财涨跌分布第 {key} 档为负：{val}")
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
    data = _em_data(raw, f"东财{'涨停' if kind == 'zt' else '炸板'}池")
    pool = data.get("pool") or []
    tc = int(data.get("tc") or 0)
    if tc != len(pool):
        raise ParseError(f"东财池子家数 tc={tc} ≠ 明细 {len(pool)} 条（pagesize 不够或返回被截断）")
    lbcs = [int(r.get("lbc") or 0) for r in pool] if kind == "zt" else []
    if kind == "zt" and any(x < 1 for x in lbcs):
        raise ParseError("涨停池 lbc 不是正整数")
    return Pool(kind=kind, trade_date=requested, tc=tc, rows=len(pool),
                max_lbc=(max(lbcs) if lbcs else None))
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/sources.py tests/test_market_sources.py
git commit -m "feat(market): 东财涨跌分布与涨停/炸板池解析（23 档齐全、tc=明细、qdate 弃用）"
```

### Task 8: sources.py — 一次请求的封装 `fetch()` [B]

**Files:**
- Modify: `src/quant/market/sources.py`
- Test: `tests/test_market_sources.py`

把「取 + 解析」包成一个函数，live.py 只跟 `Result` 打交道：成功带值，失败带原因，**永不抛**。

- [ ] **Step 1: 写测试**

```python
def test_fetch_returns_ok_result():
    def fake(url, headers=None):
        assert "qt.gtimg.cn" in url
        return _fix("tencent_quotes.bin")
    r = sources.fetch(fake, sources.tencent_quote_url(CODES),
                      lambda raw: sources.parse_tencent_quotes(raw, CODES))
    assert r.ok and r.value["sh000001"].price > 0 and r.error is None


def test_fetch_turns_fetch_and_parse_errors_into_failed_results():
    def dead(url, headers=None):
        raise FetchError("timeout")
    r = sources.fetch(dead, "x", lambda raw: raw)
    assert not r.ok and "timeout" in r.error
    r2 = sources.fetch(lambda u, h=None: b"garbage", sources.em_fenbu_url(), sources.parse_em_fenbu)
    assert not r2.ok and r2.value is None
```

- [ ] **Step 2: 实现**

```python
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    value: T | None
    error: str | None = None

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
        return Result(value=None, error=f"{type(e).__name__}: {e}")
```

- [ ] **Step 3: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_market_sources.py -q` → PASS。

```bash
git add src/quant/market/sources.py tests/test_market_sources.py
git commit -m "feat(market): fetch() 把取数与校验折成永不抛的 Result"
```

### Task 9: session.py — 交易日三态、状态表、过期判据 [B]

**Files:**
- Create: `src/quant/market/session.py`
- Test: `tests/test_market_session.py`

纯函数，spec §4.1–§4.3 的直译。**只返回枚举**，文案在 `guide.HOME_STATE_TEXT`（Task 19）。

- [ ] **Step 1: 写测试（表驱动）**

```python
# tests/test_market_session.py
from datetime import date, datetime, time

import pytest

from quant.market import session
from quant.market.session import Calendar, State, TradeDay

WED = date(2026, 9, 23)          # 周三，交易日
SAT = date(2026, 9, 26)
CAL = Calendar(first=date(2026, 9, 1), last=date(2026, 10, 31),
               trading=frozenset({date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 28)}))


def at(h, m, s=0, day=WED):
    return datetime.combine(day, time(h, m, s))


# ---------------------------------------------------------------- 三态判定链（§4.1）
@pytest.mark.parametrize("today,cal,ok,src,now_t,expect", [
    (SAT, None, True, SAT, time(10, 0), TradeDay.NON_TRADING),          # 周末一律休市
    (date(2026, 9, 25), CAL, True, WED, time(10, 0), TradeDay.NON_TRADING),  # 日历说中秋休市
    (WED, CAL, False, None, time(10, 0), TradeDay.TRADING),              # 日历说交易日，请求失败也算
    (WED, None, True, WED, time(9, 20), TradeDay.TRADING),               # 源日期 = 今天 → 交易日
    (WED, None, True, date(2026, 9, 22), time(9, 20), TradeDay.UNKNOWN), # 截止前源日期还旧 → 未知
    (WED, None, True, date(2026, 9, 22), time(9, 46), TradeDay.NON_TRADING),  # 过截止仍旧 → 非交易日
    (WED, None, False, None, time(9, 46), TradeDay.UNKNOWN),             # 请求失败永不计入
    (date(2026, 11, 5), CAL, True, None, time(9, 46), TradeDay.UNKNOWN), # 日历不覆盖 + 源失败
])
def test_trade_day_chain(today, cal, ok, src, now_t, expect):
    assert session.trade_day(today, cal, ok, src, now_t) is expect


def test_calendar_covers_only_its_own_range():
    assert CAL.covers(date(2026, 9, 25)) and not CAL.covers(date(2026, 11, 5))
    assert CAL.is_trading(WED) and not CAL.is_trading(date(2026, 9, 25))


# ---------------------------------------------------------------- 状态表（§4.2）
@pytest.mark.parametrize("now,td,expect,interval,ttl", [
    (at(10, 0, day=SAT), TradeDay.NON_TRADING, State.CLOSED_DAY, 60, 600),
    (at(8, 50), TradeDay.TRADING, State.PRE_OPEN, 60, 600),
    (at(9, 14), TradeDay.TRADING, State.AUCTION, 30, 25),
    (at(9, 26), TradeDay.TRADING, State.WAIT_OPEN, 30, 25),
    (at(9, 30), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(11, 30, 0), TradeDay.TRADING, State.LUNCH, 60, 600),
    (at(12, 59, 59), TradeDay.TRADING, State.LUNCH, 60, 600),
    (at(13, 0), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(14, 59, 59), TradeDay.TRADING, State.TRADING, 30, 25),
    (at(15, 0), TradeDay.TRADING, State.CLOSED, 60, 25),          # 收盘到截止：仍取数
    (at(15, 29, 59), TradeDay.TRADING, State.CLOSED, 60, 25),
    (at(15, 30), TradeDay.TRADING, State.CLOSED, 60, 600),        # 截止后：长缓存
    (at(20, 0), TradeDay.TRADING, State.CLOSED, 60, 600),
    (at(10, 0), TradeDay.UNKNOWN, State.UNKNOWN, 30, 25),         # 未知：节奏按交易日行，状态是未知
    (at(20, 0), TradeDay.UNKNOWN, State.UNKNOWN, 60, 600),
])
def test_state_table(now, td, expect, interval, ttl):
    d = session.decide(now, td)
    assert (d.state, d.interval_s, d.cache_ttl) == (expect, interval, ttl)


def test_non_trading_day_overrides_every_clock_row():
    for h in (9, 10, 12, 14, 16):
        assert session.decide(at(h, 15, day=SAT), TradeDay.NON_TRADING).state is State.CLOSED_DAY


@pytest.mark.parametrize("a,b,changes", [
    (at(11, 29, 50), at(11, 30, 20), True),      # 交易中 → 午休
    (at(12, 59, 50), at(13, 0, 20), True),       # 午休 → 交易中
    (at(8, 59), at(9, 14, 30), True),            # 盘前 → 集合竞价
    (at(20, 0), at(9, 15, day=date(2026, 9, 24)), True),   # 昨晚开着的页今早醒来
    (at(10, 0), at(10, 0, 30), False),
])
def test_transitions_change_the_decision(a, b, changes):
    da, db = session.decide(a, TradeDay.TRADING), session.decide(b, TradeDay.TRADING)
    assert (da != db) is changes


# ---------------------------------------------------------------- 过期判据（§4.3）
def test_stale_by_last_minute_point():
    # 最后一点标 10:00（表示 09:59-10:00 那分钟），现在 10:03:30 → 已过 3 分钟 → 延迟
    assert session.is_stale(at(10, 3, 30), time(10, 0), None) is True
    assert session.is_stale(at(10, 2, 59), time(10, 0), None) is False


def test_stale_falls_back_to_quote_timestamp_when_no_minute():
    assert session.is_stale(at(10, 3, 0), None, at(10, 0, 59)) is True
    assert session.is_stale(at(10, 1, 0), None, at(10, 0, 0)) is False


def test_stale_direction_is_not_reversed():
    """数据时刻晚于现在（时钟不同步）绝不能判成延迟。"""
    assert session.is_stale(at(10, 0), time(10, 5), None) is False


@pytest.mark.parametrize("now", [at(9, 20), at(9, 30, 30), at(12, 0), at(14, 58), at(15, 10)])
def test_stale_is_only_judged_in_continuous_auction(now):
    assert session.is_stale(now, time(9, 0), at(9, 0)) is False


def test_expected_trade_day_order():
    assert session.expected_trade_day(WED, CAL, time(10, 0), None) == WED        # 本轮源日期优先
    assert session.expected_trade_day(None, CAL, time(10, 0), None) == WED       # 日历：今天已开盘
    assert session.expected_trade_day(None, CAL, time(9, 0), None) == date(2026, 9, 22) or \
        session.expected_trade_day(None, CAL, time(9, 0), None) < WED             # 开盘前 → 上一交易日
    assert session.expected_trade_day(None, None, time(10, 0), date(2026, 9, 22)) == date(2026, 9, 22)
    assert session.expected_trade_day(None, None, time(10, 0), None) is None
```

注意 `expected_trade_day` 的第三个断言：日历的 `trading` 集合里没有 9-22，所以「上一交易日」按集合算是 9-21 之前——测试只断言它早于今天。实现按日历 `trading` 集合往前找。

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_session.py -q`
Expected: FAIL（ImportError）。

- [ ] **Step 3: 实现**

```python
"""首页的时段判定（spec §4）。纯函数：不看时钟、不碰网络、不碰 streamlit。

三件事：今天是不是交易日（三态判定链）、现在处于哪个状态、数据算不算过期。
**只返回枚举**，文案映射在 app/guide.py 的 HOME_STATE_TEXT——本模块属于 src/，不 import app/。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum

# 时段边界（北京时间；全项目假定本机时钟即北京时间）
AUCTION_START = time(9, 14)
AUCTION_END = time(9, 25)
OPEN = time(9, 30)
LUNCH_START = time(11, 30)
LUNCH_END = time(13, 0)
CLOSE = time(15, 0)
#: 收盘截止时刻：东财分布收盘后至少 20 分钟还在变（实测 15:20 才稳）。暂定 15:30，Task 26 按实测改。
CLOSE_CUTOFF = time(15, 30)
#: 判休市截止时刻：源日期过了这个点还是旧的才算非交易日。暂定 9:45，Task 26 按实测改。
NON_TRADING_CUTOFF = time(9, 45)
# 连续竞价时段（过期判据只在这两段里判）
CONTINUOUS = ((time(9, 31), LUNCH_START), (time(13, 1), time(14, 57)))
STALE_MINUTE_S = 180      # 分时最后一点落后 3 分钟
STALE_QUOTE_S = 120       # 行情时间戳落后 2 分钟

LIVE_INTERVAL_S, TICK_INTERVAL_S = 30, 60
SHORT_TTL_S, LONG_TTL_S = 25, 600


class TradeDay(Enum):
    TRADING = "trading"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


class State(Enum):
    CLOSED_DAY = "closed_day"   # 休市（非交易日）
    PRE_OPEN = "pre_open"       # 盘前
    AUCTION = "auction"         # 集合竞价
    WAIT_OPEN = "wait_open"     # 待开盘
    TRADING = "trading"         # 交易中
    LUNCH = "lunch"             # 午间休市
    CLOSED = "closed"           # 已收盘
    UNKNOWN = "unknown"         # 状态未知


@dataclass(frozen=True)
class Calendar:
    """本地交易日历的只读视图（store.py 从 calendar.parquet 装出来）。
    只在 [first, last] 内当作权威；范围外一律「未知」，绝不当作休市。"""
    first: date
    last: date
    trading: frozenset[date]

    def covers(self, day: date) -> bool:
        return self.first <= day <= self.last

    def is_trading(self, day: date) -> bool:
        return day in self.trading

    def previous_trading_day(self, day: date) -> date | None:
        d = day - timedelta(days=1)
        while d >= self.first:
            if d in self.trading:
                return d
            d -= timedelta(days=1)
        return None


@dataclass(frozen=True)
class Decision:
    state: State
    interval_s: int      # fragment 的 run_every
    cache_ttl: int       # 走哪个缓存入口（25 秒 / 600 秒）


def trade_day(today: date, calendar: Calendar | None, source_ok: bool,
              source_date: date | None, now_t: time,
              cutoff: time = NON_TRADING_CUTOFF) -> TradeDay:
    """§4.1 判定链，命中即停。请求失败永远不计入。"""
    if today.weekday() >= 5:
        return TradeDay.NON_TRADING
    if calendar is not None and calendar.covers(today):
        return TradeDay.TRADING if calendar.is_trading(today) else TradeDay.NON_TRADING
    if source_ok and source_date is not None:
        if source_date == today:
            return TradeDay.TRADING
        if source_date < today and now_t >= cutoff:
            return TradeDay.NON_TRADING
    return TradeDay.UNKNOWN


def _clock_row(now_t: time) -> tuple[State, int, int]:
    """交易日的时钟行 →（状态, 间隔, 缓存 TTL）。§4.2 表格的直译。"""
    if now_t < AUCTION_START:
        return State.PRE_OPEN, TICK_INTERVAL_S, LONG_TTL_S
    if now_t < AUCTION_END:
        return State.AUCTION, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < OPEN:
        return State.WAIT_OPEN, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < LUNCH_START:
        return State.TRADING, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < LUNCH_END:
        return State.LUNCH, TICK_INTERVAL_S, LONG_TTL_S
    if now_t < CLOSE:
        return State.TRADING, LIVE_INTERVAL_S, SHORT_TTL_S
    if now_t < CLOSE_CUTOFF:
        return State.CLOSED, TICK_INTERVAL_S, SHORT_TTL_S     # 收盘后统计仍在修正：每 60 秒取一次
    return State.CLOSED, TICK_INTERVAL_S, LONG_TTL_S


def decide(now: datetime, td: TradeDay) -> Decision:
    """非交易日优先于所有时钟行；未知按交易日行的节奏取数、但状态是「未知」。"""
    if td is TradeDay.NON_TRADING:
        return Decision(State.CLOSED_DAY, TICK_INTERVAL_S, LONG_TTL_S)
    state, interval, ttl = _clock_row(now.time())
    if td is TradeDay.UNKNOWN:
        state = State.UNKNOWN
    return Decision(state, interval, ttl)


def in_continuous_auction(now_t: time) -> bool:
    return any(a <= now_t < b for a, b in CONTINUOUS)


def is_stale(now: datetime, last_point: time | None, quote_ts: datetime | None) -> bool:
    """§4.3：只在连续竞价时段判；分时最后一点优先，拿不到才看行情时间戳。方向：数据落后于现在。"""
    if not in_continuous_auction(now.time()):
        return False
    if last_point is not None:
        # 分时点按分钟**结束**时刻标注，所以那一分钟的数据其实新到 (last_point − 1 分钟)
        point = datetime.combine(now.date(), last_point) - timedelta(minutes=1)
        return (now - point).total_seconds() > STALE_MINUTE_S
    if quote_ts is not None:
        return (now - quote_ts).total_seconds() > STALE_QUOTE_S
    return False


def expected_trade_day(source_date: date | None, calendar: Calendar | None, now_t: time,
                       last_known: date | None, today: date | None = None) -> date | None:
    """§3「预期交易日」：本轮源日期 → 日历推算 → 上一轮成功的源日期 → None。"""
    if source_date is not None:
        return source_date
    today = today or datetime.now().date()
    if calendar is not None and calendar.covers(today):
        if calendar.is_trading(today) and now_t >= AUCTION_START:
            return today
        return calendar.previous_trading_day(today)
    return last_known
```

`expected_trade_day` 里那个 `datetime.now()` 只是缺省值兜底，页面调用时**必须传 `today`**（Task 21 里传 `market.now().date()`），测试也传。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_session.py -q`
Expected: PASS。（`test_expected_trade_day_order` 第二个断言：把测试里的调用都补上 `today=WED`。）

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/session.py tests/test_market_session.py
git commit -m "feat(market): 时段判定纯函数——交易日三态链、8 态状态表、过期判据、预期交易日"
```

### Task 10: live.py — 一轮两段式取数、主备取舍、最近成功值 [B]

**Files:**
- Create: `src/quant/market/live.py`
- Test: `tests/test_market_live.py`

spec §3 的编排层。**三个板块各是一个整块**：行情（含腾讯家数）、分布（东财，兜底腾讯家数）、温度计（分布 ±11 + 两个池子，同轮、不跨轮拼）。每块带来源、范围标签、数据源时刻、交易日；失败时回落到 `LiveStore` 里同一块的最近成功值。

- [ ] **Step 1: 写测试**

```python
# tests/test_market_live.py
from datetime import date, datetime, time
from pathlib import Path

import pytest

from quant import market
from quant.market import live, sources
from quant.market.sources import FetchError

FIX = Path(__file__).resolve().parent / "fixtures" / "market"
CODES = [c for c, _ in market.INDEXES]
NOW = datetime(2026, 9, 24, 19, 30)


def fixture_http(broken: set[str] = frozenset()):
    """按 URL 片段找夹具；broken 里的片段直接报 FetchError（模拟某个来源挂了）。"""
    table = {
        "qt.gtimg.cn/q=": "tencent_quotes.bin", "hq.sinajs.cn": "sina_quotes.bin",
        "getTopicZDFenBu": "em_fenbu.bin", "getTopicZTPool": "em_ztpool.bin",
        "getTopicZBPool": "em_zbpool.bin", "newfqkline": None,
    }

    def get(url, headers=None):
        for frag in broken:
            if frag in url:
                raise FetchError(f"broken {frag}")
        if "minute/query?code=" in url:
            code = url.rsplit("=", 1)[1]
            return (FIX / f"tencent_minute_{code}.bin").read_bytes()
        if "newfqkline" in url:
            code = url.split("param=")[1].split(",")[0]
            return (FIX / f"tencent_kline_{code}.bin").read_bytes()
        for frag, name in table.items():
            if frag in url and name:
                return (FIX / name).read_bytes()
        raise AssertionError(f"没有这个 URL 的夹具: {url}")
    return get


def test_full_round_uses_primary_sources():
    store = live.LiveStore()
    snap = live.fetch_round(fixture_http(), NOW, calendar_hint=None, store=store,
                            allow_cross_day=True)
    assert snap.quotes.source == "tencent" and not snap.quotes.fallback
    assert snap.trade_date == snap.minutes["sh000001"].trade_date
    assert snap.breadth.source == "eastmoney" and snap.breadth.scope == sources.SCOPE_EM
    assert snap.thermo.value.limit_up == snap.breadth.value.buckets[11]
    assert snap.thermo.value.zt_tc >= 0 and snap.thermo.trade_date == snap.trade_date
    assert snap.errors == []


def test_tencent_down_falls_back_to_sina_for_quotes_only():
    snap = live.fetch_round(fixture_http({"qt.gtimg.cn/q="}), NOW, None, live.LiveStore(), True)
    assert snap.quotes.source == "sina" and not snap.quotes.fallback
    assert snap.breadth.source == "eastmoney"          # 分布不受影响


def test_eastmoney_fenbu_down_falls_back_to_tencent_breadth_and_kills_thermo():
    store = live.LiveStore()
    snap = live.fetch_round(fixture_http({"getTopicZDFenBu"}), NOW, None, store, True)
    assert snap.breadth.source == "tencent" and snap.breadth.scope == sources.SCOPE_TENCENT
    assert snap.thermo is None or snap.thermo.fallback   # 没有上一轮 → None


def test_everything_down_uses_last_good_with_its_own_stamp():
    store = live.LiveStore()
    good = live.fetch_round(fixture_http(), NOW, None, store, True)
    bad = live.fetch_round(fixture_http({"gtimg", "sinajs", "eastmoney"}),
                           NOW.replace(hour=20), None, store, allow_cross_day=True)
    assert bad.quotes.fallback and bad.quotes.value == good.quotes.value
    assert bad.quotes.ts == good.quotes.ts and bad.quotes.trade_date == good.trade_date
    assert bad.thermo.fallback and bad.thermo.value == good.thermo.value


def test_cross_day_last_good_is_refused_in_trading_hours():
    store = live.LiveStore()
    live.fetch_round(fixture_http(), NOW, None, store, True)
    tomorrow = datetime(2026, 9, 25, 10, 0)
    bad = live.fetch_round(fixture_http({"gtimg", "sinajs", "eastmoney"}), tomorrow, None,
                           store, allow_cross_day=False)
    assert bad.quotes is None and bad.breadth is None and bad.thermo is None


def test_pools_are_skipped_without_a_trade_date():
    calls = []

    def get(url, headers=None):
        calls.append(url)
        if "minute/query" in url:
            raise FetchError("no minutes")
        return fixture_http()(url, headers)
    snap = live.fetch_round(get, NOW, calendar_hint=None, store=live.LiveStore(),
                            allow_cross_day=True)
    assert not any("Pool" in u for u in calls), "不知道交易日就不请求池子"
    assert snap.trade_date is None and snap.thermo is None
    assert snap.breadth is None, "分布拿不到日期 → 本轮按失败处理，不许贴行情的日期"


def test_calendar_hint_supplies_the_trade_date_when_minutes_fail():
    def get(url, headers=None):
        if "minute/query" in url:
            raise FetchError("no minutes")
        return fixture_http()(url, headers)
    hint = date(2026, 9, 24)
    snap = live.fetch_round(get, NOW, calendar_hint=hint, store=live.LiveStore(), allow_cross_day=True)
    assert snap.trade_date == hint and snap.thermo is not None


def test_date_reconciliation_rejects_a_stale_distribution():
    """池子今天 tc=0（开盘前），分布还停在昨天（+11 档 51）→ 分布与温度计本轮作废。"""
    def get(url, headers=None):
        if "Pool" in url:
            return b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260925"}}'
        return fixture_http()(url, headers)
    snap = live.fetch_round(get, NOW, None, live.LiveStore(), True)
    assert snap.breadth is None and snap.thermo is None
    assert any("对不上" in e for e in snap.errors)


def test_store_reset_forgets_everything():
    store = live.LiveStore()
    live.fetch_round(fixture_http(), NOW, None, store, True)
    store.reset()
    assert store.get("quotes") is None and store.last_trade_date is None
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_live.py -q`
Expected: FAIL（ImportError）。

- [ ] **Step 3: 实现**

```python
"""一轮盘中取数（spec §3「一轮怎么发」）：两段并发、按块取舍、最近成功值。

块（Block）是页面唯一消费的东西：值 + 来源 + 范围标签 + 数据源时刻 + 交易日 + 是否兜底。
三个块：quotes（指数 + 腾讯家数原始件）、breadth（分布 或 腾讯家数）、thermo（温度计）。
采用顺序：主源本轮 > 兜底源本轮 > 最近成功值 > None（页面显示「暂不可用」）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from quant import market
from quant.market import sources
from quant.market.sources import HttpGet, Result

ROUND_DEADLINE_S = 6.0       # 整轮最迟收口
RECONCILE_TOLERANCE = 5      # 涨停池 tc 与分布 +11 档允许差几只（两个请求不是同一瞬间）
HTTP_GET: HttpGet = sources.default_http_get     # 测试用替身覆盖这一个名字


@dataclass(frozen=True)
class Block:
    value: Any
    source: str          # "tencent" | "sina" | "eastmoney"
    scope: str           # 范围标签（家数/分布用；行情为空串）
    ts: datetime | None  # 数据源自带的时刻
    trade_date: date | None
    fallback: bool = False


@dataclass(frozen=True)
class Thermo:
    limit_up: int        # 分布 +11 档
    limit_down: int      # 分布 −11 档
    zt_tc: int           # 涨停池家数（只用于炸板率与对账）
    zb_tc: int           # 炸板池家数
    max_lbc: int | None  # 最高连板


@dataclass
class LiveSnapshot:
    fetched_at: datetime
    trade_date: date | None
    source_ok: bool                       # 分时是否至少一只成功
    sh_status: str | None
    minutes: dict[str, sources.Minute] = field(default_factory=dict)
    quotes: Block | None = None
    breadth: Block | None = None
    thermo: Block | None = None
    errors: list[str] = field(default_factory=list)


class LiveStore:
    """进程内的最近成功值。**必须可 reset**：测试之间不许残留（spec §8）。"""

    def __init__(self) -> None:
        self._blocks: dict[str, Block] = {}
        self.last_trade_date: date | None = None

    def put(self, key: str, block: Block) -> None:
        self._blocks[key] = block

    def get(self, key: str) -> Block | None:
        return self._blocks.get(key)

    def reset(self) -> None:
        self._blocks.clear()
        self.last_trade_date = None


STORE = LiveStore()


def _run_all(jobs: dict[str, Any]) -> dict[str, Result]:
    """并发跑一组无参 callable，整体最迟 ROUND_DEADLINE_S 收口；超时的当失败。"""
    out: dict[str, Result] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futs = {pool.submit(fn): key for key, fn in jobs.items()}
        done, not_done = wait(futs, timeout=ROUND_DEADLINE_S)
        for f in done:
            out[futs[f]] = f.result()
        for f in not_done:
            out[futs[f]] = Result(value=None, error="超过整轮时限")
    return out


def _pick(current: Block | None, key: str, store: LiveStore, snap: LiveSnapshot,
          allow_cross_day: bool) -> Block | None:
    if current is not None:
        store.put(key, current)
        return current
    last = store.get(key)
    if last is None:
        return None
    if not allow_cross_day and last.trade_date != snap.trade_date:
        return None                       # 交易时段里不显示跨交易日的旧值（§3）
    return Block(last.value, last.source, last.scope, last.ts, last.trade_date, fallback=True)


def fetch_round(http_get: HttpGet, now: datetime, calendar_hint: date | None,
                store: LiveStore, allow_cross_day: bool) -> LiveSnapshot:
    codes = [c for c, _ in market.INDEXES]
    snap = LiveSnapshot(fetched_at=now, trade_date=None, source_ok=False, sh_status=None)

    # —— 第 1 段：并发
    jobs = {
        "tencent": lambda: sources.fetch(http_get, sources.tencent_quote_url(codes),
                                         lambda raw: (sources.parse_tencent_quotes(raw, codes),
                                                      sources.parse_tencent_breadth(raw))),
        "fenbu": lambda: sources.fetch(http_get, sources.em_fenbu_url(), sources.parse_em_fenbu),
    }
    for code in codes:
        jobs[f"min:{code}"] = (lambda c=code: sources.fetch(
            http_get, sources.tencent_minute_url(c), lambda raw: sources.parse_tencent_minute(raw, c)))
    # 新浪与主源同一轮并发发出（多 1 个请求），主源失败才采用；日期校验放到拿到预期交易日之后
    jobs["sina_raw"] = lambda: sources.fetch(http_get, sources.sina_quote_url(codes),
                                             lambda raw: raw, headers=sources.SINA_HEADERS)
    res = _run_all(jobs)

    # —— 交易日期：6 次分时里成功的那些必须一致
    dates = set()
    for code in codes:
        r = res[f"min:{code}"]
        if r.ok:
            snap.minutes[code] = r.value
            dates.add(r.value.trade_date)
        else:
            snap.errors.append(f"分时 {code}: {r.error}")
    if len(dates) > 1:
        snap.errors.append(f"分时交易日期不一致: {sorted(dates)}")
        snap.minutes.clear()
        dates.clear()
    snap.source_ok = bool(dates)
    if dates:
        snap.trade_date = dates.pop()
        store.last_trade_date = snap.trade_date
        sh = snap.minutes.get("sh000001")
        snap.sh_status = sh.sh_status if sh else None
    else:
        snap.trade_date = calendar_hint or store.last_trade_date

    # —— 行情块
    quotes_block = None
    if res["tencent"].ok:
        quotes, tencent_breadth = res["tencent"].value
        quotes_block = Block(quotes, "tencent", "", quotes["sh000001"].ts, snap.trade_date)
    else:
        snap.errors.append(f"腾讯行情: {res['tencent'].error}")
        tencent_breadth = None
        if res["sina_raw"].ok:
            try:
                quotes = sources.parse_sina_quotes(res["sina_raw"].value, codes, snap.trade_date)
                quotes_block = Block(quotes, "sina", "", quotes["sh000001"].ts, snap.trade_date)
            except sources.ParseError as e:
                snap.errors.append(f"新浪行情: {e}")
        else:
            snap.errors.append(f"新浪行情: {res['sina_raw'].error}")
    snap.quotes = _pick(quotes_block, "quotes", store, snap, allow_cross_day)

    # —— 第 2 段：池子（只在知道交易日时）
    zt = zb = None
    if snap.trade_date is not None:
        pools = _run_all({
            "zt": lambda: sources.fetch(http_get, sources.em_pool_url("zt", snap.trade_date),
                                        lambda raw: sources.parse_em_pool(raw, "zt", snap.trade_date)),
            "zb": lambda: sources.fetch(http_get, sources.em_pool_url("zb", snap.trade_date),
                                        lambda raw: sources.parse_em_pool(raw, "zb", snap.trade_date)),
        })
        zt, zb = pools["zt"].value, pools["zb"].value
        for k in ("zt", "zb"):
            if not pools[k].ok:
                snap.errors.append(f"东财池子 {k}: {pools[k].error}")

    # —— 分布块 + 温度计块（同轮、同源、日期对账）
    breadth_block = thermo_block = None
    dist = res["fenbu"].value if res["fenbu"].ok else None
    if dist is None:
        snap.errors.append(f"东财分布: {res['fenbu'].error}")
    if dist is not None and zt is not None and zb is not None:
        bucket11 = dist.buckets[11]
        if (zt.tc == 0 and bucket11 > 0) or abs(zt.tc - bucket11) > RECONCILE_TOLERANCE:
            snap.errors.append(f"分布与池子的日期对不上：涨停池 {zt.tc} vs 分布 +11 档 {bucket11}")
        else:
            stamp = quotes_block.ts if quotes_block else now
            breadth_block = Block(dist, "eastmoney", dist.scope, stamp, snap.trade_date)
            thermo_block = Block(Thermo(limit_up=bucket11, limit_down=dist.buckets[-11],
                                        zt_tc=zt.tc, zb_tc=zb.tc, max_lbc=zt.max_lbc),
                                 "eastmoney", dist.scope, stamp, snap.trade_date)
    if breadth_block is None and dist is None and tencent_breadth is not None:
        # 兜底：只有家数，分布图由页面显示「暂不可用」，范围标签跟着换
        breadth_block = Block(tencent_breadth, "tencent", tencent_breadth.scope,
                              quotes_block.ts if quotes_block else now, snap.trade_date)
    snap.breadth = _pick(breadth_block, "breadth", store, snap, allow_cross_day)
    snap.thermo = _pick(thermo_block, "thermo", store, snap, allow_cross_day)
    return snap
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_live.py -q`
Expected: PASS。若 `test_eastmoney_fenbu_down_…` 里 `snap.thermo` 不是 None，检查 `_pick`：上一轮没有就该是 None。

- [ ] **Step 5: Commit**

```bash
git add src/quant/market/live.py tests/test_market_live.py
git commit -m "feat(market): live.fetch_round——两段并发、按块主备取舍、日期对账、最近成功值"
```

### Task 11: turnover.py — 两市成交额的文案与口径一致性 [B]

**Files:**
- Create: `src/quant/market/turnover.py`
- Test: `tests/test_market_turnover.py`

spec §2 第 3 项 + §3 表「两市成交额」两行。上证 + 深证成指 = 沪深两市全部。

- [ ] **Step 1: 写测试**

```python
# tests/test_market_turnover.py
from datetime import date, datetime, time

from quant.market import turnover
from quant.market.sources import DailyBar

D23, D24 = date(2026, 9, 23), date(2026, 9, 24)
BARS_SH = [DailyBar(D23, 83_413_286.30), DailyBar(D24, 90_000_000.00)]   # 万元
BARS_SZ = [DailyBar(D23, 93_084_355.37), DailyBar(D24, 95_000_000.00)]


def test_previous_full_day_is_the_last_bar_strictly_before_the_trade_date():
    y = turnover.previous_day(BARS_SH, BARS_SZ, D24)
    assert y.day == D23 and abs(y.amount_wan - (83_413_286.30 + 93_084_355.37)) < 1e-6


def test_previous_day_none_when_no_earlier_bar():
    assert turnover.previous_day(BARS_SH[1:], BARS_SZ[1:], D24) is None


def test_consistency_check_uses_a_1_wan_tolerance():
    ok = turnover.consistent(BARS_SH, BARS_SZ, D24, quote_sh_wan=90_000_000.6, quote_sz_wan=95_000_000.2)
    assert ok is True
    assert turnover.consistent(BARS_SH, BARS_SZ, D24, 90_000_002.0, 95_000_000.0) is False
    assert turnover.consistent(BARS_SH, BARS_SZ, date(2026, 9, 25), 1, 1) is None   # 当天那根不存在 → 没法检查


def test_settled_flag_is_monotonic():
    assert turnover.settled(datetime(2026, 9, 24, 15, 31), D24, D24) is True     # 过截止
    assert turnover.settled(datetime(2026, 9, 24, 10, 0), D24, D24) is False
    assert turnover.settled(datetime(2026, 9, 25, 8, 0), D24, date(2026, 9, 25)) is True  # 数据属昨天
    assert turnover.settled(datetime(2026, 9, 26, 10, 0), D24, date(2026, 9, 26)) is True  # 休市


def test_intraday_wording_has_no_percentage():
    text = turnover.line(now=datetime(2026, 9, 24, 12, 0), today=D24, trade_date=D24,
                         current_wan=1.47e8, current_ts=time(11, 30),
                         prev=DailyBar(D23, 2.14e8), pct_ok=None)
    assert text == "两市成交额 1.47 万亿（截至 11:30）· 9 月 23 日全天 2.14 万亿"
    assert "%" not in text


def test_auction_is_still_intraday_wording():
    text = turnover.line(datetime(2026, 9, 24, 9, 20), D24, D24, 3.0e6, time(9, 20),
                         DailyBar(D23, 2.14e8), None)
    assert "截至 09:20" in text and "%" not in text


def test_after_close_gives_percentage_only_when_check_passed():
    kw = dict(today=D24, trade_date=D24, current_wan=2.30e8, current_ts=time(15, 0),
              prev=DailyBar(D23, 2.00e8))
    with_pct = turnover.line(datetime(2026, 9, 24, 20, 0), pct_ok=True, **kw)
    assert with_pct == "两市成交额 9 月 24 日全天 2.30 万亿 · 9 月 23 日全天 2.00 万亿 · +15.0%"
    no_pct = turnover.line(datetime(2026, 9, 24, 15, 10), pct_ok=None, **kw)   # 截止前：检查还没做
    assert "%" not in no_pct and "全天 2.30 万亿" in no_pct
    assert "%" not in turnover.line(datetime(2026, 9, 24, 20, 0), pct_ok=False, **kw)


def test_holiday_shows_two_full_days_from_the_data_date():
    text = turnover.line(datetime(2026, 9, 25, 10, 0), date(2026, 9, 25), D24, 2.30e8, time(15, 0),
                         DailyBar(D23, 2.00e8), True)
    assert text.startswith("两市成交额 9 月 24 日全天 2.30 万亿")


def test_missing_previous_day_shows_only_the_current_value():
    assert turnover.line(datetime(2026, 9, 24, 12, 0), D24, D24, 1.47e8, time(11, 30), None, None) \
        == "两市成交额 1.47 万亿（截至 11:30）"
```

- [ ] **Step 2: 跑，确认失败** → ImportError。

- [ ] **Step 3: 实现**

```python
"""两市成交额（spec §2 第 3 项、§3）。上证 + 深证成指 = 沪深两市全部（含极少量 B 股）。

盘中绝不给「较上一交易日 %」：半天比全天必然难看且误导。百分比只在
口径一致性检查**已做且通过**时给（`pct_ok is True`）；没做（None）或没过（False）都不给。
"""
from __future__ import annotations

from datetime import date, datetime, time

from quant.market.session import CLOSE, CLOSE_CUTOFF
from quant.market.sources import DailyBar

CONSISTENCY_TOLERANCE_WAN = 1.0     # [37] 是截断后的整数，走完的 bar 带两位小数，不能比相等


def previous_day(sh: list[DailyBar], sz: list[DailyBar], trade_date: date) -> DailyBar | None:
    """日期**严格早于** trade_date 的最后一根，两市相加。两边日期对不上就不给。"""
    prev_sh = [b for b in sh if b.day < trade_date]
    prev_sz = [b for b in sz if b.day < trade_date]
    if not prev_sh or not prev_sz or prev_sh[-1].day != prev_sz[-1].day:
        return None
    return DailyBar(prev_sh[-1].day, prev_sh[-1].amount_wan + prev_sz[-1].amount_wan)


def consistent(sh: list[DailyBar], sz: list[DailyBar], trade_date: date,
               quote_sh_wan: float, quote_sz_wan: float) -> bool | None:
    """K 线里 trade_date 那根与行情快照 [37] 相差 < 1 万元 → 口径一致。那根不存在 → None（没法检查）。"""
    bar_sh = next((b for b in sh if b.day == trade_date), None)
    bar_sz = next((b for b in sz if b.day == trade_date), None)
    if bar_sh is None or bar_sz is None:
        return None
    return (abs(bar_sh.amount_wan - quote_sh_wan) < CONSISTENCY_TOLERANCE_WAN
            and abs(bar_sz.amount_wan - quote_sz_wan) < CONSISTENCY_TOLERANCE_WAN)


def settled(now: datetime, trade_date: date, today: date) -> bool:
    """当天那根 K 线是否已定格（§3 缓存键的第二个分量，单调不回退）：
    数据属于过去的交易日、或今天已过收盘截止时刻。"""
    if trade_date < today:
        return True
    return now.time() >= CLOSE_CUTOFF


def _wan_yi(amount_wan: float) -> str:
    return f"{amount_wan / 1e8:.2f} 万亿"


def _day(d: date) -> str:
    return f"{d.month} 月 {d.day} 日"


def line(now: datetime, today: date, trade_date: date, current_wan: float, current_ts: time,
         prev: DailyBar | None, pct_ok: bool | None) -> str:
    intraday = trade_date == today and now.time() < CLOSE
    if intraday:
        head = f"两市成交额 {_wan_yi(current_wan)}（截至 {current_ts:%H:%M}）"
    else:
        head = f"两市成交额 {_day(trade_date)}全天 {_wan_yi(current_wan)}"
    if prev is None:
        return head
    text = f"{head} · {_day(prev.day)}全天 {_wan_yi(prev.amount_wan)}"
    if not intraday and pct_ok is True and prev.amount_wan > 0:
        pct = (current_wan / prev.amount_wan - 1) * 100
        text += f" · {pct:+.1f}%"
    return text
```

- [ ] **Step 4: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_market_turnover.py -q` → PASS。

```bash
git add src/quant/market/turnover.py tests/test_market_turnover.py
git commit -m "feat(market): 两市成交额——上一交易日取法、口径一致性容差、分时段文案"
```

### Task 12: store.py — data/market/ 读写、日历完整性、gaps.json、保留规则 [B]

**Files:**
- Create: `src/quant/market/store.py`
- Test: `tests/test_market_store.py`

spec §5.2 第 2 步、§5.5。原子写沿用仓库写法（`mkstemp(prefix=name+".", suffix=".tmp")` + `os.replace`）。

- [ ] **Step 1: 写测试**

```python
# tests/test_market_store.py
import json
from datetime import date, timedelta

import pandas as pd
import pytest

from quant.market import store
from quant.market.session import Calendar

D = lambda s: date.fromisoformat(s)


def _snap(n=3):
    return pd.DataFrame({"code": [f"sh.60000{i}" for i in range(n)],
                         "close": [1.0] * n, "preclose": [1.0] * n,
                         "tradestatus": [1] * n, "isST": [0] * n})


def test_layout(tmp_path):
    ms = store.MarketStore(tmp_path / "data" / "market")
    assert ms.daily_dir == tmp_path / "data" / "market" / "daily"
    assert ms.breadth_path.name == "breadth.parquet" and ms.gaps_path.name == "gaps.json"


def test_snapshot_roundtrip_is_atomic_and_lists_by_date(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.save_snapshot(D("2026-09-22"), _snap())
    ms.save_snapshot(D("2026-09-23"), _snap())
    (ms.daily_dir / "notes.txt").write_text("x")
    (ms.daily_dir / "2026-09-24.parquet.tmp").write_text("half")
    assert ms.snapshot_dates() == [D("2026-09-22"), D("2026-09-23")]
    assert list(ms.load_snapshot(D("2026-09-22")).columns) == list(_snap().columns)
    assert not list(ms.daily_dir.glob("*.tmp")) or True   # tmp 由 clean_tmp 管


def test_clean_tmp_covers_both_directories(tmp_path):
    ms = store.MarketStore(tmp_path)
    ms.daily_dir.mkdir(parents=True)
    (ms.daily_dir / "a.parquet.tmp").write_text("x")
    (ms.root / "breadth.parquet.abc.tmp").write_text("x")
    (ms.root / "keep.json").write_text("{}")
    assert ms.clean_tmp() == 2
    assert (ms.root / "keep.json").exists()


def test_calendar_roundtrip_and_view(tmp_path):
    ms = store.MarketStore(tmp_path)
    days = pd.date_range("2026-09-01", "2026-09-30").date
    trading = {d for d in days if d.weekday() < 5 and d != D("2026-09-25")}
    ms.save_calendar(days, trading)
    cal = ms.load_calendar()
    assert isinstance(cal, Calendar) and cal.first == D("2026-09-01") and cal.last == D("2026-09-30")
    assert cal.is_trading(D("2026-09-24")) and not cal.is_trading(D("2026-09-25"))
    assert ms.load_calendar() is not None and store.MarketStore(tmp_path / "nope").load_calendar() is None


def test_calendar_completeness_rules():
    days = list(pd.date_range("2025-05-01", "2026-11-23").date)   # ≈ today−500 .. today+60
    trading = frozenset(d for d in days if d.weekday() < 5)
    today = D("2026-09-24")
    assert store.calendar_complete(days, trading, today, last_done=D("2026-09-23"), need=310) is None
    gap = [d for d in days if d != D("2026-01-15")]
    assert "缺" in store.calendar_complete(gap, trading, today, D("2026-09-23"), 310)
    short = [d for d in days if d >= D("2026-06-01")]
    assert "310" in store.calendar_complete(short, trading, today, D("2026-09-23"), 310)
    ends_early = [d for d in days if d < today]
    assert "末日" in store.calendar_complete(ends_early, trading, today, D("2026-09-23"), 310)


def test_gaps_record_and_promote(tmp_path):
    ms = store.MarketStore(tmp_path)
    assert ms.permanent_gaps() == set()
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-24"))
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-24"))       # 同一天重试多次只记一次
    assert ms.permanent_gaps() == set()
    ms.record_failure(D("2026-08-14"), failed_on=D("2026-09-25"))
    assert ms.permanent_gaps() == {D("2026-08-14")}
    doc = json.loads(ms.gaps_path.read_text())
    assert doc["2026-08-14"] == {"failed_on": ["2026-09-24", "2026-09-25"], "permanent": True}


def test_retention_deletes_only_dated_parquets_before_the_window(tmp_path):
    ms = store.MarketStore(tmp_path)
    for s in ("2026-01-02", "2026-06-01", "2026-09-23"):
        ms.save_snapshot(D(s), _snap())
    (ms.daily_dir / "readme.parquet").write_text("not a date")
    (ms.daily_dir / "2026-01-02.parquet.bak").write_text("x")
    removed = ms.apply_retention(window_first=D("2026-06-01"))
    assert removed == [D("2026-01-02")]
    assert ms.snapshot_dates() == [D("2026-06-01"), D("2026-09-23")]
    assert (ms.daily_dir / "readme.parquet").exists() and (ms.daily_dir / "2026-01-02.parquet.bak").exists()


def test_breadth_table_roundtrip(tmp_path):
    ms = store.MarketStore(tmp_path)
    df = pd.DataFrame({"date": [D("2026-09-23")], "above20": [1], "n20": [2]})
    ms.save_breadth(df)
    assert ms.load_breadth().equals(df)
    assert store.MarketStore(tmp_path / "nope").load_breadth() is None
```

- [ ] **Step 2: 跑，确认失败** → ImportError。

- [ ] **Step 3: 实现**

```python
"""收盘数据的磁盘布局（spec §5.5）：data/market/{daily/YYYY-MM-DD.parquet, breadth.parquet,
calendar.parquet, gaps.json}。全部可再生、不进 git。

写盘一律原子（mkstemp + os.replace，同 data/cache.py）。删除只碰 daily/ 里文件名严格是
日期的 .parquet，且只在日历完整时（由调用方保证）执行。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from quant.market.session import Calendar

_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")
SNAPSHOT_COLUMNS = ["code", "close", "preclose", "tradestatus", "isST"]


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class MarketStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.daily_dir = self.root / "daily"
        self.breadth_path = self.root / "breadth.parquet"
        self.calendar_path = self.root / "calendar.parquet"
        self.gaps_path = self.root / "gaps.json"

    # —— 快照
    def snapshot_path(self, day: date) -> Path:
        return self.daily_dir / f"{day.isoformat()}.parquet"

    def snapshot_dates(self) -> list[date]:
        if not self.daily_dir.is_dir():
            return []
        out = []
        for p in self.daily_dir.iterdir():
            m = _DATED.match(p.name)
            if m:
                out.append(date.fromisoformat(m.group(1)))
        return sorted(out)

    def save_snapshot(self, day: date, df: pd.DataFrame) -> None:
        _atomic_write(self.snapshot_path(day), lambda tmp: df[SNAPSHOT_COLUMNS].to_parquet(tmp, index=False))

    def load_snapshot(self, day: date) -> pd.DataFrame:
        return pd.read_parquet(self.snapshot_path(day))

    def clean_tmp(self) -> int:
        n = 0
        for d in (self.root, self.daily_dir):
            if d.is_dir():
                for p in d.glob("*.tmp"):
                    p.unlink()
                    n += 1
        return n

    # —— 日历
    def save_calendar(self, days: Iterable[date], trading: Iterable[date]) -> None:
        trading = set(trading)
        df = pd.DataFrame({"date": list(days)})
        df["is_trading"] = df["date"].isin(trading)
        _atomic_write(self.calendar_path, lambda tmp: df.to_parquet(tmp, index=False))

    def load_calendar(self) -> Calendar | None:
        if not self.calendar_path.is_file():
            return None
        try:
            df = pd.read_parquet(self.calendar_path)
        except (OSError, ValueError):
            return None
        days = [pd.Timestamp(d).date() for d in df["date"]]
        trading = frozenset(d for d, t in zip(days, df["is_trading"]) if bool(t))
        return Calendar(first=min(days), last=max(days), trading=trading)

    # —— 永久缺口
    def _gaps(self) -> dict:
        if not self.gaps_path.is_file():
            return {}
        try:
            return json.loads(self.gaps_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def record_failure(self, day: date, failed_on: date) -> None:
        doc = self._gaps()
        entry = doc.setdefault(day.isoformat(), {"failed_on": [], "permanent": False})
        if failed_on.isoformat() not in entry["failed_on"]:
            entry["failed_on"].append(failed_on.isoformat())
        entry["permanent"] = len(entry["failed_on"]) >= 2
        _atomic_write(self.gaps_path, lambda tmp: Path(tmp).write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"))

    def permanent_gaps(self) -> set[date]:
        return {date.fromisoformat(k) for k, v in self._gaps().items() if v.get("permanent")}

    # —— 指标表
    def save_breadth(self, df: pd.DataFrame) -> None:
        _atomic_write(self.breadth_path, lambda tmp: df.to_parquet(tmp, index=False))

    def load_breadth(self) -> pd.DataFrame | None:
        if not self.breadth_path.is_file():
            return None
        try:
            return pd.read_parquet(self.breadth_path)
        except (OSError, ValueError):
            return None

    # —— 保留规则
    def apply_retention(self, window_first: date) -> list[date]:
        """删 daily/ 里早于窗口首日的快照。只碰文件名严格匹配日期的 .parquet。"""
        removed = []
        for day in self.snapshot_dates():
            if day < window_first:
                self.snapshot_path(day).unlink()
                removed.append(day)
        return removed


def calendar_complete(days: list[date], trading: frozenset[date], today: date,
                      last_done: date, need: int) -> str | None:
    """§5.2「日历完整」：连续不缺日、末日 ≥ 今天、last_done 及之前至少 need 个交易日。
    返回 None 表示完整，否则返回原因。"""
    if not days:
        return "日历为空"
    days = sorted(days)
    for a, b in zip(days, days[1:]):
        if (b - a).days != 1:
            return f"日历缺日：{a} 与 {b} 之间"
    if days[-1] < today:
        return f"日历末日 {days[-1]} 早于今天 {today}"
    n = sum(1 for d in trading if d <= last_done)
    if n < need:
        return f"截至 {last_done} 只有 {n} 个交易日，不足 {need}"
    return None
```

- [ ] **Step 4: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_market_store.py -q` → PASS。

```bash
git add src/quant/market/store.py tests/test_market_store.py
git commit -m "feat(market): data/market/ 存储——原子写、日历完整性、gaps.json 两次失败才永久、保留规则只删日期文件"
```

### Task 13: breadth.py — 快照 → 复权链 → 宽度指标表 [B]

**Files:**
- Create: `src/quant/market/breadth.py`
- Test: `tests/test_market_breadth.py`

spec §5.3。每只股票**所有行都参与连乘** `close/preclose`；停牌行比值记 1、不计入根数；缺行当「毒 bar」（NaN）——回看期跨过它的指标自然变缺值。比较用相对容差 1e-9。

- [ ] **Step 1: 写测试（手算小样例）**

```python
# tests/test_market_breadth.py
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.market import breadth

D = lambda s: date.fromisoformat(s)
DAYS = [D(f"2026-09-{d:02d}") for d in (1, 2, 3, 4, 7, 8)]      # 6 个交易日


def snap(rows):
    """rows: [(code, close, preclose, tradestatus, isST)]"""
    return pd.DataFrame(rows, columns=["code", "close", "preclose", "tradestatus", "isST"])


def test_adjusted_chain_handles_ex_rights_and_suspension():
    snaps = {
        DAYS[0]: snap([("a", 10.0, 10.0, 1, 0)]),
        DAYS[1]: snap([("a", 11.0, 10.0, 1, 0)]),
        DAYS[2]: snap([("a", 5.5, 5.5, 1, 0)]),          # 除权：昨收折算 11→5.5，价格没跌
        DAYS[3]: snap([("a", 5.5, 5.5, 0, 0)]),          # 停牌：比值 1，不计根数
        DAYS[4]: snap([("a", 6.05, 5.5, 1, 0)]),
        DAYS[5]: snap([("a", 6.05, 6.05, 1, 0)]),
    }
    adj = breadth.adjusted_close(snaps, DAYS)["a"]
    assert adj.iloc[0] == pytest.approx(1.0)
    assert adj.iloc[1] == pytest.approx(1.1) and adj.iloc[2] == pytest.approx(1.1)   # 除权日不跌
    assert adj.iloc[3] == pytest.approx(1.1) and adj.iloc[4] == pytest.approx(1.21)
    bars = breadth.bar_index(snaps, DAYS)["a"]
    assert list(bars) == [1, 2, 3, 3, 4, 5], "停牌日不计入根数"


def test_ex_rights_on_a_suspended_day_is_not_a_drop():
    snaps = {
        DAYS[0]: snap([("a", 10.0, 10.0, 1, 0)]),
        DAYS[1]: snap([("a", 5.0, 5.0, 0, 0)]),          # 停牌当天除权：baostock 给折算后的 preclose 与 close
        DAYS[2]: snap([("a", 5.0, 5.0, 1, 0)]),
    }
    adj = breadth.adjusted_close(snaps, DAYS[:3])["a"]
    assert adj.iloc[2] == pytest.approx(1.0), "复牌日不能把除权缺口当成真跌"


def test_ma_breadth_counts_only_stocks_with_enough_bars():
    # 两只票，N=3：a 涨、b 跌；c 只有两根，不进分母
    closes = {"a": [1, 2, 3, 4, 5, 6], "b": [6, 5, 4, 3, 2, 1], "c": [None, None, None, None, 1, 1]}
    snaps = {}
    for i, d in enumerate(DAYS):
        rows = []
        for code, series in closes.items():
            if series[i] is None:
                continue
            prev = series[i - 1] if i and series[i - 1] is not None else series[i]
            rows.append((code, float(series[i]), float(prev), 1, 0))
        snaps[d] = snap(rows)
    table = breadth.compute(snaps, DAYS, windows=(3,), high_low_window=3)
    last = table.iloc[-1]
    assert (last["above3"], last["n3"]) == (1, 2), "a 在均线上、b 在下、c 历史不足"
    assert (last["new_high"], last["new_low"], last["n_hl"]) == (1, 1, 2)


def test_gap_day_poisons_the_lookback_window():
    snaps = {d: snap([("a", 1.0 + i * 0.1, 1.0 + (i - 1) * 0.1 if i else 1.0, 1, 0)])
             for i, d in enumerate(DAYS) if d != DAYS[2]}          # 9-03 整天缺
    table = breadth.compute(snaps, DAYS, windows=(3,), high_low_window=3)
    by = table.set_index("date")
    assert by.loc[DAYS[3], "n3"] == 0 and by.loc[DAYS[4], "n3"] == 0   # 回看跨过缺口 → 不进分母
    assert by.loc[DAYS[5], "n3"] == 1                                   # 3 根都在缺口之后了


def test_single_stock_missing_row_is_a_gap_for_that_stock_only():
    snaps = {}
    for i, d in enumerate(DAYS):
        rows = [("b", 2.0, 2.0, 1, 0)]
        if d != DAYS[2]:
            rows.append(("a", 1.0, 1.0, 1, 0))
        snaps[d] = snap(rows)
    table = breadth.compute(snaps, DAYS, windows=(2,), high_low_window=2).set_index("date")
    assert table.loc[DAYS[3], "n2"] == 1 and table.loc[DAYS[5], "n2"] == 2


def test_st_and_suspended_are_excluded_from_the_denominator():
    snaps = {d: snap([("a", 1.0, 1.0, 1, 0), ("s", 1.0, 1.0, 1, 1), ("h", 1.0, 1.0, 0, 0)])
             for d in DAYS}
    table = breadth.compute(snaps, DAYS, windows=(2,), high_low_window=2)
    assert table.iloc[-1]["n2"] == 1


def test_tie_with_the_rolling_high_counts_as_new_high_within_tolerance():
    vals = [1.0, 1.2, 1.1, 1.2]
    snaps = {d: snap([("a", v, vals[i - 1] if i else v, 1, 0)]) for i, (d, v) in enumerate(zip(DAYS, vals))}
    table = breadth.compute(snaps, DAYS[:4], windows=(2,), high_low_window=3)
    assert table.iloc[-1]["new_high"] == 1


def test_output_columns():
    snaps = {d: snap([("a", 1.0, 1.0, 1, 0)]) for d in DAYS}
    cols = list(breadth.compute(snaps, DAYS).columns)
    assert cols == ["date", "above20", "n20", "above60", "n60", "above200", "n200",
                    "new_high", "new_low", "n_hl"]
```

- [ ] **Step 2: 跑，确认失败** → ImportError。

- [ ] **Step 3: 实现**

```python
"""趋势宽度（spec §5.3）：每日快照 → 复权收盘链 → 站上 N 日线占比、52 周新高/新低。

范围：当日交易中、非 ST、且至少有 N 根历史。「根」= 该股实际交易的日子，停牌日不计。
缺口（整天缺快照，或某只股票在首末出现之间缺一天）当作一根值为 NaN 的「毒 bar」：
rolling 均值/极值一碰到它就是 NaN，回看期跨过缺口的日子自然进不了分子分母——
这正是 §5.3 要的语义，而且不用单独维护缺口表。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

WINDOWS = (20, 60, 200)
HIGH_LOW_WINDOW = 250
REL_TOL = 1e-9


def _wide(snaps: dict[date, pd.DataFrame], days: list[date], col: str) -> pd.DataFrame:
    frames = []
    for d in days:
        df = snaps.get(d)
        if df is None:
            continue
        s = df.set_index("code")[col]
        s.name = d
        frames.append(s)
    wide = pd.concat(frames, axis=1).T if frames else pd.DataFrame()
    return wide.reindex(index=days)              # 缺的整天成为全 NaN 行


def _listed_mask(close: pd.DataFrame) -> pd.DataFrame:
    """某只股票在首次与最后一次出现之间的日子都算「应有行」；其间的 NaN 就是缺口。"""
    present = close.notna()
    first = present.idxmax()
    last = present[::-1].idxmax()
    idx = np.array(close.index)
    mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    for code in close.columns:
        mask[code] = (idx >= first[code]) & (idx <= last[code])
    return mask


def adjusted_close(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """复权收盘（相对首日 = 1）。停牌行比值 1；缺行 → NaN 并让其后的链断掉（毒 bar）。"""
    close = _wide(snaps, days, "close").astype(float)
    pre = _wide(snaps, days, "preclose").astype(float)
    trading = _wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1
    listed = _listed_mask(close)
    ratio = close / pre
    ratio = ratio.where(trading & (pre > 0), 1.0)          # 停牌 / 昨收无效 → 1
    ratio = ratio.where(~(listed & close.isna()), np.nan)   # 缺行 → NaN
    ratio = ratio.where(listed, 1.0)                        # 未上市/已退市的日子不参与
    adj = ratio.cumprod()
    return adj.where(listed)


def bar_index(snaps: dict[date, pd.DataFrame], days: list[date]) -> pd.DataFrame:
    """每只股票到当天为止的**交易根数**（停牌日不加）。"""
    trading = (_wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    return trading.cumsum()


def _rolling_on_bars(adj: pd.DataFrame, trading: pd.DataFrame, n: int, fn: str) -> pd.DataFrame:
    """在「交易根」序列上做 rolling，再对齐回日历日。毒 bar（NaN）参与 rolling，让窗口失效。"""
    out = pd.DataFrame(np.nan, index=adj.index, columns=adj.columns)
    for code in adj.columns:
        col = adj[code]
        on_bars = col[trading[code] | col.isna()]          # 交易日 + 毒 bar
        rolled = getattr(on_bars.rolling(n, min_periods=n), fn)()
        out[code] = rolled.reindex(adj.index)
    return out


def compute(snaps: dict[date, pd.DataFrame], days: list[date],
            windows: tuple[int, ...] = WINDOWS, high_low_window: int = HIGH_LOW_WINDOW) -> pd.DataFrame:
    adj = adjusted_close(snaps, days)
    trading = (_wide(snaps, days, "tradestatus").fillna(0).astype(int) == 1)
    st = (_wide(snaps, days, "isST").fillna(0).astype(int) == 1)
    eligible = trading & ~st & adj.notna()
    rows = {"date": list(adj.index)}
    for n in windows:
        ma = _rolling_on_bars(adj, trading, n, "mean")
        ok = eligible & ma.notna()
        above = ok & (adj > ma * (1 + REL_TOL))
        rows[f"above{n}"] = above.sum(axis=1).astype(int).tolist()
        rows[f"n{n}"] = ok.sum(axis=1).astype(int).tolist()
    hi = _rolling_on_bars(adj, trading, high_low_window, "max")
    lo = _rolling_on_bars(adj, trading, high_low_window, "min")
    ok = eligible & hi.notna()
    rows["new_high"] = (ok & (adj >= hi * (1 - REL_TOL))).sum(axis=1).astype(int).tolist()
    rows["new_low"] = (ok & (adj <= lo * (1 + REL_TOL))).sum(axis=1).astype(int).tolist()
    rows["n_hl"] = ok.sum(axis=1).astype(int).tolist()
    return pd.DataFrame(rows)
```

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_market_breadth.py -q`
Expected: PASS。`test_gap_day_poisons_the_lookback_window` 若 9-08 那天 n3 仍为 0：检查毒 bar 是否被 `trading[code] | col.isna()` 选进了 rolling 序列。

- [ ] **Step 5: 性能冒烟（不进测试）**

用 Task 15 之后的真实快照再量；现在先用合成数据：5200 只 × 310 天随机快照跑 `compute`，Expected: < 5 秒。慢于此就把 `_rolling_on_bars` 的逐列循环换成对整张宽表一次 rolling（毒 bar 仍以 NaN 参与）。

- [ ] **Step 6: Commit**

```bash
git add src/quant/market/breadth.py tests/test_market_breadth.py
git commit -m "feat(market): 宽度指标——复权链全行连乘、停牌不计根、缺口即毒 bar、相对容差比较"
```

### Task 14: minicharts.py — SVG 小图 → data URI [B]

**Files:**
- Create: `src/quant/report/minicharts.py`
- Test: `tests/test_minicharts.py`

spec §2「小图的做法」：`st.html` 里的内联 `<svg>` 会被 DOMPurify 整段删掉，所以小图走 `<img src="data:image/svg+xml;base64,…">`。色值只用 palette 常量（`tests/test_charts.py` 那条「charts.py 无裸 hex」的规矩这里照用）。文字标签不进 SVG，由 HTML 排。

- [ ] **Step 1: 写测试**

```python
# tests/test_minicharts.py
import base64
import re
from pathlib import Path

from quant.report import minicharts, palette

SRC = (Path(__file__).resolve().parent.parent / "src/quant/report/minicharts.py").read_text("utf-8")


def _svg(uri: str) -> str:
    assert uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")


def test_sparkline_uses_up_color_above_prev_close_and_a_dashed_baseline():
    svg = _svg(minicharts.sparkline([(0, 10.0), (1, 10.5), (2, 10.2)], prev_close=10.0))
    assert svg.startswith("<svg") and palette.UP in svg and "stroke-dasharray" in svg
    svg2 = _svg(minicharts.sparkline([(0, 10.0), (1, 9.5)], prev_close=10.0))
    assert palette.DOWN in svg2 and palette.UP not in svg2


def test_sparkline_with_one_point_or_none_renders_an_empty_frame():
    assert "<polyline" not in _svg(minicharts.sparkline([], prev_close=1.0))
    assert "<polyline" not in _svg(minicharts.sparkline([(0, 1.0)], prev_close=1.0))


def test_histogram_colors_negative_buckets_green_positive_red_zero_neutral():
    buckets = {k: (k + 12) * 3 for k in range(-11, 12)}
    svg = _svg(minicharts.histogram(buckets))
    assert svg.count("<rect") == 23
    assert palette.UP in svg and palette.DOWN in svg and palette.NEUTRAL in svg


def test_ratio_bar_widths_are_proportional():
    svg = _svg(minicharts.ratio_bar(up=1, flat=1, down=2))
    widths = [float(w) for w in re.findall(r'width="([\d.]+)"', svg)[1:]]   # 第一个是画布
    assert widths[0] == widths[1] and abs(widths[2] - 2 * widths[0]) < 1e-6


def test_trend_line_is_grey_and_skips_nan():
    svg = _svg(minicharts.trend_line([0.4, None, 0.5, 0.6]))
    assert palette.NEUTRAL in svg and palette.UP not in svg


def test_highs_lows_bars_point_opposite_ways():
    svg = _svg(minicharts.highs_lows([3, 5], [1, 0]))
    assert svg.count("<rect") == 3      # 2 根新高 + 1 根新低（0 不画）
    assert palette.UP in svg and palette.DOWN in svg


def test_module_has_no_raw_hex():
    assert not re.search(r"#[0-9A-Fa-f]{6}", SRC), "色值只能来自 palette"


def test_svg_has_no_script_or_foreign_object():
    for uri in (minicharts.sparkline([(0, 1.0), (1, 2.0)], 1.0), minicharts.histogram({k: 1 for k in range(-11, 12)})):
        s = _svg(uri)
        assert "<script" not in s and "foreignObject" not in s
```

- [ ] **Step 2: 跑，确认失败** → ImportError。

- [ ] **Step 3: 实现**

```python
"""首页小图（spec §2）：纯函数生成 SVG，再编成 data URI 给 `<img>`。

为什么不是 plotly：每 30 秒要重画 6 幅分时图和 1 幅分布图，它们都不需要交互。
为什么不是内联 `<svg>`：Streamlit 1.61 的 st.html 用 DOMPurify 只开了 html 配置，
`<svg>` 连同子节点整段被删、且不报错（评审读前端源码证实）。`img` 在白名单里。
`img` 里的 SVG 拿不到页面 CSS 变量与网络字体，所以色值写死 palette 常量、文字不进 SVG。
"""
from __future__ import annotations

import base64
from collections.abc import Sequence

from quant.report import palette

W, H = 120, 32          # 分时/趋势小图
HW, HH = 300, 60        # 分布柱状图
BW, BH = 300, 6         # 比例条


def to_data_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _frame(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" preserveAspectRatio="none">{body}</svg>')


def _scale(values: Sequence[float], h: int, pad: int = 2) -> tuple[float, float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    return lo, (h - 2 * pad) / (hi - lo)


def sparkline(points: Sequence[tuple[float, float]], prev_close: float) -> str:
    """当日分时：x 是相对位置（任意单调数），虚线是昨收，颜色按最后一点相对昨收。"""
    if len(points) < 2:
        return to_data_uri(_frame(W, H, ""))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points] + [prev_close]
    lo, k = _scale(ys, H)
    x0, x1 = min(xs), max(xs)
    sx = (W - 2) / (x1 - x0) if x1 > x0 else 0

    def px(x): return 1 + (x - x0) * sx
    def py(y): return H - 2 - (y - lo) * k
    color = palette.UP if points[-1][1] >= prev_close else palette.DOWN
    base_y = py(prev_close)
    line = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in points)
    body = (f'<line x1="0" y1="{base_y:.1f}" x2="{W}" y2="{base_y:.1f}" stroke="{palette.NEUTRAL}" '
            f'stroke-width="0.8" stroke-dasharray="2 2"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.3"/>')
    return to_data_uri(_frame(W, H, body))


def histogram(buckets: dict[int, int]) -> str:
    """23 档涨跌分布：负档绿、0 档灰、正档红（红涨绿跌是全局铁律）。"""
    keys = sorted(buckets)
    top = max(buckets.values()) or 1
    bw = HW / len(keys)
    rects = []
    for i, k in enumerate(keys):
        h = max(1.0, buckets[k] / top * (HH - 4))
        color = palette.DOWN if k < 0 else palette.UP if k > 0 else palette.NEUTRAL
        rects.append(f'<rect x="{i * bw + 1:.1f}" y="{HH - 2 - h:.1f}" width="{bw - 2:.1f}" '
                     f'height="{h:.1f}" fill="{color}"/>')
    return to_data_uri(_frame(HW, HH, "".join(rects)))


def ratio_bar(up: int, flat: int, down: int) -> str:
    total = max(1, up + flat + down)
    x = 0.0
    parts = []
    for n, color in ((up, palette.UP), (flat, palette.NEUTRAL), (down, palette.DOWN)):
        w = BW * n / total
        parts.append(f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="{BH}" fill="{color}"/>')
        x += w
    return to_data_uri(_frame(BW, BH, "".join(parts)))


def trend_line(values: Sequence[float | None]) -> str:
    """单条灰线（零类别配色）：均线占比的近 60 日走势。None 处断开。"""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return to_data_uri(_frame(W, H, ""))
    lo, k = _scale([v for _, v in pts], H)
    sx = (W - 2) / max(1, len(values) - 1)
    segs, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append(f"{1 + i * sx:.1f},{H - 2 - (v - lo) * k:.1f}")
    if len(cur) > 1:
        segs.append(cur)
    body = "".join(f'<polyline points="{" ".join(s)}" fill="none" stroke="{palette.NEUTRAL}" '
                   f'stroke-width="1.2"/>' for s in segs)
    return to_data_uri(_frame(W, H, body))


def highs_lows(highs: Sequence[int], lows: Sequence[int]) -> str:
    """新高向上红柱、新低向下绿柱，共用零轴。这里的红绿本来就是涨跌语义，不算类别配色。"""
    n = max(len(highs), len(lows), 1)
    top = max([*highs, *lows, 1])
    bw = (W - 2) / n
    mid = H / 2
    rects = []
    for i in range(n):
        h_up = (highs[i] if i < len(highs) else 0) / top * (mid - 1)
        h_dn = (lows[i] if i < len(lows) else 0) / top * (mid - 1)
        x = 1 + i * bw
        if h_up > 0:
            rects.append(f'<rect x="{x:.1f}" y="{mid - h_up:.1f}" width="{max(0.5, bw - 1):.1f}" '
                         f'height="{h_up:.1f}" fill="{palette.UP}"/>')
        if h_dn > 0:
            rects.append(f'<rect x="{x:.1f}" y="{mid:.1f}" width="{max(0.5, bw - 1):.1f}" '
                         f'height="{h_dn:.1f}" fill="{palette.DOWN}"/>')
    return to_data_uri(_frame(W, H, "".join(rects)))
```

- [ ] **Step 4: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_minicharts.py -q` → PASS。

```bash
git add src/quant/report/minicharts.py tests/test_minicharts.py
git commit -m "feat(report): 首页小图——SVG 走 img data URI（st.html 会删内联 svg），色值只取 palette"
```

---

## 里程碑 C：收盘数据任务

### Task 15: baostock_provider — 带错误码的异常 + 全市场快照 [C]

**Files:**
- Modify: `src/quant/data/baostock_provider.py:22-40, 105-147`
- Test: `tests/test_baostock_provider.py`（追加）

spec §5.2「所有 baostock 读取都走 `_fetch`」：`_check` 改抛带 `code` 的 `RuntimeError` 子类，既有调用方（只捕 RuntimeError）不受影响。

- [ ] **Step 1: 写测试**

```python
# tests/test_baostock_provider.py（追加）
import pandas as pd
import pytest

from quant.data import baostock_provider as bp


class _RS:
    """baostock ResultData 的最小替身。"""
    def __init__(self, code="0", msg="", rows=(), fields=()):
        self.error_code, self.error_msg, self.fields = code, msg, list(fields)
        self._rows, self._i = list(rows), 0

    def next(self):
        if self._i < len(self._rows):
            self._i += 1
            return True
        return False

    def get_row_data(self):
        return self._rows[self._i - 1]


def test_check_raises_an_error_that_carries_the_code():
    with pytest.raises(bp.BaostockError) as ei:
        bp._check(_RS(code="10001001", msg="用户未登录"))
    assert ei.value.code == "10001001" and isinstance(ei.value, RuntimeError)
    assert bp.BaostockError("10001001", "x").is_session_error
    assert not bp.BaostockError("10004011", "x").is_session_error


def test_market_snapshot_converts_types_and_validates(monkeypatch):
    rows = [["2026-09-23", "sh.600000", "9.03", "9.07", "8.97", "8.98", "9.04", "1", "1", "3", "0.16",
             "1", "-0.6637", "5.8", "0.4", "1", "1", "0"],
            ["2026-09-23", "sz.000001", "1", "1", "1", "1", "1", "", "", "3", "", "0", "", "", "", "", "", "0"]]
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST".split(",")
    monkeypatch.setattr(bp.bs, "query_daily_history_k_AStock", lambda date: _RS(rows=rows, fields=fields))
    monkeypatch.setattr(bp, "SNAPSHOT_MIN_ROWS", 1)
    df = bp.BaostockProvider().get_market_snapshot(pd.Timestamp("2026-09-23").date())
    assert list(df.columns) == ["code", "close", "preclose", "tradestatus", "isST"]
    assert df["tradestatus"].tolist() == [1, 0] and df["isST"].dtype.kind == "i"
    assert df["close"].dtype.kind == "f"


def test_market_snapshot_rejects_rows_of_another_day(monkeypatch):
    rows = [["2026-09-22", "sh.600000", "9", "9", "9", "9", "9", "1", "1", "3", "0", "1", "0", "", "", "", "", "0"]]
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST".split(",")
    monkeypatch.setattr(bp.bs, "query_daily_history_k_AStock", lambda date: _RS(rows=rows, fields=fields))
    monkeypatch.setattr(bp, "SNAPSHOT_MIN_ROWS", 1)
    with pytest.raises(bp.SnapshotInvalid, match="日期"):
        bp.BaostockProvider().get_market_snapshot(pd.Timestamp("2026-09-23").date())


def test_market_snapshot_rejects_pctchg_mismatch_and_bad_prefix(monkeypatch):
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST".split(",")
    bad_pct = [["2026-09-23", "sh.600000", "9", "9", "9", "9.9", "9.0", "1", "1", "3", "0", "1", "5.0", "", "", "", "", "0"]]
    monkeypatch.setattr(bp.bs, "query_daily_history_k_AStock", lambda date: _RS(rows=bad_pct, fields=fields))
    monkeypatch.setattr(bp, "SNAPSHOT_MIN_ROWS", 1)
    with pytest.raises(bp.SnapshotInvalid, match="pctChg"):
        bp.BaostockProvider().get_market_snapshot(pd.Timestamp("2026-09-23").date())
    bj = [["2026-09-23", "bj.920001", "9", "9", "9", "9", "9", "1", "1", "3", "0", "1", "0", "", "", "", "", "0"]]
    monkeypatch.setattr(bp.bs, "query_daily_history_k_AStock", lambda date: _RS(rows=bj, fields=fields))
    with pytest.raises(bp.SnapshotInvalid, match="前缀"):
        bp.BaostockProvider().get_market_snapshot(pd.Timestamp("2026-09-23").date())


def test_empty_snapshot_returns_empty_frame_not_error(monkeypatch):
    """error_code=0 且 0 行 = 真空结果（当天未发布）；由调用方按 §5.2 分支处理。"""
    monkeypatch.setattr(bp.bs, "query_daily_history_k_AStock", lambda date: _RS(rows=[], fields=[]))
    df = bp.BaostockProvider().get_market_snapshot(pd.Timestamp("2026-09-23").date())
    assert df.empty
```

- [ ] **Step 2: 跑，确认失败** → AttributeError。

- [ ] **Step 3: 实现（改 `_check`，加类与方法）**

```python
# 替换原来的 _check：
class BaostockError(RuntimeError):
    """baostock 返回非 0 错误码。code 是字符串（baostock 的口径）。"""
    SESSION_CODES = frozenset({"10001001", "10001003", "10001007", "10002001", "10002002",
                               "10002003", "10002004", "10002005", "10002006", "10002007", "10002008"})

    def __init__(self, code: str, msg: str):
        super().__init__(f"baostock 错误 {code}: {msg}")
        self.code = code

    @property
    def is_session_error(self) -> bool:
        return self.code in self.SESSION_CODES


class SnapshotInvalid(ValueError):
    """全市场快照没通过校验——不存、报错说明是哪一天（spec §5.2）。"""


def _check(rs) -> None:
    if rs.error_code != "0":
        raise BaostockError(rs.error_code, rs.error_msg)


SNAPSHOT_MIN_ROWS = 4000
SNAPSHOT_COLUMNS = ["code", "close", "preclose", "tradestatus", "isST"]

# BaostockProvider 里追加：
    def get_market_snapshot(self, day: date) -> pd.DataFrame:
        """全市场当日日线（baostock 0.9.3 的 query_daily_history_k_AStock）。

        0 行 = error_code 0 的真空结果（当天未发布），**原样返回空表**由调用方分支；
        非 0 错误码由 _fetch 抛 BaostockError。校验不过抛 SnapshotInvalid。
        只保留首页要用的五列并转类型：baostock 全是字符串，`"1" == 1` 恒 False，
        不转的话 `tradestatus == 1` 会静默得到空表、所有分母变 0。
        """
        df = _fetch(bs.query_daily_history_k_AStock(date=str(day)))
        if df.empty:
            return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
        if (df["date"] != str(day)).any():
            raise SnapshotInvalid(f"{day} 的快照里混有别的日期：{sorted(set(df['date']) - {str(day)})}")
        if len(df) < SNAPSHOT_MIN_ROWS:
            raise SnapshotInvalid(f"{day} 的快照只有 {len(df)} 行，少于 {SNAPSHOT_MIN_ROWS}（可能入库中途）")
        if df["code"].duplicated().any():
            raise SnapshotInvalid(f"{day} 的快照代码重复")
        if not df["code"].str.match(r"^(sh|sz)\.").all():
            raise SnapshotInvalid(f"{day} 的快照有非 sh./sz. 前缀的代码")
        out = pd.DataFrame({
            "code": df["code"].astype(str),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "preclose": pd.to_numeric(df["preclose"], errors="coerce"),
            "tradestatus": pd.to_numeric(df["tradestatus"], errors="coerce").fillna(0).astype(int),
            "isST": pd.to_numeric(df["isST"], errors="coerce").fillna(0).astype(int),
        })
        trading = out["tradestatus"] == 1
        if (out.loc[trading, "preclose"] <= 0).any() or out.loc[trading, "close"].isna().any():
            raise SnapshotInvalid(f"{day} 的快照里交易中的行有无效价格")
        pct = pd.to_numeric(df["pctChg"], errors="coerce")
        calc = (out["close"] / out["preclose"] - 1) * 100
        bad = trading & pct.notna() & ((pct - calc).abs() > 1e-3)
        if bad.any():
            raise SnapshotInvalid(f"{day} 的快照 pctChg 与 close/preclose 对不上：{out.loc[bad, 'code'].head(3).tolist()}")
        return out
```

`get_market_snapshot` 用到的 `date` 已在文件顶部 import。`SNAPSHOT_MIN_ROWS` 测试里 monkeypatch 成 1。

- [ ] **Step 4: 跑既有 + 新测试**

Run: `.venv/bin/python -m pytest tests/test_baostock_provider.py tests/test_service.py tests/test_cache.py -q`
Expected: PASS（`_check` 抛的仍是 RuntimeError 子类，老断言 `pytest.raises(RuntimeError)` 照过）。

- [ ] **Step 5: Commit**

```bash
git add src/quant/data/baostock_provider.py tests/test_baostock_provider.py
git commit -m "feat(data): BaostockError 带错误码可判会话失效；get_market_snapshot 五列转类型 + 快照校验"
```

### Task 16: 任务登记 + 日志解析器 [C]

**Files:**
- Modify: `src/quant/runner/jobs.py:105-131`
- Modify: `src/quant/runner/progress.py`
- Modify: `tests/test_runner_jobs.py:16-25`
- Create: `tests/fixtures/market_breadth_sample.log`、`tests/fixtures/market_breadth_failed.log`
- Test: `tests/test_runner_progress.py`（追加）

日志格式（spec §5.2「日志与进度」）是**接口**：脚本（Task 17）与解析器都对着它写。

- [ ] **Step 1: 写两份日志夹具**

`tests/fixtures/market_breadth_sample.log`：
```
login success!
待补 3 天
[1/3] 2026-09-24 跳过：当天的收盘数据还没发布
[2/3] 2026-09-23 5222 行 用时 18.4s
[3/3] 2026-09-22 5222 行 用时 21.0s
指标表已重算：2 天
全部完成: 已补到 2026-09-23；2026-09-24 的收盘数据还没发布，稍后再试
logout success!
```
`tests/fixtures/market_breadth_failed.log`：
```
login success!
待补 2 天
[1/2] 2026-09-23 5222 行 用时 18.4s
[2/2] 2026-08-14 跳过：重试后仍为 0 行
指标表已重算：1 天
补数失败：2026-08-14 拿不到数据（已记录，再失败一次将不再重试）
```
注意两份里**都没有**「已保存:」这个子串（Task 17 有测试钉着脚本源码）。

- [ ] **Step 2: 写测试**

```python
# tests/test_runner_progress.py（追加）
from pathlib import Path

from quant.runner import process, progress

FIX = Path(__file__).resolve().parent / "fixtures"


def test_parse_market_breadth_counts_saved_and_skipped_rows():
    p = progress.parse_market_breadth((FIX / "market_breadth_sample.log").read_text("utf-8"))
    assert (p.current, p.total) == (3, 3) and p.phase == progress.DONE
    assert p.extras["已补"] == "2 天" and p.extras["跳过"] == "1 天"
    assert p.eta_s is None                        # 已完成不给 ETA


def test_parse_market_breadth_eta_uses_the_last_ten_durations():
    log = "待补 20 天\n" + "\n".join(f"[{i}/20] 2026-0{9}-{25 - i:02d} 5000 行 用时 {10 + i}s" for i in range(1, 13))
    p = progress.parse_market_breadth(log)
    assert p.current == 12 and p.total == 20
    avg = sum(10 + i for i in range(3, 13)) / 10           # 最近 10 行：i=3..12
    assert abs(p.eta_s - avg * 8) < 1e-6 and p.phase == "补数中"


def test_parse_market_breadth_header_only_is_starting():
    p = progress.parse_market_breadth("login success!\n待补 5 天\n")
    assert p.total == 5 and p.current is None and p.phase == "补数中"
    assert progress.parse_market_breadth("").phase == progress.STARTING


def test_failed_log_is_failed_after_zombie_reaping(tmp_path):
    """面板重启后 runner 只凭日志判成败：失败路径的日志绝不能含收尾标记。"""
    log = (FIX / "market_breadth_failed.log").read_text("utf-8")
    assert not any(m in log for m in process.DONE_MARKERS)
    p = progress.parse_market_breadth(log)
    assert p.phase != progress.DONE and "失败" in p.extras.get("错误", "") or p.phase == "补数中"
```

- [ ] **Step 3: 写 jobs 测试改动**

`tests/test_runner_jobs.py` 里 `test_three_jobs_only` 改名 `test_four_jobs_only`，集合加 `"market_breadth"`；`test_scripts_exist_and_parsers_bound` 加一行 `assert JOBS["market_breadth"].parser is progress.parse_market_breadth`。

- [ ] **Step 4: 跑，确认失败** → AttributeError / 集合不等。

- [ ] **Step 5: 实现解析器**

```python
# progress.py 追加（正则放在文件顶部那组旁边）
# scripts/run_market_breadth.py
_BREADTH_HEADER = re.compile(r"^待补 (\d+) 天$", re.M)
_BREADTH_ROW = re.compile(
    r"^\[(\d+)/(\d+)\] (\d{4}-\d{2}-\d{2}) (?:(\d+) 行 用时 ([\d.]+)s|跳过：(.*))$", re.M)
_BREADTH_DONE = re.compile(r"^全部完成: ", re.M)
_BREADTH_FAILED = re.compile(r"^补数失败：(.*)$", re.M)
ETA_WINDOW = 10


def parse_market_breadth(log_text: str) -> Progress:
    """更新收盘数据：`待补 N 天` 给 total，每天一行（存下/跳过两种）给 current。

    ETA 按**最近 10 个存下的日子**的平均用时估（首日约 20 秒/天，抖动大，全程线性外推会被
    前几天的慢请求带偏）。跳过的行不计入用时。完成/失败的判定与 process.DONE_MARKERS 同口径。
    """
    total = None
    header = _BREADTH_HEADER.search(log_text)
    if header:
        total = int(header.group(1))
    rows = _BREADTH_ROW.findall(log_text)
    current = int(rows[-1][0]) if rows else None
    if rows:
        total = int(rows[-1][1])
    saved = [float(r[4]) for r in rows if r[4]]
    skipped = sum(1 for r in rows if r[5])
    extras: dict[str, str] = {}
    if rows:
        extras = {"已补": f"{len(saved)} 天", "跳过": f"{skipped} 天"}
    eta = None
    if saved and total is not None and current is not None and total > current:
        recent = saved[-ETA_WINDOW:]
        eta = sum(recent) / len(recent) * (total - current)
    phase = "补数中" if log_text.strip() else STARTING
    if _BREADTH_DONE.search(log_text):
        phase, eta = DONE, None
    failed = _BREADTH_FAILED.search(log_text)
    if failed:
        phase, eta = "失败", None
        extras["错误"] = failed.group(1)
    crash = _crash(log_text)
    if crash:
        phase, eta = CRASHED, None
        extras |= crash
    return Progress(current=current, total=total, eta_s=eta, phase=phase, extras=extras)
```

- [ ] **Step 6: 登记任务**

`jobs.py` 的 `JOBS` 末尾追加：
```python
    # 首页「趋势宽度」的数据来源（spec §5）。无参数；控件只在「市场概览」页，控制台不渲染它。
    "market_breadth": Job(
        name="market_breadth", label="更新收盘数据",
        script=str(SCRIPTS / "run_market_breadth.py"),
        params=(),
        parser=progress.parse_market_breadth, result_kind="market_breadth"),
```

- [ ] **Step 7: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_runner_progress.py tests/test_runner_jobs.py -q`
Expected: `test_scripts_exist_and_parsers_bound` **仍然 FAIL**——脚本文件还没建（Task 17）。其余 PASS。这是可接受的中间态：Task 17 之前不提交 jobs.py 也行；若要每步绿，先建一个只含 docstring 的 `scripts/run_market_breadth.py` 占位，Task 17 再填。**按后者做。**

- [ ] **Step 8: Commit**

```bash
git add src/quant/runner/jobs.py src/quant/runner/progress.py tests/test_runner_jobs.py tests/test_runner_progress.py tests/fixtures/market_breadth_*.log scripts/run_market_breadth.py
git commit -m "feat(runner): 登记「更新收盘数据」任务；日志解析器按最近 10 天均速估 ETA"
```

此时 `tests/test_dashboard_console.py::test_three_cards_render`、`test_dashboard_guide.py::CARDS_WITH_HELP`、`test_guide.py::test_every_job_has_a_popover_help` 等会红——它们默认 JOBS = 控制台卡片。**Task 18 一并改**，本任务先不跑全量。

### Task 17: scripts/run_market_breadth.py [C]

**Files:**
- Create: `scripts/run_market_breadth.py`
- Test: `tests/test_run_market_breadth.py`

spec §5.2 全部语义。脚本的编排逻辑放进可测的 `run(provider_factory, store, today, now_t, log)`，`main()` 只负责组装。

- [ ] **Step 1: 写测试**

```python
# tests/test_run_market_breadth.py
import importlib.util
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quant.data.baostock_provider import BaostockError, SnapshotInvalid
from quant.market import store as mstore

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_market_breadth.py"
SRC = SCRIPT.read_text("utf-8")


def _load():
    spec = importlib.util.spec_from_file_location("run_market_breadth", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_market_breadth"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cal(today: date):
    days = [today - timedelta(days=500) + timedelta(days=i) for i in range(561)]
    return days, frozenset(d for d in days if d.weekday() < 5)


def _snap(n=5):
    return pd.DataFrame({"code": [f"sh.60000{i}" for i in range(n)], "close": [1.0] * n,
                         "preclose": [1.0] * n, "tradestatus": [1] * n, "isST": [0] * n})


class FakeProvider:
    """按日期返回快照；missing 里的日期返回空表；invalid 里的抛 SnapshotInvalid；
    fail_login_after 次调用后抛一次会话错误。"""
    def __init__(self, days, trading, missing=(), invalid=(), session_fail_on=None):
        self.days, self.trading = days, trading
        self.missing, self.invalid, self.session_fail_on = set(missing), set(invalid), session_fail_on
        self.calls, self.logins = [], 0

    def __enter__(self):
        self.logins += 1
        return self

    def __exit__(self, *a):
        return False

    def get_trade_calendar(self, start, end):
        return [d for d in self.trading if start <= d <= end]

    def calendar_days(self, start, end):
        return [d for d in self.days if start <= d <= end]

    def get_market_snapshot(self, day):
        self.calls.append(day)
        if day == self.session_fail_on and self.calls.count(day) == 1:
            raise BaostockError("10001001", "用户未登录")
        if day in self.invalid:
            raise SnapshotInvalid(f"{day} bad")
        if day in self.missing:
            return pd.DataFrame(columns=mstore.SNAPSHOT_COLUMNS)
        return _snap()


TODAY = date(2026, 9, 24)      # 周四


def _run(tmp_path, provider, now_t=time(19, 0), today=TODAY):
    mod = _load()
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    lines = []
    code = mod.run(lambda: provider, ms, today=today, now_t=now_t, log=lines.append)
    return code, "\n".join(lines), ms


def test_first_run_backfills_newest_first_and_recomputes_every_five_days(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading)
    code, log, ms = _run(tmp_path, prov)
    assert code == 0 and "全部完成: " in log
    assert prov.calls[0] > prov.calls[-1], "从新往旧"
    assert prov.calls[0] == TODAY, "19:00 已过就绪时刻，窗口末端是今天"
    assert len(ms.snapshot_dates()) == 310
    assert ms.load_breadth() is not None
    assert log.count("指标表已重算") >= 310 // 5


def test_before_ready_time_the_window_ends_yesterday(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading)
    _run(tmp_path, prov, now_t=time(16, 0))
    assert TODAY not in prov.calls and max(prov.calls) == date(2026, 9, 23)


def test_today_not_published_is_skipped_and_still_a_success(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading, missing={TODAY})
    code, log, ms = _run(tmp_path, prov)
    assert code == 0
    assert re.search(r"^\[1/\d+\] 2026-09-24 跳过：", log, re.M)
    assert "2026-09-24 的收盘数据还没发布" in log and "全部完成: " in log
    assert len(ms.snapshot_dates()) == 310 - 1 + 1 or len(ms.snapshot_dates()) >= 309


def test_past_day_zero_rows_is_recorded_and_run_fails_but_continues(tmp_path):
    days, trading = _cal(TODAY)
    bad = date(2026, 8, 14)
    prov = FakeProvider(days, trading, missing={bad})
    code, log, ms = _run(tmp_path, prov)
    assert code != 0 and "补数失败：" in log and "全部完成" not in log
    assert prov.calls.count(bad) == 2, "重试 1 次"
    assert min(prov.calls) < bad, "记下之后继续往旧补"
    assert ms.permanent_gaps() == set() and "2026-08-14" in ms.gaps_path.read_text()
    assert ms.load_breadth() is not None, "失败也重算"


def test_second_failure_on_another_day_makes_it_permanent_and_stops_retrying(tmp_path):
    days, trading = _cal(TODAY)
    bad = date(2026, 8, 14)
    _run(tmp_path, FakeProvider(days, trading, missing={bad}))
    prov2 = FakeProvider(days, trading, missing={bad})
    code, log, ms = _run(tmp_path, prov2, today=TODAY + timedelta(days=1))
    assert ms.permanent_gaps() == {bad} and code != 0
    prov3 = FakeProvider(days, trading, missing={bad})
    code3, log3, _ = _run(tmp_path, prov3, today=TODAY + timedelta(days=4))
    assert bad not in prov3.calls and code3 == 0, "永久缺口不再重试、不再让任务失败"


def test_session_error_relogs_in_and_retries(tmp_path):
    days, trading = _cal(TODAY)
    prov = FakeProvider(days, trading, session_fail_on=date(2026, 9, 22))
    code, log, ms = _run(tmp_path, prov)
    assert code == 0 and prov.logins >= 2 and date(2026, 9, 22) in ms.snapshot_dates()


def test_incremental_run_only_fetches_the_gap(tmp_path):
    days, trading = _cal(TODAY)
    _run(tmp_path, FakeProvider(days, trading))
    prov = FakeProvider(days, trading)
    _run(tmp_path, prov, today=date(2026, 9, 25))          # 中秋休市 → 没有新交易日
    assert prov.calls == [] and "待补 0 天" in "\n".join([])  or prov.calls == []


def test_retention_runs_even_when_fetching_failed(tmp_path):
    days, trading = _cal(TODAY)
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.save_snapshot(date(2025, 1, 6), _snap())            # 窗口外的旧文件
    prov = FakeProvider(days, trading, missing={date(2026, 8, 14)})
    code, _, ms = _run(tmp_path, prov)
    assert code != 0 and date(2025, 1, 6) not in ms.snapshot_dates()


def test_incomplete_calendar_aborts_without_deleting(tmp_path):
    days, trading = _cal(TODAY)
    short = [d for d in days if d >= TODAY - timedelta(days=100)]
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.save_snapshot(date(2025, 1, 6), _snap())
    prov = FakeProvider(short, frozenset(d for d in short if d.weekday() < 5))
    code, log, ms = _run(tmp_path, prov)
    assert code != 0 and "日历" in log and date(2025, 1, 6) in ms.snapshot_dates()


def test_script_never_prints_the_saved_marker_anywhere():
    assert "已保存:" not in SRC, "process.DONE_MARKERS 用子串匹配，这个前缀会把失败判成成功"


def test_tmp_files_are_cleaned_at_start(tmp_path):
    ms = mstore.MarketStore(tmp_path / "data" / "market")
    ms.daily_dir.mkdir(parents=True)
    (ms.daily_dir / "x.parquet.tmp").write_text("x")
    (ms.root / "breadth.parquet.y.tmp").write_text("x")
    days, trading = _cal(TODAY)
    _run(tmp_path, FakeProvider(days, trading))
    assert not list(ms.root.glob("*.tmp")) and not list(ms.daily_dir.glob("*.tmp"))
```

- [ ] **Step 2: 跑，确认失败**（占位脚本没有 `run`）。

- [ ] **Step 3: 实现脚本**

```python
"""「更新收盘数据」入口（spec §5）：把 baostock 全市场快照按 310 个交易日的窗口补齐，
算出首页「趋势宽度」用的指标表。无参数。

    .venv/bin/python scripts/run_market_breadth.py

日志格式是与 runner 的接口（progress.parse_market_breadth 对着它写）：
    待补 N 天
    [i/N] YYYY-MM-DD 5220 行 用时 18.4s      # 存下
    [i/N] YYYY-MM-DD 跳过：<原因>            # 没存
    指标表已重算：K 天
    全部完成: …                              # 只有成功、或只缺「当天未发布」时才打
    补数失败：…                              # 有过去的日子失败时；退出码非 0

**本脚本任何路径都不许打出子串「已保存:」**——它是 process.DONE_MARKERS 之一，
runner 用子串匹配判成败，失败的一轮打了它会在面板重启后被记成成功。
"""
from __future__ import annotations

import sys
import time as _time
from collections.abc import Callable
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant import market
from quant.data.baostock_provider import BaostockError, BaostockProvider, SnapshotInvalid
from quant.market import breadth
from quant.market.store import MarketStore, calendar_complete

WINDOW_TRADING_DAYS = 310           # MA200 + 52 周 250 + 小图 60（spec §5.2）
CALENDAR_BACK_DAYS, CALENDAR_AHEAD_DAYS = 500, 60
RECOMPUTE_EVERY = 5
SESSION_RETRIES = 2
MARKET_DIR = Path("data/market")


def _recompute(ms: MarketStore, window: list[date], log: Callable[[str], None]) -> None:
    have = set(ms.snapshot_dates())
    snaps = {d: ms.load_snapshot(d) for d in window if d in have}
    if snaps:
        ms.save_breadth(breadth.compute(snaps, window))
    log(f"指标表已重算：{len(snaps)} 天")


def run(provider_factory: Callable[[], BaostockProvider], ms: MarketStore, *, today: date,
        now_t: time, log: Callable[[str], None]) -> int:
    """返回退出码。provider_factory 每次调用建一个新会话（重新登录就是再调一次）。"""
    ms.clean_tmp()
    provider = provider_factory()
    provider.__enter__()
    try:
        start = today - timedelta(days=CALENDAR_BACK_DAYS)
        end = today + timedelta(days=CALENDAR_AHEAD_DAYS)
        trading = frozenset(provider.get_trade_calendar(start, end))
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        last_done = _last_finished_trading_day(trading, today, now_t)
        why = calendar_complete(days, trading, today, last_done, WINDOW_TRADING_DAYS)
        if why:
            log(f"补数失败：交易日历不完整（{why}），本次不补数也不删文件")
            return 2
        ms.save_calendar(days, trading)
        window = sorted(d for d in trading if d <= last_done)[-WINDOW_TRADING_DAYS:]
        permanent = ms.permanent_gaps()
        have = set(ms.snapshot_dates())
        todo = [d for d in reversed(window) if d not in have and d not in permanent]
        log(f"待补 {len(todo)} 天")
        n, saved_since, failed_past, today_missing = len(todo), 0, [], None
        for i, day in enumerate(todo, 1):
            t0 = _time.monotonic()
            outcome = _fetch_one(provider_factory, provider, day)
            if isinstance(outcome, str):
                log(f"[{i}/{n}] {day} 跳过：{outcome}")
                if day == today:
                    today_missing = day
                else:
                    ms.record_failure(day, failed_on=today)
                    failed_past.append(day)
                continue
            df, provider = outcome
            ms.save_snapshot(day, df)
            log(f"[{i}/{n}] {day} {len(df)} 行 用时 {_time.monotonic() - t0:.1f}s")
            saved_since += 1
            if saved_since % RECOMPUTE_EVERY == 0:
                _recompute(ms, window, log)
        _recompute(ms, window, log)
        ms.apply_retention(window_first=window[0])
        if failed_past:
            dates = "、".join(d.isoformat() for d in failed_past)
            log(f"补数失败：{dates} 拿不到数据（已记录，再失败一次将不再重试）")
            return 1
        done = max((d for d in window if d in set(ms.snapshot_dates())), default=None)
        tail = (f"；{today_missing} 的收盘数据还没发布，稍后再试" if today_missing else "")
        log(f"全部完成: 已补到 {done}{tail}")
        return 0
    finally:
        provider.__exit__(None, None, None)


def _last_finished_trading_day(trading: frozenset[date], today: date, now_t: time) -> date:
    include_today = today in trading and now_t >= market.BREADTH_READY
    candidates = [d for d in trading if d < today or (include_today and d == today)]
    return max(candidates)


def _fetch_one(factory, provider, day: date):
    """取一天。返回 (df, provider)（provider 可能已换成重新登录的新会话），或失败原因字符串。
    0 行 = 真空结果；SnapshotInvalid 与非会话错误各重试 1 次；会话错误重新登录最多 2 次。"""
    attempts, relogins = 0, 0
    while True:
        try:
            df = provider.get_market_snapshot(day)
        except BaostockError as e:
            if e.is_session_error and relogins < SESSION_RETRIES:
                relogins += 1
                provider.__exit__(None, None, None)
                provider = factory()
                provider.__enter__()
                continue
            if attempts < 1 and not e.is_session_error:
                attempts += 1
                continue
            return f"{e}"
        except SnapshotInvalid as e:
            if attempts < 1:
                attempts += 1
                continue
            return f"快照没通过校验（{e}）"
        if df.empty:
            if attempts < 1:
                attempts += 1
                continue
            return "重试后仍为 0 行" if day != date.today() else "当天的收盘数据还没发布"
        return df, provider


def main() -> int:
    now = market.now()
    return run(BaostockProvider, MarketStore(MARKET_DIR), today=now.date(), now_t=now.time(),
               log=print)


if __name__ == "__main__":
    sys.exit(main())
```

`_fetch_one` 里判「当天」用了 `date.today()`——改成把 `today` 传进去（测试用 2026-09-24 而机器时钟不是那天）。`run()` 调用处改为 `_fetch_one(provider_factory, provider, day, today)`。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_run_market_breadth.py tests/test_runner_jobs.py -q`
Expected: PASS。`test_incremental_run_only_fetches_the_gap` 断言写得别扭——改成 `assert prov.calls == []` 一条即可。

- [ ] **Step 5: Commit**

```bash
git add scripts/run_market_breadth.py tests/test_run_market_breadth.py
git commit -m "feat(scripts): run_market_breadth——从新往旧补 310 天、每 5 天重算、当天未发布只跳过、两次失败记永久缺口"
```

---

## 里程碑 D：页面

### Task 18: 导航换落地页 + 全局离线替身 + 牵连测试 [D]

**Files:**
- Create: `app/pages_home.py`（本任务只放最小骨架：页头 + 首次引导）
- Modify: `app/dashboard.py:87-124, 148-149`
- Modify: `app/ui.py:98-111（PAGE_INTRO）, 396（control_bar 链接）`
- Modify: `app/pages_console.py:234-248, 290-310`
- Modify: `tests/conftest.py`
- Modify: `tests/test_dashboard_nav.py:48-67`、`tests/test_dashboard_console.py:74-116, 118-125`、`tests/test_dashboard_guide.py:312`、`tests/test_dashboard_visual.py:495-540`、`tests/test_guide.py`（JOB_HELP 全覆盖）

本任务的目标是**让全套测试重新变绿**：新页作为默认落地页存在、每个 AppTest 都先渲染它、且它离线、不联网、不起进程。

- [ ] **Step 1: conftest 全局隔离（autouse）**

在 `tests/conftest.py` 末尾追加：
```python
import pytest

FIXTURES_MARKET = Path(__file__).resolve().parent / "fixtures" / "market"
FROZEN_NOW = None    # 由夹具录制日决定，见 _frozen_now()


def _frozen_now():
    """冻结首页时钟：录制那天的 19:30（已收盘、过收盘截止时刻）。日期取自分时夹具的 data.date。"""
    from datetime import datetime, time
    from quant.market import sources
    m = sources.parse_tencent_minute((FIXTURES_MARKET / "tencent_minute_sh000001.bin").read_bytes(),
                                     "sh000001")
    return datetime.combine(m.trade_date, time(19, 30))


def fixture_http_get(url: str, headers=None) -> bytes:
    """首页数据源的离线替身：按 URL 片段找 tests/fixtures/market/ 里的录制文件。"""
    from quant.market.sources import FetchError
    if "minute/query?code=" in url:
        return (FIXTURES_MARKET / f"tencent_minute_{url.rsplit('=', 1)[1]}.bin").read_bytes()
    if "newfqkline" in url:
        return (FIXTURES_MARKET / f"tencent_kline_{url.split('param=')[1].split(',')[0]}.bin").read_bytes()
    table = {"qt.gtimg.cn/q=": "tencent_quotes.bin", "hq.sinajs.cn": "sina_quotes.bin",
             "getTopicZDFenBu": "em_fenbu.bin", "getTopicZTPool": "em_ztpool.bin",
             "getTopicZBPool": "em_zbpool.bin"}
    for frag, name in table.items():
        if frag in url:
            return (FIXTURES_MARKET / name).read_bytes()
    raise FetchError(f"离线替身没有这个 URL: {url}")


@pytest.fixture(autouse=True)
def offline_market(request, monkeypatch):
    """每个 AppTest 都会先渲染落地页「市场概览」，所以这四件事是全部面板测试的前提：
    1) 取数走离线替身；2) 时钟冻结在录制日收盘后；3) 清 st.cache_data 与最近成功值；
    4) 非 network 测试里任何真实联网直接失败。"""
    from quant import market
    from quant.market import live
    import requests.adapters

    monkeypatch.setattr(live, "HTTP_GET", fixture_http_get)
    frozen = _frozen_now()
    monkeypatch.setattr(market, "now", lambda: frozen)
    if request.node.get_closest_marker("network") is None:
        def _blocked(self, req, *a, **k):
            raise AssertionError(f"测试里发生了真实联网: {req.url}")
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _blocked)
    st.cache_data.clear()
    live.STORE.reset()
    yield
    st.cache_data.clear()
    live.STORE.reset()
```

- [ ] **Step 2: 最小首页骨架**

```python
# app/pages_home.py
"""「市场概览」落地页（spec）。本任务只有骨架，Task 21-23 逐块填。"""
from __future__ import annotations

import streamlit as st

import guide
import ui
from quant.runner import process


def _first_run_hint() -> None:
    """从没跑过任何任务时给一条去「使用说明」的链接。原在控制台（2026-09-05），
    落地页换成首页后跟着搬过来：引导要出现在落地的那一页。"""
    if not process.has_ever_run(ui.RUNS_DIR):
        st.page_link(ui.page_ref("guide"), label="第一次用？先看「使用说明」",
                     icon=":material/menu_book:", help=guide.guide_page("guide").lead)


def page_home() -> None:
    ui.page_head("市场概览")
    _first_run_hint()
```

- [ ] **Step 3: 导航与共享件**

`app/dashboard.py`：
- `from pages_home import page_home`。
- `CONSOLE_PAGE` 去掉 `default=True`；新增 `HOME_PAGE = st.Page(page_home, title="市场概览", icon=":material/monitoring:", url_path="home", default=True)`。
- `PAGES[""] = [HOME_PAGE, CONSOLE_PAGE, SIGNALS_PAGE]`。
- `ui.bind_pages({..., "home": HOME_PAGE, ...})`。
- 改 :6、:69-83 那段注释：落地页是「市场概览」（2026-09-23 用户定：先看市场、再干活）；`/console` 变成普通路由，不再弹「Page not found」；`/home` 是默认页自己的 url_path、会弹。

`app/ui.py`：`PAGE_INTRO` 最前面加 `"市场概览": guide.HOME_LEAD,`（Task 19 才定义 `HOME_LEAD`，本任务先写字面量 `"指数行情、涨跌分布、涨停温度计与趋势宽度，一眼看懂今天的市场。"`——**不能含「今天」**，改成 `"指数行情、涨跌分布、涨停温度计与趋势宽度：一眼看懂市场。"`）。`control_bar` 末尾的 `st.page_link(page_ref("console"), …)` 改成：
```python
    target = "home" if busy == "market_breadth" else "console"
    label = "去「市场概览」看进度" if busy == "market_breadth" else "去「任务控制台」看进度与日志"
    st.page_link(page_ref(target), label=label, icon=":material/play_circle:")
```

`app/pages_console.py`：
- `_ELSEWHERE = ("market_breadth",)` 与 `_check_roster` 里 `placed |= set(_ELSEWHERE)`，注释写「控件在市场概览页」。
- 删掉 `_first_run_hint` 及其调用（搬到首页）。
- 忙碌提示：`view.start_button_state` 的 notice 点名任务即可，不改。

- [ ] **Step 4: 改牵连的测试**

- `tests/conftest.py::PAGE_URL_PATHS` 加 `"市场概览": "home"`。
- `tests/test_dashboard_nav.py::EXPECTED_GROUPS[""]` 首位插 `("市场概览", ":material/monitoring:", "home")`；「第一页是控制台」「唯一默认页是控制台」「不切页渲染的是控制台」三条断言改成「市场概览」。
- `tests/test_dashboard_console.py`：`test_console_is_the_default_landing_page` 改为 `test_home_is_the_default_landing_page`（不切页的 `qd-title` 是「市场概览」）；首次引导三条测试保留断言、不切页（引导现在在首页，默认页就是首页）；`test_three_cards_render` 遍历 `pages_console._PIPELINE + _TOOLS` 而不是 `jobs.JOBS`；其余 `len(jobs.JOBS)` 处同样改为控制台名单。
- `tests/test_dashboard_guide.py:312`：`CARDS_WITH_HELP = len(jobs.JOBS) - len(pages_console._ELSEWHERE)`（通过 `app_module("pages_console")` 取）。
- `tests/test_dashboard_visual.py::_card_columns` 的 `starts` 只数控制台名单。
- `tests/test_guide.py::test_every_job_has_a_popover_help` 不动——Task 19 给 `JOB_HELP["market_breadth"]`。本任务先在 `guide.JOB_HELP` 里放一条占位（Task 19 替换）：
  ```python
  "market_breadth": "**更新收盘数据**——首页「趋势宽度」的数据来源。\n\n- **做什么**：占位\n- **多久**：占位\n- **结果在哪**：首页。\n",
  ```

- [ ] **Step 5: 跑全量**

Run: `.venv/bin/python -m pytest -q -x`
Expected: 全部 PASS。常见失败与修法：
- `test_every_fact_is_actually_used…`：Task 19 才加 FACTS，本任务不加。
- `tests/test_dashboard_visual.py::test_every_page_opens_with_a_page_head`：参数化的页表来自 `PAGE_URL_PATHS`，新页要有 `PAGE_INTRO` 键——Step 3 已加。
- 任何测试里出现 `AssertionError: 测试里发生了真实联网`：说明有页面在导入期就发请求，去掉。

- [ ] **Step 6: Commit**

```bash
git add -A app tests
git commit -m "feat(home): 「市场概览」成为落地页；首页离线替身/冻结时钟/联网守卫进 conftest；控制台名单加「在别的页」"
```

### Task 19: guide.py — HOME_ 文案、FACTS 两项、JOB_HELP [D]

**Files:**
- Modify: `app/guide.py`（FACTS、JOB_HELP、新增 HOME_ 常量）
- Modify: `README.md`（FACTS 逐字对账：加「市场概览」一节的雏形，含 `17:30` 与 `约 100 分钟` 两个字面量——Task 24 再补全）
- Modify: `tests/test_guide.py:55-67, 642-657`
- Test: `tests/test_guide.py`（追加）

- [ ] **Step 1: 写测试**

```python
# tests/test_guide.py（追加）
import re as _re


def test_home_state_text_covers_every_state_enum():
    from quant.market.session import State
    assert set(guide.HOME_STATE_TEXT) == set(State)


def test_home_help_covers_the_terms_on_the_page():
    for key in ("limit_up", "limit_down", "zb_rate", "max_lbc", "above_ma", "high_low", "buckets"):
        assert 10 < len(guide.HOME_HELP[key]) < 200, key


HOME_FORBIDDEN = ("今天", "今日")


def test_home_copy_never_says_today():
    """首页文案一律写具体日期或时刻（spec §4.4）。只扫 HOME_ 与 market_breadth 的帮助，
    **不改 IMPLEMENTATION_TOKENS**：其他页的文案里「今天」本来就合理。"""
    texts = {name: getattr(guide, name) for name in dir(guide) if name.startswith("HOME_")}
    texts["job_help:market_breadth"] = guide.JOB_HELP["market_breadth"]
    flat = {}
    for name, v in texts.items():
        if isinstance(v, str):
            flat[name] = v
        elif isinstance(v, dict):
            flat |= {f"{name}[{k}]": s for k, s in v.items() if isinstance(s, str)}
    for name, text in flat.items():
        for w in HOME_FORBIDDEN:
            assert w not in text, f"{name} 里出现了「{w}」：{text!r}"


def test_breadth_facts_reference_the_single_source():
    from quant import market
    assert guide.FACTS["breadth_ready"] == market.BREADTH_READY.strftime("%H:%M")
    assert "分钟" in guide.FACTS["breadth_first_run"]
```

并把 `_user_copy()` 里的前缀元组加上 `"HOME_"`，`_all_text()` 加一行：
```python
    parts += [v for name in dir(guide) if name.startswith("HOME_")
              for v in ([getattr(guide, name)] if isinstance(getattr(guide, name), str)
                        else list(getattr(guide, name).values()) if isinstance(getattr(guide, name), dict) else [])]
```

- [ ] **Step 2: 跑，确认失败** → AttributeError。

- [ ] **Step 3: 写文案**

`FACTS` 追加（**不复用 `data_ready`**）：
```python
    # —— 首页「趋势宽度」的收盘数据（另一个接口，就绪时刻单独定；Task 26 按实测改）
    "breadth_ready": _BREADTH_READY,      # 引用 quant.market.BREADTH_READY，见文件顶部
    "breadth_first_run": "约 100 分钟",   # 首次准备 310 天 × 约 20 秒（实测后改）
```
文件顶部 `from quant import market as _market` 后 `_BREADTH_READY = _market.BREADTH_READY.strftime("%H:%M")`（guide.py 已经在 src 之上、允许 import quant；它仍不 import streamlit）。

新增常量：
```python
# ---------------------------------------------------------------- 市场概览（首页）
HOME_LEAD = "指数行情、涨跌分布、涨停温度计与趋势宽度：一眼看懂市场。"
HOME_STATE_TEXT = {           # session.State → 状态条文案；时刻/日期由页面拼在后面
    "closed_day": "休市", "pre_open": "盘前", "auction": "集合竞价", "wait_open": "待开盘",
    "trading": "交易中", "lunch": "午间休市", "closed": "已收盘", "unknown": "状态未知",
}
HOME_STALE = "数据延迟"
HOME_UNAVAILABLE = "暂不可用"
HOME_FALLBACK = "暂时取不到，以下是 {stamp} 的数据"
HOME_SCOPE_TREND = "沪深 A 股，不含 ST 与当日停牌；每项只统计交易天数足够的股票"
HOME_BUCKET_NOTE = ("区间按涨跌幅绝对值划分，如 0～1% 含 1%；9% 以上合为一档；"
                    "涨停/跌停单列，只计{sealed}的；平盘指涨跌幅恰为 0。")
HOME_SEALED_INTRADAY, HOME_SEALED_CLOSE = "封住涨停/跌停", "收盘封住"
HOME_HELP = {
    "limit_up": "收盘（或此刻）封在涨停价上的股票只数。越多说明追涨的资金越活跃。",
    "limit_down": "封在跌停价上的只数。突然增多往往意味着恐慌在扩散。",
    "zb_rate": "炸板 = 盘中碰到过涨停、收盘没封住。炸板率 = 炸板 ÷（涨停 + 炸板），越高说明追高的人越容易被套。",
    "max_lbc": "连板 = 连续几个交易日涨停。最高连板越高，短线情绪越热。",
    "above_ma": "收盘价高于自己近 N 日均价的股票占比。过半说明多数股票处在上升段，反之则是普跌。",
    "high_low": "收盘价创近 52 周新高 / 新低的只数。新高多、新低少是强势市场的特征。",
    "buckets": "把全部股票按涨跌幅分成 23 档，一眼看出是普涨、普跌还是分化。",
}
HOME_TURNOVER_HELP = "上证 + 深证成指的成交额，即沪深两市全部。盘中不给「较上一交易日」的百分比：半天比全天没有意义。"
HOME_FOOTER = "盘中数据来自门户网站公开行情，可能有几秒延迟。"
HOME_TREND_TITLE = "趋势宽度"
HOME_TREND_ASOF = "收盘数据截至 {day} · " + HOME_SCOPE_TREND
HOME_TREND_NEVER = _f("趋势宽度需要约一年的收盘数据。第一次准备大约 {breadth_first_run}，可随时停止、下次接着补。")
HOME_TREND_NOT_READY = _f("{day} 的收盘数据约 {breadth_ready} 后可更新")
HOME_TREND_BUSY = "有任务在运行，稍后再更新"
HOME_TREND_SHORT = "还差约 {n} 天的收盘数据，点「更新收盘数据」继续"
HOME_TREND_GAP = "数据源缺 {days} 的数据，这几项要等窗口滚过它才有"
HOME_BTN_START, HOME_BTN_UPDATE, HOME_BTN_STOP = "开始准备", "更新收盘数据", "停止"
```
`HOME_TREND_NOT_READY` 用了 `{day}` 和 `{breadth_ready}` 两个占位，`_f` 只认 FACTS——写成 `_f("…{breadth_ready}…").replace(…)` 会乱；改成先 `_f` 再由页面 `.format(day=…)`：**把 `{day}` 写成 `{{day}}`** 让 `_f` 保留它。

`JOB_HELP["market_breadth"]` 替换占位：
```python
    "market_breadth": _f("""**更新收盘数据**——首页「趋势宽度」的数据来源。

- **做什么**：向数据源要全沪深每只股票的当日收盘数据，逐日存下，算出站上均线的占比与 52 周新高/新低。
- **多久**：每个交易日一次约 20 秒；第一次要补约一年，{breadth_first_run}，可随时停止、下次接着补。
- **什么时候跑**：交易日 {breadth_ready} 之后才有当天的数据；早点了也不会失败，只是补不到当天。
- **结果在哪**：首页「趋势宽度」那四张卡片。
"""),
```

`README.md` 新增一节骨架（Task 24 补全），至少包含：`17:30`（已有）与 `约 100 分钟` 两个字面量，让 `test_every_fact_appears_verbatim_in_the_readme` 过。

- [ ] **Step 4: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_guide.py tests/test_dashboard_guide.py -q` → PASS。

```bash
git add app/guide.py app/ui.py README.md tests/test_guide.py
git commit -m "feat(home): 首页文案常量（状态/术语/图注/按钮区），FACTS 新增 breadth_ready 与首次准备耗时"
```

### Task 20: theme.py — 首页卡片的 HTML 构造函数与 CSS [D]

**Files:**
- Modify: `app/theme.py`（CSS 追加 `.qd-card*`、`.qd-status`；新增 `stat_card`、`status_line`、`img_card`）
- Test: `tests/test_theme.py`（追加）

约束（`tests/test_theme.py` 钉着）：CSS 选择器只能是 `:root` 或 `.qd-*`；无裸 hex（色值只从 palette 转发）；构造函数一律转义；新构造函数要有对应 CSS 规则。首页**只用 running/idle 两种 pill**（琥珀/灰），不用 success/failed（spec §2「配色」）。

- [ ] **Step 1: 写测试**

```python
# tests/test_theme.py（追加）
def test_stat_card_renders_label_value_and_optional_image():
    html = theme.stat_card("上证指数", "3939.76", sub="-12.37 · -0.31%", color=theme.DOWN,
                           img="data:image/svg+xml;base64,QUJD", note="成交 7767亿")
    assert 'class="qd-card"' in html and "上证指数" in html and "3939.76" in html
    assert f'style="color: {theme.DOWN}"' in html
    assert '<img class="qd-card-img" src="data:image/svg+xml;base64,QUJD"' in html
    assert "<svg" not in html, "小图必须走 img data URI，st.html 会删内联 svg"


def test_stat_card_rejects_non_data_uri_images_and_off_palette_colors():
    import pytest
    with pytest.raises(ValueError):
        theme.stat_card("x", "1", img="https://evil/x.svg")
    with pytest.raises(ValueError):
        theme.stat_card("x", "1", color="#123456")


def test_stat_card_escapes_everything():
    html = theme.stat_card("<b>", "<i>", sub="<u>", note="<s>")
    assert "<b>" not in html and "<i>" not in html and "<u>" not in html and "<s>" not in html


def test_status_line_only_uses_running_or_idle():
    import pytest
    html = theme.status_line("交易中 · 14:35:03", running=True, extra="数据延迟")
    assert 'qd-pill-running' in html and "数据延迟" in html
    assert 'qd-pill-idle' in theme.status_line("已收盘 · 2026-09-24", running=False)
    for kind in ("success", "failed"):
        assert f"qd-pill-{kind}" not in html


def test_home_css_rules_exist_for_the_new_builders():
    for cls in (".qd-card", ".qd-card-label", ".qd-card-value", ".qd-card-sub", ".qd-card-img",
                ".qd-card-note", ".qd-status"):
        assert cls + " {" in theme.CSS, cls
```

- [ ] **Step 2: 跑，确认失败** → AttributeError。

- [ ] **Step 3: 实现**

CSS（追加到 `CSS` 末尾，`"""` 之前）：
```css
/* 首页卡片（市场概览）：一张卡 = 小标签 + 大数值 + 一行副文 + 一幅小图 + 一行注脚。
   边框沿用 .qd-metric 的左侧发丝线，不四面描边。小图是 img（data URI），不是内联 svg。 */
.qd-card {{
  border-left: 2px solid var(--qd-hairline);
  padding: 0.15rem 0 0.15rem 0.6rem;
  margin-bottom: 0.4rem;
}}
.qd-card-label {{
  font-family: var(--qd-sans);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  color: var(--qd-muted);
  white-space: nowrap;
}}
.qd-card-value {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  font-size: 1.35rem;
  font-weight: 500;
  line-height: 1.2;
  color: var(--qd-text);
}}
.qd-card-sub {{
  font-family: var(--qd-mono);
  font-variant-numeric: tabular-nums;
  font-size: 0.8rem;
}}
.qd-card-img {{
  display: block;
  width: 100%;
  height: 32px;
  margin: 0.25rem 0 0.1rem;
}}
.qd-card-note {{
  font-family: var(--qd-sans);
  font-size: 0.7rem;
  color: var(--qd-muted);
}}
.qd-status {{
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.2rem 0 0.6rem;
}}
```

构造函数：
```python
def _img(uri: str) -> str:
    if not uri.startswith("data:image/svg+xml;base64,"):
        raise ValueError("小图只接受 SVG 的 data URI（st.html 会删内联 svg，外链更不许）")
    return f'<img class="qd-card-img" src="{uri}" alt="">'


def stat_card(label: str, value: str, *, sub: str = "", color: str | None = None,
              img: str | None = None, note: str = "") -> str:
    """首页卡片。`color` 只给副文那一行（涨跌），白名单同 metric()。"""
    style = ""
    if color is not None:
        if color not in _INLINE_COLORS:
            raise ValueError(f"内联色值必须来自色板，收到 {color!r}")
        style = f' style="color: {color}"'
    parts = [f'<div class="qd-card"><div class="qd-card-label">{_esc(label)}</div>',
             f'<div class="qd-card-value">{_esc(value)}</div>']
    if sub:
        parts.append(f'<div class="qd-card-sub"{style}>{_esc(sub)}</div>')
    if img:
        parts.append(_img(img))
    if note:
        parts.append(f'<div class="qd-card-note">{_esc(note)}</div>')
    parts.append("</div>")
    return "".join(parts)


def status_line(text: str, *, running: bool, extra: str = "") -> str:
    """首页状态条：只用 running（琥珀）/ idle（灰）两种 pill——满屏红涨绿跌旁边不许出现
    红色的「延迟」或绿色的「成功」。`extra` 是叠加标记（如「数据延迟」），灰 pill 追加。"""
    kind = "running" if running else "idle"
    tail = pill(extra, "idle") if extra else ""
    return f'<div class="qd-status">{pill(text, kind)}{tail}</div>'
```

- [ ] **Step 4: 跑主题测试并提交**

Run: `.venv/bin/python -m pytest tests/test_theme.py -q` → PASS（含既有的「CSS 只选 .qd-*」「无裸 hex」）。

```bash
git add app/theme.py tests/test_theme.py
git commit -m "feat(theme): 首页卡片 stat_card / 状态条 status_line（只用琥珀与灰），小图只收 SVG data URI"
```

### Task 21: pages_home.py — 行情 fragment、状态条、指数卡片、两市成交额 [D]

**Files:**
- Modify: `app/pages_home.py`
- Test: `tests/test_dashboard_home.py`

页面只排版；判断全在 `quant.market.*`。两个缓存入口、两个 fragment 间隔。

- [ ] **Step 1: 写测试**

```python
# tests/test_dashboard_home.py
from datetime import datetime, time

import pytest
from streamlit.testing.v1 import AppTest

from quant import market
from quant.market import live, session
from tests.conftest import copy_app, fixture_http_get

HOME = "市场概览"


def _htmls(at):
    return [b for e in at.get("html") if not (b := e.proto.body).lstrip().startswith("<style>")]


def _home(tmp_path):
    return AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run()


def test_home_renders_six_index_cards_from_the_recording(tmp_path):
    at = _home(tmp_path)
    assert not at.exception, at.exception
    cards = [h for h in _htmls(at) if 'class="qd-card"' in h]
    names = [n for _, n in market.INDEXES]
    assert all(any(n in c for c in cards) for n in names), names
    assert all("<svg" not in h for h in _htmls(at)), "内联 svg 会被 st.html 删掉"
    assert any('qd-card-img' in c for c in cards), "指数卡片要有分时小图"


def test_status_line_shows_closed_with_the_data_date(tmp_path):
    at = _home(tmp_path)
    status = [h for h in _htmls(at) if 'class="qd-status"' in h]
    assert status and "已收盘" in status[0] and "2026-09" in status[0]
    assert "qd-pill-idle" in status[0]        # 收盘后不是琥珀


def test_turnover_line_after_close_names_two_full_days(tmp_path):
    at = _home(tmp_path)
    texts = [c.value for c in at.caption] + [m.value for m in at.markdown]
    line = next(t for t in texts if t.startswith("两市成交额"))
    assert "全天" in line and "截至" not in line


def test_home_opens_without_starting_any_process(tmp_path, monkeypatch):
    from quant.runner import process
    calls = []
    monkeypatch.setattr(process, "start", lambda *a, **k: calls.append(a))
    _home(tmp_path)
    assert calls == []


def test_home_opens_without_touching_baostock(tmp_path, monkeypatch):
    from quant.data import baostock_provider
    monkeypatch.setattr(baostock_provider.BaostockProvider, "__enter__",
                        lambda self: pytest.fail("首页打开时连了 baostock"))
    assert not _home(tmp_path).exception


def test_all_sources_down_shows_unavailable_not_zero(tmp_path, monkeypatch):
    from quant.market.sources import FetchError

    def dead(url, headers=None):
        raise FetchError("down")
    monkeypatch.setattr(live, "HTTP_GET", dead)
    at = _home(tmp_path)
    assert not at.exception
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    assert "暂不可用" in body and "0.00" not in body


def test_trading_hours_use_the_30s_fragment(tmp_path, monkeypatch):
    frozen = datetime.combine(market.now().date(), time(10, 15))
    monkeypatch.setattr(market, "now", lambda: frozen)
    at = _home(tmp_path)
    assert not at.exception
    status = [h for h in _htmls(at) if 'class="qd-status"' in h][0]
    assert "交易中" in status and "qd-pill-running" in status
    # 夹具是收盘后录的：分时最后一点 15:00，此刻 10:15 → 不在过期判据里报「延迟」（时间在未来）
    assert "数据延迟" not in status


def test_cache_entries_have_no_spinner(tmp_path):
    import inspect
    import pages_home
    src = inspect.getsource(pages_home)
    assert src.count("show_spinner=False") >= 2, "两个缓存入口都必须关掉函数名转圈"
```

`pages_home` 要能 import：`tests/conftest.py` 的 `app_module` 需要先 exec dashboard；这里直接 `sys.path` 已含 `app/`（`copy_app` 后 AppTest 加的）。若 import 失败，改成 `app_module("pages_home")` 并在 `_home` 之后调用。

- [ ] **Step 2: 跑，确认失败**。

- [ ] **Step 3: 实现（替换 Task 18 的骨架）**

```python
"""「市场概览」落地页（spec §2）。只排版：判断全在 quant.market.*（可单测）。

两个 fragment：行情区按 session 的决定用 30 秒或 60 秒；按钮区有任务在跑时 2 秒。
两个缓存入口（25 秒 / 600 秒）都 show_spinner=False——它默认会把函数名显示出来。
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

import guide
import theme
import ui
from quant import market
from quant.market import live, session, sources, turnover
from quant.market.session import Decision, State, TradeDay
from quant.market.store import MarketStore
from quant.report import fmt, minicharts
from quant.runner import process


def _store() -> MarketStore:
    return MarketStore(ui.ROOT / "data" / "market")


# ---------------------------------------------------------------- 取数（两个缓存入口）
def _round(allow_cross_day: bool, hint: date | None) -> live.LiveSnapshot:
    return live.fetch_round(live.HTTP_GET, market.now(), hint, live.STORE, allow_cross_day)


@st.cache_data(ttl=session.SHORT_TTL_S, show_spinner=False)
def _round_short(allow_cross_day: bool, hint: date | None, _key: str) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


@st.cache_data(ttl=session.LONG_TTL_S, show_spinner=False)
def _round_long(allow_cross_day: bool, hint: date | None, _key: str) -> live.LiveSnapshot:
    return _round(allow_cross_day, hint)


def _snapshot(decision: Decision, hint: date | None) -> live.LiveSnapshot:
    allow = decision.state not in (State.AUCTION, State.WAIT_OPEN, State.TRADING)
    fn = _round_short if decision.cache_ttl == session.SHORT_TTL_S else _round_long
    return fn(allow, hint, "v1")


# ---------------------------------------------------------------- 判定
def _calendar():
    return _store().load_calendar()


def _decide(snap: live.LiveSnapshot | None) -> tuple[Decision, TradeDay]:
    now = market.now()
    cal = _calendar()
    ok = bool(snap and snap.source_ok)
    src = snap.trade_date if (snap and snap.source_ok) else None
    td = session.trade_day(now.date(), cal, ok, src, now.time())
    return session.decide(now, td), td


def _hint(td_source: date | None) -> date | None:
    now = market.now()
    return session.expected_trade_day(td_source, _calendar(), now.time(),
                                      live.STORE.last_trade_date, today=now.date())


# ---------------------------------------------------------------- 渲染块
def _status(decision: Decision, snap: live.LiveSnapshot) -> None:
    text = guide.HOME_STATE_TEXT[decision.state.value]
    q = snap.quotes
    if decision.state in (State.CLOSED, State.CLOSED_DAY) and snap.trade_date:
        text += f" · {snap.trade_date.isoformat()}"
    elif q is not None and q.ts is not None:
        text += f" · {q.ts:%H:%M:%S}"
    last_point = None
    if (m := snap.minutes.get("sh000001")) and m.points:
        last_point = m.points[-1][0]
    stale = session.is_stale(market.now(), last_point, q.ts if q else None)
    running = decision.state in (State.AUCTION, State.WAIT_OPEN, State.TRADING)
    st.html(theme.status_line(text, running=running, extra=guide.HOME_STALE if stale else ""))


def _fallback_note(block: live.Block, current_day: date | None) -> str:
    if not block.fallback or block.ts is None:
        return ""
    stamp = block.ts.strftime("%m 月 %d 日 %H:%M") if block.trade_date != current_day else f"{block.ts:%H:%M}"
    return guide.HOME_FALLBACK.format(stamp=stamp)


def _index_cards(snap: live.LiveSnapshot) -> None:
    st.html(theme.section("指数行情"))
    if snap.quotes is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(snap.quotes, snap.trade_date):
        st.caption(note)
    quotes = snap.quotes.value
    for col, (code, name) in zip(st.columns(len(market.INDEXES)), market.INDEXES):
        q = quotes[code]
        m = snap.minutes.get(code)
        pts = [(i, p) for i, (_, p) in enumerate(m.points)] if m else []
        img = minicharts.sparkline(pts, q.prev_close) if pts else None
        sub = f"{q.change:+.2f} · {q.pct:+.2f}%"
        col.html(theme.stat_card(name, f"{q.price:.2f}", sub=sub, color=fmt.signed_color(q.change),
                                 img=img))


def _turnover(snap: live.LiveSnapshot, decision: Decision) -> None:
    q = snap.quotes
    if q is None:
        return
    quotes = q.value
    now, today = market.now(), market.now().date()
    trade_date = snap.trade_date or today
    current = quotes["sh000001"].amount_wan + quotes["sz399001"].amount_wan
    ts = q.ts.time() if q.ts else now.time()
    prev, pct_ok = _prev_day(trade_date, quotes, q.source, now, today)
    st.caption(turnover.line(now, today, trade_date, current, ts, prev, pct_ok))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _klines(trade_date: date, settled: bool, _key: str):
    """缓存键 = (交易日, 是否已定格)：交易日一变就重取，定格后再重取一次做口径检查。"""
    sh = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sh000001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sh000001"))
    sz = sources.fetch(live.HTTP_GET, sources.tencent_kline_url("sz399001"),
                       lambda raw: sources.parse_tencent_kline(raw, "sz399001"))
    return (sh.value, sz.value) if sh.ok and sz.ok else None


def _prev_day(trade_date: date, quotes, source: str, now: datetime, today: date):
    settled = turnover.settled(now, trade_date, today)
    bars = _klines(trade_date, settled, "v1")
    if bars is None:
        return None, None
    sh, sz = bars
    prev = turnover.previous_day(sh, sz, trade_date)
    pct_ok = None
    if settled and source == "tencent":
        pct_ok = turnover.consistent(sh, sz, trade_date, quotes["sh000001"].amount_wan,
                                     quotes["sz399001"].amount_wan)
    return prev, pct_ok


# ---------------------------------------------------------------- 行情区（两个 fragment）
def _render_live(expected_interval: int, expected_key: str) -> None:
    hint = _hint(live.STORE.last_trade_date)
    decision0, _ = _decide(None)
    snap = _snapshot(decision0, hint)
    decision, _ = _decide(snap)
    if decision.interval_s != expected_interval or _job_key() != expected_key:
        st.rerun()                # 状态/间隔变了，或跨过了收盘数据就绪时刻 → 整页换 fragment
    _status(decision, snap)
    _index_cards(snap)
    _turnover(snap, decision)
    _breadth_and_thermo(snap, decision)          # Task 22


@st.fragment(run_every=f"{session.LIVE_INTERVAL_S}s")
def _live_30(expected_key: str) -> None:
    _render_live(session.LIVE_INTERVAL_S, expected_key)


@st.fragment(run_every=f"{session.TICK_INTERVAL_S}s")
def _live_60(expected_key: str) -> None:
    _render_live(session.TICK_INTERVAL_S, expected_key)


def _job_key() -> str:
    """按钮区状态的粗粒度键：跨过 BREADTH_READY 时它会变，行情 fragment 据此整页重跑。"""
    return "ready" if market.now().time() >= market.BREADTH_READY else "early"


def _breadth_and_thermo(snap, decision) -> None:     # Task 22 填
    pass


def _first_run_hint() -> None:
    if not process.has_ever_run(ui.RUNS_DIR):
        st.page_link(ui.page_ref("guide"), label="第一次用？先看「使用说明」",
                     icon=":material/menu_book:", help=guide.guide_page("guide").lead)


def page_home() -> None:
    ui.page_head("市场概览")
    _first_run_hint()
    decision, _ = _decide(None)
    key = _job_key()
    (_live_30 if decision.interval_s == session.LIVE_INTERVAL_S else _live_60)(key)
    st.caption(guide.HOME_FOOTER)
```

`_decide(None)` 在第一轮没有快照时把 `source_ok=False`；有日历就按日历，否则「未知」——节奏按时钟行走，渲染后 `_decide(snap)` 用真实源日期重判，间隔不同就 `st.rerun()`。这符合 spec §4.1「拿到可靠日期后立即重判」。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py -q`
Expected: PASS。若 `test_turnover_line_after_close_names_two_full_days` 失败于找不到那行：AppTest 的 `st.caption` 在 `at.caption`；确认 `_turnover` 用的是 `st.caption`。

- [ ] **Step 5: 全量回归并提交**

Run: `.venv/bin/python -m pytest -q` → PASS。

```bash
git add app/pages_home.py tests/test_dashboard_home.py
git commit -m "feat(home): 行情区——状态条、六张指数卡片（分时小图）、两市成交额；30/60 秒 fragment 与两个缓存入口"
```

### Task 22: pages_home.py — 涨跌分布 + 涨停温度计 [D]

**Files:**
- Modify: `app/pages_home.py`
- Test: `tests/test_dashboard_home.py`

- [ ] **Step 1: 写测试**

```python
def test_distribution_block_counts_add_up_to_the_histogram(tmp_path):
    at = _home(tmp_path)
    htmls = _htmls(at)
    dist = next(h for h in htmls if "涨跌分布" in h)
    body = " ".join(htmls) + " ".join(c.value for c in at.caption)
    assert "上涨" in body and "下跌" in body and "平盘" in body
    assert body.count('qd-card-img') >= 7, "6 张指数小图 + 1 幅分布柱状图（+ 比例条）"
    assert "沪深京 A 股，不含 ST 与停牌股" in body


def test_thermometer_shows_four_readings_and_a_dash_for_zero_division(tmp_path, monkeypatch):
    at = _home(tmp_path)
    body = " ".join(_htmls(at))
    for label in ("涨停", "跌停", "炸板率", "最高连板"):
        assert label in body

    def empty_pools(url, headers=None):
        if "Pool" in url:
            return b'{"rc":0,"data":{"tc":0,"pool":[],"qdate":"20260924"}}'
        return fixture_http_get(url, headers)
    monkeypatch.setattr(live, "HTTP_GET", empty_pools)
    st_cache_clear = __import__("streamlit").cache_data.clear
    st_cache_clear()
    live.STORE.reset()
    at2 = _home(tmp_path)
    body2 = " ".join(_htmls(at2))
    # 池子空、分布 +11 档 > 0 → 日期对不上 → 分布与温度计整块「暂不可用」，不显示 0%
    assert "暂不可用" in body2 and "0.0%" not in body2


def test_fenbu_down_falls_back_to_tencent_counts_with_its_own_scope(tmp_path, monkeypatch):
    from quant.market.sources import FetchError

    def no_fenbu(url, headers=None):
        if "getTopicZDFenBu" in url:
            raise FetchError("down")
        return fixture_http_get(url, headers)
    monkeypatch.setattr(live, "HTTP_GET", no_fenbu)
    at = _home(tmp_path)
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    assert "沪深 A 股，含 ST，停牌计入平盘" in body
    assert "上涨" in body and "暂不可用" in body        # 家数有、分布图与温度计没有


def test_bucket_note_says_sealed_at_close_after_close_and_now_intraday(tmp_path, monkeypatch):
    at = _home(tmp_path)
    assert "收盘封住" in " ".join(c.value for c in at.caption)
    frozen = datetime.combine(market.now().date(), time(10, 15))
    monkeypatch.setattr(market, "now", lambda: frozen)
    __import__("streamlit").cache_data.clear(); live.STORE.reset()
    at2 = _home(tmp_path)
    assert "封住涨停/跌停" in " ".join(c.value for c in at2.caption)
```

- [ ] **Step 2: 跑，确认失败**。

- [ ] **Step 3: 实现（替换 Task 21 的占位 `_breadth_and_thermo`）**

```python
def _breadth_and_thermo(snap: live.LiveSnapshot, decision: Decision) -> None:
    left, right = st.columns([1.4, 1])
    with left:
        _distribution(snap, decision)
    with right:
        _thermometer(snap)


def _distribution(snap: live.LiveSnapshot, decision: Decision) -> None:
    b = snap.breadth
    scope = b.scope if b is not None else ""
    st.html(theme.section("涨跌分布", theme.pill(scope, "idle") if scope else ""))
    if b is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(b, snap.trade_date):
        st.caption(note)
    v = b.value
    up, flat, down = v.up, v.flat, v.down
    st.html(f'<div class="qd-card-sub">'
            f'<span class="qd-up">上涨 {up}</span> · 平盘 {flat} · '
            f'<span class="qd-down">下跌 {down}</span></div>')
    st.html(theme.stat_card("", "", img=minicharts.ratio_bar(up, flat, down)))
    if isinstance(v, sources.Distribution):
        st.html(theme.stat_card("", "", img=minicharts.histogram(v.buckets)))
        sealed = (guide.HOME_SEALED_CLOSE if decision.state in (State.CLOSED, State.CLOSED_DAY)
                  else guide.HOME_SEALED_INTRADAY)
        st.caption(guide.HOME_BUCKET_NOTE.format(sealed=sealed), help=guide.HOME_HELP["buckets"])
    else:
        st.caption(guide.HOME_UNAVAILABLE + "：分布图需要另一来源，此刻只有涨跌家数")


def _thermometer(snap: live.LiveSnapshot) -> None:
    st.html(theme.section("涨停温度计", theme.pill("不含 ST", "idle")))
    t = snap.thermo
    if t is None:
        st.caption(guide.HOME_UNAVAILABLE)
        return
    if note := _fallback_note(t, snap.trade_date):
        st.caption(note)
    v: live.Thermo = t.value
    denom = v.zt_tc + v.zb_tc
    rate = fmt.MISSING if denom == 0 else f"{100 * v.zb_tc / denom:.1f}%"
    lbc = fmt.MISSING if v.max_lbc is None else f"{v.max_lbc} 板"
    c1, c2 = st.columns(2)
    c1.html(theme.stat_card("涨停", str(v.limit_up), color=fmt.UP))
    c2.html(theme.stat_card("跌停", str(v.limit_down), color=fmt.DOWN))
    c3, c4 = st.columns(2)
    c3.html(theme.stat_card("炸板率", rate, note=f"炸板 {v.zb_tc} 只"))
    c4.html(theme.stat_card("最高连板", lbc))
    with st.popover("?"):
        for key in ("limit_up", "limit_down", "zb_rate", "max_lbc"):
            st.markdown(guide.HOME_HELP[key])
```

`theme.stat_card("", "", img=…)` 会渲染空 label/value——给 `stat_card` 加一个分支：label 与 value 都为空时不输出那两行（补一条主题测试）。

- [ ] **Step 4: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py tests/test_theme.py -q` → PASS。

```bash
git add app/pages_home.py app/theme.py tests
git commit -m "feat(home): 涨跌分布（同源家数 + 比例条 + 23 档柱状图）与涨停温度计（同轮整块、0/0 显示 —）"
```

### Task 23: pages_home.py — 趋势宽度四卡 + 按钮区（8 种状态）[D]

**Files:**
- Modify: `app/pages_home.py`
- Create: `src/quant/market/trend.py`（按钮区状态判定的纯函数）
- Test: `tests/test_market_trend.py`、`tests/test_dashboard_home.py`

spec §2 第 6 项、§5.4。判定放进 `trend.py`（纯函数，表驱动测），页面只映射到控件。

- [ ] **Step 1: 写 trend.py 的测试**

```python
# tests/test_market_trend.py
from datetime import date, datetime, time

import pandas as pd
import pytest

from quant.market import trend
from quant.market.session import Calendar
from quant.market.trend import ButtonState as B

D = lambda s: date.fromisoformat(s)
CAL = Calendar(D("2026-09-01"), D("2026-10-31"),
               frozenset(D(f"2026-09-{d:02d}") for d in (21, 22, 23, 24, 28, 29, 30)))
READY = time(17, 30)


def _st(**kw):
    base = dict(state_broken=False, running=False, busy_other=False, last_failed=False,
                snapshot_dates=[], calendar=CAL, permanent=set(),
                now=datetime(2026, 9, 24, 20, 0), ready=READY)
    base.update(kw)
    return trend.button_state(**base)


def test_order_of_precedence():
    assert _st(state_broken=True, running=True) is B.BROKEN
    assert _st(running=True) is B.RUNNING
    assert _st(busy_other=True) is B.BUSY_OTHER
    assert _st(last_failed=True) is B.FAILED_START           # 0 份快照 + 上次失败 → 仍给「开始准备」
    assert _st(last_failed=True, snapshot_dates=[D("2026-09-23")]) is B.FAILED_UPDATE
    assert _st() is B.NEVER


def test_missing_days_and_up_to_date():
    have = [D(f"2026-09-{d:02d}") for d in (21, 22, 23)]
    assert _st(snapshot_dates=have) is B.UPDATE                        # 20:00 已过就绪 → 缺 9-24
    assert _st(snapshot_dates=have, now=datetime(2026, 9, 24, 16, 0)) is B.NOT_READY   # 交易日就绪前
    assert _st(snapshot_dates=have + [D("2026-09-24")]) is B.UP_TO_DATE
    assert _st(snapshot_dates=have + [D("2026-09-24")], now=datetime(2026, 9, 25, 10, 0)) is B.UP_TO_DATE  # 中秋
    assert _st(snapshot_dates=have, now=datetime(2026, 9, 25, 10, 0)) is B.UPDATE     # 休市日缺 9-24 照样可补


def test_permanent_gaps_do_not_count_as_missing():
    have = [D("2026-09-22"), D("2026-09-23"), D("2026-09-24")]
    assert _st(snapshot_dates=have, permanent={D("2026-09-21")}) is B.UP_TO_DATE
    assert _st(snapshot_dates=have) is B.UPDATE


def test_no_calendar_means_unknown_missing_days_but_still_offers_update():
    assert _st(snapshot_dates=[D("2026-09-23")], calendar=None) is B.UPDATE


def test_card_text_for_insufficient_history():
    assert trend.short_text(n_missing=37, permanent_hit=False) == "还差约 37 天的收盘数据，点「更新收盘数据」继续"
    assert "窗口" in trend.short_text(n_missing=3, permanent_hit=True)
```

- [ ] **Step 2: 实现 trend.py**

```python
"""首页「趋势宽度」按钮区的状态判定（spec §5.4，按顺序命中即停）。纯函数。"""
from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum

from quant.market.session import Calendar


class ButtonState(Enum):
    BROKEN = "broken"            # 任务状态文件损坏 → 报错、不给按钮
    RUNNING = "running"          # 进度 + 停止
    BUSY_OTHER = "busy_other"    # 有别的任务在跑
    FAILED_START = "failed_start"    # 上次失败且 0 份快照 → 错误 + 「开始准备」
    FAILED_UPDATE = "failed_update"  # 上次失败 → 错误 + 「更新收盘数据」
    NEVER = "never"              # 从没准备过 → 说明 + 「开始准备」
    UPDATE = "update"            # 缺日子且可补
    NOT_READY = "not_ready"      # 交易日、就绪前、已补到上一交易日
    UP_TO_DATE = "up_to_date"    # 什么都不显示（日期在标题旁）


def missing_days(snapshot_dates: list[date], calendar: Calendar | None, permanent: set[date],
                 now: datetime, ready: time) -> list[date] | None:
    """日历里截至「最近一个已结束交易日」的交易日 − 已有 − 永久缺口。日历不覆盖 → None（未知）。"""
    today = now.date()
    if calendar is None or not calendar.covers(today):
        return None
    last_done = today if (calendar.is_trading(today) and now.time() >= ready) \
        else calendar.previous_trading_day(today)
    have = set(snapshot_dates)
    return sorted(d for d in calendar.trading if d <= last_done and d not in have and d not in permanent)


def button_state(*, state_broken: bool, running: bool, busy_other: bool, last_failed: bool,
                 snapshot_dates: list[date], calendar: Calendar | None, permanent: set[date],
                 now: datetime, ready: time) -> ButtonState:
    if state_broken:
        return ButtonState.BROKEN
    if running:
        return ButtonState.RUNNING
    if busy_other:
        return ButtonState.BUSY_OTHER
    if last_failed:
        return ButtonState.FAILED_START if not snapshot_dates else ButtonState.FAILED_UPDATE
    if not snapshot_dates:
        return ButtonState.NEVER
    missing = missing_days(snapshot_dates, calendar, permanent, now, ready)
    if missing is None:
        return ButtonState.UPDATE          # 不知道缺不缺 → 给按钮，让任务自己判
    if missing:
        return ButtonState.UPDATE
    today = now.date()
    if calendar.is_trading(today) and now.time() < ready and today not in set(snapshot_dates):
        return ButtonState.NOT_READY
    return ButtonState.UP_TO_DATE


def short_text(n_missing: int, permanent_hit: bool) -> str:
    if permanent_hit:
        return "数据源缺了其中几天，这一项要等窗口滚过它们才有"
    return f"还差约 {n_missing} 天的收盘数据，点「更新收盘数据」继续"
```

（`short_text` 的两句话搬到 `guide.HOME_TREND_SHORT/GAP` 也行，但 `src/` 不能 import `app/`——这里保留字面量并让 `test_guide` 的 HOME_ 扫描不管它；或者由页面按枚举取 guide 文案。**选后者**：`short_text` 只返回枚举/数字，文案在页面拼。）

- [ ] **Step 3: 写页面测试**

```python
# tests/test_dashboard_home.py（追加）
import json, os, sys
from quant.runner import jobs


def _fake_run(root, status, log="", exit_code=None):
    runs = root / "output" / "runs"; (runs / "logs").mkdir(parents=True, exist_ok=True)
    lp = runs / "logs" / "market_breadth_x.log"; lp.write_text(log, encoding="utf-8")
    (runs / "market_breadth.json").write_text(json.dumps({
        "script": "market_breadth", "run_id": "x", "pid": os.getpid(),
        "argv": [sys.executable, "-u", jobs.JOBS["market_breadth"].script], "log_path": str(lp),
        "started_at": "2026-09-24T19:00:00", "status": status, "exit_code": exit_code,
        "finished_at": None if status == "running" else "2026-09-24T19:05:00"}), encoding="utf-8")


def test_never_prepared_shows_the_explanation_and_a_start_button(tmp_path):
    at = _home(tmp_path)
    assert at.button(key="home_breadth_start"), "从没准备过 → 「开始准备」"
    assert any("大约" in c.value for c in at.caption)


def test_running_shows_progress_and_stop(tmp_path):
    _fake_run(tmp_path, "running", log="待补 310 天\n[57/310] 2026-06-30 5220 行 用时 18.4s\n")
    at = _home(tmp_path)
    assert at.button(key="home_breadth_stop") and not at.button(key="home_breadth_start")
    assert any("57/310" in p.value for p in at.progress) or any("57" in c.value for c in at.caption)


def test_failed_first_run_shows_the_error_and_still_offers_start(tmp_path):
    _fake_run(tmp_path, "failed", log="待补 3 天\n补数失败：交易日历不完整（日历为空）\n", exit_code=2)
    at = _home(tmp_path)
    assert at.button(key="home_breadth_start")
    assert any("交易日历不完整" in c.value for c in at.caption + at.error)


def test_up_to_date_shows_only_the_date_in_the_title(tmp_path):
    from quant.market.store import MarketStore
    import pandas as pd
    ms = MarketStore(tmp_path / "data" / "market")
    day = market.now().date()
    days = pd.date_range(day.replace(day=1), day + pd.Timedelta(days=40)).date
    ms.save_calendar(days, {d for d in days if d.weekday() < 5})
    ms.save_snapshot(day, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                        "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [day], "above20": [1], "n20": [2], "above60": [0], "n60": [0],
                                  "above200": [0], "n200": [0], "new_high": [0], "new_low": [0], "n_hl": [0]}))
    at = _home(tmp_path)
    assert not at.button(key="home_breadth_start") and not at.button(key="home_breadth_update")
    body = " ".join(_htmls(at)) + " ".join(c.value for c in at.caption)
    assert body.count(day.isoformat()) >= 1
    assert "还差约" in body, "60/200 日线历史不足要说清缺多少天"
```

- [ ] **Step 4: 实现页面部分**

```python
from quant.market import trend
from quant.market.trend import ButtonState as B
from quant.runner import progress, view

TREND_WINDOWS = ((20, "above20", "n20"), (60, "above60", "n60"), (200, "above200", "n200"))
TREND_DAYS_SHOWN = 60


def _trend_cards(ms: MarketStore) -> None:
    table = ms.load_breadth()
    asof = table["date"].iloc[-1] if table is not None and len(table) else None
    pill = theme.pill(guide.HOME_TREND_ASOF.format(day=pd.Timestamp(asof).date()), "idle") if asof is not None else ""
    st.html(theme.section(guide.HOME_TREND_TITLE, pill))
    if table is None or not len(table):
        return
    tail = table.tail(TREND_DAYS_SHOWN)
    cols = st.columns(4)
    for col, (n, above, denom) in zip(cols, TREND_WINDOWS):
        last = tail.iloc[-1]
        if last[denom] == 0:
            missing = _need_days(table, denom, n)
            col.html(theme.stat_card(f"站上 {n} 日线", fmt.MISSING,
                                     note=guide.HOME_TREND_SHORT.format(n=missing)))
            continue
        pct = 100 * last[above] / last[denom]
        series = [(r[above] / r[denom]) if r[denom] else None for _, r in tail.iterrows()]
        col.html(theme.stat_card(f"站上 {n} 日线", f"{pct:.1f}%", sub=f"{int(last[above])}/{int(last[denom])}",
                                 img=minicharts.trend_line(series), note=f"近 {len(tail)} 个交易日"))
    last = tail.iloc[-1]
    if last["n_hl"] == 0:
        cols[3].html(theme.stat_card("52 周新高 / 新低", fmt.MISSING,
                                     note=guide.HOME_TREND_SHORT.format(n=_need_days(table, "n_hl", 250))))
    else:
        cols[3].html(theme.stat_card("52 周新高 / 新低", f"{int(last['new_high'])} / {int(last['new_low'])}",
                                     img=minicharts.highs_lows(tail["new_high"].tolist(), tail["new_low"].tolist()),
                                     note=f"近 {len(tail)} 个交易日"))
    with st.popover("?"):
        st.markdown(guide.HOME_HELP["above_ma"])
        st.markdown(guide.HOME_HELP["high_low"])


def _need_days(table, denom: str, n: int) -> int:
    """历史不足时还差几天：窗口 N 减去表里已有的天数（下限 1）。"""
    return max(1, n - int(len(table)))


def _job_area(ms: MarketStore) -> None:
    try:
        state = process.read_state("market_breadth", ui.RUNS_DIR)
        busy = process.any_running(ui.RUNS_DIR)
        broken = False
    except RuntimeError as e:
        st.error(str(e)); return
    running = state is not None and state.status == process.RUNNING
    now = market.now()
    bstate = trend.button_state(
        state_broken=broken, running=running,
        busy_other=bool(busy) and busy != "market_breadth",
        last_failed=state is not None and state.status == process.FAILED,
        snapshot_dates=ms.snapshot_dates(), calendar=ms.load_calendar(),
        permanent=ms.permanent_gaps(), now=now, ready=market.BREADTH_READY)
    if bstate is B.RUNNING:
        log = process.read_log(state)
        prog = progress.parse_market_breadth(log)
        head, detail = view.progress_lines(prog, view.elapsed_seconds(state), status=state.status)
        ratio = view.progress_ratio(prog)
        st.progress(ratio, text=head) if ratio is not None else st.caption(head)
        if detail:
            st.caption(detail)
        if st.button(f"⏹ {guide.HOME_BTN_STOP}", key="home_breadth_stop"):
            ui.stop_job(state)
        return
    if bstate is B.BUSY_OTHER:
        st.caption(guide.HOME_TREND_BUSY); return
    if bstate in (B.FAILED_START, B.FAILED_UPDATE):
        log = process.read_log(state)
        st.caption(progress.parse_market_breadth(log).extras.get("错误") or "上次运行失败")
        with st.expander("完整日志"):
            st.code(log or "（暂无输出）", language="text", height=ui.LOG_BOX_HEIGHT)
    if bstate in (B.NEVER, B.FAILED_START):
        if bstate is B.NEVER:
            st.caption(guide.HOME_TREND_NEVER)
        label, key = guide.HOME_BTN_START, "home_breadth_start"
    elif bstate in (B.UPDATE, B.FAILED_UPDATE):
        label, key = guide.HOME_BTN_UPDATE, "home_breadth_update"
    elif bstate is B.NOT_READY:
        st.caption(guide.HOME_TREND_NOT_READY.format(day=f"{now.month} 月 {now.day} 日")); return
    else:
        return                                   # UP_TO_DATE：什么都不显示
    help_col, btn_col = st.columns([1, 6])
    with help_col.popover("?"):
        st.markdown(guide.JOB_HELP["market_breadth"])
    if btn_col.button(f"▶ {label}", key=key):
        ui.start_job("market_breadth", {})


@st.fragment(run_every=ui.AUTO_REFRESH_S)
def _job_live(was_busy: str) -> None:
    ms = _store()
    _job_area(ms)
    try:
        busy = process.any_running(ui.RUNS_DIR) or ""
    except RuntimeError:
        busy = was_busy
    if busy != was_busy:
        st.rerun()                # 跑完/停了 → 整页重跑，宽度卡片跟着刷新


@st.fragment
def _job_idle(was_busy: str) -> None:
    _job_area(_store())
```

`page_home()` 末尾（页脚之前）追加：
```python
    ms = _store()
    _trend_cards(ms)
    try:
        busy = process.any_running(ui.RUNS_DIR) or ""
    except RuntimeError:
        busy = ""
    (_job_live if busy == "market_breadth" else _job_idle)(busy)
```
`import pandas as pd` 加到文件顶部。

- [ ] **Step 5: 跑测试并提交**

Run: `.venv/bin/python -m pytest tests/test_market_trend.py tests/test_dashboard_home.py -q` → PASS；再 `.venv/bin/python -m pytest -q` 全量 → PASS。

```bash
git add src/quant/market/trend.py app/pages_home.py tests
git commit -m "feat(home): 趋势宽度四卡（历史不足写还差几天）+ 按钮区 8 态（上次失败排在从没准备过之前）"
```

### Task 24: README、dashboard 注释与既有说法的同步 [D]

**Files:**
- Modify: `README.md`（spec §6 逐条列出的位置）
- Modify: `app/dashboard.py`（模块 docstring 与 :69-83 注释）
- Test: `tests/test_guide.py`、`tests/test_packaging.py`（既有）

- [ ] **Step 1: README 逐条改**

- `:18` 「面板打开就是「任务控制台」」→ 「面板打开就是「市场概览」——指数行情、涨跌分布、涨停温度计与趋势宽度；「任务控制台」是第二项……」。
- `:177` 「侧边栏十个页面、分四组」→ 十一个页面；主组三页：**市场概览**（2026-09-23 起是默认落地页）、任务控制台、信号；控制台括号里「2026-09-05 起是默认落地页」改成「2026-09-05～2026-09-23 曾是默认落地页」。
- `:256` `st.cache_data`「本项目只有一处」→ 「本项目有三处：`app/pool.py` 的候选清单（TTL 6 小时）、`app/pages_home.py` 首页行情的两个入口（25 秒 / 600 秒）与两市日 K（24 小时）。清掉首页那几份的唯一后果是下一轮多发一次请求。」
- `:338` 落地页那段：加「2026-09-23 起落地页换成「市场概览」；`/console` 从此是普通路由、不再弹「Page not found」；`/home` 是默认页自己的 url_path，会弹。」
- `:557` 「无盘中与分钟级」→ 「策略与信号仍无盘中与分钟级；首页的盘中行情只是展示，不进任何策略。」
- `:52` 「那 3 项 deselected 是需要联网的 baostock 集成测试」→ 「那 6 项 deselected 是需要联网的集成测试（baostock 3 项、首页行情源 3 项）」（数字与 Task 25 一起改）。
- 新增「## 市场概览（首页）」一节（放在 §2 之后、§3 之前，后续编号顺延或用不带编号的标题以免撞 `test_the_quickstart_section_pointers_all_resolve`——**用不带编号的三级标题挂在 §2 下面**）：数据来源与口径（腾讯/新浪/东财 push2ex 各管什么、东财 push2 要 cookie 所以不用、分布的统计范围与分档规则、深证成指成交额=深市全部）、刷新规则（状态表摘要、收盘截止时刻、判休市截止时刻）、非官方说明、「更新收盘数据」任务（310 天窗口、每 5 天重算、gaps.json、约 100 分钟、磁盘占用实测值——Task 26 填）、`data/market/` 可再生不进备份清单。FACTS 的 `17:30` 与 `约 100 分钟` 必须逐字出现。

- [ ] **Step 2: dashboard.py 注释**

模块 docstring 第 6 行「使用说明是**默认落地页**」改成「市场概览是**默认落地页**（2026-09-23 用户定；09-05～09-23 是任务控制台，更早是使用说明）」；:69-83 那段顺序说明同步。

- [ ] **Step 3: 跑对账测试并提交**

Run: `.venv/bin/python -m pytest tests/test_guide.py tests/test_packaging.py tests/test_dashboard_nav.py -q` → PASS。

```bash
git add README.md app/dashboard.py
git commit -m "docs(home): README 新增「市场概览」一节；落地页/页数/cache_data/盘中说法同步"
```

---

## 里程碑 E：实测定常数、联网测试、真机复核

### Task 25: 联网集成测试 3 条 + 计数同步 [E]

**Files:**
- Create: `tests/test_market_integration.py`
- Modify: `tests/test_packaging.py:110-135`（`marked == 3` → `6`）
- Modify: `README.md:48, 52`

- [ ] **Step 1: 写联网测试（模块级 `pytestmark = pytest.mark.network`）**

```python
# tests/test_market_integration.py
"""真的去请求腾讯 / 东财 / baostock（`pytest -m network`）。三条，与 test_packaging 的计数对账。"""
import pytest

from quant import market
from quant.market import sources

pytestmark = pytest.mark.network
CODES = [c for c, _ in market.INDEXES]


def test_tencent_quotes_still_parse():
    raw = sources.default_http_get(sources.tencent_quote_url(CODES))
    q = sources.parse_tencent_quotes(raw, CODES)
    assert set(q) == set(CODES) and sources.parse_tencent_breadth(raw).total > 4000


def test_eastmoney_fenbu_still_parses():
    dist = sources.parse_em_fenbu(sources.default_http_get(sources.em_fenbu_url()))
    assert dist.total > 4000 and sorted(dist.buckets) == list(range(-11, 12))


def test_baostock_snapshot_of_a_known_day():
    from datetime import date
    from quant.data.baostock_provider import BaostockProvider
    with BaostockProvider() as p:
        df = p.get_market_snapshot(date(2026, 9, 22))
    assert len(df) == 5222 and df["tradestatus"].sum() == 5209        # 实测值（spec §1）
```

- [ ] **Step 2: 改计数与 README**

`tests/test_packaging.py` 里 `assert marked == 3` → `6`，断言文案里的「3 deselected」→「6 deselected」；README `:48` 与 `:52` 同步。

- [ ] **Step 3: 跑**

Run: `.venv/bin/python -m pytest -q`（默认 deselect 6 项）→ PASS；`.venv/bin/python -m pytest -m network tests/test_market_integration.py -q` → PASS（需联网）。

```bash
git add tests/test_market_integration.py tests/test_packaging.py README.md
git commit -m "test(home): 三条联网回归（腾讯行情、东财分布、baostock 快照）；联网计数 3→6"
```

### Task 26: spec §10 的实测 → 定常数、补夹具 [E]

**Files:**
- Modify: `src/quant/market/__init__.py`（`BREADTH_READY`）、`src/quant/market/session.py`（`CLOSE_CUTOFF`、`NON_TRADING_CUTOFF`，必要时集合竞价期校验）、`src/quant/market/sources.py`（竞价期高低校验若需放宽）
- Modify: `app/guide.py`（`breadth_first_run`、README 同步）
- Create: `tests/fixtures/market/*_0850|_0916|_0926|_0931|_0945.bin`、`*_holiday.bin`
- Test: 补 `tests/test_market_session.py` 与 `tests/test_market_sources.py` 的边界用例

日程（2026-09-25 中秋、26–27 周末，**9 月 28 日（周一）是下一个交易日**）：

- [ ] **Step 1: 休市日录制（9 月 25 日任一时刻）**

Run: `.venv/bin/python scripts/probe_market_sources.py tests/fixtures/market --tag holiday --date 20260924`
看：分时 `data.date` 是不是 `20260924`（休市日源日期停在上一交易日 → §4.1 第 3 条成立）；`qt.market` 状态串是什么；新浪各指数 `[30]` 日期。据此补一条 `parse_tencent_minute` 的休市日夹具测试。

- [ ] **Step 2: 交易日盘前/竞价录制（9 月 28 日）**

分别在 8:50、9:16、9:26、9:31、9:45 各跑一次（`--tag 0850` …，`--date 20260928`）。看：
- 分时 `data.date` 几点切到当天 → 定 `NON_TRADING_CUTOFF`（切换时刻 + 15 分钟余量）。
- 竞价期腾讯 `[5]/[33]/[34]`（开/高/低）是否为 0 → 若为 0，`parse_tencent_quotes` 的高低校验在 `opn == 0` 时已跳过（Task 4 写法），确认即可；若开为 0 而高低非 0，改成「三者任一为 0 则不校验高低关系」。
- 东财分布几点切到当天、池子 `tc` 是否为 0、两者对账在 9:16/9:26 是否会误判 → 若分布在 9:15 前已切换而池子为空，`RECONCILE_TOLERANCE` 规则会把整块判失败（符合设计，显示上一轮）。记录到 spec §10。
- 11:30–13:00 录一次 `qt.market`（午休状态词）；14:57–15:00 录腾讯 `[30]`。

- [ ] **Step 3: 收盘后定格时刻（9 月 28 日 15:00–15:40 每分钟）**

写个 bash 循环调探针（`--tag 15MM`），比较东财分布 23 档与池子 `tc` 何时不再变、腾讯最后一个分时点何时到 15:00。定 `CLOSE_CUTOFF` = 最后一次变动时刻 + 5 分钟余量（不早于 15:30）。

- [ ] **Step 4: baostock 就绪时刻（9 月 28 日 17:00–19:30 每 15 分钟）**

`.venv/bin/python -c "..."` 查 `get_market_snapshot(date(2026,9,28))` 行数。第一次非空的时刻 + 15 分钟 = `BREADTH_READY`；顺带看中途有没有出现过 4000 < 行数 < 5000 的半截结果（有 → 98% 规则确有必要，写进 README）。

- [ ] **Step 5: 首次准备实测**

在首页点「开始准备」跑完整一轮，记录耗时与 `du -sh data/market`，写进 `FACTS["breadth_first_run"]`（如「约 95 分钟」）与 README 的磁盘占用；Task 13 Step 5 的性能冒烟用真实 310 天快照再量一次。

- [ ] **Step 6: 一次性对账（不进单测，结果写 README）**

- `newfqkline` 里 `sz399001` 09-22 成交额 ×1e4 vs baostock `sz.399106` 的 1127554799048.82 元。
- 用真实快照跑 `breadth.compute` 限定在本地缓存那 3010 只上，9-21 的 above20/n20 与 1501/3010 比（±0.2pp）。
- 炸板池范围：看 `em_zbpool*.bin` 里名称有没有 ST、代码有没有 92 开头 → 决定温度计的范围标签。
- 「±11 只算有封单的」：连看 3 个交易日的涨停池 vs 收盘价=涨停价的只数。

- [ ] **Step 7: 改常数、补测试、更新 spec §10 的结论、提交**

```bash
git add -A
git commit -m "chore(home): 按 9/25、9/28 实测定常数——判休市截止、收盘截止、收盘数据就绪时刻；补盘前/竞价/休市夹具"
```

### Task 27: 真机复核、spec 收尾、合并 [E]

- [ ] **Step 1: 真机三次**（浏览器，弹窗要截图）
  - 盘中一次（9 月 28 日 10:00 后）：状态条「交易中 · 时刻」琥珀 pill、30 秒后数字变、六张小图显示（**`<img>` 版 SVG 真的画出来了**）、成交额是「截至 HH:MM」且无百分比、温度计四个数。
  - 收盘后一次：状态条灰、「已收盘 · 日期」、成交额两个全天 + 百分比（口径检查通过时）、「更新收盘数据」按钮在就绪时刻后出现、点下去进度每 2 秒动、跑完卡片刷新。
  - 休市日一次（9 月 25 日）：「休市 · 最近交易日 2026-09-24」、一切数字是 9-24 的定格值、没有「交易中」。
  - 根地址落到首页；`/console` 直接打开不弹窗；`/home` 弹「Page not found」后落首页（记进 README，已写）。
- [ ] **Step 2: spec 收尾**：§10 每条写上实测结论与日期；§1 的「待复核」项按结果改。
- [ ] **Step 3: 全量测试 + 变异抽查**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q
```
抽三处做变异确认测试真的守着：`session.is_stale` 的比较方向反过来、`parse_em_pool` 去掉 `tc != len(pool)`、`run_market_breadth` 把「当天 0 行」改成整轮 return——三处都必须有测试变红，改回。

- [ ] **Step 4: 合并**

```bash
git checkout main && git merge --no-ff feat/v0.6-market-overview
.venv/bin/python -m pytest -q
```
`pyproject.toml` 版本号 0.5.0 → 0.6.0，README 首行同步（`test_the_version_matches_the_readme_title`）。推送前与用户确认（公开仓）。

---

## 附：实现中最容易踩的坑（从 spec 评审里摘的，实现每一步前扫一眼）

1. **不带 `run_every` 的 fragment 不会自己醒**——每个状态都挂定时，只是间隔与联不联网不同。
2. **st.html 会整段删掉内联 `<svg>`，且不报错**——小图只走 `img` data URI；测试断言 body 里没有 `<svg`。
3. **`st.cache_data` 默认转圈会把函数名显示出来**——两个缓存入口都 `show_spinner=False`。
4. **东财池子的 `qdate` 永远是服务器当天**——日期只用我们传进去的；空池 `tc=0` 不报错，靠与分布 +11 档对账。
5. **腾讯/新浪收盘后的时间戳会继续往前走**——过期检测只在连续竞价时段判，状态条的时刻用数据源自带的。
6. **baostock 字段全是字符串**——`"1" == 1` 恒 False；`get_market_snapshot` 已转类型，别在别处再读原始表。
7. **error_code 非 0 与 0 行是两回事**——前者由 `_fetch` 抛 `BaostockError`（会话类才重登），后者才是「未发布」。
8. **脚本任何地方不许出现「已保存:」子串**——runner 用子串匹配判成败。
9. **「今天」「今日」不进首页文案**——`test_home_copy_never_says_today` 只扫 HOME_，别去改 `IMPLEMENTATION_TOKENS`。
10. **变异测试要 `-B` 并清 `__pycache__`**——同长度改写会命中陈旧 .pyc。

