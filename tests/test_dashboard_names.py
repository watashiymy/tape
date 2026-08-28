# tests/test_dashboard_names.py — 面板「名称」列的两个离线来源与合并优先级（v0.2.3）
#
# 背景（用户真实数据坐实）：名称此前唯一的离线来源是扫描 CSV，而扫描只写**出了信号**
# 的标的——5 份扫描文件累计只记了 313 只，每轮却扫 3010 只。用户 8 只持仓里有 5 只
# 从没出过信号，于是信号池表格的「名称」列大半空缺。
#
# 修法：全市场清单落盘（data/symbols.parquet，含每只票的 name），面板优先读它。
# 这个文件钉住三件事：
#   1. 合并优先级（清单有 / CSV 有 / 两者都有取谁 / 两者都无）；
#   2. 缺名显示 —（fmt.MISSING）而不是空白——空白像 bug，— 是"暂无"；
#   3. 降级：没有清单文件时行为与从前完全一致；清单文件坏了也不许把页面打没。
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from quant.data.symbols import save_symbols
from quant.report import fmt
from tests.conftest import app_module, copy_app, goto_page, stub_navigation

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "config" / "settings.yaml"
PAGE = "信号池"
SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"


def _config(tmp_path: Path, universe=("600519", "000333")) -> Path:
    """tmp_path 里的一份真实配置副本（只换 universe）。绝不碰仓库里那份。"""
    from quant.config_edit import replace_universe_block
    path = tmp_path / "config" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        replace_universe_block(REAL_CONFIG.read_text(encoding="utf-8"), universe),
        encoding="utf-8")
    return path


def _listing(tmp_path: Path, rows, as_of=date(2026, 8, 26)) -> Path:
    path = tmp_path / "data" / "symbols.parquet"
    save_symbols(pd.DataFrame(list(rows), columns=["symbol", "name"]), as_of, path)
    return path


def _scan_csv(tmp_path: Path, rows: str, day: str = "2026-08-27") -> Path:
    path = tmp_path / "output" / "scan" / f"{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCAN_HEADER + rows, encoding="utf-8")
    return path


def _ui(tmp_path: Path, monkeypatch, page: str = PAGE):
    """exec 一遍 tmp_path 里那份 dashboard.py，取回它加载的 app/ui.py 模块。

    必须走 dashboard.py（而不是直接 import ui）：路径由它每轮 `ui.bind(ROOT)` 钉住，
    绕过去会拿到上一个测试留在 sys.modules 里的那份，于是读到别的目录还照样"通过"。
    """
    stub_navigation(monkeypatch, page)
    spec = importlib.util.spec_from_file_location(f"dash_names_{tmp_path.name}",
                                                  copy_app(tmp_path))
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    return app_module("ui")


def _at(tmp_path: Path) -> AppTest:
    return goto_page(
        AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(), PAGE)


def _table(at: AppTest, index: int = 0) -> pd.DataFrame:
    value = at.get("dataframe")[index].value
    return getattr(value, "data", value)


# ================================================================ 合并优先级

def test_names_come_from_the_local_listing(tmp_path, monkeypatch):
    """清单覆盖全市场（约 3000 只），这是本次修的主路径：
    从没出过信号的标的（扫描 CSV 里根本没有它）现在也有名字了。"""
    _config(tmp_path)
    _listing(tmp_path, [("002078", "太阳纸业"), ("600519", "贵州茅台")])

    names = _ui(tmp_path, monkeypatch).symbol_names()

    assert names["002078"] == "太阳纸业"
    assert names["600519"] == "贵州茅台"


def test_names_still_come_from_the_scan_csv(tmp_path, monkeypatch):
    """扫描 CSV 这个来源不能丢：清单是**筛过**的（主板、非 ST、上市满 400 天），
    后来变成 ST 的票会从清单里消失，但它可能还在用户的信号池里、也还在老扫描文件里。"""
    _config(tmp_path)
    _listing(tmp_path, [("600519", "贵州茅台")])
    _scan_csv(tmp_path, "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")

    names = _ui(tmp_path, monkeypatch).symbol_names()

    assert names["000333"] == "美的集团", "清单里没有的票，CSV 这个补充来源必须还管用"
    assert names["600519"] == "贵州茅台"


def test_the_scan_csv_wins_when_the_two_sources_disagree(tmp_path, monkeypatch):
    """冲突取 CSV：它记的是"扫描那天实际看到的名字"，而清单文件按 is_fresh 的窗口
    最多可以滞后 7 天。正常情况下两者恒等（扫描写 CSV 用的就是当轮那份清单），
    这条规则只在清单明显更旧时才起作用——但必须是**确定**的一条，不能碰运气。"""
    _config(tmp_path)
    _listing(tmp_path, [("000333", "美的集团")])
    _scan_csv(tmp_path, "2026-08-27,000333,美的新名,ma_cross,72.5,3.1,1200000000,2.4\n")

    assert _ui(tmp_path, monkeypatch).symbol_names()["000333"] == "美的新名"


