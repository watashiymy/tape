"""Streamlit 本地面板 TAPE：streamlit run app/dashboard.py

八页三组（v0.3.1 §1）：使用说明 / 任务控制台 / 今日信号（无标题主组），
记账 / 持仓与盈亏（「交易日志」组），信号池 / 回测报告 / 个股K线（「研究」组），
走 st.navigation + st.Page 的原生导航（每页一个 URL，侧栏按组分段）。
使用说明是**默认落地页**，纯文档（文案全在 app/guide.py）；
三张只读页展示 output/ 与 data/cache/ 里的产物，顶部各有一条精简控制条（§4.2）；
控制台页可在**本机**起三个入口脚本并看进度、日志与结果；
信号池页增删 config/settings.yaml 的 universe（v0.2.2 §3）；
记账页读写 journal/trades.csv —— 面板上唯一会改**不可再生**文件的地方
（v0.3.0 §5），持仓与盈亏页展示由它算出来的结果。

**本文件只做装配**（设计 §4）：页面函数在 app/pages_*.py，共享件在 app/ui.py，
文案在 app/guide.py，视觉在 app/theme.py，业务逻辑全在 src/quant/。
拆分的理由与"为什么共享件不能留在本文件"见 app/ui.py 的模块 docstring。

面板能执行本机命令，所以启动时**必须**显式绑回环地址：
    streamlit run app/dashboard.py --server.address 127.0.0.1
不加这个参数时 streamlit 监听 *:8501（所有网卡，v0.2.1 M3 实测），
切勿再用 --server.address 0.0.0.0 主动暴露到局域网。"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# app/ 也得显式进 sys.path：`streamlit run` 会把脚本目录放进去，但用 importlib 直接
# 加载本文件（测试就是这么干的）不会，那时 `import theme` 直接 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import guide       # noqa: E402  说明页与就地帮助的全部文案（app/guide.py，纯数据）
import journal_ui  # noqa: E402  日志页的编排层（这里只用它那个跳页钩子）
import theme       # noqa: E402  视觉基座（app/theme.py）
import ui          # noqa: E402  共享件：页头 / 表格 / 控制条 / 路径（app/ui.py）
from pages_backtest import page_backtest, page_kline        # noqa: E402
from pages_console import page_console                      # noqa: E402
from pages_guide import page_guide                          # noqa: E402
from pages_journal_entry import page_journal_entry          # noqa: E402
from pages_journal_report import page_journal_report        # noqa: E402
from pages_signals import page_signals                      # noqa: E402
from pages_universe import page_universe                    # noqa: E402

# 路径每轮重新钉一遍（ui.bind 的 docstring 说明了为什么不能只靠 ui.py 的 __file__）。
# 主脚本每次 rerun 都重新 exec，所以这行也每轮都跑。
ui.bind(ROOT)

st.set_page_config(page_title=theme.PAGE_TITLE, layout="wide")
# 字体、语义化 CSS 与 Cmd+C 热键修复（app/theme.py）。必须每轮都注入：Streamlit
# 每次 rerun 重画整棵元素树，上一轮的 <style>/<script> 不留下来。
# 底色/主色不在这里——那些走 .streamlit/config.toml。
theme.inject()
# 品牌块 TAPE（v0.2.2 §2.1）。走 st.logo 而不是 st.sidebar.html：侧栏里
# **导航之上**只有这一个官方位置，普通侧栏元素一律排在导航链接下面（实测）。
st.logo(theme.BRAND_LOGO, size=theme.BRAND_LOGO_SIZE)

# 侧栏八页三组（v0.3.1 §1）：st.navigation 的 Mapping 分组形态取代扁平列表。
# 每组一个键、组内一个 Page 列表；dict 的插入序就是侧栏组序。
#
# 主组的键是空字符串 ""：**实测**（streamlit 1.61.1，真浏览器截图）它渲染为
# 无标题组——三个日常页顶格显示、没有组头占位；官方文档也确认 "" 是合法键
# （st.navigation 收到 list 时内部就折成 {"": pages}）。命名组「交易日志」「研究」
# 各带组头。设计里"若 "" 渲染异常则三组都起名"的备选方案因此不必启用。
#
# 顺序沿袭 v0.2.2 §2.2 + v0.3.0 §5 的既有决定：
#   -「使用说明」第一位且 default=True → **默认落地页**（v0.2.1 的决定不变）：
#     第一次打开面板的人先看说明，而不是先对着一句"暂无回测结果"发愁；
#   -「任务控制台」第二位：它是最常用的操作入口；
#   -「交易日志」组紧跟「今日信号」：看到信号 → 记一笔，挨着才是一条路；
#     组内「记账」在前、「持仓与盈亏」在后——先记后看，就是使用顺序。
#     「记账」沿用老的 journal 路径（老书签不断），报表页用新的 positions；
#   -「研究」组（信号池 / 回测报告 / 个股K线）殿后。
# url_path 显式给短英文：不给的话会取函数名，地址栏出现 /page_backtest 这种内部名。
# 「记一笔」要跳到的那一页单独留个名字：st.switch_page 收的是 Page 对象
# （函数页没有文件路径可给），而按下标从组里取第几个日后一定会错位。
ENTRY_PAGE = st.Page(page_journal_entry, title="记账", icon=":material/edit_note:",
                     url_path="journal")
PAGES = {
    "": [
        st.Page(page_guide, title="使用说明", icon=":material/menu_book:",
                url_path="guide", default=True),
        st.Page(page_console, title="任务控制台", icon=":material/play_circle:",
                url_path="console"),
        st.Page(page_signals, title="今日信号", icon=":material/notifications:",
                url_path="signals"),
    ],
    "交易日志": [
        ENTRY_PAGE,
        st.Page(page_journal_report, title="持仓与盈亏",
                icon=":material/account_balance_wallet:", url_path="positions"),
    ],
    "研究": [
        st.Page(page_universe, title="信号池", icon=":material/list:",
                url_path="universe"),
        st.Page(page_backtest, title="回测报告", icon=":material/assessment:",
                url_path="backtest"),
        st.Page(page_kline, title="个股K线", icon=":material/candlestick_chart:",
                url_path="kline"),
    ],
}
# 落地页会被切走，切走之后就没有说明页的入口提示了，所以侧栏常驻一句指路。
st.sidebar.caption("第一次用先看「使用说明」页（侧栏第一项，也是默认落地页）："
                   "三个任务怎么配合、输出怎么读、已知局限在哪。")
# 面板自 v0.2.0 起能在本机起进程，"纯只读"从此是假话（设计 §4.3）。
st.sidebar.caption("本面板可在**本机**启动三个任务（全市场扫描 / 每日信号 / 回测），"
                   "同时只允许一个任务；进度、日志与结果见「任务控制台」页。"
                   "命令行入口全部保留，两种方式等价。")
# 必须是侧边栏里看得见的一句，不能只写在模块 docstring 里：面板能执行本机命令，
# 暴露到网络就等同于把远程命令执行接口挂到局域网上（设计 §5.3）。
# 措辞在 v0.2.1 M3 纠正过：原先写"streamlit 默认只监听本机、保持默认即可"是**假话**
# ——实测不带地址参数时监听 *:8501（所有网卡，日志里的 Network URL 就是证据）。
# 安全建议只给可执行的那一条：显式绑回环地址。详情见「使用说明」页的安全提示块。
st.sidebar.warning("安全提示：本面板可在本机执行脚本。启动时请显式绑定回环地址"
                   f"（`{guide.FACTS['bind_flag']}`）；不加时 streamlit 监听所有网卡，"
                   "同网段的人就能点这里的「开始」。"
                   "切勿用 `--server.address 0.0.0.0` 暴露到局域网。")
st.sidebar.caption("策略仅用于学习，不构成投资建议。")
# 「今日信号」与扫描表里点过「记一笔」之后要跳到「记账」子页（v0.3.1 §1：
# 目标从整页「交易日志」改指拆出来的录入页）。
# 必须排在 st.navigation **之后**：st.switch_page 只认已注册的页，而注册就发生在
# st.navigation 里；也不能放进按钮回调——那时脚本还没重跑，页面也还没注册。
nav = st.navigation(PAGES)
journal_ui.jump_if_requested(ENTRY_PAGE)
nav.run()
