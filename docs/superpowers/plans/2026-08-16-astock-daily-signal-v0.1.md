# A 股日线信号系统 v0.1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 A 股日线技术分析信号系统 v0.1：数据获取与缓存 → 指标 → 双均线/唐奇安策略 → 自研回测引擎（A 股规则）→ 绩效报告 → 每日信号脚本 → Streamlit 面板。

**Architecture:** 分层架构（data / indicators / strategy / backtest / report / signal / app），策略只见 DataFrame、引擎只见信号、UI 纯只读。信号在后复权价上计算，撮合与约束在原始价上执行，除权除息日按复权因子比率调整持仓股数。

**Tech Stack:** Python ≥3.12（当前 venv 3.14，Task 0 验证）、pandas、pyarrow、baostock、PyYAML、plotly、streamlit、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-astock-daily-signal-v0.1-design.md`（实现中所有取舍以 spec 为准；本计划引用其决策编号，如"决策5=分段印花税"）

**验证状态：** 本计划中的全部离线代码与测试已在临时环境实际执行验证（pandas 3.0.5 / Python 3.14）——42 项单测全部通过，并用合成数据（含除权、停牌）跑通了"数据服务 → 策略 → 回测 → 指标 → 图表 → 信号"端到端链路。所有手算断言数值经实跑核对无误。**未经验证的部分只有 baostock 联网代码**（Task 4/13/14 的网络路径），故 Task 0 的探针脚本是其字段口径的事实基准。

**约定：**
- 所有命令在项目根目录 `/Users/watashi/workspace/pycharm-project/quant_demo` 执行，Python 一律用 `.venv/bin/python`（若 Task 0 降级则为 `.venv312/bin/python`，后续所有命令同步替换）。
- 单元测试一律离线（fixture 数据），网络测试标记 `@pytest.mark.network`，默认跳过。
- 每个任务以 git commit 收尾；测试未过不许提交。

---

## 文件结构总览

```
config/settings.yaml            # 全部可调参数（Task 1）
src/quant/__init__.py
src/quant/config.py             # Settings/Costs 数据类 + YAML 加载（Task 1）
src/quant/data/__init__.py
src/quant/data/cache.py         # parquet 缓存：load/save/merge（Task 2）
src/quant/data/pipeline.py      # prepare_bars 纯函数：停牌过滤/adj_*派生/校验（Task 3）
src/quant/data/provider.py      # DataProvider 抽象接口（Task 4）
src/quant/data/baostock_provider.py  # baostock 实现（Task 4）
src/quant/data/service.py       # DataService：provider+cache+pipeline 编排（Task 4）
src/quant/indicators/__init__.py     # ma/rolling_high/rolling_low/atr（Task 5）
src/quant/strategy/__init__.py       # REGISTRY + build_strategies（Task 6/7）
src/quant/strategy/base.py           # Strategy 抽象基类（Task 6）
src/quant/strategy/ma_cross.py       # 双均线（Task 6）
src/quant/strategy/donchian.py       # 唐奇安突破（Task 7）
src/quant/backtest/__init__.py
src/quant/backtest/costs.py          # 佣金/分段印花税/滑点（Task 8）
src/quant/backtest/portfolio.py      # Slot/Trade 数据类（Task 9）
src/quant/backtest/engine.py         # Backtester 主循环（Task 9/10）
src/quant/report/__init__.py
src/quant/report/metrics.py          # 绩效指标（Task 11）
src/quant/report/charts.py           # plotly 净值/回撤/K线图（Task 12）
src/quant/signal/__init__.py
src/quant/signal/scan.py             # 信号扫描（Task 14）
scripts/probe_baostock.py            # 一次性探针（Task 0）
scripts/run_backtest.py              # 回测入口（Task 13）
scripts/run_daily_signal.py          # 每日信号入口（Task 14）
app/dashboard.py                     # Streamlit 三页面（Task 15）
tests/conftest.py                    # make_bars fixture 工厂（Task 2 起共用）
tests/test_config.py ... tests/test_signal_scan.py（各任务对应）
pyproject.toml
README.md（Task 16）
```

---

### Task 0: 项目脚手架与依赖验证 [M1]

**Files:**
- Create: `pyproject.toml`, `src/quant/__init__.py`, `tests/__init__.py`, `scripts/probe_baostock.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "quant-demo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pandas>=2.2",
    "pyarrow>=15",
    "baostock>=0.8.8",
    "PyYAML>=6",
    "plotly>=5.20",
    "streamlit>=1.30",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not network'"
markers = ["network: 需要联网访问 baostock 的集成测试"]
```

- [ ] **Step 2: 创建包骨架**

```bash
mkdir -p src/quant tests scripts config app
touch src/quant/__init__.py tests/__init__.py
```

- [ ] **Step 3: 安装依赖（Python 3.14 兼容性验证，spec §15）**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: 全部安装成功。**若 baostock 或其依赖在 3.14 下安装/导入失败**：`brew install python@3.12 && /opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv312 && .venv312/bin/pip install -e ".[dev]"`，并在本计划顶部"约定"处记录改用 `.venv312`。

- [ ] **Step 4: 写探针脚本验证 baostock 真实行为（字段名/类型以实际输出为准，后续任务如与探针输出不符，以探针为准修正）**

```python
# scripts/probe_baostock.py — 一次性探针，验证 API 行为，不进入正式代码
import baostock as bs

lg = bs.login()
print("login:", lg.error_code, lg.error_msg)

rs = bs.query_history_k_data_plus(
    "sh.600519", "date,open,high,low,close,volume,amount,tradestatus,isST",
    start_date="2024-01-01", end_date="2024-01-15", frequency="d", adjustflag="3")
df = rs.get_data()
print("K线列:", list(df.columns)); print(df.head(3))

rs2 = bs.query_adjust_factor(code="sh.600519", start_date="1990-01-01", end_date="2024-12-31")
fac = rs2.get_data()
print("复权因子列:", list(fac.columns)); print(fac.to_string())
# 关键确认：backAdjustFactor 必须是"自上市累积"口径（随时间单调不减、只在除权日出现新记录），
# Task 4 的 ffill 到日频才成立。若它是"单次事件因子"（每条都接近 1），必须改为累乘后再 ffill。

rs3 = bs.query_trade_dates(start_date="2024-01-01", end_date="2024-01-15")
print(rs3.get_data().head(5))

rs4 = bs.query_history_k_data_plus(
    "sh.000300", "date,close", start_date="2024-01-01", end_date="2024-01-15",
    frequency="d", adjustflag="3")
print("指数:", rs4.get_data().head(3))
bs.logout()
```

Run: `.venv/bin/python scripts/probe_baostock.py`
Expected: login 成功（error_code="0"），四段输出均有数据；确认 `tradestatus`/`isST` 取值为字符串 "1"/"0"，复权因子表含 `dividOperateDate` 与 `backAdjustFactor` 列。把实际输出粘贴到 commit message 或注释中备查。

- [ ] **Step 5: pytest 空跑**

Run: `.venv/bin/python -m pytest`
Expected: `no tests ran`（退出码 5 属正常）

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests scripts
git commit -m "chore: 项目脚手架、依赖与 baostock 探针"
```

---

### Task 1: 配置加载 [M1]

**Files:**
- Create: `config/settings.yaml`, `src/quant/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写 config/settings.yaml（spec §12）**

```yaml
universe: ["600519", "600036", "601318", "600900", "000333",
           "600030", "600276", "601088", "600887", "601899"]
benchmark: "000300"
backtest:
  start: "2016-01-01"
  capital: 5000000
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:                       # 仅卖出，按成交日分段（spec 决策5）
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies:
  ma_cross: {fast: 20, slow: 60}
  donchian: {entry_n: 20, exit_n: 10, amount_n: 20, amount_ratio: 1.5}
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_config.py
from datetime import date
from quant.config import load_settings

