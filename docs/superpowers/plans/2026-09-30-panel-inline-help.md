# 面板就地帮助改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 去掉首页与任务控制台上所有带下拉箭头的「? ⌄」弹出框，改成紧贴被解释对象的小圆圈「?」（悬停或 Tab 键选中出提示框）；首页按钮自带说明；六张指数卡片与「52 周新高 / 新低」卡片的小图对齐。

**Architecture:** 样式层 `app/theme.py` 新增纯函数 `help_dot()`（先整体转义、再只认加粗 / 空行分段 / 带续行的列表三种写法）与对应 CSS；`stat_card()` / `section()` 各加 `help` 参数把圆点放进标签或标题里；页面只从 `guide` 取文字传进去。控制台卡片标题、首页卡片标签用这个圆点，首页按钮用 Streamlit 原生 `help=`。

**Tech Stack:** Python 3.14、Streamlit 1.61.1（`st.html` 经 DOMPurify 清洗、不套 iframe；AppTest 离线测试）、pytest。

**Spec:** `docs/superpowers/specs/2026-09-29-panel-inline-help-design.md`（三轮评审定稿）。**实现每一步前先读它对应的小节。**

**分支：** `feat/v0.6-market-overview`（已检出，别切）。提交身份 `CH3NQ1 <khalilstar@qq.com>`，用
`git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit`。所有命令在仓库根目录跑，Python 一律 `.venv/bin/python`。

---

## 文件结构

| 文件 | 改什么 | 职责 |
|---|---|---|
| `app/theme.py` | 新增 `help_dot` + 私有 `_tip_markup` / `_tip_inline`；`stat_card` 加 `help`、`help_align`，`sub` 接受元组；`section` 加 `help`、`help_align`、`help_wide`；CSS 加 `--qd-surface` 与 `.qd-help-*` 规则，删死规则 `.qd-help` | 样式层：只拼 HTML、不 import `guide` |
| `app/guide.py` | `HOME_HELP["above_ma"]` 改 `{n}` 模板；新增 `HOME_HL_TOTAL`；改写 `JOB_HELP["market_breadth"]`；三处注释 | 文案层 |
| `app/pages_home.py` | 温度计与趋势卡片挂圆点、删两个弹出框；按钮区删列与弹出框、按钮带 `help=`；指数卡片副文分两行；52 周卡片补副文 | 首页排版 |
| `app/pages_console.py` | `_card_head` 去列与弹出框、改用 `section(help=…)`；`_RIGHT_COLUMN` 常量；`_render_card` 两处传方向；两段 docstring | 控制台排版 |
| `tests/test_theme.py` | 圆点、转换器、卡片、标题、CSS 的单测 | |
| `tests/test_guide.py` | 改一条测试名与 docstring；加「每条帮助都能干净渲染」 | |
| `tests/test_dashboard_home.py` | `_card` 助手改前缀匹配；新测试 7 条；改 1 条 | |
| `tests/test_dashboard_guide.py` | 按路径加载 theme；换掉 4 条弹出框测试；删两个只为弹出框服务的助手 | |
| `tests/test_dashboard_console.py` | `test_three_cards_render` 标题断言锚定开头 | |
| `README.md`、首页设计文档 | 旧说法同步 | |

任务顺序：Task 1–2 样式层（不碰页面，页面行为不变）→ Task 3 文案 → Task 4 首页 → Task 5 控制台 → Task 6 文档与收尾 → Task 7 真浏览器验收（由主会话做）。

---

### Task 1: 帮助圆点 `help_dot` 与它的 CSS

**Files:**
- Modify: `app/theme.py`（CSS 的 `:root`、删 `.qd-help` 规则、文件末尾追加 CSS；在 `stat_card` 之前加函数）
- Test: `tests/test_theme.py`（文件末尾追加）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_theme.py` 末尾追加（文件顶部已有 `import re` 与按路径加载的 `theme`）：

```python
# ================================================================ 就地帮助圆点（2026-09-29 设计）

def _css_rules() -> dict[str, str]:
    """{选择器头: 规则体}。CSS 约定每条规则的选择器单独一行，所以按行首解析。"""
    return {m.group(1).strip(): m.group(2)
            for m in re.finditer(r"^\s*([^{}@\n][^{}\n]*?)\s*\{([^}]*)\}", theme.CSS, re.M)}


def test_help_dot_structure_and_modifiers():
    plain = theme.help_dot("一句话。")
    assert plain == ('<span class="qd-help-dot" tabindex="0">?'
                     '<span class="qd-help-tip"><p>一句话。</p></span></span>')
    assert 'class="qd-help-dot qd-help-end"' in theme.help_dot("x", align="end")
    assert 'class="qd-help-dot qd-help-wide"' in theme.help_dot("x", wide=True)
    assert 'class="qd-help-dot qd-help-end qd-help-wide"' in theme.help_dot("x", align="end", wide=True)
    assert "qd-help-end" not in theme.help_dot("x", align="middle"), "未知方向按 start"
    assert "<svg" not in plain


def test_help_dot_escapes_before_it_formats():
    html = theme.help_dot('" onmouseover=alert(1) x="<script>x</script>**粗**')
    assert "<script" not in html
    assert 'onmouseover=alert(1) x="' not in html, "引号没转义就能跳出属性"
    assert "&quot; onmouseover=alert(1)" in html
    assert "<b>粗</b>" in html, "转义之后才认加粗"


def test_help_dot_markdown_subset():
    text = ("**信号跟踪**——盯着信号池，这是唯一会提示你**卖出**的任务。\n\n"
            "- **做什么**：只看信号池里的标的，\n  与前一交易日比对。\n"
            "- **多久**：通常十几秒。\n")
    html = theme.help_dot(text)
    assert "<p><b>信号跟踪</b>——盯着信号池，这是唯一会提示你<b>卖出</b>的任务。</p>" in html, html
    assert ("<ul><li><b>做什么</b>：只看信号池里的标的， 与前一交易日比对。</li>"
            "<li><b>多久</b>：通常十几秒。</li></ul>") in html, html
    assert "**" not in html and "\n" not in html


