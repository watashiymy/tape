# tests/test_dashboard_journal.py — v0.3.0 M3「交易日志」页（设计 §5）
#
# 这一页与面板其余五页有一个根本差别：**它会写一份不可再生的文件**。
# 其余页面最坏的后果是显示错了，重跑一次就好；这里最坏的后果是用户真实的交易记录
# 被改错或删错，而那是找不回来的（只能靠 git）。所以断言的重心是三件事：
#
#   1. **写盘的内容对**：费用按成本模型自动算（与回测同口径）、名称自动带出、
#      前导零不丢；阻断项一律不落盘（文件逐字节不变）。
#   2. **警告不阻断、但看得见**：价格越界/卖超照记，页面上说得出来。
#      这是设计 §3 的原则——日志的首要职责是如实记录发生了什么。
#   3. **所见即所得**：筛完再导出，导出的就是屏幕上那些行；而筛选**不**影响
#      持仓与盈亏（那两块必须反映全部记录，否则用户会看到一个筛出来的假持仓）。
#
# 编辑区走 st.data_editor。AppTest 没给它公开入口，conftest.edit_table 照前端格式
# 塞一条 WidgetState（{"edited_rows": ...}），于是"改一格 + 点保存 → 文件真的变了"
# 是端到端验证的。注意：AppTest 不会把 data_editor 的状态带到下一次 run，
# 所以编辑与点按钮必须在**同一次**注入里发生（helper 已经这么做）。
#
# 删除走每行最前面的按钮 + 确认框（2026-11-18，见下面的 _delete）。
import base64
import errno
import importlib.util
import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.testing.v1 import AppTest

from quant.backtest.costs import commission, stamp_tax
from quant.config import load_settings
from quant.journal import export, schema, store
from tests.conftest import (action_button_keys, app_module, click_row_button, copy_app,
                            edit_table, element_path, element_slot, goto_page, make_bars,
                            path_to_element, sits_on_the_right)

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "config" / "settings.yaml"
# v0.3.1 M1：「交易日志」拆成两个子页（设计 §1）。录入/历史/导出在「记账」，
# 持仓/盈亏/来源在「持仓与盈亏」——下面的测试按这条分界各找各的页。
PAGE = "记账"
REPORT_PAGE = "持仓与盈亏"
SIGNALS_PAGE = "信号"
COSTS = load_settings(REAL_CONFIG).costs

SCAN_HEADER = "date,symbol,name,strategy,close,pct_chg,amount,amount_ratio_20d\n"
SIGNAL_HEADER = "date,symbol,strategy,action,close\n"

TODAY = date.today()
D_BUY = date(2026, 8, 3)
D_SELL = date(2026, 8, 24)


def _load_journal_ui():
    """单独加载 app/journal_ui.py（日志页的编排层：控件 key、表格、校验编排）。

    与 test_dashboard_universe 加载 pool.py 同一手法，也是同一个理由：
    这个模块刻意**不 import 任何 app 内部模块**（ui/theme/guide），路径一律由调用方
    传进来，所以它能脱离 Streamlit 运行时被直接加载——而"改错/删错用户的交易记录"
    是本功能最贵的故障，那部分逻辑必须能单独测。
    """
    spec = importlib.util.spec_from_file_location("qd_journal_ui_probe",
                                                  ROOT / "app" / "journal_ui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qd_journal_ui_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


jui = _load_journal_ui()


# ---------------------------------------------------------------- 夹具

def _root(tmp_path: Path) -> Path:
    """一个能让日志页正常工作的临时仓库根：真配置（成本模型）+ 空的 journal/。"""
    config = tmp_path / "config" / "settings.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL_CONFIG, config)      # 只读地用真配置：成本模型必须是真的
    return tmp_path


def _trades_path(tmp_path: Path) -> Path:
    return tmp_path / "journal" / "trades.csv"


def _journal(tmp_path: Path, *rows) -> Path:
    """在临时根里落一份日志。行走 schema.apply_defaults，与真实录入同一条路。"""
    records = []
    for i, row in enumerate(rows, 1):
        record = schema.apply_defaults(row, costs=COSTS)
        record["trade_id"] = f"T{i}"
        records.append(record)
    path = _trades_path(tmp_path)
    store.save_trades(pd.DataFrame(records, columns=list(schema.COLUMNS)), path)
    return path


def _buy(**over) -> dict:
    row = dict(date=D_BUY, symbol="000333", name="美的集团", kind="buy",
               shares=1000.0, price=71.5, source="ma_cross", reason="20日线金叉")
    row.update(over)
    return row


def _sell(**over) -> dict:
    row = dict(date=D_SELL, symbol="000333", kind="sell", shares=1000.0, price=75.0,
               source="ma_cross", reason="跌破20日线")
    row.update(over)
    return row


def _cache(tmp_path: Path, symbol: str = "000333", *, low=70.0, high=73.0,
           close=72.0, day: date = D_BUY) -> None:
    """给价格区间校验与持仓最新价喂一份最小可用缓存。"""
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    make_bars([
        {"date": str(day - timedelta(days=1)), "open": low, "high": high, "low": low,
         "close": low, "volume": 1000, "amount": 1e7},
        {"date": str(day), "open": low, "high": high, "low": low, "close": close,
         "volume": 1000, "amount": 1e7},
    ]).to_parquet(cache / f"{symbol}.parquet")


def _page(tmp_path: Path, page: str = PAGE) -> AppTest:
    return goto_page(
        AppTest.from_file(str(copy_app(tmp_path)), default_timeout=30).run(), page)


def _texts(at: AppTest) -> str:
    """页面上所有能读到的文字（markdown / caption / 提示 / 我们自己包的 HTML）。"""
    parts = [e.proto.body for e in at.get("html")]
    for kind in ("markdown", "caption", "warning", "error", "info", "success", "text"):
        parts += [getattr(e, "value", "") or "" for e in at.get(kind)]
    return "\n".join(str(p) for p in parts)


def _frames(at: AppTest) -> list[pd.DataFrame]:
    """页面上每张表喂进去的数据（st.dataframe / st.data_editor 都是 Dataframe 元素）。"""
    return [e.value for e in at.get("dataframe")]


# ================================================================ 三态渲染（设计 §7）
# v0.3.1 M1：两个子页 × 空日志 / 有数据 / 有一致性告警，先保证一个组合都不崩，
# 再对"该显示什么"逐页做针对性断言（各自的下面几条）。

_STATES = {
    "空日志": lambda root: None,
    "有数据": lambda root: _journal(root, _buy(), _sell()),
    "有一致性告警": lambda root: _journal(root, _buy(shares=100.0),
                                    _sell(shares=500.0)),
}


@pytest.mark.parametrize("state", list(_STATES))
@pytest.mark.parametrize("page", [PAGE, REPORT_PAGE])
def test_each_subpage_renders_every_journal_state(tmp_path, page, state):
    root = _root(tmp_path)
    _STATES[state](root)
    at = _page(root, page)
    assert not at.exception, f"{page} × {state} 抛异常: {at.exception}"


@pytest.mark.parametrize("page", [PAGE, REPORT_PAGE])
def test_an_empty_journal_says_so_in_words(tmp_path, page):
    """第一次打开面板时日志是空的——这是每个新用户的第一屏，不许假装有数据。"""
    at = _page(_root(tmp_path), page)
    assert not at.exception, at.exception
    assert "还没有" in _texts(at) or "还没记过" in _texts(at), _texts(at)


@pytest.mark.parametrize("page", [PAGE, REPORT_PAGE])
def test_empty_journal_shows_no_fabricated_zero_pnl(tmp_path, page):
    """空日志的已实现盈亏是"没有"，不是 0.00。
    0 会被读成"我不赚不亏"，而实情是一笔都还没记。"""
    at = _page(_root(tmp_path), page)
    assert not at.exception, at.exception
    text = _texts(at)
    assert "0.00" not in text, f"空日志页面上出现了编出来的 0：{text}"


def test_journal_with_data_renders_positions_and_pnl(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    _cache(root, close=76.0, day=D_SELL)
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    text = _texts(at)
    assert "美的集团" in text or any("000333" in str(f.values) for f in _frames(at))


def test_realized_pnl_matches_the_hand_computed_number(tmp_path):
    """手算对照（费用一律走 backtest/costs.py，与回测同口径）：

        买入 1000 股 @ 71.50 → 名义 71,500，佣金 max(71500*0.00025, 5) = 17.88
        成本基 = 71,500 + 17.88 = 71,517.88
        卖出 1000 股 @ 75.00 → 名义 75,000，佣金 18.75，印花税 75,000*0.0005 = 37.50
        净得 = 75,000 − 18.75 − 37.50 = 74,943.75
        已实现盈亏 = 74,943.75 − 71,517.88 = **3,425.87**
    """
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    assert "3,425.87" in _texts(at), _texts(at)


def _notices(at: AppTest) -> list:
    """页面上「要你看一眼」的那几块（theme.notice）。一块里可以摞着几句。"""
    return [e for e in at.get("html") if 'class="qd-notice"' in e.proto.body
            and not e.proto.body.lstrip().startswith("<style>")]


def _notice_texts(at: AppTest) -> str:
    return "\n".join(e.proto.body for e in _notices(at))


def _links(at: AppTest) -> dict[str, str]:
    """页面正文里的链接：{链接上的字: 去哪一页}。页头那个「帮助中心」不算——它每页都有。"""
    return {el.proto.label: el.proto.page for el in at.main.get("page_link")
            if "帮助中心" not in el.proto.label}


def test_oversell_warning_is_visible_and_does_not_break_the_page(tmp_path):
    """卖超（漏记了买入）：设计 §3 要求在持仓页**持续可见**，但不阻断、不崩页。
    2026-11-18 起它是一行点名哪只票的话（下面几条细钉），不再是不说是谁的黄框。"""
    root = _root(tmp_path)
    _journal(root, _buy(shares=100.0), _sell(shares=500.0))
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    text = _notice_texts(at)
    assert "美的集团（000333）" in text and str(D_SELL) in text, text
    assert "卖出 500 股" in text and "那时手里只有 100 股" in text, text


def test_a_row_the_engine_cannot_read_is_reported_not_swallowed(tmp_path):
    """用户会用 Excel 手改这份 CSV。一行看不懂时跳过 + 标记，
    但必须在页面上说出来——静默跳过等于给一个少算了一笔的盈亏。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    text = path.read_text(encoding="utf-8-sig").replace(",1000.0,71.5,", ",,,", 1)
    path.write_text("﻿" + text, encoding="utf-8")
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    assert "跳过" in _texts(at), _texts(at)


def test_the_report_page_tucks_the_three_tables_into_tabs(tmp_path):
    """v0.3.1 §1 的排版验收：持仓 / 平仓明细 / 来源对比收进 st.tabs，
    不再纵向全部铺开（原页五块堆一屏正是这次拆页要治的拥挤）。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    labels = [t.label for t in at.get("tab")]
    assert labels == ["当前持仓", "平仓明细", "来源对比"], labels


# ================================================================ 仪表盘（v0.3.1 §2，M2）
# 指标块 2×3 + 两图并排（曲线/占比），来源对比图在来源 tab 里。
# 铁律有两条：缺市价的标的**不按 0 计入**（如实显示 — 并注明有几只没算进来）；
# 新图零类别配色（那是 test_charts.py 钉的，这里钉页面接线与数字本身）。

# 开着一半仓位的日志：买 1000 卖 500，剩 500 股在手（仪表盘要有市值可算）。
# 手算（真配置成本模型，同 test_realized_pnl_matches_the_hand_computed_number）：
#   买 1000 @ 71.50 → 成本基 71,500 + 17.88 = 71,517.88
#   卖  500 @ 75.00 → 名义 37,500，佣金 9.38，印花税 18.75，净得 37,471.87
#     消耗成本 71,517.88 × 500/1000 = 35,758.94 → 已实现 **1,712.93**
#   缓存最新价 76.00 → 市值 76 × 500 = **38,000.00**
#     浮动 = 38,000 − 35,758.94 = **2,241.06**
#     总盈亏 = 1,712.93 + 2,241.06 = **3,953.99**

def _half_closed(root: Path) -> None:
    _journal(root, _buy(), _sell(shares=500.0))
    _cache(root, close=76.0, day=D_SELL)


def test_dashboard_shows_the_six_hand_computed_metrics(tmp_path):
    root = _root(tmp_path)
    _half_closed(root)
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    blob = "".join(e.proto.body for e in at.get("html"))
    for label, value in [("已实现总收益（含分红）", "1,712.93"),
                         ("当前持仓市值", "38,000.00"),
                         ("浮动盈亏", "2,241.06"),
                         ("总盈亏（已实现+浮动）", "3,953.99")]:
        assert f">{label}</div>" in blob, f"缺指标卡 {label}"
        assert value in blob, f"{label} 的值不对（该是 {value}）：{blob}"
    assert ">胜率</div>" in blob and ">盈亏比</div>" in blob


def test_dashboard_renders_everything_when_there_is_data(tmp_path):
    """仪表盘里是累计盈亏曲线与持仓占比，来源对比图在「来源对比」tab 里。曲线与来源对比是 plotly 图；
    持仓占比 2026-11-18 起是页面自己的 HTML（占比条 / 环形图二选一）。"""
    root = _root(tmp_path)
    _half_closed(root)
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 2, "该有曲线与来源对比两张 plotly 图"
    assert len(_weights_html(at)) == 1, "该有一块持仓占比"


def test_unpriced_positions_show_dash_and_an_honest_note(tmp_path):
    """没有本地缓存的持仓：市值/浮动/总盈亏显示 —（不按 0 编），
    并注明有几只没算进来——静默漏项会让看的人以为市值就这么点。"""
    root = _root(tmp_path)
    _journal(root, _buy())          # 只有买入，无缓存 → 1 只持仓、无市价
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    blob = "".join(e.proto.body for e in at.get("html"))
    for label in ("当前持仓市值", "浮动盈亏", "总盈亏（已实现+浮动）"):
        assert f'>{label}</div><div class="qd-metric-value">—<' in blob, \
            f"{label} 缺市价时该显示 —：{blob}"
    text = _texts(at)
    assert "1 只" in text and "无市价" in text, text


def test_empty_journal_dashboard_offers_guidance_not_an_empty_chart(tmp_path):
    """空态（§2.2）：不画空图，显示引导文案。"""
    at = _page(_root(tmp_path), REPORT_PAGE)
    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 0, "空日志不该画任何图"
    assert [c.value for c in at.main.caption] == [_guide().JOURNAL_NO_CURVE], \
        [c.value for c in at.main.caption]


def test_the_page_admits_why_it_is_yuan_not_percent(tmp_path):
    """设计 §2.6：日志不记本金与出入金，收益率分母只能编造——
    这条取舍必须写在页面上，否则一定有人问"为什么不是收益率曲线"。"""
    root = _root(tmp_path)
    _half_closed(root)
    at = _page(root, REPORT_PAGE)
    assert "本金" in _texts(at), _texts(at)


def test_dashboard_metrics_hand_computed_with_a_price_gap():
    """dashboard_metrics 的纯函数口径（页面只是转述它）。手算：
    A 500 股 @ 76.00 → 市值 38,000.00，成本 35,758.94 → 浮动 2,241.06；
    B 100 股无市价 → **不计入**市值与浮动（不按 0 也不按成本顶进指标卡）。
    已实现 1,712.93 → 总盈亏 = 1,712.93 + 2,241.06 = 3,953.99。"""
    from quant.journal.pnl import Position

    summary = {"total_realized": 1712.93, "realized_pnl": 1712.93, "dividends": 0.0,
               "n_trades": 1, "win_rate": 1.0, "profit_factor": None,
               "avg_holding_days": 21.0}
    positions = (Position("000333", "美的集团", 500.0, 35758.94, 71.5179),
                 Position("600519", "贵州茅台", 100.0, 160000.0, 1600.0))
    metrics, unpriced = jui.dashboard_metrics(
        summary, positions, {"000333": 76.0, "600519": float("nan")})

    assert unpriced == 1
    by_label = {label: (text, color) for label, text, color in metrics}
    assert by_label["已实现总收益（含分红）"] == ("1,712.93", jui.fmt.UP)
    assert by_label["当前持仓市值"][0] == "38,000.00"
    assert by_label["浮动盈亏"] == ("2,241.06", jui.fmt.UP)
    assert by_label["总盈亏（已实现+浮动）"] == ("3,953.99", jui.fmt.UP)
    assert by_label["胜率"][0] == "100.00%"
    assert by_label["盈亏比"][0] == "—"


def test_dashboard_metrics_all_dashes_when_nothing_is_known():
    """空日志：六项全是 —，一个 0.00 都不许有（0 会被读成"不赚不亏"）。"""
    from quant.journal.pnl import compute_pnl

    report = compute_pnl(store.empty_trades())
    metrics, unpriced = jui.dashboard_metrics(report.summary, report.positions, {})
    assert unpriced == 0
    assert [text for _l, text, _c in metrics] == ["—"] * 6
    assert all(color is None for _l, _t, color in metrics)


def test_dashboard_metrics_hide_the_total_when_floating_is_unknowable():
    """有持仓但全都没市价：浮动是"算不出来"，总盈亏也必须是 —。
    拿"已实现"顶给"总盈亏"等于宣称浮动为 0——那正是本页明令禁止的编数。"""
    from quant.journal.pnl import Position

    summary = {"total_realized": 1712.93, "realized_pnl": 1712.93, "dividends": 0.0,
               "n_trades": 1, "win_rate": 1.0, "profit_factor": None,
               "avg_holding_days": 21.0}
    positions = (Position("000333", "美的集团", 500.0, 35758.94, 71.5179),)
    metrics, unpriced = jui.dashboard_metrics(summary, positions, {})

    assert unpriced == 1
    by_label = {label: text for label, text, _c in metrics}
    assert by_label["已实现总收益（含分红）"] == "1,712.93"
    assert by_label["当前持仓市值"] == "—"
    assert by_label["浮动盈亏"] == "—"
    assert by_label["总盈亏（已实现+浮动）"] == "—"


def _walk(node):
    """AppTest 元素树递归展开（顶层只给容器，卡片里的控件要自己走下去）。"""
    for child in getattr(node, "children", {}).values():
        yield child
        yield from _walk(child)


def test_the_entry_page_keeps_the_form_in_a_card(tmp_path):
    """v0.3.1 §1 的排版验收：录入表单占一个卡片区（带边框容器），
    历史表留在卡片之外全宽铺开。

    st.container(border=True) 在 1.61.1 的元素树里是 flex_container 块、
    边框标志在 proto.flex_container.border（实测，vertical.border 恒为 False）。
    只断言"有个带框容器"不够——还要钉住录入按钮在框内、日志编辑表在框外，
    不然把整页包进一个框也能通过。
    """
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _page(root)
    assert not at.exception, at.exception
    cards = [n for n in _walk(at._tree)
             if getattr(n, "type", "") == "flex_container"
             and n.proto.flex_container.border]
    assert len(cards) == 1, f"录入表单应恰好一个卡片容器，实际 {len(cards)} 个"
    inside = list(_walk(cards[0]))
    assert any(getattr(e, "key", None) == jui.SUBMIT_KEY for e in inside), \
        "「记这一笔」不在卡片里"
    # 2026-11-16：卡片里只有一张表单、一个主按钮——送股与分红那张表单在卡片下面，不在里面
    assert not any(getattr(e, "key", None) == jui.OTHER_SUBMIT_KEY for e in inside), \
        "「其他记录」不该再挤在录入卡片里"
    assert at.button(key=jui.OTHER_SUBMIT_KEY), "「其他记录」的入口不能丢"
    submit = at.button(key=jui.SUBMIT_KEY)
    assert (submit.label, submit.proto.icon) == ("记这一笔", ":material/add:"), \
        "按钮的图标走 icon=，不拼在文字前面（2026-11-16）"
    editors = [e for e in _walk(at._tree)
               if getattr(e, "type", "") == "dataframe" and e.proto.id]
    assert editors, "找不到日志编辑表"
    assert not any(e is i for e in editors for i in inside), "历史表不该被收进卡片"


# ================================================================ 录入（设计 §5.1）

def _fill_entry(at: AppTest, *, symbol="000333", shares=1000.0, price=71.5,
                day: date = D_BUY, kind="buy", reason="20日线金叉") -> AppTest:
    at.button_group(key=jui.KIND_KEY).set_value(kind)
    at.date_input(key=jui.DATE_KEY).set_value(day)
    at.text_input(key=jui.SYMBOL_KEY).set_value(symbol)
    at = at.run()
    at.number_input(key=jui.SHARES_KEY).set_value(shares)
    at.number_input(key=jui.PRICE_KEY).set_value(price)
    at.text_input(key=jui.REASON_KEY).set_value(reason)
    return at.run()


def test_recording_a_trade_writes_one_row_with_computed_costs(tmp_path):
    """端到端：填表 → 点记录 → journal/trades.csv 真的多了一行。

    手算对照：名义 71,500，佣金 max(71500*0.00025, 5.0) = 17.88，买入印花税 0。
    """
    root = _root(tmp_path)
    _cache(root)
    at = _fill_entry(_page(root))
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    saved = store.load_trades(_trades_path(root))
    assert len(saved) == 1, saved
    row = saved.iloc[0]
    assert row["symbol"] == "000333"           # 前导零活着
    assert row["kind"] == "buy"
    assert row["amount"] == 71500.0
    assert row["fee"] == round(commission(71500.0, COSTS), 2) == 17.88
    assert row["tax"] == 0.0
    assert row["trade_id"], "落盘的行必须带 store 发的主键"


def test_a_recorded_sell_gets_the_stamp_tax_of_its_trade_date(tmp_path):
    """印花税按**成交日**取税率（2023-08-28 起千分之零点五）。
    手算：75,000 × 0.0005 = 37.50。用今天的税率算历史成交是个永不报错的错数字。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _fill_entry(_page(root), kind="sell", shares=1000.0, price=75.0, day=D_SELL,
                     reason="跌破20日线")
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    saved = store.load_trades(_trades_path(root))
    sell = saved[saved["kind"] == "sell"].iloc[0]
    assert sell["tax"] == round(stamp_tax(75000.0, D_SELL, COSTS), 2) == 37.5


def test_the_name_and_the_price_range_are_filled_in_from_local_data(tmp_path):
    """设计 §5.1：输入代码后自动带出名称与该日 [low, high]。
    两者都只用本地数据（symbols.parquet / data/cache），离线照样有。"""
    root = _root(tmp_path)
    _cache(root, low=70.0, high=73.0)
    (root / "output" / "scan").mkdir(parents=True, exist_ok=True)
    (root / "output" / "scan" / "2026-08-03.csv").write_text(
        SCAN_HEADER + "2026-08-03,000333,美的集团,ma_cross,71.5,1.2,3e8,1.8\n",
        encoding="utf-8")
    at = _fill_entry(_page(root))

    text = _texts(at)
    assert "美的集团" in text, text
    assert "70.00" in text and "73.00" in text, f"没带出当日价格区间：{text}"


def test_the_fee_input_is_prefilled_by_the_cost_model(tmp_path):
    """设计 §5.1："费用自动按成本模型算好可改"。

    预填而不是留空：留空时用户不知道该填什么，而随手填 0 会让盈亏系统性偏高。
    手算：1000 股 × 71.50 = 71,500 → 佣金 17.88。
    """
    at = _fill_entry(_page(_root(tmp_path)))
    assert at.number_input(key=jui.FEE_KEY).value == 17.88
    assert at.number_input(key=jui.TAX_KEY).value == 0.0


def test_changing_the_price_recomputes_the_prefilled_fee(tmp_path):
    """改了价格费用就得跟着重算。留着上一次的数字是最难发现的一种错：
    表格里那一行看着完全正常。手算：1000 × 100.00 = 100,000 → 佣金 25.00。"""
    at = _fill_entry(_page(_root(tmp_path)))
    at.number_input(key=jui.PRICE_KEY).set_value(100.0)
    at = at.run()
    assert at.number_input(key=jui.FEE_KEY).value == 25.0


def test_the_date_picker_cannot_even_reach_the_future(tmp_path):
    """唯一的阻断项（设计 §3）是未来日期。录入表单干脆让它**选不出来**：
    上限钉成今天，比让用户填完一屏再被拒好。

    （校验层的阻断仍然在——编辑区可以手打日期，见下面那条。）
    """
    root = _root(tmp_path)
    at = _fill_entry(_page(root))
    assert at.date_input(key=jui.DATE_KEY).proto.max == TODAY.isoformat()

    at.date_input(key=jui.DATE_KEY).set_value(TODAY + timedelta(days=1))
    at = at.run()
    assert not at.exception, at.exception
    assert at.date_input(key=jui.DATE_KEY).value == TODAY, "居然选出了明天"


def test_old_trades_can_still_be_recorded(tmp_path):
    """日期下限不能用 Streamlit 的默认值（value 往前十年）：那会让"补记 2014 年那笔"
    直接选不出日期，而且没有任何提示——一个静默的功能缺口。"""
    at = _fill_entry(_page(_root(tmp_path)))
    assert at.date_input(key=jui.DATE_KEY).proto.min == jui.EARLIEST_DAY.isoformat()


def test_editing_a_date_into_the_future_is_refused(tmp_path):
    """编辑区可以手打日期，所以阻断必须在那里生效，且文件**一个字节都不许动**。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)
    at = edit_table(at, {0: {"date": str(TODAY + timedelta(days=1))}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    assert path.read_bytes() == before, "未来日期竟然落盘了"
    assert "晚于今天" in "".join(str(e.value) for e in at.error), \
        [str(e.value) for e in at.error]


def test_a_price_outside_the_day_range_is_recorded_with_a_warning(tmp_path):
    """本功能最实用的一条：71.5 打成 715 当场看得出来。

    但它是**警告不是阻断**：可能是缓存里没这只票，也可能是盘后大宗。
    这是日志——首要职责是如实记录发生了什么，不是替用户否定现实。
    """
    root = _root(tmp_path)
    _cache(root, low=70.0, high=73.0)
    at = _fill_entry(_page(root), price=715.0)
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    assert len(store.load_trades(_trades_path(root))) == 1, "警告不该阻断落盘"
    warnings = "\n".join(str(w.value) for w in at.warning)
    assert "区间" in warnings and "715" in warnings, warnings


def test_recording_without_shares_is_refused(tmp_path):
    """没有股数的买入不是"现实被否定"，而是一张没填完的表单。
    放行等于把 NaN 交给 FIFO，得到一个看着正常的错数字。"""
    root = _root(tmp_path)
    at = _page(root)
    at.date_input(key=jui.DATE_KEY).set_value(D_BUY)
    at.text_input(key=jui.SYMBOL_KEY).set_value("000333")
    at = at.run()
    at.number_input(key=jui.PRICE_KEY).set_value(71.5)
    at = at.run()
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    assert "".join(str(e.value) for e in at.error)
    assert not _trades_path(root).exists()


def test_other_records_can_be_entered_too(tmp_path):
    """送股与分红必须有录入口（设计 §2.2）：没有它们，持仓会与券商对不上、
    此后每一笔卖出的 FIFO 配对全错——而且不会报错。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _page(root)
    at.button_group(key=jui.OTHER_KIND_KEY).set_value("dividend")
    at.date_input(key=jui.OTHER_DATE_KEY).set_value(D_SELL)
    at.text_input(key=jui.OTHER_SYMBOL_KEY).set_value("000333")
    at = at.run()
    at.number_input(key=jui.OTHER_AMOUNT_KEY).set_value(320.0)
    at = at.run()
    at = at.button(key=jui.OTHER_SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    saved = store.load_trades(_trades_path(root))
    assert list(saved["kind"]) == ["buy", "dividend"], saved
    assert saved.iloc[1]["amount"] == 320.0


def test_an_unreadable_config_disables_only_the_entry_form(tmp_path):
    """成本模型来自 settings.yaml。读不到时**不许**拿 0 顶包记账（那会污染不可再生的
    日志），但已经记下的日志、持仓、盈亏与导出都不依赖它，必须照常可用。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    (root / "config" / "settings.yaml").write_text("universe: [", encoding="utf-8")
    at = _page(root)

    assert not at.exception, at.exception
    assert not [b for b in at.button if b.key == jui.SUBMIT_KEY], "配置坏了还能录入"
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])]
    assert shown, "配置坏了不该连日志表一起藏起来"

    at = goto_page(at, REPORT_PAGE)
    assert not at.exception, at.exception
    assert "3,425.87" in _texts(at), "配置坏了不该连盈亏一起藏起来"


