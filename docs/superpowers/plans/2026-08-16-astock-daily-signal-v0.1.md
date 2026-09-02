# A 股日线信号系统 v0.1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 A 股日线技术分析信号系统 v0.1：数据获取与缓存 → 指标 → 双均线/唐奇安策略 → 自研回测引擎（A 股规则）→ 绩效报告 → 每日信号脚本 → Streamlit 面板。

**Architecture:** 分层架构（data / indicators / strategy / backtest / report / signal / app），策略只见 DataFrame、引擎只见信号、UI 纯只读。信号在后复权价上计算，撮合与约束在原始价上执行，除权除息日按复权因子比率调整持仓股数。

**Tech Stack:** Python ≥3.12（当前 venv 3.14，Task 0 验证）、pandas、pyarrow、baostock、PyYAML、plotly、streamlit、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-astock-daily-signal-v0.1-design.md`（实现中所有取舍以 spec 为准；本计划引用其决策编号，如"决策5=分段印花税"）

**验证状态：** 本计划中的全部离线代码与测试已在临时环境实际执行验证（pandas 3.0.5 / Python 3.14）——42 项单测全部通过，并用合成数据（含除权、停牌）跑通了"数据服务 → 策略 → 回测 → 指标 → 图表 → 信号"端到端链路。所有手算断言数值经实跑核对无误。**未经验证的部分只有 baostock 联网代码**（Task 4/13/14 的网络路径），故 Task 0 的探针脚本是其字段口径的事实基准。

**约定：**
- 所有命令在项目根目录 执行，Python 一律用 `.venv/bin/python`（若 Task 0 降级则为 `.venv312/bin/python`，后续所有命令同步替换）。
- 单元测试一律离线（fixture 数据），网络测试标记 `@pytest.mark.network`，默认跳过。
- 每个任务以 git commit 收尾；测试未过不许提交。

---

## 文件结构总览

```
config/settings.yaml            # 全部可调参数（Task 1）
src/quant/__init__.py
src/quant/config.py             # Settings/Costs 数据类 + YAML 加载（Task 1）
src/quant/data/__init__.py
src/quant/data/cache.py         # parquet 缓存：load/save/merge + load_meta/save_meta（Task 2）
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
    assert s.universe == ("600519", "000333")
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


def test_stamp_rate_three_segments_and_order_independent(tmp_path):
    """回归：印花税若再次调整，最自然的改法就是往列表里追加一段。
    "满足任一边界即返回"的写法会让带 frm 的第二段吞掉其后所有日期，
    静默返回旧税率——错误税率污染每一次回测且永不报错。"""
    body = """
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
%s
  slippage: 0.001
strategies: {}
"""
    seg_a = '    - {until: "2023-08-27", rate: 0.001}'
    seg_b = '    - {from: "2023-08-28", until: "2026-12-31", rate: 0.0005}'
    seg_c = '    - {from: "2027-01-01", rate: 0.00025}'

    for name, segs in [("正序", [seg_a, seg_b, seg_c]), ("乱序", [seg_c, seg_a, seg_b])]:
        cfg = tmp_path / f"s_{name}.yaml"
        cfg.write_text(body % "\n".join(segs), encoding="utf-8")
        c = load_settings(cfg).costs
        assert c.stamp_rate(date(2016, 1, 4)) == 0.001, name
        assert c.stamp_rate(date(2024, 5, 6)) == 0.0005, name
        assert c.stamp_rate(date(2027, 6, 1)) == 0.00025, name


def test_stamp_rate_gap_raises_loudly(tmp_path):
    cfg = tmp_path / "gap.yaml"
    cfg.write_text("""
universe: ["600519"]
benchmark: "000300"
backtest: {start: "2016-01-01", capital: 1000000}
costs:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_tax:
    - {until: "2020-12-31", rate: 0.001}
    - {from: "2022-01-01", rate: 0.0005}
  slippage: 0.001
strategies: {}
""", encoding="utf-8")
    c = load_settings(cfg).costs
    with pytest.raises(ValueError, match="没有覆盖"):
        c.stamp_rate(date(2021, 6, 1))  # 落在缺口里必须报错，不能悄悄返回某个税率


def test_real_config_file():
    """两个 tmp_path 测试都自带 YAML，谁也管不到真正被脚本加载的那个文件。
    这里钉住 config/settings.yaml 本身，防止手改配置时打错字。"""
    s = load_settings("config/settings.yaml")
    assert len(s.universe) == 10
    # 全部 6 位数字：YAML 1.1 会把不加引号的 000333 当八进制解析成 219，
    # str() 之后变成 "219" —— 一个静默错误的股票代码。
    assert all(len(c) == 6 and c.isdigit() for c in s.universe)
    assert s.capital == 5_000_000
    assert s.costs.commission_rate == 0.00025
    assert s.costs.commission_min == 5.0
    assert s.costs.slippage == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 27)) == 0.001
    assert s.costs.stamp_rate(date(2023, 8, 28)) == 0.0005
    assert set(s.strategies) == {"ma_cross", "donchian"}
```

测试文件顶部需 `import pytest`。

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL（ModuleNotFoundError: quant.config）

- [ ] **Step 4: 实现 src/quant/config.py**

```python
"""配置加载：settings.yaml → 不可变数据类。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml


def _to_date(v) -> date:
    # datetime 必须先于 date 判断：isinstance(datetime_obj, date) 为 True，
    # 漏判会让 datetime 一路流到回测循环里才炸（date 与 datetime 无法比较）。
    if isinstance(v, datetime):
        return v.date()
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
        """取 d 当日适用的印花税率。语义是"该日期是否落在本段区间内"（与，不是或）。

        不可写成"满足任一边界就返回"——那样一旦追加第三段（税率再次调整时的
        自然改法），带 frm 的那段会吞掉其后所有日期，静默返回旧税率，
        而错误税率会污染每一次回测且永不报错。当前写法与声明顺序无关。
        """
        for r in self.stamp_tax:
            if r.frm is not None and d < r.frm:
                continue        # 尚未生效
            if r.until is not None and d > r.until:
                continue        # 已经失效
            return r.rate
        raise ValueError(f"没有覆盖 {d} 的印花税规则")


@dataclass(frozen=True)
class Settings:
    universe: tuple[str, ...]      # 用 tuple 而非 list：frozen 只挡重新赋值，挡不住 list 原地修改
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
        universe=tuple(str(s) for s in raw["universe"]),
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


REQUIRED = ["open", "high", "low", "close", "volume", "amount"]


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """手工构造日线 DataFrame。rows 每项须含 date + REQUIRED 全部列，
    可选 adj_factor（默认1.0）/trade_status（默认1）/is_st（默认0）。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col, default in [("adj_factor", 1.0), ("trade_status", 1), ("is_st", 0)]:
        if col not in df.columns:
            df[col] = default
        else:
            # 关键：只有部分行显式给了该列时，pandas 会把其余行填成 NaN。
            # 少了 fillna，"只给一行 trade_status=0"的用例会让全部行都不等于 1 而被过滤光；
            # 少了 astype，dtype 会变成 float，与线上永远是 int 的形态不符
            # （断言 [1, 0] 察觉不到，因为 1.0 == 1）。
            df[col] = df[col].fillna(default).astype(type(default))
    # 必填列漏写只会得到一列 NaN，而 NaN 参与比较恒为 False：
    # 例如 Task 7 的成交额过滤会静默不出信号，测试还"通过"——测的却是错的东西。
    missing = [c for c in REQUIRED if c not in df.columns or df[c].isna().any()]
    if missing:
        raise ValueError(f"make_bars: 必填列缺失或含 NaN: {missing}")
    return df
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_cache.py
import pandas as pd
import pytest

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
    # 缓存层最该保证的是整个 schema 原样回来，不只是 close 这一列
    assert list(loaded.columns) == list(df.columns)
    assert loaded.dtypes.equals(df.dtypes)
    assert loaded.index.name == "date"


def test_load_missing_returns_none(tmp_path):
    assert BarCache(tmp_path).load("600519") is None


def test_meta_roundtrip_and_missing_returns_empty_dict(tmp_path):
    """meta 缺失必须返回 {} 而不是抛异常：本地已有的老缓存全都没有 meta.json，
    抛异常会让整个回测入口在第一只标的上就死掉。"""
    cache = BarCache(tmp_path)
    assert cache.load_meta("600519") == {}
    cache.save_meta("600519", {"covered_start": "2016-01-01"})
    assert cache.load_meta("600519") == {"covered_start": "2016-01-01"}
    assert cache.load_meta("600036") == {}          # 不能串标的


def test_save_meta_is_atomic_and_leaves_no_temp_file(tmp_path):
    cache = BarCache(tmp_path)
    cache.save_meta("600519", {"covered_start": "2016-01-01"})
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "600519.meta.json").exists()


def test_meta_does_not_collide_with_bars_file(tmp_path):
    """meta 与行情同目录同前缀。写 meta 若覆盖了 parquet，缓存直接全毁。"""
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0)])
    cache.save("600519", df)
    cache.save_meta("600519", {"covered_start": "2024-01-01"})
    assert cache.load("600519") is not None
    assert cache.load("600519")["close"].tolist() == [10.0]


def test_merge_dedup_keeps_last():
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    new = make_bars([_row("2024-01-03", 11.5), _row("2024-01-04", 12.0)])
    merged = BarCache.merge(old, new)
    assert len(merged) == 3
    assert merged.loc["2024-01-03", "close"] == 11.5  # 重叠日期以新数据为准
    assert merged.index.is_monotonic_increasing


def test_merge_from_empty_cache():
    """每个标的第一次取数都走这条分支（cached is None），必须钉住。"""
    new = make_bars([_row("2024-01-03", 11.0), _row("2024-01-02", 10.0)])
    merged = BarCache.merge(None, new)
    assert len(merged) == 2
    assert merged.index.is_monotonic_increasing


def test_merge_with_empty_new_preserves_dtypes():
    """pandas 3.0 的 concat 不再忽略空块的 dtype：拼一张空表会把所有列变成 object，
    而 object 下的算术照样不报错——只在当次进程里错，重跑又对，最难查的那类。"""
    old = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 11.0)])
    empty = pd.DataFrame(columns=old.columns, index=pd.DatetimeIndex([], name="date"))
    merged = BarCache.merge(old, empty)
    assert len(merged) == 2
    assert merged.dtypes.equals(old.dtypes)


def test_merge_mismatched_columns_raises():
    """加字段后本地旧缓存全是旧 schema。静默合并会让缺失列变 NaN，
    且 prepare_bars 一条告警都不发——指标全 NaN、净值一条直线、全程不报错。"""
    old = make_bars([_row("2024-01-02", 10.0)])
    new = make_bars([_row("2024-01-03", 11.0)]).drop(columns=["adj_factor"])
    with pytest.raises(ValueError, match="列不一致"):
        BarCache.merge(old, new)


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    cache = BarCache(tmp_path)
    df = make_bars([_row("2024-01-02", 10.0)])
    cache.save("600519", df)
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "600519.parquet").exists()


