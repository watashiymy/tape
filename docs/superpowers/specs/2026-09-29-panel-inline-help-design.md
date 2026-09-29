# 面板就地帮助改版：去掉弹出框，改成紧贴对象的小圆圈「?」（2026-09-29）

分支：`feat/v0.6-market-overview`（随市场概览首页一起合并）。

## 0. 起因与用户决定

用户截图圈出首页三个「? ⌄」按钮：「很奇怪，不知道是什么东西」。实测原因三条：

1. **长得像下拉框。** 它们是 `st.popover("?")`。Streamlit 1.61.1 给每个弹出框强制加下拉箭头
   （只有标签恰为 `:material/menu:` 等三个图标时例外），所以「?」看起来是选择框。
2. **不挨着被解释的东西。** 温度计那个在 2×2 卡片下方；趋势宽度那个在四卡下方、贴着第一张；
   按钮区那个在按钮**左边**的 1/7 列里，与按钮之间空出约 80 px。后两个在同一竖线上上下相距 56 px。
3. **打开要自己对号。** 温度计弹出框一次列四段，「涨停」「跌停」两段不写自己说的是哪个数。

同一页上成交额那行和分布图说明末尾，已经有 `st.caption(help=)` 的原生小圆圈「?」，悬停即出说明——
这是用户认得的样子。对照页（三方案并排）实测后，**用户选定**：

- **方案甲**：每张卡片标签后紧跟一个小圆圈「?」，悬停只解释这一个数；首页按钮去掉旁边的「?」，
  说明改为悬停按钮本身出现；三个弹出框全部去掉。
- 同批顺手改：**任务控制台**三张任务卡片的「?」按同一样式改；**六张指数卡片**分时小图对齐；
  **「52 周新高 / 新低」**卡片小图与左边三张对齐。

否决的两案（留作记录，别再提）：区块标题旁放原生圆圈（`st.html` 默认铺满整行，圆圈被挤到行尾，又离标题很远）；
弹出框挪到标题行右侧（下拉箭头去不掉，仍像选择框）。

## 1. 帮助圆点（新构件）

### 1.1 结构、外观与行为

HTML 结构（类名定死，旧的无人使用的 `.qd-help` 规则在同一改动里删掉，避免新类误继承它）：

```html
<span class="qd-help-dot [qd-help-end] [qd-help-wide]" tabindex="0">?<span class="qd-help-tip">…</span></span>
```

- **圆点**：直径约 0.95rem 的描边圆圈，里面一个「?」；`position: relative`；颜色 `--qd-muted`，
  悬停或键盘选中时圆圈与字变 `--qd-text`。尺寸与观感向原生 `help=` 图标看齐。
  圆点**自己的字也定死**，不继承所在位置：`display: inline-flex` 居中、`font-family: var(--qd-sans)`、
  约 0.65rem、`font-weight: 400`、`letter-spacing: normal`、`line-height: 1`、`vertical-align: middle`。
  （否则控制台标题里的圆点会拿到 1.05rem 衬线加粗的「?」，几乎撑满圆圈，和首页卡片里的圆点长得不一样。）
- **提示框** `.qd-help-tip`：平时 `display: none`；圆点 `:hover` 或 **`:focus-visible`** 时 `display: block`。
  用 `:focus-visible` 而不是 `:focus`：鼠标点一下带 `tabindex` 的元素也会让它获得焦点，用 `:focus` 就成了
  「点一下钉住、移开也不消失」，而这不在本次要做的范围内（见 §7）；`:focus-visible` 只在键盘选中时生效。
  `position: absolute`，在圆点下方；**正的 `z-index`**（Streamlit 每个元素容器都是 `position: relative`，
  不设层级就会被后面的卡片、说明行、参数控件盖住）。**无动画**（`tests/test_theme.py` 禁止
  `transition` / `animation` / `@keyframes`）。
- 提示框**自己定死字体**，不继承所在位置：`font-family: var(--qd-sans)`、`font-weight: 400`、
  `letter-spacing: normal`、`white-space: normal`、`text-transform: none`、约 0.8rem、行高 1.5。
  （它在首页挂在 `.qd-card-label` 里，那里是等宽感的 0.06em 字距加 `nowrap`；在控制台挂在 `.qd-section`
  里，那里是衬线字体。不定死的话两处提示框长得不一样。）
