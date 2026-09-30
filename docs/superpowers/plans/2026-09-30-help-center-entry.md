# 帮助中心入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 侧边栏去掉「手册」组（三页改为不在菜单里显示、地址照常可用）；除手册三页外，每页页名那一行最右端放一个「帮助中心」链接到「使用说明」；手册三页顶部加页内导航。

**Architecture:** `ui.page_head` 一处改动覆盖八个页面：页头改为 4:1 两栏，左栏是原页头 HTML，右栏是靠右的 `st.page_link`。`theme.CSS` 给页名与状态标签加不收缩、给说明加可折行，使窄屏只折说明。`dashboard.py` 给手册三页 `visibility="hidden"`。`pages_guide._render` 在页头下放三页的 `page_link` 行，删掉页尾目录。

**Tech Stack:** Python 3.14、Streamlit 1.61.1（`st.Page(visibility=)`、`st.columns`、`st.container(horizontal=True, horizontal_alignment="right")`、`st.page_link`；AppTest）、pytest。

**Spec:** `docs/superpowers/specs/2026-09-30-help-center-entry-design.md`（评审修订后定稿）。

**分支：** `feat/help-center`（已检出，别切）。提交身份 `git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit`，提交说明末尾空一行加
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。所有命令在仓库根目录跑，Python 一律 `.venv/bin/python`。**不许** `git checkout --` / `git stash`；
变异自查用 `cp` 备份、`mv` 还原。

---

## 文件结构

| 文件 | 改什么 |
|---|---|
| `app/theme.py` | `.qd-title` / `.qd-sub` / `.qd-head-right` 三条 CSS 各加一两行 |
| `app/guide.py` | 新增 `HELP_CENTER_LABEL`；删 `GUIDE_MORE_TITLE` |
| `app/ui.py` | `page_head` 加 `help_center` 参数与两栏排版；`PAGE_INTRO["记账"]` 去星号 |
| `app/dashboard.py` | 手册三页 `visibility="hidden"`；几段注释 |
| `app/pages_guide.py` | `_render` 页头传 `help_center=False`、加手册导航行；删 `_contents` |
| `tests/test_theme.py`、`tests/test_dashboard_visual.py`、`tests/test_dashboard_nav.py`、`tests/test_dashboard_guide.py` | 见各任务 |
| `README.md` | 第 18、177、366 行附近 |

任务顺序：Task 1 样式 → Task 2 页头入口 → Task 3 侧边栏隐藏 → Task 4 手册导航 → Task 5 文档 → Task 6 真浏览器验收（主会话做）。

---

### Task 1: 页头 CSS——页名与状态标签不收缩，说明可折行

**Files:** Modify `app/theme.py`（`.qd-title`、`.qd-sub`、`.qd-head-right` 三条规则）；Test `tests/test_theme.py`（末尾追加）

- [ ] **Step 1: 写失败的测试**

```python
# ================================================================ 页头右端的「帮助中心」（2026-09-30 设计）

def test_page_head_keeps_title_and_pill_on_one_line_and_lets_the_lead_wrap():
    """页头左栏变窄时（1024 宽、侧边栏展开）只让那句说明折行：不加这几条，页名会被折成「信 / 号」。"""
    heads = [h for h, _ in _css_rule_pairs()]
    for sel in (".qd-title", ".qd-sub", ".qd-head-right"):
        assert heads.count(sel) == 1, sel
    rules = _css_rules()
    assert "white-space: nowrap" in rules[".qd-title"] and "flex-shrink: 0" in rules[".qd-title"]
    assert "white-space: nowrap" in rules[".qd-head-right"] and "flex-shrink: 0" in rules[".qd-head-right"]
    assert "min-width: 0" in rules[".qd-sub"]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_theme.py -q -k lead_wrap` → FAIL（`white-space: nowrap` 不在 `.qd-title` 里）

- [ ] **Step 3: 实现**——三条规则改成：