def test_make_bars_fills_partially_specified_optional_columns():
    """make_bars 被 9 个后续测试文件依赖。只有部分行显式给了 trade_status 时，
    pandas 会把其余行填成 NaN——若不回填默认值，Task 3 的停牌过滤测试会把所有行滤光。"""
    df = make_bars([
        dict(date="2024-01-02", open=10, high=10, low=10, close=10, volume=1000, amount=1e4),
        dict(date="2024-01-03", open=10, high=10, low=10, close=10, volume=0, amount=0,
             trade_status=0),
    ])
    assert df["trade_status"].tolist() == [1, 0]   # 未给的那行必须是 1，不能是 NaN
    assert df["adj_factor"].tolist() == [1.0, 1.0]
    assert df["is_st"].tolist() == [0, 0]
    assert df.index.name == "date"
    # 必须断言 dtype：只比值察觉不到 float 污染（1.0 == 1 恒成立），
    # 而线上 provider 永远产出 int，fixture 造出 float 就是在测线上不存在的形态。
    assert df["trade_status"].dtype == "int64"
    assert df["is_st"].dtype == "int64"
    assert df["adj_factor"].dtype == "float64"


def test_make_bars_rejects_missing_required_column():
    """必填列漏写只会得到一列 NaN，而 NaN 参与比较恒为 False：
    Task 7 的成交额过滤会静默不出信号，测试还"通过"——测的却是错的东西。"""
    with pytest.raises(ValueError, match="必填列"):
        make_bars([
            dict(date="2024-01-02", open=10, high=10, low=10, close=10, volume=1000, amount=1e4),
            dict(date="2024-01-03", open=10, high=10, low=10, close=10, volume=1000),  # 漏 amount
        ])
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: FAIL（ModuleNotFoundError: quant.data）

- [ ] **Step 4: 实现 src/quant/data/cache.py（并创建空 `src/quant/data/__init__.py`）**

```python
"""行情本地缓存：每标的一个 parquet 文件（spec 决策9）+ 一个 meta.json 记录已覆盖区间。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class BarCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def _meta_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.meta.json"

    def load_meta(self, symbol: str) -> dict:
        """已取数区间等元信息。缺文件返回 {}（老缓存自动降级为全量重拉一次后自愈）。"""
        p = self._meta_path(symbol)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_meta(self, symbol: str, meta: dict) -> None:
        tmp = self._meta_path(symbol).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        tmp.replace(self._meta_path(symbol))

    def load(self, symbol: str) -> pd.DataFrame | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        # 先写临时文件再原子替换：刷新 10 只标的时按 Ctrl-C 不会留下半截 parquet
        tmp = self._path(symbol).with_suffix(".parquet.tmp")
        df.to_parquet(tmp)
        tmp.replace(self._path(symbol))

    @staticmethod
    def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
        if old is None or old.empty:
            return new.sort_index()
        if new is None or new.empty:
            # 必须挡：pandas 3.0 的 concat 不再忽略空块的 dtype，
            # 拼一张空表会把 9 列全部污染成 object，而后续算术照样不报错
            return old.sort_index()
        if set(old.columns) != set(new.columns):
            # 静默合并会让缺失列变 NaN，且 prepare_bars 一条告警都不会发：
            # 指标全 NaN → 策略无信号 → 净值一条直线，全程无异常。必须响亮失败。
            raise ValueError(
                f"缓存列与新数据列不一致，请删除缓存目录或用 refresh=True 重拉；"
                f"缓存独有={set(old.columns) - set(new.columns)}，"
                f"新数据独有={set(new.columns) - set(old.columns)}")
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")]  # 重叠日期以新数据为准
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
import pandas as pd
import pytest

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


def test_each_adj_column_scales_its_own_source():
    # 防 adj_high/adj_low 被错写成 close*factor：ATR(Task 5) 直接吃这两列，
    # 错算不报错、只会给出错的止损位。原用例 open==close 且不校验 high/low，抓不到。
    raw = make_bars([dict(date="2024-01-02", open=10.0, high=12.0, low=9.0, close=11.0,
                          volume=1000, amount=11000, adj_factor=2.0)])
    df, _ = prepare_bars(raw)
    r = df.loc["2024-01-02"]
    assert (r["adj_open"], r["adj_high"], r["adj_low"], r["adj_close"]) == (20.0, 24.0, 18.0, 22.0)


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


def test_ohlc_sanity_catches_high_below_close():
    # high > low 成立，但 high < close：只有完整谓词拦得住
    bad = make_bars([dict(date="2024-01-02", open=10.0, high=10.5, low=9.0, close=11.0,
                          volume=1000, amount=11000)])
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


def test_ohlc_sanity_catches_low_above_open():
    bad = make_bars([dict(date="2024-01-02", open=9.5, high=11.0, low=9.8, close=10.0,
                          volume=1000, amount=10000)])
    df, warns = prepare_bars(bad)
    assert len(df) == 0
    assert any("OHLC" in w for w in warns)


@pytest.mark.parametrize("col", ["open", "high", "low", "close", "amount", "adj_factor"])
def test_nan_in_price_column_is_dropped_and_warns(col):
    # baostock 的空串经 pd.to_numeric(errors="coerce") 会变 NaN。NaN 与任何数比较恒为 False，
    # 挡不住 OHLC 谓词；派生出的 adj_* 同样是 NaN，指标层不报错、只给一串 NaN。
    # make_bars 拒收 NaN，所以在构造之后注入（模拟线上数据洞）。
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 10.0), _row("2024-01-04", 10.0)])
    raw.loc[pd.Timestamp("2024-01-03"), col] = float("nan")
    df, warns = prepare_bars(raw)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-04"]
    assert not df[["adj_open", "adj_high", "adj_low", "adj_close"]].isna().any().any()
    assert any("缺失" in w for w in warns)


def test_nan_close_does_not_swallow_jump_warning():
    # NaN 会让自身与次日两天的 pct_change 都变 NaN，把真实跳变告警一并吞掉。
    raw = make_bars([_row("2024-01-02", 10.0), _row("2024-01-03", 10.0),
                     _row("2024-01-04", 20.0), _row("2024-01-05", 20.0)])
    raw.loc[pd.Timestamp("2024-01-03"), "close"] = float("nan")
    _, warns = prepare_bars(raw)
    assert any("跳变" in w for w in warns)  # 剔除 NaN 行后 10 → 20 的跳变必须仍被发现


def test_suspended_rows_removed_before_adj_and_warnings():
    # 钉住清洗顺序：停牌行必须先剔除，再派生 adj_* / 出告警。
    # 停牌行故意带 is_st=1 与异常 adj_factor：顺序一旦调换，ST 告警会被它触发。
    raw = make_bars([
        _row("2024-01-02", 10.0),
        _row("2024-01-03", 10.0, trade_status=0, volume=0, is_st=1, adj_factor=99.0),
        _row("2024-01-04", 10.0),
    ])
    df, warns = prepare_bars(raw)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-04"]
    assert (df["adj_factor"] == 1.0).all()
    assert not any("ST" in w for w in warns)


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

    df = df[(df["trade_status"] == 1) & (df["volume"] > 0)].copy()

    # NaN（如 baostock 空串经 to_numeric 转换而来）与任何数比较恒为 False：既躲得过下面的
    # OHLC 谓词，又会让 adj_* 全变 NaN、让自身与次日的 pct_change 双双失效（跳变漏报）。
    na_price = df[["open", "high", "low", "close", "amount", "adj_factor"]].isna().any(axis=1)
    if na_price.any():
        warns.append(f"价格/成交额缺失 {int(na_price.sum())} 行，已剔除: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[na_price]]}")
        df = df[~na_price].copy()

    bad_ohlc = (df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1)) \
        | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad_ohlc.any():
        warns.append(f"OHLC 逻辑异常 {int(bad_ohlc.sum())} 行，已剔除: "
                     f"{[d.strftime('%Y-%m-%d') for d in df.index[bad_ohlc]]}")
        df = df[~bad_ohlc].copy()

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
Expected: 17 passed

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


ALL_DAYS = [d.date() for d in pd.bdate_range("2024-01-01", "2024-01-31")]


class FakeProvider(DataProvider):
    def __init__(self):
        self.calls: list[tuple] = []
        self.data = make_bars([_row(str(d), 10.0 + i) for i, d in enumerate(ALL_DAYS)])

    def get_daily_bars(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        mask = (self.data.index.date >= start) & (self.data.index.date <= end)
        return self.data[mask]

    def get_index_daily(self, index_code, start, end):
        raise NotImplementedError

    def get_trade_calendar(self, start, end):
        return [d for d in ALL_DAYS if start <= d <= end]


def test_first_fetch_pulls_full_range_and_caches(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert len(df) == len(ALL_DAYS)
    assert provider.calls[0] == ("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert cache.load("600519") is not None


def test_second_fetch_is_incremental_with_overlap(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 20))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    cached_max = date(2024, 1, 19)  # 2024-01-20 是周六，最后一个工作日为 19 日
    # 增量：不从头拉，但要回拉 OVERLAP_DAYS 天重叠（不是"最新日+1天"）
    assert provider.calls[1][1] == cached_max - timedelta(days=OVERLAP_DAYS)
    assert provider.calls[1][1] > date(2024, 1, 1)


def test_incremental_still_works_when_start_is_a_holiday(tmp_path):
    """start 落在非交易日是生产常态（settings.yaml 的 2016-01-01 是元旦）。
    若用"缓存最早一根 bar 的日期 > start"判头部缺口，首根 bar 恒晚于 start，
    该条件永远为真 → 增量分支变死代码，每次运行全量重拉 10 年且无任何告警。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)          # 周日；FakeProvider 首个交易日是 2024-01-01
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    df, _ = svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    cached_max = ALL_DAYS[-1]                   # 2024-01-31
    assert provider.calls[1][1] == cached_max - timedelta(days=OVERLAP_DAYS)
    assert len(df) == len(ALL_DAYS)             # 增量不能少给数据


def test_legacy_cache_without_meta_self_heals_after_one_full_fetch(tmp_path):
    """本地已有的老缓存没有 meta.json。允许它全量重拉一次补齐元信息，
    但必须**只此一次**——否则修复等于没修，每次运行照旧全量重拉。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)           # 用非交易日，才能证伪"按首根 bar 判缺口"
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    cache._meta_path("600519").unlink()          # 模拟修复前写下的老缓存
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[1][1] == holiday_start                 # 第 2 次：全量，自愈
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[2][1] == ALL_DAYS[-1] - timedelta(days=OVERLAP_DAYS)  # 第 3 次：增量


def test_head_backfill_then_next_run_is_incremental(tmp_path):
    """回补完头部缺口后，covered_start 必须记成更早的那个 start，
    否则下一次运行又被判成"头部有缺口"，永远全量重拉。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    holiday_start = date(2023, 12, 31)           # 用非交易日，才能证伪"按首根 bar 判缺口"
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31))
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))      # 回补头部
    svc.get_bars("600519", holiday_start, date(2024, 1, 31))
    assert provider.calls[1][1] == holiday_start
    assert provider.calls[2][1] == ALL_DAYS[-1] - timedelta(days=OVERLAP_DAYS)
    assert cache.load_meta("600519")["covered_start"] == "2023-12-31"


def test_refresh_resets_covered_start_to_the_new_request(tmp_path):
    """refresh 会丢掉旧缓存整表。covered_start 若仍停在更早的日期，
    缓存里其实没有的那段历史会被当成"已覆盖"，此后再也不会补。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31), refresh=True)
    assert cache.load_meta("600519")["covered_start"] == "2024-01-22"
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert provider.calls[2][1] == date(2024, 1, 1)   # 头部确实缺，必须回补
    assert len(df) == len(ALL_DAYS)


def test_overlap_refetch_corrects_stale_intraday_bar(tmp_path):
    """盘中运行会把当天未收盘的 bar 写进缓存。回拉重叠 + merge 的 keep='last'
    必须能用收盘后的正确数据覆盖它——否则这根脏 bar 永久污染此后所有回测。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 19))

    # 篡改缓存中最后一根，模拟盘中抓到的半截 K 线
    dirty = cache.load("600519")
    dirty.loc[dirty.index.max(), "close"] = 999.0
    cache.save("600519", dirty)
    assert cache.load("600519")["close"].iloc[-1] == 999.0

    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    good = provider.data.loc[pd.Timestamp("2024-01-19"), "close"]
    assert df.loc[pd.Timestamp("2024-01-19"), "close"] == good  # 已被修正


def test_refresh_forces_full_fetch(tmp_path):
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31), refresh=True)
    assert provider.calls[1][1] == date(2024, 1, 1)


def test_returned_range_is_clamped_to_request(tmp_path):
    """缓存比请求区间长是常态。不夹住 end，样本外的行会被悄悄喂给回测且无告警。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))   # 缓存整月
    df, _ = svc.get_bars("600519", date(2024, 1, 8), date(2024, 1, 10))
    assert df.index.min().date() >= date(2024, 1, 8)
    assert df.index.max().date() <= date(2024, 1, 10)   # 不夹 end 时这里会拿到 1/31


def test_earlier_start_backfills_cache_head(tmp_path):
    """先跑近期、后来想回溯更早——缓存头部的缺口必须回补，
    否则 get_bars 静默返回比请求区间更短的数据，回测在自己没要过的区间上出结论。"""
    provider, cache = FakeProvider(), BarCache(tmp_path)
    svc = DataService(provider, cache)
    svc.get_bars("600519", date(2024, 1, 22), date(2024, 1, 31))
    df, _ = svc.get_bars("600519", date(2024, 1, 1), date(2024, 1, 31))
    assert len(df) == len(ALL_DAYS)                     # 修复前只有 8 行且无告警
    assert provider.calls[1][1] == date(2024, 1, 1)
```

