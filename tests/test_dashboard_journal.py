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
import importlib.util
import json
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
from tests.conftest import (app_module, click_row_button, copy_app, edit_table, goto_page,
                            make_bars)

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


def test_oversell_warning_is_visible_and_does_not_break_the_page(tmp_path):
    """卖超（漏记了买入）：设计 §3 要求"警告并在持仓页持续标红"，但不阻断、不崩页。"""
    root = _root(tmp_path)
    _journal(root, _buy(shares=100.0), _sell(shares=500.0))
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    warnings = "\n".join(str(w.value) for w in at.warning)
    assert "卖出" in warnings and "配得上" in warnings, warnings


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


def test_dashboard_renders_all_three_charts_when_there_is_data(tmp_path):
    """曲线 + 占比在仪表盘并排，来源对比图在「来源对比」tab 里——共三张。"""
    root = _root(tmp_path)
    _half_closed(root)
    at = _page(root, REPORT_PAGE)

    assert not at.exception, at.exception
    assert len(at.get("plotly_chart")) == 3, "该有曲线/占比/来源对比三张图"


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
    text = _texts(at)
    assert "还没有平仓记录——第一笔卖出后这里会出现你的已实现盈亏曲线" in text, text


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
    at.radio(key=jui.KIND_KEY).set_value(kind)
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
    at.radio(key=jui.OTHER_KIND_KEY).set_value("dividend")
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
    assert "上一版" in text, f"没说清有「上一版」可以换回：{text}"
    button = at.button(key=jui.RESTORE_KEY)
    assert (button.label, button.proto.icon) == ("恢复上一版", ":material/undo:")
    assert button.disabled, "没勾确认之前按钮必须是禁用的（两步纪律，同删除）"
    assert not at.checkbox(key=jui.RESTORE_CONFIRM_KEY).disabled


def test_the_restore_button_is_disabled_and_explained_before_the_first_save(tmp_path):
    """一笔都没记过、也没有任何备份的时候：按钮在（让人知道有这回事），但禁用，
    tooltip 说清为什么——而不是点下去报错。"""
    root = _root(tmp_path)
    at = _page(root)

    assert not at.exception, at.exception
    assert "上一版" in _texts(at)
    button = at.button(key=jui.RESTORE_KEY)
    assert button.disabled
    assert "第一次保存" in button.proto.help, button.proto.help
    assert at.checkbox(key=jui.RESTORE_CONFIRM_KEY).disabled


def test_restoring_swaps_the_journal_with_its_backup_end_to_end(tmp_path):
    """端到端：删一行并保存（造出备份）→ 勾确认 → 点恢复 → 日志回到两行、
    备份里躺着刚才那一行的版本；确认框复位；再来一次就换回去。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    two_rows = path.read_bytes()
    at = _page(root)
    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)
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


def test_saving_edits_really_leaves_a_backup_behind(tmp_path):
    """端到端：在页面上删掉一行并保存 → 上一版仍然躺在 .bak 里。
    这条走的是最危险的那条路（整表重写），也是页面上那行小字承诺的东西。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)

    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

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


def test_marking_a_row_for_deletion_and_saving_removes_it(tmp_path):
    """删除是两步（勾选 + 保存）。一键删除对一份不可再生的文件太危险：
    误点一下就没了，而 git 里那次改动还没提交。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    saved = store.load_trades(_trades_path(root))
    assert list(saved["trade_id"]) == ["T1"], saved


def test_ticking_delete_without_saving_changes_nothing(tmp_path):
    """勾了但没点保存 → 文件一个字节都不许动。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)
    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}}, dataframe=_log_table_index(at))

    assert not at.exception, at.exception
    assert path.read_bytes() == before


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
    assert at.radio(key=jui.KIND_KEY).value == "buy"
    assert at.selectbox(key=jui.SOURCE_KEY).value == "ma_cross"
    assert "美的集团" in _texts(at)
    assert at.number_input(key=jui.PRICE_KEY).value is None, "价格不该被预填"


def test_recording_a_sell_signal_prefills_the_sell_direction(tmp_path):
    root = _root(tmp_path)
    _signals(root, "2026-08-24,000333,donchian,sell,71.5\n")
    at = _page(root, SIGNALS_PAGE)
    at = click_row_button(at, jui.RECORD_COLUMN, 0, jui.RECORD_LABEL)

    assert not at.exception, at.exception
    assert at.radio(key=jui.KIND_KEY).value == "sell"
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


def test_split_editor_result_separates_edits_from_deletions():
    edited = pd.DataFrame({"trade_id": ["T1", "T2", "T3"],
                           "reason": ["甲", "乙", "丙"],
                           jui.DELETE_COLUMN: [False, True, False]})
    keep, deleted = jui.split_editor_result(edited)

    assert deleted == ["T2"]
    assert list(keep["trade_id"]) == ["T1", "T3"]
    assert jui.DELETE_COLUMN not in keep.columns, "勾选列不许流进日志文件"


