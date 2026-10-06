# 趋势宽度：四张小图合成卡片下方一张正式图表（2026-10-06）

分支：`feat/trend-chart`（从 main 拉出；main 已含 `2026-10-06-trend-chart-scale` 那次小图改版）。做完合回 main；推送公开仓前先问用户。

## 0. 起因与用户决定

小图改成固定尺子加 50% 虚线之后，用户仍然觉得「既没有横轴也没有纵轴，看不懂」，注脚「近 60 个交易日 · 虚线 = 50%」也看不懂。
一张 120×32 的无字小图承载不了「占比随时间的走势」这个信息。

**用户选定（方案甲）**：卡片下方一张**两行的正式图表**——上行三条占比线 + 50% 虚线，下行新高红柱 / 新低绿柱；
四张卡片**只留数字**，四张小图全部去掉；带「60 / 120 / 全部」的时间切换。

否决：乙（只合并三条线、52 周卡片留小柱图——新高新低依旧没有坐标轴）；丙（不加时间切换——310 天历史永远看不到）。

## 1. 图表（`src/quant/report/charts.py`）

- 新函数 `breadth_chart(table: pd.DataFrame, span: int | None) -> go.Figure`。`table` 是 `store.load_breadth()` 的指标表
  （列 `date, above20, n20, above60, n60, above200, n200, new_high, new_low, n_hl`，按日期升序，只含有快照的日子）；
  `span` 是只画最近多少行，`None` 画全部。空表由页面挡住，函数不负责。
- **一张图、一条横轴、两个纵轴区域**——**不用 `make_subplots`**：评审在真浏览器里实测，分行子图各有自己的横轴（`xaxis2` 只是
  `matches` 上行），`hovermode="x unified"` 会上下两行各弹一个提示框，看不到「同一天的三个占比加新高新低」。
  单图写法（`go.Figure()`）三条线用默认的 `x/y`，两组柱 `yaxis="y2"`，布局：
  `xaxis=dict(type="category", categoryorder="category ascending", nticks=8, anchor="y2")`，
  `yaxis=dict(domain=[0.36, 1.0], range=[0, 1], tickformat=".0%", tickvals=[0, .25, .5, .75, 1])`，
  `yaxis2=dict(domain=[0.0, 0.30], anchor="x", tickvals=[-lo_top, 0, hi_top], ticktext=[f"{lo_top:,d}", "0", f"{hi_top:,d}"])`
  （`hi_top = max(新高最大值, 1)`、`lo_top = max(新低最大值, 1)`，**两侧各按自己的最大值**：纵轴范围是自动的、通常不对称，
  共用一个最大值会让小的那一侧刻度落到范围外、整侧没有刻度——真机验收抓到；`tickformat` 打不出绝对值，只能 `tickvals/ticktext`），
  `barmode="relative"`、`hovermode="x unified"`、`hoversubplots="axis"`。评审实测：一个提示框里是日期、三个占比、新高 N 只、新低 N 只。
  - **上半幅**：三条折线。图例名是图表的一部分，放在 `charts.py` 的常量
    `BREADTH_LINES = ((20, "站上 20 日线", "solid"), (60, "站上 60 日线", "dash"), (200, "站上 200 日线", "dot"))`
    （与 `KLINE_FREQS` 同类的显示名表，`charts.py` 里本来就有「策略」「回撤」这类中文显示名，不经 `guide`；
    第三项是线型——琥珀 / 灰 / 暖白三色之间的色差不够大，照 `equity_chart` 的做法再用实线 / 虚线 / 点线区分）。
    y = `above{n} / n{n}`，**分母为 0 的日子记 NaN**（线断开，不画成 0%）。配色走 `_DARK_LAYOUT` 的 `colorway`（刻意不含红绿）。
    **50% 一条虚线**：`fig.add_hline(y=0.5, line=dict(dash="dot", color=palette.NEUTRAL))`（单图不带 `row/col`）。
    每条线 `hovertemplate="%{y:.1%}"`、`line=dict(width=2, dash=…)`。
  - **下半幅**：`go.Bar(x=x, y=highs, name="新高", marker_color=palette.UP, hovertemplate="%{y:,d} 只")` 与
    `go.Bar(x=x, y=[-v for v in lows], name="新低", marker_color=palette.DOWN, customdata=lows, hovertemplate="%{customdata:,d} 只")`，
    都 `yaxis="y2"`。悬停模板里**不写** trace 名（统一悬停会自带名字，写了就成「新低 : 新低 87 只」）。
  - **横轴是交易日的类目轴**（与 K 线图同一做法，x 用 `YYYY-MM-DD` 字符串），**不留周末与假日的空档**；`nticks=8`（310 个类目
    不设会每个都标）。
- 图例放在图上方（`legend=dict(orientation="h", y=1.02, yanchor="bottom")`），没有 rangeslider，`height=420`，
  `margin=dict(t=40, b=30, l=50, r=20)`。走 `_apply_dark`（它的 `update_yaxes` 同时覆盖 `yaxis` 与 `yaxis2`）。
