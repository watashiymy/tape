# 趋势宽度正式图表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页「趋势宽度」四张卡片只留数字；卡片下方一张两行 plotly 图表（上：三条「站上 N 日线」占比线 + 50% 虚线；下：52 周新高红柱向上 / 新低绿柱向下），带「60 / 120 / 全部」切换；删掉四张小图及其代码。

**Architecture:** 图表是 `quant.report.charts.breadth_chart(table, span)` 纯函数（单图、一条类目横轴、两个纵轴区域——分行子图的统一悬停会分成两个框）；页面 `_trend_chart` 只放一个 `st.radio` + `st.plotly_chart` + 一行说明。`minicharts` 只剩分时 / 分布 / 比例条。

**Tech Stack:** Python 3.14、plotly 6.9、Streamlit 1.61.1（AppTest 读 `plotly_chart` 元素的 `proto.spec` JSON）、pytest。

**Spec:** `docs/superpowers/specs/2026-10-06-trend-breadth-chart-design.md`（评审修订后定稿）。

**分支：** `feat/trend-chart`（已检出，别切）。提交身份 `git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit`，
提交说明末尾空一行加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。所有命令在仓库根目录跑，Python 一律 `.venv/bin/python`。
**不许** `git checkout --` / `git stash`；变异自查用 `cp` 备份、`mv` 还原。

---

### Task 1: `charts.breadth_chart` 与两张表

**Files:** Modify `src/quant/report/charts.py`（`KLINE_SPANS` 之后加常量；文件末尾加函数）；Test `tests/test_charts.py`

- [ ] **Step 1: 写失败的测试**

1a. `tests/test_charts.py` 顶部 `from quant.report.charts import (...)` 里加 `breadth_chart`。
1b. `_figs()` 的字典加一项 `"breadth": breadth_chart(_breadth_table(), 60),`；四处 `@pytest.mark.parametrize("kind", ["equity", "kline", "journal_cum", "weights", "source"])`
    都加上 `"breadth"`。
1c. 在 `_figs` 定义之前加夹具与测试：