def test_help_dot_joins_single_newlines_inside_a_paragraph():
    assert "<p>第一行 第二行</p><p>第二段</p>" in theme.help_dot("第一行\n第二行\n\n第二段")


def test_help_dot_css_rules():
    rules = _css_rules()
    dot, tip = rules[".qd-help-dot"], rules[".qd-help-tip"]
    for prop in ("position: relative", "font-family: var(--qd-sans)", "line-height: 1"):
        assert prop in dot, prop
    for prop in ("display: none", "position: absolute", "z-index:", "font-family: var(--qd-sans)",
                 "letter-spacing: normal", "white-space: normal", "background: var(--qd-surface)"):
        assert prop in tip, prop
    assert f"--qd-surface: {theme.SURFACE};" in theme.CSS
    heads = list(rules)
    assert ".qd-help-end .qd-help-tip" in heads and ".qd-help-wide .qd-help-tip" in heads
    shown = [h for h in heads if "qd-help-tip" in h and "display: block" in rules[h]]
    assert len(shown) == 1 and ":hover" in shown[0] and ":focus-visible" in shown[0], shown
    help_heads = [h for h in heads if "qd-help" in h]
    assert not [h for h in help_heads if re.search(r":focus(?!-visible)", h)], \
        "只能用 :focus-visible：鼠标点一下也会触发 :focus，提示框就钉住不走了"
    assert ".qd-help-dot .qd-help-tip ul" in heads, "列表缩进要两级类名，压过 st.html 外层的 ul 内边距"
    assert ".qd-help {" not in theme.CSS, "旧的 .qd-help 规则没人用，删掉免得新类误继承"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_theme.py -q -k help_dot`
Expected: FAIL（`AttributeError: module 'qd_theme' has no attribute 'help_dot'`，以及 CSS 断言 KeyError）

- [ ] **Step 3: 实现**

3a. `app/theme.py` 文件顶部 import 区加 `import re`（放在 `import html as _html` 之后）。

3b. CSS 的 `:root` 块里，在 `--qd-hairline: {HAIRLINE};` 之后加一行：

```css
  --qd-surface: {SURFACE};