- 不做缩放交互：`st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "displaylogo": False})`——
  与「持仓与盈亏」页三张图同一走法；这块内容不在盘中 30 秒刷新的 fragment 里，一张交互图不影响刷新节奏。

### 1.1 时间切换

- `charts.BREADTH_SPANS: dict[str, tuple[str, int | None]] = {"60": ("60 个交易日", 60), "120": ("120 个交易日", 120), "all": ("全部", None)}`
  （与 `KLINE_SPANS` 同形：内部键永不改，显示名随时可改）。
- 页面：`st.radio(guide.HOME_TREND_SPAN_LABEL, list(charts.BREADTH_SPANS), horizontal=True, key="home_trend_span",
  format_func=lambda k: charts.BREADTH_SPANS[k][0])`，默认第一项「60 个交易日」。放在图表正上方。
  表里不足所选天数时画全部（`table.tail(n)` 本来就是这个语义），不报错。

## 2. 页面与文案（`app/pages_home.py`、`app/guide.py`）

- `_trend_cards(ms)`：
  - 四张卡片**不再传 `img`**；「有数」那一支**不再传 `note`**（「近 N 个交易日」没有图可指了）；「—」那一支的缺多少天注脚不动。
    卡片剩：标签 + 帮助圆点、数值、副文（`2070/5004`、`共 4922 只`）。
  - 四张卡片之后：时间切换 + 图表 + 一行说明 `st.caption(guide.HOME_TREND_CHART_NOTE)`。表读不出或为空时（现有的降级分支）
    只画区块标题，不出切换与图表。
  - `TREND_DAYS_SHOWN` 常量删掉（它只服务小图的 `tail(60)`；卡片数值用 `table.iloc[-1]`，图表用 `BREADTH_SPANS`）。
- `app/guide.py`：
  - 新增 `HOME_TREND_SPAN_LABEL = "显示最近"`；
  - 新增 `HOME_TREND_CHART_NOTE = "上半幅：收盘价站上各自 20 / 60 / 200 日均线的股票占比，虚线是 50%，过半说明多数股票在均线之上；下半幅：创 52 周新高（向上）与新低（向下）的只数。"`；
  - 删掉 `HOME_RECENT_DAYS` 与 `HOME_ABOVE_MA_NOTE`（无人使用）；
  - `HOME_HELP["above_ma"]` 去掉末尾「小图固定按 0–100% 画，虚线是 50%。」那句（图表自带坐标轴与说明），其余不动；
    `HOME_HELP["high_low"]` 不动。
- `src/quant/report/minicharts.py`：删掉 `trend_line`、`highs_lows`、`_ok`、`MARK_W, MARK_H`（无人使用；模块 docstring 与 `W, H` 的注释
  改成只说分时小图）。`sparkline`、`histogram`、`ratio_bar`、`_scale` 不动。

## 3. 旧说法同步

- `docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md`：第 82 行附近「均线占比用单条灰线」以及第 238、458 行附近
  提到趋势小图的句子，改成「卡片只留数字，下方一张两行图表（2026-10-06，见本文）」。`README.md` 里的「小图」都是指数分时小图，不用改。
- `src/quant/market/__init__.py` 第 23 行注释 `# MA200 + 52 周 250 + 小图 60` → `# MA200 + 52 周 250 + 图表默认 60`。
- 项目记忆 `quant-panel-ux-decisions.md`：上一条「趋势宽度小图用固定 0–100% 尺子」改为「2026-10-06 当天再改：小图整体换成
  卡片下方一张正式图表；minicharts 只剩分时/分布/比例条」。

## 4. 测试

- `tests/test_charts.py`：
  - `breadth_chart`：3 条折线 `trace.yaxis in (None, "y")`、2 组柱 `trace.yaxis == "y2"`，5 条 trace 都在同一条横轴 `x` 上；
    折线名与线型 = `BREADTH_LINES`；`fig.layout.colorway == palette.CHART_SERIES` 且折线 `line.color not in (palette.UP, palette.DOWN)`；
    新高柱 `marker.color == palette.UP` 且 y ≥ 0，新低柱 `palette.DOWN` 且 y ≤ 0、`customdata` 是正数、`hovertemplate` 含 `customdata` 与「只」；
  - 分母为 0 的行折线 y 是 NaN；
  - `yaxis.range == (0, 1)`、`tickformat == ".0%"`；有一条 `y0 == y1 == 0.5`、`dash == "dot"` 的 shape；
    `yaxis2` 的 `tickvals == [-新低最大值, 0, 新高最大值]`、`ticktext` 三项都是非负整数字符串（夹具：`["5", "0", "3"]`）；
    `xaxis.anchor == "y2"`、`barmode == "relative"`、`legend.yanchor == "bottom"`、三条线 `hovertemplate == "%{y:.1%}"`；
  - 横轴 `type == "category"`、`nticks == 8`、x 是 `YYYY-MM-DD` 字符串、与表的行一一对应（无周末空档）；
  - `span=60` 只画最后 60 行，`span=None` 画全部，表不足 60 行画全部；
  - 图例 `orientation == "h"` 且在图上方；`hovermode == "x unified"`、`hoversubplots == "axis"`；
  - `BREADTH_SPANS` 的键是 `("60", "120", "all")`、值 `(60, 120, None)`；
  - 把 `breadth_chart(...)` 加进 `_figs()` 与四条暗色基线的 parametrize（底色 / 字体 / 网格 / colorway）。