```python
def _breadth_table(n: int = 70) -> pd.DataFrame:
    """指标表的最小夹具：n 个工作日；200 日线前 n−1 天分母为 0（线要断开）；新高 3、新低 5。"""
    days = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame({"date": days.date, "above20": range(n), "n20": [100] * n,
                         "above60": [50] * n, "n60": [100] * n,
                         "above200": [0] * n, "n200": [0] * (n - 1) + [100],
                         "new_high": [3] * n, "new_low": [5] * n, "n_hl": [100] * n})


def test_breadth_chart_puts_three_lines_on_y_and_two_bars_on_y2():
    """单图双纵轴（不用 make_subplots）：分行子图各有横轴，统一悬停会上下各弹一个框。"""
    fig = breadth_chart(_breadth_table(), None)
    lines = [t for t in fig.data if t.type == "scatter"]
    bars = [t for t in fig.data if t.type == "bar"]
    assert [(t.name, t.line.dash) for t in lines] == [(name, dash) for _, name, dash in charts.BREADTH_LINES]
    assert all(t.yaxis in (None, "y") for t in lines) and all(t.yaxis == "y2" for t in bars)
    assert all(t.xaxis in (None, "x") for t in fig.data), "五条 trace 共用同一条横轴"
    assert fig.layout.colorway == palette.CHART_SERIES
    assert all(t.line.color not in (palette.UP, palette.DOWN) for t in lines)
    hi, lo = bars
    assert hi.name == "新高" and hi.marker.color == palette.UP and min(hi.y) >= 0
    assert lo.name == "新低" and lo.marker.color == palette.DOWN and max(lo.y) <= 0
    assert all(v > 0 for v in lo.customdata) and "customdata" in lo.hovertemplate and "只" in lo.hovertemplate
    assert "只" in hi.hovertemplate and "新高" not in hi.hovertemplate and "新低" not in lo.hovertemplate


def test_breadth_chart_zero_denominator_breaks_the_line_instead_of_drawing_zero():
    import math
    y200 = breadth_chart(_breadth_table(), None).data[2].y
    assert all(math.isnan(v) for v in y200[:-1]) and y200[-1] == 0.0


def test_breadth_chart_axes_fixed_percent_scale_with_fifty_percent_line_and_abs_ticks():
    fig = breadth_chart(_breadth_table(), None)
    assert fig.layout.yaxis.range == (0, 1) and fig.layout.yaxis.tickformat == ".0%"
    assert fig.layout.yaxis.domain[0] > fig.layout.yaxis2.domain[1], "上半幅在下半幅之上、不重叠"
    shapes = [s for s in fig.layout.shapes if s.y0 == 0.5 and s.y1 == 0.5]
    assert len(shapes) == 1 and shapes[0].line.dash == "dot"
    assert list(fig.layout.yaxis2.ticktext) == ["5", "0", "5"]      # top = max(新高 3, 新低 5)：刻度显示绝对值
    assert fig.layout.xaxis.type == "category" and fig.layout.xaxis.nticks == 8
    assert fig.layout.hovermode == "x unified" and fig.layout.hoversubplots == "axis"
    assert fig.layout.legend.orientation == "h" and fig.layout.legend.y > 1


def test_breadth_chart_x_is_one_category_per_row_without_calendar_gaps():
    table = _breadth_table(10)
    x = list(breadth_chart(table, None).data[0].x)
    assert x == [pd.Timestamp(d).strftime("%Y-%m-%d") for d in table["date"]]
    assert len(x) == 10 and x[0] == "2026-06-01" and x[-1] == "2026-06-12"   # 跨了一个周末，类目轴不留空档


def test_breadth_chart_span_keeps_only_the_most_recent_rows():
    table = _breadth_table(70)
    x60 = list(breadth_chart(table, 60).data[0].x)
    assert len(x60) == 60 and x60[-1] == list(breadth_chart(table, None).data[0].x)[-1]
    assert len(breadth_chart(table, None).data[0].x) == 70
    assert len(breadth_chart(table, 120).data[0].x) == 70        # 表不足 120 行 → 画全部


def test_breadth_spans_offer_60_120_and_all():
    assert list(charts.BREADTH_SPANS) == ["60", "120", "all"]
    assert [v[1] for v in charts.BREADTH_SPANS.values()] == [60, 120, None]
```

- [ ] **Step 2: 跑，确认失败** → `.venv/bin/python -m pytest tests/test_charts.py -q` → ImportError（`breadth_chart` 不存在），整文件红。

- [ ] **Step 3: 实现**

3a. `src/quant/report/charts.py`：在 `KLINE_SPANS` 定义之后加

```python
#: 趋势宽度图表（首页，2026-10-06）上半幅的三条线：(均线天数, 显示名, 线型)。显示名与首页卡片标签同文
#: （tests/test_dashboard_home.py 钉住不漂）；琥珀 / 灰 / 暖白三色之间色差不够大，再用实线 / 虚线 / 点线区分
#: （同 equity_chart 给基准线加点线）。
BREADTH_LINES: tuple[tuple[int, str, str], ...] = (
    (20, "站上 20 日线", "solid"), (60, "站上 60 日线", "dash"), (200, "站上 200 日线", "dot"))

#: 趋势宽度图表显示最近多少个交易日：内部键 → (显示名, 行数；None = 全部)。同 KLINE_SPANS：键永不改。
BREADTH_SPANS: dict[str, tuple[str, int | None]] = {
    "60": ("60 个交易日", 60),
    "120": ("120 个交易日", 120),
    "all": ("全部", None),
}
```

3b. 文件末尾加