def test_load_settings(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519", "000333"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 5000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies:
  ma_cross: {fast: 20, slow: 60}
""",
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.universe == ["600519", "000333"]
    assert s.start == date(2016, 1, 1)
    assert s.capital == 5_000_000
    assert s.costs.commission_min == 5.0
    assert s.strategies["ma_cross"]["fast"] == 20

def test_stamp_rate_segments(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(
        """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2023-08-27", rate: 0.001}
    - {from: "2023-08-28", rate: 0.0005}
  slippage: 0.001
strategies: {}
""",
        encoding="utf-8",
    )
    c = load_settings(cfg).costs
    assert c.stamp_rate(date(2023, 8, 27)) == 0.001
    assert c.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert c.stamp_rate(date(2016, 1, 4)) == 0.001
    assert c.stamp_rate(date(2026, 8, 14)) == 0.0005
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL（ModuleNotFoundError: quant.config）

- [ ] **Step 4: 实现 src/quant/config.py**

```python
"""配置加载：settings.yaml → 不可变数据类。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


def _to_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@dataclass(frozen=True)
class StampTaxRule:
    rate: float
    until: date | None = None  # 含当日
    frm: date | None = None    # 含当日


@dataclass(frozen=True)
class Costs:
    commission_rate: float
    commission_min: float
    slippage: float
    stamp_tax: tuple[StampTaxRule, ...]

    def stamp_rate(self, d: date) -> float:
        for r in self.stamp_tax:
            if r.until is not None and d <= r.until:
                return r.rate
            if r.frm is not None and d >= r.frm:
                return r.rate
        raise ValueError(f"没有覆盖 {d} 的印花税规则")


@dataclass(frozen=True)
class Settings:
    universe: list[str]
    benchmark: str
    start: date
    capital: float
    costs: Costs
    strategies: dict[str, dict]


def load_settings(path: str | Path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = tuple(
        StampTaxRule(
            rate=float(item["rate"]),
            until=_to_date(item["until"]) if "until" in item else None,
            frm=_to_date(item["from"]) if "from" in item else None,
        )
        for item in raw["costs"]["stamp_tax"]
    )
    costs = Costs(
        commission_rate=float(raw["costs"]["commission_rate"]),
        commission_min=float(raw["costs"]["commission_min"]),
        slippage=float(raw["costs"]["slippage"]),
        stamp_tax=rules,
    )
    return Settings(
        universe=[str(s) for s in raw["universe"]],
        benchmark=str(raw["benchmark"]),
        start=_to_date(raw["backtest"]["start"]),
        capital=float(raw["backtest"]["capital"]),
        costs=costs,
        strategies={k: dict(v) for k, v in (raw.get("strategies") or {}).items()},
    )
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add config src/quant/config.py tests/test_config.py
git commit -m "feat: 配置加载与分段印花税规则"
```

---

### Task 2: parquet 缓存层 [M1]

**Files:**
- Create: `src/quant/data/__init__.py`, `src/quant/data/cache.py`
- Test: `tests/conftest.py`, `tests/test_cache.py`

- [ ] **Step 1: 写 tests/conftest.py（全项目共用的 fixture 工厂）**

```python
# tests/conftest.py
import pandas as pd


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """手工构造日线 DataFrame。rows 每项至少含 date/open/high/low/close/volume/amount，
    可选 adj_factor（默认1.0）/trade_status（默认1）/is_st（默认0）。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col, default in [("adj_factor", 1.0), ("trade_status", 1), ("is_st", 0)]:
        if col not in df.columns:
            df[col] = default
        else:
            # 关键：只有部分行显式给了该列时，pandas 会把其余行填成 NaN。
            # 少了这一步，"只给一行 trade_status=0"的用例会让全部行都不等于 1 而被过滤光。
            df[col] = df[col].fillna(default)
    return df
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_cache.py
from quant.data.cache import BarCache
from tests.conftest import make_bars


def _row(d, px):
    return dict(date=d, open=px, high=px, low=px, close=px, volume=1000, amount=px * 1000)


def test_save_and_load_roundtrip(tmp_path):
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    cache.save("600519", df)
    loaded = cache.load("600519")
    assert loaded is not None
    assert list(loaded.index) == list(df.index)
    assert loaded["close"].tolist() == [10.0, 11.0]


def test_load_missing_returns_none(tmp_path):
    assert BarCache(tmp_path).load("600519") is None


def test_merge_dedup_keeps_last(tmp_path):
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    new = make_bars([_row("2024-01-03", 11.5), _row("2024-01-04", 12.0)])
    merged = BarCache(tmp_path).merge(old, new)
    assert len(merged) == 3
    assert merged.loc["2024-01-03", "close"] == 11.5  # 重叠日期以新数据为准
    assert merged.index.is_monotonic_increasing
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: FAIL（ModuleNotFoundError: quant.data）

- [ ] **Step 4: 实现 src/quant/data/cache.py（并创建空 `src/quant/data/__init__.py`）**

```python
"""行情本地缓存：每标的一个 parquet 文件（spec 决策9）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


class BarCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        df.to_parquet(self._path(symbol))

    @staticmethod
    def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
        if old is None or old.empty:
            return new.sort_index()
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")]
        return merged.sort_index()
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/quant/data tests/conftest.py tests/test_cache.py
git commit -m "feat: parquet 行情缓存（增量合并去重）"
```

---

### Task 3: 数据管道 prepare_bars [M1]

**Files:**
- Create: `src/quant/data/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline.py
from quant.data.pipeline import prepare_bars
from tests.conftest import make_bars


def _row(d, px, **kw):
    base = dict(date=d, open=px, high=px * 1.01, low=px * 0.99, close=px,
                volume=1000, amount=px * 1000)
    base.update(kw)
    return base


def test_derives_adj_columns():
    raw = make_bars([_row("2024-01-02", 10.0, adj_factor=2.0)])
    df, _ = prepare_bars(raw)
    assert df.loc["2024-01-02", "adj_close"] == 20.0
    assert df.loc["2024-01-02", "adj_open"] == 20.0


def test_filters_suspended_rows():
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, trade_status=0, volume=0),  # 停牌行
        _row("2024-01-04", 11.0),
    ])
    df, _ = prepare_bars(raw)
    assert len(df) == 2  # 停牌行被过滤（spec 决策6）
    assert "2024-01-03" not in df.index.strftime("%Y-%m-%d")


def test_dedup_and_warn():
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-02", 10.5)])
    df, warns = prepare_bars(raw)
    assert len(df) == 1
    assert any("重复" in w for w in warns)


def test_jump_warning_only_when_factor_unchanged():
    # 单日 -20%，因子未变 → 告警；因子变化（除权日）→ 不告警
    raw1 = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 8.0)])
    _, warns1 = prepare_bars(raw1)
    assert any("跳变" in w for w in warns1)

    raw2 = make_bars([
        _row("2024-01-02", 10.0, adj_factor=1.0),
        _row("2024-01-03", 8.0, adj_factor=1.25),  # 除权日
    ])
    _, warns2 = prepare_bars(raw2)
    assert not any("跳变" in w for w in warns2)


def test_ohlc_sanity():
    bad = make_bars([_row("2024-01-02", 10.0, high=9.0, low=11.0)])  # high < low
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


def test_st_period_warns_but_keeps_rows():
    # spec 决策7：股票池应剔除 ST，但历史区间内曾被 ST 的标的要告警提醒（数据仍保留）
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, is_st=1),
        _row("2024-01-04", 10.0, is_st=1),
    ])
    df, warns = prepare_bars(raw)
    assert len(df) == 3
    assert any("ST" in w for w in warns)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL（No module named quant.data.pipeline）

- [ ] **Step 3: 实现 src/quant/data/pipeline.py**

```python
"""原始行情 → 可用行情：去重、停牌过滤、adj_* 派生、质量校验（spec §5、决策1/6）。"""
from __future__ import annotations

import pandas as pd

JUMP_THRESHOLD = 0.11  # 主板池校验阈值（spec §5）


def prepare_bars(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """返回 (清洗后的 df, 告警列表)。df 含原始列 + adj_open/adj_high/adj_low/adj_close。"""
    warns: list[str] = []
    df = raw.sort_index().copy()

    dup = df.index.duplicated(keep="last")
    if dup.any():
        warns.append(f"重复日期 {int(dup.sum())} 行，保留最后一行")
        df = df[~df.index.duplicated(keep="last")]

    df = df[(df["trade_status"] == 1) & (df["volume"] > 0)]

    bad_ohlc = (df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1)) \
        | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad_ohlc.any():
        warns.append(f"OHLC 逻辑异常 {int(bad_ohlc.sum())} 行，已剔除: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[bad_ohlc]]}")
        df = df[~bad_ohlc]

    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]

    pct = df["close"].pct_change().abs()
    factor_changed = df["adj_factor"].diff().fillna(0.0) != 0.0
    jump = (pct > JUMP_THRESHOLD) & (~factor_changed)
    if jump.any():
        warns.append(f"异常跳变（|涨跌|>{JUMP_THRESHOLD:.0%} 且非除权日）: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[jump]]}")

    st_rows = df["is_st"] == 1
    if st_rows.any():
        # 保留数据但提醒：ST 期间涨跌幅限制为 5%，本引擎按 10% 建模（spec 决策7/8）
        warns.append(f"该标的在 {df.index[st_rows].min():%Y-%m-%d} ~ "
                     f"{df.index[st_rows].max():%Y-%m-%d} 期间为 ST（共 {int(st_rows.sum())} 天），"
                     f"涨跌停建模与实际不符，建议从股票池剔除")
    return df, warns
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/data/pipeline.py tests/test_pipeline.py
git commit -m "feat: 数据管道（停牌过滤/复权派生/质量校验）"
```

---

### Task 4: BaostockProvider 与 DataService [M1]

**Files:**
- Create: `src/quant/data/provider.py`, `src/quant/data/baostock_provider.py`, `src/quant/data/service.py`
- Test: `tests/test_service.py`（离线，用 FakeProvider）, `tests/test_baostock_integration.py`（network 标记）

- [ ] **Step 1: 写接口 src/quant/data/provider.py（spec §5）**

```python
"""数据源抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataProvider(ABC):
    """返回的 DataFrame：DatetimeIndex(date)，列 open/high/low/close/volume/amount(原始价)
    + adj_factor(后复权因子) + trade_status(1正常/0停牌) + is_st(0/1)。"""

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

    @abstractmethod
    def get_index_daily(self, index_code: str, start: date, end: date) -> pd.DataFrame: ...

    @abstractmethod
    def get_trade_calendar(self, start: date, end: date) -> list[date]: ...
```

