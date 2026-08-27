# 导航改版 / 信号池编辑 / 热键修复 v0.2.2 设计文档

- 日期：2026-08-27
- 状态：待实施
- 前置：v0.2.1 已完工（885 项离线 + 3 项联网测试全绿）
- 用户诉求三项：① 侧边栏改名（英文）+ 换掉圆点选择器 ② 页面上增删信号池股票 ③ 修 Cmd+C 触发"Clear caches"

## 1. 诉求③：Cmd+C 被 Streamlit 热键吃掉（已定位到根因）

### 1.1 根因（读 Streamlit 前端 bundle + 浏览器实测确认）

Streamlit 用 hotkeys-js 绑定**单键**快捷键（`r` 重跑、`c` 清缓存）。其过滤器（bundle 里的 `t_`）只排除输入类元素：

```js
t_ = e => { var t = e.target||e.srcElement, n = t.tagName;
            return !(t.isContentEditable || n===`INPUT` || n===`SELECT` || n===`TEXTAREA`) }
```

**完全不检查修饰键**。在表格/正文里选中文字按 Cmd+C 时，事件目标是普通元素（实测 `document.activeElement` 为 `SECTION`）→ 过滤器放行 → 触发 `CLEAR_CACHE`。这是 Streamlit 上游行为，不是本项目的 bug，但用户体验上必须修。

### 1.2 修法：包装 `hotkeys.filter`（而非拦截键盘事件）

浏览器实测：`window.hotkeys` 已暴露，`window.hotkeys.filter` 是可写函数。**与库协作而非对抗**：让带修饰键的组合直接不进热键系统，裸按 `c`/`r` 仍照常工作。

```js
const wrap = (orig) => function (e) {
  if (e && (e.metaKey || e.ctrlKey || e.altKey)) return false;   // Cmd/Ctrl/Alt 组合一律放行给浏览器
  return orig ? orig.apply(this, arguments) : true;
};
```

**关键难点**：Streamlit 在 `useEffect` 里重新赋值 `hotkeys.filter`（依赖变化时重跑），直接赋值会被覆盖。用 `Object.defineProperty` 装 setter，**任何后续赋值都自动被包装**：

```js
let current = wrap(window.hotkeys.filter);
Object.defineProperty(window.hotkeys, 'filter', {
  configurable: true,
  get: () => current,
  set: (v) => { current = wrap(v); },      // Streamlit 再设几次都自动裹上
});
```

守卫：`window.hotkeys` 不存在时静默跳过（升级后若不再暴露，不能让面板崩）；用标志位防重复安装。

**注入方式**：`st.html(..., unsafe_allow_javascript=True)`（实测该参数存在），渲染在主文档而非 iframe，能触到 `window.hotkeys`。放在 `app/theme.py` 的注入点旁，每页都生效。

### 1.3 可测性

浏览器行为无法用 pytest 覆盖，因此：
- **纯逻辑下沉**：把包装逻辑写成独立 JS 字符串常量，Python 侧断言其包含关键片段（`metaKey`、`defineProperty`、幂等标志），防止被误删；
- **人工验收脚本**：README 写明验证步骤（选中表格文字 → Cmd+C → 应正常复制且不弹对话框；裸按 `c` → 仍应弹对话框，证明没把功能整个禁掉）。
- 注意：本机浏览器面板复现不了（对 cmd 键映射不同），**必须由用户在自己 Mac 上确认**，实施报告要如实说明这一点，不许声称"已验证通过"。

## 2. 诉求①：命名与导航

### 2.1 名字：**TAPE**

取自 "reading the tape"（看盘）——技术分析的**原初形态**：交易所报价纸带上滚动的价与量，正是本系统唯一的输入。短、英文、衬线大写好看，且与既定的"金融电报"视觉调性同源。不是那种可以套在任何项目上的通用科技名。

侧边栏锁定为：

```
TAPE                     ← Songti SC 衬线大写，字距放开
A股日线信号              ← 小号灰字副标
```

### 2.2 导航：`st.navigation` + `st.Page` 取代 `st.sidebar.radio`

现在的圆点单选器是 v0.1 的临时做法。1.61 已有原生多页 API（实测 `st.navigation` / `st.Page` / `st.page_link` 均存在），收益：

- 渲染成**导航链接**而非单选圆点，每项可带图标；
- **真实 URL 路由**：每页独立地址，浏览器前进/后退可用，可收藏、可分享；
- 是官方支持面，比自制导航更抗升级。

页面定义（顺序与图标）：

| 页 | 图标 | 说明 |
|---|---|---|
| 使用说明 | :material/menu_book: | 默认落地页（保持 v0.2.1 的决定） |
| 任务控制台 | :material/play_circle: | 从末位提到第二位——它是最常用的操作入口 |
| 今日信号 | :material/notifications: | |
| 信号池 | :material/list: | **新增**（见 §3） |
| 回测报告 | :material/assessment: | |
| 个股K线 | :material/candlestick_chart: | |

**重构风险**：`st.navigation` 要求把页面拆成可调用对象。现有 `app/dashboard.py` 是单文件多函数 + 末尾 `if page == ...` 分发。改法：保留所有页面函数不动，用 `st.Page(func, title=..., icon=...)` 包装即可，**函数体一行不改**——把重构风险压到最小。现有 885 项测试里凡断言侧栏结构的会失败，属预期，同步更新。

## 3. 诉求②：页面上增删信号池

### 3.1 现状与痛点