```css
.qd-title {{
  font-family: var(--qd-serif);
  font-size: 1.45rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--qd-text);
  line-height: 1.2;
  white-space: nowrap;
  flex-shrink: 0;
}}
.qd-sub {{
  font-family: var(--qd-sans);
  font-size: 0.8rem;
  color: var(--qd-muted);
  min-width: 0;
}}
.qd-head-right {{
  margin-left: auto;
  flex-shrink: 0;
  white-space: nowrap;
}}
```

并在 `.qd-head {{` 规则上方的 CSS 注释里（没有就新加一行 `/* … */`）写一句：
`/* 页名与状态标签不收缩、说明可折行：页头与右端「帮助中心」同一行（2026-09-30），左栏变窄时只折说明。 */`
（注释里不许出现花括号——选择器纪律测试会把 `{` 当规则头解析。）

- [ ] **Step 4: 跑样式测试** → `.venv/bin/python -m pytest tests/test_theme.py -q` 全 PASS

- [ ] **Step 5: 提交**

```bash
git add app/theme.py tests/test_theme.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "style(panel): 页头页名与状态标签不收缩、说明可折行（为页头右端的帮助中心让位）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 页头右端的「帮助中心」；记账页头去星号

**Files:** Modify `app/guide.py`、`app/ui.py`；Test `tests/test_dashboard_visual.py`（末尾追加）

- [ ] **Step 1: 写失败的测试**（`tests/test_dashboard_visual.py` 末尾；文件里已有 `_page`、`_htmls`、`_descend`、`_bodies_under`）

```python
# ================================================================ 页头右端的「帮助中心」（2026-09-30 设计）
HELP_CENTER = "帮助中心"
NON_MANUAL_PAGES = ["市场概览", "任务控制台", "信号", "记账", "持仓与盈亏", "信号池", "回测报告", "个股K线"]


@pytest.mark.parametrize("page", NON_MANUAL_PAGES)
def test_every_page_has_a_help_center_link_on_its_title_row(tmp_path, page):
    """手册不在侧边栏里了（它不是系统的功能），入口放在每页页名那一行的最右端。
    同一行的证据：页头 HTML 在 4/5 宽的那一栏，链接在 1/5 宽的那一栏。"""
    at = _page(tmp_path, page)
    assert not at.exception, at.exception
    links = [el for el in at.get("page_link") if el.proto.label == HELP_CENTER]
    assert len(links) == 1, [el.proto.label for el in at.get("page_link")]
    assert links[0].proto.page == "guide", links[0].proto
    cols = at.get("column")
    head_cols = [c for c in cols if any('class="qd-head"' in b for b in _bodies_under(c))]
    link_cols = [c for c in cols if any(getattr(e, "proto", None) is links[0].proto for e in _descend(c))]
    assert len(head_cols) == 1 and len(link_cols) == 1, (len(head_cols), len(link_cols))
    assert abs(head_cols[0].proto.weight - 0.8) < 1e-6, head_cols[0].proto.weight
    assert abs(link_cols[0].proto.weight - 0.2) < 1e-6, link_cols[0].proto.weight


