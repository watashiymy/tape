# tests/test_journal_refusal_wording.py — 「记账」页：一行被拦下时，页面上那句话怎么说（页面级）
#
# 起因（2026-11-18）：历史表里把「类型」那一格清空（选中它按删除键）再点保存，被拦下是对的，可页面上那句话是
# 「无法解释的记录类型 'nan'，只支持 buy/sell/adjust/dividend」：'nan' 是空格子在程序里的样子，使用者没填过
# 这个词；后面四个是存进文件的键，页面上别处都叫买入 / 卖出 / 股数调整 / 现金分红。
# 这句话有三个地方会看到：页面顶上「没有保存」那一条、删除确认框里「没有删除，也没有保存」那一段、
# 「记一笔」表单的报错。句子本身出自 quant.journal.schema（tests/test_journal_schema.py 管），
# 这里钉的是它们到了页面上的样子，以及页面自己拼的那段「是哪一行」。
#
# 夹具与小工具取自 tests/test_dashboard_journal.py（同 test_engine_constraints 取 test_engine_basic 的做法）。
# 单独成文件而不是加进那个文件：那里已经近四千行，而且同一时间有几条分支在往里加测试。
import re

import pandas as pd
import pytest

from quant.journal import schema
from tests.test_dashboard_journal import (README, TODAY, _Browser, _buy, _delete, _dialog_errors,
                                          _journal, _page, _pending_notes, _record, _root, _sell,
                                          _three_trades, jui)


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


# ================================================================ 文档

def test_the_readme_quotes_the_missing_kind_refusal_as_the_page_says_it():
    """README 里引的那句「缺少记录类型（……）」得是页面上真会出现的那一句：句子再改，文档跟着红。"""
    [issue] = [i for i in schema.validate_trade(_buy(kind=float("nan")), today=TODAY)
               if i.field == "kind"]
    assert f"「{issue.message}」" in README, issue.message
