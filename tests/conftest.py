import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
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
#
# v0.3.1 M1：「交易日志」拆成「记账」与「持仓与盈亏」两个子页。
# 「记账」沿用老的 journal 路径——一键记账跳的就是它，老书签也不断。
PAGE_URL_PATHS = {
    "市场概览": "home",
    "使用说明": "guide",
    # 说明手册拆出来的子页（v0.5.0 三个；2026-09-05「自定义策略」搬去 docs/）
    "读懂回测": "guide-metrics",
    "边界与安全": "guide-limits",
    "任务控制台": "console",
    "信号": "signals",
    "记账": "journal",
    "持仓与盈亏": "positions",
    "信号池": "universe",
    "回测报告": "backtest",
    "个股K线": "kline",
}


def goto_page(at: AppTest, title: str) -> AppTest:
    """把 AppTest 切到指定页并重跑。"""
    at._page_hash = calc_hash(PAGE_URL_PATHS[title])
    return at.run()


# ---------------------------------------------------------------- 按钮在哪（2026-11-16）
# 用户提的要求：操作按钮统一位置——一律靠右（表单的提交按钮在表单右下角）。
# AppTest 没有像素，但元素树里有排版：一行分栏是 flex_container（子块是 column，带权重），
# st.container(horizontal=True, horizontal_alignment="right") 是 justify=JUSTIFY_END 的
# flex_container。下面三个助手让各页的测试能说"这个按钮在右边"，而不是只说"它存在"。

def element_path(at: AppTest, key: str) -> tuple:
    """从页面根到 key 那个控件，一路经过的块（不含控件自己，最外层在前）。找不到就断言失败。"""
    def walk(node, trail):
        for child in getattr(node, "children", {}).values():
            if getattr(child, "key", None) == key:
                return (*trail, node)
            found = walk(child, (*trail, node))
            if found is not None:
                return found
        return None
    path = walk(at._tree, ())
    assert path is not None, f"页面上找不到 {key}"
    return path


def path_to_element(at: AppTest, target) -> tuple:
    """从页面根到 target 那个元素（按对象身份找，给没有 key 的元素用：报错、灰字……），
    一路经过的块，最外层在前。找不到就断言失败。"""
    def walk(node, trail):
        for child in getattr(node, "children", {}).values():
            if child is target:
                return (*trail, node)
            found = walk(child, (*trail, node))
            if found is not None:
                return found
        return None
    path = walk(at._tree, ())
    assert path is not None, "页面上找不到那个元素"
    return path


def _is_column_row(block) -> bool:
    return any(getattr(c, "type", "") == "column" for c in getattr(block, "children", {}).values())


def element_slot(at: AppTest, key: str) -> tuple:
    """key 那个控件在页面上的"位置"：从根往下每一层是第几个孩子、什么类型，最后是它自己。
    两个控件的这串东西相同，就是同一个槽位——一个消失、另一个出现时，它会落在同一个地方
    （「停止」变成「开始」的那种事故，比的就是这个）。"""
    path = element_path(at, key)
    trail = []
    for parent, child in zip(path, path[1:]):
        trail.append((list(parent.children.values()).index(child), getattr(child, "type", "?")))
    last = next(i for i, c in enumerate(path[-1].children.values())
                if getattr(c, "key", None) == key)
    return (*trail, (last, "widget"))


def sits_on_the_right(at: AppTest, key: str) -> bool:
    """key 那个按钮是不是靠在它那一块的右缘。两步都得过：

    1. **自己那一行靠右**：自里向外找到第一个"横着排东西"的块，它得是靠右对齐的横向容器
       （justify 是 END），或者按钮直接放在一行分栏里很窄的最后一栏（不超过整行的四分之一，
       按钮撑满它时也算）。竖着排的普通容器、折叠区不算数，继续往外找；**随内容宽**的横向容器
       也不算数——它只是把几个按钮捆成不拆开的一组，这一组摆在哪由它外面那层说了算。
    2. **它外面每一层分栏都是最后一栏**：靠右对齐的容器若在左边那一栏里，按钮只到那一栏的右缘，
       右边还空着大半行（2026-11-16 独立审查举的三个反例都是这一类）。
    """
    path = element_path(at, key)
    aligned_at = None
    for i in range(len(path) - 1, -1, -1):
        node = path[i]
        kind = getattr(node, "type", "")
        if kind == "column":
            columns = list(path[i - 1].children.values())
            if columns[-1] is not node:
                return False                            # 不在最后一栏：右边还有别的栏
            if aligned_at is None and i == len(path) - 1:
                weights = [c.weight for c in columns]
                if weights[-1] > sum(weights) / 4:
                    return False                        # 直接放在一个宽栏里：按钮在它的左缘
                aligned_at = i
        elif aligned_at is None and kind == "flex_container" and not _is_column_row(node):
            flex = node.proto.flex_container
            if flex.direction == flex.Direction.HORIZONTAL:
                if flex.justify == flex.Justify.JUSTIFY_END:
                    aligned_at = i
                elif not node.proto.width_config.use_content:
                    return False
    return aligned_at is not None


def action_button_keys(at: AppTest) -> list[str]:
    """页面正文里全部操作按钮（st.button 与下载按钮）的 key，按页面上的先后。
    表格每行的「＋ / −」是表格的一列，不是按钮元素，不在此列。"""
    def walk(node):
        for child in getattr(node, "children", {}).values():
            if getattr(child, "type", "") in ("button", "download_button"):
                yield child.key
            yield from walk(child)
    return [k for k in walk(at.main) if k]


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


