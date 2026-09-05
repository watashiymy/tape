"""「个股K线」的渲染口：把 plotly 图放进项目自己的 iframe 组件，而不是 st.plotly_chart（2026-09-05）。

**为什么绕开 st.plotly_chart**：Streamlit 的图表组件在 `onUpdate` 里把每一次缩放中间态
写回 React state 并重画整图，而 react-plotly 在缩放过程中的每个 `plotly_relayouting`
事件上都触发 `onUpdate`。真机实测（同一张 120 根 K 线，20 次触控板缩放事件）：
横轴起点 0.1 → 3.0 后被冲回初始值 −0.5，如此往复三轮——用户看到的就是"画面抽动"；
同一张图放进独立页面则 0.1 → 10.4 单调平滑。所以 K 线由自己的 iframe 承载，plotly
独占事件循环。

**plotly.js 从哪来**：不联网（面板离线也要能看图）、不复制 4.8 MB 进仓库或产物目录——
用 `declare_component(path=<已安装 plotly 包的 package_data 目录>)` 把那个目录挂成
Streamlit 的静态资源，`index.html` 按相对路径加载 `../<组件名>/plotly.min.js`。
版本与生成 JSON 的 Python 端天然一致。

**协议**：`kline_component/index.html` 只实现组件协议的最小子集（componentReady /
render / setFrameHeight），不依赖 streamlit-component-lib。
"""
from __future__ import annotations

import json
from pathlib import Path

import plotly
import streamlit.components.v1 as components

#: 宿主页所在目录（随仓库分发）与 plotly 包自带的 plotly.min.js 所在目录。
COMPONENT_DIR = Path(__file__).resolve().parent / "kline_component"
PLOTLY_DATA_DIR = Path(plotly.__file__).resolve().parent / "package_data"
PLOTLY_JS_NAME = "plotly.min.js"

#: iframe 高度 = 图高度。图的 layout.height 由 charts.kline_chart 定（550）。
DEFAULT_HEIGHT = 550

_declared: dict | None = None


def _components() -> dict:
    """两个"组件"：一个真渲染（index.html），一个只为把 plotly 包目录挂成可访问的静态路径。

    **在脚本运行期内声明、只声明一次**：Streamlit 只在有 ScriptRunContext 时登记组件，
    且明说 import 期声明将来会被禁止。首次 render() 一定发生在脚本运行期，之后缓存。
    名字会带上本模块名前缀（kline_view.tape_kline），URL 形如
    /component/kline_view.tape_kline/index.html，两者同级，所以相对路径 ../<name>/ 可达。
    """
    global _declared
    if _declared is None:
        kline = components.declare_component("tape_kline", path=str(COMPONENT_DIR))
        plotlyjs = components.declare_component("tape_plotlyjs", path=str(PLOTLY_DATA_DIR))
        _declared = {"kline": kline, "plotly_js": plotly_js_url(plotlyjs.name)}
    return _declared


def plotly_js_url(plotlyjs_component_name: str) -> str:
    """index.html 加载 plotly.min.js 用的**相对**路径：不写成 /component/... 绝对路径，
    面板挂在 server.baseUrlPath 之下时绝对路径就错了，相对路径怎么挂都对。"""
    return f"../{plotlyjs_component_name}/{PLOTLY_JS_NAME}"


def spec(fig) -> dict:
    """plotly 图 → 可 JSON 化的 {data, layout}。走 fig.to_json()：numpy 数组、时间戳、
    NaN 都由 plotly 自己的编码器处理，自己 json.dumps 会在第一个 numpy 标量上炸。"""
    return json.loads(fig.to_json())


def render(fig, *, config: dict, bg: str, key: str = "kline") -> None:
    """渲染一张 K 线。`config` 是给前端的 plotly 配置（scrollZoom 等），`bg` 是 iframe
    底色（与图的纸面同色，边界看不出来）。同一个 key 的 iframe 跨重跑复用，
    Plotly.react 按 uirevision 保留缩放位置。"""
    height = int(fig.layout.height or DEFAULT_HEIGHT)
    parts = _components()
    parts["kline"](spec=spec(fig), config=dict(config), plotly_js=parts["plotly_js"],
                   height=height, bg=bg, key=key, default=None)