测试文件顶部需 `import pandas as pd`、`from datetime import date, timedelta`，并从 `quant.data.service` 导入 `OVERLAP_DAYS`。

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

OVERLAP_DAYS = 5  # 增量取数时回拉的重叠天数，用于覆盖盘中运行留下的未收盘 bar


class DataService:
    def __init__(self, provider: DataProvider, cache: BarCache):
        self.provider = provider
        self.cache = cache

    def get_bars(self, symbol: str, start: date, end: date | None = None,
                 refresh: bool = False) -> tuple[pd.DataFrame, list[str]]:
        end = end or date.today()
        cached = None if refresh else self.cache.load(symbol)
        covered_raw = None if refresh else self.cache.load_meta(symbol).get("covered_start")
        covered_start = date.fromisoformat(covered_raw) if covered_raw else None
        if cached is None or cached.empty:
            fetch_start = start
        elif covered_start is None or covered_start > start:
            # 缓存头部有缺口（本次 start 早于**已请求过的**最早日期）。仍按"缓存最新日回拉重叠"
            # 取数的话，缺的那段历史永远补不回来，get_bars 会静默返回比请求区间更短的数据。
            #
            # 判据必须用"已请求过的 start"，不能用"缓存里最早那根 bar 的日期"：
            # start 落在非交易日时（settings.yaml 的 2016-01-01 是元旦），首根 bar 恒晚于 start，
            # 该条件永远为真 → 增量分支变成死代码，每次运行都全量重拉 10 年，缓存形同虚设且无告警。
            fetch_start = start
        else:
            # 回拉 OVERLAP_DAYS 天重叠，而不是从"最新日+1天"开始。
            # 原因：若曾在交易日盘中运行过，当天那根**未收盘**的 K 线会被写进缓存；
            # 用"最新日+1天"会永远跳过它，这根错误的 bar 将永久污染此后所有回测且无告警。
            # 重叠重拉让 merge 的 keep="last" 自动修正，代价只是每次多几行网络数据。
            fetch_start = max(start, cached.index.max().date() - timedelta(days=OVERLAP_DAYS))
        if fetch_start <= end:
            new = self.provider.get_daily_bars(symbol, fetch_start, end)
            merged = self.cache.merge(cached, new)  # merge 自己会处理 new 为空
            self.cache.save(symbol, merged)
            self.cache.save_meta(symbol, {"covered_start":
                                          min(fetch_start, covered_start or fetch_start).isoformat()})
        else:
            merged = cached
        if merged is None or merged.empty:
            raise ValueError(f"{symbol}: 无可用数据（{start}~{end}）")
        df, warns = prepare_bars(merged)
        # 必须双向裁剪：缓存通常比本次请求的区间更长，不夹住 end 会把 end 之后的行
        # 一并交给回测（样本外区间被悄悄吃掉），且完全无告警。
        in_range = (df.index.date >= start) & (df.index.date <= end)
        return df[in_range], warns
```

注意：缓存里已有的历史行不会因为后来的分红而失效——baostock 的**后**复权因子是"从上市日累积"的口径，新除权只新增新日期的因子记录，旧行照旧有效（这正是 spec 决策1 选后复权的第二个原因）。

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_service.py -v`
Expected: 6 passed

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
    # 翻页请求失败时 next() 只是把服务端错误码写进 rs.error_code 后 return False，不抛异常，
    # 与"读完了"无法区分。少查这一次，残缺的半截历史会被当成完整数据喂给回测。
    _check(rs)
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

- [ ] **Step 7: 写 provider 单元测试（离线）+ 网络集成测试（默认跳过）**

离线部分 `tests/test_baostock_provider.py` 用假结果集覆盖 `_fetch` 的失败路径：baostock 的
`next()` 翻页失败时只把服务端错误码写进 `rs.error_code` 后 `return False`，不抛异常，
因此循环结束后必须再 `_check(rs)` 一次，否则半截历史会被静默当成完整数据。

```python
# tests/test_baostock_provider.py —— 不联网的单元测试（联网用例见 test_baostock_integration.py）
import pytest

from quant.data.baostock_provider import _fetch, to_bs_code


class FakeResultSet:
    """模拟 baostock 的 ResultData。

    关键行为（照抄 baostock/data/resultset.py 的 next()）：翻页请求失败时**不抛异常**，
    只是把服务端返回的错误码写进 self.error_code 然后 return False——与"数据读完了"
    在调用方看来完全一样。
    """
    fields = ["date", "close"]

    def __init__(self, rows, fail_after=None):
        self._rows = rows
        self._i = 0
        self._fail_after = fail_after
        self.error_code = "0"
        self.error_msg = ""

    def next(self):
        if self._fail_after is not None and self._i == self._fail_after:
            self.error_code = "10002"
            self.error_msg = "网络接收错误"
            return False
        return self._i < len(self._rows)

    def get_row_data(self):
        row = self._rows[self._i]
        self._i += 1
        return row


def test_to_bs_code():
    assert to_bs_code("600519") == "sh.600519"
    assert to_bs_code("000333") == "sz.000333"
    assert to_bs_code("300750") == "sz.300750"


def test_fetch_reads_all_rows():
    df = _fetch(FakeResultSet([["2024-01-02", "10"], ["2024-01-03", "11"]]))
    assert list(df["close"]) == ["10", "11"]


def test_fetch_raises_when_first_page_failed():
    rs = FakeResultSet([])
    rs.error_code, rs.error_msg = "10001", "登录失效"
    with pytest.raises(RuntimeError, match="10001"):
        _fetch(rs)


def test_fetch_raises_when_pagination_fails_midway():
    """翻到第二页时服务端报错：修复前循环"正常"结束，半截数据被当成完整历史返回，
    回测于是在残缺行情上跑完且无任何异常。"""
    rows = [["2024-01-02", "10"], ["2024-01-03", "11"], ["2024-01-04", "12"]]
    with pytest.raises(RuntimeError, match="10002"):
        _fetch(FakeResultSet(rows, fail_after=2))
```

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

Run: `.venv/bin/python -m pytest tests/test_service.py tests/test_baostock_provider.py -v && .venv/bin/python -m pytest -m network -v`
Expected: 全部 passed（网络测试若因网络环境失败，记录原因；字段不符则回到 Step 6 按探针输出修正）

- [ ] **Step 9: Commit**

```bash
git add src/quant/data tests/test_service.py tests/test_baostock_provider.py tests/test_baostock_integration.py
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
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 11.0])   # 末段非对称：均值≠中位数
    out = ma(s, 3)
    assert pd.isna(out.iloc[1])                # 暖机期不得出值（min_periods 默认=n）
    assert out.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(6.0)   # (3+4+11)/3


def test_rolling_high_low_include_current_bar():
    s = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0, 0.5])
    # 含当日（spec §6）：若误写成 shift(1) 再滚动，下面两个值分别变成 4.0 / 1.0
    assert rolling_high(s, 3).iloc[4] == 5.0   # max(4,1,5)
    assert rolling_low(s, 3).iloc[5] == 0.5    # min(1,5,0.5)
    # 暖机期必须是 NaN：若 min_periods=1，唐奇安(Task 7)会拿"仅 1~2 根"的极值当通道，
    # 在样本开头凭空造出突破信号。
    assert pd.isna(rolling_high(s, 3).iloc[1])
    assert pd.isna(rolling_low(s, 3).iloc[1])


def test_atr_hand_computed():
    # b2 向下跳空（TR 由"与昨收的缺口"决定）、b3 不跳空且振幅最大（TR 由日内 high-low 决定），
    # 两种主导情形各覆盖一次；只覆盖其中一种，另一半算错也测不出来。
    df = make_bars([
        dict(date="2024-01-02", open=10, high=11, low=9, close=10, volume=1, amount=1),
        dict(date="2024-01-03", open=5, high=6, low=4, close=5, volume=1, amount=1),
        dict(date="2024-01-04", open=5, high=10, low=2, close=6, volume=1, amount=1),
    ])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * 2.0           # 后复权价 ≠ 原始价，钉死 atr 只读 adj_*
    out = atr(df, 2)
    # 后复权口径：TR2 = max(12-8, |12-20|, |8-20|) = 12（缺口项胜）
    #             TR3 = max(20-4, |20-10|, |4-10|) = 16（日内振幅胜）
    assert pd.isna(out.iloc[0])
    # 首根没有昨收，TR 按惯例退化为当日 high-low=4。这条同时钉住 max(axis=1) 的 skipna 语义：
    # 若改成 skipna=False 想让缺失更响亮，首根 TR 会变 NaN，整条 ATR 暖机静默推迟一天。
    assert out.iloc[1] == pytest.approx(8.0)   # (4+12)/2
    assert out.iloc[2] == pytest.approx(14.0)  # (12+16)/2
```

> **样本设计约束（评审后加固，勿随手简化）**：三个用例的样本都是为「证伪」挑的，改数字前先想清楚它钉的是什么。
> ma 末位取 11 是为了让均值≠中位数；rolling 序列末位补 0.5 是因为 `[3,1,4,1,5]` 上 `rolling_low` 含不含当日都等于 1.0（断言恒真）；
> ATR 必须一根跳空、一根不跳空——全跳空则 `h - l` 写反也测不出，全不跳空则丢掉缺口项也测不出。
> `adj_* = 原始价 × 2` 用于钉死 `atr` 只读后复权列（跨除权日读原始价会让 ATR 止损位整体错位）。

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
import pandas as pd

from quant.strategy.ma_cross import MaCross
from tests.conftest import make_bars