class NavPages(list):
    """stub_navigation 的返回值：**侧栏顺序**的扁平页表，外加 `.groups`。

    v0.3.1 起 st.navigation 收的是 Mapping[组名, Sequence[Page]]（分组形态），
    但既有测试大多只关心扁平顺序，所以保持"它就是个 list"；
    要对账分组结构（组名、组内顺序）的测试读 `.groups`。
    """

    def __init__(self) -> None:
        super().__init__()
        self.groups: dict[str, list[FakePage]] = {}


def stub_navigation(monkeypatch, title: str) -> NavPages:
    """让 bare 模式 exec 的 dashboard.py 渲染指定页，并返回**侧栏顺序**的页表。

    元素带 title/icon/url_path/default，可直接对账导航表（见 tests/test_dashboard_nav.py）。

    顺序取自传给 `st.navigation` 的实参（Mapping 按组序展开），**不是** `st.Page`
    的调用顺序——两者不等价：dashboard.py 会把需要 `st.switch_page` 跳转的页
    （如「记账」）提前声明成变量再插进页表，于是它的构造调用排在最前，而侧栏位置
    在中间。v0.3.0 加交易日志时这条差异让两个顺序断言假失败过一次；
    侧栏顺序的唯一事实来源是 st.navigation 收到的那份实参。
    """
    pages = NavPages()

    def _page(*args, **kwargs) -> FakePage:
        return FakePage(*args, **kwargs)

    def _navigation(items, **_kwargs) -> FakePage:
        # dict 的插入序就是侧栏组序（streamlit 1.61.1 按此渲染）；
        # 传 list 时折成单个无名组，两种形态共用同一套断言口径。
        groups = dict(items) if isinstance(items, dict) else {"": list(items)}
        pages.groups = {name: list(members) for name, members in groups.items()}
        pages[:] = [p for members in pages.groups.values() for p in members]
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


# ---------------------------------------------------------------- 首页离线替身（Task 18）
#
# 落地页换成「市场概览」之后，**每一个** AppTest 都会先渲染这一页——所以离线替身、
# 冻结时钟、联网守卫这三件事不能只是首页自己那几条测试的事，得是全部面板测试的
# 前提，否则任何一条既有测试都可能在无意间打到真实网络或用当天真实时间。

FIXTURES_MARKET = Path(__file__).resolve().parent / "fixtures" / "market"
FROZEN_NOW = None    # 由夹具录制日决定，见 _frozen_now()


def _frozen_now():
    """冻结首页时钟：录制那天的 19:30（已收盘、过收盘截止时刻）。日期取自分时夹具的 data.date。"""
    from datetime import datetime, time
    from quant.market import sources
    m = sources.parse_tencent_minute((FIXTURES_MARKET / "tencent_minute_sh000001.bin").read_bytes(),
                                     "sh000001")
    return datetime.combine(m.trade_date, time(19, 30))


def fixture_http_get(url: str, headers=None) -> bytes:
    """首页数据源的离线替身：按 URL 片段找 tests/fixtures/market/ 里的录制文件。"""
    from quant.market.sources import FetchError
    if "minute/query?code=" in url:
        return (FIXTURES_MARKET / f"tencent_minute_{url.rsplit('=', 1)[1]}.bin").read_bytes()
    if "newfqkline" in url:
        return (FIXTURES_MARKET / f"tencent_kline_{url.split('param=')[1].split(',')[0]}.bin").read_bytes()
    table = {"qt.gtimg.cn/q=": "tencent_quotes.bin", "hq.sinajs.cn": "sina_quotes.bin",
             "getTopicZDFenBu": "em_fenbu.bin", "getTopicZTPool": "em_ztpool.bin",
             "getTopicZBPool": "em_zbpool.bin"}
    for frag, name in table.items():
        if frag in url:
            return (FIXTURES_MARKET / name).read_bytes()
    raise FetchError(f"离线替身没有这个 URL: {url}")


@pytest.fixture(autouse=True)
def keep_the_main_module(monkeypatch):
    """每条测试结束后把 `sys.modules["__main__"]` 换回原样。

    AppTest 跑脚本时会把 `__main__` 换成那份脚本，跑完**不换回来**。而用 spawn 起子进程的测试
    （tests/test_journal_store.py 里两个进程同时写日志那条）在子进程里会先把父进程的 `__main__`
    按文件路径重跑一遍：前一条 AppTest 留下的若是一份整页面板脚本，子进程就白跑一遍面板；
    留下的若是 `AppTest.from_function` 的临时脚本（跑完就没了），子进程直接以退出码 1 结束，
    那条测试红得莫名其妙——2026-11-16 给 conftest 的排版助手加自测时撞上的，与排在它前面的
    是哪条测试有关，单跑永远是绿的。测试之间不该靠这种残留互相影响。
    """
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])


@pytest.fixture(autouse=True)
def offline_market(request, monkeypatch):
    """每个 AppTest 都会先渲染落地页「市场概览」，所以这四件事是全部面板测试的前提：
    1) 取数走离线替身；2) 时钟冻结在录制日收盘后；3) 清 st.cache_data 与最近成功值；
    4) 非 network 测试里任何真实联网直接失败。"""
    from quant import market
    from quant.market import live
    import requests.adapters

    monkeypatch.setattr(live, "HTTP_GET", fixture_http_get)
    frozen = _frozen_now()
    monkeypatch.setattr(market, "now", lambda: frozen)
    if request.node.get_closest_marker("network") is None:
        def _blocked(self, req, *a, **k):
            raise AssertionError(f"测试里发生了真实联网: {req.url}")
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _blocked)
    st.cache_data.clear()
    live.STORE.reset()
    yield
    st.cache_data.clear()
    live.STORE.reset()
