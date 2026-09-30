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

- `ui.page_head(page, *, help_center=True)`：页头从一个 `st.html` 改为一个
  `st.container(horizontal=True, vertical_alignment="center", gap="small")`，里面依次是
  - 原来的页头 HTML（`theme.page_head(...)`，页名 + 一句话说明 + 任务状态标签，不改）；
  - `st.page_link(page_ref("guide"), label=guide.HELP_CENTER_LABEL, icon=":material/help:")`。
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
- 页头 HTML（`theme.page_head`）本身不改。

## 5. 旧说法同步

- `app/dashboard.py`：顶部 docstring 与 `PAGES` 上方的顺序说明里关于「手册」组的几句；`GUIDE_PAGE_OBJS` 的注释。
- `app/pages_guide.py`：模块 docstring 与 `_render`、`page_guide` 的 docstring。
- `README.md`：第 177 行附近「侧边栏十一个页面、分四组……「手册」组是……」改成三组八页，并说明手册从各页右上角的
  「帮助中心」进；第 366 行附近「「手册」组第一页「使用说明」……」改成现行进法；「就地帮助」一节开头那句
  「散在手册之外的各功能页里」保持。
- 测试里描述手册组位置的注释（`tests/test_dashboard_nav.py`、`tests/test_dashboard_guide.py`）。
- 项目记忆 `quant-panel-ux-decisions.md` 记一笔：手册不占侧边栏，从页头「帮助中心」进（2026-09-30 用户定）。

## 6. 测试

- `tests/test_dashboard_nav.py`：分组与页序照旧，另加断言——「手册」组三页 `visibility == "hidden"`，其余八页 `"visible"`。
  （`tests/conftest.py` 的 `FakePage` 已有 `visibility` 参数。）
- 八个非手册页：页面上恰有一个标签为「帮助中心」的 `page_link`，目标是「使用说明」页；它与页头 HTML 在同一个水平容器里。
- 手册三页：没有「帮助中心」链接；恰有三个手册导航链接，按 `GUIDE_PAGES` 顺序，当前页那个 `disabled`、另两个不 disabled；
  「使用说明」页不再出现 `GUIDE_MORE_TITLE`。
- 既有的「说明页没有按钮」「手册每页不放控制条」等测试照常通过。

## 7. 验收（真浏览器）

- 侧边栏没有「手册」组头，也没有那三页；三组八页顺序正确。
- 八个页面的页名那一行最右端都有「帮助中心」，点它到「使用说明」，地址栏变成 `/guide`；**不整页刷新**：点之前在页面
  的 JS 环境里放一个标记变量，跳过去之后它还在，就说明是站内跳转、同一个会话。
- 手册三页顶部导航可互相跳转，当前页是灰的；直接在地址栏打开三页的地址不弹「Page not found」。
- 1280 与 1024 两种宽度下，「帮助中心」不换行、不挤掉任务状态标签。
