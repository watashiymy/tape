# 手册移出侧边栏，页头右端放「帮助中心」入口（2026-09-30）

分支：`feat/help-center`，从 `feat/v0.6-market-overview` 拉出（v0.6 还没合并，首页与导航正是在那条分支上改的；
从 main 拉必然冲突）。做完先合回 `feat/v0.6-market-overview`，再随 v0.6 一起进 main。

## 0. 起因与用户决定

用户原话：「手册」并不是这个系统的功能，不需要一个专门的菜单，而且还放在了比较显眼的位置；在页面右上角放一个
类似「帮助中心」的按钮，链接到手册的页面，会比较合理。

对照页（两种位置并排）实测后，**用户选定「甲」**：「帮助中心」放在**页名那一行的最右端**，紧挨任务状态标签，不多占高度；
页头下方那条细线会在链接前断开一小段，用户接受。否决「乙」（页名上方单独一行）：每页顶部多一行高度。

## 1. 侧边栏

- 「手册」组的三页（使用说明、读懂回测、边界与安全）**全部设为 `visibility="hidden"`**，仍然留在 `st.navigation` 的
  「手册」组里注册。实测（Streamlit 1.61.1，真浏览器）：一个组的页面全部隐藏时，侧边栏**连组头都不出现**；直接打开
  `/guide`、`/guide-metrics`、`/guide-limits` 照常渲染对应页面；`st.page_link` / `st.switch_page` 照常能跳过去。
- 侧边栏从此是三组八页：主组（市场概览、任务控制台、信号）、交易日志（记账、持仓与盈亏）、研究（信号池、回测报告、个股K线）。
- 页面对象、`url_path`、`ui.bind_pages` 的登记都不变：老书签不断，首页新手提示、各处 `page_ref("guide")` 照常可用。

## 2. 「帮助中心」入口

- `ui.page_head(page, *, help_center=True)`：`help_center=True` 时页头改为一行两栏
  `st.columns([4, 1], vertical_alignment="center", gap="small")`：
  - 左栏：原来的页头 HTML（`theme.page_head(...)`，页名 + 一句话说明 + 任务状态标签，HTML 不改）；
  - 右栏：`st.container(horizontal=True, horizontal_alignment="right")` 里放
    `st.page_link(page_ref("guide"), label=guide.HELP_CENTER_LABEL, icon=":material/help:")`，靠右。
  `help_center=False` 时照旧是一个顶层 `st.html`，与现在逐字相同。
- **为什么是分栏不是水平容器**（评审实测）：`st.container(horizontal=True)` 在 1.61.1 强制允许换行，页头 HTML 按自身整行宽度
  排版，1024 宽、侧边栏展开时「帮助中心」会被挤到下一行。分栏不换行（视口窄于 640 px 才上下堆叠，本面板只在桌面用）。
- **窄屏时只让那句说明折行**：`theme.CSS` 里 `.qd-title` 加 `white-space: nowrap; flex-shrink: 0`，`.qd-head-right` 加
  `flex-shrink: 0; white-space: nowrap`，`.qd-sub` 加 `min-width: 0`。实测（对照页，侧边栏 300 px）：1024 宽时较长的几句说明
  折成两行、页头由 36 px 变 55 px，页名与状态标签仍各一行、「帮助中心」仍在同一行最右端；1280 宽起全部一行。
  不加这几条时，左栏变窄会把页名折成「信 / 号」「市场概 / 览」。
- 栏宽 4:1 的依据：1024 宽时右栏约 110 px，放得下图标加「帮助中心」四个字；更宽的屏幕上链接与状态标签之间空得多一些
  （1680 宽时约 156 px），但仍在同一行最右端。
- `guide.HELP_CENTER_LABEL = "帮助中心"`（文案进 `guide`，页面不写字面量）。
- 用 `st.page_link` 而不是页头 HTML 里的 `<a href>`：`<a>` 会整页刷新、开一个新会话（Streamlit 文档原话：
  "Navigating to an internal page by URL starts a new session"），记账页填到一半的内容就丢了；`page_link` 是站内跳转。
- **手册三页自己不放「帮助中心」**（`help_center=False`）：它们顶部有手册的页内导航（§3），再放一个指向第一页的链接是重复。
- 其余八页（市场概览、任务控制台、信号、记账、持仓与盈亏、信号池、回测报告、个股K线）全部有。它们都经 `ui.page_head`，
  一处改动全部覆盖。

## 3. 手册的页内导航

- 手册移出侧边栏后，三页之间要有路可走。`pages_guide._render` 在页头下方加一行
  `st.container(horizontal=True, gap="small")`，按 `guide.GUIDE_PAGES` 的顺序放三个 `st.page_link`
  （标题、图标、`help=` 该页的一句话说明）；**当前页 `disabled=True`**（显示为灰色、点了不动），其余两页可点。
- 「使用说明」页尾原来的目录 `_contents()`（「更多说明」+ 另两页的链接）与这行导航内容重复，**删掉**；
  `guide.GUIDE_MORE_TITLE` 随之无人使用，一并删掉。
