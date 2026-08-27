import shutil
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_hash

REQUIRED = ["open", "high", "low", "close", "volume", "amount"]

APP_DIR = Path(__file__).resolve().parent.parent / "app"


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
    """让 bare 模式 exec 的 dashboard.py 渲染指定页，并返回它声明的全部页面。

    返回的列表顺序就是侧栏顺序，元素带 title/icon/url_path/default，
    可直接对账导航表（见 tests/test_dashboard_nav.py）。
    """
    pages: list[FakePage] = []

    def _page(*args, **kwargs) -> FakePage:
        pages.append(FakePage(*args, **kwargs))
        return pages[-1]

    def _navigation(items, **_kwargs) -> FakePage:
        picked = [p for p in items if p.title == title]
        if not picked:
            raise AssertionError(
                f"面板里没有标题为 {title!r} 的页：{[p.title for p in items]}")
        return picked[0]

    monkeypatch.setattr(st, "Page", _page)
    monkeypatch.setattr(st, "navigation", _navigation)
    return pages


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