```python
# ================================================================ 趋势宽度（首页，2026-10-06）

def breadth_chart(table: pd.DataFrame, span: int | None) -> go.Figure:
    """首页「趋势宽度」卡片下方那张图：上半幅三条「站上 N 日线」占比线 + 50% 虚线，下半幅 52 周新高红柱向上 /
    新低绿柱向下。`table` 是指标表（按日期升序，只含有快照的日子），`span` 只画最近多少行，None 画全部。

    **单图、一条横轴、两个纵轴区域，不用 make_subplots**：分行子图各有自己的横轴，`hovermode="x unified"` 会上下各弹
    一个提示框，看不到「同一天的三个占比加新高新低」（真浏览器实测）。横轴是交易日的类目轴（同 K 线图），不留周末空档。
    分母为 0 的日子记 NaN——线断开，不画成 0%。下半幅的刻度用 tickvals/ticktext 显示绝对值（tickformat 打不出绝对值），
    新低柱 y 取负、悬停用 customdata 显示正数；悬停模板里不写 trace 名（统一悬停自带名字，写了就成「新低 : 新低 87 只」）。
    """
    rows = table if span is None else table.tail(span)
    x = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in rows["date"]]
    fig = go.Figure()
    for n, name, dash in BREADTH_LINES:
        denom = rows[f"n{n}"].astype(float)
        ratio = (rows[f"above{n}"] / denom.where(denom > 0)).tolist()     # 分母 0 → NaN
        fig.add_trace(go.Scatter(x=x, y=ratio, name=name, mode="lines", line=dict(width=2, dash=dash),
                                 hovertemplate="%{y:.1%}"))
    fig.add_hline(y=0.5, line=dict(dash="dot", color=palette.NEUTRAL))
    highs = [int(v) for v in rows["new_high"]]
    lows = [int(v) for v in rows["new_low"]]
    fig.add_trace(go.Bar(x=x, y=highs, name="新高", marker_color=palette.UP, hovertemplate="%{y:,d} 只", yaxis="y2"))
    fig.add_trace(go.Bar(x=x, y=[-v for v in lows], name="新低", marker_color=palette.DOWN, customdata=lows,
                         hovertemplate="%{customdata:,d} 只", yaxis="y2"))
    top = max(max(highs, default=0), max(lows, default=0), 1)
    fig.update_layout(
        xaxis=dict(type="category", categoryorder="category ascending", nticks=8, anchor="y2"),
        yaxis=dict(domain=[0.36, 1.0], range=[0, 1], tickformat=".0%", tickvals=[0, 0.25, 0.5, 0.75, 1]),
        yaxis2=dict(domain=[0.0, 0.30], anchor="x", tickvals=[-top, 0, top],
                    ticktext=[f"{top:,d}", "0", f"{top:,d}"]),
        barmode="relative", hovermode="x unified", hoversubplots="axis", height=420,
        margin=dict(t=40, b=30, l=50, r=20), legend=dict(orientation="h", y=1.02, yanchor="bottom"))
    return _apply_dark(fig)
```

- [ ] **Step 4: 跑** → `.venv/bin/python -m pytest tests/test_charts.py -q` 全 PASS（含四条暗色基线 parametrize 的 `breadth` 项、`charts.py` 无裸写色值）。

- [ ] **Step 5: 提交**