# ================================================ 备份兜底（v0.3.2 §3；2026-09-05 改成按钮）
#
# 日志移出版本控制之后，"误删一行用 git 找回"这条路没了。替代品是每次写盘前
# 自动另存的上一版。**用户必须知道它存在**——一个没人知道的备份等于没有备份。
# v0.3.2 的做法是把备份文件名写在页面上，让人自己去文件系统里换；2026-09-05 起
# 页面上是一个「↶ 恢复上一版」按钮（勾确认再点，交换语义），不再要求使用者
# 认识 .bak 文件——"易用的功能"替代"知道路径"。

def test_the_entry_page_offers_restore_instead_of_naming_the_backup_file(tmp_path):
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    store.save_trades(store.load_trades(path), path)     # 造出一份备份
    at = _page(root)

    assert not at.exception, at.exception
    text = _texts(at)
    assert "trades.csv.bak" not in text, f"记账页还在要求使用者认识备份文件：{text}"
    # 「恢复上一版」这个按钮自己就是告示（2026-11-16 第二轮之前另有一行常驻的灰字讲它）；
    # 怎么用、只留几版，在它的悬停说明里。
    button = at.button(key=jui.RESTORE_KEY)
    assert (button.label, button.proto.icon) == ("恢复上一版", ":material/undo:")
    assert button.proto.help == _guide().JOURNAL_RESTORE_HELP and "只留一版" in button.proto.help
    assert ".bak" not in button.proto.help
    assert button.disabled, "没勾确认之前按钮必须是禁用的（两步纪律，同删除）"
    assert not at.checkbox(key=jui.RESTORE_CONFIRM_KEY).disabled


def test_the_restore_button_is_disabled_and_explained_before_the_first_save(tmp_path):
    """一笔都没记过、也没有任何备份的时候：按钮在（让人知道有这回事），但禁用，
    tooltip 说清为什么——而不是点下去报错。"""
    root = _root(tmp_path)
    at = _page(root)

    assert not at.exception, at.exception
    button = at.button(key=jui.RESTORE_KEY)
    assert button.label == "恢复上一版" and button.disabled
    assert "第一次保存" in button.proto.help, button.proto.help
    assert at.checkbox(key=jui.RESTORE_CONFIRM_KEY).disabled


def test_restoring_swaps_the_journal_with_its_backup_end_to_end(tmp_path):
    """端到端：删一行并保存（造出备份）→ 勾确认 → 点恢复 → 日志回到两行、
    备份里躺着刚才那一行的版本；确认框复位；再来一次就换回去。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    two_rows = path.read_bytes()
    at = _page(root)
    at = _delete(at, 1)
    assert len(store.load_trades(path)) == 1
    one_row = path.read_bytes()

    at.checkbox(key=jui.RESTORE_CONFIRM_KEY).check().run()
    assert not at.button(key=jui.RESTORE_KEY).disabled
    at.button(key=jui.RESTORE_KEY).click().run()

    assert not at.exception, at.exception
    assert path.read_bytes() == two_rows, "没换回上一版"
    assert store.backup_path(path).read_bytes() == one_row, "刚才那版该成为新的备份"
    # 成功走 st.toast（journal_ui.show_flash 的 ok 档），不在 _texts 里
    assert any("已恢复上一版" in t.value for t in at.toast), [t.value for t in at.toast]
    assert not at.checkbox(key=jui.RESTORE_CONFIRM_KEY).value, "确认框没复位，手一抖就再换一次"
    assert at.button(key=jui.RESTORE_KEY).disabled

    at.checkbox(key=jui.RESTORE_CONFIRM_KEY).check().run()
    at.button(key=jui.RESTORE_KEY).click().run()
    assert path.read_bytes() == one_row, "再点一次应该换回来"
    assert store.backup_path(path).read_bytes() == two_rows


def test_deleting_really_leaves_a_backup_behind(tmp_path):
    """端到端：在页面上删掉一条 → 上一版仍然躺在 .bak 里。
    这条走的是最危险的那条路（整表重写），也是确认框里那句「删错了可以换回」承诺的东西。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)

    at = _delete(at, 1)

    assert not at.exception, at.exception
    assert len(store.load_trades(path)) == 1, "删除没生效，这条测的就不是备份了"
    assert store.backup_path(path).read_bytes() == before
    assert len(store.load_trades(store.backup_path(path))) == 2


# ================================================================ 筛选与导出（§5.2 / §5.5）

def _spy_downloads(monkeypatch) -> list[dict]:
    """把两个下载按钮的 data 截下来。DownloadButton 的 proto 只带一个媒体 URL，
    字节拿不到，而"导出的是当前筛选结果"这条只有看字节才算验过。"""
    captured: list[dict] = []

    def _download_button(label, data, file_name=None, mime=None, **kwargs):
        captured.append({"label": label, "data": data, "file_name": file_name,
                         "mime": mime, "key": kwargs.get("key")})
        return False

    monkeypatch.setattr(st, "download_button", _download_button)
    return captured


def test_both_export_buttons_are_offered(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)

    keys = {e.proto.id.split("-")[-1] for e in at.get("download_button")}
    assert {jui.CSV_KEY, jui.XLSX_KEY} <= keys, keys


def test_the_exported_csv_carries_the_bom(tmp_path, monkeypatch):
    """设计 §5.5 点名的坑，字节断言。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    captured = _spy_downloads(monkeypatch)
    at = _page(root)

    assert not at.exception, at.exception
    csv = [c for c in captured if c["key"] == jui.CSV_KEY]
    assert len(csv) == 1, captured
    assert csv[0]["data"][:3] == b"\xef\xbb\xbf", csv[0]["data"][:8]
    assert csv[0]["file_name"].endswith(".csv")
    assert csv[0]["mime"] == export.CSV_MIME


def test_the_export_contains_exactly_the_filtered_rows(tmp_path, monkeypatch):
    """所见即所得（设计 §5.5）。筛掉的那笔不许出现在导出里，
    否则用户按季度导出去报税/复盘，拿到的是整本日志。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(), _buy(symbol="600519", name="贵州茅台",
                                        shares=100.0, price=1600.0,
                                        source="discretionary", reason="跌到心理价位"))
    captured = _spy_downloads(monkeypatch)
    at = _page(root)
    at.multiselect(key=jui.FILTER_SYMBOL_KEY).set_value(["600519"])
    at = at.run()

    assert not at.exception, at.exception
    csv = [c for c in captured if c["key"] == jui.CSV_KEY][-1]
    text = csv["data"].decode("utf-8-sig")
    assert "600519" in text and "000333" not in text, text
    xlsx = [c for c in captured if c["key"] == jui.XLSX_KEY][-1]
    assert xlsx["data"][:2] == b"PK", "xlsx 不是一个 zip 容器"
    assert xlsx["mime"] == export.EXCEL_MIME


def test_filtering_narrows_the_log_table(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at = at.run()

    assert not at.exception, at.exception
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])]
    assert shown, "找不到日志表"
    assert list(shown[0]["trade_id"]) == ["T2"], shown[0]


def test_filtering_does_not_change_positions_or_pnl(tmp_path):
    """筛选只作用于日志表与导出。持仓与盈亏必须反映**全部**记录——
    按日期筛一下就看到一个不存在的持仓，是最容易让人做错决定的显示错误。
    拆页后筛选条件留在 session_state 里，所以要真的带着筛选切过去看一眼。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["buy"])
    at = at.run()
    assert not at.exception, at.exception

    at = goto_page(at, REPORT_PAGE)
    assert not at.exception, at.exception
    assert "3,425.87" in _texts(at), "只筛出买入之后，已实现盈亏被算成了别的数"


def test_reason_keyword_filter_is_wired_up(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.text_input(key=jui.FILTER_REASON_KEY).set_value("跌破")
    at = at.run()

    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])]
    assert list(shown[0]["trade_id"]) == ["T2"], shown[0]


# ================================================================ 编辑与删除（§5.2）

def _log_table_index(at: AppTest) -> int:
    """日志编辑区在第几张表（它是页面上唯一可编辑的那张：proto.id 非空）。"""
    editable = [i for i, e in enumerate(at.get("dataframe")) if e.proto.id]
    assert editable, "日志表不是 st.data_editor（没有可编辑的表）"
    return editable[0]


def test_editing_a_cell_and_saving_rewrites_that_row_only(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at = edit_table(at, {0: {"reason": "改成别的理由"}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    saved = store.load_trades(_trades_path(root))
    assert list(saved["reason"]) == ["改成别的理由", "跌破20日线"], saved


def _resend_edits(at: AppTest, states, edits: dict[int, dict] | None) -> None:
    """表里没保存的改动：浏览器每一轮都会把它们原样送回来，AppTest 不会，所以要自己带上。"""
    if not edits:
        return
    table: WidgetState = states.widgets.add()
    table.id = at.get("dataframe")[_log_table_index(at)].proto.id
    table.string_value = json.dumps({
        "edited_rows": {str(r): cells for r, cells in edits.items()},
        "added_rows": [], "deleted_rows": []})


def _delete_states(at: AppTest, row: int, edits: dict[int, dict] | None = None):
    """点历史表第 row 行删除按钮那一下的全部控件状态。"""
    editor = at.get("dataframe")[_log_table_index(at)]
    assert jui.DELETE_COLUMN in editor.proto.button_click_widgets, \
        f"历史表没有删除按钮那一列: {dict(editor.proto.button_click_widgets)}"
    states = at._tree.get_widget_states()
    _resend_edits(at, states, edits)
    click: WidgetState = states.widgets.add()
    click.id = editor.proto.button_click_widgets[jui.DELETE_COLUMN]
    click.string_trigger_value.data = json.dumps({"row": row, "label": jui.DELETE_LABEL})
    return states


def _click_delete(at: AppTest, row: int, *, edits: dict[int, dict] | None = None) -> AppTest:
    """点历史表第 row 行最前面的删除按钮：确认框出来，别的什么都还没发生。"""
    return at._run(widget_state=_delete_states(at, row, edits))


def _answer(at: AppTest, confirm: bool = True, *, edits: dict[int, dict] | None = None) -> AppTest:
    """在开着的确认框里点「删除」（confirm=False 时点「取消」）。

    真实浏览器里这一下只重跑确认框那一块；AppTest 只会整页重跑。确认框从点了删除按钮起一直挂着、
    每一轮都在页尾重开，所以整页重跑时框里的按钮照样接得到这一下——走到的是同一段代码。"""
    assert _dialog(at) is not None, "确认框没开，没有按钮可点"
    states = at._tree.get_widget_states()
    _resend_edits(at, states, edits)
    pressed: WidgetState = states.widgets.add()
    pressed.id = at.button(key=jui.DELETE_OK_KEY if confirm else jui.DELETE_CANCEL_KEY).id
    pressed.trigger_value = True
    return at._run(widget_state=states)


def _delete(at: AppTest, row: int, *, edits: dict[int, dict] | None = None,
            confirm: bool = True) -> AppTest:
    """点删除按钮，再在确认框里点「删除」（confirm=False 时点「取消」）——两下各是一轮。"""
    at = _click_delete(at, row, edits=edits)
    assert not at.exception, at.exception
    return _answer(at, confirm, edits=edits)


def _dismiss(at: AppTest) -> AppTest:
    """从右上角 × / Esc / 点框外把确认框关掉（前端把这件事当成一次点击送回后台）。"""
    box = _dialog(at)
    assert box is not None and box.proto.dialog.id, "确认框没开，或者关掉它不会通知后台"
    states = at._tree.get_widget_states()
    closed: WidgetState = states.widgets.add()
    closed.id = box.proto.dialog.id
    closed.trigger_value = True
    return at._run(widget_state=states)


def _dialog(at: AppTest):
    """页面上开着的确认框（没有就是 None）。它不在页面正文里：streamlit 把它挂在另一棵树上。"""
    found = [n for n in _walk(at._tree) if getattr(n, "type", "") == "dialog"]
    assert len(found) <= 1, f"同时开着 {len(found)} 个确认框"
    return found[0] if found else None


def _dialog_text(at: AppTest) -> str:
    """确认框里能读到的全部文字（不折行的空格换成普通空格，比字面用）。"""
    box = _dialog(at)
    assert box is not None, "确认框没开"
    parts = []
    for e in _walk(box):
        parts.append(getattr(getattr(e, "proto", None), "body", "") or "")
        if getattr(e, "type", "") in ("markdown", "caption"):
            parts.append(str(e.value))
    return "\n".join(parts).replace(NBSP, " ")


def test_deleting_a_row_asks_first_and_removes_it_on_confirm(tmp_path):
    """删除是两步，但两步都叫删除：点那一行的删除按钮 → 确认框 → 点「删除」。
    一键删除对一份不可再生的文件太危险（误点一下就没了）；改之前的两步是"勾一下 + 点保存"，
    用户的话：很奇怪的交互。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)

    at = _click_delete(at, 1)
    assert not at.exception, at.exception
    assert _dialog(at) is not None, "点了删除按钮，确认框没出来"
    assert path.read_bytes() == before, "还没确认，文件一个字节都不许动"

    at = _delete(at, 1)
    assert not at.exception, at.exception
    saved = store.load_trades(path)
    assert list(saved["trade_id"]) == ["T1"], saved
    assert _dialog(at) is None, "删完了确认框还开着"
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T1"], "表里还摆着删掉的那一条"