- 提示框内部标签固定为：段落 `<p>`、列表 `<ul><li>`、加粗 `<b>`；`.qd-help-tip p` / `ul` / `li` 的外边距与
  列表缩进在本项目 CSS 里**显式写出**（`st.html` 外层给 `ul` 加了很大的左内边距，不写死就看规则顺序碰运气）。
- 底色 `--qd-surface`（新增进 `:root`，值为 `SURFACE`）、1px `--qd-hairline` 描边、文字 `--qd-text`、圆角、投影；
  投影用 `rgba(0,0,0,…)` 这种非十六进制写法。`box-sizing: border-box`，宽度含内边距：
  **窄档 15rem**（默认，首页卡片短句）、**宽档 22rem**（`qd-help-wide`，任务说明）。
- **展开方向**：默认从圆点左侧向右展开；`qd-help-end` 从圆点右侧向左展开。
  **规则：挂点在页面右半边就用 `end`**，使提示框不越出视口右缘（1024 px 宽时右半边的挂点向右展开 15–22rem 会越界）。
- 颜色**只取色板**；所有选择器以 `.qd-` 开头；不用 `<svg>`；不碰 Streamlit 内部类名与 `data-testid`。
- **读屏不在本次范围**：面板只在本机桌面浏览器用（默认绑 127.0.0.1）。圆点可用 Tab 键选中并显示提示框，
  这就是本次的键盘可达性要求；不加 `aria-label`（它会把「?」换成一个需要文案的名字，而样式层不能引用文案层，见 1.3）。

### 1.2 内容格式（极简 Markdown）

提示框文字**先整体 HTML 转义，再**只认三种写法，其余原样显示：

| 写法 | 渲染 |
|---|---|
| `**加粗**`（同一行内，非贪婪；一行可有多处） | `<b>` |
| 空行分段 | 段落 |
| 行首 `- ` 的列表项；紧随其后、以空格缩进的行是该项的续行 | 列表 |

段内单个换行当作空格（与 Markdown 一致；`JOB_HELP` 的列表项靠两空格缩进的续行写长句）。这三种正好覆盖现有
全部帮助文案：`HOME_HELP` 是纯文字；四条 `JOB_HELP` 只用了加粗、带续行的列表与空行（2026-09-29 逐条核对，
无代码、链接、标题、编号列表、单星号；没有哪一行的 `**` 是奇数个）。**不做通用 Markdown**：要新写法时再扩，并同步本表与测试。

### 1.3 放在哪一层

- 纯函数 `theme.help_dot(text: str, *, align: str = "start", wide: bool = False) -> str`（`align` 取 `"start"` / `"end"`，
  其他值按 `"start"`）。极简 Markdown 转换是它的私有辅助函数，单独单测。
- `theme.stat_card(..., help: str = "", help_align: str = "start")`：`help` 非空时圆点紧跟在标签文字后，**在
  `qd-card-label` 那个 div 里**。`label` 为空（纯图卡）时忽略 `help`。`help` 为空时输出与现在逐字相同。
- `theme.section(title, pill_html="", *, help: str = "", help_align: str = "start", help_wide: bool = False)`：
  圆点紧跟标题文字、在 pill 之前。`help` 为空时输出与现在逐字相同。
- **`theme.py` 不 import `guide`**：测试按文件路径单独加载 `theme.py`，那时 `app/` 不在 `sys.path` 上，import 会让
  `tests/test_theme.py` 收集失败。提示框里的文字全部由页面从 `guide` 取来当参数传进去；圆点本身的「?」是符号，不是文案。

## 2. 各页挂点

### 2.1 首页（`app/pages_home.py`）

- **涨停温度计**：四张卡片各挂自己的一句：`HOME_HELP["limit_up" | "limit_down" | "zb_rate" | "max_lbc"]`。
  温度计整块在页面右侧 1/2.4 那一栏，**四张都用 `end`**。删掉卡片下方的 `st.popover("?")`。