- [ ] **Step 2: 写 DataService 失败测试（FakeProvider 离线验证增量逻辑）**

```python
# tests/test_service.py
from datetime import date

from quant.data.cache import BarCache
from quant.data.provider import DataProvider
from quant.data.service import DataService
from tests.conftest import make_bars


def _row(d, px):
    return dict(date=d, open=px, high=px * 1.01, low=px * 0.99, close=px,
                volume=1000, amount=px * 1000)


class FakeProvider(DataProvider):
    def __init__(self):
        self.calls: list[tuple] = []
        self.data = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0),
                               _row("2024-01-04", 12.0)])

    def get_daily_bars(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        mask = (self.data.index.date >= start) & (self.data.index.date <= end)
        return self.data[mask]

    def get_index_daily(self, index_code, start, end):
        raise NotImplementedError

    def get_trade_calendar(self, start, end):
        return [d for d in [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
                if start <= d <= end]


def test_first_fetch_pulls_full_range_and_caches(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 4))
    assert len(df) == 3
    assert provider.calls[0] == ("600519", date(2024, 1, 1), date(2024, 1, 4))
    assert cache.load("600519") is not None


def test_second_fetch_is_incremental(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 3))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 4))
    # 第二次只拉缺失区间：start 应为缓存最新日的次日
    assert provider.calls[1][1] == date(2024, 1, 4)


def test_refresh_forces_full_fetch(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 4))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 4), refresh=True)
    assert provider.calls[1][1] == date(2024, 1, 1)
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_service.py -v`
Expected: FAIL（No module named quant.data.service）

- [ ] **Step 4: 实现 src/quant/data/service.py**

```python
"""DataService：cache 优先、增量拉取、经 prepare_bars 清洗后交付。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from quant.data.cache import BarCache
from quant.data.pipeline import prepare_bars
from quant.data.provider import DataProvider


class DataService:
    def __init__(self, provider: DataProvider, cache: BarCache):
        self.provider = provider
        self.cache = cache

    def get_bars(self, symbol: str, start: date, end: date | None = None,
                 refresh: bool = False) -> tuple[pd.DataFrame, list[str]]:
        end = end or date.today()
        cached = None if refresh else self.cache.load(symbol)
        if cached is None or cached.empty:
            fetch_start = start
        else:
            fetch_start = cached.index.max().date() + timedelta(days=1)
        if fetch_start <= end:
            new = self.provider.get_daily_bars(symbol, fetch_start, end)
            merged = self.cache.merge(cached, new) if not new.empty else cached
            if merged is not None:
                self.cache.save(symbol, merged)
        else:
            merged = cached
        if merged is None or merged.empty:
            raise ValueError(f"{symbol}: 无可用数据（{start}~{end}）")
        df, warns = prepare_bars(merged)
        return df[df.index.date >= start], warns
```

注意：缓存里已有的历史行不会因为后来的分红而失效——baostock 的**后**复权因子是"从上市日累积"的口径，新除权只新增新日期的因子记录，旧行照旧有效（这正是 spec 决策1 选后复权的第二个原因）。

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_service.py -v`
Expected: 3 passed

- [ ] **Step 6: 实现 src/quant/data/baostock_provider.py（字段以 Task 0 探针实际输出为准）**

```python
"""baostock 数据源实现（spec §5）。用法：with BaostockProvider() as p: ..."""
from __future__ import annotations

from datetime import date

import baostock as bs
import pandas as pd

from quant.data.provider import DataProvider

_K_FIELDS = "date,open,high,low,close,volume,amount,tradestatus,isST"
_NUM_COLS = ["open", "high", "low", "close", "volume", "amount"]
_EMPTY_COLS = _NUM_COLS + ["adj_factor", "trade_status", "is_st"]


def to_bs_code(symbol: str) -> str:
    """'600519' → 'sh.600519'；'000333' → 'sz.000333'。6 开头沪市，其余(0/3开头)深市。"""
    return ("sh." if symbol.startswith("6") else "sz.") + symbol


def _check(rs) -> None:
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 错误 {rs.error_code}: {rs.error_msg}")


def _fetch(rs) -> pd.DataFrame:
    """把 baostock 结果集读成 DataFrame。

    **必须用这个函数，不要用 rs.get_data()。** baostock 0.9.3 的 get_data() 在翻页分支里
    调用了 pandas 2.0 已删除的 DataFrame.append，任何首页恰好返回 2000 行（分页大小）的
    查询都会抛 AttributeError——10 年日线（约 2579 行）正好命中，实测必崩。
    官方惯用的 next()+get_row_data() 逐行迭代没有这个问题，已用真实数据验证通过。
    """
    _check(rs)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


class BaostockProvider(DataProvider):
    def __enter__(self):
        _check(bs.login())
        return self

    def __exit__(self, *exc):
        bs.logout()
        return False

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        code = to_bs_code(symbol)
        rs = bs.query_history_k_data_plus(
            code, _K_FIELDS, start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")  # 3 = 不复权（原始价）
        df = _fetch(rs)
        if df.empty:
            return pd.DataFrame(columns=_EMPTY_COLS,
                                index=pd.DatetimeIndex([], name="date"))
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for c in _NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["trade_status"] = pd.to_numeric(df.pop("tradestatus"), errors="coerce").fillna(0).astype(int)
        df["is_st"] = pd.to_numeric(df.pop("isST"), errors="coerce").fillna(0).astype(int)
        df["adj_factor"] = self._adj_factor_series(code, end).reindex(df.index, method="ffill").fillna(1.0)
        return df[_EMPTY_COLS]

    @staticmethod
    def _adj_factor_series(code: str, end: date) -> pd.Series:
        # 从上市早期拉全量因子记录（只在除权除息日有记录），ffill 到日频。
        # 探针已确认 backAdjustFactor 是"自上市累积"口径（单调不减、每个除权日一条、日期无重复），
        # 所以直接 ffill 即可，无需累乘。
        rs = bs.query_adjust_factor(code=code, start_date="1990-01-01", end_date=str(end))
        fac = _fetch(rs)
        if fac.empty:
            return pd.Series(dtype=float)
        idx = pd.to_datetime(fac["dividOperateDate"])
        return pd.Series(fac["backAdjustFactor"].astype(float).values, index=idx).sort_index()

    def get_index_daily(self, index_code: str, start: date, end: date) -> pd.DataFrame:
        code = "sh." + index_code if index_code.startswith("0") else index_code
        rs = bs.query_history_k_data_plus(
            code, "date,close", start_date=str(start), end_date=str(end),
            frequency="d", adjustflag="3")
        df = _fetch(rs)
        if df.empty:
            raise ValueError(f"指数 {index_code} 在 {start}~{end} 无数据（检查代码前缀是否为 sh./sz.）")
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df

    def get_trade_calendar(self, start: date, end: date) -> list[date]:
        # 注意：query_trade_dates 返回的是**日历日**（含周末节假日），需按 is_trading_day 过滤。
        # 10 年区间约 3879 个日历日 → 2579 个交易日（实测）。
        rs = bs.query_trade_dates(start_date=str(start), end_date=str(end))
        df = _fetch(rs)
        trading = df[df["is_trading_day"] == "1"]
        return [d.date() for d in pd.to_datetime(trading["calendar_date"])]
```

注意：沪深300 指数在 baostock 的代码是 `sh.000300`，而深市股票 000333 是 `sz.000333`——**指数与个股的前缀规则不同**，所以 `get_index_daily` 单独处理，不复用 `to_bs_code`。

- [ ] **Step 7: 写网络集成测试（默认跳过）**

```python
# tests/test_baostock_integration.py
from datetime import date

import pytest

from quant.data.baostock_provider import BaostockProvider

pytestmark = pytest.mark.network


def test_fetch_real_bars_and_calendar():
    with BaostockProvider() as p:
        df = p.get_daily_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
        assert len(df) > 15
        assert {"open", "close", "adj_factor", "trade_status"} <= set(df.columns)
        assert (df["close"] > 0).all()
        cal = p.get_trade_calendar(date(2024, 1, 1), date(2024, 1, 31))
        assert len(cal) == len(df)  # 该月茅台无停牌，交易日数与K线行数一致
        idx = p.get_index_daily("000300", date(2024, 1, 1), date(2024, 1, 31))
        assert len(idx) == len(cal)


def test_large_range_crosses_pagination_boundary():
    """回归测试：baostock 0.9.3 的 get_data() 在翻页时用了 pandas 已删除的 DataFrame.append，
    首页恰好 2000 行就会抛 AttributeError。10 年日线约 2579 行必然触发，因此
    provider 必须走 _fetch() 的逐行迭代。区间务必 > 2000 行，否则测不到这个分支。"""
    with BaostockProvider() as p:
        df = p.get_daily_bars("600519", date(2016, 1, 1), date(2026, 8, 14))
        assert len(df) > 2000, f"区间太小测不到翻页分支（{len(df)} 行）"
        assert df.index.is_monotonic_increasing
        assert not df.index.has_duplicates
        assert df["adj_factor"].notna().all()
        cal = p.get_trade_calendar(date(2016, 1, 1), date(2026, 8, 14))
        assert len(cal) > 2000
```

- [ ] **Step 8: 运行（含网络测试一次性验证）**

Run: `.venv/bin/python -m pytest tests/test_service.py -v && .venv/bin/python -m pytest -m network -v`
Expected: 全部 passed（网络测试若因网络环境失败，记录原因；字段不符则回到 Step 6 按探针输出修正）

- [ ] **Step 9: Commit**

```bash
git add src/quant/data tests/test_service.py tests/test_baostock_integration.py
git commit -m "feat: baostock 数据源与增量数据服务"
```

---

### Task 5: 指标层 [M2]

**Files:**
- Create: `src/quant/indicators/__init__.py`
- Test: `tests/test_indicators.py`

- [ ] **Step 1: 写失败测试（手算对照）**

```python
# tests/test_indicators.py
import pandas as pd
import pytest

from quant.indicators import atr, ma, rolling_high, rolling_low
from tests.conftest import make_bars


def test_ma_hand_computed():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ma(s, 3)
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(4.0)   # (3+4+5)/3


def test_rolling_high_low_include_current_bar():
    s = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0])
    assert rolling_high(s, 3).iloc[4] == 5.0   # max(4,1,5)，含当日（spec §6 窗口约定）
    assert rolling_low(s, 3).iloc[4] == 1.0