def test_cancelling_the_confirmation_changes_nothing(tmp_path):
    """点了删除按钮又点「取消」→ 文件一个字节都不许动，确认框收起来。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _delete(_page(root), 1, confirm=False)

    assert not at.exception, at.exception
    assert path.read_bytes() == before
    assert not store.backup_path(path).exists(), "没删成，却写了一份备份"
    assert _dialog(at) is None, "取消之后确认框还开着"


def test_saving_an_edit_that_breaks_a_row_is_refused(tmp_path):
    """编辑区里把股数清空 → 拒写。落盘的话 FIFO 会静默少算一笔。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)
    at = edit_table(at, {0: {"shares": None}}, dataframe=_log_table_index(at),
                    click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    assert path.read_bytes() == before, "坏行竟然落盘了"
    assert "".join(str(e.value) for e in at.error), "拒写了却没说为什么"


def test_the_editor_keeps_the_trade_id_visible_but_not_editable(tmp_path):
    """主键是编辑/删除的唯一定位依据，改得动就等于换了一笔交易的身份。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _page(root)
    editor = at.get("dataframe")[_log_table_index(at)]
    assert "trade_id" in list(editor.value.columns)
    # 禁用状态落在 proto.columns 那串 JSON 里（Arrow proto 没有单独的字段）。
    assert json.loads(editor.proto.columns)["trade_id"]["disabled"] is True, \
        editor.proto.columns


# ================================================================ 从信号一键记账（§5.1）

def _signals(root: Path, rows: str) -> None:
    out = root / "output" / "signals"
    out.mkdir(parents=True, exist_ok=True)
    (out / "2026-08-24.csv").write_text(SIGNAL_HEADER + rows, encoding="utf-8")


def _scan(root: Path, rows: str) -> None:
    out = root / "output" / "scan"
    out.mkdir(parents=True, exist_ok=True)
    (out / "2026-08-24.csv").write_text(SCAN_HEADER + rows, encoding="utf-8")


def test_the_signal_table_offers_a_record_button(tmp_path):
    root = _root(tmp_path)
    _signals(root, "2026-08-24,000333,ma_cross,buy,71.5\n")
    at = _page(root, SIGNALS_PAGE)

    assert not at.exception, at.exception
    proto = at.get("dataframe")[0].proto
    assert jui.RECORD_COLUMN in dict(proto.button_click_widgets), \
        dict(proto.button_click_widgets)


def test_the_scan_table_keeps_the_pool_button_and_gains_the_record_one(tmp_path):
    """扫描表已经有一个「＋ 加入信号池」。记账按钮是**新增一列**，
    不许顶掉原来那个——那是 v0.2.2 的闭环上最短的一条路。"""
    root = _root(tmp_path)
    _scan(root, "2026-08-24,000333,美的集团,ma_cross,71.5,1.2,3e8,1.8\n")
    at = _page(root, SIGNALS_PAGE)

    assert not at.exception, at.exception
    columns = dict(at.get("dataframe")[0].proto.button_click_widgets)
    assert jui.RECORD_COLUMN in columns and "信号池" in columns, columns


def test_recording_from_a_signal_jumps_to_the_journal_with_the_form_prefilled(tmp_path):
    """设计 §5.1 的闭环：发现信号 → 决策 → 留痕。

    预填 代码/名称/日期/方向/来源，**不预填价格**：信号那天的收盘价不是用户的
    成交价，填上去就是一个看着正常的错数字。
    """
    root = _root(tmp_path)
    _signals(root, "2026-08-24,000333,ma_cross,buy,71.5\n")
    (root / "output" / "scan").mkdir(parents=True, exist_ok=True)
    (root / "output" / "scan" / "2026-08-24.csv").write_text(
        SCAN_HEADER + "2026-08-24,000333,美的集团,ma_cross,71.5,1.2,3e8,1.8\n",
        encoding="utf-8")
    at = _page(root, SIGNALS_PAGE)
    at = click_row_button(at, jui.RECORD_COLUMN, 0, jui.RECORD_LABEL)

    assert not at.exception, at.exception
    heads = [e.proto.body for e in at.get("html") if 'class="qd-head"' in e.proto.body]
    assert any(f'class="qd-title">{PAGE}<' in h for h in heads), \
        f"点了「记一笔」没跳到日志页：{heads}"
    assert at.text_input(key=jui.SYMBOL_KEY).value == "000333"
    assert at.date_input(key=jui.DATE_KEY).value == date(2026, 8, 24)
    assert at.button_group(key=jui.KIND_KEY).value == "buy"
    assert at.selectbox(key=jui.SOURCE_KEY).value == "ma_cross"
    assert "美的集团" in _texts(at)
    assert at.number_input(key=jui.PRICE_KEY).value is None, "价格不该被预填"


def test_recording_a_sell_signal_prefills_the_sell_direction(tmp_path):
    root = _root(tmp_path)
    _signals(root, "2026-08-24,000333,donchian,sell,71.5\n")
    at = _page(root, SIGNALS_PAGE)
    at = click_row_button(at, jui.RECORD_COLUMN, 0, jui.RECORD_LABEL)

    assert not at.exception, at.exception
    assert at.button_group(key=jui.KIND_KEY).value == "sell"
    assert at.selectbox(key=jui.SOURCE_KEY).value == "donchian"


def test_recording_from_the_scan_table_prefills_the_key_not_the_display_name(tmp_path):
    """扫描表的「＋ 记一笔」：表里**显示**「双均线交叉」，预填的 source 仍是键。

    两半必须在同一条测试里断言——它们就是 v0.4.0 M1 那条不变量的两面：显示换名、
    数据存键。只钉住显示的话，把 pages_signals 里的
    `_record_column(df, ...)` 写成 `_record_column(fmt.map_strategy_labels(df), ...)`
    照样全绿：预填拿到的 strategy 是「双均线交叉」，它不在 schema.SOURCES 里，
    于是 prefills 的兜底把它降级成 source="other" 写进**用户真实的** trades.csv。
    页面不报错、数字也正常，只有「按来源对比谁更赚钱」那块会静默丢掉所有信号来源的
    交易——本项目 MEMORY 里记的那类静默失败。

    夹具刻意只放扫描 CSV、不放 signals/：这样扫描表就是本页第一张表（dataframe=0）。
    对称的信号表路径由上面两条钉住（那边的夹具里 dataframe[0] 是信号表）。
    """
    root = _root(tmp_path)
    _scan(root, "2026-08-24,000333,美的集团,ma_cross,71.5,1.2,3e8,1.8\n")
    at = _page(root, SIGNALS_PAGE)
    assert not at.exception, at.exception
    shown = _frames(at)[0]["strategy"].tolist()
    at = click_row_button(at, jui.RECORD_COLUMN, 0, jui.RECORD_LABEL, dataframe=0)

    assert not at.exception, at.exception
    assert shown == ["双均线交叉"], f"扫描表的策略列该显示中文名：{shown}"
    assert at.selectbox(key=jui.SOURCE_KEY).value == "ma_cross", \
        "预填的 source 必须是内部键：显示名不在 schema.SOURCES 里，会被兜底成 other"
    assert at.text_input(key=jui.SYMBOL_KEY).value == "000333"


def test_the_scan_table_still_prefills_the_key_when_the_pool_config_is_broken(tmp_path):
    """扫描表的降级分支（读不到信号池配置时那一路）也必须预填键。

    这是同一条不变量的**第二个**调用点：配置读不出来时扫描表照常显示、照常带
    「＋ 记一笔」（它只写 journal/trades.csv，不依赖配置），于是它也能把中文显示名
    写进用户真实的日志。两个分支各写一次 `_record_column`，只钉住一个的话另一个
    改坏了不会红。

    这一路上录入表单本身是关着的（拿不到成本模型就不给录入，免得把 0 元费用写进
    日志），所以探针落在 **session_state** 上——`apply_prefill()` 就在那句
    caption 之前把预填写进 session_state，那才是"存进去的是什么"的第一手证据。

    v0.5.0 之前这条读的是 caption 里的文字，而那句话现在显示**中文来源名**
    （内部键甩到用户脸上是另一个体验问题，已改）。显示与存储从此分开：
    这条只管存储，别再拿显示文字当探针。
    """
    root = _root(tmp_path)
    # 坏在**本地池子**文件上：settings.yaml 本身是好的，坏文件不许被悄悄绕过
    (root / "config" / "universe.local.yaml").write_text(
        "universe: 这不是列表\n", encoding="utf-8")
    _scan(root, "2026-08-24,000333,美的集团,ma_cross,71.5,1.2,3e8,1.8\n")
    at = _page(root, SIGNALS_PAGE)

    assert not at.exception, at.exception
    assert "读不到信号池配置" in _texts(at), "这条测的是降级分支，得先确认真走了那一路"
    columns = dict(at.get("dataframe")[0].proto.button_click_widgets)
    assert jui.RECORD_COLUMN in columns and "信号池" not in columns, columns
    assert _frames(at)[0]["strategy"].tolist() == ["双均线交叉"]

    at = click_row_button(at, jui.RECORD_COLUMN, 0, jui.RECORD_LABEL, dataframe=0)
    assert not at.exception, at.exception
    assert at.session_state[jui.SOURCE_KEY] == "ma_cross", \
        (f"预填的 source 必须是内部键，实际 {at.session_state.get(jui.SOURCE_KEY)!r}："
         f"显示名不在 schema.SOURCES 里，会被兜底成 other")
    # 显示层则该是中文（同一份预填的两面）
    assert "已按「双均线交叉信号" in _texts(at), _texts(at)[:200]


# ================================================================ 纯函数（可脱离 Streamlit 测）

def test_prefills_map_action_and_strategy_onto_the_journal_fields():
    df = pd.DataFrame({"date": ["2026-08-24", "2026-08-24"],
                       "symbol": ["000333", "600519"],
                       "strategy": ["ma_cross", "donchian"],
                       "action": ["buy", "sell"]})
    got = jui.prefills(df, {"000333": "美的集团"})

    assert got[0] == {"symbol": "000333", "name": "美的集团",
                      "date": date(2026, 8, 24), "kind": "buy", "source": "ma_cross"}
    assert got[1]["kind"] == "sell" and got[1]["source"] == "donchian"
    assert got[1]["name"] == "", "查不到名称就留空，不许编一个"


def test_prefills_of_an_unknown_strategy_fall_back_to_other():
    """来源必须落在 schema.SOURCES 里（选择框只认这四个）。
    将来加了新策略而这里没更新时，退到 other 比让选择框崩掉好。"""
    df = pd.DataFrame({"date": ["2026-08-24"], "symbol": ["000333"],
                       "strategy": ["turtle"], "action": ["buy"]})
    assert jui.prefills(df)[0]["source"] == "other"


def test_prefills_default_to_buy_when_there_is_no_action_column():
    """全市场扫描只报当日新触发的 BUY，它的 CSV 没有 action 列。"""
    df = pd.DataFrame({"date": ["2026-08-24"], "symbol": ["000333"],
                       "name": ["美的集团"], "strategy": ["ma_cross"]})
    got = jui.prefills(df)[0]
    assert got["kind"] == "buy" and got["name"] == "美的集团"


NBSP = "\u00a0"       # 不折行的空格


def _record(**over) -> dict:
    """一行归一过的记录（同日志文件里读出来的样子），给"把一笔写成一行字"那几条用。"""
    return schema.apply_defaults(_buy(**over), costs=COSTS)


def _said(row: dict) -> str:
    """那一行字，把不折行的空格换成普通空格——比字面用。"""
    return jui.describe_trade(row).replace(NBSP, " ")


def test_a_trade_is_described_in_one_line_a_person_can_check():
    """确认框里要写明删的是**哪一笔**：哪天、买还是卖、哪只票、多少股、什么价。
    这一行是防删错的最后一道——只写「确定删除吗」的确认框，点的人什么都核对不了。"""
    said = _said(_record())
    for part in ("2026-08-03", "买入", "美的集团（000333）", "1,000 股 × 71.50"):
        assert part in said, said
    sold = _said(schema.apply_defaults(_sell(name="美的集团"), costs=COSTS))
    assert "卖出" in sold and "75.00" in sold, sold
    for key in ("buy", "sell", "ma_cross"):
        assert key not in said + sold, f"确认框里冒出了内部键 {key!r}"


def test_a_described_trade_never_breaks_inside_a_number():
    """「1,000 股 ×」与价钱拆在两行上很难读（真机上看到的：确认框窄，正好从乘号后面折）。
    每一段里面用不折行的空格，段与段之间才许折。"""
    said = jui.describe_trade(_record())
    assert f"1,000{NBSP}股{NBSP}×{NBSP}71.50" in said, repr(said)
    assert " " in said, "段与段之间得留着能折行的普通空格"


def test_adjustments_and_dividends_are_described_by_what_they_carry():
    """送股没有成交价、分红没有股数：各写各有的那个数，不写「0 股 × 0.00」。"""
    def said(**row) -> str:
        return _said(schema.apply_defaults(dict(date=D_BUY, symbol="000333", **row), costs=COSTS))

    adjust = said(name="美的集团", kind="adjust", shares=100.0)
    assert "股数调整" in adjust and "+100 股" in adjust and "×" not in adjust, adjust
    assert "-50 股" in said(kind="adjust", shares=-50.0)
    dividend = said(name="美的集团", kind="dividend", amount=320.0)
    assert "现金分红" in dividend and "到账 320.00 元" in dividend and "股" not in dividend, dividend


def test_a_trade_without_a_name_is_described_by_its_code_alone():
    """查不到名称的票只写代码——不写一对空括号，也不编一个名字。"""
    said = _said(_record(name=""))
    assert "000333" in said and "（" not in said, said


def test_the_delete_column_is_a_button_on_every_row():
    """历史表最前面那一列是每行一个删除按钮：只有一个垃圾桶图标，没有字。
    改之前它是一列叫「删除」的勾选框，勾了什么也不发生，要去点「保存修改」才真的删。"""
    shown = _shown(_buy(), _sell())
    assert jui.DELETE_LABEL == ":material/delete:"
    assert list(shown[jui.DELETE_COLUMN]) == [jui.DELETE_LABEL] * 2
    assert set(shown.columns) == {*schema.COLUMNS, jui.DELETE_COLUMN}


def test_a_click_is_turned_into_the_record_id_of_that_row():
    """点了第几行 → 那一行的记录号。认不出的一律是 None（当没点过）：没有点击、行号越界
    （含正好越界的那一个）、行号是负数或根本不是数——按错位的行号去认，认到的是别人那一条。"""
    ids = ("T1", "T3", "T4")
    assert jui.clicked_id(ids, {"row": 1, "label": jui.DELETE_LABEL}) == "T3"
    assert jui.clicked_id(ids, {"row": 0}) == "T1" and jui.clicked_id(ids, {"row": 2}) == "T4"
    for click in (None, {}, {"row": 3}, {"row": 7}, {"row": -1}, {"row": None}, {"row": "1"},
                  {"row": True}):
        assert jui.clicked_id(ids, click) is None, f"{click!r} 被认成了某一行"
    assert jui.clicked_id((), {"row": 0}) is None


def test_the_delete_buttons_are_wired_to_the_record_ids_on_screen():
    """按钮点下去交回来的是"第几行"。回调要把它换成记录号，用的必须是**画这张表那一轮**
    各行的记录号（随列配置一起交给按钮列）——表里显示的是筛选后的行，行号与整本日志对不上。"""
    column = jui.editor_columns(("T1", "T3"))[jui.DELETE_COLUMN]
    assert column.on_click is jui.on_delete_click and column.args == (("T1", "T3"), column.key)
    # 那一列自己的身份也跟着这一轮的记录号走：换了一批行（哪怕只是换了先后）就是另一个控件，
    # 旧表上的点击送不到新表头上。同一批行则是同一个控件。
    assert column.key.startswith(jui.DELETE_CLICK_KEY), column.key
    assert column.key == jui.editor_columns(["T1", "T3"])[jui.DELETE_COLUMN].key
    others = [("T1", "T2"), ("T3", "T1"), ("T1",), ("T1", "T3", "T4"), ()]
    assert column.key not in {jui.editor_columns(ids)[jui.DELETE_COLUMN].key for ids in others}
    config = column.config
    assert config["type_config"]["type"] == "button"
    assert config["type_config"]["button_type"] == "tertiary", "一列描了边的按钮太吵"
    assert config["pinned"] is True, "表横着拖去看备注时，删除按钮得跟着留在左边"
    assert config["label"] == "", "表头不写字：垃圾桶自己就说清了"


def test_price_range_comes_from_the_local_cache(tmp_path):
    _cache(tmp_path, low=70.0, high=73.0)
    cache = tmp_path / "data" / "cache"
    assert jui.price_range("000333", D_BUY, cache) == (70.0, 73.0)


def test_price_range_is_none_when_that_day_is_not_cached(tmp_path):
    """拿不到就什么都不说（设计 §3 的 Context）：编一条"区间未知所以可疑"的警告，
    只会训练用户忽略所有警告。"""
    _cache(tmp_path, day=D_BUY)
    cache = tmp_path / "data" / "cache"
    assert jui.price_range("000333", D_SELL, cache) is None
    assert jui.price_range("600519", D_BUY, cache) is None


def test_price_range_survives_a_corrupt_cache_file(tmp_path):
    """缓存坏了不许把录入表单打没（K 线页会为同一个文件响亮报错）。"""
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    (cache / "000333.parquet").write_bytes(b"not a parquet")
    assert jui.price_range("000333", D_BUY, cache) is None


def test_positions_table_shows_a_dash_instead_of_a_fake_zero_price(tmp_path):
    """设计 §4.3：浮动盈亏依赖本地缓存，没缓存的标的显示 — 而不是 0。"""
    from quant.journal.pnl import Position

    rows = jui.positions_table(
        (Position(symbol="000333", name="美的集团", shares=1000.0,
                  cost=71517.88, unit_cost=71.5179),), tmp_path / "nowhere")
    assert rows.loc[0, jui.PRICE_COLUMN] == "—"
    assert rows.loc[0, jui.FLOAT_PNL_COLUMN] == "—"


def test_positions_table_computes_the_floating_pnl_from_the_cached_close(tmp_path):
    """手算：最新价 76.00 × 1000 股 = 76,000；成本 71,517.88 → 浮动盈亏 4,482.12。"""
    from quant.journal.pnl import Position

    _cache(tmp_path, close=76.0)
    rows = jui.positions_table(
        (Position(symbol="000333", name="美的集团", shares=1000.0,
                  cost=71517.88, unit_cost=71.5179),), tmp_path / "data" / "cache")
    assert rows.loc[0, jui.PRICE_COLUMN] == "76.00"
    assert rows.loc[0, jui.FLOAT_PNL_COLUMN] == "4,482.12"


def test_summary_metrics_never_render_none_or_nan():
    """零平仓时胜率与盈亏比是"算不出来"。渲染出 None/nan 是甩到用户脸上的乱码。"""
    from quant.journal.pnl import compute_pnl

    report = compute_pnl(store.empty_trades())
    texts = [text for _label, text, _color in jui.summary_metrics(report.summary)]
    assert all(t and "nan" not in t.lower() and "None" not in t for t in texts), texts
    assert texts.count("—") >= 3, texts


def test_kind_labels_cover_every_record_type():
    """四种记录类型都要有中文标签：漏一个，那一档在筛选框里就是个裸 'adjust'。"""
    assert set(jui.KIND_LABELS) == set(schema.KINDS)
    assert set(jui.SOURCE_LABELS) == set(schema.SOURCES)


def test_source_labels_are_derived_from_the_strategy_labels():
    """v0.4.0 M1：策略侧的来源标签 = 策略显示名 + "信号"，派生自注册表，
    不再手写死；两个固定项照旧。"""
    from quant.strategy import REGISTRY, strategy_label

    for key in REGISTRY:
        assert jui.SOURCE_LABELS[key] == f"{strategy_label(key)}信号", \
            f"{key} 的来源标签没跟上策略显示名"
    assert jui.SOURCE_LABELS["ma_cross"] == "双均线交叉信号"
    assert jui.SOURCE_LABELS["donchian"] == "唐奇安通道突破信号"
    assert jui.SOURCE_LABELS["discretionary"] == "自主决策"
    assert jui.SOURCE_LABELS["other"] == "其他"


def test_source_labels_follow_a_new_strategy_registration():
    """往 REGISTRY 塞个假策略，SOURCE_LABELS 必须自动跟上（设计 §1.3）：
    新策略自动获得日志侧显示名，不再有"欠一个座位"的那类静默缺口。
    与 SOURCES 同一验证路径：注册后重新加载模块（新装策略走的正是这条路）。"""
    from quant.strategy import REGISTRY
    from quant.strategy.base import Strategy

    class Fake(Strategy):
        name = "fake_v99"
        label = "假策略"

        def generate_positions(self, df):
            raise NotImplementedError

    REGISTRY["fake_v99"] = Fake
    try:
        importlib.reload(importlib.import_module("quant.journal.schema"))
        fresh = _load_journal_ui()
        assert fresh.SOURCE_LABELS["fake_v99"] == "假策略信号"
        assert set(fresh.SOURCE_LABELS) == set(fresh.schema.SOURCES), \
            "派生之后两边必须继续同步（选择框只认 SOURCES 里的值）"
    finally:
        del REGISTRY["fake_v99"]
        importlib.reload(importlib.import_module("quant.journal.schema"))
        sys.modules["qd_journal_ui_probe"] = jui   # 放回本文件其余测试用的那份


# ================================================================ 文档（设计 §4.3）

README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_has_a_journal_section():
    assert "交易日志" in README


@pytest.mark.parametrize("kind", schema.KINDS)
def test_the_readme_explains_every_record_type(kind):
    """四种记录类型的含义必须写下来。adjust/dividend 尤其重要：
    不知道有它们的用户会漏记送股与分红，而那会让持仓与盈亏静默出错。"""
    assert kind in README, f"README 没提到记录类型 {kind}"
    assert jui.KIND_LABELS[kind] in README, f"README 没解释 {kind} 是什么"


@pytest.mark.parametrize("fragment", ["浮动盈亏", "配股", "税务"])
def test_the_readme_states_the_known_limits(fragment):
    """设计 §4.3 的三条已知局限，一条都不许漏——用户会拿这些数字做决定。"""
    section = README.split("交易日志", 1)[1]
    assert fragment in section, f"README 的交易日志一节没写到局限：{fragment}"


def test_the_page_shows_the_known_limits_too(tmp_path):
    """局限要写在**页面上**，不只在 README 里：看数字的人不一定读过 README。
    拆页后数字在「持仓与盈亏」页，局限就得跟着数字走。"""
    at = _page(_root(tmp_path), REPORT_PAGE)
    text = _texts(at)
    assert "浮动盈亏" in text and "行情" in text, text


def test_the_entry_page_still_explains_the_record_types(tmp_path):
    """四种记录类型的解释跟着录入表单走：漏记 adjust/dividend 的后果
    （持仓对不上、FIFO 全错且不报错）必须在记的那一刻看得到。"""
    at = _page(_root(tmp_path))
    text = _texts(at)
    assert "四种记录类型" in text, text


# ================================================================ 持仓与信号池对账（v0.5.0）

def _set_pool(tmp_path: Path, symbols: tuple[str, ...]) -> None:
    """把临时根的信号池设成 symbols（写本地覆盖文件，与面板同一条路）。"""
    from quant.config_edit import write_local_universe
    write_local_universe(tmp_path / "config" / "settings.yaml", symbols)


def _tab(at: AppTest, label: str):
    return next(t for t in at.get("tab") if t.label == label)


def test_positions_page_warns_about_holdings_outside_the_pool(tmp_path):
    """项目自己把这件事称为「最容易踩的坑」（不加进信号池的票没有任何人管它的
    卖出），而唯一知道你持着什么的这一页之前从不看信号池。

    典型剧本：扫描报出买入信号，买了、记了账，但忘了点 ＋ 加进池子；此后「信号跟踪」
    每天替你盯的是另一批标的，而它不会报错。
    """
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="000333"), _buy(symbol="600519", name="贵州茅台"))
    _set_pool(tmp_path, ("600519",))          # 000333 持着但不在池子里

    at = _page(tmp_path, REPORT_PAGE)

    assert not at.exception, at.exception
    text = _notice_texts(at)
    assert "美的集团（000333）不在信号池里" in text, text
    assert "600519" not in text and "贵州茅台" not in text, "池子里的那只被误报了"


def test_positions_page_stays_quiet_when_everything_is_tracked(tmp_path):
    """全在池内就一个字都不说。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="600519", name="贵州茅台"))
    _set_pool(tmp_path, ("600519",))

    at = _page(tmp_path, REPORT_PAGE)

    assert not at.exception, at.exception
    assert not _notices(at), _notice_texts(at)
    assert not _links(at), _links(at)


def test_the_pool_reminder_sits_above_the_positions_table_not_at_the_top(tmp_path):
    """它说的是持仓，不影响这一页的任何数字；自己拿主意买的票不在池子里是常态。2026-11-18 之前
    它是页顶的一个黄框，用户看着它问「这句话啥意思？还搞得那么显眼」。现在是「当前持仓」
    标签页里、表格上面的一行：一句会变的话 + 圆点里的为什么 + 右端去信号池的链接。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="000333"))
    _set_pool(tmp_path, ("600519",))
    at = _page(tmp_path, REPORT_PAGE)
    assert not at.exception, at.exception
    guide, theme = _guide(), app_module("theme")

    (notice,) = _notices(at)
    assert notice.proto.body == theme.notice(
        guide.JOURNAL_OUTSIDE_POOL.format(names="美的集团（000333）"),
        help=guide.JOURNAL_OUTSIDE_POOL_HELP)
    tab = _tab(at, "当前持仓")
    inside = list(_walk(tab))
    assert any(e is notice for e in inside), "提醒不在「当前持仓」标签页里"
    table = next(e for e in inside if any(e is t for t in at.get("dataframe")))
    order = [e for e in inside if e is notice or e is table]
    assert order[0] is notice, "提醒应该在持仓表的上面"
    assert not at.main.warning, [w.value for w in at.main.warning]
    assert _links(at) == {guide.JOURNAL_OUTSIDE_POOL_LINK: "universe"}, _links(at)
    # 为什么与要不要管，只在圆点里
    tip = notice.proto.body.split('class="qd-help-tip">', 1)[1]
    assert "只给" in tip and "可以不管" in tip, tip
    shown = notice.proto.body.split('<span class="qd-help-dot', 1)[0]
    assert "可以不管" not in shown and len(shown) < 90, shown


def test_the_pool_reminder_names_every_stray_holding_once(tmp_path):
    """几只都不在池子里：一行说完，用顿号连。没有名称的只说代码，后面补一个空格。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="000333"), _buy(symbol="600519", name="贵州茅台"),
             _buy(symbol="601318", name=""))
    _set_pool(tmp_path, ("000001",))
    at = _page(tmp_path, REPORT_PAGE)
    assert not at.exception, at.exception
    (notice,) = _notices(at)
    assert "美的集团（000333）、贵州茅台（600519）、601318 不在信号池里" in notice.proto.body, \
        notice.proto.body


def test_a_sold_out_stock_is_not_reported_as_a_stray_holding(tmp_path):
    """账上对不上的票会在持仓表里留一行 0 股的空壳（为了挂那个标记）。手里一股都没有的票，
    没有什么卖出可盯——不许因为它不在池子里就提醒。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="600519", name="贵州茅台"),
             _sell(symbol="600036", name="招商银行"))        # 只有卖出：0 股的空壳
    _set_pool(tmp_path, ("600519",))                        # 600036 不在池子里
    at = _page(tmp_path, REPORT_PAGE)
    assert not at.exception, at.exception
    assert "不在信号池" not in _notice_texts(at), _notice_texts(at)
    assert "universe" not in _links(at).values(), _links(at)
    assert "招商银行（600036）" in _notice_texts(at), "前提：那行空壳确实在（页顶说了它的卖出对不上）"


def test_the_pool_reminder_is_skipped_when_the_pool_cannot_be_read(tmp_path):
    """配置读不到时不提示：不猜，也不在盈亏页上再报一次配置错。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="000333"))
    (tmp_path / "config" / "settings.yaml").write_text("universe: [\n", encoding="utf-8")
    at = _page(tmp_path, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not _notices(at), _notice_texts(at)


# ================================================================ 账上对不上的地方（2026-11-18）
#
# 用户看着页顶两条黄框问：「这两句话啥意思？还搞得那么显眼」。句子点名哪只票、哪一天、怎么办，
# 是 quant.journal.pnl 的事（tests/test_journal_pnl.py 钉着）；这里钉页面怎么摆：
# 排在所有数字之前、一直留到补齐为止，但不再是黄框；右端一个去「记账」的链接。

def _cards(at: AppTest) -> list:
    return [n for n in _walk(at._tree) if getattr(n, "type", "") == "flex_container"
            and n.proto.flex_container.border]


def test_ledger_problems_are_listed_before_every_number(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(symbol="600036", name="招商银行"))
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception

    order = _page_order(at)
    notice = next(i for i, b in enumerate(order) if 'class="qd-notice"' in b)
    dashboard = next(i for i, b in enumerate(order) if 'class="qd-section">仪表盘' in b)
    first_metric = next(i for i, b in enumerate(order) if 'class="qd-metric"' in b)
    assert notice < dashboard < first_metric, "账上对不上的地方要排在所有数字之前"
    tabs = [e for t in at.get("tab") for e in _walk(t)]
    assert not any(e is n for n in _notices(at) for e in tabs), "不许收进某个标签页里"


def test_ledger_problems_are_a_quiet_card_with_one_way_out(tmp_path):
    """一块带边框的区域：左边一条一句，右端**一个**去「记账」的链接（几条的去处是同一页，
    一条一个就是一列一模一样的按钮）。不用 st.warning：那是一整条黄框。"""
    root = _root(tmp_path)
    _journal(root, _buy(shares=100.0), _sell(shares=500.0),           # 卖出比买入多
             _sell(symbol="600036", name="招商银行"))                  # 卖出找不到买入
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    guide = _guide()

    assert not at.main.warning, [w.value for w in at.main.warning]
    (card,) = _cards(at)
    (block,) = _notices(at)
    assert any(e is block for e in _walk(card)), "那几句话不在带边框的那一块里"
    assert block.proto.body.count('class="qd-notice"') == 2, block.proto.body
    assert "美的集团（000333）" in block.proto.body and "招商银行（600036）" in block.proto.body
    links = [el for el in _walk(card) if getattr(el, "type", "") == "page_link"]
    assert [(el.proto.label, el.proto.page) for el in links] == [(guide.JOURNAL_FIX_LINK, "journal")]
    # 链接在右边那一栏里、靠右；那一栏窄，所以字要短（1024 宽时只有一百来像素）
    row = path_to_element(at, links[0])
    column = next(n for n in reversed(row) if getattr(n, "type", "") == "column")
    columns = list(row[row.index(column) - 1].children.values())
    assert columns[-1] is column and column.weight <= 0.25, [c.weight for c in columns]
    assert row[-1].proto.flex_container.justify == row[-1].proto.flex_container.JUSTIFY_END
    assert len(guide.JOURNAL_FIX_LINK) <= 4 and len(guide.JOURNAL_OUTSIDE_POOL_LINK) <= 4
    # 两栏竖直居中：左边的话折成两三行时，链接不贴在第一行的顶上
    assert all("vertical_alignment: CENTER" in str(c.proto) for c in columns), [str(c.proto) for c in columns]


def test_identical_sentences_are_written_once_with_a_count(tmp_path):
    """两行一模一样的坏记录：那句话只写一次（别刷屏），但要说有两笔——不说的话，改好其中一行之后
    那句话原样留着，看着像改了没用。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _buy(), _sell())
    text = path.read_text(encoding="utf-8-sig").replace(",1000.0,71.5,", ",,,", 2)
    path.write_text("\ufeff" + text, encoding="utf-8")
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    (block,) = _notices(at)
    assert block.proto.body.count('class="qd-notice"') == 2, block.proto.body      # 坏买入一句 + 卖出对不上一句
    assert block.proto.body.count("股数或成交价是空的或者不是正数") == 1, block.proto.body
    assert "（这样的记录有 2 笔）" in block.proto.body, block.proto.body
    assert block.proto.body.count("这样的记录有") == 1, "只有一笔的那句后面不许带这半句"


def test_two_look_alike_sells_do_not_hide_behind_one_line(tmp_path):
    """同一天两笔同样股数、都找不到买入的卖出，是两笔不同的记录。初稿把它们并成一句、不说几笔：
    补上其中一笔的买入之后，那句「账上没有它的买入记录」原样留着（独立审查在真机上试出来的）。
    现在：两笔时说有两笔；补上一笔之后剩下的那句说的是真话——买过，但在这之前已经卖完了。"""
    root = _root(tmp_path)
    stray = dict(symbol="600036", name="招商银行", shares=1000.0, price=50.0)
    _journal(root, _sell(**stray), _sell(**stray))
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    (block,) = _notices(at)
    assert block.proto.body.count('class="qd-notice"') == 1
    assert "在这之前没有它的买入记录" in block.proto.body and "（这样的记录有 2 笔）" in block.proto.body

    _journal(root, _buy(date=D_BUY, **stray), _sell(**stray), _sell(**stray))     # 补上一笔 1000 股的买入
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    (block,) = _notices(at)
    assert block.proto.body.count('class="qd-notice"') == 1
    assert "已经卖完了" in block.proto.body, block.proto.body
    assert "没有它的买入记录" not in block.proto.body and "这样的记录有" not in block.proto.body


def test_a_clean_ledger_shows_no_card_at_all(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not _cards(at) and not _notices(at) and not _links(at)
    assert not at.main.warning


def test_the_report_page_carries_no_standing_explanation(tmp_path):
    """用户的话：「这些数字怎么算的、已知局限——这个说明需要专门做一个下拉框吗？」不需要：
    它是不会变的解释，按 2026-11-16 定的规矩进「？」。页顶那个折叠区并进了页名后面的帮助圆点。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not list(at.expander), [e.label for e in at.expander]
    plain = "\n".join([m.value for m in at.main.markdown] + [c.value for c in at.main.caption])
    for gone in ("已知局限", "配股", "税务", "券商数据"):
        assert gone not in plain, f"页面上又常驻着一段说明：{gone}"
    head = next(b for e in at.get("html") if 'class="qd-head"' in (b := e.proto.body))
    tip = head.split('class="qd-help-tip">', 1)[1].split("</span></span>", 1)[0]
    for fragment in ("按你记的账", "不是券商数据", "配股", "税务"):
        assert fragment in tip, f"页名后面的圆点里缺「{fragment}」：{tip}"
    assert "qd-help-wide" in head, "两段话加两条，要用宽档的提示框"


#: 用户看着页顶那句话问「啥意思」的那几个词（算盈亏那一层自己的说法），外加它们原先的叫法「告警」。
REPORT_JARGON = ("批次", "配对", "配得上", "成本未知", "告警", "卖超")


@pytest.mark.parametrize("state", ["有持仓", "有平仓", "账上对不上"])
def test_nothing_on_the_report_page_speaks_in_the_engines_words(tmp_path, state):
    """页顶那句话改了，同一页的「？」里不许还留着同一批词（独立审查指出：平仓明细的读表说明与
    仪表盘的圆点里原先写着「吃掉两个买入批次」「配对关系」「未配对的股数」「上面会有告警」）。
    看的是整页的文字，圆点里的也算。"""
    root = _root(tmp_path)
    rows = {"有持仓": [_buy()], "有平仓": [_buy(), _sell(shares=500.0)],
            "账上对不上": [_buy(shares=100.0), _sell(shares=500.0),
                       _sell(symbol="600036", name="招商银行")]}[state]
    _journal(root, *rows)
    _cache(root)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    text = "\n".join(e.proto.body for e in at.get("html")
                     if not e.proto.body.lstrip().startswith("<style>"))
    text += "\n".join(c.value for c in at.main.caption) + "\n".join(m.value for m in at.main.markdown)
    for word in REPORT_JARGON:
        assert word not in text, f"页面上还有「{word}」：…{text[max(0, text.index(word) - 40):text.index(word) + 40]}…"
    columns = jui.positions_columns() | jui.matches_columns() | jui.by_source_columns()
    helps = "".join(str(c.get("help") or "") for c in columns.values())
    for word in REPORT_JARGON:
        assert word not in helps, f"列头说明里还有「{word}」"


# ================================================================ 仪表盘：曲线与持仓占比各占一整行（2026-11-18）
#
# 原先固定两栏。还没有平仓记录时左半边只有一句话，下面空着三百多像素——用户截图圈出来问
# 「为什么左边要空出一大块空间」。初稿是"两样都有才并排"，独立审查在 1024 宽下量出来不行
# （占比图的固定边距吃掉了半幅宽的一栏，留给条的地方只剩十几像素）。现在：有哪样画哪样，一样一行。
# AppTest 没有像素，钉的是：东西是不是待在某一栏里。

def _in_a_column(at: AppTest, element) -> bool:
    return any(getattr(n, "type", "") == "column" for n in path_to_element(at, element))


def _dashboard_charts(at: AppTest) -> list:
    """仪表盘里的 plotly 图（不算「来源对比」标签页里那张）。"""
    tabbed = [e for t in at.get("tab") for e in _walk(t)]
    return [c for c in at.get("plotly_chart") if not any(c is e for e in tabbed)]


def _weights_html(at: AppTest) -> list:
    """持仓占比那一块的正文：占比条（qd-weights）或环形图（qd-donut）。"""
    return [e for e in at.get("html") if not e.proto.body.lstrip().startswith("<style>")
            and ('class="qd-weights"' in e.proto.body or 'class="qd-donut"' in e.proto.body)]


def test_the_weights_take_the_whole_row_until_there_is_a_curve(tmp_path):
    """只有持仓、还没有平仓：持仓占比独占一行，曲线的位置不留一栏空着；下面一句话说还缺什么。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    _cache(root)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not _dashboard_charts(at), "还没有已平仓的交易，不该有曲线"
    (weights,) = _weights_html(at)
    assert not _in_a_column(at, weights), "持仓占比还待在半幅宽的那一栏里，另一半是空的"
    captions = [c.value for c in at.main.caption]
    assert captions == [_guide().JOURNAL_NO_CURVE], captions
    assert not any(_in_a_column(at, c) for c in at.main.caption), "那句话也不许单独占着半栏"
    order = [e for e in _walk(at.main) if e is weights or e is at.main.caption[0]]
    assert order[0] is weights, "那句话在持仓占比的下面"


def test_an_unmatched_sell_does_not_count_as_a_closed_trade(tmp_path):
    """用户的账本就是这样：卖过一笔，但账上找不到它的买入。原先那句「还没有平仓记录——
    第一笔卖出后这里会出现…」对着这种账本是一句假话；现在说的是「已平仓的交易」。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(symbol="600036", name="招商银行"))
    _cache(root)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not _dashboard_charts(at)
    note = _guide().JOURNAL_NO_CURVE
    assert note in [c.value for c in at.main.caption]
    assert "已平仓" in note and "卖出" not in note, note