def _bars(adj_closes, factors=None):
    """按后复权价路径构造行情，raw = adj / factor。
    factor 必须出现台阶（除权日），否则 raw 与 adj_* 恒等、均线比较又是尺度不变的
    （ma(kx,n) > ma(kx,m) ⟺ ma(x,n) > ma(x,m)），
    "信号误用原始价"这一错法就测不出来（spec 决策1）。"""
    factors = factors or [1.0] * len(adj_closes)
    rows = [dict(date=f"2024-01-{i+1:02d}", open=a / f, high=a / f, low=a / f,
                 close=a / f, volume=1000, amount=a / f * 1000, adj_factor=f)
            for i, (a, f) in enumerate(zip(adj_closes, factors))]
    df = make_bars(rows)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * df["adj_factor"]
    return df


# 复权价：先升(暖机) → 走平 → 除权后继续升；参数用小窗口便于手推
ADJ = [10.0, 11.0, 12.0, 13.0, 13.0, 13.0, 13.0, 13.0, 14.0, 15.0, 16.0, 17.0]
FAC = [1.0] * 8 + [2.0] * 4          # idx8 为 10 送 10 除权日，raw 价腰斩


def test_positions_follow_ma_state():
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ, FAC))
    assert pd.api.types.is_integer_dtype(pos)      # 契约是 {0,1} int；bool 会让下游 shift(1) 变 object
    assert set(pos.unique()) <= {0, 1}
    # 逐日手推 ma2 vs ma4（后复权价）。整条路径都断言，才能证伪"信号误用原始价"：
    # 除权日 idx8 起 raw 腰斩，改用 raw 会在 idx8~10 空仓，凭空造出一次往返交易
    assert list(pos) == [0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1]
    # 暖机期：slow 未成形必须为 0。前缀取"上涨"而非横盘——横盘时 f == s，
    # min_periods=1 之类的错法也返回 0，断言会退化成永真
    assert (pos.iloc[:3] == 0).all()
    assert pos.iloc[6] == 0                        # 走平段 fast == slow，判据是 > 而非 >=


# 除权后先涨后跌，检验 fast 跌回 slow 之下能离场
ADJ_DOWN = ADJ + [16.0, 14.0, 12.0, 10.0]
FAC_DOWN = [1.0] * 8 + [2.0] * 8


def test_positions_exit_on_downtrend():
    pos = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN, FAC_DOWN))
    assert pos.iloc[11] == 1          # 上升段持有
    assert pos.iloc[-1] == 0          # 下跌段 fast 跌回 slow 之下 → 空仓


def test_no_lookahead():
    # 未来函数错位测试（spec §13）：截断未来数据，历史信号不得改变。
    # 逐个截断点都要查——单一截断点会挑到"盲点"：shift(-1) 这类偷看未来的错法，
    # 恰好在某些 n 上截断前后都是 0，一个点测不出来（实测 n=13 即为盲点）
    full = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN, FAC_DOWN))
    for n in range(4, len(ADJ_DOWN)):
        trunc = MaCross(fast=2, slow=4).generate_positions(_bars(ADJ_DOWN[:n], FAC_DOWN[:n]))
        assert list(full.iloc[:n]) == list(trunc), f"截断到第 {n} 天后，历史信号被改写"
```

> 测试强度说明（变异测试实证）：`_bars` 若让 adj_* 与原始价恒等，则"信号误用原始价"、
> 去掉 `.astype(int)`、`min_periods=1` 三种错法都能全绿存活。fixture 必须带除权台阶、
> 断言必须查 dtype 与整条路径，7 个变异才会全部被杀死。

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

**评审加固（Task 7 复审）**：`_bars` 必须支持 `factors` 台阶，否则 adj_* 与原始价恒等、"突破判定误用原始价"测不出来；成交额阈值要用"放量但只有 1.3 倍"的序列，平稳成交额对 `amount_ratio` 零约束；三个窗口必须有一组互不相同的取值，默认 entry_n == amount_n == 20 会让接线错误静默通过。

```python
# tests/test_donchian.py
from quant.strategy.donchian import Donchian
from tests.conftest import make_bars


def _bars(closes, amounts, factors=None):
    """closes 传的是**后复权价**路径，raw = adj / factor。
    factor 必须能出现台阶（除权日），否则 raw 与 adj_* 恒等，
    "突破判定误用原始价"这一错法就测不出来（spec 决策1，与 test_ma_cross 同一套路）。"""
    factors = factors or [1.0] * len(closes)
    rows = [dict(date=f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                 open=c / f, high=c / f, low=c / f, close=c / f,
                 volume=1000, amount=a, adj_factor=f)
            for i, (c, a, f) in enumerate(zip(closes, amounts, factors))]
    df = make_bars(rows)
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] * df["adj_factor"]
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
    # 逐日锁死整条路径，而不是只查两个端点：
    # 入场在 idx20（第一个"创新高 + 成交额 2.07 倍于前 20 日均额"的日子；idx24 起量比
    # 跌回 1.47 < 1.5，所以只有 idx20 这一次入场机会）；
    # 出场在 idx32（close=19.0 < 前 10 日最低 21.0，idx30/31 的 23.0/21.0 都还没跌破）。
    assert list(pos) == [0] * 20 + [1] * 12 + [0] * 5
    assert pos.iloc[29] == 1     # 上升末端仍持有
    assert pos.iloc[-1] == 0     # 跌破前 10 日最低 → 空仓


def test_no_entry_without_amount_expansion():
    closes = [10.0 + i * 0.5 for i in range(40)]
    amounts = [1e6] * 40                                    # 成交额平稳 → 量能条件不满足
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0


def test_signal_uses_adjusted_price():
    """spec 决策1：突破判定必须用后复权价 adj_close，用原始价会被除权缺口骗出假信号。

    后复权价一路上涨、期间遇 10 送 10 除权日：正确实现全程持有；
    误用原始价则在除权日因 raw 腰斩被判"跌破前 10 日最低"而假出场，
    此后量能条件不再满足、再也进不来 —— 凭空造出一次往返交易。
    """
    n = 45
    closes = [10.0 + i * 0.5 for i in range(n)]     # 后复权价每天创新高
    factors = [1.0] * 30 + [2.0] * 15               # idx30 为 10 送 10 除权日，raw 价腰斩
    amounts = [1e6] * 20 + [2.5e6] + [1e6] * 24     # 只有 idx20 这一天放量，仅一次入场机会
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts, factors))
    assert list(pos) == [0] * 20 + [1] * 25


def test_amount_ratio_threshold_is_enforced():
    """放量但未达 amount_ratio 倍数时不得入场。

    test_no_entry_without_amount_expansion 用的是"完全平稳"的成交额（amt 恰好等于均额），
    靠严格 > 就挡住了 —— 把 amount_ratio 改成 1.0、甚至把倍数整个删掉，那条测试照样通过。
    要锁住阈值倍数，必须用"确实在放量、但只有 1.3 倍"的序列。
    """
    n = 45
    closes = [10.0 + i * 0.5 for i in range(n)]      # 每天创新高，价格条件恒满足
    amounts = [1e6] * 20
    for i in range(20, n):
        amounts.append(1.3 * sum(amounts[i - 20:i]) / 20)   # 恒为前 20 日均额的 1.3 倍
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert pos.sum() == 0, "成交额只有前均额的 1.3 倍，未达 1.5 倍阈值，不应入场"