- `tests/test_dashboard_home.py`：
  - 70 天夹具：`len(at.get("plotly_chart")) == 1`；`r = at.radio(key="home_trend_span")`：`r.options == [v[0] for v in BREADTH_SPANS.values()]`
    （AppTest 读到的是 `format_func` 之后的显示名）、`r.value == "60"`；`json.loads(el.proto.spec)["data"][0]["x"]` 长 60；
    `r.set_value("120").run()` 后长 70（表只有 70 行，画全部）；`el.proto.config` 含 `displayModeBar`（别碰 `el.value`，会 KeyError）；
  - `charts.BREADTH_LINES` 的显示名 == `guide.HOME_ABOVE_MA.format(n=…)`（图例与卡片标签不许漂）；
  - 四张趋势卡片的 HTML 里**没有** `qd-card-img`（首页其余板块的小图照旧，`body.count("qd-card-img") >= 7` 那条既有断言仍过）；
    「有数」的卡片没有 `qd-card-note`；「—」的卡片仍有缺多少天的注脚；
  - 说明行 `guide.HOME_TREND_CHART_NOTE` 在 `at.caption` 里；
  - 表读不出（既有的「列不对」测试）：`at.get("plotly_chart") == []`、`"home_trend_span" not in {r.key for r in at.radio}`；
  - 改写：`test_enough_history_shows_sixty_day_trend_lines_and_the_help`（去掉 `qd-card-img` 与「近 60 个交易日」断言，改断言上面这些）；
    `test_ma_card_notes_say_the_dashed_line_is_fifty_percent` 删掉；`test_each_trend_card_explains_only_itself…` 里
    `"近 60 日" in _card(...)` 那条改为检查帮助文案仍含「近 60 日均价」。
- `tests/test_minicharts.py`：删掉 `trend_line`、`highs_lows` 的测试（含 `_attrs`/`_ys` 若无他用）。
- `tests/test_guide.py::test_above_ma_help_is_a_template_for_each_window`：删掉「虚线是 50%」「0–100%」那条断言与 `HOME_ABOVE_MA_NOTE`
  那条，保留 `{n}` / `近 {n} 日` 的两条。
- 变异自查：删掉 50% 虚线、折线颜色换成 `palette.UP`、新低柱 y 取正、`span` 不生效、横轴改成日期轴、柱子挪回 `yaxis="y"`、
  卡片重新传 `img`——各至少一条测试红。

## 5. 验收（真浏览器，8502 端口自建实例）

- 四张卡片只有数字；下方图表两行，横轴是日期、上行纵轴 0–100% 带 50% 虚线、下行红柱向上绿柱向下；图例三项；
  悬停某一天显示日期与三个占比、新高新低只数。
- 切到「120 个交易日」「全部」图表随之变化，切回「60」复原；切换不影响页面其他部分。
- 1280 与 1024 宽下图表宽度随容器、不横向溢出。

## 6. 不做

- 不做缩放、不做日期范围拖选（切换三档够用）。
- 不改指数分时小图、分布柱状图、比例条。
- 不把图表放进盘中刷新的 fragment。

## 7. 验收记录（2026-10-06，真浏览器，8502 端口自建实例）

实现提交 `8b08d01`（图表函数）、`8b4a4b3`（首页接入）。1280 宽：

- 四张卡片只剩数字（无小图、无「近 N 个交易日」注脚）；下方一张图表 820×420，图例五项在图上方（三条线实 / 虚 / 点），
  横轴 8 个日期刻度（2026-07-03 … 2026-09-21），上半幅纵轴 0% / 25% / 50% / 75% / 100% 带 50% 虚线，下半幅红柱向上、绿柱向下。
- 悬停 2026-08-25：**一个提示框**——「站上 20 日线 54.5% · 站上 60 日线 47.5% · 站上 200 日线 22.3% · 新高 19 只 · 新低 12 只」。
- 切「全部」：310 个点（2025-06-23 … 2026-09-24）；切换是整页重跑，其余板块不动。
- 1024 宽：图表宽 564、随容器、不横向溢出。
- 说明行「上半幅：…；下半幅：…」在图下。
- **发现并改**：下半幅纵轴刻度按两侧共用的最大值取，坐标范围却是自动的，新高一侧的刻度落在范围外、只显示了「1,167」与「0」
  （7 月那天新低 1167 只）。改为两侧各按自己的最大值标刻度（见 §1 修订）。