def test_curve_and_weights_each_take_a_whole_row_once_both_exist(tmp_path):
    """两样都有：曲线在上、持仓占比在下，各占一整行，谁都不许待在半幅宽的一栏里。"""
    root = _root(tmp_path)
    _half_closed(root)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    (curve,) = _dashboard_charts(at)
    (weights,) = _weights_html(at)
    assert not _in_a_column(at, curve) and not _in_a_column(at, weights), "又并排了：1024 宽下条会被挤没"
    assert "累计已实现盈亏" in json.loads(curve.proto.spec)["layout"]["title"]["text"]
    order = [e for e in _walk(at.main) if e is curve or e is weights]
    assert order == [curve, weights], "曲线在上、持仓占比在下"
    assert _guide().JOURNAL_NO_CURVE not in [c.value for c in at.main.caption]


def test_the_curve_takes_the_whole_row_when_everything_is_sold(tmp_path):
    """全部卖光：只有曲线，独占一行。没有持仓这件事，紧挨着的「当前持仓」标签页里已经写着，
    图表区不再说一遍（同一屏不说两遍）；持仓占比连标题和切换都不出。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    (chart,) = _dashboard_charts(at)
    assert not _in_a_column(at, chart)
    assert "累计已实现盈亏" in json.loads(chart.proto.spec)["layout"]["title"]["text"]
    assert not list(at.main.caption), [c.value for c in at.main.caption]
    assert "当前没有持仓" in _texts(at), "「当前持仓」标签页里那句还在"
    assert _texts(at).count("没有持仓") == 1, "没有持仓这件事同一屏说了两遍"
    assert not _weights_html(at) and not list(at.radio), "没有持仓：不出一个空的持仓占比"
    assert _guide().JOURNAL_WEIGHTS_TITLE not in _texts(at)


# ================================================================ 持仓占比：占比条 / 环形图（2026-11-18）
#
# 用户问「"持仓占比"使用饼图来呈现会不会更直观？」，看过三种画法的对比后要的是：两种都做，自己选。
# 占比条（默认）：一行一只，条的满格是全部持仓；环形图：整圈是全部持仓，最多单独画五只。

def _holdings(root: Path, *shares: float) -> None:
    """记若干只持仓（每只 10 元买入），并给它们各备一份行情（收盘 10 元）——市值就是 股数 × 10。"""
    names = ["美的集团", "贵州茅台", "招商银行", "平安银行", "万科A", "格力电器", "中国平安", "五粮液", "海康威视"]
    codes = ["000333", "600519", "600036", "000001", "000002", "000651", "601318", "000858", "002415"]
    _journal(root, *[_buy(symbol=c, name=n, shares=float(s), price=10.0)
                     for c, n, s in zip(codes, names, shares)])
    for code in codes[:len(shares)]:
        _cache(root, code, low=9.5, high=10.5, close=10.0)


def _form_radio(at: AppTest):
    return at.radio(key=jui.WEIGHTS_FORM_KEY)


def test_weights_default_to_bars_with_a_toggle_on_the_right_of_the_title(tmp_path):
    """默认是占比条。标题与切换在同一行：标题（后面跟着帮助圆点）在左，切换靠右。"""
    root = _root(tmp_path)
    _holdings(root, 600, 300, 100)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    guide, theme = _guide(), app_module("theme")

    radio = _form_radio(at)
    assert list(radio.options) == ["占比条", "环形图"], "选项显示中文名，第一个是默认"
    assert radio.value == "bars"
    assert radio.label == guide.JOURNAL_WEIGHTS_FORM_LABEL and radio.proto.label_visibility.value != 0, \
        "切换的名字不显示在页面上（读屏软件照样念得到）"
    assert radio.horizontal

    title = next(e for e in at.get("html") if 'class="qd-minihead"' in e.proto.body
                 and not e.proto.body.lstrip().startswith("<style>"))
    assert title.proto.body == theme.subhead(guide.JOURNAL_WEIGHTS_TITLE, help=guide.JOURNAL_WEIGHTS_HELP)
    row = path_to_element(at, title)[-1]
    assert element_path(at, jui.WEIGHTS_FORM_KEY)[-1] is row, "标题与切换要在同一行"
    flex = row.proto.flex_container
    assert flex.direction == flex.HORIZONTAL and flex.justify == flex.JUSTIFY_END, "横着排、靠右"
    order = [e for e in row.children.values()]
    assert order[0] is title and order[-1] is radio, "标题在左，切换在最右"

    (body,) = _weights_html(at)
    assert 'class="qd-weights"' in body.proto.body and 'class="qd-donut"' not in body.proto.body


def test_bars_are_one_row_per_holding_and_as_wide_as_the_share(tmp_path):
    """手算：600、300、100 股各 10 元 → 市值 6,000 / 3,000 / 1,000，占 60% / 30% / 10%。
    条宽就是占比（满格是全部持仓），不是按最大的那只比出来的。"""
    root = _root(tmp_path)
    _holdings(root, 600, 300, 100)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    body = _weights_html(at)[0].proto.body
    assert re.findall(r'class="qd-weights-name">([^<]*)<', body) == \
        ["000333 美的集团", "600519 贵州茅台", "600036 招商银行"]
    assert re.findall(r'style="width: ([\d.]+)%"', body) == ["60.0", "30.0", "10.0"]
    assert re.findall(r'class="qd-weights-pct">([^<]*)<', body) == ["60.0%", "30.0%", "10.0%"]
    assert re.findall(r'class="qd-weights-value">([^<]*)<', body) == ["6,000 元", "3,000 元", "1,000 元"]
    assert re.findall(r'class="qd-weights-flag">([^<]*)<', body) == ["", "", ""]


def test_switching_to_the_donut_replaces_the_bars(tmp_path):
    root = _root(tmp_path)
    _holdings(root, 600, 300, 100)
    at = _page(root, REPORT_PAGE)
    _form_radio(at).set_value("donut").run()
    assert not at.exception, at.exception
    assert _form_radio(at).value == "donut"
    (body,) = _weights_html(at)
    html = body.proto.body
    assert 'class="qd-donut"' in html and 'class="qd-weights"' not in html
    assert re.findall(r'class="qd-donut-name">([^<]*)<', html) == \
        ["000333 美的集团", "600519 贵州茅台", "600036 招商银行"]
    assert re.findall(r'class="qd-donut-pct">([^<]*)<', html) == ["60.0%", "30.0%", "10.0%"]
    assert re.findall(r'class="qd-donut-value">([^<]*)<', html) == ["6,000 元", "3,000 元", "1,000 元"]
    assert re.findall(r'<span class="(qd-donut-key[^"]*)"></span>', html) == \
        [f"qd-donut-key qd-donut-key-{i}" for i in range(3)], "颜色按大小排：最大的一块用最亮的一档"
    # 圆环是一张 SVG 的 data URI，弧的颜色与清单里的色块是同一组
    svg = base64.b64decode(re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', html).group(1)).decode()
    ramp = app_module("theme").PRIMARY_RAMP
    assert re.findall(r'stroke="([^"]+)"', svg) == list(ramp[:3])
    # 换回来
    _form_radio(at).set_value("bars").run()
    assert 'class="qd-weights"' in _weights_html(at)[0].proto.body


def test_the_donut_folds_everything_past_the_fifth_into_others(tmp_path):
    """八只：28 / 19 / 14 / 11 / 9 / 8 / 6 / 5（百股）。环形图单独画前五只，后三只（8 + 6 + 5 = 19%）并成
    「其他 3 只」、用灰；占比条不并，八只都列。"""
    root = _root(tmp_path)
    _holdings(root, 2800, 1900, 1400, 1100, 900, 800, 600, 500)
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    bars = _weights_html(at)[0].proto.body
    assert len(re.findall(r'class="qd-weights-name">', bars)) == 8, "占比条要把八只都列出来"

    _form_radio(at).set_value("donut").run()
    assert not at.exception, at.exception
    html = _weights_html(at)[0].proto.body
    names = re.findall(r'class="qd-donut-name">([^<]*)<', html)
    assert len(names) == 6 and names[-1] == "其他 3 只", names
    assert re.findall(r'class="qd-donut-pct">([^<]*)<', html)[-1] == "19.0%"
    assert re.findall(r'class="qd-donut-value">([^<]*)<', html)[-1] == "19,000 元"
    keys = re.findall(r'<span class="(qd-donut-key[^"]*)"></span>', html)
    assert keys[:5] == [f"qd-donut-key qd-donut-key-{i}" for i in range(5)] and keys[5] == "qd-donut-key"
    theme = app_module("theme")
    svg = base64.b64decode(re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', html).group(1)).decode()
    assert re.findall(r'stroke="([^"]+)"', svg) == [*theme.PRIMARY_RAMP, theme.MUTED], "「其他」那一段弧是灰的"


def test_exactly_six_holdings_are_all_drawn_and_the_sixth_is_gray(tmp_path):
    """正好多出一只时不并：画一块「其他 1 只」不如画它自己。色板只有五档，第六只用灰。"""
    root = _root(tmp_path)
    _holdings(root, 3000, 2000, 1500, 1300, 1200, 1000)
    at = _page(root, REPORT_PAGE)
    _form_radio(at).set_value("donut").run()
    assert not at.exception, at.exception
    html = _weights_html(at)[0].proto.body
    names = re.findall(r'class="qd-donut-name">([^<]*)<', html)
    assert len(names) == 6 and names[-1] == "000651 格力电器" and not any("其他" in n for n in names), names
    assert re.findall(r'<span class="(qd-donut-key[^"]*)"></span>', html)[-1] == "qd-donut-key"


@pytest.mark.parametrize("form", ["bars", "donut"])
def test_a_holding_without_a_price_is_flagged_in_both_forms(tmp_path, form):
    """没有本地行情的持仓按成本顶上（漏掉它，其余几只的占比就都虚高了），金额后面那一小格标「按成本」。
    手算：有价的 600 股 × 10 = 6,000；没价的 400 股按成本 4,000 + 买入佣金 5 = 4,005。"""
    root = _root(tmp_path)
    _journal(root, _buy(symbol="000333", shares=600.0, price=10.0),
             _buy(symbol="600519", name="贵州茅台", shares=400.0, price=10.0))
    _cache(root, "000333", low=9.5, high=10.5, close=10.0)          # 600519 没有行情
    at = _page(root, REPORT_PAGE)
    if form == "donut":
        _form_radio(at).set_value("donut").run()
    assert not at.exception, at.exception
    html = _weights_html(at)[0].proto.body
    cls = "qd-weights" if form == "bars" else "qd-donut"
    assert re.findall(rf'class="{cls}-value">([^<]*)<', html) == ["6,000 元", "4,005 元"]
    assert re.findall(rf'class="{cls}-flag">([^<]*)<', html) == ["", _guide().JOURNAL_WEIGHTS_BY_COST]


def test_the_form_choice_is_kept_for_the_session_not_dropped_on_a_page_switch():
    """选了环形图，去别的页转一圈回来还得是环形图。Streamlit 默认在控件不渲染时就把它的值丢掉；
    靠 `persist_state="session"` 留住（真机上走过一遍：记账页 → 回来，还是环形图）。
    AppTest 换不了页，只能在源码层钉住这个参数。"""
    import ast
    tree = ast.parse((ROOT / "app" / "pages_journal_report.py").read_text(encoding="utf-8"))
    radios = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "radio"]
    assert len(radios) == 1, "这一页只有持仓占比的画法切换这一个单选"
    kwargs = {k.arg: k.value for k in radios[0].keywords}
    assert ast.literal_eval(kwargs["persist_state"]) == "session"
    assert ast.unparse(kwargs["key"]) == "journal_ui.WEIGHTS_FORM_KEY"
    assert ast.literal_eval(kwargs["horizontal"]) is True


def test_color_steps_follow_the_rank_and_leave_two_kinds_of_block_gray():
    """环形图的颜色档 = 按大小排在第几。并出来的「其他」不参加排队——哪怕它排在色板够得着的位置上
    （现在的设置下它总在色板之外，这条判断平时碰不到，变异自查里它活下来过，所以单独钉住）；
    排到色板之外的那一只也是灰的。"""
    own = {"others": 0}
    assert jui.weight_steps([own, own, own], ramp=5) == [0, 1, 2]
    assert jui.weight_steps([own] * 6, ramp=5) == [0, 1, 2, 3, 4, None], "第六只落在色板之外"
    assert jui.weight_steps([own, own, {"others": 4}], ramp=5) == [0, 1, None], "「其他」在色板够得着的位置上也是灰"
    assert jui.weight_steps([own] * 5 + [{"others": 3}], ramp=5) == [0, 1, 2, 3, 4, None]
    assert jui.weight_steps([], ramp=5) == []


def test_the_donut_keeps_as_many_holdings_as_the_ramp_has_steps():
    """环形图单独画几只 = 色板里琥珀有几档。两个数各改各的话，第六只会拿到一个没有规则的色块。"""
    assert jui.WEIGHTS_DONUT_KEEP == len(app_module("theme").PRIMARY_RAMP) == 5


# ================================================================ 记账页改版（2026-11-16）
#
# 录入区只摆每一笔都要填的那几样，费用 / 成交时刻 / 备注收进折叠区；收起来之后，
# 「会写进去什么」必须在卡片明面上写一行（控制台改版时定下的同一条规矩：看不见的值不许悄悄生效）。
# 历史表把人要看的列排在前面，类型与来源显示中文；筛选收起来之后，筛了什么同样写在明面上。

def _summary(**over) -> str:
    args = dict(kind="buy", day=D_BUY, shares=1000.0, price=71.5, fee=17.88, tax=0.0,
                costs=COSTS)
    args.update(over)
    return jui.entry_summary(**args)


def test_entry_summary_spells_out_what_a_buy_will_cost():
    """手算：1000 × 71.50 = 71,500.00；佣金 17.88；买入无印花税；共付出 71,517.88。"""
    text = _summary()
    assert "买入 1,000 股 × 71.50 = 71,500.00 元" in text, text
    assert "佣金 17.88" in text and "印花税 0.00" in text, text
    assert "共付出 71,517.88 元" in text, text


def test_entry_summary_of_a_sell_is_what_lands_in_the_account():
    """卖出是到手多少：75,000 − 18.75 − 37.50 = 74,943.75。"""
    text = _summary(kind="sell", day=D_SELL, price=75.0, fee=18.75, tax=37.5)
    assert "卖出 1,000 股 × 75.00 = 75,000.00 元" in text, text
    assert "到手 74,943.75 元" in text and "共付出" not in text, text


def test_entry_summary_uses_the_fee_you_typed_not_the_model():
    """费用输入框收进了折叠区：改成券商实际值之后，明面上这一行得跟着变，
    否则写进去的是 5.00、屏幕上还写着 17.88。"""
    text = _summary(fee=5.0)
    assert "佣金 5.00" in text and "共付出 71,505.00 元" in text, text


def test_entry_summary_falls_back_to_the_cost_model_when_the_fee_is_blank():
    """费用留空时落盘走成本模型（schema.apply_defaults）——这一行说的必须是同一个数。"""
    text = _summary(fee=None, tax=None)
    assert f"佣金 {commission(71500.0, COSTS):.2f}" in text, text


@pytest.mark.parametrize("shares, price", [(None, 71.5), (1000.0, None), (0.0, 71.5),
                                           (1000.0, 0.0)])
def test_entry_summary_is_empty_until_shares_and_price_are_both_there(shares, price):
    """没填完就什么都不说：一行"买入 0 股 = 0.00 元"是编出来的数字。"""
    assert _summary(shares=shares, price=price) == ""


def test_entry_summary_keeps_the_third_decimal_of_a_low_priced_share():
    """低价股的厘位有意义（3.245 不是 3.25）；整数价位不拖着三个零。"""
    assert "× 3.245 =" in _summary(shares=10000.0, price=3.245, fee=None)
    assert "× 71.50 =" in _summary()


def test_entry_summary_only_speaks_for_buys_and_sells():
    assert _summary(kind="dividend") == ""


def test_entry_summary_never_rounds_what_you_typed(tmp_path):
    """这一行是拿来核对的：写出来的股数、成交价必须就是输入的那个数，
    而等号后面的金额是按**没舍入**的数算的——把 150.5 股写成 150 股、把 3.2456 写成 3.246，
    等号两边就对不上了（审查时抓到的）。常见的整数股、两位小数价照旧不拖尾巴。"""
    odd = _summary(shares=150.5, price=10.0, fee=None)
    assert "买入 150.5 股 × 10.00 = 1,505.00 元" in odd, odd
    fine = _summary(shares=100.0, price=3.2456, fee=None)
    assert "× 3.2456 = 324.56 元" in fine, fine
    assert "买入 1,000 股 × 71.50 =" in _summary()


def test_entry_summary_shows_a_trade_whose_amount_rounds_to_nothing():
    """1 股 × 0.001 元，成交金额舍入后是 0.00——可它照样会被记下来（佣金还有最低 5 元），
    核对行不能因为金额是 0 就整行不出。"""
    text = _summary(shares=1.0, price=0.001, fee=None)
    assert "= 0.00 元" in text and f"佣金 {commission(0.0, COSTS):.2f}" in text, text


def test_filter_summary_is_empty_when_nothing_is_set():
    blank = {"start": None, "end": None, "symbols": (), "kinds": (), "sources": (),
             "reason": "  "}
    assert jui.filter_summary(blank) == ""
    assert jui.filter_summary({}) == ""


def test_filter_summary_names_every_condition_in_words():
    """筛选收进了折叠区：筛了什么得在表格上方写出来，而且是给人看的字——
    类型与来源用显示名，不是 sell / discretionary。"""
    text = jui.filter_summary({
        "start": date(2026, 8, 1), "end": date(2026, 8, 31), "symbols": ["600519", "000333"],
        "kinds": ["sell"], "sources": ["discretionary", "ma_cross"], "reason": "跌破"})
    assert "2026-08-01 至 2026-08-31" in text, text
    assert "600519、000333" in text, text
    assert "卖出" in text and "sell" not in text, text
    assert "自主决策" in text and "discretionary" not in text and "ma_cross" not in text, text
    assert "「跌破」" in text, text


def test_filter_summary_reads_naturally_with_only_one_end_of_the_date_range():
    assert "2026-08-01 起" in jui.filter_summary({"start": date(2026, 8, 1)})
    assert "截至 2026-08-31" in jui.filter_summary({"end": date(2026, 8, 31)})


def _shown(*rows) -> pd.DataFrame:
    """编辑区喂进去的那张表：归一过的日志行 + 末列「删除」（同页面上 editor_frame 的产物）。"""
    records = []
    for i, row in enumerate(rows, 1):
        record = schema.apply_defaults(row, costs=COSTS)
        record["trade_id"] = f"T{i}"
        records.append(record)
    base = store.apply_edits(pd.DataFrame(records, columns=list(schema.COLUMNS)),
                             pd.DataFrame(columns=["trade_id"]))
    return jui.editor_frame(base)


def test_pending_changes_counts_the_rows_whose_values_differ():
    """「还没保存」数的是**改过的行**。2026-11-18 之前它还数"勾了几行要删除"——删除那时是
    表里的一列勾，要等保存；现在删除走每行自己的按钮，表里攒着没保存的只有改动。"""
    shown = _shown(_buy(), _sell(), _buy(symbol="600519", shares=100.0, price=1600.0),
                   _sell(symbol="600519", shares=100.0, price=1700.0))
    edited = shown.copy()
    edited.loc[0, "reason"] = "改了"
    edited.loc[3, "price"] = 10.0
    edited.loc[3, "note"] = "同一行改了两格，还是一行"
    assert jui.pending_changes(shown, edited) == 2


def test_pending_changes_is_about_values_not_about_what_was_touched():
    """把原来的值又敲了回去：表里的值和文件里的一样，就没有东西要保存。
    审查时抓到的：按"编辑器记了哪些格子被碰过"来算，这种会报「还没保存」，
    而点下去得到的是一句「没有需要保存的改动」——同一屏两句话打架。"""
    shown = _shown(_buy(), _sell())
    edited = shown.copy()
    edited.loc[0, "reason"] = shown.loc[0, "reason"]            # 原值敲回去
    assert jui.pending_changes(shown, edited) == 0


def test_pending_changes_survives_the_types_an_editor_hands_back():
    """编辑器交回来的值不一定和喂进去的同一个类型：日期可能是字符串 / date / Timestamp，
    数字可能是整数或字符串，空格子可能是 None / NaN / 空串。类型不同不等于改过。"""
    shown = _shown(_buy(), _sell())
    edited = shown.astype(object).copy()
    edited.loc[0, "date"] = str(D_BUY)                          # 同一天，写成字符串
    edited.loc[0, "shares"] = 1000                              # 同一个数，整数
    edited.loc[0, "stop_plan"] = None                           # 本来就空（NaN）
    edited.loc[1, "note"] = None                                # 本来就空（""）
    edited.loc[1, "date"] = D_SELL                              # 同一天，date 对象
    assert jui.pending_changes(shown, edited) == 0
    edited.loc[1, "date"] = D_SELL - timedelta(days=1)          # 真改了一天
    assert jui.pending_changes(shown, edited) == 1


def test_pending_changes_of_an_untouched_editor_is_zero():
    shown = _shown(_buy(), _sell())
    assert jui.pending_changes(shown, shown.copy()) == 0
    assert jui.pending_changes(shown.iloc[0:0], shown.iloc[0:0]) == 0


def test_pending_changes_measures_with_the_same_ruler_as_the_save():
    """提醒与保存必须是同一把尺（复审抓到的不一致）：保存时文字**原样**落盘、不去空白，
    空着的数字与 0 是两回事。初稿的提醒把"只敲了几个空格""空的改成 0"当成没改——
    按钮安安静静，点下去却是「已保存：改了 1 行」，备份也跟着换了一版。"""
    shown = _shown(_buy(), _sell())
    spaces = shown.copy()
    spaces.loc[0, "note"] = "   "                                  # 本来是空串
    assert jui.pending_changes(shown, spaces) == 1
    trailing = shown.copy()
    trailing.loc[0, "reason"] = shown.loc[0, "reason"] + " "       # 末尾多一个空格
    assert jui.pending_changes(shown, trailing) == 1
    zero = shown.copy()
    zero.loc[0, "stop_plan"] = 0.0                                 # 本来没填
    assert jui.pending_changes(shown, zero) == 1
    # 而"同一个空"的不同写法仍然不算改动
    blank = shown.astype(object).copy()
    blank.loc[0, "note"] = None
    blank.loc[0, "stop_plan"] = float("nan")
    assert jui.pending_changes(shown, blank) == 0


def test_pending_changes_would_rather_say_nothing_than_guess():
    """两张表对不上（行数不同）——固定行数的编辑器不会这样，真遇上了也只是不提醒：
    这句话是提示，不许因为它把整页打崩，更不许乱报一个数。"""
    shown = _shown(_buy(), _sell())
    assert jui.pending_changes(shown, shown.iloc[:1].copy()) == 0
    assert jui.pending_changes(shown.iloc[:1], shown.copy()) == 0
    assert len(jui.changed_rows(shown, shown.iloc[:1].copy())) == 0


def test_changed_rows_are_what_a_save_would_write():
    """改过的那几行整行交回来，不带删除按钮那一列——删除时顺带保存，写进去的就是这几行。
    没改过的行不在里面：没碰过的记录不该被这一轮渲染时读到的旧值再写一遍。
    （「保存修改」不走这里：它交给落盘那一层的是整张表，自 v0.3.0 起一直如此，这次没有动。）"""
    shown = _shown(_buy(), _sell(), _buy(symbol="600519", shares=100.0, price=1600.0))
    edited = shown.copy()
    edited.loc[1, "note"] = "补一句"
    rows = jui.changed_rows(shown, edited)
    assert list(rows["trade_id"]) == ["T2"] and list(rows["note"]) == ["补一句"]
    assert list(rows.columns) == list(schema.COLUMNS), "删除按钮那一列不许流进日志文件"


def test_changed_rows_can_leave_out_the_ones_being_deleted():
    """正要删掉的那一笔上没保存的改动不算数——删了，改动就没有意义。"""
    shown = _shown(_buy(), _sell(), _buy(symbol="600519", shares=100.0, price=1600.0))
    edited = shown.copy()
    edited.loc[0, "note"] = "改了也白改"
    edited.loc[2, "note"] = "这一行要留着"
    assert list(jui.changed_rows(shown, edited, skip=["T1"])["trade_id"]) == ["T3"]
    assert list(jui.changed_rows(shown, edited)["trade_id"]) == ["T1", "T3"]


def test_the_pending_note_counts_rows_and_says_where_to_save():
    assert jui.pending_note(0) == ""
    said = jui.pending_note(2)
    assert "改了 2 行" in said and "还没保存" in said and "「保存修改」" in said, said
    assert "删除" not in said, "删除不再等保存，这句话里不该再提它"


def test_the_editor_leads_with_what_a_person_reads_and_ends_with_the_record_id():
    """改之前第一列是机器编号、「删除」在最右边的屏幕外。现在：删除勾选框最前，
    接着是日期 / 代码 / 名称 / 类型 / 股数 / 成交价 / 金额，记录号排最后——
    它还在（改删靠它定位，「持仓与盈亏」页的平仓明细也指回它），只是不占第一眼。"""
    order = list(jui.EDITOR_ORDER)
    assert order[0] == jui.DELETE_COLUMN and order[-1] == "trade_id", order
    assert order[1:8] == ["date", "symbol", "name", "kind", "shares", "price", "amount"], order
    assert sorted(order) == sorted([*schema.COLUMNS, jui.DELETE_COLUMN]), \
        "列序必须恰好覆盖全部字段：漏一列，那一列就从表里消失且不报错"
    assert set(jui.editor_columns()) == set(order)


@pytest.mark.parametrize("column, labels", [("kind", "KIND_LABELS"),
                                            ("source", "SOURCE_LABELS")])
def test_the_editor_shows_kind_and_source_in_chinese_but_stores_the_keys(column, labels):
    """表里显示「买入 / 自主决策」，写回文件的仍是 buy / discretionary（文件格式不变）。
    改之前这两列直接摆着内部键——面板别处从不出现的英文词。"""
    options = jui.editor_columns()[column]["type_config"]["options"]
    wanted = getattr(jui, labels)
    keys = schema.KINDS if column == "kind" else schema.SOURCES
    assert [o["value"] for o in options] == list(keys)
    assert [o["label"] for o in options] == [wanted[k] for k in keys], options


@pytest.mark.parametrize("column", [*schema.NUM_COLUMNS, "name", "symbol", "date"])
def test_no_column_that_can_grow_is_pinned_to_a_fixed_width(column):
    """数字右对齐，列宽钉死之后放不下时被截掉的是**左边**：真机上 1,234,500 股显示成
    234500.00，一千五百万的金额少了最高位——一个看着完全正常的错数字。名称长的 ETF、
    十个字符的日期同理。这些列的宽度一律交给表格按内容定。
    （钉了宽度的只有勾选框与理由两列：理由是长文字，被截的是右边、看得出没显示完。）"""
    assert jui.editor_columns()[column]["width"] is None, \
        f"{column} 列被钉成了固定宽度: {jui.editor_columns()[column]['width']}"


def test_export_buttons_say_it_in_words_with_an_icon(tmp_path):
    """按钮图标走 icon=，不把 ↓ 拼在文字前面；CSV 那个不再把一句解释塞进按钮文字
    （"Excel 可直接打开"挪到悬停提示里——按钮上的字长到折成两行）。
    悬停提示必须说清**导出的是筛选后的这些行**：筛选收进折叠区之后更容易忘。"""
    payloads = jui.export_payloads(pd.DataFrame(columns=list(schema.COLUMNS)), TODAY)
    assert [(p["kind"], p["label"]) for p in payloads] == [("csv", "导出 CSV"),
                                                          ("xlsx", "导出 Excel")]
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    buttons = {e.proto.id.split("-")[-1]: e.proto for e in at.get("download_button")}
    for key, label in ((jui.CSV_KEY, "导出 CSV"), (jui.XLSX_KEY, "导出 Excel")):
        proto = buttons[key]
        assert (proto.label, proto.icon) == (label, ":material/download:"), (proto.label, proto.icon)
        assert "筛选" in proto.help, f"{label} 的提示没说导出的是筛选后的行: {proto.help}"
    assert "Excel" in buttons[jui.CSV_KEY].help, "CSV 能用 Excel 直接打开这件事还得说"


def _toasts(at: AppTest) -> list[str]:
    return [t.value for t in at.toast]


def test_saving_with_nothing_changed_leaves_the_file_and_the_backup_alone(tmp_path):
    """备份只留一版，每次写入都会把它换成"写入前的样子"。所以一次**什么都没改**的保存
    不是无害的：删错一条之后，手一抖再点一下「保存修改」，备份就被换成了删错之后
    的那一版——唯一的撤销手段没了，而屏幕上只是又弹了一句"已保存"。
    没有改动就不写盘：文件与备份都逐字节不动，并且如实说没有东西要保存。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    two_rows = path.read_bytes()
    at = _page(root)
    at = _delete(at, 1)
    one_row = path.read_bytes()
    assert store.backup_path(path).read_bytes() == two_rows, "前提：备份是删之前的那一版"

    at = at.button(key=jui.SAVE_KEY).click().run()

    assert not at.exception, at.exception
    assert path.read_bytes() == one_row
    assert store.backup_path(path).read_bytes() == two_rows, \
        "一次空保存把唯一的备份换成了删错之后的那一版"
    assert any("没有" in t and "改动" in t for t in _toasts(at)), _toasts(at)