def test_amount_baseline_excludes_today():
    """成交额基准是"前 amount_n 日均额"，当日那根放量柱不能算进分母。

    amount_n=5、当日 1.6e6 vs 前 5 日均额 1.0e6 → 1.6 倍 > 1.5 倍，应当入场；
    若基准误含当日，分母变成 (4×1.0+1.6)/5 = 1.12e6，阈值抬到 1.68e6 → 反而进不去。
    """
    closes = [10.0 + i * 0.5 for i in range(30)]
    amounts = [1e6] * 20 + [1.6e6] + [1e6] * 9
    pos = Donchian(entry_n=20, exit_n=10, amount_n=5, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert list(pos) == [0] * 20 + [1] * 10


def test_window_params_are_wired_distinctly():
    """entry_n / exit_n / amount_n 三个窗口各接各的。

    默认参数下 entry_n == amount_n == 20，接线接错也测不出来；这里三个值互不相同：
    - 入场 idx15：20.0 突破前 15 日最高 17.0，且 2e6 > 1.5 × 前 3 日均额 1e6
      （若 amount_n 误接 entry_n=15，基准变成前 15 日均额 2.6e6，阈值 3.9e6 → 全程空仓）
    - 出场 idx23：22.0 < 前 5 日最低 23.0
      （若出场窗口误接 entry_n=15，前 15 日最低是 14.0 → 永不出场，一路持有到底）
    """
    closes = ([10.0 + i * 0.5 for i in range(15)]           # idx0~14 暖机，10.0 → 17.0
              + [20.0, 21.0, 22.0, 23.0, 24.0, 25.0]        # idx15 突破入场，随后续涨
              + [24.0, 23.0, 22.0, 21.0, 20.0]              # idx21~25 回落
              + [20.0] * 4)                                 # idx26~29 走平
    amounts = [3e6] * 12 + [1e6] * 3 + [2e6] + [1e6] * 14   # 前期活跃 → 缩量整理 → 放量突破
    pos = Donchian(entry_n=15, exit_n=5, amount_n=3, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    assert list(pos) == [0] * 15 + [1] * 8 + [0] * 7


def test_entry_requires_strictly_greater():
    """入场的两个判据都是严格大于：等于阈值不算突破，也不算放量。"""
    closes = [10.0 + i * 0.5 for i in range(20)] + [19.5, 20.0] + [20.0] * 8
    amounts = [1e6] * 20 + [2e6, 1.575e6] + [1e6] * 8
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    # idx20：close 19.5 恰等于前 20 日最高 19.5（量能充足），> 而非 >= 才不入场
    assert pos.iloc[20] == 0, "收盘等于前 N 日最高不算突破，价格判据须为 >"
    # idx21：close 20.0 确实突破，但成交额 1.575e6 恰等于 1.5 × 前 20 日均额 1.05e6
    assert pos.iloc[21] == 0, "成交额恰等于阈值不算放量，量能判据须为 >"
    assert pos.sum() == 0


def test_exit_requires_strictly_less():
    """出场判据是严格小于：收盘恰好等于前 exit_n 日最低不算跌破，继续持有。"""
    closes = [10.0 + i * 0.5 for i in range(20)] + [20.0] * 15   # 突破后走平在 20.0
    amounts = [1e6] * 20 + [2e6] + [1e6] * 14
    pos = Donchian(entry_n=20, exit_n=10, amount_n=20, amount_ratio=1.5).generate_positions(
        _bars(closes, amounts))
    # idx30 起前 10 日最低恒为 20.0，与当日收盘相等；判据若写成 <= 会在这里假出场
    assert list(pos) == [0] * 20 + [1] * 15


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
    budget: float = 0.0        # 固定额度=初始本金/N，不随权益浮动（spec §8）
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
    return Settings(universe=tuple(universe), benchmark="000300",
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


def _mk(dates, prices, factor=1.0):
    """按给定原始价造日线；adj_* = 原始价 × factor（factor≠1 时二者可区分）。"""
    df = make_bars([dict(date=d, open=p, high=p * 1.02, low=p * 0.98, close=p,
                         volume=1e6, amount=1e7, adj_factor=factor)
                    for d, p in zip(dates, prices)])
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * factor
    return df


D3 = ["2024-01-02", "2024-01-03", "2024-01-04"]


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
    assert s.holding_days == 5   # 01-03 买入 → 01-08 卖出，自然日

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


def test_budget_is_fixed_and_does_not_compound_after_a_winning_round():
    """spec §8：每只资金上限 = **初始本金**/N，固定额度，不随权益浮动。
    500,000 单标的，价 10 买入 → 价 20 卖出（翻倍），再次建仓时**仍只能动用 500,000**，
    赚到的部分留作闲置现金，不参与下一轮建仓。

    01-03 开 10.0：px=10.01，499 手=49,900 股，成交额 499,499.0，佣金 124.87475，
                   现金余 376.12525。
    01-05 开 20.0：px=19.98，成交额 997,002.0，佣金 249.2505，印花税 498.501，
                   净得 996,254.2485 → 现金 996,630.37375（≈ 本金的 1.99 倍）。
    01-08 开 20.0：px=20.02，可动用额度 = min(996,630.37375, 500,000) = 500,000
                   → 249 手=24,900 股，成交额 498,498.0，佣金 124.6245，
                   现金余 996,630.37375-498,498-124.6245 = 498,007.74925。
    若额度随权益复利（slot 现金全吃），第二次会买成 49,700 股、投入 994,994.0，
    相当于把固定额度放大到 1.99 倍，系统性虚增此后全部收益与回撤。
    末日净值 = 498,007.74925 + 24,900×20.0 = 996,007.74925。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04",
             "2024-01-05", "2024-01-08", "2024-01-09"]
    df = _mk(dates, [10.0, 10.0, 10.0, 20.0, 20.0, 20.0])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 1, 1, 1])},
                     settings(capital=500_000)).run()

    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 2
    b2 = buys[1]
    assert b2.date == pd.Timestamp("2024-01-08")
    assert b2.shares == 24_900                      # 复利膨胀时会是 49,700
    assert b2.price == pytest.approx(20.02)
    assert b2.commission == pytest.approx(124.6245)
    # 单次建仓投入（含佣金）不得超过固定额度
    assert b2.shares * b2.price + b2.commission <= 500_000
    assert res.equity.iloc[-1] == pytest.approx(996_007.74925)


def test_each_symbol_gets_independent_budget():
    """每标的独立额度 = 本金/N，互不挪用。本金 50 万、2 个标的 → 每腿 25 万。
    A(开 10.0)：px=10.01，25 万够 249 手 → 24,900 股，成交额 249,249.0，佣金 62.31225。
    B(开 20.0)：px=20.02，25 万够 124 手 → 12,400 股，成交额 248,248.0，佣金 62.062。
    若额度错写成全额本金，A 会买成 49,900 股（两腿合计 2 倍杠杆）。"""
    a, b = _mk(D3, [10.0] * 3), _mk(D3, [20.0] * 3)
    bt = Backtester({"A": a, "B": b},
                    {"A": _positions(a, [1, 1, 1]), "B": _positions(b, [1, 1, 1])},
                    settings(capital=500_000, universe=("A", "B")))
    res = bt.run()
    ta = next(t for t in res.trades if t.symbol == "A")
    tb = next(t for t in res.trades if t.symbol == "B")
    assert ta.shares == 24_900 and tb.shares == 12_400
    assert ta.commission == pytest.approx(62.31225)
    assert tb.commission == pytest.approx(62.062)
    # 两腿现金余额独立：A 余 250,000-249,249-62.31225，B 余 250,000-248,248-62.062
    assert res.equity.iloc[-1] == pytest.approx(688.68775 + 24_900 * 10.0
                                                + 1_689.938 + 12_400 * 20.0)


def test_fill_uses_raw_price_not_adjusted():
    """撮合/整手/成本一律用原始价，后复权价只喂信号。
    adj_factor=3 时 adj_open=30.0，但成交必须是 10.0×1.001=10.01、49,900 股；
    若误用 adj_open 则会变成 30.03 元、16,600 股。"""
    df = _mk(D3, [10.0] * 3, factor=3.0)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])}, settings()).run()
    b, s = res.trades[0], res.trades[1]
    assert b.action == "buy" and s.action == "sell"
    assert b.price == pytest.approx(10.01)
    assert b.shares == 49_900
    # 卖出同样用原始价：10.0×0.999=9.99（误用 adj 则为 29.97）
    assert s.price == pytest.approx(9.99)
    assert s.commission == pytest.approx(124.62525)
    assert s.stamp == pytest.approx(249.2505)


def test_commission_never_overdraws_the_budget():
    """额度 100,100：整手向下取整得 100 手(10,000 股)，成交额 100,100.0，
    但加佣金 25.025 就透支了 → 必须退到 99 手(9,900 股)，成交额 99,099.0、佣金 24.77475。
    少了这一步现金会变成 -25.025（净值凭空多算一笔钱）。"""
    df = _mk(D3, [10.0] * 3)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=100_100)).run()
    b = res.trades[0]
    assert b.shares == 9_900
    assert b.commission == pytest.approx(24.77475)
    # 现金余 100,100-99,099-24.77475 = 976.22525，绝不为负
    assert res.equity.iloc[-1] == pytest.approx(976.22525 + 9_900 * 10.0)


def test_engine_charges_the_minimum_commission_on_both_sides():
    """小额成交双边都按 5 元最低佣金收，而非按费率。额度 2,100、价 10.0：
    买 200 股，成交额 2,002.0，费率佣金仅 0.5005 → 实收 5.0，现金余 93.0；
    卖 200 股 px=9.99，成交额 1,998.0，费率佣金 0.4995 → 实收 5.0，印花税 0.999，
    净得 1,992.001，pnl = 1,992.001 - 2,007.0 = -14.999，末日净值 2,085.001。
    去掉最低佣金后净值会虚高到 2,094.001。"""
    df = _mk(D3, [10.0] * 3)
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])},
                     settings(capital=2_100)).run()
    b, s = res.trades[0], res.trades[1]
    assert b.shares == 200
    assert b.commission == pytest.approx(5.0)
    assert s.commission == pytest.approx(5.0)
    assert s.stamp == pytest.approx(0.999)
    assert s.pnl == pytest.approx(-14.999)
    assert res.equity.iloc[-1] == pytest.approx(2_085.001)


def test_existing_position_is_never_rebought():
    """已持仓时 want=1 必须什么都不做。若去掉 `slot.shares == 0` 守卫，
    价格从 100 跌到 40 会用剩余现金再买 200 股并把 slot.shares 从 4,900 **覆盖**掉，
    账面凭空蒸发 4,700 股。
    01-03 开 100.0：px=100.1，49 手=4,900 股，成交额 490,490.0，佣金 122.6225，
                    现金余 9,387.3775。
    01-04 开 40.0：已持仓 → 不动。末日净值 = 9,387.3775 + 4,900×40.0 = 205,387.3775
                   （无守卫时只剩 374.3775 + 200×40 = 8,374.3775）。"""
    df = _mk(D3, [100.0, 100.0, 40.0])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=500_000)).run()
    assert len(res.trades) == 1
    b = res.trades[0]
    assert b.date == pd.Timestamp("2024-01-03")
    assert b.shares == 4_900
    assert b.commission == pytest.approx(122.6225)
    assert res.equity.iloc[-1] == pytest.approx(9_387.3775 + 4_900 * 40.0)
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

> **评审补充（Task 10 代码评审 Issue 1 / Issue 2）**：上面 6 个用例漏了两类东西——
> 除权日与涨跌停判定的交互（Issue 1 的真 bug），以及一批「只测正向、不测负向」的盲区
> （阈值可被收紧/放宽、`>=` 边界、一字**跌停**分支、一字板的方向条件、`_limit_down`
> 用原始价而非 `adj_open`、除权乘的是因子比率而非因子本身）。以下 8 个用例逐个钉死这些点，
> 每个都经手工变异验证确实能杀死对应错法：

```python
def test_ex_dividend_day_is_not_mistaken_for_a_limit_board():
    """10送10 除权日：原始价 100→50，但后复权因子 1.0→2.0，除权参考价就是 50，
    真实涨跌幅 0%，绝不是跌停。拿未除权的昨收 100 去比会误判成跌停并顺延卖出。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-04", open=50.0, high=50.5, low=49.5, close=50.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-05", open=50.0, high=51.0, low=49.0, close=50.5,
             volume=1e6, amount=1e8, adj_factor=2.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])},
                     settings(capital=50_000)).run()
    assert res.skipped == []
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-04")


def test_ex_dividend_day_one_word_limit_up_still_blocks_buy():
    """除权日叠加一字涨停：除权参考价 50 → 涨停价 55，一手都买不到。
    用未除权昨收 100 比较时 55>=109.5 为 False、close 55>100 也为 False，两个分支同时失效。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=1.0),
        dict(date="2024-01-03", open=55.0, high=55.0, low=55.0, close=55.0,
             volume=1e3, amount=1e5, adj_factor=2.0),
        dict(date="2024-01-04", open=56.0, high=58.0, low=55.5, close=57.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 1])},
                     settings(capital=50_000)).run()
    assert any("涨停" in r[2] for r in res.skipped)
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-04")


def test_ex_dividend_scales_shares_by_factor_ratio_not_by_factor():
    """除权调整必须乘「因子比率」f/last_factor，不是因子本身 f。
    真实的 baostock 后复权因子是自上市累积值，起点从来不是 1.0；这里用 2.0→2.5（10送2.5）
    把两种写法拉开：正确 400×1.25=500 股，错写成 ×f 则是 400×2.5=1000 股，
    净值凭空从 49,949.99 涨到 89,949.99。原除权用例的因子恰好是 1.0→1.25，两种写法数值相同，
    根本分辨不出来。手算：01-03 以 100×1.001=100.1 买 400 股，
    成交额 40,040，佣金 max(10.01,5)=10.01，现金余 9,949.99；
    01-04 除权后 500 股 × 80 = 40,000，净值 49,949.99 与除权前 400×100+9,949.99 相等。"""
    df = _df([
        dict(date="2024-01-02", open=100.0, high=100.0, low=100.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-03", open=100.0, high=101.0, low=99.0, close=100.0,
             volume=1e6, amount=1e8, adj_factor=2.0),
        dict(date="2024-01-04", open=80.0, high=81.0, low=79.5, close=80.0,
             volume=1e6, amount=1e8, adj_factor=2.5),
        dict(date="2024-01-05", open=80.0, high=82.0, low=79.0, close=81.0,
             volume=1e6, amount=1e8, adj_factor=2.5),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 0])},
                     settings(capital=50_000)).run()
    assert res.equity.loc["2024-01-03"] == pytest.approx(49_949.99)
    assert res.equity.loc["2024-01-04"] == pytest.approx(49_949.99)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].shares == 500.0


def test_moderate_gap_up_is_not_a_limit_board():
    """负向用例：+5% 高开离 ±10% 板还远，必须当天就成交。
    少了它，把 LIMIT_UP_RATIO 收紧（例如 1.095→1.045）会让引擎把普通高开当涨停顺延，
    全部正向用例仍然全绿。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.5, high=10.8, low=10.4, close=10.7, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1])}, settings()).run()
    assert res.skipped == []
    buys = [t for t in res.trades if t.action == "buy"]
    assert len(buys) == 1 and buys[0].date == pd.Timestamp("2024-01-03")