def test_atr_hand_computed():
    df = make_bars([
        dict(date="2024-01-02", open=10, high=11, low=9, close=10, volume=1, amount=1),
        dict(date="2024-01-03", open=10, high=12, low=10, close=11, volume=1, amount=1),
        dict(date="2024-01-04", open=11, high=11, low=8, close=9, volume=1, amount=1),
    ])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    out = atr(df, 2)
    # TR2 = max(12-10, |12-10|, |10-10|) = 2；TR3 = max(3, |11-11|, |8-11|) = 3
    assert out.iloc[2] == pytest.approx(2.5)   # (2+3)/2
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/indicators/__init__.py**

```python
"""基础指标：全部纯函数，标准滚动窗口（含当日）。需要"不含当日"时由调用方先 shift(1)（spec §6）。"""
from __future__ import annotations

import pandas as pd


def ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).max()


def rolling_low(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).min()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["adj_high"], df["adj_low"], df["adj_close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/indicators tests/test_indicators.py
git commit -m "feat: 基础指标库（ma/rolling_high/rolling_low/atr）"
```

---

### Task 6: 策略基类与双均线 [M2]

**Files:**
- Create: `src/quant/strategy/__init__.py`, `src/quant/strategy/base.py`, `src/quant/strategy/ma_cross.py`
- Test: `tests/test_ma_cross.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ma_cross.py
from quant.strategy.ma_cross import MaCross
from tests.conftest import make_bars


def _bars(closes):
    rows = [dict(date=f"2024-01-{i+1:02d}", open=c, high=c, low=c, close=c,
                 volume=1000, amount=c * 1000) for i, c in enumerate(closes)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def test_positions_follow_ma_state():
    # 前 8 天横盘 10，随后连涨 → fast(2) 上穿 slow(4) → 持有；参数用小窗口便于手推
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0]
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    assert set(pos.unique()) <= {0, 1}
    assert pos.iloc[-1] == 1          # 上升段持有
    assert pos.iloc[5] == 0           # 横盘段 fast == slow，不持有
    assert (pos.iloc[:3] == 0).all()  # slow 未成形前必须为 0


def test_positions_exit_on_downtrend():
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0] + [12.0, 10.0, 8.0, 6.0]
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    assert pos.iloc[-1] == 0          # 下跌段 fast 跌回 slow 之下 → 空仓


def test_no_lookahead():
    # 未来函数错位测试（spec §13）：截断未来数据，历史信号不得改变
    closes = [10.0] * 8 + [11.0, 12.0, 13.0, 14.0, 12.0, 10.0]
    full = MaCross(fast=2, slow=4).generate_positions(_bars(closes))
    trunc = MaCross(fast=2, slow=4).generate_positions(_bars(closes[:-3]))
    assert (full.iloc[: len(trunc)].values == trunc.values).all()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_ma_cross.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 base.py、ma_cross.py 与 strategy/__init__.py**

```python
# src/quant/strategy/base.py
"""策略基类：输入清洗后的行情 df（含 adj_* 列），输出目标仓位（spec 决策10）。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        """返回与 df.index 对齐的 Series，值 ∈ {0, 1}；只能使用当日及以前的数据。"""
```

```python
# src/quant/strategy/ma_cross.py
"""双均线：MA(fast) 在 MA(slow) 之上 → 持有（状态式，与"上穿买入/下穿卖出"事件式等价）。"""
from __future__ import annotations

import pandas as pd

from quant.indicators import ma
from quant.strategy.base import Strategy


class MaCross(Strategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 60):
        assert fast < slow, "fast 必须小于 slow"
        self.fast, self.slow = fast, slow

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        f = ma(df["adj_close"], self.fast)
        s = ma(df["adj_close"], self.slow)
        return (f > s).astype(int)  # NaN 比较为 False → 暖机期自动为 0
```

```python
# src/quant/strategy/__init__.py
"""策略注册表：配置名 → 策略类（Task 7 加入 donchian）。"""
from quant.strategy.base import Strategy
from quant.strategy.ma_cross import MaCross

REGISTRY: dict[str, type[Strategy]] = {"ma_cross": MaCross}


def build_strategies(strategy_cfg: dict[str, dict]) -> list[Strategy]:
    return [REGISTRY[name](**params) for name, params in strategy_cfg.items()]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_ma_cross.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/strategy tests/test_ma_cross.py
git commit -m "feat: 策略基类与双均线策略"
```

---

### Task 7: 唐奇安通道突破（含死信号回归测试） [M2]

**Files:**
- Create: `src/quant/strategy/donchian.py`
- Modify: `src/quant/strategy/__init__.py`（注册 donchian）
- Test: `tests/test_donchian.py`

- [ ] **Step 1: 写失败测试**

**关键**：死信号回归测试的数据必须同时满足成交额条件（评审备注——持续放量），否则信号不触发的原因会与窗口 bug 混淆。

```python
# tests/test_donchian.py
from quant.strategy.donchian import Donchian
from tests.conftest import make_bars


def _bars(closes, amounts):
    rows = [dict(date=f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                 open=c, high=c, low=c, close=c, volume=1000, amount=a)
            for i, (c, a) in enumerate(zip(closes, amounts))]
    df = make_bars(rows)
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col]
    return df


def test_dead_signal_regression():
    """spec §13：持续创新高 + 持续放量的序列必须触发信号。
    若滚动窗口误含当日（未 shift），"收盘 > N日最高"永不成立 → 本测试失败。"""
    n = 40
    closes = [10.0 + i * 0.5 for i in range(n)]            # 每天创新高
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(n)]  # 每天成交额 > 1.5×前均额
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() > 0, "死信号：突破窗口可能误含当日（须先 shift(1) 再滚动）"
    assert pos.iloc[-1] == 1


def test_exit_on_breakdown():
    up = [10.0 + i * 0.5 for i in range(30)]
    down = [25.0 - i * 2.0 for i in range(1, 8)]            # 快速击穿 10 日低点
    closes = up + down
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(len(closes))]
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.iloc[29] == 1     # 上升末端仍持有
    assert pos.iloc[-1] == 0     # 跌破前 10 日最低 → 空仓


def test_no_entry_without_amount_expansion():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6] * 40                                    # 成交额平稳 → 量能条件不满足
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0


def test_no_lookahead():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6 * (1.6 ** min(i, 12)) for i in range(40)]
    strat = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5)
    full = strat.generate_positions(_bars(closes, amounts))
    trunc = strat.generate_positions(_bars(closes[:-5], amounts[:-5]))
    assert (full.iloc[: len(trunc)].values == trunc.values).all()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_donchian.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/strategy/donchian.py**

```python
"""唐奇安通道突破（spec §7）：
入场 = 收盘 > 前 entry_n 日（不含当日）最高收盘 且 成交额 > amount_ratio × 前 amount_n 日均额；
出场 = 收盘 < 前 exit_n 日（不含当日）最低收盘。
窗口必须先 shift(1) 再滚动——含当日则"收盘 > N日最高"永不成立（死信号）。"""
from __future__ import annotations

import pandas as pd

from quant.indicators import ma, rolling_high, rolling_low
from quant.strategy.base import Strategy


class Donchian(Strategy):
    name = "donchian"

    def __init__(self, entry_n: int = 20, exit_n: int = 10,
                 amount_n: int = 20, amount_ratio: float = 1.5):
        self.entry_n, self.exit_n = entry_n, exit_n
        self.amount_n, self.amount_ratio = amount_n, amount_ratio

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        c, amt = df["adj_close"], df["amount"]
        upper = rolling_high(c.shift(1), self.entry_n)          # 前 N 日，不含当日
        lower = rolling_low(c.shift(1), self.exit_n)
        amt_ok = amt > self.amount_ratio * ma(amt.shift(1), self.amount_n)
        entry = (c > upper) & amt_ok
        exit_ = c < lower

        pos = pd.Series(0, index=df.index, dtype=int)
        holding = False
        for i in range(len(df)):
            if holding and exit_.iloc[i]:
                holding = False
            elif not holding and entry.iloc[i]:
                holding = True
            pos.iloc[i] = int(holding)
        return pos
```

- [ ] **Step 4: 注册到 strategy/__init__.py**

```python
from quant.strategy.base import Strategy
from quant.strategy.donchian import Donchian
from quant.strategy.ma_cross import MaCross

REGISTRY: dict[str, type[Strategy]] = {"ma_cross": MaCross, "donchian": Donchian}


def build_strategies(strategy_cfg: dict[str, dict]) -> list[Strategy]:
    return [REGISTRY[name](**params) for name, params in strategy_cfg.items()]
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_donchian.py tests/test_ma_cross.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/quant/strategy tests/test_donchian.py
git commit -m "feat: 唐奇安通道突破策略（窗口不含当日+成交额过滤）"
```

---

### Task 8: 成本模型 [M3]

**Files:**
- Create: `src/quant/backtest/__init__.py`, `src/quant/backtest/costs.py`
- Test: `tests/test_costs.py`

- [ ] **Step 1: 写失败测试（手算对照，覆盖分段印花税与最低佣金）**

```python
# tests/test_costs.py
from datetime import date

import pytest

from quant.backtest.costs import commission, stamp_tax
from quant.config import Costs, StampTaxRule

CFG = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


def test_commission_normal():
    assert commission(100_000, CFG) == pytest.approx(25.0)   # 10万 × 万2.5


def test_commission_min_5():
    assert commission(10_000, CFG) == pytest.approx(5.0)     # 2.5 元 → 按最低 5 元


def test_stamp_tax_segmented():
    assert stamp_tax(100_000, date(2023, 8, 25), CFG) == pytest.approx(100.0)  # 0.1%
    assert stamp_tax(100_000, date(2023, 8, 29), CFG) == pytest.approx(50.0)   # 0.05%
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_costs.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/backtest/costs.py（并创建空 `backtest/__init__.py`）**

```python
"""交易成本（spec 决策5）：佣金双边收、印花税仅卖出且按成交日分段。滑点在引擎撮合价中体现。"""
from __future__ import annotations

from datetime import date

from quant.config import Costs


def commission(notional: float, cfg: Costs) -> float:
    return max(notional * cfg.commission_rate, cfg.commission_min)


def stamp_tax(notional: float, d: date, cfg: Costs) -> float:
    return notional * cfg.stamp_rate(d)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_costs.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/backtest tests/test_costs.py
git commit -m "feat: 成本模型（最低佣金/分段印花税）"
```

---

### Task 9: 回测引擎·基础撮合 [M3]

**Files:**
- Create: `src/quant/backtest/portfolio.py`, `src/quant/backtest/engine.py`
- Test: `tests/test_engine_basic.py`

- [ ] **Step 1: 写 portfolio.py（数据类，无逻辑，直接实现不先写测试）**

```python
"""账本数据结构。资金模型（spec §8）：每标的一个独立 Slot，额度=初始本金/N，互不挪用。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Trade:
    symbol: str
    action: str            # "buy" | "sell"
    date: pd.Timestamp
    price: float           # 含滑点的成交价
    shares: float
    commission: float
    stamp: float = 0.0
    pnl: float | None = None          # 仅卖出时结算
    holding_days: int | None = None   # 仅卖出时结算（自然日）