def test_the_save_message_counts_the_rows_that_really_changed(tmp_path):
    """改之前它说「改动 2 行」——表里一共两行，只改了一行的理由。那个数是"表里有几行"，
    不是"改了几行"；只删一行时更离谱：七行的表会说「改动 6 行、删除 1 行」。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at = edit_table(at, {0: {"reason": "改成别的理由"}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    said = " ".join(_toasts(at))
    assert "改了 1 行" in said and "删除" not in said, said


def test_the_delete_message_says_what_happened_and_how_to_undo(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(), _buy(symbol="600519", name="贵州茅台", shares=100.0,
                                        price=1600.0, source="discretionary"))
    at = _delete(_page(root), 1)

    assert not at.exception, at.exception
    said = " ".join(_toasts(at))
    assert "已删除 1 条" in said and "改" not in said.split("。")[0], said
    assert "恢复上一版" in said, "刚删完是最需要知道'还能换回来'的时刻"


# ---------------------------------------------------------------- 版式（AppTest）

def _guide():
    return app_module("guide")


def _keys_inside(node) -> set:
    return {getattr(e, "key", None) for e in _walk(node)} - {None}


def _expander(at: AppTest, label: str):
    found = [e for e in at.expander if e.label == label]
    assert len(found) == 1, f"应恰好一个「{label}」折叠区: {[e.label for e in at.expander]}"
    return found[0]


def _check_line(at: AppTest) -> str:
    """录入区「核对」那一行（没出就是空串）。"""
    for body in (e.proto.body for e in at.get("html")):
        # theme.inject() 那段 <style> 也走 st.html，里面就有这个类名——滤掉免得蒙对
        if "qd-task-status" in body and not body.lstrip().startswith("<style>"):
            return body
    return ""


def test_fees_and_the_two_optional_fields_are_tucked_into_more(tmp_path):
    """自动算好的费用与两个可空项收进「更多」：它们九成的时候不用碰，
    摆在明面上却和必填的五样一个分量——第一次来的人分不清先填哪个。
    折叠而不是不渲染：控件一直在页面上，费用照常自动算。"""
    at = _fill_entry(_page(_root(tmp_path)))
    more = _expander(at, _guide().JOURNAL_MORE)
    assert more.proto.expanded is False, "「更多」默认应该收起"
    tucked = {jui.FEE_KEY, jui.TAX_KEY, jui.TIME_KEY, jui.NOTE_KEY}
    assert _keys_inside(more) == tucked, _keys_inside(more)
    assert at.number_input(key=jui.FEE_KEY).value == 17.88, "收起来不等于不算"


def test_what_will_be_written_is_spelled_out_on_the_card(tmp_path):
    """费用收起来之后，会写进去的数必须在明面上：成交金额、费用、最后付出多少。
    手算：1000 × 71.50 = 71,500.00；佣金 17.88；共付出 71,517.88。"""
    at = _fill_entry(_page(_root(tmp_path)))
    line = _check_line(at)
    assert _guide().JOURNAL_CHECK_KEY in line, line
    assert "买入 1,000 股 × 71.50 = 71,500.00 元" in line, line
    assert "佣金 17.88" in line and "共付出 71,517.88 元" in line, line


def test_the_check_line_stays_silent_until_shares_and_price_are_in(tmp_path):
    """没填完时不许出一行"买入 0 股 = 0.00 元"。"""
    at = _page(_root(tmp_path))
    assert _check_line(at) == "", _check_line(at)


def test_a_fee_typed_by_hand_shows_in_the_check_line_and_lands_in_the_file(tmp_path):
    """在「更多」里把佣金改成券商实际值：明面上那一行跟着变，写进文件的也是它。
    屏幕上写着 17.88、文件里记的是 5.00，正是"看不见的值悄悄生效"。"""
    root = _root(tmp_path)
    at = _fill_entry(_page(root))
    at.number_input(key=jui.FEE_KEY).set_value(5.0)
    at = at.run()
    line = _check_line(at)
    assert "佣金 5.00" in line and "共付出 71,505.00 元" in line, line

    at = at.button(key=jui.SUBMIT_KEY).click().run()
    assert not at.exception, at.exception
    assert store.load_trades(_trades_path(root)).iloc[0]["fee"] == 5.0


def test_the_history_filters_wait_in_a_collapsed_expander(tmp_path):
    """七笔记录上面常驻六个筛选框，是这一页最吵的一排。收进折叠区，控件照旧都在。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    box = _expander(at, _guide().JOURNAL_FILTER)
    assert box.proto.expanded is False
    assert _keys_inside(box) == {
        jui.FILTER_START_KEY, jui.FILTER_END_KEY, jui.FILTER_SYMBOL_KEY,
        jui.FILTER_KIND_KEY, jui.FILTER_SOURCE_KEY, jui.FILTER_REASON_KEY}, _keys_inside(box)
    # 没筛的时候，表格上方不说"筛出几条"，也没有「清除筛选」
    assert not any("筛出" in c.value for c in at.main.caption)
    assert not [b for b in at.button if b.key == jui.FILTER_CLEAR_KEY]


def test_an_active_filter_is_spelled_out_above_the_table_and_can_be_cleared(tmp_path):
    """筛选收起来之后，筛了什么必须写在表格上方——导出只导筛出来的这些行，
    看不见条件的话人会以为导出的是全部。旁边一个「清除筛选」一下复位全部条件。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    # 六个条件全设上（都选得中 T2 那笔卖出）：「清除筛选」漏掉哪一个，表就回不到两行
    at.date_input(key=jui.FILTER_START_KEY).set_value(D_SELL)
    at.date_input(key=jui.FILTER_END_KEY).set_value(D_SELL)
    at.multiselect(key=jui.FILTER_SYMBOL_KEY).set_value(["000333"])
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at.multiselect(key=jui.FILTER_SOURCE_KEY).set_value(["ma_cross"])
    at.text_input(key=jui.FILTER_REASON_KEY).set_value("跌破")
    at = at.run()

    said = [c.value for c in at.main.caption if "筛出" in c.value]
    assert len(said) == 1, [c.value for c in at.main.caption]
    assert "筛出 1 / 2 条" in said[0] and "卖出" in said[0] and "「跌破」" in said[0], said
    # "导出也只含这些"是解释，不在这一行；它在两个导出按钮自己的悬停说明里
    assert "导出" not in said[0], said
    exports = at.get("download_button")
    assert len(exports) == 2 and all("筛选" in e.proto.help for e in exports), \
        [e.proto.help for e in exports]
    clear = at.button(key=jui.FILTER_CLEAR_KEY)
    assert (clear.label, clear.proto.type) == (_guide().JOURNAL_FILTER_CLEAR, "tertiary")

    at = clear.click().run()
    assert not at.exception, at.exception
    assert at.date_input(key=jui.FILTER_START_KEY).value is None
    assert at.date_input(key=jui.FILTER_END_KEY).value is None
    for key in (jui.FILTER_SYMBOL_KEY, jui.FILTER_KIND_KEY, jui.FILTER_SOURCE_KEY):
        assert at.multiselect(key=key).value == [], key
    assert not at.text_input(key=jui.FILTER_REASON_KEY).value
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])]
    assert list(shown[0]["trade_id"]) == ["T1", "T2"], shown[0]
    assert not any("筛出" in c.value for c in at.main.caption)


def test_filtering_everything_away_says_so_and_keeps_the_way_out(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.text_input(key=jui.FILTER_REASON_KEY).set_value("根本没有这个词")
    at = at.run()
    assert not at.exception, at.exception
    assert _guide().JOURNAL_FILTER_NONE in _texts(at)
    assert at.button(key=jui.FILTER_CLEAR_KEY), "筛没了更需要「清除筛选」"
    assert not [e for e in at.get("dataframe") if e.proto.id], "筛没了就不画一张空的编辑表"
    # 没有表可改，就没有「保存修改」（点下去没有东西可存）；撤销与导出照留。
    # 变异自查抓到这一处没人钉：按钮从表格的函数里挪出来之后，它在不在得另外判。
    keys = action_button_keys(at)
    assert jui.SAVE_KEY not in keys, keys
    assert {jui.RESTORE_KEY, jui.CSV_KEY, jui.XLSX_KEY} <= set(keys), keys


def test_an_empty_journal_shows_the_way_in_and_nothing_to_operate_on(tmp_path):
    """一笔都没记过：筛选、表格、导出都没有东西可作用——只留那句指路的话和撤销手段
    （「恢复上一版」照留，见上面那条：正要大批量录入的时刻更需要知道改错了还有救）。"""
    at = _page(_root(tmp_path))
    assert not at.exception, at.exception
    guide = _guide()
    assert guide.JOURNAL_EMPTY in _texts(at)
    assert guide.JOURNAL_LOG_TITLE.format(n=0) in _texts(at)
    assert not [e for e in at.expander if e.label == guide.JOURNAL_FILTER], "没有记录时不出筛选"
    assert not at.get("download_button"), "没有记录时不出导出"
    # 屏幕上没有导出按钮，页面上就不该有哪句话还叫人"用导出"（审查时抓到的）
    assert not any("导出" in c.value for c in at.main.caption), [c.value for c in at.main.caption]
    assert not [e for e in at.get("dataframe") if e.proto.id]
    # 撤销手段还在：按钮自己就是告示（没有可恢复的版本，所以禁用，悬停说明里说为什么）
    restore = at.button(key=jui.RESTORE_KEY)
    assert restore.label == "恢复上一版" and restore.disabled
    # 没有表可改，小标题后面就不带那个讲"怎么改怎么删"的帮助圆点
    head = [h for h in _texts(at).splitlines() if 'class="qd-section"' in h and "历史记录" in h]
    assert head and "qd-help-dot" not in head[0], head


def test_unsaved_edits_are_called_out_next_to_the_save_button(tmp_path):
    """表里改了却没点保存，切到别的页再回来就没了。有没保存的改动时：
    按钮变成主按钮，下面写明改了几行。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)
    quiet = at.button(key=jui.SAVE_KEY)
    assert (quiet.label, quiet.proto.icon, quiet.proto.type) == \
        ("保存修改", ":material/save:", "secondary"), "没有改动时它只是一个安静的按钮"
    assert not any("还没保存" in c.value for c in at.main.caption)

    at = edit_table(at, {0: {"reason": "改一下"}}, dataframe=_log_table_index(at))
    assert not at.exception, at.exception
    said = [c.value for c in at.main.caption if "还没保存" in c.value]
    assert len(said) == 1 and "改了 1 行" in said[0] and "删除" not in said[0], \
        [c.value for c in at.main.caption]
    assert at.button(key=jui.SAVE_KEY).proto.type == "primary"
    assert path.read_bytes() == before, "只是提醒，没点保存就不许动文件"


def test_the_history_table_is_reordered_for_reading_but_carries_every_field(tmp_path):
    """列序只管显示：交回来的表仍带全部字段（保存靠 trade_id 定位、导出要全部列）。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    editor = at.get("dataframe")[_log_table_index(at)]
    assert list(editor.proto.column_order) == list(jui.EDITOR_ORDER), editor.proto.column_order
    assert set(editor.value.columns) == {*schema.COLUMNS, jui.DELETE_COLUMN}
    # 上面看的是**喂进去**的表；交回来的那张是不是也全须全尾，要保存一轮才知道——
    # 少一列的话 store.save_trades 会拒写，没碰过的格子也必须原样回到文件里。
    before = store.load_trades(_trades_path(root))
    at = edit_table(at, {0: {"note": "补一句"}}, dataframe=_log_table_index(at),
                    click=jui.SAVE_KEY)
    assert not at.error, [e.value for e in at.error]
    after = store.load_trades(_trades_path(root))
    assert list(after.columns) == list(schema.COLUMNS)
    untouched = [c for c in schema.COLUMNS if c != "note"]
    pd.testing.assert_frame_equal(after[untouched], before[untouched])
    assert list(after["note"]) == ["补一句", ""]


def test_blank_cells_show_a_dash_not_the_word_none(tmp_path):
    """没填的格子（计划止损、成交时刻）出厂显示灰字 "None"——一个英文词，还像真填了什么。
    面板别处的缺值都是 —。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _page(root)
    editor = at.get("dataframe")[_log_table_index(at)]
    assert editor.proto.placeholder == "—", repr(editor.proto.placeholder)


def test_the_tools_under_the_table_explain_themselves_on_hover(tmp_path):
    """表格上下改之前是四段常驻的灰字（怎么改怎么删、保存说明、撤销手段、导出说明）。
    2026-11-16 第二轮起一句都不常驻：怎么改怎么删在「历史记录」小标题后面的帮助圆点里，
    其余的挂在各自的按钮上（悬停才出）。没有待保存的改动时，表格周围没有一行小字。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    store.save_trades(store.load_trades(path), path)     # 造出一份备份：恢复按钮才有正经的说明
    at = _page(root)
    guide = _guide()
    assert at.button(key=jui.SAVE_KEY).proto.help == guide.JOURNAL_EDIT_HINT
    assert at.button(key=jui.RESTORE_KEY).proto.help == guide.JOURNAL_RESTORE_HELP
    assert {e.proto.help for e in at.get("download_button")} == set(guide.JOURNAL_EXPORT_HELP.values())
    captions = [c.value for c in at.main.caption]
    assert captions == [], f"没有筛选、没有待保存的改动时，这一页不该有任何常驻的小字: {captions}"
    hint = guide.TABLE_HINTS["journal"]
    assert "不会" in hint and "重算" in hint, "改股数或成交价时金额不会跟着变——这一句不能丢"
    head = [h for h in _texts(at).splitlines() if 'class="qd-section"' in h and "历史记录" in h]
    assert len(head) == 1 and "qd-help-tip" in head[0] and "重算" in head[0], head


# ---------------------------------------------------------------- 记完之后清表单（2026-11-16 真机抓到）
#
# 记完一笔，输入框里上一笔的代码、股数、成交价还留在浏览器里。原先的清法是把控件的值从
# session_state 里删掉：后台确实空了，可浏览器不知道——它手里那份旧值还在，下一次任何操作
# （哪怕只是点一下「卖出」）都会把它们原样送回来。AppTest 没有浏览器，这件事只能看协议：
# 明确写回空值时控件的 set_value 为真，浏览器照着清；只删键时它为假，浏览器留着旧值。

def test_recording_a_trade_tells_the_browser_to_empty_the_form(tmp_path):
    root = _root(tmp_path)
    at = _fill_entry(_page(root))
    at.number_input(key=jui.STOP_KEY).set_value(68.0)
    at.text_input(key=jui.NOTE_KEY).set_value("备注一句")
    at.text_input(key=jui.TIME_KEY).set_value("14:35")
    at = at.run()
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    assert len(store.load_trades(_trades_path(root))) == 1, "前提：这一笔记下了"
    for key in (jui.SYMBOL_KEY, jui.REASON_KEY, jui.NOTE_KEY, jui.TIME_KEY):
        box = at.text_input(key=key)
        assert not box.value and box.proto.set_value, f"{key} 没有被明确清空"
    for key in (jui.SHARES_KEY, jui.PRICE_KEY, jui.STOP_KEY):
        box = at.number_input(key=key)
        assert box.value is None and box.proto.set_value, f"{key} 没有被明确清空"
    # 股数与成交价空了，费用跟着回到 0——留着上一笔的佣金是最难发现的一种错
    assert at.number_input(key=jui.FEE_KEY).value == 0.0
    assert _check_line(at) == "", "表单空了，核对那一行也该消失"


def test_the_form_keeps_direction_source_and_date_after_recording(tmp_path):
    """一晚上连着记的往往是同方向、同来源的几笔，补记旧交易时刚选好的日期也别动：
    这三样不清（v0.5.0 的决定，照旧）。"""
    root = _root(tmp_path)
    at = _fill_entry(_page(root), kind="sell", day=D_SELL)
    at.selectbox(key=jui.SOURCE_KEY).set_value("ma_cross")
    at = at.run()
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    assert at.button_group(key=jui.KIND_KEY).value == "sell"
    assert at.selectbox(key=jui.SOURCE_KEY).value == "ma_cross"
    assert at.date_input(key=jui.DATE_KEY).value == D_SELL


def test_a_refused_trade_leaves_the_form_as_typed(tmp_path):
    """没记下来（缺股数）时一个字都不许清：人要照着提示把缺的那格补上，不是重填一遍。"""
    root = _root(tmp_path)
    at = _page(root)
    at.text_input(key=jui.SYMBOL_KEY).set_value("000333")
    at.text_input(key=jui.REASON_KEY).set_value("先写了理由")
    at = at.run()
    at.number_input(key=jui.PRICE_KEY).set_value(71.5)
    at = at.run()
    at = at.button(key=jui.SUBMIT_KEY).click().run()

    assert not _trades_path(root).exists()
    assert at.text_input(key=jui.SYMBOL_KEY).value == "000333"
    assert at.text_input(key=jui.REASON_KEY).value == "先写了理由"
    assert at.number_input(key=jui.PRICE_KEY).value == 71.5


def _record_a_dividend(at: AppTest, amount: float = 320.0) -> AppTest:
    at.button_group(key=jui.OTHER_KIND_KEY).set_value("dividend")
    at.date_input(key=jui.OTHER_DATE_KEY).set_value(D_SELL)
    at.text_input(key=jui.OTHER_SYMBOL_KEY).set_value("000333")
    at.text_input(key=jui.OTHER_REASON_KEY).set_value("2025年度分红")
    at = at.run()
    at.number_input(key=jui.OTHER_AMOUNT_KEY).set_value(amount)
    at = at.run()
    return at.button(key=jui.OTHER_SUBMIT_KEY).click().run()


def test_recording_a_dividend_empties_that_form_so_a_second_click_adds_nothing(tmp_path):
    """「其他记录」那张表单以前记完**根本不清**（要清的键里只有买卖那张的）：到账金额还在
    框里，手一抖再点一次，就是第二笔一模一样的分红——记录号不同，没有任何报错拦得住。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _record_a_dividend(_page(root))

    assert not at.exception, at.exception
    assert list(store.load_trades(_trades_path(root))["kind"]) == ["buy", "dividend"]
    amount = at.number_input(key=jui.OTHER_AMOUNT_KEY)
    assert amount.value is None and amount.proto.set_value, "到账金额没有被明确清空"
    for key in (jui.OTHER_SYMBOL_KEY, jui.OTHER_REASON_KEY):
        box = at.text_input(key=key)
        assert not box.value and box.proto.set_value, f"{key} 没有被明确清空"

    at = at.button(key=jui.OTHER_SUBMIT_KEY).click().run()
    assert list(store.load_trades(_trades_path(root))["kind"]) == ["buy", "dividend"], \
        "再点一次不该多出第二笔分红"


def test_recording_a_dividend_does_not_wipe_a_half_typed_trade(tmp_path):
    """两张表单各清各的：上面那张买卖表单填到一半，去下面记了一笔分红，回来不该是空的。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _fill_entry(_page(root), symbol="600519", shares=100.0, price=1500.0)
    at = _record_a_dividend(at)

    assert not at.exception, at.exception
    assert at.text_input(key=jui.SYMBOL_KEY).value == "600519"
    assert at.number_input(key=jui.SHARES_KEY).value == 100.0
    assert at.number_input(key=jui.PRICE_KEY).value == 1500.0


def test_recording_a_share_adjustment_empties_its_share_count_too(tmp_path):
    """送股那一档填的是股数变化：记完同样要清，否则再点一次就是第二笔一模一样的送股。"""
    root = _root(tmp_path)
    _journal(root, _buy())
    at = _page(root)
    at.button_group(key=jui.OTHER_KIND_KEY).set_value("adjust")
    at.date_input(key=jui.OTHER_DATE_KEY).set_value(D_SELL)
    at.text_input(key=jui.OTHER_SYMBOL_KEY).set_value("000333")
    at = at.run()
    at.number_input(key=jui.OTHER_SHARES_KEY).set_value(100.0)
    at = at.run()
    at = at.button(key=jui.OTHER_SUBMIT_KEY).click().run()

    assert not at.exception, at.exception
    assert list(store.load_trades(_trades_path(root))["kind"]) == ["buy", "adjust"]
    shares = at.number_input(key=jui.OTHER_SHARES_KEY)
    assert shares.value is None and shares.proto.set_value, "股数变化没有被明确清空"


# ---------------------------------------------------------------- 编辑区的改动跟着谁走（2026-11-16 审查抓到）
#
# data_editor 记的是"第几行的哪一格改成了什么"。带 key 的固定行数编辑器，身份只看表的**结构**
# （列、行数、索引），不看里面的值（streamlit 1.61 的文档原话：Edits are matched by row position）。
# 于是两个行数相同的筛选结果是**同一个控件**：在「买入」那两行里改了第一行、没保存，
# 切到「卖出」（也是两行），那处改动就落在了另一笔交易上——真实浏览器里复现过
# （当时复现用的是删除那一列的勾，2026-11-18 起那一列是按钮，没有"勾着没保存"这回事了）。
# AppTest 不会把编辑器的状态带到下一轮，这类问题它天然看不见；下面这个替身照浏览器的规矩来：
# 记着没保存的改动，**按当时那个控件 id** 每一轮都原样送回去——id 没变就会被套到新表上，变了就作废。

class _Browser:
    def __init__(self, at: AppTest):
        self.at = at
        self.editor_id = ""
        self.edits: dict[int, dict] = {}

    def _editor(self):
        found = [e for e in self.at.get("dataframe") if e.proto.id]
        return found[0] if found else None

    def edit(self, edits: dict[int, dict]) -> AppTest:
        self.editor_id = self._editor().proto.id
        self.edits = edits
        return self.rerun()

    def rerun(self, click: str | None = None, delete: int | None = None) -> AppTest:
        """`click` 是这一轮按下的按钮；`delete` 是这一轮点了历史表第几行的删除按钮。"""
        states = self.at._tree.get_widget_states()
        if self.edits:
            state: WidgetState = states.widgets.add()
            state.id = self.editor_id          # 旧 id：控件换了的话，后台根本不认这份状态
            state.string_value = json.dumps({
                "edited_rows": {str(row): cells for row, cells in self.edits.items()},
                "added_rows": [], "deleted_rows": []})
        if delete is not None:
            row: WidgetState = states.widgets.add()
            row.id = self._editor().proto.button_click_widgets[jui.DELETE_COLUMN]
            row.string_trigger_value.data = json.dumps({"row": delete, "label": jui.DELETE_LABEL})
        if click is not None:
            pressed: WidgetState = states.widgets.add()
            pressed.id = self.at.button(key=click).id
            pressed.trigger_value = True
        self.at = self.at._run(widget_state=states)
        editor = self._editor()
        if editor is None or editor.proto.id != self.editor_id:
            self.edits = {}                    # 控件换了：浏览器手里那份改动也就没了
        return self.at

    def delete(self, row: int, *, confirm: bool = True) -> AppTest:
        """点第 row 行的删除按钮，再在确认框里点「删除」/「取消」——两下各是一轮。"""
        self.rerun(delete=row)
        return self.rerun(click=jui.DELETE_OK_KEY if confirm else jui.DELETE_CANCEL_KEY)


def _four_trades(root: Path) -> Path:
    return _journal(
        root, _buy(reason="买一"), _sell(reason="卖一"),
        _buy(symbol="600519", name="贵州茅台", shares=100.0, price=1600.0, reason="买二"),
        _sell(symbol="600519", shares=100.0, price=1700.0, reason="卖二"))


def _pending_notes(at: AppTest) -> list[str]:
    return [c.value for c in at.main.caption if "还没保存" in c.value]


def test_the_delete_button_deletes_the_row_on_screen_not_that_row_of_the_file(tmp_path):
    """筛「卖出」（T2、T4）→ 点第一行的删除按钮。按钮交回来的是"第一行"——整本日志的第一行是 T1。
    确认框里写的、最后删掉的，都必须是屏幕上那一行（T2）：换成记录号用的是画这张表那一轮各行的记录号。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    at = _page(root)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at = at.run()
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T2", "T4"], "前提：表里是卖出那两行"

    at = _click_delete(at, 0)
    assert "卖一" not in _dialog_text(at) and "卖出" in _dialog_text(at)
    assert "美的集团" in _dialog_text(at) or "000333" in _dialog_text(at), _dialog_text(at)
    at = _delete(at, 0)
    assert not at.exception, at.exception
    assert list(store.load_trades(path)["trade_id"]) == ["T1", "T3", "T4"], \
        f"点的是屏幕上第一行（T2），删掉的却是别的: {list(store.load_trades(path)['trade_id'])}"


