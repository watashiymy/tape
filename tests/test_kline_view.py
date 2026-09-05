# tests/test_kline_view.py — 「个股K线」的 iframe 组件（2026-09-05）
#
# 为什么有这个组件：st.plotly_chart 在缩放中途反复重画整图（Streamlit 的 onUpdate 把每个
# plotly_relayouting 中间态写回 React state），触控板双指缩放时横轴范围被反复冲回初始值
# ——真机实测 20 次事件里回弹三轮，用户看到的就是"画面抽动"。放进自己的 iframe 后 plotly
# 独占事件循环，实测单调平滑。这里钉住：宿主页与协议在、plotly.js 从已安装的包里本地提供
# （不联网、不复制）、图能编成 JSON、路径是相对的。
import importlib.util
import json
import sys
from pathlib import Path

from quant.report import charts
from tests.conftest import make_bars

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    """按路径加载 app/ 下的模块（同 tests/test_guide.py 的做法）。kline_view 只依赖
    plotly 与 streamlit，不依赖别的 app 模块，直接加载即可、不必先 exec dashboard.py。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / "app" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kline_view = _load("qd_kline_view", "kline_view.py")
INDEX = (kline_view.COMPONENT_DIR / "index.html").read_text(encoding="utf-8")


def _bars():
    return make_bars([dict(date=f"2024-01-{d:02d}", open=10 + d, high=11 + d, low=9 + d,
                           close=10.5 + d, volume=1e6, amount=1e7) for d in range(2, 8)])


def test_the_host_page_and_the_bundled_plotly_js_exist():
    assert (kline_view.COMPONENT_DIR / "index.html").is_file()
    js = kline_view.PLOTLY_DATA_DIR / kline_view.PLOTLY_JS_NAME
    assert js.is_file() and js.stat().st_size > 1_000_000, "plotly 包里该有 plotly.min.js"


def test_plotly_js_is_served_from_the_installed_package_not_a_cdn():
    """离线也要能看图，也不许把 4.8 MB 复制进仓库：只能从已安装的 plotly 包目录提供。"""
    assert "cdn" not in INDEX.lower() and "https://" not in INDEX
    assert not any(p.suffix == ".js" for p in kline_view.COMPONENT_DIR.iterdir()), \
        "kline_component/ 里不该躺着一份复制来的 plotly.js"
    url = kline_view.plotly_js_url("kline_view.tape_plotlyjs")
    assert url == "../kline_view.tape_plotlyjs/plotly.min.js"
    assert not url.startswith("/"), "必须是相对路径：面板挂在 baseUrlPath 下时绝对路径就错了"


def test_the_host_page_speaks_the_minimal_component_protocol():
    """componentReady / render / setFrameHeight 三条消息，一条都不能少：少 ready 父页不发
    数据，少 setFrameHeight 的话 iframe 高度是 0。"""
    for token in ("streamlit:componentReady", "streamlit:render", "streamlit:setFrameHeight",
                  "isStreamlitMessage", "Plotly.react", "args.plotly_js", "args.spec",
                  "args.config"):
        assert token in INDEX, f"index.html 缺 {token}"


def test_the_host_page_explains_why_it_exists():
    """根因必须写在宿主页里：三个月后有人想"简化"回 st.plotly_chart 时能看到实测数据。"""
    assert "st.plotly_chart" in INDEX and "relayouting" in INDEX and "抽动" in INDEX


def test_spec_is_plain_json_with_data_and_layout():
    """fig.to_json 处理 numpy / 时间戳 / NaN；自己 json.dumps 会在第一个 numpy 标量上炸。"""
    fig = charts.kline_chart(_bars(), [], "T")
    spec = kline_view.spec(fig)
    json.dumps(spec)                                   # 必须可序列化
    assert [tr["type"] for tr in spec["data"]] == ["candlestick"]
    assert spec["layout"]["xaxis"]["type"] == "category"
    assert spec["layout"]["height"] == 550


def test_render_passes_the_figure_config_and_relative_js_path(monkeypatch):
    """render() 交给前端的就是这几样：spec / config（含 scrollZoom）/ 相对 js 路径 / 高度 / 底色，
    key 固定为 kline——同一个 iframe 跨重跑复用，Plotly.react 才能按 uirevision 保留缩放。"""
    calls = []
    monkeypatch.setattr(kline_view, "_components",
                        lambda: {"kline": lambda **kw: calls.append(kw),
                                 "plotly_js": "../x.tape_plotlyjs/plotly.min.js"})
    kline_view.render(charts.kline_chart(_bars(), [], "T"), config=charts.KLINE_CONFIG,
                      bg="#123456")
    assert len(calls) == 1
    kw = calls[0]
    assert kw["config"]["scrollZoom"] is True and kw["config"]["responsive"] is True
    assert kw["plotly_js"] == "../x.tape_plotlyjs/plotly.min.js"
    assert kw["height"] == 550 and kw["bg"] == "#123456" and kw["key"] == "kline"
    assert kw["default"] is None
    assert kw["spec"]["data"][0]["type"] == "candlestick"


def test_config_is_copied_not_shared():
    """前端拿到的是 KLINE_CONFIG 的副本：组件序列化时改动不许写回模块常量。"""
    calls = []
    kline_view._declared = None
    try:
        kline_view._declared = {"kline": lambda **kw: calls.append(kw), "plotly_js": "../p/plotly.min.js"}
        kline_view.render(charts.kline_chart(_bars(), [], "T"), config=charts.KLINE_CONFIG, bg="#000")
        assert calls[0]["config"] is not charts.KLINE_CONFIG
        assert calls[0]["config"] == charts.KLINE_CONFIG
    finally:
        kline_view._declared = None