@dataclass
class Slot:
    symbol: str
    cash: float
    shares: float = 0.0
    entry_date: pd.Timestamp | None = None
    cost_basis: float = 0.0     # 本轮持仓累计买入支出（含费用）
    last_close: float | None = None
    last_factor: float | None = None


@dataclass
class BacktestResult:
    equity: pd.Series                  # 日频总净值
    trades: list[Trade]
    skipped: list[tuple] = field(default_factory=list)  # (date, symbol, 原因)
```

- [ ] **Step 2: 写引擎基础撮合的失败测试（数字全部手算，见注释）**

```python
# tests/test_engine_basic.py
from datetime import date

import pandas as pd
import pytest

from quant.backtest.engine import Backtester
from quant.config import Costs, Settings, StampTaxRule
from tests.conftest import make_bars

COSTS = Costs(
    commission_rate=0.00025, commission_min=5.0, slippage=0.001,
    stamp_tax=(StampTaxRule(rate=0.001, until=date(2023, 8, 27)),
               StampTaxRule(rate=0.0005, frm=date(2023, 8, 28))),
)


def settings(capital=500_000, universe=("TEST",)):
    return Settings(universe=list(universe), benchmark="000300",
                    start=date(2024, 1, 1), capital=capital, costs=COSTS, strategies={})


def _bars():
    rows = [
        dict(date="2024-01-02", open=10.0, high=10.5, low=9.8, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.6, low=9.9, close=10.5, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=11.0, high=11.5, low=10.8, close=11.2, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=12.0, high=12.2, low=11.7, close=12.0, volume=1e6, amount=1e7),
        dict(date="2024-01-08", open=12.0, high=12.1, low=11.5, close=11.8, volume=1e6, amount=1e7),
    ]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def _positions(df, values):
    return pd.Series(values, index=df.index, dtype=int)


def test_buy_next_open_and_sell_next_open():
    """目标仓位 d1 收盘=1 → d2 开盘买入；d4 收盘=0 → d5 开盘卖出。全程手算：
    买入 d3(2024-01-03) 开盘？——不对：pos=[1,1,1,0,0] 的 desired=shift(1)=[0,1,1,1,0]，
    d2(01-03) 便是首个 want=1 日，以 10.0×1.001=10.01 成交。
    额度 500,000：499 手=49,900 股，成交额 499,499.0，佣金 max(124.87,5)=124.874750，
    现金余 500,000-499,499-124.87475=376.12525。
    d5(01-08) want=0：以 12.0×0.999=11.988 卖出 49,900 股，成交额 598,201.2，
    佣金 149.5503，印花税(2024→0.05%) 299.1006，净得 597,752.5491，
    pnl = 597,752.5491 - 499,623.87475 = 98,128.67435。"""
    df = _bars()
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 0, 0])}, settings())
    res = bt.run()

    buys = [t for t in res.trades if t.action == "buy"]
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(buys) == 1 and len(sells) == 1

    b = buys[0]
    assert b.date == pd.Timestamp("2024-01-03")
    assert b.price == pytest.approx(10.01)
    assert b.shares == 49_900
    assert b.commission == pytest.approx(124.87475)

    s = sells[0]
    assert s.date == pd.Timestamp("2024-01-08")
    assert s.price == pytest.approx(11.988)
    assert s.stamp == pytest.approx(299.1006)
    assert s.pnl == pytest.approx(98_128.67435)

    # 最终净值 = 初始现金余量 + 卖出净得
    assert res.equity.iloc[-1] == pytest.approx(376.12525 + 597_752.5491)


def test_equity_marks_to_close_daily():
    """d2 买入后，当日净值按收盘价 10.5 估值：
    49,900×10.5 + 376.12525 = 524,326.12525。"""
    df = _bars()
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 0, 0])}, settings())
    res = bt.run()
    assert res.equity.loc["2024-01-03"] == pytest.approx(524_326.12525)
    # 未建仓日净值 = 全额现金
    assert res.equity.loc["2024-01-02"] == pytest.approx(500_000.0)