```

3c. 删掉整条死规则（现在第 221–225 行附近，全仓无人使用）：

```css
.qd-help {{
  font-family: var(--qd-sans);
  font-size: 0.76rem;
  color: var(--qd-muted);
}}
```

3d. 在 CSS 字符串末尾、`.qd-status {{ … }}` 规则之后、结尾的 `"""` 之前追加（注意 f-string 里花括号写两遍）：

```css

/* 就地帮助圆点（2026-09-29）：紧跟在卡片标签或卡片标题后面的小圆圈「?」，悬停或 Tab 键选中
   出提示框。取代带下拉箭头的 st.popover（用户看了以为是选择框）。圆点与提示框都自己定死字体：
   它们挂在 .qd-card-label（0.06em 字距 + nowrap）与 .qd-section（衬线）里，不定死两处长得不一样。
   只用 :focus-visible 不用 :focus——鼠标点一下也会触发 :focus，提示框就钉住不走了。 */
.qd-help-dot {{
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 0.95rem;
  height: 0.95rem;
  margin-left: 0.3rem;
  border: 1px solid var(--qd-muted);
  border-radius: 50%;
  font-family: var(--qd-sans);
  font-size: 0.62rem;
  font-weight: 400;
  letter-spacing: normal;
  line-height: 1;
  color: var(--qd-muted);
  vertical-align: middle;
  cursor: help;
}}
.qd-help-dot:hover, .qd-help-dot:focus-visible {{
  color: var(--qd-text);
  border-color: var(--qd-text);
  outline: none;
}}
.qd-help-tip {{
  display: none;
  position: absolute;
  top: 1.35rem;
  left: -0.4rem;
  z-index: 20;
  box-sizing: border-box;
  width: 15rem;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--qd-hairline);
  border-radius: 6px;
  background: var(--qd-surface);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
  color: var(--qd-text);
  font-family: var(--qd-sans);
  font-size: 0.8rem;
  font-weight: 400;
  letter-spacing: normal;
  line-height: 1.5;
  text-align: left;
  text-transform: none;
  white-space: normal;
  cursor: auto;
}}
.qd-help-wide .qd-help-tip {{
  width: 22rem;
}}
.qd-help-end .qd-help-tip {{
  left: auto;
  right: -0.4rem;
}}
.qd-help-dot:hover .qd-help-tip, .qd-help-dot:focus-visible .qd-help-tip {{
  display: block;
}}
.qd-help-dot .qd-help-tip p {{
  margin: 0 0 0.4rem;
}}
.qd-help-dot .qd-help-tip ul {{
  margin: 0 0 0.4rem;
  padding-left: 1.1rem;
}}
.qd-help-dot .qd-help-tip li {{
  margin: 0 0 0.2rem;
}}
.qd-help-dot .qd-help-tip > :last-child {{
  margin-bottom: 0;
}}
```

3e. 在 `def _img(` 之前加：

```python
# ---------------------------------------------------------------- 就地帮助圆点（2026-09-29）
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _tip_inline(text: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", text)


def _tip_markup(text: str) -> str:
    """帮助文字 → 提示框 HTML。**先整体转义**，再只认三种写法（spec §1.2）：
    `**加粗**`；空行分段；行首 `- ` 的列表项（紧随其后、以空格缩进的行是它的续行）。
    段内单个换行当空格。不做通用 Markdown：现有帮助文案只用了这三种，要新写法时再扩。"""
    blocks: list[str] = []
    para: list[str] = []
    items: list[list[str]] = []

    def flush_para() -> None:
        if para:
            blocks.append(f"<p>{_tip_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list() -> None:
        if items:
            lis = "".join(f"<li>{_tip_inline(' '.join(i))}</li>" for i in items)
            blocks.append(f"<ul>{lis}</ul>")
            items.clear()

    for raw in _esc(text).split("\n"):
        line = raw.strip()
        if not line:
            flush_para()
            flush_list()
        elif raw.startswith("- "):
            flush_para()
            items.append([line[2:].strip()])
        elif raw[:1].isspace() and items:
            items[-1].append(line)
        else:
            flush_list()
            para.append(line)
    flush_para()
    flush_list()
    return "".join(blocks)


def help_dot(text: str, *, align: str = "start", wide: bool = False) -> str:
    """紧跟在标签或标题后面的小圆圈「?」，悬停或 Tab 键选中出提示框（spec §1）。

    `align="end"`：提示框从圆点右侧向左展开——挂点在页面右半边时用，免得越出视口右缘。
    `wide=True`：宽档，给任务说明这种多段带列表的长文字。
    文字由调用方从 guide 取来传入：本模块**不 import guide**（测试按文件路径单独加载本模块，
    那时 app/ 不在 sys.path 上）。圆点里的「?」是符号，不是文案。
    """
    cls = "qd-help-dot"
    if align == "end":
        cls += " qd-help-end"
    if wide:
        cls += " qd-help-wide"
    return (f'<span class="{cls}" tabindex="0">?'
            f'<span class="qd-help-tip">{_tip_markup(text)}</span></span>')
```

- [ ] **Step 4: 跑，确认通过；再跑整个样式测试文件**

Run: `.venv/bin/python -m pytest tests/test_theme.py -q`
Expected: 全部 PASS（既有的「无动画」「选择器只针对 .qd-」「十六进制只来自色板」照样过：新规则用 `rgba(...)` 不是十六进制）。

- [ ] **Step 5: 提交**

```bash
git add app/theme.py tests/test_theme.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(theme): 就地帮助圆点 help_dot——先转义再认加粗/分段/带续行列表；悬停或键盘选中出提示框，删死规则 .qd-help

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `stat_card` 与 `section` 接上圆点；`sub` 接受多段

**Files:**
- Modify: `app/theme.py`（`section`、`stat_card`）
- Test: `tests/test_theme.py`（末尾追加）

- [ ] **Step 1: 写失败的测试**

```python
def test_stat_card_without_help_is_byte_identical():
    want = ('<div class="qd-card"><div class="qd-card-label">涨停</div>'
            f'<div class="qd-card-value" style="color: {theme.UP}">51</div></div>')
    assert theme.stat_card("涨停", "51", color=theme.UP) == want


def test_stat_card_help_dot_sits_inside_the_label():
    html = theme.stat_card("炸板率", "12.3%", help="炸板 = 盘中碰到过涨停、收盘没封住。", help_align="end")
    dot = theme.help_dot("炸板 = 盘中碰到过涨停、收盘没封住。", align="end")
    assert f'<div class="qd-card-label">炸板率{dot}</div>' in html, html


def test_image_only_card_ignores_help():
    html = theme.stat_card("", "", img="data:image/svg+xml;base64,QUJD", help="x")
    assert "qd-help-dot" not in html and "qd-card-label" not in html


def test_stat_card_sub_tuple_is_one_line_per_part():
    html = theme.stat_card("深证成指", "13316.97", sub=("-319.10", "-2.34%"), color=theme.DOWN)
    line = f'<div class="qd-card-sub" style="color: {theme.DOWN}">'
    assert html.count(line) == 2, html
    assert f"{line}-319.10</div>{line}-2.34%</div>" in html
    assert '<div class="qd-card-value">13316.97</div>' in html, "有副文时颜色上副文、不上数值"
    escaped = theme.stat_card("x", "1", sub=("<u>", "<i>"))
    assert "<u>" not in escaped and "<i>" not in escaped


def test_stat_card_sub_str_is_not_split_into_characters():
    html = theme.stat_card("站上 20 日线", "41.4%", sub="2070/5004")
    assert html.count('class="qd-card-sub"') == 1 and ">2070/5004<" in html


def test_section_without_help_is_byte_identical():
    p = theme.pill("空闲", "idle")
    assert theme.section("回测", p) == f'<div class="qd-section">回测<span class="qd-section-pill">{p}</span></div>'
    assert theme.section("回测") == '<div class="qd-section">回测</div>'


def test_section_help_dot_follows_the_title_before_the_pill():
    p = theme.pill("空闲", "idle")
    html = theme.section("信号跟踪", p, help="**信号跟踪**——x", help_align="end", help_wide=True)
    dot = theme.help_dot("**信号跟踪**——x", align="end", wide=True)
    assert html == f'<div class="qd-section">信号跟踪{dot}<span class="qd-section-pill">{p}</span></div>'
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_theme.py -q -k "stat_card or section"`
Expected: 新测试 FAIL（`TypeError: ... unexpected keyword argument 'help'`；元组 `sub` 被当成字符串转义）

- [ ] **Step 3: 实现**

把 `section` 换成：

```python
def section(title: str, pill_html: str = "", *, help: str = "", help_align: str = "start",
            help_wide: bool = False) -> str:
    """区块小标题：衬线 + 发丝线（§2.3 第 2 条，不用四面描边的卡片）。
    `help` 非空时，标题文字后紧跟一个帮助圆点（在 pill 之前）；空时输出与以前逐字相同。"""
    dot = help_dot(help, align=help_align, wide=help_wide) if help else ""
    right = f'<span class="qd-section-pill">{pill_html}</span>' if pill_html else ""
    return f'<div class="qd-section">{_esc(title)}{dot}{right}</div>'
```

把 `stat_card` 换成：

```python
def stat_card(label: str, value: str, *, sub: str | tuple[str, ...] = "", color: str | None = None,
              img: str | None = None, note: str = "", help: str = "", help_align: str = "start") -> str:
    """首页卡片。`color` 上在副文那一行（涨跌）；没有副文时上在数值上。白名单同 metric()。
    label 与 value 都为空 = 纯图卡（比例条、柱状图），不输出那两行，`help` 也忽略。

    `sub` 可以是一段字符串（一行，输出与以前逐字相同），也可以是元组（每段一行、同色）——
    指数卡片把涨跌额与涨跌幅分两行，六张卡片才一样高、分时小图才对齐（spec §3.1）。
    `help` 非空时标签后紧跟一个帮助圆点（在 qd-card-label 那个 div 里）。"""
    style = ""
    if color is not None:
        if color not in _INLINE_COLORS:
            raise ValueError(f"内联色值必须来自色板，收到 {color!r}")
        style = f' style="color: {color}"'
    subs = (sub,) if isinstance(sub, str) else tuple(sub)
    parts = ['<div class="qd-card">']
    if label or value:
        dot = help_dot(help, align=help_align) if (help and label) else ""
        parts.append(f'<div class="qd-card-label">{_esc(label)}{dot}</div>')
        parts.append(f'<div class="qd-card-value"{"" if any(subs) else style}>{_esc(value)}</div>')
    for s in subs:
        if s:
            parts.append(f'<div class="qd-card-sub"{style}>{_esc(s)}</div>')
    if img:
        parts.append(_img(img))
    if note:
        parts.append(f'<div class="qd-card-note">{_esc(note)}</div>')
    parts.append("</div>")
    return "".join(parts)
```

注意：`help_dot` 定义在 `_img` 之前（Task 1），`section` 在文件更前面也没关系——调用发生在运行时。

- [ ] **Step 4: 跑样式测试与首页测试（页面行为此时应完全不变）**

Run: `.venv/bin/python -m pytest tests/test_theme.py tests/test_dashboard_home.py tests/test_dashboard_console.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/theme.py tests/test_theme.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(theme): stat_card/section 接上帮助圆点；stat_card 副文可多段（每段一行同色）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 文案——`above_ma` 模板、`HOME_HL_TOTAL`、任务说明改写、注释

**Files:**
- Modify: `app/guide.py`（`HOME_HELP`、`HOME_TREND_*` 附近、`JOB_HELP["market_breadth"]`、三处注释）
- Test: `tests/test_guide.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_guide.py`：把 `test_every_job_has_a_popover_help` 整条换成：

```python
def test_every_job_has_its_help_text():
    """每个任务一段就地说明（控制台卡片标题旁的圆点、首页按钮的悬停提示）。
    JOBS 里加了新任务却忘了写说明，这条会红。"""
    assert set(guide.JOB_HELP) == set(jobs.JOBS), \
        f"JOB_HELP 与 JOBS 不一致: {set(guide.JOB_HELP) ^ set(jobs.JOBS)}"
```

并在 `test_home_help_covers_the_terms_on_the_page` 之后追加：

```python
def test_above_ma_help_is_a_template_for_each_window():
    text = guide.HOME_HELP["above_ma"]
    assert "{n}" in text and "近 {n} 日" in text
    assert "近 60 日" in text.format(n=60)


def test_high_low_total_is_a_template():
    assert guide.HOME_HL_TOTAL.format(n=4922) == "共 4922 只"


def test_breadth_job_help_names_no_button_and_has_no_stray_space():
    text = guide.JOB_HELP["market_breadth"]
    assert "需要 约" not in text, "FACTS 值自带「约」，前面不能再有空格"
    assert "更新收盘数据" not in text.splitlines()[0], "从没准备过时按钮叫「开始准备」，首句不点名按钮"
    assert "首页" not in text, "这段只在首页出现，不再写「首页」"


def test_every_help_text_renders_cleanly_in_a_help_dot():
    """帮助圆点只认加粗 / 空行分段 / 带续行列表三种写法：现有全部文案渲染后不能剩下 ** 或换行。"""
    texts = list(guide.JOB_HELP.values()) + list(guide.HOME_HELP.values())
    for t in texts:
        html = theme.help_dot(t, wide=True)
        assert "**" not in html and "\n" not in html, t[:24]
        assert html.count("<b>") == html.count("</b>"), t[:24]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_guide.py -q`
Expected: 新增的三条 FAIL（`above_ma` 不是模板、`HOME_HL_TOTAL` 不存在、任务说明有空格与「首页」）；`test_every_help_text_renders_cleanly_in_a_help_dot` 应当已经 PASS。

- [ ] **Step 3: 实现**

3a. `HOME_HELP["above_ma"]` 改为：

```python
    "above_ma": "收盘价高于自己近 {n} 日均价的股票占比。过半说明多数股票处在上升段，反之则是普跌。",   # 页面按 20/60/200 填
```

3b. 在 `HOME_HIGH_LOW` 定义（Task 23 加的 `HOME_HIGH_LOW = "52 周新高 / 新低"`）的下一行加：

```python
HOME_HL_TOTAL = "共 {n} 只"      # 52 周卡片副文：参与新高新低统计的股票数，与左边三张的分母同义
```

3c. `JOB_HELP["market_breadth"]` 整条换成：

```python
    "market_breadth": _f("""**收盘数据**——「趋势宽度」四张卡片的数据来源。

