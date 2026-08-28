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
PAGE = "交易日志"
SIGNALS_PAGE = "今日信号"
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

def test_empty_journal_renders_without_exception(tmp_path):
    """第一次打开面板时日志是空的——这是每个新用户的第一屏，不许崩、不许假装有数据。"""
    at = _page(_root(tmp_path))
    assert not at.exception, at.exception
    assert "还没有" in _texts(at) or "暂无" in _texts(at), _texts(at)


def test_empty_journal_shows_no_fabricated_zero_pnl(tmp_path):
    """空日志的已实现盈亏是"没有"，不是 0.00。
    0 会被读成"我不赚不亏"，而实情是一笔都还没记。"""
    at = _page(_root(tmp_path))
    assert not at.exception, at.exception
    text = _texts(at)
    assert "0.00" not in text, f"空日志页面上出现了编出来的 0：{text}"


def test_journal_with_data_renders_positions_and_pnl(tmp_path):
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    _cache(root, close=76.0, day=D_SELL)
    at = _page(root)

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
    at = _page(root)

    assert not at.exception, at.exception
    assert "3,425.87" in _texts(at), _texts(at)


def test_oversell_warning_is_visible_and_does_not_break_the_page(tmp_path):
    """卖超（漏记了买入）：设计 §3 要求"警告并在持仓页持续标红"，但不阻断、不崩页。"""
    root = _root(tmp_path)
    _journal(root, _buy(shares=100.0), _sell(shares=500.0))
    at = _page(root)

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
    at = _page(root)

    assert not at.exception, at.exception
    assert "跳过" in _texts(at), _texts(at)


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
    assert "3,425.87" in _texts(at), "配置坏了不该连盈亏一起藏起来"


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
    按日期筛一下就看到一个不存在的持仓，是最容易让人做错决定的显示错误。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    at = _page(root)
    at.multiselect(key=jui.FILTER_KIND_KEY).set_value(["buy"])
    at = at.run()

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
    """局限要写在**页面上**，不只在 README 里：看数字的人不一定读过 README。"""
    at = _page(_root(tmp_path))
    text = _texts(at)
    assert "浮动盈亏" in text and "缓存" in text, text