@pytest.mark.parametrize("page", NON_MANUAL_PAGES)
def test_page_head_lead_is_plain_text(tmp_path, page):
    """页头 HTML 会转义文字：说明里写 ** 会原样显示两对星号（记账页 2026-09-30 真机确认过）。"""
    at = _page(tmp_path, page)
    head = [h for h in _htmls(at) if 'class="qd-head"' in h][0]
    assert "**" not in head, head
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_dashboard_visual.py -q -k "help_center or plain_text"`
Expected: 8 条 `help_center` FAIL（没有「帮助中心」链接）；`plain_text[记账]` FAIL，其余 7 条 PASS。

- [ ] **Step 3: 实现**

3a. `app/guide.py`：在 `GUIDE_MORE_TITLE = "更多说明"` 上方加

```python
#: 各页页名那一行最右端的手册入口（2026-09-30：手册不占侧边栏，从这里进）。
HELP_CENTER_LABEL = "帮助中心"
```

3b. `app/ui.py`：`PAGE_INTRO["记账"]` 改为 `"记你真实成交的每一笔；历史记录可筛选、可修改、可删除、可导出。"`，
并在 `PAGE_INTRO = {` 上方注释末尾加一行：`# 页头是纯文字（theme.page_head 会转义）：不许写 ** 之类的 Markdown，会原样显示。`

3c. `app/ui.py`：`page_head` 整个换成

```python
def page_head(page: str, *, help_center: bool = True) -> None:
    """通用页头（§2.4）：页名衬线大字 + 一句话说明 + 右侧全局任务状态 pill。

    `help_center=True`（默认）时一行两栏：左栏是页头，右栏靠右一个「帮助中心」链接到「使用说明」
    （2026-09-30：手册不是系统的功能，不占侧边栏，从这里进）。用分栏不用水平容器：
    st.container(horizontal=True) 在 1.61.1 强制允许换行，1024 宽、侧边栏展开时会把链接挤到下一行
    （评审实测）；分栏不换行，左栏变窄时靠 theme.CSS 只让说明折行。用 st.page_link 不用页头 HTML 里的
    <a>：后者整页刷新、开新会话，记账页填到一半的内容就丢了。手册三页自己传 False（它们有页内导航）。

    这里**不**报 st.error：状态文件损坏时下面的控制条/卡片会报，页头再喊一遍只是噪声；
    但 pill 必须如实说"未知"，不能退回"空闲"（那是猜的，而且正是 fail-safe 要挡的误导）。
    """
    try:
        text, kind = view.busy_pill(process.any_running(RUNS_DIR))
    except RuntimeError:
        text, kind = view.UNKNOWN_TEXT, view.PILL_IDLE
    head = theme.page_head(page, PAGE_INTRO[page], theme.pill(text, kind))
    if not help_center:
        st.html(head)
        return
    left, right = st.columns([4, 1], vertical_alignment="center", gap="small")
    left.html(head)
    with right.container(horizontal=True, horizontal_alignment="right"):
        st.page_link(page_ref("guide"), label=guide.HELP_CENTER_LABEL, icon=":material/help:")
```

- [ ] **Step 4: 跑页头相关测试与全量**

Run: `.venv/bin/python -m pytest tests/test_dashboard_visual.py tests/test_dashboard_console.py tests/test_dashboard_home.py -q` → PASS
Run: `.venv/bin/python -m pytest -q` → PASS，**除了**手册页相关的既有断言若因多出一个「帮助中心」链接而失败（`tests/test_dashboard_guide.py::test_the_landing_page_links_to_the_other_pages`、`test_the_toc_uses_page_links_not_buttons` 预期会红，Task 4 改写它们）。把实际失败清单写进报告；除这两条外不许有别的失败。

- [ ] **Step 5: 提交**

```bash
git add app/guide.py app/ui.py tests/test_dashboard_visual.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(panel): 各页页名那一行右端放「帮助中心」链接到使用说明；记账页头去掉原样显示的星号

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 侧边栏不再显示手册三页

**Files:** Modify `app/dashboard.py`（`GUIDE_PAGE_OBJS` 与注释）；Test `tests/test_dashboard_nav.py`

- [ ] **Step 1: 写失败的测试**（`tests/test_dashboard_nav.py`，放在 `test_navigation_groups_match_the_design` 之后）

```python
def test_the_manual_is_hidden_from_the_sidebar_but_still_registered(tmp_path, monkeypatch):
    """2026-09-30 用户定：手册不是系统的功能，不占侧边栏；从各页页头右端的「帮助中心」进。
    三页仍在 st.navigation 里注册（「手册」组整组隐藏时侧边栏连组头都不出现，实测），
    所以 /guide 等地址、st.page_link、st.switch_page 照常可用。"""
    _, pages = _declared_pages(tmp_path, monkeypatch)
    assert [p.title for p in pages.groups["手册"]] == ["使用说明", "读懂回测", "边界与安全"]
    hidden = [p.title for p in pages if p.visibility == "hidden"]
    assert hidden == ["使用说明", "读懂回测", "边界与安全"], hidden
```

- [ ] **Step 2: 跑，确认失败** → `.venv/bin/python -m pytest tests/test_dashboard_nav.py -q -k hidden` → FAIL（`hidden == []`）

- [ ] **Step 3: 实现**

3a. `GUIDE_PAGE_OBJS` 的 `st.Page(...)` 加 `visibility="hidden"`：

```python
GUIDE_PAGE_OBJS = {
    p.key: st.Page(pages_guide.RENDERERS[p.key], title=p.title, icon=p.icon,
                   url_path=p.url_path, visibility="hidden")
    for p in guide.GUIDE_PAGES
}
```

3b. 注释同步：
- `GUIDE_PAGE_OBJS` 上方「# 说明手册三页（v0.5.0 拆页；2026-09-05 起整组进「手册」，不再有一页当落地页）。」改为
  「# 说明手册三页（v0.5.0 拆页；2026-09-30 起整组隐藏：不在侧边栏里，从各页页头右端的「帮助中心」进）。」
- `PAGES` 上方顺序说明里「#   -「手册」组紧跟主组：使用说明 / 读懂回测 / 边界与安全，三页说明放一起才读得通；」改为
  「#   -「手册」组整组隐藏（2026-09-30 用户定：手册不是系统的功能，不该占侧边栏）：三页仍在这里注册，
   地址与 st.page_link 照常可用；一个组的页全部隐藏时侧边栏连组头都不出现（实测 1.61.1）；」
- `ui.bind_pages` 上方「# 登记页对象，供 st.page_link 使用（说明落地页的目录、控制台第二步的两个跳转）。」改为
  「# 登记页对象，供 st.page_link 使用（各页页头的「帮助中心」、手册的页内导航、控制台第二步的两个跳转）。」
- `tests/test_dashboard_nav.py` 的 `EXPECTED_GROUPS` 上方注释末尾加一行：
  「# 2026-09-30（用户定）：「手册」组三页全部 visibility="hidden"，侧边栏不显示，从页头「帮助中心」进。」

- [ ] **Step 4: 跑** → `.venv/bin/python -m pytest tests/test_dashboard_nav.py tests/test_dashboard_guide.py -q`；
  除 Task 2 报告里那两条预期失败外全 PASS（`goto_page` 按 url_path 的哈希切页，隐藏页照样能切）。

- [ ] **Step 5: 提交**

```bash
git add app/dashboard.py tests/test_dashboard_nav.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(panel): 侧边栏不再显示手册三页（整组隐藏，地址与链接照常可用）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 手册的页内导航；删页尾目录

**Files:** Modify `app/pages_guide.py`、`app/guide.py`（删 `GUIDE_MORE_TITLE`）；Test `tests/test_dashboard_guide.py`

- [ ] **Step 1: 改测试**

1a. `test_every_section_of_the_guide_gets_a_serif_heading` 里两行
```python
        # 落地页尾多一个「更多说明」的目录小标题，它不是正文节
        seen += [h for h in titles if guide.GUIDE_MORE_TITLE not in h]
```
换成 `        seen += titles`

1b. 删掉 `test_the_landing_page_links_to_the_other_pages` 与 `test_the_toc_uses_page_links_not_buttons` 两条，原位置换成：

```python
@pytest.mark.parametrize("key", [p.key for p in guide.GUIDE_PAGES])
def test_each_manual_page_has_the_manual_nav_and_no_help_center(tmp_path, key):
    """手册不在侧边栏里之后，三页之间靠页头下方这一行互相跳转（2026-09-30）：
    按 GUIDE_PAGES 顺序三个 page_link，当前页灰掉（disabled），每个带一句「这页讲什么」。
    用 page_link 不用按钮：在这个面板里按钮意味着"会起进程"，说明页一个都不许有。
    手册页自己不放「帮助中心」——它就在帮助中心里，页内导航的第一项就是那一页。"""
    page = guide.guide_page(key)
    at = _page(tmp_path, page.title)
    assert not at.exception, at.exception
    links = [el.proto for el in at.get("page_link")]
    titles = [p.title for p in guide.GUIDE_PAGES]
    assert "帮助中心" not in [pb.label for pb in links], links
    nav = [pb for pb in links if pb.label in titles]
    assert [pb.label for pb in nav] == titles, links
    assert [pb.disabled for pb in nav] == [p.key == key for p in guide.GUIDE_PAGES], nav
    assert all(pb.help for pb in nav), "每个链接都要有一句这页讲什么"
    assert not [h for h in _htmls(at) if "更多说明" in h], "页尾目录已换成页头下的导航行"
```

1c. 文件头注释第 6 行「#   - 说明页在「手册」组第一位（2026-09-05 起落地页是任务控制台，见 test_dashboard_nav.py）；」改为
「#   - 手册三页不在侧边栏里（2026-09-30），靠页头下方的页内导航互相跳转，其余各页从页头「帮助中心」进；」；
`PAGES = [...]` 上方注释末尾加一行「# 2026-09-30：手册组整组隐藏，不在侧边栏里（见 test_dashboard_nav.py）。」

- [ ] **Step 2: 跑，确认失败** → `.venv/bin/python -m pytest tests/test_dashboard_guide.py -q -k manual_nav`
  → 3 条 FAIL（页头下没有导航行；「使用说明」页还有「帮助中心」与「更多说明」）

- [ ] **Step 3: 实现**

3a. `app/pages_guide.py`：`_render` 换成

```python
def _render(page_key: str) -> None:
    """渲染手册的一页：页头 + 手册导航行 + 属于这一页的那几节。

    手册三页不在侧边栏里（2026-09-30 用户定：手册不是系统的功能），所以页头下方放一行导航：
    三页各一个 page_link，当前页灰掉。其余各页从页头右端的「帮助中心」进来；手册页自己不放
    「帮助中心」（help_center=False）——导航行的第一项就是那一页。
    用 page_link 不用 st.button：说明页不许出现按钮（在这个面板里按钮意味着"会起进程"）。

    循环体与拆分前逐行相同——安全提示仍单独走 st.warning（§3.1 第 7 条），
    闭环图仍跟在有 flow 的那一节标题下面。
    """
    page = guide.guide_page(page_key)
    ui.page_head(page.title, help_center=False)
    with st.container(horizontal=True, gap="small"):
        for p in guide.GUIDE_PAGES:
            st.page_link(ui.page_ref(p.key), label=p.title, icon=p.icon, help=p.lead,
                         disabled=(p.key == page_key))
    for sec in guide.page_sections(page_key):
        st.html(theme.section(sec.title))
        if sec.flow:
            st.html(theme.flow(sec.flow))          # 闭环图：扫描 → 信号池 → 信号跟踪
        if sec.emphasis:
            st.warning(sec.body)                   # 安全提示单独成块
        else:
            st.markdown(sec.body)
```

3b. 删掉 `_contents` 整个函数；`page_guide` 换成

```python
def page_guide() -> None:
    """手册第一页，也是各页页头「帮助中心」的落点。"""
    _render("guide")
```

3c. 模块 docstring 末尾加一段：
「2026-09-30 起三页不在侧边栏里（手册不是系统的功能）：各页页头右端的「帮助中心」进第一页，
三页之间靠页头下方的导航行互相跳转，原来第一页页尾的目录随之删掉（同一件事说两遍）。」

3d. `app/guide.py`：删掉 `GUIDE_MORE_TITLE = "更多说明"` 那一行（及其上方只说它的注释，如有）。
删前 `grep -rn "GUIDE_MORE_TITLE" app/ tests/` 确认除了 1a 已改掉的那处再无引用。

- [ ] **Step 4: 跑手册测试与全量**

Run: `.venv/bin/python -m pytest tests/test_dashboard_guide.py -q` → PASS（含既有的「说明页没有按钮」「手册每页不放控制条」）
Run: `.venv/bin/python -m pytest -q` → 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/pages_guide.py app/guide.py tests/test_dashboard_guide.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(guide): 手册三页页头下加页内导航（当前页灰掉），删掉第一页页尾的目录

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: README 同步与变异自查

**Files:** Modify `README.md`

- [ ] **Step 1: README 三处**

- 第 18 行：「…都在那儿；从未跑过任务时它会给一条去「使用说明」的链接（三个任务怎么配合、结果怎么读），不用回来翻这份 README。」
  改为「…都在那儿；各页页名那一行最右端的「帮助中心」进面板自带的手册（三个任务怎么配合、结果怎么读），从未跑过任务时首页还会给一条去「使用说明」的链接，不用回来翻这份 README。」
- 第 177 行：「侧边栏十一个页面、分四组」改为「侧边栏八个页面、分三组」；删掉「「手册」组是 **使用说明**（v0.2.1 新增，面板自带的手册，见 §4；」起到「**边界与安全**；」止的那一段（用 `grep -o` 先取出原文确认边界），
  并在该行末尾补一句：「手册三页（**使用说明**、**读懂回测**、**边界与安全**，见 §4）不在侧边栏里：各页页名那一行最右端的「帮助中心」进第一页，三页之间靠页头下方的导航互相跳转（2026-09-30 用户定：手册不是系统的功能）。」
- 第 366 行整段换成：
  「手册（使用说明 / 读懂回测 / 边界与安全）是面板自带的说明——不用回来翻 README 就能把三个任务和它们的输出读懂。它曾是默认落地页（v0.2.1～v0.5.0），2026-09-05～09-23 落地页是「任务控制台」，2026-09-23 起是「市场概览」；从未跑过任务时首页给一条去「使用说明」的链接。2026-09-30 起手册不在侧边栏里：从各页页名那一行最右端的「帮助中心」进，三页之间靠页头下方的导航互相跳转；`/guide`、`/guide-metrics`、`/guide-limits` 这几个地址照常能直接打开。」

- [ ] **Step 2: 全量**：`.venv/bin/python -m pytest -q` → 全部 PASS（`tests/test_guide.py` / `test_packaging.py` 里有对账 README 的测试）

- [ ] **Step 3: 变异自查**（每项 `cp` 备份 → 改 → `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest <tests> -q -x` 应 FAIL → `mv` 还原）

| # | 改动 | 应红的测试 |
|---|---|---|
| 1 | `page_head` 的 `st.columns([4, 1], …)` 改成 `[3, 1]` | `tests/test_dashboard_visual.py` |
| 2 | `page_head` 默认值改成 `help_center=False` | `tests/test_dashboard_visual.py` |
| 3 | `_render` 里 `help_center=False` 删掉 | `tests/test_dashboard_guide.py` |
| 4 | `disabled=(p.key == page_key)` 改成 `disabled=False` | `tests/test_dashboard_guide.py` |
| 5 | `GUIDE_PAGE_OBJS` 去掉 `visibility="hidden"` | `tests/test_dashboard_nav.py` |
| 6 | `.qd-title` 删掉 `flex-shrink: 0;` | `tests/test_theme.py` |
| 7 | `PAGE_INTRO["记账"]` 加回 `**` | `tests/test_dashboard_visual.py` |

没红的补一条最小测试并在报告里说明。

- [ ] **Step 4: 提交**

```bash
git add README.md
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "docs(panel): README 改成三组八页、手册从页头「帮助中心」进

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 真浏览器验收（主会话做）

先重启 `tape` 预览服务。1280 与 1024 两种宽度（侧边栏展开）：
- [ ] 侧边栏没有「手册」组头与三页，三组八页顺序正确。
- [ ] 八个页面：「帮助中心」与页名同一行、在最右端、不折行；页名与状态标签不折行；记账页头没有星号。
- [ ] 点「帮助中心」到「使用说明」，地址变 `/guide`；点之前在页面 JS 里放一个标记变量，跳过去之后还在（站内跳转、同一会话）。
- [ ] 手册三页：页头下导航可互相跳转，当前页灰；直接打开 `/guide`、`/guide-metrics`、`/guide-limits` 不弹「Page not found」。
- [ ] 结果写进设计文档末尾「§8 验收记录」并提交；项目记忆（`quant-panel-ux-decisions.md`、`quant-panel-copy-for-users.md`）同步。