- **趋势宽度**：三张「站上 N 日线」各挂 `HOME_HELP["above_ma"]`，**文案改成带 `{n}` 的模板**
  （「收盘价高于自己近 {n} 日均价的股票占比……」），页面按 20/60/200 填；「52 周新高 / 新低」挂
  `HOME_HELP["high_low"]`。四卡一行：**第 1、2 张 `start`，第 3、4 张 `end`**。
  分母为 0、显示「—」的卡片也挂（那时最需要知道这个数是什么）——每张卡片「有数」与「—」两个分支**都**传
  `help` 与 `help_align`。展开方向随列序定（第 1–4 张：start、start、end、end），写进 `TREND_WINDOWS` 旁的一个
  常量或循环里的 `enumerate`，不要在两个分支里各写一遍字面量。删掉四卡下方的弹出框。
  指标表读不出、整块降级时不渲染卡片，也就没有圆点（与现在弹出框在降级时不出现一致）。
- **按钮区**：去掉 `st.columns([1, 6])` 与其中的弹出框；「开始准备」「更新收盘数据」按钮 `help=JOB_HELP["market_breadth"]`
  （原生按钮提示，支持加粗与列表，实测可用；AppTest 能读到 `Button.help`）。这两个按钮从不禁用。运行中、别的任务在跑、
  未到就绪时刻、已是最新这几态本来就没有这个按钮，也就没有这段说明——与现在一致（弹出框也只在有按钮时出现）。
- 成交额行、分布图说明行的原生 `help=` 不动。

### 2.2 任务控制台（`app/pages_console.py`）

- `_card_head(job, pill_html="", *, help_align="start")`：去掉 `st.columns([5, 1])` 与弹出框，改为
  `theme.section(job.label, pill_html, help=JOB_HELP[job.name], help_align=help_align, help_wide=True)`：
  圆点紧跟卡片标题文字。**不挂在按钮上**：状态文件损坏的分支没有按钮，而 docstring 写明那正是最需要说明的时候。
- 展开方向：流水线那一行里**右栏的卡片（信号跟踪）用 `end`**，其余用 `start`。在 `_PIPELINE` 旁放一个模块级常量
  （如 `_RIGHT_COLUMN = (_PIPELINE[1],)`），`_render_card` 自己据此算出方向，传给**两处** `_card_head`
  （状态文件损坏的分支与正常分支）。两个 fragment 包装函数只转发任务名，签名不用改。
- `_card_head` 的 docstring 改写（「右上角」→「紧跟标题」，保留「帮助必须与标题同一行」的理由）。
- 「人工步骤」那行已在 2026-09-14 改成链接自带 `help=`，不动。

## 3. 两处对齐

### 3.1 六张指数卡片

现状：1280 px 宽时卡片约 123 px，副文「−319.10 · −2.34%」在部分卡片折成两行，卡片高 107/128 px 交替，
分时小图高低不齐。改法：**涨跌额与涨跌幅固定分两行**，去掉中间的「 · 」。

- `theme.stat_card` 的 `sub` 改为 `sub: str | tuple[str, ...] = ""`：`str` 是一行、输出与现在逐字相同；
  `tuple` 每段一个 `<div class="qd-card-sub"{style}>`，每段各自转义、带同一个内联颜色。
  （不能对 `str` 逐元素迭代：「2070/5004」会被拆成一字一行。）
- 首页指数卡片传 `sub=(f"{q.change:+.2f}", f"{q.pct:+.2f}%")`。六张卡片结构一致，任何宽度下小图都在同一水平线上。

### 3.2 「52 周新高 / 新低」卡片

现状：三张「站上 N 日线」有副文「2070/5004」，这张没有，小图高出约 20 px。改法：补一行副文
`guide.HOME_HL_TOTAL = "共 {n} 只"`，`n` 取指标表的 `n_hl`（参与新高新低统计的股票数），与左边三张的分母同义。
`n_hl` 为 0、显示「—」时不出副文（与左边三张分母为 0 时一致）。

## 4. 文案与旧说法同步

