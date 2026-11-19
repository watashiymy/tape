# tests/test_journal_refusal_wording.py — 「记账」页：一行被拦下时，页面上那句话怎么说（页面级）
#
# 起因（2026-11-18）：历史表里把「类型」那一格清空（选中它按删除键）再点保存，被拦下是对的，可页面上那句话是
# 「无法解释的记录类型 'nan'，只支持 buy/sell/adjust/dividend」：'nan' 是空格子在程序里的样子，使用者没填过
# 这个词；后面四个是存进文件的键，页面上别处都叫买入 / 卖出 / 股数调整 / 现金分红。
# 这句话有三个地方会看到：页面顶上「没有保存」那一条、删除确认框里「没有删除，也没有保存」那一段、
# 「记一笔」表单的报错。句子本身出自 quant.journal.schema（tests/test_journal_schema.py 管），
# 这里钉的是它们到了页面上的样子，以及页面自己拼的那段「是哪一行」。
#
# 同一个病根还有一处不只是措辞（2026-11-19）：按删除键清空的「代码」被当成"不是 6 位数字"的警告放行了，
# 点保存会存进去一条代码是空的记录——见下面「代码空着」那一节。
#
# 夹具与小工具取自 tests/test_dashboard_journal.py（同 test_engine_constraints 取 test_engine_basic 的做法）。
# 单独成文件而不是加进那个文件：那里已经近四千行，而且同一时间有几条分支在往里加测试。
import re

import pandas as pd
import pytest

from quant.journal import schema, store
from tests.test_dashboard_journal import (README, TODAY, _Browser, _buy, _delete, _dialog_errors,
                                          _fill_entry, _journal, _page, _pending_notes, _record,
                                          _root, _sell, _three_trades, _toasts, jui)


def _assert_no_program_words(said: str) -> None:
    """给使用者看的那句拒绝里不许有：空格子在程序里的样子（nan / None / NaT）、存进文件的类型键。"""
    assert said, "前提：有那句拒绝"
    for word in ("nan", "none", "nat"):
        assert not re.search(rf"(?<![a-z]){word}(?![a-z])", said, re.IGNORECASE), \
            f"拒绝的话里冒出了 {word!r}: {said}"
    for key in schema.KINDS:
        assert key not in said, f"拒绝的话里冒出了存进文件的键 {key!r}: {said}"


def _page_errors(at) -> str:
    return "".join(str(e.value) for e in at.error)


# ================================================================ 类型空着（2026-11-18）

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
    said = _page_errors(at)
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


# ================================================================ 被拦下的是哪一行（2026-11-18）

def test_a_refused_row_is_not_introduced_as_none(tmp_path):
    """被拦下的每一行，前面有一段「是哪一行」：记录号（日期 代码）。清空的正是日期那一格时，
    这一段原先是「T1（None 000333）」。空着的那一样不写，剩什么写什么。"""
    root = _root(tmp_path)
    _journal(root, _buy(), _sell())
    browser = _Browser(_page(root))
    browser.edit({0: {"date": None}})
    at = browser.rerun(click=jui.SAVE_KEY)
    said = _page_errors(at)
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


# ================================================================ 代码空着（2026-11-19）
#
# 历史表里把「代码」那一格清空有两种清法，交回来的东西不一样（真实浏览器里在后台逐个看过，streamlit 1.61）：
# - 选中那一格按删除键：交回空值（NaN），格子里显示「—」；
# - 点进去把字删光再回车：交回空串，格子里什么都不显示。
# 校验原先只认得空串。NaN 被当成了 "nan" 三个字母，判成「代码 'nan' 不是 6 位数字」——一条警告，而保存只看
# 阻断项：按删除键清空再点「保存修改」，提示是「已保存：改了 1 行」，文件里那一行的代码是空的。
# 设计上「缺少标的代码」是该当场拦下的：不知道是哪只票的记录，之后算持仓与盈亏时只能跳过它。
# 所以两种清法各钉一条：漏的是前一种（下面送 None），后一种一直拦得住（送空串），别改着改着又漏一种。

@pytest.mark.parametrize("cleared", [None, ""], ids=["按删除键清空", "把字删光"])
def test_clearing_a_code_and_saving_is_refused_and_nothing_is_written(tmp_path, cleared):
    root = _root(tmp_path)
    path = _journal(root, _buy(), _sell())
    before = path.read_bytes()
    browser = _Browser(_page(root))
    at = browser.edit({0: {"symbol": cleared}})
    assert any("改了 1 行" in note for note in _pending_notes(at)), "清空代码是一处改动"
    at = browser.rerun(click=jui.SAVE_KEY)
    assert not at.exception, at.exception
    assert path.read_bytes() == before, "代码是空的一行竟然落盘了"
    assert not store.backup_path(path).exists(), "没存成，却换了一份上一版"
    assert not any("已保存" in t for t in _toasts(at)), _toasts(at)
    said = _page_errors(at)
    assert "没有保存" in said and "T1（2026-08-03）：" in said and "缺少标的代码" in said, said
    _assert_no_program_words(said)


def test_a_cleared_code_stops_the_delete_too(tmp_path):
    """删除会把表里别的行上没保存的改动一起写进去：其中一行的代码清空了，就整个不写——不删、不存。"""
    root = _root(tmp_path)
    path = _three_trades(root)
    before = path.read_bytes()
    at = _delete(_page(root), 2, edits={0: {"symbol": None}})
    assert not at.exception, at.exception
    assert path.read_bytes() == before, "代码是空的一行竟然落盘了，或者那一条竟然被删了"
    said = _dialog_errors(at)
    assert "没有删除" in said and "T1" in said and "缺少标的代码" in said, said
    _assert_no_program_words(said)


# ================================================================ 记下之后那句「请复核」里的数与代码（2026-11-19）

def test_a_million_share_odd_lot_is_written_out_in_full(tmp_path):
    """一百万股以上的数原先写成「买入 1.00005e+06 股不是 100 股的整数倍」。写全，带千分位。"""
    root = _root(tmp_path)
    at = _fill_entry(_page(root), shares=1000050.0, price=1.234)
    at = at.button(key=jui.SUBMIT_KEY).click().run()
    assert not at.exception, at.exception
    said = " ".join(str(w.value) for w in at.warning)
    assert "买入 1,000,050 股不是 100 股的整数倍" in said, said
    assert "e+" not in said, said


def test_a_short_code_is_quoted_the_way_the_page_quotes_things(tmp_path):
    """填了三位的代码，记下之后提醒复核：原先是「代码 '333' 不是 6 位数字」——那对英文单引号是程序的写法。"""
    root = _root(tmp_path)
    at = _fill_entry(_page(root), symbol="333", shares=100.0, price=7.1)
    at = at.button(key=jui.SUBMIT_KEY).click().run()
    assert not at.exception, at.exception
    said = " ".join(str(w.value) for w in at.warning)
    assert "代码「333」不是 6 位数字" in said, said
    assert "'" not in said, said


# ================================================================ 文档

def test_the_readme_quotes_the_missing_kind_refusal_as_the_page_says_it():
    """README 里引的那句「缺少记录类型（……）」得是页面上真会出现的那一句：句子再改，文档跟着红。"""
    [issue] = [i for i in schema.validate_trade(_buy(kind=float("nan")), today=TODAY)
               if i.field == "kind"]
    assert f"「{issue.message}」" in README, issue.message
