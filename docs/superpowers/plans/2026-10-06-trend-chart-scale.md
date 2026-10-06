# 趋势宽度小图固定尺子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页三张「站上 N 日线」卡片的小图改成固定 0–100% 的尺子，加 50% 虚线和末端标记，注脚与帮助说明虚线含义。

**Architecture:** 只改 `quant.report.minicharts.trend_line` 的几何（签名不变）、两条文案常量与一处注脚调用。页面排版、其他小图不动。

**Tech Stack:** Python 3.14、Streamlit 1.61.1（AppTest）、pytest。

**Spec:** `docs/superpowers/specs/2026-10-06-trend-chart-scale-design.md`。

**分支：** `feat/trend-chart-scale`（已检出，别切）。提交身份 `git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit`，
提交说明末尾空一行加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。所有命令在仓库根目录跑，Python 一律 `.venv/bin/python`。
**不许** `git checkout --` / `git stash`；变异自查用 `cp` 备份、`mv` 还原。

---

### Task 1: `trend_line` 固定尺子、50% 虚线、末端标记

**Files:** Modify `src/quant/report/minicharts.py`（`trend_line`）；Test `tests/test_minicharts.py`

- [ ] **Step 1: 写失败的测试**（追加在 `test_trend_line_is_grey_and_skips_nan` 之后；文件里已有 `_svg`、`re`、`minicharts`、`palette`）

```python
def _attrs(tag: str, svg: str) -> list[dict[str, str]]:
    """svg 里每个 <tag …> 的属性字典（按出现顺序）。"""
    return [dict(re.findall(r'(\w[\w-]*)="([^"]*)"', m)) for m in re.findall(rf"<{tag}\b[^>]*>", svg)]


def _ys(points: str) -> list[float]:
    return [float(p.split(",")[1]) for p in points.split()]


def test_trend_line_uses_a_fixed_zero_to_hundred_scale():
    """占比小图不再按自身最小/最大拉满：三张卡片共用一把 0–100% 的尺子（2026-10-06 用户反馈）。"""
    H = minicharts.H
    full = _attrs("polyline", _svg(minicharts.trend_line([0.0, 1.0])))[0]["points"]
    assert _ys(full) == [H - 2, 2.0], full
    mid = _ys(_attrs("polyline", _svg(minicharts.trend_line([0.4, 0.6])))[0]["points"])
    assert 4 < min(mid) and max(mid) < H - 4, mid            # 旧实现会把 0.4–0.6 拉满画布
    clipped = _ys(_attrs("polyline", _svg(minicharts.trend_line([-0.5, 1.5])))[0]["points"])
    assert clipped == [H - 2, 2.0], clipped                   # 越界按 0/1 截断


def test_trend_line_has_a_fifty_percent_dashed_baseline_under_the_line():
    svg = _svg(minicharts.trend_line([0.4, 0.6]))
    lines = _attrs("line", svg)
    assert len(lines) == 1 and lines[0]["stroke-dasharray"] == "2 2", svg
    assert lines[0]["x1"] == "0" and lines[0]["x2"] == str(minicharts.W)
    assert float(lines[0]["y1"]) == float(lines[0]["y2"]) == minicharts.H / 2
    assert svg.index("<line ") < svg.index("<polyline"), "虚线在折线下面"


def test_trend_line_marks_the_last_valid_point_with_a_near_square_rect():
    """末端标记画在最后一个有效值上（孤立点也标）；用小矩形不用圆——画布横向随卡片拉伸，圆会变成椭圆。"""
    W, H = minicharts.W, minicharts.H
    for values, idx in (([0.4, None, 0.5, 0.6], 3), ([0.4, 0.5, None], 1), ([0.4, 0.5, None, 0.6], 3)):
        svg = _svg(minicharts.trend_line(values))
        rects = _attrs("rect", svg)
        assert len(rects) == 1 and "<circle" not in svg, svg
        r = rects[0]
        cx = float(r["x"]) + float(r["width"]) / 2
        cy = float(r["y"]) + float(r["height"]) / 2
        sx = (W - 2) / (len(values) - 1)
        assert abs(cx - (1 + idx * sx)) < 0.11, (values, cx)
        assert abs(cy - (H - 2 - values[idx] * (H - 4))) < 0.11, (values, cy)
        assert svg.rindex("<polyline") < svg.index("<rect"), "标记在最上层"


def test_trend_line_with_fewer_than_two_points_is_an_empty_frame():
    for values in ([], [0.5], [None, 0.5, None]):
        svg = _svg(minicharts.trend_line(values))
        assert "<polyline" not in svg and "<line " not in svg and "<rect" not in svg, values
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_minicharts.py -q`
Expected: 新 4 条里 3 条 FAIL（固定尺子、虚线、标记）；`fewer_than_two_points` 与既有 `is_grey_and_skips_nan` PASS。