def test_moderate_gap_down_is_not_a_limit_board():
    """负向用例：-5% 低开不是跌停，卖单必须当天成交，不许顺延。
    对应 LIMIT_DOWN_RATIO 被放宽（0.905→0.95 之类）的错法。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.5, high=9.7, low=9.3, close=9.4, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0])}, settings()).run()
    assert res.skipped == []
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-04")


def test_limit_thresholds_are_inclusive_at_the_boundary():
    """阈值边界：开盘价恰好等于昨收×1.095（10.95）就算涨停，差一分钱（10.94）就不算。
    钉死 `>=` 不能写成 `>`，也钉死阈值本身不能挪动。
    （10.0×1.095 在 IEEE754 下恰为 10.95，比较无浮点毛刺。）"""
    on = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.95, high=11.2, low=10.9, close=11.0, volume=1e6, amount=1e7),
    ])
    res_on = Backtester({"TEST": on}, {"TEST": _positions(on, [1, 1])}, settings()).run()
    assert any("涨停" in r[2] for r in res_on.skipped) and res_on.trades == []

    off = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.94, high=11.2, low=10.9, close=11.0, volume=1e6, amount=1e7),
    ])
    res_off = Backtester({"TEST": off}, {"TEST": _positions(off, [1, 1])}, settings()).run()
    assert res_off.skipped == [] and len(res_off.trades) == 1


def test_one_word_limit_down_blocks_sell_then_fills_next_day():
    """一字跌停也不可卖——此前只测了一字涨停，跌停侧的 one_word 分支完全是盲区。
    这里 01-04 跌幅仅 5%（未触及 -9.5% 阈值），开盘价那条判据不会命中，
    只能靠 high==low 且 close<昨收 识别（ST 股的 ±5% 板就是这个形态）。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-04", open=9.5, high=9.5, low=9.5, close=9.5, volume=1e3, amount=1e4),
        dict(date="2024-01-05", open=9.5, high=9.7, low=9.4, close=9.6, volume=1e6, amount=1e7),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")


def test_flat_one_word_bar_is_not_a_limit_board():
    """一字板还得看方向：全天一个价、但与昨收持平（冷门股无人交易），既非涨停也非跌停，
    买卖都该照常成交。钉死两处方向判据不能写成 `>=` / `<=`。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-03", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e3, amount=1e4),
        dict(date="2024-01-04", open=10.0, high=10.3, low=9.8, close=10.0, volume=1e6, amount=1e7),
        dict(date="2024-01-05", open=10.0, high=10.0, low=10.0, close=10.0, volume=1e3, amount=1e4),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 1, 0, 0])}, settings()).run()
    assert res.skipped == []
    assert [(t.action, t.date) for t in res.trades] == [
        ("buy", pd.Timestamp("2024-01-03")), ("sell", pd.Timestamp("2024-01-05"))]


def test_limit_down_is_judged_on_raw_price_not_adjusted_price():
    """项目铁律「涨跌停判定用原始价」在跌停侧的证明：因子恒为 5.0（成熟股的累积后复权因子，
    期间无除权），原始价 10.0→9.0 是实打实的跌停，但 adj_open=45.0 远高于昨收 10.0——
    把判据改用 adj_open 就会漏判并当天卖出。"""
    df = _df([
        dict(date="2024-01-02", open=10.0, high=10.1, low=9.9, close=10.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-03", open=10.0, high=10.2, low=9.9, close=10.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-04", open=9.0, high=9.2, low=8.8, close=9.0,
             volume=1e6, amount=1e7, adj_factor=5.0),
        dict(date="2024-01-05", open=9.0, high=9.2, low=8.9, close=9.1,
             volume=1e6, amount=1e7, adj_factor=5.0),
    ])
    res = Backtester({"TEST": df}, {"TEST": _positions(df, [1, 0, 0, 0])}, settings()).run()
    assert any("跌停" in r[2] for r in res.skipped)
    sells = [t for t in res.trades if t.action == "sell"]
    assert len(sells) == 1 and sells[0].date == pd.Timestamp("2024-01-05")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_engine_constraints.py -v`
Expected: FAIL（涨跌停/除权用例失败——占位方法返回 False/pass）

- [ ] **Step 3: 实现三个占位方法**

> **评审修正（Task 10 代码评审 Issue 1）**：`_apply_factor` 原稿只折算 `slot.shares`，
> 没有把 `slot.last_close`（昨收，原始价）折算到今日价格体系，而 `_try_buy`/`_try_sell`
> 正是拿这个未折算的昨收当 `prev_close` 去判涨跌停。除权日原始价本就跳空（10送10 直接腰斩），
> 于是卖单被误判跌停顺延、除权日的真实涨停板反而漏判（收益系统性高估）。
> `prepare_bars` 的跳变告警早已用 `~factor_changed` 处理过同一件事，引擎漏了这个修正。
> 下面是修正后的版本。

```python
    def _apply_factor(self, slot: Slot, row: pd.Series) -> None:
        """除权除息日按后复权因子比率调整持仓股数（等效分红再投资，spec §8 步骤4），
        并把「昨收」折算到今日价格体系。少了后半步，除权造成的原始价缺口（10送10 直接腰斩）
        会被涨跌停判定当成真实跌幅：卖单被误判跌停顺延，除权日的真实涨停板反而漏判。"""
        f = float(row["adj_factor"])
        if slot.last_factor is None or f == slot.last_factor:
            return
        ratio = f / slot.last_factor
        if slot.shares > 0:
            slot.shares *= ratio
        if slot.last_close is not None:
            slot.last_close /= ratio      # 昨收 → 除权参考价（与今日原始价同一体系）

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
Expected: 24 passed（9 项 basic + 15 项 constraints，后者含评审补充的 8 项）

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

**评审加固（Task 11 复审）**：原三条测试对 8 个字段里的 `cagr` 零断言、对 `sharpe` 只断言了
`std == 0` 的短路分支，32 个变异体中 15 个存活。三处必须钉死：
① `[100, 110, 99]` 的滚动峰值恰好等于全期最高点，`cummax()` 写成 `max()`（未来函数）数值不变，
必须补一条峰值出现在低谷**之后**的序列；② 夏普要有一条非零数值断言（口径含 `ddof=1` 与 `sqrt(252)`）
外加符号方向断言，否则符号反转、去年化、`std(ddof=0)`、`pct_change`→`diff` 全部静默通过；
③ `cagr` 需要一条能区分 `len(equity)` 与 `len(equity)-1` 口径的断言（126 根 bar 下分别是
0.4641 与 0.46857）。另补零 pnl 归为亏损、无亏损单时 `profit_factor is None` 两条边界，
供 Task 12/13/15 消费该字典时依赖。

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


def test_max_drawdown_uses_running_peak_not_global_max():
    # 峰值出现在低谷「之后」：若用全期最高点当基准（未来函数）会算出 -0.55
    m = compute_metrics(_equity([100, 90, 200]), trades=[])
    assert m["max_drawdown"] == pytest.approx(-0.10)   # (90-100)/100


def test_sharpe_value():
    # 日收益 [0.10, 0.20, 0.60]：mean=0.3，std(ddof=1)=sqrt(0.14/2)
    # sharpe = 0.3 / sqrt(0.07) * sqrt(252) = 0.3 * sqrt(3600) = 18.0
    m = compute_metrics(_equity([100, 110, 132, 211.2]), trades=[])
    assert m["sharpe"] == pytest.approx(18.0)


def test_sharpe_sign_follows_returns():
    up = compute_metrics(_equity([100, 110, 132, 211.2]), trades=[])["sharpe"]
    down = compute_metrics(_equity([211.2, 132, 110, 100]), trades=[])["sharpe"]
    assert up > 0 and down < 0


def test_cagr_annualizes_by_bar_count():
    # 126 根 bar = 0.5 年（252 交易日/年），100 -> 121 → cagr = 1.21**2 - 1
    m = compute_metrics(_equity([100.0] * 125 + [121.0]), trades=[])
    assert m["cagr"] == pytest.approx(0.4641)


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


def test_breakeven_trade_counts_as_loss():
    t = pd.Timestamp("2024-01-05")
    trades = [
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10),
        Trade("A", "sell", t, 10, 100, 5, 5, pnl=0.0, holding_days=10),
    ]
    m = compute_metrics(_equity([100, 101]), trades)
    assert m["n_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)


def test_profit_factor_none_when_no_losses():
    t = pd.Timestamp("2024-01-05")
    trades = [Trade("A", "sell", t, 10, 100, 5, 5, pnl=100.0, holding_days=10)]
    m = compute_metrics(_equity([100, 101]), trades)
    assert m["profit_factor"] is None
    assert m["win_rate"] == pytest.approx(1.0)


def test_flat_equity_no_crash():
    m = compute_metrics(_equity([100, 100, 100]), trades=[])
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
```

**口径备注**：年化用 `len(equity)`（bar 数）而非 `len(equity) - 1`（区间数），属口径选择不属算错，
由 `test_cagr_annualizes_by_bar_count` 固定；`profit_factor` 在「无亏损单」「无盈利单」「无平仓单」
三种情况下一律返回 `None`（避免除零/inf），Task 13/15 格式化前必须判空，
不能直接 `f"{m['profit_factor']:.2f}"`。

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
- Test: `tests/test_charts.py`（冒烟：能生成 Figure 且含预期轨迹数，能写出 HTML；
  另加固断言轨迹内容——归一化起点、回撤用滚动峰值、K线 OHLC 槽位与红涨绿跌、买卖点日期/价格/形状）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_charts.py
import pandas as pd
import pytest

from quant.backtest.portfolio import Trade
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from tests.conftest import make_bars


def _eq(values, start="2024-01-02"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def _bars():
    rows = [dict(date=f"2024-01-{d:02d}", open=10 + d, high=11 + d, low=9 + d,
                 close=10.5 + d, volume=1e6, amount=1e7) for d in range(2, 8)]
    return make_bars(rows)


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
    df = _bars()
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    # K线 + 买点 + 卖点 = 3 条轨迹
    assert len(fig.data) == 3


def test_equity_and_benchmark_both_start_at_one():
    # 不归一化 / 用最后一天归一化 → 起点不是 1，两条曲线不可比
    eq = _eq([200.0, 220.0, 180.0])
    bench = {"HS300": pd.Series([400.0, 440.0, 400.0], index=eq.index)}
    fig = equity_chart(eq, bench)
    assert fig.data[0].y[0] == pytest.approx(1.0)
    assert fig.data[1].y[0] == pytest.approx(1.0)
    assert list(fig.data[0].y) == pytest.approx([1.0, 1.1, 0.9])


def test_drawdown_uses_running_peak_not_global_max():
    # 峰值在低谷之后：用全期最高当基准（未来函数）会得到 [-0.5, -0.55, 0, -0.25]
    fig = equity_chart(_eq([100.0, 90.0, 200.0, 150.0]), {})
    dd = fig.data[-1]
    assert dd.name == "回撤"
    assert list(dd.y) == pytest.approx([0.0, -0.1, 0.0, -0.25])


def test_drawdown_matches_metrics_max_drawdown():
    eq = _eq([100.0, 130.0, 91.0, 120.0, 60.0, 80.0])
    dd = equity_chart(eq, {}).data[-1]
    assert min(dd.y) == pytest.approx(compute_metrics(eq, [])["max_drawdown"])


def test_equity_on_row1_drawdown_on_row2():
    eq = _eq([100.0, 110.0, 105.0])
    fig = equity_chart(eq, {"HS300": pd.Series([1.0, 2.0, 3.0], index=eq.index)})
    assert [t.yaxis for t in fig.data] == ["y", "y", "y2"]


def test_benchmark_nan_head_dropped_not_silently_blank():
    # 少了 dropna：s.iloc[0] 是 NaN → 整条基准线全 NaN，图上什么也没有却不报错
    eq = _eq([100.0, 101.0, 102.0, 103.0])
    bench = pd.Series([float("nan"), 100.0, 110.0, 121.0], index=eq.index)
    b = equity_chart(eq, {"HS300": bench}).data[1]
    assert not pd.isna(list(b.y)).any()
    assert list(b.y) == pytest.approx([1.0, 1.1, 1.21])
    assert pd.Timestamp(b.x[0]) == eq.index[1]


def test_kline_uses_raw_ohlc_in_correct_slots():
    df = _bars()
    k = kline_chart(df, [], "TEST").data[0]
    assert list(k.x) == list(df.index)
    assert list(k.open) == list(df["open"])
    assert list(k.high) == list(df["high"])
    assert list(k.low) == list(df["low"])
    assert list(k.close) == list(df["close"])


def test_kline_ashare_color_convention_red_up_green_down():
    k = kline_chart(_bars(), [], "TEST").data[0]
    assert k.increasing.line.color == "red"
    assert k.decreasing.line.color == "green"


def test_buy_sell_markers_carry_own_date_price_and_shape():
    df = _bars()
    trades = [
        Trade("TEST", "buy", pd.Timestamp("2024-01-03"), 13.0, 100, 5),
        Trade("TEST", "sell", pd.Timestamp("2024-01-06"), 16.0, 100, 5, 5, pnl=290.0),
    ]
    fig = kline_chart(df, trades, "TEST")
    buy, sell = fig.data[1], fig.data[2]
    assert buy.name == "买入" and sell.name == "卖出"
    assert [pd.Timestamp(x) for x in buy.x] == [pd.Timestamp("2024-01-03")]
    assert list(buy.y) == pytest.approx([13.0])
    assert [pd.Timestamp(x) for x in sell.x] == [pd.Timestamp("2024-01-06")]
    assert list(sell.y) == pytest.approx([16.0])
    assert buy.marker.symbol == "triangle-up" and buy.marker.color == "red"
    assert sell.marker.symbol == "triangle-down" and sell.marker.color == "green"


def test_kline_title_and_no_rangeslider():
    fig = kline_chart(_bars(), [], "sh.600519")
    assert fig.layout.title.text == "sh.600519"
    assert fig.layout.xaxis.rangeslider.visible is False
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
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/quant/report/charts.py tests/test_charts.py
git commit -m "feat: 净值/回撤与K线买卖点图表"
```

---

### Task 13: 回测入口与基准 [M4]

**Files:**
- Create: `scripts/run_backtest.py`
- Test: `tests/test_run_backtest.py`（离线，覆盖落盘与基准口径）+ 手动端到端验收（真实数据，联网）

- [ ] **Step 0: 先写 tests/test_run_backtest.py 并运行确认失败**

```python
# tests/test_run_backtest.py
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.portfolio import Trade

_SPEC = importlib.util.spec_from_file_location(
    "run_backtest", Path(__file__).resolve().parent.parent / "scripts" / "run_backtest.py")
run_backtest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_backtest)


def _trade(symbol="600519", action="buy"):
    return Trade(symbol=symbol, action=action, date=pd.Timestamp("2024-01-05"),
                 price=10.0, shares=100, commission=5.0)


def test_trades_csv_keeps_header_when_no_trades(tmp_path):
    """零成交是真实结果（暖机期吃满全部 K 线时就会发生），不是异常。
    不带表头写出的 trades.csv 只有一个换行符，下游 pd.read_csv 直接 EmptyDataError——
    面板/报告一打开就崩，而回测本身其实跑成功了。"""
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([], path)
    df = pd.read_csv(path)          # 修复前：EmptyDataError: No columns to parse from file
    assert df.empty
    assert list(df.columns) == run_backtest.TRADE_COLUMNS


def test_trades_csv_columns_match_trade_fields(tmp_path):
    """空表表头必须与有成交时的列**逐字一致**，否则下游按列名取值会在空表上 KeyError。"""
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([_trade()], path)
    filled = pd.read_csv(path, dtype={"symbol": str})   # 不指定则 000333 会被读成 333
    assert list(filled.columns) == run_backtest.TRADE_COLUMNS
    assert filled["symbol"].tolist() == ["600519"]
    assert filled["commission"].tolist() == [5.0]

    empty_path = tmp_path / "empty.csv"
    run_backtest.write_trades([], empty_path)
    assert list(pd.read_csv(empty_path).columns) == list(filled.columns)


def test_no_trade_field_is_silently_dropped(tmp_path):
    """显式传 columns 的代价是漏列即静默丢数据（to_csv 根本不写那一列）。
    卖出行的 pnl / holding_days 是绩效复核的唯一依据，丢了不会报错只会算错。"""
    sold = Trade(symbol="600519", action="sell", date=pd.Timestamp("2024-02-05"),
                 price=12.0, shares=100, commission=5.0, stamp=0.6,
                 pnl=190.0, holding_days=31)
    path = tmp_path / "trades.csv"
    run_backtest.write_trades([sold], path)
    got = pd.read_csv(path, dtype=str).iloc[0].to_dict()   # dtype=str：逐字比对写出的内容
    expected = {k: str(v) for k, v in vars(sold).items()} | {"date": "2024-02-05"}
    assert got == expected


def test_equal_weight_hold_normalizes_each_symbol_to_one():
    """基准是"等权买入持有"：每只先按各自首日归一再取均值。
    直接对价格取均值会让高价股主导基准，贵州茅台一只就能决定曲线形状。"""
    idx = pd.bdate_range("2024-01-01", periods=3)
    bars = {
        "A": pd.DataFrame({"adj_close": [100.0, 110.0, 120.0]}, index=idx),
        "B": pd.DataFrame({"adj_close": [10.0, 10.0, 10.0]}, index=idx),
    }
    got = run_backtest.equal_weight_hold(bars)
    assert got.iloc[0] == pytest.approx(1.0)
    assert got.iloc[1] == pytest.approx((1.1 + 1.0) / 2)
    assert got.iloc[2] == pytest.approx((1.2 + 1.0) / 2)
```

Run: `.venv/bin/python -m pytest tests/test_run_backtest.py -v`
Expected: FAIL（模块尚不存在 / 零成交时 trades.csv 无表头 → EmptyDataError）

- [ ] **Step 1: 实现 scripts/run_backtest.py**

```python
"""回测入口：python scripts/run_backtest.py [--config config/settings.yaml] [--strategy 名称]
输出到 output/<策略>_<运行时间戳>/：metrics.json、equity.csv、trades.csv、report.html、
kline_<代码>.html × N。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quant.backtest.engine import Backtester
from quant.backtest.portfolio import Trade
from quant.config import load_settings
from quant.data.baostock_provider import BaostockProvider
from quant.data.cache import BarCache
from quant.data.service import DataService
from quant.report.charts import equity_chart, kline_chart
from quant.report.metrics import compute_metrics
from quant.strategy import build_strategies