```bash
git add src/quant/report/charts.py tests/test_charts.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(charts): 趋势宽度图表——单图双纵轴，三条占比线 + 50% 虚线，新高红柱向上 / 新低绿柱向下，交易日类目轴，60/120/全部

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: 首页接上图表，卡片只留数字；文案

**Files:** Modify `app/pages_home.py`、`app/guide.py`；Test `tests/test_dashboard_home.py`、`tests/test_guide.py`

- [ ] **Step 1: 改测试、写失败的测试**

1a. `tests/test_dashboard_home.py`：删掉 `test_enough_history_shows_sixty_day_trend_lines_and_the_help` 与
`test_ma_card_notes_say_the_dashed_line_is_fifty_percent` 两条；在原第一条的位置加：

```python
def test_trend_block_has_numbers_only_cards_and_one_chart_with_a_span_switch(tmp_path):
    """2026-10-06：四张卡片只留数字；走势看卡片下方那张两行图表（坐标轴、图例、悬停），带 60/120/全部 切换。"""
    import json
    from quant.report import charts
    _breadth_fixture(tmp_path, 70)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    for label in ("站上 20 日线", "站上 60 日线", "站上 200 日线", "52 周新高 / 新低"):
        assert "qd-card-img" not in _card(at, label), label
    assert "qd-card-note" not in _card(at, "站上 20 日线"), "有数的卡片不再有「近 N 个交易日」注脚"
    assert "还差约" in _card_note(_card(at, "站上 200 日线")), "「—」的卡片仍写缺多少天"
    g = app_module("guide")
    assert g.HOME_HELP["above_ma"].format(n=20) in _card(at, "站上 20 日线")
    assert [name for _, name, _ in charts.BREADTH_LINES] == [g.HOME_ABOVE_MA.format(n=n) for n, _, _ in charts.BREADTH_LINES], \
        "图例名与卡片标签不许漂"
    r = at.radio(key="home_trend_span")
    assert r.options == [v[0] for v in charts.BREADTH_SPANS.values()] and r.value == "60"   # AppTest 读到的是显示名
    el = at.get("plotly_chart")
    assert len(el) == 1, "四张卡片下方恰一张图表"
    assert len(json.loads(el[0].proto.spec)["data"][0]["x"]) == 60
    assert "displayModeBar" in el[0].proto.config
    assert g.HOME_TREND_CHART_NOTE in _captions(at)
    at = r.set_value("120").run()
    assert not at.exception, at.exception
    assert len(json.loads(at.get("plotly_chart")[0].proto.spec)["data"][0]["x"]) == 70    # 表只有 70 行 → 画全部
    assert at.radio(key="home_trend_span").value == "120"
```

1b. `test_a_breadth_table_with_wrong_columns_degrades_only_that_block` 末尾加两行：

```python
    assert at.get("plotly_chart") == [], "表读不出就没有图表"
    assert "home_trend_span" not in {r.key for r in at.radio}
```

1c. `tests/test_guide.py::test_above_ma_help_is_a_template_for_each_window`：删掉
`assert "虚线是 50%" in text and "0–100%" in text, ...` 与 `assert guide.HOME_ABOVE_MA_NOTE.format(n=60) == ...` 两行；
在其后加 `assert "小图" not in text and "虚线" not in text, "图表自带坐标轴与说明，帮助里不再讲小图"`。
在同文件 `test_high_low_total_is_a_template` 之后加：

```python
def test_trend_chart_copy_exists_and_is_plain():
    assert guide.HOME_TREND_SPAN_LABEL == "显示最近"
    note = guide.HOME_TREND_CHART_NOTE
    assert "上半幅" in note and "下半幅" in note and "50%" in note and "**" not in note
    assert not hasattr(guide, "HOME_RECENT_DAYS") and not hasattr(guide, "HOME_ABOVE_MA_NOTE"), "小图的注脚常量已删"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py -q -k "span_switch or wrong_columns"` → 1 FAIL（`span_switch`：卡片里有 `qd-card-img`）；
`wrong_columns` 本就通过（降级分支既无图表也无 radio，新加的两条断言此时就成立）
Run: `.venv/bin/python -m pytest tests/test_guide.py -q -k "above_ma or trend_chart_copy"` → 2 FAIL

- [ ] **Step 3: 实现**

3a. `app/guide.py`：
- `HOME_HELP["above_ma"]` 改回不带小图那句：
  `"above_ma": "收盘价高于自己近 {n} 日均价的股票占比。过半说明多数股票处在上升段，反之则是普跌。",   # 页面按 20/60/200 填`
- 删掉 `HOME_RECENT_DAYS = "近 {n} 个交易日"` 与 `HOME_ABOVE_MA_NOTE = ...` 两行，原位置改为：

```python
HOME_TREND_SPAN_LABEL = "显示最近"         # 趋势宽度图表上方的切换：60 / 120 / 全部 个交易日
HOME_TREND_CHART_NOTE = ("上半幅：收盘价站上各自 20 / 60 / 200 日均线的股票占比，虚线是 50%，过半说明多数股票在均线之上；"
                         "下半幅：创 52 周新高（向上）与新低（向下）的只数。")
```