- **做什么**：向数据源要全沪深每只股票的当日收盘数据，逐日存下，算出站上均线的占比与 52 周新高/新低。
- **多久**：每个交易日一次约 20 秒；第一次要补一年左右的历史，需要{breadth_first_run}，可随时停止、下次接着补。
- **什么时候跑**：交易日 {breadth_ready} 之后才有当天的数据；早点了也不会失败，只是补不到当天。
- **结果在哪**：就是上方「趋势宽度」那四张卡片。
"""),
```

3d. 三处注释：
- `#: 人工步骤卡片的 \`?\` 帮助。` → `#: 「人工步骤」那行「去增删信号池」链接的悬停说明。`
- `#: 人工步骤卡片正文那句（比 ? 里的短，讲的是同一件事的结论）。` → `#: 「人工步骤」那行正文（比链接上的悬停说明短，讲的是同一件事的结论）。`
- `# 任务卡片右上角 st.popover("?") 的内容：这个任务做什么、大概多久、结果在哪看。` →
  `# 任务的就地说明：控制台卡片标题旁的帮助圆点、首页「开始准备 / 更新收盘数据」按钮的悬停提示。这个任务做什么、大概多久、结果在哪看。`

- [ ] **Step 4: 跑文案测试与全量**

Run: `.venv/bin/python -m pytest tests/test_guide.py -q` → PASS
Run: `.venv/bin/python -m pytest -q` → 全部 PASS（`test_enough_history_shows_sixty_day_trend_lines_and_the_help` 此时仍查 `at.markdown` 里的原文模板——弹出框还在、还在渲染模板原文，所以照样过；Task 4 改它）。

- [ ] **Step 5: 提交**

```bash
git add app/guide.py tests/test_guide.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "copy(home): 站上 N 日线说明改模板、新增「共 N 只」、收盘数据任务说明不点名按钮并去掉多余空格

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 首页——卡片挂圆点、删弹出框、按钮自带说明、两处对齐

**Files:**
- Modify: `app/pages_home.py`（`_index_cards`、`_thermometer`、`TREND_WINDOWS` 旁、`_trend_cards`、`_job_area` 末尾）
- Test: `tests/test_dashboard_home.py`

- [ ] **Step 1: 改测试助手、写失败的测试**

1a. `_card` 助手换成前缀匹配（标签 div 里多了圆点；这个前缀对八个标签都唯一，含「站上 20」与「站上 200」）：

```python
def _card(at, label: str) -> str:
    """label 那张首页卡片的 html（每张卡片是一个 st.html 元素）。标签后面可能跟着帮助圆点，
    所以按前缀 `<div class="qd-card-label">{label}<` 匹配。"""
    cards = [h for h in _htmls(at) if f'<div class="qd-card-label">{label}<' in h]
    assert len(cards) == 1, (label, cards)
    return cards[0]
```

1b. `test_enough_history_shows_sixty_day_trend_lines_and_the_help` 的最后两行换成：

```python
    guide = app_module("guide")
    assert guide.HOME_HELP["above_ma"].format(n=20) in _card(at, "站上 20 日线")
```

1c. 文件末尾追加：

```python
# ---------------------------------------------------------------- 就地帮助圆点（2026-09-29 设计）
THERMO_HELP = {"涨停": "limit_up", "跌停": "limit_down", "炸板率": "zb_rate", "最高连板": "max_lbc"}
#: 标签 → (HOME_HELP 键, N, 是否向左展开)。右半边的挂点向左展开（spec §1.1）。
TREND_HELP = {"站上 20 日线": ("above_ma", 20, False), "站上 60 日线": ("above_ma", 60, False),
              "站上 200 日线": ("above_ma", 200, True), "52 周新高 / 新低": ("high_low", None, True)}


def test_home_has_no_popover(tmp_path):
    _breadth_fixture(tmp_path, 22)
    at = _home(tmp_path)
    assert not at.exception, at.exception
    assert at.get("popover") == [], "带下拉箭头的「? ⌄」弹出框不该再出现"


def test_each_thermometer_card_explains_only_itself_and_opens_leftward(tmp_path):
    at = _home(tmp_path)
    tips = app_module("guide").HOME_HELP
    for label, key in THERMO_HELP.items():
        card = _card(at, label)
        assert tips[key] in card, label
        assert not [k for k in THERMO_HELP.values() if k != key and tips[k] in card], label
        assert "qd-help-end" in card, f"{label}：温度计整块在右栏，提示框要向左展开"


def test_each_trend_card_explains_only_itself_and_the_right_half_opens_leftward(tmp_path):
    _breadth_fixture(tmp_path, 22)     # 22 天：20 日线有数，60/200/52 周是「—」——两个分支都在这一次渲染里
    at = _home(tmp_path)
    assert not at.exception, at.exception
    help_ = app_module("guide").HOME_HELP
    texts = {label: (help_[key].format(n=n) if n else help_[key])
             for label, (key, n, _) in TREND_HELP.items()}
    for label, (_, _, end) in TREND_HELP.items():
        card = _card(at, label)
        assert texts[label] in card, label
        assert not [o for other, o in texts.items() if other != label and o in card], label
        assert ("qd-help-end" in card) is end, label
    assert "近 60 日" in _card(at, "站上 60 日线")
    assert "qd-card-sub" not in _card(at, "52 周新高 / 新低"), "「—」时不出「共 N 只」"


def test_high_low_card_shows_its_denominator_and_help(tmp_path):
    """n_hl > 0 那个分支：补一行「共 N 只」（与左边三张的分母同位置，小图才对齐），圆点照挂。"""
    import pandas as pd
    ms = _market_store(tmp_path)
    day = _fixture_day()
    days = pd.date_range(day.replace(day=1), day + pd.Timedelta(days=40)).date
    ms.save_calendar(days, {day})
    ms.save_snapshot(day, pd.DataFrame({"code": ["sh.600000"], "close": [1.0], "preclose": [1.0],
                                        "tradestatus": [1], "isST": [0]}))
    ms.save_breadth(pd.DataFrame({"date": [day], "above20": [1], "n20": [2], "above60": [0], "n60": [0],
                                  "above200": [0], "n200": [0], "new_high": [36], "new_low": [104],
                                  "n_hl": [4922]}))
    at = _home(tmp_path)
    assert not at.exception, at.exception
    g = app_module("guide")
    card = _card(at, "52 周新高 / 新低")
    assert _card_value(card) == "36 / 104"
    assert g.HOME_HL_TOTAL.format(n=4922) in card
    assert g.HOME_HELP["high_low"] in card and "qd-help-end" in card


def test_index_cards_put_change_and_percent_on_two_lines(tmp_path):
    at = _home(tmp_path)
    for _, name in market.INDEXES:
        card = _card(at, name)
        assert card.count('class="qd-card-sub"') == 2, card
        assert " · " not in card, card


def test_the_start_button_carries_the_job_help(tmp_path):
    at = _home(tmp_path)                        # 从没准备过 → 「开始准备」
    assert at.button(key="home_breadth_start").help == app_module("guide").JOB_HELP["market_breadth"]


def test_the_update_button_carries_the_job_help(tmp_path):
    _breadth_fixture(tmp_path, 22)              # 夹具日自己的快照还没有 → 「更新收盘数据」
    at = _home(tmp_path)
    assert at.button(key="home_breadth_update").help == app_module("guide").JOB_HELP["market_breadth"]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py -q`
Expected: 新测试 FAIL（有弹出框、卡片里没有说明、副文一行、按钮 `help` 为空）；`test_enough_history…` 也 FAIL（卡片里还没有说明）。

- [ ] **Step 3: 实现**

3a. `_index_cards` 里：

```python
        sub = (f"{q.change:+.2f}", f"{q.pct:+.2f}%")    # 分两行：合一行时窄卡片有的折有的不折，小图就高低不齐
```

3b. `_thermometer`：在函数上方（`def _thermometer` 之前）加常量，并把四张卡片与弹出框那段换掉：

```python
THERMO_HELP_ALIGN = "end"     # 温度计整块在页面右侧那一栏：提示框向左展开，免得越出视口右缘
```

```python
    labels, tips = guide.HOME_THERMO_LABELS, guide.HOME_HELP
    c1, c2 = st.columns(2)
    c1.html(theme.stat_card(labels["limit_up"], str(v.limit_up), color=fmt.UP,     # 没有副文 → 颜色上数值（Task 20）
                            help=tips["limit_up"], help_align=THERMO_HELP_ALIGN))
    c2.html(theme.stat_card(labels["limit_down"], str(v.limit_down), color=fmt.DOWN,
                            help=tips["limit_down"], help_align=THERMO_HELP_ALIGN))
    c3, c4 = st.columns(2)
    c3.html(theme.stat_card(labels["zb_rate"], rate, note=guide.HOME_ZB_COUNT.format(n=v.zb_tc),
                            help=tips["zb_rate"], help_align=THERMO_HELP_ALIGN))
    c4.html(theme.stat_card(labels["max_lbc"], lbc, help=tips["max_lbc"], help_align=THERMO_HELP_ALIGN))
```

（删掉原来的 `with st.popover("?"): …` 三行。）

3c. `TREND_DAYS_SHOWN = 60` 下一行加：

```python
#: 四张趋势卡片提示框的展开方向（按列序）：右半边两张向左展开，免得越出视口右缘。
TREND_HELP_ALIGN = ("start", "start", "end", "end")
```

3d. `_trend_cards` 从 `cols = st.columns(4)` 起到函数末尾换成：

```python
    cols = st.columns(4)
    for i, (col, (n, above, denom)) in enumerate(zip(cols, TREND_WINDOWS)):
        label = guide.HOME_ABOVE_MA.format(n=n)
        tip = dict(help=guide.HOME_HELP["above_ma"].format(n=n), help_align=TREND_HELP_ALIGN[i])
        if last[denom] == 0:
            col.html(theme.stat_card(label, fmt.MISSING, note=_shortfall_note(n, asof, cal, have, gaps), **tip))
            continue
        pct = 100 * last[above] / last[denom]
        series = [(r[above] / r[denom]) if r[denom] else None for _, r in tail.iterrows()]
        col.html(theme.stat_card(label, f"{pct:.1f}%", sub=f"{int(last[above])}/{int(last[denom])}",
                                 img=minicharts.trend_line(series),
                                 note=guide.HOME_RECENT_DAYS.format(n=len(tail)), **tip))
    hl_tip = dict(help=guide.HOME_HELP["high_low"], help_align=TREND_HELP_ALIGN[3])
    if last["n_hl"] == 0:
        note = _shortfall_note(breadth.HIGH_LOW_WINDOW, asof, cal, have, gaps)
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, fmt.MISSING, note=note, **hl_tip))
    else:
        cols[3].html(theme.stat_card(guide.HOME_HIGH_LOW, f"{int(last['new_high'])} / {int(last['new_low'])}",
                                     sub=guide.HOME_HL_TOTAL.format(n=int(last["n_hl"])),
                                     img=minicharts.highs_lows(tail["new_high"].tolist(), tail["new_low"].tolist()),
                                     note=guide.HOME_RECENT_DAYS.format(n=len(tail)), **hl_tip))
```

（删掉原来末尾的 `with st.popover("?"): …` 三行。）

3e. `_job_area` 末尾四行（`help_col, btn_col = …` 到 `ui.start_job(…)`）换成：

```python
    # 说明挂在按钮自己身上（原生按钮提示，支持加粗与列表）；不再在旁边另开一个带下拉箭头的「?」。
    if st.button(f"▶ {label}", key=key, help=guide.JOB_HELP["market_breadth"]):
        ui.start_job("market_breadth", {})
```

- [ ] **Step 4: 跑首页测试与全量**

Run: `.venv/bin/python -m pytest tests/test_dashboard_home.py -q` → 全部 PASS
Run: `.venv/bin/python -m pytest -q` → 全部 PASS（`N passed, 6 deselected`）
Run: `grep -n "popover" app/pages_home.py` → 无输出

- [ ] **Step 5: 提交**

```bash
git add app/pages_home.py tests/test_dashboard_home.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(home): 温度计与趋势卡片标签旁挂帮助圆点、按钮自带说明，去掉三个「? ⌄」；指数卡片副文分两行、52 周卡片补「共 N 只」，小图对齐

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 任务控制台——卡片标题旁挂圆点

**Files:**
- Modify: `app/pages_console.py`（`_card_head`、`_render_card`、`_ELSEWHERE` 下方加常量、`_manual_step_strip` docstring 一句）
- Test: `tests/test_dashboard_guide.py`、`tests/test_dashboard_console.py`

- [ ] **Step 1: 改测试、写失败的测试**

1a. `tests/test_dashboard_guide.py` 文件头注释第 7–8 行
`#   - 每张任务卡片右上角的 st.popover，以及每个参数控件的 help=` / `#     （漏挂不会报错，只是 tooltip 永远不出现）；`
改为
`#   - 每张任务卡片标题旁的帮助圆点，以及每个参数控件的 help=` / `#     （漏挂不会报错，只是说明永远不出现）；`

1b. 在 `guide` 按路径加载那几行之后加（`theme` 没有 dataclass，不必塞 `sys.modules`）：

```python
_THEME_SPEC = importlib.util.spec_from_file_location("qd_theme_ui", ROOT / "app" / "theme.py")
theme = importlib.util.module_from_spec(_THEME_SPEC)
_THEME_SPEC.loader.exec_module(theme)
```

1c. 删掉 `_popover_label` 助手（`def _popover_label(block) -> str:` 那 3 行）。

1d. 把 `# ================================================================ 就地帮助：卡片 popover` 那一节——
`CARDS_WITH_HELP` 常量与注释、`test_every_card_has_a_help_popover`、`test_the_popover_content_is_this_jobs_help`、
`test_the_popover_sits_next_to_the_card_title`、`test_control_bar_pages_have_no_popover`——整段换成：

```python
# ================================================================ 就地帮助：卡片标题旁的帮助圆点

#: 控制台上的三张任务卡片（market_breadth 的控件在首页，pages_console._ELSEWHERE）。
CONSOLE_CARDS = tuple(n for n in jobs.JOBS if n != "market_breadth")
#: 流水线那一行右栏的卡片：提示框向左展开，免得越出视口右缘（spec §2.2）。
RIGHT_COLUMN = ("daily_signal",)


def _card_section(at: AppTest, name: str) -> str:
    """name 那张卡片的标题 html。**锚定在开头**找：每条 JOB_HELP 都以 **{label}** 开头，渲染成
    <b>{label}</b> 后同样包含 >{label}<，只查子串的话标题丢了也照样通过。"""
    anchor = f'class="qd-section">{jobs.JOBS[name].label}<'
    hits = [h for h in _htmls(at) if anchor in h]
    assert len(hits) == 1, (name, hits)
    return hits[0]


def test_console_has_no_popover(tmp_path):
    """带下拉箭头的「? ⌄」弹出框看起来像选择框（2026-09-29 用户反馈），全部换成帮助圆点。"""
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    assert at.get("popover") == []


@pytest.mark.parametrize("corrupt", [False, True], ids=["normal", "corrupt-state"])
def test_each_card_title_carries_its_own_job_help(tmp_path, corrupt):
    """圆点紧跟卡片标题（同一个 qd-section 里），内容是**这个**任务的说明；右栏那张向左展开。
    状态文件损坏的分支也要有——那时没有按钮，却最需要知道这个任务是干什么的。"""
    if corrupt:
        runs = tmp_path / "output" / "runs"
        runs.mkdir(parents=True)
        (runs / "backtest.json").write_text('{"script": "backtest", "pid":', encoding="utf-8")
    at = _page(tmp_path, "任务控制台")
    assert not at.exception, at.exception
    for name in CONSOLE_CARDS:
        align = "end" if name in RIGHT_COLUMN else "start"
        want = theme.help_dot(guide.JOB_HELP[name], align=align, wide=True)
        assert want in _card_section(at, name), name


def test_control_bar_pages_have_no_help_dot_or_popover(tmp_path):
    """精简控制条要越薄越好（§4.2）：任务说明只挂在控制台的完整卡片上。"""
    for page in ("回测报告", "个股K线", "信号"):
        at = _page(tmp_path, page)
        assert at.get("popover") == [], f"{page} 的控制条不该有弹出框"
        sections = [h for h in _htmls(at) if 'class="qd-section"' in h]
        assert not [s for s in sections if "qd-help-dot" in s], f"{page} 的控制条不该有帮助圆点"
```

1e. `tests/test_dashboard_console.py::test_three_cards_render` 第一个循环换成：

```python
    for name in CONSOLE_JOBS:
        label = jobs.JOBS[name].label
        assert f'class="qd-section">{label}<' in text, f"缺少「{label}」卡片，实际: {text}"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/python -m pytest tests/test_dashboard_guide.py tests/test_dashboard_console.py -q`
Expected: `test_console_has_no_popover`、`test_each_card_title_carries_its_own_job_help[normal|corrupt-state]` FAIL；其余 PASS。

- [ ] **Step 3: 实现**

3a. `_ELSEWHERE = …` 那一行下面加：

```python
_RIGHT_COLUMN = (_PIPELINE[1],)                 # 流水线那一行右栏的卡片：帮助提示框向左展开，免得越出视口右缘
```

3b. `_card_head` 整个换成：

```python
def _card_head(job: jobs.Job, pill_html: str = "", *, help_align: str = "start") -> None:
    """卡片标题行：衬线小标题 + 帮助圆点 + 状态 pill（§3.2）。

    帮助必须与标题同一行：塞到参数控件下面就排在按钮后面，正好在"不知道这个按钮会干什么"
    的那一刻看不见。圆点紧跟标题文字，悬停或 Tab 键选中出说明（2026-09-29 起取代带下拉箭头的
    st.popover——用户看了以为是选择框）。状态文件损坏的那条分支也走这里——那时候没有按钮，
    人却最需要知道这个任务是干什么的，所以说明不能挂在按钮上。
    """
    st.html(theme.section(job.label, pill_html, help=guide.JOB_HELP[job.name],
                          help_align=help_align, help_wide=True))
```

3c. `_render_card` 里：在 `job = jobs.JOBS[job_name]` 下一行加
`align = "end" if job_name in _RIGHT_COLUMN else "start"`，并把两处调用改为
`_card_head(job, help_align=align)` 与 `_card_head(job, theme.pill(*view.status_pill(state)), help_align=align)`。

3d. `_manual_step_strip` docstring 里「详细说明挂在「去信号池」那个链接的 tooltip 上，不再单独开 ? 弹窗。」改为
「详细说明挂在「去信号池」那个链接的悬停提示上。」

- [ ] **Step 4: 跑控制台相关测试与全量**

Run: `.venv/bin/python -m pytest tests/test_dashboard_guide.py tests/test_dashboard_console.py -q` → PASS
Run: `.venv/bin/python -m pytest -q` → 全部 PASS
Run: `grep -rn "popover" app/` → 无输出

- [ ] **Step 5: 提交**

```bash
git add app/pages_console.py tests/test_dashboard_guide.py tests/test_dashboard_console.py
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "feat(console): 任务卡片标题旁挂帮助圆点取代「? ⌄」；右栏卡片向左展开，状态文件损坏时照样有说明

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 文档同步与变异自查

**Files:**
- Modify: `README.md`（第 18 行附近、「就地帮助」一节）
- Modify: `docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md`（第 93、225 行）

- [ ] **Step 1: README**

- 第 18 行：「每日流水线、就绪状态与 `?` 帮助都在那儿；」→「每日流水线、就绪状态与各任务的就地说明（卡片标题旁的小圆圈 `?`）都在那儿；」
- 「就地帮助」一节第一条：
  「- 控制台每张任务卡片右上角一个 `?`，点开是"这个任务做什么、大概多久、参数含义、产物在哪"；」→
  「- 控制台每张任务卡片标题旁一个小圆圈 `?`，悬停（或按 Tab 选中）是"这个任务做什么、大概多久、结果在哪"；首页涨停温度计与趋势宽度的每张卡片标签旁也有一个，只解释那一个数；首页「开始准备 / 更新收盘数据」按钮悬停即出任务说明；」

- [ ] **Step 2: 首页设计文档**

- 第 93 行「挂在卡片的 `?` 上」→「挂在卡片标签旁的小圆圈 `?` 上（悬停即出；2026-09-29 由弹出框改来，见 `2026-09-29-panel-inline-help-design.md`）」
- 第 225 行「`JOB_HELP` 的说明挂在按钮区的 `?` 上。」→「`JOB_HELP` 的说明挂在按钮自己的悬停提示上（2026-09-29 改，见 `2026-09-29-panel-inline-help-design.md`）。」

- [ ] **Step 3: 全量与文案对账**

Run: `.venv/bin/python -m pytest -q` → 全部 PASS
Run: `grep -rn "右上角\|popover\|弹出框" app/ README.md` → 只允许剩下与本改动无关的说法（逐条看一眼并在报告里列出）

- [ ] **Step 4: 变异自查（每项改完立刻还原；用 `cp` 备份、`mv` 还原，**不许** `git checkout --`）**

对每一项：`cp <file> /tmp/mut.bak` → 改 → `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest <tests> -q -x` 应 FAIL → `mv /tmp/mut.bak <file>`。

| # | 改动 | 应红的测试文件 |
|---|---|---|
| 1 | `_thermometer` 里删掉「跌停」卡片的 `help=…` | `tests/test_dashboard_home.py` |
| 2 | `_trend_cards` 的 `n_hl > 0` 分支删掉 `**hl_tip` | `tests/test_dashboard_home.py` |
| 3 | `TREND_HELP_ALIGN` 改成 `("start", "start", "start", "end")` | `tests/test_dashboard_home.py` |
| 4 | `THERMO_HELP_ALIGN = "start"` | `tests/test_dashboard_home.py` |
| 5 | `_RIGHT_COLUMN = ()` | `tests/test_dashboard_guide.py` |
| 6 | `_render_card` 损坏分支改回 `_card_head(job)` | `tests/test_dashboard_guide.py` |
| 7 | `theme.section` 里 `{_esc(title)}` 删掉 | `tests/test_dashboard_guide.py tests/test_dashboard_console.py` |
| 8 | CSS 显示规则里 `:focus-visible` 改成 `:focus` | `tests/test_theme.py` |
| 9 | `_tip_markup` 改为对**未转义**的 `text` 做 split、最后再整体 `_esc` | `tests/test_theme.py` |
| 10 | 指数卡片 `sub` 改回 `f"{q.change:+.2f} · {q.pct:+.2f}%"` | `tests/test_dashboard_home.py` |
| 11 | 52 周卡片去掉 `sub=guide.HOME_HL_TOTAL…` | `tests/test_dashboard_home.py` |
| 12 | 按钮去掉 `help=…` | `tests/test_dashboard_home.py` |

若某项没有红：补一条最小测试让它红，并在报告里说明。

- [ ] **Step 5: 提交**

```bash
git add README.md docs/superpowers/specs/2026-09-23-market-overview-homepage-design.md
git -c user.name=CH3NQ1 -c user.email=khalilstar@qq.com commit -m "docs(panel): README 与首页设计文档改成现行的就地帮助说法（小圆圈 ?、按钮悬停说明）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

（若变异自查补了测试，一并提交，提交说明里写明。）

---

### Task 7: 真浏览器验收（主会话做，不派子代理）

浏览器面板在主会话里。**先重启** `tape` 预览服务（Streamlit 不热载 `src/` 与已导入的 `app/` 模块）。

- [ ] 1280 px 与 1024 px 各一轮：逐个悬停首页 8 个圆点、2 个按钮（「开始准备」或「更新收盘数据」，按本机数据状态出现哪个就测哪个），控制台 3 个圆点。提示框完整、没被后面的元素盖住、不越出视口、文字是对应那句。
- [ ] Tab 键能依次选中圆点并显示提示框；鼠标点一下圆点再移开，提示框消失。
- [ ] 提示框里的列表缩进正常；首页与控制台的圆点大小、字形一致。
- [ ] 脚本读六张指数卡片、四张趋势卡片 `.qd-card-img` 的 `getBoundingClientRect().top`：各组内相差不超过 1 px。
- [ ] 首页与控制台都没有「⌄」下拉箭头。
- [ ] 结果写进设计文档末尾一节「§8 验收记录（2026-09-30）」并提交。