def test_an_unsaved_edit_does_not_ride_the_row_position_onto_another_trade(tmp_path):
    """同一件事的另一半：没保存的备注也不许换一批记录之后写到别人头上。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    before = path.read_bytes()
    browser = _Browser(_page(root))
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["buy"])
    browser.rerun()
    browser.edit({0: {"note": "只想给 T1 加的备注"}})
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, at.exception
    assert path.read_bytes() == before, list(store.load_trades(path)["note"])


def test_unsaved_edits_stay_put_while_the_same_rows_are_on_screen(tmp_path):
    """反过来不能矫枉过正：表里还是那几行时（在页面上别处点了点什么），没保存的改动得留着。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    browser = _Browser(_page(root))
    browser.edit({0: {"note": "先写着"}})
    browser.at.text_input(key=jui.SYMBOL_KEY).set_value("600519")      # 页面上别处的操作
    at = browser.rerun()
    assert any("改了 1 行" in note for note in _pending_notes(at)), _pending_notes(at)
    at = browser.rerun(click=jui.SAVE_KEY)
    assert list(store.load_trades(path)["note"]) == ["先写着", "", "", ""]


def test_a_saved_edit_does_not_come_back_as_unsaved(tmp_path):
    """只改不删的保存（行数没变）之后，编辑区必须是干净的：不许又冒出一句「还没保存」，
    也不许之后换一批记录时把这次的改动再套一遍。

    两批记录**行数相同**（买入两笔、卖出两笔）是这条测试的要害：行数不同的话，
    连带 key 的编辑器也会复位，测不出"改动跟着行号走"（复审指出初稿正是这样空跑的）。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    browser = _Browser(_page(root))
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["buy"])
    browser.rerun()
    browser.edit({0: {"reason": "改成别的理由"}})
    at = browser.rerun(click=jui.SAVE_KEY)
    assert list(store.load_trades(path)["reason"])[0] == "改成别的理由"
    saved = path.read_bytes()

    browser.at.text_input(key=jui.SYMBOL_KEY).set_value("600519")      # 之后在页面上随便动一下
    at = browser.rerun()
    assert _pending_notes(at) == [], "明明保存过了"
    assert at.button(key=jui.SAVE_KEY).proto.type == "secondary"
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])   # 换成另外两笔
    at = browser.rerun()
    assert _pending_notes(at) == [], "换了一批记录，保存过的那次改动不该跟过来"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert path.read_bytes() == saved, \
        f"保存过的那次改动又被套到了别的记录上: {list(store.load_trades(path)['reason'])}"


def test_typing_the_old_value_back_is_not_an_unsaved_change(tmp_path):
    """把原来的理由原样敲回去：没有东西要保存，就不该亮着「还没保存」和主按钮——
    否则点下去得到的是「没有需要保存的改动」，同一屏两句话打架。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {"reason": "20日线金叉"}})                  # 文件里本来就是这句
    assert _pending_notes(at) == [], _pending_notes(at)
    assert at.button(key=jui.SAVE_KEY).proto.type == "secondary"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert path.read_bytes() == before
    assert _pending_notes(at) == []


def test_restoring_the_previous_version_leaves_no_edit_behind(tmp_path):
    """恢复上一版换的是整张表：换之前没保存的改动不许留在编辑区里等着被再保存一次。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    browser.edit({0: {"reason": "第一次改"}})
    browser.rerun(click=jui.SAVE_KEY)                                  # 造出备份（上一版 = 没改之前）
    original = store.backup_path(path).read_bytes()
    browser.edit({1: {"note": "还没保存的备注"}})
    browser.at.checkbox(key=jui.RESTORE_CONFIRM_KEY).check()
    browser.rerun()
    at = browser.rerun(click=jui.RESTORE_KEY)
    assert path.read_bytes() == original, "前提：换回了上一版"
    at = browser.rerun()
    assert _pending_notes(at) == [], "换回上一版之后还挂着换之前的改动"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert path.read_bytes() == original


# ---------------------------------------------------------------- 复审之后补的几条（2026-11-16）

def test_whitespace_typed_into_a_blank_cell_is_announced_before_it_is_saved(tmp_path):
    """页面上的提醒与保存的结论必须一致：在空着的备注里敲了三个空格，保存会把它写进文件
    （并换掉备份）——那保存之前就得亮着「还没保存」，不能安安静静的。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    at = browser.edit({0: {"note": "   "}})
    assert any("改了 1 行" in note for note in _pending_notes(at)), _pending_notes(at)
    assert at.button(key=jui.SAVE_KEY).proto.type == "primary"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert any("改了 1 行" in t for t in _toasts(at)), _toasts(at)
    assert list(store.load_trades(path)["note"]) == ["   ", ""]


def test_a_blanked_delete_cell_does_not_take_the_table_down(tmp_path):
    """删除按钮那一列是只读的，正常情况下表格不会送回"这一格被清空"。真送回来了（2026-11-16 那一列
    还是勾选框时，选中它按 Delete 键就是这样，表格连同下面的保存、恢复、导出整个消失一轮）
    也不许出事：这一列的值不参与任何判断——不算改动，保存不动文件，更不会被当成"要删"。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {jui.DELETE_COLUMN: None}})
    assert not at.exception, at.exception
    assert [e for e in at.get("dataframe") if e.proto.id], "表格不见了"
    assert _pending_notes(at) == [], "那一格被清空不是一处改动"
    assert _dialog(at) is None
    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, at.exception
    assert path.read_bytes() == before


def test_clearing_a_date_and_saving_is_refused_in_words(tmp_path):
    """表里把日期清空再点保存：必须是那句设计好的拒绝（缺少成交日期、文件不动），
    不是一屏 traceback。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {"date": None}})
    assert not at.exception, at.exception
    assert any("改了 1 行" in note for note in _pending_notes(at)), "清空日期是一处改动"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, f"清空日期后保存不该崩页: {at.exception}"
    assert path.read_bytes() == before, "缺日期的行竟然落盘了"
    assert "成交日期" in "".join(str(e.value) for e in at.error), [e.value for e in at.error]


def test_a_refused_trade_does_not_cost_the_table_its_unsaved_edits(tmp_path):
    """表里改了一格、还没保存，这时去上面点了「记这一笔」（表单没填完，被拒）。
    点完之后那一轮：改动还在、提醒还在。初稿里提交完立刻整页重跑——重跑发生在表格**画出来之前**，
    后台就把表格这一轮的状态当成"控件不在了"丢掉，提醒少报一轮，而浏览器里那一格明明还是改过的。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    browser.edit({1: {"note": "还没保存的备注"}})
    at = browser.rerun(click=jui.SUBMIT_KEY)             # 表单是空的：这一笔会被拒
    assert path.read_bytes() == before, "前提：没记下来"
    assert "".join(str(e.value) for e in at.error), "前提：被拒了，有那句为什么"
    assert any("改了 1 行" in note for note in _pending_notes(at)), \
        f"点了一下「记这一笔」，表里没保存的改动就不提了: {_pending_notes(at)}"


def test_recording_does_not_drop_the_history_filter_for_a_round(tmp_path):
    """筛着「卖出」时去点「记这一笔」（被拒也一样）：点完之后表里还得是筛过的那几行。
    旧毛病——提交后在筛选框画出来之前就整页重跑，后台把筛选条件丢了一轮：
    表格变回全部记录，筛选框里却还写着「卖出」。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    browser.rerun()
    at = browser.rerun(click=jui.SUBMIT_KEY)             # 空表单：被拒
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T2"], f"筛选条件丢了一轮: {list(shown['trade_id'])}"
    assert at.multiselect(key=jui.FILTER_KIND_KEY).value == ["sell"]


def test_entry_summary_writes_the_price_out_in_full(tmp_path):
    """核对行不舍入：3.24567 就写 3.24567（初稿到第四位小数就四舍五入了，与"照输入的原样写"不符）。"""
    text = _summary(shares=100000.0, price=3.24567, fee=None)
    assert "× 3.24567 = 324,567.00 元" in text, text


def test_kind_labels_come_from_the_schema_names():
    """记录类型的中文名只有一份（schema.KIND_NAMES）：页面上的标签与校验信息里的叫法取自同一处，
    不会一个叫「股数调整」一个叫别的。"""
    assert jui.KIND_LABELS == dict(schema.KIND_NAMES)