OUTPUT = Path("output")

# 显式列名从 Trade 字段派生：加字段不会漏列，零成交时表头也不会消失。
TRADE_COLUMNS = [f.name for f in fields(Trade)]


def write_trades(trades: list[Trade], path: Path) -> None:
    """成交流水落盘。必须显式给 columns：零成交（暖机期吃满全部 K 线时就会发生）时
    pd.DataFrame([]) 一列都没有，写出的文件只有一个换行符，
    下游 pd.read_csv 直接 EmptyDataError——回测明明跑成功了，面板一开就崩。"""
    pd.DataFrame([vars(t) for t in trades], columns=TRADE_COLUMNS).to_csv(path, index=False)


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
        write_trades(result.trades, run_dir / "trades.csv")
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

Run: `time .venv/bin/python scripts/run_backtest.py --strategy ma_cross`
Expected（"明显加快"必须量化，否则增量分支静默失效也看不出来）:
- 总耗时比 Step 2 至少快一个数量级（实测 45s → 6s）
- `data/cache/<代码>.meta.json` 存在且 `covered_start` 等于 settings 的 start
- 若第二次仍与第一次同量级慢，说明走了全量重拉分支，**不要**归因于网络抖动，去查
  `DataService.get_bars` 的头部缺口判据（历史 bug：用首根 bar 日期比 start，
  start 是元旦时该条件恒真）

- [ ] **Step 4: 人工合理性检查（不是测试，是学习环节——打开看！）**

- 打开 `report.html`：策略净值与两条基准形态是否合理（无跳崖式毛刺）
- 打开任一 `kline_*.html`：买卖点是否落在 K 线上、突破点位置是否符合策略定义
- 查看 `skipped.csv`：涨跌停顺延/资金不足记录是否合理

- [ ] **Step 5: Commit**

```bash
git add scripts/run_backtest.py tests/test_run_backtest.py
git commit -m "feat: 回测入口（含基准对比与报告输出）"
```

---

### Task 14: 每日信号 [M5]