def test_insufficient_budget_is_skipped_not_crash():
    """茅台场景（评审问题1）：额度 5 万买不起 1 手 10 元×100 股？能。改成价格 600 元：
    1 手 6 万 > 5 万额度 → 跳过并记录，不崩溃、不部分成交。"""
    rows = [dict(date=f"2024-01-{d:02d}", open=600.0, high=610.0, low=590.0, close=600.0,
                 volume=1e6, amount=6e8) for d in (2, 3, 4)]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                    settings(capital=50_000))
    res = bt.run()
    assert res.trades == []
    assert any("资金不足" in r[2] for r in res.skipped)
    assert (res.equity == 50_000.0).all()
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_engine_basic.py -v`
Expected: FAIL（cannot import quant.backtest.engine）

- [ ] **Step 4: 实现 src/quant/backtest/engine.py（本任务先实现基础撮合；涨跌停/T+1/除权在 Task 10 补全，接口一次到位）**

```python
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
        slots = {s: Slot(symbol=s, cash=budget) for s in symbols}
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

    # --- Task 10 中补全涨跌停判定与除权调整；本任务给出占位实现 ---

    def _apply_factor(self, slot: Slot, row: pd.Series) -> None:
        pass  # Task 10 实现

    def _limit_up(self, row: pd.Series, prev_close: float | None) -> bool:
        return False  # Task 10 实现

    def _limit_down(self, row: pd.Series, prev_close: float | None) -> bool:
        return False  # Task 10 实现

    def _try_buy(self, slot: Slot, row: pd.Series, t: pd.Timestamp,
                 trades: list[Trade], skipped: list[tuple]) -> None:
        if self._limit_up(row, slot.last_close):
            skipped.append((t, slot.symbol, "涨停无法买入，顺延"))
            return
        cfg = self.settings.costs
        px = float(row["open"]) * (1 + cfg.slippage)
        qty = int(slot.cash // (px * 100)) * 100
        while qty >= 100 and qty * px + commission(qty * px, cfg) > slot.cash:
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
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_engine_basic.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/quant/backtest tests/test_engine_basic.py
git commit -m "feat: 回测引擎基础撮合（次日开盘/成本/独立额度）"
```

---

### Task 10: 回测引擎·A 股约束 [M3]

**Files:**
- Modify: `src/quant/backtest/engine.py`（实现三个占位方法）
- Test: `tests/test_engine_constraints.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_constraints.py
import pandas as pd
import pytest

from quant.backtest.engine import Backtester
from tests.conftest import make_bars
from tests.test_engine_basic import COSTS, settings, _positions


def _df(rows):
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    return df


def test_limit_up_blocks_buy_then_fills_next_day():
    """d2 相对 d1 收盘(10.0)高开 10% → 买入顺延；d3 正常开盘成交。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=11.0, high=11.0, low=10.9, close=11.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=11.2, high=11.5, low=11.0, close=11.3, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])}, settings()).run()
    assert any("涨停" in r[2] for r in res.skipped)
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_one_word_limit_board_blocks_buy():
    """一字涨停（high==low 且收盘高于昨收）也不可买。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.9, high=10.9, low=10.9, close=10.9, volume=1e3, amount=1e4),
        dict(date="2024-01-04", open=11.0, high=11.8, low=11.0, close=11.5, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])}, settings()).run()
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_limit_down_blocks_sell_then_fills_next_day():
    """positions=[1,0,0,0] → desired=shift(1)=[0,1,0,0]：
    01-03 买入；01-04 目标转 0 但低开 10%（9.0 ≤ 10.0×0.905）触发跌停顺延；01-05 正常卖出。
    注意不可写成 [1,1,0,0]——那样 01-04 的目标仓位仍是 1，根本不会尝试卖出，测不到跌停分支。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.0, high=9.0, low=8.8, close=8.9, volume=1e6, amount=1e7),   # 低开10%
        dict(date="2024-01-05", open=8.8, high=9.0, low=8.5, close=8.6, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")


def test_suspension_keeps_last_close_valuation():
    """d3 无行情行（停牌）：不交易，净值沿用 d2 收盘估值。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.5, low=10.0, close=10.4, volume=1e6, amount=1e7),
        # 01-04 停牌，行已被 pipeline 过滤
        dict(date="2024-01-05", open=10.5, high=10.8, low=10.3, close=10.6, volume=1e6, amount=1e7),
    ])
    two = _df([
        dict(date="2024-01-02", open=5.0, high=5.0, low=5.0, close=5.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=5.0, high=5.1, low=4.9, close=5.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=5.0, high=5.2, low=5.0, close=5.1, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=5.1, high=5.3, low=5.0, close=5.2, volume=1e6, amount=1e7),
    ])
    res = Backtester(
        {"A": df, "B": two},
        {"A": _positions(df, [1, 1, 1]), "B": _positions(two, [0, 0, 0, 0])},
        settings(universe=("A", "B")),
    ).run()
    # 01-04 出现在日历里（B 有行情），A 停牌持仓按 10.4 估值
    assert "2024-01-04" in res.equity.index.strftime("%Y-%m-%d")
    buys = [t for t in res.trades if t.symbol == "A" and t.action == "buy"]
    qty = buys[0].shares
    cash_a = 250_000 - buys[0].shares * buys[0].price - buys[0].commission
    assert res.equity.loc["2024-01-04"] == pytest.approx(cash_a + qty * 10.4 + 250_000)


def test_ex_dividend_adjusts_shares_and_nav_continuous():
    """除权日（评审问题5 的机制验证）：持有 100 股，因子 1.0→1.25，原始价 100→80。
    调整后株数 125，市值 125×80=10,000 = 调整前 100×100 —— 净值连续。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0, volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0, volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-04", open=80.0, high=81.0, low=79.5, close=80.0, volume=1e6, amount=1e8, adj_factor=1.25),
        dict(date="2024-01-05", open=80.0, high=82.0, low=79.0, close=81.0, volume=1e6, amount=1e8, adj_factor=1.25),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1, 1])},
                     settings(capital=50_000)).run()
    b = [t for t in res.trades if t.action == "buy"][0]
    # d2 买入 400 股 @100.1；d4 除权 → 500 股
    assert b.shares == 400
    eq_before = res.equity.loc["2024-01-03"]   # 400×100 + 现金
    eq_after = res.equity.loc["2024-01-04"]    # 500×80 + 现金 —— 应相等
    assert eq_after == pytest.approx(eq_before)


def test_t_plus_1_guard():
    """引擎的 desired=shift(1) 天然不会当日买当日卖，此测试直接驱动私有方法验证保险丝。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.5, low=9.9, close=10.2, volume=1e6, amount=1e7),
    ])
    bt = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1])}, settings())
    from quant.backtest.portfolio import Slot
    slot = Slot(symbol="TEST", cash=0.0, shares=100.0,
                entry_date=pd.Timestamp("2024-01-03"), cost_basis=1000.0,
                last_close=10.0, last_factor=1.0)
    trades, skipped = [], []
    bt._try_sell(slot, df.loc[pd.Timestamp("2024-01-03")], pd.Timestamp("2024-01-03"), trades, skipped)
    assert trades == [] and any("T+1" in r[2] for r in skipped)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_engine_constraints.py -v`
Expected: FAIL（涨跌停/除权用例失败——占位方法返回 False/pass）

- [ ] **Step 3: 实现三个占位方法**

```python
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
```

- [ ] **Step 4: 运行全部引擎测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_engine_basic.py tests/test_engine_constraints.py -v`
Expected: 9 passed

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add src/quant/backtest tests/test_engine_constraints.py
git commit -m "feat: 引擎 A 股约束（涨跌停顺延/T+1/停牌估值/除权调整）"
```

---

### Task 11: 绩效指标 [M4]

**Files:**
- Create: `src/quant/report/__init__.py`, `src/quant/report/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: 写失败测试（手算对照）**

```python
# tests/test_metrics.py
import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report.metrics import compute_metrics


def _equity(values, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_total_return_and_drawdown():
    m = compute_metrics(_equity([100, 110, 99]), trades=[])
    assert m["total_return"] == pytest.approx(-0.01)
    assert m["max_drawdown"] == pytest.approx(-0.10)   # (99-110)/110


def test_trade_stats():
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=-50.0, holding_days=20),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=200.0, holding_days=30),
        Trade("A", "buy", t, 10, 100, 5),   # 买入不参与胜率
    ]
    m = compute_metrics(_equity([100, 101, 102]), trades)
    assert m["n_trades"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["profit_factor"] == pytest.approx(300.0 / 50.0)
    assert m["avg_holding_days"] == pytest.approx(20.0)


def test_flat_equity_no_crash():
    m = compute_metrics(_equity([100, 100, 100]), trades=[])
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/report/metrics.py（并创建空 `report/__init__.py`）**

```python
"""绩效指标（spec §9）。夏普：rf=0 简化口径（中国无风险利率约 1.5–2%，报告中注明）。"""
from __future__ import annotations

import math

import pandas as pd

from quant.backtest.portfolio import Trade

TRADING_DAYS = 252


