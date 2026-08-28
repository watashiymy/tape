import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

REQUIRED = ["open", "high", "low", "close", "volume", "amount"]

APP_DIR = Path(__file__).resolve().parent.parent / "app"
# 面板自 v0.2.2 M3 起拆成"装配 + 共享件 + 每页一个模块"（设计 §4）。
# 源码级断言（废弃 API、st.code 的 height、magic 裸三元、写死文案）必须扫**全部**
# 这些文件：只扫 dashboard.py 的话，页面代码搬走之后那些断言全部变成空跑。
APP_FILES = sorted(APP_DIR.glob("*.py"))


def copy_app(tmp_path: Path) -> Path:
    """把整个 app/ 目录复制到 tmp_path/app，返回复制后的 dashboard.py 路径。

    复制而不是直接跑仓库里那份：dashboard.py 的 ROOT 是从 __file__ 推出来的，
    复制后 OUTPUT / RUNS_DIR 全落在 tmp_path，与仓库真实产物完全隔离
    （否则测试会读到、甚至停掉真任务）。
    必须**整目录**复制：dashboard.py 自 v0.2.1 起 `import theme`（同目录的
    app/theme.py），只复制 dashboard.py 一个文件会 ModuleNotFoundError。
    """
    app_dir = tmp_path / "app"
    shutil.copytree(APP_DIR, app_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return app_dir / "dashboard.py"


# ---------------------------------------------------------------- 切页（v0.2.2 M1）
# 导航自 v0.2.2 起是 st.navigation + st.Page，v0.1 的 st.sidebar.radio 已经删掉，
# 所以过去那句 `at.sidebar.radio[0].set_value(page)` 全部换成这里的两个助手。
#
# 页面路由靠 url_path 的哈希（Page._script_hash == calc_hash(url_path)），
# AppTest 只有 `_page_hash` 这一个入口（公开的 switch_page 只认 pages/ 目录下的
# **文件**，本项目是单文件多函数，用不了）。这张表是测试侧的副本，
# test_dashboard_nav.py 有一条测试拿面板里真实的 st.Page 声明与它对账。
PAGE_URL_PATHS = {
    "使用说明": "guide",
    "任务控制台": "console",
    "今日信号": "signals",
    "交易日志": "journal",
    "信号池": "universe",
    "回测报告": "backtest",
    "个股K线": "kline",
}


def goto_page(at: AppTest, title: str) -> AppTest:
    """把 AppTest 切到指定页并重跑。"""
    at._page_hash = calc_hash(PAGE_URL_PATHS[title])
    return at.run()


class FakePage:
    """`st.Page` 的测试替身，只在 **bare 模式**（importlib 直接 exec dashboard.py）用。

    真 Page 在没有 ScriptRunContext 时会提前 return：既不记标题也不记页面函数，
    `run()` 直接返回什么都不画——那样的 exec 探针测不到任何东西。
    这个替身把声明原样记下来，`run()` 直接调页面函数。
    """

    def __init__(self, page, *, title=None, icon=None, url_path=None,
                 default=False, visibility="visible"):
        self.page = page
        self.title = title
        self.icon = icon
        self.url_path = url_path
        self.default = default
        self.visibility = visibility

    def run(self) -> None:
        self.page()


def stub_navigation(monkeypatch, title: str) -> list[FakePage]:
    """让 bare 模式 exec 的 dashboard.py 渲染指定页，并返回**侧栏顺序**的页表。

    元素带 title/icon/url_path/default，可直接对账导航表（见 tests/test_dashboard_nav.py）。

    顺序取自传给 `st.navigation` 的列表，**不是** `st.Page` 的调用顺序——两者不等价：
    dashboard.py 会把需要 `st.switch_page` 跳转的页（如「交易日志」）提前声明成变量
    再插进 PAGES，于是它的构造调用排在最前，而侧栏位置在中间。v0.3.0 加交易日志时
    这条差异让两个顺序断言假失败过一次；侧栏顺序的唯一事实来源是 st.navigation 收到的列表。
    """
    pages: list[FakePage] = []

    def _page(*args, **kwargs) -> FakePage:
        return FakePage(*args, **kwargs)

    def _navigation(items, **_kwargs) -> FakePage:
        pages[:] = list(items)          # 就地替换：调用方拿到的是同一个列表对象
        picked = [p for p in pages if p.title == title]
        if not picked:
            raise AssertionError(
                f"面板里没有标题为 {title!r} 的页：{[p.title for p in pages]}")
        return picked[0]

    monkeypatch.setattr(st, "Page", _page)
    monkeypatch.setattr(st, "navigation", _navigation)
    return pages


def app_module(name: str):
    """取 bare 模式下 dashboard.py 连带 import 进来的某个 app 模块（如 "ui"）。

    必须**先** exec 过 dashboard.py（stub_navigation + importlib）：面板自 v0.2.2 M3
    起把页面拆到 app/pages_*.py、共享件拆到 app/ui.py，而路径（ROOT/OUTPUT/RUNS_DIR）
    由 dashboard.py 每轮调 `ui.bind(ROOT)` 钉住——绕过 dashboard.py 直接 import ui
    会拿到上一个测试留在 sys.modules 里的那份（它的 __file__ 指向别的 tmp 目录），
    于是断言悄悄读错目录。理由详见 app/ui.py 的模块 docstring。
    """
    mod = sys.modules.get(name)
    assert mod is not None, f"app 模块 {name!r} 还没被加载：先 exec 一遍 dashboard.py"
    return mod


def click_row_button(at: AppTest, column: str, row: int, label: str,
                     *, dataframe: int = 0) -> AppTest:
    """点表格里 `st.column_config.ButtonColumn` 的某一行按钮（v0.2.2 M3）。

    AppTest 没给这种按钮公开的 `.click()`：它注册的是 `string_trigger_value` 类型的
    widget，widget id 挂在 dataframe 元素的 `proto.button_click_widgets[列名]` 上。
    这里照前端的格式塞一条 WidgetState（`{"row": int, "label": str}`，格式由
    streamlit 的 ButtonClickSerde.deserialize 校验），于是 on_click 回调真的被调用
    ——只有这样才能端到端验证"点 − 真的改了 settings.yaml"。
    """
    proto = at.get("dataframe")[dataframe].proto
    assert column in proto.button_click_widgets, \
        f"第 {dataframe} 张表没有按钮列 {column!r}：{dict(proto.button_click_widgets)}"
    states = at._tree.get_widget_states()
    state: WidgetState = states.widgets.add()
    state.id = proto.button_click_widgets[column]
    state.string_trigger_value.data = json.dumps({"row": row, "label": label})
    return at._run(widget_state=states)


def edit_table(at: AppTest, edits: dict[int, dict], *, dataframe: int = 0,
               click: str | None = None) -> AppTest:
    """改 `st.data_editor` 里的几格（v0.3.0 M3），可顺带按下一个按钮。

    AppTest 没给 data_editor 公开入口：它在元素树里就是个普通 Dataframe，只是
    `proto.id` 非空（那就是 widget id）。这里照前端的格式塞一条 WidgetState
    （`{"edited_rows": {行号: {列: 值}}, ...}`），于是"改一格 + 点保存 → 文件真的
    变了"是端到端验证的，不是只验了一个纯函数。

    **`click` 必须与编辑在同一次注入里**：AppTest 不认识 data_editor 这个 widget，
    因此下一次 `run()` 不会把它的状态带过去（实测：编辑会在点按钮那一轮丢失，
    而断言"文件没变"照样通过——一个静默失败的测试）。
    """
    element = at.get("dataframe")[dataframe]
    assert element.proto.id, \
        f"第 {dataframe} 张表不是 st.data_editor（proto.id 为空），改不动"
    states = at._tree.get_widget_states()
    state: WidgetState = states.widgets.add()
    state.id = element.proto.id
    state.string_value = json.dumps({
        "edited_rows": {str(row): values for row, values in edits.items()},
        "added_rows": [], "deleted_rows": [],
    })
    if click is not None:
        pressed: WidgetState = states.widgets.add()
        pressed.id = at.button(key=click).id
        pressed.trigger_value = True
    return at._run(widget_state=states)


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """手工构造日线 DataFrame。rows 每项须含 date + REQUIRED 全部列，
    可选 adj_factor（默认1.0）/trade_status（默认1）/is_st（默认0）。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    for col, default in [("adj_factor", 1.0), ("trade_status", 1), ("is_st", 0)]:
        if col not in df.columns:
            df[col] = default
        else:
            # 关键：只有部分行显式给了该列时，pandas 会把其余行填成 NaN。
            # 少了 fillna，"只给一行 trade_status=0"的用例会让全部行都不等于 1 而被过滤光；
            # 少了 astype，dtype 会变成 float，与线上永远是 int 的形态不符
            # （断言 [1, 0] 察觉不到，因为 1.0 == 1）。
            df[col] = df[col].fillna(default).astype(type(default))
    # 必填列漏写只会得到一列 NaN，而 NaN 参与比较恒为 False：
    # 例如 Task 7 的成交额过滤会静默不出信号，测试还"通过"——测的却是错的东西。
    missing = [c for c in REQUIRED if c not in df.columns or df[c].isna().any()]
    if missing:
        raise ValueError(f"make_bars: 必填列缺失或含 NaN: {missing}")
    return df