def test_split_editor_result_treats_a_blank_tick_as_not_deleted():
    """勾选列可能是 None（编辑器交回来的空格）。当成"删"就会删掉没勾的行。"""
    edited = pd.DataFrame({"trade_id": ["T1"], jui.DELETE_COLUMN: [None]})
    keep, deleted = jui.split_editor_result(edited)
    assert deleted == [] and list(keep["trade_id"]) == ["T1"]


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


def test_positions_page_warns_about_holdings_outside_the_pool(tmp_path):
    """项目自己把这件事称为「最容易踩的坑」（不加进 universe 的票没有任何人管它的
    卖出），而唯一知道你持着什么的这一页之前从不看信号池。

    典型剧本：扫描报了 BUY，买了、记了账，但忘了点 ＋ 加进池子；此后「信号跟踪」
    每天替你盯的是另一批标的，而它不会报错。
    """
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="000333"), _buy(symbol="600519", name="贵州茅台"))
    _set_pool(tmp_path, ("600519",))          # 000333 持着但不在池子里

    at = _page(tmp_path, REPORT_PAGE)

    assert not at.exception, at.exception
    warns = [w.value for w in at.main.warning]
    assert any("000333" in w and "不在信号池" in w for w in warns), warns
    assert not any("600519" in w and "不在信号池" in w for w in warns), \
        "池子里的那只被误报了"


def test_positions_page_stays_quiet_when_everything_is_tracked(tmp_path):
    """全在池内就一个字都不说——这一页的告警区排在所有数字之前，
    到处都是黄框等于没有黄框。"""
    _root(tmp_path)
    _journal(tmp_path, _buy(symbol="600519", name="贵州茅台"))
    _set_pool(tmp_path, ("600519",))

    at = _page(tmp_path, REPORT_PAGE)

    assert not at.exception, at.exception
    assert not any("不在信号池" in w.value for w in at.main.warning), \
        [w.value for w in at.main.warning]


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


def test_pending_changes_counts_edits_and_ticked_deletions_separately():
    shown = _shown(_buy(), _sell(), _buy(symbol="600519", shares=100.0, price=1600.0),
                   _sell(symbol="600519", shares=100.0, price=1700.0))
    edited = shown.copy()
    edited.loc[0, "reason"] = "改了"
    edited.loc[2, jui.DELETE_COLUMN] = True
    edited.loc[3, "price"] = 10.0
    edited.loc[3, jui.DELETE_COLUMN] = True
    assert jui.pending_changes(shown, edited) == (1, 2), \
        "又改又勾删除的那一行算删除——删了，改动就没有意义"


def test_pending_changes_is_about_values_not_about_what_was_touched():
    """把原来的值又敲了回去、勾上又取消：表里的值和文件里的一样，就没有东西要保存。
    审查时抓到的：按"编辑器记了哪些格子被碰过"来算，这两种都会报「还没保存」，
    而点下去得到的是一句「没有需要保存的改动」——同一屏两句话打架。"""
    shown = _shown(_buy(), _sell())
    edited = shown.copy()
    edited.loc[0, "reason"] = shown.loc[0, "reason"]            # 原值敲回去
    edited.loc[1, jui.DELETE_COLUMN] = False                    # 勾上又取消
    assert jui.pending_changes(shown, edited) == (0, 0)


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
    assert jui.pending_changes(shown, edited) == (0, 0)
    edited.loc[1, "date"] = D_SELL - timedelta(days=1)          # 真改了一天
    assert jui.pending_changes(shown, edited) == (1, 0)


def test_pending_changes_of_an_untouched_editor_is_zero():
    shown = _shown(_buy(), _sell())
    assert jui.pending_changes(shown, shown.copy()) == (0, 0)
    assert jui.pending_changes(shown.iloc[0:0], shown.iloc[0:0]) == (0, 0)


def test_pending_changes_measures_with_the_same_ruler_as_the_save():
    """提醒与保存必须是同一把尺（复审抓到的不一致）：保存时文字**原样**落盘、不去空白，
    空着的数字与 0 是两回事。初稿的提醒把"只敲了几个空格""空的改成 0"当成没改——
    按钮安安静静，点下去却是「已保存：改了 1 行」，备份也跟着换了一版。"""
    shown = _shown(_buy(), _sell())
    spaces = shown.copy()
    spaces.loc[0, "note"] = "   "                                  # 本来是空串
    assert jui.pending_changes(shown, spaces) == (1, 0)
    trailing = shown.copy()
    trailing.loc[0, "reason"] = shown.loc[0, "reason"] + " "       # 末尾多一个空格
    assert jui.pending_changes(shown, trailing) == (1, 0)
    zero = shown.copy()
    zero.loc[0, "stop_plan"] = 0.0                                 # 本来没填
    assert jui.pending_changes(shown, zero) == (1, 0)
    # 而"同一个空"的不同写法仍然不算改动
    blank = shown.astype(object).copy()
    blank.loc[0, "note"] = None
    blank.loc[0, "stop_plan"] = float("nan")
    assert jui.pending_changes(shown, blank) == (0, 0)