- `JOB_HELP["market_breadth"]`：
  - 「需要 {breadth_first_run}」去掉多余空格（现在渲染出「需要 约 110 分钟」）；
  - 首句「**更新收盘数据**——首页「趋势宽度」的数据来源」改成不点名按钮的说法（按钮在从没准备过时叫「开始准备」），
    如「**收盘数据**——「趋势宽度」四张卡片的数据来源」；「结果在哪」改成「就是上方「趋势宽度」那四张卡片」
    （这段说明只在首页那几张卡片下方出现，不再写「首页」）。`**做什么**` `**多久**` `**结果在哪**` 三个加粗标题保留
    （`tests/test_guide.py` 钉着）。
- `guide.py` 三处注释：`PIPELINE_MANUAL_HELP` 上方「人工步骤卡片的 `?` 帮助」、`PIPELINE_MANUAL_NOTE` 上方「比 ? 里的短」、
  `JOB_HELP` 上方「任务卡片右上角 st.popover("?")」，改成现行说法。
- `pages_console.py`：`_manual_step_strip` docstring 里「不再单独开 ? 弹窗」一句改写；`_card_head` docstring 见 2.2。
- `tests/test_guide.py::test_every_job_has_a_popover_help` 改名与 docstring（它只钉键集合）。
- `tests/test_dashboard_guide.py`：文件头注释「右上角的 st.popover」、`CARDS_WITH_HELP` 的注释改写；
  `_popover_label` 等只为弹出框服务的辅助函数，改完无人使用就删掉。
- `README.md`：第 18 行「`?` 帮助都在那儿」与「就地帮助」一节「右上角一个 `?`，点开是……」改为
  「标题旁的小圆圈 `?`，悬停是……」，并补一句首页卡片与按钮的就地说明。
- 首页设计文档 `2026-09-23-market-overview-homepage-design.md` 第 93、225 行改成现行挂法，注明见本文。

## 5. 测试

### 5.1 `tests/test_theme.py`

- `help_dot`：转义（含 `" onmouseover=` 与 `<script>`）在 Markdown 转换之前；三种写法；一行两处加粗（取
  `JOB_HELP["daily_signal"]` 首行那种）；续行并入列表项；段内单换行变空格；`align="end"` 出 `qd-help-end`、
  `wide=True` 出 `qd-help-wide`、未知 `align` 按 start；无 `<svg`。
- `stat_card`：`help` 的圆点在 `qd-card-label` div 内；空标签时不出圆点；`help` 为空时输出与现在逐字相同；
  `sub` 为 `str` 时输出与现在逐字相同、为 `tuple` 时每段一个 `qd-card-sub`、各自转义、同色。
- `section`：圆点在标题文字后、pill 前；`help` 为空时输出与现在逐字相同。
- CSS：用 `test_theme` 现有的选择器正则取规则头来查，`.qd-help-dot` `.qd-help-tip` 各有独立规则，
  `.qd-help-end` `.qd-help-wide` 以后代选择器形式出现（如 `.qd-help-end .qd-help-tip`），不要照抄 `cls + " {"` 的写法；
  提示框规则含 `z-index` 与 `font-family`，圆点规则含 `font-family` 与 `line-height`；显示提示框的规则用
  `:focus-visible`、不含裸 `:focus`；旧 `.qd-help {` 规则已删；无动画、无新十六进制（既有测试照常覆盖）。
- 转换器输出的标签固定为 `<p>` / `<ul><li>` / `<b>`，单测按这个断言。

### 5.2 `tests/test_dashboard_home.py`

- 首页 `at.get("popover") == []`。
- 温度计与趋势宽度每张卡片的 HTML 含**它自己**那句说明、不含别张的；「站上 60 日线」那张含「近 60 日」。
- **展开方向**：涨停、跌停、炸板率、最高连板、站上 200 日线、52 周新高 / 新低六张带 `qd-help-end`；
  站上 20 日线、站上 60 日线两张不带。