3b. `app/pages_home.py`：
- import 行 `from quant.report import fmt, minicharts` 改为 `from quant.report import charts, fmt, minicharts`。
- 删掉 `TREND_DAYS_SHOWN = 60` 那一行。
- `_trend_cards` 从 `tail = table.tail(TREND_DAYS_SHOWN)` 起到函数末尾换成：

```python
    last = table.iloc[-1]
    have, cal, gaps = set(ms.snapshot_dates()), ms.load_calendar(), ms.permanent_gaps()
    cols = st.columns(4)
    for i, (col, (n, above, denom)) in enumerate(zip(cols, TREND_WINDOWS)):
        label = guide.HOME_ABOVE_MA.format(n=n)
        tip = dict(help=guide.HOME_HELP["above_ma"].format(n=n), help_align=TREND_HELP_ALIGN[i])
        if last[denom] == 0:
            col.html(theme.stat_card(label, fmt.MISSING, note=_shortfall_note(n, asof, cal, have, gaps), **tip))
            continue
        pct = 100 * last[above] / last[denom]
        col.html(theme.stat_card(label, f"{pct:.1f}%", sub=f"{int(last[above])}/{int(last[denom])}", **tip))
    hl_tip = dict(help=guide.HOME_HELP["high_low"], help_align=TREND_HELP_ALIGN[3])
    if last["n_hl"] == 0:
        note = _shortfall_note(breadth.HIGH_LOW_WINDOW, asof, cal, have, gaps)
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, fmt.MISSING, note=note, **hl_tip))
    else:
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, f"{int(last['new_high'])} / {int(last['new_low'])}",
                                     sub=guide.HOME_HL_TOTAL.format(n=int(last["n_hl"])), **hl_tip))
    _trend_chart(table)


def _trend_chart(table: pd.DataFrame) -> None:
    """四张卡片下方的正式图表（2026-10-06）：小图没有坐标轴，用户看不懂；改成带日期横轴、百分比纵轴、图例与悬停的
    两行图。它在盘中 30 秒刷新的 fragment 之外（收盘数据一天一变），一张交互图不影响刷新节奏。切换用 st.radio
    记在 session_state（键 home_trend_span），整页重跑也保留。"""
    span = st.radio(guide.HOME_TREND_SPAN_LABEL, list(charts.BREADTH_SPANS), horizontal=True,
                    key="home_trend_span", format_func=lambda k: charts.BREADTH_SPANS[k][0])
    st.plotly_chart(charts.breadth_chart(table, charts.BREADTH_SPANS[span][1]), width="stretch",
                    config={"displayModeBar": False, "displaylogo": False})
    st.caption(guide.HOME_TREND_CHART_NOTE)
```

（`_trend_cards` 顶部 `table is None or table.empty` 的降级分支不变；`asof` 那两行不变。）

- [ ] **Step 4: 跑** → `.venv/bin/python -m pytest tests/test_dashboard_home.py tests/test_guide.py tests/test_dashboard_import.py -q` PASS；
  全量 `.venv/bin/python -m pytest -q` → 只剩 `tests/test_minicharts.py` 里 `trend_line` / `highs_lows` 的测试（Task 3 删）仍 PASS，不应有失败。

- [ ] **Step 5: 提交**

```bash
git add app/pages_home.py app/guide.py tests/test_dashboard_home.py tests/test_guide.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(home): 趋势宽度卡片只留数字，卡片下方一张两行图表（三条占比线 + 50% 虚线 / 新高新低柱），60/120/全部 切换

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: 删小图死代码、旧说法同步、变异自查

**Files:** Modify `src/quant/report/minicharts.py`、`tests/test_minicharts.py`、`src/quant/market/__init__.py`、`docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md`

- [ ] **Step 1: 删死代码**

- `src/quant/report/minicharts.py`：删掉 `MARK_W, MARK_H = ...` 行、`_ok`、`trend_line`、`highs_lows` 三个函数；
  `W, H = 120, 32          # 分时/趋势小图` 的注释改为 `# 分时小图`；模块 docstring 首行「首页小图」保持，不用改。
  删前 `grep -rnw "trend_line\|highs_lows\|_ok\|MARK_W" app/ src/ tests/` 确认只剩本文件与 `tests/test_minicharts.py`（用 `-w`：
  `_ok(` 会误中 `test_limit_ok(` 之类的名字）。