- [ ] **Step 3: 实现**——`trend_line` 整个换成（`_scale` 留给 `sparkline`，别删）：

```python
MARK_W, MARK_H = 1.4, 3.2   # 趋势小图末端标记：画布横向随卡片拉伸 1.3–2.4 倍，这个比例拉伸后接近正方形（圆会变椭圆）


def trend_line(values: Sequence[float | None]) -> str:
    """均线占比的近 60 日走势，**固定 0–100% 的尺子**（0 → 底、1 → 顶），不按序列自身的最小/最大缩放——
    否则站上 200 日线那张 13%–31% 的小幅波动会被拉得和 20 日线 19%–91% 一样剧烈（2026-10-06 用户反馈）。
    画布正中一条 50% 虚线（与分时小图的昨收虚线同一套语言）；单条灰线（零类别配色）；None 处断开；
    末端一个小矩形标出当前位置（画在最后一个有效值上，哪怕它是孤立点）。不足两个有效点只出空画布。
    绘制顺序：虚线 → 折线段 → 标记。"""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return to_data_uri(_frame(W, H, ""))
    pad = 2
    sx = (W - 2) / max(1, len(values) - 1)

    def px(i): return 1 + i * sx
    def py(v): return H - pad - min(1.0, max(0.0, v)) * (H - 2 * pad)
    mid = H / 2
    segs, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append(f"{px(i):.1f},{py(v):.1f}")
    if len(cur) > 1:
        segs.append(cur)
    li, lv = pts[-1]
    body = (f'<line x1="0" y1="{mid:.1f}" x2="{W}" y2="{mid:.1f}" stroke="{palette.NEUTRAL}" '
            f'stroke-width="0.8" stroke-dasharray="2 2"/>'
            + "".join(f'<polyline points="{" ".join(s)}" fill="none" stroke="{palette.NEUTRAL}" '
                      f'stroke-width="1.2"/>' for s in segs)
            + f'<rect x="{px(li) - MARK_W / 2:.1f}" y="{py(lv) - MARK_H / 2:.1f}" width="{MARK_W}" '
              f'height="{MARK_H}" fill="{palette.NEUTRAL}"/>')
    return to_data_uri(_frame(W, H, body))
```

把 `MARK_W, MARK_H` 放在文件顶部 `BW, BH = 300, 6` 之后。

- [ ] **Step 4: 跑** → `.venv/bin/python -m pytest tests/test_minicharts.py tests/test_dashboard_home.py -q` 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/quant/report/minicharts.py tests/test_minicharts.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(home): 趋势宽度小图改固定 0–100% 尺子、正中 50% 虚线、末端小矩形标记（原按自身最小最大拉满，三张图看着一样剧烈）

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: 注脚与帮助文案

**Files:** Modify `app/guide.py`、`app/pages_home.py`；Test `tests/test_guide.py`、`tests/test_dashboard_home.py`

- [ ] **Step 1: 写失败的测试**

1a. `tests/test_guide.py::test_above_ma_help_is_a_template_for_each_window` 末尾加两行：

```python
    assert "虚线是 50%" in text and "0–100%" in text, "帮助里要说清小图的尺子与虚线"
    assert guide.HOME_ABOVE_MA_NOTE.format(n=60) == "近 60 个交易日 · 虚线 = 50%"
```

1b. `tests/test_dashboard_home.py` 末尾追加：