- `_contents` 的 docstring 里「几页全在侧栏里看得见，再加一层页内导航就是纯噪声」这条理由从此不成立——
  页内导航的理由改写进 `_render` 的 docstring。
- 说明页「不许出现按钮」的规则不变：`page_link` 是导航链接，不是按钮。

## 4. 不变的

- 首页「第一次用？先看「使用说明」」新手提示保留（只在从没跑过任务时出现，出现在落地页上才有用）。
- 手册内容、分页、页面对象的标题与图标都不动。
- 页头 HTML（`theme.page_head`）本身不改；只加上面三条 CSS。
- **顺手修一处既有错误**：`ui.PAGE_INTRO["记账"]` 写的是「记你**真实成交**的每一笔……」，而页头 HTML 会转义文字，页面上原样显示两对星号
  （2026-09-30 真机确认）。改成不带星号的纯文字。

## 5. 旧说法同步

- `app/dashboard.py`：顶部 docstring 与 `PAGES` 上方的顺序说明里关于「手册」组的几句；`GUIDE_PAGE_OBJS` 的注释；
  `ui.bind_pages` 上方「说明落地页的目录」那句注释（目录已删）。
- `app/ui.py`：`page_head` 的 docstring（说明右栏「帮助中心」与 `help_center` 参数）。
- `app/pages_guide.py`：模块 docstring 与 `_render`、`page_guide` 的 docstring。
- `README.md`：第 177 行附近「侧边栏十一个页面、分四组……「手册」组是……」改成三组八页，并说明手册从各页页名那一行右端的
  「帮助中心」进；第 366 行附近以「「手册」组第一页「使用说明」」开头的**整段重写**（那段里「控制台在从未跑过任务时给一条去这里的
  链接」「/console 会落到控制台」也早已过时：新手提示在市场概览，/console 是普通路由）；第 18 行可顺带提一句「帮助中心」；
  「就地帮助」一节开头那句「散在手册之外的各功能页里」保持。
- 测试里描述手册组位置的注释（`tests/test_dashboard_nav.py`、`tests/test_dashboard_guide.py`）。
- 项目记忆 `quant-panel-ux-decisions.md` 记一笔：手册不占侧边栏，从页头「帮助中心」进（2026-09-30 用户定）；
  `quant-panel-copy-for-users.md` 里「「使用说明」在「手册」组」「控制台给首次运行链接」两处过时说法一并改掉。

## 6. 测试

- `tests/test_dashboard_nav.py`：分组与页序照旧，另加断言——「手册」组三页 `visibility == "hidden"`，其余八页 `"visible"`。
  （`tests/conftest.py` 的 `FakePage` 已有 `visibility` 参数。）
- 八个非手册页：页面上恰有一个标签为「帮助中心」的 `page_link`，目标页是「使用说明」（AppTest 里 `proto.page == "guide"`）；
  它所在的列（`at.get("column")`，权重 1/5）与装着 `qd-head` 的列（权重 4/5）是同一行的两栏。
- 手册三页：没有「帮助中心」链接；恰有三个手册导航链接，按 `GUIDE_PAGES` 顺序，当前页那个 `proto.disabled` 为真、另两个为假；
  「使用说明」页不再出现字样「更多说明」（常量 `GUIDE_MORE_TITLE` 已删，断言用字面量）。
- `theme.CSS` 里 `.qd-title` 含 `white-space: nowrap` 与 `flex-shrink: 0`，`.qd-head-right` 含 `flex-shrink: 0`，`.qd-sub` 含 `min-width: 0`。
- `PAGE_INTRO` 的每一句都不含 `**`（页头是纯文字）。
- **要改写或删掉的既有测试**（`tests/test_dashboard_guide.py`）：`test_the_landing_page_links_to_the_other_pages`（原期望页尾两条目录链接）、
  `test_the_toc_uses_page_links_not_buttons`（原断言链接数 = 页数 − 1）、第 157 行按 `GUIDE_MORE_TITLE` 过滤标题的那处。
  其余既有测试（「说明页没有按钮」「手册每页不放控制条」等）照常通过：`page_link` 不算按钮，禁用的也不算（评审用 AppTest 实测）。

## 7. 验收（真浏览器）

- 侧边栏没有「手册」组头，也没有那三页；三组八页顺序正确。
- 八个页面的页名那一行最右端都有「帮助中心」，点它到「使用说明」，地址栏变成 `/guide`；**不整页刷新**：点之前在页面
  的 JS 环境里放一个标记变量，跳过去之后它还在，就说明是站内跳转、同一个会话。
- 手册三页顶部导航可互相跳转，当前页是灰的；直接在地址栏打开三页的地址不弹「Page not found」。
- 1280 与 1024 两种宽度（侧边栏展开）下，八个页面的「帮助中心」都与页名在同一行、不折行；页名与状态标签不折行；
  记账页页头不再出现星号。