def compute_metrics(equity: pd.Series, trades: list[Trade]) -> dict:
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    years = len(equity) / TRADING_DAYS
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    daily = equity.pct_change().dropna()
    std = float(daily.std())
    sharpe = float(daily.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    peak = equity.cummax()
    max_dd = float(((equity - peak) / peak).min())

    closed = [t for t in trades if t.pnl is not None]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    loss_sum = abs(sum(t.pnl for t in losses))
    hold = [t.holding_days for t in closed if t.holding_days is not None]

    return {
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "n_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        "profit_factor": (sum(t.pnl for t in wins) / loss_sum) if wins and loss_sum > 0 else None,
        "avg_holding_days": (sum(hold) / len(hold)) if hold else None,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/report tests/test_metrics.py
git commit -m "feat: 绩效指标计算"
```

---

### Task 12: plotly 图表 [M4]

**Files:**
- Create: `src/quant/report/charts.py`
- Test: `tests/test_charts.py`（冒烟：能生成 Figure 且含预期轨迹数，能写出 HTML）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_charts.py
import pandas as pd

from quant.backtest.portfolio import Trade
from quant.report.charts import equity_chart, kline_chart
from tests.conftest import make_bars


def test_equity_chart_smoke(tmp_path):
    idx = pd.bdate_range("2024-01-02", periods=10)
    eq = pd.Series(range(100, 110), index=idx, dtype=float)
    bench = {"沪深300": pd.Series(range(100, 120, 2), index=idx, dtype=float)}
    fig = equity_chart(eq, bench)
    # 2 条净值线 + 1 条回撤 = 3 条轨迹
    assert len(fig.data) == 3
    out = tmp_path / "report.html"
    fig.write_html(out)
    assert out.stat().st_size > 0


def test_kline_chart_smoke():
    rows = [dict(date=f"2024-01-{d:02d}", open=10 + d, high=11 + d, low=9 + d,
                 close=10.5 + d, volume=1e6, amount=1e7) for d in range(2, 8)]
    df = make_bars(rows)
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    # K线 + 买点 + 卖点 = 3 条轨迹
    assert len(fig.data) == 3
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/report/charts.py**

```python
"""plotly 图表（spec §9）：净值+回撤、K线+买卖点。K 线用原始价（所见即真实价位）。"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quant.backtest.portfolio import Trade


def equity_chart(equity: pd.Series, benchmarks: dict[str, pd.Series]) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                        subplot_titles=("净值（归一化）", "回撤"))
    fig.add_trace(go.Scatter(x=equity.index, y=equity / equity.iloc[0],
                             name="策略", line=dict(width=2)), row=1, col=1)
    for name, series in benchmarks.items():
        s = series.dropna()
        fig.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0], name=name,
                                 line=dict(dash="dot")), row=1, col=1)
    dd = equity / equity.cummax() - 1
    fig.add_trace(go.Scatter(x=dd.index, y=dd, name="回撤", fill="tozeroy"), row=2, col=1)
    fig.update_layout(height=600, hovermode="x unified")
    return fig


def kline_chart(df: pd.DataFrame, trades: list[Trade], title: str) -> go.Figure:
    fig = go.Figure(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=title, increasing_line_color="red", decreasing_line_color="green"))  # A股红涨绿跌
    buys = [t for t in trades if t.action == "buy"]
    sells = [t for t in trades if t.action == "sell"]
    if buys:
        fig.add_trace(go.Scatter(x=[t.date for t in buys], y=[t.price for t in buys],
                                 mode="markers", name="买入",
                                 marker=dict(symbol="triangle-up", size=12, color="red")))
    if sells:
        fig.add_trace(go.Scatter(x=[t.date for t in sells], y=[t.price for t in sells],
                                 mode="markers", name="卖出",
                                 marker=dict(symbol="triangle-down", size=12, color="green")))
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=550)
    return fig
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/report/charts.py tests/test_charts.py
git commit -m "feat: 净值/回撤与K线买卖点图表"
```

---

### Task 13: 回测入口与基准 [M4]

**Files:**
- Create: `scripts/run_backtest.py`
- Test: 手动端到端验收（真实数据，联网）

- [ ] **Step 1: 实现 scripts/run_backtest.py**

```python
"""回测入口：python scripts/run_backtest.py [--config config/settings.yaml] [--strategy 名称]
输出到 output/<策略>_<运行时间戳>/：metrics.json、equity.csv、trades.csv、report.html、
kline_<代码>.html × N。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.backtest.engine import Backtester
from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from quant.strategy import build_strategies

OUTPUT = Path("output")


def equal_weight_hold(bars: dict[str, pd.DataFrame]) -> pd.Series:
    """等权买入持有基准：各标的后复权收盘归一化后取均值（分红再投资口径，与策略同权）。"""
    norm = [df["adj_close"] / df["adj_close"].iloc[0] for df in bars.values()]
    return pd.concat(norm, axis=1).ffill().mean(axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--strategy", default=None, help="只跑指定策略，默认全部")
    ap.add_argument("--refresh", action="store_true", help="强制全量刷新行情缓存")
    args = ap.parse_args()

    settings = load_settings(args.config)
    cache = BarCache("data/cache")
    bars: dict[str, pd.DataFrame] = {}
    with BaostockProvider() as provider:
        service = DataService(provider, cache)
        for sym in settings.universe:
            df, warns = service.get_bars(sym, settings.start, refresh=args.refresh)
            for w in warns:
                print(f"[warn] {sym}: {w}")
            bars[sym] = df
            print(f"[data] {sym}: {len(df)} 根K线 ({df.index.min().date()} ~ {df.index.max().date()})")
        bench_close = provider.get_index_daily(
            settings.benchmark, settings.start, pd.Timestamp.now().date())["close"]

    strategies = build_strategies(settings.strategies)
    if args.strategy:
        strategies = [s for s in strategies if s.name == args.strategy]
        if not strategies:
            sys.exit(f"未知策略: {args.strategy}")

    benchmarks = {"沪深300(价格指数,不含分红)": bench_close, "等权买入持有": equal_weight_hold(bars)}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for strat in strategies:
        positions = {s: strat.generate_positions(df) for s, df in bars.items()}
        result = Backtester(bars, positions, settings).run()
        metrics = compute_metrics(result.equity, result.trades)

        run_dir = OUTPUT / f"{strat.name}_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        result.equity.rename("equity").to_csv(run_dir / "equity.csv")
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(run_dir / "trades.csv", index=False)
        pd.DataFrame(result.skipped, columns=["date", "symbol", "reason"]).to_csv(
            run_dir / "skipped.csv", index=False)
        equity_chart(result.equity, benchmarks).write_html(run_dir / "report.html")
        for sym, df in bars.items():
            sym_trades = [t for t in result.trades if t.symbol == sym]
            kline_chart(df, sym_trades, sym).write_html(run_dir / f"kline_{sym}.html")

        print(f"\n===== {strat.name} =====")
        for k, v in metrics.items():
            print(f"  {k:>18}: {v:.4f}" if isinstance(v, float) else f"  {k:>18}: {v}")
        print(f"  报告目录: {run_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 端到端验收（联网，首次拉取约 10 年×10 只日线，需几分钟）**

Run: `.venv/bin/python scripts/run_backtest.py`
Expected:
- 10 只标的各打印 K 线数量（2016 年至今每只约 2400+ 根）
- 两个策略各输出指标表与报告目录；`output/` 下生成 metrics.json/equity.csv/trades.csv/report.html/kline_*.html
- 无未捕获异常；`[warn]` 行如出现，检查内容是否合理（如个别标的历史停牌）

- [ ] **Step 3: 再跑一次验证增量缓存**

Run: `.venv/bin/python scripts/run_backtest.py --strategy ma_cross`
Expected: 数据加载明显加快（走缓存），结果目录正常生成

- [ ] **Step 4: 人工合理性检查（不是测试，是学习环节——打开看！）**

- 打开 `report.html`：策略净值与两条基准形态是否合理（无跳崖式毛刺）
- 打开任一 `kline_*.html`：买卖点是否落在 K 线上、突破点位置是否符合策略定义
- 查看 `skipped.csv`：涨跌停顺延/资金不足记录是否合理

- [ ] **Step 5: Commit**

```bash
git add scripts/run_backtest.py
git commit -m "feat: 回测入口（含基准对比与报告输出）"
```

---

### Task 14: 每日信号 [M5]

**Files:**
- Create: `src/quant/signal/__init__.py`, `src/quant/signal/scan.py`, `scripts/run_daily_signal.py`
- Test: `tests/test_signal_scan.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_signal_scan.py
import pandas as pd

from quant.signal.scan import scan
from quant.strategy.base import Strategy
from tests.conftest import make_bars


class StubStrategy(Strategy):
    name = "stub"

    def __init__(self, positions_by_symbol):
        self.positions_by_symbol = positions_by_symbol

    def generate_positions(self, df):
        sym = df.attrs["symbol"]
        return pd.Series(self.positions_by_symbol[sym], index=df.index, dtype=int)


def _bars(symbol, n=3):
    rows = [dict(date=f"2024-01-{d:02d}", open=10, high=11, low=9, close=10,
                 volume=1e6, amount=1e7) for d in range(2, 2 + n)]
    df = make_bars(rows)
    df.attrs["symbol"] = symbol
    return df


def test_scan_detects_new_buy_and_sell():
    bars = {"AAA": _bars("AAA"), "BBB": _bars("BBB"), "CCC": _bars("CCC")}
    strat = StubStrategy({"AAA": [0, 0, 1],    # 最新一日 0→1：新买入信号
                          "BBB": [1, 1, 0],    # 1→0：新卖出信号
                          "CCC": [1, 1, 1]})   # 状态不变：无信号
    signals = scan(bars, [strat])
    assert {(s["symbol"], s["action"]) for s in signals} == {("AAA", "BUY"), ("BBB", "SELL")}
    assert all(s["strategy"] == "stub" for s in signals)
    assert all(s["date"] == "2024-01-04" for s in signals)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_signal_scan.py -v`
Expected: FAIL（cannot import）

- [ ] **Step 3: 实现 src/quant/signal/scan.py（并创建空 `signal/__init__.py`）**

```python
"""每日信号扫描（spec §11）：最新一根K线的目标仓位相对前一根的变化 = 新信号。"""
from __future__ import annotations

import pandas as pd

from quant.strategy.base import Strategy


def scan(bars: dict[str, pd.DataFrame], strategies: list[Strategy]) -> list[dict]:
    signals: list[dict] = []
    for strat in strategies:
        for sym, df in bars.items():
            pos = strat.generate_positions(df)
            if len(pos) < 2:
                continue
            prev_p, cur = int(pos.iloc[-2]), int(pos.iloc[-1])
            if cur == prev_p:
                continue
            signals.append({
                "date": df.index[-1].strftime("%Y-%m-%d"),
                "symbol": sym,
                "strategy": strat.name,
                "action": "BUY" if cur > prev_p else "SELL",
                "close": float(df["close"].iloc[-1]),
            })
    return signals
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_signal_scan.py -v`
Expected: 1 passed

- [ ] **Step 5: 实现 scripts/run_daily_signal.py**

```python
"""每日信号入口（收盘后手动运行；baostock 数据约 17:30 后更新——spec §11）。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.signal.scan import scan
from quant.strategy import build_strategies

SIGNAL_DIR = Path("output/signals")


def main() -> None:
    settings = load_settings("config/settings.yaml")
    bars = {}
    with BaostockProvider() as provider:
        cal = provider.get_trade_calendar(date.today() - timedelta(days=21), date.today())
        if not cal:
            sys.exit("近三周无交易日？交易日历异常，退出")
        expected = cal[-1]  # 最近一个交易日（含今天）
        service = DataService(provider, BarCache("data/cache"))
        for sym in settings.universe:
            df, warns = service.get_bars(sym, settings.start)
            for w in warns:
                print(f"[warn] {sym}: {w}")
            bars[sym] = df

    stale = {s: df.index.max().date() for s, df in bars.items()
             if df.index.max().date() < expected}
    if len(stale) == len(bars):
        # 全部落后 → 数据源尚未更新（baostock 约 17:30 后才有当日数据）
        print(f"全部标的数据均未更新到 {expected}，稍后再试（最新: {sorted(set(stale.values()))}）")
        sys.exit(1)
    if stale:
        # 部分落后 → 多半是个股停牌，跳过它们继续扫描其余标的
        print(f"以下标的数据落后于 {expected}（多为停牌），本次跳过：")
        for s, d in stale.items():
            print(f"  {s}: 最新 {d}")
        bars = {s: df for s, df in bars.items() if s not in stale}

    signals = scan(bars, build_strategies(settings.strategies))
    print(f"\n===== {expected} 信号 =====")
    if not signals:
        print("今日无新信号")
    else:
        print(pd.DataFrame(signals).to_string(index=False))
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = SIGNAL_DIR / f"{expected}.csv"
    pd.DataFrame(signals, columns=["date", "symbol", "strategy", "action", "close"]).to_csv(
        out, index=False)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 端到端验收（联网）**

Run: `.venv/bin/python scripts/run_daily_signal.py`
Expected: 数据新鲜时输出信号表（多数日子"今日无新信号"属正常）并生成 `output/signals/<date>.csv`；数据未更新时明确提示并退出码 1。两种结果都算通过，记录实际走到哪个分支。

- [ ] **Step 7: Commit**

```bash
git add src/quant/signal scripts/run_daily_signal.py tests/test_signal_scan.py
git commit -m "feat: 每日信号扫描与入口脚本"
```

---

### Task 15: Streamlit 面板 [M6]

**Files:**
- Create: `app/dashboard.py`
- Test: `tests/test_dashboard_import.py`（冒烟）+ 手动验收

- [ ] **Step 1: 实现 app/dashboard.py（纯只读，spec §10）**

```python
"""Streamlit 本地面板：streamlit run app/dashboard.py
三页面：回测报告 / 个股K线 / 今日信号。只读 output/ 与 data/cache/，不触发任何计算。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components  # 显式导入：部分版本下 st.components 不自动可用

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant.backtest.portfolio import Trade  # noqa: E402
from quant.data.cache import BarCache       # noqa: E402
from quant.data.pipeline import prepare_bars  # noqa: E402
from quant.report.charts import kline_chart   # noqa: E402

OUTPUT = ROOT / "output"

METRIC_LABELS = {
    "total_return": "总收益率", "cagr": "年化收益率", "max_drawdown": "最大回撤",
    "sharpe": "夏普比率(rf=0)", "n_trades": "交易次数", "win_rate": "胜率",
    "profit_factor": "盈亏比", "avg_holding_days": "平均持仓天数",
}


def list_runs() -> list[Path]:
    if not OUTPUT.exists():
        return []
    return sorted((p for p in OUTPUT.iterdir()
                   if p.is_dir() and (p / "metrics.json").exists()), reverse=True)


def page_backtest() -> None:
    runs = list_runs()
    if not runs:
        st.info("暂无回测结果。先运行: python scripts/run_backtest.py")
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: p.name)
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    cols = st.columns(4)
    for i, (k, label) in enumerate(METRIC_LABELS.items()):
        v = metrics.get(k)
        text = "—" if v is None else (f"{v:.2%}" if k in
                ("total_return", "cagr", "max_drawdown", "win_rate") else f"{v:.2f}")
        cols[i % 4].metric(label, text)
    components.html((run / "report.html").read_text(encoding="utf-8"),
                    height=650, scrolling=True)
    st.subheader("交易明细")
    st.dataframe(pd.read_csv(run / "trades.csv"), use_container_width=True)
    skipped = run / "skipped.csv"
    if skipped.exists():
        st.subheader("被跳过的订单（涨跌停/资金不足等）")
        st.dataframe(pd.read_csv(skipped), use_container_width=True)


def page_kline() -> None:
    runs = list_runs()
    if not runs:
        st.info("暂无回测结果。先运行: python scripts/run_backtest.py")
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: p.name)
    trades_df = pd.read_csv(run / "trades.csv")
    symbols = sorted({p.stem.replace("kline_", "") for p in run.glob("kline_*.html")})
    sym = st.selectbox("选择标的", symbols)
    raw = BarCache(ROOT / "data" / "cache").load(sym)
    if raw is None:
        st.error(f"缓存中无 {sym} 行情")
        return
    df, _ = prepare_bars(raw)
    sym_trades = [
        Trade(r.symbol, r.action, pd.Timestamp(r.date), r.price, r.shares, r.commission)
        for r in trades_df[trades_df["symbol"].astype(str).str.zfill(6) == sym].itertuples()
    ]
    st.plotly_chart(kline_chart(df, sym_trades, sym), use_container_width=True)


def page_signals() -> None:
    sig_dir = OUTPUT / "signals"
    files = sorted(sig_dir.glob("*.csv"), reverse=True) if sig_dir.exists() else []
    if not files:
        st.info("暂无信号记录。收盘后运行: python scripts/run_daily_signal.py")
        return
    latest = files[0]
    st.subheader(f"最新信号（{latest.stem}）")
    df = pd.read_csv(latest)
    st.dataframe(df, use_container_width=True) if len(df) else st.write("当日无新信号")
    if len(files) > 1:
        st.subheader("历史信号")
        hist = pd.concat([pd.read_csv(f) for f in files[1:]], ignore_index=True)
        st.dataframe(hist, use_container_width=True) if len(hist) else st.write("无")


st.set_page_config(page_title="quant_demo v0.1", layout="wide")
st.sidebar.title("quant_demo")
page = st.sidebar.radio("页面", ["回测报告", "个股K线", "今日信号"])
st.sidebar.caption("本面板纯只读；回测与信号请用命令行运行。策略仅用于学习，不构成投资建议。")
{"回测报告": page_backtest, "个股K线": page_kline, "今日信号": page_signals}[page]()
```

- [ ] **Step 2: 写导入冒烟测试**

```python
# tests/test_dashboard_import.py
import ast
from pathlib import Path


def test_dashboard_syntax_ok():
    """streamlit 脚本无法直接 import 测试（顶层执行 UI 代码），至少保证语法正确。"""
    src = Path("app/dashboard.py").read_text(encoding="utf-8")
    ast.parse(src)
```

- [ ] **Step 3: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_dashboard_import.py -v`
Expected: 1 passed

- [ ] **Step 4: 手动验收**

Run: `.venv/bin/python -m streamlit run app/dashboard.py`
Expected（浏览器逐页检查）:
- 回测报告页：指标卡显示中文标签与百分比格式；net值图可交互；交易明细/跳过订单表可见
- 个股K线页：切换标的正常，买▲卖▼标记落点正确
- 今日信号页：显示最新 CSV 内容（或"暂无"提示）
- 终端 Ctrl+C 退出后无残留进程

- [ ] **Step 5: Commit**

```bash
git add app tests/test_dashboard_import.py
git commit -m "feat: Streamlit 只读面板（回测报告/个股K线/今日信号）"
```

---

### Task 16: README 与收尾 [M6]

**Files:**
- Create: `README.md`

- [ ] **Step 1: 写 README.md**

内容必须包含（面向三个月后忘光了的自己）：
- 一段话说明这是什么（学习用 A 股日线信号系统）与免责声明
- 安装：venv + `pip install -e ".[dev]"`
- 三个使用入口及顺序：`run_backtest.py` → `run_daily_signal.py`（收盘后，约 17:30 后）→ `streamlit run app/dashboard.py`
- 配置说明：settings.yaml 各段含义（股票池/成本/策略参数）
- 架构图（引用 spec 的目录结构）与 spec/plan 文档链接
- 已知近似与局限（spec §15 摘要）：涨跌停判定、非整数股、rf=0 等

- [ ] **Step 2: 全量测试 + 全流程复跑**

Run: `.venv/bin/python -m pytest && .venv/bin/python scripts/run_backtest.py --strategy donchian`
Expected: 测试全部通过；回测正常出报告

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README（用法/配置/已知局限）"
```

---

## 执行注意事项（给执行者）

1. **顺序执行 Task 0→16**，不要跳步；每个任务的测试不过不许 commit。
2. **Task 0 的探针输出是后续的事实基准**：若 baostock 实际字段名/取值与 Task 4 代码不符，以探针为准修正代码与测试，并在 commit message 里说明。
3. **禁止改动 spec 定死的语义**：唐奇安窗口 shift(1)、分段印花税日期、撮合用原始价、desired=shift(1)。如实现中发现 spec 有误，停下来向主会话报告，不要自行变更。
4. 手算断言若与实现相差在浮点误差内（pytest.approx 默认容差），属正常；相差超过 1 分钱级别，先怀疑实现而不是放宽断言。
5. 联网步骤（Task 4 Step 8、Task 13、Task 14 Step 6）失败时重试一次；仍失败则记录报错原文并继续后续离线任务，最后统一回来补验收。