```python
def test_ma_card_notes_say_the_dashed_line_is_fifty_percent(tmp_path):
    """三张均线卡片的注脚带「虚线 = 50%」；52 周卡片的注脚不带（它没有那条虚线）。"""
    import pandas as pd
    _breadth_fixture(tmp_path, 70)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    g = app_module("guide")
    for label in ("站上 20 日线", "站上 60 日线"):
        assert g.HOME_ABOVE_MA_NOTE.format(n=60) in _card(at, label), label
    assert "虚线" not in _card(at, "52 周新高 / 新低")
    # 52 周卡片有数的那一支：注脚仍是「近 N 个交易日」，不带虚线
    ms = _market_store(tmp_path)
    day = _fixture_day()
    days = pd.date_range(day.replace(day=1), day + pd.Timedelta(days=40)).date
    ms.save_calendar(days, {day})
    ms.save_snapshot(day, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                        "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [day], "above20": [1], "n20": [2], "above60": [0], "n60": [0],
                                  "above200": [0], "n200": [0], "new_high": [36], "new_low": [104],
                                  "n_hl": [4922]}))
    at2 = _home(tmp_path)
    hl = _card(at2, "52 周新高 / 新低")
    assert g.HOME_RECENT_DAYS.format(n=1) in hl and "虚线" not in hl, hl
```

（`_breadth_fixture` 写在同一个 `tmp_path` 的 `data/market` 下，第二段用一行表整体覆盖 `breadth.parquet`、再存一天快照即可；
两次 `_home` 之间不需要清缓存——AppTest 每次都是新进程状态。若第二段因残留快照走了「—」分支，就改用新的 `tmp_path / "b"` 目录
另建 `MarketStore` 并把 `copy_app` 指过去——先按上面的写法跑，不行再调。）

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_guide.py -q -k above_ma` → FAIL（`AttributeError: HOME_ABOVE_MA_NOTE`）
Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py -q -k fifty_percent` → FAIL

- [ ] **Step 3: 实现**

3a. `app/guide.py`：`HOME_RECENT_DAYS = "近 {n} 个交易日"` 下一行加

```python
HOME_ABOVE_MA_NOTE = HOME_RECENT_DAYS + " · 虚线 = 50%"   # 三张均线卡片的注脚：小图固定 0–100% 尺子，虚线是 50%
```

`HOME_HELP["above_ma"]` 改为：

```python
    "above_ma": "收盘价高于自己近 {n} 日均价的股票占比。过半说明多数股票处在上升段，反之则是普跌。小图固定按 0–100% 画，虚线是 50%。",   # 页面按 20/60/200 填
```

3b. `app/pages_home.py::_trend_cards`：均线卡片有数那一支的 `note=guide.HOME_RECENT_DAYS.format(n=len(tail))` 改为
`note=guide.HOME_ABOVE_MA_NOTE.format(n=len(tail))`（只改循环里那一处；52 周卡片的两处不动）。

- [ ] **Step 4: 跑** → `.venv/bin/python -m pytest tests/test_guide.py tests/test_dashboard_home.py -q` PASS；全量 `.venv/bin/python -m pytest -q` PASS

- [ ] **Step 5: 变异自查**（`cp` 备份 → 改 → `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest <tests> -q -x` 应 FAIL → `mv` 还原）

| # | 改动 | 应红 |
|---|---|---|
| 1 | `trend_line` 删掉虚线那段 | `tests/test_minicharts.py` |
| 2 | `py` 改回按 `_scale(values)` 自动缩放 | `tests/test_minicharts.py` |
| 3 | 删掉 `<rect` 那段 | `tests/test_minicharts.py` |
| 4 | `<rect …>` 换成 `<circle cx cy r="1.6">` | `tests/test_minicharts.py` |
| 5 | 52 周卡片有数那一支的 `note` 改用 `HOME_ABOVE_MA_NOTE` | `tests/test_dashboard_home.py` |

- [ ] **Step 6: 提交**

```bash
git add app/guide.py app/pages_home.py tests/test_guide.py tests/test_dashboard_home.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "copy(home): 均线卡片注脚「近 N 个交易日 · 虚线 = 50%」，帮助说明小图尺子与虚线

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: 真浏览器验收（主会话做）

重启面板（`src/` 改了，Streamlit 不热载）。
- [ ] 三张均线卡片：虚线在小图正中；末端标记接近正方形；最近一天的线末端分别在虚线下、上、下方靠底；200 日线那张明显比 20 日线平。
- [ ] 注脚「近 60 个交易日 · 虚线 = 50%」；帮助圆点里有虚线那句；52 周卡片不变。
- [ ] 结果写进设计文档末尾「§6 验收记录」并提交。