def test_every_expander_on_the_entry_page_has_a_key():
    """折叠区必须带 key（真机上看到的）：页面顶上一冒出报错那一条，下面的元素整体往后挪一位，
    不带 key 的折叠区会被当成新的、**自己合上**——正填着「更多」里的佣金、正开着「筛选」，
    点一下「记这一笔」被拒，面板就在眼前关掉了。带 key 的折叠区认的是 key，不认位置。
    AppTest 看不到开合，只能在源码层钉住：这一页的每个 st.expander 都得有 key。"""
    import ast

    tree = ast.parse((ROOT / "app" / "pages_journal_entry.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "expander"]
    assert len(calls) == 4, f"这一页应有四个折叠区（说明 / 更多 / 其他记录 / 筛选），实际 {len(calls)}"
    for call in calls:
        keys = [kw for kw in call.keywords if kw.arg == "key"]
        assert keys, f"L{call.lineno} 的折叠区没有 key"
    assert len({ast.unparse(kw.value) for c in calls for kw in c.keywords if kw.arg == "key"}) == 4, \
        "四个折叠区的 key 不许重复"


def test_the_first_refusal_a_newcomer_sees_is_in_plain_chinese(tmp_path):
    """什么都没填就点「记这一笔」——第一次来的人最可能做的第一件事。那句拒绝里不许有 buy / shares。"""
    at = _page(_root(tmp_path))
    at = at.button(key=jui.SUBMIT_KEY).click().run()
    said = "".join(str(e.value) for e in at.error)
    assert "没有" in said and "股数" in said and "成交价" in said, said
    for key in ("buy", "shares", "price"):
        assert key not in said, f"报错里冒出了内部键 {key!r}: {said}"


# ================================================================ 删除：每行一个按钮 + 确认框（2026-11-18）
#
# 用户的话：「"删除"的交互有点太奇怪了：1. 为什么"删除"的选择框要放在第一列的位置
# 2. "先勾选要删除的记录、再点击保存"——很奇怪的交互」。
# 改之前删除是表里的一列勾选框：勾了什么也不发生，要去点一个叫「保存修改」的按钮才真的删。
# 现在每行最前面一个删除按钮：点了弹确认框、写明删的是哪一条，确认即删，不再经过「保存修改」。
# 防误删靠两样：确认框（点的人能核对是哪一条），和删之前照旧留下的上一版（「恢复上一版」换得回来）。
# 没变的纪律：落盘按记录号，不按"表里第几行"；备份只留一版，所以它什么时候会被换掉要说在前面。

def _three_trades(root: Path) -> Path:
    return _journal(root, _buy(), _sell(),
                    _buy(symbol="600519", name="贵州茅台", shares=100.0, price=1600.0,
                         source="discretionary", reason="跌到心理价位"))


def test_the_history_table_leads_with_a_delete_button_not_a_tick_box(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    editor = at.get("dataframe")[_log_table_index(at)]
    columns = json.loads(editor.proto.columns)
    kinds = {name: (c.get("type_config") or {}).get("type") for name, c in columns.items()}
    assert kinds[jui.DELETE_COLUMN] == "button", columns[jui.DELETE_COLUMN]
    assert "checkbox" not in kinds.values(), f"表里不该再有勾选框那一列: {kinds}"
    assert list(editor.proto.column_order)[0] == jui.DELETE_COLUMN
    assert list(editor.proto.button_click_widgets) == [jui.DELETE_COLUMN], "按钮列没有接上点击"
    assert list(editor.value[jui.DELETE_COLUMN]) == [jui.DELETE_LABEL] * 2
    assert _dialog(at) is None, "没点删除，确认框不许自己冒出来"


def test_the_confirmation_names_the_record_and_says_how_to_undo(tmp_path):
    """确认框里写的是**哪一条**（哪天、买还是卖、哪只票、多少股、什么价）和删错了怎么办。
    只写一句「确定删除吗」的确认框，点的人什么都核对不了。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _click_delete(_page(root), 0)
    assert not at.exception, at.exception
    guide = _guide()
    assert _dialog(at).proto.dialog.title == guide.JOURNAL_DELETE_TITLE
    said = _dialog_text(at)
    assert guide.JOURNAL_DELETE_ASK in said, said
    for part in ("2026-08-03", "买入", "美的集团（000333）", "1,000 股 × 71.50"):
        assert part in said, f"确认框里没写 {part}: {said}"
    assert "2026-08-24" not in said, "没点的那一条不该出现在确认框里"
    assert guide.JOURNAL_DELETE_UNDO in said and "「恢复上一版」" in guide.JOURNAL_DELETE_UNDO
    assert "还没保存" not in said and "换不回来" not in said, f"没有的事不许说: {said}"
    for key in ("buy", "T1", "trade_id", "ma_cross"):
        assert key not in said, f"确认框里冒出了 {key!r}: {said}"


def test_the_confirmation_ends_with_cancel_then_a_primary_delete_on_the_right(tmp_path):
    """确认框里两个按钮靠右，主按钮在最右（全站的规矩：操作按钮一律靠右）。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _click_delete(_page(root), 0)
    guide = _guide()
    cancel, confirm = at.button(key=jui.DELETE_CANCEL_KEY), at.button(key=jui.DELETE_OK_KEY)
    assert (cancel.label, cancel.proto.type) == (guide.JOURNAL_DELETE_CANCEL, "secondary")
    assert (confirm.label, confirm.proto.type, confirm.proto.icon) == \
        (guide.JOURNAL_DELETE_OK, "primary", ":material/delete:")
    row = element_path(at, jui.DELETE_OK_KEY)[-1]
    assert row is element_path(at, jui.DELETE_CANCEL_KEY)[-1], "两个按钮应在同一行"
    flex = row.proto.flex_container
    assert flex.direction == flex.Direction.HORIZONTAL and flex.justify == flex.Justify.JUSTIFY_END
    assert [k for e in _walk(row) if (k := getattr(e, "key", None))] == \
        [jui.DELETE_CANCEL_KEY, jui.DELETE_OK_KEY], "主按钮在最右"
    assert any(n is _dialog(at) for n in element_path(at, jui.DELETE_OK_KEY)), "按钮得在确认框里"


def test_the_confirmation_stays_open_through_reruns_it_did_not_ask_for(tmp_path):
    """确认框从点了删除按钮起一直在，直到回答或关掉。它一弹出就抢走焦点——上面表单里敲了字还没回车的
    输入框当场失焦提交，引出一次整页重跑；表格里刚改完没回车的那一格，一百多毫秒后也会引出一次。
    初稿让确认框"只在点击那一轮打开"，于是这两种再平常不过的状态下，框闪一下就没了，第一下删不动
    （独立审查在真实浏览器里抓到的；我自己复现：出现 87 毫秒后消失）。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _click_delete(_page(root), 1)
    assert "卖出" in _dialog_text(at), "前提：确认框开了，问的是第二条"

    at.text_input(key=jui.SYMBOL_KEY).set_value("600")          # 表单里那个没回车的输入框失焦提交
    at = at.run()
    assert not at.exception, at.exception
    assert _dialog(at) is not None, "别处引起的一次整页重跑，把确认框关掉了"
    assert "卖出" in _dialog_text(at) and "2026-08-24" in _dialog_text(at), "框还在，问的却不是那一条了"
    assert path.read_bytes() == before

    at = _answer(at)
    assert list(store.load_trades(path)["trade_id"]) == ["T1"]


def test_the_confirmation_catches_up_with_an_edit_that_lands_a_moment_later(tmp_path):
    """表格里刚改完一格、没按回车就去点另一行的删除按钮：那一格的改动要晚一百多毫秒才送到后台。
    确认框先开（那时还不知道有改动），改动一到，框里得补上「会一起保存」——并且真的一起保存。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    late = {0: {"note": "刚改完没回车"}}
    at = _click_delete(_page(root), 2)
    assert "还没保存" not in _dialog_text(at), "前提：改动还没到"

    at = edit_table(at, late, dataframe=_log_table_index(at))    # 改动到了（这一轮没有人点删除）
    assert not at.exception, at.exception
    assert _guide().JOURNAL_DELETE_ALSO_SAVES.format(n=1) in _dialog_text(at), _dialog_text(at)

    at = _answer(at, edits=late)
    saved = store.load_trades(path)
    assert list(saved["trade_id"]) == ["T1", "T2"] and list(saved["note"]) == ["刚改完没回车", ""]


def test_the_confirmation_stands_when_a_filter_hides_the_record_under_it(tmp_path):
    """筛选词敲了没回车就去点垃圾桶：确认框弹出的同时那个输入框失焦提交，表里换成筛过的行，
    点的那一条可能正好被筛了出去。它还好好地在账上——确认框问的仍然是它（真机上验过），确认了删的也是它。
    拿"表里现在还有没有它"去判断，就会对着一条明明还在的记录说「已经不在记录里了」。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    at = _click_delete(_page(root), 0)                                   # 点的是 T1（买入）
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])         # 筛选这时才提交
    at = at.run()
    assert not at.exception, at.exception
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T2", "T4"], "前提：表里已经没有点的那一条"
    assert _dialog(at) is not None and _toasts(at) == [], f"它还在账上，框却收了: {_toasts(at)}"
    assert "2026-08-03" in _dialog_text(at) and "买入" in _dialog_text(at), _dialog_text(at)

    at = _answer(at)
    assert list(store.load_trades(path)["trade_id"]) == ["T2", "T3", "T4"]


@pytest.mark.parametrize("confirm", [True, False])
def test_an_answered_confirmation_does_not_come_back(tmp_path, confirm):
    """答过了（删除或取消）就到此为止：之后页面再怎么重跑，它都不许自己又冒出来。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _delete(_page(root), 1, confirm=confirm)
    assert _dialog(at) is None and jui.DELETE_ASK_KEY not in at.session_state
    at.text_input(key=jui.SYMBOL_KEY).set_value("600519")
    at = at.run()
    assert not at.exception and _dialog(at) is None


def test_closing_the_confirmation_withdraws_the_request(tmp_path):
    """从右上角 ×、按 Esc、点框外关掉：等于取消。文件不动，之后重跑也不再回来。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _dismiss(_click_delete(_page(root), 1))
    assert not at.exception, at.exception
    assert _dialog(at) is None and jui.DELETE_ASK_KEY not in at.session_state
    assert path.read_bytes() == before and not store.backup_path(path).exists()
    at = at.run()
    assert _dialog(at) is None


def test_a_pending_request_is_dropped_when_the_ledger_cannot_be_read(tmp_path):
    """确认框挂着的时候文件读不出来了（页面只剩那句报错、提前返回）：那次请求作废，
    不许留到文件修好之后某一轮不请自来。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    good = path.read_bytes()
    at = _click_delete(_page(root), 0)
    assert _dialog(at) is not None, "前提：确认框开了"
    path.write_text("这不是一份交易记录\n", encoding="utf-8")
    at = at.run()
    assert at.error, "前提：这一轮文件读不出来"
    assert _dialog(at) is None and jui.DELETE_ASK_KEY not in at.session_state
    path.write_bytes(good)
    at = at.run()
    assert not at.exception and _dialog(at) is None


def test_leaving_the_page_withdraws_the_request(tmp_path):
    """确认框挂着的时候去了别的页（浏览器的后退键办得到）：那次请求作废，回来不许又弹出来。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _click_delete(_page(root), 0)
    assert _dialog(at) is not None, "前提：确认框开了"
    at = goto_page(at, REPORT_PAGE)
    assert not at.exception, at.exception
    assert _dialog(at) is None and jui.DELETE_ASK_KEY not in at.session_state
    at = goto_page(at, PAGE)
    assert _dialog(at) is None and path.read_bytes() == before


def test_the_confirmation_closes_itself_when_the_record_goes_away_under_it(tmp_path):
    """确认框开着的时候，另一个标签页把这一条删了：下一轮重跑时框自己收起来，说一句它已经不在了。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    at = _click_delete(_page(root), 1)
    assert _dialog(at) is not None, "前提：确认框开了"
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T2"], path)
    after = path.read_bytes()
    at = at.run()
    assert not at.exception, at.exception
    assert _dialog(at) is None and jui.DELETE_ASK_KEY not in at.session_state
    assert any(_guide().JOURNAL_DELETE_GONE in t for t in _toasts(at)), _toasts(at)
    assert path.read_bytes() == after


def test_a_click_on_a_row_that_is_not_there_is_ignored(tmp_path):
    """行号对不上（表格与点击之间数据变了）一律当没点过：按错位的行号去认，认到的是别人那一条。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    for row in (2, 7):                       # 表里两行：2 是越界的第一个行号，别在边上差一格
        at = _click_delete(_page(root), row)
        assert not at.exception, f"点第 {row} 行：{at.exception}"
        assert _dialog(at) is None and path.read_bytes() == before
        # 当没点过，就是什么都不说：不登记请求，也不冒出一句「这一条已经不在记录里了」
        assert jui.DELETE_ASK_KEY not in at.session_state and _toasts(at) == [], _toasts(at)


def test_the_click_is_resolved_against_the_table_that_was_on_screen(tmp_path):
    """这一页的表里是 T1、T2、T3，另一个标签页刚删了 T1。在这一页点第二行（T2）的删除按钮：
    确认框里必须是 T2。按"这一轮重新读到的表的第二行"去认，就成了 T3——点的人看着美的集团，
    确认框里写着贵州茅台还好，写错了没看出来就是删错。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    at = _page(root)
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T1"], path)        # 另一个标签页删了 T1
    at = _click_delete(at, 1)                                           # 屏幕上第二行是 T2
    said = _dialog_text(at)
    assert "卖出" in said and "美的集团" in said and "贵州茅台" not in said, said
    at = _answer(at)
    assert list(store.load_trades(path)["trade_id"]) == ["T3"]


def test_a_click_from_a_table_that_is_no_longer_on_screen_is_dropped(tmp_path):
    """后台已经把新的一批行画出去了，浏览器上那张旧表的点击才到（回车提交筛选之后几十毫秒内点垃圾桶
    就是这样——独立审查用真实的键盘鼠标事件复现过：确认框里写的是筛选后同一位置的另一条）。
    这一下点击必须作废，不许拿旧表的"第几行"去新表里认。做法与那张表不带 key 是同一个道理：
    按钮那一列的身份取自这一轮各行的记录号，表里换了内容，旧表上的按钮就不再是同一个控件。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    at = _page(root)
    stale = _delete_states(at, 0)                       # 旧表（T1、T2、T3）上点第一行的那一下
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T1"], path)
    before = path.read_bytes()
    at = at.run()                                       # 后台这边的表已经是 T2、T3
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T2", "T3"], "前提：后台已经换了一批行"

    at = at._run(widget_state=stale)                    # 这时旧表上的那一下点击才到
    assert not at.exception, at.exception
    assert _dialog(at) is None, f"旧表上的点击被拿去新表里认了: {_dialog(at) and _dialog_text(at)}"
    assert path.read_bytes() == before


def test_clicking_delete_on_a_record_that_is_already_gone_says_so(tmp_path):
    """另一个标签页刚把这一条删了，这一页的表里它还在。点它的删除按钮：不开确认框，说一句它已经不在了；
    别的什么都不动。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    at = _page(root)
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T2"], path)
    after = path.read_bytes()
    at = _click_delete(at, 1)
    assert not at.exception, at.exception
    assert _dialog(at) is None
    assert any(_guide().JOURNAL_DELETE_GONE in t for t in _toasts(at)), _toasts(at)
    assert path.read_bytes() == after


def test_deleting_leaves_every_other_record_exactly_as_it_was(tmp_path):
    """删掉中间一条：其余几条的内容与先后一个字不变——文件里就是少了那一行。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    at = _delete(_page(root), 1)
    assert not at.exception, at.exception
    assert path.read_text(encoding="utf-8-sig").splitlines() == [l for l in lines if not l.startswith("T2,")]


def test_deleting_with_a_filter_on_keeps_the_filter(tmp_path):
    """筛着「卖出」删一条：删完表里还得是筛过的（剩下的那一条卖出），筛选框里也还写着卖出。
    确认框是整页画完之后才开的——开早了，它里面那次整页重跑会让还没画出来的控件丢一轮状态
    （2026-11-16「记这一笔」栽过的同一个坑）。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    browser = _Browser(_page(root))
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    browser.rerun()
    at = browser.delete(0)
    assert not at.exception, at.exception
    assert list(store.load_trades(path)["trade_id"]) == ["T1", "T3", "T4"]
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T4"], f"筛选条件丢了一轮: {list(shown['trade_id'])}"
    assert at.multiselect(key=jui.FILTER_KIND_KEY).value == ["sell"]
    assert any("筛出 1 / 3 条" in c.value for c in at.main.caption), [c.value for c in at.main.caption]


def test_deleting_with_unsaved_edits_elsewhere_says_so_and_saves_them_too(tmp_path):
    """表里别的行上有没保存的改动时去删一条。删完表里的内容变了，表格会换成一张新的，
    那些改动留不住——所以确认框里当场说明"会一起保存"，确认之后一次写进去（一次写入、一份上一版）。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    before = path.read_bytes()
    edits = {0: {"note": "补一句备注"}}
    at = _click_delete(_page(root), 2, edits=edits)
    assert _guide().JOURNAL_DELETE_ALSO_SAVES.format(n=1) in _dialog_text(at), _dialog_text(at)
    assert path.read_bytes() == before, "还没确认"

    at = _delete(at, 2, edits=edits)
    assert not at.exception, at.exception
    saved = store.load_trades(path)
    assert list(saved["trade_id"]) == ["T1", "T2"] and list(saved["note"]) == ["补一句备注", ""]
    assert store.backup_path(path).read_bytes() == before, "删除与那处改动应是同一次写入"
    said = " ".join(_toasts(at))
    assert "已删除 1 条" in said and "保存了" in said and "1 行" in said, said


def test_unsaved_edits_on_the_record_being_deleted_are_not_announced(tmp_path):
    """正要删的那一条上没保存的改动不算数：删了，改动就没有意义——确认框里不提，也不会有东西被保存。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    edits = {2: {"note": "改了也白改"}}
    at = _click_delete(_page(root), 2, edits=edits)
    assert "还没保存" not in _dialog_text(at), _dialog_text(at)
    at = _delete(at, 2, edits=edits)
    assert list(store.load_trades(path)["note"]) == ["", ""]
    assert "保存了" not in " ".join(_toasts(at)), _toasts(at)


def _dialog_errors(at: AppTest) -> str:
    """确认框**里面**的报错（页面最上面的不算）。"""
    box = _dialog(at)
    assert box is not None, "确认框没开"
    return "".join(str(e.value) for e in _walk(box) if getattr(e, "type", "") == "error")


def test_a_broken_unsaved_edit_stops_the_delete_and_nothing_is_written(tmp_path):
    """别的行上没保存的改动是坏的（把股数清空了）。删除会连它一起写进去——那就整个不写：
    不删、不存，并说明是哪一行坏了。落盘之后盈亏会悄悄少算一笔，比删不成糟得多。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    before = path.read_bytes()
    at = _delete(_page(root), 2, edits={0: {"shares": None}})
    assert not at.exception, at.exception
    assert path.read_bytes() == before, "坏行竟然落盘了，或者那一条竟然被删了"
    assert not store.backup_path(path).exists()
    said = _dialog_errors(at)
    assert "没有删除" in said and "T1" in said and "股数" in said, said


def test_a_refused_delete_says_why_inside_the_confirmation(tmp_path):
    """删不成的时候，确认框不许一关了之。原因若是写在页面最上面（别的提示都在那儿），人正盯着
    页面下方的表格，看到的只是"点了删除、框关了、什么都没发生"（独立审查在真实浏览器里量的：
    那句红字在视口上沿之外）。所以框留着，原因就写在框里、两个按钮的上面。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    broken = {0: {"shares": None}}
    at = _delete(_page(root), 2, edits=broken)
    box = _dialog(at)
    assert box is not None, "删不成，确认框却关了"
    assert "没有删除" in _dialog_errors(at)
    assert not [e for e in at.main.error], f"原因该写在确认框里: {[e.value for e in at.main.error]}"
    order = [getattr(e, "key", None) or getattr(e, "type", "") for e in _walk(box)]
    assert order.index("error") < order.index(jui.DELETE_CANCEL_KEY), f"原因要在两个按钮上面: {order}"
    # 框还在，就还能答：这回点取消，一切照旧
    at = _answer(at, confirm=False, edits=broken)
    assert _dialog(at) is None and list(store.load_trades(path)["trade_id"]) == ["T1", "T2", "T3"]


def test_opening_and_cancelling_the_confirmation_keeps_the_unsaved_edits(tmp_path):
    """点了删除又取消：表里还是那几行，没保存的改动得留着（提醒也还亮着），之后照常保存得进去。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    browser.edit({0: {"note": "先写着"}})
    at = browser.delete(1, confirm=False)
    assert not at.exception, at.exception
    assert any("改了 1 行" in note for note in _pending_notes(at)), _pending_notes(at)
    browser.rerun(click=jui.SAVE_KEY)
    saved = store.load_trades(path)
    assert list(saved["trade_id"]) == ["T1", "T2"] and list(saved["note"]) == ["先写着", ""]


def test_the_second_delete_in_a_row_says_the_first_can_no_longer_come_back(tmp_path):
    """上一版只留一版。连着删两条：删第二条时上一版会换成"删掉第一条之后"的样子——第一条从此换不回来。
    这件事要在确认框里当场说，并写明是哪一条（还想要它，得先取消、去恢复）。
    第一次删的时候没有这回事，不许喊。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    at = _page(root)
    guide = _guide()
    assert "换不回来" not in _dialog_text(_click_delete(at, 0)), "第一次删：没有哪一条会因此换不回来"

    at = _delete(at, 0)                                  # 删掉 T1（2026-08-03 买入 美的集团）
    assert list(store.load_trades(path)["trade_id"]) == ["T2", "T3"], "前提：T1 删掉了"
    at = _click_delete(at, 1)                            # 再删 T3（贵州茅台）
    said = _dialog_text(at)
    assert guide.JOURNAL_DELETE_FADING.format(n=1) in said, said
    assert "换不回来" in guide.JOURNAL_DELETE_FADING and "「恢复上一版」" in guide.JOURNAL_DELETE_FADING
    asked, fading = said.index("贵州茅台"), said.index("美的集团")
    assert asked < said.index(guide.JOURNAL_DELETE_FADING.format(n=1)) < fading, \
        f"先说要删哪一条，再说哪一条会跟着换不回来: {said}"
    assert "2026-08-03 买入 美的集团（000333） 1,000 股 × 71.50" in " ".join(said[fading - 20:].split()), \
        f"换不回来的那一条也要写明是哪一条: {said}"


def test_no_warning_when_the_previous_version_holds_nothing_the_ledger_lacks(tmp_path):
    """上一版里没有"现在已经没有"的记录时（上一次是改了一格、或者刚恢复过），不提这回事。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    at = _page(root)
    at = edit_table(at, {0: {"note": "补一句"}}, dataframe=_log_table_index(at), click=jui.SAVE_KEY)
    assert store.backup_path(path).exists(), "前提：有上一版了"
    assert "换不回来" not in _dialog_text(_click_delete(at, 1))


def test_records_that_are_merely_filtered_out_are_not_said_to_be_lost(tmp_path):
    """筛着「卖出」删一条，上一版里四条都在：筛掉的那两条买入还在账上，确认框不许说它们
    "这次删除之后就换不回来了"——比的是整本日志，不是表里筛出来的那几行。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    at = _page(root)
    at = edit_table(at, {0: {"note": "补一句"}}, dataframe=_log_table_index(at), click=jui.SAVE_KEY)
    assert len(store.load_trades(store.backup_path(path))) == 4, "前提：上一版里四条都在"
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at = at.run()
    at = _click_delete(at, 0)
    assert "换不回来" not in _dialog_text(at), _dialog_text(at)


def test_deleting_the_last_record_leaves_the_empty_state_with_a_way_back(tmp_path):
    """删到一条不剩：页面回到空账本的样子，「恢复上一版」是亮着的——刚删光是最需要它的时刻。"""
    root = _root(tmp_path)
    path = _journal(root, _buy())
    one_row = path.read_bytes()
    at = _delete(_page(root), 0)
    assert not at.exception, at.exception
    assert len(store.load_trades(path)) == 0
    assert _guide().JOURNAL_EMPTY in _texts(at)
    assert any("已删除 1 条" in t for t in _toasts(at)), _toasts(at)
    assert not at.checkbox(key=jui.RESTORE_CONFIRM_KEY).disabled, "删光之后恢复不了"
    at.checkbox(key=jui.RESTORE_CONFIRM_KEY).check().run()
    at.button(key=jui.RESTORE_KEY).click().run()
    assert path.read_bytes() == one_row


def test_saving_no_longer_deletes_anything(tmp_path):
    """「保存修改」只写改动。删除有它自己的按钮和确认框——一个叫保存的按钮不该顺带删东西。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    at = _page(root)
    at = edit_table(at, {0: {"note": "补一句"}}, dataframe=_log_table_index(at), click=jui.SAVE_KEY)
    assert list(store.load_trades(path)["trade_id"]) == ["T1", "T2"]
    assert all("删除" not in t for t in _toasts(at)), _toasts(at)
    assert "删除" not in _guide().JOURNAL_EDIT_HINT and "勾" not in _guide().JOURNAL_EDIT_HINT


def _readme_edit_paragraph(text: str) -> str:
    """README「改与删」那一段。"""
    return next(line for line in text.splitlines() if line.startswith("**改与删**"))


def test_the_readme_describes_the_delete_button_not_the_tick_box():
    """README 那一段要跟着页面走：删除是每行一个按钮 + 确认框，不再是"勾一下再保存"。
    文档停在旧做法上，照着做的人会去表里找一列已经不存在的勾选框。"""
    said = _readme_edit_paragraph(README)
    for part in ("删除按钮", "确认框", "会一起保存", "换不回来", "on_delete_click"):
        assert part in said, f"README「改与删」一段没写到：{part}"
    assert "勾最左边" not in said and "勾了几行要删" not in said, "README 还在讲那一列勾选框"


# —— 落盘那一层（不经过页面）

def _flashes(monkeypatch) -> list[tuple[str, str]]:
    """把 journal_ui 留给下一轮的提示截下来（脱离页面时没有会话可存）。"""
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(jui, "flash", lambda level, text: said.append((level, text)))
    return said


def test_delete_trades_removes_by_record_id_and_leaves_the_previous_version(tmp_path, monkeypatch):
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    before = path.read_bytes()
    assert jui.delete_trades(["T2"], path=path, today=TODAY) is True
    assert list(store.load_trades(path)["trade_id"]) == ["T1", "T3"]
    assert store.backup_path(path).read_bytes() == before, "删之前的那一版得留着"
    assert [level for level, _ in said] == ["ok"], said
    assert "已删除 1 条" in said[0][1] and "「恢复上一版」" in said[0][1], said


def test_delete_trades_touches_nothing_when_the_record_is_already_gone(tmp_path, monkeypatch):
    """要删的记录不在了（另一个标签页刚删过）：这一次什么都不动——连上一版都不换——并如实说。
    一半在、一半不在也一样：只删认得出的那几条，等于替用户改了主意。"""
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    before = path.read_bytes()
    for ids in (["T9"], ["T1", "T9"]):
        assert jui.delete_trades(ids, path=path, today=TODAY) is False
        assert path.read_bytes() == before and not store.backup_path(path).exists()
    assert [level for level, _ in said] == ["error", "error"] and "不在" in said[0][1], said
    assert "T9" not in said[0][1] and "trade_id" not in said[0][1], f"这句话是给使用者看的: {said[0][1]}"


def test_delete_trades_touches_nothing_when_an_edited_record_is_already_gone(tmp_path, monkeypatch):
    """表里改过、要顺带保存的那一行，另一个标签页刚把它删了：同样整个不动、用人话说
    ——不许把「日志里没有这些 trade_id」这种话甩到页面上。"""
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    shown = jui.editor_frame(store.load_trades(path))
    edited = shown.copy()
    edited.loc[0, "note"] = "补一句"
    pending = jui.changed_rows(shown, edited, skip=["T2"])
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T1"], path)        # 另一个标签页删了 T1
    after, previous = path.read_bytes(), store.backup_path(path).read_bytes()
    assert jui.delete_trades(["T2"], path=path, today=TODAY, pending=pending) is False
    assert path.read_bytes() == after and store.backup_path(path).read_bytes() == previous
    level, text = said[0]
    assert level == "error" and "不在" in text, said
    for leaked in ("trade_id", "ValueError", "T1"):
        assert leaked not in text, f"这句话是给使用者看的，不该有 {leaked!r}: {text}"


def test_delete_trades_writes_the_unsaved_edits_of_other_rows_in_the_same_go(tmp_path, monkeypatch):
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    before = path.read_bytes()
    shown = jui.editor_frame(store.load_trades(path))
    edited = shown.copy()
    edited.loc[0, "note"] = "补一句"
    pending = jui.changed_rows(shown, edited, skip=["T2"])
    assert jui.delete_trades(["T2"], path=path, today=TODAY, pending=pending) is True
    saved = store.load_trades(path)
    assert list(saved["trade_id"]) == ["T1", "T3"] and list(saved["note"]) == ["补一句", ""]
    assert store.backup_path(path).read_bytes() == before, "一次写入、一份上一版"
    assert "已删除 1 条" in said[0][1] and "保存了" in said[0][1] and "1 行" in said[0][1], said


def test_delete_trades_refuses_everything_when_an_unsaved_edit_is_broken(tmp_path, monkeypatch):
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    before = path.read_bytes()
    shown = jui.editor_frame(store.load_trades(path))
    edited = shown.astype(object).copy()
    edited.loc[0, "shares"] = None
    pending = jui.changed_rows(shown, edited, skip=["T2"])
    assert len(pending) == 1, "前提：那一行算改过"
    assert jui.delete_trades(["T2"], path=path, today=TODAY, pending=pending) is False
    assert path.read_bytes() == before and not store.backup_path(path).exists()
    assert said[0][0] == "error" and "没有删除" in said[0][1], said


def test_delete_trades_reports_a_failed_write_and_leaves_the_file_alone(tmp_path, monkeypatch):
    """写盘失败必须如实说，不能弹一句"已删除"：用户会以为删掉了。"""
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("磁盘满了")
    monkeypatch.setattr(store, "save_trades", boom)
    assert jui.delete_trades(["T2"], path=path, today=TODAY) is False
    assert path.read_bytes() == before
    assert said[0][0] == "error" and "删除失败" in said[0][1] and "磁盘满了" in said[0][1], said


# ---------------------------------------------------------------- 写不成时说"没动"的那三句（2026-11-18）
#
# 记一笔、保存修改、删除，三条写入的路在写不成时各有一句话，说的都是"没动"。2026-11-18 之前这句话
# 只对了一半：日志确实没变，可上一版在写新内容之前就已经被换成了当前这一版——唯一的撤销手段没了，
# 页面上却没有一个字提到它。上面那条把整个 save_trades 换成了桩，看不到这件事；这里把桩打在最底下
# （把表写成文件的那一步），另存上一版与替换走的都是真的，再看两个文件是不是真的都没动。

def _write_by_recording(path: Path) -> bool:
    return jui.submit(_buy(symbol="600036", name="招商银行", shares=500.0, price=40.0),
                      path=path, costs=COSTS, today=TODAY)


def _write_by_saving_an_edit(path: Path) -> bool:
    shown = jui.editor_frame(store.load_trades(path))
    edited = shown.copy()
    edited.loc[0, "note"] = "补一句"
    return jui.save_edits(edited, path=path, today=TODAY)


def _write_by_deleting(path: Path) -> bool:
    return jui.delete_trades(["T1"], path=path, today=TODAY)


@pytest.mark.parametrize("write, verdict", [
    (_write_by_recording, "这一笔没有记下来"),
    (_write_by_saving_an_edit, "日志文件未被修改"),
    (_write_by_deleting, "记录没有被修改"),
], ids=["记一笔", "保存修改", "删除"])
def test_a_write_that_fails_leaves_the_journal_and_the_previous_version_as_they_were(
        tmp_path, monkeypatch, write, verdict):
    said = _flashes(monkeypatch)
    path = _three_trades(tmp_path)
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T3"], path)    # 再存一次：上一版 = 三条都在的那一版
    journal, previous = path.read_bytes(), store.backup_path(path).read_bytes()
    assert previous != journal, "前提：上一版与当前这版不一样，换没换才看得出来"

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(pd.DataFrame, "to_csv", disk_full)

    assert write(path) is False

    assert path.read_bytes() == journal, "页面说没动，日志却变了"
    assert store.backup_path(path).read_bytes() == previous, \
        "页面说没动，上一版却被换成了当前这一版：点「恢复上一版」再也换不回原来那一版"
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "trades.csv", "trades.csv.bak", "trades.csv.lock"]
    assert len(said) == 1, said
    level, text = said[0]
    assert level == "error" and verdict in text and "No space left" in text, said


def test_the_delete_plan_gathers_what_the_confirmation_has_to_say(tmp_path):
    """确认框要摆出来的三样东西在一处备齐：要删的那一条、表里别的行上没保存的改动、
    上一版里有而现在已经没有的记录。框里说的"另有几行会一起保存"与真正写进去的是同一份。"""
    path = _three_trades(tmp_path)
    trades = store.load_trades(path)
    shown = jui.editor_frame(trades)
    edited = shown.copy()
    edited.loc[0, "note"] = "补一句"
    edited.loc[1, "note"] = "要删的这一条也改了，不算数"
    rows, pending, fading = jui.delete_plan(["T2"], trades, (shown, edited), path)
    assert [row["trade_id"] for row in rows] == ["T2"] and rows[0]["kind"] == "sell"
    assert list(pending["trade_id"]) == ["T1"] and list(pending["note"]) == ["补一句"]
    assert fading == [], "还没有上一版"
    # 要删的那一条按记录号从**整本日志**里取：表里这时未必还有它（筛选在框开着的时候生效了）
    only_buys = jui.editor_frame(trades[trades["kind"] == "buy"])
    rows, pending, _ = jui.delete_plan(["T2"], trades, (only_buys, only_buys.copy()), path)
    assert [row["trade_id"] for row in rows] == ["T2"] and len(pending) == 0


def test_the_delete_plan_has_nothing_to_save_when_there_is_no_table(tmp_path):
    """筛没了、没有表可改的时候（确认框是之前开的，还挂着）：没有改动可言，照样问得出来。"""
    path = _three_trades(tmp_path)
    rows, pending, fading = jui.delete_plan(["T3"], store.load_trades(path), None, path)
    assert [row["trade_id"] for row in rows] == ["T3"]
    assert len(pending) == 0 and "trade_id" in pending.columns


def test_the_delete_plan_is_none_when_the_record_is_gone(tmp_path):
    """要删的记录里有一条已经不在了：没有可问的，交回 None（页面据此不开确认框、说一句）。"""
    path = _three_trades(tmp_path)
    trades = store.load_trades(path)
    assert jui.delete_plan(["T9"], trades, None, path) is None
    assert jui.delete_plan(["T1", "T9"], trades, None, path) is None


def test_the_delete_plan_measures_the_previous_version_against_the_whole_ledger(tmp_path):
    """"上一版里有、现在已经没有"是拿**整本日志**比出来的，不是拿表里筛出来的那几行：
    筛掉的记录还好好地在账上，说它们"这次删除之后就换不回来了"是一句吓人的假话。"""
    path = _three_trades(tmp_path)
    trades = store.load_trades(path)
    store.save_trades(trades, path)                                     # 造出上一版：三条都在
    shown = jui.editor_frame(trades[trades["kind"] == "sell"])          # 表里只筛出卖出那一条
    rows, pending, fading = jui.delete_plan(["T2"], trades, (shown, shown.copy()), path)
    assert fading == [], f"筛掉的记录被说成了换不回来: {[row['trade_id'] for row in fading]}"
    store.save_trades(trades[trades["trade_id"] != "T1"], path)         # 真删掉一条
    rows, pending, fading = jui.delete_plan(["T2"], store.load_trades(path), None, path)
    assert [row["trade_id"] for row in fading] == ["T1"]


def test_only_in_backup_is_what_a_restore_could_still_bring_back(tmp_path):
    """上一版里有、现在已经没有的记录——再写一次盘，它们就换不回来了。"""
    path = _three_trades(tmp_path)
    assert jui.only_in_backup(store.load_trades(path), path) == [], "还没有上一版"
    trades = store.load_trades(path)
    store.save_trades(trades[trades["trade_id"] != "T2"], path)        # 上一版 = 三条都在
    left = jui.only_in_backup(store.load_trades(path), path)
    assert [row["trade_id"] for row in left] == ["T2"] and left[0]["kind"] == "sell", left
    store.restore_backup(path)                                         # 换回来：现在三条，上一版两条
    assert jui.only_in_backup(store.load_trades(path), path) == []


def test_only_in_backup_shrugs_at_a_previous_version_it_cannot_read(tmp_path):
    """上一版读不出来（被手工改坏了）：这只是确认框里的一句提醒，不许因为它连删除都开不了。"""
    path = _three_trades(tmp_path)
    store.backup_path(path).write_text("symbol,kind\n600519,buy\n", encoding="utf-8")
    assert jui.only_in_backup(store.load_trades(path), path) == []


# ================================================================ 第二轮：减字、重排、按钮靠右（2026-11-16）
#
# 用户看过上面那一版之后提的三条：① 页面上常驻的解释性文字拿掉，用「？」提示、光标移上去
# 再弹出来；② 「记一笔」方向与四个输入框挤在一行，单选框后面空着一大截，看着别扭；
# ③ 操作按钮位置不统一——「记这一笔」在左下，别的页有的在右。
# AppTest 没有像素，下面钉的是元素树里看得见的东西：谁和谁同一行、第几栏、各栏权重、
# 容器朝哪边对齐，以及哪些话在明面上、哪些只在悬停说明里。

def _column_row(at: AppTest, key: str):
    """key 那个控件所在的（最里层）那一行分栏。"""
    rows = [n for n in element_path(at, key)
            if any(getattr(c, "type", "") == "column" for c in getattr(n, "children", {}).values())]
    assert rows, f"{key} 不在任何一行分栏里"
    return rows[-1]


def _row_layout(at: AppTest, key: str) -> list[tuple[list[str], float]]:
    """key 所在那一行分栏：每一栏里的控件 key（按先后）与这一栏的权重。"""
    return [([k for e in _walk(col) if (k := getattr(e, "key", None))], col.weight)
            for col in _column_row(at, key).children.values()]


def _page_order(at: AppTest) -> list[str]:
    """页面正文里的先后：带 key 的控件记 key，我们自己包的 HTML 记正文。"""
    out = []
    for e in _walk(at.main):
        if key := getattr(e, "key", None):
            out.append(key)
        elif (body := getattr(getattr(e, "proto", None), "body", None)) and "qd-" in body:
            out.append(body)
    return out


def test_direction_is_a_toggle_above_the_fields_not_one_of_them(tmp_path):
    """方向是表单最上面的一个「买入｜卖出」切换，不再是第一行里的一格。
    改之前它与四个输入框排成一行五格：单选框只占半格，后面空着一大截（用户原话：看着别扭）。"""
    at = _page(_root(tmp_path))
    kind = at.button_group(key=jui.KIND_KEY)
    assert kind.options == ["买入", "卖出"] and kind.value == "buy"
    assert kind.proto.required, "点已经选中的那个不许变成「都没选」"
    assert kind.proto.label_visibility.value == kind.proto.label_visibility.COLLAPSED, \
        "「买入｜卖出」自己就说清了这是什么，上面不必再顶一行「方向」"
    assert getattr(element_path(at, jui.KIND_KEY)[-1], "type", "") != "column", \
        "方向又被塞回某一行分栏里了"
    order = _page_order(at)
    assert order.index(jui.KIND_KEY) < order.index(jui.SYMBOL_KEY), "切换在四个输入框上面"


def test_the_first_row_is_four_equal_fields(tmp_path):
    """第一行只有每一笔都要填的四样，四格等宽：代码、成交日期、股数、成交价。"""
    at = _page(_root(tmp_path))
    layout = _row_layout(at, jui.SYMBOL_KEY)
    assert [keys for keys, _ in layout] == [[jui.SYMBOL_KEY], [jui.DATE_KEY],
                                            [jui.SHARES_KEY], [jui.PRICE_KEY]], layout
    assert len({weight for _, weight in layout}) == 1, f"四格应等宽: {layout}"


def test_the_why_row_lines_up_with_the_four_fields_above(tmp_path):
    """第二行：理由占上面四格里的前两格，来源与计划止损各占一格。
    **先对半分、右半边再对半分**，不是一行 2:1:1——栏间空隙是固定宽度，三栏两道空隙、
    四栏三道，2:1:1 的竖线会比上面那一行偏出几个像素（真机量过）；嵌套之后逐栏对齐。"""
    at = _page(_root(tmp_path))
    outer = _row_layout(at, jui.REASON_KEY)
    assert [keys for keys, _ in outer] == [[jui.REASON_KEY], [jui.SOURCE_KEY, jui.STOP_KEY]], outer
    assert outer[0][1] == outer[1][1], f"左右各半: {outer}"
    inner = _row_layout(at, jui.SOURCE_KEY)
    assert [keys for keys, _ in inner] == [[jui.SOURCE_KEY], [jui.STOP_KEY]], inner
    assert inner[0][1] == inner[1][1], f"右半边再对半: {inner}"


def test_an_empty_form_carries_no_standing_explanation(tmp_path):
    """什么都没填时，卡片上没有一行说明。改之前第一行下面常驻一句"填入 6 位代码后，这里会带出
    名称与该日的价格区间（都取自本地数据，离线可用）"——用户点名让拿掉的就是它。
    那句话现在在「代码」输入框自己的悬停说明里。"""
    at = _page(_root(tmp_path))
    assert [c.value for c in at.main.caption] == [], [c.value for c in at.main.caption]
    shown = [b for b in _page_order(at) if "qd-task-lead" in b or "qd-task-status" in b]
    assert shown == [], f"什么都没填，表单底部却有字: {shown}"
    tip = at.text_input(key=jui.SYMBOL_KEY).proto.help
    assert tip == _guide().JOURNAL_SYMBOL_HELP and "价格区间" in tip and "离线" not in tip


@pytest.mark.parametrize("key", [jui.SYMBOL_KEY, jui.DATE_KEY, jui.SHARES_KEY, jui.PRICE_KEY,
                                 jui.REASON_KEY, jui.SOURCE_KEY, jui.STOP_KEY,
                                 jui.OTHER_SYMBOL_KEY, jui.OTHER_DATE_KEY, jui.OTHER_SHARES_KEY,
                                 jui.OTHER_AMOUNT_KEY, jui.OTHER_SOURCE_KEY, jui.OTHER_REASON_KEY])
def test_field_titles_carry_no_explanations_in_brackets(tmp_path, key):
    """输入框的标题只说它是什么。改之前有四个标题带着括号说明（理由（为什么做这一笔）、
    股数变化（送股填正、缩股填负）……），那些话现在在悬停说明或示例文字里。"""
    at = _page(_root(tmp_path))
    widget = next(e for e in _walk(at.main) if getattr(e, "key", None) == key)
    assert "（" not in widget.label and "(" not in widget.label, widget.label


def test_the_check_lines_sit_in_the_footer_beside_the_submit_button(tmp_path):
    """核对那两行在表单底部左侧，紧挨着右下角的主按钮——点之前眼睛正好经过它。
    改之前它夹在第一行与第二行之间，按钮单独在左下角。"""
    at = _fill_entry(_page(_root(tmp_path)))
    left, right = _column_row(at, jui.SUBMIT_KEY).children.values()
    said = [b for e in _walk(left) if (b := getattr(getattr(e, "proto", None), "body", None))]
    assert len(said) == 1 and "qd-task-lead" in said[0] and "qd-task-status" in said[0], said
    assert "美的" in said[0] or "000333" in said[0], said        # 第一行：代码带出的名称
    assert "共付出 71,517.88 元" in said[0], said                # 第二行：会写进去的钱
    assert _keys_inside(right) == {jui.SUBMIT_KEY}
    order = _page_order(at)
    assert order.index(jui.FEE_KEY) < order.index(said[0]) < order.index(jui.SUBMIT_KEY), \
        "核对在「更多」之后、主按钮之前——它是表单的最后一样东西"


def test_the_submit_button_is_the_primary_one_in_the_bottom_right(tmp_path):
    at = _page(_root(tmp_path))
    button = at.button(key=jui.SUBMIT_KEY)
    assert (button.label, button.proto.type) == ("记这一笔", "primary")
    assert sits_on_the_right(at, jui.SUBMIT_KEY), "「记这一笔」应在表单右下角"
    card_keys = [k for k in _page_order(at) if k in (jui.KIND_KEY, jui.SYMBOL_KEY, jui.REASON_KEY,
                                                     jui.FEE_KEY, jui.SUBMIT_KEY)]
    assert card_keys[-1] == jui.SUBMIT_KEY, f"主按钮是表单里的最后一个控件: {card_keys}"


def test_other_records_form_is_laid_out_like_the_main_one(tmp_path):
    """「其他记录」与上面那张表单同一种排法：类型是最上面的一个切换，一行四格，按钮在右下角。
    改之前折叠区一打开先是三行灰字，那些话现在在两个输入框自己的悬停说明里。"""
    at = _page(_root(tmp_path))
    guide = _guide()
    box = next(e for e in at.expander if e.label.startswith("其他记录"))
    kind = at.button_group(key=jui.OTHER_KIND_KEY)
    assert kind.options == ["股数调整", "现金分红"] and kind.value == "adjust" and kind.proto.required
    layout = _row_layout(at, jui.OTHER_SYMBOL_KEY)
    assert [keys for keys, _ in layout] == [[jui.OTHER_SYMBOL_KEY], [jui.OTHER_DATE_KEY],
                                            [jui.OTHER_SHARES_KEY], [jui.OTHER_AMOUNT_KEY]], layout
    assert len({weight for _, weight in layout}) == 1, layout
    assert [keys for keys, _ in _row_layout(at, jui.OTHER_REASON_KEY)] == [
        [jui.OTHER_REASON_KEY], [jui.OTHER_SOURCE_KEY, jui.OTHER_SUBMIT_KEY]]
    assert [keys for keys, _ in _row_layout(at, jui.OTHER_SOURCE_KEY)] == [
        [jui.OTHER_SOURCE_KEY], [jui.OTHER_SUBMIT_KEY]]
    assert at.button(key=jui.OTHER_SUBMIT_KEY).label == "记这一条"
    assert sits_on_the_right(at, jui.OTHER_SUBMIT_KEY)
    assert not [e for e in _walk(box) if getattr(e, "type", "") == "caption"], "折叠区里又有常驻的灰字了"
    assert at.number_input(key=jui.OTHER_SHARES_KEY).proto.help == guide.JOURNAL_ADJUST_HELP
    # 正负号填反是这一页上不会报错的错：空着的框里摆一个示例，不能只靠悬停说明
    assert at.number_input(key=jui.OTHER_SHARES_KEY).proto.placeholder == "100 或 -50"
    assert at.number_input(key=jui.OTHER_AMOUNT_KEY).proto.help == guide.JOURNAL_DIVIDEND_HELP
    assert "不改成本总额" in guide.JOURNAL_ADJUST_HELP and "佣金" in guide.JOURNAL_DIVIDEND_HELP


def test_every_button_on_the_entry_page_sits_on_the_right(tmp_path):
    """操作按钮一律靠右（用户：位置要统一）。谁新加一个按钮顺手放在左边，这条就红。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])      # 让「清除筛选」也出现
    at = at.run()
    keys = action_button_keys(at)
    assert set(keys) == {jui.SUBMIT_KEY, jui.OTHER_SUBMIT_KEY, jui.FILTER_CLEAR_KEY, jui.SAVE_KEY,
                         jui.RESTORE_KEY, jui.CSV_KEY, jui.XLSX_KEY}, keys
    for key in keys:
        assert sits_on_the_right(at, key), f"{key} 不在右边"


def test_the_actions_under_the_table_are_one_row_ending_with_save(tmp_path):
    """历史表下面一行，从左到右：恢复上一版（确认框在它前面，先勾再点）、两个导出、保存修改
    ——主按钮在最右。改之前它们分三处：保存在表格左下，撤销在更下面一行的左边，导出在那一行右边。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    row = [jui.RESTORE_CONFIRM_KEY, jui.RESTORE_KEY, jui.CSV_KEY, jui.XLSX_KEY, jui.SAVE_KEY]
    assert [k for k in _page_order(at) if k in row] == row
    # 同一个靠右的横向容器里分两组：撤销一组，导出与保存一组。窄屏放不下时整组折行，
    # 不会把确认框和它的按钮、或者两个导出拆在两行上。
    groups = [element_path(at, key)[-1] for key in row]
    assert groups[0] is groups[1] and groups[2] is groups[3] is groups[4], "分组不对"
    assert groups[0] is not groups[2]
    for group in (groups[0], groups[2]):
        assert group.proto.width_config.use_content, "组要随内容宽，否则它自己就占满一行"
    outers = {id(element_path(at, key)[-2]) for key in row}
    assert len(outers) == 1, "两组应在同一个横向容器里"
    editor = at.get("dataframe")[_log_table_index(at)]
    order = [e for e in _walk(at.main)]
    assert order.index(editor) < order.index(at.button(key=jui.SAVE_KEY)), "这一行在表格下面"


def test_the_pending_note_appears_below_the_actions_and_moves_no_button(tmp_path):
    """有没保存的改动时那句提醒在按钮那一行的**下面**、靠右。它是改了第一格才冒出来的：
    放在按钮上方会把整行按钮顶下去——真实浏览器里量过，1024 宽下「恢复上一版」正好落到
    「保存修改」原来的位置上，确认框若恰好勾着，奔着保存去的那一下就成了恢复（独立审查抓到的）。
    所以钉两件事：提醒在那一行之后；五样东西的位置在提醒出现前后一模一样。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    row = [jui.RESTORE_CONFIRM_KEY, jui.RESTORE_KEY, jui.CSV_KEY, jui.XLSX_KEY, jui.SAVE_KEY]
    at = _page(root)
    quiet = {key: element_slot(at, key) for key in row}

    at = edit_table(at, {0: {"reason": "改了理由"}}, dataframe=_log_table_index(at))
    assert not at.exception, at.exception
    notes = [c for c in at.main.caption if "还没保存" in c.value]
    assert len(notes) == 1, [c.value for c in at.main.caption]
    assert {key: element_slot(at, key) for key in row} == quiet, "提醒一出现，按钮挪了位置"
    order = [e for e in _walk(at.main)]
    holder = element_path(at, jui.SAVE_KEY)[-2]            # 靠右的那一行（[-1] 是行里的一组）
    assert order.index(holder) < order.index(notes[0]), "提醒要在按钮那一行下面"
    # 靠右：它在一个靠右对齐的横向容器里（容器的对齐在元素树里看得见，文字自己的对齐看不见）
    box = path_to_element(at, notes[0])[-1].proto.flex_container
    assert box.direction == box.Direction.HORIZONTAL and box.justify == box.Justify.JUSTIFY_END
    assert at.button(key=jui.SAVE_KEY).proto.type == "primary"


def test_the_submit_button_does_not_move_when_the_check_lines_appear(tmp_path):
    """核对那两行是填完最后一格才冒出来的——人正把光标往「记这一笔」上移。那一行两栏必须
    **顶端对齐**：底端对齐时按钮会被它顶下去（真实浏览器里量过，1024 宽下核对折成四行，
    按钮下移 48 px）。AppTest 量不了像素，钉的是对齐方式与按钮在元素树里的位置不变。"""
    at = _page(_root(tmp_path))
    empty = element_slot(at, jui.SUBMIT_KEY)
    at = _fill_entry(at)
    assert _check_line(at), "前提：核对那两行出来了"
    assert element_slot(at, jui.SUBMIT_KEY) == empty
    for column in _column_row(at, jui.SUBMIT_KEY).children.values():
        align = column.proto.vertical_alignment
        assert align == column.proto.VerticalAlignment.TOP, "核对与按钮那一行要顶端对齐"


def test_the_action_row_survives_a_missing_excel_writer(tmp_path, monkeypatch):
    """导不出 Excel（缺那个库）时：只摘掉 Excel 按钮并如实说明，CSV、撤销、保存照常——
    这句说明现在与按钮同在一行里，页面不许因此出错（独立审查指出这条分支在页面上没人测）。"""
    def no_excel(frame):
        raise RuntimeError("导出 Excel 需要另装一个库")
    monkeypatch.setattr(export, "to_excel_bytes", no_excel)
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    assert not at.exception, at.exception
    keys = action_button_keys(at)
    assert jui.XLSX_KEY not in keys and {jui.CSV_KEY, jui.SAVE_KEY, jui.RESTORE_KEY} <= set(keys), keys
    said = [c for c in at.main.caption if "导出 Excel 需要" in c.value]
    assert len(said) == 1, [c.value for c in at.main.caption]
    assert path_to_element(at, said[0])[-1] is element_path(at, jui.CSV_KEY)[-1], \
        "那句说明应在导出那一组里，占着 Excel 按钮的位置"
    for key in keys:
        assert sits_on_the_right(at, key), key


def test_prefilled_form_keeps_one_short_warning_about_the_signal_day(tmp_path):
    """从「信号」页点「＋ 记一笔」过来：表单上方留一句话（预填的日期是信号日，不是成交日——
    照着记就是一个看着正常的错数）。只留要做的事，不再展开讲为什么。"""
    text = _guide().JOURNAL_PREFILLED.format(source="双均线交叉信号", day="2026-08-25")
    assert "信号日" in text and "实际成交那天" in text and len(text) < 70, text
    # 成交价与股数是空着的（不预填），所以说"填"不说"改"（独立审查指出初稿的"请按实际成交改"
    # 读着像它们已经填好了）
    assert "成交价与股数按实际成交填" in text, text


# ---------------------------------------------------------------- 「持仓与盈亏」页的减字
def _report_section(at: AppTest, title: str) -> str:
    heads = [b for e in at.get("html")
             if f'class="qd-section">{title}' in (b := e.proto.body)]
    assert len(heads) == 1, heads
    return heads[0]


def test_the_report_page_keeps_how_the_numbers_are_counted_in_the_dashboard_dot(tmp_path):
    """口径说明（为什么是元、与回测同口径、没有市价的怎么算）在「仪表盘」小标题后面的帮助圆点里。
    2026-11-16 之前它们是指标块上下常驻的三段灰字。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    head = _report_section(at, "仪表盘")
    tip = head.split('class="qd-help-tip">', 1)[1]
    assert "收益率" in tip and "同一套口径" in tip and "不按 0 算" in tip, tip
    assert "qd-help-wide" in head, "三段话，要用宽档的提示框"
    shown = "\n".join(c.value for c in at.main.caption)
    for gone in ("同一套口径", "金额一律是", "同一个函数"):
        assert gone not in shown, f"页面上又常驻着一句口径说明：{gone}"


def test_positions_without_a_price_are_still_called_out_in_one_short_line(tmp_path):
    """有持仓拿不到市价：这是事实、会影响上面的数怎么读，所以留在明面上——只留事实那半句，
    为什么与怎么办在圆点里。"""
    root = _root(tmp_path)
    _journal(root, _buy())                      # 只买不卖：一只持仓，tmp 里没有它的行情
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    line = _guide().JOURNAL_UNPRICED.format(n=1)
    assert line in [c.value for c in at.main.caption], [c.value for c in at.main.caption]
    assert len(line) < 30 and "跑一次" not in line, line


def test_report_tables_in_tabs_get_a_short_help_line_above_them(tmp_path):
    """标签页里的表没有小标题可挂帮助圆点：表格上方一行很短的「读表说明 ?」，话在圆点里。
    初稿把它们挂在列头上——列头的悬停说明没有问号可看，平仓明细要指的那一列还在横向滚动条
    外面（独立审查在真实浏览器里量的），等于没有。表格上方那行完整的灰字也不许回来。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    guide, theme = _guide(), app_module("theme")
    lines = [b for e in at.get("html") if 'class="qd-help-line"' in (b := e.proto.body)
             and not b.lstrip().startswith("<style>")]
    assert lines == [theme.help_line(guide.TABLE_HELP_LABEL, guide.TABLE_HINTS["closings"]),
                     theme.help_line(guide.TABLE_HELP_LABEL, guide.TABLE_HINTS["by_source"])], lines
    shown = [c.value for c in at.main.caption]
    assert guide.TABLE_HINTS["closings"] not in shown and guide.TABLE_HINTS["by_source"] not in shown
    # 每一行紧挨在它那张表的上面
    order = [e for e in _walk(at.main)]
    tables = list(at.get("dataframe"))
    assert len(tables) >= 2, "前提：平仓明细与来源对比两张表都画出来了"
    for body in lines:
        line = next(e for e in order if getattr(getattr(e, "proto", None), "body", None) == body)
        after = order[order.index(line) + 1]
        assert any(after is t for t in tables), "「读表说明」下面紧跟的应该是表格"


def test_an_empty_report_table_gets_no_help_line(tmp_path):
    """表格是空的时候不出「读表说明」：对着一句"还没有已平仓的交易"解释列只是噪声。"""
    root = _root(tmp_path)
    _journal(root, _buy())                      # 只买不卖：没有平仓明细，也没有来源对比
    at = _page(root, REPORT_PAGE)
    assert not at.exception, at.exception
    assert not [e for e in at.get("html") if 'class="qd-help-line"' in e.proto.body
                and not e.proto.body.lstrip().startswith("<style>")]


# ================================================================ 表格右上角那条浮动工具条的落脚处（2026-11-18）
#
# 历史表（st.data_editor）自带一条浮动工具条：右上角四个小图标（显示 / 隐藏列、下载、搜索、全屏）。
# 平时它是透明的，缩在表格与上一行之间那 1rem 的空隙里；鼠标停在表上、表里选中着一格、
# 或者鼠标从表格往上移经过它的时候，它显出来并上移——顶边比表格顶边高出 2.65rem（42.4 像素），
# 贴着表格右缘、宽约 106 像素。于是表格正上方靠右的控件被它盖住：有筛选生效时是「清除筛选」
# 下面约三分之二（点它的下半截没有反应，再偏一点就点开了表格的搜索框）；筛选框展开时是
# 「理由关键词」输入框的右下角。真实浏览器里复现过、逐点量过。
#
# 表格没有关掉这条工具条的开关，主题样式又只许选我们自己的 .qd-*（不能去藏它、挪它），
# 所以做法只有一个：**表格正上方靠右，不放可点的东西**——在会紧挨着表格的两处各垫一条空当
# （ui.toolbar_room）：「筛选」折叠区的最下面（只有展开时才看得见），和「清除筛选」那一行与表格之间
# （只有筛选生效时才有）。平时——折叠区收着、没有筛选——页面和原先一模一样。
#
# AppTest 没有像素。下面钉的是元素树里看得见的东西：谁紧挨在表格上面、空当垫在哪儿、垫的是哪一档；
# 几何关系（哪一档够高）另有两条：一条按 streamlit 自己的尺寸表算，一条去看装着的前端里
# 那个上移的距离还是不是 2.65rem。真实浏览器里的量法与结果在
# docs/superpowers/specs/2026-11-18-table-toolbar-room-design.md。

#: 工具条显出来时，顶边比表格顶边高出多少（rem）。streamlit 1.61.1 前端里写死的数，
#: 真实浏览器里量到的是 42.4 像素（根字号 16）。
TOOLBAR_RISE_REM = 2.65
#: 页面上相邻两个元素之间的空隙（rem）。同样是量出来的：16 像素。
ELEMENT_GAP_REM = 1.0

FILTER_KEYS = (jui.FILTER_START_KEY, jui.FILTER_END_KEY, jui.FILTER_SYMBOL_KEY,
               jui.FILTER_KIND_KEY, jui.FILTER_SOURCE_KEY, jui.FILTER_REASON_KEY)


def _log_table(at: AppTest):
    return at.get("dataframe")[_log_table_index(at)]


def _just_above(at: AppTest, target):
    """target 在它所在的那一块里，紧挨着的上一个元素（它已经是第一个时返回 None）。"""
    siblings = list(path_to_element(at, target)[-1].children.values())
    index = next(i for i, child in enumerate(siblings) if child is target)
    return siblings[index - 1] if index else None


def _is_room(element) -> bool:
    return getattr(element, "type", "") == "space"


def _filtered_page(tmp_path, **filters) -> AppTest:
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    for key, value in filters.items():
        widget = next(e for e in _walk(at.main) if getattr(e, "key", None) == key)
        widget.set_value(value)
    return at.run() if filters else at


def test_the_clear_button_is_kept_out_from_under_the_table_toolbar(tmp_path):
    """有筛选生效时，「清除筛选」那一行与表格之间垫着一条空当。没有它，按钮下面约三分之二
    会被表格右上角抬起来的工具条盖住：点过表里的格子之后再去点「清除筛选」的下半截，筛选还在。"""
    at = _filtered_page(tmp_path, **{jui.FILTER_KIND_KEY: ["sell"]})
    assert not at.exception, at.exception
    room = _just_above(at, _log_table(at))
    assert _is_room(room), f"表格正上方应该是一条空当，实际是 {getattr(room, 'type', room)}"
    row = _just_above(at, room)
    assert jui.FILTER_CLEAR_KEY in _keys_inside(row), "空当上面紧挨着的应该是「清除筛选」那一行"
    # 版式里别的都没动：按钮还在那一行的右端，「筛出几条」还在它左边
    assert sits_on_the_right(at, jui.FILTER_CLEAR_KEY)
    assert [c.value for c in _walk(row) if getattr(c, "type", "") == "caption"] == [
        _guide().JOURNAL_FILTERED.format(shown=1, total=2, summary="类型 卖出")]


def test_the_filter_box_ends_with_room_for_the_table_toolbar(tmp_path):
    """「筛选」折叠区的最下面垫着一条空当：展开而又没有筛选生效时，紧挨在表格上面的就是那六个
    筛选框——最右边的「理由关键词」右下角会被工具条盖住。垫在折叠区**里面**，收起来时它不占地方。"""
    at = _filtered_page(tmp_path)
    box = _expander(at, _guide().JOURNAL_FILTER)
    children = list(box.children.values())
    assert _is_room(children[-1]), \
        f"折叠区里最后一样应该是空当，实际是 {[getattr(c, 'type', '?') for c in children]}"
    inside = list(_walk(box))
    for key in FILTER_KEYS:
        widget = next(e for e in inside if getattr(e, "key", None) == key)
        assert inside.index(widget) < inside.index(children[-1]), f"{key} 排到空当后面去了"
    assert [e for e in inside if _is_room(e)] == [children[-1]], "折叠区里只垫这一条"


def test_without_a_filter_the_table_still_sits_right_under_the_filter_box(tmp_path):
    """平时（没有筛选生效）页面和原先一样：表格紧跟在「筛选」折叠区下面，中间不多留空。
    折叠区收着的时候，表格正上方靠右没有控件可盖——为一条偶尔才出现的工具条天天空着一截，不值。"""
    at = _filtered_page(tmp_path)
    above = _just_above(at, _log_table(at))
    assert above is _expander(at, _guide().JOURNAL_FILTER), \
        f"表格上面紧挨着的应该是「筛选」折叠区，实际是 {getattr(above, 'type', above)}"
    rooms = [e for e in _walk(at.main) if _is_room(e)]
    assert len(rooms) == 1, "没有筛选时，整页只有折叠区里那一条空当"


def test_filtering_everything_away_leaves_no_gap_for_a_table_that_is_not_there(tmp_path):
    """筛到一条不剩：没有表格，也就没有工具条——那句「没有符合条件的记录」紧跟在
    「清除筛选」那一行下面，不垫空当。"""
    at = _filtered_page(tmp_path, **{jui.FILTER_REASON_KEY: "根本没有这个词"})
    assert not at.exception, at.exception
    said = next(e for e in _walk(at.main)
                if getattr(e, "type", "") == "markdown" and e.value == _guide().JOURNAL_FILTER_NONE)
    assert jui.FILTER_CLEAR_KEY in _keys_inside(_just_above(at, said))
    box = _expander(at, _guide().JOURNAL_FILTER)
    outside = [e for e in _walk(at.main) if _is_room(e) and not any(e is i for i in _walk(box))]
    assert outside == [], "没有表格，却在折叠区外面垫了空当"


def test_every_gap_left_for_the_table_toolbar_is_the_agreed_size(tmp_path, monkeypatch):
    """两处空当垫的是同一档（ui.TOOLBAR_ROOM），而且这一档够高：上下各一道元素间的空隙
    加上它，不小于工具条上移的距离。谁把它调小一档，工具条就又压到按钮上了。"""
    from streamlit.elements.lib.layout_utils import SIZE_TO_REM_MAPPING

    sizes: list = []
    monkeypatch.setattr(st, "space", lambda size="small": sizes.append(size))
    at = _filtered_page(tmp_path)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    sizes.clear()                        # 只数筛选生效的这一轮
    at = at.run()
    assert not at.exception, at.exception
    room = app_module("ui").TOOLBAR_ROOM
    assert len(sizes) == 2, f"有筛选时垫两处（折叠区里面、表格上面），实际 {len(sizes)} 处"
    assert set(sizes) == {room}, sizes
    clearance = ELEMENT_GAP_REM + SIZE_TO_REM_MAPPING[room] + ELEMENT_GAP_REM
    assert clearance >= TOOLBAR_RISE_REM, \
        f"空当只有 {clearance}rem，工具条要上移 {TOOLBAR_RISE_REM}rem"
    # 也别垫得太多：再大一档就是 2.5rem，表格上方会空出一大截
    assert SIZE_TO_REM_MAPPING[room] < 1.0, room


def test_streamlit_still_lifts_the_table_toolbar_by_the_distance_we_measured():
    """绊线：空当的高度是照着 streamlit 前端里的一个数留的——工具条显出来时 `top: -2.65rem`
    （平时 `-1rem`）。这个数没有任何公开接口，升级 streamlit 时它要是变了（抬得更高），
    工具条会悄悄地又压回按钮上，上面那些测试一条都不会红。所以直接去装着的前端里看一眼。

    这条红了不等于页面坏了，只说明"当初量的那个数不作数了"：在真实浏览器里把
    docs/superpowers/specs/2026-11-18-table-toolbar-room-design.md 里的量法重做一遍，
    再回来改 TOOLBAR_RISE_REM 与 ui.TOOLBAR_ROOM。
    """
    static = Path(st.__file__).parent / "static"
    marker = "top 100ms 100ms"           # 工具条"显出来"那条过渡，整个前端里只有它这么写
    chunks = [text for path in static.rglob("*.js")
              if marker in (text := path.read_text(encoding="utf-8", errors="ignore"))]
    assert chunks, f"在 {static} 里找不到表格工具条的样式了：streamlit 的前端换了写法，重新量一遍"
    offsets: set[str] = set()
    for text in chunks:
        at = text.index(marker)
        offsets |= set(re.findall(r"-\d+(?:\.\d+)?rem", text[max(0, at - 600):at + 600]))
    assert offsets == {f"-{TOOLBAR_RISE_REM}rem", f"-{ELEMENT_GAP_REM:g}rem"}, \
        f"工具条上移的距离变了（现在是 {sorted(offsets)}）：重新量一遍，再改空当的高度"


# ================================================================ 被拦下时那句话里的"程序话"（2026-11-18）
#
# 历史表里把「类型」那一格清空（选中它按删除键）再点保存，被拦下是对的，可页面上那句话是
# 「无法解释的记录类型 'nan'，只支持 buy/sell/adjust/dividend」：'nan' 是空格子在程序里的样子，
# 使用者没填过这个词；后面四个是存进文件的键，页面上别处都叫买入 / 卖出 / 股数调整 / 现金分红。
# 这句话有三个地方会看到：页面顶上「没有保存」那一条、删除确认框里「没有删除，也没有保存」那一段、
# 「记一笔」表单的报错（表单上类型是个必选的切换，填不出空值，那一处由 schema 层的测试管）。

def _assert_no_program_words(said: str) -> None:
    """给使用者看的那句拒绝里不许有：空格子在程序里的样子（nan / None / NaT）、存进文件的类型键。"""
    assert said, "前提：有那句拒绝"
    for word in ("nan", "none", "nat"):
        assert not re.search(rf"(?<![a-z]){word}(?![a-z])", said, re.IGNORECASE), \
            f"拒绝的话里冒出了 {word!r}: {said}"
    for key in schema.KINDS:
        assert key not in said, f"拒绝的话里冒出了存进文件的键 {key!r}: {said}"


def test_clearing_a_kind_and_saving_is_refused_in_the_pages_own_words(tmp_path):
    """清空类型再点保存：文件不动，那句话说的是这一行缺类型、可以选哪几种（页面上的名字）。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {"kind": None}})
    assert not at.exception, at.exception
    assert any("改了 1 行" in note for note in _pending_notes(at)), "清空类型是一处改动"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, f"清空类型后保存不该崩页: {at.exception}"
    assert path.read_bytes() == before, "没有类型的行竟然落盘了"
    said = "".join(str(e.value) for e in at.error)
    assert "没有保存" in said and "类型" in said, said
    # 是哪一行：记录号（日期 代码）。表格交回来的日期带着时分秒，写出来只到日。
    assert "T1（2026-08-03 000333）：" in said, said
    _assert_no_program_words(said)
    for name in jui.KIND_LABELS.values():
        assert name in said, f"没告诉他可以选「{name}」: {said}"


def test_a_cleared_kind_stops_the_delete_in_the_pages_own_words(tmp_path):
    """确认框里那一段「没有删除，也没有保存」用的是同一句校验的话（删除会连同表里没保存的改动一起
    保存，所以也过校验）：别的行上把类型清空了再去删，框里说的是缺类型——没有 nan，没有存进文件的键。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    before = path.read_bytes()
    at = _delete(_page(root), 2, edits={0: {"kind": None}})
    assert not at.exception, at.exception
    assert path.read_bytes() == before, "没有类型的行竟然落盘了，或者那一条竟然被删了"
    said = _dialog_errors(at)
    assert "没有删除" in said and "T1" in said and "类型" in said, said
    _assert_no_program_words(said)


def test_a_refused_row_is_not_introduced_as_none(tmp_path):
    """被拦下的每一行，前面有一段「是哪一行」：记录号（日期 代码）。清空的正是日期那一格时，
    这一段原先是「T1（None 000333）」。空着的那一样不写，剩什么写什么。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    browser.edit({0: {"date": None}})
    at = browser.rerun(click=jui.SAVE_KEY)
    said = "".join(str(e.value) for e in at.error)
    assert "T1（000333）：" in said and "成交日期" in said, said
    _assert_no_program_words(said)


@pytest.mark.parametrize("cells, intro", [
    ({}, "T1（2026-08-03 000333）："),
    ({"date": pd.NaT}, "T1（000333）："),
    ({"symbol": float("nan")}, "T1（2026-08-03）："),
    ({"symbol": "   "}, "T1（2026-08-03）："),
    ({"symbol": " 000333 "}, "T1（2026-08-03 000333）："),
    ({"date": None, "symbol": None}, "T1："),
], ids=["都在", "日期空着", "代码是清空的格子", "代码只有空白", "代码两头带空白", "都空着"])
def test_a_refused_row_is_named_by_whatever_it_still_has(cells, intro):
    """那一段「是哪一行」逐种情形钉住：日期、代码哪个空着就不写哪个，都空着就只剩记录号——
    不写「None」「nan」，也不留一对空括号或者括号里一个孤零零的空格。"""
    row = {**_record(shares=None), "trade_id": "T1", **cells}      # 缺股数：保证这一行一定被拦下
    lines = jui.blocking_rows(pd.DataFrame([row]).astype(object), today=TODAY)
    assert len(lines) == 1 and lines[0].startswith(intro), lines
    _assert_no_program_words(lines[0])


def test_the_readme_quotes_the_missing_kind_refusal_as_the_page_says_it():
    """README 里引的那句「缺少记录类型（……）」得是页面上真会出现的那一句：句子再改，文档跟着红。"""
    [issue] = [i for i in schema.validate_trade(_buy(kind=float("nan")), today=TODAY)
               if i.field == "kind"]
    assert f"「{issue.message}」" in README, issue.message
