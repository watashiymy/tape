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
from streamlit.testing.v1 import AppTest

from quant.backtest.costs import commission, stamp_tax
from quant.config import load_settings
from quant.journal import export, schema, store
from tests.conftest import (click_row_button, copy_app, edit_table, goto_page,
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
    for label, value in [("总已实现（含分红）", "1,712.93"),
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
    assert by_label["总已实现（含分红）"] == ("1,712.93", jui.fmt.UP)
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
    assert by_label["总已实现（含分红）"] == "1,712.93"
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
        "「＋ 记这一笔」不在卡片里"
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


# ================================================ 备份兜底（v0.3.2 §3）
#
# 日志移出版本控制之后，"误删一行用 git 找回"这条路没了。替代品是每次写盘前
# 自动另存的 `journal/trades.csv.bak`。**用户必须知道它存在**——一个没人知道的
# 备份文件等于没有备份：真出事的那一刻，他只会以为数据没了。

def test_the_entry_page_says_where_the_backup_file_is(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)

    assert not at.exception, at.exception
    text = _texts(at)
    assert "trades.csv.bak" in text, f"记账页没说备份文件在哪：{text}"
    assert "上一版" in text, f"没说清 .bak 里是什么（紧邻的上一版）：{text}"


def test_the_backup_note_is_there_before_the_first_trade_too(tmp_path):
    """一笔都没记过的时候也要说：那正是用户会大批量录入的时刻。"""
    root = _root(tmp_path)
    at = _page(root)

    assert not at.exception, at.exception
    assert "trades.csv.bak" in _texts(at)


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
    assert "浮动盈亏" in text and "缓存" in text, text


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