`config/settings.yaml` 的 `universe` 目前是用户手工编辑的 7 只票。每次从扫描结果里看中一只，要打开编辑器、找到位置、手动粘代码、注意引号和逗号——正是要消除的摩擦。

### 3.2 写入策略：外科式改写，**不用 yaml.safe_dump**

`yaml.safe_dump` 会重排键序、丢掉全部注释——而这个文件里的注释是有价值的（印花税分段依据、各参数含义）。也不引入 `ruamel.yaml`（新依赖，为一个小功能不值当）。

**做法**：只重写 `universe:` 这一个块，其余字节原样保留。

```python
# src/quant/config_edit.py（纯函数，离线可测）
def replace_universe_block(text: str, symbols: Sequence[str]) -> str:
    """把 YAML 文本里的 universe 块换成新列表，其余部分逐字节不动。
    支持两种既有写法：单行 flow（universe: ["a","b"]）与多行 flow（跨行的 [...]）。
    找不到 universe 块 / 有多个 → 抛错，不猜。"""
```

- 用**行扫描**定位 `^universe:` 到其 flow 列表闭合 `]` 为止（需处理跨行）；
- 生成规范格式（每行一只，便于 diff 与人工编辑）；
- **写入后立即 `load_settings` 复核**：解析失败或 universe 不等于预期 → 回滚原文件并抛错。宁可失败也不能留下坏配置——它驱动全部三个脚本。
- 落盘走已有的原子写模式（tmp + `os.replace`），与 `cache.py` 一致。

### 3.3 校验规则（`src/quant/universe.py`，纯函数）

| 规则 | 理由 |
|---|---|
| 6 位数字 | 防 YAML 把 `000333` 解析成八进制 219（已踩过） |
| 必须在扫描池内（主板、非 ST、上市满 400 天） | 引擎按主板 ±10% 建模；ST/新股会让回测口径失真 |
| 去重、保持稳定顺序（新增追加到末尾） | diff 友好 |
| 至少保留 1 只 | 空 universe 会让两个入口报出误导性错误（v0.1.1 已修过一次） |

扫描池清单来自 `get_all_symbols`，**带缓存**（`@st.cache_data(ttl=6h)`）——它要拉 7000+ 条记录，不能每次交互都请求。

### 3.4 界面

**A. 新增「信号池」页**

- 顶部：当前池子表格（代码 / 名称 / 最新价 / 加入日期(若有)），每行一个 `−` 按钮（`st.column_config.ButtonColumn` + `on_click`，实测 1.61 支持）；
- 搜索添加：`st.selectbox` 支持输入过滤，选项为"代码 名称"（如 `600519 贵州茅台`），选中后点 `+ 加入`；
  - 用 selectbox 而非自由文本框：代码可从候选中选，杜绝输错；同时支持按名称搜索（用户多半记得住名字记不住代码）；
- 底部：一行小字说明"改动会写入 config/settings.yaml，命令行运行同样生效"，以及当前池子大小。

**B. 扫描结果表加 `+` 列**

「今日信号」页的全市场扫描区块，每行加 `+` 按钮：一键把该股加入信号池。已在池中的行显示 `✓ 已在池中`（禁用态）。这是最短路径——看到信号当场就能加。

**C. 反馈**

加入/移除后 `st.toast` 提示 + 表格立即刷新。**不做二次确认弹窗**：这是低风险可逆操作（移错了再加回来即可），弹窗只会增加摩擦。

## 4. 分层与可测性

面板逻辑继续下沉，UI 只组装：

```
src/quant/config_edit.py   # replace_universe_block（纯文本变换）+ 原子写 + 写后复核
src/quant/universe.py      # 校验规则、去重、排序、"是否在扫描池内"判定
app/pages_universe.py      # 信号池页渲染（若 dashboard.py 过大则拆出）
```

`app/dashboard.py` 当前 426 行，加上新页会接近 600 行——超出"能一屏把握"的范围，因此本次顺带把页面函数按页拆到 `app/pages_*.py`，`dashboard.py` 只留导航装配与共享工具。这是"改到哪里就把哪里理顺"，不是无关重构。

## 5. 测试策略

- **`replace_universe_block`**：单行 flow / 多行 flow / 带行内注释 / universe 不在首行 / 缺失该键（抛错）/ 值含奇怪空白；**断言其余字节完全不变**（用完整 settings.yaml 做 fixture，比对非 universe 部分）。
- **写后复核回滚**：monkeypatch 让复核失败，断言原文件内容未变。
- **校验规则**：非 6 位、非数字、不在扫描池、重复、清空到 0 只（拒绝）。
- **热键 JS 常量**：断言含 `metaKey` / `defineProperty` / 幂等标志。
- **AppTest**：六页渲染无异常；信号池页在"扫描池获取失败（离线）"时降级为只读并给提示，不崩页。
- **回归**：885 项现有测试；改导航后侧栏结构断言同步更新；三个脚本命令行行为完全不变。
- **真实验收**：面板上加一只票 → 检查 `settings.yaml` 的 diff 只有 universe 块变化 → 命令行跑 `run_daily_signal.py` 确认新票被纳入。

## 6. 不做

- 不做 universe 的分组/标签/备注（YAGNI，等真有多个池子的需求再说）
- 不引入 ruamel.yaml
- 不做撤销历史（git 就是历史；且操作可逆）
- 不改三个脚本的命令行接口
- 不做热键的完整重映射（只解除修饰键组合被劫持，保留 Streamlit 原有单键功能）