- `tests/test_minicharts.py`：删掉 `_attrs`、`_ys`、`test_trend_line_*` 五条、`test_highs_lows_bars_point_opposite_ways`。

- [ ] **Step 2: 旧说法**

- `src/quant/market/__init__.py` 第 22 行注释 `# MA200 + 52 周 250 + 小图 60` → `# MA200 + 52 周 250 + 图表默认 60`（代码不动）。
- `docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md`：
  - 第 82 行「每张卡片附「分子/分母」只数，外加一幅近 60 个交易日的小图：均线占比用单条灰线；新高/新低画成上红下绿的柱，这里红绿本来就是涨跌语义。」
    → 「每张卡片附「分子/分母」只数；走势在四张卡片下方一张两行图表里（2026-10-06 由小图改来，见 `2026-10-06-trend-breadth-chart-design.md`）：上半幅三条占比线 + 50% 虚线，下半幅新高红柱向上 / 新低绿柱向下，带 60/120/全部 切换。」
  - 第 238 行「再加小图 60 天」→「再加图表默认显示的 60 天」。
  - 第 458 行「六张分时小图、比例条、23 档柱状图、四张趋势小图、新高/新低图都完整显示」→
    「六张分时小图、比例条、23 档柱状图都完整显示（四张趋势小图与新高/新低图 2026-10-06 已换成 plotly 图表）」。

- [ ] **Step 3: 全量** → `.venv/bin/python -m pytest -q` 全 PASS；`grep -rn "trend_line\|highs_lows\|HOME_RECENT_DAYS\|HOME_ABOVE_MA_NOTE\|TREND_DAYS_SHOWN" app/ src/ tests/ | grep -v hasattr`
  → 无输出（`tests/test_guide.py` 里那条 `hasattr(...)` 断言本来就提到这两个旧常量名）。

- [ ] **Step 4: 变异自查**（`cp` 备份 → 改 → `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest <tests> -q -x` 应 FAIL → `mv` 还原）

| # | 改动 | 应红 |
|---|---|---|
| 1 | `breadth_chart` 删掉 `add_hline` | `tests/test_charts.py` |
| 2 | 折线 `line=dict(width=2, dash=dash, color=palette.UP)` | `tests/test_charts.py` |
| 3 | 新低柱 `y=lows`（不取负） | `tests/test_charts.py` |
| 4 | `rows = table`（`span` 不生效） | `tests/test_charts.py`、`tests/test_dashboard_home.py` |
| 5 | `xaxis` 去掉 `type="category"` | `tests/test_charts.py` |
| 6 | 两组柱去掉 `yaxis="y2"` | `tests/test_charts.py` |
| 7 | 20 日线卡片加回 `img=minicharts.sparkline([(0, 1), (1, 2)], 1)` | `tests/test_dashboard_home.py` |

- [ ] **Step 5: 提交**

```bash
git add src/quant/report/minicharts.py tests/test_minicharts.py src/quant/market/__init__.py docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "chore(home): 删掉趋势小图的死代码（trend_line/highs_lows），旧说法同步

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: 真浏览器验收（主会话做，8502 端口自建实例）

- [ ] 四张卡片只有数字；下方图表两行：横轴日期、上半幅纵轴 0–100% 带 50% 虚线与三条线（实 / 虚 / 点）、下半幅红柱向上绿柱向下、图例五项在图上方。
- [ ] 悬停某一天：一个提示框里是日期、三个占比、新高 N 只、新低 N 只（正数）。
- [ ] 切「120 个交易日」「全部」图表随之变化，切回「60」复原；页面其他部分不动。
- [ ] 1280 与 1024 宽下图表随容器宽、不横向溢出。
- [ ] 结果写进设计文档末尾「§7 验收记录」并提交。