**Files:**
- Create: `src/quant/signal/__init__.py`, `src/quant/signal/scan.py`, `scripts/run_daily_signal.py`
- Test: `tests/test_signal_scan.py`, `tests/test_run_daily_signal.py`

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
    # close 与 open 必须逐根不同，否则"取首根收盘/取当日开盘"这类错法测不出来
    rows = [dict(date=f"2024-01-{d:02d}", open=1 + d, high=30, low=1, close=10 * (d - 1),
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


def test_scan_uses_previous_bar_not_first_bar_and_reports_latest_close():
    """仓位必须与**上一根**比（与首根比会漏掉"昨买今卖"），close 必须是最新一根的收盘价。"""
    df = _bars("AAA")           # close = 10, 20, 30；open = 3, 4, 5
    signals = scan({"AAA": df}, [StubStrategy({"AAA": [0, 1, 0]})])
    assert [(s["symbol"], s["action"], s["date"], s["close"]) for s in signals] == [
        ("AAA", "SELL", "2024-01-04", 30.0)]


def test_scan_runs_every_strategy():
    """线上配了 2 个策略；只跑第一个会让另一个的信号静默消失。"""
    df = _bars("AAA")
    a, b = StubStrategy({"AAA": [0, 0, 1]}), StubStrategy({"AAA": [1, 1, 0]})
    a.name, b.name = "s1", "s2"
    assert {(s["strategy"], s["action"]) for s in scan({"AAA": df}, [a, b])} == {
        ("s1", "BUY"), ("s2", "SELL")}


def test_scan_skips_symbol_with_single_bar():
    """新股上市首日只有一根K线，取 iloc[-2] 会 IndexError 把整轮扫描炸掉。"""
    df = _bars("AAA", n=1)
    assert scan({"AAA": df}, [StubStrategy({"AAA": [1]})]) == []
```

> 变异测试记录：初版只有 `test_scan_detects_new_buy_and_sell`，10 个真实变异体存活 6 个
> （`len(pos) < 2 → < 0`、`close` 取首根/读 open 列/写死 0.0、`strategies[:1]`、
> `pos.iloc[-2] → pos.iloc[0]`）。补上上面三个用例并让 `_bars` 的 close/open 逐根不同后，
> 10 个变异体全部 KILLED。

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
Expected: 4 passed

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


def require_strategies(strategy_cfg: dict[str, dict]) -> list:
    """构造策略，空表则退出。

    "今日无新信号"是多数日子的正常结果，与"一个策略都没跑"的输出**逐字相同**：
    同样打印无信号、同样写出只有表头的 CSV、同样退出码 0。而 config.py 的
    `raw.get("strategies") or {}` 让 settings.yaml 的 strategies 段缺失/为空/
    键名拼错时静默得到 {}——不挡住，信号系统会天天空跑且零告警，用户永远发现不了。
    在联网取数之前就退出，免得白抓十只标的的行情。
    """
    strategies = build_strategies(strategy_cfg)
    if not strategies:
        sys.exit("配置里没有任何策略（settings.yaml 的 strategies 段缺失或为空），拒绝空跑")
    return strategies


def main() -> None:
    settings = load_settings("config/settings.yaml")
    strategies = require_strategies(settings.strategies)
    bars = {}
    with BaostockProvider() as provider:
        cal = provider.get_trade_calendar(date.today() - timedelta(days=21), date.today())
        if not cal:
            sys.exit("近三周无交易日？交易日历异常，退出")
        expected = cal[-1]  # 最近一个交易日（含今天）
        if expected != date.today():
            # 周末/长假补跑上一交易日的信号是真实且合理的用法，故不像 spec §11 那样硬退出；
            # 但必须显式说破，否则用户会把上一交易日的旧信号当成今天的新信号。
            print(f"[注意] 今天 {date.today()} 非交易日，以下是最近交易日 {expected} 的信号"
                  f"（重算结果与当日一致，会覆盖同名 CSV）")
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

    signals = scan(bars, strategies)
    print(f"\n===== {expected} 信号 =====（扫描 {len(bars)} 只 × {len(strategies)} 个策略）")
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

> 关于"非交易日"：spec §11 写的是「非交易日或数据未更新则明确提示退出」，这里**只提示不退出**。
> 周末/长假补跑上一交易日的信号是真实需求，硬退出会挡掉它；重算幂等，终端与 CSV 都带真实日期。
> 这是有意放宽，已加显式提示消除"把旧信号当新信号"的风险。

- [ ] **Step 5b: 空策略表守卫的回归测试**

```python
# tests/test_run_daily_signal.py
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_daily_signal", Path(__file__).resolve().parent.parent / "scripts" / "run_daily_signal.py")
run_daily_signal = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_daily_signal)


def test_empty_strategy_config_aborts():
    with pytest.raises(SystemExit) as e:
        run_daily_signal.require_strategies({})
    assert "strategies" in str(e.value)


def test_valid_strategy_config_builds_strategies():
    got = run_daily_signal.require_strategies({"ma_cross": {"fast": 20, "slow": 60}})
    assert [s.name for s in got] == ["ma_cross"]
```

- [ ] **Step 6: 端到端验收（联网）**

Run: `.venv/bin/python scripts/run_daily_signal.py`
Expected: 数据新鲜时输出信号表（多数日子"今日无新信号"属正常）并生成 `output/signals/<date>.csv`；数据未更新时明确提示并退出码 1。两种结果都算通过，记录实际走到哪个分支。

- [ ] **Step 7: Commit**

```bash
git add src/quant/signal scripts/run_daily_signal.py \
        tests/test_signal_scan.py tests/test_run_daily_signal.py
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
import re
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


RUN_STAMP = re.compile(r"_(\d{8}_\d{6})$")   # run_backtest.py 的 {策略}_{YYYYMMDD}_{HHMMSS}


def _run_key(p: Path) -> tuple[str, str]:
    """按目录名尾部的时间戳排序。直接 sorted(paths, reverse=True) 比的是整条路径字符串，
    策略名会压过时间戳（"ma_cross_" > "donchian_"），默认选中的就不是最新那次回测。
    没有时间戳的目录归到最后（reverse=True 下空串最小）。"""
    m = RUN_STAMP.search(p.name)
    return (m.group(1) if m else "", p.name)


def list_runs() -> list[Path]:
    if not OUTPUT.exists():
        return []
    return sorted((p for p in OUTPUT.iterdir()
                   if p.is_dir() and (p / "metrics.json").exists()),
                  key=_run_key, reverse=True)


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
    # dtype 必须显式给：symbol 写出去是字符串 "000333"，pd.read_csv 会推断成 int64
    # 吃掉前导零，表里就显示成不存在的股票代码 333（所有深市 000xxx 都中招）
    st.dataframe(pd.read_csv(run / "trades.csv", dtype={"symbol": str}),
                 use_container_width=True)
    skipped = run / "skipped.csv"
    if skipped.exists():
        st.subheader("被跳过的订单（涨跌停/资金不足等）")
        st.dataframe(pd.read_csv(skipped, dtype={"symbol": str}), use_container_width=True)


def page_kline() -> None:
    runs = list_runs()
    if not runs:
        st.info("暂无回测结果。先运行: python scripts/run_backtest.py")
        return
    run = st.selectbox("选择回测", runs, format_func=lambda p: p.name)
    trades_df = pd.read_csv(run / "trades.csv", dtype={"symbol": str})
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
    df = pd.read_csv(latest, dtype={"symbol": str})
    # 必须写成 if/else 语句：streamlit 的 magic 会把函数体内**裸的三元表达式**
    # （ast.IfExp，不属于它豁免的 ast.Call）整个包进 st.write()，
    # 于是 st.dataframe() 的返回值 DeltaGenerator 被 st.write 当对象内省，
    # 把整份 Streamlit API 手册糊在信号表下面；无信号那天则渲染出一个 `None`。
    if len(df):
        st.dataframe(df, use_container_width=True)
    else:
        st.write("当日无新信号")
    if len(files) > 1:
        st.subheader("历史信号")
        hist = pd.concat([pd.read_csv(f, dtype={"symbol": str}) for f in files[1:]],
                         ignore_index=True)
        if len(hist):
            st.dataframe(hist, use_container_width=True)
        else:
            st.write("无")


st.set_page_config(page_title="quant_demo v0.1", layout="wide")
st.sidebar.title("quant_demo")
page = st.sidebar.radio("页面", ["回测报告", "个股K线", "今日信号"])
st.sidebar.caption("本面板纯只读；回测与信号请用命令行运行。策略仅用于学习，不构成投资建议。")
{"回测报告": page_backtest, "个股K线": page_kline, "今日信号": page_signals}[page]()
```

- [ ] **Step 2: 写冒烟测试 + 三条回归测试**

三条回归测试各自钉死一个真实踩过的坑：streamlit magic 把裸三元包进 `st.write`、`pd.read_csv` 吃掉 `000333` 的前导零、`list_runs()` 按路径字符串排序导致默认选中的不是最新回测。`_load_dashboard` 把 dashboard.py 复制到 tmp_path 再加载，使 `ROOT`/`OUTPUT` 落在临时目录，与仓库真实 `output/` 隔离。

```python
# tests/test_dashboard_import.py
import ast
import importlib.util
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

DASHBOARD = Path("app/dashboard.py")


def test_dashboard_syntax_ok():
    """streamlit 脚本无法直接 import 测试（顶层执行 UI 代码），至少保证语法正确。"""
    src = DASHBOARD.read_text(encoding="utf-8")
    ast.parse(src)


def test_no_statement_is_wrapped_by_streamlit_magic():
    """streamlit 的 magic 会把函数体里**裸的表达式语句**包进
    __streamlitmagic__.transparent_write()（= st.write）。它只豁免 ast.Call /
    docstring / yield / await，裸三元 ast.IfExp 不在名单里：
    `st.dataframe(...) if len(df) else st.write("...")` 会被整条包起来，
    于是 st.dataframe() 返回的 DeltaGenerator 被 st.write 走对象内省分支，
    把约 90 行 Streamlit API 方法表糊在信号表下面；走 else 分支那天则渲染出一个 `None`。
    只在 `streamlit run` 下发作，普通 import 察觉不到，所以这里直接跑它的 AST 改写。
    """
    from streamlit.runtime.scriptrunner import magic

    tree = magic.add_magic(DASHBOARD.read_text(encoding="utf-8"), str(DASHBOARD))
    wrapped = [
        f"L{n.lineno}: {ast.unparse(n)}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "transparent_write"
    ]
    assert wrapped == [], (
        "以下语句会被 streamlit magic 悄悄包进 st.write（请改写成 if/else 语句）:\n"
        + "\n".join(wrapped)
    )


def _load_dashboard(tmp_path, monkeypatch, page):
    """把 dashboard.py 复制到临时根目录再加载，使 ROOT/OUTPUT 落在 tmp_path，
    与仓库真实 output/ 完全隔离（quant 是 editable 安装，import 不受 sys.path 影响）。
    返回 (模块, 该页渲染时喂给 st.dataframe 的 DataFrame 列表)。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    shutil.copy(DASHBOARD, app_dir / "dashboard.py")
    frames: list[pd.DataFrame] = []
    # 顶层代码在 exec_module 时就会渲染选中页，所以桩必须先装好
    monkeypatch.setattr(st.sidebar, "radio", lambda *a, **k: page)
    monkeypatch.setattr(st, "dataframe", lambda df, *a, **k: frames.append(df))
    spec = importlib.util.spec_from_file_location(
        f"dashboard_under_test_{page}", app_dir / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, frames


def test_backtest_page_keeps_leading_zero_symbols(tmp_path, monkeypatch):
    """trades.csv / skipped.csv 里 symbol 是字符串 000333，pd.read_csv 不带 dtype
    会把整列推断成 int64 吃掉前导零，表里显示成不存在的股票代码 333。"""
    run = tmp_path / "output" / "ma_cross_20260817_121152"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text('{"total_return": 0.1}', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "trades.csv").write_text(
        "symbol,action,date,price,shares,commission\n"
        "000333,buy,2016-04-05,10.0,100,5.0\n", encoding="utf-8")
    (run / "skipped.csv").write_text(
        "symbol,date,reason\n000001,2016-04-05,涨停\n", encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "回测报告")

    assert [f["symbol"].tolist() for f in frames] == [["000333"], ["000001"]]


def test_signal_page_keeps_leading_zero_symbols(tmp_path, monkeypatch):
    """今日信号页（最新 + 历史）同样不能吃掉前导零。"""
    sig = tmp_path / "output" / "signals"
    sig.mkdir(parents=True)
    header = "symbol,date,strategy,signal,close\n"
    (sig / "2026-08-14.csv").write_text(
        header + "000333,2026-08-14,ma_cross,buy,10.0\n", encoding="utf-8")
    (sig / "2026-08-13.csv").write_text(
        header + "000001,2026-08-13,ma_cross,sell,9.0\n", encoding="utf-8")

    _, frames = _load_dashboard(tmp_path, monkeypatch, "今日信号")

    assert [f["symbol"].tolist() for f in frames] == [["000333"], ["000001"]]


def test_list_runs_puts_newest_first_regardless_of_strategy_name(tmp_path, monkeypatch):
    """目录名是 {策略}_{YYYYMMDD}_{HHMMSS}；按整条路径字符串排序会让策略名压过时间戳
    （"ma_cross_" > "donchian_"），面板默认选中的就不是最新那次回测。"""
    out = tmp_path / "output"
    for name in ("ma_cross_20260817_121152", "donchian_20260817_123313",
                 "ma_cross_20200101_000000"):
        (out / name).mkdir(parents=True)
        (out / name / "metrics.json").write_text("{}", encoding="utf-8")

    mod, _ = _load_dashboard(tmp_path, monkeypatch, "今日信号")  # 信号页不读 run 目录

    assert [p.name for p in mod.list_runs()] == [
        "donchian_20260817_123313",      # 最新
        "ma_cross_20260817_121152",
        "ma_cross_20200101_000000",      # 最旧
    ]
```

- [ ] **Step 3: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_dashboard_import.py -v`
Expected: 5 passed

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