- 按钮：`at.button(key="home_breadth_start" | "home_breadth_update").help == JOB_HELP["market_breadth"]`。
- 六张指数卡片各有**两个** `class="qd-card-sub"`、不含「 · 」。
- 52 周卡片「共 N 只」：用 `save_breadth` 写一张末行 `n_hl > 0` 的表，并按既有
  `test_up_to_date_shows_only_the_date_in_the_title` 的做法配齐快照与日历，走非「—」分支；再测 `n_hl == 0` 时无副文。
- 两个分支都要测到：52 周卡片「共 N 只」那条（`n_hl > 0` 分支）同时断言该卡含 `HOME_HELP["high_low"]` 与
  `qd-help-end`；既有 `n_hl == 0` 的趋势夹具覆盖「—」分支。
- 既有测试的调整：`_card` 助手改为按标签前缀 `<div class="qd-card-label">{label}<` 匹配（标签 div 里多了圆点；
  这个前缀对八个标签都唯一，含「站上 20」与「站上 200」）；`_card_value` 不用改。原「`HOME_HELP["above_ma"]` 在
  `at.markdown` 里」那条改为在卡片 HTML 里找 `HOME_HELP["above_ma"].format(n=…)`（模板原文不会出现在页面上）。

### 5.3 `tests/test_dashboard_guide.py` 与 `tests/test_dashboard_console.py`

- 控制台 `at.get("popover") == []`。
- **找卡片标题一律锚定在开头**：`f'class="qd-section">{label}<'`。不能只查 `>{label}<`：每条 `JOB_HELP` 都以
  `**{label}**` 开头，渲染成 `<b>{label}</b>` 后同样包含 `>{label}<`，标题丢了也照样通过。
- 三张卡片：用上面的锚定方式找到各自的 `qd-section`，其 HTML **包含** `theme.help_dot(JOB_HELP[name], align=…, wide=True)`
  的完整输出（用渲染后的形式比，不拿带 `**` 的原文首行去比）；信号跟踪那张的 `align` 是 `end`，另两张是 `start`。
  测试里取 `theme` 的方式照 `tests/test_theme.py` 按文件路径加载（`st.html` 的 proto 体就是服务端原样字符串，可直接比子串）。
- 「帮助与标题同一行」改为：锚定找到的那个 `qd-section` 里含 `qd-help-dot`。
- `test_dashboard_console::test_three_cards_render`：标题断言改为上面的锚定形式。
- **状态文件损坏的分支也测**：写一个坏的任务状态文件，三张卡片的 `qd-section` 仍各含自己的 `help_dot(…)` 完整输出，
  信号跟踪仍是 `end`。
- 控制条那几页（回测报告、个股 K 线、信号）：既无弹出框，`qd-section` 里也**无 `qd-help-dot`**。

### 5.4 变异自查

以下每项各至少让一条测试变红：删掉任一张首页卡片的 `help=`（含 52 周卡片 `n_hl > 0` 分支）；任一张首页卡片的 `end` 反置；
信号跟踪卡片的 `end` 反置（正常分支与状态文件损坏分支各一次）；控制台卡片标题文字丢失；显示规则由 `:focus-visible` 改回
`:focus`；转义与 Markdown 转换的顺序颠倒；指数卡片副文合回一行；52 周卡片不出「共 N 只」；按钮不带 `help=`。

## 6. 验收（真浏览器）

- 1280 px 与 1024 px 两种宽度，逐个悬停首页 8 个圆点与 2 个按钮、控制台 3 个圆点：提示框完整、没被后面的元素盖住、
  不越出视口、文字是对应的那句；Tab 键能依次选中圆点并显示提示框；鼠标点一下圆点再移开，提示框消失；
  提示框里的列表缩进正常；首页与控制台的圆点大小、字形一致。
- 用脚本读六张指数卡片小图、四张趋势卡片小图的 `getBoundingClientRect().top`：各组内相差不超过 1 px。
- 首页与控制台都没有「⌄」下拉箭头。

## 7. 不做

- 不改提示框以外的交互（按钮、进度、刷新节奏都不动）。
- 不做通用 Markdown、不做点击固定的提示框、不做读屏适配：面板只在本机桌面浏览器用（默认绑 127.0.0.1），触屏不在考虑内。
- 不动成交额行、分布图说明、参数控件、链接上已有的原生 `help=`。