def test_a_symbol_in_neither_source_has_no_name(tmp_path, monkeypatch):
    """两个来源都没有 → 字典里就没有这个键，由渲染层显示 —（见下面那条）。
    绝不许编一个"未知"塞进去：那看着像个名字。"""
    _config(tmp_path)
    _listing(tmp_path, [("600519", "贵州茅台")])

    assert "002078" not in _ui(tmp_path, monkeypatch).symbol_names()


def test_the_newest_scan_csv_still_wins_among_csvs(tmp_path, monkeypatch):
    """既有行为（改过名的取最近一份）不许被这次改动弄丢。"""
    _config(tmp_path)
    _scan_csv(tmp_path, "2026-08-20,000333,旧名字,ma_cross,72.5,3.1,1200000000,2.4\n",
              day="2026-08-20")
    _scan_csv(tmp_path, "2026-08-27,000333,新名字,ma_cross,72.5,3.1,1200000000,2.4\n",
              day="2026-08-27")

    assert _ui(tmp_path, monkeypatch).symbol_names()["000333"] == "新名字"


# ================================================================ 降级

def test_no_listing_file_behaves_exactly_like_before(tmp_path, monkeypatch):
    """降级第一条：没有 data/symbols.parquet（还没跑过扫描）时，
    名称仍从扫描 CSV 来，一个异常都不许抛。"""
    _config(tmp_path)
    _scan_csv(tmp_path, "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")

    assert not (tmp_path / "data" / "symbols.parquet").exists()
    assert _ui(tmp_path, monkeypatch).symbol_names() == {"000333": "美的集团"}


def test_a_corrupt_listing_file_does_not_take_the_page_down(tmp_path, monkeypatch):
    """降级第二条：清单文件坏了（拉了 2-4 分钟写到一半被 Ctrl-C）。

    命令行那边 load_symbols 会**响亮**抛错（那里必须响亮：静默会退化成每轮重拉）；
    但面板这边名称只是"看得舒服一点"的东西，不该让信号池页整页打不开
    ——那是唯一能把误加的票删掉的地方。所以这里接住、退回扫描 CSV，
    并把路径与自愈办法如实说出来。
    """
    _config(tmp_path)
    path = _listing(tmp_path, [("600519", "贵州茅台")])
    path.write_bytes(path.read_bytes()[:20])
    _scan_csv(tmp_path, "2026-08-27,000333,美的集团,ma_cross,72.5,3.1,1200000000,2.4\n")

    at = _at(tmp_path)

    assert not at.exception, at.exception
    said = "\n".join(w.value for w in at.main.warning)
    assert "symbols.parquet" in said, f"没说清是哪个文件坏了：{said}"
    assert list(_table(at)["名称"]) == [fmt.MISSING, "美的集团"], \
        "坏清单必须退回扫描 CSV 这个来源，而不是整列变空"


# ================================================================ 页面上的样子

def test_the_pool_table_shows_a_dash_for_a_missing_name(tmp_path, monkeypatch):
    """空白像 bug（"是不是没加载出来？"），— 是明确的"暂无"。
    fmt.MISSING 就是本项目统一的缺值符号，最新价那一列已经在用它。"""
    _config(tmp_path, ("600519", "002078"))
    _listing(tmp_path, [("600519", "贵州茅台")])

    at = _at(tmp_path)

    assert not at.exception, at.exception
    assert list(_table(at)["名称"]) == ["贵州茅台", fmt.MISSING]
    assert "" not in list(_table(at)["名称"]), "缺名不许留空白"


def test_the_universe_page_names_every_holding_once_the_listing_is_local(tmp_path):
    """本次任务的验收形态：用户那 8 只票有 5 只从没出过信号（扫描 CSV 里没有它们），
    清单落盘之后必须**全部**有名字。"""
    holdings = [("002078", "太阳纸业"), ("001337", "四川黄金"), ("002920", "德赛西威"),
                ("002859", "洁美科技"), ("600584", "长电科技"), ("601138", "工业富联"),
                ("600869", "远东股份"), ("002241", "歌尔股份")]
    _config(tmp_path, tuple(s for s, _ in holdings))
    _listing(tmp_path, holdings)

    at = _at(tmp_path)

    assert not at.exception, at.exception
    assert list(_table(at)["名称"]) == [n for _, n in holdings]


def test_the_universe_page_still_works_without_any_listing_file(tmp_path):
    """降级在页面层再钉一遍：没有清单文件时这页照常打开，名称列显示 —。"""
    _config(tmp_path, ("600519", "002078"))

    at = _at(tmp_path)

    assert not at.exception, at.exception
    assert list(_table(at)["名称"]) == [fmt.MISSING, fmt.MISSING]


# ================================================================ 路径纪律

def test_the_listing_path_is_bound_to_the_dashboard_root(tmp_path, monkeypatch):
    """SYMBOLS_PATH 必须跟着 ui.bind() 走，否则面板会去读**仓库真实的**
    data/symbols.parquet——测试于是在读线上数据，断言还照样通过。"""
    _config(tmp_path)
    ui = _ui(tmp_path, monkeypatch)
    assert ui.SYMBOLS_PATH == tmp_path / "data" / "symbols.parquet"


@pytest.fixture(autouse=True)
def _clear_cache():
    """面板里的清单读取可能走 st.cache_data，而缓存是进程级的。"""
    st.cache_data.clear()
    yield
    st.cache_data.clear()