def test_pending_changes_would_rather_say_nothing_than_guess():
    """两张表对不上（行数不同、交回来的表没有「删除」列）——固定行数的编辑器不会这样，
    真遇上了也只是不提醒：这句话是提示，不许因为它把整页打崩，更不许乱报一个数。"""
    shown = _shown(_buy(), _sell())
    assert jui.pending_changes(shown, shown.iloc[:1].copy()) == (0, 0)
    assert jui.pending_changes(shown.iloc[:1], shown.copy()) == (0, 0)
    assert jui.pending_changes(shown, shown.drop(columns=[jui.DELETE_COLUMN])) == (0, 0)


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
    """备份只留一版，每次保存都会把它换成"保存前的样子"。所以一次**什么都没改**的保存
    不是无害的：删错一行并保存之后，手一抖再点一下「保存修改」，备份就被换成了删错之后
    的那一版——唯一的撤销手段没了，而屏幕上只是又弹了一句"已保存"。
    没有改动就不写盘：文件与备份都逐字节不动，并且如实说没有东西要保存。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    two_rows = path.read_bytes()
    at = _page(root)
    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)
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


def test_the_save_message_for_a_deletion_does_not_claim_edits(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(), _buy(symbol="600519", name="贵州茅台", shares=100.0,
                                        price=1600.0, source="discretionary"))
    at = _page(root)
    at = edit_table(at, {1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    assert not at.exception, at.exception
    said = " ".join(_toasts(at))
    assert "删除 1 行" in said and "改了" not in said, said
    assert "恢复上一版" in said, "刚删完是最需要知道'还能换回来'的时刻"


def test_the_save_message_reports_both_when_both_happened(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell(), _buy(symbol="600519", name="贵州茅台", shares=100.0,
                                        price=1600.0, source="discretionary"))
    at = _page(root)
    at = edit_table(at, {0: {"note": "补一句备注"}, 2: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at), click=jui.SAVE_KEY)

    said = " ".join(_toasts(at))
    assert "改了 1 行" in said and "删除 1 行" in said, said
    saved = store.load_trades(_trades_path(root))
    assert list(saved["note"]) == ["补一句备注", ""] and list(saved["trade_id"]) == ["T1", "T2"]


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


def test_the_first_row_is_the_five_things_every_trade_needs(tmp_path):
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
    assert "导出也只含这些" in said[0], said
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
    # 屏幕上没有导出按钮，那句常驻的话里就不该还叫人"用导出"（审查时抓到的）
    assert not any("导出" in c.value for c in at.main.caption), [c.value for c in at.main.caption]
    assert any("恢复上一版" in c.value for c in at.main.caption), "撤销手段在哪还得说"
    assert not [e for e in at.get("dataframe") if e.proto.id]
    assert at.button(key=jui.RESTORE_KEY).disabled


def test_unsaved_edits_are_called_out_next_to_the_save_button(tmp_path):
    """表里改了却没点保存，切到别的页再回来就没了。有没保存的改动时：
    按钮变成主按钮，旁边写明改了几行、勾了几行要删。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    at = _page(root)
    quiet = at.button(key=jui.SAVE_KEY)
    assert (quiet.label, quiet.proto.icon, quiet.proto.type) == \
        ("保存修改", ":material/save:", "secondary"), "没有改动时它只是一个安静的按钮"
    assert not any("还没保存" in c.value for c in at.main.caption)

    at = edit_table(at, {0: {"reason": "改一下"}, 1: {jui.DELETE_COLUMN: True}},
                    dataframe=_log_table_index(at))
    assert not at.exception, at.exception
    said = [c.value for c in at.main.caption if "还没保存" in c.value]
    assert len(said) == 1 and "改了 1 行" in said[0] and "勾了 1 行要删除" in said[0], \
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
    """表格下面改之前是三段常驻的灰字。现在只留一句撤销手段在哪，
    其余的话挂在各自的按钮上（悬停才出）。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    guide = _guide()
    assert at.button(key=jui.SAVE_KEY).proto.help == guide.JOURNAL_EDIT_HINT
    captions = [c.value for c in at.main.caption]
    assert guide.JOURNAL_BACKUP_NOTE in captions, captions
    assert guide.JOURNAL_EDIT_HINT not in captions and \
        not any(v in captions for v in guide.JOURNAL_EXPORT_HELP.values()), captions
    hint = guide.TABLE_HINTS["journal"]
    assert captions.count(hint) == 1 and "不会" in hint and "重算" in hint, \
        "改股数或成交价时金额不会跟着变——这一句得留在表格上方"


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
    assert at.radio(key=jui.KIND_KEY).value == "sell"
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
    at.radio(key=jui.OTHER_KIND_KEY).set_value("dividend")
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
    at.radio(key=jui.OTHER_KIND_KEY).set_value("adjust")
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
# 于是两个行数相同的筛选结果是**同一个控件**：在「买入」那两行里勾了第一行删除、没保存，
# 切到「卖出」（也是两行），那个勾就落在了另一笔交易上——真实浏览器里复现过。
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

    def rerun(self, click: str | None = None) -> AppTest:
        states = self.at._tree.get_widget_states()
        if self.edits:
            state: WidgetState = states.widgets.add()
            state.id = self.editor_id          # 旧 id：控件换了的话，后台根本不认这份状态
            state.string_value = json.dumps({
                "edited_rows": {str(row): cells for row, cells in self.edits.items()},
                "added_rows": [], "deleted_rows": []})
        if click is not None:
            pressed: WidgetState = states.widgets.add()
            pressed.id = self.at.button(key=click).id
            pressed.trigger_value = True
        self.at = self.at._run(widget_state=states)
        editor = self._editor()
        if editor is None or editor.proto.id != self.editor_id:
            self.edits = {}                    # 控件换了：浏览器手里那份改动也就没了
        return self.at


def _four_trades(root: Path) -> Path:
    return _journal(
        root, _buy(reason="买一"), _sell(reason="卖一"),
        _buy(symbol="600519", name="贵州茅台", shares=100.0, price=1600.0, reason="买二"),
        _sell(symbol="600519", shares=100.0, price=1700.0, reason="卖二"))


def _pending_notes(at: AppTest) -> list[str]:
    return [c.value for c in at.main.caption if "还没保存" in c.value]


def test_an_unsaved_tick_does_not_ride_the_row_position_onto_another_trade(tmp_path):
    """筛「买入」（T1、T3）→ 勾第一行删除、不保存 → 改筛「卖出」（T2、T4，也是两行）。
    那个勾绝不能落到 T2 头上：页面不许说还有没保存的改动，点保存也不许删掉任何一笔。"""
    root = _root(tmp_path)
    path = _four_trades(root)
    before = path.read_bytes()
    browser = _Browser(_page(root))
    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["buy"])
    browser.rerun()
    at = browser.edit({0: {jui.DELETE_COLUMN: True}})
    assert any("勾了 1 行要删除" in note for note in _pending_notes(at)), "前提：勾上了"

    browser.at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["sell"])
    at = browser.rerun()
    shown = [f for f in _frames(at) if "trade_id" in getattr(f, "columns", [])][0]
    assert list(shown["trade_id"]) == ["T2", "T4"], "前提：换成了卖出那两行"
    assert _pending_notes(at) == [], "换了一批记录，上一批里没保存的勾不该跟过来"

    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, at.exception
    assert path.read_bytes() == before, \
        f"在买入里勾的删除，删掉的却是别的记录: {list(store.load_trades(path)['trade_id'])}"


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


def test_pressing_delete_on_the_delete_tick_does_not_take_the_table_down(tmp_path):
    """选中「删除」那一格（或一片包含它的格子）按 Delete 键，表格送回来的是"这一格清空"。
    勾选列若是硬邦邦的 bool，清空这一下就是 TypeError：表格连同它下面的保存、恢复、导出
    整个消失一轮。这一列 2026-11-16 起排在最前面，比以前好碰得多。清空 = 没勾。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {jui.DELETE_COLUMN: None}})
    assert not at.exception, at.exception
    assert [e for e in at.get("dataframe") if e.proto.id], "表格不见了"
    assert _pending_notes(at) == [], "清空的勾不是待删除"
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
    """表里勾了一行删除、还没保存，这时去上面点了「记这一笔」（表单没填完，被拒）。
    点完之后那一轮：勾还在、提醒还在。初稿里提交完立刻整页重跑——重跑发生在表格**画出来之前**，
    后台就把表格这一轮的状态当成"控件不在了"丢掉，提醒少报一轮，而浏览器里那个勾明明还勾着。"""
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    browser.edit({1: {jui.DELETE_COLUMN: True}})
    at = browser.rerun(click=jui.SUBMIT_KEY)             # 表单是空的：这一笔会被拒
    assert path.read_bytes() == before, "前提：没记下来"
    assert "".join(str(e.value) for e in at.error), "前提：被拒了，有那句为什么"
    assert any("勾了 1 行要删除" in note for note in _pending_notes(at)), \
        f"点了一下「记这一笔」，表里没保存的勾就不提了: {_pending_notes(at)}"


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
